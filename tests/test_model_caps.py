"""Image input per resolved (provider, model): strip only for text-only models,
never mutate saved history, and keep provider transports honest."""
import copy

from app import chats, context_meter as context, model_caps
from app.sources import chatgpt, devin, kimi, ollama, openrouter

PNG = "data:image/png;base64,iVBORw0KGgo="


def _turn(url=PNG):
    return [{"role": "user", "content": [{"type": "text", "text": "what is this"},
                                          {"type": "image_url", "image_url": {"url": url}}]}]


def test_known_capabilities_per_provider():
    assert model_caps.image_input("claude", "claude/claude-sonnet-5") is True
    assert model_caps.image_input("grok", "grok-4.7") is True
    assert model_caps.image_input("zai", "glm-5.3") is False
    assert model_caps.image_input("zai", "glm-5v-turbo") is True
    assert model_caps.image_input("mimo", "mimo-v2.5") is True
    assert model_caps.image_input("mimo", "mimo-v2.5-pro") is None
    assert model_caps.image_input("devin", "devin/claude-opus-5") is True
    assert model_caps.image_input("devin", "devin/swe-2") is None
    assert model_caps.image_input("openai", "anything") is None


def test_catalog_metadata_wins():
    rows = chatgpt.parse_catalog_payload({"models": [
        {"slug": "gpt-text-only", "input_modalities": ["text"]},
        {"slug": "gpt-seeing", "input_modalities": ["text", "image"]},
    ]})
    assert rows == ["gpt-text-only", "gpt-seeing"]
    assert model_caps.image_input("chatgpt", "gpt-text-only") is False
    assert model_caps.image_input("chatgpt", "gpt-seeing") is True
    parsed = kimi.parse_catalog_payload({"data": [{"id": "k-text", "supports_image_in": False}, {"id": "k-plain"}]})
    assert [row["image"] for row in parsed] == [False, None]


def test_openrouter_input_modalities(monkeypatch):
    monkeypatch.setattr(openrouter, "cached_catalog", lambda: {"by_id": {
        "acme/eye": {"image": True}, "acme/text": {"image": False}}})
    assert model_caps.image_input("openrouter", "openrouter/acme/eye") is True
    assert model_caps.image_input("openrouter", "acme/text") is False
    assert model_caps.image_input("openrouter", "acme/missing") is None


def test_switch_text_only_and_back_keeps_history():
    saved = _turn()
    before = copy.deepcopy(saved)
    text, omitted = model_caps.prepare(saved, "zai", "glm-5.3")
    assert omitted == 1
    assert text[0]["content"] == "what is this\n\n" + model_caps.OMITTED
    vision, omitted = model_caps.prepare(saved, "claude", "claude-sonnet-5")
    assert omitted == 0 and vision[0]["content"][1]["image_url"]["url"] == PNG
    unknown, omitted = model_caps.prepare(saved, "openai", "local")
    assert omitted == 0 and unknown == saved
    assert saved == before


def test_prepare_keeps_plain_string_parts():
    # A mixed string+part content list must keep the string when the images
    # are replaced with text notes, not silently drop it.
    msgs = [{"role": "user", "content": ["see this", {"type": "image_url", "image_url": {"url": PNG}}]}]
    out, omitted = model_caps.prepare(msgs, "zai", "glm-5.3")
    assert omitted == 1
    assert out[0]["content"].startswith("see this")
    assert model_caps.OMITTED in out[0]["content"]


def test_saved_attachment_round_trip():
    content = chats.model_content("", [{"kind": "image", "data": PNG}])
    text, omitted = model_caps.prepare([{"role": "user", "content": content}], "zai", "glm-5.3")
    assert omitted == 1 and model_caps.OMITTED in text[0]["content"]


def test_input_image_is_normalized_for_adapters():
    msgs = [{"role": "user", "content": [{"type": "input_image", "image_url": PNG, "detail": "high"}]}]
    out, _ = model_caps.prepare(msgs, "chatgpt", "gpt-seeing")
    assert out[0]["content"] == [{"type": "image_url", "image_url": {"url": PNG, "detail": "high"}}]
    assert chatgpt._input_content(out[0]["content"])[0] == {"type": "input_image", "image_url": PNG, "detail": "high"}


def test_meter_counts_the_note_not_the_image():
    with_image = context.meter_from_messages(_turn(), model="claude-sonnet-5", type_id="claude")
    stripped = context.meter_from_messages(_turn(), model="glm-5.3", type_id="zai")
    assert with_image["used"] - stripped["used"] > 1000


def test_devin_refuses_remote_images_instead_of_dropping():
    assert devin._has_remote_image(_turn("https://example.test/a.png"))
    assert not devin._has_remote_image(_turn())


def test_invoke_chat_strips_per_route_and_reports(monkeypatch):
    """Pool route is text-only; a failed pool attempt is not retried elsewhere."""
    import asyncio

    from app import api
    from app.sources.base import ChatResult

    sent = []

    class Src:
        def __init__(self, type_id, status):
            self.type_id, self.source_id, self.status = type_id, 1, status

        async def chat(self, body, **_kw):
            sent.append((self.type_id, body["messages"]))
            return ChatResult(status=self.status, body={}, model=body["model"])

    for name in ("grok", "chatgpt", "zai", "devin", "kimi", "mimo", "claude", "cursor", "nanogpt"):
        monkeypatch.setattr(api, f"user_has_{name}", lambda _uid: True)
    monkeypatch.setattr(api, "pick", lambda *a, **k: asyncio.sleep(0, (Src("zai", 503), "glm-5.3")))
    monkeypatch.setattr(api, "_safe_record_usage", lambda *a, **k: True)
    monkeypatch.setattr(api, "session_id_from", lambda *a, **k: None)

    class Req:
        headers = {}
        scope = {}

    body = {"model": "glm-5.3", "messages": _turn()}
    *_, result = asyncio.run(api._invoke_chat(Req(), {"id": 1}, body))
    assert sent == [("zai", [{"role": "user", "content": "what is this\n\n" + model_caps.OMITTED}])]
    assert result.status == 503
    assert result.response_headers["X-Fuse-Images-Omitted"] == "1"
    assert body["messages"] == _turn()

    sent.clear()
    monkeypatch.setattr(api, "pick", lambda *a, **k: asyncio.sleep(0, (Src("zai", 200), "glm-5.3")))
    *_, result = asyncio.run(api._invoke_chat(Req(), {"id": 1}, {"model": "glm-5.3", "messages": _turn()}))
    assert result.response_headers["X-Fuse-Images-Omitted"] == "1"


def test_image_validation_rejects_malformed_payloads():
    import pytest
    from fastapi import HTTPException
    from app import api

    api._validate_messages(_turn())
    api._validate_messages(_turn("https://example.test/a.png"))
    for url in ("", "data:image/png,hello", "data:image/png;base64,???", "data:text/plain;base64,QQ==", "file:///tmp/a.png"):
        with pytest.raises(HTTPException) as exc:
            api._validate_messages(_turn(url))
        assert exc.value.status_code == 400


def test_ollama_catalog_reads_capabilities_without_completions(monkeypatch):
    import asyncio
    import httpx

    calls = []
    def handle(request):
        calls.append(request.url.path)
        if request.url.path.endswith('/tags'):
            return httpx.Response(200, json={"models": [{"name": "audit-eye"}, {"name": "audit-text"}]})
        import json
        name = json.loads(request.content)["model"]
        return httpx.Response(200, json={"capabilities": ["completion", "vision"] if name == "audit-eye" else ["completion"]})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(ollama.httpx, 'AsyncClient', lambda **kw: client_type(transport=httpx.MockTransport(handle), **kw))
    monkeypatch.setattr(ollama.accounts, 'cred_secret', lambda cred: 'synthetic')
    monkeypatch.setattr(ollama, '_tags_cache', {})
    src = object.__new__(ollama.OllamaSource)
    src.source_id, src.creds, src.base = -900, [{"id": -1}], 'https://example.test/api'
    assert asyncio.run(src._tags()) == ['audit-eye', 'audit-text']
    assert model_caps.image_input('ollama', 'audit-eye') is True
    assert model_caps.image_input('ollama', 'audit-text') is False
    assert calls == ['/api/tags', '/api/show', '/api/show']


def test_ollama_merges_native_and_converted_images():
    msgs = [{"role": "user", "images": ["bmF0aXZl"], "content": _turn()[0]["content"]}]
    assert ollama.to_ollama(msgs)[0]["images"] == ["bmF0aXZl", "iVBORw0KGgo="]
