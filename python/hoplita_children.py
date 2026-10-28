
import re
import threading
from contextlib import contextmanager
from datetime import datetime

try:
    from hoplita_orchestrator import CAPABILITY_TOOLS, capabilities_to_profile
except Exception:
    CAPABILITY_TOOLS = {"none": "none"}

    def capabilities_to_profile(caps):
        return "none"


STORE = "children"

_NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,47}$")

FIELDS = ("name", "role", "model", "capabilities", "tools", "notes_folders",
          "projects", "skills_folder", "avatar", "node")

_get_sql = None
_runner = None
_get_models = None


def set_sql_getter(getter):
    global _get_sql
    _get_sql = getter


def set_runner(fn):
    global _runner
    _runner = fn


def set_models_getter(getter):
    global _get_models
    _get_models = getter


def _sql():
    return _get_sql() if _get_sql is not None else None


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _key(name):
    return (name or "").strip().lower()


def normalize_target(handle):
    h = (handle or "").strip()
    if h.lower().startswith("child:"):
        h = h.split(":", 1)[1].strip()
    return h


def _clean_list(v):
    if v is None:
        return None
    if isinstance(v, str):
        v = [p for p in re.split(r"[,\n]", v)]
    out = []
    for x in (v or []):
        s = str(x).strip()
        if s and s not in out:
            out.append(s)
    return out


def _sane(p):
    if not isinstance(p, dict) or not p.get("name"):
        return None
    return {
        "name": str(p.get("name") or ""),
        "role": str(p.get("role") or ""),
        "model": str(p.get("model") or ""),
        "capabilities": _clean_list(p.get("capabilities")) or [],
        "tools": _clean_list(p.get("tools")) or [],
        "notes_folders": _clean_list(p.get("notes_folders")) or [],
        "projects": _clean_list(p.get("projects")) or [],
        "node": str(p.get("node") or "").strip(),
        "skills_folder": str(p.get("skills_folder") or ""),
        "avatar": str(p.get("avatar") or ""),
        "created_at": str(p.get("created_at") or ""),
        "updated_at": str(p.get("updated_at") or ""),
        "rev": int(p.get("rev") or 1),
        "key": str(p.get("key") or _key(p.get("name"))),
    }


def load_all():
    sql = _sql()
    if sql is None:
        return []
    out = []
    try:
        rows = sql.load(STORE) or []
    except Exception:
        return []
    for p in rows:
        pack = _sane(p)
        if pack:
            out.append(pack)
    out.sort(key=lambda x: x["name"].lower())
    return out


def find(handle):
    name = normalize_target(handle)
    if not name or "@" in name:
        return None
    k = _key(name)
    for pack in load_all():
        if pack["key"] == k:
            return pack
    return None


_push_pack = None


def set_pack_pusher(fn):
    global _push_pack
    _push_pack = fn


def _save(pack):
    sql = _sql()
    if sql is None:
        return False
    pack["updated_at"] = _now()
    sql.upsert(STORE, pack["key"], pack)
    if pack.get("node") and _push_pack is not None:
        try:
            _push_pack(pack)
        except Exception:
            pass
    return True


def _model_warning(model):
    if not model or model in ("base", "default") or _get_models is None:
        return ""
    try:
        pool = list(_get_models() or [])
    except Exception:
        return ""
    if model in pool:
        return ""
    return (f"'{model}' is not in the model pool ({', '.join(pool) or 'empty'}), "
            f"so this child runs on the base model until you add it.")


def _bad_capabilities(caps):
    known = set(CAPABILITY_TOOLS)
    return [c for c in (caps or []) if c not in known]


def create(name, role="", model="", capabilities=None, notes_folders=None,
           skills_folder="", avatar="", projects=None, node=""):
    name = normalize_target(name)
    if not name:
        return {"error": "A child needs a name."}
    if "@" in name:
        return {"error": ("A local child's name cannot contain '@': that is how "
                          "a federated contact is written (alice@example.org) and "
                          "a local name resolves first, so it would shadow them. "
                          "Use a plain name, or fed_spawn_child for a real node.")}
    if not _NAME_OK.match(name):
        return {"error": ("Name may only use letters, digits, space, '.', '_' "
                          f"or '-' (max 48): '{name}'.")}
    if find(name) is not None:
        return {"error": f"You already have a child called '{name}'."}
    if _sql() is None:
        return {"error": "The vault is not open, so there is nowhere to keep it."}
    caps = _clean_list(capabilities) or []
    bad = _bad_capabilities(caps)
    if bad:
        return {"error": (f"Unknown capabilities {bad}. Pick from: "
                          f"{', '.join(sorted(CAPABILITY_TOOLS))}.")}
    pack = _sane({"name": name, "role": role, "model": (model or "").strip(),
                  "capabilities": caps,
                  "notes_folders": _clean_list(notes_folders) or [],
                  "projects": _clean_list(projects) or [],
                  "node": (node or "").strip(),
                  "skills_folder": (skills_folder or "").strip(),
                  "avatar": (avatar or "").strip(),
                  "created_at": _now(), "rev": 1, "key": _key(name)})
    if not _save(pack):
        return {"error": "Could not write the pack."}
    return pack


def teach(name, role_append="", role="", model=None, capabilities=None,
          add_capabilities=None, notes_folders=None, add_notes_folders=None,
          projects=None, add_projects=None, skills_folder=None, avatar=None,
          node=None):
    pack = find(name)
    if pack is None:
        have = [p["name"] for p in load_all()]
        return {"error": (f"No local child called '{normalize_target(name)}'. "
                          + (f"You have: {', '.join(have)}." if have
                             else "You have none yet — child_create makes one."))}
    before = dict(pack)
    changed = {}

    def _set(field, value):
        if value is None:
            return
        if pack.get(field) == value:
            return
        changed[field] = {"from": before.get(field), "to": value}
        pack[field] = value

    if role_append and str(role_append).strip():
        extra = str(role_append).strip()
        base = (pack.get("role") or "").strip()
        _set("role", (base + "\n" + extra).strip() if base else extra)
    if role and str(role).strip():
        _set("role", str(role).strip())

    if capabilities is not None or add_capabilities is not None:
        caps = (_clean_list(capabilities) if capabilities is not None
                else list(pack["capabilities"]))
        for c in (_clean_list(add_capabilities) or []):
            if c not in caps:
                caps.append(c)
        bad = _bad_capabilities(caps)
        if bad:
            return {"error": (f"Unknown capabilities {bad}. Pick from: "
                              f"{', '.join(sorted(CAPABILITY_TOOLS))}.")}
        _set("capabilities", caps)

    if notes_folders is not None or add_notes_folders is not None:
        folders = (_clean_list(notes_folders) if notes_folders is not None
                   else list(pack["notes_folders"]))
        for f in (_clean_list(add_notes_folders) or []):
            if f not in folders:
                folders.append(f)
        _set("notes_folders", folders)

    if projects is not None or add_projects is not None:
        projs = (_clean_list(projects) if projects is not None
                 else list(pack["projects"]))
        for pr in (_clean_list(add_projects) or []):
            if pr not in projs:
                projs.append(pr)
        _set("projects", projs)

    if model is not None:
        _set("model", str(model).strip())
    if node is not None:
        _set("node", str(node).strip())
    if skills_folder is not None:
        _set("skills_folder", str(skills_folder).strip())
    if avatar is not None:
        _set("avatar", str(avatar).strip())

    if not changed:
        return {"pack": pack, "changed": {}, "unchanged": True,
                "message": f"Nothing to change on '{pack['name']}'."}
    pack["rev"] = int(pack.get("rev") or 1) + 1
    if not _save(pack):
        return {"error": "Could not write the pack."}
    out = {"pack": pack, "changed": changed, "unchanged": False,
           "message": describe_diff(pack["name"], changed)}
    warn = _model_warning(pack.get("model"))
    if warn:
        out["warning"] = warn
    return out


def describe_diff(name, changed):
    if not changed:
        return f"Nothing changed on '{name}'."
    bits = []
    for field, d in changed.items():
        old, new = d.get("from"), d.get("to")
        if field == "role":
            old_t, new_t = str(old or ""), str(new or "")
            if new_t.startswith(old_t) and old_t:
                bits.append(f"role += «{new_t[len(old_t):].strip()}»")
            else:
                bits.append(f"role ← «{new_t[:80]}»")
        elif isinstance(new, list):
            bits.append(f"{field}: {old or []} → {new}")
        else:
            bits.append(f"{field}: '{old or ''}' → '{new or ''}'")
    return f"'{name}': " + "; ".join(bits)


def delete(name):
    pack = find(name)
    if pack is None:
        return {"error": f"No local child called '{normalize_target(name)}'."}
    sql = _sql()
    if sql is None:
        return {"error": "The vault is not open."}
    try:
        sql.delete(STORE, pack["key"])
    except Exception as e:
        return {"error": f"Could not delete it: {e}"}
    msg = (f"'{pack['name']}' is gone. Its pack went with it; nothing it "
           "wrote did.")
    if pack.get("node"):
        msg += (f" WARNING: it was running on '{pack['node']}'. That node is "
                "still enrolled and still wearing this role — deleting the "
                "pack here does not un-teach it. Use fed_revoke_child to "
                "actually cut it off.")
    return {"ok": True, "name": pack["name"], "node": pack.get("node", ""),
            "message": msg}


def as_contact(pack):
    return {"handle": pack["name"], "kind": "local_child", "local": True,
            "status": "local", "online": None, "verified": False,
            "trust_level": "", "avatar": pack.get("avatar", ""),
            "role": pack.get("role", ""), "model": pack.get("model", ""),
            "capabilities": list(pack.get("capabilities") or []),
            "notes_folders": list(pack.get("notes_folders") or []),
            "projects": list(pack.get("projects") or []),
            "node": pack.get("node", ""),
            "skills_folder": pack.get("skills_folder", "")}


_SCOPE = threading.local()


@contextmanager
def scoped(pack):
    prev = getattr(_SCOPE, "pack", None)
    _SCOPE.pack = pack
    try:
        yield pack
    finally:
        _SCOPE.pack = prev


def active():
    return getattr(_SCOPE, "pack", None)


def notes_scope():
    pack = active()
    if not pack:
        return None
    return list(pack.get("notes_folders") or []) or None


def _reachable():
    scope = notes_scope()
    if scope is None:
        return None
    pack = active() or {}
    skills = (pack.get("skills_folder") or "").strip()
    return scope + [skills] if skills and skills not in scope else scope


def notes_allowed(category):
    scope = _reachable()
    return scope is None or str(category or "") in scope


def scope_refusal(category):
    pack = active()
    scope = _reachable() or []
    return (f"'{category}' is outside what {pack['name'] if pack else 'this child'} "
            f"can see. Its folders are: {', '.join(scope)}. Ask its owner to widen "
            f"the scope (child_teach add_notes_folders) rather than working around it.")


def projects_scope():
    pack = active()
    if not pack:
        return None
    return list(pack.get("projects") or []) or None


def projects_allowed(project):
    scope = projects_scope()
    return scope is None or str(project or "general") in scope


def project_refusal(project):
    scope = projects_scope() or []
    pack = active()
    return (f"'{project or 'general'}' is not one of "
            f"{pack['name'] if pack else 'this child'}'s projects. It works in: "
            f"{', '.join(scope)}. Ask its owner to widen the scope "
            f"(child_teach add_projects) rather than working around it.")


def skills_scope():
    pack = active()
    if not pack:
        return None
    folder = (pack.get("skills_folder") or "").strip()
    return [folder] if folder else None



def system_block(pack):
    lines = [f"You are «{pack['name']}», a specialised agent of this user."]
    role = (pack.get("role") or "").strip()
    if role:
        lines += ["", role]
    folders = pack.get("notes_folders") or []
    if folders:
        lines += ["", ("Your notes scope: " + ", ".join(folders) +
                       ". Those are the folders you can search, read and write; "
                       "any other one will refuse.")]
    projs = pack.get("projects") or []
    if projs:
        lines.append("Your task projects: " + ", ".join(projs) +
                     ". You can list, create and update the tasks in those; "
                     "any other project will refuse.")
    skills = (pack.get("skills_folder") or "").strip()
    if skills:
        lines.append(f"Your skills live in the '{skills}' folder.")
    lines += ["", ("Answer with the RESULT of the task, not with a report about "
                   "having done it. Whoever asked cannot see your steps.")]
    return "\n".join(lines)


def run(pack, message, terminal=True, event_sink=None):
    if _runner is None:
        return (f"[local child '{pack['name']}' cannot run: the MCP did not "
                "register a runner]")
    try:
        with scoped(pack):
            out = _runner(pack, message, terminal, event_sink)
    except Exception as e:
        return f"[local child '{pack['name']}' failed: {e}]"
    return (out or "").strip() or f"[local child '{pack['name']}': empty]"



def tool_child_create(a):
    res = create(a.get("name", ""), a.get("role", ""), a.get("model", ""),
                 a.get("capabilities"), a.get("notes_folders"),
                 a.get("skills_folder", ""), a.get("avatar", ""),
                 a.get("projects"), a.get("node", ""))
    if res.get("error"):
        return res
    out = {"ok": True, "child": res,
           "message": (f"'{res['name']}' created. Give it work with "
                       f"fed_ask_agent(handle='{res['name']}', message=…), from a "
                       f"plan (target 'child:{res['name']}') or from a CRON job. "
                       "It is LOCAL: no identity, no server, nothing to enroll.")}
    warn = _model_warning(res.get("model"))
    if warn:
        out["warning"] = warn
    return out


def tool_child_list(a):
    kids = load_all()
    return {"ok": True, "count": len(kids), "children": kids,
            "message": (f"{len(kids)} local child(ren)." if kids else
                        "No local children yet. child_create makes one: a "
                        "personalisation pack that runs here, with no identity "
                        "and no server (that is fed_spawn_child).")}


def tool_child_teach(a):
    return teach(a.get("name", ""),
                 role_append=a.get("role_append", ""),
                 role=a.get("role", ""),
                 model=a.get("model"),
                 capabilities=a.get("capabilities"),
                 add_capabilities=a.get("add_capabilities"),
                 notes_folders=a.get("notes_folders"),
                 add_notes_folders=a.get("add_notes_folders"),
                 projects=a.get("projects"),
                 add_projects=a.get("add_projects"),
                 node=a.get("node"),
                 skills_folder=a.get("skills_folder"),
                 avatar=a.get("avatar"))


def tool_child_delete(a):
    return delete(a.get("name", ""))


CHILD_TOOLS = {
    "child_create": {
        "desc": ("Create a LOCAL child: a specialised agent of this user that "
                 "runs on THIS machine as a sub-agent. It has no identity, no "
                 "keys, no server and no quota — nothing to enroll. Use this for "
                 "«I want an agent for X» / «create me a child that…». (The other "
                 "kind, fed_spawn_child, is for a headless node on ANOTHER "
                 "machine and needs a token.) A child is a pack: 'role' (what it "
                 "is and how it works — this is what matters), 'model' (a pool "
                 "alias, empty = the same model as this turn), 'capabilities' "
                 "(which tools it gets), 'notes_folders' (the ONLY note folders "
                 "it may search, read and write), 'projects' (the TODO "
                 "projects whose pending tasks it may see, do and update), "
                 "'skills_folder', 'avatar' (one emoji). WARNING to pass on: local children run in PARALLEL, so "
                 "three tasks to three children compete for the same model server."),
        "fn": tool_child_create},
    "child_list": {
        "desc": ("List the LOCAL children (personalisation packs on this "
                 "machine) with their role, model, capabilities and folders. "
                 "Read this before teaching one, so you change what you meant."),
        "fn": tool_child_list},
    "child_teach": {
        "desc": ("Teach a local child by CHANGING NAMED FIELDS of its pack, and "
                 "get back the diff. This is what «give my child X the folder Y "
                 "and reinforce its role» means. 'role_append' ADDS to its role "
                 "(reinforcing is adding — use 'role' only to replace it "
                 "outright, which loses what was there). Lists come in two "
                 "flavours: 'add_notes_folders' adds, 'notes_folders' replaces "
                 "the whole list; same for capabilities. Everything you do not "
                 "pass stays as it was. Report the diff you get back to the "
                 "user — it is how they see what their sentence did."),
        "fn": tool_child_teach},
    "child_delete": {
        "desc": ("Delete a LOCAL child. Its pack goes; whatever it wrote (notes, "
                 "tasks, files) stays. Does not touch federated children — those "
                 "are revoked with fed_revoke_child."),
        "fn": tool_child_delete},
}

CHILD_SCHEMAS = {
    "child_create": {
        "properties": {
            "name": {"type": "string", "description": "How you will call it, "
                     "e.g. 'Researcher'. Letters, digits, space, '.', '_' "
                     "or '-'. No '@': that is a federated handle."},
            "role": {"type": "string", "description": "What it is and how it "
                     "works — the prompt it lives by. The most important field."},
            "model": {"type": "string", "description": "Pool alias to run it on. "
                      "Empty = whichever model the calling turn is on."},
            "capabilities": {"type": "array", "items": {"type": "string"},
                             "description": "Which tools it gets: web, research, "
                             "files, code, terminal, write, none."},
            "notes_folders": {"type": "array", "items": {"type": "string"},
                              "description": "Note folders it may search, read "
                              "and write. Empty = no restriction (it sees what "
                              "you see)."},
            "projects": {"type": "array", "items": {"type": "string"},
                         "description": "TODO projects whose tasks it may see, "
                         "create and update. Empty = no restriction."},
            "skills_folder": {"type": "string", "description": "Folder holding "
                              "its skills. Set = that IS its skill set."},
            "avatar": {"type": "string", "description": "One emoji."},
            "node": {"type": "string", "description": "Handle of a FEDERATED "
                     "child (fed_list_children) to run this pack on, instead of "
                     "here. Empty = here, as a sub-agent."},
        },
        "required": ["name"]},
    "child_list": {"properties": {}, "required": []},
    "child_teach": {
        "properties": {
            "name": {"type": "string", "description": "Which local child."},
            "role_append": {"type": "string", "description": "Text ADDED to its "
                            "role. This is the normal way to reinforce it."},
            "role": {"type": "string", "description": "REPLACES the role "
                     "entirely. Only when the user says to rewrite it."},
            "model": {"type": "string", "description": "New pool alias; empty "
                      "string means 'follow the calling turn'."},
            "capabilities": {"type": "array", "items": {"type": "string"},
                             "description": "REPLACES the capability list."},
            "add_capabilities": {"type": "array", "items": {"type": "string"},
                                 "description": "Adds to the capability list."},
            "notes_folders": {"type": "array", "items": {"type": "string"},
                              "description": "REPLACES its folders. Use for «have "
                              "it search ONLY in X»."},
            "add_notes_folders": {"type": "array", "items": {"type": "string"},
                                  "description": "Adds folders. Use for «give it X "
                                  "as a place to look»."},
            "projects": {"type": "array", "items": {"type": "string"},
                         "description": "REPLACES its TODO projects."},
            "add_projects": {"type": "array", "items": {"type": "string"},
                             "description": "Adds TODO projects it works in. "
                             "Use for «have it take care of X's tasks»."},
            "skills_folder": {"type": "string", "description": "Its skills "
                              "folder; empty string clears it."},
            "avatar": {"type": "string", "description": "One emoji."},
            "node": {"type": "string", "description": "Handle of a federated "
                     "child to run this pack ON — this is what «emancipate» "
                     "means, and it re-pushes on every save. Empty string "
                     "brings it back home."},
        },
        "required": ["name"]},
    "child_delete": {
        "properties": {"name": {"type": "string",
                                "description": "The local child to delete."}},
        "required": ["name"]},
}
