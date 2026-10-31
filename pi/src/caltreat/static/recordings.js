// Recordings (recordings_ui.py). Settings page: recs.pick() uploads audio files: the browser decodes them (wav, mp3,
// m4a/aac, ogg/opus, flac: whatever it can play), mixes to mono at 48 kHz and sends a 16-bit WAV, so the Pi needs no
// ffmpeg. recs.pickZip() restores a recordings zip. Engineer page: recs.update(state) paints the pinned recordings
// (tap = play / stop, ring = progress) and a stop-all; the Pi pushes the shared state.
(() => {
  if (window.recs) return;
  const SR = 48000, MAX_S = 600, MAX_BYTES = 100 * 1024 * 1024;
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const msg = (text, bad) => {
    const m = document.querySelector('.recmsg');
    if (m) { m.textContent = text; m.classList.toggle('bad', !!bad); }
  };

  function wav(x) {  // Float32Array mono at SR -> 16-bit WAV
    const b = new ArrayBuffer(44 + x.length * 2), v = new DataView(b);
    const str = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
    str(0, 'RIFF'); v.setUint32(4, 36 + x.length * 2, true); str(8, 'WAVEfmt ');
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, SR, true); v.setUint32(28, SR * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
    str(36, 'data'); v.setUint32(40, x.length * 2, true);
    for (let i = 0; i < x.length; i++) v.setInt16(44 + i * 2, Math.max(-32768, Math.min(32767, Math.round(x[i] * 32768))), true);
    return b;
  }

  async function toWav(file) {
    if (file.size > MAX_BYTES) throw new Error(`bigger than ${MAX_BYTES / 1024 / 1024} MB`);
    const AC = window.AudioContext || window.webkitAudioContext;
    const OAC = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    const ctx = new AC();
    let audio;
    try {
      audio = await ctx.decodeAudioData(await file.arrayBuffer());
    } catch (e) {
      throw new Error('this browser cannot read it as audio');
    } finally {
      try { ctx.close(); } catch (e) { /* already closed */ }
    }
    if (audio.duration > MAX_S) throw new Error(`longer than ${MAX_S / 60} min`);
    const off = new OAC(1, Math.max(1, Math.ceil(audio.duration * SR)), SR);  // mono: the channels are mixed down
    const src = off.createBufferSource();
    src.buffer = audio; src.connect(off.destination); src.start();
    return wav((await off.startRendering()).getChannelData(0));
  }

  function chooser(accept, multiple, then) {
    const i = document.createElement('input');
    i.type = 'file'; i.accept = accept; i.multiple = multiple; i.style.display = 'none';
    i.addEventListener('change', () => { then([...i.files]); i.remove(); });
    document.body.appendChild(i);
    i.click();
  }

  async function post(url, body, type) {
    const r = await fetch(url, { method: 'POST', body, headers: { 'Content-Type': type } });
    let j = {};
    try { j = await r.json(); } catch (e) { /* not JSON */ }
    if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  }

  async function upload(files) {
    let ok = 0;
    const bad = [];
    for (const [n, f] of files.entries()) {
      msg(`⏳ ${n + 1}/${files.length} ${f.name}`);
      try {
        const name = f.name.replace(/\.[^.]+$/, '');
        await post(`/api/recordings?name=${encodeURIComponent(name)}`, await toWav(f), 'audio/wav');
        ok++;
      } catch (e) {
        bad.push(`${f.name}: ${e.message}`);
      }
    }
    msg((ok ? `✓ ${ok}` : '') + (bad.length ? ` ✗ ${bad.join(' · ')}` : ''), bad.length > 0);
  }

  async function restore(files) {
    if (!files.length) return;
    msg(`⏳ ${files[0].name}`);
    try {
      const j = await post('/api/recordings/restore', files[0], 'application/zip');
      msg(`✓ ${j.count}`);
    } catch (e) {
      msg(`✗ ${files[0].name}: ${e.message}`, true);
    }
  }

  // ---- engineer page ----
  const C = 2 * Math.PI * 29;  // ring circumference (r = 29, as the sequence buttons)
  let shown = '';
  function update(st) {
    const desk = document.querySelector('main.desk');
    if (!desk) return;
    let sec = document.getElementById('recs');
    if (!st.pinned.length) { if (sec) sec.remove(); shown = ''; return; }
    const key = JSON.stringify(st.pinned);
    if (!sec) {
      sec = document.createElement('section');
      sec.id = 'recs'; sec.className = 'ctl recs';
      desk.insertBefore(sec, desk.querySelector('footer.foot'));
    }
    if (key !== shown) {
      shown = key;
      sec.innerHTML = '<h2 title="Recordings pinned in Settings: tap to play, tap again to stop. Several can play at once">'
        + '♪ Recordings<button class="recstop" type="button" title="Stop all recordings" aria-label="Stop all recordings">■</button></h2>'
        + '<div class="recgrid">' + st.pinned.map(p =>
          `<button class="recbtn" type="button" data-id="${esc(p.id)}" aria-pressed="false" `
          + `title="${esc(p.name)} (${Math.floor(p.dur / 60)}:${String(Math.round(p.dur % 60)).padStart(2, '0')}${p.loop ? ', loops' : ''}): tap to play / stop">`
          + `<svg class="pring" viewBox="0 0 64 64" aria-hidden="true"><circle class="rt" cx="32" cy="32" r="29"/><circle class="rp" cx="32" cy="32" r="29"/></svg>`
          + `<i aria-hidden="true">${p.loop ? '⟳' : '▶'}</i><b>${esc(p.name)}</b></button>`).join('') + '</div>';
      sec.querySelectorAll('.recbtn').forEach(b => b.addEventListener('click', () => emitEvent('rec', { a: 'toggle', id: b.dataset.id })));
      sec.querySelector('.recstop').addEventListener('click', () => emitEvent('rec', { a: 'stop_all' }));
    }
    sec.querySelectorAll('.recbtn').forEach(b => {
      const p = st.playing[b.dataset.id];
      const on = p !== undefined;
      b.classList.toggle('on', on);
      b.setAttribute('aria-pressed', on);
      b.querySelector('i').textContent = on ? '■' : (st.pinned.find(x => x.id === b.dataset.id) || {}).loop ? '⟳' : '▶';
      b.querySelector('.rp').style.strokeDashoffset = on ? C * (1 - p) : C;
    });
    sec.querySelector('.recstop').disabled = !Object.keys(st.playing).length;
  }

  window.recs = {
    pick: () => chooser('audio/*,.wav,.mp3,.m4a,.aac,.ogg,.oga,.opus,.flac,.webm', true, upload),
    pickZip: () => chooser('.zip,application/zip', false, restore),
    upload, update, toWav,
  };
})();
