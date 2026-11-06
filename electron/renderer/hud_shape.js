"use strict";
// Pure geometry of the control HUD. ONE rounded shape drives three consumers, so they can never disagree:
//   - `clipPath`  CSS clip-path: path("...") the glass panel is clipped with,
//   - `path`      the same SVG path, stroked by the renderer as the rim/glow outline,
//   - `points`    that path sampled densely into a polygon (>= 16 points per corner) for main's pointer hit-test, so
//                 everything outside the ROUNDED silhouette stays click-through to the game.
//
// Silhouettes
//   bottom dock  the slanted bar: wide base flush with the screen edge, narrower far edge, slanted sides (default 14°,
//                0..30). All four corners are rounded.
//   other docks  a plain rounded rectangle (the slant applies to the bottom bar only).
// Corner radii: the far (top) corners of the bar use `radius` (token glass.radius). The bar's two base corners sit on the
// screen edge and are acute (90°-slant), so they use `baseRadius` (default 0.6 * radius): still clearly rounded, but the
// wide stance on the edge stays wide. In a rounded rectangle all four corners use `radius`.
// Corner curve: a single cubic Bézier per corner. smooth = 0 reproduces a circular arc; the default smooth = 0.6 starts
// the curve earlier on each edge and eases in, which gives the continuous-curvature "squircle" corner of Apple's HIG.
// Radii are clamped (uniformly) so neighbouring corners never overlap, which makes radius > half the height degrade to a pill.
//
// Loaded as a classic script in the renderer and as CommonJS by main/tests. No DOM, no Electron.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.VoiHudShape = factory();
})(typeof self !== "undefined" ? self : this, function () {
  const DOCKS = Object.freeze(["bottom", "right", "left", "top"]);
  const MAX_SLANT = 30;
  const DEFAULT_MAX_INSET = 64; // a tall panel (settings open) must not lose its whole top to the slant
  const DEFAULT_RADIUS = 22, DEFAULT_SMOOTH = 0.6, DEFAULT_BASE_FACTOR = 0.6, DEFAULT_SAMPLES = 24;
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
  const r2 = value => Math.round(value * 100) / 100;
  const num = (value, fallback) => (Number.isFinite(Number(value)) && value !== null && value !== "" ? Number(value) : fallback);

  // Horizontal run of one slanted side over the panel depth: depth * tan(angle from vertical), capped.
  function insetFor(depth, length, slantDeg, maxInset = DEFAULT_MAX_INSET) {
    const slant = clamp(Number.isFinite(slantDeg) ? slantDeg : 0, 0, MAX_SLANT);
    const raw = Math.max(0, depth) * Math.tan((slant * Math.PI) / 180);
    return r2(Math.min(raw, maxInset, Math.max(0, length) * 0.2));
  }

  // Local frame: u runs along the docked edge (0..L), v runs away from it (0..D). Base is v=0 and is the full width.
  function toWindow(dock, width, height, [u, v]) {
    switch (dock) {
      case "bottom": return [u, height - v];
      case "top": return [u, v];
      case "right": return [width - v, u];
      case "left": return [v, u];
      default: throw new TypeError("unknown dock");
    }
  }

  const sub = (a, b) => [a[0] - b[0], a[1] - b[1]];
  const unit = v => { const n = Math.hypot(v[0], v[1]) || 1; return [v[0] / n, v[1] / n]; };
  const at = (origin, dir, dist) => [origin[0] + dir[0] * dist, origin[1] + dir[1] * dist];

  // Corner at vertex V between neighbours P (previous) and N (next) for radius r. `reach` = how far along each edge the
  // curve extends; `handle` = Bézier handle length. smooth = 0 is a circular arc (handle = 4/3 tan(turn/4) r).
  function cornerFor(P, V, N, r, smooth) {
    const e1 = unit(sub(P, V)), e2 = unit(sub(N, V));
    const turn = Math.PI - Math.acos(clamp(e1[0] * e2[0] + e1[1] * e2[1], -1, 1)); // how far the edge direction bends
    if (!(r > 0) || turn < 1e-6) return { V, e1, e2, reach: 0, handle: 0 };
    return { V, e1, e2, reach: r * Math.tan(turn / 2) * (1 + 0.25 * smooth), handle: (4 / 3) * Math.tan(turn / 4) * r * (1 + 0.55 * smooth) };
  }
  // Scale by the uniform clamp factor f and place the tangent points / handles.
  function finishCorner(c, f) {
    const reach = c.reach * f, handle = c.handle * f;
    const t1 = at(c.V, c.e1, reach), t2 = at(c.V, c.e2, reach);
    return { ...c, reach, t1, t2, c1: at(t1, c.e1, -handle), c2: at(t2, c.e2, -handle) };
  }

  const cubic = (p0, p1, p2, p3, t) => {
    const m = 1 - t, a = m * m * m, b = 3 * m * m * t, c = 3 * m * t * t, d = t * t * t;
    return [a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]];
  };

  // shape(dock, width, height, {slantDeg, radius, baseRadius, smooth, samples, maxInset})
  //   -> {dock, width, height, slantDeg, inset, radius, baseRadius, smooth, vertices, points, path, clipPath}
  // `points` are in window space, one closed ring (no repeated first point).
  function shape(dock, width, height, options = {}) {
    if (!DOCKS.includes(dock)) throw new TypeError("unknown dock");
    if (!(width > 0) || !(height > 0)) throw new TypeError("shape needs a positive size");
    const along = dock === "bottom" || dock === "top";
    const L = along ? width : height, D = along ? height : width;
    const slantDeg = dock === "bottom" ? clamp(Number(options.slantDeg ?? 14) || 0, 0, MAX_SLANT) : 0; // only the bottom bar is slanted
    const inset = insetFor(D, L, slantDeg, options.maxInset ?? DEFAULT_MAX_INSET);
    const radius = Math.max(0, num(options.radius, DEFAULT_RADIUS));
    const slanted = dock === "bottom";
    const baseRadius = Math.max(0, num(options.baseRadius, slanted ? radius * DEFAULT_BASE_FACTOR : radius));
    const smooth = clamp(num(options.smooth, DEFAULT_SMOOTH), 0, 1);
    const samples = Math.max(4, Math.round(num(options.samples, DEFAULT_SAMPLES)));

    // base-left, far-left, far-right, base-right (local frame)
    const local = [[0, 0], [inset, D], [L - inset, D], [L, 0]];
    const radii = [baseRadius, radius, radius, baseRadius];
    const n = local.length;
    const raw = local.map((V, i) => cornerFor(local[(i + n - 1) % n], V, local[(i + 1) % n], radii[i], smooth));
    // uniform clamp: on every edge the two curves must fit, so f = min over edges of len / (reachA + reachB)
    let f = 1;
    for (let i = 0; i < n; i += 1) {
      const j = (i + 1) % n, len = Math.hypot(local[j][0] - local[i][0], local[j][1] - local[i][1]), used = raw[i].reach + raw[j].reach;
      if (used > len) f = Math.min(f, len / used);
    }
    const corners = raw.map(c => finishCorner(c, f));

    const win = p => toWindow(dock, width, height, p);
    const ring = [];
    for (const c of corners) {
      if (!c.reach) { ring.push(win(c.V)); continue; }
      for (let k = 0; k <= samples; k += 1) ring.push(win(cubic(c.t1, c.c1, c.c2, c.t2, k / samples)));
    }
    // path: start where corner 0's curve ends, then corners 1..3, then corner 0 whose curve returns to the start
    const pt = p => `${r2(p[0])} ${r2(p[1])}`;
    let d = `M${pt(win(corners[0].t2))}`;
    for (const c of [corners[1], corners[2], corners[3], corners[0]]) {
      d += ` L${pt(win(c.t1))}`;
      if (c.reach) d += ` C${pt(win(c.c1))} ${pt(win(c.c2))} ${pt(win(c.t2))}`;
    }
    d += " Z";
    const points = ring.map(p => [r2(p[0]), r2(p[1])]);
    return {
      dock, width, height, slantDeg, inset, radius: r2(radius * f), baseRadius: r2(baseRadius * f), smooth, clampFactor: f,
      vertices: local.map(p => win(p).map(r2)), points, path: d, clipPath: `path("${d}")`,
    };
  }

  const svgPoints = points => points.map(([x, y]) => `${r2(x)},${r2(y)}`).join(" ");

  // Ray-casting point-in-polygon; boundary counts as inside so the visible edge is always clickable.
  function containsPoint(points, x, y) {
    let inside = false;
    for (let i = 0, j = points.length - 1; i < points.length; j = i, i += 1) {
      const [xi, yi] = points[i], [xj, yj] = points[j];
      const cross = (x - xi) * (yj - yi) - (y - yi) * (xj - xi);
      if (Math.abs(cross) < 1e-9 && x >= Math.min(xi, xj) - 1e-9 && x <= Math.max(xi, xj) + 1e-9 && y >= Math.min(yi, yj) - 1e-9 && y <= Math.max(yi, yj) + 1e-9) return true;
      if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }
  const translate = (points, dx, dy) => points.map(([x, y]) => [r2(x + dx), r2(y + dy)]);

  // Padding that keeps content inside the slanted sides and off the rounded corners: the content's far edge sits
  // `contentStart` px from the far side, where the shape is `inset` narrower per side (plus ~a third of the corner radius).
  function contentPadding(s, { contentStart = 12, pad = 8, base = { top: 12, right: 14, bottom: 14, left: 14 } } = {}) {
    const along = s.dock === "bottom" || s.dock === "top";
    const D = along ? s.height : s.width;
    const slanted = s.inset * clamp((D - contentStart) / D, 0, 1) + pad + s.radius * 0.3;
    const out = { ...base };
    if (along) { out.left = Math.max(base.left, r2(slanted)); out.right = Math.max(base.right, r2(slanted)); }
    else { out.top = Math.max(base.top, r2(slanted)); out.bottom = Math.max(base.bottom, r2(slanted)); }
    return out;
  }

  return { DOCKS, MAX_SLANT, DEFAULT_MAX_INSET, DEFAULT_RADIUS, DEFAULT_SMOOTH, insetFor, shape, svgPoints, containsPoint, translate, contentPadding };
});
