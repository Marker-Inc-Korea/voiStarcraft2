"use strict";
// Realtime voice client (Electron main process). The renderer never touches the network or the
// ephemeral secret: it only streams pcm16 chunks in and receives state/transcript/intent events.
const { createVoiceMachine, TOOL_NAME } = require("./voice_state.js");
const { validateRealtimeSession, validateIntentRequest } = require("./protocol.js");

const REALTIME_UA = "curl/8.7.1"; // the Cloudflare-fronted proxy rejects the default Node UA (error 1010)
const SECRET_MIN_REMAINING_S = 30;
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
    return session;
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
      sessionReadyTimer = setTimer(() => { if (!stale()) { machine.fail("Realtime 세션 초기화 시간 초과"); scheduleReconnect("session.update timeout"); } }, 8000);
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
      case "transcript": onTranscript({ turn: effect.turn.id, text: effect.turn.transcript || effect.turn.partial, partial: effect.partial, final: !effect.partial }); break;
      case "tool_call": submitIntent(effect, mine).catch(error => onLog(`intent failed: ${error.message}`)); break;
      case "no_command": onIntent({ turn: effect.turn.id, ok: false, kind: "no_command", error: "명령으로 인식되지 않았습니다", transcript: effect.turn.transcript }); break;
      case "bad_tool_arguments": onIntent({ turn: effect.turn ? effect.turn.id : 0, ok: false, kind: "bad_arguments", error: "음성 명령 인자를 해석하지 못했습니다" }); break;
      case "unexpected_tool": onIntent({ turn: effect.turn ? effect.turn.id : 0, ok: false, kind: "unexpected_tool", error: `허용되지 않은 도구 호출: ${effect.name.slice(0, 40)}` }); break;
      default: break;
    }
  }

  async function submitIntent(effect, mine) {
    const turnId = effect.turnId;
    machine.intentStarted();
    if (actingTimer) { clearTimer(actingTimer); actingTimer = null; }
    const startedAt = now();
    let transcript = effect.turn ? effect.turn.transcript : "";
    if (!transcript && machine.getTurn(turnId)) {
      await new Promise(resolve => setTimer(resolve, TRANSCRIPT_GRACE_MS));
      const turn = machine.getTurn(turnId);
      transcript = turn ? turn.transcript || turn.partial : "";
    }
    const latency = { speech_end_to_tool_call: effect.speechEndToToolCall, speech_ms: effect.speechMs, transcript_wait: now() - startedAt };
    let ok = false;
    try {
      const body = validateIntentRequest({ tool_arguments: effect.args, transcript, latency_ms: { ...latency, tool_call_to_publish: null } });
      const publishStart = now();
      const response = await requestJson("/api/realtime/intent", { method: "POST", body: JSON.stringify(body) });
      latency.tool_call_to_publish = now() - startedAt;
      latency.publish_request = now() - publishStart;
      ok = Boolean(response && response.accepted !== false);
      onIntent({ turn: turnId, ok, kind: "submitted", transcript, tier: response && response.tier || "", response, latency_ms: latency, tool_arguments: effect.args });
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
      session = null;
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
