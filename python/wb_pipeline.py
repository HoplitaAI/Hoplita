#!/usr/bin/env python3
import hashlib
import json
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import wb_document as D
import wb_dossier as DS
import wb_store as S
import wb_vcs as V

EXPLORE_TOOLS = frozenset(("code_outline", "code_read_range", "code_read_smart", "code_search",
                           "code_symbols", "code_definition", "code_references", "list_dir",
                           "git_log", "git_show", "intent", "load_tools",
                           "use_tool"))
MAX_PARTS = 6
MAX_FILES_PER_PART = 6
EXPLORE_ITER = 12


class PipelineError(Exception):
    pass


class Ctx:

    def __init__(self, json_call, agent, cancelled=lambda: False, progress=lambda *a, **k: None,
                 lang="English", parallel=2, focus=lambda stage, **k: ""):
        self.json_call, self.agent, self.cancelled = json_call, agent, cancelled
        self.progress, self.lang, self.parallel, self.focus = progress, lang, max(1, parallel), focus



def json_from(text):
    if isinstance(text, dict):
        return text
    text = text or ""
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _end = dec.raw_decode(text[m.start():])
            if isinstance(obj, dict):
                return obj
        except ValueError:
            continue
    return None


def _norm(s):
    return " ".join(str(s or "").split())


def _text_at(root, commit, rel):
    try:
        return V.read_text(root, commit, rel)
    except Exception:
        return None


def resolve_anchor(root, commit, rel, quote, lines=8, side="head"):
    rel = str(rel or "").strip().lstrip("./")
    q = _norm(quote)
    if not rel or not q:
        return None
    text = _text_at(root, commit, rel)
    if text is None:
        return None
    ls = text.split("\n")
    norm = [_norm(x) for x in ls]
    idx = next((i for i, x in enumerate(norm) if x == q), None)
    if idx is None:
        idx = next((i for i, x in enumerate(norm) if q in x), None)
    if idx is None and len(q) > 24:
        idx = next((i for i, x in enumerate(norm) if x and x in q and len(x) > 12), None)
    if idx is None:
        return None
    n = max(1, min(int(lines or 8), 30))
    end = min(len(ls), idx + n)
    while end > idx + 1 and not ls[end - 1].strip():
        end -= 1
    return {"file": rel, "start": {"side": side, "line": idx + 1}, "end": {"side": side, "line": end}}


def link_of(src):
    return (f"review-source:{src['start']['side']}/{src['file']}"
            f"#L{src['start']['line']}-L{src['end']['line']}")


def fingerprint(root, commit, files):
    h = hashlib.sha1()
    for rel in sorted(files):
        h.update(rel.encode())
        h.update((_text_at(root, commit, rel) or "\0missing").encode("utf-8", "replace"))
    return h.hexdigest()


def _brief_key(root, kind, part):
    raw = json.dumps([root, kind, sorted(part.get("files") or []), part.get("question", ""),
                      bool(part.get("flow"))], ensure_ascii=False)
    return hashlib.sha1(raw.encode()).hexdigest()



class Board:

    def __init__(self, wid, lease=None):
        self.st, self.wid = S.store(), wid
        self.lease = lease or f"draw-{uuid.uuid4()}"

    def begin(self, focus=""):
        self.st.activity(self.wid, "begin", self.lease, "document",
                         {"description": focus} if focus else None)

    def focus(self, text):
        try:
            self.st.activity(self.wid, "renew", self.lease, "document",
                             {"description": text} if text else None)
        except Exception:
            pass

    def end(self):
        import wb_tools
        wb_tools.wb_activity(self.wid, "end", self.lease)

    def cmd(self, op):
        return self.st.command(dict(op, reviewId=self.wid), lease_id=self.lease)

    def insert(self, content, parent=None, after=None):
        e = {"type": "insert", "content": content}
        if parent:
            e["parentId"] = parent
        if after:
            e["afterId"] = after
        return self.cmd({"type": "edit", "edit": e})

    def remove(self, target):
        return self.cmd({"type": "edit", "edit": {"type": "remove", "targetId": target}})

    def snap(self):
        return self.st.snapshot(self.wid)


def create_board(root, target, title, project_of):
    st = S.store()
    rid = st.register_repository(root)["id"]
    target = dict(target, repositoryId=rid)
    op = {"type": "create", "title": title, "target": target}
    proj = project_of(st.repository(rid)["path"]) if project_of else ""
    if proj:
        op["project"] = proj
    return st.command(op)["reviewId"]



_PLAN_OVERVIEW = """You plan a whiteboard that explains how a software project works to someone about
to work on it. Below is a dossier computed from the repository: README, manifests, layout,
entry points, the modules the rest imports most (the core) and what changes.

Decide the story. Answer ONLY with JSON:
{{"title": "the whiteboard's title, in {lang}",
  "story": "2-4 sentences in {lang}: what the project is and does, and how it is put together",
  "parts": [{{"id": "p1", "title": "section title in {lang}",
              "question": "what this part must explain",
              "files": ["repo-relative paths, at most {mf}, taken from the dossier"]}}],
  "flow": {{"part": "the id of the part that traces the main flow",
            "title": "diagram title in {lang}",
            "question": "the main flow to trace end to end: from where to where"}}}}
Rules: 3 to {mp} parts, in reading order; the first says what it is and how it is laid out;
one part traces the main flow from an entry point through the core; files must appear in
the dossier; prefer entry points and core modules over tests.

{dossier}"""

_PLAN_CHANGE = """You plan a whiteboard that lets a reviewer see and weigh a change to a software
project. Below: a dossier of the project, and the change (files with added/removed lines,
and the commit messages).

Answer ONLY with JSON:
{{"title": "the whiteboard's title, in {lang}",
  "story": "2-4 sentences in {lang}: what changes and why",
  "parts": [{{"id": "p1", "title": "section title in {lang}",
              "question": "what this part must explain about the change",
              "files": ["repo-relative paths of changed files (and at most 2 unchanged ones for context)"]}}],
  "flow": {{"part": "the id of the part whose design is worth a diagram",
            "title": "diagram title in {lang}",
            "question": "the path through the changed code to draw"}}}}
Rules: 1 to {mp} parts grouping the changed files by concern (every changed file that
matters in one part, at most {mf} files each); in reading order.

{change}

{dossier}"""


def _plan(ctx, kind, dossier, change_text=""):
    if kind == "overview":
        prompt = _PLAN_OVERVIEW.format(lang=ctx.lang, mf=MAX_FILES_PER_PART, mp=MAX_PARTS,
                                       dossier=dossier["text"])
    else:
        prompt = _PLAN_CHANGE.format(lang=ctx.lang, mf=MAX_FILES_PER_PART, mp=MAX_PARTS,
                                     change=change_text, dossier=dossier["text"][:6000])
    plan = ctx.json_call(prompt, "You plan whiteboards of code. Terse, exact, JSON only.")
    plan = plan if isinstance(plan, dict) else {}
    known = set(dossier["files"])
    parts = []
    for i, p in enumerate(plan.get("parts") or []):
        if not isinstance(p, dict):
            continue
        files = [f.strip().lstrip("./") for f in (p.get("files") or []) if isinstance(f, str)]
        files = [f for f in files if f in known][:MAX_FILES_PER_PART]
        if not files:
            continue
        parts.append({"id": str(p.get("id") or f"p{i + 1}"), "title": str(p.get("title") or f"Part {i + 1}"),
                      "question": str(p.get("question") or ""), "files": files})
        if len(parts) >= MAX_PARTS:
            break
    if not parts:
        seed = (dossier.get("entries") or [])[:2] + (dossier.get("central") or [])[:4]
        seed = [f for f in dict.fromkeys(seed) if f in known][:MAX_FILES_PER_PART]
        if not seed:
            raise PipelineError("The plan named no file of the project.")
        parts = [{"id": "p1", "title": dossier["root"].rsplit("/", 1)[-1],
                  "question": "What this code is and how it works", "files": seed}]
    flow = plan.get("flow") if isinstance(plan.get("flow"), dict) else {}
    fpart = next((p for p in parts if p["id"] == str(flow.get("part") or "")), parts[min(1, len(parts) - 1)])
    fpart["flow"] = {"title": str(flow.get("title") or fpart["title"]),
                     "question": str(flow.get("question") or fpart["question"])}
    return {"title": str(plan.get("title") or dossier["root"].rsplit("/", 1)[-1]),
            "story": str(plan.get("story") or ""), "parts": parts}



_EXPLORE = """You are one of several explorers reading a code project for a whiteboard. Your part:
«{title}» — {question}
Project root: {root} (tools take ABSOLUTE paths: {root}/<file>).
Start from these files: {files}.
{extra}
Read like an engineer, not a scanner: code_outline first; code_search to find where a name is
defined or called; code_read_range for the few spans that answer. Stay inside your part and
stop as soon as you can answer — around eight reads.

Answer ONLY with JSON:
{{"summary": "3-5 sentences in {lang} answering the question",
  "claims": [{{"text": "one sentence in {lang}", "file": "repo-relative path",
               "quote": "ONE line of code copied exactly from that file (the evidence)",
               "lines": "how many lines from that line show it, 1-30"{side}}}]{flow}}}
4 to 8 claims; every quote verbatim from the file{sidenote}."""


def _explore_prompt(ctx, root, part, kind, wid):
    flow = ""
    if part.get("flow"):
        flow = (',\n  "flow": [{"label": "short step in %s", "file": "path", "quote": "one exact line"%s}]'
                % (ctx.lang, ', "side": "head or base"' if kind != "overview" else ""))
    extra = ""
    if part.get("flow"):
        extra = f"Also trace, in order, 4-9 steps of: {part['flow']['question']}.\n"
    side = sidenote = ""
    if kind != "overview":
        extra += (f"This is a CHANGE: wb_diff(whiteboardId=\"{wid}\", paths=[...], format=\"patch\") shows "
                  f"it with base and head line numbers.\n")
        side = ', "side": "head (the new code) or base (the old)"'
        sidenote = ", from the side it names"
    return _EXPLORE.format(title=part["title"], question=part["question"], root=root,
                           files=", ".join(part["files"]), extra=extra, lang=ctx.lang,
                           side=side, flow=flow, sidenote=sidenote)


def _explore(ctx, root, pins, part, kind, wid):
    tools = set(EXPLORE_TOOLS) | ({"wb_diff"} if kind != "overview" else set())
    reply = ctx.agent(_explore_prompt(ctx, root, part, kind, wid), tools, EXPLORE_ITER,
                      f"explore-{part['id']}")
    brief = json_from(reply) or {}
    return _anchor_brief(root, pins, brief)


def _commit_for(pins, side):
    return pins["base"] if side == "base" else pins["head"]


def _anchor_brief(root, pins, brief):
    out = {"summary": str(brief.get("summary") or ""), "claims": [], "flow": []}
    for c in brief.get("claims") or []:
        if not isinstance(c, dict):
            continue
        side = "base" if str(c.get("side") or "").startswith("base") else "head"
        try:
            n = int(re.sub(r"\D", "", str(c.get("lines") or "8")) or 8)
        except ValueError:
            n = 8
        src = resolve_anchor(root, _commit_for(pins, side), c.get("file"), c.get("quote"), n, side)
        out["claims"].append({"text": str(c.get("text") or ""), "source": src})
    for s in brief.get("flow") or []:
        if not isinstance(s, dict) or not str(s.get("label") or "").strip():
            continue
        side = "base" if str(s.get("side") or "").startswith("base") else "head"
        src = resolve_anchor(root, _commit_for(pins, side), s.get("file"), s.get("quote"), 6, side)
        out["flow"].append({"label": str(s["label"])[:60], "source": src})
    return out


def explore_all(ctx, root, pins, parts, kind, wid, board=None, fresh=False):
    st = S.store()
    briefs, todo = {}, []
    for p in parts:
        fp = fingerprint(root, pins["head"], p["files"])
        if kind != "overview":
            fp += fingerprint(root, pins["base"], p["files"])
        key = _brief_key(root, kind, p)
        cached = None if fresh else st.brief_get(key, fp)
        if cached is not None:
            briefs[p["id"]] = cached
        else:
            todo.append((p, key, fp))
    done = [0]
    lock = threading.Lock()

    def run(item):
        p, key, fp = item
        if ctx.cancelled():
            return
        b = _explore(ctx, root, pins, p, kind, wid)
        if _answered(b):
            st.brief_put(key, fp, b)
        with lock:
            briefs[p["id"]] = b
            done[0] += 1
            ctx.progress("exploring", n=done[0], of=len(todo))
            if board:
                board.focus(ctx.focus("exploring", n=done[0], of=len(todo)))

    if todo:
        ctx.progress("exploring", n=0, of=len(todo))
        with ThreadPoolExecutor(max_workers=min(ctx.parallel, len(todo))) as ex:
            list(ex.map(run, todo))
    return briefs, len(todo)



_WRITE = """You write the prose of a whiteboard in {lang}. Below is the material explorers
gathered, part by part, with every claim's evidence already turned into a link.

Answer ONLY with JSON:
{{"intro": "2-4 sentences in {lang} that open the whiteboard",
  "sections": {{"<part id>": "markdown in {lang}: one to three short paragraphs telling that part"}}}}
Rules: weave the claims into the story and link each one by wrapping a few of its words as
[words](link) with its link EXACTLY as given; never write a link that is not given; no
headings (each section already has its title); no code blocks (code peeks are added by
the page).

Story: {story}

{material}"""


def _material(plan, briefs, only=None):
    out = []
    for p in plan["parts"]:
        if only and p["id"] not in only:
            continue
        b = briefs.get(p["id"]) or {}
        lines = [f"## part {p['id']}: {p['title']}", f"summary: {b.get('summary', '')}"]
        for c in b.get("claims") or []:
            lines.append(f"- {c['text']}" + (f"  (link: {link_of(c['source'])})" if c.get("source") else ""))
        out.append("\n".join(lines))
    return "\n\n".join(out)


def _allowed_links(briefs):
    return {link_of(c["source"]) for b in briefs.values() for c in (b.get("claims") or []) if c.get("source")}


def _clean_links(md, allowed):
    def keep(m):
        return m.group(0) if m.group(2) in allowed or not m.group(2).startswith("review-") else m.group(1)
    return re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", keep, md or "")


def _answered(brief):
    return bool((brief or {}).get("summary") or (brief or {}).get("claims"))


def _write_prose(ctx, plan, briefs, only=None):
    res = ctx.json_call(_WRITE.format(lang=ctx.lang, story=plan.get("story", ""),
                                      material=_material(plan, briefs, only)),
                        "You write clear technical prose. JSON only.") or {}
    allowed = _allowed_links(briefs)
    secs = res.get("sections") if isinstance(res.get("sections"), dict) else {}
    out = {pid: _clean_links(str(md), allowed) for pid, md in secs.items()}
    return _clean_links(str(res.get("intro") or ""), allowed), out


def _fallback_prose(brief):
    parts = [brief.get("summary") or ""]
    for c in brief.get("claims") or []:
        if c.get("source"):
            parts.append(f"- [{c['text']}]({link_of(c['source'])})")
        elif c.get("text"):
            parts.append(f"- {c['text']}")
    return "\n".join(p for p in parts if p).strip()


def _flow_block(part, brief):
    steps = [s for s in (brief.get("flow") or []) if s.get("label")]
    if len(steps) < 2:
        steps = [{"label": c["text"][:56], "source": c.get("source")} for c in (brief.get("claims") or [])[:7]
                 if c.get("text")]
    if len(steps) < 2:
        return None
    nodes, edges = [], []
    for i, s in enumerate(steps[:9]):
        n = {"key": f"s{i}", "label": s["label"][:56] or f"{i + 1}",
             "kind": "terminal" if i in (0, len(steps[:9]) - 1) else "process"}
        if s.get("source"):
            n["attachments"] = [{"label": s["label"][:56] or "code", "sources": [s["source"]]}]
        nodes.append(n)
        if i:
            edges.append({"from": f"s{i - 1}", "to": f"s{i}"})
    return {"type": "flow_diagram", "title": part["flow"]["title"][:80] or part["title"],
            "nodes": nodes, "edges": edges}


def _peeks(brief, n=2):
    out, seen = [], set()
    for c in sorted((c for c in brief.get("claims") or [] if c.get("source")),
                    key=lambda c: -(c["source"]["end"]["line"] - c["source"]["start"]["line"])):
        f = c["source"]["file"]
        if f in seen:
            continue
        seen.add(f)
        out.append({"type": "code_peek", "source": c["source"], "caption": c["text"][:120]})
        if len(out) >= n:
            break
    return out


def _safe_insert(board, content, parent=None):
    try:
        return board.insert(content, parent)
    except (S.WbError, D.DocError):
        if content.get("type") == "markdown":
            plain = re.sub(r"\[([^\]]+)\]\(review-[^)]+\)", r"\1", content["markdown"])
            try:
                return board.insert(dict(content, markdown=plain), parent)
            except (S.WbError, D.DocError):
                return None
        if content.get("type") == "flow_diagram":
            bare = dict(content, nodes=[{k: v for k, v in n.items() if k != "attachments"}
                                        for n in content["nodes"]])
            try:
                return board.insert(bare, parent)
            except (S.WbError, D.DocError):
                return None
        return None


def fill_section(board, part, brief, prose):
    sid = part["section_id"]
    _safe_insert(board, {"type": "markdown", "markdown": prose or _fallback_prose(brief) or part["question"]}, sid)
    if part.get("flow"):
        fb = _flow_block(part, brief)
        if fb:
            _safe_insert(board, fb, sid)
    for pk in _peeks(brief):
        _safe_insert(board, pk, sid)



def _change_text(root, pins):
    try:
        files = V.changed_files(root, pins["base"], pins["head"])
    except Exception:
        files = []
    lines = [f"- {f.get('path')}: +{f.get('additions', 0)} -{f.get('deletions', 0)} ({f.get('status', '')})"
             for f in files[:80]]
    try:
        msgs = [c.get("subject", "") for c in V.commits(root, pins["base"], pins["head"])][:12]
    except Exception:
        msgs = []
    return ("Changed files:\n" + "\n".join(lines) + ("\n\nCommits:\n" + "\n".join(msgs) if msgs else "")), \
        [f.get("path") for f in files]


def _drop(board, job):
    try:
        board.end()
    except Exception:
        pass
    try:
        S.store().command({"type": "delete", "reviewId": board.wid})
    except Exception:
        pass
    job["whiteboardId"] = None


def draw(job, ctx, project_of=None):
    root = job["root"]
    kind = job["what"]
    ctx.progress("mapping")
    dossier = DS.build(root)
    target = ({"kind": "worktree"} if kind == "changes" else
              {"kind": "commits", "head": "HEAD", "base": "HEAD~1"} if kind == "commit" else
              {"kind": "commits", "head": "HEAD"})
    wid = create_board(root, target, os.path.basename(root), project_of)
    job["whiteboardId"] = wid
    board = Board(wid)
    board.begin(ctx.focus("planning"))
    try:
        pins = board.snap()["pins"]
        change_text = ""
        if kind != "overview":
            change_text, changed = _change_text(root, pins)
            dossier["files"] = list(dict.fromkeys(dossier["files"] + [c for c in changed if c]))
        ctx.progress("planning")
        plan = _plan(ctx, "overview" if kind == "overview" else "change", dossier, change_text)
        if ctx.cancelled():
            _drop(board, job)
            return
        board.cmd({"type": "rename", "title": plan["title"][:120]})
        if plan.get("story"):
            _safe_insert(board, {"type": "markdown", "markdown": plan["story"]})
        for p in plan["parts"]:
            p["section_id"] = board.insert({"type": "section", "title": p["title"][:120], "children": []})["targetId"]
        S.store().plan_put(wid, dict(plan, kind=kind))
        briefs, explored = explore_all(ctx, root, pins, plan["parts"], kind, wid, board)
        job["explored"] = explored
        if ctx.cancelled():
            _drop(board, job)
            return
        if not any(_answered(b) for b in briefs.values()):
            board.end()
            S.store().command({"type": "delete", "reviewId": wid})
            job["whiteboardId"] = None
            raise PipelineError("no_answers")
        ctx.progress("writing")
        board.focus(ctx.focus("writing"))
        _intro, prose = _write_prose(ctx, plan, briefs)
        for p in plan["parts"]:
            fill_section(board, p, briefs.get(p["id"]) or {}, prose.get(p["id"]))
    finally:
        try:
            board.end()
        except Exception:
            pass
    return wid


def _prepare(job, ctx, pins, kind, fresh=False):
    root = job["root"]
    dossier = DS.build(root)
    change_text = ""
    if kind != "overview":
        change_text, changed = _change_text(root, pins)
        dossier["files"] = list(dict.fromkeys(dossier["files"] + [c for c in changed if c]))
    ctx.progress("planning")
    plan = _plan(ctx, "overview" if kind == "overview" else "change", dossier, change_text)
    briefs, explored = explore_all(ctx, root, pins, plan["parts"],
                                   "overview" if kind == "overview" else "change", job.get("whiteboardId"),
                                   fresh=fresh)
    job["explored"] = explored
    if not any(_answered(b) for b in briefs.values()):
        raise PipelineError("no_answers")
    ctx.progress("writing")
    _intro, prose = _write_prose(ctx, plan, briefs)
    return plan, briefs, prose


def _apply(board, plan, briefs, prose, kind):
    for b in board.snap().get("document") or []:
        board.remove(b["id"])
    if plan.get("story"):
        _safe_insert(board, {"type": "markdown", "markdown": plan["story"]})
    for p in plan["parts"]:
        p["section_id"] = board.insert({"type": "section", "title": p["title"][:120], "children": []})["targetId"]
    S.store().plan_put(board.wid, dict(plan, kind=kind))
    for p in plan["parts"]:
        fill_section(board, p, briefs.get(p["id"]) or {}, prose.get(p["id"]))


def update(job, ctx, info):
    st = S.store()
    wid = job["whiteboardId"]
    snap = st.snapshot(wid)
    pins_old = snap["pins"]
    rid = pins_old["repositoryId"]
    repo = st.repository(rid)["path"]
    job["root"] = job.get("root") or repo
    kind = info.get("kind")
    plan = st.plan_get(wid)
    now = V.head_commit(repo)
    board = Board(wid)
    ctx.progress("checking")
    board.begin(ctx.focus("checking"))
    try:
        if kind == "changes":
            pins = {"repositoryId": rid, "base": now, "head": V.WORKTREE}
            content = _prepare(job, ctx, pins, "changes")
            board.cmd({"type": "set_target", "target": {"kind": "worktree", "repositoryId": rid}})
            _apply(board, *content, "changes")
            return wid
        pins = {"repositoryId": rid, "base": now, "head": now}
        cited = {ref[2].get("file") for ref in D.references(snap.get("document") or [])
                 if isinstance(ref[2], dict) and ref[2].get("file")}
        try:
            changed = {f["path"] for f in V.changed_files(repo, pins_old["head"], now)}
        except Exception:
            changed = None
        plan_files = {f for p in (plan or {}).get("parts", []) for f in p["files"]}
        touched = None if changed is None else (changed & (cited | plan_files))
        trial = dict(snap, pins=pins, target={"kind": "commits", **pins})
        warned = {m.group(1) for w in st._validate_sources(trial, soft=True)
                  for m in [re.match(r"(?:head|base)/([^#]+)#", str(w))] if m}
        target = {"kind": "commits", "head": "HEAD", "repositoryId": rid}
        if touched is not None and not touched and not warned:
            board.cmd({"type": "set_target", "target": target})
            job["modelFree"] = True
            return wid
        ids = {b["id"] for b in snap.get("document") or []}
        if not plan or any(p.get("section_id") not in ids for p in plan["parts"]):
            content = _prepare(job, ctx, pins, "overview")
            board.cmd({"type": "set_target", "target": target})
            _apply(board, *content, "overview")
            return wid
        parts = [p for p in plan["parts"] if touched is None or set(p["files"]) & (touched | warned)] \
            or plan["parts"]
        briefs, explored = explore_all(ctx, repo, pins, parts, "overview", wid, board)
        job["explored"] = explored
        live = [p for p in parts if _answered(briefs.get(p["id"]))]
        if len(live) < len(parts):
            raise PipelineError("no_answers")
        ctx.progress("writing")
        board.focus(ctx.focus("writing"))
        _intro, prose = _write_prose(ctx, plan, briefs, only={p["id"] for p in live})
        board.cmd({"type": "set_target", "target": target})
        for p in live:
            sec = next((b for b in board.snap()["document"] if b["id"] == p["section_id"]), None)
            for child in (sec or {}).get("children") or []:
                board.remove(child["id"])
            fill_section(board, p, briefs.get(p["id"]) or {}, prose.get(p["id"]))
    finally:
        try:
            board.end()
        except Exception:
            pass
    return wid


def kind_of(snap, plan=None):
    if plan and plan.get("kind") in ("overview", "changes", "commit"):
        return plan["kind"]
    pins = snap.get("pins") or {}
    if pins.get("head") == V.WORKTREE:
        return "changes"
    return "overview" if pins.get("base") == pins.get("head") else "commit"


def redraw(job, ctx):
    st = S.store()
    wid = job["whiteboardId"]
    snap = st.snapshot(wid)
    if snap.get("kind") == "scratchpad" or not snap.get("pins"):
        raise PipelineError("not_redrawable")
    pins_old = snap["pins"]
    rid = pins_old["repositoryId"]
    repo = st.repository(rid)["path"]
    job["root"] = repo
    kind = kind_of(snap, st.plan_get(wid))
    now = V.head_commit(repo)
    if kind == "overview":
        pins, target = {"repositoryId": rid, "base": now, "head": now}, {"kind": "commits", "head": "HEAD", "repositoryId": rid}
    elif kind == "changes":
        pins, target = {"repositoryId": rid, "base": now, "head": V.WORKTREE}, {"kind": "worktree", "repositoryId": rid}
    else:
        pins, target = pins_old, None
    board = Board(wid)
    board.begin(ctx.focus("planning"))
    try:
        content = _prepare(job, ctx, pins, kind, fresh=True)
        if target:
            board.cmd({"type": "set_target", "target": target})
        board.cmd({"type": "rename", "title": content[0]["title"][:120]})
        _apply(board, *content, kind)
    finally:
        try:
            board.end()
        except Exception:
            pass
    return wid
