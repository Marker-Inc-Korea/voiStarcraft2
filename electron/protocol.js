"use strict";
function validateCommand(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) throw new TypeError("command must be an object");
  if (Object.keys(input).some(key => !["text", "response_language"].includes(key))) throw new TypeError("unexpected command field");
  const text = typeof input.text === "string" ? input.text.trim() : "";
  if (!text || text.length > 2000) throw new TypeError("command text must contain 1..2000 characters");
  return { text, ui_language: "ko", response_language: input.response_language === "en" ? "en" : "ko" };
}
function validateBaseUrl(value) {
  const url = new URL(value);
  if (url.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname) || url.username || url.password || url.pathname !== "/" || url.search || url.hash) throw new TypeError("Commander URL must be a loopback HTTP origin");
  return url.origin;
}
module.exports = { validateCommand, validateBaseUrl };
