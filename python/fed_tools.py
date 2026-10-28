
import copy as _copy
import os
import threading as _threading
import sys as _sys
import time as _time

import hoplita_crypto as crypto
import hoplita_federation as fed
import fed_sync as fs
import fed_adapters as fa
import hoplita_children
import hoplita_storage as hs
import uuid as _uuid
from urllib.parse import urlparse as _urlparse


_get_store = None
_get_notes = None


def set_store_getter(getter):
    global _get_store
    _get_store = getter


def set_notes_getter(getter):
    global _get_notes
    _get_notes = getter


def _notes():
    if _get_notes is None:
        return None
    return _get_notes()



def _is_local_only(category):
    nm = _notes()
    try:
        return bool(nm is not None
                    and (nm.get_local_only() or {}).get(category))
    except Exception:
        return False


def _local_only_refusal(category):
    return {"error": "local_only",
            "message": f"The folder '{category}' is marked LOCAL-ONLY: "
                       f"nothing of it goes through the federation. The "
                       f"owner can lift the mark from the folder's menu."}


def _sync_row_visible(store, payload, locked):
    if store == "chat_sessions" and payload.get("local_only"):
        return False
    if not locked:
        return True
    if store == "notes" and payload.get("category") in locked:
        return False
    if store == "whiteboards" and (payload.get("payload") or {}).get("project") in locked:
        return False
    if store == "chat_sessions":
        cp = (payload.get("code_project") or "").rstrip("/\\")
        if cp and os.path.basename(cp) in locked:
            return False
    return True


def _sync_locked_set():
    nm = _notes()
    try:
        return {k for k, v in (nm.get_local_only() or {}).items()
                if v} if nm is not None else set()
    except Exception:
        return set()



_CODE_CHUNK = 900_000
_code_push_lock = _threading.Lock()

_project_journal_fn = None


def set_project_journal(fn):
    global _project_journal_fn
    _project_journal_fn = fn


def _run_git(args, cwd, timeout=180):
    import subprocess
    import hoplita_paths as _hp
    try:
        r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                           text=True, timeout=timeout, **_hp.no_console())
        return r.returncode == 0, ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as e:
        return False, str(e)


def _code_cat(path):
    return os.path.basename((path or "").strip().rstrip("/\\"))


def _code_name_for(path):
    path = os.path.abspath((path or "").strip().rstrip("/\\") or ".")
    return _code_meta(f"code_name_for:{path}") or _code_cat(path)


def _code_meta(key, value=None):
    sql = _sql()
    if sql is None:
        return None
    if value is None:
        return sql.meta_get(key)
    sql.meta_set(key, value)
    return value


def _code_peers(cat, update=None):
    raw = _code_meta(f"code_peers:{cat}") or "{}"
    try:
        peers = _json.loads(raw)
    except Exception:
        peers = {}
    if update is not None:
        peers.update(update)
        _code_meta(f"code_peers:{cat}", _json.dumps(peers))
    return peers


def _code_projects(add=None):
    raw = _code_meta("code_projects") or "[]"
    try:
        cats = list(_json.loads(raw))
    except Exception:
        cats = []
    if add and add not in cats:
        cats.append(add)
        _code_meta("code_projects", _json.dumps(cats))
    return cats


def _code_branch(path):
    ok, out = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], path)
    return out if ok and out and out != "HEAD" else ""


def _code_tip(path):
    ok, out = _run_git(["rev-parse", "HEAD"], path)
    return out if ok else ""


def _code_bundle(path, base=None):
    import tempfile as _tmpf
    branch = _code_branch(path)
    tip = _code_tip(path)
    if not branch or not tip:
        return None, "not a git repository with a checked-out branch", None
    fd, fp = _tmpf.mkstemp(prefix="hoplita-code-", suffix=".bundle")
    os.close(fd)
    spec = f"{base}..{branch}" if base else branch
    ok, out = _run_git(["bundle", "create", fp, spec], path)
    if not ok:
        try:
            os.unlink(fp)
        except OSError:
            pass
        if base and ("empty" in out.lower() or "excluded" in out.lower()):
            return None, "up_to_date", None
        return None, f"git bundle failed: {out[:300]}", None
    return fp, branch, tip


def _code_chunks(bundle_path):
    import base64 as _b64
    with open(bundle_path, "rb") as f:
        data = _b64.b64encode(f.read()).decode()
    return [data[i:i + _CODE_CHUNK]
            for i in range(0, len(data), _CODE_CHUNK)] or [""]


def _code_join(parts_b64, out_path):
    import base64 as _b64
    with open(out_path, "wb") as f:
        f.write(_b64.b64decode("".join(parts_b64)))


def _code_send(handle, etype, cat, branch, base, tip, bundle_path):
    store, server_url, token = _require_session()
    my_pub = store.data.get("public_key_b64")
    recipient_pub, err = _trusted_pub(server_url, token, handle)
    if err:
        raise RuntimeError(err)
    chunks = _code_chunks(bundle_path)
    xfer = _uuid.uuid4().hex
    sent_ids = []
    for i, chunk in enumerate(chunks):
        payload = {"project": cat, "branch": branch, "base": base or "",
                   "tip": tip, "xfer": xfer, "part": i,
                   "parts": len(chunks), "data": chunk}
        envelope = crypto.make_envelope(etype, payload, sender_pub_b64=my_pub)
        blob = _seal(_store(), recipient_pub, envelope)
        try:
            r = _with_relogin(lambda b=blob: fed.server_send_envelope(
                server_url, token, handle, etype, b, {}))
            if isinstance(r, dict) and r.get("envelope_id"):
                sent_ids.append(r["envelope_id"])
        except Exception:
            if sent_ids:
                try:
                    fed.server_withdraw(server_url, token, ids=sent_ids)
                except Exception:
                    pass
            raise
    return len(chunks), xfer


def _code_send_one(handle, etype, payload):
    store, server_url, token = _require_session()
    my_pub = store.data.get("public_key_b64")
    recipient_pub, err = _trusted_pub(server_url, token, handle)
    if err:
        raise RuntimeError(err)
    env = crypto.make_envelope(etype, payload, sender_pub_b64=my_pub)
    blob = _seal(_store(), recipient_pub, env)
    return _with_relogin(lambda: fed.server_send_envelope(
        server_url, token, handle, etype, blob, {}))


def _code_fetch_type(etype, ack_ids=None):
    store, server_url, token = _require_session()
    if ack_ids:
        fed.server_ack_envelopes(server_url, token, ack_ids)
        return []
    resp = fed.server_fetch_inbox_types(server_url, token, [etype], peek=True)
    my_priv = store.data.get("private_key_b64")
    out = []
    for env in resp.get("envelopes", []):
        try:
            dec = _unsealed(env)
            p = dec.get("payload", {})
        except Exception:
            continue
        sender = (env.get("sender") or env.get("from")
                  or dec.get("sender_handle") or "?")
        out.append((env.get("id"), sender, p))
    return out


def _code_xfers(rows):
    grouped = {}
    for env_id, sender, p in rows:
        x = grouped.setdefault(p.get("xfer") or "?", {
            "sender": sender, "project": p.get("project", ""),
            "branch": p.get("branch", ""), "base": p.get("base", ""),
            "tip": p.get("tip", ""), "total": int(p.get("parts") or 0),
            "ids": [], "got": {}})
        x["ids"].append(env_id)
        x["got"][int(p.get("part") or 0)] = p.get("data", "")
    done = {}
    for xid, x in grouped.items():
        if x["total"] and len(x["got"]) == x["total"]:
            x["parts"] = [x["got"][i] for i in range(x["total"])]
            done[xid] = x
    return done


def _code_journal(path, text):
    if _project_journal_fn is None:
        return
    try:
        _project_journal_fn(path, text)
    except Exception:
        pass


_CODE_RAILS = {"busy": False}


def _code_rails_async():
    if _CODE_RAILS["busy"]:
        return
    _CODE_RAILS["busy"] = True

    def _work():
        try:
            with fed.sync_pass():
                r = tool_fed_code_pull()
            for s in (r.get("served") or []):
                if s.get("parts"):
                    print(f"[CODE-RAILS] served '{s.get('project')}' to "
                          f"{s.get('to')}: {s['parts']} part(s), "
                          f"{_human_bytes(s.get('bytes'))}", file=_sys.stderr)
                elif s.get("error"):
                    print(f"[CODE-RAILS] ✗ could not serve "
                          f"'{s.get('project')}': {s['error']}",
                          file=_sys.stderr)
            for a in (r.get("applied") or []):
                if a.get("materialized"):
                    print(f"[CODE-RAILS] '{a.get('project')}' materialized at "
                          f"{a.get('path')}", file=_sys.stderr)
                elif a.get("error"):
                    print(f"[CODE-RAILS] ✗ '{a.get('project')}': {a['error']}",
                          file=_sys.stderr)
        except Exception as e:
            print(f"[CODE-RAILS] ✗ {e}", file=_sys.stderr)
        finally:
            _CODE_RAILS["busy"] = False

    _threading.Thread(target=_work, daemon=True,
                      name="code-rails").start()


def _human_bytes(n):
    n = int(n or 0)
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    if n >= 1024:
        return f"{n // 1024} KB"
    return f"{n} B"


def tool_fed_code_state():
    out = {"ok": True, "mailbox": {}, "offers": [], "awaiting": [],
           "accepted_yes_waiting_to_serve": [], "projects": {}}
    for _t in ("code_offer", "code_offer_ok", "code_invite", "code_pack"):
        try:
            rows = _code_fetch_type(_t)
            out["mailbox"][_t] = len(rows)
            if _t == "code_offer":
                out["offers"] = [{"from": s, "project": p.get("project"),
                                  "bytes": p.get("bytes")}
                                 for _i, s, p in rows]
            if _t == "code_offer_ok":
                out["accepted_yes_waiting_to_serve"] = [
                    {"from": s, "project": p.get("project")}
                    for _i, s, p in rows]
        except Exception as e:
            out["mailbox"][_t] = f"error: {e}"
    for p in (_code_projects() or []):
        _path = _code_meta(f"code_path:{p}") or ""
        out["projects"][p] = {
            "path": _path,
            "is_repo": bool(_path) and os.path.isdir(os.path.join(_path,
                                                                 ".git")),
            "awaiting_from": _code_meta(f"code_await:{p}") or "",
            "peers": _code_peers(p)}
        if out["projects"][p]["awaiting_from"]:
            out["awaiting"].append(p)
    out["message"] = (
        f"mailbox: {out['mailbox']} · awaiting: {out['awaiting'] or 'none'} · "
        f"yes-to-serve: {len(out['accepted_yes_waiting_to_serve'])}")
    return out


def _code_size_estimate(path):
    ok, out = _run_git(["count-objects", "-v"], path)
    if not ok:
        return 0
    total = 0
    for line in (out or "").splitlines():
        k, _, v = line.partition(":")
        if k.strip() in ("size", "size-pack"):
            try:
                total += int(v.strip()) * 1024
            except ValueError:
                pass
    return total


def tool_fed_share_code(path, handle):
    path = os.path.abspath((path or "").strip() or ".")
    cat = _code_name_for(path)
    if _is_local_only(cat):
        return _local_only_refusal(cat)
    if not os.path.isdir(os.path.join(path, ".git")):
        return {"error": f"'{path}' is not a git repository."}
    ok, branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], path)
    if not ok or not branch:
        return {"error": f"could not read the branch: {branch}"}
    _ok2, tip = _run_git(["rev-parse", "HEAD"], path)
    est = _code_size_estimate(path)
    need = int(est * 1.37) + 4096
    try:
        _st, _url, _tok = _require_session()
        _q = fed.server_quota(_url, _tok)
        _lim = int(((_q or {}).get("limits") or {}).get("max_inbox_bytes") or 0)
        _use = int(((_q or {}).get("usage") or {}).get("outbox_bytes") or 0)
        if _lim and need + _use > _lim:
            return {"error": (
                f"'{cat}' weighs ~{need // 1024} KB and you only have "
                f"{max(_lim - _use, 0) // 1024} KB of mail in flight left "
                f"(limit {_lim // 1024} KB). Nothing was sent. What is full is "
                f"YOUR mail in flight — parcels you sent that nobody has "
                f"collected yet — so free it with fed_code_retract on the peer "
                f"whose transfer died, or ask the operator for more room."),
                "needed_bytes": need, "limit_bytes": _lim}
    except Exception:
        pass
    xfer = _uuid.uuid4().hex
    try:
        _code_send_one(handle, "code_offer",
                       {"project": cat, "branch": branch, "tip": tip,
                        "bytes": est, "xfer": xfer})
    except Exception as e:
        return {"error": f"could not send the offer: {e}"}
    _code_peers(cat, {handle: tip})
    _code_projects(add=cat)
    _code_meta(f"code_path:{cat}", path)
    extras = {}
    try:
        extras["folder"] = tool_fed_share_folder(handle, cat)
    except Exception as e:
        extras["folder"] = {"error": str(e)}
    try:
        extras["project"] = tool_fed_share_project(cat, handle)
    except Exception as e:
        extras["project"] = {"error": str(e)}
    return {"ok": True, "project": cat, "branch": branch, "tip": tip,
            "offered_bytes": est, "xfer": xfer, "with": extras,
            "message": f"Offered '{cat}' to {handle}: {est // 1024} KB, "
                       f"branch {branch}. NOTHING has travelled yet — when "
                       f"they accept, the bundle is built and sent then."}


def tool_fed_code_pending():
    try:
        offers_raw = _code_fetch_type("code_offer")
    except Exception as e:
        return {"error": str(e)}
    offers, seen_off = [], set()
    for _id, sender, p in offers_raw:
        k = (sender, p.get("project", ""))
        if k in seen_off:
            continue
        seen_off.add(k)
        offers.append({"from": sender, "project": p.get("project", ""),
                       "branch": p.get("branch", ""),
                       "bytes": int(p.get("bytes") or 0),
                       "xfer": p.get("xfer", "")})
    try:
        rows = _code_fetch_type("code_invite")
    except Exception as e:
        return {"error": str(e)}
    xfers = _code_xfers(rows)
    partial = {}
    for _id, sender, p in rows:
        k = (sender, p.get("project", ""), p.get("xfer", ""))
        x = partial.setdefault(k, {"n": 0, "total": 0})
        x["n"] += 1
        x["total"] = max(x["total"], int(p.get("parts") or 0))
    awaiting = []
    _online = {}
    if any(_code_meta(f"code_await:{_p}") for _p in (_code_projects() or [])):
        try:
            for _c in (tool_fed_list_contacts().get("contacts") or []):
                _online[_c.get("handle")] = bool(_c.get("online"))
        except Exception:
            pass
    for _p in (_code_projects() or []):
        _who = _code_meta(f"code_await:{_p}")
        if _who:
            awaiting.append({"project": _p, "from": _who,
                             "path": _code_meta(f"code_path:{_p}") or "",
                             "peer_online": _online.get(_who)})
    return {"offers": offers, "awaiting": awaiting, "pending": [
        {"from": x["sender"], "project": x["project"],
         "branch": x["branch"], "complete": True}
        for x in xfers.values()],
        "arriving": [{"from": s, "project": pr, "xfer": xf,
                      "parts_here": v["n"], "parts_total": v["total"],
                      "stuck_hint": (
                          "the sender's transfer did not finish (their mailbox "
                          "quota, a cut connection). It can never be accepted "
                          "like this: ask them to share again, or free the "
                          "space with fed_code_discard."
                          if v["total"] and v["n"] < v["total"] else "")}
                     for (s, pr, xf), v in partial.items()
                     if not any(x["sender"] == s and x["project"] == pr
                                for x in xfers.values())]}


def tool_fed_code_unshare(path=None, project=None, peer=None):
    cat = (project or "").strip() or _code_name_for(
        os.path.abspath((path or ".").strip() or "."))
    try:
        peers = dict(_code_peers(cat))
    except Exception as e:
        return {"error": str(e)}
    if peer and peer not in peers:
        return {"error": f"'{peer}' is not a peer of '{cat}'. "
                         f"Peers: {', '.join(sorted(peers)) or '(none)'}"}
    if not peers:
        return {"ok": True, "project": cat, "removed": [],
                "note": "this project had no peers."}
    gone = [peer] if peer else sorted(peers)
    for h in gone:
        peers.pop(h, None)
    _code_meta(f"code_peers:{cat}", _json.dumps(peers))
    _code_journal(
        _code_meta(f"code_path:{cat}") or "",
        f"Co-work ended with {', '.join(gone)}: nothing more travels either "
        f"way. Their clone is still theirs — code cannot be un-given.")
    return {"ok": True, "project": cat, "removed": gone,
            "peers_left": sorted(peers),
            "note": "They stop receiving your commits and their packs for this "
                    "project are dropped here. Their existing clone remains "
                    "theirs: code cannot be un-given."}


def _partial_hint(handle, project):
    try:
        rows = _code_fetch_type("code_invite")
    except Exception:
        return "could not read the inbox"
    got, total = 0, 0
    for _id, sender, p in rows:
        if sender == handle and p.get("project") == project:
            got += 1
            total = max(total, int(p.get("parts") or 0))
    if not got:
        return "nothing from them for that project"
    return f"{got} of {total or '?'} sealed parts"


def tool_fed_code_discard(project=None, handle=None):
    try:
        rows = _code_fetch_type("code_invite") + _code_fetch_type("code_pack")
    except Exception as e:
        return {"error": str(e)}
    ids, hit = [], []
    seen = {}
    for env_id, sender, p in rows:
        key = (sender, p.get("project", ""), p.get("xfer", ""))
        seen.setdefault(key, []).append(env_id)
    for (sender, proj, _xfer), env_ids in seen.items():
        if project and proj != project:
            continue
        if handle and sender != handle:
            continue
        ids.extend(env_ids)
        hit.append({"from": sender, "project": proj, "parts": len(env_ids)})
    if not ids:
        return {"ok": True, "discarded": [],
                "note": "nothing matching in the inbox."}
    try:
        _code_fetch_type("code_invite", ack_ids=ids)
    except Exception as e:
        return {"error": f"could not consume them: {e}"}
    return {"ok": True, "discarded": hit,
            "note": "Mailbox quota freed. An incomplete bundle was unusable by "
                    "anyone, so nothing recoverable was lost — ask them to run "
                    "fed_share_code again."}


def tool_fed_code_retract(handle):
    handle = (handle or "").strip()
    if not handle:
        return {"error": "say WHOSE transfer to take back: the peer's handle."}
    try:
        _st, url, tok = _require_session()
    except Exception as e:
        return {"error": str(e)}
    freed, taken = 0, 0
    for etype in ("code_invite", "code_pack"):
        try:
            r = fed.server_withdraw(url, tok, resource_type=etype,
                                    recipient=handle)
            taken += int((r or {}).get("withdrawn") or 0)
            freed += int((r or {}).get("freed_bytes") or 0)
        except Exception as e:
            return {"error": f"could not take them back: {e}"}
    rearmed = []
    for cat in (_code_projects() or []):
        peers = _code_peers(cat)
        if handle in peers and peers[handle]:
            _code_peers(cat, {handle: ""})
            rearmed.append(cat)
    return {"ok": True, "peer": handle, "withdrawn": taken,
            "freed_bytes": freed, "rearmed": rearmed,
            "message": (
                f"Took back {taken} part(s) of mine that {handle} had not "
                f"collected ({_human_bytes(freed)} of your mail in flight "
                f"freed)." + (
                    f" The next push to them rebuilds "
                    f"{', '.join(rearmed)} whole instead of an increment on "
                    f"top of something that never landed."
                    if rearmed else "") + (
                    " Nothing recoverable was lost: an incomplete bundle "
                    "applies to nobody." if taken else
                    " There was nothing of yours waiting for them."))}


def tool_fed_code_peers(path=None, project=None):
    cat = (project or "").strip() or _code_name_for(
        os.path.abspath((path or ".").strip() or "."))
    try:
        peers = _code_peers(cat)
    except Exception as e:
        return {"error": str(e)}
    return {"project": cat,
            "peers": [{"handle": h, "last_tip": str(t or "")[:12]}
                      for h, t in sorted(peers.items())]}


def tool_fed_accept_code(handle, project, dest_path):
    import tempfile as _tmpf
    _raw = (dest_path or "").strip()
    dest = os.path.abspath(os.path.expanduser(_raw))
    if not _raw:
        return {"error": "give the path where YOU want the project. It is the "
                         "user's choice: ask them, do not invent one."}
    if os.path.exists(dest) and os.listdir(dest):
        return {"error": f"'{dest}' exists and is not empty."}
    try:
        _offers = _code_fetch_type("code_offer")
    except Exception as e:
        return {"error": str(e)}
    _mine = [(i, p) for i, s, p in _offers
             if s == handle and p.get("project") == project]
    if _mine:
        _oid, _op = _mine[0]
        _code_meta(f"code_path:{project}", dest)
        _code_meta(f"code_await:{project}", handle)
        try:
            _code_send_one(handle, "code_offer_ok",
                           {"project": project, "xfer": _op.get("xfer", ""),
                            "branch": _op.get("branch", "")})
        except Exception as e:
            return {"error": f"could not send the acceptance: {e}"}
        _code_fetch_type("code_offer", ack_ids=[i for i, _ in _mine])
        _code_meta(f"code_name_for:{dest}", project)
        _code_peers(project, {handle: _op.get("tip", "")})
        _code_projects(add=project)
        extras, rail_errs = {}, []
        for _label, _fn in (("folder",
                             lambda: tool_fed_accept_folder(
                                 handle, folder_name=project)),
                            ("project",
                             lambda: tool_fed_accept_project(project, handle))):
            try:
                extras[_label] = _fn()
            except Exception as e:
                extras[_label] = {"error": str(e)}
            if isinstance(extras[_label], dict) and extras[_label].get("error"):
                rail_errs.append(f"{_label}: {extras[_label]['error']}")
        _msg = (f"Accepted '{project}' from {handle} "
                f"(~{_human_bytes(int(_op.get('bytes') or 0))}). They build "
                f"and send the bundle NOW; it lands at {dest} within a couple "
                f"of minutes, or press «⇊ Bring in what arrived» on the Co-work page.")
        if rail_errs:
            _msg += ("  ⚠ The code is on its way, but its side rails did NOT "
                     "attach: " + "; ".join(rail_errs)
                     + ". The memory folder and the task board will not appear "
                       "until that is fixed.")
        return {"ok": True, "project": project, "from": handle,
                "path": dest, "awaiting": True, "with": extras,
                "rail_errors": rail_errs,
                "bytes": int(_op.get("bytes") or 0), "message": _msg}
    try:
        rows = _code_fetch_type("code_invite")
    except Exception as e:
        return {"error": str(e)}
    xfers = [x for x in _code_xfers(rows).values()
             if x["sender"] == handle and x["project"] == project]
    if not xfers:
        return {"error": f"no complete code invite from '{handle}' for "
                         f"'{project}'. What IS there: {_partial_hint(handle, project)}. "
                         f"An incomplete transfer cannot be applied by anyone: "
                         f"ask them to share it again, or clear it with "
                         f"fed_code_discard to free your mailbox quota."}
    x = xfers[0]
    fd, bfile = _tmpf.mkstemp(prefix="hoplita-code-", suffix=".bundle")
    os.close(fd)
    try:
        _code_join(x["parts"], bfile)
        os.makedirs(os.path.dirname(dest) or "/", exist_ok=True)
        ok, out = _clone_into_place(x["branch"], bfile, dest)
        if not ok:
            return {"error": f"git clone from bundle failed: {out[:300]}"}
        _run_git(["remote", "remove", "origin"], dest)
    finally:
        try:
            os.unlink(bfile)
        except OSError:
            pass
    _code_meta(f"code_name_for:{dest}", project)
    _code_meta(f"code_path:{project}", dest)
    _code_peers(project, {handle: x["tip"]})
    _code_projects(add=project)
    try:
        _code_fetch_type("code_invite", ack_ids=x["ids"])
    except Exception:
        pass
    extras = {}
    try:
        extras["folder"] = tool_fed_accept_folder(handle,
                                                  folder_name=project)
    except Exception as e:
        extras["folder"] = {"error": str(e)}
    try:
        extras["project"] = tool_fed_accept_project(project, handle)
    except Exception as e:
        extras["project"] = {"error": str(e)}
    _code_journal(dest, f"Joined {handle}'s project '{project}' "
                        f"(branch {x['branch']}, tip {x['tip'][:9]}) — "
                        f"materialized at {dest}.")
    note = ""
    if _code_cat(dest) != project:
        note = (f" Your directory is named '{_code_cat(dest)}'; the alias "
                f"keeps memory, journal and pushes keyed to '{project}' "
                f"anyway.")
    return {"ok": True, "project": project, "at": dest,
            "branch": x["branch"], "tip": x["tip"], "with": extras,
            "message": f"Project '{project}' plugged in at {dest}."
                       + note}


def tool_fed_code_push(path=None, project=None):
    if path:
        path = os.path.abspath(path.strip())
        cat = _code_name_for(path)
    else:
        cat = (project or "").strip()
        path = _code_meta(f"code_path:{cat}") or ""
    if not cat or not path or not os.path.isdir(path):
        return {"error": "no such code project here (share or accept one "
                         "first)."}
    if _is_local_only(cat):
        return _local_only_refusal(cat)
    peers = _code_peers(cat)
    if not peers:
        return {"skipped": "no code peers for this project."}
    with _code_push_lock:
        results, tip_now = {}, _code_tip(path)
        for handle, last in list(peers.items()):
            if last == tip_now:
                results[handle] = "up_to_date"
                continue
            bundle, branch, tip = _code_bundle(path, base=last or None)
            if bundle is None:
                results[handle] = branch
                continue
            try:
                parts, _x = _code_send(handle, "code_pack", cat, branch,
                                       last, tip, bundle)
                _code_peers(cat, {handle: tip})
                results[handle] = f"sent {parts} part(s)"
            except Exception as e:
                results[handle] = f"error: {e}"
            finally:
                try:
                    os.unlink(bundle)
                except OSError:
                    pass
    return {"ok": True, "project": cat, "tip": tip_now, "peers": results}


def _code_serve_offers():
    try:
        rows = _code_fetch_type("code_offer_ok")
    except Exception as e:
        print(f"[CODE] could not look at the mailbox for accepted offers: {e}",
              file=_sys.stderr)
        return []
    out = []
    for env_id, sender, p in rows:
        cat = p.get("project", "")
        path = _code_meta(f"code_path:{cat}") or ""
        if not cat or not path or not os.path.isdir(os.path.join(path, ".git")):
            continue
        bundle, branch, tip = _code_bundle(path)
        if bundle is None:
            out.append({"project": cat, "to": sender, "error": branch})
            _code_fetch_type("code_offer_ok", ack_ids=[env_id])
            continue
        try:
            parts, xfer = _code_send(sender, "code_pack", cat, branch, "",
                                     tip, bundle)
            out.append({"project": cat, "to": sender, "parts": parts,
                        "xfer": xfer,
                        "bytes": os.path.getsize(bundle)})
            _code_peers(cat, {sender: tip})
        except Exception as e:
            out.append({"project": cat, "to": sender, "error": str(e)})
            try:
                os.unlink(bundle)
            except OSError:
                pass
            continue
        _code_fetch_type("code_offer_ok", ack_ids=[env_id])
        try:
            os.unlink(bundle)
        except OSError:
            pass
    return out


_CODE_PULL_LOCK = _threading.Lock()


def _clone_into_place(branch, bundle, dest):
    import shutil as _sh
    import uuid as _uu
    parent = os.path.dirname(os.path.abspath(dest)) or "/"
    os.makedirs(parent, exist_ok=True)
    if os.path.isdir(dest) and os.listdir(dest):
        return False, (f"destination path '{dest}' already exists and is not "
                       f"an empty directory")
    tmp = os.path.join(parent, f".{os.path.basename(dest)}.arriving-{_uu.uuid4().hex[:8]}")
    ok, out = _run_git(["clone", "-b", branch, bundle, tmp], parent)
    if not ok:
        _sh.rmtree(tmp, ignore_errors=True)
        return ok, out
    try:
        if os.path.isdir(dest):
            os.rmdir(dest)
        os.rename(tmp, dest)
    except OSError as e:
        _sh.rmtree(tmp, ignore_errors=True)
        return False, f"could not move the clone into place: {e}"
    return True, out


def tool_fed_code_pull():
    with _CODE_PULL_LOCK:
        return _code_pull_once()


def _code_pull_once():
    served = _code_serve_offers()
    try:
        rows = _code_fetch_type("code_pack")
    except Exception as e:
        return {"error": str(e)}
    applied, left = [], 0
    for x in _code_xfers(rows).values():
        cat = x["project"]
        path = _code_meta(f"code_path:{cat}") or ""
        if path and _code_meta(f"code_await:{cat}") \
                and not os.path.isdir(os.path.join(path, ".git")):
            import tempfile as _tmpf
            _fd, _bf = _tmpf.mkstemp(prefix="hoplita-code-", suffix=".bundle")
            os.close(_fd)
            try:
                _code_join(x["parts"], _bf)
                os.makedirs(os.path.dirname(path) or "/", exist_ok=True)
                _ok, _out = _clone_into_place(x["branch"], _bf, path)
                if not _ok:
                    applied.append({"project": cat, "from": x["sender"],
                                    "error": f"clone failed: {_out[:200]}"})
                    continue
                _run_git(["remote", "remove", "origin"], path)
                _code_meta(f"code_await:{cat}", "")
                _code_projects(add=cat)
                _code_fetch_type("code_pack", ack_ids=x["ids"])
                _code_journal(path, f"Joined {x['sender']}'s project "
                                    f"'{cat}' (branch {x['branch']}, tip "
                                    f"{(x['tip'] or '')[:9]}) — materialized "
                                    f"at {path}.")
                applied.append({"project": cat, "from": x["sender"],
                                "path": path, "materialized": True})
            finally:
                try:
                    os.unlink(_bf)
                except OSError:
                    pass
            continue
        if not path or not os.path.isdir(path):
            left += 1
            continue
        _known = _code_peers(cat)
        if x["sender"] not in _known:
            applied.append({"project": cat, "from": x["sender"],
                            "dropped": "no longer a peer of this project"})
            try:
                _code_fetch_type("code_pack", ack_ids=x["ids"])
            except Exception:
                pass
            continue
        import tempfile as _tmpf
        fd, bfile = _tmpf.mkstemp(prefix="hoplita-code-", suffix=".bundle")
        os.close(fd)
        try:
            _code_join(x["parts"], bfile)
            ok, out = _run_git(["bundle", "verify", bfile], path)
            if not ok:
                applied.append({"project": cat, "from": x["sender"],
                                "error": "bundle does not apply here "
                                         "(missing history). Ask them for "
                                         "fed_share_code again.",
                                "detail": out[:200]})
                continue
            ref = f"refs/hoplita/incoming/{x['sender']}/{x['branch']}"
            ok, out = _run_git(["fetch", bfile,
                                f"+{x['branch']}:{ref}"], path)
            if not ok:
                applied.append({"project": cat, "from": x["sender"],
                                "error": f"fetch failed: {out[:200]}"})
                continue
            _ok, subjects = _run_git(
                ["log", "--format=%h %s", f"{ref}~..{ref}"
                 if not x["base"] else f"{x['base']}..{ref}"], path)
            _code_fetch_type("code_pack", ack_ids=x["ids"])
            head = subjects.splitlines()[:8]
            _code_journal(path, "Received from " + x["sender"] + " ("
                          + x["branch"] + ", quarantined in " + ref + "):\n"
                          + "\n".join(head))
            applied.append({"project": cat, "from": x["sender"],
                            "ref": ref, "commits": head})
            _activity_record(x["sender"], "commits", cat,
                             (head[0] if head else ""), n=len(head))
        finally:
            try:
                os.unlink(bfile)
            except OSError:
                pass
    return {"ok": True, "applied": applied, "waiting_elsewhere": left,
            "served": [r for r in served if r]}


def _code_resolve(path, project):
    if path:
        path = os.path.abspath(str(path).strip())
        cat = _code_name_for(path)
    else:
        cat = (project or "").strip()
        path = _code_meta(f"code_path:{cat}") or ""
    if not cat or not path or not os.path.isdir(os.path.join(path, ".git")):
        return None, None
    return cat, path


def tool_fed_code_incoming(path=None, project=None):
    cat, path = _code_resolve(path, project)
    if not cat:
        return {"error": "no such code project here (share or accept one "
                         "first)."}
    ok, out = _run_git(["for-each-ref", "--format=%(refname)",
                        "refs/hoplita/incoming/"], path)
    if not ok:
        return {"error": f"git for-each-ref failed: {out[:200]}"}
    _ok, head = _run_git(["rev-parse", "HEAD"], path)
    head = head.strip() if _ok else ""
    incoming = []
    for ref in [l.strip() for l in out.splitlines() if l.strip()]:
        rest = ref[len("refs/hoplita/incoming/"):]
        peer, _, branch = rest.partition("/")
        _ok, n = _run_git(["rev-list", "--count", f"HEAD..{ref}"], path)
        ahead = int(n.strip() or 0) if _ok and n.strip().isdigit() else 0
        if ahead <= 0:
            continue
        _ok, subjects = _run_git(["log", "--format=%h %s", f"HEAD..{ref}"], path)
        _ok2, stat = _run_git(["diff", "--stat", f"HEAD...{ref}"], path)
        ff_ok, _ = _run_git(["merge-base", "--is-ancestor", "HEAD", ref], path)
        _ok3, tip = _run_git(["rev-parse", ref], path)
        incoming.append({
            "peer": peer, "branch": branch, "ref": ref,
            "tip": tip.strip() if _ok3 else "",
            "commits": ahead,
            "subjects": (subjects.splitlines() if _ok else [])[:20],
            "files": [l.strip() for l in (stat.splitlines() if _ok2 else [])[:-1]][:30],
            "summary": (stat.splitlines()[-1].strip() if _ok2 and stat.strip() else ""),
            "fast_forward": bool(ff_ok),
        })
    incoming.sort(key=lambda x: (x["peer"], x["branch"]))
    return {"ok": True, "project": cat, "path": path, "head": head,
            "incoming": incoming}


def tool_fed_code_take(path=None, project=None, peer="", branch="main"):
    cat, path = _code_resolve(path, project)
    if not cat:
        return {"error": "no such code project here (share or accept one "
                         "first)."}
    peer = (peer or "").strip()
    branch = (branch or "main").strip()
    if not peer:
        return {"error": "peer is required: whose quarantined branch to take."}
    ref = f"refs/hoplita/incoming/{peer}/{branch}"
    ok, _ = _run_git(["rev-parse", "--verify", "--quiet", ref], path)
    if not ok:
        return {"error": f"nothing quarantined from {peer} on {branch} "
                         f"({ref} does not exist). fed_code_incoming lists "
                         "what there is."}
    _ok, dirty = _run_git(["status", "--porcelain"], path)
    if _ok and dirty.strip():
        return {"error": "the working tree has uncommitted changes: commit "
                         "or stash them first, a merge must not mix with them.",
                "dirty": [l.strip() for l in dirty.splitlines()][:20]}
    _ok, n = _run_git(["rev-list", "--count", f"HEAD..{ref}"], path)
    ahead = int(n.strip() or 0) if _ok and n.strip().isdigit() else 0
    if ahead <= 0:
        return {"ok": True, "project": cat, "peer": peer, "branch": branch,
                "merged": 0, "note": "already integrated: nothing ahead of HEAD."}
    ff_ok, _ = _run_git(["merge-base", "--is-ancestor", "HEAD", ref], path)
    _ok, before = _run_git(["rev-parse", "HEAD"], path)
    ok, out = _run_git(["merge", "--no-edit",
                        "--ff" if ff_ok else "--no-ff", ref], path)
    if not ok:
        _ok2, conflicts = _run_git(["diff", "--name-only", "--diff-filter=U"], path)
        _run_git(["merge", "--abort"], path)
        return {"error": "conflict", "project": cat, "peer": peer,
                "branch": branch, "ref": ref,
                "files": [l.strip() for l in (conflicts.splitlines() if _ok2 else []) if l.strip()],
                "message": f"The merge conflicts and was aborted: the tree is "
                           f"as it was. Resolve by hand: git merge {ref}",
                "detail": out[:300]}
    _ok, after = _run_git(["rev-parse", "HEAD"], path)
    _code_journal(path, f"Integrated {ahead} commit(s) from {peer} ({branch}, "
                        f"{'fast-forward' if ff_ok else 'merge commit'}): "
                        f"{before.strip()[:9]} → {after.strip()[:9]}.")
    _activity_record(peer, "integrated", cat, f"{ahead} commit(s)", n=ahead)
    if not ff_ok:
        code_autopush(path)
    return {"ok": True, "project": cat, "peer": peer, "branch": branch,
            "merged": ahead, "fast_forward": bool(ff_ok),
            "head": after.strip()}


def code_autopush(path):
    try:
        cat = _code_name_for(path)
        if not cat or not _code_peers(cat):
            return
        _threading.Thread(target=tool_fed_code_push, args=(path,),
                          daemon=True, name=f"code-push-{cat}").start()
    except Exception:
        pass


def _code_engine_tick():
    try:
        if _code_projects():
            r = tool_fed_code_pull()
            for a in (r.get("applied") or []):
                print(f"[FED-CODE] {a}", file=_sys.stderr)
    except Exception:
        pass


_get_todo = None


def set_todo_getter(getter):
    global _get_todo
    _get_todo = getter


def _todo():
    if _get_todo is None:
        return None
    return _get_todo()


def _store():
    if _get_store is None:
        raise Exception("Federation not initialized (fed_store not injected)")
    s = _get_store()
    if s is None:
        raise Exception("Federation not initialized (vault not unlocked)")
    return s


_get_sql = None
_get_reload = None


def set_sql_getter(getter):
    global _get_sql
    _get_sql = getter
    hoplita_children.set_sql_getter(getter)
    hoplita_children.set_pack_pusher(push_child_pack)


def set_reload_getter(getter):
    global _get_reload
    _get_reload = getter


def _sql():
    return _get_sql() if _get_sql is not None else None



def _signing_keys(st):
    if not st or not st.has_identity():
        return None, None
    try:
        return st.ensure_signing_keys()
    except Exception:
        return None, None


def _sign_contact(st, peer, peer_pub, peer_sign_pub="", trust=None):
    priv, _pub = _signing_keys(st)
    if not priv:
        return ""
    payload = {
        "typ": "contact", "me": st.data.get("handle"), "peer": peer,
        "peer_pub": peer_pub, "peer_sign_pub": peer_sign_pub or "",
        "ts": int(_time.time())}
    if trust is not None:
        payload["trust"] = trust
    return crypto.sign_attestation(priv, payload)


def _sign_grant(st, folder_id, peer, peer_pub):
    priv, _pub = _signing_keys(st)
    if not priv:
        return ""
    return crypto.sign_attestation(priv, {
        "typ": "share", "owner": st.data.get("handle"),
        "folder_id": folder_id, "peer": peer, "peer_pub": peer_pub,
        "ts": int(_time.time())})


def _sign_publish(st, folder_id):
    priv, _pub = _signing_keys(st)
    if not priv:
        return ""
    return crypto.sign_attestation(priv, {
        "typ": "publish", "owner": st.data.get("handle"),
        "folder_id": folder_id, "ts": int(_time.time())})


def _open_contact_att(st, handle, attestation):
    _priv, pub = _signing_keys(st)
    if not (pub and attestation):
        return None
    payload = crypto.verify_attestation(pub, attestation)
    if not isinstance(payload, dict):
        return None
    if payload.get("typ") != "contact" or payload.get("peer") != handle:
        return None
    if payload.get("me") != st.data.get("handle"):
        return None
    return payload


def _open_grant(owner_sign_pub, grant, folder_id, peer=None, owner=None):
    if not (owner_sign_pub and grant):
        return None
    payload = crypto.verify_attestation(owner_sign_pub, grant)
    if not isinstance(payload, dict):
        return None
    if payload.get("typ") != "share" or payload.get("folder_id") != folder_id:
        return None
    if peer is not None and payload.get("peer") != peer:
        return None
    if owner is not None and payload.get("owner") != owner:
        return None
    return payload


def _pin_contact(st, handle, pub, attestation=""):
    try:
        fp = crypto.fingerprint(pub)
    except Exception:
        fp = ""
    import datetime as _dt
    pins = st.data.setdefault("pinned_keys", {})
    entry = pins.get(handle) or {"first_seen": _dt.datetime.now().isoformat()}
    entry.update({"pub": pub, "fingerprint": fp})
    if attestation:
        entry["att"] = attestation
    pins[handle] = entry
    try:
        st.save_data()
    except Exception:
        pass


def _attest_contact_now(st, server_url, token, handle, pub, sign_pub_hint=""):
    att = _sign_contact(st, handle, pub, sign_pub_hint)
    if not att:
        return ""
    _pin_contact(st, handle, pub, att)
    try:
        fed.server_attest_contact(server_url, token, handle, att)
    except Exception:
        pass
    return att


def _peer_sign_pub(st, server_url, token, handle):
    entry = (st.data.get("pinned_keys") or {}).get(handle) or {}
    payload = _open_contact_att(st, handle, entry.get("att") or "")
    if payload and payload.get("peer_sign_pub"):
        return payload["peer_sign_pub"]
    try:
        kr = fed.server_get_key(server_url, token, handle)
        return kr.get("sign_pub") or ""
    except Exception:
        return ""


def _trusted_pub(server_url, token, handle):
    try:
        kr = fed.server_get_key(server_url, token, handle)
    except fed.FedServerError as e:
        _wait = getattr(e, "retry_after", None)
        if e.status == 429 and _wait and _wait <= _KEY_429_MAX_WAIT:
            _time.sleep(_wait + 0.5)
            try:
                kr = fed.server_get_key(server_url, token, handle)
            except fed.FedServerError as e2:
                return None, f"Could not obtain the key for '{handle}': {e2}"
        else:
            return None, f"Could not obtain the key for '{handle}': {e}"
    srv_pub = kr.get("public_key")
    srv_sign_pub = kr.get("sign_pub") or ""
    if not srv_pub:
        return None, f"'{handle}' has no public key on the server."
    try:
        st = _store()
    except Exception as e:
        return None, f"No vault to verify '{handle}' against: {e}"
    if not _signing_keys(st)[0]:
        return None, ("Could not load or create my signing key: refusing to "
                      "trust any key I cannot attest.")
    pins = st.data.setdefault("pinned_keys", {})
    pinned = pins.get(handle) or {}

    payload = _open_contact_att(st, handle, pinned.get("att") or "")
    if payload:
        if payload.get("peer_pub") == srv_pub:
            return srv_pub, None
        return None, (f"⚠ The public key for '{handle}' does NOT match my "
                      f"SIGNED attestation (attested "
                      f"fp={pinned.get('fingerprint')}). Possible MITM or "
                      f"compromised relay — BLOCKED: will not encrypt toward "
                      f"an unverified key. If they genuinely reset their "
                      f"identity, remove the contact and add them again.")

    if not pinned.get("pub"):
        try:
            rows = fed.server_list_contacts(server_url, token).get("contacts", [])
        except Exception:
            rows = []
        for c in rows:
            if c.get("handle") != handle:
                continue
            payload = _open_contact_att(st, handle, c.get("attestation") or "")
            if payload:
                if payload.get("peer_pub") != srv_pub:
                    return None, (f"⚠ The public key for '{handle}' does NOT "
                                  f"match my signed attestation from the "
                                  f"server. Possible MITM or compromised "
                                  f"relay — BLOCKED.")
                _pin_contact(st, handle, srv_pub, c["attestation"])
                return srv_pub, None
            break

    if pinned.get("pub") and pinned["pub"] != srv_pub:
        return None, (f"⚠ The public key for '{handle}' does NOT match the one "
                      f"pinned on first contact (pinned fp={pinned.get('fingerprint')}). "
                      f"Possible MITM or compromised relay — BLOCKED: will not "
                      f"encrypt toward an unverified key.")

    att = _attest_contact_now(st, server_url, token, handle, srv_pub,
                              srv_sign_pub)
    if not att:
        return None, (f"Could not SIGN an attestation for '{handle}': "
                      f"refusing to encrypt toward an unattested key.")
    return srv_pub, None


_TRUST_DEFAULT = {"level": "accept", "permissions": {}}


def _my_contact_att(st, handle, row=None):
    pinned = ((st.data.get("pinned_keys") or {}).get(handle) or {}).get("att") or ""
    best, best_att = None, ""
    for att in (pinned, (row or {}).get("attestation") or ""):
        payload = _open_contact_att(st, handle, att)
        if payload and (best is None or int(payload.get("ts") or 0) > int(best.get("ts") or 0)):
            best, best_att = payload, att
    return best, best_att


def _adopt_unsigned_trust(st, server_url, token, rows):
    if st.data.get("trust_signed"):
        return
    for c in rows:
        h = c.get("handle")
        if not h or c.get("status") != "accepted":
            continue
        payload, _att = _my_contact_att(st, h, c)
        if not payload or "trust" in payload:
            continue
        trust = {"level": c.get("trust_level") or "accept",
                 "permissions": c.get("permissions") or {}}
        att = _sign_contact(st, h, payload.get("peer_pub", ""),
                            payload.get("peer_sign_pub", ""), trust=trust)
        if not att:
            return
        _pin_contact(st, h, payload.get("peer_pub", ""), att)
        try:
            fed.server_attest_contact(server_url, token, h, att)
        except Exception:
            pass
    st.data["trust_signed"] = int(_time.time())
    try:
        st.save_data()
    except Exception:
        pass


def _signed_trust_of(st, server_url, token, rows):
    _adopt_unsigned_trust(st, server_url, token, rows)
    out = {}
    for c in rows:
        h = c.get("handle")
        if not h or c.get("status") != "accepted":
            continue
        payload, _att = _my_contact_att(st, h, c)
        if payload:
            out[h] = payload.get("trust") or dict(_TRUST_DEFAULT)
    return out


def _resign_trust(st, server_url, token, handle, trust):
    try:
        rows = fed.server_list_contacts(server_url, token).get("contacts", [])
    except fed.FedServerError:
        rows = []
    row = next((c for c in rows if c.get("handle") == handle), None)
    payload, _att = _my_contact_att(st, handle, row)
    if not payload:
        return ""
    att = _sign_contact(st, handle, payload.get("peer_pub", ""),
                        payload.get("peer_sign_pub", ""), trust=trust)
    if not att:
        return ""
    _pin_contact(st, handle, payload.get("peer_pub", ""), att)
    try:
        fed.server_attest_contact(server_url, token, handle, att)
    except Exception:
        pass
    return att


def inbound_contact_pub(server_url, token, handle):
    st = _store()
    if handle and handle == (st.data.get("owner") or ""):
        pub, err = _trusted_pub(server_url, token, handle)
        if not pub:
            raise RuntimeError(err)
        return pub
    try:
        srv_pub = fed.server_get_key(server_url, token, handle).get("public_key") or ""
        rows = fed.server_list_contacts(server_url, token).get("contacts", [])
    except fed.FedServerError as e:
        raise RuntimeError(f"Could not check '{handle}': {e}")
    row = next((c for c in rows if c.get("handle") == handle), None)
    payload, _att = _my_contact_att(st, handle, row)
    if not payload:
        raise RuntimeError(f"'{handle}' is not a contact I signed: the server "
                           f"listing them is not enough.")
    if not srv_pub or payload.get("peer_pub") != srv_pub:
        raise RuntimeError(f"⚠ The key the server gives for '{handle}' is not "
                           f"the one I signed: possible MITM or compromised "
                           f"relay — BLOCKED.")
    return srv_pub


def _seal(st, recipient_pub, envelope):
    return crypto.box_for(st.data.get("private_key_b64"), recipient_pub, envelope)


def _open_from(st, server_url, token, env, keys=None):
    sender = env.get("sender") or ""
    keys = {} if keys is None else keys
    if sender not in keys:
        if sender and sender == st.data.get("handle"):
            keys[sender] = st.data.get("public_key_b64")
        else:
            try:
                keys[sender] = inbound_contact_pub(server_url, token, sender)
            except Exception as e:
                print(f"[FED] envelope {env.get('id')} from {sender!r} not opened: {e}",
                      file=_sys.stderr)
                keys[sender] = None
    pub = keys.get(sender)
    if not pub:
        return sender, None
    try:
        dec = crypto.unbox(st.data.get("private_key_b64"), pub, env.get("ciphertext") or "")
    except Exception:
        print(f"[FED] envelope {env.get('id')} does not open with {sender!r}'s key: "
              f"not theirs", file=_sys.stderr)
        return sender, None
    rtype = env.get("resource_type")
    if rtype and dec.get("type") and dec.get("type") != rtype:
        return sender, None
    return sender, dec


_OPEN_KEYS = {}
_OPEN_KEYS_TTL = 30


def _unsealed(env):
    st, server_url, token = _require_session()
    now = _time.time()
    keys = {h: p for h, (t, p) in list(_OPEN_KEYS.items()) if now - t < _OPEN_KEYS_TTL}
    sender, dec = _open_from(st, server_url, token, env, keys)
    if sender not in _OPEN_KEYS or now - _OPEN_KEYS[sender][0] >= _OPEN_KEYS_TTL:
        _OPEN_KEYS[sender] = (now, keys.get(sender))
    if dec is None:
        raise ValueError(f"not shown to come from {sender!r}")
    return dec


def tool_fed_register(handle, email, server_url, invite=""):
    store = _store()
    if store.has_identity():
        return {"error": "You already have a federated identity. Use fed_status "
                         "to view it, or delete it manually to regenerate.",
                "handle": store.data.get("handle")}

    priv_b64, pub_b64 = crypto.generate_identity()
    sign_priv_b64, sign_pub_b64 = crypto.generate_signing_identity()
    fp = crypto.fingerprint(pub_b64)

    try:
        resp = fed.server_register(server_url, handle, email, pub_b64, fp,
                                   invite, sign_pub=sign_pub_b64)
    except fed.FedServerError as e:
        return {"error": f"Registration failed: {e}"}

    store.set_identity(priv_b64, pub_b64, handle, server_url)
    store.data["sign_priv_b64"] = sign_priv_b64
    store.data["sign_pub_b64"] = sign_pub_b64
    store.save_data()

    base = (f"Identity '{handle}' registered (no password: you authenticate "
            f"with your key).")
    result = {
        "ok": True,
        "handle": handle,
        "fingerprint": fp,
        "server_url": server_url,
        "email_sent": resp.get("email_sent"),
    }
    if resp.get("dev_pin"):
        result["dev_pin"] = resp["dev_pin"]
        result["message"] = (f"{base} PIN (dev server): {resp['dev_pin']}. "
                             f"Verify with fed_verify.")
    elif resp.get("email_sent") is False:
        result["message"] = (
            f"{base} BUT the server could NOT send the verification email, so "
            f"no PIN is on its way. You cannot log in until the account is "
            f"verified: ask the server admin for the PIN and pass it to "
            f"fed_verify. Do not register again — the handle is already yours.")
    else:
        result["message"] = (f"{base} Check the inbox of {email} for a 6-digit "
                             f"PIN (it expires in 15 minutes) and pass it to "
                             f"fed_verify.")
    return result


def tool_fed_verify(pin):
    store = _store()
    if not store.has_identity():
        return {"error": "No identity. Register first with fed_register."}
    handle = store.data["handle"]
    server_url = store.data["server_url"]
    if store.data.get("email_verified"):
        return {"ok": True, "handle": handle, "already_verified": True,
                "message": f"'{handle}' is ALREADY verified — the PIN was "
                           f"accepted earlier and is consumed. Do NOT call "
                           f"fed_verify again; run fed_login."}
    try:
        fed.server_verify(server_url, handle, pin)
    except fed.FedServerError as e:
        d = getattr(e, "data", None) or {}
        if d.get("error") == "PIN expired":
            return {"error": "Verification failed: the PIN expired.",
                    "resent": bool(d.get("resent")),
                    "next_step": ("A new PIN was sent to the registration "
                                  "email: run fed_verify with it."
                                  if d.get("resent") else
                                  "Wait a minute and run fed_verify with the "
                                  "same expired PIN again: that sends a new one.")}
        return {"error": f"Verification failed: {e}"}
    store.data["email_verified"] = True
    store.save_data()
    return {"ok": True, "handle": handle,
            "message": f"'{handle}' verified. You can now run fed_login."}


def tool_fed_login():
    store = _store()
    if not store.has_identity():
        return {"error": "No identity. Register first with fed_register."}
    handle = store.data["handle"]
    server_url = store.data["server_url"]
    priv_b64 = store.data["private_key_b64"]
    try:
        resp = fed.server_login(server_url, handle, priv_b64)
    except fed.FedServerError as e:
        low = str(e).lower()
        if "verif" in low:
            store.data["email_verified"] = False
            store.save_data()
            return {"error": f"Login failed: {e}",
                    "email_verified": False,
                    "next_step": "fed_verify(pin) with the 6-digit PIN emailed "
                                 "at registration. Until that runs, login "
                                 "cannot work — retrying it will not help."}
        if getattr(e, "status", None) == 404 or "not found" in low:
            return {"error": f"Login failed: {e}",
                    "next_step": "This server has no account for this handle "
                                 "any more (deleted server-side?). Retrying "
                                 "login will never work. To reset: fed_leave "
                                 "with force_local=true (wipes the local "
                                 "identity), then fed_register from zero."}
        return {"error": f"Login failed: {e}"}
    token = resp.get("token")
    if not token:
        return {"error": "The server did not return a token."}
    store.set_token(token)
    try:
        _priv_k, _pub_k = _signing_keys(store)
        if _pub_k:
            fed.server_publish_sign_key(server_url, token, _pub_k)
    except Exception:
        pass
    return {"ok": True, "handle": handle,
            "message": f"Session started as '{handle}' (key-based auth, no password)."}


def tool_fed_status():
    store = _store()
    if not store.has_identity():
        return {"registered": False,
                "message": "No federated identity. Use fed_register to create one."}
    d = store.data
    logged_in = bool(d.get("token"))
    verified = d.get("email_verified")
    online_check = None
    if logged_in:
        try:
            fed.server_heartbeat(d["server_url"], d["token"])
            online_check = "ok"
        except fed.FedServerError as e:
            if getattr(e, "status", None) == 429:
                online_check = "throttled"
            elif _try_relogin(store):
                try:
                    fed.server_heartbeat(d["server_url"], store.data["token"])
                    online_check = "ok"
                except fed.FedServerError:
                    online_check = "token_invalido"
            else:
                online_check = "token_invalido"
    logged_in = bool(store.data.get("token"))
    if logged_in and verified is not True:
        verified = True
        store.data["email_verified"] = True
        store.save_data()
    return {
        "registered": True,
        "handle": d.get("handle"),
        "email_verified": verified,
        "next_step": (None if verified is not False else
                      "fed_verify(pin) — the account exists but the email is "
                      "NOT verified, so login will keep failing"),
        "fingerprint": d.get("fingerprint"),
        "server_url": d.get("server_url"),
        "logged_in": logged_in,
        "session": online_check,
        "ws": _ws_state_block(),
        "public_key_b64": d.get("public_key_b64"),
    }


def _ws_state_block():
    try:
        s = fed.ws_state()
    except Exception:
        return {"up": False, "phase": "unknown"}
    if s.get("phase") == "too_old":
        s["consequence"] = ("the server no longer serves this version of "
                            "HOPLITA: nothing federated works until it is "
                            "updated (install.py --update)")
    elif not s.get("up") and s.get("phase") not in ("idle",):
        s["consequence"] = ("your contacts see YOU offline and pushes do not "
                            "arrive; everything else works over REST")
    return s



def _require_session():
    store = _store()
    if not store.has_identity():
        raise Exception("No identity. Register with fed_register first.")
    token = store.data.get("token")
    if not token:
        if _try_relogin(store):
            token = store.data.get("token")
        if not token:
            raise Exception("No session. Run fed_login first.")
    return store, store.data["server_url"], token


def _try_relogin(store):
    try:
        server_url = store.data.get("server_url")
        handle = store.data.get("handle")
        priv = store.data.get("private_key_b64")
        if not (server_url and handle and priv):
            return False
        resp = fed.server_login(server_url, handle, priv)
        tok = resp.get("token")
        if tok:
            store.set_token(tok)
            try:
                fed.ws_configure(server_url, tok)
            except Exception:
                pass
            return True
    except Exception:
        pass
    return False


def _with_relogin(fn):
    try:
        return fn()
    except fed.FedServerError as e:
        if "401" in str(e) or "Invalid token" in str(e):
            store = _store()
            if _try_relogin(store):
                return fn()
        raise


def tool_fed_add_contact(handle, trust_level="accept"):
    try:
        st, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    if trust_level not in ("open", "accept", "analyze"):
        trust_level = "accept"
    att = ""
    peer_pub = ""
    try:
        kr = fed.server_get_key(server_url, token, handle)
        peer_pub = kr.get("public_key") or ""
        if peer_pub:
            att = _sign_contact(st, handle, peer_pub,
                                kr.get("sign_pub") or "",
                                trust={"level": trust_level, "permissions": {}})
    except fed.FedServerError:
        pass
    try:
        fed.server_add_contact(server_url, token, handle, trust_level,
                               attestation=att)
    except fed.FedServerError as e:
        return {"error": f"Could not add contact: {e}"}
    if att:
        _pin_contact(st, handle, peer_pub, att)
    return {"ok": True, "handle": handle, "trust_level": trust_level,
            "message": f"Request sent to '{handle}'. They must accept it for "
                       f"the contact to be mutual."}


def tool_fed_accept_contact(handle, trust_level=None):
    try:
        st, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    if trust_level and trust_level not in ("open", "accept", "analyze"):
        trust_level = None
    att = ""
    peer_pub = ""
    try:
        kr = fed.server_get_key(server_url, token, handle)
        peer_pub = kr.get("public_key") or ""
        if peer_pub:
            att = _sign_contact(st, handle, peer_pub,
                                kr.get("sign_pub") or "",
                                trust={"level": trust_level or "accept",
                                       "permissions": {}})
    except fed.FedServerError:
        pass
    try:
        fed.server_accept_contact(server_url, token, handle, trust_level,
                                  attestation=att)
    except fed.FedServerError as e:
        return {"error": f"Could not accept: {e}"}
    if att:
        _pin_contact(st, handle, peer_pub, att)
    return {"ok": True, "handle": handle,
            "message": f"Contact with '{handle}' accepted."}


def tool_fed_list_contacts():
    locals_ = [hoplita_children.as_contact(p) for p in hoplita_children.load_all()]
    try:
        _, server_url, token = _require_session()
    except Exception as e:
        if locals_:
            return {"ok": True, "count": len(locals_), "contacts": locals_,
                    "offline_reason": str(e),
                    "message": (f"{len(locals_)} local child(ren). No federation "
                                f"session, so no remote contacts: {e}")}
        return {"error": str(e)}
    try:
        resp = _with_relogin(lambda: fed.server_list_contacts(server_url, _store().data["token"]))
    except fed.FedServerError as e:
        if locals_:
            return {"ok": True, "count": len(locals_), "contacts": locals_,
                    "offline_reason": str(e),
                    "message": (f"{len(locals_)} local child(ren). The server did "
                                f"not answer, so no remote contacts: {e}")}
        return {"error": f"Could not list contacts: {e}"}
    contacts = resp.get("contacts", [])
    try:
        _kids = {k.get("handle") for k in
                 fed.server_list_children(server_url, _store().data["token"]).get("children", [])
                 if isinstance(k, dict)}
    except Exception:
        _kids = set()
    for c in contacts:
        c["presence"] = "🟢 online" if c.get("online") else "⚫ offline"
        if c.get("handle") in _kids:
            c["kind"] = "child"
    _ws_note = ""
    try:
        _s = fed.ws_state()
        if not _s.get("up") and _s.get("phase") not in ("idle",):
            _ws_note = (f" ⚠ your OWN live socket is {_s.get('phase')}"
                        + (f" ({_s.get('error')})" if _s.get("error") else "")
                        + " — your contacts see YOU offline until it is back.")
    except Exception:
        pass
    contacts = locals_ + contacts
    n_local = len(locals_)
    return {
        "ok": True,
        "count": len(contacts),
        "contacts": contacts,
        "ws": _ws_state_block(),
        "local_children": n_local,
        "message": ((f"{len(contacts)} contact(s)"
                     + (f", {n_local} of them local children." if n_local else "."))
                    if contacts
                    else "You have no contacts yet. Use fed_add_contact.") + _ws_note,
    }


def _child_kit(model="", base_url="", model_id="", api_key="", search=False):
    import hoplita_llmcfg as llm
    import hoplita_paths as _hp
    cfg, _problem = llm.read_config(_hp.config_path(), restore=False)
    if base_url or model_id:
        if not (base_url and model_id):
            return None, ("A model of its own needs both base_url and "
                          "model_id (api_key if the endpoint asks for one).")
        api = {"base_url": base_url.rstrip("/"), "api_key": api_key or "",
               "model": model_id}
        extra, vision = "", ""
    else:
        if model and model.lower() != "base":
            sec = llm.model_section(cfg, model)
            if not sec:
                return None, (f"No pool model '{model}'. Leave it empty for "
                              "the base model, or give base_url and model_id.")
        else:
            sec = "API"
        url, key, mid = llm.resolve_endpoint(cfg, sec)
        api = {"base_url": url, "api_key": key, "model": mid}
        if sec == "API":
            sec = llm.model_section(cfg, llm.base_alias(cfg)) or "API"
        extra = (cfg.get(sec, "extra_body", fallback="") or "").strip()
        vision = (cfg.get(sec, "vision", fallback="") or "").strip()
    if not api["model"]:
        return None, "This machine has no model configured to hand over."
    if extra:
        api["extra_body"] = extra
    if vision:
        api["vision"] = vision
    kit = {"v": 1, "api": api,
           "language": cfg.get("APP", "language", fallback="en") or "en"}
    if search:
        prov = (cfg.get("SEARCH", "provider", fallback="") or "").strip().lower()
        sec = prov.upper()
        if not (prov and cfg.has_section(sec)
                and (cfg.get(sec, "api_key", fallback="") or "").strip()):
            return None, ("This machine has no web search with a key to hand "
                          "over ([SEARCH] provider and its api_key).")
        kit["search"] = {"provider": prov,
                         "settings": {k: cfg.get(sec, k) for k in cfg.options(sec)}}
    return kit, ""


def _child_handle(label):
    import re
    me = (_store().data.get("handle") or "").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", (label or "child").lower()).strip("-")
    return f"{me}-{slug or 'child'}" if me else (slug or "child")


INSTALL_SH = "https://raw.githubusercontent.com/HoplitaAI/Hoplita/main/install.sh"
INSTALL_PS1 = "https://raw.githubusercontent.com/HoplitaAI/Hoplita/main/install.ps1"


def host_command(handle, server_url, token):
    return (f"curl -fsSL {INSTALL_SH} | "
            f"HOPLITA_SPAWN_SERVER={server_url} HOPLITA_SPAWN_HANDLE={handle} "
            f"HOPLITA_SPAWN_TOKEN={token} sh -s -- --child")


def host_command_windows(handle, server_url, token):
    return (f"$env:HOPLITA_SPAWN_SERVER='{server_url}'; "
            f"$env:HOPLITA_SPAWN_HANDLE='{handle}'; $env:HOPLITA_SPAWN_TOKEN='{token}'; "
            f"& ([scriptblock]::Create((irm {INSTALL_PS1}))) --child")


def tool_fed_spawn_child(label="", handle="", model="", base_url="",
                         model_id="", api_key="", search=False,
                         launch=False):
    try:
        store, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    kit, kerr = _child_kit(model, base_url, model_id, api_key, search)
    if kerr:
        return {"error": kerr}
    has_kit = "spawn_kit" in (fed.server_protocol(server_url).get("caps") or [])
    if launch and not has_kit:
        return {"error": "This federation server is too old to hand a child its "
                         "starter kit (no spawn_kit): it must be updated before "
                         "a container child can boot by itself."}
    handle = (handle or "").strip().lower() or _child_handle(label)
    try:
        resp = fed.server_create_spawn_token(server_url, token, label=label)
    except fed.FedServerError as e:
        return {"error": f"Could not create the enrollment token: {e}"}
    raw = resp.get("token")
    out = {
        "ok": True,
        "spawn_token": raw,
        "label": resp.get("label", label),
        "expires_at": resp.get("expires_at"),
        "server_url": server_url,
        "message": ("Enrollment token created (valid 60 min, single use). "
                    "Launch it on the target server with:\n"
                    f"  hoplita-cli --spawn --server {server_url} "
                    f"--token {raw}"),
    }
    if not has_kit:
        return out
    try:
        fed.server_set_spawn_kit(server_url, token, resp.get("spawn_id"),
                                 crypto.seal_kit(raw, kit))
    except fed.FedServerError as e:
        return {"error": f"The token was created but its starter kit could not "
                         f"be attached: {e}"}
    import hoplita_child_image as img
    rt = img.runtime() or "docker"
    cmd = " ".join(img.run_args(rt, handle, server_url, raw))
    host = host_command(handle, server_url, raw)
    host_win = host_command_windows(handle, server_url, raw)
    out.update({
        "handle": handle,
        "kit": {"model": kit["api"]["model"],
                "search": (kit.get("search") or {}).get("provider", "")},
        "container_command": cmd,
        "build_command": f"python3 {img.__file__} build",
        "host_command": host,
        "host_command_windows": host_win,
        "message": (f"Child '{handle}' ready to boot (token valid 60 min, single "
                    f"use). It will think with {kit['api']['model']}"
                    + (f" and search with {kit['search']['provider']}"
                       if kit.get("search") else "")
                    + ". Every key in its kit is one the child holds and can "
                      "spend. Start it in a container with:\n  " + cmd
                    + f"\n(build the {img.IMAGE} image first if this machine "
                      f"lacks it: python3 {img.__file__} build)"
                    + "\n\nOr on any machine (a server, a VPS), with nothing "
                      "installed first; it stays running as a service:\n  " + host
                    + "\n\nOn Windows, in PowerShell:\n  " + host_win),
    })
    if launch:
        r = img.launch(handle, server_url, raw)
        if r.get("error"):
            out["launch_error"] = r["error"]
            out["message"] += "\n\nLaunching it here failed: " + r["error"]
        else:
            out["launched"] = r
            out["message"] = (f"Child '{handle}' is starting in a {r['runtime']} "
                              f"container"
                              + (f" (the {img.IMAGE} image was built first)"
                                 if r.get("built") else "")
                              + f". It thinks with {kit['api']['model']}"
                              + (f" and searches with {kit['search']['provider']}"
                                 if kit.get("search") else "")
                              + "; it appears in your contacts once it enrolls.")
    return out


def _apply_child_kit(kit):
    import hoplita_llmcfg as llm
    import hoplita_paths as _hp
    path = _hp.config_path()
    if os.path.exists(path):
        text = open(path, encoding="utf-8").read()
    else:
        ex = os.path.join(_hp.hoplita_base(), "config.ini.example")
        text = open(ex, encoding="utf-8").read() if os.path.exists(ex) else ""
    done = []
    for k, v in (kit.get("api") or {}).items():
        text = llm.set_value(text, "API", k, v)
        done.append(f"API.{k}")
    if kit.get("language"):
        text = llm.set_value(text, "APP", "language", kit["language"])
        done.append("APP.language")
    srch = kit.get("search") or {}
    if srch.get("provider"):
        text = llm.set_value(text, "SEARCH", "provider", srch["provider"])
        sec = srch["provider"].upper()
        for k, v in (srch.get("settings") or {}).items():
            text = llm.set_value(text, sec, k, v)
        done.append(f"SEARCH={srch['provider']}")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return done


def tool_fed_list_children():
    try:
        _, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    try:
        resp = fed.server_list_children(server_url, token)
    except fed.FedServerError as e:
        return {"error": f"Could not list children: {e}"}
    children = resp.get("children", [])
    for ch in children:
        ch["presence"] = "🟢 online" if ch.get("online") else "⚫ offline"
    return {
        "ok": True,
        "count": len(children),
        "children": children,
        "message": (f"{len(children)} child(ren)." if children
                    else "You have no children yet. Use fed_spawn_child to create "
                         "an enrollment token."),
    }


def tool_fed_enroll_child(server_url, spawn_token, handle):
    store = _store()
    if store.has_identity():
        return {"error": "This instance already has a federated identity. A "
                         "child enrolls into a clean vault.",
                "handle": store.data.get("handle")}

    handle = (handle or "").strip().lower()
    if not (server_url and spawn_token and handle):
        return {"error": "Missing data (server_url, spawn_token, handle)."}

    priv_b64, pub_b64 = crypto.generate_identity()
    fp = crypto.fingerprint(pub_b64)

    try:
        resp = fed.server_redeem_spawn_token(server_url, spawn_token, handle,
                                             pub_b64, fp)
    except fed.FedServerError as e:
        return {"error": f"Enrollment failed: {e}"}

    session_token = resp.get("token")
    if not session_token:
        return {"error": "The server did not return a session token."}

    store.set_identity(priv_b64, pub_b64, handle, server_url)
    store.set_token(session_token)
    _owner = (resp.get("owner") or "").strip()
    if _owner:
        try:
            store.data["owner"] = _owner
            store.save_data()
        except Exception as e:
            print(f"[FED] could not record the owner: {e}", file=_sys.stderr)

    out = {
        "ok": True,
        "handle": handle,
        "owner": resp.get("owner"),
        "fingerprint": fp,
        "server_url": server_url,
        "message": f"Child '{handle}' enrolled under '{resp.get('owner')}'. "
                   f"Identity and session saved in the vault.",
    }
    if resp.get("kit"):
        try:
            out["kit_applied"] = _apply_child_kit(
                crypto.open_kit(spawn_token, resp["kit"]))
            out["message"] += " Starter kit written to config.ini."
        except Exception as e:
            out["kit_error"] = f"The starter kit could not be applied: {e}"
            out["message"] += " " + out["kit_error"]
    return out



def tool_fed_share(handle, resource_type, payload):
    try:
        store, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}

    recipient_pub, kerr = _trusted_pub(server_url, token, handle)
    if kerr:
        return {"error": kerr}

    my_pub = store.data.get("public_key_b64")
    envelope = crypto.make_envelope(resource_type, payload, sender_pub_b64=my_pub)
    try:
        blob = _seal(_store(), recipient_pub, envelope)
    except Exception as e:
        return {"error": f"Encryption failed: {e}"}

    meta = {"resource_type": resource_type}
    try:
        resp = fed.server_send_envelope(server_url, token, handle, resource_type,
                                        blob, meta)
    except fed.FedServerError as e:
        return {"error": f"Could not send: {e}"}
    return {
        "ok": True,
        "recipient": handle,
        "resource_type": resource_type,
        "envelope_id": resp.get("envelope_id"),
        "message": f"'{resource_type}' shared with '{handle}' (E2E encrypted; "
                   f"the server cannot read it).",
    }


def tool_fed_inbox(resource_type=None):
    try:
        store, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}

    my_priv = store.data.get("private_key_b64")
    if not my_priv:
        return {"error": "No private key in the vault."}

    try:
        if resource_type:
            resp = fed.server_fetch_inbox_types(server_url, token,
                                                [resource_type], peek=True)
        else:
            resp = fed.server_fetch_inbox_types(server_url, token, ["note"],
                                                peek=True)
    except fed.FedServerError as e:
        return {"error": f"Could not fetch the inbox: {e}"}

    envelopes = resp.get("envelopes", [])
    items = []
    errors = []
    for env in envelopes:
        rtype = env.get("resource_type")
        if resource_type and rtype != resource_type:
            continue
        try:
            decrypted = _unsealed(env)
            items.append({
                "from": env.get("sender"),
                "resource_type": rtype,
                "payload": decrypted.get("payload"),
                "envelope_id": env.get("id"),
            })
        except Exception as e:
            errors.append({"envelope_id": env.get("id"), "error": str(e)})

    result = {
        "ok": True,
        "count": len(items),
        "items": items,
        "message": (f"{len(items)} resource(s) received." if items
                    else "Nothing new in your inbox."),
    }
    if errors:
        result["decrypt_errors"] = errors
    return result


def _consume_envelope(envelope_id):
    if not envelope_id:
        return False
    try:
        _store_, server_url, token = _require_session()
        fed.server_ack_envelopes(server_url, token, [envelope_id])
        return True
    except Exception:
        return False



def tool_fed_set_permissions(handle, resource_type, perms):
    try:
        st, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    if not isinstance(perms, dict):
        return {"error": "perms must be an object (e.g. {'can_add': true, "
                         "'visibility': 'busy'})."}
    try:
        rows = fed.server_list_contacts(server_url, token).get("contacts", [])
    except fed.FedServerError as e:
        return {"error": f"Could not set permissions: {e}"}
    trust = _signed_trust_of(st, server_url, token, rows).get(handle)
    if trust is None:
        return {"error": f"'{handle}' is not an accepted contact I signed: "
                         f"there is nothing to set permissions on."}
    trust = _copy.deepcopy(trust)
    merged = dict((trust.setdefault("permissions", {})).get(resource_type) or {})
    merged.update(perms)
    trust["permissions"][resource_type] = merged
    if not _resign_trust(st, server_url, token, handle, trust):
        return {"error": f"Could not SIGN the new permissions for '{handle}' "
                         f"(vault unavailable?): nothing was changed."}
    try:
        fed.server_set_permissions(server_url, token, handle, resource_type, perms)
    except fed.FedServerError:
        pass
    return {
        "ok": True,
        "handle": handle,
        "resource_type": resource_type,
        "permissions": trust["permissions"],
        "message": f"Permissions for '{resource_type}' on '{handle}' updated.",
    }



def tool_fed_share_note(handle, category, title):
    if _is_local_only(category):
        return _local_only_refusal(category)
    notes = _notes()
    if notes is None:
        return {"error": "Notes not available in this session."}
    try:
        _, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}

    found = None
    for n in notes.get_notes(category):
        if n.get("title", "").strip().lower() == title.strip().lower():
            found = n
            break
    if not found:
        return {"error": f"Could not find the note '{title}' in category "
                         f"'{category}'. Check the title and category."}

    payload = {
        "category": category,
        "title": found.get("title"),
        "content": found.get("content"),
        "original_created_at": found.get("created_at"),
    }
    return tool_fed_share(handle, "note", payload)


def tool_fed_accept_note(envelope_index=0):
    notes = _notes()
    if notes is None:
        return {"error": "Notes not available in this session."}

    inbox = tool_fed_inbox("note")
    if inbox.get("error"):
        return inbox
    items = inbox.get("items", [])
    if not items:
        return {"error": "You have no shared notes in the inbox."}
    if envelope_index >= len(items):
        return {"error": f"There are only {len(items)} note(s) in the inbox."}

    item = items[envelope_index]
    payload = item.get("payload", {})
    sender = item.get("from", "unknown")
    category = payload.get("category") or "compartidas"
    title = payload.get("title", "(untitled)")
    content = payload.get("content", "")

    note = notes.add_note(category, f"{title}  ·  from {sender}", content)
    if note is None:
        return {"error": "Could not save the note."}
    _consume_envelope(item.get("envelope_id"))
    return {
        "ok": True,
        "from": sender,
        "category": category,
        "title": title,
        "message": f"Note '{title}' from {sender} saved in your category "
                   f"'{category}'.",
    }


SHARED_SKILLS_CATEGORY = "shared-skills"
SKILLS_MARK_TYPE = "folder.skills"
CHILD_PACK_TYPE = "child.pack"
_MY_PACK_KEY = "my_pack"


def tool_fed_share_appointment(handle, title, date, description="", urgency="Normal"):
    try:
        store, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    my_pub = store.data.get("public_key_b64")
    peer_pub, kerr = _trusted_pub(server_url, _store().data["token"], handle)
    if kerr:
        return {"error": kerr}
    payload = {
        "title": title,
        "date": date,
        "description": description,
        "urgency": urgency,
    }
    env = crypto.make_envelope("calendar_event", payload, sender_pub_b64=my_pub)
    try:
        blob = _seal(_store(), peer_pub, env)
        _with_relogin(lambda: fed.server_send_envelope(
            server_url, _store().data["token"], handle, "calendar_event", blob, {}))
    except Exception as e:
        return {"error": f"Could not send the appointment: {e}"}
    return {
        "ok": True,
        "shared_with": handle,
        "title": title,
        "date": date,
        "message": f"Appointment '{title}' ({date}) sent to '{handle}'. They will "
                   f"see it in their incoming appointments and decide whether to "
                   f"add it to their calendar.",
    }


def tool_fed_list_incoming_appointments():
    try:
        store, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    my_priv = store.data.get("private_key_b64")
    try:
        resp = _with_relogin(lambda: fed.server_fetch_inbox_types(
            server_url, _store().data["token"], ["calendar_event"], peek=True))
    except fed.FedServerError as e:
        return {"error": f"Could not query the inbox: {e}"}
    incoming = []
    for env in resp.get("envelopes", []):
        if env.get("resource_type") != "calendar_event":
            continue
        try:
            dec = _unsealed(env)
            p = dec.get("payload", {})
            incoming.append({
                "from": env.get("sender", "?"),
                "title": p.get("title", "(untitled)"),
                "date": p.get("date", ""),
                "description": p.get("description", ""),
                "urgency": p.get("urgency", "Normal"),
                "envelope_id": env.get("id"),
            })
        except Exception:
            continue
    return {
        "ok": True,
        "count": len(incoming),
        "appointments": incoming,
        "message": (f"{len(incoming)} incoming appointment(s) to accept."
                    if incoming else "No incoming appointments."),
    }


def tool_fed_list_appointment_bounces():
    try:
        store, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    my_priv = store.data.get("private_key_b64")
    try:
        resp = fed.server_fetch_inbox_types(server_url, token, ["cita_denied"], peek=True)
    except fed.FedServerError as e:
        return {"error": str(e)}
    out, acked = [], []
    for env in resp.get("envelopes", []):
        if env.get("resource_type") != "cita_denied":
            continue
        try:
            p = _unsealed(env).get("payload", {})
        except Exception:
            acked.append(env.get("id")); continue
        out.append({"from": env.get("sender", "?"),
                    "reason": p.get("reason", "you don't have permission")})
        acked.append(env.get("id"))
    if acked:
        try:
            fed.server_ack_envelopes(server_url, token, acked)
        except fed.FedServerError:
            pass
    return {"ok": True, "count": len(out), "bounces": out}


def _auto_accept_appointments():
    todo = _todo()
    if todo is None:
        return
    try:
        store, server_url, token = _require_session()
    except Exception:
        return
    my_priv = store.data.get("private_key_b64")
    my_pub = store.data.get("public_key_b64")
    try:
        resp = fed.server_fetch_inbox_types(server_url, token, ["calendar_event"], peek=True)
    except fed.FedServerError:
        return
    envs = resp.get("envelopes", [])
    if not envs:
        return
    try:
        contacts = fed.server_list_contacts(server_url, token).get("contacts", [])
    except fed.FedServerError:
        return
    signed = _signed_trust_of(store, server_url, token, contacts)
    contact_handles = set(signed)
    appt_level = {h: (((t.get("permissions") or {}).get("citas") or {}).get("level") or "no")
                  for h, t in signed.items()}
    existing = {(a.get("date"), a.get("title", "").strip().lower()) for a in todo.get_appointments()}
    tombs = (todo.get_deleted_appointments() if hasattr(todo, "get_deleted_appointments") else {})
    acked, added, denied = [], 0, 0
    for env in envs:
        sender = env.get("sender")
        if sender not in contact_handles:
            acked.append(env.get("id")); continue
        level = appt_level.get(sender, "no")
        if level == "review":
            continue
        if level == "no":
            try:
                spub, _kerr = _trusted_pub(server_url, token, sender)
                if spub:
                    b = _seal(_store(), spub, crypto.make_envelope(
                        "cita_denied", {"reason": "you don't have permission"}, sender_pub_b64=my_pub))
                    fed.server_send_envelope(server_url, token, sender, "cita_denied", b, {})
            except Exception:
                pass
            acked.append(env.get("id")); denied += 1; continue
        try:
            p = _unsealed(env).get("payload", {})
            key = (p.get("date", ""), p.get("title", "").strip().lower())
            if f"{key[0]}|{key[1]}" in tombs:
                acked.append(env.get("id")); continue
            if key in existing:
                acked.append(env.get("id")); continue
            todo.add_appointment(p.get("date", ""), p.get("title", "(untitled)"),
                                 p.get("description", ""), rag_urls="",
                                 urgency=p.get("urgency", "Normal"))
            existing.add(key); added += 1; acked.append(env.get("id"))
        except Exception:
            continue
    if acked:
        try:
            fed.server_ack_envelopes(server_url, token, acked)
        except fed.FedServerError:
            pass
    if added or denied:
        print(f"[FED-SYNC] appointments: {added} auto-added, {denied} rejected.")

def tool_fed_accept_appointment(title=None, from_handle=None):
    todo = _todo()
    if todo is None:
        return {"error": "Calendar not available in this session."}
    listing = tool_fed_list_incoming_appointments()
    if listing.get("error"):
        return listing
    apts = listing.get("appointments", [])
    if title:
        apts = [a for a in apts if a["title"].strip().lower() == title.strip().lower()]
    if from_handle:
        apts = [a for a in apts if a["from"] == from_handle]
    if not apts:
        return {"error": "Could not find that incoming appointment."}
    if len(apts) > 1:
        titles = ", ".join(f"'{a['title']}' (from {a['from']})" for a in apts)
        return {"error": f"There are several incoming appointments: {titles}. Specify title and/or from_handle."}
    apt = apts[0]
    dup = any(a.get("date") == apt["date"]
              and a.get("title", "").strip().lower() == apt["title"].strip().lower()
              for a in todo.get_appointments())
    if not dup:
        todo.add_appointment(apt["date"], apt["title"], apt["description"],
                             rag_urls="", urgency=apt.get("urgency", "Normal"))
    try:
        key = f"{apt['date']}|{apt['title'].strip().lower()}"
        if hasattr(todo, "data") and key in todo.data.get("deleted_appointments", {}):
            del todo.data["deleted_appointments"][key]
            if hasattr(todo, "save_data"):
                todo.save_data()
    except Exception:
        pass
    try:
        if apt.get("envelope_id"):
            _with_relogin(lambda: fed.server_ack_envelopes(
                _store().data["server_url"], _store().data["token"],
                [apt["envelope_id"]]))
    except Exception:
        pass
    return {
        "ok": True,
        "duplicate_skipped": dup,
        "title": apt["title"],
        "date": apt["date"],
        "from": apt["from"],
        "message": f"Appointment '{apt['title']}' ({apt['date']}) from '{apt['from']}' "
                   f"added to your calendar.",
    }


def tool_fed_share_skill(handle, title, content):
    payload = {"title": title, "content": content, "kind": "skill"}
    return tool_fed_share(handle, "skill", payload)


def tool_fed_list_shared_skills():
    inbox = tool_fed_inbox("skill")
    if inbox.get("error"):
        return inbox
    items = inbox.get("items", [])
    pend = []
    for i, it in enumerate(items):
        p = it.get("payload", {}) or {}
        pend.append({
            "index": i,
            "from": it.get("from", "unknown"),
            "title": p.get("title", "(untitled)"),
            "preview": (p.get("content", "") or "")[:200],
        })
    return {
        "ok": True,
        "count": len(pend),
        "pending": pend,
        "message": (f"{len(pend)} shared skill(s) pending acceptance."
                    if pend else "No shared skills pending."),
    }


def tool_fed_accept_skill(envelope_index=0):
    notes = _notes()
    if notes is None:
        return {"error": "Notes not available in this session."}

    inbox = tool_fed_inbox("skill")
    if inbox.get("error"):
        return inbox
    items = inbox.get("items", [])
    if not items:
        return {"error": "You have no shared skills in the inbox."}
    if envelope_index >= len(items):
        return {"error": f"There are only {len(items)} skill(s) in the inbox."}

    item = items[envelope_index]
    payload = item.get("payload", {}) or {}
    sender = item.get("from", "unknown")
    title = payload.get("title", "(untitled)")
    content = payload.get("content", "")

    if SHARED_SKILLS_CATEGORY not in notes.get_categories():
        notes.add_category(SHARED_SKILLS_CATEGORY)
    note = notes.add_note(SHARED_SKILLS_CATEGORY,
                          f"{title}  ·  @{sender}", content)
    if note is None:
        return {"error": "Could not save the skill."}
    _consume_envelope(item.get("envelope_id"))
    return {
        "ok": True,
        "from": sender,
        "category": SHARED_SKILLS_CATEGORY,
        "title": title,
        "message": f"Skill '{title}' from @{sender} accepted into your folder "
                   f"'{SHARED_SKILLS_CATEGORY}'. It is now searchable by your agent.",
    }


def tool_device_info(args=None):
    import hoplita_device as dev
    d = dev.device()
    return {"id": d["id"], "name": d["name"],
            "hint": "Tie a tab to this id and its turns run HERE, whichever "
                    "machine the user is typing from. For anything that only "
                    "exists on this computer: a mounted disk, a running "
                    "service, a folder, a network only this box reaches, a "
                    "project and its git."}


def tool_device_rename(args):
    import hoplita_device as dev
    return dev.rename((args or {}).get("name", ""))


def tool_device_regenerate(args=None):
    import hoplita_device as dev
    return dev.regenerate()


def tool_fed_ask_agent(handle, message, max_iterations=None, device="", tab="",
                       terminal=True, event_sink=None):
    import fed_session
    pack = hoplita_children.find(handle)
    if pack is not None and pack.get("node"):
        handle, pack = pack["node"], None
    if pack is not None:
        reply = hoplita_children.run(pack, message, terminal, event_sink)
        _activity_record(pack["name"], "child_task", "",
                         title=(message or "")[:80])
        return {"ok": True, "from": pack["name"], "local": True, "reply": reply,
                "message": f"'{pack['name']}' (local child) answered."}
    try:
        _require_session()
    except Exception as e:
        return {"error": str(e)}

    collected = {"events": [], "reply": None, "terminal": None}
    try:
        _emit = TURN_EMITTER() if TURN_EMITTER else None
    except Exception:
        _emit = None

    def on_event(ev):
        collected["events"].append(ev)
        et = ev.get("event")
        if et == "session_ack" and _emit:
            try:
                _emit({"event": "tool_progress", "args": {"peer": handle},
                       "key": ("fed.session_awaiting_approval"
                               if ev.get("state") == "awaiting_approval"
                               else "fed.session_accepted")})
            except Exception:
                pass
        if et == "reply":
            collected["reply"] = ev.get("reply", "")
        if et in fed_session.TERMINAL_EVENTS:
            collected["terminal"] = et

    try:
        orig = fed_session.SessionOriginator(handle, on_event, device=device,
                                             tab=tab)
        terminal = orig.start(message, max_iterations,
                              should_stop=_turn_stop_probe())
    except Exception as e:
        return {"error": f"Could not open the session with '{handle}': {e}"}

    et = terminal.get("event")
    if et == "reply":
        return {"ok": True, "from": handle, "reply": terminal.get("reply", ""),
                "iterations": terminal.get("iterations"),
                "message": f"Reply from '{handle}' received."}
    if et == "cancelled":
        return {"ok": True, "from": handle, "cancelled": True,
                "message": "The session was cancelled."}
    if et == "iteration_limit":
        return {"ok": True, "from": handle,
                "message": f"'{handle}' ran out of iterations without concluding."}
    if terminal.get("code") == "no_answer":
        return {"error": f"'{handle}': {terminal.get('error', 'no answer')}",
                "no_answer": True, "retryable": False,
                "message": f"Nobody at '{handle}' answered the request in time: "
                           f"it was not refused, nobody was there. Do not call "
                           f"this tool again now; tell the user, who can try "
                           f"later or ask them to be at their machine."}
    if terminal.get("code") == "declined" or terminal.get("retryable") is False:
        return {"error": f"'{handle}': {terminal.get('error', 'declined')}",
                "declined": True,
                "retryable": False,
                "message": f"'{handle}' DECLINED this task. This is a person's "
                           f"decision, not a temporary failure: do NOT call "
                           f"this tool again for the same thing. Tell the user "
                           f"it was declined and ask what to do instead."}
    return {"error": f"'{handle}': {terminal.get('error', 'unknown error')}"}


def tool_fed_remove_contact(handle):
    try:
        st, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    try:
        fed.server_remove_contact(server_url, token, handle)
    except fed.FedServerError as e:
        return {"error": f"Could not remove '{handle}': {e}"}
    try:
        if (st.data.get("pinned_keys") or {}).pop(handle, None) is not None:
            st.save_data()
    except Exception:
        pass
    return {"ok": True, "handle": handle,
            "message": f"Contact '{handle}' removed (bilateral)."}


def tool_fed_revoke_child(handle):
    try:
        _, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    try:
        fed.server_revoke_child(server_url, token, handle)
    except fed.FedServerError as e:
        return {"error": f"Could not revoke '{handle}': {e}"}
    came_home = []
    for pack in hoplita_children.load_all():
        if (pack.get("node") or "").strip() == handle:
            hoplita_children.teach(pack["name"], node="")
            came_home.append(pack["name"])
    msg = (f"Child '{handle}' revoked: session invalidated and identity "
           f"deleted. Loses access instantly.")
    if came_home:
        msg += (f" {', '.join(came_home)} now run HERE again — the pack was "
                f"never on that machine only.")
    msg += (" It cannot reach the server or you any more, but whatever already "
            "arrived is still on that machine: the folders you shared with it, "
            "and the pack it was last given.")
    return {"ok": True, "handle": handle, "came_home": came_home,
            "message": msg}


def tool_fed_leave(force_local=False):
    store = _store()
    if not store.has_identity():
        return {"error": "You have no federated identity; there is nothing to leave."}
    handle = store.data.get("handle")
    server_url = store.data.get("server_url")
    token = store.data.get("token")

    server_ok = False
    server_msg = ""
    if server_url and token:
        try:
            resp = fed.server_deregister(server_url, token)
            server_ok = bool(resp.get("ok"))
            rc = resp.get("revoked_children", 0)
            server_msg = (f"Deregistration on the server OK (revoked {rc} child(ren))."
                          if server_ok else "The server did not confirm the deregistration.")
        except fed.FedServerError as e:
            server_msg = f"The server did not respond to the deregistration: {e}"

    if not server_ok and not force_local:
        return {"error": server_msg + " To leave anyway and delete only your "
                "local identity (server down, or the account already deleted "
                "server-side), call fed_leave again with force_local=true.",
                "needs_force_local": True}

    try:
        sql = _sql()
        if sql is not None:
            sql.meta_set("sync_version", 0)
            sql.meta_set(_SYNC_KNOWN, "{}")
            sql.meta_set(_SYNC_PUSHED_FP, "")
            for _p in (_code_projects() or []):
                sql.meta_set(f"code_peers:{_p}", "{}")
                sql.meta_set(f"code_await:{_p}", "")
    except Exception:
        pass
    try:
        fed.ws_stop()
    except Exception:
        pass
    store.clear_identity()
    return {
        "ok": True,
        "left": handle,
        "server_deregistered": server_ok,
        "message": (f"You have left the federation as '{handle}'. "
                    + server_msg +
                    " Local identity deleted. You can register again on another "
                    "server whenever you want."),
    }



import hashlib as _hashlib


def _folder_id_for(handle, folder_name):
    raw = f"{handle}:{folder_name}".encode()
    return _hashlib.sha256(raw).hexdigest()[:16]


def _project_id_for(handle, project_name):
    raw = f"{handle}:project:{project_name}".encode()
    return _hashlib.sha256(raw).hexdigest()[:16]

def tool_fed_list_incoming_projects():
    try:
        _, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    try:
        folders = _with_relogin(lambda: fed.server_list_folders(
            _store().data["server_url"], _store().data["token"])).get("folders", [])
    except fed.FedServerError as e:
        return {"error": f"Could not list projects: {e}"}
    pending = [f for f in folders
               if f.get("kind") == "project"
               and f.get("status") in ("pending", "pending_incoming")
               and f.get("role") == "peer"]
    return {
        "ok": True,
        "count": len(pending),
        "projects": [{"name": f.get("folder_name"), "from": f.get("with"),
                      "project_id": f.get("folder_id")} for f in pending],
        "message": (f"{len(pending)} incoming project(s) to accept."
                    if pending else "No incoming projects to accept."),
    }


def tool_fed_list_shared_projects():
    try:
        _, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    try:
        folders = _with_relogin(lambda: fed.server_list_folders(
            _store().data["server_url"], _store().data["token"])).get("folders", [])
    except fed.FedServerError as e:
        return {"error": f"Could not list shared projects: {e}"}
    projs = [{"name": f.get("folder_name"), "with": f.get("with"),
              "status": f.get("status"),
              "role": ("shared by me" if f.get("role") == "owner"
                       else "shared with me")}
             for f in folders if f.get("kind") == "project"]
    return {
        "ok": True,
        "count": len(projs),
        "projects": projs,
        "message": (f"{len(projs)} shared project(s)." if projs
                    else "You don't participate in any shared project."),
    }


def tool_fed_accept_project(project_name=None, handle=None):
    todo = _todo()
    if todo is None:
        return {"error": "TODO/projects not available in this session."}
    try:
        _, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    listing = tool_fed_list_incoming_projects()
    if listing.get("error"):
        return listing
    cand = listing.get("projects", [])
    if project_name:
        cand = [p for p in cand if p["name"] == project_name]
    if handle:
        cand = [p for p in cand if p["from"] == handle]
    if not cand:
        return {"error": "Could not find an incoming project"
                         + (f" named '{project_name}'" if project_name else "")
                         + (f" from '{handle}'" if handle else "")
                         + ". Use fed_list_incoming_projects to see the pending ones."}
    if len(cand) > 1:
        names = ", ".join(f"'{p['name']}' (from {p['from']})" for p in cand)
        return {"error": f"There are several incoming projects: {names}. "
                         f"Specify project_name and/or handle."}
    proj = cand[0]
    try:
        _with_relogin(lambda: fed.server_accept_folder(
            _store().data["server_url"], _store().data["token"],
            proj["from"], proj["project_id"]))
    except fed.FedServerError as e:
        return {"error": f"Could not accept the project: {e}"}
    if proj["name"] not in todo.get_projects():
        todo.add_project(proj["name"])
    pulled = 0
    try:
        pull = tool_fed_project_pull(proj["name"])
        pulled = pull.get("applied", 0) if isinstance(pull, dict) else 0
    except Exception:
        pass
    msg = f"Project '{proj['name']}' from '{proj['from']}' accepted and created in your TODO."
    if pulled:
        msg += f" {pulled} task(s) brought in."
    else:
        msg += (" No tasks in the mailbox yet: ask the other party to share it "
                "again (re-push) or wait for their next push.")
    return {
        "ok": True,
        "project": proj["name"],
        "from": proj["from"],
        "tasks_pulled": pulled,
        "message": msg,
    }


def tool_fed_share_project(project_name, handle):
    if _is_local_only(project_name):
        return _local_only_refusal(project_name)
    todo = _todo()
    if todo is None:
        return {"error": "TODO/projects not available in this session."}
    if project_name not in todo.get_projects():
        return {"error": f"No project named '{project_name}' exists. "
                         f"Projects: {', '.join(todo.get_projects())}"}
    try:
        store, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    my_handle = store.data.get("handle")
    project_id = _project_id_for(my_handle, project_name)
    peer_pub, kerr = _trusted_pub(server_url, token, handle)
    if kerr:
        return {"error": kerr}
    grant = _sign_grant(store, project_id, handle, peer_pub)
    if not grant:
        return {"error": "Could not SIGN the grant (vault unavailable?): "
                         "nothing was shared. No grant, no share."}
    already = False
    try:
        _with_relogin(lambda: fed.server_share_folder(
            _store().data["server_url"], _store().data["token"], handle,
            project_id, project_name, kind="project", grant=grant))
    except fed.FedServerError as e:
        if e.status == 409 or "409" in str(e) or "ya" in str(e).lower():
            already = True
            try:
                _with_relogin(lambda: fed.server_grant_folder(
                    _store().data["server_url"], _store().data["token"],
                    project_id, handle, grant))
            except fed.FedServerError:
                pass
        else:
            return {"error": f"Could not share the project: {e}"}
    push = tool_fed_project_push(project_name)
    sent = push.get("sent", 0) if isinstance(push, dict) else 0
    prefix = (f"Project '{project_name}' was already shared with '{handle}'; "
              f"tasks resent." if already
              else f"Project '{project_name}' shared with '{handle}'.")
    msg = prefix
    if isinstance(push, dict) and push.get("error"):
        msg += f" (Note: {push['error']})"
    elif sent:
        msg += f" {sent} task(s) sent. Have the other party accept it with fed_accept_project."
    else:
        msg += " (The project has no tasks yet.)"
    return {
        "ok": True,
        "project": project_name,
        "shared_with": handle,
        "tasks_sent": sent,
        "message": msg,
    }


def tool_fed_share_folder(handle, folder_name):
    if _is_local_only(folder_name):
        return _local_only_refusal(folder_name)
    try:
        store, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    my_handle = store.data.get("handle")
    folder_id = _folder_id_for(my_handle, folder_name)
    peer_pub, kerr = _trusted_pub(server_url, token, handle)
    if kerr:
        return {"error": kerr}
    grant = _sign_grant(store, folder_id, handle, peer_pub)
    if not grant:
        return {"error": "Could not SIGN the grant (vault unavailable?): "
                         "nothing was shared. No grant, no share."}
    try:
        resp = fed.server_share_folder(server_url, token, handle, folder_id,
                                       folder_name, grant=grant)
    except fed.FedServerError as e:
        if e.status == 409:
            try:
                fed.server_grant_folder(server_url, token, folder_id, handle,
                                        grant)
            except fed.FedServerError:
                pass
        return {"error": f"Could not share the folder: {e}"}
    _skills_flag = False
    try:
        nm = _notes()
        _skills_flag = bool((nm.get_skill_cats() or {}).get(folder_name)) if nm else False
    except Exception:
        pass
    if _skills_flag:
        try:
            tool_fed_share(handle, SKILLS_MARK_TYPE, {"folder": folder_name})
        except Exception as e:
            print(f"[FED] skills mark not sent for {folder_name!r}: {e}",
                  file=_sys.stderr)

    return {
        "ok": True,
        "folder_name": folder_name,
        "folder_id": folder_id,
        "shared_with": handle,
        "skills": _skills_flag,
        "status": resp.get("status", "pending"),
        "message": f"Folder '{folder_name}' proposed to '{handle}'. Once they "
                   f"accept, it will be a shared space where you can both put in "
                   f"and take out content.",
    }


def tool_fed_accept_folder(handle, folder_name=None, folder_id=None):
    try:
        _, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    if not folder_id:
        try:
            folders = fed.server_list_folders(server_url, token).get("folders", [])
        except fed.FedServerError as e:
            return {"error": f"Could not list folders: {e}"}
        cand = [f for f in folders
                if f.get("with") == handle and f.get("status") == "pending"
                and f.get("kind", "folder") != "project"
                and (folder_name is None or f.get("folder_name") == folder_name)]
        if not cand:
            return {"error": f"Could not find a pending note folder from '{handle}'"
                             + (f" named '{folder_name}'" if folder_name else "")
                             + ". (If it is a PROJECT, use fed_accept_project.)"}
        if len(cand) > 1 and folder_name is None:
            names = ", ".join(f["folder_name"] for f in cand)
            return {"error": f"There are several pending folders from '{handle}': "
                             f"{names}. Specify which with folder_name."}
        folder_id = cand[0]["folder_id"]
        folder_name = cand[0]["folder_name"]
    try:
        resp = fed.server_accept_folder(server_url, token, handle, folder_id)
    except fed.FedServerError as e:
        return {"error": f"Could not accept the folder: {e}"}
    fname = resp.get("folder_name", folder_name)
    pulled = 0
    try:
        pull = tool_fed_folder_pull(fname)
        pulled += pull.get("applied", 0) if isinstance(pull, dict) else 0
    except Exception:
        pass
    try:
        ppull = tool_fed_project_pull(fname)
        pulled += ppull.get("applied", 0) if isinstance(ppull, dict) else 0
    except Exception:
        pass
    msg = (f"Folder '{fname}' accepted. It is now a shared space with "
           f"'{handle}'.")
    if pulled:
        msg += f" Brought in {pulled} note(s) that were already waiting."
    return {
        "ok": True,
        "folder_name": fname,
        "folder_id": folder_id,
        "shared_with": handle,
        "pulled": pulled,
        "message": msg,
    }


def tool_fed_list_folders():
    try:
        _, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    try:
        resp = fed.server_list_folders(server_url, token)
    except fed.FedServerError as e:
        return {"error": f"Could not list folders: {e}"}
    folders = [f for f in resp.get("folders", [])
               if f.get("kind", "folder") != "project"]
    return {
        "ok": True,
        "count": len(folders),
        "folders": folders,
        "message": (f"{len(folders)} shared note folder(s)." if folders
                    else "You don't participate in any shared note folder."),
    }



def tool_fed_publish_folder(folder_name):
    if _is_local_only(folder_name):
        return _local_only_refusal(folder_name)
    try:
        st, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    notes = _notes()
    cats = (notes.get_categories() or []) if notes is not None else []
    if folder_name not in cats:
        return {"error": f"'{folder_name}' is not one of your note folders.",
                "available": cats[:30]}
    my_handle = st.data["handle"]
    fid = _folder_id_for(my_handle, folder_name)
    att = _sign_publish(st, fid)
    if not att:
        return {"error": "Could not SIGN the publication (vault "
                         "unavailable?): nothing was published."}
    try:
        resp = _with_relogin(lambda: fed.server_publish_folder(
            _store().data["server_url"], _store().data["token"],
            fid, folder_name, attestation=att))
    except fed.FedServerError as e:
        return {"error": f"Could not publish: {e}"}
    return {"ok": True, "published": folder_name,
            "subscribers": resp.get("subscribers", 0),
            "message": f"'{folder_name}' is now public: anyone on the server "
                       f"can find it asking for {my_handle}'s public folders "
                       f"and subscribe. They read, you write."}


def tool_fed_unpublish_folder(folder_name):
    try:
        st, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    fid = _folder_id_for(st.data["handle"], folder_name)
    try:
        resp = _with_relogin(lambda: fed.server_unpublish_folder(
            _store().data["server_url"], _store().data["token"], fid))
    except fed.FedServerError as e:
        return {"error": f"Could not unpublish: {e}"}
    return {"ok": True, "unpublished": folder_name,
            "subscribers_dropped": resp.get("subscribers_dropped", 0)}


def tool_fed_public_folders(handle):
    try:
        _, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    try:
        resp = _with_relogin(lambda: fed.server_public_folders(
            _store().data["server_url"], _store().data["token"],
            (handle or "").strip().lower()))
    except fed.FedServerError as e:
        return {"error": f"Could not list public folders: {e}"}
    pubs = resp.get("public_folders", [])
    return {"ok": True, "handle": resp.get("handle"),
            "public_folders": pubs, "count": len(pubs),
            "message": (f"{len(pubs)} public folder(s)." if pubs else
                        "No public folders.")}


def tool_fed_subscribe_folder(handle, folder_name=None, folder_id=None):
    try:
        st, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    handle = (handle or "").strip().lower()
    try:
        cat = _with_relogin(lambda: fed.server_public_folders(
            _store().data["server_url"], _store().data["token"], handle))
    except fed.FedServerError as e:
        return {"error": f"Could not list public folders: {e}"}
    pubs = cat.get("public_folders", [])
    match = [p for p in pubs
             if p.get("folder_id") == folder_id
             or (folder_name and p.get("folder_name") == folder_name)]
    if not match:
        return {"error": f"No public folder of {handle} matches.",
                "available": [{"folder_name": p["folder_name"],
                               "folder_id": p["folder_id"]} for p in pubs]}
    target = match[0]
    _pub, kerr = _trusted_pub(server_url, token, handle)
    if not _pub:
        return {"error": f"Could not subscribe: {kerr}"}
    try:
        resp = _with_relogin(lambda: fed.server_subscribe_folder(
            _store().data["server_url"], _store().data["token"],
            handle, target["folder_id"]))
    except fed.FedServerError as e:
        return {"error": f"Could not subscribe: {e}"}
    _fed_ensure_container("folder", target["folder_name"], _notes())
    return {"ok": True, "subscribed": target["folder_name"],
            "owner": handle, "subscribers": resp.get("subscribers"),
            "message": f"Subscribed to '{target['folder_name']}' from "
                       f"{handle} (read-only). Their updates arrive with "
                       f"their pushes; you cannot write back."}


def tool_fed_unsubscribe_folder(handle, folder_name=None, folder_id=None):
    try:
        st, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    handle = (handle or "").strip().lower()
    fid = folder_id
    if not fid:
        try:
            cat = _with_relogin(lambda: fed.server_public_folders(
                _store().data["server_url"], _store().data["token"], handle))
            match = [p for p in cat.get("public_folders", [])
                     if p.get("folder_name") == folder_name]
            fid = match[0]["folder_id"] if match else None
        except fed.FedServerError:
            fid = None
    if not fid:
        return {"error": "Could not resolve which folder; pass folder_id."}
    try:
        _with_relogin(lambda: fed.server_unsubscribe_folder(
            _store().data["server_url"], _store().data["token"], handle, fid))
    except fed.FedServerError as e:
        return {"error": f"Could not unsubscribe: {e}"}
    return {"ok": True, "unsubscribed": folder_name or fid,
            "message": "Unsubscribed. Your local copy stays yours."}



def _find_shared_peers(server_url, token, name, kind):
    try:
        folders = fed.server_list_folders(server_url, token).get("folders", [])
    except fed.FedServerError:
        return []
    return [(f.get("folder_id"), f.get("with")) for f in folders
            if f.get("folder_name") == name
            and f.get("kind", "folder") == kind]


def _granted_peers(st, server_url, token, name, kind):
    try:
        resp = fed.server_list_folders(server_url, token)
    except fed.FedServerError:
        return None, [], []
    rows = [f for f in resp.get("folders", [])
            if f.get("folder_name") == name
            and f.get("kind", "folder") == kind]
    if not rows:
        return None, [], []
    folder_id = rows[0].get("folder_id")
    my_handle = st.data.get("handle")
    my_pub = st.data.get("public_key_b64")
    _priv, my_sign_pub = _signing_keys(st)
    public = resp.get("public", [])
    peers, errors = [], []
    pub_ok = {}
    for row in rows:
        ph = row.get("with")
        fid = row.get("folder_id")
        if row.get("role") != "owner" and row.get("readonly"):
            continue
        pub, kerr = _trusted_pub(server_url, token, ph)
        if kerr:
            errors.append({"peer": ph, "error": kerr})
            continue
        if row.get("role") == "owner":
            if row.get("readonly"):
                ok = pub_ok.get(fid)
                if ok is None:
                    ok = False
                    for p in public:
                        if p.get("folder_id") != fid:
                            continue
                        pl = (crypto.verify_attestation(
                            my_sign_pub, p.get("attestation") or "")
                            if my_sign_pub else None)
                        ok = bool(isinstance(pl, dict)
                                  and pl.get("typ") == "publish"
                                  and pl.get("folder_id") == fid
                                  and pl.get("owner") == my_handle)
                        break
                    pub_ok[fid] = ok
                if not ok:
                    errors.append({"peer": ph, "error":
                                   "publication not signed by me: refusing to "
                                   "push to this subscriber (republish the "
                                   "folder to sign it)"})
                    continue
                peers.append((ph, pub))
                continue
            payload = _open_grant(my_sign_pub, row.get("grant") or "", fid,
                                  peer=ph, owner=my_handle)
            if not payload or payload.get("peer_pub") != pub:
                errors.append({"peer": ph, "error":
                               "no valid grant SIGNED BY ME for this member: "
                               "not sending (re-share with them, or run the "
                               "re-attestation, to sign it)"})
                continue
            peers.append((ph, pub))
        else:
            owner_sign = _peer_sign_pub(st, server_url, token, ph)
            payload = _open_grant(owner_sign, row.get("grant") or "", fid,
                                  peer=my_handle, owner=ph)
            if not payload or payload.get("peer_pub") != my_pub:
                errors.append({"peer": ph, "error":
                               "the owner's signed grant for me is missing or "
                               "invalid: not pushing to them"})
                continue
            peers.append((ph, pub))
    return folder_id, peers, errors


def _find_shared_folder(server_url, token, folder_name, require_accepted=False,
                        kind=None):
    try:
        folders = fed.server_list_folders(server_url, token).get("folders", [])
    except fed.FedServerError:
        return None, None
    match_pending = None
    for f in folders:
        if f.get("folder_name") != folder_name:
            continue
        if kind and f.get("kind", "folder") != kind:
            continue
        if f.get("status") == "accepted":
            return f.get("folder_id"), f.get("with")
        if f.get("status") in ("pending", "pending_incoming") and not require_accepted:
            match_pending = (f.get("folder_id"), f.get("with"))
    return match_pending if match_pending else (None, None)


def _find_accepted_folder(server_url, token, folder_name):
    return _find_shared_folder(server_url, token, folder_name)


def _new_fed_uuid():
    return _uuid.uuid4().hex


_FED_KINDS = {
    "folder":  {"share": "folder",  "etype": "folder_note",  "metakey": "folder"},
    "project": {"share": "project", "etype": "project_task", "metakey": "project"},
    "whiteboard": {"share": "project", "etype": "project_board", "metakey": "project",
                   "title_key": "wbid", "dissolve": False, "act": "board"},
}


def push_child_pack(pack):
    node = (pack or {}).get("node", "").strip()
    if not node:
        return {"skipped": "no node"}
    body = {k: pack.get(k) for k in hoplita_children.FIELDS if k != "node"}
    body["rev"] = pack.get("rev", 1)
    try:
        return tool_fed_share(node, CHILD_PACK_TYPE, body)
    except Exception as e:
        return {"error": str(e)}


def my_pack():
    sql = _sql()
    if sql is None:
        return None
    try:
        raw = sql.meta_get(_MY_PACK_KEY)
        return _json.loads(raw) if raw else None
    except Exception:
        return None


def _collect_child_packs():
    sql = _sql()
    if sql is None:
        return 0
    try:
        _, server_url, token = _require_session()
        inbox = tool_fed_inbox(CHILD_PACK_TYPE)
    except Exception:
        return 0
    owner = my_owner()
    taken, acked = 0, []
    for env in (inbox.get("items") or []):
        sender = str(env.get("from") or "").strip()
        body = env.get("payload") or {}
        acked.append(env.get("envelope_id"))
        if not owner or sender != owner:
            print(f"[FED] refused a pack from {sender!r}: this node belongs to "
                  f"{owner or 'nobody'}", file=_sys.stderr)
            _push_share_event({"kind": "pack_refused", "peer": sender})
            continue
        try:
            sql.meta_set(_MY_PACK_KEY, _json.dumps(body, ensure_ascii=False))
            taken += 1
            _push_share_event({"kind": "pack_adopted",
                               "name": body.get("name", ""), "peer": sender})
        except Exception:
            pass
    ids = [i for i in acked if i]
    if ids:
        try:
            fed.server_ack_envelopes(server_url, token, ids)
        except Exception:
            pass
    return taken


def _collect_skills_marks():
    nm = _notes()
    if nm is None:
        return 0
    try:
        _, server_url, token = _require_session()
        inbox = tool_fed_inbox(SKILLS_MARK_TYPE)
    except Exception:
        return 0
    owner = my_owner()
    try:
        known = set(nm.get_categories() or [])
    except Exception:
        known = set()
    applied, acked = 0, []
    for env in (inbox.get("items") or []):
        folder = str((env.get("payload") or {}).get("folder") or "").strip()
        sender = str(env.get("from") or "").strip()
        if not folder:
            acked.append(env.get("envelope_id"))
            continue
        if folder not in known:
            continue
        try:
            if owner and sender == owner:
                nm.set_skill_cat(folder, True)
                applied += 1
                _push_share_event({"kind": "skills_marked", "folder": folder,
                                   "peer": sender})
            else:
                _push_share_event({"kind": "skills_offered", "folder": folder,
                                   "peer": sender})
            acked.append(env.get("envelope_id"))
        except Exception:
            pass
    ids = [i for i in acked if i]
    if ids:
        try:
            fed.server_ack_envelopes(server_url, token, ids)
        except Exception:
            pass
    return applied


_PUSHED = {}
_PUSHED_LOCK = _threading.Lock()


def _item_sig(it):
    import hashlib as _hl
    return _hl.sha1(_json.dumps(it, sort_keys=True, default=str)
                    .encode("utf-8")).hexdigest()


def _fed_push(kind, name, store):
    if _is_local_only(name):
        return _local_only_refusal(name)
    cfg = _FED_KINDS[kind]
    try:
        st, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    folder_id, peers, errors = _granted_peers(st, server_url, token, name,
                                              cfg["share"])
    if folder_id is None:
        return {"error": f"'{name}' is not shared as {kind}."}
    my_pub = st.data.get("public_key_b64")
    if not peers:
        return {"error": "No member with a usable key AND a valid signed "
                         "grant — nothing sent.", "errors": errors}
    items = fa.push_shared(store, name, st.data.get("handle", ""))
    sigs = {it.get("uuid"): _item_sig(it) for it in items}
    envs = {}
    sent = 0
    unchanged = 0
    for ph, ppub in peers:
        with _PUSHED_LOCK:
            had = dict(_PUSHED.get((kind, name, ph)) or {})
        for it in items:
            uid = it.get("uuid")
            if had.get(uid) == sigs[uid]:
                unchanged += 1
                continue
            env = envs.get(uid)
            if env is None:
                env = envs[uid] = crypto.make_envelope(
                    cfg["etype"], {"name": name, "item": it},
                    sender_pub_b64=my_pub)
            try:
                blob = _seal(_store(), ppub, env)
                fed.server_send_envelope(server_url, token, ph, cfg["etype"], blob,
                                         {"folder_id": folder_id, cfg["metakey"]: name})
                sent += 1
                with _PUSHED_LOCK:
                    _PUSHED.setdefault((kind, name, ph), {})[uid] = sigs[uid]
            except Exception as e:
                errors.append({"item": uid, "peer": ph, "error": str(e)})
    who = ", ".join(ph for ph, _ in peers)
    res = {"ok": True, "name": name, "kind": kind, "with": who,
           "members": len(peers), "sent": sent, "unchanged": unchanged,
           "message": f"{sent} item(s) from '{name}' -> {len(peers)} member(s) ({who})"
                      + (f"; {unchanged} already up to date there." if unchanged else ".")}
    if errors:
        res["errors"] = errors
    return res


def _fed_ensure_container(kind, name, store):
    try:
        if kind == "project" and hasattr(store, "get_projects"):
            if name not in (store.get_projects() or []) and hasattr(store, "add_project"):
                store.add_project(name)
        elif kind == "folder" and hasattr(store, "get_categories"):
            if name not in (store.get_categories() or []) and hasattr(store, "add_category"):
                store.add_category(name)
    except Exception:
        pass


def _fed_pull(kind, name, store):
    cfg = _FED_KINDS[kind]
    try:
        st, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    my_priv = st.data.get("private_key_b64")
    my_handle = st.data.get("handle") or ""
    del_type = cfg["share"] + "_del"
    types = [cfg["etype"]] + ([del_type] if cfg.get("dissolve", True) else [])
    try:
        resp = fed.server_fetch_inbox_types(server_url, token, types, peek=True)
    except fed.FedServerError as e:
        return {"error": f"Could not fetch the inbox: {e}"}
    all_env = resp.get("envelopes", [])
    accepted = _accepted_names(server_url, token, cfg["share"])
    known = _known_names(server_url, token, cfg["share"])
    for _rn in ([name] if name else list(accepted)):
        if _rn and _rn in accepted:
            _fed_ensure_container(kind, _rn, store)
    id_for = _folder_id_for if kind == "folder" else _project_id_for
    acked, deleted = [], 0

    for env in [e for e in all_env if e.get("resource_type") == del_type
                and cfg.get("dissolve", True)]:
        try:
            p = _unsealed(env).get("payload", {})
        except Exception:
            continue
        sender = env.get("sender", "")
        rname = p.get("name", "")
        acked.append(env.get("id"))
        if id_for(sender, rname) != p.get("folder_id"):
            continue
        try:
            if kind == "folder" and hasattr(store, "delete_category") and rname in store.get_categories():
                store.delete_category(rname); deleted += 1
            elif kind == "project" and hasattr(store, "delete_project") and rname in (store.get_projects() or []):
                store.delete_project(rname); deleted += 1
        except Exception:
            pass

    by_name = {}
    for env in [e for e in all_env if e.get("resource_type") == cfg["etype"]]:
        try:
            p = _unsealed(env).get("payload", {})
        except Exception:
            acked.append(env.get("id")); continue
        rname = p.get("name") or ""
        if name and rname != name:
            continue
        if rname not in accepted:
            if known is not None and rname not in known:
                acked.append(env.get("id"))
            continue
        acked.append(env.get("id"))
        it = p.get("item")
        if it:
            by_name.setdefault(rname, []).append(it)

    applied = 0
    for rname, items in by_name.items():
        r = fa.pull_shared(store, rname, items, my_handle, _new_fed_uuid,
                           title_key=cfg.get("title_key", "title"))
        applied += len(r.get("changed", []))
        for d in (r.get("details") or []):
            who = d.get("editor") or ""
            if not who or who == my_handle:
                continue
            _k = cfg.get("act") or ("note" if kind == "folder" else "task")
            _k += ("_del" if d.get("deleted")
                   else "_add" if d.get("added") else "_edit")
            _activity_record(who, _k, rname, d.get("title", ""))

    if acked:
        try:
            fed.server_ack_envelopes(server_url, token, acked)
        except fed.FedServerError:
            pass
    return {"ok": True, "applied": applied, "deleted": deleted,
            "message": f"{applied} item(s) applied"
                       + (f", {deleted} resource(s) deleted" if deleted else "") + "."}

def tool_fed_folder_push(folder_name):
    notes = _notes()
    if notes is None:
        return {"error": "Notes not available in this session."}
    return _fed_push("folder", folder_name, notes)


_AUTOPUSH_TIMERS = {}
_AUTOPUSH_LOCK = _threading.Lock()
_AUTOPUSH_DEBOUNCE_S = 2.0

_TODO_MUTATORS = {"add_task", "update_task", "delete_task"}
_NOTES_MUTATORS = {"add_note", "update_note", "delete_note"}


def _autopush_fire(kind, name):
    with _AUTOPUSH_LOCK:
        _AUTOPUSH_TIMERS.pop((kind, name), None)
    try:
        push = (tool_fed_project_push if kind in ("project", "whiteboard")
                else tool_fed_folder_push)
        r = push(name)
        if isinstance(r, dict) and r.get("sent"):
            print(f"[FED-SYNC] auto-push {kind} '{name}': "
                  f"{r['sent']} item(s)")
        elif isinstance(r, dict) and r.get("error"):
            print(f"[FED-SYNC] ✗ auto-push {kind} '{name}': {r['error']}")
    except Exception as e:
        print(f"[FED-SYNC] auto-push {kind} '{name}' failed: {e}")


def _autopush_schedule(kind, name):
    if not name:
        return
    if _is_local_only(name):
        return
    try:
        store = _store()
        if store is None or not store.has_identity():
            return
        fid, _peer = _find_shared_folder(store.data["server_url"],
                                         store.data["token"], name,
                                         kind=_FED_KINDS.get(kind, {}).get("share", kind))
        if not fid:
            return
    except Exception:
        return
    if kind == "whiteboard":
        kind = "project"
    with _AUTOPUSH_LOCK:
        t = _AUTOPUSH_TIMERS.get((kind, name))
        if t is not None:
            t.cancel()
        t = _threading.Timer(_AUTOPUSH_DEBOUNCE_S, _autopush_fire,
                             args=(kind, name))
        t.daemon = True
        _AUTOPUSH_TIMERS[(kind, name)] = t
        t.start()


_SNAP_PUSH_DELAY = 60.0
_snap_push_timer = None
_MUTATOR_PREFIXES = ("add_", "delete_", "update_", "set_", "save_", "create_",
                     "remove_", "rename_", "clear_", "toggle_", "move_",
                     "import_")


_snap_merging = False


def _on_store_change(store):
    if _snap_merging:
        return
    _snapshot_autopush_schedule()


try:
    hs.set_change_hook(_on_store_change)
except Exception:
    pass


_snap_last_arm = 0.0
_SNAP_REARM_EVERY = 5.0


def _snapshot_autopush_schedule():
    global _snap_push_timer, _snap_last_arm
    try:
        _now_arm = _time.time()
        if (_snap_push_timer is not None and _snap_push_timer.is_alive()
                and _now_arm - _snap_last_arm < _SNAP_REARM_EVERY):
            return
        _snap_last_arm = _now_arm
        if _snap_push_timer is not None:
            _snap_push_timer.cancel()
        t = _threading.Timer(_SNAP_PUSH_DELAY, _snapshot_autopush_fire)
        t.daemon = True
        _snap_push_timer = t
        t.start()
    except Exception:
        pass


def _snapshot_autopush_fire():
    try:
        st = _store()
        if not (st.has_identity() and st.data.get("token")):
            return
        r = tool_fed_sync_push()
        if isinstance(r, dict) and r.get("needs_pull"):
            tool_fed_sync_pull()
            tool_fed_sync_push()
    except Exception:
        pass


def after_local_mutation(manager_name, method, args, kwargs, result):
    try:
        if any(method.startswith(p) for p in _MUTATOR_PREFIXES):
            _snapshot_autopush_schedule()
    except Exception:
        pass
    try:
        if manager_name == "todo" and method in _TODO_MUTATORS:
            name = kwargs.get("project")
            if name is None and isinstance(result, dict):
                name = result.get("project")
            if name is None and method == "add_task" and len(args) >= 6:
                name = args[5]
            if name is None and method == "update_task" and args:
                todo = _todo()
                if todo is not None:
                    for tk in todo.get_tasks():
                        if tk.get("id") == args[0]:
                            name = tk.get("project")
                            break
            if name:
                _autopush_schedule("project", name)
            elif method == "delete_task":
                try:
                    store = _store()
                    for pn in _accepted_names(store.data["server_url"],
                                              store.data["token"], "project"):
                        _autopush_schedule("project", pn)
                except Exception:
                    pass
        elif manager_name == "notes" and method in _NOTES_MUTATORS:
            name = kwargs.get("category")
            if name is None and args:
                name = args[0]
            if name:
                _autopush_schedule("folder", name)
    except Exception:
        pass


def _resource_info(kind, name):
    try:
        store, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    try:
        folders = fed.server_list_folders(server_url, token).get("folders", [])
    except fed.FedServerError as e:
        return {"error": str(e)}
    mine_owner = [f for f in folders
                  if f.get("folder_name") == name
                  and f.get("kind", "folder") == kind
                  and f.get("role") == "owner"]
    mine_member = [f for f in folders
                   if f.get("folder_name") == name
                   and f.get("kind", "folder") == kind
                   and f.get("role") == "peer"]
    if mine_owner:
        return {"ok": True, "name": name, "kind": kind, "role": "owner",
                "members": [{"handle": f.get("with"),
                             "status": f.get("status")} for f in mine_owner]}
    if mine_member:
        f = mine_member[0]
        return {"ok": True, "name": name, "kind": kind, "role": "member",
                "owner": f.get("with"), "status": f.get("status")}
    return {"ok": True, "name": name, "kind": kind, "role": "none",
            "message": "Not shared."}


def tool_fed_shared_overview():
    try:
        store, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    try:
        _fresp = fed.server_list_folders(server_url, token)
        folders = _fresp.get("folders", [])
        contacts = fed.server_list_contacts(server_url, token).get("contacts", [])
    except fed.FedServerError as e:
        return {"error": str(e)}
    try:
        children = fed.server_list_children(server_url, token).get("children", [])
    except Exception:
        children = []
    out = {"folders": {}, "projects": {}}
    for f in folders:
        kind = f.get("kind", "folder")
        name = f.get("folder_name")
        bucket = out["projects"] if kind == "project" else out["folders"]
        info = bucket.setdefault(name, {"name": name, "kind": kind,
                                        "role": "none", "members": []})
        if f.get("role") == "owner":
            info["role"] = "owner"
            if f.get("readonly"):
                info["subscribers"] = info.get("subscribers", 0) + 1
            else:
                info["members"].append({"handle": f.get("with"),
                                        "status": f.get("status")})
        elif info["role"] != "owner":
            info["role"] = "member"
            info["owner"] = f.get("with")
            info["status"] = f.get("status")
            if f.get("readonly"):
                info["readonly"] = True
    return {"ok": True, "folders": out["folders"], "projects": out["projects"],
            "public": _fresp.get("public") or [],
            "contacts": [{"handle": c.get("handle"),
                          "status": c.get("status")} for c in contacts],
            "children": [{"handle": c.get("handle"),
                          "status": c.get("status")} for c in children]}


def tool_fed_folder_info(folder_name):
    if _is_local_only(folder_name):
        return _local_only_refusal(folder_name)
    return _resource_info("folder", folder_name)


def tool_fed_project_info(project_name):
    if _is_local_only(project_name):
        return _local_only_refusal(project_name)
    return _resource_info("project", project_name)


def tool_fed_delete_shared(kind, name):
    try:
        store, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    info = _resource_info(kind, name)
    if info.get("role") != "owner":
        return {"error": "Only the owner can delete for everyone. If you are a "
                         "member, use unshare (your copy is yours)."}
    my_handle = store.data["handle"]
    my_pub = store.data.get("public_key_b64")
    fid = (_project_id_for if kind == "project" else _folder_id_for)(my_handle, name)
    links = _find_shared_peers(server_url, token, name, kind)
    link_fid = links[0][0] if links else fid
    rtype = "project_del" if kind == "project" else "folder_del"
    notified = 0
    for m in info.get("members", []):
        if m.get("status") != "accepted":
            continue
        try:
            pub, kerr = _trusted_pub(server_url, token, m["handle"])
            if kerr:
                continue
            payload = {"kind": kind, "name": name, "folder_id": fid}
            env = crypto.make_envelope(rtype, payload, sender_pub_b64=my_pub)
            blob = _seal(_store(), pub, env)
            fed.server_send_envelope(server_url, token, m["handle"], rtype,
                                     blob, {"folder_id": fid})
            notified += 1
        except Exception:
            continue
    try:
        _with_relogin(lambda: fed.server_unshare_folder(
            _store().data["server_url"], _store().data["token"], link_fid))
    except fed.FedServerError:
        pass
    deleted_local = False
    try:
        if kind == "project":
            todo = _todo()
            if todo is not None and hasattr(todo, "delete_project"):
                todo.delete_project(name); deleted_local = True
        else:
            notes = _notes()
            if notes is not None and hasattr(notes, "delete_category"):
                notes.delete_category(name); deleted_local = True
    except Exception:
        pass
    return {"ok": True, "name": name, "kind": kind,
            "members_notified": notified, "deleted_local": deleted_local,
            "message": f"'{name}' deleted for everyone: {notified} member(s) "
                       f"will receive the deletion; links broken."}


def _unshare(kind, name, peer=None):
    try:
        store, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    fid, _link_peer = _find_shared_folder(server_url, token, name, kind=kind)
    if not fid:
        noun = "project" if kind == "project" else "folder"
        return {"error": f"There is no shared {noun} named '{name}'."}
    try:
        _with_relogin(lambda: fed.server_unshare_folder(
            _store().data["server_url"], _store().data["token"], fid,
            peer_handle=peer))
    except fed.FedServerError as e:
        return {"error": f"Could not unshare: {e}"}
    purged = 0
    try:
        types = (["project_task", "project_tomb", "project_board"] if kind == "project"
                 else ["folder_note", "folder_tomb"])
        resp = fed.server_fetch_inbox_types(server_url, token, types,
                                            peek=True)
        my_priv = store.data.get("private_key_b64")
        ids = []
        for env in resp.get("envelopes", []):
            try:
                dec = _unsealed(env)
                p = dec.get("payload", {})
            except Exception:
                continue
            res_name = p.get("name") or p.get("project") or p.get("folder") or ""
            if res_name == name:
                ids.append(env.get("id"))
        if ids:
            fed.server_ack_envelopes(server_url, token, ids)
            purged = len(ids)
    except fed.FedServerError:
        pass
    noun = "project" if kind == "project" else "folder"
    return {"ok": True, "name": name, "kind": kind, "purged": purged,
            "message": f"{noun.capitalize()} '{name}' unshared. Your local copy "
                       f"is yours (delete it if you want); the other side's is "
                       f"theirs. {purged} pending envelope(s) purged."}


def tool_fed_unshare_folder(folder_name, peer=None):
    return _unshare("folder", folder_name, peer=peer)


def tool_fed_unshare_project(project_name, peer=None):
    return _unshare("project", project_name, peer=peer)


def _known_names(server_url, token, kind):
    try:
        folders = fed.server_list_folders(server_url, token).get("folders", [])
    except fed.FedServerError:
        return None
    return {f.get("folder_name") for f in folders
            if f.get("kind", "folder") == kind}


def _accepted_names(server_url, token, kind):
    try:
        folders = fed.server_list_folders(server_url, token).get("folders", [])
    except fed.FedServerError:
        return set()
    return {f.get("folder_name") for f in folders
            if f.get("status") == "accepted"
            and f.get("kind", "folder") == kind}


def tool_fed_folder_pull(folder_name=None):
    notes = _notes()
    if notes is None:
        return {"error": "Notes not available in this session."}
    return _fed_pull("folder", folder_name, notes)


def _boards_along(res, fn):
    try:
        import wb_sync
        b = fn(wb_sync.ProjectBoards())
    except Exception as e:
        b = {"error": str(e)}
    if isinstance(res, dict) and not res.get("error"):
        res["whiteboards"] = {k: b.get(k) for k in ("sent", "applied", "error")
                              if isinstance(b, dict) and b.get(k) is not None}
    return res


def tool_fed_project_push(project_name):
    todo = _todo()
    if todo is None:
        return {"error": "TODO/projects not available in this session."}
    return _boards_along(_fed_push("project", project_name, todo),
                         lambda st: _fed_push("whiteboard", project_name, st))

def tool_fed_project_pull(project_name=None):
    todo = _todo()
    if todo is None:
        return {"error": "TODO/projects not available in this session."}
    return _boards_along(_fed_pull("project", project_name, todo),
                         lambda st: _fed_pull("whiteboard", project_name, st))


import time as _time

_sync_thread = None
_sync_stop = None
_sync_interval = 15
_sync_last_status = {"running": False, "last_run": None, "folders": 0, "errors": []}
_SYNC_PARACHUTE_S = 300
_SYNC_DOWN_S = 60
_sync_wake = _threading.Event()


def _sync_wait(interval):
    start = _time.time()
    while _sync_stop is not None and not _sync_stop.is_set():
        try:
            up = fed.ws_is_up() is True
        except Exception:
            up = False
        left = ((_SYNC_PARACHUTE_S if up else max(interval, _SYNC_DOWN_S))
                - (_time.time() - start))
        if left <= 0:
            return "timer"
        if _sync_wake.wait(min(left, interval)):
            _sync_wake.clear()
            return "rung"
    return "stop"


def sync_ring():
    _sync_wake.set()


def _offer_grant_ok(st, server_url, token, row):
    owner = row.get("with")
    fid = row.get("folder_id")
    my_handle = st.data.get("handle")
    my_pub = st.data.get("public_key_b64")
    owner_sign = _peer_sign_pub(st, server_url, token, owner)
    payload = _open_grant(owner_sign, row.get("grant") or "", fid,
                          peer=my_handle, owner=owner)
    return bool(payload and payload.get("peer_pub") == my_pub)


def _process_share_offers():
    try:
        store, server_url, token = _require_session()
    except Exception:
        return
    try:
        folders = fed.server_list_folders(server_url, token).get("folders", [])
    except fed.FedServerError:
        return
    pend = [f for f in folders if f.get("status") == "pending"]
    if not pend:
        return
    try:
        contacts = fed.server_list_contacts(server_url, token).get("contacts", [])
    except fed.FedServerError:
        contacts = []
    perms_by = {h: (t.get("permissions") or {})
                for h, t in _signed_trust_of(store, server_url, token, contacts).items()}
    for f in pend:
        owner = f.get("with")
        fid = f.get("folder_id")
        fname = f.get("folder_name")
        if not owner or not fid:
            continue
        rtype = "proyectos" if f.get("kind", "folder") == "project" else "notas"
        level = ((perms_by.get(owner) or {}).get(rtype) or {}).get("level") or "review"
        if level == "auto":
            if not _offer_grant_ok(store, server_url, token, f):
                continue
            try:
                fed.server_accept_folder(server_url, token, owner, fid)
                if rtype == "proyectos":
                    tool_fed_project_pull(fname)
                else:
                    tool_fed_folder_pull(fname)
            except Exception:
                pass
            continue
        continue


def _sync_loop(interval):
    global _sync_last_status
    while not _sync_stop.is_set():
        with fed.sync_pass():
            try:
                store = _get_store() if _get_store else None
                if store and store.has_identity() and store.data.get("token"):
                    server_url = store.data["server_url"]
                    token = store.data["token"]
                    _pending = fed.pass_inbox_look(server_url, token)

                    def _has(*kinds, prefix=None):
                        if _pending is None:
                            return True
                        return bool(_pending & set(kinds)) or bool(
                            prefix and any(str(x).startswith(prefix)
                                           for x in _pending))
                    try:
                        _process_share_offers()
                    except Exception:
                        pass
                    if _has(prefix="code_"):
                        _code_engine_tick()
                    try:
                        if _has("folder.skills"):
                            _collect_skills_marks()
                        if _has("child.pack"):
                            _collect_child_packs()
                    except Exception:
                        pass
                    try:
                        folders = fed.server_list_folders(server_url, token).get("folders", [])
                    except Exception:
                        folders = []
                    accepted = [f for f in folders if f.get("status") == "accepted"]
                    errs = []
                    for f in accepted:
                        name = f.get("folder_name")
                        if not name:
                            continue
                        try:
                            if _has("folder_note", "folder_tomb", "folder_del"):
                                tool_fed_folder_pull(name)
                        except Exception as e:
                            errs.append({"folder": name, "op": "notes_pull", "error": str(e)})
                        try:
                            tool_fed_folder_push(name)
                        except Exception as e:
                            errs.append({"folder": name, "op": "notes_push", "error": str(e)})
                        try:
                            if _has("project_task", "project_tomb", "project_del",
                                    "project_board"):
                                tool_fed_project_pull(name)
                        except Exception as e:
                            errs.append({"folder": name, "op": "tasks_pull", "error": str(e)})
                        try:
                            tool_fed_project_push(name)
                        except Exception:
                            pass
                    _sync_last_status = {
                        "running": True, "last_run": _time.time(),
                        "folders": len(accepted), "errors": errs}
            except Exception as e:
                _sync_last_status = {"running": True, "last_run": _time.time(),
                                     "folders": 0, "errors": [{"error": str(e)}]}
        _sync_wait(interval)
    _sync_last_status["running"] = False


def start_sync_engine(interval=None):
    global _sync_thread, _sync_stop, _sync_interval
    if interval:
        _sync_interval = interval
    if _sync_thread and _sync_thread.is_alive():
        return {"ok": True, "message": "The sync engine was already running."}
    _sync_stop = _threading.Event()
    _sync_thread = _threading.Thread(target=_sync_loop, args=(_sync_interval,),
                                     daemon=True, name="fed-sync")
    _sync_thread.start()
    return {"ok": True, "interval": _sync_interval,
            "message": f"Folder sync engine started (every {_sync_interval}s)."}


def stop_sync_engine():
    global _sync_stop
    if _sync_stop:
        _sync_stop.set()
        _sync_wake.set()
    return {"ok": True, "message": "Sync engine stopped."}


def get_contact_trust(handle):
    try:
        st, server_url, token = _require_session()
    except Exception:
        return None
    if handle and handle == (st.data.get("owner") or ""):
        return "open"
    try:
        resp = fed.server_list_contacts(server_url, token)
    except fed.FedServerError:
        return None
    trust = _signed_trust_of(st, server_url, token, resp.get("contacts", [])).get(handle)
    if trust is None:
        return None
    lvl = ((trust.get("permissions") or {}).get("sesiones") or {}).get("level")
    if lvl == "auto":
        return "open"
    if lvl == "review":
        return "accept"
    if lvl == "no":
        return "reject"
    return trust.get("level") or "accept"


_session_thread = None
_session_stop = None
_session_interval = 1.5

_CATCHUP_PARACHUTE_S = 300
_CATCHUP_DOWN_S = 5


def _catchup_due(ws_up, prev_ws_up, last_ts, now):
    if last_ts <= 0:
        return True
    if not ws_up:
        return (now - last_ts) >= _CATCHUP_DOWN_S
    if prev_ws_up is False:
        return True
    return (now - last_ts) >= _CATCHUP_PARACHUTE_S
import queue as _queue
_PENDING_APPROVALS = {}
_PENDING_LOCK = _threading.Lock()
_EXPIRED_APPROVALS = set()
APPROVAL_TIMEOUT_S = 300
_approval_seq = 0


_DENIED_UNTIL = {}
DENY_COOLDOWN_S = 120


def deny_cooldown_left(peer):
    return max(0.0, _DENIED_UNTIL.get(peer, 0) - _time.time())


def clear_deny_cooldown(peer):
    _DENIED_UNTIL.pop(peer, None)


def _bridge_approver(peer, message, analyze, session_id=""):
    global _approval_seq
    left = deny_cooldown_left(peer)
    if left > 0:
        print(f"[SESSION-IN] auto-denied {peer!r}: you said no "
              f"{int(DENY_COOLDOWN_S - left)}s ago ({int(left)}s left)",
              file=_sys.stderr)
        return False
    with _PENDING_LOCK:
        _approval_seq += 1
        aid = f"appr_{_approval_seq}"
        q = _queue.Queue(maxsize=1)
        _PENDING_APPROVALS[aid] = {"peer": peer, "message": message,
                                   "analyze": analyze, "queue": q,
                                   "session_id": session_id or ""}
    try:
        answer = q.get(timeout=APPROVAL_TIMEOUT_S)
        return None if answer is None else bool(answer)
    except _queue.Empty:
        with _PENDING_LOCK:
            _EXPIRED_APPROVALS.add(aid)
        return "timeout"
    finally:
        with _PENDING_LOCK:
            _PENDING_APPROVALS.pop(aid, None)


def withdraw_approval(session_id):
    if not session_id:
        return False
    with _PENDING_LOCK:
        for aid, v in list(_PENDING_APPROVALS.items()):
            if v.get("session_id") == session_id:
                try:
                    v["queue"].put_nowait(None)
                except Exception:
                    pass
                return True
    return False


def list_pending_approvals():
    with _PENDING_LOCK:
        return [{"id": aid, "peer": v["peer"], "message": v["message"],
                 "analyze": v["analyze"]}
                for aid, v in _PENDING_APPROVALS.items()]


def approval_expired(approval_id):
    with _PENDING_LOCK:
        return approval_id in _EXPIRED_APPROVALS


def resolve_approval(peer, approved, approval_id=""):
    if approval_id:
        with _PENDING_LOCK:
            v = _PENDING_APPROVALS.get(approval_id)
        if v is None or v["peer"] != peer:
            return {"ok": False, "reason": "that request is no longer pending"}
    if approved:
        clear_deny_cooldown(peer)
    else:
        _DENIED_UNTIL[peer] = _time.time() + DENY_COOLDOWN_S
    with _PENDING_LOCK:
        for aid, v in list(_PENDING_APPROVALS.items()):
            if (aid == approval_id) if approval_id else (v["peer"] == peer):
                try:
                    v["queue"].put_nowait(bool(approved))
                except Exception:
                    pass
                return {"ok": True, "resolved": aid, "approved": bool(approved)}
    return {"ok": False, "reason": "no pending approval for that peer"}


_SESSION_EVENTS = []
_SESSION_EVENTS_LOCK = _threading.Lock()
_OUTBOUND_ORIGINATORS = {}
_OUTBOUND_LOCK = _threading.Lock()


TURN_STOP_PROBE = None
TURN_EMITTER = None


def _turn_stop_probe():
    try:
        return TURN_STOP_PROBE() if TURN_STOP_PROBE else None
    except Exception:
        return None


def _push_session_event(peer, direction, ev):
    with _SESSION_EVENTS_LOCK:
        _SESSION_EVENTS.append({"peer": peer, "direction": direction, "event": ev})


def _push_inbound_session_event(peer, ev):
    _push_session_event(peer, "in", ev)


def _push_outbound_session_event(peer, ev):
    _push_session_event(peer, "out", ev)


def drain_session_events():
    with _SESSION_EVENTS_LOCK:
        out = list(_SESSION_EVENTS)
        _SESSION_EVENTS.clear()
    return out


_SHARE_EVENTS = []
_SHARE_EVENTS_LOCK = _threading.Lock()
_WAKE_EVENTS = []
_WAKE_LOCK = _threading.Lock()


def my_owner():
    try:
        st = _get_store() if _get_store else None
        return (st.data.get("owner") or "").strip() if st else ""
    except Exception:
        return ""


def _push_share_event(ev):
    with _SHARE_EVENTS_LOCK:
        _SHARE_EVENTS.append(ev)


def drain_share_events():
    with _SHARE_EVENTS_LOCK:
        out = list(_SHARE_EVENTS)
        _SHARE_EVENTS.clear()
    return out


def _push_wake(kind, data=None):
    with _WAKE_LOCK:
        _WAKE_EVENTS.append({"kind": kind, "data": data or {}})


def drain_wake_events():
    with _WAKE_LOCK:
        out = list(_WAKE_EVENTS)
        _WAKE_EVENTS.clear()
    return out


def _register_outbound_originator(peer, orig):
    with _OUTBOUND_LOCK:
        _OUTBOUND_ORIGINATORS[peer] = orig


def _unregister_outbound_originator(peer):
    with _OUTBOUND_LOCK:
        _OUTBOUND_ORIGINATORS.pop(peer, None)


def start_outbound_session(peer, message):
    import fed_session
    pack = hoplita_children.find(peer)
    if pack is not None:
        def _local_worker():
            res = tool_fed_ask_agent(
                pack["name"], message,
                event_sink=lambda ev, _p=peer: _push_outbound_session_event(_p, ev))
            if isinstance(res, dict) and res.get("error"):
                _push_outbound_session_event(peer, {"event": "error",
                                                    "error": res["error"]})
        _threading.Thread(target=_local_worker, daemon=True,
                          name=f"child-{peer}").start()
        return {"ok": True, "local": True}
    with _OUTBOUND_LOCK:
        orig = _OUTBOUND_ORIGINATORS.get(peer)
    if orig is not None and orig.is_alive():
        try:
            orig.send_turn(message)
            return {"ok": True}
        except Exception as e:
            print(f"[SESSION-OUT] resend ERROR with {peer!r}: {e}",
                  file=_sys.stderr)
    print(f"[SESSION-OUT] opening outbound session → {peer!r} "
          f"(msg {len(message or '')} chars)", file=_sys.stderr)
    o = fed_session.SessionOriginator(
        peer, on_event=lambda ev, _p=peer: _push_outbound_session_event(_p, ev))
    _register_outbound_originator(peer, o)

    def worker():
        try:
            o.start(message, persistent=True)
            print(f"[SESSION-OUT] {peer!r} listener ended", file=_sys.stderr)
        except Exception as e:
            print(f"[SESSION-OUT] ERROR with {peer!r}: {e}", file=_sys.stderr)
            _push_outbound_session_event(peer, {"event": "error", "error": str(e)})
        finally:
            _unregister_outbound_originator(peer)

    _threading.Thread(target=worker, daemon=True, name=f"session-out-{peer}").start()
    return {"ok": True}


def inject_local_session_turn(peer, message):
    import fed_session
    if not fed_session.has_inbound_session(peer):
        return {"ok": False,
                "error": f"no active incoming session with '{peer}'"}

    def worker():
        try:
            fed_session.inject_local_turn(
                peer, message,
                local_sink=lambda ev, _p=peer: _push_inbound_session_event(_p, ev))
        except Exception as e:
            print(f"[SESSION-IN] inject ERROR with {peer!r}: {e}",
                  file=_sys.stderr)
            _push_inbound_session_event(peer, {"event": "error", "error": str(e)})

    _threading.Thread(target=worker, daemon=True,
                      name=f"session-inject-{peer}").start()
    return {"ok": True}


def close_inbound_session(peer):
    import fed_session
    return fed_session.close_inbound_session(peer)


def stop_outbound_session(peer):
    with _OUTBOUND_LOCK:
        orig = _OUTBOUND_ORIGINATORS.get(peer)
    if orig:
        try:
            orig.stop()
        except Exception:
            pass
        return {"ok": True}
    return {"ok": False, "reason": "no active outgoing session"}


def _mine_by_device(envelopes):
    import hoplita_device as _dev
    mine = _dev.device_id()
    out = []
    for e in envelopes or []:
        want = ((e.get("meta") or {}).get("device") or "").strip()
        if not want or want == mine:
            out.append(e)
    return out


_DISPATCHED_ENVS = {}
_DISPATCHED_GUARD = _threading.Lock()


def _first_dispatch(env_id):
    if not env_id:
        return True
    now = _time.time()
    with _DISPATCHED_GUARD:
        if env_id in _DISPATCHED_ENVS:
            return False
        _DISPATCHED_ENVS[env_id] = now
        if len(_DISPATCHED_ENVS) > 2000:
            for k in sorted(_DISPATCHED_ENVS, key=_DISPATCHED_ENVS.get)[:1000]:
                _DISPATCHED_ENVS.pop(k, None)
    return True


def _dispatch_session_envelope(env):
    import fed_session
    if not _first_dispatch(env.get("id")):
        print(f"[SESSION-IN] envelope {env.get('id')} already dispatched: skipped",
              file=_sys.stderr)
        return
    rtype = env.get("resource_type")
    if rtype == fed_session.T_STOP:
        fed_session.handle_incoming_stop(env)
        withdraw_approval((env.get("meta") or {}).get("session_id"))
        _sender = env.get("sender", "")
        if _sender:
            _push_inbound_session_event(
                _sender, {"event": "session_closed", "by": "peer"})
            try:
                fed_session._unregister_inbound_session(_sender)
            except Exception:
                pass
        return
    elif rtype == fed_session.T_TURN:
        appr = _bridge_approver
        sender = env.get("sender", "")
        print(f"[SESSION-IN] received agent.turn from {sender!r} → dispatching",
              file=_sys.stderr)
        sink = (lambda ev, _p=sender: _push_inbound_session_event(_p, ev))
        _threading.Thread(
            target=_serialized_incoming_turn,
            args=(env, get_contact_trust, appr, sink),
            daemon=True, name="fed-incoming-turn").start()


_TURN_LOCKS = {}
_TURN_LOCKS_GUARD = _threading.Lock()


def _turn_lock(peer):
    with _TURN_LOCKS_GUARD:
        lk = _TURN_LOCKS.get(peer)
        if lk is None:
            lk = _threading.Lock()
            _TURN_LOCKS[peer] = lk
        return lk


def _serialized_incoming_turn(env, get_trust, approver, sink):
    import fed_session
    peer = env.get("sender", "")
    try:
        _me = _store().data.get("handle", "")
    except Exception:
        _me = ""
    if peer and peer == _me:
        return fed_session.handle_incoming_turn(env, get_trust, approver, sink)
    lk = _turn_lock(peer)
    if lk.locked():
        print(f"[SESSION-IN] {peer!r} busy → this turn waits its place in line",
              file=_sys.stderr)
        try:
            sink({"event": "queued", "peer": peer})
        except Exception:
            pass
    with lk:
        return fed_session.handle_incoming_turn(env, get_trust, approver, sink)


def _ws_event(event, data):
    import fed_session
    if event == "live":
        fed_session.on_live(data or {})
        return
    if event == "live.refused":
        fed_session.live_refused(data or {})
        return
    if event == "notice":
        _push_wake("notice", data or {})
        return
    if event == "ws.connected":
        _push_wake("resync", {})
        fed.invalidate_folders()
        fed.invalidate_keys()
        fed.invalidate_inbox()
        sync_ring()
        fed_session.wake_all()
        return
    if event == "contact.update":
        fed.invalidate_keys()
        fed.invalidate_folders()
    if event == "presence" or event == "contact.update":
        _push_wake("contacts", data or {})
        return
    if event in ("folder.shared", "folder.accepted", "folder.unshared"):
        fed.invalidate_folders()
        sync_ring()
    if event == "folder.shared":
        d = data or {}
        _push_share_event({"from": d.get("from", ""),
                           "folder_name": d.get("folder_name", ""),
                           "kind": d.get("kind", "folder")})
        return
    if event != "envelope.new":
        return
    fed.invalidate_inbox()
    rtype = (data or {}).get("resource_type")
    if rtype not in ("calendar_event", "project_task", "folder_note",
                     "project_tomb", "folder_tomb", "project_del", "folder_del",
                     "project_board",
                     fed_session.T_EVENT, fed_session.T_CLIENT_TOOL_CALL,
                     fed_session.T_TURN, fed_session.T_STOP):
        sync_ring()
    if rtype == "calendar_event":
        _threading.Thread(target=_auto_accept_appointments, daemon=True,
                          name="fed-auto-appt").start()
        return
    if rtype in ("project_task", "folder_note", "project_tomb", "folder_tomb",
                 "project_del", "folder_del", "project_board"):
        pull = (tool_fed_project_pull
                if rtype in ("project_task", "project_tomb", "project_del",
                             "project_board")
                else tool_fed_folder_pull)
        _threading.Thread(target=lambda: pull(), daemon=True,
                          name="fed-auto-pull").start()
        return
    if rtype in (fed_session.T_EVENT, fed_session.T_CLIENT_TOOL_CALL):
        fed_session.wake_all()
        return
    if rtype not in (fed_session.T_TURN, fed_session.T_STOP):
        return
    try:
        store = _store()
        resp = fed.server_fetch_inbox_types(
            store.data["server_url"], store.data["token"],
            [fed_session.T_TURN, fed_session.T_STOP],
            peek=True, prefer_rest=True)
        _seen = resp.get("envelopes", [])
        _pushed = _mine_by_device(_seen)
        if len(_seen) != len(_pushed):
            print(f"[FED-WS] {len(_seen) - len(_pushed)} envelope(s) left for "
                  f"another device", file=_sys.stderr)
        if _pushed:
            try:
                fed.server_ack_envelopes(
                    store.data["server_url"], store.data["token"],
                    [e.get("id") for e in _pushed if e.get("id")])
            except Exception as _ae:
                print(f"[FED-WS] ack failed: {_ae}", file=_sys.stderr)
        print(f"[FED-WS] push → fetched {len(_pushed)} session envelope(s) via REST",
              file=_sys.stderr)
        for env in _pushed:
            _dispatch_session_envelope(env)
    except Exception as e:
        print(f"[FED-WS] error processing session push: {e}")


def _session_loop(interval):
    import fed_session
    last_token = None
    _hb_done = {"ok": False, "noid": False}
    _fail = {"n": 0, "last": "", "said": 0.0}
    while _session_stop and not _session_stop.is_set():
        try:
            store = _store()
            if store is not None and store.has_identity() and store.data.get("token"):
                server_url = store.data["server_url"]
                token = store.data["token"]
                if not _hb_done["ok"]:
                    print(f"[SESSION-LOOP] active · identity="
                          f"{store.data.get('handle')!r} · token=yes · "
                          f"listening for agent.turn/stop", file=_sys.stderr)
                    _hb_done["ok"] = True
                if token != last_token:
                    fed.ws_configure(server_url, token, on_event=_ws_event)
                    last_token = token
                _now = _time.time()
                try:
                    _ws_up = fed.ws_is_up() is True
                except Exception:
                    _ws_up = False
                _due = _catchup_due(_ws_up, _hb_done.get("prev_ws"),
                                    _hb_done.get("last_catchup", 0.0), _now)
                _hb_done["prev_ws"] = _ws_up
                _envs = []
                if _due:
                    with fed.sync_pass():
                        resp = fed.server_fetch_inbox_types(
                            server_url, token,
                            [fed_session.T_TURN, fed_session.T_STOP],
                            peek=True, prefer_rest=True)
                        _hb_done["last_catchup"] = _time.time()
                        _envs = _mine_by_device(resp.get("envelopes", []))
                        if _envs:
                            try:
                                fed.server_ack_envelopes(
                                    server_url, token,
                                    [e.get("id") for e in _envs if e.get("id")])
                            except Exception as _ae:
                                print(f"[SESSION-LOOP] ack failed: {_ae}",
                                      file=_sys.stderr)
                            print(f"[SESSION-LOOP] catch-up picked up {len(_envs)} "
                                  f"session envelope(s)", file=_sys.stderr)
                        for env in _envs:
                            _dispatch_session_envelope(env)
                        _pending = fed.pass_inbox_types(server_url, token)

                        def _due_for(*kinds, prefix=None):
                            if _pending is None:
                                return True
                            return bool(_pending & set(kinds)) or bool(
                                prefix and any(str(x).startswith(prefix)
                                               for x in _pending))
                        try:
                            if _due_for("project_task", "project_tomb",
                                        "project_del", "project_board"):
                                tool_fed_project_pull()
                            if _due_for("folder_note", "folder_tomb",
                                        "folder_del"):
                                tool_fed_folder_pull()
                            if _due_for("calendar_event"):
                                _auto_accept_appointments()
                        except Exception:
                            pass
                        if _due_for(prefix="code_"):
                            _code_rails_async()
                if _ws_up != _hb_done.get("tick_ws") \
                        or _now - _hb_done.get("last_tick", 0) > 900:
                    _hb_done["tick_ws"] = _ws_up
                    _ago = (int(_now - _hb_done.get("last_catchup", _now))
                            if _hb_done.get("last_catchup") else 0)
                    print(f"[SESSION-LOOP] tick · ws="
                          f"{'up' if _ws_up else 'down'} · last catch-up "
                          f"{_ago}s ago", file=_sys.stderr)
                    _hb_done["last_tick"] = _now
            else:
                if not _hb_done["noid"]:
                    print("[SESSION-LOOP] NO identity/token — not serving "
                          "incoming sessions (federation not logged in?)",
                          file=_sys.stderr)
                    _hb_done["noid"] = True
            if _fail["n"]:
                print(f"[SESSION-LOOP] recovered after {_fail['n']} failed "
                      f"poll(s)", file=_sys.stderr)
            _fail.update(n=0, last="", said=0.0)
        except Exception as e:
            _fail["n"] += 1
            _msg = str(e)
            if _msg != _fail["last"] or _time.time() - _fail["said"] > 60:
                print(f"[FED-SESSION] error"
                      + (f" (x{_fail['n']} seguidos)" if _fail["n"] > 1 else "")
                      + f": {_msg}")
                _fail["last"], _fail["said"] = _msg, _time.time()
        if _session_stop:
            _wait = interval if not _fail["n"] else min(
                60, interval * (2 ** min(_fail["n"], 6)))
            _session_stop.wait(_wait)


def start_session_engine(interval=None):
    global _session_thread, _session_stop, _session_interval
    if interval:
        _session_interval = interval
    if _session_thread and _session_thread.is_alive():
        return {"ok": True, "message": "The session poller was already running."}
    _session_stop = _threading.Event()
    _session_thread = _threading.Thread(
        target=_session_loop, args=(_session_interval,),
        daemon=True, name="fed-session-poller")
    _session_thread.start()
    return {"ok": True, "interval": _session_interval,
            "message": f"Incoming session poller started (every {_session_interval}s)."}


def stop_session_engine():
    global _session_stop
    if _session_stop:
        _session_stop.set()
    return {"ok": True, "message": "Session poller stopped."}


def tool_fed_sync_status():
    s = dict(_sync_last_status)
    s["interval"] = _sync_interval
    s["thread_alive"] = bool(_sync_thread and _sync_thread.is_alive())
    if s.get("last_run"):
        s["seconds_since_last_run"] = round(_time.time() - s["last_run"], 1)
    return s


def tool_fed_delete_note_guarded(category, note_uuid):
    notes = _notes()
    if notes is None:
        return {"error": "Notes not available."}
    st = _store()
    my_handle = st.data.get("handle") if st else ""
    it = {i["uuid"]: i for i in notes.fed_items(category)}.get(note_uuid)
    if it is None:
        return {"error": "Note not found."}
    shared = False
    try:
        _, server_url, token = _require_session()
        fid, _peer = _find_shared_folder(server_url, token, category, kind="folder")
        shared = bool(fid)
    except Exception:
        shared = False
    if shared:
        notes.fed_upsert(category, fs.tombstoned(it, my_handle))
        _autopush_schedule("folder", category)
        return {"ok": True, "mode": "tombstone"}
    bucket = notes.data.get("categories", {}).get(category, [])
    nid = next((n.get("id") for n in bucket if n.get("uuid") == note_uuid), None)
    if nid is not None and hasattr(notes, "delete_note"):
        notes.delete_note(category, nid)
    return {"ok": True, "mode": "physical"}


def tool_fed_clear_category_guarded(category):
    notes = _notes()
    if notes is None:
        return {"error": "Notes not available."}
    bucket = notes.data.get("categories", {}).get(category, [])
    if not bucket:
        return {"ok": True, "cleared": 0, "mode": "empty"}
    shared = False
    try:
        _, server_url, token = _require_session()
        fid, _peer = _find_shared_folder(server_url, token, category, kind="folder")
        shared = bool(fid)
    except Exception:
        shared = False
    if shared:
        return {"ok": False, "shared": True, "mode": "needs_per_note"}
    n = notes.clear_category(category)
    return {"ok": True, "cleared": n, "mode": "physical"}


def tool_fed_delete_task_guarded(project, task_uuid):
    todo = _todo()
    if todo is None:
        return {"error": "TODO/projects not available."}
    st = _store()
    my_handle = st.data.get("handle") if st else ""
    it = {i["uuid"]: i for i in todo.fed_items(project)}.get(task_uuid)
    if it is None:
        return {"error": "Task not found."}
    shared = False
    try:
        _, server_url, token = _require_session()
        fid, _peer = _find_shared_folder(server_url, token, project, kind="project")
        shared = bool(fid)
    except Exception:
        shared = False
    if shared:
        todo.fed_upsert(project, fs.tombstoned(it, my_handle))
        _autopush_schedule("project", project)
        return {"ok": True, "mode": "tombstone"}
    tid = next((t.get("id") for t in todo.data.get("tasks", []) if t.get("uuid") == task_uuid), None)
    if tid is not None and hasattr(todo, "delete_task"):
        todo.delete_task(tid)
    return {"ok": True, "mode": "physical"}


def tool_fed_debug_notes(category):
    notes = _notes()
    if notes is None:
        return {"error": "Notes not available."}
    out = [{"uuid": i.get("uuid"), "rev": i.get("rev"),
            "deleted": i.get("deleted"), "editor": i.get("editor", ""),
            "title": (i.get("payload") or {}).get("title", "")}
           for i in notes.fed_items(category)]
    return {"ok": True, "category": category, "count": len(out), "notes": out}


def tool_fed_is_shared_folder(folder_name):
    if _is_local_only(folder_name):
        return {"shared": False}
    try:
        _, server_url, token = _require_session()
    except Exception:
        return {"shared": False}
    fid, peer = _find_accepted_folder(server_url, token, folder_name)
    return {"shared": bool(fid), "with": peer, "folder_id": fid}


def tool_fed_is_shared_project(project_name):
    if _is_local_only(project_name):
        return {"shared": False}
    try:
        _, server_url, token = _require_session()
    except Exception:
        return {"shared": False}
    links = _find_shared_peers(server_url, token, project_name, "project")
    return {"shared": bool(links), "with": (links[0][1] if links else None),
            "folder_id": (links[0][0] if links else None)}


def tool_fed_save_note_guarded(category, note_uuid, title, content, base_rev):
    notes = _notes()
    if notes is None:
        return {"error": "Notes not available."}
    st = _store()
    my_handle = st.data.get("handle") if st else ""
    r = fa.save_from_gui(notes, category, note_uuid, int(base_rev),
                         {"title": title, "content": content},
                         editor=my_handle, uuid_gen=_new_fed_uuid)
    _autopush_schedule("folder", category)
    if not r.get("conflict"):
        return {"ok": True, "conflict": False}
    return {"ok": True, "conflict": True,
            "saved_as_local": f"{title}{fs.LOCAL_SUFFIX}",
            "warning": (f"⚠ '{title}' changed while you were editing it (a new "
                        f"version arrived via sync). Your edit was saved as "
                        f"'{title}{fs.LOCAL_SUFFIX}'. The note keeps the version "
                        f"that arrived; review and merge by hand.")}


def tool_fed_save_task_guarded(project, task_uuid, payload, base_rev):
    todo = _todo()
    if todo is None:
        return {"error": "TODO/projects not available."}
    st = _store()
    my_handle = st.data.get("handle") if st else ""
    cur = None
    for it in todo.fed_items(project):
        if it.get("uuid") == task_uuid:
            cur = dict(it.get("payload", {})); break
    merged = cur or {}
    merged.update({k: v for k, v in (payload or {}).items() if v is not None})
    r = fa.save_from_gui(todo, project, task_uuid, int(base_rev), merged,
                         editor=my_handle, uuid_gen=_new_fed_uuid)
    _autopush_schedule("project", project)
    title = merged.get("title", "")
    if not r.get("conflict"):
        return {"ok": True, "conflict": False}
    return {"ok": True, "conflict": True,
            "saved_as_local": f"{title}{fs.LOCAL_SUFFIX}",
            "warning": (f"⚠ '{title}' changed while you were editing it. Your edit "
                        f"was saved as '{title}{fs.LOCAL_SUFFIX}'; the task keeps "
                        f"what arrived. Review and merge by hand.")}


FED_TOOLS = {
    "fed_register": {
        "desc": "Register a new federated identity on an HOPLITA federation "
                "server. Generates cryptographic keys (the private one is stored "
                "encrypted in your vault, never leaves your machine), and sends "
                "only the public key to the server. NO password needed: you "
                "authenticate with your key. After registering, verify the email "
                "with fed_verify. Use it the FIRST time you want to join a federation.",
        "fn": lambda a: tool_fed_register(a["handle"], a["email"],
                                          a["server_url"],
                                          a.get("invite", ""))},
    "fed_verify": {
        "desc": "Verify your federated identity's email with the 6-digit PIN "
                "you received after fed_register.",
        "fn": lambda a: tool_fed_verify(a["pin"])},
    "fed_login": {
        "desc": "Log in to the federation server. NO password required: it "
                "authenticates with your private key via a cryptographic "
                "challenge. Gets a session token. Do it after verifying, or when "
                "your session has expired.",
        "fn": lambda a: tool_fed_login()},
    "fed_status": {
        "desc": "Show your federated identity's status: your handle, your "
                "cryptographic fingerprint, the server, and whether you have an "
                "active session. Does NOT expose your private key. Use it to "
                "check whether you are registered/logged in to the federation.",
        "fn": lambda a: tool_fed_status()},
    "fed_add_contact": {
        "desc": "Request contact with another federation member by their handle. "
                "The trust level (trust_level) defines what they can do if they "
                "delegate tasks to you: 'open' (executes without asking), "
                "'accept' (you ask for confirmation first), 'analyze' (your AI "
                "pre-filters). Defaults to 'accept'. The contact must accept for "
                "it to be mutual.",
        "fn": lambda a: tool_fed_add_contact(a["handle"], a.get("trust_level", "accept"))},
    "fed_accept_contact": {
        "desc": "Accept an incoming contact request from another handle. "
                "Optionally sets the trust_level you grant them.",
        "fn": lambda a: tool_fed_accept_contact(a["handle"], a.get("trust_level"))},
    "fed_list_contacts": {
        "desc": "List your federation contacts with their status "
                "(pending/accepted), trust level, and whether they are online or "
                "offline right now. Use it to see who you can share with or "
                "delegate to, and who is connected.",
        "fn": lambda a: tool_fed_list_contacts()},
    "fed_spawn_child": {
        "desc": "Generate an enrollment token to create a CHILD: a headless "
                "agent of yours that runs on another machine (a server without a "
                "desktop) or in a container. Returns a single-use token. The "
                "child self-enrolls, appears in your children list and is fully trusted "
                "(free rein, it's yours). Optional: 'label' to recognize it "
                "(e.g. 'kitchen-pc'). The token also carries the child's starter "
                "kit, sealed so the server cannot read it: the model it thinks "
                "with ('model', a pool alias, empty for the base; or a model of "
                "its OWN with base_url + model_id + api_key, the safer choice "
                "for a child acting on its own, since it holds and can spend "
                "every key it gets) and, with search=true, this machine's web "
                "search. The answer includes two commands that boot it with "
                "nothing typed in: one for a container, one for any machine "
                "with HOPLITA installed; launch=true builds the image if "
                "needed and starts that container on THIS machine, for jobs "
                "you want boxed away from it (an untrusted MCP, media work).",
        "fn": lambda a: tool_fed_spawn_child(
            a.get("label", ""), a.get("handle", ""), a.get("model", ""),
            a.get("base_url", ""), a.get("model_id", ""), a.get("api_key", ""),
            bool(a.get("search")), bool(a.get("launch")))},
    "fed_list_children": {
        "desc": "List your children (headless agents you have enrolled) with "
                "their online/offline presence. Use it to see which machines you "
                "have running and which you can send tasks to.",
        "fn": lambda a: tool_fed_list_children()},
    "fed_ask_agent": {
        "desc": "Ask a REMOTE agent for help: a child of yours or a friend's "
                "(contact's) agent. The task runs on the remote's MACHINE with "
                "THEIR LLM and THEIR tools, and returns the result to you. Use it "
                "to delegate work to a child (e.g. check a server) or to leverage "
                "a friend's agent. If it's a friend, they may have to approve the "
                "task depending on the trust they give you. Pass 'handle' (the "
                "child/friend) and 'message' (the task).",
        "fn": lambda a: tool_fed_ask_agent(a["handle"], a["message"],
                                           a.get("max_iterations"),
                                           a.get("device", ""),
                                           a.get("tab", ""))},
    "device_info": {
        "desc": "Which MACHINE this install is (id + name). A tab tied to this "
                "id runs its turns HERE, whichever machine the user is typing "
                "from. Their data follows them between computers; a machine "
                "does not — a mounted disk, a running service, a folder, a "
                "network only this box can reach.",
        "fn": lambda a: tool_device_info(a)},
    "device_rename": {
        "desc": "Name this machine so it is recognisable from the others "
                "('home', 'laptop'). Local only, never synced.",
        "fn": lambda a: tool_device_rename(a)},
    "device_regenerate": {
        "desc": "Give this machine a NEW id. Use it when a second computer was "
                "set up by copying the folder and both now answer to the same "
                "one — a tab pinned to 'home' would then run in whichever "
                "picked up the envelope first.",
        "fn": lambda a: tool_device_regenerate(a)},
    "fed_remove_contact": {
        "desc": "Remove a contact (BILATERAL removal: you both stop being "
                "contacts and the pending sends between you are discarded). Use "
                "it to break with a contact or to clean up. Irreversible.",
        "fn": lambda a: tool_fed_remove_contact(a["handle"])},
    "fed_revoke_child": {
        "desc": "Remove a CHILD entirely: invalidates its session instantly, "
                "deletes its identity and the contact. Use it if a child is "
                "compromised or you no longer need it. Your own children only. "
                "Irreversible.",
        "fn": lambda a: tool_fed_revoke_child(a["handle"])},
    "fed_leave": {
        "desc": "LEAVE the federation entirely: deregisters you from the server "
                "(deletes your identity, contacts, envelopes and revokes your "
                "children) and deletes your local identity. Use it to leave for "
                "good or before migrating to ANOTHER server (then you can "
                "register again). If the server is down, pass force_local=true to "
                "delete at least your local identity. Irreversible.",
        "fn": lambda a: tool_fed_leave(a.get("force_local", False))},
    "fed_share_appointment": {
        "desc": "Send an APPOINTMENT to a contact (someone you're already a "
                "contact with), end-to-end encrypted. It does not go straight "
                "into their calendar: it stays in their incoming appointments "
                "until they accept it, and only if they let you add them "
                "appointments (can_add). Pass 'handle', 'title', 'date' (e.g. "
                "'2026-07-20 18:00') and optionally 'description' and 'urgency'. "
                "Use it when the user wants to share/send an appointment or event "
                "to someone.",
        "fn": lambda a: tool_fed_share_appointment(
            a["handle"], a["title"], a["date"],
            a.get("description", ""), a.get("urgency", "Normal"))},
    "fed_list_incoming_appointments": {
        "desc": "List the APPOINTMENTS other contacts have sent you and you "
                "haven't yet added to your calendar. Review them and accept them "
                "with fed_accept_appointment.",
        "fn": lambda a: tool_fed_list_incoming_appointments()},
    "fed_list_appointment_bounces": {
        "desc": "'No permission' bounces for appointments you sent to contacts "
                "who have you at 'no'. Internal GUI use (consumes them on read).",
        "fn": lambda a: tool_fed_list_appointment_bounces()},
    "fed_accept_appointment": {
        "desc": "Accept an incoming APPOINTMENT and add it to your local "
                "calendar. Optional: 'title' and/or 'from_handle' to choose which "
                "one if there are several. Respects the can_add permission you "
                "have for the sender.",
        "fn": lambda a: tool_fed_accept_appointment(
            a.get("title"), a.get("from_handle"))},
    "fed_share_skill": {
        "desc": "Share a skill of YOURS with a contact, end-to-end encrypted. It "
                "does not land directly on their machine: it stays in their "
                "pending tray until they accept it (so nobody puts skills into "
                "someone else's agent without their OK). Pass the skill's 'title' "
                "and 'content'.",
        "fn": lambda a: tool_fed_share_skill(a["handle"], a["title"], a["content"])},
    "fed_list_shared_skills": {
        "desc": "List the skills your contacts have shared with you that are "
                "PENDING acceptance (your agent doesn't see them yet). Review "
                "them and accept the ones you want with fed_accept_skill.",
        "fn": lambda a: tool_fed_list_shared_skills()},
    "fed_accept_skill": {
        "desc": "Accept a shared skill (from the pending tray) and save it in "
                "your 'shared-skills' folder, kept separate from your own skills. "
                "It keeps the @origin of whoever sent it and becomes searchable "
                "by your agent. 'envelope_index': which one to accept (0 = the "
                "first in the fed_list_shared_skills list).",
        "fn": lambda a: tool_fed_accept_skill(a.get("envelope_index", 0))},
    "fed_share": {
        "desc": "Share a resource with a federation contact, end-to-end "
                "ENCRYPTED. The content is encrypted with the recipient's public "
                "key: the server only sees a blob, never the content. Pass "
                "'resource_type' (e.g. 'note') and 'payload' (the content as an "
                "object).",
        "fn": lambda a: tool_fed_share(a["handle"], a["resource_type"], a["payload"])},
    "fed_inbox": {
        "desc": "Collect the resources others have shared with you and decrypt "
                "them with your private key. Optionally filter by type "
                "(resource_type, e.g. 'note'). Returns the contents in clear "
                "(only for you; they traveled encrypted).",
        "fn": lambda a: tool_fed_inbox(a.get("resource_type"))},
    "fed_set_permissions": {
        "desc": "Configure granular permissions over a contact for a resource "
                "type. Typical example (calendar): give someone permission to add "
                "you appointments but only see that you're busy, not the details: "
                "resource_type='calendar', perms={'can_add': true, 'visibility': "
                "'busy'}. visibility can be 'none', 'busy' or 'full'. The "
                "permissions merge with the existing ones.",
        "fn": lambda a: tool_fed_set_permissions(a["handle"], a["resource_type"], a["perms"])},
    "fed_share_note": {
        "desc": "Share a REAL NOTE from your HOPLITA notes with a contact. You give "
                "it the category and title of a note you already have; HOPLITA "
                "looks it up, encrypts it with the recipient's key and sends it. "
                "On receipt, the contact can save it in their notes.",
        "fn": lambda a: tool_fed_share_note(a["handle"], a["category"], a["title"])},
    "fed_accept_note": {
        "desc": "Save into YOUR notes a note that was shared with you and is in "
                "your federation inbox. By default it saves the first; optionally "
                "give envelope_index for another. The note lands in its original "
                "category with the origin noted in the title.",
        "fn": lambda a: tool_fed_accept_note(a.get("envelope_index", 0))},
    "fed_shared_overview": {
        "desc": "Complete view of the federated data in one call: contacts + role/members of each shared folder and project. For menus/panels.",
        "params": {},
        "required": [],
        "fn": lambda a: tool_fed_shared_overview()},
    "fed_folder_info": {
        "desc": "Sharing info for a note folder: if you're owner, who it's shared with (members and statuses); if you're a member, who the owner is.",
        "params": {"folder_name": {"type": "string"}},
        "required": ["folder_name"],
        "fn": lambda a: tool_fed_folder_info(a["folder_name"])},
    "fed_project_info": {
        "desc": "Sharing info for a project: owner → members and statuses; member → owner.",
        "params": {"project_name": {"type": "string"}},
        "required": ["project_name"],
        "fn": lambda a: tool_fed_project_info(a["project_name"])},
    "fed_delete_shared_folder": {
        "desc": "OWNER ONLY: delete a shared folder FOR ALL members (like the owner in Dropbox). Members lose their copy; links broken.",
        "params": {"folder_name": {"type": "string"}},
        "required": ["folder_name"],
        "fn": lambda a: tool_fed_delete_shared("folder", a["folder_name"])},
    "fed_delete_shared_project": {
        "desc": "OWNER ONLY: delete a shared project FOR ALL members.",
        "params": {"project_name": {"type": "string"}},
        "required": ["project_name"],
        "fn": lambda a: tool_fed_delete_shared("project", a["project_name"])},
    "fed_unshare_folder": {
        "desc": "Unlink a shared note folder (from either side, even if the other no longer exists). Your local copy stays; it stops syncing and purges pending envelopes.",
        "params": {"folder_name": {"type": "string"}, "peer": {"type": "string", "description": "Owner only: remove THIS member (the rest continue)."}},
        "required": ["folder_name"],
        "fn": lambda a: tool_fed_unshare_folder(a["folder_name"], a.get("peer"))},
    "fed_unshare_project": {
        "desc": "Unlink a shared project (from either side). Your local copy stays; it stops syncing and purges pending envelopes.",
        "params": {"project_name": {"type": "string"}, "peer": {"type": "string", "description": "Owner only: remove THIS member."}},
        "required": ["project_name"],
        "fn": lambda a: tool_fed_unshare_project(a["project_name"], a.get("peer"))},
    "fed_share_folder": {
        "desc": "Propose sharing a FOLDER with a contact. In the federation the "
                "unit of sharing is ALWAYS the folder, never standalone files. It "
                "stays pending until the contact accepts it; once accepted, the "
                "folder is a live shared space where both put in and take out "
                "content. Give it the folder name.",
        "fn": lambda a: tool_fed_share_folder(a["handle"], a["folder_name"])},
    "fed_share_code": {
        "desc": "Invite a contact to CO-WORK on a code project: sends the whole "
                "current branch as an E2E-sealed git bundle (the server relays "
                "opaque chunks) and shares the project's memory folder and task "
                "project alongside. They choose THEIR path with fed_accept_code. "
                "After that, commits flow both ways into quarantine refs — "
                "nobody's trunk moves without their own hand.",
        "fn": lambda a: tool_fed_share_code(a["path"], a["handle"])},
    "fed_accept_code": {
        "desc": "Accept a code co-work invite: materializes the project at a "
                "path the USER chose (git clone from the sealed bundle), then "
                "accepts the memory folder and task project that came with it. "
                "Their future commits land in refs/hoplita/incoming/<peer>/…, "
                "never on your trunk. `dest_path` IS THE USER'S TO CHOOSE: ASK "
                "THEM, and never invent one — a repo landing in a folder they "
                "did not pick is a repo they will not find, and on Windows or "
                "macOS a made-up '~/code/...' is a guess about somebody else's "
                "machine. The answer always reports the ABSOLUTE path where it "
                "landed.",
        "fn": lambda a: tool_fed_accept_code(a["handle"], a["project"],
                                             a["dest_path"])},
    "fed_code_discard": {
        "desc": "Throw away a code transfer stuck half-arrived and GET THE "
                "MAILBOX QUOTA BACK. A multi-part invite that died mid-flight "
                "leaves its parts in the inbox forever: it can never be "
                "accepted, and its megabytes keep the quota full so the next "
                "transfer fails too. Nothing recoverable is lost — an "
                "incomplete bundle is unusable by anybody.",
        "fn": lambda a: tool_fed_code_discard(a.get("project"), a.get("handle"))},
    "fed_code_unshare": {
        "desc": "Stop co-working on a code project: one peer (`peer`) or all of "
                "them. They stop receiving your commits and their packs stop "
                "being applied here. Their existing CLONE stays theirs — code "
                "cannot be un-given, and this tool does not pretend otherwise.",
        "fn": lambda a: tool_fed_code_unshare(a.get("path"), a.get("project"),
                                              a.get("peer"))},
    "fed_code_peers": {
        "desc": "Who a code project is already shared WITH (and the last commit "
                "each of them got). The counterpart of fed_code_pending, which "
                "only says what is coming IN.",
        "fn": lambda a: tool_fed_code_peers(a.get("path"), a.get("project"))},
    "fed_code_retract": {
        "desc": "Take back MY code parts still in flight to a peer who never "
                "collected them, and free MY mail-in-flight quota. The outgoing "
                "half of fed_code_discard: use it when sharing code is refused "
                "for lack of room, or when a transfer to somebody died half-way. "
                "The next push to them rebuilds the branch whole.",
        "fn": lambda a: tool_fed_code_retract(a.get("handle"))},
    "fed_code_pending": {
        "desc": "Code co-work invites waiting in MY inbox: who, which project, "
                "and whether all sealed parts have arrived. Answer them with "
                "fed_accept_code.",
        "fn": lambda a: tool_fed_code_pending()},
    "fed_code_push": {
        "desc": "Send my new commits (current branch) to every code peer of the "
                "project as sealed incremental bundles. Runs by itself after "
                "git_commit — call it by hand after committing from a terminal.",
        "fn": lambda a: tool_fed_code_push(a.get("path"), a.get("project"))},
    "fed_code_state": {
        "desc": "WHERE a code share actually is, in one call: what is in the mailbox by type, what was offered, what was accepted and is waiting to be served or received, and the path/peers recorded per project. For when the screen says nothing and the folder is empty.",
        "fn": lambda a: tool_fed_code_state()},
    "fed_code_incoming": {
        "desc": "A peer's commits waiting in QUARANTINE: per peer and branch, "
                "the commits, the files, fast-forward or merge.",
        "fn": lambda a: tool_fed_code_incoming(a.get("path"), a.get("project"))},
    "fed_code_take": {
        "desc": "Integrate a peer's quarantined branch into the current one "
                "(fast-forward or merge; conflicts aborted and named; dirty "
                "tree refused). Only when the user says so.",
        "fn": lambda a: tool_fed_code_take(a.get("path"), a.get("project"),
                                           a.get("peer", ""),
                                           a.get("branch", "main"))},
    "fed_code_pull": {
        "desc": "Apply every complete incoming code pack, QUARANTINE only: each "
                "lands in refs/hoplita/incoming/<peer>/<branch> and the project "
                "journal narrates what arrived; my trunk does not move. The "
                "background engine already does this — by hand it answers 'did "
                "anything arrive?'.",
        "fn": lambda a: tool_fed_code_pull()},
    "fed_share_project": {
        "desc": "Share a TODO PROJECT (with its tasks) with a contact, IN ONE "
                "STEP: creates the link and sends the encrypted tasks. The other "
                "party accepts it and the tasks appear in their TODO. Use it when "
                "the user wants to share/send a project or its tasks to someone. "
                "Pass 'project_name' and 'handle'. (Appointments go with "
                "fed_share_appointment.)",
        "fn": lambda a: tool_fed_share_project(a["project_name"], a["handle"])},
    "fed_list_incoming_projects": {
        "desc": "List the PROJECTS other contacts have shared with you that are "
                "PENDING acceptance (they arrived but aren't in your TODO yet). "
                "Use it when the user asks whether they have pending projects or "
                "wants to see what's been shared with them. Accept them with "
                "fed_accept_project.",
        "fn": lambda a: tool_fed_list_incoming_projects()},
    "fed_list_shared_projects": {
        "desc": "List ALL shared projects you participate in: those YOU shared "
                "and those shared with you, with status and with whom. Use it to "
                "answer whether a project is shared with someone.",
        "fn": lambda a: tool_fed_list_shared_projects()},
    "fed_accept_project": {
        "desc": "Accept a PROJECT shared with you and bring its tasks into your "
                "TODO. Use it when the user wants to accept an incoming project. "
                "Optional: 'project_name' and/or 'handle' to choose if there are "
                "several.",
        "fn": lambda a: tool_fed_accept_project(a.get("project_name"), a.get("handle"))},
    "fed_accept_folder": {
        "desc": "Accept a NOTE FOLDER someone proposed sharing with you (NOT "
                "projects: those go with fed_accept_project). Give the handle of "
                "whoever shared it with you and optionally the folder name (if "
                "you have several pending from that person). After accepting, the "
                "folder is a live shared space.",
        "fn": lambda a: tool_fed_accept_folder(a["handle"], a.get("folder_name"), a.get("folder_id"))},
    "fed_list_folders": {
        "desc": "List the shared NOTE FOLDERS you participate in, with status and "
                "with whom. ONLY note folders — for TODO PROJECTS use "
                "fed_list_incoming_projects / fed_share_project.",
        "fn": lambda a: tool_fed_list_folders()},
    "fed_publish_folder": {
        "desc": "Make one of your note folders PUBLIC: anyone on the server can "
                "discover it by your handle and subscribe read-only. Your pushes "
                "reach every subscriber; nobody can push back. Different from "
                "fed_share_folder, which is a private two-way link with ONE "
                "contact.",
        "fn": lambda a: tool_fed_publish_folder(a["folder_name"])},
    "fed_unpublish_folder": {
        "desc": "Withdraw a folder from your public catalog and drop its "
                "subscribers.",
        "fn": lambda a: tool_fed_unpublish_folder(a["folder_name"])},
    "fed_public_folders": {
        "desc": "What public folders does a handle offer? Names, subscriber "
                "counts, and whether you are already subscribed. Use it when the "
                "user asks what someone shares publicly.",
        "fn": lambda a: tool_fed_public_folders(a["handle"])},
    "fed_subscribe_folder": {
        "desc": "Subscribe READ-ONLY to someone's public folder (no handshake). "
                "Their updates arrive with their pushes; you cannot write back. "
                "Pass handle and folder_name (or folder_id).",
        "fn": lambda a: tool_fed_subscribe_folder(
            a["handle"], a.get("folder_name"), a.get("folder_id"))},
    "fed_unsubscribe_folder": {
        "desc": "Drop your read-only subscription to a public folder. Your "
                "local copy stays yours.",
        "fn": lambda a: tool_fed_unsubscribe_folder(
            a["handle"], a.get("folder_name"), a.get("folder_id"))},
    "fed_activity": {
        "desc": "What each CONTACT has done to the things you share with them: "
                "commits on a shared project, notes and tasks added, edited or "
                "deleted in a shared folder/project. Answers 'what has this contact been "
                "up to on our stuff'. Returns the individual events (newest "
                "first) and a ready grouping by contact + resource + day, which "
                "is what reads like a timeline ('alice · budget · 4 "
                "commits'). Only records changes that actually arrived here, so "
                "it never reveals anything they did not share.",
        "fn": lambda a: tool_fed_activity(a.get("limit", 200))},
    "fed_activity_clear": {
        "desc": "Erase the contact activity log: everything, or just one "
                "contact's if you pass 'peer'. It only drops the LOG — the "
                "notes, tasks and commits themselves are untouched.",
        "fn": lambda a: tool_fed_activity_clear(a.get("peer"))},
    "fed_debug_notes": {
        "desc": "DEBUG: dump uuid/rev/deleted/title of all notes (incl. deleted) "
                "in a category, to diagnose sync/deletion.",
        "fn": lambda a: tool_fed_debug_notes(a["category"])},
    "fed_folder_push": {
        "desc": "Sync OUTWARD: send the notes of a shared folder (a note "
                "category) to the contact you share it with. Each note travels "
                "encrypted. Use it when you want the other party to receive your "
                "notes from that folder. The folder must already be shared and "
                "accepted.",
        "fn": lambda a: tool_fed_folder_push(a["folder_name"])},
    "fed_folder_pull": {
        "desc": "Sync INWARD: collect the notes shared with you in folders and "
                "save them into your categories. If an arriving note collides "
                "with one of yours that you had modified, your version is "
                "preserved as '<title> (local)' so you can review and merge it by "
                "hand — your work is never destroyed. Optionally give folder_name "
                "to collect only that folder.",
        "fn": lambda a: tool_fed_folder_pull(a.get("folder_name"))},
    "fed_sync_status": {
        "desc": "Show the status of the automatic folder synchronization engine "
                "(which runs in the background keeping your shared folders "
                "alive): whether it's active, when the last pass was, and how "
                "many folders it syncs. The sync is automatic, no need to launch "
                "it by hand.",
        "fn": lambda a: tool_fed_sync_status()},
    "fed_project_push": {
        "desc": "Sync OUTWARD the TASKS of a shared project: send each task "
                "(with its progress) to the contact you share the project with. "
                "Appointments are NOT sent (the calendar is personal, goes "
                "separately). The project must be shared and accepted. Normally "
                "the automatic sync already does it.",
        "fn": lambda a: tool_fed_project_push(a["project_name"])},
    "fed_project_pull": {
        "desc": "Sync INWARD the TASKS shared with you in projects, saving them "
                "in your task list with their progress. If an arriving task "
                "collides with a modified one of yours, your version is preserved "
                "as '(local)'. Normally the automatic sync already does it; use "
                "it if you want to force a collection.",
        "fn": lambda a: tool_fed_project_pull(a.get("project_name"))},
    "fed_is_shared_folder": {
        "desc": "Indicate whether a note folder/category is shared and accepted. "
                "Internal GUI use to decide whether to apply conflict protection "
                "on save.",
        "fn": lambda a: tool_fed_is_shared_folder(a["folder_name"])},
    "fed_is_shared_project": {
        "desc": "Indicate whether a project is shared and accepted. Internal GUI "
                "use for the protected saving of tasks.",
        "fn": lambda a: tool_fed_is_shared_project(a["project_name"])},
    "fed_save_note_guarded": {
        "desc": "Save a note from a shared folder protecting against conflicts: "
                "if while you were editing another version arrived by sync, your "
                "edit is saved as '(local)' and not lost. Internal GUI use when "
                "saving notes from shared folders.",
        "fn": lambda a: tool_fed_save_note_guarded(a["category"], a["note_uuid"],
                                                   a["title"], a["content"],
                                                   a.get("base_rev", 1))},
    "fed_save_task_guarded": {
        "desc": "Save a task from a shared project with a collision guard (by "
                "logical version). Internal GUI use.",
        "fn": lambda a: tool_fed_save_task_guarded(a["project"], a["task_uuid"],
                                                   a.get("payload", {}),
                                                   a.get("base_rev", 1))},
    "fed_delete_note_guarded": {
        "desc": "Delete (propagable tombstone) a note from a shared folder.",
        "fn": lambda a: tool_fed_delete_note_guarded(a["category"], a["note_uuid"])},
    "fed_clear_category_guarded": {
        "desc": "Clear ALL notes of a category in one pass (one shared-check + one save) instead of N per-note deletes.",
        "fn": lambda a: tool_fed_clear_category_guarded(a["category"])},
    "fed_sync_push": {
        "desc": "Push a full ENCRYPTED snapshot of your data to your server (sealed to your own key; the server can't read it). Returns needs_pull if the server has a newer snapshot.",
        "fn": lambda a: tool_fed_sync_push()},
    "fed_sync_breakdown": {
        "desc": "What YOUR device snapshot weighs, store by store (rows and bytes, fattest first, plus the 12 heaviest rows). The server only sees a sealed blob; this is how «why is my sync heavy» gets answered — with numbers, before pruning anything.",
        "fn": lambda a: tool_fed_sync_breakdown()},
    "fed_sync_pull": {
        "desc": "Pull your encrypted snapshot from the server and merge it into this device (newer-wins per item; nothing deleted blindly).",
        "fn": lambda a: tool_fed_sync_pull()},
    "fed_motd": {
        "desc": "Get the operator's current message-of-the-day (maintenance/downtime notices).",
        "fn": lambda a: tool_fed_motd()},
    "fed_notices": {
        "desc": "Get server→client notices for you (broadcast + targeted, with offline catch-up).",
        "fn": lambda a: tool_fed_notices()},
    "fed_quota": {
        "desc": "Show your server plan: tier, limits and current usage (sync bytes, mailbox, contacts, children, rate).",
        "fn": lambda a: tool_fed_quota()},
    "fed_delete_task_guarded": {
        "desc": "Delete (propagable tombstone) a task from a shared project.",
        "fn": lambda a: tool_fed_delete_task_guarded(a["project"], a["task_uuid"])},
}
FED_TOOLS.update(hoplita_children.CHILD_TOOLS)

FED_TOOL_PARAMS = {
    "fed_register": {
        "properties": {
            "handle": {"type": "string", "description": "Your unique identifier "
                       "in the federation, e.g. 'alice@example.org' or 'alice'."},
            "email": {"type": "string", "description": "Email for verification."},
            "server_url": {"type": "string", "description": "URL of the "
                           "federation server, e.g. 'https://hoplita.net'."},
            "invite": {"type": "string", "description": "Invitation code, if "
                       "the server doesn't allow open registration (the usual "
                       "case once it has a public domain)."},
        },
        "required": ["handle", "email", "server_url"]},
    "fed_verify": {
        "properties": {"pin": {"type": "string", "description": "The 6-digit PIN "
                       "received by email after registration."}},
        "required": ["pin"]},
    "fed_login": {
        "properties": {},
        "required": []},
    "fed_status": {"properties": {}, "required": []},
    "fed_add_contact": {
        "properties": {
            "handle": {"type": "string", "description": "The handle of the "
                       "contact to add, e.g. 'alice@example.org'."},
            "trust_level": {"type": "string", "description": "Trust level: "
                            "'open', 'accept' or 'analyze'. Defaults to 'accept'."},
        },
        "required": ["handle"]},
    "fed_accept_contact": {
        "properties": {
            "handle": {"type": "string", "description": "The handle of whoever "
                       "sent you the contact request."},
            "trust_level": {"type": "string", "description": "Optional: trust "
                            "level you grant them."},
        },
        "required": ["handle"]},
    "fed_list_contacts": {"properties": {}, "required": []},
    "fed_spawn_child": {
        "properties": {
            "label": {"type": "string", "description": "Free label to recognize "
                      "the child later, e.g. 'kitchen-pc'. Optional."},
            "handle": {"type": "string", "description": "The child's own handle "
                       "on the server. Optional: by default your handle and the "
                       "label."},
            "model": {"type": "string", "description": "Pool alias of the model "
                      "it thinks with. Empty = your base model."},
            "base_url": {"type": "string", "description": "A model of its own: "
                         "OpenAI-compatible endpoint URL (with model_id)."},
            "model_id": {"type": "string", "description": "A model of its own: "
                         "the model id at base_url."},
            "api_key": {"type": "string", "description": "A model of its own: "
                        "its key, if the endpoint needs one."},
            "search": {"type": "boolean", "description": "Hand over this "
                       "machine's web search too. Default false."},
            "launch": {"type": "boolean", "description": "Build the image if "
                       "missing and start the child in a container on this "
                       "machine. Default false."},
        },
        "required": []},
    "fed_list_children": {"properties": {}, "required": []},
    "fed_ask_agent": {
        "properties": {
            "handle": {"type": "string", "description": "The child or friend "
                       "(contact) to delegate the task to."},
            "message": {"type": "string", "description": "The task to run on the "
                        "remote's machine."},
            "max_iterations": {"type": "integer", "description": "Optional: cap "
                               "on the remote turn's iterations."},
            "tab": {"type": "string", "description": "Optional: the calling "
                    "tab's cross-machine id, so the far end renders the turn "
                    "in THAT conversation instead of opening a separate one."},
            "device": {"type": "string", "description": "Optional: run on ONE "
                       "specific machine of that identity (its device id). Two "
                       "of your own computers share a handle and therefore one "
                       "mailbox, so without this the turn goes to whichever "
                       "answers first — and a path only exists on one of them."},
        },
        "required": ["handle", "message"]},
    "device_info": {"properties": {}},
    "device_rename": {
        "properties": {"name": {"type": "string", "description": "Short name, "
                                "e.g. 'home' or 'laptop'."}},
        "required": ["name"]},
    "device_regenerate": {"properties": {}},
    "fed_remove_contact": {
        "properties": {"handle": {"type": "string", "description": "Contact to remove."}},
        "required": ["handle"]},
    "fed_revoke_child": {
        "properties": {"handle": {"type": "string", "description": "Child to revoke."}},
        "required": ["handle"]},
    "fed_leave": {
        "properties": {"force_local": {"type": "boolean", "description": "If the "
                       "server doesn't respond, delete the local identity anyway "
                       "(to migrate from a dead server). Defaults to false."}},
        "required": []},
    "fed_share_appointment": {
        "properties": {
            "handle": {"type": "string", "description": "Handle of the contact "
                       "you send the appointment to."},
            "title": {"type": "string", "description": "Appointment title."},
            "date": {"type": "string", "description": "Appointment date/time, "
                     "e.g. '2026-07-20 18:00'."},
            "description": {"type": "string", "description": "Description (optional)."},
            "urgency": {"type": "string", "description": "Urgency: 'Alta', 'Normal' "
                        "or 'Baja' (optional, defaults to Normal)."},
        },
        "required": ["handle", "title", "date"]},
    "fed_list_incoming_appointments": {"properties": {}, "required": []},
    "fed_list_appointment_bounces": {"properties": {}, "required": []},
    "fed_accept_appointment": {
        "properties": {
            "title": {"type": "string", "description": "Title of the appointment "
                      "to accept (optional, to choose if there are several)."},
            "from_handle": {"type": "string", "description": "Sender's handle "
                            "(optional, to choose if there are several)."},
        },
        "required": []},
    "fed_share_skill": {
        "properties": {
            "handle": {"type": "string", "description": "Handle of the contact "
                       "you share the skill with."},
            "title": {"type": "string", "description": "Skill title."},
            "content": {"type": "string", "description": "Skill content."},
        },
        "required": ["handle", "title", "content"]},
    "fed_list_shared_skills": {"properties": {}, "required": []},
    "fed_accept_skill": {
        "properties": {
            "envelope_index": {"type": "integer", "description": "Index of the "
                               "skill to accept from the pending list (0 = first)."},
        },
        "required": []},
    "fed_share": {
        "properties": {
            "handle": {"type": "string", "description": "The contact to share "
                       "with, e.g. 'alice@example.org'."},
            "resource_type": {"type": "string", "description": "Resource type: "
                              "'note', 'skill', 'project', etc."},
            "payload": {"type": "object", "description": "The content to share "
                        "as an object."},
        },
        "required": ["handle", "resource_type", "payload"]},
    "fed_inbox": {
        "properties": {"resource_type": {"type": "string", "description": "Optional: "
                       "filter only one resource type, e.g. 'note'."}},
        "required": []},
    "fed_set_permissions": {
        "properties": {
            "handle": {"type": "string", "description": "The contact whose "
                       "permissions to configure, e.g. 'bob@example.org'."},
            "resource_type": {"type": "string", "description": "Resource type: "
                              "'calendar', 'notes', etc."},
            "perms": {"type": "object", "description": "Permissions as an object. "
                      "For calendar: {'can_add': bool, 'visibility': 'none'|'busy'|"
                      "'full'}."},
        },
        "required": ["handle", "resource_type", "perms"]},
    "fed_share_note": {
        "properties": {
            "handle": {"type": "string", "description": "The contact to share "
                       "the note with."},
            "category": {"type": "string", "description": "The note's category "
                         "in your HOPLITA notes."},
            "title": {"type": "string", "description": "The title of the note to "
                      "share."},
        },
        "required": ["handle", "category", "title"]},
    "fed_accept_note": {
        "properties": {"envelope_index": {"type": "integer", "description": "Optional: "
                       "which inbox note to save (0 = the first)."}},
        "required": []},
    "fed_share_folder": {
        "properties": {
            "handle": {"type": "string", "description": "The contact to share "
                       "the folder with."},
            "folder_name": {"type": "string", "description": "Name of the folder "
                            "to share, e.g. 'Recipes'."},
        },
        "required": ["handle", "folder_name"]},
    "fed_share_code": {
        "properties": {
            "path": {"type": "string", "description": "The project's root "
                     "directory (a git repository)."},
            "handle": {"type": "string", "description": "The contact to invite."},
        },
        "required": ["path", "handle"]},
    "fed_accept_code": {
        "properties": {
            "handle": {"type": "string", "description": "Who invited me."},
            "project": {"type": "string", "description": "The project name, as "
                        "fed_code_pending lists it."},
            "dest_path": {"type": "string", "description": "Where I want it on "
                          "THIS machine (new or empty directory)."},
        },
        "required": ["handle", "project", "dest_path"]},
    "fed_code_peers": {"properties": {"path": {"type": "string"}, "project": {"type": "string"}}},
    "fed_code_discard": {"properties": {"project": {"type": "string"}, "handle": {"type": "string", "description": "Sender. Omitted = every stuck transfer."}}},
    "fed_code_unshare": {"properties": {"path": {"type": "string"}, "project": {"type": "string"}, "peer": {"type": "string", "description": "Handle to drop. Omitted = every peer of the project."}}},
    "fed_code_retract": {"properties": {"handle": {"type": "string", "description": "The peer whose uncollected parts of mine to take back."}}, "required": ["handle"]},
    "fed_code_pending": {"properties": {}},
    "fed_code_state": {"properties": {}},
    "fed_code_push": {
        "properties": {
            "path": {"type": "string", "description": "The project's root."},
            "project": {"type": "string", "description": "Or its name, if the "
                        "path is registered."},
        }},
    "fed_code_pull": {"properties": {}},
    "fed_code_incoming": {"properties": {
        "path": {"type": "string"}, "project": {"type": "string"}}},
    "fed_code_take": {"properties": {
        "path": {"type": "string"}, "project": {"type": "string"},
        "peer": {"type": "string"}, "branch": {"type": "string"}},
        "required": ["peer"]},
    "fed_share_project": {
        "properties": {
            "project_name": {"type": "string", "description": "Name of the TODO "
                             "project to share (with its tasks)."},
            "handle": {"type": "string", "description": "The contact to share "
                       "the project with."},
        },
        "required": ["project_name", "handle"]},
    "fed_list_incoming_projects": {"properties": {}, "required": []},
    "fed_list_shared_projects": {"properties": {}, "required": []},
    "fed_accept_project": {
        "properties": {
            "project_name": {"type": "string", "description": "Name of the "
                             "project to accept (optional, to choose if there are several)."},
            "handle": {"type": "string", "description": "Handle of whoever shared "
                       "it with you (optional, to choose if there are several)."},
        },
        "required": []},
    "fed_accept_folder": {
        "properties": {
            "handle": {"type": "string", "description": "Who proposed the folder to you."},
            "folder_name": {"type": "string", "description": "Optional: folder "
                            "name if you have several pending from that person."},
            "folder_id": {"type": "string", "description": "Optional: direct id."},
        },
        "required": ["handle"]},
    "fed_list_folders": {"properties": {}, "required": []},
    "fed_publish_folder": {
        "properties": {
            "folder_name": {"type": "string", "description": "The note folder "
                            "(category) to make public."},
        },
        "required": ["folder_name"]},
    "fed_unpublish_folder": {
        "properties": {
            "folder_name": {"type": "string", "description": "The public folder "
                            "to withdraw."},
        },
        "required": ["folder_name"]},
    "fed_public_folders": {
        "properties": {
            "handle": {"type": "string", "description": "Whose public catalog "
                       "to list (e.g. someone@server)."},
        },
        "required": ["handle"]},
    "fed_subscribe_folder": {
        "properties": {
            "handle": {"type": "string", "description": "The owner's handle."},
            "folder_name": {"type": "string", "description": "The public folder's "
                            "name (or pass folder_id)."},
            "folder_id": {"type": "string", "description": "Optional: direct id."},
        },
        "required": ["handle"]},
    "fed_unsubscribe_folder": {
        "properties": {
            "handle": {"type": "string", "description": "The owner's handle."},
            "folder_name": {"type": "string", "description": "The folder's name "
                            "(or pass folder_id)."},
            "folder_id": {"type": "string", "description": "Optional: direct id."},
        },
        "required": ["handle"]},
    "fed_activity": {
        "properties": {
            "limit": {"type": "integer", "description": "How many of the most "
                      "recent events to read (default 200)."},
        },
        "required": []},
    "fed_activity_clear": {
        "properties": {
            "peer": {"type": "string", "description": "Handle whose events to "
                     "erase. Omit to erase the whole log."},
        },
        "required": []},
    "fed_debug_notes": {
        "properties": {
            "category": {"type": "string", "description": "Note category to dump."},
        },
        "required": ["category"]},
    "fed_folder_push": {
        "properties": {
            "folder_name": {"type": "string", "description": "The name of the "
                            "folder (category) whose notes you want to send."},
        },
        "required": ["folder_name"]},
    "fed_folder_pull": {
        "properties": {
            "folder_name": {"type": "string", "description": "Optional: collect "
                            "only this folder's notes."},
        },
        "required": []},
    "fed_sync_status": {"properties": {}, "required": []},
    "fed_project_push": {
        "properties": {
            "project_name": {"type": "string", "description": "The name of the "
                             "project whose tasks you want to send."},
        },
        "required": ["project_name"]},
    "fed_project_pull": {
        "properties": {
            "project_name": {"type": "string", "description": "Optional: collect "
                             "only this project's tasks."},
        },
        "required": []},
    "fed_is_shared_folder": {
        "properties": {
            "folder_name": {"type": "string", "description": "Folder name."},
        },
        "required": ["folder_name"]},
    "fed_is_shared_project": {
        "properties": {
            "project_name": {"type": "string", "description": "Project name."},
        },
        "required": ["project_name"]},
    "fed_save_note_guarded": {
        "properties": {
            "category": {"type": "string"},
            "note_uuid": {"type": "string"},
            "title": {"type": "string"},
            "content": {"type": "string"},
            "base_rev": {"type": "integer", "description": "The rev (logical "
                         "version) the GUI loaded when opening the note."},
        },
        "required": ["category", "note_uuid", "title", "content"]},
    "fed_save_task_guarded": {
        "properties": {
            "project": {"type": "string"},
            "task_uuid": {"type": "string"},
            "payload": {"type": "object", "description": "The task's edited "
                        "fields (title, description, progress, priority, rag_urls)."},
            "base_rev": {"type": "integer"},
        },
        "required": ["project", "task_uuid", "payload"]},
    "fed_delete_note_guarded": {
        "properties": {"category": {"type": "string"}, "note_uuid": {"type": "string"}},
        "required": ["category", "note_uuid"]},
    "fed_clear_category_guarded": {
        "properties": {"category": {"type": "string"}},
        "required": ["category"]},
    "fed_sync_push": {"properties": {}},
    "fed_sync_breakdown": {"properties": {}},
    "fed_sync_pull": {"properties": {}},
    "fed_motd": {"properties": {}},
    "fed_notices": {"properties": {}},
    "fed_quota": {"properties": {}},
    "fed_delete_task_guarded": {
        "properties": {"project": {"type": "string"}, "task_uuid": {"type": "string"}},
        "required": ["project", "task_uuid"]},
}

for _n, _t in FED_TOOLS.items():
    if "params" in _t and _n not in FED_TOOL_PARAMS:
        FED_TOOL_PARAMS[_n] = {"properties": _t["params"],
                               "required": list(_t.get("required", []))}
FED_TOOL_PARAMS.update(hoplita_children.CHILD_SCHEMAS)



import json as _json
from datetime import datetime as _dt, timedelta as _timedelta

_ACTIVITY_STORE = "activity"
_ACTIVITY_KINDS = ("commits", "note_add", "note_edit", "note_del",
                   "task_add", "task_edit", "task_del",
                   "board_add", "board_edit", "board_del",
                   "child_task")


def _activity_record(peer, kind, resource, title="", n=1):
    try:
        sql = _sql()
        if sql is None or not peer or not kind:
            return
        ts = _dt.now().isoformat(timespec="seconds")
        key = f"{ts}|{_uuid.uuid4().hex[:8]}"
        sql.upsert(_ACTIVITY_STORE, key,
                   {"key": key, "ts": ts, "peer": str(peer), "kind": str(kind),
                    "resource": str(resource or ""), "title": str(title or ""),
                    "n": int(n or 1)})
    except Exception:
        pass


def _activity_rows(limit=500):
    sql = _sql()
    if sql is None:
        return []
    out = []
    for p in (sql.load(_ACTIVITY_STORE) or []):
        if not isinstance(p, dict) or not p.get("peer"):
            continue
        out.append(p)
    out.sort(key=lambda r: str(r.get("ts", "")), reverse=True)
    return out[:max(1, int(limit or 500))]


def tool_fed_activity(limit=200):
    rows = _activity_rows(limit)
    groups = {}
    for r in rows:
        day = str(r.get("ts", ""))[:10]
        k = (r.get("peer"), r.get("resource"), r.get("kind"), day)
        g = groups.setdefault(k, {"peer": r.get("peer"),
                                  "resource": r.get("resource"),
                                  "kind": r.get("kind"), "day": day,
                                  "n": 0, "titles": [], "ts": r.get("ts")})
        g["n"] += int(r.get("n") or 1)
        if r.get("title") and len(g["titles"]) < 6:
            g["titles"].append(r["title"])
        if str(r.get("ts", "")) > str(g["ts"] or ""):
            g["ts"] = r.get("ts")
    out = sorted(groups.values(), key=lambda g: str(g["ts"]), reverse=True)
    return {"ok": True, "count": len(rows), "events": rows, "groups": out}


def tool_fed_activity_clear(peer=None):
    sql = _sql()
    if sql is None:
        return {"error": "SQL store unavailable."}
    gone = 0
    for p in (sql.load(_ACTIVITY_STORE) or []):
        if not isinstance(p, dict):
            continue
        if peer and p.get("peer") != peer:
            continue
        k = p.get("key")
        if not k:
            continue
        try:
            sql.delete(_ACTIVITY_STORE, k)
            gone += 1
        except Exception:
            pass
    return {"ok": True, "deleted": gone}



_KEY_429_MAX_WAIT = 60

_SYNC_STORES = ("notes", "tasks", "appointments", "jobs", "credentials",
                "facts", "lessons", "experience", "notes_index",
                "chat_sessions", "canvases", "firewall",
                "whiteboards",
                _ACTIVITY_STORE,
                hoplita_children.STORE,
                hs.TOMBSTONE_STORE,
                "project_events",
                "todo__blob", "notes__blob", "cred__blob", "cron__blob")


def _sync_export_row(store, p):
    if store == "notes" and p.get("encrypted"):
        nm = _notes()
        if nm is not None:
            try:
                p = {**p, "content": nm.decrypt_note_content(p["content"]),
                     "encrypted": False, "_synced_plain": True}
            except Exception:
                pass
    return p


def _sync_import_row(store, p):
    if store == "notes" and (p.get("_synced_plain") or not p.get("encrypted")):
        nm = _notes()
        if nm is not None:
            try:
                p = {**p, "content": nm.encrypt_note_content(p.get("content", "") or ""),
                     "encrypted": True}
                p.pop("_synced_plain", None)
            except Exception:
                pass
    return p


def _sync_row_key(p):
    return (p.get("uuid") or p.get("id") or str(p.get("site", "")).lower()
            or p.get("key") or "")


def _tied_copy_wins(store, incoming, current):
    if store != "chat_sessions" or not isinstance(current, dict) \
            or not isinstance(incoming, dict):
        return None

    def _pin(row):
        v = row.get("pinned_device")
        return v.get("id") if isinstance(v, dict) else None
    pin = _pin(incoming) or _pin(current)
    if not pin:
        return None
    in_home = incoming.get("writer_device") == pin
    cur_home = current.get("writer_device") == pin
    if in_home and not cur_home:
        return True
    if cur_home and not in_home:
        return False
    return None


def _sync_row_version(p):
    rev = int(p.get("rev", 0) or 0)
    ts = str(p.get("updated_at") or p.get("updated") or p.get("ts")
             or p.get("created_at") or "")
    return (rev, ts)



_SYNC_KNOWN = "sync_known"


def _sync_fp(p):
    return _hashlib.sha1(_json.dumps(p, sort_keys=True, ensure_ascii=False,
                                    default=str).encode("utf-8")).hexdigest()[:16]


_SYNC_PUSHED_FP = "sync_pushed_fp"


def _sync_row_stores():
    return [s for s in _SYNC_STORES
            if not s.endswith("__blob") and s != hs.TOMBSTONE_STORE]


def _sync_known_save(sql, pairs):
    known = {}
    for s, p in pairs:
        k = _sync_row_key(p)
        if k:
            known.setdefault(s, {})[k] = _sync_fp(p)
    sql.meta_set(_SYNC_KNOWN, _json.dumps(known))


def _sync_known_load(sql):
    try:
        return _json.loads(sql.meta_get(_SYNC_KNOWN) or "{}") or {}
    except Exception:
        return {}


def _tomb_key(store, p):
    title = str(p.get("title") or "").strip().lower()
    if store == "appointments":
        return f"{p.get('date', '')}|{title}"
    if store == "tasks":
        return f"{p.get('project', 'general')}|{title}"
    if store == "notes":
        return f"{p.get('category', '')}|{title}"
    return None


def _tombstoned(tombs, store, p):
    row_ts = str(_sync_row_version(p)[1] or "")
    key = _tomb_key(store, p)
    if key:
        ts = (tombs.get(store) or {}).get(key)
        if ts and str(ts) >= row_ts:
            return True
    uts = (tombs.get("_uni") or {}).get(f"{store}:{_sync_row_key(p)}")
    return bool(uts and str(uts) >= row_ts)


def _collect_tombstones(snap):
    tombs = {"appointments": {}, "tasks": {}, "notes": {}}
    todo = _todo()
    if todo is not None and hasattr(todo, "data"):
        tombs["appointments"].update(todo.data.get("deleted_appointments") or {})
        tombs["tasks"].update(todo.data.get("deleted_tasks") or {})
    notes = _notes()
    if notes is not None and hasattr(notes, "data"):
        tombs["notes"].update(notes.data.get("deleted_notes") or {})
    for row in (snap or {}).get("rows", []):
        if not str(row.get("store", "")).endswith("__blob"):
            continue
        p = row.get("payload") or {}
        for store, mapkey in (("appointments", "deleted_appointments"),
                              ("tasks", "deleted_tasks"),
                              ("notes", "deleted_notes")):
            for k, v in (p.get(mapkey) or {}).items():
                if str(v) > str(tombs[store].get(k, "")):
                    tombs[store][k] = v
    uni = {}
    sql = _sql()
    rows_in = [r.get("payload") or {} for r in (snap or {}).get("rows", [])
               if r.get("store") == hs.TOMBSTONE_STORE]
    for p in (sql.load(hs.TOMBSTONE_STORE) if sql is not None else []) + rows_in:
        k = str(p.get("key") or "")
        if k and str(p.get("ts", "")) > str(uni.get(k, "")):
            uni[k] = str(p.get("ts", ""))
    tombs["_uni"] = uni
    return tombs


def _merge_blob_row(sql, store, incoming):
    locals_ = sql.load(store)
    before = dict(locals_[0]) if locals_ else {}
    merged = dict(before)
    for k, v in (incoming or {}).items():
        if isinstance(v, dict):
            m = dict(merged.get(k, {}))
            m.update(v)
            merged[k] = m
        elif (k == "jobs" and isinstance(v, list)
              and all(isinstance(x, dict) for x in v)):
            def _fresh(j):
                return (int(j.get("run_count") or 0),
                        str(j.get("last_run") or ""))
            _mine = {j.get("id"): j for j in (merged.get(k) or [])
                     if isinstance(j, dict)}
            out = dict(_mine)
            for _in in v:
                _id = _in.get("id")
                _cur = _mine.get(_id)
                if _cur is None or _fresh(_in) > _fresh(_cur):
                    out[_id] = _in
            merged[k] = list(out.values())
        elif isinstance(v, list) and all(isinstance(x, str) for x in v):
            merged[k] = sorted(set(merged.get(k, []) or []) | set(v))
        else:
            merged[k] = v
    if merged == before:
        return False
    sql.upsert(store, "blob", merged)
    return True


_sync_lock = _threading.Lock()


def tool_fed_sync_push():
    with _sync_lock:
        return _fed_sync_push()


def tool_fed_sync_pull():
    with _sync_lock:
        return _fed_sync_pull()


def _fed_sync_push():
    sql = _sql()
    if sql is None:
        return {"error": "SQL store unavailable (nothing to sync)."}
    if not _store().has_identity():
        return {"skipped": "no_identity"}
    try:
        _, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    st = _store()
    my_pub = st.data.get("public_key_b64")
    if not my_pub:
        return {"error": "No identity public key (register federation first)."}
    _locked = _sync_locked_set()
    try:
        _cutoff = (_dt.now() - _timedelta(days=60)).isoformat()
        for _p in sql.load(hs.TOMBSTONE_STORE):
            if str(_p.get("ts", "")) < _cutoff:
                sql.delete_raw(hs.TOMBSTONE_STORE, _p.get("key", ""))
    except Exception:
        pass

    def _export(s, p):
        p = _sync_export_row(s, p)
        if _locked and s == "notes__blob" \
                and isinstance(p.get("deleted_notes"), dict):
            dn = {k: v for k, v in p["deleted_notes"].items()
                  if k not in _locked}
            if len(dn) != len(p["deleted_notes"]):
                p = {**p, "deleted_notes": dn}
        return p

    rows = [{"store": s, "payload": _export(s, p)}
            for s in _SYNC_STORES for p in sql.load(s)
            if _sync_row_visible(s, p, _locked)]
    base = int(sql.meta_get("sync_version") or 0)
    _fp = _hashlib.sha256(_json.dumps(rows, sort_keys=True, ensure_ascii=False,
                                      default=str).encode("utf-8")).hexdigest()
    if sql.meta_get(_SYNC_PUSHED_FP) == f"{base}:{_fp}":
        return {"ok": True, "unchanged": True, "version": base, "rows": len(rows)}
    snap = {"v": 1, "ts": _dt.now().isoformat(), "n": len(rows), "rows": rows,
            "stores": _sync_row_stores()}
    pad = (-len(_json.dumps(snap, ensure_ascii=False))) % 65536
    snap["_pad"] = "0" * pad
    sealed = _seal(_store(), my_pub, snap)
    _t0 = _time.time()
    try:
        resp = fed.server_sync_push(server_url, token, sealed, base)
    except fed.FedServerError as e:
        d = getattr(e, "data", {}) or {}
        _why = (f"{_time.time() - _t0:.1f} s, {len(sealed) / 1e6:.1f} MB: {e}")
        print(f"[SYNC] push failed after {_why}", file=_sys.stderr)
        if getattr(e, "status", None) == 409 or "409" in str(e) \
                or "conflict" in str(e).lower():
            return {"ok": False, "needs_pull": True,
                    "message": "Server has a newer snapshot — pull first."}
        if d.get("error") == "quota_exceeded":
            return {"error": "quota_exceeded", "resource": d.get("resource"),
                    "limit": d.get("limit"), "used": d.get("used")}
        if d.get("error") == "offline":
            return {"error": "offline", "detail": _why}
        return {"error": f"Sync push failed: {e}"}
    sql.meta_set("sync_version", resp.get("version", base + 1))
    sql.meta_set(_SYNC_PUSHED_FP, f"{resp.get('version', base + 1)}:{_fp}")
    try:
        _row_stores = set(_sync_row_stores())
        _sync_known_save(sql, [(s, p) for s in _row_stores for p in sql.load(s)
                               if _sync_row_visible(s, p, _locked)])
    except Exception as e:
        print(f"[SYNC] could not record what the server holds: {e}", file=_sys.stderr)
    return {"ok": True, "version": resp.get("version", base + 1),
            "rows": len(rows)}


def tool_fed_sync_breakdown():
    sql = _sql()
    if sql is None:
        return {"error": "SQL store unavailable."}
    stores, top, total = [], [], 0
    for s in _SYNC_STORES:
        try:
            rows = sql.load(s) or []
        except Exception:
            rows = []
        size = 0
        for p in rows:
            try:
                b = len(_json.dumps(p, ensure_ascii=False, default=str))
            except Exception:
                b = 0
            size += b
            if isinstance(p, dict):
                label = str(p.get("title") or p.get("key") or p.get("id")
                            or p.get("uuid") or "")[:60]
            else:
                label = ""
            top.append((b, s, label))
        total += size
        stores.append({"store": s, "rows": len(rows), "bytes": size})
    stores.sort(key=lambda x: -x["bytes"])
    top.sort(key=lambda x: -x[0])
    return {"ok": True, "total_bytes": total,
            "stores": stores,
            "top_rows": [{"bytes": b, "store": s, "row": lbl}
                         for b, s, lbl in top[:12]],
            "note": ("the snapshot is the WHOLE of this, re-sealed and "
                     "re-uploaded on every push (padded to 64KB buckets); "
                     "prune the fat stores and every future push gets "
                     "lighter")}


def _fed_sync_pull():
    sql = _sql()
    if sql is None:
        return {"error": "SQL store unavailable."}
    if not _store().has_identity():
        return {"skipped": "no_identity"}
    try:
        _, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    st = _store()
    my_priv = st.data.get("private_key_b64")
    _t0 = _time.time()
    try:
        resp = fed.server_sync_pull(server_url, token,
                                    have_version=sql.meta_get("sync_version"))
    except fed.FedServerError as e:
        if (getattr(e, "data", {}) or {}).get("error") == "offline":
            _why = f"{_time.time() - _t0:.1f} s: {e}"
            print(f"[SYNC] pull failed after {_why}", file=_sys.stderr)
            return {"error": "offline", "detail": _why}
        return {"error": f"Sync pull failed: {e}"}
    blob = resp.get("ciphertext")
    version = int(resp.get("version", 0) or 0)
    if resp.get("unchanged"):
        return {"ok": True, "merged": 0, "unchanged": True, "version": version}
    if not blob:
        sql.meta_set(_SYNC_PUSHED_FP, "")
        return {"ok": True, "merged": 0, "message": "Nothing on the server yet."}
    try:
        snap = crypto.unbox(my_priv, st.data.get("public_key_b64"), blob)
    except Exception:
        try:
            crypto.unseal(my_priv, blob)
        except Exception:
            return {"error": "decrypt_failed"}
        sql.meta_set("sync_version", str(version))
        return {"ok": True, "merged": 0, "unsigned_replaced": True,
                "message": "The copy on the server was not sealed by this "
                           "identity: not merged; the next upload replaces it."}
    global _snap_merging
    _snap_merging = True
    local = {}
    for s in _SYNC_STORES:
        if s.endswith("__blob"):
            continue
        local[s] = {_sync_row_key(p): p for p in sql.load(s)}
    try:
        tombs = _collect_tombstones(snap)
        merged = 0
        for row in snap.get("rows", []):
            store = row.get("store")
            p = row.get("payload") or {}
            if not store:
                continue
            if store.endswith("__blob"):
                if _merge_blob_row(sql, store, p):
                    merged += 1
                continue
            if store != hs.TOMBSTONE_STORE and _tombstoned(tombs, store, p):
                continue
            key = _sync_row_key(p)
            cur = local.get(store, {}).get(key)
            _tied = _tied_copy_wins(store, p, cur)
            if _tied is False:
                continue
            if cur is None or _tied or _sync_row_version(p) > _sync_row_version(cur):
                sql.upsert(store, key, _sync_import_row(store, p))
                merged += 1
        for store in _SYNC_STORES:
            if store.endswith("__blob") or store == hs.TOMBSTONE_STORE:
                continue
            for p in sql.load(store):
                if _tombstoned(tombs, store, p):
                    sql.delete_raw(store, _sync_row_key(p))
                    merged += 1
        known = _sync_known_load(sql)
        if version < int(sql.meta_get("sync_version") or 0):
            sql.meta_set(_SYNC_PUSHED_FP, "")
            print(f"[SYNC] the server's snapshot (v{version}) is older than the "
                  f"last one seen here: nothing dropped for being absent",
                  file=_sys.stderr)
            known = {}
        covered = set(snap.get("stores") or [])
        in_snap = {}
        for row in snap.get("rows", []):
            in_snap.setdefault(row.get("store"), set()).add(
                _sync_row_key(row.get("payload") or {}))
        _locked_now = set(_sync_locked_set())
        for _b in sql.load("notes__blob"):
            _locked_now |= {k for k, v in (_b.get("local_only") or {}).items() if v}
        gone = 0
        for store in _sync_row_stores():
            ks = known.get(store) or {}
            if store not in covered or not ks:
                continue
            for p in sql.load(store):
                k = _sync_row_key(p)
                if (k in in_snap.get(store, ()) or k not in ks
                        or ks[k] != _sync_fp(p)
                        or not _sync_row_visible(store, p, _locked_now)):
                    continue
                sql.delete_raw(store, k)
                merged += 1
                gone += 1
        if gone:
            print(f"[SYNC] {gone} row(s) deleted on another machine while "
                  f"this one was away: dropped here too", file=_sys.stderr)
        _row_stores = set(_sync_row_stores())
        _sync_known_save(sql, [(s, p) for s in _row_stores for p in sql.load(s)
                               if _sync_row_key(p) in in_snap.get(s, ())])
    finally:
        _snap_merging = False
    sql.meta_set("sync_version", version)
    if merged and _get_reload is not None:
        try:
            _get_reload()
        except Exception as e:
            print(f"[SYNC] reload after pull failed: {e}", file=_sys.stderr)
    return {"ok": True, "merged": merged, "version": version}


def tool_fed_motd():
    try:
        _, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    try:
        return fed.server_motd(server_url, token)
    except fed.FedServerError as e:
        return {"error": f"MOTD fetch failed: {e}"}


def tool_fed_notices():
    try:
        _, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    try:
        return fed.server_notices(server_url, token)
    except fed.FedServerError as e:
        return {"error": f"Notices fetch failed: {e}"}


def tool_fed_quota():
    if not _store().has_identity():
        return {"registered": False,
                "error": "No identity. Register with fed_register first."}
    try:
        store, server_url, token = _require_session()
    except Exception as e:
        return {"error": str(e)}
    who = {"handle": store.data.get("handle") or "",
           "server": _urlparse(server_url).hostname or server_url}
    try:
        out = fed.server_quota(server_url, token)
    except fed.FedServerError as e:
        return dict(who, error=f"Quota fetch failed: {e}")
    return dict(out, **who) if isinstance(out, dict) else out
