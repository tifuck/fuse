/* Usage page: fetches /usage.json for the chosen range/provider/scope and
   paints every analytic section. The server-rendered lifetime card needs
   no script. Relies on escapeText() from app.js. */
(() => {
  const root = document.querySelector("[data-usage-dashboard]");
  if (!root) return;

  const $ = (sel) => root.querySelector(sel);
  const esc = (s) => escapeText(s == null ? "" : String(s));
  const compactFmt = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
  const intFmt = new Intl.NumberFormat();
  const SERIES = ["--uz-s1", "--uz-s2", "--uz-s3", "--uz-s4", "--uz-s5", "--uz-s6"];
  const MIX = { uncached: "--uz-s1", cached: "--uz-s3", write: "--uz-s6", visible: "--uz-s4", reasoning: "--uz-s5" };
  const RANGE_TEXT = { "30m": "Last 30 minutes", "6h": "Last 6 hours", "24h": "Last 24 hours", "3d": "Last 3 days", "7d": "Last 7 days", "30d": "Last 30 days" };
  const RANGES = Object.keys(RANGE_TEXT);
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

  const state = { range: "24h", provider: "", scope: "account", mode: "tokens", sort: "tokens", dir: -1 };
  const params = new URLSearchParams(location.search);
  if (RANGES.includes(params.get("range"))) state.range = params.get("range");
  if (params.get("provider")) state.provider = params.get("provider").slice(0, 32);
  if (params.get("scope") === "all" && root.dataset.admin === "1") state.scope = "all";

  const RECENT_PREVIEW = 10;
  let recentAll = false;
  let data = null;
  let controller = null;
  // Colour follows the model, not its rank: slots are handed out in first-seen
  // order and kept for the session, so filters never repaint survivors.
  const slots = new Map();
  const providerSlots = new Map();

  function cssVar(name) {
    return getComputedStyle(root).getPropertyValue(name).trim() || "#888";
  }
  function slotColor(map, id) {
    if (id === "other") return cssVar("--uz-other");
    if (!map.has(id)) {
      const used = new Set(map.values());
      const free = SERIES.find((s) => !used.has(s));
      map.set(id, free || null);
    }
    const slot = map.get(id);
    return slot ? cssVar(slot) : cssVar("--uz-other");
  }
  const modelColor = (id) => slotColor(slots, id);
  const providerColor = (id) => slotColor(providerSlots, id);

  function compact(n) {
    return compactFmt.format(Number(n) || 0);
  }
  function money(micros, { zero = "$0" } = {}) {
    const d = (Number(micros) || 0) / 1e6;
    if (!d) return zero;
    if (d >= 1000) return "$" + compactFmt.format(d);
    if (d >= 1) return "$" + d.toFixed(2);
    if (d >= 0.01) return "$" + d.toFixed(2);
    return "$" + d.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
  }
  function pct(n, digits = 1) {
    if (n === null || n === undefined || !Number.isFinite(Number(n))) return "—";
    return Number(n).toFixed(digits).replace(/\.0$/, "") + "%";
  }
  function when(value, opts) {
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? "" : d.toLocaleString([], opts);
  }
  function tickLabel(at) {
    const r = state.range;
    if (r === "30m" || r === "6h" || r === "24h") return when(at, { hour: "numeric", minute: "2-digit" });
    if (r === "3d") return when(at, { weekday: "short", hour: "numeric" });
    return when(at, { month: "short", day: "numeric" });
  }
  function bucketLabel(at) {
    const start = new Date(at);
    const end = new Date(start.getTime() + (data.range.bucket_seconds || 3600) * 1000);
    const day = { weekday: "short", month: "short", day: "numeric" };
    const time = { hour: "numeric", minute: "2-digit" };
    if ((data.range.bucket_seconds || 0) >= 86400) return start.toLocaleDateString([], day);
    return `${start.toLocaleDateString([], day)}, ${start.toLocaleTimeString([], time)} – ${end.toLocaleTimeString([], time)}`;
  }
  function ago(value) {
    const t = new Date(value).getTime();
    if (!t) return "";
    const s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    if (s < 86400) return Math.floor(s / 3600) + "h ago";
    return Math.floor(s / 86400) + "d ago";
  }

  /* —— Controls —— */
  function syncPressed(group, attr, value) {
    if (!group) return;
    group.querySelectorAll(`[${attr}]`).forEach((b) => {
      const on = b.getAttribute(attr) === value;
      b.classList.toggle("on", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }
  function syncControls() {
    syncPressed($("[data-usage-range]"), "data-range", state.range);
    syncPressed($("[data-usage-scope]"), "data-scope", state.scope);
    syncPressed($("[data-chart-mode]"), "data-mode", state.mode);
  }
  function syncUrl() {
    const q = new URLSearchParams(location.search);
    state.range === "24h" ? q.delete("range") : q.set("range", state.range);
    state.provider ? q.set("provider", state.provider) : q.delete("provider");
    state.scope === "all" ? q.set("scope", "all") : q.delete("scope");
    const next = location.pathname + (q.toString() ? "?" + q : "") + location.hash;
    history.replaceState(null, "", next);
  }

  $("[data-usage-range]")?.addEventListener("click", (e) => {
    const b = e.target.closest("[data-range]");
    if (!b || b.dataset.range === state.range) return;
    state.range = b.dataset.range;
    load();
  });
  $("[data-usage-scope]")?.addEventListener("click", (e) => {
    const b = e.target.closest("[data-scope]");
    if (!b || b.dataset.scope === state.scope) return;
    state.scope = b.dataset.scope;
    load();
  });
  $("[data-usage-provider]")?.addEventListener("change", (e) => {
    state.provider = e.target.value;
    load();
  });
  $("[data-chart-mode]")?.addEventListener("click", (e) => {
    const b = e.target.closest("[data-mode]");
    if (!b || b.dataset.mode === state.mode) return;
    state.mode = b.dataset.mode;
    syncControls();
    if (data) plot.render(true);
  });
  root.querySelectorAll("[data-sort]").forEach((b) => {
    b.addEventListener("click", () => {
      const key = b.dataset.sort;
      state.dir = state.sort === key ? -state.dir : -1;
      state.sort = key;
      root.querySelectorAll("[data-sort]").forEach((x) => {
        if (x === b) x.setAttribute("aria-sort", state.dir < 0 ? "descending" : "ascending");
        else x.removeAttribute("aria-sort");
      });
      if (data) renderModels();
    });
  });

  /* —— Header caption + provider options —— */
  function renderCaption() {
    const el = $("[data-usage-caption]");
    if (!el) return;
    const provider = state.provider ? (data.provider_options || []).find((p) => p.id === state.provider)?.name || state.provider : "";
    const who = data.scope === "all" ? "all accounts" : "your account";
    el.innerHTML = `<b>${esc(RANGE_TEXT[data.range.id] || "")}</b> · ${esc(who)}${provider ? " · " + esc(provider) : ""} · compared with the previous ${esc((RANGE_TEXT[data.range.id] || "").replace("Last ", ""))}`;
  }
  function renderProviderOptions() {
    const sel = $("[data-usage-provider]");
    if (!sel) return;
    sel.innerHTML =
      '<option value="">All providers</option>' +
      (data.provider_options || []).map((p) => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("");
    sel.value = state.provider;
  }

  /* —— KPIs —— */
  // Volumes scale from zero; ratios (floor: "min") scale between their own
  // extremes so a steady 95% reads as a line, not a solid block.
  function sparkPath(values, w, h, floor) {
    const nums = values.map((v) => (v === null || v === undefined ? null : Number(v) || 0));
    const real = nums.filter((v) => v !== null);
    if (nums.length < 2 || real.length < 1) return null;
    const max = Math.max(...real);
    const min = floor === "min" ? Math.min(...real) : 0;
    if (max <= 0 || max === min) return { line: `M0 ${(h / 2).toFixed(1)} H${w}`, area: "" };
    let last = real[0];
    const pts = nums.map((v, i) => {
      if (v !== null) last = v;
      return [(i * w) / (nums.length - 1), h - 1 - ((last - min) / (max - min)) * (h - 3)];
    });
    const line = "M" + pts.map((p) => p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" L");
    return { line, area: `${line} L${w} ${h} L0 ${h} Z` };
  }
  function spark(values, floor) {
    const p = sparkPath(values, 120, 28, floor);
    if (!p) return "";
    return `<svg viewBox="0 0 120 28" preserveAspectRatio="none">${p.area && floor !== "min" ? `<path class="area" d="${p.area}"/>` : ""}<path class="line" d="${p.line}" vector-effect="non-scaling-stroke"/></svg>`;
  }
  function delta(value, { unit = "%", good = null, suffix = "vs prev." } = {}) {
    if (value === null || value === undefined) return { html: `<span>No prior data</span>`, cls: "" };
    const n = Number(value) || 0;
    const arrow = n > 0 ? "↑" : n < 0 ? "↓" : "→";
    const size = Math.abs(n) >= 100 ? Math.abs(n).toFixed(0) : Math.abs(n).toFixed(1).replace(/\.0$/, "");
    let cls = "";
    if (good && Math.abs(n) >= 0.5) cls = (n > 0) === (good === "up") ? "good" : "bad";
    return { html: `<b>${arrow} ${size}${unit}</b><span>${suffix}</span>`, cls };
  }
  function setKpi(id, value, d, sparkValues, { floor, note } = {}) {
    const el = root.querySelector(`[data-kpi="${id}"]`);
    if (!el) return;
    el.querySelector("[data-kpi-value]").innerHTML = value;
    el.querySelector("[data-kpi-note]").textContent = note || "";
    const dl = el.querySelector("[data-kpi-delta]");
    dl.innerHTML = d.html;
    dl.className = "uz-delta " + (d.cls || "");
    el.querySelector("[data-kpi-spark]").innerHTML = sparkValues && !data.summary.empty ? spark(sparkValues, floor) : "";
  }
  function renderKpis() {
    const s = data.summary;
    const series = data.series || [];
    const noRates = !s.pricing_coverage && !s.cost;
    const empty = s.empty;
    const none = { html: "<span>No traffic in range</span>", cls: "" };
    setKpi("tokens", compact(s.tokens), empty ? none : delta(s.token_change), series.map((p) => p.tokens));
    setKpi("requests", intFmt.format(s.requests), empty ? none : delta(s.request_change), series.map((p) => p.requests));
    setKpi(
      "cost",
      noRates ? "—" : esc(s.cost_label),
      empty
        ? none
        : noRates
          ? { html: `<span>${s.untracked >= s.requests ? "Billed to your OpenRouter key" : "No rates for these models"}</span>` }
          : delta(s.cost_change),
      series.map((p) => p.cost),
      { note: !noRates && s.pricing_coverage < 100 && s.requests ? `${s.pricing_coverage}% priced` : "" }
    );
    setKpi(
      "cache",
      pct(s.cache_hit),
      empty ? none : s.cache_hit === null ? { html: "<span>No prompt tokens</span>" } : delta(s.cache_hit_change, { unit: " pts", good: "up" }),
      series.map((p) => (p.prompt ? p.cached / p.prompt : null)),
      { floor: "min" }
    );
    setKpi(
      "success",
      s.success_rate === null ? "—" : pct(s.success_rate),
      empty ? none : delta(s.success_change, { unit: " pts", good: "up" }),
      series.map((p) => (p.requests ? (p.requests - p.failed) / p.requests : null)),
      { floor: "min", note: s.failed ? `${intFmt.format(s.failed)} failed` : "" }
    );
    setKpi(
      "per_request",
      s.per_request === null ? "—" : compact(s.per_request) + "<small>tok</small>",
      empty ? none : delta(s.per_request_change),
      series.map((p) => (p.requests ? p.tokens / p.requests : null)),
      { floor: "min" }
    );
  }

  /* —— Stacked activity chart —— */
  const MODE = {
    tokens: { key: "segments", total: "tokens", title: "Tokens by model", fmt: (v) => intFmt.format(v), axis: compact },
    cost: { key: "cost_segments", total: "cost", title: "Est. cost by model", fmt: (v) => money(v), axis: (v) => money(v) },
    requests: { key: "request_segments", total: "requests", title: "Requests by model", fmt: (v) => intFmt.format(v), axis: compact },
  };
  const plot = (() => {
    const el = $("[data-plot]");
    const tip = $("[data-tip]");
    let touched = false;
    let drawnW = 0;
    el.addEventListener("touchstart", () => (touched = true), { passive: true });

    function hide() {
      el.classList.remove("is-hovering");
      el.querySelectorAll(".col.is-hot").forEach((c) => c.classList.remove("is-hot"));
      tip.hidden = true;
    }
    function show(col, ev) {
      const m = MODE[state.mode];
      const point = data.series[Number(col.dataset.i)];
      if (!point) return;
      el.classList.add("is-hovering");
      el.querySelectorAll(".col.is-hot").forEach((c) => c !== col && c.classList.remove("is-hot"));
      col.classList.add("is-hot");
      const total = Number(point[m.total]) || 0;
      const rows = data.series_models
        .map((s) => ({ name: s.name, color: modelColor(s.id), v: Number(point[m.key][s.id]) || 0 }))
        .concat([{ name: "Other", color: modelColor("other"), v: Number(point[m.key].other) || 0 }])
        .filter((r) => r.v)
        .sort((a, b) => b.v - a.v);
      const head = [`${intFmt.format(point.tokens)} tokens`, `${intFmt.format(point.requests)} req`];
      if (point.cost) head.push(money(point.cost));
      if (point.failed) head.push(`${point.failed} failed`);
      tip.innerHTML =
        `<strong>${esc(bucketLabel(point.at))}</strong><span class="head">${esc(head.join(" · "))}</span>` +
        rows
          .map(
            (r) =>
              `<span class="line"><i class="uz-dot" style="background:${r.color}"></i><span>${esc(r.name)}</span><b>${esc(m.fmt(r.v))} · ${total ? Math.round((r.v * 100) / total) : 0}%</b></span>`
          )
          .join("");
      tip.hidden = false;
      const wrap = el.parentElement.getBoundingClientRect();
      const box = col.getBoundingClientRect();
      let x = (ev && ev.clientX ? ev.clientX : box.left + box.width / 2) - wrap.left + 14;
      if (x + tip.offsetWidth > wrap.width - 4) x = x - tip.offsetWidth - 28;
      tip.style.left = Math.max(4, x) + "px";
      tip.style.top = Math.max(4, Math.min((ev && ev.clientY ? ev.clientY - wrap.top : 40) - tip.offsetHeight / 2, wrap.height - tip.offsetHeight - 4)) + "px";
    }
    el.addEventListener("mousemove", (ev) => {
      if (touched) return;
      const col = ev.target.closest(".col");
      col ? show(col, ev) : hide();
    });
    el.addEventListener("mouseleave", () => !touched && hide());
    el.addEventListener("click", (ev) => {
      const col = ev.target.closest(".col");
      col ? show(col, ev) : hide();
    });
    el.addEventListener("focusin", (ev) => {
      const col = ev.target.closest(".col");
      if (col) show(col);
    });
    el.addEventListener("focusout", hide);

    function niceMax(v) {
      if (v <= 0) return 1;
      const exp = Math.pow(10, Math.floor(Math.log10(v)));
      const f = v / exp;
      const step = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((c) => f <= c);
      return step * exp;
    }

    function render(force) {
      const w = Math.floor(el.clientWidth);
      const h = Math.floor(el.clientHeight);
      if (w < 80 || h < 80) return;
      if (!force && Math.abs(w - drawnW) < 4) return;
      drawnW = w;
      hide();
      const m = MODE[state.mode];
      const title = $("[data-chart-title]");
      if (title) title.textContent = m.title;
      const sub = $("[data-chart-sub]");
      if (sub) {
        const s = data.summary;
        const total = state.mode === "cost" ? money(s.cost) : intFmt.format(s[m.total === "tokens" ? "tokens" : "requests"]);
        sub.textContent = `${total} total · ${data.range.bucket} buckets`;
      }
      const points = data.series || [];
      const max = Math.max(0, ...points.map((p) => Number(p[m.total]) || 0));
      const ids = data.series_models.map((s) => s.id).concat(["other"]);
      renderLegend();
      if (!max) {
        el.innerHTML = `<p class="uz-empty">${state.mode === "cost" && data.summary.tokens ? "No rates configured for these models yet." : "No recorded requests in this range."}</p>`;
        return;
      }
      const narrow = w < 560;
      const minStep = narrow ? 7 : 9;
      const width = Math.max(w, points.length * minStep + 60);
      const top = 10;
      const bottom = 26;
      const top_ = niceMax(max);
      const labels = [0, 1, 2, 3, 4].map((i) => m.axis((top_ * i) / 4));
      const left = Math.max(...labels.map((l) => l.length)) * 6.4 + 12;
      const right = 6;
      const plotW = width - left - right;
      const plotH = h - top - bottom;
      const step = plotW / points.length;
      const barW = Math.max(2, Math.min(28, step * 0.64));
      const y = (v) => (v / top_) * plotH;
      let svg = `<svg width="${width}" height="${h}" viewBox="0 0 ${width} ${h}" role="img" aria-label="${esc(m.title)}">`;
      labels.forEach((label, i) => {
        const gy = Math.round(top + plotH - (plotH * i) / 4) + 0.5;
        svg += `<line class="grid" x1="${left}" x2="${width - right}" y1="${gy}" y2="${gy}"/>`;
        svg += `<text class="axis" x="${left - 8}" y="${gy + 3.5}" text-anchor="end">${esc(label)}</text>`;
      });
      const want = narrow ? 4 : Math.max(4, Math.min(8, Math.floor(plotW / 110)));
      const every = Math.max(1, Math.ceil(points.length / want));
      points.forEach((p, i) => {
        const x = left + i * step + (step - barW) / 2;
        let yy = top + plotH;
        const label = `${bucketLabel(p.at)}: ${m.fmt(Number(p[m.total]) || 0)}`;
        svg += `<g class="col" data-i="${i}" tabindex="0" role="graphics-symbol" aria-label="${esc(label)}">`;
        svg += `<rect class="hit" x="${left + i * step}" y="${top}" width="${step}" height="${plotH}"/>`;
        const stack = ids.map((id) => [id, Number(p[m.key][id]) || 0]).filter(([, v]) => v);
        stack.forEach(([id, v], k) => {
          const bh = Math.max(1, y(v));
          yy -= bh;
          const isTop = k === stack.length - 1;
          const r = isTop ? Math.min(3, barW / 2, bh) : 0;
          // Only the top of the stack is rounded; the baseline stays square.
          const d = r
            ? `M${x} ${yy + bh} V${yy + r} Q${x} ${yy} ${x + r} ${yy} H${x + barW - r} Q${x + barW} ${yy} ${x + barW} ${yy + r} V${yy + bh} Z`
            : `M${x} ${yy + bh} V${yy} H${x + barW} V${yy + bh} Z`;
          svg += `<path class="bar" d="${d}" fill="${modelColor(id)}"/>`;
        });
        svg += `</g>`;
        if (i % every === 0 && points.length - i > every * 0.4) {
          svg += `<text class="axis" x="${x + barW / 2}" y="${h - 8}" text-anchor="${i === 0 ? "start" : "middle"}">${esc(tickLabel(p.at))}</text>`;
        }
      });
      svg += "</svg>";
      el.innerHTML = svg;
      if (width > w) el.scrollLeft = el.scrollWidth;
    }
    return { render };
  })();

  function renderLegend() {
    const el = $("[data-legend]");
    const m = MODE[state.mode];
    const hasOther = (data.series || []).some((p) => Number(p[m.key].other));
    el.innerHTML =
      data.series_models
        .map((s) => `<span title="${esc(s.name)} · ${esc(s.provider_label)}"><i class="uz-dot" style="background:${modelColor(s.id)}"></i><span class="n">${esc(s.name)}</span><em>${esc(s.provider_label)}</em></span>`)
        .join("") + (hasOther ? `<span><i class="uz-dot" style="background:${modelColor("other")}"></i>Other models</span>` : "");
    el.hidden = !data.series_models.length;
  }

  /* —— Token anatomy —— */
  function renderAnatomy() {
    const mix = (data.mix || []).filter((p) => p.tokens > 0);
    const s = data.summary;
    $("[data-mix-bar]").innerHTML = mix
      .map((p) => `<span style="flex:${p.tokens} 1 0;background:${cssVar(MIX[p.id])}" title="${esc(p.name)} ${pct(p.share)}"></span>`)
      .join("");
    $("[data-mix]").innerHTML = mix.length
      ? mix
          .map(
            (p) =>
              `<div><dt><i class="uz-dot" style="background:${cssVar(MIX[p.id])}"></i>${esc(p.name)}</dt><dd>${compact(p.tokens)}<small>${pct(p.share)}</small></dd></div>`
          )
          .join("")
      : '<p class="uz-empty">No tokens in this range.</p>';
    const facts = [];
    const priced = s.pricing_coverage > 0 || s.cost > 0;
    // Rates cover priced traffic only; say so when some of the range is unpriced.
    const partial = s.priced < s.requests ? " (priced)" : "";
    if (priced && s.blended !== null) facts.push(["Cost per 1M tokens" + partial, money(s.blended * 1e6)]);
    if (priced && s.cost_per_request !== null) facts.push(["Cost per request" + partial, money(s.cost_per_request)]);
    if (s.cache_saved_known && s.cache_saved) facts.push(["Saved by cache (list rates)", money(s.cache_saved), "good"]);
    if (s.prompt) facts.push(["Output per 1K input", compact((s.output * 1000) / s.prompt) + " tok"]);
    if (s.output) facts.push(["Reasoning share of output", pct((s.reasoning * 100) / s.output)]);
    if (s.peak_at) facts.push(["Busiest bucket", `${tickLabel(s.peak_at)} · ${compact(s.peak_tokens)}`]);
    if (s.models_used) facts.push(["Models used", intFmt.format(s.models_used)]);
    $("[data-facts]").innerHTML = facts
      .map(([k, v, cls]) => `<div><dt>${esc(k)}</dt><dd class="${cls || ""}">${esc(v)}</dd></div>`)
      .join("");
  }

  /* —— Models table —— */
  function changeCell(item) {
    if (item.is_new) return '<span class="uz-chg new">new</span>';
    if (item.token_change === null || item.token_change === undefined) return '<span class="uz-chg">—</span>';
    const n = Number(item.token_change);
    const size = Math.abs(n) >= 100 ? Math.abs(n).toFixed(0) : Math.abs(n).toFixed(0);
    return `<span class="uz-chg">${n > 0 ? "↑" : n < 0 ? "↓" : "→"}${size}%</span>`;
  }
  function trendSvg(values, color) {
    const p = sparkPath(values || [], 72, 20);
    return p ? `<svg viewBox="0 0 72 20" preserveAspectRatio="none" aria-hidden="true"><path d="${p.line}" stroke="${color}" vector-effect="non-scaling-stroke"/></svg>` : "";
  }
  function renderModels() {
    const body = $("[data-models]");
    const sub = $("[data-models-sub]");
    const models = (data.models || []).slice();
    if (!models.length) {
      body.innerHTML = '<tr><td colspan="9" class="uz-empty">No model usage in this range.</td></tr>';
      if (sub) sub.textContent = "Nothing recorded yet";
      return;
    }
    const total = data.summary.tokens || models.reduce((a, m) => a + m.tokens, 0);
    const val = (m) => {
      const v = m[state.sort];
      if (state.sort === "cost" && !m.cost_known && !m.cost) return -1;
      return v === null || v === undefined ? -1 : Number(v);
    };
    models.sort((a, b) => (val(a) - val(b)) * state.dir || b.tokens - a.tokens);
    const inSeries = new Set(data.series_models.map((s) => s.id));
    const more = Math.max(0, data.summary.models_used - models.length);
    if (sub) sub.textContent = `${data.summary.models_used} model${data.summary.models_used === 1 ? "" : "s"}${more ? ` · top ${models.length} shown` : ""}`;
    body.innerHTML = models
      .map((m) => {
        const color = modelColor(inSeries.has(m.id) ? m.id : "other");
        const share = total ? (m.tokens * 100) / total : 0;
        const errors = m.failed
          ? `<span class="${m.error_rate >= 5 ? "uz-warn" : ""}">${pct(m.error_rate)}</span><span class="uz-cell-sub">${m.failed} failed</span>`
          : '<span class="mute">0%</span>';
        const cost = !m.cost_tracked
          ? '<span class="uz-rate-needed" title="Billed to your own OpenRouter key; Fuse does not cost it">own key</span>'
          : m.cost_known || m.cost
            ? money(m.cost)
            : '<span class="uz-rate-needed" title="No price configured for this model">no rate</span>';
        const perM = m.per_million !== null && m.per_million !== undefined ? money(m.per_million, { zero: "$0" }) : "—";
        return `<tr>
          <td class="uz-cell-name"><div class="uz-name"><i class="uz-dot" style="background:${color}"></i><div><strong title="${esc(m.name)}">${esc(m.name)}</strong><small>${esc(m.provider_label)}${m.failed ? `<span class="uz-sm-only uz-warn"> · ${m.failed} failed (${pct(m.error_rate, 0)})</span>` : ""}</small></div></div></td>
          <td class="num" data-label="Tokens"><div class="uz-share"><i><b style="width:${share.toFixed(1)}%"></b></i><span>${compact(m.tokens)}</span></div><span class="uz-cell-sub">${pct(share)}</span></td>
          <td class="num" data-label="Requests">${intFmt.format(m.requests)}<span class="uz-cell-sub">${m.per_request ? compact(m.per_request) + "/req" : ""}</span></td>
          <td class="num uz-hide-md" data-label="In / out">${compact(m.prompt)} / ${compact(m.output || m.completion)}</td>
          <td class="num" data-label="Cache hit">${pct(m.cache_hit, 0)}</td>
          <td class="num uz-cell-err" data-label="Errors">${errors}</td>
          <td class="num" data-label="Cost">${cost}</td>
          <td class="num uz-hide-md" data-label="$ / 1M">${perM}</td>
          <td class="uz-cell-trend"><div class="uz-trend">${trendSvg(m.spark, color)}${changeCell(m)}</div></td>
        </tr>`;
      })
      .join("");
  }

  /* —— Providers + sources —— */
  function barRows(items, { total, name, meta, extraClass }) {
    return items
      .map((it) => {
        const share = total ? (it.tokens * 100) / total : 0;
        return `<div class="uz-bar-row ${extraClass ? extraClass(it) : ""}">
          <div class="uz-name"><div><strong>${name(it)}</strong><small>${esc(meta(it))}</small></div></div>
          <div class="uz-bar-vals">${compact(it.tokens)} · ${pct(share, share >= 10 ? 0 : 1)}<small>${esc(intFmt.format(it.requests))} req${it.cost ? " · " + money(it.cost) : ""}</small></div>
          <div class="uz-bar-track"><b style="width:${Math.max(share, 0.5).toFixed(1)}%"></b></div>
        </div>`;
      })
      .join("");
  }
  function renderBreakdowns() {
    const total = data.summary.tokens;
    const prov = $("[data-providers]");
    prov.innerHTML = data.providers.length
      ? barRows(data.providers, {
          total,
          name: (p) => esc(p.name),
          meta: (p) => {
            const bits = [];
            if (p.cache_hit !== null) bits.push(`${pct(p.cache_hit, 0)} cache hit`);
            if (p.failed) bits.push(`${p.failed} failed`);
            if (p.is_new) bits.push("new this period");
            else if (p.token_change !== null) bits.push(`${p.token_change > 0 ? "↑" : "↓"}${Math.abs(p.token_change).toFixed(0)}% vs prev.`);
            return bits.join(" · ");
          },
        })
      : '<p class="uz-empty">No provider usage in this range.</p>';
    const src = $("[data-sources]");
    src.innerHTML = (data.keys || []).length
      ? barRows(data.keys, {
          total,
          name: (k) => esc(k.name) + (k.kind === "chat" ? ' <span class="uz-tag uz-lg-only">built-in</span>' : ""),
          meta: (k) => [k.prefix ? k.prefix + "…" : k.kind === "chat" ? "Dashboard chat, agents and tasks" : "", k.last_at ? "last " + ago(k.last_at) : ""].filter(Boolean).join(" · "),
          extraClass: (k) => (k.kind === "chat" ? "is-chat" : ""),
        })
      : '<p class="uz-empty">No requests in this range.</p>';
  }

  /* —— Heatmap (local time) —— */
  function renderHeat() {
    const el = $("[data-heat]");
    const sub = $("[data-heat-sub]");
    const grid = Array.from({ length: 7 }, () => new Array(24).fill(0));
    const reqs = Array.from({ length: 7 }, () => new Array(24).fill(0));
    (data.activity || []).forEach(([epoch, tokens, requests]) => {
      const d = new Date(epoch * 1000);
      const day = (d.getDay() + 6) % 7;
      grid[day][d.getHours()] += tokens;
      reqs[day][d.getHours()] += requests;
    });
    const max = Math.max(0, ...grid.flat());
    if (!max) {
      el.innerHTML = '<div class="uz-heat-empty">No activity in this range.</div>';
      return;
    }
    const heat = cssVar("--uz-heat");
    const track = cssVar("--uz-track");
    const shade = (v) => (v ? `color-mix(in srgb, ${heat} ${Math.round(18 + 82 * Math.sqrt(v / max))}%, ${track})` : "");
    let html = '<div class="uz-heat-grid" role="img" aria-label="Token activity by weekday and hour"><span></span>';
    for (let h = 0; h < 24; h += 1) html += `<span class="h">${h % 6 === 0 ? h : ""}</span>`;
    const byDay = grid.map((row) => row.reduce((a, b) => a + b, 0));
    const byHour = Array.from({ length: 24 }, (_, h) => grid.reduce((a, row) => a + row[h], 0));
    grid.forEach((row, d) => {
      html += `<span class="d">${DAYS[d]}</span>`;
      row.forEach((v, h) => {
        const t = `${DAYS[d]} ${String(h).padStart(2, "0")}:00 — ${intFmt.format(v)} tokens, ${reqs[d][h]} req`;
        html += `<span class="c" style="${v ? "background:" + shade(v) : ""}" title="${esc(t)}"></span>`;
      });
    });
    html += "</div>";
    const peakDay = byDay.indexOf(Math.max(...byDay));
    const peakHour = byHour.indexOf(Math.max(...byHour));
    const hourText = new Date(2000, 0, 1, peakHour).toLocaleTimeString([], { hour: "numeric" });
    const scale = [0.1, 0.3, 0.55, 0.8, 1].map((f) => `<i style="background:${shade(f * max)}"></i>`).join("");
    html += `<div class="uz-heat-foot"><span>Busiest <b>${DAYS[peakDay]}</b> · around <b>${esc(hourText)}</b></span><span class="uz-scale"><span>Less</span>${scale}<span>More</span></span></div>`;
    el.innerHTML = html;
    if (sub) {
      const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "local time";
      sub.textContent = `Tokens by weekday and hour · last ${data.activity_days} days · ${tz}`;
    }
  }

  /* —— Reliability —— */
  function renderReliability() {
    const el = $("[data-reliability]");
    const s = data.summary;
    if (!s.requests) {
      el.innerHTML = '<p class="uz-empty">No requests in this range.</p>';
      return;
    }
    const ok = s.ok_requests;
    const parts = [
      ["Succeeded", ok, "var(--uz-good)"],
      ["Failed", s.failed, "var(--uz-bad)"],
      ["Cancelled", s.cancelled, "var(--mute)"],
    ];
    let html = `<div class="uz-rel-top"><p class="uz-rel-big">${pct(s.success_rate)}<small>success</small></p><p class="uz-sub">${intFmt.format(s.requests)} requests</p></div>`;
    html += `<div class="uz-rel-bar">${parts.filter((p) => p[1]).map((p) => `<span style="flex:${p[1]} 1 0;background:${p[2]}" title="${p[0]} ${p[1]}"></span>`).join("")}</div>`;
    html += `<div class="uz-rel-key">${parts.map((p) => `<span><i class="uz-dot" style="background:${p[2]}"></i>${p[0]} <b>${intFmt.format(p[1])}</b></span>`).join("")}</div>`;
    const failing = (data.models || []).filter((m) => m.failed).sort((a, b) => b.failed - a.failed).slice(0, 4);
    if (failing.length) {
      const top = failing[0].failed;
      html += `<div class="uz-rel-list"><p class="uz-label">Failures by model</p>${failing
        .map(
          (m) => `<div class="uz-bar-row"><div class="uz-name"><div><strong>${esc(m.name)}</strong><small>${esc(m.provider_label)}</small></div></div><div class="uz-bar-vals">${m.failed} failed<small>${pct(m.error_rate)} of ${m.requests}</small></div><div class="uz-bar-track"><b style="width:${((m.failed * 100) / top).toFixed(1)}%"></b></div></div>`
        )
        .join("")}</div>`;
    } else {
      html += `<div class="uz-rel-list"><p class="uz-sub">No failed requests in this range.</p></div>`;
    }
    el.innerHTML = html;
  }

  /* —— Recent —— */
  function renderRecent() {
    const body = $("[data-recent]");
    const rows = data.recent || [];
    const sub = $("[data-recent-sub]");
    if (sub) sub.textContent = rows.length ? `Latest ${rows.length} in this range, newest first` : "Nothing in this range";
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="7" class="uz-empty">No requests in this range.</td></tr>';
      return;
    }
    const more = $("[data-recent-more]");
    if (more) {
      more.hidden = rows.length <= RECENT_PREVIEW;
      more.textContent = recentAll ? "Show fewer" : `Show all ${rows.length}`;
      more.setAttribute("aria-expanded", recentAll ? "true" : "false");
    }
    body.innerHTML = (recentAll ? rows : rows.slice(0, RECENT_PREVIEW))
      .map((r) => {
        const cachedPct = r.prompt && r.cached ? Math.round((r.cached * 100) / r.prompt) : 0;
        const cost = r.cost ? money(r.cost) : r.cost_known ? "$0" : '<span class="mute">—</span>';
        const time = when(r.at, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
        return `<tr>
          <td class="uz-cell-when"><span title="${esc(r.at)}">${esc(time)}</span><span class="uz-via"> · ${esc(r.via)}</span></td>
          <td class="uz-cell-model"><div class="uz-name"><div><strong title="${esc(r.model)}">${esc(r.model)}</strong><small>${esc(r.provider_label)}</small></div></div></td>
          <td class="uz-cell-src">${r.via_kind === "chat" ? '<span class="mute">Fuse chat</span>' : esc(r.via)}</td>
          <td class="num uz-cell-in">${intFmt.format(r.prompt)}<span class="uz-cell-sub">${cachedPct ? cachedPct + "% cached" : "&nbsp;"}</span></td>
          <td class="num uz-cell-out">${intFmt.format(r.output)}<span class="uz-cell-sub">${r.reasoning ? compact(r.reasoning) + " reasoning" : "&nbsp;"}</span></td>
          <td class="num uz-cell-cost">${cost}</td>
          <td class="uz-cell-status"><span class="uz-status ${esc(r.status)}">${esc(r.status)}</span></td>
          <td class="uz-cell-io">${compact(r.prompt)} in${cachedPct ? ` (${cachedPct}% cached)` : ""} · ${compact(r.output)} out · ${cost}</td>
        </tr>`;
      })
      .join("");
  }

  /* —— Users (admin, all-users scope) —— */
  function renderUsers() {
    const panel = $("[data-user-comparison]");
    if (!panel) return;
    panel.hidden = data.scope !== "all";
    if (panel.hidden) return;
    const el = $("[data-users]");
    if (!data.users.length) {
      el.innerHTML = '<p class="uz-empty">No account usage in this range.</p>';
      return;
    }
    const ids = data.providers.map((p) => p.id);
    // Providers past the six series slots share the "Other" swatch.
    const colorOf = (id) => providerColor(id);
    ids.forEach(colorOf);
    const named = data.providers.filter((p) => providerSlots.get(p.id));
    const hasOther = named.length < data.providers.length;
    const top = data.users[0].tokens || 1;
    el.innerHTML =
      data.users
        .map((u) => {
          const segs = ids
            .map((id) => {
              const v = u.providers[id] || 0;
              return v ? `<span style="flex:${v} 1 0;background:${providerColor(id)}" title="${esc(id)} ${intFmt.format(v)} tokens"></span>` : "";
            })
            .join("");
          return `<div class="uz-user"><div class="uz-name"><div><strong title="${esc(u.email)}">${esc(u.email)}</strong><small>${intFmt.format(u.requests)} requests</small></div></div><div class="uz-stack" style="width:${Math.max(2, (u.tokens * 100) / top).toFixed(1)}%">${segs}</div><div class="uz-bar-vals">${compact(u.tokens)}<small>${money(u.cost)}</small></div></div>`;
        })
        .join("") +
      `<div class="uz-users-legend">${named.map((p) => `<span><i class="uz-dot" style="background:${colorOf(p.id)}"></i>${esc(p.name)}</span>`).join("")}${hasOther ? `<span><i class="uz-dot" style="background:${cssVar("--uz-other")}"></i>Other</span>` : ""}</div>`;
  }

  function paint() {
    // Seed colour slots in chart order so the top model always gets slot 1
    // the first time it appears.
    data.series_models.forEach((s) => modelColor(s.id));
    renderCaption();
    renderProviderOptions();
    renderKpis();
    plot.render(true);
    renderAnatomy();
    renderModels();
    renderBreakdowns();
    renderHeat();
    renderReliability();
    renderRecent();
    renderUsers();
  }

  function fail() {
    const msg = "Usage data could not be loaded. Try again shortly.";
    $("[data-plot]").innerHTML = `<p class="uz-empty">${msg}</p>`;
    root.querySelectorAll("[data-kpi-value]").forEach((el) => (el.textContent = "—"));
    root.querySelectorAll("[data-kpi-delta]").forEach((el) => (el.textContent = "Load failed"));
    ["[data-providers]", "[data-sources]", "[data-heat]", "[data-reliability]"].forEach((s) => ($(s).innerHTML = `<p class="uz-empty">${msg}</p>`));
    $("[data-models]").innerHTML = `<tr><td colspan="9" class="uz-empty">${msg}</td></tr>`;
    $("[data-recent]").innerHTML = `<tr><td colspan="7" class="uz-empty">${msg}</td></tr>`;
  }

  async function load() {
    syncControls();
    syncUrl();
    if (controller) controller.abort();
    controller = new AbortController();
    const q = new URLSearchParams({ range: state.range });
    if (state.provider) q.set("provider", state.provider);
    if (state.scope === "all") q.set("scope", "all");
    const plotEl = $("[data-plot]");
    plotEl.setAttribute("aria-busy", "true");
    try {
      const r = await fetch("/usage.json?" + q, { signal: controller.signal });
      if (r.status === 401) {
        location.assign("/login");
        return;
      }
      if (!r.ok) throw new Error("usage");
      data = await r.json();
      paint();
    } catch (err) {
      if (err.name !== "AbortError") fail();
    } finally {
      plotEl.removeAttribute("aria-busy");
    }
  }

  root.querySelectorAll("time[data-ago]").forEach((el) => {
    const text = ago(el.getAttribute("datetime"));
    if (text) el.textContent = "used " + text;
  });

  $("[data-recent-more]")?.addEventListener("click", () => {
    recentAll = !recentAll;
    if (data) renderRecent();
  });

  if (typeof ResizeObserver !== "undefined") {
    let t = 0;
    new ResizeObserver(() => {
      if (!data) return;
      clearTimeout(t);
      t = setTimeout(() => plot.render(false), 100);
    }).observe($("[data-plot]"));
  }
  load();
})();
