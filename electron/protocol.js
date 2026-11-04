"use strict";
function validateCommand(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) throw new TypeError("command must be an object");
  if (Object.keys(input).some(key => !["text", "response_language"].includes(key))) throw new TypeError("unexpected command field");
  const text = typeof input.text === "string" ? input.text.trim() : "";
  if (!text || text.length > 2000) throw new TypeError("command text must contain 1..2000 characters");
  return { text, ui_language: "ko", response_language: input.response_language === "en" ? "en" : "ko" };
}
function validateCursor(input) {
  if (input === undefined || input === null) return 0;
  if (!Number.isInteger(input) || input < 0) throw new TypeError("history cursor must be a non-negative integer");
  return input;
}
function validateTargetPin(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) throw new TypeError("target pin must be an object");
  if (Object.keys(input).some(key => !["name", "target", "metadata"].includes(key))) throw new TypeError("unexpected target pin field");
  const name = typeof input.name === "string" ? input.name.trim() : "";
  const target = typeof input.target === "string" ? input.target.trim() : "";
  if (!name || name.length > 128) throw new TypeError("target pin name must contain 1..128 characters");
  const semanticTargets = new Set(["self_main","self_ramp","self_choke","self_natural","self_third","self_mineral_line","self_geyser","enemy_main","enemy_ramp","enemy_choke","enemy_front","enemy_natural","enemy_third","enemy_mineral_line","scout_location","last_seen_enemy_area"]);
  if (!semanticTargets.has(target)) throw new TypeError("target pin must be a known semantic target");
  if (input.metadata !== undefined && (!input.metadata || typeof input.metadata !== "object" || Array.isArray(input.metadata))) throw new TypeError("target pin metadata must be an object");
  const metadata = input.metadata === undefined ? {} : {...input.metadata};
  if (Object.entries(metadata).some(([key,value]) => !["source","label"].includes(key) || typeof value !== "string" || value.length > 120)) throw new TypeError("raw runtime identity is not allowed");
  return {name, target, metadata};
}
function validateBaseUrl(value) {
  const url = new URL(value);
  if (url.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname) || url.username || url.password || url.pathname !== "/" || url.search || url.hash) throw new TypeError("Commander URL must be a loopback HTTP origin");
  return url.origin;
}


// ---- voice / overlay / window contracts -------------------------------------------------
const VOICE_STATES = Object.freeze(["idle", "connecting", "listening", "thinking", "acting", "error"]);
const OVERLAY_STATES = Object.freeze(["off", "listening", "thinking"]);
const DOCK_MODES = Object.freeze(["bottom", "right"]);
const ATTENTION_REASONS = Object.freeze(["stage", "blocked", "emergency", "error"]);
const MAX_AUDIO_CHUNK_BYTES = 48000; // 1 s of 24 kHz pcm16 mono; renderer sends 50 ms chunks.
function plainObject(input, label) {
  if (!input || typeof input !== "object" || Array.isArray(input)) throw new TypeError(`${label} must be an object`);
  return input;
}
function validateVoiceControl(input) {
  if (input === undefined || input === null) return {};
  plainObject(input, "voice control");
  if (Object.keys(input).length) throw new TypeError("unexpected voice control field");
  return {};
}
function validateVoiceAudio(input) {
  let view;
  if (input instanceof ArrayBuffer) view = new Uint8Array(input);
  else if (ArrayBuffer.isView(input)) view = new Uint8Array(input.buffer, input.byteOffset, input.byteLength);
  else throw new TypeError("audio chunk must be binary pcm16");
  if (view.byteLength < 2 || view.byteLength > MAX_AUDIO_CHUNK_BYTES || view.byteLength % 2) throw new TypeError(`audio chunk must be 2..${MAX_AUDIO_CHUNK_BYTES} bytes of pcm16`);
  return Buffer.from(view.buffer, view.byteOffset, view.byteLength);
}
function validateVoiceLevel(input) {
  if (typeof input !== "number" || !Number.isFinite(input) || input < 0 || input > 1) throw new TypeError("voice level must be a number 0..1");
  return Math.round(input * 100) / 100;
}
function validateMicError(input) {
  plainObject(input, "mic error");
  if (Object.keys(input).some(key => key !== "reason")) throw new TypeError("unexpected mic error field");
  const reason = typeof input.reason === "string" ? input.reason.trim().slice(0, 160) : "";
  if (!reason) throw new TypeError("mic error reason must contain 1..160 characters");
  return {reason};
}
function validateOverlayState(input) {
  plainObject(input, "overlay state");
  if (Object.keys(input).some(key => key !== "state")) throw new TypeError("unexpected overlay field");
  if (!OVERLAY_STATES.includes(input.state)) throw new TypeError("unknown overlay state");
  return {state: input.state};
}
function overlayStateForVoice(voice) {
  if (!voice || !voice.active) return "off";
  if (voice.state === "listening") return "listening";
  if (voice.state === "thinking") return "thinking";
  return "off";
}
function validateDockMode(input) {
  plainObject(input, "dock mode");
  if (Object.keys(input).some(key => key !== "mode")) throw new TypeError("unexpected dock mode field");
  if (!DOCK_MODES.includes(input.mode)) throw new TypeError("unknown dock mode");
  return {mode: input.mode};
}
function validateDockPin(input) {
  plainObject(input, "dock pin");
  if (Object.keys(input).some(key => key !== "pinned")) throw new TypeError("unexpected dock pin field");
  if (typeof input.pinned !== "boolean") throw new TypeError("pinned must be a boolean");
  return {pinned: input.pinned};
}
function validateDockFocus(input) {
  plainObject(input, "dock focus");
  if (Object.keys(input).some(key => key !== "focus")) throw new TypeError("unexpected dock focus field");
  if (typeof input.focus !== "boolean") throw new TypeError("focus must be a boolean");
  return {focus: input.focus};
}
// Window-relative layout rectangle of the glass panel, reported by the renderer for hit-testing.
function validateDockRect(input) {
  plainObject(input, "dock rect");
  if (Object.keys(input).some(key => !["x", "y", "width", "height"].includes(key))) throw new TypeError("unexpected dock rect field");
  const out = {};
  for (const key of ["x", "y", "width", "height"]) {
    const value = input[key];
    if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 20000) throw new TypeError("dock rect values must be 0..20000");
    out[key] = Math.round(value);
  }
  if (out.width < 1 || out.height < 1) throw new TypeError("dock rect must be non-empty");
  return out;
}
function validateDockAttention(input) {
  plainObject(input, "dock attention");
  if (Object.keys(input).some(key => key !== "reason")) throw new TypeError("unexpected dock attention field");
  if (!ATTENTION_REASONS.includes(input.reason)) throw new TypeError("unknown attention reason");
  return {reason: input.reason};
}
const GLASS_LIMITS = Object.freeze({ alphaMin: 0.08, alphaMax: 0.9, alphaDefault: 0.28, blurMin: 0, blurMax: 40, blurDefault: 12 });
// Partial update of the glass look: any subset of {alpha, blur, vibrancy}; numbers range-checked, never clamped silently.
function validateGlass(input) {
  plainObject(input, "glass settings");
  if (Object.keys(input).some(key => !["alpha", "blur", "vibrancy"].includes(key))) throw new TypeError("unexpected glass field");
  const out = {};
  if (input.alpha !== undefined) {
    if (typeof input.alpha !== "number" || !Number.isFinite(input.alpha) || input.alpha < GLASS_LIMITS.alphaMin - 1e-9 || input.alpha > GLASS_LIMITS.alphaMax + 1e-9) throw new TypeError("glass alpha must be 0.08..0.90");
    out.alpha = Math.round(input.alpha * 100) / 100;
  }
  if (input.blur !== undefined) {
    if (typeof input.blur !== "number" || !Number.isFinite(input.blur) || input.blur < GLASS_LIMITS.blurMin || input.blur > GLASS_LIMITS.blurMax) throw new TypeError("glass blur must be 0..40");
    out.blur = Math.round(input.blur);
  }
  if (input.vibrancy !== undefined) {
    if (typeof input.vibrancy !== "boolean") throw new TypeError("vibrancy must be a boolean");
    out.vibrancy = input.vibrancy;
  }
  if (!Object.keys(out).length) throw new TypeError("glass settings need alpha, blur or vibrancy");
  return out;
}
// Keyboard nudge helper: 5 % steps, clamped to the allowed range (the shortcut may press past the end).
function nudgeGlassAlpha(current, direction) {
  const next = Math.round((current + (direction > 0 ? 0.05 : -0.05)) * 100) / 100;
  return Math.min(GLASS_LIMITS.alphaMax, Math.max(GLASS_LIMITS.alphaMin, next));
}
function sanitizeStoredGlass(raw) {
  const out = { alpha: GLASS_LIMITS.alphaDefault, blur: GLASS_LIMITS.blurDefault, vibrancy: false };
  if (raw && typeof raw === "object") {
    try { Object.assign(out, validateGlass({ alpha: raw.alpha, blur: raw.blur, vibrancy: raw.vibrancy })); } catch {
      for (const key of ["alpha", "blur", "vibrancy"]) { try { if (raw[key] !== undefined) Object.assign(out, validateGlass({ [key]: raw[key] })); } catch { /* keep default */ } }
    }
  }
  return out;
}
function validateRealtimeWsUrl(value) {
  const url = new URL(value);
  const loopback = ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname);
  if (url.username || url.password || url.hash) throw new TypeError("realtime url must not carry credentials");
  if (url.protocol === "wss:") return url.href;
  if (url.protocol === "ws:" && loopback) return url.href;
  throw new TypeError("realtime url must be wss:// (ws:// only for loopback)");
}
function validateRealtimeSession(input) {
  plainObject(input, "realtime session");
  const secret = input.client_secret;
  if (typeof secret !== "string" || secret.length < 8 || secret.length > 4096 || /\s/.test(secret)) throw new TypeError("realtime session lacks a client_secret");
  const wsUrl = validateRealtimeWsUrl(input.ws_url);
  const expiresAt = typeof input.expires_at === "number" && Number.isFinite(input.expires_at) ? input.expires_at : null;
  const tools = Array.isArray(input.tools) ? input.tools : [];
  if (tools.length > 8) throw new TypeError("realtime session has too many tools");
  return {clientSecret: secret, wsUrl, expiresAt, model: typeof input.model === "string" ? input.model : "", tools, tierHint: typeof input.tier_hint === "string" ? input.tier_hint : ""};
}
function validateIntentRequest(input) {
  plainObject(input, "intent request");
  if (Object.keys(input).some(key => !["tool_arguments", "transcript", "latency_ms"].includes(key))) throw new TypeError("unexpected intent field");
  const transcript = typeof input.transcript === "string" ? input.transcript.trim().slice(0, 2000) : "";
  let toolArguments;
  if (input.tool_arguments !== undefined) {
    toolArguments = plainObject(input.tool_arguments, "tool_arguments");
    if (JSON.stringify(toolArguments).length > 32768) throw new TypeError("tool_arguments too large");
  }
  if (!toolArguments && !transcript) throw new TypeError("intent needs tool_arguments or transcript");
  const latency = {};
  for (const [key, value] of Object.entries(input.latency_ms && typeof input.latency_ms === "object" ? input.latency_ms : {})) {
    if (/^[a-z_]{1,48}$/.test(key) && (value === null || (typeof value === "number" && Number.isFinite(value)))) latency[key] = value === null ? null : Math.round(value);
  }
  const body = {transcript, latency_ms: latency};
  if (toolArguments) body.tool_arguments = toolArguments;
  return body;
}
module.exports = {
  validateCommand, validateBaseUrl, validateCursor, validateTargetPin,
  VOICE_STATES, OVERLAY_STATES, DOCK_MODES, ATTENTION_REASONS, MAX_AUDIO_CHUNK_BYTES,
  validateVoiceControl, validateVoiceAudio, validateVoiceLevel, validateMicError, validateOverlayState, overlayStateForVoice, validateDockMode, validateDockPin, validateDockFocus, validateDockRect, validateDockAttention,
  GLASS_LIMITS, validateGlass, nudgeGlassAlpha, sanitizeStoredGlass,
  validateRealtimeWsUrl, validateRealtimeSession, validateIntentRequest,
};
