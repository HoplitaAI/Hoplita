
import json
import os
import re
import threading
import time
from hoplita_prompts import _JUDGE_SYSTEM, _QA_SYSTEM, _QA_SYSTEM_FULL


_llm_chat = None
_get_tools = None
_get_tool_params = None
_max_iter = 8
_heavy_tools = ("browse_web",)
_translate = None
_get_max_tokens = None

TERMINAL_EVENTS = ("reply", "cancelled", "iteration_limit", "error",
                   "proxy_response", "proxy_end")

NEVER_DELEGATE = ("plan_and_orchestrate", "intent", "call_council",
                  "edit_my_config")
HARNESS_TAG = ("<INTERNAL HARNESS MESSAGE — no human wrote this; act on it, "
               "never reply to it>")
QA_TAG = "<AUTONOMOUS QA REVIEWER, NOT THE USER>"
HARNESS_TAGS = (HARNESS_TAG, QA_TAG)


_GATE_WORK_TOOLS = frozenset(("add_note", "update_note", "add_task", "task_depends",
                              "update_task_progress", "project_write"))


def _did_gate_work(trace, since, nudge=""):
    work = _GATE_WORK_TOOLS
    if "project_journal(" in (nudge or ""):
        work = frozenset(("project_journal", "project_write"))
    return any(isinstance(s, dict) and str(s.get("tool")) in work
               for s in (trace or [])[since:])

def _real_tools(trace):
    return sum(1 for s in (trace or []) if isinstance(s, dict)
               and str(s.get("tool")) != "intent")


def strip_images(messages, model=""):
    note = (f"[image not sent: {model or 'this model'} is declared without "
            "vision, so it cannot be shown pictures]")
    out, n = [], 0
    for m in messages or []:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, list) and any(isinstance(p, dict) and "image" in str(p.get("type", ""))
                                       for p in c):
            parts = []
            for p in c:
                if isinstance(p, dict) and "image" in str(p.get("type", "")):
                    parts.append({"type": "text", "text": note})
                    n += 1
                else:
                    parts.append(p)
            m = dict(m, content=parts)
        out.append(m)
    return out, n


def harness_msg(text, tag=HARNESS_TAG):
    return {"role": "user", "content": f"{tag}\n{text}"}


def is_harness(m):
    c = (m or {}).get("content")
    if isinstance(c, list):
        c = next((p.get("text", "") for p in c
                  if isinstance(p, dict) and p.get("type") == "text"), "")
    return isinstance(c, str) and c.lstrip().startswith(HARNESS_TAGS)


PLAN_GATE_KEEP = "[not a plan]"
PROGRESS_GATE_KEEP = "[no task landed]"
JOURNAL_GATE_KEEP = "[nothing to journal]"
GATE_KEEP_TOKENS = (PLAN_GATE_KEEP, PROGRESS_GATE_KEEP, JOURNAL_GATE_KEEP)
NEVER_DELEGATE_PREFIXES = ("hands_", "wb_")

_FALLBACK_STRINGS = {
    "llm.trunc_server": "{text}\n\n⚠ Response truncated by the LLM server "
                        "(completion_tokens={ct}, client requested {cm}).",
    "llm.trunc_client": "{text}\n\n⚠ Response truncated by the client's "
                        "max_tokens (completion_tokens={ct}/{cm}).",
    "llm.filtered": "{text}\n\n⚠ Response filtered by the server's moderation.",
    "inject.escalated":
        "[HANDED OVER — you are now {model}] The previous model stalled on "
        "this turn: same paths, no new data. You have its whole trace above. "
        "Do NOT repeat what already failed — say briefly what you think went "
        "wrong, choose a different approach, and carry on.",
    "canvas.shot":
        "[CANVAS SCREENSHOT — what the canvas ACTUALLY shows right now, from "
        "canvas_see. Judge from THIS image: composition, positions, colours, "
        "overlap. If it does not match what your code intends, the image is "
        "the truth, not the code.]",
    "tools.withheld":
        "{names}: it exists, but in this role you do NOT have it. Do not ask "
        "for it again and do not call it: it is not in your payload and it "
        "will not be.",
    "tools.withheld_canvas":
        "It works on a shared surface, and the LAST task paints it — the one "
        "nobody depends on, which is not you: there is work after yours. "
        "Deliver the finished content in your answer and say it is ready to "
        "paint.",
    "inject.mid_turn":
        "[WHILE YOU WORK — the user says:] {text}\n"
        "This is an ASIDE, not a new request. Deal with it now, then RESUME "
        "the task you were on exactly where you left it: the turn is not "
        "over until the ORIGINAL request is complete. If this message "
        "cancels or replaces that request, it will say so — do not assume "
        "it does.",
    "inject.background":
        "[WHILE YOU WORK — one of your background jobs reports:] {text}\n"
        "This is not a new request. Take it into account now, then RESUME "
        "the task you were on exactly where you left it: the turn is not "
        "over until the ORIGINAL request is complete.",
    "inject.metacog_background":
        "[YOUR BACKGROUND JOBS] {n} of them are live: {names}. If what you "
        "need is one of their results, do not wait for it with more tool "
        "calls: end the turn with a short answer and it will wake you when "
        "it lands. Stop the ones you no longer need with background_stop.",
    "inject.resume_original":
        "[BEFORE YOU ANSWER] The user interrupted this turn with an aside "
        "and you handled it. The request that STARTED the turn was:\n"
        "{request}\n"
        "Is THAT complete? If not, do not answer yet — carry on with it. If "
        "it is, or the aside made it moot, say so in one line and answer.",
    "inject.intent_open":
        "[YOUR OWN LIST IS STILL OPEN] You wrote this plan and you are about "
        "to answer with items neither done nor dropped:\n{list}\n"
        "Either finish them now, or mark with intent() what is done, what "
        "failed and what no longer applies — and say so in one line. A plan "
        "nobody closes is not a plan.",
    "inject.metacog_loop":
        "[STOP — you are repeating yourself] {tool} has been called {n} "
        "times with the same arguments and came back with the same result. "
        "Doing it again will return the same. Before anything else, answer "
        "briefly, out loud: (1) what are you assuming that the result says "
        "is wrong? (2) what is DIFFERENT to try — another tool, other "
        "arguments, looking first (a capture, a listing, a read) before "
        "acting again? Then take that different path. If it cannot be done "
        "this way, say what is missing.",
    "inject.metacog_last":
        "[STILL STUCK — after you were asked to rethink] Nothing has moved "
        "since. There is no other model to hand this to, so it is yours, and "
        "there are two honest ways on: take a REALLY different approach now "
        "(not another variant of what failed), or stop and tell the user "
        "exactly what blocks you and what you would need. Say which, then do it.",
    "inject.metacog_close":
        "[STOP — answer now] You were asked twice to change course and "
        "nothing moved. No more tools. Tell the user, plainly and briefly: "
        "what you tried, what you established, what blocks you, and what "
        "they (or another model or tool) would need to provide to get past it.",
    "inject.metacog_council":
        "[YOU CAN ASK YOUR OTHER MODELS] If you do not see whether to stop "
        "or what else to do with what you have, convene a council: "
        "call_council(topic=...) — directly, or through use_tool if it is not "
        "in your list — with what you are trying to do, what keeps happening, "
        "what you have so far, and the question (stop here, or what else to "
        "try?). They answer with a deliberated view and any dissent. It costs "
        "several model calls: worth it when you really do not see the way; "
        "if you do, just take it.",
    "inject.metacog_nudge":
        "[PAUSE — think before acting] Look at what you have actually "
        "achieved so far and what keeps resisting. Answer briefly, out "
        "loud: (1) Is the current path producing new data? (2) If not, "
        "what DIFFERENT path could — another source, another angle, "
        "another of your tools? (3) Update your intent list with "
        "intent() to match. Then continue — or, if the goal is already "
        "met or truly out of reach, say so honestly.",
    "inject.metacog_list": "Your intent list right now:\n{list}",
}


def _t(key, **kwargs):
    if _translate:
        try:
            s = _translate(key, **kwargs)
            if s and s != key:
                return s
        except Exception:
            pass
    tpl = _FALLBACK_STRINGS.get(key, key)
    try:
        return tpl.format(**kwargs)
    except (KeyError, IndexError):
        return tpl


_INJECT = {}
_INJECT_LOCK = threading.Lock()
_INJECT_MAX_TURNS = 64
_LOOP_LIMIT = 4

_RESULT_CAP = 4000

_LINE_ORIENTED_TOOLS = {"code_read_smart", "code_read_range", "code_search",
                        "code_index", "code_references", "code_deps",
                        "read_file", "run_command",
                        "git_diff", "git_show", "git_log", "git_status",
                        "read_note"}


def _range_hint(name):
    try:
        props = ((_get_tool_params() or {}).get(name) or {}).get("properties") or {}
    except Exception:
        props = {}
    if "start_line" in props:
        return (f"re-call {name} with the same arguments plus start_line and "
                f"end_line for the lines you need")
    if name == "run_command":
        return ("run it again filtered to the part you need (sed -n 'A,Bp', "
                "tail, grep), unless running it twice is not safe")
    if name.startswith("git_"):
        return f"re-call {name} with narrower arguments (a path, a range)"
    return ("code_read_range(path, start_line, end_line) for the lines you "
            "need, or re-call with narrower arguments")


_LINE_WINDOW_SHARE = 0.15
_LINE_CHARS_PER_TOKEN = 3
_LINE_CAP_MAX = 100000


def line_cap(window):
    try:
        w = int(window or 0)
    except (TypeError, ValueError):
        w = 0
    return max(_RESULT_CAP, min(_LINE_CAP_MAX,
                                int(w * _LINE_CHARS_PER_TOKEN * _LINE_WINDOW_SHARE)))


def line_cap_for_tool():
    return int(getattr(_tool_emit, "line_cap", 0) or 0) or _RESULT_CAP


_LAST_WINDOW = 0


def last_window():
    return _LAST_WINDOW


def _remember_window(w):
    global _LAST_WINDOW
    try:
        _LAST_WINDOW = int(w or 0) or _LAST_WINDOW
    except (TypeError, ValueError):
        pass


_FILE_READERS = ("read_file", "code_read_range")


def _cut_text_field(name, args, result, cap):
    try:
        obj = json.loads(result)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    texts = [(k, v) for k, v in obj.items() if isinstance(v, str)]
    lists = [(k, v) for k, v in obj.items() if isinstance(v, list) and v]
    if lists:
        lkey, lst = max(lists, key=lambda kv: len(json.dumps(kv[1], ensure_ascii=False)))
        tlen = max((len(v) for _k, v in texts), default=0)
        if len(json.dumps(lst, ensure_ascii=False)) > tlen:
            rest = dict(obj)
            rest[lkey] = []
            budget = max(400, cap - len(json.dumps(rest, ensure_ascii=False)) - 600)
            kept, size = [], 0
            for it in lst:
                w = len(json.dumps(it, ensure_ascii=False)) + 2
                if kept and size + w > budget:
                    break
                kept.append(it)
                size += w
            obj[lkey] = kept
            obj["cut"] = (f"items 1-{len(kept)} of {len(lst)} in '{lkey}'. The "
                          f"rest EXISTS and is not here. Never fill it in "
                          f"from memory.")
            obj["next"] = (f"re-call {name} narrower (a sub-path, a "
                           f"file_pattern, a longer and more specific query), "
                           f"or search the full list where it is kept.")
            return json.dumps(obj, ensure_ascii=False), 1, len(kept), len(lst)
    if not texts:
        return None
    key, text = max(texts, key=lambda kv: len(kv[1]))
    lines = text[:-1].split("\n") if text.endswith("\n") else text.split("\n")
    first = 1
    total = len(lines)
    if name == "code_read_range":
        try:
            first = int(str(obj.get("range", "1")).split("-")[0])
            total = int(obj.get("total_lines") or total)
        except (TypeError, ValueError):
            first = 1
    rest = dict(obj)
    rest[key] = ""
    budget = max(400, cap - len(json.dumps(rest, ensure_ascii=False)) - 600)
    kept, size = [], 0
    for ln in lines:
        w = len(json.dumps(ln, ensure_ascii=False))
        if kept and size + w > budget:
            break
        kept.append(ln)
        size += w
    if len(kept) == 1 and size > budget:
        kept = [kept[0][:budget]]
    last = first + len(kept) - 1
    obj[key] = "\n".join(kept)
    obj["cut"] = (f"lines {first}-{last} of {total}. The rest EXISTS and is "
                  f"not here. Never fill it in from memory.")
    path = (args or {}).get("path")
    if name in _FILE_READERS and path and last < total:
        obj["next"] = (f"code_read_range(path={json.dumps(path)}, "
                       f"start_line={last + 1}, end_line={min(total, last + len(kept))})")
    else:
        obj["next"] = _range_hint(name)
    return json.dumps(obj, ensure_ascii=False), first, last, total


def _lines_seen(name, args, result):
    path = (args or {}).get("path")
    if name not in _FILE_READERS or not path:
        return None
    try:
        obj = json.loads(result)
    except Exception:
        return None
    if not isinstance(obj, dict) or obj.get("error"):
        return None
    m = re.match(r"lines (\d+)-(\d+) of (\d+)", str(obj.get("cut") or ""))
    if m:
        a, b, n = (int(x) for x in m.groups())
        return path, a, b, n
    if name == "code_read_range":
        try:
            a, b = (int(x) for x in str(obj.get("range", "")).split("-"))
            return path, a, b, int(obj.get("total_lines") or b)
        except (TypeError, ValueError):
            return None
    n = len(str(obj.get("result") or "").splitlines())
    return path, 1, n, n


def set_result_cap(n):
    global _RESULT_CAP
    try:
        _RESULT_CAP = max(1000, int(n))
    except Exception:
        pass


_spill_fn = None
_DIGEST_INPUT_MAX = 60000


def configure_spill(fn):
    global _spill_fn
    _spill_fn = fn


def _content_of(raw):
    m_ = ((raw or {}).get("message") if isinstance(raw, dict) and "message" in raw
          else (raw or {}))
    return ((m_ or {}).get("content", "") or "").strip()


def _digest_chunks(text, size):
    if len(text) <= size:
        return [text]
    out, i = [], 0
    while i < len(text):
        end = min(i + size, len(text))
        if end < len(text):
            cut = text.rfind("\n", i + size // 2, end)
            if cut > i:
                end = cut + 1
        out.append(text[i:end])
        i = end
    return out


def _take_verbatim(result):
    if '"_verbatim"' not in result:
        return ""
    try:
        v = json.loads(result).get("_verbatim")
    except Exception:
        return ""
    v = str(v or "").strip()
    return (v[:1200] + "\n\n") if v else ""


def _digest_result(name, args, result, messages, chat):
    try:
        tail = _conversation_tail(messages)[-6:]
        parts = _digest_chunks(result, _DIGEST_INPUT_MAX)
        n = len(parts)
        share = max(600, _RESULT_CAP // n)
        budget = max(512, ((share // 3) + 128) * 2)
        head = ("You compress tool output. Data survives, padding dies. "
                "Never invent.")
        pieces = []
        for k, piece in enumerate(parts, 1):
            where = f" (PART {k} OF {n} of a long result)" if n > 1 else ""
            user = (
                "A tool just returned a LARGE result. Compress it for the agent "
                "mid-task.\n\nCONVERSATION (oldest first — what is being worked "
                "on):\n  " + "\n  ".join(tail)
                + f"\n\nTOOL: {name}({json.dumps(args, ensure_ascii=False)[:300]})"
                + f"\n\nRESULT{where}:\n" + piece
                + "\n\nWrite the extract: EVERY datum relevant to the "
                  "conversation — names, figures, addresses, prices, ratings, "
                  "dates, colours, sizes, quotes — dense lines, nothing made "
                  "up, no commentary, no summary prose. If this part is "
                  "boilerplate with little data, say so in one line.")
            msgs = [{"role": "system", "content": head},
                    {"role": "user", "content": user}]
            try:
                raw = chat(msgs, None, max_tokens=budget)
            except TypeError:
                raw = chat(msgs, None)
            got = _content_of(raw)
            if not got:
                got = _content_of(chat(msgs, None))
            if got:
                pieces.append(f"[part {k}/{n}]\n{got}" if n > 1 else got)
        if not pieces:
            return ""
        out = "\n\n".join(pieces)
        if n > 1:
            out = (f"[digested in {n} parts — ALL of the result was read]\n"
                   + out)
        return out
    except Exception:
        return ""


def inject(turn_id, text, kind="user"):
    text = (text or "").strip()
    if not text:
        return False
    with _INJECT_LOCK:
        if turn_id not in _INJECT:
            return False
        _INJECT[turn_id].append((text, kind if kind == "background" else "user"))
        return True


def withdraw(turn_id, text):
    text = (text or "").strip()
    with _INJECT_LOCK:
        box = _INJECT.get(turn_id)
        for i, (msg, kind) in enumerate(box or []):
            if msg == text and kind == "user":
                del box[i]
                return True
    return False


def forget_turn(turn_id):
    with _INJECT_LOCK:
        _INJECT.pop(turn_id, None)
    with _TURN_GOALS_LOCK:
        _TURN_GOALS.pop(turn_id, None)


def _watch_turn(turn_id):
    with _INJECT_LOCK:
        if len(_INJECT) >= _INJECT_MAX_TURNS:
            _INJECT.pop(next(iter(_INJECT)), None)
        _INJECT[turn_id] = []


def _drain_injections(turn_id):
    with _INJECT_LOCK:
        pending = _INJECT.get(turn_id) or []
        if pending:
            _INJECT[turn_id] = []
    return pending


def set_max_iter(n):
    global _max_iter
    try:
        _max_iter = max(1, int(n))
    except Exception:
        pass


_METACOG = {"enabled": True, "stall_seconds": 120, "stall_tools": 4,
            "heavy_cut": 3}
_METACOG_MAX_NUDGES = 2


def set_metacog(enabled=None, stall_seconds=None, stall_tools=None,
                heavy_cut=None):
    try:
        if enabled is not None:
            _METACOG["enabled"] = bool(enabled)
        if stall_seconds is not None:
            _METACOG["stall_seconds"] = max(10, int(stall_seconds))
        if stall_tools is not None:
            _METACOG["stall_tools"] = max(2, int(stall_tools))
        if heavy_cut is not None:
            _METACOG["heavy_cut"] = max(2, int(heavy_cut))
    except Exception:
        pass


def configure_deferred(hot, catalogue, on_tool_used=None):
    global _hot_tools, _tool_catalogue, _on_tool_used
    _hot_tools, _tool_catalogue = hot, catalogue
    _on_tool_used = on_tool_used


def prefix_divergence(prev_messages, prev_tools, messages, tools, ctx_chars=70):
    if prev_messages is None:
        return None
    if json.dumps(prev_tools or [], sort_keys=True) != json.dumps(tools or [], sort_keys=True):
        before = {t.get("function", {}).get("name") for t in (prev_tools or [])}
        after = {t.get("function", {}).get("name") for t in (tools or [])}
        added, gone = sorted(after - before), sorted(before - after)
        return ("tools block changed" + (f": +{added}" if added else "")
                + (f" -{gone}" if gone else "") + ("" if added or gone else " (same names, different schemas)"))
    n = min(len(prev_messages), len(messages))
    for i in range(n):
        a = json.dumps(prev_messages[i], ensure_ascii=False, sort_keys=True)
        b = json.dumps(messages[i], ensure_ascii=False, sort_keys=True)
        if a != b:
            off = next((k for k in range(min(len(a), len(b))) if a[k] != b[k]), min(len(a), len(b)))
            role = messages[i].get("role", "?")
            lo = max(0, off - ctx_chars // 2)
            return (f"message #{i} ({role}) differs at char {off}: "
                    f"{a[lo:off + ctx_chars]!r} -> {b[lo:off + ctx_chars]!r}")
    if len(messages) < len(prev_messages):
        return f"shorter than the previous request: {len(messages)} < {len(prev_messages)} messages"
    return None


def single_system(messages):
    out, head, rest = [], [], []
    for m in (messages or []):
        if m.get("role") == "system" and isinstance(m.get("content"), str):
            head.append(m)
        else:
            rest.append(m)
    if len(head) <= 1:
        return list(messages or [])
    merged = {**head[0],
              "content": "\n\n".join(m.get("content", "") for m in head
                                      if (m.get("content") or "").strip())}
    out.append(merged)
    out.extend(rest)
    return out


_side_chat = None


def configure_side_chat(fn):
    global _side_chat
    _side_chat = fn


def configure(llm_chat, get_tools, get_tool_params, max_iter=8,
              heavy_tools=("browse_web",),
              translate=None, get_max_tokens=None, qa_budget_chars=None):
    global _llm_chat, _get_tools, _get_tool_params, _max_iter, _heavy_tools
    global _translate, _get_max_tokens, _QA_BUDGET_CHARS
    _llm_chat = llm_chat
    _get_tools = get_tools
    _get_tool_params = get_tool_params
    _max_iter = max_iter
    _heavy_tools = tuple(heavy_tools or ())
    _translate = translate
    _get_max_tokens = get_max_tokens
    if qa_budget_chars:
        _QA_BUDGET_CHARS = max(2000, int(qa_budget_chars))


_hot_tools = None
_tool_catalogue = None


def _tools_as_openai(allowed=None, expanded=None):
    tools = _get_tools() or {}
    params = _get_tool_params() or {}
    keep = None if allowed is None else set(allowed)
    exp = None if expanded is None else set(expanded)
    out = []
    for name, spec in tools.items():
        if keep is not None and name not in keep:
            continue
        if exp is not None and name not in exp:
            continue
        p = params.get(name, {"properties": {}, "required": []})
        out.append({
            "type": "function",
            "function": {
                "name": name,
                "description": spec.get("desc", ""),
                "parameters": {
                    "type": "object",
                    "properties": p.get("properties", {}),
                    "required": p.get("required", []),
                },
            },
        })
    if exp is not None:
        out.append(USE_TOOL)
    return out


USE_TOOL = {
    "type": "function",
    "function": {
        "name": "use_tool",
        "description": "Call ANY tool from the AVAILABLE TOOLS catalogue by "
                       "name, with its arguments as an object. Use it for "
                       "tools that are not in your tools list; ask "
                       "load_tools(names=[...]) first when you need their "
                       "parameters.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string",
                         "description": "the tool's exact name"},
                "arguments": {"type": "object",
                              "description": "its arguments",
                              "additionalProperties": True},
            },
            "required": ["name"],
        },
    },
}

_on_tool_used = None
_consent = None
_background = None


def configure_background(adapter):
    global _background
    _background = adapter


BACKGROUND_NEVER = frozenset({
    "background_run", "background_watch", "background_stop",
    "background_list", "use_tool", "intent", "load_tools",
    "plan_and_orchestrate", "call_council", "sticky_note"})


def call_args_or_complaint(raw, name, cut=False):
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return {}, None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as e:
            out = {"error": f"the arguments of this call to '{name}' are not valid "
                            f"JSON ({e.msg} at character {e.pos} of {len(raw)}). "
                            "Nothing was done.",
                   "it_ended_with": raw[-120:]}
            if cut:
                out["cut"] = True
                out["hint"] = ("your reply was cut by the output limit before this "
                               "call closed: send what it carries in smaller pieces")
            else:
                out["hint"] = ("re-send the call with its arguments as one valid JSON "
                               "object: quotes, backslashes and line breaks inside "
                               "strings escaped")
            return None, out
        if raw is None:
            return {}, None
    if not isinstance(raw, dict):
        return None, {"error": f"the arguments of this call to '{name}' must be a JSON "
                               f"object; they came as {_kind_of(raw)}. Nothing was done."}
    return raw, None


def _unwrap_background(args):
    if not isinstance(args, dict):
        return "", {}, "", "background_run needs `tool` and `arguments`."
    real = str(args.get("tool", "") or "").strip()
    inner, bad = call_args_or_complaint(args.get("arguments", {}), real or "background_run")
    if bad:
        return real, {}, "", bad["error"]
    label = str(args.get("label", "") or "").strip()[:80]
    if real == "use_tool":
        real = str(inner.get("name", "") or "").strip()
        inner = inner.get("arguments", {}) if isinstance(inner.get("arguments"), dict) else {}
    if not real:
        return "", {}, label, ("background_run needs `tool` (the tool's exact "
                               "name) and `arguments` (an object).")
    if real in BACKGROUND_NEVER:
        return real, inner, label, (f"'{real}' cannot run in the background: "
                                    "call it directly.")
    return real, inner, label, ""


def configure_consent(fn):
    global _consent
    _consent = fn

_TURN_GOALS = {}
_TURN_GOALS_LOCK = threading.Lock()


RECORD_SENTINELS = (
    "[REGISTRO INTERNO DEL TURNO", "[INTERNAL TURN RECORD",
    "[Lo que hice en el turno anterior", "[What I did in the previous turn")

_RECORD_EN = {
    "chat.context_full": "[⚠ CONTEXT FULL — the model could not write "
                         "anything: the prompt is {pt} tokens and the "
                         "completion was cut at once. Nothing to redo. "
                         "Reset or compact this conversation, or open a "
                         "new tab, and ask again.]",
    "chat.tool_call_cut": "[⚠ TOOL CALL CUT — the model began a call to `{tool}` and the output limit cut it after {ct} completion tokens (max_tokens {cm}): its reasoning spent the budget before the call closed, and the server passed the fragment on as text. Nothing to redo. Raise [CHAT] max_tokens or lower the model's reasoning effort, and ask again.]",
    "chat.tool_call_text": "[⚠ TOOL CALL AS TEXT — the model wrote its call to `{tool}` as text: its server did not read it as a call and neither could this loop. Nothing to hand over. Ask again; if it repeats, the model's chat template and its server disagree on how a call is written.]",
    "verify.blocked_reply": "[⚠ the check `{cmd}` could NOT run: {why}. The "
                            "definition of done was not checked this turn — "
                            "unblock run_command for this conversation, or "
                            "drop the check.]",
    "chat.turn_trace": "[INTERNAL TURN RECORD — previous turn, tool → "
                       "result. Not an answer; never quoted:\n{steps}\n"
                       "A value cut short here is re-fetched with the tool "
                       "— filling it in from memory is making it up.]",
    "chat.turn_trace_cut": " …(cut)",
    "chat.turn_trace_more": "  …and {n} more step(s)",
    "chat.turn_qa_fail": "  ⚠ the reviewer rejected an answer: {why}",
    "chat.turn_qa_ok": "  ✓ the reviewer passed the final answer",
    "chat.turn_trace_failed": "  ✗ the turn DIED: {e}",
    "chat.turn_echo_dead": "⚠ I have no valid answer: I kept producing my "
                           "internal record instead of addressing your "
                           "message. Ask again or rephrase.",
    "chat.turn_qa_capped": "[⚠ UNVERIFIED: the reviewer rejected this "
                           "answer and the redo budget ran out. Last "
                           "objection: {why}]",
}

_RECORD_STEPS = 12
_RECORD_RESULT_CHARS = 1000
_RECORD_ARGS_CHARS = 160


def _rtr(key, **kw):
    if _translate is not None:
        try:
            out = _translate(key, **kw)
            if out and out != key:
                return out
        except Exception:
            pass
    return _RECORD_EN.get(key, key).format(**kw)


_LOOP_MSG_PREFIXES = ("[QUALITY REVIEW FAILED]", "[DEFINITION OF DONE",
                      "[NOT AN ANSWER]", "[INSTRUCTION]", "[PRE-FLIGHT",
                      "[PAUSE",
                      "[WHILE YOU WORK", "[BEFORE YOU ANSWER")

INJECTED_MSG_PREFIXES = ("[FIXED CONTEXT]", "[USER FIXED]", "[PDF:", "[File ",
                         "=== FIXED CONTEXTS ===", "[SYSTEM]", "[RAG]",
                         "[STICKY NOTE")

_INJECT_SEP = "\n\n---\n\n"

_INJECTED_HEADS = ("[MEMORY", "[MEMORIA", "[LESSONS", "[LECCIONES",
                   "[RULES", "[REGLAS", "[SKILL", "[HABILIDAD", "[PROJECT",
                   "[PROYECTO", "[CODE PROJECT", "[TOOLS", "[HERRAMIENTAS",
                   "🗄",
                   "[TODAY", "[HOY")


def user_said(m):
    c = (m or {}).get("content")
    if isinstance(c, list):
        c = "\n".join(p.get("text", "") for p in c
                       if isinstance(p, dict) and p.get("type") == "text")
    if not isinstance(c, str):
        return ""
    t = c.strip()
    if _INJECT_SEP in t and t.startswith(("[",) + _INJECTED_HEADS + HARNESS_TAGS):
        t = t.rsplit(_INJECT_SEP, 1)[-1].strip()
    if t.startswith(INJECTED_MSG_PREFIXES) or t.startswith(_INJECTED_HEADS) \
            or t.startswith(HARNESS_TAGS):
        return ""
    return t





def _conversation_tail(messages, per_line=0):
    tail = []
    for m in messages or []:
        if m.get("role") not in ("user", "assistant"):
            continue
        c = m.get("content")
        if isinstance(c, list):
            c = "\n".join(p.get("text", "") for p in c
                           if isinstance(p, dict) and p.get("type") == "text")
        if not isinstance(c, str) or not c.strip():
            continue
        if c.startswith(_LOOP_MSG_PREFIXES) or c.lstrip().startswith(HARNESS_TAGS):
            continue
        for s_ in RECORD_SENTINELS:
            j = c.find(s_)
            if j >= 0:
                c = c[:j].rstrip()
                break
        if c:
            _line = c.replace(chr(10), " ")
            tail.append(f"{m['role']}: "
                        + (_line[:per_line] if per_line else _line))
    return tail


def _preflight_expensive(name, args, messages, chat):
    try:
        tail = _conversation_tail(messages)
        if len(tail) < 2:
            return True, "", ""
        prompt = (
            f"[PRE-FLIGHT CHECK] An agent is about to run the EXPENSIVE "
            f"tool {name} with arguments:\n{json.dumps(args, ensure_ascii=False)[:600]}\n\n"
            "Conversation so far (oldest first):\n  " + "\n  ".join(tail[-8:])
            + "\n\nDo the arguments CONTRADICT anything the conversation "
              "already settled? A settled referent can be of ANY kind — "
              "the thing itself, its place, its colour, its size, its "
              "date, its version, its id: whatever got fixed. "
              "Contradiction means the conversation established X and the "
              "arguments name a DIFFERENT X of the same kind. Details the "
              "conversation never mentioned are NOT contradictions.\n"
              'Reply ONLY JSON: {"ok": true} or {"ok": false, '
              '"why": "<one line>", "fix": "<corrected value>"}')
        raw = chat([{"role": "system",
                     "content": "You check one thing and answer JSON."},
                    {"role": "user", "content": prompt}], None)
        m_ = (raw or {}).get("message") if isinstance(raw, dict)             and "message" in raw else (raw or {})
        txt = (m_ or {}).get("content", "") or ""
        mm = re.search(r"\{.*\}", txt, re.S)
        if not mm:
            return True, "", ""
        data = json.loads(mm.group(0))
        if data.get("ok") is False:
            return (False, str(data.get("why", ""))[:200],
                    str(data.get("fix", ""))[:200])
    except Exception:
        pass
    return True, "", ""


def render_turn_record(tool_trace, qa_verdicts=None, failed=""):
    qa_verdicts = qa_verdicts or []
    if not tool_trace and not qa_verdicts and not failed:
        return ""
    lines = []
    for tr in (tool_trace or [])[:_RECORD_STEPS]:
        res = str(tr.get("result", tr.get("result_preview", "")) or "")
        res = res.replace("\n", " ").strip()
        shown = res[:_RECORD_RESULT_CHARS]
        cut = _rtr("chat.turn_trace_cut") if len(res) > len(shown) else ""
        try:
            args = json.dumps(tr.get("args", {}),
                              ensure_ascii=False)[:_RECORD_ARGS_CHARS]
        except Exception:
            args = str(tr.get("args", ""))[:_RECORD_ARGS_CHARS]
        lines.append(f"  {tr.get('tool', '?')}({args})"
                     + (f" → {shown}{cut}" if res else ""))
    if tool_trace and len(tool_trace) > _RECORD_STEPS:
        lines.append(_rtr("chat.turn_trace_more",
                          n=len(tool_trace) - _RECORD_STEPS))
    for v in qa_verdicts:
        if not v.get("ok"):
            lines.append(_rtr("chat.turn_qa_fail", why=v.get("why", "")))
    if qa_verdicts and qa_verdicts[-1].get("ok"):
        lines.append(_rtr("chat.turn_qa_ok"))
    if failed:
        lines.append(_rtr("chat.turn_trace_failed", e=str(failed)[:200]))
    return _rtr("chat.turn_trace", steps="\n".join(lines))


def set_turn_goal(turn_id, goal):
    if not turn_id or not (goal or "").strip():
        return
    with _TURN_GOALS_LOCK:
        _TURN_GOALS[turn_id] = goal.strip()


def _take_turn_goal(turn_id):
    with _TURN_GOALS_LOCK:
        return _TURN_GOALS.get(turn_id, "")


_tool_emit = threading.local()


def conversation_tail_for_tool():
    return list(getattr(_tool_emit, "tail", []) or [])


def conversation_for_tool():
    return list(getattr(_tool_emit, "messages", []) or [])


def turn_for_tool():
    return getattr(_tool_emit, "turn", "") or ""


def tool_may(name):
    fn = getattr(_tool_emit, "may", None)
    if fn is None:
        return True
    try:
        return bool(fn(name))
    except Exception:
        return False


def tool_progress(key, **args):
    fn = getattr(_tool_emit, "fn", None)
    if fn is None:
        return
    try:
        fn({"event": "tool_progress", "key": key, "args": args})
    except Exception:
        pass


def args_complaint(name, arguments, exc, spec):
    props = (spec or {}).get("properties") or {}
    req = set((spec or {}).get("required") or [])
    takes = [k + (" (required)" if k in req else "") for k in props]
    sent = sorted((arguments or {}).keys())
    unknown = [k for k in sent if k not in props]
    return {"error": f"bad arguments for '{name}': {exc}",
            "it_takes": takes, "you_sent": sent,
            **({"unknown": unknown} if unknown else {}),
            "hint": f"re-call {name} using exactly the parameter names in "
                    f"'it_takes'"}


_JSON_KIND = {"array": list, "object": dict}


def _kind_of(v):
    if isinstance(v, bool):
        return "boolean"
    return {str: "string", int: "integer", float: "number", list: "array",
            dict: "object", type(None): "null"}.get(type(v), type(v).__name__)


def args_contract_complaint(name, arguments, spec):
    if not isinstance(arguments, dict) or not isinstance(spec, dict):
        return None
    props = spec.get("properties")
    if isinstance(props, dict) and props and spec.get("additionalProperties") is not True:
        unknown = [k for k in arguments if k not in props]
        if unknown:
            req = [k for k in (spec.get("required") or []) if k in props]
            out = args_complaint(
                name, arguments,
                ", ".join(f"'{k}'" for k in unknown)
                + (" is not a parameter" if len(unknown) == 1 else " are not parameters")
                + f" of {name}; its parameters are: " + ", ".join(props)
                + (f" (required: {', '.join(req)})" if req else "")
                + ". Nothing was done.", spec)
            return out
    props = props if isinstance(props, dict) else {}
    for key, val in arguments.items():
        decl = props.get(key) or {}
        want = decl.get("type")
        items = decl.get("items") or {}
        if val is None or not (want == "object"
                               or (want == "array" and items.get("type") in _JSON_KIND)):
            continue
        what = f"'{key}' must be an {want}"
        if want == "array":
            fields = list((items.get("properties") or {}).keys())
            what += f" of {items['type']}s" + (f" with {', '.join(fields)}" if fields else "")
        bad = None
        if not isinstance(val, _JSON_KIND[want]):
            bad = f"it came as a {_kind_of(val)}"
            if isinstance(val, str) and val.strip()[:1] in ("[", "{"):
                bad += " holding JSON text: send the value itself, not encoded as a string"
        elif want == "array":
            for i, it in enumerate(val):
                if not isinstance(it, _JSON_KIND[items["type"]]):
                    bad = f"item {i} came as a {_kind_of(it)}"
                    break
        if bad:
            return {"error": f"bad arguments for '{name}': {what}; {bad}. "
                             "Nothing was done.",
                    "it_takes": {k: (v or {}).get("type") for k, v in props.items()},
                    "hint": f"re-call {name} with '{key}' as a JSON {want}"}
    return None


def _unseen_pictures(tool_trace):
    pending = []
    for step in tool_trace or []:
        res = step.get("result")
        if step.get("tool") == "see_image":
            _p = os.path.realpath(os.path.expanduser(str((step.get("args") or {}).get("path") or "")))
            pending = [x for x in pending if os.path.realpath(x) != _p]
            continue
        if isinstance(res, str) and '"must_look"' in res:
            try:
                d = json.loads(res)
            except Exception:
                continue
            pending += [str(x) for x in (d.get("must_look") or []) if x]
    return pending


def unwrap_call_envelope(name, arguments):
    if (isinstance(arguments, dict) and set(arguments) == {"name", "arguments"}
            and arguments.get("name") == name):
        inner = arguments.get("arguments")
        if isinstance(inner, str):
            try:
                inner = json.loads(inner)
            except Exception:
                return arguments
        if isinstance(inner, dict):
            return inner
    return arguments


def args_preview(args, limit=120):
    if not isinstance(args, dict):
        s = str(args)
        return s if len(s) <= limit else s[:limit - 1] + "…"

    def _short(v):
        if isinstance(v, str):
            if len(v) <= 40:
                return repr(v)
            if not v.split()[1:] and ("/" in v or "\\" in v):
                return repr("…" + v[-37:])
            return repr(v[:37] + "…")
        if isinstance(v, (dict, list, tuple)):
            try:
                t = json.dumps(v, ensure_ascii=False, default=str)
            except Exception:
                t = str(v)
        else:
            t = repr(v)
        return t if len(t) <= 40 else t[:37] + "…"

    _long = (str, dict, list, tuple)
    items = ([(k, v) for k, v in args.items() if not isinstance(v, _long)]
             + [(k, v) for k, v in args.items() if isinstance(v, _long)])
    out = ", ".join(f"{k}={_short(v)}" for k, v in items)
    return out if len(out) <= limit else out[:limit - 1] + "…"


def result_preview(name, result, limit=200):
    txt = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    if not txt.startswith("{") or '"file"' not in txt:
        return txt[:limit]
    try:
        o = json.loads(txt)
    except Exception:
        return txt[:limit]
    fns = o.get("functions") if isinstance(o, dict) else None
    if isinstance(fns, list) and "hits_total" in o:
        total, nfiles = int(o.get("hits_total") or 0), int(o.get("files_total") or 0)
        page = o.get("page") or {}
        head = f"{total} hits in {nfiles} file{'s' if nfiles != 1 else ''}"
        if page.get("cursor"):
            head += f", page of {page.get('lines')} lines ({len(fns)} functions), more follow: "
        else:
            head += f", {len(fns)} function{'s' if len(fns) != 1 else ''}: "
        parts = []
        if page.get("cursor") and o.get("by_dir"):
            parts = [f"{d.get('dir')}/ ({d.get('hits')})" for d in o["by_dir"] if d.get("dir")][:6]
        for x in fns:
            f_ = str(x.get("file", "")).replace("\\", "/").rsplit("/", 1)[-1]
            if x.get("scope"):
                parts.append(f"{f_} {x['scope']} {x.get('scope_lines') or ''} ({x['hits']})".replace("  ", " "))
            else:
                parts.append(f"{f_}:{x.get('lines')} ({x['hits']}) no function told")
        out = head + ", ".join(parts)
        return out if len(out) <= limit else out[:limit - 1] + "…"
    items = None
    if isinstance(o, dict):
        for key in ("results", "matches"):
            cand = o.get(key)
            if (isinstance(cand, list) and cand
                    and all(isinstance(x, dict) and "file" in x and "line" in x
                            for x in cand[:5])):
                items = cand
                break
    if items is None:
        return txt[:limit]
    files = len({str(x.get("file")) for x in items})
    head = f"{len(items)} hits"
    m = re.match(r"items 1-(\d+) of (\d+)", str(o.get("cut") or ""))
    if m:
        head = f"{m.group(1)} of {m.group(2)} hits"
    head += f" in {files} file{'s' if files != 1 else ''}: "
    seen, parts = set(), []
    for x in items:
        f = str(x.get("file", "")).replace("\\", "/").rsplit("/", 1)[-1]
        sc = x.get("scope") or x.get("symbol")
        span = x.get("scope_lines") or (f"{x.get('line')}-{x.get('ends')}" if x.get("ends") else None)
        k = (f, sc, span)
        if k in seen:
            continue
        seen.add(k)
        if sc and span:
            parts.append(f"{f} {sc} {span}")
        elif sc:
            parts.append(f"{f} {sc}")
        else:
            parts.append(f"{f}:{x.get('line')}")
    out = head + ", ".join(parts)
    return out if len(out) <= limit else out[:limit - 1] + "…"


def _execute_tool(name, arguments, consented=False):
    if _on_tool_used is not None and name != "load_tools":
        try:
            _on_tool_used(name)
        except Exception:
            pass
    tools = _get_tools() or {}
    if name not in tools:
        return json.dumps({"error": f"tool '{name}' does not exist. Do not guess "
                                    "names: use one from the catalogue."})
    arguments = unwrap_call_envelope(name, arguments)
    if _consent is not None and not consented:
        try:
            refusal = _consent(name, arguments)
        except Exception as e:
            refusal = {"error": f"the person's consent could not be asked: {e}"}
        if refusal:
            return json.dumps(refusal, ensure_ascii=False)
    try:
        _shape = args_contract_complaint(name, arguments, (_get_tool_params() or {}).get(name))
    except Exception:
        _shape = None
    if _shape:
        return json.dumps(_shape, ensure_ascii=False)
    try:
        result = tools[name]["fn"](arguments)
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False)
    except KeyError as e:
        _k = e.args[0] if e.args else None
        _spec = {}
        try:
            _spec = (_get_tool_params() or {}).get(name) or {}
        except Exception:
            pass
        if isinstance(_k, str) and _k in (_spec.get("properties") or {}):
            return json.dumps(args_complaint(name, arguments, e, _spec),
                              ensure_ascii=False)
        return json.dumps({"error": f"failed executing '{name}': {e}"})
    except Exception as e:
        return json.dumps({"error": f"failed executing '{name}': {e}"})


NO_REPLY_MARKS = ("[The model ran out of tokens",
                  "[The model returned an empty",
                  "[The remote agent exhausted its iterations",
                  "[The model produced only internal reasoning")


def produced_nothing(reply):
    return bool(reply) and str(reply).lstrip().startswith(NO_REPLY_MARKS)


def flatten_parts(msg):
    parts = msg.get("content")
    if not isinstance(parts, list):
        return msg
    text, thinking = [], []
    for p in parts:
        if isinstance(p, str):
            text.append(p)
        elif not isinstance(p, dict):
            continue
        elif p.get("type") in ("thinking", "reasoning"):
            v = p.get("thinking") or p.get("reasoning") or p.get("text") or ""
            if isinstance(v, list):
                v = "".join(str(q.get("text", "")) for q in v if isinstance(q, dict))
            thinking.append(str(v))
        else:
            text.append(str(p.get("text") or ""))
    out = dict(msg, content="".join(text), content_parts=parts)
    if thinking and not (out.get("reasoning_content") or out.get("reasoning")):
        out["reasoning_content"] = "".join(thinking)
    return out


def _assistant_as_generated(msg, tool_calls=None, keep_content=True):
    parts = msg.get("content_parts")
    out = {"role": "assistant",
           "content": (parts or msg.get("content") or "") if keep_content else ""}
    for k in ("reasoning_content", "reasoning"):
        if msg.get(k) and not parts:
            out[k] = msg[k]
            break
    if tool_calls:
        out["tool_calls"] = tool_calls
    return out


def _clean_reply(content, reasoning="", finish_reason=None):
    if not content:
        if reasoning:
            if finish_reason == "length":
                return (f"{NO_REPLY_MARKS[0]} while reasoning ({len(reasoning)} "
                        "chars) without producing a reply. Increase max_tokens or "
                        "rephrase.]")
            return (f"{NO_REPLY_MARKS[3]} ({len(reasoning)} chars) and "
                    "stopped without a visible reply.]")
        return f"{NO_REPLY_MARKS[1]} response.]"
    cleaned = re.sub(r"<think>.*?</think>", "", content,
                     flags=re.DOTALL | re.IGNORECASE)
    while "<think>" in cleaned.lower() or "</think>" in cleaned.lower():
        cleaned = re.sub(r"<think>.*?</think>", "", cleaned,
                         flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r"<think>.*$", "", cleaned,
                         flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r"^.*?</think>", "", cleaned,
                         flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"\n\n+", "\n\n", cleaned).strip()
    if cleaned:
        return cleaned
    return "[The model produced only internal reasoning, with no visible reply.]"


def _truncation_warning(text, finish_reason, usage):
    if finish_reason == "length":
        client_max = _get_max_tokens() if _get_max_tokens else 0
        ct = (usage or {}).get("completion_tokens", 0)
        if ct and client_max and ct < client_max * 0.5:
            return _t("llm.trunc_server", text=text, ct=ct, cm=client_max)
        return _t("llm.trunc_client", text=text, ct=ct, cm=client_max)
    if finish_reason == "content_filter":
        return _t("llm.filtered", text=text)
    return text


_TC_NAME = r"[\w./:-]+"
_TC_MARK = re.compile(r"<tool_call>|<function=|\[TOOL_CALLS\]", re.I)


def _tc_visible(content):
    text = re.sub(r"<think>.*?</think>", "", content or "", flags=re.S | re.I)
    return re.sub(r"<\|[^|]+\|>", "", text)


def _tc_arg(value):
    s = value.strip()
    if re.fullmatch(r"-?\d+(\.\d+)?|true|false|null|\{.*\}|\[.*\]", s, re.S):
        try:
            return json.loads(s)
        except Exception:
            pass
    return s


def _tc_from_json(obj):
    if not isinstance(obj, dict):
        return None
    if obj.get("type") == "function" and isinstance(obj.get("function"), dict):
        obj = obj["function"]
    name = obj.get("name")
    args = obj.get("arguments", obj.get("parameters"))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            return None
    if args is None:
        args = {}
    if not isinstance(name, str) or not name or not isinstance(args, dict):
        return None
    return name, args


def _tc_json_at(text):
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0),
                default=-1)
    if start < 0:
        return None
    try:
        parsed, _ = json.JSONDecoder().raw_decode(text[start:])
    except Exception:
        return None
    return parsed


def _recover_tool_call_from_content(content, iteration, known=None):
    text = _tc_visible(content)
    calls = []

    def add(pair):
        if pair:
            name, args = pair
            calls.append({"id": f"call_{iteration}_{len(calls)}", "type": "function",
                          "function": {"name": name,
                                       "arguments": json.dumps(args, ensure_ascii=False)}})

    def pairs(body, key_re):
        return {k: _tc_arg(v) for k, v in re.findall(key_re, body, flags=re.S)}

    for body in re.findall(r"<tool_call>(.*?)</tool_call>", text, flags=re.S | re.I):
        body = body.strip()
        if body[:1] in "{[":
            parsed = _tc_json_at(body)
            for obj in (parsed if isinstance(parsed, list) else [parsed]):
                add(_tc_from_json(obj))
            continue
        m = re.match(rf"({_TC_NAME})\s*(.*)$", body, flags=re.S)
        if m and ("<arg_key>" in m.group(2) or not m.group(2).strip()):
            add((m.group(1), pairs(m.group(2),
                                   r"<arg_key>(.*?)</arg_key>\s*<arg_value>(.*?)</arg_value>")))
    for name, body in re.findall(rf"<function=({_TC_NAME})>(.*?)</function>", text,
                                 flags=re.S | re.I):
        body = body.strip()
        if body[:1] == "{":
            parsed = _tc_from_json(_tc_json_at(body))
            add((name, parsed[1]) if parsed else (name, {}))
        else:
            add((name, pairs(body, rf"<parameter=({_TC_NAME})>(.*?)</parameter>")))
    m = re.search(r"\[TOOL_CALLS\]\s*([\[{].*)$", text, flags=re.S)
    if m and not calls:
        parsed = _tc_json_at(m.group(1))
        for obj in (parsed if isinstance(parsed, list) else [parsed]):
            add(_tc_from_json(obj))
    if not calls and not _TC_MARK.search(text):
        parsed = _tc_json_at(text)
        pair = _tc_from_json(parsed)
        if pair and (parsed.get("type") == "function"
                     or (known is not None and pair[0] in known)):
            add(pair)
    return calls or None


def _tool_call_in_text(content):
    text = _tc_visible(content).strip()
    if _TC_MARK.match(text):
        return True
    last = None
    for m in _TC_MARK.finditer(text):
        last = m
    if last is None:
        return False
    tail = text[last.end():]
    mark = last.group(0).lower()
    if mark == "<tool_call>":
        return "</tool_call>" not in tail.lower()
    if mark == "<function=":
        return "</function>" not in tail.lower()
    return _tc_json_at(tail) is None


def _tool_call_name_in_text(content):
    text = _tc_visible(content)
    m = (re.search(rf"<tool_call>\s*({_TC_NAME})", text, flags=re.I)
         or re.search(rf"<function=({_TC_NAME})>", text, flags=re.I)
         or re.search(r'"name"\s*:\s*"([^"]+)"', text))
    return m.group(1) if m else ""


def _tool_names(tools):
    return {t_["function"]["name"] for t_ in (tools or [])
            if isinstance(t_, dict) and isinstance(t_.get("function"), dict)
            and t_["function"].get("name")}


_QA_BUDGET_CHARS = 40000


def _worth_reviewing(tool_trace, reply=""):
    tool_trace = [s for s in tool_trace if s.get("tool") != "intent"]
    if not tool_trace:
        return False
    if len(tool_trace) >= 2:
        return True
    prev = str(tool_trace[0].get("result",
                                 tool_trace[0].get("result_preview",
                                                   ""))).strip().lower()
    if ('"error"' in prev or prev.startswith("error")
            or "traceback" in prev or "exception" in prev):
        return True
    if len(prev) < 40:
        return True
    for empty in ('"results": []', '"results":[]', '"items": []', '"items":[]',
                  '"data": []', '"data":[]', "no results", "not found"):
        if empty in prev:
            return True
    if str(reply or "").rstrip().endswith("?"):
        return True
    _name = str(tool_trace[0].get("tool", ""))
    _READ_PREFIXES = ("search_", "list_", "get_", "read_", "recall",
                      "use_skill", "load_tools", "session_", "code_read",
                      "code_search", "code_index", "code_outline",
                      "code_references", "code_definition", "code_deps",
                      "code_diagnostics", "code_lsp", "code_toolchain",
                      "git_log", "git_show", "git_status", "git_diff",
                      "fed_code_pending", "fed_public_folders", "device_info")
    if not any(_name.startswith(p) for p in _READ_PREFIXES):
        return True
    return False


_QA_LAST = threading.local()


def _run_qa(request, tool_trace, reply, chat, goal="", given="", prior=None,
            asides=None):
    _QA_LAST.said = ""
    try:
        def _failed(res):
            if isinstance(res, dict):
                return bool(res.get("error")), str(res.get("error") or "")
            if isinstance(res, str) and res.lstrip().startswith("{"):
                try:
                    d = json.loads(res)
                except Exception:
                    return False, ""
                if isinstance(d, dict) and d.get("error"):
                    return True, str(d["error"])
            return False, ""

        def _shape(args):
            if not isinstance(args, dict):
                args = {"": args}
            out = []
            for k, v in args.items():
                v = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
                out.append(f"{k}={v}" if "\n" not in v
                           else f"{k}=<{len(v)} chars>")
            return ", ".join(out)

        def _came_back_empty(res):
            s = (res if isinstance(res, str) else str(res)).strip()
            if not s.startswith("{"):
                return False
            try:
                j = json.loads(s)
            except Exception:
                return False
            if not isinstance(j, dict):
                return False
            keys = [k for k in ("results", "matches", "items", "entries",
                                "commits", "notes", "hits") if k in j]
            return bool(keys) and not any(j[k] for k in keys)

        _steps, _full = [], []
        for tr in (tool_trace or []):
            _res = tr.get("result", tr.get("result_preview", ""))
            _bad, _why = _failed(_res)
            _n = len(_res if isinstance(_res, str) else str(_res))
            if tr.get("tool") == "verify" and "ok" in tr:
                _tail = ("exit 0 — the definition of done PASSED"
                         if tr["ok"] is True else
                         "exit ≠0 — the definition of done FAILED"
                         if tr["ok"] is False else
                         "did NOT run (refused by the firewall) — the work "
                         "was NOT checked")
            elif _bad:
                _tail = f"FAILED: {_why}"
            elif _came_back_empty(_res):
                _tail = "ok, EMPTY (nothing matched)"
            else:
                _tail = f"ok, {_n} chars back"
            _steps.append(f"- {tr.get('tool')}({_shape(tr.get('args'))}) → "
                          + _tail)
            _a = tr.get("args")
            _a = _a if isinstance(_a, str) else json.dumps(_a, ensure_ascii=False)
            _r = _res if isinstance(_res, str) else json.dumps(_res, ensure_ascii=False)
            _full.append(f"### {tr.get('tool')} → {_tail}\nARGUMENTS: {_a}\nRETURNED:\n{_r}")
        evidence = "\n".join(_steps) or "(no tools used)"
        goal_block = (f"ACCEPTANCE CRITERION set by the USER. This is the bar. "
                      f"If the answer does not meet it, it FAILS however well "
                      f"written it is:\n{goal}\n\n" if (goal or "").strip() else "")
        given_block = (f"GIVEN — the user's own saved memory, handed to the "
                       f"agent before it answered. This is EVIDENCE:\n{given}"
                       f"\n\n" if (given or "").strip() else "")
        prior_block = ""
        if prior:
            _hist = "\n".join(f"  round {i + 1}: {str(v.get('why', ''))}"
                               for i, v in enumerate(prior)
                               if not v.get("ok") and v.get("why"))
            if _hist:
                prior_block = (
                    "YOUR OWN EARLIER OBJECTIONS on this same answer. The text "
                    "you are reading is the agent's ANSWER TO THEM, so anything "
                    "that exists to satisfy one of these is not unmotivated — "
                    "it is compliance, and it is not a reason to fail it "
                    "again. Only raise something NEW, or say the objection "
                    "still stands and why:\n" + _hist + "\n"
                    "AND ONE CHECK THAT ONLY EXISTS ON A REDO: the person "
                    "NEVER saw the earlier attempt nor any objection. An "
                    "answer that speaks TO THE REVIEW — conceding a critique, "
                    "apologizing for something the person never saw, "
                    "announcing it is getting back on topic — is addressed "
                    "to the wrong party: FAIL it and name exactly the "
                    "sentences to strip (the substance around them may "
                    "already be right and must be kept).\n\n")
        full_evidence = "\n\n".join(_full) or "(no tools used)"
        _said = []
        for _a in (asides or []):
            _who = ("a background job of the agent reported"
                    if _a.get("kind") == "background" else "the USER said")
            _said.append(f"- {_who}: {_a.get('text', '')}")
        asides_block = ("SAID DURING THE TURN — what reached the agent while "
                        "it worked, in order. This is EVIDENCE, and what the "
                        "user said here is part of the request:\n"
                        + "\n".join(_said) + "\n\n") if _said else ""
        def _compose(ev):
            return (f"{goal_block}{prior_block}"
                    f"REQUEST (may include what earlier sub-tasks handed over):\n{request}"
                    f"\n\n{given_block}{asides_block}"
                    f"STEPS THE AGENT TOOK:\n{ev}"
                    f"\n\nPROPOSED ANSWER:\n{reply}")
        user = _compose(full_evidence)
        system, mode = _QA_SYSTEM_FULL, "grounded"
        if len(user) > _QA_BUDGET_CHARS:
            user = _compose(evidence)
            system, mode = _QA_SYSTEM, "contract"
        _qa_msgs = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]
        raw = chat(_qa_msgs, None)
        def _qa_verdict(raw_):
            m_ = (raw_ or {}).get("message") if isinstance(raw_, dict) \
                and "message" in raw_ else (raw_ or {})
            txt_ = (m_ or {}).get("content", "") or ""
            rsn_ = (m_ or {}).get("reasoning_content", "") \
                or (m_ or {}).get("reasoning", "") or ""
            for src in (txt_, rsn_):
                for cand in re.findall(r"\{[^{}]*\}", src or ""):
                    try:
                        d_ = json.loads(cand)
                    except Exception:
                        continue
                    if isinstance(d_, dict) and "ok" in d_:
                        return d_, txt_, rsn_
            return None, txt_, rsn_

        data, txt, reasoning = _qa_verdict(raw)
        _QA_LAST.said = (txt or reasoning or "").strip()
        if data is None and not txt.strip() and reasoning.strip():
            data, _t2, _r2 = _qa_verdict(chat(
                _qa_msgs + [{"role": "assistant", "content": reasoning},
                            {"role": "user", "content":
                             "That was your reasoning, not a verdict. Write ONLY "
                             "the JSON object now: {\"ok\": true|false, \"why\": "
                             "\"...\", \"next\": \"...\"}."}], None))
            _QA_LAST.said = ((_QA_LAST.said + "\n\n") if _QA_LAST.said else "") \
                + (_t2 or _r2 or "").strip()
        if data is None:
            return (None, "the reviewer gave no verdict (no JSON object in its "
                          "answer)" + (" — it only reasoned" if reasoning.strip()
                                       and not txt.strip() else ""), "", mode)
        if data.get("ok") is False:
            return (False, str(data.get("why", "")),
                    str(data.get("next", "")), mode)
        return (True, str(data.get("why", "") or ""),
                str(data.get("next", "") or ""), mode)
    except Exception as e:
        return (None, f"the reviewer failed: {e}", "",
                locals().get("mode", "contract"))


def judge_output(chat, expect, out, command=""):
    expect = (expect or "").strip()
    if not (expect and callable(chat)):
        return None
    msgs = [{"role": "system", "content": _JUDGE_SYSTEM},
            {"role": "user", "content":
             f"COMMAND:\n{command}\n\nEXPECTATION:\n{expect}"
             f"\n\nOUTPUT:\n{out[-4000:]}"}]

    def _verdict(raw):
        m_ = (raw or {}).get("message") if isinstance(raw, dict) \
            and "message" in raw else (raw or {})
        txt = (m_ or {}).get("content", "") or ""
        reasoning = (m_ or {}).get("reasoning_content", "") \
            or (m_ or {}).get("reasoning", "") or ""
        for src in (txt, reasoning):
            for cand in re.findall(r"\{[^{}]*\}", src or ""):
                try:
                    data = json.loads(cand)
                except Exception:
                    continue
                if isinstance(data, dict) and isinstance(data.get("ok"), bool):
                    return data["ok"], str(data.get("why", ""))[:400], txt, reasoning
        return None, "", txt, reasoning

    try:
        ok, why, txt, reasoning = _verdict(chat(msgs, None))
        if ok is not None:
            return (ok, why)
        if not txt.strip() and reasoning.strip():
            ok2, why2, _t2, _r2 = _verdict(chat(
                msgs + [{"role": "assistant", "content": reasoning[:6000]},
                        {"role": "user", "content":
                         "That was your reasoning, not a verdict. Write ONLY "
                         "the JSON object now: {\"ok\": true|false, "
                         "\"why\": \"...\"}."}], None))
            if ok2 is not None:
                return (ok2, why2)
    except Exception:
        pass
    return None


def _run_verifier(command, run_tool, goal="", judge=None):
    raw = run_tool("run_command", {"command": command, "timeout": 300})
    try:
        data = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception:
        return (str(raw)[:1500], False, None)
    if not isinstance(data, dict):
        return (str(data)[:1500], False, None)
    if "exit_code" not in data and "returncode" not in data and data.get("error"):
        return (str(data.get("error"))[:600], None, None)
    code = data.get("exit_code", data.get("returncode", 1))
    out = (str(data.get("stdout", "")) + "\n" + str(data.get("stderr", ""))).strip()
    if not out:
        out = str(data)[:1500]
    ok = code == 0
    out = out[-2500:]
    goal = (goal or "").strip()
    judged = None
    if goal:
        judged = False
        if not ok:
            out += ("\n\n[CHECK GOAL — not judged, the command itself "
                    "failed] %s" % goal)
        else:
            v = judge(goal, out) if callable(judge) else None
            if v is None:
                out += ("\n\n[CHECK GOAL — NOBODY JUDGED IT, so it is not "
                        "verified. Judge it against the output above "
                        "yourself, and say so] %s" % goal)
            else:
                judged = True
                gok, why = v
                ok = ok and gok
                out += "\n\n[CHECK GOAL] %s %s" % (
                    "✓" if gok else "✗", (why or goal))
    return (out, ok, judged)


_INTENT_MAX_ITEMS = 24
_INTENT_GOAL_CHARS = 200


def _render_intent(items):
    if not items:
        return "(empty — set it with intent(set=[...]))"
    _MARK = {"done": "✓", "failed": "✗", "doing": "●", "pending": "○"}
    return "\n".join(f"{_MARK.get(it['status'], '○')} [{it['id']}] {it['goal']}"
                     for it in items)


def _apply_intent(items, args):
    changed, notes = False, []
    args = args if isinstance(args, dict) else {}

    def _clean(goals):
        out = []
        for g in (goals if isinstance(goals, (list, tuple)) else [goals]):
            g = str(g or "").strip()[:_INTENT_GOAL_CHARS]
            if g:
                out.append(g)
        return out

    if "set" in args:
        fresh = _clean(args.get("set"))[:_INTENT_MAX_ITEMS]
        items[:] = [{"id": i + 1, "goal": g, "status": "pending"}
                    for i, g in enumerate(fresh)]
        changed = True
    if args.get("add"):
        for g in _clean(args.get("add")):
            if len(items) >= _INTENT_MAX_ITEMS:
                notes.append(f"list is full ({_INTENT_MAX_ITEMS}) — rewrite it "
                             "with set instead of growing it")
                break
            items.append({"id": (max((it["id"] for it in items), default=0) + 1),
                          "goal": g, "status": "pending"})
            changed = True
    by_id = {it["id"]: it for it in items}

    def _mark(ids, status):
        nonlocal changed
        for raw in (ids if isinstance(ids, (list, tuple)) else [ids]):
            try:
                it = by_id.get(int(raw))
            except (TypeError, ValueError):
                it = None
            if it is None:
                notes.append(f"no item with id {raw!r}")
            elif it["status"] != status:
                it["status"] = status
                changed = True
    if args.get("done") is not None:
        _mark(args.get("done"), "done")
    if args.get("failed") is not None:
        _mark(args.get("failed"), "failed")
    if args.get("doing") is not None:
        try:
            target = by_id.get(int(args.get("doing")))
        except (TypeError, ValueError):
            target = None
        if target is None:
            notes.append(f"no item with id {args.get('doing')!r}")
        else:
            for it in items:
                if it is not target and it["status"] == "doing":
                    it["status"] = "pending"
            if target["status"] != "doing":
                target["status"] = "doing"
                changed = True
    result = _render_intent(items)
    if notes:
        result += "\n[" + "; ".join(notes) + "]"
    return changed, result


def _result_was_complete(raw):
    s = (raw or "")
    if "[CUT: lines" in s or "[DIGEST of a" in s:
        return False
    st = s.strip()
    if st.startswith("{"):
        try:
            j = json.loads(st)
            if isinstance(j, dict) and j.get("truncated"):
                return False
        except Exception:
            pass
    return True


def _covered_by_earlier(name, args, earlier):
    if not isinstance(args, dict):
        return False

    def _split(a):
        paths, rest = {}, {}
        for k, v in a.items():
            if isinstance(v, str) and ("/" in v or v.startswith(".")):
                paths[k] = v.rstrip("/")
            else:
                rest[k] = v
        return paths, rest

    p_now, r_now = _split(args)
    if not p_now:
        return False
    for n2, a2, complete in earlier:
        if not complete or n2 != name or not isinstance(a2, dict):
            continue
        p_old, r_old = _split(a2)
        if r_old != r_now or set(p_old) != set(p_now):
            continue
        if all(p_now[k] == p_old[k]
               or p_now[k].startswith(p_old[k] + "/") for k in p_now):
            return True
    return False


def _is_sterile_result(result, was_repeat, covered=False):
    if was_repeat:
        return True
    s = (result or "").strip()
    if not s:
        return True
    if s.startswith("❌"):
        return True
    if s.startswith("{"):
        try:
            j = json.loads(s)
            if isinstance(j, dict):
                if j.get("error"):
                    return True
                _keys = [k for k in ("results", "matches", "items", "entries",
                                     "commits", "notes", "hits") if k in j]
                if _keys and not any(j[k] for k in _keys):
                    return bool(covered)
        except Exception:
            pass
    return False


def run_agentic_turn(user_message=None, history=None, system=None,
                     max_iterations=None, on_event=None, cancel=None,
                     turn_id=None, messages=None, llm_chat=None,
                     delegate=None, delegate_names=(), verify=None, qa=None,
                     qa_chat=None, allowed_tools=None, qa_max_rounds=None,
                     expanded_extra=None,
                     goal=None, verify_goal=None, escalate=None,
                     model_label="", is_blocked=None,
                     plan_gate=None,
                     given="",
                     council=False,
                     qa_self=False):
    _emit_out = on_event or (lambda ev: None)
    _partial = {}

    def emit(ev):
        if ev.get("event") == "reply" and _partial:
            for _r in _partial.values():
                if len(_r["seen"]) < _r["total"] and not _r.get("consulted"):
                    _emit_out({"event": "tool_progress", "key": "read.partial",
                               "args": {"path": str(_r["path"]),
                                        "read": str(len(_r["seen"])),
                                        "lines": str(_r["total"])}})
            _partial.clear()
        _emit_out(ev)
    is_cancelled = cancel or (lambda: False)
    chat = llm_chat or _llm_chat
    _window = 0
    delegate_all = "*" in (delegate_names or ())

    if chat is None:
        err = {"error": "Agentic loop not configured in the MCP."}
        emit({"event": "error", "error": err["error"], "iterations": 0})
        return err
    if messages is None and not user_message:
        err = {"error": "Neither 'messages' nor 'user_message' — nothing to do."}
        emit({"event": "error", "error": err["error"], "iterations": 0})
        return err
    max_it = max_iterations or _max_iter
    _final_answer_done = False
    _plan_gated = False
    _thought_only_retried = False
    _plan_original = None
    _plan_nudge = ""
    _plan_trace_len = 0
    _plan_backup = None
    _plan_nudge_at = -1
    iter_baseline = _max_iter
    verify_cmd = (verify or "").strip() if isinstance(verify, str) else ""
    verify_goal_txt = ((verify_goal or "").strip()
                       if isinstance(verify_goal, str) else "")
    verify_runs = 0
    verify_blocked = False
    verified = None
    qa_mode = (qa or "off").strip().lower() if isinstance(qa, str) else "off"
    qa_rounds = 0
    qa_candidates = []
    QA_MAX_ROUNDS = max(1, int(qa_max_rounds)) if qa_max_rounds else 2
    qa_verdicts = []
    record_echoes = 0
    preflight_rejects = 0

    allowed = None if allowed_tools is None else set(allowed_tools) | {"intent"}

    def _is_allowed(name):
        return allowed is None or name in allowed

    def _may_here(name):
        return _is_allowed(name) and not (callable(is_blocked) and is_blocked(name))

    def _is_delegated(name):
        if not delegate or name in NEVER_DELEGATE \
                or name.startswith(NEVER_DELEGATE_PREFIXES):
            return False
        return delegate_all or name in (delegate_names or ())

    def run_tool(name, args, call_id=None):
        dele = _is_delegated(name)
        if dele:
            if callable(is_blocked) and is_blocked(name):
                return json.dumps(
                    {"error": f"🛡 FIREWALL: '{name}' is blocked"},
                    ensure_ascii=False)
            if _consent is not None:
                try:
                    _ref = _consent(name, args)
                except Exception as e:
                    _ref = {"error": f"the person's consent could not be asked: {e}"}
                if _ref:
                    return json.dumps(_ref, ensure_ascii=False)
            cid = call_id or f"verify_{verify_runs}"
            emit({"event": "client_tool_call", "call_id": cid,
                  "tool": name, "args": args})
            return delegate(cid, name, args)
        return _execute_tool(name, args)

    if messages is not None:
        messages = list(messages)
    else:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        if history:
            for h in history:
                role = h.get("role")
                content = h.get("content", "")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_message})

    expanded = None
    if _hot_tools is not None and _tool_catalogue is not None:
        expanded = set(_hot_tools() or [])
        expanded.add("load_tools")
        expanded.add("intent")
        if allowed_tools is not None:
            expanded &= set(allowed_tools) | {"load_tools", "intent"}
        if expanded_extra:
            expanded |= (set(expanded_extra)
                         & (set(allowed_tools) if allowed_tools is not None
                            else set(expanded_extra)))
    all_tools = _tools_as_openai(allowed, expanded)

    def _catalogue_msg():
        shelf = _tool_catalogue(expanded or ()) if _tool_catalogue else []
        if not shelf:
            return None
        return {"role": "system", "content":
                "AVAILABLE TOOLS (the full catalogue). These exist and work, "
                "but you do not have their parameters yet. As soon as you know "
                "which one you need, ask for its schema with "
                "load_tools(names=[...]) and then call it through "
                "use_tool(name=..., arguments={...}). "
                "Do NOT say you cannot do something if its tool is on "
                "this list.\n  - "
                + "\n  - ".join(shelf)}

    goal = (goal or "").strip()
    if goal:
        _at = 0
        while _at < len(messages) and messages[_at].get("role") == "system":
            _at += 1
        messages.insert(_at, {"role": "system", "content":
                              "GOAL / DEFINITION OF DONE set by the user. You "
                              "are not done until you meet it, and you will be "
                              "reviewed against this:\n" + goal})

    _cat_at = None
    if expanded is not None:
        _cat = _catalogue_msg()
        if _cat:
            _at = 0
            while _at < len(messages) and messages[_at].get("role") == "system":
                _at += 1
            messages.insert(_at, _cat)
            _cat_at = _at

    tool_trace = []
    _heavy_cut, _heavy_streaks = set(), {}
    _turn_base = len(messages)

    try:
        _total_avail = (len(allowed) if allowed is not None
                        else len(_get_tools() or {}))
    except Exception:
        _total_avail = len(all_tools)
    emit({"event": "turn_start", "turn_id": turn_id,
          "tools": len(all_tools), "max_iterations": max_it,
          "shelf": max(0, _total_avail - len(all_tools)),
          "carried": [(t_.get("function") or {}).get("name", "")
                      for t_ in all_tools]})
    _watch_turn(turn_id)
    _seen_calls, _repeats = {}, {}
    _calls_log = []
    intent_items = []
    _pending_shots = []
    _intent_chased = False
    _intent_original = None
    _intent_chase_at = -1
    _intent_snapshot = None
    _resume_original = None
    _resume_chase_at = -1
    _resume_snapshot = None
    _injected_any = False
    _asides = []
    _resume_chased = False
    _look_asked = set()
    _look_asks = 0
    _orig_request = (user_message or "").strip()
    if not _orig_request:
        for _m in reversed(messages):
            _said = user_said(_m)
            if (_m.get("role") == "user" and _said
                    and not _said.startswith(_LOOP_MSG_PREFIXES + HARNESS_TAGS)):
                _orig_request = _said
                break
    _stall_t0 = time.time()
    _sterile_tools = 0
    _nudges_sent = 0
    _nudge_armed = True
    _escalated = False
    _loop_reflected = set()
    _asked_since_progress = 0
    _sterile_since_ask = 0
    _last_asked = False
    _no_handover = False
    _final_key = "inject.final_answer"

    def _progress():
        nonlocal _sterile_tools, _nudge_armed, _stall_t0
        nonlocal _asked_since_progress, _sterile_since_ask, _last_asked
        _stall_t0 = time.time()
        _sterile_tools = 0
        _nudge_armed = True
        _asked_since_progress = 0
        _sterile_since_ask = 0
        _last_asked = False

    iteration = 0
    while True:
        if _max_iter != iter_baseline:
            iter_baseline = _max_iter
            if _max_iter != max_it:
                emit({"event": "max_iterations", "max": _max_iter, "was": max_it})
                max_it = _max_iter
        if iteration >= max_it:
            if not _final_answer_done and not verify_cmd:
                _final_answer_done = True
                messages.append(harness_msg(_t(_final_key)))
                if is_cancelled():
                    break
                try:
                    _fraw = chat(single_system(messages), None)
                except Exception:
                    _fraw = None
                    break
                _fmsg = (_fraw.get("message") or {}) if isinstance(_fraw, dict) else {}
                _freply = _clean_reply(_fmsg.get("content", "") or "",
                                       _fmsg.get("reasoning_content", "")
                                       or _fmsg.get("reasoning", "") or "")
                if _freply and not produced_nothing(_freply) \
                        and not any(m in _freply for m in NO_REPLY_MARKS):
                    _rec = render_turn_record(tool_trace, qa_verdicts)
                    emit({"event": "reply", "reply": _freply,
                          "iterations": iteration, "turn_record": _rec,
                          "transcript": messages[_turn_base:],
                          "from_final_pass": True})
                    return {"reply": _freply, "tool_trace": tool_trace,
                            "iterations": iteration, "verified": verified,
                            "turn_record": _rec,
                            "transcript": messages[_turn_base:]}
            break
        iteration += 1

        for _msg, _kind in _drain_injections(turn_id):
            messages.append({"role": "user", "content": _t(
                "inject.mid_turn" if _kind != "background" else "inject.background",
                text=_msg)})
            _injected_any = True
            _asides.append({"kind": _kind, "text": _msg})
            if _kind == "background":
                _progress()
            emit({"event": "injected", "message": _msg, "n": iteration,
                  "kind": _kind})

        if is_cancelled():
            emit({"event": "cancelled", "iterations": iteration,
                  "turn_record": render_turn_record(tool_trace, qa_verdicts,
                                                    failed="cancelled")})
            return {"cancelled": True, "tool_trace": tool_trace,
                    "iterations": iteration}
        emit({"event": "iteration", "n": iteration, "max": max_it})

        if (_METACOG["enabled"] and _asked_since_progress
                and _sterile_since_ask >= max(1, _METACOG["stall_tools"])):
            _handed = False
            if not _escalated and not _no_handover and escalate is not None:
                try:
                    _new = escalate()
                except Exception as _e:
                    _new = None
                    emit({"event": "escalate_failed", "why": str(_e)[:160]})
                if _new:
                    chat, _label = _new
                    _escalated = _handed = True
                    _sterile_since_ask = 0
                    messages.append(harness_msg(_t("inject.escalated", model=_label)))
                    emit({"event": "model_escalated", "to": _label, "n": iteration,
                          "after": "asked", "sterile_tools": _sterile_tools})
                else:
                    _no_handover = True
            if not _handed:
                if not _last_asked:
                    _last_asked = True
                    _asked_since_progress += 1
                    _sterile_since_ask = 0
                    messages.append(harness_msg(_t("inject.metacog_last")))
                    emit({"event": "metacog_nudge", "n": iteration,
                          "sterile_tools": _sterile_tools, "stalled_s": 0,
                          "reason": "last"})
                elif _final_key != "inject.metacog_close":
                    _final_key = "inject.metacog_close"
                    max_it = iteration - 1
                    emit({"event": "metacog_close", "n": iteration,
                          "sterile_tools": _sterile_tools})
                    continue

        if (_METACOG["enabled"] and _nudge_armed
                and _nudges_sent < _METACOG_MAX_NUDGES
                and _sterile_tools >= 1
                and (_sterile_tools >= _METACOG["stall_tools"]
                     or time.time() - _stall_t0 >= _METACOG["stall_seconds"])):
            _stalled_s = round(time.time() - _stall_t0, 1)
            nudge = _t("inject.metacog_nudge")
            if intent_items:
                nudge += "\n\n" + _t("inject.metacog_list",
                                     list=_render_intent(intent_items))
            if council:
                nudge += "\n\n" + _t("inject.metacog_council")
            _bg_now = []
            if _background is not None:
                try:
                    _bg_now = list(_background.live_for_turn(turn_id) or [])
                except Exception:
                    _bg_now = []
            if _bg_now:
                nudge += "\n\n" + _t(
                    "inject.metacog_background", n=len(_bg_now),
                    names=", ".join(f"{j.get('id')} ({j.get('label')})"
                                    for j in _bg_now[:6]))
            messages.append(harness_msg(nudge))
            _nudges_sent += 1
            _nudge_armed = False
            _asked_since_progress += 1
            _sterile_since_ask = 0
            emit({"event": "metacog_nudge", "n": iteration,
                  "sterile_tools": _sterile_tools, "stalled_s": _stalled_s,
                  "council": bool(council)})

        tools = all_tools

        try:
            _pk = len(json.dumps(messages, ensure_ascii=False)) // 1024
        except Exception:
            _pk = 0
        emit({"event": "llm_call", "n_messages": len(messages),
              "n_tools": len(tools), "payload_kb": _pk,
              "model": model_label})
        _t_llm = time.time()
        raw, _llm_err = None, None
        for _try in range(3):
            if is_cancelled():
                break
            try:
                raw = chat(single_system(messages), tools)
                _llm_err = None
                break
            except Exception as e:
                _llm_err = e
                if getattr(e, "overflow", False):
                    emit({"event": "context_overflow", "prompt": e.prompt,
                          "reply": e.reply, "window": e.window})
                    break
                if getattr(e, "no_retry", False):
                    break
                if _try < 2:
                    emit({"event": "tool_progress", "key": "chat.llm_retry",
                          "args": {"n": _try + 1, "e": str(e)[:80]}})
                    time.sleep((5, 15)[_try])
        if is_cancelled():
            emit({"event": "cancelled", "iterations": iteration,
                  "turn_record": render_turn_record(tool_trace, qa_verdicts,
                                                    failed="cancelled")})
            return {"cancelled": True, "tool_trace": tool_trace,
                    "iterations": iteration}
        if _llm_err is not None:
            emit({"event": "error", "error": f"LLM failure: {_llm_err}",
                  "iterations": iteration,
                  "transcript": messages[_turn_base:],
                  "turn_record": render_turn_record(
                      tool_trace, qa_verdicts,
                      failed=f"LLM failure: {_llm_err}")})
            return {"error": f"LLM failure: {_llm_err}",
                    "iterations": iteration, "tool_trace": tool_trace}
        emit({"event": "llm_done",
              "duration_s": round(time.time() - _t_llm, 1)})

        if isinstance(raw, dict) and "message" in raw:
            msg = raw.get("message") or {}
            usage = raw.get("usage") or {}
            finish_reason = raw.get("finish_reason", "")
            _window = int(raw.get("window") or 0) or _window
            _remember_window(_window)
        else:
            msg, usage, finish_reason = (raw or {}), {}, ""

        pt = usage.get("prompt_tokens")
        if pt:
            emit({"event": "tokens", "prompt_tokens": pt,
                  "iteration": iteration,
                  "estimated": bool(usage.get("estimated"))})

        tool_calls = msg.get("tool_calls")
        content = msg.get("content", "") or ""
        reasoning = msg.get("reasoning_content", "") or msg.get("reasoning", "") or ""
        _thought = reasoning
        if not _thought and "<think>" in content:
            _mt = re.search(r"<think>(.*?)</think>", content, re.S)
            _thought = (_mt.group(1) if _mt else "").strip()
        if _thought:
            emit({"event": "thought", "iteration": iteration,
                  "chars": len(_thought), "text": _thought})

        _tc_rescued = False
        if not tool_calls and content:
            recovered = _recover_tool_call_from_content(content, iteration,
                                                        known=_tool_names(tools))
            if recovered:
                tool_calls = recovered
                _tc_rescued = True

        if not tool_calls and content and _tool_call_in_text(content):
            _ct = (usage or {}).get("completion_tokens", 0)
            _cut = finish_reason == "length"
            _name = _tool_call_name_in_text(content) or "?"
            emit({"event": "tool_call_unread", "iteration": iteration,
                  "cut": _cut, "tool": _name, "completion_tokens": _ct,
                  "chars": len(content)})
            _why = _rtr("chat.tool_call_cut" if _cut else "chat.tool_call_text",
                        tool=_name, ct=_ct,
                        cm=_get_max_tokens() if _get_max_tokens else 0)
            emit({"event": "error", "error": _why, "iterations": iteration,
                  "transcript": messages[_turn_base:],
                  "turn_record": render_turn_record(
                      tool_trace, qa_verdicts, failed=_why)})
            return {"error": _why, "iterations": iteration,
                    "tool_trace": tool_trace}

        if not tool_calls:
            if not content and reasoning and finish_reason != "length" \
                    and not _thought_only_retried and iteration < max_it \
                    and not is_cancelled():
                _thought_only_retried = True
                messages.append(_assistant_as_generated(msg))
                messages.append(harness_msg(_t("inject.answer_after_thinking")))
                emit({"event": "reasoning_only", "iteration": iteration,
                      "chars": len(reasoning)})
                continue
            reply = _clean_reply(content, reasoning, finish_reason)
            reply = _truncation_warning(reply, finish_reason, usage)
            _truncated = finish_reason == "length"
            if _truncated and (usage or {}).get("completion_tokens", 0) <= 48 \
                    and len(_clean_reply(content, reasoning).strip()) < 200:
                _pt = (usage or {}).get("prompt_tokens", 0)
                emit({"event": "context_full", "prompt_tokens": _pt,
                      "completion_tokens": (usage or {}).get(
                          "completion_tokens", 0), "iterations": iteration})
                _why = _rtr("chat.context_full", pt=_pt)
                emit({"event": "error", "error": _why,
                      "iterations": iteration,
                      "transcript": messages[_turn_base:],
                      "turn_record": render_turn_record(
                          tool_trace, qa_verdicts, failed=_why)})
                return {"error": _why, "iterations": iteration,
                        "tool_trace": tool_trace}
            if _plan_original is not None:
                _plan_nudge_at = -1
                if not reply.strip().startswith(GATE_KEEP_TOKENS) \
                        and not _did_gate_work(tool_trace, _plan_trace_len, _plan_nudge):
                    emit({"event": "plan_gate_ignored", "iteration": iteration})
                _settle = getattr(plan_gate, "settle", None)
                if callable(_settle):
                    try:
                        _sr = _settle(tool_trace, _orig_request)
                    except Exception:
                        _sr = None
                    if isinstance(_sr, dict) and _sr.get("ok"):
                        emit({"event": "journal_unexplained",
                              "iteration": iteration})
                reply = _plan_original
                _plan_original = None
            elif plan_gate is not None and not _plan_gated \
                    and iteration < max_it and not is_cancelled():
                try:
                    _pg = plan_gate(reply, tool_trace)
                except Exception:
                    _pg = None
                if _pg:
                    _plan_gated = True
                    _plan_original = _plan_backup = reply
                    _plan_nudge = _pg
                    _plan_trace_len = len(tool_trace)
                    _plan_nudge_at = len(messages)
                    messages.append(_assistant_as_generated(msg))
                    messages.append(harness_msg(_pg))
                    emit({"event": "plan_gate", "iteration": iteration})
                    continue
            if _plan_backup and reply.strip().startswith(GATE_KEEP_TOKENS):
                reply = _plan_backup
            if any(s in reply for s in RECORD_SENTINELS):
                record_echoes += 1
                emit({"event": "qa_result", "ok": False, "round": 0,
                      "why": "pasted the internal turn record as the answer",
                      "mode": "echo"})
                if record_echoes <= 2 and iteration < max_it:
                    messages.append(_assistant_as_generated(msg))
                    messages.append(harness_msg(
                        "[NOT AN ANSWER] That was the internal turn record, not a reply. Answer the user's message now, calling the tools you need; if they asked what you did, summarise it in your own words."))
                    continue
                reply = _rtr("chat.turn_echo_dead")
                _rec = render_turn_record(tool_trace, qa_verdicts,
                                          failed="record echoed 3x")
                emit({"event": "reply", "reply": reply,
                      "iterations": iteration, "turn_record": _rec,
                      "transcript": messages[_turn_base:]})
                return {"reply": reply, "tool_trace": tool_trace,
                        "iterations": iteration, "verified": False,
                        "turn_record": _rec,
                        "transcript": messages[_turn_base:]}
            if _resume_original is not None:
                if not (_real_tools(tool_trace) > _resume_snapshot):
                    reply = _resume_original
                    emit({"event": "resume_chase_ignored", "iterations": iteration})
                _resume_original = None
                _resume_chase_at = -1
            if _intent_original is not None:
                _moved = _real_tools(tool_trace) > _intent_snapshot
                if not _moved:
                    reply = _intent_original
                    emit({"event": "intent_chase_ignored", "iterations": iteration})
                _intent_original = None
                _intent_chase_at = -1
            _open_intent = [i for i in intent_items
                            if str(i.get("status", "")).lower()
                            in ("pending", "doing", "")]
            _worked = _real_tools(tool_trace) > 0
            _bg_live = []
            if _background is not None:
                try:
                    _bg_live = list(_background.live_for_turn(turn_id) or [])
                except Exception:
                    _bg_live = []
            if _open_intent and _worked and not _intent_chased \
                    and iteration < max_it and not _bg_live:
                _intent_chased = True
                _intent_original = reply
                _intent_snapshot = _real_tools(tool_trace)
                _intent_chase_at = len(messages)
                messages.append(_assistant_as_generated(msg))
                messages.append(harness_msg(_t(
                    "inject.intent_open",
                    list=_render_intent(intent_items))))
                emit({"event": "intent_open", "n": len(_open_intent),
                      "iterations": iteration})
                continue
            if (_injected_any and not _resume_chased and iteration < max_it
                    and _orig_request):
                _resume_chased = True
                _resume_original = reply
                _resume_snapshot = _real_tools(tool_trace)
                _resume_chase_at = len(messages)
                messages.append(_assistant_as_generated(msg))
                messages.append(harness_msg(_t(
                    "inject.resume_original", request=_orig_request[:1500])))
                emit({"event": "resume_check", "iterations": iteration})
                continue
            _unseen = [p_ for p_ in _unseen_pictures(tool_trace) if p_ not in _look_asked]
            if _unseen and _look_asks < 3 and iteration < max_it:
                _look_asks += 1
                _look_asked.update(_unseen)
                messages.append(_assistant_as_generated(msg))
                messages.append(harness_msg(_t("inject.look_first",
                                               paths=", ".join(_unseen[:4]))))
                emit({"event": "look_check", "n": len(_unseen), "iterations": iteration})
                continue
            if verify_cmd and not verify_blocked:
                verify_runs += 1
                emit({"event": "verify_run", "command": verify_cmd,
                      "attempt": verify_runs, "iterations": iteration})
                v_out, v_ok, v_judged = _run_verifier(
                    verify_cmd, run_tool, goal=verify_goal_txt,
                    judge=(lambda _e, _o: judge_output(
                        qa_chat or chat, _e, _o, command=verify_cmd)))
                if v_ok is None:
                    verify_blocked = True
                    emit({"event": "verify_result", "ok": None, "blocked": True,
                          "attempt": verify_runs, "output_preview": v_out[:200]})
                    tool_trace.append({
                        "tool": "verify", "args": {"command": verify_cmd},
                        "ok": None,
                        "result": _rtr("verify.blocked_reply",
                                       cmd=verify_cmd, why=v_out[:300])})
                    reply = (_rtr("verify.blocked_reply", cmd=verify_cmd,
                                  why=v_out[:300]) + "\n\n" + reply)
                    v_ok = True
                    v_out = ""
                else:
                    emit({"event": "verify_result", "ok": v_ok,
                          "attempt": verify_runs,
                          "output_preview": v_out[:200]})
                if v_out.strip():
                    tool_trace.append({
                        "tool": "verify", "args": {"command": verify_cmd},
                        "ok": bool(v_ok),
                        "result": _rtr("verify.output_for_you", ok=("0" if v_ok
                                                                    else "≠0"),
                                       out=v_out)})
                if not v_ok and iteration < max_it:
                    messages.append(_assistant_as_generated(msg))
                    messages.append(harness_msg(
                        f"[DEFINITION OF DONE NOT MET] `{verify_cmd}` still fails (attempt {verify_runs}). Its output:\n{v_out}\n\nRead the output, fix the cause, run it again. The same fix twice is not a fix: change approach."))
                    continue
                if verify_blocked:
                    pass
                elif not v_ok:
                    reply = (f"[⚠ `{verify_cmd}`"
                             + (f" (goal `{verify_goal_txt}`)"
                                if verify_goal_txt and v_judged is not False
                                else (f" (goal `{verify_goal_txt}` NOT "
                                      "judged)" if verify_goal_txt else ""))
                             + f" STILL FAILS — the answer "
                             f"below claims otherwise at its own risk. Last "
                             f"output: {v_out[:300]}]\n\n" + reply)
                else:
                    verified = True
                    reply += "\n\n" + (
                        f"[verified: `{verify_cmd}` exits 0"
                        + (f" and meets `{verify_goal_txt}`"
                           if verify_goal_txt and v_judged
                           else (f" — goal `{verify_goal_txt}` NOT judged"
                                 if verify_goal_txt else "")) + "]")
            _tail = _conversation_tail(messages)
            _latest = (user_message or "").strip()
            if not _latest:
                for _m in reversed(messages):
                    _said = user_said(_m)
                    if (_m.get("role") == "user" and _said
                            and not _said.startswith(_LOOP_MSG_PREFIXES + HARNESS_TAGS)):
                        _latest = _said
                        break
                if not _latest and _tail:
                    _latest = _tail[-1].split(": ", 1)[-1]
            if len(_tail) > 1:
                _prev = 0
                for _k in range(len(_tail) - 2, -1, -1):
                    if _tail[_k].startswith("user: "):
                        _prev = _k
                        break
                _ctx_lines = _tail[_prev:-1]
                _req = ("THE TURN BEFORE THIS ONE (what the request below may "
                        "be pointing at — everything earlier was already "
                        "reviewed when it happened):\n  "
                        + "\n  ".join(_ctx_lines)
                        + "\n\nLATEST MESSAGE (the one this answer must "
                          "satisfy):\n" + _latest) if _ctx_lines else _latest
            else:
                _req = _latest
            _goal = _take_turn_goal(turn_id) or goal
            _reviewable = (qa_mode in ("auto", "always")
                           and not _truncated
                           and (qa_mode == "always" or _goal
                                or _worth_reviewing(tool_trace, reply)
                                or (not tool_trace
                                    and len(reply.strip()) >= 350)))
            if _reviewable and qa_self:
                if not qa_rounds:
                    emit({"event": "qa_skipped", "why": "self",
                          "iterations": iteration})
                _reviewable = False
            if (_reviewable and qa_rounds < QA_MAX_ROUNDS
                    and iteration < max_it):
                qa_rounds += 1
                emit({"event": "qa_run", "round": qa_rounds, "iterations": iteration})
                qa_ok, qa_why, qa_next, qa_level = _run_qa(
                    _req, tool_trace, reply, qa_chat or chat, _goal, given,
                    prior=qa_verdicts, asides=_asides)
                if is_cancelled():
                    emit({"event": "cancelled", "iterations": iteration,
                          "turn_record": render_turn_record(
                              tool_trace, qa_verdicts, failed="cancelled")})
                    return {"cancelled": True, "tool_trace": tool_trace,
                            "iterations": iteration}
                emit({"event": "qa_result", "ok": qa_ok, "round": qa_rounds,
                      "why": qa_why, "next": qa_next, "mode": qa_level,
                      "reviewed": reply,
                      "said": getattr(_QA_LAST, "said", "")})
                qa_verdicts.append({"ok": qa_ok, "why": qa_why})
                if qa_ok is False:
                    if reply and not produced_nothing(reply):
                        qa_candidates.append(reply)
                    messages.append(_assistant_as_generated(msg))
                    messages.append(harness_msg(
                        f"[QUALITY REVIEW FAILED] {qa_why}\nWhat to do: {qa_next}\n\nRewrite the answer whole for the person, fixing the problem with better evidence if needed; never the same answer again.\nWHAT THEY ASKED:\n{_req}", tag=QA_TAG))
                    continue
            elif (_reviewable
                    and (not qa_verdicts
                         or not qa_verdicts[-1].get("ok"))):
                emit({"event": "qa_run", "round": qa_rounds + 1,
                      "iterations": iteration})
                qa_ok, qa_why, _qn, qa_level = _run_qa(
                    _req, tool_trace, reply, qa_chat or chat, _goal, given,
                    prior=qa_verdicts, asides=_asides)
                if is_cancelled():
                    emit({"event": "cancelled", "iterations": iteration,
                          "turn_record": render_turn_record(
                              tool_trace, qa_verdicts, failed="cancelled")})
                    return {"cancelled": True, "tool_trace": tool_trace,
                            "iterations": iteration}
                emit({"event": "qa_result", "ok": qa_ok,
                      "round": qa_rounds + 1, "why": qa_why, "next": _qn,
                      "mode": qa_level, "reviewed": reply,
                      "said": getattr(_QA_LAST, "said", "")})
                qa_verdicts.append({"ok": qa_ok, "why": qa_why})
                if qa_ok is False:
                    _best = max(qa_candidates, key=len) if qa_candidates else ""
                    if _best and len(reply) * 2 < len(_best):
                        emit({"event": "prep",
                              "note": {"key": "qa.redo_discarded",
                                       "args": {"kept": str(len(_best)),
                                                "dropped": str(len(reply))}}})
                        reply = _best
                    reply = (_rtr("chat.turn_qa_capped", why=qa_why)
                             + "\n\n" + reply)
            _left = [i for i in intent_items
                     if str(i.get("status", "")).lower() in ("pending", "doing", "")]
            if _bg_live:
                emit({"event": "background_live", "n": len(_bg_live),
                      "jobs": [{"id": j.get("id"), "label": j.get("label"),
                                "kind": j.get("kind")} for j in _bg_live]})
            elif _left:
                for i in _left:
                    i["status"] = "unfinished"
                emit({"event": "intent", "items": [dict(it) for it in intent_items]})
                emit({"event": "intent_left", "n": len(_left),
                      "goals": [str(i.get("goal", ""))[:60] for i in _left]})
            _rec = render_turn_record(tool_trace, qa_verdicts)
            emit({"event": "reply", "reply": reply, "iterations": iteration,
                  "turn_record": _rec, "transcript": messages[_turn_base:]})
            return {"reply": reply, "tool_trace": tool_trace,
                    "iterations": iteration, "verified": verified,
                    "turn_record": _rec, "transcript": messages[_turn_base:]}

        messages.append(_assistant_as_generated(msg, tool_calls,
                                                keep_content=not _tc_rescued))

        for tc in tool_calls:
            if is_cancelled():
                emit({"event": "cancelled", "iterations": iteration,
                  "turn_record": render_turn_record(tool_trace, qa_verdicts,
                                                    failed="cancelled")})
                return {"cancelled": True, "tool_trace": tool_trace,
                        "iterations": iteration}
            fn = tc.get("function", {})
            name = fn.get("name", "")
            raw_args = fn.get("arguments", "{}")
            args, _bad_args = call_args_or_complaint(raw_args, name,
                                                     cut=finish_reason == "length")
            call_id = tc.get("id") or f"call_{iteration}_{name}"
            if not _bad_args and name == "use_tool" and "arguments" in args:
                _ureal = str(args.get("name", "") or "").strip()
                _ui, _bad_args = call_args_or_complaint(
                    args["arguments"], _ureal or name, cut=finish_reason == "length")
                if not _bad_args:
                    args = dict(args, arguments=_ui)
            if _bad_args:
                result = json.dumps(_bad_args, ensure_ascii=False)
                emit({"event": "tool_result", "tool": name, "delegated": False,
                      "result_preview": result[:200], "duration_s": 0.0})
                tool_trace.append({"tool": name, "args": {"raw": str(raw_args)[:500]},
                                   "result": result})
                messages.append({"role": "tool", "tool_call_id": tc.get("id", name),
                                 "content": result})
                _sterile_tools += 1
                continue

            if name == "use_tool":
                _real = (str((args or {}).get("name", "")).strip()
                         if isinstance(args, dict) else "")
                _inner = ((args or {}).get("arguments", {})
                          if isinstance(args, dict) else {})
                if not _real:
                    result = json.dumps(
                        {"error": "use_tool needs `name` (a tool from the "
                                  "catalogue) and `arguments` (an object)",
                         "you_sent": sorted(args) if isinstance(args, dict) else [],
                         "it_came_as": json.dumps(args, ensure_ascii=False)[:200]},
                        ensure_ascii=False)
                    emit({"event": "tool_result", "tool": name,
                          "delegated": False, "result_preview": result[:200],
                          "duration_s": 0.0})
                    tool_trace.append({"tool": name, "args": args,
                                       "result": result})
                    messages.append({"role": "tool",
                                     "tool_call_id": tc.get("id", name),
                                     "content": result})
                    _sterile_tools += 1
                    continue
                name = _real
                args = _inner if isinstance(_inner, dict) else {}
            _bg = None
            if name == "background_run":
                _real, _inner, _label, _err = _unwrap_background(args)
                if not _err and _background is None:
                    _err = "background work is not available here."
                if _err:
                    result = json.dumps({"error": _err}, ensure_ascii=False)
                    emit({"event": "tool_result", "tool": name,
                          "delegated": False, "result_preview": result[:200],
                          "duration_s": 0.0})
                    tool_trace.append({"tool": name, "args": args,
                                       "result": result})
                    messages.append({"role": "tool",
                                     "tool_call_id": tc.get("id", name),
                                     "content": result})
                    _sterile_tools += 1
                    continue
                name, args = _real, _inner
                _bg = {"label": _label}
            args = unwrap_call_envelope(name, args)

            if name == "intent":
                emit({"event": "tool_call", "tool": name,
                      "args_preview": args_preview(args)})
                _changed, result = _apply_intent(intent_items, args)
                if _changed:
                    _progress()
                else:
                    _sterile_tools += 1
                emit({"event": "intent",
                      "items": [dict(it) for it in intent_items]})
                emit({"event": "tool_result", "tool": name,
                      "result_preview": result[:200], "duration_s": 0.0,
                      "delegated": False})
                tool_trace.append({"tool": name, "args": args,
                                   "result": result})
                messages.append({"role": "tool",
                                 "tool_call_id": tc.get("id", name),
                                 "content": result})
                continue

            delegated = _is_delegated(name)
            t0 = time.time()
            if name in _heavy_tools and preflight_rejects < 2:
                _pf_ok, _pf_why, _pf_fix = _preflight_expensive(
                    name, args, messages, qa_chat or chat)
                if not _pf_ok:
                    preflight_rejects += 1
                    _rej = json.dumps(
                        {"error": "preflight_rejected",
                         "why": _pf_why, "fix": _pf_fix,
                         "message": "This call contradicts a referent the "
                                    "conversation already settled. Correct "
                                    "the arguments and call again."},
                        ensure_ascii=False)
                    emit({"event": "tool_progress",
                          "key": "preflight.reject",
                          "args": {"tool": name, "why": _pf_why[:140]}})
                    emit({"event": "tool_result", "tool": name,
                          "delegated": False, "result_preview": _rej[:200],
                          "duration_s": 0.0})
                    tool_trace.append({"tool": name, "args": args,
                                       "result": _rej})
                    messages.append({"role": "tool",
                                     "tool_call_id": tc.get("id", name),
                                     "content": _rej})
                    _sterile_tools += 1
                    continue
            if name in _heavy_cut:
                result = json.dumps(
                    {"error": f"'{name}' is OFF for the rest of this turn",
                     "message": f"{name} came back sterile "
                                f"{_heavy_streaks.get(name, 0)} times in a "
                                "row this turn; this source is not yielding "
                                "the data this way. Work with what you have "
                                "or take a different path — do not call it "
                                "again."}, ensure_ascii=False)
                emit({"event": "tool_result", "tool": name, "delegated": False,
                      "result_preview": result[:200], "duration_s": 0.0})
                tool_trace.append({"tool": name, "args": args,
                                   "result": result})
                messages.append({"role": "tool",
                                 "tool_call_id": tc.get("id", name),
                                 "content": result})
                _sterile_tools += 1
                continue
            if not _is_allowed(name):
                result = json.dumps(
                    {"error": f"'{name}' is not available to this agent"},
                    ensure_ascii=False)
                emit({"event": "tool_result", "tool": name, "delegated": False,
                      "result_preview": result, "duration_s": 0.0})
                tool_trace.append({"tool": name, "args": args,
                                   "result": result})
                messages.append({"role": "tool",
                                 "tool_call_id": tc.get("id", name),
                                 "content": result})
                _sterile_tools += 1
                continue
            if delegated and callable(is_blocked) and is_blocked(name):
                result = json.dumps(
                    {"error": f"🛡 FIREWALL: '{name}' is blocked",
                     "message": "The user's firewall refuses this tool. Do "
                                "not retry it; work another way or say what "
                                "you could not do."}, ensure_ascii=False)
                emit({"event": "tool_result", "tool": name, "delegated": False,
                      "result_preview": result, "duration_s": 0.0})
                tool_trace.append({"tool": name, "args": args,
                                   "result": result})
                messages.append({"role": "tool",
                                 "tool_call_id": tc.get("id", name),
                                 "content": result})
                _sterile_tools += 1
                continue
            _refusal = None
            if _consent is not None:
                try:
                    _refusal = _consent(name, args)
                except Exception as e:
                    _refusal = {"error": f"the person's consent could not be asked: {e}"}
            if _refusal:
                result = json.dumps(_refusal, ensure_ascii=False)
                emit({"event": "tool_result", "tool": name, "delegated": False,
                      "result_preview": result, "duration_s": 0.0})
                tool_trace.append({"tool": name, "args": args,
                                   "result": result})
                messages.append({"role": "tool",
                                 "tool_call_id": tc.get("id", name),
                                 "content": result})
                _sterile_tools += 1
                continue
            _fw_shut, _role_shut = [], []
            if name == "load_tools" and isinstance(args, dict):
                _asked = args.get("names")
                _asked = ([_asked] if isinstance(_asked, str)
                          else list(_asked or []))
                _asked = [str(n).strip() for n in _asked if str(n).strip()]
                _known = _get_tools() or {}
                _fw_shut = [n for n in _asked
                            if callable(is_blocked) and is_blocked(n)]
                _role_shut = [n for n in _asked if n not in _fw_shut
                              and n in _known and not _is_allowed(n)]
                if _fw_shut or _role_shut:
                    args = dict(args, names=[n for n in _asked
                                             if n not in _fw_shut
                                             and n not in _role_shut])
            if _bg is not None:
                _work = None if delegated else (
                    lambda _n=name, _a=args: _execute_tool(_n, _a, consented=True))
                try:
                    _job = _background.start(name, args, _bg["label"], turn_id,
                                             delegated=delegated, work=_work)
                except Exception as _e:
                    _job = {"error": f"could not start it: {_e}"}
                if not isinstance(_job, dict) or _job.get("error"):
                    result = json.dumps(_job if isinstance(_job, dict)
                                        else {"error": "could not start it"},
                                        ensure_ascii=False)
                    _sterile_tools += 1
                else:
                    if delegated:
                        emit({"event": "client_tool_call", "call_id": call_id,
                              "tool": name, "args": args,
                              "background": _job["id"]})
                    else:
                        emit({"event": "tool_call", "tool": name,
                              "args_preview": args_preview(args),
                              "background": _job["id"]})
                    result = json.dumps(
                        {"started": True, "job_id": _job["id"], "tool": name,
                         "label": _job.get("label", ""),
                         "note": "It runs on its own. Its result reaches you "
                                 "as a message when it lands: in this turn "
                                 "if you are still working, or as this "
                                 "conversation's next turn if you ended it. "
                                 "Do not poll for it; background_stop(job_id) "
                                 "cancels it."}, ensure_ascii=False)
                    _progress()
                emit({"event": "tool_result", "tool": name,
                      "delegated": delegated, "result_preview": result[:200],
                      "duration_s": 0.0, "background": True})
                tool_trace.append({"tool": name, "args": args,
                                   "result": result, "background": True})
                messages.append({"role": "tool",
                                 "tool_call_id": tc.get("id", name),
                                 "content": result})
                continue
            if delegated:
                emit({"event": "client_tool_call", "call_id": call_id,
                      "tool": name, "args": args})
                try:
                    result = delegate(call_id, name, args)
                except TimeoutError as e:
                    result = json.dumps(
                        {"error": f"'{name}' timed out on the client: {e}",
                         "message": "That single call overran its clock and "
                                    "was unfinished. Retry it (maybe with "
                                    "narrower arguments), route around it, "
                                    "or drop that path — the task is still "
                                    "yours."},
                        ensure_ascii=False)
                    emit({"event": "tool_result", "tool": name,
                          "result_preview": result[:200], "duration_s": 0.0,
                          "delegated": True})
                if result is None:
                    emit({"event": "cancelled", "iterations": iteration,
                  "turn_record": render_turn_record(tool_trace, qa_verdicts,
                                                    failed="cancelled")})
                    return {"cancelled": True, "tool_trace": tool_trace,
                            "iterations": iteration}
            else:
                emit({"event": "tool_call", "tool": name,
                      "args_preview": args_preview(args)})
                _tool_emit.fn = emit
                _tool_emit.tail = _conversation_tail(messages)[-8:]
                _tool_emit.messages = messages
                _tool_emit.line_cap = line_cap(_window)
                _tool_emit.turn = turn_id
                _tool_emit.may = _may_here
                try:
                    result = _execute_tool(name, args, consented=True)
                finally:
                    _tool_emit.fn = None
                    _tool_emit.tail = []
                    _tool_emit.messages = []
                    _tool_emit.line_cap = 0
                    _tool_emit.turn = ""
                    _tool_emit.may = None
            if name == "load_tools" and (_fw_shut or _role_shut):
                if _fw_shut:
                    result += ("\n[HOPLITA] 🛡 FIREWALL: " + ", ".join(_fw_shut)
                               + " blocked in this conversation: not served "
                                 "and not callable. Do not ask for them "
                                 "again; work another way or say what you "
                                 "could not do.")
                if _role_shut:
                    result += "\n[HOPLITA] " + _t("tools.withheld",
                                                  names=", ".join(_role_shut))
                    if any(g in ("render_html", "dashboard_render")
                           for g in _role_shut):
                        result += " " + _t("tools.withheld_canvas")
                emit({"event": "tools_withheld",
                      "tools": _fw_shut + _role_shut,
                      "firewall": list(_fw_shut)})
            if name == "load_tools":
                if _heavy_cut:
                    try:
                        _asked_cut = [g for g in
                                      ((json.loads(result) or {})
                                       .get("loaded") or [])
                                      if g in _heavy_cut]
                    except Exception:
                        _asked_cut = []
                    if _asked_cut:
                        result += ("\n[HOPLITA] " + ", ".join(_asked_cut)
                                   + " exists, but it came back sterile "
                                     f"{_METACOG['heavy_cut']} times in a "
                                     "row this turn and is OFF until the "
                                     "next turn. Do not request it again: "
                                     "answer with what you have, or take a "
                                     "different path.")
            if name == "load_tools" and expanded is not None:
                try:
                    _got = (json.loads(result) or {}).get("loaded") or []
                except Exception:
                    _got = []
                _held = ([g for g in _got if g not in set(allowed_tools)]
                         if allowed_tools is not None else [])
                if _held:
                    result += "\n[HOPLITA] " + _t("tools.withheld",
                                                  names=", ".join(_held))
                    if any(g in ("render_html", "dashboard_render")
                           for g in _held):
                        result += " " + _t("tools.withheld_canvas")
                    _got = [g for g in _got if g not in _held]
                    emit({"event": "tools_withheld", "tools": _held})
                if _got:
                    emit({"event": "tools_expanded", "tools": _got,
                          "total": len(all_tools)})
                    result += ("\n[HOPLITA] Call " + ", ".join(_got)
                               + " through use_tool(name=..., arguments={...})"
                                 " — the manual above is complete; nothing "
                                 "else needs loading.")
            if '"screenshot_b64"' in result:
                try:
                    _shot = json.loads(result)
                    _b64 = _shot.pop("screenshot_b64", "")
                    _mime = _shot.pop("screenshot_mime", "image/png")
                    if _b64:
                        if len(_pending_shots) < 2:
                            _pending_shots.append((_mime, _b64))
                            _shot["screenshot"] = ("attached as an image "
                                                   "right after this round's "
                                                   "tool results")
                        else:
                            _shot["screenshot"] = ("DROPPED: two screenshots "
                                                   "already ride this round; "
                                                   "look at those first")
                        result = json.dumps(_shot, ensure_ascii=False)
                except Exception:
                    pass
            _cap = (line_cap(_window) if name in _LINE_ORIENTED_TOOLS
                    else _RESULT_CAP)
            _cut_here = False
            if len(result) > _cap:
                _full_len = len(result)
                _verbatim = _take_verbatim(result)
                _dig = ("" if name in _LINE_ORIENTED_TOOLS
                        else _digest_result(name, args, result, messages,
                                            _side_chat or chat))
                if _dig:
                    _loc = ""
                    if _spill_fn is not None:
                        try:
                            _loc = _spill_fn(name, result) or ""
                        except Exception:
                            _loc = ""
                    _by = ""
                    try:
                        _by = (_side_chat.label() if _side_chat is not None
                               and hasattr(_side_chat, "label") else "") or ""
                    except Exception:
                        _by = ""
                    emit({"event": "tool_progress", "key": "digest.applied",
                          "args": {"tool": name, "total": str(_full_len),
                                   "by": _by or "?"}})
                    result = (_verbatim + _dig
                              + f"\n[DIGEST of a {_full_len}-char result."
                              + " Data above was extracted, not invented."
                              + (f" The FULL text is kept and searchable: "
                                 f"{_loc}. Something not in this extract may "
                                 f"well be in the original: search it before "
                                 f"re-calling the tool, which would return "
                                 f"the same text." if _loc else
                                 " If something you need is missing, re-call "
                                 "the tool, narrower.")
                              + "]")
                elif (_struct := _cut_text_field(name, args, result, _cap)):
                    _txt, _a, _b, _n = _struct
                    _cut_here = True
                    _loc = ""
                    if _spill_fn is not None:
                        try:
                            _loc = _spill_fn(name, result) or ""
                        except Exception:
                            _loc = ""
                    emit({"event": "tool_progress", "key": "digest.cut",
                          "args": {"tool": name, "total": str(_full_len),
                                   "first": str(_a), "shown": str(_b),
                                   "lines": str(_n)}})
                    if _loc:
                        _o = json.loads(_txt)
                        _o["full_text"] = f"kept and searchable: {_loc}"
                        _txt = json.dumps(_o, ensure_ascii=False)
                    result = _verbatim + _txt
                else:
                    _kept = result[:_cap]
                    _nl = _kept.rfind("\n")
                    if _nl > _cap // 2:
                        _kept = _kept[:_nl]
                    _loc = ""
                    if _spill_fn is not None:
                        try:
                            _loc = _spill_fn(name, result) or ""
                        except Exception:
                            _loc = ""
                    _shown = _kept.count("\n") + 1
                    _total = result.count("\n") + 1
                    emit({"event": "tool_progress", "key": "digest.cut",
                          "args": {"tool": name, "total": str(_full_len),
                                   "first": "1", "shown": str(_shown),
                                   "lines": str(_total)}})
                    result = (_verbatim + _kept
                              + f"\n...[CUT: lines 1-{_shown} of {_total} "
                                f"({len(_kept)} of {_full_len} chars). The "
                                f"rest EXISTS and is not here."
                              + (f" FULL text kept and searchable: "
                                 f"{_loc}." if _loc else "")
                              + f" Or: {_range_hint(name)}. "
                                "Never fill it in from memory.]")
            if not _cut_here and name in _FILE_READERS and '"cut"' in result:
                try:
                    _o = json.loads(result)
                    _m = re.match(r"lines (\d+)-(\d+) of (\d+)",
                                  str(_o.get("cut") or "")) if isinstance(_o, dict) else None
                except Exception:
                    _m = None
                if _m:
                    _cut_here = True
                    emit({"event": "tool_progress", "key": "digest.cut",
                          "args": {"tool": name, "total": str(len(result)),
                                   "first": _m.group(1), "shown": _m.group(2),
                                   "lines": _m.group(3)}})
            if _partial and not _cut_here:
                _argtxt = json.dumps(args, ensure_ascii=False, default=str)
                for _k2, _r2 in _partial.items():
                    if name not in _FILE_READERS and (
                            str(_r2["path"]) in _argtxt or _k2 in _argtxt):
                        _r2["consulted"] = True
            _seen = _lines_seen(name, args, result)
            if _seen:
                _p, _a, _b, _n = _seen
                _k = os.path.normpath(os.path.expanduser(str(_p)))
                if _k in _partial or _cut_here:
                    _r = _partial.setdefault(_k, {"path": _p, "total": _n, "seen": set()})
                    _r["seen"].update(range(_a, _b + 1))
            dur = round(time.time() - t0, 2)
            _firma = f"{name}:{json.dumps(args, sort_keys=True, default=str)[:600]}"
            _raw = result[:600]
            _before = _seen_calls.get(_firma)
            _es_repe = _before is not None and _before == _raw
            if _es_repe:
                _repeats[_firma] = _repeats.get(_firma, 1) + 1
                result += ("\n\n[HOPLITA] Identical call, identical result "
                           f"({_repeats[_firma]} times now). Repeating it will "
                           "return this again. If what you needed is not in "
                           "here, the source does not have it in this form — "
                           "change the approach or say what is missing; do not "
                           "retry.")
            _seen_calls[_firma] = _raw
            _covered = _covered_by_earlier(name, args, _calls_log)
            _calls_log.append((name, args, _result_was_complete(result)))
            _sterile = _is_sterile_result(result, _es_repe, _covered)
            if _sterile:
                _sterile_tools += 1
                if _asked_since_progress:
                    _sterile_since_ask += 1
            else:
                _progress()
            if name in _heavy_tools:
                if _sterile:
                    _heavy_streaks[name] = _heavy_streaks.get(name, 0) + 1
                    if (_heavy_streaks[name] >= _METACOG["heavy_cut"]
                            and name not in _heavy_cut):
                        _heavy_cut.add(name)
                        result += (f"\n[HOPLITA] {name} has come back "
                                   "sterile (empty/error/repeat) "
                                   f"{_heavy_streaks[name]} times in a row "
                                   "— this source is not yielding the data "
                                   "this way. It is OFF for the rest of "
                                   "this turn: work with what you have, or "
                                   "take a different path.")
                        emit({"event": "tool_progress",
                              "key": "chat.heavy_cut",
                              "args": {"tool": name,
                                       "n": _heavy_streaks[name]}})
                else:
                    _heavy_streaks[name] = 0
            if _repeats.get(_firma, 1) >= _LOOP_LIMIT and _firma not in _loop_reflected:
                _loop_reflected.add(_firma)
                _repeats[_firma] = 0
                _asked_since_progress += 1
                _sterile_since_ask = 0
                messages.append({"role": "tool",
                                 "tool_call_id": tc.get("id", name),
                                 "content": result})
                _q = _t("inject.metacog_loop", tool=name, n=_LOOP_LIMIT)
                if council:
                    _q += "\n\n" + _t("inject.metacog_council")
                messages.append(harness_msg(_q))
                emit({"event": "metacog_nudge", "n": iteration,
                      "sterile_tools": _sterile_tools, "stalled_s": 0,
                      "reason": "loop", "tool": name, "council": bool(council)})
                tool_trace.append({"tool": name, "args": args, "result": result})
                emit({"event": "tool_result", "tool": name,
                      "result_preview": result_preview(name, result), "duration_s": dur,
                      "delegated": delegated})
                continue
            if _repeats.get(_firma, 1) >= _LOOP_LIMIT:
                if not _escalated and escalate is not None:
                    try:
                        _new = escalate()
                    except Exception as _e:
                        _new = None
                        emit({"event": "escalate_failed", "why": str(_e)[:160]})
                    if _new:
                        _chat2, _label = _new
                        chat = _chat2
                        _escalated = True
                        messages.append({"role": "tool",
                                         "tool_call_id": tc.get("id", name),
                                         "content": result})
                        messages.append(harness_msg(_t("inject.escalated",
                                                        model=_label)))
                        emit({"event": "model_escalated", "to": _label,
                              "n": iteration, "after": "loop_broken",
                              "tool": name, "times": _repeats[_firma]})
                        _repeats[_firma] = 0
                        continue
                emit({"event": "loop_broken", "tool": name,
                      "times": _repeats[_firma]})
                messages.append({"role": "tool",
                                 "tool_call_id": tc.get("id", name),
                                 "content": result})
                _reply = _t("agent.loop_broken", tool=name,
                            n=_repeats[_firma])
                _rec = render_turn_record(
                    tool_trace, qa_verdicts,
                    failed=f"loop broken: {name} x{_repeats[_firma]}")
                emit({"event": "reply", "reply": _reply,
                      "iterations": iteration, "turn_record": _rec,
                      "transcript": messages[_turn_base:]})
                return {"reply": _reply,
                        "iterations": iteration, "tool_trace": tool_trace,
                        "stopped": "loop", "turn_record": _rec,
                        "transcript": messages[_turn_base:]}
            tool_trace.append({"tool": name, "args": args, "result": result})
            emit({"event": "tool_result", "tool": name,
                  "result_preview": result_preview(name, result), "duration_s": dur,
                  "delegated": delegated})
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", name),
                "content": result,
            })

        if _pending_shots:
            _content = [{"type": "text", "text": HARNESS_TAG + "\n" + _t("canvas.shot")}]
            for _mime, _b64 in _pending_shots:
                _content.append({"type": "image_url", "image_url": {
                    "url": f"data:{_mime};base64,{_b64}"}})
            messages.append({"role": "user", "content": _content})
            emit({"event": "canvas_shot_attached", "n": len(_pending_shots)})
            _pending_shots = []

    _rec = render_turn_record(tool_trace, qa_verdicts,
                              failed="iteration limit reached")
    emit({"event": "iteration_limit", "iterations": max_it,
          "turn_record": _rec})
    return {"reply": f"{NO_REPLY_MARKS[2]} without concluding. "
                     "Check the tool trace or rephrase.]",
            "tool_trace": tool_trace, "iterations": max_it,
            "turn_record": _rec}
