// vendored from caltrain-sounds app/engine.js at 5d0b32b (pi/engine/sync.sh); do not edit here
// KISS EMU sound engine: pure per-sample DSP, no DOM, no Web Audio nodes. The same text runs in the browser's
// AudioWorklet (or a ScriptProcessor fallback) and in Node for the offline renders (app/render.js).
// Parameters come from analysis/params.json and the v2 detail analyses (analysis/*_detail.py, horn_onset.py), all
// fitted to recordings of Caltrain Stadler KISS EMUs.
const KISS_PARAMS = {
  horn: { f0: 383.0, // v3: bell-referenced stationary f0 (analysis/horn_pitch_audit.py); v1/v2 had 376.2
          // v4 (analysis/horn_spectrum_v4.py): 40 harmonics fitted to the on-axis blasts of nWcv0Koz_9E (horn pointing at the
          // listener), dB re the strongest; v1-v3 had 24 from the dimmer side-on/station clips
          harm: [-0.5, 0.0, -5.5, -4.1, -7.4, -12.2, -18.5, -21.6, -22.6, -23.7, -26.8, -25.2, -29.7, -28.7, -30.0, -29.4, -31.9, -31.3, -29.1, -33.1, -38.0, -40.5, -39.3, -38.8, -33.9, -33.5, -33.6, -35.2, -35.8, -39.3, -41.9, -47.5, -48.1, -48.8, -48.4, -50.6, -50.8, -53.8, -50.4, -48.0],
          // v6 (analysis/ref_Ugz1xBldvZ4.py): that v4 fit + the near-field top end of the owner's Ugz1xBldvZ4 (camera ~12 m from
          // the horn): +0.8 dB at h13 rising to +14 dB at h24-26; far listeners ('approaching', 'distance') get it back through farAir
          noise: -52.4, airBands: [-13.4, -20.8, -41.7], // v6: +3.5 dB (Ugz1xBldvZ4 floor); v4 air: noise at 1.4 / 2.8 (low-passed at 3.5 kHz) / 6 kHz between the harmonics, dB re the harmonic sum
          approachDoppler: 1.041, // median on-axis f0 398.6 / 383 Hz (nWcv0Koz_9E)
          
          onset: { scoopFrom: -60, scoopSd: 25, peak: 52, peakSd: 8, tPeak: 0.010, tau: 0.15, fastW: 0.55, fastTau: 0.05, rise0: -0.035, rise1: 0.020,
                   harmDelay: [0, 0.015, 0.013, 0.022, 0.025, 0.025, 0.028, 0.028].concat(new Array(32).fill(0.025)),
                   bloomDb: [6, 2.5, 3].concat(new Array(37).fill(2.5)), bloomRise: 0.02, bloomT: 0.05, bloomTau: 0.12 },
          squeak: { p: 0.8, f: 4120, glide: 300, dur: 0.041, lead: 0.055, db: -30, p2: 0.35, f2: 5900 },
          release: { fast: 0.03, slow: 0.12, mix: 0.45, sag: -6, hiTilt: 0.12 }, minHold: 0.08, retrig: { t0: 0.12, t1: 0.26 }, burstDb: 9,
          jitter: { slowC: 1.5, fastC: 2.0, lvlDb: 0.8 },
          // v5 roughness (analysis/horn_roughness_v5.py): each harmonic's level wanders on its own (sd dB, h1 / h2+, at
          // ~lvlHz), plus common FM phase noise (cents rms, one-pole at fmHz) that gives the lines their skirts
          phases: [0.0, -1.59, 1.0, 2.61, -2.95, -1.87, -0.53, 1.05, -3.11, 2.63, 2.56, -1.63], // v5: h1-h12 real (circular mean of 8), h13+ fixed spread
          rough: { lvlSd: [2.0, 3.7], lvlHz: 8, fmCents: 6, fmHz: 10 }, // v6: level wander 2-pole at 8 Hz (v5 1-pole 15 Hz: cycle-to-cycle shimmer 10 %, real 2.6-7.5 %)
          // v6 blow-up (Ugz1xBldvZ4 long press; also the longest nWcv0Koz_9E blast): a press starts cleaner and darker and
          // builds over ~1.5 s: harmonic centroid 2.3 -> 3.0-3.2, level wander 0.6 -> 3.7-4.3 dB, jitter 0.5 -> 2.2 %, air +3 dB.
          // b = 0..1 (reset by a fresh press, kept through a quick re-press); tilt dB per harmonic step (h1-h8) per unit b
          // around b = mid; v5's roughness = scale 1 at b ~0.5
          blow: { tau: 3.0, relTau: 0.4, mid: 0.1, tilt: 1.5, roughLo: 0.2, roughHi: 2.2, fmHi: 7, fmPow: 3, airDb: 0 }, // v6.1: air no longer builds (owner: too breathy when held)
          // v8.1 (t-0594, analysis/horn_longhold_v81.py): b kept rising to 1 over ~10 s, past the measured late-press point
          // (b ~0.6 at 3 s), so a long hold grew 20-26 cents of slow pitch wander and ~5 dB per-harmonic wander (the 'groan
          // tube'); real holds (the 54 s stuck horn BkvsxLuNibQ, PP3Q-1fcfgA, Ugz1xBldvZ4) stay at 1-4 cents. Its effect now
          // saturates softly above `knee` at `sat`; the common FM noise is band-passed (fmLoHz-fmHz) so it widens the lines
          // without wandering the pitch, and the h1-h2 level wander is shallower (lvlLoK) so the envelope does not sweep
          blowSat: { knee: 0.45, sat: 0.62, fmLoHz: 4, lvlLoK: 0.75, fmK: 0.5 },
          // v8.2 (t-0598, analysis/horn_warble_v82.py): the residual warble of a long hold was v5/v6's roughness itself:
          // fitted on moving trains (pass-bys), where the moving multipath beats the harmonics, it gives a held note 3-17x
          // the real 2-20 Hz modulation (f0 ~1 cent/sqrt(Hz) flat to 20 Hz, h-levels 2.9 dB at 4-10 Hz). The stuck horn with
          // the train standing (BkvsxLuNibQ 39.5-53.5 s) is far steadier: f0 0.35 / 0.15 / 0.14 cents rms at 0.3-2 / 2-4 /
          // 4-10 Hz, h-levels 2.7 / 1.3 / 0.7 dB (slow drift, steep fall-off), neighbours correlated ~0.2. After `after` s of
          // a press the roughness crossfades (tau) to that spectrum: f0 one-pole f0Hz at f0C cents + fastC, per-harmonic
          // levels 2-pole lvlHz at lvlSd dB (inputs mixed with the neighbours' by nbr), common level lvlDb; FM noise off
          // v8.3 (t-0602, analysis/horn_hold_v83.py): that crossfade 2 s in WAS the remaining oddity: v6's blow-up made the
          // roughness grow for the first ~2.5 s (f0 2-20 Hz 1.5 -> 1.9 c, h-levels 1.6 -> 2.6 dB), then it fell 4-8x over
          // 2-7 s (to 0.2 c / 0.8 dB) and the level sagged 0.5-1 dB (the lognormal mean of the deeper wander): a held note
          // went alive -> dead seconds in. Real long presses do not change character after the onset; the near-field
          // roughness of Ugz1xBldvZ4 / PP3Q / TnRw (3-6 c, 2.5 dB, constant over the press) is moving-train propagation, so
          // the standstill spectrum now applies from `after` = 0.3 s (settled by ~0.8 s, inside the onset); meanK keeps each
          // harmonic's mean power as its wander depth changes
          steady: { after: 0.3, tau: 0.25, f0C: 0.6, f0Hz: 0.5, fastC: 0.08, lvlSd: [0.8, 2.4], lvlHz: 2.2, nbr: 0.12, lvlDb: 0.1, meanK: 1 },
          // v6.1 (analysis/breath_v61.py): sustained breath under the note re v6. The real near-field floor between the harmonics
          // is -40 dB re total at 1-4 kHz (mostly ambience) and -47/-55 at 4-8/8-12 kHz; v6 had -25/-31 at 1-4 kHz (10-15 dB
          // too breathy), its harmonics alone -49..-71 (so the floor is not just their skirts). Shape: 1-4 kHz bands -15 dB,
          // 6 kHz band +2 dB (4-8 kHz floor just under the real one). Breath slider 0..1: 0 = none, else +-12 dB around the shape (0.5 = default); onset burst separate
          breath: 0.5, breathShape: { lowDb: -15, highDb: 2 },
          // v6 far field: air absorption between the 12 m clip and the 100-200 m nWcv0Koz_9E blasts (high shelf + dip,
          // within 1 dB of the measured difference); applied to the horn in 'approaching' and 'distance'
          farAir: { hs: [6549, 1.05, -7.5], pk: [10043, 1.79, -6.6] } },
  bell: { period: 0.4, jitterMs: 0.7, strikeSdDb: 0.8,
          partials: [[805.0, -10.6, -37.6], [1387.5, 0.0, -4.1], [2035.0, -6.2, -23.8], [2737.5, -18.1, -24.0], [3495.0, -16.0, -29.9],
                     [4312.5, -24.0, -65.2], [5202.5, -9.2, -147.9], // v1; below: v2 upper/inharmonic partials (level at strike, dB/s)
                     [1201.1, -23.0, -75], [1528.9, -16.0, -91], [2083.3, -25.5, -14], [2185.6, -26.5, -42], [2394.9, -29.0, -77],
                     [2627.1, -33.0, -60], [2837.7, -32.0, -46], [4652.5, -36.0, -119], [5536.7, -40.5, -86], [6009.1, -29.5, -145],
                     [6155.1, -28.5, -111], [6590.5, -39.0, -139], [7587.8, -39.5, -130]],
          body: { db: -18, rise: 0.02, tau: 0.12 } },
  door: { kiss: { f0: 2197, harm: [-1.9, 0.0, -9.9, -7.9, -13.7], dipEvery: 1.1, dipLen: 0.025, dipDb: -16 }, // owner's recording
          steady: { f0: 1760, harm: [-4.5, 0.0, -15.7, -22.0, -31.2] },                                         // 96tvGV1FsSA
          // v9 (analysis/doors_r2h.py, r2_h_pdUsIA): the opening, closing and after-close signals (harm: h1-h5 dB re h2)
          open: { f0: 2197.0, harm: [-5.4, 0.0, -8.9, -8.6, -24.5], db: 1.5, gapEvery: 0.546, gapFirst: 0.535, gapLen: 0.035, gapDb: -30, edge: 0.0008, click: { db: -16, f: 2900, q: 0.7, len: 0.07 } },
          close: { f0: 1758.3, harm: [-6.4, 0.0, -12.6, -21.9, -19.0] },
          after: { f0: 1758.3, harm: [-8.8, 0.0, -13.6, -15.2, -14.4], db: 3.2, pulseEvery: 0.211, pulseFirst: 0.13, pulseLen: 0.075, pulseDb: -15, pulseH1Db: -3, edge: 0.004 } },
  interior: { // owner's interior recordings (analysis/interior_detail.py): HVAC bed + aux tones; rolling noise grows with speed
          bed: 4.0, bedSpeed: 0.45, rolling: [['bp', 1300, 1.2, -17], ['bp', 2600, 1.5, -20], ['bp', 5000, 2.5, -30]],
          tones: [[3961, -42], [920, -50]], level: 0.38 },
  motor: { aMax: 980, cSlope: 2.144, cOff: 34.3, bSlope: 2.985, bOff: 745.2, dSlope: 0.181, carrier: 2022, carSlope: 0.419,
           lvC: -7.4, lvCar: -2.8, lvB: -12, lvD: -8, syncConst: 2090, lvSyncConst: -16, asyncOff: [495, 525], syncUp: 560, syncDown: 540,
           syncJump: 0.12, brakeTone: 1650, stopDecelBase: 0.034, stopDecelProp: 0.06 },
  hiss: { dur: 3.0, whistleF0: 3607, whistleHarm: [-15, 0, -16, -26], whistleLv: 0.2, hfLv: 0.035, hfBuild: 0.8 },
  // v7 (t-0590, analysis/interior_v7.py): inside a KISS car. Bed and rolling as 1/3-octave noise tables (dB re the bed's
  // strongest band) from the owner's interior recordings (standing: Ambient_train_brake 7.5-15 s; moving minus standing:
  // Ambient_train1, +3.6 dB, taken as 55 mph); rolling scales 25 dB per decade of speed (assumed); HVAC/aux lines (standing
  // bed: 281/548/931 Hz, a 120.3 Hz comb 3.6-4.4 kHz, ~8 dB stronger moving); switch/crossing clatter: welded rail, so no
  // regular joints: random events (~1 per 250 m, x3 below 25 mph: station turnouts) hitting the car's four axles at 0 / 2.7 / 19.0 / 21.7 m (bogie wheelbase
  // and centres assumed: the clips did not resolve them); 920 Hz brake/aux line while braking slowly; the stop's air
  // v8: that pulse sound is the SANDER (owner), see sand below. Exterior sounds
  // through the body: horn h3+ ~-16 dB re h1-h2 (own horn heard inside, m8onTdsUUXg 228-256 s), level ~+8 dB over the
  // cruise bed; bell a further -12 dB; door tone ~ the bed's total (owner); whine through the floor.
  inside: { bands: [25, 31, 40, 50, 63, 79, 100, 126, 159, 200, 252, 317, 400, 504, 635, 800, 1008, 1270, 1600, 2016, 2540, 3200, 4032, 5080, 6400],
            bed: [-19.3, -12.2, -7.2, -1.6, -1.2, 0.0, -2.0, -2.9, -6.4, -2.8, -9.7, -8.5, -10.0, -10.8, -13.6, -14.3, -10.1, -17.4, -20.2, -22.3, -27.0, -26.4, -28.9, -31.3, -35.4],
            roll55: [-22.2, -12.0, -8.3, -3.3, -3.3, -1.9, -0.2, -0.4, 0.3, -2.2, -2.1, -6.9, -12.7, -13.3, -11.3, -12.9, -12.3, -12.8, -12.5, -14.7, -18.0, -20.5, -21.5, -23.8, -25.2],
            rollPerDecade: 25, refMph: 55, bedDb: -30, tones: [[281, -18.5], [548, -19.6], [931, -19.0]],
            comb: { f0: 120.3, k: [30, 31, 32, 33, 34, 35, 36, 37], db: [-38.5, -31.8, -32.3, -33.7, -29.4, -33.6, -44.8, -45.8], movingDb: 8 },
            clatter: { perMeter: 1 / 250, stationBoost: 3, stationMph: 25, axles: [0, 2.7, 19.0, 21.7], nearDb: 0, farDb: -9, levelDb: 8, forcedDensity: 4 },
            brakeTone: { f: 920, db: -14 },
            doorLeak: { db: -4, def: 0.7, tau: 0.08, peakDb: -9, att: 0.001, rel: 0.8, pkRel: 0.05 }, // v10.3 (t-0604): open doorway = the exterior sound at db re outside (Mix 'Door leak' at def); leveller: peakDb, see KissEngine
            body: { shelfHz: 900, shelfDb: -16, hornDb: -4.3, bellDb: -17, doorDb: -6, motorLp: 1200, motorDb: -9, stopDb: -14 } },
  // v11 (t-0603): outside, next to one end of the train (class Outside; analysis/exterior_v11_inventory.md). Tables:
  // dB per 1/3-octave band re their own maximum. idle = rooftop HVAC / fan roar at standstill (13DeEw8vzRM 48-68 s,
  // PP3Q-1fcfgA 74-94 s, smoothed); roll = pass-by at 70-74 mph (S_AUeU5kSM8 81-87 / 127-133 s, KD_8ggMEJ6U 30-48 s);
  // growl = traction at 0-15 mph minus standing (5Z4R7qLlvMg 60-70 s, 13DeEw8vzRM 88-96 s). Levels (dB, band units)
  // set so the scenes keep the clips' A-weighted ratios (README v11): standing << departing << pass-by < horn
  outside: { bands: [31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000],
            idle: [-20, -12, -4, 0, 0, -1, 0, -1.5, -2, -5, -8, -8, -12, -13, -13, -14, -14, -17, -18, -20, -22, -25, -28, -33, -36, -45],
            roll: [-18, -17, -11, -10, -7, -5, -4, -3, -2, 0, -1, -2, -4, -5, -6, -7, -6, -8, -9, -12, -14, -14, -15, -15, -16, -27],
            growl: [-16, -10, -5, -2, 0, 0, 0, 0, 0, -1, -2, -5, -8, -10, -12, -13, -15, -17, -19, -22, -25, -28, -31, -35, -40, -45],
            // v11.1 (t-0620): idleDb +2.5 dB (v11 -3): the fan / HVAC roar only slightly louder (owner)
            levelDb: -30, idleDb: -0.5, rollDb: 8.7, refMph: 72, rollPerDecade: 30, hfPerDecade: 10, fadeMph: 5, forcedMph: 55,
            growlDb: -9, growlSpeedDb: 3, regenK: 0.7, blowRise: 0.8, blowTau: 4,
            // v11.1 (t-0620): the observer rides WITH the train (the speaker is on it): no per-car swell (v11: carM 26 / swellDb 6,
            // the pass-by envelope), steady rolling at the swell's mean power (rollDb 8 -> 8.7) with the natural fluctuation of a
            // co-moving reference (M8 EMU, open vestibule door, LlSD3I1x83U cruise 340-370 / 600-670 / 900-940 s: no car rhythm,
            // sd 0.8-1.0 dB over 1 s and 1.3-1.7 dB over 0.1 s): slow OU sdDb / tau + fast fsdDb / ftau, the bands above 1 kHz
            // with their own slow part (hsdDb); midDb: 500-1250 Hz up re the pass-by table (close to the wheels the reference has
            // +4 dB there re the KISS pass-by shape; half applied, different train and microphones)
            fluct: { sdDb: 1.2, tau: 2.0, fsdDb: 1.1, ftau: 0.15, hsdDb: 0.7, htau: 0.8 }, midDb: [[500, 1.5], [630, 2], [800, 2], [1000, 2], [1250, 1.5]],
            // v11.1: comb lineDb +6.5 on top of idleDb (+2.5): the converter comb +9 dB re v11 (owner: louder electrical hum). lines:
            // lower converter / fan tones found only with the train standing (13DeEw8vzRM 48-68 s, PP3Q-1fcfgA 74-94 s; absent
            // in the no-train background PP3Q 122-128 s): 178 / 215 Hz (roof fans or the transformer pump, +10..15 dB over the
            // local floor), 1203 / 2165 Hz = comb lines 10 / 18 (+7..+25 dB); [Hz, dB band units re idleDb]
            comb: { f0: 120.3, k: [30, 31, 32, 33, 34, 35, 36, 37, 63, 64, 65, 66, 67, 68, 69, 70],
                    db: [-30, -28, -27, -29, -27, -28, -30, -31, -44, -43, -44, -43, -44, -43, -43, -45], lineDb: 6.5,
                    lines: [[178, -17], [215, -16], [1203, -34], [2165.4, -38]] }, combLoad: 1.0,
            motorHarm: { k: [2, 3, 4, 5, 6, 8], lv: [0, -3, -4, -6, -8, -10], db: -10 }, car2Db: -16,
            // v11.1 clatter: a switch passes under every axle of the train in turn (cars x carM, axles re each car's end, the
            // speaker at the train's end distM from the wheel/rail contact); each impact at (r0 / r)^spread re the nearest,
            // later by the travel and the sound's flight (r / 343 m/s), the farther ones duller (farM: through lpFar); impacts
            // under cutDb dropped (spread 0.8 < 1: the underbody gap carries the knocks along the train a little; free field
            // 1.0 left the trailing trucks inaudible under the rolling). db 30 (v11 6): the lead truck's impacts +12 dB over
            // the rolling at 50 mph (5 ms frames; v11's were under it at speed). FRONT: the lead truck under the speaker, the trucks behind fainter and later; REAR: the mirror,
            // the far trucks first, the last truck (loudest) a train length later than at the front
            clatter: { meanM: 300, stationBoost: 3, stationMph: 25, forcedDensity: 4, db: 30, axles: [2.15, 4.85, 21.15, 23.85], cars: 7, carM: 26,
                       distM: 2.5, spread: 0.8, cutDb: -24, farM: 12, lpFar: 2200 },
            ends: { front: { hfDb: 1.5 }, rear: { hfDb: -1.5 } } },
  // v8 (t-0592): the sander (sand blown onto the rails), analysis/sand in README: 1507-1529 Hz tone + ~2 kHz hiss in
  // pulses (owner's clip: 0.38 / gap 0.16 / 0.24 s, then smaller 0.12 s ones), +17-19 dB over the interior bed inside, weak
  // outside (+7 dB over a platform, PP3Q-1fcfgA); fires as the train comes to rest (0.5-2 s after; rV_-ggnomUI x2, owner x2,
  // PP3Q-1fcfgA) and now and then when starting off
  sand: { f: 1518, pulses: [[0.0, 0.36, 0], [0.54, 0.26, 0], [0.88, 0.12, -6], [1.26, 0.12, -8]], toneDb: -6, insideDb: 8.4, outsideDb: -22, autoDelay: 0.8 },
  // v9 (t-0599): the door sequence from the owner's reference r2_h_pdUsIA (analysis/doors_r2h.py / .json; replaces v8's
  // borrowed SBB/DB motion). Times in s, open from the tap (= the open signal's onset), close events from the leaves'
  // start (move) or from the seal (S); hits [peak time, type, dB re the closing tone, rise, decay to -12 dB].
  // Open: signal 0 -> leaves open + 0.10; body thumps at 0.76 / 2.0 s; the leaves start at 3.01 s with a push-out
  // (+ a second at +0.5 s) and travel 2.85 s (video); faint 840 Hz whine. Close: 1758 Hz signal from the tap; the leaves
  // start at 1.89 s and travel 3.15 s; actuator whine from +0.29 s (610 -> 872 -> 840 Hz) until 1.25 s before the seal
  // (then the plug pull-in), a knock at +0.83 s, a 975 Hz blip 0.75 s before the seal, contact -0.14, SEAL THUD, lock
  // +0.21, signal off +0.13; after-signal +0.43 to +3.30 s with a body thud at +2.34 and a tick at +3.14.
  // reopen: obstruction while closing -> bump, open signal after 0.07 s, leaves back out after 0.35 s.
  doorMech: { hitTypes: { body: [['lp', 250, 0.7], ['peak', 120, 1.2, 8]], seal: [['lp', 2200, 0.7], ['lp', 3500, 0.7], ['peak', 125, 1.0, 3]],
                          push: [['lp', 900, 0.7], ['lp', 1500, 0.7], ['peak', 80, 1.0, 4]], click: [['bp', 300, 0.6], ['lp', 2000, 0.7]] },
              open: { pre: [[0.757, 'body', 19.2, 0.059, 0.043], [1.996, 'body', 21.9, 0.041, 0.048]], move: 3.01, travel: 2.85, toneAfter: 0.10,
                      push: [[0.014, 'push', 9.4, 0.053, 0.118], [0.505, 'push', 7.4, 0.029, 0.018]], slide: [0, -22],
                      whine: { start: 0.0, dur: 1.6, track: [[0, 830], [1, 845]], db: -30, h2: -4.6, rise: 0.15, fall: 0.2 } },
              close: { move: 1.89, travel: 3.153, slide: [-5, -24], knock: [0.832, 'click', 3.6, 0.024, 0.036],
                       whine: { start: 0.29, endBeforeSeal: 1.25, track: [[0, 610], [0.05, 620], [0.18, 872], [0.32, 840]], db: -13.2, h2: -18.6, rise: 0.04, fall: 0.03 },
                       blip: { at: -0.75, f: 975, dur: 0.06, db: -20 },
                       seal: [[-0.144, 'click', 5.3, 0.013, 0.020], [0, 'seal', 12.7, 0.024, 0.059], [0.208, 'click', 3.2, 0.010, 0.009],
                              [2.344, 'body', 13.9, 0.031, 0.111], [3.138, 'click', 5.6, 0.024, 0.021]],
                       toneOff: 0.13, after: [0.427, 3.303] },
              reopen: { bump: [0, 'seal', 6, 0.012, 0.04], toneDelay: 0.07, move: 0.35 }, reclose: { move: 0.8 },
              hitKnee: 6, hitRatio: 0.3, hitSine: { body: [118, 0.75] }, hitLayer: { body: ['seal', -14] }, // body thumps: 75 % a ~118 Hz damped resonance (lower crest factor than noise) + a faint broadband knock
              beepHz: 2888, beepLen: 0.34, beepDb: -2, // v8 passenger button beep (SBB KISS)
              outsideHp: 150, // v10 recorded doors heard outside (an approximation): 2 x 2nd-order high-pass, the cabin boom stays inside
              // v11.1 (t-0620, owner: the door recording is from inside the car): on the exterior speakers (Outside on, not
              // inside) the doors (recording, synth sequence and the door tone) play db lower (-13 = -10 dB re INSIDE with the
              // default routing: doors FRONT / REAR -3 dB vs INSIDE 0 dB through the body's doorDb -6 dB) and a little duller
              // (high shelf: the cabin's bright small-room tail does not reach the platform as is)
              outside: { db: -13, shelfHz: 3500, shelfDb: -5, lpHz: 9000 },
              recDb: -1.7 }, // v10: the recording's closing signal is set to v9's level, then -1.7 dB = v9's K-weighted loudness (the real thumps are louder than v9's compressed ones)
  stop: { squealF: 2050, wanderHz: 15, lumpDb: 4, chatter: 0.25, cluster2Db: -8, squealLv: 0.05, rollLv: 0.6, hiLv: 0.2, lfLv: 6.0,
          groanLv: 0.25, thumpLv: 1.0, standaloneFrom: 0.2, decelBase: 0.034, decelProp: 0.06,
          air: { delay: 0.0, dur: 2.6, whistleScale: 0.3, midScale: 0.6, hfScale: 1.4 } },
  // v8.2 output stage: v1-v8.1 ran linear to 0.6 then a soft knee (tanh) to 1.0, and at the app defaults (master 85 %,
  // horn 85 %) a held horn alone peaked at 2.6 there (v8.1, Dry: 16 % of samples in the knee, peaks squashed up to 8 dB,
  // more as the harmonics wandered). All sounds now go out `gain` lower (the mix balance is unchanged) into a knee from
  // `knee`: the horn alone at the defaults stays linear in every environment (analysis/horn_warble_v82.py, README)
  out: { gain: 0.55, knee: 0.85 },
};

const TWO_PI = 2 * Math.PI;
const db = (d) => Math.pow(10, d / 20);
const clamp = (x, a, b) => (x < a ? a : x > b ? b : x);
const ramp = (x, a, b) => clamp((x - a) / (b - a), 0, 1); // 0 below a, 1 above b
const tauCoef = (sr, tau) => 1 - Math.exp(-1 / (sr * tau));

class Rng { // xorshift32, uniform in [-1, 1)
  constructor(seed) { this.s = (seed >>> 0) || 1; }
  next() { let x = this.s; x ^= x << 13; x ^= x >>> 17; x ^= x << 5; this.s = x >>> 0; return this.s / 2147483648 - 1; }
}

class Biquad { // RBJ cookbook
  constructor() { this.b0 = 1; this.b1 = 0; this.b2 = 0; this.a1 = 0; this.a2 = 0; this.z1 = 0; this.z2 = 0; }
  set(type, f, q, sr, gainDb = 0) {
    const w = TWO_PI * Math.min(f, sr * 0.45) / sr, c = Math.cos(w), s = Math.sin(w), al = s / (2 * q), A = Math.pow(10, gainDb / 40);
    let b0, b1, b2, a0, a1, a2;
    if (type === 'lp') { b0 = (1 - c) / 2; b1 = 1 - c; b2 = b0; a0 = 1 + al; a1 = -2 * c; a2 = 1 - al; }
    else if (type === 'hp') { b0 = (1 + c) / 2; b1 = -(1 + c); b2 = b0; a0 = 1 + al; a1 = -2 * c; a2 = 1 - al; }
    else if (type === 'bp') { b0 = al; b1 = 0; b2 = -al; a0 = 1 + al; a1 = -2 * c; a2 = 1 - al; }
    else if (type === 'hs') { const q2 = 2 * Math.sqrt(A) * al; b0 = A * ((A + 1) + (A - 1) * c + q2); b1 = -2 * A * ((A - 1) + (A + 1) * c);
      b2 = A * ((A + 1) + (A - 1) * c - q2); a0 = (A + 1) - (A - 1) * c + q2; a1 = 2 * ((A - 1) - (A + 1) * c); a2 = (A + 1) - (A - 1) * c - q2; } // high shelf
    else { b0 = 1 + al * A; b1 = -2 * c; b2 = 1 - al * A; a0 = 1 + al / A; a1 = -2 * c; a2 = 1 - al / A; } // peak
    this.b0 = b0 / a0; this.b1 = b1 / a0; this.b2 = b2 / a0; this.a1 = a1 / a0; this.a2 = a2 / a0;
    return this;
  }
  run(x) { const y = this.b0 * x + this.z1; this.z1 = this.b1 * x - this.a1 * y + this.z2; this.z2 = this.b2 * x - this.a2 * y; return y; }
}

// Horn (v2, analysis/horn_onset.py on 23 clean blasts): one harmonic series. Each blast from silence plays the measured
// valve sequence: a thin ~4.1 kHz squeak (sometimes a second ~5.9 kHz one) for ~40 ms before the note, then the note
// rises in ~40 ms with its pitch scooping up from ~-60 cents to a +52 cent peak 10 ms after t_ref and settling with tau
// ~0.12 s; harmonics enter in order (h1 first, h2-h3 +15 ms, h4+ +25 ms), the fundamental blooms (+7 dB at ~50 ms) and an
// air burst rides the onset. Release: two-slope decay (fast 20 ms + slow 160 ms), upper harmonics first, slight sag.
// A tap shorter than the valve sequence still makes a real short blast (release no earlier than t_ref + minHold).
const gauss = (r) => (r.next() + r.next() + r.next()) * 1.0; // ~N(0, 1)
class Horn {
  constructor(sr, P, rng) {
    this.sr = sr; this.P = P; this.rng = rng; this.n = P.harm.length;
    this.g = new Float64Array(this.n); this.gT = new Float64Array(this.n); this.fl = new Float64Array(this.n); this.hk = []; this.dly = P.onset.harmDelay;
    this.bloom = P.onset.bloomDb.map((d) => db(d) - 1); this.relTau = this.hk.map(() => 0);
    this.gate = false; this.active = false; this.bt = 0; this.relT = -1; this.pendingOff = false; this.ph = 0; this.sqPh = 0; this.sq2Ph = 0;
    this.k = 0; this.cents = 0; this.lv = 1; this.js = 0; this.jf = 0; this.jl = 0; this.jw = 0; this.jwl = 0; this.rl = new Float64Array(this.n); this.rl2 = new Float64Array(this.n); this.rc = new Float64Array(this.n).fill(1); this.gz = new Float64Array(this.n);
    const rho = P.phases.concat(Array.from({ length: Math.max(0, this.n - P.phases.length) }, (_, i) => ((i * 2.399963) % TWO_PI) - Math.PI));
    this.pc = rho.map((v, i) => Math.cos(v - (i + 1) * Math.PI / 2)); this.ps = rho.map((v, i) => Math.sin(v - (i + 1) * Math.PI / 2)); this.retrig = false; this.relLevel = 0;
    this.nf = new Biquad().set('bp', 2500, 0.6, sr); this.bf = new Biquad().set('hp', 4500, 0.7, sr);
    this.af = [1400, 2800, 6000].map((fc) => new Biquad().set('bp', fc, 1.0, sr)); this.ag = P.airBands.map(db);
    this.alp = [new Biquad().set('lp', 3500, 0.7, sr), new Biquad().set('lp', 3500, 0.7, sr)]; // the real floor falls ~13 dB/oct above 2 kHz
    this.blow = 0; this.bTilt = new Float64Array(8).fill(1); this.airK = 1; this.far = false;
    const FA = P.farAir; this.fa = [new Biquad().set('hs', FA.hs[0], FA.hs[1], sr, FA.hs[2]), new Biquad().set('peak', FA.pk[0], FA.pk[1], sr, FA.pk[2])];
    this.setBreath(P.breath); this.doppler = 1; this.intensity = 0.85; this.setIntensity(0.85); this.newBlast(1);
  }
  setIntensity(v) { // louder = brighter (more pressure): tilt the upper harmonics
    this.intensity = v; const tilt = (0.85 - v) * 1.6; // 0 at the app default (85 %): the fitted profile
    this.hk = this.P.harm.map((d, i) => db(d - tilt * i)); const s = this.hk.reduce((a, b) => a + b, 0); this.norm = 1.5 / s;
    this.level = 0.15 + 0.85 * v;
  }
  setBreath(v) { this.breath = v; const g = v <= 0 ? 0 : db(24 * (v - 0.5)); this.brLo = g * db(this.P.breathShape.lowDb); this.brHi = g * db(this.P.breathShape.highDb); }
  newBlast(fresh) { // per-blast randomness, spread as measured across real blasts; `fresh` 0..1 = how settled the horn
    // was (analysis/horn_toots.py): 1 from silence or a gap >= ~0.25 s; a quicker re-press (0.16 s, tail still at
    // -27 dB) gave a third of the pitch peak, no level overshoot and no squeak
    const O = this.P.onset, Q = this.P.squeak, r = this.rng, depth = 0.7 + 0.3 * this.intensity;
    this.fresh = fresh; this.retrig = fresh < 1; this.blow *= 1 - fresh; // v6: a fresh press starts the blow-up again
    this.scoop = this.cents * (1 - fresh) + (O.scoopFrom + O.scoopSd * gauss(r) * 0.5) * fresh;
    this.peak = (O.peak + O.peakSd * gauss(r) * 0.5) * depth * (0.3 + 0.7 * fresh);
    this.tau = O.tau * Math.exp(0.3 * gauss(r) * 0.5);
    this.bloomK = fresh * depth * Math.exp(0.35 * gauss(r) * 0.5);
    this.sqOn = fresh > 0.7 && (r.next() * 0.5 + 0.5) < Q.p * fresh; this.sq2On = this.sqOn && (r.next() * 0.5 + 0.5) < Q.p2;
    this.sqF = Q.f * (1 + 0.035 * gauss(r) * 0.5); this.sqGlide = Q.glide * r.next(); this.sqDur = Q.dur * Math.exp(0.3 * gauss(r) * 0.5);
    this.sqLvl = db(Q.db + 3 * r.next()) * depth; this.sq2F = Q.f2 * (1 + 0.03 * r.next());
    // time from the button to t_ref (note 12 dB under its early peak): the full valve sequence from rest, just the
    // ~40 ms rise when the air column is still moving
    this.tRef = this.sqOn ? Q.lead : 0.04 + (Q.lead - 0.04) * fresh;
  }
  setGate(on) {
    if (on && !this.gate) {
      if (this.active && this.relT < 0) this.pendingOff = false; // pressed again before the release began: keep sounding
      else {
        const R = this.P.retrig, fresh = !this.active ? 1 : clamp((this.relT - R.t0) / (R.t1 - R.t0), 0, 1);
        // the new onset rises out of what is still sounding (no restart from zero, no click)
        for (let h = 0; h < this.n; h++) this.fl[h] = this.active ? clamp(this.g[h] / this.hk[h], 0, 1) : 0;
        if (!this.active) this.cents = 0;
        this.newBlast(fresh); this.active = true; this.bt = 0; this.relT = -1; this.pendingOff = false;
      }
    } else if (!on && this.gate && this.active) this.pendingOff = true;
    this.gate = on;
  }
  control(n) { // every 16 samples: pitch, per-harmonic gains, jitter
    const O = this.P.onset, R = this.P.release, J = this.P.jitter, tau = this.bt - this.tRef, r = this.rng;
    if (this.pendingOff && tau >= this.P.minHold) { this.pendingOff = false; this.relT = 0; }
    // pitch: scoop up to the peak, then exponential settle
    let c;
    if (tau < O.tPeak) { const u = clamp((tau - O.rise0) / (O.tPeak - O.rise0), 0, 1); c = this.scoop + (this.peak - this.scoop) * (0.5 - 0.5 * Math.cos(Math.PI * u)); }
    else { const d = tau - O.tPeak; c = this.peak * (O.fastW * Math.exp(-d / O.fastTau) + (1 - O.fastW) * Math.exp(-d / this.tau)); } // fast + slow settle
    const ST = this.P.steady, hw = this.bt - this.tRef - ST.after, w = hw > 0 ? 1 - Math.exp(-hw / ST.tau) : 0, w1 = 1 - w; // v8.2: settled 0..1
    const dt = n / this.sr, a1 = TWO_PI * (0.8 * w1 + ST.f0Hz * w) * dt, a2 = TWO_PI * 8 * dt, a3 = TWO_PI * 1.5 * dt;
    // one-pole filtered noise scaled to the measured sd: ~1 Hz pitch wander, 4-15 Hz jitter, slow level wander (dB)
    this.js += (gauss(r) * (J.slowC * w1 + ST.f0C * w) * Math.sqrt(2 / a1) - this.js) * a1;
    this.jf += (gauss(r) * (J.fastC * w1 + ST.fastC * w) * Math.sqrt(2 / a2) - this.jf) * a2;
    this.jl += (gauss(r) * (J.lvlDb * w1 + ST.lvlDb * w) * Math.sqrt(2 / a3) - this.jl) * a3;
    // v6 blow-up: b rises while the valve is held, falls during a release
    const B = this.P.blow; this.blow += this.relT < 0 ? (1 - this.blow) * dt / B.tau : -this.blow * dt / B.relTau;
    const S = this.P.blowSat, be = this.blow < S.knee ? this.blow : S.knee + (S.sat - S.knee) * Math.tanh((this.blow - S.knee) / (S.sat - S.knee)); // v8.1: saturates
    const rs = B.roughLo + (B.roughHi - B.roughLo) * be, tl = B.tilt * (be - B.mid);
    for (let i = 0; i < 8; i++) this.bTilt[i] = db(tl * (i - 1)); // pivot at h2: h1 down, h3+ up (A5: h1 -12, h3 -2.5 dB re total)
    this.airK = db(B.airDb * (be - B.mid));
    const RG = this.P.rough, a4 = Math.min(1, TWO_PI * RG.fmHz * dt), a5 = TWO_PI * (RG.lvlHz * w1 + ST.lvlHz * w) * dt, k5 = Math.sqrt(2 / a5), a6 = TWO_PI * S.fmLoHz * dt;
    this.jw += (gauss(r) * RG.fmCents * B.fmHi * S.fmK * Math.pow(be, B.fmPow) * w1 * Math.sqrt(2 / a4) - this.jw) * a4; // v5 common FM phase noise (v6: grows late in a press; v8.2: off once settled)
    this.jwl += (this.jw - this.jwl) * a6; // v8.1: its slow part (< fmLoHz) is removed below, so it adds skirts but no pitch wander
    const gz = this.gz, nb = ST.nbr * w, nk = 1 / Math.sqrt(1 + 2 * nb * nb); for (let h = 0; h < this.n; h++) gz[h] = gauss(r);
    for (let h = 0; h < this.n; h++) { // per-harmonic level wander (dB); v8.2: settled = slower, shallower, neighbours partly shared
      const g = (gz[h] + nb * ((h ? gz[h - 1] : 0) + (h < this.n - 1 ? gz[h + 1] : 0))) * nk, sd = RG.lvlSd[h ? 1 : 0] * rs * (h < 2 ? S.lvlLoK : 1) * w1 + ST.lvlSd[h < 2 ? 0 : 1] * w;
      this.rl[h] += (g * sd * k5 * 1.41 - this.rl[h]) * a5; this.rl2[h] += (this.rl[h] - this.rl2[h]) * a5;
      this.rc[h] = ST.meanK ? Math.exp(-0.01325 * sd * sd) : 1; } // v8.3: keep each harmonic's mean power as its wander depth changes (lognormal: +sd^2 ln10 / 20 dB)
    let rel = 1, sag = 0;
    if (this.relT >= 0) sag = R.sag * (1 - Math.exp(-this.relT / 0.1));
    this.cents = c + this.js + this.jf + (this.jw - this.jwl) + sag;
    // gains
    const bl = tau > 0 ? (1 - Math.exp(-tau / O.bloomRise)) * Math.exp(-Math.max(0, tau - O.bloomT) / O.bloomTau) * this.bloomK : 0;
    const wob = db(this.jl);
    let lvMax = 0;
    for (let h = 0; h < this.n; h++) {
      const x = tau - this.dly[h], u = clamp((x - O.rise0) / (O.rise1 - O.rise0), 0, 1);
      const fl = this.fl[h]; let gg = this.hk[h] * (fl + (1 - fl) * u * u) * (1 + this.bloom[h] * bl) * wob * db(this.rl2[h]) * this.rc[h] * this.bTilt[Math.min(h, 7)];
      if (this.relT >= 0) { const ts = R.slow / (1 + R.hiTilt * h); rel = R.mix * Math.exp(-this.relT / R.fast) + (1 - R.mix) * Math.exp(-this.relT / ts); gg *= rel; if (h === 0) this.relLevel = rel; }
      this.gT[h] = gg; lvMax = Math.max(lvMax, rel);
    }
    if (this.relT < 0) this.relLevel = 1;
    if (this.relT >= 0) { this.relT += dt; if (lvMax < 2e-4 && this.relT > 0.05) { this.active = false; this.g.fill(0); } }
    this.bt += dt;
  }
  sample() {
    if (!this.active) return 0;
    if ((this.k++ & 15) === 0) this.control(16);
    const f = this.P.f0 * this.doppler * Math.pow(2, this.cents / 1200), tau = this.bt - this.tRef;
    this.ph += f / this.sr; if (this.ph >= 1) this.ph -= 1;
    const th = TWO_PI * this.ph, c2 = 2 * Math.cos(th), nyq = this.sr * 0.47;
    for (let h = 0; h < this.n; h++) this.g[h] += (this.gT[h] - this.g[h]) * 0.08;
    // h_k = cos(k th + psi_k), psi_k = rho_k - k pi/2 (v5: rho = the real harmonic phases re k x phase(h1), so the cycle
    // has the real, rounder shape instead of the all-sine pulse of v1-v4); sin and cos by Chebyshev recurrence
    let sp = 0, sk = Math.sin(th), cp = 1, ck = Math.cos(th), sum = this.g[0] * (this.pc[0] * ck - this.ps[0] * sk);
    for (let k = 2; k <= this.n && k * f < nyq; k++) {
      const sn = c2 * sk - sp; sp = sk; sk = sn; const cn = c2 * ck - cp; cp = ck; ck = cn;
      sum += this.g[k - 1] * (this.pc[k - 1] * ck - this.ps[k - 1] * sk);
    }
    // squeak: thin gliding tone(s) before the note, raised-cosine gated
    let sq = 0;
    if (this.sqOn && this.bt < this.sqDur) {
      const u = this.bt / this.sqDur, w = Math.sin(Math.PI * u) ** 1.5;
      this.sqPh += (this.sqF + this.sqGlide * (u - 0.5)) / this.sr; if (this.sqPh >= 1) this.sqPh -= 1;
      sq = Math.sin(TWO_PI * this.sqPh) * w * this.sqLvl;
      if (this.sq2On && this.bt < this.sqDur * 0.7) { this.sq2Ph += this.sq2F / this.sr; if (this.sq2Ph >= 1) this.sq2Ph -= 1;
        sq += Math.sin(TWO_PI * this.sq2Ph) * Math.sin(Math.PI * this.bt / (this.sqDur * 0.7)) * this.sqLvl * 0.6; }
    }
    // air: steady breath under the note + a burst as the valve opens (+9 dB over the sustain hiss, ~40 ms)
    const relA = this.relT >= 0 ? this.relLevel : 1, n0 = this.rng.next();
    const burst = this.fresh < 0.5 ? 0 : this.fresh * db(this.P.burstDb) * Math.exp(-Math.max(0, tau + 0.04) / 0.04) * clamp(this.bt / 0.01, 0, 1);
    const hs = this.g[1] / this.hk[1] * this.airK; // sustained breath follows h2's envelope (v4 air between the harmonics)
    const breath = (this.nf.run(n0) * db(this.P.noise + 12) + this.alp[1].run(this.alp[0].run(this.af[0].run(n0) * this.ag[0] + this.af[1].run(n0) * this.ag[1])) * 1.5) * hs * this.brLo +
      this.af[2].run(n0) * this.ag[2] * 1.5 * hs * this.brHi +
      this.bf.run(n0) * db(this.P.noise + 12) * burst * relA; // valve-opening air burst (not scaled by Breath)
    const y = ((sum + sq * this.hk[1]) * this.norm + breath) * this.level;
    return this.far ? this.fa[1].run(this.fa[0].run(y)) : y; // v6: air absorption for a far listener
  }
}

// Bell (v2, analysis/bell_detail.py): electronic, identical strikes every `period` (strikes correlate 0.92-0.94, level
// sd 0.8 dB, timing sd 0.7 ms). Each strike: modal bank of the 7 v1 partials plus 13 weaker, fast-decaying inharmonic
// upper partials (1.2-7.6 kHz) that v1 lacked, and a 2-10 kHz noise body (10 ms rise, ~120 ms decay) in place of v1's
// 4 ms click. The previous strike is faded out in ~8 ms (the dips seen before each strike).
class Bell {
  constructor(sr, P, rng) {
    this.sr = sr; this.rng = rng; this.P = P; this.period = Math.round(P.period * sr);
    this.parts = P.partials.map(([f, l, d]) => ({ w: TWO_PI * f / sr, a: db(l), dm: Math.pow(10, d / 20 / sr) }));
    const s = this.parts.slice(0, 7).reduce((x, p) => x + p.a, 0); this.norm = 0.8 / s;
    this.slots = [0, 1].map(() => ({ on: false, t: 0, fade: 1, fm: 1, re: [], im: [], cw: [], sw: [], amp: [] }));
    for (const sl of this.slots) for (const p of this.parts) { sl.re.push(0); sl.im.push(0); sl.amp.push(0); sl.cw.push(Math.cos(p.w)); sl.sw.push(Math.sin(p.w)); }
    this.cur = 0; this.on = false; this.ctr = 0; this.tail = -1; this.body = 0; this.bodyAtk = 0;
    this.bd = Math.exp(-1 / (sr * P.body.tau)); this.ba = tauCoef(sr, P.body.rise / 2.2);
    this.hp = new Biquad().set('hp', 2000, 0.7, sr); this.lp = new Biquad().set('lp', 10000, 0.7, sr); this.pk = new Biquad().set('peak', 6500, 1.0, sr, 4);
    this.level = 0.8; this.fadeFast = Math.exp(-1 / (sr * 0.0025));
  }
  setOn(on) {
    if (on && !this.on) { this.ctr = 0; this.tail = -1; }
    if (!on && this.on) this.tail = this.ctr; // ring to the end of this period, then release
    this.on = on;
  }
  strike() {
    const old = this.slots[this.cur]; if (old.on) old.fm = this.fadeFast;
    this.cur ^= 1; const sl = this.slots[this.cur], g = db(this.P.strikeSdDb * gauss(this.rng));
    sl.on = true; sl.t = 0; sl.fade = 1; sl.fm = 1;
    for (let i = 0; i < this.parts.length; i++) { sl.re[i] = 1; sl.im[i] = 0; sl.amp[i] = this.parts[i].a * g; }
    this.body = db(this.P.body.db) * g; this.bodyAtk = 0;
  }
  sample() {
    if (this.on) { if (this.ctr <= 0) { this.strike(); this.ctr = this.period + Math.round(this.P.jitterMs * 1e-3 * this.sr * gauss(this.rng)); } this.ctr--; }
    else if (this.tail >= 0) { if (this.tail-- === 0) { const sl = this.slots[this.cur]; sl.fm = Math.exp(-1 / (this.sr * 0.12)); this.body *= 0.3; } }
    let y = 0;
    for (const sl of this.slots) {
      if (!sl.on) continue;
      const att = sl.t < 48 ? sl.t / 48 : 1; sl.t++;
      let v = 0;
      for (let i = 0; i < this.parts.length; i++) {
        const r = sl.re[i], m = sl.im[i];
        sl.re[i] = r * sl.cw[i] - m * sl.sw[i]; sl.im[i] = r * sl.sw[i] + m * sl.cw[i];
        v += sl.im[i] * sl.amp[i]; sl.amp[i] *= this.parts[i].dm;
      }
      if ((sl.t & 4095) === 0) for (let i = 0; i < this.parts.length; i++) { const n = Math.hypot(sl.re[i], sl.im[i]); sl.re[i] /= n; sl.im[i] /= n; }
      sl.fade *= sl.fm; if (sl.fade < 1e-4) sl.on = false;
      y += v * att * sl.fade;
    }
    if (this.body > 1e-5) {
      this.bodyAtk += (1 - this.bodyAtk) * this.ba;
      y += this.pk.run(this.lp.run(this.hp.run(this.rng.next()))) * this.body * this.bodyAtk; this.body *= this.bd;
    }
    return y * this.norm * this.level;
  }
}

// Door signal (v2, analysis/door_detail.py). Two measured styles, both continuous (no beeping), ~20 ms rise, ~6 ms off:
// 'kiss' = the owner's own recording inside a KISS car: 2197 Hz, 5 harmonics with h2 strongest, short ~25 ms dips
// (-16 dB) about every 1.1 s; 'steady' = the YouTube clip 96tvGV1FsSA: 1760 Hz, 5 harmonics (v1 had 4). These two remain
// for the held tone {t:'door',on} (default 'kiss').
// v9 (t-0599, analysis/doors_r2h.py on the owner's reference r2_h_pdUsIA): opening and closing have DIFFERENT signals,
// played by the door sequence (DoorMech below): 'open' = 2197 Hz cut by a sharp ~35 ms gap every 0.546 s (the clicks in
// it), from the release until the leaves are fully open; 'close' = 1758 Hz, continuous, until just after the seal thud;
// 'after' = 1758 Hz again once the doors have sealed, its upper harmonics pulsing every 0.211 s (4.74 Hz). Each gap of
// the open signal carries a ~70 ms noise burst around 2.9 kHz (1-6 kHz, ~-16 dB re the signal), as in the clip.
class Door {
  constructor(sr, P) {
    this.sr = sr; this.P = P; this.ph = 0; this.env = 0; this.gate = false; this.t = 0; this.level = 0.7; this.seqOn = false;
    this.aOn = tauCoef(sr, 0.008); this.aOff = tauCoef(sr, 0.003); this.dip = 1; this.dipH = 1; this.setStyle('kiss');
  }
  use(S) { this.S = S; this.w = TWO_PI * S.f0 / this.sr; this.h = S.harm.map(db); this.norm = 0.5 / this.h.reduce((a, b) => a + b, 0) * db(S.db || 0); this.aD = tauCoef(this.sr, S.edge || 0.003);
    if (S.click && !this.cbq) { this.cr = new Rng(4242); this.cbq = new Biquad().set('bp', S.click.f, S.click.q, this.sr); let q = 0; for (let i = 0; i < 16384; i++) { const v = this.cbq.run(this.cr.next()); if (i >= 4096) q += v * v; } this.cn = 1 / Math.sqrt(q / 12288); }
    this.rms = Math.sqrt(this.h.reduce((a, b) => a + b * b, 0) / 2); } // signal RMS at norm 1: the gap bursts are set relative to it
  setStyle(st) { this.style = st === 'steady' ? 'steady' : 'kiss'; if (!this.seqOn) this.use(this.P[this.style]); }
  setGate(on) { if (this.seqOn) { this.seqOn = false; this.use(this.P[this.style]); } if (on && !this.gate && this.env < 0.05) this.t = 0; this.gate = on; }
  play(name) { const S = this.P[name], sw = S !== this.S; this.seqOn = true; this.use(S); if (sw || this.env < 0.05) this.t = 0; this.gate = true; } // v9 sequence tones
  stop() { this.gate = false; }
  sample() {
    this.env += ((this.gate ? 1 : 0) - this.env) * (this.gate ? this.aOn : this.aOff);
    if (!this.gate && this.env < 1e-5) { this.env = 0; return 0; }
    const S = this.S; let dt = 1, dh = 1, burst = 0;
    if (S.dipEvery) { const u = (this.t / this.sr) % S.dipEvery; dt = u > S.dipEvery - S.dipLen && this.t > this.sr * 0.5 ? db(S.dipDb) : 1; }
    else if (S.gapEvery) { const u = this.t / this.sr - S.gapFirst; if (u >= 0 && u % S.gapEvery < S.gapLen) dt = db(S.gapDb);
      if (S.click && this.gate) { const c = S.click, w = (u + c.len / 2) % S.gapEvery; if (u + c.len / 2 >= 0 && w < c.len) { const sw = Math.sin(Math.PI * w / c.len); burst = this.cbq.run(this.cr.next()) * this.cn * sw * sw * db(c.db) * this.rms * Math.SQRT2; } } }
    else if (S.pulseEvery) { const u = this.t / this.sr - S.pulseFirst; if (u >= 0) { const v = u % S.pulseEvery; if (v < S.pulseLen) { const c = 0.5 - 0.5 * Math.cos(TWO_PI * v / S.pulseLen); dt = db(S.pulseH1Db * c); dh = db((S.pulseDb - S.pulseH1Db) * c); } } }
    this.dip += (dt - this.dip) * this.aD; this.dipH += (dh - this.dipH) * this.aD; this.t++;
    this.ph += this.w; if (this.ph > TWO_PI) this.ph -= TWO_PI;
    let y = this.h[0] * Math.sin(this.ph); for (let k = 1; k < this.h.length; k++) y += this.h[k] * Math.sin((k + 1) * this.ph) * this.dipH;
    return (y * this.dip + burst) * this.norm * this.env * this.level;
  }
}

// Traction inverter / motor whine (v2). `throttle` sets the target speed; speed follows at a limited rate. Line A
// (0-980 Hz) is the speed proxy; the other lines are fitted functions of A (analysis/motor_fit.json). v2 adds what the
// onboard ride shows: the asynchronous PWM (2 kHz carrier + sidebands) fades out by A ~525 Hz, a quiet stretch, then the
// synchronous line B cuts in abruptly (~0.3 s) at A ~560 Hz (3 starts in the ride); regenerative braking when the
// throttle is pulled back runs the same lines back down (B falls ~46 Hz/s), with the same jump in reverse; whine level
// follows tractive/braking effort; in sync mode a constant ~2.09 kHz line joins; lines carry a little frequency jitter
// and level flutter; and the last metres of a braked stop add a wobbling blended-brake tone.
class Motor {
  constructor(sr, P, rng) {
    this.sr = sr; this.P = P; this.rng = rng; this.throttle = 0; this.speed = 0; this.load = 0; this.gain = 0; this.regen = 0;
    this.accelTime = 14; this.N = 10; this.ph = new Float64Array(this.N); this.f = new Float64Array(this.N); this.a = new Float64Array(this.N);
    this.fj = new Float64Array(this.N); this.fl = new Float64Array(this.N); this.sync = false; this.syncG = 0; this.syncT = 0;
    this.rumble = new Biquad().set('lp', 220, 0.7, sr); this.level = 0.8; this.stopping = false; this.k = 0; this.bw = 0; this.bwv = 0; this.bb = 0; this.bph = 0;
  }
  control() { // every 64 samples
    const dt = 64 / this.sr, P = this.P, r = this.rng, decelTime = Math.max(6, this.accelTime * 0.8);
    const prev = this.speed;
    if (this.stopping && this.throttle > 0.01) this.stopping = false; // power applied: the stop is cancelled
    if (this.stopping) this.speed = Math.max(0, this.speed - dt * (P.stopDecelBase + P.stopDecelProp * this.speed)); // STOP: service brake
    else if (this.throttle > this.speed) this.speed = Math.min(this.throttle, this.speed + dt / this.accelTime);
    else this.speed = Math.max(this.throttle, this.speed - dt / decelTime);
    const accel = (this.speed - prev) / dt;
    // effort: motoring when accelerating (or holding speed under power), regen when braking
    const motoring = this.throttle > 0.01 ? clamp(0.35 + (this.throttle - this.speed) * 3 + 0.3 * this.throttle, 0, 1) : 0;
    const braking = accel < -1e-4 && this.speed > 0.004 ? clamp(0.45 + (this.speed - this.throttle) * 2.5, 0, 1) *
      (this.stopping ? ramp(this.speed, 0.04, 0.13) : ramp(this.speed, 0.004, 0.03)) : 0; // a STOP blends regen out to friction below ~10 mph
    this.regen += (braking - this.regen) * 0.02;
    this.load += (Math.max(motoring, braking) - this.load) * 0.02;
    const A = P.aMax * this.speed, C = P.cSlope * A + P.cOff, car = P.carrier + P.carSlope * A;
    // async -> sync PWM change: discrete, with hysteresis
    if (!this.sync && A >= P.syncUp) { this.sync = true; this.syncT = 0; }
    else if (this.sync && A < P.syncDown) this.sync = false;
    this.syncT += dt; this.syncG += ((this.sync ? 1 : 0) - this.syncG) * (dt / P.syncJump);
    const asyncG = 1 - ramp(A, P.asyncOff[0], P.asyncOff[1]);
    const fan = 0.2 * C * ramp(A, 20, 80), idle = this.throttle > 0.01 || this.speed > 0.002 ? 1 : 0;
    const F = [A, C, car, car + fan, car - fan, car + 2 * fan, car - 2 * fan, P.bSlope * A + P.bOff, P.dSlope * A,
               P.syncConst];
    const Aa = [ramp(A, 60, 300), db(P.lvC) * ramp(A, 15, 60) * asyncG,
      db(P.lvCar) * asyncG * idle, db(P.lvCar - 6) * ramp(fan, 5, 40) * asyncG, db(P.lvCar - 6) * ramp(fan, 5, 40) * asyncG,
      db(P.lvCar - 30) * ramp(fan, 10, 60) * asyncG, db(P.lvCar - 30) * ramp(fan, 10, 60) * asyncG,
      db(P.lvB) * this.syncG, db(P.lvD) * ramp(A, 20, 120), db(P.lvSyncConst) * this.syncG];
    for (let i = 0; i < this.N; i++) {
      this.fj[i] += (gauss(r) * 0.0006 * Math.sqrt(2 / 0.05) - this.fj[i]) * 0.05;  // ~0.06 % frequency jitter (lines stay sharp)
      this.fl[i] += (gauss(r) * 0.17 * Math.sqrt(2 / 0.02) - this.fl[i]) * 0.02;   // ~1.5 dB level flutter
      this.f[i] = F[i] * (1 + this.fj[i]); this.a[i] += (Aa[i] * (1 + this.fl[i]) - this.a[i]) * 0.05;
    }
    this.gain += (this.load - this.gain) * 0.05;
    // blended brake: wobbling tone in the last few km/h of a braked stop
    this.bwv += (r.next() * 0.6 - this.bwv) * 0.02; this.bw += (this.bwv - this.bw) * 0.05;
    this.bb = this.regen * ramp(this.speed, 0.004, 0.012) * (1 - ramp(this.speed, 0.08, 0.14));
  }
  sample() {
    if ((this.k++ & 63) === 0) this.control();
    if (this.gain < 1e-4 && this.speed < 1e-4) return 0;
    let y = 0;
    for (let i = 0; i < this.N; i++) {
      if (this.a[i] < 1e-5 || this.f[i] <= 0 || this.f[i] > this.sr * 0.45) continue;
      this.ph[i] += this.f[i] / this.sr; if (this.ph[i] >= 1) this.ph[i] -= 1;
      y += this.a[i] * Math.sin(TWO_PI * this.ph[i]);
    }
    let b = 0;
    if (this.bb > 1e-4) {
      const fb = this.P.brakeTone * (1 + 0.06 * this.bw); this.bph += fb / this.sr; if (this.bph >= 1) this.bph -= 1;
      b = (Math.sin(TWO_PI * this.bph) + 0.4 * Math.sin(TWO_PI * 1.31 * this.bph)) * this.bb * 0.35;
    }
    const rr = this.rumble.run(this.rng.next()) * 0.12 * this.speed;
    return (y * 0.22 * this.gain + rr + b * 0.22) * this.level;
  }
}

// v7: inside the car (see KISS_PARAMS.inside). One bank of 1/3-octave band-passes on one noise source; each band's gain
// = the standing bed + rolling(speed), updated every 64 samples. Plus HVAC/aux lines, switch clatter, the slow-braking
// 920 Hz line. (v7 also played a 'air exhaust' here: it is the sander, now class Sander.)
class InsideCar {
  constructor(sr, P, rng) {
    this.sr = sr; this.P = P; this.rng = rng; this.level = 0.6; this.lvS = 0; this.speed = 0; this.on = false; this.k = 0;
    // two cascaded band-passes per band (12 dB/oct skirts: the loud low bands must not leak into the quiet top end), each
    // normalised by its equivalent noise bandwidth so a band's output power is its table level
    this.bp = P.bands.map((f) => [new Biquad().set('bp', f, 2.8, sr), new Biquad().set('bp', f, 2.8, sr)]); this.g = new Float64Array(P.bands.length); this.gT = new Float64Array(P.bands.length);
    this.bw = this.bp.map(([q]) => { let e = 0; const N = 40000; for (let i = 1; i < N; i++) { const w = Math.PI * i / N, c1 = Math.cos(w), c2 = Math.cos(2 * w), s1 = Math.sin(w), s2 = Math.sin(2 * w);
      const nr = q.b0 + q.b1 * c1 + q.b2 * c2, ni = -(q.b1 * s1 + q.b2 * s2), dr = 1 + q.a1 * c1 + q.a2 * c2, di = -(q.a1 * s1 + q.a2 * s2), m = (nr * nr + ni * ni) / (dr * dr + di * di); e += m * m; } return Math.sqrt(3 * N / e); }); // uniform noise: variance 1/3
    this.tw = P.tones.map(([f]) => TWO_PI * f / sr); this.ta = P.tones.map(([, d]) => Math.SQRT2 * db(d)); this.tp = P.tones.map(() => 0);
    this.cw = P.comb.k.map((k) => TWO_PI * k * P.comb.f0 / sr); this.ca = P.comb.db.map((d) => Math.SQRT2 * db(d)); this.cp = P.comb.k.map(() => (rng.next() * 0.5 + 0.5) * TWO_PI);
    this.hits = []; this.distToEvent = this.nextEvent(); this.hitLp = new Biquad().set('lp', 4500, 0.7, sr); this.hitBp = new Biquad().set('hp', 300, 0.7, sr); this.hitEnv = 0;
    this.brPh = 0; this.brG = 0; this.braking = false; this.inside = false;
    this.ext = 0; this.wantExt = false; this.gR = new Float64Array(P.bands.length); this.gRT = new Float64Array(P.bands.length); // v10.3: the exterior part (no HVAC) for an open doorway
    // v8 layers: mode 'auto' (inside only) / 'on' (also outside; rolling at >= 55 mph when slower) / 'off', and a level each
    this.mode = { hvac: 'auto', rolling: 'auto', rumble: 'auto', clatter: 'auto' }; this.lv = { hvac: 1, rolling: 1, rumble: 1, clatter: 1 }; this.k_ = { hvac: 0, rolling: 0, rumble: 0, clatter: 0 };
    this.sm = 0;
  }
  nextEvent() { return -Math.log(1 - (this.rng.next() * 0.5 + 0.5) * 0.999) / this.P.clatter.perMeter; } // metres to the next switch / crossing (at the station rate below stationMph: x stationBoost)
  eff(k) { const m = this.mode[k]; return (m === 'off' ? 0 : m === 'on' ? 1 : this.inside ? 1 : 0) * this.lv[k]; }
  active() { return this.inside || ['hvac', 'rolling', 'rumble', 'clatter'].some((k) => this.mode[k] === 'on'); }
  clatterNow() { this.distToEvent = 0; this.forceClatter = true; } // one-shot: the next control block plays an event
  control(n) {
    const P = this.P, mph = this.speed * 79, dt = n / this.sr;
    for (const k in this.k_) this.k_[k] = this.eff(k);
    const rlOf = (k) => { const v = this.mode[k] === 'on' ? Math.max(mph, P.refMph) : mph; return v > 0.5 ? P.rollPerDecade * Math.log10(v / P.refMph) : -120; };
    const rRo = rlOf('rolling'), rRu = rlOf('rumble'), kH = this.k_.hvac ** 2, kRo = this.k_.rolling ** 2, kRu = this.k_.rumble ** 2;
    for (let i = 0; i < this.g.length; i++) { const lo = P.bands[i] < 250; // rumble / body boom = the rolling bands under 250 Hz
      const pr = (lo ? kRu : kRo) * Math.pow(10, (P.roll55[i] + (lo ? rRu : rRo)) / 10);
      this.gT[i] = Math.sqrt(kH * Math.pow(10, P.bed[i] / 10) + pr) * this.bw[i]; this.gRT[i] = Math.sqrt(pr) * this.bw[i]; }
    // switch / crossing clatter: the car's axles pass the same rail feature one after another
    const kC = this.k_.clatter;
    const cOn = this.mode.clatter === 'on'; // forced on: clatter at >= 40 mph even when standing
    if (((mph > 3 || cOn) && kC > 0) || this.forceClatter) {
      const vm = this.forceClatter || cOn ? Math.max(mph, 40) : mph, v = vm * 0.44704; this.distToEvent -= v * dt * (vm < P.clatter.stationMph ? P.clatter.stationBoost : 1) * Math.max(kC, 0.05) * (cOn ? P.clatter.forcedDensity : 1);
      if (this.distToEvent <= 0) {
        this.forceClatter = false;
        const C = P.clatter, amp = Math.pow(vm / 79, 0.8) * db(C.levelDb) * (0.5 + 0.5 * (this.rng.next() * 0.5 + 0.5)) * (kC > 0 ? Math.min(1.5, kC) : 1);
        C.axles.forEach((x, i) => this.hits.push({ t: x / v, a: amp * db(i < 2 ? C.nearDb : C.farDb) * (0.8 + 0.2 * this.rng.next()) }));
        this.distToEvent = this.nextEvent();
      }
    }
    for (const h of this.hits) h.t -= dt;
    while (this.hits.length && this.hits[0].t <= 0) { this.hitEnv = Math.max(this.hitEnv, this.hits[0].a); this.hits.shift(); }
    this.hits.sort((a, b) => a.t - b.t);
    // slow braking: the 920 Hz brake/aux line (owner's clip, last ~5 s of a stop)
    const bt = this.braking && mph < 20 && mph > 0.3 && this.inside ? 1 : 0; this.brG += (bt * db(P.brakeTone.db) - this.brG) * Math.min(1, dt / 0.3);
  }
  sample() {
    const on = this.active(), target = on ? (this.inside ? 0.4 + 0.6 * this.level : 0.6) : 0; this.lvS += (target - this.lvS) * 0.0005;
    if (this.lvS < 1e-4 && !on && this.hitEnv < 1e-5 && !this.forceClatter && !this.hits.length) { this.ext = 0; return 0; }
    if ((this.k++ & 63) === 0) this.control(64);
    const P = this.P, n = this.rng.next(), n2 = this.rng.next(), we = this.wantExt; let y = 0, yR = 0; // two independent sources on alternate bands: no cancellation where bands overlap
    for (let i = 0; i < this.bp.length; i++) { this.g[i] += (this.gT[i] - this.g[i]) * 0.02; const v = this.bp[i][1].run(this.bp[i][0].run(i & 1 ? n2 : n)); y += v * this.g[i];
      if (we) { this.gR[i] += (this.gRT[i] - this.gR[i]) * 0.02; yR += v * this.gR[i]; } }
    let t = 0; for (let i = 0; i < this.tw.length; i++) { this.tp[i] += this.tw[i]; if (this.tp[i] > TWO_PI) this.tp[i] -= TWO_PI; t += this.ta[i] * Math.sin(this.tp[i]); }
    const cm = db(P.comb.movingDb * Math.min(1, this.speed * 1.5));
    for (let i = 0; i < this.cw.length; i++) { this.cp[i] += this.cw[i]; if (this.cp[i] > TWO_PI) this.cp[i] -= TWO_PI; t += this.ca[i] * cm * Math.sin(this.cp[i]); }
    t *= this.k_.hvac;
    // clatter: each hit a short thud (noise burst, 12 ms decay) through the floor
    let h = 0; if (this.hitEnv > 1e-5) { h = this.hitLp.run(this.hitBp.run(n)) * this.hitEnv * 6; this.hitEnv *= Math.exp(-1 / (0.012 * this.sr)); }
    let b = 0; if (this.brG > 1e-5) { this.brPh += TWO_PI * P.brakeTone.f / this.sr; if (this.brPh > TWO_PI) this.brPh -= TWO_PI; b = Math.sin(this.brPh) * this.brG; }
    this.ext = we ? (yR * this.lvS + h * Math.max(this.lvS, 0.6)) * db(P.bedDb) : 0; // v10.3: rolling / rumble / clatter as outside (no HVAC, no brake line)
    return ((y + t + b) * this.lvS + h * Math.max(this.lvS, 0.6)) * db(P.bedDb);
  }
}

// v11 (t-0603, analysis/exterior_v11_inventory.md / .py / .json): the train as heard OUTSIDE, next to one end of it.
// Ranked by audibility in the exterior clips: (1) wheel/rail rolling noise whenever moving (1/3-octave pass-by shape,
// swelling once per car), (2) the brake-air exhaust after a stop (Stop -> Hiss, already in the engine), (3) traction
// while motoring / regenerating: a 40-400 Hz growl (motors, gears, cooling blowers), the motor's magnetic harmonics
// (lines rising with the stator frequency) and the inverter carrier's 2nd harmonic near 4 kHz (the carrier itself is
// the Motor whine), (4) squeal (Stop), (5) the aux converter's 120.3 Hz comb at 3.6-4.4 and 7.6-8.4 kHz, (6) rooftop
// HVAC / fan roar 40-400 Hz at standstill (the KISS idles quietly: -18 dBA re a close departure), (7) clatter over
// switches / crossings (the end car's four axles; at the REAR the same rail features arrive a train length later).
// One bank of 1/3-octave band-passes (two noise sources on alternate bands, as InsideCar) whose band gains are the
// power sum of the idle bed, the rolling and the growl, updated every 64 samples; tones by phasors. Off unless the
// engine gets {t:'exterior', on:true} (app/buses.js turns it on for every bus), so plain engine renders are unchanged.
// v11.1 (t-0620): the listener rides WITH the train (the speakers are on the costume): no per-car swell, steady rolling
// with a slow natural wander, the switch heard under each truck in turn (nearest loudest; KISS_PARAMS.outside.clatter).
class Outside {
  constructor(sr, P, rng, motor) {
    this.sr = sr; this.P = P; this.rng = rng; this.motor = motor; this.on = false; this.end = 'front'; this.k = 0; this.lvS = 0;
    this.speed = 0; this.load = 0; this.regen = 0; this.motoring = 0; this.A = 0; this.car = 0; this.carA = 0; this.inside = false;
    this.mode = { hvac: 'auto', rolling: 'auto', rumble: 'auto', clatter: 'auto', traction: 'auto' }; this.lv = { hvac: 1, rolling: 1, rumble: 1, clatter: 1, traction: 1 };
    this.bp = P.bands.map((f) => [new Biquad().set('bp', f, 2.8, sr), new Biquad().set('bp', f, 2.8, sr)]); this.g = new Float64Array(P.bands.length); this.gT = new Float64Array(P.bands.length);
    this.bw = this.bp.map(([q]) => { let e = 0; const N = 20000; for (let i = 1; i < N; i++) { const w = Math.PI * i / N, c1 = Math.cos(w), c2 = Math.cos(2 * w), s1 = Math.sin(w), s2 = Math.sin(2 * w);
      const nr = q.b0 + q.b1 * c1 + q.b2 * c2, ni = -(q.b1 * s1 + q.b2 * s2), dr = 1 + q.a1 * c1 + q.a2 * c2, di = -(q.a1 * s1 + q.a2 * s2), m = (nr * nr + ni * ni) / (dr * dr + di * di); e += m * m; } return Math.sqrt(3 * N / e); });
    const C = P.comb; this.cf = C.k.map((k) => k * C.f0).concat(C.lines.map((l) => l[0])); this.ca = C.k.map((k, i) => Math.SQRT2 * db(C.db[i] + P.idleDb + C.lineDb)).concat(C.lines.map((l) => Math.SQRT2 * db(l[1] + P.idleDb + C.lineDb))); this.cre = this.cf.map(() => 1); this.cim = this.cf.map(() => 0);
    this.crot = this.cf.map((f) => [Math.cos(TWO_PI * f / sr), Math.sin(TWO_PI * f / sr)]); this.cf.forEach((_, i) => { const a = (rng.next() + 1) * Math.PI; this.cre[i] = Math.cos(a); this.cim[i] = Math.sin(a); });
    this.mh = new Float64Array(P.motorHarm.k.length); this.mhA = new Float64Array(P.motorHarm.k.length); this.mp = new Float64Array(P.motorHarm.k.length); this.ph2 = 0; this.a2 = 0;
    this.blow = 0; this.fl = [0, 0, 0]; this.frng = new Rng(9091); // v11.1: rolling fluctuation (slow, fast, HF slow), dB
    this.mid = P.bands.map((f) => { const m = P.midDb.find((b) => b[0] === f); return m ? Math.pow(10, m[1] / 10) : 1; });
    this.crng = new Rng(4242); this.jrng = new Rng(5353); this.dist = this.nextEvent(); this.hits = []; this.hitEnv = 0; this.hitEnvF = 0; this.hitDk = Math.exp(-1 / (0.014 * sr)); this.hitDkF = Math.exp(-1 / (0.02 * sr));
    this.hitHp = new Biquad().set('hp', 250, 0.7, sr); this.hitLp = new Biquad().set('lp', 6000, 0.7, sr); this.hitLpF = [new Biquad().set('lp', P.clatter.lpFar, 0.7, sr), new Biquad().set('lp', P.clatter.lpFar, 0.7, sr)];
    this.setEnd('front'); this.w2 = 0; this.Lidle = P.idle.map((d) => Math.pow(10, d / 10)); this.Lroll = P.roll.map((d) => Math.pow(10, d / 10)); this.Lgrowl = P.growl.map((d) => Math.pow(10, d / 10));
  }
  setEnd(e) { const r = e === 'rear' ? 'rear' : 'front'; if (r === 'rear' && this.end !== 'rear') { this.rng = new Rng((this.rng.s ^ 0x5bd1e995) >>> 0 || 1); this.frng = new Rng(7177); } // the rear end: its own noise
    this.end = r; this.tilt = this.P.ends[r]; this.axl = this.axleTable(r); }
  // v11.1: [s after the switch reaches the train's front axle per m/s of speed -> distance travelled, gain, far] per audible axle
  axleTable(end) {
    const C = this.P.clatter, L = C.cars * C.carM, r0 = Math.hypot(C.axles[0], C.distM), out = [];
    for (let c = 0; c < C.cars; c++) for (const a of C.axles) {
      const x = c * C.carM + a, d = end === 'rear' ? L - x : x, r = Math.hypot(d, C.distM), g = Math.pow(r0 / r, C.spread);
      if (20 * Math.log10(g) >= C.cutDb) out.push({ x: x - C.axles[0], r, g, far: d > C.farM });
    }
    return out;
  }
  nextEvent() { return -Math.log(1 - (this.crng.next() * 0.5 + 0.5) * 0.999) * this.P.clatter.meanM; } // own stream: FRONT and REAR meet the same rail features
  clatterNow() { this.dist = 0; this.force = true; }
  eff(k) { const m = this.mode[k]; return m === 'off' ? 0 : this.lv[k]; }
  control(n) {
    const Mo = this.motor, MP = KISS_PARAMS.motor; this.speed = Mo.speed; this.A = Mo.speed * MP.aMax; this.regen = Mo.regen; this.motoring = Mo.throttle > 0.01 ? Mo.load : 0;
    this.car = Mo.f[2]; this.carA = Mo.a[2] * 0.22 * Mo.gain * Mo.level; // the Motor's carrier line as it plays it
    const P = this.P, dt = n / this.sr, mph = this.speed * 79, T = this.tilt;
    const kI = this.eff('hvac'), kRo = this.eff('rolling'), kRu = this.eff('rumble'), kT = this.eff('traction');
    // traction: motoring / regen effort, the blowers keep running a few seconds after (tau blowTau)
    const eff = this.mode.traction === 'on' ? 1 : Math.max(this.motoring, this.regen * P.regenK);
    this.blow += (eff - this.blow) * Math.min(1, dt / (eff > this.blow ? P.blowRise : P.blowTau));
    const gG = kT * kT * this.blow * this.blow * Math.pow(10, (P.growlDb + P.growlSpeedDb * Math.min(1, mph / 40)) / 10);
    // rolling: 30 dB / decade re refMph; above 1 kHz a further hfPerDecade (the roughness spectrum moves up with speed)
    const rv = this.mode.rolling === 'on' ? Math.max(mph, P.forcedMph) : mph, uv = this.mode.rumble === 'on' ? Math.max(mph, P.forcedMph) : mph;
    const fade = (v) => (v < 0.5 ? 0 : Math.min(1, v / P.fadeMph));
    const rDb = (v) => (v < 0.5 ? -200 : P.rollPerDecade * Math.log10(v / P.refMph));
    // v11.1: co-moving, no per-car swell: rail roughness makes the steady rolling wander a little (Ornstein-Uhlenbeck in dB)
    const F = P.fluct, fl = this.fl, ou = (i, sd, tau) => { const a = Math.min(1, dt / tau); fl[i] += -fl[i] * a + sd * Math.sqrt(2 * a) * gauss(this.frng); };
    ou(0, F.sdDb, F.tau); ou(1, F.fsdDb, F.ftau); ou(2, F.hsdDb, F.htau);
    const sw = Math.pow(10, (fl[0] + fl[1]) / 10), swH = Math.pow(10, fl[2] / 10);
    const pRo = kRo * kRo * fade(rv) * Math.pow(10, (P.rollDb + rDb(rv)) / 10) * sw, pRu = kRu * kRu * fade(uv) * Math.pow(10, (P.rollDb + rDb(uv)) / 10) * sw;
    const hfv = Math.max(rv, 1), hf = P.hfPerDecade * Math.log10(hfv / P.refMph), gI = kI * kI * Math.pow(10, P.idleDb / 10);
    for (let i = 0; i < this.g.length; i++) {
      const f = P.bands[i], lo = f < 250, hiDb = f > 1000 ? hf * Math.min(1, Math.log2(f / 1000)) + T.hfDb * Math.min(1, Math.log2(f / 1000) / 2) : 0;
      const p = gI * this.Lidle[i] + (lo ? pRu : pRo * Math.pow(10, hiDb / 10) * this.mid[i] * (f > 1000 ? swH : 1)) * this.Lroll[i] + gG * this.Lgrowl[i];
      this.gT[i] = Math.sqrt(p) * this.bw[i];
    }
    this.combK = kI; // aux converter: on whenever the train is powered; stronger under load (as inside)
    // motor magnetic harmonics: k x the stator frequency A, with the effort
    const M = P.motorHarm, mt = kT * this.blow * db(M.db);
    for (let i = 0; i < M.k.length; i++) { const f = M.k[i] * this.A; this.mhA[i] = f > 40 && f < 6000 ? mt * db(M.lv[i]) * ramp(this.A, 15, 60) : 0; this.mh[i] = TWO_PI * f / this.sr; }
    this.a2 = kT * this.carA * db(P.car2Db) / db(P.levelDb); this.w2 = TWO_PI * 2 * this.car / this.sr; // 2 x carrier, re the Motor's carrier line (levelDb undone)
    // clatter: the end car's four axles over a switch / crossing; the REAR end meets it a train length later
    const kC = this.eff('clatter'), cOn = this.mode.clatter === 'on';
    if (((mph > 3 || cOn) && kC > 0) || this.force) {
      const vm = this.force || cOn ? Math.max(mph, 40) : mph, v = vm * 0.44704, C = P.clatter;
      this.dist -= v * dt * (vm < C.stationMph ? C.stationBoost : 1) * (cOn ? C.forcedDensity : 1) * Math.max(kC, 0.05);
      if (this.dist <= 0) {
        const amp = Math.pow(vm / 79, 0.8) * db(C.db) * (0.6 + 0.4 * (this.crng.next() * 0.5 + 0.5)) * (kC > 0 ? Math.min(1.5, kC) : 1);
        const t0 = this.force ? Math.min(...this.axl.map((h) => h.x / v + h.r / 343)) : 0; // a forced one (the pad) starts now at either end
        for (let i = 0; i < this.axl.length; i++) { const h = this.axl[i]; this.hits.push({ t: h.x / v + h.r / 343 - t0, a: amp * h.g * db(i % 2 ? -1 : 0) * (0.8 + 0.2 * this.jrng.next()), far: h.far }); }
        this.force = false; this.dist = this.nextEvent();
      }
    }
    if (this.hits.length) { for (const h of this.hits) h.t -= dt; this.hits.sort((a, b) => a.t - b.t); while (this.hits.length && this.hits[0].t <= 0) { const h = this.hits.shift(); if (h.far) this.hitEnvF = Math.max(this.hitEnvF, h.a); else this.hitEnv = Math.max(this.hitEnv, h.a); } }
  }
  sample() {
    this.lvS += ((this.on ? 1 : 0) - this.lvS) * 0.0005;
    if (this.lvS < 1e-4 && !this.on) return 0;
    if ((this.k++ & 63) === 0) this.control(64);
    const n = this.rng.next(), n2 = this.rng.next(); let y = 0;
    for (let i = 0; i < this.bp.length; i++) { this.g[i] += (this.gT[i] - this.g[i]) * 0.02; if (this.g[i] > 1e-9) y += this.bp[i][1].run(this.bp[i][0].run(i & 1 ? n2 : n)) * this.g[i]; }
    let t = 0;
    if (this.combK > 0) { const cm = this.combK * (1 + this.P.combLoad * this.blow); for (let i = 0; i < this.cf.length; i++) { const r = this.crot[i], re = this.cre[i] * r[0] - this.cim[i] * r[1], im = this.cre[i] * r[1] + this.cim[i] * r[0]; this.cre[i] = re; this.cim[i] = im; t += this.ca[i] * im; } t *= cm;
      if ((this.k & 4095) === 0) for (let i = 0; i < this.cf.length; i++) { const m = 1 / Math.hypot(this.cre[i], this.cim[i]); this.cre[i] *= m; this.cim[i] *= m; } }
    for (let i = 0; i < this.mh.length; i++) if (this.mhA[i] > 1e-7) { let p = this.mp[i] + this.mh[i]; if (p > TWO_PI) p -= TWO_PI; this.mp[i] = p; t += this.mhA[i] * Math.sin(p); }
    if (this.a2 > 1e-7) { this.ph2 += this.w2; if (this.ph2 > TWO_PI) this.ph2 -= TWO_PI; t += this.a2 * Math.sin(this.ph2); }
    let h = 0; if (this.hitEnv > 1e-5) { h = this.hitLp.run(this.hitHp.run(n)) * this.hitEnv; this.hitEnv *= this.hitDk; }
    if (this.hitEnvF > 1e-5) { const L = this.hitLpF; h += L[1].run(L[0].run(n2)) * this.hitEnvF * 1.6; this.hitEnvF *= this.hitDkF; } // far trucks: duller, a little longer
    return (y + t + h) * this.lvS * db(this.P.levelDb);
  }
}

// v8: the sander - a ~1.52 kHz tone and ~2 kHz hiss in pulses (KISS_PARAMS.sand). Inside: +8.4 dB over the interior bed;
// outside a weak hiss under the train.
class Sander {
  constructor(sr, P, rng) {
    this.sr = sr; this.P = P; this.rng = rng; this.t = -1; this.ph = 0; this.level = 0.7;
    this.bp = new Biquad().set('bp', 2000, 0.8, sr); this.lp = new Biquad().set('lp', 6000, 0.7, sr);
  }
  trigger() { this.t = 0; this.jit = this.P.pulses.map(() => 0.9 + 0.2 * (this.rng.next() * 0.5 + 0.5)); }
  sample() {
    if (this.t < 0) return 0;
    const P = this.P, u0 = this.t / this.sr; let env = 0;
    P.pulses.forEach(([a, d, g], i) => { const dd = d * this.jit[i], u = u0 - a; if (u > 0 && u < dd + 0.06) env = Math.max(env, db(g) * Math.min(1, u / 0.01) * (u < dd ? 1 : Math.exp(-(u - dd) / 0.02))); });
    if (u0 > 2.0) { this.t = -1; return 0; }
    this.t++; this.ph += TWO_PI * P.f / this.sr; if (this.ph > TWO_PI) this.ph -= TWO_PI;
    const n = this.rng.next();
    return (this.lp.run(this.bp.run(n)) * 2.2 + Math.sin(this.ph) * db(P.toneDb)) * env * this.level;
  }
}

// v10 (t-0600): the doors played from the owner's reference recording itself (analysis/doors_v10_extract.py ->
// app/samples/doors_v10.wav / .flac / .json): r2_h_pdUsIA's open and close sequences, cleaned (1249 Hz line notched where
// present, cabin background down <= 10 dB), and the real obstruction re-open from N_VLcREWVVU. Same state machine as
// v9; here the playhead gives the state and the leaf position (markers from the video), so the icon follows the
// recording. Reversals are 30 ms equal-power crossfades: closing while opening -> the close sample where its leaves stand
// where they are now; opening while closing -> the re-open snippet (thud, 1.77 -> 2.2 kHz), then the open sample where
// its leaves stand there; a tap before the leaves move cancels (fade out). An uninterrupted sequence is the sample
// itself (sample-exact at the sample's 44.1 kHz; cubic interpolation at other rates). pcm: Float32Array at meta.sr.
class DoorRec {
  constructor(sr, pcm, meta) {
    this.sr = sr; this.x = pcm; this.M = meta; this.S = meta.segments; this.rate = meta.sr / sr; this.vo = []; this.main = null;
    this.state = 'closed'; this.p = 0; this.p0 = 0; this.xf = Math.round(0.03 * sr);
  }
  segT(v) { return v.i / this.M.sr - this.S[v.s].start; } // s into its segment
  posOf(s, t) { const S = this.S[s];
    if (s === 'reopen') return this.p0;
    const [a, b] = S.move, u = clamp((t - a) / (b - a), 0, 1); return s === 'open' ? 1 - (1 - u) * (1 - u) : 1 - u * u * (3 - 2 * u); }
  stOf(s, t) { return s === 'reopen' ? 'opening' : s === 'open' ? (t < this.S.open.move[1] ? 'opening' : 'open') : (t < this.S.close.move[1] ? 'closing' : 'closed'); }
  tOpen(p) { const [a, b] = this.S.open.move; return a + (1 - Math.sqrt(clamp(1 - p, 0, 1))) * (b - a); }
  tClose(p) { const [a, b] = this.S.close.move; let lo = 0, hi = 1; for (let k = 0; k < 30; k++) { const u = (lo + hi) / 2; if (1 - u * u * (3 - 2 * u) > p) lo = u; else hi = u; } return a + (lo + hi) / 2 * (b - a); }
  moving() { const v = this.main; if (!v) return false; if (v.s === 'reopen') return true; const t = this.segT(v), [a, b] = this.S[v.s].move; return t >= a && t < b; }
  play(s, t) { // crossfade from whatever plays into segment s at t s
    const fadeIn = this.vo.some((v) => v.dir >= 0) ? this.xf : 0;
    for (const v of this.vo) if (v.dir >= 0) { v.dir = -1; v.k = 0; v.N = this.xf; }
    const S = this.S[s], i0 = (S.start + t) * this.M.sr, v = { s, i: this.rate === 1 ? Math.round(i0) : i0, end: Math.round((S.start + S.dur) * this.M.sr), dir: fadeIn ? 1 : 0, k: 0, N: fadeIn };
    this.vo.push(v); this.main = v; this.state = this.stOf(s, t); this.p = this.posOf(s, t);
  }
  cancel(st, p) { for (const v of this.vo) if (v.dir >= 0) { v.dir = -1; v.k = 0; v.N = this.xf; } this.main = null; this.state = st; this.p = p; }
  open(force) {
    if (this.state === 'closing' && this.moving()) { this.p0 = this.p; return this.play('reopen', 0); } // obstruction re-open
    if (this.state === 'closing') { this.cancel('open', 1); if (!force) return; }
    if ((this.state === 'opening' || this.state === 'open') && !force) return;
    this.play('open', 0);
  }
  close(force) {
    if (this.state === 'opening' && this.moving()) return this.play('close', this.tClose(this.p)); // reverse from where the leaves are
    if (this.state === 'opening') { this.cancel('closed', 0); if (!force) return; } // released, not moving yet: cancel
    if ((this.state === 'closing' || this.state === 'closed') && !force) return;
    this.play('close', 0);
  }
  reopen() { if (this.state !== 'closing' || !this.moving()) { this.state = 'closing'; } this.p0 = this.state === 'closing' && this.moving() ? this.p : 0.4; this.play('reopen', 0); } // v8 'doorReopen'
  stop() { this.cancel(this.p >= 0.5 ? 'open' : 'closed', this.p >= 0.5 ? 1 : 0); }
  busy() { return this.vo.length > 0; }
  read(i) { const x = this.x; if (this.rate === 1) return x[i];
    const k = Math.floor(i), f = i - k, a = x[k - 1] || 0, b = x[k] || 0, c = x[k + 1] || 0, d = x[k + 2] || 0; // cubic Hermite (Catmull-Rom)
    return b + 0.5 * f * (c - a + f * (2 * a - 5 * b + 4 * c - d + f * (3 * (b - c) + d - a))); }
  sample() {
    let y = 0;
    for (let j = this.vo.length - 1; j >= 0; j--) {
      const v = this.vo[j]; let g = 1;
      if (v.dir > 0) { g = Math.sin(0.5 * Math.PI * v.k / v.N); if (++v.k >= v.N) v.dir = 0; }
      else if (v.dir < 0) { g = Math.cos(0.5 * Math.PI * v.k / v.N); if (++v.k >= v.N) { this.vo.splice(j, 1); continue; } }
      y += this.read(v.i) * g; v.i += this.rate;
      if (v === this.main && v.s === 'reopen' && !v.handed && v.i >= v.end - this.xf * this.rate) { v.handed = true; this.play('open', this.tOpen(this.p0)); continue; }
      if (v.i >= v.end) { this.vo.splice(j, 1); if (v === this.main) { this.main = null; this.state = v.s === 'open' ? 'open' : 'closed'; this.p = v.s === 'open' ? 1 : 0; } }
    }
    const m = this.main; if (m) { const t = this.segT(m); this.state = this.stOf(m.s, t); this.p = this.posOf(m.s, t); }
    return y;
  }
}

// v9 (t-0599): the door sequence, measured on the owner's reference r2_h_pdUsIA (analysis/doors_r2h.py). A state machine
// closed -> opening -> open -> closing -> closed that drives the door signal (this.tone, the Door above) and the
// mechanics: dull body thumps (~120 Hz), the push-out as the leaves start, the actuator whine (closing: 610 -> 872 ->
// 840 Hz; opening: faint), the slide noise, the seal thud, the lock and the after-sounds. The leaf position p (0 closed,
// 1 open) is tracked, so a tap during a motion reverses it from where it is: closing while opening closes from there;
// opening while closing is the obstruction re-open (a bump, the 2.2 kHz signal, the leaves back out; N_VLcREWVVU does
// exactly this); a tap before the leaves have moved cancels. Hits: [peak time s, type, dB re the closing tone, rise s,
// decay s to -12 dB]. Messages: doors() toggles; open(force) / close(force) (force: play it whatever the state, v8's
// doorOpen / doorClose); reopen(); button() (v8 passenger beep).
class DoorMech {
  constructor(sr, P, rng, tone) {
    this.sr = sr; this.P = P; this.rng = rng; this.tone = tone; this.level = 0.7; this.t = 0; this.ev = []; this.hits = [];
    this.state = 'closed'; this.p = 0; this.mv = null; this.wh = null; this.blip = null; this.beep = 0; this.bph = 0;
    const C = tone.P.close, h = C.harm.map(db); this.ref = 0.5 / h.reduce((a, b) => a + b, 0) * Math.sqrt(h.reduce((a, b) => a + b * b, 0) / 2); // closing tone RMS at level 1
    this.tn = {}; const nr = new Rng(977); // unit-RMS gain of each hit filter on white noise (own generator: the shared streams stay as they were)
    for (const k in P.hitTypes) { const f = this.chain(k); let s = 0; for (let i = 0; i < 16384; i++) { let v = nr.next(); for (const b of f) v = b.run(v); if (i >= 4096) s += v * v; } this.tn[k] = 1 / Math.sqrt(s / 12288); }
    this.nb = [new Biquad().set('lp', 300, 0.7, sr), new Biquad().set('lp', 300, 0.7, sr), new Biquad().set('hp', 1000, 0.7, sr)]; // slide noise: low (< 300 Hz) and high (> 1 kHz) parts, levels in open/close.slide
    this.cb = new Biquad().set('bp', 160, 1.2, sr);
    // v10: recorded doors (setSamples); mode 'recorded' (default once samples are loaded) or 'synth' (v9); outside an
    // approximation of the cabin recording heard on the platform: the cabin boom (< 150 Hz) does not carry outside
    this.rec = null; this.mode = 'recorded'; this.inside = false; this.recGain = 0; this.ohp = [new Biquad().set('hp', P.outsideHp, 0.7, sr), new Biquad().set('hp', P.outsideHp, 0.7, sr)];
    this.ref12 = 0.5 / h.reduce((a, b) => a + b, 0) * Math.sqrt((h[0] * h[0] + h[1] * h[1]) / 2); // closing signal h1+h2 RMS at level 1
  }
  setSamples(pcm, meta) { this.recStop(); this.rec = pcm && meta ? new DoorRec(this.sr, pcm, meta) : null; if (this.rec) { this.rec.state = this.state === 'open' || this.state === 'opening' ? 'open' : 'closed'; this.rec.p = this.rec.state === 'open' ? 1 : 0; this.recGain = this.ref12 / meta.tone_rms * db(this.P.recDb); } }
  setMode(m) { const on = this.recOn(); this.mode = m === 'synth' ? 'synth' : 'recorded'; if (on !== this.recOn()) this.recStop(); }
  recOn() { return !!this.rec && this.mode === 'recorded'; }
  recStop() { // switching voice mid-sequence: both stop, the doors settle at the nearer rest state
    const st = this.p >= 0.5 ? 'open' : 'closed'; if (this.rec) this.rec.stop(); this.ev = []; this.mv = null; this.wh = null; this.tone.stop();
    this.state = st; this.p = st === 'open' ? 1 : 0; if (this.rec) { this.rec.state = st; this.rec.p = this.p; } }
  chain(k) { return this.P.hitTypes[k].map(([ty, f, q, g]) => new Biquad().set(ty, f, q, this.sr, g || 0)); }
  at(dt, fn) { const t = this.t + Math.max(0, Math.round(dt * this.sr)); let i = this.ev.length; while (i > 0 && this.ev[i - 1][0] > t) i--; this.ev.splice(i, 0, [t, fn]); }
  hit([, type, d, rise, dec]) { // level above hitKnee compressed (hitRatio): the measured LF thumps (+13..+22 dB over the signal) would clip
    const P = this.P, dd = d > P.hitKnee ? P.hitKnee + (d - P.hitKnee) * P.hitRatio : d, sn = P.hitSine[type];
    this.hits.push({ f: this.chain(type), g: db(dd) * this.tn[type], n: 0, rise: Math.max(1, Math.round(rise * this.sr)), dk: Math.exp(-1.3816 / (dec * this.sr)), e: 1,
                     w: sn ? TWO_PI * sn[0] * (1 + 0.04 * this.rng.next()) / this.sr : 0, mix: sn ? sn[1] : 0, ph: 0, gn: this.tn[type] });
    const ly = P.hitLayer[type]; if (ly) this.hit([0, ly[0], dd + ly[1], rise, dec * 0.6]); }
  hitAt(h, off) { this.at(off + h[0] - h[3], () => this.hit(h)); }
  whine(W, dur) { this.wh = { W, n: 0, end: Math.round(dur * this.sr), ph: 0 }; }
  // sequences (times in s from now)
  doors() { if (this.state === 'closed' || this.state === 'closing') this.open(); else this.close(); }
  open(force) {
    if (this.recOn()) { this.rec.open(force); this.state = this.rec.state; this.p = this.rec.p; return; }
    if (this.state === 'closing' && (this.mv || this.p < 1)) return this.reverseToOpen();
    if (this.state === 'closing') { this.ev = []; this.tone.stop(); this.wh = null; this.state = 'open'; if (!force) return; }
    if ((this.state === 'opening' || this.state === 'open') && !force) return;
    const P = this.P.open; this.ev = []; this.mv = null; this.wh = null; this.p = 0; this.state = 'opening'; this.tone.play('open');
    for (const h of P.pre) this.hitAt(h, 0);
    this.openFrom(P.move);
  }
  close(force) {
    if (this.recOn()) { this.rec.close(force); this.state = this.rec.state; this.p = this.rec.p; return; }
    if (this.state === 'opening' && (this.mv || this.p > 0)) return this.reverseToClose();
    if (this.state === 'opening') { this.ev = []; this.tone.stop(); this.wh = null; this.state = 'closed'; if (!force) return; } // released, not moving yet: cancel
    if ((this.state === 'closing' || this.state === 'closed') && !force) return;
    this.ev = []; this.mv = null; this.wh = null; this.p = 1; this.state = 'closing'; this.tone.play('close');
    this.closeFrom(this.P.close.move);
  }
  reopen() { if (this.recOn()) { this.rec.reopen(); this.state = this.rec.state; this.p = this.rec.p; return; }
    if (this.state !== 'closing') { this.ev = []; this.state = 'closing'; this.p = 0.4; } this.reverseToOpen(); } // v8 'doorReopen'
  reverseToOpen() { // obstruction: the leaves stop, bump, the open signal, back out
    const R = this.P.reopen; this.ev = []; this.mv = null; this.wh = null; this.state = 'opening'; this.tone.stop();
    this.hit(R.bump); this.at(R.toneDelay, () => this.tone.play('open')); this.openFrom(R.move);
  }
  reverseToClose() { this.ev = []; this.mv = null; this.wh = null; this.state = 'closing'; this.tone.play('close'); this.closeFrom(this.P.reclose.move); }
  openFrom(M) {
    const P = this.P.open, L = Math.max(0.3, P.travel * (1 - this.p));
    this.at(M, () => { this.mv = { t0: this.t, n: Math.round(L * this.sr), p0: this.p, p1: 1, slide: P.slide }; });
    if (this.p <= 0) for (const h of P.push) this.hitAt(h, M);
    this.at(M + P.whine.start, () => this.whine(P.whine, Math.min(P.whine.dur, L)));
    this.at(M + L, () => { this.state = 'open'; this.p = 1; });
    this.at(M + L + P.toneAfter, () => this.tone.stop());
  }
  closeFrom(M) {
    const P = this.P.close, L = Math.max(0.4, P.travel * this.p), S = M + L; // S: the seal
    this.at(M, () => { this.mv = { t0: this.t, n: Math.round(L * this.sr), p0: this.p, p1: 0, slide: P.slide }; });
    const w0 = M + P.whine.start; this.at(w0, () => this.whine(P.whine, Math.max(0.25, S - P.whine.endBeforeSeal - w0)));
    if (P.knock[0] < L - 1) this.hitAt(P.knock, M);
    if (L + P.blip.at > 0.5) this.at(S + P.blip.at, () => { this.blip = { n: 0, N: Math.round(P.blip.dur * this.sr), ph: 0 }; });
    for (const h of P.seal) this.hitAt(h, S);
    this.at(S, () => { this.state = 'closed'; this.p = 0; });
    this.at(S + P.toneOff, () => this.tone.stop());
    this.at(S + P.after[0], () => this.tone.play('after')); this.at(S + P.after[1], () => this.tone.stop());
  }
  button() { this.beep = Math.round(this.P.beepLen * this.sr); this.bph = 0; }
  busy() { return this.ev.length > 0 || this.hits.length > 0 || this.mv || this.wh || this.blip || this.beep > 0; }
  sample() {
    let r = 0;
    if (this.rec && this.rec.busy()) { r = this.rec.sample() * this.recGain * this.tone.level; if (this.recOn()) { this.state = this.rec.state; this.p = this.rec.p; } // v10 recorded doors (level: Door sound)
      if (!this.inside) r = this.ohp[1].run(this.ohp[0].run(r)); }
    if (!this.busy()) { this.t++; return r; }
    const P = this.P, sr = this.sr;
    while (this.ev.length && this.ev[0][0] <= this.t) this.ev.shift()[1]();
    this.t++; let y = 0;
    for (let i = this.hits.length - 1; i >= 0; i--) { const h = this.hits[i]; let v = this.rng.next(); for (const b of h.f) v = b.run(v);
      v *= h.g; if (h.mix) { h.ph += h.w; v = v * (1 - h.mix) + Math.sin(h.ph) * Math.SQRT2 * h.mix * h.g / h.gn; } // body: a damped panel resonance + noise
      const a = h.n < h.rise ? 0.5 - 0.5 * Math.cos(Math.PI * h.n / h.rise) : (h.e *= h.dk); h.n++; y += v * a;
      if (h.n > h.rise && h.e < 1e-4) this.hits.splice(i, 1); }
    if (this.mv) { const m = this.mv, k = this.t - m.t0, u = Math.min(1, k / m.n), sh = m.p1 > m.p0 ? 1 - (1 - u) * (1 - u) : u * u * (3 - 2 * u);
      this.p = m.p0 + (m.p1 - m.p0) * sh; const e = Math.min(1, k / (0.1 * sr), (m.n - k) / (0.15 * sr)), n = this.rng.next();
      y += (this.nb[1].run(this.nb[0].run(n)) * db(m.slide[0]) * 2.4 + this.nb[2].run(n) * db(m.slide[1]) * 1.15) * Math.max(0, e);
      if (k >= m.n) this.mv = null; }
    if (this.wh) { const w = this.wh, W = w.W, u = w.n / sr, tr = W.track; let f = tr[tr.length - 1][1];
      for (let j = 1; j < tr.length; j++) if (u < tr[j][0]) { const [t0, f0] = tr[j - 1], [t1, f1] = tr[j]; f = f0 + (f1 - f0) * (u - t0) / (t1 - t0); break; }
      w.ph += TWO_PI * f / sr; if (w.ph > TWO_PI) w.ph -= TWO_PI;
      const e = Math.min(1, u / W.rise, (w.end - w.n) / (W.fall * sr)); y += (Math.sin(w.ph) + db(W.h2) * Math.sin(2 * w.ph)) * db(W.db) * Math.SQRT2 * e; if (++w.n >= w.end) this.wh = null; }
    if (this.blip) { const b = this.blip, B = P.close.blip; b.ph += TWO_PI * B.f / sr; y += Math.sin(b.ph) * Math.sin(Math.PI * b.n / b.N) * db(B.db) * Math.SQRT2; if (++b.n >= b.N) this.blip = null; }
    if (this.beep > 0) { const L = Math.round(P.beepLen * sr), u = L - this.beep, r = Math.min(1, u / (0.005 * sr), this.beep / (0.005 * sr)); this.beep--;
      this.bph += TWO_PI * P.beepHz / sr; if (this.bph > TWO_PI) this.bph -= TWO_PI; y += Math.sin(this.bph) * r * db(P.beepDb) * Math.SQRT2; }
    return y * this.ref * this.level + r;
  }
}

// Air whistle shared by the air release and the brake stop: f0 ~3.61 kHz, h2 (7.21 kHz) strongest, h1/h3 -15/-16 dB,
// h4 weak; it wanders in small steps (+-30 Hz) and flutters +-4 dB.
class Whistle {
  constructor(sr, rng, f0, harm) { this.sr = sr; this.rng = rng; this.f0 = f0; this.h = harm.map(db); this.ph = 0; this.fw = 0; this.fwT = 0; this.lv = 0; this.lvT = 0; this.k = 0; }
  sample(bright) {
    if ((this.k++ & 255) === 0) { if (this.rng.next() > 0.85) this.fwT = this.rng.next() * 0.004; this.lvT = this.rng.next() * 0.45; }
    this.fw += (this.fwT - this.fw) * 0.0008; this.lv += (this.lvT - this.lv) * 0.0006;
    this.ph += this.f0 * (1 + this.fw) / this.sr; if (this.ph >= 1) this.ph -= 1;
    const p = TWO_PI * this.ph, h = this.h;
    return (h[0] * Math.sin(p) + h[1] * Math.sin(2 * p) + h[2] * bright * Math.sin(3 * p) + h[3] * bright * Math.sin(4 * p)) * (1 + this.lv);
  }
}

// Air release (v2, analysis/air_detail.py): broadband hiss (strongest 1-4 kHz, 4-6.5 kHz ~15 dB down) reaching its
// plateau in ~0.2 s while the 9-14 kHz part and the whistle keep building (~+15 / +10 dB over ~1.5 s); the whistle (h2
// 7.2 kHz) ends up the strongest component; at the end the top end goes first. A one-shot of `dur` s.
class Hiss {
  constructor(sr, P, rng) {
    this.sr = sr; this.P = P; this.rng = rng; this.t = 0; this.act = false; this.level = 0.7; this.o = { whistleScale: 1, midScale: 1, hfScale: 1 };
    this.hp = new Biquad().set('hp', 1800, 0.7, sr); this.pk = new Biquad().set('peak', 3200, 0.8, sr, 8);
    this.lp = new Biquad().set('lp', 4000, 0.8, sr); this.lp2 = new Biquad().set('peak', 5300, 1.2, sr, -12); this.hf = new Biquad().set('hp', 6500, 0.7, sr); this.hf2 = new Biquad().set('lp', 14000, 0.7, sr);
    this.wh = new Whistle(sr, rng, P.whistleF0, P.whistleHarm);
  }
  trigger(o) { // o (optional, the stop's release at standstill): {dur, whistleScale, midScale, hfScale}
    this.o = o || { whistleScale: 1, midScale: 1, hfScale: 1 }; this.t = o ? -Math.round((o.delay || 0) * this.sr) : 0; this.act = true;
    this.dur = (o && o.dur || this.P.dur) * (0.9 + 0.2 * (this.rng.next() * 0.5 + 0.5)); }
  sample() {
    if (!this.act) return 0;
    if (this.t < 0) { this.t++; return 0; }
    const t = this.t / this.sr, d = this.dur; this.t++;
    if (t > d + 0.8) { this.act = false; return 0; }
    const on = Math.min(1, t / 0.2) ** 1.5, rel = t < d ? 1 : Math.exp(-(t - d) / 0.25), relHf = t < d ? 1 : Math.exp(-(t - d) / 0.1);
    const body = on * (t < d ? 1 - 0.15 * t / d : 0.85) * rel, grow = 1 - Math.exp(-t / this.P.hfBuild);
    const n = this.rng.next(), mid = this.lp2.run(this.lp.run(this.pk.run(this.hp.run(n)))), hi = this.hf2.run(this.hf.run(n));
    const wOn = Math.min(1, t / 0.25), wOff = t < d ? 1 : Math.exp(-(t - d) / 0.04);
    const w = this.wh.sample(0.3 + 0.7 * grow) * wOn * wOff * (0.3 + 0.7 * grow);
    const o = this.o; return (mid * 0.45 * o.midScale * body + hi * this.P.hfLv * o.hfScale * (0.16 + 0.84 * grow) * relHf * on + w * this.P.whistleLv * o.whistleScale) * this.level;
  }
}

// Train stop (v3, analysis/stop_analysis.py on 5 platform arrivals + the owner's interior clips; replaces v2's squeal,
// whose 7 kHz whistle was 16 dB over everything: real stops put it 13-22 dB under the main squeal, A-weighted).
// What a stop consists of, loudest first (A-weighted): (1) a squeal CLUSTER of 5-6 near-equal partials across
// 1.75-2.26 kHz, 30-60 Hz apart, wandering, with slow lumps (~0.8/1.6 Hz, 3-5 dB) + 5 Hz wobble + 20-60 Hz chatter,
// from ~30 mph down to ~3 mph, spreading out and getting lumpier at low speed; (2) brake/rolling noise 200-1400 Hz
// within a few dB of it; (3) a weaker high squeal band (~7 kHz) and a 2.7-2.9 kHz cluster; (4) low rumble < 200 Hz
// (weak A-weighted, strongest unweighted) with a groan in the last metres; (5) at standstill a ~0.35 s low thump
// (peak ~30-60 Hz) and, 0.4 s later, the brake-air release (2-8 kHz, whistle inside its band, not on top).
// Moving: the motor brakes the train down (regen whine falling, blending out below ~10 mph) while this plays at the
// matching speeds. Already stopped: plays the last ~8 s of a stop on its own.
class Stop {
  constructor(sr, P, rng, motor, hiss) {
    this.sr = sr; this.P = P; this.rng = rng; this.motor = motor; this.hiss = hiss; this.level = 0.5; this.on = false; this.v = 0; this.own = false;
    this.k = 0; this.thump = -1; this.airT = -1; this.N = 8; this.ph = new Float64Array(this.N); this.fo = new Float64Array(this.N); this.fw = new Float64Array(this.N);
    this.fwT = new Float64Array(this.N); this.am = new Float64Array(this.N); this.amT = new Float64Array(this.N); this.lump = 0; this.lumpT = 0; this.chat = 0;
    this.roll = new Biquad().set('bp', 600, 0.5, sr); this.roll2 = new Biquad().set('lp', 1400, 0.7, sr); this.lf = new Biquad().set('lp', 140, 0.7, sr);
    this.gr1 = new Biquad().set('bp', 260, 2.0, sr); this.gr2 = new Biquad().set('bp', 470, 2.0, sr); this.hi = new Biquad().set('bp', 7000, 3.0, sr);
    this.th = new Biquad().set('lp', 90, 0.8, sr); this.thPh = 0; this.groan = 0; this.groanT = 0; this.sq = 0; this.g = new Float64Array(5);
  }
  trigger(standalone) { // standalone (v8 'squeal'): the last seconds of a stop even while the train moves on
    if (this.on) return;
    const r = this.rng, P = this.P; this.on = true; this.thump = -1; this.airT = -1;
    this.own = standalone || this.motor.speed < 0.02; this.v = this.own ? P.standaloneFrom : this.motor.speed; this.fin = this.own ? 0.7 : 0;
    if (!this.own) { this.motor.stopping = true; this.motor.throttle = 0; }
    // cluster partials for this stop: offsets from the centre (Hz) and starting levels, as measured (1.75-2.26 kHz)
    const offs = [-280, -150, -75, -20, 30, 80, 140, 760]; // last one = the weaker 2.7-2.9 kHz cluster
    for (let i = 0; i < this.N; i++) { this.fo[i] = P.squealF + offs[i] * (1 + 0.15 * r.next()); this.fw[i] = 0; this.am[i] = 0; }
  }
  sample() {
    if (!this.on && this.airT < 0) return 0;
    const P = this.P, r = this.rng, dt = 1 / this.sr;
    if ((this.k++ & 63) === 0) { // control rate
      const cdt = 64 / this.sr;
      if (this.on) {
        if (this.own) this.v = Math.max(0, this.v - cdt * (P.decelBase + P.decelProp * this.v));
        else { this.v = this.motor.speed; if (!this.motor.stopping && this.v > 0.02) { this.on = false; this.motor.stopping = false; } } // cancelled by power
        if (this.v <= 0.006 && this.thump < 0) this.thump = 0;
      }
      const v = this.v, low = 1 - ramp(v, 0.05, 0.16); // 0 at speed, 1 in the last metres
      // squeal: in from ~30 mph, a little louder as it slows, gone by ~3 mph
      const sqT = this.on ? ramp(v, 0.02, 0.05) * (1 - ramp(v, 0.3, 0.38)) * (1 + 0.4 * low) : 0;
      this.sq += (sqT - this.sq) * 0.05;
      // lumps (slow random, 3-5 dB) and per-partial wander; wider and faster when slow (stick-slip)
      if ((this.k & 4095) === 1) this.lumpT = gauss(r) * P.lumpDb * 0.5;
      this.lump += (this.lumpT - this.lump) * 0.02;
      for (let i = 0; i < this.N; i++) {
        if ((r.next() * 0.5 + 0.5) < 0.01) { this.fwT[i] = gauss(r) * (P.wanderHz + 30 * low); this.amT[i] = gauss(r) * 4; }
        this.fw[i] += (this.fwT[i] - this.fw[i]) * 0.01; this.am[i] += (this.amT[i] - this.am[i]) * 0.01;
      }
      const gT = this.on ? 1 : 0;
      // brake/rolling noise (to the last metre) and low rumble: fade in over ~1.5 s after the press, ~0.15 s out at standstill
      this.fin = Math.min(1, (this.fin || 0) + cdt / 1.5);
      const g0T = this.on && v > 0.003 ? (0.3 + 0.7 * Math.pow(v / 0.3, 0.5)) * this.fin : 0, g1T = this.on ? Math.min(1, v / 0.25) * this.fin : 0;
      this.g[0] += (g0T - this.g[0]) * 0.01; this.g[1] += (g1T - this.g[1]) * 0.01;
      if ((this.k & 8191) === 1) this.groanT = (r.next() * 0.5 + 0.5) < 0.6 ? 1 : 0.2;    // groan bursts in the last metres
      this.groan += (this.groanT * low * (v > 0.006 ? 1 : 0) * gT - this.groan) * 0.01;
      if (this.thump >= 0 && this.on && this.thump > 0.4) { this.on = false; this.motor.stopping = false; this.airT = 0; this.hiss.trigger(P.air); }
    }
    const v = this.v; let y = 0;
    // squeal cluster
    if (this.sq > 1e-4) {
      const ch = 1 + P.chatter * Math.sin(TWO_PI * 5.1 * this.k / this.sr) * (0.5 + 0.5 * Math.sin(TWO_PI * 0.37 * this.k / this.sr));
      this.chat += (r.next() - this.chat) * 0.02; // 20-60 Hz chatter (filtered noise AM)
      let c = 0;
      for (let i = 0; i < this.N; i++) {
        this.ph[i] += (this.fo[i] + this.fw[i]) * dt; if (this.ph[i] >= 1) this.ph[i] -= 1;
        c += Math.sin(TWO_PI * this.ph[i]) * db(this.am[i] + (i === this.N - 1 ? P.cluster2Db : 0));
      }
      y += c * this.sq * db(this.lump) * ch * (1 + 0.25 * this.chat) * P.squealLv;
    }
    const n = r.next();
    if (this.g[0] > 1e-4) y += this.roll2.run(this.roll.run(n)) * this.g[0] * P.rollLv + this.hi.run(n) * this.sq * P.hiLv * db(this.lump);
    if (this.g[1] > 1e-4) y += this.lf.run(n) * this.g[1] * P.lfLv;
    if (this.groan > 1e-4) y += (this.gr1.run(n) + 0.7 * this.gr2.run(n)) * this.groan * P.groanLv;
    if (this.thump >= 0 && this.thump < 0.6) { // ~0.35 s low thump at standstill (peak 30-60 Hz)
      const t = this.thump; this.thump += dt; this.thPh += 38 * dt;
      const e = Math.min(1, t / 0.02) * Math.exp(-t / 0.11);
      y += (Math.sin(TWO_PI * this.thPh) * 0.8 + this.th.run(n) * 3) * e * P.thumpLv;
    }
    if (this.airT >= 0) { this.airT += dt; if (this.airT > 0.5) this.airT = -1; }
    return y * this.level;
  }
}

// Interior ambient (v2, owner's recordings inside a KISS car, analysis/interior_detail.py): an HVAC bed (white noise
// through a 45 Hz high-pass and one-pole low-passes at 100 Hz and 1.5 kHz: strongest at 50-200 Hz, ~-4 dB/oct to 1 kHz,
// ~-7 dB/oct above), the aux-converter tones (3.96 kHz, 0.92 kHz), and rolling noise that grows with speed (+3 dB bed,
// +6-8 dB at 1.6-6 kHz at cruise). Off unless the 'Interior ambient' level is raised.
class Interior {
  constructor(sr, P, rng) {
    this.sr = sr; this.P = P; this.rng = rng; this.level = 0; this.lvS = 0; this.speed = 0;
    this.hp = new Biquad().set('hp', 45, 0.7, sr); this.a1 = 1 - Math.exp(-TWO_PI * 70 / sr); this.a2 = 1 - Math.exp(-TWO_PI * 700 / sr); this.z1 = 0; this.z2 = 0;
    this.a3 = 1 - Math.exp(-TWO_PI * 3000 / sr); this.r1 = 0; this.r2 = 0; this.a3 = 1 - Math.exp(-TWO_PI * 3000 / sr); this.r1 = 0; this.r2 = 0;
    this.f = P.rolling.map(([ty, f, q]) => new Biquad().set(ty, f, q, sr)); this.gr = P.rolling.map((b) => db(b[3]));
    this.tw = P.tones.map(([f]) => TWO_PI * f / sr); this.ta = P.tones.map(([, d]) => db(d)); this.tp = P.tones.map(() => 0); this.am = 0; this.amT = 0; this.k = 0;
  }
  sample() {
    this.lvS += (this.level - this.lvS) * 0.0005;
    if (this.lvS < 1e-4) return 0;
    if ((this.k++ & 1023) === 0) this.amT = this.rng.next() * 0.25; this.am += (this.amT - this.am) * 0.0005; // slow body/rail swell
    const n = this.rng.next(), sp = this.speed;
    this.z1 += (this.hp.run(n) - this.z1) * this.a1; this.z2 += (this.z1 - this.z2) * this.a2;
    let r = 0; for (let i = 0; i < this.f.length; i++) r += this.f[i].run(n) * this.gr[i];
    this.r1 += (r - this.r1) * this.a3; this.r2 += (this.r1 - this.r2) * this.a3; r = this.r2; // keep the skirts off the top end
    let t = 0; for (let i = 0; i < this.tw.length; i++) { this.tp[i] += this.tw[i]; if (this.tp[i] > TWO_PI) this.tp[i] -= TWO_PI; t += this.ta[i] * Math.sin(this.tp[i]); }
    t *= 1 + 1.5 * sp; // the ~4 kHz aux line is ~8 dB stronger at cruise in the moving clip
    return (this.z2 * this.P.bed * (1 + this.P.bedSpeed * sp) + r * sp * (1 + this.am) + t) * this.P.level * this.lvS;
  }
}

// Acoustic environment (v2). 'dry' = as measured close to the train; 'platform' = early reflections from the train
// body / canopy + a modest diffuse tail (the open-station clips keep their tails under -25 dB within 0.2 s);
// 'distance' = ~150 m away: air absorption, a ground reflection that drifts as the train moves, relatively more tail.
class Environment {
  constructor(sr) {
    this.sr = sr; this.mode = 'dry'; const L = [1557, 1617, 1491, 1422].map((n) => Math.round(n * sr / 44100));
    this.dl = L.map((n) => new Float32Array(n)); this.di = [0, 0, 0, 0]; this.lpz = [0, 0, 0, 0]; this.o = [0, 0, 0, 0];
    this.er = new Float32Array(Math.round(0.12 * sr)); this.ei = 0; this.gr = new Float32Array(Math.round(0.01 * sr)); this.gi = 0; this.gLag = Math.round(0.0018 * sr);
    this.airLp = new Biquad(); this.airHs = new Biquad(); this.k = 0; this.t = 0; this.wetS = 0; this.dryS = 1;
    this.set('dry');
  }
  set(mode) {
    const sr = this.sr; this.mode = mode;
    const M = { dry: { rt: 0.6, wet: 0, dry: 1, damp: 0.3, er: 0 }, platform: { rt: 1.1, wet: db(-17), dry: 1, damp: 0.35, er: db(-11) },
                distance: { rt: 1.8, wet: db(-12), dry: db(-6), damp: 0.55, er: db(-9) } }[mode] || { rt: 0.6, wet: 0, dry: 1, damp: 0.3, er: 0 };
    this.M = M; this.fb = this.dl.map((d) => Math.pow(10, -3 * d.length / sr / M.rt));
    this.taps = mode === 'platform' ? [[0.007, 0.8], [0.013, -0.6], [0.023, 0.5], [0.037, -0.35], [0.052, 0.25]]
              : [[0.031, 0.6], [0.058, -0.45], [0.083, 0.35], [0.11, 0.25]];
    this.taps = this.taps.map(([s, g]) => [Math.round(s * sr), g]);
    this.airLp.set('lp', 5200, 0.6, sr); this.airHs.set('peak', 9000, 0.5, sr, -6);
  }
  run(x) {
    const M = this.M; this.wetS += (M.wet - this.wetS) * 0.0005; this.dryS += (M.dry - this.dryS) * 0.0005;
    if (this.mode === 'dry' && this.wetS < 1e-4) return x * this.dryS;
    let d = x;
    if (this.mode === 'distance') { // air absorption + ground reflection (path difference 1.8 ms)
      // v8.2: the path difference no longer drifts (v1-v8.1: 1.2-2.4 ms at 0.07 Hz, rounded to whole samples): its comb
      // notches swept through the harmonics (h10 cycling ~1 Hz, h20 ~2 Hz: a phaser on a held note) with ~12 Hz zipper steps
      d = this.airHs.run(this.airLp.run(x)); const g = this.gr, n = g.length; g[this.gi] = d;
      d = d - 0.55 * g[(this.gi - this.gLag + n) % n]; this.gi = (this.gi + 1) % n;
    }
    // early reflections
    const e = this.er, ne = e.length; e[this.ei] = d; let er = 0;
    for (const [s, gg] of this.taps) er += gg * e[(this.ei - s + ne) % ne];
    this.ei = (this.ei + 1) % ne;
    // 4-line FDN (Householder feedback), one-pole damping in the loop
    const o = this.o; for (let j = 0; j < 4; j++) o[j] = this.dl[j][this.di[j]];
    const s4 = (o[0] + o[1] + o[2] + o[3]) * 0.5, inp = d + er * 0.5;
    let wet = 0;
    for (let j = 0; j < 4; j++) {
      const v = (o[j] - s4) * this.fb[j]; this.lpz[j] += (v - this.lpz[j]) * (1 - M.damp);
      this.dl[j][this.di[j]] = inp * 0.35 + this.lpz[j]; if (++this.di[j] >= this.dl[j].length) this.di[j] = 0; wet += o[j];
    }
    return d * this.dryS + er * M.er + wet * this.wetS;
  }
}

class KissEngine {
  constructor(sr, seed = 12345) {
    const rng = new Rng(seed), P = KISS_PARAMS; this.sr = sr;
    this.horn = new Horn(sr, P.horn, rng); this.bell = new Bell(sr, P.bell, rng); this.door = new Door(sr, P.door); this.interior = new Interior(sr, P.interior, rng);
    this.motor = new Motor(sr, P.motor, rng); this.hiss = new Hiss(sr, P.hiss, rng); this.stop = new Stop(sr, P.stop, rng, this.motor, this.hiss);
    this.env = new Environment(sr);
    // v7 inside the car: interior layers + exterior sounds through the body / floor
    const B = P.inside.body; this.inside = new InsideCar(sr, P.inside, new Rng(seed + 7)); this.isInside = false; this.hissWas = false; // own random stream: Outside renders stay identical
    this.sander = new Sander(sr, P.sand, new Rng(seed + 11)); this.doorMech = new DoorMech(sr, P.doorMech, new Rng(seed + 13), this.door); // v8; v9: drives the door signal
    this.outside = new Outside(sr, P.outside, new Rng(seed + 17), this.motor); // v11: the exterior soundscape (off until {t:'exterior', on:true})
    this.autoSand = 'inside'; this.sandAt = -1; this.stopWas = false; this.whineOn = true; this.timers = []; this.hissBody = [new Biquad().set('hs', B.shelfHz, 0.7, sr, B.shelfDb), new Biquad().set('lp', 6000, 0.7, sr)];
    this.hornBody = [new Biquad().set('hs', B.shelfHz, 0.7, sr, B.shelfDb), new Biquad().set('lp', 6000, 0.7, sr)];
    this.bellBody = [new Biquad().set('hs', B.shelfHz, 0.7, sr, B.shelfDb), new Biquad().set('lp', 6000, 0.7, sr)];
    this.floorM = [new Biquad().set('lp', B.motorLp, 0.7, sr), new Biquad().set('lp', B.motorLp, 0.7, sr)]; this.floorS = new Biquad().set('lp', 600, 0.7, sr);
    this.gIn = { h: db(B.hornDb), b: db(B.bellDb), d: db(B.doorDb), m: db(B.motorDb), s: db(B.stopDb) };
    // v10 (owner): exterior sounds get louder inside while the doors are open. No clip gives open vs shut for the same
    // exterior sound (in r2_h_pdUsIA the cabin HVAC dominates: the ambience rises only ~1.4 dB with the doors open), so
    // this is a physical estimate. v10.3 (t-0604, owner: not dramatic enough): the open doorway adds the exterior sounds
    // as heard outside (the raw sum ex, NOT through each sound's body loss), broadband at doorLeak.db re outside, scaled
    // by the leaf position and the Mix 'Door leak' level (leakV, def = doorLeak.db), smoothed (tau) so there are no steps.
    // Every exterior sound adds its outside sample to ex in render(); a component's exterior part may come as its .ext
    // (InsideCar: rolling / rumble / clatter without the HVAC; app/buses.js applies the bus gain to .ext too)
    // Leveller (the loud horn): the leak alone is turned down (attack att, release rel) so the inside mix stays under
    // peakDb at the output (INSIDE strip at its defaults: gain -4 dB, limiter -12 dBFS = -8 dBFS here), so the doors
    // never push the INSIDE limiter into pumping; with the doors shut nothing changes.
    const LK = P.inside.doorLeak; this.dOpen = 0; this.dOk = tauCoef(sr, LK.tau); this.leakV = LK.def; this.leakG = db(LK.db) / LK.def;
    const DO = P.doorMech.outside; this.doorOutG = db(DO.db); this.doorOutF = [new Biquad().set('hs', DO.shelfHz, 0.7, sr, DO.shelfDb), new Biquad().set('lp', DO.lpHz, 0.7, sr)]; // v11.1: doors on the exterior speakers
    this.lkT = db(LK.peakDb); this.lkGl = 1; this.lkPb = 0; this.lkPl = 0; this.lkA = tauCoef(sr, LK.att); this.lkR = tauCoef(sr, LK.rel); this.lkPk = Math.exp(-1 / (LK.pkRel * sr));
    this.master = 0.8; this.masterS = 0.8; this.oG = P.out.gain; this.oK = P.out.knee; this.pattern = []; this.t = 0; this.manualHorn = false; this.patternOn = false;
  }
  // Messages: {t:'horn',on} {t:'bell',on} {t:'door',on} {t:'throttle',v} {t:'hiss'} {t:'stop'} (alias 'squeal')
  //           {t:'intensity',k,v} {t:'master',v} {t:'accel',v} {t:'pattern',seq:[[startS,durS],...]} {t:'stopPattern'}
  //           {t:'env',v:'dry'|'platform'|'distance'|'approaching'} {t:'doorStyle',v:'kiss'|'steady'} (intensity k:'interior' = ambient level)
  //           v9: {t:'doors',v:'toggle'|'open'|'close'} the door sequence (state() reports doors / doorPos)
  //           v10: {t:'doorSamples',pcm,meta} the recorded doors (app/samples), {t:'doorSound',v:'recorded'|'synth'}
  msg(m) {
    switch (m.t) {
      case 'horn': this.manualHorn = !!m.on; break;
      case 'bell': this.bell.setOn(!!m.on); break;
      case 'door': this.door.setGate(!!m.on); break;
      case 'env': { const v = String(m.v); this.env.set(v === 'approaching' || v === 'inside' ? 'dry' : v); // approaching: in front of a moving train
        this.isInside = v === 'inside'; this.inside.on = this.isInside; this.inside.inside = this.isInside; // v7: inside the car
        this.doorMech.inside = this.isInside; this.inside.wantExt = false; this.horn.doppler = v === 'approaching' ? KISS_PARAMS.horn.approachDoppler : 1; this.horn.far = v === 'approaching' || v === 'distance'; break; }
      case 'doorStyle': this.door.setStyle(String(m.v)); break;
      case 'throttle': this.motor.throttle = clamp(+m.v, 0, 1); break;
      case 'accel': this.motor.accelTime = clamp(+m.v, 1, 120); break;
      case 'hiss': this.hiss.trigger(); break;
      case 'stop': this.stop.trigger(); break;
      case 'squeal': this.stop.trigger(true); break; // v8: squeal on its own, the train keeps going
      case 'master': this.master = clamp(+m.v, 0, 1.5); break;
      case 'intensity': {
        const v = clamp(+m.v, 0, 1);
        if (m.k === 'horn') this.horn.setIntensity(v); else if (m.k === 'breath') this.horn.setBreath(v); else if (m.k === 'bell') this.bell.level = v;
        else if (m.k === 'motor') this.motor.level = v; else if (m.k === 'door') this.door.level = v;
        else if (m.k === 'hiss') { this.hiss.level = v; this.stop.level = v * 0.7; }
        else if (m.k === 'interior') { this.interior.level = v; this.inside.level = v; }
        else if (m.k === 'sand') this.sander.level = v; else if (m.k === 'doorMech') this.doorMech.level = v;
        else if (m.k === 'doorLeak') this.leakV = v; // v10.3: Door leak (0 = v10's shut path only)
        break;
      }
      // v8: door parts, sand, clatter, layers
      case 'doors': { const v = String(m.v || 'toggle'); if (v === 'open') this.doorMech.open(); else if (v === 'close') this.doorMech.close(); else this.doorMech.doors(); break; } // v9
      case 'doorSamples': this.doorMech.setSamples(m.pcm, m.meta); break; // v10: {pcm: Float32Array at meta.sr, meta: app/samples/doors_v10.json}
      case 'doorSound': this.doorMech.setMode(String(m.v)); break;        // v10: 'recorded' (default when samples are loaded) | 'synth' (v9)
      case 'doorOpen': this.doorMech.open(true); break; // v8 one-shots: play the sequence whatever the state
      case 'doorClose': this.doorMech.close(true); break;
      case 'doorReopen': this.doorMech.reopen(); break;
      case 'doorButton': this.doorMech.button(); break;
      case 'sand': this.sander.trigger(); break;
      case 'clatter': if (this.outside.on && !this.isInside) this.outside.clatterNow(); else this.inside.clatterNow(); break; // v11: outside, the end car's axles
      case 'exterior': if (m.on != null) this.outside.on = !!m.on; if (m.end) this.outside.setEnd(String(m.end)); break; // v11: {t:'exterior', on, end:'front'|'rear'}
      case 'autoSand': this.autoSand = String(m.v); break; // 'off' | 'inside' | 'always'
      case 'layer': if (m.k === 'whine') this.whineOn = m.mode !== 'off'; else if (m.k in this.inside.mode) this.inside.mode[m.k] = String(m.mode);
        if (m.mode && m.k in this.outside.mode) this.outside.mode[m.k] = String(m.mode); // v11: the exterior layers (traction is outside only)
        if (m.v != null) { if (m.k in this.inside.lv) this.inside.lv[m.k] = clamp(+m.v, 0, 3); if (m.k in this.outside.lv) this.outside.lv[m.k] = clamp(+m.v, 0, 3); } break;
      case 'pattern': this.pattern = m.seq.map(([s, d]) => [this.t + Math.round(s * this.sr), this.t + Math.round((s + d) * this.sr)]); break;
      case 'stopPattern': this.pattern = []; break;
    }
  }
  render(out, n) {
    for (let i = 0; i < n; i++) {
      let pat = false;
      if (this.pattern.length) {
        while (this.pattern.length && this.t >= this.pattern[0][1]) this.pattern.shift();
        pat = this.pattern.length > 0 && this.t >= this.pattern[0][0];
      }
      this.horn.setGate(this.manualHorn || pat);
      this.masterS += (this.master - this.masterS) * 0.001;
      let x;
      if (!this.isInside) {
        const h = this.horn.sample(), bl = this.bell.sample(), dr = this.door.sample(), mo = this.motor.sample(), hi = this.hiss.sample(), st = this.stop.sample(); // same order as v7 (shared random stream)
        const sd = this.sander.sample() * db(KISS_PARAMS.sand.outsideDb); let dm = this.doorMech.sample();
        if (this.outside.on) { const F = this.doorOutF; dm = F[1].run(F[0].run(dm + dr)) * this.doorOutG; } else dm += dr; // v11.1: the inside door recording, quieter outside
        x = this.env.run(h + bl + (this.whineOn ? mo : 0) + hi + st + sd + dm + (this.outside.on || this.outside.lvS > 1e-4 ? this.outside.sample() : 0)) + this.interior.sample();
        if (this.inside.lvS > 1e-4 || this.inside.active() || this.inside.hitEnv > 1e-5 || this.inside.forceClatter || this.inside.hits.length) x += this.inside.sample(); // forced layers, a clatter one-shot, fading out after leaving the car
      } else { // v7: heard from inside the car
        const g = this.gIn, hb = this.hornBody, bb = this.bellBody, fm = this.floorM;
        const o = (this.dOpen += (this.doorMech.p * this.leakV - this.dOpen) * this.dOk), lk = o > 1e-6; this.inside.wantExt = lk; // v10.3: open doorway (0 shut .. 1 open x Door leak)
        const h0 = this.horn.sample(), h = hb[1].run(hb[0].run(h0)) * g.h;
        const b0 = this.bell.sample(), b = bb[1].run(bb[0].run(b0)) * g.b;
        const m0 = this.motor.sample(), mo = fm[1].run(fm[0].run(m0)) * g.m * (this.whineOn ? 1 : 0);
        const s0 = this.stop.sample(), st = this.floorS.run(s0) * g.s;
        const a0 = this.hiss.sample(), hs = this.hissBody[1].run(this.hissBody[0].run(a0)) * g.s; // the air release through the body (v8; v7 played the sander here)
        this.inside.speed = this.motor.speed; this.inside.braking = this.motor.stopping || this.motor.regen > 0.2;
        const sd0 = this.sander.sample(); // inside: under the floor at a fixed level; v10: plus its outside sound through an open doorway
        const ic = this.inside.sample(); // own random stream: sampled before the doorway sum (needs its .ext)
        const ex = lk ? h0 + b0 + (this.whineOn ? m0 : 0) + s0 + a0 + sd0 * db(KISS_PARAMS.sand.outsideDb) + this.inside.ext + (this.outside.on ? this.outside.sample() : 0) : 0; // v10.3: exterior sounds as outside; v11: + the exterior soundscape
        x = this.env.run(h + b + this.door.sample() * g.d + mo + st + hs + this.doorMech.sample() * g.d) + ic + sd0 * db(KISS_PARAMS.inside.bedDb + KISS_PARAMS.sand.insideDb) * 2.75 * 1.4;
        if (lk) { // v10.3: + the doorway (env is 'dry' = linear inside), levelled against the worst case (in phase with the rest)
          const xl = ex * o * this.leakG, ab = Math.abs(x), al = Math.abs(xl), Tx = this.lkT / Math.max(1e-6, this.masterS * this.oG);
          this.lkPb = ab > this.lkPb ? ab : this.lkPb * this.lkPk; this.lkPl = al > this.lkPl ? al : this.lkPl * this.lkPk;
          const t = this.lkPb + this.lkPl <= Tx ? 1 : Math.max(0, (Tx - this.lkPb) / this.lkPl);
          this.lkGl += (t - this.lkGl) * (t < this.lkGl ? this.lkA : this.lkR); x += xl * this.lkGl;
        } else this.lkGl = 1;
      }
      this.interior.speed = this.motor.speed;
      // v8: auto sand as the train comes to rest after a STOP (off / inside only / always), and timed actions
      const stopOn = this.stop.on; if (this.stopWas && !stopOn && this.motor.speed < 0.01 && (this.autoSand === 'always' || (this.autoSand === 'inside' && this.isInside))) this.sandAt = this.t + Math.round(KISS_PARAMS.sand.autoDelay * this.sr);
      this.stopWas = stopOn; if (this.sandAt >= 0 && this.t >= this.sandAt) { this.sandAt = -1; this.sander.trigger(); }
      if (this.timers.length) { for (const tm of this.timers) if (this.t >= tm[0]) tm[1](); this.timers = this.timers.filter((tm) => this.t < tm[0]); }
      const v = x * this.masterS * this.oG, av = Math.abs(v); // v8.2: linear below oK, soft knee above (to 1.0)
      out[i] = av < this.oK ? v : Math.sign(v) * (this.oK + (1 - this.oK) * Math.tanh((av - this.oK) / (1 - this.oK)));
      this.t++;
    }
  }
  state() { return { speed: this.motor.speed, A: this.motor.speed * KISS_PARAMS.motor.aMax, patternBusy: this.pattern.length > 0, doors: this.doorMech.state, doorPos: this.doorMech.p, doorSound: this.doorMech.recOn() ? 'recorded' : 'synth' }; }
}
