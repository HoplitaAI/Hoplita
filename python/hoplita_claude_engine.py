import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time

SDK_PACKAGE = "claude-agent-sdk"

TOOL_DOORS = {
    "Bash": "run_command", "BashOutput": "run_command", "KillShell": "run_command",
    "KillBash": "run_command",
    "Edit": "code_apply_patch", "MultiEdit": "code_apply_patch",
    "Write": "code_write_file", "NotebookEdit": "code_write_file",
    "Read": "read_file", "Glob": "list_dir", "Grep": "code_search", "LS": "list_dir",
    "WebFetch": "scrape_url", "WebSearch": "search_web",
}

_STATUS = {"pending": "pending", "in_progress": "doing", "completed": "done"}


def sdk_available():
    return importlib.util.find_spec("claude_agent_sdk") is not None


def cli_path():
    return shutil.which("claude")


def status():
    out = {"sdk": sdk_available(), "cli": cli_path() or "",
           "api_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
           "signed_in": False, "sdk_version": ""}
    home = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    out["signed_in"] = (None if sys.platform == "darwin"
                        else os.path.isfile(os.path.join(home, ".credentials.json")))
    if out["sdk"]:
        try:
            import claude_agent_sdk as _s
            out["sdk_version"] = getattr(_s, "__version__", "")
        except Exception:
            out["sdk"] = False
    return out


def install(timeout=900):
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", SDK_PACKAGE],
                           capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        return {"ok": False, "output": str(e)}
    importlib.invalidate_caches()
    return {"ok": r.returncode == 0 and sdk_available(),
            "output": ((r.stdout or "") + (r.stderr or ""))[-1500:]}


def _preview(v, n=300):
    if isinstance(v, str):
        s = v
    else:
        try:
            s = json.dumps(v, ensure_ascii=False)
        except Exception:
            s = str(v)
    return s if len(s) <= n else s[:n] + "…"


def _result_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(c.get("text", "")) if isinstance(c, dict) else str(c)
                         for c in content)
    return "" if content is None else str(content)


def run_turn(prompt, emit, should_stop, cwd="", session_id="", door_blocked=None,
             system_append="", model=""):
    if not sdk_available():
        return {"error": "the Claude Code SDK is not installed"}
    import claude_agent_sdk as S

    state = {"reply": "", "session_id": session_id or "", "cost": None,
             "tools": {}, "t0": {}, "n_tools": 0, "error": ""}

    def _shut(name):
        door = TOOL_DOORS.get(name)
        if door and door_blocked is not None and door_blocked(door):
            return f"HOPLITA's firewall has {door} shut, which {name} opens."
        return ""

    async def pre_tool(inp, _tool_use_id, _ctx):
        why = _shut((inp or {}).get("tool_name", ""))
        if why:
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                           "permissionDecision": "deny",
                                           "permissionDecisionReason": why}}
        return {}

    async def can_use(name, args, _ctx):
        why = _shut(name)
        if why:
            return S.PermissionResultDeny(message=why)
        return S.PermissionResultAllow()

    opts = dict(cwd=(cwd or os.path.expanduser("~")), can_use_tool=can_use,
                hooks={"PreToolUse": [S.HookMatcher(matcher=None, hooks=[pre_tool])]},
                permission_mode="default", client_app="hoplita")
    if session_id:
        opts["resume"] = session_id
    if cli_path():
        opts["cli_path"] = cli_path()
    if system_append:
        opts["system_prompt"] = {"type": "preset", "preset": "claude_code",
                                 "append": system_append}
    if model:
        opts["model"] = model

    def on_message(m):
        if isinstance(m, S.SystemMessage):
            if m.subtype == "init":
                d = m.data or {}
                state["session_id"] = d.get("session_id") or state["session_id"]
                emit({"event": "llm_call", "model": d.get("model") or "Claude Code",
                      "n_messages": 0, "n_tools": len(d.get("tools") or []),
                      "payload_kb": 0, "engine": "claude_code"})
            return
        if isinstance(m, S.AssistantMessage):
            sub = bool(m.parent_tool_use_id)
            if getattr(m, "error", None) and not sub:
                state["error"] = " ".join(b.text for b in (m.content or [])
                                          if isinstance(b, S.TextBlock)).strip() \
                    or str(m.error)
                return
            for b in m.content or []:
                if isinstance(b, S.ThinkingBlock) and (b.thinking or "").strip():
                    emit({"event": "thought", "text": b.thinking})
                elif isinstance(b, S.TextBlock) and not sub:
                    state["reply"] = b.text
                elif isinstance(b, S.ToolUseBlock):
                    state["tools"][b.id] = b.name
                    state["t0"][b.id] = time.time()
                    state["n_tools"] += 1
                    if b.name == "TodoWrite" and not sub:
                        items = [{"id": i + 1, "goal": str(t.get("content", "")),
                                  "status": _STATUS.get(t.get("status"), "pending")}
                                 for i, t in enumerate((b.input or {}).get("todos") or [])]
                        emit({"event": "intent", "items": items})
                    emit({"event": "tool_call", "tool": b.name,
                          "args_preview": _preview(b.input, 240),
                          "subagent": m.parent_tool_use_id or ""})
            return
        if isinstance(m, S.UserMessage):
            for b in (m.content if isinstance(m.content, list) else []):
                if isinstance(b, S.ToolResultBlock):
                    name = state["tools"].get(b.tool_use_id, "?")
                    dur = time.time() - state["t0"].get(b.tool_use_id, time.time())
                    text = _result_text(b.content)
                    emit({"event": "tool_result", "tool": name, "delegated": False,
                          "duration_s": round(dur, 2),
                          "result_preview": (json.dumps({"error": text[:300]}, ensure_ascii=False)
                                             if b.is_error else _preview(text, 300))})
            return
        if isinstance(m, S.ResultMessage):
            state["session_id"] = m.session_id or state["session_id"]
            state["cost"] = m.total_cost_usd
            if m.is_error:
                state["error"] = state["error"] or "; ".join(str(e) for e in (m.errors or [])) \
                    or (m.result or "").strip() or (m.subtype or "error")
                if not state["n_tools"] and not state["reply"]:
                    state["session_id"] = session_id or ""
            elif m.result and not state["reply"]:
                state["reply"] = m.result
            u = m.usage or {}
            pt = int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0) \
                + int(u.get("cache_creation_input_tokens") or 0)
            if pt:
                emit({"event": "tokens", "prompt_tokens": pt, "iteration": m.num_turns,
                      "estimated": False})

    async def go():
        async with S.ClaudeSDKClient(options=S.ClaudeAgentOptions(**opts)) as client:
            await client.query(prompt)
            stopper = asyncio.create_task(_watch_stop(client))
            try:
                async for m in client.receive_response():
                    on_message(m)
            finally:
                stopper.cancel()

    async def _watch_stop(client):
        while True:
            await asyncio.sleep(0.5)
            if should_stop():
                try:
                    await client.interrupt()
                except Exception:
                    pass
                return

    try:
        asyncio.run(go())
    except Exception as e:
        state["error"] = state["error"] or f"{type(e).__name__}: {e}"
    out = {"reply": state["reply"], "session_id": state["session_id"],
           "cost_usd": state["cost"], "tools": state["n_tools"]}
    if state["error"]:
        out["error"] = state["error"]
    return out
