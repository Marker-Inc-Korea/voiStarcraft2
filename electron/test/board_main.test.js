const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
const S = require("../renderer/settings.js");

const DISPLAY = { id: 1, bounds: { x: 0, y: 0, width: 1512, height: 982 }, workArea: { x: 0, y: 38, width: 1512, height: 944 } };
const OTHER = { id: 2, bounds: { x: 2549, y: -1598, width: 1080, height: 1920 }, workArea: { x: 2549, y: -1573, width: 1080, height: 1895 } };

const test_cleanup = [];
test.afterEach(() => { while (test_cleanup.length) test_cleanup.pop().stop(); });
function harness(over = {}) {
  const calls = [];
  const windows = [];
  class FakeWin {
    constructor(options) { this.options = options; this.bounds = { x: options.x, y: options.y, width: options.width, height: options.height }; this.visible = false; this.destroyed = false; this.sent = []; windows.push(this);
      this.webContents = { isLoading: () => false, send: (channel, payload) => this.sent.push({ channel, payload }), setWindowOpenHandler() {}, on() {}, once() {} }; }
    setAlwaysOnTop(...a) { calls.push(["alwaysOnTop", ...a]); } setVisibleOnAllWorkspaces() {} setFocusable(v) { calls.push(["focusable", v]); }
    setIgnoreMouseEvents(...a) { calls.push(["ignore", ...a]); } on() {} loadFile() {} isDestroyed() { return this.destroyed; } isVisible() { return this.visible; }
    showInactive() { this.visible = true; calls.push(["showInactive"]); } show() { calls.push(["SHOW"]); } focus() { calls.push(["FOCUS"]); } hide() { this.visible = false; }
    setBounds(b) { this.bounds = { ...b }; } getBounds() { return this.bounds; } destroy() { this.destroyed = true; }
  }
  const state = { settings: S.defaults(), display: DISPLAY, dock: null, cursor: { x: 900, y: 500 }, now: 1000, shortcuts: new Map() };
  if (over.settings) state.settings = over.settings;
  const { createBoard } = require("../board.js");
  const board = createBoard({
    BrowserWindow: FakeWin, getSettings: () => state.settings, getDisplay: () => state.display, getDockRect: () => state.dock, getCursor: () => state.cursor, now: () => state.now,
    registerGlobal: (accel, cb) => { state.shortcuts.set(accel, cb); return true; }, unregisterGlobal: accel => state.shortcuts.delete(accel), onShortcutStatus: () => {},
  });
  const start = board.start;
  board.start = () => { start(); test_cleanup.push(board); };
  return { board, state, calls, windows };
}
const card = (id, over = {}) => ({ id, name: id, tier: "operation", stages: ["done", "active", "todo", "todo", "todo"], blockedReason: "", sim: false, ...over });
const cards = n => Array.from({ length: n }, (_, i) => card(`c${i}`));

test("empty board: tiny tab, shown inactive, click-through, never focuses", () => {
  const { board, windows, calls } = harness();
  board.start();
  const win = windows[0];
  assert.equal(win.options.focusable, false); assert.equal(win.options.frame, false); assert.equal(win.options.transparent, true); assert.equal(win.options.skipTaskbar, true);
  assert.deepEqual([win.bounds.width, win.bounds.height], [92, 26]);
  assert.ok(calls.some(c => c[0] === "showInactive")); assert.ok(!calls.some(c => c[0] === "SHOW" || c[0] === "FOCUS"));
  assert.deepEqual(calls.find(c => c[0] === "ignore"), ["ignore", true, { forward: true }]);
  assert.equal(win.visible, true);
});
test("hideWhenEmpty hides the window entirely", () => {
  const s = S.defaults(); s.board.hideWhenEmpty = true;
  const { board, windows } = harness({ settings: s });
  board.start();
  assert.equal(windows.length, 0);
  board.setCards(cards(2));
  assert.equal(windows[0].visible, true);
});
test("cards: 3-4 per page, window never grows with card count, 40 % cap, upper-left", () => {
  const { board, windows, state } = harness();
  board.start();
  const heights = new Set();
  for (const n of [1, 2, 3, 4, 7, 12, 24]) {
    board.setCards(cards(n));
    const b = windows[0].bounds;
    assert.ok(b.height <= 982 * 0.4); assert.equal(b.x, 12); assert.ok(b.y >= 38);
    if (n >= 3) heights.add(b.height);
    assert.ok(board.snapshot().view.cards.length <= 3);
  }
  assert.equal(heights.size, 2); // 3 cards single page vs. paged (header) - never more
  state.settings = { ...state.settings, board: { ...state.settings.board, pageSize: 4 } };
  board.configure();
  assert.ok(board.snapshot().view.cards.length <= 4);
});
test("hover hit-test: interactive only while the cursor is on the board; hover pauses paging", () => {
  const { board, windows, state, calls } = harness();
  board.start(); board.setCards(cards(7));
  const b = windows[0].bounds;
  state.cursor = { x: b.x + 10, y: b.y + 10 }; board.tick();
  assert.deepEqual(calls.filter(c => c[0] === "ignore").pop(), ["ignore", false]);
  assert.equal(board.snapshot().hovered, true);
  state.now += 60000; board.tick();
  assert.equal(board.snapshot().view.label, "1/3"); // hovered: no auto-advance
  state.cursor = { x: 900, y: 500 }; board.tick();
  assert.deepEqual(calls.filter(c => c[0] === "ignore").pop(), ["ignore", true, { forward: true }]);
});
test("auto-advance via ticks and shortcut registration only while paging", () => {
  const { board, state } = harness();
  board.start();
  assert.equal(state.shortcuts.size, 0);
  board.setCards(cards(7));
  assert.deepEqual([...state.shortcuts.keys()].sort(), ["CommandOrControl+Shift+PageDown", "CommandOrControl+Shift+PageUp"]);
  state.now += 6000; board.tick();
  assert.equal(board.snapshot().view.label, "2/3");
  state.shortcuts.get("CommandOrControl+Shift+PageDown")(); assert.equal(board.snapshot().view.label, "3/3");
  state.shortcuts.get("CommandOrControl+Shift+PageUp")(); assert.equal(board.snapshot().view.label, "2/3");
  board.setCards(cards(2));
  assert.equal(state.shortcuts.size, 0);
});
test("follows the HUD display and avoids the HUD panel", () => {
  const { board, windows, state } = harness();
  board.start(); board.setCards(cards(3));
  state.display = OTHER; board.relayout();
  assert.equal(windows[0].bounds.x, 2549 + 12); assert.equal(windows[0].bounds.y, -1598 + 64);
  state.display = DISPLAY; state.dock = { x: 0, y: 40, width: 400, height: 300 }; board.relayout();
  const b = windows[0].bounds;
  assert.ok(b.x >= 408 || b.y >= 348 || b.y + b.height <= 32);
});
test("board:view is pushed to the board window and is deduplicated", () => {
  const { board, windows } = harness();
  board.start(); board.setCards(cards(2));
  const views = () => windows[0].sent.filter(s => s.channel === "board:view");
  const n = views().length;
  board.tick(); board.tick();
  assert.equal(views().length, n);
  assert.equal(views().at(-1).payload.cards.length, 2);
});
test("disabled board hides and never creates a window", () => {
  const s = S.defaults(); s.board.enabled = false;
  const { board, windows } = harness({ settings: s });
  board.start(); board.setCards(cards(3));
  assert.equal(windows.length, 0);
});

test("main.js wiring is delimited, validated and cleaned up", () => {
  const main = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");
  assert.match(main, /require\("\.\/board\.js"\)/);
  assert.match(main, /Board\.CHANNELS\.publish, input => \{ if \(board\) board\.setCards\(P\.validateBoardCards\(input\)\)/);
  assert.match(main, /board\.ownsWebContents\(event\.sender\)/);
  assert.match(main, /board\.stop\(\)/);
  assert.match(main, /BOARD_CSP/);
  const boardSrc = fs.readFileSync(path.join(__dirname, "..", "board.js"), "utf8");
  for (const needle of ["frame: false", "transparent: true", "hasShadow: false", "skipTaskbar: true", "focusable: false", 'setAlwaysOnTop(true, "screen-saver")', "setIgnoreMouseEvents(true, { forward: true })", "visibleOnFullScreen: true", "showInactive"]) assert.ok(boardSrc.includes(needle), needle);
  assert.doesNotMatch(boardSrc, /\.focus\(|\.show\(\)/);
  const preload = fs.readFileSync(path.join(__dirname, "..", "board", "preload.js"), "utf8");
  assert.doesNotMatch(preload, /commander|invoke/);
});
