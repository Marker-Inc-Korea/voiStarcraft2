const test = require("node:test");
const assert = require("node:assert/strict");
const V = require("../sketch/window/view.js");
const M = require("../sketch/route_model.js");

const LAYOUT = { ox: 36, oy: 36, w: 200, h: 200, width: 272, height: 272 };
const find = (node, pred, out = []) => { if (pred(node)) out.push(node); for (const c of node.children || []) find(c, pred, out); return out; };
const cls = (node, name) => (node.attrs.class || "").split(" ").includes(name);

function planModel(extra = []) {
  const m = M.createRouteModel({ now: () => 0 });
  m.ingest({ kind: "route_plan", plan_id: "p", route: { waypoints: [{ nx: 0.2, ny: 0.8, label_ko: "앞마당" }, { nx: 0.5, ny: 0.5, label_ko: "맵 중앙" }], final: { action: "attack", nx: 0.9, ny: 0.1, label_ko: "적 3번째" } } });
  for (const e of extra) m.ingest(e);
  return m;
}

test("points are placed in window pixels: offset + normalized * minimap size", () => {
  assert.deepEqual(V.toPx(LAYOUT, 0.5, 0.25), { x: 136, y: 86 });
});
test("route scene: dashed polyline through all points, numbered waypoint discs, a sword at the final point for attack", () => {
  const scene = V.buildScene({ view: planModel().view(0), layout: LAYOUT });
  const line = find(scene, n => cls(n, "route-line"))[0];
  assert.equal(line.attrs.points, "76,198 136,136 216,58");
  const nums = find(scene, n => cls(n, "wp-num")).map(n => n.text);
  assert.deepEqual(nums, ["1", "2"]);
  const fin = find(scene, n => cls(n, "final"))[0];
  assert.equal(fin.attrs["data-action"], "attack"); assert.equal(fin.attrs["data-icon"], "sword");
  assert.ok(find(fin, n => cls(n, "final-icon")).length >= 1);
  assert.deepEqual(find(scene, n => cls(n, "final-label")).map(n => n.text), ["적 3번째"]);
  assert.deepEqual(find(scene, n => cls(n, "wp-label")).map(n => n.text), ["앞마당", "맵 중앙"]);
});
test("a move route ends in a flag", () => {
  const m = M.createRouteModel({ now: () => 0 });
  m.ingest({ kind: "route_plan", plan_id: "p", route: { waypoints: [], final: { action: "move", nx: 0.5, ny: 0.5, label_ko: "중앙" } } });
  const fin = find(V.buildScene({ view: m.view(0), layout: LAYOUT }), n => cls(n, "final"))[0];
  assert.equal(fin.attrs["data-icon"], "flag");
});
test("reached waypoints are marked from progress; the done segment is drawn solid up to the last reached point", () => {
  const m = planModel([{ kind: "route_progress", plan_id: "p", reached_index: 0, done: false, released: false }]);
  const scene = V.buildScene({ view: m.view(0), layout: LAYOUT });
  const wps = find(scene, n => cls(n, "wp"));
  assert.deepEqual(wps.map(n => [n.attrs["data-reached"], n.attrs["data-next"]]), [["true", "false"], ["false", "true"]]);
  const done = find(scene, n => cls(n, "route-line-done"))[0];
  assert.equal(done.attrs.points, "76,198");
  const m2 = planModel([{ kind: "route_progress", plan_id: "p", reached_index: 1, done: false, released: false }]);
  assert.equal(find(V.buildScene({ view: m2.view(0), layout: LAYOUT }), n => cls(n, "route-line-done"))[0].attrs.points, "76,198 136,136");
});
test("fading route: the whole route layer carries the model alpha", () => {
  let t = 0;
  const m = M.createRouteModel({ now: () => t, fadeMs: 1000, holdMs: 0 });
  m.ingest({ kind: "route_plan", plan_id: "p", route: { waypoints: [], final: { action: "move", nx: 0.5, ny: 0.5, label_ko: "x" } } });
  m.ingest({ kind: "route_progress", plan_id: "p", reached_index: -1, done: true, released: false });
  const layer = find(V.buildScene({ view: m.view(500), layout: LAYOUT }), n => cls(n, "layer-route"))[0];
  assert.equal(layer.attrs.opacity, "0.5");
});
test("drawn strokes: underlay + bright line, an arrow head at the end pointing along the last segment, dots at corners only", () => {
  const strokes = [[[0.1, 0.1, 0], [0.9, 0.1, 100], [0.9, 0.9, 200]]];
  const scene = V.buildScene({ view: { route: null, cursor: null, strokes: { strokes, alpha: 1 } }, layout: LAYOUT });
  const line = find(scene, n => cls(n, "stroke"))[0];
  assert.equal(line.attrs.points, "56,56 216,56 216,216");
  assert.equal(find(scene, n => cls(n, "stroke-under")).length, 1);
  const arrow = find(scene, n => cls(n, "arrow"))[0];
  assert.ok(arrow, "arrow head present"); assert.equal(arrow.tag, "polygon");
  const tip = arrow.attrs.points.split(" ").map(p => p.split(",").map(Number));
  assert.ok(tip.some(([x, y]) => x === 216 && y > 216 - 1), "tip is at/after the last point, pointing down");
  const dots = find(scene, n => cls(n, "corner-dot"));
  assert.equal(dots.length, 1); assert.deepEqual([dots[0].attrs.cx, dots[0].attrs.cy], [216, 56]);
});
test("faded strokes (submitted) carry the alpha; a live in-progress stroke is bright", () => {
  const scene = V.buildScene({ view: { route: null, cursor: null, strokes: { strokes: [[[0.1, 0.1, 0], [0.5, 0.5, 9]]], alpha: 0.45 } }, layout: LAYOUT });
  assert.equal(find(scene, n => cls(n, "layer-strokes"))[0].attrs.opacity, "0.45");
  const live = V.buildScene({ view: { route: null, cursor: null, strokes: null }, layout: LAYOUT, live: [[0.1, 0.1, 0], [0.4, 0.4, 5]] });
  assert.equal(find(live, n => cls(n, "layer-strokes"))[0].attrs.opacity, "1");
});
test("ghost cursor placement is a pure pixel transform; label and action are passed through", () => {
  const p = V.cursorPlacement({ nx: 0.5, ny: 0.5, action: "attack", label: "적 본진", alpha: 0.8, seq: 3 }, LAYOUT);
  assert.deepEqual(p, { x: 136, y: 136, action: "attack", label: "적 본진", alpha: 0.8, seq: 3 });
  assert.equal(V.cursorPlacement(null, LAYOUT), null);
});
test("labels near the right/bottom edge flip to the other side so they stay inside the window", () => {
  const near = V.labelAnchor(LAYOUT, 0.97, 0.5, 40);
  assert.equal(near.anchor, "end");
  const left = V.labelAnchor(LAYOUT, 0.1, 0.5, 40);
  assert.equal(left.anchor, "start");
});
