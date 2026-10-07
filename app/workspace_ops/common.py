"""Shared primitives for the workspace tool modules: the WorkspaceError
surface, tool-argument parsing, host-path quoting, and agent-exec failure
shaping. No transport calls, no state.

Moved verbatim from app/workspace.py (see sourcemap in the work dir); the
facade imports these back as its stable surface.
"""
from __future__ import annotations

import json
import shlex

from ..hosts import HostError


class WorkspaceError(HostError):
    def __init__(self, detail: str, status_code: int = 400, *, extra: dict | None = None):
        super().__init__(detail, status_code)
        self.extra = extra or {}


def _args(raw: str) -> dict:
    try:
        data = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError, ValueError):
        raise WorkspaceError("arguments must be a complete JSON object") from None
    if not isinstance(data, dict):
        raise WorkspaceError("arguments must be a JSON object")
    return data


def _require(args: dict, *names: str) -> None:
    for name in names:
        if name not in args:
            raise WorkspaceError(f"{name} is required")


def _as_bool(value, default=False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "1", "yes"):
            return True
        if low in ("false", "0", "no"):
            return False
    if value is None:
        return default
    return bool(value)


def _int_arg(args: dict, key: str, default: int, lo: int, hi: int) -> int:
    raw = args.get(key)
    if raw is None or raw == "":
        return default
    try:
        val = int(float(raw))
    except (TypeError, ValueError, OverflowError):
        return default
    if val < lo and lo >= 1:
        # 0 from a model that fills every property means "unset", not
        # "one line" or "one match".
        return default
    return max(lo, min(val, hi))


def _quote_host_path(path: str) -> str:
    for home in ("~", "$HOME"):
        if path == home or path.startswith(home + "/"):
            return '"$HOME"' + shlex.quote(path[len(home):])
    return shlex.quote(path)


_BARE_EXEC_ERRORS = frozenset({"", "exit", "grep failed", "glob failed", "list failed", "exec failed"})


def _first_lines(text: str, n: int = 8, cap: int = 500) -> str:
    lines = [ln for ln in str(text or "").splitlines() if ln.strip()][:n]
    return "\n".join(lines)[:cap]


def _exec_fail_detail(data: dict, fallback: str = "failed") -> str:
    """Never a bare 'exit'. Prefer stderr, then stdout, then a named fallback + code."""
    err = _first_lines(data.get("err") or "")
    out = _first_lines(data.get("out") or "")
    error = (data.get("error") or "").strip()
    if err:
        body = err
    elif out:
        body = out
    elif error.lower() not in _BARE_EXEC_ERRORS and not error.lower().startswith("exit"):
        body = error
    else:
        body = fallback
    try:
        code = int(data.get("exit"))
    except (TypeError, ValueError):
        code = None
    if code is not None and f"exit {code}" not in body.lower():
        body = f"{body} (exit {code})"
    if body.strip().lower() in ("exit", ""):
        body = f"{fallback}" + (f" (exit {code})" if code is not None else "")
    return body


def _agent_error(data: dict, fallback: str) -> WorkspaceError:
    extra: dict = {}
    code = data.get("exit")
    if code is not None and code != "":
        extra["exit_code"] = code
    err = (data.get("err") or "").strip()
    if err:
        extra["stderr"] = err[:500]
    out = (data.get("out") or "").strip()
    if out:
        extra["stdout"] = out[:500]
    return WorkspaceError(_exec_fail_detail(data, fallback), extra=extra)


def _enrich_agent_data(data: dict, *, op: str = "") -> dict:
    """Rewrite the connector's generic exec error 'exit' before any caller sees it."""
    if not isinstance(data, dict) or data.get("ok"):
        return data
    error = (data.get("error") or "").strip()
    if error.lower() not in _BARE_EXEC_ERRORS and not error.lower().startswith("exit"):
        return data
    detail = _exec_fail_detail(data, fallback=(f"{op} failed" if op else "failed"))
    if detail == error:
        return data
    data = dict(data)
    data["error"] = detail
    return data


def _exec_text(data: dict) -> str:
    return " ".join(str(data.get(key) or "") for key in ("err", "out", "error"))


_GENERIC_MUTATION_ERRORS = frozenset({"", "exit", "replace failed", "write failed"}) | _BARE_EXEC_ERRORS


def _is_generic_mutation_error(data: dict) -> bool:
    error = str(data.get("error") or "").strip().lower()
    return error in _GENERIC_MUTATION_ERRORS or error.startswith("exit")


def _pipe_ok_exit(code, detail: str = "", *, no_match_ok: bool = True) -> bool:
    if code is None or code == "":
        return False
    try:
        val = int(code or 0)
    except (TypeError, ValueError):
        return False
    # 0 = matches, 1 = no matches (rg/grep) or a find write error,
    # 141 = SIGPIPE from head closing the pipe. ls exits 1 on real trouble,
    # so _list passes no_match_ok=False to keep its native fallback.
    if val in (0, 141):
        return True
    if val == 1:
        return no_match_ok
    # Connectors exec with SIGPIPE ignored (their socket writes must survive
    # closed pipes), so head instead leaves the producer dying on EPIPE with
    # a "write error: Broken pipe" message and exit 2. Same cause; only
    # tolerate exit 2 when that message is actually present.
    return val == 2 and "broken pipe" in (detail or "").lower()
