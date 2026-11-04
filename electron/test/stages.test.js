const test = require("node:test");
const assert = require("node:assert/strict");
const S = require("../renderer/stages.js");
const ids = rail => rail.stages.map(s => `${s.id}:${s.state}`).join(" ");
const stage = (name, ok = true, extra = {}) => ({ name, ok, manager: "OperationDirector", ...extra });
const op = (stages, extra = {}, vector = {}) => ({ operation_id: "o1", update_id: "u1", command_text: "x", active: true, transport_status: "published", disposition: "active", consumption_status: "pending_consumption", update: { vector: { command_layer: "operation", ...vector } }, intervention: { command_execution: { stages, state: "" } }, ...extra });

test("published but nothing consumed: only Heard+Understood are done, never later stages", () => {
  const rail = S.deriveOperationRail(op([stage("parsed")]));
  assert.equal(ids(rail), "heard:done understood:done assigned:active ordered:todo effect:todo");
  assert.equal(rail.effectObserved, false);
});
test("Effect observed appears only with the backend's effect_observed stage", () => {
  const upto = names => S.deriveOperationRail(op(names.map(n => stage(n)), {}, {}), { directRuntime: { kind: "live" } });
  assert.equal(upto(["parsed", "queued_or_assigned", "order_issued", "action_issued"]).effectObserved, false);
  const rail = upto(["parsed", "queued_or_assigned", "order_issued", "action_issued", "effect_observed"]);
  assert.equal(rail.effectObserved, true); assert.equal(rail.stages[4].state, "done");
});
test("blocked disposition pins the blocker to the first open stage and carries the reason", () => {
  const rail = S.deriveOperationRail(op([stage("parsed"), stage("queued_or_assigned")], { disposition: "blocked", active: false, intervention: { command_execution: { failed: true, blocker_reason: "composition_shortfall", stages: [stage("parsed"), stage("queued_or_assigned")] } } }), { directRuntime: { kind: "live" } });
  assert.equal(rail.blocked, true); assert.equal(rail.reason, "composition_shortfall");
  assert.equal(ids(rail), "heard:done understood:done assigned:done ordered:blocked effect:todo");
});
test("a failed engine stage blocks even if the disposition looks active", () => {
  const rail = S.deriveOperationRail(op([stage("parsed"), stage("queued_or_assigned", false, { reason: "no_idle_units" })]));
  assert.equal(rail.blocked, true); assert.equal(rail.reason, "no_idle_units");
  assert.equal(rail.stages[2].state, "blocked");
});
test("superseded is a neutral end, not a failure", () => {
  const rail = S.deriveOperationRail(op([stage("parsed")], { disposition: "superseded", active: false }));
  assert.equal(rail.blocked, false); assert.equal(rail.ended, true);
});
test("dry-run fake bot: Ordered/Effect render as simulation, never as real", () => {
  const stages = ["parsed", "queued_or_assigned", "order_issued", "effect_observed"].map(n => stage(n));
  const rail = S.deriveOperationRail(op(stages), { directRuntime: { kind: "dry_run_fake_bot" } });
  assert.equal(rail.effectObserved, false); assert.equal(rail.simulatedEffect, true); assert.equal(rail.runtime, "dry_run_fake_bot");
  assert.equal(ids(rail), "heard:done understood:done assigned:done ordered:sim effect:sim");
});
test("missing direct_runtime on a non-macro op is unknown, macro is MicroMachine", () => {
  assert.equal(S.deriveOperationRail(op([stage("parsed")])).runtime, "unknown");
  assert.equal(S.deriveOperationRail(op([stage("parsed")], {}, { command_layer: "macro" })).runtime, "micromachine");
  assert.equal(S.runtimeKind("micro", { kind: "bogus" }), "unknown");
});
test("parallel operations get their own rails; stale rails survive the poll gap", () => {
  const status = { operations: [op([stage("parsed"), stage("queued_or_assigned")], { operation_id: "a" }), op([stage("parsed")], { operation_id: "b" })] };
  const view = S.deriveCommand({ updateId: "u1", text: "x" }, status);
  assert.equal(view.kind, "operations"); assert.equal(view.rails.length, 2);
  assert.notEqual(ids(view.rails[0].rail), ids(view.rails[1].rail));
});
test("command without operations: refused status blocks Understood with the backend reason", () => {
  const view = S.deriveCommand({ updateId: "u9", text: "x" }, { operations: [], latest_request: { update_id: "u9", status: "refused", refusal_reason: "정책 위반" } });
  assert.equal(view.kind, "command"); assert.equal(view.rail.blocked, true); assert.equal(view.rail.reason, "정책 위반");
  assert.equal(view.rail.stages[1].state, "blocked");
});
test("send errors (e.g. direct_control_conflict) show as Blocked, not success", () => {
  const rail = S.deriveCommandRail({ text: "후퇴", error: "direct_control_conflict" }, null);
  assert.equal(rail.blocked, true); assert.equal(rail.stages[1].detail, "direct_control_conflict");
  assert.equal(rail.effectObserved, false);
});
test("direct_executed in dry-run is simulation; with unknown runtime nothing past Understood is claimed", () => {
  const card = { updateId: "u2", text: "x", tier: "operation" };
  const req = { update_id: "u2", status: "direct_executed" };
  const sim = S.deriveCommandRail(card, req, { kind: "dry_run_fake_bot" });
  assert.equal(ids(sim), "heard:done understood:done assigned:sim ordered:sim effect:todo");
  const unknown = S.deriveCommandRail(card, req, undefined);
  assert.equal(ids(unknown), "heard:done understood:done assigned:todo ordered:todo effect:todo");
});
