const test = require("node:test");
const assert = require("node:assert/strict");
const { createFakeBackend } = require("../fixtures/fake_backend.js");

test("fake backend serves the route contract shapes: POST /api/route/sketch, GET /api/route/events, scripted events via /__ctl/route", async () => {
  const backend = createFakeBackend({ port: 0 });
  const port = await backend.listen();
  const base = `http://127.0.0.1:${port}`;
  const post = (p, body) => fetch(base + p, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) }).then(async r => ({ status: r.status, body: await r.json() }));
  const strokes = [[[0.1, 0.9, 0], [0.9, 0.1, 500]]];
  const res = await post("/api/route/sketch", { update_id: "sk-1", strokes, image_png_b64: null, transcript: "가", minimap_rect: { x: 1, y: 2, w: 3, h: 4 } });
  assert.equal(res.status, 202);
  assert.equal(res.body.kind, "execute"); assert.ok(res.body.plan_id); assert.equal(res.body.route.final.nx, 0.9); assert.equal(res.body.route.final.ny, 0.1);
  assert.equal(res.body.route.waypoints.length, 0);
  assert.deepEqual(backend.state.sketches[0].strokes, strokes);
  const bad = await post("/api/route/sketch", { update_id: "x", strokes: [] });
  assert.equal(bad.status, 400);
  await post("/__ctl/route", { event: { kind: "route_plan", plan_id: res.body.plan_id, route: res.body.route, narration_ko: "x" } });
  await post("/__ctl/route", { event: { kind: "agent_cursor", plan_id: res.body.plan_id, nx: 0.5, ny: 0.5, action: "move", label_ko: "" } });
  const page = await (await fetch(`${base}/api/route/events?after=0`)).json();
  assert.deepEqual(page.events.map(e => [e.route_seq, e.kind]), [[1, "route_plan"], [2, "agent_cursor"]]); assert.equal(page.latest, 2);
  assert.deepEqual((await (await fetch(`${base}/api/route/events?after=1`)).json()).events.map(e => e.route_seq), [2]);
  await backend.close();
});
