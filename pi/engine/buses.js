// vendored from caltrain-sounds app/buses.js at 5d0b32b (pi/engine/sync.sh); do not edit here
// KISS costume speaker buses: ONE definition shared by the browser page (Output selector preview, AudioWorklet or
// ScriptProcessor) and the Raspberry Pi player (pi/), so what a phone previews is what the Pi sends to that speaker.
// Plain script like engine.js (no DOM, no imports): build.py inlines it after the engine; node require()s it.
//
// Buses: FRONT = outside at the front cab end, INSIDE = inside the car (two speakers, same signal), REAR = outside at
// the back. Each bus runs its own KissEngine with its environment; the routing matrix is a gain per engine component
// (applied by wrapping the components' sample(): the engine itself is not changed and every component still runs, so
// speed, STOP timing and the auto-sander stay the same on all buses). Each output channel then has a strip: gain, mute,
// delay, high-pass (speaker protection) and a peak limiter.
const KissBuses = (() => {
  const BUSES = ['front', 'inside', 'rear'];
  const CHANNELS = ['FRONT', 'INSIDE_L', 'INSIDE_R', 'REAR'];
  const BUS_OF = { FRONT: 'front', INSIDE_L: 'inside', INSIDE_R: 'inside', REAR: 'rear' };
  const CHANNELS_OF = { front: ['FRONT'], inside: ['INSIDE_L', 'INSIDE_R'], rear: ['REAR'] };
  // outside environments a bus can use (the engine's 'Environment' presets); INSIDE is always the engine's 'inside'
  const ENV_OUT = ['dry', 'platform', 'distance', 'approaching'];
  const BUS_DEFS = { front: { env: 'dry' }, inside: { env: 'inside' }, rear: { env: 'dry' } };
  const SOUNDS = [ // key, label, engine component(s)
    ['horn', 'Horn', ['horn']],
    ['bell', 'Bell', ['bell']],
    ['doors', 'Doors (signals + mechanics)', ['door', 'doorMech']],
    ['whine', 'Motor whine', ['motor']],
    ['stop', 'Stop (squeal, brakes, thump)', ['stop']],
    ['air', 'Air release', ['hiss']],
    ['sand', 'Sander', ['sander']],
    ['rolling', 'Inside the car: HVAC, rolling, rumble, clatter', ['inside']],
    ['exterior', 'Outside: idle hum (HVAC, converter), traction, rolling, clatter', ['outside']], // v11
    ['ambient', 'Old interior ambient (v2)', ['interior']],
  ];
  // dB per sound and bus; null = off. Horn: FRONT, or REAR, or both (hornRoute), always inside (the engine muffles it).
  const DEFAULT_MATRIX = {
    horn:    { front: 0,    inside: 0,    rear: null },
    bell:    { front: 0,    inside: 0,    rear: -6 },
    doors:   { front: -3,   inside: 0,    rear: -3 },
    whine:   { front: -8,   inside: 0,    rear: 0 },
    stop:    { front: -6,   inside: 0,    rear: 0 },
    air:     { front: -6,   inside: 0,    rear: 0 },
    sand:    { front: -6,   inside: 0,    rear: 0 },
    rolling: { front: null, inside: 0,    rear: null }, // v11: outside buses play 'exterior' instead
    exterior: { front: 0,   inside: 0,    rear: 0 },    // v11; INSIDE: only through the open doorway (the car has its own model)
    ambient: { front: null, inside: null, rear: null },
  };
  // channel strips (speakers: Alpine SXE-1726S 6.5" 40 W, 60 Hz-20 kHz, via a Fosi CA30 amp; pi/README.md)
  const DEFAULT_OUTPUTS = {
    FRONT:    { gainDb: 0,  mute: false, delayMs: 0, highpassHz: 80, limiterDb: -1 },
    INSIDE_L: { gainDb: -4, mute: false, delayMs: 0, highpassHz: 80, limiterDb: -12 },
    INSIDE_R: { gainDb: -4, mute: false, delayMs: 0, highpassHz: 80, limiterDb: -12 },
    REAR:     { gainDb: 0,  mute: false, delayMs: 0, highpassHz: 80, limiterDb: -1 },
  };
  const toGain = (d) => (d == null ? 0 : Math.pow(10, d / 20));
  const clone = (o) => JSON.parse(JSON.stringify(o));
  // the matrix with the horn route applied: 'front' | 'rear' | 'both'
  function effectiveMatrix(matrix, hornRoute) {
    const m = clone(matrix || DEFAULT_MATRIX), r = hornRoute || 'front', h = m.horn || (m.horn = {});
    for (const s in DEFAULT_MATRIX) if (!m[s]) m[s] = clone(DEFAULT_MATRIX[s]); // a matrix saved before a sound existed (v11: exterior)
    const base = h.front != null ? h.front : h.rear != null ? h.rear : 0;
    h.front = r === 'rear' ? null : (h.front != null ? h.front : base); h.rear = r === 'front' ? null : (h.rear != null ? h.rear : base);
    return m;
  }

  class Bus {
    constructor(KissEngine, sr, name, opts = {}) {
      this.name = name; this.sr = sr; this.e = new KissEngine(sr, opts.seed || 12345); this.env = null;
      this.g = {}; this.k = 1 - Math.exp(-1 / (0.02 * sr)); // 20 ms gain smoothing
      for (const [key, , comps] of SOUNDS) { this.g[key] = 0; for (const c of comps) this.wrap(c, key); }
      this.setEnv(name === 'inside' ? 'inside' : opts.env);
      this.e.msg({ t: 'exterior', on: true, end: name === 'rear' ? 'rear' : 'front' }); // v11: the exterior soundscape, per end
    }
    wrap(cname, key) {
      // closure-local gain state (no keyed object writes per sample); the prototype method is called directly (a bound
      // function would not inline)
      const c = this.e[cname], proto = Object.getPrototypeOf(c).sample, self = this, e = this.e, k = this.k;
      const skippable = cname === 'horn' || cname === 'outside', isInside = cname === 'inside', hasExt = 'ext' in c; // .ext: a component's exterior part (engine: open doorway), same bus gain
      let s = 0;
      c.sample = function () {
        const t = self.g[key];
        if (s !== t) { s += (t - s) * k; if (Math.abs(s - t) < 1e-6) s = t; }
        if (skippable && t === 0 && s === 0) return 0; // horn / exterior routed off this bus: skip its DSP
        if (isInside && self.env !== 'inside') { c.speed = e.motor.speed; c.braking = e.motor.stopping || e.motor.regen > 0.2; }
        const y = proto.call(c);
        if (hasExt) c.ext *= s;
        return s === 0 ? 0 : y * s;
      };
    }
    setEnv(env) { // outside buses: one of ENV_OUT (live, no rebuild)
      if (this.name === 'inside') env = 'inside'; else if (!ENV_OUT.includes(env)) env = BUS_DEFS[this.name] ? BUS_DEFS[this.name].env : 'dry';
      if (env === this.env) return; this.env = env; this.e.msg({ t: 'env', v: env }); this.exteriorRolling();
    }
    // v11: outside buses no longer play the inside car's layers (v10.2 ran its rolling table outside): the engine's
    // Outside component is the exterior (idle hum, traction, rolling, clatter), its layers set by the Layers panel
    // ('auto' = tied to the train's state, 'on' forced, 'off'); the inside car's model stays silent outside
    exteriorRolling() { if (this.env === 'inside') return; const ins = this.e.inside; ins.inside = false; ins.on = false; for (const k in ins.mode) ins.mode[k] = 'auto'; }
    setGains(row) { for (const [key] of SOUNDS) if (key in row) this.g[key] = toGain(row[key]); this.exteriorRolling(); }
    // control messages: env is per bus; on an outside bus the layers go to the exterior model only
    msg(m) {
      if (m.t === 'env') return;
      if (m.t === 'layer' && m.k !== 'whine' && this.env !== 'inside') {
        const o = this.e.outside; if (m.v != null && m.k in o.lv) o.lv[m.k] = Math.max(0, Math.min(3, +m.v));
        if (m.mode && m.k in o.mode) o.mode[m.k] = String(m.mode); return; }
      this.e.msg(m);
    }
    render(buf, n) { this.e.render(buf, n); }
  }

  // ---- channel strip: gain, mute, delay, high-pass (4th-order Butterworth), peak limiter (2 ms look-ahead) ----
  class HP {
    constructor(f, q, sr) { const w = 2 * Math.PI * f / sr, c = Math.cos(w), al = Math.sin(w) / (2 * q), a0 = 1 + al;
      this.b0 = (1 + c) / 2 / a0; this.b1 = -(1 + c) / a0; this.b2 = this.b0; this.a1 = -2 * c / a0; this.a2 = (1 - al) / a0; this.z1 = 0; this.z2 = 0; }
    run(x) { const y = this.b0 * x + this.z1; this.z1 = this.b1 * x - this.a1 * y + this.z2; this.z2 = this.b2 * x - this.a2 * y; return y; }
  }
  const dbToLin = (d) => Math.pow(10, d / 20);
  class Strip {
    constructor(sr) { this.sr = sr; this.hpf = 0; this.f = []; this.gS = 0; this.dl = new Float32Array(1); this.di = 0; this.dn = 0;
      this.la = Math.max(1, Math.round(0.002 * sr)); this.lbuf = new Float32Array(this.la); this.li = 0; this.env = 1; this.holdG = 1; this.holdN = 0;
      this.rel = 1 - Math.exp(-1 / (0.15 * sr)); this.ceil = 1; this.peak = 0; this.gr = 0; this.set({}); }
    set(c) {
      this.gain = c.mute ? 0 : dbToLin(c.gainDb || 0);
      if ((c.highpassHz || 0) !== this.hpf) { this.hpf = c.highpassHz || 0; this.f = this.hpf > 0 ? [new HP(this.hpf, 0.5412, this.sr), new HP(this.hpf, 1.3066, this.sr)] : []; }
      const n = Math.round((c.delayMs || 0) * this.sr / 1000);
      if (n !== this.dn) { this.dn = n; this.dl = new Float32Array(n + 1); this.di = 0; }
      this.ceil = dbToLin(c.limiterDb == null ? -1 : c.limiterDb);
    }
    // x: input block; writes out[off + i * stride] (one channel of an interleaved buffer, or a plain channel: 1, 0)
    process(x, n, out, stride, off) {
      const g = this.gain, f = this.f, dl = this.dl, D = this.dn, la = this.la, lb = this.lbuf, ceil = this.ceil;
      let peak = this.peak, gs = this.gS, env = this.env, minG = 1;
      for (let i = 0; i < n; i++) {
        gs += (g - gs) * 0.002;
        let v = x[i] * gs;
        for (let k = 0; k < f.length; k++) v = f[k].run(v);
        if (D) { dl[this.di] = v; this.di = this.di >= D ? 0 : this.di + 1; v = dl[this.di]; }
        const a = Math.abs(v), need = a > ceil ? ceil / a : 1;
        if (need <= this.holdG) { this.holdG = need; this.holdN = la; } else if (--this.holdN <= 0) { this.holdG = need; this.holdN = la; } // peak hold over the look-ahead
        const target = this.holdG;
        env = target < env ? target : env + (target - env) * this.rel;
        const d = lb[this.li]; lb[this.li] = v; this.li = this.li + 1 === la ? 0 : this.li + 1;
        let y = d * env; if (y > ceil) y = ceil; else if (y < -ceil) y = -ceil;
        out[off + i * stride] = y;
        const ay = Math.abs(y); if (ay > peak) peak = ay; if (env < minG) minG = env;
      }
      this.peak = peak; this.gS = gs; this.env = env; this.gr = Math.min(this.gr, 20 * Math.log10(minG));
    }
    meter() { const p = this.peak, gr = this.gr; this.peak = 0; this.gr = 0; return { peakDb: p > 0 ? +(20 * Math.log10(p)).toFixed(1) : -120, grDb: +gr.toFixed(1) }; }
  }
  // test / identify signals: 'identify' = N beeps (N = channel number) then pink-ish noise; 'sine' = 1 kHz (amp gain
  // setting with a meter); 'sweep' = 50 Hz - 10 kHz
  class TestSignal {
    constructor(sr, kind, index, levelDb = -12) {
      this.sr = sr; this.kind = kind; this.idx = index; this.t = 0; this.ph = 0; this.lv = dbToLin(levelDb); this.rng = 1234567;
      this.b = [0, 0, 0]; this.dur = kind === 'identify' ? 0.3 * (index + 1) + 2.0 : kind === 'sweep' ? 6 : 5;
    }
    done() { return this.t >= this.dur * this.sr; }
    sample() {
      const t = this.t++ / this.sr; let y = 0;
      if (this.kind === 'identify') {
        const nb = this.idx + 1, f = [440, 554, 659, 880][this.idx % 4] || 440;
        if (t < 0.3 * nb) { const u = t % 0.3; if (u < 0.18) { this.ph += f / this.sr; y = Math.sin(2 * Math.PI * this.ph) * Math.min(1, u / 0.005, (0.18 - u) / 0.005); } }
        else if (t > 0.3 * nb + 0.3 && t < this.dur - 0.1) { // Paul Kellet's economy pink filter
          this.rng = (this.rng * 1664525 + 1013904223) >>> 0; const w = this.rng / 2147483648 - 1, b = this.b;
          b[0] = 0.99765 * b[0] + w * 0.0990460; b[1] = 0.96300 * b[1] + w * 0.2965164; b[2] = 0.57000 * b[2] + w * 1.0526913;
          y = (b[0] + b[1] + b[2] + w * 0.1848) * 0.25;
        }
      } else if (this.kind === 'sine') { this.ph += 1000 / this.sr; y = Math.sin(2 * Math.PI * this.ph); }
      else { const f = 50 * Math.pow(200, t / this.dur); this.ph += f / this.sr; y = Math.sin(2 * Math.PI * this.ph) * Math.min(1, t / 0.05, (this.dur - t) / 0.05); }
      if (this.ph > 1) this.ph -= 1;
      return y * this.lv;
    }
  }

  // ---- browser preview: what the Pi sends to one speaker (or all four on a 4+ channel device) ----
  // Messages: the engine's, plus {t:'output', v:'front'|'inside'|'rear'|'all'}, {t:'busEnv', bus, env},
  // {t:'busConfig', matrix?, hornRoute?, outputs?, buses?} (the remote page passes the Pi's settings).
  // Only the selected bus runs (all three for 'all'); a bus started on a switch gets the running one's motor state and
  // the last value of every setting, so the train keeps its speed.
  const STATE_KEYS = { master: 1, accel: 1, doorStyle: 1, autoSand: 1, throttle: 1, bell: 1, door: 1, horn: 1, doorSamples: 1, doorSound: 1 }; // replayed to a bus started later
  class Preview {
    constructor(KissEngine, sr) {
      this.KE = KissEngine; this.sr = sr; this.mode = null; this.buses = {}; this.strips = {}; this.log = new Map();
      this.cfg = { matrix: clone(DEFAULT_MATRIX), hornRoute: 'front', outputs: clone(DEFAULT_OUTPUTS), buses: clone(BUS_DEFS) };
      this.bufs = {}; this.setMode('front');
    }
    keyOf(m) { return m.t === 'intensity' || m.t === 'layer' ? m.t + ':' + m.k : STATE_KEYS[m.t] ? m.t : null; }
    setMode(v) {
      if (!['front', 'inside', 'rear', 'all'].includes(v) || v === this.mode) return;
      this.mode = v; const need = v === 'all' ? BUSES : [v], prev = Object.values(this.buses)[0];
      for (const b of need) if (!this.buses[b]) {
        const bus = new Bus(this.KE, this.sr, b, { env: (this.cfg.buses[b] || {}).env }); this.applyGains(bus);
        for (const m of this.log.values()) bus.msg(m);
        if (prev) { const s = prev.e.motor, d = bus.e.motor; for (const k of Object.keys(s)) if (typeof s[k] === 'number') d[k] = s[k];
          const st = prev.e.state(); if (st.doors === 'open' || st.doors === 'opening') bus.msg({ t: 'doors', v: 'open' }); }
        this.buses[b] = bus;
      }
      for (const b of Object.keys(this.buses)) if (!need.includes(b)) delete this.buses[b];
      for (const ch of CHANNELS) if (need.includes(BUS_OF[ch]) && !this.strips[ch]) { this.strips[ch] = new Strip(this.sr); this.strips[ch].set(this.cfg.outputs[ch] || {}); }
    }
    applyGains(bus) { const mx = effectiveMatrix(this.cfg.matrix, this.cfg.hornRoute), row = {}; for (const s in mx) row[s] = mx[s][bus.name]; bus.setGains(row); }
    msg(m) {
      if (m.t === 'output') return this.setMode(String(m.v));
      if (m.t === 'busEnv') { if (this.cfg.buses[m.bus]) this.cfg.buses[m.bus].env = m.env; if (this.buses[m.bus]) this.buses[m.bus].setEnv(m.env); return; }
      if (m.t === 'busConfig') {
        if (m.matrix) this.cfg.matrix = m.matrix; if (m.hornRoute) this.cfg.hornRoute = m.hornRoute;
        if (m.outputs) { this.cfg.outputs = Object.assign(clone(DEFAULT_OUTPUTS), m.outputs); for (const ch in this.strips) this.strips[ch].set(this.cfg.outputs[ch] || {}); }
        if (m.buses) for (const b in m.buses) if (this.cfg.buses[b] && m.buses[b].env) { this.cfg.buses[b].env = m.buses[b].env; if (this.buses[b]) this.buses[b].setEnv(m.buses[b].env); }
        for (const b in this.buses) this.applyGains(this.buses[b]); return;
      }
      const k = this.keyOf(m); if (k) this.log.set(k, m);
      for (const b in this.buses) this.buses[b].msg(m);
    }
    // outs: channel buffers (Float32Array each). Single bus: its channel(s) on outs[0] / outs[1] (FRONT and REAR on
    // both sides), others silent. 'all': FRONT, INSIDE_L, INSIDE_R, REAR on outs[0..3].
    render(outs, n) {
      for (const b in this.buses) { let x = this.bufs[b]; if (!x || x.length < n) x = this.bufs[b] = new Float32Array(n); this.buses[b].render(x, n); }
      const map = this.mode === 'all' ? CHANNELS : this.mode === 'inside' ? ['INSIDE_L', 'INSIDE_R'] : [CHANNELS_OF[this.mode][0], CHANNELS_OF[this.mode][0]];
      for (let c = 0; c < outs.length; c++) {
        const ch = map[c]; if (!ch) { outs[c].fill(0); continue; }
        if (c > 0 && map[c] === map[c - 1]) { outs[c].set(outs[c - 1].subarray(0, n)); continue; }
        this.strips[ch].process(this.bufs[BUS_OF[ch]], n, outs[c], 1, 0);
      }
    }
    state() { const b = this.buses[this.mode] || Object.values(this.buses)[0]; return b ? b.e.state() : { speed: 0, A: 0 }; }
  }

  return { BUSES, CHANNELS, BUS_OF, CHANNELS_OF, ENV_OUT, BUS_DEFS, SOUNDS, DEFAULT_MATRIX, DEFAULT_OUTPUTS, toGain, effectiveMatrix,
    Bus, Strip, TestSignal, Preview, dbToLin };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = KissBuses;
