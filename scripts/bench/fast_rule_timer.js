"use strict";
// Line-oriented JS rule worker for latency_trace.py: stdin {"id","text"} -> stdout {"id","hit","family","action","us"}.
// `us` = mean microseconds of electron/fast_rules.matchFastRule over 200 calls (process.hrtime inside this process,
// so the Python side never contaminates the measurement). `{"cmd":"session_update","env":{},"prefs":{}}` returns the
// exact session.update the Electron client would send (voice.defaultSessionUpdate).
const path = require("node:path");
const readline = require("node:readline");
const electron = path.resolve(__dirname, "..", "..", "electron");
const { matchFastRule } = require(path.join(electron, "fast_rules.js"));
const { defaultSessionUpdate } = require(path.join(electron, "voice.js"));
const rl = readline.createInterface({ input: process.stdin });
rl.on("line", line => {
  let msg;
  try { msg = JSON.parse(line); } catch { return; }
  if (msg.cmd === "session_update") { process.stdout.write(JSON.stringify({ id: msg.id, update: defaultSessionUpdate(msg.env || {}, msg.prefs || {}) }) + "\n"); return; }
  const N = 200;
  matchFastRule(msg.text); // warm the regex JIT once, like a long-lived app
  const t0 = process.hrtime.bigint();
  let hit = null;
  for (let i = 0; i < N; i += 1) hit = matchFastRule(msg.text);
  const us = Number(process.hrtime.bigint() - t0) / 1000 / N;
  process.stdout.write(JSON.stringify({ id: msg.id, hit: Boolean(hit), family: hit ? hit.family : "", action: hit ? hit.action : "", ability: hit ? hit.ability : "", us }) + "\n");
});
