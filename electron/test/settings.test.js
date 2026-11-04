const test = require("node:test");
const assert = require("node:assert/strict");
const S = require("../renderer/settings.js");
const I = require("../renderer/i18n.js");

test("defaults cover every group and match the documented values", () => {
  const d = S.defaults();
  assert.deepEqual(Object.keys(d), ["glass", "layout", "rainbow", "voice", "server"]);
  assert.equal(d.glass.alpha, 0.28); assert.equal(d.glass.blur, 12); assert.equal(d.glass.vibrancy, false);
  assert.equal(d.layout.dock, "bottom"); assert.equal(d.layout.autoHide, true); assert.equal(d.layout.hideDelay, 600); assert.equal(d.layout.zone, 2); assert.equal(d.layout.display, "cursor");
  assert.equal(d.rainbow.enabled, true); assert.equal(d.rainbow.depth, 90);
  assert.equal(d.voice.silence, 450); assert.equal(d.voice.mode, "always"); assert.equal(d.voice.shortcut, "CommandOrControl+Shift+Space"); assert.equal(d.voice.language, "ko");
  assert.equal(d.server.url, "http://127.0.0.1:8765");
  assert.equal(S.defaults({ serverUrl: "http://localhost:9000" }).server.url, "http://localhost:9000");
  assert.equal(S.defaults({ serverUrl: "https://evil.example" }).server.url, "http://127.0.0.1:8765");
});
test("every default sits inside its own range and passes its own validator", () => {
  const d = S.defaults();
  for (const group of S.GROUPS) for (const [key, spec] of Object.entries(S.SCHEMA[group])) {
    assert.doesNotThrow(() => S.validatePatch({ [group]: { [key]: d[group][key] } }), `${group}.${key}`);
    if (spec.type === "num") { assert.ok(spec.min <= spec.def && spec.def <= spec.max, `${group}.${key}`); }
  }
});
test("every numeric setting rejects values just outside its range and accepts the bounds (no silent clamping)", () => {
  for (const group of S.GROUPS) for (const [key, spec] of Object.entries(S.SCHEMA[group])) {
    if (spec.type !== "num") continue;
    assert.doesNotThrow(() => S.validatePatch({ [group]: { [key]: spec.min } }), `${group}.${key} min`);
    assert.doesNotThrow(() => S.validatePatch({ [group]: { [key]: spec.max } }), `${group}.${key} max`);
    const step = spec.integer ? 1 : 0.01;
    assert.throws(() => S.validatePatch({ [group]: { [key]: spec.min - step } }), /must be/, `${group}.${key} below`);
    assert.throws(() => S.validatePatch({ [group]: { [key]: spec.max + step } }), /must be/, `${group}.${key} above`);
    for (const bad of [NaN, Infinity, "5", null, true]) assert.throws(() => S.validatePatch({ [group]: { [key]: bad } }), /must be/, `${group}.${key} ${bad}`);
  }
});
test("integers are rounded, decimals are kept to 2 places", () => {
  assert.equal(S.validatePatch({ glass: { blur: 12.6 } }).glass.blur, 13);
  assert.equal(S.validatePatch({ glass: { alpha: 0.2849 } }).glass.alpha, 0.28);
});
test("enums, booleans and strings are strictly validated", () => {
  for (const dock of ["bottom", "right", "left", "top"]) assert.equal(S.validatePatch({ layout: { dock } }).layout.dock, dock);
  assert.throws(() => S.validatePatch({ layout: { dock: "center" } }), /layout\.dock/);
  assert.throws(() => S.validatePatch({ voice: { mode: "toggle" } }), /voice\.mode/);
  assert.throws(() => S.validatePatch({ voice: { language: "fr" } }), /voice\.language/);
  assert.throws(() => S.validatePatch({ glass: { vibrancy: 1 } }), /boolean/);
  assert.throws(() => S.validatePatch({ layout: { display: "all" } }), /layout\.display/);
  assert.equal(S.validatePatch({ layout: { display: "id:2" } }).layout.display, "id:2");
  assert.throws(() => S.validatePatch({ voice: { device: "x".repeat(300) } }), /voice\.device/);
  assert.throws(() => S.validatePatch({ voice: { device: "bad<script>" } }), /voice\.device/);
  assert.equal(S.validatePatch({ voice: { device: "default" } }).voice.device, "default");
});
test("server.url must be a loopback http origin", () => {
  assert.equal(S.validatePatch({ server: { url: "http://localhost:9000" } }).server.url, "http://localhost:9000");
  for (const url of ["https://example.com", "http://10.1.1.1:8765", "http://127.0.0.1.evil.test", "http://127.0.0.1:8765/api", "http://u:p@127.0.0.1", "ftp://127.0.0.1", "nonsense"]) assert.throws(() => S.validatePatch({ server: { url } }), /server\.url|invalid format/, url);
});
test("shortcut must be a valid accelerator", () => {
  assert.equal(S.validatePatch({ voice: { shortcut: "CommandOrControl+Alt+M" } }).voice.shortcut, "CommandOrControl+Alt+M");
  for (const shortcut of ["", "M", "Shift", "Ctrl+", "CommandOrControl+Shift+Nope", "x".repeat(60)]) assert.throws(() => S.validatePatch({ voice: { shortcut } }), /voice\.shortcut/, shortcut);
});
test("unknown groups/keys, empty and non-object patches are rejected", () => {
  assert.throws(() => S.validatePatch({ foo: { a: 1 } }), /unknown settings group/);
  assert.throws(() => S.validatePatch({ glass: { nope: 1 } }), /unknown setting glass\.nope/);
  assert.throws(() => S.validatePatch({}), /empty/);
  assert.throws(() => S.validatePatch({ glass: {} }), /empty/);
  assert.throws(() => S.validatePatch(null), /object/);
  assert.throws(() => S.validatePatch([]), /object/);
  assert.throws(() => S.validatePatch({ glass: 5 }), /object/);
});
test("stored settings are sanitised field by field; corrupt files never break the UI", () => {
  const d = S.defaults();
  assert.deepEqual(S.sanitizeStored(null), d);
  assert.deepEqual(S.sanitizeStored("junk"), d);
  const s = S.sanitizeStored({ glass: { alpha: 0.6, blur: 999, hue: "red" }, layout: { dock: "left", zone: 0 }, voice: { shortcut: "bad" }, evil: { x: 1 } });
  assert.equal(s.glass.alpha, 0.6); assert.equal(s.glass.blur, 12); assert.equal(s.glass.hue, 250);
  assert.equal(s.layout.dock, "left"); assert.equal(s.layout.zone, 2); assert.equal(s.voice.shortcut, "CommandOrControl+Shift+Space");
  assert.equal(s.evil, undefined);
  assert.equal(S.sanitizeStored({ server: { url: "https://evil.example" } }).server.url, "http://127.0.0.1:8765");
});
test("merge applies a patch group-wise without mutating the input", () => {
  const d = S.defaults();
  const m = S.merge(d, { glass: { alpha: 0.5 } });
  assert.equal(m.glass.alpha, 0.5); assert.equal(m.glass.blur, 12); assert.equal(d.glass.alpha, 0.28); assert.notEqual(m.layout, d.layout);
});
test("reset patch restores every default and validates", () => {
  const reset = S.resetPatch();
  assert.deepEqual(S.merge({ ...S.defaults(), glass: { ...S.defaults().glass, alpha: 0.9 } }, reset), S.defaults());
  assert.doesNotThrow(() => S.validatePatch({ glass: reset.glass, layout: reset.layout, rainbow: reset.rainbow, voice: reset.voice }));
});
test("alpha nudge: 5% steps, clamped", () => {
  assert.equal(S.nudgeAlpha(0.28, 1), 0.33); assert.equal(S.nudgeAlpha(0.28, -1), 0.23); assert.equal(S.nudgeAlpha(0.1, -1), 0.08); assert.equal(S.nudgeAlpha(0.9, 1), 0.9);
});
test("overlay and voice views expose only their own subset", () => {
  const s = S.defaults(); s.rainbow.speed = 10; s.voice.sensitivity = 100; s.voice.mode = "ptt";
  const o = S.overlayView(s);
  assert.equal(o.loopSeconds, 3); assert.equal(o.depth, 90); assert.equal(o.glass, undefined); assert.equal(o.silence, undefined);
  const v = S.voiceView(s);
  assert.equal(v.ptt, true); assert.equal(v.threshold, 0.1); assert.equal(S.voiceView(S.defaults()).threshold, 0.5); assert.equal(v.rainbow, undefined);
});
test("every schema key has a label in both languages (no raw keys on screen)", () => {
  for (const lang of ["ko", "en"]) for (const group of S.GROUPS.filter(g => g !== "server")) for (const key of Object.keys(S.SCHEMA[group])) {
    if (group === "voice" && key === "device") continue; // labelled as voice_device too
    assert.ok(I.tables[lang][`${group}_${key}`], `${lang} ${group}_${key}`);
  }
  for (const tab of ["glass", "layout", "rainbow", "voice", "server", "reset"]) for (const lang of ["ko", "en"]) assert.ok(I.tables[lang][`tab_${tab}`], `${lang} tab_${tab}`);
});
test("blocked reasons collapse to exactly one word", () => {
  const ko = I.create("ko");
  assert.equal(ko.reason("composition_shortfall: 마린 4기 중 2기만 사용 가능"), "병력부족");
  assert.equal(ko.reason("not enough minerals"), "자원부족");
  assert.equal(ko.reason("direct_control_conflict: Direct lease owns the units"), "충돌");
  assert.equal(ko.reason("지원하지 않는 명령입니다"), "거절");
  assert.equal(ko.reason("action 'stop_group' was refused without issuing orders: insufficient_units."), "병력부족");
  assert.equal(ko.reason("unit tags already owned: [13, 14]"), "충돌");
  assert.equal(ko.reason("missing_prerequisite_chain"), "선행필요");
  assert.equal(ko.reason(""), "막힘");
  assert.equal(I.create("en").reason("not enough minerals"), "Resources");
});
