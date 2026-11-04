import hashlib
import json
import os
import signal
import subprocess
import threading
import time
import uuid as _uuid

MIN_EVERY_S = 5
DEFAULT_EVERY_S = 30
PROBE_TIMEOUT_S = 120
RESULT_CHARS = 4000
PREVIEW_CHARS = 400
OUTPUT_TAIL = 5000
KEEP_DONE_S = 24 * 3600
KEEP_OUTPUT_S = 7 * 24 * 3600
EVENTS_MAX = 200
WAKES = ("change", "always")
PROBES = ("file", "command", "tool")
NOT_IN_BACKGROUND = frozenset({
    "background_run", "background_watch", "background_stop",
    "background_list", "use_tool", "intent", "load_tools",
    "plan_and_orchestrate", "call_council", "sticky_note"})
_VOLATILE = ("duration_s", "elapsed_s", "timestamp", "at", "now")
DANGEROUS = ("rm -rf /", "format c:", "mkfs", "dd if=/dev/zero")

_LOCK = threading.RLock()
_JOBS = {}
_EVENTS = []
_PROCS = {}
_HOME = ""
_RUN = None
_DELIVER = None
_MAX = None
_STARTED = False
_WAKE = threading.Event()
CURRENT = threading.local()


def configure(home, run_fn=None, deliver_fn=None, max_fn=None):
    global _HOME, _RUN, _DELIVER, _MAX
    _HOME = home or ""
    _RUN = run_fn
    _DELIVER = deliver_fn
    _MAX = max_fn


def _path():
    return os.path.join(_HOME, "background.json") if _HOME else ""


def _out_dir():
    import tempfile
    d = os.path.join(_HOME or tempfile.gettempdir(), "background")
    os.makedirs(d, exist_ok=True)
    return d


def output_path(stem):
    return os.path.join(_out_dir(), f"{stem}.out")


def _new_id():
    return _uuid.uuid4().hex[:8]


def _now():
    return time.time()


def current_job():
    return getattr(CURRENT, "job", "") or ""


def register_process(job_id, proc):
    if job_id:
        with _LOCK:
            _PROCS[job_id] = proc


def _unregister_process(job_id):
    with _LOCK:
        _PROCS.pop(job_id, None)


def ceiling():
    try:
        n = int(_MAX()) if _MAX else 8
    except Exception:
        n = 8
    return max(1, n)


def _live():
    return [j for j in _JOBS.values() if j["state"] == "live"]


def _public(j):
    keys = ("id", "kind", "label", "conv", "tab", "turn", "tool", "state",
            "created", "ended", "every_s", "wake", "until_s", "fires",
            "rounds", "last_preview", "result", "delegated", "output_path",
            "probe")
    out = {k: j.get(k) for k in keys if k in j}
    if j.get("state") == "live":
        out["age_s"] = int(_now() - j.get("created", _now()))
    return out


def _save():
    p = _path()
    if not p:
        return
    with _LOCK:
        jobs = [{k: v for k, v in j.items() if k not in ("busy",)}
                for j in _JOBS.values()]
    try:
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"jobs": jobs}, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
    except Exception:
        pass


def _load():
    p = _path()
    if not p or not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8") as fh:
            return (json.load(fh) or {}).get("jobs") or []
    except Exception:
        return []


def start():
    global _STARTED
    if _STARTED:
        return
    _STARTED = True
    now = _now()
    for j in _load():
        if not isinstance(j, dict) or not j.get("id"):
            continue
        j.setdefault("fires", 0)
        j.setdefault("rounds", 0)
        j["busy"] = False
        if j.get("state") == "live" and j.get("kind") == "run":
            j["state"] = "lost"
            j["ended"] = now
            with _LOCK:
                _JOBS[j["id"]] = j
            _emit(j, "lost", _text_lost(j))
            continue
        if j.get("state") == "live" and j.get("kind") == "watch":
            h, pv, _full = _probe(j.get("probe") or {})
            j["last_hash"], j["last_preview"] = h, pv
            j["next_at"] = now + float(j.get("every_s") or DEFAULT_EVERY_S)
        if j.get("state") != "live" and now - float(j.get("ended") or 0) > KEEP_DONE_S:
            continue
        with _LOCK:
            _JOBS[j["id"]] = j
    _save()
    try:
        for name in os.listdir(_out_dir()):
            fp = os.path.join(_out_dir(), name)
            if now - os.path.getmtime(fp) > KEEP_OUTPUT_S:
                os.remove(fp)
    except Exception:
        pass
    threading.Thread(target=_scheduler, daemon=True,
                     name="background-watch").start()


def new_run(conv, tab, turn, tool, arguments, label="", delegated=False):
    tool = (tool or "").strip()
    if not tool:
        return {"error": "background_run needs `tool` (the tool's exact name) "
                         "and `arguments` (an object)."}
    if tool in NOT_IN_BACKGROUND:
        return {"error": f"'{tool}' cannot run in the background: call it "
                         "directly."}
    with _LOCK:
        if len(_live()) >= ceiling():
            return {"error": f"the ceiling of live background jobs "
                             f"({ceiling()}, [CHAT] max_threads) is reached. "
                             "Stop one you no longer need with "
                             "background_stop, or wait for one to end."}
        j = {"id": _new_id(), "kind": "run", "label": (label or tool)[:80],
             "conv": conv or "", "tab": tab or "", "turn": turn or "",
             "tool": tool, "arguments": arguments if isinstance(arguments, dict) else {},
             "state": "live", "created": _now(), "delegated": bool(delegated),
             "fires": 0, "rounds": 0, "busy": False}
        _JOBS[j["id"]] = j
    _save()
    return j


def start_run(job, work):
    def _go():
        CURRENT.job = job["id"]
        try:
            res = work()
        except Exception as e:
            finish(job["id"], None, error=str(e))
        else:
            finish(job["id"], res)
        finally:
            CURRENT.job = ""
    threading.Thread(target=_go, daemon=True,
                     name=f"background-{job['id']}").start()


def finish(job_id, result, error=None):
    with _LOCK:
        j = _JOBS.get(job_id)
        if j is None or j["state"] != "live":
            return None
        j["state"] = "failed" if error else "done"
        j["ended"] = _now()
        text = _result_text(result, error)
        j["result"] = text[:PREVIEW_CHARS]
        if j.get("output_path") is None:
            j["output_path"] = _output_of(result)
    _unregister_process(job_id)
    _save()
    _emit(j, "failed" if error else "finished", _text_finished(j, text))
    return j


def _output_of(result):
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except Exception:
            return ""
    return str((result or {}).get("output_path") or "") if isinstance(result, dict) else ""


def _result_text(result, error=None):
    if error:
        return f"error: {error}"
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False)
    except Exception:
        return str(result)


def new_watch(conv, tab, turn, label, probe, every_s=DEFAULT_EVERY_S,
              wake="change", until_s=0, job_id="", blocked=None):
    try:
        every_s = max(MIN_EVERY_S, int(every_s or DEFAULT_EVERY_S))
    except Exception:
        every_s = DEFAULT_EVERY_S
    try:
        until_s = max(0, int(until_s or 0))
    except Exception:
        until_s = 0
    wake = (wake or "change").strip().lower()
    if wake not in WAKES:
        return {"error": f"`wake` is one of {list(WAKES)}."}
    if job_id:
        with _LOCK:
            j = _JOBS.get(job_id)
            if j is None or j.get("kind") != "watch" or j["state"] != "live":
                return {"error": f"no live watch '{job_id}'. background_list "
                                 "shows what is live."}
            j["every_s"], j["wake"], j["until_s"] = every_s, wake, until_s
            if label:
                j["label"] = label[:80]
            j["next_at"] = min(float(j.get("next_at") or 0), _now() + every_s)
        _save()
        return {"job": j, "updated": True}
    err = _check_probe(probe, blocked)
    if err:
        return {"error": err}
    with _LOCK:
        if len(_live()) >= ceiling():
            return {"error": f"the ceiling of live background jobs "
                             f"({ceiling()}, [CHAT] max_threads) is reached. "
                             "Stop one you no longer need with "
                             "background_stop, or wait for one to end."}
    h, pv, _full = _probe(probe)
    with _LOCK:
        j = {"id": _new_id(), "kind": "watch",
             "label": (label or _probe_label(probe))[:80],
             "conv": conv or "", "tab": tab or "", "turn": turn or "",
             "tool": probe.get("tool", "") if probe.get("kind") == "tool" else "",
             "probe": dict(probe), "every_s": every_s, "wake": wake,
             "until_s": until_s, "state": "live", "created": _now(),
             "fires": 0, "rounds": 0, "busy": False,
             "last_hash": h, "last_preview": pv, "next_at": _now() + every_s}
        _JOBS[j["id"]] = j
    _save()
    return {"job": j, "baseline": pv}


def _check_probe(probe, blocked):
    if not isinstance(probe, dict) or probe.get("kind") not in PROBES:
        return ("a watch needs ONE of: `file` (a path), `command` (a shell "
                "command) or `tool` + `arguments`.")
    k = probe["kind"]
    if k == "file" and not (probe.get("path") or "").strip():
        return "`file` needs a path."
    if k == "command":
        c = (probe.get("command") or "").strip()
        if not c:
            return "`command` is empty."
        if any(d in c.lower() for d in DANGEROUS):
            return "Blocked"
    if k == "tool":
        t = (probe.get("tool") or "").strip()
        if not t:
            return "`tool` needs the tool's exact name."
        if t in NOT_IN_BACKGROUND:
            return f"'{t}' cannot be a probe."
        if callable(blocked) and blocked(t):
            return f"'{t}' is not available to this agent."
    return ""


def _probe_label(probe):
    k = probe.get("kind")
    if k == "file":
        return os.path.basename(probe.get("path", "")) or "file"
    if k == "command":
        return (probe.get("command") or "")[:40]
    return probe.get("tool") or "tool"


def _hash(obj):
    if isinstance(obj, dict):
        obj = {k: v for k, v in obj.items() if k not in _VOLATILE}
    try:
        s = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        s = str(obj)
    return hashlib.sha1(s.encode("utf-8", "replace")).hexdigest()


def _probe(probe):
    k = (probe or {}).get("kind")
    try:
        if k == "file":
            p = os.path.expanduser(probe.get("path") or "")
            if not os.path.exists(p):
                st = {"exists": False}
                pv = f"{p}: missing"
            else:
                s = os.stat(p)
                st = {"exists": True, "size": s.st_size, "mtime": int(s.st_mtime),
                      "dir": os.path.isdir(p)}
                pv = (f"{p}: {'folder' if st['dir'] else 'file'}, {s.st_size} bytes, "
                      f"modified {time.strftime('%H:%M:%S', time.localtime(s.st_mtime))}")
            return _hash(st), pv, st
        if k == "command":
            r = run_shell(probe.get("command") or "", probe.get("cwd") or None,
                          timeout=PROBE_TIMEOUT_S, capture_only=True)
            st = {"exit_code": r.get("exit_code"), "output": r.get("output", "")}
            if r.get("error"):
                st["error"] = r["error"]
            pv = f"exit {st.get('exit_code')}: {(st.get('output') or st.get('error') or '')[:PREVIEW_CHARS]}"
            return _hash(st), pv, st
        if k == "tool":
            if _RUN is None:
                st = {"error": "no tool runner here"}
            else:
                st = _RUN(probe.get("tool") or "", probe.get("arguments") or {})
            if isinstance(st, str):
                try:
                    st = json.loads(st)
                except Exception:
                    pass
            pv = _result_text(st)[:PREVIEW_CHARS]
            return _hash(st), pv, st
    except Exception as e:
        st = {"error": str(e)}
        return _hash(st), f"error: {e}"[:PREVIEW_CHARS], st
    st = {"error": "unknown probe"}
    return _hash(st), "unknown probe", st


def _scheduler():
    while True:
        _WAKE.wait(1.0)
        _WAKE.clear()
        now = _now()
        due = []
        with _LOCK:
            for j in _live():
                if j.get("kind") != "watch" or j.get("busy"):
                    continue
                if float(j.get("next_at") or 0) <= now:
                    j["busy"] = True
                    j["next_at"] = now + float(j.get("every_s") or DEFAULT_EVERY_S)
                    due.append(j)
        for j in due:
            threading.Thread(target=_round, args=(j,), daemon=True,
                             name=f"watch-{j['id']}").start()


def _round(j):
    try:
        h, pv, _full = _probe(j.get("probe") or {})
        with _LOCK:
            if j["state"] != "live":
                return
            j["rounds"] = int(j.get("rounds") or 0) + 1
            changed = h != j.get("last_hash")
            before = j.get("last_preview") or ""
            j["last_hash"], j["last_preview"] = h, pv
            fire = changed or j.get("wake") == "always"
            if fire:
                j["fires"] = int(j.get("fires") or 0) + 1
            over = (j.get("until_s") and
                    _now() - float(j.get("created") or 0) >= float(j["until_s"]))
            if over:
                j["state"] = "done"
                j["ended"] = _now()
        if fire:
            _emit(j, "fired", _text_fired(j, before, pv, changed))
            _save()
        if over:
            _emit(j, "ended", _text_ended(j, "its time limit passed"))
            _save()
    finally:
        with _LOCK:
            j["busy"] = False


def stop(job_id, why="stopped"):
    with _LOCK:
        j = _JOBS.get(job_id)
        if j is None:
            return None
        was_live = j["state"] == "live"
        if was_live:
            j["state"] = "stopped"
            j["ended"] = _now()
            j["why"] = why
        proc = _PROCS.pop(job_id, None)
    if proc is not None:
        _kill(proc)
    if was_live:
        _save()
    return j


def stop_conv(conv="", tab=""):
    out = []
    with _LOCK:
        ids = [j["id"] for j in _live()
               if (conv and j.get("conv") == conv) or (tab and j.get("tab") == tab)]
    for i in ids:
        j = stop(i, "conversation closed")
        if j is not None:
            out.append(j["id"])
    return out


def get(job_id):
    with _LOCK:
        j = _JOBS.get(job_id)
    return _public(j) if j else None


def list_jobs(conv="", tab="", live_only=False):
    with _LOCK:
        js = list(_JOBS.values())
    if conv or tab:
        js = [j for j in js if (conv and j.get("conv") == conv)
              or (tab and j.get("tab") == tab)]
    if live_only:
        js = [j for j in js if j["state"] == "live"]
    js.sort(key=lambda j: j.get("created", 0))
    return [_public(j) for j in js]


def live_for_turn(turn_id):
    if not turn_id:
        return []
    with _LOCK:
        return [_public(j) for j in _live() if j.get("turn") == turn_id]


def live_for_conv(conv="", tab=""):
    with _LOCK:
        return [_public(j) for j in _live()
                if (conv and j.get("conv") == conv) or (tab and j.get("tab") == tab)]


def live_counts():
    acc = {}
    now = _now()
    with _LOCK:
        for j in _live():
            k = (j.get("conv") or "", j.get("tab") or "")
            e = acc.setdefault(k, {"conv": k[0], "tab": k[1], "n": 0, "labels": [],
                                   "jobs": []})
            e["n"] += 1
            if len(e["labels"]) < 6:
                e["labels"].append(j.get("label") or j.get("tool") or j["id"])
            if len(e["jobs"]) < 8:
                e["jobs"].append({
                    "id": j["id"], "kind": j.get("kind"), "label": j.get("label"),
                    "tool": j.get("tool"), "every_s": j.get("every_s"),
                    "fires": j.get("fires", 0), "rounds": j.get("rounds", 0),
                    "age_s": int(now - float(j.get("created") or now)),
                    "last_preview": str(j.get("last_preview") or "")[:80]})
    return list(acc.values())


CLAIM_S = 30
_CLAIMS = {}


def drain_events(conv="", tab=""):
    now = _now()
    with _LOCK:
        if conv or tab:
            _CLAIMS[(conv, tab)] = now
            keep, out = [], []
            for e in _EVENTS:
                if (conv and e.get("conv") == conv) or (tab and e.get("tab") == tab):
                    out.append(e)
                else:
                    keep.append(e)
            _EVENTS[:] = keep
            return out
        claimed = {k for k, t0 in _CLAIMS.items() if now - t0 < CLAIM_S}
        for k in list(_CLAIMS):
            if now - _CLAIMS[k] >= CLAIM_S:
                _CLAIMS.pop(k, None)
        keep, out = [], []
        for e in _EVENTS:
            mine = any((c and e.get("conv") == c) or (t and e.get("tab") == t)
                       for c, t in claimed)
            (keep if mine else out).append(e)
        _EVENTS[:] = keep
    return out


def peek_events():
    with _LOCK:
        return list(_EVENTS)


def _emit(j, what, text):
    ev = {"job_id": j["id"], "kind": j.get("kind"), "what": what,
          "label": j.get("label"), "tool": j.get("tool"), "conv": j.get("conv"),
          "tab": j.get("tab"), "turn": j.get("turn"), "at": _now(),
          "text": text, "message": frame_new_turn(text),
          "fires": j.get("fires", 0), "elapsed_s": int(_now() - float(j.get("created") or _now()))}
    taken = False
    if _DELIVER is not None:
        try:
            taken = bool(_DELIVER(j.get("conv") or "", j.get("tab") or "", text))
        except Exception:
            taken = False
    with _LOCK:
        j["delivered"] = "turn" if taken else "face"
        if not taken:
            _EVENTS.append(ev)
            del _EVENTS[:-EVENTS_MAX]
    return ev


def frame_new_turn(text):
    return ("[BACKGROUND EVENT] A job this conversation started earlier "
            "reports:\n" + text + "\n"
            "Nobody typed anything: this arrived on its own. Act on it as "
            "part of the work this conversation was doing — go on with that "
            "work if this is what it waited for, or take note and answer "
            "briefly.")


def _text_finished(j, text):
    out = text if len(text) <= RESULT_CHARS else (
        text[:RESULT_CHARS] + f"\n... ({len(text) - RESULT_CHARS} more characters cut"
        + (f"; the whole output is in {j.get('output_path')}" if j.get("output_path") else "")
        + ")")
    el = int(float(j.get("ended") or _now()) - float(j.get("created") or _now()))
    return (f"[BACKGROUND RESULT] job {j['id']} ('{j.get('label')}'): "
            f"{j.get('tool')} {'failed' if j.get('state') == 'failed' else 'finished'} "
            f"after {el} s.\n{out}")


def _text_fired(j, before, now, changed):
    head = (f"[BACKGROUND WATCH] '{j.get('label')}' (job {j['id']}) "
            f"{'fired: it changed' if changed else 'reports its round'}"
            f" (round {j.get('rounds')}, every {j.get('every_s')} s).")
    body = (f"\nBefore: {before}\nNow: {now}" if changed
            else f"\nNow: {now}")
    tail = (f"\nIt keeps watching. background_stop(\"{j['id']}\") ends it; "
            f"background_watch(job_id=\"{j['id']}\", every_s=N) changes its pace.")
    return head + body + tail


def _text_ended(j, why):
    return (f"[BACKGROUND WATCH] '{j.get('label')}' (job {j['id']}) ended: {why}, "
            f"after {j.get('rounds')} rounds and {j.get('fires')} changes. "
            "Start it again if you still need it.")


def _text_lost(j):
    return (f"[BACKGROUND RESULT] job {j['id']} ('{j.get('label')}'): {j.get('tool')} "
            "was LOST: HOPLITA restarted while it ran and its result is gone. "
            "Start it again if you still need it.")


def run_shell(command, cwd=None, timeout=600, capture_only=False):
    command = (command or "").strip()
    if not command:
        return {"error": "empty command"}
    if any(d in command.lower() for d in DANGEROUS):
        return {"error": "Blocked"}
    try:
        timeout = max(1, int(timeout or 600))
    except Exception:
        timeout = 600
    job = current_job()
    stem = job or _new_id()
    out_path = "" if capture_only else output_path(stem)
    t0 = _now()
    kw = {}
    try:
        import hoplita_paths
        kw.update(hoplita_paths.no_console())
    except Exception:
        pass
    if os.name == "posix":
        kw["start_new_session"] = True
    proc = None
    try:
        if capture_only:
            proc = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, cwd=cwd or None, **kw)
        else:
            fh = open(out_path, "wb")
            proc = subprocess.Popen(command, shell=True, stdout=fh,
                                    stderr=subprocess.STDOUT, cwd=cwd or None, **kw)
        if job:
            register_process(job, proc)
        try:
            if capture_only:
                data, _ = proc.communicate(timeout=timeout)
                text = (data or b"").decode("utf-8", "replace")
            else:
                proc.wait(timeout=timeout)
                fh.close()
                text = _tail(out_path)
        except subprocess.TimeoutExpired:
            _kill(proc)
            if not capture_only:
                fh.close()
                text = _tail(out_path)
            else:
                text = ""
            return {"error": f"Timeout ({timeout}s)", "exit_code": -1,
                    "output": text, "output_path": out_path,
                    "duration_s": round(_now() - t0, 1)}
        rc = proc.returncode
        out = {"exit_code": rc, "output": text[-OUTPUT_TAIL:],
               "duration_s": round(_now() - t0, 1)}
        if not capture_only:
            out["output_path"] = out_path
            if len(text) > OUTPUT_TAIL:
                out["truncated"] = True
                out["note"] = f"the tail is shown; the whole output is in {out_path}"
        if job and _JOBS.get(job, {}).get("state") == "stopped":
            out["stopped"] = True
        return out
    except Exception as e:
        return {"error": str(e)}
    finally:
        if job:
            _unregister_process(job)


def _tail(path, n=OUTPUT_TAIL * 4):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - n))
            return fh.read().decode("utf-8", "replace")
    except Exception:
        return ""


def _kill(proc):
    try:
        if proc.poll() is not None:
            return
        if os.name == "posix":
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:
                proc.terminate()
        else:
            proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            if os.name == "posix":
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:
                    proc.kill()
            else:
                proc.kill()
    except Exception:
        pass


def _reset_for_tests():
    with _LOCK:
        _JOBS.clear()
        _EVENTS.clear()
        _PROCS.clear()
