#!/usr/bin/env python3
import json
import os
import re
import uuid

import wb_document as D
import wb_store as S
import wb_vcs as V

_INSTR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wb_instructions")
TOPICS = ("authoring", "file-lenses", "scratchpad")


def _st():
    return S.store()


_HOOKS = {"category": None, "journal": None}


def set_project_hooks(category_of, journal):
    _HOOKS["category"], _HOOKS["journal"] = category_of, journal


def _project_of(repo_root):
    fn = _HOOKS["category"]
    try:
        return (fn(repo_root) if fn else os.path.basename(repo_root)) or ""
    except Exception:
        return os.path.basename(repo_root)


def _journal_if_due(wid):
    st = _st()
    v = st.journal_due(wid)
    if v is None or not _HOOKS["journal"]:
        return None
    snap = st.snapshot(wid)
    p = snap.get("pins") or {}
    try:
        root = st.repository(p["repositoryId"])["path"]
    except Exception:
        return None
    rng = f"{p.get('base', '')[:8]}..{'worktree' if p.get('head') == 'worktree' else p.get('head', '')[:8]}"
    verb = "updated" if st._one("SELECT 1 FROM journal_marks WHERE review_id=?", (wid,)) else "written"
    head = f"🧭 Whiteboard {verb}: «{snap.get('title')}» ({rng})"
    body = f"Open it on the CODE tab's whiteboard page, or wb_open(\"{wid}\")."
    try:
        r = _HOOKS["journal"](root, body, head)
    except Exception as e:
        return {"error": str(e)}
    if isinstance(r, dict) and r.get("skipped"):
        return None
    if not (isinstance(r, dict) and r.get("error")):
        st.journal_mark(wid, v)
    return r


def _public(obj):
    if isinstance(obj, dict):
        return {("whiteboardId" if k == "reviewId" else k): _public(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_public(x) for x in obj]
    if isinstance(obj, str):
        return obj.replace("reviewId", "whiteboardId")
    return obj


def _ok(fn):
    def run(*a, **kw):
        try:
            return _public(fn(*a, **kw))
        except (S.WbError, D.DocError, V.VcsError) as e:
            return {"error": _public(str(e)), "status": getattr(e, "status", 400)}
    run.__name__ = fn.__name__
    return run


def _need(**kw):
    for k, v in kw.items():
        if v in (None, "", {}):
            hint = (" — the id wb_create returned (\"scratchpad\" for the scratchpad)"
                    if k == "whiteboardId" else "")
            raise S.WbError(f"{k} is required{hint}.")


def _progress(key, **args):
    try:
        import fed_agent
        fed_agent.tool_progress(key, **args)
    except Exception:
        pass


def _open(wid, title=""):
    _progress("wb.open", id=wid, title=title or wid)
    return True



@_ok
def wb_capabilities():
    return {"desktopAvailable": True, "scratchpadEnabled": True,
            "structuralDiff": bool(V.diffr_binary()),
            "blockTypes": list(D.BLOCK_TYPES)}


@_ok
def wb_get_instructions(topic="authoring"):
    topic = topic or "authoring"
    if topic not in TOPICS:
        return {"error": f"topic must be one of {', '.join(TOPICS)}"}
    with open(os.path.join(_INSTR_DIR, f"{topic}.md"), encoding="utf-8") as f:
        return f.read()


@_ok
def wb_register_repository(path):
    _need(path=path)
    return _st().register_repository(path)


def _repo_arg(spec):
    if isinstance(spec, dict):
        rid = spec.get("repositoryId")
        if isinstance(rid, str) and (rid.startswith("/") or rid.startswith("~") or ":\\" in rid) \
                and os.path.isdir(os.path.expanduser(rid)):
            return {**spec, "repositoryId": _st().register_repository(os.path.expanduser(rid))["id"]}
    return spec


@_ok
def wb_resolve_pins(repositoryId, base, head):
    return _st().resolve_pins(repositoryId, base, head)


@_ok
def wb_create(title=None, target=None, pins=None, kind=None, open=True):
    target, pins = _repo_arg(target), _repo_arg(pins)
    op = {"type": "create"}
    for k, v in (("title", title), ("target", target), ("pins", pins), ("kind", kind)):
        if v is not None:
            op[k] = v
    repo_id = (target or pins or {}).get("repositoryId")
    if repo_id and kind != "scratchpad":
        try:
            op["project"] = _project_of(_st().repository(repo_id)["path"])
        except S.WbError:
            pass
    r = _st().command(op)
    if open is not False:
        r["opened"] = _open(r["reviewId"], title or "")
    if not _conversation_called("wb_get_instructions"):
        r["next"] = ("Read wb_get_instructions (the authoring workflow) before the first edit: "
                     "every claim about code carries a verified source range.")
    return r


@_ok
def wb_activity(whiteboardId, action, leaseId, scope="document", focus="__keep__"):
    _need(whiteboardId=whiteboardId, action=action, leaseId=leaseId)
    snap_ = _st().activity(whiteboardId, action, leaseId, scope or "document", focus)
    if action == "end" and (scope or "document") == "document":
        j = _journal_if_due(whiteboardId)
        if j is not None:
            snap_ = dict(snap_, journal="written" if not (isinstance(j, dict) and j.get("error")) else j)
    return snap_


def _cmd(op, commandId=None, leaseId=None):
    return _st().command(op, command_id=commandId, lease_id=leaseId)


def _leased(whiteboardId, leaseId):
    if whiteboardId or not leaseId:
        return whiteboardId
    row = _st()._one("SELECT review_id FROM leases WHERE lease_id=?", (leaseId,))
    return row[0] if row else whiteboardId


def _edit_type(edit):
    if not isinstance(edit, dict) or edit.get("type"):
        return edit
    if "content" in edit:
        return {"type": "replace" if edit.get("targetId") else "insert", **edit}
    if "changes" in edit and edit.get("targetId"):
        return {"type": "update", **edit}
    return edit


@_ok
def wb_edit(whiteboardId, edit, commandId=None, leaseId=None):
    whiteboardId = _leased(whiteboardId, leaseId)
    _need(whiteboardId=whiteboardId, edit=edit)
    return _cmd({"type": "edit", "reviewId": whiteboardId, "edit": _edit_type(edit)}, commandId, leaseId)


@_ok
def wb_lens_edit(whiteboardId, edit, commandId=None, leaseId=None):
    whiteboardId = _leased(whiteboardId, leaseId)
    _need(whiteboardId=whiteboardId, edit=edit)
    return _cmd({"type": "lens", "reviewId": whiteboardId, "edit": edit}, commandId, leaseId)


@_ok
def wb_lens_get(whiteboardId):
    _need(whiteboardId=whiteboardId)
    return _st().lenses_report(whiteboardId)


@_ok
def wb_get(whiteboardId, version=None, targetId=None, full=False, format="text"):
    _need(whiteboardId=whiteboardId)
    snap = _st().snapshot(whiteboardId, version)
    if format == "json":
        if targetId:
            return D.locate(snap["document"], targetId)[2]
        return snap
    return D.outline(snap, full=bool(full), target_id=targetId)


@_ok
def wb_list():
    return _st().catalog()


@_ok
def wb_history(whiteboardId):
    _need(whiteboardId=whiteboardId)
    return _st().history(whiteboardId)


@_ok
def wb_rename(whiteboardId, title, commandId=None, leaseId=None):
    _need(whiteboardId=whiteboardId, title=title)
    return _cmd({"type": "rename", "reviewId": whiteboardId, "title": title}, commandId, leaseId)


@_ok
def wb_repin(whiteboardId, pins, commandId=None, leaseId=None):
    _need(whiteboardId=whiteboardId, pins=pins)
    return _cmd({"type": "repin", "reviewId": whiteboardId, "pins": pins}, commandId, leaseId)


@_ok
def wb_set_target(whiteboardId, target, commandId=None, leaseId=None):
    _need(whiteboardId=whiteboardId, target=target)
    return _cmd({"type": "set_target", "reviewId": whiteboardId, "target": target}, commandId, leaseId)


@_ok
def wb_restore(whiteboardId, version, commandId=None, leaseId=None):
    _need(whiteboardId=whiteboardId)
    return _cmd({"type": "restore", "reviewId": whiteboardId, "version": version}, commandId, leaseId)


@_ok
def wb_delete(whiteboardId, commandId=None):
    _need(whiteboardId=whiteboardId)
    return _cmd({"type": "delete", "reviewId": whiteboardId}, commandId)


@_ok
def wb_attention(whiteboardId, action):
    _need(whiteboardId=whiteboardId, action=action)
    return _cmd({"type": "attention", "reviewId": whiteboardId, "action": action})


@_ok
def wb_open(whiteboardId):
    _need(whiteboardId=whiteboardId)
    snap = _st().snapshot(whiteboardId)
    _open(whiteboardId, snap.get("title", ""))
    return {"ok": True, "opened": True, "id": whiteboardId}


def _pins_for(snap, repositoryId=None, head=None, base=None):
    if repositoryId or head:
        if not (repositoryId and head):
            raise S.WbError("Reading at explicit pins needs both repositoryId and head.")
        st = _st()
        root = st.repository(repositoryId)["path"]
        return root, {"head": V.resolve(root, head), "base": V.resolve(root, base or head)}
    pins = snap.get("pins")
    if not pins:
        raise S.WbError("This whiteboard has no pins: pass repositoryId and head.")
    root = _st().repository(pins["repositoryId"])["path"]
    return root, {"head": pins["head"], "base": pins["base"]}


@_ok
def wb_diff(whiteboardId, paths=None, format="files", context=3, maxBytes=40000,
            repositoryId=None, head=None, base=None, commit=None):
    _need(whiteboardId=whiteboardId)
    snap = _st().snapshot(whiteboardId)
    if commit and repositoryId and not base:
        root = _st().repository(repositoryId)["path"]
        h = V.resolve(root, commit)
        try:
            b = V.resolve(root, h + "^")
        except V.VcsError:
            b = h
        if paths is not None and not isinstance(paths, list):
            paths = [paths]
        if format == "patch":
            return V.numbered_patch(root, b, h, paths, context, maxBytes) or "(no changes)"
        return V.changed_files(root, b, h, paths)
    root, c = _pins_for(snap, repositoryId, head, base)
    b, h = c["base"], c["head"]
    if commit:
        cm = next((x for x in V.commits(root, b, h) if x["commit"].startswith(commit)), None)
        if not cm:
            return {"error": "commit must be one of this whiteboard's commits (wb_commits)."}
        b, h = cm["parent"], cm["commit"]
    if paths is not None and not isinstance(paths, list):
        paths = [paths]
    if format == "patch":
        return V.numbered_patch(root, b, h, paths, context, maxBytes) or "(no changes)"
    return V.changed_files(root, b, h, paths)


@_ok
def wb_file(whiteboardId, file, side="head", repositoryId=None, head=None, base=None):
    _need(whiteboardId=whiteboardId, file=file)
    snap = _st().snapshot(whiteboardId)
    root, c = _pins_for(snap, repositoryId, head, base)
    text = V.read_text(root, c[side], file)
    return {"file": file, "side": side, "commit": c[side], "text": text}


@_ok
def wb_tree(whiteboardId, path="", side="head", repositoryId=None, head=None, base=None):
    _need(whiteboardId=whiteboardId)
    snap = _st().snapshot(whiteboardId)
    root, c = _pins_for(snap, repositoryId, head, base)
    return V.tree(root, c[side], path or "")


@_ok
def wb_source(whiteboardId, source):
    _need(whiteboardId=whiteboardId, source=source)
    snap = _st().snapshot(whiteboardId)
    s = source or {}
    side = s.get("side") or "head"
    pins = s.get("pins")
    if pins:
        pins = D.check_pins(pins, "source.pins")
    try:
        a, b = int(s.get("fromLine")), int(s.get("toLine"))
    except (TypeError, ValueError):
        raise S.WbError("source needs {file, fromLine, toLine} (numbers), and side/pins optionally.")
    lines = _st().read_range(snap, side, D.check_path(s.get("file")), a, b, pins)
    return {"side": side, "file": s.get("file"), "fromLine": a, "toLine": b,
            "text": "\n".join(f"{a + i:>5}  {ln}" for i, ln in enumerate(lines))}


@_ok
def wb_commits(whiteboardId):
    _need(whiteboardId=whiteboardId)
    snap = _st().snapshot(whiteboardId)
    root, c = _pins_for(snap)
    return V.commits(root, c["base"], c["head"])


@_ok
def wb_upload(kind, repositoryId=None, id=None, trace=None, base64=None, model=None,
              side=None, pins=None):
    res = {"kind": kind, "repositoryId": repositoryId or "", "id": id or str(uuid.uuid4())}
    for k, v in (("trace", trace), ("base64", base64), ("model", model),
                 ("side", side), ("pins", pins)):
        if v is not None:
            res[k] = v
    return _st().upload(res)


def _message_text(m):
    c = m.get("content")
    if isinstance(c, list):
        c = " ".join(p.get("text", "") for p in c if isinstance(p, dict))
    return str(c or "")


@_ok
def wb_trace_capture(label=None, repositoryId=None):
    try:
        import fed_agent
        messages = fed_agent.conversation_for_tool()
        is_harness = fed_agent.is_harness
        user_said = fed_agent.user_said
    except Exception:
        messages, is_harness, user_said = [], (lambda _m: False), _message_text
    events = []
    for i, m in enumerate(messages):
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role == "system" or is_harness(m):
            continue
        text = (user_said(m) if role == "user" else _message_text(m)).strip()
        if not text:
            continue
        if role == "user":
            ev_role = "user"
        elif role == "assistant":
            ev_role = "assistant"
        elif role == "tool":
            ev_role = "tool"
            text = text[:2000]
        else:
            continue
        events.append({"id": str(i), "role": ev_role, "text": text})
    if not events:
        return {"error": "No conversation to capture: this runs inside a turn."}
    first_user = next((e["text"] for e in events if e["role"] == "user"), "conversation")
    res = _st().upload({"kind": "trace", "repositoryId": repositoryId or "",
                        "trace": {"label": label or first_user[:80], "events": events}})
    listing = [{"eventId": e["id"], "role": e["role"], "text": e["text"][:300]}
               for e in events if e["role"] in ("user", "assistant")]
    return {"traceId": res["id"], "events": listing,
            "note": "Quote with {type:trace_quote, traceId, eventId, text} or "
                    "[verbatim words](review-trace:<traceId>#<eventId>). The words must "
                    "appear in that event."}



_CMD = (" commandId, when given, must be a fresh UUID per operation (reuse it with "
        "identical input when retrying a lost response: it will not edit twice). "
        "For content changes include the leaseId from wb_activity.")

_EDIT_DESC = (
    "Edit a whiteboard component. Common content shapes: section "
    '`{"type":"section","title":"…","children":[]}`; prose `{"type":"markdown","markdown":"…"}`. '
    "Sections require `children` (not `blocks`); Markdown uses `markdown` (not `text`). To populate "
    "a section later, insert content with `edit.parentId` set to its returned ID. Flow diagram: "
    '`{"type":"flow_diagram","title":"Flow","nodes":[{"key":"a","label":"Start"},{"key":"b","label":"Finish"}],'
    '"edges":[{"from":"a","to":"b"}]}`. Use nodes and edges arrays (not children); at least one node is '
    "required, edges may be empty, and edge endpoints name unique node keys; a node may carry "
    '`attachments:[{"label":"…","sources":[<source>]}]` and `kind: process|decision|terminal`. Code peek: '
    '`{"type":"code_peek","source":{"file":"src/app.ts","start":{"side":"head","line":10},"end":{"side":"head","line":20}}}`. '
    'Call stack diff: `{"type":"call_stack_diff","title":"Request flow","base":[{"label":"Entry","source":<base source>}],'
    '"head":[{"label":"Entry","source":<head source>}]}`. Both base and head arrays are required; either may be '
    "empty. Each frame requires source; optional key/parentKey express nesting, and parentKey must name an "
    "earlier frame on that side; via:{kind:call|queue|callback|rpc, reason} explains the hop. Sequence: "
    '`{"type":"sequence","title":"Request","actors":{"a":"Client","b":"Server"},"steps":[{"from":"a","to":"b",'
    '"label":"Send","explanation":"Client sends a request."}]}`. actors is a key-to-label object; steps reference '
    "actor keys, style call|return|async, and each needs exactly one of source, explanation, or code. Database "
    'lens: `{"type":"database_lens","title":"Read items","actors":{"app":"App"},"stores":{"db":{"label":"DB",'
    '"storage":"relational","collections":{"items":{"label":"Items","fields":{"id":{"label":"ID","dataType":"integer"}}}}}},'
    '"useCases":[{"label":"Load","operations":[{"kind":"read","actor":"app","store":"db","collection":"items",'
    '"label":"Fetch items","source":<source>}]}]}`. actors, stores, collections, and fields are keyed objects; '
    "useCases and operations are arrays; each operation requires source and must reference declared keys. Also: "
    '`{"type":"callout","tone":"info|warning|danger|success","title":"…","children":[]}`, '
    '`{"type":"code","language":"py","text":"…"}`, `{"type":"divider"}`, '
    '`{"type":"trace_quote","traceId":"…","eventId":"…","text":"verbatim words"}`. A source is '
    '`{"file":"path","start":{"side":"head","line":10},"end":{"side":"head","line":20}}`: paths are '
    "repository-relative, lines 1-based and inclusive, checked against the file at the pinned commit (a range "
    "past the file, or a peek of blank lines, rejects the edit). Replace example paths and lines with verified "
    "ranges (wb_diff format:patch prints them). The host assigns short durable IDs; use returned IDs to edit "
    "components in place, never send ids yourself. The result identifies the edited component and, for an "
    "insert or replace, its first-level children. Edit types: insert {content, parentId?, afterId?}, replace "
    "{targetId, content}, update {targetId, changes} (shallow; null removes an optional field; structural keys "
    "like steps/nodes/actors need replace), move {targetId, parentId?, afterId?}, remove {targetId}. Omitted "
    "placement appends; on the scratchpad it lands at the top. While a reader may be watching, write small and "
    "often: one paragraph per edit, so the document draws itself as you go. Insert a new diagram whole; change "
    "a diagram already on the board one unit at a time: insert, update or remove a flow_node, flow_edge or step "
    "by ID (parentId names the diagram). Insert a flow_node with `link:{from|to: <existing key>, label?}` so it "
    "arrives attached; removing a flow_node removes its edges.") + _CMD

_T = {
    "wb_capabilities": ("Discover what the whiteboard offers here: scratchpad, structural diff (diffr), block types. Read before authoring.", {}),
    "wb_get_instructions": ("Read the whiteboard guidance before creating or editing a whiteboard. The default topic "
                            "gives the authoring workflow; \"file-lenses\" covers Diff-view lenses; \"scratchpad\" "
                            "covers drawing an explanation that is not about a change. When the user asks to be "
                            "shown how code works or wants a diagram of code, draw it on the scratchpad.",
                            {"topic": {"type": "string", "enum": list(TOPICS)}}),
    "wb_register_repository": ("Register a local Git repository (any path inside it). Returns {id, name}.",
                               {"path": {"type": "string"}}, ["path"]),
    "wb_resolve_pins": ("Resolve base and head revisions (branch, HEAD~1, sha) to immutable commit IDs for create or repin.",
                        {"repositoryId": {"type": "string"}, "base": {"type": "string"}, "head": {"type": "string"}},
                        ["repositoryId", "base", "head"]),
    "wb_create": ("Create a whiteboard of immutable commits or of the working files. target: "
                  "{kind:\"commits\", repositoryId, head, base?} (revisions are resolved on acceptance; omitted base "
                  "means source at head with no diff — supply the parent to review introduced changes) or "
                  "{kind:\"worktree\", repositoryId, base?} (uncommitted work against HEAD). The whiteboard opens in "
                  "HOPLITA's code tab (its whiteboard page) unless open:false. Returns reviewId (use it as whiteboardId), the pins "
                  "and the repository path, so no follow-up read is needed before diffing.",
                  {"title": {"type": "string"}, "target": {"type": "object"}, "pins": {"type": "object"},
                   "kind": {"type": "string", "enum": ["scratchpad"]}, "open": {"type": "boolean"}}),
    "wb_activity": ("Acquire an exclusive authoring session for one scope of a whiteboard: begin with a fresh leaseId "
                    "(any UUID you make up), pass that leaseId on every write in that scope, and end when finished. "
                    "Scopes: \"document\" (wb_edit and every other document write) and \"lenses\" (wb_lens_edit). "
                    "The lease expires after 10 minutes without an accepted edit or renewal; renew during long reads. "
                    "Use focus {description (≤160 chars), targetId?} to show the reader what you are working on; "
                    "omitted focus keeps it, null clears it. End the session only when the whiteboard is finished: "
                    "readers treat a whiteboard with content and no live session as ready.",
                    {"whiteboardId": {"type": "string"}, "action": {"type": "string", "enum": ["begin", "renew", "end"]},
                     "leaseId": {"type": "string"}, "scope": {"type": "string", "enum": ["document", "lenses"]},
                     "focus": {"type": ["object", "null"]}}, ["whiteboardId", "action", "leaseId"]),
    "wb_edit": (_EDIT_DESC, {"whiteboardId": {"type": "string"},
                             "edit": {"type": "object", "required": ["type"], "properties": {
                                 "type": {"type": "string", "enum": ["insert", "replace", "update", "move", "remove"]},
                                 "content": {"type": "object", "description": "the block, with its own type"},
                                 "targetId": {"type": "string"}, "parentId": {"type": "string"},
                                 "afterId": {"type": "string"}, "changes": {"type": "object"}}},
                             "commandId": {"type": "string"}, "leaseId": {"type": "string"}}, ["whiteboardId", "edit"]),
    "wb_lens_edit": ("Edit one Diff-view lens. Lenses partition the whiteboard's change for the Diff view; they sit "
                     "beside the document (never in it) and version with it. edit: {type:\"insert\", title, targets, "
                     "afterId?} | {type:\"update\", targetId, title?, targets?} | {type:\"remove\", targetId}. targets: "
                     "[{kind:\"files\", patterns:[paths or globs]}] or [{kind:\"ranges\", sources:[<source>]}]. Write "
                     "one lens per call. Requires the lenses lease (wb_activity scope:\"lenses\"). The result reports "
                     "uncategorized: changed lines no lens selects yet, by file. Keep adding lenses until it is empty." + _CMD,
                     {"whiteboardId": {"type": "string"}, "edit": {"type": "object"},
                      "commandId": {"type": "string"}, "leaseId": {"type": "string"}}, ["whiteboardId", "edit"]),
    "wb_lens_get": ("Read the whiteboard's lenses (ids, titles, targets, fileCount) and what is still uncategorized.",
                    {"whiteboardId": {"type": "string"}}, ["whiteboardId"]),
    "wb_get": ("Read a whiteboard as a nested text outline with editable IDs. targetId reads one component in full; "
               "full:true reads all content; format:\"json\" for raw data.",
               {"whiteboardId": {"type": "string"}, "version": {"type": "integer"}, "targetId": {"type": "string"},
                "full": {"type": "boolean"}, "format": {"type": "string", "enum": ["text", "json"]}}, ["whiteboardId"]),
    "wb_list": ("List saved whiteboards.", {}),
    "wb_history": ("List a whiteboard's saved versions.", {"whiteboardId": {"type": "string"}}, ["whiteboardId"]),
    "wb_rename": ("Change a whiteboard's title." + _CMD, {"whiteboardId": {"type": "string"}, "title": {"type": "string"},
                                                          "commandId": {"type": "string"}, "leaseId": {"type": "string"}},
                  ["whiteboardId", "title"]),
    "wb_repin": ("Update the whiteboard's pins {repositoryId, base, head} (commit IDs from wb_resolve_pins), keeping "
                 "the document and its IDs. Returns warnings for source ranges to verify; fix them with wb_edit." + _CMD,
                 {"whiteboardId": {"type": "string"}, "pins": {"type": "object"},
                  "commandId": {"type": "string"}, "leaseId": {"type": "string"}}, ["whiteboardId", "pins"]),
    "wb_set_target": ("Change the whiteboard's target (commits or worktree), keeping the document and its IDs." + _CMD,
                      {"whiteboardId": {"type": "string"}, "target": {"type": "object"},
                       "commandId": {"type": "string"}, "leaseId": {"type": "string"}}, ["whiteboardId", "target"]),
    "wb_restore": ("Restore title, pins and content from a saved version (as a new version)." + _CMD,
                   {"whiteboardId": {"type": "string"}, "version": {"type": "integer"},
                    "commandId": {"type": "string"}, "leaseId": {"type": "string"}}, ["whiteboardId", "version"]),
    "wb_delete": ("Permanently delete a whiteboard and its history.", {"whiteboardId": {"type": "string"},
                                                                       "commandId": {"type": "string"}}, ["whiteboardId"]),
    "wb_attention": ("Mark a whiteboard viewed, dismissed or restored without changing its content.",
                     {"whiteboardId": {"type": "string"}, "action": {"type": "string", "enum": ["view", "dismiss", "restore"]}},
                     ["whiteboardId", "action"]),
    "wb_open": ("Show a whiteboard on HOPLITA's code tab (its whiteboard page) now. Later edits appear live; call it once.",
                {"whiteboardId": {"type": "string"}}, ["whiteboardId"]),
    "wb_diff": ("Read the whiteboard's changes. format:\"files\" (default) lists them with status and counts; "
                "format:\"patch\" returns plain-text patches with base and head line numbers on every line, ready for "
                "review-source links and sources. paths selects files; commit selects one of its commits; "
                "repositoryId/base/head compare explicit pins instead. Patches past maxBytes are listed with a paths hint.",
                {"whiteboardId": {"type": "string"}, "paths": {"type": "array", "items": {"type": "string"}},
                 "format": {"type": "string", "enum": ["files", "patch"]}, "context": {"type": "integer"},
                 "maxBytes": {"type": "integer"}, "commit": {"type": "string"}, "repositoryId": {"type": "string"},
                 "head": {"type": "string"}, "base": {"type": "string"}}, ["whiteboardId"]),
    "wb_file": ("Read a complete source file at the whiteboard's pins (side base|head), or at explicit pins "
                "(repositoryId + head).",
                {"whiteboardId": {"type": "string"}, "file": {"type": "string"}, "side": {"type": "string", "enum": ["base", "head"]},
                 "repositoryId": {"type": "string"}, "head": {"type": "string"}, "base": {"type": "string"}},
                ["whiteboardId", "file"]),
    "wb_tree": ("List immediate directory entries at the whiteboard's pins (or explicit pins).",
                {"whiteboardId": {"type": "string"}, "path": {"type": "string"}, "side": {"type": "string", "enum": ["base", "head"]},
                 "repositoryId": {"type": "string"}, "head": {"type": "string"}, "base": {"type": "string"}}, ["whiteboardId"]),
    "wb_source": ("Read an exact code range {side, file, fromLine, toLine, pins?} with line numbers, at the whiteboard's pins.",
                  {"whiteboardId": {"type": "string"}, "source": {"type": "object"}}, ["whiteboardId", "source"]),
    "wb_commits": ("List the commits in the whiteboard's pinned comparison.", {"whiteboardId": {"type": "string"}}, ["whiteboardId"]),
    "wb_upload": ("Retain a trace, a PNG image or a software map for use in a whiteboard. kind:\"trace\" with "
                  "trace:{label, events:[{id, role:user|assistant|tool, text}]}; kind:\"image\" with base64; kind:\"map\" "
                  "with model:{systems:{key:{label, containers:{key:{label, components:{key:{label, coverage:{files:[…]}}}}}}}, "
                  "relationships:[{from, to, label}]} and side. Reusing an id requires identical content.",
                  {"kind": {"type": "string", "enum": ["trace", "image", "map"]}, "repositoryId": {"type": "string"},
                   "id": {"type": "string"}, "trace": {"type": "object"}, "base64": {"type": "string"},
                   "model": {"type": "object"}, "side": {"type": "string"}, "pins": {"type": "object"}}, ["kind"]),
    "wb_trace_capture": ("Retain THIS conversation as a trace resource, to quote the user's own words in the whiteboard "
                         "(requirements) and your earlier decisions. Returns traceId and the events with their ids.",
                         {"label": {"type": "string"}, "repositoryId": {"type": "string"}}),
}

_FN = {
    "wb_capabilities": lambda a: wb_capabilities(),
    "wb_get_instructions": lambda a: wb_get_instructions(a.get("topic") or "authoring"),
    "wb_register_repository": lambda a: wb_register_repository(a.get("path")),
    "wb_resolve_pins": lambda a: wb_resolve_pins(a.get("repositoryId"), a.get("base"), a.get("head")),
    "wb_create": lambda a: wb_create(a.get("title"), a.get("target"), a.get("pins"), a.get("kind"), a.get("open", True)),
    "wb_activity": lambda a: wb_activity(a.get("whiteboardId"), a.get("action"), a.get("leaseId"),
                                         a.get("scope") or "document", a["focus"] if "focus" in a else "__keep__"),
    "wb_edit": lambda a: wb_edit(a.get("whiteboardId"), a.get("edit"), a.get("commandId"), a.get("leaseId")),
    "wb_lens_edit": lambda a: wb_lens_edit(a.get("whiteboardId"), a.get("edit"), a.get("commandId"), a.get("leaseId")),
    "wb_lens_get": lambda a: wb_lens_get(a.get("whiteboardId")),
    "wb_get": lambda a: wb_get(a.get("whiteboardId"), a.get("version"), a.get("targetId"), a.get("full", False),
                               a.get("format") or "text"),
    "wb_list": lambda a: wb_list(),
    "wb_history": lambda a: wb_history(a.get("whiteboardId")),
    "wb_rename": lambda a: wb_rename(a.get("whiteboardId"), a.get("title"), a.get("commandId"), a.get("leaseId")),
    "wb_repin": lambda a: wb_repin(a.get("whiteboardId"), a.get("pins"), a.get("commandId"), a.get("leaseId")),
    "wb_set_target": lambda a: wb_set_target(a.get("whiteboardId"), a.get("target"), a.get("commandId"), a.get("leaseId")),
    "wb_restore": lambda a: wb_restore(a.get("whiteboardId"), a.get("version"), a.get("commandId"), a.get("leaseId")),
    "wb_delete": lambda a: wb_delete(a.get("whiteboardId"), a.get("commandId")),
    "wb_attention": lambda a: wb_attention(a.get("whiteboardId"), a.get("action")),
    "wb_open": lambda a: wb_open(a.get("whiteboardId")),
    "wb_diff": lambda a: wb_diff(a.get("whiteboardId"), a.get("paths"), a.get("format") or "files",
                                 a.get("context", 3), a.get("maxBytes", 40000), a.get("repositoryId"),
                                 a.get("head"), a.get("base"), a.get("commit")),
    "wb_file": lambda a: wb_file(a.get("whiteboardId"), a.get("file"), a.get("side") or "head",
                                 a.get("repositoryId"), a.get("head"), a.get("base")),
    "wb_tree": lambda a: wb_tree(a.get("whiteboardId"), a.get("path") or "", a.get("side") or "head",
                                 a.get("repositoryId"), a.get("head"), a.get("base")),
    "wb_source": lambda a: wb_source(a.get("whiteboardId"), a.get("source")),
    "wb_commits": lambda a: wb_commits(a.get("whiteboardId")),
    "wb_upload": lambda a: wb_upload(a.get("kind"), a.get("repositoryId"), a.get("id"), a.get("trace"),
                                     a.get("base64"), a.get("model"), a.get("side"), a.get("pins")),
    "wb_trace_capture": lambda a: wb_trace_capture(a.get("label"), a.get("repositoryId")),
}


_WID_IN_TEXT = re.compile(r'"?(?:whiteboardId|reviewId)"?\s*[:=]\s*"([0-9a-f]{8}-[0-9a-f-]{27}|scratchpad)"')


def _conversation_board():
    try:
        import fed_agent
        msgs = fed_agent.conversation_for_tool()
    except Exception:
        return None
    for m in reversed(msgs):
        texts = [m.get("content") if isinstance(m.get("content"), str) else ""]
        for tc in (m.get("tool_calls") or []):
            texts.append(str(((tc or {}).get("function") or {}).get("arguments") or ""))
        for tx in reversed(texts):
            for wid in reversed(_WID_IN_TEXT.findall(tx or "")):
                if _st().exists(wid) or wid == S.SCRATCHPAD:
                    return wid
    return None


def _conversation_called(name):
    try:
        import fed_agent
        msgs = fed_agent.conversation_for_tool()
    except Exception:
        return True
    if not msgs:
        return True
    return any(((tc or {}).get("function") or {}).get("name") == name
               for m in msgs for tc in (m.get("tool_calls") or []))


def _conversation_lease(wid):
    try:
        import fed_agent
        msgs = fed_agent.conversation_for_tool()
    except Exception:
        return None
    held = {r[0] for r in _st()._db.execute(
        "SELECT lease_id FROM leases WHERE review_id=?", (wid,)).fetchall()}
    for m in reversed(msgs):
        for tc in reversed(m.get("tool_calls") or []):
            f = (tc or {}).get("function") or {}
            if f.get("name") != "wb_activity":
                continue
            try:
                a = json.loads(f.get("arguments") or "{}") if isinstance(f.get("arguments"), str) \
                    else (f.get("arguments") or {})
            except Exception:
                continue
            if a.get("leaseId") in held:
                return a["leaseId"]
    return None


def _with_board(name, fn):
    if "whiteboardId" not in _T[name][1]:
        return fn

    takes_lease = "leaseId" in _T[name][1] and name != "wb_activity"

    def run(a):
        a = dict(a or {})
        named = bool(a.get("whiteboardId"))
        wid = a.get("whiteboardId") or _leased(None, a.get("leaseId")) or _conversation_board()
        if wid and takes_lease and not a.get("leaseId"):
            lease = _conversation_lease(wid)
            if lease:
                a["leaseId"] = lease
        if named or not wid:
            return fn(a)
        a["whiteboardId"] = wid
        r = fn(a)
        if isinstance(r, dict) and not r.get("error"):
            r = {"whiteboardId": wid, **r}
        elif isinstance(r, str):
            r = f"(whiteboardId {wid})\n{r}"
        return r
    return run


def tools():
    return {n: {"desc": spec[0], "fn": _with_board(n, _FN[n])} for n, spec in _T.items()}


def tool_params():
    out = {}
    for n, spec in _T.items():
        props = spec[1]
        req = spec[2] if len(spec) > 2 else []
        out[n] = {"properties": props, "required": req}
    return out
