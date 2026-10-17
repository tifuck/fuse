"""The compacted transcript view: durable rows from the latest handoff on."""
from app.chat_generation.messages import _session_tail_block
from app.chat_views import child_display_messages, compaction_cut

HANDOFF = {"type": "marker", "kind": "compaction", "text": "Context compacted.", "summary": "Earlier work"}
ROWS = [
    {"id": "u1", "role": "user", "content": "old"},
    {"id": "a1", "role": "assistant", "content": "old reply"},
    {"id": "u2", "role": "user", "content": "Hi"},
    {"id": "a2", "role": "assistant", "content": "Hello"},
]


def state(**checkpoint):
    return {"checkpoints": [{"summary": "Earlier work", **checkpoint}]}


def test_manual_compact_starts_after_its_boundary_under_the_summary():
    assert compaction_cut(ROWS, state(provider=True, display_through_id="a1")) == {
        "id": "a1", "include": False, "summary": "Earlier work"}
    assert compaction_cut(ROWS, state(through_message_id="a1"))["include"] is False


def test_mid_turn_handoff_starts_at_the_reply_that_carries_it():
    rows = [*ROWS[:3], {**ROWS[3], "segments": [HANDOFF, {"type": "content", "text": "Hello"}]}]
    assert compaction_cut(rows, state(provider=True, display_through_id="a2")) == {
        "id": "a2", "include": True, "summary": ""}


def test_no_cut_without_a_checkpoint_or_its_boundary_row():
    assert compaction_cut(ROWS, {"checkpoints": []}) is None
    assert compaction_cut(ROWS, state(provider=True, display_through_id="gone")) is None
    assert compaction_cut(ROWS, state(provider=True)) is None


def test_subagent_panel_hides_the_server_session_tail():
    tail = _session_tail_block([])
    rows = child_display_messages({"status": "completed", "request": {}, "message": {}, "provider_messages": [
        {"role": "user", "content": "Do the task" + tail},
        {"role": "assistant", "content": "Done"},
    ]}, include_current=True)
    assert rows[0]["content"] == "Do the task"
