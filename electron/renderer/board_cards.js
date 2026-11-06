"use strict";
// Pure view-model for the tactical board: the HUD still derives every operation rail from backend evidence (stages.js)
// but no longer draws it. This module turns the HUD's command list + backend status into the array the board window
// receives through `window.commander.board.publish(cards)` -> `board:publish` -> `board:cards`.
//
// Card shape (validated again in protocol.js): { id, name, tier, stages[5], blockedReason, sim }
//   tier: macro|operation|micro|emergency|""     stages: done|active|todo|blocked|ended|sim (Effect is `done` only for effect_observed)
// Newest command first; at most MAX_CARDS entries.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory(require("./stages.js"));
  else root.VoiBoardCards = factory(root.VoiStages);
})(typeof self !== "undefined" ? self : this, function (Stages) {
  const MAX_CARDS = 24;
  const MAX_ID = 64, MAX_NAME = 80, MAX_REASON = 200;

  // The rails kept for a command: live backend operations if listed, else the last known ones (marked stale), else the
  // command-level rail. `card.lastView` remembers the last operation list so a finished operation does not vanish.
  function viewFor(card, status) {
    const derived = Stages.deriveCommand(card, status);
    let rails;
    if (derived.kind === "operations") {
      rails = derived.rails.map(({ op, rail }) => ({ op, rail, stale: false }));
      card.lastView = rails.map(({ op, rail }) => ({ op, rail }));
    } else if (card.lastView) rails = card.lastView.map(item => ({ ...item, stale: true }));
    else rails = [{ op: null, rail: derived.rail, stale: false }];
    return { rails };
  }

  function opName(op, fallback) {
    if (!op) return String(fallback || "").slice(0, MAX_NAME);
    const vector = (op.update && op.update.vector) || {};
    return String(vector.goal || op.command_text || op.operation_id || fallback || "").slice(0, MAX_NAME);
  }
  const isOpen = rail => !rail.blocked && !rail.ended && !rail.completed && !rail.effectObserved;

  function cardsFor(commands, status) {
    const out = [];
    let open = 0;
    const views = [];
    for (const card of [...commands].reverse()) { // newest first
      const view = viewFor(card, status);
      views.push({ card, view });
      view.rails.forEach(({ op, rail, stale }, index) => {
        if (!stale && isOpen(rail)) open += 1;
        if (out.length >= MAX_CARDS) return;
        const tier = Stages.normalizeTier(rail.tier || card.tier) || "";
        const reason = rail.blocked ? String(rail.reason || card.error || "") : "";
        const opId = op && (op.operation_id || op.update_id) ? String(op.operation_id || op.update_id) : String(index);
        out.push({
          id: `c${card.key}-${opId}`.slice(0, MAX_ID),
          name: opName(op, card.text).trim(),
          tier,
          stages: rail.stages.map(stage => stage.state),
          blockedReason: reason.slice(0, MAX_REASON),
          sim: rail.runtime === "dry_run_fake_bot",
        });
      });
    }
    // ids must be unique even if the backend repeats an operation id across commands
    const seen = new Set();
    for (const item of out) { let id = item.id, n = 1; while (seen.has(id)) id = `${item.id.slice(0, MAX_ID - 4)}~${n++}`; seen.add(id); item.id = id; }
    return { cards: out, open, views };
  }

  // The one number the HUD shows: how many operations are OPEN right now (falls as they complete or block). When none is
  // open any more it keeps showing how many operations this session had, so the word never reads "0" next to history.
  const opsCount = (total, open) => open || total;

  return { MAX_CARDS, viewFor, opName, isOpen, cardsFor, opsCount };
});
