"use strict";
// Global-shortcut bookkeeping, pure over an injected `globalShortcut` (Electron's, or a fake in tests).
// Registration can fail (another app owns the key, bad accelerator, duplicate); that is reported as a boolean per name so the
// HUD can show a one-word state on the button instead of failing silently.

function createShortcutRegistry(globalShortcut, { onDebug = () => {} } = {}) {
  const registered = new Map(); // accelerator -> callback

  function unregister(accelerator) {
    if (!registered.has(accelerator)) return;
    try { globalShortcut.unregister(accelerator); } catch { /* ignore */ }
    registered.delete(accelerator);
  }
  function register(accelerator, callback) {
    unregister(accelerator);
    let ok = false;
    try { ok = Boolean(globalShortcut.register(accelerator, callback)); } catch { ok = false; }
    if (ok) registered.set(accelerator, callback);
    else onDebug(`could not register ${accelerator}`);
    return ok;
  }
  return { register, unregister, has: accelerator => registered.has(accelerator), get size() { return registered.size; }, accelerators: () => [...registered.keys()] };
}

// Emergency words are bound to accelerators by name. Returns {retreat:bool, hold:bool, stop:bool}.
// Re-registering is idempotent: previous emergency bindings are released first (so a changed accelerator never leaves
// the old one live), and two actions mapped to the same accelerator fail loudly for the second (never silently shadowed).
function createEmergencyShortcuts(registry, trigger, actions = ["retreat", "hold", "stop"]) {
  let bound = new Map(); // action -> accelerator currently registered by us
  function bind(accelerators) {
    for (const accelerator of bound.values()) registry.unregister(accelerator);
    bound = new Map();
    const status = {};
    const claimed = new Set();
    for (const action of actions) {
      const accelerator = accelerators[action];
      if (typeof accelerator !== "string" || !accelerator || claimed.has(accelerator)) { status[action] = false; continue; }
      claimed.add(accelerator);
      const ok = registry.register(accelerator, () => trigger(action));
      status[action] = ok;
      if (ok) bound.set(action, accelerator);
    }
    return status;
  }
  return { bind, unbind: () => bind({}), bound: () => Object.fromEntries(bound) };
}

module.exports = { createShortcutRegistry, createEmergencyShortcuts };
