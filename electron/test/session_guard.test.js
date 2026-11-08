const test = require("node:test");
const assert = require("node:assert/strict");
const G = require("../smoke/session_guard.js");

test("ioreg IOConsoleLocked parses Yes/No and unknown", () => {
  assert.equal(G.parseConsoleLocked('    "IOConsoleLocked" = Yes\n'), true);
  assert.equal(G.parseConsoleLocked('    "IOConsoleLocked" = No\n'), false);
  assert.equal(G.parseConsoleLocked("nothing here"), null);
});
test("black capture is dark, real content is not", () => {
  assert.equal(G.meanBrightnessBGRA(Buffer.alloc(4 * 1000)), 0);
  assert.ok(G.meanBrightnessBGRA(Buffer.alloc(4 * 1000, 128)) > G.MIN_BRIGHTNESS);
});
test("a locked or dark screen is BLOCKED, never a pass; unknown readings do not block", () => {
  assert.equal(G.evaluate({ locked: true, brightness: 120 }).ok, false);
  assert.match(G.evaluate({ locked: true, brightness: 120 }).reason, /locked/);
  assert.equal(G.evaluate({ locked: false, brightness: 0 }).ok, false);
  assert.match(G.evaluate({ locked: false, brightness: 0 }).reason, /dark/);
  assert.equal(G.evaluate({ locked: false, brightness: 90 }).ok, true);
  assert.equal(G.evaluate({ locked: null, brightness: null }).ok, true);
});
test("the smoke driver and the real-click probe both run the guard first", () => {
  const fs = require("node:fs"), path = require("node:path");
  for (const f of ["driver.js", "e2e_real.js", "focus_probe.js"]) assert.match(fs.readFileSync(path.join(__dirname, "..", "smoke", f), "utf8"), /session_guard/, f);
});
