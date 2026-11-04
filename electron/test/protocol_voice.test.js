const test = require("node:test");
const assert = require("node:assert/strict");
const P = require("../protocol.js");

test("voice control takes no fields", () => {
  assert.deepEqual(P.validateVoiceControl(undefined), {});
  assert.deepEqual(P.validateVoiceControl({}), {});
  assert.throws(() => P.validateVoiceControl({ url: "x" }), /unexpected/);
});
test("audio chunk: bounded, even-length, binary only", () => {
  const ok = Buffer.from(new Int16Array(1200).buffer);
  const out = P.validateVoiceAudio(new Uint8Array(ok).buffer);
  assert.equal(out.length, 2400);
  assert.equal(P.validateVoiceAudio(new Uint8Array(4)).length, 4);
  assert.equal(P.validateVoiceAudio(new Int16Array(10)).length, 20);
  for (const bad of ["abc", null, 12, {}, new Uint8Array(0), new Uint8Array(3), new Uint8Array(P.MAX_AUDIO_CHUNK_BYTES + 2)]) assert.throws(() => P.validateVoiceAudio(bad), /audio chunk/, String(bad));
  assert.equal(P.validateVoiceAudio(new Uint8Array(P.MAX_AUDIO_CHUNK_BYTES)).length, P.MAX_AUDIO_CHUNK_BYTES);
});
test("mic error reason is trimmed and bounded", () => {
  assert.deepEqual(P.validateMicError({ reason: "  denied " }), { reason: "denied" });
  assert.equal(P.validateMicError({ reason: "x".repeat(500) }).reason.length, 160);
  assert.throws(() => P.validateMicError({ reason: "" }), /reason/);
  assert.throws(() => P.validateMicError({ reason: "a", extra: 1 }), /unexpected/);
});
test("overlay state allowlist and voice->overlay mapping", () => {
  for (const state of ["off", "listening", "thinking"]) assert.deepEqual(P.validateOverlayState({ state }), { state });
  assert.throws(() => P.validateOverlayState({ state: "rainbow" }), /unknown/);
  assert.equal(P.overlayStateForVoice({ active: true, state: "listening" }), "listening");
  assert.equal(P.overlayStateForVoice({ active: true, state: "thinking" }), "thinking");
  for (const state of ["idle", "connecting", "acting", "error"]) assert.equal(P.overlayStateForVoice({ active: true, state }), "off");
  assert.equal(P.overlayStateForVoice({ active: false, state: "listening" }), "off");
});
test("dock IPC validators", () => {
  assert.deepEqual(P.validateDockMode({ mode: "right" }), { mode: "right" });
  assert.throws(() => P.validateDockMode({ mode: "top" }), /unknown/);
  assert.deepEqual(P.validateDockPin({ pinned: true }), { pinned: true });
  assert.throws(() => P.validateDockPin({ pinned: "yes" }), /boolean/);
  assert.deepEqual(P.validateDockFocus({ focus: false }), { focus: false });
  assert.deepEqual(P.validateDockRect({ x: 10.4, y: 0, width: 900.6, height: 200 }), { x: 10, y: 0, width: 901, height: 200 });
  for (const bad of [{ x: -1, y: 0, width: 1, height: 1 }, { x: 0, y: 0, width: 0, height: 5 }, { x: 0, y: 0, width: NaN, height: 5 }, { x: 0, y: 0, width: 1, height: 1, z: 1 }]) assert.throws(() => P.validateDockRect(bad));
  assert.deepEqual(P.validateDockAttention({ reason: "blocked" }), { reason: "blocked" });
  assert.throws(() => P.validateDockAttention({ reason: "boom" }), /attention/);
});
test("realtime ws url: wss only, ws only for loopback, no credentials", () => {
  assert.ok(P.validateRealtimeWsUrl("wss://proxy.nomadamas.org/v1/realtime?model=gpt-realtime"));
  assert.ok(P.validateRealtimeWsUrl("ws://127.0.0.1:9/v1/realtime"));
  for (const bad of ["ws://evil.example/x", "http://127.0.0.1/x", "wss://u:p@host/x", "file:///x"]) assert.throws(() => P.validateRealtimeWsUrl(bad), /realtime url/, bad);
});
test("realtime session validation extracts the secret and bounds tools", () => {
  const s = P.validateRealtimeSession({ client_secret: "ek_abcdefgh", expires_at: 1.8e9, model: "gpt-realtime", ws_url: "wss://h/v1/realtime?model=m", tools: [{ name: "submit_command" }], tier_hint: "x" });
  assert.equal(s.clientSecret, "ek_abcdefgh"); assert.equal(s.expiresAt, 1.8e9); assert.equal(s.tools.length, 1);
  assert.throws(() => P.validateRealtimeSession({ ws_url: "wss://h/x" }), /client_secret/);
  assert.throws(() => P.validateRealtimeSession({ client_secret: "ek_has space", ws_url: "wss://h/x" }), /client_secret/);
  assert.throws(() => P.validateRealtimeSession({ client_secret: "ek_abcdefgh", ws_url: "wss://h/x", tools: new Array(9).fill({}) }), /tools/);
});
test("intent request needs tool_arguments or transcript, sanitises latency", () => {
  assert.deepEqual(P.validateIntentRequest({ transcript: "  후퇴 " }), { transcript: "후퇴", latency_ms: {} });
  const r = P.validateIntentRequest({ tool_arguments: { command_layer: "macro", goal: "g" }, latency_ms: { speech_end_to_tool_call: 812.6, "bad key!": 1, x: "nope", y: null } });
  assert.equal(r.latency_ms.speech_end_to_tool_call, 813); assert.equal(r.latency_ms.y, null); assert.equal(Object.keys(r.latency_ms).length, 2);
  assert.throws(() => P.validateIntentRequest({}), /needs/);
  assert.throws(() => P.validateIntentRequest({ transcript: "x", unit_tags: [1] }), /unexpected/);
  assert.throws(() => P.validateIntentRequest({ tool_arguments: { blob: "x".repeat(40000) } }), /too large/);
});

test("glass settings: alpha 0.08..0.90, blur 0..40, vibrancy boolean, partial updates only", () => {
  assert.deepEqual(P.validateGlass({ alpha: 0.28 }), { alpha: 0.28 });
  assert.deepEqual(P.validateGlass({ alpha: 0.08, blur: 0, vibrancy: true }), { alpha: 0.08, blur: 0, vibrancy: true });
  assert.deepEqual(P.validateGlass({ alpha: 0.9, blur: 39.6 }), { alpha: 0.9, blur: 40 });
  for (const bad of [{ alpha: 0.07 }, { alpha: 0.91 }, { alpha: "0.3" }, { alpha: NaN }, { blur: -1 }, { blur: 41 }, { blur: Infinity }, { vibrancy: 1 }, {}, { alpha: 0.3, color: "red" }]) assert.throws(() => P.validateGlass(bad), /glass|vibrancy|unexpected/, JSON.stringify(bad));
});
test("glass alpha nudge moves in 5% steps and clamps at both ends", () => {
  assert.equal(P.nudgeGlassAlpha(0.28, 1), 0.33);
  assert.equal(P.nudgeGlassAlpha(0.28, -1), 0.23);
  assert.equal(P.nudgeGlassAlpha(0.1, -1), 0.08);
  assert.equal(P.nudgeGlassAlpha(0.88, 1), 0.9);
  assert.equal(P.nudgeGlassAlpha(0.9, 1), 0.9);
  let a = 0.08; for (let i = 0; i < 30; i += 1) a = P.nudgeGlassAlpha(a, 1); assert.equal(a, 0.9);
});
test("stored glass settings are sanitised field by field (corrupt file never breaks the UI)", () => {
  assert.deepEqual(P.sanitizeStoredGlass(null), { alpha: 0.28, blur: 12, vibrancy: false });
  assert.deepEqual(P.sanitizeStoredGlass({ alpha: 0.5, blur: 99, vibrancy: true }), { alpha: 0.5, blur: 12, vibrancy: true });
  assert.deepEqual(P.sanitizeStoredGlass({ alpha: "x", blur: 20 }), { alpha: 0.28, blur: 20, vibrancy: false });
});

test("voice level IPC accepts 0..1 numbers only", () => {
  assert.equal(P.validateVoiceLevel(0), 0); assert.equal(P.validateVoiceLevel(0.456), 0.46); assert.equal(P.validateVoiceLevel(1), 1);
  for (const bad of [-0.1, 1.1, NaN, "0.5", null, undefined, {}]) assert.throws(() => P.validateVoiceLevel(bad), /voice level/);
});
