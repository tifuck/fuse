"""Persist OAuth refresh results for pool credentials.

Providers rotate refresh tokens: once the token endpoint answers, the old
refresh token is dead. The new pair must reach the row even when `extra`
changed while the request was in flight, or the account is lost until the
donor re-links it.
"""

from __future__ import annotations

import logging
import json
from datetime import datetime, timedelta, timezone

from .. import accounts, db
from ..crypto import encrypt

logger = logging.getLogger(__name__)

CAS_ATTEMPTS = 5
_MAX_SPAN = 365 * 86400


def expires_at(seconds, default: int) -> str:
    """ISO expiry from a provider's expires_in; malformed values use default."""
    try:
        span = int(float(seconds)) if seconds not in (None, "") else default
    except (TypeError, ValueError, OverflowError):
        span = default
    span = max(60, min(span, _MAX_SPAN))
    return (datetime.now(timezone.utc) + timedelta(seconds=span)).strftime("%Y-%m-%dT%H:%M:%SZ")


def persist_rotated(cas, cred_id: int, old_extra_raw, secret: str, extra: dict, rotated: dict, *, merge=None) -> bool:
    """Write refreshed tokens with compare-and-swap.

    `cas(cred_id, old_raw, secret, new_extra) -> bool` is the source's
    conditional update. On a lost race the rotated fields (plus `merge`, a
    callback that folds other refresh results into the newest copy) are
    re-applied to the current row until the write lands.
    """
    cid = int(cred_id)
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
        if merged.get("refresh_token") != orig_refresh:
            return False
        merged.update(rotated)
        if merge is not None:
            merge(merged)
        conn.execute("UPDATE credentials SET secret=?, extra=? WHERE id=?",
                     (secret, encrypt(json.dumps(merged)), cid))
    return True
