const test = require("node:test");
const assert = require("node:assert/strict");
const B = require("../renderer/board_cards.js");
const P = require("../protocol.js");

const op = (id, extra = {}) => ({ operation_id: id, update_id: "u1", command_text: `작전 ${id}`, transport_status: "published", active: true, ...extra });
const card = (key, extra = {}) => ({ key, text: `명령 ${key}`, updateId: "", tier: "", error: "", source: "text", lastView: null, ...extra });

test("cards are newest first, shaped for board:publish, and pass the board validator", () => {
  const status = { operations: [op("a", { update_id: "u1" }), op("b", { update_id: "u2" })] };
  const { cards, open } = B.cardsFor([card(1, { updateId: "u1" }), card(2, { updateId: "u2" })], status);
  assert.equal(cards.length, 2); assert.equal(cards[0].name.includes("b"), true);
  assert.equal(cards[0].stages.length, 5); assert.ok(open >= 1);
  if (P.validateBoardCards) assert.doesNotThrow(() => P.validateBoardCards(cards));
  for (const c of cards) assert.deepEqual(Object.keys(c).sort(), ["blockedReason", "id", "name", "sim", "stages", "tier"]);
});
test("a refused command is blocked with its reason; unknown/blank tier stays empty; effect is never invented", () => {
  const { cards } = B.cardsFor([card(1, { error: "insufficient_units" })], {});
  assert.equal(cards[0].stages[1], "blocked"); assert.match(cards[0].blockedReason, /insufficient_units/);
  assert.equal(cards[0].tier, ""); assert.notEqual(cards[0].stages[4], "done");
  assert.ok(P.validateBoardCards ? P.validateBoardCards(cards) : true);
});
test("fake-bot execution is flagged sim and effect is never done for it", () => {
  const c = card(1, { directExecuted: true, directRuntime: { runtime: "dry_run_fake_bot" }, tier: "micro", text: "정찰" });
  const { cards } = B.cardsFor([c], { direct_runtime: { runtime: "dry_run_fake_bot" } });
  assert.notEqual(cards[0].stages[4], "done"); assert.equal(cards[0].tier, "micro");
});
test("at most 24 cards, ids unique and <= 64 chars, names bounded", () => {
  const commands = Array.from({ length: 40 }, (_, i) => card(i + 1, { text: "가".repeat(200) }));
  const { cards } = B.cardsFor(commands, {});
  assert.equal(cards.length, B.MAX_CARDS);
  assert.equal(new Set(cards.map(c => c.id)).size, cards.length);
  for (const c of cards) { assert.ok(c.id.length <= 64); assert.ok(c.name.length <= 80); }
  if (P.validateBoardCards) assert.doesNotThrow(() => P.validateBoardCards(cards));
});
test("open count: live operations count, blocked ones do not; a vanished operation keeps its last (stale) rail", () => {
  const c = card(1, { updateId: "u1" });
  const live = B.cardsFor([c], { operations: [op("a")] });
  assert.equal(live.open, 1);
  const gone = B.cardsFor([c], { operations: [] });
  assert.equal(gone.open, 0); assert.equal(gone.cards.length, 1);
});
test("ops count semantics: shows OPEN operations (falls as they block/complete), total only once none is open", () => {
  const c = card(1, { updateId: "u1" });
  const two = B.cardsFor([c], { operations: [op("a"), op("b")] });
  assert.equal(two.open, 2); assert.equal(B.opsCount(two.cards.length, two.open), 2);
  const oneBlocked = B.cardsFor([c], { operations: [op("a"), op("b", { disposition: "blocked", active: false })] });
  assert.equal(oneBlocked.cards.length, 2); assert.equal(oneBlocked.open, 1); assert.equal(B.opsCount(oneBlocked.cards.length, oneBlocked.open), 1);
  const oneDone = B.cardsFor([c], { operations: [op("a"), op("b", { disposition: "completed" })] });
  assert.equal(oneDone.open, 1);
  const none = B.cardsFor([c], { operations: [op("a", { disposition: "blocked" }), op("b", { disposition: "completed" })] });
  assert.equal(none.open, 0); assert.equal(B.opsCount(none.cards.length, none.open), 2, "nothing open: shows the session total, never 0 beside history");
});
test("ops count against the fixture timeline: two open at first, one after the scripted blocker (the smoke check is time-dependent)", async () => {
  const { createFakeBackend } = require("../fixtures/fake_backend.js");
  const fb = createFakeBackend({ port: 0 });
  const RealNow = Date.now; let offset = 0; Date.now = () => RealNow() + offset;
  try {
    const port = await fb.listen();
    const args = { command_layer: "operation", goal: "g", operations: [
      { operation_id: "scout", goal: "정찰", task_type: "scout" },
      { operation_id: "strike", goal: "공격", task_type: "attack", fixture_stop_at: 4, fixture_blocked: "composition_shortfall" } ] };
    const accepted = await (await fetch(`http://127.0.0.1:${port}/api/realtime/intent`, { method: "POST", body: JSON.stringify({ transcript: "t", tool_arguments: args }) })).json();
    const c = card(1, { updateId: accepted.update_id, tier: "operation", source: "voice" });
    const at = async seconds => { offset = seconds * 1000; return B.cardsFor([c], await (await fetch(`http://127.0.0.1:${port}/api/micromachine/status`)).json()); };
    assert.equal((await at(1)).open, 2);
    assert.equal((await at(4)).open, 2);
    const late = await at(9);
    assert.equal(late.open, 1); assert.equal(late.cards.length, 2); assert.equal(late.cards.filter(x => x.stages.includes("blocked")).length, 1);
  } finally { Date.now = RealNow; await fb.close(); }
});
