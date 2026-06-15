# Phase 0 Interface Contracts

This document is the implementation-facing contract for ToyCraft Commander's
Phase 0 command loop. It defines the typed Intent DSL boundary, validation gate,
and rule-engine/executor interface. It does not add SC2, BWAPI, voice control, or
autonomous bot behavior.

## Contract Flow

```text
command_text
  -> CommandInterpreter
  -> IntentPayload
  -> IntentFeasibilityValidator.validate_intent(payload, state)
  -> ToyCraftExecutorInterface.apply_effects(payload, state)
  -> ToyCraftRuleEngine.execute_intent(payload, state)
  -> ToyCraftExecutionResult
  -> StateNarrator
```

Blocked interpretation stops before validation. Rejected validation stops before
execution. Executor failures must return a blocked result with unchanged state.

## Typed Intent DSL

The Intent DSL source of truth is `toycraft_commander/intents.py`.
`INTENT_DSL_FORMAT_VERSION` is `toycraft.intent_dsl.v1`.

Every payload is an intent-specific dataclass that inherits the same common
fields:

| Field | Type | Required | Contract |
| --- | --- | --- | --- |
| `intent` | `IntentName` | yes | One of exactly 11 canonical Phase 0 intent names. |
| `priority` | `Priority` | yes | `low`, `normal`, `high`, or `urgent`; defaults to `normal`. |
| `constraints` | `tuple[str, ...]` | yes | Natural-language or normalized conditions that must hold before execution. Empty is valid. |

The 11 canonical typed payload schemas are:

| Intent | Payload class | Required intent-specific fields |
| --- | --- | --- |
| `GATHER_RESOURCE` | `GatherResourceIntent` | `resource`, `worker_count`, `base` |
| `BUILD_STRUCTURE` | `BuildStructureIntent` | `structure`, `location` |
| `TRAIN_WORKER` | `TrainWorkerIntent` | `count` |
| `TRAIN_ARMY` | `TrainArmyIntent` | `unit_type`, `count` |
| `SCOUT` | `ScoutIntent` | `target`, `unit_group` |
| `SUMMARIZE_STATE` | `SummarizeStateIntent` | none beyond common fields |
| `DEFEND` | `DefendIntent` | `location`, `unit_group` |
| `REPAIR` | `RepairIntent` | `target`, `worker_count` |
| `EXPAND` | `ExpandIntent` | `location` |
| `HARASS` | `HarassIntent` | `target`, `unit_group` |
| `MOVE_CAMERA` | `MoveCameraIntent` | `target` |

The stable parsed-command display envelope is:

```json
{
  "format": "toycraft.intent_dsl.v1",
  "command_text": "본진 입구 수비해",
  "intent_dsl": {
    "intent": "DEFEND",
    "priority": "urgent",
    "constraints": ["hold ramp against early pressure"],
    "location": "main ramp",
    "unit_group": "available combat units"
  },
  "entity_references": []
}
```

Payload serialization must use `serialize_intent_payload()` or
`IntentCommandPayload.to_dsl_document()` so field order follows
`INTENT_DSL_FIELD_ORDER_BY_INTENT`: common fields first, then the required
intent-specific fields.

## Validation Contract

The validator boundary is `IntentFeasibilityValidator` in
`toycraft_commander/feasibility.py`:

```python
class IntentFeasibilityValidator(Protocol):
    def validate_intent(
        self,
        payload: IntentPayload | Mapping[str, object],
        state: ToyCraftState,
    ) -> IntentValidationResult:
        ...
```

Inputs:

| Input | Contract |
| --- | --- |
| `payload` | A typed `IntentPayload` or raw mapping coercible through `validate_intent_payload()`. |
| `state` | Immutable `ToyCraftState` snapshot containing resources, supply, units, structures, queues, map claims, damaged targets, positions, and combat pressure state. |

Outputs:

| Output field | Contract |
| --- | --- |
| `executable` | `True` only when the payload may reach `apply_effects`. |
| `status` | `ValidationStatus.EXECUTABLE` or `ValidationStatus.REJECTED`. |
| `payload` | Typed payload on success; may be absent for malformed or unsupported raw input. |
| `reason` | Human-readable rejection summary for blocked commands. |
| `alternative` | Actionable supported alternative for blocked commands. |
| `missing_fields` | Required DSL fields missing from raw payload validation. |
| `issues` | Ordered `FeasibilityIssue` values with typed reason codes. |
| `reason_code` / `reason_codes` | Machine-readable `FeasibilityErrorReason` values for tests, demos, and UI adapters. |

Validation invariants:

1. The validator must not mutate `ToyCraftState`.
2. Unsupported intents, malformed payloads, missing required fields, invalid
   field values, impossible state requests, and conflicting constraints return
   `executable=False`.
3. Every rejected result must include a reason and an actionable alternative
   through its primary `FeasibilityIssue`.
4. State-aware checks cover resources, gas, supply, prerequisites, producer
   availability, worker availability, unit-group availability, targets, map
   locations, damaged repair targets, and known conflicting constraints.
5. Validation remains a gate only; it does not spend resources, enqueue units,
   move units, repair targets, or apply combat damage.

## Rule Engine and Executor Contract

`ToyCraftExecutorInterface` is the backend seam used by the pipeline and future
adapter experiments:

```python
class ToyCraftExecutorInterface(Protocol):
    def apply_effects(
        self,
        payload: IntentPayload | Mapping[str, object],
        state: ToyCraftState,
    ) -> ToyCraftExecutionResult:
        ...

    def advance_time(
        self,
        state: ToyCraftState,
        seconds: int,
    ) -> ToyCraftExecutionResult:
        ...
```

The default `ToyCraftExecutor` delegates to `ToyCraftRuleEngineInterface`:

```python
class ToyCraftRuleEngineInterface(Protocol):
    def execute_intent(
        self,
        payload: IntentPayload | Mapping[str, object],
        state: ToyCraftState,
    ) -> ToyCraftExecutionResult:
        ...

    def advance_time(
        self,
        state: ToyCraftState,
        seconds: int,
    ) -> ToyCraftExecutionResult:
        ...
```

`ToyCraftRuleEngine.execute_intent()` validates first through its configured
`IntentFeasibilityValidator`. If validation rejects, it returns a rejected
`ToyCraftExecutionResult` and leaves `before_state == after_state`.

`ToyCraftExecutionResult` fields:

| Field | Contract |
| --- | --- |
| `intent` | Canonical intent name, `PROGRESS_TIME`, or `UNKNOWN` for malformed rejected input. |
| `validation` | Validation result used for the execution decision. |
| `before_state` / `after_state` | Immutable snapshots before and after rule application. |
| `executed` | `True` only for successful mutating or read-only execution. |
| `read_only` | `True` for state summaries, rejected results, and backend failures. |
| `narration` | Non-empty Korean commander-facing explanation or rejection message. |
| `state_changes` | Raw state-change labels for demos and backward-compatible narration. |
| `executed_actions` | Structured actions such as `spend_resources`, `queue_construction`, `queue_production`, `move_units`, `apply_damage`, or `advance_time`. |
| `state_delta` | Structured before/after deltas derived from state snapshots. |
| `summary` | Structured state summary for `SUMMARIZE_STATE` and UI adapters. |
| `failure` | `CommandFailureReport` for rejected or failed executions only. |

Execution invariants:

1. `executed=True` requires executable validation.
2. Rejected execution results must include `failure`, must have no
   `executed_actions`, and must not mutate state.
3. State-changing execution results require at least one `executed_action` and a
   non-empty structured `state_delta`.
4. Read-only results must keep `before_state == after_state`.
5. `advance_time(state, seconds)` is the only contract for deterministic timer
   progression; normal command execution must not advance time implicitly.
6. The default Phase 0 rule table handles `GATHER_RESOURCE`, `BUILD_STRUCTURE`,
   `TRAIN_WORKER`, `TRAIN_ARMY`, `SUMMARIZE_STATE`, `DEFEND`, `EXPAND`, and
   `HARASS` through ToyCraft-only rules.
7. `REPAIR` is still part of the canonical typed DSL and feasibility contract;
   until a ToyCraft repair effect handler is registered, a validated repair
   payload must be blocked at the executor boundary rather than mutating state.

## Pipeline Stop Conditions

The command pipeline in `toycraft_commander/pipeline.py` coordinates the stages
without owning stage-specific logic:

| Pipeline status | Stop point | State mutation |
| --- | --- | --- |
| `blocked_before_validation` | Interpreter returned no payload or parser error. | Not allowed. |
| `blocked_by_validation` | Validator rejected a typed payload. | Not allowed. |
| `blocked_by_executor` | Executor returned or raised a backend failure. | Not allowed. |
| `executed` | Executor accepted the payload and narrator rendered the result. | Allowed only when execution result is state-changing. |

The safety contract is simple: unsupported, impossible, or conflicting commands
must not execute, must include a reason plus alternative, and must preserve
`before_state == after_state`.

## SC2 Command Plan Contract

The real StarCraft II execution boundary lives in `starcraft_commander`. It
reuses the same 11 canonical Intent DSL payloads, but it emits semantic SC2
command plans rather than ToyCraft state transitions or python-sc2 method names.
The stable public SC2 action type name set is:

| Action type | Semantic contract |
| --- | --- |
| `assign_workers` | Assign worker units to a resource, base, or economy role. |
| `build_structure` | Request construction of an SC2 structure at a semantic target alias. |
| `train_unit` | Request production of one or more units from the appropriate producer. |
| `move_group` | Move a named or resolved unit group to a semantic map target. |
| `attack_move` | Issue combat movement toward a defensive or offensive target. |
| `repair` | Assign repair workers to a damaged unit or structure target. |
| `observe` | Read or summarize visible runtime state without issuing a mutating order. |
| `move_camera` | Move the player's camera to a resolved semantic map target without issuing unit orders. |

These names are the public API vocabulary for command plans, logs, UI adapters,
and fake BotAI-style tests. They must remain semantic action categories; callers
must not depend on python-sc2 method names, ToyCraft executor action labels, or
mouse-click automation details.

## SC2 Readiness Boundary

Phase 0 readiness for SC2 is limited to the visible executor abstraction. A
future backend may implement `ToyCraftExecutorInterface`, but it must preserve
the typed Intent DSL, validator gate, execution result shape, narration inputs,
and no-mutation rejection safety invariant. Phase 0 does not implement SC2 API
calls, BWAPI, voice input, hidden autonomous build-order control, or live
opponent modeling.

## SC2 Live Contracts Summary

The live runtime in `starcraft_commander/` implements the boundary above. This
is a concise index; the module docstrings are the source of truth for field
semantics and invariants.

| Contract | Module | Summary |
| --- | --- | --- |
| `SC2CommanderState` | `state_resolver.py` | Frozen semantic snapshot resolved from BotAI observations: minerals, vespene, supply, own/enemy unit and structure counts (UPPERCASE space-free type names), idle workers, army count, game time. `SC2StateResolver.resolve(bot)` never raises; degraded fields are recorded in `observation_notes`, and `observation_complete` is false whenever any note exists. |
| `MapTargetResolution` | `map_resolver.py` | Outcome of resolving one semantic map target to a `MapPoint`. Available resolutions carry a position and nothing else; unavailable resolutions carry a reason and the currently available alternatives, never a position. Covers `self_main`, `self_ramp`, `self_natural`, `enemy_main`, `enemy_ramp`, `enemy_front`, `enemy_natural`, `enemy_mineral_line` plus best-effort `self_mineral_line` and `self_geyser`. |
| `MapGeometryInference` | `map_resolver.py` | Auditable map-geometry snapshot built from start locations, expansion/base anchors, ramps, mineral patches, and geysers. Every observation or base cluster carries deterministic `confidence`, `visibility`, and `source` metadata for dashboard/debug display. |
| `SC2FeasibilityResult` | `feasibility.py` | Gate verdict for one intent against `SC2CommanderState`. Executable results carry no reasons; rejected results carry at least one reason code, at least one Korean reason, and a non-empty Korean actionable alternative. Unknown state (`None`) or incomplete observation rejects mutating intents; only `SUMMARIZE_STATE` stays executable. |
| `SC2NarrationResponse` | `narrator.py` | One Korean commander-facing narration with status `executed`, `partially_executed`, `blocked`, or `read_only`, plus structured detail lines. Skipped or partially issued work is never narrated as success; unenforced plan constraints are disclosed. |
| `SC2CommandOutcome` | `live_pipeline.py` | One outcome per command (or compound part) with status `executed`, `partially_executed`, `blocked`, `read_only`, or `clarification`. Pipeline artifacts (`intent_dsl`, `plan`, `execution_result`, `feasibility`) are present only for stages that actually ran; clarification outcomes carry none. |
| `SC2ActionReport` | `contracts.py` | Per-action adapter report carrying `requested_count` vs `issued_count`, `detail`, and JSON-ready `audit`. `is_partial` flags issuance shortfalls; `bool(report)` is true only for a full, shortfall-free application so boolean callers never overclaim. Placement-aware build reports audit `resolved_target_policy`, `placement_policy`, `anchor_source`, `search_result`, and exact `failure_reason`. |

### Adapter Method Contract

`SC2RuntimeExecutor.execute(plan)` dispatches every planned `SC2CommandAction`
by calling the method named after its `action_type` on the bound runtime
adapter. `PythonSC2BotAdapter` therefore implements exactly the eight semantic
action type names as methods:

```text
assign_workers   build_structure   train_unit   move_group
attack_move      repair            observe       move_camera
```

- Counted methods return `SC2ActionReport` (partial issuance is never collapsed
  into a boolean); placement-aware `build_structure` paths return
  `SC2ActionReport` with target/placement audit fields while simple legacy
  build paths may still return a plain bool; `observe` returns a JSON-ready
  mapping.
- The adapter defines none of the executor lifecycle hook names (`start`,
  `close`, `stop`, `on_start`, `on_end`) so they cannot collide with python-sc2
  `BotAI` lifecycle semantics.
- A runtime missing both the action method and `execute_commander_action`
  yields a structured `SC2ExecutionError` with
  `exception_type="MissingBotCapability"`; the action is skipped and the result
  is not a success.
- Unknown plan targets and unknown priorities are rejected at construction
  time with the supported-value listing (strict validation, no silent
  defaults or pass-through).

### Observation Channel

`observe` results are stored on `SC2PlanExecutionResult.audit["observations"]`
keyed by action index, alongside `audit["action_reports"]` for per-action
adapter reports. The narrator reads state summaries from this audit channel;
no component smuggles observations through narration prose.
