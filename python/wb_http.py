#!/usr/bin/env python3
import json
import mimetypes
import os
import threading
from collections import OrderedDict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import (FileResponse, JSONResponse, PlainTextResponse,
                               Response, StreamingResponse)

import wb_document as D
import wb_store as S
import wb_vcs as V

router = APIRouter()
_CANVAS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wb_canvas")
_ROWS_CACHE = OrderedDict()
_ROWS_LOCK = threading.Lock()


def _st():
    return S.store()


def _err(e):
    return JSONResponse({"error": str(e)}, status_code=getattr(e, "status", 400) or 400)



@router.get("/wb/app")
def wb_app():
    return FileResponse(os.path.join(_CANVAS_DIR, "index.html"),
                        headers={"Cache-Control": "no-store"})


@router.get("/wb/host/{action}")
def wb_host(action: str):
    return Response(status_code=204)


@router.get("/wb/static/{name:path}")
def wb_static(name: str):
    full = os.path.realpath(os.path.join(_CANVAS_DIR, name))
    if not full.startswith(os.path.realpath(_CANVAS_DIR) + os.sep) or not os.path.isfile(full):
        raise HTTPException(404)
    mime = mimetypes.guess_type(full)[0] or "application/octet-stream"
    return FileResponse(full, media_type=mime, headers={"Cache-Control": "no-store"})


@router.get("/wb/api/strings")
def wb_strings(lang: str = None):
    import hoplita_i18n as I
    code = lang if lang and lang.replace("_", "").isalpha() and len(lang) <= 8 else None
    table = {}
    for c in ([code] if code else []) + [getattr(I, "_lang", "en") or "en", "en"]:
        path = os.path.join(I._find_lang_dir(), f"{c}.json")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                table = {k: v for k, v in json.load(f).items() if k.startswith("wbc.")}
            break
    return JSONResponse(table, headers={"Cache-Control": "no-store"})



def _decorate(snap):
    out = dict(snap)
    rid = snap["reviewId"]
    out["activity"] = _st().activity_snapshot(rid)
    pins = snap.get("pins")
    if pins:
        try:
            repo = _st().repository(pins["repositoryId"])
            out["repositoryName"], out["repositoryPath"] = repo["name"], repo["path"]
        except S.WbError:
            out["repositoryName"] = "?"
    out["structural"] = bool(V.diffr_binary())
    if pins:
        try:
            import wb_pipeline as P
            out["drawnAs"] = P.kind_of(snap, _st().plan_get(rid))
        except Exception:
            pass
    return out


@router.get("/wb/api/list")
def wb_list(project: str = None, root: str = None):
    try:
        items = None
        if root and not project:
            import wb_tools
            project = wb_tools._project_of(root.rstrip("/\\"))
            items = [dict(w, projectOfRoot=project) for w in _st().catalog(project)]
        items = items if items is not None else _st().catalog(project)
        return [_listed(w) for w in items]
    except Exception as e:
        return _err(e)


def _listed(w):
    import wb_pipeline as P
    st = _st()
    plan = {}
    try:
        plan = st.plan_get(w["id"]) or {}
        if w.get("pins"):
            w["drawnAs"] = P.kind_of(st.snapshot(w["id"]), plan)
            w["repositoryPath"] = st.repository(w["pins"]["repositoryId"])["path"]
    except Exception:
        pass
    if plan.get("kind") == "history":
        w["since"], w["until"] = plan.get("since") or "", plan.get("until") or ""
    if plan.get("ref"):
        w["ref"] = plan["ref"]
    return w


@router.get("/wb/api/months")
def wb_months(root: str):
    import wb_history
    try:
        return {"months": wb_history.months(os.path.realpath(root.rstrip("/\\")))}
    except Exception as e:
        return _err(e)



_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _repo_pair(root, commit):
    root = os.path.realpath(root.rstrip("/\\"))
    if commit == "worktree":
        return root, V.head_commit(root), V.WORKTREE
    head = V.resolve(root, commit)
    try:
        base = V.resolve(root, head + "~1")
    except Exception:
        base = _EMPTY_TREE
    return root, base, head


@router.get("/wb/api/repo/log")
def wb_repo_log(root: str, skip: int = 0, n: int = 60):
    try:
        root = os.path.realpath(root.rstrip("/\\"))
        raw = V._git(root, ["log", f"--skip={max(0, skip)}", f"-n{max(1, min(n, 200))}",
                            "--format=%H%x00%P%x00%an%x00%aI%x00%s%x1e", "HEAD"])
        explained = {}
        st = _st()
        import wb_pipeline as P
        for w in st.catalog():
            pins = w.get("pins") or {}
            if pins.get("head") and pins.get("head") != V.WORKTREE and w.get("blocks"):
                try:
                    if P.kind_of(st.snapshot(w["id"]), st.plan_get(w["id"])) == "commit":
                        explained.setdefault(pins["head"], w["id"])
                except Exception:
                    pass
        out = []
        for rec in raw.split("\x1e"):
            rec = rec.strip("\n")
            if not rec:
                continue
            c, parents, an, at, subj = (rec.split("\0") + [""] * 5)[:5]
            out.append({"sha": c, "parents": len(parents.split()), "author": an, "date": at,
                        "subject": subj, "whiteboardId": explained.get(c)})
        return {"commits": out, "more": len(out) == max(1, min(n, 200))}
    except Exception as e:
        return _err(e)


@router.get("/wb/api/repo/files")
def wb_repo_files(root: str, commit: str):
    try:
        r, b, h = _repo_pair(root, commit)
        return V.changed_files(r, b, h)
    except Exception as e:
        return _err(e)


@router.get("/wb/api/repo/filediff")
def wb_repo_filediff(root: str, commit: str, path: str):
    try:
        r, b, h = _repo_pair(root, commit)
        fr = _file_rows(r, b, h, D.check_path(path))
        rows = [_row_view(fr, i) for i in range(len(fr["rows"]))]
        return {"path": path, "mode": fr["mode"], "status": fr["status"],
                "previousPath": fr["previousPath"], "rows": rows, "bands": fr["bands"]}
    except Exception as e:
        return _err(e)


@router.get("/wb/api/{wid}/periods")
def wb_periods(wid: str):
    import wb_history
    try:
        st = _st()
        plan = st.plan_get(wid) or {}
        if plan.get("kind") != "history":
            return {"periods": []}
        root = st.repository(st.snapshot(wid)["pins"]["repositoryId"])["path"]
        return {"periods": wb_history.periods(root, plan), "grain": plan.get("grain") or ""}
    except Exception as e:
        return _err(e)



@router.get("/wb/api/find")
def wb_find(root: str, what: str, ref: str = None, since: str = "", until: str = ""):
    import wb_jobs
    import wb_pipeline as P
    root = os.path.realpath(root.rstrip("/\\"))
    running = next((j for j in wb_jobs.jobs(root) if j["what"] == what and j["state"] == "running"
                     and (what not in ("incoming", "commit") or (j.get("ref") or None) == (ref or None))
                     and (what != "history" or (j.get("since"), j.get("until")) == (since, until))), None)
    try:
        if what == "incoming" or (what == "commit" and ref):
            head = V.resolve(root, ref)
        elif what == "history":
            import wb_history
            head = wb_history.range_head(root, since, until)
        else:
            head = V.head_commit(root)
    except Exception:
        head = ""
    found = None
    st = _st()
    for item in st.catalog():
        pins = item.get("pins") or {}
        if not pins or item.get("kind") == "scratchpad" or not item.get("blocks"):
            continue
        try:
            if os.path.realpath(st.repository(pins["repositoryId"])["path"]) != root:
                continue
        except S.WbError:
            continue
        k = P.kind_of(st.snapshot(item["id"]), st.plan_get(item["id"]))
        if k != what or (k in ("commit", "incoming", "history") and pins.get("head") != head):
            continue
        if k == "history":
            pl = st.plan_get(item["id"]) or {}
            if (pl.get("since") or "", pl.get("until") or "") != (since, until):
                continue
        found = item["id"]
        break
    return {"whiteboardId": found, "running": running, "head": head}

@router.post("/wb/api/draw")
async def wb_draw(request: Request):
    import wb_jobs
    try:
        body = await request.json()
        return wb_jobs.start(body.get("root"), body.get("what"), body.get("model") or "",
                             body.get("whiteboardId") or None,
                             blocked=body.get("blocked") or (),
                             remote_ok=bool(body.get("remoteOk")),
                             ref=body.get("ref") or None,
                             since=body.get("since") or "", until=body.get("until") or "")
    except wb_jobs.JobError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return _err(e)


@router.get("/wb/api/draw")
def wb_draw_jobs(root: str = None):
    import wb_jobs
    return {"jobs": wb_jobs.jobs(root)}


@router.post("/wb/api/draw/{jid}/cancel")
def wb_draw_cancel(jid: str):
    import wb_jobs
    try:
        return wb_jobs.cancel(jid)
    except wb_jobs.JobError as e:
        return JSONResponse({"error": str(e)}, status_code=404)


@router.get("/wb/api/{wid}")
def wb_get(wid: str, version: int = None):
    try:
        snap = _decorate(_st().snapshot(wid, version))
        if snap.get("pins") and snap.get("kind") != "scratchpad":
            import wb_pipeline as P
            snap["drawnAs"] = P.kind_of(snap, _st().plan_get(wid))
        return snap
    except S.WbError as e:
        return _err(e)


@router.get("/wb/api/{wid}/staleness")
def wb_staleness(wid: str):
    try:
        return _st().staleness(wid)
    except S.WbError as e:
        return _err(e)


@router.get("/wb/api/{wid}/history")
def wb_history(wid: str):
    try:
        return _st().history(wid)
    except S.WbError as e:
        return _err(e)


@router.get("/wb/api/{wid}/watch")
def wb_watch(wid: str, request: Request):
    st = _st()
    try:
        st.snapshot(wid)
    except S.WbError as e:
        return _err(e)

    def gen():
        mark = -1
        while True:
            try:
                cur = st.change_mark(wid)
                if cur != mark:
                    mark = cur
                    snap = st.snapshot(wid)
                    yield (json.dumps(_decorate(snap), ensure_ascii=False) + "\n").encode()
                else:
                    yield b"\n"
            except S.WbError as e:
                yield (json.dumps({"error": str(e)}) + "\n").encode()
                return
            st.wait_change(wid, mark, timeout=20.0)
    return StreamingResponse(gen(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store"})



def _anchor(snap, repositoryId=None, head=None, base=None):
    if repositoryId and head:
        root = _st().repository(repositoryId)["path"]
        return root, (base or head), head
    pins = snap.get("pins")
    if not pins:
        raise S.WbError("No pins to read at.")
    root = _st().repository(pins["repositoryId"])["path"]
    return root, pins["base"], pins["head"]


def _file_rows(root, base, head, path):
    key = (root, base, head, path)
    with _ROWS_LOCK:
        if key in _ROWS_CACHE:
            _ROWS_CACHE.move_to_end(key)
            return _ROWS_CACHE[key]
    files = V.changed_files(root, base, head, [path]) if base != head else []
    f = next((x for x in files if x["path"] == path), None)
    prev = (f or {}).get("previousPath") or path
    btext = (V.read_file(root, base, prev) or b"").decode("utf-8", "replace") if f else None
    htext = (V.read_file(root, head, path) or b"").decode("utf-8", "replace")
    if f is None:
        btext = htext
    out = {"path": path, "previousPath": prev if prev != path else None,
           "status": (f or {}).get("status", "unchanged"),
           "base": btext.split("\n") if btext is not None else [],
           "head": htext.split("\n"), "bands": [], "spans": {}, "mode": "textual"}
    if f is None:
        n = len(out["head"])
        out["rows"] = [(i, i) for i in range(n)]
        out["changed"] = {"base": set(), "head": set()}
    else:
        ev = V.structural_diff(root, base, head, [path, prev] if prev != path else [path])
        fe = next((e for e in ev or [] if e.get("type") == "file" and "diff" in e
                   and e["diff"].get("type") == "text"), None)
        if fe is not None:
            d = fe["diff"]
            out["mode"] = "structural"
            out["rows"] = V.structural_rows(d)
            sc = d.get("structural_changes") or {}
            out["changed"] = {s: {ln for a, b in sc.get(s, []) for ln in range(a, b)}
                              for s in ("base", "head")}
            out["bands"] = _bands(d, out["rows"], fe.get("visibility"))
            for side, key2 in (("base", "lhs"), ("head", "rhs")):
                for leaf in V._leaves((d.get(key2) or {}).get("regions")):
                    for sp in leaf.get("changed") or []:
                        out["spans"].setdefault(side, {}).setdefault(sp["line"], []).append(
                            [sp["start_column"], sp["end_column"]])
        else:
            out["rows"] = V.textual_rows(root, base, head, path, prev)
            hr = V.hunk_ranges(root, base, head, path, prev)
            out["changed"] = {s: {ln - 1 for a, b in hr[s] for ln in range(a, b + 1)}
                              for s in ("base", "head")}
    with _ROWS_LOCK:
        _ROWS_CACHE[key] = out
        while len(_ROWS_CACHE) > 64:
            _ROWS_CACHE.popitem(last=False)
    return out


def _bands(diff, rows, file_vis):
    head_row = {}
    base_row = {}
    for i, (b, h) in enumerate(rows):
        if h is not None:
            head_row.setdefault(h, i)
        if b is not None:
            base_row.setdefault(b, i)
    bands = []

    def walk(regions, side_rows, side):
        for r in regions or []:
            vis = r.get("visibility") or {}
            if vis.get("collapsed"):
                a, e = V._leaf_span(r)
                rs = [side_rows[x] for x in range(a, e) if x in side_rows]
                if rs:
                    bands.append({"fromRow": min(rs), "toRow": max(rs), "side": side,
                                  "label": vis.get("label") or f"{e - a} hidden lines",
                                  "tags": r.get("tags") or []})
                continue
            walk(r.get("children"), side_rows, side)
    walk((diff.get("rhs") or {}).get("regions"), head_row, "head")
    walk((diff.get("lhs") or {}).get("regions"), base_row, "base")
    bands.sort(key=lambda b: (b["fromRow"], -b["toRow"]))
    merged = []
    for b in bands:
        if merged and b["fromRow"] <= merged[-1]["toRow"]:
            continue
        merged.append(b)
    return merged


def _row_view(fr, i):
    b, h = fr["rows"][i]
    if b is not None and h is not None:
        changed = b in fr["changed"]["base"] or h in fr["changed"]["head"]
        return {"b": b + 1, "h": h + 1, "kind": "mod" if changed else "same",
                "text": fr["head"][h] if h < len(fr["head"]) else "",
                "old": (fr["base"][b] if changed and b < len(fr["base"]) else None),
                "spans": fr["spans"].get("head", {}).get(h),
                "oldSpans": fr["spans"].get("base", {}).get(b) if changed else None}
    if h is not None:
        return {"b": None, "h": h + 1, "kind": "add",
                "text": fr["head"][h] if h < len(fr["head"]) else "",
                "spans": fr["spans"].get("head", {}).get(h)}
    return {"b": b + 1, "h": None, "kind": "del",
            "text": fr["base"][b] if b < len(fr["base"]) else "",
            "spans": fr["spans"].get("base", {}).get(b)}


@router.get("/wb/api/{wid}/peek")
def wb_peek(wid: str, file: str, startSide: str = "head", startLine: int = 1,
            endSide: str = "head", endLine: int = 1, context: int = 3,
            repositoryId: str = None, head: str = None, base: str = None):
    try:
        snap = _st().snapshot(wid)
        root, b, h = _anchor(snap, repositoryId, head, base)
        path = D.check_path(file)
        fr = _file_rows(root, b, h, path)
        idx = {"base": 0, "head": 1}

        def find(side, line):
            k = idx[side]
            for i, row in enumerate(fr["rows"]):
                if row[k] == line - 1:
                    return i
            return None
        a = find(startSide, startLine)
        e = find(endSide, endLine)
        if a is None or e is None or e < a:
            if b == h or startSide == endSide == "head":
                n = len(fr["head"])
                a0, e0 = max(0, startLine - 1), min(n - 1, endLine - 1)
                lines = [{"b": None, "h": i + 1, "kind": "same", "text": fr["head"][i]}
                         for i in range(max(0, a0 - context), min(n, e0 + context + 1))]
                for x in lines:
                    x["selected"] = a0 + 1 <= x["h"] <= e0 + 1
                return {"file": path, "mode": fr["mode"], "rows": lines}
            raise S.WbError("Diff selection unavailable.")
        lo, hi = max(0, a - context), min(len(fr["rows"]) - 1, e + context)
        rows = []
        for i in range(lo, hi + 1):
            rv = _row_view(fr, i)
            rv["selected"] = a <= i <= e
            rows.append(rv)
        return {"file": path, "mode": fr["mode"], "rows": rows,
                "status": fr["status"], "previousPath": fr["previousPath"]}
    except (S.WbError, D.DocError, V.VcsError) as e:
        return _err(e)


def _scope(root, b, h, commit):
    if not commit:
        return b, h
    c = next((x for x in V.commits(root, b, h) if x["commit"].startswith(commit)), None)
    if not c:
        raise S.WbError("commit must be one of this whiteboard's commits.")
    return c["parent"], c["commit"]


@router.get("/wb/api/{wid}/files")
def wb_files(wid: str, commit: str = None):
    try:
        snap = _st().snapshot(wid)
        root, b, h = _anchor(snap)
        b, h = _scope(root, b, h, commit)
        return V.changed_files(root, b, h)
    except (S.WbError, V.VcsError) as e:
        return _err(e)


@router.get("/wb/api/{wid}/filediff")
def wb_filediff(wid: str, path: str, commit: str = None):
    try:
        snap = _st().snapshot(wid)
        root, b, h = _anchor(snap)
        b, h = _scope(root, b, h, commit)
        fr = _file_rows(root, b, h, D.check_path(path))
        rows = [_row_view(fr, i) for i in range(len(fr["rows"]))]
        return {"path": path, "mode": fr["mode"], "status": fr["status"],
                "previousPath": fr["previousPath"], "rows": rows, "bands": fr["bands"]}
    except (S.WbError, D.DocError, V.VcsError) as e:
        return _err(e)


@router.get("/wb/api/{wid}/file")
def wb_file(wid: str, file: str, side: str = "head", repositoryId: str = None,
            head: str = None, base: str = None):
    try:
        snap = _st().snapshot(wid)
        root, b, h = _anchor(snap, repositoryId, head, base)
        commit = h if side == "head" else b
        return {"file": file, "side": side, "commit": commit, "root": root,
                "text": V.read_text(root, commit, D.check_path(file))}
    except (S.WbError, D.DocError, V.VcsError) as e:
        return _err(e)


@router.get("/wb/api/{wid}/commits")
def wb_commits(wid: str):
    try:
        snap = _st().snapshot(wid)
        root, b, h = _anchor(snap)
        out = V.commits(root, b, h)
        for c in out:
            c["files"] = V.changed_files(root, c["parent"], c["commit"]) if c["parent"] else []
        return out
    except (S.WbError, V.VcsError) as e:
        return _err(e)


@router.get("/wb/api/{wid}/progress")
def wb_progress(wid: str):
    try:
        return _st().progress(wid)
    except (S.WbError, V.VcsError) as e:
        return _err(e)


@router.post("/wb/api/{wid}/progress")
async def wb_mark(wid: str, request: Request):
    try:
        body = await request.json()
        return _st().mark_viewed(wid, body.get("path"), bool(body.get("viewed", True)),
                                 body.get("ranges"), body.get("lensId"))
    except (S.WbError, V.VcsError) as e:
        return _err(e)


@router.get("/wb/api/{wid}/resource/{rid}")
def wb_resource(wid: str, rid: str):
    try:
        r = _st().resource(rid)
        return Response(r["data"], media_type=r["mimeType"],
                        headers={"x-content-type-options": "nosniff"})
    except S.WbError as e:
        return _err(e)


@router.post("/wb/api/{wid}/copy-context")
async def wb_copy_context(wid: str, request: Request):
    try:
        body = await request.json()
        snap = _st().snapshot(wid)
        pins = snap.get("pins") or {}
        kind = body.get("kind") or "text"
        title = str(body.get("title") or body.get("quote") or "")[:100].replace("\n", " ")
        head = [f"Selected {'code' if kind == 'code' else 'text'} from Whiteboard: {title}",
                f"Whiteboard ID: {wid}", f"Version: {snap['version']}"]
        if pins:
            head += [f"Repository ID: {pins['repositoryId']}",
                     f"Whiteboard base: {pins['base']}", f"Whiteboard head: {pins['head']}"]
        head.append(f'Read this version with wb_get({{"whiteboardId":"{wid}","version":{snap["version"]},"full":true}}).')
        if kind == "code":
            rows = body.get("rows") or []
            fence = "```"
            while any(fence in (r.get("text") or "") for r in rows):
                fence += "`"
            diff = "\n".join(("+" if r.get("kind") == "add" else "-" if r.get("kind") == "del" else " ")
                             + (r.get("text") or "") for r in rows)
            bodytxt = (f"File: {body.get('file')} ({body.get('side', 'head')} "
                       f"{body.get('fromLine')}-{body.get('toLine')})\n{fence}diff\n{diff}\n{fence}")
        else:
            bodytxt = "\n".join("> " + ln for ln in str(body.get("quote") or "").split("\n"))
        return {"text": "\n".join(head) + "\n\n" + bodytxt + "\n"}
    except S.WbError as e:
        return _err(e)
