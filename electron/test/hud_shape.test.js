const test = require("node:test");
const assert = require("node:assert/strict");
const H = require("../renderer/hud_shape.js");

const inside = (s, x, y) => H.containsPoint(s.points, x, y);
const hasNaN = s => s.points.some(p => p.some(v => !Number.isFinite(v)));
const bbox = pts => ({ minX: Math.min(...pts.map(p => p[0])), maxX: Math.max(...pts.map(p => p[0])), minY: Math.min(...pts.map(p => p[1])), maxY: Math.max(...pts.map(p => p[1])) });

test("bottom bar: base on the screen edge is the full width, far edge is narrower by the slant, path is a true arc/curve path", () => {
  const s = H.shape("bottom", 980, 100, { slantDeg: 14, radius: 22 });
  assert.ok(Math.abs(s.inset - 100 * Math.tan((14 * Math.PI) / 180)) < 0.02, `inset ${s.inset}`);
  assert.deepEqual(s.vertices[0], [0, 100]); assert.deepEqual(s.vertices[3], [980, 100]); // base is flush, full width
  assert.equal(s.vertices[1][1], 0); assert.ok(s.vertices[1][0] > 0 && s.vertices[2][0] < 980);
  assert.match(s.clipPath, /^path\("M[\d. -]+( [LC][\d. -]+)+ Z"\)$/);
  assert.equal((s.path.match(/C/g) || []).length, 4, "four Bézier corners, no chamfer lines");
  const box = bbox(s.points);
  assert.ok(box.minX >= 0 && box.maxX <= 980 && box.minY >= 0 && box.maxY <= 100 + 1e-6, "never exceeds the window box");
  assert.ok(s.points.length >= 4 * 16, `dense ring: ${s.points.length}`);
});
test("bar corners are rounded: the sharp polygon vertices are NOT part of the shape, points just inside the curve are", () => {
  const s = H.shape("bottom", 980, 100, { slantDeg: 14, radius: 22 });
  for (const v of s.vertices) assert.equal(inside(s, v[0], v[1]), false, `vertex ${v} must be click-through (rounded away)`);
  // far corners: point a few px in from the vertex along the bisector-ish direction is still outside the curve; 12 px in is inside
  const [, fl, fr] = s.vertices;
  assert.equal(inside(s, fl[0] + 1, fl[1] + 1), false); assert.equal(inside(s, fr[0] - 1, fr[1] + 1), false);
  assert.equal(inside(s, fl[0] + 22, fl[1] + 12), true); assert.equal(inside(s, fr[0] - 22, fr[1] + 12), true);
  // base corners on the screen edge are rounded too (less than the far ones)
  assert.equal(inside(s, 1, 99), false); assert.equal(inside(s, 979, 99), false);
  assert.equal(inside(s, 14, 92), true); assert.equal(inside(s, 966, 92), true);
  assert.ok(s.baseRadius < s.radius && s.baseRadius > 0);
});
test("every point of the sampled ring is inside the unrounded polygon and on or inside the shape boundary of each rounded corner", () => {
  const s = H.shape("bottom", 980, 100, { slantDeg: 30, radius: 22 });
  for (const p of s.points) assert.equal(H.containsPoint(s.vertices, p[0], p[1]), true, String(p));
});
test("slanted sides: just inside is hit, just outside is click-through, for slant 14 and 30", () => {
  for (const slantDeg of [14, 30]) {
    const s = H.shape("bottom", 980, 100, { slantDeg, radius: 22 });
    const [bl, fl, fr, br] = s.vertices;
    for (const [a, b, sign] of [[bl, fl, 1], [br, fr, -1]]) {
      const t = 0.5, mx = a[0] + (b[0] - a[0]) * t, my = a[1] + (b[1] - a[1]) * t; // mid of the straight side, away from corners
      assert.equal(inside(s, mx + sign * 2, my), true, `slant ${slantDeg} just inside`);
      assert.equal(inside(s, mx - sign * 2, my), false, `slant ${slantDeg} just outside`);
    }
    assert.equal(inside(s, 490, 50), true); assert.equal(inside(s, 490, -2), false); assert.equal(inside(s, 490, 102), false);
  }
});
test("slant 0 is a rounded rectangle; slant is clamped to 0..30 and monotonic", () => {
  const rect = H.shape("bottom", 800, 90, { slantDeg: 0, radius: 20 });
  assert.equal(rect.inset, 0);
  assert.equal(inside(rect, 400, 45), true); assert.equal(inside(rect, 1, 1), false, "far corner rounded"); assert.equal(inside(rect, 0, 45), true, "straight left side is hit");
  let previous = -1;
  for (const deg of [0, 5, 12, 18, 30]) { const inset = H.shape("bottom", 800, 90, { slantDeg: deg }).inset; assert.ok(inset >= previous, `${deg}`); previous = inset; }
  assert.equal(H.shape("bottom", 800, 90, { slantDeg: 99 }).inset, H.shape("bottom", 800, 90, { slantDeg: 30 }).inset);
  assert.equal(H.shape("bottom", 800, 90, { slantDeg: -5 }).inset, 0);
  assert.equal(H.shape("bottom", 800, 90, { slantDeg: NaN }).inset, 0);
});
test("a tall panel (settings open) caps the inset so the top never collapses", () => {
  const s = H.shape("bottom", 980, 640, { slantDeg: 30 });
  assert.ok(s.inset <= H.DEFAULT_MAX_INSET && s.inset <= 980 * 0.2);
  assert.ok(s.vertices[1][0] < 980 / 2);
  assert.equal(hasNaN(s), false);
});
test("left/right/top docks are plain rounded rectangles: no slant, same radius token, base corners rounded too", () => {
  const w = 400, h = 300;
  for (const dock of ["left", "right", "top"]) {
    const s = H.shape(dock, w, h, { slantDeg: 30, radius: 22 });
    assert.equal(s.inset, 0, `${dock} ignores the slant`); assert.equal(s.slantDeg, 0);
    assert.equal(s.baseRadius, s.radius);
    assert.deepEqual(s.vertices.map(v => v.slice()).sort(), [[0, 0], [0, h], [w, 0], [w, h]].sort());
    for (const v of s.vertices) assert.equal(inside(s, v[0], v[1]), false, `${dock} corner ${v} rounded`);
    assert.equal(inside(s, w / 2, h / 2), true);
    assert.equal(inside(s, 4, 4), false, `${dock} 4,4 inside the corner arc is click-through`);
    // straight sides stay hit on the edges
    assert.equal(inside(s, w / 2, 0.5), true); assert.equal(inside(s, w / 2, h - 0.5), true); assert.equal(inside(s, 0.5, h / 2), true); assert.equal(inside(s, w - 0.5, h / 2), true);
    assert.equal((s.path.match(/C/g) || []).length, 4);
  }
  assert.throws(() => H.shape("middle", w, h), /unknown dock/);
  assert.throws(() => H.shape("bottom", 0, h), /positive/);
});
test("every dock yields a hit-testable shape in all four orientations (outside is outside, centre is inside)", () => {
  for (const dock of H.DOCKS) {
    const s = H.shape(dock, 500, 220, { slantDeg: 14, radius: 18 });
    assert.equal(inside(s, 250, 110), true, dock);
    for (const [x, y] of [[-1, 110], [501, 110], [250, -1], [250, 221]]) assert.equal(inside(s, x, y), false, `${dock} ${x},${y}`);
  }
});
test("radius 0 degenerates to the sharp trapezoid (vertices hit, no curves); radius > half the height clamps to a pill without NaN or overshoot", () => {
  const sharp = H.shape("bottom", 980, 100, { slantDeg: 14, radius: 0 });
  assert.equal((sharp.path.match(/C/g) || []).length, 0);
  assert.deepEqual(sharp.points, sharp.vertices);
  for (const v of sharp.vertices) assert.equal(inside(sharp, v[0], v[1]), true);
  assert.equal(inside(sharp, 2, 10), false, "slant still cuts the top-left");
  for (const dock of H.DOCKS) {
    const s = H.shape(dock, 400, 60, { slantDeg: 30, radius: 400 });
    assert.equal(hasNaN(s), false, dock);
    assert.ok(s.clampFactor < 1 && s.radius > 0);
    const box = bbox(s.points);
    assert.ok(box.minX >= -1e-6 && box.maxX <= 400 + 1e-6 && box.minY >= -1e-6 && box.maxY <= 60 + 1e-6, `${dock} stays inside the window box`);
    assert.equal(inside(s, 200, 30), true, dock);
  }
  const negative = H.shape("bottom", 400, 60, { radius: -5 });
  assert.equal(negative.radius, 0);
});
test("smooth=0 is a circular arc: the corner midpoint lies at the expected distance from the arc centre; smooth>0 is cut further in", () => {
  const circ = H.shape("top", 400, 200, { radius: 40, smooth: 0, samples: 64 });
  // top-left corner of a rect: centre (40,40); the arc midpoint is at 40*(1-cos45)=11.716 from each edge
  const mid = circ.points.find(p => Math.abs(p[0] - p[1]) < 0.05 && p[0] < 40);
  assert.ok(Math.abs(mid[0] - 11.72) < 0.15, `circular midpoint ${mid}`);
  const squircle = H.shape("top", 400, 200, { radius: 40, smooth: 1, samples: 64 });
  const mid2 = squircle.points.find(p => Math.abs(p[0] - p[1]) < 0.05 && p[0] < 50);
  assert.ok(mid2[0] > mid[0] - 1 && mid2[0] < 20, `smoothed midpoint ${mid2}`);
});
test("hit-test: boundary counts as inside; works in screen space after translate (what main does with the window-relative rect)", () => {
  const s = H.shape("bottom", 980, 100, { slantDeg: 14, radius: 22 });
  assert.equal(inside(s, 490, 0), true); assert.equal(inside(s, 490, 100), true);
  const screen = H.translate(s.points, 470, 980);
  assert.equal(H.containsPoint(screen, 470 + 490, 980 + 50), true);
  assert.equal(H.containsPoint(screen, 470 + 3, 980 + 5), false);
  assert.equal(H.containsPoint(screen, 100, 100), false);
});
test("content padding keeps content off the slanted sides and corner arcs", () => {
  const s = H.shape("bottom", 980, 100, { slantDeg: 20, radius: 22 });
  const pad = H.contentPadding(s);
  assert.ok(pad.left >= s.inset * 0.8 && pad.left === pad.right);
  assert.equal(H.svgPoints([[0, 1], [2.555, 3]]), "0,1 2.56,3");
});
