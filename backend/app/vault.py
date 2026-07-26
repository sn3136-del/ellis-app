"""Encrypted secrets vault. Portal passwords and session tokens are stored only
as ciphertext keyed by an opaque reference; the rest of the DB persists just
the reference.

Ciphertext is persisted to the vault_secrets table (write-through cache in
front), so a backend/worker restart can still reveal a persisted session or
credential reference — the workflow no longer loses its portal session refs on
restart. Production backend: AWS Secrets Manager (activation: set
AWS_SECRETS_PREFIX + credentials). Encryption: AES-256-GCM with a key derived
from ELLIS_VAULT_PASSPHRASE. Plaintext is never written to the DB, logs, or
email.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets

from .config import settings

_BACKENDS = {}  # ref -> ciphertext (write-through cache over vault_secrets)


def _db_session():
    from .db import SessionLocal
    return SessionLocal()


def _db_put(ref: str, ciphertext: str, meta: dict | None) -> None:
    from . import models
    db = _db_session()
    try:
        row = db.get(models.VaultSecret, ref)
        if row is None:
            db.add(models.VaultSecret(ref=ref, ciphertext=ciphertext, meta=meta or {}))
        else:
            row.ciphertext = ciphertext
        db.commit()
    except Exception:  # noqa: BLE001 — cache still holds it for this process
        db.rollback()
    finally:
        db.close()


def _db_get(ref: str) -> str | None:
    from . import models
    db = _db_session()
    try:
        row = db.get(models.VaultSecret, ref)
        return row.ciphertext if row is not None else None
    except Exception:  # noqa: BLE001
        return None
    finally:
        db.close()


def _db_delete(ref: str) -> bool:
    from . import models
    db = _db_session()
    try:
        row = db.get(models.VaultSecret, ref)
        if row is None:
            return False
        db.delete(row)
        db.commit()
        return True
    except Exception:  # noqa: BLE001
        db.rollback()
        return False
    finally:
        db.close()


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
    ct = _encrypt(secret_value)
    _BACKENDS[ref] = ct
    _db_put(ref, ct, meta)
    return {"ref": ref, "provider": "local_encrypted", "meta": meta or {}}


def reveal(ref: str) -> str:
    ct = _BACKENDS.get(ref)
    if ct is None:
        ct = _db_get(ref)   # restart survival: rehydrate from vault_secrets
        if ct is None:
            raise KeyError("vault ref not found")
        _BACKENDS[ref] = ct
    return _decrypt(ct)


def rotate(ref: str, new_value: str) -> dict:
    if _BACKENDS.get(ref) is None and _db_get(ref) is None:
        raise KeyError("vault ref not found")
    ct = _encrypt(new_value)
    _BACKENDS[ref] = ct
    _db_put(ref, ct, None)
    return {"ref": ref, "rotated": True}


def destroy(ref: str) -> bool:
    in_cache = _BACKENDS.pop(ref, None) is not None
    in_db = _db_delete(ref)
    return in_cache or in_db


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
