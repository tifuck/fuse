"""Remote transport scripts and connector result interpretation: cwd
normalization for connectors, SSH job/tool scripts, PowerShell quoting and
the read-only stat probe, and exec-result shaping. Pure construction: no
transport calls, no state.

Moved verbatim from app/workspace.py (see sourcemap in the work dir); the
facade imports these back as its stable surface.
"""
from __future__ import annotations

import re
import shlex

from .common import _quote_host_path


def _ps_literal(text: str) -> str:
    # PowerShell also closes a single-quoted string on the curly quotes
    # U+2018-U+201B; doubling any of them keeps it literal.
    return "'" + re.sub("(['\u2018\u2019\u201a\u201b])", r"\1\1", str(text)) + "'"


def _ps_path_expr(path: str) -> str:
    raw = str(path or "").replace("/", "\\")
    if raw.startswith("~\\"):
        return f"Join-Path $env:USERPROFILE {_ps_literal(raw[2:])}"
    return _ps_literal(raw)


def _probe_cmd(path: str, *, windows: bool) -> str:
    """Read-only stat probe: kind, size, writability, parent state. Never mutates."""
    if windows:
        return (
            f"$p = {_ps_path_expr(path)}\n"
            "$d = Split-Path -Parent $p\n"
            "if (Test-Path -LiteralPath $p -PathType Container) { 'dir' } "
            "elseif (Test-Path -LiteralPath $p -PathType Leaf) { (Get-Item -LiteralPath $p).Length } "
            "else { 'missing' }\n"
            "if (-not (Test-Path -LiteralPath $d -PathType Container)) { 'dmiss' } else { 'dw' }\n"
        )
    q = _quote_host_path(path)
    return (
        f"p={q}\n"
        'd=$(dirname "$p")\n'
        'if [ -d "$p" ]; then echo dir; echo na\n'
        'elif [ -f "$p" ]; then wc -c < "$p"\n'
        'if [ -w "$p" ]; then echo w; else echo nw; fi\n'
        "else echo missing; echo na; fi\n"
        'if [ -d "$d" ] && [ -w "$d" ]; then echo dw\n'
        'elif [ -d "$d" ]; then echo dnw\n'
        "else echo dmiss; fi\n"
    )


def _agent_cwd(cwd: str) -> str:
    """Connector chdir()s the cwd string as-is. $HOME is not a path."""
    if cwd in ("~", "$HOME", "", "%USERPROFILE%"):
        return "~"
    if cwd.startswith("$HOME/"):
        return "~/" + cwd[6:]
    if cwd.startswith("$HOME\\"):
        return "~\\" + cwd[6:]
    if cwd.startswith("%USERPROFILE%"):
        rest = cwd[13:]
        if rest.startswith("\\") or rest.startswith("/"):
            return "~" + rest.replace("/", "\\")
        return "~"
    return cwd


_SLEEP_CMD = re.compile(r"^\s*(?:setsid\s+)?sleep\s+\d+(?:\.\d+)?\s*$")


def _is_sleep_command(command: str) -> bool:
    """A command that is only `sleep` stops at its foreground timeout instead of
    moving to the background (a wait must not outlive the wait)."""
    return bool(_SLEEP_CMD.match(command or ""))


def _shell_prefix(cwd: str) -> str:
    if cwd in ("~", "$HOME"):
        return 'cd "$HOME"'
    if cwd.startswith("~/"):
        rest = shlex.quote(cwd[2:])
        return f'cd "$HOME"/{rest}'
    if cwd.startswith("$HOME/"):
        rest = shlex.quote(cwd[6:])
        return f'cd "$HOME"/{rest}'
    return f"cd {shlex.quote(cwd)}"


_PGID_DIR = "$HOME/.cache/fuse/jobs"


def _ssh_job_script(cwd: str, command: str, token: str = "") -> str:
    """bash script for an SSH command: stdin closed (a prompt fails fast
    instead of hanging until the deadline), non-interactive env, and a cwd
    failure that stops the whole command instead of running the rest of a
    `a; b` list in $HOME. With a token, the session's process group id is
    recorded so a timeout or kill can terminate the whole tree."""
    lines = [
        "exec </dev/null",
        "export GIT_TERMINAL_PROMPT=0 DEBIAN_FRONTEND=noninteractive PAGER=cat GIT_PAGER=cat",
    ]
    if token:
        lines += [
            f'__fuse_pgf="{_PGID_DIR}/{token}"',
            # Only record a group this session leads (sshd setsid()s the
            # exec'd shell), never a foreign one.
            '__fuse_pg=$(ps -o pgid= -p $$ 2>/dev/null | tr -dc 0-9)',
            'if [ -n "$__fuse_pg" ] && { [ "$__fuse_pg" = "$$" ] || [ "$__fuse_pg" = "$PPID" ]; }; then',
            '  { mkdir -p "${__fuse_pgf%/*}" && printf \'%s\\n\' "$__fuse_pg" > "$__fuse_pgf"; } 2>/dev/null',
            "fi",
            "trap 'rm -f \"$__fuse_pgf\" 2>/dev/null' EXIT",
        ]
    lines.append(f"{_shell_prefix(cwd)} || exit 1")
    lines.append(command)
    return "\n".join(lines) + "\n"


def _ssh_kill_script(token: str) -> str:
    return (
        f'f="{_PGID_DIR}/{token}"; pg=$(tr -dc 0-9 < "$f" 2>/dev/null); '
        'if [ -n "$pg" ] && [ "$pg" -gt 1 ]; then kill -TERM -- -"$pg" 2>/dev/null; '
        'sleep 2; kill -KILL -- -"$pg" 2>/dev/null; fi; rm -f "$f"; exit 0'
    )


def _agent_output_complete(data: dict) -> bool:
    if not isinstance(data, dict):
        return False
    if "output_complete" in data:
        return data.get("output_complete") is True
    truncated = data.get("truncated")
    return truncated is False or truncated == 0


def _agent_timed_out(data: dict) -> bool:
    if not isinstance(data, dict):
        return False
    if data.get("timeout") is True or data.get("timeout") == 1:
        return True
    error = str(data.get("error") or "").strip().lower()
    return error == "timed out" or error.startswith("timed out")


def _agent_job_output(stdout: str, stderr: str) -> str:
    stdout = stdout or ""
    stderr = stderr or ""
    if stdout and stderr:
        sep = "" if stdout.endswith("\n") else "\n"
        return f"{stdout}{sep}[stderr]\n{stderr}"
    return stdout or stderr


class _AgentExec:
    __slots__ = ("stdout", "stderr", "exit_status")

    def __init__(self, data: dict):
        self.stdout = data.get("out") or ""
        self.stderr = data.get("err") or ""
        code = data.get("exit")
        if data.get("error") and code is None:
            code = 1
        self.exit_status = int(code) if isinstance(code, int) else (0 if data.get("ok") else 1)


class _ExecResult:
    """Runner result for command jobs: exit status plus combined output."""

    __slots__ = ("exit_status", "output")

    def __init__(self, exit_status, output: str):
        self.exit_status = exit_status
        self.output = output or ""


_SSH_BEGIN = "\x1efuse-begin\x1e"


def _strip_begin(out: str) -> str:
    """Drop anything a login profile printed before the tool's own output."""
    text = out or ""
    at = text.find(_SSH_BEGIN)
    if at < 0:
        return text
    rest = text[at + len(_SSH_BEGIN):]
    return rest[1:] if rest.startswith("\n") else rest


def _ssh_tool_script(cwd: str, cmd: str) -> str:
    """bash -lc script for bounded tool commands: no stdin, fail closed on a
    bad cwd, and a sentinel so login-profile chatter never reaches parsers."""
    return (
        "exec </dev/null\n"
        f"{_shell_prefix(cwd or '~')} || exit 1\n"
        f"printf '%s\\n' {shlex.quote(_SSH_BEGIN)}\n"
        f"{cmd}\n"
    )


def _as_text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value or "")
