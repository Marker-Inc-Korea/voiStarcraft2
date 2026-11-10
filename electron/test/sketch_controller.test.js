const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { createSketchController, CHANNELS } = require("../sketch/controller.js");

const DISPLAY = { id: 1, scaleFactor: 2, bounds: { x: 0, y: 0, width: 1512, height: 982 }, workArea: { x: 0, y: 38, width: 1512, height: 944 } };
const SETTINGS = { glass: { skin: "cosmic", reduceMotion: false }, sketch: { enabled: true, fallbackShortcut: "Control+Alt+D", captureImage: true } };
const STROKE = [[0.1, 0.9, 0], [0.5, 0.5, 300], [0.9, 0.1, 600]];

class FakeWin extends EventEmitter {
  constructor(options) {
    super(); this.options = options; this.calls = []; this.handlers = {}; this.visible = false; this.bounds = { x: 0, y: 0, width: 0, height: 0 }; this.sent = [];
    this.webContents = { isDestroyed: () => false, isLoading: () => false, send: (channel, payload) => this.sent.push([channel, payload]), setWindowOpenHandler() {}, on: (name, fn) => { this.handlers[name] = fn; } };
  }
  setAlwaysOnTop(...a) { this.calls.push(["setAlwaysOnTop", ...a]); } setVisibleOnAllWorkspaces() {} setFocusable(v) { this.calls.push(["setFocusable", v]); }
  setIgnoreMouseEvents(...a) { this.calls.push(["ignore", ...a]); this.ignore = a[0]; }
  setBounds(b) { this.calls.push(["setBounds", b]); this.bounds = { ...b }; } getBounds() { return { ...this.bounds }; }
  showInactive() { this.calls.push(["showInactive"]); this.visible = true; } show() { this.calls.push(["show"]); this.visible = true; }
  focus() { this.calls.push(["focus"]); } moveTop() { this.calls.push(["moveTop"]); } hide() { this.calls.push(["hide"]); this.visible = false; }
  isVisible() { return this.visible; } isDestroyed() { return Boolean(this.gone); } destroy() { this.gone = true; } loadFile() { if (this.handlers["did-finish-load"]) this.handlers["did-finish-load"](); }
  sentTo(channel) { return this.sent.filter(s => s[0] === channel).map(s => s[1]); }
}
function rig(over = {}) {
  let t = 1000;
  const timers = [];
  const wins = [], posts = [], hud = [], globals = new Map(), fsWrites = {};
  const settings = JSON.parse(JSON.stringify(SETTINGS));
  const ctl = createSketchController({
    BrowserWindow: class extends FakeWin { constructor(o) { super(o); wins.push(this); } },
    getDisplays: () => [DISPLAY], getCursor: () => ({ x: 400, y: 400 }), getSettings: () => settings,
    requestJson: async (url, opts) => { if (over.failPost && url === "/api/route/sketch") throw new Error("Commander HTTP 500"); posts.push([url, opts && opts.body ? JSON.parse(opts.body) : null]); if (url.startsWith("/api/route/events")) return { events: [], latest: 0 }; return over.answer || { kind: "none", plan_id: "p", narration_ko: "알아볼 수 없습니다", question_ko: null, route: null, squad: null }; },
    pushHud: m => hud.push(m), registerGlobal: (a, cb) => { globals.set(a, cb); return true; }, unregisterGlobal: a => globals.delete(a),
    captureDeps: () => ({ getAccess: () => over.access || "denied", getSources: async () => [] }),
    spawn: () => { const p = new EventEmitter(); p.stdout = new EventEmitter(); p.kill = () => {}; wins.proc = p; return p; },
    helperPath: "/x/chord_helper", storeFile: "/tmp/none/sketch.json", fs: { mkdirSync() {}, writeFileSync: (f, d) => { fsWrites[f] = d; }, readFileSync: () => { throw new Error("none"); } },
    windowFile: "/x/index.html", preloadFile: "/x/preload.js", now: () => t,
    setTimer: (fn, ms) => { const timer = { fn, at: t + ms, live: true }; timers.push(timer); return timer; }, clearTimer: timer => { if (timer) timer.live = false; },
    collectTimeoutMs: 800, settleMs: 0,
  });
  const advance = async ms => { t += ms; for (const timer of timers.slice()) if (timer.live && timer.at <= t) { timer.live = false; timer.fn(); } await new Promise(r => setImmediate(r)); };
  const flush = async () => { for (let i = 0; i < 10; i++) await advance(1); };
  // the renderer answers `collect` with its strokes
  const answerCollect = strokes => { const w = wins[0]; const orig = w.webContents.send; w.webContents.send = (c, p) => { orig(c, p); if (c === CHANNELS.collect) setImmediate(() => ctl.handleStrokes({ strokes })); }; };
  return { ctl, wins, posts, hud, globals, fsWrites, settings, advance, flush, answerCollect, now: () => t };
}
const drawOnce = async (r, strokes = [STROKE]) => {
  r.ctl.start(); r.ctl._test.markLoaded();
  r.ctl._test.machine.chord("down"); await r.advance(200);
  r.answerCollect(strokes);
  r.ctl._test.machine.chord("up"); await r.flush();
};

test("while the chord is held the window is EXACTLY the minimap rect, accepts the mouse and is shown inactive; nothing ever takes focus", async () => {
  const r = rig(); r.ctl.start();
  r.ctl._test.machine.chord("down"); await r.advance(200);
  const w = r.wins[0];
  const rect = r.ctl.statusSnapshot().calibration.rect;
  assert.deepEqual(w.bounds, { x: rect.x, y: rect.y, width: rect.w, height: rect.h });
  assert.equal(w.ignore, false, "mouse events accepted only now"); assert.ok(w.calls.some(c => c[0] === "showInactive"));
  for (const bad of ["focus", "show", "moveTop"]) assert.ok(!w.calls.some(c => c[0] === bad), `${bad} must never be called`);
  assert.equal(w.options.focusable, false); assert.equal(w.options.type, process.platform === "darwin" ? "panel" : undefined);
  assert.deepEqual(w.sentTo(CHANNELS.mode).pop(), { mode: "draw" });
});
test("releasing the chord turns the surface click-through at once, then POSTs strokes + transcript with a null image (no capture permission)", async () => {
  const r = rig({ access: "denied" });
  r.ctl.noteTranscript(1, "앞마당으로 가서 공격");
  await drawOnce(r);
  const w = r.wins[0];
  assert.equal(w.ignore, true);
  const post = r.posts.find(p => p[0] === "/api/route/sketch");
  assert.ok(post, "posted");
  assert.deepEqual(Object.keys(post[1]).sort(), ["image_png_b64", "minimap_rect", "strokes", "transcript", "update_id"]);
  assert.equal(post[1].image_png_b64, null); assert.deepEqual(post[1].strokes, [STROKE]); assert.equal(post[1].transcript, "앞마당으로 가서 공격");
  assert.match(r.hud.find(h => h.type === "sketch-result" && h.kind === "sent").text, /해석/);
  for (const bad of ["focus", "show", "moveTop"]) assert.ok(!w.calls.some(c => c[0] === bad));
});
test("the answer text is shown in the HUD; an ask keeps the strokes visible, faded; none says nothing was done", async () => {
  const r = rig({ answer: { kind: "ask", plan_id: "p", narration_ko: "", question_ko: "어느 쪽 길로 갈까요?", route: null, squad: null } });
  await drawOnce(r);
  const last = r.hud.filter(h => h.type === "sketch-result").pop();
  assert.equal(last.kind, "ask"); assert.equal(last.text, "어느 쪽 길로 갈까요?");
  const sub = r.wins[0].sentTo(CHANNELS.submitted);
  assert.ok(sub.length >= 1 && sub.every(s => s.strokes.length === 1));
  assert.ok(r.ctl._test.model.view(r.now()).strokes, "strokes stay until a plan arrives");
  assert.ok(r.wins[0].visible);
});
test("an execute answer WITHOUT a route is never reported as execute", async () => {
  const r = rig({ answer: { kind: "execute", plan_id: "p", narration_ko: "공격합니다", question_ko: null, route: null, squad: null } });
  await drawOnce(r);
  const last = r.hud.filter(h => h.type === "sketch-result").pop();
  assert.equal(last.kind, "invalid"); assert.match(last.text, /아무것도 하지 않았습니다/);
});
test("a failed POST says nothing was executed and never claims success", async () => {
  const r = rig({ failPost: true });
  await drawOnce(r);
  const last = r.hud.filter(h => h.type === "sketch-result").pop();
  assert.equal(last.kind, "failed"); assert.match(last.text, /전송하지 못/); assert.match(last.text, /아무것도 실행하지 않/);
});
test("a chord released with no strokes drawn posts nothing and says so", async () => {
  const r = rig(); await drawOnce(r, []);
  assert.equal(r.posts.filter(p => p[0] === "/api/route/sketch").length, 0);
  assert.match(r.hud.filter(h => h.type === "sketch-result").pop().text, /선이 없/);
});
test("another key / Esc while drawing cancels: nothing is POSTed and the HUD says cancelled", async () => {
  const r = rig(); r.ctl.start(); r.ctl._test.markLoaded();
  r.ctl._test.machine.chord("down"); await r.advance(200);
  r.ctl._test.machine.interrupt(); r.ctl._test.machine.chord("up"); await r.flush();
  assert.equal(r.posts.filter(p => p[0] === "/api/route/sketch").length, 0);
  assert.match(r.hud.filter(h => h.type === "sketch-result").pop().text, /취소/);
  assert.equal(r.wins[0].ignore, true);
});
test("a click without a drag reported by the window cancels the sketch", async () => {
  const r = rig(); r.ctl.start(); r.ctl._test.markLoaded();
  r.ctl._test.machine.chord("down"); await r.advance(200);
  r.ctl.handleCancel("tap");
  r.ctl._test.machine.chord("up"); await r.flush();
  assert.equal(r.posts.filter(p => p[0] === "/api/route/sketch").length, 0);
});
test("helper loss while drawing cancels the sketch (a release we cannot see must not execute)", async () => {
  const r = rig(); r.ctl.start(); r.ctl._test.markLoaded();
  r.ctl._test.machine.chord("down"); await r.advance(200);
  r.ctl._test.helper.status(); r.ctl._test.machine.reset("helper-exit"); await r.flush();
  assert.equal(r.posts.filter(p => p[0] === "/api/route/sketch").length, 0);
  assert.match(r.hud.filter(h => h.type === "sketch-result").pop().text, /취소/);
});
test("the renderer never answering `collect` times out into a no-strokes result (no POST)", async () => {
  const r = rig(); r.ctl.start(); r.ctl._test.markLoaded();
  r.ctl._test.machine.chord("down"); await r.advance(200);
  r.ctl._test.machine.chord("up"); await r.advance(900); await r.flush();
  assert.equal(r.posts.filter(p => p[0] === "/api/route/sketch").length, 0);
});
test("malformed strokes from the window are rejected, not repaired", async () => {
  const r = rig(); await drawOnce(r, [[[2, 0.5, 0], [0.5, 0.5, 10]]]);
  assert.equal(r.posts.filter(p => p[0] === "/api/route/sketch").length, 0);
  assert.match(r.hud.filter(h => h.type === "sketch-result").pop().text, /전송하지 못/);
});
test("route events from the feed reach the window and the HUD; a route_plan replaces the drawn strokes and shows the display window click-through", async () => {
  const r = rig({ answer: { kind: "execute", plan_id: "p1", narration_ko: "공격합니다", question_ko: null, route: { waypoints: [], final: { action: "attack", nx: 0.9, ny: 0.1, label_ko: "적" } }, squad: null } });
  await drawOnce(r);
  r.ctl._test.model.ingest({ kind: "route_plan", plan_id: "p1", route: { waypoints: [], final: { action: "attack", nx: 0.9, ny: 0.1, label_ko: "적" } } });
  assert.equal(r.ctl._test.model.view(r.now()).strokes, null);
  assert.ok(r.ctl._test.model.card());
});
test("toggle fallback shortcut starts and ends a sketch; Esc is claimed only while toggled", async () => {
  const r = rig(); r.ctl.start(); r.ctl._test.markLoaded();
  assert.ok(r.globals.has("Control+Alt+D"), "fallback registered");
  r.globals.get("Control+Alt+D")();
  assert.ok(r.globals.has("Escape")); assert.equal(r.ctl.statusSnapshot().active, true);
  r.answerCollect([STROKE]); r.globals.get("Control+Alt+D")(); await r.flush();
  assert.ok(!r.globals.has("Escape"));
  assert.equal(r.posts.filter(p => p[0] === "/api/route/sketch").length, 1);
});
test("disabling the feature mid-sketch cancels and unregisters the fallback", async () => {
  const r = rig(); r.ctl.start(); r.ctl._test.markLoaded();
  r.ctl._test.machine.chord("down"); await r.advance(200);
  r.settings.sketch.enabled = false; r.ctl.configure(); await r.flush();
  assert.equal(r.posts.filter(p => p[0] === "/api/route/sketch").length, 0); assert.ok(!r.globals.has("Control+Alt+D"));
});
test("calibration: the window covers the display, accepts the mouse, saves a display+resolution keyed rect, then goes click-through", async () => {
  const r = rig(); r.ctl.start(); r.ctl._test.markLoaded();
  assert.equal(r.ctl.statusSnapshot().calibration.calibrated, false);
  assert.equal(r.ctl.startCalibration(), true);
  const w = r.wins[0];
  assert.deepEqual(w.bounds, { x: 0, y: 0, width: 1512, height: 982 }); assert.equal(w.ignore, false);
  r.ctl.handleCalibration({ action: "save", rect: { x: 12, y: 700, w: 260, h: 260 } });
  assert.equal(w.ignore, true);
  const snap = r.ctl.statusSnapshot().calibration;
  assert.equal(snap.calibrated, true); assert.deepEqual(snap.rect, { x: 12, y: 700, w: 260, h: 260 });
  assert.ok(Object.keys(r.fsWrites).length === 1 && JSON.parse(Object.values(r.fsWrites)[0]).rects["1@1512x982@2"]);
  r.ctl.resetCalibration(); assert.equal(r.ctl.statusSnapshot().calibration.calibrated, false);
});
test("chords are ignored while calibrating", async () => {
  const r = rig(); r.ctl.start(); r.ctl._test.markLoaded(); r.ctl.startCalibration();
  r.ctl._test.machine.chord("down"); await r.advance(200);
  assert.equal(r.ctl._test.state().drawing, false);
});
