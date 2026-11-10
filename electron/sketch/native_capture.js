"use strict";
// Pure protocol + state for NATIVE minimap mouse capture.
//
// Why: a mouse-down on any Electron window activates our app and steals keyboard focus from SC2 (a type:"panel" window is not a real
// non-activating NSPanel on Electron 36). So while sketching, the native helper owns the mouse: main sends it the minimap rect and
// {"capture":true}; the helper runs an active tap for left mouse down/drag/up ONLY, reports the ones inside the rect and consumes them
// (neither the game nor our windows ever see them); everything outside the rect passes through untouched. It never posts events and never
// moves the cursor. The sketch window then stays click-through and just renders the strokes built from these messages.
//
//   main -> helper (stdin):  {"rect":{"x","y","w","h"}}   {"capture":true|false}
//   helper -> main (stdout): {"accessibility":true|false}  {"capture":"on"|"off"|"unavailable"}  {"mouse":"down"|"drag"|"up","x","y","t"}
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.VoiSketchNativeCapture = factory();
})(typeof self !== "undefined" ? self : this, function () {
  const clamp01 = v => Math.min(1, Math.max(0, v));
  const r4 = v => Math.round(v * 10000) / 10000;
  const finite = v => typeof v === "number" && Number.isFinite(v);

  const encodeCapture = on => `${JSON.stringify({ capture: Boolean(on) })}\n`;
  function encodeRect(rect) {
    if (!rect || typeof rect !== "object" || !["x", "y", "w", "h"].every(k => finite(rect[k])) || rect.w <= 0 || rect.h <= 0) throw new TypeError("capture rect invalid");
    return `${JSON.stringify({ rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.w), h: Math.round(rect.h) } })}\n`;
  }
  function parseMouse(msg) {
    if (!msg || typeof msg !== "object") return null;
    const kind = { down: "down", drag: "drag", up: "up" }[msg.mouse];
    if (!kind || !finite(msg.x) || !finite(msg.y)) return null;
    return { kind, x: msg.x, y: msg.y, t: finite(msg.t) ? msg.t : 0 };
  }
  const inRect = (rect, x, y) => x >= rect.x && x <= rect.x + rect.w && y >= rect.y && y <= rect.y + rect.h;

  // Helper mouse events -> recorder steps in minimap-FRAME coordinates. The helper only reports presses that started inside the rect;
  // this re-checks (defence in depth) and ignores anything without an accepted press.
  function createStrokeSession(rect) {
    let grabbing = false;
    const norm = (x, y) => ({ nx: r4(clamp01((x - rect.x) / rect.w)), ny: r4(clamp01((y - rect.y) / rect.h)) });
    function feed(m) {
      if (m.kind === "down") {
        if (grabbing || !inRect(rect, m.x, m.y)) return null;
        grabbing = true; return { step: "begin", ...norm(m.x, m.y), t: m.t };
      }
      if (!grabbing) return null;
      if (m.kind === "drag") return { step: "move", ...norm(m.x, m.y), t: m.t };
      grabbing = false; return { step: "end", ...norm(m.x, m.y), t: m.t };
    }
    return { feed, grabbing: () => grabbing };
  }

  // off -> pending (asked, no ack yet) -> native (helper owns the mouse) | fallback (no native path: the Electron panel takes the mouse)
  // release: native with a consumed press in flight -> draining (helper keeps consuming until that press is released) -> off
  function createCaptureState() {
    let mode = "off", pressed = false, ax = "unknown";
    return {
      mode: () => mode,
      accessibility: () => ax,
      setAccessibility(ok) { ax = ok ? "granted" : "denied"; },
      request(on) {
        if (!on) return;
        pressed = false;
        mode = ax === "denied" ? "fallback" : "pending";
      },
      ack(status) {
        if (mode === "pending" && status === "on") mode = "native";
        else if ((mode === "pending" || mode === "native") && status === "unavailable") mode = "fallback";
        else if (status === "off" && mode === "draining") mode = "off";
      },
      mouse(m) {
        if (m.kind === "down") pressed = true;
        else if (m.kind === "up") { pressed = false; if (mode === "draining") mode = "off"; }
      },
      release() {
        if (mode === "native" && pressed) mode = "draining";
        else mode = "off";
      },
    };
  }

  return { encodeCapture, encodeRect, parseMouse, inRect, createStrokeSession, createCaptureState };
});
