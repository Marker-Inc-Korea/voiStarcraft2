"use strict";
const { app, BrowserWindow, ipcMain, screen, globalShortcut, session: electronSession } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const WebSocket = require("ws");
const P = require("./protocol.js");
const Settings = require("./renderer/settings.js");
const { createVoiceClient } = require("./voice.js");
const { layoutFor, createDockController, inside } = require("./dock_state.js");
const Displays = require("./displays.js");
const HudShape = require("./renderer/hud_shape.js");
const Skins = require("./renderer/skins.js");
const { createShortcutRegistry, createEmergencyShortcuts } = require("./shortcuts.js");

const ENV_URL = P.validateBaseUrl(process.env.VOI_COMMANDER_URL || "http://127.0.0.1:8765");
let baseUrl = ENV_URL;
const rendererFile = path.join(__dirname, "renderer", "index.html");
const overlayFile = path.join(__dirname, "overlay", "index.html");
const RENDERER_CSP = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'none'; media-src 'none'; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'";
const OVERLAY_CSP = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'none'; base-uri 'none'; form-action 'none'";
const CHANNELS = Object.freeze({
  status: "commander:status", runtimeStatus: "commander:runtime-status", command: "commander:command", capabilities: "commander:capabilities",
  registry: "commander:registry", targetPin: "commander:target-pin", events: "commander:events",
  voiceStart: "voice:start", voiceStop: "voice:stop", voiceState: "voice:state", voiceAudio: "voice:audio", voiceMicError: "voice:mic-error", voiceLevel: "voice:level", voicePtt: "voice:ptt",
  settingsGet: "settings:get", settingsSet: "settings:set", settingsReset: "settings:reset", settingsOpen: "settings:open", serverProbe: "server:probe", shortcutCheck: "shortcut:check", rainbowPreview: "rainbow:preview",
  dockState: "dock:state", dockFocus: "dock:focus", dockRect: "dock:rect", dockAttention: "dock:attention", emergency: "commander:emergency",
  push: "commander:push", overlayState: "overlay:state", overlayLevel: "overlay:level", overlaySettings: "overlay:settings",
});
const POLL_MS = 50;
const ATTENTION_PULSE_MS = 400; // one attention pulse; the dock controller then holds the bar open for its 2.5 s idle grace
const PREVIEW_MS = 2000;
const SHORTCUT_OPACITY_UP = "CommandOrControl+Alt+Up";
const SHORTCUT_OPACITY_DOWN = "CommandOrControl+Alt+Down";
const SHORTCUT_SETTINGS = "CommandOrControl+,";
const FORCE_VOICE_STATES = new Set(["listening", "thinking", "acting"]);

let mainWindow = null;
const overlays = new Map(); // display id -> overlay BrowserWindow (one per target display)
let follower = Displays.createFollower({ dwellMs: 250 });
let overlayHideTimer = null;
let overlayState = "off";
let voice = null;
let settings = Settings.defaults({ serverUrl: ENV_URL });
let settingsFile = "";
let saveTimer = null;
let controller = createDockController();
let dockDisplayId = null;
let panelRect = null; // window-relative rect of the glass panel as laid out by the renderer
let revealed = false;
let ignoring = true; // current setIgnoreMouseEvents state (true = click-through)
let windowFocusable = false;
let shortcutOpen = false;
let settingsOpen = false;
let attentionUntil = 0;
let previewUntil = 0;
let pollTimer = null;
let fakeCursor = null; // test hook only; never set in production
let lastDockPush = "";
let lastReason = "";
let currentLayout = null;
let lastSession = { model: "", tierHint: "" };
let shortcutStatus = { toggle: true, settings: true, emergency: { retreat: true, hold: true, stop: true } };
let pttToggled = false;

async function requestJson(pathname, options = {}) {
  const response = await fetch(new URL(pathname, baseUrl), {
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

// ---- persisted settings --------------------------------------------------------------------
function loadSettings() {
  settingsFile = path.join(app.getPath("userData"), "settings.json");
  let raw = null;
  try { raw = JSON.parse(fs.readFileSync(settingsFile, "utf8")); } catch { /* first run or unreadable: defaults */ }
  settings = Settings.sanitizeStored(raw, { serverUrl: ENV_URL });
  baseUrl = settings.server.url === Settings.defaults({ serverUrl: ENV_URL }).server.url && !raw ? ENV_URL : settings.server.url;
}
function saveSettingsSoon() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveSettingsNow, 200);
}
function saveSettingsNow() {
  clearTimeout(saveTimer);
  try { fs.mkdirSync(path.dirname(settingsFile), { recursive: true }); fs.writeFileSync(settingsFile, JSON.stringify(settings)); } catch (error) { if (process.env.VOI_DEBUG) console.log(`[settings] ${error.message}`); }
}
function displayChoices() {
  return screen.getAllDisplays().map((display, index) => ({ id: display.id, label: `${index + 1}`, primary: display.id === screen.getPrimaryDisplay().id }));
}
function settingsSnapshot() {
  return { settings, status: { shortcut: shortcutStatus, displays: displayChoices(), model: lastSession.model, tierHint: lastSession.tierHint, platform: process.platform, vibrancySupported: process.platform === "darwin", baseUrl } };
}
function broadcastSettings() {
  pushToRenderer({ type: "settings", ...settingsSnapshot() });
  sendOverlaySettings();
}
function dockOptions() {
  return {
    autoHide: settings.layout.autoHide, hideDelayMs: settings.layout.hideDelay, zonePx: settings.layout.zone,
    widthScale: settings.layout.width, heightScale: settings.layout.height, margin: settings.layout.margin,
    followCursor: settings.layout.display === "cursor",
  };
}

// Applies a validated patch: persist, re-layout, re-register shortcuts, reconfigure voice, broadcast to bar + overlay.
function applySettings(patch) {
  const before = settings;
  settings = Settings.merge(settings, patch);
  const changed = group => Boolean(patch[group]);
  if (changed("glass") && "vibrancy" in patch.glass) applyVibrancy();
  if (changed("layout")) {
    if ("display" in patch.layout) { dockDisplayId = resolveDockDisplay(); }
    placeDock(dockDisplayId, "settings");
  }
  if (changed("server") && settings.server.url !== before.server.url) { baseUrl = settings.server.url; if (voice && voice.snapshot().active) voice.stop(); }
  if (changed("emergency")) registerEmergencyShortcuts();
  if (changed("voice")) {
    if ("shortcut" in patch.voice) registerToggleShortcut();
    if (voice) voice.reconfigure();
    if ("mode" in patch.voice) pttToggled = false;
  }
  if (changed("rainbow") && "screen" in patch.rainbow) syncOverlays();
  if (changed("rainbow") || changed("glass")) { updateOverlay(); sendOverlaySettings(); }
  saveSettingsSoon();
  broadcastSettings();
  return settingsSnapshot();
}

// ---- displays / geometry -------------------------------------------------------------------
// Everything is in Electron's global DIP space; displays may have negative origins, mixed scale factors and portrait
// orientation. Selection/geometry is the pure displays.js module; nothing here assumes primary or (0,0).
const displayList = () => screen.getAllDisplays().map(display => ({ id: display.id, bounds: display.bounds, workArea: display.workArea, scaleFactor: display.scaleFactor }));
const displayById = id => displayList().find(display => display.id === id) || Displays.pickDisplay(displayList(), currentCursor());
function currentCursor() { return fakeCursor || screen.getCursorScreenPoint(); }
function resolveDockDisplay() { return Displays.resolveDockDisplay(displayList(), settings.layout.display, currentCursor()).id; }
function layoutOptions(display) {
  return { workArea: display.workArea, widthScale: settings.layout.width, heightScale: settings.layout.height, edgePx: settings.layout.zone, margin: settings.layout.margin };
}
const dockTrace = []; // last placements: why/where the dock window moved (diagnostics for multi-display issues)
function trace(event) { dockTrace.push({ t: Date.now(), ...event }); if (dockTrace.length > 60) dockTrace.shift(); }
function placeDock(displayId, why = "") {
  trace({ ev: "placeDock", from: dockDisplayId, to: displayId, why, cursor: currentCursor(), dock: settings.layout.dock });
  dockDisplayId = displayId;
  const display = displayById(displayId);
  currentLayout = layoutFor(settings.layout.dock, display.bounds, layoutOptions(display));
  if (mainWindow && !mainWindow.isDestroyed()) {
    applyBounds(mainWindow, currentLayout.window);
  }
  syncOverlays();
  pushDock(true);
}
// setBounds can land off by a rounding step when the window moves between displays of different scale factors;
// re-apply until the window is where we asked (bounded, no loop forever).
function applyBounds(win, want) {
  win.setBounds(want, false);
  for (let i = 0; i < 3; i += 1) {
    const got = win.getBounds();
    if (Displays.sameRect(got, want)) return true;
    win.setBounds(want, false);
  }
  return Displays.withinRounding(win.getBounds(), want, 1);
}
// The visible HUD is a trapezoid inside that rectangle (renderer/hud_shape.js, the SAME function the renderer clips with):
// pointer hit-testing uses the polygon, so the slanted corners and everything outside it stay click-through to the game.
function panelPolygon() {
  const rect = panelScreenRect();
  if (!rect) return null;
  const shape = HudShape.shape(settings.layout.dock, rect.width, rect.height, { slantDeg: settings.glass.slant, chamfer: Skins.chamferFor(settings.glass.skin) });
  return HudShape.translate(shape.points, rect.x, rect.y);
}
function pointerOverPanel(cursor) {
  const polygon = panelPolygon();
  return Boolean(polygon && HudShape.containsPoint(polygon, cursor.x, cursor.y));
}
function panelScreenRect() {
  if (!mainWindow || mainWindow.isDestroyed() || !currentLayout) return null;
  const w = currentLayout.window;
  if (!panelRect) return w;
  return { x: w.x + panelRect.x, y: w.y + panelRect.y, width: panelRect.width, height: panelRect.height };
}

// ---- rainbow screen-edge overlay ------------------------------------------------------------
// 무지개 > 화면: "current" = one overlay on the dock/cursor display, "all" = one overlay window per display.
function createOverlayWindow(display) {
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
  win.webContents.on("did-finish-load", () => { sendOverlaySettings(win); win.webContents.send(CHANNELS.overlayState, { state: overlayState }); });
  win.loadFile(overlayFile);
  return win;
}
const liveOverlays = () => [...overlays.values()].filter(win => !win.isDestroyed());
// Reconcile overlay windows with the displays that should carry one (create / move / destroy).
function syncOverlays() {
  const displays = displayList();
  const active = displays.find(display => display.id === dockDisplayId) || Displays.pickDisplay(displays, currentCursor());
  const targets = Displays.overlayTargets(displays, settings.rainbow.screen, active);
  const existing = new Map([...overlays].filter(([, win]) => !win.isDestroyed()).map(([id, win]) => [id, win.getBounds()]));
  const plan = Displays.planOverlays(existing, targets);
  for (const id of plan.destroy) { const win = overlays.get(id); if (win && !win.isDestroyed()) win.destroy(); overlays.delete(id); }
  for (const [id, win] of overlays) if (win.isDestroyed()) overlays.delete(id);
  for (const display of targets) {
    if (plan.create.includes(display.id)) overlays.set(display.id, createOverlayWindow(display));
    else if (plan.move.includes(display.id)) applyBounds(overlays.get(display.id), display.bounds);
    const win = overlays.get(display.id);
    if (win && overlayState !== "off" && !win.isVisible()) win.showInactive();
  }
}
function sendOverlaySettings(only) {
  const payload = { ...Settings.overlayView(settings), reduceMotion: settings.glass.reduceMotion };
  for (const win of only ? [only] : liveOverlays()) if (!win.isDestroyed() && !win.webContents.isLoading()) win.webContents.send(CHANNELS.overlaySettings, payload);
}
// The overlay state is derived (voice state, rainbow preferences, preview) so every input funnels through one place.
function desiredOverlayState() {
  const now = Date.now();
  if (now < previewUntil) return "listening";
  if (!settings.rainbow.enabled) return "off";
  const state = voice ? P.overlayStateForVoice(voice.snapshot()) : "off";
  if (state === "thinking" && !settings.rainbow.thinking) return "off";
  return state;
}
function updateOverlay() { setOverlay(desiredOverlayState()); }
function setOverlay(next) {
  const { state } = P.validateOverlayState({ state: next });
  if (state === overlayState) return;
  overlayState = state;
  if (overlayHideTimer) { clearTimeout(overlayHideTimer); overlayHideTimer = null; }
  if (state !== "off") {
    syncOverlays();
    for (const win of liveOverlays()) { if (!win.isVisible()) win.showInactive(); win.setAlwaysOnTop(true, "screen-saver"); }
  } else {
    overlayHideTimer = setTimeout(() => { overlayHideTimer = null; if (overlayState === "off") for (const win of liveOverlays()) win.hide(); }, 400);
  }
  for (const win of liveOverlays()) if (!win.webContents.isLoading()) win.webContents.send(CHANNELS.overlayState, { state });
}
function previewRainbow() {
  previewUntil = Date.now() + PREVIEW_MS;
  updateOverlay();
  setTimeout(updateOverlay, PREVIEW_MS + 60);
}

// ---- voice ---------------------------------------------------------------------------------
function createVoice() {
  return createVoiceClient({
    requestJson, WebSocketImpl: WebSocket, getPrefs: () => Settings.voiceView(settings),
    onState: snapshot => {
      if (snapshot.state === "error") attentionUntil = Date.now() + ATTENTION_PULSE_MS;
      updateOverlay();
      pushToRenderer({ type: "voice-state", ...snapshot });
    },
    onTranscript: transcript => pushToRenderer({ type: "transcript", ...transcript }),
    onIntent: intent => pushToRenderer({ type: "intent", ...intent }),
    onSession: info => { lastSession = info; pushToRenderer({ type: "session", ...info }); },
    onLog: message => { if (process.env.VOI_DEBUG) console.log(`[voice] ${message}`); },
  });
}

// ---- dock: reveal / hide / interactivity / focus ---------------------------------------------
function dockSnapshot() {
  return { revealed, dock: settings.layout.dock, shortcutOpen, settingsOpen, reason: lastReason };
}
function pushDock(force) {
  const snapshot = dockSnapshot();
  const key = JSON.stringify(snapshot);
  if (!force && key === lastDockPush) return;
  lastDockPush = key;
  pushToRenderer({ type: "dock-state", ...snapshot });
}
// Real behind-window blur is an OS feature (CSS backdrop-filter only blurs content inside the window), so it is an opt-in toggle.
function applyVibrancy() {
  if (process.platform !== "darwin" || !mainWindow || mainWindow.isDestroyed()) return;
  try { mainWindow.setVibrancy(settings.glass.vibrancy ? "under-window" : null); } catch (error) { if (process.env.VOI_DEBUG) console.log(`[vibrancy] ${error.message}`); }
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
  return Boolean((voiceState && voiceState.active && FORCE_VOICE_STATES.has(voiceState.state)) || now < attentionUntil || shortcutOpen || settingsOpen);
}
function tick() {
  if (!mainWindow || mainWindow.isDestroyed()) return null;
  const now = Date.now();
  const cursor = currentCursor();
  const decision = controller.update({
    cursor, displays: displayList(), dockDisplayId, mode: settings.layout.dock, panelRect: revealed ? panelScreenRect() : null,
    forceOpen: forceOpen(now), pinned: settings.layout.pinned, now, settings: dockOptions(),
  });
  lastReason = decision.reason;
  // The dock follows the CURSOR's display (debounced) while collapsed, unless a fixed display is chosen.
  if (!revealed && settings.layout.display === "cursor") {
    const target = follower.update(displayList(), cursor, dockDisplayId, now);
    if (target) placeDock(target.id, "follow-cursor");
  }
  if (decision.changed) {
    revealed = decision.revealed;
    if (revealed) { if (!mainWindow.isVisible()) mainWindow.showInactive(); registerRevealedShortcuts(); } else { releaseFocus(); shortcutOpen = false; setSettingsOpen(false); unregisterRevealedShortcuts(); }
    pushDock(true);
  }
  // Interactive only where the trapezoid glass actually is; everything else stays click-through to the game.
  const overPanel = revealed && pointerOverPanel(cursor);
  setIgnore(!overPanel);
  return decision;
}
function startPolling() { if (!pollTimer) pollTimer = setInterval(tick, POLL_MS); }
function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

function setSettingsOpen(open) {
  if (settingsOpen === open) return;
  settingsOpen = open;
  if (open) { registerGlobal("Escape", () => setSettingsOpen(false)); tick(); } else unregisterGlobal("Escape");
  pushDock(true);
}
function toggleShortcut() {
  if (settings.voice.mode === "ptt" && voice.snapshot().active) { // push-to-talk via shortcut: press to start talking, press again to send
    pttToggled = !pttToggled;
    voice.pttSet(pttToggled);
    tick();
    return;
  }
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

// ---- global shortcuts (Electron cannot see key-up, so PTT-by-shortcut is press/press) ----------
const shortcutRegistry = createShortcutRegistry(globalShortcut, { onDebug: message => { if (process.env.VOI_DEBUG) console.log(`[shortcut] ${message}`); } });
const registerGlobal = (accelerator, callback) => shortcutRegistry.register(accelerator, callback);
const unregisterGlobal = accelerator => shortcutRegistry.unregister(accelerator);
// Emergency parity: retreat / hold / stop have always-visible buttons AND global shortcuts that take the same route
// (deterministic POST /api/emergency, no model). A shortcut that cannot be registered is reported, never silent.
const emergencyShortcuts = createEmergencyShortcuts(shortcutRegistry, action => { runEmergency(action, "shortcut").catch(() => {}); });
function registerEmergencyShortcuts() {
  const status = emergencyShortcuts.bind(settings.emergency);
  shortcutStatus = { ...shortcutStatus, emergency: status };
  return status;
}
async function runEmergency(action, origin) {
  attentionUntil = Date.now() + ATTENTION_PULSE_MS; // a pressed emergency key always shows its result, even if the bar was collapsed
  let result;
  try { result = { ok: true, action, response: await requestJson("/api/emergency", { method: "POST", body: JSON.stringify({ action }), timeoutMs: 4000 }) }; }
  catch (error) { result = { ok: false, action, error: String((error && error.message) || error).slice(0, 240) }; }
  if (origin === "shortcut") pushToRenderer({ type: "emergency", origin, ...result });
  return result;
}
let toggleAccelerator = "";
function registerToggleShortcut() {
  if (toggleAccelerator) unregisterGlobal(toggleAccelerator);
  toggleAccelerator = settings.voice.shortcut;
  shortcutStatus = { ...shortcutStatus, toggle: registerGlobal(toggleAccelerator, toggleShortcut) };
  return shortcutStatus.toggle;
}
// Cmd+, is only claimed while the bar is revealed so it never shadows other apps (or the game) when collapsed.
function registerRevealedShortcuts() { shortcutStatus = { ...shortcutStatus, settings: registerGlobal(SHORTCUT_SETTINGS, () => setSettingsOpen(!settingsOpen)) }; }
function unregisterRevealedShortcuts() { unregisterGlobal(SHORTCUT_SETTINGS); }
function nudgeGlass(direction) { return applySettings({ glass: { alpha: Settings.nudgeAlpha(settings.glass.alpha, direction) } }); }

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
  handle(CHANNELS.targetPin, input => requestJson("/api/direct/registry/target-pin", { method: "POST", body: JSON.stringify(P.validateTargetPin(input)) }));
  handle(CHANNELS.events, input => requestJson(`/api/history?after=${P.validateCursor(input && input.after)}`));
  handle(CHANNELS.command, input => requestJson("/api/micromachine/modulate", { method: "POST", body: JSON.stringify({ ...P.validateCommand(input), async_publish: true }) }));
  handle(CHANNELS.voiceStart, async input => { P.validateVoiceControl(input); return voice.start(); });
  handle(CHANNELS.voiceStop, input => { P.validateVoiceControl(input); shortcutOpen = false; return voice.stop(); });
  handle(CHANNELS.voiceState, () => voice.snapshot());
  handle(CHANNELS.settingsGet, () => settingsSnapshot());
  handle(CHANNELS.settingsSet, input => applySettings(P.validateSettingsPatch(input)));
  handle(CHANNELS.settingsReset, input => {
    const { group } = P.validateSettingsReset(input);
    const fresh = Settings.defaults({ serverUrl: ENV_URL });
    return applySettings(group ? { [group]: fresh[group] } : fresh);
  });
  handle(CHANNELS.settingsOpen, input => { setSettingsOpen(P.validateSettingsOpen(input).open); return dockSnapshot(); });
  handle(CHANNELS.serverProbe, async input => {
    const { url } = P.validateServerProbe(input);
    const started = Date.now();
    try {
      const response = await fetch(new URL("/api/runtime/status", url), { redirect: "error", signal: AbortSignal.timeout(2500), headers: { accept: "application/json", ...(process.env.VOI_COMMANDER_TOKEN ? { Authorization: `Bearer ${process.env.VOI_COMMANDER_TOKEN}` } : {}) } });
      return { ok: response.ok, status: response.status, ms: Date.now() - started };
    } catch (error) { return { ok: false, status: 0, ms: Date.now() - started, error: String((error && error.name) || "error") }; }
  });
  handle(CHANNELS.shortcutCheck, input => { // can this accelerator be claimed? (does not keep it registered)
    const { accelerator } = P.validateShortcutPreview(input);
    if (accelerator === toggleAccelerator) return { ok: true, own: true };
    if (Object.values(emergencyShortcuts.bound()).includes(accelerator)) return { ok: true, own: true };
    if (shortcutRegistry.has(accelerator)) return { ok: false, reason: "used" };
    let ok = false;
    try { ok = globalShortcut.register(accelerator, () => {}); if (ok) globalShortcut.unregister(accelerator); } catch { ok = false; }
    return { ok, reason: ok ? "" : "used" };
  });
  handle(CHANNELS.rainbowPreview, input => { P.validateRainbowPreview(input); previewRainbow(); return { ms: PREVIEW_MS }; });
  handle(CHANNELS.emergency, input => runEmergency(P.validateEmergency(input).action, "ui"));
  handle(CHANNELS.dockState, () => dockSnapshot());
  handle(CHANNELS.dockFocus, input => { const { focus } = P.validateDockFocus(input); if (focus) return { focused: focusDock() }; releaseFocus(); return { focused: false }; });
  listen(CHANNELS.dockRect, input => { panelRect = P.validateDockRect(input); });
  listen(CHANNELS.dockAttention, input => { P.validateDockAttention(input); attentionUntil = Date.now() + ATTENTION_PULSE_MS; });
  listen(CHANNELS.voiceAudio, input => { voice.pushAudio(P.validateVoiceAudio(input)); });
  listen(CHANNELS.voiceLevel, input => {
    const level = P.validateVoiceLevel(input);
    if (overlayState === "listening" && settings.rainbow.reactive) for (const win of liveOverlays()) win.webContents.send(CHANNELS.overlayLevel, level);
  });
  listen(CHANNELS.voicePtt, input => { voice.pttSet(P.validatePushToTalk(input).down); });
  listen(CHANNELS.voiceMicError, input => { voice.micFailed(P.validateMicError(input).reason); });
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
    const isOverlay = liveOverlays().some(win => win.webContents.id === details.webContentsId);
    callback({ responseHeaders: { ...details.responseHeaders, "Content-Security-Policy": [isOverlay ? OVERLAY_CSP : RENDERER_CSP] } });
  });
}

function createDockWindow() {
  const display = displayById(dockDisplayId);
  currentLayout = layoutFor(settings.layout.dock, display.bounds, layoutOptions(display));
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
  mainWindow.webContents.once("did-finish-load", () => { mainWindow.showInactive(); pushDock(true); broadcastSettings(); });
  mainWindow.on("closed", () => { mainWindow = null; });
  mainWindow.loadFile(rendererFile);
  return mainWindow;
}

function onDisplaysChanged(event) {
  trace({ ev: "display-event", cursor: currentCursor() });
  follower.reset();
  dockDisplayId = resolveDockDisplay();
  placeDock(dockDisplayId, "displays-changed");
  broadcastSettings();
}

app.whenReady().then(() => {
  installPermissions(electronSession.defaultSession);
  loadSettings();
  dockDisplayId = resolveDockDisplay();
  voice = createVoice();
  registerIpc();
  createDockWindow();
  syncOverlays();
  for (const eventName of ["display-added", "display-removed", "display-metrics-changed"]) screen.on(eventName, onDisplaysChanged);
  registerToggleShortcut();
  registerEmergencyShortcuts();
  registerGlobal(SHORTCUT_OPACITY_UP, () => nudgeGlass(1));
  registerGlobal(SHORTCUT_OPACITY_DOWN, () => nudgeGlass(-1));
  startPolling();
  app.on("activate", () => { startPolling(); });
});
app.on("window-all-closed", () => app.quit());
app.on("will-quit", () => { saveSettingsNow(); globalShortcut.unregisterAll(); stopPolling(); });
app.on("before-quit", () => { if (voice) voice.stop(); });

module.exports = {
  CHANNELS, SHORTCUT_OPACITY_UP, SHORTCUT_OPACITY_DOWN, SHORTCUT_SETTINGS, requestJson,
  // Test/smoke hooks (read-only views of live state, plus a fake cursor).
  _internals: {
    getTrace: () => dockTrace.slice(), getMain: () => mainWindow, getOverlay: () => liveOverlays()[0] || null, getOverlays: () => liveOverlays(), getDockDisplayId: () => dockDisplayId, getVoice: () => voice, getOverlayState: () => overlayState,
    setFakeCursor: point => { fakeCursor = point; },
    tick, toggleShortcut, nudgeGlass, applySettings, setSettingsOpen, previewRainbow,
    getSettings: () => settings, panelPolygon, pointerOverPanel,
    dockInfo: () => ({ revealed, ignoring, focusable: windowFocusable, focused: Boolean(mainWindow && mainWindow.isFocused()), dock: settings.layout.dock, glass: settings.glass, reason: lastReason, bounds: mainWindow && mainWindow.getBounds(), panelRect, layout: currentLayout, shortcutOpen, settingsOpen, baseUrl }),
    setOverlay,
  },
};
