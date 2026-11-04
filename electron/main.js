"use strict";
const { app, BrowserWindow, ipcMain, screen, globalShortcut, session: electronSession } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const WebSocket = require("ws");
const {
  validateCommand, validateBaseUrl, validateCursor, validateTargetPin,
  validateVoiceControl, validateVoiceAudio, validateVoiceLevel, validateMicError, validateOverlayState, overlayStateForVoice,
  validateDockMode, validateDockPin, validateDockFocus, validateDockRect, validateDockAttention, DOCK_MODES,
  validateGlass, nudgeGlassAlpha, sanitizeStoredGlass, GLASS_LIMITS,
} = require("./protocol.js");
const { createVoiceClient } = require("./voice.js");
const { layoutFor, createDockController, inside } = require("./dock_state.js");

const BASE_URL = validateBaseUrl(process.env.VOI_COMMANDER_URL || "http://127.0.0.1:8765");
const rendererFile = path.join(__dirname, "renderer", "index.html");
const overlayFile = path.join(__dirname, "overlay", "index.html");
const RENDERER_CSP = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'none'; media-src 'none'; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'";
const OVERLAY_CSP = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'none'; base-uri 'none'; form-action 'none'";
const CHANNELS = Object.freeze({
  status: "commander:status", runtimeStatus: "commander:runtime-status", command: "commander:command", capabilities: "commander:capabilities",
  registry: "commander:registry", targetPin: "commander:target-pin", events: "commander:events",
  voiceStart: "voice:start", voiceStop: "voice:stop", voiceState: "voice:state", voiceAudio: "voice:audio", voiceMicError: "voice:mic-error", voiceLevel: "voice:level",
  dockState: "dock:state", dockMode: "dock:mode", dockPin: "dock:pin", dockGlass: "dock:glass", dockFocus: "dock:focus", dockRect: "dock:rect", dockAttention: "dock:attention",
  push: "commander:push", overlayState: "overlay:state", overlayLevel: "overlay:level",
});
const POLL_MS = 50;
const ATTENTION_PULSE_MS = 400; // one attention pulse; the dock controller then holds the bar open for its 2.5 s idle grace
const SHORTCUT = "CommandOrControl+Shift+Space";
const SHORTCUT_OPACITY_UP = "CommandOrControl+Alt+Up";
const SHORTCUT_OPACITY_DOWN = "CommandOrControl+Alt+Down";
const FORCE_VOICE_STATES = new Set(["listening", "thinking", "acting"]);

let mainWindow = null;
let overlayWindow = null;
let overlayHideTimer = null;
let overlayState = "off";
let voice = null;
let prefs = { mode: "bottom", pinned: false, glass: sanitizeStoredGlass(null) };
let prefsFile = "";
let controller = createDockController();
let dockDisplayId = null;
let panelRect = null; // window-relative rect of the glass panel as laid out by the renderer
let revealed = false;
let ignoring = true; // current setIgnoreMouseEvents state (true = click-through)
let windowFocusable = false;
let shortcutOpen = false;
let attentionUntil = 0;
let pollTimer = null;
let fakeCursor = null; // test hook only; never set in production
let lastDockPush = "";
let currentLayout = null;

async function requestJson(pathname, options = {}) {
  const response = await fetch(new URL(pathname, BASE_URL), {
    ...options, redirect: "error", signal: AbortSignal.timeout(options.timeoutMs || 15000),
    headers: { accept: "application/json", "content-type": "application/json", ...(process.env.VOI_COMMANDER_TOKEN ? { Authorization: `Bearer ${process.env.VOI_COMMANDER_TOKEN}` } : {}) },
  });
  let payload;
  try { payload = await response.json(); } catch { payload = {}; }
  if (!response.ok) throw new Error((payload && (payload.error || payload.refusal_reason)) || `Commander HTTP ${response.status}`);
  return payload;
}

function trustedSender(event) {
  return Boolean(event.senderFrame) && event.senderFrame === event.sender.mainFrame && event.senderFrame.url === pathToFileURL(rendererFile).href
    && mainWindow && !mainWindow.isDestroyed() && event.sender === mainWindow.webContents;
}
function pushToRenderer(payload) {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send(CHANNELS.push, payload);
}

// ---- persisted prefs -----------------------------------------------------------------------
function loadPrefs() {
  prefsFile = path.join(app.getPath("userData"), "dock-prefs.json");
  try {
    const raw = JSON.parse(fs.readFileSync(prefsFile, "utf8"));
    if (DOCK_MODES.includes(raw.mode)) prefs.mode = raw.mode;
    if (typeof raw.pinned === "boolean") prefs.pinned = raw.pinned;
    prefs.glass = sanitizeStoredGlass(raw.glass);
  } catch { /* first run or unreadable: defaults */ }
}
function savePrefs() {
  try { fs.mkdirSync(path.dirname(prefsFile), { recursive: true }); fs.writeFileSync(prefsFile, JSON.stringify(prefs)); } catch (error) { if (process.env.VOI_DEBUG) console.log(`[prefs] ${error.message}`); }
}

// ---- displays / geometry -------------------------------------------------------------------
const displayList = () => screen.getAllDisplays().map(display => ({ id: display.id, bounds: display.bounds }));
const displayById = id => screen.getAllDisplays().find(display => display.id === id) || screen.getPrimaryDisplay();
function currentCursor() { return fakeCursor || screen.getCursorScreenPoint(); }

function placeDock(displayId) {
  dockDisplayId = displayId;
  const display = displayById(displayId);
  currentLayout = layoutFor(prefs.mode, display.bounds, { workArea: display.workArea });
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.setBounds(currentLayout.window, false);
  followOverlay();
  pushDock(true);
}
function panelScreenRect() {
  if (!mainWindow || mainWindow.isDestroyed() || !currentLayout) return null;
  const w = currentLayout.window;
  if (!panelRect) return w;
  return { x: w.x + panelRect.x, y: w.y + panelRect.y, width: panelRect.width, height: panelRect.height };
}

// ---- rainbow screen-edge overlay ------------------------------------------------------------
function createOverlayWindow() {
  const display = displayById(dockDisplayId);
  const win = new BrowserWindow({
    ...display.bounds, show: false, frame: false, transparent: true, resizable: false, movable: false, minimizable: false, maximizable: false, closable: false,
    fullscreenable: false, focusable: false, skipTaskbar: true, hasShadow: false, backgroundColor: "#00000000", enableLargerThanScreen: true,
    ...(process.platform === "darwin" ? { type: "panel" } : {}),
    webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, preload: path.join(__dirname, "overlay", "preload.js"), backgroundThrottling: false },
  });
  win.setAlwaysOnTop(true, "screen-saver");
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true, skipTransformProcessType: true });
  win.setIgnoreMouseEvents(true, { forward: true });
  win.setFocusable(false);
  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  win.webContents.on("will-navigate", event => event.preventDefault());
  win.loadFile(overlayFile);
  return win;
}
function followOverlay() {
  if (!overlayWindow || overlayWindow.isDestroyed()) return;
  const target = displayById(dockDisplayId).bounds;
  const bounds = overlayWindow.getBounds();
  if (bounds.x !== target.x || bounds.y !== target.y || bounds.width !== target.width || bounds.height !== target.height) overlayWindow.setBounds(target);
}
function setOverlay(next) {
  const { state } = validateOverlayState({ state: next });
  if (state === overlayState) return;
  overlayState = state;
  if (!overlayWindow || overlayWindow.isDestroyed()) return;
  if (overlayHideTimer) { clearTimeout(overlayHideTimer); overlayHideTimer = null; }
  if (state !== "off") {
    followOverlay();
    if (!overlayWindow.isVisible()) overlayWindow.showInactive();
    overlayWindow.setAlwaysOnTop(true, "screen-saver");
  } else {
    overlayHideTimer = setTimeout(() => { overlayHideTimer = null; if (overlayState === "off" && overlayWindow && !overlayWindow.isDestroyed()) overlayWindow.hide(); }, 400);
  }
  overlayWindow.webContents.send(CHANNELS.overlayState, { state });
}

// ---- voice ---------------------------------------------------------------------------------
function createVoice() {
  return createVoiceClient({
    requestJson, WebSocketImpl: WebSocket,
    onState: snapshot => {
      if (snapshot.state === "error") attentionUntil = Date.now() + ATTENTION_PULSE_MS;
      setOverlay(overlayStateForVoice(snapshot));
      pushToRenderer({ type: "voice-state", ...snapshot });
    },
    onTranscript: transcript => pushToRenderer({ type: "transcript", ...transcript }),
    onIntent: intent => pushToRenderer({ type: "intent", ...intent }),
    onLog: message => { if (process.env.VOI_DEBUG) console.log(`[voice] ${message}`); },
  });
}

// ---- dock: reveal / hide / interactivity / focus ---------------------------------------------
function dockSnapshot() {
  return { revealed, mode: prefs.mode, pinned: prefs.pinned, shortcutOpen, reason: lastReason, glass: { ...prefs.glass }, vibrancySupported: process.platform === "darwin" };
}
let lastReason = "";
function pushDock(force) {
  const snapshot = dockSnapshot();
  const key = JSON.stringify(snapshot);
  if (!force && key === lastDockPush) return;
  lastDockPush = key;
  pushToRenderer({ type: "dock-state", ...snapshot });
}
function setIgnore(next) {
  if (!mainWindow || mainWindow.isDestroyed() || next === ignoring) return;
  ignoring = next;
  if (next) mainWindow.setIgnoreMouseEvents(true, { forward: true }); else mainWindow.setIgnoreMouseEvents(false);
}
function setFocusable(next) {
  if (!mainWindow || mainWindow.isDestroyed() || next === windowFocusable) return;
  windowFocusable = next;
  mainWindow.setFocusable(next);
}
function releaseFocus() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  if (mainWindow.isFocused()) mainWindow.blur();
  setFocusable(false);
  if (process.platform === "darwin" && !app.isHidden()) { /* focusable:false hands key status back to the previously active app */ }
}
function focusDock() {
  if (!mainWindow || mainWindow.isDestroyed() || !revealed) return false;
  setFocusable(true);
  mainWindow.focus();
  mainWindow.webContents.focus();
  return true;
}
function forceOpen(now) {
  const voiceState = voice ? voice.snapshot() : null;
  return Boolean((voiceState && voiceState.active && FORCE_VOICE_STATES.has(voiceState.state)) || now < attentionUntil || shortcutOpen);
}
function tick() {
  if (!mainWindow || mainWindow.isDestroyed()) return null;
  const now = Date.now();
  const cursor = currentCursor();
  const decision = controller.update({
    cursor, displays: displayList(), dockDisplayId, mode: prefs.mode, panelRect: revealed ? panelScreenRect() : null,
    forceOpen: forceOpen(now), pinned: prefs.pinned, now,
  });
  lastReason = decision.reason;
  if (decision.moveToDisplayId !== null && !revealed) placeDock(decision.moveToDisplayId);
  if (decision.changed) {
    revealed = decision.revealed;
    if (revealed) { if (!mainWindow.isVisible()) mainWindow.showInactive(); } else { releaseFocus(); shortcutOpen = false; }
    pushDock(true);
  }
  // Interactive only where the glass panel actually is; everything else stays click-through to the game.
  const panel = panelScreenRect();
  const overPanel = revealed && panel && inside(cursor, panel);
  setIgnore(!overPanel);
  return decision;
}
function startPolling() { if (!pollTimer) pollTimer = setInterval(tick, POLL_MS); }
function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

// Real behind-window blur is an OS feature (CSS backdrop-filter only blurs content inside the window), so it is an opt-in toggle.
function applyVibrancy() {
  if (process.platform !== "darwin" || !mainWindow || mainWindow.isDestroyed()) return;
  try { mainWindow.setVibrancy(prefs.glass.vibrancy ? "under-window" : null); } catch (error) { if (process.env.VOI_DEBUG) console.log(`[vibrancy] ${error.message}`); }
}
function setGlass(patch) {
  prefs.glass = { ...prefs.glass, ...patch };
  savePrefs();
  if ("vibrancy" in patch) applyVibrancy();
  pushDock(true);
  return dockSnapshot();
}
function nudgeGlass(direction) { return setGlass({ alpha: nudgeGlassAlpha(prefs.glass.alpha, direction) }); }
function setMode(mode) {
  if (prefs.mode === mode) return;
  prefs.mode = mode; savePrefs();
  panelRect = null; // renderer re-reports after its relayout
  placeDock(dockDisplayId);
}
function toggleShortcut() {
  if (!shortcutOpen) {
    shortcutOpen = true;
    tick();
    voice.start().catch(() => {});
    focusDock();
  } else {
    shortcutOpen = false;
    voice.stop();
    tick();
  }
  pushDock(true);
}

// ---- IPC -----------------------------------------------------------------------------------
function registerIpc() {
  const handle = (channel, callback) => ipcMain.handle(channel, (event, input) => {
    if (!trustedSender(event)) throw new Error("Untrusted IPC sender");
    return callback(input);
  });
  const listen = (channel, callback) => ipcMain.on(channel, (event, input) => {
    if (!trustedSender(event)) return;
    try { callback(input); } catch (error) { if (process.env.VOI_DEBUG) console.log(`[ipc ${channel}] ${error.message}`); }
  });
  handle(CHANNELS.status, () => requestJson("/api/micromachine/status"));
  handle(CHANNELS.runtimeStatus, () => requestJson("/api/runtime/status"));
  handle(CHANNELS.capabilities, () => requestJson("/api/direct/capabilities"));
  handle(CHANNELS.registry, () => requestJson("/api/direct/registry"));
  handle(CHANNELS.targetPin, input => requestJson("/api/direct/registry/target-pin", { method: "POST", body: JSON.stringify(validateTargetPin(input)) }));
  handle(CHANNELS.events, input => requestJson(`/api/history?after=${validateCursor(input && input.after)}`));
  handle(CHANNELS.command, input => requestJson("/api/micromachine/modulate", { method: "POST", body: JSON.stringify({ ...validateCommand(input), async_publish: true }) }));
  handle(CHANNELS.voiceStart, async input => { validateVoiceControl(input); return voice.start(); });
  handle(CHANNELS.voiceStop, input => { validateVoiceControl(input); shortcutOpen = false; return voice.stop(); });
  handle(CHANNELS.voiceState, () => voice.snapshot());
  handle(CHANNELS.dockState, () => dockSnapshot());
  handle(CHANNELS.dockMode, input => { setMode(validateDockMode(input).mode); return dockSnapshot(); });
  handle(CHANNELS.dockGlass, input => setGlass(validateGlass(input)));
  handle(CHANNELS.dockPin, input => { prefs.pinned = validateDockPin(input).pinned; savePrefs(); tick(); pushDock(true); return dockSnapshot(); });
  handle(CHANNELS.dockFocus, input => { const { focus } = validateDockFocus(input); if (focus) return { focused: focusDock() }; releaseFocus(); return { focused: false }; });
  listen(CHANNELS.dockRect, input => { panelRect = validateDockRect(input); });
  listen(CHANNELS.dockAttention, input => { validateDockAttention(input); attentionUntil = Date.now() + ATTENTION_PULSE_MS; });
  listen(CHANNELS.voiceAudio, input => { voice.pushAudio(validateVoiceAudio(input)); });
  listen(CHANNELS.voiceLevel, input => {
    const level = validateVoiceLevel(input);
    if (overlayState === "listening" && overlayWindow && !overlayWindow.isDestroyed()) overlayWindow.webContents.send(CHANNELS.overlayLevel, level);
  });
  listen(CHANNELS.voiceMicError, input => { voice.micFailed(validateMicError(input).reason); });
}

function installPermissions(ses) {
  const mainContentsId = () => (mainWindow && !mainWindow.isDestroyed() ? mainWindow.webContents.id : -1);
  const audioOnly = details => !details || !Array.isArray(details.mediaTypes) || (details.mediaTypes.length > 0 && details.mediaTypes.every(type => type === "audio"));
  ses.setPermissionRequestHandler((contents, permission, callback, details) => {
    callback(Boolean(contents && contents.id === mainContentsId() && permission === "media" && audioOnly(details) && (!details || !details.requestingUrl || details.requestingUrl.startsWith("file://"))));
  });
  ses.setPermissionCheckHandler((contents, permission, _origin, details) => Boolean(contents && contents.id === mainContentsId() && permission === "media" && (!details || details.mediaType === undefined || details.mediaType === "audio" || details.mediaType === "unknown")));
  ses.setDevicePermissionHandler(() => false);
  ses.webRequest.onHeadersReceived((details, callback) => {
    const isOverlay = overlayWindow && !overlayWindow.isDestroyed() && details.webContentsId === overlayWindow.webContents.id;
    callback({ responseHeaders: { ...details.responseHeaders, "Content-Security-Policy": [isOverlay ? OVERLAY_CSP : RENDERER_CSP] } });
  });
}

function createDockWindow() {
  const display = displayById(dockDisplayId);
  currentLayout = layoutFor(prefs.mode, display.bounds, { workArea: display.workArea });
  mainWindow = new BrowserWindow({
    ...currentLayout.window, show: false, frame: false, transparent: true, hasShadow: false, backgroundColor: "#00000000",
    resizable: false, movable: false, minimizable: false, maximizable: false, fullscreenable: false, closable: false,
    skipTaskbar: true, focusable: false, enableLargerThanScreen: true, title: "voiStarcraft2",
    ...(process.platform === "darwin" ? { type: "panel" } : {}),
    webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, preload: path.join(__dirname, "preload.js"), backgroundThrottling: false },
  });
  applyVibrancy();
  mainWindow.setAlwaysOnTop(true, "screen-saver");
  mainWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true, skipTransformProcessType: true });
  mainWindow.setIgnoreMouseEvents(true, { forward: true });
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  mainWindow.webContents.on("will-navigate", event => event.preventDefault());
  mainWindow.webContents.once("did-finish-load", () => { mainWindow.showInactive(); pushDock(true); });
  mainWindow.on("closed", () => { mainWindow = null; });
  mainWindow.loadFile(rendererFile);
  return mainWindow;
}

function onDisplaysChanged() {
  const displays = screen.getAllDisplays();
  if (!displays.some(display => display.id === dockDisplayId)) dockDisplayId = screen.getPrimaryDisplay().id;
  placeDock(dockDisplayId);
}

app.whenReady().then(() => {
  installPermissions(electronSession.defaultSession);
  loadPrefs();
  dockDisplayId = screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).id;
  voice = createVoice();
  registerIpc();
  createDockWindow();
  overlayWindow = createOverlayWindow();
  for (const eventName of ["display-added", "display-removed", "display-metrics-changed"]) screen.on(eventName, onDisplaysChanged);
  for (const [accelerator, callback] of [[SHORTCUT, toggleShortcut], [SHORTCUT_OPACITY_UP, () => nudgeGlass(1)], [SHORTCUT_OPACITY_DOWN, () => nudgeGlass(-1)]]) {
    if (!globalShortcut.register(accelerator, callback) && process.env.VOI_DEBUG) console.log(`[shortcut] could not register ${accelerator}`);
  }
  startPolling();
  app.on("activate", () => { startPolling(); });
});
app.on("window-all-closed", () => app.quit());
app.on("will-quit", () => { globalShortcut.unregisterAll(); stopPolling(); });
app.on("before-quit", () => { if (voice) voice.stop(); });

module.exports = {
  CHANNELS, SHORTCUT, SHORTCUT_OPACITY_UP, SHORTCUT_OPACITY_DOWN, validateCommand, requestJson,
  // Test/smoke hooks (read-only views of live state, plus a fake cursor).
  _internals: {
    getMain: () => mainWindow, getOverlay: () => overlayWindow, getVoice: () => voice, getOverlayState: () => overlayState,
    setFakeCursor: point => { fakeCursor = point; },
    tick,
    toggleShortcut, nudgeGlass, setGlass,
    dockInfo: () => ({ revealed, ignoring, focusable: windowFocusable, focused: Boolean(mainWindow && mainWindow.isFocused()), mode: prefs.mode, pinned: prefs.pinned, glass: prefs.glass, reason: lastReason, bounds: mainWindow && mainWindow.getBounds(), panelRect, layout: currentLayout, shortcutOpen }),
    setOverlay,
  },
};
