
import json
import random
import re
import threading
import time
import uuid

import requests
try:
    import websocket
except ImportError:
    websocket = None

import hoplita_crypto as crypto



def build_identity_store(base_storage_cls):
    class FederationIdentityStore(base_storage_cls):
        DEFAULT_FILENAME = "fed_identity.json"
        DEFAULT_DATA = {
            "private_key_b64": None,
            "public_key_b64": None,
            "sign_priv_b64": None,
            "sign_pub_b64": None,
            "handle": None,
            "fingerprint": None,
            "server_url": None,
            "token": None,
            "pinned_keys": {},
        }
        LOG_LABEL = "FedIdentity"

        def has_identity(self):
            return bool(self.data.get("private_key_b64"))

        def set_identity(self, priv_b64, pub_b64, handle, server_url):
            self.data["private_key_b64"] = priv_b64
            self.data["public_key_b64"] = pub_b64
            self.data["handle"] = handle
            self.data["fingerprint"] = crypto.fingerprint(pub_b64)
            self.data["server_url"] = server_url
            self.save_data()

        def ensure_signing_keys(self):
            if not self.data.get("sign_priv_b64"):
                priv, pub = crypto.generate_signing_identity()
                self.data["sign_priv_b64"] = priv
                self.data["sign_pub_b64"] = pub
                self.save_data()
            return self.data["sign_priv_b64"], self.data["sign_pub_b64"]

        def set_token(self, token):
            self.data["token"] = token
            self.save_data()

        def clear_identity(self):
            import copy
            self.data = copy.deepcopy(self.DEFAULT_DATA)
            self.save_data()

    return FederationIdentityStore



class FedServerError(Exception):
    def __init__(self, message, status=None, data=None):
        super().__init__(message)
        self.status = status
        self.data = data or {}
        self.retry_after = None


def _api(server_url):
    return server_url.rstrip("/") + "/api/fed"


def _post(server_url, path, payload, token=None, timeout=15):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Token {token}"
    try:
        r = requests.post(f"{_api(server_url)}/{path}", json=payload,
                          headers=headers, timeout=timeout)
    except requests.exceptions.RequestException as e:
        raise FedServerError(f"Could not connect to server: {e}",
                             data={"error": "offline"})
    if r.status_code not in (200, 201):
        _raise_from(r)
    return r.json()


def _get(server_url, path, token=None, timeout=15):
    headers = {}
    if token:
        headers["Authorization"] = f"Token {token}"
    try:
        r = requests.get(f"{_api(server_url)}/{path}", headers=headers,
                         timeout=timeout)
    except requests.exceptions.RequestException as e:
        raise FedServerError(f"Could not connect to server: {e}",
                             data={"error": "offline"})
    if r.status_code not in (200, 201):
        _raise_from(r)
    return r.json()


def _raise_from(r):
    data = {}
    try:
        data = r.json()
        msg = (data.get("message") or data.get("error")
               or data.get("detail") or json.dumps(data))
    except Exception:
        body = (r.text or "").strip()
        if body[:1] == "<" or "<html" in body[:300].lower():
            msg = ("the server broke while handling this and did not say why. "
                   "Nothing you did caused it and nothing was lost: try again "
                   "in a moment, and if it keeps happening, whoever runs the "
                   "server has to look at it")
        else:
            msg = body[:200]
    retry = None
    if r.status_code == 429:
        try:
            retry = float(r.headers.get("Retry-After", "") or 0) or None
        except (TypeError, ValueError):
            retry = None
        if retry is None:
            m = re.search(r"(\d+)\s*second", str(msg))
            retry = float(m.group(1)) if m else None
    err = FedServerError(f"[{r.status_code}] {msg}",
                         status=r.status_code, data=data)
    err.retry_after = retry
    raise err




class FedWS:

    def __init__(self):
        self._ws = None
        self._lock = threading.Lock()
        self._pending = {}
        self._on_event = None
        self._url = None
        self._token = None
        self._stop = threading.Event()
        self._thread = None
        self.authenticated = False
        self.state = {"phase": "idle", "error": "", "since": 0.0}
        self._said = ""

    def _set_state(self, phase, error=""):
        import sys as _s
        self.state = {"phase": phase, "error": str(error or ""),
                      "since": time.time()}
        key = f"{phase}|{error}"
        if phase in ("no_lib", "auth_failed", "down") and key != self._said:
            self._said = key
            print(f"[FED-WS] socket {phase}"
                  + (f": {error}" if error else "")
                  + " — operations fall back to REST; contacts see you "
                    "OFFLINE until it is back", file=_s.stderr)

    def configure(self, server_url, token, on_event=None):
        new_url = server_url.rstrip("/")
        changed = (new_url != self._url) or (token != self._token)
        self._url = new_url
        self._token = token
        if on_event is not None:
            self._on_event = on_event
        self._stop.clear()
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name="fed-ws")
            self._thread.start()
        elif changed:
            self.authenticated = False
            with self._lock:
                ws = self._ws
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass

    def stop(self):
        self._stop.set()
        with self._lock:
            ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def is_up(self):
        return self.authenticated and self._ws is not None

    def _run(self):
        if websocket is None:
            self._set_state("no_lib",
                            "python package 'websocket-client' is not "
                            "installed (pip install websocket-client)")
            return
        backoff = 1.0
        while not self._stop.is_set():
            up_at = 0.0
            try:
                ws_url = self._url.replace("https://", "wss://").replace(
                    "http://", "ws://") + "/ws/fed/"
                ws = websocket.create_connection(ws_url, timeout=10)
                ws.settimeout(70)
                with self._lock:
                    self._ws = ws
                rid = uuid.uuid4().hex
                ws.send(json.dumps({"op": "auth", "token": self._token,
                                    "req_id": rid}))
                first = json.loads(ws.recv())
                if not first.get("ok"):
                    self._set_state("auth_failed",
                                    str(first.get("error") or first)[:200])
                    self.authenticated = False
                    try:
                        ws.close()
                    except Exception:
                        pass
                    with self._lock:
                        self._ws = None
                    time.sleep(_jittered(backoff)); backoff = min(backoff * 2, 30)
                    continue
                self.authenticated = True
                self._set_state("up")
                up_at = time.monotonic()
                if self._on_event:
                    try:
                        self._on_event("ws.connected", {})
                    except Exception:
                        pass
                last_rx = time.monotonic()
                while not self._stop.is_set():
                    try:
                        raw = ws.recv()
                        last_rx = time.monotonic()
                    except websocket.WebSocketTimeoutException:
                        if time.monotonic() - last_rx > 210:
                            break
                        try:
                            ws.send(json.dumps({"op": "ping",
                                                "req_id": uuid.uuid4().hex}))
                            continue
                        except Exception:
                            break
                    if not raw:
                        break
                    msg = json.loads(raw)
                    if "req_id" in msg and msg["req_id"] in self._pending:
                        slot = self._pending[msg["req_id"]]
                        slot["resp"] = msg
                        slot["event"].set()
                    elif "event" in msg and self._on_event:
                        try:
                            self._on_event(msg["event"], msg.get("data") or {})
                        except Exception:
                            pass
            except Exception as e:
                if self.state.get("phase") not in ("auth_failed",):
                    self._set_state("down", repr(e)[:200])
            else:
                if self.state.get("phase") == "up":
                    self._set_state("down", "connection closed")
            finally:
                self.authenticated = False
                with self._lock:
                    self._ws = None
            if not self._stop.is_set():
                if up_at and time.monotonic() - up_at >= 60:
                    backoff = 1.0
                time.sleep(_jittered(backoff))
                backoff = min(backoff * 2, 30)

    def request(self, op, params=None, timeout=15):
        with self._lock:
            ws = self._ws
        if ws is None or not self.authenticated:
            raise FedServerError("[WS] socket unavailable")
        rid = uuid.uuid4().hex
        slot = {"event": threading.Event(), "resp": None}
        self._pending[rid] = slot
        try:
            try:
                ws.send(json.dumps({"op": op, "req_id": rid,
                                    "params": params or {}}))
            except Exception as e:
                self.authenticated = False
                with self._lock:
                    if self._ws is ws:
                        self._ws = None
                try:
                    ws.close()
                except Exception:
                    pass
                raise FedServerError(f"[WS] send failed: {e}")
            if not slot["event"].wait(timeout):
                raise FedServerError("[WS] timeout waiting for response")
            resp = slot["resp"]
        finally:
            self._pending.pop(rid, None)
        if not resp.get("ok"):
            status = resp.get("status", 500)
            msg = resp.get("message") or resp.get("error", "error")
            err = FedServerError(f"[{status}] {msg}", status=status, data=resp)
            try:
                ra = resp.get("retry_after")
                err.retry_after = int(ra) if ra else None
            except (TypeError, ValueError):
                err.retry_after = None
            raise err
        data = resp.get("data") or {}
        if isinstance(data, dict) and data.get("use_rest"):
            raise FedServerError("[WS] response too large: use REST")
        return data


def _jittered(backoff):
    return min(backoff, 30) * random.uniform(0.5, 1.0)


_WS = FedWS()


def ws_configure(server_url, token, on_event=None):
    _WS.configure(server_url, token, on_event)


def ws_stop():
    _WS.stop()


def ws_state():
    s = dict(_WS.state)
    s["up"] = _WS.is_up()
    return s


def ws_is_up():
    return _WS.is_up()


def _op(server_url, token, op, params, rest_fn, timeout=15):
    if _WS.is_up() and not _too_big_for_ws(params):
        try:
            return _WS.request(op, params, timeout=timeout)
        except FedServerError as e:
            if "[WS]" not in str(e):
                raise
    return rest_fn()


_WS_MAX_PARAMS = 500_000


def _too_big_for_ws(params):
    try:
        total = 0
        for v in (params or {}).values():
            if isinstance(v, str):
                total += len(v)
            elif isinstance(v, (list, dict)):
                total += len(json.dumps(v, default=str))
            if total > _WS_MAX_PARAMS:
                return True
        return False
    except Exception:
        return False


def server_register(server_url, handle, email, public_key, fingerprint,
                    invite="", sign_pub=""):
    body = {"handle": handle, "email": email,
            "public_key": public_key, "fingerprint": fingerprint}
    if invite:
        body["invite"] = invite
    if sign_pub:
        body["sign_pub"] = sign_pub
    return _post(server_url, "register", body)


def server_publish_sign_key(server_url, token, sign_pub):
    params = {"sign_pub": sign_pub}
    return _op(server_url, token, "publish_sign_key", params,
               lambda: _post(server_url, "signkey", params, token=token))


def server_attest_contact(server_url, token, handle, attestation):
    params = {"handle": handle, "attestation": attestation}
    return _op(server_url, token, "attest_contact", params,
               lambda: _post(server_url, "contacts/attest", params,
                             token=token))


def server_grant_folder(server_url, token, folder_id, peer_handle, grant):
    params = {"folder_id": folder_id, "peer_handle": peer_handle,
              "grant": grant}
    return _op(server_url, token, "grant_folder", params,
               lambda: _post(server_url, "folders/grant", params,
                             token=token))


def server_verify(server_url, handle, pin):
    return _post(server_url, "verify", {"handle": handle, "pin": pin})


def server_login(server_url, handle, priv_b64):
    import hoplita_crypto as crypto
    ch = _post(server_url, "login/challenge", {"handle": handle})
    blob = ch.get("challenge")
    if not blob:
        raise FedServerError("The server did not return a login challenge")
    try:
        opened = crypto.unseal(priv_b64, blob)
        nonce = opened.get("nonce")
    except Exception as e:
        raise FedServerError(f"Could not decrypt the challenge (wrong key?): {e}")
    return _post(server_url, "login/verify", {"handle": handle, "nonce": nonce})


def server_heartbeat(server_url, token):
    return _post(server_url, "heartbeat", {}, token=token)



def server_add_contact(server_url, token, handle, trust_level="accept",
                       attestation=""):
    payload = {"handle": handle, "trust_level": trust_level}
    if attestation:
        payload["attestation"] = attestation
    return _op(server_url, token, "add_contact", payload,
               lambda: _post(server_url, "contacts/add", payload,
                             token=token))


def server_accept_contact(server_url, token, handle, trust_level=None,
                          attestation=""):
    payload = {"handle": handle}
    if trust_level:
        payload["trust_level"] = trust_level
    if attestation:
        payload["attestation"] = attestation
    return _op(server_url, token, "accept_contact", payload,
               lambda: _post(server_url, "contacts/accept", payload,
                             token=token))


def server_list_contacts(server_url, token):
    return _op(server_url, token, "list_contacts", {},
               lambda: _get(server_url, "contacts", token=token))


def server_set_permissions(server_url, token, handle, resource_type, perms):
    params = {"handle": handle, "resource_type": resource_type, "perms": perms}
    return _op(server_url, token, "set_permissions", params,
               lambda: _post(server_url, "contacts/permissions", params,
                             token=token))


def server_get_key(server_url, token, handle):
    return _op(server_url, token, "get_key", {"handle": handle},
               lambda: _get(server_url, f"key/{handle}", token=token))



def server_create_spawn_token(server_url, token, label=""):
    return _post(server_url, "spawn/create", {"label": label}, token=token)


def server_redeem_spawn_token(server_url, spawn_token, handle, public_key,
                              fingerprint=""):
    return _post(server_url, "spawn/redeem", {
        "token": spawn_token, "handle": handle, "public_key": public_key,
        "fingerprint": fingerprint})


def server_list_children(server_url, token):
    return _get(server_url, "children", token=token)


def server_remove_contact(server_url, token, handle):
    return _op(server_url, token, "remove_contact", {"handle": handle},
               lambda: _post(server_url, "contacts/remove",
                             {"handle": handle}, token=token))


def server_revoke_child(server_url, token, handle):
    return _post(server_url, "children/revoke", {"handle": handle}, token=token)


def server_deregister(server_url, token):
    return _post(server_url, "deregister", {}, token=token)



def server_send_envelope(server_url, token, recipient, resource_type, ciphertext,
                         meta=None):
    payload = {"recipient": recipient, "resource_type": resource_type,
               "ciphertext": ciphertext}
    if meta:
        payload["meta"] = meta
    return _op(server_url, token, "send_envelope", payload,
               lambda: _post(server_url, "envelope/send", payload,
                             token=token))


_INBOX_MAX_PAGES = 64

_BODY_CACHE = {}
_BODY_ORDER = []
_BODY_BYTES = [0]
_BODY_CACHE_MAX = 64 * 1024 * 1024
_BODY_LOCK = threading.Lock()


def _body_cache_put(server_url, env):
    key = (server_url, env.get("id"))
    size = len(env.get("ciphertext") or "")
    with _BODY_LOCK:
        if key in _BODY_CACHE or size > _BODY_CACHE_MAX // 4:
            return
        _BODY_CACHE[key] = env
        _BODY_ORDER.append(key)
        _BODY_BYTES[0] += size
        while _BODY_BYTES[0] > _BODY_CACHE_MAX and _BODY_ORDER:
            old = _BODY_ORDER.pop(0)
            gone = _BODY_CACHE.pop(old, None)
            if gone is not None:
                _BODY_BYTES[0] -= len(gone.get("ciphertext") or "")


def _body_cache_get(server_url, env_id):
    with _BODY_LOCK:
        return _BODY_CACHE.get((server_url, env_id))


_HEADS_SUPPORTED = {}


def invalidate_inbox():
    m = _memo()
    if m is not None:
        for k in [k for k in m if k[0] == "heads"]:
            m.pop(k, None)


def pass_inbox_look(server_url, token):
    if _memo() is None or _HEADS_SUPPORTED.get(server_url) is False:
        return None
    try:
        _inbox_heads(server_url, token)
    except Exception:
        return None
    return pass_inbox_types(server_url, token)


def pass_inbox_types(server_url, token):
    m = _memo()
    snap = m.get(("heads", server_url, token, "")) if m is not None else None
    if not snap or snap.get("truncated"):
        return None
    return {h.get("resource_type") for h in snap["heads"]}


def _inbox_heads(server_url, token, prefer_rest=False):
    m = _memo()
    key = ("heads", server_url, token, "")
    if m is not None and key in m:
        return m[key]

    def _page(after):
        params = {"types": [], "peek": "1", "bodies": "0", "paged": "1"}
        url = "inbox?peek=1&bodies=0&paged=1"
        if after:
            params["after"] = after
            url += f"&after={after}"
        if prefer_rest:
            return _get(server_url, url, token=token)
        return _op(server_url, token, "fetch_inbox", params,
                   lambda: _get(server_url, url, token=token))

    resp = _page(0)
    if resp.get("bodies") is not False:
        _HEADS_SUPPORTED[server_url] = False
        return None
    heads = list(resp.get("envelopes") or [])
    pages = 1
    while resp.get("more") and resp.get("next_after") and pages < _INBOX_MAX_PAGES:
        resp = _page(resp["next_after"])
        heads.extend(resp.get("envelopes") or [])
        pages += 1
    snap = {"heads": heads,
            "truncated": bool(resp.get("more") and pages >= _INBOX_MAX_PAGES)}
    if m is not None:
        m[key] = snap
    return snap


def server_fetch_inbox_types(server_url, token, types, peek=False, prefer_rest=False):
    def _page(after, extra=None):
        params = {"types": list(types), "peek": "1" if peek else "",
                  "paged": "1"}
        params.update(extra or {})
        if after:
            params["after"] = after

        def _rest():
            url = f"inbox?types={','.join(types)}&paged=1"
            if peek:
                url += "&peek=1"
            for k, v in (extra or {}).items():
                url += f"&{k}={v}"
            if after:
                url += f"&after={after}"
            return _get(server_url, url, token=token)
        if prefer_rest:
            return _rest()
        return _op(server_url, token, "fetch_inbox", params, _rest)

    def _walk(extra=None):
        resp = _page(0, extra)
        envs = list(resp.get("envelopes") or [])
        pages = 1
        while resp.get("more") and resp.get("next_after") and pages < _INBOX_MAX_PAGES:
            resp = _page(resp["next_after"], extra)
            envs.extend(resp.get("envelopes") or [])
            pages += 1
        truncated = bool(resp.get("more") and pages >= _INBOX_MAX_PAGES)
        return resp, envs, truncated

    if not peek:
        _last, envelopes, truncated = _walk()
        invalidate_inbox()
        return {"envelopes": envelopes, "count": len(envelopes),
                "more": truncated}

    snap = None
    if _HEADS_SUPPORTED.get(server_url) is not False:
        snap = _inbox_heads(server_url, token, prefer_rest)
    if snap is None:
        _last, heads, truncated = _walk()
        for e in heads:
            if e.get("ciphertext") is not None:
                _body_cache_put(server_url, e)
        return {"envelopes": heads, "count": len(heads), "more": truncated}
    want = set(types)
    heads = [h for h in snap["heads"] if h.get("resource_type") in want]
    truncated = snap["truncated"]
    if not heads:
        return {"envelopes": [], "count": 0, "more": truncated}
    missing = [h["id"] for h in heads
               if _body_cache_get(server_url, h.get("id")) is None]
    fresh = {}
    for i in range(0, len(missing), 200):
        chunk = missing[i:i + 200]
        _r, got, _t = _walk({"ids": ",".join(str(x) for x in chunk)})
        for e in got:
            fresh[e.get("id")] = e
            _body_cache_put(server_url, e)
    out = []
    for h in heads:
        e = fresh.get(h.get("id")) or _body_cache_get(server_url, h.get("id"))
        if e is not None:
            out.append(e)
    return {"envelopes": out, "count": len(out), "more": truncated}



def server_publish_folder(server_url, token, folder_id, folder_name,
                          kind="folder", attestation=""):
    params = {"folder_id": folder_id, "folder_name": folder_name,
              "kind": kind}
    if attestation:
        params["attestation"] = attestation
    return _op(server_url, token, "publish_folder", params,
               lambda: _post(server_url, "folders/publish", params,
                             token=token))


def server_unpublish_folder(server_url, token, folder_id):
    params = {"folder_id": folder_id}
    return _op(server_url, token, "unpublish_folder", params,
               lambda: _post(server_url, "folders/unpublish", params,
                             token=token))


def server_public_folders(server_url, token, handle):
    params = {"handle": handle}
    return _op(server_url, token, "public_folders", params,
               lambda: _get(server_url,
                            f"public-folders?handle={handle}", token=token))


def server_subscribe_folder(server_url, token, handle, folder_id):
    params = {"handle": handle, "folder_id": folder_id}
    return _op(server_url, token, "subscribe_folder", params,
               lambda: _post(server_url, "folders/subscribe", params,
                             token=token))


def server_unsubscribe_folder(server_url, token, handle, folder_id):
    params = {"handle": handle, "folder_id": folder_id}
    return _op(server_url, token, "unsubscribe_folder", params,
               lambda: _post(server_url, "folders/unsubscribe", params,
                             token=token))



def server_unshare_folder(server_url, token, folder_id, peer_handle=None):
    params = {"folder_id": folder_id}
    if peer_handle:
        params["peer_handle"] = peer_handle
    return _op(server_url, token, "unshare_folder", params,
               lambda: _post(server_url, "folders/unshare", params,
                             token=token))


def server_ack_envelopes(server_url, token, ids):
    return _op(server_url, token, "ack_envelopes", {"ids": list(ids)},
               lambda: _post(server_url, "inbox/ack", {"ids": list(ids)},
                             token=token))


def server_share_folder(server_url, token, handle, folder_id, folder_name,
                        kind="folder", grant=""):
    params = {"handle": handle, "folder_id": folder_id,
              "folder_name": folder_name, "kind": kind}
    if grant:
        params["grant"] = grant
    return _op(server_url, token, "share_folder", params,
               lambda: _post(server_url, "folders/share", params,
                             token=token))


def server_accept_folder(server_url, token, handle, folder_id):
    params = {"handle": handle, "folder_id": folder_id}
    return _op(server_url, token, "accept_folder", params,
               lambda: _post(server_url, "folders/accept", params,
                             token=token))


def server_list_folders(server_url, token):
    return _op(server_url, token, "list_folders", {},
               lambda: _get(server_url, "folders", token=token))

def server_sync_pull(server_url, token):
    return _op(server_url, token, "sync_pull", {},
               lambda: _get(server_url, "sync/pull", token=token))


def server_sync_push(server_url, token, ciphertext, base_version):
    params = {"ciphertext": ciphertext, "base_version": int(base_version)}
    return _op(server_url, token, "sync_push", params,
               lambda: _post(server_url, "sync/push", params, token=token))


def server_motd(server_url, token):
    return _op(server_url, token, "motd", {},
               lambda: _get(server_url, "motd", token=token))


def server_notices(server_url, token):
    return _op(server_url, token, "notices", {},
               lambda: _get(server_url, "notices", token=token))


def server_withdraw(server_url, token, ids=None, resource_type="",
                    recipient=""):
    body = {}
    if ids:
        body["ids"] = list(ids)
    if resource_type:
        body["resource_type"] = resource_type
    if recipient:
        body["recipient"] = recipient
    return _post(server_url, "outbox/withdraw", body, token=token)


def server_quota(server_url, token):
    return _op(server_url, token, "quota", {},
               lambda: _get(server_url, "quota", token=token))


_PASS = threading.local()


class sync_pass:

    def __enter__(self):
        _PASS.depth = getattr(_PASS, "depth", 0) + 1
        if _PASS.depth == 1:
            _PASS.memo = {}
        return self

    def __exit__(self, *exc):
        _PASS.depth -= 1
        if _PASS.depth == 0:
            _PASS.memo = {}
        return False


def _memo():
    return _PASS.memo if getattr(_PASS, "depth", 0) > 0 else None


def invalidate_folders():
    m = _memo()
    if m is not None:
        for k in [k for k in m if k[0] == "folders"]:
            m.pop(k, None)


def invalidate_keys():
    m = _memo()
    if m is not None:
        for k in [k for k in m if k[0] == "key"]:
            m.pop(k, None)


_list_folders_uncached = server_list_folders
_get_key_uncached = server_get_key


def server_list_folders(server_url, token):
    m = _memo()
    key = ("folders", server_url, token, "")
    if m is not None and key in m:
        return json.loads(m[key])
    resp = _list_folders_uncached(server_url, token)
    if m is not None and isinstance(resp, dict) and "folders" in resp:
        m[key] = json.dumps(resp)
    return resp


def server_get_key(server_url, token, handle):
    m = _memo()
    key = ("key", server_url, token, handle)
    if m is not None and key in m:
        return json.loads(m[key])
    resp = _get_key_uncached(server_url, token, handle)
    if m is not None and isinstance(resp, dict) and resp.get("public_key"):
        m[key] = json.dumps(resp)
    return resp


def _clears(fn, *kinds):
    def wrapped(*a, **kw):
        try:
            return fn(*a, **kw)
        finally:
            if "folders" in kinds:
                invalidate_folders()
            if "keys" in kinds:
                invalidate_keys()
    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    return wrapped


for _name, _kinds in (
        ("server_share_folder", ("folders",)),
        ("server_accept_folder", ("folders",)),
        ("server_unshare_folder", ("folders",)),
        ("server_grant_folder", ("folders",)),
        ("server_publish_folder", ("folders",)),
        ("server_unpublish_folder", ("folders",)),
        ("server_subscribe_folder", ("folders",)),
        ("server_unsubscribe_folder", ("folders",)),
        ("server_add_contact", ("keys",)),
        ("server_accept_contact", ("keys",)),
        ("server_attest_contact", ("keys",)),
        ("server_remove_contact", ("folders", "keys")),
        ("server_revoke_child", ("folders", "keys")),
        ("server_deregister", ("folders", "keys")),
        ("server_login", ("folders", "keys"))):
    globals()[_name] = _clears(globals()[_name], *_kinds)


_ack_uncached = server_ack_envelopes


def server_ack_envelopes(server_url, token, ids):
    resp = _ack_uncached(server_url, token, ids)
    invalidate_inbox()
    with _BODY_LOCK:
        for i in ids or []:
            gone = _BODY_CACHE.pop((server_url, i), None)
            if gone is not None:
                _BODY_BYTES[0] -= len(gone.get("ciphertext") or "")
                try:
                    _BODY_ORDER.remove((server_url, i))
                except ValueError:
                    pass
    return resp

