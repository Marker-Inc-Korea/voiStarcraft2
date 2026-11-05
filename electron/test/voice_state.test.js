const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { createVoiceMachine } = require("../voice_state.js");
const { createVoiceClient, defaultSessionUpdate } = require("../voice.js");

function machine() {
  let t = 1000; const states = [];
  const m = createVoiceMachine({ now: () => t, onChange: snap => states.push(snap.state) });
  return { m, states, tick: ms => { t += ms; } };
}
const toolDone = args => ({ type: "response.function_call_arguments.done", name: "submit_command", call_id: "c1", arguments: JSON.stringify(args) });

test("state machine: idle -> connecting -> idle(ready) -> listening -> thinking -> tool call -> acting -> idle", () => {
  const { m, states, tick } = machine();
  assert.equal(m.snapshot().state, "idle");
  m.setActive(true); assert.equal(m.snapshot().state, "connecting");
  m.setConnection("ready"); assert.equal(m.snapshot().state, "idle"); assert.equal(m.snapshot().active, true);
  m.handle({ type: "input_audio_buffer.speech_started", item_id: "i1" }); assert.equal(m.snapshot().state, "listening");
  tick(1500);
  m.handle({ type: "input_audio_buffer.speech_stopped", item_id: "i1" }); assert.equal(m.snapshot().state, "thinking");
  m.handle({ type: "conversation.item.input_audio_transcription.completed", item_id: "i1", transcript: " 마린 정찰 " });
  tick(700);
  const effects = m.handle(toolDone({ command_layer: "operation", goal: "g" }));
  const call = effects.find(e => e.type === "tool_call");
  assert.ok(call); assert.equal(call.speechEndToToolCall, 700); assert.equal(call.speechMs, 1500); assert.equal(call.args.goal, "g");
  assert.equal(m.snapshot().turn.transcript, "마린 정찰");
  assert.equal(m.snapshot().state, "acting"); // straight from thinking, no idle flash
  m.intentFinished(call.turnId, true); assert.equal(m.snapshot().state, "acting"); // hold window
  m.setHoldActing(false); assert.equal(m.snapshot().state, "idle");
  assert.deepEqual([...new Set(states)], ["connecting", "idle", "listening", "thinking", "acting"]);
  assert.ok(!states.slice(states.indexOf("thinking")).slice(0, states.slice(states.indexOf("thinking")).indexOf("acting")).includes("idle"), "no idle between thinking and acting");
});
test("transcript deltas accumulate then final replaces them", () => {
  const { m } = machine(); m.setActive(true); m.setConnection("ready");
  m.handle({ type: "input_audio_buffer.speech_started", item_id: "i1" });
  let fx = m.handle({ type: "conversation.item.input_audio_transcription.delta", item_id: "i1", delta: "마린 " });
  fx = m.handle({ type: "conversation.item.input_audio_transcription.delta", item_id: "i1", delta: "두 기" });
  assert.equal(fx[0].turn.partial, "마린 두 기"); assert.equal(fx[0].partial, true);
  fx = m.handle({ type: "conversation.item.input_audio_transcription.completed", item_id: "i1", transcript: "마린 두 기" });
  assert.equal(fx[0].turn.transcript, "마린 두 기"); assert.equal(fx[0].turn.partial, ""); assert.equal(fx[0].partial, false);
});
test("events are ignored while voice is not armed", () => {
  const { m } = machine();
  assert.deepEqual(m.handle({ type: "input_audio_buffer.speech_started", item_id: "x" }), []);
  assert.equal(m.snapshot().state, "idle"); assert.equal(m.snapshot().active, false);
});
test("server error -> error state with reason; next speech clears it", () => {
  const { m } = machine(); m.setActive(true); m.setConnection("ready");
  m.handle({ type: "error", error: { code: "rate_limit_exceeded", message: "slow down" } });
  assert.equal(m.snapshot().state, "error"); assert.equal(m.snapshot().reason, "slow down");
  m.handle({ type: "input_audio_buffer.speech_started", item_id: "n" });
  assert.equal(m.snapshot().state, "listening"); assert.equal(m.snapshot().reason, "");
});
test("benign errors are ignored", () => {
  const { m } = machine(); m.setActive(true); m.setConnection("ready");
  m.handle({ type: "error", error: { code: "input_audio_buffer_commit_empty", message: "empty" } });
  assert.equal(m.snapshot().state, "idle");
});
test("failed response -> error with reason", () => {
  const { m } = machine(); m.setActive(true); m.setConnection("ready");
  m.handle({ type: "input_audio_buffer.speech_started", item_id: "a" });
  m.handle({ type: "input_audio_buffer.speech_stopped", item_id: "a" });
  m.handle({ type: "response.done", response: { status: "failed", status_details: { error: { message: "quota" } } } });
  assert.equal(m.snapshot().state, "error"); assert.match(m.snapshot().reason, /quota/);
});
test("response without a tool call becomes a no_command effect and returns to idle", () => {
  const { m } = machine(); m.setActive(true); m.setConnection("ready");
  m.handle({ type: "input_audio_buffer.speech_started", item_id: "a" });
  m.handle({ type: "input_audio_buffer.speech_stopped", item_id: "a" });
  const fx = m.handle({ type: "response.done", response: { status: "completed", output: [{ type: "message" }] } });
  assert.equal(fx[0].type, "no_command"); assert.equal(m.snapshot().state, "idle");
});
test("tool call with invalid JSON, wrong tool, or non-object is not forwarded", () => {
  const { m } = machine(); m.setActive(true); m.setConnection("ready");
  for (const ev of [{ type: "response.function_call_arguments.done", name: "submit_command", arguments: "{oops" }, { type: "response.function_call_arguments.done", name: "submit_command", arguments: "[1]" }, { type: "response.function_call_arguments.done", name: "rm_rf", arguments: "{}" }]) {
    m.handle({ type: "input_audio_buffer.speech_started", item_id: "a" }); m.handle({ type: "input_audio_buffer.speech_stopped", item_id: "a" });
    assert.equal(m.handle(ev).some(e => e.type === "tool_call"), false);
  }
});
test("new speech while thinking supersedes the earlier turn state", () => {
  const { m } = machine(); m.setActive(true); m.setConnection("ready");
  m.handle({ type: "input_audio_buffer.speech_started", item_id: "a" });
  m.handle({ type: "input_audio_buffer.speech_stopped", item_id: "a" });
  assert.equal(m.snapshot().state, "thinking");
  m.handle({ type: "input_audio_buffer.speech_started", item_id: "b" });
  assert.equal(m.snapshot().state, "listening");
});
test("losing the socket drops pending turns", () => {
  const { m } = machine(); m.setActive(true); m.setConnection("ready");
  m.handle({ type: "input_audio_buffer.speech_started", item_id: "a" });
  m.setConnection("connecting"); assert.equal(m.snapshot().state, "connecting");
  m.setConnection("ready"); assert.equal(m.snapshot().state, "idle");
});

// ---------- full client against a fake ws ----------
class FakeWs extends EventEmitter {
  static last = null;
  constructor(url, options) { super(); this.url = url; this.options = options; this.readyState = 0; this.sent = []; this.bufferedAmount = 0; FakeWs.last = this; setImmediate(() => { this.readyState = 1; this.emit("open"); }); }
  send(data) { this.sent.push(JSON.parse(data)); }
  ping() {} close() { this.readyState = 3; }
  server(event) { this.emit("message", JSON.stringify(event)); }
}
const flush = () => new Promise(resolve => setImmediate(() => setImmediate(resolve)));
function client(overrides = {}) {
  const log = { states: [], transcripts: [], intents: [], requests: [] };
  let t = 5000;
  const c = createVoiceClient({
    requestJson: async (pathname, options) => {
      log.requests.push({ pathname, body: options && options.body ? JSON.parse(options.body) : null });
      if (pathname === "/api/realtime/session") return { client_secret: "ek_testsecret", expires_at: 4e9, ws_url: "wss://rt.example/v1/realtime?model=m", model: "m", tools: [] };
      if (pathname === "/api/realtime/intent") return { accepted: true, update_id: "u1", tier: "operation" };
      throw new Error("unexpected " + pathname);
    },
    WebSocketImpl: FakeWs, now: () => t, env: {},
    onState: s => log.states.push(s.state), onTranscript: x => log.transcripts.push(x), onIntent: x => log.intents.push(x),
    setTimer: (fn, ms) => (ms >= 8000 ? 0 : setTimeout(fn, Math.min(ms, 5))), clearTimer: handle => { if (handle) clearTimeout(handle); }, ...overrides,
  });
  return { c, log, advance: ms => { t += ms; } };
}
test("client: secret via /api/realtime/session, bearer + curl UA headers, server_vad session.update, then ready", async () => {
  const { c, log } = client();
  await c.start(); await flush();
  const ws = FakeWs.last;
  assert.equal(ws.url, "wss://rt.example/v1/realtime?model=m");
  assert.equal(ws.options.headers.Authorization, `${"Bear" + "er"} ek_testsecret`);
  assert.equal(ws.options.headers["User-Agent"], "curl/8.7.1");
  const update = ws.sent.find(e => e.type === "session.update");
  assert.equal(update.session.audio.input.turn_detection.type, "server_vad");
  assert.equal(update.session.audio.input.turn_detection.create_response, true);
  assert.equal(update.session.audio.input.format.rate, 24000);
  assert.equal(c.snapshot().state, "connecting");
  ws.server({ type: "session.updated" });
  assert.equal(c.snapshot().state, "idle");
  assert.equal(log.requests[0].pathname, "/api/realtime/session");
  c.stop();
});
test("client: audio chunks become input_audio_buffer.append (base64) only when ready", async () => {
  const { c } = client();
  assert.equal(c.pushAudio(Buffer.alloc(8)), false);
  await c.start(); await flush();
  const ws = FakeWs.last;
  assert.equal(c.pushAudio(Buffer.alloc(8)), false); // not ready yet
  ws.server({ type: "session.updated" });
  assert.ok(c.pushAudio(Buffer.from([1, 0, 2, 0])));
  const append = ws.sent.filter(e => e.type === "input_audio_buffer.append");
  assert.equal(append.length, 1); assert.equal(append[0].audio, Buffer.from([1, 0, 2, 0]).toString("base64"));
  c.stop();
});
test("client: full turn posts intent with tool args, transcript and latency; states follow the spec", async () => {
  const { c, log, advance } = client();
  await c.start(); await flush();
  const ws = FakeWs.last; ws.server({ type: "session.updated" });
  ws.server({ type: "input_audio_buffer.speech_started", item_id: "i1" });
  assert.equal(c.snapshot().state, "listening");
  advance(1200);
  ws.server({ type: "input_audio_buffer.speech_stopped", item_id: "i1" });
  assert.equal(c.snapshot().state, "thinking");
  ws.server({ type: "conversation.item.input_audio_transcription.completed", item_id: "i1", transcript: "마린 정찰" });
  advance(640);
  ws.server(toolDone({ command_layer: "operation", goal: "정찰", operations: [{ goal: "a" }, { goal: "b" }] }));
  await flush();
  const post = log.requests.find(r => r.pathname === "/api/realtime/intent");
  assert.ok(post, "intent posted");
  assert.equal(post.body.transcript, "마린 정찰");
  assert.equal(post.body.tool_arguments.operations.length, 2);
  assert.equal(post.body.latency_ms.speech_end_to_tool_call, 640);
  assert.equal(post.body.latency_ms.speech_ms, 1200);
  const done = log.intents.find(i => i.kind === "submitted");
  assert.equal(done.ok, true); assert.equal(done.tier, "operation"); assert.equal(typeof done.latency_ms.tool_call_to_publish, "number");
  assert.ok(log.states.includes("listening") && log.states.includes("thinking") && log.states.includes("acting"));
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(c.snapshot().state, "idle");
  c.stop();
});
test("client: backend refusing the intent surfaces ok:false with the reason, no success", async () => {
  const { c, log } = client({ requestJson: async pathname => { if (pathname === "/api/realtime/session") return { client_secret: "ek_testsecret", expires_at: 4e9, ws_url: "wss://rt.example/x" }; throw new Error("direct_control_conflict"); } });
  await c.start(); await flush();
  const ws = FakeWs.last; ws.server({ type: "session.updated" });
  ws.server({ type: "input_audio_buffer.speech_started", item_id: "i1" }); ws.server({ type: "input_audio_buffer.speech_stopped", item_id: "i1" });
  ws.server({ type: "conversation.item.input_audio_transcription.completed", item_id: "i1", transcript: "후퇴" });
  ws.server(toolDone({ command_layer: "emergency", goal: "후퇴" }));
  await flush();
  const failed = log.intents.find(i => i.kind === "failed");
  assert.equal(failed.ok, false); assert.match(failed.error, /direct_control_conflict/);
  c.stop();
});
test("client: session mint failure -> error state with reason", async () => {
  const { c, log } = client({ requestJson: async () => { throw new Error("proxy 502"); } });
  await c.start(); await flush();
  assert.equal(c.snapshot().state, "error"); assert.match(c.snapshot().reason, /proxy 502/);
  c.stop();
  assert.ok(log.states.includes("error"));
});
test("client: socket close while armed -> error then reconnect with a fresh socket", async () => {
  const { c } = client();
  await c.start(); await flush();
  const first = FakeWs.last; first.server({ type: "session.updated" });
  first.emit("close", 1006, Buffer.from("gone"));
  assert.equal(c.snapshot().state, "error"); assert.match(c.snapshot().reason, /1006/);
  await new Promise(resolve => setTimeout(resolve, 30)); await flush();
  assert.notEqual(FakeWs.last, first);
  FakeWs.last.server({ type: "session.updated" });
  assert.equal(c.snapshot().state, "idle");
  c.stop();
});
test("client: expired secret is re-minted on reconnect", async () => {
  let mints = 0;
  const { c } = client({ requestJson: async pathname => { if (pathname === "/api/realtime/session") { mints += 1; return { client_secret: `ek_secret_${mints}xxx`, expires_at: 10, ws_url: "wss://rt.example/x" }; } return {}; } });
  await c.start(); await flush();
  const first = FakeWs.last; first.server({ type: "session.updated" });
  first.emit("close", 1006, Buffer.from(""));
  await new Promise(resolve => setTimeout(resolve, 30)); await flush();
  assert.equal(mints, 2);
  assert.equal(FakeWs.last.options.headers.Authorization, `${"Bear" + "er"} ek_secret_2xxx`);
  c.stop();
});
test("client: stop closes the socket and returns to idle/inactive; late events are ignored", async () => {
  const { c } = client();
  await c.start(); await flush();
  const ws = FakeWs.last; ws.server({ type: "session.updated" });
  c.stop();
  assert.equal(c.snapshot().active, false); assert.equal(c.snapshot().state, "idle");
  ws.server({ type: "input_audio_buffer.speech_started", item_id: "late" });
  assert.equal(c.snapshot().state, "idle");
});
test("session.update VAD silence is configurable and bounded", () => {
  assert.equal(defaultSessionUpdate({ VOI_VAD_SILENCE_MS: "700" }).session.audio.input.turn_detection.silence_duration_ms, 700);
  assert.equal(defaultSessionUpdate({ VOI_VAD_SILENCE_MS: "5" }).session.audio.input.turn_detection.silence_duration_ms, 450);
  assert.equal(defaultSessionUpdate({ VOI_TRANSCRIBE_MODEL: "off" }).session.audio.input.transcription, undefined);
});

// ---------- rule-first path (deterministic rule beats the model; dedupe + cancel) ----------
function ruleClient(intentImpl) {
  const log = { posts: [], intents: [] };
  const { c, advance } = client({
    requestJson: async (pathname, options) => {
      if (pathname === "/api/realtime/session") return { client_secret: "ek_testsecret", expires_at: 4e9, ws_url: "wss://rt.example/v1/realtime?model=m", model: "m", tools: [] };
      const body = JSON.parse(options.body);
      log.posts.push(body);
      return intentImpl(body);
    },
    onIntent: x => log.intents.push(x),
  });
  return { c, log, advance };
}
const accepted = body => (body.fast ? { accepted: true, tier: "emergency", source: "fast_rule", update_id: "u-rule" } : { accepted: true, tier: "operation", source: "tool_call" });
async function armed(rc) {
  await rc.c.start(); await flush();
  const ws = FakeWs.last; ws.server({ type: "session.updated" }); return ws;
}
const speak = (ws, item) => { ws.server({ type: "input_audio_buffer.speech_started", item_id: item }); ws.server({ type: "input_audio_buffer.speech_stopped", item_id: item }); };
const cancels = ws => ws.sent.filter(e => e.type === "response.cancel");

test("rule-first: 후퇴 posts a fast intent on the transcript, cancels the model response and ignores its tool call", async () => {
  const rc = ruleClient(accepted); const ws = await armed(rc);
  speak(ws, "i1"); rc.advance(300);
  ws.server({ type: "response.created", response: { id: "resp_1" } });
  ws.server({ type: "conversation.item.input_audio_transcription.completed", item_id: "i1", transcript: "후퇴." });
  await flush();
  assert.equal(rc.log.posts.length, 1);
  assert.deepEqual({ ...rc.log.posts[0], latency_ms: undefined }, { transcript: "후퇴.", fast: true, path: "rule", latency_ms: undefined });
  assert.equal(cancels(ws).length, 1); assert.equal(cancels(ws)[0].response_id, "resp_1");
  // the model's own call for the same utterance arrives anyway: it must NOT execute again
  ws.server({ ...toolDone({ command_layer: "emergency", goal: "후퇴", emergency: { force_retreat: true } }), response_id: "resp_1" });
  ws.server({ type: "response.done", response: { id: "resp_1", status: "cancelled", output: [] } });
  await flush();
  assert.equal(rc.log.posts.length, 1, "no second post");
  const submitted = rc.log.intents.filter(i => i.kind === "submitted");
  assert.equal(submitted.length, 1); assert.equal(submitted[0].path, "rule"); assert.equal(submitted[0].ok, true);
  assert.equal(typeof submitted[0].latency_ms.speech_end_to_decision, "number"); assert.equal(typeof submitted[0].latency_ms.decision_to_publish, "number");
  assert.equal(rc.log.intents.filter(i => i.kind === "no_command").length, 0);
  rc.c.stop();
});
test("rule-first: the response created after the rule fired is cancelled as soon as it appears", async () => {
  const rc = ruleClient(accepted); const ws = await armed(rc);
  speak(ws, "i1");
  ws.server({ type: "conversation.item.input_audio_transcription.completed", item_id: "i1", transcript: "홀드" });
  await flush();
  assert.equal(rc.log.posts.length, 1); assert.equal(cancels(ws).length, 0, "no response yet, nothing to cancel");
  ws.server({ type: "response.created", response: { id: "resp_late" } });
  assert.equal(cancels(ws).length, 1); assert.equal(cancels(ws)[0].response_id, "resp_late");
  ws.server({ ...toolDone({ command_layer: "emergency", goal: "홀드", emergency: { hold_position: true } }), response_id: "resp_late" });
  await flush();
  assert.equal(rc.log.posts.length, 1);
  rc.c.stop();
});
test("rule-first: no rule hit (or a negation) falls through to the model tool call exactly once", async () => {
  for (const text of ["마린 두 기 정찰해", "후퇴하지 마", "멈추지 마"]) {
    const rc = ruleClient(accepted); const ws = await armed(rc);
    speak(ws, "i1"); ws.server({ type: "response.created", response: { id: "r1" } });
    ws.server({ type: "conversation.item.input_audio_transcription.completed", item_id: "i1", transcript: text });
    await flush();
    assert.equal(rc.log.posts.length, 0, text); assert.equal(cancels(ws).length, 0, text);
    ws.server({ ...toolDone({ command_layer: "operation", goal: "g", operations: [{ goal: "a" }] }), response_id: "r1" });
    await flush();
    assert.equal(rc.log.posts.length, 1, text); assert.equal(rc.log.posts[0].fast, undefined); assert.equal(rc.log.posts[0].path, "model");
    assert.ok(rc.log.posts[0].tool_arguments);
    rc.c.stop();
  }
});
test("rule-first: a model call that already ran is never followed by a rule post for the same turn", async () => {
  const rc = ruleClient(accepted); const ws = await armed(rc);
  speak(ws, "i1"); ws.server({ type: "response.created", response: { id: "r1" } });
  ws.server({ ...toolDone({ command_layer: "emergency", goal: "후퇴", emergency: { force_retreat: true } }), response_id: "r1" });
  ws.server({ type: "conversation.item.input_audio_transcription.completed", item_id: "i1", transcript: "후퇴" });
  await flush(); await new Promise(resolve => setTimeout(resolve, 30));
  assert.equal(rc.log.posts.length, 1); assert.ok(rc.log.posts[0].tool_arguments); assert.equal(rc.log.posts[0].fast, undefined);
  rc.c.stop();
});
test("rule-first: rule request failure or a server fast_miss releases the turn to the model call", async () => {
  for (const impl of [body => { if (body.fast) throw new Error("backend down"); return accepted(body); }, body => (body.fast ? { accepted: false, status: "fast_miss" } : accepted(body))]) {
    const rc = ruleClient(impl); const ws = await armed(rc);
    speak(ws, "i1"); ws.server({ type: "response.created", response: { id: "r1" } });
    ws.server({ type: "conversation.item.input_audio_transcription.completed", item_id: "i1", transcript: "후퇴" });
    // the model's call lands while the rule request is still in flight: it is parked, not lost
    ws.server({ ...toolDone({ command_layer: "emergency", goal: "후퇴", emergency: { force_retreat: true } }), response_id: "r1" });
    await flush(); await flush();
    assert.equal(rc.log.posts.length, 2, JSON.stringify(rc.log.posts));
    assert.equal(rc.log.posts[0].fast, true); assert.ok(rc.log.posts[1].tool_arguments);
    assert.equal(cancels(ws).length, 0);
    rc.c.stop();
  }
});
test("rule-first: a model call arriving while the rule request is pending is dropped once the rule is handled", async () => {
  let release; const gate = new Promise(resolve => { release = resolve; });
  const rc = ruleClient(async body => { if (body.fast) { await gate; } return accepted(body); });
  const ws = await armed(rc);
  speak(ws, "i1"); ws.server({ type: "response.created", response: { id: "r1" } });
  ws.server({ type: "conversation.item.input_audio_transcription.completed", item_id: "i1", transcript: "멈춰" });
  ws.server({ ...toolDone({ command_layer: "emergency", goal: "멈춰", emergency: { cancel_attacks: true } }), response_id: "r1", call_id: "c9" });
  await flush();
  assert.equal(rc.log.posts.length, 1);
  release(); await flush(); await flush();
  assert.equal(rc.log.posts.length, 1, "parked model call must not post");
  assert.equal(rc.c.snapshot().state, "acting");
  assert.equal(cancels(ws).length, 1);
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(rc.c.snapshot().state, "idle");
  rc.c.stop();
});
test("rule-first: two utterances back to back are deduped per turn (second is model, first is rule)", async () => {
  const rc = ruleClient(accepted); const ws = await armed(rc);
  speak(ws, "i1"); ws.server({ type: "response.created", response: { id: "r1" } });
  ws.server({ type: "conversation.item.input_audio_transcription.completed", item_id: "i1", transcript: "후퇴" });
  await flush();
  speak(ws, "i2"); ws.server({ type: "response.created", response: { id: "r2" } });
  ws.server({ type: "conversation.item.input_audio_transcription.completed", item_id: "i2", transcript: "마린 둘 정찰" });
  ws.server({ ...toolDone({ command_layer: "emergency", goal: "후퇴", emergency: { force_retreat: true } }), response_id: "r1", call_id: "c1" });
  ws.server({ ...toolDone({ command_layer: "operation", goal: "정찰", operations: [{ goal: "s" }] }), response_id: "r2", call_id: "c2" });
  await flush();
  assert.equal(rc.log.posts.length, 2);
  assert.equal(rc.log.posts[0].fast, true); assert.equal(rc.log.posts[1].transcript, "마린 둘 정찰"); assert.ok(rc.log.posts[1].tool_arguments);
  rc.c.stop();
});
test("rule-first: a replayed tool-call event (same call id) posts once", async () => {
  const rc = ruleClient(accepted); const ws = await armed(rc);
  speak(ws, "i1"); ws.server({ type: "response.created", response: { id: "r1" } });
  ws.server({ type: "conversation.item.input_audio_transcription.completed", item_id: "i1", transcript: "탱크 정찰" });
  const call = { ...toolDone({ command_layer: "operation", goal: "g", operations: [{ goal: "a" }] }), response_id: "r1", call_id: "dup" };
  ws.server(call); ws.server(call);
  await flush();
  assert.equal(rc.log.posts.length, 1);
  rc.c.stop();
});
test("rule-first can be switched off by preference", async () => {
  const rc = ruleClient(accepted);
  const off = client({ getPrefs: () => ({ fastRules: false }), requestJson: async (pathname, options) => { if (pathname === "/api/realtime/session") return { client_secret: "ek_x", expires_at: 4e9, ws_url: "wss://rt.example/x" }; rc.log.posts.push(JSON.parse(options.body)); return accepted({}); } });
  await off.c.start(); await flush();
  const ws = FakeWs.last; ws.server({ type: "session.updated" });
  speak(ws, "i1"); ws.server({ type: "conversation.item.input_audio_transcription.completed", item_id: "i1", transcript: "후퇴" });
  await flush();
  assert.equal(rc.log.posts.length, 0);
  off.c.stop();
});
