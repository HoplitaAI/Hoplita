#!/usr/bin/env python3
import copy
import re
from urllib.parse import unquote


class DocError(Exception):

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status



def _label(v, where):
    if not isinstance(v, str) or not v.strip():
        raise DocError(f"{where} must be a non-empty string.")
    return v.strip()


def _opt_str(v, where):
    if v is None:
        return None
    if not isinstance(v, str):
        raise DocError(f"{where} must be a string.")
    return v


def _strict(obj, where, allowed):
    if not isinstance(obj, dict):
        raise DocError(f"{where} must be an object.")
    extra = sorted(set(obj) - set(allowed))
    if extra:
        raise DocError(f"{where}: unknown field(s) {', '.join(extra)}. "
                       f"Allowed: {', '.join(sorted(allowed))}.")


def _enum(v, where, options, default=None):
    if v is None:
        if default is None:
            raise DocError(f"{where} is required ({'|'.join(options)}).")
        return default
    if v not in options:
        raise DocError(f"{where} must be one of {'|'.join(options)}.")
    return v


def _pos_int(v, where):
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        raise DocError(f"{where} must be a positive integer.")
    return v


def _keyed(obj, where):
    if not isinstance(obj, dict) or not obj:
        raise DocError(f"{where} must be a non-empty object keyed by name.")
    for k in obj:
        _label(k, f"{where} key")
    return obj



SIDES = ("base", "head")


def check_path(path):
    path = str(path or "").strip()
    if (not path or path.startswith("/") or "\\" in path
            or any(s in (".", "..") for s in path.split("/"))
            or any(ord(c) < 32 for c in path)):
        raise DocError("Source file must be a repository-relative path.")
    return path


def check_pins(p, where):
    if p is None:
        return None
    _strict(p, where, ("repositoryId", "head", "base"))
    out = {"repositoryId": _label(p.get("repositoryId"), f"{where}.repositoryId"),
           "head": _label(p.get("head"), f"{where}.head")}
    if p.get("base") is not None:
        out["base"] = _label(p.get("base"), f"{where}.base")
    return out


def check_selection(sel, where):
    _strict(sel, where, ("file", "start", "end", "pins"))
    out = {"file": check_path(sel.get("file"))}
    for end in ("start", "end"):
        e = sel.get(end)
        _strict(e, f"{where}.{end}", ("side", "line"))
        out[end] = {"side": _enum(e.get("side"), f"{where}.{end}.side", SIDES, "head"),
                    "line": _pos_int(e.get("line"), f"{where}.{end}.line")}
    if out["start"]["side"] == out["end"]["side"] and out["end"]["line"] < out["start"]["line"]:
        raise DocError("Source range ends before it starts.")
    pins = check_pins(sel.get("pins"), f"{where}.pins")
    if pins is not None:
        if "base" not in pins and "base" in (out["start"]["side"], out["end"]["side"]):
            raise DocError("A base-side endpoint needs base pins.")
        out["pins"] = pins
    return out


def selection_anchors(sel):
    s, e = sel["start"], sel["end"]
    if s["side"] == e["side"]:
        return [(s["side"], sel["file"], s["line"], e["line"])]
    return [(s["side"], sel["file"], s["line"], s["line"]),
            (e["side"], sel["file"], e["line"], e["line"])]



SOURCE_LINK = re.compile(r"^review-source:(base|head)/(.+)#L(\d+)(?:-L(\d+))?$", re.I)
TRACE_LINK = re.compile(r"^review-trace:([^#]+)#(.+)$")
_FENCE = re.compile(r"(^|\n)(```|~~~).*?(\n\2[^\n]*|\Z)", re.S)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_INLINE_LINK = re.compile(r"\[((?:[^\[\]]|\[[^\]]*\])*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_REF_LINK = re.compile(r"\[((?:[^\[\]]|\[[^\]]*\])*)\]\[([^\]]*)\]")
_REF_DEF = re.compile(r"^[ ]{0,3}\[([^\]]+)\]:\s*<?(\S+?)>?(?:\s+.*)?$", re.M)


def markdown_links(md):
    body = _FENCE.sub("\n", md or "")
    body = _INLINE_CODE.sub("", body)
    defs = {m.group(1).strip().lower(): m.group(2) for m in _REF_DEF.finditer(body)}
    out = [(m.group(1), m.group(2)) for m in _INLINE_LINK.finditer(body)]
    for m in _REF_LINK.finditer(body):
        key = (m.group(2) or m.group(1)).strip().lower()
        if key in defs:
            out.append((m.group(1), defs[key]))
    return out


def parse_source_link(href):
    m = SOURCE_LINK.match(href)
    if not m:
        raise DocError("Use review-source:head/path#L10-L24 (or base) for a source link.")
    try:
        path = unquote(m.group(2), errors="strict")
    except UnicodeDecodeError:
        raise DocError("Invalid URL encoding in source link.")
    a = int(m.group(3))
    b = int(m.group(4) or m.group(3))
    if a < 1 or b < a:
        raise DocError("Source range ends before it starts.")
    return {"side": m.group(1).lower(), "file": check_path(path),
            "fromLine": a, "toLine": b}


def check_markdown_links(md, block_id="?"):
    for _text, href in markdown_links(md):
        if href.startswith(("http://", "https://", "mailto:", "#")):
            continue
        if href.lower().startswith("review-trace:"):
            if not TRACE_LINK.match(href):
                raise DocError("Invalid trace quote link.")
            continue
        if href.lower().startswith("review-source:"):
            parse_source_link(href)
            continue
        raise DocError(
            f'Unsupported Markdown link "{href}" in block {block_id}. Use '
            f"[label](review-source:head/path#L10-L24) or "
            f"review-source:base/path#L10-L24 for repository files, with a "
            f"repository-relative path and verified line numbers. External "
            f"links must use https://, http://, or mailto:; document anchors "
            f"use #heading.")



CONTAINERS = ("section", "callout")
LEAVES = ("markdown", "code", "divider", "code_peek", "sequence",
          "call_stack_diff", "database_lens", "image", "trace_quote",
          "software_map", "flow_diagram")
BLOCK_TYPES = CONTAINERS + LEAVES
UNITS = ("step", "flow_node", "flow_edge")
PREFIX = {"sequence": "diagram", "flow_diagram": "diagram", "step": "step",
          "flow_node": "node", "flow_edge": "edge"}


def _check_code(c, where):
    _strict(c, where, ("language", "text"))
    return {"language": _opt_str(c.get("language"), f"{where}.language") or "text",
            "text": _opt_str(c.get("text"), f"{where}.text") or ""}


def check_step(s, where="step"):
    _strict(s, where, ("id", "type", "from", "to", "label", "style", "source",
                       "explanation", "code"))
    out = {"type": "step", "from": _label(s.get("from"), f"{where}.from"),
           "to": _label(s.get("to"), f"{where}.to"),
           "label": _label(s.get("label"), f"{where}.label"),
           "style": _enum(s.get("style"), f"{where}.style",
                          ("call", "return", "async"), "call")}
    if s.get("id"):
        out["id"] = s["id"]
    n = 0
    if s.get("source") is not None:
        out["source"] = check_selection(s["source"], f"{where}.source"); n += 1
    if s.get("explanation") is not None:
        out["explanation"] = _label(s["explanation"], f"{where}.explanation"); n += 1
    if s.get("code") is not None:
        out["code"] = _check_code(s["code"], f"{where}.code"); n += 1
    if n != 1:
        raise DocError("A step needs exactly one of source, explanation, or code.")
    return out


def check_flow_node(nd, where="flow_node", allow_link=False):
    allowed = ["id", "type", "key", "label", "description", "kind", "attachments"]
    if allow_link:
        allowed.append("link")
    _strict(nd, where, allowed)
    out = {"type": "flow_node", "key": _label(nd.get("key"), f"{where}.key"),
           "label": _label(nd.get("label"), f"{where}.label"), "attachments": []}
    if nd.get("id"):
        out["id"] = nd["id"]
    if nd.get("description") is not None:
        out["description"] = _opt_str(nd["description"], f"{where}.description")
    if nd.get("kind") is not None:
        out["kind"] = _enum(nd["kind"], f"{where}.kind", ("process", "decision", "terminal"))
    for i, a in enumerate(nd.get("attachments") or []):
        _strict(a, f"{where}.attachments[{i}]", ("label", "sources"))
        srcs = a.get("sources")
        if not isinstance(srcs, list) or not srcs:
            raise DocError(f"{where}.attachments[{i}].sources needs at least one source.")
        out["attachments"].append({
            "label": _label(a.get("label"), f"{where}.attachments[{i}].label"),
            "sources": [check_selection(x, f"{where}.attachments[{i}].sources[{j}]")
                        for j, x in enumerate(srcs)]})
    if allow_link and nd.get("link") is not None:
        lk = nd["link"]
        _strict(lk, f"{where}.link", ("from", "to", "label", "style"))
        if (lk.get("from") is None) == (lk.get("to") is None):
            raise DocError("A link names exactly one of from or to: the node already on the board.")
        out["link"] = {k: lk[k] for k in ("from", "to", "label", "style") if lk.get(k) is not None}
    return out


def check_flow_edge(e, where="flow_edge"):
    _strict(e, where, ("id", "type", "from", "to", "label", "style"))
    out = {"type": "flow_edge", "from": _label(e.get("from"), f"{where}.from"),
           "to": _label(e.get("to"), f"{where}.to")}
    if e.get("id"):
        out["id"] = e["id"]
    if e.get("label") is not None:
        out["label"] = _opt_str(e["label"], f"{where}.label")
    if e.get("style") is not None:
        out["style"] = _enum(e["style"], f"{where}.style", ("solid", "dashed"))
    return out


def _check_frame(f, where):
    _strict(f, where, ("id", "key", "parentKey", "callSite", "source",
                       "contextSources", "label", "via"))
    out = {"source": check_selection(f.get("source"), f"{where}.source")}
    if f.get("id"):
        out["id"] = f["id"]
    if f.get("key") is not None:
        out["key"] = _label(f["key"], f"{where}.key")
    if "parentKey" in f:
        out["parentKey"] = None if f["parentKey"] is None else _label(f["parentKey"], f"{where}.parentKey")
    if f.get("callSite") is not None:
        out["callSite"] = check_selection(f["callSite"], f"{where}.callSite")
    if f.get("contextSources") is not None:
        cs = f["contextSources"]
        if not isinstance(cs, list) or len(cs) > 1000:
            raise DocError(f"{where}.contextSources must be a list (≤1000).")
        out["contextSources"] = [check_selection(x, f"{where}.contextSources[{i}]")
                                 for i, x in enumerate(cs)]
    if f.get("label") is not None:
        out["label"] = _label(f["label"], f"{where}.label")
    if f.get("via") is not None:
        v = f["via"]
        _strict(v, f"{where}.via", ("kind", "reason"))
        out["via"] = {"kind": _enum(v.get("kind"), f"{where}.via.kind",
                                    ("call", "queue", "callback", "rpc")),
                      "reason": _label(v.get("reason"), f"{where}.via.reason")}
    return out


def _check_field(fd, where):
    _strict(fd, where, ("label", "dataType", "nullable", "primaryKey",
                        "references", "example", "fields"))
    out = {"label": _label(fd.get("label"), f"{where}.label"),
           "dataType": _label(fd.get("dataType"), f"{where}.dataType")}
    for k in ("nullable", "primaryKey"):
        if fd.get(k) is not None:
            if not isinstance(fd[k], bool):
                raise DocError(f"{where}.{k} must be true or false.")
            out[k] = fd[k]
    if fd.get("references") is not None:
        r = fd["references"]
        _strict(r, f"{where}.references", ("store", "collection", "field"))
        out["references"] = {k: _label(r.get(k), f"{where}.references.{k}")
                             for k in ("store", "collection", "field")}
    if "example" in fd:
        out["example"] = fd["example"]
    if fd.get("fields") is not None:
        out["fields"] = {k: _check_field(v, f"{where}.fields.{k}")
                         for k, v in _keyed(fd["fields"], f"{where}.fields").items()}
    return out


def check_block(b, where="block"):
    if not isinstance(b, dict):
        raise DocError(f"{where} must be an object.")
    t = b.get("type")
    if t in UNITS:
        raise DocError(f"A {t} belongs inside a {'sequence' if t == 'step' else 'flow_diagram'}.")
    if t not in BLOCK_TYPES:
        raise DocError(f"{where}.type must be one of {', '.join(BLOCK_TYPES)}.")
    where = f"{where}({t})"
    ident = {"id": b["id"]} if b.get("id") else {}
    if t == "section":
        if "children" not in b:
            raise DocError("Sections require `children` (not `blocks`).")
        _strict(b, where, ("id", "type", "title", "defaultCollapsed", "children"))
        out = {**ident, "type": t, "title": _label(b.get("title"), f"{where}.title"),
               "children": _children(b.get("children"), where)}
        if b.get("defaultCollapsed") is not None:
            out["defaultCollapsed"] = bool(b["defaultCollapsed"])
        return out
    if t == "callout":
        _strict(b, where, ("id", "type", "title", "tone", "children"))
        out = {**ident, "type": t,
               "tone": _enum(b.get("tone"), f"{where}.tone",
                             ("info", "warning", "danger", "success"), "info"),
               "children": _children(b.get("children"), where)}
        if b.get("title") is not None:
            out["title"] = _label(b["title"], f"{where}.title")
        return out
    if t == "markdown":
        if "markdown" not in b:
            raise DocError("Markdown uses `markdown` (not `text`).")
        _strict(b, where, ("id", "type", "markdown", "pins"))
        md = _opt_str(b.get("markdown"), f"{where}.markdown") or ""
        check_markdown_links(md, b.get("id", "?"))
        out = {**ident, "type": t, "markdown": md}
        if b.get("pins") is not None:
            out["pins"] = check_pins(b["pins"], f"{where}.pins")
        return out
    if t == "code":
        _strict(b, where, ("id", "type", "language", "text", "caption"))
        out = {**ident, "type": t, **_check_code({"language": b.get("language"),
                                                   "text": b.get("text")}, where)}
        if b.get("caption") is not None:
            out["caption"] = _opt_str(b["caption"], f"{where}.caption")
        return out
    if t == "divider":
        _strict(b, where, ("id", "type"))
        return {**ident, "type": t}
    if t == "code_peek":
        _strict(b, where, ("id", "type", "source", "caption"))
        out = {**ident, "type": t, "source": check_selection(b.get("source"), f"{where}.source")}
        if b.get("caption") is not None:
            out["caption"] = _opt_str(b["caption"], f"{where}.caption")
        return out
    if t == "sequence":
        _strict(b, where, ("id", "type", "title", "actors", "steps"))
        actors = {k: _label(v, f"{where}.actors.{k}")
                  for k, v in _keyed(b.get("actors"), f"{where}.actors").items()}
        steps = b.get("steps")
        if not isinstance(steps, list):
            raise DocError(f"{where}.steps must be a list.")
        out = {**ident, "type": t, "title": _label(b.get("title"), f"{where}.title"),
               "actors": actors,
               "steps": [check_step(s, f"{where}.steps[{i}]") for i, s in enumerate(steps)]}
        return out
    if t == "flow_diagram":
        _strict(b, where, ("id", "type", "title", "description", "direction", "nodes", "edges"))
        nodes, edges = b.get("nodes"), b.get("edges")
        if not isinstance(nodes, list) or not nodes:
            raise DocError("Use nodes and edges arrays (not children); at least one node is required.")
        if len(nodes) > 100:
            raise DocError("A flow diagram holds at most 100 nodes.")
        if edges is None:
            edges = []
        if not isinstance(edges, list) or len(edges) > 300:
            raise DocError("A flow diagram's edges must be a list (≤300).")
        out = {**ident, "type": t, "title": _label(b.get("title"), f"{where}.title"),
               "nodes": [check_flow_node(n, f"{where}.nodes[{i}]") for i, n in enumerate(nodes)],
               "edges": [check_flow_edge(e, f"{where}.edges[{i}]") for i, e in enumerate(edges)]}
        if b.get("description") is not None:
            out["description"] = _opt_str(b["description"], f"{where}.description")
        if b.get("direction") is not None:
            out["direction"] = _enum(b["direction"], f"{where}.direction", ("right", "down"))
        return out
    if t == "call_stack_diff":
        _strict(b, where, ("id", "type", "title", "base", "head"))
        for side in SIDES:
            if not isinstance(b.get(side), list):
                raise DocError("Both base and head arrays are required; either may be empty.")
        return {**ident, "type": t, "title": _label(b.get("title"), f"{where}.title"),
                "base": [_check_frame(f, f"{where}.base[{i}]") for i, f in enumerate(b["base"])],
                "head": [_check_frame(f, f"{where}.head[{i}]") for i, f in enumerate(b["head"])]}
    if t == "database_lens":
        _strict(b, where, ("id", "type", "title", "actors", "stores", "useCases"))
        actors = {}
        for k, v in _keyed(b.get("actors"), f"{where}.actors").items():
            if isinstance(v, str):
                actors[k] = _label(v, f"{where}.actors.{k}")
            else:
                _strict(v, f"{where}.actors.{k}", ("label", "softwareMapPath"))
                actors[k] = {"label": _label(v.get("label"), f"{where}.actors.{k}.label")}
                if v.get("softwareMapPath") is not None:
                    actors[k]["softwareMapPath"] = _label(v["softwareMapPath"], f"{where}.actors.{k}.softwareMapPath")
        stores_in = b.get("stores")
        if not isinstance(stores_in, dict) or not stores_in:
            raise DocError("A database lens needs at least one store.")
        stores = {}
        for k, s in stores_in.items():
            sw = f"{where}.stores.{k}"
            _strict(s, sw, ("label", "storage", "dataStoreKind", "softwareMapPath", "collections"))
            st = {"label": _label(s.get("label"), f"{sw}.label"),
                  "storage": _enum(s.get("storage"), f"{sw}.storage", ("relational", "document")),
                  "collections": {}}
            if s.get("dataStoreKind") is not None:
                st["dataStoreKind"] = _enum(s["dataStoreKind"], f"{sw}.dataStoreKind",
                                            ("database", "objectStore", "bucket", "artifactStore", "fileStore"))
            if s.get("softwareMapPath") is not None:
                st["softwareMapPath"] = _label(s["softwareMapPath"], f"{sw}.softwareMapPath")
            for ck, c in _keyed(s.get("collections"), f"{sw}.collections").items():
                cw = f"{sw}.collections.{ck}"
                _strict(c, cw, ("label", "key", "fields"))
                col = {"label": _label(c.get("label"), f"{cw}.label"),
                       "fields": {fk: _check_field(fv, f"{cw}.fields.{fk}")
                                  for fk, fv in _keyed(c.get("fields"), f"{cw}.fields").items()}}
                if c.get("key") is not None:
                    col["key"] = _label(c["key"], f"{cw}.key")
                st["collections"][ck] = col
            stores[k] = st
        ucs = b.get("useCases")
        if not isinstance(ucs, list) or not ucs:
            raise DocError("A database lens needs at least one use case.")
        use_cases = []
        for i, u in enumerate(ucs):
            uw = f"{where}.useCases[{i}]"
            _strict(u, uw, ("id", "label", "summary", "operations"))
            ops = u.get("operations")
            if not isinstance(ops, list) or not ops:
                raise DocError("A use case needs at least one operation.")
            uc = {"label": _label(u.get("label"), f"{uw}.label"), "operations": []}
            if u.get("id"):
                uc["id"] = u["id"]
            if u.get("summary") is not None:
                uc["summary"] = _opt_str(u["summary"], f"{uw}.summary")
            for j, o in enumerate(ops):
                ow = f"{uw}.operations[{j}]"
                _strict(o, ow, ("id", "kind", "store", "collection", "field", "actor",
                                "label", "detail", "source"))
                op = {"kind": _enum(o.get("kind"), f"{ow}.kind", ("read", "write")),
                      "store": _label(o.get("store"), f"{ow}.store"),
                      "collection": _label(o.get("collection"), f"{ow}.collection"),
                      "actor": _label(o.get("actor"), f"{ow}.actor"),
                      "label": _label(o.get("label"), f"{ow}.label"),
                      "source": check_selection(o.get("source"), f"{ow}.source")}
                if o.get("id"):
                    op["id"] = o["id"]
                if o.get("field") is not None:
                    op["field"] = _label(o["field"], f"{ow}.field")
                if o.get("detail") is not None:
                    op["detail"] = _opt_str(o["detail"], f"{ow}.detail")
                uc["operations"].append(op)
            use_cases.append(uc)
        return {**ident, "type": t, "title": _label(b.get("title"), f"{where}.title"),
                "actors": actors, "stores": stores, "useCases": use_cases}
    if t == "image":
        _strict(b, where, ("id", "type", "assetId", "alt", "caption"))
        out = {**ident, "type": t, "assetId": _label(b.get("assetId"), f"{where}.assetId"),
               "alt": _label(b.get("alt"), f"{where}.alt")}
        if b.get("caption") is not None:
            out["caption"] = _opt_str(b["caption"], f"{where}.caption")
        return out
    if t == "trace_quote":
        _strict(b, where, ("id", "type", "traceId", "eventId", "text"))
        return {**ident, "type": t, **{k: _label(b.get(k), f"{where}.{k}")
                                       for k in ("traceId", "eventId", "text")}}
    if t == "software_map":
        _strict(b, where, ("id", "type", "mapVersionId", "focusElementId"))
        out = {**ident, "type": t, "mapVersionId": _label(b.get("mapVersionId"), f"{where}.mapVersionId")}
        if b.get("focusElementId") is not None:
            out["focusElementId"] = _label(b["focusElementId"], f"{where}.focusElementId")
        return out
    raise DocError(f"Unknown block type {t}.")


def _children(ch, where):
    if not isinstance(ch, list):
        raise DocError(f"{where}.children must be a list.")
    return [check_block(c, f"{where}.children[{i}]") for i, c in enumerate(ch)]


def check_content(c, where="content"):
    t = (c or {}).get("type") if isinstance(c, dict) else None
    if t == "step":
        return check_step(c, where)
    if t == "flow_node":
        return check_flow_node(c, where, allow_link=True)
    if t == "flow_edge":
        return check_flow_edge(c, where)
    return check_block(c, where)



def check_references(doc):
    for b in walk(doc):
        t = b.get("type")
        if t == "sequence":
            for s in b["steps"]:
                for k in (s["from"], s["to"]):
                    if k not in b["actors"]:
                        raise DocError(f"Unknown component name: {k}")
        elif t == "flow_diagram":
            keys = [n["key"] for n in b["nodes"]]
            if len(keys) != len(set(keys)):
                raise DocError("Flow node keys must be unique within the diagram.")
            for e in b["edges"]:
                if e["from"] not in keys or e["to"] not in keys:
                    raise DocError(f"Unknown flow endpoint: {e['from']} → {e['to']}")
        elif t == "call_stack_diff":
            for side in SIDES:
                seen = []
                for f in b[side]:
                    if f.get("key") is not None:
                        if f["key"] in seen:
                            raise DocError(f"Frame keys must be unique within {side}.")
                    pk = f.get("parentKey")
                    if pk is not None and pk not in seen:
                        raise DocError("A frame parentKey must name an earlier frame on the same side.")
                    if f.get("key") is not None:
                        seen.append(f["key"])
        elif t == "database_lens":
            for uc in b["useCases"]:
                for op in uc["operations"]:
                    if op["actor"] not in b["actors"]:
                        raise DocError(f"Unknown component name: {op['actor']}")
                    st = b["stores"].get(op["store"])
                    if st is None:
                        raise DocError(f"Unknown component name: {op['store']}")
                    col = st["collections"].get(op["collection"])
                    if col is None:
                        raise DocError(f"Unknown component name: {op['collection']}")
                    if op.get("field"):
                        fields = col["fields"]
                        for part in op["field"].split("."):
                            if not fields or part not in fields:
                                raise DocError(f"Unknown component name: {op['field']}")
                            fields = fields[part].get("fields")



def walk(blocks):
    for b in blocks or []:
        yield b
        if b.get("children"):
            yield from walk(b["children"])


def unit_lists(b):
    if b.get("type") == "sequence":
        return [("steps", b["steps"])]
    if b.get("type") == "flow_diagram":
        return [("nodes", b["nodes"]), ("edges", b["edges"])]
    return []


def assign_ids(node, counter):
    def nid(prefix):
        counter[0] += 1
        return f"{prefix}-{counter[0]}"
    t = node.get("type")
    if not node.get("id"):
        node["id"] = nid(PREFIX.get(t, "block"))
    for _name, lst in unit_lists(node):
        for u in lst:
            if not u.get("id"):
                u["id"] = nid(PREFIX[u["type"]])
    if t == "call_stack_diff":
        for side in SIDES:
            for f in node[side]:
                if not f.get("id"):
                    f["id"] = nid("frame")
    if t == "database_lens":
        for uc in node["useCases"]:
            if not uc.get("id"):
                uc["id"] = nid("case")
            for op in uc["operations"]:
                if not op.get("id"):
                    op["id"] = nid("operation")
    for c in node.get("children") or []:
        assign_ids(c, counter)


def strip_ids(node):
    def has(n):
        if not isinstance(n, dict):
            return False
        if n.get("id"):
            return True
        for v in n.values():
            if isinstance(v, list) and any(has(x) for x in v):
                return True
            if isinstance(v, dict) and any(isinstance(x, (dict, list)) and
                                           (has(x) if isinstance(x, dict) else any(has(y) for y in x))
                                           for x in v.values()):
                return True
        return False
    if has(node):
        raise DocError("IDs are assigned by the server.")


def locate(doc, target_id):
    def rec(lst, parent):
        for i, b in enumerate(lst):
            if b.get("id") == target_id:
                return lst, i, b, parent
            for _name, units in unit_lists(b):
                for j, u in enumerate(units):
                    if u.get("id") == target_id:
                        return units, j, u, b
            if b.get("children"):
                r = rec(b["children"], b)
                if r:
                    return r
        return None
    r = rec(doc, None)
    if r is None:
        raise DocError(f"Target {target_id} does not exist.", 404)
    return r



_NO_PATCH = {"nodes", "edges", "id", "type", "children", "steps", "actors",
             "stores", "useCases", "base", "head", "__proto__", "constructor",
             "prototype"}


def check_edit(e):
    if not isinstance(e, dict):
        raise DocError("edit must be an object.")
    t = e.get("type")
    shapes = {"insert": ("type", "content", "parentId", "afterId"),
              "replace": ("type", "targetId", "content"),
              "update": ("type", "targetId", "changes"),
              "move": ("type", "targetId", "parentId", "afterId"),
              "remove": ("type", "targetId")}
    if t not in shapes:
        raise DocError("edit.type must be one of insert|replace|update|move|remove.")
    _strict(e, "edit", shapes[t])
    return e


def apply_edit(doc, edit, counter, placement="append"):
    check_edit(edit)
    t = edit["type"]

    def place(element, parent_id, after_id, moving_id=None):
        parent_block = locate(doc, parent_id)[2] if parent_id else None
        et = element.get("type")
        if et in UNITS:
            if parent_block is None:
                raise DocError(f"A {et} belongs inside a {'sequence' if et == 'step' else 'flow_diagram'}.")
            want = "sequence" if et == "step" else "flow_diagram"
            if parent_block.get("type") != want:
                raise DocError(f"A {et} belongs inside a {want}.")
            target = (parent_block["steps"] if et == "step" else
                      parent_block["edges"] if et == "flow_edge" else parent_block["nodes"])
        else:
            if parent_block is None:
                target = doc
            else:
                if "children" not in parent_block:
                    raise DocError("Invalid parent for this element.")
                if moving_id and any(x.get("id") == moving_id for x in walk([parent_block])):
                    raise DocError("Cannot move a block inside itself.")
                target = parent_block["children"]
        if after_id:
            if after_id == element.get("id"):
                raise DocError("An element cannot follow itself.")
            idx = next((i for i, x in enumerate(target) if x.get("id") == after_id), None)
            if idx is None:
                raise DocError("afterId must identify a sibling.")
            target.insert(idx + 1, element)
        elif placement == "first" and target is doc:
            target.insert(0, element)
        else:
            target.append(element)
        return parent_block

    if t == "insert":
        content = edit.get("content")
        strip_ids(content)
        el = check_content(copy.deepcopy(content))
        link = el.pop("link", None)
        if el.get("type") not in UNITS:
            assign_ids(el, counter)
        else:
            counter[0] += 1
            el["id"] = f"{PREFIX[el['type']]}-{counter[0]}"
        parent_block = place(el, edit.get("parentId"), edit.get("afterId"))
        res = {"targetId": el["id"], "type": el["type"]}
        if link is not None:
            counter[0] += 1
            edge = {"type": "flow_edge", "id": f"edge-{counter[0]}",
                    "from": link.get("from") or el["key"], "to": link.get("to") or el["key"]}
            if link.get("label") is not None:
                edge["label"] = link["label"]
            if link.get("style") is not None:
                edge["style"] = link["style"]
            parent_block["edges"].append(edge)
            res["linkId"] = edge["id"]
        res["children"] = _first_children(el)
        res["summary"] = _summary("insert", el, parent_block, res.get("linkId"))
        return res

    target_id = edit.get("targetId")
    if not target_id:
        raise DocError("targetId is required.")
    lst, i, el, parent = locate(doc, target_id)

    if t == "remove":
        del lst[i]
        if el.get("type") == "flow_node" and parent is not None:
            parent["edges"][:] = [x for x in parent["edges"]
                                  if x["from"] != el["key"] and x["to"] != el["key"]]
        return {"targetId": target_id, "type": el.get("type"),
                "summary": _summary("remove", el, parent)}

    if t == "replace":
        if el.get("type") in UNITS:
            raise DocError(f"Patch the {el['type']} or replace its diagram.")
        content = edit.get("content")
        strip_ids(content)
        new = check_block(copy.deepcopy(content))
        new["id"] = el["id"]
        assign_ids(new, counter)
        lst[i] = new
        return {"targetId": target_id, "type": new["type"], "children": _first_children(new),
                "summary": _summary("replace", new, parent)}

    if t == "update":
        changes = edit.get("changes")
        if not isinstance(changes, dict) or not changes:
            raise DocError("Supply at least one field to update.")
        for k in changes:
            if k in _NO_PATCH:
                raise DocError(f"Cannot patch {k}; use structural edits or replace.")
            if k == "link":
                raise DocError("A link only comes with a new node; insert a flow_edge instead.")
        merged = copy.deepcopy(el)
        for k, v in changes.items():
            if v is None:
                merged.pop(k, None)
            else:
                merged[k] = v
        if merged.get("type") in UNITS:
            fixed = check_content(merged)
        else:
            fixed = check_block(merged)
        fixed["id"] = el["id"]
        lst[i] = fixed
        s = _summary("update", fixed, parent)
        s["fields"] = sorted(changes)
        return {"targetId": target_id, "type": fixed["type"], "summary": s}

    if t == "move":
        if el.get("type") in UNITS:
            if edit.get("parentId") and edit["parentId"] != (parent or {}).get("id"):
                raise DocError(f"Move a {el['type']} within its own diagram.")
        elif edit.get("parentId") and any(x.get("id") == edit["parentId"] for x in walk([el])):
            raise DocError("Cannot move a block inside itself.")
        del lst[i]
        try:
            parent_block = place(el, edit.get("parentId") or
                                 ((parent or {}).get("id") if el.get("type") in UNITS else None),
                                 edit.get("afterId"), moving_id=el.get("id"))
        except DocError:
            lst.insert(i, el)
            raise
        return {"targetId": target_id, "type": el.get("type"),
                "summary": _summary("move", el, parent_block)}
    raise DocError("Unknown edit.")


def _first_children(el):
    if el.get("children") is not None:
        return [{"id": c["id"], "type": c["type"]} for c in el["children"]]
    units = []
    for _n, lst in unit_lists(el):
        units += [{"id": u["id"], "type": u["type"]} for u in lst]
    return units or None


def _summary(kind, el, parent, link_id=None):
    s = {"type": kind, "targetId": el.get("id"), "kind": el.get("type")}
    if el.get("type") in UNITS:
        s["unit"] = el["type"]
        s["blockId"] = (parent or {}).get("id")
    else:
        s["blockId"] = el.get("id")
        units = [u["id"] for _n, lst in unit_lists(el) for u in lst]
        if units:
            s["units"] = units
    if link_id:
        s["linkId"] = link_id
    return s



def references(doc):
    out = []

    def sel(ref_id, s, kind="peek"):
        out.append((ref_id, kind, s, s.get("pins")))

    for b in walk(doc):
        t, bid = b.get("type"), b.get("id")
        if t == "markdown":
            for _text, href in markdown_links(b["markdown"]):
                if href.lower().startswith("review-source:"):
                    r = parse_source_link(href)
                    s = {"file": r["file"], "start": {"side": r["side"], "line": r["fromLine"]},
                         "end": {"side": r["side"], "line": r["toLine"]}}
                    if b.get("pins"):
                        s["pins"] = b["pins"]
                    sel(f"{bid}:{href}", s, "link")
        elif t == "code_peek":
            sel(f"{bid}:source", b["source"])
        elif t == "sequence":
            for st in b["steps"]:
                if st.get("source"):
                    sel(f"{st['id']}:source", st["source"])
        elif t == "flow_diagram":
            for n in b["nodes"]:
                for i, a in enumerate(n["attachments"]):
                    for j, s in enumerate(a["sources"]):
                        sel(f"{n['id']}:{i}:{j}", s)
        elif t == "call_stack_diff":
            for side in SIDES:
                for f in b[side]:
                    sel(f"{f['id']}:source", f["source"])
                    if f.get("callSite"):
                        sel(f"{f['id']}:callSite", f["callSite"])
                    for j, s in enumerate(f.get("contextSources") or []):
                        sel(f"{f['id']}:ctx{j}", s, "link")
        elif t == "database_lens":
            for uc in b["useCases"]:
                for op in uc["operations"]:
                    sel(f"{op['id']}:source", op["source"])
    return out


def resource_refs(doc):
    out = []
    for b in walk(doc):
        t = b.get("type")
        if t == "image":
            out.append(("image", b["assetId"], b))
        elif t == "trace_quote":
            out.append(("trace", b["traceId"], b))
        elif t == "software_map":
            out.append(("map", b["mapVersionId"], b))
        elif t == "markdown":
            for text, href in markdown_links(b["markdown"]):
                m = TRACE_LINK.match(href)
                if m:
                    out.append(("trace", unquote(m.group(1)),
                                {"type": "trace_quote", "traceId": unquote(m.group(1)),
                                 "eventId": unquote(m.group(2)), "text": text, "id": b.get("id")}))
    return out


def outline(snapshot, full=False, target_id=None):
    import json as _json
    doc = snapshot.get("document") or []
    if target_id:
        _l, _i, el, _p = locate(doc, target_id)
        return _json.dumps(el, ensure_ascii=False, indent=1)
    lines = [f"Whiteboard {snapshot['reviewId']} v{snapshot['version']}: {snapshot.get('title', '')}"]
    pins = snapshot.get("pins")
    if pins:
        lines.append(f"Pins: repository {pins['repositoryId']} base {pins['base'][:12]} head {pins['head'][:12]}")

    def rec(blocks, depth):
        for b in blocks:
            t = b["type"]
            title = b.get("title") or ""
            if t == "markdown":
                txt = b["markdown"].strip().replace("\n", " ")
                title = txt if full else (txt[:90] + ("…" if len(txt) > 90 else ""))
            elif t == "code_peek":
                s = b["source"]
                title = f"{s['file']} {s['start']['side']}:{s['start']['line']}-{s['end']['side']}:{s['end']['line']}"
            elif t == "trace_quote":
                title = f"«{b['text'][:80]}» ({b['traceId']}#{b['eventId']})"
            lines.append("  " * depth + f"[{b['id']}] {t}: {title}".rstrip())
            for _n, lst in unit_lists(b):
                for u in lst:
                    desc = (f"{u['from']} → {u['to']}: {u.get('label', '')}" if u["type"] in ("step", "flow_edge")
                            else f"{u['key']}: {u['label']}")
                    lines.append("  " * (depth + 1) + f"[{u['id']}] {u['type']}: {desc}")
            if full and t in ("sequence", "flow_diagram", "database_lens", "call_stack_diff"):
                lines.append("  " * (depth + 1) + _json.dumps(
                    {k: v for k, v in b.items() if k not in ("steps", "nodes", "edges")},
                    ensure_ascii=False)[:2000])
            if b.get("children"):
                rec(b["children"], depth + 1)
    rec(doc, 0)
    lenses = snapshot.get("lenses") or []
    lines.append("")
    lines.append("Lenses (edit with wb_lens_edit):" if lenses else "No lenses yet (add them with wb_lens_edit).")
    for ln in lenses:
        lines.append(f"  [{ln['id']}] {ln['title']}")
    lines.append("")
    lines.append("Use targetId for one component's complete content, or full:true for the whole whiteboard.")
    return "\n".join(lines)
