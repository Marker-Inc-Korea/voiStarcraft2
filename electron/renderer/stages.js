"use strict";
// Truthful execution-stage derivation. Pure functions over the JSON the backend already serves
// (/api/micromachine/status, /api/history); loaded as a classic script in the renderer and as a
// CommonJS module under `node --test`.
//
// Rule: a stage is only "done" when the backend reports evidence for it. Nothing here guesses.
// Heard         -> the utterance/text reached us (always true for a listed command)
// Understood    -> backend compiled & published it (parsed/reduced/published/consumed_by_manager)
// Assigned      -> OperationDirector reported queued_or_assigned
// Ordered       -> order_issued / action_issued
// Effect        -> effect_observed (movement or engagement telemetry). Never inferred from anything else.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.VoiStages = factory();
})(typeof self !== "undefined" ? self : this, function () {
  const STAGE_IDS = Object.freeze(["heard", "understood", "assigned", "ordered", "effect"]);
  const ENGINE_TO_UI = Object.freeze({
    parsed: "understood", reduced: "understood", published: "understood", consumed_by_manager: "understood",
    queued_or_assigned: "assigned", order_issued: "ordered", action_issued: "ordered", effect_observed: "effect",
  });
  const DONE_IMPLIES = Object.freeze({
    understood: ["parsed", "reduced", "published", "consumed_by_manager", "queued_or_assigned", "order_issued", "action_issued", "effect_observed"],
    assigned: ["queued_or_assigned", "order_issued", "action_issued", "effect_observed"],
    ordered: ["order_issued", "action_issued", "effect_observed"],
    effect: ["effect_observed"],
  });
  const BLOCKING_DISPOSITIONS = new Set(["blocked", "expired"]);
  const NEUTRAL_END_DISPOSITIONS = new Set(["superseded"]);
  const FAILED_TRANSPORT = new Set(["publish_failed", "refused", "clarification_required"]);
  const TIERS = Object.freeze(["macro", "operation", "micro", "emergency"]);

  const asObject = value => (value && typeof value === "object" && !Array.isArray(value) ? value : {});
  const asArray = value => (Array.isArray(value) ? value : []);
  const text = value => (typeof value === "string" ? value.trim() : "");

  function normalizeTier(value) {
    const tier = text(value).toLowerCase();
    return TIERS.includes(tier) ? tier : "";
  }
  function operationTier(op) {
    const vector = asObject(asObject(op.update).vector);
    const queue = asObject(op.command_queue);
    const layer = normalizeTier(vector.command_layer) || normalizeTier(queue.command_layer);
    if (layer) return layer;
    if (text(vector.override_level) === "emergency" || Object.keys(asObject(vector.emergency)).length) return "emergency";
    if (text(asObject(vector.tactical_task).task_type) === "execute_ability") return "micro";
    return "";
  }

  function stageEvidence(op) {
    const execution = asObject(asObject(op.intervention).command_execution);
    const done = new Set();
    const failed = new Map(); // engine stage name -> reason
    for (const stage of asArray(execution.stages)) {
      const item = asObject(stage);
      const name = text(item.name);
      if (!name) continue;
      if (item.ok === true) done.add(name);
      else if (item.ok === false) failed.set(name, text(item.reason) || text(item.manager) || name);
    }
    return { execution, done, failed };
  }
  const anyDone = (done, names) => names.some(name => done.has(name));

  // Reason text a human can act on, strongest evidence first.
  function blockerReason(op, evidence) {
    const compile = asObject(op.compile_result);
    return text(evidence.execution.blocker_reason)
      || text(asObject(evidence.execution.telemetry).blocked_reason)
      || [...evidence.failed.values()][0]
      || text(compile.refusal_reason) || text(compile.clarification_prompt)
      || text(op.transport_status) || text(op.disposition) || "";
  }

  // Where non-macro vectors really run. "dry_run_fake_bot" = scripted DemoFakeBotAI, NOT MicroMachine/SC2.
  // Macro vectors always go through the blackboard MicroMachine reads. A missing field is "unknown".
  function runtimeKind(tier, directRuntime) {
    if (tier === "macro") return "micromachine";
    const kind = text(asObject(directRuntime).kind);
    return kind === "dry_run_fake_bot" || kind === "live" ? kind : "unknown";
  }

  // Rail for one backend operation (a parallel operation has its own rail).
  function deriveOperationRail(op, context = {}) {
    op = asObject(op);
    const evidence = stageEvidence(op);
    const { done, failed } = evidence;
    const disposition = text(op.disposition);
    const transport = text(op.transport_status);
    const done5 = {
      heard: Boolean(text(op.command_text) || text(context.text)),
      understood: transport === "published" || op.active === true || anyDone(done, DONE_IMPLIES.understood),
      assigned: anyDone(done, DONE_IMPLIES.assigned),
      ordered: anyDone(done, DONE_IMPLIES.ordered),
      effect: anyDone(done, DONE_IMPLIES.effect),
    };
    const hardBlocked = BLOCKING_DISPOSITIONS.has(disposition) || evidence.execution.failed === true || evidence.execution.expired === true || FAILED_TRANSPORT.has(transport) || failed.size > 0;
    const neutralEnd = !hardBlocked && (NEUTRAL_END_DISPOSITIONS.has(disposition) || evidence.execution.superseded === true);
    const reason = hardBlocked || neutralEnd ? blockerReason(op, evidence) : "";
    const consumption = text(op.consumption_status);
    let firstOpen = STAGE_IDS.find(id => !done5[id]) || "";
    // A failed engine stage pins the blockage to its UI stage even if later flags look set.
    for (const name of failed.keys()) { const ui = ENGINE_TO_UI[name]; if (ui && STAGE_IDS.indexOf(ui) < STAGE_IDS.indexOf(firstOpen || "effect")) firstOpen = ui; }
    const tier = operationTier(op) || normalizeTier(context.tier);
    const runtime = runtimeKind(tier, context.directRuntime);
    const simulated = runtime === "dry_run_fake_bot";
    const stages = STAGE_IDS.map(id => {
      let state = done5[id] && !(hardBlocked && id === firstOpen) ? "done" : "todo";
      // Fake-bot execution is shown as simulation, never as a real order/effect.
      if (state === "done" && simulated && (id === "ordered" || id === "effect")) state = "sim";
      let detail = "";
      if (id === firstOpen && state !== "done") {
        if (hardBlocked) { state = "blocked"; detail = reason; }
        else if (neutralEnd) { state = "ended"; detail = reason || disposition; }
        else if (disposition === "completed") { state = "todo"; }
        else {
          state = "active";
          if (id === "assigned" || id === "ordered" || id === "effect") detail = consumption && consumption !== "consumed" ? consumption : "";
        }
      }
      return { id, state, detail };
    });
    return {
      stages, tier, disposition, blocked: hardBlocked, ended: neutralEnd, completed: disposition === "completed",
      reason, effectObserved: done5.effect && !simulated, simulatedEffect: done5.effect && simulated, runtime, consumption,
    };
  }

  // Rail for a command that has no backend operation yet (still compiling, refused, needs clarification, or emergency fast-path).
  function deriveCommandRail(card, latestRequest, directRuntime) {
    card = asObject(card);
    const request = asObject(latestRequest);
    const matches = text(card.updateId) && text(request.update_id) === text(card.updateId);
    const status = matches ? text(request.status) : "";
    const stages = STAGE_IDS.map(id => ({ id, state: "todo", detail: "" }));
    stages[0].state = text(card.text) ? "done" : "active";
    let reason = "";
    let blocked = false;
    if (card.error) { stages[1] = { id: "understood", state: "blocked", detail: String(card.error) }; blocked = true; reason = String(card.error); }
    else if (status === "refused" || status === "clarification_required") {
      reason = text(request.refusal_reason) || text(request.clarification_prompt) || status;
      stages[1] = { id: "understood", state: "blocked", detail: reason }; blocked = true;
    } else if (status === "direct_executed" || card.directExecuted) {
      // Non-macro vector executed by the direct runtime (fake bot in dry-run): never a real order/effect.
      // Understood is real (compiled+accepted). Assigned/Ordered are only claimed as simulation when the backend says so.
      stages[1].state = "done";
      const kind = runtimeKind(normalizeTier(card.tier) || "operation", directRuntime);
      if (kind === "dry_run_fake_bot") { stages[2].state = "sim"; stages[3].state = "sim"; }
      else if (kind === "live") { stages[2].state = "active"; }
      else stages[2].detail = "unknown_runtime";
    } else if (status === "compiled" || status === "published") {
      stages[1].state = "done"; stages[2].state = "active"; stages[2].detail = text(request.consumption_status);
    } else if (stages[0].state === "done") {
      stages[1].state = "active";
    }
    const tier = normalizeTier(card.tier) || normalizeTier(asObject(request.command_queue).command_layer);
    return { stages, tier, disposition: "", blocked, ended: false, completed: false, reason, effectObserved: false, simulatedEffect: false, runtime: runtimeKind(tier || "operation", directRuntime), consumption: matches ? text(request.consumption_status) : "" };
  }

  function operationsForCommand(card, operations) {
    const id = text(asObject(card).updateId);
    if (!id) return [];
    return asArray(operations).filter(op => text(asObject(op).update_id) === id);
  }

  // The rail kept for a command: operations if the backend lists them, else the command-level rail.
  function deriveCommand(card, status) {
    status = asObject(status);
    const ops = operationsForCommand(card, status.operations);
    if (ops.length) {
      const rails = ops.map(op => ({ op, rail: deriveOperationRail(op, { text: card.text, directRuntime: card.directRuntime || status.direct_runtime }) }));
      return { kind: "operations", rails };
    }
    return { kind: "command", rail: deriveCommandRail(card, status.latest_request, card.directRuntime || status.direct_runtime) };
  }

  function summarize(rail) {
    if (rail.blocked) return "blocked";
    if (rail.ended) return "ended";
    const last = [...rail.stages].reverse().find(stage => stage.state === "done");
    return last ? last.id : "";
  }

  return { STAGE_IDS, TIERS, ENGINE_TO_UI, deriveOperationRail, deriveCommandRail, deriveCommand, operationsForCommand, operationTier, normalizeTier, summarize, runtimeKind };
});
