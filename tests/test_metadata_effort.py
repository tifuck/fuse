"""Title/recap calls pick an effort the resolved provider accepts.

A fixed reasoning_effort="none" was a 400 on Grok and Codex, so every title
on those pools fell through to a deterministic fallback.
"""
import asyncio

from app import api
from app.sources import metadata_effort
from app.sources.base import ChatResult


def test_metadata_effort_per_provider():
    assert metadata_effort("grok", "grok-4.7") == "low"
    assert metadata_effort("zai", "glm-5.3") == "none"
    assert metadata_effort("mimo", "mimo-v2.6-flash") == "none"
    # Unknown levels (the generic picker list): send no effort at all.
    for type_id in ("openai", "openrouter", "llama"):
        assert metadata_effort(type_id, "m") is None
    # Codex's levels start at low (an unread model falls back to low..high).
    assert metadata_effort("chatgpt", "m") == "low"


def test_invoke_chat_replaces_effort_for_the_resolved_route(monkeypatch):
    sent = []

    class Src:
        def __init__(self, type_id, status):
            self.type_id, self.source_id, self.status = type_id, 1, status

        async def chat(self, body, **_kw):
            sent.append((self.type_id, body.get("reasoning_effort")))
            return ChatResult(status=self.status, body={}, model=body["model"])

    for name in ("grok", "chatgpt", "zai", "devin", "kimi", "mimo", "claude", "cursor", "nanogpt"):
        monkeypatch.setattr(api, f"user_has_{name}", lambda _uid: True)
    monkeypatch.setattr(api, "_safe_record_usage", lambda *a, **k: True)
    monkeypatch.setattr(api, "session_id_from", lambda *a, **k: None)

    class Req:
        headers = {}
        scope = {}

    def run(type_id, status=200, **kwargs):
        sent.clear()
        monkeypatch.setattr(api, "pick", lambda *a, **k: asyncio.sleep(0, (Src(type_id, status), "m")))
        body = {"model": "m", "messages": [{"role": "user", "content": "hi"}], "reasoning_effort": "high"}
        asyncio.run(api._invoke_chat(Req(), {"id": 1}, body, **kwargs))
        return list(sent)

    assert run("grok", cheap_reasoning=True) == [("grok", "low")]
    assert run("chatgpt", cheap_reasoning=True) == [("chatgpt", "low")]
    # A failed pool attempt is not retried on another route.
    assert run("zai", 503, cheap_reasoning=True) == [("zai", "none")]
    # Ordinary chat keeps the caller's effort.
    assert run("grok") == [("grok", "high")]
