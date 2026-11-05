const test = require("node:test");
const assert = require("node:assert/strict");
const { createPaging, isTerminal } = require("../board/paging.js");

const S = (...states) => states;
const card = (id, over = {}) => ({ id, name: `op ${id}`, tier: "operation", stages: S("done", "active", "todo", "todo", "todo"), blockedReason: "", sim: false, ...over });
const many = (n, over) => Array.from({ length: n }, (_, i) => card(`c${i}`, over));

test("1/N labels and page contents: 7 cards, page size 3 -> 3 pages (3,3,1)", () => {
  const p = createPaging({ pageSize: 3, autoAdvanceMs: 0 });
  p.ingest(many(7), 0);
  let v = p.view(0);
  assert.equal(v.label, "1/3"); assert.equal(v.cards.length, 3); assert.deepEqual(v.cards.map(c => c.id), ["c0", "c1", "c2"]);
  p.next(1); v = p.view(1); assert.equal(v.label, "2/3"); assert.deepEqual(v.cards.map(c => c.id), ["c3", "c4", "c5"]);
  p.next(2); v = p.view(2); assert.equal(v.label, "3/3"); assert.deepEqual(v.cards.map(c => c.id), ["c6"]);
  p.next(3); assert.equal(p.view(3).label, "1/3"); // wraps
  p.prev(4); assert.equal(p.view(4).label, "3/3");
});
test("a page never exceeds pageSize, whatever is published (cap 24 cards)", () => {
  for (const size of [3, 4]) {
    const p = createPaging({ pageSize: size, autoAdvanceMs: 0 });
    p.ingest(many(100), 0);
    assert.equal(p.view(0).total, 24);
    for (let i = 0; i < 20; i += 1) { assert.ok(p.view(i).cards.length <= size); p.next(i); }
  }
});
test("one page has no label", () => {
  const p = createPaging({ pageSize: 4 });
  p.ingest(many(4), 0);
  assert.equal(p.view(0).label, ""); assert.equal(p.view(0).pageCount, 1);
});
test("auto-advance every N seconds, paused while hovered, resumes after hover ends + pin", () => {
  const p = createPaging({ pageSize: 3, autoAdvanceMs: 6000 });
  p.ingest(many(7), 0);
  p.tick(5999); assert.equal(p.view(5999).label, "1/3");
  p.tick(6000); assert.equal(p.view(6000).label, "2/3");
  p.setHover(true, 6100);
  p.tick(20000); assert.equal(p.view(20000).label, "2/3");          // hovered: frozen
  p.setHover(false, 20000);
  p.tick(20500); assert.equal(p.view(20500).label, "2/3");          // hover pin
  p.tick(26100); assert.equal(p.view(26100).label, "3/3");
});
test("autoAdvance 0 = off", () => {
  const p = createPaging({ pageSize: 3, autoAdvanceMs: 0 });
  p.ingest(many(7), 0); p.tick(600000); assert.equal(p.view(600000).label, "1/3");
});
test("manual paging holds auto-advance for a while", () => {
  const p = createPaging({ pageSize: 3, autoAdvanceMs: 6000, manualHoldMs: 10000 });
  p.ingest(many(7), 0);
  p.next(1000);
  p.tick(8000); assert.equal(p.view(8000).label, "2/3");
  p.tick(11001); p.tick(17001); assert.notEqual(p.view(17001).label, "2/3");
});
test("a NEW card jumps to the page that contains it, then returns after jumpMs", () => {
  const p = createPaging({ pageSize: 3, autoAdvanceMs: 0, jumpMs: 4000, ageOutMs: 0 });
  const base = many(7);
  p.ingest(base, 0);
  p.next(10); p.prev(11); // on page 1, manual
  assert.equal(p.view(12).label, "1/3");
  // newest-first list: a new card at the END of the list lands on page 3
  const jumped = p.ingest([...base, card("late")], 100);
  assert.deepEqual(jumped, { page: 2, reason: "new" });
  assert.equal(p.view(100).label, "3/3");
  p.tick(4099); assert.equal(p.view(4099).label, "3/3");
  p.tick(4100); assert.equal(p.view(4100).label, "1/3");
});
test("a changed card jumps to its page; an unchanged republish does not", () => {
  const p = createPaging({ pageSize: 3, autoAdvanceMs: 0, ageOutMs: 0 });
  const list = many(7);
  p.ingest(list, 0);
  assert.equal(p.ingest(list.map(c => ({ ...c })), 50), null);
  const changed = list.map(c => (c.id === "c4" ? { ...c, stages: S("done", "done", "active", "todo", "todo") } : c));
  assert.deepEqual(p.ingest(changed, 100), { page: 1, reason: "changed" });
});
test("blocked beats new/changed; emergency beats blocked", () => {
  const p = createPaging({ pageSize: 3, autoAdvanceMs: 0, ageOutMs: 0 });
  const list = many(9);
  p.ingest(list, 0);
  const next = list.map(c => (c.id === "c7" ? { ...c, stages: S("done", "blocked", "todo", "todo", "todo"), blockedReason: "자원" } : c));
  next[1] = { ...next[1], name: "renamed" };
  assert.deepEqual(p.ingest(next, 10), { page: 2, reason: "blocked" });
  const emergency = [card("em", { tier: "emergency" }), ...next];
  assert.deepEqual(p.ingest(emergency, 20), { page: 0, reason: "emergency" });
});
test("single page: no jump", () => {
  const p = createPaging({ pageSize: 3 });
  assert.equal(p.ingest(many(2), 0), null);
  assert.equal(p.view(0).label, "");
});
test("finished cards age out after ageOutMs; live cards never do; an aged-out card does not come back unless it changes", () => {
  const p = createPaging({ pageSize: 3, ageOutMs: 45000, autoAdvanceMs: 0 });
  const done = card("done", { stages: S("done", "done", "done", "done", "done") });
  const live = card("live");
  p.ingest([live, done], 0);
  assert.equal(p.view(44999).total, 2);
  assert.equal(p.view(45000).total, 1);
  p.ingest([live, done], 46000);                       // HUD republishes the same finished card
  assert.equal(p.view(46000).total, 1);
  p.ingest([live, { ...done, name: "again" }], 47000); // changed -> visible again
  assert.equal(p.view(47000).total, 2);
  assert.equal(p.view(1e9).cards.some(c => c.id === "live"), true);
});
test("ageOut 0 = never; blocked and ended count as finished; sim fake-bot order counts as finished", () => {
  const forever = createPaging({ pageSize: 3, ageOutMs: 0 });
  forever.ingest([card("d", { stages: S("done", "done", "done", "done", "done") })], 0);
  assert.equal(forever.view(1e9).total, 1);
  assert.equal(isTerminal(card("b", { stages: S("done", "blocked", "todo", "todo", "todo") })), true);
  assert.equal(isTerminal(card("e", { stages: S("done", "ended", "todo", "todo", "todo") })), true);
  assert.equal(isTerminal(card("s", { sim: true, stages: S("done", "done", "sim", "sim", "todo") })), true);
  assert.equal(isTerminal(card("l")), false);
});
test("empty: tab ('작전 0') by default, hidden when hideWhenEmpty; live count excludes finished", () => {
  const p = createPaging({ pageSize: 3 });
  p.ingest([], 0);
  assert.equal(p.view(0).mode, "tab"); assert.equal(p.view(0).live, 0);
  p.configure({ hideWhenEmpty: true }); assert.equal(p.view(0).mode, "hidden");
  p.ingest([card("a"), card("b", { stages: S("done", "done", "done", "done", "done") })], 1);
  assert.equal(p.view(1).mode, "board"); assert.equal(p.view(1).live, 1);
});
test("page index is clamped when the list shrinks", () => {
  const p = createPaging({ pageSize: 3, autoAdvanceMs: 0, ageOutMs: 0 });
  p.ingest(many(7), 0); p.next(1); p.next(2);
  p.ingest(many(2), 3);
  const v = p.view(3); assert.equal(v.page, 0); assert.equal(v.label, ""); assert.equal(v.cards.length, 2);
});
test("pageSize change re-pages (3 -> 4)", () => {
  const p = createPaging({ pageSize: 3, autoAdvanceMs: 0 });
  p.ingest(many(7), 0);
  assert.equal(p.view(0).pageCount, 3);
  p.configure({ pageSize: 4 });
  assert.equal(p.view(0).pageCount, 2); assert.equal(p.view(0).cards.length, 4);
});
test("duplicate ids are collapsed; fresh flag clears after freshMs", () => {
  const p = createPaging({ pageSize: 3, freshMs: 2500 });
  p.ingest([card("a"), card("a")], 0);
  assert.equal(p.view(0).total, 1); assert.equal(p.view(0).cards[0].fresh, true); assert.equal(p.view(2500).cards[0].fresh, false);
});
