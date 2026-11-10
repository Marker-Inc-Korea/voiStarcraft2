"use strict";
// Pure view-model for the route display. Consumes the route events the backend emits (route_plan / route_progress / agent_cursor)
// and answers "what is on the minimap right now" for any `now`. It only ever shows what an event reported: reached markers come from
// route_progress, the final point is reached only on done:true, every coordinate is validated to lie on the minimap frame.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.VoiRouteModel = factory();
})(typeof self !== "undefined" ? self : this, function () {
  const SUBMITTED_ALPHA = 0.45;
  const ASK_LINGER_MS = 15000, NONE_LINGER_MS = 4000;
  const CURSOR_FADE_MS = 400;
  const ACTIONS = ["move", "attack"], CURSOR_ACTIONS = ["move", "attack", "select"];
  const inFrame = v => typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 1;
  const text = v => (typeof v === "string" ? v.trim() : "");

  function createRouteModel({ now = Date.now, fadeMs = 1500, holdMs = 4000, cursorIdleMs = 2500 } = {}) {
    let route = null, cursor = null, cursorSeq = 0, sketch = null, lastSeq = 0;

    function parsePlan(event) {
      const r = event.route;
      if (!r || typeof r !== "object" || !r.final || !ACTIONS.includes(r.final.action) || !inFrame(r.final.nx) || !inFrame(r.final.ny)) return null;
      if (!Array.isArray(r.waypoints) || !r.waypoints.every(w => w && inFrame(w.nx) && inFrame(w.ny))) return null;
      return {
        planId: String(event.plan_id || ""), narration: text(event.narration_ko), squad: event.squad || null,
        waypoints: r.waypoints.map((w, i) => ({ nx: w.nx, ny: w.ny, label: text(w.label_ko) || `경유 ${i + 1}` })),
        final: { action: r.final.action, nx: r.final.nx, ny: r.final.ny, label: text(r.final.label_ko) || "목표" },
        reachedIndex: -1, done: false, released: false, reason: "", endedAt: null,
      };
    }
    function ingest(event) {
      if (!event || typeof event !== "object") return;
      if (Number.isInteger(event.route_seq)) { if (event.route_seq <= lastSeq) return; lastSeq = event.route_seq; }
      if (event.kind === "route_plan") {
        const plan = parsePlan(event);
        if (!plan) return;
        route = plan; sketch = null; cursor = null;
      } else if (event.kind === "route_progress") {
        if (!route || String(event.plan_id || "") !== route.planId) return;
        if (Number.isFinite(event.reached_index)) route.reachedIndex = Math.max(route.reachedIndex, Math.min(route.waypoints.length - 1, Math.max(-1, Math.floor(event.reached_index))));
        if (event.done === true && !route.released && !route.done) { route.done = true; route.reachedIndex = route.waypoints.length - 1; route.endedAt = now(); }
        else if (event.released === true && !route.done && !route.released) { route.released = true; route.reason = text(event.reason); route.endedAt = now(); }
      } else if (event.kind === "agent_cursor") {
        if (!route || String(event.plan_id || "") !== route.planId || !inFrame(event.nx) || !inFrame(event.ny) || !CURSOR_ACTIONS.includes(event.action)) return;
        cursor = { nx: event.nx, ny: event.ny, action: event.action, label: text(event.label_ko), at: now(), seq: ++cursorSeq };
      }
    }
    function resetSeq() { lastSeq = 0; }

    function progressText(r) {
      const items = [...r.waypoints.map(w => ({ label: w.label })), { label: r.final.label }];
      const nextIndex = r.done || r.released ? -1 : r.reachedIndex + 1;
      const parts = items.map((item, i) => {
        const reached = r.done ? true : i <= r.reachedIndex && i < r.waypoints.length;
        return `${item.label}${reached ? " ✓" : i === nextIndex ? " (이동 중)" : ""}`;
      });
      parts.push(r.final.action === "attack" ? "공격" : "이동");
      if (r.released) parts.push("중단");
      return parts.join(" → ");
    }
    function routeView(at) {
      if (!route) return null;
      let alpha = 1;
      if (route.endedAt !== null) {
        const past = at - route.endedAt - holdMs;
        if (past >= fadeMs) return null;
        if (past > 0) alpha = 1 - past / fadeMs;
      }
      const active = !route.done && !route.released;
      const nextWp = active ? route.reachedIndex + 1 : -1;
      return {
        planId: route.planId, narration: route.narration, squad: route.squad, alpha, phase: route.done ? "done" : route.released ? "released" : "active", reason: route.reason,
        polyline: [...route.waypoints, route.final].map(p => ({ nx: p.nx, ny: p.ny })),
        waypoints: route.waypoints.map((w, i) => ({ number: i + 1, nx: w.nx, ny: w.ny, label: w.label, reached: route.done || i <= route.reachedIndex, next: nextWp === i })),
        final: { marker: route.final.action === "attack" ? "sword" : "flag", action: route.final.action, nx: route.final.nx, ny: route.final.ny, label: route.final.label, reached: route.done, next: active && nextWp === route.waypoints.length },
      };
    }
    function view(at = now()) {
      const r = routeView(at);
      let c = null;
      if (cursor) {
        const past = at - cursor.at - cursorIdleMs;
        if (past < CURSOR_FADE_MS) c = { nx: cursor.nx, ny: cursor.ny, action: cursor.action, label: cursor.label, alpha: past > 0 ? 1 - past / CURSOR_FADE_MS : 1, seq: cursor.seq };
      }
      let s = null;
      if (sketch && !route) {
        const linger = sketch.settled === "ask" ? ASK_LINGER_MS : sketch.settled ? NONE_LINGER_MS : Infinity;
        if (sketch.settledAt === null || at - sketch.settledAt <= linger) s = { planId: sketch.planId, strokes: sketch.strokes, alpha: SUBMITTED_ALPHA, settled: sketch.settled };
      }
      return { route: r, progressText: r ? progressText(route) : "", cursor: c, strokes: s };
    }
    return {
      ingest, resetSeq, view,
      setSketch(planId, strokes) { sketch = { planId, strokes, settled: "", settledAt: null }; },
      settleSketch(kind) { if (sketch) { sketch.settled = kind || "none"; sketch.settledAt = now(); } },
      clearSketch() { sketch = null; },
      clearRoute() { route = null; cursor = null; },
      isActive: () => Boolean(route && !route.done && !route.released),
    };
  }
  return { SUBMITTED_ALPHA, ASK_LINGER_MS, NONE_LINGER_MS, createRouteModel };
});
