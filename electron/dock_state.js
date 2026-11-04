"use strict";
// Pure edge-dock logic: where the dock window goes, when it reveals, when it hides. No Electron,
// no timers, no I/O: main.js feeds it the cursor/displays/time every ~50 ms and applies the decision.
//
// Geometry is in screen DIP, same space as screen.getCursorScreenPoint() and display.bounds.
const DOCK_MODES = Object.freeze(["bottom", "right", "left", "top"]);
const DEFAULTS = Object.freeze({
  edgePx: 2,            // trigger zone thickness at the display edge
  edgeDwellMs: 120,     // cursor must stay in the zone this long (SC2 edge-scroll would otherwise pop the bar open)
  slopPx: 12,           // panel rect inflation while revealed
  hideDelayMs: 600,     // cursor must stay outside panel+slop this long before collapsing
  idleCollapseMs: 2500, // after the last forced-open reason ends
  bottomMaxWidth: 980, bottomWidthRatio: 0.9, bottomMaxHeight: 640, bottomHeightRatio: 0.75,
  sideWidth: 380, margin: 10,
  widthScale: 100, heightScale: 100, // percent
  autoHide: true, followCursor: true,
});

const inside = (point, rect) => point.x >= rect.x && point.x < rect.x + rect.width && point.y >= rect.y && point.y < rect.y + rect.height;
const inflate = (rect, amount) => ({ x: rect.x - amount, y: rect.y - amount, width: rect.width + 2 * amount, height: rect.height + 2 * amount });
function displayContaining(displays, point) {
  return displays.find(display => inside(point, display.bounds)) || null;
}

// Window = maximum expanded footprint of the mode on a display; it never resizes afterwards.
// trigger = the edge strip (screen coords) that reveals the dock. options.workArea keeps side docks out of the menu bar.
function layoutFor(mode, bounds, options = {}) {
  const o = { ...DEFAULTS, ...options };
  if (!DOCK_MODES.includes(mode)) throw new TypeError("unknown dock mode");
  const zone = Math.max(1, Math.round(o.edgePx));
  const ws = Math.min(100, Math.max(40, o.widthScale)) / 100;
  const hs = Math.min(100, Math.max(40, o.heightScale)) / 100;
  const wa = o.workArea || bounds;
  if (mode === "bottom" || mode === "top") {
    const baseWidth = Math.max(320, Math.min(o.bottomMaxWidth, Math.floor(bounds.width * o.bottomWidthRatio)));
    const baseHeight = Math.max(160, Math.min(o.bottomMaxHeight, Math.floor(bounds.height * o.bottomHeightRatio)));
    const width = Math.max(320, Math.floor(baseWidth * ws));
    const height = Math.max(160, Math.floor(baseHeight * hs));
    const x = bounds.x + Math.floor((bounds.width - width) / 2);
    if (mode === "bottom") {
      const y = bounds.y + bounds.height - height;
      return { window: { x, y, width, height }, trigger: { x, y: bounds.y + bounds.height - zone, width, height: zone }, handle: { x, y: bounds.y + bounds.height - 4, width, height: 4 } };
    }
    const y = Math.max(bounds.y, wa.y);
    return { window: { x, y, width, height }, trigger: { x, y: bounds.y, width, height: zone }, handle: { x, y: bounds.y, width, height: 4 } };
  }
  const width = Math.min(Math.floor((o.sideWidth + o.margin) * ws), bounds.width);
  const top = Math.max(bounds.y, wa.y);
  const bottom = Math.min(bounds.y + bounds.height, wa.y + wa.height);
  const fullHeight = bottom - top;
  const height = Math.max(240, Math.floor(fullHeight * hs));
  const y = top + Math.floor((fullHeight - height) / 2);
  if (mode === "right") {
    return { window: { x: bounds.x + bounds.width - width, y, width, height }, trigger: { x: bounds.x + bounds.width - zone, y, width: zone, height }, handle: { x: bounds.x + bounds.width - 4, y, width: 4, height } };
  }
  return { window: { x: bounds.x, y, width, height }, trigger: { x: bounds.x, y, width: zone, height }, handle: { x: bounds.x, y, width: 4, height } };
}

// True when another display lies directly beyond the edge at this point (cursor is just crossing screens).
function edgeIsShared(mode, bounds, point, displays) {
  const probe = mode === "bottom" ? { x: point.x, y: bounds.y + bounds.height + 1 }
    : mode === "top" ? { x: point.x, y: bounds.y - 1 }
    : mode === "right" ? { x: bounds.x + bounds.width + 1, y: point.y }
    : { x: bounds.x - 1, y: point.y };
  return displays.some(display => inside(probe, display.bounds));
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
    const cursorDisplay = displayContaining(displays, cursor);
    const forced = Boolean(input.forceOpen);
    if (forced) lastForceAt = now;
    const pinned = Boolean(input.pinned) || o.autoHide === false;
    const layout = layoutFor(mode, dockDisplay.bounds, { ...o, edgePx: o.zonePx || o.edgePx, workArea: dockDisplay.workArea });
    const panelRect = input.panelRect || layout.window;
    const hold = inflate(panelRect, o.slopPx);
    let moveToDisplayId = null;
    let reason = lastReason;
    const before = revealed;

    if (!revealed) {
      // While collapsed the window may follow the cursor's display so every monitor edge works.
      if (o.followCursor && cursorDisplay && cursorDisplay.id !== dockDisplay.id) moveToDisplayId = cursorDisplay.id;
      const onDockDisplay = cursorDisplay && cursorDisplay.id === dockDisplay.id;
      const inEdge = Boolean(onDockDisplay) && inside(cursor, layout.trigger) && !edgeIsShared(mode, dockDisplay.bounds, cursor, displays);
      if (inEdge) { if (edgeSince === null) edgeSince = now; } else edgeSince = null;
      if (pinned) { revealed = true; reason = "pinned"; }
      else if (forced) { revealed = true; reason = "attention"; }
      else if (inEdge && now - edgeSince >= o.edgeDwellMs) { revealed = true; reason = "edge"; }
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
    return { revealed, changed: revealed !== before, reason, cursorInPanel: revealed && inside(cursor, hold), moveToDisplayId, layout, displayId: dockDisplay.id };
  }
  return { update, get revealed() { return revealed; }, reset() { revealed = false; outsideSince = null; edgeSince = null; lastForceAt = -Infinity; } };
}

module.exports = { DOCK_MODES, DEFAULTS, layoutFor, createDockController, displayContaining, inside, inflate, edgeIsShared };
