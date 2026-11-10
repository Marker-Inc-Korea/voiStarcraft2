"use strict";
// Pure minimap geometry for the sketch surface. Loaded as CommonJS by main/tests and as a classic script in the sketch window.
// Everything is Electron global DIP (displays may have negative origins and mixed scale factors).
//
// A "rect" is the SC2 minimap FRAME on screen (the game's square minimap widget). Strokes are normalised to that frame only
// (0..1, origin top-left, y down). The letterboxed playable area inside the frame is NOT handled here: the backend maps
// frame -> playable area (starcraft_commander/route_sketch.py) and the renderer never assumes it.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.VoiSketchGeometry = factory();
})(typeof self !== "undefined" ? self : this, function () {
  // ASSUMPTION (only a live SC2 can confirm; the calibration tab exists because of it): with the standard SC2 UI the minimap is a
  // square panel in the bottom-left corner of the SC2 view whose side is ~24.5 % of the view height, with a ~1 % margin from the
  // left/bottom edges. This also assumes SC2 fills the display (fullscreen / borderless); windowed SC2 needs the drag-to-fit step.
  const DEFAULT_SIDE_OF_HEIGHT = 0.245;
  const DEFAULT_MARGIN_OF_HEIGHT = 0.01;
  const MIN_SIDE = 40;
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
  const round = value => Math.round(value * 1000) / 1000;

  function normalize(rect, x, y) {
    const nx = (x - rect.x) / rect.w, ny = (y - rect.y) / rect.h;
    return { nx: round(nx), ny: round(ny), inside: x >= rect.x && x <= rect.x + rect.w && y >= rect.y && y <= rect.y + rect.h };
  }
  const denormalize = (rect, nx, ny) => ({ x: rect.x + nx * rect.w, y: rect.y + ny * rect.h });
  const contains = (rect, x, y) => x >= rect.x && x <= rect.x + rect.w && y >= rect.y && y <= rect.y + rect.h;

  function defaultMinimapRect(display) {
    const b = display.bounds;
    const side = Math.round(b.height * DEFAULT_SIDE_OF_HEIGHT);
    const margin = Math.round(b.height * DEFAULT_MARGIN_OF_HEIGHT);
    return { x: b.x + margin, y: b.y + b.height - margin - side, w: side, h: side };
  }

  const displayKey = display => `${display.id}@${display.bounds.width}x${display.bounds.height}@${display.scaleFactor}`;

  // Stored rects are display-LOCAL (offset from the display origin) so rearranging monitors does not orphan them.
  const emptyStore = () => ({ version: 1, rects: {} });
  const validLocal = r => r && ["x", "y", "w", "h"].every(k => typeof r[k] === "number" && Number.isFinite(r[k])) && r.w >= MIN_SIDE && r.h >= MIN_SIDE;
  function parseStore(text) {
    const out = emptyStore();
    let raw = null;
    try { raw = typeof text === "string" ? JSON.parse(text) : text; } catch { return out; }
    if (!raw || typeof raw !== "object" || !raw.rects || typeof raw.rects !== "object") return out;
    for (const [key, value] of Object.entries(raw.rects)) if (/^\d+@\d+x\d+@[\d.]+$/.test(key) && validLocal(value)) out.rects[key] = { x: value.x, y: value.y, w: value.w, h: value.h };
    return out;
  }
  function saveRect(store, display, rect) {
    const local = { x: rect.x - display.bounds.x, y: rect.y - display.bounds.y, w: rect.w, h: rect.h };
    return { version: 1, rects: { ...store.rects, [displayKey(display)]: local } };
  }
  function clearRect(store, display) {
    const rects = { ...store.rects };
    delete rects[displayKey(display)];
    return { version: 1, rects };
  }
  // -> { rect (global DIP), calibrated }. An uncalibrated display gets the documented default guess.
  function resolveRect(store, display) {
    const local = store.rects[displayKey(display)];
    if (!local) return { rect: defaultMinimapRect(display), calibrated: false };
    return { rect: { x: display.bounds.x + local.x, y: display.bounds.y + local.y, w: local.w, h: local.h }, calibrated: true };
  }

  function fitRect(rect, bounds) {
    const w = Math.round(clamp(rect.w, MIN_SIDE, Math.max(MIN_SIDE, bounds.width)));
    const h = Math.round(clamp(rect.h, MIN_SIDE, Math.max(MIN_SIDE, bounds.height)));
    return { x: Math.round(clamp(rect.x, bounds.x, bounds.x + bounds.width - w)), y: Math.round(clamp(rect.y, bounds.y, bounds.y + bounds.height - h)), w, h };
  }

  // Screen-capture source pixels of the rect: display-local DIP * scale factor.
  function capturePixels(rect, display) {
    const s = display.scaleFactor || 1;
    return { x: Math.round((rect.x - display.bounds.x) * s), y: Math.round((rect.y - display.bounds.y) * s), w: Math.round(rect.w * s), h: Math.round(rect.h * s) };
  }
  function outputSize(w, h, max) {
    const long = Math.max(w, h);
    if (long <= max) return { w, h };
    const k = max / long;
    return { w: Math.max(1, Math.round(w * k)), h: Math.max(1, Math.round(h * k)) };
  }
  function displayForRect(displays, rect) {
    const cx = rect.x + rect.w / 2, cy = rect.y + rect.h / 2;
    const hit = displays.find(d => cx >= d.bounds.x && cx < d.bounds.x + d.bounds.width && cy >= d.bounds.y && cy < d.bounds.y + d.bounds.height);
    if (hit) return hit;
    let best = null, bestDist = Infinity;
    for (const d of displays) {
      const dx = Math.max(d.bounds.x - cx, 0, cx - (d.bounds.x + d.bounds.width)), dy = Math.max(d.bounds.y - cy, 0, cy - (d.bounds.y + d.bounds.height));
      const dist = dx * dx + dy * dy;
      if (dist < bestDist) { best = d; bestDist = dist; }
    }
    return best;
  }

  // Window rect for the sketch/route surface: the minimap rect grown by `pad` DIP per side (room for labels/ghost cursor), clamped to the
  // display. (ox, oy) = where the minimap frame's top-left sits inside that window. pad 0 = exactly the rect (sketch mode).
  function windowFor(rect, bounds, pad = 0) {
    const x0 = Math.max(bounds.x, rect.x - pad), y0 = Math.max(bounds.y, rect.y - pad);
    const x1 = Math.min(bounds.x + bounds.width, rect.x + rect.w + pad), y1 = Math.min(bounds.y + bounds.height, rect.y + rect.h + pad);
    return { bounds: { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }, ox: rect.x - x0, oy: rect.y - y0 };
  }
  const moveRect = (rect, dx, dy, bounds) => fitRect({ ...rect, x: rect.x + dx, y: rect.y + dy }, bounds);
  // Drag one corner of the (square) minimap frame; the opposite corner stays put and the side follows the larger drag distance.
  function resizeFromCorner(rect, corner, point, bounds) {
    const left = corner === "tl" || corner === "bl", top = corner === "tl" || corner === "tr";
    const ax = left ? rect.x + rect.w : rect.x, ay = top ? rect.y + rect.h : rect.y; // anchor = opposite corner
    const roomX = left ? ax - bounds.x : bounds.x + bounds.width - ax, roomY = top ? ay - bounds.y : bounds.y + bounds.height - ay;
    const wanted = Math.max(left ? ax - point.x : point.x - ax, top ? ay - point.y : point.y - ay);
    const side = Math.round(clamp(wanted, MIN_SIDE, Math.max(MIN_SIDE, Math.min(roomX, roomY))));
    return { x: left ? ax - side : ax, y: top ? ay - side : ay, w: side, h: side };
  }

  return { windowFor, moveRect, resizeFromCorner, DEFAULT_SIDE_OF_HEIGHT, DEFAULT_MARGIN_OF_HEIGHT, MIN_SIDE, normalize, denormalize, contains, defaultMinimapRect, displayKey, emptyStore, parseStore, saveRect, clearRect, resolveRect, fitRect, capturePixels, outputSize, displayForRect };
});
