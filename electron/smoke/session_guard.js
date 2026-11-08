"use strict";
// Real-pointer smoke runs are meaningless on a locked or dark screen: synthetic input is dropped and screencapture is black,
// so every focus/click check would "fail" for a reason that has nothing to do with the app. This guard turns that into an
// explicit BLOCKED result (exit code 2) instead of a fake FAIL. Pure parsers are exported for unit tests.
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const MIN_BRIGHTNESS = 5; // mean of R,G,B over a 0..255 capture; a powered-off/locked-dark display reads ~0

// `ioreg -n Root -d1` prints `"IOConsoleLocked" = Yes|No`. (CGSSessionScreenIsLocked, with two S, is the CoreGraphics twin of this flag.)
function parseConsoleLocked(ioregText) {
  const match = /"IOConsoleLocked"\s*=\s*(Yes|No|true|false)/i.exec(String(ioregText));
  if (!match) return null;
  return /^(yes|true)$/i.test(match[1]);
}

// mean of R,G,B over a BGRA bitmap (Electron nativeImage.toBitmap() order on macOS), sampling every `step`-th pixel
function meanBrightnessBGRA(bitmap, step = 16) {
  let sum = 0, n = 0;
  for (let i = 0; i + 3 < bitmap.length; i += 4 * step) { sum += bitmap[i] + bitmap[i + 1] + bitmap[i + 2]; n += 3; }
  return n ? sum / n : 0;
}

function evaluate({ locked, brightness }) {
  if (locked === true) return { ok: false, reason: "screen locked (IOConsoleLocked = Yes)" };
  if (typeof brightness === "number" && brightness <= MIN_BRIGHTNESS) return { ok: false, reason: `screen dark (mean brightness ${brightness.toFixed(2)} <= ${MIN_BRIGHTNESS})` };
  return { ok: true, reason: "" };
}

function readConsoleLocked() {
  try { return parseConsoleLocked(execFileSync("ioreg", ["-n", "Root", "-d1"], { encoding: "utf8", timeout: 5000 })); } catch { return null; }
}

function readBrightness(nativeImage) {
  const file = path.join(os.tmpdir(), `voi-guard-${process.pid}.png`);
  try {
    execFileSync("screencapture", ["-x", "-t", "png", file], { timeout: 8000 });
    const image = nativeImage.createFromPath(file);
    return image.isEmpty() ? 0 : meanBrightnessBGRA(image.toBitmap());
  } catch { return null; } finally { try { fs.unlinkSync(file); } catch { /* none */ } }
}

// Returns {ok, reason}. Non-darwin and SMOKE_SKIP_GUARD=1 are never blocked.
function checkSession(nativeImage) {
  if (process.platform !== "darwin" || process.env.SMOKE_SKIP_GUARD === "1") return { ok: true, reason: "" };
  return evaluate({ locked: readConsoleLocked(), brightness: readBrightness(nativeImage) });
}

// Prints the BLOCKED line and returns true when the caller must stop (and exit with code 2).
function isBlocked(nativeImage) {
  const verdict = checkSession(nativeImage);
  if (verdict.ok) return false;
  console.log(`BLOCKED: ${verdict.reason}. Real-pointer checks would only measure the lock screen, not the app. Unlock the Mac, wake the display, rerun.`);
  return true;
}

module.exports = { MIN_BRIGHTNESS, parseConsoleLocked, meanBrightnessBGRA, evaluate, checkSession, isBlocked };
