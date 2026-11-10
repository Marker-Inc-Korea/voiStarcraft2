const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { glidePath, centre } = require("../smoke/vcursor.js");

const root = path.join(__dirname, "..");
// The second cursor must act only through Accessibility: no input events created/posted/tapped, no real cursor movement.
const FORBIDDEN = /CGWarpMouseCursorPosition|CGDisplayMoveCursorToPoint|CGAssociateMouseAndMouseCursorPosition|CGEventPost|CGEventCreate|CGEvent\s*\(|CGEventTapCreate|PostToPid|SLEvent|SLPS|CGSSetConnectionProperty|warp_cursor|cursor_daemon|sendInputEvent|NSCursor/i;

test("the second cursor (helper + client) never creates, posts or taps input events and never moves the real cursor", () => {
  const hits = [];
  for (const rel of ["smoke/ax_cursor.swift", "smoke/vcursor.js"]) {
    fs.readFileSync(path.join(root, rel), "utf8").split("\n").forEach((line, i) => { const m = FORBIDDEN.exec(line); if (m) hits.push(`${rel}:${i + 1}: ${m[0]}`); });
  }
  assert.deepEqual(hits, []);
  const swift = fs.readFileSync(path.join(root, "smoke", "ax_cursor.swift"), "utf8");
  for (const api of ["AXUIElementPerformAction", "kAXFocusedAttribute", "kAXValueAttribute", "AXDOMIdentifier"]) assert.ok(swift.includes(api), api);
});

test("the guard detects each forbidden API (so it cannot pass vacuously)", () => {
  for (const sample of ["CGWarpMouseCursorPosition(p)", "CGEventPost(.cghidEventTap, e)", "e.postToPid(pid)", "CGEventPostToPid(pid, e)", "CGEvent(mouseEventSource: nil)", "SLEventPostToPid", "/tmp/warp_cursor", "wc.sendInputEvent({})"]) assert.match(sample, FORBIDDEN, sample);
});

test("the pointer glides from where it was and lands exactly on the element centre", () => {
  const to = centre({ x: 100, y: 200, w: 40, h: 20 });
  assert.deepEqual(to, { x: 120, y: 210 });
  assert.deepEqual(glidePath(null, to), [to], "first move jumps straight there");
  const steps = glidePath({ x: 0, y: 0 }, to, 4);
  assert.equal(steps.length, 4);
  assert.deepEqual(steps.at(-1), to);
  assert.ok(steps.every((p, i) => i === 0 || (p.x >= steps[i - 1].x && p.y >= steps[i - 1].y)), "monotonic towards the target");
});
