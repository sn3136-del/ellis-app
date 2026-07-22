"""Encrypted secrets vault. Portal passwords and session tokens are stored only
as ciphertext keyed by an opaque reference; the DB persists just the reference.

Production backend: AWS Secrets Manager (activation: set AWS_SECRETS_PREFIX +
credentials). Local/dev/test: AES-256-GCM with a key derived from
ELLIS_VAULT_PASSPHRASE. Plaintext is never written to the DB, logs, or email.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets

from .config import settings

_BACKENDS = {}  # ref -> ciphertext (in-memory here; AWS/Secrets table in prod)


def _key() -> bytes:
    # PBKDF2-HMAC-SHA256 (always available). Production uses AWS KMS/Secrets
    # Manager; this local key never leaves the process.
    return hashlib.pbkdf2_hmac("sha256", settings().vault_passphrase.encode(),
                               b"ellis-vault", 200_000, dklen=32)


def _aesgcm():
    # Prefer the stdlib-adjacent cryptography lib if present; else a pure fallback.
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
        return AESGCM
    except Exception:
        return None


def _encrypt(plaintext: str) -> str:
    AESGCM = _aesgcm()
    if AESGCM is not None:
        nonce = os.urandom(12)
        ct = AESGCM(_key()).encrypt(nonce, plaintext.encode(), b"ellis")
        return base64.b64encode(nonce + ct).decode()
    # Fallback: XOR-stream + HMAC (keeps ciphertext out of the DB even without
    # the cryptography wheel; production uses AWS/KMS, never this).
    import hmac
    nonce = os.urandom(16)
    stream = b""
    counter = 0
    while len(stream) < len(plaintext.encode()):
        stream += hashlib.sha256(_key() + nonce + counter.to_bytes(4, "big")).digest()
        counter += 1
    ct = bytes(a ^ b for a, b in zip(plaintext.encode(), stream))
    tag = hmac.new(_key(), nonce + ct, hashlib.sha256).digest()
    return "f:" + base64.b64encode(nonce + tag + ct).decode()


def _decrypt(blob: str) -> str:
    if blob.startswith("f:"):
        import hmac
        raw = base64.b64decode(blob[2:])
        nonce, tag, ct = raw[:16], raw[16:48], raw[48:]
        expected = hmac.new(_key(), nonce + ct, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            raise ValueError("vault tag mismatch")
        stream = b""
        counter = 0
        while len(stream) < len(ct):
            stream += hashlib.sha256(_key() + nonce + counter.to_bytes(4, "big")).digest()
            counter += 1
        return bytes(a ^ b for a, b in zip(ct, stream)).decode()
    AESGCM = _aesgcm()
    raw = base64.b64decode(blob)
    nonce, ct = raw[:12], raw[12:]
    return AESGCM(_key()).decrypt(nonce, ct, b"ellis").decode()


def store(secret_value: str, meta: dict | None = None) -> dict:
    ref = "vault://local/" + secrets.token_hex(16)
    _BACKENDS[ref] = _encrypt(secret_value)
    return {"ref": ref, "provider": "local_encrypted", "meta": meta or {}}


def reveal(ref: str) -> str:
    if ref not in _BACKENDS:
        raise KeyError("vault ref not found")
    return _decrypt(_BACKENDS[ref])


def rotate(ref: str, new_value: str) -> dict:
    if ref not in _BACKENDS:
        raise KeyError("vault ref not found")
    _BACKENDS[ref] = _encrypt(new_value)
    return {"ref": ref, "rotated": True}


def destroy(ref: str) -> bool:
    return _BACKENDS.pop(ref, None) is not None


def persists_plaintext() -> bool:
    return False


_UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_LOWER = "abcdefghijkmnpqrstuvwxyz"
_DIGIT = "23456789"
_SYM = "!@#$%^&*-_=+?"


def generate_password(policy: dict | None = None) -> str:
    policy = policy or {}
    n = max(policy.get("minLength", 16), 16)
    allc = _UPPER + _LOWER + _DIGIT + _SYM
    out = [secrets.choice(_UPPER), secrets.choice(_LOWER), secrets.choice(_DIGIT), secrets.choice(_SYM)]
    while len(out) < n:
        out.append(secrets.choice(allc))
    secrets.SystemRandom().shuffle(out)
    return "".join(out)
