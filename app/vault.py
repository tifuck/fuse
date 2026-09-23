"""RAM-only per-user DEK vault. Host secrets are DEK-wrapped at rest, not APP_SECRET."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time

from . import db
from .crypto import (
    VAULT_KDF,
    DecryptionError,
    unwrap_dek,
    vault_kdf,
    wrap_dek,
)

IDLE_SECONDS = 12 * 60 * 60
KEEP_SECONDS = 4 * 60 * 60
KEEP_COOKIE = "fuse_vault"


class VaultLocked(RuntimeError):
    """Cookie may be valid but this process has no DEK for the user."""


class VaultError(RuntimeError):
    pass


_lock = threading.Lock()
_vault: dict[int, dict] = {}


def _row(user_id: int, conn=None):
    q = "SELECT vault_salt, wrapped_dek, vault_kdf FROM users WHERE id=?"
    if conn is not None:
        return conn.execute(q, (user_id,)).fetchone()
    return db.fetchone(q, (user_id,))


def _params(raw) -> dict:
    if not raw:
        return dict(VAULT_KDF)
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return dict(VAULT_KDF)
    return data if isinstance(data, dict) else dict(VAULT_KDF)


def _salt_bytes(raw) -> bytes:
    if raw is None:
        return b""
    if isinstance(raw, memoryview):
        raw = raw.tobytes()
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, str):
        try:
            return bytes.fromhex(raw)
        except ValueError:
            return raw.encode()
    return bytes(raw)


def create_wrapped_dek(password: str) -> tuple[bytes, str, str]:
    dek = os.urandom(32)
    salt = os.urandom(16)
    kek = vault_kdf(password, salt, VAULT_KDF)
    wrapped = wrap_dek(dek, kek)
    return salt, wrapped, json.dumps(VAULT_KDF, separators=(",", ":"))


def ensure_user_vault(conn, user_id: int, password: str) -> bytes:
    """Create a vault on first use. Returns plaintext DEK. Never blanks existing ciphertext."""
    row = conn.execute(
        "SELECT vault_salt, wrapped_dek, vault_kdf FROM users WHERE id=?", (user_id,)
    ).fetchone()
    if row is None:
        raise VaultError("user not found")
    if row["wrapped_dek"]:
        salt = _salt_bytes(row["vault_salt"])
        kek = vault_kdf(password, salt, _params(row["vault_kdf"]))
        return unwrap_dek(row["wrapped_dek"], kek)
    salt, wrapped, params = create_wrapped_dek(password)
    conn.execute(
        "UPDATE users SET vault_salt=?, wrapped_dek=?, vault_kdf=? WHERE id=?",
        (salt, wrapped, params, user_id),
    )
    kek = vault_kdf(password, salt, VAULT_KDF)
    return unwrap_dek(wrapped, kek)


def unlock(user_id: int, password: str) -> None:
    if not isinstance(password, str) or not password or len(password) > 256:
        raise VaultError("invalid password")
    with db.tx() as conn:
        conn.execute("BEGIN IMMEDIATE")
        dek = ensure_user_vault(conn, user_id, password)
    with _lock:
        rec = _vault.get(user_id) or {}
        rec["dek"] = dek
        rec["last_seen"] = time.monotonic()
        rec.setdefault("pinned_runs", set())
        _vault[user_id] = rec


def lock(user_id: int) -> None:
    with _lock:
        _vault.pop(int(user_id), None)


def lock_all() -> None:
    with _lock:
        _vault.clear()


def touch(user_id: int) -> None:
    with _lock:
        rec = _vault.get(int(user_id))
        if rec is not None:
            rec["last_seen"] = time.monotonic()


def pin_run(user_id: int, run_id: str) -> None:
    with _lock:
        rec = _vault.get(int(user_id))
        if rec is None:
            raise VaultLocked("vault locked")
        rec["pinned_runs"].add(str(run_id))
        rec["last_seen"] = time.monotonic()


def unpin_run(user_id: int, run_id: str) -> None:
    with _lock:
        rec = _vault.get(int(user_id))
        if rec is None:
            return
        rec["pinned_runs"].discard(str(run_id))


def unlocked(user_id: int) -> bool:
    try:
        get_dek(user_id)
        return True
    except VaultLocked:
        return False


def get_dek(user_id: int) -> bytes:
    uid = int(user_id)
    now = time.monotonic()
    with _lock:
        rec = _vault.get(uid)
        if rec is None or not rec.get("dek"):
            raise VaultLocked("vault locked")
        idle = now - float(rec.get("last_seen") or 0)
        pinned = rec.get("pinned_runs") or set()
        if idle > IDLE_SECONDS and not pinned:
            _vault.pop(uid, None)
            raise VaultLocked("vault locked")
        rec["last_seen"] = now
        return rec["dek"]


def _put_dek(user_id: int, dek: bytes) -> None:
    if not isinstance(dek, (bytes, bytearray)) or len(dek) != 32:
        raise VaultError("invalid dek")
    with _lock:
        rec = _vault.get(int(user_id)) or {}
        rec["dek"] = bytes(dek)
        rec["last_seen"] = time.monotonic()
        rec.setdefault("pinned_runs", set())
        _vault[int(user_id)] = rec


def _wrapped_fp(row) -> str:
    """Stable fingerprint of the account's current wrapped DEK.

    Binds keep-cookies to the vault they were issued for: a different (or
    recycled-uid) account never accepts a cookie minted under another vault.
    """
    return hashlib.sha256(str(row["wrapped_dek"] or "").encode()).hexdigest()[:32] if row else ""


def issue_keep(user_id: int) -> str | None:
    """Encrypt the live DEK for a short HttpOnly cookie. None if the vault is locked."""
    from .crypto import encrypt

    try:
        dek = get_dek(user_id)
    except VaultLocked:
        return None
    row = _row(user_id)
    payload = json.dumps(
        {
            "v": 2,
            "uid": int(user_id),
            "fp": _wrapped_fp(row),
            "dek": base64.b64encode(dek).decode("ascii"),
            "exp": int(time.time()) + KEEP_SECONDS,
        },
        separators=(",", ":"),
    )
    return encrypt(payload)


def restore_keep(user_id: int, token: str) -> bool:
    """Load a keep-cookie DEK into RAM. Does not extend idle by itself until get_dek."""
    from .crypto import DecryptionError, decrypt

    if unlocked(user_id):
        return True
    if not isinstance(token, str) or not token:
        return False
    try:
        data = json.loads(decrypt(token))
    except (DecryptionError, json.JSONDecodeError, TypeError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    if data.get("v") != 2:
        # v1 carries no vault fingerprint; reject rather than trust a DEK
        # whose owning account cannot be verified.
        return False
    try:
        uid = int(data.get("uid"))
        exp = int(data.get("exp") or 0)
    except (TypeError, ValueError):
        return False
    if uid != int(user_id) or exp < time.time():
        return False
    if str(data.get("fp") or "") != _wrapped_fp(_row(user_id)):
        # Account deleted/recreated (or vault replaced): this DEK belongs to
        # a different vault. Loading it would encrypt future host secrets
        # with a key the current password can never unwrap.
        return False
    raw = data.get("dek")
    if not isinstance(raw, str):
        return False
    try:
        dek = base64.b64decode(raw.encode("ascii"), validate=True)
    except (ValueError, TypeError):
        return False
    if len(dek) != 32:
        return False
    _put_dek(user_id, dek)
    return True


def rewrap(user_id: int, old_password: str, new_password: str) -> None:
    from .crypto import hash_password, verify_password

    if len(new_password) < 8 or len(new_password) > 256:
        raise VaultError("password")
    with db.tx() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT password_hash, vault_salt, wrapped_dek, vault_kdf FROM users WHERE id=?",
            (user_id,),
        ).fetchone()
        if row is None or not verify_password(old_password, row["password_hash"]):
            raise VaultError("auth")
        if row["wrapped_dek"]:
            salt = _salt_bytes(row["vault_salt"])
            kek = vault_kdf(old_password, salt, _params(row["vault_kdf"]))
            dek = unwrap_dek(row["wrapped_dek"], kek)
        else:
            dek = os.urandom(32)
        new_salt = os.urandom(16)
        params = json.dumps(VAULT_KDF, separators=(",", ":"))
        kek2 = vault_kdf(new_password, new_salt, VAULT_KDF)
        wrapped = wrap_dek(dek, kek2)
        conn.execute(
            "UPDATE users SET password_hash=?, vault_salt=?, wrapped_dek=?, vault_kdf=? WHERE id=?",
            (hash_password(new_password), new_salt, wrapped, params, user_id),
        )
    with _lock:
        rec = _vault.get(int(user_id)) or {}
        rec["dek"] = dek
        rec["last_seen"] = time.monotonic()
        rec.setdefault("pinned_runs", set())
        _vault[int(user_id)] = rec
