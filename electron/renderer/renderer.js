"use strict";
// Renderer: pure view over IPC. It never touches the network; every fact shown here comes from the
// backend (via window.commander) or is labelled as local (heard text, send failures).
(function () {
  const api = window.commander;
  const { deriveCommand, summarize, normalizeTier } = window.VoiStages;
  const i18n = window.VoiI18n.create(localStorage.getItem("voi.lang") || "ko");
  const t = (key, vars) => i18n.t(key, vars);
  const $ = id => document.getElementById(id);
  const body = document.body;
  body.dataset.platform = api.platform === "darwin" ? "darwin" : "other";
  const MAX_CARDS = 14;
  const POLL_ACTIVE_MS = 900, POLL_IDLE_MS = 3000, RUNTIME_MS = 5000;

  const state = {
    cards: [], nextKey: 1, status: null, backendOk: null, runtime: null, voice: { state: "idle", active: false, reason: "" },
    transcript: { text: "", partial: false }, events: [], cursor: 0, dock: { revealed: false, mode: "bottom", pinned: false, reason: "" }, lastStatusAt: 0,
  };
  const cardEls = new Map();

  // ---------- tiny DOM helper (no innerHTML) ----------
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("data-") || k.startsWith("aria-") || k === "title" || k === "role") el.setAttribute(k, v === true ? "" : String(v));
      else el[k] = v;
    }
    for (const child of children.flat()) if (child !== undefined && child !== null && child !== false) el.append(child.nodeType ? child : document.createTextNode(String(child)));
    return el;
  }

  // ---------- i18n ----------
  function applyI18n() {
    document.documentElement.lang = i18n.lang;
    body.dataset.lang = i18n.lang;
    for (const el of document.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);
    for (const el of document.querySelectorAll("[data-i18n-title]")) el.title = t(el.dataset.i18nTitle);
    for (const el of document.querySelectorAll("[data-i18n-placeholder]")) el.placeholder = t(el.dataset.i18nPlaceholder);
    for (const el of document.querySelectorAll("[data-i18n-aria]")) el.setAttribute("aria-label", t(el.dataset.i18nAria));
    $("btn-lang").textContent = i18n.lang === "ko" ? "KO" : "EN";
    renderVoice(); renderChips(); renderAll();
  }

  // ---------- cards ----------
  function addCard(card) {
    const entry = { key: state.nextKey++, at: Date.now(), text: "", source: "text", updateId: "", tier: "", error: "", latency: null, listening: false, lastView: null, ...card };
    state.cards.push(entry);
    while (state.cards.length > MAX_CARDS) { const old = state.cards.shift(); const el = cardEls.get(old.key); if (el) el.remove(); cardEls.delete(old.key); }
    renderCards();
    return entry;
  }
  const voiceCards = new Map(); // realtime turn id -> card

  function viewFor(card) {
    const derived = deriveCommand(card, state.status);
    let rails;
    if (derived.kind === "operations") {
      rails = derived.rails.map(({ op, rail }) => ({ op, rail, stale: false }));
      card.lastView = rails.map(({ op, rail }) => ({ op, rail }));
    } else if (card.lastView) {
      rails = card.lastView.map(item => ({ ...item, stale: true }));
    } else rails = [{ op: null, rail: derived.rail, stale: false }];
    return { rails, command: derived.kind === "command" ? derived.rail : null };
  }

  function tierBadge(tier) {
    const key = normalizeTier(tier);
    return h("span", { class: "tier", "data-tier": key || "unknown", text: key ? t(`tier_${key}`) : t("tier_unknown") });
  }
  const STATE_ICON = { done: "✓", blocked: "!", ended: "–", active: "", todo: "", sim: "◇" };
  function stageRail(rail) {
    const items = rail.stages.map(stage => h("li", { class: "stage", "data-state": stage.state, "data-stage": stage.id, title: stage.detail || t(`stage_${stage.id}`) },
      h("span", { class: "node", text: STATE_ICON[stage.state] || "" }),
      h("span", { class: "label", text: t(`stage_${stage.id}`) })));
    return h("ol", { class: "rail", "data-effect": rail.effectObserved ? "yes" : "no" }, items);
  }
  const opGoal = op => {
    if (!op) return "";
    const update = (op && op.update) || {};
    const vector = update.vector || {};
    return String(vector.goal || op.command_text || "").slice(0, 120);
  };
  function composition(op) {
    const vector = ((op && op.update) || {}).vector || {};
    const reqs = Array.isArray(vector.composition_requirements) ? vector.composition_requirements : [];
    return reqs.slice(0, 4).map(r => `${r.unit_type || ""}${Number.isInteger(r.count) ? ` ×${r.count}` : ""}`.trim()).filter(Boolean);
  }
  function routeTarget(op) {
    const vector = ((op && op.update) || {}).vector || {};
    const route = vector.route_intent && vector.route_intent.route_type, target = vector.target_intent && vector.target_intent.target_type;
    return [route, target].filter(Boolean).join(" → ");
  }
  // Non-macro tiers run on the direct runtime: say truthfully where (fake bot / live / unknown).
  function runtimeBadge(rail) {
    if (!rail || !rail.runtime || rail.runtime === "micromachine" || rail.runtime === "live") return null;
    return h("span", { class: "runtime-badge", "data-runtime": rail.runtime, title: t(rail.runtime === "unknown" ? "unknownNote" : "simNote"), text: t(`runtime_${rail.runtime}`) });
  }
  function railBlock(item, parallel, card) {
    const { op, rail, stale } = item;
    const tier = (op && rail.tier) || rail.tier || card.tier;
    const state = rail.blocked ? "blocked" : rail.ended ? "ended" : rail.completed ? "completed" : "live";
    const meta = [];
    if (op) {
      for (const part of composition(op)) meta.push(h("span", { class: "meta-chip", text: part }));
      const rt = routeTarget(op); if (rt) meta.push(h("span", { class: "meta-chip", text: rt }));
    }
    return h("div", { class: "op", "data-state": state, "data-stale": stale ? "yes" : "no" },
      parallel ? h("div", { class: "op-head" }, tierBadge(tier), h("span", { class: "op-goal", text: opGoal(op) || t("stage_understood") }), meta) : null,
      runtimeBadge(rail) && (rail.tier || card.tier) ? runtimeBadge(rail) : null,
      stageRail(rail),
      rail.blocked || rail.ended ? h("p", { class: "reason", "data-kind": rail.blocked ? "blocked" : "ended" }, h("b", { text: rail.blocked ? t("blockedBecause") : t("stageEnded") }), ` ${rail.reason || t("reasonUnknown")}`) : null,
      !rail.blocked && !rail.ended && rail.consumption && rail.consumption !== "consumed" && !rail.effectObserved ? h("p", { class: "hint", text: `${t("consumption")}: ${t(rail.consumption)}` }) : null,
      stale ? h("p", { class: "hint", text: "· last backend report" }) : null);
  }

  function renderCard(card) {
    const view = viewFor(card);
    notifyAttention(card, view);
    const first = view.rails[0];
    const parallel = view.rails.length > 1;
    const tier = card.tier || (first && first.rail.tier) || "";
    const signature = JSON.stringify([card.directRuntime, card.directExecuted, card.text, card.listening, card.source, tier, card.error, card.latency, i18n.lang, view.rails.map(r => [r.rail, r.stale, r.op && r.op.operation_id, opGoal(r.op)])]);
    let el = cardEls.get(card.key);
    if (el && el.dataset.sig === signature) return el;
    const created = !el;
    if (!el) { el = h("article", { class: "card", "data-key": card.key }); cardEls.set(card.key, el); }
    el.dataset.sig = signature;
    const overall = view.rails.some(r => r.rail.blocked) ? "blocked" : view.rails.every(r => r.rail.effectObserved) ? "effect" : view.rails.some(r => r.rail.effectObserved) ? "partial" : "live";
    el.dataset.overall = overall;
    el.dataset.parallel = parallel ? "yes" : "no";
    const lat = card.latency;
    const showCardError = card.error && !view.rails.some(item => item.rail.blocked && item.rail.reason === card.error);
    el.replaceChildren(...[
      h("header", { class: "card-head" },
        h("span", { class: "src", "data-source": card.source, title: card.source === "voice" ? t("voiceSource") : t("textSource"), text: card.source === "voice" ? "🎙" : "⌨" }),
        h("p", { class: "utterance", text: card.text || (card.listening ? t("transcriptListening") : "…") }),
        parallel ? h("span", { class: "par", text: t("parallelN", { n: view.rails.length }) }) : null,
        tierBadge(tier),
        h("time", { text: new Date(card.at).toLocaleTimeString(i18n.lang === "ko" ? "ko-KR" : "en-US", { hour12: false }) })),
      h("div", { class: "ops-grid", "data-count": view.rails.length }, view.rails.map(item => railBlock(item, parallel, card))),
      showCardError ? h("p", { class: "reason", "data-kind": "blocked" }, h("b", { text: t("blockedBecause") }), ` ${card.error}`) : null,
      lat && (lat.speech_end_to_tool_call != null || lat.tool_call_to_publish != null)
        ? h("footer", { class: "latency" }, h("span", { text: t("latency") }),
          lat.speech_end_to_tool_call != null ? h("span", { class: "lat", text: `${t("latSpeech")} ${Math.round(lat.speech_end_to_tool_call)}${t("ms")}` }) : null,
          lat.tool_call_to_publish != null ? h("span", { class: "lat", text: `${t("latPublish")} ${Math.round(lat.tool_call_to_publish)}${t("ms")}` }) : null) : null,
    ].filter(Boolean));
    if (created) el.classList.add("enter");
    return el;
  }
  function renderCards() {
    const host = $("cards");
    const ordered = [...state.cards].reverse(); // newest first
    ordered.forEach((card, index) => {
      const el = renderCard(card);
      if (host.children[index] !== el) host.insertBefore(el, host.children[index] || null);
    });
    $("deck-empty").hidden = state.cards.length > 0;
  }

  // ---------- sidebar ----------
  function renderSidebar() {
    const ops = state.status && Array.isArray(state.status.operations) ? state.status.operations : [];
    $("ops-count").textContent = String(ops.length);
    $("ops-empty").hidden = ops.length > 0;
    const host = $("ops");
    host.replaceChildren(...ops.slice(0, 40).map(op => {
      const rail = window.VoiStages.deriveOperationRail(op, { directRuntime: state.status && state.status.direct_runtime });
      const mission = String(op.mission || "operation");
      const parts = composition(op);
      const disp = String(op.disposition || "pending");
      return h("li", { class: "op-item", "data-state": rail.blocked ? "blocked" : rail.ended ? "ended" : rail.completed ? "completed" : "live" },
        h("div", { class: "op-top" }, tierBadge(rail.tier), h("span", { class: "op-mission", text: t(`mission_${mission}`) === `mission_${mission}` ? mission : t(`mission_${mission}`) }), h("span", { class: "disp", "data-disp": disp, text: t(`disp_${disp}`) === `disp_${disp}` ? disp : t(`disp_${disp}`) })),
        h("p", { class: "op-name", text: opGoal(op) || String(op.operation_id || "") }),
        parts.length || routeTarget(op) ? h("div", { class: "squad" }, parts.map(p => h("span", { class: "meta-chip", text: p })), routeTarget(op) ? h("span", { class: "meta-chip", text: routeTarget(op) }) : null) : null,
        h("div", { class: "mini-rail", "aria-label": summarize(rail) }, rail.stages.map(stage => h("i", { "data-state": stage.state, title: t(`stage_${stage.id}`) }))),
        rail.blocked || rail.ended ? h("p", { class: "reason small", text: rail.reason || t("reasonUnknown") }) : null);
    }));
    $("events-empty").hidden = state.events.length > 0;
    $("events").replaceChildren(...state.events.slice(-30).reverse().map(event => h("li", { "data-status": event.status || "" },
      h("span", { class: "ev-status", text: event.status || "event" }),
      h("span", { class: "ev-text", text: String(event.narration || event.command_text || "").slice(0, 160) }))));
  }

  // ---------- chips / voice ----------
  function renderChips() {
    const backend = $("chip-backend");
    const tone = state.backendOk === null ? "warn" : state.backendOk ? "ok" : "bad";
    backend.dataset.tone = tone;
    backend.lastElementChild.textContent = state.backendOk === null ? t("chipConnecting") : state.backendOk ? t("chipBackendOk") : t("chipBackendDown");
    const runtime = $("chip-runtime");
    const info = state.runtime;
    let label = t("runtimeUnknown"), rtone = "muted";
    if (info) {
      if (info.enabled === false) { label = t("runtimeDisabled"); rtone = "muted"; }
      else { const status = String(info.status || info.state || "").slice(0, 24); label = `${t("runtimeLabel")} ${status || "—"}`; rtone = /run|ready|active|connected/i.test(status) ? "ok" : /fail|error|exit/i.test(status) ? "bad" : "warn"; }
    }
    runtime.dataset.tone = rtone; runtime.lastElementChild.textContent = label;
  }
  const VOICE_LABEL = { idle: "voiceReady", connecting: "voiceConnecting", listening: "voiceListening", thinking: "voiceThinking", acting: "voiceActing", error: "voiceError" };
  function renderVoice() {
    const v = state.voice;
    const shown = v.active ? v.state : "off";
    body.dataset.voice = shown;
    const orb = $("orb");
    orb.setAttribute("aria-pressed", v.active ? "true" : "false");
    orb.title = v.active ? t("orbOn") : t("orbOff");
    $("voice-badge").textContent = !v.active ? t("voiceIdle") : t(VOICE_LABEL[v.state] || "voiceIdle");
    $("voice-badge").dataset.reason = v.reason || "";
    const line = $("transcript");
    let text = state.transcript.text, placeholder = false;
    if (!v.active) { text = text || t("transcriptPlaceholder"); placeholder = !state.transcript.text; }
    else if (v.state === "error") { text = v.reason || t("voiceError"); placeholder = false; }
    else if (!text) { text = v.state === "listening" ? t("transcriptListening") : t("transcriptReady"); placeholder = true; }
    else if (v.reason && v.state === "idle") text = `${text}  ·  ${v.reason}`;
    line.textContent = text;
    line.dataset.placeholder = placeholder ? "true" : "false";
    line.dataset.partial = state.transcript.partial ? "true" : "false";
    line.dataset.error = v.state === "error" && v.active ? "true" : "false";
  }

  function renderAll() { renderCards(); renderSidebar(); }

  // ---------- backend polling ----------
  function hasOpenCommands() {
    return state.cards.some(card => {
      if (card.error) return false;
      const view = viewFor(card);
      return view.rails.some(item => !item.rail.blocked && !item.rail.ended && !item.rail.completed && !item.rail.effectObserved);
    });
  }
  async function pollStatus() {
    try {
      state.status = await api.status();
      state.backendOk = true; state.lastStatusAt = Date.now();
    } catch { state.backendOk = false; }
    renderChips(); renderAll();
  }
  async function pollRuntime() {
    try { state.runtime = await api.runtimeStatus(); state.backendOk = true; } catch { state.runtime = null; state.backendOk = false; }
    renderChips();
  }
  async function pollEvents() {
    try {
      const payload = await api.events(state.cursor);
      const events = Array.isArray(payload.events) ? payload.events : [];
      if (Number.isInteger(payload.latest)) state.cursor = Math.max(state.cursor, payload.latest);
      if (events.length) { state.events.push(...events); state.events = state.events.slice(-60); renderSidebar(); }
    } catch { /* backend chip is driven by status polling */ }
  }
  let statusTimer = 0;
  function schedule() {
    clearTimeout(statusTimer);
    statusTimer = setTimeout(async () => { if (!document.hidden) { await Promise.all([pollStatus(), state.dock.revealed && state.dock.mode === "right" ? pollEvents() : null]); } schedule(); }, hasOpenCommands() || state.voice.state === "acting" ? POLL_ACTIVE_MS : POLL_IDLE_MS);
  }
  function pokeSoon() { clearTimeout(statusTimer); statusTimer = setTimeout(async () => { await pollStatus(); schedule(); }, 250); }

  // ---------- commands ----------
  // Fold a modulate/intent response into a card without inventing success.
  function applyResponse(card, response) {
    response = response || {};
    card.updateId = String(response.update_id || card.updateId || "");
    if (response.direct_runtime && typeof response.direct_runtime === "object") card.directRuntime = response.direct_runtime;
    if (response.tier) card.tier = normalizeTier(response.tier);
    if (response.status === "direct_executed") card.directExecuted = true;
    const refused = response.accepted === false || response.ok === false || response.status === "refused";
    if (card.tier === "emergency") api.dock.attention("emergency");
    if (refused) card.error = String(response.error || response.refusal_reason || (response.compile_result && response.compile_result.refusal_reason) || response.status || t("sendFailed"));
  }
  async function submitText(text, extra) {
    const card = addCard({ text, source: "text", ...extra });
    if (extra && extra.tier === "emergency") api.dock.attention("emergency");
    try {
      const response = await api.sendCommand(text, i18n.lang);
      applyResponse(card, response);
    } catch (error) { api.dock.attention("error"); card.error = `${t("sendFailed")}: ${String((error && error.message) || error).replace(/^Error invoking remote method '[^']+': (Error: )?/, "")}`; }
    renderCards(); pokeSoon();
  }

  function onPush(event) {
    if (!event || typeof event !== "object") return;
    if (event.type === "voice-state") {
      const prev = state.voice;
      state.voice = { state: event.state, active: Boolean(event.active), reason: event.reason || "" };
      if (state.voice.active && !window.VoiMic.running && !micStarting) startMic();
      if (!state.voice.active && window.VoiMic.running) window.VoiMic.stop();
      if (prev.state !== "acting" && state.voice.state === "acting") pokeSoon();
      renderVoice(); schedule();
    } else if (event.type === "dock-state") applyDock(event);
    else if (event.type === "transcript") {
      let card = voiceCards.get(event.turn);
      if (!card) { card = addCard({ source: "voice", listening: true }); voiceCards.set(event.turn, card); state.transcript = { text: "", partial: true }; }
      if (event.text) { card.text = event.text; state.transcript = { text: event.text, partial: Boolean(event.partial) }; }
      if (event.final) card.listening = false;
      renderVoice(); renderCards();
    } else if (event.type === "intent") {
      let card = voiceCards.get(event.turn);
      if (!card) card = addCard({ source: "voice" });
      voiceCards.set(event.turn, card);
      card.listening = false;
      if (event.transcript && !card.text) { card.text = event.transcript; state.transcript = { text: event.transcript, partial: false }; }
      if (!card.text) card.text = "(음성)";
      if (event.latency_ms) card.latency = event.latency_ms;
      if (event.tier) card.tier = normalizeTier(event.tier);
      if (event.ok && event.response) applyResponse(card, event.response);
      else { api.dock.attention("error"); card.error = event.kind === "no_command" ? t("noCommand") : String(event.error || t("sendFailed")); }
      renderVoice(); renderCards(); pokeSoon();
    }
  }

  let micStarting = false;
  async function startMic() {
    micStarting = true;
    try { await window.VoiMic.start(chunk => api.voice.sendAudio(chunk), level => api.voice.sendLevel(level)); }
    catch (error) {
      const reason = error && error.name === "NotAllowedError" ? t("micDenied") : String((error && error.message) || error);
      api.voice.micError(reason);
    } finally { micStarting = false; }
  }
  async function toggleVoice() {
    try {
      if (state.voice.active) { localStorage.setItem("voi.armed", "0"); await api.voice.stop(); }
      else { localStorage.setItem("voi.armed", "1"); await api.voice.start(); }
    } catch (error) { state.voice = { state: "error", active: true, reason: String((error && error.message) || error) }; renderVoice(); }
  }

  // ---------- dock (edge-docked auto-hide bar; main process decides reveal/hide from the cursor) ----------
  function applyDock(next) {
    if (!next) return;
    const wasRevealed = state.dock.revealed;
    state.dock = { revealed: Boolean(next.revealed), mode: next.mode === "right" ? "right" : "bottom", pinned: Boolean(next.pinned), reason: next.reason || "" };
    body.dataset.dock = state.dock.mode;
    body.dataset.revealed = String(state.dock.revealed);
    $("btn-pin").setAttribute("aria-pressed", String(state.dock.pinned));
    if (next.glass) applyGlass(next.glass, Boolean(next.vibrancySupported));
    if (state.dock.revealed && !wasRevealed) { pollEvents(); pollStatus(); }
    if (!state.dock.revealed && wasRevealed && document.activeElement && document.activeElement.blur) document.activeElement.blur();
    reportRect();
  }
  // Glass look: live CSS variables, no reload. Text-shadow/scrim strengthen as the fill gets more transparent.
  function applyGlass(glass, vibrancySupported) {
    const alpha = Math.min(0.9, Math.max(0.08, Number(glass.alpha)));
    const root = document.documentElement.style;
    root.setProperty("--glass-alpha", String(alpha));
    root.setProperty("--glass-blur", `${Math.round(glass.blur)}px`);
    root.setProperty("--legible", String(Math.min(0.95, 0.45 + (1 - alpha) * 0.6).toFixed(2)));
    root.setProperty("--scrim", String((0.22 + (1 - alpha) * 0.3).toFixed(2)));
    if (document.activeElement !== $("rng-alpha")) $("rng-alpha").value = String(Math.round(alpha * 100));
    if (document.activeElement !== $("rng-blur")) $("rng-blur").value = String(Math.round(glass.blur));
    $("out-alpha").textContent = `${Math.round(alpha * 100)}%`;
    $("out-blur").textContent = `${Math.round(glass.blur)}px`;
    const button = $("btn-vibrancy");
    button.hidden = !vibrancySupported;
    button.setAttribute("aria-pressed", String(Boolean(glass.vibrancy)));
  }
  let glassTimer = 0;
  function sendGlass(patch) { clearTimeout(glassTimer); glassTimer = setTimeout(() => api.dock.setGlass(patch).then(applyDock).catch(() => {}), 40); }
  let lastRect = "";
  function reportRect() {
    const panel = $("panel");
    // offset* ignores the CSS slide transform, so main always gets the revealed layout rectangle.
    const rect = { x: panel.offsetLeft, y: panel.offsetTop, width: panel.offsetWidth, height: panel.offsetHeight };
    const key = JSON.stringify(rect);
    if (key !== lastRect && rect.width > 0 && rect.height > 0) { lastRect = key; api.dock.reportRect(rect); }
  }
  new ResizeObserver(reportRect).observe($("panel"));
  const attentionSeen = new Map(); // card key -> summary
  function notifyAttention(card, view) {
    const summary = view.rails.map(item => item.rail.stages.map(stage => stage.state[0]).join("")).join("|");
    const prev = attentionSeen.get(card.key);
    attentionSeen.set(card.key, summary);
    if (prev === undefined || prev === summary) return;
    const blocked = view.rails.some(item => item.rail.blocked);
    const emergency = view.rails.some(item => item.rail.tier === "emergency") || card.tier === "emergency";
    api.dock.attention(blocked ? "blocked" : emergency ? "emergency" : "stage");
  }

  // ---------- wiring ----------
  $("cmd-form").addEventListener("submit", event => {
    event.preventDefault();
    const input = $("cmd-input"); const text = input.value.trim();
    if (!text) return;
    input.value = ""; submitText(text);
  });
  $("btn-retreat").addEventListener("click", () => submitText(i18n.lang === "en" ? "emergency retreat now" : "긴급 후퇴", { tier: "emergency" }));
  $("orb").addEventListener("click", toggleVoice);
  $("rng-alpha").addEventListener("input", event => { const alpha = Number(event.target.value) / 100; applyGlass({ ...(state.glass || { blur: Number($("rng-blur").value) }), alpha, blur: Number($("rng-blur").value), vibrancy: $("btn-vibrancy").getAttribute("aria-pressed") === "true" }, !$("btn-vibrancy").hidden); sendGlass({ alpha }); });
  $("rng-blur").addEventListener("input", event => { const blur = Number(event.target.value); applyGlass({ alpha: Number($("rng-alpha").value) / 100, blur, vibrancy: $("btn-vibrancy").getAttribute("aria-pressed") === "true" }, !$("btn-vibrancy").hidden); sendGlass({ blur }); });
  $("btn-vibrancy").addEventListener("click", () => api.dock.setGlass({ vibrancy: $("btn-vibrancy").getAttribute("aria-pressed") !== "true" }).then(applyDock).catch(() => {}));
  $("btn-pin").addEventListener("click", () => api.dock.setPinned(!state.dock.pinned).then(applyDock).catch(() => {}));
  $("btn-dock").addEventListener("click", () => api.dock.setMode(state.dock.mode === "bottom" ? "right" : "bottom").then(applyDock).catch(() => {}));
  // Typing needs key focus; hovering never takes it. Focus is granted on an explicit click only.
  $("cmd-input").addEventListener("pointerdown", () => { api.dock.focus(true).then(() => $("cmd-input").focus()).catch(() => {}); });
  $("cmd-input").addEventListener("blur", () => { setTimeout(() => { if (!document.hasFocus() || document.activeElement === document.body) api.dock.focus(false).catch(() => {}); }, 120); });
  $("btn-lang").addEventListener("click", () => { i18n.set(i18n.lang === "ko" ? "en" : "ko"); localStorage.setItem("voi.lang", i18n.lang); applyI18n(); });
  window.addEventListener("keydown", event => {
    if (event.key === "Escape") { if (document.activeElement && document.activeElement.blur) document.activeElement.blur(); api.dock.focus(false).catch(() => {}); }
    if ((event.metaKey || event.ctrlKey) && event.key === "." ) $("btn-retreat").click();
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) pokeSoon(); });

  api.onEvent(onPush);
  applyI18n();
  setInterval(pollRuntime, RUNTIME_MS);
  pollRuntime(); pollStatus().then(schedule);
  api.dock.state().then(applyDock).catch(() => {});
  api.voice.state().then(snapshot => { state.voice = { state: snapshot.state, active: snapshot.active, reason: snapshot.reason || "" }; renderVoice(); if (!snapshot.active && localStorage.getItem("voi.armed") === "1") toggleVoice(); }).catch(() => {});
  // Re-render relative stage text every few seconds without extra IPC.
  setInterval(renderCards, 4000);
  reportRect();
})();
