const test = require("node:test");
const assert = require("node:assert/strict");
const Sky = require("../renderer/sky.js");

test("starCount scales with area and the 별 slider, is capped, and 0 area / 0 density behave", () => {
  assert.equal(Sky.starCount(0, 0, 1), 0);
  assert.ok(Sky.starCount(980, 120, 0.5) > Sky.starCount(980, 120, 0.1));
  assert.ok(Sky.starCount(980, 640, 1) > Sky.starCount(980, 120, 1));
  assert.equal(Sky.starCount(5000, 5000, 1), Sky.MAX_STARS);
  assert.ok(Sky.starCount(980, 640, 0) > 0, "slider at 0 still lays out a sparse base set (intensity 0 hides them)");
  assert.equal(Sky.starCount(980, 640, 9), Sky.starCount(980, 640, 1)); // out-of-range density is clamped
});
test("layout: deterministic per seed, three depths in the intended proportions, values in range", () => {
  const a = Sky.layout(200, 7), b = Sky.layout(200, 7), c = Sky.layout(200, 8);
  assert.deepEqual(a, b); assert.notDeepEqual(a, c);
  const by = id => a.filter(s => s.depth === id).length;
  assert.equal(by("far") + by("mid") + by("near"), a.length);
  assert.ok(by("far") > by("mid") && by("mid") > by("near") && by("near") >= 10);
  for (const s of a) { assert.ok(s.x >= 0 && s.x <= 1 && s.y >= 0 && s.y <= 1); assert.ok(s.alpha > 0 && s.alpha <= 1); assert.ok(s.r > 0 && s.r < 2); }
  const far = a.filter(s => s.depth === "far"), near = a.filter(s => s.depth === "near");
  assert.ok(Math.max(...far.map(s => s.r)) <= Math.min(...near.map(s => s.r)) + 1e-9, "tiny / medium / bright by depth");
});
test("only a handful of the brightest near stars flare (max 5), and all flares are near stars", () => {
  const stars = Sky.layout(400, 3);
  const flares = stars.filter(s => s.flare);
  assert.ok(flares.length >= 1 && flares.length <= 5);
  assert.ok(flares.every(s => s.depth === "near"));
  assert.equal(Sky.layout(10, 3).filter(s => s.flare).length <= 5, true);
});
test("constellation: short local edges between bright stars only, deterministic, empty for tiny skies", () => {
  const stars = Sky.layout(300, 11);
  const edges = Sky.constellation(stars, { points: 6, maxLength: 0.28 });
  assert.ok(edges.length >= 1 && edges.length <= 5);
  for (const [p, q] of edges) { assert.notEqual(p.depth, "far"); assert.notEqual(q.depth, "far"); assert.ok(Math.hypot(p.x - q.x, p.y - q.y) <= 0.28 + 1e-9); }
  assert.deepEqual(Sky.constellation(stars, { points: 6, maxLength: 0.28 }).map(e => e.map(s => [s.x, s.y])), edges.map(e => e.map(s => [s.x, s.y])));
  assert.deepEqual(Sky.constellation([], {}), []); assert.deepEqual(Sky.constellation(Sky.layout(3, 1), { points: 6 }).length <= 1, true);
  assert.deepEqual(Sky.constellation(stars, { points: 6, maxLength: 0 }), []);
});
test("twinkle: bounded 0.55..1 when on, constant 0.85 when off", () => {
  const s = Sky.layout(50, 2)[0];
  for (let t = 0; t < 20; t += 0.37) { const v = Sky.twinkle(s, t, true); assert.ok(v >= 0.55 - 1e-9 && v <= 1 + 1e-9); assert.equal(Sky.twinkle(s, t, false), 0.85); }
  const vals = new Set(Array.from({ length: 40 }, (_, i) => Sky.twinkle(s, i * 0.31, true).toFixed(3)));
  assert.ok(vals.size > 10, "actually varies");
});
test("parallax: near stars drift faster than far ones, drift wraps in 0..1, higher 별 속도 drifts faster", () => {
  // Measure displacement modulo the unit width (positions wrap, so compare travelled distance, not raw coordinates).
  const travelled = (star, t, speed) => { const d = Sky.driftX(star, t, speed) - star.x; return d < 0 ? d + 1 : d; };
  const far = { x: 0.5, parallax: 0.35 }, near = { x: 0.5, parallax: 1.2 };
  assert.ok(travelled(near, 40, 0.5) > travelled(far, 40, 0.5) && travelled(far, 40, 0.5) > 0);
  assert.ok(travelled(near, 40, 1) > travelled(near, 40, 0.5) && travelled(near, 40, 0.5) > travelled(near, 40, 0));
  assert.ok(travelled(near, 40, 0) > 0, "speed 0 is slow but not frozen");
  for (const t of [0, 1, 1e3, 1e6]) for (const sp of [0, 0.5, 1, 7]) { const x = Sky.driftX(near, t, sp); assert.ok(x >= 0 && x < 1, `${t} ${sp} ${x}`); }
  assert.equal(Sky.driftX(near, 0, 0.5), 0.5);
});
test("meteors: scheduled 8-14 s apart, streak starts and ends invisible, head moves, tail trails", () => {
  const rand = Sky.mulberry32(5);
  for (let i = 0; i < 200; i += 1) { const d = Sky.nextMeteorDelay(rand); assert.ok(d >= 8000 && d <= 14000, String(d)); }
  const m = Sky.newMeteor(Sky.mulberry32(9));
  assert.ok(m.duration >= 650 && m.duration <= 1100);
  const p0 = Sky.meteorPoint(m, 0), pm = Sky.meteorPoint(m, 0.5), p1 = Sky.meteorPoint(m, 1);
  assert.ok(p0.alpha < 1e-9 && p1.alpha < 1e-9 && pm.alpha > 0.99);
  assert.ok(Math.abs(pm.x - p0.x) > 0.05, "head travels");
  assert.ok(Math.hypot(pm.x - pm.tailX, pm.y - pm.tailY) > 0.05, "visible tail at mid-life");
  assert.ok(Math.sign(pm.x - pm.tailX) === m.dir, "the tail trails behind the direction of travel");
  assert.equal(Sky.meteorPoint(m, -3).alpha < 1e-9, true); assert.equal(Sky.meteorPoint(m, 9).alpha < 1e-9, true); // out-of-range life is clamped
});
test("intensity: stars and nebula follow their OWN sliders; low glass opacity does not dim them", () => {
  const lowGlass = Sky.intensity({ stars: 0.5, nebula: 0.5, glassAlpha: 0.08 });
  const highGlass = Sky.intensity({ stars: 0.5, nebula: 0.5, glassAlpha: 0.9 });
  assert.ok(lowGlass.star === highGlass.star, "stars ignore glass opacity entirely");
  assert.ok(lowGlass.nebula >= highGlass.nebula, "nebula never gets dimmer on thinner glass");
  assert.ok(lowGlass.nebula > 0.3 && lowGlass.star > 0.6, "still clearly visible at the lowest glass alpha");
  assert.equal(Sky.intensity({ stars: 0, nebula: 0 }).star, 0); assert.equal(Sky.intensity({ stars: 0, nebula: 0 }).nebula, 0); assert.equal(Sky.intensity({ stars: 0, nebula: 0 }).aurora, 0);
  assert.ok(Sky.intensity({ stars: 1 }).star > Sky.intensity({ stars: 0.2 }).star);
  assert.ok(Sky.intensity({ nebula: 5 }).nebula <= 1.15 + 1e-9); // clamped
});
test("shouldDraw: ~30 fps cap, paused when hidden, nothing when motion is reduced", () => {
  assert.equal(Sky.shouldDraw({ now: 100, last: 70, visible: true, still: false }), false);   // 30 ms < 33
  assert.equal(Sky.shouldDraw({ now: 104, last: 70, visible: true, still: false }), true);    // 34 ms
  assert.equal(Sky.shouldDraw({ now: 1e6, last: 0, visible: false, still: false }), false);   // collapsed/hidden bar
  assert.equal(Sky.shouldDraw({ now: 1e6, last: 0, visible: true, still: true }), false);     // reduced motion = one still frame
});
test("space.js wiring: one rAF loop, capped, gated on visibility and reduced motion (static check)", () => {
  const src = require("node:fs").readFileSync(require("node:path").join(__dirname, "..", "renderer", "space.js"), "utf8");
  assert.equal((src.match(/requestAnimationFrame\(/g) || []).length, 2, "a single loop (kick + frame)");
  assert.match(src, /Sky\.shouldDraw/); assert.match(src, /document\.body\.dataset\.revealed === "true"/);
  assert.match(src, /still\(\)\) \{ draw\(performance\.now\(\)\); return; \}/);
  assert.match(src, /LOOP_SECONDS = 75/); assert.match(src, /plus|lighter/);
  assert.doesNotMatch(src, /Math\.random/, "no unseeded randomness in the render path");
});
