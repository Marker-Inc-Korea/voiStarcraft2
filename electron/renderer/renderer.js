"use strict";
// Renderer: pure view over IPC. It never touches the network; every fact shown here comes from the
// backend (via window.commander) or is labelled as local (heard text, send failures).
// The in-game surface is words only: orb, one-line transcript, input, 후퇴/홀드/멈춰, gear. Everything else lives in settings.
// Operation cards are NOT drawn here: the HUD derives them (board_cards.js) and publishes them to the separate tactical board window.
(function () {
  const api = window.commander;
  const { normalizeTier } = window.VoiStages;
  const BoardCards = window.VoiBoardCards, HudShape = window.VoiHudShape, Skins = window.VoiSkins;
  const S = window.VoiSettings;
  const i18n = window.VoiI18n.create("ko");
  const t = (key, vars) => i18n.t(key, vars);
  const $ = id => document.getElementById(id);
  const body = document.body;
  const root = document.documentElement.style;
  const MAX_COMMANDS = 12;
  const POLL_ACTIVE_MS = 900, POLL_IDLE_MS = 3000;

  const state = {
    cards: [], nextKey: 1, status: null, backendOk: null, runtime: null, voice: { state: "idle", active: false, reason: "" },
    transcript: { text: "", partial: false }, dock: { revealed: false, dock: "bottom", settingsOpen: false },
    settings: S.defaults(), env: { shortcut: {}, displays: [], model: "", platform: "", vibrancySupported: false, baseUrl: "" },
    tab: "glass", latencies: [], level: 0, devices: [], server: { state: "checking" }, recording: false, resetArmed: false,
  };
  const EMERGENCY = ["retreat", "hold", "stop"];
  const EMERGENCY_TEXT = { ko: { retreat: "긴급 후퇴", hold: "긴급 홀드", stop: "긴급 멈춤" }, en: { retreat: "emergency retreat now", hold: "emergency hold position", stop: "emergency stop" } };
  body.dataset.platform = api.platform === "darwin" ? "darwin" : "other";

  // ---------- tiny DOM helper (no innerHTML) ----------
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("data-") || k.startsWith("aria-") || k === "title" || k === "role" || k === "type" || k === "min" || k === "max" || k === "step") el.setAttribute(k, v === true ? "" : String(v));
      else el[k] = v;
    }
    for (const child of children.flat()) if (child !== undefined && child !== null && child !== false) el.append(child.nodeType ? child : document.createTextNode(String(child)));
    return el;
  }

  // ---------- applying settings: CSS variables only, so everything is live with no reload ----------
  function applySettingsToCss() {
    const g = state.settings.glass, l = state.settings.layout;
    body.dataset.skin = Skins.normalize(g.skin);
    for (const [name, value] of Object.entries(Skins.cssVars(g.skin, g))) root.setProperty(name, value);
    root.setProperty("--gap", `${l.margin}px`);
    body.dataset.dock = l.dock;
    body.dataset.motion = g.reduceMotion ? "off" : "on";
    window.VoiSpace.configure({ enabled: Skins.normalize(g.skin) === "cosmic", stars: g.stars / 100, speed: g.starSpeed / 100, nebula: g.nebula / 100, hue: g.hue, saturation: g.saturation / 100, twinkle: g.twinkle, meteors: g.meteors, still: g.reduceMotion, glassAlpha: g.alpha });
    applyShape();
    reportRect();
  }

  // ---------- shape: one pure rounded path (hud_shape.js) drives the clip, the outline/glow and main's hit-test ----------
  // bottom dock = slanted bar with all four corners rounded; every other dock = plain rounded rectangle.
  let lastShapeKey = "";
  function applyShape() {
    const hud = $("hud");
    const width = hud.offsetWidth, height = hud.offsetHeight;
    if (width < 2 || height < 2) return;
    const g = state.settings.glass, dock = state.settings.layout.dock;
    const smooth = Skins.smoothFor(g.skin);
    const bar = $("bar");
    const barDepth = (dock === "bottom" || dock === "top") ? bar.offsetHeight + 26 : 0;
    const key = [dock, width, height, g.slant, g.radius, smooth, barDepth].join("|");
    if (key === lastShapeKey) return;
    lastShapeKey = key;
    const shape = HudShape.shape(dock, width, height, { slantDeg: g.slant, radius: g.radius, smooth });
    $("panel").style.clipPath = shape.clipPath;
    const outline = $("outline");
    outline.setAttribute("width", String(width)); outline.setAttribute("height", String(height)); outline.setAttribute("viewBox", `0 0 ${width} ${height}`);
    $("outline-poly").setAttribute("d", shape.path); $("outline-glow").setAttribute("d", shape.path);
    // content stays inside the slanted sides and off the rounded corners: the bar is near the wide base, the settings reach the far edge
    const along = dock === "bottom" || dock === "top";
    const depth = along ? height : width;
    const atBar = shape.inset * Math.min(1, barDepth / depth);
    const full = shape.inset + shape.radius * 0.35;
    if (along) {
      root.setProperty("--pad-x", `${Math.max(14, Math.round(atBar + 10))}px`);
      root.setProperty("--pad-set", `${Math.max(0, Math.round(full + 6 - Math.max(14, atBar + 10)))}px`);
      root.setProperty("--pad-y", "0px");
    } else {
      root.setProperty("--pad-x", "14px"); root.setProperty("--pad-set", "0px");
      root.setProperty("--pad-y", `${Math.round(shape.radius * 0.35 + 6)}px`);
    }
  }

  // ---------- commands -> board cards ----------
  // `state.cards` are the commands this session. Their stage rails come from backend evidence (stages.js); they are
  // published to the board window and only a one-word count is shown here.
  function addCard(card) {
    const entry = { key: state.nextKey++, at: Date.now(), text: "", source: "text", updateId: "", tier: "", error: "", latency: null, listening: false, lastView: null, ...card };
    state.cards.push(entry);
    while (state.cards.length > MAX_COMMANDS) state.cards.shift();
    refreshBoard();
    return entry;
  }
  const voiceCards = new Map(); // realtime turn id -> card
  const attentionSeen = new Map();
  let lastPublished = "";
  function refreshBoard() {
    const { cards, open, views } = BoardCards.cardsFor(state.cards, state.status, { fallbackName: card => (card.source === "voice" ? (card.listening ? t("listening") : "…") : "") });
    for (const { card, view } of views) notifyAttention(card, view);
    state.openOps = open;
    renderOps(cards.length, open);
    const key = JSON.stringify([i18n.lang, cards]);
    if (key !== lastPublished) { lastPublished = key; try { api.board.publish(cards); } catch { /* board window is optional */ } }
  }
  function renderOps(total, open) {
    const el = $("ops");
    el.hidden = total === 0;
    el.dataset.live = open > 0 ? "yes" : "no";
    $("ops-word").textContent = t("ops");
    $("ops-count").textContent = String(BoardCards.opsCount(total, open));
  }

  // ---------- bar: transcript, orb, gear dot ----------
  function renderVoice() {
    const v = state.voice;
    body.dataset.voice = v.active ? v.state : "off";
    const orb = $("orb");
    orb.setAttribute("aria-pressed", v.active ? "true" : "false");
    const line = $("transcript");
    let text = state.transcript.text;
    if (v.active && v.state === "error") text = v.reason || t("error");
    else if (v.active && !text) text = v.state === "listening" ? t("listening") : v.state === "connecting" ? t("connecting") : "";
    line.textContent = text;
    line.dataset.empty = text ? "false" : "true";
    line.dataset.partial = state.transcript.partial ? "true" : "false";
    line.dataset.error = v.active && v.state === "error" ? "true" : "false";
  }
  function renderBackend() {
    $("btn-gear").dataset.backend = state.backendOk === null ? "unknown" : state.backendOk ? "ok" : "bad";
    $("term-word").textContent = t("command");
    $("cmd-input").placeholder = t("command");
    $("cmd-input").setAttribute("aria-label", t("command"));
    $("btn-gear").setAttribute("aria-label", t("settings"));
    $("orb").setAttribute("aria-label", t("mic"));
  }

  // ---------- backend polling ----------
  function hasOpenCommands() { return state.openOps > 0; }
  async function pollStatus() {
    try { state.status = await api.status(); state.backendOk = true; } catch { state.backendOk = false; }
    renderBackend(); refreshBoard();
  }
  let statusTimer = 0;
  function schedule() {
    clearTimeout(statusTimer);
    statusTimer = setTimeout(async () => { if (!document.hidden) await pollStatus(); schedule(); }, hasOpenCommands() || state.voice.state === "acting" ? POLL_ACTIVE_MS : POLL_IDLE_MS);
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
    if (card.tier === "emergency") api.dock.attention("emergency");
    const refused = response.accepted === false || response.ok === false || response.status === "refused" || /conflict|failed|rejected/.test(String(response.status || "")) || response.error;
    if (refused) card.error = String(response.error || response.refusal_reason || (response.compile_result && response.compile_result.refusal_reason) || response.status || "failed");
  }
  async function submitText(text, extra) {
    const card = addCard({ text, source: "text", ...extra });
    try {
      applyResponse(card, await api.sendCommand(text, i18n.lang));
    } catch (error) { api.dock.attention("error"); card.error = String((error && error.message) || error).replace(/^Error invoking remote method '[^']+': (Error: )?/, ""); }
    refreshBoard(); pokeSoon();
  }

  // ---------- emergency: global shortcuts (and the voice fast rules) take the same deterministic route (no model); the HUD has no emergency buttons ----------
  // Folds an emergency result ({ok, action, response|error}) into one board card: honest about refusal, never invents success.
  function foldEmergency(result) {
    if (!result || !EMERGENCY.includes(result.action)) return;
    const card = addCard({ text: EMERGENCY_TEXT[i18n.lang][result.action], source: "text", tier: "emergency" });
    api.dock.attention("emergency");
    if (result.ok && result.response) {
      applyResponse(card, result.response);
      card.tier = "emergency";
    } else card.error = String(result.error || "failed").replace(/^Error invoking remote method '[^']+': (Error: )?/, "");
    refreshBoard(); pokeSoon();
  }
  async function triggerEmergency(action) {
    let result;
    try { result = await api.emergency(action); } catch (error) { result = { ok: false, action, error: String((error && error.message) || error) }; }
    foldEmergency(result);
  }
  function onPush(event) {
    if (!event || typeof event !== "object") return;
    if (event.type === "voice-state") {
      const prev = state.voice;
      state.voice = { state: event.state, active: Boolean(event.active), reason: event.reason || "" };
      // Open the mic only once the Realtime session is ready: audio before that is dropped by main anyway, and the OS
      // mic indicator should not light up while we are still connecting. A reconnect (error) keeps the open mic.
      if (state.voice.active && !window.VoiMic.running && !micStarting && ["idle", "listening", "thinking", "acting"].includes(state.voice.state)) startMic();
      if (!state.voice.active && window.VoiMic.running) window.VoiMic.stop();
      if (prev.state !== "acting" && state.voice.state === "acting") pokeSoon();
      renderVoice(); schedule();
    } else if (event.type === "dock-state") applyDock(event);
    else if (event.type === "settings") applyEnvelope(event);
    else if (event.type === "emergency") foldEmergency(event); // a global shortcut fired in main
    else if (event.type === "session") { state.env.model = event.model || state.env.model; if (state.dock.settingsOpen) renderSettings(); }
    else if (event.type === "transcript") {
      let card = voiceCards.get(event.turn);
      if (!card) { card = addCard({ source: "voice", listening: true }); voiceCards.set(event.turn, card); state.transcript = { text: "", partial: true }; }
      if (event.text) { card.text = event.text; state.transcript = { text: event.text, partial: Boolean(event.partial) }; }
      if (event.final) card.listening = false;
      renderVoice(); refreshBoard();
    } else if (event.type === "intent") {
      let card = voiceCards.get(event.turn);
      if (!card) card = addCard({ source: "voice" });
      voiceCards.set(event.turn, card);
      card.listening = false;
      if (event.transcript && !card.text) { card.text = event.transcript; state.transcript = { text: event.transcript, partial: false }; }
      if (event.latency_ms) { card.latency = event.latency_ms; state.latencies.push(event.latency_ms); state.latencies = state.latencies.slice(-10); }
      if (event.tier) card.tier = normalizeTier(event.tier);
      if (event.ok && event.response) applyResponse(card, event.response);
      else { api.dock.attention("error"); card.error = String(event.error || event.kind || "failed"); }
      renderVoice(); refreshBoard(); pokeSoon();
      if (state.dock.settingsOpen && state.tab === "voice") renderSettings();
    }
  }

  // ---------- mic / voice ----------
  let micStarting = false, pttHeld = false;
  async function startMic() {
    micStarting = true;
    try { await window.VoiMic.start(chunk => api.voice.sendAudio(chunk), level => { state.level = level; api.voice.sendLevel(level); paintLevel(); }, state.settings.voice.device); }
    catch (error) { api.voice.micError(String((error && error.name === "NotAllowedError" ? "denied" : error && error.message) || error)); }
    finally { micStarting = false; }
  }
  async function toggleVoice() {
    try { if (state.voice.active) await api.voice.stop(); else await api.voice.start(); }
    catch (error) { state.voice = { state: "error", active: true, reason: String((error && error.message) || error) }; renderVoice(); }
  }
  async function restartMic() { if (window.VoiMic.running) { window.VoiMic.stop(); await startMic(); } }

  // ---------- dock ----------
  function applyDock(next) {
    if (!next) return;
    const wasRevealed = state.dock.revealed;
    state.dock = { revealed: Boolean(next.revealed), dock: next.dock || state.dock.dock, settingsOpen: Boolean(next.settingsOpen), reason: next.reason || "" };
    body.dataset.revealed = String(state.dock.revealed);
    body.dataset.settings = state.dock.settingsOpen ? "open" : "closed";
    $("btn-gear").setAttribute("aria-pressed", String(state.dock.settingsOpen));
    if (state.dock.revealed && !wasRevealed) pollStatus();
    if (!state.dock.revealed && wasRevealed && document.activeElement && document.activeElement.blur) document.activeElement.blur();
    if (state.dock.settingsOpen) renderSettings();
    reportRect();
  }
  let lastRect = "";
  function reportRect() {
    const hud = $("hud");
    // offset* ignores the CSS slide transform, so main always gets the revealed layout rectangle; main derives the same
    // rounded shape from it + the dock/slant/radius/skin settings (hud_shape.js) for pointer hit-testing.
    const rect = { x: hud.offsetLeft, y: hud.offsetTop, width: hud.offsetWidth, height: hud.offsetHeight };
    const key = JSON.stringify(rect);
    if (key !== lastRect && rect.width > 0 && rect.height > 0) { lastRect = key; api.dock.reportRect(rect); }
  }
  new ResizeObserver(() => { applyShape(); reportRect(); }).observe($("hud"));
  function notifyAttention(card, view) {
    const summary = view.rails.map(item => item.rail.stages.map(stage => stage.state[0]).join("")).join("|");
    const prev = attentionSeen.get(card.key);
    attentionSeen.set(card.key, summary);
    if (prev === undefined || prev === summary) return;
    const blocked = view.rails.some(item => item.rail.blocked);
    const emergency = view.rails.some(item => item.rail.tier === "emergency") || card.tier === "emergency";
    api.dock.attention(blocked ? "blocked" : emergency ? "emergency" : "stage");
  }

  // ---------- settings panel (generated from the schema; words, sliders with numbers, toggles; no prose) ----------
  const TABS = ["glass", "layout", "rainbow", "voice", "emergency", ...(S.SCHEMA.board ? ["board"] : []), "server", "reset"];
  let pending = {}, pendingTimer = 0;
  function patch(group, key, value) {
    state.settings[group][key] = value; // optimistic: the bar updates before the round trip
    applySettingsToCss();
    pending[group] = { ...(pending[group] || {}), [key]: value };
    clearTimeout(pendingTimer);
    pendingTimer = setTimeout(async () => {
      const send = pending; pending = {};
      try { applyEnvelope(await api.settings.set(send)); } catch { api.settings.get().then(applyEnvelope).catch(() => {}); }
    }, 60);
  }
  function applyEnvelope(envelope) {
    if (!envelope || !envelope.settings) return;
    if (!Object.keys(pending).length) state.settings = envelope.settings;
    state.env = { ...state.env, ...envelope.status };
    i18n.set(state.settings.voice.language === "en" ? "en" : "ko");
    applySettingsToCss(); renderBackend(); refreshBoard(); renderVoice();
    if (state.dock.settingsOpen && !isDragging()) renderSettings();
  }
  let dragging = false;
  const isDragging = () => dragging;
  const fmt = (spec, value) => `${spec.scale ? Math.round(value * spec.scale) : value}${spec.unit ? ` ${spec.unit}` : ""}`.replace(" %", "%");
  function slider(group, key) {
    const spec = S.SCHEMA[group][key];
    const scale = spec.scale || 1;
    const value = state.settings[group][key];
    const out = h("output", { text: fmt(spec, value) });
    const input = h("input", { type: "range", min: String(spec.min * scale), max: String(spec.max * scale), step: spec.integer || scale > 1 ? "1" : "0.01", value: String(Math.round(value * scale * 100) / 100), "aria-label": t(`${group}_${key}`) });
    input.addEventListener("pointerdown", () => { dragging = true; });
    window.addEventListener("pointerup", () => { dragging = false; }, { once: false });
    input.addEventListener("input", () => { const v = Number(input.value) / scale; out.textContent = fmt(spec, v); patch(group, key, spec.integer ? Math.round(v) : Math.round(v * 100) / 100); });
    return h("div", { class: "ctl" }, input, out);
  }
  function toggle(group, key) {
    const on = state.settings[group][key];
    const button = h("button", { class: "switch", type: "button", role: "switch", "aria-checked": String(on), "aria-label": t(`${group}_${key}`), "data-on": String(on) });
    button.addEventListener("click", () => { const next = button.dataset.on !== "true"; button.dataset.on = String(next); button.setAttribute("aria-checked", String(next)); patch(group, key, next); if (group === "glass" && key === "vibrancy") setTimeout(() => api.settings.get().then(applyEnvelope), 200); });
    return h("div", { class: "ctl" }, button);
  }
  function choice(group, key, values, labelOf) {
    const current = state.settings[group][key];
    return h("div", { class: "ctl seg", role: "group" }, values.map(value => {
      const b = h("button", { type: "button", "data-on": String(value === current), text: labelOf(value) });
      b.addEventListener("click", () => { patch(group, key, value); renderSettings(); if (group === "voice" && key === "language") applyEnvelope({ settings: state.settings, status: state.env }); });
      return b;
    }));
  }
  const row = (label, control, extra) => h("div", { class: "row" }, h("span", { class: "k", text: label }), control, extra || null);
  const rowFor = (group, key, control) => row(t(`${group}_${key}`), control || (S.SCHEMA[group][key].type === "bool" ? toggle(group, key) : slider(group, key)));

  function tabGlass() {
    const rows = [rowFor("glass", "skin", choice("glass", "skin", S.SCHEMA.glass.skin.values, v => t(`skin_${v}`)))];
    rows.push(...["alpha", "blur"].map(key => rowFor("glass", key)));
    if (state.env.vibrancySupported) rows.push(rowFor("glass", "vibrancy"));
    return [...rows, ...["hue", "saturation", "borderGlow", "radius", "slant", "stars", "starSpeed", "nebula", "textSize"].map(key => rowFor("glass", key)), rowFor("glass", "twinkle"), rowFor("glass", "meteors"), rowFor("glass", "reduceMotion")];
  }
  function tabLayout() {
    const displays = [{ id: "cursor", label: t("display_cursor") }, ...state.env.displays.map(d => ({ id: `id:${d.id}`, label: d.label }))];
    return [
      rowFor("layout", "dock", choice("layout", "dock", S.DOCKS, v => t(`dock_${v}`))),
      ...["width", "height"].map(key => rowFor("layout", key)),
      rowFor("layout", "autoHide"), rowFor("layout", "hideDelay"), rowFor("layout", "zone"), rowFor("layout", "pinned"),
      rowFor("layout", "display", choice("layout", "display", displays.map(d => d.id), v => (displays.find(d => d.id === v) || { label: v }).label)),
      rowFor("layout", "margin"),
    ];
  }
  function tabRainbow() {
    const preview = h("button", { class: "btn", type: "button", text: t("preview") });
    preview.addEventListener("click", () => api.settings.previewRainbow().catch(() => {}));
    return [rowFor("rainbow", "enabled"), rowFor("rainbow", "screen", choice("rainbow", "screen", ["current", "all"], v => t(`screen_${v}`))), ...["depth", "intensity", "speed", "brightness"].map(key => rowFor("rainbow", key)), rowFor("rainbow", "thinking"), rowFor("rainbow", "reactive"), row(t("preview"), h("div", { class: "ctl" }, preview))];
  }
  let meterEl = null;
  function paintLevel() { if (meterEl && meterEl.isConnected) meterEl.style.setProperty("--lvl", String(Math.min(1, state.level))); }
  // One recorder for every accelerator setting (voice toggle, emergency words, board paging): click, press keys, main checks it.
  function shortcutButton(group, key) {
    const id = `${group}.${key}`;
    const recording = state.recording === id;
    const button = h("button", { class: "btn", type: "button", text: recording ? t("pressKeys") : state.settings[group][key] });
    button.addEventListener("click", () => { state.recording = id; renderSettings(); });
    button.addEventListener("keydown", async event => {
      if (state.recording !== id) return;
      event.preventDefault();
      if (event.key === "Escape") { state.recording = false; renderSettings(); return; }
      const accelerator = acceleratorFrom(event);
      if (!accelerator) return;
      const check = await api.settings.checkShortcut(accelerator).catch(() => ({ ok: false }));
      state.recording = false;
      if (check.ok) patch(group, key, accelerator); else button.textContent = t("shortcutUsed");
      setTimeout(renderSettings, check.ok ? 0 : 900);
    });
    if (recording) setTimeout(() => button.focus(), 0);
    return button;
  }
  function tabEmergency() {
    const keys = (state.env.shortcut && state.env.shortcut.emergency) || {};
    return EMERGENCY.map(action => row(t(`emergency_${action}`), h("div", { class: "ctl" }, shortcutButton("emergency", action), keys[action] === false ? h("output", { class: "status-word", "data-state": "bad", text: t("em_nokey") }) : null)));
  }
  function tabBoard() {
    const specs = S.SCHEMA.board;
    return Object.keys(specs).map(key => {
      const spec = specs[key];
      const label = t(`board_${key}`);
      if (spec.type === "bool") return rowFor("board", key);
      if (spec.type === "enum") return row(label, choice("board", key, spec.values, v => t(`anchor_${v.replace("-", "")}`)));
      if (spec.type === "str") return row(label, h("div", { class: "ctl" }, shortcutButton("board", key)));
      return rowFor("board", key);
    });
  }
  function tabVoice() {
    const select = h("select", { "aria-label": t("voice_device") }, h("option", { value: "", text: t("defaultDevice") }), ...state.devices.map(d => h("option", { value: d.deviceId, text: (d.label || d.deviceId).slice(0, 36), selected: d.deviceId === state.settings.voice.device })));
    select.addEventListener("change", async () => { patch("voice", "device", select.value); await restartMic(); });
    meterEl = h("div", { class: "meter", style: "" }, h("i"));
    const record = shortcutButton("voice", "shortcut");
    const lat = state.latencies;
    const avg = key => { const v = lat.map(l => l[key]).filter(x => typeof x === "number"); return v.length ? Math.round(v.reduce((a, b) => a + b, 0) / v.length) : null; };
    return [
      row(t("voice_device"), h("div", { class: "ctl" }, select)),
      row(t("voice_level"), h("div", { class: "ctl" }, meterEl)),
      rowFor("voice", "silence"), rowFor("voice", "sensitivity"),
      rowFor("voice", "mode", choice("voice", "mode", ["always", "ptt"], v => t(`mode_${v}`))),
      row(t("voice_shortcut"), h("div", { class: "ctl" }, record)),
      rowFor("voice", "language", choice("voice", "language", ["ko", "en", "auto"], v => t(`lang_${v}`))),
      row(t("voice_model"), h("div", { class: "ctl" }, h("output", { text: state.env.model || "—" }))),
      row(t("voice_latency"), h("div", { class: "ctl lat" }, h("output", { text: `${t("lat_speech")} ${avg("speech_end_to_tool_call") ?? "—"} ms` }), h("output", { text: `${t("lat_publish")} ${avg("tool_call_to_publish") ?? "—"} ms` }), h("output", { class: "dim", text: `n=${lat.length}` }))),
    ];
  }
  function acceleratorFrom(event) {
    if (["Control", "Meta", "Alt", "Shift"].includes(event.key)) return "";
    const mods = [];
    if (event.metaKey || event.ctrlKey) mods.push("CommandOrControl");
    if (event.altKey) mods.push("Alt");
    if (event.shiftKey) mods.push("Shift");
    if (!mods.length) return "";
    const map = { " ": "Space", ArrowUp: "Up", ArrowDown: "Down", ArrowLeft: "Left", ArrowRight: "Right", Enter: "Return" };
    const code = event.code || "";
    let key = map[event.key] || (/^Key([A-Z])$/.test(code) ? code.slice(3) : /^Digit(\d)$/.test(code) ? code.slice(5) : event.key.length === 1 ? event.key.toUpperCase() : event.key);
    const full = `${mods.join("+")}+${key}`;
    return S.ACCELERATOR.test(full) ? full : "";
  }
  function tabServer() {
    const input = h("input", { type: "text", value: state.settings.server.url, spellcheck: false, "aria-label": t("server_url") });
    const word = h("output", { class: "status-word", "data-state": state.server.state, text: state.server.state === "ok" ? t("connected") : state.server.state === "bad" ? t("disconnected") : t("checking") });
    const apply = h("button", { class: "btn", type: "button", text: t("apply") });
    const probe = async () => {
      state.server = { state: "checking" }; word.dataset.state = "checking"; word.textContent = t("checking");
      let url; try { url = new URL(input.value).origin; } catch { state.server = { state: "bad" }; word.dataset.state = "bad"; word.textContent = t("disconnected"); return false; }
      const result = await api.settings.probeServer(url).catch(() => ({ ok: false }));
      state.server = { state: result.ok ? "ok" : "bad" }; word.dataset.state = state.server.state; word.textContent = result.ok ? `${t("connected")} ${result.ms} ms` : t("disconnected");
      return result.ok;
    };
    apply.addEventListener("click", async () => { try { new URL(input.value); } catch { return; } try { await api.settings.set({ server: { url: new URL(input.value).origin } }); } catch { word.dataset.state = "bad"; word.textContent = t("disconnected"); return; } probe(); pollStatus(); });
    setTimeout(probe, 0);
    return [row(t("server_url"), h("div", { class: "ctl" }, input, apply)), row(t("server_status"), h("div", { class: "ctl" }, word))];
  }
  function tabReset() {
    const make = (label, group) => {
      const b = h("button", { class: "btn danger", type: "button", text: state.resetArmed === (group || "all") ? t("confirm") : label });
      b.addEventListener("click", async () => {
        if (state.resetArmed !== (group || "all")) { state.resetArmed = group || "all"; renderSettings(); setTimeout(() => { if (state.resetArmed) { state.resetArmed = false; if (state.dock.settingsOpen && state.tab === "reset") renderSettings(); } }, 3000); return; }
        state.resetArmed = false;
        applyEnvelope(await api.settings.reset(group).catch(() => null));
        renderSettings();
      });
      return b;
    };
    return [row(t("reset_all"), h("div", { class: "ctl" }, make(t("reset_all"), undefined))), ...["glass", "layout", "rainbow", "voice", "emergency", ...(S.SCHEMA.board ? ["board"] : [])].map(group => row(t(`tab_${group}`), h("div", { class: "ctl" }, make(t(`tab_${group}`), group))))];
  }
  const TAB_BUILDERS = { glass: tabGlass, layout: tabLayout, rainbow: tabRainbow, voice: tabVoice, emergency: tabEmergency, board: tabBoard, server: tabServer, reset: tabReset };
  function renderSettings() {
    if (!state.dock.settingsOpen) return;
    const tabs = $("tabs");
    tabs.replaceChildren(...TABS.map(tab => {
      const b = h("button", { type: "button", role: "tab", "aria-selected": String(tab === state.tab), "data-tab": tab, text: t(`tab_${tab}`) });
      b.addEventListener("click", () => { state.tab = tab; renderSettings(); });
      return b;
    }));
    meterEl = null;
    $("rows").replaceChildren(...TAB_BUILDERS[state.tab]());
    reportRect();
  }
  async function loadDevices() {
    try { state.devices = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === "audioinput"); } catch { state.devices = []; }
    if (state.dock.settingsOpen && state.tab === "voice") renderSettings();
  }
  function setSettingsOpen(open) {
    api.settings.open(open).then(applyDock).catch(() => {});
    if (open) { loadDevices(); }
  }

  // ---------- wiring ----------
  $("cmd-form").addEventListener("submit", event => {
    event.preventDefault();
    const input = $("cmd-input"); const text = input.value.trim();
    if (!text) return;
    input.value = ""; $("cmd-form").dataset.filled = "false"; submitText(text);
  });
  $("cmd-input").addEventListener("input", () => { $("cmd-form").dataset.filled = $("cmd-input").value ? "true" : "false"; });
  $("orb").addEventListener("click", toggleVoice);
  $("btn-gear").addEventListener("click", () => setSettingsOpen(!state.dock.settingsOpen));
  // Typing needs key focus; hovering never takes it. Focus is granted on an explicit click only.
  $("cmd-input").addEventListener("pointerdown", () => { api.dock.focus(true).then(() => $("cmd-input").focus()).catch(() => {}); });
  $("cmd-input").addEventListener("blur", () => { setTimeout(() => { if (!document.hasFocus() || document.activeElement === document.body) api.dock.focus(false).catch(() => {}); }, 120); });
  document.addEventListener("pointerdown", event => { // click outside the settings panel (but not on the gear) closes it
    if (!state.dock.settingsOpen) return;
    if (event.target.closest("#settings") || event.target.closest("#btn-gear")) return;
    setSettingsOpen(false);
  });
  window.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      if (state.recording) return;
      if (state.dock.settingsOpen) { setSettingsOpen(false); return; }
      if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
      api.dock.focus(false).catch(() => {});
    }
    if ((event.metaKey || event.ctrlKey) && event.key === ",") { event.preventDefault(); setSettingsOpen(!state.dock.settingsOpen); }
    if ((event.metaKey || event.ctrlKey) && event.key === ".") triggerEmergency("retreat");
    if (state.settings.voice.mode === "ptt" && event.code === "Space" && !event.repeat && !pttHeld && document.activeElement && document.activeElement.tagName !== "INPUT") { pttHeld = true; api.voice.ptt(true); }
  });
  window.addEventListener("keyup", event => { if (pttHeld && event.code === "Space") { pttHeld = false; api.voice.ptt(false); } });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) pokeSoon(); });

  api.onEvent(onPush);
  applySettingsToCss(); renderBackend(); renderVoice();
  api.settings.get().then(applyEnvelope).catch(() => {});
  api.dock.state().then(applyDock).catch(() => {});
  api.voice.state().then(snapshot => { state.voice = { state: snapshot.state, active: snapshot.active, reason: snapshot.reason || "" }; renderVoice(); }).catch(() => {});
  pollStatus().then(schedule);
  setInterval(refreshBoard, 4000);
  reportRect();
})();
