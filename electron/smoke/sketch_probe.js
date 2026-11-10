"use strict";
// Real-pointer probe of the minimap sketch surface via the REAL native helper path: `npx electron smoke/sketch_probe.js`.
// RUN BY THE SUPERVISOR, not by the agent (it moves the pointer with real CGEvents).
//
//   1. refuses to run (BLOCKED, exit 2) when CGSSessionScreenIsLocked == 1 (two S), IOConsoleLocked is Yes, or the screen capture is dark
//   2. builds electron/native/bin/chord_helper if missing and starts main.js against the fake backend WITH the helper; the chord itself is
//      SIMULATED in the chord state machine (a real Control+Option press needs a human); main then sends {"rect"} + {"capture":true} to the helper
//   3. a REAL OS drag (smoke/sketch_drag.swift) across the minimap rect must be consumed by the helper: strokes reach the fake backend, the
//      frontmost app (`lsappinfo front`, sampled every 100 ms for the whole run) never changes, no Electron window is focused
//   4. a "witness" tap downstream of the helper proves: the drag inside the rect was NOT delivered to any app, a click OUTSIDE the rect WAS
//      (and creates no stroke, and the frontmost app is unchanged)
//   5. the pointer is never moved by the app; the ghost cursor is a DOM element
//   Needs: Accessibility for the helper's parent chain (the app that launched this), Input Monitoring for the tap/witness.
//   FAILS (not skips) when the helper reports no accessibility: that is the fallback path, which cannot keep the game's focus.
const { app, nativeImage } = require("electron");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync, spawn } = require("node:child_process");
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
  delete process.env.VOI_SKETCH_NO_HELPER; // the real helper is the point of this probe
  const HELPER = path.join(__dirname, "..", "native", "bin", "chord_helper");
  if (!fs.existsSync(HELPER)) execFileSync("sh", [path.join(__dirname, "..", "native", "build.sh")], { stdio: "inherit", timeout: 180000 });
  const I = require("../main.js")._internals;
  await app.whenReady();
  if (SessionGuard.isBlocked(nativeImage)) { await backend.close(); app.exit(2); return; }
  const dragSource = path.join(__dirname, "sketch_drag.swift");
  // A stale cached binary without the witness mode made the outside-click checks fail (and the "consumed" check pass vacuously).
  if (!fs.existsSync(DRAG) || fs.statSync(DRAG).mtimeMs < fs.statSync(dragSource).mtimeMs) {
    execFileSync("swiftc", ["-O", dragSource, "-o", DRAG], { timeout: 120000, stdio: "inherit" });
  }
  while (!I.getSketch()) await sleep(50);
  const sketch = I.getSketch();
  await sleep(2000);
  const machine = sketch._test.machine;
  const snap0 = sketch.statusSnapshot();
  check("native helper is running", snap0.helper.state === "ok", JSON.stringify(snap0.helper));
  check("helper reports Accessibility granted (else this would be the focus-stealing fallback)", snap0.accessibility.granted === true, JSON.stringify({ granted: snap0.accessibility.granted }));
  const rect = snap0.calibration.rect;

  // sample the frontmost app for the whole run
  const front0 = frontmost();
  const seen = new Set([front0]);
  const sampler = setInterval(() => seen.add(frontmost()), 100);
  const mainFocus0 = I.getMain() ? I.getMain().isFocused() : false;
  const pointerBefore = pointer();

  const witness = spawn(DRAG, ["witness", "40"], { stdio: ["ignore", "pipe", "ignore"] });
  const witnessed = [];
  witness.stdout.on("data", chunk => { for (const line of String(chunk).split("\n")) if (line.trim()) witnessed.push(line.trim()); });
  await sleep(800);
  check("witness tap is up", witnessed.includes("witness ready"), witnessed[0] || "no output");

  machine.chord("down");
  await sleep(600);
  const win = sketch.getWindow();
  const snap1 = sketch.statusSnapshot();
  check("capture mode is native (helper owns the mouse)", snap1.captureMode === "native", `mode=${snap1.captureMode}`);
  const bounds = win && win.getBounds();
  check("window sits exactly over the minimap rect but is click-through", bounds && bounds.x === rect.x && bounds.y === rect.y && bounds.width === rect.w && bounds.height === rect.h, JSON.stringify({ bounds, rect }));
  check("window is visible but not focused", win && win.isVisible() && !win.isFocused());

  const at = (nx, ny) => `${Math.round(rect.x + nx * rect.w)},${Math.round(rect.y + ny * rect.h)}`;
  const path3 = [[0.15, 0.85], [0.15, 0.2], [0.8, 0.2]];
  const before = witnessed.length;
  execFileSync(DRAG, ["drag", ...path3.map(([x, y]) => at(x, y))], { timeout: 15000 });
  await sleep(500);
  const afterDrag = witnessed.slice(before);
  check("the drag inside the rect was consumed (no app received it)", afterDrag.length === 0, JSON.stringify(afterDrag.slice(0, 4)));

  // a click OUTSIDE the rect while capture is on must still reach the app below, and must not become a stroke
  const outside = `${Math.round(rect.x + rect.w + 120)},${Math.round(rect.y - 120)}`;
  const beforeOutside = witnessed.length;
  execFileSync(DRAG, ["click", outside], { timeout: 10000 });
  await sleep(500);
  const gotOutside = witnessed.slice(beforeOutside);
  check("a click outside the rect reached the app below (witness saw down+up)", gotOutside.some(l => l.startsWith("down ")) && gotOutside.some(l => l.startsWith("up ")), JSON.stringify(gotOutside));

  machine.chord("up");
  await sleep(2500);
  check("exactly one sketch POST reached the backend", backend.state.sketches.length === 1, `n=${backend.state.sketches.length}`);
  const sent = backend.state.sketches[0];
  if (sent) {
    check("exactly one stroke (the outside click made none)", sent.strokes.length === 1, `strokes=${sent.strokes.length}`);
    const pts = sent.strokes[0];
    const close = (p, q) => Math.abs(p[0] - q[0]) < 0.03 && Math.abs(p[1] - q[1]) < 0.03;
    check("first/last points match the dragged path (normalized to the frame)", close(pts[0], path3[0]) && close(pts[pts.length - 1], path3[2]), `${JSON.stringify(pts[0])} ${JSON.stringify(pts[pts.length - 1])}`);
    check("all points are inside 0..1 and time is monotonic", pts.every((p, i) => p[0] >= 0 && p[0] <= 1 && p[1] >= 0 && p[1] <= 1 && (i === 0 || p[2] >= pts[i - 1][2])));
    check("payload carries the rect and a null-or-png image", sent.minimap_rect.w === rect.w && (sent.image_png_b64 === null || sent.image_png_b64.length > 100), `image=${sent.image_png_b64 === null ? "null" : "png"}`);
  }
  check("capture released: the helper is no longer consuming (mode off)", sketch.statusSnapshot().captureMode === "off", sketch.statusSnapshot().captureMode);
  // after release a press INSIDE the old rect must pass through to the app below
  const beforeAfter = witnessed.length;
  execFileSync(DRAG, ["click", at(0.5, 0.5)], { timeout: 10000 });
  await sleep(500);
  check("after release a click inside the rect reaches the app again", witnessed.slice(beforeAfter).some(l => l.startsWith("down ")), JSON.stringify(witnessed.slice(beforeAfter)));

  clearInterval(sampler);
  check("frontmost app never changed during the whole run", seen.size === 1, `${JSON.stringify([...seen])}`);
  check("no Electron window took focus", (I.getMain() ? I.getMain().isFocused() : false) === mainFocus0 && !(win && win.isFocused()));
  const pointerRelease = pointer();
  await sleep(1500);
  check("the app never moved the pointer by itself", pointer() === pointerRelease, `${pointerRelease} -> ${pointer()} (before the drags: ${pointerBefore})`);
  await fetch(`${process.env.VOI_COMMANDER_URL}/__ctl/route`, { method: "POST", body: JSON.stringify({ event: { kind: "route_plan", plan_id: "plan-fx-1", route: { waypoints: [], final: { action: "move", nx: 0.8, ny: 0.2, label_ko: "끝점" } } } }) });
  await fetch(`${process.env.VOI_COMMANDER_URL}/__ctl/route`, { method: "POST", body: JSON.stringify({ event: { kind: "agent_cursor", plan_id: "plan-fx-1", nx: 0.8, ny: 0.2, action: "move", label_ko: "끝점" } }) });
  await sketch._test.feedPoll();
  await sleep(800);
  const ghost = await sketch.getWindow().webContents.executeJavaScript("(()=>{const g=document.getElementById('cursor');return {hidden:g.hidden,transform:g.style.transform}})()", true);
  check("ghost cursor is shown as a DOM element at the agent point", ghost.hidden === false && /translate\(/.test(ghost.transform), JSON.stringify(ghost));
  check("pointer still untouched by the ghost cursor", pointer() === pointerRelease);

  try { witness.kill(); } catch { /* done */ }
  const failed = checks.filter(c => !c.ok);
  console.log(`[sketch-probe] ${failed.length ? "FAIL" : "PASS"} ${checks.length - failed.length}/${checks.length} (load: ${os.loadavg()[0].toFixed(1)})`);
  await backend.close();
  app.exit(failed.length ? 1 : 0);
})().catch(error => { console.error("[sketch-probe] FAILED", error); app.exit(1); });
