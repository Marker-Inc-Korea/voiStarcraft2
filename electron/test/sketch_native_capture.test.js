const test = require("node:test");
const assert = require("node:assert/strict");
const N = require("../sketch/native_capture.js");

const RECT = { x: 100, y: 500, w: 200, h: 200 };

test("stdin protocol: capture and rect are single JSON lines; a bad rect is refused, never sent", () => {
  assert.equal(N.encodeCapture(true), '{"capture":true}\n');
  assert.equal(N.encodeCapture(false), '{"capture":false}\n');
  assert.equal(N.encodeRect(RECT), '{"rect":{"x":100,"y":500,"w":200,"h":200}}\n');
  assert.equal(N.encodeRect({ x: 10.4, y: 1.6, w: 40.2, h: 40.2 }), '{"rect":{"x":10,"y":2,"w":40,"h":40}}\n');
  for (const bad of [null, {}, { x: 1, y: 2, w: 0, h: 5 }, { x: NaN, y: 0, w: 5, h: 5 }, { x: 0, y: 0, w: -5, h: 5 }, "x"]) assert.throws(() => N.encodeRect(bad), /rect/);
});
test("stdout mouse messages are validated: only down/drag/up with finite coordinates", () => {
  assert.deepEqual(N.parseMouse({ mouse: "down", x: 150, y: 600, t: 12345.5 }), { kind: "down", x: 150, y: 600, t: 12345.5 });
  assert.deepEqual(N.parseMouse({ mouse: "drag", x: 1, y: 2 }), { kind: "drag", x: 1, y: 2, t: 0 });
  for (const bad of [null, {}, { mouse: "move", x: 1, y: 2 }, { mouse: "down", x: "1", y: 2 }, { mouse: "up", x: Infinity, y: 2 }, { mouse: "down", y: 2 }]) assert.equal(N.parseMouse(bad), null);
});
test("rect hit test is inclusive of the edges and exclusive outside", () => {
  assert.equal(N.inRect(RECT, 100, 500), true); assert.equal(N.inRect(RECT, 300, 700), true); assert.equal(N.inRect(RECT, 301, 600), false); assert.equal(N.inRect(RECT, 150, 499), false);
});
test("a native stroke session turns screen points into frame-normalized stroke steps", () => {
  const s = N.createStrokeSession(RECT);
  assert.deepEqual(s.feed({ kind: "down", x: 150, y: 600, t: 1000 }), { step: "begin", nx: 0.25, ny: 0.5, t: 1000 });
  assert.deepEqual(s.feed({ kind: "drag", x: 200, y: 550, t: 1100 }), { step: "move", nx: 0.5, ny: 0.25, t: 1100 });
  assert.deepEqual(s.feed({ kind: "up", x: 250, y: 520, t: 1200 }), { step: "end", nx: 0.75, ny: 0.1, t: 1200 });
  assert.equal(s.grabbing(), false);
});
test("a press outside the rect never begins a stroke (the helper lets those pass to the app below)", () => {
  const s = N.createStrokeSession(RECT);
  assert.equal(s.feed({ kind: "down", x: 50, y: 600, t: 1 }), null);
  assert.equal(s.feed({ kind: "drag", x: 150, y: 600, t: 2 }), null, "a drag with no accepted press is ignored");
  assert.equal(s.feed({ kind: "up", x: 150, y: 600, t: 3 }), null);
});
test("dragging out of the rect keeps the stroke, clamped to the frame edge; release outside ends it", () => {
  const s = N.createStrokeSession(RECT);
  s.feed({ kind: "down", x: 290, y: 600, t: 1 });
  assert.deepEqual(s.feed({ kind: "drag", x: 400, y: 800, t: 2 }), { step: "move", nx: 1, ny: 1, t: 2 });
  assert.deepEqual(s.feed({ kind: "up", x: 400, y: 800, t: 3 }), { step: "end", nx: 1, ny: 1, t: 3 });
});
test("a second press while grabbing is ignored; the session can start a new stroke after release", () => {
  const s = N.createStrokeSession(RECT);
  s.feed({ kind: "down", x: 150, y: 600, t: 1 });
  assert.equal(s.feed({ kind: "down", x: 160, y: 610, t: 2 }), null);
  s.feed({ kind: "up", x: 160, y: 610, t: 3 });
  assert.equal(s.feed({ kind: "down", x: 170, y: 620, t: 4 }).step, "begin");
});
test("capture state: pending until the helper acks; unavailable falls back to the panel; off waits for the release of a grabbed press", () => {
  const c = N.createCaptureState();
  assert.equal(c.mode(), "off");
  c.request(true); assert.equal(c.mode(), "pending");
  c.ack("on"); assert.equal(c.mode(), "native");
  c.mouse({ kind: "down" }); c.release(); assert.equal(c.mode(), "draining", "stay armed until the press that was consumed is released");
  c.mouse({ kind: "up" }); assert.equal(c.mode(), "off");
  const d = N.createCaptureState(); d.request(true); d.ack("unavailable"); assert.equal(d.mode(), "fallback");
  d.release(); assert.equal(d.mode(), "off");
  const e = N.createCaptureState(); e.request(true); e.ack("on"); e.release(); assert.equal(e.mode(), "off", "no press in flight: immediately off");
});
test("reset (helper died) drops straight to off, even with a consumed press in flight", () => {
  const c = N.createCaptureState(); c.request(true); c.ack("on"); c.mouse({ kind: "down" }); c.reset();
  assert.equal(c.mode(), "off");
});
test("when accessibility is known to be missing the native path is skipped up front", () => {
  const c = N.createCaptureState();
  c.setAccessibility(false); c.request(true); assert.equal(c.mode(), "fallback");
  c.release();
  c.setAccessibility(true); c.request(true); assert.equal(c.mode(), "pending");
  assert.equal(N.createCaptureState().accessibility(), "unknown");
});
