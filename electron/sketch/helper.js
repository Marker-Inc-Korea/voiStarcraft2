"use strict";
// Supervisor for the native chord helper (electron/native/chord_helper.swift). The helper only LISTENS to modifier-flag changes and
// key-down notices (it never posts events and never touches the cursor) and writes line-delimited JSON to stdout:
//   {"status":"ready"} | {"status":"denied","reason":"input-monitoring"} | {"chord":"down"|"up"} | {"key":"other"}
// This module owns parsing, restart with backoff, bounded crash loops, a slow retry while permission is denied, and reports a
// "lost" notice whenever the helper dies so a held chord can never be left half-tracked. All deps are injected (testable).
const DENIED_RETRY_MS = 15000;
const BACKOFF_MS = Object.freeze([500, 1000, 2000, 4000, 8000]);
const MAX_RESTARTS = 5;
const RESTART_WINDOW_MS = 60000;

function createHelperSupervisor({ command, args = [], spawn, onChord = () => {}, onInterrupt = () => {}, onLost = () => {}, onStatus = () => {}, now = Date.now, setTimer = setTimeout, clearTimer = clearTimeout }) {
  let child = null, timer = null, stopped = false, buffer = "";
  let status = { state: "idle", reason: "" };
  let crashes = [];
  const set = (state, reason = "") => { if (status.state === state && status.reason === reason) return; status = { state, reason }; onStatus({ ...status }); };
  const arm = (fn, ms) => { if (timer) clearTimer(timer); timer = setTimer(() => { timer = null; fn(); }, ms); };

  function line(raw) {
    let msg;
    try { msg = JSON.parse(raw); } catch { return; }
    if (!msg || typeof msg !== "object") return;
    if (msg.status === "ready") set("ok");
    else if (msg.status === "denied") set("denied", typeof msg.reason === "string" ? msg.reason.slice(0, 40) : "");
    else if (msg.chord === "down" || msg.chord === "up") onChord(msg.chord);
    else if (msg.key === "other") onInterrupt();
  }
  function onData(chunk) {
    buffer += chunk.toString("utf8");
    let index;
    while ((index = buffer.indexOf("\n")) >= 0) { const l = buffer.slice(0, index).trim(); buffer = buffer.slice(index + 1); if (l) line(l); }
    if (buffer.length > 4096) buffer = "";
  }
  function launch() {
    if (stopped) return;
    buffer = "";
    let proc;
    try { proc = spawn(command, args); } catch (error) { set(error && error.code === "ENOENT" ? "missing" : "failed", String((error && error.message) || error).slice(0, 80)); return; }
    child = proc;
    set("starting");
    proc.stdout.on("data", chunk => { if (child === proc) onData(chunk); });
    proc.on("error", error => {
      if (child !== proc) return;
      child = null; onLost("helper-error");
      set(error && error.code === "ENOENT" ? "missing" : "failed", String((error && error.message) || error).slice(0, 80));
    });
    proc.on("exit", () => {
      if (child !== proc) return;
      child = null;
      onLost("helper-exit");
      if (stopped) return;
      if (status.state === "denied") { arm(launch, DENIED_RETRY_MS); return; }
      const t = now();
      crashes = crashes.filter(at => t - at < RESTART_WINDOW_MS); crashes.push(t);
      if (crashes.length > MAX_RESTARTS) { set("failed", "crash-loop"); return; }
      set("starting");
      arm(launch, BACKOFF_MS[Math.min(crashes.length - 1, BACKOFF_MS.length - 1)]);
    });
  }
  return {
    start() { stopped = false; if (!child && !timer) launch(); },
    retry() { crashes = []; if (timer) { clearTimer(timer); timer = null; } if (child) { const old = child; child = null; try { old.kill(); } catch { /* gone */ } } stopped = false; launch(); },
    stop() { stopped = true; if (timer) { clearTimer(timer); timer = null; } if (child) { const old = child; child = null; try { old.kill(); } catch { /* gone */ } } set("stopped"); },
    status: () => ({ ...status }),
  };
}

module.exports = { createHelperSupervisor, DENIED_RETRY_MS, BACKOFF_MS, MAX_RESTARTS, RESTART_WINDOW_MS };
