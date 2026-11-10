"use strict";
// Second cursor for the smoke drivers: acts on OUR OWN windows through the Accessibility helper (smoke/ax_cursor.swift) and shows
// where it acts with a separate pointer window. The user's real mouse and keyboard are never used: no warp, no posted events.
// Hover positions are simulated by the caller through main's `_internals.setFakeCursor` (the dock only reads cursor positions).
const { execFileSync, spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const readline = require("node:readline");

const BIN = process.env.AX_CURSOR || "/tmp/voi_ax_cursor";
const SOURCE = path.join(__dirname, "ax_cursor.swift");
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function ensureBinary() {
  if (!fs.existsSync(BIN) || fs.statSync(BIN).mtimeMs < fs.statSync(SOURCE).mtimeMs) {
    execFileSync("swiftc", ["-O", SOURCE, "-o", BIN], { timeout: 180000, stdio: "inherit" });
  }
}

// Pure: element frame (global points) -> the point the pointer glides to (centre) and the in-between steps.
function centre(frame) { return { x: frame.x + frame.w / 2, y: frame.y + frame.h / 2 }; }
function glidePath(from, to, steps = 6) {
  if (!from) return [to];
  return Array.from({ length: steps }, (_, i) => {
    const t = (i + 1) / steps;
    return { x: from.x + (to.x - from.x) * t, y: from.y + (to.y - from.y) * t };
  });
}

const POINTER_HTML = `<!doctype html><meta charset="utf-8"><style>
html,body{margin:0;background:transparent;overflow:hidden;font:600 12px -apple-system,system-ui;color:#fff}
svg{position:absolute;left:0;top:0;filter:drop-shadow(0 0 6px #ff4fd8)}
#l{position:absolute;left:22px;top:14px;padding:2px 7px;border-radius:9px;background:rgba(40,10,60,.82);border:1px solid #ff8be8;white-space:nowrap}
#l:empty{display:none}</style>
<svg width="22" height="26" viewBox="0 0 22 26"><path d="M2 2 L2 21 L7.5 15.5 L11 24 L14.5 22.5 L11 14.5 L19 14.5 Z" fill="#ff4fd8" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>
<div id="l"></div>`;

async function createVirtualCursor({ BrowserWindow, pid = process.pid, readyTimeoutMs = 8000 } = {}) {
  ensureBinary();
  const pointer = new BrowserWindow({
    width: 220, height: 44, show: false, frame: false, transparent: true, resizable: false, movable: false, focusable: false,
    skipTaskbar: true, hasShadow: false, backgroundColor: "#00000000", alwaysOnTop: true,
    ...(process.platform === "darwin" ? { type: "panel" } : {}),
    webPreferences: { sandbox: true, contextIsolation: true },
  });
  pointer.setIgnoreMouseEvents(true);
  pointer.setAlwaysOnTop(true, "screen-saver", 1); // one above the HUD / overlay / board, which all sit at "screen-saver"
  pointer.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  await pointer.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(POINTER_HTML)}`);

  const child = spawn(BIN, ["serve", String(pid)], { stdio: ["pipe", "pipe", "inherit"] });
  const lines = readline.createInterface({ input: child.stdout });
  const waiting = [];
  lines.on("line", line => { const next = waiting.shift(); if (next) { try { next(JSON.parse(line)); } catch { next({ ok: false, error: line }); } } });
  const ask = body => new Promise(resolve => { waiting.push(resolve); child.stdin.write(`${JSON.stringify(body)}\n`); });
  const ready = await Promise.race([new Promise(resolve => waiting.push(resolve)), sleep(readyTimeoutMs).then(() => ({ ok: false, error: "helper not ready" }))]);
  if (!ready.ok) { child.kill(); pointer.destroy(); throw new Error(`ax_cursor: ${ready.error || "not ready"}`); }

  let at = null;
  let chain = Promise.resolve();
  const serial = fn => (chain = chain.then(fn, fn));

  async function moveTo(x, y, label = "") {
    for (const p of glidePath(at, { x, y })) {
      if (!pointer.isDestroyed()) pointer.setPosition(Math.round(p.x - 2), Math.round(p.y - 2), false);
      await sleep(18);
    }
    at = { x, y };
    if (!pointer.isDestroyed()) {
      await pointer.webContents.executeJavaScript(`document.getElementById("l").textContent=${JSON.stringify(String(label))}`);
      if (!pointer.isVisible()) pointer.showInactive();
    }
  }

  async function act(op, id, { value, label = "", glide = true } = {}) {
    return serial(async () => {
      if (glide) {
        const located = await ask({ op: "frame", id });
        if (located.ok) { const c = centre(located.frame); await moveTo(c.x, c.y, label); }
      }
      return ask({ op, id, ...(value === undefined ? {} : { value }) });
    });
  }

  return {
    trusted: Boolean(ready.trusted),
    moveTo: (x, y, label) => serial(() => moveTo(x, y, label)),
    frame: id => serial(() => ask({ op: "frame", id })),
    point: id => act("frame", id, { label: "" }),
    press: (id, opts) => act("press", id, opts),
    focus: (id, opts) => act("focus", id, opts),
    type: (id, value, opts = {}) => act("set", id, { ...opts, value }),
    close() { try { child.stdin.end(); child.kill(); } catch { /* gone */ } if (!pointer.isDestroyed()) pointer.destroy(); },
  };
}

module.exports = { createVirtualCursor, centre, glidePath, BIN, SOURCE };
