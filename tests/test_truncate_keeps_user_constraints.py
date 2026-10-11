from app import context_compact as context


def test_truncate_fallback_keeps_user_constraints_verbatim():
    rule = "IMPORTANT: this machine is read-only, never deploy or restart services."
    rows = [{"role": "user", "content": "Set up the project. " + "detail " * 120 + rule}]
    for i in range(40):
        rows.append({"role": "assistant", "content": f"step {i} " + "work " * 50})
        rows.append({"role": "user", "content": f"middle note {i}: keep going"})
    summary = context.truncate_summary(rows)
    assert rule in summary
    assert "middle note 20" in summary  # middle user rows are not dropped either
    assert len(summary) < 40_000


def test_learned_window_uses_smallest_live_source():
    from app import context_meter

    saved = (dict(context_meter._WINDOW_CACHE), dict(context_meter._WINDOW_SOURCES))
    try:
        context_meter.remember_window("llama", "same-name", 32_768, source_id=1)
        context_meter.remember_window("llama", "same-name", 131_072, source_id=2)
        # Whichever refreshed last used to win; the smaller backend overflowed.
        assert context_meter.cached_window("llama", "same-name") == 32_768
    finally:
        context_meter._WINDOW_CACHE.clear()
        context_meter._WINDOW_CACHE.update(saved[0])
        context_meter._WINDOW_SOURCES.clear()
        context_meter._WINDOW_SOURCES.update(saved[1])
