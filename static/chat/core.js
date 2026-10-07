// Shared helpers: formatting plus run predicates over S. No factories.
import { S } from './state.js';
import { runIsActive } from './traces/semantics.js';

// Packed snapshots (?pack=1) carry each text/reasoning segment's length in
// place of a second copy of its text; slice it back out of the message.
function untimed(segment) {
  if (!segment || typeof segment !== "object" || !("ms" in segment)) return segment;
  const { ms, ...rest } = segment;
  return rest;
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function safeUrl(raw) {
  const url = String(raw || "").trim();
  if (/^https?:\/\//i.test(url) || /^mailto:/i.test(url)) return url;
  if (/^\/a\/[A-Za-z0-9_-]+(?:\/(?:content|download))?$/.test(url)) return url;
  if (url === "/artifacts") return url;
  return "";
}

// Shows `text` in a content block: paced while its message is streaming
// (`writing`), whole otherwise.


function splitThink(text) {
  const re = /<\/?(?:think|thinking|thought|reasoning)>/gi;
  let reasoning = "";
  let content = "";
  let inThink = false;
  let i = 0;
  let match;
  while ((match = re.exec(text))) {
    const chunk = text.slice(i, match.index);
    if (inThink) reasoning += chunk;
    else content += chunk;
    inThink = match[0][1] !== "/";
    i = match.index + match[0].length;
  }
  const tail = text.slice(i);
  if (inThink) reasoning += tail;
  else content += tail;
  return { reasoning, content, inThink };
}

function deltaParts(obj) {
  if (!obj || typeof obj !== "object") return {};
  if (obj.error) {
    const err = obj.error;
    return { error: typeof err === "string" ? err : err.message || "Upstream error" };
  }
  const choice = (obj.choices || [])[0] || {};
  const delta = choice.delta || {};
  const message = choice.message || {};
  let content = "";
  let reasoning = "";
  const take = (value) => {
    if (typeof value === "string") return value;
    if (Array.isArray(value)) {
      return value
        .map((part) => (typeof part === "string" ? part : part && (part.text || part.content) || ""))
        .join("");
    }
    if (value && typeof value === "object") {
      if (typeof value.content === "string") return value.content;
      if (typeof value.text === "string") return value.text;
      if (typeof value.summary === "string") return value.summary;
    }
    return "";
  };
  content = take(delta.content) || (choice.delta ? "" : take(message.content));
  for (const key of ["reasoning_content", "reasoning", "thinking", "reasoning_text"]) {
    reasoning += take(delta[key]) || take(message[key]) || take(obj[key]);
  }
  return {
    content,
    reasoning,
    finish: choice.finish_reason,
    usage: obj.usage && typeof obj.usage === "object" ? obj.usage : null,
  };
}

function formatTurnDuration(ms) {
  const n = Math.max(0, Math.round(Number(ms) || 0));
  if (n < 10_000) {
    const sec = n / 1000;
    const digits = n < 1000 || (n < 10_000 && n % 1000) ? 1 : 0;
    return sec.toFixed(digits).replace(/\.0$/, "") + "s";
  }
  const total = Math.max(1, Math.round(n / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h) {
    if (m && s) return `${h}h ${m}m ${s}s`;
    if (m) return `${h}h ${m}m`;
    if (s) return `${h}h ${s}s`;
    return `${h}h`;
  }
  if (m) return s ? `${m}m ${s}s` : `${m}m`;
  return `${s}s`;
}

function usageInOut(usage) {
  if (!usage || typeof usage !== "object") return null;
  const inRaw = usage.prompt_tokens ?? usage.input_tokens;
  const outRaw = usage.output_tokens ?? usage.completion_tokens;
  if (inRaw == null && outRaw == null) return null;
  const inn = Math.max(0, Math.round(Number(inRaw) || 0));
  const out = Math.max(0, Math.round(Number(outRaw) || 0));
  if (!inn && !out) return null;
  return { inn, out };
}

function formatTokens(value) {
  const n = Math.max(0, Math.round(Number(value) || 0));
  if (n >= 1_000_000 || Math.round(n / 1000) >= 1000) return (n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1).replace(/\.0$/, "") + "M";
  if (n >= 10_000) return Math.round(n / 1000) + "k";
  if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, "") + "k";
  return String(n);
}

function formatTurnMeta(msg) {
  const bits = [];
  {
    const split = usageInOut(msg && msg.usage);
    if (split && split.out) {
      const fmt = new Intl.NumberFormat();
      bits.push({
        text: formatTokens(split.out) + " tok",
        title: fmt.format(split.out) + " output tokens this reply",
      });
    }
  }
  if (msg && msg.elapsedMs != null) {
    const text = formatTurnDuration(msg.elapsedMs);
    bits.push({ text, title: text + " elapsed" });
  }
  return bits;
}

function runIsStopping(run) {
  return Boolean(run && (run.cancel_requested || (S.stoppingRunId && run.id === S.stoppingRunId)));
}

function messageRunLive(msg) {
  return Boolean(runIsActive(S.activeRun) && msg?.id === S.activeRun.message_id);
}

function humanMs(ms) {
  const n = Number(ms);
  if (!Number.isFinite(n) || n < 0) return "";
  const rounded = Math.round(n);
  if (rounded < 1000) return `${rounded}ms`;
  const secs = rounded / 1000;
  if (secs < 60) return `${secs < 10 ? secs.toFixed(1) : Math.round(secs)}s`;
  return formatTurnDuration(n);
}

export { deltaParts, escapeHtml, formatTokens, formatTurnDuration, formatTurnMeta, humanMs, messageRunLive, runIsStopping, safeUrl, splitThink, untimed, usageInOut };
