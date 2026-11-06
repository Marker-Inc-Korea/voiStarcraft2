"use strict";
// Realtime voice client (Electron main process). The renderer never touches the network or the
// ephemeral secret: it only streams pcm16 chunks in and receives state/transcript/intent events.
const { createVoiceMachine, TOOL_NAME } = require("./voice_state.js");
const { validateRealtimeSession, validateIntentRequest } = require("./protocol.js");
const { matchFastRule } = require("./fast_rules.js");

const REALTIME_UA = "curl/8.7.1"; // the Cloudflare-fronted proxy rejects the default Node UA (error 1010)
const SECRET_MIN_REMAINING_S = 30;
const WARM_REFRESH_LEAD_S = 60;
const SESSION_ROTATE_MS = 55 * 60 * 1000; // Realtime sessions are capped at 60 minutes.
const TRANSCRIPT_GRACE_MS = 350;
const ACTING_HOLD_MS = 1400;
const PING_MS = 20000;
const BACKOFF_MS = [500, 1000, 2000, 4000, 8000];
const MAX_QUEUED_SEND_BYTES = 4 * 1024 * 1024;

// `prefs` = settings voiceView ({silenceMs, threshold, ptt, language}); env stays as the developer override.
function defaultSessionUpdate(env, prefs = {}) {
  const envSilence = Number(env.VOI_VAD_SILENCE_MS);
  const silence = Number.isFinite(envSilence) && envSilence >= 150 && envSilence <= 2000 ? envSilence : (prefs.silenceMs >= 150 && prefs.silenceMs <= 2000 ? prefs.silenceMs : 450);
  const threshold = typeof prefs.threshold === "number" && prefs.threshold >= 0.1 && prefs.threshold <= 0.9 ? prefs.threshold : 0.5;
  const transcription = env.VOI_TRANSCRIBE_MODEL === "off" ? null : { model: env.VOI_TRANSCRIBE_MODEL || "gpt-4o-mini-transcribe" };
  if (transcription && (prefs.language === "ko" || prefs.language === "en")) transcription.language = prefs.language;
  const input = {
    format: { type: "audio/pcm", rate: 24000 },
    // Push-to-talk: no server VAD, the client commits/creates the response itself on key release.
    turn_detection: prefs.ptt ? null : { type: "server_vad", threshold, prefix_padding_ms: 300, silence_duration_ms: silence, create_response: true, interrupt_response: false },
  };
  if (transcription) input.transcription = transcription;
  return { type: "session.update", session: { type: "realtime", audio: { input } } };
}

function createVoiceClient(options) {
  const {
    requestJson, WebSocketImpl, onState = () => {}, onTranscript = () => {}, onIntent = () => {}, onLog = () => {},
    now = Date.now, env = process.env, getPrefs = () => ({}), onSession = () => {}, setTimer = setTimeout, clearTimer = clearTimeout, setIntervalFn = setInterval, clearIntervalFn = clearInterval,
  } = options;
  if (typeof requestJson !== "function" || typeof WebSocketImpl !== "function") throw new TypeError("voice client needs requestJson and WebSocketImpl");
  const machine = createVoiceMachine({ now, onChange: snapshot => onState(snapshot) });
  let ws = null;
  let generation = 0; // bumps on every (re)connect so late events from old sockets are ignored
  let session = null; // validated session + fetchedAt
  let pingTimer = null;
  let reconnectTimer = null;
  let rotateTimer = null;
  let actingTimer = null;
  let attempts = 0;
  let eventCounter = 0;
  const benignEventIds = new Set();
  let sessionReadyTimer = null;
  let warmTimer = null;
  const warmPool = () => getPrefs().warmPool !== false; // pre-minted secret on by default (measured: docs/voice-intent-benchmark.md round 3)
  let pttHeld = false;
  let pttItem = 0;

  function send(payload) {
    if (!ws || ws.readyState !== 1) return false;
    if (ws.bufferedAmount > MAX_QUEUED_SEND_BYTES) return false; // drop audio rather than grow without bound
    eventCounter += 1;
    const event_id = `voi_${eventCounter}`;
    ws.send(JSON.stringify({ ...payload, event_id }));
    return event_id;
  }
  function clearSocketTimers() {
    if (pingTimer) { clearIntervalFn(pingTimer); pingTimer = null; }
    if (sessionReadyTimer) { clearTimer(sessionReadyTimer); sessionReadyTimer = null; }
    if (rotateTimer) { clearTimer(rotateTimer); rotateTimer = null; }
  }
  function teardownSocket() {
    generation += 1;
    clearSocketTimers();
    const old = ws; ws = null;
    if (old) { try { old.removeAllListeners(); old.on("error", () => {}); old.close(); } catch { /* already closed */ } }
  }
  function secretUsable() {
    return session && (session.expiresAt === null || session.expiresAt - now() / 1000 > SECRET_MIN_REMAINING_S);
  }
  async function ensureSession(force) {
    if (!force && secretUsable()) return session;
    const payload = await requestJson("/api/realtime/session", { method: "POST", body: "{}" });
    session = { ...validateRealtimeSession(payload), fetchedAt: now() };
    onSession({ model: session.model, tierHint: session.tierHint });
    scheduleWarmRefresh();
    return session;
  }
  // Warm pool (prefs.warmPool, default on): keep a pre-minted client secret so arming voice skips the mint round trip. Secrets live ~10 min
  // and can open several sockets; re-mint WARM_REFRESH_LEAD_S before expiry while voice is idle.
  function scheduleWarmRefresh() {
    if (!warmPool()) return;
    if (warmTimer) { clearTimer(warmTimer); warmTimer = null; }
    if (!session || session.expiresAt === null) return;
    // Cap at 30 min: setTimeout delays above 2^31-1 ms fire immediately, and unref(): the pool must never keep the process alive.
    const delay = Math.min(30 * 60 * 1000, Math.max(5000, (session.expiresAt - now() / 1000 - WARM_REFRESH_LEAD_S) * 1000));
    warmTimer = setTimer(() => {
      warmTimer = null;
      if (machine.snapshot().active) { scheduleWarmRefresh(); return; } // the live socket outlives its secret; reconnects mint on demand
      prefetch().catch(() => {});
    }, delay);
    if (warmTimer && typeof warmTimer.unref === "function") warmTimer.unref();
  }
  async function prefetch() {
    if (!warmPool()) return false;
    try { await ensureSession(true); return true; } catch (error) { onLog(`prefetch failed: ${String(error && error.message || error).slice(0, 160)}`); return false; }
  }
  function scheduleReconnect(reason) {
    if (!machine.snapshot().active) return;
    if (reconnectTimer) return;
    const delay = BACKOFF_MS[Math.min(attempts, BACKOFF_MS.length - 1)];
    attempts += 1;
    machine.setConnection("connecting");
    onLog(`reconnect in ${delay}ms: ${reason}`);
    reconnectTimer = setTimer(() => { reconnectTimer = null; connect().catch(() => {}); }, delay);
  }
  async function connect() {
    if (!machine.snapshot().active) return;
    teardownSocket();
    machine.setConnection("connecting");
    const mine = generation;
    let current;
    try { current = await ensureSession(attempts > 0 && !secretUsable()); } catch (error) {
      if (mine !== generation || !machine.snapshot().active) return;
      machine.fail(`음성 세션을 만들지 못했습니다: ${error.message}`);
      scheduleReconnect(error.message);
      return;
    }
    if (mine !== generation || !machine.snapshot().active) return;
    const socket = new WebSocketImpl(current.wsUrl, { headers: { Authorization: `Bearer ${current.clientSecret}`, "User-Agent": REALTIME_UA }, handshakeTimeout: 10000, perMessageDeflate: false });
    ws = socket;
    const stale = () => mine !== generation || ws !== socket;
    socket.on("open", () => {
      if (stale()) return;
      const id = send(defaultSessionUpdate(env, getPrefs()));
      pingTimer = setIntervalFn(() => { try { if (socket.readyState === 1) socket.ping(); } catch { /* ignore */ } }, PING_MS);
      sessionReadyTimer = setTimer(() => { if (!stale()) { machine.fail("Realtime 세션 초기화 시간 초과"); scheduleReconnect("session.update timeout"); } }, 15000);
      if (!id) { machine.fail("Realtime 소켓 전송 실패"); }
    });
    socket.on("message", data => { if (!stale()) handleMessage(String(data), mine); });
    socket.on("unexpected-response", (_req, res) => {
      if (stale()) return;
      machine.fail(`Realtime 연결 거부 (HTTP ${res && res.statusCode})`);
      if (res && (res.statusCode === 401 || res.statusCode === 403)) session = null;
      scheduleReconnect(`http ${res && res.statusCode}`);
    });
    socket.on("error", error => { if (!stale()) onLog(`ws error: ${error.message}`); });
    socket.on("close", (code, reason) => {
      if (stale()) return;
      clearSocketTimers();
      ws = null;
      if (!machine.snapshot().active) return;
      machine.fail(`Realtime 연결이 끊어졌습니다 (${code}${reason && String(reason) ? ` ${String(reason).slice(0, 80)}` : ""})`);
      session = secretUsable() ? session : null;
      scheduleReconnect(`closed ${code}`);
    });
  }

  function markReady(mine) {
    if (sessionReadyTimer) { clearTimer(sessionReadyTimer); sessionReadyTimer = null; }
    attempts = 0;
    machine.setConnection("ready");
    rotateTimer = setTimer(function rotate() {
      if (mine !== generation) return;
      if (machine.isQuiet()) { onLog("rotating realtime session"); session = null; connect().catch(() => {}); } else rotateTimer = setTimer(rotate, 5000);
    }, SESSION_ROTATE_MS);
  }

  function handleMessage(text, mine) {
    let event;
    try { event = JSON.parse(text); } catch { return; }
    if (!event || typeof event.type !== "string") return;
    if (event.type === "session.updated") { markReady(mine); return; }
    if (event.type === "session.created") return;
    if (event.type === "error" && event.event_id && benignEventIds.delete(event.event_id)) return;
    if (event.type === "error" && event.error && event.error.event_id && benignEventIds.delete(event.error.event_id)) return;
    if (event.type === "response.done") pruneConversation(event);
    runEffects(machine.handle(event), mine);
  }
  // The model must decide every utterance from scratch ("exactly what the user said"), so processed
  // conversation items are removed instead of letting earlier commands bias later ones.
  function pruneConversation(event) {
    const response = event.response || {};
    const ids = [];
    for (const item of Array.isArray(response.output) ? response.output : []) if (item && typeof item.id === "string") ids.push(item.id);
    for (const id of ids) {
      const eventId = send({ type: "conversation.item.delete", item_id: id });
      if (eventId) { benignEventIds.add(eventId); if (benignEventIds.size > 64) benignEventIds.delete(benignEventIds.values().next().value); }
    }
  }

  function runEffects(effects, mine) { for (const effect of effects) runEffect(effect, mine); }
  function runEffect(effect, mine) {
    switch (effect.type) {
      case "speech_started": onTranscript({ turn: effect.turn.id, text: "", partial: true, final: false, started: true }); break;
      case "speech_stopped": if (effect.turn.transcript) tryRule(effect.turn.id, mine).catch(error => onLog(`rule failed: ${error.message}`)); break;
      case "transcript":
        onTranscript({ turn: effect.turn.id, text: effect.turn.transcript || effect.turn.partial, partial: effect.partial, final: !effect.partial });
        // Rule-first: act on the FINAL transcript (a delta prefix such as "후퇴" can still become "후퇴하지 마").
        if (!effect.partial && effect.turn.transcript) tryRule(effect.turn.id, mine).catch(error => onLog(`rule failed: ${error.message}`));
        break;
      case "cancel_response": cancelResponse(effect.responseId); break;
      case "tool_call_ignored": onLog(`model tool call ignored for turn ${effect.turn && effect.turn.id}: ${effect.reason}`); break;
      case "tool_call": submitIntent(effect, mine).catch(error => onLog(`intent failed: ${error.message}`)); break;
      case "no_command": onIntent({ turn: effect.turn.id, ok: false, kind: "no_command", error: "명령으로 인식되지 않았습니다", transcript: effect.turn.transcript }); break;
      case "bad_tool_arguments": onIntent({ turn: effect.turn ? effect.turn.id : 0, ok: false, kind: "bad_arguments", error: "음성 명령 인자를 해석하지 못했습니다" }); break;
      case "unexpected_tool": onIntent({ turn: effect.turn ? effect.turn.id : 0, ok: false, kind: "unexpected_tool", error: `허용되지 않은 도구 호출: ${effect.name.slice(0, 40)}` }); break;
      default: break;
    }
  }

  function cancelResponse(responseId) {
    const id = send(responseId ? { type: "response.cancel", response_id: responseId } : { type: "response.cancel" });
    if (id) { benignEventIds.add(id); if (benignEventIds.size > 64) benignEventIds.delete(benignEventIds.values().next().value); }
  }

  // Deterministic rule path: the transcript alone decides for the (<= 5) promoted families, so the intent is posted right
  // away (`fast: true`) instead of after the model's tool call; the model response is then cancelled and its later
  // tool call ignored (dedupe by turn/response id in voice_state). A miss or a failed request releases the turn to the model.
  async function tryRule(turnId, mine) {
    if (getPrefs().fastRules === false) return;
    const snapshot = machine.getTurn(turnId);
    if (!snapshot || !snapshot.transcript) return;
    const hit = matchFastRule(snapshot.transcript);
    if (!hit) return;
    const claim = machine.beginRule(turnId);
    if (!claim) return;
    if (actingTimer) { clearTimer(actingTimer); actingTimer = null; }
    const decidedAt = now();
    const speechEnd = claim.speechStoppedAt || decidedAt;
    const latency = { speech_end_to_decision: Math.max(0, decidedAt - speechEnd), speech_ms: snapshot.speechMs };
    let handled = false;
    let ok = false;
    let response = null;
    let failure = "";
    try {
      const body = validateIntentRequest({ transcript: snapshot.transcript, fast: true, path: "rule", latency_ms: { ...latency, decision_to_publish: null } });
      response = await requestJson("/api/realtime/intent", { method: "POST", body: JSON.stringify(body) });
      latency.decision_to_publish = now() - decidedAt;
      latency.speech_end_to_tool_call = latency.speech_end_to_decision; latency.tool_call_to_publish = latency.decision_to_publish; // the HUD latency readout keeps its keys
      handled = Boolean(response) && response.status !== "fast_miss";
      ok = handled && response.accepted !== false;
    } catch (error) {
      failure = String(error && error.message || error).slice(0, 240);
      latency.decision_to_publish = now() - decidedAt;
    }
    const outcome = machine.finishRule(turnId, { handled, ok });
    if (handled) {
      if (outcome.responseId) cancelResponse(outcome.responseId);
      onIntent({ turn: turnId, ok, kind: "submitted", path: "rule", family: hit.family, transcript: snapshot.transcript, tier: response && response.tier || "", response, latency_ms: latency, tool_arguments: null });
      if (ok) actingTimer = setTimer(() => { actingTimer = null; machine.setHoldActing(false); }, ACTING_HOLD_MS);
      return;
    }
    if (failure) onLog(`rule request failed, falling back to the model: ${failure}`);
    if (outcome.deferred) runEffect(outcome.deferred, mine);
  }

  async function submitIntent(effect, mine) {
    const turnId = effect.turnId;
    if (actingTimer) { clearTimer(actingTimer); actingTimer = null; }
    const startedAt = now();
    let transcript = effect.turn ? effect.turn.transcript : "";
    if (!transcript && machine.getTurn(turnId)) {
      await new Promise(resolve => setTimer(resolve, TRANSCRIPT_GRACE_MS));
      const turn = machine.getTurn(turnId);
      transcript = turn ? turn.transcript || turn.partial : "";
    }
    const latency = { speech_end_to_tool_call: effect.speechEndToToolCall, speech_end_to_decision: effect.speechEndToToolCall, speech_ms: effect.speechMs, transcript_wait: now() - startedAt };
    let ok = false;
    try {
      const body = validateIntentRequest({ tool_arguments: effect.args, transcript, path: "model", latency_ms: { ...latency, tool_call_to_publish: null } });
      const publishStart = now();
      const response = await requestJson("/api/realtime/intent", { method: "POST", body: JSON.stringify(body) });
      latency.tool_call_to_publish = now() - startedAt;
      latency.publish_request = now() - publishStart;
      latency.decision_to_publish = latency.publish_request;
      ok = Boolean(response && response.accepted !== false);
      onIntent({ turn: turnId, ok, kind: "submitted", path: "model", transcript, tier: response && response.tier || "", response, latency_ms: latency, tool_arguments: effect.args });
    } catch (error) {
      latency.tool_call_to_publish = now() - startedAt;
      onIntent({ turn: turnId, ok: false, kind: "failed", transcript, error: String(error && error.message || error).slice(0, 240), latency_ms: latency, tool_arguments: effect.args });
    } finally {
      if (mine === generation || !ws) {
        machine.intentFinished(turnId, ok);
        if (ok) actingTimer = setTimer(() => { actingTimer = null; machine.setHoldActing(false); }, ACTING_HOLD_MS);
      } else machine.intentFinished(turnId, ok);
    }
  }

  return {
    machine,
    // Pre-mint the client secret (call once at app start; refreshes itself WARM_REFRESH_LEAD_S before expiry while voice is idle).
    prefetch,
    async start() {
      if (machine.snapshot().active) return machine.snapshot();
      attempts = 0;
      machine.setActive(true);
      await connect();
      return machine.snapshot();
    },
    // Re-send the session config after a settings change (VAD/sensitivity/language/PTT take effect live).
    reconfigure() { if (ws && ws.readyState === 1 && machine.snapshot().state !== "connecting") send(defaultSessionUpdate(env, getPrefs())); },
    stop() {
      pttHeld = false;
      if (reconnectTimer) { clearTimer(reconnectTimer); reconnectTimer = null; }
      if (actingTimer) { clearTimer(actingTimer); actingTimer = null; }
      teardownSocket();
      if (!(warmPool() && secretUsable())) session = null; // warm pool: the unexpired secret serves the next arming without a mint round trip
      return machine.setActive(false);
    },
    // Called with a validated pcm16 Buffer; silently dropped unless the socket is ready.
    pushAudio(buffer) {
      if (getPrefs().ptt && !pttHeld) return false; // push-to-talk: only stream while the key is held
      const snap = machine.snapshot();
      if (!snap.active || !ws || ws.readyState !== 1 || snap.state === "connecting" || snap.state === "error") return false;
      return Boolean(send({ type: "input_audio_buffer.append", audio: buffer.toString("base64") }));
    },
    // Push-to-talk: no server VAD, so the client frames the turn. Down = start (clear stale audio), up = commit + respond.
    pttSet(down) {
      const snap = machine.snapshot();
      if (!getPrefs().ptt || !snap.active || !ws || ws.readyState !== 1 || snap.state === "connecting" || snap.state === "error") return false;
      if (down === pttHeld) return true;
      pttHeld = down;
      pttItem += 1;
      const item_id = `ptt_${pttItem}`;
      if (down) {
        send({ type: "input_audio_buffer.clear" });
        runEffects(machine.handle({ type: "input_audio_buffer.speech_started", item_id }), generation);
      } else {
        runEffects(machine.handle({ type: "input_audio_buffer.speech_stopped", item_id: `ptt_${pttItem - 1}` }), generation);
        send({ type: "input_audio_buffer.commit" });
        send({ type: "response.create" });
      }
      return true;
    },
    micFailed(reason) { machine.fail(`마이크: ${reason}`); },
    snapshot: () => machine.snapshot(),
    TOOL_NAME,
  };
}
module.exports = { createVoiceClient, defaultSessionUpdate, REALTIME_UA };
