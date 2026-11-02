
import os
import json
import time
import base64

from nacl.public import PrivateKey, PublicKey, SealedBox, Box
from nacl.encoding import Base64Encoder


ENVELOPE_VERSION = 3



def generate_identity():
    sk = PrivateKey.generate()
    priv_b64 = sk.encode(Base64Encoder).decode("utf-8")
    pub_b64 = sk.public_key.encode(Base64Encoder).decode("utf-8")
    return priv_b64, pub_b64



def make_envelope(resource_type, payload, *, frm, to, rid="", meta=None):
    return {
        "v": ENVELOPE_VERSION,
        "type": resource_type,
        "from": frm,
        "to": to,
        "rid": rid or "",
        "nonce": os.urandom(16).hex(),
        "ts": int(time.time()),
        "meta": meta or {},
        "payload": payload,
    }



def seal_for(recipient_pub_b64, envelope):
    pub = PublicKey(recipient_pub_b64.encode("utf-8"), Base64Encoder)
    sealed = SealedBox(pub)
    plaintext = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
    ciphertext = sealed.encrypt(plaintext)
    return base64.b64encode(ciphertext).decode("utf-8")


def unseal(my_priv_b64, blob_b64):
    sk = PrivateKey(my_priv_b64.encode("utf-8"), Base64Encoder)
    unseal_box = SealedBox(sk)
    ciphertext = base64.b64decode(blob_b64)
    plaintext = unseal_box.decrypt(ciphertext)
    return json.loads(plaintext.decode("utf-8"))



def box_for(my_priv_b64, recipient_pub_b64, envelope):
    sk = PrivateKey(my_priv_b64.encode("utf-8"), Base64Encoder)
    pub = PublicKey(recipient_pub_b64.encode("utf-8"), Base64Encoder)
    box = Box(sk, pub)
    plaintext = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
    encrypted = box.encrypt(plaintext)
    return base64.b64encode(bytes(encrypted)).decode("utf-8")


def unbox(my_priv_b64, sender_pub_b64, blob_b64):
    sk = PrivateKey(my_priv_b64.encode("utf-8"), Base64Encoder)
    pub = PublicKey(sender_pub_b64.encode("utf-8"), Base64Encoder)
    box = Box(sk, pub)
    ciphertext = base64.b64decode(blob_b64)
    plaintext = box.decrypt(ciphertext)
    return json.loads(plaintext.decode("utf-8"))



def _kit_key(raw_token):
    import hashlib
    return hashlib.blake2b(raw_token.encode("utf-8"), digest_size=32,
                           person=b"hoplita-kit-v1").digest()


def seal_kit(raw_token, kit):
    from nacl.secret import SecretBox
    box = SecretBox(_kit_key(raw_token))
    ct = box.encrypt(json.dumps(kit, sort_keys=True).encode("utf-8"))
    return base64.b64encode(bytes(ct)).decode("ascii")


def open_kit(raw_token, blob_b64):
    from nacl.secret import SecretBox
    box = SecretBox(_kit_key(raw_token))
    return json.loads(box.decrypt(base64.b64decode(blob_b64)).decode("utf-8"))



def fingerprint(pub_b64):
    import hashlib
    raw = base64.b64decode(pub_b64)
    h = hashlib.sha256(raw).hexdigest().upper()
    groups = [h[i:i+4] for i in range(0, 20, 4)]
    return " ".join(groups)


def safety_code(a_handle, a_pub, a_sign, b_handle, b_pub, b_sign):
    import hashlib
    halves = sorted(f"{h.lower()}|{p}|{s}" for h, p, s in
                    ((a_handle, a_pub, a_sign or ""), (b_handle, b_pub, b_sign or "")))
    digest = hashlib.sha512(("hoplita-safety-1\n" + "\n".join(halves)).encode()).digest()
    n = int.from_bytes(digest, "big")
    groups = []
    for _ in range(12):
        groups.append(f"{n % 100000:05d}")
        n //= 100000
    return " ".join(groups)



def generate_signing_identity():
    from nacl.signing import SigningKey
    sk = SigningKey.generate()
    priv_b64 = sk.encode(Base64Encoder).decode("utf-8")
    pub_b64 = sk.verify_key.encode(Base64Encoder).decode("utf-8")
    return priv_b64, pub_b64


def _canonical_payload(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sign_attestation(sign_priv_b64, payload):
    from nacl.signing import SigningKey
    sk = SigningKey(sign_priv_b64.encode("utf-8"), Base64Encoder)
    sig = sk.sign(_canonical_payload(payload)).signature
    return json.dumps({"payload": payload,
                       "sig": base64.b64encode(sig).decode("utf-8")},
                      ensure_ascii=False, sort_keys=True)


def verify_attestation(sign_pub_b64, attestation):
    from nacl.signing import VerifyKey
    from nacl.exceptions import BadSignatureError
    try:
        obj = json.loads(attestation)
        payload = obj["payload"]
        sig = base64.b64decode(obj["sig"])
        vk = VerifyKey(sign_pub_b64.encode("utf-8"), Base64Encoder)
        vk.verify(_canonical_payload(payload), sig)
        return payload
    except Exception:
        return None
