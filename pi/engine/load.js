// vendored from caltrain-sounds app/samples/load.js at 5d0b32b (pi/engine/sync.sh); do not edit here
// v10 recorded doors for Node (render.js, tests, pi/): no browser APIs, only fs.
//   const { loadDoorSamples } = require('<repo>/app/samples/load.js');
//   const ds = loadDoorSamples();                      // { pcm: Float32Array (int16 / 32768, at ds.meta.sr), meta }
//   engine.msg({ t: 'doorSamples', pcm: ds.pcm, meta: ds.meta });   // once per KissEngine (the array is only read)
// Files: doors_v10.wav (16-bit PCM mono 44.1 kHz, segments open / close / reopen back to back) and doors_v10.json
// (segment start / dur in s, leaf-motion markers, tone_rms for the level match); doors_v10.flac is the same PCM,
// lossless, for the page (app/build.py embeds it). Made by analysis/doors_v10_extract.py.
const fs = require('fs'), path = require('path');
function loadDoorSamples(dir = __dirname, name = 'doors_v10') {
  const meta = JSON.parse(fs.readFileSync(path.join(dir, name + '.json'), 'utf8')), b = fs.readFileSync(path.join(dir, name + '.wav'));
  if (b.toString('ascii', 0, 4) !== 'RIFF' || b.toString('ascii', 8, 12) !== 'WAVE') throw new Error(name + '.wav: not a WAV file');
  let o = 12, fmt = null, data = null;
  while (o + 8 <= b.length) { const id = b.toString('ascii', o, o + 4), n = b.readUInt32LE(o + 4);
    if (id === 'fmt ') fmt = { ch: b.readUInt16LE(o + 10), sr: b.readUInt32LE(o + 12), bits: b.readUInt16LE(o + 22) }; else if (id === 'data') data = [o + 8, n];
    o += 8 + n + (n & 1); }
  if (!fmt || !data || fmt.ch !== 1 || fmt.bits !== 16 || fmt.sr !== meta.sr) throw new Error(name + '.wav: expected 16-bit mono at ' + meta.sr + ' Hz');
  const n = data[1] >> 1, pcm = new Float32Array(n);
  for (let i = 0; i < n; i++) pcm[i] = b.readInt16LE(data[0] + 2 * i) / 32768;
  return { pcm, meta };
}
module.exports = { loadDoorSamples };
