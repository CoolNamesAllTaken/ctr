// Engineer page: sends presses to the Pi (NiceGUI emitEvent 'ct' {a: action, v: value}) and paints the shared state the
// Pi pushes (ct.update(state), ~10 Hz while it changes). The Pi runs the engine and the sequences, so every browser
// shows the same thing. Push-to-talk: the microphone goes to the Pi over a WebSocket (/intercom/ws), see intercom.py.
window.ct = (() => {
  const $ = (id) => document.getElementById(id);
  const send = (a, v) => emitEvent('ct', { a, v });
  let S = {}, cfg = {};

  function holdButton(el, down, up) {
    el.addEventListener('pointerdown', (e) => { e.preventDefault(); try { el.setPointerCapture(e.pointerId); } catch (_) {} down(); });
    for (const ev of ['pointerup', 'pointercancel', 'lostpointercapture']) el.addEventListener(ev, up);
    el.addEventListener('contextmenu', (e) => e.preventDefault());
    el.addEventListener('keydown', (e) => { if ((e.key === 'Enter' || e.key === ' ') && !e.repeat) { e.preventDefault(); down(); } });
    el.addEventListener('keyup', (e) => { if (e.key === 'Enter' || e.key === ' ') up(); });
  }
  function tap(el, fn) {
    el.addEventListener('pointerdown', (e) => { e.preventDefault(); fn(); el.classList.add('flash'); setTimeout(() => el.classList.remove('flash'), 300); });
    el.addEventListener('keydown', (e) => { if ((e.key === 'Enter' || e.key === ' ') && !e.repeat) { e.preventDefault(); fn(); } });
  }

  // ---------- horn / bell / doors / power ----------
  let hornHeld = false;
  const hornDown = () => { if (hornHeld) return; hornHeld = true; $('horn').classList.add('down'); send('horn', true); };
  const hornUp = () => { if (!hornHeld) return; hornHeld = false; $('horn').classList.remove('down'); send('horn', false); };
  let doorHeld = false;
  const doorDown = () => { if (doorHeld) return; doorHeld = true; send('door', true); };
  const doorUp = () => { if (!doorHeld) return; doorHeld = false; send('door', false); };
  let thrDrag = false, thrSent = 0;
  function setThr(v) { $('throttle').value = v; $('thrOut').textContent = v + ' %'; }

  // ---------- push-to-talk ----------
  const IC = { on: false, ws: null, ctx: null, src: null, node: null, stream: null, seq: 0, idle: null, queue: [] };
  function talkPaint() {
    const b = $('talk'); if (!b) return;
    b.classList.toggle('insecure', !window.isSecureContext);
    b.classList.toggle('busy', S.talk === 'busy' && !IC.on);
    b.classList.toggle('on', IC.on && IC.live);
    b.classList.toggle('wait', IC.on && !IC.live);
    if (!window.isSecureContext) b.title = 'Talk needs the secure page: tap to open ' + cfg.https + ' (accept the certificate once)';
    else if (S.talk === 'busy' && !IC.on) b.title = 'Someone else is talking';
    else b.title = 'Hold to talk on the inside speakers (T)';
  }
  function level(v) { const b = $('talk'); if (b) b.style.setProperty('--lvl', Math.min(1, v * 4).toFixed(3)); }
  async function talkDown() {
    if (IC.on) return;
    if (!window.isSecureContext) { if (cfg.https) location.href = cfg.https; return; }
    if (S.talk === 'busy') return;
    IC.on = true; IC.live = false; IC.queue = []; clearTimeout(IC.idle); talkPaint();
    try {
      if (!IC.stream) IC.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: !cfg.raw, autoGainControl: !cfg.raw, channelCount: 1 } });
      if (!IC.ctx) {
        IC.ctx = new (window.AudioContext || window.webkitAudioContext)({ latencyHint: 'interactive' });
        await IC.ctx.audioWorklet.addModule('/static/mic-worklet.js');
        IC.src = IC.ctx.createMediaStreamSource(IC.stream);
        IC.sink = IC.ctx.createGain(); IC.sink.gain.value = 0; IC.sink.connect(IC.ctx.destination);
      }
      await IC.ctx.resume();
      if (!IC.on) return; // released while starting
      const node = new AudioWorkletNode(IC.ctx, 'caltreat-mic'); IC.node = node;
      node.port.onmessage = (ev) => {
        if (IC.node !== node) return;
        const pcm = new Int16Array(ev.data.pcm), buf = new ArrayBuffer(12 + pcm.byteLength), dv = new DataView(buf);
        dv.setUint32(0, IC.seq++, true); dv.setFloat64(4, Date.now() - 10, true); new Int16Array(buf, 12).set(pcm); // capture time of the frame's first sample
        if (IC.live && IC.ws && IC.ws.readyState === 1) IC.ws.send(buf); else if (IC.queue.length < 50) IC.queue.push(buf);
        level(ev.data.rms);
      };
      IC.src.connect(node); node.connect(IC.sink);
      const ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/intercom/ws?c=' + encodeURIComponent(cfg.client || ''));
      ws.binaryType = 'arraybuffer'; IC.ws = ws;
      ws.onmessage = (e) => {
        let m = {}; try { m = JSON.parse(e.data); } catch (_) {}
        if (m.ok) { IC.live = true; for (const b of IC.queue) ws.send(b); IC.queue = []; talkPaint(); }
        else if (m.busy || m.stop) talkUp();
      };
      ws.onclose = () => { if (IC.ws === ws) talkUp(); };
    } catch (err) {
      const b = $('talk'); if (b) b.title = 'Microphone: ' + (err && err.message || err);
      document.documentElement.dataset.talkError = String(err && err.message || err);
      talkUp();
    }
  }
  function talkUp() {
    if (!IC.on) return;
    IC.on = false; IC.live = false; IC.queue = [];
    if (IC.node) { try { IC.node.port.postMessage('stop'); IC.src.disconnect(IC.node); IC.node.disconnect(); } catch (_) {} IC.node = null; }
    if (IC.ws) { const ws = IC.ws; IC.ws = null; try { ws.close(); } catch (_) {} }
    level(0); talkPaint();
    clearTimeout(IC.idle); // keep the microphone open a little while for the next press, then let it go
    IC.idle = setTimeout(() => { if (IC.on) return; if (IC.stream) IC.stream.getTracks().forEach((t) => t.stop()); IC.stream = null; if (IC.ctx) IC.ctx.close(); IC.ctx = null; }, 20000);
  }

  // ---------- state from the Pi ----------
  let ringShown = {};
  function update(st) {
    S = Object.assign(S, st);
    if ('mph' in st) $('mph').textContent = st.mph;
    if ('hz' in st) $('hz').textContent = st.hz;
    $('lampHorn').classList.toggle('on', !!S.horn);
    $('lampBell').classList.toggle('on', !!S.bell);
    $('lampDoor').classList.toggle('on', S.doors && S.doors !== 'closed');
    const bell = $('bell'); bell.classList.toggle('on', !!S.bell); bell.setAttribute('aria-pressed', !!S.bell);
    const d = $('door'), open = S.doors === 'open' || S.doors === 'opening', moving = S.doors === 'opening' || S.doors === 'closing';
    d.classList.toggle('on', open); d.classList.toggle('moving', moving); d.setAttribute('aria-pressed', open);
    d.setAttribute('aria-label', 'Doors: ' + (S.doors || 'closed'));
    const pos = S.doorPos || 0;
    $('leafL').setAttribute('x', (5 - 18 * pos).toFixed(1)); $('leafR').setAttribute('x', (28.5 + 18 * pos).toFixed(1));
    if (!thrDrag && 'thr' in st) setThr(st.thr);
    // sequences
    const q = S.seq || {};
    for (const [n, id] of Object.entries({ depart: 'sqDepart', arrive: 'sqArrive', crossing: 'sqCrossing' })) {
      const el = $(id), on = q.active === n; el.classList.toggle('on', on); el.setAttribute('aria-pressed', on);
      const p = on ? q.progress : 0; if (ringShown[n] !== p) { ringShown[n] = p; el.querySelector('.rp').style.strokeDashoffset = (182.2 * (1 - p)).toFixed(1); }
    }
    const auto = q.active === 'auto'; $('sqAuto').setAttribute('aria-pressed', auto);
    $('seqFill').style.width = (q.active ? 100 * q.progress : 0).toFixed(1) + '%';
    $('seqStep').textContent = q.active ? (auto ? '↻ ' : '') + q.step : '';
    $('seqCancel').disabled = !q.active;
    document.documentElement.dataset.seq = q.active ? q.active + ':' + q.index : '';
    $('timing').querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', b.dataset.m === S.timing));
    document.querySelectorAll('.pat').forEach((b) => b.classList.toggle('busy', b.dataset.k === S.pattern));
    // lights
    for (const [id, mode] of Object.entries(S.lights || {})) {
      const el = $('lt_' + id); if (!el) continue;
      el.classList.toggle('on', mode === 'on'); el.classList.toggle('flash', mode === 'flash'); el.querySelector('small').textContent = mode === 'off' ? '' : mode === 'on' ? '●' : '◐';
    }
    const chip = $('status'); chip.textContent = S.err ? '!' : 'Live'; chip.classList.toggle('live', !S.err); chip.classList.toggle('err', !!S.err); chip.title = S.err || 'Connected to the train';
    talkPaint();
  }

  function init(c) {
    cfg = c;
    holdButton($('horn'), hornDown, hornUp);
    holdButton($('door'), doorDown, doorUp);
    tap($('bell'), () => send('bell'));
    tap($('air'), () => send('air'));
    tap($('stop'), () => send('stop'));
    const thr = $('throttle');
    thr.addEventListener('pointerdown', () => { thrDrag = true; });
    for (const ev of ['pointerup', 'pointercancel']) thr.addEventListener(ev, () => { thrDrag = false; });
    thr.addEventListener('input', () => { setThr(+thr.value); const now = performance.now(); if (now - thrSent > 40) { thrSent = now; } send('throttle', +thr.value); });
    thr.addEventListener('change', () => { thrDrag = false; send('throttle', +thr.value); });
    for (const [n, id] of Object.entries({ depart: 'sqDepart', arrive: 'sqArrive', crossing: 'sqCrossing', auto: 'sqAuto' })) tap($(id), () => send('seq', n));
    $('seqCancel').addEventListener('click', () => send('cancel'));
    $('timing').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) send('timing', b.dataset.m); });
    document.querySelectorAll('.pat').forEach((b) => tap(b, () => send('signal', b.dataset.k)));
    document.querySelectorAll('.lt').forEach((b) => tap(b, () => send('light', b.dataset.f)));
    const talk = $('talk');
    talk.addEventListener('pointerdown', (e) => { e.preventDefault(); try { talk.setPointerCapture(e.pointerId); } catch (_) {} talkDown(); });
    for (const ev of ['pointerup', 'pointercancel', 'pointerleave', 'lostpointercapture']) talk.addEventListener(ev, talkUp);
    talk.addEventListener('contextmenu', (e) => e.preventDefault());
    document.addEventListener('visibilitychange', () => { if (document.visibilityState !== 'visible') { hornUp(); doorUp(); talkUp(); } });
    window.addEventListener('blur', () => { hornUp(); doorUp(); });
    // keys (desktop)
    const held = {};
    window.addEventListener('keydown', (e) => {
      if (e.repeat || e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target.tagName; if (t === 'INPUT' || t === 'SELECT' || t === 'TEXTAREA') return;
      const k = e.key.toLowerCase();
      if (k === ' ' || k === 'h') { e.preventDefault(); held[k] = true; hornDown(); }
      else if (k === 't') { talkDown(); }
      else if (k === 'b') send('bell');
      else if (k === 'd') doorDown();
      else if (k === 'a') send('air');
      else if (k === 's') send('stop');
      else if (k === 'arrowup') { e.preventDefault(); send('throttle', Math.min(100, +thr.value + 10)); }
      else if (k === 'arrowdown') { e.preventDefault(); send('throttle', Math.max(0, +thr.value - 10)); }
      else if (k === '0') send('throttle', 0);
      else if (k >= '1' && k <= '9') { const b = document.querySelectorAll('.pat')[+k - 1]; if (b) send('signal', b.dataset.k); }
      else if (k === 'g') send('seq', 'depart'); else if (k === 'r') send('seq', 'arrive'); else if (k === 'x') send('seq', 'crossing'); else if (k === 'u') send('seq', 'auto');
      else if (k === 'escape') send('cancel');
      else if (k === 'l') send('light', 'head'); else if (k === 'm') send('light', 'markers');
    });
    window.addEventListener('keyup', (e) => {
      const k = e.key.toLowerCase();
      if (k === ' ' || k === 'h') { held[k] = false; if (!held[' '] && !held.h) hornUp(); }
      else if (k === 'd') doorUp();
      else if (k === 't') talkUp();
    });
    talkPaint();
  }
  return { init, update, _ic: IC };
})();
