"""Permission and persistence regressions; no live database or providers."""
import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app import artifacts, permissions, security, vault
from app.sources import token_store


@pytest.mark.parametrize("command", [
    "echo $HOME && cat /etc/shadow", "cat .env",
    "cat ./a ./b ./c ./d ./e ./f /etc/shadow", "find . -delete",
    "python3 script.py", "cat .aws/credentials", "cat .*",
])
def test_shell_requires_approval(command):
    gate = SimpleNamespace(thread_id=1, user_id=1, unattended=True, requester={})
    with patch.object(permissions, "bypass_enabled", return_value=False), patch.object(permissions, "grant_covers", return_value=False), patch.object(permissions, "auto_enabled", return_value=False):
        result = asyncio.run(permissions.review(
            tool="run_terminal_command", args={"command": command}, user_id=1,
            host_id=1, host_alias="test", root="/srv/app", windows=False, gate=gate,
        ))
    assert not result.allowed


def test_secret_cannot_auto_approve():
    gate = SimpleNamespace(thread_id=1, user_id=1, unattended=False, requester={})
    ask = AsyncMock(return_value="deny")
    with patch.object(permissions, "bypass_enabled", return_value=False), patch.object(permissions, "grant_covers", return_value=False), patch.object(permissions, "auto_enabled", return_value=True), patch.object(permissions, "_ask_and_wait", ask):
        result = asyncio.run(permissions.review(
            tool="read_file", args={"path": "/etc/shadow"}, user_id=1,
            host_id=1, host_alias="test", root="/srv/app", windows=False, gate=gate,
        ))
    assert not result.allowed
    assert ask.call_args.args[1]["secret"]


def test_cleanup_preserves_hourly_bucket():
    limiter = security.RateLimiter()
    with patch.object(security.time, "monotonic", return_value=0):
        assert limiter.allow("hourly", 1, 3600)
    with patch.object(security.time, "monotonic", return_value=120):
        for i in range(8192):
            limiter.allow(str(i), 1, 60)
        assert not limiter.allow("hourly", 1, 3600)
    with patch.object(security.time, "monotonic", return_value=3601):
        assert limiter.allow("hourly", 1, 3600)


def test_vault_observation_does_not_extend_idle():
    record = {"dek": b"x" * 32, "last_seen": 1, "pinned_runs": set()}
    with patch.object(vault, "_vault", {1: record}), patch.object(vault.time, "monotonic", return_value=10):
        assert vault.unlocked(1)
        assert record["last_seen"] == 1
        with patch.object(vault.time, "monotonic", return_value=vault.IDLE_SECONDS + 2):
            assert not vault.unlocked(1)


def test_artifact_rejects_foreign_thread():
    conn = Mock()
    conn.execute.return_value.fetchone.return_value = None
    with pytest.raises(artifacts.ArtifactError):
        artifacts._link_thread(conn, 1, 999)
    assert conn.execute.call_count == 1


def test_rotation_contention_locks_final_merge():
    row = {"extra": "old", "disabled": 0}
    conn = Mock()
    conn.execute.return_value.fetchone.return_value = row

    @contextmanager
    def tx():
        yield conn

    with patch.object(token_store.db, "fetchone", return_value=row), patch.object(token_store.db, "tx", tx), patch.object(token_store.accounts, "cred_extra", return_value={"refresh_token": "old"}), patch.object(token_store, "encrypt", side_effect=lambda value: value):
        assert token_store.persist_rotated(lambda *a: False, 1, "old", "secret", {}, {"refresh_token": "new"})
    assert "FOR UPDATE" in conn.execute.call_args_list[0].args[0]
    assert '"refresh_token": "new"' in conn.execute.call_args.args[1][1]


def test_rotation_does_not_overwrite_relink():
    with patch.object(token_store.db, "fetchone", return_value={"extra": "new", "disabled": 0}), patch.object(token_store.accounts, "cred_extra", side_effect=[{"refresh_token": "old"}, {"refresh_token": "relinked"}]):
        assert not token_store.persist_rotated(lambda *a: False, 1, "old", "secret", {}, {"refresh_token": "rotated"})
