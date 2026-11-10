const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const read = (...parts) => fs.readFileSync(path.join(__dirname, "..", ...parts), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
const sketch = read("sketch", "window", "sketch.css"), overlay = read("overlay", "overlay.css");

test("sketch window: the route is one accent over a neutral casing; no coloured glow on strokes, ghost cursor, card or calibration", () => {
  assert.match(sketch, /\.route-line \{[^}]*stroke: var\(--accent\)/); assert.match(sketch, /\.route-line-done \{[^}]*stroke: var\(--accent\)/); assert.match(sketch, /\.stroke \{[^}]*stroke: var\(--accent\)/);
  assert.match(sketch, /\.route-under \{[^}]*stroke: var\(--casing\)/);
  for (const [, filter] of sketch.matchAll(/filter\s*:\s*([^;}]+)/g)) assert.doesNotMatch(filter, /var\(|oklch|#[0-9a-f]{3,6}|rgb\((?!0 0 0)/i, `filter: ${filter}`);
  for (const [, shadow] of sketch.matchAll(/box-shadow\s*:\s*([^;}]+)/g)) assert.doesNotMatch(shadow, /var\(--(accent|ink|ghost|route)/, `box-shadow: ${shadow}`);
  assert.doesNotMatch(sketch, /--ink|--route|--ghost|--done|-glow/);
});
test("sketch window: nothing loops (the agent cursor only moves when main sends a cursor event) and only the shared easing is used", () => {
  assert.doesNotMatch(sketch, /animation\s*:|@keyframes|infinite/);
  assert.doesNotMatch(sketch + overlay, /ease-in-out|ease-out|ease-in\b|cubic-bezier|--ease-(in-out|out)/);
  assert.doesNotMatch(sketch + overlay, /text-transform\s*:\s*uppercase|font-weight\s*:\s*[789]00|font:\s*[789]00/);
});
test("overlay: the edge only moves while listening/thinking; with the voice off no animation runs", () => {
  for (const [, selector] of overlay.matchAll(/([^{}]+)\{[^}]*animation\s*:\s*(?!none)[^;}]*infinite/g)) assert.match(selector, /body:not\(\[data-state="off"\]\)|data-state="thinking"/, selector.trim());
  assert.match(overlay, /\.spin \{[^}]*animation: none/);
  assert.doesNotMatch(overlay, /\bborder\s*:|outline\s*:|box-shadow/);
});
