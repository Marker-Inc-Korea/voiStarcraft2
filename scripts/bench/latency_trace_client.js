"use strict";
// Node driver for scripts/bench/latency_trace.py. It runs the REAL electron/voice.js client (rule path, intent POST,
// response.cancel, pruning, session.update) in plain Node (no Electron, no pointer/screens) against the real Realtime proxy
// and a local `web_gui --dry-run`, and records raw per-utterance timestamps (performance.now(), ms). The mic is emulated by
// streaming pcm16 24 kHz in 50 ms chunks at real-time pace, exactly like electron/renderer/mic-worklet.js.
//
//   node latency_trace_client.js --job job.json
// job.json: { base, out, rows:[{id,tier,text,files:[v0,v1,v2 pcm paths]}], reps, silenceMs, endpoint:"server_vad"|"energy",
//   energyMs, energyShortMs, auth:"mint"|"direct", sessionBody (path|null), blackboardDir, label, coldN, prefetch, gapMs,
//   earlyFire:{stableMs}|null, thresholdSilenceMs }
const fs = require("node:fs");
const path = require("node:path");
const { performance } = require("node:perf_hooks");
const WebSocket = require(path.resolve(__dirname, "..", "..", "electron", "node_modules", "ws"));
const { createVoiceClient } = require(path.resolve(__dirname, "..", "..", "electron", "voice.js"));

const job = JSON.parse(fs.readFileSync(process.argv[process.argv.indexOf("--job") + 1], "utf8"));
const CHUNK_BYTES = 2400; // 50 ms @ 24 kHz pcm16
const CHUNK_MS = 50;
const out = fs.createWriteStream(job.out, { flags: "a" });
const emit = obj => out.write(JSON.stringify(obj) + "\n");
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const now = () => performance.now();
const apiKey = () => { for (const name of ["VOI_REALTIME_API_KEY", "MYPROXY_API_KEY", "OPENAI_API_KEY"]) if (process.env[name]) return process.env[name]; return ""; };

let current = null; // the utterance being measured
let auth = "mint";
let mintInfo = { status: null, ms: null, attempts: 0 };
const sessionBody = job.sessionBody ? JSON.parse(fs.readFileSync(job.sessionBody, "utf8")) : null;
const directBase = (process.env.VOI_REALTIME_BASE_URL || "https://proxy.nomadamas.org/v1").replace(/\/$/, "");
const model = (process.env.VOI_REALTIME_MODEL || "gpt-realtime");

// ---- CPU steal proxy: wall time of a fixed 50 ms CPU-time busy loop (1.0 = no contention) ----
function stealProbe() {
  const cpu0 = process.cpuUsage();
  const w0 = now();
  let spin = 0;
  for (;;) {
    spin += 1;
    if ((spin & 0x3fff) === 0) {
      const c = process.cpuUsage(cpu0);
      if ((c.user + c.system) >= 50000) break;
    }
  }
  return Math.round((now() - w0) / 50 * 100) / 100;
}

// ---- traced requestJson (local web_gui) ----
async function requestJson(pathname, options = {}) {
  const t0 = now();
  const record = { path: pathname, t0, method: options.method || "GET" };
  if (current) current.requests.push(record);
  let response;
  const attempts = pathname === "/api/realtime/session" ? 4 : 1; // proxy 429 `client_secret_capacity_exhausted`: back off 1/2/4 s, then key auth
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      response = await fetch(new URL(pathname, job.base), {
        ...options, redirect: "error", signal: AbortSignal.timeout(options.timeoutMs || 25000),
        headers: { accept: "application/json", "content-type": "application/json" },
      });
    } catch (error) { record.t1 = now(); record.error = String(error.message || error); throw error; }
    mintInfo.attempts = attempt + 1;
    if (response.status !== 429 || attempt === attempts - 1) break;
    mintInfo.n429 = (mintInfo.n429 || 0) + 1;
    await response.arrayBuffer().catch(() => {});
    await sleep(1000 * 2 ** attempt);
  }
  let payload;
  try { payload = await response.json(); } catch { payload = {}; }
  record.t1 = now();
  record.status = response.status;
  if (pathname === "/api/realtime/intent") {
    try { record.body = JSON.parse(options.body); } catch { /* ignore */ }
    record.backend_handle = payload && payload.latency_ms ? payload.latency_ms.backend_handle : null;
    record.update_id = payload && payload.update_id || "";
    record.resp_status = payload && payload.status || "";
    record.accepted = payload && payload.accepted;
    record.tier = payload && payload.tier || "";
    record.source = payload && payload.source || "";
  }
  if (pathname === "/api/realtime/session") {
    mintInfo.status = response.status; mintInfo.ms = Math.round(record.t1 - t0);
    if (job.auth === "direct" || !response.ok) {
      // direct-key fallback (proxy 429 capacity / explicit A/B): same session body is applied via session.update below
      auth = "direct_key";
      const base = directBase.replace(/^http/, "ws");
      return { client_secret: apiKey(), expires_at: Math.floor(Date.now() / 1000) + 3000, model, ws_url: `${base}/realtime?model=${model}`, tools: [], tier_hint: "" };
    }
    auth = "mint";
  }
  if (!response.ok) throw new Error((payload && (payload.error || payload.refusal_reason)) || `Commander HTTP ${response.status}`);
  return payload;
}

// ---- traced WebSocket ----
class TracedWS extends WebSocket {
  constructor(url, opts) {
    super(url, opts);
    this.on("message", data => { const t = now(); let ev; try { ev = JSON.parse(String(data)); } catch { return; } onWsEvent(ev, t); });
  }
  send(data, ...rest) {
    let text = data;
    try {
      const ev = JSON.parse(String(data));
      const t = now();
      if (current && ["response.cancel", "input_audio_buffer.commit", "input_audio_buffer.clear", "response.create"].includes(ev.type)) current.sent.push({ type: ev.type, t });
      if (ev.type === "session.update" && auth === "direct_key" && sessionBody) {
        const body = JSON.parse(JSON.stringify(sessionBody.session)); delete body.model;
        ev.session = { ...body, audio: { input: { ...(body.audio && body.audio.input || {}), ...(ev.session.audio && ev.session.audio.input || {}) } } };
        text = JSON.stringify(ev);
      }
    } catch { /* binary / non-JSON */ }
    return super.send(text, ...rest);
  }
}

function onWsEvent(ev, t) {
  if (ev.type === "session.updated") { sessionUpdatedAt = t; return; }
  const u = current;
  if (!u) return;
  const e = u.ev;
  switch (ev.type) {
    case "input_audio_buffer.speech_started": e.speech_started = e.speech_started ?? t; u.audio_start_ms = ev.audio_start_ms; break;
    case "input_audio_buffer.speech_stopped": e.speech_stopped = e.speech_stopped ?? t; u.audio_end_ms = ev.audio_end_ms; break;
    case "input_audio_buffer.committed": e.committed = e.committed ?? t; break;
    case "conversation.item.input_audio_transcription.delta": u.deltas.push({ t, d: ev.delta }); break;
    case "conversation.item.input_audio_transcription.completed": e.transcript = e.transcript ?? t; u.asr_text = ev.transcript; break;
    case "response.created": e.response_created = e.response_created ?? t; break;
    case "response.function_call_arguments.delta": e.tool_first = e.tool_first ?? t; break;
    case "response.function_call_arguments.done": e.tool_done = e.tool_done ?? t; u.tool_args_raw = ev.arguments; break;
    case "response.done": e.response_done = t; u.response_status = ev.response && ev.response.status; u.tokens_total = ev.response && ev.response.usage && ev.response.usage.total_tokens; break;
    case "error": u.errors.push({ t, code: ev.error && ev.error.code, message: String(ev.error && ev.error.message || "").slice(0, 160) }); break;
    default: break;
  }
}
let sessionUpdatedAt = 0;
function pendingEffects() { return false; }

// ---- compile-result poll (async publish -> latest_modulation_compile_result.json) ----
async function waitCompile(updateId, budgetMs) {
  const file = path.join(job.blackboardDir, "latest_modulation_compile_result.json");
  const deadline = now() + budgetMs;
  while (now() < deadline) {
    try {
      const doc = JSON.parse(fs.readFileSync(file, "utf8"));
      if (doc && doc.update_id === updateId) return { t: now(), status: doc.status || "" };
    } catch { /* not written yet / mid-rename */ }
    await sleep(2);
  }
  return null;
}

// ---- audio ----
function loadPcm(file) { return fs.readFileSync(file); }
function chunkRms(buf, offset) {
  let sum = 0; const n = Math.min(CHUNK_BYTES, buf.length - offset) >> 1;
  if (n <= 0) return 0;
  for (let i = 0; i < n; i += 1) { const v = buf.readInt16LE(offset + i * 2) / 32768; sum += v * v; }
  return Math.sqrt(sum / n);
}
function voicedEndMs(pcm) { // last chunk whose RMS clears max(0.02, 12% of the p95 chunk RMS)
  const rms = []; for (let o = 0; o < pcm.length; o += CHUNK_BYTES) rms.push(chunkRms(pcm, o));
  const sorted = [...rms].sort((a, b) => a - b);
  const thr = Math.max(0.02, 0.12 * sorted[Math.floor(sorted.length * 0.95)]);
  let first = -1, last = -1; rms.forEach((v, i) => { if (v > thr) { if (first < 0) first = i; last = i; } });
  return { startMs: first * CHUNK_MS, endMs: (last + 1) * CHUNK_MS, thr, fileMs: Math.round(pcm.length / 48) };
}

// ---- client ----
let client = null;
const prefs = { silenceMs: job.silenceMs || 450, threshold: 0.5, ptt: job.endpoint === "energy", language: "auto", fastRules: job.fastRules !== false, ...(job.prefs || {}) };
const env = { VOI_VAD_SILENCE_MS: String(job.silenceMs || 450), ...(job.env || {}) };
function makeClient(extra = {}) {
  return createVoiceClient({
    requestJson, WebSocketImpl: TracedWS, env, getPrefs: () => prefs,
    onIntent: intent => { if (current) current.intents.push({ t: now(), intent }); },
    onTranscript: tr => { if (current) current.transcripts.push({ t: now(), turn: tr.turn, partial: tr.partial, final: tr.final, started: Boolean(tr.started), text: tr.text }); },
    onLog: msg => { if (current) current.logs.push({ t: now(), msg: String(msg).slice(0, 200) }); else startupLogs.push(String(msg).slice(0, 200)); },
    ...extra,
  });
}
const startupLogs = [];

async function waitReady(timeoutMs) {
  const deadline = now() + timeoutMs;
  while (now() < deadline) {
    const s = client.snapshot();
    if (s.state === "idle" && s.active) return true;
    if (s.state === "error") await sleep(100); else await sleep(10);
  }
  return false;
}

async function runUtterance(row, rep, variantIdx) {
  const file = row.files[variantIdx % row.files.length];
  const pcm = loadPcm(file);
  const voiced = voicedEndMs(pcm);
  const u = { kind: "utterance", label: job.label, id: row.id, tier: row.tier, text: row.text, rep, variant: `v${variantIdx % row.files.length}`, voiced, ev: {}, deltas: [], sent: [], requests: [], intents: [], transcripts: [], logs: [], errors: [], late: [] };
  u.steal_before = stealProbe();
  u.uptime_load = require("node:os").loadavg();
  current = u;
  const energy = job.endpoint === "energy";
  const t0 = now() + 30;
  u.t_stream0 = t0;
  let stopSent = false;
  let voicedMs = 0, silenceRun = 0, everVoiced = false, peak = 0;
  let k = 0;
  const maxMs = (job.maxWaitMs || 30000);
  let doneAt = 0;
  if (energy) client.pttSet(true);
  for (;;) {
    const due = t0 + (k + 1) * CHUNK_MS; // a real mic delivers chunk k when its 50 ms have elapsed
    const wait = due - now();
    if (wait > 0) await sleep(wait);
    u.late.push(Math.max(0, Math.round(now() - due)));
    const off = k * CHUNK_BYTES;
    const chunk = off < pcm.length ? pcm.subarray(off, Math.min(pcm.length, off + CHUNK_BYTES)) : Buffer.alloc(CHUNK_BYTES);
    if (!stopSent || !energy) client.pushAudio(chunk);
    if (energy && !stopSent) {
      const r = chunkRms(chunk, 0); peak = Math.max(peak * 0.995, r);
      const v = r > Math.max(job.energyThreshold || 0.03, 0.12 * peak);
      if (v) { voicedMs += CHUNK_MS; silenceRun = 0; everVoiced = true; } else if (everVoiced) silenceRun += CHUNK_MS;
      const needed = voicedMs <= (job.shortVoicedMs || 900) ? (job.energyShortMs || 150) : (job.energyMs || 400);
      if (everVoiced && voicedMs >= 100 && silenceRun >= needed) { stopSent = true; u.ev.client_endpoint = now(); client.pttSet(false); }
    }
    k += 1;
    if (u.intents.length && !doneAt) doneAt = now();
    if (!doneAt && u.ev.response_done !== undefined && u.ev.transcript !== undefined && now() - Math.max(u.ev.response_done, u.ev.transcript) > 2500 && u.requests.every(r => r.t1 !== undefined) && !pendingEffects()) { u.no_intent = true; break; } // never cut a POST that is still in flight
    if (doneAt) {
      const settled = u.ev.response_done !== undefined && (u.ev.transcript !== undefined || now() - doneAt > 3000);
      if (settled || now() - doneAt > 6000) break;
    }
    if (now() - t0 > maxMs) { u.timeout = true; break; }
  }
  // compile result for the accepted intents
  for (const { intent } of u.intents) {
    const response = intent.response || {};
    const updateId = response.update_id;
    if (updateId && response.accepted !== false) {
      const c = await waitCompile(updateId, 4000);
      intent.compile = c ? { t: c.t, status: c.status } : null;
    }
  }
  u.steal_after = stealProbe();
  u.auth = auth;
  current = null;
  // idle gap lets the pruning deletes land and the VAD reset
  await sleep(job.gapMs || 500);
  return u;
}

function sanitize(u) {
  // drop heavy bits, keep what the analyzer needs
  const intents = u.intents.map(({ t, intent }) => ({ t, ok: intent.ok, kind: intent.kind, path: intent.path, family: intent.family || "", transcript: intent.transcript || "", tier: intent.tier || "", tool_arguments: intent.tool_arguments || null,
    latency_ms: intent.latency_ms || null, error: intent.error || "", response_status: intent.response && intent.response.status || "", accepted: intent.response ? intent.response.accepted : null,
    update_id: intent.response && intent.response.update_id || "", source: intent.response && intent.response.source || "", backend_handle: intent.response && intent.response.latency_ms ? intent.response.latency_ms.backend_handle : null, compile: intent.compile || null }));
  return { ...u, intents, transcripts: u.transcripts.filter(x => x.final || x.started) };
}

async function coldStarts(n) {
  // time-to-ready of start() with / without a prefetched secret (the next arming of voice by the user)
  for (let i = 0; i < n; i += 1) {
    const rec = { kind: "cold", label: job.label, i, prefetch: Boolean(job.prefetch) };
    mintInfo = { status: null, ms: null, attempts: 0 };
    const c = makeClient();
    client = c;
    if (job.prefetch && typeof c.prefetch === "function") { const p0 = now(); await c.prefetch(); rec.prefetch_ms = Math.round(now() - p0); }
    const t0 = now();
    await c.start();
    const ready = await waitReady(30000);
    rec.start_to_ready_ms = ready ? Math.round(now() - t0) : null;
    rec.mint = { ...mintInfo }; rec.auth = auth; rec.load = require("node:os").loadavg();
    emit(rec);
    c.stop();
    await sleep(800);
  }
}

async function postsMode() {
  // client-side rule-path costs: matchFastRule time is measured in fast_rule_timer.js; here the POST legs (undici keep-alive, loopback)
  const results = [];
  const run = async (name, pathname, bodyObj, n) => {
    for (let i = 0; i < n; i += 1) {
      const t0 = now();
      let res = null, err = "";
      try { res = await requestJsonPlain(pathname, bodyObj); } catch (e) { err = String(e.message || e); }
      const t1 = now();
      const updateId = res && res.update_id;
      let compile = null;
      if (updateId) { const c = await waitCompile(updateId, 4000); compile = c ? c.t - t1 : null; }
      results.push({ kind: "post", name, i, ms: t1 - t0, backend_handle: res && res.latency_ms ? res.latency_ms.backend_handle : null, status: res && res.status || "", accepted: res ? res.accepted : null, compile_after_return_ms: compile, error: err, load: require("node:os").loadavg()[0] });
      await sleep(60);
    }
  };
  await run("emergency_button", "/api/emergency", { action: "retreat" }, job.n || 30);
  await run("intent_fast_rule", "/api/realtime/intent", { transcript: "후퇴", fast: true, path: "rule", latency_ms: {} }, job.n || 30);
  await run("intent_fast_miss", "/api/realtime/intent", { transcript: "마린 두 기는 정찰해", fast: true, path: "rule", latency_ms: {} }, job.n || 30);
  await run("intent_fast_stim", "/api/realtime/intent", { transcript: "스팀팩 써", fast: true, path: "rule", latency_ms: {} }, job.n || 30);
  for (const r of results) emit({ ...r, label: job.label });
}
async function requestJsonPlain(pathname, bodyObj) {
  const response = await fetch(new URL(pathname, job.base), { method: "POST", body: JSON.stringify(bodyObj), redirect: "error", headers: { accept: "application/json", "content-type": "application/json" } });
  return response.json();
}

async function main() {
  emit({ kind: "meta", label: job.label, job: { ...job, rows: undefined, nrows: job.rows.length }, node: process.version, loadavg: require("node:os").loadavg(), steal_start: stealProbe(), start: new Date().toISOString() });
  if (job.mode === "posts") { await postsMode(); emit({ kind: "end", label: job.label }); await new Promise(resolve => out.end(resolve)); return; }
  if (job.coldN) { await coldStarts(job.coldN); emit({ kind: "end", label: job.label }); await new Promise(resolve => out.end(resolve)); return; }
  client = makeClient();
  const t0 = now();
  await client.start();
  const ready = await waitReady(40000);
  emit({ kind: "ready", label: job.label, ok: ready, ms: Math.round(now() - t0), mint: mintInfo, auth, startupLogs, state: client.snapshot().state });
  if (!ready) { emit({ kind: "end", label: job.label, failed: "not ready" }); await new Promise(resolve => out.end(resolve)); client.stop(); return; }
  await sleep(600);
  // rep-major order so every row sees each audio variant; shuffle rows with a fixed seed per rep
  const rows = job.rows.slice();
  let seed = 12345;
  const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
  for (let rep = 0; rep < job.reps; rep += 1) {
    const order = rows.slice().sort(() => rnd() - 0.5);
    for (const row of order) {
      if (!client.snapshot().active || client.snapshot().state === "error" || client.snapshot().state === "connecting") { // recover like the app would (reconnect backoff happens inside)
        const ok = await waitReady(15000);
        if (!ok) { await client.start(); await waitReady(20000); }
      }
      const u = await runUtterance(row, rep, rep);
      emit(sanitize(u));
    }
  }
  emit({ kind: "end", label: job.label, loadavg: require("node:os").loadavg(), end: new Date().toISOString() });
  client.stop();
  await new Promise(resolve => out.end(resolve));
}
main().then(() => process.exit(0), error => { emit({ kind: "fatal", label: job.label, error: String(error && error.stack || error).slice(0, 600) }); out.end(() => process.exit(1)); });
