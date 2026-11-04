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
test("unknown mode is rejected", () => assert.throws(() => layoutFor("diagonal", D1.bounds), /dock mode/));

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

test("side layouts honour the work area (menu bar) vertically, bottom layout is unaffected", () => {
  const wa = { x: 0, y: 38, width: 1512, height: 944 };
  const r = layoutFor("right", D1.bounds, { workArea: wa });
  assert.equal(r.window.y, 38); assert.equal(r.window.height, 944);
  assert.equal(r.trigger.height, 944); // trigger follows the window span
  assert.deepEqual(layoutFor("bottom", D1.bounds, { workArea: wa }).window, layoutFor("bottom", D1.bounds).window);
});
test("left and top docks: geometry and edge triggers", () => {
  const left = layoutFor("left", D1.bounds);
  assert.equal(left.window.x, 0); assert.equal(left.window.width, 390); assert.deepEqual(left.trigger, { x: 0, y: 0, width: 2, height: 982 });
  const top = layoutFor("top", D1.bounds);
  assert.equal(top.window.y, 0); assert.equal(top.window.width, 980); assert.deepEqual(top.trigger, { x: top.window.x, y: 0, width: 980, height: 2 });
  const c = createDockController();
  const topEdge = { x: 700, y: 0 };
  c.update({ ...base, mode: "top", cursor: topEdge, now: 0 });
  assert.equal(c.update({ ...base, mode: "top", cursor: topEdge, now: 200 }).revealed, true);
  const c2 = createDockController();
  c2.update({ ...base, mode: "left", cursor: { x: 0, y: 400 }, now: 0 });
  assert.equal(c2.update({ ...base, mode: "left", cursor: { x: 0, y: 400 }, now: 200 }).revealed, true);
});
test("layout scale, zone size and margin settings feed the geometry", () => {
  const half = layoutFor("bottom", D1.bounds, { widthScale: 50, heightScale: 50 });
  assert.equal(half.window.width, 490); assert.equal(half.window.height, 320);
  assert.equal(layoutFor("bottom", D1.bounds, { edgePx: 8 }).trigger.height, 8);
  const side = layoutFor("right", D1.bounds, { heightScale: 50 });
  assert.equal(side.window.height, 491); assert.equal(side.window.y, Math.floor((982 - 491) / 2));
});
test("settings override hide delay, zone, autoHide and follow-cursor per update", () => {
  const c = createDockController();
  reveal(c);
  const panel = layoutFor("bottom", D1.bounds).window;
  const far = { x: 700, y: 10 };
  const s = { hideDelayMs: 1500 };
  c.update(at(far, 5000, { panelRect: panel, settings: s }));
  assert.equal(c.update(at(far, 6400, { panelRect: panel, settings: s })).revealed, true);
  assert.equal(c.update(at(far, 6550, { panelRect: panel, settings: s })).revealed, false);
  // autoHide:false behaves like a pin
  const c2 = createDockController();
  assert.equal(c2.update(at({ x: 5, y: 5 }, 0, { settings: { autoHide: false } })).revealed, true);
  assert.equal(c2.update(at({ x: 5, y: 5 }, 90000, { settings: { autoHide: false } })).revealed, true);
  // a 6px zone is hit 5px above the edge, a 2px zone is not
  const c3 = createDockController();
  const p = { x: 700, y: 977 };
  c3.update(at(p, 0, { settings: { zonePx: 6 } }));
  assert.equal(c3.update(at(p, 200, { settings: { zonePx: 6 } })).revealed, true);
  const c4 = createDockController();
  c4.update(at(p, 0)); assert.equal(c4.update(at(p, 200)).revealed, false);
  // followCursor:false pins the dock to its display
  const c5 = createDockController();
  assert.equal(c5.update({ ...base, displays: [D1, D2], cursor: { x: 2000, y: 500 }, now: 0, settings: { followCursor: false } }).moveToDisplayId, null);
});
