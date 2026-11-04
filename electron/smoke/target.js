"use strict";
// Pure helper for the smoke drivers: which display to test and how to turn a fraction of it into a global cursor point.
// SMOKE_DISPLAY = primary (default) | <index into getAllDisplays()> | id:<displayId> | cursor (display under the cursor now)
function pickTarget(displays, primaryId, spec, cursor) {
  const text = String(spec || "primary").trim();
  if (text === "primary") return displays.find(d => d.id === primaryId) || displays[0];
  if (text === "cursor" && cursor) return displays.find(d => cursor.x >= d.bounds.x && cursor.x < d.bounds.x + d.bounds.width && cursor.y >= d.bounds.y && cursor.y < d.bounds.y + d.bounds.height) || displays[0];
  const byId = /^id:(\d+)$/.exec(text);
  if (byId) { const found = displays.find(d => d.id === Number(byId[1])); if (found) return found; throw new Error(`SMOKE_DISPLAY ${text}: no such display`); }
  if (/^\d+$/.test(text)) { const found = displays[Number(text)]; if (found) return found; throw new Error(`SMOKE_DISPLAY ${text}: index out of range (${displays.length} displays)`); }
  throw new Error(`SMOKE_DISPLAY ${text}: expected primary|<index>|id:<n>|cursor`);
}
// Fraction (0..1) of a display's bounds -> global cursor point, optionally offset by whole pixels from an edge.
const pointIn = (bounds, fx, fy, dx = 0, dy = 0) => ({ x: Math.round(bounds.x + bounds.width * fx) + dx, y: Math.round(bounds.y + bounds.height * fy) + dy });
// Bottom-edge hover point, `inset` px above the last row; side points likewise.
const edgePoint = (side, bounds, along = 0.5, inset = 1) => side === "bottom" ? { x: Math.round(bounds.x + bounds.width * along), y: bounds.y + bounds.height - inset }
  : side === "top" ? { x: Math.round(bounds.x + bounds.width * along), y: bounds.y + inset - 1 }
  : side === "right" ? { x: bounds.x + bounds.width - inset, y: Math.round(bounds.y + bounds.height * along) }
  : { x: bounds.x + inset - 1, y: Math.round(bounds.y + bounds.height * along) };
module.exports = { pickTarget, pointIn, edgePoint };
