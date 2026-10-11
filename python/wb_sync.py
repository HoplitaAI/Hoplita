#!/usr/bin/env python3
from datetime import datetime

import wb_store as S

STORE = "whiteboards"

_HOOKS = {"sql": None, "handle": None, "shared": None}


def setup(sql_getter, handle_getter, on_project_change=None):
    _HOOKS.update(sql=sql_getter, handle=handle_getter, shared=on_project_change)
    st = S.store()
    if _on_local_change not in st.listeners:
        st.listeners.append(_on_local_change)


def _sql():
    try:
        return _HOOKS["sql"]() if _HOOKS["sql"] else None
    except Exception:
        return None


def _me():
    try:
        return (_HOOKS["handle"]() if _HOOKS["handle"] else "") or ""
    except Exception:
        return ""


def _now():
    return datetime.now().isoformat()


def _rows(sql):
    return {r["uuid"]: r for r in (sql.load(STORE) or []) if isinstance(r, dict) and r.get("uuid")}


def _mirror(sql, wid):
    st = S.store()
    board = st.portable(wid)
    snap = board["snapshot"]
    row = {"uuid": wid, "rev": snap["version"], "editor": _me(), "deleted": False,
           "updated_at": _now(),
           "payload": {"wbid": wid, "title": snap.get("title") or "",
                       "project": snap.get("project") or "",
                       "kind": snap.get("kind") or "review", "board": board}}
    sql.upsert(STORE, wid, row)
    return row


def _on_local_change(wid, deleted):
    sql = _sql()
    if sql is None or not wid:
        return
    if deleted:
        prior = _rows(sql).get(wid) or {}
        pp = prior.get("payload") or {}
        row = {"uuid": wid, "rev": int(prior.get("rev") or 0) + 1, "editor": _me(),
               "deleted": True, "updated_at": _now(),
               "payload": {"wbid": wid, "title": pp.get("title") or "",
                           "project": pp.get("project") or "", "kind": pp.get("kind") or "review"}}
        sql.upsert(STORE, wid, row)
    else:
        row = _mirror(sql, wid)
    project = row["payload"].get("project")
    if project and row["payload"].get("kind") != "scratchpad" and _HOOKS["shared"]:
        try:
            _HOOKS["shared"](project)
        except Exception:
            pass


def _apply(sql, row):
    st = S.store()
    wid, rev = row["uuid"], int(row.get("rev") or 0)
    have = st.version(wid)
    if row.get("deleted"):
        if have is not None and have < rev:
            return st.forget(wid)
        if have is not None:
            _mirror(sql, wid)
        return False
    if have is not None and have > rev:
        _mirror(sql, wid)
        return False
    p = row.get("payload") or {}
    board = p.get("board")
    if not board:
        return False
    snap = dict(board["snapshot"])
    snap["title"] = p.get("title") or snap.get("title")
    if p.get("project"):
        snap["project"] = p["project"]
    if have is not None and have == rev:
        if _same(st.snapshot(wid), snap):
            return False
        rev = have + 1
        st.import_portable(wid, {**board, "snapshot": snap}, rev)
        _mirror(sql, wid)
        return True
    return st.import_portable(wid, {**board, "snapshot": snap}, rev)


def _same(a, b):
    keys = ("title", "project", "pins", "target", "document", "lenses")
    return all(a.get(k) == b.get(k) for k in keys)


def reconcile():
    sql = _sql()
    if sql is None:
        return 0
    rows = _rows(sql)
    changed = 0
    for row in rows.values():
        try:
            changed += bool(_apply(sql, row))
        except Exception as e:
            print(f"[WB-SYNC] {row.get('uuid')}: {e}")
    for wid in set(S.store().ids()) - set(rows):
        try:
            _mirror(sql, wid)
        except Exception as e:
            print(f"[WB-SYNC] mirror {wid}: {e}")
    return changed


class ProjectBoards:

    _ITEM = ("uuid", "rev", "editor", "payload", "deleted", "local_only")

    def fed_items(self, project):
        sql = _sql()
        if sql is None:
            return []
        return [{k: r.get(k) for k in self._ITEM}
                for r in _rows(sql).values()
                if (r.get("payload") or {}).get("project") == project
                and (r.get("payload") or {}).get("kind") != "scratchpad"]

    def fed_upsert(self, project, item):
        sql = _sql()
        if sql is None:
            return
        row = {**item, "updated_at": _now()}
        sql.upsert(STORE, item["uuid"], row)
        try:
            _apply(sql, row)
        except Exception as e:
            print(f"[WB-SYNC] {item.get('uuid')}: {e}")
