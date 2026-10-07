// call-semantics presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
function consoleFreezeThoughtTimes(msg, live) {
    if (!msg._thoughtMs) msg._thoughtMs = {};
    if (!msg._thoughtClock) msg._thoughtClock = { liveKey: "", liveStart: 0 };
    const segs = Array.isArray(msg.segments) ? msg.segments : [];
    const groups = [];
    for (let i = 0; i < segs.length; i += 1) {
      if (!segs[i] || segs[i].type !== "reasoning") continue;
      let j = i;
      while (j + 1 < segs.length && segs[j + 1] && segs[j + 1].type === "reasoning") j += 1;
      groups.push({ key: "think-" + j, index: j, start: i });
      i = j;
    }
    const elapsed = (deps.liveElapsedSeconds(msg, live) || 0) * 1000;
    const last = groups[groups.length - 1];
    const liveKey = live && last && !segs.some((seg, index) => index > last.index) && !deps.SETTLED_PHASES.has(deps.runPhase(msg)) ? last.key : "";
    // The server times each thought as it streams (ms on the segment); the
    // estimates below only cover messages saved before it did.
    groups.forEach((group) => {
      if (group.key === liveKey) return;
      let sum = 0;
      for (let k = group.start; k <= group.index; k += 1) {
        if (typeof segs[k].ms !== "number") return;
        sum += segs[k].ms;
      }
      msg._thoughtMs[group.key] = Math.max(1000, sum);
    });
    const clock = msg._thoughtClock;
    if (clock.liveKey && clock.liveKey !== liveKey) {
      if (msg._thoughtMs[clock.liveKey] == null) {
        msg._thoughtMs[clock.liveKey] = Math.max(1000, elapsed - (clock.liveStart || 0));
      }
    }
    if (liveKey && clock.liveKey !== liveKey) {
      clock.liveKey = liveKey;
      clock.liveStart = elapsed;
    } else if (!liveKey) {
      clock.liveKey = "";
    }
    if (!live) {
      let toolMs = 0;
      (msg.tools || []).forEach((trace) => {
        const n = Number(trace.call_duration_ms ?? trace.execution_duration_ms ?? trace.elapsed_ms ?? trace.latency_ms);
        if (Number.isFinite(n) && n > 0) toolMs += n;
      });
      const total = Number(msg.elapsedMs);
      const budget = Math.max(groups.length * 1000, (Number.isFinite(total) ? total : elapsed) - toolMs);
      const weights = groups.map((group) => {
        const seg = segs[group.index];
        return Math.max(1, String(seg && seg.text || "").length);
      });
      const sum = weights.reduce((a, b) => a + b, 0) || 1;
      let assigned = 0;
      const missing = groups.filter((group) => msg._thoughtMs[group.key] == null);
      missing.forEach((group, i) => {
        const share = i === missing.length - 1
          ? Math.max(1000, budget - assigned)
          : Math.max(1000, Math.round(budget * (weights[groups.indexOf(group)] / sum)));
        msg._thoughtMs[group.key] = share;
        assigned += share;
      });
      clock.liveKey = "";
    }
    return msg._thoughtMs;
  }

function ccVowels(word) {
    return (word.match(/[aeiou]/gi) || []).length;
  }

function ccIng(word) {
    const lower = word.toLowerCase();
    if (Object.hasOwn(deps.CC_ING, lower)) return deps.CC_ING[lower];
    if (/ie$/i.test(word)) return word.slice(0, -2) + "ying";
    if (/[^eoy]e$/i.test(word)) return word.slice(0, -1) + "ing";
    if (deps.CC_CVC.test(word) && ccVowels(word) === 1) return word + word.slice(-1) + "ing";
    if (/c$/i.test(word)) return word + "king";
    return word + "ing";
  }

function ccPast(word) {
    const lower = word.toLowerCase();
    if (Object.hasOwn(deps.CC_PAST, lower)) return deps.CC_PAST[lower];
    if (/e$/i.test(word)) return word + "d";
    if (/[^aeiou]y$/i.test(word)) return word.slice(0, -1) + "ied";
    if (deps.CC_CVC.test(word) && ccVowels(word) === 1) return word + word.slice(-1) + "ed";
    if (/c$/i.test(word)) return word + "ked";
    if (Object.hasOwn(deps.CC_ING, lower)) return deps.CC_ING[lower].slice(0, -3) + "ed";
    return word + "ed";
  }

function ccCase(source, word) {
    return /^[A-Z]/.test(source) ? word.charAt(0).toUpperCase() + word.slice(1) : word;
  }

function ccVerbLike(word) {
    if (word.length < 2 || word.length > 16 || !/^[A-Za-z]+$/.test(word) || word === word.toUpperCase()) return false;
    const lower = word.toLowerCase();
    if (deps.CC_PAST_FORMS.has(lower)) return false;
    if (Object.hasOwn(deps.CC_PAST, lower)) return true;
    if (/ing$/i.test(word) && word.length > 4) return false;
    if (/ed$/i.test(word) && !/eed$/i.test(word) && word.length > 3) return false;
    if (/s$/i.test(word) && !/(ss|us)$/i.test(word) && word.length > 3) return false;
    return /[aeiou]/i.test(word) || /y/i.test(word);
  }

function ccChain(rest, form) {
    return rest.replace(deps.CC_CHAIN.re, (...args) => {
      const match = args[0];
      const [pre, lead, comma, verb] = deps.CC_CHAIN.lookbehind ? ["", args[1], args[2], args[3]] : [args[1] || "", args[2], args[3], args[4]];
      const offset = args[deps.CC_CHAIN.lookbehind ? 4 : 5];
      return deps.CC_VERBS.has(verb) && !deps.CC_CHAIN_STOP.test(rest.slice(offset + match.length)) ? pre + (lead ?? comma ?? "") + form(verb) : match;
    });
  }

function ccInflect(text) {
    const trimmed = text.trimStart();
    const lead = text.slice(0, text.length - trimmed.length);
    const parts = /^(\S+)([\s\S]*)$/.exec(trimmed);
    if (!parts) return undefined;
    const word = parts[1];
    const rest = parts[2] || "";
    const prefixed = deps.CC_PREFIXED.exec(word);
    if (prefixed && ccVerbLike(prefixed[2])) {
      const [, prefix, stem] = prefixed;
      const whole = (prefix + stem).toLowerCase();
      if (deps.CC_PAST_FORMS.has(whole) || deps.CC_PAST_FORMS.has(stem.toLowerCase())) return undefined;
      const form = (table, fn) => prefix + "-" + ccCase(stem, Object.hasOwn(table, whole) ? table[whole].slice(prefix.length) : fn(stem));
      return { running: lead + form(deps.CC_ING, ccIng) + ccChain(rest, ccIng), done: lead + form(deps.CC_PAST, ccPast) + ccChain(rest, ccPast) };
    }
    if (!ccVerbLike(word)) return undefined;
    return { running: lead + ccCase(word, ccIng(word)) + ccChain(rest, ccIng), done: lead + ccCase(word, ccPast(word)) + ccChain(rest, ccPast) };
  }

function ccRunningForm(text) {
    const first = /^\s*(\S+)/.exec(text)?.[1];
    if (first === undefined) return undefined;
    const base = (deps.CC_PREFIXED.exec(first)?.[2] ?? first).toLowerCase();
    return deps.CC_VERBS.has(base) || Object.hasOwn(deps.CC_PAST, base) || Object.hasOwn(deps.CC_ING, base) ? ccInflect(text)?.running : undefined;
  }

function ccTimeoutText(ms) {
    return ccDuration(ms).replace(/(?: 0[smh])+$/, "");
  }

function ccDuration(ms) {
    if (ms < 60000) return ms <= 0 ? "0s" : `${Math.floor(ms / 1000)}s`;
    let d = Math.floor(ms / 86400000);
    let h = Math.floor((ms % 86400000) / 3600000);
    let m = Math.floor((ms % 3600000) / 60000);
    let s = Math.round((ms % 60000) / 1000);
    if (s === 60) { s = 0; m += 1; }
    if (m === 60) { m = 0; h += 1; }
    if (h === 24) { h = 0; d += 1; }
    if (d > 0) return `${d}d ${h}h ${m}m`;
    if (h > 0) return `${h}h ${m}m ${s}s`;
    return `${m}m ${s}s`;
  }

function ccFlat(text) {
    return String(text || "").replace(/\s+/g, " ").trim();
  }

function ccClip(text, max) {
    const chars = Array.from(String(text || ""));
    return chars.length <= max ? chars.join("") : chars.slice(0, max - 1).join("") + "…";
  }

function ccTokens(n) {
    const value = Math.max(0, Math.round(Number(n) || 0));
    return value >= 1000 ? CC_COMPACT.format(value).toLowerCase() : String(value);
  }

function ccBytes(n) {
    const bytes = Math.max(0, Math.round(Number(n) || 0));
    const fixed = (value) => value.toFixed(1).replace(/\.0$/, "");
    if (bytes < 1024) return `${bytes} bytes`;
    if (bytes < 1024 * 1024) return `${fixed(bytes / 1024)}KB`;
    if (bytes < 1024 ** 3) return `${fixed(bytes / 1024 / 1024)}MB`;
    return `${fixed(bytes / 1024 ** 3)}GB`;
  }

function ccErrorFold(lines) {
    const all = lines.map((line) => String(line ?? ""));
    if (!all.length) all.push("failed");
    if (!/^(Error|Cancelled): /.test(all[0])) all[0] = `Error: ${all[0]}`;
    const show = all.slice(0, 10);
    const hidden = all.length - show.length;
    const html = (rows) => `<div class="chat-console-lines is-error">${rows.map((line) => `<div class="chat-console-stdout">${deps.renderAnsi(line) || " "}</div>`).join("")}</div>`;
    return {
      status: "",
      preview: html(show) + (hidden > 0 ? `<div class="chat-console-more" data-step-toggle>… +${hidden} ${hidden === 1 ? "line" : "lines"}</div>` : ""),
      full: hidden > 0 ? html(all) : "",
      keep: true,
    };
  }

const CC_COMPACT = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

  return { consoleFreezeThoughtTimes, ccVowels, ccIng, ccPast, ccCase, ccVerbLike, ccChain, ccInflect, ccRunningForm, ccTimeoutText, ccDuration, ccFlat, ccClip, ccTokens, ccBytes, ccErrorFold, CC_COMPACT };
}
