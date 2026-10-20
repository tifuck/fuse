"""Per-round write and read volume of a live run.

Incident (October 2026): a 105-tool-call turn on a thread with 1.2 MB of
retained history sent MariaDB ~28 MB per tool call. Every heartbeat, counter
or queue update rewrote the run's whole request, which still carried the
replayed history, and the runtime re-saved an unchanged transcript several
times per round; the inbox read both blobs at every tool boundary. These pin
the fixes against a disposable MariaDB schema.
"""
import json
import re
from unittest.mock import patch

import pytest

from app import chat_runtime, chat_store as store, chats, db, mysql_compat
from tests.mariadb_fixture import isolated_database
from tests.test_read_file_images_runtime import job

pytestmark = pytest.mark.skipif(__import__("os").environ.get("FUSE_CHAT_TEST_MODE") != "1",
                                reason="requires disposable MariaDB fixture")


@pytest.fixture
def thread():
    with isolated_database():
        store.invalidate_committed_cursors()
        db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'volume@test.invalid','unused',?)", (db.now(),))
        yield chats.create_thread(1, model="glm-offline")
        store.invalidate_committed_cursors()


def start(thread, text="inspect"):
    view = chats.get_thread(1, thread["id"])
    return store.start_turn(1, thread["id"], text, view["revision"], f"cmd-{thread['id']}-{view['revision']}")["run"]


def stored_request(run):
    return json.loads(db.fetchone("SELECT request FROM chat_runs WHERE id=?", (run["id"],))["request"])


class Statements:
    """Rewritten SQL the app sends, for asserting which columns move."""

    def __init__(self):
        self.sql = []

    def __enter__(self):
        real = mysql_compat.Conn.native

        def native(conn, sql, args=()):
            self.sql.append(sql)
            return real(conn, sql, args)

        self._patch = patch.object(mysql_compat.Conn, "native", native)
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()

    def run_updates(self):
        return [s for s in self.sql if s.startswith("UPDATE `chat_runs`")]

    def reads_transcript(self):
        # Selecting the column itself; MD5/LOCATE over it stay in MariaDB.
        columns = (re.sub(r"(?:MD5|LOCATE)\(.*?`provider_messages`\)", "", s.split(" FROM ")[0])
                   for s in self.sql if s.startswith("SELECT"))
        return [c for c in columns if "`provider_messages`" in c]


def test_progress_saves_drop_the_history_copy_and_skip_repeats(thread):
    run = start(thread)
    seed = run["request"]["messages"]
    assert seed and "messages" in stored_request(run)
    rows = [*seed, *chat_runtime._tool_messages([job()])]

    with Statements() as seen:
        store.save_turn_progress(run["id"], rows)
    request = stored_request(run)
    # provider_messages now carries the leg; the request keeps no second copy.
    assert "messages" not in request
    assert request["model"] == "glm-offline" and isinstance(request["agent"], dict)
    saved = db.fetchone("SELECT provider_messages FROM chat_runs WHERE id=?", (run["id"],))["provider_messages"]
    assert json.loads(saved) and len(json.loads(saved)) == len(rows)
    assert any("`provider_messages` = %s" in s and "`request` = %s" in s for s in seen.run_updates())

    with Statements() as seen:
        store.save_turn_progress(run["id"], rows)
    # Same transcript and request: only the liveness stamp is written.
    assert [re.sub(r"`updated_at` = %s", "", s).count("%s") for s in seen.run_updates()] == [1]
    assert not seen.reads_transcript()

    with Statements() as seen:
        store.save_turn_progress(run["id"], [*rows, {"role": "assistant", "content": "done"}])
    assert any("`provider_messages` = %s" in s and "`request` = %s" not in s for s in seen.run_updates())


def test_an_empty_transcript_keeps_the_request_history(thread):
    run = start(thread)
    store.save_turn_progress(run["id"], [])
    # Readers fall back to request.messages while provider_messages is empty.
    assert stored_request(run)["messages"] == run["request"]["messages"]


def test_recovery_and_steer_continuations_rebuild_the_history_from_the_transcript(thread):
    run = start(thread)
    rows = [*run["request"]["messages"], *chat_runtime._tool_messages([job()])]
    store.save_turn_progress(run["id"], rows)
    db.execute("UPDATE chat_runs SET status='running', updated_at=? WHERE id=?", (db.now(), run["id"]))
    store.recover_runs()
    resumed = db.fetchone("SELECT status, request, provider_messages FROM chat_runs WHERE id=?", (run["id"],))
    assert resumed["status"] == "queued"
    request = json.loads(resumed["request"])
    # The worker starts from request.messages: the sealed transcript is back.
    assert request["messages"] == json.loads(resumed["provider_messages"])
    assert [m["role"] for m in request["messages"][:len(rows)]] == [m["role"] for m in rows]

    other = chats.create_thread(1, model="glm-offline")  # the first run is queued again
    turn = start(other, "second")
    rows = [*turn["request"]["messages"], *chat_runtime._tool_messages([job()])]
    store.save_turn_progress(turn["id"], rows)
    assert store.queue_steer(turn["id"], "s1", "also check the footer")
    store.finish_run(turn["id"], {"content": "", "reasoning": ""}, "completed")
    continued = db.fetchone("SELECT status, request FROM chat_runs WHERE id=?", (turn["id"],))
    assert continued["status"] == "queued"
    assert json.loads(continued["request"])["messages"][:len(rows)] == store.seal_provider_messages(rows)


def test_inbox_peeks_leave_the_transcript_alone_until_a_queue_has_entries(thread):
    run = start(thread)
    rows = [*run["request"]["messages"], *chat_runtime._tool_messages([job()])]
    store.save_turn_progress(run["id"], rows)
    with Statements() as seen:
        assert store.take_steers(run["id"]) == []
        assert store.take_followups(run["id"]) == []
        assert store.take_child_results(run["id"]) == []
        assert store.steer_display_deliveries(run["id"]) == []
        assert store.followup_display_deliveries(run["id"]) == []
    assert seen.sql and not seen.reads_transcript()

    assert store.queue_steer(run["id"], "s2", "stop after this file")
    assert [item["id"] for item in store.take_steers(run["id"])] == ["s2"]
    steer = store.steer_message({"id": "s2", "text": "stop after this file"})
    store.save_turn_progress(run["id"], [*rows, steer])
    # Delivery is read from the durable transcript and acknowledged with it.
    assert store.take_steers(run["id"]) == []
