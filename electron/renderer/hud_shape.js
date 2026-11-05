"use strict";
// Pure geometry of the control HUD: a trapezoid anchored to the docked screen edge (wide base on the edge, narrower far
// edge, slanted sides), optionally with chamfered far corners. The same polygon drives three things, so they can never
// disagree: the CSS clip-path, the outline/corner brackets drawn in the renderer, and the pointer hit-test in main
// (everything outside the polygon stays click-through to the game).
// Loaded as a classic script in the renderer and as CommonJS by main/tests. No DOM, no Electron.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.VoiHudShape = factory();
})(typeof self !== "undefined" ? self : this, function () {
  const DOCKS = Object.freeze(["bottom", "right", "left", "top"]);
  const MAX_SLANT = 30;
  const DEFAULT_MAX_INSET = 64; // a tall panel (settings open) must not lose its whole top to the slant
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
  const r2 = value => Math.round(value * 100) / 100;

  // Horizontal run of one slanted side over the panel depth: depth * tan(angle from vertical), capped.
  function insetFor(depth, length, slantDeg, maxInset = DEFAULT_MAX_INSET) {
    const slant = clamp(Number.isFinite(slantDeg) ? slantDeg : 0, 0, MAX_SLANT);
    const raw = Math.max(0, depth) * Math.tan((slant * Math.PI) / 180);
    return r2(Math.min(raw, maxInset, Math.max(0, length) * 0.2));
  }

  // Local frame: u runs along the docked edge (0..L), v runs away from it (0..D). Base is v=0 and is the full width.
  function toWindow(dock, width, height, u, v) {
    switch (dock) {
      case "bottom": return [u, height - v];
      case "top": return [u, v];
      case "right": return [width - v, u];
      case "left": return [v, u];
      default: throw new TypeError("unknown dock");
    }
  }

  // shape(dock, width, height, {slantDeg, chamfer, maxInset}) -> {dock, inset, chamfer, points, clipPath}
  function shape(dock, width, height, options = {}) {
    if (!DOCKS.includes(dock)) throw new TypeError("unknown dock");
    if (!(width > 0) || !(height > 0)) throw new TypeError("shape needs a positive size");
    const along = dock === "bottom" || dock === "top";
    const L = along ? width : height, D = along ? height : width;
    const inset = insetFor(D, L, Number(options.slantDeg ?? 14), options.maxInset ?? DEFAULT_MAX_INSET);
    const chamfer = clamp(Number(options.chamfer) || 0, 0, Math.max(0, Math.min(D / 2, (L - 2 * inset) / 4)));
    const side = Math.hypot(inset, D);
    const local = [[0, 0]];
    if (chamfer > 0) {
      const ux = (-inset / side) * chamfer, uy = (-D / side) * chamfer; // far-left corner, towards the base
      local.push([inset + ux, D + uy], [inset + chamfer, D], [L - inset - chamfer, D], [L - inset - ux, D + uy]);
    } else local.push([inset, D], [L - inset, D]);
    local.push([L, 0]);
    const points = local.map(([u, v]) => toWindow(dock, width, height, u, v).map(r2));
    return { dock, width, height, inset, chamfer, points, clipPath: clipPath(points) };
  }
  const clipPath = points => `polygon(${points.map(([x, y]) => `${r2(x)}px ${r2(y)}px`).join(", ")})`;
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

  // Padding that keeps content inside the slanted sides: the content's far edge sits `contentStart` px from the far side,
  // where the polygon is `inset` narrower per side, so the worst-case side padding is inset*(D-contentStart)/D + pad.
  function contentPadding(s, { contentStart = 12, pad = 8, base = { top: 12, right: 14, bottom: 14, left: 14 } } = {}) {
    const along = s.dock === "bottom" || s.dock === "top";
    const D = along ? s.height : s.width;
    const slanted = s.inset * clamp((D - contentStart) / D, 0, 1) + pad;
    const out = { ...base };
    if (along) { out.left = Math.max(base.left, r2(slanted)); out.right = Math.max(base.right, r2(slanted)); }
    else { out.top = Math.max(base.top, r2(slanted)); out.bottom = Math.max(base.bottom, r2(slanted)); }
    return out;
  }

  // SVG path "M a L corner L b" for every vertex: Terran-console corner brackets of length `len` along the adjacent edges.
  function cornerBrackets(points, len = 14, only = null) {
    const n = points.length;
    let d = "";
    for (let i = 0; i < n; i += 1) {
      if (only && !only.includes(i)) continue;
      const p = points[i], prev = points[(i + n - 1) % n], next = points[(i + 1) % n];
      const toward = q => {
        const dx = q[0] - p[0], dy = q[1] - p[1], dist = Math.hypot(dx, dy) || 1, k = Math.min(len, dist / 2) / dist;
        return [r2(p[0] + dx * k), r2(p[1] + dy * k)];
      };
      const a = toward(prev), b = toward(next);
      d += `${d ? " " : ""}M${a[0]} ${a[1]} L${r2(p[0])} ${r2(p[1])} L${b[0]} ${b[1]}`;
    }
    return d;
  }

  return { DOCKS, MAX_SLANT, DEFAULT_MAX_INSET, insetFor, shape, clipPath, svgPoints, containsPoint, translate, contentPadding, cornerBrackets };
});
