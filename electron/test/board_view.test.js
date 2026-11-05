const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const V = require("../board/view.js");
const { createPaging } = require("../board/paging.js");
const S = require("../renderer/settings.js");
const P = require("../protocol.js");

const card = (id, over = {}) => ({ id, name: "앞마당 방어", tier: "operation", stages: ["done", "done", "active", "todo", "todo"], blockedReason: "", sim: false, ...over });
const model = (cards, settings = S.defaults()) => { const p = createPaging({ pageSize: 3, ageOutMs: 0 }); p.ingest(cards, 0); return V.buildBoardView(p.view(0), settings); };

test("truthfulness: effect word only when stage 5 is done and not simulated; fake bot shows 시뮬", () => {
  const real = model([card("a", { stages: ["done", "done", "done", "done", "done"] })]).cards[0];
  assert.equal(real.status, "effect"); assert.equal(real.statusWord, "효과");
  const fake = model([card("b", { sim: true, stages: ["done", "done", "sim", "sim", "todo"] })]).cards[0];
  assert.equal(fake.status, "sim"); assert.equal(fake.statusWord, "시뮬"); assert.equal(fake.simWord, "시뮬");
  const fakeEffect = model([card("c", { sim: true, stages: ["done", "done", "sim", "sim", "sim"] })]).cards[0];
  assert.notEqual(fakeEffect.status, "effect"); assert.doesNotMatch(fakeEffect.bar, /^#{5}$/);
  const ordered = model([card("d", { stages: ["done", "done", "done", "done", "active"] })]).cards[0];
  assert.notEqual(ordered.status, "effect"); assert.equal(ordered.statusWord, "명령");
});
test("blocked shows exactly one reason word", () => {
  const c = model([card("a", { stages: ["done", "blocked", "todo", "todo", "todo"], blockedReason: "not enough minerals" })]).cards[0];
  assert.equal(c.status, "blocked"); assert.equal(c.statusWord, "자원부족"); assert.equal(c.title, "not enough minerals");
  const en = S.defaults(); en.voice.language = "en";
  assert.equal(model([card("a", { stages: ["done", "blocked", "todo", "todo", "todo"], blockedReason: "not enough minerals" })], en).cards[0].statusWord, "Resources");
});
test("terminal bar: [####-] style, one char per stage", () => {
  assert.equal(V.stageBar(["done", "done", "done", "done", "todo"]), "####-");
  assert.equal(V.stageBar(["done", "active", "todo", "blocked", "ended"]), "#>-x.");
  assert.equal(V.stageBar(["done", "done", "sim", "sim", "todo"]), "##~~-");
});
test("skin and opacity come from the shared settings; unknown skin falls back to cosmic", () => {
  const s = S.defaults(); s.glass.skin = "terminal"; s.board.opacity = 0.4;
  const m = model([card("a")], s);
  assert.equal(m.skin, "terminal"); assert.equal(m.opacity, 0.4);
  assert.equal(V.skinOf({ glass: { skin: "weird" } }), "cosmic"); assert.equal(V.skinOf(undefined), "cosmic");
});
test("modes: tab shows the word tab with the live count, hidden has none", () => {
  const p = createPaging({ pageSize: 3 }); p.ingest([], 0);
  assert.equal(V.buildBoardView(p.view(0), S.defaults()).tabLabel, "작전 0");
  const en = S.defaults(); en.voice.language = "en";
  assert.equal(V.buildBoardView(p.view(0), en).tabLabel, "Operation 0");
  assert.equal(V.buildBoardView({ ...p.view(0), mode: "board" }, S.defaults()).tabLabel, "");
});
test("every word on the board is short (words-only surface)", () => {
  const m = model([card("a", { stages: ["done", "blocked", "todo", "todo", "todo"], blockedReason: "x", sim: true })]).cards[0];
  for (const w of [m.tierWord, m.statusWord, m.simWord]) assert.ok(w.length <= 8, w);
});

test("renderer is words only: no helper paragraphs, no innerHTML, no network, CSP-friendly", () => {
  const dir = path.join(__dirname, "..", "board");
  const html = fs.readFileSync(path.join(dir, "index.html"), "utf8"), js = fs.readFileSync(path.join(dir, "board.js"), "utf8"), css = fs.readFileSync(path.join(dir, "board.css"), "utf8");
  assert.doesNotMatch(html, /<p[ >]|style=|onclick/); assert.doesNotMatch(js, /innerHTML|\bfetch\(|XMLHttpRequest|WebSocket|eval\(/);
  assert.match(css, /body\[data-skin="terminal"\]/); assert.match(css, /--board-alpha/); assert.match(css, /@keyframes blink/); assert.match(css, /repeating-linear-gradient/);
  assert.match(css, /prefers-reduced-motion/);
});

test("board:publish validator: shape, caps, state enum", () => {
  const ok = P.validateBoardCards([{ id: "a", name: "작전", tier: "operation", stages: ["done", "done", "active", "todo", "todo"], blockedReason: "", sim: false }]);
  assert.deepEqual(ok[0], { id: "a", name: "작전", tier: "operation", stages: ["done", "done", "active", "todo", "todo"], blockedReason: "", sim: false });
  // HUD may send {state} objects; they normalise to strings
  assert.deepEqual(P.validateBoardCards([{ id: "b", name: "x", tier: "", stages: ["done", "todo", "todo", "todo", "todo"].map(state => ({ id: "s", state, detail: "" })), blockedReason: null }])[0].stages, ["done", "todo", "todo", "todo", "todo"]);
  assert.deepEqual(P.validateBoardCards([]), []);
  const bad = (card, re) => assert.throws(() => P.validateBoardCards([{ id: "a", name: "n", tier: "macro", stages: ["todo", "todo", "todo", "todo", "todo"], ...card }]), re);
  bad({ stages: ["todo"] }, /5 stages/); bad({ stages: ["todo", "todo", "todo", "todo", "nope"] }, /stage state/); bad({ tier: "boss" }, /tier/);
  bad({ id: "" }, /id/); bad({ id: "x".repeat(65) }, /id/); bad({ name: "x".repeat(81) }, /name/); bad({ blockedReason: "x".repeat(201) }, /blockedReason/);
  bad({ sim: "yes" }, /sim/); bad({ unit_tags: [1] }, /unexpected/);
  assert.throws(() => P.validateBoardCards("x"), /array/); assert.throws(() => P.validateBoardCards([null]), /object/);
  assert.throws(() => P.validateBoardCards(Array.from({ length: 25 }, (_, i) => ({ id: `i${i}`, name: "n", tier: "macro", stages: ["todo", "todo", "todo", "todo", "todo"] }))), /limited/);
  assert.throws(() => P.validateBoardCards([{ id: "d", name: "n", stages: ["todo", "todo", "todo", "todo", "todo"] }, { id: "d", name: "n", stages: ["todo", "todo", "todo", "todo", "todo"] }]), /duplicate/);
});

test("board settings: defaults, ranges, enums, accelerators, sanitising", () => {
  const d = S.defaults().board;
  assert.deepEqual(d, { enabled: true, anchor: "upper-left", pageSize: 3, width: 320, autoAdvanceSec: 6, offsetX: 12, offsetY: 64, hideWhenEmpty: false, ageOutSec: 45, opacity: 0.62, pageUpShortcut: "CommandOrControl+Shift+PageUp", pageDownShortcut: "CommandOrControl+Shift+PageDown" });
  assert.deepEqual(P.validateSettingsPatch({ board: { anchor: "left", pageSize: 4, autoAdvanceSec: 0 } }), { board: { anchor: "left", pageSize: 4, autoAdvanceSec: 0 } });
  for (const patch of [{ anchor: "right" }, { pageSize: 5 }, { pageSize: 2 }, { width: 299 }, { width: 341 }, { autoAdvanceSec: -1 }, { offsetX: 201 }, { opacity: 0.05 }, { opacity: 1.1 }, { pageUpShortcut: "PageUp" }, { hideWhenEmpty: "no" }, { nope: 1 }])
    assert.throws(() => P.validateSettingsPatch({ board: patch }), /must be|unknown|invalid format/, JSON.stringify(patch));
  const s = S.sanitizeStored({ board: { anchor: "bad", pageSize: 4, width: 9999 } });
  assert.equal(s.board.anchor, "upper-left"); assert.equal(s.board.pageSize, 4); assert.equal(s.board.width, 320);
  assert.equal(S.sanitizeStored({}).board.enabled, true);
});
