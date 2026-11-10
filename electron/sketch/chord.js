"use strict";
// Pure state machine for the sketch chord (hold Control+Option, nothing else). No I/O: the native helper's {"chord":"down"|"up"}
// lines, "another key was pressed" notices, UI cancels and the fallback toggle shortcut all funnel into one machine, and the
// machine emits exactly {type: "start"|"end"|"cancel", source: "chord"|"toggle", reason?}.
//
// Safety rule: only "end" lets the caller send the drawn route. Anything that makes the end uncertain (another key, helper loss,
// disabling the feature, Esc) is a "cancel", never an "end".
//   idle -> (chord down) pending -> (debounce) active -> (chord up) idle                 [end]
//   pending/active + another key  -> blocked (active emits cancel:key) -> (chord up) idle
//   idle -> (toggle) toggled -> (toggle) idle [end] | cancel() idle [cancel]
const DEFAULT_DEBOUNCE_MS = 120;

function createChordMachine({ debounceMs = DEFAULT_DEBOUNCE_MS, now = Date.now, setTimer = setTimeout, clearTimer = clearTimeout, onEvent = () => {} } = {}) {
  let state = "idle"; // idle | pending | active | blocked | toggled
  let timer = null;
  let enabled = true;
  const emit = (type, source, reason) => { const event = { type, source, at: now() }; if (reason) event.reason = reason; onEvent(event); };
  const stopTimer = () => { if (timer) { clearTimer(timer); timer = null; } };

  function chord(direction) {
    if (!enabled) return;
    if (direction === "down") {
      if (state !== "idle") return; // duplicate down, or blocked/toggled: ignore
      state = "pending";
      stopTimer();
      timer = setTimer(() => { timer = null; if (state === "pending") { state = "active"; emit("start", "chord"); } }, debounceMs);
    } else if (direction === "up") {
      if (state === "pending") { stopTimer(); state = "idle"; } // a tap shorter than the debounce is not a sketch
      else if (state === "active") { state = "idle"; emit("end", "chord"); }
      else if (state === "blocked") state = "idle";
      // toggled: a chord release is unrelated to a toggled sketch
    }
  }
  // Any other key pressed while the chord is held (Ctrl+Opt+D, Ctrl+Opt+Up, ...) means it was not a sketch chord.
  function interrupt() {
    if (state === "pending") { stopTimer(); state = "blocked"; }
    else if (state === "active") { state = "blocked"; emit("cancel", "chord", "key"); }
  }
  function toggle() {
    if (!enabled) return;
    if (state === "idle") { state = "toggled"; emit("start", "toggle"); }
    else if (state === "toggled") { state = "idle"; emit("end", "toggle"); }
    // pending/active/blocked: the physical chord owns the sketch
  }
  function cancel(reason = "cancel") {
    if (state === "active") { state = "blocked"; emit("cancel", "chord", reason); } // the keys are still down: wait for the release
    else if (state === "toggled") { state = "idle"; emit("cancel", "toggle", reason); }
  }
  // The helper (our only view of the keys) is gone or restarted: a held chord can no longer be tracked.
  function reset(reason = "reset") {
    if (state === "active") { state = "idle"; emit("cancel", "chord", reason); }
    else if (state === "pending" || state === "blocked") { stopTimer(); state = "idle"; }
  }
  function setEnabled(next) {
    enabled = Boolean(next);
    if (enabled) return;
    stopTimer();
    if (state === "active") emit("cancel", "chord", "disabled");
    else if (state === "toggled") emit("cancel", "toggle", "disabled");
    state = "idle";
  }
  return {
    chord, interrupt, toggle, cancel, reset, setEnabled,
    state: () => (state === "toggled" ? "active" : state),
    source: () => (state === "active" ? "chord" : state === "toggled" ? "toggle" : ""),
    isActive: () => state === "active" || state === "toggled",
  };
}

module.exports = { createChordMachine, DEFAULT_DEBOUNCE_MS };
