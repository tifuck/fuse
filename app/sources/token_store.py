"""Persist OAuth refresh results for pool credentials.

Providers rotate refresh tokens: once the token endpoint answers, the old
refresh token is dead. The new pair must reach the row even when `extra`
changed while the request was in flight, or the account is lost until the
donor re-links it.
"""

from __future__ import annotations

import asyncio
import logging
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from .. import accounts, db
from ..config import settings
from ..crypto import encrypt

logger = logging.getLogger(__name__)

CAS_ATTEMPTS = 5
# The old refresh token is already spent when persist_rotated runs: a
# transient DB error is retried rather than losing the account.
PERSIST_BACKOFF = (0.5, 2.0, 5.0)
_MAX_SPAN = 365 * 86400
OWNER_KEY = "oauth_token_owner"


def claim_refresh_ownership() -> bool:
    """Turn OAuth refresh off unless this database owns the pooled tokens.

    Refresh tokens are single use, so exactly one database may spend them:
    the schema that schema_meta names as owner. Clones and restores copy that
    row verbatim, so they never own the tokens, whatever their .env says (a
    clone refreshing a shared token kills the owner's copy). Runs at boot.
    """
    row = db.fetchone("SELECT value FROM schema_meta WHERE key=?", (OWNER_KEY,))
    owner = row["value"] if row else ""
    schema = urlsplit(settings.database_url).path.strip("/")
    if owner != schema:
        if settings.oauth_refresh_enabled:
            logger.warning("OAuth refresh off: database %r does not own the pooled tokens (schema_meta %s=%r)",
                           schema, OWNER_KEY, owner)
        settings.oauth_refresh_enabled = False
    logger.info("OAuth refresh %s for database %r", "on" if settings.oauth_refresh_enabled else "off", schema)
    return settings.oauth_refresh_enabled


def expires_at(seconds, default: int) -> str:
    """ISO expiry from a provider's expires_in; malformed values use default."""
    try:
        span = int(float(seconds)) if seconds not in (None, "") else default
    except (TypeError, ValueError, OverflowError):
        span = default
    span = max(60, min(span, _MAX_SPAN))
    return (datetime.now(timezone.utc) + timedelta(seconds=span)).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_refresh_failures(what: str, rows, results) -> None:
    """Log the exceptions asyncio.gather(..., return_exceptions=True) collected."""
    for row, result in zip(rows, results):
        if isinstance(result, Exception):
            logger.warning(
                "%s failed for credential %s", what, row["id"],
                exc_info=(type(result), result, result.__traceback__),
            )


_refresh_tasks: set = set()


def _log_orphan(task) -> None:
    _refresh_tasks.discard(task)
    # Retrieve the outcome so a caller that left does not leave it unobserved;
    # a caller still waiting gets (and logs) the exception itself.
    if not task.cancelled() and task.exception() is not None:
        logger.debug("Token refresh failed", exc_info=task.exception())


async def shield_refresh(coro):
    """Run a refresh (lock, token POST, persist) to the end even when the
    caller is cancelled: once the POST is sent the provider may already have
    spent the old refresh token, so abandoning it before the persist loses
    the account. The caller still sees its own cancellation."""
    task = asyncio.ensure_future(coro)
    _refresh_tasks.add(task)
    task.add_done_callback(_log_orphan)
    return await asyncio.shield(task)


def persist_rotated(cas, cred_id: int, old_extra_raw, secret: str, extra: dict, rotated: dict, *, merge=None) -> bool:
    """Write refreshed tokens with compare-and-swap.

    `cas(cred_id, old_raw, secret, new_extra) -> bool` is the source's
    conditional update. On a lost race the rotated fields (plus `merge`, a
    callback that folds other refresh results into the newest copy) are
    re-applied to the current row until the write lands. DB errors are
    retried with backoff (this blocks: call it through durable_call).
    """
    cid = int(cred_id)
    delays = iter(PERSIST_BACKOFF)
    while True:
        try:
            return _persist_once(cas, cid, old_extra_raw, secret, extra, rotated, merge)
        except Exception:
            delay = next(delays, None)
            if delay is None:
                logger.error("Credential %s: rotated OAuth tokens could not be saved; the account will need re-linking",
                             cid, exc_info=True)
                raise
            logger.warning("Credential %s: saving rotated OAuth tokens failed; retrying in %ss", cid, delay, exc_info=True)
            time.sleep(delay)


def _already_saved(current: dict, rotated: dict) -> bool:
    # Access tokens are unique per refresh; the refresh token may not rotate.
    return bool(rotated.get("access_token")) and all(current.get(k) == rotated.get(k)
                                                    for k in ("access_token", "refresh_token"))


def _persist_once(cas, cid: int, old_extra_raw, secret: str, extra: dict, rotated: dict, merge) -> bool:
    if cas(cid, old_extra_raw, secret, extra):
        return True
    try:
        original = accounts.cred_extra({"extra": old_extra_raw})
    except Exception:
        logger.warning("Credential %s extra unreadable during token refresh; rotated tokens dropped", cid)
        return False
    orig_refresh = original.get("refresh_token")
    for _ in range(CAS_ATTEMPTS):
        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cid,))
        if row is None:
            logger.warning("Credential %s was removed during token refresh; rotated tokens dropped", cid)
            return False
        merged = accounts.cred_extra(row)
        if _already_saved(merged, rotated):
            return True  # An earlier attempt committed before its error.
        if row["disabled"] or merged.get("refresh_token") != orig_refresh:
            return False  # Removed/re-linked credentials supersede this refresh.
        merged.update(rotated)
        if merge is not None:
            merge(merged)
        if cas(cid, row["extra"], secret, merged):
            return True
    # Serialize only the final merge, never the provider request. A row lock
    # prevents unrelated metadata writers from starving token persistence.
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM credentials WHERE id=? FOR UPDATE", (cid,)).fetchone()
        if row is None or row["disabled"]:
            return False
        merged = accounts.cred_extra(row)
        if _already_saved(merged, rotated):
            return True
        if merged.get("refresh_token") != orig_refresh:
            return False
        merged.update(rotated)
        if merge is not None:
            merge(merged)
        conn.execute("UPDATE credentials SET secret=?, extra=? WHERE id=?",
                     (secret, encrypt(json.dumps(merged)), cid))
    return True
