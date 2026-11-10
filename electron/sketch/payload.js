"use strict";
// Pure builder/validator for POST /api/route/sketch and for interpreting its answer. No I/O.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.VoiSketchPayload = factory();
})(typeof self !== "undefined" ? self : this, function () {
  const MAX_TRANSCRIPT = 300;
  const MAX_IMAGE_B64 = 1500000;
  const MAX_STROKES = 8, MAX_POINTS = 400;
  const r4 = v => Math.round(v * 10000) / 10000;
  let counter = 0;
  const newUpdateId = () => `sk-${Date.now().toString(36)}-${(counter++ + Math.floor(Math.random() * 1e6)).toString(36)}${(counter).toString(36)}`;

  function checkStrokes(strokes) {
    if (!Array.isArray(strokes) || !strokes.length) throw new TypeError("sketch needs at least one stroke");
    if (strokes.length > MAX_STROKES) throw new TypeError(`sketch has too many strokes (max ${MAX_STROKES})`);
    return strokes.map((stroke, si) => {
      if (!Array.isArray(stroke) || stroke.length < 2) throw new TypeError(`stroke ${si} needs at least 2 points`);
      if (stroke.length > MAX_POINTS) throw new TypeError(`stroke ${si} has too many points`);
      let lastT = -1;
      return stroke.map((p, pi) => {
        if (!Array.isArray(p) || p.length !== 3 || !p.every(Number.isFinite)) throw new TypeError(`stroke ${si} point ${pi} is not [nx, ny, t_ms]`);
        const [x, y, t] = p;
        if (x < 0 || x > 1 || y < 0 || y > 1) throw new TypeError(`stroke ${si} point ${pi} is outside the minimap frame`);
        if (t < lastT) throw new TypeError(`stroke ${si} point ${pi} goes back in time`);
        lastT = t;
        return [r4(x), r4(y), Math.round(t)];
      });
    });
  }
  function checkImage(value) {
    if (value === null || value === undefined) return null;
    if (typeof value !== "string") throw new TypeError("image must be a base64 string or null");
    const b64 = value.replace(/^data:image\/png;base64,/, "");
    if (!b64) return null;
    if (b64.length > MAX_IMAGE_B64) throw new TypeError("image is too large");
    return b64;
  }
  function buildPayload({ updateId, strokes, imagePngB64, transcript, rect }) {
    if (typeof updateId !== "string" || !updateId) throw new TypeError("update_id required");
    if (!rect || !["x", "y", "w", "h"].every(k => Number.isFinite(rect[k]))) throw new TypeError("minimap rect required");
    return {
      update_id: updateId,
      strokes: checkStrokes(strokes),
      image_png_b64: checkImage(imagePngB64),
      transcript: String(transcript || "").trim().slice(0, MAX_TRANSCRIPT),
      minimap_rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.w), h: Math.round(rect.h) },
    };
  }

  // What the user said while / just before drawing: finals and partials of recent turns, one entry per turn (latest wording wins).
  function createTranscriptWindow({ windowMs = 10000 } = {}) {
    const turns = new Map();
    return {
      push(turn, text, at) { if (!text) return; const prior = turns.get(turn); turns.set(turn, { text: String(text), at, order: prior ? prior.order : turns.size }); },
      text(now) { return [...turns.values()].filter(t => t.at >= now - windowMs && t.at <= now).sort((a, b) => a.order - b.order).map(t => t.text.trim()).filter(Boolean).join(" "); },
      clear() { turns.clear(); },
    };
  }

  const inFrame = v => typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 1;
  function routeValid(route) {
    if (!route || typeof route !== "object" || !route.final || typeof route.final !== "object") return false;
    if (!["move", "attack"].includes(route.final.action) || !inFrame(route.final.nx) || !inFrame(route.final.ny)) return false;
    return Array.isArray(route.waypoints) && route.waypoints.every(w => w && inFrame(w.nx) && inFrame(w.ny));
  }
  const FALLBACK = {
    execute: "경로 계획을 받았습니다", ask: "어느 쪽으로 할지 다시 알려 주세요", refuse: "실행하지 않았습니다", none: "알아볼 수 있는 경로가 없어 아무것도 하지 않았습니다",
    invalid: "응답을 이해하지 못해 아무것도 하지 않았습니다", noRoute: "경로가 없는 응답이라 아무것도 하지 않았습니다",
  };
  // Never reports "execute" unless the answer carries a route whose every point lies on the frame.
  function interpretResponse(response) {
    if (!response || typeof response !== "object") return { kind: "invalid", text: FALLBACK.invalid, planId: "", route: null, squad: null };
    const planId = typeof response.plan_id === "string" ? response.plan_id : "";
    const narration = typeof response.narration_ko === "string" ? response.narration_ko.trim() : "";
    const question = typeof response.question_ko === "string" ? response.question_ko.trim() : "";
    const kind = response.kind;
    if (kind === "execute") {
      if (!routeValid(response.route)) return { kind: "invalid", text: FALLBACK.noRoute, planId, route: null, squad: null };
      return { kind, text: narration || FALLBACK.execute, planId, route: response.route, squad: response.squad || null };
    }
    if (kind === "ask") return { kind, text: question || narration || FALLBACK.ask, planId, route: null, squad: null };
    if (kind === "refuse" || kind === "none") return { kind, text: narration || FALLBACK[kind], planId, route: null, squad: null };
    return { kind: "invalid", text: FALLBACK.invalid, planId, route: null, squad: null };
  }

  return { MAX_TRANSCRIPT, MAX_IMAGE_B64, newUpdateId, checkStrokes, buildPayload, createTranscriptWindow, routeValid, interpretResponse, FALLBACK };
});
