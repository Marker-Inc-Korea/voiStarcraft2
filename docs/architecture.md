# Phase 0 Component Architecture

ToyCraft Commander is a text-first Phase 0 simulator. Its architecture validates
the commander loop before SC2 or BWAPI integration: Korean command text becomes a
typed Intent DSL, the DSL is validated against a ToyCraft state snapshot,
feasible commands mutate state through deterministic rules, and the result is
narrated back to the commander.

## Runtime Flow

```text
Korean command text
  -> CommandInterpreter
  -> typed Intent DSL payload
  -> IntentFeasibilityValidator
  -> ToyCraftExecutorInterface
  -> ToyCraftRuleEngine
  -> StateNarrator
  -> Korean commander response
```

The pipeline is intentionally one-way. Unsupported interpretation results stop
before validation. Rejected validation results stop before execution. Executor
failures return a blocked execution result without mutating state.

For implementation-facing payload and interface details, see
[contracts.md](contracts.md). That contract document is the stable reference for
the typed Intent DSL, `IntentFeasibilityValidator`, `ToyCraftRuleEngineInterface`,
and `ToyCraftExecutorInterface`.

## End-to-End Command Data Flow

One commander command travels through the system as a typed data handoff, not as
free-form prose shared between layers:

1. Korean input enters the pipeline as `CommandProcessingRequest.command_text`
   with the current immutable `ToyCraftState` snapshot.
2. `CommandInterpreter.interpret(command_text)` preserves the original text and
   maps the Korean utterance to the nearest supported canonical intent. A
   successful result contains an intent-specific `IntentPayload`; unsupported,
   malformed, or ambiguous text returns a clarification result with alternatives
   and no payload.
3. The resolved payload is the typed Intent DSL contract. It always carries the
   common fields `intent`, `priority`, and `constraints`, plus only the fields
   required by that canonical intent, such as `resource`, `structure`,
   `location`, `unit`, `unit_group`, or `target`.
4. `IntentFeasibilityValidator.validate_intent(payload, state)` checks the typed
   DSL against the ToyCraft state snapshot. The validator returns an
   `IntentValidationResult` with either executable status or rejected status,
   reason codes, missing fields, and an actionable alternative.
5. Only executable validation results reach
   `ToyCraftExecutorInterface.apply_effects(payload, state)`. The default
   ToyCraft executor delegates to `ToyCraftRuleEngine`, which applies
   deterministic economy, production, construction, repair, movement, or combat
   effects and returns `ToyCraftExecutionResult` with before/after states,
   executed actions, state-change labels, and structured deltas.
6. `StateNarrator` receives either the rejected validation outcome or the
   execution result. It renders the Korean commander-facing narration from
   structured fields, including the original command, canonical intent,
   validation status, reason/alternative when blocked, and state deltas when
   executed.
7. `CommandProcessingResponse` returns the complete adapter-facing record:
   lifecycle status, original command text, selected Intent DSL when available,
   validation, execution result when reached, before/after state snapshots,
   failure report when blocked, and final narration.

Example successful command:

```text
"본진 입구에 배럭 지어"
  -> BUILD_STRUCTURE Intent DSL
     {intent: BUILD_STRUCTURE, priority: normal, constraints: (...),
      structure: Barracks, location: main ramp}
  -> executable validation
  -> ToyCraftRuleEngine spends minerals, reserves one SCV, queues construction
  -> Korean narration explains the Barracks order and resource/state changes
```

Example blocked command:

```text
"배틀크루저 뽑아"
  -> no supported Phase 0 payload, or a rejected MVP payload if typed directly
  -> no executor call
  -> Korean narration explains the reason and suggests a supported alternative
```

The safety invariant is that every blocked path returns a reason plus an
alternative and leaves `before_state == after_state`.

## Component Boundaries

| Component | Module | Owns | Does not own |
| --- | --- | --- | --- |
| Korean command interpreter | `toycraft_commander/interpreter.py` | Maps Korean or mixed text to the nearest supported MVP intent, preserves original `command_text`, returns typed parser failures and clarification prompts. | Resource feasibility, state mutation, combat math, or narration of state changes. |
| Intent DSL schemas | `toycraft_commander/intents.py` | The exactly 11 canonical intent names, common fields `intent`, `priority`, `constraints`, typed intent-specific payloads, DSL serialization, and payload shape validation. | ToyCraft resource availability, map availability, production queues, or rule execution. |
| Feasibility validator | `toycraft_commander/feasibility.py` | Checks whether a typed payload can execute against an immutable `ToyCraftState`, including resources, supply, prerequisites, producers, workers, targets, locations, and conflicting constraints. | Applying effects, advancing time, changing queues, or rendering final commander prose. |
| ToyCraft state and domain models | `toycraft_commander/resources.py`, `toycraft_commander/units.py`, `toycraft_commander/structures.py`, `toycraft_commander/map.py`, `toycraft_commander/ownership.py`, `toycraft_commander/state_resolver.py` | Minimal Terran-focused simulator vocabulary: minerals, gas, supply, SCV, Marine, Vulture, Zealot, structures, named map locations, ownership, and unit-group resolution. | Natural-language parsing, command lifecycle orchestration, or external game APIs. |
| Rule engine | `toycraft_commander/executor.py` via `ToyCraftRuleEngineInterface` | Deterministic ToyCraft effects for registered feasible commands, including economy ticks, spending, production queues, construction queues, time advancement, defense, expansion, harassment, and state deltas. Feasible commands without a registered effect handler are blocked without mutation. | Free-text interpretation or deciding whether invalid commands should execute. |
| Executor abstraction | `toycraft_commander/executor.py` via `ToyCraftExecutorInterface` | The backend seam used by the pipeline to apply effects and advance time. The default implementation wraps the ToyCraft rule engine. | UI input, voice input, SC2 adapter implementation, or narrator formatting. |
| State narrator | `toycraft_commander/narrator.py` | Converts execution results and rejected validations into Korean commander-facing responses with structured metadata, reason codes, alternatives, and state-change summaries. | Choosing intents, validating feasibility, or mutating state. |
| Command pipeline | `toycraft_commander/pipeline.py` | Coordinates exactly one command through interpreter, validator, executor, and narrator; enforces stop points and response invariants. | Owning stage-specific logic, advancing time implicitly, or bypassing validation. |
| Demo adapter | `toycraft_commander/demo.py` | Provides a 5-7 minute text transcript showing command text, selected Intent DSL, execution status, time advancement, and narration. | Adding new canonical intents, real SC2 control, voice control, or autonomous bot behavior. |

## Responsibility Rules

1. The interpreter may only select or reject an intent. It must not inspect or
   mutate `ToyCraftState`.
2. The Intent DSL must remain the stable command contract. It contains common
   fields plus intent-specific parameters, not validation outcomes or execution
   deltas.
3. The validator is the only normal gate from typed DSL to execution. Commands
   that are unsupported, impossible, or conflicting must return rejection reasons
   and alternatives before the executor is called.
4. The rule engine may assume a command already passed validation, but it still
   returns typed blocked execution results if an execution backend error occurs.
5. The narrator renders outcomes from structured inputs. It must not infer hidden
   game rules from prose-only execution strings.
6. The pipeline coordinates dependencies through interfaces so tests, demos, and
   a future backend adapter can replace individual layers independently.

## SC2 Readiness Boundary

Phase 0 does not implement SC2, BWAPI, voice input, full autonomy, build-order
optimization, or live opponent modeling. SC2 readiness is limited to keeping the
execution seam visible:

- `ToyCraftExecutorInterface.apply_effects(payload, state)` is the future adapter
  slot for applying a validated Intent DSL command to another backend.
- `ToyCraftExecutorInterface.advance_time(state, seconds)` is the backend time
  progression slot.
- Upstream components should continue to depend on typed Intent DSL payloads and
  executor results, not ToyCraft implementation details.
- A future SC2 adapter must preserve the same safety rule: rejected or unclear
  commands do not reach effect application.

## MVP Scope Guard

The Phase 0 architecture supports exactly these 11 canonical intents:
`GATHER_RESOURCE`, `BUILD_STRUCTURE`, `TRAIN_WORKER`, `TRAIN_ARMY`, `SCOUT`,
`SUMMARIZE_STATE`, `DEFEND`, `REPAIR`, `EXPAND`, `HARASS`, and `MOVE_CAMERA`.

New aliases may map Korean wording to one of these intents, but they must not add
an eleventh canonical intent. New simulation details are acceptable only when
they improve text-command UX validation without crossing into real game-control
integration.

## MicroMachine Cockpit Architecture

The production-oriented SC2 bot-control target is patched MicroMachine, not
the legacy python-sc2 commander. The web GUI's default chat and browser voice
input compile human intent into bounded policy modulation DSL and publish it to
a MicroMachine blackboard. MicroMachine remains the autonomous player; the UI,
LLM, replay, or future neural provider may only modulate manager-level policy
axes.

```text
Korean text or browser voice
  -> Web GUI MicroMachine mode in the unified Commander Chat
  -> bounded provider compiler / keyword provider / future LLM provider
  -> PolicyModulationVector
  -> MicroMachineModulationBackend
  -> blackboard files consumed by patched MicroMachine C++ managers
  -> telemetry + tactical logs
  -> web DSL intervention dashboard
```

This path does not call python-sc2, s2client-api, raw unit tags, keyboard hooks,
mouse automation, OCR, or screen scraping. The old `/api/command` route is
available only when the user explicitly selects **Legacy python-sc2 commander**
mode. Runtime launch/status is mode-aware: the same cockpit calls
`/api/runtime/start` and `/api/runtime/status`; MicroMachine mode starts the
patched MicroMachine smoke/live script with the selected blackboard directory,
while legacy mode starts the older python-sc2 demo only after a key has been
saved.
mode in the web UI.

## Legacy Live SC2 Architecture

The real StarCraft II runtime lives in `starcraft_commander/`. It reuses the
Phase 0 Korean interpreter and the typed Intent DSL unchanged, then replaces
the ToyCraft validator/executor/narrator stack with live equivalents that gate
against resolved BotAI observations and issue semantic python-sc2 orders.
Module docstrings are the source of truth for each seam; see
[contracts.md](contracts.md) for the contract summary.

### Legacy Runtime Flow

```text
Korean text or push-to-talk voice
  -> CommandInterpreter (reused from toycraft_commander)
  -> typed Intent DSL payload
  -> SC2FeasibilityValidator (gates against SC2CommanderState)
  -> SC2ActionPlanner (semantic SC2ExecutionPlan)
  -> SC2RuntimeExecutor (lifecycle-aware async boundary)
  -> PythonSC2BotAdapter (eight semantic action methods)
  -> python-sc2 BotAI (real game orders inside the game loop)
  -> SC2KoreanNarrator (Korean commander response)
```

`SC2CommandSession.process_text` in `live_pipeline.py` composes the whole flow
into one async call and returns one structured `SC2CommandOutcome` per command
part. Compound Korean utterances (such as the MVP command
"마린 6기 입구로 보내고 SCV 계속 찍어") are split heuristically so no command
part is silently dropped; unsupported parts come back as honest clarification
outcomes. State resolution happens per command from the executor's bound
runtime, so validation always gates against the freshest observation.

The package import surface is dependency-free: `starcraft_commander/__init__.py`
eagerly imports only the stdlib-only contracts and lazily loads every other
surface, so importing the package never requires ToyCraft, StarCraft II,
python-sc2, faster-whisper, or sounddevice.

### Live Component Boundaries

| Component | Module | Owns | Does not own |
| --- | --- | --- | --- |
| Semantic contracts | `starcraft_commander/contracts.py` | `SC2CommandAction`, `SC2ExecutionPlan`, `SC2PlanExecutionResult`, `SC2ActionReport`, `SC2ExecutionError`; strict priority validation; JSON-ready serialization. Pure stdlib. | Planning, execution, narration, or python-sc2 types. |
| Action planner + runtime executor | `starcraft_commander/sc2_executor.py` | Intent DSL to semantic plan mapping, strict target alias validation (unknown targets rejected with the supported list), lifecycle-aware async execution, structured `MissingBotCapability` errors, per-action audit (`audit['observations']`, `audit['action_reports']`). | Real python-sc2 calls or Korean narration. |
| State resolver | `starcraft_commander/state_resolver.py` | Never-raise duck-typed resolution of BotAI observations into `SC2CommanderState`; degraded fields recorded as `observation_notes`. | Feasibility decisions or order issuance. |
| Map resolver | `starcraft_commander/map_resolver.py` | Core semantic map targets plus best-effort extras resolved to `MapPoint` coordinates, including discovered `enemy_front`; auditable `MapGeometryInference` from starts, base clusters, ramps, minerals, and geysers with confidence/visibility/source metadata; explicit unavailable entries with reasons; unknown names rejected with available alternatives. | Pathing, combat targeting heuristics, or build placement legality. |
| BotAI adapter | `starcraft_commander/python_sc2_adapter.py` | The eight semantic action methods translated into duck-typed BotAI operations; `SC2ActionReport` requested-vs-issued counts; no lifecycle method names. python-sc2 lazy-imported only inside functions. This is legacy commander plumbing, not MicroMachine. | Lifecycle hooks, plan ordering, or narration. |
| Live feasibility validator | `starcraft_commander/feasibility.py` | Conservative gating of typed payloads against `SC2CommanderState`: resources, supply, tech prerequisites, producers, workers; unknown or incomplete state rejects mutating commands; only `SUMMARIZE_STATE` survives incomplete observation. | Mutating state or issuing orders. |
| Korean narrator | `starcraft_commander/narrator.py` | `SC2NarrationResponse` rendering of execution results, rejections, and state summaries; honest `partially_executed`/`blocked` statuses; disclosure of unenforced constraints. | Choosing intents or validating feasibility. |
| Live pipeline | `starcraft_commander/live_pipeline.py` | `SC2CommandSession` composition, compound-command splitting, `SC2CommandOutcome` per part with stage artifacts only for stages that ran. | Stage-specific logic or game-loop scheduling. |
| Voice input | `starcraft_commander/voice_input.py` | Microphone capture and Whisper transcription seams producing plain text for the unchanged interpreter; lazy optional dependencies with actionable `MissingVoiceDependencyError`. | Command interpretation or execution. |
| Dependency guards | `starcraft_commander/runtime_deps.py` | `is_*_available()` probes and `require_*()` guards with bilingual install hints for python-sc2 (burnysc2), faster-whisper, and sounddevice. | Any game or audio logic. |
| Web GUI | `starcraft_commander/web_gui.py` | Local cockpit with default MicroMachine DSL mode, explicit legacy python-sc2 commander mode, token-protected network binding, chat/voice routing, mode-aware runtime start/status, MicroMachine status, and DSL evidence dashboard. | Raw game control, MicroMachine C++ gameplay, or hidden mode switching. |
| Demo entrypoint | `starcraft_commander/demo_sc2.py` | `python -m starcraft_commander.demo_sc2`: `--dry-run` scripted fake-BotAI mode (testable), legacy live local-custom-game mode, `--voice` push-to-talk with a transcription confidence gate. | New intents or autonomous play. |

### Live Safety Invariants

The Phase 0 safety rules carry over unchanged to the live runtime:

1. Rejected commands never reach execution: interpreter clarifications stop
   before validation, validator rejections stop before planning, and planner
   refusals (unknown targets) stop before the executor. Every blocked outcome
   carries a Korean reason and an actionable alternative.
2. Partial execution is surfaced honestly: any skipped action, missing runtime
   capability, partial order issuance, or unenforced constraint produces
   `partially_executed` or `blocked`, never an unqualified success.
   `SC2CommandOutcome` refuses to carry pipeline artifacts for stages that
   never ran, so a blocked outcome cannot masquerade as an executed one.
3. Unknown game state rejects mutating commands: a missing runtime or
   incomplete observation (`observation_notes` non-empty) is grounds for
   conservative rejection rather than optimistic guessing.
4. No mouse or screen automation anywhere. Legacy commander mode uses only
   semantic python-sc2 API calls; MicroMachine cockpit mode uses only bounded
   policy modulation files and telemetry.
