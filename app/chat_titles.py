"""Thread title generation, modelled on Claude Code's session titles.

When the user sends a message to an untitled chat, the chat route notifies
this module and the chat is named from its first real prompt in parallel with
the turn; the sidebar shows a short preview of that prompt until the title
lands. A periodic sweep covers missed notifications and retries failures with
backoff. After a few failures it stops until the user sends another message,
and the preview stays as the title meanwhile.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from . import db, chat_tx, chat_views
from .chat_schema import TITLE_MAX, _ROLES, _text, parse_messages

logger = logging.getLogger(__name__)

SWEEP_BATCH = 3
SWEEP_BACKOFF = timedelta(minutes=5)
SWEEP_INTERVAL = 60
_inflight: set[int] = set()
_tasks: set[asyncio.Task] = set()
_regen_inflight: set[int] = set()
_regen_tasks: set[asyncio.Task] = set()
_due: set[int] = set()
_wake: asyncio.Event | None = None
_wake_loop: asyncio.AbstractEventLoop | None = None


def claim(user_id: int, thread_id: int, *, explicit=False) -> str:
    token = uuid4().hex
    until = (datetime.now(timezone.utc) + timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.tx() as conn:
        cur = conn.execute("""UPDATE chat_threads SET title_job_token=?,title_job_until=? WHERE id=? AND user_id=?
                             AND (title_job_until='' OR title_job_until<?)""" + ("" if explicit else " AND title_state=''"),
                           (token, until, thread_id, user_id, db.now()))
        return token if cur.rowcount else ""


def release(thread_id: int, token: str, *, failed=False):
    db.execute("""UPDATE chat_threads SET title_job_token='',title_job_until='',
                  title_attempts=title_attempts+?,title_attempted_at=? WHERE id=? AND title_job_token=?""",
               (1 if failed else 0, db.now() if failed else "", thread_id, token))


def notify(thread_id: int) -> None:
    """The user sent a message: name the chat now instead of waiting for the sweep."""
    _due.add(int(thread_id))
    if _wake is not None and _wake_loop is not None:
        _wake_loop.call_soon_threadsafe(_wake.set)


def _backoff_cutoff() -> str:
    moment = datetime.now(timezone.utc) - SWEEP_BACKOFF
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


async def _generate(user_id: int, thread_id: int, model: str, prompt: str, placeholder: str) -> None:
    from . import accounts, chat_runtime, chat_runs

    token = await asyncio.to_thread(claim, user_id, thread_id)
    if not token:
        _inflight.discard(thread_id)
        return
    failed = False
    try:
        owner = await asyncio.to_thread(accounts.get_user, user_id)
        if owner is None:
            failed = True
            return
        title = await chat_runtime.generate_thread_title(
            chat_runs.internal_request(user_id), dict(owner), model, prompt
        )
        if not title:
            failed = True
            return
        applied = await asyncio.to_thread(apply_generated_title, user_id, thread_id, title, placeholder,
                                          token=token)
        # A rename settles title_state, so a None here is a miss worth backing off.
        failed = applied is None
    except Exception:
        logger.exception("Title sweep failed for thread %s", thread_id)
        failed = True
    finally:
        await asyncio.to_thread(release, thread_id, token, failed=failed)
        _inflight.discard(thread_id)


async def sweep() -> None:
    """Queue title generation for due and pending threads; returns immediately."""

    due = set(_due)
    _due.difference_update(due)
    cutoff = _backoff_cutoff()
    candidates = [
        item
        for item in await asyncio.to_thread(pending_title_threads, max(SWEEP_BATCH, len(due)),
                                            attempted_before=cutoff, due=due)
        if item["id"] not in _inflight
    ]
    for item in candidates:
        _inflight.add(item["id"])
        task = asyncio.create_task(
            _generate(item["user_id"], item["id"], item["model"], item["prompt"], item["title"]),
            name=f"chat-title-sweep-{item['id']}",
        )
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)


async def loop() -> None:
    global _wake, _wake_loop
    _wake = asyncio.Event()
    _wake_loop = asyncio.get_running_loop()
    try:
        while True:
            _wake.clear()
            try:
                await sweep()
            except Exception:
                logger.exception("Title sweep cycle failed")
            try:
                await asyncio.wait_for(_wake.wait(), timeout=SWEEP_INTERVAL)
            except asyncio.TimeoutError:
                pass
    finally:
        _wake = None
        _wake_loop = None


async def drain() -> None:
    """Cancel sweep work during shutdown."""
    for task in list(_tasks) + list(_regen_tasks):
        task.cancel()
    await asyncio.gather(*_tasks, *_regen_tasks, return_exceptions=True)
    _tasks.clear()
    _regen_tasks.clear()
    _inflight.clear()
    _regen_inflight.clear()
    _due.clear()


async def regenerate(user_id: int, thread_id: int) -> bool:
    """Regenerate a thread title from the end of the conversation (explicit /title).

    Runs as a standalone upstream request built from the stored transcript;
    the thread's messages are never modified, so the conversation's upstream
    prompt cache stays intact. Returns True when a regeneration was attempted.
    """
    from . import accounts, chat_runtime, chat_runs

    if thread_id in _regen_inflight:
        return False
    _regen_inflight.add(thread_id)
    token = await asyncio.to_thread(claim, user_id, thread_id, explicit=True)
    if not token:
        _regen_inflight.discard(thread_id)
        return False
    try:
        row = await asyncio.to_thread(db.fetchone,
            "SELECT model, messages FROM chat_threads WHERE id=? AND user_id=?",
            (thread_id, user_id),
        )
        if row is None:
            return False
        try:
            source = title_tail_source(parse_messages(row["messages"]))
        except Exception:
            logger.exception("Title source build failed for thread %s", thread_id)
            source = ""
        if not source.strip():
            await asyncio.to_thread(title_regen_failed, user_id, thread_id)
            return True
        owner = await asyncio.to_thread(accounts.get_user, user_id)
        if owner is None:
            return False
        title = await chat_runtime.generate_thread_title(
            chat_runs.internal_request(user_id),
            dict(owner),
            row["model"] or "",
            source,
        )
        if not title:
            await asyncio.to_thread(title_regen_failed, user_id, thread_id)
            return True
        await asyncio.to_thread(apply_regenerated_title, user_id, thread_id, title, token=token)
        return True
    except Exception:
        logger.exception("Title regeneration failed for thread %s", thread_id)
        try:
            await asyncio.to_thread(title_regen_failed, user_id, thread_id)
        except Exception:
            logger.exception("Title regen failure event failed for thread %s", thread_id)
        return True
    finally:
        await asyncio.to_thread(release, thread_id, token)
        _regen_inflight.discard(thread_id)


def spawn_regen(user_id: int, thread_id: int) -> asyncio.Task:
    """Queue a background regeneration; the route returns immediately."""
    task = asyncio.create_task(regenerate(user_id, thread_id), name=f"chat-title-regen-{thread_id}")
    _regen_tasks.add(task)
    task.add_done_callback(_regen_tasks.discard)
    return task


TITLE_PROMPT_MAX = 800
# Claude Code (generateSessionTitle): the first real prompt, at least 10
# characters, capped at 2,000 characters as a 1,500-character head plus the tail.
TITLE_MIN_CHARS = 10
TITLE_INPUT_MAX = 2_000
TITLE_INPUT_HEAD = 1_500
# Claude Code's conversation-tail source for a re-title (/title): the last
# 1,000 characters of user and assistant text.
TITLE_TAIL_CHARS = 1_000
TITLE_REGEN_PROMPT_MAX = 6_000
TITLE_SOURCE_MAX = 3_000
PROVISIONAL_TITLE_WORDS = 6
PROVISIONAL_TITLE_CHARS = 48
# A failed title is retried by the sweep with backoff, then only when the
# user sends another message (Claude Code retries on the next prompt).
TITLE_SWEEP_ATTEMPTS = 3
_TITLE_THINK = re.compile(r"</?(?:think|thinking|thought|reasoning)>", re.I)
_TITLE_THINK_BLOCK = re.compile(
    r"<(?:think|thinking|thought|reasoning)>.*?</(?:think|thinking|thought|reasoning)>",
    re.I | re.S,
)
_TITLE_WRAP = re.compile(r"^[\s\"'`“”‘’*#\-–—]+|[\s\"'`“”‘’*#\-–—.,;:!?]+$")
_TITLE_PREFIX = re.compile(r"^(?:title|chat title|session title|topic)\s*:\s*", re.I)
_TITLE_COMMAND = re.compile(r"^/[\w:-]+(?:\s|$)")
_PLACEHOLDER_TITLES = frozenset({"", "new chat", "title", "untitled", "chat"})
# Control/bidi characters never belong in metadata text, and < > are framing
# characters: content carrying them could close a <session>/<episode> wrapper
# or smuggle markup into a stored title. Tag-shaped spans are dropped whole.
_SCRUB_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069<>]")
_HTML_TAG = re.compile(r"<[a-zA-Z!?/][^<>]*>")


def _scrub_text(text: str) -> str:
    return _SCRUB_CHARS.sub(" ", text or "")


def _cut_at_word(text: str, limit: int) -> str:
    """Cut at limit, keeping whole words when a boundary is available."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    boundary = cut.rfind(" ")
    return cut[:boundary] if boundary >= limit // 2 else cut


def _clean_title(title: str, *, strict: bool = False) -> str:
    title = re.sub(r"\s+", " ", _scrub_text(title).strip())
    if strict:
        _text(title, "title", TITLE_MAX)
        return title
    return _cut_at_word(title, TITLE_MAX)
def _message_plain_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        bits = []
        for part in content:
            if isinstance(part, str):
                bits.append(part)
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    bits.append(text)
        return " ".join(bits)
    return ""
def first_user_text(messages: list[dict], *, limit: int = TITLE_PROMPT_MAX) -> str:
    for msg in messages:
        if msg.get("role") != "user" or msg.get("auto"):
            continue
        text = re.sub(r"\s+", " ", _message_plain_text(msg.get("content")).strip())
        if text:
            return clip_metadata_text(text, limit)
        names = [str(a.get("name") or "Attachment") for a in msg.get("attachments") or [] if isinstance(a, dict)]
        if names:
            return clip_metadata_text("Attachments: " + ", ".join(names), limit)
    return ""
def clip_metadata_text(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", _scrub_text(text)).strip()
    if len(text) <= limit:
        return text
    marker = " […] "
    if limit <= len(marker):
        return text[:max(0, limit)]
    head = max(0, int((limit - len(marker)) * 0.65))
    tail = max(0, limit - len(marker) - head)
    return text[:head] + marker + (text[-tail:] if tail else "")
def parse_generated_title(raw: str) -> str:
    """The model's title, lightly cleaned. Claude Code only trims its JSON
    field; this also unwraps the prose, fences and labels other models add."""
    text = _TITLE_THINK_BLOCK.sub(" ", raw or "")
    if _TITLE_THINK.search(text):
        return ""
    text = text.replace("\r", "\n").strip()
    text = re.sub(r"^```\w*\s*|\s*```$", "", text).strip()
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            text = parsed.get("title", "") if isinstance(parsed, dict) else ""
        except (ValueError, TypeError):
            return ""
    if not isinstance(text, str):
        return ""
    lines = [candidate.strip() for candidate in text.splitlines() if candidate.strip()]
    if not lines:
        return ""
    line = _TITLE_PREFIX.sub("", lines[0])
    line = _HTML_TAG.sub(" ", line)
    line = re.sub(r"\*\*|__|`", "", line)
    line = _TITLE_WRAP.sub("", line).strip()
    lowered = line.lower()
    if not line or lowered in _PLACEHOLDER_TITLES or lowered.startswith((
        "i cannot", "i can't", "i am unable", "i'm unable", "sorry", "as an ai",
    )):
        return ""
    return _clean_title(line)
def _title_plain(text: str) -> str:
    text = re.sub(r"```.*?(?:```|$)", " ", text or "", flags=re.S)
    text = re.sub(r"https?://(?:www\.)?([^/\s]+)(?:/([\w.-]+/[\w.-]+)/(?:issues|pull)/(\d+))?\S*",
                  lambda m: f"{m.group(2)}#{m.group(3)}" if m.group(3) else m.group(1), text)
    text = re.sub(r"^\s*/\w[\w-]*", " ", text)
    text = re.sub(r"[`*_#>~\[\]]+(?=\s|$)|(?:^|(?<=\s))[`*_#>~\[\]]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()
def _first_user(messages: list[dict]) -> dict | None:
    return next((m for m in messages if m.get("role") == "user" and not m.get("auto")), None)
def provisional_title(messages: list[dict]) -> str:
    """Short, readable preview of the first request shown until a real title lands."""
    first = _first_user(messages)
    if first is None:
        return "New chat"
    text = _title_plain(_message_plain_text(first.get("content")))
    if not text:
        names = [str(a.get("name") or "") for a in first.get("attachments") or [] if isinstance(a, dict)]
        text = next((n for n in names if n.strip()), "")
        return _clean_title(text)[:PROVISIONAL_TITLE_CHARS] or "New chat"
    words = text.split()
    out = []
    for word in words[:PROVISIONAL_TITLE_WORDS]:
        if len(" ".join(out + [word])) > PROVISIONAL_TITLE_CHARS:
            break
        out.append(word)
    if not out:
        out = [words[0][:PROVISIONAL_TITLE_CHARS]]
    title = " ".join(out).rstrip(".,;:!?")
    if len(out) < len(words):
        title += "…"
    return title if not is_placeholder_title(title) else "New chat"
def _title_session(text: str) -> str:
    return (
        "<session>\n" + text + "\n</session>\n\n"
        "Write the title in the predominant language of the session \u2014 a stray word or code token in "
        "another language doesn't change it, and neither does the English of these instructions."
    )
def title_prompt_text(messages: list[dict]) -> str:
    """The prompt Claude Code titles a session from: the first user message
    that is not a slash command and has at least TITLE_MIN_CHARS characters."""
    for msg in messages:
        if msg.get("role") != "user" or msg.get("auto"):
            continue
        text = _scrub_text(_message_plain_text(msg.get("content"))).strip()
        if _TITLE_COMMAND.match(text) or len(text) < TITLE_MIN_CHARS:
            continue
        if len(text) > TITLE_INPUT_MAX:
            head = _cut_at_word(text, TITLE_INPUT_HEAD)
            # Size the marker from an upper bound first so the tail shares the
            # budget exactly and "omitted" counts only the characters dropped.
            probe = f"\n[\u2026 {len(text)} characters omitted \u2026]\n"
            tail = max(0, TITLE_INPUT_MAX - len(head) - len(probe))
            omitted = len(text) - len(head) - tail
            marker = f"\n[\u2026 {omitted} characters omitted \u2026]\n"
            text = head + marker + (text[-tail:] if tail else "")
        return text
    return ""
def title_source(messages: list[dict]) -> str:
    """Automatic title request body, or "" while no prompt qualifies yet."""
    text = title_prompt_text(messages)
    return _title_session(text) if text else ""
def title_tail_source(messages: list[dict], *, limit: int = TITLE_TAIL_CHARS) -> str:
    """Explicit /title body: the end of the conversation, as Claude Code
    re-titles a session from its last 1,000 characters of text."""
    turns = [
        (m.get("role"), _scrub_text(_message_plain_text(m.get("content"))).strip())
        for m in messages
        if m.get("role") in _ROLES and not m.get("auto")
    ]
    # Label the turns so the model can tell a request from an answer (and so a
    # role-shaped line in the text cannot impersonate one).
    labelled = [
        f"{'Request' if role == 'user' else 'Output'}: {part}"
        for role, part in turns
        if part
    ]
    text = "\n".join(labelled)
    if not text:
        return ""
    if len(text) > limit:
        tail = text[-limit:]
        # Unlike Claude Code, keep the latest request in view when a long
        # final answer would fill the whole tail.
        latest = next((line for line in reversed(labelled) if line.startswith("Request: ")), "")
        if latest and latest not in tail:
            head = _cut_at_word(latest, limit // 2)
            marker = "\n[\u2026]\n"
            tail = head + marker + text[-(limit - len(head) - len(marker)):]
        text = tail
    return _title_session(text)
def parse_generated_recap(raw: str) -> str:
    text = _TITLE_THINK_BLOCK.sub(" ", raw or "")
    if _TITLE_THINK.search(text):
        return ""
    # Tag-shaped spans are markup; stray < > are kept because comparisons like
    # "n < 3" are content and the recap renders as plain text.
    text = _HTML_TAG.sub(" ", text)
    text = re.sub(r"`|\*\*|__", "", text)
    text = re.sub(r"^\s*(?:[-•#>]+|\d+\.)\s*", "", re.sub(r"\s+", " ", text).strip())
    text = re.sub(r"^(?:recap|summary)\s*:\s*", "", text, flags=re.I)
    lowered = text.lower()
    if not text or len(text) > 400 or len(text.split()) > 45 or lowered.startswith((
        "i cannot", "i can't", "i am unable", "i'm unable", "sorry", "as an ai",
    )):
        return ""
    return text
def is_placeholder_title(title: str | None, *, project_name: str = "") -> bool:
    text = (title or "").strip()
    if not text or text.lower() in _PLACEHOLDER_TITLES:
        return True
    needle = (project_name or "").strip()
    return bool(needle) and text.lower() == needle.lower()
def title_needs_generation(
    title: str | None, *, project_name: str = "", first_user_text: str = "", title_state: str = "",
    provisional: str = "",
) -> bool:
    """True while no real title exists: a placeholder, the provisional preview,
    or a legacy row whose title is just the raw first user message."""
    if title_state in ("generated", "user", "fallback"):
        return False
    if is_placeholder_title(title, project_name=project_name):
        return True
    text = (title or "").strip()
    seed = (first_user_text or "").strip()
    return (bool(seed) and text == _clean_title(seed)) or (bool(provisional) and text == provisional)
def title_from_messages(messages: list[dict]) -> str:
    for msg in messages:
        if msg.get("role") != "user" or msg.get("auto"):
            continue
        if isinstance(msg.get("content"), str) and msg["content"].strip():
            return _clean_title(msg["content"]) or "New chat"
        attachments = msg.get("attachments") or []
        if attachments:
            name = attachments[0].get("name") if isinstance(attachments[0], dict) else ""
            return _clean_title(str(name or "Attachment")) or "New chat"
    return "New chat"

# --- title application, moved from app/chat_store.py (chat-store-extract.py) ---


def apply_generated_title(user_id, thread_id, title, placeholder="", *, token=None):
    """Replace an auto title without changing conversation activity or a user rename."""
    from . import chats

    new_title = parse_generated_title(title) if title else ""
    if not new_title:
        return None
    allowed = {"", "New chat"}
    if placeholder:
        allowed.add(placeholder)
    with chat_tx.transaction() as conn:
        pk = chats.resolve_thread_pk(user_id, thread_id, conn)
        if pk is None:
            return None
        thread_id = pk
        row = conn.execute(
            """SELECT t.id, t.title, t.title_state, t.title_job_token, t.kind, t.project_id, t.messages, p.name AS project_name
               FROM chat_threads t LEFT JOIN user_projects p ON p.id=t.project_id
               WHERE t.id=? AND t.user_id=?""",
            (thread_id, user_id),
        ).fetchone()
        if row is None:
            return None
        if token is not None and row["title_job_token"] != token:
            return None
        if row["title_state"] in ("user", "generated", "fallback"):
            return None
        project_name = ""
        if (row["kind"] if "kind" in row.keys() else "") == "project":
            try:
                project_name = row["project_name"] or ""
            except (KeyError, IndexError, TypeError):
                project_name = ""
        current = row["title"] or ""
        try:
            allowed.add(provisional_title(chats.parse_messages(row["messages"])))
        except Exception:
            pass
        if current not in allowed and not is_placeholder_title(current, project_name=project_name):
            return None
        cur = conn.execute(
            "UPDATE chat_threads SET title=?, title_state=?, title_attempts=0, title_attempted_at='', title_job_token='',title_job_until='', revision=revision+1 WHERE id=? AND user_id=? AND title_state=''"
            + (" AND title_job_token=?" if token is not None else ""),
            (new_title, "generated", thread_id, user_id) + ((token,) if token is not None else ()),
        )
        if not cur.rowcount:
            return None
        chat_tx.thread_event(conn, user_id, thread_id)
        return chat_views.thread_view(conn, user_id, thread_id)


def apply_regenerated_title(user_id, thread_id, title, *, token=None):
    """Force-replace the title from an explicit /title request.

    Unlike apply_generated_title this may overwrite a user rename, since the
    user explicitly asked for a new title. Messages are never touched, so the
    conversation's upstream prompt cache and last-activity timestamp stay intact.
    """
    from . import chats

    new_title = parse_generated_title(title) if title else ""
    if not new_title:
        return None
    with chat_tx.transaction() as conn:
        pk = chats.resolve_thread_pk(user_id, thread_id, conn)
        if pk is None:
            return None
        row = conn.execute(
            "SELECT id, title, title_state, title_job_token FROM chat_threads WHERE id=? AND user_id=?",
            (pk, user_id),
        ).fetchone()
        if row is None:
            return None
        if token is not None and row["title_job_token"] != token:
            return None
        cur = conn.execute(
            "UPDATE chat_threads SET title=?, title_state='generated', title_attempts=0, title_attempted_at='',title_job_token='',title_job_until='', revision=revision+1 WHERE id=? AND user_id=?"
            + (" AND title_job_token=?" if token is not None else ""),
            (new_title, pk, user_id) + ((token,) if token is not None else ()),
        )
        if not cur.rowcount:
            return None
        chat_tx.thread_event(conn, user_id, pk)
        return chat_views.thread_view(conn, user_id, pk)


def title_regen_failed(user_id, thread_id):
    """Tell the client an explicit /title request produced nothing."""
    from . import chats

    with chat_tx.transaction() as conn:
        pk = chats.resolve_thread_pk(user_id, thread_id, conn)
        if pk is None:
            return
        chat_tx.event(conn, user_id, "title.failed", pk, {"reason": "empty"})


def pending_title_threads(limit=3, *, attempted_before="", due=()):
    """Threads still waiting for a generated title.

    Like Claude Code, a chat is named from its first prompt as soon as that
    prompt is sent, in parallel with the turn. ``due`` threads (the user just
    sent a message) skip the retry backoff and the attempt cap, so a failed
    title is retried on the next message, as Claude Code does.
    """
    from . import chats

    due = [int(i) for i in due][:50]
    due_sql = (" OR t.id IN (" + ",".join("?" for _ in due) + ")") if due else ""
    rows = db.fetchall(
        f"""SELECT t.*, p.name AS project_name FROM chat_threads t
           LEFT JOIN user_projects p ON p.id=t.project_id
           WHERE t.title_state=''
             AND ((t.title_attempted_at='' OR t.title_attempted_at < ?) AND t.title_attempts < ?{due_sql})
             AND (t.title_job_until='' OR t.title_job_until < ?)
           ORDER BY {"(t.id IN (" + ",".join("?" for _ in due) + ")) DESC, " if due else ""}t.title_attempted_at, t.updated_at LIMIT ?""",
        (attempted_before, TITLE_SWEEP_ATTEMPTS, *due, db.now(), *due, max(int(limit) * 8, int(limit))),
    )
    out = []
    for row in rows:
        keys = row.keys()
        project_name = (row["project_name"] or "") if "project_name" in keys else ""
        try:
            messages = chats.parse_messages(row["messages"])
        except Exception:
            continue
        source = title_source(messages)
        if source and title_needs_generation(
            row["title"], project_name=project_name,
            first_user_text=title_from_messages(messages),
            title_state=row["title_state"] if "title_state" in row.keys() else "",
            provisional=provisional_title(messages),
        ):
            out.append({
                "id": int(row["id"]),
                "user_id": int(row["user_id"]),
                "model": row["model"] or "",
                "title": row["title"] or "",
                "prompt": source,
                "attempts": int(row["title_attempts"]),
            })
            if len(out) >= int(limit):
                break
        else:
            # No prompt qualifies yet (empty, too short, a slash command or
            # attachments only); the next message makes the thread due again.
            db.execute("UPDATE chat_threads SET title_attempted_at=? WHERE id=?", (db.now(), row["id"]))
    return out


def mark_title_attempted(thread_id):
    """Record a failed title attempt so retries back off instead of hammering."""
    db.execute(
        "UPDATE chat_threads SET title_attempts=title_attempts+1, title_attempted_at=? WHERE id=?",
        (db.now(), thread_id),
    )
