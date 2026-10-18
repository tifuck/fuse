"""Provider-failure classification and resume policy, plus the
per-(source, upstream) capability cache.

Decides which failures are worth re-running with the same context
(429/5xx/dropped streams/timeouts) and which are terminal (auth,
plan gating, context overflow). Also owns the wire error frames and
the resume notes folded back into the request."""

from __future__ import annotations

import re
import time


_TOOLS_UNSUPPORTED = re.compile(
    r"does not support (tools|function.?call)"
    r"|unknown (parameter|field) ['\"]tools"
    r"|['\"]tools['\"] is not (allowed|supported)"
    r"|invalid tools\b"
    r"|failed to (parse|initialize).{0,20}(grammar|sampler)",
    re.I,
)


# Idle timeouts (no data frames) resume twice, then fail. Other 5xx/429
# still use MAX_RESUME_RETRIES. Keeps a keep-alive-only stream from
# occupying a child for tens of minutes.
IDLE_STALL_RETRIES = 2


# Provider-failure resume: how many times one turn may re-run a round whose
# upstream request or stream died in a transient way, and how long to wait
# between attempts. The round does not advance the completion budget. Four
# retries after the first failure (five attempts total) with exponential
# backoff; Retry-After, when present, can stretch a single wait up to the cap.
MAX_RESUME_RETRIES = 4
_RESUME_BACKOFF = (2.0, 6.0, 15.0, 40.0)
_RESUME_MAX_WAIT = 60.0
_RESUME_NOTE = (
    "[Fuse] The previous response was interrupted by a temporary provider error. "
    "Continue from exactly where the interrupted text above stops. Do not repeat or "
    "rephrase anything already delivered, and do not re-run tool calls whose results "
    "already appear in this conversation. Server-added notice, not user input."
)
_RESUME_TOOLS_NOTE = (
    "[Fuse] The previous response was interrupted by a temporary provider error after "
    "the tool results above. Continue the interrupted work from those results; do not "
    "re-run tool calls that already have results. Server-added notice, not user input."
)


_RESUMABLE_MESSAGE = re.compile(
    r"upstream (stream failed|timeout|transport failure)"
    r"|no upstream keys"
    r"|stream ended without a terminal"
    r"|timed? ?out"
    r"|idle timeout"
    r"|temporar"
    r"|unavailable"
    r"|overloaded"
    r"|try again later"
    r"|high demand"
    r"|at capacity"
    r"|server.?busy"
    r"|please try again",
    re.I,
)


# Short assistant text that is actually a provider failure painted as content
# (OpenAI-compat proxies do this with the classic overloaded sentence).
# Anchored to the whole reply so a real report that mentions "overloaded"
# is not treated as a transport failure.
# One sentence, then only retry boilerplate: a longer reply that opens
# with "The server was overloaded" is a report, not a failure.
_ERROR_TAIL = r"[^.!?\n]{0,80}[.!?]?(?:\s*(?:please try again|sorry about that|you can retry)[^.!?\n]{0,80}[.!?]?)*"
_PROVIDER_CONTENT_ERROR = re.compile(
    r"^\s*(?:error:\s*)?(?:"
    r"our servers? are currently overloaded\.?(?:\s*please try again later\.?)?"
    r"|the (?:server|engine|service) (?:is|was|had) (?:currently )?(?:overloaded|busy|unavailable|an error)" + _ERROR_TAIL +
    r"|please try again later\.?"
    r"|the service is temporarily unable to process your request" + _ERROR_TAIL +
    r"|server(?:s)? (?:are|is) (?:busy|at capacity)" + _ERROR_TAIL +
    r"|overloaded\.?\s*please try again later\.?"
    r")\s*$",
    re.I,
)


_CHILD_PROVIDER_FAILURE = (
    "The subagent experienced difficulties talking to the model{attempts}"
    "{detail}. No further work was done after that failure. Continue the parent task "
    "without this child's results, or spawn_subagent again with task_id to continue "
    "this subagent."
)


_TERMINAL_MESSAGE = re.compile(
    r"rejected the configured (account|api key)"
    r"|requires a (paid|subscription)"
    r"|context length"
    r"|invalid api key"
    # Devin's dead-SKU breaker (devin_catalog._sku_unavailable_message): the
    # same model and effort fail instantly for the next 30 minutes or more.
    r"|is temporarily unavailable on devin",
    re.I,
)


# api.invoke_chat wraps every source-adapter exception in this exact 502
# message. Those are local bugs (serialization, TypeError), not upstream
# conditions: retrying the identical payload fails identically and only
# burns the resume budget and backoff.
_LOCAL_ADAPTER_FAILURE = re.compile(r"^upstream request failed \([A-Za-z_][\w.]*\)$")


_capability: dict[tuple[str, str], tuple[float, str]] = {}
_CAP_TTL = 3600.0


def _model_type_guess(model: str) -> str | None:
    mid = (model or "").strip()
    if mid.startswith("explabs/"):
        return "explabs"
    if mid.startswith("openrouter/"):
        return "openrouter"
    if mid.startswith("devin/"):
        return "devin"
    if mid.startswith("grok"):
        return "grok"
    if mid.startswith("gpt-"):
        return "chatgpt"
    if mid.startswith("glm-"):
        return "zai"
    if mid.startswith("kimi-") or mid == "k3" or mid.startswith("k3-"):
        return "kimi"
    if mid.startswith("cursor/"):
        return "cursor"
    return None


def _error_obj(status: int, body) -> dict:
    from .. import api

    if status == 401:
        status = 502
    msg = api._error_message(body) or "upstream error"
    err_type = "access_denied" if status in (401, 402, 403) else "api_error"
    return {"error": {"message": msg, "type": err_type, "code": status if 400 <= status <= 599 else 502}}


def _resumable_failure(code: int, message: str) -> bool:
    """True when a failed round is worth re-running with the same context.

    Transient upstream conditions (429, 5xx, dropped streams, timeouts, key
    pools that were briefly exhausted) are resumable. Definitive rejections —
    auth, plan gating, context overflow, malformed requests — are not: the
    retry would burn quota and fail identically.
    """
    if _TERMINAL_MESSAGE.search(message or ""):
        return False
    if _LOCAL_ADAPTER_FAILURE.match((message or "").strip()):
        return False
    if isinstance(code, int) and (code == 429 or 500 <= code <= 599):
        return True
    return bool(_RESUMABLE_MESSAGE.search(message or ""))


def _should_resume(code: int, message: str, *, resumes_left: int, idle_stalls: int) -> bool:
    if resumes_left <= 0:
        return False
    if not _resumable_failure(code, message):
        return False
    if _is_idle_timeout(message) and idle_stalls > IDLE_STALL_RETRIES:
        return False
    return True


def _error_code_and_message(raw) -> tuple[int, str]:
    """Pull (code, message) out of the various provider error shapes.

    OpenAI-compat servers disagree: nested `{"error": {"message", "code"}}`,
    a bare string `{"error": "..."}`, or `_error_obj` with an int code.
    """
    from .. import api

    if raw is None:
        return 0, ""
    if isinstance(raw, str):
        return 0, raw.strip()[:400]
    if not isinstance(raw, dict):
        return 0, str(raw)[:400]
    inner = raw.get("error")
    msg = api._error_message(raw)
    if not msg and isinstance(inner, str):
        msg = inner.strip()[:400]
    code = None
    for obj in (inner if isinstance(inner, dict) else None, raw):
        if not isinstance(obj, dict):
            continue
        for key in ("code", "status", "status_code"):
            val = obj.get(key)
            if isinstance(val, bool):
                continue
            if isinstance(val, int):
                code = val
                break
            if isinstance(val, str) and val.isdigit():
                code = int(val)
                break
        if code is not None:
            break
    return (code if isinstance(code, int) else 0), msg or ""


def _provider_error_content(text: str) -> bool:
    """True when assistant text is a short provider-failure sentence, not a report."""
    t = (text or "").strip()
    if not t or len(t) > 400:
        return False
    return bool(_PROVIDER_CONTENT_ERROR.match(t))


def _header_retry_after(headers) -> float:
    if not isinstance(headers, dict):
        return 0.0
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if not raw:
        return 0.0
    try:
        return max(0.0, float(str(raw).strip()))
    except (TypeError, ValueError):
        return 0.0


def _resume_delay(attempt_no: int, retry_after: float = 0.0, backoff=_RESUME_BACKOFF) -> float:
    """Backoff for resume attempt `attempt_no` (1-based), honoring Retry-After."""
    idx = min(max(int(attempt_no), 1) - 1, len(backoff) - 1)
    base = backoff[idx]
    extra = min(max(float(retry_after or 0.0), 0.0), _RESUME_MAX_WAIT)
    return min(max(base, extra), _RESUME_MAX_WAIT)


def _as_error_frame(raw, fallback_status: int = 502) -> dict:
    code, msg = _error_code_and_message(raw)
    return _error_obj(code or fallback_status, {"error": {"message": msg or "upstream error"}})


def _exhausted_provider_error(is_child: bool, raw, fallback_status: int = 502, *, attempts: int | None = None) -> dict:
    """Wire error after the resume budget is spent, or for a definitive failure.

    `attempts` is how many retries this step actually made; a definitive
    failure (auth, plan gating) made none and must not claim otherwise.
    Children get a parent-facing wrap.
    """
    frame = _as_error_frame(raw, fallback_status)
    if not is_child:
        return frame
    _code, msg = _error_code_and_message(frame)
    detail = f" ({msg})" if msg else ""
    tried = MAX_RESUME_RETRIES if attempts is None else max(0, int(attempts))
    wrapped = _CHILD_PROVIDER_FAILURE.format(
        attempts=f" after {tried} {'retry' if tried == 1 else 'retries'}" if tried else "", detail=detail)
    return _error_obj(_code or fallback_status or 503, {"error": {"message": wrapped}})


def _promote_provider_content_error(summary: dict) -> dict:
    """Treat a 200 that is only an overload sentence as a failed round."""
    if not isinstance(summary, dict) or summary.get("error"):
        return summary
    if summary.get("calls"):
        return summary
    content = str(summary.get("content") or "")
    finish = str(summary.get("finish") or "").lower()
    if finish in ("error", "network_error") or _provider_error_content(content):
        msg = content.strip() or "upstream error"
        summary = dict(summary)
        summary["error"] = _error_obj(503, {"error": {"message": msg}})
        summary["content"] = ""
    return summary


def _apply_resume_state(messages: list[dict], partial: str) -> None:
    """Fold an interrupted round's partial output + a resume note into the request.

    The client already displayed `partial`, so it stays: it enters the request
    as an assistant message and the model is told to continue exactly where it
    stopped. With no partial text, a note is only needed when tool results are
    waiting; a bare first-round failure just retries the identical payload.
    """
    text = (partial or "").strip()
    if text:
        messages.append({"role": "assistant", "content": partial})
        messages.append({"role": "user", "content": _RESUME_NOTE})
    elif messages and messages[-1].get("role") == "tool":
        messages.append({"role": "user", "content": _RESUME_TOOLS_NOTE})


def _is_idle_timeout(message: str) -> bool:
    text = (message or "").lower()
    return "idle" in text and "timeout" in text


def _cap_get(src_type: str, upstream: str) -> str | None:
    rec = _capability.get((src_type, upstream))
    if not rec:
        return None
    expires, value = rec
    if expires <= time.monotonic():
        _capability.pop((src_type, upstream), None)
        return None
    return value


def _cap_set(src_type: str, upstream: str, value: str) -> None:
    if len(_capability) > 4096:
        # Bound the cache: entries are normally few, but a long-lived process
        # churning models would otherwise grow it without eviction.
        now = time.monotonic()
        for key in [k for k, (expires, _v) in _capability.items() if expires <= now]:
            _capability.pop(key, None)
    _capability[(src_type, upstream)] = (time.monotonic() + _CAP_TTL, value)
