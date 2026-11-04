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
test("real backend lists EVERY stage; ok:false + reason means no evidence yet, not a failure", () => {
  const all = [stage("parsed"), stage("reduced"), stage("published"), stage("consumed_by_manager", false, { reason: "Telemetry has not consumed the latest update id." }), stage("queued_or_assigned", false, { reason: "No manager queue or squad assignment evidence." }), stage("order_issued", false, { reason: "No concrete manager order was issued." }), stage("effect_observed", false, { reason: "No observed unit movement" })];
  const rail = S.deriveOperationRail(op(all, { transport_status: "published" }), { directRuntime: { kind: "live" } });
  assert.equal(rail.blocked, false); assert.equal(rail.effectObserved, false);
  assert.equal(ids(rail), "heard:done understood:done assigned:active ordered:todo effect:todo");
});
test("direct_failed transport is a block even without a direct_outcome", () => {
  const rail = S.deriveOperationRail(op([], { transport_status: "direct_failed", active: false }));
  assert.equal(rail.blocked, true);
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

const direct = (o, extra = {}) => ({ operation_id: "o", command_text: "x", transport_status: "direct_executed", disposition: "pending", consumption_status: "not_published", update: { vector: { command_layer: "operation" } }, intervention: { command_execution: { stages: [] } }, command_queue: { direct_outcome: o }, ...extra });
const FAKE = { directRuntime: { kind: "dry_run_fake_bot" } };
test("direct_outcome executed on the fake bot: assigned/ordered are simulation, effect never active or observed", () => {
  const rail = S.deriveOperationRail(direct({ status: "executed", reasons: [], shortfalls: [] }), FAKE);
  assert.equal(ids(rail), "heard:done understood:done assigned:sim ordered:sim effect:todo");
  assert.equal(rail.effectObserved, false); assert.equal(rail.blocked, false);
});
test("direct_outcome conflict is Blocked with the backend reason, never success", () => {
  const rail = S.deriveOperationRail(direct({ status: "conflict", reasons: ["Direct lease owns the units"] }), FAKE);
  assert.equal(rail.blocked, true); assert.match(rail.reason, /direct_control_conflict/); assert.equal(rail.stages[2].state, "blocked");
});
test("direct_outcome partial shows the shortfall as blocked at Ordered", () => {
  const rail = S.deriveOperationRail(direct({ status: "partial", reasons: [], shortfalls: [{ requested: 4, issued: 2 }] }), FAKE);
  assert.equal(rail.blocked, true); assert.match(rail.reason, /composition_shortfall: 4→2/); assert.equal(ids(rail), "heard:done understood:done assigned:sim ordered:blocked effect:todo");
});
test("direct_outcome refused / runtime_not_attached / clarification_required block with their reason", () => {
  for (const status of ["refused", "runtime_not_attached", "no_executable_action"]) assert.equal(S.deriveOperationRail(direct({ status, reasons: ["why"] }), FAKE).blocked, true, status);
  const c = S.deriveOperationRail(direct({ status: "clarification_required", reasons: ["어느 유닛?"] }), FAKE);
  assert.equal(c.blocked, true); assert.equal(c.stages[1].state, "blocked"); assert.equal(c.reason, "어느 유닛?");
});
test("live direct runtime: executed = assigned+ordered done, effect still not observed", () => {
  const rail = S.deriveOperationRail(direct({ status: "executed" }), { directRuntime: { kind: "live" } });
  assert.equal(ids(rail), "heard:done understood:done assigned:done ordered:done effect:active"); assert.equal(rail.effectObserved, false);
});
