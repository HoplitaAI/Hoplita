
import json
import queue
import sys
import threading
import time
import uuid

import hoplita_crypto as crypto
import hoplita_federation as fed


T_TURN = "agent.turn"
T_EVENT = "agent.event"
T_STOP = "agent.stop"
T_CLIENT_TOOL_CALL = "agent.client_tool_call"
T_TOOL_RESULT = "agent.tool_result"

TERMINAL_EVENTS = ("reply", "cancelled", "iteration_limit", "error")

_store_getter = None
_run_turn = None
def _incoming_qa_mode():
    import os
    return (os.environ.get("HOPLITA_INCOMING_QA", "always").strip().lower()
            or "always")
_execute_local_tool = None
_session_system = None
_session_tools = None
_orchestration_ctx = None


def configure(store_getter, run_turn, execute_local_tool,
              session_system_getter=None, orchestration_ctx=None,
              session_tools_getter=None):
    global _store_getter, _run_turn, _execute_local_tool, _session_system
    global _orchestration_ctx, _session_tools
    _store_getter = store_getter
    _run_turn = run_turn
    _execute_local_tool = execute_local_tool
    _session_system = session_system_getter
    _orchestration_ctx = orchestration_ctx
    _session_tools = session_tools_getter


def _orchestrating(**kw):
    if _orchestration_ctx is None:
        import contextlib
        return contextlib.nullcontext()
    return _orchestration_ctx(**kw)


def _session_tools_for():
    if _session_tools is None:
        return None
    try:
        return _session_tools()
    except Exception:
        return None


def _session_system_for(message):
    if _session_system is None:
        return None
    try:
        return _session_system(message)
    except Exception:
        return None


def _session_ctx():
    store = _store_getter() if _store_getter else None
    if store is None or not store.has_identity():
        raise RuntimeError("No federated identity in the vault.")
    d = store.data
    token = d.get("token")
    if not token:
        raise RuntimeError("No active session. Log in first.")
    return (store, d["server_url"], token,
            d["private_key_b64"], d["public_key_b64"])


def _peer_pubkey(server_url, token, handle):
    resp = fed.server_get_key(server_url, token, handle)
    pub = resp.get("public_key")
    if not pub:
        raise RuntimeError(f"'{handle}' has no public key on the server.")
    return pub


def _send(server_url, token, my_priv, recipient, recipient_pub, rtype,
          payload, session_id, device=""):
    meta = {"session_id": session_id}
    if device:
        meta["device"] = device
    env = crypto.make_envelope(rtype, payload, meta=meta)
    blob = crypto.box_for(my_priv, recipient_pub, env)
    fed.server_send_envelope(server_url, token, recipient, rtype, blob,
                             meta=meta)



class SessionOriginator:

    _THROTTLE_SAY_AFTER = 4

    RESCUE_POLL_S = 5.0

    def _wait_next(self):
        if self._wake.wait(self.poll_interval):
            self._wake.clear()
        return not self._stop.is_set()

    def wake(self):
        self._wake.set()

    @staticmethod
    def _throttle_wait(exc):
        if getattr(exc, "status", None) != 429:
            return None
        return max(1.0, float(getattr(exc, "retry_after", None) or 2.0))

    def __init__(self, peer_handle, on_event, poll_interval=None,
                 overall_timeout=1800, device="", tab=""):
        self.peer = peer_handle
        self.device = (device or "").strip()
        self.tab = (tab or "").strip()
        self.on_event = on_event or (lambda ev: None)
        self.poll_interval = poll_interval or self.RESCUE_POLL_S
        self.overall_timeout = overall_timeout
        self.session_id = uuid.uuid4().hex[:12]
        self._throttled = 0
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._done = threading.Event()
        self._opened = False

    def is_alive(self):
        return not self._done.is_set() and not self._stop.is_set()

    def send_turn(self, message, max_iterations=None):
        store, server_url, token, my_priv, my_pub = _session_ctx()
        peer_pub = _peer_pubkey(server_url, token, self.peer)
        turn_payload = {"message": message, "max_iterations": max_iterations,
                        "origin_pub": my_pub}
        if self.tab:
            turn_payload["tab"] = self.tab
        _send(server_url, token, my_priv, self.peer, peer_pub, T_TURN,
              turn_payload, self.session_id, device=self.device)
        print(f"[SESSION-OUT] turn SENT → recipient={self.peer!r} "
              f"session={self.session_id}", file=sys.stderr)
        if not self._opened:
            self._opened = True
            self.on_event({"event": "session_open", "peer": self.peer,
                           "session_id": self.session_id})

    def stop(self):
        self._stop.set()
        try:
            store, server_url, token, my_priv, my_pub = _session_ctx()
            peer_pub = _peer_pubkey(server_url, token, self.peer)
            _send(server_url, token, my_priv, self.peer, peer_pub, T_STOP,
                  {"reason": "user_stop"}, self.session_id)
        except Exception:
            pass

    def start(self, message, max_iterations=None, persistent=False):
        store, server_url, token, my_priv, my_pub = _session_ctx()
        peer_pub = _peer_pubkey(server_url, token, self.peer)

        self.send_turn(message, max_iterations)

        deadline = time.time() + self.overall_timeout
        terminal = {"event": "error", "error": "session with no response (timeout)"}
        while not self._stop.is_set() and time.time() < deadline:
            try:
                resp = fed.server_fetch_inbox_types(
                    server_url, token, [T_EVENT, T_CLIENT_TOOL_CALL],
                    prefer_rest=True)
            except Exception as e:
                wait = self._throttle_wait(e)
                if wait is not None:
                    self._throttled += 1
                    if self._throttled == self._THROTTLE_SAY_AFTER:
                        self.on_event({"event": "status",
                                       "text": "server asking to slow down; "
                                               "backing off"})
                    self._stop.wait(wait)
                    continue
                self._throttled = 0
                self.on_event({"event": "error",
                               "error": f"failed to receive events: {e}"})
                terminal = {"event": "error", "error": str(e)}
                if not persistent:
                    break
                self._stop.wait(self.poll_interval)
                continue
            envelopes = resp.get("envelopes", [])
            mine = [e for e in envelopes
                    if (e.get("meta") or {}).get("session_id") == self.session_id]
            got_terminal = False
            for env in sorted(mine, key=lambda e: e.get("created_at", "")):
                try:
                    dec = crypto.unbox(my_priv, peer_pub, env["ciphertext"])
                except Exception:
                    continue
                ev = dec.get("payload", {})
                rtype = env["resource_type"]
                deadline = time.time() + self.overall_timeout
                if isinstance(ev, dict):
                    ev.setdefault("t_recv", time.time())
                    if env.get("created_at"):
                        ev.setdefault("t_server", env.get("created_at"))
                if rtype == T_CLIENT_TOOL_CALL:
                    self._handle_client_tool_call(
                        ev, server_url, token, my_priv, peer_pub)
                    continue
                self.on_event(ev)
                if ev.get("event") == "session_closed":
                    self._done.set()
                    return ev
                if ev.get("event") in TERMINAL_EVENTS:
                    terminal = ev
                    got_terminal = True
                    if not persistent:
                        break
            if got_terminal and not persistent:
                self._done.set()
                return terminal
            self._wait_next()

        if self._stop.is_set():
            terminal = {"event": "cancelled", "iterations": 0}
            self.on_event(terminal)
        self._done.set()
        return terminal

    def _handle_client_tool_call(self, ev, server_url, token, my_priv, peer_pub):
        call_id = ev.get("call_id")
        name = ev.get("tool", "")
        args = ev.get("args", {}) or {}
        try:
            result = _execute_local_tool(name, args) if _execute_local_tool \
                else json.dumps({"error": "no local tool executor"})
        except Exception as e:
            result = json.dumps({"error": f"failed executing '{name}': {e}"})
        if not isinstance(result, str):
            result = json.dumps(result, ensure_ascii=False)
        _send(server_url, token, my_priv, self.peer, peer_pub, T_TOOL_RESULT,
              {"call_id": call_id, "result": result}, self.session_id)



class EventOutbox:

    RETRIES = 3

    def __init__(self, send_fn):
        self._q = queue.Queue()
        self._send = send_fn
        self.sent = 0
        self.failed = 0
        self.slowest_s = 0.0
        self._t = threading.Thread(target=self._run, daemon=True,
                                   name="fed-session-outbox")
        self._t.start()

    def put(self, ev):
        self._q.put(ev)

    def close(self, timeout=60):
        self._q.put(None)
        self._t.join(timeout)
        return not self._t.is_alive()

    def report(self, session_id):
        if self.failed:
            print(f"[SESSION-OUT] {self.failed} event(s) lost, {self.sent} sent, "
                  f"slowest send {self.slowest_s:.1f}s (session {session_id})",
                  file=sys.stderr)

    def _run(self):
        while True:
            ev = self._q.get()
            if ev is None:
                return
            t0 = time.time()
            for attempt in range(self.RETRIES + 1):
                try:
                    self._send(ev)
                    self.sent += 1
                    break
                except Exception as e:
                    if attempt == self.RETRIES:
                        self.failed += 1
                        print(f"[SESSION-OUT] event {ev.get('event')!r} lost "
                              f"after {self.RETRIES + 1} tries: {e}",
                              file=sys.stderr)
                        break
                    wait = SessionOriginator._throttle_wait(e)
                    time.sleep(min(wait if wait is not None else 1.0, 30.0))
            self.slowest_s = max(self.slowest_s, time.time() - t0)


def handle_incoming_turn(env, get_trust, approver=None, local_sink=None):
    store, server_url, token, my_priv, my_pub = _session_ctx()
    session_id = (env.get("meta") or {}).get("session_id") or uuid.uuid4().hex[:12]
    sender = env.get("sender", "")

    try:
        sender_pub = _peer_pubkey(server_url, token, sender)
    except Exception as e:
        return {"ok": False, "reason": f"no key for '{sender}': {e}"}

    try:
        dec = crypto.unbox(my_priv, sender_pub, env["ciphertext"])
    except Exception:
        return {"ok": False, "reason": "authorship not verified (possible spoofing)"}
    payload = dec.get("payload", {})
    message = payload.get("message", "")
    max_iter = payload.get("max_iterations")

    trust = get_trust(sender)
    if sender and sender == store.data.get("handle"):
        trust = "open"
        print(f"[SESSION-IN] turn from MY OWN identity on another device "
              f"· session={session_id}", file=sys.stderr)
    print(f"[SESSION-IN] turn from {sender!r} · trust={trust!r} · "
          f"session={session_id}", file=sys.stderr)
    if trust is None or trust == "reject":
        try:
            _send(server_url, token, my_priv, sender, sender_pub, T_EVENT,
                  {"event": "error", "code": "declined", "retryable": False,
                   "error": f"'{store.data['handle']}' does not allow you to "
                            f"open remote sessions (permission 'no')."},
                  session_id)
        except Exception:
            pass
        return {"ok": False, "reason": f"'{sender}' has no session permission"}
    if trust != "open":
        approved = False
        if approver is not None:
            try:
                approved = bool(approver(sender, message, trust == "analyze"))
            except Exception:
                approved = False
        if not approved:
            _send(server_url, token, my_priv, sender, sender_pub, T_EVENT,
                  {"event": "error", "code": "declined", "retryable": False,
                   "error": f"'{store.data['handle']}' rejected the task "
                            f"(trust '{trust}', not approved)."},
                  session_id)
            return {"ok": False, "reason": "not approved by the gate"}

    cancel_flag = threading.Event()
    _register_cancel(session_id, cancel_flag)
    _new_session = not has_inbound_session(sender)
    _register_inbound_session(sender, session_id, sender_pub)

    _tab = str((payload or {}).get("tab") or "").strip()

    outbox = EventOutbox(lambda ev: _send(server_url, token, my_priv, sender,
                                          sender_pub, T_EVENT, ev, session_id))

    def on_event(ev):
        if local_sink:
            try:
                local_sink(dict(ev, tab=_tab) if _tab else ev)
            except Exception:
                pass
        outbox.put(dict(ev, t_exec=time.time()))

    if local_sink:
        try:
            if _new_session:
                local_sink({"event": "session_open", "peer": sender,
                            "session_id": session_id})
            local_sink({"event": "user_message", "from": sender,
                        "text": message})
        except Exception:
            pass

    _CANVAS_TOOLS = ("render_html", "dashboard_render")

    def delegate(call_id, name, args):
        if name in _CANVAS_TOOLS:
            _a = args or {}
            html = _a.get("html", "")
            on_event({"event": "canvas", "html": html, "tool": name,
                      "append": bool(_a.get("append"))})
            return json.dumps({"rendered": True, "chars": len(html),
                               "appended": bool(_a.get("append"))},
                              ensure_ascii=False)
        if name == "alert":
            a = args or {}
            on_event({"event": "alert", "title": a.get("title", "🔔"),
                      "message": a.get("message", "")})
            return json.dumps({"alerted": True}, ensure_ascii=False)
        _send(server_url, token, my_priv, sender, sender_pub, T_CLIENT_TOOL_CALL,
              {"call_id": call_id, "tool": name, "args": args}, session_id)
        deadline = time.time() + 900
        while time.time() < deadline:
            if cancel_flag.is_set():
                return None
            try:
                resp = fed.server_fetch_inbox_types(server_url, token,
                                                    [T_TOOL_RESULT],
                                                    prefer_rest=True)
            except Exception:
                time.sleep(1.0)
                continue
            for e in resp.get("envelopes", []):
                if (e.get("meta") or {}).get("session_id") != session_id:
                    continue
                try:
                    d = crypto.unbox(my_priv, sender_pub, e["ciphertext"])
                except Exception:
                    continue
                pl = d.get("payload", {})
                if pl.get("call_id") == call_id:
                    return pl.get("result", "")
            time.sleep(1.0)
        raise TimeoutError("the originator did not return the tool in time")

    _delegated = ("render_html", "dashboard_render", "alert")
    try:
        with _orchestrating(emit=on_event, cancel=cancel_flag.is_set,
                            delegate=delegate, delegate_names=_delegated,
                            unattended=True, turn_id=session_id):
            result = _run_turn(
                user_message=message, max_iterations=max_iter,
                system=_session_system_for(message),
                allowed_tools=_session_tools_for(),
                on_event=on_event, cancel=cancel_flag.is_set,
                turn_id=session_id,
                qa=_incoming_qa_mode(),
                delegate=delegate, delegate_names=_delegated)
    finally:
        _unregister_cancel(session_id)
        if not outbox.close(timeout=60):
            print(f"[SESSION-OUT] outbox did not drain in 60s "
                  f"(session {session_id})", file=sys.stderr)
        outbox.report(session_id)
    return {"ok": True, "session_id": session_id, "result": result}


_INBOUND_SESSIONS = {}
_INBOUND_LOCK = threading.Lock()


def _register_inbound_session(peer, session_id, sender_pub):
    with _INBOUND_LOCK:
        _INBOUND_SESSIONS[peer] = {"session_id": session_id,
                                   "sender_pub": sender_pub}


def _unregister_inbound_session(peer):
    with _INBOUND_LOCK:
        _INBOUND_SESSIONS.pop(peer, None)


def has_inbound_session(peer):
    with _INBOUND_LOCK:
        return peer in _INBOUND_SESSIONS


def close_inbound_session(peer):
    with _INBOUND_LOCK:
        sess = _INBOUND_SESSIONS.get(peer)
    if not sess:
        return {"ok": False, "reason": f"no incoming session with '{peer}'"}
    session_id = sess["session_id"]
    sender_pub = sess["sender_pub"]
    try:
        store, server_url, token, my_priv, my_pub = _session_ctx()
        _send(server_url, token, my_priv, peer, sender_pub, T_EVENT,
              {"event": "session_closed", "by": "peer"}, session_id)
    except Exception:
        pass
    with _CANCELS_LOCK:
        cev = _CANCELS.get(session_id)
    if cev is not None:
        cev.set()
    _unregister_inbound_session(peer)
    return {"ok": True}


def inject_local_turn(peer, message, local_sink=None):
    with _INBOUND_LOCK:
        sess = _INBOUND_SESSIONS.get(peer)
    if not sess:
        return {"ok": False, "reason": f"no active incoming session with '{peer}'"}
    session_id = sess["session_id"]
    sender_pub = sess["sender_pub"]
    store, server_url, token, my_priv, my_pub = _session_ctx()
    my_handle = store.data.get("handle") or "?"

    cancel_flag = threading.Event()
    _register_cancel(session_id, cancel_flag)

    outbox = EventOutbox(lambda ev: _send(server_url, token, my_priv, peer,
                                          sender_pub, T_EVENT, ev, session_id))

    def on_event(ev):
        if local_sink:
            try:
                local_sink(ev)
            except Exception:
                pass
        outbox.put(dict(ev, t_exec=time.time()))

    try:
        _send(server_url, token, my_priv, peer, sender_pub, T_EVENT,
              {"event": "user_message", "from": my_handle, "text": message},
              session_id)
    except Exception:
        pass

    _CANVAS_TOOLS = ("render_html", "dashboard_render")

    def delegate(call_id, name, args):
        if name in _CANVAS_TOOLS:
            _a = args or {}
            html = _a.get("html", "")
            on_event({"event": "canvas", "html": html, "tool": name,
                      "append": bool(_a.get("append"))})
            return json.dumps({"rendered": True, "chars": len(html),
                               "appended": bool(_a.get("append"))},
                              ensure_ascii=False)
        if name == "alert":
            a = args or {}
            on_event({"event": "alert", "title": a.get("title", "🔔"),
                      "message": a.get("message", "")})
            return json.dumps({"alerted": True}, ensure_ascii=False)
        _send(server_url, token, my_priv, peer, sender_pub, T_CLIENT_TOOL_CALL,
              {"call_id": call_id, "tool": name, "args": args}, session_id)
        deadline = time.time() + 900
        while time.time() < deadline:
            if cancel_flag.is_set():
                return None
            try:
                resp = fed.server_fetch_inbox_types(server_url, token,
                                                    [T_TOOL_RESULT],
                                                    prefer_rest=True)
            except Exception:
                time.sleep(1.0)
                continue
            for e in resp.get("envelopes", []):
                if (e.get("meta") or {}).get("session_id") != session_id:
                    continue
                try:
                    d = crypto.unbox(my_priv, sender_pub, e["ciphertext"])
                except Exception:
                    continue
                pl = d.get("payload", {})
                if pl.get("call_id") == call_id:
                    return pl.get("result", "")
            time.sleep(1.0)
        raise TimeoutError("the originator did not return the tool in time")

    _delegated = ("render_html", "dashboard_render", "alert")
    try:
        with _orchestrating(emit=on_event, cancel=cancel_flag.is_set,
                            delegate=delegate, delegate_names=_delegated,
                            unattended=True, turn_id=session_id):
            result = _run_turn(
                user_message=message, max_iterations=None,
                system=_session_system_for(message),
                allowed_tools=_session_tools_for(),
                on_event=on_event, cancel=cancel_flag.is_set,
                turn_id=session_id,
                qa=_incoming_qa_mode(),
                delegate=delegate, delegate_names=_delegated)
    finally:
        _unregister_cancel(session_id)
        if not outbox.close(timeout=60):
            print(f"[SESSION-OUT] outbox did not drain in 60s "
                  f"(session {session_id})", file=sys.stderr)
        outbox.report(session_id)
    return {"ok": True, "session_id": session_id, "result": result}


_CANCELS = {}
_CANCELS_LOCK = threading.Lock()


def _register_cancel(session_id, ev):
    with _CANCELS_LOCK:
        _CANCELS[session_id] = ev


def _unregister_cancel(session_id):
    with _CANCELS_LOCK:
        _CANCELS.pop(session_id, None)


def handle_incoming_stop(env):
    session_id = (env.get("meta") or {}).get("session_id")
    if not session_id:
        return {"ok": False}
    with _CANCELS_LOCK:
        ev = _CANCELS.get(session_id)
    if ev is not None:
        ev.set()
        return {"ok": True, "cancelled": session_id}
    return {"ok": False, "reason": "session not active here"}
