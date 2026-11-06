const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const K = require("../renderer/skins.js");
const S = require("../renderer/settings.js");
const I = require("../renderer/i18n.js");

test("skin setting: cosmic | terminal, default cosmic, strict enum, sanitised, live-patchable", () => {
  assert.equal(S.defaults().glass.skin, "cosmic");
  assert.equal(S.validatePatch({ glass: { skin: "terminal" } }).glass.skin, "terminal");
  for (const bad of ["Terminal", "matrix", "", 1, null]) assert.throws(() => S.validatePatch({ glass: { skin: bad } }), /glass\.skin/);
  assert.equal(S.sanitizeStored({ glass: { skin: "matrix" } }).glass.skin, "cosmic");
  assert.equal(S.sanitizeStored({ glass: { skin: "terminal" } }).glass.skin, "terminal");
  assert.equal(S.merge(S.defaults(), { glass: { skin: "terminal" } }).glass.skin, "terminal");
  assert.deepEqual(K.SKINS, S.SCHEMA.glass.skin.values);
  for (const lang of ["ko", "en"]) for (const key of ["glass_skin", "skin_cosmic", "skin_terminal", "glass_slant"]) assert.ok(I.tables[lang][key], `${lang} ${key}`);
  assert.equal(I.tables.ko.skin_cosmic, "우주"); assert.equal(I.tables.ko.skin_terminal, "터미널"); assert.equal(I.tables.ko.glass_skin, "스킨");
});
test("slant setting: 0..30 degrees, default 14, strict", () => {
  assert.equal(S.defaults().glass.slant, 14);
  assert.equal(S.validatePatch({ glass: { slant: 0 } }).glass.slant, 0); assert.equal(S.validatePatch({ glass: { slant: 30 } }).glass.slant, 30);
  assert.throws(() => S.validatePatch({ glass: { slant: 31 } }), /glass\.slant/); assert.throws(() => S.validatePatch({ glass: { slant: -1 } }), /glass\.slant/);
  assert.equal(S.sanitizeStored({ glass: { slant: 99 } }).glass.slant, 14);
});
test("emergency shortcut settings: defaults Cmd/Ctrl+Shift+R/H/X, valid accelerators only", () => {
  const d = S.defaults().emergency;
  assert.deepEqual(d, { retreat: "CommandOrControl+Shift+R", hold: "CommandOrControl+Shift+H", stop: "CommandOrControl+Shift+X" });
  assert.equal(S.validatePatch({ emergency: { hold: "CommandOrControl+Alt+H" } }).emergency.hold, "CommandOrControl+Alt+H");
  for (const bad of ["", "R", "Shift", "CommandOrControl+Shift+Nope", 5]) assert.throws(() => S.validatePatch({ emergency: { stop: bad } }), /emergency\.stop/, String(bad));
  assert.equal(S.sanitizeStored({ emergency: { retreat: "bad" } }).emergency.retreat, "CommandOrControl+Shift+R");
  for (const lang of ["ko", "en"]) for (const key of ["tab_emergency", "emergency_retreat", "emergency_hold", "emergency_stop", "hold", "stop", "retreat", "em_nokey"]) assert.ok(I.tables[lang][key], `${lang} ${key}`);
  assert.equal(I.tables.ko.retreat, "후퇴"); assert.equal(I.tables.ko.hold, "홀드"); assert.equal(I.tables.ko.stop, "멈춰");
});
test("terminal skin stays legible at the lowest opacity: scrim/legible/glow grow as the panel gets clearer", () => {
  const clear = K.legibility("terminal", 0.08), solid = K.legibility("terminal", 0.9);
  assert.ok(Number(clear.scrim) > Number(solid.scrim) && Number(clear.legible) > Number(solid.legible) && Number(clear.glow) > Number(solid.glow));
  assert.ok(Number(clear.scrim) >= 0.4 && Number(solid.scrim) >= 0.4, "never a bare transparent background behind phosphor text");
  assert.ok(Number(clear.legible) <= 0.98);
  assert.deepEqual(K.legibility("cosmic", 0.28), { legible: "0.88", scrim: "0.44", glow: "0.50" }); // unchanged round-1 formulas
  assert.equal(K.legibility("nonsense", 0.28).scrim, K.legibility("cosmic", 0.28).scrim);
});
test("cssVars feed opacity/blur through unchanged for both skins; every skin is rounded (no chamfer concept)", () => {
  const glass = { ...S.defaults().glass, alpha: 0.5, blur: 20, radius: 26 };
  for (const skin of K.SKINS) { const v = K.cssVars(skin, glass); assert.equal(v["--glass-alpha"], "0.5"); assert.equal(v["--glass-blur"], "20px"); assert.equal(v["--radius"], "26px"); }
  assert.equal(K.CHAMFER, undefined); assert.equal(K.chamferFor, undefined);
  for (const skin of K.SKINS) { const m = K.smoothFor(skin); assert.ok(m > 0 && m <= 1, skin); }
  assert.equal(K.smoothFor("x"), K.smoothFor("cosmic")); assert.equal(K.normalize("x"), "cosmic");
});
test("both skins produce a rounded HUD shape through the same geometry main hit-tests", () => {
  const H = require("../renderer/hud_shape.js");
  for (const skin of K.SKINS) {
    const s = H.shape("bottom", 900, 90, { slantDeg: 14, radius: 22, smooth: K.smoothFor(skin) });
    assert.equal((s.path.match(/C/g) || []).length, 4, skin);
    for (const v of s.vertices) assert.equal(H.containsPoint(s.points, v[0], v[1]), false, `${skin} corner is rounded`);
  }
});
test("terminal stylesheet: phosphor green monospace, scanlines, CRT glow, block cursor, rounded geometry, no cosmic canvas", () => {
  const css = fs.readFileSync(path.join(__dirname, "..", "renderer", "style.css"), "utf8");
  const html = fs.readFileSync(path.join(__dirname, "..", "renderer", "index.html"), "utf8");
  const terminal = css.slice(css.indexOf("TERMINAL SKIN"));
  for (const needle of ["#2cff7a", '"SF Mono", Menlo, "IBM Plex Mono"', "repeating-linear-gradient(180deg", "radial-gradient(120% 130%", "@keyframes blink", ".cursor", 'content: ">"', "var(--term-glow)", "var(--scrim)"]) assert.ok(css.includes(needle), needle);
  assert.match(terminal, /body\[data-skin="terminal"\] #space[^{]*\{ display: none/);
  assert.match(terminal, /prefers-reduced-motion: reduce[\s\S]*\.cursor/);
  assert.match(html, /<i class="cursor"><\/i>/); assert.match(html, /id="term-word"/);
  assert.doesNotMatch(terminal, /Inter|Helvetica/);
});
