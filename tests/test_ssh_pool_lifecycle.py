"""SSH pool lifecycle and bounded helper output (no live SSH)."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import ssh_pool, workspace


class _Conn:
    def __init__(self, channels=()):
        self._channels = dict.fromkeys(channels)
        self.closed = False

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True


def _reset():
    ssh_pool._pool.clear()
    ssh_pool._gen.clear()
    ssh_pool._key_locks.clear()


def test_reaper_closes_idle_sessions_but_never_busy_ones():
    _reset()
    idle, busy, fresh = _Conn(), _Conn(channels=["chan"]), _Conn()
    old = time.monotonic() - ssh_pool.IDLE_CLOSE - 5
    ssh_pool._pool.update({(1, 1): {"conn": idle, "last": old}, (1, 2): {"conn": busy, "last": old},
                           (1, 3): {"conn": fresh, "last": time.monotonic()}})
    asyncio.run(ssh_pool.reap_idle())
    assert idle.closed and (1, 1) not in ssh_pool._pool
    assert not busy.closed and (1, 2) in ssh_pool._pool
    assert not fresh.closed and (1, 3) in ssh_pool._pool
    _reset()


def test_trust_evicts_and_a_connect_racing_the_pin_change_is_discarded():
    _reset()
    pooled = _Conn()
    ssh_pool._pool[(1, 7)] = {"conn": pooled, "last": time.monotonic()}
    with patch.object(ssh_pool, "set_pin", return_value={"ok": True}):
        asyncio.run(ssh_pool.trust(1, 7, "ssh-ed25519", "ab" * 16))
    assert pooled.closed and (1, 7) not in ssh_pool._pool

    old_key, new_key = _Conn(), _Conn()
    dials = []

    async def connect_once(user_id, host_id, probe=False):
        dials.append(1)
        if len(dials) == 1:
            ssh_pool.evict(user_id, host_id)  # pin changed while this dial ran
            return old_key, "a", "d"
        return new_key, "a", "d"

    with patch.object(ssh_pool, "_connect_once", connect_once):
        got = asyncio.run(ssh_pool.connection(1, 7))
    assert got is new_key and old_key.closed
    assert ssh_pool._pool[(1, 7)]["conn"] is new_key
    _reset()


def test_run_remote_keeps_only_the_cap_of_huge_output():
    class Reader:
        def __init__(self, chunks):
            self.chunks = list(chunks)

        async def read(self, n):
            return self.chunks.pop(0) if self.chunks else ""

    big = ["x" * 65536] * 50
    proc = SimpleNamespace(stdout=Reader(big), stderr=Reader(["err"]), wait=AsyncMock(), exit_status=0)
    conn = SimpleNamespace(create_process=AsyncMock(return_value=proc))

    async def run():
        with patch.object(workspace, "_is_agent", return_value=False), \
                patch("app.ssh_pool.connection", AsyncMock(return_value=conn)):
            return await workspace._run_remote(1, 2, "true", "", 10, rate=False)

    res = asyncio.run(run())
    assert len(res.stdout) == workspace._SSH_OUT_MAX
    assert res.stderr == "err"
