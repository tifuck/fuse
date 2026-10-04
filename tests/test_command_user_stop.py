"""Dashboard cancellation notifies its owner, unlike an agent's own kill call."""
import asyncio
import time
from unittest.mock import patch

import pytest

from app import command_jobs, chat_store, chat_runs


@pytest.mark.parametrize("child", [False, True])
def test_user_stop_delivers_to_owner(child):
    rec = {"id": "job", "user_id": 1, "thread_id": 7, "run_id": "owner",
           "background": True, "status": "running", "started": time.monotonic(),
           "command": "test", "out": "", "notify": False}
    with patch.object(command_jobs, "_get", return_value=rec), \
         patch.object(command_jobs, "mark_notified", side_effect=lambda r: r.update(notify=True)), \
         patch.object(command_jobs, "kill", return_value=("", {})) as kill:
        assert command_jobs.kill_thread_job(1, 8, "job") is None
        assert "user_cancelled" not in rec
        command_jobs.kill_thread_job(1, 7, "job")
        kill.assert_called_once_with("job", 1)
    with patch.object(command_jobs, "_persist_finish"), \
         patch.object(command_jobs, "_mark_delivered") as ack, \
         patch.object(command_jobs, "_child_run_state", return_value="active" if child else None), \
         patch.object(chat_store, "queue_child_result", return_value=True) as queue, \
         patch.object(chat_runs, "schedule_wake") as wake:
        asyncio.run(command_jobs._finish(rec, status="cancelled", exit_code=None, error="termination unconfirmed"))
        assert queue.call_args.args[0] == "owner"
        assert "Stopped by the user" in queue.call_args.args[1]
        assert wake.call_count == (0 if child else 1)
        ack.assert_not_called()


def test_agent_kill_does_not_notify_again():
    with patch.object(chat_store, "queue_child_result") as queue:
        asyncio.run(command_jobs._deliver({"status": "cancelled", "run_id": "owner"}))
        queue.assert_not_called()
