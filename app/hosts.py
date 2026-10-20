"""Per-user SSH hosts and projects. Secrets are DEK-wrapped; list/get never return them."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
from .database_errors import IntegrityError
from datetime import datetime, timedelta, timezone

from . import db
from .config import settings
from .crypto import DecryptionError, decrypt_with_dek, encrypt_with_dek
from .vault import VaultLocked, get_dek, unlocked

logger = logging.getLogger(__name__)

RESERVED_ALIASES = frozenset({"local", "localhost", "127.0.0.1"})
_ALIAS_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,39}$")
_USER_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_CODE_RE = re.compile(r"^fuse_[A-Za-z0-9_-]{8,64}$")
CATALOG_CHARS = 2048
PAIRING_TTL = timedelta(minutes=15)
MAX_PENDING_PAIRINGS = 3
MAX_AGENT_HOSTS = 128
AGENT_TOKEN_PREFIX = "fa_"
# Folder colors a new project picks from at random. Users can pick any hex.
PROJECT_COLORS = (
    "#E5484D", "#F76B15", "#FFC53D", "#46A758", "#12A594",
    "#0090FF", "#3E63DD", "#8E4EC6", "#D6409F", "#A18072",
)
_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
LOGO_MAX_BYTES = 96 * 1024
_LOGO_MAGIC = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/webp": (b"RIFF",),
}


class HostError(Exception):
    def __init__(self, detail: str, status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _text(value, field: str, limit: int, *, strip: bool = True) -> str:
    if not isinstance(value, str):
        raise HostError(f"invalid {field}")
    text = value.strip() if strip else value
    if not text or len(text) > limit:
        raise HostError(f"invalid {field}")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise HostError(f"invalid {field}")
    return text


def normalize_alias(raw) -> str:
    alias = _text(raw, "alias", 40).lower()
    if alias in RESERVED_ALIASES or not _ALIAS_RE.match(alias):
        raise HostError("alias is reserved or invalid")
    return alias


def _port(raw) -> int:
    if raw is None or raw == "":
        return 22
    try:
        port = int(raw)
    except (TypeError, ValueError) as exc:
        raise HostError("invalid port") from exc
    if port < 1 or port > 65535:
        raise HostError("invalid port")
    return port


def _auth_type(raw) -> str:
    kind = (raw or "key").strip().lower()
    if kind not in ("key", "password"):
        raise HostError("auth_type must be key or password")
    return kind


def public_origin() -> str:
    return (settings.public_base_url or "").rstrip("/")


def token_hash(raw: str) -> str:
    return hashlib.sha256((raw or "").encode()).hexdigest()


def _iso_in(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require_dek(user_id: int) -> bytes:
    try:
        return get_dek(user_id)
    except VaultLocked as exc:
        raise HostError("vault locked", 401) from exc


PERMISSION_DEFAULTS = ("ask", "auto", "bypass")


def _permission_default(value) -> str:
    return value if value in PERMISSION_DEFAULTS else "ask"


def settings_for(user_id: int, conn=None) -> dict:
    q = "SELECT * FROM user_agent_settings WHERE user_id=?"
    row = conn.execute(q, (user_id,)).fetchone() if conn is not None else db.fetchone(q, (user_id,))
    if row is None:
        return {
            "agent_coding": False,
            "allow_nested_spawn": False,
            "permission_mode": "project",
            "local_workspace_enabled": False,
            "default_chat_permissions": "ask",
            "default_project_permissions": "ask",
        }
    mode = row["permission_mode"] if row["permission_mode"] in ("project", "read_only") else "project"
    return {
        "agent_coding": bool(row["agent_coding"]),
        "allow_nested_spawn": bool(row["allow_nested_spawn"]),
        "permission_mode": mode,
        "local_workspace_enabled": bool(row["local_workspace_enabled"]),
        "default_chat_permissions": _permission_default(_row_get(row, "default_chat_permissions")),
        "default_project_permissions": _permission_default(_row_get(row, "default_project_permissions")),
    }


def enable_coding_if_off(user_id: int) -> None:
    """Auto-enable agent coding in one atomic statement.

    No read-modify-write: a user's concurrent save of agent_coding=false is
    either fully before this (we re-enable, as a connecting agent implies)
    or fully after it (their choice wins). It can never be silently reverted
    by a stale intermediate read.

    One upsert, not INSERT IGNORE then UPDATE: on MariaDB the ignored insert
    takes a shared lock on the existing row, so two connectors of one user
    saying hello at once (every reconnect after a restart) each held it and
    deadlocked upgrading to the UPDATE's exclusive lock.
    """
    with db.tx() as conn:
        conn.execute(
            """INSERT INTO user_agent_settings(user_id, agent_coding) VALUES(?, 1)
               ON CONFLICT(user_id) DO UPDATE SET agent_coding=1""",
            (user_id,),
        )


def set_settings(user_id: int, *, agent_coding=None, allow_nested_spawn=None, permission_mode=None,
                 default_chat_permissions=None, default_project_permissions=None) -> dict:
    with db.tx() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT OR IGNORE INTO user_agent_settings(user_id) VALUES(?)",
            (user_id,),
        )
        row = settings_for(user_id, conn)
        if agent_coding is not None and not isinstance(agent_coding, bool):
            raise HostError("agent_coding must be true or false")
        if allow_nested_spawn is not None and not isinstance(allow_nested_spawn, bool):
            raise HostError("allow_nested_spawn must be true or false")
        coding = row["agent_coding"] if agent_coding is None else agent_coding
        nested = row["allow_nested_spawn"] if allow_nested_spawn is None else allow_nested_spawn
        mode = row["permission_mode"]
        if permission_mode is not None:
            if permission_mode not in ("project", "read_only"):
                raise HostError("invalid permission_mode")
            mode = permission_mode
        for raw in (default_chat_permissions, default_project_permissions):
            if raw is not None and raw not in PERMISSION_DEFAULTS:
                raise HostError("default permissions must be ask, auto or bypass")
        chat_default = default_chat_permissions or row["default_chat_permissions"]
        project_default = default_project_permissions or row["default_project_permissions"]
        conn.execute(
            """UPDATE user_agent_settings
               SET agent_coding=?, allow_nested_spawn=?, permission_mode=?,
                   default_chat_permissions=?, default_project_permissions=?
               WHERE user_id=?""",
            (1 if coding else 0, 1 if nested else 0, mode, chat_default, project_default, user_id),
        )
        return settings_for(user_id, conn)


def _row_get(row, key, default=None):
    if row is None:
        return default
    try:
        if hasattr(row, "keys") and key not in row.keys():
            return default
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def _permission_mode(raw, *, required: bool = False) -> str:
    if raw is None or raw == "":
        if required:
            raise HostError("invalid permission_mode")
        return "project"
    mode = str(raw).strip()
    if mode not in ("project", "read_only"):
        raise HostError("invalid permission_mode")
    return mode


def _project_permissions(raw) -> str:
    if raw is None or raw == "":
        return ""
    if raw not in PERMISSION_DEFAULTS:
        raise HostError("default_permissions must be ask, auto, bypass or empty")
    return raw


def new_thread_permissions(user_id: int, conn, project=None) -> str:
    """Permission mode a new chat starts in: its project's own default, else
    the account default for project sessions or plain chats."""
    own = _row_get(project, "default_permissions") or ""
    if own in PERMISSION_DEFAULTS:
        return own
    key = "default_project_permissions" if project is not None else "default_chat_permissions"
    return settings_for(user_id, conn)[key]


def project_color(project_id, stored="") -> str:
    """The stored folder color, or a stable palette pick for projects made
    before colors existed (so they do not all share the theme accent)."""
    if isinstance(stored, str) and _COLOR_RE.match(stored):
        return stored.upper()
    try:
        index = int(project_id)
    except (TypeError, ValueError):
        index = 0
    return PROJECT_COLORS[index % len(PROJECT_COLORS)]


def _color(raw) -> str:
    if not isinstance(raw, str) or not _COLOR_RE.match(raw.strip()):
        raise HostError("color must be a #RRGGBB hex value")
    return raw.strip().upper()


def logo_url(project_id, rev) -> str:
    try:
        rev = int(rev or 0)
    except (TypeError, ValueError):
        rev = 0
    return f"/projects/{int(project_id)}/logo?v={rev}" if rev > 0 else ""


def _project_view(row) -> dict:
    mode = _row_get(row, "permission_mode") or "project"
    if mode not in ("project", "read_only"):
        mode = "project"
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "remote_path": row["remote_path"],
        "notes": _row_get(row, "notes") or "",
        "default_model": _row_get(row, "default_model") or "",
        "permission_mode": mode,
        "default_permissions": _row_get(row, "default_permissions") or "",
        "color": project_color(row["id"], _row_get(row, "color") or ""),
        "logo_url": logo_url(row["id"], _row_get(row, "logo_rev") or 0),
        "archived": bool(_row_get(row, "archived") or 0),
        "archived_at": _row_get(row, "archived_at") or "",
        "created_at": row["created_at"],
        "updated_at": _row_get(row, "updated_at") or row["created_at"],
    }


def _projects(conn, user_id: int, host_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM user_projects WHERE user_id=? AND host_id=? ORDER BY name",
        (user_id, host_id),
    ).fetchall()
    return [_project_view(r) for r in rows]


def _public_host(row, projects=None) -> dict:
    auth = row["auth_type"] or ""
    uid = int(_row_get(row, "user_id") or 0)
    hid = int(row["id"])
    online = False
    if auth == "agent" and uid:
        from . import agent_hub

        online = agent_hub.is_online(uid, hid)
    version = _row_get(row, "agent_version") or ""
    return {
        "id": hid,
        "alias": row["alias"],
        "label": row["label"] or "",
        "hostname": row["hostname"],
        "port": int(row["port"] or 0),
        "username": row["username"],
        "auth_type": auth,
        "kind": "agent" if auth == "agent" else "ssh",
        "default_cwd": row["default_cwd"] or "",
        "key_algo": row["key_algo"] or "",
        "fingerprint": row["fingerprint"] or "",
        "pinned": bool(row["fingerprint"]) or auth == "agent",
        "disabled": bool(row["disabled"]),
        "online": online,
        "agent_version": version,
        "agent_os": _row_get(row, "agent_os") or "",
        "agent_arch": _row_get(row, "agent_arch") or "",
        "last_seen_at": _row_get(row, "last_seen_at"),
        "last_ok_at": row["last_ok_at"],
        "last_error": row["last_error"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "projects": projects if projects is not None else [],
    }


def has_ssh_hosts(user_id: int) -> bool:
    row = db.fetchone(
        "SELECT 1 FROM user_hosts WHERE user_id=? AND auth_type!='agent' LIMIT 1",
        (user_id,),
    )
    return row is not None


def list_bundle(user_id: int) -> dict:
    with db.tx() as conn:
        conn.execute("BEGIN")
        hosts = conn.execute(
            "SELECT * FROM user_hosts WHERE user_id=? ORDER BY alias", (user_id,)
        ).fetchall()
        items = [_public_host(row, _projects(conn, user_id, row["id"])) for row in hosts]
        pending = _pending_from_conn(conn, user_id)
        return {
            "uid": user_id,
            "hosts": items,
            "pending": pending,
            "base": public_origin(),
            "settings": settings_for(user_id, conn),
            "vault": "unlocked" if unlocked(user_id) else "locked",
        }


def get_host(user_id: int, host_id: int):
    row = db.fetchone("SELECT * FROM user_hosts WHERE id=? AND user_id=?", (host_id, user_id))
    if row is None:
        return None
    with db.tx() as conn:
        conn.execute("BEGIN")
        return _public_host(row, _projects(conn, user_id, host_id))


def add_host(user_id: int, raw: dict) -> dict:
    dek = _require_dek(user_id)
    alias = normalize_alias(raw.get("alias"))
    hostname = _text(raw.get("hostname"), "hostname", 253).lower().rstrip(".")
    username = _text(raw.get("username"), "username", 64)
    if not _USER_RE.match(username):
        raise HostError("invalid username")
    auth = _auth_type(raw.get("auth_type"))
    secret = raw.get("secret")
    if not isinstance(secret, str) or not secret.strip() or len(secret) > 16_000:
        raise HostError("secret is required")
    passphrase = raw.get("passphrase") or ""
    if not isinstance(passphrase, str) or len(passphrase) > 1024:
        raise HostError("invalid passphrase")
    label = raw.get("label") or ""
    if not isinstance(label, str) or len(label) > 80:
        raise HostError("invalid label")
    cwd = raw.get("default_cwd") or ""
    if not isinstance(cwd, str) or len(cwd) > 400:
        raise HostError("invalid default_cwd")
    port = _port(raw.get("port"))
    cipher = encrypt_with_dek(secret, dek)
    extra = encrypt_with_dek(passphrase, dek) if passphrase else ""
    ts = db.now()
    with db.tx() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """INSERT INTO user_hosts(
                    user_id, alias, label, hostname, port, username, auth_type, secret, passphrase,
                    default_cwd, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    user_id,
                    alias,
                    label.strip(),
                    hostname,
                    port,
                    username,
                    auth,
                    cipher,
                    extra,
                    cwd.strip(),
                    ts,
                    ts,
                ),
            )
        except IntegrityError as exc:
            if exc.errno == 1062:
                raise HostError("alias already exists", 409) from exc
            raise HostError("could not save host", 500) from exc
        host_id = db.last_id(conn)
        row = conn.execute("SELECT * FROM user_hosts WHERE id=?", (host_id,)).fetchone()
        return _public_host(row, [])


def patch_host(user_id: int, host_id: int, raw: dict) -> dict:
    allowed = {
        "alias",
        "label",
        "hostname",
        "port",
        "username",
        "auth_type",
        "secret",
        "passphrase",
        "default_cwd",
        "disabled",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise HostError("unsupported host fields")
    from . import chat_store as store

    with store.transaction() as conn:
        # Row-locked: every column is written back, so a concurrent patch read
        # from the same snapshot would revert a secret saved in between.
        row = conn.execute(
            "SELECT * FROM user_hosts WHERE id=? AND user_id=?" + db.for_update(conn), (host_id, user_id)
        ).fetchone()
        if row is None:
            raise HostError("host not found", 404)
        alias = row["alias"]
        if "alias" in raw:
            alias = normalize_alias(raw["alias"])
        hostname = row["hostname"]
        port = int(row["port"])
        clear_pin = False
        if "hostname" in raw:
            hostname = _text(raw.get("hostname"), "hostname", 253).lower().rstrip(".")
            if hostname != row["hostname"]:
                clear_pin = True
        if "port" in raw:
            port = _port(raw.get("port"))
            if port != int(row["port"]):
                clear_pin = True
        username = row["username"]
        if "username" in raw:
            username = _text(raw.get("username"), "username", 64)
            if not _USER_RE.match(username):
                raise HostError("invalid username")
        auth = row["auth_type"]
        if "auth_type" in raw:
            if row["auth_type"] == "agent":
                raise HostError("connector auth cannot be changed")
            auth = _auth_type(raw.get("auth_type"))
        label = row["label"]
        if "label" in raw:
            if not isinstance(raw["label"], str) or len(raw["label"]) > 80:
                raise HostError("invalid label")
            label = raw["label"].strip()
        cwd = row["default_cwd"]
        if "default_cwd" in raw:
            if not isinstance(raw["default_cwd"], str) or len(raw["default_cwd"]) > 400:
                raise HostError("invalid default_cwd")
            cwd = raw["default_cwd"].strip()
        disabled = row["disabled"]
        if "disabled" in raw:
            if not isinstance(raw["disabled"], bool):
                raise HostError("disabled must be true or false")
            disabled = 1 if raw["disabled"] else 0
        secret = row["secret"]
        passphrase = row["passphrase"]
        if "secret" in raw or "passphrase" in raw:
            if row["auth_type"] == "agent" or auth == "agent":
                raise HostError("connector has no SSH secret")
            dek = _require_dek(user_id)
            if "secret" in raw:
                if not isinstance(raw["secret"], str) or not raw["secret"].strip() or len(raw["secret"]) > 16_000:
                    raise HostError("invalid secret")
                secret = encrypt_with_dek(raw["secret"], dek)
            if "passphrase" in raw:
                if not isinstance(raw["passphrase"], str) or len(raw["passphrase"]) > 1024:
                    raise HostError("invalid passphrase")
                passphrase = encrypt_with_dek(raw["passphrase"], dek) if raw["passphrase"] else ""
        key_algo = row["key_algo"]
        fingerprint = row["fingerprint"]
        if clear_pin:
            key_algo = ""
            fingerprint = ""
        ts = db.now()
        try:
            conn.execute(
                """UPDATE user_hosts SET alias=?, label=?, hostname=?, port=?, username=?, auth_type=?,
                   secret=?, passphrase=?, default_cwd=?, disabled=?, key_algo=?, fingerprint=?, updated_at=?
                   WHERE id=? AND user_id=?""",
                (
                    alias,
                    label,
                    hostname,
                    port,
                    username,
                    auth,
                    secret,
                    passphrase,
                    cwd,
                    disabled,
                    key_algo,
                    fingerprint,
                    ts,
                    host_id,
                    user_id,
                ),
            )
        except IntegrityError as exc:
            if exc.errno == 1062:
                raise HostError("alias already exists", 409) from exc
            raise HostError("could not update host", 500) from exc
        if alias != row["alias"]:
            projects_changed(conn, user_id)
        row = conn.execute("SELECT * FROM user_hosts WHERE id=?", (host_id,)).fetchone()
        public = _public_host(row, _projects(conn, user_id, host_id))
    if clear_pin or any(k in raw for k in ("username", "auth_type", "secret", "passphrase")):
        # Drop the pooled session: it was opened against the old endpoint or
        # credentials and must not serve new work. After the commit, or a
        # connect in between could reopen it from the old row.
        _evict_pool(user_id, host_id)
    return public


def delete_host(user_id: int, host_id: int) -> bool:
    from . import chat_store as store

    with store.transaction() as conn:
        row = conn.execute(
            "SELECT id FROM user_hosts WHERE id=? AND user_id=?", (host_id, user_id)
        ).fetchone()
        if row is None:
            return False
        # Threads keep running without their host binding, but never point at
        # ids that this host's cascading project deletes (or a future reused
        # project rowid) will silently rebind to.
        bound = "user_id=? AND (host_id=? OR project_id IN (SELECT id FROM user_projects WHERE host_id=? AND user_id=?))"
        tids = [int(r["id"]) for r in conn.execute(
            "SELECT id FROM chat_threads WHERE " + bound, (user_id, host_id, host_id, user_id)).fetchall()]
        conn.execute(
            """UPDATE chat_threads SET host_id=NULL,
                   project_id=CASE WHEN project_id IN (SELECT id FROM user_projects WHERE host_id=? AND user_id=?) THEN NULL ELSE project_id END
               WHERE """ + bound,
            (host_id, user_id, user_id, host_id, host_id, user_id),
        )
        conn.execute("DELETE FROM user_hosts WHERE id=? AND user_id=?", (host_id, user_id))
        for tid in tids:
            store.thread_event(conn, user_id, tid)
        projects_changed(conn, user_id)
    _evict_pool(user_id, host_id)
    _publish(user_id, {"type": "host_removed", "id": int(host_id)})
    return True


def add_project(user_id: int, host_id: int, raw: dict) -> dict:
    name = _text(raw.get("name"), "name", 80)
    path = _text(raw.get("remote_path"), "remote_path", 400)
    notes = raw.get("notes") or ""
    if not isinstance(notes, str) or len(notes) > 400:
        raise HostError("invalid notes")
    model = raw.get("default_model") or ""
    if not isinstance(model, str) or len(model) > 200:
        raise HostError("invalid default_model")
    mode = _permission_mode(raw.get("permission_mode"))
    perms = _project_permissions(raw.get("default_permissions"))
    color = _color(raw["color"]) if raw.get("color") else secrets.choice(PROJECT_COLORS)
    ts = db.now()
    from . import chat_store as store

    with store.transaction() as conn:
        host = conn.execute(
            "SELECT id FROM user_hosts WHERE id=? AND user_id=?", (host_id, user_id)
        ).fetchone()
        if host is None:
            raise HostError("host not found", 404)
        try:
            conn.execute(
                """INSERT INTO user_projects(
                    user_id, host_id, name, remote_path, notes, default_model, permission_mode, default_permissions,
                    color, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (user_id, host_id, name, path, notes.strip(), model.strip(), mode, perms, color, ts, ts),
            )
        except IntegrityError as exc:
            raise HostError("project already exists on this host", 409) from exc
        pid = db.last_id(conn)
        projects_changed(conn, user_id)
        row = conn.execute("SELECT * FROM user_projects WHERE id=?", (pid,)).fetchone()
        return _project_view(row)


def patch_project(user_id: int, host_id: int, project_id: int, raw: dict) -> dict:
    allowed = {"name", "remote_path", "notes", "default_model", "permission_mode", "default_permissions", "color", "archived"}
    if set(raw) - allowed:
        raise HostError("unsupported project fields")
    from . import chat_store as store

    # A chat transaction: archive/restore emits thread events, and only this
    # one wakes other tabs and moves the watermarks after commit.
    with store.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM user_projects WHERE id=? AND user_id=? AND host_id=?",
            (project_id, user_id, host_id),
        ).fetchone()
        if row is None:
            raise HostError("project not found", 404)
        name = _text(raw["name"], "name", 80) if "name" in raw else row["name"]
        path = _text(raw["remote_path"], "remote_path", 400) if "remote_path" in raw else row["remote_path"]
        notes = row["notes"]
        if "notes" in raw:
            if not isinstance(raw["notes"], str) or len(raw["notes"]) > 400:
                raise HostError("invalid notes")
            notes = raw["notes"].strip()
        model = _row_get(row, "default_model") or ""
        if "default_model" in raw:
            if not isinstance(raw["default_model"], str) or len(raw["default_model"]) > 200:
                raise HostError("invalid default_model")
            model = raw["default_model"].strip()
        mode = _permission_mode(_row_get(row, "permission_mode") or "project")
        if "permission_mode" in raw:
            mode = _permission_mode(raw.get("permission_mode"), required=True)
        perms = _project_permissions(raw.get("default_permissions", _row_get(row, "default_permissions")))
        color = _color(raw["color"]) if "color" in raw else project_color(row["id"], _row_get(row, "color") or "")
        if "archived" in raw and not isinstance(raw["archived"], bool):
            raise HostError("archived must be true or false")
        ts = db.now()
        try:
            conn.execute(
                """UPDATE user_projects SET name=?, remote_path=?, notes=?, default_model=?, permission_mode=?,
                   default_permissions=?, color=?, updated_at=?
                   WHERE id=?""",
                (name, path, notes, model, mode, perms, color, ts, project_id),
            )
        except IntegrityError as exc:
            raise HostError("project already exists on this host", 409) from exc
        if "archived" in raw and raw["archived"] != bool(_row_get(row, "archived") or 0):
            set_project_archived(conn, user_id, project_id, raw["archived"])
        projects_changed(conn, user_id)
        row = conn.execute("SELECT * FROM user_projects WHERE id=?", (project_id,)).fetchone()
        return _project_view(row)


def projects_changed(conn, user_id: int) -> None:
    """Every open chat page redraws its project rows from this list."""
    from . import chat_store as store
    from .chat_threads import list_sidebar_projects

    store.event(conn, user_id, "projects.updated", payload={"projects": list_sidebar_projects(user_id, conn)})


def set_project_archived(conn, user_id: int, project_id: int, archived: bool, *, skip_running: bool = False) -> list[int]:
    """Archive a project with its open sessions, or restore it with the
    sessions that were archived along with it. Returns the touched thread ids."""
    from . import chat_store as store

    ts = db.now()
    if archived:
        conn.execute(
            "UPDATE user_projects SET archived=1, archived_at=?, updated_at=? WHERE id=? AND user_id=?",
            (ts, ts, project_id, user_id),
        )
        rows = conn.execute(
            "SELECT id FROM chat_threads WHERE user_id=? AND project_id=? AND kind='project' AND archived=0",
            (user_id, project_id),
        ).fetchall()
        ids = [int(r["id"]) for r in rows]
        if skip_running and ids:
            marks = ",".join("?" * len(ids))
            # Grouped so MariaDB does not take run row locks for a read.
            busy = {int(r["thread_id"]) for r in conn.execute(
                f"""SELECT thread_id, COUNT(*) AS n FROM chat_runs
                    WHERE thread_id IN ({marks}) AND status IN ('queued','running') GROUP BY thread_id""",
                ids,
            ).fetchall()}
            ids = [i for i in ids if i not in busy]
        for tid in ids:
            conn.execute(
                """UPDATE chat_threads SET archived=1, archived_at=?, archived_by_project=1, updated_at=?, revision=revision+1
                   WHERE id=? AND user_id=?""",
                (ts, ts, tid, user_id),
            )
    else:
        conn.execute(
            "UPDATE user_projects SET archived=0, archived_at='', updated_at=? WHERE id=? AND user_id=?",
            (ts, project_id, user_id),
        )
        rows = conn.execute(
            "SELECT id FROM chat_threads WHERE user_id=? AND project_id=? AND kind='project' AND archived=1 AND archived_by_project=1",
            (user_id, project_id),
        ).fetchall()
        ids = [int(r["id"]) for r in rows]
        for tid in ids:
            conn.execute(
                """UPDATE chat_threads SET archived=0, archived_at='', archived_by_project=0, active_at=?, updated_at=?, revision=revision+1
                   WHERE id=? AND user_id=?""",
                (ts, ts, tid, user_id),
            )
    for tid in ids:
        store.thread_event(conn, user_id, tid)
    return ids


def set_project_logo(user_id: int, project_id: int, mime: str, data: bytes) -> dict:
    if mime not in _LOGO_MAGIC:
        raise HostError("logo must be PNG, JPEG or WebP")
    if not data or len(data) > LOGO_MAX_BYTES:
        raise HostError(f"logo must be under {LOGO_MAX_BYTES // 1024} KB")
    if not any(data.startswith(magic) for magic in _LOGO_MAGIC[mime]) or (mime == "image/webp" and data[8:12] != b"WEBP"):
        raise HostError("logo file does not match its type")
    ts = db.now()
    from . import chat_store as store

    with store.transaction() as conn:
        row = conn.execute("SELECT * FROM user_projects WHERE id=? AND user_id=?", (project_id, user_id)).fetchone()
        if row is None:
            raise HostError("project not found", 404)
        conn.execute("DELETE FROM project_logos WHERE project_id=?", (project_id,))
        conn.execute(
            "INSERT INTO project_logos(project_id, user_id, mime, data, updated_at) VALUES (?,?,?,?,?)",
            (project_id, user_id, mime, data, ts),
        )
        conn.execute(
            "UPDATE user_projects SET logo_rev=logo_rev+1, updated_at=? WHERE id=? AND user_id=?",
            (ts, project_id, user_id),
        )
        projects_changed(conn, user_id)
        row = conn.execute("SELECT * FROM user_projects WHERE id=?", (project_id,)).fetchone()
        return _project_view(row)


def clear_project_logo(user_id: int, project_id: int) -> dict:
    from . import chat_store as store

    with store.transaction() as conn:
        row = conn.execute("SELECT * FROM user_projects WHERE id=? AND user_id=?", (project_id, user_id)).fetchone()
        if row is None:
            raise HostError("project not found", 404)
        conn.execute("DELETE FROM project_logos WHERE project_id=? AND user_id=?", (project_id, user_id))
        conn.execute("UPDATE user_projects SET logo_rev=0, updated_at=? WHERE id=? AND user_id=?", (db.now(), project_id, user_id))
        projects_changed(conn, user_id)
        row = conn.execute("SELECT * FROM user_projects WHERE id=?", (project_id,)).fetchone()
        return _project_view(row)


def project_logo(user_id: int, project_id: int):
    row = db.fetchone("SELECT mime, data FROM project_logos WHERE project_id=? AND user_id=?", (project_id, user_id))
    if row is None:
        return None
    data = row["data"]
    if isinstance(data, str):
        data = data.encode("latin-1")
    return row["mime"], bytes(data)


def delete_project(user_id: int, host_id: int, project_id: int, *, sessions: str = "delete",
                   cancel_active: bool = True, archived_before: str | None = None) -> bool:
    """Forget a project (the folder on the machine is never touched).

    sessions="delete" removes its chats too; "keep" turns them into plain
    chats so no thread points at a project id that no longer exists.
    archived_before (cleanup sweeps): delete only while the project is still
    archived since before that time and each session is still archived; a
    restore at any point stops the deletion (HostError 409) with nothing
    deleted."""
    from . import chat_store as store
    from .chat_threads import public_id_for

    if sessions not in ("delete", "keep"):
        raise HostError("sessions must be delete or keep")
    if sessions == "delete" and archived_before is not None:
        return _delete_archived_project(user_id, host_id, project_id, archived_before)
    # One transaction per mode: a failure part-way used to leave some
    # sessions deleted (or detached) while the project survived.
    active = []
    ids: list[int] = []
    with store.transaction() as conn:
        row = conn.execute(
            "SELECT id FROM user_projects WHERE id=? AND user_id=? AND host_id=?",
            (project_id, user_id, host_id),
        ).fetchone()
        if row is None:
            return False
        if sessions == "delete":
            threads = conn.execute(
                "SELECT id, revision, public_id FROM chat_threads WHERE user_id=? AND project_id=? AND kind='project'",
                (user_id, project_id),
            ).fetchall()
            ids = [int(t["id"]) for t in threads]
            if ids:
                active = conn.execute(
                    "SELECT * FROM chat_runs WHERE user_id=? AND status IN ('queued','running') AND thread_id IN ("
                    + ",".join("?" for _ in ids) + ")",
                    (user_id, *ids),
                ).fetchall()
            if active and not cancel_active:
                raise HostError("project has a running session; nothing was deleted", 409)
            # Same order as delete_thread: runs before threads.
            for run in active:
                conn.execute("UPDATE chat_runs SET cancel_requested=1,updated_at=? WHERE id=?", (db.now(), run["id"]))
                kind = "agent.cancel_requested" if run["parent_run_id"] else "run.cancel_requested"
                store.event(conn, user_id, kind, run["thread_id"], store._cancel_event_payload(run))
            for t in threads:
                conn.execute("DELETE FROM chat_runs WHERE thread_id=? AND user_id=?", (t["id"], user_id))
                conn.execute("DELETE FROM chat_threads WHERE id=? AND user_id=?", (t["id"], user_id))
                store.event(conn, user_id, "thread.deleted", t["id"],
                            {"revision": int(t["revision"]) + 1, "id": t["public_id"] or public_id_for(t["id"], conn)})
        else:
            for r in conn.execute(
                "SELECT id FROM chat_threads WHERE user_id=? AND project_id=?", (user_id, project_id)
            ).fetchall():
                conn.execute(
                    """UPDATE chat_threads SET kind='chat', host_id=NULL, project_id=NULL, archived_by_project=0,
                       updated_at=?, revision=revision+1 WHERE id=? AND user_id=?""",
                    (db.now(), r["id"], user_id),
                )
                store.thread_event(conn, user_id, r["id"])
        if archived_before is not None:
            project = conn.execute(
                "SELECT archived, archived_at, updated_at, created_at FROM user_projects"
                " WHERE id=? AND user_id=? AND host_id=?" + db.for_update(conn),
                (project_id, user_id, host_id),
            ).fetchone()
            since = (project["archived_at"] or project["updated_at"] or project["created_at"]) if project else ""
            if project is None or not project["archived"] or not since or since >= archived_before:
                raise HostError("project was restored; nothing was deleted", 409)
        # A session attached since the read above must not outlive the
        # project row it points at; raising rolls the whole delete back.
        live = conn.execute(
            "SELECT 1 FROM chat_threads WHERE user_id=? AND project_id=? AND kind='project' LIMIT 1",
            (user_id, project_id),
        ).fetchone()
        if live is not None:
            raise HostError("project still has sessions; nothing was deleted", 409)
        result = conn.execute(
            "DELETE FROM user_projects WHERE id=? AND user_id=? AND host_id=?",
            (project_id, user_id, host_id),
        )
        projects_changed(conn, user_id)
    # Wake live workers after the commit, as delete_thread does.
    from . import chat_runs, command_jobs
    from .vault import unpin_run

    for run in active:
        chat_runs.cancel(run["id"])
        unpin_run(user_id, run["id"])  # its row is gone: finish_run cannot unpin it
    for tid in ids:
        try:
            command_jobs.cancel_thread(user_id, tid)
        except Exception:
            logger.exception("Could not cancel command jobs of deleted thread %s", tid)
    return result.rowcount > 0


def _delete_archived_project(user_id: int, host_id: int, project_id: int, archived_before: str) -> bool:
    """Retention: delete an archived project with its archived sessions in
    one transaction. Deleting session by session let a restore racing the
    sweep stop it halfway, with the deleted sessions gone for good."""
    from . import chat_store as store
    from .chat_threads import public_id_for

    with store.transaction() as conn:
        project = conn.execute(
            "SELECT archived, archived_at, updated_at, created_at FROM user_projects"
            " WHERE id=? AND user_id=? AND host_id=?" + db.for_update(conn),
            (project_id, user_id, host_id),
        ).fetchone()
        if project is None:
            return False
        since = project["archived_at"] or project["updated_at"] or project["created_at"]
        if not project["archived"] or not since or since >= archived_before:
            raise HostError("project was restored; nothing was deleted", 409)
        threads = conn.execute(
            "SELECT id, archived, revision, public_id FROM chat_threads WHERE user_id=? AND project_id=? AND kind='project'"
            + db.for_update(conn),
            (user_id, project_id),
        ).fetchall()
        if any(not t["archived"] for t in threads):
            raise HostError("project still has sessions; nothing was deleted", 409)
        ids = [int(t["id"]) for t in threads]
        if ids and conn.execute(
            "SELECT 1 FROM chat_runs WHERE user_id=? AND status IN ('queued','running') AND thread_id IN ("
            + ",".join("?" for _ in ids) + ") LIMIT 1",
            (user_id, *ids),
        ).fetchone():
            raise HostError("project has a running session; nothing was deleted", 409)
        for t in threads:
            conn.execute("DELETE FROM chat_runs WHERE thread_id=? AND user_id=?", (t["id"], user_id))
            conn.execute("DELETE FROM chat_threads WHERE id=? AND user_id=?", (t["id"], user_id))
            store.event(conn, user_id, "thread.deleted", t["id"],
                        {"revision": int(t["revision"]) + 1, "id": t["public_id"] or public_id_for(t["id"], conn)})
        result = conn.execute(
            "DELETE FROM user_projects WHERE id=? AND user_id=? AND host_id=?",
            (project_id, user_id, host_id),
        )
        projects_changed(conn, user_id)
    from . import command_jobs

    for tid in ids:
        try:
            command_jobs.cancel_thread(user_id, tid)
        except Exception:
            logger.exception("Could not cancel command jobs of deleted thread %s", tid)
    return result.rowcount > 0


def secret_plain(user_id: int, host_id: int) -> tuple[str, str, dict]:
    """Return (secret, passphrase, public host). Never logs. Raises HostError."""
    row = db.fetchone("SELECT * FROM user_hosts WHERE id=? AND user_id=?", (host_id, user_id))
    if row is None:
        raise HostError("host not found", 404)
    if row["auth_type"] == "agent":
        raise HostError("connector hosts are not SSH")
    dek = _require_dek(user_id)
    try:
        secret = decrypt_with_dek(row["secret"], dek)
        extra = decrypt_with_dek(row["passphrase"], dek) if row["passphrase"] else ""
    except DecryptionError as exc:
        raise HostError("host secret could not be decrypted", 500) from exc
    return secret, extra, _public_host(row)


def set_pin(user_id: int, host_id: int, algorithm: str, fingerprint: str) -> dict:
    algo = _text(algorithm, "algorithm", 32)
    digest = _text(fingerprint, "fingerprint", 128).lower().replace(":", "")
    if not re.fullmatch(r"[0-9a-f]{32,128}", digest):
        raise HostError("invalid fingerprint")
    ts = db.now()
    with db.tx() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id FROM user_hosts WHERE id=? AND user_id=?", (host_id, user_id)
        ).fetchone()
        if row is None:
            raise HostError("host not found", 404)
        conn.execute(
            "UPDATE user_hosts SET key_algo=?, fingerprint=?, last_error='', updated_at=? WHERE id=?",
            (algo, digest, ts, host_id),
        )
        row = conn.execute("SELECT * FROM user_hosts WHERE id=?", (host_id,)).fetchone()
        return _public_host(row, _projects(conn, user_id, host_id))


def set_host_status(user_id: int, host_id: int, *, ok: bool, error: str = "") -> None:
    err = (error or "")[:400]
    ts = db.now()
    if ok:
        db.execute(
            "UPDATE user_hosts SET last_ok_at=?, last_error='', updated_at=? WHERE id=? AND user_id=?",
            (ts, ts, host_id, user_id),
        )
    else:
        db.execute(
            "UPDATE user_hosts SET last_error=?, updated_at=? WHERE id=? AND user_id=?",
            (err, ts, host_id, user_id),
        )
    view = get_host(user_id, host_id)
    if view:
        _publish(user_id, {"type": "host", "host": view})


def _project_catalog_bit(project: dict) -> str:
    extra = []
    if project.get("default_model"):
        extra.append(project["default_model"])
    if project.get("permission_mode") == "read_only":
        extra.append("read-only")
    suffix = f" [{', '.join(extra)}]" if extra else ""
    return f"{project['name']} ({project['remote_path']}){suffix}"


def _catalog_work_root(host: dict, *, bound_host_id=None, bound_cwd: str = "") -> str:
    if bound_host_id is not None and int(host["id"]) == int(bound_host_id) and bound_cwd:
        return bound_cwd
    return host.get("default_cwd") or ""


def _catalog_roots_bit(host: dict, *, bound_host_id=None, bound_cwd: str = "") -> str:
    from . import permissions

    windows = str(host.get("agent_os") or "").strip().lower().startswith("win")
    roots = permissions.compute_roots(_catalog_work_root(host, bound_host_id=bound_host_id, bound_cwd=bound_cwd), windows)
    return f"  roots: {', '.join(roots)}"


def catalog_for_prompt(user_id: int, *, clip: bool = True, bound_host_id=None, bound_project_id=None, bound_cwd: str = "", only_bound: bool = False) -> str:
    bundle = list_bundle(user_id)
    lines = []
    bound_line = ""
    # The bound host leads so a clipped catalog never drops its roots line.
    ordered = sorted(bundle["hosts"], key=lambda h: bound_host_id is None or int(h["id"]) != int(bound_host_id))
    for host in ordered:
        if host["disabled"]:
            continue
        if only_bound and bound_host_id is not None and int(host["id"]) != int(bound_host_id):
            continue
        projects = ", ".join(_project_catalog_bit(p) for p in host["projects"][:8]) or "(no projects)"
        extra = _catalog_roots_bit(host, bound_host_id=bound_host_id, bound_cwd=bound_cwd)
        cwd = _catalog_work_root(host, bound_host_id=bound_host_id, bound_cwd=bound_cwd)
        if host.get("auth_type") == "agent":
            ident = f"connector {host.get('username') or ''}@{host.get('hostname') or host['alias']}".strip()
            osn = (host.get("agent_os") or "").strip().lower()
            shell = "  windows powershell" if osn.startswith("win") else ("  linux bash" if osn == "linux" else "")
            lines.append(f"- {host['alias']}  {ident}  agent{shell}{extra}  projects: {projects}")
        else:
            lines.append(
                f"- {host['alias']}  {host['username']}@{host['hostname']}:{host['port']}  {host['auth_type']}{extra}  projects: {projects}"
            )
        if bound_host_id is not None and int(host["id"]) == int(bound_host_id):
            if bound_project_id is not None:
                match = next((p for p in host["projects"] if int(p["id"]) == int(bound_project_id)), None)
                if match:
                    bound_line = f"This chat is bound to {host['alias']} / {match['name']} at {match['remote_path']}."
            if not bound_line:
                bound_line = f"This chat is bound to {host['alias']}" + (f" cwd={cwd}." if cwd else ".")
    if not lines:
        return bound_line
    text = ("Hosts:\n" + "\n".join(lines)) if not bound_line else (bound_line + "\nHosts:\n" + "\n".join(lines))
    if not clip:
        return text[:8000]
    if len(text) <= CATALOG_CHARS:
        return text
    suffix = "\n… (search_tools query hosts for the rest)"
    keep = text[:max(0, CATALOG_CHARS - len(suffix))]
    # Whole lines only: a path cut mid-word reads as a real one.
    cut = keep.rfind("\n")
    return (keep[:cut] if cut > 0 else keep).rstrip() + suffix


def resolve_project(user_id: int, name: str) -> dict | None:
    needle = (name or "").strip()
    if not needle:
        return None
    rows = db.fetchall(
        """SELECT p.id AS project_id, p.name, p.remote_path, p.default_model, p.permission_mode,
                  h.id AS host_id, h.alias
           FROM user_projects p JOIN user_hosts h ON h.id=p.host_id
           WHERE p.user_id=? AND p.name=? AND h.disabled=0""",
        (user_id, needle),
    )
    if len(rows) != 1:
        return None
    row = rows[0]
    return _resolved_project(row)


def _resolved_project(row) -> dict:
    mode = _row_get(row, "permission_mode") or "project"
    if mode not in ("project", "read_only"):
        mode = "project"
    return {
        "project_id": int(row["project_id"] if "project_id" in row.keys() else row["id"]),
        "name": row["name"],
        "remote_path": row["remote_path"],
        "default_model": _row_get(row, "default_model") or "",
        "permission_mode": mode,
        "host_id": int(row["host_id"]),
        "alias": row["alias"],
    }


def resolve_project_id(user_id: int, project_id: int) -> dict | None:
    try:
        pid = int(project_id)
    except (TypeError, ValueError):
        return None
    row = db.fetchone(
        """SELECT p.id AS project_id, p.name, p.remote_path, p.default_model, p.permission_mode,
                  h.id AS host_id, h.alias
           FROM user_projects p JOIN user_hosts h ON h.id=p.host_id
           WHERE p.id=? AND p.user_id=? AND h.user_id=? AND h.disabled=0""",
        (pid, user_id, user_id),
    )
    if row is None:
        return None
    return _resolved_project(row)


def get_project(user_id: int, host_id: int, project_id: int) -> dict | None:
    row = db.fetchone(
        """SELECT p.*, h.alias
           FROM user_projects p JOIN user_hosts h ON h.id=p.host_id
           WHERE p.id=? AND p.user_id=? AND p.host_id=? AND h.user_id=?""",
        (project_id, user_id, host_id, user_id),
    )
    if row is None:
        return None
    view = _project_view(row)
    view["host_id"] = int(host_id)
    view["alias"] = row["alias"]
    return view


def latest_project_thread_id(user_id: int, host_id: int, project_id: int):
    row = db.fetchone(
        """SELECT public_id, id FROM chat_threads
           WHERE user_id=? AND host_id=? AND project_id=? AND archived=0
           ORDER BY updated_at DESC, id DESC LIMIT 1""",
        (user_id, host_id, project_id),
    )
    if not row:
        return None
    if "public_id" in row.keys() and row["public_id"]:
        return str(row["public_id"])
    return str(int(row["id"]))


def effective_permission(user_id: int, *, bound_host_id=None, bound_cwd="") -> str:
    mode = settings_for(user_id).get("permission_mode") or "project"
    if mode == "read_only":
        return "read_only"
    if not bound_host_id or not bound_cwd:
        return mode if mode in ("project", "read_only") else "project"
    # Prefix, not equality: a subfolder of a read-only project (a subagent
    # spawned with cwd=<project>/sub, or a write target inside it) is still
    # read-only.
    rows = db.fetchall(
        "SELECT remote_path FROM user_projects WHERE user_id=? AND host_id=? AND permission_mode='read_only'",
        (user_id, int(bound_host_id)),
    )
    if any(_path_within(bound_cwd, _row_get(r, "remote_path") or "") for r in rows):
        return "read_only"
    return "project"


def _path_within(path: str, parent: str) -> bool:
    from .permissions import _norm, _under

    if not path or not parent:
        return False
    windows = bool(re.match(r"^[A-Za-z]:", parent)) or "\\" in parent or bool(re.match(r"^[A-Za-z]:", path))
    return _under(_norm(path, windows), _norm(parent, windows), windows)


def host_by_alias(user_id: int, alias: str):
    if not alias:
        return None
    return db.fetchone(
        "SELECT * FROM user_hosts WHERE user_id=? AND alias=? AND disabled=0",
        (user_id, alias.strip().lower()),
    )


def install_line(code: str, origin: str = "") -> str:
    base = (origin or public_origin()).rstrip("/")
    token = (code or "").strip()
    if not base or not _CODE_RE.match(token):
        return ""
    return f"curl -fsSL {base}/i/{token} | bash"


def install_windows_line(code: str, origin: str = "") -> str:
    base = (origin or public_origin()).rstrip("/")
    token = (code or "").strip()
    if not base or not _CODE_RE.match(token):
        return ""
    return f"irm {base}/i/{token}.ps1 | iex"


def download_windows_url(origin: str = "") -> str:
    base = (origin or public_origin()).rstrip("/")
    if not base:
        return ""
    return f"{base}/agent/dist/fuse-agent-windows-amd64.exe"


def _pairing_view(row, origin: str = "") -> dict:
    """Pending view. The code is reveal-once (mint response only); never re-read from disk."""
    return {
        "id": int(row["id"]),
        "prefix": row["prefix"] or "",
        "alias_hint": row["alias_hint"] or "",
        "expires_at": row["expires_at"],
        "created_at": row["created_at"],
        "code": "",
        "install": "",
        "install_windows": "",
        "download_windows": "",
    }


def _pending_from_conn(conn, user_id: int) -> list[dict]:
    now = db.now()
    rows = conn.execute(
        """SELECT * FROM host_pairings
           WHERE user_id=? AND used_at IS NULL AND expires_at>?
           ORDER BY id DESC""",
        (user_id, now),
    ).fetchall()
    origin = public_origin()
    return [_pairing_view(row, origin) for row in rows]


def list_pending_pairings(user_id: int) -> list[dict]:
    with db.tx() as conn:
        conn.execute("BEGIN")
        return _pending_from_conn(conn, user_id)


def _evict_pool(user_id: int, host_id: int) -> None:
    try:
        from . import ssh_pool

        ssh_pool.evict(user_id, host_id)
    except Exception:
        pass


def mint_pairing(user_id: int, alias_hint: str = "") -> dict:
    if alias_hint is None:
        alias_hint = ""
    if not isinstance(alias_hint, str):
        raise HostError("invalid alias_hint")
    hint = alias_hint.strip().lower()
    if hint:
        hint = normalize_alias(hint)
    now = db.now()
    with db.tx() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM host_pairings WHERE user_id=? AND (expires_at<? OR (used_at IS NOT NULL AND used_at<?))",
            (user_id, now, now),
        )
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM host_pairings WHERE user_id=? AND used_at IS NULL AND expires_at>?",
            (user_id, now),
        ).fetchone()["c"]
        if int(n) >= MAX_PENDING_PAIRINGS:
            raise HostError("too many unused install codes")
        for _ in range(8):
            code = "fuse_" + secrets.token_urlsafe(16)
            if not _CODE_RE.match(code):
                continue
            digest = token_hash(code)
            try:
                conn.execute(
                    """INSERT INTO host_pairings(
                        user_id, code_hash, code, prefix, alias_hint, expires_at, created_at
                    ) VALUES (?,?,?,?,?,?,?)""",
                    (
                        user_id,
                        digest,
                        "",
                        code[:10],
                        hint,
                        _iso_in(PAIRING_TTL),
                        now,
                    ),
                )
            except IntegrityError:
                continue
            row = conn.execute("SELECT * FROM host_pairings WHERE id=?", (db.last_id(conn),)).fetchone()
            view = _pairing_view(row, public_origin())
            view["code"] = code
            origin = public_origin()
            view["install"] = install_line(code, origin)
            view["install_windows"] = install_windows_line(code, origin)
            view["download_windows"] = download_windows_url(origin)
            break
        else:
            raise HostError("could not mint install code", 500)
    _publish(user_id, {"type": "pending", "pairing": view})
    return view


def cancel_pairing(user_id: int, pairing_id: int) -> bool:
    with db.tx() as conn:
        conn.execute("BEGIN IMMEDIATE")
        result = conn.execute(
            "DELETE FROM host_pairings WHERE id=? AND user_id=? AND used_at IS NULL",
            (pairing_id, user_id),
        )
        ok = result.rowcount > 0
    if ok:
        _publish(user_id, {"type": "pending_gone", "id": int(pairing_id)})
    return ok


def _slug_alias(raw: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", (raw or "").lower()).strip("-")
    text = text[:40].strip("-") or "machine"
    if text[0].isdigit():
        text = "m-" + text[:38]
    if text in RESERVED_ALIASES or not _ALIAS_RE.match(text):
        text = "machine"
    return text


def _unique_alias(conn, user_id: int, hint: str) -> str:
    base = _slug_alias(hint)
    alias = base
    n = 2
    while conn.execute(
        "SELECT 1 FROM user_hosts WHERE user_id=? AND alias=?", (user_id, alias)
    ).fetchone():
        suffix = f"-{n}"
        alias = (base[: 40 - len(suffix)] + suffix)
        n += 1
        if n > 99:
            alias = "m-" + secrets.token_hex(4)
            break
    return alias


def _agent_username(raw) -> str:
    text = (raw or "user").strip()
    if not _USER_RE.match(text):
        text = re.sub(r"[^A-Za-z0-9._-]+", "", text)[:64] or "user"
    return text[:64]


def undo_pairing(user_id: int, host_id: int, code: str) -> None:
    """The device token never reached the connector (hello_ok send failed):
    forget the new host and let the same install code be redeemed again."""
    delete_host(user_id, host_id)
    db.execute(
        "UPDATE host_pairings SET used_at=NULL WHERE code_hash=? AND user_id=?",
        (token_hash((code or "").strip()), int(user_id)),
    )


def redeem_pairing(code: str, meta: dict | None = None) -> tuple[dict, str]:
    """Burn a pairing code. Returns (public host, raw device token). No vault."""
    token = (code or "").strip()
    if not _CODE_RE.match(token):
        raise HostError("invalid install code", 401)
    meta = meta if isinstance(meta, dict) else {}
    hostname = _text(str(meta.get("hostname") or "machine"), "hostname", 253).lower().rstrip(".")
    username = _agent_username(meta.get("username") or meta.get("user"))
    version = meta.get("ver") or meta.get("version") or ""
    if not isinstance(version, str) or len(version) > 40:
        version = ""
    os_name = meta.get("os") or ""
    if not isinstance(os_name, str) or len(os_name) > 40:
        os_name = ""
    else:
        os_name = os_name.strip().lower()
    arch = meta.get("arch") or ""
    if not isinstance(arch, str) or len(arch) > 40:
        arch = ""
    else:
        arch = arch.strip().lower()
    now = db.now()
    digest = token_hash(token)
    device = AGENT_TOKEN_PREFIX + secrets.token_urlsafe(32)
    with db.tx() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM host_pairings WHERE code_hash=? AND used_at IS NULL", (digest,)
        ).fetchone()
        if row is None:
            raise HostError("invalid install code", 401)
        if row["expires_at"] <= now:
            raise HostError("install code expired", 401)
        # Claim before registering: BEGIN IMMEDIATE does not lock this row on
        # MariaDB, so the conditional UPDATE is what makes the code single-use.
        claimed = conn.execute(
            "UPDATE host_pairings SET used_at=?, code='' WHERE id=? AND used_at IS NULL",
            (now, row["id"]),
        )
        if claimed.rowcount != 1:
            raise HostError("invalid install code", 401)
        user_id = int(row["user_id"])
        agents = conn.execute(
            "SELECT COUNT(*) AS c FROM user_hosts WHERE user_id=? AND auth_type='agent'",
            (user_id,),
        ).fetchone()["c"]
        if int(agents) >= MAX_AGENT_HOSTS:
            raise HostError("too many connector machines", 409)
        hint = row["alias_hint"] or hostname
        for _ in range(8):
            alias = _unique_alias(conn, user_id, hint)
            try:
                conn.execute(
                    """INSERT INTO user_hosts(
                        user_id, alias, label, hostname, port, username, auth_type, secret, passphrase,
                        default_cwd, device_token_hash, agent_version, agent_os, agent_arch,
                        last_seen_at, last_ok_at, last_error,
                        created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        user_id,
                        alias,
                        "",
                        hostname,
                        0,
                        username,
                        "agent",
                        "",
                        "",
                        "",
                        token_hash(device),
                        version.strip(),
                        os_name,
                        arch,
                        now,
                        now,
                        "",
                        now,
                        now,
                    ),
                )
            except IntegrityError:
                # A concurrent redeem took the alias first; pick another.
                hint = f"{hint}-{secrets.token_hex(2)}"
                continue
            break
        else:
            raise HostError("could not register connector machine", 500)
        host_id = db.last_id(conn)
        host_row = conn.execute("SELECT * FROM user_hosts WHERE id=?", (host_id,)).fetchone()
        view = _public_host(host_row, [])
        pairing_id = int(row["id"])
    view["online"] = True
    view["user_id"] = user_id
    _publish(user_id, {"type": "pending_gone", "id": pairing_id})
    _publish(user_id, {"type": "host", "host": view})
    return view, device


def host_by_device_token(raw: str):
    token = (raw or "").strip()
    if not token.startswith(AGENT_TOKEN_PREFIX) or len(token) < 20:
        return None
    return db.fetchone(
        "SELECT * FROM user_hosts WHERE device_token_hash=? AND auth_type='agent' AND disabled=0",
        (token_hash(token),),
    )


def touch_agent(
    user_id: int,
    host_id: int,
    *,
    version: str = "",
    hostname: str = "",
    username: str = "",
    os_name: str = "",
    arch: str = "",
) -> dict:
    now = db.now()
    with db.tx() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM user_hosts WHERE id=? AND user_id=? AND auth_type='agent'",
            (host_id, user_id),
        ).fetchone()
        if row is None:
            raise HostError("host not found", 404)
        ver = version.strip()[:40] if isinstance(version, str) else (row["agent_version"] or "")
        hostn = row["hostname"]
        user = row["username"]
        if hostname:
            try:
                hostn = _text(hostname, "hostname", 253).lower().rstrip(".")
            except HostError:
                hostn = row["hostname"]
        if username:
            user = _agent_username(username)
        osn = _row_get(row, "agent_os") or ""
        archn = _row_get(row, "agent_arch") or ""
        if isinstance(os_name, str) and os_name.strip() and len(os_name) <= 40:
            osn = os_name.strip().lower()
        if isinstance(arch, str) and arch.strip() and len(arch) <= 40:
            archn = arch.strip().lower()
        conn.execute(
            """UPDATE user_hosts SET last_seen_at=?, last_ok_at=?, last_error='', agent_version=?,
               hostname=?, username=?, agent_os=?, agent_arch=?, updated_at=? WHERE id=?""",
            (now, now, ver, hostn, user, osn, archn, now, host_id),
        )
        row = conn.execute("SELECT * FROM user_hosts WHERE id=?", (host_id,)).fetchone()
        view = _public_host(row, _projects(conn, user_id, host_id))
    view["online"] = True
    _publish(user_id, {"type": "host", "host": view})
    return view


def mark_agent_offline(user_id: int, host_id: int) -> None:
    row = db.fetchone(
        "SELECT * FROM user_hosts WHERE id=? AND user_id=? AND auth_type='agent'",
        (host_id, user_id),
    )
    if row is None:
        return
    view = get_host(user_id, host_id)
    if view:
        view["online"] = False
        _publish(user_id, {"type": "host", "host": view})


def rotate_agent_token(user_id: int, host_id: int) -> str:
    device = AGENT_TOKEN_PREFIX + secrets.token_urlsafe(32)
    now = db.now()
    with db.tx() as conn:
        conn.execute("BEGIN IMMEDIATE")
        result = conn.execute(
            """UPDATE user_hosts SET device_token_hash=?, updated_at=?
               WHERE id=? AND user_id=? AND auth_type='agent'""",
            (token_hash(device), now, host_id, user_id),
        )
        if result.rowcount <= 0:
            raise HostError("host not found", 404)
    return device


def _publish(user_id: int, msg: dict) -> None:
    try:
        from . import host_events

        host_events.publish(user_id, msg)
    except Exception:
        pass
