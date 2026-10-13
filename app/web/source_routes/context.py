"""Shared helpers for the /sources route modules: config parsing,
pending sign-in lookups, OAuth token validation and the pool account card."""

from __future__ import annotations

import json
import time
from ... import accounts, stats
from ...sources import grok as grok_mod
from ...sources import chatgpt as chatgpt_mod
from ...sources import devin as devin_mod
from ...sources import kimi as kimi_mod
from ...sources import claude as claude_mod
from ..deps import _chatgpt_pending, _devin_pending, _kimi_pending, _mimo_pending, _claude_pending, _pending, _PENDING_TTL

def _source_cfg(row) -> dict:
    try:
        data = json.loads((row["config"] if row else None) or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _pending_for(uid: int):
    data = _pending.get(uid)
    if not data:
        return None
    if time.time() - float(data.get("_started") or 0) > _PENDING_TTL:
        _pending.pop(uid, None)
        return None
    _clamp_interval(data)
    return data


def _chatgpt_pending_for(uid: int):
    data = _chatgpt_pending.get(uid)
    if not data:
        return None
    if time.time() - float(data.get("_started") or 0) > _PENDING_TTL:
        _chatgpt_pending.pop(uid, None)
        return None
    _clamp_interval(data)
    return data


def _devin_pending_for(uid: int):
    data = _devin_pending.get(uid)
    if not data:
        return None
    if time.time() - float(data.get("_started") or 0) > _PENDING_TTL:
        _devin_pending.pop(uid, None)
        return None
    return data


def _kimi_pending_for(uid: int):
    data = _kimi_pending.get(uid)
    if not data:
        return None
    if time.time() - float(data.get("_started") or 0) > _PENDING_TTL:
        _kimi_pending.pop(uid, None)
        return None
    _clamp_interval(data)
    return data


def _claude_pending_for(uid: int):
    data = _claude_pending.get(uid)
    if not data:
        return None
    if time.time() - float(data.get("_started") or 0) > _PENDING_TTL:
        _claude_pending.pop(uid, None)
        return None
    return data


def _mimo_pending_for(uid: int):
    data = _mimo_pending.get(uid)
    if not data:
        return None
    if time.time() - float(data.get("_started") or 0) > _PENDING_TTL:
        _mimo_pending.pop(uid, None)
        return None
    return data


def _clamp_interval(data: dict) -> None:
    """The auth server controls `interval`; keep poll backoffs inside 3-30s
    before the value reaches a client `retry_after` or the poll page."""
    try:
        data["interval"] = max(3, min(int(data.get("interval") or 5), 30))
    except (TypeError, ValueError):
        data["interval"] = 5


def _oauth_tokens(raw) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Token response was not an object.")
    access = raw.get("access_token")
    refresh = raw.get("refresh_token", "")
    expires = raw.get("expires_in")
    if (
        not isinstance(access, str)
        or not access
        or len(access) > 16_384
        or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in access)
    ):
        raise ValueError("Token response did not contain a valid access token.")
    if not isinstance(refresh, str) or len(refresh) > 16_384 or any(
        ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in refresh
    ):
        raise ValueError("Token response contained an invalid refresh token.")
    if expires in (None, ""):
        # Optional in OAuth (RFC 6749 5.1): the issued tokens are already
        # spent from the device code, so keep them; expires_at() applies the
        # provider default.
        return {**raw, "access_token": access, "refresh_token": refresh, "expires_in": None}
    if isinstance(expires, bool):
        raise ValueError("Token response contained an invalid expiry.")
    try:
        expires = int(expires)
    except (TypeError, ValueError):
        raise ValueError("Token response contained an invalid expiry.") from None
    if expires <= 0 or expires > 31_536_000:
        raise ValueError("Token response contained an invalid expiry.")
    return {**raw, "access_token": access, "refresh_token": refresh, "expires_in": expires}


# Terminal device-flow error codes, matched exactly: a transient message
# merely containing "expired" or "denied" must keep polling, not kill the
# pending sign-in.
_TERMINAL_OAUTH_ERRORS = frozenset(
    {
        "access_denied",
        "authorization_declined",
        "cancelled",
        "expired_token",
        "invalid_grant",
        "invalid_request",
        "unauthorized_client",
        "unsupported_grant_type",
    }
)


def _terminal_oauth_error(error: str) -> bool:
    return (error or "").strip().lower() in _TERMINAL_OAUTH_ERRORS


def _pool_card(user, row, *, kind: str, index: int, extra: dict | None = None) -> dict:
    mine = row["donated_by"] == user["id"]
    admin = bool(user["is_admin"])
    extra = extra if isinstance(extra, dict) else {}
    label = row["label"] or f"{kind} account"
    if not mine and not admin:
        label = f"{kind} plan {index}"
    email = ""
    if mine or admin:
        if kind == "ChatGPT":
            email = chatgpt_mod.account_email(extra)
        elif kind == "Grok":
            email = grok_mod.account_email(extra)
        elif kind == "Devin":
            email = devin_mod.account_email(extra)
        elif kind == "Kimi":
            email = kimi_mod.account_email(extra)
        elif kind == "Claude":
            email = claude_mod.account_email(extra)
        if not email and admin and row["donated_by"] and row["donated_by"] != user["id"]:
            donor = accounts.get_user(row["donated_by"])
            try:
                email = str((donor["email"] if donor is not None else "") or "").strip()
            except (KeyError, IndexError, TypeError):
                email = ""
    return {
        "id": row["id"],
        "label": label,
        "email": email,
        "mine": mine,
        "can_revoke": mine or admin,
        "show_billing": mine or admin,
        "fuse": stats.fuse_view(row),
    }
