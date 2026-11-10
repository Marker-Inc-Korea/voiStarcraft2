"use strict";
// Headless render (no pointer, no real window on screen) of the minimap route display with a sample route, reached markers, a ghost
// cursor and the drawn strokes, in both skins: `npx electron smoke/route_shots.js [outDir]`. The page backdrop is a synthetic
// minimap-like picture injected for the capture only. Output: <outDir>/route-<skin>-{strokes,route}.png
const { app, BrowserWindow } = require("electron");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
app.setPath("userData", fs.mkdtempSync(path.join(os.tmpdir(), "voi-route-shots-")));
const OUT = process.argv[2] ? path.resolve(process.argv[2]) : fs.mkdtempSync(path.join(os.tmpdir(), "voi-route-png-"));
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const SIDE = 260, PAD = 36;
const LAYOUT = { mode: "display", ox: PAD, oy: PAD, w: SIDE, h: SIDE, width: SIDE + 2 * PAD, height: SIDE + 2 * PAD };
const STROKES = [[[0.12, 0.84, 0], [0.3, 0.8, 150], [0.5, 0.52, 380], [0.62, 0.5, 520], [0.78, 0.22, 800], [0.88, 0.14, 900]]];
const PLAN = { kind: "route_plan", plan_id: "p1", route: { waypoints: [{ nx: 0.2, ny: 0.82, label_ko: "앞마당" }, { nx: 0.52, ny: 0.5, label_ko: "맵 중앙" }, { nx: 0.74, ny: 0.3, label_ko: "적 3번째" }], final: { action: "attack", nx: 0.88, ny: 0.14, label_ko: "적 본진" } }, narration_ko: "중앙을 지나 적 본진을 공격합니다" };
const BACKDROP = `html{background:#0a0f1c !important} body{background:radial-gradient(circle at 30% 70%, #1d4b2e 0 14%, transparent 15%), radial-gradient(circle at 80% 18%, #5a2630 0 12%, transparent 13%), radial-gradient(circle at 50% 50%, #26365c 0 22%, transparent 23%), #101828 !important}`;

app.whenReady().then(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const written = [];
  const win = new BrowserWindow({ width: LAYOUT.width, height: LAYOUT.height, show: false, frame: false, transparent: false, webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, preload: path.join(__dirname, "..", "sketch", "window", "preload.js"), backgroundThrottling: false } });
  for (const skin of ["cosmic", "terminal"]) {
    await win.loadFile(path.join(__dirname, "..", "sketch", "window", "index.html")); // fresh state per skin (same window: a second window failed to load under load)
    await win.webContents.insertCSS(BACKDROP);
    const send = (channel, payload) => win.webContents.send(channel, payload);
    send("sketch:skin", { skin, reduceMotion: true });
    send("sketch:layout", LAYOUT);
    // 1) the user's drawing: bright line, arrow head, dots at corners
    send("sketch:mode", { mode: "idle" });
    send("sketch:submitted", { planId: "p1", strokes: STROKES, bright: true });
    await sleep(500);
    fs.writeFileSync(path.join(OUT, `route-${skin}-strokes.png`), (await win.webContents.capturePage()).toPNG());
    // 2) the planned route replaces the strokes; first waypoint reached, ghost cursor heading to the second
    send("sketch:route-event", PLAN);
    send("sketch:route-event", { kind: "route_progress", plan_id: "p1", reached_index: 0, done: false, released: false });
    send("sketch:route-event", { kind: "agent_cursor", plan_id: "p1", nx: 0.52, ny: 0.5, action: "move", label_ko: "맵 중앙으로 이동" });
    await sleep(900);
    fs.writeFileSync(path.join(OUT, `route-${skin}-route.png`), (await win.webContents.capturePage()).toPNG());
    // 3) attack order at the final point
    send("sketch:route-event", { kind: "route_progress", plan_id: "p1", reached_index: 2, done: false, released: false });
    send("sketch:route-event", { kind: "agent_cursor", plan_id: "p1", nx: 0.88, ny: 0.14, action: "attack", label_ko: "공격" });
    await sleep(900);
    fs.writeFileSync(path.join(OUT, `route-${skin}-attack.png`), (await win.webContents.capturePage()).toPNG());
    written.push(skin);
  }
  win.destroy();
  console.log(`[route-shots] ${written.join(",")} -> ${OUT}`);
  app.exit(0);
}).catch(error => { console.error(error); app.exit(1); });
