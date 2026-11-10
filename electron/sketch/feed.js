"use strict";
// Polls GET /api/route/events?after=<route_seq> (route-only twin of the SSE stream; route_seq is its own cursor, independent of
// /api/history seq) and hands new route events to the caller exactly once, in order. Pure over an injected requestJson.
const KINDS = new Set(["route_plan", "route_progress", "agent_cursor"]);
const ACTIVE_MS = 400, IDLE_MS = 1500, MAX_DELAY_MS = 30000;

function createRouteFeed({ requestJson, onEvents = () => {}, onReset = () => {} }) {
  let cursor = 0, failed = 0;
  async function poll() {
    let page;
    try { page = await requestJson(`/api/route/events?after=${cursor}`); } catch { failed += 1; return false; }
    failed = 0;
    if (!page || typeof page !== "object") return true;
    const latest = Number.isFinite(page.latest) ? page.latest : null;
    if (latest !== null && latest < cursor) { cursor = 0; onReset(); return true; } // the backend restarted: its sequence started over
    const rows = Array.isArray(page.events) ? page.events : [];
    const fresh = [];
    let top = cursor;
    for (const row of rows) {
      if (!row || typeof row !== "object" || !Number.isInteger(row.route_seq) || row.route_seq <= cursor) continue;
      top = Math.max(top, row.route_seq);
      if (KINDS.has(row.kind)) fresh.push(row);
    }
    fresh.sort((a, b) => a.route_seq - b.route_seq);
    cursor = top;
    if (fresh.length) onEvents(fresh);
    return true;
  }
  const nextDelay = ({ active }) => Math.min(MAX_DELAY_MS, (active ? ACTIVE_MS : IDLE_MS) * 2 ** Math.min(failed, 6));
  return { poll, nextDelay, failures: () => failed, cursor: () => cursor };
}

module.exports = { createRouteFeed, KINDS };
