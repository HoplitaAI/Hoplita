#!/usr/bin/env python3
import re
import threading
import time
import uuid

WHATS = ("changes", "commit", "overview", "update", "redraw", "incoming", "history")

_JOBS = {}
_LOCK = threading.Lock()
_RUNNER = {"fn": None, "project_of": None}
_KEEP_S = 6 * 3600


class JobError(Exception):
    pass


def set_runner(fn, project_of=None):
    _RUNNER["fn"], _RUNNER["project_of"] = fn, project_of


def _project(root):
    fn = _RUNNER["project_of"]
    try:
        return (fn(root) if fn else "") or ""
    except Exception:
        return ""


def _public(job):
    return {k: job.get(k) for k in ("id", "root", "project", "what", "ref", "since", "until",
                                    "alias", "model", "state",
                                    "whiteboardId", "started", "ended", "error", "reply", "usage",
                                    "stage", "explored", "modelFree", "trace", "gate")}


def _prune():
    now = time.time()
    for jid in [j for j, job in _JOBS.items()
                if job["state"] != "running" and now - (job.get("ended") or now) > _KEEP_S]:
        _JOBS.pop(jid, None)


def start(root, what, alias="", whiteboard=None, blocked=(), remote_ok=False, ref=None,
          since="", until=""):
    root = (root or "").rstrip("/\\")
    if not root:
        raise JobError("root is required: the project's checkout.")
    if what not in WHATS:
        raise JobError(f"what must be one of {', '.join(WHATS)}.")
    if _RUNNER["fn"] is None:
        raise JobError("No agent can draw here: the MCP has no runner.")
    if what in ("update", "redraw") and not whiteboard:
        raise JobError("whiteboardId is required to bring a whiteboard up to date or redraw it.")
    ref = (ref or "").strip()
    if what == "incoming" and not ref.startswith("refs/hoplita/incoming/"):
        raise JobError("ref is required: a branch under refs/hoplita/incoming/.")
    if what == "commit" and ref and not re.fullmatch(r"[0-9a-f]{7,40}", ref):
        raise JobError("ref of a commit is its sha (7 to 40 hex digits).")
    since, until = (since or "").strip(), (until or "").strip()
    if since and until and since > until:
        since, until = until, since
    with _LOCK:
        _prune()
        for job in _JOBS.values():
            if job["root"] == root and job["what"] == what and job["state"] == "running" \
                    and (what not in ("update", "redraw") or job["whiteboardId"] == whiteboard) \
                    and job.get("ref") == (ref or None) \
                    and (job.get("since") or "", job.get("until") or "") == (since, until):
                return _public(job)
        job = {"id": uuid.uuid4().hex[:12], "root": root, "project": _project(root),
               "what": what, "alias": (alias or "base").strip() or "base", "model": "",
               "state": "running", "whiteboardId": whiteboard if what in ("update", "redraw") else None,
               "started": time.time(), "ended": None, "error": None, "reply": "",
               "cancel": threading.Event(),
               "ref": ref or None, "since": since, "until": until,
               "blocked": sorted(str(b) for b in (blocked or ()) if b),
               "remote_ok": bool(remote_ok)}
        _JOBS[job["id"]] = job
    threading.Thread(target=_run, args=(job,), daemon=True, name=f"wb-draw-{job['id']}").start()
    return _public(job)


def _run(job):
    try:
        job["reply"] = _RUNNER["fn"](job) or ""
        job["state"] = "cancelled" if job["cancel"].is_set() else (
            "done" if job.get("whiteboardId") and not job.get("failed") else "failed")
        if job["state"] == "failed" and not job.get("error"):
            job["error"] = job["reply"][:300] or "The agent ended without a whiteboard."
    except JobError as e:
        job["state"], job["error"] = "failed", str(e)
    except Exception as e:
        job["state"], job["error"] = "failed", f"{type(e).__name__}: {e}"
    finally:
        job["ended"] = time.time()


def jobs(root=None):
    root = (root or "").rstrip("/\\")
    with _LOCK:
        out = [_public(j) for j in _JOBS.values() if not root or j["root"] == root]
    return sorted(out, key=lambda j: j["started"], reverse=True)


def cancel(jid):
    with _LOCK:
        job = _JOBS.get(jid)
    if not job:
        raise JobError(f"No drawing job {jid}.")
    job["cancel"].set()
    return _public(job)
