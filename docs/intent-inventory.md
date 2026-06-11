# Phase 0 Canonical Intent Inventory

ToyCraft Commander supports exactly 10 canonical intents for the MVP. Korean free-form
commands should map to the nearest supported intent before validation and execution.

| Intent | Brief semantics |
| --- | --- |
| `GATHER_RESOURCE` | Assign Terran workers to collect minerals or vespene gas at a known base. |
| `BUILD_STRUCTURE` | Order an SCV to construct a Terran structure such as a Supply Depot, Barracks, Refinery, Bunker, or Command Center. |
| `TRAIN_WORKER` | Queue SCV production from an available Command Center when supply and minerals allow it. |
| `TRAIN_ARMY` | Queue combat units from available production structures, starting with Marines for the Phase 0 Terran MVP. |
| `SCOUT` | Send a selected worker or squad to reveal enemy location, expansion timing, or incoming pressure. |
| `SUMMARIZE_STATE` | Summarize the current ToyCraft economy, supply, army, structures, and pressure state for commander awareness. |
| `DEFEND` | Rally or reposition units to protect a base, structure, worker line, or choke point from enemy pressure. |
| `REPAIR` | Assign SCVs to restore hit points on damaged mechanical units or Terran structures. |
| `EXPAND` | Create or prepare a new Terran base by building a Command Center at a feasible expansion location. |
| `HARASS` | Send a small force to disrupt enemy workers or economy while avoiding a full committed fight. |

These names are the canonical `intent` values for the typed Intent DSL. All later
intent-specific schemas should keep the common fields `intent`, `priority`, and
`constraints`.

## Utterance Matrix Canonical Coverage

The Korean utterance matrix must enumerate and cover exactly these canonical
Intent DSL names:

| Coverage order | Canonical intent name |
| --- | --- |
| 1 | `GATHER_RESOURCE` |
| 2 | `BUILD_STRUCTURE` |
| 3 | `TRAIN_WORKER` |
| 4 | `TRAIN_ARMY` |
| 5 | `SCOUT` |
| 6 | `SUMMARIZE_STATE` |
| 7 | `DEFEND` |
| 8 | `REPAIR` |
| 9 | `EXPAND` |
| 10 | `HARASS` |

Interpreter aliases may add multiple Korean phrases for one canonical intent, but
they must not add an eleventh intent or rename these DSL values. The exported
`UTTERANCE_COVERAGE_CANONICAL_INTENT_NAMES` tuple is the first-class coverage
guard inventory for Korean utterance tests. The interpreter-level
`UTTERANCE_MATRIX_CANONICAL_INTENT_NAMES` alias intentionally points to that same
tuple so representative utterance coverage, payload validation, and execution
dispatch share one source of truth.

## Representative Korean Utterance Matrix

The representative matrix intentionally contains exactly two Korean utterances
for each canonical intent. Broader interpreter aliases may contain more examples,
but this matrix is the fixed 20-row accuracy target for MVP smoke tests.
The executable test corpus is exported as `KOREAN_COMMAND_TEST_CORPUS`, where
each row contains `command_text` and a JSON-ready `expected_dsl` object with the
common `intent`, `priority`, and `constraints` fields plus the intent-specific
typed fields below.

| Canonical intent | Korean utterance | Representative payload focus |
| --- | --- | --- |
| `GATHER_RESOURCE` | `미네랄에 일꾼 세 기 붙여` | minerals, 3 workers, main base |
| `GATHER_RESOURCE` | `가스에 SCV 하나 붙여` | gas, 1 worker, main base |
| `BUILD_STRUCTURE` | `본진 입구에 서플라이 디포 지어` | Supply Depot at main ramp |
| `BUILD_STRUCTURE` | `본진에 배럭 지어` | Barracks at main base |
| `TRAIN_WORKER` | `일꾼 계속 찍어` | one SCV with continuous worker-production constraint |
| `TRAIN_WORKER` | `SCV 계속 생산해` | one SCV with continuous worker-production constraint |
| `TRAIN_ARMY` | `마린 계속 뽑아` | one Marine |
| `TRAIN_ARMY` | `해병 생산해` | one Marine |
| `SCOUT` | `SCV 하나로 정찰 보내` | one SCV to enemy front |
| `SCOUT` | `일꾼 하나 적 앞마당 확인해` | one SCV to enemy natural |
| `SUMMARIZE_STATE` | `상태 알려줘` | read-only state summary |
| `SUMMARIZE_STATE` | `현재 상황 요약해` | read-only state summary |
| `DEFEND` | `입구 막아` | available combat units to main ramp |
| `DEFEND` | `본진 입구 수비해` | available combat units to main ramp |
| `REPAIR` | `벙커 수리해` | one SCV repairs front bunker |
| `REPAIR` | `SCV 두 기로 앞 벙커 고쳐` | two SCVs repair front bunker |
| `EXPAND` | `앞마당 가져가` | natural expansion |
| `EXPAND` | `앞마당에 커맨드센터 준비해` | natural expansion |
| `HARASS` | `마린 두 기로 적 미네랄 라인 견제해` | two Marines to enemy mineral line |
| `HARASS` | `상대 일꾼 라인 흔들어` | two Marines to enemy mineral line |

## Minimal Intent DSL Field Schema

Every Intent DSL payload must include these common required fields:

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `intent` | `intent` | yes | One of the 10 canonical MVP intent names. |
| `priority` | `priority` | yes | One of `low`, `normal`, `high`, or `urgent`. |
| `constraints` | `constraint_list` | yes | Conditions that must hold before execution. Empty list is allowed when no constraint was stated. |

Shared DSL primitives are intentionally small:

| Primitive | Values |
| --- | --- |
| `CanonicalIntentName` | `GATHER_RESOURCE`, `BUILD_STRUCTURE`, `TRAIN_WORKER`, `TRAIN_ARMY`, `SCOUT`, `SUMMARIZE_STATE`, `DEFEND`, `REPAIR`, `EXPAND`, `HARASS` |
| `PriorityLevel` | `low`, `normal`, `high`, `urgent` |
| `IntentFieldType` | `intent`, `priority`, `constraint_list`, `resource`, `integer`, `base`, `structure`, `location`, `unit`, `unit_group`, `target` |

Each canonical intent then adds only the minimal required intent-specific fields:

| Intent | Required intent-specific fields |
| --- | --- |
| `GATHER_RESOURCE` | `resource`, `worker_count`, `base` |
| `BUILD_STRUCTURE` | `structure`, `location` |
| `TRAIN_WORKER` | `count` |
| `TRAIN_ARMY` | `unit_type`, `count` |
| `SCOUT` | `target`, `unit_group` |
| `SUMMARIZE_STATE` | none beyond common fields |
| `DEFEND` | `location`, `unit_group` |
| `REPAIR` | `target`, `worker_count` |
| `EXPAND` | `location` |
| `HARASS` | `target`, `unit_group` |

This schema is intentionally small for Phase 0. Interpreter confidence,
validation results, execution deltas, and narration stay outside the intent
payload boundary so later ToyCraft and SC2-ready executors can consume the same
typed command contract.

## Stable Intent DSL Display Format

Parsed Korean commands serialize to the versioned `toycraft.intent_dsl.v1`
document format. The stable envelope order is:

| Field | Type | Notes |
| --- | --- | --- |
| `format` | string | Always `toycraft.intent_dsl.v1` for this Phase 0 contract. |
| `command_text` | string | Original Korean or mixed-language user command text. |
| `intent_dsl` | object | Typed intent payload in canonical schema field order. |
| `entity_references` | object[] | Optional typed command mentions such as unit groups, targets, or locations. |

The `intent_dsl` object always renders common fields first, followed by the
intent-specific required fields from the schema table above. For example:

```json
{
  "format": "toycraft.intent_dsl.v1",
  "command_text": "본진 입구 수비해",
  "intent_dsl": {
    "intent": "DEFEND",
    "priority": "urgent",
    "constraints": [
      "hold ramp against early pressure"
    ],
    "location": "main ramp",
    "unit_group": "available combat units"
  },
  "entity_references": []
}
```

Executable helpers live in `toycraft_commander.intents`:
`serialize_intent_payload`, `render_intent_payload`,
`serialize_intent_command`, and `render_intent_command`. The legacy `to_dict()`
methods remain JSON-ready for validation and execution, while
`IntentCommandPayload.to_dsl_document()` and `.to_dsl_json()` expose the stable
display document for UI, logs, and demos.

## Typed Validation Result and Feasibility Reasons

Intent validation returns an `IntentValidationResult` before any ToyCraft state
mutation. The result keeps narrator-friendly text (`reason`, `alternative`) and
also exposes machine-readable fields for safe execution gates:

| Field | Type | Notes |
| --- | --- | --- |
| `executable` | `bool` | `true` only when the typed payload may reach the rule engine. |
| `status` | `ValidationStatus` | `executable` or `rejected`. |
| `payload` | intent-specific payload or `None` | Typed DSL payload when validation succeeds. |
| `issues` | `FeasibilityIssue[]` | Blocking typed issues when validation rejects a command. |
| `reason_code` | `FeasibilityErrorReason` or `None` | Primary blocking reason for fast routing and tests. |
| `missing_fields` | `string[]` | Required DSL fields absent from the command payload. |

`FeasibilityErrorReason` is the shared vocabulary for unsupported, impossible,
or conflicting commands. Payload validation emits `malformed_payload`,
`missing_required_field`, `unsupported_intent`, and `invalid_field_value`.
State-aware feasibility checks in `toycraft_commander/feasibility.py` reuse the
same type rather than adding free-form strings:

| Category | Reasons |
| --- | --- |
| DSL/input safety | `malformed_payload`, `missing_required_field`, `unsupported_intent`, `invalid_field_value`, `unsupported_phase_zero_scope` |
| Economy and production feasibility | `insufficient_minerals`, `insufficient_gas`, `insufficient_supply`, `missing_prerequisite`, `unavailable_producer` |
| Unit and map feasibility | `unavailable_worker`, `unavailable_unit_group`, `invalid_target`, `location_unavailable` |
| Commander constraint safety | `constraint_conflict` |

## Korean Interpreter Aliases

Interpreter aliases are phrase-mapping conveniences, not additional canonical
intents. The `keep_worker_production` alias maps Korean free utterances such as
`일꾼 계속 찍어`, `SCV 계속 생산해`, `에스시비 쉬지 말고 뽑아`, `일꾼 생산 유지해`,
and `커맨드센터에서 SCV 하나씩 계속 찍어` to the nearest supported typed DSL:
`TRAIN_WORKER` with `count=1` and a continuity constraint.

The `prevent_supply_block` alias maps Korean free utterances such as
`서플 막히지 않게 해`, `인구수 안 막히게 보급고 지어`, `서플라이 디포 미리 올려`,
`보급고 하나 지어서 인구 트이게 해`, and `인구 막히기 전에 서플 하나 지어` to
the nearest supported typed DSL: `BUILD_STRUCTURE` with `structure="Supply Depot"`
and a supply-block prevention constraint.

The `build_structure` alias maps direct construction utterances such as
`본진 입구에 서플라이 디포 지어`, `본진에 배럭 지어`, `병영 하나 앞마당 쪽에 올려`,
`본진 가스에 리파이너리 지어`, `정제소 지어서 가스 캐게 해`, and
`앞마당 입구에 벙커 건설해` to the nearest supported typed DSL:
`BUILD_STRUCTURE` with a requested Terran structure and placement location.

The `train_unit` alias maps Marine production utterances such as
`마린 계속 뽑아`, `해병 생산해`, `배럭에서 마린 두 기 찍어`, `마린 세 기 추가해`,
and `방어용 해병 네 기 만들어` to the nearest supported typed DSL:
`TRAIN_ARMY` with `unit_type="Marine"` and a requested count. Phase 0 keeps this
alias intentionally narrow: Vulture production remains outside the current
army-training DSL even though Vulture exists as a ToyCraft model for later
harassment scenarios.

The `send_scout` alias maps scouting utterances such as `SCV 하나로 정찰 보내`,
`일꾼 하나 적 앞마당 확인해`, `적 본진으로 정찰 가`, `상대 입구 빨리 체크해`,
and `마린 한 기로 적 미네랄 라인 봐` to the nearest supported typed DSL:
`SCOUT` with a target such as `enemy front`, `enemy natural`, `enemy main`, or
`enemy mineral line` and a worker or Marine scouting group.

The `defend_ramp` alias maps defensive army-control utterances such as
`입구 막아`, `본진 입구 수비해`, `마린들 램프에 세워`, `해병으로 언덕 지켜`,
and `초반 러시 오니까 입구 홀드해` to the nearest supported typed DSL:
`DEFEND` with `location="main ramp"` and a ramp-hold constraint.

The `retreat_army` alias maps defensive fallback utterances such as
`병력 뒤로 빼`, `마린들 본진으로 후퇴시켜`, `싸움 빼고 병력 살려`,
`해병들 안전하게 뒤로 빠져`, and `무리하지 말고 병력 회군해` to the nearest
supported typed DSL: `DEFEND` with a safe fallback location and an army-preservation
constraint. This remains an interpreter alias only; Phase 0 still exposes exactly
10 canonical intents.

The `summarize_state` alias maps Korean and English commander-awareness utterances
such as `상태 알려줘`, `현재 상황 요약해`, `지금 뭐 하고 있어`, `게임 상태 브리핑해`,
`summarize state`, and `show game status` to the typed DSL:
`SUMMARIZE_STATE` with only the common fields `intent`, `priority`, and
`constraints`. It is read-only and exists so a text demo can ask ToyCraft for a
state narration without issuing a state-changing order.

## Korean and English Command Pattern Lexicons

The interpreter exposes four phrase-family lexicons for supported Korean and
English MVP command wording. These are pattern vocabularies for nearest-intent
matching, not additional canonical intents:

| Lexicon category | Korean examples | English examples | Nearest typed DSL outcomes |
| --- | --- | --- | --- |
| `unit_selection` | `SCV`, `일꾼`, `마린`, `해병`, `병력` | `SCV`, `worker`, `Marine`, `Marines`, `army` | Selects unit groups for `SCOUT`, `DEFEND`, and `HARASS` payload fields. |
| `movement` | `보내`, `정찰`, `확인`, `입구`, `후퇴`, `회군` | `send`, `scout`, `rally`, `hold`, `pull back`, `retreat` | Maps scouting, ramp holds, and fallback movement to `SCOUT` or `DEFEND`. |
| `production` | `생산`, `찍어`, `뽑아`, `건설`, `배럭`, `벙커` | `train`, `produce`, `queue`, `build`, `barracks`, `bunker` | Maps worker, Marine, and Terran building requests to production DSL payloads. |
| `attack` | `공격`, `압박`, `견제`, `흔들`, `적 미네랄` | `attack`, `pressure`, `harass`, `deny`, `enemy mineral line` | Maps pressure and harassment language to the Phase 0 `HARASS` DSL. |

The concrete registry lives in `toycraft_commander.interpreter` as
`COMMAND_PATTERN_LEXICONS`. Pattern matching remains deliberately lightweight:
text is case-folded and whitespace-insensitive, then routed to one of the 10
canonical intent schemas before validation and execution.

The module-level interpreter interface is `CommandInterpreter`. It exposes
`interpret_text(command_text) -> IntentPayload | None` for raw DSL selection and
`interpret(command_text) -> CommandInterpretationResult` for caller-safe parsing
with clarification data. `DEFAULT_COMMAND_INTERPRETER` is the production Phase 0
instance, and the compatibility helpers `interpret_command_text` and
`interpret_command` delegate to it. This keeps Korean text interpretation
separate from validation, rule-engine execution, and narration while preserving
the exact 10-intent MVP scope.

## Economy and Production Payload Types

The economy/production subset now has concrete typed payload classes in
`toycraft_commander.intents` for the first validator and rule-engine boundary:

| Intent | Payload type | Intent-specific typed fields |
| --- | --- | --- |
| `GATHER_RESOURCE` | `GatherResourceIntent` | `resource`, `worker_count`, `base` |
| `BUILD_STRUCTURE` | `BuildStructureIntent` | `structure`, `location` |
| `TRAIN_WORKER` | `TrainWorkerIntent` | `count` |
| `TRAIN_ARMY` | `TrainArmyIntent` | `unit_type`, `count` |

Each payload preserves the common `intent`, `priority`, and `constraints` fields,
normalizes constraints to an immutable tuple internally, and serializes them as a
JSON-ready list through `to_dict()`. Phase 0 production is intentionally Terran
focused: allowed structures are Supply Depot, Barracks, Refinery, Bunker, and
Command Center, while army training is limited to Marines.

## Scouting, Building, and Tech/Progression Payload Types

Phase 0 also exposes narrow typed payload registries for the commander UX slices
needed by interpretation, validation, and later executor boundaries:

| Slice | Intent | Payload type | Intent-specific typed fields |
| --- | --- | --- | --- |
| Scouting | `SCOUT` | `ScoutIntent` | `target`, `unit_group` |
| Building | `BUILD_STRUCTURE` | `BuildStructureIntent` | `structure`, `location` |
| Tech/progression | `EXPAND` | `ExpandIntent` | `location` |

`EXPAND` is modeled as progression rather than generic construction because it
represents the strategic decision to take a new base. The ToyCraft rule engine
can later validate whether that command resolves into a feasible Command Center
placement without changing the Intent DSL payload shape.

## Map Target Lookup Boundary

Phase 0 resolves location and target strings through `toycraft_commander.map`
before any future rule-engine execution. The helper layer intentionally stays
small: it exposes canonical location names, Korean/English aliases, integer
`TilePosition` values, and `TargetablePosition` records for command targets such
as `main ramp`, `main geyser`, `natural expansion`, `enemy natural`, `enemy
front`, `enemy mineral line`, and `front bunker`.

This is a ToyCraft-only abstraction boundary. It gives validators and narrators
a stable target contract while avoiding SC2 map APIs, BWAPI tile data, voice
input, or full autonomous bot behavior in Phase 0.

## State-Aware Feasibility Dispatch

`toycraft_commander.feasibility` adds the ToyCraft state gate between typed DSL
validation and future execution. `ToyCraftState` snapshots minerals, gas, supply,
units, structures, busy workers, busy producers, production queues, claimed
locations, and damaged targets. `validate_intent_feasibility(payload, state)`
accepts either a raw DSL
mapping or a typed payload, runs payload validation when needed, dispatches to
one rule per canonical intent, and returns the shared `IntentValidationResult`
without mutating state.

The dispatch table intentionally covers exactly the 10 MVP intents:
`GATHER_RESOURCE`, `BUILD_STRUCTURE`, `TRAIN_WORKER`, `TRAIN_ARMY`, `SCOUT`,
`SUMMARIZE_STATE`, `DEFEND`, `REPAIR`, `EXPAND`, and `HARASS`. Rules currently check
minimum Phase 0 feasibility only: resources, supply, prerequisites, production
queue slots, available SCVs, unit-group availability, target/location resolution,
damaged repair targets, expansion occupancy, and known conflicting constraints.
