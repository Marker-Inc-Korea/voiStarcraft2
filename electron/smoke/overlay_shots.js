"use strict";
// `npx electron smoke/overlay_shots.js`: rainbow edge glow over a bright and a dark full-display backdrop,
// captured with the real `screencapture -x` of the whole display (so the transparent overlay window is composited by macOS).
const { app, BrowserWindow, screen } = require("electron");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFile, execFileSync } = require("node:child_process");
// The user's real mouse is left alone unless REAL_POINTER=1; otherwise main's fake-cursor hook places the dock.
const REAL_POINTER = process.env.REAL_POINTER === "1";
if (REAL_POINTER) { try { execFileSync(process.env.WARP_CURSOR || "/tmp/warp_cursor", ["700", "400"]); } catch { /* cursor stays where it is */ } }
app.setPath("userData", fs.mkdtempSync(path.join(os.tmpdir(), "voi-ov-")));
const OUT = path.join(__dirname, "..", "docs", "screens");
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const cap = name => new Promise(resolve => execFile("screencapture", ["-x", path.join(OUT, `${name}.png`)], () => resolve()));
(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const { createFakeBackend } = require("../fixtures/fake_backend.js");
  const backend = createFakeBackend({ port: 0 });
  process.env.VOI_COMMANDER_URL = `http://127.0.0.1:${await backend.listen()}`;
  const main = require("../main.js");
  if (!REAL_POINTER) main._internals.setFakeCursor({ x: 700, y: 400 });
  await app.whenReady();
  const I = main._internals;
  while (!I.getOverlay()) await sleep(100);
  const overlay = I.getOverlay();
  await sleep(1200);
  const display = screen.getPrimaryDisplay().bounds;
  const backdrop = new BrowserWindow({ ...display, show: false, frame: false, focusable: false, skipTaskbar: true, hasShadow: false, webPreferences: { partition: "backdrop-test", sandbox: true } });
  const page = (bg, fg) => "data:text/html;charset=utf-8," + encodeURIComponent(`<!doctype html><body style="margin:0;background:${bg};color:${fg};font:700 40px sans-serif;display:grid;place-content:center;text-align:center">edge glow test<br><span style="font-size:18px;font-weight:400">the colour must feather inward from the screen edge, no hard line</span></body>`);
  for (const [name, bg, fg] of [["bright", "#f4f1e8", "#222"], ["dark", "#05060c", "#ddd"]]) {
    await backdrop.loadURL(page(bg, fg));
    backdrop.showInactive();
    await sleep(500);
    for (const state of ["listening", "thinking"]) {
      I.setOverlay(state);
      await sleep(900);
      await cap(`10-edge-glow-${state}-over-${name}`);
      if (state === "listening") { overlay.webContents.send("overlay:level", 1); await sleep(250); await cap(`10-edge-glow-listening-loud-over-${name}`); overlay.webContents.send("overlay:level", 0); }
    }
    I.setOverlay("off");
    await sleep(500);
  }
  console.log("[overlay] done", JSON.stringify({ bounds: overlay.getBounds(), display }));
  await backend.close();
  app.exit(0);
})().catch(error => { console.error(error); app.exit(1); });
