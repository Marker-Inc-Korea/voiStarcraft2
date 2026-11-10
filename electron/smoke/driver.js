"use strict";
// Real-app smoke run: `npx electron smoke/driver.js`. Boots the fake backend, launches the real main process, arms voice with
// Chromium's fake microphone, scripts spoken turns over the fake Realtime socket and saves screenshots to a temp dir.
// DEFAULT = second cursor (smoke/vcursor.js): hover positions go through main's fake-cursor hook, clicks and typing through the
// Accessibility helper, and a separate pointer window shows where it acts, so the user's real mouse stays free.
// REAL_POINTER=1 moves the real cursor instead (smoke/warp_cursor.swift + cursor_daemon.swift must be compiled to /tmp).
const { app } = require("electron");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFile, execFileSync } = require("node:child_process");
const Target = require("./target.js");
const SessionGuard = require("./session_guard.js");
const { createVirtualCursor } = require("./vcursor.js");
app.commandLine.appendSwitch("use-fake-device-for-media-stream");
app.commandLine.appendSwitch("use-fake-ui-for-media-stream");
app.setPath("userData", fs.mkdtempSync(path.join(os.tmpdir(), "voi-smoke-"))); // fresh prefs + localStorage

// Verification only: images go to a temp dir unless SMOKE_SAVE_SHOTS=1 (docs/screens is not touched by default).
const OUT = process.env.SMOKE_SAVE_SHOTS === "1" ? path.join(__dirname, "..", "docs", "screens") : fs.mkdtempSync(path.join(os.tmpdir(), "voi-smoke-shots-"));
const WARP = process.env.WARP_CURSOR || "/tmp/warp_cursor";
const REAL_POINTER = process.env.REAL_POINTER === "1" && fs.existsSync(WARP);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const report = { steps: [], checks: [] };
const note = (name, detail) => { report.steps.push({ name, detail }); console.log(`[smoke] ${name}: ${JSON.stringify(detail)}`); };
const check = (name, ok, detail) => {
  if (!ok && globalThis.__dumpTrace) globalThis.__dumpTrace(name); report.checks.push({ name, ok: Boolean(ok), detail }); console.log(`[smoke] ${ok ? "PASS" : "FAIL"} ${name}${detail === undefined ? "" : ` ${JSON.stringify(detail)}`}`); };

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const { createFakeBackend } = require("../fixtures/fake_backend.js");
  const backend = createFakeBackend({ port: 0, runtime: process.env.SMOKE_RUNTIME || "dry_run_fake_bot" });
  const port = await backend.listen();
  process.env.VOI_COMMANDER_URL = `http://127.0.0.1:${port}`;
  const { screen, nativeImage } = require("electron");
  await app.whenReady();
  if (SessionGuard.isBlocked(nativeImage)) { await backend.close(); app.exit(2); return; } // locked/dark screen: BLOCKED (exit 2), never a fake FAIL
  const target = Target.pickTarget(screen.getAllDisplays(), screen.getPrimaryDisplay().id, process.env.SMOKE_DISPLAY, screen.getCursorScreenPoint());
  const main = require("../main.js"); // after whenReady(): main.js picks its display from the cursor, so park the cursor first
  if (REAL_POINTER) { try { const p = Target.pointIn(target.bounds, 0.5, 0.5); execFileSync(WARP, [String(p.x), String(p.y)]); await sleep(150); } catch { /* cursor stays */ } } else main._internals.setFakeCursor(Target.pointIn(target.bounds, 0.5, 0.5));
  const I = main._internals;
  globalThis.__dumpTrace = name => { try { const t0 = Date.now(); console.log(`[smoke] TRACE for failed "${name}": cursor=${JSON.stringify(screen.getCursorScreenPoint())} dock=${JSON.stringify(I.dockInfo())}`); for (const e of I.getTrace().slice(-8)) console.log(`[smoke]   trace -${t0 - e.t}ms ${JSON.stringify(e)}`); } catch { /* ignore */ } };
  const DAEMON = process.env.CURSOR_DAEMON || "/tmp/cursor_daemon";
  const realCursor = REAL_POINTER;
  // Second cursor (default): never touches the real pointer. Its pointer window shows where hover/click/typing happen.
  const vc = realCursor ? null : await createVirtualCursor({ BrowserWindow: require("electron").BrowserWindow });
  // A real mouse under a human hand drifts the pointer between a warp and the check that follows, which makes the dock
  // (correctly!) follow the cursor to another display. The pinned point is re-asserted every 25 ms by a long-lived helper
  // so a check sees the position it asked for; drift is counted and reported rather than hidden.
  const daemon = realCursor && fs.existsSync(DAEMON) ? require("node:child_process").spawn(DAEMON, [], { stdio: ["pipe", "ignore", "inherit"] }) : null;
  let pinned = null, drifted = 0;
  const sendPoint = p => { if (daemon && daemon.stdin.writable) daemon.stdin.write(`${p.x} ${p.y}\n`); else execFileSync(WARP, [String(p.x), String(p.y)]); };
  const pinTimer = setInterval(() => { if (!pinned) return; const c = screen.getCursorScreenPoint(); if (Math.abs(c.x - pinned.x) > 1 || Math.abs(c.y - pinned.y) > 1) { drifted += 1; sendPoint(pinned); } else if (daemon) sendPoint(pinned); }, 25);
  // Second-cursor mode must never make our app the frontmost one (that is what taking the user's focus looks like).
  const frontmostPid = () => { try { return Number(/"pid"=(\d+)/.exec(execFileSync("lsappinfo", ["info", "-only", "pid", execFileSync("lsappinfo", ["front"]).toString().trim()]).toString())[1]); } catch { return 0; } };
  const frontSamples = [];
  const frontTimer = realCursor ? null : setInterval(() => frontSamples.push(frontmostPid()), 250);
  app.on("will-quit", () => { clearInterval(pinTimer); clearInterval(frontTimer); if (daemon) daemon.kill(); if (vc) vc.close(); });
  const warp = async (x, y) => {
    if (realCursor) { pinned = { x: Math.round(x), y: Math.round(y) }; sendPoint(pinned); I.setFakeCursor(null); } else { I.setFakeCursor({ x, y }); await vc.moveTo(x, y); }
    await sleep(60);
  };
  const pressId = (id, label = "") => (vc ? vc.press(id, { label }) : js(`document.getElementById(${JSON.stringify(id)}).click()`));
  const waitFor = async (fn, label, timeout = 8000) => { const t0 = Date.now(); for (;;) { const value = await fn(); if (value) return value; if (Date.now() - t0 > timeout) throw new Error(`timeout: ${label}`); await sleep(80); } };
  const win = await waitFor(() => I.getMain(), "dock window");
  await waitFor(() => !win.webContents.isLoading(), "renderer load");
  await waitFor(() => I.getOverlay(), "overlay window");
  await waitFor(() => !I.getOverlay().webContents.isLoading(), "overlay load");
  // Overlay windows are created/destroyed as the dock moves between displays (and for 화면=전체): always resolve the live one.
  const overlay = new Proxy({}, { get: (_t, key) => { const w = I.getOverlay(); const v = w && w[key]; return typeof v === "function" ? v.bind(w) : v; } });
  win.webContents.on("console-message", event => { const e = event && event.level !== undefined ? event : {}; if (e.level === "error" || e.level === 3) note("renderer console error", { message: e.message, line: e.lineNumber }); });
  const js = code => win.webContents.executeJavaScript(code, true);
  const shot = async (name, target = win) => { const image = await target.webContents.capturePage(); const file = path.join(OUT, `${name}.png`); fs.writeFileSync(file, image.toPNG()); note(`capturePage ${name}`, { size: image.getSize() }); };
  const screencapture = name => new Promise(resolve => { const file = path.join(OUT, `${name}.png`); execFile("screencapture", ["-x", file], error => { note(`screencapture ${name}`, { ok: !error }); resolve(!error); }); });
  const info = () => I.dockInfo();
  const display = target.bounds; // the display UNDER TEST (SMOKE_DISPLAY), not necessarily primary / (0,0)
  const displayInfo = target;
  note("environment", { cursorMode: realCursor ? "real pointer" : "second cursor (Accessibility)", axTrusted: vc ? vc.trusted : null, display, displayId: target.id, scaleFactor: target.scaleFactor, allDisplays: screen.getAllDisplays().map(x => ({ id: x.id, bounds: x.bounds, scale: x.scaleFactor })), electron: process.versions.electron });
  await waitFor(() => js("document.getElementById('btn-gear').dataset.backend==='ok'"), "backend dot ok");
  // Pin the dock to the display under test so a drifting/hand-moved pointer cannot relocate it mid-check; the follow-the-cursor
  // behaviour has its own section (9b) which switches 모니터 back to 커서.
  I.applySettings({ layout: { display: `id:${target.id}` } }); await sleep(300);

  // ---- 1. collapsed: handle only, click-through, never focused ----
  await warp(display.x + display.width / 2, display.y + display.height / 2);
  await sleep(900);
  let d = info();
  check("collapsed: panel not revealed", d.revealed === false);
  check("collapsed: window is click-through", d.ignoring === true);
  check("collapsed: window not focusable and not focused", d.focusable === false && d.focused === false, { focusable: d.focusable, focused: d.focused });
  check("window is the fixed dock footprint (bottom mode)", d.bounds.y + d.bounds.height === display.y + display.height && d.bounds.width <= 980, d.bounds);
  await screencapture("01-collapsed-handle");

  // ---- 2. hover the last pixel row across the bar's span: reveal without taking focus ----
  await warp(display.x + display.width / 2, display.y + display.height - 1);
  await waitFor(() => info().revealed, "hover reveal", 3000);
  await sleep(500);
  d = info();
  check("hover reveals the bar (reason edge)", d.revealed && d.reason === "edge", { reason: d.reason });
  check("hover did NOT take focus", d.focused === false && d.focusable === false, { focused: d.focused, focusable: d.focusable, appFocusedWindow: Boolean(require("electron").BrowserWindow.getFocusedWindow()) });
  check("no focused BrowserWindow after hover", require("electron").BrowserWindow.getFocusedWindow() === null);
  await warp(display.x + display.width / 2, display.y + display.height - 60); // over the panel
  await sleep(400);
  check("cursor over panel -> window interactive", info().ignoring === false);
  await screencapture("02-hover-revealed-bar");

  // ---- 3. leaving hides after the debounce ----
  await warp(display.x + display.width / 2, display.y + 200);
  await sleep(300);
  check("still open 300ms after leaving (debounce)", info().revealed === true);
  await waitFor(() => info().revealed === false, "collapse after leaving", 3000);
  check("collapsed after cursor left", info().revealed === false && info().ignoring === true);

  // ---- 4. voice via real orb click; auto-reveal while listening + rainbow overlay ----
  await warp(display.x + display.width / 2, display.y + display.height - 1);
  await waitFor(() => info().revealed, "reveal for orb");
  await warp(display.x + display.width / 2, display.y + display.height - 60);
  await sleep(300);
  await pressId("orb", "음성 켜기");
  await waitFor(() => backend.state.sockets.size > 0, "realtime socket");
  await waitFor(() => js("document.body.dataset.voice==='idle'"), "voice ready", 8000);
  await waitFor(() => backend.state.audioBytes > 0, "mic audio", 6000).catch(() => {});
  check("mic audio reached the fake realtime socket", backend.state.audioBytes > 0, { bytes: backend.state.audioBytes, chunks: backend.state.audioChunks });
  await warp(display.x + display.width / 2, display.y + 150); // cursor far away: dock must stay up for voice
  const args = {
    command_layer: "operation", goal: "마린 두 기는 정찰, 네 기는 오른쪽 길로 공격",
    operations: [
      { operation_id: "scout", goal: "마린 2기 적 본진 정찰", unit_classes: ["marine"], count: 2, task_type: "scout", route_type: "direct", target_type: "enemy_main" },
      { operation_id: "strike", goal: "마린 4기 오른쪽 길로 적 멀티 공격", unit_classes: ["marine"], count: 4, task_type: "attack", route_type: "flank_right", target_type: "enemy_natural", fixture_stop_at: 4, fixture_blocked: "composition_shortfall: 마린 4기 중 2기만 사용 가능" },
    ],
  };
  backend.speak({ transcript: "마린 두 기는 정찰하고 네 기는 오른쪽 길로 공격", tool_arguments: args, speech_ms: 6000, think_ms: 11000 });
  await waitFor(() => js("document.body.dataset.voice==='listening'"), "listening state");
  await waitFor(() => I.getOverlayState() === "listening", "overlay listening");
  await sleep(1500);
  d = info();
  check("voice listening keeps the dock revealed with cursor elsewhere", d.revealed === true, { reason: d.reason });
  check("overlay: listening, visible, alwaysOnTop, non-focusable, not focused", I.getOverlayState() === "listening" && overlay.isVisible() && overlay.isAlwaysOnTop() && !overlay.isFocusable() && !overlay.isFocused(), { bounds: overlay.getBounds() });
  check("overlay covers the full display bounds", JSON.stringify(overlay.getBounds()) === JSON.stringify(display), { overlay: overlay.getBounds(), display });
  check("dock window still not focused during voice", info().focused === false);
  await screencapture("03-listening-overlay-fullscreen");
  await waitFor(() => js("document.body.dataset.voice==='thinking'"), "thinking state", 9000);
  await sleep(600);
  await screencapture("04-thinking-overlay-fullscreen");
  await waitFor(() => js("Number(document.getElementById('ops-count').textContent)>=2"), "two parallel operations counted in the HUD ops word (rails live in the board window)", 14000);
  const peakOps = Number(await js("document.getElementById('ops-count').textContent")); // the count is OPEN operations: it only falls from here
  await sleep(9000);
  I.applySettings({ layout: { pinned: true } }); await sleep(500); // the bar would auto-collapse 2.5 s after voice ended
  await screencapture("05-operations-strip-screen");
  I.applySettings({ layout: { pinned: false } });
  note("intent forwarded", { intents: backend.state.intents.map(i => ({ transcript: i.transcript, tier: i.tool_arguments && i.tool_arguments.command_layer, latency: i.latency_ms })) });
  // The fake backend blocks the "strike" operation ~5.5 s after it starts, so after the 9 s wait the OPEN count has legitimately fallen
  // from 2 to 1 (the product shows open operations, see board_cards.opsCount). Assert the invariant, not a time-dependent number.
  const nowOps = Number(await js("document.getElementById('ops-count').textContent"));
  check("HUD shows only a one-word operation count (no cards in the HUD)", peakOps >= 2 && Number.isInteger(nowOps) && nowOps >= 1 && nowOps <= peakOps && await js("document.querySelectorAll('.card, .op, .rail').length===0"), { peakOps, nowOps });
  check("bar has no paragraphs / helper text", await js("document.querySelectorAll('.panel p:not(.transcript):not(.utterance)').length===0"));

  // ---- 5. text input: click focuses (only then), hide releases focus ----
  await warp(display.x + display.width / 2, display.y + display.height - 60);
  await sleep(300);
  await pressId("orb", "음성 끄기"); // stop voice
  await sleep(500);
  await warp(display.x + display.width / 2, display.y + display.height - 1);
  await waitFor(() => info().revealed, "bar revealed before measuring", 4000);
  await warp(display.x + display.width / 2, display.y + display.height - 60);
  await waitFor(() => js("getComputedStyle(document.getElementById('panel')).transform==='none' && getComputedStyle(document.getElementById('panel')).opacity==='1'"), "slide finished", 4000);
  const inputRect = await js("(()=>{const r=document.getElementById('cmd-input').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()");
  const wb = win.getBounds();
  if (vc) {
    // Second cursor: the Accessibility helper focuses the input and types into it. The real pointer and keyboard are untouched,
    // so the window must NOT take OS focus (taking it is what a real click does; that path is focus_probe.js, REAL_POINTER only).
    const typed = await vc.type("cmd-input", "앞마당 방어해", { label: "명령 입력" });
    await sleep(300);
    d = info();
    check("second cursor types into the input through Accessibility", typed.ok === true && await js("document.getElementById('cmd-input').value") === "앞마당 방어해", typed);
    check("second cursor typing does not take OS focus from the user's app", d.focused === false, { focusable: d.focusable, focused: d.focused });
    await js("document.getElementById('cmd-form').requestSubmit()");
  } else {
    await warp(wb.x + inputRect.x, wb.y + inputRect.y); // a real user's cursor is on the input when clicking
    await sleep(250);
    note("before input click", { revealed: info().revealed, ignoring: info().ignoring, inputRect, wb });
    win.webContents.sendInputEvent({ type: "mouseDown", x: Math.round(inputRect.x), y: Math.round(inputRect.y), button: "left", clickCount: 1 });
    win.webContents.sendInputEvent({ type: "mouseUp", x: Math.round(inputRect.x), y: Math.round(inputRect.y), button: "left", clickCount: 1 });
    await sleep(600);
    d = info();
    check("clicking the input grants focus", d.focusable === true && d.focused === true, { focusable: d.focusable, focused: d.focused });
    await js("(()=>{const i=document.getElementById('cmd-input');i.value='앞마당 방어해';document.getElementById('cmd-form').requestSubmit();})()");
  }
  await sleep(300);
  await js("(()=>{const i=document.getElementById('cmd-input');i.value='refuse me';document.getElementById('cmd-form').requestSubmit();})()");
  await sleep(2800);
  await warp(display.x + display.width / 2, display.y + 100);
  await waitFor(() => info().revealed === false, "collapse after commands", 12000);
  d = info();
  check("collapse releases focus (focusable false, not focused)", d.focusable === false && d.focused === false, { focusable: d.focusable, focused: d.focused });

  // ---- 6. runtime field missing -> muted unknown badge ----
  backend.state.runtime = "missing";
  await warp(display.x + display.width / 2, display.y + display.height - 1);
  await waitFor(() => info().revealed, "reveal for unknown test");
  await warp(display.x + display.width / 2, display.y + display.height - 60);
  await js("(()=>{const i=document.getElementById('cmd-input');i.value='정찰 보내';document.getElementById('cmd-form').requestSubmit();})()");
  await sleep(2600);
  check("missing direct_runtime still yields a published board card (no card UI in the HUD)", await js("document.querySelectorAll('.card').length===0 && document.getElementById('ops').hidden===false"));
  backend.state.runtime = "dry_run_fake_bot";

  // ---- 7. minimal bar screenshot (voice off, no cards) ----
  await pressId("orb", "음성").catch(() => {});
  await sleep(200);
  const stopVoice = async () => { if (I.getVoice().snapshot().active) { await pressId("orb", "음성 끄기"); await sleep(500); } };
  await stopVoice();
  await warp(display.x + display.width / 2, display.y + display.height - 1);
  await waitFor(() => info().revealed, "reveal for minimal bar");
  await warp(display.x + display.width / 2, display.y + display.height - 60);
  await sleep(600);
  await screencapture("06-minimal-bar");
  check("minimal HUD shows only orb, input, gear (no retreat/hold/stop buttons)", await js("[...document.querySelectorAll('.bar button, .bar input')].map(n=>n.id).join(',')==='orb,cmd-input,btn-gear' && !document.getElementById('emergency') && document.getElementById('cmd-input').placeholder==='명령'"));

  // ---- 8. settings panel: gear click opens at once, pins the bar, every tab, live apply, Esc / outside / gear close ----
  if (vc) await vc.point("btn-gear"); // glide first so the timing below measures the press, not the pointer animation
  const openedAt = Date.now();
  await (vc ? vc.press("btn-gear", { label: "설정", glide: false }) : js("document.getElementById('btn-gear').click()"));
  await waitFor(() => info().settingsOpen && js("document.body.dataset.settings==='open'"), "settings open", 1500);
  check("gear opens settings within 300ms", Date.now() - openedAt < 300, { ms: Date.now() - openedAt });
  check("settings open pins the bar revealed", info().revealed === true);
  await warp(display.x + display.width / 2, display.y + 100); // cursor far away
  await sleep(1500);
  check("bar stays revealed while settings are open (cursor away)", info().revealed === true);
  for (const tab of ["glass", "layout", "rainbow", "voice", "server", "reset"]) {
    await js(`document.querySelector('.tabs [data-tab="${tab}"]').click()`);
    await sleep(tab === "server" ? 700 : 350);
    const rows = await js("document.querySelectorAll('#rows .row').length");
    check(`settings tab ${tab} renders rows`, rows >= 2, { rows });
    check(`tab ${tab}: labels are words (no sentences)`, await js("[...document.querySelectorAll('#rows .k')].every(n=>n.textContent.length<=10 && !/[.。]/.test(n.textContent))"));
    await screencapture(`07-settings-${tab}`);
  }
  await js("document.querySelector('.tabs [data-tab=\"glass\"]').click()");
  await sleep(300);
  // live apply: drag-equivalent input events on the real sliders
  const setRange = (index, value) => js(`(()=>{const r=document.querySelectorAll('#rows input[type=range]')[${index}];r.value=${value};r.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  await setRange(0, 60); await sleep(400);
  check("opacity slider applies live to the bar (CSS var) and persists in main", Math.abs(Number(await js("getComputedStyle(document.documentElement).getPropertyValue('--glass-alpha')")) - 0.6) < 0.001 && Math.abs(I.getSettings().glass.alpha - 0.6) < 0.001, { main: I.getSettings().glass.alpha });
  await setRange(2, 200); await sleep(300); // hue slider is the 3rd range on the glass tab (after the toggle row is skipped)
  I.applySettings({ glass: { alpha: 0.12, blur: 4 } }); await sleep(500);
  await screencapture("08-glass-combo-alpha12-blur4");
  I.applySettings({ glass: { alpha: 0.62, blur: 24, hue: 200, saturation: 70 } }); await sleep(500);
  await screencapture("08-glass-combo-alpha62-blur24");
  check("settings persisted to userData/settings.json", await (async () => { await sleep(500); const f = path.join(app.getPath("userData"), "settings.json"); return fs.existsSync(f) && JSON.parse(fs.readFileSync(f, "utf8")).glass.alpha === 0.62; })());
  I.applySettings({ glass: { alpha: 0.28, blur: 12, hue: 250, saturation: 55 } });
  // close paths
  await pressId("btn-gear", "설정 닫기"); await sleep(300);
  check("gear again closes settings", info().settingsOpen === false);
  await pressId("btn-gear", "설정"); await sleep(300);
  win.webContents.sendInputEvent({ type: "keyDown", keyCode: "Escape" }); win.webContents.sendInputEvent({ type: "keyUp", keyCode: "Escape" }); await sleep(300);
  note("escape result", { settingsOpen: info().settingsOpen });
  if (info().settingsOpen) await js("window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}))");
  await sleep(300);
  check("Esc closes settings", info().settingsOpen === false);
  await pressId("btn-gear", "설정"); await sleep(300);
  await js("document.getElementById('transcript').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}))"); await sleep(300);
  check("click outside closes settings", info().settingsOpen === false);

  // ---- 8b. adjustable transparency over a busy, colourful backdrop (the real desktop shows through the glass) ----
  const { BrowserWindow } = require("electron");
  const backdrop = new BrowserWindow({ ...display, show: false, frame: false, focusable: false, skipTaskbar: true, hasShadow: false, webPreferences: { partition: "backdrop-test", sandbox: true } });
  const stripes = Array.from({ length: 14 }, (_, i) => `<div style="position:absolute;left:${i * 7.2}%;top:0;bottom:0;width:7.2%;background:hsl(${i * 26},90%,${i % 2 ? 55 : 42}%)"></div>`).join("");
  await backdrop.loadURL("data:text/html;charset=utf-8," + encodeURIComponent(`<!doctype html><body style="margin:0;overflow:hidden;font:700 64px sans-serif;color:#fff"><div style="position:absolute;inset:0">${stripes}</div><div style="position:absolute;left:5%;right:5%;bottom:6%;height:230px;background:repeating-linear-gradient(45deg,#fff 0 18px,#111 18px 36px);opacity:.9"></div><div style="position:absolute;left:8%;bottom:10%;mix-blend-mode:difference">BACKDROP VISIBLE THROUGH GLASS 12345</div></body>`));
  backdrop.showInactive();
  await sleep(500);
  I.applySettings({ layout: { pinned: true } });
  await warp(display.x + display.width / 2, display.y + 120);
  await sleep(900);
  for (const pct of [10, 28, 60, 90]) {
    I.applySettings({ glass: { alpha: pct / 100, blur: pct === 10 ? 4 : 12 } });
    await sleep(700);
    check(`glass ${pct}%: CSS variable applied live`, Math.abs(Number(await js("getComputedStyle(document.documentElement).getPropertyValue('--glass-alpha')")) - pct / 100) < 0.001);
    await screencapture(`10-glass-alpha-${String(pct).padStart(2, "0")}-over-backdrop`);
  }
  const before = info().glass.alpha;
  I.nudgeGlass(-1); await sleep(300);
  check("opacity shortcut nudges alpha by 5%", Math.abs(info().glass.alpha - Math.max(0.08, before - 0.05)) < 0.001, { before, after: info().glass.alpha });
  I.applySettings({ glass: { alpha: 0.28, blur: 12 }, layout: { pinned: false } });
  backdrop.destroy();
  await sleep(300);

  // ---- 9. layout: dock position changes live (left / top / right) with correct window geometry ON THE DISPLAY UNDER TEST ----
  const Displays = require("../displays.js");
  for (const dock of ["right", "left", "top", "bottom"]) {
    I.applySettings({ layout: { dock } }); await sleep(500);
    const b = info().bounds;
    const onTarget = screen.getDisplayMatching(b).id === target.id && b.x >= display.x && b.y >= display.y && b.x + b.width <= display.x + display.width && b.y + b.height <= display.y + display.height;
    check(`dock ${dock}: window flush with that edge of display ${target.id}`, onTarget && Displays.isFlush(dock, target, b) && info().dock === dock, { bounds: b, display });
  }
  I.applySettings({ layout: { dock: "right" } }); await sleep(400);
  const rightTrigger = info().layout.trigger;
  await warp(rightTrigger.x + 1, rightTrigger.y + rightTrigger.height / 2);
  await waitFor(() => info().revealed, "reveal right edge", 3000);
  await warp(display.x + display.width - 120, rightTrigger.y + rightTrigger.height / 2);
  await sleep(700);
  I.applySettings({ layout: { dock: "bottom" } });
  await warp(display.x + display.width / 2, display.y + 100);
  await waitFor(() => info().revealed === false, "collapse after layout test", 12000);

  // ---- 9b. multi-display: the dock follows the CURSOR's display (debounced) and the overlay scope setting ----
  I.applySettings({ layout: { display: "cursor" } }); await sleep(200);
  const all = screen.getAllDisplays();
  note("displays", all.map(x => ({ id: x.id, bounds: x.bounds, scale: x.scaleFactor, rotation: x.rotation })));
  for (const other of all) {
    const p = Target.pointIn(other.bounds, 0.5, 0.5);
    await warp(p.x, p.y);
    await waitFor(() => I.getDockDisplayId() === other.id, `dock follows cursor to display ${other.id}`, 4000).catch(() => {});
    await sleep(300);
    const b = info().bounds;
    check(`cursor on display ${other.id} -> dock window is on display ${other.id}`, I.getDockDisplayId() === other.id && screen.getDisplayMatching(b).id === other.id && Displays.isFlush("bottom", other, b), { dock: I.getDockDisplayId(), bounds: b });
  }
  // back to the display under test
  { const p = Target.pointIn(display, 0.5, 0.5); await warp(p.x, p.y); await waitFor(() => I.getDockDisplayId() === target.id, "dock back on target", 4000); }
  I.applySettings({ layout: { display: `id:${target.id}` } }); await sleep(300);
  I.previewRainbow(); await sleep(500);
  check("rainbow 화면=현재: exactly one overlay, covering the dock's display", I.getOverlays().length === 1 && JSON.stringify(I.getOverlays()[0].getBounds()) === JSON.stringify(display), { overlays: I.getOverlays().map(w => w.getBounds()) });
  await waitFor(() => I.getOverlayState() === "off", "preview ends", 4000);
  I.applySettings({ rainbow: { screen: "all" } }); await sleep(600);
  I.previewRainbow(); await sleep(600);
  const boundsOf = w => JSON.stringify(w.getBounds());
  check("rainbow 화면=전체: one overlay window per display, each exactly on its display", I.getOverlays().length === all.length && all.every(x => I.getOverlays().some(w => boundsOf(w) === JSON.stringify(x.bounds))), { overlays: I.getOverlays().map(w => w.getBounds()) });
  check("rainbow 화면=전체: every overlay is visible, click-through-capable and never focused", I.getOverlays().every(w => w.isVisible() && !w.isFocusable() && !w.isFocused() && w.isAlwaysOnTop()));
  await waitFor(() => I.getOverlayState() === "off", "preview ends 3", 4000);
  I.applySettings({ rainbow: { screen: "current" } }); await sleep(500);
  check("rainbow 화면=현재 again: extra overlay windows are destroyed", I.getOverlays().length === 1, { n: I.getOverlays().length });

  // ---- 10. rainbow preview (2s) and per-setting overlay behaviour ----
  I.previewRainbow(); await sleep(600);
  check("rainbow preview shows the overlay without voice", I.getOverlayState() === "listening" && overlay.isVisible());
  await waitFor(() => I.getOverlayState() === "off", "preview ends", 4000);
  I.applySettings({ rainbow: { enabled: false } });
  I.previewRainbow(); await sleep(500);
  check("preview still works when disabled (it is a preview)", I.getOverlayState() === "listening");
  await waitFor(() => I.getOverlayState() === "off", "preview ends 2", 4000);
  I.applySettings({ rainbow: { enabled: true, depth: 140, intensity: 80 } }); await sleep(300);
  check("overlay received its settings subset", await overlay.webContents.executeJavaScript("getComputedStyle(document.documentElement).getPropertyValue('--edge-depth').trim()") === "140px");
  I.applySettings({ rainbow: { depth: 90, intensity: 95 } });

  // ---- 11. global shortcut path ----
  await warp(display.x + display.width / 2, display.y + 100);
  await sleep(600);
  I.toggleShortcut();
  await sleep(900);
  check("shortcut opens the dock and starts voice", info().revealed === true && info().shortcutOpen === true && Boolean(I.getVoice().snapshot().active));
  I.toggleShortcut();
  await sleep(500);
  check("shortcut again stops voice", I.getVoice().snapshot().active === false);
  if (vc) {
    clearInterval(frontTimer);
    check("second cursor never made this app frontmost (the user's app kept focus)", frontSamples.length > 20 && !frontSamples.includes(process.pid), { samples: frontSamples.length, ownPid: process.pid, distinct: [...new Set(frontSamples)] });
  }

  note("cursor drift corrected", { times: drifted });
  report.cursorDriftCorrections = drifted;
  fs.writeFileSync(path.join(OUT, "smoke-report.json"), JSON.stringify(report, null, 2));
  const failed = report.checks.filter(c => !c.ok);
  console.log(`[smoke] ${report.checks.length - failed.length}/${report.checks.length} checks passed`);
  await backend.close();
  app.exit(failed.length ? 1 : 0);
}
main().catch(error => { console.error("[smoke] FAILED", error); try { fs.writeFileSync(path.join(OUT, "smoke-report.json"), JSON.stringify({ ...report, error: String((error && error.stack) || error) }, null, 2)); } catch { /* ignore */ } app.exit(1); });
