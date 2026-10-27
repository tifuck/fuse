// Dictation mic capture: average the input down to 16 kHz mono and post
// 20 ms Int16 chunks to the page.
class DictationCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = sampleRate / 16000;
    this.acc = 0;
    this.sum = 0;
    this.count = 0;
    this.out = new Int16Array(320);
    this.n = 0;
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this.sum += ch[i];
      this.count += 1;
      this.acc += 1;
      if (this.acc < this.step) continue;
      this.acc -= this.step;
      const v = Math.max(-1, Math.min(1, this.sum / this.count));
      this.sum = 0;
      this.count = 0;
      this.out[this.n++] = v < 0 ? v * 32768 : v * 32767;
      if (this.n === this.out.length) {
        this.port.postMessage(this.out, [this.out.buffer]);
        this.out = new Int16Array(320);
        this.n = 0;
      }
    }
    return true;
  }
}

registerProcessor("dictation-capture", DictationCapture);
