// Phone quick dial: a movable hub on the screen edge that fans chat actions
// out on spokes. Tap opens the fan; press-and-hold opens it under the thumb so
// sliding onto an action and letting go runs it; an immediate drag moves the
// hub, which snaps to the nearer side edge. Actions go through
// window.FuseChatActions (chat.js); back closes the fan (FuseBack).
(function quickDial() {
  const root = document.querySelector(".chat");
  const dial = document.querySelector("[data-fuse-dial]");
  const scrim = document.querySelector("[data-dial-scrim]");
  const actions = window.FuseChatActions;
  if (!root || !dial || !scrim || !actions || !window.FuseBack) return;
  const hub = dial.querySelector("[data-dial-hub]");
  const fan = dial.querySelector("[data-dial-fan]");
  const spokes = dial.querySelector("[data-dial-spokes]");
  const mobile = window.matchMedia("(max-width: 800px)");
  const STORE = "fuse.chat.dial.v1";
  const HUB = 48;
  const INSET = 16;
  const RADIUS = 112;
  const HOLD_MS = 260;
  const SVGNS = "http://www.w3.org/2000/svg";
  const ICONS = {
    retry: '<path d="M12.8 8a4.8 4.8 0 1 1-1.5-3.5M12.8 2.6v2.6h-2.6" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
    stop: '<rect x="4.2" y="4.2" width="7.6" height="7.6" rx="1.4" fill="currentColor"/>',
    undo: '<path d="M5.5 4L2.8 6.7l2.7 2.7M3 6.7h6.4a3.8 3.8 0 0 1 0 7.6H7" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
    redo: '<path d="M10.5 4l2.7 2.7-2.7 2.7M13 6.7H6.6a3.8 3.8 0 0 0 0 7.6H9" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
    rewind: '<path d="M8 4.6V8l2.4 1.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><path d="M2.6 8a5.4 5.4 0 1 0 1.6-3.8M2.6 2.8v2.6h2.6" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
    agents: '<rect x="3" y="5" width="10" height="7.5" rx="2" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M8 5V2.8" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/><circle cx="6" cy="8.6" r="0.9" fill="currentColor"/><circle cx="10" cy="8.6" r="0.9" fill="currentColor"/>',
    new: '<path d="M9.5 2.5H4a1.5 1.5 0 0 0-1.5 1.5v8A1.5 1.5 0 0 0 4 13.5h8a1.5 1.5 0 0 0 1.5-1.5V6.5M11.6 2.2l2.2 2.2L8.5 9.7H6.3V7.5z" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linejoin="round"/>',
  };
  // Slot order runs top to bottom along the arc.
  const SLOTS = [
    { id: "retry" },
    { id: "undo" },
    { id: "redo" },
    { id: "rewind" },
    { id: "agents" },
    { id: "new" },
  ];

  function describe(id, state) {
    if (id === "retry") {
      return state.running
        ? { icon: "stop", label: "Stop" }
        : { icon: "retry", label: "Retry", off: !state.hasTurns && "Nothing to retry." };
    }
    if (id === "undo") return { icon: "undo", label: "Undo", off: (state.running && "Wait for the current reply to finish.") || (!state.hasTurns && "Nothing to undo.") };
    if (id === "redo") return { icon: "redo", label: "Redo", off: (state.running && "Wait for the current reply to finish.") || (!state.canRedo && "Nothing to redo.") };
    if (id === "rewind") return { icon: "rewind", label: "Rewind", off: (state.running && "Wait for the current reply to finish.") || (!state.hasTurns && "Nothing to rewind.") };
    if (id === "agents") return { icon: "agents", label: "Agents" };
    return { icon: "new", label: "New chat" };
  }

  const sats = SLOTS.map((slot) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "fuse-dial-sat";
    btn.setAttribute("role", "menuitem");
    btn.tabIndex = -1;
    btn.dataset.action = slot.id;
    btn.innerHTML = '<svg viewBox="0 0 16 16" width="17" height="17" aria-hidden="true"></svg><span class="fuse-dial-label"></span><span class="fuse-dial-badge" hidden></span>';
    btn.addEventListener("click", (event) => {
      event.stopPropagation();
      if (event.detail === 0 || !press) fire(SLOTS.indexOf(slot));
    });
    fan.appendChild(btn);
    const line = document.createElementNS(SVGNS, "line");
    spokes.appendChild(line);
    return { slot, btn, line, off: false, offText: "" };
  });

  let open = false;
  let side = "left";
  let yFrac = 0.62;
  let geometry = [];
  let hot = -1;
  let press = null;
  try {
    const saved = JSON.parse(localStorage.getItem(STORE) || "null");
    if (saved && (saved.side === "left" || saved.side === "right")) side = saved.side;
    if (saved && Number.isFinite(saved.y)) yFrac = Math.min(1, Math.max(0, saved.y));
  } catch (_) {}

  function savePosition() {
    try { localStorage.setItem(STORE, JSON.stringify({ side, y: Number(yFrac.toFixed(4)) })); } catch (_) {}
  }
  function prefOn() {
    const prefs = window.FuseChatAppearance?.getComposerPrefs?.();
    return !prefs || prefs.dial !== false;
  }
  function typing() {
    const el = document.activeElement;
    return Boolean(el && (el.tagName === "TEXTAREA" || (el.tagName === "INPUT" && !["checkbox", "radio", "button", "range"].includes(el.type)) || el.isContentEditable));
  }
  // The band the hub may sit in: below the chat bar, above the composer.
  function band() {
    const vv = window.visualViewport;
    const viewH = vv ? vv.height : window.innerHeight;
    const bar = root.querySelector(".chat-bar");
    const dock = root.querySelector(".chat-box") || root.querySelector("[data-chat-form]");
    const top = (bar ? bar.getBoundingClientRect().bottom : 0) + 12;
    const bottom = Math.min(viewH, dock ? dock.getBoundingClientRect().top : viewH) - 12 - HUB;
    return { top, bottom: Math.max(top, bottom) };
  }
  function place() {
    const b = band();
    const y = Math.round(b.top + (b.bottom - b.top) * yFrac);
    dial.style.top = y + "px";
    dial.style.left = side === "left" ? INSET + "px" : "";
    dial.style.right = side === "right" ? INSET + "px" : "";
    dial.dataset.side = side;
  }

  function visible() {
    return mobile.matches && prefOn() && !root.classList.contains("is-history") && !typing() &&
      FuseBack.layers().every((id) => id === "dial");
  }
  function sync() {
    const show = visible();
    if (!show && open) setOpen(false);
    dial.hidden = !show;
    if (show) place();
  }

  // Spread the arc to fit the band: a hub near the top or bottom rotates the
  // fan away from the edge and tightens the spacing only if it must.
  function layout() {
    const b = band();
    const hubY = parseFloat(dial.style.top) || b.top;
    const cy = hubY + HUB / 2;
    const vv = window.visualViewport;
    const viewH = vv ? vv.height : window.innerHeight;
    const room = (px) => (px >= RADIUS ? 90 : (Math.asin(Math.max(0, px) / RADIUS) * 180) / Math.PI);
    const upLim = room(cy - (b.top - 4) - 26);
    const downLim = room(Math.min(viewH, b.bottom + HUB + 12) - cy - 26);
    const span = Math.min(160, upLim + downLim);
    const center = Math.min(Math.max(0, span / 2 - upLim), downLim - span / 2);
    const step = span / (SLOTS.length - 1);
    const dir = side === "left" ? 1 : -1;
    geometry = SLOTS.map((_, i) => {
      const a = ((center - span / 2 + i * step) * Math.PI) / 180;
      return { x: dir * Math.cos(a) * RADIUS, y: Math.sin(a) * RADIUS, cos: dir * Math.cos(a), sin: Math.sin(a) };
    });
  }

  function paint() {
    const state = actions.state();
    // Live subagents, as the agent tree's main row counts them ("3 running").
    const countEl = root.querySelector("[data-agents-list] .is-main .chat-agents-doing");
    const count = ((countEl && countEl.textContent.trim()) || "").split(" ")[0];
    sats.forEach((sat, i) => {
      const d = describe(sat.slot.id, state);
      const g = geometry[i];
      sat.off = Boolean(d.off);
      sat.offText = d.off || "";
      sat.btn.querySelector("svg").innerHTML = ICONS[d.icon];
      const label = sat.btn.querySelector(".fuse-dial-label");
      label.textContent = d.label;
      label.dataset.ax = g.cos > 0.35 ? "start" : g.cos < -0.35 ? "end" : "mid";
      label.dataset.ay = g.sin > 0.5 ? "below" : g.sin < -0.5 ? "above" : "mid";
      // Anchor each label just outside its circle, along the spoke.
      label.style.setProperty("--lx", (g.cos * 27).toFixed(1) + "px");
      label.style.setProperty("--ly", (g.sin * 27).toFixed(1) + "px");
      sat.btn.setAttribute("aria-label", d.label);
      sat.btn.classList.toggle("is-off", sat.off);
      sat.btn.setAttribute("aria-disabled", sat.off ? "true" : "false");
      const badge = sat.btn.querySelector(".fuse-dial-badge");
      badge.hidden = !(sat.slot.id === "agents" && count);
      badge.textContent = count;
      sat.btn.style.setProperty("--x", g.x.toFixed(1) + "px");
      sat.btn.style.setProperty("--y", g.y.toFixed(1) + "px");
      sat.btn.style.setProperty("--i", String(i));
      sat.line.setAttribute("x1", "0");
      sat.line.setAttribute("y1", "0");
      sat.line.setAttribute("x2", g.x.toFixed(1));
      sat.line.setAttribute("y2", g.y.toFixed(1));
    });
  }

  function setHot(index) {
    if (index === hot) return;
    hot = index;
    sats.forEach((sat, i) => sat.btn.classList.toggle("is-hot", i === index));
    if (index >= 0) navigator.vibrate?.(8);
  }

  function setOpen(next) {
    next = Boolean(next);
    if (next === open) return;
    open = next;
    if (open) {
      layout();
      paint();
      FuseBack.open("dial", () => setOpen(false));
    } else {
      FuseBack.drop("dial");
      setHot(-1);
    }
    dial.classList.toggle("is-open", open);
    scrim.hidden = !open;
    hub.setAttribute("aria-expanded", open ? "true" : "false");
    sats.forEach((sat) => { sat.btn.tabIndex = open ? 0 : -1; });
  }

  function fire(index) {
    const sat = sats[index];
    if (!sat) return;
    setOpen(false);
    if (sat.off) {
      actions.hint(sat.offText);
      return;
    }
    navigator.vibrate?.(12);
    actions.run(sat.slot.id);
  }

  function nearest(x, y) {
    const r = hub.getBoundingClientRect();
    const cx = r.left + r.width / 2;
    const cy = r.top + r.height / 2;
    let best = -1;
    let bestD = 34;
    geometry.forEach((g, i) => {
      const d = Math.hypot(x - (cx + g.x), y - (cy + g.y));
      if (d < bestD) { bestD = d; best = i; }
    });
    return best;
  }

  hub.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    const r = dial.getBoundingClientRect();
    press = { id: event.pointerId, x: event.clientX, y: event.clientY, mode: "pending", wasOpen: open, top: r.top, left: r.left };
    try { hub.setPointerCapture(event.pointerId); } catch (_) {}
    press.timer = window.setTimeout(() => {
      if (!press || press.mode !== "pending") return;
      press.mode = "slide";
      if (!open) setOpen(true);
      navigator.vibrate?.(10);
    }, HOLD_MS);
  });
  hub.addEventListener("pointermove", (event) => {
    if (!press || event.pointerId !== press.id) return;
    const dx = event.clientX - press.x;
    const dy = event.clientY - press.y;
    if (press.mode === "pending" && Math.hypot(dx, dy) > 8) {
      window.clearTimeout(press.timer);
      press.mode = open ? "slide" : "drag";
      if (press.mode === "drag") dial.classList.add("is-dragging");
    }
    if (press.mode === "drag") {
      const b = band();
      const top = Math.min(b.bottom, Math.max(b.top, press.top + dy));
      const left = Math.min(window.innerWidth - HUB - 4, Math.max(4, press.left + dx));
      dial.style.top = top + "px";
      dial.style.left = left + "px";
      dial.style.right = "";
    } else if (press.mode === "slide") {
      setHot(nearest(event.clientX, event.clientY));
    }
  });
  function endPress(event) {
    if (!press || event.pointerId !== press.id) return;
    window.clearTimeout(press.timer);
    const p = press;
    press = null;
    if (p.mode === "pending") {
      setOpen(!p.wasOpen);
    } else if (p.mode === "slide") {
      const index = event.type === "pointercancel" ? -1 : nearest(event.clientX, event.clientY);
      if (index >= 0) fire(index);
      else if (!p.wasOpen) setOpen(false);
      else setHot(-1);
    } else if (p.mode === "drag") {
      dial.classList.remove("is-dragging");
      const r = dial.getBoundingClientRect();
      side = r.left + r.width / 2 < window.innerWidth / 2 ? "left" : "right";
      const b = band();
      yFrac = b.bottom > b.top ? (r.top - b.top) / (b.bottom - b.top) : 0.5;
      yFrac = Math.min(1, Math.max(0, yFrac));
      savePosition();
      place();
    }
  }
  hub.addEventListener("pointerup", endPress);
  hub.addEventListener("pointercancel", endPress);
  // A pointer gesture on the hub ends in a synthesized click. Swallow it
  // before document-level "click outside closes it" handlers see it, or a
  // hold-and-slide onto Rewind would open the picker and shut it at once.
  window.addEventListener("click", (event) => {
    if (event.detail !== 0 && hub.contains(event.target)) {
      event.stopImmediatePropagation();
      event.preventDefault();
    }
  }, true);
  // Keyboard and assistive tech: Enter/Space toggles.
  hub.addEventListener("click", (event) => {
    if (event.detail !== 0) return;
    setOpen(!open);
    if (open) sats[0].btn.focus();
  });
  hub.addEventListener("contextmenu", (event) => event.preventDefault());
  scrim.addEventListener("click", () => setOpen(false));
  dial.addEventListener("keydown", (event) => {
    if (!open) return;
    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
      hub.focus();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const i = sats.findIndex((sat) => sat.btn === document.activeElement);
      const next = (i + (event.key === "ArrowDown" ? 1 : -1) + sats.length) % sats.length;
      sats[next].btn.focus();
    }
  });

  mobile.addEventListener("change", sync);
  document.addEventListener("fuse:back-change", sync);
  document.addEventListener("fuse-composer-pref", sync);
  document.addEventListener("focusin", sync);
  document.addEventListener("focusout", () => window.setTimeout(sync, 0));
  new MutationObserver(sync).observe(root, { attributes: true, attributeFilter: ["class"] });
  window.addEventListener("resize", sync);
  window.visualViewport?.addEventListener("resize", sync);
  const dock = root.querySelector("[data-chat-form]");
  if (dock && "ResizeObserver" in window) new ResizeObserver(() => { if (!dial.hidden && !press) place(); }).observe(dock);
  sync();
})();
