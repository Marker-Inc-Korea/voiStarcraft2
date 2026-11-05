"use strict";
// Pure Realtime event -> voice state machine. No I/O, no timers: voice.js owns the socket and
// calls into this module, which makes every transition testable with a scripted event list.
//
// Public voice states (contract with the renderer / overlay):
//   idle | connecting | listening | thinking | acting | error
// `active` tells "the user armed voice" apart from a plain idle, so "idle + active" is the
// hands-free "ready, speak any time" condition.
const BENIGN_ERROR_CODES = new Set(["input_audio_buffer_commit_empty", "response_cancel_not_active", "conversation_already_has_active_response"]);
const MAX_TRANSCRIPT = 2000;
const TOOL_NAME = "submit_command";

function createVoiceMachine({ now = Date.now, onChange = () => {} } = {}) {
  let connection = "off"; // off | connecting | ready
  let active = false;
  let errorReason = "";
  let infoReason = ""; // non-error hint, e.g. "no command recognised"
  let holdActing = false;
  let nextTurnId = 1;
  let speakingTurn = null;
  let lastState = "";
  let lastKey = "";
  const turns = new Map(); // turn id -> turn
  const byItem = new Map(); // realtime item id -> turn
  const awaitingTool = []; // FIFO of turns that finished speaking and wait for submit_command
  let intentInFlight = 0;
  let lastTurn = null;
  const byResponse = new Map(); // realtime response id -> turn (so a late/cancelled response never lands on the wrong utterance)
  const ruleAwaiting = []; // turns the rule layer executed whose model response has not been created yet (to be cancelled on response.created)
  const seenCalls = new Set(); // function-call ids already turned into an intent (a replayed event must not execute twice)

  function derive() {
    if (!active) return "idle";
    if (errorReason) return "error";
    if (connection !== "ready") return "connecting";
    if (speakingTurn) return "listening";
    if (awaitingTool.some(waitsForModel)) return "thinking";
    if (intentInFlight > 0 || holdActing) return "acting";
    return "idle";
  }
  function snapshot() {
    const turn = speakingTurn || awaitingTool.find(waitsForModel) || lastTurn;
    return {
      state: derive(), active, reason: errorReason || infoReason,
      turn: turn ? publicTurn(turn) : null,
    };
  }
  function publicTurn(turn) {
    return {
      id: turn.id, transcript: turn.transcript, partial: turn.partial, status: turn.status,
      speechMs: turn.speechStoppedAt && turn.speechStartedAt ? turn.speechStoppedAt - turn.speechStartedAt : null,
    };
  }
  function changed() {
    const snap = snapshot();
    const key = JSON.stringify(snap);
    lastState = snap.state;
    if (key !== lastKey) { lastKey = key; onChange(snap); }
    return snap;
  }
  function turnFor(itemId) {
    if (itemId && byItem.has(itemId)) return byItem.get(itemId);
    return null;
  }
  function newTurn(itemId) {
    const turn = { id: nextTurnId++, itemId: itemId || "", speechStartedAt: now(), speechStoppedAt: 0, transcript: "", partial: "", status: "speaking", transcriptError: "" };
    turns.set(turn.id, turn);
    if (itemId) byItem.set(itemId, turn);
    lastTurn = turn;
    if (turns.size > 24) { const oldest = turns.keys().next().value; const gone = turns.get(oldest); turns.delete(oldest); if (gone && gone.itemId) byItem.delete(gone.itemId); }
    return turn;
  }
  // A turn the deterministic rule layer owns (pending or done) no longer waits for the model: it is shown as "acting".
  function waitsForModel(turn) { return turn.handledBy !== "pending_rule" && turn.handledBy !== "rule"; }
  function rememberResponse(responseId, turn) {
    byResponse.set(responseId, turn); turn.responseId = responseId;
    if (byResponse.size > 48) byResponse.delete(byResponse.keys().next().value);
  }
  function claimResponseTurn(responseId) {
    const turn = typeof responseId === "string" ? byResponse.get(responseId) : null;
    if (turn) removeAwaiting(turn);
    return turn || null;
  }
  // Which utterance does a tool call belong to? Its response id when known, else the oldest unanswered utterance
  // (responses answer utterances in order; rule-executed turns whose response is still pending count as unanswered).
  function turnForCall(responseId) {
    const known = claimResponseTurn(responseId);
    if (known) return known;
    const oldest = [...ruleAwaiting.filter(t => !t.responseId), ...awaitingTool].sort((a, b) => a.id - b.id)[0];
    if (oldest) { const at = ruleAwaiting.indexOf(oldest); if (at >= 0) ruleAwaiting.splice(at, 1); removeAwaiting(oldest); return oldest; }
    return speakingTurn ? speakingTurn : lastTurn;
  }
  function removeAwaiting(turn) { const index = awaitingTool.indexOf(turn); if (index >= 0) awaitingTool.splice(index, 1); }

  function handle(event) {
    const effects = [];
    if (!event || typeof event.type !== "string") return effects;
    switch (event.type) {
      case "input_audio_buffer.speech_started": {
        if (!active) break;
        if (speakingTurn) { speakingTurn.status = "abandoned"; removeAwaiting(speakingTurn); }
        errorReason = ""; infoReason = "";
        speakingTurn = newTurn(event.item_id);
        effects.push({ type: "speech_started", turn: publicTurn(speakingTurn) });
        break;
      }
      case "input_audio_buffer.speech_stopped": {
        if (!active) break;
        const turn = turnFor(event.item_id) || speakingTurn;
        if (!turn) break;
        if (event.item_id && !turn.itemId) { turn.itemId = event.item_id; byItem.set(event.item_id, turn); }
        turn.speechStoppedAt = now();
        turn.status = "awaiting_tool";
        if (speakingTurn === turn) speakingTurn = null;
        if (!awaitingTool.includes(turn)) awaitingTool.push(turn);
        effects.push({ type: "speech_stopped", turn: publicTurn(turn) });
        break;
      }
      case "input_audio_buffer.committed": {
        const turn = turnFor(event.item_id) || speakingTurn || [...awaitingTool].reverse().find(t => !t.itemId);
        if (turn && event.item_id && !turn.itemId) { turn.itemId = event.item_id; byItem.set(event.item_id, turn); }
        break;
      }
      case "conversation.item.input_audio_transcription.delta": {
        const turn = turnFor(event.item_id) || lastTurn;
        if (!turn || typeof event.delta !== "string") break;
        turn.partial = (turn.partial + event.delta).slice(-MAX_TRANSCRIPT);
        effects.push({ type: "transcript", partial: true, turn: publicTurn(turn) });
        break;
      }
      case "conversation.item.input_audio_transcription.completed": {
        const turn = turnFor(event.item_id) || lastTurn;
        if (!turn || typeof event.transcript !== "string") break;
        turn.transcript = event.transcript.trim().slice(0, MAX_TRANSCRIPT);
        turn.partial = "";
        effects.push({ type: "transcript", partial: false, turn: publicTurn(turn) });
        break;
      }
      case "conversation.item.input_audio_transcription.failed": {
        const turn = turnFor(event.item_id) || lastTurn;
        if (turn) turn.transcriptError = String(event.error && event.error.message || "transcription failed").slice(0, 160);
        break;
      }
      case "response.created": {
        const response = event.response || {};
        if (!active || typeof response.id !== "string") break;
        // responses are created in utterance order: the oldest turn without a response owns this one
        const candidates = [...ruleAwaiting, ...awaitingTool].filter(t => !t.responseId).sort((a, b) => a.id - b.id);
        const turn = candidates[0];
        if (turn) {
          rememberResponse(response.id, turn);
          const at = ruleAwaiting.indexOf(turn); if (at >= 0) ruleAwaiting.splice(at, 1);
          if (turn.handledBy === "rule") effects.push({ type: "cancel_response", responseId: response.id }); // the rule already executed this utterance
        }
        break;
      }
      case "response.function_call_arguments.done": {
        if (!active) break;
        if (event.call_id && seenCalls.has(event.call_id)) break;
        const turn = turnForCall(event.response_id);
        const calledAt = now();
        if (event.name !== TOOL_NAME) { effects.push({ type: "unexpected_tool", name: String(event.name || ""), turn: turn ? publicTurn(turn) : null }); break; }
        let args;
        try { args = JSON.parse(event.arguments); } catch { args = null; }
        if (!args || typeof args !== "object" || Array.isArray(args)) {
          if (turn) turn.status = "failed";
          effects.push({ type: "bad_tool_arguments", turn: turn ? publicTurn(turn) : null });
          infoReason = "음성 명령 인자를 해석하지 못했습니다";
          break;
        }
        if (turn && turn.handledBy === "rule") { effects.push({ type: "tool_call_ignored", turn: publicTurn(turn), reason: "rule_handled" }); break; } // deterministic rule already executed this utterance
        if (turn) { turn.status = "calling"; turn.toolCallAt = calledAt; turn.callId = event.call_id || ""; }
        if (event.call_id) { seenCalls.add(event.call_id); if (seenCalls.size > 96) seenCalls.delete(seenCalls.values().next().value); }
        intentInFlight += 1; holdActing = false; // thinking -> acting directly; the caller pairs this with intentFinished()
        const callEffect = { type: "tool_call", args, turn: turn ? publicTurn(turn) : null, turnId: turn ? turn.id : 0, speechEndToToolCall: turn && turn.speechStoppedAt ? calledAt - turn.speechStoppedAt : null, speechMs: turn && turn.speechStoppedAt ? turn.speechStoppedAt - turn.speechStartedAt : null };
        if (turn && turn.handledBy === "pending_rule") { turn.deferredCall = callEffect; break; } // the rule request is still in flight: keep the model's call in case the rule is released
        effects.push(callEffect);
        break;
      }
      case "response.done": {
        const response = event.response || {};
        const doneTurn = typeof response.id === "string" ? byResponse.get(response.id) || null : null;
        if (doneTurn) byResponse.delete(response.id);
        const ruleOwned = doneTurn && (doneTurn.handledBy === "rule" || doneTurn.handledBy === "pending_rule");
        if (ruleOwned) { removeAwaiting(doneTurn); break; } // cancelled / completed model response of a rule-executed utterance: nothing to report
        if (response.status === "failed" || response.status === "incomplete") {
          const detail = response.status_details || {};
          const reason = String((detail.error && (detail.error.message || detail.error.code)) || detail.reason || response.status).slice(0, 200);
          const turn = doneTurn ? (removeAwaiting(doneTurn), doneTurn) : awaitingTool.shift();
          if (turn) turn.status = "failed";
          if (active) errorReason = `Realtime 응답 실패: ${reason}`;
          effects.push({ type: "response_failed", reason });
        } else if (awaitingTool.length && !speakingTurn) {
          const hadCall = Array.isArray(response.output) && response.output.some(item => item && item.type === "function_call");
          if (!hadCall) {
            const turn = doneTurn ? (awaitingTool.includes(doneTurn) ? (removeAwaiting(doneTurn), doneTurn) : null) : awaitingTool.shift();
            if (turn && waitsForModel(turn)) {
              turn.status = "no_command";
              infoReason = "명령으로 인식되지 않았습니다";
              effects.push({ type: "no_command", turn: publicTurn(turn) });
            }
          }
        }
        break;
      }
      case "error": {
        const err = event.error || {};
        if (BENIGN_ERROR_CODES.has(err.code)) break;
        if (active) errorReason = String(err.message || err.code || "Realtime error").slice(0, 240);
        effects.push({ type: "server_error", code: String(err.code || ""), reason: errorReason });
        break;
      }
      default: break;
    }
    changed();
    return effects;
  }

  return {
    handle, snapshot,
    isQuiet: () => !speakingTurn && !awaitingTool.length && intentInFlight === 0,
    setActive(value) {
      active = Boolean(value);
      if (!active) { connection = "off"; errorReason = ""; infoReason = ""; speakingTurn = null; awaitingTool.length = 0; ruleAwaiting.length = 0; intentInFlight = 0; holdActing = false; }
      return changed();
    },
    setConnection(value) { connection = value; if (value === "ready") errorReason = ""; if (value !== "ready") { speakingTurn = null; awaitingTool.length = 0; ruleAwaiting.length = 0; } return changed(); },
    fail(reason) { errorReason = String(reason || "error").slice(0, 240); speakingTurn = null; return changed(); },
    clearError() { errorReason = ""; infoReason = ""; return changed(); },
    // Deterministic-rule path. beginRule claims the utterance (the model's later tool call is parked/ignored);
    // finishRule(handled) keeps the claim (and drops the parked call) or releases it and returns the parked model call to run.
    beginRule(turnId) {
      const turn = turns.get(turnId);
      if (!turn || turn.handledBy || !active || (turn.status !== "awaiting_tool" && turn.status !== "speaking")) return null; // the model's call already ran: too late
      turn.handledBy = "pending_rule"; turn.status = "rule_pending"; intentInFlight += 1; holdActing = false; changed();
      return { responseId: turn.responseId || "", speechStoppedAt: turn.speechStoppedAt || 0 };
    },
    finishRule(turnId, { handled, ok }) {
      const turn = turns.get(turnId);
      if (!turn) return { responseId: "", deferred: null };
      const deferred = turn.deferredCall || null; turn.deferredCall = null;
      intentInFlight = Math.max(0, intentInFlight - 1);
      if (handled) {
        turn.handledBy = "rule"; turn.status = ok ? "submitted" : "failed"; holdActing = Boolean(ok);
        if (deferred) { intentInFlight = Math.max(0, intentInFlight - 1); } // the parked model call never runs
        removeAwaiting(turn);
        if (!turn.responseId) { ruleAwaiting.push(turn); if (ruleAwaiting.length > 8) ruleAwaiting.shift(); }
        changed();
        return { responseId: turn.responseId || "", deferred: null };
      }
      turn.handledBy = ""; turn.status = deferred ? "calling" : "awaiting_tool"; changed();
      return { responseId: "", deferred };
    },
    intentFinished(turnId, ok) { intentInFlight = Math.max(0, intentInFlight - 1); const turn = turns.get(turnId); if (turn) turn.status = ok ? "submitted" : "failed"; holdActing = ok; changed(); },
    setHoldActing(value) { holdActing = Boolean(value); changed(); },
    markTurn(turnId, status) { const turn = turns.get(turnId); if (turn) turn.status = status; },
    getTurn(turnId) { const turn = turns.get(turnId); return turn ? publicTurn(turn) : null; },
  };
}
module.exports = { createVoiceMachine, TOOL_NAME };
