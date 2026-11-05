"use strict";
// Paints the view model built in main (board/view.js). Words only; both skins are CSS-variable driven so they switch live.
(function () {
  const $ = id => document.getElementById(id);
  const body = document.body;
  const host = $("cards");
  const els = new Map(); // card id -> element
  function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (value === undefined || value === null) continue;
      if (key === "text") node.textContent = value; else if (key === "class") node.className = value; else node.setAttribute(key, value);
    }
    for (const child of children) if (child) node.append(child);
    return node;
  }
  function rail(card) {
    if (body.dataset.skin === "terminal") return el("span", { class: "bar", "data-effect": card.status === "effect" ? "yes" : "no", text: `[${card.bar}]` });
    return el("ol", { class: "rail", "data-effect": card.status === "effect" ? "yes" : "no" }, ...card.stages.map((state, index) => el("li", { class: "stage", "data-state": state, "data-stage": String(index) }, el("span", { class: "node" }))));
  }
  function cardEl(card) {
    const prefix = body.dataset.skin === "terminal" ? el("span", { class: "prompt", text: ">" }) : null;
    return el("article", { class: "card", "data-id": card.id, "data-tier": card.tier || "unknown", "data-status": card.status, "data-fresh": card.fresh ? "yes" : "no", "data-finished": card.finished ? "yes" : "no", title: card.title },
      el("div", { class: "l1" }, prefix, el("span", { class: "name", text: card.name }), el("span", { class: "tier", text: card.tierWord }), card.sim ? el("span", { class: "badge", "data-kind": "sim", text: card.simWord }) : null),
      el("div", { class: "l2" }, rail(card), el("span", { class: "status", "data-kind": card.status, text: card.statusWord })));
  }
  function render(model) {
    if (!model) return;
    body.dataset.skin = model.skin === "terminal" ? "terminal" : "cosmic";
    body.dataset.mode = model.mode;
    body.dataset.motion = model.reduceMotion ? "off" : "on";
    body.dataset.paged = model.pageCount > 1 ? "yes" : "no";
    const root = document.documentElement.style;
    root.setProperty("--board-alpha", String(model.opacity));
    root.setProperty("--hue", String(model.hue));
    $("label").textContent = model.label;
    $("tab").textContent = model.tabLabel;
    const sig = JSON.stringify([model.skin, model.cards.map(c => [c.id, c.name, c.bar, c.status, c.statusWord, c.tierWord, c.fresh, c.finished, c.sim])]);
    if (host.dataset.sig === sig) return;
    host.dataset.sig = sig;
    els.clear();
    host.replaceChildren(...model.cards.map(card => { const node = cardEl(card); els.set(card.id, node); return node; }));
  }
  // `board:cards` is the raw published list (main also derives the paged `board:view` from it); kept only as a count for diagnostics.
  window.boardApi.onCards(cards => { body.dataset.total = String(Array.isArray(cards) ? cards.length : 0); });
  window.boardApi.onView(render);
  document.addEventListener("click", () => window.boardApi.page(1));
  document.addEventListener("contextmenu", event => { event.preventDefault(); window.boardApi.page(-1); });
  let wheelAt = 0;
  document.addEventListener("wheel", event => {
    const now = performance.now();
    if (Math.abs(event.deltaY) < 2 || now - wheelAt < 300) return;
    wheelAt = now; window.boardApi.page(event.deltaY > 0 ? 1 : -1);
  }, { passive: true });
})();
