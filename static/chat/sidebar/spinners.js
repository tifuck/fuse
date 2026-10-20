// Run spinners and age chips in the history list.
import { S } from '../state.js';
import { escapeHtml } from '../core.js';

function stampSpinHtml(html, stamp) {
  const tag = String(stamp || "run").replace(/[^A-Za-z0-9_-]/g, "") || "run";
  return html.replace(/id="cas-/g, `id="${tag}-cas-`).replace(/url\(#cas-/g, `url(#${tag}-cas-`);
}

function threadIsRunning(item) {
  return Boolean(item && item.running);
}

function ageStamp(iso) {
  const ts = Date.parse(iso || "");
  if (!Number.isFinite(ts)) return "";
  const secs = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (secs < 60) return "now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return mins + "m";
  const hours = Math.floor(mins / 60);
  if (hours < 24) return hours + "h";
  const days = Math.floor(hours / 24);
  if (days < 7) return days + "d";
  const weeks = Math.floor(days / 7);
  if (weeks < 5) return weeks + "w";
  const months = Math.floor(days / 30.44);
  if (months < 12) return months + "mo";
  return Math.floor(days / 365.25) + "y";
}

function ageChip(item) {
  if (!item || !item.updated_at) return "";
  const stamp = ageStamp(item.updated_at);
  if (!stamp) return "";
  const when = new Date(item.updated_at);
  const title = Number.isFinite(when.getTime()) ? ` title="${escapeHtml(when.toLocaleString())}"` : "";
  return `<span class="chat-history-age" data-age-stamp="${escapeHtml(String(item.updated_at))}"${title}>${escapeHtml(stamp)}</span>`;
}

export function createSpinners(ctx) {
  const { agents, render, threads, ui } = ctx;
    function pickRandomSpin() {
    const pool = S.ACTIVITY_SPINNERS.filter((s) => !S.recentSpinIds.includes(s.id));
    const source = pool.length ? pool : S.ACTIVITY_SPINNERS;
    return source[Math.floor(Math.random() * source.length)];
  }

    function spinAge(origin) {
    const elapsed = Math.max(0, Date.now() - (origin || Date.now()));
    return `${((elapsed % S.SPINNER_HOLD_MS) / 1000).toFixed(3)}s`;
  }

    function currentRunSpin() {
    if (S.reduceMotion.matches) return { key: "still", html: S.SPINNER_STILL };
    const win = Math.floor(Date.now() / S.SPINNER_HOLD_MS);
    if (S.currentSpin === null || win !== S.spinWindow) {
      S.spinWindow = win;
      S.currentSpin = pickRandomSpin();
      S.recentSpinIds.push(S.currentSpin.id);
      if (S.recentSpinIds.length > S.SPINNER_MEMORY) S.recentSpinIds.shift();
    }
    return { key: S.currentSpin.id, html: S.currentSpin.html };
  }

    function applySpinEl(el, spin, stamp, swap, origin) {
    if (!el || !spin) return;
    const current = el.querySelector(":scope > .cas-face:not(.cas-swap-out)");
    if (el.dataset.spinSet === spin.key && current) return;
    const swapping = swap !== false && Boolean(el.dataset.spinSet) && Boolean(current) && !S.reduceMotion.matches;
    el.dataset.spinSet = spin.key;
    const face = document.createElement("span");
    face.className = "cas-face";
    if (origin) face.style.setProperty("--cas-age", spinAge(origin));
    face.innerHTML = stampSpinHtml(spin.html, stamp);
    if (!swapping) {
      el.replaceChildren(face);
      return;
    }
    face.classList.add("cas-swap-in");
    el.querySelectorAll(":scope > .cas-face").forEach((prev) => {
      prev.classList.remove("cas-swap-in");
      prev.classList.add("cas-swap-out");
      const done = () => prev.remove();
      prev.addEventListener("animationend", done, { once: true });
      window.setTimeout(done, 480);
    });
    el.appendChild(face);
  }

    function runSpinMarkup(stamp) {
    {
      return `<span class="chat-run-spin" data-run-spin="${escapeHtml(String(stamp))}" data-spin-set="console-glyph" aria-hidden="true">` +
        `<span class="chat-console-spin">${ui.consoleSpinGlyph(true)}</span></span>`;
    }

  }

    function fillSpinEl(el, stamp, swap) {

    {
      ui.setConsoleSpin(el);
      return;
    }

  }

    function setSpinVisible(el, on, stamp) {
    if (!el) return;
    el.hidden = !on;
    if (on) fillSpinEl(el, stamp, false);
    else {
      el.replaceChildren();
      delete el.dataset.spinSet;
    }
  }

    function noteServerRunning(list) {
    (list || []).forEach((item) => { if (item && item.id) S.serverRunning.set(item.id, Boolean(item.running)); });
  }

    function currentThreadBusy() {
    if (S.streaming || S.agentsRows.some((row) => agents.agentLive(row.status))) return true;
    // A live background command, the main agent's or a subagent's, is work too.
    if (S.jobsThreadId === S.currentId && S.jobsRows.length) return true;
    if (S.agentsLoadedFor === S.currentId) return false;
    return S.serverRunning.get(S.currentId) === true;
  }

    function syncCurrentThreadBusy() {
    const current = threads.threadById(S.currentId);
    const busy = currentThreadBusy();
    if (current && current.running !== busy) {
      current.running = busy;
      render.renderHistory();
    }
  }

    function anyRunningOfKind(kind) {
    const match = (item) => threadIsRunning(item) && (kind === "project" ? item.kind === "project" : item.kind !== "project");
    return S.threads.some(match) || S.archivedThreads.some(match) || S.searchThreads.some(match) || S.searchArchived.some(match);
  }

    function stopRunSpinClock() {
    if (S.runSpinTimer) {
      window.clearInterval(S.runSpinTimer);
      S.runSpinTimer = 0;
    }
  }

    function startRunSpinClock() {
    if (S.runSpinTimer || S.reduceMotion.matches) return;
    S.runSpinTimer = window.setInterval(() => {
      const nodes = [...S.root.querySelectorAll("[data-run-spin]")].filter((el) => !el.hidden);
      if (!nodes.length) {
        stopRunSpinClock();
        return;
      }
      nodes.forEach((el) => fillSpinEl(el, el.getAttribute("data-run-spin") || "run"));
    }, 250);
  }

    function syncRunSpinners() {
    const chatBusy = anyRunningOfKind("chat");
    const projectBusy = anyRunningOfKind("project");
    const headingBusy = S.historyTab === "projects" ? projectBusy : chatBusy;
    setSpinVisible(S.root.querySelector("[data-run-spin='heading']"), headingBusy, "heading");
    setSpinVisible(S.root.querySelector("[data-run-spin='chats']"), chatBusy, "chats");
    setSpinVisible(S.root.querySelector("[data-run-spin='projects']"), projectBusy, "projects");
    S.root.querySelectorAll("[data-history-tab]").forEach((btn) => {
      const tab = btn.getAttribute("data-history-tab");
      const busy = tab === "projects" ? projectBusy : chatBusy;
      btn.classList.toggle("is-busy", busy);
      btn.setAttribute("aria-busy", busy ? "true" : "false");
    });
    S.root.querySelectorAll("[data-run-spin]").forEach((el) => {
      if (!el.hidden) fillSpinEl(el, el.getAttribute("data-run-spin") || "run", false);
    });
    const any = chatBusy || projectBusy;
    if (any) {
      if (!S.runSpinOrigin) S.runSpinOrigin = Date.now();
      startRunSpinClock();
    } else {
      S.runSpinOrigin = 0;
      stopRunSpinClock();
    }
  }

    function refreshAgeChips() {
    if (!S.historyList) return;
    S.historyList.querySelectorAll("[data-age-stamp]").forEach((el) => {
      const next = ageStamp(el.getAttribute("data-age-stamp"));
      if (next && el.textContent !== next) el.textContent = next;
    });
  }

    function startAgeClock() {
    if (S.ageTimer) return;
    S.ageTimer = window.setInterval(refreshAgeChips, S.AGE_TICK_MS);
  }

  return {
    applySpinEl,
    currentRunSpin,
    currentThreadBusy,
    noteServerRunning,
    pickRandomSpin,
    runSpinMarkup,
    startAgeClock,
    syncCurrentThreadBusy,
    syncRunSpinners,
    ageChip,
    threadIsRunning,
  };
}

export { ageChip, threadIsRunning };
