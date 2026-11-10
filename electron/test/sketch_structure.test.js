const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const root = path.join(__dirname, "..");
const read = (...p) => fs.readFileSync(path.join(root, ...p), "utf8");

test("sketch window is a frameless transparent non-focusable panel above the game; shown only with showInactive; never focused", () => {
  const src = read("sketch", "controller.js");
  for (const needle of ["frame: false", "transparent: true", "focusable: false", "hasShadow: false", 'setAlwaysOnTop(true, "screen-saver")', "visibleOnFullScreen: true", "setIgnoreMouseEvents(true, { forward: true })", 'type: "panel"', "win.setFocusable(false)"]) assert.ok(src.includes(needle), needle);
  assert.doesNotMatch(src, /\.focus\(\)|win\.show\(\)|moveTop\(|setFocusable\(true\)/);
  assert.match(src, /showInactive\(\)/);
  assert.match(src, /sandbox: true, contextIsolation: true, nodeIntegration: false/);
});
test("sketch window gets its own strict CSP (no network, no inline script) and main applies it to that window only", () => {
  const { SKETCH_CSP } = require("../sketch/controller.js");
  assert.match(SKETCH_CSP, /connect-src 'none'/); assert.match(SKETCH_CSP, /script-src 'self'/); assert.doesNotMatch(SKETCH_CSP, /unsafe/);
  const main = read("main.js");
  assert.match(main, /isSketch \? SKETCH_CSP/); assert.match(main, /require\("\.\/sketch\/controller\.js"\)/);
  const html = read("sketch", "window", "index.html"), js = read("sketch", "window", "sketch.js"), view = read("sketch", "window", "view.js");
  assert.doesNotMatch(html, /<script>[^<]|onclick=|style="/); assert.doesNotMatch(js + view, /innerHTML|eval\(|new Function|fetch\(|XMLHttpRequest|WebSocket\(/);
});
test("sketch IPC: the window can only send validated strokes/cancel/calibration and main checks the sender is the sketch window", () => {
  const main = read("main.js"), pre = read("sketch", "window", "preload.js");
  assert.match(main, /sketch\.ownsWebContents\(event\.sender\)/);
  for (const v of ["validateSketchStrokes", "validateSketchCancel", "validateSketchCalibration", "validateSketchAction"]) assert.match(main, new RegExp(v));
  assert.match(pre, /sketch:strokes/); assert.doesNotMatch(pre, /ipcRenderer\.invoke|require\("fs"\)/);
});
test("HUD exposes only the bare sketch actions", () => {
  const pre = read("preload.js");
  assert.match(pre, /sketch:action/); assert.match(pre, /\n  sketch,\n/);
});
test("the sketch POST goes through main's requestJson (token/origin guard shared with the other routes); the renderer has no network", () => {
  const ctl = read("sketch", "controller.js");
  assert.match(ctl, /requestJson\("\/api\/route\/sketch", \{ method: "POST"/);
  assert.doesNotMatch(read("renderer", "renderer.js"), /\/api\/route/);
});
test("settings: sketch group + minimap tab + Korean explanations are wired", () => {
  const rend = read("renderer", "renderer.js");
  assert.match(rend, /tab_sketch|"sketch"/); assert.match(rend, /tabSketch/); assert.match(rend, /api\.sketch\.action\("calibrate"\)/);
  const S = require("../renderer/settings.js");
  assert.throws(() => S.validatePatch({ sketch: { fallbackShortcut: "D" } })); // a bare key is not a valid accelerator
  assert.deepEqual(S.validatePatch({ sketch: { enabled: false } }), { sketch: { enabled: false } });
});
test("the chord is Control+Option only: the helper rejects Command/Shift in the chord and reports interruptions", () => {
  const swift = read("native", "chord_helper.swift");
  assert.match(swift, /\[\.maskControl, \.maskAlternate\]/); assert.match(swift, /\[\.maskCommand, \.maskShift\]/); assert.match(swift, /"\{\\"key\\":\\"other\\"\}"/);
});
test("route card in the board validates through the existing board protocol", () => {
  const P = require("../protocol.js"), M = require("../sketch/route_model.js");
  const m = M.createRouteModel({ now: () => 0 });
  m.ingest({ kind: "route_plan", plan_id: "p", route: { waypoints: [{ nx: 0.2, ny: 0.8, label_ko: "앞마당" }], final: { action: "attack", nx: 0.9, ny: 0.1, label_ko: "적" } } });
  const { open, ...card } = m.card();
  assert.doesNotThrow(() => P.validateBoardCards([card]));
});

test("main spawns the helper with a stdin pipe (capture/rect commands) and the settings tab shows the accessibility state", () => {
  assert.match(read("main.js"), /stdio: \["pipe", "pipe", "ignore"\]/);
  assert.match(read("renderer", "renderer.js"), /info\.accessibility/); assert.match(read("sketch", "controller.js"), /accessibilityMessage/);
});
