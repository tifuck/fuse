// Dictation without the DOM: phrase splitting, WAV encoding, and where the
// dictated text goes among what the composer already held.

export const RATE = 16000;
const PER_MS = RATE / 1000;

// A mono 16-bit PCM WAV of the chunks (Int16Array each), as the server expects.
export function wav(chunks) {
  const samples = chunks.reduce((n, c) => n + c.length, 0);
  const buf = new ArrayBuffer(44 + samples * 2);
  const v = new DataView(buf);
  const text = (at, s) => { for (let i = 0; i < s.length; i++) v.setUint8(at + i, s.charCodeAt(i)); };
  text(0, "RIFF"); v.setUint32(4, 36 + samples * 2, true); text(8, "WAVE");
  text(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, RATE, true); v.setUint32(28, RATE * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  text(36, "data"); v.setUint32(40, samples * 2, true);
  let at = 44;
  for (const c of chunks) for (let i = 0; i < c.length; i++, at += 2) v.setInt16(at, c[i], true);
  return buf;
}

export function rms(chunk) {
  let sum = 0;
  for (let i = 0; i < chunk.length; i++) sum += chunk[i] * chunk[i];
  return Math.sqrt(sum / (chunk.length || 1)) / 32768;
}

// The composer around the caret, with the dictated text spliced in between.
export function splice(before, text, after) {
  const lead = before && text && !/\s$/.test(before) ? " " : "";
  const tail = after && text && !/^\s/.test(after) ? " " : "";
  const head = before + lead + text;
  return { value: head + tail + after, caret: head.length };
}

function phrase() {
  return { chunks: [], samples: 0, voicedMs: 0, quietMs: 0, text: "", sent: 0, done: false };
}

// Cuts the mic stream into phrases at pauses, so each request is one short
// phrase (the model's window is ~30 s) and a finished phrase is decoded once
// more on its whole audio. The open phrase is re-decoded as it grows: that is
// the live text.
export class Phrases {
  // A phrase ends at a long pause, or at a short one once it is long enough;
  // ending at every short pause decoded half-sentences ("Ask not. What…").
  constructor({ pauseMs = 1500, shortPauseMs = 600, minMs = 8000, softMs = 15000, maxMs = 25000 } = {}) {
    Object.assign(this, { pauseMs, shortPauseMs, minMs, softMs, maxMs });
    this.list = [];
    this.open = phrase();
    // ponytail: energy gate over a slow noise floor; swap in Silero VAD if
    // noisy rooms split phrases badly.
    this.floor = 0.004;
  }

  push(chunk) {
    const level = rms(chunk);
    const ms = chunk.length / PER_MS;
    this.floor = level < this.floor ? level : this.floor + (level - this.floor) * 0.002;
    const voiced = level > Math.max(0.012, this.floor * 3);
    const p = this.open;
    p.chunks.push(chunk);
    p.samples += chunk.length;
    if (voiced) { p.voicedMs += ms; p.quietMs = 0; } else p.quietMs += ms;
    if (p.voicedMs < 150) {
      // Not speech yet: keep only a short lead-in so the first word is whole.
      while (p.samples > 300 * PER_MS) p.samples -= p.chunks.shift().length;
      if (!voiced) p.voicedMs = 0;
      return voiced;
    }
    if (!this.list.includes(p)) this.list.push(p);
    const dur = p.samples / PER_MS;
    if (p.quietMs >= this.pauseMs || dur >= this.maxMs
      || (dur >= this.minMs && p.quietMs >= this.shortPauseMs)
      || (dur >= this.softMs && p.quietMs >= 200)) this.close();
    return voiced;
  }

  // Ends the open phrase (at a pause, or when dictation stops).
  close() {
    if (this.list.includes(this.open)) this.open.done = true;
    this.open = phrase();
  }

  // The next phrase to send: a finished one not yet decoded on all its audio,
  // else the open one once it has grown by minMs since its last decode.
  next(minMs = 500) {
    const done = this.list.find(p => p.done && p.sent < p.samples);
    if (done) return done;
    const p = this.open;
    return this.list.includes(p) && p.samples - p.sent >= minMs * PER_MS ? p : null;
  }

  settled() { return this.list.every(p => p.done && p.sent >= p.samples); }

  text() { return this.list.map(p => p.text).filter(Boolean).join(" "); }
}
