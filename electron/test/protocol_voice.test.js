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
  assert.deepEqual(P.validateDockFocus({ focus: false }), { focus: false });
  assert.throws(() => P.validateDockFocus({ focus: 1 }), /boolean/);
  assert.deepEqual(P.validateDockRect({ x: 10.4, y: 0, width: 900.6, height: 200 }), { x: 10, y: 0, width: 901, height: 200 });
  for (const bad of [{ x: -1, y: 0, width: 1, height: 1 }, { x: 0, y: 0, width: 0, height: 5 }, { x: 0, y: 0, width: NaN, height: 5 }, { x: 0, y: 0, width: 1, height: 1, z: 1 }]) assert.throws(() => P.validateDockRect(bad));
  assert.deepEqual(P.validateDockAttention({ reason: "blocked" }), { reason: "blocked" });
  assert.throws(() => P.validateDockAttention({ reason: "boom" }), /attention/);
});
test("settings IPC validators: patch, reset, open, probe, shortcut, preview, push-to-talk", () => {
  assert.deepEqual(P.validateSettingsPatch({ glass: { alpha: 0.5 }, layout: { dock: "left" } }), { glass: { alpha: 0.5 }, layout: { dock: "left" } });
  assert.throws(() => P.validateSettingsPatch({ glass: { alpha: 5 } }), /glass\.alpha/);
  assert.throws(() => P.validateSettingsPatch({ nope: { a: 1 } }), /unknown settings group/);
  assert.deepEqual(P.validateSettingsReset(undefined), {});
  assert.deepEqual(P.validateSettingsReset({ group: "glass" }), { group: "glass" });
  assert.throws(() => P.validateSettingsReset({ group: "x" }), /unknown settings group/);
  assert.throws(() => P.validateSettingsReset({ group: "glass", extra: 1 }), /unexpected/);
  assert.deepEqual(P.validateSettingsOpen({ open: true }), { open: true });
  assert.throws(() => P.validateSettingsOpen({ open: "yes" }), /boolean/);
  assert.deepEqual(P.validateServerProbe({ url: "http://localhost:9000" }), { url: "http://localhost:9000" });
  for (const url of ["https://example.com", "http://10.0.0.5:8765", "http://127.0.0.1.evil.test", "file:///x", "http://u:p@127.0.0.1"]) assert.throws(() => P.validateServerProbe({ url }), /loopback/, url);
  assert.deepEqual(P.validateShortcutPreview({ accelerator: "CommandOrControl+Shift+K" }), { accelerator: "CommandOrControl+Shift+K" });
  for (const accelerator of ["K", "Shift", "CommandOrControl+", "Ctrl+Alt+NotAKey", "a".repeat(100)]) assert.throws(() => P.validateShortcutPreview({ accelerator }), /accelerator/, accelerator);
  assert.deepEqual(P.validateRainbowPreview(undefined), {}); assert.throws(() => P.validateRainbowPreview({ ms: 9 }), /unexpected/);
  assert.deepEqual(P.validatePushToTalk({ down: true }), { down: true }); assert.throws(() => P.validatePushToTalk({ down: 1 }), /boolean/);
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

test("voice level IPC accepts 0..1 numbers only", () => {
  assert.equal(P.validateVoiceLevel(0), 0); assert.equal(P.validateVoiceLevel(0.456), 0.46); assert.equal(P.validateVoiceLevel(1), 1);
  for (const bad of [-0.1, 1.1, NaN, "0.5", null, undefined, {}]) assert.throws(() => P.validateVoiceLevel(bad), /voice level/);
});
