"use strict";
// Pure stroke recorder + shape helpers for the minimap sketch. Points are minimap-FRAME-normalized [nx, ny, t_ms] (0..1, origin
// top-left); t is milliseconds since the first point of the sketch. Nothing outside 0..1 is ever recorded: the VLM downstream may
// only ever see coordinates that lie on the frame the user drew on.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.VoiSketchStrokes = factory();
})(typeof self !== "undefined" ? self : this, function () {
  const TAP_DIST = 0.02;       // a stroke whose whole path is shorter than this (frame fractions) is a click, not a drawing
  const MIN_STEP = 0.004;      // jitter filter between recorded points
  const MAX_STROKES = 8, MAX_POINTS = 400;
  const CORNER_ARM = 0.06, CORNER_ANGLE = 50;
  const clamp01 = v => Math.min(1, Math.max(0, v));
  const inFrame = (x, y) => Number.isFinite(x) && Number.isFinite(y) && x >= 0 && x <= 1 && y >= 0 && y <= 1;
  const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
  const r4 = v => Math.round(v * 10000) / 10000;

  function createRecorder({ minStep = MIN_STEP, maxStrokes = MAX_STROKES, maxPoints = MAX_POINTS } = {}) {
    let strokes = [], current = null, t0 = null, length = 0;
    const stamp = t => { if (t0 === null) t0 = t; return Math.max(0, Math.round(t - t0)); };
    function begin(x, y, t) {
      if (current || strokes.length >= maxStrokes || !inFrame(x, y)) return false;
      current = [[r4(x), r4(y), stamp(t)]]; length = 0;
      return true;
    }
    function move(x, y, t) {
      if (!current || !Number.isFinite(x) || !Number.isFinite(y)) return false;
      const point = [r4(clamp01(x)), r4(clamp01(y)), stamp(t)];
      const last = current[current.length - 1];
      const step = dist(point, last);
      if (step < minStep || current.length >= maxPoints - 1) return false; // keep one slot for the final point
      current.push(point); length += step;
      return true;
    }
    function close(point) {
      const stroke = current; current = null;
      if (point) { length += dist(point, stroke[stroke.length - 1]); if (dist(point, stroke[stroke.length - 1]) > 0) stroke.push(point); }
      if (length < TAP_DIST || stroke.length < 2) { length = 0; return { kind: "tap" }; }
      length = 0; strokes.push(stroke);
      return { kind: "stroke", stroke };
    }
    function end(x, y, t) {
      if (!current) return { kind: "none" };
      const ok = Number.isFinite(x) && Number.isFinite(y);
      return close(ok ? [r4(clamp01(x)), r4(clamp01(y)), stamp(t)] : null);
    }
    function finish() { return current ? close(null) : { kind: "none" }; }
    function cancel() { strokes = []; current = null; t0 = null; length = 0; }
    return { begin, move, end, finish, cancel, open: () => Boolean(current), strokes: () => strokes.map(s => s.map(p => p.slice())), preview: () => (current ? current.map(p => p.slice()) : null), count: () => strokes.length };
  }

  // Corners (sharp turns) of a stroke, for the small dots drawn at them. Arm-length based so a finely sampled gentle curve never
  // accumulates into a "corner"; adjacent candidates collapse onto the sharpest one.
  function corners(points) {
    const found = [];
    for (let i = 1; i < points.length - 1; i++) {
      let j = i - 1; while (j > 0 && dist(points[j], points[i]) < CORNER_ARM) j--;
      let k = i + 1; while (k < points.length - 1 && dist(points[k], points[i]) < CORNER_ARM) k++;
      if (dist(points[j], points[i]) < CORNER_ARM || dist(points[k], points[i]) < CORNER_ARM) continue;
      const a = [points[i][0] - points[j][0], points[i][1] - points[j][1]], b = [points[k][0] - points[i][0], points[k][1] - points[i][1]];
      const angle = Math.abs(Math.atan2(a[0] * b[1] - a[1] * b[0], a[0] * b[0] + a[1] * b[1])) * 180 / Math.PI;
      if (angle >= CORNER_ANGLE) found.push({ nx: points[i][0], ny: points[i][1], index: i, angle });
    }
    const out = [];
    for (const c of found) {
      const prev = out[out.length - 1];
      if (prev && c.index - prev.index <= 12 && Math.hypot(prev.nx - c.nx, prev.ny - c.ny) < CORNER_ARM) { if (c.angle > prev.angle) out[out.length - 1] = c; } else out.push(c);
    }
    return out;
  }

  // Unit direction of the stroke's end (for the arrow head): from the last point back to the first point at least `min` away.
  function endDirection(points, min = 0.01) {
    if (!points || points.length < 2) return null;
    const last = points[points.length - 1];
    for (let i = points.length - 2; i >= 0; i--) {
      const d = dist(last, points[i]);
      if (d >= min) return { dx: (last[0] - points[i][0]) / d, dy: (last[1] - points[i][1]) / d };
    }
    return null;
  }

  return { TAP_DIST, MIN_STEP, MAX_STROKES, MAX_POINTS, createRecorder, corners, endDirection };
});
