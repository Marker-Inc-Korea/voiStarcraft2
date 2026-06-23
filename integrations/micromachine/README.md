# MicroMachine Runtime Integration Kit

This directory contains the concrete integration surface for connecting
voiStarcraft2 policy modulation to the public MicroMachine C++ bot.

Verified upstream:

- Repository: `https://github.com/RaphaelRoyerRivard/MicroMachine`
- Commit: `eb893161371dab975a0a7e600f9e250ac03ec1ef`

## Files

| File | Purpose |
| --- | --- |
| `HOOK_MANIFEST.json` | Real upstream source/function hook manifest for manager-level modulation. |
| `voi_policy_blackboard.hpp` | Header-only C++17 reader for `latest_modulation.kv`. |
| `patches/0001-macos-latest-s2client-policy-blackboard.patch` | Verified patch bundle for current `s2client-api`, libvoxelbot linking, and the voi blackboard hook. |
| `scripts/build_macos_local.sh` | Reproducible macOS build script for `s2client-api` plus patched MicroMachine. |
| `scripts/smoke_macos_local.sh` | Local StarCraft II smoke script that writes modulation and requires both telemetry and real macro-opening evidence. |
| `scripts/soak_macos_local.sh` | Long-run local StarCraft II soak gate with deterministic artifacts and production sign-off classifiers. |
| `scripts/soak_matrix_macos_local.sh` | Map/race/difficulty matrix runner that aggregates per-case soak reports. |
| `MICROMACHINE_MAP_POOL.json` | Versioned required/diagnostic/excluded map-pool contract used by production matrix defaults. |

## Runtime Files

The Python sidecar writes these files through
`starcraft_commander.micromachine_runtime.MicroMachineFilesystemBlackboard`:

| File | Consumer |
| --- | --- |
| `latest_modulation.json` | Auditable canonical JSON blackboard update. |
| `latest_modulation.kv` | C++ stdlib-readable flat overlay for MicroMachine hooks. |
| `modulation_updates.jsonl` | Append-only update audit log. |
| `latest_telemetry.json` | Latest MicroMachine telemetry emitted back to Python. |
| `telemetry.jsonl` | Append-only telemetry audit log. |

## Wiring Steps

1. Copy `voi_policy_blackboard.hpp` into MicroMachine `src/`.
2. Add a `voi::PolicyBlackboard` member to `GameCommander` or `CCBot`.
3. In `GameCommander::onStart()` and `GameCommander::onFrame(bool executeMacro)`, call:

   ```cpp
   m_policyBlackboard.loadFromFile("<shared-dir>/latest_modulation.kv");
   if (!m_policyBlackboard.isProtocolCompatible()
       || m_policyBlackboard.isExpired(m_bot.GetGameLoop())) {
       // Ignore stale/incompatible modulation.
   }
   ```

4. Pass the blackboard by reference or expose a read-only accessor to managers.
5. Read biases only as modulation, never direct orders:

   ```cpp
   const float defendBias = blackboard.getFloat("combat.defend_bias", 0.0f);
   const bool forceRetreat = blackboard.getBool("emergency.force_retreat", false);
   ```

6. Emit telemetry back to the same shared directory as `latest_telemetry.json`.

## Required Hook Points

Use `HOOK_MANIFEST.json` as the source of truth. The central polling point is
`src/GameCommander.cpp` in `GameCommander::onFrame(bool executeMacro)`, before
the manager calls. Manager-domain reads should then be attached around:

- `ProductionManager::onFrame(bool executeMacro)`
- `ProductionManager::manageBuildOrderQueue()`
- `CombatCommander::onFrame(const std::vector<Unit> & combatUnits)`
- `CombatCommander::shouldWeStartAttacking()`
- `ScoutManager::onFrame()` / `ScoutManager::moveScouts()`
- `WorkerManager::onFrame(bool executeMacro)`
- `CombatAnalyzer::onFrame()`
- `Squad::onFrame()`

## Production Constraint

The C++ bot must never treat the blackboard as a raw command stream. It should
only read bounded keys such as `combat.defend_bias`, `economy.expand_bias`, or
`emergency.force_retreat` and then let existing MicroMachine managers decide
how to act.

## Local Smoke Test Gate

This repository can test the Python sidecar, file transport, key flattening,
and manifest consistency without a local StarCraft II installation. The local
smoke script is the runtime gate for the C++ bot: it removes stale telemetry,
launches patched MicroMachine against local StarCraft II, and fails unless
`latest_telemetry.json` reaches `MIN_TELEMETRY_FRAME` with the active
`smoke-001` modulation and the bot actually executes the opening macro path.
The smoke pins the Terran strategy to `Terran_MarineRush` and uses a low
computer difficulty so the gate measures connection, manager initialization,
and opening macro execution rather than combat pressure. The smoke bootstrap
also ensures the Terran opening trains at least one Marine after the first
Barracks so a clean upstream checkout cannot pass by only constructing workers
and buildings.
The gate requires `build command type=TERRAN_SUPPLYDEPOT`,
`TERRAN_SUPPLYDEPOT UnderConstruction`,
`build command type=TERRAN_BARRACKS`, and
`TERRAN_BARRACKS UnderConstruction`, followed by a post-Barracks unit command
such as `create unit item=Marine result=1`, a `build command
type=TERRAN_REFINERY`, and positive mineral and gas income after the Refinery completes. It
fails immediately on known false positive signatures such as `Failed to place
Barracks`, `Failed to place Refinery`, or exact building cancellation lines.

## Verified macOS Runtime

The local machine smoke completed these boundaries on 2026-06-21:

- StarCraft II install: `/Users/jinminseong/Desktop/StarCraft2/StarCraft II`
- SC2 executable used by `s2client-api`: `Versions/Base96883/SC2.app/Contents/MacOS/SC2`
- Map: `AcropolisLE.SC2Map`
- `s2client-api` commit: `614acc00abb5355e4c94a1b0279b46e9d845b7ce`
- MicroMachine commit: `eb893161371dab975a0a7e600f9e250ac03ec1ef`
- MicroMachine executable: `/private/tmp/MicroMachine/build-latest-api/bin/MicroMachine`

Observed smoke evidence:

```text
Connected to 127.0.0.1:8167
WaitJoinGame finished successfully.
Terran VS Zerg on Acropolis LE
0: initializeManagers | MicroMachine v1.18.0
894: constructAssignedBuildings | build command type=TERRAN_SUPPLYDEPOT
TERRAN_SUPPLYDEPOT UnderConstruction
3550: constructAssignedBuildings | build command type=TERRAN_BARRACKS
TERRAN_BARRACKS UnderConstruction
4590: create | create unit item=Marine result=1
4940: constructAssignedBuildings | build command type=TERRAN_REFINERY
TERRAN_REFINERY UnderConstruction
Gas income:       67
```

Telemetry written by the patched bot:

```json
{"active_modulation_ids":["smoke-aggressive-pressure"],"bot_name":"MicroMachine","frame":6076,"last_failure":null,"managers":{"CombatCommander":{"active":true,"aggression":0.55,"bounded_intervention":true,"combat_unit_count":1,"defend_bias":0.15,"force_retreat":false},"GameCommander":{"combat_aggression":0.55,"combat_defend_bias":0.15,"emergency_cancel_attacks":false,"emergency_force_retreat":false,"policy_active":true,"scouting_require_fresh_enemy_observation":false,"scouting_risk_tolerance":0.45,"scouting_scout_priority":0.7,"update_id":"smoke-aggressive-pressure"},"ScoutManager":{"active":true,"bounded_intervention":true,"has_worker_scout":true,"require_fresh_enemy_observation":false,"risk_tolerance":0.45,"scout_priority":0.7,"scout_unit_count":1,"status":"Enemy base unknown, exploring","under_attack":false}},"protocol_version":"voi-mm-bridge/v1","race":"Terran"}
```

The patch now defers heavy MicroMachine manager initialization until the first
valid observation is available, preventing the previous frame-0
`Invalid setup detected. | 0x0000000` / `0x0000001` base-location path. It also
adds read-only policy accessors and wires `emergency.force_retreat`,
`emergency.cancel_attacks`, `combat.defend_bias`, and `combat.aggression` into
`CombatCommander` attack/retreat thresholds. Issue 10.10 extends this evidence
with two bounded intervention profiles: `smoke-defensive-hold` starts as a
macro-safe defensive/scouting bias, then the smoke publishes
`smoke-aggressive-pressure` only after the macro gate is already satisfied. The
patched bot writes both `latest_telemetry.json` and `telemetry.jsonl`, so the
smoke can verify ScoutManager activity, CombatCommander activity, manager-level
bias changes, stale modulation, inactive policy, and the defensive-to-aggressive
transition without issuing raw unit commands. The building manager now trusts
the authoritative SC2 placement query for normal non-addon buildings before
canceling tracked construction, which prevents an opening Barracks from being
removed solely because the legacy local tile cache disagrees. The worker
manager applies the same principle to completed friendly Refineries: if the base
is not under attack and the depot/refinery are complete, a path-safety false
negative no longer prevents gas-worker assignment. This fallback is required for
a full-game macro smoke because the Refinery can be built successfully while gas
income remains zero if workers are never assigned. Keyword:
`gas-worker path-safety fallback`.

The `s2client-api` patch also turns the macOS process launch into an
environment-preserving `execve`, avoids invalid observer/computer setup fields,
enables raw observation options needed by MicroMachine, and converts mismatched
image-grid payloads from process-killing assertions into ordinary false query
results. The smoke must run outside Codex filesystem/network sandboxing because
SC2 API loopback and GUI process launch are host-level operations.

## Long-Run Soak Gate

`scripts/soak_macos_local.sh` is the production sign-off gate after the short
smoke passes. It launches the same patched MicroMachine runtime, publishes a
macro-safe `soak-defensive-hold` profile first, switches to
`soak-aggressive-pressure` only after the required SupplyDepot, Barracks,
Refinery, Marine/Reaper, and positive mineral/gas income evidence appears, and
keeps refreshing the aggressive profile so a long run cannot silently fall back
to stale modulation.

Example:

```bash
MICROMACHINE_DIR=/private/tmp/MicroMachine \
MICROMACHINE_BUILD_DIR=/private/tmp/MicroMachine/build-latest-api \
SOAK_ENEMY_RACE=Zerg \
SOAK_ENEMY_DIFFICULTY=1 \
SOAK_TARGET_FRAME=12000 \
SOAK_TIMEOUT_SECONDS=1200 \
integrations/micromachine/scripts/soak_macos_local.sh
```

Configurable thresholds:

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `SOAK_ENEMY_RACE` | `Zerg` | Built-in AI enemy race: `Terran`, `Protoss`, `Zerg`, or `Random`. |
| `SOAK_ENEMY_DIFFICULTY` | `1` | Built-in AI difficulty from 1 to 10. |
| `SOAK_TARGET_FRAME` | `12000` | Required latest telemetry frame for pass. |
| `SOAK_TIMEOUT_SECONDS` | `1200` | Wall-clock budget before timeout failure. |
| `SOAK_TELEMETRY_STALL_SECONDS` | `90` | Fails if telemetry stops updating before target. |
| `SOAK_PRODUCTION_DEADLOCK_FRAME` | `9000` | Fails if opening production evidence is still missing. |
| `SOAK_PRODUCTION_STALL_FRAMES` | `8000` | Fails if no later production log evidence appears within this frame window after target. |
| `SOAK_INCOME_STALL_FRAMES` | `2000` | Fails if recent mineral/gas income evidence is missing near the target frame. |
| `SOAK_MAX_PLACEMENT_FAILURES` | `3` | Fails repeated placement/path/cancel loops. |
| `SOAK_MODULATION_CONSUMPTION_GRACE_FRAMES` | `128` | Fails if telemetry does not consume the latest modulation refresh after this frame grace window. |
| `SOAK_AGGRESSIVE_MIN_FRAME` | `13000` | Keeps the 12000-frame production gate in defensive hold, then permits aggressive-pressure modulation for longer soaks. |
| `SOAK_MAX_ATTEMPTS` | `2` | Bounded retry count for map/start-location flakes; every attempt keeps its own artifact directory. |
| `SOAK_NON_RETRYABLE_FAILURE_CODES` | classifier terminal failures | Failure codes that stop retry immediately instead of hiding deterministic bot/runtime failures behind a later pass. |
| `SOAK_ARTIFACT_ROOT` | `/private/tmp/voi-mm-soak` | Parent for run archives. |
| `SOAK_RUN_DIR` | `${SOAK_ARTIFACT_ROOT}/${SOAK_RUN_ID}` | Exact deterministic artifact directory when supplied by CI/user. |

The top-level soak writes a summary `soak_report.json` into `SOAK_RUN_DIR`.
Each bounded retry writes fixed artifact names into
`SOAK_RUN_DIR/attempt-N/`:

| Artifact | Meaning |
| --- | --- |
| `micromachine.log` | Full MicroMachine runtime log. |
| `latest_telemetry.json` | Latest C++ telemetry snapshot. |
| `telemetry.jsonl` | Telemetry archive used to prove manager-level intervention. |
| `latest_modulation.json` / `latest_modulation.kv` | Current bounded policy modulation. |
| `modulation_updates.jsonl` | Modulation refresh audit trail. |
| `soak_report.json` | Final classifier report; `ok: true` is required for sign-off. |

The Python classifier behind the script is
`starcraft_commander.micromachine_soak.classify_micromachine_soak`. It detects
MicroMachine crash/early process stop, SC2 disconnect signatures, telemetry
stall, repeated placement failures, no-production deadlock, production stall,
recent income stall, missing `CombatCommander`/`ScoutManager` bounded
intervention, and stale or inactive modulation. The live loop runs the classifier with
`--allow-incomplete` so target-frame progress is allowed while terminal
failures still stop the run immediately. The final pass requires target frame,
macro evidence, CombatCommander and ScoutManager intervention evidence, and no
classifier failures. The default 12k-frame gate remains in defensive hold; for
longer soaks, aggressive-profile refreshes begin after
`SOAK_AGGRESSIVE_MIN_FRAME` and use frame-suffixed update IDs such as
`soak-aggressive-pressure-13000`, so the final telemetry must prove MicroMachine
consumed the latest refresh rather than merely reporting an older still-active
profile. When the script stops the game after the target frame,
`soak_report.json` records `termination_reason:
target_frame_reached_cleanup` instead of presenting the cleanup as a natural
game exit. If an attempt hits a deterministic classifier failure, it still
writes its own final `soak_report.json`; the bounded retry wrapper stops on
non-retryable classifier failures instead of masking them, promotes the first
passing attempt with artifact paths rewritten relative to top-level
`SOAK_RUN_DIR`, or writes a failed top-level summary with all attempt reports.

Verified local sign-off evidence for Issue 10.11:

| Run | Evidence |
| --- | --- |
| `issue-10-11-final-acropolis-v3` | `SOAK_RUN_DIR=/private/tmp/voi-mm-soak/issue-10-11-final-acropolis-v3`, `MAP_FILE=AcropolisLE.SC2Map`, top-level `soak_report.json` has `ok: true`, `latest_frame: 12056`, `macro_evidence_ok: true`, `manager_intervention_ok: true`, and `termination_reason: target_frame_reached_cleanup`. |
| `issue-10-11-final-thunderbird-v2` | Negative control: `MAP_FILE=Ladder2019Season3/ThunderbirdLE.SC2Map` stopped on non-retryable `no_production_deadlock` at frame 7089, proving failed macro games are not hidden by retries. |

## Matrix Diversity Gate

Use `scripts/soak_matrix_macos_local.sh` for map, race, and difficulty
diversity. The runner creates one `soak_macos_local.sh` artifact directory per
case and writes an aggregate `matrix_report.json`.
When explicit `SOAK_MATRIX_*` overrides are not provided, the runner reads
`MICROMACHINE_MAP_POOL.json` and uses the selected
`SOAK_MATRIX_QUALIFICATION_TIER` defaults. The default tier is `production`,
which currently requires `AcropolisLE.SC2Map` against Zerg, Protoss, and Terran
at difficulty 1. `Ladder2019Season3/ThunderbirdLE.SC2Map` remains diagnostic
until the no-production deadlock blocker is fixed. Excluded maps are outside
the support contract and cannot be used for production sign-off.
Each case writes `preflight_report.json` before SC2 launch. Known unsupported,
missing, geometry-risk, or placement-risk maps are turned into ordinary failed
case artifacts instead of being hidden by a late no-production deadlock.
Set `SOAK_MATRIX_MAP_ROOTS` to a colon-separated list when the preflight should
verify local map availability.

Example:

```bash
MICROMACHINE_DIR=/private/tmp/MicroMachine \
MICROMACHINE_BUILD_DIR=/private/tmp/MicroMachine/build-latest-api \
SOAK_MATRIX_RUN_ID=production-diversity-001 \
SOAK_MATRIX_QUALIFICATION_TIER=production \
integrations/micromachine/scripts/soak_matrix_macos_local.sh
```

Production qualification must run without `SOAK_MATRIX_ALLOW_FAILURES` and
requires `matrix_report.json.failed == 0`. Set `SOAK_MATRIX_ALLOW_FAILURES=1`
only for diagnostics or negative-control runs; those reports are evidence for
debugging, not production sign-off.

Example diagnostic run for the known Thunderbird blocker:

```bash
SOAK_MATRIX_RUN_ID=diagnostic-thunderbird-001 \
SOAK_MATRIX_QUALIFICATION_TIER=diagnostic \
SOAK_MATRIX_MAP_FILES="Ladder2019Season3/ThunderbirdLE.SC2Map" \
SOAK_MATRIX_ALLOW_FAILURES=1 \
integrations/micromachine/scripts/soak_matrix_macos_local.sh
```

The manifest records this as
`thunderbird_walloff_geometry_no_production_deadlock` with the known artifact
path, reproduction command, root-cause candidates, evidence signatures, and
promotion criteria. See `docs/micromachine-thunderbird-blocker.md`. Do not
promote Thunderbird to production until it passes the documented 12000-frame
matrix gate with `SOAK_MATRIX_ALLOW_FAILURES=0`.

Verified local matrix evidence for Issue 10.12:

| Run | Evidence |
| --- | --- |
| `issue-10-13-acropolis-races-zero-v4` | `/private/tmp/voi-mm-soak-matrix/issue-10-13-acropolis-races-zero-v4/matrix_report.json` passed with `SOAK_MAX_ATTEMPTS=1`, `passed=3`, `failed=0` for `AcropolisLE.SC2Map` against `Zerg`, `Protoss`, and `Terran` difficulty 1. |

See `docs/micromachine-production-ops.md` for the CI, self-hosted soak, and
neural/SOTA provider runbook.

## Production Sign-Off Criteria

Issue #10 is production-ready for user QA only when all gates below pass on the
same patched MicroMachine build:

| Gate | Required evidence |
| --- | --- |
| Unit contracts | `pytest` passes for DSL, provider compiler, bridge, runtime, observability, and soak classifier. |
| Patch reproducibility | MicroMachine patch applies to upstream commit `eb893161371dab975a0a7e600f9e250ac03ec1ef`. |
| Smoke | `smoke_macos_local.sh` reaches `MIN_TELEMETRY_FRAME`, produces real macro evidence, and shows active aggressive modulation. |
| Manager intervention | Telemetry proves both `CombatCommander.bounded_intervention=true` and `ScoutManager.bounded_intervention=true`. |
| Long-run soak | `soak_macos_local.sh` reaches `SOAK_TARGET_FRAME` and writes `soak_report.json` with `ok: true`. |
| Matrix diversity | `soak_matrix_macos_local.sh` writes a reviewed `matrix_report.json` with `failed=0`. |
| Neural/provider swap | Callers use `MicroMachineModulationBackend`, `publish_policy_modulation_provider_output(...)`, or `publish_neural_representation_modulation(...)`, so future neural representation providers publish the same bounded vector contract without raw SC2 controls. |
| CI/operations | Hosted CI runs unit contracts and script syntax; real SC2 soak matrices run from the self-hosted macOS workflow. |

Non-blocking risks after sign-off:

| Risk | Mitigation |
| --- | --- |
| AI Arena ladder strength is not automatically proven by local soak. | Run later ladder/evaluation batches using the same artifact report format. |
| The C++ hook remains a patch against a fixed MicroMachine commit. | Re-run patch apply and soak when upstream commit changes. |
| User intent quality depends on provider output. | Invalid or raw-control provider payloads are rejected before reaching MicroMachine. |
