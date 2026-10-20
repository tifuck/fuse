"""Move inline images in stored transcripts to chat_images (one-off backfill).

New writes keep each large image once per thread (app/chat_images.py). Rows
written before that still repeat every screenshot inline, so an old
image-heavy thread rereads megabytes each time it is opened or continued.

Usage: python -m scripts.dehydrate_chat_images [--apply] [--pause SECONDS]

Run from the tree whose schema you mean (/root/fuse is fuse_live, /root/fuse-dev
is fuse_dev). Without --apply it only reports what would move. Safe while Fuse
runs: each row is read on its own and rewritten only if it is unchanged since
(MD5 compare), and active runs are left to their own next save. Uses one
connection, not a pool. Reclaiming the freed table space afterwards is a
separate OPTIMIZE TABLE.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time

from app import chat_history, chat_images, chat_tx
from app.config import settings
from app.mysql_compat import connect


def _md5(value) -> str:
    return hashlib.md5((value or "").encode("utf-8")).hexdigest()


def _load(raw):
    try:
        return json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _request(value, images):
    """A request with its message lists canonical, or None when unchanged."""
    if not isinstance(value, dict):
        return None
    out = value
    for key in ("messages", "display_seed"):
        if isinstance(out.get(key), list):
            swapped, found = chat_images.canonical(out[key])
            if found:
                images.update(found)
                out = {**out, key: swapped}
    return out if out is not value else None


def _runs(conn, apply, pause, totals):
    ids = [row["id"] for row in conn.execute(
        "SELECT id FROM chat_runs WHERE status NOT IN ('queued','running') ORDER BY thread_id, id").fetchall()]
    conn.commit()
    for ident in ids:
        row = conn.execute(
            "SELECT r.id, r.thread_id, r.user_id, r.request, r.provider_messages, t.id AS live_thread "
            "FROM chat_runs r LEFT JOIN chat_threads t ON t.id=r.thread_id "
            "WHERE r.id=? AND r.status NOT IN ('queued','running')", (ident,)).fetchone()
        if row is None or row["live_thread"] is None:
            conn.commit()
            continue
        images = {}
        provider = _load(row["provider_messages"])
        new_provider = None
        if isinstance(provider, list):
            swapped, found = chat_images.canonical(provider)
            if found:
                images.update(found)
                new_provider = chat_tx.dumps(swapped)
        new_request = _request(_load(row["request"]), images)
        if not images:
            conn.commit()
            continue
        before = len(row["provider_messages"] or "") + len(row["request"] or "")
        provider_json = new_provider if new_provider is not None else row["provider_messages"]
        request_json = chat_tx.dumps(new_request) if new_request is not None else row["request"]
        totals["runs"] += 1
        totals["images"] += len(images)
        totals["before"] += before
        totals["after"] += len(provider_json or "") + len(request_json or "")
        if apply:
            chat_images.save(conn, row["thread_id"], row["user_id"], images)
            cur = conn.execute(
                "UPDATE chat_runs SET provider_messages=?, request=? WHERE id=? AND request_digest=? "
                "AND MD5(COALESCE(provider_messages,''))=? AND status NOT IN ('queued','running')",
                (provider_json, request_json, ident, _md5(row["request"]), _md5(row["provider_messages"])))
            if not cur.rowcount:
                totals["skipped"] += 1
        conn.commit()
        if pause:
            time.sleep(pause)


def _threads(conn, apply, pause, totals):
    ids = [row["id"] for row in conn.execute("SELECT id FROM chat_threads ORDER BY id").fetchall()]
    conn.commit()
    for ident in ids:
        row = conn.execute("SELECT id, user_id, retained_history FROM chat_threads WHERE id=?", (ident,)).fetchone()
        memory = _load(row["retained_history"]) if row else None
        if not isinstance(memory, dict) or not isinstance(memory.get("messages"), list):
            conn.commit()
            continue
        swapped, images = chat_images.canonical(memory["messages"])
        if not images:
            conn.commit()
            continue
        raw = chat_history._json({**memory, "messages": swapped})
        totals["threads"] += 1
        totals["images"] += len(images)
        totals["before"] += len(row["retained_history"])
        totals["after"] += len(raw)
        if apply:
            chat_images.save(conn, ident, row["user_id"], images)
            cur = conn.execute("UPDATE chat_threads SET retained_history=? WHERE id=? AND MD5(retained_history)=?",
                               (raw, ident, _md5(row["retained_history"])))
            if not cur.rowcount:
                totals["skipped"] += 1
        conn.commit()
        if pause:
            time.sleep(pause)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="rewrite the rows (default: report only)")
    parser.add_argument("--pause", type=float, default=0.05, help="seconds between rows (default 0.05)")
    args = parser.parse_args()
    totals = {"runs": 0, "threads": 0, "images": 0, "before": 0, "after": 0, "skipped": 0}
    conn = connect(settings.database_url, max_connections=1)
    try:
        _runs(conn, args.apply, args.pause, totals)
        _threads(conn, args.apply, args.pause, totals)
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    verb = "moved" if args.apply else "would move"
    print(f"{verb} {totals['images']} inline image(s) from {totals['runs']} run(s) and {totals['threads']} thread(s): "
          f"{totals['before'] / 1048576:.1f} MB -> {totals['after'] / 1048576:.1f} MB of JSON"
          + (f"; {totals['skipped']} row(s) changed meanwhile and were left as they were" if totals["skipped"] else ""))


if __name__ == "__main__":
    main()
