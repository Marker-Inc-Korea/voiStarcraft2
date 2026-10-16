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
module.exports = { validateCommand, validateBaseUrl, validateCursor, validateTargetPin };
