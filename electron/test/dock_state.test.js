const test = require("node:test");
const assert = require("node:assert/strict");
const { layoutFor, createDockController, edgeIsShared } = require("../dock_state.js");

const D1 = { id: 1, bounds: { x: 0, y: 0, width: 1512, height: 982 } };
const D2 = { id: 2, bounds: { x: 1512, y: 0, width: 1920, height: 1080 } }; // to the right of D1
const D3 = { id: 3, bounds: { x: 0, y: 982, width: 1512, height: 800 } };   // below D1
const base = { displays: [D1], dockDisplayId: 1, mode: "bottom", forceOpen: false, pinned: false };
const at = (cursor, now, extra) => ({ ...base, cursor, now, ...extra });

test("bottom layout is ~min(980, 90%) wide, centered, flush with the display bottom", () => {
  const { window, trigger, handle } = layoutFor("bottom", D1.bounds);
  assert.equal(window.width, 980);
  assert.equal(window.x, Math.floor((1512 - 980) / 2));
  assert.equal(window.y + window.height, 982);
  assert.deepEqual(trigger, { x: window.x, y: 980, width: 980, height: 2 });
  assert.equal(handle.height, 4);
  const narrow = layoutFor("bottom", { x: 0, y: 0, width: 800, height: 600 });
  assert.equal(narrow.window.width, 720); // 90 %
});
test("right layout spans the full display height at the right edge, ~380px (+margin)", () => {
  const { window, trigger } = layoutFor("right", D2.bounds);
  assert.equal(window.height, 1080);
  assert.equal(window.x + window.width, 1512 + 1920);
  assert.equal(window.width, 390);
  assert.deepEqual(trigger, { x: 1512 + 1920 - 2, y: 0, width: 2, height: 1080 });
});
test("unknown mode is rejected", () => assert.throws(() => layoutFor("top", D1.bounds), /dock mode/));

test("edge zone hit reveals after the dwell, not instantly", () => {
  const c = createDockController();
  const inZone = { x: 700, y: 981 };
  let r = c.update(at(inZone, 1000));
  assert.equal(r.revealed, false);
  r = c.update(at(inZone, 1060));
  assert.equal(r.revealed, false);
  r = c.update(at(inZone, 1130));
  assert.equal(r.revealed, true);
  assert.equal(r.changed, true);
  assert.equal(r.reason, "edge");
});
test("zone is limited to the bar's horizontal span and the last 2px", () => {
  const c = createDockController();
  for (const point of [{ x: 100, y: 981 }, { x: 1400, y: 981 }, { x: 700, y: 979 }]) {
    c.update(at(point, 0)); assert.equal(c.update(at(point, 500)).revealed, false, JSON.stringify(point));
  }
});
test("leaving the zone before the dwell resets it", () => {
  const c = createDockController();
  c.update(at({ x: 700, y: 981 }, 0));
  c.update(at({ x: 700, y: 900 }, 80));
  c.update(at({ x: 700, y: 981 }, 100));
  assert.equal(c.update(at({ x: 700, y: 981 }, 180)).revealed, false);
  assert.equal(c.update(at({ x: 700, y: 981 }, 230)).revealed, true);
});
function reveal(c, extra) { const p = { x: 700, y: 981 }; c.update(at(p, 0, extra)); return c.update(at(p, 200, extra)); }
test("stays revealed inside the panel rect + 12px slop, hides only after 600ms outside", () => {
  const c = createDockController();
  assert.equal(reveal(c).revealed, true);
  const panel = layoutFor("bottom", D1.bounds).window; // top edge y=...
  const justAbove = { x: 700, y: panel.y - 10 }; // inside the slop
  for (let t = 300; t < 5000; t += 50) assert.equal(c.update(at(justAbove, t, { panelRect: panel })).revealed, true);
  const outside = { x: 700, y: panel.y - 40 };
  let r = c.update(at(outside, 6000, { panelRect: panel })); assert.equal(r.revealed, true);
  r = c.update(at(outside, 6590, { panelRect: panel })); assert.equal(r.revealed, true);
  r = c.update(at(outside, 6650, { panelRect: panel })); assert.equal(r.revealed, false);
  assert.equal(r.changed, true); assert.equal(r.reason, "idle");
});
test("returning inside before the debounce elapses cancels the hide", () => {
  const c = createDockController();
  reveal(c);
  const panel = layoutFor("bottom", D1.bounds).window;
  c.update(at({ x: 700, y: 10 }, 5000, { panelRect: panel }));
  c.update(at({ x: 700, y: 10 }, 5500, { panelRect: panel }));
  c.update(at({ x: 700, y: panel.y + 30 }, 5550, { panelRect: panel }));
  assert.equal(c.update(at({ x: 700, y: 10 }, 5600, { panelRect: panel })).revealed, true);
  assert.equal(c.update(at({ x: 700, y: 10 }, 6150, { panelRect: panel })).revealed, true);
  assert.equal(c.update(at({ x: 700, y: 10 }, 6250, { panelRect: panel })).revealed, false);
});
test("voice/attention override reveals without the cursor and holds 2.5s after the last force", () => {
  const c = createDockController();
  let r = c.update(at({ x: 300, y: 300 }, 1000, { forceOpen: true }));
  assert.equal(r.revealed, true); assert.equal(r.reason, "attention");
  const far = { x: 300, y: 300 };
  assert.equal(c.update(at(far, 2000, { forceOpen: true })).revealed, true);
  assert.equal(c.update(at(far, 3000, { forceOpen: false })).revealed, true); // 1s after last force
  assert.equal(c.update(at(far, 4400, { forceOpen: false })).revealed, true); // 2.4s
  assert.equal(c.update(at(far, 4500, { forceOpen: false })).revealed, false); // >2.5s and >600ms outside
});
test("pin keeps it open regardless of the cursor and releases normally", () => {
  const c = createDockController();
  let r = c.update(at({ x: 10, y: 10 }, 0, { pinned: true }));
  assert.equal(r.revealed, true); assert.equal(r.reason, "pinned");
  assert.equal(c.update(at({ x: 10, y: 10 }, 60000, { pinned: true })).revealed, true);
  assert.equal(c.update(at({ x: 10, y: 10 }, 60100, { pinned: false })).revealed, true);  // debounce starts
  assert.equal(c.update(at({ x: 10, y: 10 }, 60800, { pinned: false })).revealed, false);
});
test("multi-display: collapsed dock follows the cursor's display; shared edges do not trigger", () => {
  const c = createDockController();
  const displays = [D1, D2, D3];
  let r = c.update({ ...base, displays, cursor: { x: 2000, y: 500 }, now: 0 });
  assert.equal(r.moveToDisplayId, 2);
  // D1's bottom edge borders D3 below: crossing it is not a reveal request.
  const edge = { x: 700, y: 981 };
  assert.equal(edgeIsShared("bottom", D1.bounds, edge, displays), true);
  c.update({ ...base, displays, cursor: edge, now: 100 });
  assert.equal(c.update({ ...base, displays, cursor: edge, now: 500 }).revealed, false);
  // D2's bottom edge is the outer border: reveals.
  const e2 = { x: 2400, y: 1079 };
  const dockOn2 = { ...base, displays, dockDisplayId: 2 };
  c.update({ ...dockOn2, cursor: e2, now: 1000 });
  assert.equal(c.update({ ...dockOn2, cursor: e2, now: 1200 }).revealed, true);
});
test("a cursor on another display never triggers the dock display's edge", () => {
  const c = createDockController();
  const displays = [D1, D2];
  const dockOn2 = { ...base, displays, dockDisplayId: 2 };
  c.update({ ...dockOn2, cursor: { x: 700, y: 981 }, now: 0 });
  const r = c.update({ ...dockOn2, cursor: { x: 700, y: 981 }, now: 500 });
  assert.equal(r.revealed, false); assert.equal(r.moveToDisplayId, 1);
});
test("mode switch: right mode uses the last 2px column and recomputes the window", () => {
  const c = createDockController();
  const right = { ...base, mode: "right" };
  const bottomEdge = { x: 700, y: 981 };
  c.update({ ...right, cursor: bottomEdge, now: 0 });
  assert.equal(c.update({ ...right, cursor: bottomEdge, now: 500 }).revealed, false);
  const col = { x: 1511, y: 400 };
  c.update({ ...right, cursor: col, now: 1000 });
  const r = c.update({ ...right, cursor: col, now: 1200 });
  assert.equal(r.revealed, true);
  assert.equal(r.layout.window.width, 390);
  assert.equal(r.layout.window.height, 982);
  // inside the sidebar slop it stays open; far left hides after the debounce
  const panel = r.layout.window;
  assert.equal(c.update({ ...right, cursor: { x: panel.x - 8, y: 400 }, now: 1300, panelRect: panel }).revealed, true);
  c.update({ ...right, cursor: { x: 100, y: 400 }, now: 6000, panelRect: panel });
  assert.equal(c.update({ ...right, cursor: { x: 100, y: 400 }, now: 6700, panelRect: panel }).revealed, false);
});

test("right layout honours the work area (menu bar) vertically, bottom layout is unaffected", () => {
  const wa = { x: 0, y: 38, width: 1512, height: 944 };
  const r = layoutFor("right", D1.bounds, { workArea: wa });
  assert.equal(r.window.y, 38); assert.equal(r.window.height, 944);
  assert.equal(r.trigger.y, 0); assert.equal(r.trigger.height, 982); // the trigger still spans the full edge
  assert.deepEqual(layoutFor("bottom", D1.bounds, { workArea: wa }).window, layoutFor("bottom", D1.bounds).window);
});
