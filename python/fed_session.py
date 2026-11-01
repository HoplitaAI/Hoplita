
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

TERMINAL_EVENTS = ("reply", "cancelled", "iteration_limit", "error",
                   "proxy_response", "proxy_end")

_store_getter = None
_run_turn = None
TIED_REQUEST = None
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


def _session_system_for(message, peer=""):
    if _session_system is None:
        return None
    try:
        me = (_session_ctx()[0].data or {}).get("handle", "")
    except Exception:
        me = ""
    own = bool(peer) and peer == me
    try:
        return _session_system(message, own=own)
    except TypeError:
        return None
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
    import fed_tools
    st = fed_tools._store()
    if handle and handle == st.data.get("handle"):
        return st.data.get("public_key_b64")
    pub, err = fed_tools._trusted_pub(server_url, token, handle)
    if not pub:
        raise RuntimeError(err)
    return pub


def _inbound_pubkey(server_url, token, handle):
    import fed_tools
    return fed_tools.inbound_contact_pub(server_url, token, handle)


def _send(server_url, token, my_priv, recipient, recipient_pub, rtype,
          payload, session_id, device=""):
    meta = {"session_id": session_id}
    if device:
        meta["device"] = device
    env = crypto.make_envelope(rtype, payload, meta=meta)
    blob = crypto.box_for(my_priv, recipient_pub, env)
    fed.server_send_envelope(server_url, token, recipient, rtype, blob,
                             meta=meta)



_LIVE = {}
_LIVE_LOCK = threading.Lock()


def _hand_over(session_id, env):
    with _LIVE_LOCK:
        owner = _LIVE.get(session_id)
    if owner is None:
        return False
    owner._parked.put(env)
    owner.wake()
    return True


def wake_all():
    with _LIVE_LOCK:
        live = list(_LIVE.values())
    for o in live:
        o.wake()


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
                 overall_timeout=1800, device="", tab="", extra=None):
        self.peer = peer_handle
        self.extra = dict(extra or {})
        self.device = (device or "").strip()
        self.tab = (tab or "").strip()
        _emit = on_event or (lambda ev: None)
        _emit_lock = threading.RLock()

        def _locked(ev):
            with _emit_lock:
                return _emit(ev)
        self.on_event = _locked
        self.poll_interval = poll_interval or self.RESCUE_POLL_S
        self.overall_timeout = overall_timeout
        self.session_id = uuid.uuid4().hex[:12]
        self._throttled = 0
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._done = threading.Event()
        self._opened = False
        self._parked = queue.Queue()
        self._live_q = queue.Queue()
        self._rx_lock = threading.Lock()
        self._seen = set()
        self._order = {}
        self._peer_pub = None

    def is_alive(self):
        return not self._done.is_set() and not self._stop.is_set()

    def send_turn(self, message, max_iterations=None):
        store, server_url, token, my_priv, my_pub = _session_ctx()
        peer_pub = _peer_pubkey(server_url, token, self.peer)
        turn_payload = {"message": message, "max_iterations": max_iterations,
                        "origin_pub": my_pub,
                        "live": fed.ws_live_ok()}
        if self.tab:
            turn_payload["tab"] = self.tab
        if self.extra:
            turn_payload.update(self.extra)
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
        self._wake.set()
        try:
            store, server_url, token, my_priv, my_pub = _session_ctx()
            peer_pub = _peer_pubkey(server_url, token, self.peer)
            send_live_stop(self.peer, peer_pub, my_priv, self.session_id)
            _send(server_url, token, my_priv, self.peer, peer_pub, T_STOP,
                  {"reason": "user_stop"}, self.session_id)
        except Exception:
            pass

    def start(self, message, max_iterations=None, persistent=False,
              should_stop=None):
        store, server_url, token, my_priv, my_pub = _session_ctx()
        peer_pub = _peer_pubkey(server_url, token, self.peer)
        self._peer_pub = peer_pub
        with _LIVE_LOCK:
            _LIVE[self.session_id] = self
        threading.Thread(target=self._pump, args=(my_priv,), daemon=True,
                         name=f"session-live-{self.session_id}").start()
        try:
            if should_stop is not None:
                threading.Thread(target=self._watch_stop, args=(should_stop,),
                                 daemon=True,
                                 name=f"session-stop-{self.session_id}").start()
            return self._listen(message, max_iterations, persistent,
                                server_url, token, my_priv, peer_pub)
        finally:
            with _LIVE_LOCK:
                if _LIVE.get(self.session_id) is self:
                    del _LIVE[self.session_id]

    def _watch_stop(self, should_stop):
        while not self._done.is_set() and not self._stop.is_set():
            try:
                if should_stop():
                    print(f"[SESSION-OUT] the waiting turn was stopped → "
                          f"stopping session={self.session_id} with "
                          f"{self.peer!r}", file=sys.stderr)
                    self.stop()
                    return
            except Exception:
                return
            self._done.wait(0.5)

    def _take_parked(self):
        out = []
        while True:
            try:
                out.append(self._parked.get_nowait())
            except queue.Empty:
                return out

    def _listen(self, message, max_iterations, persistent,
                server_url, token, my_priv, peer_pub):
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
            mine = self._take_parked()
            for e in envelopes:
                sid = (e.get("meta") or {}).get("session_id")
                if sid == self.session_id:
                    mine.append(e)
                else:
                    _hand_over(sid, e)
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
                if isinstance(ev, dict):
                    ev.setdefault("via", "mailbox")
                self._deliver(ev)
                if ev.get("event") == "session_closed":
                    self._flush_all()
                    self._done.set()
                    return ev
                if ev.get("event") in TERMINAL_EVENTS:
                    terminal = ev
                    got_terminal = True
                    if not persistent:
                        break
            if got_terminal:
                self._flush_all()
            if got_terminal and not persistent:
                self._done.set()
                return terminal
            self._wait_next()

        self._flush_all()
        if self._stop.is_set():
            terminal = {"event": "cancelled", "iterations": 0}
            self.on_event(terminal)
        self._done.set()
        return terminal

    def _pump(self, my_priv):
        while not self._done.is_set() and not self._stop.is_set():
            try:
                d = self._live_q.get(timeout=0.25)
            except queue.Empty:
                d = None
            if d is not None:
                try:
                    env = crypto.unbox(my_priv, self._peer_pub, d.get("box", ""))
                except Exception:
                    env = None
                if (env and env.get("type") == T_EVENT
                        and (env.get("meta") or {}).get("session_id") == self.session_id
                        and isinstance(env.get("payload"), dict)):
                    ev = env["payload"]
                    ev.setdefault("t_recv", time.time())
                    ev["via"] = "live"
                    self._deliver(ev)
            self._expire_gaps()

    def _deliver(self, ev):
        lt, seq = (ev.get("lt"), ev.get("seq")) if isinstance(ev, dict) else (None, None)
        with self._rx_lock:
            if not lt or not isinstance(seq, int):
                self.on_event(ev)
                return True
            if (lt, seq) in self._seen:
                return False
            self._seen.add((lt, seq))
            st = self._order.setdefault(lt, {"next": 1, "held": {}, "since": None})
            if seq < st["next"]:
                self.on_event(ev)
                return True
            st["held"][seq] = ev
            self._release(st)
            return True

    def _release(self, st):
        while st["next"] in st["held"]:
            self.on_event(st["held"].pop(st["next"]))
            st["next"] += 1
        st["since"] = (st["since"] or time.monotonic()) if st["held"] else None

    def _expire_gaps(self):
        now = time.monotonic()
        with self._rx_lock:
            for st in self._order.values():
                if st["held"] and st["since"] and now - st["since"] > LIVE_GAP_S:
                    st["next"] = min(st["held"])
                    st["since"] = None
                    self._release(st)

    def _flush_all(self):
        with self._rx_lock:
            for st in self._order.values():
                while st["held"]:
                    st["next"] = min(st["held"])
                    self._release(st)

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



EPHEMERAL_EVENTS = frozenset(("iteration", "llm_call", "llm_done", "tokens",
                              "tool_progress", "tick", "prep"))

LIVE_GAP_S = 1.5

_LIVE_REFUSED = {}
_LIVE_REFUSED_S = 300


def live_refused(data):
    to = str((data or {}).get("to") or "").lower()
    if to and (data or {}).get("error") != "live_throttled":
        _LIVE_REFUSED[to] = time.monotonic() + _LIVE_REFUSED_S


class LiveTx:

    def __init__(self, my_priv, peer, peer_pub, session_id, peer_has_live):
        self.my_priv = my_priv
        self.peer = peer
        self.peer_pub = peer_pub
        self.session_id = session_id
        self.enabled = bool(peer_has_live)
        self.lt = uuid.uuid4().hex[:10]
        self.seq = 0
        self.live = 0
        self.skipped = 0
        self._lock = threading.Lock()

    def stamp(self, ev):
        with self._lock:
            self.seq += 1
            return dict(ev, lt=self.lt, seq=self.seq)

    def send(self, ev):
        if not self.enabled:
            return False
        if _LIVE_REFUSED.get(self.peer.lower(), 0) > time.monotonic():
            return False
        limit = fed.ws_live_max()
        if not limit:
            return False
        try:
            env = crypto.make_envelope(T_EVENT, ev,
                                       meta={"session_id": self.session_id})
            blob = crypto.box_for(self.my_priv, self.peer_pub, env)
        except Exception:
            return False
        data = {"k": T_EVENT, "sid": self.session_id, "box": blob}
        if len(blob) + 120 > limit:
            return False
        if fed.ws_live(self.peer, data):
            self.live += 1
            return True
        return False

    def route(self, ev, outbox):
        went = self.send(ev)
        if went and ev.get("event") in EPHEMERAL_EVENTS:
            self.skipped += 1
            return
        outbox.put(ev)


def send_live_stop(peer, peer_pub, my_priv, session_id):
    if not fed.ws_live_ok():
        return False
    try:
        env = crypto.make_envelope(T_STOP, {"reason": "user_stop"},
                                   meta={"session_id": session_id})
        blob = crypto.box_for(my_priv, peer_pub, env)
    except Exception:
        return False
    return fed.ws_live(peer, {"k": T_STOP, "sid": session_id, "box": blob})


def on_live(data):
    data = data or {}
    sender = str(data.get("from") or "")
    d = data.get("data") or {}
    if not isinstance(d, dict):
        return
    kind, sid = d.get("k"), d.get("sid")
    if kind == T_EVENT:
        with _LIVE_LOCK:
            owner = _LIVE.get(sid)
        if owner is not None and owner.peer.lower() == sender.lower():
            owner._live_q.put(d)
            owner.wake()
    elif kind == T_STOP:
        with _INBOUND_LOCK:
            sess = _INBOUND_SESSIONS.get(sender)
        if not sess or sess.get("session_id") != sid:
            return
        try:
            store, _url, _tok, my_priv, _pub = _session_ctx()
            env = crypto.unbox(my_priv, sess["sender_pub"], d.get("box", ""))
        except Exception:
            return
        if env.get("type") != T_STOP or (env.get("meta") or {}).get("session_id") != sid:
            return
        with _CANCELS_LOCK:
            ev = _CANCELS.get(sid)
        if ev is not None:
            print(f"[SESSION-IN] stop by the live lane · session={sid}",
                  file=sys.stderr)
            ev.set()


def handle_incoming_turn(env, get_trust, approver=None, local_sink=None):
    store, server_url, token, my_priv, my_pub = _session_ctx()
    session_id = (env.get("meta") or {}).get("session_id") or uuid.uuid4().hex[:12]
    sender = env.get("sender", "")

    def _refused_here(why):
        if local_sink is not None:
            try:
                local_sink({"event": "session_refused", "why": why})
            except Exception:
                pass

    def _decline_to_server_key(text):
        try:
            _raw = fed.server_get_key(server_url, token, sender).get("public_key")
            if _raw:
                _send(server_url, token, my_priv, sender, _raw, T_EVENT,
                      {"event": "error", "code": "declined", "retryable": False,
                       "error": text}, session_id)
        except Exception as e:
            print(f"[SESSION-IN] the decline to {sender!r} could not be sent: {e}",
                  file=sys.stderr)

    try:
        if sender and sender == store.data.get("handle"):
            sender_pub = my_pub
        else:
            sender_pub = _inbound_pubkey(server_url, token, sender)
    except Exception as e:
        _decline_to_server_key(f"'{store.data.get('handle', '')}' does not take "
                               f"sessions from you: not a contact of theirs.")
        _refused_here("not_contact")
        return {"ok": False, "reason": f"no key for '{sender}': {e}"}

    try:
        dec = crypto.unbox(my_priv, sender_pub, env["ciphertext"])
    except Exception:
        _decline_to_server_key(f"'{store.data.get('handle', '')}' could not verify "
                               f"that this request comes from you: the key it holds "
                               f"for you does not match. If you reinstalled or changed "
                               f"keys, they need to accept you again.")
        _refused_here("unverified")
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
        except Exception as e:
            print(f"[SESSION-IN] the decline to {sender!r} could not be sent: {e}",
                  file=sys.stderr)
        _refused_here("no_permission" if trust == "reject" else "not_contact")
        return {"ok": False, "reason": f"'{sender}' has no session permission"}
    _first = not has_inbound_session(sender)

    def _ack(state):
        try:
            _send(server_url, token, my_priv, sender, sender_pub, T_EVENT,
                  {"event": "session_ack", "state": state,
                   "by": store.data.get("handle", "")}, session_id)
        except Exception as e:
            print(f"[SESSION-IN] could not send the {state} ack to {sender!r}: {e}",
                  file=sys.stderr)

    if trust != "open" and _session_approved(sender, session_id):
        trust = "open"
    if trust != "open":
        approved = False
        if approver is not None:
            _ack("awaiting_approval")
            try:
                answer = approver(sender, message, trust == "analyze",
                                  session_id=session_id)
            except Exception:
                answer = False
            if answer == "timeout":
                mins = max(1, int(getattr(sys.modules.get("fed_tools"),
                                          "APPROVAL_TIMEOUT_S", 300)) // 60)
                _send(server_url, token, my_priv, sender, sender_pub, T_EVENT,
                      {"event": "error", "code": "no_answer", "retryable": False,
                       "error": f"'{store.data['handle']}' did not answer in "
                                f"{mins} minutes: nobody accepted or refused it. "
                                f"Send it again when they are there."},
                      session_id)
                return {"ok": False, "reason": "nobody answered in time"}
            if answer is None:
                print(f"[SESSION-IN] {sender!r} withdrew session={session_id} "
                      f"before it was approved", file=sys.stderr)
                return {"ok": False, "reason": "withdrawn by the originator"}
            approved = bool(answer)
            if approved:
                _remember_approval(sender, session_id)
        if not approved:
            _send(server_url, token, my_priv, sender, sender_pub, T_EVENT,
                  {"event": "error", "code": "declined", "retryable": False,
                   "error": f"'{store.data['handle']}' rejected the task "
                            f"(trust '{trust}', not approved)."},
                  session_id)
            return {"ok": False, "reason": "not approved by the gate"}
    if _first:
        _ack("accepted")

    cancel_flag = threading.Event()
    _register_cancel(session_id, cancel_flag)
    _new_session = not has_inbound_session(sender)
    _peer_live = bool((payload or {}).get("live"))
    _register_inbound_session(sender, session_id, sender_pub, live=_peer_live)

    _tab = str((payload or {}).get("tab") or "").strip()
    _own = bool(sender and sender == store.data.get("handle"))
    _treq = (payload or {}).get("tied_request") if _own else None
    if not isinstance(_treq, dict):
        _treq = None
    _paint = _treq is None or _treq.get("path") == "/agent/turn"

    outbox = EventOutbox(lambda ev: _send(server_url, token, my_priv, sender,
                                          sender_pub, T_EVENT, ev, session_id))
    live = LiveTx(my_priv, sender, sender_pub, session_id, _peer_live)

    def on_event(ev):
        if local_sink and (_paint or ev.get("event") == "tab_saved"):
            try:
                local_sink(dict(ev, tab=_tab) if _tab else ev)
            except Exception:
                pass
        live.route(live.stamp(dict(ev, t_exec=time.time())), outbox)

    if local_sink and _paint:
        def _sink(ev):
            local_sink(dict(ev, tab=_tab) if _tab else ev)
        try:
            if _new_session:
                _sink({"event": "session_open", "peer": sender,
                       "session_id": session_id})
            _sink({"event": "user_message", "from": sender, "text": message})
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

    if _own and (payload or {}).get("tied_turn") is not None:
        on_event({"event": "error", "code": "update", "retryable": False,
                  "error": "this tab is tied to a machine running a newer "
                           "HOPLITA: update this one to use it"})
        _unregister_cancel(session_id)
        outbox.close(timeout=30)
        return {"ok": False, "reason": "tied turn from an older version"}
    if _treq is not None and TIED_REQUEST is not None:
        print(f"[SESSION-IN] tied {_treq.get('method', '?')} {_treq.get('path', '?')} "
              f"· session={session_id}", file=sys.stderr)
        try:
            result = TIED_REQUEST(_treq, on_event, cancel_flag)
        finally:
            _unregister_cancel(session_id)
            if not outbox.close(timeout=60):
                print(f"[SESSION-OUT] outbox did not drain in 60s "
                      f"(session {session_id})", file=sys.stderr)
            outbox.report(session_id)
            _report_live(live)
        return {"ok": True, "session_id": session_id, "result": result}

    _delegated = ("render_html", "dashboard_render", "alert")
    try:
        with _orchestrating(emit=on_event, cancel=cancel_flag.is_set,
                            delegate=delegate, delegate_names=_delegated,
                            unattended=True, turn_id=session_id):
            result = _run_turn(
                user_message=message, max_iterations=max_iter,
                system=_session_system_for(message, sender),
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
        _report_live(live)
    return {"ok": True, "session_id": session_id, "result": result}


def _report_live(live):
    if live.live:
        print(f"[SESSION-OUT] live lane: {live.live} of {live.seq} event(s), "
              f"{live.skipped} of them only live (session {live.session_id})",
              file=sys.stderr)


_INBOUND_SESSIONS = {}
_INBOUND_LOCK = threading.Lock()


_APPROVED_SESSIONS = {}
APPROVAL_IDLE_S = 30 * 60


def _remember_approval(peer, session_id):
    with _INBOUND_LOCK:
        _APPROVED_SESSIONS[session_id] = (peer, time.time())


def _session_approved(peer, session_id):
    with _INBOUND_LOCK:
        hit = _APPROVED_SESSIONS.get(session_id)
        if not hit or hit[0] != peer:
            return False
        if time.time() - hit[1] > APPROVAL_IDLE_S:
            _APPROVED_SESSIONS.pop(session_id, None)
            return False
        _APPROVED_SESSIONS[session_id] = (peer, time.time())
        return True


def _forget_approvals(peer):
    with _INBOUND_LOCK:
        for sid in [k for k, v in _APPROVED_SESSIONS.items() if v[0] == peer]:
            _APPROVED_SESSIONS.pop(sid, None)


def _register_inbound_session(peer, session_id, sender_pub, live=False):
    with _INBOUND_LOCK:
        _INBOUND_SESSIONS[peer] = {"session_id": session_id,
                                   "sender_pub": sender_pub, "live": live}


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
    _forget_approvals(peer)
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
    live = LiveTx(my_priv, peer, sender_pub, session_id, sess.get("live"))

    def on_event(ev):
        if local_sink:
            try:
                local_sink(ev)
            except Exception:
                pass
        live.route(live.stamp(dict(ev, t_exec=time.time())), outbox)

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
                system=_session_system_for(message, peer),
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
        _report_live(live)
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
