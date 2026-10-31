// Engineer page: EMERGENCY STOP and the stack lights (safety.py). Wraps ct.update (cab.js) like cars.js does.
// State from the Pi: {estop: 0 | activation number, stack: green | yellow | red | estop, blue: off | on | flash}.
//
// E-STOP is press-and-hold: 0.3 s (the ring fills) sends it; letting go earlier does nothing, so a brush of a
// pocket or a sleeve doesn't stop the show, but a real press is still a fraction of a second. Key: hold E.
// While latched, holding the same button 2 s (or tapping ↺ in the banner) resets it; power, Depart / Arrive /
// Crossing / Auto run and closing the doors are locked on the page (and refused by the Pi).
(() => {
  const $ = (id) => document.getElementById(id);
  const ARM_MS = 300, RESET_MS = 2000;
  let S = { estop: 0, stack: 'red', blue: 'off' }, timer = null;

  function paint() {
    const on = !!S.estop, btn = $('estop'), ban = $('esBan');
    document.documentElement.dataset.estop = on ? '1' : '';
    if (ban) ban.hidden = !on;
    if (btn) {
      btn.classList.toggle('latched', on);
      btn.setAttribute('aria-label', on ? 'Emergency stop latched: hold 2 s to reset' : 'Emergency stop: hold');
      $('esHint').textContent = on ? '↺ hold 2 s' : 'hold';
    }
    const st = $('stack');
    if (st) { st.dataset.s = S.stack || 'red'; st.dataset.blue = S.blue || 'off'; }
    const thr = $('throttle'); if (thr) thr.disabled = on;
    for (const id of ['sqDepart', 'sqArrive', 'sqCrossing', 'sqAuto']) { const b = $(id); if (b) b.classList.toggle('locked', on); }
    const d = $('door'); if (d) d.classList.toggle('locked', on && d.classList.contains('on'));
  }

  function disarm() {
    clearTimeout(timer); timer = null;
    const b = $('estop'); if (b) { b.classList.remove('arming'); b.style.removeProperty('--hold'); }
  }
  function arm() {
    if (timer) return;
    const b = $('estop'), ms = S.estop ? RESET_MS : ARM_MS;
    b.style.setProperty('--hold', ms + 'ms'); b.classList.add('arming');
    timer = setTimeout(() => {
      timer = null; b.classList.remove('arming');
      emitEvent('ct', { a: S.estop ? 'estop_reset' : 'estop' });
      if (!S.estop) { // show it at once; the Pi's state follows (or, if the press never got there, it goes back)
        S.estop = -1; paint();
        setTimeout(() => { if (S.estop === -1) { S.estop = 0; paint(); } }, 1500);
      }
    }, ms);
  }

  function hook() {
    if (!window.ct || ct._estop) return !!(window.ct && ct._estop);
    const orig = ct.update;
    ct.update = (st) => {
      orig(st);
      for (const k of ['estop', 'stack', 'blue']) if (k in st) S[k] = st[k];
      paint();
    };
    ct._estop = true;
    return true;
  }
  function wire() {
    const b = $('estop');
    if (!b || b._wired) return !!(b && b._wired);
    b._wired = true;
    b.addEventListener('pointerdown', (e) => { e.preventDefault(); try { b.setPointerCapture(e.pointerId); } catch (_) {} arm(); });
    for (const ev of ['pointerup', 'pointercancel', 'lostpointercapture']) b.addEventListener(ev, disarm);
    b.addEventListener('contextmenu', (e) => e.preventDefault());
    b.addEventListener('keydown', (e) => { if ((e.key === 'Enter' || e.key === ' ') && !e.repeat) { e.preventDefault(); e.stopPropagation(); arm(); } });
    b.addEventListener('keyup', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.stopPropagation(); disarm(); } });
    $('esReset').addEventListener('click', () => emitEvent('ct', { a: 'estop_reset' }));
    window.addEventListener('keydown', (e) => {
      if (e.repeat || e.metaKey || e.ctrlKey || e.altKey || e.key.toLowerCase() !== 'e') return;
      const t = e.target.tagName; if (t === 'INPUT' || t === 'SELECT' || t === 'TEXTAREA') return;
      arm();
    });
    window.addEventListener('keyup', (e) => { if (e.key.toLowerCase() === 'e') disarm(); });
    window.addEventListener('blur', disarm);
    paint();
    return true;
  }
  const tick = setInterval(() => { if (hook() && wire()) clearInterval(tick); }, 50);
})();
