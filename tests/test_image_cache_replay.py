"""Frozen image admission survives catalog refreshes and retained-history reloads."""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from app import api, chat_history, model_caps
from app.sources.base import ChatResult


def image(label="one"):
    return {"role": "user", "content": [
        {"type": "text", "text": label},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,QQ=="}},
    ]}


@pytest.fixture
def routed(monkeypatch):
    sent = []
    route = {"provider": "openai", "model": "eye", "image": True}
    for name in ("grok", "chatgpt", "zai", "devin", "kimi", "mimo", "claude"):
        monkeypatch.setattr(api, f"user_has_{name}", lambda uid: True)
    monkeypatch.setattr(api, "session_id_from", lambda *a, **k: None)
    monkeypatch.setattr(api, "_record_attempts", lambda *a, **k: None)
    monkeypatch.setattr(model_caps, "image_input", lambda *a, **k: route["image"])

    async def chat(body, **kwargs):
        sent.append(copy.deepcopy(body))
        return ChatResult(status=200, body={}, model=body["model"])

    async def pick(*args, **kwargs):
        return SimpleNamespace(type_id=route["provider"], source_id=1, chat=chat), route["model"]

    monkeypatch.setattr(api, "pick", pick)
    return route, sent


def invoke(rows, snapshot=None, prepared=None, **body):
    request = SimpleNamespace(headers={}, scope={"fuse_retained_history": True})
    return asyncio.run(api.invoke_chat(request, {"id": 1},
        {"model": "eye", "messages": rows, **body}, image_snapshot=snapshot, prepared=prepared))


@pytest.mark.parametrize("initial", [False, True, None])
def test_catalog_flip_restart_and_new_images(routed, initial):
    route, sent = routed
    route["image"] = initial
    rows = [{"role": "system", "content": "frozen"}, image()]
    snapshot = {"system": "frozen", "tools": [], "images": {}}
    durable = []

    async def persist():
        # The admission is durable before the provider receives any bytes.
        assert not sent
        durable.append(chat_history.envelope([image()], rows, {}, prompt_snapshot=snapshot))

    invoke(rows, snapshot["images"], persist)
    first = copy.deepcopy(sent[-1]["messages"])
    restored = json.loads(durable[0])
    rows = [{"role": "system", "content": restored["prompt_snapshot"]["system"]}, *restored["messages"]]
    snapshot = restored["prompt_snapshot"]
    route["image"] = initial is False
    rows.append(image("new"))
    invoke(rows, snapshot["images"])
    assert sent[-1]["messages"][:2] == first
    assert isinstance(sent[-1]["messages"][-1]["content"], list) is route["image"]
    assert isinstance(rows[1]["content"], list)  # retained pixels are never replaced
    assert "images" not in sent[-1] and "prompt_snapshot" not in sent[-1]


def test_route_switch_and_compaction_reset(routed):
    route, sent = routed
    rows, snapshot = [image()], {}
    route["image"] = False
    invoke(rows, snapshot)
    original = sent[-1]["messages"]
    route.update(model="other", image=True)
    invoke(rows, snapshot)
    assert sent[-1]["messages"] == rows
    route.update(model="eye", image=True)
    invoke(rows, snapshot)
    assert sent[-1]["messages"] == original
    route["provider"] = "ollama"
    invoke(rows, snapshot)
    assert sent[-1]["messages"] == rows
    route["provider"] = "openai"
    invoke(rows, {})  # runtime refresh_prompt creates a new snapshot at compaction
    assert sent[-1]["messages"] == rows


def test_client_snapshot_cannot_override_admission_or_leak(routed):
    route, sent = routed
    rows, forged = [image()], {}
    invoke(rows, forged)
    route["image"] = False
    invoke(rows, prompt_snapshot={"images": forged}, image_snapshot=forged)
    assert model_caps.OMITTED in sent[-1]["messages"][0]["content"]
    assert "prompt_snapshot" not in sent[-1] and "image_snapshot" not in sent[-1]


def test_no_image_budget_and_retired_budget_decisions_are_readmitted(monkeypatch):
    monkeypatch.setattr(model_caps, "image_input", lambda *a, **k: True)
    rows = [image(str(i)) for i in range(60)]
    snapshot = {}
    out, omitted = model_caps.prepare(rows, "openai", "eye", snapshot=snapshot)
    assert omitted == 0 and out == rows
    route = next(iter(snapshot.values()))
    for key in list(route)[1:]:
        route[key] = "budget"  # saved by the retired 16-image cap
    out, omitted = model_caps.prepare(rows, "openai", "eye", snapshot=snapshot)
    assert omitted == 0 and out == rows
    assert set(route.values()) == {"admit"}


@pytest.mark.parametrize("status,message", [
    (400, "too many images and documents: 101 > 100"),
    (413, "Request exceeds the maximum size"),
    (400, "Too many images in request. Max is 500."),
])
def test_provider_image_refusal_sheds_oldest_and_retries(routed, monkeypatch, status, message):
    route, sent = routed
    checkpoints = []

    async def chat(body, **kwargs):
        sent.append(copy.deepcopy(body))
        kept = sum(isinstance(m["content"], list) for m in body["messages"])
        if kept > 3:
            return ChatResult(status=status, body={"error": {"message": message}}, model=body["model"])
        return ChatResult(status=200, body={}, model=body["model"])

    async def pick(*args, **kwargs):
        return SimpleNamespace(type_id="openai", source_id=1, chat=chat), "eye"

    async def persist():
        checkpoints.append(len(sent))

    monkeypatch.setattr(api, "pick", pick)
    monkeypatch.setattr(api, "_safe_record_usage", lambda *a, **k: True)
    rows = [image(str(i)) for i in range(10)]
    snapshot = {}
    *_, result = invoke(rows, snapshot, persist)
    assert result.status == 200
    assert [sum(isinstance(m["content"], list) for m in b["messages"]) for b in sent] == [10, 5, 2]
    assert checkpoints == [0, 1, 2]  # every new admission is durable before it is sent
    final = sent[-1]["messages"]
    assert model_caps.LIMIT_OMITTED in final[0]["content"]
    assert isinstance(final[-1]["content"], list)  # newest images survive
    assert result.response_headers["X-Fuse-Images-Shed"] == "8"
    sent.clear()
    invoke(rows + [image("new")], snapshot)
    assert sent[0]["messages"][:10] == final  # the shed decision stays frozen


def test_unrelated_refusal_and_text_only_request_do_not_retry(routed, monkeypatch):
    _, sent = routed

    async def chat(body, **kwargs):
        sent.append(body)
        return ChatResult(status=400, body={"error": {"message": "invalid tool schema"}}, model=body["model"])

    async def pick(*args, **kwargs):
        return SimpleNamespace(type_id="openai", source_id=1, chat=chat), "eye"

    monkeypatch.setattr(api, "pick", pick)
    *_, result = invoke([image()], {})
    assert result.status == 400 and len(sent) == 1
    sent.clear()
    *_, result = invoke([{"role": "user", "content": "no images"}], {})
    assert len(sent) == 1


def test_runtime_persists_admission_before_dispatch(monkeypatch, routed):
    from app import artifacts, chat_runtime, chat_store, hosts

    _, sent = routed
    saved = []
    monkeypatch.setattr(hosts, "settings_for", lambda uid: {})
    monkeypatch.setattr(artifacts, "list_for_prompt", lambda *a: [])
    monkeypatch.setattr(chat_store, "save_child_meter", lambda *a: None)
    monkeypatch.setattr(chat_store, "take_child_results", lambda *a: [])
    monkeypatch.setattr(chat_store, "take_followups", lambda *a: [])
    monkeypatch.setattr(chat_store, "steer_display_deliveries", lambda *a: [])
    monkeypatch.setattr(chat_store, "followup_display_deliveries", lambda *a: [])
    monkeypatch.setattr("app.context_meter.should_auto_compact", lambda *a, **k: False)
    monkeypatch.setattr(api, "_safe_record_usage", lambda *a, **k: True)

    def save(run_id, messages, **kwargs):
        saved.append(json.loads(json.dumps({"messages": messages, **kwargs["extra"]})))

    async def dispatch(src, body, *args, **kwargs):
        assert saved[-1]["prompt_snapshot"]["images"]
        sent.append(copy.deepcopy(body))
        return ChatResult(status=400, body={"error": {"message": "test stop"}}, model=body["model"])

    monkeypatch.setattr(chat_store, "save_turn_progress", save)
    monkeypatch.setattr(api, "_run_chat", dispatch)

    async def run(raw):
        request = SimpleNamespace(headers={}, scope={})
        response = await chat_runtime.stream_dashboard_chat(request, {"id": 1}, raw,
            frozen_context={"instructions": "", "profile": ""})
        async for _ in response.body_iterator:
            pass

    raw = {"model": "eye", "messages": [image()], "tools": [], "run_id": "synthetic",
           "history_replay": True, "agent": {"depth": 1, "resumed": True}}
    asyncio.run(run(raw))
    first = sent[-1]["messages"]
    restored = saved[-1]
    routed[0]["image"] = False
    raw.update(messages=restored["messages"], prompt_snapshot=restored["prompt_snapshot"])
    asyncio.run(run(raw))
    assert sent[-1]["messages"] == first


def test_large_tool_history_serialized_prefix_survives_replay(routed):
    route, sent = routed
    rows = [{"role": "system", "content": "Frozen large-history regression"}, image("initial")]
    snapshot = {"system": rows[0]["content"], "tools": [], "images": {}}
    previous = b""
    previous_count = 0
    raw_args = '{ "path" : "src/example.py", "offset" : 1, "limit" : 2000 }'
    # ~800 KB of ASCII tool evidence: ~200k tokens at a rough 4 bytes/token,
    # not a tokenizer measurement or a claim about any provider's context limit.
    result_text = "synthetic evidence line with stable spacing and punctuation.\n"
    result_text = (result_text * (10000 // len(result_text) + 1))[:10000]

    def serialized(messages):
        return json.dumps(messages, ensure_ascii=False, separators=(",", ":")).encode()

    for turn in range(81):
        route["image"] = not (25 <= turn < 55)
        if turn:
            call_id = f"read-{turn}"
            rows.extend([
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": call_id, "type": "function", "function": {
                        "name": "read_file", "arguments": raw_args}}]},
                {"role": "tool", "tool_call_id": call_id, "content": result_text},
            ])
        if turn in (25, 55):
            rows.append(image(f"new image at turn {turn}"))
        invoke(chat_history.for_model(rows, "eye"), snapshot["images"])
        wire = sent[-1]["messages"]
        if previous_count:
            assert serialized(wire[:previous_count]) == previous, f"historical prefix changed at turn {turn}"
        for row in wire:
            for call in row.get("tool_calls", []):
                assert call["function"]["arguments"] == raw_args
        previous, previous_count = serialized(wire), len(wire)
        # Simulate a process reload between every appended exchange, retaining
        # the originals plus server-owned admission rather than stripped pixels.
        restored = json.loads(json.dumps({"messages": rows, "prompt_snapshot": snapshot}))
        rows, snapshot = restored["messages"], restored["prompt_snapshot"]
        sent.clear()

    assert 800000 <= len(previous) <= 850000
    assert len([row for row in wire if row.get("role") == "tool"]) == 80
    image_rows = [row for row in wire if row.get("role") == "user"]
    assert isinstance(image_rows[0]["content"], list)
    assert model_caps.OMITTED in image_rows[1]["content"]
    assert isinstance(image_rows[2]["content"], list)


def test_checkpoint_failure_prevents_provider_send(routed):
    _, sent = routed

    async def fail():
        raise RuntimeError("checkpoint failed")

    with pytest.raises(RuntimeError, match="checkpoint failed"):
        invoke([image()], {}, fail)
    assert sent == []
