"use strict";
// Main-process owner of the tactical board window: lifecycle, display-follow, click-through/hover hit-test,
// paging shortcuts and the `board:cards` / `board:view` plumbing. Electron objects are injected so the whole controller
// is unit-testable with fakes (test/board_main.test.js). Geometry/paging/view are pure modules under electron/board/.
const path = require("node:path");
const Layout = require("./board/layout.js");
const Paging = require("./board/paging.js");
const View = require("./board/view.js");

const CHANNELS = Object.freeze({ cards: "board:cards", publish: "board:publish", view: "board:view", page: "board:page" });
const BOARD_CSP = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'none'; base-uri 'none'; form-action 'none'";
const POLL_MS = 80;

function pagingOptions(settings) {
  const b = settings.board;
  return { pageSize: b.pageSize, autoAdvanceMs: b.autoAdvanceSec * 1000, ageOutMs: b.ageOutSec * 1000, hideWhenEmpty: b.hideWhenEmpty };
}

// deps: { BrowserWindow, getSettings(), getDisplay() -> {id,bounds,workArea}, getDockRect() -> rect|null, getCursor(),
//         registerGlobal(accel, cb) -> bool, unregisterGlobal(accel), onShortcutStatus(ok), pushCards?(cards) (test hook), now?() }
function createBoard(deps) {
  const now = deps.now || Date.now;
  const paging = Paging.createPaging(pagingOptions(deps.getSettings()));
  let win = null;
  let placed = null;       // last applied { visible, rect }
  let ignoring = true;
  let hovered = false;
  let lastSig = "";
  let timer = null;
  let layoutState = { visible: false, rect: null, pageSize: 3 };
  let shortcuts = { up: "", down: "" };
  let latestCards = [];

  const alive = () => Boolean(win && !win.isDestroyed());
  const settings = () => deps.getSettings();

  function createWindow(rect) {
    win = new deps.BrowserWindow({
      ...rect, show: false, frame: false, transparent: true, resizable: false, movable: false, minimizable: false, maximizable: false, closable: false,
      fullscreenable: false, focusable: false, skipTaskbar: true, hasShadow: false, backgroundColor: "#00000000", enableLargerThanScreen: true, title: "voiStarcraft2 board",
      ...(process.platform === "darwin" ? { type: "panel" } : {}),
      webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, preload: path.join(__dirname, "board", "preload.js"), backgroundThrottling: false },
    });
    win.setAlwaysOnTop(true, "screen-saver");
    win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true, skipTransformProcessType: true });
    win.setIgnoreMouseEvents(true, { forward: true });
    win.setFocusable(false);
    ignoring = true;
    win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    win.webContents.on("will-navigate", event => event.preventDefault());
    win.webContents.on("did-finish-load", () => { lastSig = ""; pushView(); });
    win.on("closed", () => { win = null; placed = null; });
    win.loadFile(path.join(__dirname, "board", "index.html"));
  }

  function currentView() { return paging.view(now()); }

  // Effective page size = configured size, reduced when the 40 % height cap or the HUD panel leaves room for fewer cards.
  function relayout() {
    const s = settings();
    const total = paging.view(now()).total;
    if (!s.board.enabled) { layoutState = { visible: false, rect: null, pageSize: s.board.pageSize }; return applyWindow(); }
    const display = deps.getDisplay();
    const compute = size => Layout.computeBoardLayout({
      display, anchor: s.board.anchor, pageSize: size, cardCount: Math.min(size, total), pageCount: Math.ceil(total / size),
      width: s.board.width, offsetX: s.board.offsetX, offsetY: s.board.offsetY, collapsed: total === 0, dockRect: deps.getDockRect(),
    });
    let size = Math.min(s.board.pageSize, Layout.fitSlots(display));
    let next = compute(size);
    if (next.visible && next.pageSize < size) { size = next.pageSize; next = compute(size); }
    if (total === 0 && s.board.hideWhenEmpty) next = { ...next, visible: false };
    layoutState = { ...next, pageSize: size };
    if (size !== effectiveSize) { effectiveSize = size; paging.configure({ pageSize: size }); }
    return applyWindow();
  }
  let effectiveSize = 0;

  function applyWindow() {
    if (!layoutState.visible) {
      if (alive() && win.isVisible()) win.hide();
      if (alive() && !ignoring) setIgnore(true);
      placed = null;
      return;
    }
    const rect = layoutState.rect;
    if (!alive()) createWindow(rect);
    const same = placed && placed.x === rect.x && placed.y === rect.y && placed.width === rect.width && placed.height === rect.height;
    if (!same) {
      win.setBounds(rect, false);
      for (let i = 0; i < 2; i += 1) { const got = win.getBounds(); if (got.x === rect.x && got.y === rect.y && got.width === rect.width && got.height === rect.height) break; win.setBounds(rect, false); }
      placed = { ...rect };
    }
    if (!win.isVisible()) win.showInactive(); // never steals focus
  }

  function pushView() {
    if (!alive() || win.webContents.isLoading()) return;
    const model = View.buildBoardView(currentView(), settings());
    const sig = JSON.stringify(model);
    if (sig === lastSig) return;
    lastSig = sig;
    win.webContents.send(CHANNELS.view, model);
  }

  function setIgnore(next) {
    if (!alive() || next === ignoring) return;
    ignoring = next;
    if (next) win.setIgnoreMouseEvents(true, { forward: true }); else win.setIgnoreMouseEvents(false);
  }

  function updateShortcuts() {
    const s = settings();
    const view = currentView();
    const want = s.board.enabled && view.mode === "board" && view.pageCount > 1 && layoutState.visible
      ? { up: s.board.pageUpShortcut, down: s.board.pageDownShortcut } : { up: "", down: "" };
    if (want.up === shortcuts.up && want.down === shortcuts.down) return;
    if (shortcuts.up) deps.unregisterGlobal(shortcuts.up);
    if (shortcuts.down) deps.unregisterGlobal(shortcuts.down);
    let ok = true;
    if (want.up) ok = deps.registerGlobal(want.up, () => page(-1)) && ok;
    if (want.down) ok = deps.registerGlobal(want.down, () => page(1)) && ok;
    shortcuts = want;
    if (deps.onShortcutStatus) deps.onShortcutStatus(ok);
  }

  // One refresh funnel: layout (size depends on card count / page count / mode), window, view, shortcuts.
  function refresh() {
    relayout();
    pushView();
    updateShortcuts();
  }

  function page(delta) {
    const moved = delta > 0 ? paging.next(now()) : paging.prev(now());
    if (moved) refresh();
    return moved;
  }

  function hitTest() {
    if (!alive() || !layoutState.visible || !placed || !win.isVisible()) { if (hovered) { hovered = false; paging.setHover(false, now()); } return; }
    const cursor = deps.getCursor();
    const inside = cursor.x >= placed.x && cursor.x < placed.x + placed.width && cursor.y >= placed.y && cursor.y < placed.y + placed.height;
    if (inside !== hovered) { hovered = inside; paging.setHover(inside, now()); }
    setIgnore(!inside);
  }

  function tick() {
    paging.tick(now());
    hitTest();
    refresh();
  }

  // Cards from main (already validated by P.validateBoardCards). Returns the jump taken.
  function setCards(cards) {
    latestCards = cards;
    const jump = paging.ingest(cards, now());
    refresh();
    if (alive() && !win.webContents.isLoading()) win.webContents.send(CHANNELS.cards, cards); // raw cards for diagnostics/consumers
    return jump;
  }

  function configure() {
    effectiveSize = 0; // pageSize may have changed in settings
    paging.configure(pagingOptions(settings()));
    refresh();
  }

  function start() { if (!timer) timer = setInterval(tick, POLL_MS); refresh(); }
  function stop() {
    if (timer) { clearInterval(timer); timer = null; }
    if (shortcuts.up) deps.unregisterGlobal(shortcuts.up);
    if (shortcuts.down) deps.unregisterGlobal(shortcuts.down);
    shortcuts = { up: "", down: "" };
    if (alive()) { win.destroy(); }
    win = null;
  }
  // The board window may only ask for a page change.
  function ownsWebContents(contents) { return alive() && win.webContents === contents; }

  return { start, stop, setCards, configure, relayout: refresh, tick, page, ownsWebContents, getWindow: () => win, snapshot: () => ({ layout: layoutState, view: currentView(), ignoring, hovered, shortcuts, cards: latestCards.length }) };
}

module.exports = { createBoard, CHANNELS, BOARD_CSP, POLL_MS, pagingOptions };
