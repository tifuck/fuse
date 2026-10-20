// provider-error presentation. Dependencies are live getters supplied by index.js.
//
// Provider failures, one presentation for every provider. The server
// (app/provider_errors.py) words a provider failure as
//   "<Provider>: <summary>[; retry|resets in <span> (<clock>)]. <provider's own text>"
// with <summary> from its fixed _SUMMARY set; keep SUMMARIES in step with it.
// Limits (spent usage, no credits, not on the plan, rate limited) read as
// brass; everything else is a red fault. Text that is not in that form (Fuse's
// own failures, older stored errors) is a generic fault with the text as its
// detail.
export function create(deps) {
const SUMMARIES = {
  "rate limited": "limit",
  "plan usage limit reached": "limit",
  "out of credits": "limit",
  "not included in this plan": "limit",
  "account sign-in rejected": "fault",
  "access denied": "fault",
  "request rejected": "fault",
  "conversation too long for this model": "fault",
  "blocked by content policy": "fault",
  "model not available": "fault",
  "overloaded": "fault",
  "server error": "fault",
  "timed out": "fault",
  "stopped responding": "fault",
  "connection lost": "fault",
};
const SHAPE = /^([A-Za-z][\w.() -]{0,30}): ([a-z0-9][^;.]*?)(?:; ((?:retry|resets) in [^.]*?))?\.(?:\s+([\s\S]*))?$/;
// A subagent's failure wraps the provider's sentence for the parent model.
const CHILD = /talking to the model(?: after \d+ retr(?:y|ies))? \(([\s\S]*)\)\. No further work/;

function summaryTone(summary) {
    if (SUMMARIES[summary]) return SUMMARIES[summary];
    return /^[\w-]+ usage limit reached$/.test(summary) ? "limit" : "";
  }

// {provider, title, tone, reset, detail} for a stored error string.
function parseProviderError(text) {
    const raw = String(text || "").replace(/\s+/g, " ").trim();
    const inner = raw.match(CHILD);
    const hit = (inner ? inner[1] : raw).match(SHAPE);
    const tone = hit && summaryTone(hit[2]);
    if (!tone) return { provider: "", title: "Request failed", tone: "fault", reset: "", detail: raw };
    // "resets in 2h 10m (22:53 UTC)": the clock stays true after a reload,
    // the span does not, so a dated reset shows its clock.
    const clock = (hit[3] || "").match(/\(([^)]+)\)$/);
    const reset = clock ? `Resets ${clock[1]}` : (hit[3] || "").replace(/^\w/, (c) => c.toUpperCase());
    return {
      provider: hit[1],
      title: hit[2].charAt(0).toUpperCase() + hit[2].slice(1),
      tone,
      reset,
      detail: (hit[4] || "").trim(),
    };
  }

// One line for a failed call row: "ChatGPT · 5-hour usage limit reached · Resets 22:53 UTC".
function providerErrorLine(text) {
    const info = parseProviderError(text);
    if (!info.provider) return String(text || "");
    return [info.provider, info.title, info.reset].filter(Boolean).join(" · ");
  }

function providerErrorHtml(text) {
    const info = parseProviderError(text);
    const head = [info.provider, info.title].filter(Boolean).map(deps.escapeHtml).join(' <span class="chat-error-sep">·</span> ');
    return `<span class="chat-error-head"><span class="chat-error-title">${head}</span>` +
      (info.reset ? `<span class="chat-error-reset">${deps.escapeHtml(info.reset)}</span>` : "") + "</span>" +
      (info.detail ? `<span class="chat-error-detail">${deps.escapeHtml(info.detail)}</span>` : "");
  }

  return { parseProviderError, providerErrorLine, providerErrorHtml };
}
