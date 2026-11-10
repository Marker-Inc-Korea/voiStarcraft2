const test = require("node:test");
const assert = require("node:assert/strict");
const G = require("../sketch/geometry.js");

const BUILTIN = { id: 1, scaleFactor: 2, bounds: { x: 0, y: 0, width: 1512, height: 982 } };
const EXT = { id: 3, scaleFactor: 1, bounds: { x: -891, y: -1440, width: 3440, height: 1440 } };

test("normalize/denormalize round-trip inside a global-DIP rect, origin top-left, y down", () => {
  const rect = { x: 100, y: 700, w: 200, h: 200 };
  assert.deepEqual(G.normalize(rect, 100, 700), { nx: 0, ny: 0, inside: true });
  assert.deepEqual(G.normalize(rect, 300, 900), { nx: 1, ny: 1, inside: true });
  assert.deepEqual(G.normalize(rect, 200, 800), { nx: 0.5, ny: 0.5, inside: true });
  assert.deepEqual(G.denormalize(rect, 0.25, 0.75), { x: 150, y: 850 });
  assert.equal(G.normalize(rect, 99, 800).inside, false);
  assert.equal(G.normalize(rect, 200, 901).inside, false);
});
test("normalization is about the minimap FRAME only: a non-square frame still maps to 0..1 (no letterbox assumption here)", () => {
  const rect = { x: 0, y: 0, w: 300, h: 150 };
  assert.deepEqual(G.normalize(rect, 150, 75), { nx: 0.5, ny: 0.5, inside: true });
  assert.deepEqual(G.denormalize(rect, 1, 1), { x: 300, y: 150 });
});
test("default rect: bottom-left square scaled with the display height, inside the display, per display", () => {
  const r = G.defaultMinimapRect(BUILTIN);
  assert.equal(r.w, r.h, "square frame");
  assert.ok(r.x >= BUILTIN.bounds.x && r.x + r.w <= BUILTIN.bounds.x + BUILTIN.bounds.width);
  assert.ok(r.y + r.h <= BUILTIN.bounds.y + BUILTIN.bounds.height);
  assert.ok(r.y + r.h > BUILTIN.bounds.y + BUILTIN.bounds.height * 0.9, "hugs the bottom edge");
  assert.ok(r.x < BUILTIN.bounds.x + BUILTIN.bounds.width * 0.1, "hugs the left edge");
  assert.ok(Math.abs(r.h / BUILTIN.bounds.height - G.DEFAULT_SIDE_OF_HEIGHT) < 0.002);
  const e = G.defaultMinimapRect(EXT);
  assert.ok(e.x >= EXT.bounds.x && e.x < EXT.bounds.x + 200, "negative-origin display: rect follows the display origin");
  assert.ok(e.y + e.h <= EXT.bounds.y + EXT.bounds.height);
});
test("calibration key: display id + resolution + scale factor", () => {
  assert.equal(G.displayKey(BUILTIN), "1@1512x982@2");
  assert.equal(G.displayKey(EXT), "3@3440x1440@1");
  assert.notEqual(G.displayKey(BUILTIN), G.displayKey({ ...BUILTIN, bounds: { ...BUILTIN.bounds, width: 1728, height: 1117 } }), "a resolution change invalidates the stored rect");
});
test("stored rects are display-local; resolve returns the saved rect in global DIP, else the default (flagged)", () => {
  let store = G.emptyStore();
  const r0 = G.resolveRect(store, EXT);
  assert.equal(r0.calibrated, false);
  store = G.saveRect(store, EXT, { x: -800, y: -300, w: 250, h: 250 });
  const r1 = G.resolveRect(store, EXT);
  assert.equal(r1.calibrated, true);
  assert.deepEqual(r1.rect, { x: -800, y: -300, w: 250, h: 250 });
  assert.equal(G.resolveRect(store, BUILTIN).calibrated, false, "other display untouched");
  assert.equal(G.resolveRect(store, { ...EXT, bounds: { ...EXT.bounds, width: 2560 } }).calibrated, false, "other resolution untouched");
  const back = G.parseStore(JSON.stringify(G.saveRect(G.emptyStore(), EXT, { x: -800, y: -300, w: 250, h: 250 })));
  assert.deepEqual(G.resolveRect(back, EXT).rect, r1.rect);
  store = G.clearRect(store, EXT); assert.equal(G.resolveRect(store, EXT).calibrated, false);
});
test("parseStore never throws and drops garbage entries", () => {
  assert.deepEqual(G.parseStore("not json"), G.emptyStore());
  const s = G.parseStore(JSON.stringify({ version: 1, rects: { "1@1512x982@2": { x: 1, y: 2, w: -5, h: 10 }, "2@10x10@1": { x: "a" }, "3@100x100@1": { x: 10, y: 10, w: 50, h: 50 } } }));
  assert.deepEqual(Object.keys(s.rects), ["3@100x100@1"]);
});
test("fitRect clamps a dragged/resized rect into its display and keeps a minimum size", () => {
  const d = BUILTIN.bounds;
  assert.deepEqual(G.fitRect({ x: -50, y: 900, w: 200, h: 200 }, d), { x: 0, y: 782, w: 200, h: 200 });
  const tiny = G.fitRect({ x: 10, y: 10, w: 3, h: 3 }, d);
  assert.ok(tiny.w >= G.MIN_SIDE && tiny.h >= G.MIN_SIDE);
  const huge = G.fitRect({ x: 0, y: 0, w: 5000, h: 5000 }, d);
  assert.ok(huge.w <= d.width && huge.h <= d.height);
});
test("scale factor: capture pixels follow the display scale, output is capped at 512 px on the long side", () => {
  const rect = { x: 10, y: 700, w: 240, h: 240 };
  assert.deepEqual(G.capturePixels(rect, BUILTIN), { x: 20, y: 1400, w: 480, h: 480 });
  assert.deepEqual(G.outputSize(480, 480, 512), { w: 480, h: 480 });
  assert.deepEqual(G.outputSize(1024, 512, 512), { w: 512, h: 256 });
  assert.deepEqual(G.outputSize(300, 600, 512), { w: 256, h: 512 });
});
test("the display holding the rect is found with negative origins and gaps", () => {
  assert.equal(G.displayForRect([BUILTIN, EXT], { x: -800, y: -300, w: 250, h: 250 }).id, 3);
  assert.equal(G.displayForRect([BUILTIN, EXT], { x: 10, y: 700, w: 240, h: 240 }).id, 1);
});
test("hit-testing for the sketch window: only points inside the rect belong to the sketch", () => {
  const rect = { x: 10, y: 10, w: 100, h: 100 };
  assert.equal(G.contains(rect, 10, 10), true); assert.equal(G.contains(rect, 110, 110), true); assert.equal(G.contains(rect, 111, 50), false);
});
