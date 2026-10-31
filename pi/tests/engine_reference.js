// Reference render for tests/test_engine.py: what the v12 page's Output selector plays for each bus
// (KissBuses.Preview set to that bus), for a score of [sample, message] pairs, in blocks of BLOCK. Writes float32
// interleaved [front, inside, rear] to stdout.
//   node tests/engine_reference.js score.json frames [engine dir]
const fs = require('fs'), path = require('path');
const [score, frames, dir] = [JSON.parse(fs.readFileSync(process.argv[2], 'utf8')), +process.argv[3], process.argv[4] || path.join(__dirname, '..', 'engine')];
const KissEngine = new Function(fs.readFileSync(path.join(dir, 'engine.js'), 'utf8') + '\nreturn KissEngine;')();
const KissBuses = require(path.join(dir, 'buses.js'));
const BLOCK = 512, SR = 48000, BUSES = ['front', 'inside', 'rear'];
const out = new Float32Array(frames * 3);
BUSES.forEach((bus, b) => {
  const p = new KissBuses.Preview(KissEngine, SR), tmp = [new Float32Array(BLOCK), new Float32Array(BLOCK)];
  p.msg({ t: 'output', v: bus });
  let k = 0;
  for (let i = 0; i < frames; i += BLOCK) {
    while (k < score.length && score[k][0] <= i) p.msg(score[k++][1]);
    p.render(tmp, BLOCK);
    for (let j = 0; j < BLOCK && i + j < frames; j++) out[(i + j) * 3 + b] = tmp[0][j];
  }
});
fs.writeSync(1, Buffer.from(out.buffer));
