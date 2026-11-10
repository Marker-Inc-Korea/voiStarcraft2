const test = require("node:test");
const assert = require("node:assert/strict");
const M = require("../sketch/route_model.js");

const PLAN = {
  kind: "route_plan", plan_id: "p1", narration_ko: "공격합니다",
  route: {
    waypoints: [{ nx: 0.2, ny: 0.8, x: 20, y: 30, label_ko: "앞마당" }, { nx: 0.5, ny: 0.5, x: 50, y: 50, label_ko: "맵 중앙" }],
    final: { action: "attack", nx: 0.9, ny: 0.1, x: 90, y: 10, label_ko: "적 3번째" },
  },
  squad: { label_ko: "해병 8기", unit_count: 8 },
};
const make = () => { let t = 0; const m = M.createRouteModel({ now: () => t, fadeMs: 1000, holdMs: 500, cursorIdleMs: 2000 }); return { m, at: v => { t = v; } }; };

test("route_plan -> polyline through the waypoints in order ending at the final point, numbered, with the final marker kind", () => {
  const { m } = make();
  m.ingest(PLAN);
  const v = m.view(0);
  assert.deepEqual(v.route.polyline, [{ nx: 0.2, ny: 0.8 }, { nx: 0.5, ny: 0.5 }, { nx: 0.9, ny: 0.1 }]);
  assert.deepEqual(v.route.waypoints.map(w => [w.number, w.label, w.reached, w.next]), [[1, "앞마당", false, true], [2, "맵 중앙", false, false]]);
  assert.deepEqual([v.route.final.marker, v.route.final.label, v.route.final.reached], ["sword", "적 3번째", false]);
  assert.equal(v.route.alpha, 1); assert.equal(v.route.phase, "active");
});
test("final marker: attack = sword, move = flag", () => {
  const { m } = make();
  m.ingest({ ...PLAN, route: { ...PLAN.route, final: { ...PLAN.route.final, action: "move" } } });
  assert.equal(m.view(0).route.final.marker, "flag");
});
test("progress text follows the contract example and only claims what route_progress reported", () => {
  const { m } = make();
  m.ingest(PLAN);
  assert.equal(m.view(0).progressText, "앞마당 (이동 중) → 맵 중앙 → 적 3번째 → 공격");
  m.ingest({ kind: "route_progress", plan_id: "p1", reached_index: 0, done: false, released: false });
  assert.equal(m.view(0).progressText, "앞마당 ✓ → 맵 중앙 (이동 중) → 적 3번째 → 공격");
  assert.deepEqual(m.view(0).route.waypoints.map(w => [w.reached, w.next]), [[true, false], [false, true]]);
  m.ingest({ kind: "route_progress", plan_id: "p1", reached_index: 1, done: false, released: false });
  assert.equal(m.view(0).progressText, "앞마당 ✓ → 맵 중앙 ✓ → 적 3번째 (이동 중) → 공격");
  m.ingest({ kind: "route_progress", plan_id: "p1", reached_index: 1, done: true, released: false });
  assert.equal(m.view(0).progressText, "앞마당 ✓ → 맵 중앙 ✓ → 적 3번째 ✓ → 공격");
  assert.equal(m.view(0).route.final.reached, true); assert.equal(m.view(0).route.phase, "done");
});
test("reached_index -1 / missing means nothing reached yet; out-of-range values are clamped, never invented", () => {
  const { m } = make();
  m.ingest(PLAN);
  m.ingest({ kind: "route_progress", plan_id: "p1", reached_index: -1, done: false, released: false });
  assert.equal(m.view(0).route.waypoints[0].reached, false);
  m.ingest({ kind: "route_progress", plan_id: "p1", done: false, released: false });
  assert.equal(m.view(0).route.waypoints[0].reached, false);
  m.ingest({ kind: "route_progress", plan_id: "p1", reached_index: 99, done: false, released: false });
  assert.deepEqual(m.view(0).route.waypoints.map(w => w.reached), [true, true]);
  assert.equal(m.view(0).route.final.reached, false, "only done:true marks the final point reached");
});
test("progress never goes backwards (a late duplicate cannot un-reach a waypoint)", () => {
  const { m } = make();
  m.ingest(PLAN);
  m.ingest({ kind: "route_progress", plan_id: "p1", reached_index: 1, done: false, released: false });
  m.ingest({ kind: "route_progress", plan_id: "p1", reached_index: 0, done: false, released: false });
  assert.deepEqual(m.view(0).route.waypoints.map(w => w.reached), [true, true]);
});
test("progress and cursor for another plan are ignored", () => {
  const { m } = make();
  m.ingest(PLAN);
  m.ingest({ kind: "route_progress", plan_id: "OTHER", reached_index: 1, done: true, released: false });
  m.ingest({ kind: "agent_cursor", plan_id: "OTHER", nx: 0.3, ny: 0.3, action: "move", label_ko: "x" });
  assert.equal(m.view(0).route.phase, "active"); assert.equal(m.view(0).cursor, null);
});
test("done: route holds, then fades out and disappears; released fades too and says it was interrupted", () => {
  const { m, at } = make();
  m.ingest(PLAN);
  at(100); m.ingest({ kind: "route_progress", plan_id: "p1", reached_index: 1, done: true, released: false });
  at(300); assert.equal(m.view(300).route.alpha, 1);
  assert.ok(Math.abs(m.view(1100).route.alpha - 0.5) < 1e-9);
  assert.equal(m.view(1700).route, null);
  const b = make(); b.m.ingest(PLAN);
  b.at(0); b.m.ingest({ kind: "route_progress", plan_id: "p1", reached_index: 0, done: false, released: true, reason: "emergency" });
  const v = b.m.view(0);
  assert.equal(v.route.phase, "released"); assert.equal(v.route.reason, "emergency");
  assert.equal(v.progressText, "앞마당 ✓ → 맵 중앙 → 적 3번째 → 공격 → 중단");
  assert.equal(b.m.view(2000).route, null);
});
test("agent cursor: normalized point, action and tooltip; fades after idle; a new event moves it", () => {
  const { m, at } = make();
  m.ingest(PLAN);
  at(10); m.ingest({ kind: "agent_cursor", plan_id: "p1", nx: 0.5, ny: 0.5, action: "move", label_ko: "맵 중앙" });
  assert.deepEqual(m.view(10).cursor, { nx: 0.5, ny: 0.5, action: "move", label: "맵 중앙", alpha: 1, seq: 1 });
  at(1500); m.ingest({ kind: "agent_cursor", plan_id: "p1", nx: 0.9, ny: 0.1, action: "attack", label_ko: "" });
  const c = m.view(1500).cursor;
  assert.deepEqual([c.nx, c.ny, c.action, c.seq], [0.9, 0.1, "attack", 2]);
  assert.equal(m.view(1500 + 2000 + 2000).cursor, null);
  const bad = make(); bad.m.ingest(PLAN);
  bad.m.ingest({ kind: "agent_cursor", plan_id: "p1", nx: 2, ny: 0.1, action: "move", label_ko: "" });
  bad.m.ingest({ kind: "agent_cursor", plan_id: "p1", nx: 0.1, ny: 0.1, action: "teleport", label_ko: "" });
  assert.equal(bad.m.view(0).cursor, null, "out-of-frame points and unknown actions are dropped");
});
test("a route_plan with an invalid coordinate is ignored as a whole (nothing is drawn that is not on the frame)", () => {
  const { m } = make();
  m.ingest({ ...PLAN, route: { ...PLAN.route, waypoints: [{ nx: -0.2, ny: 0.8, label_ko: "x" }] } });
  assert.equal(m.view(0).route, null);
  m.ingest({ ...PLAN, route: { waypoints: [], final: { action: "dance", nx: 0.5, ny: 0.5 } } });
  assert.equal(m.view(0).route, null);
  m.ingest({ ...PLAN, route: null });
  assert.equal(m.view(0).route, null);
});
test("a route with only the final point is valid (no waypoints)", () => {
  const { m } = make();
  m.ingest({ ...PLAN, route: { waypoints: [], final: PLAN.route.final } });
  assert.equal(m.view(0).progressText, "적 3번째 (이동 중) → 공격");
  assert.deepEqual(m.view(0).route.polyline, [{ nx: 0.9, ny: 0.1 }]);
});
test("missing labels get ordinal fallbacks", () => {
  const { m } = make();
  m.ingest({ ...PLAN, route: { waypoints: [{ nx: 0.2, ny: 0.8, label_ko: "" }], final: { action: "move", nx: 0.5, ny: 0.5 } } });
  assert.equal(m.view(0).progressText, "경유 1 (이동 중) → 목표 → 이동");
});
test("drawn strokes stay visible, faded, until a route_plan arrives; then the plan replaces them", () => {
  const { m } = make();
  m.setSketch("p1", STROKES());
  let v = m.view(0);
  assert.equal(v.strokes.alpha, M.SUBMITTED_ALPHA); assert.equal(v.strokes.strokes.length, 1);
  m.ingest(PLAN);
  assert.equal(m.view(0).strokes, null); assert.ok(m.view(0).route);
});
test("strokes are NOT replaced by a plan that is for a different (stale) sketch", () => {
  const { m } = make();
  m.setSketch("sk-new", STROKES());
  m.ingest({ ...PLAN, plan_id: "sk-old" , stale: true });
  assert.ok(m.view(0).route, "plan ids are assigned by the backend; any later plan supersedes");
});
test("an ask/refuse/none answer keeps the strokes faded, then clears them after the linger time", () => {
  const { m, at } = make();
  at(0); m.setSketch("p1", STROKES()); m.settleSketch("ask");
  assert.ok(m.view(1000).strokes);
  assert.equal(m.view(M.ASK_LINGER_MS + 1).strokes, null);
  const b = make(); b.m.setSketch("p2", STROKES()); b.m.settleSketch("none");
  assert.equal(b.m.view(M.NONE_LINGER_MS + 1).strokes, null);
  assert.ok(M.NONE_LINGER_MS < M.ASK_LINGER_MS);
});
test("events replayed with the same route_seq are applied once (poll overlap)", () => {
  const { m } = make();
  m.ingest({ ...PLAN, route_seq: 5 });
  m.ingest({ kind: "route_progress", plan_id: "p1", reached_index: 1, done: false, released: false, route_seq: 6 });
  m.ingest({ kind: "route_progress", plan_id: "p1", reached_index: 0, done: false, released: false, route_seq: 6 });
  assert.equal(m.view(0).route.waypoints[1].reached, true);
  m.ingest({ ...PLAN, route_seq: 5, plan_id: "zzz" });
  assert.equal(m.view(0).route.planId, "p1");
});
test("isActive: a plan that is neither done nor released nor faded counts as an open route", () => {
  const { m, at } = make();
  assert.equal(m.isActive(), false);
  m.ingest(PLAN); assert.equal(m.isActive(), true);
  at(0); m.ingest({ kind: "route_progress", plan_id: "p1", reached_index: 1, done: true, released: false });
  assert.equal(m.isActive(), false);
});
function STROKES() { return [[[0.1, 0.9, 0], [0.5, 0.5, 100], [0.9, 0.1, 200]]]; }

test("execute answer keeps the strokes faded for a long linger while we wait for the route_plan event", () => {
  const { m } = make();
  m.setSketch("p1", STROKES()); m.settleSketch("execute");
  assert.ok(m.view(M.NONE_LINGER_MS + 1).strokes);
  assert.equal(m.view(M.EXECUTE_LINGER_MS + 1).strokes, null);
  assert.ok(M.EXECUTE_LINGER_MS > M.ASK_LINGER_MS);
});
test("operation card: only backend-reported facts move the stages; the effect needs done:true", () => {
  const { m } = make();
  assert.equal(m.card(), null);
  m.ingest(PLAN);
  let c = m.card();
  assert.equal(c.id, "route-p1"); assert.equal(c.tier, "operation"); assert.equal(c.name, "앞마당 (이동 중) → 맵 중앙 → 적 3번째 → 공격");
  assert.deepEqual(c.stages, ["done", "done", "active", "todo", "todo"]); assert.equal(c.open, true);
  m.ingest({ kind: "agent_cursor", plan_id: "p1", nx: 0.2, ny: 0.8, action: "select", label_ko: "해병" });
  assert.deepEqual(m.card().stages, ["done", "done", "done", "active", "todo"]);
  m.ingest({ kind: "agent_cursor", plan_id: "p1", nx: 0.2, ny: 0.8, action: "move", label_ko: "" });
  assert.deepEqual(m.card().stages, ["done", "done", "done", "done", "active"]);
  m.ingest({ kind: "route_progress", plan_id: "p1", reached_index: 1, done: false, released: false });
  assert.equal(m.card().stages[4], "active", "a reached waypoint is not the effect of the whole route");
  m.ingest({ kind: "route_progress", plan_id: "p1", reached_index: 1, done: true, released: false });
  c = m.card(); assert.deepEqual(c.stages, ["done", "done", "done", "done", "done"]); assert.equal(c.open, false);
});
test("operation card in a simulated runtime shows 시뮬 stages, never a real order/effect", () => {
  const { m } = make();
  m.ingest(PLAN);
  m.ingest({ kind: "agent_cursor", plan_id: "p1", nx: 0.2, ny: 0.8, action: "move", label_ko: "" });
  m.ingest({ kind: "route_progress", plan_id: "p1", reached_index: 1, done: true, released: false });
  assert.deepEqual(m.card({ sim: true }).stages, ["done", "done", "done", "sim", "sim"]);
  assert.equal(m.card({ sim: true }).sim, true);
});
test("operation card: released blocks the first open stage with the reason", () => {
  const { m } = make();
  m.ingest(PLAN);
  m.ingest({ kind: "agent_cursor", plan_id: "p1", nx: 0.2, ny: 0.8, action: "select", label_ko: "" });
  m.ingest({ kind: "route_progress", plan_id: "p1", reached_index: 0, done: false, released: true, reason: "emergency" });
  const c = m.card();
  assert.deepEqual(c.stages, ["done", "done", "done", "blocked", "todo"]); assert.equal(c.blockedReason, "emergency"); assert.equal(c.open, false);
});
test("operation card name is capped at 80 characters", () => {
  const { m } = make();
  m.ingest({ ...PLAN, route: { waypoints: Array.from({ length: 6 }, (_, i) => ({ nx: 0.1 * i, ny: 0.5, label_ko: "아주 긴 지점 이름" + i })), final: PLAN.route.final } });
  assert.ok(m.card().name.length <= 80);
});
