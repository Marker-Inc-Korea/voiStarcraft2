"use strict";
// Crop of the minimap frame (including the user's strokes, which are drawn in our overlay above the game) via desktopCapturer.
// Deps are injected so this is testable: getAccess() -> systemPreferences.getMediaAccessStatus("screen"), getSources(opts) -> desktopCapturer.getSources.
// Never throws: any problem is { imagePngB64: null, reason } and the sketch is sent without an image.
const MAX_SIDE = 512;
const DEFAULT_TIMEOUT_MS = 1500;
const Geometry = require("./geometry.js");

function meanBrightness(bitmap) {
  let sum = 0, n = 0;
  const step = Math.max(1, Math.floor(bitmap.length / 4 / 4096)) * 4;
  for (let i = 0; i + 3 < bitmap.length; i += step) { sum += bitmap[i] + bitmap[i + 1] + bitmap[i + 2]; n += 3; }
  return n ? sum / n : 0;
}

async function captureMinimap({ getAccess, getSources, timeoutMs = DEFAULT_TIMEOUT_MS }, display, rect) {
  const none = reason => ({ imagePngB64: null, reason });
  try {
    if (getAccess() !== "granted") return none("permission");
    const px = Geometry.capturePixels({ x: 0, y: 0, w: display.bounds.width, h: display.bounds.height }, display);
    let timer;
    const timeout = new Promise(resolve => { timer = setTimeout(() => resolve("timeout"), timeoutMs); });
    const sources = await Promise.race([getSources({ types: ["screen"], thumbnailSize: { width: px.w, height: px.h } }), timeout]);
    clearTimeout(timer);
    if (sources === "timeout") return none("timeout");
    const source = sources.find(s => String(s.display_id) === String(display.id));
    if (!source) return none("no-source");
    const image = source.thumbnail;
    if (!image || image.isEmpty()) return none("empty");
    const size = image.getSize();
    const kx = size.width / display.bounds.width, ky = size.height / display.bounds.height; // measured, not assumed
    const x0 = Math.max(0, Math.round((rect.x - display.bounds.x) * kx)), y0 = Math.max(0, Math.round((rect.y - display.bounds.y) * ky));
    const x1 = Math.min(size.width, Math.round((rect.x + rect.w - display.bounds.x) * kx)), y1 = Math.min(size.height, Math.round((rect.y + rect.h - display.bounds.y) * ky));
    if (x1 - x0 < 2 || y1 - y0 < 2) return none("empty");
    let crop = image.crop({ x: x0, y: y0, width: x1 - x0, height: y1 - y0 });
    if (meanBrightness(crop.toBitmap()) <= 2) return none("dark");
    const out = Geometry.outputSize(x1 - x0, y1 - y0, MAX_SIDE);
    if (out.w !== x1 - x0 || out.h !== y1 - y0) crop = crop.resize({ width: out.w, height: out.h, quality: "good" });
    return { imagePngB64: crop.toPNG().toString("base64"), reason: "" };
  } catch { return none("error"); }
}

module.exports = { captureMinimap, MAX_SIDE, meanBrightness };
