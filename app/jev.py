"""TypeSafe Jev decisions via OpenRouter. Not a chat model.

Used for permission auto-approve: clip thread state, ask atomic Noul
questions, compose the yes/no in code. Fail closed on any miss.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from . import chats, db

logger = logging.getLogger(__name__)

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.13"
TIMEOUT_S = 2.0
# Bound concurrent Jev calls instead of serializing every user's decision behind
# one global lock: a slow upstream must not stall other runs' permission reviews.
GATE_CONCURRENCY = 4
_gate_sem: asyncio.Semaphore | None = None


def _gate() -> asyncio.Semaphore:
    global _gate_sem
    if _gate_sem is None:
        _gate_sem = asyncio.Semaphore(GATE_CONCURRENCY)
    return _gate_sem
DANGEROUS_CEILING = 0.40
USER_CLIP = 2_000
RECENT_TEXT = 400
RECENT_PREVIEW = 200
RECENT_MAX = 12
COMMAND_CLIP = 800

# The coding agent's own command, judged whole: a clipped command could hide
# its dangerous tail past the cut, so a longer one asks instead.
ACTION_CLIP = 4_000

QUESTIONS = {
    "dangerous": {
        "type": "noul",
        "instructions": (
            "Is `action` dangerous: could it destroy data, make an irreversible or system-wide change, "
            "or expose critical secrets? Judge only the harm of this one action, not whether it matches "
            "the user's task. Most coding-agent actions are routine and not dangerous."
        ),
        "criteria": {
            "true": (
                "Deletes or overwrites data beyond ordinary project edits (recursive deletes, git clean, "
                "git reset --hard, dropping databases), wipes disks, force-pushes or rewrites shared history, "
                "changes system configuration, services, users, or permissions, or pipes a downloaded script "
                "into a shell. Or exposes critical secrets: prints, copies, or uploads credentials, API keys, "
                "tokens, private keys, password stores, or environment variables, or sends local files to a "
                "remote host."
            ),
            "false": (
                "Reading, listing, or searching files; running tests, builds, linters, or project scripts; "
                "git status, diff, log, add, commit, pull, or a normal push; installing project dependencies; "
                "creating or editing project files; HTTP GET requests. Listing /home or a sibling folder is "
                "not dangerous."
            ),
        },
    },
}

def user_can_auto(user_id: int | None) -> bool:
    if not user_id:
        return False
    try:
        from .sources import openrouter as openrouter_mod

        return bool(openrouter_mod.user_has_openrouter(int(user_id)))
    except Exception:
        logger.warning("Jev key lookup failed", exc_info=True)
        return False


def looks_destructive(command: str) -> bool:
    from .permissions import _destructive_command

    return _destructive_command(command)


def _noul(answers: dict, key: str) -> float | None:
    rec = answers.get(key) if isinstance(answers, dict) else None
    if not isinstance(rec, dict):
        return None
    raw = rec.get("noul")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if value != value or value < 0.0 or value > 1.0:
        return None
    return value


def should_allow(answers: dict) -> bool:
    """Allow unless Jev says the action is dangerous. Missing answer → ask."""
    dangerous = _noul(answers, "dangerous")
    if dangerous is None:
        return False
    return dangerous < DANGEROUS_CEILING


def clip_thread_state(user_id: int, thread_id: int, action: dict, *, roots: list[str] | None = None) -> dict:
    """Small structured state. Full transcripts rot Jev 1.13 accuracy."""
    row = db.fetchone(
        "SELECT messages FROM chat_threads WHERE id=? AND user_id=?",
        (int(thread_id), int(user_id)),
    )
    messages = chats.parse_messages(row["messages"]) if row else []
    user_request = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            user_request = str(msg.get("content") or "").strip()[:USER_CLIP]
            break
    recent: list[dict] = []
    for msg in messages[-8:]:
        role = msg.get("role")
        if role == "user":
            text = str(msg.get("content") or "").strip()
            if text:
                recent.append({"role": "user", "text": text[:RECENT_TEXT]})
        elif role == "assistant":
            text = str(msg.get("content") or "").strip()
            if text:
                recent.append({"role": "assistant", "text": text[:RECENT_TEXT]})
            for tool in msg.get("tools") or []:
                if not isinstance(tool, dict):
                    continue
                rec = {
                    "role": "tool",
                    "name": str(tool.get("name") or "")[:40],
                    "ok": tool.get("status") not in ("error", "interrupted"),
                }
                path = str(tool.get("path") or "").strip()
                if path:
                    rec["path"] = path[:400]
                preview = str(tool.get("preview") or tool.get("summary") or "").strip()
                if preview:
                    rec["preview"] = preview[:RECENT_PREVIEW]
                command = str(tool.get("command") or "").strip()
                if command:
                    rec["command"] = command[:COMMAND_CLIP]
                recent.append(rec)
    act = dict(action or {})
    command = str(act.get("command") or "")
    if command:
        act["command"] = command[:ACTION_CLIP]
    return {
        "user_request": user_request,
        "recent": recent[-RECENT_MAX:],
        "action": act,
        "allowed_roots": [str(r) for r in (roots or []) if r][:8],
    }


async def evaluate(user_id: int, state: dict, questions: dict | None = None, timeout: float = TIMEOUT_S):
    """POST /api/alpha/decisions with the user's OpenRouter key. None on any failure."""
    try:
        from . import chat_runs as chat_runs_mod
        from .sources import openrouter as openrouter_mod

        if getattr(chat_runs_mod, "_stopping", False):
            return None

        picked = openrouter_mod.pick_key_for(int(user_id))
        if not picked:
            return None
        _cred, _extra, key, _via = picked
        if not key:
            return None
        body = {
            "model": MODEL,
            "state": state,
            "questions": questions or QUESTIONS,
        }
        timeout_s = max(0.4, min(float(timeout), 2.0))
        async with _gate():
            if getattr(chat_runs_mod, "_stopping", False):
                return None
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=min(0.8, timeout_s))) as client:
                response = await asyncio.wait_for(
                    client.post(DECISIONS_URL, headers=openrouter_mod._headers(key), json=body),
                    timeout=timeout_s + 0.2,
                )
        if response.status_code != 200:
            logger.warning("Jev HTTP %s body=%s", response.status_code, (response.text or "")[:300])
            return None
        payload = response.json()
        if not isinstance(payload, dict):
            logger.warning("Jev non-object payload")
            return None
        answers = payload.get("answers")
        if not isinstance(answers, dict) or payload.get("error"):
            logger.warning("Jev rejected payload status=%s error=%s", response.status_code, payload.get("error"))
            return None
        dangerous = answers.get("dangerous") if isinstance(answers.get("dangerous"), dict) else {}
        logger.warning("Jev ok dangerous=%s model=%s", dangerous.get("noul"), payload.get("model"))
        return payload
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("Jev request failed", exc_info=True)
        return None


async def judge(user_id: int, thread_id: int, action: dict, *, roots: list[str] | None = None) -> bool:
    """True only when Jev is confident the action is a safe auto-allow."""
    try:
        if len(str((action or {}).get("command") or "")) > ACTION_CLIP:
            logger.warning("Jev skip long command tool=%s", (action or {}).get("tool"))
            return False
        # Destructive-pattern hits are not a verdict: they ride along as an
        # advisory reason and Jev decides. A regex refusal here would make
        # auto mode card on every sudo/rm instead of judging it.
        if not user_can_auto(user_id):
            logger.warning("Jev skip no OpenRouter key user=%s", user_id)
            return False
        state = clip_thread_state(user_id, thread_id, action, roots=roots)
        result = await evaluate(user_id, state)
        if not result:
            logger.warning("Jev no result tool=%s path=%s", (action or {}).get("tool"), (action or {}).get("path"))
            return False
        allowed = should_allow(result.get("answers") or {})
        logger.warning("Jev judge tool=%s path=%s allowed=%s", (action or {}).get("tool"), (action or {}).get("path") or (action or {}).get("cwd"), allowed)
        return allowed
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("Jev judge failed", exc_info=True)
        return False
