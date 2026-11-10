"use strict";
// Main-process owner of the minimap sketch feature. Electron objects and I/O are injected so the whole thing runs under fakes
// (test/sketch_controller.test.js). It wires: native chord helper -> chord state machine -> sketch window (draw) -> stroke collection ->
// minimap capture -> POST /api/route/sketch -> HUD result line, and the route event feed -> route model -> sketch window + HUD.
//
// Invariants (each is asserted by a test):
//   - the sketch window is a non-focusable panel that is only ever shown with showInactive(); focus()/show()/moveTop-with-activation are never called
//   - it accepts mouse events ONLY while drawing (bounds == the minimap rect exactly) or calibrating; otherwise it is click-through
//   - nothing is POSTed unless the chord was RELEASED (or the toggle pressed again) with at least one valid stroke
//   - nothing in here touches the real mouse cursor in any way; the "agent cursor" is a DOM element in the sketch window
const path = require("node:path");
const Chord = require("./chord.js");
const Geometry = require("./geometry.js");
const Helper = require("./helper.js");
const Feed = require("./feed.js");
const RouteModel = require("./route_model.js");
const Payload = require("./payload.js");
const Capture = require("./capture.js");
const Messages = require("./messages.js");
const Displays = require("../displays.js");

const CHANNELS = Object.freeze({
  layout: "sketch:layout", mode: "sketch:mode", collect: "sketch:collect", routeEvent: "sketch:route-event", routeReset: "sketch:route-reset",
  submitted: "sketch:submitted", settle: "sketch:settle", clear: "sketch:clear", skin: "sketch:skin",
  strokes: "sketch:strokes", cancel: "sketch:cancel", calibration: "sketch:calibration",
});
const SKETCH_CSP = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'none'; base-uri 'none'; form-action 'none'";
const DISPLAY_PAD = 36;          // DIP of room around the minimap for labels and the ghost cursor while NOT drawing
const AWAIT_PLAN_MS = 30000;     // after an "execute" answer: poll fast until the route_plan event shows up
const CALIBRATION_TIMEOUT_MS = 120000;
const CANCEL_REASONS = Object.freeze({ key: "다른 키", esc: "Esc", tap: "클릭", "helper-exit": "도우미 종료", "helper-error": "도우미 오류", disabled: "비활성" });

function createSketchController(deps) {
  const {
    BrowserWindow, getDisplays, getCursor, getSettings, requestJson, pushHud = () => {}, registerGlobal = () => false, unregisterGlobal = () => {},
    captureDeps, spawn, helperPath, storeFile, fs, windowFile, preloadFile, onAttention = () => {}, onStatus = () => {}, onDebug = () => {},
    now = Date.now, setTimer = setTimeout, clearTimer = clearTimeout, existsSync = () => true,
    collectTimeoutMs = 800, settleMs = 90, helperDebounceMs = Chord.DEFAULT_DEBOUNCE_MS,
  } = deps;

  let win = null, loaded = false;
  let store = Geometry.emptyStore();
  let target = null;                 // { display, rect, calibrated } of the last sketch (route display follows it)
  let drawing = false, calibrating = null, calibTimer = null;
  let sketchSeq = 0, awaitPlanUntil = 0, toggleEscape = false;
  let skinMsg = { skin: "cosmic", reduceMotion: false };
  let layoutMsg = null, modeMsg = "idle", submittedMsg = null, replay = [];
  let pendingCollect = null, pollTimer = null, visTimer = null, started = false;
  let helperStatus = { state: "idle", reason: "" };
  let fallbackOk = false, fallbackAccel = "";
  const model = RouteModel.createRouteModel({ now });
  const transcript = Payload.createTranscriptWindow({ windowMs: 10000 });
  const alive = () => Boolean(win && !win.isDestroyed());
  const settings = () => getSettings().sketch;
  const debug = message => onDebug(`[sketch] ${message}`);

  // ---- chord machine + helper ----------------------------------------------------------------
  const machine = Chord.createChordMachine({ debounceMs: helperDebounceMs, now, setTimer, clearTimer, onEvent: onMachineEvent });
  const helper = Helper.createHelperSupervisor({
    command: helperPath, spawn,
    onChord: direction => machine.chord(direction), onInterrupt: () => machine.interrupt(), onLost: reason => machine.reset(reason),
    onStatus: status => { helperStatus = status; onStatus(); }, now, setTimer, clearTimer,
  });

  // ---- window --------------------------------------------------------------------------------
  function send(channel, payload) { if (alive() && loaded && !win.webContents.isDestroyed()) win.webContents.send(channel, payload); }
  function flushState() {
    send(CHANNELS.skin, skinMsg);
    if (layoutMsg) send(CHANNELS.layout, layoutMsg);
    send(CHANNELS.mode, { mode: modeMsg });
    if (submittedMsg) send(CHANNELS.submitted, submittedMsg);
    for (const event of replay) send(CHANNELS.routeEvent, event);
  }
  function ensureWindow() {
    if (alive()) return win;
    loaded = false;
    win = new BrowserWindow({
      x: 0, y: 0, width: 100, height: 100, show: false, frame: false, transparent: true, resizable: false, movable: false, minimizable: false, maximizable: false, closable: false,
      fullscreenable: false, focusable: false, skipTaskbar: true, hasShadow: false, backgroundColor: "#00000000", enableLargerThanScreen: true, title: "voiStarcraft2 sketch",
      ...(process.platform === "darwin" ? { type: "panel" } : {}),
      webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, preload: preloadFile, backgroundThrottling: false },
    });
    win.setAlwaysOnTop(true, "screen-saver");
    win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true, skipTransformProcessType: true });
    win.setIgnoreMouseEvents(true, { forward: true });
    win.setFocusable(false);
    win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    win.webContents.on("will-navigate", event => event.preventDefault());
    win.webContents.on("did-finish-load", () => { loaded = true; flushState(); });
    win.on("closed", () => { win = null; loaded = false; });
    win.loadFile(windowFile);
    return win;
  }
  function placeWindow(bounds) {
    const want = { x: Math.round(bounds.x), y: Math.round(bounds.y), width: Math.round(bounds.w), height: Math.round(bounds.h) };
    for (let attempt = 0; attempt < 3; attempt++) {
      win.setBounds(want);
      const got = win.getBounds();
      if (got.x === want.x && got.y === want.y && got.width === want.width && got.height === want.height) return;
    }
  }
  function setIgnore(ignore) { if (alive()) { if (ignore) win.setIgnoreMouseEvents(true, { forward: true }); else win.setIgnoreMouseEvents(false); } }
  function showWindow() { if (alive() && !win.isVisible()) win.showInactive(); }
  function hideWindow() { if (alive() && win.isVisible()) win.hide(); }

  // The window as the route display: the minimap rect plus room for labels; click-through.
  function layoutDisplay() {
    if (!target) return;
    const frame = Geometry.windowFor(target.rect, target.display.bounds, DISPLAY_PAD);
    placeWindow(frame.bounds);
    layoutMsg = { mode: "display", ox: frame.ox, oy: frame.oy, w: target.rect.w, h: target.rect.h, width: frame.bounds.w, height: frame.bounds.h };
    send(CHANNELS.layout, layoutMsg);
  }
  function setMode(next) { modeMsg = next; send(CHANNELS.mode, { mode: next }); }
  function routeVisible() { const view = model.view(now()); return Boolean(view.route || view.cursor || view.strokes); }
  function scheduleVisibility() {
    if (visTimer) return;
    visTimer = setTimer(() => {
      visTimer = null;
      if (drawing || calibrating || !alive()) return;
      if (routeVisible()) scheduleVisibility(); else hideWindow();
    }, 500);
  }
  function showRouteDisplay() {
    if (!target || drawing || calibrating) return;
    ensureWindow(); layoutDisplay(); setIgnore(true); setMode("idle"); showWindow(); scheduleVisibility();
  }

  // ---- display / rect ------------------------------------------------------------------------
  function resolveTarget(display) { const { rect, calibrated } = Geometry.resolveRect(store, display); return { display, rect, calibrated }; }
  function cursorDisplay() { const list = getDisplays(); return Displays.pickDisplay(list, getCursor()); }
  const refreshTarget = () => { if (target) { const list = getDisplays(); const display = list.find(d => d.id === target.display.id); if (display) target = resolveTarget(display); } };

  // ---- drawing -------------------------------------------------------------------------------
  function onMachineEvent(event) {
    if (event.source === "toggle") {
      if (event.type === "start") { toggleEscape = registerGlobal("Escape", () => machine.cancel("esc")); } else if (toggleEscape) { unregisterGlobal("Escape"); toggleEscape = false; }
    }
    if (event.type === "start") beginDraw(event.source);
    else if (event.type === "end") finishDraw();
    else cancelDraw(event.reason || "cancel");
  }
  function beginDraw(source) {
    if (calibrating) { debug("chord ignored while calibrating"); return; }
    target = resolveTarget(cursorDisplay());
    drawing = true; sketchSeq += 1;
    model.clearSketch(); model.clearRoute(); replay = []; submittedMsg = null;
    ensureWindow();
    placeWindow(target.rect); // EXACTLY the minimap frame
    layoutMsg = { mode: "draw", ox: 0, oy: 0, w: target.rect.w, h: target.rect.h, width: target.rect.w, height: target.rect.h };
    send(CHANNELS.clear, { what: "all" });
    send(CHANNELS.layout, layoutMsg);
    setMode("draw");
    setIgnore(false);
    showWindow();
    debug(`draw start (${source}) on display ${target.display.id}`);
  }
  function cancelDraw(reason) {
    if (!drawing) return;
    drawing = false;
    setIgnore(true);
    send(CHANNELS.clear, { what: "all" });
    setMode("idle");
    layoutDisplay();
    pushHud({ type: "sketch-result", kind: "cancel", text: Messages.resultLine({ kind: "cancel", reason }), reason });
    onAttention();
    if (!routeVisible()) hideWindow();
    debug(`draw cancelled (${reason})`);
  }
  function collectStrokes() {
    return new Promise(resolve => {
      const timer = setTimer(() => { if (pendingCollect && pendingCollect.resolve === resolve) pendingCollect = null; resolve(null); }, collectTimeoutMs);
      pendingCollect = { resolve: strokes => { clearTimer(timer); resolve(strokes); } };
      send(CHANNELS.collect, {});
    });
  }
  async function finishDraw() {
    if (!drawing) return;
    drawing = false;
    const seq = sketchSeq, snapshot = target;
    setIgnore(true); // the chord is released: the surface stops taking the mouse immediately
    const strokes = await collectStrokes();
    if (!strokes || !strokes.length) {
      send(CHANNELS.clear, { what: "all" }); setMode("idle"); layoutDisplay();
      pushHud({ type: "sketch-result", kind: "none", text: Messages.resultLine({ kind: "no-strokes" }) });
      onAttention();
      if (!routeVisible()) hideWindow();
      return;
    }
    let checked;
    try { checked = Payload.checkStrokes(strokes); } catch (error) {
      send(CHANNELS.clear, { what: "all" }); setMode("idle");
      pushHud({ type: "sketch-result", kind: "failed", text: Messages.resultLine({ kind: "failed" }), error: String(error.message).slice(0, 120) });
      onAttention(); return;
    }
    const updateId = Payload.newUpdateId();
    model.setSketch(updateId, checked);
    submittedMsg = { planId: updateId, strokes: checked, bright: true };
    layoutDisplay(); setMode("idle"); send(CHANNELS.submitted, submittedMsg);
    pushHud({ type: "sketch-result", kind: "sent", text: Messages.resultLine({ kind: "sent" }), planId: updateId });
    await new Promise(resolve => setTimer(resolve, settleMs)); // let the strokes paint in the padded layout before the screenshot
    let image = { imagePngB64: null, reason: "disabled" };
    if (settings().captureImage) image = await Capture.captureMinimap(captureDeps(), snapshot.display, snapshot.rect);
    submittedMsg = { planId: updateId, strokes: checked, bright: false };
    send(CHANNELS.submitted, submittedMsg);
    let body;
    try { body = Payload.buildPayload({ updateId, strokes: checked, imagePngB64: image.imagePngB64, transcript: transcript.text(now()), rect: snapshot.rect }); } catch (error) {
      settle("none", seq, { type: "sketch-result", kind: "failed", text: Messages.resultLine({ kind: "failed" }), error: String(error.message).slice(0, 120) }); return;
    }
    let response;
    try { response = await requestJson("/api/route/sketch", { method: "POST", body: JSON.stringify(body), timeoutMs: 30000 }); } catch (error) {
      settle("none", seq, { type: "sketch-result", kind: "failed", text: Messages.resultLine({ kind: "failed" }), error: String((error && error.message) || error).slice(0, 160) }); return;
    }
    const result = Payload.interpretResponse(response);
    debug(`answer ${result.kind} image=${image.imagePngB64 ? "yes" : image.reason}`);
    if (result.kind === "execute") awaitPlanUntil = now() + AWAIT_PLAN_MS;
    settle(result.kind === "execute" ? "execute" : result.kind === "ask" ? "ask" : "none", seq, { type: "sketch-result", kind: result.kind, text: result.text, planId: result.planId, squad: result.squad, image: image.imagePngB64 ? "sent" : image.reason });
    if (result.kind === "execute") feedPoll();
  }
  function settle(kind, seq, hud) {
    pushHud(hud); onAttention();
    if (seq !== sketchSeq || drawing) return; // a newer sketch started meanwhile: do not touch its surface
    model.settleSketch(kind); send(CHANNELS.settle, { kind });
    showRouteDisplay();
  }

  // ---- renderer -> main --------------------------------------------------------------------------
  function ownsWebContents(contents) { return alive() && win.webContents === contents; }
  function handleStrokes(input) {
    if (!pendingCollect) return;
    const pending = pendingCollect; pendingCollect = null;
    const strokes = input && Array.isArray(input.strokes) ? input.strokes : [];
    pending.resolve(strokes);
  }
  function handleCancel(reason) { machine.cancel(reason === "tap" ? "tap" : "esc"); }

  // ---- calibration -----------------------------------------------------------------------------
  function startCalibration() {
    if (drawing || calibrating) return false;
    const display = cursorDisplay();
    const resolved = resolveTarget(display);
    calibrating = { display, rect: resolved.rect };
    ensureWindow();
    placeWindow({ x: display.bounds.x, y: display.bounds.y, w: display.bounds.width, h: display.bounds.height });
    layoutMsg = { mode: "calibrate", ox: 0, oy: 0, w: display.bounds.width, h: display.bounds.height, width: display.bounds.width, height: display.bounds.height, origin: { x: display.bounds.x, y: display.bounds.y }, rect: resolved.rect, defaultRect: Geometry.defaultMinimapRect(display) };
    send(CHANNELS.clear, { what: "all" }); send(CHANNELS.layout, layoutMsg); setMode("calibrate");
    setIgnore(false); showWindow();
    registerGlobal("Escape", () => endCalibration("cancel"));
    calibTimer = setTimer(() => endCalibration("cancel"), CALIBRATION_TIMEOUT_MS);
    onStatus();
    return true;
  }
  function endCalibration(action, rect) {
    if (!calibrating) return;
    const { display } = calibrating;
    calibrating = null;
    if (calibTimer) { clearTimer(calibTimer); calibTimer = null; }
    unregisterGlobal("Escape");
    if (action === "save" && rect) {
      const fitted = Geometry.fitRect(rect, display.bounds);
      store = Geometry.saveRect(store, display, fitted);
      persist();
    }
    target = resolveTarget(display);
    setIgnore(true); setMode("idle"); layoutDisplay();
    if (!routeVisible()) hideWindow();
    onStatus();
  }
  function handleCalibration(input) {
    if (!calibrating || !input) return;
    if (input.action === "save") endCalibration("save", input.rect);
    else endCalibration("cancel");
  }
  function resetCalibration() {
    const display = calibrating ? calibrating.display : cursorDisplay();
    store = Geometry.clearRect(store, display); persist();
    if (calibrating) endCalibration("cancel");
    refreshTarget(); onStatus();
  }
  function persist() { try { fs.mkdirSync(path.dirname(storeFile), { recursive: true }); fs.writeFileSync(storeFile, JSON.stringify(store)); } catch (error) { debug(`store: ${error.message}`); } }
  function loadStore() { try { store = Geometry.parseStore(fs.readFileSync(storeFile, "utf8")); } catch { store = Geometry.emptyStore(); } }

  // ---- route feed -------------------------------------------------------------------------------
  const feed = Feed.createRouteFeed({
    requestJson,
    onEvents: events => { for (const event of events) ingest(event); },
    onReset: () => { model.resetSeq(); send(CHANNELS.routeReset, {}); pushHud({ type: "route-reset" }); },
  });
  function ingest(event) {
    model.ingest(event);
    if (event.kind === "route_plan") { replay = [event]; submittedMsg = null; } else replay.push(event);
    if (replay.length > 200) replay.splice(1, replay.length - 150);
    if (!target) target = resolveTarget(cursorDisplay());
    send(CHANNELS.routeEvent, event);
    pushHud({ type: "route-event", event });
    showRouteDisplay();
    if (event.kind === "route_plan") onAttention();
  }
  async function feedPoll() { try { await feed.poll(); } catch { /* feed never throws; defensive */ } }
  function loop() {
    pollTimer = setTimer(async () => {
      pollTimer = null;
      if (!started || !settings().enabled) return;
      await feedPoll();
      loop();
    }, feed.nextDelay({ active: model.isActive() || now() < awaitPlanUntil || drawing }));
  }

  // ---- lifecycle / settings ---------------------------------------------------------------------
  function configure() {
    const s = settings();
    skinMsg = { skin: getSettings().glass.skin, reduceMotion: getSettings().glass.reduceMotion };
    send(CHANNELS.skin, skinMsg);
    machine.setEnabled(s.enabled);
    if (fallbackAccel && fallbackAccel !== s.fallbackShortcut) { unregisterGlobal(fallbackAccel); fallbackAccel = ""; fallbackOk = false; }
    if (s.enabled && !fallbackAccel) { fallbackAccel = s.fallbackShortcut; fallbackOk = registerGlobal(fallbackAccel, () => machine.toggle()); if (!fallbackOk) fallbackAccel = ""; }
    if (!s.enabled && fallbackAccel) { unregisterGlobal(fallbackAccel); fallbackAccel = ""; fallbackOk = false; }
    if (s.enabled && started) { if (existsSync(helperPath)) helper.start(); else { helperStatus = { state: "missing", reason: "" }; } if (!pollTimer) loop(); }
    if (!s.enabled) { helper.stop(); if (pollTimer) { clearTimer(pollTimer); pollTimer = null; } if (!calibrating) hideWindow(); }
    onStatus();
  }
  function start() {
    started = true; loadStore();
    configure();
  }
  function stop() {
    started = false;
    helper.stop();
    if (pollTimer) { clearTimer(pollTimer); pollTimer = null; }
    if (visTimer) { clearTimer(visTimer); visTimer = null; }
    if (calibTimer) { clearTimer(calibTimer); calibTimer = null; }
    if (fallbackAccel) { unregisterGlobal(fallbackAccel); fallbackAccel = ""; }
    if (alive()) win.destroy();
  }
  function onDisplaysChanged() {
    refreshTarget();
    if (alive() && win.isVisible() && !drawing && !calibrating) layoutDisplay();
  }

  function statusSnapshot() {
    const s = settings();
    const display = target ? target.display : cursorDisplay();
    const resolved = resolveTarget(display);
    const permission = captureDeps().getAccess();
    return {
      enabled: s.enabled, active: machine.isActive(), calibrating: Boolean(calibrating),
      helper: { ...helperStatus, ...Messages.helperMessage(helperStatus, s.fallbackShortcut) },
      fallback: { accelerator: s.fallbackShortcut, registered: fallbackOk },
      capture: { permission, enabled: s.captureImage, ...Messages.captureMessage({ permission, enabled: s.captureImage }) },
      calibration: { calibrated: resolved.calibrated, rect: resolved.rect, displayKey: Geometry.displayKey(display), ...Messages.calibrationMessage({ calibrated: resolved.calibrated }) },
    };
  }

  return {
    start, stop, configure, noteTranscript: (turn, text) => transcript.push(turn, text, now()),
    handleStrokes, handleCancel, handleCalibration, ownsWebContents, startCalibration, resetCalibration,
    retryHelper: () => helper.retry(), onDisplaysChanged, statusSnapshot, getWindow: () => (alive() ? win : null),
    _test: { machine, helper, model, feed, feedPoll, state: () => ({ drawing, calibrating: Boolean(calibrating), target, loaded }), markLoaded: () => { loaded = true; flushState(); } },
  };
}

module.exports = { createSketchController, CHANNELS, SKETCH_CSP, DISPLAY_PAD, CANCEL_REASONS };
