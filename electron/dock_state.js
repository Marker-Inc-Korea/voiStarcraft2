"use strict";
// Pure edge-dock logic: where the dock window goes, when it reveals, when it hides. No Electron,
// no timers, no I/O: main.js feeds it the cursor/displays/time every ~50 ms and applies the decision.
//
// Geometry is in screen DIP, same space as screen.getCursorScreenPoint() and display.bounds.
const Displays = require("./displays.js");
const DOCK_MODES = Displays.DOCK_SIDES;
const DEFAULTS = Object.freeze({
  edgePx: 2,            // trigger zone thickness at the display edge
  sharedEdgeDwellMs: 450, // an edge that is entirely a seam to another display: crossing it must not open the bar, resting on it must
  edgeDwellMs: 120,     // cursor must stay in the zone this long (SC2 edge-scroll would otherwise pop the bar open)
  slopPx: 12,           // panel rect inflation while revealed
  hideDelayMs: 600,     // cursor must stay outside panel+slop this long before collapsing
  idleCollapseMs: 2500, // after the last forced-open reason ends
  bottomMaxWidth: 980, bottomWidthRatio: 0.9, bottomMaxHeight: 640, bottomHeightRatio: 0.75,
  sideWidth: 380, margin: 10,
  widthScale: 100, heightScale: 100, // percent
  autoHide: true,
});

const inside = (point, rect) => point.x >= rect.x && point.x < rect.x + rect.width && point.y >= rect.y && point.y < rect.y + rect.height;
const inflate = (rect, amount) => ({ x: rect.x - amount, y: rect.y - amount, width: rect.width + 2 * amount, height: rect.height + 2 * amount });
function displayContaining(displays, point) {
  return displays.find(display => inside(point, display.bounds)) || null;
}

// Window = maximum expanded footprint of the mode on a display; it never resizes afterwards.
// trigger = the edge strip (screen coords) that reveals the dock. Geometry lives in displays.js (pure, multi-display safe).
// `bounds` is the display's bounds (flush edge); options.workArea only insets side/top docks below the menu bar.
function layoutFor(mode, bounds, options = {}) {
  if (!DOCK_MODES.includes(mode)) throw new TypeError("unknown dock mode");
  const o = { ...DEFAULTS, ...options };
  const display = { bounds, workArea: o.workArea || bounds };
  const window = Displays.dockWindowRect(mode, display, { widthScale: o.widthScale, heightScale: o.heightScale, sideWidth: o.sideWidth, margin: o.margin, barMaxWidth: o.bottomMaxWidth, barWidthRatio: o.bottomWidthRatio, barMaxHeight: o.bottomMaxHeight, barHeightRatio: o.bottomHeightRatio });
  const trigger = Displays.triggerZone(mode, display, window, o.edgePx);
  const handle = Displays.triggerZone(mode, display, window, 4);
  return { window, trigger, handle };
}

// True when another display lies directly beyond the edge at this point (cursor is just crossing screens).
function edgeIsShared(mode, bounds, point, displays) {
  const probe = mode === "bottom" ? { x: point.x, y: bounds.y + bounds.height + 1 }
    : mode === "top" ? { x: point.x, y: bounds.y - 1 }
    : mode === "right" ? { x: bounds.x + bounds.width + 1, y: point.y }
    : { x: bounds.x - 1, y: point.y };
  return displays.some(display => inside(probe, display.bounds));
}

// True when EVERY point of the trigger zone has another display beyond it (the edge is nothing but a seam).
function edgeFullyShared(mode, bounds, trigger, displays) {
  const n = 9;
  for (let i = 0; i < n; i += 1) {
    const point = mode === "bottom" || mode === "top"
      ? { x: trigger.x + Math.floor((trigger.width * (i + 0.5)) / n), y: trigger.y }
      : { x: trigger.x, y: trigger.y + Math.floor((trigger.height * (i + 0.5)) / n) };
    if (!edgeIsShared(mode, bounds, point, displays)) return false;
  }
  return true;
}

function createDockController(options = {}) {
  let revealed = false;
  let outsideSince = null;
  let edgeSince = null;
  let lastForceAt = -Infinity;
  let lastReason = "";

  // input: { cursor:{x,y}, displays:[{id,bounds,workArea?}], dockDisplayId, mode, panelRect?:screen rect while laid out,
  //          forceOpen, pinned, now(ms), settings?: {autoHide, hideDelayMs, zonePx, widthScale, heightScale, margin, followCursor} }
  function update(input) {
    const { cursor, displays, dockDisplayId, mode, now } = input;
    const o = { ...DEFAULTS, ...options, ...(input.settings || {}) };
    const dockDisplay = displays.find(display => display.id === dockDisplayId) || displays[0];
    const cursorDisplay = Displays.pickDisplay(displays, cursor);
    const forced = Boolean(input.forceOpen);
    if (forced) lastForceAt = now;
    const pinned = Boolean(input.pinned) || o.autoHide === false;
    const layout = layoutFor(mode, dockDisplay.bounds, { ...o, edgePx: o.zonePx || o.edgePx, workArea: dockDisplay.workArea });
    const panelRect = input.panelRect || layout.window;
    const hold = inflate(panelRect, o.slopPx);
    let reason = lastReason;
    const before = revealed;

    if (!revealed) {
      // Moving the dock to another display is main's debounced follower (displays.createFollower), not decided here.
      const onDockDisplay = cursorDisplay.id === dockDisplay.id && Displays.inside(cursor, dockDisplay.bounds);
      const inZone = Boolean(onDockDisplay) && inside(cursor, layout.trigger);
      const shared = inZone && edgeIsShared(mode, dockDisplay.bounds, cursor, displays);
      // A seam point normally never opens the bar (the cursor is just crossing screens). If the whole edge is a seam the
      // bar would be unreachable, so there the user must rest on it for the longer shared-edge dwell instead.
      const fullySharedEdge = shared && edgeFullyShared(mode, dockDisplay.bounds, layout.trigger, displays);
      const inEdge = inZone && (!shared || fullySharedEdge);
      const dwellMs = fullySharedEdge ? o.sharedEdgeDwellMs : o.edgeDwellMs;
      if (inEdge) { if (edgeSince === null) edgeSince = now; } else edgeSince = null;
      if (pinned) { revealed = true; reason = "pinned"; }
      else if (forced) { revealed = true; reason = "attention"; }
      else if (inEdge && now - edgeSince >= dwellMs) { revealed = true; reason = "edge"; }
      if (revealed) { outsideSince = null; edgeSince = null; }
    } else {
      if (inside(cursor, hold)) outsideSince = null;
      else if (outsideSince === null) outsideSince = now;
      const outsideLongEnough = outsideSince !== null && now - outsideSince >= o.hideDelayMs;
      const idleLongEnough = now - lastForceAt >= o.idleCollapseMs;
      if (!pinned && !forced && outsideLongEnough && idleLongEnough) { revealed = false; outsideSince = null; reason = "idle"; }
      else if (pinned) reason = "pinned";
      else if (forced) reason = "attention";
    }
    lastReason = reason;
    return { revealed, changed: revealed !== before, reason, cursorInPanel: revealed && inside(cursor, hold), cursorDisplayId: cursorDisplay ? cursorDisplay.id : null, layout, displayId: dockDisplay.id };
  }
  return { update, get revealed() { return revealed; }, reset() { revealed = false; outsideSince = null; edgeSince = null; lastForceAt = -Infinity; } };
}

module.exports = { DOCK_MODES, DEFAULTS, layoutFor, createDockController, displayContaining, inside, inflate, edgeIsShared, edgeFullyShared };
