function csrfToken() {
  const el = document.querySelector('meta[name="csrf-token"]');
  return (el && el.getAttribute("content")) || "";
}

window.fuseConfirm = function fuseConfirm(message, opts) {
  const options = opts && typeof opts === "object" ? opts : {};
  const dialog = document.querySelector("dialog.modal[data-confirm]");
  const text = dialog && dialog.querySelector("[data-confirm-text]");
  const yes = dialog && dialog.querySelector("[data-confirm-yes]");
  const no = dialog && dialog.querySelector("[data-confirm-no]");
  if (!dialog || !text || !yes || !no || typeof dialog.showModal !== "function") {
    return Promise.resolve(window.confirm(message || "Are you sure?"));
  }
  if (dialog._fuseConfirmBusy) return Promise.resolve(false);
  dialog._fuseConfirmBusy = new Promise((resolve) => {
    const label = options.confirmLabel || "Confirm";
    const previous = yes.textContent;
    let settled = false;
    text.textContent = message || "Are you sure?";
    yes.textContent = label;
    yes.classList.toggle("danger", Boolean(options.danger));
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      yes.removeEventListener("click", onYes);
      no.removeEventListener("click", onNo);
      dialog.removeEventListener("cancel", onCancel);
      dialog.removeEventListener("click", onBackdrop);
      dialog.removeEventListener("close", onClose);
      yes.textContent = previous;
      yes.classList.remove("danger");
      dialog._fuseConfirmBusy = null;
      if (dialog.open) dialog.close();
      resolve(Boolean(ok));
    };
    const onYes = (event) => {
      event.preventDefault();
      finish(true);
    };
    const onNo = (event) => {
      event.preventDefault();
      finish(false);
    };
    const onCancel = (event) => {
      event.preventDefault();
      finish(false);
    };
    const onBackdrop = (event) => {
      if (event.target === dialog) finish(false);
    };
    const onClose = () => finish(false);
    yes.addEventListener("click", onYes);
    no.addEventListener("click", onNo);
    dialog.addEventListener("cancel", onCancel);
    dialog.addEventListener("click", onBackdrop);
    dialog.addEventListener("close", onClose, { once: true });
    if (!dialog.open) dialog.showModal();
    yes.focus();
  });
  return dialog._fuseConfirmBusy;
};
function fuseConfirm(message, opts) {
  return window.fuseConfirm(message, opts);
}

(function orModelPicker() {
  document.querySelectorAll("[data-or-list]").forEach((list) => {
    const form = list.closest("form");
    if (!form) return;
    const filter = form.querySelector("[data-or-filter]");
    const count = form.querySelector("[data-or-count]");
    const rows = Array.from(list.querySelectorAll("[data-or-row]"));

    function refresh() {
      const needle = (filter && filter.value ? filter.value : "").trim().toLowerCase();
      let shown = 0;
      rows.forEach((row) => {
        const text = row.textContent.toLowerCase();
        const hit = !needle || text.includes(needle);
        row.hidden = !hit;
        if (hit) shown += 1;
      });
      const checked = rows.filter((row) => row.querySelector("input[type=checkbox]").checked).length;
      if (count) count.textContent = `${shown} shown · ${checked} enabled`;
    }

    if (filter) {
      filter.addEventListener("input", refresh);
      filter.addEventListener("keydown", (event) => {
        if (event.key === "Enter") event.preventDefault();
      });
    }
    list.addEventListener("change", refresh);
    refresh();
  });
})();

(function accountMenu() {
  document.querySelectorAll("[data-account]").forEach((root) => {
    const toggle = root.querySelector("[data-account-toggle]");
    const menu = root.querySelector("[data-account-menu]");
    if (!toggle || !menu) return;

    const closer = root.querySelector("[data-account-close]");
    const mobile = window.matchMedia("(max-width: 800px)");
    let accountPushed = false;
    function setOpen(open) {
      const next = Boolean(open);
      const was = !menu.hidden;
      root.classList.toggle("is-open", next);
      menu.hidden = !next;
      toggle.setAttribute("aria-expanded", next ? "true" : "false");
      if (next && !was && mobile.matches) {
        accountPushed = true;
        history.pushState({ fuseAccount: true }, "");
      } else if (!next && was && accountPushed) {
        accountPushed = false;
        if (history.state && history.state.fuseAccount) history.replaceState({}, "");
      } else if (!next) {
        accountPushed = false;
      }
    }

    toggle.addEventListener("click", (event) => {
      event.stopPropagation();
      const next = menu.hidden;
      document.querySelectorAll("[data-account]").forEach((other) => {
        if (other !== root) {
          other.classList.remove("is-open");
          const otherMenu = other.querySelector("[data-account-menu]");
          const otherToggle = other.querySelector("[data-account-toggle]");
          if (otherMenu) otherMenu.hidden = true;
          if (otherToggle) otherToggle.setAttribute("aria-expanded", "false");
        }
      });
      setOpen(next);
    });
    closer?.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
    });
    document.addEventListener("click", (event) => {
      if (!root.contains(event.target)) setOpen(false);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") setOpen(false);
    });
    window.addEventListener("popstate", () => {
      if (accountPushed && !menu.hidden) {
        accountPushed = false;
        root.classList.remove("is-open");
        menu.hidden = true;
        toggle.setAttribute("aria-expanded", "false");
      }
    });
  });
})();

(function settingsRail() {
  const shell = document.querySelector("[data-settings]");
  const rail = document.querySelector("[data-app-rail]");
  const toggles = Array.from(document.querySelectorAll("[data-rail-toggle]"));
  const scrim = document.querySelector("[data-nav-scrim]");
  if (!shell || !rail || !toggles.length) return;

  const mobile = window.matchMedia("(max-width: 800px)");
  const STORE_WIDTH = "fuse.settings.railWidth";
  const WIDTH = { min: 14 * 16, max: 30 * 16, default: 19 * 16 };
  const background = shell.querySelectorAll(".settings-bar > :not([data-rail-toggle]), .settings-scroll");
  let railPushed = false;

  function clampWidth(px) {
    const deskMax = Math.min(WIDTH.max, Math.max(WIDTH.min, window.innerWidth - 340));
    return Math.round(Math.min(deskMax, Math.max(WIDTH.min, px)));
  }
  function applyWidth(px, persist) {
    shell.style.setProperty("--chat-history", px + "px");
    if (persist) {
      try { localStorage.setItem(STORE_WIDTH, String(px)); } catch (_) {}
    }
  }
  function currentWidth() {
    const raw = Number.parseFloat(shell.style.getPropertyValue("--chat-history"));
    return Number.isFinite(raw) ? raw : WIDTH.default;
  }

  function sync() {
    const drawer = mobile.matches && shell.classList.contains("is-history");
    const expanded = mobile.matches ? drawer : !shell.classList.contains("history-collapsed");
    toggles.forEach((btn) => {
      btn.setAttribute("aria-expanded", expanded ? "true" : "false");
      const label = btn.querySelector(".visually-hidden");
      if (label) label.textContent = expanded ? "Close sidebar" : "Open sidebar";
    });
    if (scrim) scrim.hidden = !drawer;
    document.body.classList.toggle("is-nav", drawer);
    background.forEach((el) => { el.inert = drawer; });
    rail.inert = !expanded;
  }

  function setDrawer(open) {
    const next = Boolean(open);
    const was = shell.classList.contains("is-history");
    shell.classList.toggle("is-history", next);
    document.body.classList.toggle("is-nav", next && mobile.matches);
    if (next && !was && mobile.matches) {
      railPushed = true;
      history.pushState({ fuseSettingsRail: true }, "");
    } else if (!next && was && railPushed) {
      railPushed = false;
      if (history.state && history.state.fuseSettingsRail) history.replaceState({}, "");
    } else if (!next) {
      railPushed = false;
    }
    sync();
  }

  toggles.forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.stopPropagation();
      if (mobile.matches) setDrawer(!shell.classList.contains("is-history"));
      else {
        shell.classList.toggle("history-collapsed");
        sync();
      }
    });
  });
  if (scrim) scrim.addEventListener("click", () => setDrawer(false));
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (mobile.matches && shell.classList.contains("is-history")) {
      event.stopPropagation();
      setDrawer(false);
    }
  });
  mobile.addEventListener("change", () => {
    setDrawer(false);
    shell.classList.remove("history-collapsed");
    sync();
  });
  window.addEventListener("popstate", () => {
    if (railPushed && shell.classList.contains("is-history")) {
      railPushed = false;
      shell.classList.remove("is-history");
      document.body.classList.remove("is-nav");
      sync();
    }
  });

  const resizeHandle = shell.querySelector("[data-rail-resize]");
  if (resizeHandle) {
    let savedWidth = 0;
    try { savedWidth = Number(localStorage.getItem(STORE_WIDTH)) || 0; } catch (_) {}
    if (savedWidth >= WIDTH.min && savedWidth <= WIDTH.max) applyWidth(savedWidth, false);
    let resizeRaf = 0;
    resizeHandle.addEventListener("pointerdown", (event) => {
      if (window.innerWidth <= 800) return;
      event.preventDefault();
      try { resizeHandle.setPointerCapture(event.pointerId); } catch (_) {}
      resizeHandle.classList.add("is-dragging");
      document.body.classList.add("is-resizing-history");
      const onMove = (moveEvent) => {
        const rect = shell.getBoundingClientRect();
        cancelAnimationFrame(resizeRaf);
        resizeRaf = requestAnimationFrame(() => {
          applyWidth(clampWidth(moveEvent.clientX - rect.left), false);
        });
      };
      const onUp = () => {
        resizeHandle.classList.remove("is-dragging");
        document.body.classList.remove("is-resizing-history");
        resizeHandle.removeEventListener("pointermove", onMove);
        resizeHandle.removeEventListener("pointerup", onUp);
        resizeHandle.removeEventListener("pointercancel", onUp);
        applyWidth(clampWidth(currentWidth()), true);
      };
      resizeHandle.addEventListener("pointermove", onMove);
      resizeHandle.addEventListener("pointerup", onUp);
      resizeHandle.addEventListener("pointercancel", onUp);
    });
    resizeHandle.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      const step = event.shiftKey ? 32 : 16;
      applyWidth(clampWidth(currentWidth() + (event.key === "ArrowRight" ? step : -step)), true);
    });
    resizeHandle.addEventListener("dblclick", () => applyWidth(WIDTH.default, true));
  }

  sync();
})();

document.querySelectorAll(".copy").forEach((el) => {
  el.addEventListener("click", async () => {
    const t = el.getAttribute("data-copy") || el.textContent;
    const originalTitle = el.getAttribute("title") || "";
    try {
      await navigator.clipboard.writeText(t.trim());
      el.classList.add("ok");
      setTimeout(() => el.classList.remove("ok"), 800);
    } catch (_) {
      el.classList.add("err");
      el.setAttribute("title", "Clipboard copy failed. Select and copy the text manually.");
      el.setAttribute("aria-label", "Clipboard copy failed");
      const label = el.querySelector(".catalog-copy");
      if (label) label.textContent = "Copy failed";
      const failure = document.createElement("span");
      failure.className = "err";
      failure.textContent = " Copy failed";
      if (!label) el.appendChild(failure);
      setTimeout(() => {
        el.classList.remove("err");
        if (originalTitle) el.setAttribute("title", originalTitle);
        else el.removeAttribute("title");
        el.removeAttribute("aria-label");
        if (label) label.textContent = "Copy";
        failure.remove();
      }, 2200);
    }
  });
});

const docsJump = document.querySelector("[data-docs-jump]");
if (docsJump) {
  const links = [...docsJump.querySelectorAll("a[href^='#']")];
  const mark = (id) => {
    links.forEach((link) => link.classList.toggle("on", link.getAttribute("href") === "#" + id));
  };
  links.forEach((link) => {
    link.addEventListener("click", () => mark((link.getAttribute("href") || "").slice(1)));
  });
  const sections = links
    .map((link) => document.getElementById((link.getAttribute("href") || "").slice(1)))
    .filter(Boolean);
  if (sections.length && "IntersectionObserver" in window) {
    const io = new IntersectionObserver(
      (entries) => {
        const vis = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (!vis) return;
        mark(vis.target.id);
      },
      { rootMargin: "-20% 0px -65% 0px", threshold: [0, 0.15, 0.4] }
    );
    sections.forEach((section) => io.observe(section));
  }
}

document.querySelectorAll("[data-page-tabs]").forEach((nav) => {
  const root = nav.closest("[data-tab-root]") || document;
  nav.querySelectorAll("[data-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      const tab = button.dataset.tab;
      nav.querySelectorAll("[data-tab]").forEach((item) => {
        const on = item === button;
        item.classList.toggle("on", on);
        item.setAttribute("aria-selected", on ? "true" : "false");
      });
      root.querySelectorAll("[data-tab-panel]").forEach((panel) => {
        panel.classList.toggle("hidden", panel.getAttribute("data-tab-panel") !== tab);
      });
    });
  });
});

const artifactVersion = document.querySelector("[data-artifact-page] [data-artifact-version]");
if (artifactVersion) {
  artifactVersion.addEventListener("change", () => {
    const url = new URL(window.location.href);
    url.searchParams.set("v", artifactVersion.value);
    window.location.assign(url.toString());
  });
}

document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (form.dataset.confirmed === "1") {
      delete form.dataset.confirmed;
      return;
    }
    event.preventDefault();
    const submitter = form.querySelector('button[type="submit"], button:not([type]), input[type="submit"]');
    const confirmLabel = ((submitter && (submitter.textContent || submitter.value)) || "Confirm").replace(/\s+/g, " ").trim() || "Confirm";
    const danger = Boolean(form.querySelector("button.danger, .btn.danger"));
    fuseConfirm(form.getAttribute("data-confirm") || "Are you sure?", {
      confirmLabel,
      danger,
    }).then((ok) => {
      if (!ok) return;
      form.dataset.confirmed = "1";
      if (typeof form.requestSubmit === "function") form.requestSubmit();
      else form.submit();
    });
  });
});

document.querySelectorAll("[data-dialog-open]").forEach((button) => {
  button.addEventListener("click", () => {
    const dialog = document.getElementById(button.getAttribute("data-dialog-open"));
    if (dialog && !dialog.open) dialog.showModal();
  });
});

document.querySelectorAll("dialog.modal").forEach((dialog) => {
  if (dialog.hasAttribute("data-confirm") && !dialog.id) return;
  dialog.querySelectorAll("[data-dialog-close]").forEach((button) => {
    button.addEventListener("click", () => dialog.close());
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  if (dialog.hasAttribute("data-auto-open")) dialog.showModal();
});

function syncSourceForm() {
  const type = document.querySelector("#source-type");
  if (!type) return;
  const v = type.value;
  document.querySelectorAll(".f-url").forEach((n) => n.classList.remove("hidden"));
  document.querySelectorAll(".f-llama").forEach((n) => n.classList.toggle("hidden", v !== "llama"));
  document.querySelectorAll(".f-keys").forEach((n) => n.classList.toggle("hidden", v === "llama"));
  document.querySelectorAll(".f-rot").forEach((n) => n.classList.toggle("hidden", v !== "openai"));
  document.querySelectorAll(".f-model").forEach((n) => n.classList.toggle("hidden", v !== "ollama"));
}
const typeSel = document.querySelector("#source-type");
if (typeSel) {
  typeSel.addEventListener("change", syncSourceForm);
  syncSourceForm();
}

async function pollDevice(endpoint, interval, redirect) {
  const panel = document.querySelector(`[data-device-poll][data-endpoint="${endpoint}"]`);
  const status = panel ? panel.querySelector(".poll-status") : null;
  let wait = Math.max(3, Number(interval) || 5) * 1000;
  async function tick() {
    try {
      const r = await fetch(endpoint, {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken() },
      });
      const data = await r.json();
      if (r.status === 401) {
        window.location.assign("/login");
        return;
      }
      if (data.ok) {
        window.location.href = redirect;
        return;
      }
      if (data.pending) {
        if (data.retry_after) wait = Math.max(3, Number(data.retry_after) || 5) * 1000;
        if (status) status.textContent = data.error || "Waiting for confirmation…";
        setTimeout(tick, wait);
        return;
      }
      if (status) status.textContent = data.error || "Sign-in failed.";
    } catch (e) {
      if (status) status.textContent = "Could not reach the server.";
      setTimeout(tick, wait);
    }
  }
  setTimeout(tick, wait);
}

document.querySelectorAll("[data-device-poll]").forEach((device) => {
  pollDevice(
    device.getAttribute("data-endpoint"),
    device.getAttribute("data-interval"),
    device.getAttribute("data-redirect") || window.location.pathname
  );
});

function escapeText(s) {
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function probeBox(id) {
  const el = document.getElementById("probe-" + id);
  if (!el) return null;
  el.classList.remove("hidden");
  if (el.tagName === "TR") return el.querySelector("td") || el;
  return el;
}

function renderProbe(el, data) {
  if (!el) return;
  if (data.error && !(data.checks && data.checks.length)) {
    el.innerHTML = `<p class="callout fault" role="alert">${escapeText(data.error)}</p>`;
    return;
  }
  const items = (data.checks || [])
    .map((c) => {
      const kind = c.skip ? "skip" : c.ok ? "ok" : "bad";
      const mark = c.skip ? "skip" : c.ok ? "ok" : "fail";
      return `<li><span class="mark ${kind}">${mark}</span><span><b>${escapeText(c.name)}</b> <span class="detail">${escapeText(c.detail || "")}</span></span></li>`;
    })
    .join("");
  const ms = data.ms ? ` · ${data.ms}ms` : "";
  const head = data.ok
    ? `<p class="callout ok">Passed${ms}</p>`
    : `<p class="callout fault">Failed${ms}</p>`;
  el.innerHTML = `${head}<ul class="checks">${items}</ul>`;
}

document.querySelectorAll(".probe").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const id = btn.getAttribute("data-source");
    if (!id) return;
    const out = probeBox(id);
    btn.disabled = true;
    const label = btn.textContent;
    btn.textContent = "Testing…";
    if (out) out.innerHTML = `<p class="pulse">Sending a short prompt and checking the wire…</p>`;
    try {
      const r = await fetch("/admin/sources/" + encodeURIComponent(id) + "/test", {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken() },
      });
      const data = await r.json();
      renderProbe(out, data);
    } catch (_) {
      renderProbe(out, { ok: false, error: "Could not reach the server.", checks: [] });
    } finally {
      btn.disabled = false;
      btn.textContent = label;
    }
  });
});

const usageDashboard = document.querySelector("[data-usage-dashboard]");
if (usageDashboard) {
  const numberFmt = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
  const integerFmt = new Intl.NumberFormat();
  const PALETTE = ["#C5E85A", "#6EE7B7", "#E2C36B", "#7EB6D6", "#D4A0B8", "#8FCF8A", "#8A9188"];
  const OTHER_COLOR = "#8A9188";
  const MIX_COLORS = { uncached: "#C5E85A", cached: "#6EE7B7", visible: "#7EB6D6", reasoning: "#D4A0B8" };
  const SPARK_COLOR = { tokens: "#C5E85A", requests: "#6EE7B7", cost: "#E2C36B", cache: "#D4A0B8", blended: "#8FCF8A" };
  const colorMap = new Map();
  const provider = document.querySelector("[data-usage-provider]");
  const scope = document.querySelector("[data-usage-scope]");
  const rangeRoot = document.querySelector("[data-usage-range]");
  let selectedRange = "24h";
  let chartMode = "tokens";
  let activeTab = "overview";
  let lastData = null;
  let controller = null;

  function colorFor(id) {
    const key = String(id || "other");
    if (key === "other") return OTHER_COLOR;
    if (!colorMap.has(key)) {
      const used = new Set(colorMap.values());
      const next = PALETTE.find((c) => c !== OTHER_COLOR && !used.has(c)) || PALETTE[colorMap.size % PALETTE.length];
      colorMap.set(key, next);
    }
    return colorMap.get(key);
  }

  function compact(value) {
    return numberFmt.format(Number(value) || 0);
  }

  function money(micros) {
    const dollars = (Number(micros) || 0) / 1000000;
    if (!dollars) return "$0.00";
    if (dollars >= 1) return "$" + dollars.toFixed(2);
    if (dollars >= 0.01) return "$" + dollars.toFixed(2);
    return "$" + dollars.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
  }

  function blendedLabel(value) {
    if (value === null || value === undefined) return "—";
    const n = Number(value);
    if (!Number.isFinite(n)) return "—";
    return n >= 1 ? "$" + n.toFixed(2) : "$" + n.toFixed(n >= 0.1 ? 2 : 3);
  }

  function moneyAxis(dollars) {
    if (dollars >= 1000) return "$" + numberFmt.format(dollars);
    if (dollars >= 1) return "$" + dollars.toFixed(dollars >= 100 ? 0 : 1);
    if (dollars > 0) return "$" + dollars.toFixed(3);
    return "$0";
  }

  function reduceMotion() {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function modeValue(point, modelId, mode) {
    if (mode === "cost") return Number((point.cost_segments || {})[modelId]) || 0;
    return Number((point.segments || {})[modelId]) || 0;
  }

  function modeTotal(point, mode) {
    return mode === "cost" ? Number(point.cost) || 0 : Number(point.tokens) || 0;
  }

  function modeText(value, mode) {
    return mode === "cost" ? money(value) : integerFmt.format(value);
  }

  function dateLabel(value, range) {
    const date = new Date(value);
    if (range === "30m" || range === "6h" || range === "24h") return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    if (range === "3d") return date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric" });
    return date.toLocaleDateString([], { month: "short", day: "numeric" });
  }

  function recentWhen(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value || "";
    return date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  }

  function sparkSVG(values, color) {
    const w = 96;
    const h = 36;
    const p = 2;
    const nums = (values || []).map((v) => Number(v) || 0);
    if (nums.length < 2) return "";
    const max = Math.max(...nums, 0);
    const min = Math.min(...nums, 0);
    if (max <= 0 && min >= 0) {
      return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true"><path d="M${p} ${h - p} H${w - p}" fill="none" stroke="${color}" stroke-width="1.4" opacity="0.35"/></svg>`;
    }
    const span = max - min || 1;
    const pts = nums.map((v, i) => {
      const x = p + (i * (w - p * 2)) / (nums.length - 1);
      const y = h - p - ((v - min) / span) * (h - p * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true"><polyline points="${pts.join(" ")}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
  }

  function seriesValues(data, key) {
    return (data.series || []).map((point) => {
      if (key === "tokens") return Number(point.tokens) || 0;
      if (key === "requests") return Number(point.requests) || 0;
      if (key === "cost") return Number(point.cost) || 0;
      if (key === "cache") return point.prompt ? (Number(point.cached) || 0) / point.prompt : 0;
      if (key === "blended") return point.tokens ? (Number(point.cost) || 0) / point.tokens : 0;
      return 0;
    });
  }

  function modelSpark(data, modelId) {
    return (data.series || []).map((point) => Number((point.segments || {})[modelId]) || 0);
  }

  function deltaHTML(value, emptyCopy, extra, unit) {
    if (emptyCopy) return `<span class="delta-fig">${escapeText(emptyCopy)}</span>`;
    if (value === null || value === undefined) {
      return `<span class="delta-fig">New in this period</span>`;
    }
    const n = Number(value) || 0;
    const arrow = n > 0 ? "↑" : n < 0 ? "↓" : "→";
    return `<span class="delta-fig">${arrow} ${Math.abs(n).toFixed(1)}${unit || "%"}</span><span class="metric-vs">${escapeText(extra || "vs previous")}</span>`;
  }

  function subtitleText(data, mode) {
    const scopeText = data.scope === "all" ? "all users" : "this account";
    const providerName = data.provider
      ? (data.provider_options || []).find((item) => item.id === data.provider)?.name || data.provider
      : "";
    const providerText = providerName ? " · " + providerName : "";
    const ratesText = mode === "cost" && !data.summary.pricing_coverage ? " · no rates for these models" : "";
    return `${data.range.bucket} buckets · ${scopeText}${providerText}${ratesText}`;
  }

  function setMetrics(data) {
    const summary = data.summary;
    const noRates = !summary.pricing_coverage && !summary.cost;
    const values = {
      tokens: compact(summary.tokens),
      requests: compact(summary.requests),
      cost_label: noRates ? "—" : summary.cost_label,
      cache_hit: summary.cache_hit === null || summary.cache_hit === undefined ? "—" : `${summary.cache_hit}%`,
      blended: noRates ? "—" : blendedLabel(summary.blended),
    };
    Object.entries(values).forEach(([key, value]) => {
      const el = usageDashboard.querySelector(`[data-metric="${key}"]`);
      if (el) el.textContent = value;
    });
    const empty = Boolean(summary.empty);
    const deltas = [
      ["token_change", empty ? "No traffic in this range" : null, "vs previous"],
      ["request_change", empty ? "No traffic in this range" : null, "vs previous"],
      ["cost_change", empty ? "No traffic in this range" : noRates ? "No rates for used models" : null, "vs previous"],
      ["cache_hit_change", empty ? "No prompt tokens" : summary.cache_hit === null ? "no cached usage yet" : null, "vs previous", " pts"],
      ["blended_change", empty ? "No traffic in this range" : noRates ? "No rates for used models" : null, "vs previous"],
    ];
    deltas.forEach(([key, copy, extra, unit]) => {
      const el = usageDashboard.querySelector(`[data-delta="${key}"]`);
      if (!el) return;
      const value = summary[key];
      el.innerHTML = copy ? deltaHTML(null, copy) : deltaHTML(value, null, extra, unit);
      el.classList.toggle("up", !copy && Number(value) > 0);
      el.classList.toggle("down", !copy && Number(value) < 0);
    });
    Object.keys(SPARK_COLOR).forEach((key) => {
      const el = usageDashboard.querySelector(`[data-spark="${key}"]`);
      if (el) el.innerHTML = sparkSVG(seriesValues(data, key), SPARK_COLOR[key]);
    });
    const sub = usageDashboard.querySelector("[data-chart-subtitle]");
    if (sub) sub.textContent = subtitleText(data, chartMode);
    const chartTitle = usageDashboard.querySelector("[data-chart-title]");
    if (chartTitle) chartTitle.textContent = chartMode === "cost" ? "Cost by model" : "Tokens by model";
    const trendsSub = usageDashboard.querySelector("[data-trends-subtitle]");
    if (trendsSub) trendsSub.textContent = subtitleText(data, chartMode);
    const trendsTitle = usageDashboard.querySelector("[data-trends-title]");
    if (trendsTitle) trendsTitle.textContent = chartMode === "cost" ? "Spend over time" : "Usage over time";
  }

  function syncModeButtons() {
    document.querySelectorAll("[data-chart-mode] button, [data-trends-mode] button").forEach((item) => {
      const active = item.dataset.mode === chartMode;
      item.classList.toggle("on", active);
      item.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }

  function createPlot(chart, tip, legend) {
    let raf = 0;
    let lastCount = 0;
    let drawnW = 0;
    let touched = false;
    const fromHeights = new Map();
    if (chart) {
      chart.addEventListener(
        "touchstart",
        () => {
          touched = true;
        },
        { passive: true }
      );
      chart.addEventListener("click", (event) => {
        if (!touched || !tip) return;
        if (event.target.closest(".chart-column")) return;
        chart.classList.remove("is-hovering");
        chart.querySelectorAll(".chart-column.is-hot").forEach((el) => el.classList.remove("is-hot"));
        tip.classList.add("hidden");
      });
    }

    function availableWidth() {
      return Math.floor(chart.clientWidth) || 0;
    }

    function placeTip(column, data, mode, event) {
      if (!tip) return;
      const point = data.series[Number(column.dataset.index)];
      if (!point) return;
      const total = modeTotal(point, mode);
      const lines = data.series_models
        .map((model) => ({ name: model.name, value: modeValue(point, model.id, mode), color: colorFor(model.id) }))
        .filter((item) => item.value)
        .map(
          (item) =>
            `<span class="tip-swatch" style="background:${item.color}"></span> ${escapeText(item.name)}: ${modeText(item.value, mode)} (${total ? ((item.value * 100) / total).toFixed(1) : "0.0"}%)`
        );
      const otherValue = modeValue(point, "other", mode);
      if (otherValue) lines.push(`<span class="tip-swatch" style="background:${OTHER_COLOR}"></span> Other: ${modeText(otherValue, mode)}`);
      const headline =
        mode === "cost"
          ? `${money(point.cost)} · ${integerFmt.format(point.tokens)} tokens`
          : `${integerFmt.format(point.tokens)} tokens · ${integerFmt.format(point.requests)} req`;
      tip.innerHTML = `<strong>${escapeText(dateLabel(point.at, data.range.id))}</strong><span class="tip-head">${headline}</span>${lines.length ? `<span class="tip-lines">${lines.join("<br>")}</span>` : ""}`;
      tip.classList.remove("hidden");
      const wrap = chart.parentElement.getBoundingClientRect();
      const left = event && event.clientX ? event.clientX - wrap.left + 12 : column.getBoundingClientRect().left - wrap.left + 16;
      const top = event && event.clientY ? event.clientY - wrap.top - tip.offsetHeight - 12 : 8;
      tip.style.left = Math.max(4, Math.min(left, wrap.width - tip.offsetWidth - 4)) + "px";
      tip.style.top = Math.max(4, top) + "px";
    }

    function render(data, mode, opts) {
      const animate = !opts || opts.animate !== false;
      const available = availableWidth();
      if (available < 80) return;
      if (!animate && Math.abs(available - drawnW) < 6 && lastCount) return;
      drawnW = available;
      const points = data.series || [];
      const max = Math.max(...points.map((point) => modeTotal(point, mode)), 0);
      if (legend) {
        const hasOther = points.some((point) => modeValue(point, "other", mode));
        legend.innerHTML =
          data.series_models
            .map(
              (model) =>
                `<span class="legend-swatch"><i class="series-dot" style="background:${colorFor(model.id)}"></i>${escapeText(model.name)}<span class="legend-provider">${escapeText(model.provider)}</span></span>`
            )
            .join("") + (hasOther ? `<span class="legend-swatch"><i class="series-dot" style="background:${OTHER_COLOR}"></i>Other</span>` : "");
      }
      if (!points.length || !max) {
        lastCount = 0;
        fromHeights.clear();
        chart.classList.remove("is-wide");
        chart.innerHTML =
          mode === "cost" && points.some((point) => point.tokens)
            ? '<p class="empty chart-empty">No rates configured for these models yet.</p>'
            : '<p class="empty chart-empty">No recorded requests in this range.</p>';
        return;
      }
      const compactPlot = available < 640;
      const minStep = compactPlot ? 8 : data.range.id === "30d" ? 20 : 28;
      const width = Math.max(available, points.length * minStep);
      const height = Math.max(196, Math.round(chart.clientHeight) || (compactPlot ? 220 : 280));
      const margin = { top: 12, right: compactPlot ? 16 : 10, bottom: 30, left: compactPlot ? 40 : 54 };
      const plotWidth = Math.max(1, width - margin.left - margin.right);
      const plotHeight = height - margin.top - margin.bottom;
      const step = plotWidth / points.length;
      const barWidth = Math.max(compactPlot ? 3 : 5, Math.min(compactPlot ? 16 : 26, step * 0.58));
      const axisFmt = (v) => (mode === "cost" ? moneyAxis(v / 1000000) : compact(v));
      const needsScroll = width > available + 1;
      const wanted = compactPlot ? 4 : 7;
      const every = Math.max(1, Math.ceil(points.length / wanted));
      const ticks = [];
      for (let i = 0; i < points.length; i += every) ticks.push(i);
      const lastIndex = points.length - 1;
      if (ticks[ticks.length - 1] !== lastIndex) {
        if (lastIndex - ticks[ticks.length - 1] <= Math.max(1, Math.floor(every * 0.55))) ticks[ticks.length - 1] = lastIndex;
        else ticks.push(lastIndex);
      }
      const tickSet = new Set(ticks);
      const grow = lastCount !== points.length || reduceMotion() || !animate;
      const seriesIds = data.series_models.map((model) => model.id).concat(["other"]);
      let svg = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none"${needsScroll ? ` style="min-width:${width}px"` : ""} role="img" aria-label="${mode === "cost" ? "Estimated cost by model" : "Stacked token usage by model"}">`;
      for (let line = 0; line <= 4; line += 1) {
        const y = margin.top + plotHeight - (plotHeight * line) / 4;
        svg += `<line class="chart-grid" x1="${margin.left}" x2="${width - margin.right}" y1="${y}" y2="${y}"></line>`;
        svg += `<text class="chart-axis" x="${margin.left - 8}" y="${y + 3}" text-anchor="end">${escapeText(axisFmt((max * line) / 4))}</text>`;
      }
      ticks.forEach((index) => {
        const x = margin.left + index * step + step / 2;
        svg += `<line class="chart-grid chart-grid-v" x1="${x}" x2="${x}" y1="${margin.top}" y2="${margin.top + plotHeight}"></line>`;
      });
      const targets = [];
      points.forEach((point, index) => {
        const x = margin.left + index * step + (step - barWidth) / 2;
        let y = margin.top + plotHeight;
        svg += `<g class="chart-column" data-index="${index}" tabindex="0" role="graphics-symbol" aria-label="${escapeText(dateLabel(point.at, data.range.id))}, ${integerFmt.format(point.tokens)} tokens, ${money(point.cost)}">`;
        seriesIds.forEach((id) => {
          const value = modeValue(point, id, mode);
          if (!value) return;
          const h = Math.max(1, (value / max) * plotHeight);
          y -= h;
          const key = `${index}:${id}`;
          const prev = fromHeights.get(key);
          const startH = grow || prev === undefined ? 0 : prev.h;
          const startY = grow || prev === undefined ? margin.top + plotHeight : prev.y;
          svg += `<rect class="chart-bar" data-key="${escapeText(key)}" x="${x}" y="${startY}" width="${barWidth}" height="${startH}" fill="${colorFor(id)}"></rect>`;
          targets.push({ key, y, h });
        });
        svg += `<rect class="chart-hit" x="${margin.left + index * step}" y="${margin.top}" width="${step}" height="${plotHeight}"></rect></g>`;
        if (tickSet.has(index)) {
          const anchor = index === lastIndex ? "end" : index === 0 ? "start" : "middle";
          svg += `<text class="chart-axis" x="${x + barWidth / 2}" y="${height - 8}" text-anchor="${anchor}">${escapeText(dateLabel(point.at, data.range.id))}</text>`;
        }
      });
      chart.classList.toggle("is-wide", needsScroll);
      chart.innerHTML = svg + "</svg>";
      if (needsScroll) chart.scrollLeft = chart.scrollWidth;
      const rects = new Map();
      chart.querySelectorAll(".chart-bar").forEach((el) => rects.set(el.getAttribute("data-key"), el));
      const duration = grow ? 520 : 380;
      const start = performance.now();
      if (raf) cancelAnimationFrame(raf);
      const ease = (t) => 1 - Math.pow(1 - t, 3);
      const tick = (now) => {
        const p = reduceMotion() || !animate ? 1 : ease(Math.min(1, (now - start) / duration));
        targets.forEach((to) => {
          const el = rects.get(to.key);
          if (!el) return;
          const prev = fromHeights.get(to.key);
          const fromH = grow || prev === undefined ? 0 : prev.h;
          const fromY = grow || prev === undefined ? margin.top + plotHeight : prev.y;
          el.setAttribute("y", fromY + (to.y - fromY) * p);
          el.setAttribute("height", Math.max(0, fromH + (to.h - fromH) * p));
        });
        if (p < 1) raf = requestAnimationFrame(tick);
        else {
          fromHeights.clear();
          targets.forEach((to) => fromHeights.set(to.key, { y: to.y, h: to.h }));
        }
      };
      raf = requestAnimationFrame(tick);
      lastCount = points.length;
      const hideTip = () => {
        if (touched) return;
        chart.classList.remove("is-hovering");
        chart.querySelectorAll(".chart-column.is-hot").forEach((el) => el.classList.remove("is-hot"));
        if (tip) tip.classList.add("hidden");
      };
      chart.querySelectorAll(".chart-column").forEach((column) => {
        column.addEventListener("mousemove", (event) => {
          if (touched) return;
          chart.classList.add("is-hovering");
          chart.querySelectorAll(".chart-column.is-hot").forEach((el) => el.classList.remove("is-hot"));
          column.classList.add("is-hot");
          placeTip(column, data, mode, event);
        });
        column.addEventListener("mouseleave", hideTip);
        column.addEventListener("click", (event) => {
          chart.classList.add("is-hovering");
          chart.querySelectorAll(".chart-column.is-hot").forEach((el) => el.classList.remove("is-hot"));
          column.classList.add("is-hot");
          placeTip(column, data, mode, event);
        });
        column.addEventListener("focus", (event) => placeTip(column, data, mode, event));
        column.addEventListener("blur", () => tip && tip.classList.add("hidden"));
      });
    }

    return { render, availableWidth };
  }

  function renderRanking(target, items, kind) {
    if (!target) return;
    if (!items.length) {
      target.innerHTML = '<p class="empty">No usage in this range.</p>';
      return;
    }
    const total = items.reduce((sum, row) => sum + row.tokens, 0);
    target.innerHTML = items
      .map((item, index) => {
        const id = item.id || item.name;
        const share = total ? ((item.tokens * 100) / total).toFixed(1) : "0.0";
        let sub = `${share}% of tokens`;
        if (kind === "model") sub = `${item.provider} · ${compact(item.prompt)} in / ${compact(item.output || item.completion)} out`;
        if (kind === "key") sub = item.prefix ? `${item.prefix}…` : `${compact(item.requests)} req`;
        const cost = kind === "model" && !item.cost_known ? '<span class="unknown-cost">rate needed</span>' : money(item.cost);
        const metric = `${compact(item.tokens)} tok<span>${compact(item.requests)} req · ${cost}</span>`;
        return `<div class="rank-row"><span class="rank-index">${index + 1}</span><i class="rank-dot" style="background:${colorFor(kind === "provider" ? "provider:" + id : id)}"></i><div class="rank-name"><strong title="${escapeText(item.name)}">${escapeText(item.name)}</strong><span>${escapeText(sub)}</span></div><div class="rank-values">${metric}</div></div>`;
      })
      .join("");
  }

  function renderTrends(data) {
    const target = usageDashboard.querySelector("[data-model-trends]");
    if (!target) return;
    const items = (data.models || []).slice(0, 8);
    if (!items.length) {
      target.innerHTML = '<p class="empty">No usage in this range.</p>';
      return;
    }
    target.innerHTML = items
      .map((item) => {
        const spark = sparkSVG(modelSpark(data, item.id), colorFor(item.id));
        let delta = '<span class="trend-delta mute">—</span>';
        if (item.is_new) delta = '<span class="trend-delta up">New</span>';
        else if (item.token_change !== null && item.token_change !== undefined) {
          const n = Number(item.token_change) || 0;
          const cls = n > 0 ? "up" : n < 0 ? "down" : "mute";
          delta = `<span class="trend-delta ${cls}">${n > 0 ? "↑" : n < 0 ? "↓" : "→"} ${Math.abs(n).toFixed(0)}%</span>`;
        }
        return `<div class="trend-row"><i class="rank-dot" style="background:${colorFor(item.id)}"></i><div class="rank-name"><strong title="${escapeText(item.name)}">${escapeText(item.name)}</strong><span>${escapeText(item.provider)}</span></div><div class="trend-spark">${spark}</div>${delta}</div>`;
      })
      .join("");
  }

  function renderMix(data) {
    const el = usageDashboard.querySelector("[data-token-mix]");
    if (!el) return;
    const mix = (data.mix || []).filter((part) => part.tokens > 0);
    if (!mix.length) {
      el.innerHTML = "";
      return;
    }
    const bar = mix
      .map((part) => `<span style="width:${Math.max(part.share, 0.4)}%;background:${MIX_COLORS[part.id] || OTHER_COLOR}" title="${escapeText(part.name)}"></span>`)
      .join("");
    const key = mix
      .map((part) => `<span><i class="series-dot" style="background:${MIX_COLORS[part.id] || OTHER_COLOR}"></i>${escapeText(part.name)} ${part.share}%</span>`)
      .join("");
    el.innerHTML = `<div class="mix-bar">${bar}</div><div class="mix-legend">${key}</div>`;
  }

  function renderRecent(data) {
    const panel = usageDashboard.querySelector("[data-recent-panel]");
    const body = usageDashboard.querySelector("[data-recent-rows]");
    if (!panel || !body) return;
    const rows = data.recent || [];
    const table = panel.querySelector(".table-wrap");
    const emptyEl = panel.querySelector("[data-recent-empty]");
    const sub = panel.querySelector("[data-recent-sub]");
    if (sub) sub.textContent = rows.length ? `latest ${rows.length} in this range` : "no requests in this range";
    if (table) table.classList.toggle("hidden", !rows.length);
    if (emptyEl) emptyEl.classList.toggle("hidden", Boolean(rows.length));
    body.innerHTML = rows
      .map((row) => {
        const ok = row.status === "success";
        const status = `<span class="badge ${ok ? "on" : "held"}">${escapeText(row.status)}</span>`;
        return `<tr><td data-label="When" class="nows">${escapeText(recentWhen(row.at))}</td><td data-label="Model" class="mono wrap">${escapeText(row.model)}</td><td data-label="Provider">${escapeText(row.provider_label)}</td><td data-label="Input" class="mono num tight">${integerFmt.format(row.prompt)}</td><td data-label="Output" class="mono num tight">${integerFmt.format(row.output)}</td><td data-label="Cost" class="mono num tight">${row.cost ? money(row.cost) : "—"}</td><td data-label="Status" class="tight">${status}</td></tr>`;
      })
      .join("");
  }

  function renderUsers(data) {
    const panel = usageDashboard.querySelector("[data-user-comparison]");
    if (!panel) return;
    const all = data.scope === "all";
    panel.classList.toggle("hidden", !all);
    if (!all) return;
    const target = panel.querySelector("[data-user-bars]");
    if (!data.users.length) {
      target.innerHTML = '<p class="empty">No user usage in this range.</p>';
      return;
    }
    const providerIds = data.providers.map((item) => item.id);
    target.innerHTML = data.users
      .map((user) => {
        const segments = providerIds
          .map((id) => {
            const value = user.providers[id] || 0;
            const pct = user.tokens ? (value * 100) / user.tokens : 0;
            return value ? `<span style="width:${pct}%;background:${colorFor("provider:" + id)}" title="${escapeText(id)}: ${integerFmt.format(value)} tokens"></span>` : "";
          })
          .join("");
        return `<div class="user-bar-row"><div class="user-identity"><strong title="${escapeText(user.email)}">${escapeText(user.email)}</strong><span>${compact(user.requests)} requests</span></div><div class="user-stack">${segments}</div><div class="user-total">${compact(user.tokens)} tok · ${money(user.cost)}</div></div>`;
      })
      .join("");
  }

  function updateProviderOptions(data) {
    if (!provider) return;
    const current = provider.value;
    provider.innerHTML =
      '<option value="">All providers</option>' +
      data.provider_options.map((item) => `<option value="${escapeText(item.id)}">${escapeText(item.name)}</option>`).join("");
    provider.value = current;
  }

  const overviewPlot = createPlot(
    usageDashboard.querySelector("[data-stacked-chart]"),
    usageDashboard.querySelector("[data-chart-tip]"),
    usageDashboard.querySelector("[data-chart-legend]")
  );
  const trendsPlot = createPlot(
    usageDashboard.querySelector("[data-trends-chart]"),
    usageDashboard.querySelector("[data-trends-tip]"),
    usageDashboard.querySelector("[data-trends-legend]")
  );

  function paint(data, { animate } = { animate: true }) {
    setMetrics(data);
    overviewPlot.render(data, chartMode, { animate });
    if (activeTab === "trends") trendsPlot.render(data, chartMode, { animate });
    renderRanking(usageDashboard.querySelector("[data-model-ranking]"), data.models, "model");
    renderRanking(usageDashboard.querySelector("[data-provider-ranking]"), data.providers, "provider");
    renderRanking(usageDashboard.querySelector("[data-key-ranking]"), data.keys || [], "key");
    renderTrends(data);
    renderMix(data);
    renderRecent(data);
    renderUsers(data);
    updateProviderOptions(data);
  }

  async function loadUsage() {
    if (controller) controller.abort();
    controller = new AbortController();
    const chart = usageDashboard.querySelector("[data-stacked-chart]");
    chart.setAttribute("aria-busy", "true");
    const params = new URLSearchParams({ range: selectedRange });
    if (provider && provider.value) params.set("provider", provider.value);
    if (scope && scope.value === "all") params.set("scope", "all");
    try {
      const response = await fetch("/usage.json?" + params.toString(), { signal: controller.signal });
      if (response.status === 401) {
        window.location.assign("/login");
        return;
      }
      if (!response.ok) throw new Error("usage");
      const data = await response.json();
      lastData = data;
      paint(data, { animate: true });
    } catch (error) {
      if (error.name !== "AbortError") {
        const message = '<p class="empty chart-empty">Usage history could not be loaded. Try again shortly.</p>';
        usageDashboard.querySelectorAll("[data-stacked-chart], [data-trends-chart]").forEach((el) => {
          el.innerHTML = message;
          el.removeAttribute("aria-busy");
        });
        usageDashboard.querySelectorAll("[data-model-ranking], [data-provider-ranking], [data-key-ranking], [data-model-trends]").forEach((el) => {
          el.innerHTML = '<p class="empty">Usage data could not be loaded.</p>';
        });
        usageDashboard.querySelectorAll("[data-chart-subtitle], [data-trends-subtitle]").forEach((el) => {
          el.textContent = "Usage data could not be loaded.";
        });
        usageDashboard.querySelectorAll("[data-metric]").forEach((el) => {
          el.textContent = "—";
        });
        usageDashboard.querySelectorAll("[data-delta]").forEach((el) => {
          el.textContent = "Load failed";
          el.classList.remove("up", "down");
        });
        usageDashboard.querySelectorAll("[data-spark], [data-token-mix]").forEach((el) => {
          el.innerHTML = "";
        });
        const recentPanel = usageDashboard.querySelector("[data-recent-panel]");
        if (recentPanel) {
          const recentTable = recentPanel.querySelector(".table-wrap");
          const recentEmpty = recentPanel.querySelector("[data-recent-empty]");
          const recentSub = recentPanel.querySelector("[data-recent-sub]");
          if (recentTable) recentTable.classList.add("hidden");
          if (recentEmpty) {
            recentEmpty.textContent = "Usage data could not be loaded.";
            recentEmpty.classList.remove("hidden");
          }
          if (recentSub) recentSub.textContent = "load failed";
        }
        const users = usageDashboard.querySelector("[data-user-bars]");
        if (users) users.innerHTML = '<p class="empty">Usage data could not be loaded.</p>';
      }
    } finally {
      chart.removeAttribute("aria-busy");
    }
  }

  if (rangeRoot) {
    rangeRoot.querySelectorAll("[data-range]").forEach((button) => {
      button.addEventListener("click", () => {
        selectedRange = button.dataset.range;
        rangeRoot.querySelectorAll("[data-range]").forEach((item) => {
          const active = item === button;
          item.classList.toggle("on", active);
          item.setAttribute("aria-pressed", active ? "true" : "false");
        });
        loadUsage();
      });
    });
  }
  document.querySelectorAll("[data-chart-mode] button, [data-trends-mode] button").forEach((button) => {
    button.addEventListener("click", () => {
      chartMode = button.dataset.mode;
      syncModeButtons();
      if (lastData) paint(lastData, { animate: true });
    });
  });
  usageDashboard.querySelectorAll("[data-usage-tabs] [data-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      activeTab = button.dataset.tab;
      usageDashboard.querySelectorAll("[data-usage-tabs] [data-tab]").forEach((item) => {
        const on = item === button;
        item.classList.toggle("on", on);
        item.setAttribute("aria-selected", on ? "true" : "false");
      });
      usageDashboard.querySelectorAll("[data-tab-panel]").forEach((panel) => {
        panel.classList.toggle("hidden", panel.getAttribute("data-tab-panel") !== activeTab);
      });
      usageDashboard.querySelectorAll("[data-overview-only]").forEach((panel) => {
        panel.classList.toggle("hidden", activeTab !== "overview");
      });
      if (lastData && activeTab === "trends") {
        const draw = () => trendsPlot.render(lastData, chartMode, { animate: true });
        requestAnimationFrame(() => requestAnimationFrame(draw));
        setTimeout(draw, 80);
      }
    });
  });
  if (provider) provider.addEventListener("change", loadUsage);
  if (scope) scope.addEventListener("change", loadUsage);
  if (typeof ResizeObserver !== "undefined") {
    let resizeTimer = 0;
    const observe = (el, plot) => {
      if (!el) return;
      let lastW = 0;
      let busy = false;
      const ro = new ResizeObserver(() => {
        if (!lastData || busy) return;
        const next = Math.floor(el.clientWidth) || 0;
        if (next < 80 || Math.abs(next - lastW) < 4) return;
        lastW = next;
        window.clearTimeout(resizeTimer);
        resizeTimer = window.setTimeout(() => {
          busy = true;
          plot.render(lastData, chartMode, { animate: false });
          lastW = Math.floor(el.clientWidth) || lastW;
          busy = false;
        }, 120);
      });
      ro.observe(el);
    };
    observe(usageDashboard.querySelector("[data-stacked-chart]"), overviewPlot);
    observe(usageDashboard.querySelector("[data-trends-chart]"), trendsPlot);
  }
  loadUsage();
}

/* —— Shared toasts + vault unlock prompt (fuseUI) —— */
(() => {
  const PW_KEY = "fuse.vault.pw";
  const DISMISS_KEY = "fuse.vault.dismissed";
  const PW_TTL = 12 * 60 * 60 * 1000;
  let toastRoot = null;
  let vaultEl = null;
  let vaultScrim = null;
  let vaultHandlers = [];
  let vaultUid = null;
  let autoTried = false;

  function csrf() {
    const el = document.querySelector('meta[name="csrf-token"]');
    return (el && el.getAttribute("content")) || "";
  }

  function ensureRoot() {
    if (!toastRoot || !document.body.contains(toastRoot)) {
      toastRoot = document.createElement("div");
      toastRoot.className = "fuse-toasts";
      document.body.appendChild(toastRoot);
    }
    return toastRoot;
  }

  const store = {
    load(uid) {
      try {
        const raw = JSON.parse(localStorage.getItem(PW_KEY) || "null");
        if (!raw || typeof raw.p !== "string" || !raw.p) return "";
        if (Number(raw.u) !== Number(uid)) return "";
        if (!raw.t || Date.now() - Number(raw.t) > PW_TTL) {
          localStorage.removeItem(PW_KEY);
          return "";
        }
        return raw.p;
      } catch (_) {
        return "";
      }
    },
    save(uid, password) {
      try {
        localStorage.setItem(PW_KEY, JSON.stringify({ u: Number(uid), p: String(password), t: Date.now() }));
      } catch (_) {}
    },
    clear() {
      try {
        localStorage.removeItem(PW_KEY);
      } catch (_) {}
    },
  };

  function dismissed() {
    try {
      return sessionStorage.getItem(DISMISS_KEY) === "1";
    } catch (_) {
      return false;
    }
  }

  function setDismissed() {
    try {
      sessionStorage.setItem(DISMISS_KEY, "1");
    } catch (_) {}
  }

  function clearDismissed() {
    try {
      sessionStorage.removeItem(DISMISS_KEY);
    } catch (_) {}
  }

  function toast(opts) {
    opts = opts || {};
    const key = opts.key || "";
    if (key) {
      const root = toastRoot;
      const existing = root && root.querySelector('[data-toast-key="' + key.replace(/"/g, '\\"') + '"]');
      if (existing && existing._ctrl) {
        existing._ctrl.update(opts);
        return existing._ctrl;
      }
    }
    const el = document.createElement("div");
    el.className = "fuse-toast is-idle";
    if (key) el.setAttribute("data-toast-key", key);
    el.setAttribute("role", "status");
    const dot = document.createElement("span");
    dot.className = "fuse-toast-dot";
    const body = document.createElement("div");
    body.className = "fuse-toast-body";
    const title = document.createElement("b");
    const msg = document.createElement("span");
    body.appendChild(title);
    body.appendChild(msg);
    const act = document.createElement("span");
    act.className = "fuse-toast-act";
    const x = document.createElement("button");
    x.type = "button";
    x.className = "fuse-toast-x";
    x.textContent = "✕";
    x.setAttribute("aria-label", "Dismiss");
    el.appendChild(dot);
    el.appendChild(body);
    el.appendChild(act);
    el.appendChild(x);
    let timer = null;
    const ctrl = {
      el,
      update(next) {
        next = next || {};
        el.className = "fuse-toast is-" + (next.kind || "idle");
        title.textContent = next.title || "";
        msg.textContent = next.msg || "";
        act.innerHTML = "";
        if (next.actionLabel) {
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "btn sm ghost";
          btn.textContent = next.actionLabel;
          btn.addEventListener("click", () => {
            if (typeof next.onAction === "function") next.onAction();
          });
          act.appendChild(btn);
        }
        window.clearTimeout(timer);
        if (next.ttl) timer = window.setTimeout(() => ctrl.close(), next.ttl);
      },
      close() {
        window.clearTimeout(timer);
        el.remove();
      },
    };
    el._ctrl = ctrl;
    x.addEventListener("click", () => ctrl.close());
    ensureRoot().appendChild(el);
    ctrl.update(opts);
    return ctrl;
  }

  function closeVault() {
    if (vaultEl) {
      vaultEl.remove();
      vaultEl = null;
    }
    if (vaultScrim) {
      vaultScrim.remove();
      vaultScrim = null;
    }
    vaultHandlers = [];
  }

  function emitUnlocked() {
    clearDismissed();
    const fns = vaultHandlers.slice();
    closeVault();
    fns.forEach((fn) => {
      try {
        fn();
      } catch (_) {}
    });
  }

  function vaultPrompt(opts) {
    opts = opts || {};
    if (typeof opts.onUnlocked === "function" && !vaultHandlers.includes(opts.onUnlocked)) {
      vaultHandlers.push(opts.onUnlocked);
    }
    if (opts.error) clearDismissed();
    if (vaultEl) {
      const err = vaultEl.querySelector(".fuse-vault-err");
      if (err) {
        err.hidden = !opts.error;
        err.textContent = opts.error || "";
      }
      return;
    }
    const el = document.createElement("div");
    el.className = "fuse-vault";
    const scrim = document.createElement("div");
    scrim.className = "fuse-vault-scrim is-on";
    const why = document.createElement("p");
    why.className = "fuse-vault-why";
    why.textContent = opts.message || "Unlock machines to use SSH. Your account password unwraps those secrets here.";
    const form = document.createElement("form");
    const label = document.createElement("label");
    label.className = "fuse-vault-field";
    const span = document.createElement("span");
    span.textContent = "Account password";
    const input = document.createElement("input");
    input.type = "password";
    input.maxLength = 256;
    input.autocomplete = "current-password";
    input.placeholder = "Account password";
    label.appendChild(span);
    label.appendChild(input);
    const submit = document.createElement("button");
    submit.type = "submit";
    submit.className = "btn sm";
    submit.textContent = "Unlock";
    form.appendChild(label);
    form.appendChild(submit);
    const err = document.createElement("p");
    err.className = "callout fault fuse-vault-err";
    err.hidden = true;
    if (opts.error) {
      err.hidden = false;
      err.textContent = opts.error;
    }
    const dismiss = document.createElement("button");
    dismiss.type = "button";
    dismiss.className = "fuse-vault-dismiss";
    dismiss.textContent = "Not now";
    el.appendChild(why);
    el.appendChild(form);
    el.appendChild(err);
    el.appendChild(dismiss);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const password = input.value;
      if (!password) return;
      submit.disabled = true;
      try {
        const res = await fetch("/account/vault/unlock", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf() },
          body: JSON.stringify({ password }),
        });
        if (res.ok) {
          if (vaultUid != null) store.save(vaultUid, password);
          input.value = "";
          emitUnlocked();
          return;
        }
        if (res.status === 400 || res.status === 401) store.clear();
        let detail = "Password is wrong.";
        try {
          const body = await res.json();
          const msg = body && body.detail && typeof body.detail === "string" ? body.detail : "";
          if (msg) detail = msg;
        } catch (_) {}
        err.hidden = false;
        err.textContent = detail;
      } catch (_) {
        err.hidden = false;
        err.textContent = "Could not reach Fuse. Try again.";
      } finally {
        submit.disabled = false;
      }
    });
    dismiss.addEventListener("click", () => {
      setDismissed();
      closeVault();
    });
    vaultEl = el;
    vaultScrim = scrim;
    document.body.appendChild(scrim);
    document.body.appendChild(el);
    input.focus();
  }

  async function tryStoredPassword() {
    if (vaultUid == null) return false;
    const password = store.load(vaultUid);
    if (!password) return false;
    try {
      const res = await fetch("/account/vault/unlock", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf() },
        body: JSON.stringify({ password }),
      });
      if (res.ok) {
        store.save(vaultUid, password);
        return true;
      }
      if (res.status === 400 || res.status === 401) store.clear();
      return false;
    } catch (_) {
      return false;
    }
  }

  async function vaultGate(opts) {
    opts = opts || {};
    vaultUid = opts.uid != null ? Number(opts.uid) : vaultUid;
    if (typeof opts.onUnlocked === "function" && !vaultHandlers.includes(opts.onUnlocked)) {
      vaultHandlers.push(opts.onUnlocked);
    }
    if (opts.vault === "unlocked" || opts.hasSsh === false) return;
    if (!autoTried) {
      autoTried = true;
      if (await tryStoredPassword()) {
        emitUnlocked();
        return;
      }
    }
    if (!opts.force && dismissed()) return;
    vaultPrompt(opts);
  }

  document.addEventListener("submit", (event) => {
    const form = event.target.closest ? event.target.closest('form[action="/logout"]') : null;
    if (form) store.clear();
  }, true);

  window.fuseUI = {
    toast,
    vaultPrompt,
    vaultGate,
    closeVault,
    vault: store,
  };
})();

/* —— Bug report —— */
(function () {
  const ERROR_RING_MAX = 6;
  const errorRing = [];
  const THEME_KEY = "fuse.chat.theme.v1";

  window.addEventListener("error", (event) => {
    errorRing.push({
      message: String(event.message || "error").slice(0, 200),
      source: String(event.filename || "").split("/").pop(),
      line: event.lineno,
      at: new Date().toISOString(),
    });
    if (errorRing.length > ERROR_RING_MAX) errorRing.shift();
  });
  window.addEventListener("unhandledrejection", (event) => {
    const reason = event.reason && (event.reason.message || event.reason);
    errorRing.push({
      message: String(reason || "unhandled rejection").slice(0, 200),
      at: new Date().toISOString(),
    });
    if (errorRing.length > ERROR_RING_MAX) errorRing.shift();
  });

  function currentView() {
    const body = document.body;
    if (body.classList.contains("is-artifact-page")) return "artifact";
    if (body.classList.contains("is-settings")) return "settings";
    if (body.classList.contains("is-chat")) return "chat";
    return "site";
  }

  function uiState() {
    const state = {};
    const toggle = document.querySelector("[data-history-toggle]");
    if (toggle) state.sidebarOpen = toggle.getAttribute("aria-expanded") === "true";
    const tab = document.querySelector("[data-history-tab][aria-selected='true']");
    if (tab) state.historyTab = tab.getAttribute("data-history-tab");
    const artifact = document.querySelector("[data-chat-artifact]");
    if (artifact) state.artifactOpen = !artifact.hidden;
    state.streaming = document.body.classList.contains("is-streaming");
    return state;
  }

  function snapshot() {
    const chatView = document.querySelector("[data-chat]");
    const theme = chatView ? localStorage.getItem(THEME_KEY) || "" : "";
    return {
      view: currentView(),
      ui_state: uiState(),
      viewport: `${window.innerWidth}x${window.innerHeight}`,
      screen: `${screen.width}x${screen.height}`,
      dpr: Number(window.devicePixelRatio.toFixed(2)),
      locale: navigator.language || "",
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "",
      online: navigator.onLine,
      page_age_ms: Math.round(performance.now()),
      theme,
      thread_ref: (location.pathname.match(/^\/chat\/([^/]+)/) || [])[1] || "",
      user_agent: navigator.userAgent,
      recent_errors: errorRing.slice(-ERROR_RING_MAX),
    };
  }

  function init() {
    const dialog = document.querySelector("dialog.modal[data-bug-report]");
    if (!dialog || typeof dialog.showModal !== "function") return;
    const form = dialog.querySelector("[data-bug-report-form]");
    const done = dialog.querySelector("[data-bug-done]");
    const summary = dialog.querySelector("[data-bug-summary]");
    const expected = dialog.querySelector("[data-bug-expected]");
    const details = dialog.querySelector("[data-bug-details]");
    const contextPre = dialog.querySelector("[data-bug-context]");
    const status = dialog.querySelector("[data-bug-status]");
    const refOut = dialog.querySelector("[data-bug-ref]");
    const send = dialog.querySelector("[data-bug-send]");
    let busy = false;

    const reset = () => {
      form.hidden = false;
      done.hidden = true;
      status.textContent = "";
      send.disabled = false;
      busy = false;
    };

    dialog.addEventListener("close", reset);
    dialog.querySelectorAll("[data-bug-close], [data-bug-finish]").forEach((button) => {
      button.addEventListener("click", () => dialog.close());
    });
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });

    const toast = (message) => {
      if (window.fuseUI && typeof window.fuseUI.toast === "function") window.fuseUI.toast(message);
    };

    const submit = async (event) => {
      event.preventDefault();
      if (busy) return;
      const text = summary.value.trim();
      if (text.length < 4) {
        status.textContent = "A few words are needed.";
        return;
      }
      busy = true;
      send.disabled = true;
      status.textContent = "Sending…";
      const snap = snapshot();
      contextPre.textContent = JSON.stringify(
        { page: location.pathname + location.search, ...snap },
        null,
        2,
      );
      try {
        const response = await fetch("/report-issue", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
          body: JSON.stringify({
            summary: text.slice(0, 4000),
            expected: expected.value.trim().slice(0, 2000),
            page: location.pathname + location.search,
            view: snap.view,
            context: snap,
          }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || !payload.ok) {
          const message = payload && payload.detail ? payload.detail : "Could not send the report.";
          status.textContent = typeof message === "string" ? message : "Could not send the report.";
          send.disabled = false;
          busy = false;
          return;
        }
        refOut.textContent = payload.ref || "";
        form.hidden = true;
        done.hidden = false;
        summary.value = "";
        expected.value = "";
      } catch (_) {
        status.textContent = "Network error — try again.";
        send.disabled = false;
        busy = false;
      }
    };

    form.addEventListener("submit", submit);
    details.addEventListener("toggle", () => {
      if (details.open) {
        const snap = snapshot();
        contextPre.textContent = JSON.stringify(
          { page: location.pathname + location.search, ...snap },
          null,
          2,
        );
      }
    });

    document.querySelectorAll("[data-bug-report-trigger]").forEach((button) => {
      button.addEventListener("click", () => {
        reset();
        if (details.open) details.open = false;
        dialog.showModal();
        summary.focus();
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();

(function sourceMineFilter() {
  function byGroup(attr, group) {
    return Array.from(document.querySelectorAll(`[${attr}]`)).filter((el) => el.getAttribute("data-mine-group") === group);
  }

  function tablesFor(input) {
    const group = input.getAttribute("data-mine-group") || "";
    if (group) return byGroup("data-source-table", group);
    const next = input.closest(".stack-y")?.querySelector("[data-source-table]");
    return next ? [next] : [];
  }

  function inputsFor(input) {
    const group = input.getAttribute("data-mine-group") || "";
    if (!group) return [input];
    return byGroup("data-mine-only", group);
  }

  function apply(input) {
    const on = Boolean(input.checked);
    inputsFor(input).forEach((el) => {
      el.checked = on;
    });
    tablesFor(input).forEach((table) => {
      table.classList.toggle("is-mine-only", on);
    });
  }

  document.querySelectorAll("[data-mine-only]").forEach((input) => {
    input.addEventListener("change", () => apply(input));
    apply(input);
  });
})();
