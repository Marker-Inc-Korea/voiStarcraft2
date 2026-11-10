const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const H = require("../sketch/helper.js");

function fakeProc() {
  const proc = new EventEmitter();
  proc.stdout = new EventEmitter(); proc.stderr = new EventEmitter();
  proc.killed = false; proc.kill = () => { proc.killed = true; };
  return proc;
}
function rig(options = {}) {
  let t = 0;
  const timers = [];
  const procs = [];
  const log = [];
  const sup = H.createHelperSupervisor({
    command: "/x/chord_helper", args: [],
    spawn: () => { if (options.spawnThrows) { const e = new Error("spawn ENOENT"); e.code = "ENOENT"; throw e; } const p = fakeProc(); procs.push(p); return p; },
    onChord: d => log.push(["chord", d]), onInterrupt: () => log.push(["interrupt"]), onLost: reason => log.push(["lost", reason]), onStatus: s => log.push(["status", s.state]),
    now: () => t, setTimer: (fn, ms) => { const timer = { fn, at: t + ms, live: true }; timers.push(timer); return timer; }, clearTimer: timer => { if (timer) timer.live = false; },
    ...options.deps,
  });
  const advance = ms => { t += ms; for (const timer of timers.slice()) if (timer.live && timer.at <= t) { timer.live = false; timer.fn(); } };
  const feed = (proc, text) => proc.stdout.emit("data", Buffer.from(text));
  return { sup, procs, log, advance, feed };
}

test("line-delimited JSON is parsed across arbitrary chunk boundaries; garbage lines are ignored", () => {
  const { sup, procs, log, feed } = rig();
  sup.start();
  feed(procs[0], '{"status":"ready"}\n{"chord":"do'); feed(procs[0], 'wn"}\nnot json\n{"chord":"up"}\n{"key":"other"}\n{"chord":"sideways"}\n');
  assert.deepEqual(log, [["status", "starting"], ["status", "ok"], ["chord", "down"], ["chord", "up"], ["interrupt"]]);
});
test("a denied permission is reported once, does not hammer restarts, and retries slowly so a later grant works without relaunch", () => {
  const { sup, procs, log, feed, advance } = rig();
  sup.start();
  feed(procs[0], '{"status":"denied","reason":"input-monitoring"}\n');
  procs[0].emit("exit", 3, null);
  assert.equal(sup.status().state, "denied"); assert.equal(sup.status().reason, "input-monitoring");
  advance(1000); assert.equal(procs.length, 1, "no fast restart while denied");
  advance(H.DENIED_RETRY_MS); assert.equal(procs.length, 2, "slow retry");
  feed(procs[1], '{"status":"ready"}\n');
  assert.equal(sup.status().state, "ok");
});
test("a crash while the chord is held reports a loss (the sketch is cancelled) and restarts with backoff", () => {
  const { sup, procs, log, feed, advance } = rig();
  sup.start(); feed(procs[0], '{"status":"ready"}\n{"chord":"down"}\n');
  procs[0].emit("exit", 1, null);
  assert.deepEqual(log.filter(e => e[0] === "lost"), [["lost", "helper-exit"]]);
  assert.equal(sup.status().state, "starting");
  advance(H.BACKOFF_MS[0] - 1); assert.equal(procs.length, 1);
  advance(2); assert.equal(procs.length, 2);
});
test("restarts are bounded: too many crashes in the window -> failed, no more spawns until retry()", () => {
  const { sup, procs, advance } = rig();
  sup.start();
  for (let i = 0; i < H.MAX_RESTARTS + 2; i++) { const p = procs[procs.length - 1]; p.emit("exit", 1, null); advance(10000); }
  assert.equal(sup.status().state, "failed");
  const n = procs.length; advance(120000); assert.equal(procs.length, n);
  sup.retry(); assert.equal(procs.length, n + 1);
});
test("a missing binary is a distinct status (build step not run), not a crash loop", () => {
  const { sup, procs, advance } = rig({ spawnThrows: true });
  sup.start();
  assert.equal(sup.status().state, "missing"); advance(60000); assert.equal(procs.length, 0);
});
test("async spawn error event ENOENT also means missing", () => {
  const { sup, procs } = rig();
  sup.start(); const e = new Error("x"); e.code = "ENOENT"; procs[0].emit("error", e);
  assert.equal(sup.status().state, "missing");
});
test("stop() kills the child, cancels timers and never restarts", () => {
  const { sup, procs, advance } = rig();
  sup.start(); sup.stop();
  assert.equal(procs[0].killed, true); procs[0].emit("exit", null, "SIGTERM"); advance(120000);
  assert.equal(procs.length, 1); assert.equal(sup.status().state, "stopped");
});
test("quiet period resets the crash counter (a long-running helper that dies once is not 'failed')", () => {
  const { sup, procs, advance } = rig();
  sup.start();
  for (let i = 0; i < H.MAX_RESTARTS - 1; i++) { procs[procs.length - 1].emit("exit", 1, null); advance(10000); }
  advance(H.RESTART_WINDOW_MS + 1);
  procs[procs.length - 1].emit("exit", 1, null); advance(10000);
  assert.notEqual(sup.status().state, "failed");
});
