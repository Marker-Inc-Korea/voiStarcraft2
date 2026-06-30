# MicroMachine Pre-Live QA Hardening

This document tracks the local issue scope used when GitHub issue creation is
not available from the sandbox.

## Local Issue A: SCV self-position worker command loop

Goal: fix and prove the bug where one SCV repeatedly receives a right-click or
move command to a fixed non-mineral/self-position near the Command Center.

Acceptance:

- Worker command generation emits bounded telemetry trace fields.
- Trace does not append unbounded files; only bounded latest-summary fields are
  emitted through telemetry.
- Smoke fails if a self-position block, repeat-order suppression, or scout
  duplicate move safety block is observed. A guard hit is treated as a failed
  root-cause fix, not as success.
- Idle mining/idle recovery/scout fallback paths avoid reissuing commands to
  the current position or an already active target.

Implementation:

- `BuildingManager` no longer reissues an assigned build command every frame
  while the same worker/building assignment is already in the post-command,
  pre-construction window. This addresses the observed generator path where a
  builder SCV could be repeatedly ordered to the same non-mineral build point
  before the generic duplicate-command safety layer had to intervene.
- `WorkerData` no longer reissues idle-mining target-position orders when the
  worker is already at that target or already moving to that target.
- Wall-mineral and visible-mineral recovery paths now check the active order
  target before issuing another right-click.
- Mineral-return/depot fallback paths no longer emit a direct depot waypoint
  right-click when the worker already has the same return/depot order active.
  This addresses the observed Command Center/front-of-base stationary ping
  loop instead of relying on the duplicate-order guard to hide it.
- Worker commands are labeled with root-cause reasons before dispatch, then
  summarized in `WorkerManager` telemetry instead of appended to an unbounded
  trace file.
- The smoke validator rejects any latest or archived telemetry where
  `self_position_command_block_count` or `repeat_order_suppressed_count` is
  non-zero, or where the bounded trace contract fields are missing.

## Local Issue B: Multi-strategy DSL bias evidence

Goal: prove text/LLM DSL strategy bias is consumed by MicroMachine managers for
more than marine rush.

Acceptance:

- Smoke can run with `SMOKE_STRATEGY_PROFILE_NAME`.
- Standalone soak derives the same strict strategy doctrine/action/item contract
  from `SOAK_PROFILE_SEQUENCE`, or from explicit
  `SOAK_EXPECTED_STRATEGY_DOCTRINE`, `SOAK_EXPECTED_PRODUCTION_ACTIONS`, and
  `SOAK_EXPECTED_PRODUCTION_ITEMS` overrides.
- Strategy matrix wrapper covers `bio_pressure`, `tank_defensive_hold`,
  `mech_transition`, `drop_harassment`, and `expand_macro`.
- ProductionManager evidence must match the expected strategy mode, action, and
  queue item family.
- Stale, mismatched, action-only, state-delta, or post-policy inference
  evidence is rejected.
- Strategy matrix runs must use a unique run directory so previous blackboard
  files cannot become false evidence.
- `bio_pressure` cannot pass with Marine-only evidence; it must prove a
  non-Marine support path such as TechLab/Marauder/Starport/Medivac.

Implementation:

- The smoke publisher now routes every profile through
  `build_micromachine_strategy_profile`, not a two-profile special case.
- `ProductionManager` records doctrine consumption only when the current policy
  directly queues an item. Only `queued` evidence is accepted by smoke and the
  strategy matrix.
- `bio_pressure` includes a Barracks TechLab/Marauder path, so it is no longer
  allowed to pass as a Marine-rush variant.
- `mech_transition` includes C++-consumed combat and squad axes, so it is not a
  production-only profile.
- `drop_harassment` includes Factory prerequisite bias, making the Starport and
  Medivac path reachable from early Terran state.
- The strategy matrix summary reports both the expected archive match and the
  latest production snapshot. This avoids hiding cases where a valid drop setup
  is later overwritten by normal Marine continuity production.
- The web dashboard exposes the current strategy mode/play style beside manager
  consumption evidence.

## Verified Evidence

Local validation was run on 2026-06-30 with the patched MicroMachine build at
`/private/tmp/voi-micromachine-runtime/MicroMachine/build-latest-api/bin/MicroMachine`.

Static and unit checks:

- `git diff --check`: passed.
- `bash -n integrations/micromachine/scripts/smoke_macos_local.sh`: passed.
- `bash -n integrations/micromachine/scripts/strategy_matrix_macos_local.sh`:
  passed.
- `pytest -q tests/test_micromachine_soak.py tests/test_micromachine_runtime.py
  tests/test_web_gui.py tests/test_micromachine_triage.py
  tests/test_micromachine_integration_kit.py`: 172 passed, 748
  subtests passed.
- Patch apply check against clean MicroMachine checkout: passed.
- `integrations/micromachine/scripts/build_macos_local.sh`: passed.

Real SC2 strategy matrix smoke:

| Profile | Frame | Doctrine | Representative evidence | Latest production | Worker self-position blocks |
| --- | ---: | --- | --- | --- | ---: |
| `bio_pressure` | 5697 | `bio_pressure` | `bio_marauder_techlab -> BarracksTechLab` (`queued`) | `bio_marauder_techlab -> BarracksTechLab` | 0 |
| `tank_defensive_hold` | 5869 | `tank_defensive_hold` | `factory_transition -> Factory` (`queued`) | `factory_transition -> Factory` | 0 |
| `mech_transition` | 5585 | `mech_transition` | `factory_transition -> Factory` (`queued`) | `factory_transition -> Factory` | 0 |
| `drop_harassment` | 5543 | `drop_harassment` | `factory_transition -> Factory` (`queued`, archive match) | latest normal production later continued as `Marine` | 0 |
| `expand_macro` | 5712 | `expand_macro` | `expand_macro -> CommandCenter` (`queued`) | `expand_macro -> CommandCenter` | 0 |

Evidence artifact:
`/private/tmp/voi-mm-strategy-matrix/runs/20260630230706-12686/strategy_matrix_summary.jsonl`.

Worker root-cause counters for every strategy profile:

- `self_position_command_block_count=0`.
- `repeat_order_suppressed_count=0`.
- Archived max `self_position_command_block_count=0`.
- Archived max `repeat_order_suppressed_count=0`.
- `root_cause_status=none`.

## Live QA Boundary

These checks prepare the build for user live QA. They do not replace visual QA in
the SC2 client, but they make false success impossible for the two reported
problem classes.

Live QA should still visually confirm that the SCV no longer receives repeated
self-position right-clicks in the client and that user text commands visibly
change the chosen strategy profile, production bias, and manager evidence in the
dashboard.
