// Dictation core: WAV framing, splicing into the composer, phrase splitting.
const { test } = require('node:test');
const assert = require('node:assert/strict');

const core = () => import('../static/chat/dictation-core.js');

function feed(phrases, ms, amp) {
  for (let t = 0; t < ms; t += 20) {
    const chunk = new Int16Array(320);
    for (let i = 0; i < chunk.length; i++) chunk[i] = Math.round(amp * 32767 * Math.sin((t * 16 + i) / 6));
    phrases.push(chunk);
  }
}

test('wav frames 16 kHz mono 16-bit PCM', async () => {
  const { wav, RATE } = await core();
  const buf = wav([new Int16Array([1, -2]), new Int16Array([3])]);
  const v = new DataView(buf);
  assert.equal(String.fromCharCode(...new Uint8Array(buf, 0, 4)), 'RIFF');
  assert.equal(String.fromCharCode(...new Uint8Array(buf, 8, 4)), 'WAVE');
  assert.equal(v.getUint16(22, true), 1);
  assert.equal(v.getUint32(24, true), RATE);
  assert.equal(v.getUint16(34, true), 16);
  assert.equal(v.getUint32(40, true), 6);
  assert.deepEqual([v.getInt16(44, true), v.getInt16(46, true), v.getInt16(48, true)], [1, -2, 3]);
});

test('splice spaces dictated text into the composer', async () => {
  const { splice } = await core();
  assert.deepEqual(splice('', 'hello', ''), { value: 'hello', caret: 5 });
  assert.deepEqual(splice('Fix', 'the bug', ''), { value: 'Fix the bug', caret: 11 });
  assert.deepEqual(splice('a ', 'b', 'c'), { value: 'a b c', caret: 3 });
  assert.deepEqual(splice('x', '', 'y'), { value: 'xy', caret: 1 });
});

test('phrases open on speech, grow, and close at a pause', async () => {
  const { Phrases } = await core();
  const p = new Phrases();
  feed(p, 600, 0);
  assert.equal(p.list.length, 0);
  assert.equal(p.next(), null);
  assert.ok(p.settled());

  feed(p, 1000, 0.3);
  assert.equal(p.list.length, 1);
  const first = p.next();
  assert.equal(first, p.open);
  first.sent = first.samples;
  first.text = 'one';
  assert.equal(p.next(), null, 'no re-decode until it grows');

  feed(p, 700, 0);
  assert.ok(!first.done, 'a short pause inside a short phrase keeps it open');
  feed(p, 900, 0);
  assert.ok(first.done, 'a long pause ends the phrase');
  assert.equal(p.next(), first, 'its whole audio is decoded once more');
  first.sent = first.samples;
  assert.ok(p.settled());

  feed(p, 600, 0.3);
  p.close();
  assert.equal(p.list.length, 2);
  assert.equal(p.next(), p.list[1]);
  p.list[1].text = 'two';
  p.list[1].sent = p.list[1].samples;
  assert.ok(p.settled());
  assert.equal(p.text(), 'one two');
});

test('a short pause ends a long phrase', async () => {
  const { Phrases } = await core();
  const p = new Phrases({ minMs: 2000 });
  feed(p, 2200, 0.3);
  feed(p, 650, 0);
  assert.ok(p.list[0].done);
});

test('a click is not speech', async () => {
  const { Phrases } = await core();
  const p = new Phrases();
  feed(p, 60, 0.5);
  feed(p, 1600, 0);
  p.close();
  assert.equal(p.list.length, 0);
  assert.ok(p.settled());
});

test('long speech is cut before the model window', async () => {
  const { Phrases } = await core();
  const p = new Phrases({ maxMs: 3000, softMs: 2000 });
  feed(p, 3200, 0.3);
  assert.ok(p.list[0].done);
  assert.ok(p.list[0].samples <= 3000 * 16 + 320);
});
