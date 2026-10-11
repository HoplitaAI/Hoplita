
LOCAL_SUFFIX = "-local"
TITLE_KEY = "title"



def edited(item, payload, editor):
    out = dict(item)
    out["payload"] = dict(payload)
    out["editor"] = editor
    out["rev"] = int(item["rev"]) + 1
    out["deleted"] = False
    return out


def tombstoned(item, editor):
    out = dict(item)
    out["editor"] = editor
    out["rev"] = int(item["rev"]) + 1
    out["deleted"] = True
    return out


def _winner(a, b):
    return a if (a.get("editor") or "") >= (b.get("editor") or "") else b


def _fork_of(loser, my_handle, fork_uuid, title_key=TITLE_KEY, suffix=LOCAL_SUFFIX):
    fp = dict(loser["payload"])
    fp[title_key] = f"{fp.get(title_key, '')}{suffix}"
    return {"uuid": fork_uuid, "rev": 1, "editor": my_handle,
            "payload": fp, "deleted": False, "local_only": True}



def reconcile(local, incoming, my_handle, fork_uuid=None,
              title_key=TITLE_KEY, suffix=LOCAL_SUFFIX):
    if local is None:
        return {"op": "add", "item": incoming, "fork": None}

    lr, ir = int(local["rev"]), int(incoming["rev"])
    if ir > lr:
        return {"op": "apply", "item": incoming, "fork": None}
    if ir < lr:
        return {"op": "discard", "item": local, "fork": None}

    same = (local["payload"] == incoming["payload"]
            and bool(local["deleted"]) == bool(incoming["deleted"]))
    if same:
        return {"op": "idempotent", "item": local, "fork": None}

    win = _winner(local, incoming)
    resolved = dict(win)
    resolved["rev"] = lr + 1
    loser = incoming if win is local else local
    fork = None
    if (loser.get("editor") or "") == my_handle and not loser.get("deleted"):
        fork = _fork_of(loser, my_handle, fork_uuid or (loser["uuid"] + suffix),
                        title_key=title_key, suffix=suffix)
    return {"op": "resolve", "item": resolved, "fork": fork}


def merge_incoming(local_by_uuid, incoming_items, my_handle, uuid_gen=None,
                   title_key=TITLE_KEY, suffix=LOCAL_SUFFIX):
    state = dict(local_by_uuid)
    forks, changed = [], []
    for inc in incoming_items:
        u = inc["uuid"]
        cur = state.get(u)
        fu = uuid_gen() if uuid_gen else None
        act = reconcile(cur, inc, my_handle, fork_uuid=fu,
                        title_key=title_key, suffix=suffix)
        if act["op"] in ("add", "apply", "resolve"):
            state[u] = act["item"]
            changed.append(u)
        if act.get("fork"):
            forks.append(act["fork"])
            state[act["fork"]["uuid"]] = act["fork"]
    return state, forks, changed



def guarded_save(stored, base_rev, new_payload, editor, fork_uuid=None,
                 title_key=TITLE_KEY, suffix=LOCAL_SUFFIX):
    if stored is not None and int(stored["rev"]) > int(base_rev):
        return {"op": "fork", "item": stored,
                "fork": _fork_of({"uuid": stored["uuid"], "payload": new_payload,
                                  "editor": editor, "deleted": False},
                                 editor, fork_uuid or (stored["uuid"] + suffix),
                                 title_key=title_key, suffix=suffix)}
    cur_rev = int(stored["rev"]) if stored is not None else int(base_rev)
    item = {
        "uuid": stored["uuid"] if stored is not None else fork_uuid,
        "rev": cur_rev + 1,
        "editor": editor,
        "payload": dict(new_payload),
        "deleted": False,
        "local_only": bool(stored.get("local_only")) if stored is not None else False,
    }
    return {"op": "save", "item": item, "fork": None}
