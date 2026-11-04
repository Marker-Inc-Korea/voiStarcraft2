const test = require("node:test");
const assert = require("node:assert/strict");
const D = require("../displays.js");
const { createDockController, layoutFor } = require("../dock_state.js");

// The user's real machine (read from Electron on it): built-in Retina 2x primary at the origin, a 1x portrait S27R35x
// (rotation 270) up-right of it, and a 1x ultra-wide S34CG50 directly above/left with NEGATIVE x and y.
const BUILTIN = { id: 1, scaleFactor: 2, bounds: { x: 0, y: 0, width: 1512, height: 982 }, workArea: { x: 0, y: 38, width: 1512, height: 944 } };
const PORTRAIT = { id: 2, scaleFactor: 1, bounds: { x: 2549, y: -1598, width: 1080, height: 1920 }, workArea: { x: 2549, y: -1573, width: 1080, height: 1895 } };
const ULTRAWIDE = { id: 3, scaleFactor: 1, bounds: { x: -891, y: -1440, width: 3440, height: 1440 }, workArea: { x: -891, y: -1415, width: 3440, height: 1415 } };
const ALL = [BUILTIN, PORTRAIT, ULTRAWIDE];

test("pickDisplay: the display CONTAINING the cursor, incl. negative coordinates, never primary/origin", () => {
  assert.equal(D.pickDisplay(ALL, { x: 756, y: 491 }).id, 1);
  assert.equal(D.pickDisplay(ALL, { x: 3089, y: -638 }).id, 2);
  assert.equal(D.pickDisplay(ALL, { x: 829, y: -720 }).id, 3);
  assert.equal(D.pickDisplay(ALL, { x: -719, y: -1368 }).id, 3);   // negative x and y
  assert.equal(D.pickDisplay(ALL, { x: 0, y: 0 }).id, 1);          // origin belongs to the display whose top-left it is
  assert.equal(D.pickDisplay(ALL, { x: 0, y: -1 }).id, 3);         // one pixel above primary is the ultra-wide
  assert.equal(D.pickDisplay(ALL, { x: 1511, y: 981 }).id, 1);     // last pixel is inside, one more is not
  assert.equal(D.pickDisplay(ALL, { x: 2548, y: -100 }).id, 3);
  assert.equal(D.pickDisplay(ALL, { x: 2549, y: -100 }).id, 2);
});
test("pickDisplay: a point in a gap resolves to the nearest display, deterministically", () => {
  assert.equal(D.pickDisplay(ALL, { x: 2000, y: 400 }).id, 3);   // 400 below the ultra-wide vs 488 right of the built-in: ultra-wide is nearer
  assert.equal(D.pickDisplay(ALL, { x: 1600, y: 500 }).id, 1);   // 89 right of the built-in, 500 below the ultra-wide
  assert.equal(D.pickDisplay(ALL, { x: 2560, y: 400 }).id, 2);   // below the portrait's bottom edge (y=322)
  assert.throws(() => D.pickDisplay([], { x: 0, y: 0 }), /no displays/);
});
test("resolveDockDisplay: cursor by default; a fixed display wins; a missing fixed display falls back to the cursor display", () => {
  assert.equal(D.resolveDockDisplay(ALL, "cursor", { x: 3089, y: -638 }).id, 2);
  assert.equal(D.resolveDockDisplay(ALL, "id:3", { x: 3089, y: -638 }).id, 3);
  assert.equal(D.resolveDockDisplay(ALL, "id:99", { x: 3089, y: -638 }).id, 2);
  assert.equal(D.resolveDockDisplay([BUILTIN], "id:3", { x: 3089, y: -638 }).id, 1); // only one left
});

test("dockWindowRect is flush with display.bounds on its docked edge on EVERY display and side", () => {
  for (const display of ALL) for (const side of D.DOCK_SIDES) {
    const r = D.dockWindowRect(side, display);
    assert.ok(D.isFlush(side, display, r), `${display.id} ${side} ${JSON.stringify(r)}`);
    const b = display.bounds;
    assert.ok(r.x >= b.x && r.y >= b.y && r.x + r.width <= b.x + b.width && r.y + r.height <= b.y + b.height, `inside bounds ${display.id} ${side}`);
  }
});
test("exact rectangles: negative origin (ultra-wide), portrait, Retina primary", () => {
  assert.deepEqual(D.dockWindowRect("bottom", ULTRAWIDE), { x: 339, y: -640, width: 980, height: 640 });
  assert.deepEqual(D.dockWindowRect("right", ULTRAWIDE), { x: 2159, y: -1415, width: 390, height: 1415 });   // inset by the menu-bar band
  assert.deepEqual(D.dockWindowRect("left", ULTRAWIDE), { x: -891, y: -1415, width: 390, height: 1415 });
  assert.deepEqual(D.dockWindowRect("top", ULTRAWIDE), { x: 339, y: -1415, width: 980, height: 640 });
  assert.deepEqual(D.dockWindowRect("bottom", PORTRAIT), { x: 2603, y: -318, width: 972, height: 640 });   // 90 % of 1080 wide, half-height cap
  assert.deepEqual(D.dockWindowRect("right", PORTRAIT), { x: 3239, y: -1573, width: 390, height: 1895 });
  assert.deepEqual(D.dockWindowRect("bottom", BUILTIN), { x: 266, y: 342, width: 980, height: 640 });
  assert.deepEqual(D.dockWindowRect("right", BUILTIN), { x: 1122, y: 38, width: 390, height: 944 });
});
test("a display with no menu bar (no workArea inset) uses its full height for side docks", () => {
  const bare = { id: 9, scaleFactor: 1, bounds: { x: -3000, y: 200, width: 1600, height: 900 } };
  assert.deepEqual(D.dockWindowRect("left", bare), { x: -3000, y: 200, width: 390, height: 900 });
  assert.ok(D.isFlush("top", bare, D.dockWindowRect("top", bare)));
});
test("scale options: width/height scale shrink the footprint but keep the window flush", () => {
  const r = D.dockWindowRect("bottom", ULTRAWIDE, { widthScale: 50, heightScale: 50 });
  assert.equal(r.width, 490); assert.equal(r.height, 320); assert.ok(D.isFlush("bottom", ULTRAWIDE, r));
  const side = D.dockWindowRect("right", PORTRAIT, { heightScale: 50 });
  assert.equal(side.height, Math.floor(1895 * 0.5)); assert.ok(D.isFlush("right", PORTRAIT, side));
  assert.equal(D.dockWindowRect("bottom", BUILTIN, { widthScale: 5, heightScale: 500 }).width >= 320, true); // out-of-range scales are clamped, never crash
});
test("triggerZone: a thin strip on the true display edge, spanning the dock; never leaks onto a neighbour", () => {
  for (const display of ALL) for (const side of D.DOCK_SIDES) {
    const win = D.dockWindowRect(side, display), z = D.triggerZone(side, display, win, 2), b = display.bounds;
    assert.ok(z.x >= b.x && z.y >= b.y && z.x + z.width <= b.x + b.width && z.y + z.height <= b.y + b.height, `${display.id} ${side}`);
    assert.equal(side === "bottom" || side === "top" ? z.height : z.width, 2);
    for (const other of ALL) if (other.id !== display.id) assert.equal(D.inside({ x: z.x + 1, y: z.y + 1 }, other.bounds), false, `${display.id} ${side} leaks into ${other.id}`);
  }
  assert.deepEqual(D.triggerZone("bottom", ULTRAWIDE, D.dockWindowRect("bottom", ULTRAWIDE), 2), { x: 339, y: -2, width: 980, height: 2 });
  assert.deepEqual(D.triggerZone("right", PORTRAIT, D.dockWindowRect("right", PORTRAIT), 6), { x: 3623, y: -1573, width: 6, height: 1895 });
});

test("dock controller reveals from the edge on the display UNDER THE CURSOR in a 3-display layout (negative y)", () => {
  const base = { displays: ALL, mode: "bottom", forceOpen: false, pinned: false };
  for (const display of ALL) {
    const c = createDockController();
    const win = layoutFor("bottom", display.bounds, { workArea: display.workArea });
    const edge = { x: win.window.x + Math.floor(win.window.width / 2), y: display.bounds.y + display.bounds.height - 1 };
    c.update({ ...base, dockDisplayId: display.id, cursor: edge, now: 0 });
    const r = c.update({ ...base, dockDisplayId: display.id, cursor: edge, now: 200 });
    // D1's bottom edge borders D3? No: the ultra-wide is ABOVE the built-in, so only a real shared edge is excluded.
    const shared = D.pickDisplay(ALL, { x: edge.x, y: edge.y + 1 }).id !== D.pickDisplay(ALL, edge).id && ALL.some(o => o.id !== display.id && D.inside({ x: edge.x, y: edge.y + 1 }, o.bounds));
    assert.equal(r.revealed, !shared, `display ${display.id} bottom edge`);
  }
});
test("overlayTargets: current = just the active display, all = one per display", () => {
  assert.deepEqual(D.overlayTargets(ALL, "current", PORTRAIT).map(d => d.id), [2]);
  assert.deepEqual(D.overlayTargets(ALL, "all", PORTRAIT).map(d => d.id), [1, 2, 3]);
  assert.deepEqual(D.overlayTargets([BUILTIN], "all", BUILTIN).map(d => d.id), [1]);
  assert.notEqual(D.overlayTargets(ALL, "all", PORTRAIT), ALL); // a copy, callers may mutate
});

test("follower: cursor must dwell on another display before the dock moves (no bounce along shared corners)", () => {
  const f = D.createFollower({ dwellMs: 250 });
  assert.equal(f.update(ALL, { x: 756, y: 491 }, 1, 0), null);             // already there
  assert.equal(f.update(ALL, { x: 3089, y: -638 }, 1, 100), null);         // just arrived on 2
  assert.equal(f.update(ALL, { x: 3089, y: -638 }, 1, 300), null);         // 200 ms: not yet
  assert.equal(f.update(ALL, { x: 3089, y: -638 }, 1, 360).id, 2);         // 260 ms: move
  assert.equal(f.update(ALL, { x: 756, y: 491 }, 2, 400), null);           // flicker back resets the clock
  assert.equal(f.update(ALL, { x: 3089, y: -638 }, 2, 450), null);
  assert.equal(f.update(ALL, { x: 756, y: 491 }, 2, 500), null);
  assert.equal(f.update(ALL, { x: 756, y: 491 }, 2, 760).id, 1);          // settled on 1 for 260 ms
  f.update(ALL, { x: 829, y: -720 }, 1, 800); f.reset();
  assert.equal(f.update(ALL, { x: 829, y: -720 }, 1, 900), null);          // reset starts over
});
test("isFlush / withinRounding tolerate the 1 DIP rounding of a Retina<->1x hop but nothing more", () => {
  const want = { x: 2159, y: -1415, width: 390, height: 1415 };
  assert.equal(D.withinRounding({ ...want, x: want.x + 1 }, want), true);
  assert.equal(D.withinRounding({ ...want, height: want.height - 2 }, want), false);
  assert.equal(D.isFlush("right", ULTRAWIDE, want), true);
  assert.equal(D.isFlush("right", ULTRAWIDE, { ...want, x: want.x + 1 }), false);
  assert.equal(D.isFlush("nope", ULTRAWIDE, want), false);
});
test("unknown dock side is rejected", () => {
  assert.throws(() => D.dockWindowRect("diagonal", BUILTIN), /dock side/);
  assert.throws(() => D.triggerZone("diagonal", BUILTIN, { x: 0, y: 0, width: 1, height: 1 }), /dock side/);
});

test("planOverlays: create / move / keep / destroy decisions across displays and scope switches", () => {
  const live = new Map([[1, BUILTIN.bounds]]);
  assert.deepEqual(D.planOverlays(live, [BUILTIN]), { create: [], move: [], keep: [1], destroy: [] });
  assert.deepEqual(D.planOverlays(live, ALL), { create: [2, 3], move: [], keep: [1], destroy: [] });                  // 현재 -> 전체
  assert.deepEqual(D.planOverlays(new Map(ALL.map(d => [d.id, d.bounds])), [PORTRAIT]), { create: [], move: [], keep: [2], destroy: [1, 3] }); // 전체 -> 현재 on display 2
  assert.deepEqual(D.planOverlays(live, [PORTRAIT]), { create: [2], move: [], keep: [], destroy: [1] });             // the dock moved to another display
  assert.deepEqual(D.planOverlays(new Map([[3, { ...ULTRAWIDE.bounds, width: 2560 }]]), [ULTRAWIDE]), { create: [], move: [3], keep: [], destroy: [] }); // resolution/metrics changed
  assert.deepEqual(D.planOverlays(new Map(), []), { create: [], move: [], keep: [], destroy: [] });
  // a display unplugged while in 전체: its overlay is destroyed, the rest are kept
  assert.deepEqual(D.planOverlays(new Map(ALL.map(d => [d.id, d.bounds])), [BUILTIN, ULTRAWIDE]), { create: [], move: [], keep: [1, 3], destroy: [2] });
});
test("smoke target helper: SMOKE_DISPLAY selection and cursor-point mapping on negative-origin displays", () => {
  const T = require("../smoke/target.js");
  assert.equal(T.pickTarget(ALL, 1, undefined).id, 1);
  assert.equal(T.pickTarget(ALL, 1, "primary").id, 1);
  assert.equal(T.pickTarget(ALL, 1, "2").id, 3);
  assert.equal(T.pickTarget(ALL, 1, "id:2").id, 2);
  assert.equal(T.pickTarget(ALL, 1, "cursor", { x: 829, y: -720 }).id, 3);
  assert.throws(() => T.pickTarget(ALL, 1, "7"), /out of range/);
  assert.throws(() => T.pickTarget(ALL, 1, "id:9"), /no such display/);
  assert.throws(() => T.pickTarget(ALL, 1, "left"), /expected/);
  assert.deepEqual(T.pointIn(ULTRAWIDE.bounds, 0.5, 0.5), { x: 829, y: -720 });
  assert.deepEqual(T.edgePoint("bottom", ULTRAWIDE.bounds), { x: 829, y: -1 });
  assert.deepEqual(T.edgePoint("top", PORTRAIT.bounds), { x: 3089, y: -1598 });
  assert.deepEqual(T.edgePoint("right", PORTRAIT.bounds), { x: 3628, y: -638 });
  assert.deepEqual(T.edgePoint("left", ULTRAWIDE.bounds), { x: -891, y: -720 });
  for (const display of ALL) for (const side of D.DOCK_SIDES) assert.equal(D.pickDisplay(ALL, T.edgePoint(side, display.bounds)).id, display.id, `${display.id} ${side}`);
});
