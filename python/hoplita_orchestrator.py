
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as _futwait_raw


_CURRENT = threading.local()


def current_subtask_id():
    return getattr(_CURRENT, "sid", "")


def _futwait(futures, timeout):
    done, pending = _futwait_raw(list(futures), timeout=timeout)
    return set(done), set(pending)

try:
    from hoplita_i18n import t as _t
except Exception:
    def _t(key, **kwargs):
        return key


CAPABILITY_TOOLS = {
    "web":       "browse",
    "research":  "read",
    "files":     "read",
    "terminal":  "full",
    "code":      "read",
    "write":     "full",
    "none":      "none",
}


def capabilities_to_profile(caps):
    if not caps:
        return "none"
    profiles = {CAPABILITY_TOOLS.get(c, "none") for c in caps}
    if "full" in profiles:
        return "full"
    if "browse" in profiles and "read" in profiles:
        return "browse_read"
    if "browse" in profiles:
        return "browse"
    if "read" in profiles:
        return "read"
    return "none"


def _extract_json(raw):
    if not raw:
        return None
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(cleaned[start:end + 1])
    except Exception:
        return None


EXPAND_HINT = (
    "\n\n[IF THIS TURNS OUT TO BE BIGGER] If while working you find this "
    "sub-task genuinely needs work that does not fit here, finish what you CAN "
    "do and end your reply with this exact JSON block:\n"
    '{"needs_split": [{"goal": "<concrete missing piece>", '
    '"role": "<who should do it>", "capabilities": ["web"]}]}\n'
    "Only if it is really necessary — asking for more when it is not wastes the "
    "user's time and money. If you finished, do NOT include the block.")


class Subtask:
    def __init__(self, sid, goal, role="agent", capabilities=None, deps=None,
                 model="", target="local"):
        self.id = sid
        self.goal = goal
        self.role = role or "agent"
        self.capabilities = capabilities or []
        self.model = model or ""
        self.target = (target or "local").strip() or "local"
        self.deps = deps or []
        self.status = "pending"
        self.result = None
        self.error = None
        self.attempts = 0

    @property
    def profile(self):
        return capabilities_to_profile(self.capabilities)

    def to_dict(self):
        return {"id": self.id, "goal": self.goal, "role": self.role,
                "capabilities": self.capabilities, "profile": self.profile,
                "model": self.model, "target": self.target,
                "deps": self.deps, "status": self.status,
                "result": self.result, "error": self.error,
                "attempts": self.attempts}


class Orchestrator:
    def __init__(self, llm_fn, agent_fn, progress_fn=None,
                 max_parallel=4, max_retries=2, should_stop=None,
                 subtask_timeout=1800,
                 max_expansions=2, max_subtasks=12, base_model_label="",
                 solo_single=False, model_alias=None):
        self.llm_fn = llm_fn
        self.agent_fn = agent_fn
        self.progress_fn = progress_fn or (lambda e: None)
        self.should_stop = should_stop or (lambda: False)
        self.max_parallel = max_parallel
        self.subtask_timeout = int(subtask_timeout or 0)
        self.max_retries = max_retries
        self.base_model_label = (base_model_label or "").strip()
        self.max_expansions = max_expansions
        self.max_subtasks = max_subtasks
        self.solo_single = bool(solo_single)
        self.model_alias = model_alias
        self._lock = threading.Lock()

    @staticmethod
    def is_solo(subtasks):
        if not subtasks or len(subtasks) != 1:
            return False
        st = subtasks[0]
        d = st.to_dict() if isinstance(st, Subtask) else dict(st)
        return ((str(d.get("target") or "local").strip() or "local") == "local"
                and str(d.get("model") or "").strip().lower()
                in ("", "base", "default"))

    def _ui_dicts(self, subs):
        out = []
        for s in subs:
            d = s.to_dict() if isinstance(s, Subtask) else dict(s)
            if not d.get("model") and self.base_model_label:
                d["model"] = self.base_model_label
            out.append(d)
        return out

    def plan(self, task, feedback=None, previous=None):
        system = _t("prompts.planner")
        user = f"{task}"
        if feedback and previous:
            prev_json = json.dumps(
                [s.to_dict() if isinstance(s, Subtask) else s for s in previous],
                ensure_ascii=False, indent=2)
            user = _t("prompts.planner_replan", task=task, prev=prev_json,
                      feedback=feedback)
        raw = self.llm_fn(system, user)
        data = _extract_json(raw)
        if not data or "subtasks" not in data:
            return None
        valid_caps = {"web", "research", "files", "terminal", "code", "write", "none"}
        subtasks = []
        seen_ids = set()
        for st in data["subtasks"]:
            sid = str(st.get("id", "")).strip()
            goal = str(st.get("goal", "")).strip()
            if not sid or not goal or sid in seen_ids:
                continue
            seen_ids.add(sid)
            caps = [c for c in st.get("capabilities", []) if c in valid_caps]
            subtasks.append(Subtask(
                sid, goal,
                role=str(st.get("role", "agent")).strip() or "agent",
                capabilities=caps or ["none"],
                model=str(st.get("model", "")).strip(),
                target=str(st.get("target", "local")).strip() or "local",
                deps=[str(d).strip() for d in st.get("deps", []) if str(d).strip()],
            ))
        if not subtasks:
            return None
        if self.model_alias is not None:
            for s in subtasks:
                if not s.model:
                    continue
                alias = self.model_alias(s.model)
                if alias is None:
                    self.progress_fn({"type": "note", "key": "plan.model_unknown",
                                      "args": {"id": s.id, "model": s.model}})
                    alias = ""
                s.model = alias
        valid_ids = {s.id for s in subtasks}
        for s in subtasks:
            s.deps = [d for d in s.deps if d in valid_ids and d != s.id]
        if self._has_cycle(subtasks):
            for i, s in enumerate(subtasks):
                s.deps = [subtasks[i - 1].id] if i > 0 else []
        if not (self.solo_single and feedback is None
                and self.is_solo(subtasks)):
            self.progress_fn({"type": "plan",
                              "subtasks": self._ui_dicts(subtasks)})
        return subtasks

    def _pop_split_request(self, result):
        if not result or "needs_split" not in result:
            return result, None
        data = _extract_json(result)
        if not isinstance(data, dict) or not isinstance(data.get("needs_split"), list):
            return result, None
        asked = []
        for it in data["needs_split"][:4]:
            if not isinstance(it, dict):
                continue
            goal = str(it.get("goal", "")).strip()
            if not goal:
                continue
            caps = [c for c in (it.get("capabilities") or [])
                    if c in {"web", "research", "files", "terminal", "code",
                             "write", "none"}]
            asked.append({"goal": goal,
                          "role": str(it.get("role", "")).strip() or "agent",
                          "capabilities": caps or ["none"]})
        clean = re.sub(r"\{[^{}]*needs_split.*", "", result, flags=re.S).strip()
        return (clean or result), (asked or None)

    def _graft(self, batch, subtasks, by_id, on_expand):
        proposals = []
        for st, asked in batch:
            for a in asked:
                if len(subtasks) + len(proposals) >= self.max_subtasks:
                    break
                sid = f"{st.id}x{len(proposals) + 1}"
                while sid in by_id:
                    sid += "b"
                proposals.append({"id": sid, "goal": a["goal"], "role": a["role"],
                                  "capabilities": a["capabilities"], "model": st.model,
                                  "target": st.target, "deps": [st.id],
                                  "requested_by": st.id})
        if not proposals:
            return 0
        self.progress_fn({"type": "expansion_requested", "subtasks": proposals})
        approved = on_expand(proposals)
        if not approved:
            self.progress_fn({"type": "expansion_rejected", "n": len(proposals)})
            return 0
        added = 0
        for d in approved:
            if len(subtasks) >= self.max_subtasks:
                break
            sid = str(d.get("id") or "").strip()
            goal = str(d.get("goal") or "").strip()
            if not sid or not goal or sid in by_id:
                continue
            st = Subtask(sid, goal, role=str(d.get("role") or "agent"),
                         capabilities=list(d.get("capabilities") or ["none"]),
                         model=str(d.get("model") or ""),
                         target=str(d.get("target") or "local"),
                         deps=[x for x in (d.get("deps") or []) if x in by_id])
            subtasks.append(st)
            by_id[sid] = st
            added += 1
        self.progress_fn({"type": "expansion_approved", "n": added})
        return added

    def _has_cycle(self, subtasks):
        graph = {s.id: s.deps for s in subtasks}
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {sid: WHITE for sid in graph}

        def visit(node):
            color[node] = GRAY
            for dep in graph.get(node, []):
                if color.get(dep) == GRAY:
                    return True
                if color.get(dep) == WHITE and visit(dep):
                    return True
            color[node] = BLACK
            return False

        return any(color[sid] == WHITE and visit(sid) for sid in graph)

    @staticmethod
    def _is_terminal(st, subtasks):
        return not any(st.id in (s.deps or []) for s in subtasks)

    def run(self, subtasks, on_expand=None):
        expansions = 0
        by_id = {s.id: s for s in subtasks}
        done = set()
        failed = set()
        pending_splits = []

        def snapshot():
            self.progress_fn({"type": "snapshot",
                              "subtasks": self._ui_dicts(subtasks)})

        def deps_ready(st):
            return all(d in done for d in st.deps)

        def deps_context(st):
            if not st.deps:
                return ""
            parts = []
            for d in st.deps:
                dep = by_id.get(d)
                if dep and dep.result:
                    parts.append(f"[Result of '{dep.goal}']:\n{dep.result}")
            return "\n\n".join(parts)

        def run_one(st):
            _CURRENT.sid = st.id
            st.status = "running"
            self.progress_fn({"type": "subtask_start", "id": st.id, "goal": st.goal,
                              "role": st.role,
                              "model": st.model or self.base_model_label})
            snapshot()
            ctx = deps_context(st)
            last_err = None
            for attempt in range(1, self.max_retries + 2):
                st.attempts = attempt
                try:
                    _goal = st.goal + (EXPAND_HINT if on_expand is not None
                                       and len(subtasks) < self.max_subtasks
                                       else "")
                    result = self.agent_fn(_goal, st.profile, ctx, st.model,
                                           st.target,
                                           self._is_terminal(st, subtasks))
                    if result and result.strip():
                        result, asked = self._pop_split_request(result)
                        if asked and on_expand is not None:
                            with self._lock:
                                pending_splits.append((st, asked))
                        st.result = result
                        st.status = "done"
                        self.progress_fn({"type": "subtask_done", "id": st.id,
                                          "attempts": attempt})
                        snapshot()
                        return
                    last_err = "empty result"
                except Exception as e:
                    last_err = str(e)
                self.progress_fn({"type": "subtask_retry", "id": st.id,
                                  "attempt": attempt, "error": last_err})
                snapshot()
                time.sleep(1)
            st.status = "failed"
            st.error = last_err
            self.progress_fn({"type": "subtask_failed", "id": st.id, "error": last_err})
            snapshot()

        pool = ThreadPoolExecutor(max_workers=self.max_parallel)
        try:
            while len(done) + len(failed) < len(subtasks):
                if self.should_stop():
                    for s in subtasks:
                        if s.status == "pending":
                            s.status = "failed"
                            s.error = "stopped by the user"
                            failed.add(s.id)
                    snapshot()
                    break
                ready = [s for s in subtasks
                         if s.status == "pending" and deps_ready(s)]
                if not ready:
                    blocked = [s for s in subtasks if s.status == "pending"]
                    if blocked and not any(s.status == "running" for s in subtasks):
                        for s in blocked:
                            if any(d in failed for d in s.deps):
                                s.status = "failed"
                                s.error = "dependency failed"
                                failed.add(s.id)
                                self.progress_fn({"type": "subtask_failed",
                                                  "id": s.id, "error": s.error})
                        if not [s for s in subtasks if s.status == "pending"]:
                            break
                    time.sleep(0.2)
                    self._wait_running(subtasks, done, failed)
                    continue
                futures = {pool.submit(run_one, s): s for s in ready}
                _left = set(futures)
                if self.subtask_timeout:
                    _fin, _late = _futwait(_left, timeout=self.subtask_timeout)
                    for fut in _late:
                        s = futures[fut]
                        s.status = "failed"
                        s.error = (f"no answer in {self.subtask_timeout}s — "
                                   f"cut so the plan could go on. It may still "
                                   f"be running in the background; nobody is "
                                   f"waiting for it any more.")
                        failed.add(s.id)
                        self.progress_fn({"type": "subtask_failed",
                                          "id": s.id, "error": s.error})
                    _left = _fin
                for fut in _left:
                    try:
                        fut.result()
                    except Exception as _e:
                        s = futures[fut]
                        if s.status not in ("done", "failed"):
                            s.status = "failed"
                            s.error = str(_e)[:200]
                            failed.add(s.id)
                            self.progress_fn({"type": "subtask_failed",
                                              "id": s.id, "error": s.error})
                if (pending_splits and on_expand is not None
                        and expansions < self.max_expansions):
                    with self._lock:
                        batch, pending_splits[:] = list(pending_splits), []
                    added = self._graft(batch, subtasks, by_id, on_expand)
                    if added:
                        expansions += 1
                        self.progress_fn({"type": "plan",
                                          "subtasks": self._ui_dicts(subtasks)})
                        snapshot()
                elif pending_splits:
                    pending_splits[:] = []
                for s in subtasks:
                    if s.status == "done":
                        done.add(s.id)
                    elif s.status == "failed":
                        failed.add(s.id)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        return by_id

    def _wait_running(self, subtasks, done, failed):
        for s in subtasks:
            if s.status == "done":
                done.add(s.id)
            elif s.status == "failed":
                failed.add(s.id)

    def synthesize(self, task, subtasks):
        results_text = []
        for s in subtasks:
            if s.status == "done" and s.result:
                results_text.append(f"### {s.goal}\n{s.result}")
            elif s.status == "failed":
                results_text.append(f"### {s.goal}\n[FAILED: {s.error}]")
        joined = "\n\n".join(results_text)
        system = _t("prompts.synthesizer")
        user = f"{task}\n\n{joined}"
        final = self.llm_fn(system, user)
        self.progress_fn({"type": "synthesis_done"})
        return final or joined

    def execute(self, task, approve_fn=None, on_expand=None):
        allow_expand = False
        self.progress_fn({"type": "start", "task": task})
        if self.should_stop():
            return "[stopped by the user before planning]", None
        subtasks = self.plan(task)
        if not subtasks:
            return None, None
        if self.solo_single and self.is_solo(subtasks):
            return "__SOLO__", subtasks

        if approve_fn is not None:
            while True:
                if self.should_stop():
                    return "[stopped]", subtasks
                plan_dicts = [s.to_dict() for s in subtasks]
                plan_before = [s.to_dict() for s in subtasks]
                _res = approve_fn(plan_dicts)
                decision, feedback = _res[0], _res[1]
                _opts = _res[2] if len(_res) > 2 and isinstance(_res[2], dict) else {}
                allow_expand = bool(_opts.get("allow_expand"))
                if decision == "run":
                    _by = {st.id: st for st in subtasks}
                    kept, kept_ids = [], set()
                    for d in plan_dicts:
                        st = _by.get(d.get("id"))
                        if st is None or st.id in kept_ids:
                            continue
                        st.target = (str(d.get("target") or "local").strip()
                                     or "local")
                        st.model = str(d.get("model") or "").strip()
                        goal = str(d.get("goal") or "").strip()
                        if goal:
                            st.goal = goal
                        kept.append(st)
                        kept_ids.add(st.id)
                    if not kept:
                        self.progress_fn({"type": "cancelled"})
                        return "__CANCELLED__", subtasks
                    for st in kept:
                        st.deps = [d for d in st.deps
                                   if d in kept_ids and d != st.id]
                    changed = [s.to_dict() for s in kept] != plan_before
                    subtasks = kept
                    if changed:
                        self.progress_fn({"type": "plan",
                                          "subtasks": self._ui_dicts(subtasks)})
                    break
                elif decision == "cancel":
                    self.progress_fn({"type": "cancelled"})
                    return "__CANCELLED__", subtasks
                elif decision == "edit" and feedback:
                    self.progress_fn({"type": "replanning"})
                    new_plan = self.plan(task, feedback=feedback, previous=subtasks)
                    if new_plan:
                        subtasks = new_plan
                else:
                    break

        if self.should_stop():
            return "[stopped by the user after planning]", subtasks
        by_id = self.run(subtasks, on_expand=on_expand if allow_expand else None)
        if self.should_stop():
            final = self.synthesize(task, list(by_id.values()))
            return (final or "[stopped]"), list(by_id.values())
        final = self.synthesize(task, list(by_id.values()))
        self.progress_fn({"type": "complete"})
        return final, list(by_id.values())
