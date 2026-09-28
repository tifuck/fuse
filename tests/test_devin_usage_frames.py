"""Devin usage frames merge instead of the last frame replacing the rest."""
from app.sources.devin import _merge_usage_frame, _openai_usage


def test_later_partial_frame_keeps_prompt_and_cache():
    acc = {}
    acc = _merge_usage_frame(acc, {"prompt_tokens": 42000, "cached_tokens": 40000, "cache_write_tokens": 1500, "completion_tokens": 0})
    acc = _merge_usage_frame(acc, {"prompt_tokens": 8, "cached_tokens": 0, "completion_tokens": 3205, "model_uid": "m"})
    u = _openai_usage(acc)
    assert u["prompt_tokens"] == 42000
    assert u["cached_tokens"] == 40000
    assert u["cache_write_tokens"] == 1500
    assert u["output_tokens"] == 3205
    assert u["total_tokens"] == 42000 + 3205
    assert acc["model_uid"] == "m"


def test_cumulative_snapshots_are_exact():
    acc = {}
    for out in (10, 200, 450):
        acc = _merge_usage_frame(acc, {"prompt_tokens": 5000, "cached_tokens": 4096, "completion_tokens": out})
    assert _openai_usage(acc)["output_tokens"] == 450
