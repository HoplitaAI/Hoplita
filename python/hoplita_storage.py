
import json
import os
import shutil
import threading
import time
from datetime import datetime

TOMBSTONE_STORE = "_tombstones"
_TOMBSTONE_STORES = set()


_CHANGE_HOOK = None


def set_change_hook(fn):
    global _CHANGE_HOOK
    _CHANGE_HOOK = fn


def _changed(store):
    if _CHANGE_HOOK is None:
        return
    try:
        _CHANGE_HOOK(store)
    except Exception:
        pass


def set_tombstone_stores(stores):
    global _TOMBSTONE_STORES
    _TOMBSTONE_STORES = {s for s in (stores or ())
                         if s != TOMBSTONE_STORE and not s.endswith("__blob")}

try:
    import sqlcipher3 as _sqlcipher
except ImportError:
    try:
        from pysqlcipher3 import dbapi2 as _sqlcipher
    except ImportError:
        _sqlcipher = None

from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend


def backend_available():
    return _sqlcipher is not None


class BadKeyError(Exception):
    pass


_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    store       TEXT NOT NULL,
    key         TEXT NOT NULL,
    rev         INTEGER NOT NULL DEFAULT 1,
    editor      TEXT NOT NULL DEFAULT '',
    deleted     INTEGER NOT NULL DEFAULT 0,
    local_only  INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL DEFAULT '',
    payload     TEXT NOT NULL,
    PRIMARY KEY (store, key)
);
CREATE INDEX IF NOT EXISTS ix_items_store ON items(store, deleted);
CREATE INDEX IF NOT EXISTS ix_items_rev   ON items(store, rev);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_SCHEMA_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS msgs_fts USING fts5(
    session, role, text,
    ts UNINDEXED, tab UNINDEXED,
    tokenize = "unicode61 remove_diacritics 2"
);
"""


def _envelope_from(payload):
    return (
        int(payload.get("rev", 1) or 1),
        str(payload.get("editor", "") or ""),
        1 if payload.get("deleted") else 0,
        1 if payload.get("local_only") else 0,
        str(payload.get("updated_at") or payload.get("updated") or ""),
    )


class SqlStore:
    PBKDF2_ITERATIONS = 600_000
    SALT_SIZE = 32
    BACKUP_KEEP = 30
    BACKUP_THROTTLE_S = 600

    def __init__(self, db_path, password):
        if _sqlcipher is None:
            raise RuntimeError("sqlcipher3 not installed (pip install sqlcipher3-binary)")
        self.db_path = db_path
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        key_hex = self._derive_key_hex(password)
        self._conn = _sqlcipher.connect(db_path, check_same_thread=False)
        self._conn.execute(f"PRAGMA key = \"x'{key_hex}'\"")
        try:
            self._conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except Exception as e:
            try:
                self._conn.close()
            except Exception:
                pass
            raise BadKeyError(str(e)) from e
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(_SCHEMA)
        self.fts = True
        try:
            self._conn.executescript(_SCHEMA_FTS)
        except Exception as e:
            self.fts = False
            print(f"[SQL] FTS5 unavailable, the log will not be searchable: {e}")
        self._conn.commit()

    def _derive_key_hex(self, password):
        salt_path = self.db_path + ".salt"
        if os.path.exists(salt_path):
            with open(salt_path, "rb") as f:
                salt = f.read()
        else:
            salt = os.urandom(self.SALT_SIZE)
            with open(salt_path, "wb") as f:
                f.write(salt)
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                         iterations=self.PBKDF2_ITERATIONS,
                         backend=default_backend())
        return kdf.derive(password.encode("utf-8")).hex().upper()

    def meta_get(self, key):
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return row[0] if row else None

    def meta_set(self, key, value):
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                (key, str(value)))
            self._conn.commit()

    def load(self, store):
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM items WHERE store = ?", (store,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def upsert(self, store, key, payload):
        rev, editor, deleted, local_only, updated_at = _envelope_from(payload)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO items"
                "(store, key, rev, editor, deleted, local_only, updated_at, payload)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (store, str(key), rev, editor, deleted, local_only, updated_at,
                 json.dumps(payload, ensure_ascii=False)))
            self._conn.commit()
        _changed(store)

    def delete(self, store, key):
        with self._lock:
            self._conn.execute(
                "DELETE FROM items WHERE store = ? AND key = ?", (store, str(key)))
            self._conn.commit()
        self._record_tombstone(store, [key])
        _changed(store)

    def delete_raw(self, store, key):
        with self._lock:
            self._conn.execute(
                "DELETE FROM items WHERE store = ? AND key = ?", (store, str(key)))
            self._conn.commit()

    def count(self, store):
        with self._lock:
            return self._conn.execute(
                "SELECT count(*) FROM items WHERE store = ?", (store,)).fetchone()[0]

    def replace_all(self, store, keyed_payloads):
        with self._lock:
            cur = self._conn.cursor()
            gone = []
            if store in _TOMBSTONE_STORES:
                new_keys = {str(k) for k, _ in keyed_payloads}
                gone = [r[0] for r in cur.execute(
                    "SELECT key FROM items WHERE store = ?", (store,))
                    .fetchall() if r[0] not in new_keys]
            cur.execute("DELETE FROM items WHERE store = ?", (store,))
            cur.executemany(
                "INSERT OR REPLACE INTO items"
                "(store, key, rev, editor, deleted, local_only, updated_at, payload)"
                " VALUES (?,?,?,?,?,?,?,?)",
                [(store, str(k), *_envelope_from(p),
                  json.dumps(p, ensure_ascii=False)) for k, p in keyed_payloads])
            self._conn.commit()
        self._record_tombstone(store, gone)
        _changed(store)

    def _record_tombstone(self, store, keys):
        if store == TOMBSTONE_STORE or store not in _TOMBSTONE_STORES:
            return
        ts = datetime.now().isoformat()
        for k in keys:
            self.upsert(TOMBSTONE_STORE, f"{store}:{k}",
                        {"key": f"{store}:{k}", "store": store,
                         "rowkey": str(k), "ts": ts})

    SAID_ROLES = ("user", "assistant")
    ROLE_TOOL = "tool"
    ROLE_SUMMARY = "summary"

    @staticmethod
    def _role_clause(roles):
        want = tuple(roles) if roles else SqlStore.SAID_ROLES
        if want == ("*",):
            return "", []
        return (" AND role IN (%s)" % ",".join("?" * len(want)),
                list(want))

    def log_message(self, session, role, text, ts="", tab=""):
        if not self.fts or not (text or "").strip():
            return False
        with self._lock:
            self._conn.execute(
                "INSERT INTO msgs_fts(session, role, text, ts, tab)"
                " VALUES (?,?,?,?,?)",
                (str(session or ""), str(role or ""), str(text),
                 str(ts or ""), str(tab or "")))
            self._conn.commit()
        return True

    @staticmethod
    def fts_query(text):
        import re as _re
        pal = [w for w in _re.findall(r"[\w]{3,}", (text or "").lower())]
        if not pal:
            return ""
        term = []
        for w in dict.fromkeys(pal):
            term.append(f'"{w}"*')
            if len(w) >= 6:
                term.append(f'"{w[:-2]}"*')
        return " OR ".join(term)

    def word_df(self, word):
        if not self.fts:
            return 0
        w = (word or "").strip().lower()
        if not w:
            return 0
        try:
            with self._lock:
                return self._conn.execute(
                    "SELECT count(*) FROM msgs_fts WHERE msgs_fts MATCH ?",
                    (f'"{w}"*',)).fetchone()[0]
        except Exception:
            return 0

    def search_messages(self, query, limit=8, session=None, roles=None):
        q = self.fts_query(query)
        if not self.fts or not q:
            return []
        sql = ("SELECT rowid, session, role, text, ts, tab, bm25(msgs_fts) AS s"
               " FROM msgs_fts WHERE msgs_fts MATCH ?")
        args = [q]
        _rc, _ra = self._role_clause(roles)
        sql += _rc
        args += _ra
        if session:
            sql += " AND session = ?"
            args.append(str(session))
        sql += " ORDER BY s LIMIT ?"
        args.append(int(limit))
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [{"rowid": r[0], "session": r[1], "role": r[2], "text": r[3],
                 "ts": r[4], "tab": r[5], "score": round(float(r[6]), 3)}
                for r in rows]

    def search_moments(self, query, limit=5, window_min=5, scan=300,
                       roles=None, since=""):
        import datetime as _dt
        from hoplita_search import group_and_score
        q = self.fts_query(query)
        if not self.fts or not q:
            return []
        _rc, _ra = self._role_clause(roles)
        _sc, _sa = (" AND ts > ?", [str(since)]) if since else ("", [])
        with self._lock:
            rows = self._conn.execute(
                "SELECT rowid, session, role, text, ts FROM msgs_fts"
                " WHERE msgs_fts MATCH ?" + _rc + _sc
                + " ORDER BY bm25(msgs_fts) LIMIT ?",
                [q] + _ra + _sa + [int(scan)]).fetchall()
        if not rows:
            return []
        hits = [{"rowid": r[0], "session": r[1], "role": r[2], "text": r[3],
                 "ts": r[4]} for r in rows]
        sesiones = {s_: k for k, s_ in
                    enumerate(dict.fromkeys(h["session"] for h in hits))}

        def _pos(h):
            base = sesiones[h["session"]] * 10 ** 9
            try:
                return base + _dt.datetime.fromisoformat(
                    h["ts"]).timestamp() / 60.0
            except Exception:
                return base + h["rowid"]

        _df = self.word_df
        out = []
        for g in group_and_score(query, hits, lambda h: h["text"], _pos, _df,
                                 max(1, self.messages_count()),
                                 gap=window_min, k=limit):
            ids = [x["rowid"] for x in g["items"]]
            out.append({"session": g["items"][0]["session"],
                        "score": g["score"], "covers": g["covers"],
                        "n_hits": len(ids), "from_rowid": min(ids),
                        "to_rowid": max(ids),
                        "ts": next((x["ts"] for x in g["items"] if x["ts"]), "")})
        return out

    def moment_messages(self, from_rowid, to_rowid, pad=2, cap=40,
                        roles=("*",)):
        if not self.fts:
            return []
        _rc, _ra = self._role_clause(roles)
        with self._lock:
            rows = self._conn.execute(
                "SELECT rowid, session, role, text, ts FROM msgs_fts"
                " WHERE rowid BETWEEN ? AND ?" + _rc + " ORDER BY rowid LIMIT ?",
                [int(from_rowid) - pad, int(to_rowid) + pad] + _ra
                + [int(cap)]).fetchall()
        return [{"rowid": r[0], "session": r[1], "role": r[2], "text": r[3],
                 "ts": r[4]} for r in rows]

    def message_context(self, rowid, before=2, after=2):
        if not self.fts:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT rowid, session, role, text, ts FROM msgs_fts"
                " WHERE rowid BETWEEN ? AND ? ORDER BY rowid",
                (int(rowid) - int(before), int(rowid) + int(after))).fetchall()
        return [{"rowid": r[0], "session": r[1], "role": r[2], "text": r[3],
                 "ts": r[4]} for r in rows]

    def forget_messages(self, session=None, rowids=None):
        if not self.fts:
            return 0
        with self._lock:
            if rowids:
                qs = ",".join("?" * len(rowids))
                cur = self._conn.execute(
                    f"DELETE FROM msgs_fts WHERE rowid IN ({qs})",
                    [int(r) for r in rowids])
            elif session is not None:
                cur = self._conn.execute(
                    "DELETE FROM msgs_fts WHERE session = ?", (str(session),))
            else:
                cur = self._conn.execute("DELETE FROM msgs_fts")
            n = cur.rowcount
            self._conn.commit()
        return max(0, n)

    def sessions_logged(self):
        if not self.fts:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT session, count(*), min(ts), max(ts) FROM msgs_fts"
                " GROUP BY session ORDER BY max(ts) DESC").fetchall()
        return [{"session": r[0], "n": r[1], "first": r[2], "last": r[3]}
                for r in rows]

    def messages_in(self, session):
        if not self.fts:
            return 0
        with self._lock:
            return self._conn.execute(
                "SELECT count(*) FROM msgs_fts WHERE session = ?",
                (str(session or ""),)).fetchone()[0]

    def messages_count(self):
        if not self.fts:
            return 0
        with self._lock:
            return self._conn.execute(
                "SELECT count(*) FROM msgs_fts").fetchone()[0]

    def sync_store(self, store, current, shadow):
        with self._lock:
            cur = self._conn.cursor()
            for k, ser in current.items():
                if shadow.get(k) != ser:
                    p = json.loads(ser)
                    cur.execute(
                        "INSERT OR REPLACE INTO items"
                        "(store, key, rev, editor, deleted, local_only, updated_at, payload)"
                        " VALUES (?,?,?,?,?,?,?,?)",
                        (store, str(k), *_envelope_from(p), ser))
            gone = [k for k in shadow if k not in current]
            for k in gone:
                cur.execute("DELETE FROM items WHERE store = ? AND key = ?",
                            (store, str(k)))
            self._conn.commit()
        if gone:
            self._record_tombstone(store, gone)
            _changed(store)
        return dict(current)

    def maybe_backup(self):
        try:
            bdir = os.path.join(os.path.dirname(self.db_path), "backups")
            os.makedirs(bdir, exist_ok=True)
            base = os.path.basename(self.db_path)
            mine = sorted(f for f in os.listdir(bdir)
                          if f.startswith(base + ".") and not f.endswith(".salt"))
            if mine:
                newest = os.path.getmtime(os.path.join(bdir, mine[-1]))
                if time.time() - newest < self.BACKUP_THROTTLE_S:
                    return
            with self._lock:
                self._conn.execute("PRAGMA wal_checkpoint(FULL)")
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            shutil.copy2(self.db_path, os.path.join(bdir, f"{base}.{stamp}"))
            salt = self.db_path + ".salt"
            if os.path.exists(salt):
                shutil.copy2(salt, os.path.join(bdir, f"{base}.{stamp}.salt"))
            mine = sorted(f for f in os.listdir(bdir)
                          if f.startswith(base + ".") and not f.endswith(".salt"))
            for old in mine[:-self.BACKUP_KEEP]:
                os.remove(os.path.join(bdir, old))
                if os.path.exists(os.path.join(bdir, old + ".salt")):
                    os.remove(os.path.join(bdir, old + ".salt"))
        except Exception as e:
            print(f"[SQL] backup failed: {e}")

    def close(self):
        with self._lock:
            try:
                self._conn.commit()
                self._conn.close()
            except Exception:
                pass
