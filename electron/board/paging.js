"use strict";
// Pure paging state machine of the tactical board. No Electron, no timers: the owner passes `now` (ms) into every call.
//
// Cards arrive newest-first (the HUD publishes the whole list each time). This module decides
//  - which cards are still shown (finished cards age out after `ageOutMs`),
//  - which page is visible, with auto-advance (paused while hovered / while a jump is showing / right after manual paging),
//  - the jump to the page that contains a new / changed / blocked / emergency card, and the return a few seconds later,
//  - the 1/N label and whether the board is a board, a tiny tab, or hidden.
// A page never holds more than `pageSize` cards, so the board can never grow with the number of operations.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.VoiBoardPaging = factory();
})(typeof self !== "undefined" ? self : this, function () {
  const DEFAULTS = Object.freeze({ pageSize: 3, autoAdvanceMs: 6000, ageOutMs: 45000, hideWhenEmpty: false, jumpMs: 4000, hoverPinMs: 1500, freshMs: 2500, manualHoldMs: 10000 });
  const MAX_CARDS = 24;

  const signatureOf = card => JSON.stringify([card.name, card.tier, card.stages, card.blockedReason, card.sim]);
  const isBlocked = card => card.stages.includes("blocked") || Boolean(card.blockedReason);
  // Nothing more will happen on this card: it is blocked/ended, its effect is observed, or (fake bot) the order is placed
  // and a simulated run can never produce an effect.
  function isTerminal(card) {
    if (isBlocked(card) || card.stages.includes("ended")) return true;
    const effect = card.stages[4];
    if (effect === "done" || effect === "sim") return true;
    return card.sim && (card.stages[3] === "done" || card.stages[3] === "sim");
  }

  function createPaging(options = {}) {
    let o = { ...DEFAULTS };
    let records = new Map();      // id -> { card, sig, changedAt, kind }
    let order = [];               // ids, newest first, as published
    let aged = new Map();         // id -> sig of a finished card that aged out (stays hidden until it changes again)
    let page = 0;
    let lastAdvance = 0;
    let hovered = false;
    let hoverPinUntil = 0;
    let jump = null;              // { returnPage, until }
    let manualUntil = 0;
    let started = false;

    function configure(next = {}) {
      o = { ...o, ...Object.fromEntries(Object.entries(next).filter(([, v]) => v !== undefined)) };
      o.pageSize = Math.max(1, Math.floor(o.pageSize));
      clampPage();
    }
    const visibleIds = () => order.filter(id => records.has(id));
    const pageCount = () => Math.max(1, Math.ceil(visibleIds().length / o.pageSize));
    function clampPage() { page = Math.min(Math.max(0, page), pageCount() - 1); }

    // Finished cards leave after ageOutMs without change (0 = never). Pure function of `now`.
    function prune(now) {
      if (!(o.ageOutMs > 0)) return;
      for (const [id, rec] of records) {
        if (isTerminal(rec.card) && now - rec.changedAt >= o.ageOutMs) { records.delete(id); aged.set(id, rec.sig); }
      }
      order = order.filter(id => records.has(id));
      clampPage();
    }

    // cards: normalised board cards (protocol.validateBoardCards output). Returns the jump taken ({page, reason}) or null.
    function ingest(cards, now) {
      const list = (Array.isArray(cards) ? cards : []).slice(0, MAX_CARDS);
      const wasEmpty = records.size === 0;
      const nextRecords = new Map();
      const nextOrder = [];
      const events = []; // { id, kind: new|changed|blocked|emergency }
      for (const card of list) {
        if (nextRecords.has(card.id)) continue;
        const sig = signatureOf(card);
        const prev = records.get(card.id);
        if (!prev && aged.get(card.id) === sig) continue; // finished long ago and unchanged: stay gone
        aged.delete(card.id);
        let rec = prev;
        if (!prev) {
          rec = { card, sig, changedAt: now };
          events.push({ id: card.id, kind: card.tier === "emergency" ? "emergency" : isBlocked(card) ? "blocked" : "new" });
        } else if (prev.sig !== sig) {
          const becameBlocked = isBlocked(card) && !isBlocked(prev.card);
          rec = { card, sig, changedAt: now };
          events.push({ id: card.id, kind: card.tier === "emergency" ? "emergency" : becameBlocked ? "blocked" : "changed" });
        } else rec = { ...prev, card };
        nextRecords.set(card.id, rec);
        nextOrder.push(card.id);
      }
      for (const id of [...aged.keys()]) if (!list.some(card => card.id === id)) aged.delete(id);
      records = nextRecords;
      order = nextOrder;
      prune(now);
      clampPage();
      if (!started) { started = true; lastAdvance = now; }
      // Pick the most important event: emergency > blocked > first changed/new in display order.
      const rank = { emergency: 0, blocked: 1, new: 2, changed: 3 };
      const live = events.filter(event => records.has(event.id));
      if (!live.length || wasEmpty) return null; // first cards on an empty board: page 1 is already showing
      live.sort((a, b) => rank[a.kind] - rank[b.kind] || order.indexOf(a.id) - order.indexOf(b.id));
      return jumpTo(Math.floor(order.indexOf(live[0].id) / o.pageSize), live[0].kind, now);
    }

    function jumpTo(target, reason, now) {
      if (pageCount() <= 1) { page = 0; return null; }
      jump = { returnPage: jump ? jump.returnPage : page, until: now + o.jumpMs };
      page = target;
      return { page, reason };
    }

    function setHover(next, now) {
      if (next) hoverPinUntil = now + o.hoverPinMs;
      else if (hovered) { hoverPinUntil = now + o.hoverPinMs; lastAdvance = now; }
      hovered = Boolean(next);
    }

    function go(delta, now) {
      const count = pageCount();
      if (count <= 1) return false;
      page = (page + delta + count) % count;
      jump = null; lastAdvance = now; manualUntil = now + o.manualHoldMs;
      return true;
    }
    const next = now => go(1, now);
    const prev = now => go(-1, now);

    // Timers: prune, end a jump (return), auto-advance. Returns true when the visible state changed.
    function tick(now) {
      const before = `${page}|${order.length}`;
      prune(now);
      if (jump && now >= jump.until) { page = Math.min(jump.returnPage, pageCount() - 1); jump = null; lastAdvance = now; }
      else if (!jump && !hovered && now >= hoverPinUntil && o.autoAdvanceMs > 0 && pageCount() > 1 && now >= manualUntil && now - lastAdvance >= o.autoAdvanceMs) {
        page = (page + 1) % pageCount(); lastAdvance = now;
      }
      return before !== `${page}|${order.length}`;
    }

    // What to draw. mode: "board" (cards), "tab" (tiny word tab), "hidden". The tab/hidden state is held open briefly by hover.
    function view(now) {
      prune(now);
      const ids = visibleIds();
      const count = Math.max(1, Math.ceil(ids.length / o.pageSize));
      const current = Math.min(page, count - 1);
      const pageCards = ids.slice(current * o.pageSize, (current + 1) * o.pageSize).map(id => {
        const rec = records.get(id);
        return { ...rec.card, fresh: now - rec.changedAt < o.freshMs, blocked: isBlocked(rec.card), finished: isTerminal(rec.card) };
      });
      const live = ids.filter(id => !isTerminal(records.get(id).card)).length;
      let mode = "board";
      if (!ids.length) mode = o.hideWhenEmpty ? "hidden" : "tab";
      return {
        mode, cards: pageCards, page: current, pageCount: count, total: ids.length, live,
        label: count > 1 ? `${current + 1}/${count}` : "",
        paused: hovered || now < hoverPinUntil, jumping: Boolean(jump),
      };
    }

    configure(options);
    return { configure, ingest, setHover, next, prev, tick, view, isHovered: () => hovered, reset: () => { records = new Map(); order = []; aged = new Map(); page = 0; jump = null; started = false; } };
  }

  return { DEFAULTS, MAX_CARDS, createPaging, isTerminal, isBlocked, signatureOf };
});
