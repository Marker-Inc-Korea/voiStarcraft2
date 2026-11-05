const test = require("node:test");
const assert = require("node:assert/strict");
const fixture = require("../fixtures/fast_rules.json");
const { matchFastRule, PROMOTED_FAMILIES } = require("../fast_rules.js");

function project(hit) {
  if (!hit) return null;
  const out = { family: hit.family, action: hit.action };
  if (hit.flag) out.flag = hit.flag;
  if (hit.ability) { out.ability = hit.ability; out.unit_classes = hit.unit_classes; }
  return out;
}

test("JS rule matcher agrees with the Python matcher on every fixture phrase", () => {
  assert.ok(fixture.cases.length > 300);
  const mismatches = [];
  for (const { text, expect } of fixture.cases) {
    const got = project(matchFastRule(text));
    if (JSON.stringify(got) !== JSON.stringify(expect)) mismatches.push({ text, expect, got });
  }
  assert.deepEqual(mismatches, []);
});

test("promoted families: at most five, identical to the Python set", () => {
  assert.ok(PROMOTED_FAMILIES.length <= 5);
  assert.deepEqual([...PROMOTED_FAMILIES].sort(), fixture.families);
});

test("fixture exercises every promoted family and the negation guards", () => {
  const hit = new Set(fixture.cases.filter(c => c.expect).map(c => c.expect.family));
  for (const family of PROMOTED_FAMILIES) assert.ok(hit.has(family), family);
  for (const text of ["후퇴하지 마", "멈추지 마", "정지하지 마", "don't stop", "don't retreat", "스팀팩 쓰지 마"]) {
    assert.equal(matchFastRule(text), null, text);
    assert.ok(fixture.cases.some(c => c.text === text && c.expect === null), `${text} in fixture`);
  }
});

test("emergency words map to the right action", () => {
  assert.equal(matchFastRule("후퇴").action, "retreat");
  assert.equal(matchFastRule("홀드").action, "hold");
  assert.equal(matchFastRule("멈춰").action, "stop");
  assert.equal(matchFastRule("멈춰 서").action, "hold");
  assert.equal(matchFastRule("공격 취소").action, "stop");
  assert.equal(matchFastRule(null), null);
});
