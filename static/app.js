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

// Phone back gesture: every sheet, drawer, popover and dialog that should
// close on "back" registers here. open() pushes one history entry per layer;
// back pops the top layer only. A layer closed from its own UI steps history
// back over its entry instead of rewriting it, so no dead entry is left for
// the next swipe to land on. Layers closed from underneath stay as "dead"
// placeholders until everything above them is gone, then are skipped together.
window.FuseBack = (function fuseBack() {
  const mobile = window.matchMedia("(max-width: 800px)");
  const stack = []; // { id, close, dead, pushed }
  let inflight = 0; // our own traversals whose popstate has not arrived yet
  let backBy = 0;
  let flushQueued = false;
  const settledQueue = [];

  function carry() {
    const state = history.state;
    return state && state.fuseChat ? { fuseChat: true } : {};
  }
  function pushEntry(entry) {
    entry.pushed = true;
    history.pushState({ ...carry(), fuseBack: entry.id }, "");
  }
  function busy() {
    return inflight > 0 || flushQueued;
  }
  function settle() {
    if (busy()) return;
    for (const entry of stack) if (!entry.pushed && !entry.dead) pushEntry(entry);
    while (settledQueue.length && !busy()) settledQueue.shift()();
  }
  function goBack(n) {
    backBy += n;
    if (flushQueued) return;
    // Batch every close made in this task (e.g. a pane and the subagent open
    // on top of it) into one traversal; browsers drop a second back() that is
    // issued before the first one lands.
    flushQueued = true;
    queueMicrotask(() => {
      flushQueued = false;
      const k = backBy;
      backBy = 0;
      if (k > 0) {
        inflight += 1;
        history.go(-k);
      } else settle();
    });
  }
  function trimDead() {
    let n = 0;
    while (stack.length && stack[stack.length - 1].dead) {
      if (stack.pop().pushed) n += 1;
    }
    if (n) goBack(n);
  }
  function has(id) {
    return stack.some((entry) => entry.id === id && !entry.dead);
  }
  function open(id, close) {
    if (!mobile.matches || has(id)) return;
    const entry = { id, close, dead: false, pushed: false };
    stack.push(entry);
    if (!busy()) pushEntry(entry);
  }
  function drop(id) {
    for (let i = stack.length - 1; i >= 0; i -= 1) {
      const entry = stack[i];
      if (entry.id !== id || entry.dead) continue;
      entry.dead = true;
      trimDead();
      return;
    }
  }
  // Run fn once no traversal of ours is pending, so a URL write lands on the
  // entry the page ends up on rather than on one that is about to be popped.
  function whenSettled(fn) {
    if (busy()) settledQueue.push(fn);
    else fn();
  }
  window.addEventListener("popstate", (event) => {
    if (inflight > 0) {
      inflight -= 1;
      event.fuseBackHandled = true;
      settle();
      return;
    }
    if (stack.length) {
      event.fuseBackHandled = true;
      const top = stack.pop();
      trimDead();
      if (!top.dead) {
        try { top.close(); } catch (error) { console.error(error); }
      }
      if (!busy()) settle();
      return;
    }
    if (event.state && event.state.fuseBack) {
      // A layer entry left over from before a reload: nothing is open for it.
      event.fuseBackHandled = true;
      inflight += 1;
      history.back();
    }
  });

  // Modal <dialog>s on every page (confirm, bug report, Hosts, Tasks,
  // Sources) register themselves: back closes the dialog like its Close button.
  let dialogSeq = 0;
  function watchDialog(dialog) {
    if (dialog._fuseBackId) return;
    dialog._fuseBackId = "dialog:" + (++dialogSeq);
    const sync = () => {
      if (dialog.open) open(dialog._fuseBackId, () => { if (dialog.open) dialog.close(); });
      else drop(dialog._fuseBackId);
    };
    new MutationObserver(sync).observe(dialog, { attributes: true, attributeFilter: ["open"] });
    if (dialog.open) sync();
  }
  function watchDialogs(rootEl) {
    rootEl.querySelectorAll?.("dialog").forEach(watchDialog);
  }
  watchDialogs(document);
  new MutationObserver((records) => {
    for (const record of records) {
      record.addedNodes.forEach((node) => {
        if (node.nodeType !== 1) return;
        if (node.tagName === "DIALOG") watchDialog(node);
        else watchDialogs(node);
      });
    }
  }).observe(document.documentElement, { childList: true, subtree: true });

  // An iOS Home Screen app has no system back swipe. Give it one: a drag that
  // starts at the left edge steps back, with a chevron that follows the finger.
  if (window.navigator.standalone === true) {
    let swipe = null;
    let cue = null;
    const EDGE = 18;
    const TRIGGER = 72;
    const cueEl = () => {
      if (cue) return cue;
      cue = document.createElement("div");
      cue.className = "fuse-back-cue";
      cue.setAttribute("aria-hidden", "true");
      cue.innerHTML = '<svg viewBox="0 0 16 16" width="16" height="16"><path d="M10 3.5L5.5 8l4.5 4.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
      document.body.appendChild(cue);
      return cue;
    };
    document.addEventListener("touchstart", (event) => {
      const touch = event.touches[0];
      if (event.touches.length !== 1 || !touch || touch.clientX > EDGE) { swipe = null; return; }
      if (event.target.closest?.("[data-fuse-dial], input[type=range]")) { swipe = null; return; }
      swipe = { x: touch.clientX, y: touch.clientY, dx: 0, locked: false };
    }, { passive: true });
    document.addEventListener("touchmove", (event) => {
      if (!swipe) return;
      const touch = event.touches[0];
      const dx = touch.clientX - swipe.x;
      const dy = touch.clientY - swipe.y;
      if (!swipe.locked) {
        if (Math.abs(dy) > 12 && Math.abs(dy) > Math.abs(dx)) { swipe = null; return; }
        if (dx < 10) return;
        swipe.locked = true;
      }
      swipe.dx = Math.max(0, dx);
      const el = cueEl();
      const p = Math.min(1, swipe.dx / TRIGGER);
      el.style.top = swipe.y + "px";
      el.style.setProperty("--p", p.toFixed(3));
      el.classList.toggle("is-armed", p >= 1);
      el.classList.add("is-on");
      if (event.cancelable) event.preventDefault();
    }, { passive: false });
    const end = () => {
      if (!swipe) return;
      const fire = swipe.locked && swipe.dx >= TRIGGER;
      swipe = null;
      if (cue) cue.classList.remove("is-on", "is-armed");
      if (fire) history.back();
    };
    document.addEventListener("touchend", end, { passive: true });
    document.addEventListener("touchcancel", end, { passive: true });
  }

  return { open, drop, has, whenSettled, isMobile: () => mobile.matches };
})();

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
    const backId = "account:" + (toggle.id || Math.random().toString(36).slice(2));
    function setOpen(open) {
      const next = Boolean(open);
      const was = !menu.hidden;
      root.classList.toggle("is-open", next);
      menu.hidden = !next;
      toggle.setAttribute("aria-expanded", next ? "true" : "false");
      if (next && !was) FuseBack.open(backId, () => setOpen(false));
      else if (!next && was) FuseBack.drop(backId);
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
    if (next && !was && mobile.matches) FuseBack.open("settings-rail", () => setDrawer(false));
    else if (!next && was) FuseBack.drop("settings-rail");
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
  const auth = dialog.querySelector("[data-auth-open]");
  if (auth && dialog.hasAttribute("data-auto-open")) {
    const href = auth.getAttribute("href");
    if (href) window.open(href, "_blank", "noopener");
  }
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

  document.querySelectorAll("[data-pool-own-form]").forEach((form) => {
    const input = form.querySelector("[data-pool-own]");
    const value = form.querySelector("[data-pool-own-value]");
    if (!input || !value) return;
    input.addEventListener("change", () => {
      if (form.dataset.saving) return;
      form.dataset.saving = "1";
      value.value = input.checked ? "1" : "0";
      input.disabled = true;
      form.submit();
    });
  });
})();
