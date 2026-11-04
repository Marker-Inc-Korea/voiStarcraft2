"use strict";
// Real-app smoke run: `npx electron smoke/driver.js`. Boots the fake backend, launches the real main
// process, moves the REAL cursor (CoreGraphics via smoke/warp_cursor.swift when compiled, else a test
// hook), arms voice with Chromium's fake microphone, scripts spoken turns over the fake Realtime
// socket and saves screenshots to docs/screens/. Findings are written to docs/screens/smoke-report.json.
const { app } = require("electron");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFile, execFileSync } = require("node:child_process");

app.commandLine.appendSwitch("use-fake-device-for-media-stream");
app.commandLine.appendSwitch("use-fake-ui-for-media-stream");
app.setPath("userData", fs.mkdtempSync(path.join(os.tmpdir(), "voi-smoke-"))); // fresh prefs + localStorage

const OUT = path.join(__dirname, "..", "docs", "screens");
const WARP = process.env.WARP_CURSOR || "/tmp/warp_cursor";
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const report = { steps: [], checks: [] };
const note = (name, detail) => { report.steps.push({ name, detail }); console.log(`[smoke] ${name}: ${JSON.stringify(detail)}`); };
const check = (name, ok, detail) => { report.checks.push({ name, ok: Boolean(ok), detail }); console.log(`[smoke] ${ok ? "PASS" : "FAIL"} ${name}${detail === undefined ? "" : ` ${JSON.stringify(detail)}`}`); };

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const { createFakeBackend } = require("../fixtures/fake_backend.js");
  const backend = createFakeBackend({ port: 0, runtime: process.env.SMOKE_RUNTIME || "dry_run_fake_bot" });
  const port = await backend.listen();
  process.env.VOI_COMMANDER_URL = `http://127.0.0.1:${port}`;
  const main = require("../main.js");
  const { screen } = require("electron");
  await app.whenReady();
  const I = main._internals;
  const realCursor = fs.existsSync(WARP);
  const warp = async (x, y) => {
    if (realCursor) { execFileSync(WARP, [String(Math.round(x)), String(Math.round(y))]); I.setFakeCursor(null); } else I.setFakeCursor({ x, y });
    await sleep(60);
  };
  const waitFor = async (fn, label, timeout = 8000) => { const t0 = Date.now(); for (;;) { const value = await fn(); if (value) return value; if (Date.now() - t0 > timeout) throw new Error(`timeout: ${label}`); await sleep(80); } };
  const win = await waitFor(() => I.getMain(), "dock window");
  await waitFor(() => !win.webContents.isLoading(), "renderer load");
  const overlay = await waitFor(() => I.getOverlay(), "overlay window");
  await waitFor(() => !overlay.webContents.isLoading(), "overlay load");
  win.webContents.on("console-message", event => { const e = event && event.level !== undefined ? event : {}; if (e.level === "error" || e.level === 3) note("renderer console error", { message: e.message, line: e.lineNumber }); });
  const js = code => win.webContents.executeJavaScript(code, true);
  const shot = async (name, target = win) => { const image = await target.webContents.capturePage(); const file = path.join(OUT, `${name}.png`); fs.writeFileSync(file, image.toPNG()); note(`capturePage ${name}`, { size: image.getSize() }); };
  const screencapture = name => new Promise(resolve => { const file = path.join(OUT, `${name}.png`); execFile("screencapture", ["-x", file], error => { note(`screencapture ${name}`, { ok: !error }); resolve(!error); }); });
  const info = () => I.dockInfo();
  const display = screen.getPrimaryDisplay().bounds;
  note("environment", { realCursorWarp: realCursor, display, electron: process.versions.electron });
  await waitFor(() => js("document.getElementById('chip-backend').dataset.tone==='ok'"), "backend chip ok");

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
  await js("document.getElementById('orb').click()");
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
  await waitFor(() => js("!!document.querySelector('.card .latency')"), "intent card with latency", 14000);
  await sleep(9000);
  await screencapture("05-operations-strip-screen");
  note("intent forwarded", { intents: backend.state.intents.map(i => ({ transcript: i.transcript, tier: i.tool_arguments && i.tool_arguments.command_layer, latency: i.latency_ms })) });
  check("parallel operations rendered as separate rails", await js("document.querySelectorAll('.card .op').length>=2"));
  check("dry-run fake bot is labelled as simulation", await js("!!document.querySelector('.runtime-badge[data-runtime=\"dry_run_fake_bot\"]')"));
  check("blocked reason from backend is visible", await js("[...document.querySelectorAll('.reason[data-kind=\"blocked\"]')].some(n=>/composition_shortfall/.test(n.textContent))"));
  check("no real 'Effect observed' shown for fake bot", await js("!document.querySelector('.rail[data-effect=\"yes\"]')"));

  // ---- 5. text input: click focuses (only then), hide releases focus ----
  await warp(display.x + display.width / 2, display.y + display.height - 60);
  await sleep(300);
  await js("document.getElementById('orb').click()"); // stop voice
  await sleep(500);
  const inputRect = await js("(()=>{const r=document.getElementById('cmd-input').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()");
  const wb = win.getBounds();
  await warp(wb.x + inputRect.x, wb.y + inputRect.y); // a real user's cursor is on the input when clicking
  await sleep(250);
  note("before input click", { revealed: info().revealed, ignoring: info().ignoring, inputRect, wb });
  win.webContents.sendInputEvent({ type: "mouseDown", x: Math.round(inputRect.x), y: Math.round(inputRect.y), button: "left", clickCount: 1 });
  win.webContents.sendInputEvent({ type: "mouseUp", x: Math.round(inputRect.x), y: Math.round(inputRect.y), button: "left", clickCount: 1 });
  await sleep(600);
  d = info();
  check("clicking the input grants focus", d.focusable === true && d.focused === true, { focusable: d.focusable, focused: d.focused });
  await js("(()=>{const i=document.getElementById('cmd-input');i.value='앞마당 방어해';document.getElementById('cmd-form').requestSubmit();})()");
  await sleep(300);
  await js("document.getElementById('btn-retreat').click()");
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
  check("missing direct_runtime shows 'runtime unknown' badge", await js("!!document.querySelector('.runtime-badge[data-runtime=\"unknown\"]')"));
  backend.state.runtime = "dry_run_fake_bot";

  // ---- 7. right sidebar mode (persisted) ----
  await js("document.getElementById('btn-dock').click()");
  await sleep(900);
  d = info();
  check("right mode: window spans the work-area height at the right edge", d.mode === "right" && d.bounds.height === screen.getPrimaryDisplay().workArea.height && d.bounds.x + d.bounds.width === display.x + display.width, d.bounds);
  await warp(display.x + display.width - 1, display.y + display.height / 2);
  await sleep(500);
  await warp(display.x + display.width - 100, display.y + display.height / 2);
  await sleep(900);
  check("right mode reveals", info().revealed === true);
  await screencapture("08-right-sidebar-screen");
  check("mode persisted to userData", JSON.parse(fs.readFileSync(path.join(app.getPath("userData"), "dock-prefs.json"), "utf8")).mode === "right");
  await js("document.getElementById('btn-dock').click()");
  await sleep(700);

  // ---- 7b. adjustable transparency over a busy, colourful backdrop (shows the real desktop through the glass) ----
  const { BrowserWindow } = require("electron");
  const backdrop = new BrowserWindow({ ...display, show: false, frame: false, focusable: false, skipTaskbar: true, hasShadow: false, webPreferences: { partition: "backdrop", sandbox: true } });
  const stripes = Array.from({ length: 14 }, (_, i) => `<div style="position:absolute;left:${i * 7.2}%;top:0;bottom:0;width:7.2%;background:hsl(${i * 26},90%,${i % 2 ? 55 : 42}%)"></div>`).join("");
  await backdrop.loadURL("data:text/html;charset=utf-8," + encodeURIComponent(`<body style="margin:0;overflow:hidden;font:700 64px sans-serif;color:#fff"><div style="position:absolute;inset:0">${stripes}</div><div style="position:absolute;left:5%;right:5%;bottom:6%;height:230px;background:repeating-linear-gradient(45deg,#fff 0 18px,#111 18px 36px);opacity:.9"></div><div style="position:absolute;left:8%;bottom:10%;mix-blend-mode:difference">BACKDROP VISIBLE THROUGH GLASS 12345</div></body>`));
  backdrop.showInactive();
  await sleep(500);
  await js("(()=>{const b=document.getElementById('btn-pin'); if(b.getAttribute('aria-pressed')!=='true') b.click();})()");
  await warp(display.x + display.width / 2, display.y + 120);
  await sleep(900);
  for (const pct of [10, 28, 60, 90]) {
    I.setGlass({ alpha: pct / 100, blur: pct === 10 ? 4 : 12 });
    await sleep(700);
    check(`glass ${pct}%: CSS variable applied live`, Math.abs(Number(await js("getComputedStyle(document.documentElement).getPropertyValue('--glass-alpha')")) - pct / 100) < 0.001);
    await screencapture(`09-glass-alpha-${String(pct).padStart(2, "0")}`);
  }
  const before = info().glass.alpha;
  I.nudgeGlass(-1); await sleep(300);
  check("opacity shortcut nudges alpha by 5%", Math.abs(info().glass.alpha - Math.max(0.08, before - 0.05)) < 0.001, { before, after: info().glass.alpha });
  I.setGlass({ alpha: 0.28, blur: 12 });
  await js("document.getElementById('btn-pin').click()");
  backdrop.destroy();
  await sleep(300);

  // ---- 8. global shortcut path (toggle pin-open + voice) ----
  await warp(display.x + display.width / 2, display.y + 100);
  await waitFor(() => info().revealed === false, "collapse before shortcut", 12000);
  I.toggleShortcut();
  await sleep(900);
  check("shortcut opens the dock and starts voice", info().revealed === true && info().shortcutOpen === true && Boolean(I.getVoice().snapshot().active));
  I.toggleShortcut();
  await sleep(500);
  check("shortcut again stops voice", I.getVoice().snapshot().active === false);

  fs.writeFileSync(path.join(OUT, "smoke-report.json"), JSON.stringify(report, null, 2));
  const failed = report.checks.filter(c => !c.ok);
  console.log(`[smoke] ${report.checks.length - failed.length}/${report.checks.length} checks passed`);
  await backend.close();
  app.exit(failed.length ? 1 : 0);
}
main().catch(error => { console.error("[smoke] FAILED", error); try { fs.writeFileSync(path.join(OUT, "smoke-report.json"), JSON.stringify({ ...report, error: String((error && error.stack) || error) }, null, 2)); } catch { /* ignore */ } app.exit(1); });
