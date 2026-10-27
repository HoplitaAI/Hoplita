#!/usr/bin/env python3
import base64
import copy
import datetime as _dt
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid

import wb_document as D
import wb_vcs as V

SCRATCHPAD = "scratchpad"
LEASE_TTL_S = 600
LENS_LIMIT = 200
SCOPES = ("document", "lenses")


class WbError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _now():
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _norm_quote(t):
    return " ".join(str(t or "").split())


def text_includes_quote(text, quote):
    q = _norm_quote(quote)
    return bool(q) and q in _norm_quote(text)


class _Rows:

    def __init__(self, rows, rowcount, lastrowid):
        self._rows, self.rowcount, self.lastrowid = rows, rowcount, lastrowid

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _LockedDB:

    def __init__(self, conn, lock):
        self._conn, self._lock = conn, lock

    def execute(self, sql, args=()):
        with self._lock:
            cur = self._conn.execute(sql, args)
            return _Rows(cur.fetchall() if cur.description else [], cur.rowcount, cur.lastrowid)

    def executemany(self, sql, seq):
        with self._lock:
            cur = self._conn.executemany(sql, seq)
            return _Rows([], cur.rowcount, cur.lastrowid)

    def executescript(self, script):
        with self._lock:
            self._conn.executescript(script)

    def __getattr__(self, name):
        return getattr(self._conn, name)


class Store:
    def __init__(self, home):
        os.makedirs(home, exist_ok=True)
        self.path = os.path.join(home, "whiteboards.db")
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._changes = {}
        self._db = _LockedDB(sqlite3.connect(self.path, check_same_thread=False,
                                             isolation_level=None, timeout=5), self._lock)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript("""
        CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY, version INTEGER NOT NULL,
                                           next_id INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS versions(review_id TEXT, version INTEGER,
                                            snapshot TEXT NOT NULL, PRIMARY KEY(review_id, version));
        CREATE TABLE IF NOT EXISTS receipts(command_id TEXT PRIMARY KEY, request TEXT, response TEXT);
        CREATE TABLE IF NOT EXISTS attention(review_id TEXT PRIMARY KEY, viewed_at TEXT, dismissed_at TEXT);
        CREATE TABLE IF NOT EXISTS repositories(id TEXT PRIMARY KEY, path TEXT UNIQUE, name TEXT);
        CREATE TABLE IF NOT EXISTS resources(id TEXT PRIMARY KEY, repository_id TEXT, kind TEXT,
                                             mime_type TEXT, data BLOB);
        CREATE TABLE IF NOT EXISTS coverage(review_id TEXT, file TEXT, fingerprint TEXT,
                                            coverage TEXT, PRIMARY KEY(review_id, file));
        CREATE TABLE IF NOT EXISTS leases(review_id TEXT, scope TEXT, lease_id TEXT,
                                          expires_at REAL, focus TEXT, PRIMARY KEY(review_id, scope));
        CREATE TABLE IF NOT EXISTS journal_marks(review_id TEXT PRIMARY KEY, version INTEGER);
        CREATE TABLE IF NOT EXISTS repo_links(id TEXT PRIMARY KEY, name TEXT, commits TEXT,
                                              project TEXT, target TEXT);
        CREATE TABLE IF NOT EXISTS briefs(key TEXT PRIMARY KEY, fingerprint TEXT, brief TEXT,
                                          created TEXT);
        CREATE TABLE IF NOT EXISTS plans(review_id TEXT PRIMARY KEY, plan TEXT);
        """)
        self.listeners = []
        self.checkout_for_project = None

    def _one(self, sql, args=()):
        return self._db.execute(sql, args).fetchone()

    def _notify(self, review_id):
        with self._cond:
            self._changes[review_id] = self._changes.get(review_id, 0) + 1
            self._changes["*"] = self._changes.get("*", 0) + 1
            self._cond.notify_all()

    def change_mark(self, review_id):
        with self._lock:
            return self._changes.get(review_id, 0)

    def wait_change(self, review_id, mark, timeout=25.0):
        deadline = time.time() + timeout
        with self._cond:
            while self._changes.get(review_id, 0) == mark:
                left = deadline - time.time()
                if left <= 0:
                    break
                self._expire_leases(review_id)
                self._cond.wait(min(left, 5.0))
            return self._changes.get(review_id, 0)

    def register_repository(self, path):
        root = V.repo_root(path)
        with self._lock:
            row = self._one("SELECT id, name FROM repositories WHERE path=?", (root,))
            if row:
                return {"id": row[0], "name": row[1], "path": root}
            rid = str(uuid.uuid4())
            self._db.execute("INSERT INTO repositories VALUES(?,?,?)",
                             (rid, root, os.path.basename(root)))
            return {"id": rid, "name": os.path.basename(root), "path": root}

    def repository(self, repo_id):
        row = self._one("SELECT id, path, name FROM repositories WHERE id=?", (repo_id,))
        if not row:
            link = self._one("SELECT name, commits, project, target FROM repo_links WHERE id=?",
                             (repo_id,))
            if not link:
                raise WbError(f"Repository {repo_id} is not registered.", 404)
            real = self.repository(link[3] or self._find_checkout(repo_id, *link[:3]))
            return {"id": repo_id, "path": real["path"], "name": real["name"]}
        if not os.path.isdir(row[1]):
            raise WbError(f"The registered checkout {row[1]} no longer exists.", 404)
        return {"id": row[0], "path": row[1], "name": row[2]}

    def _find_checkout(self, repo_id, name, commits, project):
        want = json.loads(commits or "[]")
        paths = [r[0] for r in self._db.execute("SELECT path FROM repositories").fetchall()]
        if project and self.checkout_for_project:
            try:
                p = self.checkout_for_project(project)
                if p:
                    paths.insert(0, p)
            except Exception:
                pass
        for path in paths:
            if not os.path.isdir(path):
                continue
            try:
                if want and all(V.resolve(path, c) == c for c in want):
                    target = self.register_repository(path)["id"]
                    self._db.execute("UPDATE repo_links SET target=? WHERE id=?", (target, repo_id))
                    return target
            except Exception:
                continue
        raise WbError(f"This whiteboard came from another machine and its repository "
                      f"«{name or '?'}» is not on this one yet. Register the checkout with "
                      f"wb_register_repository and it is found by its commits.", 404)

    def resolve_pins(self, repo_id, base, head):
        root = self.repository(repo_id)["path"]
        return {"repositoryId": repo_id, "base": V.resolve(root, base),
                "head": V.resolve(root, head)}

    def _target_pins(self, target):
        kind = target.get("kind")
        repo = self.repository(target.get("repositoryId"))
        if kind == "commits":
            head = V.resolve(repo["path"], target.get("head"))
            base = V.resolve(repo["path"], target["base"]) if target.get("base") else head
            return {"repositoryId": repo["id"], "base": base, "head": head}
        if kind == "worktree":
            base = (V.resolve(repo["path"], target["base"]) if target.get("base")
                    else V.head_commit(repo["path"]))
            if not base:
                raise WbError("This repository has no commit yet to compare the working files with.")
            return {"repositoryId": repo["id"], "base": base, "head": V.WORKTREE}
        raise WbError("target.kind must be commits or worktree.")

    def _check_pins(self, pins):
        repo = self.repository(pins["repositoryId"])
        for side in ("base", "head"):
            v = pins.get(side)
            if side == "head" and v == V.WORKTREE:
                continue
            if not v or not V.SHA_RE.match(v):
                raise WbError("Use resolved commit IDs, not moving branch names.")
            if V.resolve(repo["path"], v) != v:
                raise WbError("Use resolved commit IDs, not moving branch names.")

    def exists(self, review_id):
        return self._one("SELECT 1 FROM reviews WHERE id=?", (review_id,)) is not None

    def snapshot(self, review_id, version=None):
        if review_id == SCRATCHPAD:
            self.ensure_scratchpad()
        with self._lock:
            row = self._one("SELECT version FROM reviews WHERE id=?", (review_id,))
            if not row:
                raise WbError(f"Whiteboard {review_id} does not exist.", 404)
            v = row[0] if version is None else int(version)
            s = self._one("SELECT snapshot FROM versions WHERE review_id=? AND version=?",
                          (review_id, v))
            if not s:
                raise WbError(f"Whiteboard {review_id} has no version {v}.", 404)
            return json.loads(s[0])

    def history(self, review_id):
        rows = self._db.execute("SELECT version, snapshot FROM versions WHERE review_id=? "
                                "ORDER BY version", (review_id,)).fetchall()
        if not rows:
            raise WbError(f"Whiteboard {review_id} does not exist.", 404)
        return [{"version": v, "title": json.loads(s).get("title"),
                 "createdAt": json.loads(s).get("createdAt")} for v, s in rows]

    def journal_due(self, review_id):
        snap = self.snapshot(review_id)
        if not snap.get("project") or not snap.get("document"):
            return None
        row = self._one("SELECT version FROM journal_marks WHERE review_id=?", (review_id,))
        if row and row[0] >= snap["version"]:
            return None
        return snap["version"]

    def journal_mark(self, review_id, version):
        self._db.execute("INSERT OR REPLACE INTO journal_marks VALUES(?,?)", (review_id, version))

    def catalog(self, project=None):
        out = []
        for rid, ver in self._db.execute("SELECT id, version FROM reviews").fetchall():
            snap = self.snapshot(rid, ver)
            att = self._one("SELECT viewed_at, dismissed_at FROM attention WHERE review_id=?", (rid,))
            item = {"id": rid, "title": snap.get("title"), "version": ver,
                    "kind": snap.get("kind"), "createdAt": snap.get("createdAt"),
                    "project": snap.get("project"),
                    "pins": snap.get("pins"), "target": snap.get("target"),
                    "blocks": sum(1 for _ in D.walk(snap.get("document"))),
                    "viewedAt": att[0] if att else None,
                    "dismissedAt": att[1] if att else None,
                    "activity": self.activity_snapshot(rid)}
            if snap.get("pins"):
                try:
                    item["repositoryName"] = self.repository(snap["pins"]["repositoryId"])["name"]
                except WbError:
                    item["repositoryName"] = "?"
            out.append(item)
        out.sort(key=lambda x: x.get("createdAt") or "", reverse=True)
        if project:
            out.sort(key=lambda x: 0 if x.get("project") == project else
                     1 if x.get("kind") == "scratchpad" else 2)
        return out

    def ensure_scratchpad(self):
        with self._lock:
            if self.exists(SCRATCHPAD):
                return
            snap = {"reviewId": SCRATCHPAD, "version": 0, "title": "Scratchpad",
                    "kind": "scratchpad", "document": [], "lenses": [], "createdAt": _now()}
            self._db.execute("INSERT INTO reviews VALUES(?,?,?)", (SCRATCHPAD, 0, 0))
            self._db.execute("INSERT INTO versions VALUES(?,?,?)",
                             (SCRATCHPAD, 0, json.dumps(snap)))

    def _expire_leases(self, review_id=None):
        now = time.time()
        rows = self._db.execute("SELECT review_id FROM leases WHERE expires_at<?", (now,)).fetchall()
        if rows:
            self._db.execute("DELETE FROM leases WHERE expires_at<?", (now,))
            for (r,) in rows:
                self._changes[r] = self._changes.get(r, 0) + 1
            self._cond.notify_all()

    def activity_snapshot(self, review_id):
        with self._lock:
            self._expire_leases()
            rows = self._db.execute("SELECT scope, expires_at, focus FROM leases WHERE review_id=?",
                                    (review_id,)).fetchall()
        focuses = []
        for scope, _exp, focus in rows:
            if focus:
                f = json.loads(focus)
                f["scope"] = scope
                focuses.append(f)
        return {"workingCount": len(rows),
                "expiresAt": max([r[1] for r in rows]) if rows else None,
                "scopes": [r[0] for r in rows], "focuses": focuses}

    def activity(self, review_id, action, lease_id, scope="document", focus="__keep__"):
        if action not in ("begin", "renew", "end"):
            raise WbError("action must be begin, renew or end.")
        if scope not in SCOPES:
            raise WbError('scope must be "document" or "lenses".')
        if not lease_id:
            raise WbError("leaseId is required (any fresh UUID you choose).")
        if focus not in ("__keep__", None):
            if not isinstance(focus, dict) or not str(focus.get("description") or "").strip():
                raise WbError("focus must be {description, targetId?} or null.")
            focus = {"description": str(focus["description"]).strip()[:160],
                     **({"targetId": focus["targetId"]} if focus.get("targetId") else {})}
        if review_id == SCRATCHPAD:
            self.ensure_scratchpad()
        if not self.exists(review_id):
            raise WbError(f"Whiteboard {review_id} does not exist.", 404)
        with self._lock:
            self._expire_leases()
            row = self._one("SELECT lease_id, focus FROM leases WHERE review_id=? AND scope=?",
                            (review_id, scope))
            if action == "end":
                self._db.execute("DELETE FROM leases WHERE review_id=? AND scope=? AND lease_id=?",
                                 (review_id, scope, lease_id))
            else:
                if row and row[0] != lease_id:
                    raise WbError(("This whiteboard is" if scope == "document" else
                                   "This whiteboard's lenses are") +
                                  " being authored by another session. Wait for it to "
                                  "finish or expire, then begin your own session.", 409)
                if action == "renew" and not row:
                    raise WbError("Authoring session expired. Begin a new session.", 409)
                keep = row[1] if row else None
                new_focus = keep if focus == "__keep__" else (json.dumps(focus) if focus else None)
                self._db.execute("INSERT OR REPLACE INTO leases VALUES(?,?,?,?,?)",
                                 (review_id, scope, lease_id, time.time() + LEASE_TTL_S, new_focus))
        self._notify(review_id)
        return self.activity_snapshot(review_id)

    def _assert_write(self, review_id, lease_id, scope):
        self._expire_leases()
        row = self._one("SELECT lease_id FROM leases WHERE review_id=? AND scope=?",
                        (review_id, scope))
        if row and row[0] != lease_id:
            raise WbError(("This whiteboard is" if scope == "document" else
                           "This whiteboard's lenses are") +
                          " being authored by another session. Wait for it to finish "
                          "or expire, then begin your own session.", 409)
        if lease_id and not row:
            raise WbError("Authoring session ended or expired. Begin a new session and "
                          "reread the whiteboard before editing." if scope == "document" else
                          'No live lenses lease. Begin one with wb_activity scope:"lenses" '
                          "and reread the lenses before editing them.", 409)

    def _extend(self, review_id, lease_id, scope):
        if lease_id:
            self._db.execute("UPDATE leases SET expires_at=? WHERE review_id=? AND scope=? AND lease_id=?",
                             (time.time() + LEASE_TTL_S, review_id, scope, lease_id))

    def _anchor(self, snap, pins):
        p = pins or snap.get("pins")
        if not p:
            raise WbError("This source names no repository or commit, and the whiteboard "
                          "has no pins. On the scratchpad every source carries its own pins.")
        root = self.repository(p["repositoryId"])["path"]
        return root, {"head": p["head"], "base": p.get("base") or p["head"]}

    def read_range(self, snap, side, file, a, b, pins=None):
        root, commits = self._anchor(snap, pins)
        try:
            text = V.read_text(root, commits[side], file)
        except V.VcsError as e:
            raise WbError(str(e), e.status)
        lines = V.file_lines(text)
        if b > len(lines) or not lines:
            raise WbError(f"Source range {file}:{a}-{b} exceeds the pinned file ({len(lines)} lines).")
        return lines[a - 1:b]

    def _validate_sources(self, snap, only=None, soft=False):
        warnings = []
        for ref_id, kind, sel, pins in D.references(snap.get("document")):
            if only is not None and ref_id not in only:
                continue
            for side, file, a, b in D.selection_anchors(sel):
                try:
                    if side == "base" and pins and "base" not in pins:
                        raise WbError("A base-side source needs base pins.")
                    got = self.read_range(snap, side, file, a, b, pins)
                    if kind == "peek" and not any(x.strip() for x in got):
                        raise WbError(f"Source range {file}:{a}-{b} contains only whitespace.")
                except WbError as e:
                    if soft:
                        warnings.append(f"{side}/{file}#L{a}-L{b}: {e}")
                    else:
                        raise
        return warnings

    def _validate_resources(self, snap, soft=False):
        warnings = []
        for kind, rid, block in D.resource_refs(snap.get("document")):
            try:
                row = self._one("SELECT kind, data, repository_id FROM resources WHERE id=?", (rid,))
                if not row or row[0] != kind:
                    raise WbError(f"The {kind} resource {rid} was not uploaded (wb_upload).")
                if kind == "trace":
                    trace = json.loads(row[1])
                    ev = next((e for e in trace["events"] if e["id"] == block["eventId"]), None)
                    if ev is None or not text_includes_quote(ev["text"], block["text"]):
                        raise WbError("Quote does not match the retained trace event.")
            except WbError as e:
                if soft:
                    warnings.append(str(e))
                else:
                    raise
        return warnings

    def command(self, operation, command_id=None, lease_id=None):
        if not isinstance(operation, dict) or not operation.get("type"):
            raise WbError("operation.type is required.")
        request = json.dumps({"op": operation, "lease": lease_id}, sort_keys=True)
        with self._lock:
            if command_id:
                row = self._one("SELECT request, response FROM receipts WHERE command_id=?",
                                (command_id,))
                if row:
                    if row[0] == "null":
                        raise WbError("This command's whiteboard was deleted.", 404)
                    if row[0] != request:
                        raise WbError("Command ID was already used for different input.", 409)
                    return json.loads(row[1])
            result = self._run(operation, lease_id)
            if command_id:
                self._db.execute("INSERT OR REPLACE INTO receipts VALUES(?,?,?)",
                                 (command_id, request, json.dumps(result)))
        if operation["type"] != "attention":
            for fn in list(self.listeners):
                try:
                    fn(result.get("reviewId"), operation["type"] == "delete")
                except Exception:
                    pass
        return result

    def _load(self, review_id):
        if review_id == SCRATCHPAD:
            self.ensure_scratchpad()
        row = self._one("SELECT version, next_id FROM reviews WHERE id=?", (review_id,))
        if not row:
            raise WbError(f"Whiteboard {review_id} does not exist.", 404)
        return self.snapshot(review_id, row[0]), row[1]

    def _save(self, snap, next_id, lease_id=None, scope="document"):
        rid = snap["reviewId"]
        snap["createdAt"] = _now()
        pins = snap.get("pins") or {}
        if pins.get("head") == V.WORKTREE:
            try:
                snap["worktreeFingerprint"] = V.worktree_fingerprint(
                    self.repository(pins["repositoryId"])["path"])
            except Exception:
                snap.pop("worktreeFingerprint", None)
        self._db.execute("BEGIN IMMEDIATE")
        try:
            row = self._one("SELECT version FROM reviews WHERE id=?", (rid,))
            if row is not None and row[0] != snap["version"] - 1:
                raise WbError("Whiteboard changed during validation. Reread it and retry the edit.", 409)
            self._db.execute("INSERT INTO versions VALUES(?,?,?)",
                             (rid, snap["version"], json.dumps(snap, ensure_ascii=False)))
            self._db.execute("INSERT OR REPLACE INTO reviews VALUES(?,?,?)",
                             (rid, snap["version"], next_id))
            self._extend(rid, lease_id, scope)
            self._db.execute("COMMIT")
        except Exception:
            self._db.execute("ROLLBACK")
            raise
        self._notify(rid)

    def _run(self, op, lease_id):
        t = op["type"]
        if t == "create":
            return self._create(op)
        rid = op.get("reviewId")
        if not rid:
            raise WbError("reviewId is required.")
        if t == "delete":
            if rid == SCRATCHPAD:
                raise WbError("The scratchpad has no lifecycle, title or pins of its own.", 409)
            self._load(rid)
            for tbl in ("leases", "coverage", "attention", "versions"):
                self._db.execute(f"DELETE FROM {tbl} WHERE review_id=?", (rid,))
            self._db.execute("DELETE FROM reviews WHERE id=?", (rid,))
            self._notify(rid)
            return {"reviewId": rid, "deleted": True}
        if t == "attention":
            self._load(rid)
            action = op.get("action")
            if action not in ("view", "dismiss", "restore"):
                raise WbError("action must be view, dismiss or restore.")
            row = self._one("SELECT viewed_at, dismissed_at FROM attention WHERE review_id=?", (rid,))
            viewed, dismissed = (row or (None, None))
            if action == "view":
                viewed = _now()
            elif action == "dismiss":
                dismissed = _now()
            else:
                dismissed = None
            self._db.execute("INSERT OR REPLACE INTO attention VALUES(?,?,?)", (rid, viewed, dismissed))
            self._notify(rid)
            return {"reviewId": rid, "attention": True}
        scope = "lenses" if t == "lens" else "document"
        self._assert_write(rid, lease_id, scope)
        snap, next_id = self._load(rid)
        if rid == SCRATCHPAD and t not in ("edit", "lens", "restore"):
            raise WbError("The scratchpad has no lifecycle, title or pins of its own.", 409)
        new = copy.deepcopy(snap)
        new["version"] = snap["version"] + 1
        new.pop("lastEdit", None)
        counter = [next_id]
        result = {"reviewId": rid}
        warnings = []
        if t == "edit":
            try:
                r = D.apply_edit(new["document"], op.get("edit"), counter,
                                 placement="first" if rid == SCRATCHPAD else "append")
                D.check_references(new["document"])
            except D.DocError as e:
                raise WbError(str(e), e.status)
            new["lastEdit"] = r.pop("summary")
            result.update(r)
            before = {x[0] for x in D.references(snap["document"])}
            fresh = {x[0] for x in D.references(new["document"])} - before
            old_sel = {x[0]: json.dumps(x[2], sort_keys=True) for x in D.references(snap["document"])}
            for ref_id, _k, sel, _p in D.references(new["document"]):
                if old_sel.get(ref_id) != json.dumps(sel, sort_keys=True):
                    fresh.add(ref_id)
            self._validate_sources(new, only=fresh)
            self._validate_resources(new)
        elif t == "lens":
            if rid == SCRATCHPAD:
                raise WbError("The scratchpad has no changes of its own to lens.", 409)
            r = self._lens_edit(new, op.get("edit"), counter)
            result.update(r)
            new["lastEdit"] = {"type": "lens", "targetId": r["targetId"], "kind": "lens",
                               "blockId": r["targetId"]}
        elif t == "rename":
            new["title"] = str(op.get("title") or "").strip()
            if not new["title"]:
                raise WbError("Supply a title.")
        elif t in ("repin", "set_target"):
            if t == "repin":
                pins = op.get("pins") or {}
                self._check_pins(pins)
                new["pins"] = {k: pins[k] for k in ("repositoryId", "base", "head")}
                new["target"] = {"kind": "commits", **new["pins"]}
            else:
                new["target"] = op.get("target") or {}
                new["pins"] = self._target_pins(new["target"])
            warnings += self._validate_sources(new, soft=True)
            warnings += self._validate_resources(new, soft=True)
            if not warnings:
                pass
        elif t == "restore":
            old = self.snapshot(rid, op.get("version"))
            for k in ("title", "pins", "target", "document", "lenses"):
                if k in old:
                    new[k] = copy.deepcopy(old[k])
        else:
            raise WbError(f"Unknown operation {t}.")
        self._save(new, counter[0], lease_id, scope)
        result["version"] = new["version"]
        if warnings:
            result["warnings"] = warnings
        return result

    def _create(self, op):
        kind = op.get("kind")
        if kind == "scratchpad":
            if op.get("target") or op.get("pins"):
                raise WbError("A scratchpad has no target or pins of its own.")
            if self.exists(SCRATCHPAD):
                raise WbError("The scratchpad already exists.", 409)
            self.ensure_scratchpad()
            return {"created": True, "reviewId": SCRATCHPAD, "version": 0}
        title = str(op.get("title") or "").strip()
        if not title:
            raise WbError("Supply a title.")
        target, pins = op.get("target"), op.get("pins")
        if (target is None) == (pins is None):
            raise WbError("Supply exactly one of target or pins.")
        if target is not None:
            pins = self._target_pins(target)
        else:
            self._check_pins(pins)
            target = {"kind": "commits", **pins}
        rid = str(uuid.uuid4())
        snap = {"reviewId": rid, "version": 0, "title": title,
                "pins": {k: pins[k] for k in ("repositoryId", "base", "head")},
                "target": target, "document": [], "lenses": [], "createdAt": _now()}
        if op.get("project"):
            snap["project"] = str(op["project"])
        with self._lock:
            self._db.execute("INSERT INTO reviews VALUES(?,?,?)", (rid, -1, 0))
            try:
                self._save(snap, 0)
            except Exception:
                self._db.execute("DELETE FROM reviews WHERE id=?", (rid,))
                raise
        repo = self.repository(pins["repositoryId"])
        return {"created": True, "reviewId": rid, "version": 0,
                "whiteboard": {"id": rid, "title": title, "pins": snap["pins"],
                               "target": target, "repositoryName": repo["name"],
                               "repositoryPath": repo["path"]}}

    def _lens_targets(self, targets):
        if not isinstance(targets, list) or not (1 <= len(targets) <= 1000):
            raise WbError("targets: 1..1000 entries.")
        out = []
        for i, tg in enumerate(targets):
            if not isinstance(tg, dict):
                raise WbError(f"targets[{i}] must be an object.")
            if tg.get("kind") == "files":
                pats = tg.get("patterns")
                if not isinstance(pats, list) or not pats:
                    raise WbError(f"targets[{i}].patterns needs at least one path or glob.")
                for p in pats:
                    p = str(p)
                    if (not p or p.startswith("/") or ".." in p.split("/") or "\\" in p or "\0" in p):
                        raise WbError("File lens patterns must be repository-relative paths "
                                      "or globs using forward slashes.")
                out.append({"kind": "files", "patterns": [str(p) for p in pats]})
            elif tg.get("kind") == "ranges":
                srcs = tg.get("sources")
                if not isinstance(srcs, list) or not srcs:
                    raise WbError(f"targets[{i}].sources needs at least one source.")
                try:
                    out.append({"kind": "ranges",
                                "sources": [D.check_selection(s, f"targets[{i}].sources[{j}]")
                                            for j, s in enumerate(srcs)]})
                except D.DocError as e:
                    raise WbError(str(e))
            else:
                raise WbError(f'targets[{i}].kind must be "files" or "ranges".')
        return out

    def _lens_edit(self, snap, edit, counter):
        if not isinstance(edit, dict):
            raise WbError("edit must be an object.")
        lenses = snap.setdefault("lenses", [])
        t = edit.get("type")
        if t == "insert":
            if len(lenses) >= LENS_LIMIT:
                raise WbError(f"A whiteboard holds at most {LENS_LIMIT} lenses.")
            title = str(edit.get("title") or "").strip()
            if not title:
                raise WbError("A lens needs a title.")
            counter[0] += 1
            lens = {"id": f"lens-{counter[0]}", "title": title,
                    "targets": self._lens_targets(edit.get("targets"))}
            if edit.get("afterId"):
                idx = next((i for i, x in enumerate(lenses) if x["id"] == edit["afterId"]), None)
                if idx is None:
                    raise WbError(f"Lens {edit['afterId']} does not exist.", 404)
                lenses.insert(idx + 1, lens)
            else:
                lenses.append(lens)
            tid = lens["id"]
        elif t in ("update", "remove"):
            tid = edit.get("targetId")
            idx = next((i for i, x in enumerate(lenses) if x["id"] == tid), None)
            if idx is None:
                raise WbError(f"Lens {tid} does not exist.", 404)
            if t == "remove":
                del lenses[idx]
            else:
                if edit.get("title") is None and edit.get("targets") is None:
                    raise WbError("A lens update names a title or targets.")
                if edit.get("title") is not None:
                    lenses[idx]["title"] = str(edit["title"]).strip()
                if edit.get("targets") is not None:
                    lenses[idx]["targets"] = self._lens_targets(edit["targets"])
        else:
            raise WbError("edit.type must be insert, update or remove.")
        return {"targetId": tid, "type": "lens", "uncategorized": self.uncategorized(snap)}

    def changed(self, snap):
        pins = snap.get("pins")
        if not pins or pins["base"] == pins["head"]:
            return []
        root = self.repository(pins["repositoryId"])["path"]
        files = V.changed_files(root, pins["base"], pins["head"])
        struct = {}
        ev = V.structural_diff(root, pins["base"], pins["head"])
        for e in ev or []:
            if e.get("type") == "file" and "diff" in e and e["diff"].get("type") == "text":
                path = (e["file"].get("rhs") or e["file"].get("lhs"))["path"]
                sc = e["diff"].get("structural_changes") or {}
                struct[path] = {side: {ln + 1 for s, en in sc.get(side, []) for ln in range(s, en)}
                                for side in ("base", "head")}
        for f in files:
            if f["path"] in struct:
                f["changed"] = struct[f["path"]]
                f["mode"] = "structural"
            else:
                hr = V.hunk_ranges(root, pins["base"], pins["head"], f["path"], f.get("previousPath"))
                f["changed"] = {side: {ln for a, b in hr[side] for ln in range(a, b + 1)}
                                for side in ("base", "head")}
                f["mode"] = "textual"
        return files

    def lens_coverage(self, snap, lens, files):
        import fnmatch
        cov = {}
        for tg in lens["targets"]:
            if tg["kind"] == "files":
                for f in files:
                    names = [f["path"]] + ([f["previousPath"]] if f.get("previousPath") else [])
                    if any(n == p.lstrip("./") or fnmatch.fnmatch(n, p.lstrip("./"))
                           for p in tg["patterns"] for n in names):
                        for side in ("base", "head"):
                            key = (side, f["path"])
                            cov.setdefault(key, set()).update(f["changed"][side])
            else:
                for s in tg["sources"]:
                    for side, file, a, b in D.selection_anchors(s):
                        f = next((x for x in files if x["path"] == file or x.get("previousPath") == file), None)
                        if not f:
                            continue
                        sel = {ln for ln in range(a, b + 1)} & f["changed"][side]
                        cov.setdefault((side, f["path"]), set()).update(sel)
        return cov

    def uncategorized(self, snap, files=None):
        files = self.changed(snap) if files is None else files
        covered = {}
        for lens in snap.get("lenses") or []:
            for k, v in self.lens_coverage(snap, lens, files).items():
                covered.setdefault(k, set()).update(v)
        out, total = [], 0
        for f in files:
            left = {}
            for side in ("base", "head"):
                rest = sorted(f["changed"][side] - covered.get((side, f["path"]), set()))
                if rest:
                    left[side] = rest
            n = sum(len(v) for v in left.values())
            if n:
                total += n
                ranges = []
                for side, lines in left.items():
                    start = prev = lines[0]
                    for ln in lines[1:] + [None]:
                        if ln is not None and ln == prev + 1:
                            prev = ln
                            continue
                        ranges.append({"side": side, "fromLine": start, "toLine": prev})
                        if ln is not None:
                            start = prev = ln
                out.append({"path": f["path"], "lines": n, "ranges": ranges})
        res = {"lines": total, "files": out[:50]}
        if len(out) > 50:
            res["moreFiles"] = len(out) - 50
        return res

    def lenses_report(self, review_id):
        snap = self.snapshot(review_id)
        files = self.changed(snap)
        lenses = []
        for lens in snap.get("lenses") or []:
            cov = self.lens_coverage(snap, lens, files)
            paths = {p for (_s, p), v in cov.items()}
            item = {"id": lens["id"], "title": lens["title"], "targets": lens["targets"],
                    "fileCount": len(paths)}
            if not paths:
                item["unavailable"] = "No files match these targets"
            lenses.append(item)
        return {"version": snap["version"], "lenses": lenses,
                "uncategorized": self.uncategorized(snap, files)}

    def _fingerprint(self, snap, f):
        pins = snap["pins"]
        root = self.repository(pins["repositoryId"])["path"]
        b = V.read_file(root, pins["base"], f.get("previousPath") or f["path"]) or b""
        h = V.read_file(root, pins["head"], f["path"]) or b""
        return hashlib.sha256(json.dumps([pins["repositoryId"],
                                          hashlib.sha256(b).hexdigest(),
                                          hashlib.sha256(h).hexdigest()]).encode()).hexdigest()

    def progress(self, review_id):
        snap = self.snapshot(review_id)
        files = self.changed(snap)
        stored = {r[0]: (r[1], json.loads(r[2])) for r in self._db.execute(
            "SELECT file, fingerprint, coverage FROM coverage WHERE review_id=?", (review_id,))}
        out_files, tot, rem = [], 0, 0
        viewed_sets = {}
        for f in files:
            fp = self._fingerprint(snap, f)
            viewed = {"base": set(), "head": set()}
            if f["path"] in stored and stored[f["path"]][0] == fp:
                viewed = {k: set(v) for k, v in stored[f["path"]][1].items()}
            viewed_sets[f["path"]] = viewed
            total = {s: len(f["changed"][s]) for s in ("base", "head")}
            left = {s: len(f["changed"][s] - viewed[s]) for s in ("base", "head")}
            n, r = sum(total.values()), sum(left.values())
            tot += n
            rem += r
            state = ("viewed" if n and not r else "partial" if r < n else "unread")
            out_files.append({"path": f["path"], "previousPath": f.get("previousPath"),
                              "status": f["status"], "fingerprint": fp, "state": state,
                              "total": {"additions": total["head"], "deletions": total["base"]},
                              "remaining": {"additions": left["head"], "deletions": left["base"]},
                              "mode": f.get("mode")})
        lenses = []
        for lens in (snap.get("lenses") or []) + [None]:
            if lens is None:
                cov = {}
                for L in snap.get("lenses") or []:
                    for k, v in self.lens_coverage(snap, L, files).items():
                        cov.setdefault(k, set()).update(v)
                cov = {(s, f["path"]): f["changed"][s] - cov.get((s, f["path"]), set())
                       for f in files for s in ("base", "head")}
                lid, title = "automatic-uncategorized", "Uncategorized changes"
            else:
                cov = self.lens_coverage(snap, lens, files)
                lid, title = lens["id"], lens["title"]
            t_add = t_del = r_add = r_del = 0
            paths = set()
            for (side, path), lines in cov.items():
                if not lines:
                    continue
                paths.add(path)
                left = lines - viewed_sets.get(path, {}).get(side, set())
                if side == "head":
                    t_add += len(lines); r_add += len(left)
                else:
                    t_del += len(lines); r_del += len(left)
            n, r = t_add + t_del, r_add + r_del
            if lens is None and not n:
                continue
            lenses.append({"id": lid, "title": title, "files": sorted(paths),
                           "total": {"additions": t_add, "deletions": t_del},
                           "remaining": {"additions": r_add, "deletions": r_del},
                           "state": "viewed" if n and not r else "partial" if r < n else "unread"})
        return {"version": snap["version"], "files": out_files, "lenses": lenses,
                "total": tot, "remaining": rem, "complete": tot > 0 and rem == 0}

    def mark_viewed(self, review_id, path, viewed=True, ranges=None, lens_id=None):
        snap = self.snapshot(review_id)
        files = self.changed(snap)
        f = next((x for x in files if x["path"] == path), None)
        if f is None:
            raise WbError(f"{path} is not in this whiteboard's change.", 404)
        scope = {s: set(f["changed"][s]) for s in ("base", "head")}
        if lens_id:
            lens = next((x for x in snap.get("lenses") or [] if x["id"] == lens_id), None)
            if lens:
                cov = self.lens_coverage(snap, lens, files)
                scope = {s: cov.get((s, path), set()) for s in ("base", "head")}
        if ranges:
            sel = {"base": set(), "head": set()}
            for r in ranges:
                sel[r["side"]].update(range(int(r["fromLine"]), int(r["toLine"]) + 1))
            scope = {s: scope[s] & sel[s] for s in scope}
        fp = self._fingerprint(snap, f)
        with self._lock:
            row = self._one("SELECT fingerprint, coverage FROM coverage WHERE review_id=? AND file=?",
                            (review_id, path))
            cur = {"base": set(), "head": set()}
            if row and row[0] == fp:
                cur = {k: set(v) for k, v in json.loads(row[1]).items()}
            for s in ("base", "head"):
                cur[s] = (cur[s] | scope[s]) if viewed else (cur[s] - scope[s])
            self._db.execute("INSERT OR REPLACE INTO coverage VALUES(?,?,?,?)",
                             (review_id, path, fp, json.dumps({k: sorted(v) for k, v in cur.items()})))
        self._notify(review_id)
        return self.progress(review_id)

    def upload(self, res):
        if not isinstance(res, dict):
            raise WbError("upload must be an object.")
        rid, kind = res.get("id") or str(uuid.uuid4()), res.get("kind")
        repo_id = res.get("repositoryId") or ""
        if kind == "trace":
            tr = res.get("trace") or {}
            if not isinstance(tr.get("events"), list) or not str(tr.get("label") or "").strip():
                raise WbError("trace needs {label, events:[{id, role, text}]}.")
            ids = set()
            for e in tr["events"]:
                if not str(e.get("id") or "").strip() or e.get("role") not in ("user", "assistant", "tool"):
                    raise WbError("Each trace event needs a non-empty id and role user|assistant|tool.")
                if e["id"] in ids:
                    raise WbError("Trace event ids must be unique.")
                ids.add(e["id"])
            data = json.dumps({"label": tr["label"], "events": [
                {"id": str(e["id"]), "role": e["role"], "text": str(e.get("text") or "")}
                for e in tr["events"]], "provenance": tr.get("provenance") or "client_supplied"},
                ensure_ascii=False).encode()
            mime = "application/json"
        elif kind == "image":
            try:
                data = base64.b64decode(res.get("base64") or "", validate=True)
            except Exception:
                raise WbError("image.base64 is not valid base64.")
            if not data.startswith(b"\x89PNG"):
                raise WbError("Images must be PNG.")
            mime = "image/png"
        elif kind == "map":
            model = res.get("model") or {}
            if not isinstance(model.get("systems"), dict):
                raise WbError("map.model needs systems:{…} (containers → components with coverage.files) "
                              "and relationships:[…].")
            data = json.dumps({"side": res.get("side") or "head", "pins": res.get("pins"),
                               "model": model}, ensure_ascii=False).encode()
            mime = "application/json"
        else:
            raise WbError('upload.kind must be "trace", "image" or "map".')
        if len(data) > 8 * 1024 * 1024:
            raise WbError("An upload is at most 8 MiB.")
        with self._lock:
            row = self._one("SELECT data FROM resources WHERE id=?", (rid,))
            if row and bytes(row[0]) != data:
                raise WbError("Reusing an upload ID requires identical content.", 409)
            if not row:
                self._db.execute("INSERT INTO resources VALUES(?,?,?,?,?)",
                                 (rid, repo_id, kind, mime, data))
        return {"id": rid, "kind": kind, "mimeType": mime}

    def brief_get(self, key, fingerprint):
        row = self._one("SELECT fingerprint, brief FROM briefs WHERE key=?", (key,))
        return json.loads(row[1]) if row and row[0] == fingerprint else None

    def brief_put(self, key, fingerprint, brief):
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO briefs VALUES(?,?,?,?)",
                             (key, fingerprint, json.dumps(brief, ensure_ascii=False), _now()))

    def plan_get(self, review_id):
        row = self._one("SELECT plan FROM plans WHERE review_id=?", (review_id,))
        return json.loads(row[0]) if row else None

    def plan_put(self, review_id, plan):
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO plans VALUES(?,?)",
                             (review_id, json.dumps(plan, ensure_ascii=False)))

    def staleness(self, review_id):
        snap = self.snapshot(review_id)
        pins = snap.get("pins")
        if not pins or snap.get("kind") == "scratchpad":
            return {"kind": "scratchpad", "updatable": False, "stale": False}
        try:
            root = self.repository(pins["repositoryId"])["path"]
        except WbError as e:
            return {"kind": "unknown", "updatable": False, "stale": False, "error": str(e)}
        now = V.head_commit(root)
        if pins["head"] == V.WORKTREE:
            moved = bool(now) and now != pins["base"]
            fp = snap.get("worktreeFingerprint")
            changed = bool(fp) and fp != V.worktree_fingerprint(root)
            return {"kind": "changes", "updatable": True, "stale": moved or changed,
                    "pinned": pins["base"], "current": now,
                    "ahead": V.ahead_count(root, pins["base"], now) if moved else 0,
                    "worktreeChanged": changed}
        if pins["base"] == pins["head"]:
            return {"kind": "overview", "updatable": True, "stale": bool(now) and now != pins["head"],
                    "pinned": pins["head"], "current": now,
                    "ahead": V.ahead_count(root, pins["head"], now)}
        return {"kind": "commit", "updatable": False, "stale": False,
                "pinned": pins["head"], "current": now}

    def ids(self):
        return [r[0] for r in self._db.execute("SELECT id FROM reviews").fetchall()]

    def version(self, review_id):
        row = self._one("SELECT version FROM reviews WHERE id=?", (review_id,))
        return row[0] if row else None

    def portable(self, review_id):
        snap = self.snapshot(review_id)
        res = {}
        for _kind, xid, _b in D.resource_refs(snap.get("document") or []):
            if xid in res:
                continue
            row = self._one("SELECT kind, mime_type, data FROM resources WHERE id=?", (xid,))
            if row:
                res[xid] = {"kind": row[0], "mimeType": row[1],
                            "base64": base64.b64encode(bytes(row[2])).decode()}
        repo = None
        pins = snap.get("pins")
        if pins:
            commits = [pins[k] for k in ("base", "head") if pins.get(k) and pins[k] != V.WORKTREE]
            try:
                name = self.repository(pins["repositoryId"])["name"]
            except WbError:
                link = self._one("SELECT name FROM repo_links WHERE id=?", (pins["repositoryId"],))
                name = link[0] if link else ""
            repo = {"id": pins["repositoryId"], "name": name, "commits": commits}
        return {"snapshot": snap, "resources": res, "repository": repo}

    def import_portable(self, review_id, board, version):
        snap = copy.deepcopy(board["snapshot"])
        snap["reviewId"], snap["version"] = review_id, int(version)
        repo = board.get("repository") or {}
        used = [int(m) for m in re.findall(r'"id": "[a-z_]+-(\d+)"', json.dumps(snap))]
        with self._lock:
            have = self.version(review_id)
            if have is not None and have >= snap["version"]:
                return False
            for xid, r in (board.get("resources") or {}).items():
                self._db.execute("INSERT OR IGNORE INTO resources VALUES(?,?,?,?,?)",
                                 (xid, repo.get("id") or "", r.get("kind"), r.get("mimeType"),
                                  base64.b64decode(r.get("base64") or "")))
            rid = repo.get("id")
            if rid and not self._one("SELECT 1 FROM repositories WHERE id=?", (rid,)):
                self._db.execute("INSERT OR IGNORE INTO repo_links VALUES(?,?,?,?,NULL)",
                                 (rid, repo.get("name") or "", json.dumps(repo.get("commits") or []),
                                  snap.get("project") or ""))
            row = self._one("SELECT next_id FROM reviews WHERE id=?", (review_id,))
            next_id = max([row[0] if row else 0] + used)
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute("INSERT OR REPLACE INTO versions VALUES(?,?,?)",
                                 (review_id, snap["version"], json.dumps(snap, ensure_ascii=False)))
                self._db.execute("INSERT OR REPLACE INTO reviews VALUES(?,?,?)",
                                 (review_id, snap["version"], next_id))
                self._db.execute("INSERT OR REPLACE INTO journal_marks VALUES(?,?)",
                                 (review_id, snap["version"]))
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
        self._notify(review_id)
        return True

    def forget(self, review_id):
        with self._lock:
            if not self.exists(review_id):
                return False
            for tbl in ("leases", "coverage", "attention", "versions", "journal_marks"):
                self._db.execute(f"DELETE FROM {tbl} WHERE review_id=?", (review_id,))
            self._db.execute("DELETE FROM reviews WHERE id=?", (review_id,))
        self._notify(review_id)
        return True

    def resource(self, rid):
        row = self._one("SELECT kind, mime_type, data FROM resources WHERE id=?", (rid,))
        if not row:
            raise WbError(f"Resource {rid} does not exist.", 404)
        return {"kind": row[0], "mimeType": row[1], "data": bytes(row[2])}


_STORES = {}


def store(home=None):
    home = home or os.environ.get("HOPLITA_HOME") or os.path.expanduser("~/.hoplita")
    if home not in _STORES:
        _STORES[home] = Store(home)
    return _STORES[home]
