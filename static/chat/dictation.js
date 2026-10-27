// Dictation: the composer's mic. Speech streams into the message box as it
// is recognised (the open phrase is re-decoded about once a second, each
// phrase once more when it ends), under a small live overlay.
import { S } from './state.js';
import { Phrases, splice, wav } from './dictation-core.js';

const BANDS = 14;
const HOLD_MS = 400;
const MAX_MS = 5 * 60 * 1000;
const MAC = /Mac|iP(hone|ad|od)/.test(navigator.platform || navigator.userAgent);
export const HOTKEY_LABEL = MAC ? "⌘⇧Space" : "Ctrl+Shift+Space";

function csrfToken() {
  const el = document.querySelector('meta[name="csrf-token"]');
  return (el && el.getAttribute("content")) || "";
}

function micError(err) {
  const name = err && err.name;
  if (name === "NotAllowedError" || name === "SecurityError") return "Microphone access is blocked. Allow it in the browser's site settings.";
  if (name === "NotFoundError" || name === "OverconstrainedError") return "No microphone found.";
  if (name === "NotReadableError") return "The microphone is in use by another app.";
  return "Could not start the microphone.";
}

function clock(ms) {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function overlayHtml() {
  const bars = Array.from({ length: BANDS * 2 }, (_, i) => `<i style="--i:${i}"></i>`).join("");
  return `<span class="dictation-dot" aria-hidden="true"></span>`
    + `<span class="dictation-bars" aria-hidden="true">${bars}</span>`
    + `<span class="dictation-time" data-dictation-time>0:00</span>`
    + `<button type="button" class="dictation-btn" data-dictation-cancel aria-label="Cancel dictation" title="Cancel · Esc">`
    + `<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M4.5 4.5l7 7M11.5 4.5l-7 7" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg></button>`
    + `<button type="button" class="dictation-btn is-done" data-dictation-done aria-label="Finish dictation" title="Done · Enter or ${HOTKEY_LABEL}">`
    + `<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3.6 8.4l2.9 2.9 5.9-6.4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></button>`;
}

export function createDictation({ picker }) {
  const button = S.root.querySelector("[data-chat-mic]");
  if (!button || !S.chatBox) return null;
  const overlay = document.createElement("div");
  overlay.className = "dictation";
  overlay.hidden = true;
  overlay.setAttribute("role", "status");
  overlay.setAttribute("aria-label", "Dictation");
  overlay.innerHTML = overlayHtml();
  S.chatBox.append(overlay);
  const bars = [...overlay.querySelectorAll(".dictation-bars i")];
  const timeEl = overlay.querySelector("[data-dictation-time]");

  let run = null;

  function write(text) {
    if (!run || run.detached) return;
    if (S.input.value !== run.written) { detach(); return; }
    const { value, caret } = splice(run.before, text, run.after);
    if (value === run.written) return;
    run.written = value;
    run.writing = true;
    S.input.value = value;
    S.input.setSelectionRange(caret, caret);
    S.input.dispatchEvent(new Event("input", { bubbles: true }));
    run.writing = false;
  }

  async function decode(p) {
    const samples = p.samples;
    const r = await fetch("/speech/transcribe", {
      method: "POST",
      headers: { "Content-Type": "audio/wav", "X-CSRF-Token": csrfToken() },
      body: wav(p.chunks.slice()),
      signal: run.abort.signal,
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((data.error && data.error.message) || `Dictation failed (${r.status}).`);
    p.text = String(data.text || "").trim();
    p.sent = samples;
  }

  // One request at a time, finished phrases first; GPU decodes take ~0.3 s.
  async function pump() {
    if (!run || run.busy) return;
    const p = run.phrases.next(run.stopping ? 0 : 500);
    if (!p) { if (run.stopping && run.phrases.settled()) settle(); return; }
    run.busy = true;
    const current = run;
    try {
      await decode(p);
      current.failures = 0;
    } catch (err) {
      if (current.abort.signal.aborted) return;
      current.failures += 1;
      if (current.failures >= 3 || current.stopping) {
        picker.setHint(err.message || "Dictation failed.", true);
        p.sent = p.samples;
        current.failures = 0;
      }
    } finally {
      current.busy = false;
    }
    if (run !== current) return;
    write(current.phrases.text());
    pump();
  }

  function frame() {
    if (!run || !run.analyser) return;
    const data = run.freq;
    const half = BANDS;
    // Height is loudness (-55 dB silent, -15 dB full); the spectrum only
    // shapes it, so the bars follow the voice at any mic gain.
    run.analyser.getFloatTimeDomainData(run.wave);
    let sq = 0;
    for (let i = 0; i < run.wave.length; i++) sq += run.wave[i] * run.wave[i];
    const db = 20 * Math.log10(Math.sqrt(sq / run.wave.length) + 1e-8);
    const loud = Math.min(1, Math.max(0, (db + 55) / 40));
    run.analyser.getByteFrequencyData(data);
    const shape = [];
    for (let i = 0; i < half; i++) {
      // Log-spaced voice bands, 150 Hz to ~5 kHz, mirrored around the middle.
      const lo = Math.floor(run.binLo * Math.pow(run.binHi / run.binLo, i / half));
      const hi = Math.max(lo + 1, Math.floor(run.binLo * Math.pow(run.binHi / run.binLo, (i + 1) / half)));
      let sum = 0;
      for (let b = lo; b < hi; b++) sum += data[b];
      shape.push(sum / (hi - lo));
    }
    const top = Math.max(1, ...shape);
    for (let i = 0; i < half; i++) {
      const v = loud * (0.35 + 0.65 * shape[i] / top);
      const prev = run.levels[i];
      run.levels[i] = v > prev ? prev + (v - prev) * 0.6 : prev + (v - prev) * 0.2;
    }
    overlay.classList.toggle("is-voiced", loud > 0.25);
    for (let i = 0; i < half; i++) {
      const edge = 1 - 0.55 * Math.pow(i / half, 1.6);
      const h = Math.max(0.1, run.levels[i] * edge);
      bars[half - 1 - i].style.transform = `scaleY(${h.toFixed(3)})`;
      bars[half + i].style.transform = `scaleY(${h.toFixed(3)})`;
    }
    const elapsed = performance.now() - run.started;
    timeEl.textContent = clock(elapsed);
    if (elapsed >= MAX_MS) { finish(); return; }
    run.raf = requestAnimationFrame(frame);
  }

  function showOverlay(on) {
    overlay.hidden = !on;
    button.classList.toggle("is-live", on);
    button.setAttribute("aria-pressed", on ? "true" : "false");
    S.chatBox.classList.toggle("is-dictating", on);
  }

  function release(r) {
    cancelAnimationFrame(r.raf);
    if (r.stream) r.stream.getTracks().forEach(t => t.stop());
    if (r.ctx) r.ctx.close().catch(() => {});
    r.stream = r.ctx = r.analyser = null;
  }

  async function start() {
    if (run) return;
    if (!navigator.mediaDevices || !window.AudioWorkletNode) {
      picker.setHint("Dictation needs a browser with microphone access over HTTPS.", true);
      return;
    }
    const value = S.input.value;
    const from = S.input.selectionStart ?? value.length;
    const to = S.input.selectionEnd ?? value.length;
    const r = run = {
      original: value, before: value.slice(0, from), after: value.slice(to), written: value,
      phrases: new Phrases(), abort: new AbortController(), busy: false, failures: 0,
      stopping: false, detached: false, started: performance.now(), levels: new Array(BANDS).fill(0),
    };
    overlay.classList.remove("is-finishing", "is-voiced");
    overlay.classList.add("is-starting");
    bars.forEach(b => { b.style.transform = "scaleY(0.1)"; });
    timeEl.textContent = "0:00";
    showOverlay(true);
    if (matchMedia("(pointer: fine)").matches) S.input.focus({ preventScroll: true });
    try {
      r.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      if (run !== r) { release(r); return; }
      r.ctx = new AudioContext();
      await r.ctx.audioWorklet.addModule(new URL("./dictation-worklet.js", import.meta.url));
      if (run !== r) { release(r); return; }
      const source = r.ctx.createMediaStreamSource(r.stream);
      r.analyser = r.ctx.createAnalyser();
      r.analyser.fftSize = 1024;
      r.analyser.minDecibels = -88;
      r.analyser.maxDecibels = -28;
      r.analyser.smoothingTimeConstant = 0.55;
      r.freq = new Uint8Array(r.analyser.frequencyBinCount);
      r.wave = new Float32Array(r.analyser.fftSize);
      const hz = r.ctx.sampleRate / r.analyser.fftSize;
      r.binLo = Math.max(1, Math.round(150 / hz));
      r.binHi = Math.min(r.analyser.frequencyBinCount - 1, Math.round(5000 / hz));
      const capture = new AudioWorkletNode(r.ctx, "dictation-capture");
      capture.port.onmessage = (event) => {
        if (run !== r || r.stopping) return;
        r.phrases.push(event.data);
        pump();
      };
      source.connect(r.analyser);
      source.connect(capture);
      capture.connect(r.ctx.destination);
      if (r.ctx.state === "suspended") await r.ctx.resume();
      r.live = true;
    } catch (err) {
      if (run !== r) return;
      release(r);
      run = null;
      showOverlay(false);
      overlay.classList.remove("is-starting");
      picker.setHint(micError(err), true);
      return;
    }
    r.started = performance.now();
    overlay.classList.remove("is-starting");
    r.raf = requestAnimationFrame(frame);
  }

  function end() {
    const r = run;
    run = null;
    if (!r) return;
    r.abort.abort();
    release(r);
    showOverlay(false);
  }

  // Stop listening and let the last phrases finish decoding into the box.
  function finish() {
    if (!run || run.stopping) return;
    // Still waiting on the mic (permission prompt): nothing was heard yet.
    if (!run.live) { cancel(); return; }
    run.stopping = true;
    release(run);
    run.phrases.close();
    overlay.classList.add("is-finishing");
    overlay.classList.remove("is-voiced");
    pump();
  }

  function settle() {
    const r = run;
    write(r.phrases.text());
    end();
    if (!r.detached && matchMedia("(pointer: fine)").matches) S.input.focus({ preventScroll: true });
  }

  function cancel() {
    const r = run;
    if (!r) return;
    const restore = !r.detached && S.input.value === r.written;
    end();
    if (restore && r.written !== r.original) {
      S.input.value = r.original;
      S.input.dispatchEvent(new Event("input", { bubbles: true }));
    }
  }

  // The box changed under us (typing, another chat): keep it, stop dictating.
  function detach() {
    if (!run) return;
    run.detached = true;
    end();
  }

  function toggle() { if (run) finish(); else start(); }

  let pressedAt = 0;
  button.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    pressedAt = run ? 0 : performance.now();
    toggle();
  });
  button.addEventListener("pointerup", () => {
    if (pressedAt && run && performance.now() - pressedAt > HOLD_MS) finish();
    pressedAt = 0;
  });
  button.addEventListener("click", (event) => { if (event.detail === 0) toggle(); });
  overlay.querySelector("[data-dictation-cancel]").addEventListener("click", cancel);
  overlay.querySelector("[data-dictation-done]").addEventListener("click", finish);
  button.title = `Dictate · ${HOTKEY_LABEL}`;

  S.input.addEventListener("input", () => { if (run && !run.writing) detach(); });

  let hotkeyAt = 0;
  document.addEventListener("keydown", (event) => {
    const hotkey = event.code === "Space" && event.shiftKey && (MAC ? event.metaKey : event.ctrlKey) && !event.altKey;
    if (hotkey) {
      event.preventDefault();
      event.stopImmediatePropagation();
      if (event.repeat) return;
      hotkeyAt = run ? 0 : performance.now();
      toggle();
      return;
    }
    if (!run || event.isComposing) return;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopImmediatePropagation();
      cancel();
    } else if (event.key === "Enter" && !event.shiftKey && (event.target === S.input || event.target === document.body)) {
      event.preventDefault();
      event.stopImmediatePropagation();
      finish();
    }
  }, true);
  document.addEventListener("keyup", (event) => {
    // Held past HOLD_MS, the hotkey is push-to-talk: letting go finishes.
    if (!hotkeyAt || !["Space", "ShiftLeft", "ShiftRight", "ControlLeft", "ControlRight", "MetaLeft", "MetaRight"].includes(event.code)) return;
    if (run && performance.now() - hotkeyAt > HOLD_MS) finish();
    hotkeyAt = 0;
  }, true);

  return { start, finish, cancel, get active() { return Boolean(run); } };
}
