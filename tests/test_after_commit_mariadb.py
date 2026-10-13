"""In-memory job state follows the database, never runs ahead of it."""
import asyncio
import contextlib
import os
from unittest.mock import patch

import pytest

from app import chat_tx, command_jobs, monitor
from tests.mariadb_fixture import isolated_database

needs_db = pytest.mark.skipif(os.environ.get("FUSE_CHAT_TEST_MODE") != "1", reason="requires disposable MariaDB fixture")


@needs_db
def test_after_commit_runs_only_once_committed():
    with isolated_database():
        ran = []
        with chat_tx.transaction():
            chat_tx.after_commit(lambda: ran.append("committed"))
            assert ran == []
        assert ran == ["committed"]
        with pytest.raises(RuntimeError, match="boom"):
            with chat_tx.transaction():
                chat_tx.after_commit(lambda: ran.append("rolled back"))
                raise RuntimeError("boom")
        assert ran == ["committed"]


@needs_db
def test_rolled_back_receipt_keeps_pending_monitor_events():
    with isolated_database():
        rec = {"id": "job1", "delivered_event_seq": 0,
               "pending_events": [{"seq": 1, "envelope": "a"}, {"seq": 2, "envelope": "b"}]}
        with patch.dict(command_jobs._live, {"job1": rec}):
            with pytest.raises(RuntimeError):
                with chat_tx.transaction() as conn:
                    command_jobs.mark_delivered([monitor.event_receipt_id("job1", 1)], conn=conn)
                    raise RuntimeError("turn rejected")
            assert [item["seq"] for item in rec["pending_events"]] == [1, 2]
            with chat_tx.transaction() as conn:
                command_jobs.mark_delivered([monitor.event_receipt_id("job1", 1)], conn=conn)
            assert [item["seq"] for item in rec["pending_events"]] == [2]
            assert rec["delivered_event_seq"] == 1


def test_terminal_write_is_retried_until_it_lands():
    results = iter([False, False, True])
    calls = []

    def persist(rec, **kwargs):
        calls.append(kwargs["status"])
        return next(results)

    with patch.object(command_jobs, "_persist_finish", side_effect=persist), \
            patch.object(command_jobs, "PERSIST_RETRY_DELAYS", (0, 0, 0)):
        asyncio.run(command_jobs._persist_terminal({"id": "j"}, status="completed", exit_code=0, error=""))
    assert calls == ["completed"] * 3


def test_unsaved_terminal_record_is_kept_and_saved_later():
    results = iter([False, False, False, True])

    def persist(rec, **kwargs):
        return next(results)

    async def run():
        rec = {"id": "j", "status": "completed"}
        with patch.object(command_jobs, "_persist_finish", side_effect=persist), \
                patch.object(command_jobs, "PERSIST_RETRY_DELAYS", (0, 0)):
            await command_jobs._persist_terminal(rec, status="completed", exit_code=0, error="")
            assert rec.get("unpersisted"), "an unsaved result must be kept in memory"
            command_jobs._live.update({f"done{i}": {"status": "completed"} for i in range(command_jobs.JOB_CAP)})
            command_jobs._live["j"] = rec
            command_jobs._prune()
            assert "j" in command_jobs._live
            await asyncio.gather(*command_jobs._persisting)
        assert not rec.get("unpersisted")
        command_jobs._live.clear()

    asyncio.run(run())



@needs_db
def test_concurrent_duplicate_command_is_a_conflict_not_a_500():
    from app.chats import ChatValidationError

    with isolated_database():
        from app import accounts

        user = accounts.create_user("dup-command@test.invalid", "password12", admin=False)
        uid = int(user["id"] if isinstance(user, dict) else user)
        with chat_tx.transaction() as conn:
            chat_tx.remember(conn, uid, "cmd-1", "fp", {"ok": True})
        # A second request that passed the (non-locking) ledger read earlier.
        with pytest.raises(ChatValidationError) as caught:
            with chat_tx.transaction() as conn:
                chat_tx.remember(conn, uid, "cmd-1", "fp", {"ok": True})
        assert caught.value.status_code == 409


def test_publication_retry_classifier_handles_argless_and_lock_wait_errors():
    from app import chat_pub

    calls = []

    @contextlib.contextmanager
    def failing_tx():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError()  # no args: used to raise IndexError in the classifier
        if len(calls) == 2:
            raise Exception(1205, "Lock wait timeout exceeded")
        yield None

    with patch.object(chat_pub.chat_tx, "transaction", failing_tx):
        with pytest.raises(RuntimeError):
            chat_pub._commit_publications([])
    calls.clear()
    calls.append(1)  # skip the argless failure
    with patch.object(chat_pub.chat_tx, "transaction", failing_tx), patch.object(chat_pub.time, "sleep"):
        assert chat_pub._commit_publications([]) == []
