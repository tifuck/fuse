"""Generation helpers extracted from app.chat_runtime.

Modules:
  parsing     — pure text / tool-call parsing
  stream      — SSE frames, client frame shaping, wait pacing + beats
  policy      — provider-failure classification, resume policy, capability cache
  consume     — one provider round -> done-summary
  metadata    — compaction summaries, thread titles, recaps
  tool_jobs   — ToolJob model and transcript construction
  tool_runner — executing one tool round
  messages    — request-message assembly and no-tool grounding

app.chat_runtime re-exports the externally pinned names explicitly.
"""
