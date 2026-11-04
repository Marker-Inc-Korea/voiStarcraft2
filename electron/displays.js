"use strict";
// Pure multi-display geometry. No Electron, no timers.
//
// All rectangles/points are in Electron's global DIP space (what screen.getAllDisplays()/getCursorScreenPoint() use):
// the primary display has its top-left at (0,0), other displays sit around it and may have NEGATIVE x/y, different
// sizes, portrait orientation and different scale factors. Nothing here assumes primary, origin (0,0) or workArea
// for edge flush: a dock is flush with display.bounds, and only side docks avoid the menu bar/Dock via an inset.
const DOCK_SIDES = Object.freeze(["bottom", "right", "left", "top"]);

const inside = (point, rect) => point.x >= rect.x && point.x < rect.x + rect.width && point.y >= rect.y && point.y < rect.y + rect.height;
const sameRect = (a, b) => Boolean(a && b) && a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height;
const centre = rect => ({ x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 });
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

// Display that contains the point; for a point in a gap (cursor between mismatched monitors) the nearest by
// distance to the display rect, ties broken by list order. Never falls back to primary/origin.
function pickDisplay(displays, point) {
  if (!Array.isArray(displays) || !displays.length) throw new TypeError("no displays");
  const hit = displays.find(display => inside(point, display.bounds));
  if (hit) return hit;
  let best = displays[0], bestDistance = Infinity;
  for (const display of displays) {
    const b = display.bounds;
    const dx = Math.max(b.x - point.x, 0, point.x - (b.x + b.width - 1));
    const dy = Math.max(b.y - point.y, 0, point.y - (b.y + b.height - 1));
    const distance = dx * dx + dy * dy;
    if (distance < bestDistance) { bestDistance = distance; best = display; }
  }
  return best;
}

// Which display the dock should live on. `choice` is the 레이아웃 > 모니터 setting: "cursor" | "id:<n>".
// A fixed display that is no longer connected falls back to the cursor display (never to primary).
function resolveDockDisplay(displays, choice, cursor) {
  const match = /^id:(\d+)$/.exec(String(choice || ""));
  if (match) { const fixed = displays.find(display => display.id === Number(match[1])); if (fixed) return fixed; }
  return pickDisplay(displays, cursor);
}

// Which displays carry a rainbow overlay window: "current" (the dock/cursor display) or "all".
function overlayTargets(displays, scope, activeDisplay) {
  return scope === "all" ? displays.slice() : [activeDisplay];
}

// Reconcile existing overlay windows (display id -> current bounds) with the displays that should carry one.
// Returns what main must do; pure so the create/move/destroy decisions are unit-tested without Electron.
function planOverlays(existing, targets) {
  const want = new Map(targets.map(display => [display.id, display.bounds]));
  const create = [], move = [], keep = [], destroy = [];
  for (const [id, bounds] of want) {
    if (!existing.has(id)) create.push(id);
    else if (!sameRect(existing.get(id), bounds)) move.push(id);
    else keep.push(id);
  }
  for (const id of existing.keys()) if (!want.has(id)) destroy.push(id);
  return { create, move, keep, destroy };
}

// Where the dock window goes. The window is flush with display.bounds on its docked edge. Side docks and top docks
// stay below the menu bar (inset = top of workArea) so the panel is not clipped; the bottom edge is the bounds edge.
function dockWindowRect(side, display, o = {}) {
  if (!DOCK_SIDES.includes(side)) throw new TypeError("unknown dock side");
  const b = display.bounds;
  const wa = display.workArea || b;
  const ws = clamp(Number(o.widthScale ?? 100), 40, 100) / 100;
  const hs = clamp(Number(o.heightScale ?? 100), 40, 100) / 100;
  const portrait = b.height > b.width;
  const sideWidth = o.sideWidth ?? 380, margin = o.margin ?? 10;
  const insetTop = Math.max(0, wa.y - b.y); // menu bar (macOS) on this display; 0 on displays without one
  const insetBottom = Math.max(0, b.y + b.height - (wa.y + wa.height));
  if (side === "bottom" || side === "top") {
    // Wide displays cap at 980 DIP; narrow/portrait ones use 90 % of the width.
    const baseW = Math.max(320, Math.min(o.barMaxWidth ?? 980, Math.floor(b.width * (o.barWidthRatio ?? 0.9))));
    const heightRatio = portrait ? 0.5 : (o.barHeightRatio ?? 0.75);
    const baseH = Math.max(160, Math.min(o.barMaxHeight ?? 640, Math.floor(b.height * heightRatio)));
    const width = Math.min(b.width, Math.max(320, Math.floor(baseW * ws)));
    const height = Math.min(b.height - insetTop, Math.max(160, Math.floor(baseH * hs)));
    const x = b.x + Math.floor((b.width - width) / 2);
    const y = side === "bottom" ? b.y + b.height - height : b.y + insetTop;
    return { x, y, width, height };
  }
  const width = Math.min(Math.floor((sideWidth + margin) * ws), b.width);
  const avail = b.height - insetTop - insetBottom;
  const height = Math.min(avail, Math.max(240, Math.floor(avail * hs)));
  const y = b.y + insetTop + Math.floor((avail - height) / 2);
  const x = side === "right" ? b.x + b.width - width : b.x;
  return { x, y, width, height };
}

// Reveal trigger: a `zone`-px strip along the display edge, spanning the dock's extent on that edge.
function triggerZone(side, display, windowRect, zone = 2) {
  const b = display.bounds;
  const z = Math.max(1, Math.round(zone));
  switch (side) {
    case "bottom": return { x: windowRect.x, y: b.y + b.height - z, width: windowRect.width, height: z };
    case "top": return { x: windowRect.x, y: b.y, width: windowRect.width, height: z };
    case "right": return { x: b.x + b.width - z, y: windowRect.y, width: z, height: windowRect.height };
    case "left": return { x: b.x, y: windowRect.y, width: z, height: windowRect.height };
    default: throw new TypeError("unknown dock side");
  }
}

// Is the dock window exactly where it should be (flush with the right display edge)? Used by tests and the smoke driver.
function isFlush(side, display, rect) {
  const b = display.bounds;
  switch (side) {
    case "bottom": return rect.y + rect.height === b.y + b.height;
    case "top": return rect.y === b.y + Math.max(0, (display.workArea || b).y - b.y);
    case "right": return rect.x + rect.width === b.x + b.width;
    case "left": return rect.x === b.x;
    default: return false;
  }
}

// Cursor -> display decision with hysteresis so a cursor skimming a shared corner does not bounce the dock around.
// Returns the display to move to, or null to stay. A move needs the cursor on another display for `dwellMs`.
function createFollower({ dwellMs = 250 } = {}) {
  let candidate = null, since = 0;
  return {
    update(displays, cursor, currentId, now) {
      const target = pickDisplay(displays, cursor);
      if (target.id === currentId) { candidate = null; return null; }
      if (!candidate || candidate !== target.id) { candidate = target.id; since = now; return null; }
      if (now - since >= dwellMs) { candidate = null; return target; }
      return null;
    },
    reset() { candidate = null; },
  };
}

// Edge-flush check that tolerates 1 DIP of rounding on mixed-scale setups (Retina 2x next to 1x).
function withinRounding(a, b, tolerance = 1) {
  return Math.abs(a.x - b.x) <= tolerance && Math.abs(a.y - b.y) <= tolerance && Math.abs(a.width - b.width) <= tolerance && Math.abs(a.height - b.height) <= tolerance;
}

module.exports = { DOCK_SIDES, inside, sameRect, centre, pickDisplay, resolveDockDisplay, overlayTargets, planOverlays, dockWindowRect, triggerZone, isFlush, createFollower, withinRounding };
