import hashlib
import json
import logging
import secrets
from .database_errors import IntegrityError

from . import db
from .config import settings
from .crypto import DecryptionError, decrypt, encrypt, hash_password, verify_password
from .vault import VaultError, create_wrapped_dek, rewrap as vault_rewrap, unlock as vault_unlock

logger = logging.getLogger(__name__)

_DUMMY_HASH: str | None = None
_CRYPTO_SENTINEL_KEY = "credential_crypto_sentinel_v1"
_CRYPTO_SENTINEL_VALUE = "fuse-credential-crypto-v1"


class CredentialDataError(RuntimeError):
    """Stored credential data is malformed rather than merely absent."""


def key_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def mint_api_key() -> str:
    return "sk-" + secrets.token_urlsafe(24)


def _dummy_hash() -> str:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_urlsafe(16))
    return _DUMMY_HASH


def normalize_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not email or len(email) > 254 or " " in email or email.count("@") != 1:
        raise ValueError("email")
    local, domain = email.split("@", 1)
    if not local or not domain or "." not in domain:
        raise ValueError("email")
    if domain.startswith(".") or domain.endswith(".") or ".." in domain:
        raise ValueError("email")
    return email


def password_fingerprint(user: dict) -> str:
    """Bound into the session cookie; a password change rotates the stored hash,
    which invalidates every session minted before it."""
    return hashlib.sha256(str(user["password_hash"]).encode()).hexdigest()[:32]


def _invite_hash(token: str) -> str:
    return hashlib.sha256(str(token or "").encode()).hexdigest()


def create_user(email: str, password: str, *, admin: bool | None = None, require_invite: bool = False,
                invite: str = "") -> dict:
    email = normalize_email(email)
    if len(password) < 8 or len(password) > 256:
        raise ValueError("password")
    raw = mint_api_key()
    with db.tx() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        if n == 0:
            # Snapshot reads let two concurrent first registrations both see an
            # empty users table; the sentinel insert serializes the claim so
            # only one wins first-admin/open-registration.
            try:
                conn.execute("INSERT INTO schema_meta(key, value) VALUES('first_user_claimed', ?)", (db.now(),))
            except IntegrityError:
                n = 1
        if require_invite:
            open_reg = settings.allow_register or n == 0
            # Knowing an invited address is not an invitation: the single-use
            # token from the admin's invite link must match too.
            consumed = conn.execute(
                "DELETE FROM registration_whitelist WHERE email=? AND token_hash<>'' AND token_hash=?",
                (email, _invite_hash(invite)),
            )
            if not open_reg and consumed.rowcount == 0:
                raise ValueError("closed")
        if admin is True:
            is_admin = 1
        elif admin is False:
            is_admin = 0
        else:
            is_admin = 1 if n == 0 else 0
        salt, wrapped, params = create_wrapped_dek(password)
        try:
            conn.execute(
                """INSERT INTO users (email, password_hash, is_admin, created_at, vault_salt, wrapped_dek, vault_kdf)
                   VALUES (?,?,?,?,?,?,?)""",
                (email, hash_password(password), is_admin, db.now(), salt, wrapped, params),
            )
        except IntegrityError:
            raise ValueError("exists") from None
        uid = db.last_id(conn)
        conn.execute(
            "INSERT INTO api_keys (user_id, name, prefix, key_hash, created_at) VALUES (?,?,?,?,?)",
            (uid, "default", raw[:9], key_hash(raw), db.now()),
        )
    user = dict(get_user(uid))
    user["new_key"] = raw
    return user


def register(email: str, password: str, invite: str = "") -> dict:
    try:
        return create_user(email, password, require_invite=True, invite=invite)
    except ValueError:
        verify_password((password or "x")[:256], _dummy_hash())
        raise


def list_whitelist():
    return db.fetchall("SELECT id, email, created_at FROM registration_whitelist ORDER BY id")


def add_whitelist(email: str) -> tuple[str, str]:
    """List an email and return (email, invite token). Only the token's hash
    is stored; the admin hands the invite link to that person."""
    email = normalize_email(email)
    if user_by_email(email):
        raise ValueError("exists")
    token = secrets.token_urlsafe(24)
    try:
        db.execute(
            "INSERT INTO registration_whitelist (email, created_at, token_hash) VALUES (?,?,?)",
            (email, db.now(), _invite_hash(token)),
        )
    except IntegrityError:
        raise ValueError("listed") from None
    return email, token


def remove_whitelist(invite_id: int) -> bool:
    result = db.execute("DELETE FROM registration_whitelist WHERE id=?", (invite_id,))
    return result.rowcount > 0


def get_user(uid: int):
    return db.fetchone("SELECT * FROM users WHERE id=?", (uid,))


def session_user(uid, stamp):
    """The user a session cookie still authenticates, or None. Sessions are
    bound to the password hash, so a password change logs out every cookie
    issued before it (HTTP and WebSocket alike). An unstamped session cannot
    be checked against the current password and must sign in again."""
    if not uid or not stamp:
        return None
    user = get_user(uid)
    if not user:
        return None
    if stamp != password_fingerprint(user):
        return None
    return user


def user_by_email(email: str):
    return db.fetchone("SELECT * FROM users WHERE email=?", (email.strip().lower(),))


def login(email: str, password: str):
    if len(password) > 256:
        return None
    row = user_by_email(email)
    stored = row["password_hash"] if row else _dummy_hash()
    if not verify_password(password, stored) or not row:
        return None
    return row


def change_password(user_id: int, old_password: str, new_password: str) -> None:
    try:
        vault_rewrap(user_id, old_password, new_password)
    except DecryptionError as exc:
        raise ValueError("vault") from exc
    except VaultError as exc:
        raise ValueError(str(exc) or "password") from exc


def unlock_vault(user_id: int, password: str) -> None:
    row = get_user(user_id)
    if row is None or not verify_password(password, row["password_hash"]):
        raise ValueError("auth")
    try:
        vault_unlock(user_id, password)
    except DecryptionError as exc:
        raise ValueError("vault") from exc


def user_by_api_key(raw: str):
    row = db.fetchone(
        "SELECT u.*, k.id AS key_id FROM api_keys k JOIN users u ON u.id=k.user_id WHERE k.key_hash=?",
        (key_hash(raw),),
    )
    if row:
        db.execute("UPDATE api_keys SET last_used_at=? WHERE id=?", (db.now(), row["key_id"]))
    return row


def keys_for(uid: int):
    return db.fetchall(
        """SELECT id, name, prefix, created_at, last_used_at, prompt_tokens, completion_tokens,
                  output_tokens, visible_tokens, reasoning_tokens, cached_tokens, cache_write_tokens,
                  audio_tokens, image_tokens, total_tokens,
                  requests, cost_micros, last_model, usage_updated_at
           FROM api_keys WHERE user_id=? ORDER BY id""",
        (uid,),
    )


def create_api_key(uid: int, name: str) -> str:
    name = name.strip()
    if not name or len(name) > 80 or any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValueError("name")
    raw = mint_api_key()
    db.execute(
        "INSERT INTO api_keys (user_id, name, prefix, key_hash, created_at) VALUES (?,?,?,?,?)",
        (uid, name, raw[:9], key_hash(raw), db.now()),
    )
    return raw


def delete_api_key(uid: int, key_id: int) -> bool:
    result = db.execute("DELETE FROM api_keys WHERE id=? AND user_id=?", (key_id, uid))
    return result.rowcount > 0


def list_users():
    return db.fetchall(
        """SELECT u.id, u.email, u.is_admin, u.created_at,
                  COALESCE(SUM(p.total_tokens), 0) AS total_tokens,
                  COALESCE(SUM(p.requests), 0) AS requests,
                  COALESCE(SUM(p.cost_micros), 0) AS cost_micros
           FROM users u
           LEFT JOIN provider_usage p ON p.user_id=u.id
           GROUP BY u.id
           ORDER BY u.id"""
    )


def admin_count() -> int:
    row = db.fetchone("SELECT COUNT(*) AS n FROM users WHERE is_admin=1")
    return int(row["n"] if row else 0)


def _admins_locked(conn):
    """Lock current admin rows so the last-admin check serializes against a
    concurrent demote/delete. FOR UPDATE reads the latest committed rows."""
    return conn.execute("SELECT id FROM users WHERE is_admin=1 FOR UPDATE").fetchall()


def set_admin(user_id: int, admin: bool, *, actor_id: int) -> None:
    with db.tx() as conn:
        target = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not target:
            raise ValueError("missing")
        if user_id == actor_id:
            raise ValueError("self")
        admins = _admins_locked(conn)
        if target["is_admin"] and not admin and len(admins) <= 1:
            raise ValueError("last_admin")
        conn.execute("UPDATE users SET is_admin=? WHERE id=?", (1 if admin else 0, user_id))


_CRED_ROLLUP_EVENT_COLS = (
    "prompt_tokens", "completion_tokens", "output_tokens", "visible_tokens", "reasoning_tokens",
    "cached_tokens", "cache_write_tokens", "total_tokens",
)


def delete_user(user_id: int, *, actor_id: int) -> str:
    with db.tx() as conn:
        target = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not target:
            raise ValueError("missing")
        if user_id == actor_id:
            raise ValueError("self")
        admins = _admins_locked(conn)
        if target["is_admin"] and len(admins) <= 1:
            raise ValueError("last_admin")
        email = target["email"]
        conn.execute(
            "UPDATE credentials SET donated_by=NULL, disabled=1 WHERE donated_by=?",
            (user_id,),
        )
        # Credential rollups are SUM(usage_events); the user's events go below.
        # A locking read: the snapshot predates this point, so a plain SUM
        # would miss an event recorded since, which the DELETE then removes
        # without its rollup share. The lock also holds new ones off.
        spent = conn.execute(
            f"""SELECT credential_id, {", ".join(f"COALESCE(SUM({col}), 0) AS {col}" for col in _CRED_ROLLUP_EVENT_COLS)},
                       COALESCE(SUM(count_request), 0) AS requests,
                       COALESCE(SUM(COALESCE(reported_cost_micros, estimated_cost_micros)), 0) AS cost_micros
                FROM usage_events WHERE user_id=? AND credential_id IS NOT NULL GROUP BY credential_id"""
            + db.for_update(conn),
            (user_id,),
        ).fetchall()
        rollup_cols = (*_CRED_ROLLUP_EVENT_COLS, "requests", "cost_micros")
        conn.executemany(
            f"UPDATE credentials SET {', '.join(f'{col}={col}-?' for col in rollup_cols)} WHERE id=?",
            [(*(int(row[col]) for col in rollup_cols), row["credential_id"]) for row in spent],
        )
        conn.execute("DELETE FROM api_keys WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM provider_usage WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM usage_events WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM usage_hourly WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    try:
        from . import vault

        vault.lock(user_id)
    except Exception:
        pass
    return email


def add_credential(source_id: int, label: str, secret: str, extra: dict | None = None, donated_by: int | None = None) -> int:
    with db.tx() as conn:
        conn.execute(
            """INSERT INTO credentials (source_id, label, secret, extra, week_start, donated_by, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (source_id, label, encrypt(secret), encrypt(json.dumps(extra or {})), db.iso_week_start(), donated_by, db.now()),
        )
        return db.last_id(conn)


def cred_secret(row) -> str:
    return decrypt(row["secret"])


def _decode_extra(raw: str) -> tuple[dict, bool]:
    raw = str(raw or "")
    stripped = raw.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            return data, True
    plain = decrypt(raw)
    try:
        data = json.loads(plain)
    except json.JSONDecodeError as exc:
        raise CredentialDataError("decrypted credential extra is not valid JSON") from exc
    if not isinstance(data, dict):
        raise CredentialDataError("decrypted credential extra is not an object")
    return data, False


def cred_extra(row) -> dict:
    data, _legacy = _decode_extra(row["extra"])
    return data


def validate_credential_encryption() -> None:
    """Fail startup on a wrong key or corrupt row, then migrate legacy JSON extras."""
    with db.tx() as conn:
        sentinel = conn.execute(
            "SELECT value FROM schema_meta WHERE key=?", (_CRYPTO_SENTINEL_KEY,)
        ).fetchone()
        if sentinel and decrypt(sentinel["value"]) != _CRYPTO_SENTINEL_VALUE:
            raise DecryptionError("credential encryption sentinel is invalid")

        legacy_extras: list[tuple[str, int]] = []
        for row in conn.execute("SELECT id, secret, extra FROM credentials").fetchall():
            decrypt(row["secret"])
            extra, legacy = _decode_extra(row["extra"])
            if legacy:
                legacy_extras.append((encrypt(json.dumps(extra)), row["id"]))

        if legacy_extras:
            conn.executemany("UPDATE credentials SET extra=? WHERE id=?", legacy_extras)
        if not sentinel:
            conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES (?, ?)",
                (_CRYPTO_SENTINEL_KEY, encrypt(_CRYPTO_SENTINEL_VALUE)),
            )


# Written only by the token writers (store_tokens re-links and
# token_store.persist_rotated, both compare-and-swap on `extra`). Metadata
# writers hand in a copy read earlier; the row's own values win under its row
# lock, so a stale copy can never roll a rotated refresh token back.
_TOKEN_KEYS = ("access_token", "refresh_token", "expires_at", "id_token")


def update_extra(cred_id: int, extra: dict, **columns) -> None:
    """Write metadata into a credential's `extra` (plus optional plain columns)."""
    with db.tx() as conn:
        row = conn.execute("SELECT extra FROM credentials WHERE id=?" + db.for_update(conn), (cred_id,)).fetchone()
        if row is None:
            return
        current, _legacy = _decode_extra(row["extra"])
        merged = dict(extra)
        for key in _TOKEN_KEYS:
            if key in current:
                merged[key] = current[key]
            else:
                merged.pop(key, None)
        sets = "".join(f", {name}=?" for name in columns)
        conn.execute(
            f"UPDATE credentials SET extra=?{sets} WHERE id=?",
            (encrypt(json.dumps(merged)), *columns.values(), cred_id),
        )


def disable_credential(cred_id: int, *, extra: str | None = None) -> None:
    """extra: the stored value the failing call read. A credential re-linked or
    refreshed since keeps the row id but not that value, and must not be
    disabled for its predecessor's rejection."""
    if extra is None:
        cursor = db.execute("UPDATE credentials SET disabled=1 WHERE id=?", (cred_id,))
    else:
        cursor = db.execute("UPDATE credentials SET disabled=1 WHERE id=? AND extra=?", (cred_id, extra))
    if not cursor.rowcount:
        return
    # Every pool's revoked-grant and repeated-401 paths land here; without this
    # line a credential can vanish from the pool with nothing in the journal.
    row = db.fetchone(
        "SELECT c.label, s.type AS kind FROM credentials c JOIN sources s ON s.id=c.source_id WHERE c.id=?",
        (cred_id,),
    )
    logger.warning(
        "Disabled %s credential %s (%s)",
        (row and row["kind"]) or "pool",
        cred_id,
        (row and row["label"]) or "no label",
    )


def delete_credential(cred_id: int) -> None:
    db.execute("DELETE FROM credentials WHERE id=?", (cred_id,))


def replace_source_secret(source_id: int, label: str, secret: str) -> None:
    with db.tx() as conn:
        conn.execute("DELETE FROM credentials WHERE source_id=?", (source_id,))
        if secret:
            conn.execute(
                """INSERT INTO credentials (source_id, label, secret, extra, week_start, donated_by, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    source_id,
                    label,
                    encrypt(secret),
                    encrypt("{}"),
                    db.iso_week_start(),
                    None,
                    db.now(),
                ),
            )
