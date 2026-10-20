"""Inline images in stored provider transcripts, kept once per thread.

A read_file screenshot is a base64 data URL of a few hundred KB, and every
transcript save, retained history and continuation request repeated all of
them: a session with sixty screenshots rewrote 17 MB rows several times per
tool round, and MariaDB and Fuse each kept that much per connection or worker
thread until the host ran out of memory. Stored JSON now carries
``fuse-image:<sha256>`` in each large image's place. The data URL lives once in
chat_images, and goes back inline only on its way to a provider
(api._invoke_chat) or the run image route, so the bytes a provider sees, and
its prompt cache, are unchanged.

Hashes and comparisons of provider rows use the canonical (reference) form, so
a row matches whichever form it was stored in.
"""
from __future__ import annotations

import hashlib
import logging
import threading
from collections import OrderedDict

from . import db

logger = logging.getLogger(__name__)

PREFIX = "fuse-image:"
# A reference saves nothing worth a lookup on a small image.
MIN_CHARS = 8 * 1024
MISSING = "[Image unavailable: Fuse no longer stores it.]"
# Recently rehydrated images, per user: a running turn re-sends the same
# screenshots every round and must not fetch them from MariaDB each time.
CACHE_BYTES = 64 * 1024 * 1024
# Rows per lookup: each is a whole image, so a batch is a few MB at most.
_FETCH_BATCH = 16

_cache: OrderedDict[tuple[int, str], str] = OrderedDict()
_cache_bytes = 0
_cache_lock = threading.Lock()


def _url(part):
    """The URL an image part carries, or None for any other part."""
    if not isinstance(part, dict) or part.get("type") not in ("image_url", "input_image"):
        return None
    image = part.get("image_url")
    url = image.get("url") if isinstance(image, dict) else image
    return url if isinstance(url, str) else None


def _with_url(part, url):
    image = part.get("image_url")
    if isinstance(image, dict):
        return {**part, "image_url": {**image, "url": url}}
    return {**part, "image_url": url}


def _map(messages, swap):
    """A copy of messages with each image part replaced by swap(part, url).

    swap returns a new part, or None to keep it. Rows without a replaced part
    are shared with the input, never mutated."""
    if not isinstance(messages, list):
        return messages
    out = []
    for msg in messages:
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            out.append(msg)
            continue
        parts = None
        for index, part in enumerate(content):
            url = _url(part)
            new = swap(part, url) if url is not None else None
            if new is None:
                if parts is not None:
                    parts.append(part)
                continue
            if parts is None:
                parts = list(content[:index])
            parts.append(new)
        out.append(msg if parts is None else {**msg, "content": parts})
    return out


def is_ref(url) -> bool:
    return (isinstance(url, str) and len(url) == len(PREFIX) + 64 and url.startswith(PREFIX)
            and all(c in "0123456789abcdef" for c in url[len(PREFIX):]))


def canonical(messages):
    """(messages with each large inline image as a reference, {sha: data URL})."""
    found: dict[str, str] = {}

    def swap(part, url):
        if len(url) < MIN_CHARS or not url.startswith("data:image/") or not url.isascii():
            return None
        sha = hashlib.sha256(url.encode("ascii")).hexdigest()
        found[sha] = url
        return _with_url(part, PREFIX + sha)

    return _map(messages, swap), found


def canonical_row(row):
    """One provider row in canonical form, for hashing and comparison."""
    return canonical([row])[0][0]


def canonical_content(content):
    """A message content value (string or parts) in canonical form."""
    return canonical([{"content": content}])[0][0]["content"]


def save(conn, thread_id, user_id, images) -> None:
    """Store {sha: data URL} for a thread; rows it already has are skipped."""
    if not images or thread_id is None:
        return
    shas = sorted(images)
    have = set()
    for start in range(0, len(shas), 200):
        batch = shas[start:start + 200]
        have.update(row["sha"] for row in conn.execute(
            "SELECT sha FROM chat_images WHERE thread_id=? AND sha IN (" + ",".join("?" * len(batch)) + ")",
            (thread_id, *batch),
        ).fetchall())
    ts = db.now()
    for sha in shas:
        if sha in have:
            continue
        # One statement per image keeps each packet small. IGNORE: a
        # concurrent writer may store the same image first, and a deleted
        # thread's late save is dropped rather than failing the turn.
        conn.execute(
            "INSERT OR IGNORE INTO chat_images(thread_id, sha, user_id, url, created_at) VALUES (?,?,?,?,?)",
            (thread_id, sha, user_id, images[sha], ts),
        )


def store(conn, thread_id, user_id, messages):
    """Canonical messages, with their images saved for the thread."""
    out, images = canonical(messages)
    save(conn, thread_id, user_id, images)
    return out


def store_request(conn, thread_id, user_id, request):
    """A run request with its messages and display seed in canonical form."""
    if not isinstance(request, dict):
        return request
    out = request
    for key in ("messages", "display_seed"):
        value = out.get(key)
        if isinstance(value, list):
            stored = store(conn, thread_id, user_id, value)
            if stored is not value:
                out = {**out, key: stored}
    return out


def _refs(messages) -> list[str]:
    """The sha of every reference in messages, in order."""
    shas = []
    for msg in messages if isinstance(messages, list) else ():
        content = msg.get("content") if isinstance(msg, dict) else None
        for part in content if isinstance(content, list) else ():
            url = _url(part)
            if is_ref(url):
                shas.append(url[len(PREFIX):])
    return shas


def has_refs(messages) -> bool:
    return bool(_refs(messages))


def _cache_put(user_id, sha, url) -> None:
    global _cache_bytes
    key = (user_id, sha)
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return
        _cache[key] = url
        _cache_bytes += len(url)
        while _cache_bytes > CACHE_BYTES and _cache:
            _, old = _cache.popitem(last=False)
            _cache_bytes -= len(old)


def lookup(user_id, shas) -> dict[str, str]:
    """{sha: data URL} for the user's stored images; unknown shas are absent."""
    found: dict[str, str] = {}
    missing = []
    with _cache_lock:
        for sha in dict.fromkeys(shas):
            url = _cache.get((user_id, sha))
            if url is None:
                missing.append(sha)
            else:
                _cache.move_to_end((user_id, sha))
                found[sha] = url
    for start in range(0, len(missing), _FETCH_BATCH):
        batch = missing[start:start + _FETCH_BATCH]
        marks = ",".join("?" * len(batch))
        # A forked thread holds copies of its source's images: fetch one each.
        rows = db.fetchall(
            "SELECT i.sha, i.url FROM chat_images i JOIN ("
            "SELECT sha, MIN(thread_id) AS thread_id FROM chat_images "
            f"WHERE user_id=? AND sha IN ({marks}) GROUP BY sha) f "
            "ON f.thread_id=i.thread_id AND f.sha=i.sha",
            (user_id, *batch),
        )
        for row in rows:
            found[row["sha"]] = row["url"]
            _cache_put(user_id, row["sha"], row["url"])
    return found


def rehydrate(user_id, messages):
    """Messages with every reference back inline, as originally sent.

    An image no longer stored becomes a short note, never a reference a
    provider would reject."""
    shas = _refs(messages)
    if not shas:
        return messages
    urls = lookup(user_id, shas)
    lost = [sha for sha in dict.fromkeys(shas) if sha not in urls]
    if lost:
        logger.warning("Chat images missing for user=%s: %s", user_id, ", ".join(sha[:12] for sha in lost))

    def fill(part, url):
        if not is_ref(url):
            return None
        data = urls.get(url[len(PREFIX):])
        return _with_url(part, data) if data is not None else {"type": "text", "text": MISSING}

    return _map(messages, fill)


def resolve(user_id, url):
    """A data URL for an image part's URL: itself when inline, the stored
    image for a reference, None when that image is gone."""
    if not is_ref(url):
        return url
    return lookup(user_id, [url[len(PREFIX):]]).get(url[len(PREFIX):])


def copy_thread(conn, source_thread_id, thread_id) -> None:
    """A fork's runs and history keep the source's references."""
    conn.execute(
        "INSERT OR IGNORE INTO chat_images(thread_id, sha, user_id, url, created_at) "
        "SELECT ?, sha, user_id, url, created_at FROM chat_images WHERE thread_id=?",
        (thread_id, source_thread_id),
    )
