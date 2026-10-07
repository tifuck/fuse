"""Concurrent-subagent load harness for the chat event pipeline.

Spawns N child runs that all stream through the real worker machinery
(Output.apply, flush, checkpoint, group-commit publication) against a scratch
MariaDB schema, with the provider replaced by a synthetic SSE generator. No
pooled credentials are touched.

Usage: ./venv/bin/python -m scripts.loadtest_subagents [--children 200] [--chunks 60] [--delay-ms 4]
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import statistics
import sys
import time
from unittest.mock import patch

from fastapi.responses import StreamingResponse


@contextlib.contextmanager
def scratch_database():
    from app import db

    from tests.mariadb_fixture import isolated_database
    with isolated_database(pool_size=4):
        yield


async def run_load(children: int, chunks: int, delay_ms: float, per_tree_opt: int = 200, chunk_bytes: int = 100) -> int:
    from app import chat_runs, chat_store as store, chats, db

    with scratch_database():
        # One parent thread; N background children, the fan-out shape that
        # used to serialize every flush on the per-user cursor row.
        uid = int(time.time_ns() % 1_000_000_000)
        db.execute(
            "INSERT INTO users(id,email,password_hash,created_at) VALUES(?,?,?,?)",
            (uid, f"{uid}@test.invalid", "unused", db.now()),
        )
        thread = chats.create_thread(uid, model="offline")
        parent = store.start_turn(uid, thread["id"], "Fan out", 0, "lt-" + thread["id"], auto_title=False)["run"]

        latencies: list[float] = []

        real_checkpoint = store.checkpoint

        import inspect

        supported = set(inspect.signature(real_checkpoint).parameters)

        def timed_checkpoint(run_id, message, events=None, **kwargs):
            # Version-tolerant: the pre-optimization checkpoint accepted no
            # durable/future kwargs, which is exactly what the baseline
            # comparison run exercises.
            kwargs = {k: v for k, v in kwargs.items() if k in supported}
            started = time.monotonic()
            result = real_checkpoint(run_id, message, events=events, **kwargs)
            latencies.append(time.monotonic() - started)
            return result
        async def provider(request, user, run_request, **kwargs):
            async def chunks_gen():
                for index in range(chunks):
                    yield (
                        'data: {"choices":[{"delta":{"content":"%s"}}]}\n\n'
                        % (f"chunk {index} " + "x" * max(0, chunk_bytes - 10))
                    ).encode()
                    if index == chunks // 2:
                        yield (
                            'data: {"fuse":{"event":"tool","id":"t1","name":"read_file","status":"running"}}\n\n'
                        ).encode()
                        yield (
                            'data: {"fuse":{"event":"tool","id":"t1","name":"read_file","status":"done","output":"ok"}}\n\n'
                        ).encode()
                    if delay_ms:
                        await asyncio.sleep(delay_ms / 1000.0)
                yield b"data: [DONE]\n\n"

            return StreamingResponse(chunks_gen())

        if hasattr(store, "start_publisher"):
            store.start_publisher()
        chat_runs._stopping = False
        kids = []
        started = time.monotonic()
        # LIVE_TREE_CHILDREN caps live children per root; fan out across as
        # many parent threads as the requested concurrency needs.
        per_tree = per_tree_opt  # LIVE_TREE_CHILDREN; start_turn caps a user at 4 active roots
        parents = [parent]
        for t in range(1, (max(children, 1) + per_tree - 1) // per_tree):
            trow = chats.create_thread(uid, model="offline")
            parents.append(store.start_turn(uid, trow["id"], "Fan out", 0, f"lt-{t}-{trow['id']}", auto_title=False)["run"])
        try:
            with patch.object(store, "checkpoint", side_effect=timed_checkpoint), patch(
                "app.chat_runtime.stream_dashboard_chat", side_effect=provider
            ):
                for i in range(children):
                    p = parents[i % len(parents)]
                    child = store.start_child_run(
                        user_id=uid,
                        thread_id=p["thread_id"],
                        parent_run_id=p["id"],
                        prompt=f"work {i}",
                        description=f"worker {i}",
                        background=True,
                    )
                    task = await chat_runs.start(
                        chat_runs.internal_request(uid), {"id": uid}, child
                    )
                    kids.append(task)
                await asyncio.wait_for(asyncio.gather(*kids), timeout=300)
        finally:
            if hasattr(store, "stop_publisher"):
                await store.stop_publisher()

        elapsed = time.monotonic() - started
        events = store.events_after(uid, 0, limit=256)
        total_events = 0
        cursor = 0
        while events:
            total_events += len(events)
            cursor = events[-1]["id"]
            events = store.events_after(uid, cursor, limit=256)
        terminal = db.fetchall(
            "SELECT status, COUNT(*) AS n FROM chat_runs WHERE user_id=? AND parent_run_id IS NOT NULL GROUP BY status",
            (uid,),
        )
        bad = [row for row in terminal if row["status"] not in ("completed",)]
        lat = sorted(latencies)
        p50 = lat[int(len(lat) * 0.50)] if lat else 0.0
        p95 = lat[int(len(lat) * 0.95)] if lat else 0.0
        print(f"children={children} chunks={children and chunks} elapsed={elapsed:.2f}s")
        print(f"checkpoint calls={len(lat)} p50={p50*1000:.1f}ms p95={p95*1000:.1f}ms max={lat[-1]*1000:.1f}ms")
        print(f"events published={total_events} rate={total_events/elapsed:.0f}/s statuses={[dict(r) for r in terminal]}")
        if bad:
            print(f"FAIL: non-completed children: {[dict(r) for r in bad]}")
            return 1
        if total_events < children:
            print("FAIL: expected at least one event per child")
            return 1
        if p95 > 0.5:
            print(f"FAIL: p95 checkpoint latency {p95*1000:.0f}ms exceeds 500ms budget")
            return 1
        if elapsed > 60.0:
            print(f"FAIL: total elapsed {elapsed:.1f}s exceeds 60s budget")
            return 1
        print("OK")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--children", type=int, default=200)
    parser.add_argument("--chunks", type=int, default=60)
    parser.add_argument("--delay-ms", type=float, default=4.0)
    parser.add_argument("--per-tree", type=int, default=200, dest="per_tree")
    parser.add_argument("--chunk-bytes", type=int, default=100)
    args = parser.parse_args()
    return asyncio.run(run_load(args.children, args.chunks, args.delay_ms, args.per_tree, args.chunk_bytes))


if __name__ == "__main__":
    raise SystemExit(main())
