"use strict";
// Real-click focus probe: `npx electron smoke/focus_probe.js`. Moves the REAL pointer onto #cmd-input, clicks with a REAL CoreGraphics
// event (smoke/oscontrol.swift -> /tmp/oscontrol), types with real key events and reads what arrived. Pass = the typed text is in the input.
// Refuses to run on a locked/dark screen (BLOCKED, exit 2) because synthetic input is dropped there.
//   swiftc -O smoke/oscontrol.swift -o /tmp/oscontrol ; swiftc -O smoke/cursor_daemon.swift -o /tmp/cursor_daemon
const { app, nativeImage, screen } = require("electron");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const SessionGuard = require("./session_guard.js");
// Takes over the user's real mouse and keyboard, so it runs only when asked for explicitly (the default smoke uses the second cursor).
if (process.env.REAL_POINTER !== "1") { console.log("[focus-probe] BLOCKED: set REAL_POINTER=1 (moves your real mouse)"); process.exit(2); }

app.setPath("userData", fs.mkdtempSync(path.join(os.tmpdir(), "voi-focus-probe-")));
const OSCONTROL = process.env.OSCONTROL || "/tmp/oscontrol";
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const TYPED = "hold";

(async () => {
  const { createFakeBackend } = require("../fixtures/fake_backend.js");
  const backend = createFakeBackend({ port: 0 });
  process.env.VOI_COMMANDER_URL = `http://127.0.0.1:${await backend.listen()}`;
  const I = require("../main.js")._internals;
  await app.whenReady();
  if (SessionGuard.isBlocked(nativeImage)) { await backend.close(); app.exit(2); return; }
  const display = screen.getPrimaryDisplay().bounds;
  const center = { x: display.x + Math.round(display.width / 2), y: display.y + Math.round(display.height / 2) };
  execFileSync(OSCONTROL, ["warp", String(center.x), String(center.y)]);
  while (!I.getMain()) await sleep(50);
  await sleep(2500);
  I.applySettings({ layout: { pinned: true } });
  await sleep(2500);
  const win = I.getMain();
  const js = code => win.webContents.executeJavaScript(code, true);
  const rect = await js("(()=>{const b=document.getElementById('cmd-input').getBoundingClientRect();return {x:b.x+b.width/2,y:b.y+b.height/2}})()");
  const bounds = win.getBounds();
  const point = { x: Math.round(bounds.x + rect.x), y: Math.round(bounds.y + rect.y) };
  execFileSync(OSCONTROL, ["warp", String(point.x), String(point.y)]);
  await sleep(500);
  const hover = I.dockInfo();
  execFileSync(OSCONTROL, ["click", String(point.x), String(point.y)]);
  await sleep(600);
  execFileSync(OSCONTROL, ["type", TYPED]);
  await sleep(500);
  const value = await js("document.getElementById('cmd-input').value");
  const docFocus = await js("document.hasFocus() && document.activeElement && document.activeElement.id");
  const ok = value === TYPED && !hover.focused;
  console.log(`[focus-probe] ${ok ? "PASS" : "FAIL"} hoverFocused=${hover.focused} typed=${JSON.stringify(value)} docFocus=${JSON.stringify(docFocus)} dock=${JSON.stringify({ focusable: I.dockInfo().focusable, focused: I.dockInfo().focused })}`);
  await backend.close();
  app.exit(ok ? 0 : 1);
})().catch(error => { console.error("[focus-probe] FAILED", error); app.exit(1); });
