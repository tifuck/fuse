"""MariaDB lock conflicts: which errors may be retried, and the paths that retry
or avoid them (spawn admission, the jobs poll)."""
import contextlib
from unittest.mock import patch

import pymysql
import pytest

from app import chat_children, chat_views
from app.database_errors import IntegrityError, TransactionAborted, is_lock_conflict


def _aborted_by(cause: BaseException) -> TransactionAborted:
    try:
        raise TransactionAborted("transaction aborted by an earlier database error") from cause
    except TransactionAborted as exc:
        return exc


def test_lock_conflicts_are_deadlocks_and_lock_wait_timeouts():
    assert is_lock_conflict(pymysql.err.OperationalError(1213, "Deadlock found when trying to get lock"))
    assert is_lock_conflict(pymysql.err.OperationalError(1205, "Lock wait timeout exceeded"))
    # A deadlock swallowed by one statement surfaces on the next as aborted.
    assert is_lock_conflict(_aborted_by(pymysql.err.OperationalError(1213, "Deadlock found")))


def test_other_failures_are_not_retryable():
    assert not is_lock_conflict(None)
    assert not is_lock_conflict(RuntimeError())  # no args
    assert not is_lock_conflict(pymysql.err.OperationalError(2013, "Lost connection to MySQL server"))
    assert not is_lock_conflict(IntegrityError("Duplicate entry", errno=1062))
    assert not is_lock_conflict(_aborted_by(pymysql.err.OperationalError(2006, "MySQL server has gone away")))
    assert not is_lock_conflict(TransactionAborted("aborted"))


def test_spawn_admission_retries_a_deadlock_victim():
    calls = []

    def admit(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise pymysql.err.OperationalError(1213, "Deadlock found when trying to get lock")
        return {"id": "child-1"}

    with patch.object(chat_children, "_start_child_run", side_effect=admit), \
            patch.object(chat_children.time, "sleep") as slept:
        assert chat_children.start_child_run(user_id=1, thread_id=2) == {"id": "child-1"}
    assert len(calls) == 2
    assert calls[0] == calls[1] == {"user_id": 1, "thread_id": 2}
    slept.assert_called_once()


def test_spawn_admission_gives_up_after_its_attempts():
    deadlock = pymysql.err.OperationalError(1213, "Deadlock found when trying to get lock")
    with patch.object(chat_children, "_start_child_run", side_effect=deadlock) as admit, \
            patch.object(chat_children.time, "sleep"):
        with pytest.raises(pymysql.err.OperationalError):
            chat_children.start_child_run(user_id=1, thread_id=2)
    assert admit.call_count == chat_children.ADMISSION_ATTEMPTS


def test_spawn_admission_never_replays_other_failures():
    # A lost connection may have committed: replaying it could admit twice.
    lost = pymysql.err.OperationalError(2013, "Lost connection to MySQL server during query")
    cap = chat_children.SpawnCapError("max depth reached.")
    for error in (lost, cap):
        with patch.object(chat_children, "_start_child_run", side_effect=error) as admit, \
                patch.object(chat_children.time, "sleep"):
            with pytest.raises(type(error)):
                chat_children.start_child_run(user_id=1, thread_id=2)
        assert admit.call_count == 1


def test_jobs_poll_reads_without_locking_runs():
    # BEGIN IMMEDIATE turns the live-run SELECT into FOR UPDATE, whose gap
    # locks deadlocked spawn admission and new turns (fuse_live, 2026-10-14).
    modes = []

    class Empty:
        def execute(self, *_args):
            return self

        def fetchone(self):
            return None

    @contextlib.contextmanager
    def transaction(*, immediate=True):
        modes.append(immediate)
        yield Empty()

    with patch.object(chat_views.chat_tx, "transaction", transaction):
        assert chat_views.historical_command_jobs(1, 2) == []
    assert modes == [False]
