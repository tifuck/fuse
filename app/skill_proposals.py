"""Pending in-chat skill drafts. Disk write happens only on confirm."""
from __future__ import annotations

import json
import logging
from uuid import uuid4

from . import db, skills
from .security import limiter
from .skill_install import MAX_FILE, MAX_FILES, skill_dir, validate_files
from .skill_parse import DESC_MAX, HINT_MAX, NAME_RE, SkillError, normalize_slug, parse_skill_md

logger = logging.getLogger("app.skills")

PENDING_CAP_USER = 8
PENDING_CAP_THREAD = 3
TTL_HOURS = 24
STATUSES = frozenset({"pending", "saved", "rejected", "expired", "error"})


def expire_stale(conn=None) -> None:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=TTL_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    stuck = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    sql = (
        "UPDATE skill_proposals SET status='expired' WHERE status='pending' AND created_at < ?"
    )
    sql_stuck = (
        # A confirm that died between claim and finish goes back to pending;
        # the skill may or may not have installed, so the user re-decides.
        "UPDATE skill_proposals SET status='pending', resolved_at=NULL WHERE status='resolving' AND resolved_at < ?"
    )
    if conn is not None:
        conn.execute(sql, (cutoff,))
        conn.execute(sql_stuck, (stuck,))
    else:
        db.execute(sql, (cutoff,))
        db.execute(sql_stuck, (stuck,))


def _files_json(files: dict[str, str]) -> str:
    return json.dumps(files, ensure_ascii=False, separators=(",", ":"))


def _parse_files(raw) -> dict[str, str]:
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw or "{}")
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for key, value in data.items():
        if isinstance(key, str) and isinstance(value, str):
            out[key] = value
        if len(out) >= MAX_FILES:
            break
    return out


def _row(row) -> dict:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "thread_id": row["thread_id"],
        "run_id": row["run_id"] or "",
        "message_id": row["message_id"] or "",
        "slug": row["slug"],
        "description": row["description"] or "",
        "body": row["body"] or "",
        "files": _parse_files(row["files"]),
        "enable": bool(row["enable"]),
        "argument_hint": row["argument_hint"] or "",
        "overwrite": bool(row["overwrite"]),
        "status": row["status"],
        "created_at": row["created_at"],
        "resolved_at": row["resolved_at"] or "",
        "origin_existing": "",
    }


def public_card(item: dict) -> dict:
    files = item.get("files") or {}
    names = ["SKILL.md", *[k for k in files if k != "SKILL.md"]]
    return {
        "id": item["id"],
        "kind": "skill_save",
        "status": item["status"],
        "slug": item["slug"],
        "description": (item.get("description") or "")[:DESC_MAX],
        "overwrite": bool(item.get("overwrite")),
        "enable": bool(item.get("enable", True)),
        "files": names[:MAX_FILES],
        "origin_existing": item.get("origin_existing") or "",
        "error": item.get("error") or "",
    }


def public_full(item: dict) -> dict:
    data = public_card(item)
    data["body"] = item.get("body") or ""
    data["files_text"] = item.get("files") or {}
    data["argument_hint"] = item.get("argument_hint") or ""
    data["thread_id"] = item.get("thread_id")
    return data


def get_proposal(user_id: int, proposal_id: str, conn=None) -> dict | None:
    expire_stale(conn)
    q = "SELECT * FROM skill_proposals WHERE id=? AND user_id=?"
    row = conn.execute(q, (proposal_id, user_id)).fetchone() if conn is not None else db.fetchone(q, (proposal_id, user_id))
    return _row(row) if row else None


def _synthetic_md(slug: str, description: str, body: str, hint: str = "") -> str:
    desc = (description or "").replace("\n", " ").strip()
    hint = " ".join((hint or "").split())
    # The saved skill reads its hint back from this frontmatter only.
    quote = "'" if '"' in hint else '"'
    hint_line = f"argument-hint: {quote}{hint}{quote}\n" if hint else ""
    return f"---\nname: {slug}\ndescription: {desc}\n{hint_line}---\n\n{body or ''}\n"


def _to_bytes(files: dict[str, str]) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for rel, text in files.items():
        if not isinstance(rel, str) or not isinstance(text, str):
            raise SkillError("invalid skill file")
        out[rel] = text.encode("utf-8")
    return out


def validate_draft(*, slug: str, description: str, body: str, files: dict[str, str], argument_hint: str = "") -> tuple[str, dict[str, str]]:
    name = normalize_slug(slug)
    if not NAME_RE.fullmatch(name) or name in skills.RESERVED:
        raise SkillError("invalid skill name")
    if not isinstance(description, str) or not description.strip() or len(description) > DESC_MAX:
        raise SkillError("invalid description")
    if not isinstance(body, str) or not body.strip():
        raise SkillError("skill body is required")
    if len(body.encode("utf-8")) > MAX_FILE:
        raise SkillError("skill file too large")
    hint = (argument_hint or "")[:HINT_MAX]
    extra = files if isinstance(files, dict) else {}
    rebuilt: dict[str, str] = {}
    for rel, text in extra.items():
        if rel in ("SKILL.md", "./SKILL.md"):
            continue
        if not isinstance(rel, str) or not isinstance(text, str):
            raise SkillError("invalid skill file")
        rebuilt[rel] = text
    rebuilt["SKILL.md"] = _synthetic_md(name, description.strip(), body, hint)
    parsed = parse_skill_md(rebuilt["SKILL.md"], slug=name)
    if parsed["name"] != name:
        raise SkillError("invalid skill name")
    validate_files(_to_bytes(rebuilt))
    if hint:
        rebuilt["_hint"] = hint  # not written to disk; stripped before persist
    return name, rebuilt


def create(
    user_id: int,
    *,
    thread_id: int,
    run_id: str = "",
    message_id: str = "",
    name: str,
    description: str,
    body: str,
    files: dict | None = None,
    enable: bool = True,
    argument_hint: str = "",
) -> dict:
    expire_stale()
    if not limiter.allow(f"skill-propose:{user_id}", 10, 60):
        raise SkillError("too many skill drafts", 429)
    extra = {}
    if isinstance(files, dict):
        extra = {str(k): v for k, v in files.items() if isinstance(v, str)}
    slug, rebuilt = validate_draft(
        slug=name, description=description, body=body, files=extra, argument_hint=argument_hint
    )
    hint = rebuilt.pop("_hint", (argument_hint or "")[:HINT_MAX])
    existing = skills.get_row(user_id, slug)
    on_disk = skill_dir(user_id, slug).exists()
    overwrite = bool(existing or on_disk)
    origin_existing = (existing or {}).get("origin") or ("disk" if on_disk else "")
    proposal_id = str(uuid4())
    now = db.now()
    store_files = {k: v for k, v in rebuilt.items() if k != "SKILL.md"}
    with db.tx() as conn:
        conn.execute("BEGIN IMMEDIATE")
        expire_stale(conn)
        n_user = conn.execute(
            "SELECT COUNT(*) AS n FROM skill_proposals WHERE user_id=? AND status='pending'",
            (user_id,),
        ).fetchone()["n"]
        if int(n_user) >= PENDING_CAP_USER:
            raise SkillError("too many pending skill drafts", 429)
        n_thread = conn.execute(
            "SELECT COUNT(*) AS n FROM skill_proposals WHERE user_id=? AND thread_id=? AND status='pending'",
            (user_id, thread_id),
        ).fetchone()["n"]
        if int(n_thread) >= PENDING_CAP_THREAD:
            raise SkillError("too many pending skill drafts in this chat", 429)
        conn.execute(
            """INSERT INTO skill_proposals(
                   id, user_id, thread_id, run_id, message_id, slug, description, body, files,
                   enable, argument_hint, overwrite, status, created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'pending', ?)""",
            (
                proposal_id,
                user_id,
                int(thread_id),
                run_id or "",
                message_id or "",
                slug,
                description.strip()[:DESC_MAX],
                body,
                _files_json(store_files),
                1 if enable else 0,
                hint,
                1 if overwrite else 0,
                now,
            ),
        )
    item = get_proposal(user_id, proposal_id)
    if item:
        item["origin_existing"] = origin_existing
        item["overwrite"] = overwrite
        item["files"] = {"SKILL.md": rebuilt["SKILL.md"], **store_files}
    return item or {"id": proposal_id, "slug": slug, "overwrite": overwrite, "origin_existing": origin_existing}


def _creating_agent(run_id: str) -> dict:
    if not run_id:
        return {}
    row = db.fetchone("SELECT request FROM chat_runs WHERE id=?", (run_id,))
    if not row:
        return {}
    try:
        request = json.loads(row["request"] or "{}")
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    agent = request.get("agent") if isinstance(request, dict) else None
    return agent if isinstance(agent, dict) else {}


def _patch_permission(user_id: int, thread_id: int, proposal_id: str, status: str, extra: dict | None = None) -> None:
    from . import chat_store as store
    from . import chats

    with store.transaction() as conn:
        row = conn.execute(
            "SELECT messages, revision FROM chat_threads WHERE id=? AND user_id=?",
            (thread_id, user_id),
        ).fetchone()
        if row is None:
            return
        messages = chats.parse_messages(row["messages"])
        changed = False
        for msg in messages:
            perms = msg.get("permissions") or []
            for perm in perms:
                if perm.get("id") == proposal_id:
                    perm["status"] = status
                    if extra:
                        perm.update({k: extra[k] for k in extra if k in perm or k in ("error", "slug", "description", "overwrite", "enable", "files", "origin_existing")})
                    changed = True
        if not changed:
            return
        payload = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
        ts = db.now()
        conn.execute(
            "UPDATE chat_threads SET messages=?, revision=revision+1, updated_at=? WHERE id=? AND user_id=?",
            (payload, ts, thread_id, user_id),
        )
        store.thread_event(conn, user_id, thread_id)


def confirm_proposal(user_id: int, proposal_id: str, raw: dict) -> dict:
    expire_stale()
    item = get_proposal(user_id, proposal_id)
    if item is None:
        raise SkillError("unknown proposal", 404)
    if item["status"] != "pending":
        raise SkillError("proposal is not pending", 400)
    creating = _creating_agent(item.get("run_id") or "")
    if creating.get("auto") or creating.get("scheduled"):
        raise SkillError("cannot confirm a proposal from an automated turn", 400)
    # Claim via CAS: the guarded UPDATE is the single authority, so a
    # concurrent confirm/reject cannot interleave with the install.
    with db.tx() as conn:
        conn.execute("BEGIN IMMEDIATE")
        claimed = conn.execute(
            "UPDATE skill_proposals SET status='resolving', resolved_at=? WHERE id=? AND user_id=? AND status='pending'",
            (db.now(), proposal_id, user_id),
        ).rowcount
    if not claimed:
        raise SkillError("proposal is not pending", 400)
    try:
        slug = normalize_slug(raw.get("slug") if raw.get("slug") is not None else item["slug"])
        description = raw.get("description") if isinstance(raw.get("description"), str) else item["description"]
        body = raw.get("body") if isinstance(raw.get("body"), str) else item["body"]
        hint = raw.get("argument_hint") if isinstance(raw.get("argument_hint"), str) else item.get("argument_hint") or ""
        enable = raw.get("enable") if isinstance(raw.get("enable"), bool) else bool(item.get("enable"))
        extra_files = item.get("files") or {}
        if isinstance(raw.get("files"), dict):
            extra_files = {str(k): v for k, v in raw["files"].items() if isinstance(v, str)}
        elif isinstance(raw.get("files"), list):
            keep = {str(n) for n in raw["files"] if isinstance(n, str)}
            extra_files = {k: v for k, v in extra_files.items() if k in keep or k == "SKILL.md"}
        slug, rebuilt = validate_draft(slug=slug, description=description, body=body, files=extra_files, argument_hint=hint)
        rebuilt.pop("_hint", None)
        existing = skills.get_row(user_id, slug)
        on_disk = skill_dir(user_id, slug).exists()
        replacing = raw.get("replace") is True
        if (existing or on_disk) and not replacing:
            origin = (existing or {}).get("origin") or "disk"
            _release_claim(user_id, proposal_id)
            raise SkillError(f"skill exists ({origin}); pass replace=true", 409)
        disk_files = _to_bytes(rebuilt)
        saved = skills.install_files(
            user_id,
            disk_files,
            origin="chat",
            slug=slug,
            replace=replacing,
            enabled=bool(enable),
        )
    except Exception:
        # Install failed: put the proposal back so it is not lost.
        _release_claim(user_id, proposal_id)
        raise
    now = db.now()
    db.execute(
        """UPDATE skill_proposals SET status='saved', slug=?, description=?, body=?, argument_hint=?,
               files=?, enable=?, resolved_at=? WHERE id=? AND user_id=? AND status='resolving'""",
        (
            slug, description.strip()[:DESC_MAX], body, hint,
            json.dumps(extra_files), 1 if enable else 0, now, proposal_id, user_id,
        ),
    )
    _patch_permission(
        user_id,
        item["thread_id"],
        proposal_id,
        "saved",
        {"slug": slug, "description": description.strip()[:DESC_MAX], "enable": bool(enable), "overwrite": False},
    )
    logger.info("confirmed skill proposal user=%s slug=%s", user_id, slug)
    return saved


def _release_claim(user_id: int, proposal_id: str) -> None:
    db.execute(
        "UPDATE skill_proposals SET status='pending', resolved_at=NULL WHERE id=? AND user_id=? AND status='resolving'",
        (proposal_id, user_id),
    )


def reject_proposal(user_id: int, proposal_id: str) -> dict:
    expire_stale()
    item = get_proposal(user_id, proposal_id)
    if item is None:
        raise SkillError("unknown proposal", 404)
    if item["status"] != "pending":
        raise SkillError("proposal is not pending", 400)
    with db.tx() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "UPDATE skill_proposals SET status='rejected', resolved_at=? WHERE id=? AND user_id=? AND status='pending'",
            (db.now(), proposal_id, user_id),
        )
    if not cur.rowcount:
        raise SkillError("proposal is not pending", 400)
    item["status"] = "rejected"
    _patch_permission(user_id, item["thread_id"], proposal_id, "rejected")
    return public_card(item)
