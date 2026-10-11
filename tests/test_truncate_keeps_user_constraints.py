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
