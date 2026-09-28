-- Undo reasoning double-counted into output for providers whose own total is
-- prompt + completion (reasoning already inside completion): Kimi, MiMo, OpenRouter.
-- SET runs left to right, so the cost uses the old completion/reasoning split.
UPDATE usage_events e
LEFT JOIN model_prices p ON p.provider = e.source_type AND p.model = e.model
SET e.estimated_cost_micros = CASE
      WHEN e.pricing_known = 1 AND e.reported_cost_micros IS NULL AND COALESCE(p.reasoning_known, 0) = 0
      THEN GREATEST(0, e.estimated_cost_micros - ROUND(LEAST(e.completion_tokens, e.reasoning_tokens) * p.output_micros_per_million / 1000000))
      ELSE e.estimated_cost_micros END,
    e.output_tokens = GREATEST(e.completion_tokens, e.reasoning_tokens),
    e.visible_tokens = GREATEST(e.completion_tokens - e.reasoning_tokens, 0),
    e.total_tokens = e.prompt_tokens + GREATEST(e.completion_tokens, e.reasoning_tokens)
WHERE e.source_type <> 'grok' AND e.reasoning_tokens > 0
  AND e.output_tokens = e.completion_tokens + e.reasoning_tokens
  AND e.total_tokens = e.prompt_tokens + e.completion_tokens;
