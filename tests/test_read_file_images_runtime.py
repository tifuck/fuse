"""Canonical file images: no external hosts or model requests."""
import asyncio
import copy
import json
import pytest
from types import SimpleNamespace

from app import chat_history, chat_runtime as runtime, chat_store, chats, context, model_caps
from app.sources import chatgpt, claude, ollama

DATA = "iVBORw0KGgo="
IMAGE = {"mime": "image/png", "data": DATA, "width": 2, "height": 3,
         "original_width": 2, "original_height": 3, "format": "PNG", "frame": 0, "frames": 1}


def job(index=0, done=True):
    return runtime.ToolJob(index, f"call-{index}", "read_file", '{"path":"shot.png"}',
                           f"event-{index}", text="Image read: shot.png", done=done,
                           trace={"path": "shot.png", "image": chats.tool_image_metadata(IMAGE)},
                           image=dict(IMAGE) if done else None)


def pixels(rows):
    return [p["image_url"]["url"] for row in rows if isinstance(row.get("content"), list)
            for p in row["content"] if p.get("type") == "image_url"]


def test_batch_order_and_switching_from_text_only():
    jobs = [job(), job(1)]
    rows = runtime._tool_messages(jobs)
    assert [r["role"] for r in rows] == ["assistant", "tool", "tool", "user"]
    assert len(pixels(rows)) == 2
    saved = copy.deepcopy(rows)
    for kind, model, count in [("zai", "glm-5.3", 0), ("claude", "claude-sonnet-5", 2),
                                ("zai", "glm-5.3", 0), ("openai", "unknown", 2)]:
        outgoing, omitted = model_caps.prepare(rows, kind, model)
        assert len(pixels(outgoing)) == count
        assert omitted == 2 - count
        assert rows == saved
    assert "untrusted read_file image output" in rows[-1]["content"][0]["text"]
    assert '"call_id": "call-0"' in rows[-1]["content"][0]["text"]
    assert DATA not in json.dumps(rows[:-1])


def test_request_image_budgets_preserve_sent_prefix_without_mutating_history(monkeypatch):
    rows = runtime._tool_messages([job(i) for i in range(3)])
    original = copy.deepcopy(rows)
    monkeypatch.setattr(model_caps, "MAX_REQUEST_IMAGES", 2)
    outgoing, omitted = model_caps.prepare(rows, "claude", "claude-sonnet-5")
    assert omitted == 1 and len(pixels(outgoing)) == 2
    assert outgoing[-1]["content"][5]["text"] == model_caps.BUDGET_OMITTED
    prefix, _ = model_caps.prepare(runtime._tool_messages([job(i) for i in range(2)]), "claude", "claude-sonnet-5")
    assert outgoing[-1]["content"][:4] == prefix[-1]["content"]
    monkeypatch.setattr(model_caps, "MAX_REQUEST_IMAGE_CHARS", len(f"data:image/png;base64,{DATA}"))
    outgoing, omitted = model_caps.prepare(rows, "claude", "claude-sonnet-5")
    assert omitted == 2 and len(pixels(outgoing)) == 1
    assert rows == original


def test_partial_subagent_snapshot_seals_before_visual_message():
    rows = runtime._provider_snapshot([{"role": "system", "content": "child"}], [job(), job(1, False)])
    saved = json.loads(chat_store.dumps(rows))
    sealed = chat_store.seal_provider_messages(saved)
    assert [r["role"] for r in sealed] == ["assistant", "tool", "tool", "user"]
    assert sealed[2]["tool_call_id"] == "call-1"
    assert len(pixels(sealed)) == 1
    assert pixels(chat_history.for_model(sealed, "claude-sonnet-5")) == pixels(rows)
    text, _ = model_caps.prepare(sealed, "zai", "glm-5.3")
    assert not pixels(text)
    assert len(pixels(sealed)) == 1


def test_snapshot_envelope_reload_and_provider_adapters():
    rows = runtime._tool_messages([job()])
    display = [{"id": "u", "role": "user", "content": "inspect"}]
    envelope = json.loads(chat_history.envelope(display, rows, {}))
    assert pixels(envelope["messages"]) == [f"data:image/png;base64,{DATA}"]
    parts = envelope["messages"][-1]["content"]
    assert chatgpt._input_content(parts)[1]["image_url"].endswith(DATA)
    assert claude._user_blocks(parts)[1]["source"]["data"] == DATA
    assert ollama.to_ollama([rows[-1]])[0]["images"] == [DATA]
    assert chat_history._automated_user(rows[-1])


def test_trace_sanitization_and_honest_recovery():
    trace = {"name": "read_file", "status": "done", "path": "shot.png", "image": IMAGE}
    clean = chats._parse_tool_traces([trace], strict=True)[0]
    assert DATA not in json.dumps(clean)
    assert clean["image"]["width"] == 2
    fallback = chat_history._trace_rows({"id": "m", "tools": [clean]})
    assert "pixels are unavailable" in fallback[-1]["content"]
    assert not pixels(fallback)


def test_compaction_keeps_recent_pixels_and_marks_text_summary():
    rows = runtime._tool_messages([job()])
    assert context.estimate_content(rows[-1]["content"]) < 2000
    assert context.IMAGE_TOKENS == 1500
    head, tail = context.split_recent([{"role": "user", "content": "old" * 10000}, *rows], 3000)
    assert pixels(tail) == pixels(rows)
    summary = context.truncate_summary(rows)
    assert "Image pixels omitted" in summary
    assert DATA not in summary
    assert "not present as pixels" in context.COMPACT_PREAMBLE
    assert pixels(context.summarize_payload(rows)) == pixels(rows)


@pytest.mark.parametrize("invalid", [False, True])
def test_execution_keeps_image_only_in_snapshot_not_events(monkeypatch, invalid):
    async def execute(*args, **kwargs):
        return "Image read", {"name": "read_file", "status": "done", "path": "shot.png", "image": {**IMAGE, "data": "not-base64" if invalid else DATA}}
    monkeypatch.setattr(runtime, "execute_tool", execute)
    snapshots = []
    events = []
    async def disconnected():
        return False
    async def run():
        calls = runtime._tool_messages([job()])[0]["tool_calls"]
        async for item in runtime._run_tools(calls, user_id=1, allowed_urls=set(),
                request=SimpleNamespace(is_disconnected=disconnected), completion_i=0, budgets={},
                coding=True, session={"coding": True, "run_id": "run-test"}, loaded=["read_file"], persist=lambda jobs: snapshots.append(runtime._provider_snapshot([], jobs))):
            if isinstance(item, list):
                assert len(pixels(runtime._tool_messages(item))) == (0 if invalid else 1)
                if invalid:
                    assert "no pixels delivered" in item[0].text
            else:
                events.append(item)
    asyncio.run(run())
    assert any(pixels(snapshot) for snapshot in snapshots) is not invalid
    assert DATA not in json.dumps(events)
    if not invalid:
        image_event = next(e for e in events if e.get("image"))
        assert image_event["image"]["preview_url"] == "/chat/runs/run-test/images/ts_0_0"
        assert chats._parse_tool_traces([image_event])[0]["image"]["preview_url"] == image_event["image"]["preview_url"]
    assert any(event.get("image", {}).get("width") == 2 for event in events) is not invalid
