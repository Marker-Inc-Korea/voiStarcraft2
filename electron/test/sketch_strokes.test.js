const test = require("node:test");
const assert = require("node:assert/strict");
const S = require("../sketch/strokes.js");

test("a drag records minimap-frame-normalized points with ms timestamps relative to the first point", () => {
  const r = S.createRecorder();
  assert.equal(r.begin(0.1, 0.9, 1000), true);
  r.move(0.3, 0.7, 1100); r.move(0.5, 0.5, 1250);
  const res = r.end(0.8, 0.2, 1400);
  assert.equal(res.kind, "stroke");
  assert.deepEqual(r.strokes(), [[[0.1, 0.9, 0], [0.3, 0.7, 100], [0.5, 0.5, 250], [0.8, 0.2, 400]]]);
  assert.equal(r.open(), false);
});
test("timestamps of a second stroke continue the same clock (relative to the sketch start)", () => {
  const r = S.createRecorder();
  r.begin(0.1, 0.1, 5000); r.end(0.5, 0.5, 5200);
  r.begin(0.6, 0.6, 5600); r.end(0.9, 0.9, 5800);
  assert.deepEqual(r.strokes().map(s => [s[0][2], s[s.length - 1][2]]), [[0, 200], [600, 800]]);
});
test("points are clamped into 0..1 and non-finite points are dropped (the VLM can only ever see coordinates on the frame)", () => {
  const r = S.createRecorder();
  r.begin(0.5, 0.5, 0);
  r.move(1.4, -0.2, 50); r.move(NaN, 0.5, 60); r.move(0.5, Infinity, 70);
  r.end(0.9, 0.9, 100);
  const pts = r.strokes()[0];
  assert.deepEqual(pts.map(p => [p[0], p[1]]), [[0.5, 0.5], [1, 0], [0.9, 0.9]]);
});
test("a press that starts outside the frame does not begin a stroke", () => {
  const r = S.createRecorder();
  assert.equal(r.begin(1.2, 0.5, 0), false);
  assert.equal(r.begin(0.5, -0.1, 0), false);
  assert.equal(r.open(), false); assert.deepEqual(r.strokes(), []);
});
test("a click without a drag is a tap (the sketch is cancelled by the caller), not a stroke", () => {
  const r = S.createRecorder();
  r.begin(0.5, 0.5, 0);
  const res = r.end(0.502, 0.501, 80);
  assert.equal(res.kind, "tap"); assert.deepEqual(r.strokes(), []);
});
test("jitter below the minimum step is not recorded; the final point is always kept", () => {
  const r = S.createRecorder({ minStep: 0.01 });
  r.begin(0.2, 0.2, 0);
  for (let i = 1; i <= 20; i++) r.move(0.2 + i * 0.0001, 0.2, i * 5);
  r.move(0.6, 0.2, 200);
  r.end(0.6005, 0.2, 210);
  const pts = r.strokes()[0];
  assert.ok(pts.length <= 4, `got ${pts.length}`);
  assert.deepEqual(pts[pts.length - 1].slice(0, 2), [0.6005, 0.2]);
});
test("limits: at most maxStrokes strokes and maxPoints points per stroke (last point still preserved)", () => {
  const r = S.createRecorder({ maxStrokes: 2, maxPoints: 10, minStep: 0 });
  for (let k = 0; k < 2; k++) { r.begin(0.1, 0.1, k * 1000); for (let i = 1; i < 50; i++) r.move(0.1 + i * 0.01, 0.1, k * 1000 + i); r.end(0.95, 0.95, k * 1000 + 60); }
  assert.equal(r.begin(0.3, 0.3, 5000), false, "third stroke refused");
  assert.equal(r.strokes().length, 2);
  for (const s of r.strokes()) { assert.ok(s.length <= 10); assert.deepEqual(s[s.length - 1].slice(0, 2), [0.95, 0.95]); }
});
test("cancel drops everything including an open stroke; open() reports an in-progress stroke and finish() closes it as a stroke", () => {
  const r = S.createRecorder();
  r.begin(0.1, 0.1, 0); r.move(0.5, 0.5, 10);
  assert.equal(r.open(), true);
  const res = r.finish(30);
  assert.equal(res.kind, "stroke"); assert.equal(r.strokes().length, 1);
  r.begin(0.2, 0.2, 100); r.cancel();
  assert.deepEqual(r.strokes(), []); assert.equal(r.open(), false);
});
test("finish() on a stroke that never moved drops it (a tap) and says so", () => {
  const r = S.createRecorder();
  r.begin(0.4, 0.4, 0);
  assert.equal(r.finish(10).kind, "tap");
  assert.deepEqual(r.strokes(), []);
});
test("corners: an L-shaped stroke has a corner at the bend; a straight one has none; gentle curves have none", () => {
  const L = [[0.1, 0.1, 0], [0.5, 0.1, 100], [0.9, 0.1, 200], [0.9, 0.5, 300], [0.9, 0.9, 400]];
  const c = S.corners(L);
  assert.equal(c.length, 1); assert.deepEqual([c[0].nx, c[0].ny], [0.9, 0.1]);
  assert.deepEqual(S.corners([[0.1, 0.1, 0], [0.5, 0.5, 1], [0.9, 0.9, 2]]), []);
  const arc = Array.from({ length: 40 }, (_, i) => [0.5 + 0.4 * Math.cos(i * 0.05), 0.5 + 0.4 * Math.sin(i * 0.05), i]);
  assert.deepEqual(S.corners(arc), []);
});
test("arrow tail direction: unit vector of the last segment that is long enough", () => {
  const d = S.endDirection([[0.1, 0.1, 0], [0.5, 0.1, 10], [0.5002, 0.1, 20]]);
  assert.ok(Math.abs(d.dx - 1) < 1e-6 && Math.abs(d.dy) < 1e-6);
  assert.equal(S.endDirection([[0.5, 0.5, 0]]), null);
});
