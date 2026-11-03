import sys
import threading
import time

import hoplita_crypto as crypto

TAKE, WAIT, DROP = "take", "wait", "drop"

DATA_MAX_AGE_S = 90 * 86400
LIVE_MAX_AGE_S = 15 * 60
TOO_OLD, FROM_FUTURE, REPLAYED = "too old for its kind", "from the future", "replayed"
FUTURE_SKEW_S = 10 * 60

_rules = {}
_ctx = {"identity": None, "key_for": None, "sql": None, "ack": None}
_seen = None
_seen_lock = threading.Lock()
_opened = {}
NONCE_STORE = "inbound_nonce"


class Opened:
    __slots__ = ("id", "sender", "type", "rid", "meta", "payload", "nonce",
                 "ts", "outer")

    def __init__(self, env_id, dec, outer):
        self.id = env_id
        self.sender = dec.get("from") or ""
        self.type = dec.get("type") or ""
        self.rid = dec.get("rid") or ""
        self.meta = dec.get("meta") or {}
        self.payload = dec.get("payload")
        self.nonce = dec.get("nonce") or ""
        self.ts = int(dec.get("ts") or 0)
        self.outer = outer


def configure(identity, key_for, sql, ack):
    _ctx.update(identity=identity, key_for=key_for, sql=sql, ack=ack)


def rule(*types, max_age=DATA_MAX_AGE_S, consume_on_open=False):
    def deco(fn):
        for t in types:
            _rules[t] = (fn, max_age, consume_on_open)
        return fn
    return deco


def known_types():
    return set(_rules)


def seal(to, to_pub, rtype, payload, rid="", meta=None):
    if rtype not in _rules:
        raise ValueError(f"no inbound rule for envelope type {rtype!r}")
    me, my_priv, _pub = _ctx["identity"]()
    env = crypto.make_envelope(rtype, payload, frm=me, to=to, rid=rid, meta=meta)
    return crypto.box_for(my_priv, to_pub, env)


def _load_seen():
    global _seen
    if _seen is not None:
        return _seen
    seen = {}
    sql = _ctx["sql"]() if _ctx["sql"] else None
    if sql is not None:
        now = time.time()
        try:
            for row in sql.load(NONCE_STORE) or []:
                n, ts = row.get("id"), row.get("ts", 0)
                if not n:
                    continue
                if now - ts > DATA_MAX_AGE_S + FUTURE_SKEW_S:
                    sql.delete_raw(NONCE_STORE, n)
                else:
                    seen[n] = ts
        except Exception as e:
            print(f"[INBOUND] consumed nonces not loaded: {e}", file=sys.stderr)
    _seen = seen
    return _seen


def forget_loaded():
    global _seen
    with _seen_lock:
        _seen = None


def _consume(nonce, ts):
    with _seen_lock:
        seen = _load_seen()
        seen[nonce] = ts
        sql = _ctx["sql"]() if _ctx["sql"] else None
        if sql is not None:
            try:
                sql.upsert(NONCE_STORE, nonce, {"id": nonce, "ts": ts})
            except Exception as e:
                print(f"[INBOUND] nonce not stored: {e}", file=sys.stderr)


def _check(dec, outer_sender, outer_type, me, now):
    if not isinstance(dec, dict) or dec.get("v") != crypto.ENVELOPE_VERSION:
        return "version"
    if outer_type and dec.get("type") != outer_type:
        return "type differs from the outside"
    if (dec.get("from") or "").lower() != (outer_sender or "").lower():
        return "sender differs from the outside"
    if (dec.get("to") or "").lower() != (me or "").lower():
        return "not addressed to me"
    r = _rules.get(dec.get("type"))
    if r is None:
        return "no rule for this type"
    ts = int(dec.get("ts") or 0)
    if ts > now + FUTURE_SKEW_S:
        return FROM_FUTURE
    if now - ts > r[1]:
        return TOO_OLD
    nonce = dec.get("nonce") or ""
    if len(nonce) < 16:
        return "no nonce"
    with _seen_lock:
        if nonce in _load_seen():
            return REPLAYED
    return None


def _open(outer_sender, outer_type, blob, env_id):
    me, my_priv, my_pub = _ctx["identity"]()
    try:
        pub = my_pub if (outer_sender or "").lower() == (me or "").lower() \
            else _ctx["key_for"](outer_sender)
    except Exception as e:
        return DROP, None, f"no signed key for {outer_sender!r}: {e}"
    if not pub:
        return DROP, None, f"no signed key for {outer_sender!r}"
    try:
        dec = crypto.unbox(my_priv, pub, blob or "")
    except Exception:
        dec = None
    if dec is None and pub != my_pub:
        try:
            fresh = _ctx["key_for"](outer_sender, fresh=True)
            dec = crypto.unbox(my_priv, fresh, blob or "") if fresh and fresh != pub else None
        except Exception:
            dec = None
    if dec is None:
        return DROP, None, f"does not open with {outer_sender!r}'s key"
    why = _check(dec, outer_sender, outer_type, me, time.time())
    if why:
        return DROP, None, why
    o = Opened(env_id, dec, {"sender": outer_sender, "type": outer_type})
    verdict = _rules[o.type][0](o)
    if verdict not in (TAKE, WAIT, DROP):
        verdict = DROP
    return verdict, o, ("" if verdict == TAKE else f"rule said {verdict}")


def open_env(env):
    verdict, o, why = _open(env.get("sender") or "", env.get("resource_type") or "",
                            env.get("ciphertext") or "", env.get("id"))
    if verdict == DROP:
        print(f"[INBOUND] dropped {env.get('resource_type')!r} {env.get('id')} "
              f"from {env.get('sender')!r}: {why}", file=sys.stderr)
    elif verdict == TAKE and _rules[o.type][2]:
        _consume(o.nonce, o.ts)
    elif o is not None and o.id:
        _opened[o.id] = (o.nonce, o.ts)
    return verdict, o, why


def open_batch(envelopes, types=None, refused=None):
    taken, dropped, waiting = [], [], 0
    for env in envelopes or []:
        if types and env.get("resource_type") not in types:
            continue
        verdict, o, why = open_env(env)
        if verdict == TAKE:
            taken.append(o)
        elif verdict == WAIT:
            waiting += 1
        else:
            if env.get("id"):
                dropped.append(env.get("id"))
            if refused is not None and o is not None:
                refused.append((o, why))
    return taken, dropped, waiting


def open_live(sender, rtype, blob):
    verdict, o, why = _open(sender, rtype, blob, None)
    if verdict != TAKE:
        print(f"[INBOUND] live {rtype!r} from {sender!r} not taken: {why}",
              file=sys.stderr)
        return None
    _consume(o.nonce, o.ts)
    return o


def ack(ids):
    ids = [i for i in (ids or []) if i]
    if not ids:
        return
    for i in ids:
        got = _opened.pop(i, None)
        if got:
            _consume(*got)
    _ctx["ack"](ids)


def open_own(blob):
    me, my_priv, my_pub = _ctx["identity"]()
    try:
        dec = crypto.unbox(my_priv, my_pub, blob or "")
    except Exception:
        return None
    if (not isinstance(dec, dict) or dec.get("v") != crypto.ENVELOPE_VERSION
            or (dec.get("from") or "").lower() != (me or "").lower()
            or (dec.get("to") or "").lower() != (me or "").lower()
            or dec.get("type") not in _rules):
        return None
    return Opened(None, dec, {"sender": me, "type": dec.get("type")})


def reset_for_tests():
    global _seen
    _seen = None
    _opened.clear()
