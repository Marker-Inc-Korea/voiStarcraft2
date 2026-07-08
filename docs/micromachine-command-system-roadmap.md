# MicroMachine Live Command System Roadmap

## Problem Statement

MicroMachine already has macro production, building placement, squad assignment, and unit micro subsystems. The current live command layer does not expose those capabilities completely. The failure mode observed in live QA is not just weak telemetry: user intent can fail to become a durable, executable game plan, or it can become a shallow bias that is not consumed by the correct C++ manager.

The system must move from "chat text publishes a few bias fields" to a production-grade command system:

1. Parse natural language into structured command intents.
2. Reduce multiple chat commands into a coherent active plan.
3. Assign dynamic lifetimes based on command type.
4. Compile to a richer DSL with explicit production, composition, building, route, and micro role semantics.
5. Make ProductionManager, BuildingManager, CombatCommander, Squad, and micro managers consume those semantics.
6. Prove execution through telemetry that tracks production, assignment, order issue, SC2 action issue, unit displacement, and completion.

## Architecture Target

```text
User chat / UI controls
  -> Command Intent Parser
  -> Command Queue + Reducer
  -> Policy DSL compiler + safety gate
  -> MicroMachine blackboard
  -> ProductionManager / BuildingManager / CombatCommander
  -> Squad / RangedManager / MeleeManager / special ability logic
  -> SC2 API commands
  -> Telemetry + UI execution state
```

## PR Process

Each issue below is implemented as a separate PR.

1. Create a branch named `issue-<number>-<short-name>`.
2. Implement only that issue's scope.
3. Add unit tests and, where possible, local MicroMachine build/smoke validation.
4. Open a PR with evidence.
5. Request an independent `gpt-5.5` `xhigh` review sub-agent.
6. Fix reviewer findings until approved or explicitly waived.
7. Merge, then proceed to the next issue.

## Issue Sequence

1. #102 Command queue, reducer, and UI single-response steering.
2. #103 Dynamic command lifetime model.
3. #106 Rich DSL for composition, unit roles, production plans, building placement, and route intent.
4. #105 ProductionManager prerequisite chain consumption.
5. #104 Building placement intent consumption.
6. #108 CombatCommander composition-aware squad assignment.
7. #109 Unit-role and special-unit micro consumption.
8. #107 End-to-end telemetry and live QA acceptance harness.

## Current Baseline Already Addressed

The current working changes restore part of the tactical command path:

- Keyword commands no longer rely only on LLM forced-tool JSON for tactical attack/scout.
- Enemy base attack maps to `enemy_main`.
- Explicit unit count such as `4마린` maps to `scope/tactical_task min_units/max_units`.
- Route wording such as `우회`, `다른 길`, `flank` maps to `squad.flank_bias` and `combat.flank_bias`.
- C++ CombatCommander consumes `max_units` for MainAttack squad limiting and uses flank bias to stage through a center-biased target before the remote combat target.
- Validation run:
  - `pytest tests/test_micromachine_live_session.py tests/test_web_gui.py -q`
  - `125 passed, 210 subtests passed`
  - `integrations/micromachine/scripts/build_macos_local.sh`
  - MicroMachine build succeeded.
