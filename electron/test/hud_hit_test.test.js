const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const H = require("../renderer/hud_shape.js");
const K = require("../renderer/skins.js");
const S = require("../renderer/settings.js");

// main.js builds the hit-test polygon with exactly this call; this mirrors it so the wiring and the geometry are both pinned.
const panelPolygon = (settings, rect) => H.translate(H.shape(settings.layout.dock, rect.width, rect.height, { slantDeg: settings.glass.slant, radius: settings.glass.radius, smooth: K.smoothFor(settings.glass.skin) }).points, rect.x, rect.y);

test("main passes slant, radius and skin smoothing from the settings to the same shape the renderer clips with", () => {
  const dir = path.join(__dirname, "..");
  const main = fs.readFileSync(path.join(dir, "main.js"), "utf8"), js = fs.readFileSync(path.join(dir, "renderer", "renderer.js"), "utf8");
  assert.match(main, /HudShape\.shape\(settings\.layout\.dock, rect\.width, rect\.height, \{ slantDeg: settings\.glass\.slant, radius: settings\.glass\.radius, smooth: Skins\.smoothFor\(settings\.glass\.skin\) \}\)/);
  assert.match(js, /HudShape\.shape\(dock, width, height, \{ slantDeg: g\.slant, radius: g\.radius, smooth \}\)/);
  assert.doesNotMatch(main + js, /chamfer/i);
});
test("screen-space hit-test follows the ROUNDED corners on every dock and both skins (not a bigger rectangle)", () => {
  for (const skin of K.SKINS) for (const dock of H.DOCKS) {
    const settings = { layout: { dock }, glass: { ...S.defaults().glass, skin, radius: 22, slant: 14 } };
    const rect = { x: 300, y: 700, width: 800, height: 120 };
    const poly = panelPolygon(settings, rect);
    assert.equal(H.containsPoint(poly, rect.x + 400, rect.y + 60), true);
    for (const [cx, cy] of [[0, 0], [rect.width, 0], [0, rect.height], [rect.width, rect.height]]) {
      const sx = rect.x + cx + (cx === 0 ? 1 : -1), sy = rect.y + cy + (cy === 0 ? 1 : -1);
      assert.equal(H.containsPoint(poly, sx, sy), false, `${skin}/${dock} 1px inside corner ${cx},${cy} is click-through`);
    }
  }
});
