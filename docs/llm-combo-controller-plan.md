# LLM Combo Controller Plan

## Answer

Yes. The project can support one human utterance that the LLM expands into
multiple safe StarCraft II actions. The safe path is not to let the LLM call
arbitrary python-sc2 APIs directly. The LLM should produce a typed combo plan,
and every step should pass the existing Intent DSL, feasibility validator,
planner, executor, and narrator gates.

PvP is not currently supported. The live runner starts one commanded Terran bot
against a built-in SC2 computer opponent:

```text
Bot(Terran, CommanderLiveBot) vs Computer(Random, difficulty)
```

True player-vs-player, Battle.net ladder control, or controlling an existing
human client is outside the current runtime boundary and needs a separate
integration design.

## Current Architecture

```text
Browser / terminal command
  -> HybridCommandInterpreter
     -> LLM first and mandatory for configured sessions
     -> deprecated offline rules only for explicit --no-llm tests
  -> SC2FeasibilityValidator
  -> SC2ActionPlanner
  -> SC2RuntimeExecutor
  -> PythonSC2BotAdapter
  -> python-sc2 BotAI semantic calls
  -> SC2KoreanNarrator
  -> Web history / console output
```

The current API shape is intentionally close to "one user command request",
not "one raw SC2 action request":

```text
POST /api/command {"text": "..."}
  -> session.process_text(text)
  -> one or more outcomes
```

`session.process_text` can execute multiple validated Intent DSL steps from an
LLM combo plan. Deprecated deterministic splitting is kept only for offline
compatibility and must not be treated as the production command-understanding
path. It does not perform strategic decomposition such as
"prepare for marine pressure" -> gather idle workers, build depot, build
barracks, train marines, scout, rally defense.

## Target Architecture

```text
Human utterance
  -> LLM Combo Interpreter
     -> ComboPlan JSON
        - objective
        - language
        - steps[]
        - stop conditions
        - risk notes
  -> Combo Validator
     -> validates each step as canonical Intent DSL
     -> validates max step count, ordering, budget, and mutating/read-only mix
  -> Combo Executor
     -> executes step 1 through existing SC2CommandSession gates
     -> refreshes SC2 state after each step
     -> stops on blocked/clarification/unsafe state
  -> Combo Narrator
     -> concise strategic briefing plus per-step results
```

The LLM can plan a combo, but it cannot bypass the deterministic gates.

## ComboPlan Contract

```json
{
  "objective": "secure early Terran economy and production",
  "language": "ko",
  "steps": [
    {
      "utterance": "유휴 SCV 미네랄 채취",
      "intent": "GATHER_RESOURCE",
      "reason": "stabilize economy"
    },
    {
      "utterance": "보급고 건설",
      "intent": "BUILD_STRUCTURE",
      "reason": "avoid supply block"
    },
    {
      "utterance": "병영 건설",
      "intent": "BUILD_STRUCTURE",
      "reason": "unlock Marine production"
    }
  ],
  "stop_conditions": [
    "any step is blocked",
    "state observation is incomplete",
    "resources are insufficient for the next mutating step"
  ]
}
```

## Guardrails

1. LLM connection is mandatory before commands execute.
2. The LLM is called per human utterance, never per game frame.
3. Maximum combo length should default to 5 steps.
4. Each step must map to one of the existing canonical intents unless a future
   migration intentionally expands the DSL.
5. Each step must be validated against fresh SC2 state immediately before
   execution.
6. Execution stops on the first blocked, clarification, or partial failure that
   makes later steps unsafe.
7. The web history should show both the strategic objective and each step
   outcome, so the user can audit what the LLM did.

## Implementation Phases

### Phase 1: LLM required and dashboard UX

- Fail live startup when the selected provider key is missing.
- Reject `/api/command` when the process-local LLM key is not configured.
- Keep `/api/state` read-only available before LLM setup.
- Add English and Chinese UI chrome translations.
- Add a space-themed dashboard.
- Add a client-side strategy briefing from `/api/state`.
- Trim old chat DOM entries so the page does not grow indefinitely.
- Upgrade the briefing layout so current strategy is primary and tactical
  advice is optional behind a disclosure toggle.
- Add browser voice-input UX for the web dashboard: recording waveform bubble,
  recognized speech routed through the normal command submit path, and
  assistant "thinking" pending bubbles while command outcomes are pending.
- Track advanced building placement and coordinate selection separately in
  GitHub issue #3.

### Phase 2: Combo planning dry-run

- Add `LLMComboPlanner` with an injected fake client for tests.
- Add `ComboPlan` and `ComboStep` dataclasses.
- Validate max step count, canonical intent names, and non-empty utterances.
- Run combo planning only in dry-run tests first.

### Phase 3: Live combo execution

- Add `SC2ComboCommandSession.process_combo_text`.
- Execute steps sequentially through the existing `SC2CommandSession`.
- Refresh state before every step.
- Return one combo outcome containing a strategic briefing and child outcomes.

### Phase 4: UI controls

- Add a "combo mode" indicator.
- Show planned steps before or during execution.
- Add a cancel/stop control for queued combo execution.

## Non-goals

- No arbitrary python-sc2 method calls generated by the LLM.
- No per-frame LLM autonomy.
- No Battle.net ladder/PvP support in this phase.
- No screen/mouse automation.
