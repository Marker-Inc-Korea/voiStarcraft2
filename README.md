# TextCraft Commander

Natural-language RTS commander layer for StarCraft experiments.

Phase 0 focuses on ToyCraft, a text-based simulator that validates Korean command interpretation, typed Intent DSL validation, rule-based execution, and narration before any SC2 or BWAPI integration.

## Phase 0 Intent Inventory

The MVP supports exactly 10 canonical intents. See [docs/intent-inventory.md](docs/intent-inventory.md) and `toycraft_commander/intents.py` for the executable inventory.
Parsed Korean commands can be displayed or serialized with the stable
`toycraft.intent_dsl.v1` JSON document format via
`IntentCommandPayload.to_dsl_document()` or `.to_dsl_json()`.

## Phase 0 Unit Models

ToyCraft currently defines exactly four unit models for MVP simulation: SCV, Marine, Vulture, and Zealot. See `toycraft_commander/units.py` for typed cost and combat stat definitions used by validation, execution, and narration.

## Phase 0 Structure Models

ToyCraft defines four Terran structure models for MVP simulation: Barracks, Factory, Supply Depot, and Refinery. See `toycraft_commander/structures.py` for typed construction costs, build times, prerequisites, supply impact, and capabilities used by validation, execution, and narration.

## Phase 0 Map Target Helpers

ToyCraft uses a small named map registry for command feasibility and narration rather than a real SC2 map API. See `toycraft_commander/map.py` for canonical locations, Korean/English alias resolution, integer tile positions, and targetable-position helpers used by build, scout, defend, attack, repair, expand, and harass commands.

## Phase 0 Korean Interpreter Boundary

ToyCraft maps Korean natural-language commander text into typed Intent DSL
payloads through `toycraft_commander/interpreter.py`. The reusable
`CommandInterpreter` interface owns the mapping registry, phrase lexicons, and
the 10 canonical MVP intent inventory; legacy helpers `interpret_command_text`
and `interpret_command` delegate to `DEFAULT_COMMAND_INTERPRETER`. Unsupported,
malformed, or ambiguous text returns a `CommandInterpretationResult` with no
payload and a clarification reason, so rejected commands cannot reach validation
or execution.

## Phase 0 Feasibility Boundary

ToyCraft validates typed Intent DSL payloads against an immutable `ToyCraftState`
snapshot before any rule-engine execution. See `toycraft_commander/feasibility.py`
for the shared `IntentValidationResult`-based gate, 10-intent dispatch table, and
state checks for resources, supply, prerequisites, producers, workers, unit
groups, targets, locations, and conflicting constraints.

## Phase 0 Executor and Narration Boundary

ToyCraft execution starts in `toycraft_commander/executor.py`, which keeps the
Phase 0 rule-engine boundary separate from future SC2 executors. Implemented
handlers currently include `GATHER_RESOURCE`, which applies a deterministic
ToyCraft economy tick and worker assignment; resource-spending handlers for
`BUILD_STRUCTURE` and `EXPAND`, which validate first and then spend resources,
reserve one builder SCV, and enqueue in-progress construction without granting
completed structure effects early; training handlers for `TRAIN_WORKER` and
`TRAIN_ARMY`, which reserve supply and append production queues; and
`DEFEND` and `HARASS`, which move the selected attacker group and apply
deterministic ToyCraft combat damage. `DEFEND` additionally records
`pressure_mitigation` so defensive commands visibly reduce incoming pressure,
while `HARASS` updates only offensive target damage. `SUMMARIZE_STATE` leaves the
immutable state unchanged and generates commander-facing Korean state-summary
narration.

The State Narrator input contract lives in `toycraft_commander/narrator.py`.
It converts execution results and rejected feasibility outcomes into immutable
`StateNarratorInput` payloads containing the original command text, canonical
intent, priority, constraints, feasibility status/reasons/alternatives,
before/after ToyCraft state snapshots, execution flags, state changes, and any
structured summary data. Each payload also includes a typed
`StateNarratorChangeSummary` grouped into resource deltas, entity deltas, and map
deltas so commander-facing narration can explain what changed without parsing
rule-engine strings. This keeps narrator inputs explicit without coupling Phase 0
ToyCraft rules to a future SC2 executor.

The same module also defines the State Narrator output contract. A
`StateNarratorResponse` wraps the final Korean commander-facing `response_text`
with `StateNarratorResponseMetadata` for command text, intent, priority,
constraints, execution/read-only/blocked status, validation status, state-change
flags, raw state-change labels, reason codes, and summary data. Blocked commands
must include a `StateNarratorBlockedCommand` report with a non-empty reason,
alternative, typed reason codes, missing fields, and issue details; executed
responses cannot include blocked-command details. This makes unsupported,
impossible, or conflicting command handling visible to UI/demo adapters without
allowing rejected commands to mutate ToyCraft state.

## Phase 0 Korean Demo

Run the text-first commander demo with:

```bash
python -m toycraft_commander.demo
```

The transcript uses Korean natural-language RTS commands, prints the typed Intent
DSL selected by the interpreter, validates and executes feasible ToyCraft state
changes, advances deterministic production/build timers, and narrates each
result for a 5-7 minute MVP walkthrough.
