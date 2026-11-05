const test = require("node:test");
const assert = require("node:assert/strict");
const H = require("../renderer/hud_shape.js");

test("bottom trapezoid: base on the screen edge is the full width, far edge is narrower by the slant", () => {
  const s = H.shape("bottom", 980, 100, { slantDeg: 14 });
  assert.equal(s.points.length, 4);
  const [bl, tl, tr, br] = s.points;
  assert.deepEqual([bl, br], [[0, 100], [980, 100]]); // base is flush with the bottom edge, full width
  assert.equal(tl[1], 0); assert.equal(tr[1], 0);
  assert.ok(tl[0] > 0 && tr[0] < 980, "far edge is inset on both sides");
  assert.ok(Math.abs(tl[0] - (980 - tr[0])) < 0.02); // symmetric
  assert.ok(Math.abs(s.inset - 100 * Math.tan((14 * Math.PI) / 180)) < 0.02, `inset ${s.inset}`);
  assert.match(s.clipPath, /^polygon\(0px 100px, [\d.]+px 0px, [\d.]+px 0px, 980px 100px\)$/);
});
test("slant 0 is a rectangle; larger slant narrows the far edge monotonically; slant is clamped to 0..30", () => {
  const rect = H.shape("bottom", 800, 90, { slantDeg: 0 });
  assert.deepEqual(rect.points, [[0, 90], [0, 0], [800, 0], [800, 90]]);
  let previous = -1;
  for (const deg of [0, 5, 12, 18, 30]) { const inset = H.shape("bottom", 800, 90, { slantDeg: deg }).inset; assert.ok(inset >= previous, `${deg}`); previous = inset; }
  assert.equal(H.shape("bottom", 800, 90, { slantDeg: 99 }).inset, H.shape("bottom", 800, 90, { slantDeg: 30 }).inset);
  assert.equal(H.shape("bottom", 800, 90, { slantDeg: -5 }).inset, 0);
  assert.equal(H.shape("bottom", 800, 90, { slantDeg: NaN }).inset, 0);
});
test("a tall panel (settings open) caps the inset so the top never collapses", () => {
  const s = H.shape("bottom", 980, 640, { slantDeg: 30 });
  assert.ok(s.inset <= H.DEFAULT_MAX_INSET && s.inset <= 980 * 0.2);
  assert.ok(s.points[1][0] < 980 / 2);
});
test("every dock puts the wide base on its own screen edge", () => {
  const w = 400, h = 300;
  const bottom = H.shape("bottom", w, h, { slantDeg: 14 }).points, top = H.shape("top", w, h, { slantDeg: 14 }).points;
  assert.ok(bottom.filter(p => p[1] === h).length === 2 && bottom.filter(p => p[1] === 0).length === 2);
  assert.ok(top.filter(p => p[1] === 0).length === 2);
  assert.ok(top.filter(p => p[1] === h).every(p => p[0] > 0 && p[0] < w), "top dock: far (bottom) edge is inset");
  const left = H.shape("left", w, h, { slantDeg: 14 }).points, right = H.shape("right", w, h, { slantDeg: 14 }).points;
  assert.ok(left.filter(p => p[0] === 0).length === 2 && left.filter(p => p[0] > 0).every(p => p[1] > 0 && p[1] < h));
  assert.ok(right.filter(p => p[0] === w).length === 2 && right.filter(p => p[0] < w).every(p => p[1] > 0 && p[1] < h));
  assert.throws(() => H.shape("middle", w, h), /unknown dock/);
  assert.throws(() => H.shape("bottom", 0, h), /positive/);
});
test("terminal chamfer cuts the far corners (6 vertices) and stays inside the plain trapezoid", () => {
  const plain = H.shape("bottom", 980, 100, { slantDeg: 14 });
  const cut = H.shape("bottom", 980, 100, { slantDeg: 14, chamfer: 12 });
  assert.equal(cut.points.length, 6);
  for (const [x, y] of cut.points) assert.ok(H.containsPoint(plain.points, x, y), `${x},${y}`);
  assert.equal(H.containsPoint(cut.points, plain.points[1][0] + 0.5, 0.5), false, "the chamfered corner is click-through");
  assert.equal(H.containsPoint(plain.points, plain.points[1][0] + 0.5, 0.5), true);
});
test("hit-test: inside, slanted corners, outside, boundary", () => {
  const s = H.shape("bottom", 980, 100, { slantDeg: 14 });
  assert.equal(H.containsPoint(s.points, 490, 50), true);
  assert.equal(H.containsPoint(s.points, 2, 99), true, "bottom-left base corner is inside");
  assert.equal(H.containsPoint(s.points, 2, 10), false, "top-left corner region is cut away by the slant");
  assert.equal(H.containsPoint(s.points, 978, 10), false);
  assert.equal(H.containsPoint(s.points, 490, -1), false);
  assert.equal(H.containsPoint(s.points, 490, 101), false);
  assert.equal(H.containsPoint(s.points, -1, 50), false);
  assert.equal(H.containsPoint(s.points, 490, 0), true, "boundary counts as inside");
  assert.equal(H.containsPoint(s.points, 0, 100), true);
  // exactly on the slanted side
  const [bl, tl] = s.points; const mid = [(bl[0] + tl[0]) / 2, (bl[1] + tl[1]) / 2];
  assert.equal(H.containsPoint(s.points, mid[0], mid[1]), true);
  assert.equal(H.containsPoint(s.points, mid[0] - 1, mid[1]), false);
});
test("hit-test works in screen space after translate (what main does with the window-relative rect)", () => {
  const s = H.shape("bottom", 980, 100, { slantDeg: 14 });
  const screen = H.translate(s.points, 470, 980);
  assert.equal(H.containsPoint(screen, 470 + 490, 980 + 50), true);
  assert.equal(H.containsPoint(screen, 470 + 3, 980 + 5), false);
  assert.equal(H.containsPoint(screen, 100, 100), false);
});
test("content padding keeps content off the slanted sides and corner brackets hug vertices", () => {
  const s = H.shape("bottom", 980, 100, { slantDeg: 20 });
  const pad = H.contentPadding(s);
  assert.ok(pad.left >= s.inset * 0.8 && pad.left === pad.right);
  const path = H.cornerBrackets(s.points, 14);
  assert.equal((path.match(/M/g) || []).length, 4); assert.match(path, /^M[\d. -]+L[\d. -]+L[\d. -]+/);
  assert.equal((H.cornerBrackets(s.points, 14, [1, 2]).match(/M/g) || []).length, 2);
  assert.equal(H.svgPoints([[0, 1], [2.555, 3]]), "0,1 2.56,3");
});
