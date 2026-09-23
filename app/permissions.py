"""Interactive permission gate for chat workspace tools.

Scope (per host): /tmp (posix) / %TEMP% (windows) plus the working root — the
bound project path on the bound host, else the host's default_cwd, else the
home directory. Anything else, and reads of secret-named files anywhere, raise
an interactive ask: Allow once / Allow always (session = this chat, host =
this user+host forever) / Deny. Unattended runs (scheduled/auto) never ask:
gated actions deny with an explanatory error. Bypass is live per-thread state
read at gate time, so toggling it applies instantly to running subagents.
Auto-approve (Jev) sits next to bypass: ordinary out-of-scope calls can be
allowed without a card when the user has an OpenRouter key; secrets,
destructive shells, and low-confidence answers still ask.
"""
from __future__ import annotations

import asyncio
import logging
import ntpath
import posixpath
import re
import shlex
import time
from uuid import uuid4

from . import db

logger = logging.getLogger(__name__)

ASK_TIMEOUT_S = 60
MAX_PENDING_PER_THREAD = 8
_ACTIONS = ("allow", "allow_session", "allow_host", "deny")
_STATUSES = ("pending", "allowed", "denied", "timeout", "cancelled")

# --- secret-named files (reads prompt even inside allowed roots) ---
_SECRET_EXACT = frozenset({
    ".env", "env", ".netrc", ".npmrc", ".htpasswd", "credentials.json",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
})
_SECRET_SUFFIX = (".pem", ".key", ".keystore", ".env")
_SECRET_DOTENV = re.compile(r"^\.env([._-]|$)")
_TEMPLATE_OK = frozenset({".env.example", ".env.sample", ".env.template"})

_HOME_MARKERS = ("~", "$HOME", "%USERPROFILE%", "")
_DIR_TOOLS = frozenset({"list_dir", "grep", "glob"})
_FILE_TOOLS = frozenset({"read_file", "write_file", "search_replace"})
_GATED_TOOLS = _DIR_TOOLS | _FILE_TOOLS | {"run_terminal_command", "monitor"}

_OPAQUE_SHELL = re.compile(r"[$`]|\$\{")
_WIN_DRIVE = re.compile(r"^[A-Za-z]:")
_DESTRUCTIVE_SHELL = re.compile(
    r"(?is)("
    r"\brm\s+(?:-[^\s]*[rf][^\s]*\s*)+"
    r"|mkfs\b"
    r"|\bdd\s+if="
    r"|git\s+push\b[^\n]*--force"
    r"|chmod\s+777\b"
    r"|:\(\)\{:\|:&\};:"
    r"|\bformat\s+[a-z]:"
    r"|diskpart\b"
    r"|remove-item\b[^\n]*-recurse"
    r")"
)


def _destructive_command(command: str) -> bool:
    text = str(command or "").strip()
    if not text:
        return False
    return bool(_DESTRUCTIVE_SHELL.search(text))


def is_secret_name(name: str) -> bool:
    low = str(name or "").strip().lower()
    if not low or low in _TEMPLATE_OK:
        return False
    if low.endswith(".pub"):
        return False
    if low in _SECRET_EXACT:
        return True
    if low.endswith(_SECRET_SUFFIX):
        return True
    return bool(_SECRET_DOTENV.match(low))


def _norm(path: str, windows: bool) -> str:
    text = str(path or "")
    if windows:
        return ntpath.normcase(ntpath.normpath(text.replace("/", "\\")))
    return posixpath.normpath(text)


def _is_home_marker(root: str) -> bool:
    return root in _HOME_MARKERS


def _under(path_n: str, root_n: str, windows: bool) -> bool:
    if not root_n:
        return False
    sep = "\\" if windows else "/"
    return path_n == root_n or path_n.startswith(root_n.rstrip(sep) + sep)


def compute_roots(root: str, windows: bool) -> list[str]:
    """Allowed roots for a host given the workspace-computed working root."""
    roots: list[str] = []
    tmp = "%TEMP%" if windows else "/tmp"
    roots.append(tmp)
    if root and not _is_home_marker(root):
        roots.append(root)
    else:
        roots.append("~")
    return roots


def _in_roots(resolved: str, roots: list[str], windows: bool) -> bool:
    raw = str(resolved or "")
    if not raw:
        return True
    if _is_home_marker(raw) or raw.startswith("~") or raw.startswith("$HOME") or raw.startswith("%USERPROFILE%"):
        return any(_is_home_marker(r) for r in roots)
    if windows:
        if raw.replace("/", "\\").lower().startswith("%temp%"):
            return True
    else:
        if raw == "/tmp" or raw.startswith("/tmp/"):
            return True
    n = _norm(raw, windows)
    for r in roots:
        if _is_home_marker(r):
            continue  # absolute paths cannot be matched against an unknown home prefix
        if _under(n, _norm(r, windows), windows):
            return True
    return False


def _resolve_for_scope(target: str, base: str, windows: bool) -> str:
    """Resolve a tool path the same way execution will: relative joins the base
    (project root / cwd), empty base means home. Dots collapse lexically."""
    raw = str(target or "")
    if not raw or _is_home_marker(raw):
        return "~"
    if raw.startswith("$HOME"):
        raw = "~" + raw[5:]
    if raw.startswith("%USERPROFILE%"):
        raw = "~" + raw[13:]
    if windows:
        raw = raw.replace("/", "\\")
        base2 = base.replace("/", "\\") if base else "~"
        if _is_home_marker(base2):
            base2 = "~"
        if raw.startswith("~") or _WIN_DRIVE.match(raw) or raw.startswith("\\\\"):
            joined = raw
        else:
            joined = ntpath.join(base2, raw)
        return ntpath.normpath(joined)
    if raw.startswith("~") or raw.startswith("/"):
        joined = raw
    else:
        base2 = base if base and not _is_home_marker(base) else "~"
        joined = posixpath.join(base2, raw)
    return posixpath.normpath(joined)


def scope_allows(target: str, base: str, roots: list[str], windows: bool) -> tuple[bool, str]:
    """Resolve like execution, then check membership. Returns (allowed, resolved)."""
    resolved = _resolve_for_scope(target, base, windows)
    return _in_roots(resolved, roots, windows), resolved


def _target_of(tool: str, args: dict) -> str:
    if tool in _DIR_TOOLS:
        return str(args.get("path") or ".")
    if tool in _FILE_TOOLS:
        return str(args.get("path") or "")
    return ""


def _grant_dir_for(tool: str, target: str, windows: bool) -> str:
    """Directory remembered by 'allow always' for a path ask."""
    raw = str(target or "")
    if not raw:
        return "~"
    if _is_home_marker(raw) or raw.startswith(("~", "$HOME", "%USERPROFILE%")):
        return raw if not raw.startswith("$HOME") else "~" + raw[5:]
    if tool in _DIR_TOOLS:
        return raw
    head = (ntpath.split(raw.replace("/", "\\")) if windows else posixpath.split(raw))[0]
    return head or raw


# --- best-effort shell path extraction (fail-open) ---

def _looks_like_path(token: str, windows: bool) -> bool:
    low = token.lower()
    if not token or "://" in low:
        return False
    if token.startswith(("-", "+")):
        token = token.split("=", 1)[1] if "=" in token else ""
        if not token:
            return False
    if token.startswith(("/", "./", "../", "~")):
        return True
    if windows and (token.startswith("\\\\") or _WIN_DRIVE.match(token) or "\\" in token or token.startswith("%temp%")):
        return True
    return "/" in token


def extract_shell_paths(command: str, cwd: str, windows: bool, limit: int = 6) -> list[tuple[str, bool]] | None:
    """Path-looking tokens as (path, is_dir). None = opaque command.
    is_dir marks cd/pushd targets (a directory by construction); everything
    else is treated as a file-ish target."""
    text = str(command or "").strip()
    if not text or _OPAQUE_SHELL.search(text):
        return None
    try:
        tokens = shlex.split(text, posix=not windows)
    except ValueError:
        return None
    out: list[tuple[str, bool]] = []
    prev = ""
    expect_target = False
    for tok in tokens:
        stripped = tok.strip('"\'')
        if expect_target:
            expect_target = False
            if stripped:
                out.append((stripped, False))
            prev = ""
            continue
        if stripped in (">", ">>", "2>", "2>>", "<", "&>", "&>>"):
            expect_target = True
            continue
        if stripped in ("cd", "pushd") or stripped.lower() in ("cd", "pushd", "set-location"):
            prev = "cd"
            continue
        if prev == "cd":
            prev = ""
            if stripped and stripped not in ("&&", ";", "||", "&") and not stripped.startswith("-"):
                out.append((stripped, True))
            continue
        if _looks_like_path(stripped, windows):
            out.append((stripped, False))
    resolved: list[tuple[str, bool]] = []
    seen: list[str] = []
    for item, is_dir in out:
        cand = item
        if not (cand.startswith(("/", "~", "./", "../")) or cand.startswith("$HOME") or cand.startswith("%")
                or _WIN_DRIVE.match(cand) or cand.startswith("\\\\")):
            base = cwd or "~"
            cand = (ntpath.join(base, cand) if windows else posixpath.join(base, cand)) if not _is_home_marker(base) else "~/{}".format(cand)
        if cand and cand not in seen:
            seen.append(cand)
            resolved.append((cand, is_dir))
        if len(resolved) >= limit:
            break
    return resolved


# --- persistence ---

def init_schema(conn) -> None:
    db._ensure_column(conn, "chat_threads", "bypass_permissions", "INTEGER NOT NULL DEFAULT 0")
    db._ensure_column(conn, "chat_threads", "auto_permissions", "INTEGER NOT NULL DEFAULT 0")
    for sql in (
        """CREATE TABLE IF NOT EXISTS chat_permission_asks (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            thread_id INTEGER NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
            run_id TEXT NOT NULL,
            status TEXT NOT NULL,
            decision TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL,
            tool TEXT NOT NULL,
            host_id INTEGER,
            host TEXT NOT NULL DEFAULT '',
            path TEXT NOT NULL DEFAULT '',
            cwd TEXT NOT NULL DEFAULT '',
            command TEXT NOT NULL DEFAULT '',
            secret INTEGER NOT NULL DEFAULT 0,
            requester TEXT NOT NULL DEFAULT '',
            grant_kind TEXT NOT NULL DEFAULT '',
            grant_value TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            resolved_at TEXT)""",
        "CREATE INDEX IF NOT EXISTS chat_perm_asks_thread ON chat_permission_asks(thread_id,status)",
        "CREATE INDEX IF NOT EXISTS chat_perm_asks_run ON chat_permission_asks(run_id)",
        """CREATE TABLE IF NOT EXISTS chat_permission_grants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            thread_id INTEGER REFERENCES chat_threads(id) ON DELETE CASCADE,
            host_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(thread_id, host_id, kind, value))""",
        "CREATE INDEX IF NOT EXISTS chat_perm_grants_user ON chat_permission_grants(user_id,host_id)",
    ):
        conn.execute(sql)


def bypass_enabled(user_id: int, thread_id: int | None) -> bool:
    if not thread_id:
        return False
    row = db.fetchone("SELECT bypass_permissions FROM chat_threads WHERE id=? AND user_id=?", (int(thread_id), int(user_id)))
    return bool(row and row["bypass_permissions"])


def auto_enabled(user_id: int, thread_id: int | None) -> bool:
    if not thread_id:
        return False
    row = db.fetchone("SELECT auto_permissions FROM chat_threads WHERE id=? AND user_id=?", (int(thread_id), int(user_id)))
    return bool(row and ("auto_permissions" in row.keys()) and row["auto_permissions"])


def set_auto(user_id: int, thread_id: int, enabled: bool, conn=None) -> None:
    def _run(c):
        c.execute(
            "UPDATE chat_threads SET auto_permissions=? WHERE id=? AND user_id=?",
            (1 if enabled else 0, int(thread_id), int(user_id)),
        )
        import logging
        logging.getLogger(__name__).info("set_auto user=%s thread=%s enabled=%s", user_id, thread_id, enabled)
        if enabled:
            c.execute(
                """UPDATE chat_permission_asks SET status='allowed', decision=?, resolved_at=?
                   WHERE thread_id=? AND user_id=? AND status='pending' AND secret=0""",
                ("allow", db.now(), int(thread_id), int(user_id)),
            )
    if conn is not None:
        _run(conn)
        return
    with db.tx() as c:
        _run(c)


def set_bypass(user_id: int, thread_id: int, enabled: bool, conn=None) -> None:
    def _run(c):
        c.execute("UPDATE chat_threads SET bypass_permissions=? WHERE id=? AND user_id=?",
                  (1 if enabled else 0, int(thread_id), int(user_id)))
    if conn is not None:
        _run(conn)
        if enabled:
            resolve_thread_pending(user_id, thread_id, "allow", by="bypass", conn=conn)
        return
    with db.tx() as c:
        _run(c)
        if enabled:
            resolve_thread_pending(user_id, thread_id, "allow", by="bypass", conn=c)


def add_grant(user_id: int, thread_id: int | None, host_id: int, kind: str, value: str, conn=None) -> None:
    ts = db.now()
    sql = ("INSERT OR IGNORE INTO chat_permission_grants(user_id, thread_id, host_id, kind, value, created_at) "
           "VALUES (?,?,?,?,?,?)")
    args = (int(user_id), int(thread_id) if thread_id else None, int(host_id), kind, str(value or ""), ts)
    if conn is not None:
        conn.execute(sql, args)
        return
    with db.tx() as c:
        c.execute(sql, args)


def _grant_rows(user_id: int, thread_id: int | None, host_id: int):
    rows = db.fetchall(
        """SELECT * FROM chat_permission_grants
           WHERE user_id=? AND host_id=? AND (thread_id IS NULL OR thread_id=?)""",
        (int(user_id), int(host_id), int(thread_id) if thread_id else -1),
    )
    return rows


def grant_covers(user_id: int, thread_id: int | None, host_id: int, kind: str, value: str, windows: bool) -> bool:
    v = _norm(value, windows) if value else ""
    if not v:
        return False
    for row in _grant_rows(user_id, thread_id, host_id):
        if row["kind"] != kind:
            continue
        g = _norm(row["value"] or "", windows)
        if not g:
            continue
        if kind == "file":
            if v == g:
                return True
        elif _under(v, g, windows):
            return True
    return False


def grants_for(user_id: int, thread_id: int) -> list[dict]:
    rows = db.fetchall(
        """SELECT g.*, h.alias AS host_alias FROM chat_permission_grants g
           LEFT JOIN user_hosts h ON h.id = g.host_id
           WHERE g.user_id=? AND (g.thread_id IS NULL OR g.thread_id=?)
           ORDER BY g.created_at DESC""",
        (int(user_id), int(thread_id)),
    )
    out = []
    for row in rows:
        out.append({
            "id": int(row["id"]),
            "scope": "session" if row["thread_id"] is not None else "host",
            "host_id": int(row["host_id"]),
            "host": row["host_alias"] or "",
            "kind": row["kind"],
            "value": row["value"],
            "created_at": row["created_at"],
        })
    return out


def revoke_grant(user_id: int, grant_id: int) -> bool:
    with db.tx() as conn:
        cur = conn.execute("DELETE FROM chat_permission_grants WHERE id=? AND user_id=?", (int(grant_id), int(user_id)))
        return bool(cur.rowcount)


def roots_summary(user_id: int, thread_id: int) -> dict:
    """Working directories shown in the permissions panel."""
    row = db.fetchone(
        """SELECT t.host_id, t.project_id, h.alias AS host_alias, h.default_cwd, h.agent_os,
                  p.remote_path AS project_path
           FROM chat_threads t
           LEFT JOIN user_hosts h ON h.id = t.host_id
           LEFT JOIN user_projects p ON p.id = t.project_id
           WHERE t.id=? AND t.user_id=?""",
        (int(thread_id), int(user_id)),
    )
    hosts = db.fetchall(
        "SELECT id, alias, default_cwd, agent_os, disabled FROM user_hosts WHERE user_id=? ORDER BY alias",
        (int(user_id),),
    )
    machines = []
    for h in hosts:
        windows = str(h["agent_os"] or "").lower().startswith("win")
        root = ""
        if row and row["host_id"] == h["id"]:
            root = (row["project_path"] or "") if row["project_id"] else ""
        if not root:
            root = h["default_cwd"] or ""
        label = root if root and root not in _HOME_MARKERS else "~"
        machines.append({
            "id": int(h["id"]),
            "host": h["alias"],
            "path": label,
            "windows": windows,
            "disabled": bool(h["disabled"]),
            "active": bool(row and row["host_id"] == h["id"]),
        })
    return {
        "temp": ["/tmp", "%TEMP%"],
        "hosts": machines,
        "thread_host_id": int(row["host_id"]) if row and row["host_id"] is not None else None,
    }


def panel_for(user_id: int, thread_id: int) -> dict:
    """Host-tab payload for the permissions menu: roots + revocable grants."""
    summary = roots_summary(user_id, thread_id)
    grants = grants_for(user_id, thread_id)
    by_host: dict[int, list[dict]] = {}
    for grant in grants:
        by_host.setdefault(int(grant["host_id"]), []).append(grant)
    grant_hosts = set(by_host)
    hosts = []
    seen = set()
    for machine in summary["hosts"]:
        hid = int(machine["id"])
        if machine["disabled"] and hid not in grant_hosts:
            continue
        seen.add(hid)
        windows = bool(machine["windows"])
        temp = "%TEMP%" if windows else "/tmp"
        work = str(machine["path"] or "")
        roots = [{"path": temp, "kind": "temp"}]
        if work and work.casefold() != temp.casefold():
            roots.append({"path": work, "kind": "work"})
        root_keys = {_norm(item["path"], windows) for item in roots}
        extra = []
        for grant in by_host.get(hid, []):
            if _norm(grant["value"], windows) in root_keys:
                continue
            extra.append(grant)
        hosts.append({
            "id": hid,
            "host": machine["host"],
            "windows": windows,
            "active": bool(machine["active"]),
            "roots": roots,
            "grants": extra,
        })
    for hid, extra in by_host.items():
        if hid in seen:
            continue
        alias = extra[0].get("host") or "unknown"
        hosts.append({
            "id": hid,
            "host": alias,
            "windows": False,
            "active": False,
            "roots": [],
            "grants": extra,
        })
    if not hosts:
        hosts.append({
            "id": 0,
            "host": "local",
            "windows": False,
            "active": False,
            "roots": [{"path": "/tmp", "kind": "temp"}, {"path": "%TEMP%", "kind": "temp"}],
            "grants": [],
        })
    auto = auto_enabled(user_id, thread_id)
    available = False
    try:
        from . import jev

        available = jev.user_can_auto(user_id)
    except Exception:
        available = False
    return {
        "bypass": bypass_enabled(user_id, thread_id),
        "auto": auto,
        "auto_available": available,
        "thread_host_id": summary.get("thread_host_id"),
        "hosts": hosts,
    }


# --- ask lifecycle ---

def _public_payload(row) -> dict:
    return {
        "id": row["id"],
        "kind": "tool_permission",
        "status": row["status"],
        "perm_kind": row["kind"],
        "tool": row["tool"],
        "host": row["host"],
        "path": row["path"],
        "cwd": row["cwd"],
        "command": row["command"],
        "secret": bool(row["secret"]),
        "requester": row["requester"],
        "expires_at": row["expires_at"],
        "decision": row["decision"] or "",
        "grant_scope": row["decision"] if row["decision"] in ("allow_session", "allow_host") else "",
        "grant_kind": row["grant_kind"] or "",
        "grant_value": row["grant_value"] or "",
    }


def _get_ask(user_id: int, ask_id: str):
    return db.fetchone("SELECT * FROM chat_permission_asks WHERE id=? AND user_id=?", (str(ask_id), int(user_id)))


def resolve(user_id: int, thread_id: int, ask_id: str, action: str) -> dict:
    """HTTP decision path. Returns the public ask payload or raises ValueError."""
    if action not in _ACTIONS:
        raise ValueError("invalid action")
    ask_id = str(ask_id)
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM chat_permission_asks WHERE id=? AND user_id=?", (ask_id, int(user_id))).fetchone()
        if row is None or int(row["thread_id"]) != int(thread_id):
            raise LookupError("permission request not found")
        if row["status"] != "pending":
            return _public_payload(row)
        expires = _parse_iso(row["expires_at"])
        if expires and expires < time.time():
            conn.execute("UPDATE chat_permission_asks SET status='timeout', resolved_at=? WHERE id=?", (db.now(), ask_id))
            row = conn.execute("SELECT * FROM chat_permission_asks WHERE id=?", (ask_id,)).fetchone()
            return _public_payload(row)
        if action == "deny":
            conn.execute(
                "UPDATE chat_permission_asks SET status='denied', decision=?, resolved_at=? WHERE id=?",
                (action, db.now(), ask_id),
            )
        else:
            if action == "allow_session":
                if row["grant_kind"] and row["host_id"]:
                    add_grant(user_id, int(row["thread_id"]), int(row["host_id"]), row["grant_kind"], row["grant_value"], conn=conn)
            elif action == "allow_host":
                if row["grant_kind"] and row["host_id"]:
                    add_grant(user_id, None, int(row["host_id"]), row["grant_kind"], row["grant_value"], conn=conn)
            conn.execute(
                "UPDATE chat_permission_asks SET status='allowed', decision=?, resolved_at=? WHERE id=?",
                (action, db.now(), ask_id),
            )
        return _public_payload(conn.execute("SELECT * FROM chat_permission_asks WHERE id=?", (ask_id,)).fetchone())


def resolve_thread_pending(user_id: int, thread_id: int, action: str, *, by: str = "", conn=None) -> int:
    """Resolve every pending ask on a thread (bypass flip / cleanup)."""
    def _run(c) -> int:
        cur = c.execute(
            "UPDATE chat_permission_asks SET status='allowed', decision=?, resolved_at=? WHERE thread_id=? AND user_id=? AND status='pending'",
            (action, db.now(), int(thread_id), int(user_id)),
        )
        return int(cur.rowcount)
    if conn is not None:
        return _run(conn)
    with db.tx() as c:
        return _run(c)


def cancel_run_asks(run_id: str) -> None:
    if not run_id:
        return
    try:
        with db.tx() as conn:
            conn.execute(
                "UPDATE chat_permission_asks SET status='cancelled', resolved_at=? WHERE run_id=? AND status='pending'",
                (db.now(), str(run_id)),
            )
    except Exception:
        pass


def recover() -> None:
    """Startup: nothing is waiting anymore."""
    try:
        with db.tx() as conn:
            conn.execute(
                "UPDATE chat_permission_asks SET status='cancelled', resolved_at=? WHERE status='pending'",
                (db.now(),),
            )
    except Exception:
        pass


def _parse_iso(text: str) -> float | None:
    try:
        from datetime import datetime, timezone

        dt = datetime.strptime(str(text), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def ask_expired(expires_at) -> bool:
    """True when a stored ask deadline has passed. Missing timestamp is live."""
    expires = _parse_iso(expires_at)
    return expires is not None and expires < time.time()


def _requester_label(requester: dict | None) -> str:
    req = requester if isinstance(requester, dict) else {}
    if req.get("role") == "subagent":
        desc = str(req.get("description") or "").strip() or "subagent"
        kind = str(req.get("type") or "").strip()
        return f"{desc} ({kind})" if kind else desc
    return "Main agent"


class Gate:
    """Runtime-side handle passed into workspace.execute."""

    def __init__(self, *, user_id: int, thread_id: int | None, run_id: str,
                 requester: dict | None, unattended: bool, notify, cancel: asyncio.Event | None):
        self.user_id = int(user_id)
        self.thread_id = int(thread_id) if thread_id else None
        self.run_id = str(run_id or "")
        self.requester = requester or {"role": "main"}
        self.unattended = bool(unattended)
        self._notify = notify
        self._cancel = cancel


async def _ask_and_wait(gate: Gate, payload: dict) -> str:
    ask_id = "pa_" + uuid4().hex[:16]
    from datetime import datetime, timedelta, timezone

    expires_iso = (datetime.now(timezone.utc) + timedelta(seconds=ASK_TIMEOUT_S)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.tx() as conn:
        pending = conn.execute(
            "SELECT COUNT(*) FROM chat_permission_asks WHERE thread_id=? AND status='pending'",
            (gate.thread_id,),
        ).fetchone()[0]
        if pending >= MAX_PENDING_PER_THREAD:
            return "deny_busy"
        conn.execute(
            """INSERT INTO chat_permission_asks(
                   id, user_id, thread_id, run_id, status, decision, kind, tool, host_id, host,
                   path, cwd, command, secret, requester, grant_kind, grant_value, created_at, expires_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ask_id, gate.user_id, gate.thread_id, gate.run_id, "pending", "",
                payload.get("perm_kind") or "path", payload.get("tool") or "", payload.get("host_id"),
                payload.get("host") or "", payload.get("path") or "", payload.get("cwd") or "",
                str(payload.get("command") or "")[:8000], 1 if payload.get("secret") else 0,
                payload.get("requester_label") or "Main agent",
                payload.get("grant_kind") or "", payload.get("grant_value") or "",
                db.now(), expires_iso,
            ),
        )
    gate._notify({"event": "permission", **payload, "id": ask_id, "status": "pending", "expires_at": expires_iso})
    deadline = time.time() + ASK_TIMEOUT_S
    try:
        from . import chat_runs as chat_runs_mod

        while True:
            if gate._cancel is not None and gate._cancel.is_set():
                _finish_ask(ask_id, "cancelled")
                gate._notify({"event": "permission", "kind": "tool_permission", "id": ask_id, "status": "cancelled"})
                return "cancelled"
            if getattr(chat_runs_mod, "_stopping", False):
                _finish_ask(ask_id, "cancelled")
                gate._notify({"event": "permission", "kind": "tool_permission", "id": ask_id, "status": "cancelled"})
                return "cancelled"
            if bypass_enabled(gate.user_id, gate.thread_id):
                _finish_ask(ask_id, "allowed")
                gate._notify({"event": "permission", "kind": "tool_permission", "id": ask_id,
                              "status": "allowed", "decision": "allow"})
                return "allow"
            row = _get_ask(gate.user_id, ask_id)
            if row is not None and row["status"] != "pending":
                status = row["status"]
                gate._notify({"event": "permission", "kind": "tool_permission", "id": ask_id,
                              "status": status, "decision": row["decision"] or "",
                              "grant_scope": row["decision"] if row["decision"] in ("allow_session", "allow_host") else ""})
                return status if status != "allowed" else (row["decision"] or "allow")
            if time.time() >= deadline:
                _finish_ask(ask_id, "timeout")
                gate._notify({"event": "permission", "kind": "tool_permission", "id": ask_id, "status": "timeout"})
                return "timeout"
            await asyncio.sleep(0.25)
    except asyncio.CancelledError:
        _finish_ask(ask_id, "cancelled")
        raise


def _finish_ask(ask_id: str, status: str) -> None:
    try:
        with db.tx() as conn:
            conn.execute("UPDATE chat_permission_asks SET status=?, resolved_at=? WHERE id=? AND status='pending'",
                         (status, db.now(), ask_id))
    except Exception:
        pass


def _deny_detail(target: str, action: str) -> str:
    label = (target or "that location").strip()
    if action == "timeout":
        return f"Permission request timed out (no user response). Do not access {label}; continue without it."
    if action == "unattended":
        return (f"Unattended run — permission cannot be requested for {label}. "
                "Continue without it; ask the owner to grant access or enable bypass.")
    if action == "deny_busy":
        return "Too many pending permission requests in this chat. Continue without new locations."
    return f"Permission denied by user. Do not access {label}; continue without it."


class ReviewResult(dict):
    @property
    def allowed(self) -> bool:
        return bool(self.get("allowed"))

    @property
    def root(self):
        return self.get("root_override")

    @property
    def detail(self) -> str:
        return str(self.get("detail") or "")

    @property
    def ask_id(self) -> str:
        return str(self.get("ask_id") or "")


async def review(*, tool: str, args: dict, user_id: int,
                 host_id: int, host_alias: str, root: str, windows: bool,
                 gate: Gate | None) -> ReviewResult:
    """Decide whether a workspace tool call may run. Pure verdict + ask flow."""
    if gate is None or tool not in _GATED_TOOLS:
        return ReviewResult(allowed=True)
    if gate.thread_id is None:
        return ReviewResult(allowed=False, detail=_deny_detail("this location", "unattended"), kind="permission_denied")
    roots = compute_roots(root, windows)
    reasons: list[str] = []
    secret = False
    out_of_root = False
    target = _target_of(tool, args)
    grant_kind, grant_value = "", ""
    perm_kind = "path"
    cwd = ""
    command = ""

    if tool in ("run_terminal_command", "monitor"):
        perm_kind = "shell"
        command = str(args.get("command") or "")
        if tool == "monitor" and not command and str(args.get("url") or "").strip():
            return ReviewResult(allowed=True)
        raw_cwd = str(args.get("cwd") or "")
        cwd = raw_cwd or (root if root and not _is_home_marker(root) else "~")
        cwd_ok, resolved_cwd = scope_allows(raw_cwd or cwd, root or "~", roots, windows)
        cwd_in = (not raw_cwd) or cwd_ok
        if not cwd_in:
            reasons.append(f"working directory {cwd} is outside the allowed roots")
            out_of_root = True
            grant_kind, grant_value = "dir", resolved_cwd
        base = resolved_cwd if resolved_cwd and not _is_home_marker(resolved_cwd) else "~"
        paths = extract_shell_paths(command, base, windows)
        if paths is not None:
            for cand, is_dir in paths:
                ok, resolved_cand = scope_allows(cand, base, roots, windows)
                if ok:
                    continue
                reasons.append(f"{resolved_cand} is outside the allowed roots")
                out_of_root = True
                if not grant_value:
                    grant_kind, grant_value = "dir", (
                        resolved_cand if is_dir else _grant_dir_for("read_file", resolved_cand, windows)
                    )
        offending = [r.split(" is outside")[0] for r in reasons if "outside" in r]
        display = offending[0] if offending else cwd
    else:
        in_roots, resolved = scope_allows(target, root, roots, windows)
        display = resolved or target
        if tool == "read_file":
            base_name = (ntpath.basename(resolved.replace("/", "\\")) if windows else posixpath.basename(resolved))
            if is_secret_name(base_name):
                secret = True
                reasons.append("secret-named file")
                grant_kind, grant_value = "file", resolved
        if not in_roots:
            out_of_root = True
            reasons.append("outside the allowed roots")
            if not grant_value:
                grant_kind, grant_value = "dir", _grant_dir_for(tool, resolved, windows)

    if not reasons:
        return ReviewResult(allowed=True)

    bypass = bypass_enabled(gate.user_id, gate.thread_id)
    if bypass:
        return ReviewResult(allowed=True, root_override="" if out_of_root else None)

    if grant_value and grant_covers(gate.user_id, gate.thread_id, host_id, grant_kind, grant_value, windows):
        return ReviewResult(allowed=True, root_override="" if out_of_root else None)

    label = display if perm_kind == "shell" else (display or cwd or command)
    if gate.unattended:
        return ReviewResult(allowed=False, detail=_deny_detail(label, "unattended"), kind="permission_denied")

    payload = {
        "kind": "tool_permission",
        "perm_kind": perm_kind,
        "tool": tool,
        "host": host_alias,
        "host_id": int(host_id),
        "path": "" if perm_kind == "shell" else str(display or target)[:400],
        "cwd": cwd[:400] if perm_kind == "shell" else "",
        "command": command[:8000],
        "secret": secret,
        "reasons": reasons[:4],
        "requester": gate.requester,
        "requester_label": _requester_label(gate.requester),
        "grant_kind": grant_kind,
        "grant_value": str(grant_value)[:400],
    }
    if (
        auto_enabled(gate.user_id, gate.thread_id)
        and not secret
        and not _destructive_command(command)
    ):
        from . import jev
        from . import chat_runs as chat_runs_mod

        if jev.user_can_auto(gate.user_id) and not getattr(chat_runs_mod, "_stopping", False):
            # Read-only directory probes are why Auto exists: listing /home or
            # a sibling folder should not stop the agent for a card. Writes
            # and shell still go through Jev.
            if tool in _DIR_TOOLS or (tool == "read_file" and not secret):
                logger.warning("auto-allow read tool=%s path=%s thread=%s", tool, payload.get("path"), gate.thread_id)
                return ReviewResult(allowed=True, root_override="" if out_of_root else None)
            try:
                allowed = await jev.judge(
                    gate.user_id,
                    gate.thread_id,
                    {
                        "tool": tool,
                        "host": host_alias,
                        "path": payload["path"],
                        "cwd": payload["cwd"],
                        "command": command[:8000],
                        "secret": False,
                        "reasons": reasons[:4],
                    },
                    roots=roots,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Jev judge raised", exc_info=True)
                allowed = False
            logger.warning("Jev verdict tool=%s path=%s allowed=%s", tool, payload.get("path") or payload.get("cwd"), allowed)
            if allowed:
                return ReviewResult(allowed=True, root_override="" if out_of_root else None)
    action = await _ask_and_wait(gate, payload)
    if action in ("allow", "allow_session", "allow_host"):
        return ReviewResult(allowed=True, root_override="" if out_of_root else None, ask_id="")
    if action == "cancelled":
        return ReviewResult(allowed=False, detail="Tool interrupted.", kind="interrupted")
    return ReviewResult(allowed=False, detail=_deny_detail(label, action), kind="permission_denied")
