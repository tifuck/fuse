import asyncio
from unittest.mock import patch

from app.chat_generation.stream import _with_beats


def test_closing_during_heartbeat_cancels_and_drains_provider():
    async def scenario():
        finished = asyncio.Event()
        async def provider():
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()
        with patch("app.chat_generation.stream.PROVIDER_BEAT_S", 0.001):
            frames = _with_beats(provider())
            assert await anext(frames) == ("beat", None)
            await frames.aclose()
        assert finished.is_set()
    asyncio.run(scenario())


def test_provider_success_and_error_are_preserved():
    async def scenario():
        async def success():
            return 42
        async def failure():
            raise ValueError("provider failure")
        assert [frame async for frame in _with_beats(success())] == [("ok", 42)]
        frames = [frame async for frame in _with_beats(failure())]
        assert len(frames) == 1
        assert frames[0][0] == "err"
        assert isinstance(frames[0][1], ValueError)
    asyncio.run(scenario())
