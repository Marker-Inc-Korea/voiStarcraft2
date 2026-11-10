"use strict";
// Real-pointer probe of the minimap sketch surface: `npx electron smoke/sketch_probe.js`.  RUN BY THE SUPERVISOR, not by the agent.
//
//   1. refuses to run (BLOCKED, exit 2) when CGSSessionScreenIsLocked == 1 (two S), IOConsoleLocked is Yes, or the screen capture is dark
//   2. starts main.js against the fake backend with the chord helper DISABLED and drives the chord state machine directly (the chord is
//      SIMULATED; the real Control+Option path needs the Input Monitoring grant and is checked separately by hand)
//   3. while the simulated chord is "held": window bounds must equal the minimap rect, then a REAL OS drag (smoke/sketch_drag.swift) draws an
//      L-shaped stroke across the rect; the frontmost app (lsappinfo) and our windows' focus must never change
//   4. releases the chord: the fake backend must have received exactly one POST with strokes inside 0..1 that match the dragged path (+-3 %)
//   5. verifies the app never moved the pointer by itself (position unchanged for 1.5 s after the release) and that the ghost cursor is a DOM element
//   swiftc -O smoke/sketch_drag.swift -o /tmp/voi_sketch_drag   (built on demand)
const { app, nativeImage } = require("electron");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const SessionGuard = require("./session_guard.js");

app.setPath("userData", fs.mkdtempSync(path.join(os.tmpdir(), "voi-sketch-probe-")));
const DRAG = process.env.SKETCH_DRAG || "/tmp/voi_sketch_drag";
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const checks = [];
const check = (name, ok, detail = "") => { checks.push({ name, ok: Boolean(ok), detail }); console.log(`[sketch-probe] ${ok ? "PASS" : "FAIL"} ${name}${detail ? ` ${detail}` : ""}`); };
const frontmost = () => { try { return execFileSync("lsappinfo", ["front"], { encoding: "utf8", timeout: 3000 }).trim(); } catch { return "unknown"; } };
const pointer = () => execFileSync(DRAG, ["pos"], { encoding: "utf8", timeout: 3000 }).trim();

(async () => {
  const { createFakeBackend } = require("../fixtures/fake_backend.js");
  const backend = createFakeBackend({ port: 0 });
  process.env.VOI_COMMANDER_URL = `http://127.0.0.1:${await backend.listen()}`;
  process.env.VOI_SKETCH_NO_HELPER = "1"; // the chord is simulated below
  const I = require("../main.js")._internals;
  await app.whenReady();
  if (SessionGuard.isBlocked(nativeImage)) { await backend.close(); app.exit(2); return; }
  if (!fs.existsSync(DRAG)) execFileSync("swiftc", ["-O", path.join(__dirname, "sketch_drag.swift"), "-o", DRAG], { timeout: 120000, stdio: "inherit" });
  while (!I.getSketch()) await sleep(50);
  const sketch = I.getSketch();
  await sleep(1500);
  const machine = sketch._test.machine;
  const rect = sketch.statusSnapshot().calibration.rect;
  const frontBefore = frontmost();
  const focusBefore = I.getMain() ? I.getMain().isFocused() : false;
  const pointerBefore = pointer();

  machine.chord("down");
  await sleep(400);
  const win = sketch.getWindow();
  const bounds = win && win.getBounds();
  check("window appears exactly over the minimap rect", bounds && bounds.x === rect.x && bounds.y === rect.y && bounds.width === rect.w && bounds.height === rect.h, JSON.stringify({ bounds, rect }));
  check("window is visible but not focused", win && win.isVisible() && !win.isFocused());

  const at = (nx, ny) => `${Math.round(rect.x + nx * rect.w)},${Math.round(rect.y + ny * rect.h)}`;
  const path3 = [[0.15, 0.85], [0.15, 0.2], [0.8, 0.2]];
  execFileSync(DRAG, ["drag", ...path3.map(([x, y]) => at(x, y))], { timeout: 15000 });
  await sleep(300);
  check("frontmost app did not change while drawing", frontmost() === frontBefore, `${frontBefore} -> ${frontmost()}`);
  check("main window never took focus", (I.getMain() ? I.getMain().isFocused() : false) === focusBefore);

  machine.chord("up");
  await sleep(2500);
  check("exactly one sketch POST reached the backend", backend.state.sketches.length === 1, `n=${backend.state.sketches.length}`);
  const sent = backend.state.sketches[0];
  if (sent) {
    const pts = sent.strokes[0];
    const close = (p, q) => Math.abs(p[0] - q[0]) < 0.03 && Math.abs(p[1] - q[1]) < 0.03;
    check("first/last points match the dragged path (normalized to the frame)", close(pts[0], path3[0]) && close(pts[pts.length - 1], path3[2]), `${JSON.stringify(pts[0])} ${JSON.stringify(pts[pts.length - 1])}`);
    check("all points are inside 0..1 and time is monotonic", pts.every((p, i) => p[0] >= 0 && p[0] <= 1 && p[1] >= 0 && p[1] <= 1 && (i === 0 || p[2] >= pts[i - 1][2])));
    check("payload carries the rect and a null-or-png image", sent.minimap_rect.w === rect.w && (sent.image_png_b64 === null || sent.image_png_b64.length > 100), `image=${sent.image_png_b64 === null ? "null" : "png"}`);
  }
  check("after release the surface is click-through again", win && !win.isDestroyed() && sketch._test.state().drawing === false);
  const pointerRelease = pointer();
  await sleep(1500);
  check("the app never moved the pointer by itself", pointer() === pointerRelease, `${pointerRelease} -> ${pointer()} (before the drag: ${pointerBefore})`);
  // ghost cursor: scripted route events; the pointer must stay put while the ghost glides
  await fetch(`${process.env.VOI_COMMANDER_URL}/__ctl/route`, { method: "POST", body: JSON.stringify({ event: { kind: "route_plan", plan_id: "plan-fx-1", route: { waypoints: [], final: { action: "move", nx: 0.8, ny: 0.2, label_ko: "끝점" } } } }) });
  await fetch(`${process.env.VOI_COMMANDER_URL}/__ctl/route`, { method: "POST", body: JSON.stringify({ event: { kind: "agent_cursor", plan_id: "plan-fx-1", nx: 0.8, ny: 0.2, action: "move", label_ko: "끝점" } }) });
  await sketch._test.feedPoll();
  await sleep(800);
  const ghost = await win.webContents.executeJavaScript("(()=>{const g=document.getElementById('cursor');return {hidden:g.hidden,transform:g.style.transform}})()", true);
  check("ghost cursor is shown as a DOM element at the agent point", ghost.hidden === false && /translate\(/.test(ghost.transform), JSON.stringify(ghost));
  check("pointer still untouched by the ghost cursor", pointer() === pointerRelease);

  const failed = checks.filter(c => !c.ok);
  console.log(`[sketch-probe] ${failed.length ? "FAIL" : "PASS"} ${checks.length - failed.length}/${checks.length} (load: ${os.loadavg()[0].toFixed(1)})`);
  await backend.close();
  app.exit(failed.length ? 1 : 0);
})().catch(error => { console.error("[sketch-probe] FAILED", error); app.exit(1); });
