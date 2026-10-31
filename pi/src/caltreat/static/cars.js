// Engineer page: the reverser (A / B leads) and the master switch for the cars' lamps (cars.py). Wraps ct.update
// (cab.js) to paint them from the shared state: {dir, dirRefused, lightsOn}. The Pi refuses a direction change
// unless the train stands still with the power off (like a real reverser): the button then says so for a moment.
(() => {
  const $ = (id) => document.getElementById(id);
  let S = { dir: 'A', lightsOn: true };
  function paint() {
    const rev = $('rev');
    if (rev) {
      rev.dataset.dir = S.dir;
      rev.querySelector('i').textContent = S.dir === 'A' ? '◀' : '▶';
      $('revTxt').textContent = `${S.dir} fwd`;
      $('revHint').textContent = S.dirRefused ? '■ stop first' : '';
      rev.classList.toggle('refused', !!S.dirRefused);
      rev.setAttribute('aria-label', `Direction: car ${S.dir} leads. Tap to reverse (at a standstill)`);
    }
    const lcd = $('lcdDir');
    if (lcd) lcd.textContent = S.dir === 'A' ? '◀ A fwd' : 'B fwd ▶';
    const m = $('ltMaster');
    if (m) {
      m.classList.toggle('on', !!S.lightsOn);
      m.setAttribute('aria-pressed', !!S.lightsOn);
      m.querySelector('small').textContent = S.lightsOn ? 'on' : 'off';
    }
  }
  function hook() {
    if (!window.ct || ct._cars) return !!(window.ct && ct._cars);
    const orig = ct.update;
    ct.update = (st) => {
      orig(st);
      for (const k of ['dir', 'dirRefused', 'lightsOn']) if (k in st) S[k] = st[k];
      paint();
    };
    ct._cars = true;
    return true;
  }
  const wire = () => {
    const rev = $('rev'), m = $('ltMaster');
    if (!rev || rev._wired) return !!(rev && rev._wired);
    rev._wired = true;
    rev.addEventListener('click', () => emitEvent('ct', { a: 'dir', v: S.dir === 'A' ? 'B' : 'A' }));
    if (m) m.addEventListener('click', () => emitEvent('ct', { a: 'lights_master' }));
    paint();
    return true;
  };
  const tick = setInterval(() => { if (hook() && wire()) clearInterval(tick); }, 50);
})();
