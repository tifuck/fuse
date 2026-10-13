"""A durable-state stamp set after a snapshot began must not clamp it."""
from unittest.mock import patch

from app import chat_checkpoint, chat_tx, chat_views


def test_stamps_are_taken_before_the_snapshot_reads():
    # Writer committed row v1 and stamped 5; the snapshot starts; a newer row
    # v2 commits and stamps 9 while the snapshot (which still sees v1) runs.
    chat_checkpoint._state_cursors["run-1"] = 5
    try:
        stamps = chat_checkpoint.state_cursor_snapshot()
        chat_checkpoint._state_cursors["run-1"] = 9
        assert chat_views._durable_cursor("run-1", stamps) == 5
        # Without a pre-read set (other callers), behavior is unchanged.
        assert chat_views._durable_cursor("run-1", None) == 9
    finally:
        chat_checkpoint._state_cursors.pop("run-1", None)


def test_a_run_first_stamped_after_the_snapshot_began_clamps_to_the_pre_read_cursor():
    # No stamp yet when the snapshot copies them (user cursor 4); the run's
    # first rows then commit and stamp 5 and 9 while the snapshot may still
    # see the row at 5. The live 9 would skip 6-8; the pre-read 4 replays them.
    with patch.object(chat_tx, "committed_cursor", return_value=4):
        stamps = chat_checkpoint.state_cursor_snapshot(1)
    chat_checkpoint._state_cursors["run-new"] = 9
    try:
        assert chat_views._durable_cursor("run-new", stamps) == 4
    finally:
        chat_checkpoint._state_cursors.pop("run-new", None)


def test_get_run_snapshot_reads_stamps_before_its_transaction():
    calls = []
    real_snapshot = chat_checkpoint.state_cursor_snapshot

    def tracked(*_args):
        calls.append("stamps")
        return real_snapshot()

    class Conn:
        def execute(self, sql, args=()):
            calls.append("sql")
            class Cur:
                def fetchone(self):
                    return None
            return Cur()

    class Tx:
        def __enter__(self):
            return Conn()

        def __exit__(self, *exc):
            return False

    with patch.object(chat_checkpoint, "state_cursor_snapshot", tracked), patch.object(chat_views.db, "tx", lambda: Tx()):
        assert chat_views.get_run_snapshot(1, "missing") is None
    assert calls[0] == "stamps"
