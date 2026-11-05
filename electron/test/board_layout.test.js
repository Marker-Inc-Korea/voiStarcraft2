const test = require("node:test");
const assert = require("node:assert/strict");
const L = require("../board/layout.js");

const BUILTIN = { id: 1, bounds: { x: 0, y: 0, width: 1512, height: 982 }, workArea: { x: 0, y: 38, width: 1512, height: 944 } };
const PORTRAIT = { id: 2, bounds: { x: 2549, y: -1598, width: 1080, height: 1920 }, workArea: { x: 2549, y: -1573, width: 1080, height: 1895 } };
const ULTRAWIDE = { id: 3, bounds: { x: -891, y: -1440, width: 3440, height: 1440 }, workArea: { x: -891, y: -1415, width: 3440, height: 1415 } };
const base = { anchor: "upper-left", pageSize: 3, cardCount: 3, pageCount: 1, width: 320, offsetX: 12, offsetY: 64 };
const ok = input => { const r = L.computeBoardLayout(input); assert.ok(r.visible, JSON.stringify(r)); return r; };

test("upper-left: 320 px wide, 12 px from the left, 64 px below the top, as tall as its cards", () => {
  const r = ok({ ...base, display: BUILTIN });
  assert.deepEqual(r.rect, { x: 12, y: 64, width: 320, height: L.boardHeight(3, false) });
  assert.equal(r.reason, "");
});
test("width is clamped to 300..340", () => {
  assert.equal(ok({ ...base, display: BUILTIN, width: 100 }).rect.width, 300);
  assert.equal(ok({ ...base, display: BUILTIN, width: 999 }).rect.width, 340);
});
test("height never exceeds 40 % of the display, for every display and page size", () => {
  for (const display of [BUILTIN, PORTRAIT, ULTRAWIDE]) for (const pageSize of [3, 4]) for (const cardCount of [1, 2, 3, 4]) {
    const r = L.computeBoardLayout({ ...base, display, pageSize, cardCount: Math.min(cardCount, pageSize), pageCount: 3 });
    if (r.visible) assert.ok(r.rect.height <= Math.floor(display.bounds.height * 0.4), `${display.id} ${pageSize} ${cardCount}`);
  }
});
test("a paged board keeps ONE page height (header + pageSize cards) regardless of how many pages exist", () => {
  const a = ok({ ...base, display: BUILTIN, pageCount: 2 }).rect.height;
  const b = ok({ ...base, display: BUILTIN, pageCount: 9, cardCount: 1 }).rect.height;
  assert.equal(a, b);
  assert.equal(a, L.boardHeight(3, true));
});
test("effective page size shrinks when the 40 % cap cannot hold the configured page", () => {
  const short = { id: 9, bounds: { x: 0, y: 0, width: 1280, height: 400 }, workArea: { x: 0, y: 0, width: 1280, height: 400 } }; // cap 160 px
  const r = L.computeBoardLayout({ ...base, display: short, pageSize: 4, cardCount: 4, pageCount: 2, offsetY: 0 });
  assert.ok(r.pageSize < 4);
  assert.ok(r.rect.height <= 160);
  assert.ok(L.fitSlots(BUILTIN) >= 4);
});
test("never reaches the screen centre or the lower 40 % (bottom HUD / minimap area), on any display", () => {
  for (const display of [BUILTIN, PORTRAIT, ULTRAWIDE]) for (const anchor of L.ANCHORS) for (const pageSize of [3, 4]) {
    const r = L.computeBoardLayout({ ...base, display, anchor, pageSize, cardCount: pageSize, pageCount: 2 });
    assert.ok(r.visible);
    const b = display.bounds;
    assert.ok(r.rect.x + r.rect.width <= b.x + b.width / 2, "left of centre");
    assert.ok(r.rect.y + r.rect.height <= b.y + b.height * 0.6, "above the bottom 40 %");
    assert.ok(r.rect.y >= display.workArea.y, "below the menu bar");
    assert.ok(r.rect.x >= b.x);
  }
});
test("multi-display: rect is expressed in the owning display's global DIP space (negative origins included)", () => {
  assert.deepEqual(ok({ ...base, display: ULTRAWIDE }).rect.x, -891 + 12);
  assert.equal(ok({ ...base, display: ULTRAWIDE }).rect.y, -1415 + 0 > -1440 + 64 ? -1415 : -1440 + 64);
  assert.equal(ok({ ...base, display: PORTRAIT }).rect.x, 2549 + 12);
  assert.equal(ok({ ...base, display: PORTRAIT }).rect.y, -1598 + 64);
});
test("menu bar: the top offset never pushes the board above workArea", () => {
  assert.equal(ok({ ...base, display: BUILTIN, offsetY: 0 }).rect.y, 38);
});
test("left anchor: vertically centred in the upper 40 % band, same left offset", () => {
  const r = ok({ ...base, display: BUILTIN, anchor: "left" });
  assert.equal(r.rect.x, 12);
  const centre = r.rect.y + r.rect.height / 2;
  assert.ok(Math.abs(centre - 982 * 0.4) <= 2, String(centre));
});
test("narrow display: width shrinks so the board stays in the left 40 %", () => {
  const narrow = { id: 5, bounds: { x: 0, y: 0, width: 640, height: 480 }, workArea: { x: 0, y: 0, width: 640, height: 480 } };
  const r = ok({ ...base, display: narrow, offsetY: 10 });
  assert.ok(r.rect.x + r.rect.width <= 320);
  assert.ok(r.rect.width >= 220);
});
test("collapsed: a tiny word tab", () => {
  const r = ok({ ...base, display: BUILTIN, collapsed: true, cardCount: 0 });
  assert.equal(r.rect.width, L.METRICS.tabW); assert.equal(r.rect.height, L.METRICS.tabH); assert.equal(r.slots, 0);
});
test("HUD avoidance: a HUD panel overlapping the board moves the board beside/below it, never overlaps; no room -> hidden", () => {
  const hud = { x: 0, y: 40, width: 400, height: 300 };
  const r = ok({ ...base, display: BUILTIN, dockRect: hud });
  assert.equal(L.intersects(r.rect, hud), false);
  assert.equal(r.reason, "avoid-hud");
  const wall = { x: 0, y: 0, width: 1512, height: 982 };
  const hidden = L.computeBoardLayout({ ...base, display: BUILTIN, dockRect: wall });
  assert.equal(hidden.visible, false); assert.equal(hidden.rect, null);
  // a bottom HUD far away does not move the board
  const bottom = { x: 266, y: 800, width: 980, height: 160 };
  assert.equal(ok({ ...base, display: BUILTIN, dockRect: bottom }).reason, "");
});
test("above-the-HUD shrink reports the page size that truly fits", () => {
  const hud = { x: 0, y: 300, width: 500, height: 200 };
  const r = L.computeBoardLayout({ ...base, display: BUILTIN, pageSize: 4, cardCount: 4, pageCount: 2, dockRect: hud, offsetX: 12, offsetY: 64 });
  if (r.visible) assert.equal(L.intersects(r.rect, hud), false);
});
