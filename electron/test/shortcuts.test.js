const test = require("node:test");
const assert = require("node:assert/strict");
const { createShortcutRegistry, createEmergencyShortcuts } = require("../shortcuts.js");

// A fake Electron globalShortcut: `taken` accelerators are owned by another app and fail to register.
function fakeShortcut(taken = [], throwing = []) {
  const live = new Map(); const log = [];
  return { live, log, register(accelerator, callback) { log.push(["register", accelerator]); if (throwing.includes(accelerator)) throw new Error("bad accelerator"); if (taken.includes(accelerator) || live.has(accelerator)) return false; live.set(accelerator, callback); return true; }, unregister(accelerator) { log.push(["unregister", accelerator]); live.delete(accelerator); } };
}
const DEFAULTS = { retreat: "CommandOrControl+Shift+R", hold: "CommandOrControl+Shift+H", stop: "CommandOrControl+Shift+X" };

test("emergency shortcuts register once each and trigger exactly their own word", () => {
  const fake = fakeShortcut(); const fired = [];
  const em = createEmergencyShortcuts(createShortcutRegistry(fake), action => fired.push(action));
  assert.deepEqual(em.bind(DEFAULTS), { retreat: true, hold: true, stop: true });
  fake.live.get(DEFAULTS.hold)(); fake.live.get(DEFAULTS.retreat)(); fake.live.get(DEFAULTS.stop)();
  assert.deepEqual(fired, ["hold", "retreat", "stop"]);
});
test("registration failure is reported per word (never silent) and does not block the others", () => {
  const fake = fakeShortcut([DEFAULTS.hold], [DEFAULTS.stop]); const debug = [];
  const em = createEmergencyShortcuts(createShortcutRegistry(fake, { onDebug: m => debug.push(m) }), () => {});
  assert.deepEqual(em.bind(DEFAULTS), { retreat: true, hold: false, stop: false });
  assert.deepEqual(em.bound(), { retreat: DEFAULTS.retreat });
  assert.equal(debug.length, 2);
});
test("changing an accelerator releases the old one; the same accelerator cannot be claimed twice", () => {
  const fake = fakeShortcut(); const registry = createShortcutRegistry(fake);
  const em = createEmergencyShortcuts(registry, () => {});
  em.bind(DEFAULTS);
  const status = em.bind({ ...DEFAULTS, hold: "CommandOrControl+Alt+H" });
  assert.deepEqual(status, { retreat: true, hold: true, stop: true });
  assert.ok(!fake.live.has(DEFAULTS.hold) && fake.live.has("CommandOrControl+Alt+H"));
  const dup = em.bind({ retreat: "CommandOrControl+Shift+R", hold: "CommandOrControl+Shift+R", stop: "CommandOrControl+Shift+X" });
  assert.deepEqual(dup, { retreat: true, hold: false, stop: true });
  em.unbind(); assert.equal(fake.live.size, 0); assert.equal(registry.size, 0);
});
test("missing accelerator is a reported failure, not a crash", () => {
  const em = createEmergencyShortcuts(createShortcutRegistry(fakeShortcut()), () => {});
  assert.deepEqual(em.bind({ retreat: "CommandOrControl+Shift+R" }), { retreat: true, hold: false, stop: false });
});
test("registry: re-registering the same accelerator replaces it; unregister of an unknown key is a no-op", () => {
  const fake = fakeShortcut(); const registry = createShortcutRegistry(fake);
  assert.equal(registry.register("A", () => 1), true); assert.equal(registry.register("A", () => 2), true);
  assert.equal(fake.live.get("A")(), 2); registry.unregister("nope"); assert.equal(registry.has("A"), true);
});
