"use strict";
// Pure render-model builder for the tactical board: paging view + settings + language -> the exact words/states the
// board renderer draws. Keeps the renderer a dumb DOM painter and keeps every truthfulness rule unit-testable.
//
// Truthfulness (round 1, unchanged): `효과`/Effect is shown ONLY when the backend reported effect_observed (stage state
// "done" and not simulated). A fake bot shows `시뮬`/Sim. Stage "sim" is never counted as done.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory(require("../renderer/i18n.js"));
  else root.VoiBoardView = factory(root.VoiI18n);
})(typeof self !== "undefined" ? self : this, function (I18n) {

const STAGE_IDS = Object.freeze(["heard", "understood", "assigned", "ordered", "effect"]);
const SKINS = Object.freeze(["cosmic", "terminal"]);
const BAR_CHARS = Object.freeze({ done: "#", sim: "~", active: ">", todo: "-", blocked: "x", ended: "." });

// The HUD owns the skin setting (settings.glass.skin). Unknown / missing -> cosmic.
function skinOf(settings) {
  const skin = settings && settings.glass && settings.glass.skin;
  return SKINS.includes(skin) ? skin : "cosmic";
}
const langOf = settings => (settings && settings.voice && settings.voice.language === "en" ? "en" : "ko");

function stageBar(stages) { return stages.map(state => BAR_CHARS[state] || "-").join(""); }

// One-word status of a card, strongest truth first.
function statusOf(card, t, reasonWord) {
  if (card.blocked) return { key: "blocked", word: reasonWord(card.blockedReason) };
  if (card.stages.includes("ended")) return { key: "ended", word: t("ended") };
  if (card.stages[4] === "done" && !card.sim) return { key: "effect", word: t("stage_effect") };
  if (card.sim || card.stages.includes("sim")) return { key: "sim", word: t("sim") };
  let last = -1;
  card.stages.forEach((state, index) => { if (state === "done") last = index; });
  if (last >= 0) return { key: STAGE_IDS[last], word: t(`stage_${STAGE_IDS[last]}`) };
  const active = card.stages.indexOf("active");
  return { key: active >= 0 ? STAGE_IDS[active] : "heard", word: t(`stage_${active >= 0 ? STAGE_IDS[active] : "heard"}`) };
}

function buildBoardView(view, settings) {
  const i18n = I18n.create(langOf(settings));
  const t = key => i18n.t(key);
  const board = (settings && settings.board) || {};
  const reasonWord = reason => i18n.reason(reason);
  const cards = view.cards.map(card => {
    const status = statusOf(card, t, reasonWord);
    return {
      id: card.id,
      name: card.name || "…",
      tier: card.tier || "",
      tierWord: card.tier ? t(`tier_${card.tier}`) : t("tier_unknown"),
      stages: card.stages.slice(),
      bar: stageBar(card.stages),
      status: status.key,
      statusWord: status.word,
      sim: Boolean(card.sim),
      simWord: card.sim ? t("sim") : "",
      blocked: Boolean(card.blocked),
      fresh: Boolean(card.fresh),
      finished: Boolean(card.finished),
      title: card.blockedReason ? String(card.blockedReason).slice(0, 200) : "",
    };
  });
  return {
    mode: view.mode,
    skin: skinOf(settings),
    opacity: typeof board.opacity === "number" ? board.opacity : 0.62,
    hue: settings && settings.glass && Number.isFinite(settings.glass.hue) ? settings.glass.hue : 250,
    reduceMotion: Boolean(settings && settings.glass && settings.glass.reduceMotion),
    cards,
    label: view.label,
    page: view.page,
    pageCount: view.pageCount,
    tabLabel: view.mode === "tab" ? `${t("tier_operation")} ${view.live}` : "",
    total: view.total,
    live: view.live,
    paused: view.paused,
  };
}

return { STAGE_IDS, SKINS, BAR_CHARS, skinOf, langOf, stageBar, statusOf, buildBoardView };
});
