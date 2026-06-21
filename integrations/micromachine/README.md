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
type=TERRAN_REFINERY`, and positive gas income after the Refinery completes. It
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
