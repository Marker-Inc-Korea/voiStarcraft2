const test = require("node:test");
const assert = require("node:assert/strict");
const F = require("../sketch/feed.js");

test("route feed polls /api/route/events with the last route_seq and delivers each event once, in order", async () => {
  const calls = [];
  const pages = [
    { events: [{ route_seq: 1, kind: "route_plan", plan_id: "p" }, { route_seq: 2, kind: "agent_cursor", plan_id: "p" }], latest: 2 },
    { events: [{ route_seq: 2, kind: "agent_cursor", plan_id: "p" }, { route_seq: 3, kind: "route_progress", plan_id: "p" }], latest: 3 },
    { events: [], latest: 3 },
  ];
  const got = [];
  const feed = F.createRouteFeed({ requestJson: async p => { calls.push(p); return pages.shift(); }, onEvents: e => got.push(...e) });
  await feed.poll(); await feed.poll(); await feed.poll();
  assert.deepEqual(calls, ["/api/route/events?after=0", "/api/route/events?after=2", "/api/route/events?after=3"]);
  assert.deepEqual(got.map(e => e.route_seq), [1, 2, 3]);
});
test("unknown event kinds and malformed rows are dropped; failures do not throw and do not advance the cursor", async () => {
  const got = [];
  let fail = true;
  const feed = F.createRouteFeed({ requestJson: async () => { if (fail) throw new Error("down"); return { events: [{ route_seq: 1, kind: "other" }, { kind: "route_plan" }, null, { route_seq: 2, kind: "route_plan", plan_id: "p" }], latest: 2 }; }, onEvents: e => got.push(...e) });
  assert.equal(await feed.poll(), false);
  fail = false;
  assert.equal(await feed.poll(), true);
  assert.deepEqual(got.map(e => e.route_seq), [2]);
});
test("a server restart (latest goes backwards) resets the cursor instead of going silent", async () => {
  const got = [];
  const pages = [{ events: [{ route_seq: 7, kind: "route_plan", plan_id: "a" }], latest: 7 }, { events: [], latest: 2 }, { events: [{ route_seq: 1, kind: "route_plan", plan_id: "b" }], latest: 1 }];
  const calls = [];
  const feed = F.createRouteFeed({ requestJson: async p => { calls.push(p); return pages.shift(); }, onEvents: e => got.push(...e) });
  await feed.poll(); await feed.poll(); await feed.poll();
  assert.equal(calls[2], "/api/route/events?after=0");
  assert.deepEqual(got.map(e => e.plan_id), ["a", "b"]);
});
test("a 404 (backend without route support) reports unsupported once and backs off", async () => {
  const feed = F.createRouteFeed({ requestJson: async () => { const e = new Error("Commander HTTP 404"); throw e; }, onEvents: () => {} });
  await feed.poll(); await feed.poll();
  assert.equal(feed.failures(), 2);
  assert.ok(feed.nextDelay({ active: false }) > feed.nextDelay({ active: true }));
});
