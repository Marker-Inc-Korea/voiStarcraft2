const test = require("node:test");
const assert = require("node:assert/strict");
const { createChordMachine } = require("../sketch/chord.js");

// Fake clock + timers so debounce is deterministic.
function rig(options = {}) {
  let t = 0;
  const timers = [];
  const log = [];
  const machine = createChordMachine({
    debounceMs: 120,
    now: () => t,
    setTimer: (fn, ms) => { const timer = { fn, at: t + ms, live: true }; timers.push(timer); return timer; },
    clearTimer: timer => { if (timer) timer.live = false; },
    onEvent: event => log.push(event),
    ...options,
  });
  const advance = ms => { t += ms; for (const timer of timers) if (timer.live && timer.at <= t) { timer.live = false; timer.fn(); } };
  return { machine, log, advance };
}
const kinds = log => log.map(e => e.type + (e.source ? `:${e.source}` : "") + (e.reason ? `:${e.reason}` : ""));

test("hold longer than the debounce starts a sketch; release ends it", () => {
  const { machine, log, advance } = rig();
  machine.chord("down");
  assert.equal(machine.state(), "pending");
  advance(119); assert.deepEqual(log, []);
  advance(2); assert.deepEqual(kinds(log), ["start:chord"]); assert.equal(machine.state(), "active");
  machine.chord("up");
  assert.deepEqual(kinds(log), ["start:chord", "end:chord"]); assert.equal(machine.state(), "idle");
});
test("a quick tap shorter than the debounce never starts a sketch", () => {
  const { machine, log, advance } = rig();
  machine.chord("down"); advance(60); machine.chord("up"); advance(500);
  assert.deepEqual(log, []); assert.equal(machine.state(), "idle");
});
test("another key before the debounce blocks the chord until it is released (Ctrl+Opt+D etc. are not sketches)", () => {
  const { machine, log, advance } = rig();
  machine.chord("down"); advance(50); machine.interrupt(); advance(500);
  assert.deepEqual(log, []); assert.equal(machine.state(), "blocked");
  machine.chord("down"); advance(500); assert.deepEqual(log, [], "a repeated down while blocked is ignored");
  machine.chord("up"); assert.equal(machine.state(), "idle");
  machine.chord("down"); advance(121); assert.deepEqual(kinds(log), ["start:chord"], "after a clean release the chord works again");
});
test("another key while sketching cancels (never ends = never executes)", () => {
  const { machine, log, advance } = rig();
  machine.chord("down"); advance(130); machine.interrupt();
  assert.deepEqual(kinds(log), ["start:chord", "cancel:chord:key"]); assert.equal(machine.state(), "blocked");
  machine.chord("up"); assert.deepEqual(kinds(log), ["start:chord", "cancel:chord:key"], "release after a cancel emits nothing");
  assert.equal(machine.state(), "idle");
});
test("duplicate down/up events are idempotent", () => {
  const { machine, log, advance } = rig();
  machine.chord("down"); machine.chord("down"); advance(200); machine.chord("down");
  machine.chord("up"); machine.chord("up");
  assert.deepEqual(kinds(log), ["start:chord", "end:chord"]);
});
test("UI cancel (Esc / click without drag) cancels once and waits for the release", () => {
  const { machine, log, advance } = rig();
  machine.chord("down"); advance(200); machine.cancel("esc"); machine.cancel("esc");
  assert.deepEqual(kinds(log), ["start:chord", "cancel:chord:esc"]);
  machine.chord("up"); assert.equal(machine.state(), "idle");
  machine.cancel("esc"); assert.equal(log.length, 2, "cancel while idle does nothing");
});
test("helper loss while sketching cancels (a sketch whose end we cannot see must not execute)", () => {
  const { machine, log, advance } = rig();
  machine.chord("down"); advance(200); machine.reset("helper-exit");
  assert.deepEqual(kinds(log), ["start:chord", "cancel:chord:helper-exit"]); assert.equal(machine.state(), "idle");
  machine.reset("helper-exit"); assert.equal(log.length, 2);
});
test("helper loss while only pending drops silently", () => {
  const { machine, log, advance } = rig();
  machine.chord("down"); machine.reset("helper-exit"); advance(500);
  assert.deepEqual(log, []); assert.equal(machine.state(), "idle");
});
test("fallback toggle: press starts, press ends, source is toggle", () => {
  const { machine, log } = rig();
  machine.toggle(); assert.deepEqual(kinds(log), ["start:toggle"]); assert.equal(machine.state(), "active");
  machine.toggle(); assert.deepEqual(kinds(log), ["start:toggle", "end:toggle"]); assert.equal(machine.state(), "idle");
});
test("toggle cancel returns straight to idle; a real chord release does not end a toggled sketch", () => {
  const { machine, log, advance } = rig();
  machine.toggle(); machine.chord("up"); assert.equal(machine.state(), "active");
  machine.cancel("esc"); assert.deepEqual(kinds(log), ["start:toggle", "cancel:toggle:esc"]); assert.equal(machine.state(), "idle");
});
test("toggle is ignored while the chord is pending/active/blocked (one sketch at a time)", () => {
  const { machine, log, advance } = rig();
  machine.chord("down"); machine.toggle(); assert.deepEqual(log, []);
  advance(200); machine.toggle(); assert.deepEqual(kinds(log), ["start:chord"]);
});
test("disabled machine ignores everything", () => {
  const { machine, log, advance } = rig();
  machine.setEnabled(false); machine.chord("down"); advance(500); machine.toggle();
  assert.deepEqual(log, []);
});
test("disabling mid-sketch cancels", () => {
  const { machine, log, advance } = rig();
  machine.chord("down"); advance(200); machine.setEnabled(false);
  assert.deepEqual(kinds(log), ["start:chord", "cancel:chord:disabled"]);
});
