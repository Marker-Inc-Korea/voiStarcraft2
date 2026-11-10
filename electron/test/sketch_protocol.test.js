const test = require("node:test");
const assert = require("node:assert/strict");
const P = require("../protocol.js");

test("sketch window IPC: strokes, cancel and calibration are validated; extra fields are rejected", () => {
  assert.deepEqual(P.validateSketchStrokes({ strokes: [[[0.1, 0.2, 0], [0.3, 0.4, 5]]] }), { strokes: [[[0.1, 0.2, 0], [0.3, 0.4, 5]]] });
  assert.deepEqual(P.validateSketchStrokes({ strokes: [] }), { strokes: [] });
  assert.throws(() => P.validateSketchStrokes({ strokes: [[[2, 0.2, 0], [0.3, 0.4, 5]]] }), /frame/);
  assert.throws(() => P.validateSketchStrokes({ strokes: "x" }), /strokes/);
  assert.throws(() => P.validateSketchStrokes({ strokes: [], extra: 1 }), /unexpected/);
  assert.deepEqual(P.validateSketchCancel({ reason: "tap" }), { reason: "tap" });
  assert.deepEqual(P.validateSketchCancel({ reason: "whatever" }), { reason: "esc" });
  assert.deepEqual(P.validateSketchCalibration({ action: "cancel" }), { action: "cancel" });
  assert.deepEqual(P.validateSketchCalibration({ action: "save", rect: { x: 1, y: 2, w: 100, h: 100 } }), { action: "save", rect: { x: 1, y: 2, w: 100, h: 100 } });
  assert.throws(() => P.validateSketchCalibration({ action: "save" }), /rect/);
  assert.throws(() => P.validateSketchCalibration({ action: "save", rect: { x: 1, y: 2, w: 0, h: 100 } }), /rect/);
  assert.throws(() => P.validateSketchCalibration({ action: "format" }), /action/);
});
test("settings-window sketch actions are bare, validated commands", () => {
  for (const action of ["calibrate", "reset-calibration", "retry-helper", "request-capture"]) assert.deepEqual(P.validateSketchAction({ action }), { action });
  assert.throws(() => P.validateSketchAction({ action: "rm" }), /action/);
  assert.throws(() => P.validateSketchAction({ action: "calibrate", x: 1 }), /unexpected/);
});
