import fed_sync as fs


def push_shared(store, resource):
    return [it for it in store.fed_items(resource) if not it.get("local_only")]


def pull_shared(store, resource, incoming, my_handle, uuid_gen, title_key="title"):
    local = {it["uuid"]: it for it in store.fed_items(resource)}
    by_title = {}
    for it in local.values():
        by_title.setdefault((it.get("payload") or {}).get(title_key, ""), it["uuid"])
    fixed = []
    for inc in incoming:
        if inc.get("uuid") not in local:
            twin = by_title.get((inc.get("payload") or {}).get(title_key, ""))
            if twin is not None:
                inc = dict(inc); inc["uuid"] = twin
        fixed.append(inc)
    state, forks, changed = fs.merge_incoming(local, fixed, my_handle,
                                              uuid_gen=uuid_gen)
    details = []
    for u in changed:
        it = state[u]
        details.append({
            "uuid": u,
            "title": (it.get("payload") or {}).get(title_key, ""),
            "added": u not in local,
            "editor": it.get("editor") or "",
            "deleted": bool(it.get("deleted")),
        })
    for u in changed:
        store.fed_upsert(resource, state[u])
    for f in forks:
        store.fed_upsert(resource, f)
    return {"changed": changed, "details": details,
            "forks": [f["uuid"] for f in forks]}


def save_from_gui(store, resource, uuid, base_rev, payload, editor, uuid_gen):
    local = {it["uuid"]: it for it in store.fed_items(resource)}
    stored = local.get(uuid)
    r = fs.guarded_save(stored, base_rev, payload, editor, fork_uuid=uuid_gen())
    if r["op"] == "fork":
        store.fed_upsert(resource, r["fork"])
        return {"conflict": True, "fork_uuid": r["fork"]["uuid"], "kept": r["item"]}
    store.fed_upsert(resource, r["item"])
    return {"conflict": False, "item": r["item"]}


