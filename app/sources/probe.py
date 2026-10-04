from __future__ import annotations

import asyncio
import time

import httpx

from .. import accounts, db
from . import _BUILDERS, _row_source
from .chatgpt import credential_is_authed as chatgpt_credential_is_authed
from .grok import (
    billing_has_subscription,
    credential_is_authed,
    is_held,
    refresh_billing,
)
from .openrouter import fetch_catalog as openrouter_fetch_catalog
from .zai import (
    billing_has_subscription as zai_billing_has_subscription,
    credential_is_authed as zai_credential_is_authed,
    is_held as zai_is_held,
    refresh_quota as zai_refresh_quota,
)

TEST_PROMPT = "Reply with the single word pong."
TEST_MAX_TOKENS = 64
CHAT_TIMEOUT = 40.0


def _check(name: str, ok: bool, detail: str = "", *, skip: bool = False) -> dict:
    return {"name": name, "ok": ok, "detail": (detail or "")[:240], "skip": skip}


def _summary(names: list[str]) -> str:
    if not names:
        return "none"
    head = ", ".join(names[:3])
    extra = len(names) - 3
    return f"{len(names)} listed ({head}{'…' if extra > 0 else ''})"


def _reply_text(body: dict | None) -> str:
    if not isinstance(body, dict):
        return ""
    choices = body.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        msg = first.get("message") if isinstance(first.get("message"), dict) else {}
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return " ".join(content.split())[:160]
        reasoning = msg.get("reasoning_content") or msg.get("reasoning")
        if isinstance(reasoning, str) and reasoning.strip():
            return " ".join(reasoning.split())[:160]
        if first.get("finish_reason"):
            return f"empty (finish {first.get('finish_reason')})"
    err = body.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err)[:200]
    if isinstance(err, str) and err.strip():
        return err.strip()[:200]
    return ""


def _err(exc: BaseException) -> str:
    parts = []
    cur: BaseException | None = exc
    for _ in range(4):
        if cur is None:
            break
        text = str(cur).strip()
        if text and text not in parts:
            parts.append(text)
        cur = cur.__cause__ or getattr(cur, "__context__", None)
    detail = " — ".join(parts) if parts else type(exc).__name__
    return f"{type(exc).__name__}: {detail}"[:220]


def _tls_verify(src) -> bool:
    fn = getattr(src, "_verify", None)
    if callable(fn):
        return bool(fn())
    cfg = getattr(src, "cfg", None) or {}
    if isinstance(cfg, dict) and "verify" in cfg:
        return bool(cfg["verify"])
    return True


async def _http_get(
    url: str, headers: dict | None = None, *, verify: bool = True
) -> tuple[bool, str, int | None]:
    try:
        async with httpx.AsyncClient(timeout=8.0, verify=verify) as client:
            r = await client.get(url, headers=headers or {})
        return True, f"{url} → HTTP {r.status_code}", r.status_code
    except Exception as e:
        return False, f"{url} — {_err(e)}", None


def _auth_headers(src) -> dict:
    headers = getattr(src, "_headers", None)
    key_fn = getattr(src, "_key", None)
    if callable(headers):
        key = key_fn() if callable(key_fn) else ""
        try:
            return headers(key)
        except TypeError:
            return {}
    creds = list(getattr(src, "creds", []) or [])
    if not creds:
        return {}
    key = accounts.cred_secret(creds[0])
    return {"Authorization": f"Bearer {key}"} if key else {}


def _pick_model(src, names: list[str]) -> str:
    cfg = getattr(src, "cfg", None) or {}
    configured = str((cfg.get("model") if isinstance(cfg, dict) else "") or "").strip()
    if (
        configured
        and configured not in ("local", "llama")
        and (not names or configured in names)
    ):
        return configured[:120]
    if names:
        return names[0]
    return ""


def load(source_id: int):
    row = db.fetchone("SELECT * FROM sources WHERE id=?", (source_id,))
    if not row or row["type"] not in _BUILDERS:
        return None
    return _row_source(row)


async def probe_source(source_id: int) -> dict:
    started = time.perf_counter()
    src = load(source_id)
    if src is None:
        return {"ok": False, "error": "Unknown source", "checks": [], "ms": 0}
    if src.type_id == "grok":
        checks = await _probe_grok(src)
    elif src.type_id == "chatgpt":
        checks = await _probe_chatgpt(src)
    elif src.type_id == "zai":
        checks = await _probe_zai(src)
    elif src.type_id == "kimi":
        checks = await _probe_kimi(src)
    elif src.type_id == "mimo":
        checks = await _probe_mimo(src)
    elif src.type_id == "claude":
        checks = await _probe_claude(src)
    elif src.type_id == "explabs":
        checks = [_check("Personal keys", True, "Validated when each user connects a key; no shared-key or completion probe.", skip=True)]
    elif src.type_id == "openrouter":
        checks = await _probe_openrouter(src)
    else:
        checks = await _probe_chat_source(src)
    ms = int((time.perf_counter() - started) * 1000)
    ok = all(c["ok"] for c in checks if not c.get("skip"))
    return {"ok": ok, "checks": checks, "ms": ms}


async def _probe_chatgpt(src) -> list[dict]:
    """Auth and catalog only. Never send a ChatGPT completion as a probe."""
    checks: list[dict] = []
    creds = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND disabled=0", (src.source_id,)
    )
    if not creds:
        checks.append(_check("Accounts", False, "No ChatGPT accounts linked"))
        checks.append(
            _check(
                "Chat",
                True,
                "Skipped — Fuse does not send ChatGPT completions as a probe.",
                skip=True,
            )
        )
        return checks
    checks.append(_check("Accounts", True, f"{len(creds)} linked"))

    authed = 0
    for cred in creds:
        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],))
        if not row:
            continue
        extra = accounts.cred_extra(row)
        if chatgpt_credential_is_authed(row, extra):
            authed += 1

    checks.append(
        _check("Auth", authed > 0, f"{authed} of {len(creds)} still authed" if authed else "No live token")
    )
    checks.append(_check("Pool", authed > 0, f"{authed} usable" if authed else "No usable account"))

    try:
        names = await asyncio.wait_for(src.models(), timeout=20)
    except Exception:
        names = []
        checks.append(_check("Catalog", False, "Could not list models"))
    else:
        checks.append(_check("Catalog", bool(names), _summary(names) if names else "No models listed"))

    checks.append(
        _check(
            "Chat",
            True,
            "Skipped — Fuse does not send ChatGPT completions as a probe.",
            skip=True,
        )
    )
    return checks


async def _probe_grok(src) -> list[dict]:
    """Auth, billing, and catalog only. Never send a Grok completion."""
    checks: list[dict] = []
    creds = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND disabled=0", (src.source_id,)
    )
    if not creds:
        checks.append(_check("Accounts", False, "No Grok accounts linked"))
        checks.append(
            _check(
                "Chat",
                True,
                "Skipped — Fuse does not send Grok completions as a probe.",
                skip=True,
            )
        )
        return checks
    checks.append(_check("Accounts", True, f"{len(creds)} linked"))

    authed = 0
    subscribed = 0
    usable = 0
    for cred in creds:
        try:
            await refresh_billing(cred, force=True)
        except Exception:
            pass
        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],))
        if not row:
            continue
        extra = accounts.cred_extra(row)
        if credential_is_authed(row, extra):
            authed += 1
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
        if billing_has_subscription(billing):
            subscribed += 1
        if credential_is_authed(row, extra) and not is_held(row):
            usable += 1

    checks.append(
        _check("Auth", authed > 0, f"{authed} of {len(creds)} still authed" if authed else "No live token")
    )
    checks.append(
        _check(
            "Subscription",
            subscribed > 0,
            f"{subscribed} with an active SuperGrok sub" if subscribed else "No active SuperGrok subscription",
        )
    )
    checks.append(
        _check(
            "Pool",
            usable > 0,
            f"{usable} usable" if usable else "Every account is held or unusable",
        )
    )

    try:
        names = await asyncio.wait_for(src.models(), timeout=20)
    except Exception as e:
        names = []
        checks.append(_check("Catalog", False, f"Could not list models ({type(e).__name__})"))
    else:
        checks.append(_check("Catalog", bool(names), _summary(names) if names else "No models listed"))

    checks.append(
        _check(
            "Chat",
            True,
            "Skipped — Fuse does not send Grok completions as a probe.",
            skip=True,
        )
    )
    return checks


async def _probe_zai(src) -> list[dict]:
    """Auth, quota, and catalog only. Never send a GLM completion."""
    checks: list[dict] = []
    creds = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND disabled=0", (src.source_id,)
    )
    if not creds:
        checks.append(_check("Accounts", False, "No Z.AI keys linked"))
        checks.append(
            _check(
                "Chat",
                True,
                "Skipped — Fuse does not send Z.AI completions as a probe.",
                skip=True,
            )
        )
        return checks
    checks.append(_check("Accounts", True, f"{len(creds)} linked"))

    authed = 0
    subscribed = 0
    usable = 0
    for cred in creds:
        try:
            await zai_refresh_quota(cred, force=True)
        except Exception:
            pass
        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],))
        if not row:
            continue
        extra = accounts.cred_extra(row)
        if zai_credential_is_authed(row, extra):
            authed += 1
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
        if zai_billing_has_subscription(billing):
            subscribed += 1
        if zai_credential_is_authed(row, extra) and not zai_is_held(row):
            usable += 1

    checks.append(
        _check("Auth", authed > 0, f"{authed} of {len(creds)} still authed" if authed else "No live key")
    )
    checks.append(
        _check(
            "Subscription",
            subscribed > 0,
            f"{subscribed} with a live Coding Plan" if subscribed else "No GLM Coding Plan quota",
        )
    )
    checks.append(
        _check(
            "Pool",
            usable > 0,
            f"{usable} usable" if usable else "Every key is held or unusable",
        )
    )

    try:
        names = await asyncio.wait_for(src.models(), timeout=20)
    except Exception as e:
        names = []
        checks.append(_check("Catalog", False, f"Could not list models ({type(e).__name__})"))
    else:
        checks.append(_check("Catalog", bool(names), _summary(names) if names else "No models listed"))

    checks.append(
        _check(
            "Chat",
            True,
            "Skipped — Fuse does not send Z.AI completions as a probe.",
            skip=True,
        )
    )
    return checks


async def _probe_kimi(src) -> list[dict]:
    """Auth, quota, and catalog only. Never send a Kimi completion."""
    from .kimi import (
        billing_has_subscription as kimi_billing_has_subscription,
        credential_is_authed as kimi_credential_is_authed,
        is_held as kimi_is_held,
        refresh_quota as kimi_refresh_quota,
    )

    checks: list[dict] = []
    creds = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND disabled=0", (src.source_id,)
    )
    if not creds:
        checks.append(_check("Accounts", False, "No Kimi accounts linked"))
        checks.append(
            _check(
                "Chat",
                True,
                "Skipped — Fuse does not send Kimi completions as a probe.",
                skip=True,
            )
        )
        return checks
    checks.append(_check("Accounts", True, f"{len(creds)} linked"))

    authed = 0
    subscribed = 0
    usable = 0
    for cred in creds:
        try:
            await kimi_refresh_quota(cred, force=True)
        except Exception:
            pass
        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],))
        if not row:
            continue
        extra = accounts.cred_extra(row)
        if kimi_credential_is_authed(row, extra):
            authed += 1
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
        if kimi_billing_has_subscription(billing):
            subscribed += 1
        if kimi_credential_is_authed(row, extra) and not kimi_is_held(row):
            usable += 1

    checks.append(
        _check("Auth", authed > 0, f"{authed} of {len(creds)} still authed" if authed else "No live token")
    )
    checks.append(
        _check(
            "Subscription",
            subscribed > 0,
            f"{subscribed} with a live membership" if subscribed else "No Kimi membership quota",
        )
    )
    checks.append(
        _check(
            "Pool",
            usable > 0,
            f"{usable} usable" if usable else "Every account is held or unusable",
        )
    )

    try:
        names = await asyncio.wait_for(src.models(), timeout=20)
    except Exception as e:
        names = []
        checks.append(_check("Catalog", False, f"Could not list models ({type(e).__name__})"))
    else:
        checks.append(_check("Catalog", bool(names), _summary(names) if names else "No models listed"))

    checks.append(
        _check(
            "Chat",
            True,
            "Skipped — Fuse does not send Kimi completions as a probe.",
            skip=True,
        )
    )
    return checks


async def _probe_claude(src) -> list[dict]:
    """Profile only. Never send a Claude completion."""
    from .claude import (
        billing_has_subscription as claude_billing_has_subscription,
        credential_is_authed as claude_credential_is_authed,
        is_held as claude_is_held,
        refresh_quota as claude_refresh_quota,
    )

    checks: list[dict] = []
    creds = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND disabled=0", (src.source_id,)
    )
    if not creds:
        checks.append(_check("Accounts", False, "No Claude accounts linked"))
        checks.append(
            _check("Chat", True, "Skipped — Fuse does not send Claude completions as a probe.", skip=True)
        )
        return checks
    checks.append(_check("Accounts", True, f"{len(creds)} linked"))
    authed = 0
    subscribed = 0
    usable = 0
    for cred in creds:
        try:
            await claude_refresh_quota(cred, force=True)
        except Exception:
            pass
        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],))
        if not row:
            continue
        extra = accounts.cred_extra(row)
        if claude_credential_is_authed(row, extra):
            authed += 1
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
        if claude_billing_has_subscription(billing):
            subscribed += 1
        if claude_credential_is_authed(row, extra) and not claude_is_held(row):
            usable += 1
    checks.append(_check("Auth", authed > 0, f"{authed} of {len(creds)} still authed" if authed else "No live token"))
    checks.append(
        _check(
            "Subscription",
            subscribed > 0,
            f"{subscribed} with a live subscription" if subscribed else "No Claude subscription on file",
        )
    )
    checks.append(_check("Pool", usable > 0, f"{usable} usable" if usable else "Every account is held or unusable"))
    try:
        names = await asyncio.wait_for(src.models(), timeout=20)
    except Exception as e:
        names = []
        checks.append(_check("Catalog", False, f"Could not list models ({type(e).__name__})"))
    else:
        checks.append(_check("Catalog", bool(names), _summary(names) if names else "No models listed"))
    checks.append(
        _check("Chat", True, "Skipped — Fuse does not send Claude completions as a probe.", skip=True)
    )
    return checks


async def _probe_mimo(src) -> list[dict]:
    """Catalog only. Never send a MiMo completion."""
    from .mimo import (
        billing_has_subscription as mimo_billing_has_subscription,
        credential_is_authed as mimo_credential_is_authed,
        is_held as mimo_is_held,
        refresh_quota as mimo_refresh_quota,
    )

    checks: list[dict] = []
    creds = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND disabled=0", (src.source_id,)
    )
    if not creds:
        checks.append(_check("Accounts", False, "No Xiaomi keys linked"))
        checks.append(
            _check("Chat", True, "Skipped — Fuse does not send MiMo completions as a probe.", skip=True)
        )
        return checks
    checks.append(_check("Accounts", True, f"{len(creds)} linked"))
    authed = 0
    subscribed = 0
    usable = 0
    for cred in creds:
        try:
            await mimo_refresh_quota(cred, force=True)
        except Exception:
            pass
        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],))
        if not row:
            continue
        extra = accounts.cred_extra(row)
        if mimo_credential_is_authed(row, extra):
            authed += 1
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
        if mimo_billing_has_subscription(billing):
            subscribed += 1
        if mimo_credential_is_authed(row, extra) and not mimo_is_held(row):
            usable += 1
    checks.append(_check("Auth", authed > 0, f"{authed} of {len(creds)} still authed" if authed else "No live key"))
    checks.append(
        _check(
            "Subscription",
            subscribed > 0,
            f"{subscribed} with a live Token Plan" if subscribed else "No Token Plan",
        )
    )
    checks.append(_check("Pool", usable > 0, f"{usable} usable" if usable else "Every key is held or unusable"))
    try:
        names = await asyncio.wait_for(src.models(), timeout=20)
    except Exception as e:
        names = []
        checks.append(_check("Catalog", False, f"Could not list models ({type(e).__name__})"))
    else:
        checks.append(_check("Catalog", bool(names), _summary(names) if names else "No models listed"))
    checks.append(_check("Chat", True, "Skipped — Fuse does not send MiMo completions as a probe.", skip=True))
    return checks


async def _probe_openrouter(src) -> list[dict]:
    """Catalog only. Never send an OpenRouter completion as a probe."""
    checks: list[dict] = []
    user_n = db.fetchone(
        """SELECT COUNT(*) AS n FROM credentials
           WHERE source_id=? AND disabled=0 AND donated_by IS NOT NULL""",
        (src.source_id,),
    )
    user_count = int(user_n["n"] if user_n else 0)
    checks.append(
        _check(
            "User keys",
            True,
            f"{user_count} personal key{'s' if user_count != 1 else ''}",
            skip=True,
        )
    )
    try:
        catalog = await asyncio.wait_for(openrouter_fetch_catalog(force=True), timeout=20)
    except Exception as e:
        checks.append(_check("Catalog", False, f"Could not list models ({type(e).__name__})"))
    else:
        n = len(catalog.get("all_ids") or [])
        checks.append(_check("Catalog", n > 0, f"{n} models"))
    checks.append(
        _check(
            "Chat",
            True,
            "Skipped — Fuse does not send OpenRouter completions as a probe.",
            skip=True,
        )
    )
    return checks


async def _probe_chat_source(src) -> list[dict]:
    checks: list[dict] = []
    cfg = getattr(src, "cfg", None) or {}
    base = ""
    if isinstance(cfg, dict):
        base = str(cfg.get("base_url") or "")
    if src.type_id == "ollama":
        base = getattr(src, "base", None) or base or "https://ollama.com/api"
    if src.type_id == "llama":
        base = getattr(src, "_url", lambda: base)()
    if src.type_id == "openai":
        base = getattr(src, "_url", lambda: base)()
    if not base:
        checks.append(_check("Reach", False, "No base URL"))
        return checks

    creds = list(getattr(src, "creds", []) or [])
    headers = _auth_headers(src)
    if src.type_id == "ollama":
        ping = base.rstrip("/") + "/tags"
    else:
        ping = base.rstrip("/") + "/models"
    reached, reach_detail, status = await _http_get(ping, headers, verify=_tls_verify(src))
    checks.append(_check("Reach", reached, reach_detail))
    if not reached:
        return checks

    if src.type_id == "llama":
        checks.append(_check("Key", True, "Set" if creds else "Open (no upstream key)"))
    elif not creds:
        label = "Ollama Cloud keys" if src.type_id == "ollama" else "Upstream keys"
        checks.append(_check("Keys", False, f"No {label} configured"))
        return checks
    else:
        extra = ""
        pool = getattr(src, "pool", None)
        if pool is not None:
            for s in pool.states.values():
                s.refresh_state()
            ready = sum(1 for s in pool.states.values() if s.state in ("active", "resting"))
            extra = f" · {ready} ready"
        checks.append(_check("Keys", True, f"{len(creds)} configured{extra}"))

    try:
        names = await asyncio.wait_for(src.models(), timeout=20)
    except Exception as e:
        names = []
        checks.append(_check("Models", False, _err(e)))
    else:
        if names:
            checks.append(_check("Models", True, _summary(names)))
        elif status and status != 200:
            checks.append(_check("Models", False, f"HTTP {status}"))
        else:
            checks.append(_check("Models", False, "No models listed"))

    model = _pick_model(src, names)
    if not model:
        checks.append(_check("Chat", False, "No model to call"))
        return checks

    body = {
        "model": model,
        "messages": [{"role": "user", "content": TEST_PROMPT}],
        "stream": False,
        "max_tokens": TEST_MAX_TOKENS,
        "temperature": 0,
    }
    try:
        result = await asyncio.wait_for(src.chat(body), timeout=CHAT_TIMEOUT)
    except asyncio.TimeoutError:
        checks.append(_check("Chat", False, f"{model} timed out after {int(CHAT_TIMEOUT)}s"))
        return checks
    except Exception as e:
        checks.append(_check("Chat", False, f"{model}: {_err(e)}"))
        return checks

    if result.stream is not None:
        try:
            async for _ in result.stream:
                pass
        except Exception:
            pass
        checks.append(_check("Chat", False, f"{model}: unexpected stream"))
        return checks

    if result.status != 200:
        detail = _reply_text(result.body) or f"HTTP {result.status}"
        checks.append(_check("Chat", False, f"{model}: {detail}"))
        return checks

    text = _reply_text(result.body)
    if not text or text.startswith("empty "):
        checks.append(_check("Chat", False, f"{model}: empty reply"))
        return checks
    checks.append(_check("Chat", True, f"{model} → {text}"))
    return checks
