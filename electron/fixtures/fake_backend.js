"use strict";
// Tiny local stand-in for the Commander HTTP service + OpenAI Realtime WS, for smoke runs and tests.
// It only mimics response *shapes* the real backend serves; it is NOT evidence of real SC2 behaviour.
//
//   node fixtures/fake_backend.js [--port 8765] [--runtime dry_run_fake_bot|live|missing]
//
// Control plane (loopback only):
//   POST /__ctl/realtime  {event:{...}}      -> push a Realtime server event to the connected client
//   POST /__ctl/speak     {transcript, tool_arguments, speech_ms?, think_ms?}  -> scripted spoken turn
//   POST /__ctl/runtime   {kind}             -> switch direct_runtime reporting
//   GET  /__ctl/audio                        -> {bytes, chunks} received by the fake Realtime socket
const http = require("node:http");
const { WebSocketServer } = require("ws");

function createFakeBackend({ port = 0, runtime = "dry_run_fake_bot" } = {}) {
  const state = { runtime, commands: [], seq: 0, events: [], audioBytes: 0, audioChunks: 0, sockets: new Set(), sessionUpdates: 0, intents: [], secrets: 0 };
  const startedAt = Date.now();
  const sec = () => (Date.now() - startedAt) / 1000;
  const json = (res, code, body) => { const data = JSON.stringify(body); res.writeHead(code, { "content-type": "application/json", "content-length": Buffer.byteLength(data) }); res.end(data); };
  const readBody = req => new Promise(resolve => { const chunks = []; req.on("data", c => chunks.push(c)); req.on("end", () => { try { resolve(JSON.parse(Buffer.concat(chunks).toString() || "{}")); } catch { resolve({}); } }); });
  const directRuntime = () => state.runtime === "missing" ? undefined : state.runtime === "live" ? { kind: "live", evidence: "fixture: live session" } : { kind: "dry_run_fake_bot", evidence: "fixture: web_gui --dry-run (DemoFakeBotAI)" };
  const TIER_OF = { macro: "macro", operation: "operation", micro: "micro", emergency: "emergency" };

  function pushEvent(status, command_text, narration) { state.seq += 1; state.events.push({ seq: state.seq, status, command_text, narration }); }

  // Progressive truth: each op advances one engine stage every ~step seconds; the effect only appears when scripted.
  function buildOperation(cmd, spec, index) {
    const age = (Date.now() - cmd.at) / 1000;
    const step = cmd.step || 1.1;
    const names = ["parsed", "reduced", "consumed_by_manager", "queued_or_assigned", "order_issued", "action_issued", "effect_observed"];
    const reached = Math.floor(age / step);
    const limit = spec.stopAt == null ? names.length : spec.stopAt;
    const stages = names.slice(0, Math.min(reached, limit)).map(name => ({ name, ok: true, manager: "OperationDirector", evidence: {} }));
    const blocked = spec.blocked && reached > limit;
    const execution = { command_id: cmd.update_id, operation_id: spec.operation_id, state: stages.length ? stages[stages.length - 1].name : "published", stages, failed: Boolean(blocked), expired: false, superseded: false, completed: false, blocker_reason: blocked ? spec.blocked : "" };
    return {
      operation_id: spec.operation_id, update_id: cmd.update_id, command_text: cmd.text, mission: spec.mission, active: !blocked, transport_status: "published",
      consumption_status: blocked ? "pending_consumption" : reached >= 4 ? "consumed" : "pending_consumption", disposition: blocked ? "blocked" : "active",
      update: { update_id: cmd.update_id, vector: { goal: spec.goal, command_layer: spec.tier, composition_requirements: spec.units, route_intent: spec.route && { route_type: spec.route }, target_intent: spec.target && { target_type: spec.target } } },
      intervention: { command_execution: execution }, command_queue: { command_layer: spec.tier }, compile_result: {},
    };
  }
  function specsFor(args, transcript) {
    const ops = Array.isArray(args && args.operations) && args.operations.length ? args.operations : null;
    const tier = TIER_OF[args && args.command_layer] || "operation";
    if (!ops) return [{ operation_id: `op-${state.seq}-a`, tier, mission: tier === "emergency" ? "emergency" : tier === "macro" ? "production" : "operation", goal: (args && args.goal) || transcript, units: [] }];
    return ops.slice(0, 4).map((op, i) => ({
      operation_id: op.operation_id || `op-${state.seq}-${i}`, tier, mission: /scout|정찰/.test(`${op.task_type}${op.goal}`) ? "scouting" : "attack", goal: op.goal || args.goal,
      units: (op.unit_classes || []).map(u => ({ unit_type: u, count: op.count })), route: op.route_type, target: op.target_type,
      stopAt: op.fixture_stop_at, blocked: op.fixture_blocked,
    }));
  }
  function acceptCommand(text, args, source) {
    state.seq += 1;
    const update_id = `mm-${Date.now().toString(36)}-${state.seq}`;
    const tier = TIER_OF[args && args.command_layer] || (/후퇴|retreat/.test(text) ? "emergency" : "operation");
    const cmd = { update_id, text, at: Date.now(), tier, specs: specsFor(args || { command_layer: tier }, text), step: 1.1, source };
    state.commands.push(cmd);
    pushEvent("received", text, `명령 접수: ${text}`);
    return cmd;
  }
  function statusPayload() {
    const operations = state.commands.flatMap(cmd => cmd.specs.map((spec, i) => buildOperation(cmd, spec, i)));
    const latest = state.commands[state.commands.length - 1];
    return {
      status: operations.length ? "published" : "idle", operations, operation_summary: { total: operations.length },
      latest_request: latest ? { update_id: latest.update_id, status: "published", command_text: latest.text, consumption_status: "pending_consumption", is_active_update: true } : null,
      command_queue: latest ? { command_layer: latest.tier } : {}, consumption_status: "pending_consumption",
      ...(directRuntime() ? { direct_runtime: directRuntime() } : {}),
    };
  }

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");
    const p = url.pathname;
    if (req.method === "GET" && p === "/api/micromachine/status") return json(res, 200, statusPayload());
    if (req.method === "GET" && p === "/api/runtime/status") return json(res, 200, { enabled: true, status: "running", mode: "dry_run" });
    if (req.method === "GET" && p === "/api/history") { const after = Number(url.searchParams.get("after") || 0); return json(res, 200, { events: state.events.filter(e => e.seq > after), latest: state.seq }); }
    if (req.method === "GET" && (p === "/api/direct/registry" || p === "/api/direct/capabilities")) return json(res, 200, {});
    if (req.method === "GET" && p === "/__ctl/audio") return json(res, 200, { bytes: state.audioBytes, chunks: state.audioChunks, sessionUpdates: state.sessionUpdates, intents: state.intents, secrets: state.secrets });
    if (req.method === "POST" && p === "/api/micromachine/modulate") {
      const body = await readBody(req);
      const text = String(body.text || "");
      if (/refuse|거절/.test(text)) return json(res, 400, { accepted: false, error: "지원하지 않는 명령입니다 (fixture)" });
      if (/retreat|후퇴/.test(text) && state.runtime === "live" && process.env.FIXTURE_DIRECT_CONFLICT) return json(res, 409, { accepted: false, status: "direct_control_conflict", error: "direct_control_conflict: Direct lease owns the units" });
      const cmd = acceptCommand(text, null, "text");
      return json(res, 202, { accepted: true, ok: true, queued: true, status: "queued", update_id: cmd.update_id, command_text: text, consumption_status: "pending_compile", ...(directRuntime() ? { direct_runtime: directRuntime() } : {}) });
    }
    if (req.method === "POST" && p === "/api/realtime/session") {
      state.secrets += 1;
      const addr = server.address();
      return json(res, 200, { client_secret: "ek_fixture_secret_0000", expires_at: Math.floor(Date.now() / 1000) + 600, model: "gpt-realtime", ws_url: `ws://127.0.0.1:${addr.port}/v1/realtime?model=gpt-realtime`, tools: [{ type: "function", name: "submit_command" }], tier_hint: "fixture" });
    }
    if (req.method === "POST" && p === "/api/realtime/intent") {
      const body = await readBody(req);
      state.intents.push(body);
      const args = body.tool_arguments || {};
      const cmd = acceptCommand(String(body.transcript || args.goal || ""), args, "voice");
      return json(res, 202, { accepted: true, ok: true, queued: true, status: "queued", update_id: cmd.update_id, tier: cmd.tier, command_text: cmd.text, consumption_status: "pending_compile", ...(directRuntime() ? { direct_runtime: directRuntime() } : {}) });
    }
    if (req.method === "POST" && p === "/__ctl/runtime") { const body = await readBody(req); state.runtime = String(body.kind || "missing"); return json(res, 200, { runtime: state.runtime }); }
    if (req.method === "POST" && p === "/__ctl/realtime") { const body = await readBody(req); broadcast(body.event); return json(res, 200, { sent: state.sockets.size }); }
    if (req.method === "POST" && p === "/__ctl/speak") { const body = await readBody(req); speak(body); return json(res, 200, { scripted: true }); }
    json(res, 404, { error: "not found" });
  });

  const wss = new WebSocketServer({ noServer: true });
  server.on("upgrade", (req, socket, head) => {
    if (!/^\/v1\/realtime/.test(req.url) || req.headers.authorization !== "Bearer ek_fixture_secret_0000" || req.headers["user-agent"] !== "curl/8.7.1") { socket.write("HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n"); socket.destroy(); return; }
    wss.handleUpgrade(req, socket, head, ws => {
      state.sockets.add(ws);
      ws.send(JSON.stringify({ type: "session.created", session: {} }));
      ws.on("close", () => state.sockets.delete(ws));
      ws.on("message", data => {
        let event; try { event = JSON.parse(String(data)); } catch { return; }
        if (event.type === "session.update") { state.sessionUpdates += 1; ws.send(JSON.stringify({ type: "session.updated", session: event.session })); }
        else if (event.type === "input_audio_buffer.append") { state.audioChunks += 1; state.audioBytes += Buffer.from(event.audio || "", "base64").length; }
        else if (event.type === "conversation.item.delete") ws.send(JSON.stringify({ type: "conversation.item.deleted", item_id: event.item_id }));
      });
    });
  });
  function broadcast(event) { for (const ws of state.sockets) ws.send(JSON.stringify(event)); }
  let itemCounter = 0;
  function speak({ transcript = "", tool_arguments = {}, speech_ms = 900, think_ms = 600 }) {
    const item_id = `item_fx_${++itemCounter}`;
    broadcast({ type: "input_audio_buffer.speech_started", item_id, audio_start_ms: 0 });
    const partial = transcript.split(" ");
    partial.forEach((word, i) => setTimeout(() => broadcast({ type: "conversation.item.input_audio_transcription.delta", item_id, delta: `${word} ` }), (speech_ms * (i + 1)) / (partial.length + 1)));
    setTimeout(() => {
      broadcast({ type: "input_audio_buffer.speech_stopped", item_id, audio_end_ms: speech_ms });
      broadcast({ type: "input_audio_buffer.committed", item_id });
      setTimeout(() => broadcast({ type: "conversation.item.input_audio_transcription.completed", item_id, transcript }), 120);
      setTimeout(() => {
        broadcast({ type: "response.function_call_arguments.done", name: "submit_command", call_id: `call_${itemCounter}`, arguments: JSON.stringify(tool_arguments) });
        broadcast({ type: "response.done", response: { status: "completed", output: [{ type: "function_call", id: `fc_${itemCounter}` }] } });
      }, think_ms);
    }, speech_ms);
  }

  return {
    state, server,
    listen: () => new Promise(resolve => server.listen(port, "127.0.0.1", () => resolve(server.address().port))),
    close: () => new Promise(resolve => { for (const ws of state.sockets) ws.terminate(); server.close(() => resolve()); }),
    broadcast, speak,
  };
}

module.exports = { createFakeBackend };
if (require.main === module) {
  const args = process.argv.slice(2);
  const opt = name => { const i = args.indexOf(`--${name}`); return i >= 0 ? args[i + 1] : undefined; };
  const backend = createFakeBackend({ port: Number(opt("port") || 8765), runtime: opt("runtime") || "dry_run_fake_bot" });
  backend.listen().then(p => console.log(`fake backend on http://127.0.0.1:${p}`));
}
