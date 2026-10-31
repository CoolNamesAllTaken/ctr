// Push-to-talk capture: the microphone at the context's rate -> 16 kHz mono int16, 20 ms (320-sample) frames, posted
// to the page with their RMS level. A 2nd-order low-pass at 7 kHz before the linear-interpolation resampler keeps
// aliasing down (the input is speech).
class MicProc extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000; this.pos = 0; this.prev = 0;
    this.frame = new Int16Array(320); this.n = 0; this.sum = 0;
    const w = 2 * Math.PI * Math.min(7000, sampleRate * 0.45) / sampleRate, c = Math.cos(w), al = Math.sin(w) / (2 * 0.707), a0 = 1 + al;
    this.b = [(1 - c) / 2 / a0, (1 - c) / a0, (1 - c) / 2 / a0]; this.a = [-2 * c / a0, (1 - al) / a0];
    this.z1 = 0; this.z2 = 0; this.on = true;
    this.port.onmessage = (e) => { if (e.data === 'stop') this.on = false; };
  }
  process(inputs) {
    const x = inputs[0] && inputs[0][0];
    if (!x) return this.on;
    const [b0, b1, b2] = this.b, [a1, a2] = this.a;
    for (let i = 0; i < x.length; i++) {
      const y = b0 * x[i] + this.z1; this.z1 = b1 * x[i] - a1 * y + this.z2; this.z2 = b2 * x[i] - a2 * y; // DF2T biquad
      // emit every output sample whose time falls between the previous input sample and this one
      while (this.pos <= 1) {
        const v = this.prev + (y - this.prev) * this.pos, s = Math.max(-1, Math.min(1, v));
        this.frame[this.n++] = s < 0 ? s * 32768 : s * 32767; this.sum += s * s;
        if (this.n === 320) {
          this.port.postMessage({ pcm: this.frame.buffer, rms: Math.sqrt(this.sum / 320) }, [this.frame.buffer]);
          this.frame = new Int16Array(320); this.n = 0; this.sum = 0;
        }
        this.pos += this.ratio;
      }
      this.pos -= 1; this.prev = y;
    }
    return this.on;
  }
}
registerProcessor('caltreat-mic', MicProc);
