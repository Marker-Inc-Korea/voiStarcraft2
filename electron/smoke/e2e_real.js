"use strict";
// REAL end-to-end run: one utterance per process.
//   E2E_NAME=u1_scout_attack VOI_COMMANDER_URL=http://127.0.0.1:18765 npx electron smoke/e2e_real.js
// Real backend (web_gui --dry-run), real Realtime proxy session (server VAD), real Chromium mic path fed from a
// TTS wav via --use-file-for-fake-audio-capture. Nothing is mocked. Evidence -> docs/e2e/<name>.json + PNGs.
const { app } = require("electron");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFile, execFileSync } = require("node:child_process");

const NAME = process.env.E2E_NAME;
if (!NAME) { console.error("E2E_NAME required"); process.exit(2); }
const OUT = path.join(__dirname, "..", "docs", "e2e");
const WAV = path.join(OUT, "audio", `${NAME}.wav`);
if (!fs.existsSync(WAV)) { console.error(`missing ${WAV}`); process.exit(2); }
app.commandLine.appendSwitch("use-fake-device-for-media-stream");
app.commandLine.appendSwitch("use-fake-ui-for-media-stream");
// Chromium's sandboxed audio service cannot read ~/Desktop (macOS privacy), so feed it a copy under the temp dir.
const FEED = path.join(os.tmpdir(), `voi-e2e-${NAME}.wav`);
fs.copyFileSync(WAV, FEED);
// Test harness only: the sandboxed audio service cannot open the wav, so its sandbox is disabled for this run (not in the app).
app.commandLine.appendSwitch("disable-features", "AudioServiceSandbox");
app.commandLine.appendSwitch("use-file-for-fake-audio-capture", `${FEED}%noloop`);
app.setPath("userData", fs.mkdtempSync(path.join(os.tmpdir(), "voi-e2e-")));

const WARP = process.env.WARP_CURSOR || "/tmp/warp_cursor";
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const result = { name: NAME, startedAt: new Date().toISOString(), events: [], overlay: [], shots: [], checks: [], notes: [] };
const note = text => { result.notes.push(text); console.log(`[e2e] ${text}`); };
const check = (name, ok, detail) => { result.checks.push({ name, ok: Boolean(ok), detail }); console.log(`[e2e] ${ok ? "PASS" : "FAIL"} ${name}${detail === undefined ? "" : ` ${JSON.stringify(detail)}`}`); };

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const base = process.env.VOI_COMMANDER_URL;
  if (!base) throw new Error("VOI_COMMANDER_URL required (real backend)");
  const main = require("../main.js");
  await app.whenReady();
  const I = main._internals;
  try { execFileSync(WARP, ["756", "700"]); } catch { /* cursor stays */ }
  const waitFor = async (fn, label, timeout = 30000) => { const t0 = Date.now(); for (;;) { const value = await fn(); if (value) return value; if (Date.now() - t0 > timeout) throw new Error(`timeout: ${label}`); await sleep(40); } };
  const win = await waitFor(() => I.getMain(), "dock window");
  await waitFor(() => !win.webContents.isLoading(), "renderer load");
  const overlay = await waitFor(() => I.getOverlay(), "overlay window");
  await waitFor(() => !overlay.webContents.isLoading(), "overlay load");
  const js = code => win.webContents.executeJavaScript(code, true);
  const shot = name => new Promise(resolve => { const file = path.join(OUT, `${NAME}-${name}.png`); execFile("screencapture", ["-x", file], error => { result.shots.push({ name, file: path.basename(file), t: Date.now(), ok: !error }); console.log(`[e2e] screenshot ${name}`); resolve(); }); });
  await waitFor(() => js("document.getElementById('btn-gear').dataset.backend==='ok'"), "backend reachable", 15000);
  // Record every push event the renderer receives, with wall-clock timestamps (same events the UI renders from).
  await js("window.__e2e=[]; window.commander.onEvent(e=>window.__e2e.push(Object.assign({t:Date.now()},e))); 0");
  // Overlay state sampler (the rainbow window), independent of the renderer.
  let lastOverlay = "";
  const sampler = setInterval(() => { const s = `${I.getOverlayState()}|${overlay.isVisible()}`; if (s !== lastOverlay) { lastOverlay = s; result.overlay.push({ t: Date.now(), state: I.getOverlayState(), visible: overlay.isVisible() }); } }, 30);
  const eventsNow = async () => js("window.__e2e");

  // Arm voice by clicking the real orb. The mic opens once the Realtime session is ready; the wav starts then.
  const armedAt = Date.now();
  result.armedAt = armedAt;
  await js("document.getElementById('orb').click()");
  note("orb clicked; waiting for Realtime session (429 capacity errors retry with back-off inside voice.js)");

  const seen = new Set();
  const stateAt = async () => (await eventsNow()).filter(e => e.type === "voice-state");
  const waitState = async (state, timeout) => waitFor(async () => (await stateAt()).find(e => e.state === state && e.t >= armedAt), `state ${state}`, timeout);
  const shootOnce = async (key, delay = 0) => { if (seen.has(key)) return; seen.add(key); if (delay) await sleep(delay); await shot(key); };

  try {
    await waitFor(async () => (await stateAt()).some(e => e.state === "idle" && e.active), "voice ready (idle+active)", 150000);
    result.readyAt = Date.now();
    note(`session ready after ${result.readyAt - armedAt} ms (includes any 429 retries)`);
    const errs = (await stateAt()).filter(e => e.state === "error").map(e => e.reason);
    if (errs.length) note(`error states before ready: ${[...new Set(errs)].join(" | ").slice(0, 300)}`);
    result.preReadyErrors = [...new Set(errs)];
    await shot("1-ready");
    await waitState("listening", 30000);
    await sleep(500);
    await shootOnce("2-listening");
    result.overlayDuringListening = { state: I.getOverlayState(), visible: overlay.isVisible(), bounds: overlay.getBounds() };
    await waitState("thinking", 30000).catch(() => note("no thinking state observed (server answered between speech_stopped and poll)"));
    await shootOnce("3-thinking");
    await waitFor(async () => (await eventsNow()).some(e => e.type === "intent"), "intent event", 45000);
    await sleep(700);
    await shootOnce("4-acting");
    await waitFor(async () => { const ev = await stateAt(); const last = ev[ev.length - 1]; return last && last.state === "idle" && ev.some(e => e.state === "acting"); }, "return to idle", 20000).catch(() => note("did not return to idle within 20s"));
    I.applySettings({ layout: { pinned: true } }); // keep the bar up so the final stage rails are visible (it would auto-collapse)
    await sleep(6000); // let the backend progress stages
    await shot("5-cards");
    const dom = await js(`(()=>({
      transcript: document.getElementById('transcript').textContent,
      ops: document.getElementById('ops-count').textContent,
      voiceBody: document.body.dataset.voice
    }))()`);
    result.dom = dom;
  } catch (error) {
    result.error = String((error && error.stack) || error);
    note(`FAILED: ${error.message}`);
    await shot("x-failure").catch(() => {});
  }
  clearInterval(sampler);
  result.events = (await eventsNow().catch(() => [])).map(e => (e.type === "voice-state" ? { t: e.t, type: e.type, state: e.state, active: e.active, reason: e.reason || "" } : e.type === "transcript" ? { t: e.t, type: e.type, text: e.text, partial: e.partial, final: e.final, turn: e.turn } : e.type === "intent" ? { t: e.t, type: e.type, turn: e.turn, ok: e.ok, kind: e.kind, tier: e.tier, transcript: e.transcript, latency_ms: e.latency_ms, error: e.error, tool_arguments: e.tool_arguments, response: e.response && { accepted: e.response.accepted, status: e.response.status, tier: e.response.tier, source: e.response.source, update_id: e.response.update_id, direct_runtime: e.response.direct_runtime, latency_ms: e.response.latency_ms, error: e.response.error } } : { t: e.t, type: e.type }));
  // Backend ground truth: the voice_intent journal event + status operations for this update.
  try {
    const history = await (await fetch(new URL("/api/history?after=0", base))).json();
    result.backendVoiceIntents = history.events.filter(e => e.type === "voice_intent" || e.kind === "voice_intent" || (e.payload && e.payload.transcript)).slice(-3);
    const status = await (await fetch(new URL("/api/micromachine/status", base))).json();
    const updateIds = result.events.filter(e => e.type === "intent" && e.response).map(e => e.response.update_id);
    result.backendOperations = (status.operations || []).filter(op => updateIds.includes(op.update_id)).map(op => ({ operation_id: op.operation_id, mission: op.mission, disposition: op.disposition, transport_status: op.transport_status, consumption_status: op.consumption_status, layer: op.update && op.update.vector && op.update.vector.command_layer, goal: op.update && op.update.vector && op.update.vector.goal, stages: ((op.intervention || {}).command_execution || {}).stages && ((op.intervention || {}).command_execution || {}).stages.map(s => `${s.name}:${s.ok}`) }));
    result.directRuntime = status.direct_runtime;
    result.latestRequest = status.latest_request && { status: status.latest_request.status, consumption_status: status.latest_request.consumption_status, update_id: status.latest_request.update_id };
  } catch (error) { note(`backend ground-truth fetch failed: ${error.message}`); }
  fs.writeFileSync(path.join(OUT, `${NAME}.json`), JSON.stringify(result, null, 2));
  app.exit(result.error ? 1 : 0);
}
main().catch(error => { console.error("[e2e] FAILED", error); try { result.error = String(error.stack || error); fs.writeFileSync(path.join(OUT, `${NAME}.json`), JSON.stringify(result, null, 2)); } catch { /* ignore */ } app.exit(1); });
