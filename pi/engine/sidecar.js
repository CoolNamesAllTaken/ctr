// Caltreat sound sidecar: the v12 KISS engine (engine.js + buses.js, vendored from caltrain-sounds) for the Python
// app. Started and supervised by caltreat (src/caltreat/sidecar.py); node 18+ (Debian Bookworm's apt nodejs works).
//
//   stdin  (JSON lines)  engine / bus messages, exactly what the v12 page sends: {"t":"horn","on":true},
//                        {"t":"doors","v":"toggle"}, {"t":"intensity","k":"bell","v":0.75}, {"t":"busEnv","bus":"front",
//                        "env":"dry"}, ...; {"t":"doorFiles","dir":..., "name":...} loads the recorded doors (WAV + JSON);
//                        "at": <sample> on a message applies it when that sample is rendered (tests, exact timing);
//                        {"t":"estop"}: EMERGENCY STOP, handled here (the vendored engine has no instant stop): power
//                        off, speed 0 at once, horn and patterns off, the brake-air dump and, if it was moving, the squeal.
//   stdout (binary)      blocks of BLOCK frames, float32 little endian, interleaved [front, inside, rear]. Written
//                        with blocking writes, so the app's reading pace (its sound cards' clock) paces the engines;
//                        the app keeps the pipe small, so little audio is in flight.
//   fd $CALTREAT_STATE_FD (default 3), JSON lines:  {"ready":true}, engine state ~20/s ({"speed","A","patternBusy","doors","doorPos",
//                        "doorSound","stopping"}), {"error":...}
//
// One worker thread per bus (FRONT, INSIDE, REAR: a Pi 4 needs a core per bus), each running KissBuses.Preview set to
// its bus: exactly what the v12 page's Output selector plays for that speaker. Each renders up to DEPTH blocks ahead
// into a SharedArrayBuffer ring; a mux thread takes one block from each ring and writes it out.
const { Worker, isMainThread, parentPort, workerData, receiveMessageOnPort } = require('worker_threads');
const fs = require('fs');
const path = require('path');

const BUSES = ['front', 'inside', 'rear'];
const BLOCK = +(process.env.CALTREAT_ENGINE_BLOCK || 512);
const SR = +(process.env.CALTREAT_ENGINE_RATE || 48000);
const DEPTH = +(process.env.CALTREAT_ENGINE_DEPTH || 2); // blocks rendered ahead per bus (2: a slow block (GC) doesn't drop out)
const SLOTS = 4;
const WARMUP = process.env.CALTREAT_ENGINE_WARMUP !== '0';

function loadEngine() {
  const src = fs.readFileSync(path.join(__dirname, 'engine.js'), 'utf8');
  return new Function(src + '\nreturn KissEngine;')();
}

// ring: ctrl[0] blocks written, ctrl[1] blocks read
function ringOf(d) { return { data: new Float32Array(d.sab), c: new Int32Array(d.ctrl) }; }

if (isMainThread) {
  const rings = BUSES.map(() => ({ sab: new SharedArrayBuffer(SLOTS * BLOCK * 4), ctrl: new SharedArrayBuffer(8) }));
  const fd = process.env.CALTREAT_STATE_FD != null ? +process.env.CALTREAT_STATE_FD : 3;
  const stateOut = (() => { try { fs.fstatSync(fd); return fd; } catch (e) { return null; } })();
  const say = (o) => { if (stateOut !== null) { try { fs.writeSync(stateOut, JSON.stringify(o) + '\n'); } catch (e) { process.exit(0); } } };
  const workers = BUSES.map((bus, i) => new Worker(__filename, { workerData: { role: 'bus', bus, ring: rings[i] }, resourceLimits: { maxYoungGenerationSizeMb: 96 } }));
  let warm = 0;
  for (const w of workers) {
    w.on('message', (m) => {
      if (m.warm != null) { if (++warm === BUSES.length) { mux.postMessage({ go: true }); say({ ready: true, warmMs: m.warm }); } }
      else if (m.state) say(m.state);
      else if (m.error) say({ error: m.error });
    });
    w.on('error', (e) => { say({ error: String(e && e.stack || e) }); process.exit(2); });
  }
  const mux = new Worker(__filename, { workerData: { role: 'mux', rings } });
  mux.on('error', (e) => { say({ error: String(e && e.stack || e) }); process.exit(2); });
  // control messages: every bus gets every message (bus-specific ones are handled by Preview: busEnv)
  let buf = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', (d) => {
    buf += d; let i;
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
      if (!line) continue;
      let m; try { m = JSON.parse(line); } catch (e) { say({ error: 'bad message: ' + line.slice(0, 80) }); continue; }
      for (const w of workers) w.postMessage(m);
    }
  });
  process.stdin.on('end', () => process.exit(0)); // the app went away
} else if (workerData.role === 'bus') {
  const KissEngine = loadEngine();
  const KissBuses = require('./buses.js');
  const { loadDoorSamples } = require('./load.js');
  const r = ringOf(workerData.ring), bus = workerData.bus;
  let p = new KissBuses.Preview(KissEngine, SR);
  p.msg({ t: 'output', v: bus });
  const tmp = [new Float32Array(BLOCK), new Float32Array(BLOCK)];
  let rendered = 0; // samples rendered since go-live
  const later = []; // messages with "at"
  function estop() {
    for (const b of Object.values(p.buses)) {
      const e = b.e, moving = e.motor.speed > 0.02;
      e.msg({ t: 'throttle', v: 0 }); e.msg({ t: 'stopPattern' }); e.msg({ t: 'horn', on: false });
      e.motor.speed = 0; e.motor.stopping = false; e.motor.regen = 0;
      if (moving && !e.stop.on) e.msg({ t: 'squeal' }); // the last seconds of a stop: squeal, thump, air
      e.msg({ t: 'hiss' }); // the brake pipe venting
    }
  }
  function apply(m) {
    if (m.t === 'estop') return estop();
    if (m.t === 'doorFiles') {
      try { const ds = loadDoorSamples(m.dir, m.name || 'doors_v10'); p.msg({ t: 'doorSamples', pcm: ds.pcm, meta: ds.meta }); }
      catch (e) { if (bus === 'front') parentPort.postMessage({ error: 'door recording: ' + e.message }); }
      return;
    }
    p.msg(m);
  }
  function drain() {
    let x;
    while ((x = receiveMessageOnPort(parentPort))) {
      const m = x.message;
      if (m.at != null) later.push(m); else apply(m);
    }
    if (later.length) { later.sort((a, b) => a.at - b.at); while (later.length && later[0].at <= rendered) apply(later.shift()); }
  }
  // Warm-up of the LIVE engine (its output is thrown away): V8 optimizes the paths the engine takes at first use;
  // without it the first bell / STOP / pattern of a session ran 2-4x slower on a Pi 4, and warming a throwaway copy
  // did not help (caltrain-sounds pi/README.md). Then back to rest.
  const t0 = Date.now();
  if (WARMUP) {
    const score = [[0, { t: 'bell', on: true }], [0.2, { t: 'horn', on: true }], [1.5, { t: 'horn', on: false }],
      [1.6, { t: 'pattern', seq: [[0, 0.5], [0.8, 0.5]] }], [2, { t: 'doors', v: 'open' }], [3, { t: 'accel', v: 2 }],
      [3, { t: 'throttle', v: 1 }], [4, { t: 'hiss' }], [5, { t: 'sand' }], [5.5, { t: 'clatter' }], [7, { t: 'stop' }],
      [8, { t: 'squeal' }], [9, { t: 'doors', v: 'close' }], [9.5, { t: 'bell', on: false }], [12, { t: 'throttle', v: 0 }]];
    let k = 0;
    for (let i = 0; i < 20 * SR; i += BLOCK) {
      while (k < score.length && score[k][0] * SR <= i) p.msg(score[k++][1]);
      p.render(tmp, BLOCK);
    }
    // back to rest: doors shut, power off, tails decayed (the app then sends its settings)
    for (const m of [{ t: 'horn', on: false }, { t: 'stopPattern' }, { t: 'bell', on: false }, { t: 'throttle', v: 0 },
      { t: 'doors', v: 'close' }]) p.msg(m);
    for (let i = 0; i < 12 * SR; i += BLOCK) p.render(tmp, BLOCK);
  }
  parentPort.postMessage({ warm: Date.now() - t0 });
  let nextState = 0;
  for (;;) {
    drain();
    const fill = Atomics.load(r.c, 0) - Atomics.load(r.c, 1);
    if (fill < DEPTH) {
      const w = Atomics.load(r.c, 0);
      p.render(tmp, BLOCK);
      r.data.set(tmp[0], (w % SLOTS) * BLOCK);
      rendered += BLOCK;
      Atomics.add(r.c, 0, 1); Atomics.notify(r.c, 0);
      if (bus === 'front' && rendered >= nextState) { // ~20 state messages a second (by audio time)
        nextState = rendered + Math.round(SR / 20);
        const e = p.buses.front.e, st = e.state();
        st.stopping = !!e.motor.stopping; st.horn = !!e.manualHorn || st.patternBusy; st.t = rendered;
        parentPort.postMessage({ state: st });
      }
    } else {
      Atomics.wait(r.c, 1, Atomics.load(r.c, 1), 5); // until the mux takes a block (or 5 ms, for messages)
    }
  }
} else if (workerData.role === 'mux') {
  const rings = workerData.rings.map(ringOf), out = Buffer.alloc(BLOCK * BUSES.length * 4);
  const f = new Float32Array(out.buffer, out.byteOffset, BLOCK * BUSES.length);
  const go = new Int32Array(new SharedArrayBuffer(4));
  // wait for the warm-up to finish
  while (!receiveMessageOnPort(parentPort)) Atomics.wait(go, 0, 0, 20);
  for (;;) {
    for (let b = 0; b < rings.length; b++) {
      const r = rings[b];
      while (Atomics.load(r.c, 0) - Atomics.load(r.c, 1) < 1) Atomics.wait(r.c, 0, Atomics.load(r.c, 0), 50);
      const rd = Atomics.load(r.c, 1), off = (rd % SLOTS) * BLOCK, d = r.data;
      for (let i = 0; i < BLOCK; i++) f[i * 3 + b] = d[off + i];
      Atomics.add(r.c, 1, 1); Atomics.notify(r.c, 1);
    }
    let o = 0;
    while (o < out.length) { // blocks while the app's pipe is full
      try { o += fs.writeSync(1, out, o, out.length - o); }
      catch (e) { if (e.code === 'EAGAIN') Atomics.wait(go, 0, 0, 1); else process.exit(0); }
    }
  }
}
