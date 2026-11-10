const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.join(__dirname, "..");
const SKIP_DIRS = new Set(["node_modules", "smoke", "test", "docs", "fixtures", "bin"]);
function walk(dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) { if (!SKIP_DIRS.has(entry.name)) walk(path.join(dir, entry.name), out); }
    else if (/\.(js|swift|html|css|sh)$/.test(entry.name)) out.push(path.join(dir, entry.name));
  }
  return out;
}
// Everything shipped in the app (main, overlay, sketch window, board, renderer, native helper) -- everything except electron/smoke (the OS test driver), tests, docs.
const FORBIDDEN = /CGWarpMouseCursorPosition|CGDisplayMoveCursorToPoint|CGAssociateMouseAndMouseCursorPosition|robotjs|setCursorPos|SetCursorPos|warp|CGEventPost|\.post\s*\(\s*tap|CGEventCreateMouseEvent|CGEvent\s*\(\s*mouseEventSource|postToPid|XTestFakeMotion|nut-js|@nut-tree|nircmd|SendInput|mouse_event|NSEvent\.mouseLocation\s*=|NSCursor\.set|\.setPosition\(/i;

test("no shipped source can move, warp, post to or capture the real mouse cursor (only electron/smoke may)", () => {
  const files = walk(root);
  assert.ok(files.length > 20, "walk found the sources");
  for (const needed of ["main.js", "sketch/controller.js", "sketch/window/sketch.js", "native/chord_helper.swift", "overlay/overlay.js"]) assert.ok(files.includes(path.join(root, needed)), `${needed} is covered by the guard`);
  const hits = [];
  for (const file of files) {
    const text = fs.readFileSync(file, "utf8");
    text.split("\n").forEach((line, index) => { const m = FORBIDDEN.exec(line); if (m) hits.push(`${path.relative(root, file)}:${index + 1}: ${m[0]}`); });
  }
  assert.deepEqual(hits, []);
});
test("the guard really detects each forbidden API (so it cannot pass vacuously)", () => {
  for (const sample of ["CGWarpMouseCursorPosition(p)", "require('robotjs')", "SetCursorPos(1,2)", "cursor.warp(1,2)", "ev.post(tap: .cghidEventTap)", "CGEventPost(0, e)", "setCursorPos"]) assert.ok(FORBIDDEN.test(sample), sample);
});
test("the native helper is listen-only: a listenOnly event tap, never an active one, and it returns every event unchanged", () => {
  const swift = fs.readFileSync(path.join(root, "native", "chord_helper.swift"), "utf8");
  assert.match(swift, /options: \.listenOnly/); assert.doesNotMatch(swift, /\.defaultTap/);
  assert.match(swift, /Unmanaged\.passUnretained\(event\)/);
  assert.doesNotMatch(swift, /event\.(location|flags)\s*=[^=]/);
});
test("the ghost agent cursor is a DOM element positioned with a CSS transform inside the sketch window", () => {
  const js = fs.readFileSync(path.join(root, "sketch", "window", "sketch.js"), "utf8"), css = fs.readFileSync(path.join(root, "sketch", "window", "sketch.css"), "utf8"), html = fs.readFileSync(path.join(root, "sketch", "window", "index.html"), "utf8");
  assert.match(html, /id="cursor" class="ghost"/); assert.match(js, /ghost\.style\.transform = `translate\(/); assert.match(css, /\.ghost \{[^}]*pointer-events: none/);
});
