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
| `patches/0002-live-tactical-operation-fixes.patch` | Live tactical fixes for addon relocation ownership, exact composition rally/launch, tank siege gating, Scout/MainAttack ownership, and Viking objective following. |
| `patches/0003-production-live-qa-blockers.patch` | Live combat blockers fix for producer lift/train ability ownership, grounded addon-relocation cooldown, exact squad acquisition and thresholds, forward rallying, combat-scout ownership, stale base-defense release, tank launch/siege and morph retry, Scout Viking objective preservation, shared focus fire, policy-driven kiting, and invalid queued worker orders. |
| `patches/0004-live-operation-state-machine.patch` | Live operation state-machine fixes for rally latch keys, invalid zero-position action rejection, morph availability guards, producer command ownership, LAND target validation, and proxy Barracks grounding under active VOI policy. |
| `patches/0005-addon-relocation-recovery.patch` | Root fix for grounded LAND placement rejection, unbounded addon-site queries, producer lift/land loops, and blocked Factory/Starport addon production. |
| `patches/0006-grounded-addon-candidate-fix.patch` | Removes the second addon blocker by trusting BuildingPlacer's full producer-plus-addon footprint before lift and deferring dynamic occupancy validation to the flying LAND query. |
| `patches/0007-guaranteed-producer-grounding.patch` | Prevents Barracks/Factory/Starport from remaining airborne after addon relocation by resolving both producer-specific and generic SC2 LAND abilities, then using a rotating local-footprint fallback when SC2 placement queries report false negatives during bounded emergency grounding. |
| `patches/0008-emergency-land-query-fallback.patch` | Removes false local landing rejections from combat blocked tiles and circular proximity checks, then alternates producer-specific and generic nonqueued LAND commands when SC2 query APIs omit both valid answers. |
| `patches/0009-grounded-production-and-observed-targeting.patch` | Keeps production buildings grounded while clearing blocked addon tiles, uses only producer-specific LAND abilities, continues tech planning after supply/worker queueing, requires observed enemy evidence before enemy-base attacks, and keeps launched operations alive after casualties or temporary target loss. |
| `patches/0010-exact-composition-production-progress.patch` | Prevents exact Marine compositions from stalling behind Factory/Starport transitions, caps direct Marine continuity at the requested count, bootstraps a combat scout, allows one-unit fresh-observation scouting, and makes every Starport unit request imply its Factory prerequisite. |
| `patches/0011-production-resource-operation-persistence.patch` | Keeps production buildings grounded unless they are escaping real danger, consumes gas policy in WorkerManager, closes exact production and attached-addon prerequisites for all direct Terran combat units, requires observed targets by default, relaunches after bounded regrouping, and connects Ghost, Widow Mine, and Liberator role abilities to valid SC2 commands. |
| `patches/0012-live-operation-unblock.patch` | Quarantines blocked addon producers without lifting them, queues one addon-clear replacement Factory, rotates stalled combat scouts across enemy-start candidates, separates candidate locations from observed enemy evidence, and advances flank operations after the squad reaches the stage. |
| `patches/0013-stable-flank-stage-latch.patch` | Freezes the first valid flank or safe-path stage for the lifetime of an operation so moving observations cannot keep shifting the waypoint; 60% of the squad must reach it before the final observed target is restored. |
| `patches/0014-production-staging-and-observed-operation.patch` | Keeps new units at a home staging rally until squad assignment, blocks needless addon relocation, retries addon-safe Factory replacement, sends combat scouts before blind attacks, preserves observed-target operations through temporary contact loss, and keeps semantic operations active beyond their safety lease. |
| `patches/0015-addon-query-footprint-validation.patch` | Rejects Barracks, Factory, and Starport query-fallback placements unless every future 2x2 addon tile is terrain-buildable and free of creep, structures, minerals, and geysers. |
| `patches/0016-authoritative-addon-placement-query.patch` | Replaces stale local addon terrain/static-unit rejection with batched SC2 body and exact 2x2 addon-footprint placement queries, allowing legal Factory/Starport sites without accepting body-only placements. |
| `patches/0017-authoritative-addon-execution.patch` | Uses the same SC2-authoritative 2x2 addon-footprint query immediately before issuing an addon command, retries temporarily blocked producers after 15 seconds, and caps addon-clear Factory replacement at two total Factories. |
| `patches/0018-continuous-army-macro.patch` | Treats requested combat composition as the minimum MainAttack package, continues proportional reinforcement waves to the supply cap, fills macro SCVs, and scales Barracks/Factory/Starport with hard caps when resources accumulate. |
| `patches/0019-continuous-army-economy-scaling.patch` | Scales Refineries from requested composition gas demand, expands to saturated free bases with a three-town-hall cap, and unlocks second Factory/Starport production from gas infrastructure instead of requiring an idle gas bank. |
| `patches/0020-standing-composition-reinforcement-waves.patch` | Preserves the launched MainAttack for uncapped standing operations and atomically joins each complete home-staged composition wave instead of repeatedly trimming the army back to the initial minimum. |
| `patches/0021-offensive-sweep-self-base-exclusion.patch` | Prevents contact-lost offensive sweeps from selecting the self starting base, self-occupied expansions, or home-adjacent bases; enemy occupancy and remote unexplored enemy-side bases are preferred. |
| `patches/0022-bounded-placement-query-cache.patch` | Removes duplicate placement searches, reuses revalidated placements, caps authoritative query candidates, and bounds the legacy local fallback so building placement cannot stall `OnStep` for seconds. |
| `patches/0023-production-facility-stability-and-tank-recovery.patch` | Refuses every Barracks/Factory/Starport lift while VOI policy is active unless relocation is explicitly enabled, automatically maintains supply for continuous mixed armies, permits a third addon-safe Factory recovery attempt, and defers Vikings until requested tank tech is online. |
| `patches/0024-balanced-composition-wave-production.patch` | Advances continuous mixed-army production only when every requested unit type completes the same wave, and latches that cumulative target per operation so surplus Marines cannot cause extra Tanks or combat losses cannot deadlock a complete home reinforcement wave. |
| `patches/0025-exact-composition-production-unblock.patch` | Prevents broad doctrine biases from repeatedly queueing unrequested combat units, keeps expansion nonblocking until every first-wave unit is represented, and rate-limits rejected production-building lift diagnostics without permitting the lift. |
| `patches/0026-continuous-combat-production-relaunch.patch` | Gives exact-composition operations exclusive ownership of combat-unit selection, prevents failed expansion placement from blocking standing army production, rate-limits reconciliation churn diagnostics, lets surviving launched units reattack after bounded regroup without rebuilding the original minimum first, and closes the Thor-to-Armory prerequisite consumer path. |
| `patches/0027-resource-throughput-and-expansion-backoff.patch` | Keeps Marine production aligned with cumulative reinforcement waves, saturates every completed Refinery instead of globally capping gas workers, quarantines repeatedly failing passive expansions without blocking army production, and exposes resource/supply/gas throughput telemetry. |
| `patches/0028-startup-telemetry-initialization.patch` | Seeds UnitInfo before startup consumers, emits initial telemetry only after subordinate manager initialization, and reads completed Refineries from the startup-safe ally-unit index. |
| `patches/0029-gas-worker-completion-and-cap.patch` | Prevents gas workers from entering incomplete Refineries and trims each completed Refinery to its current target before any base/depot lookup fallback. |
| `patches/0030-stable-offensive-sweep-target.patch` | Keeps a lost-contact offensive sweep on one remote target until squad-majority arrival or a bounded route-stall timeout, instead of repointing every frame. |
| `patches/0031-adaptive-support-composition.patch` | Preserves the first exact combat wave, then latches bounded counter-unit targets per operation so Marauders, Hellions, Widow Mines, Cyclones, Thors, Medivacs, Liberators, Banshees, Ravens, Vikings, and Battlecruisers can be selected from observed enemy and strategic conditions without queue oscillation. |
| `patches/0032-operation-scoped-adaptive-combat-closure.patch` | Closes the adaptive path end to end: counts completed/training units once, shares the combat operation identity, expires stale observations under a bounded budget, treats support counts as additive, recovers occupied add-on producers, and assigns produced support units into the live MainAttack operation. |
| `patches/0033-review-closure-operation-identity-and-full-composition.patch` | Lets explicit MainAttack operations reclaim required Reapers and Banshees from lower-priority Harass squads and consumes all 32 accepted composition and unit-role entries across production, scouting, combat launch, micro roles, operation identity, and telemetry. |
| `patches/0034-semantic-operation-production-closure.patch` | Separates semantic operation state from telemetry `task_id`/publication IDs, aggregates duplicate unit counts defensively, and bootstraps Barracks, gas, and add-on prerequisites for exact Marauder/Reaper and other bio requests. |
| `patches/0035-adaptive-pressure-stable-operation-key.patch` | Keeps observed-enemy counter production active for one-shot and first-wave exact MainAttack operations, blocks optional adaptive queue growth when the selected unit cannot fit under the completed 200-supply cap, and preserves operation state when composition, role, or unit-class entries arrive in a different order. |
| `patches/0036-tactical-nuke-command-hierarchy.patch` | Closes tactical-nuke prerequisites and execution from Factory/Ghost Academy/Ghost/payload production through Ghost reservation, safe staging, SC2 command submission, and observed cast confirmation. |
| `patches/0037-location-intent-target-lock.patch` | Pins tactical-nuke combat scouts to the requested enemy location, forbids fallback to home-adjacent scout targets, restricts nuke candidates to the requested enemy-main or enemy-natural anchor, and reports target/anchor distance and match telemetry. |
| `patches/0038-explicit-terran-ability-execution.patch` | Executes explicit Terran abilities and mode changes through CombatCommander with SC2 availability, target, placement, and range guards; closes requested caster/upgrade prerequisites; emits generic actor/action telemetry; and protects the reserved tactical-nuke Ghost at home. |
| `patches/0039-explicit-scout-command-epoch.patch` | Treats each explicit combat-scout update as a fresh command epoch and forces one matching SC2 MOVE submission before ordinary duplicate suppression resumes. |
| `patches/0040-standing-production-continuity-closure.patch` | Keeps standing unit production active while operation-layer scout/attack commands are overlaid, prevents Marine continuity from being held behind requested Tank tech, scales gas/facilities from standing targets, and stops ground siege bias from creating an unrequested Liberator/Starport transition. |
| `patches/0041-explicit-ability-caster-production-priority.patch` | Treats explicit ability unit classes as authoritative caster requests across supported Terran bio, mech, air, and capital units; builds their prerequisite lane before unrelated doctrine work, waits for a completed Factory before Starport transition, and releases the priority hold once the caster is queued, training, or complete. |
| `patches/0042-explicit-ability-observation-confirmation.patch` | Keeps non-nuke explicit abilities executing after SC2 submission until a subsequent observation confirms the effect, retries safely after a bounded confirmation timeout, and preserves location-derived target/staging plus AbilityTask submission and confirmation telemetry so planned, submitted, pending, and observed completion remain distinct. |
| `patches/0043-explicit-ability-production-isolation.patch` | Isolates missing explicit-ability caster production from unrelated legacy Starport/Banshee tech, bounds build-queue inspection, and skips the redundant unbounded path-safety search after authoritative SC2 macro-placement validation. |
| `patches/0044-explicit-ability-attempt-lifecycle.patch` | Binds each explicit ability action to its exact update, task, ability, and attempt generation; separates planned, submitted, observed-accepted, and effect-observed phases; suppresses stale or irreversible duplicate submissions; and confirms actual unit state through type, cloak, buff, cargo, spawn, destination, order, energy, and availability observations. |
| `patches/0045-explicit-ability-review-closure.patch` | Uses the SC2 capability-query Stim ID for Marine/Marauder explicit casts, prevents an empty Medivac from falsely completing unload-all while another scoped Medivac still carries passengers, and gives active explicit siege, burrow, cloak, vehicle, Viking, and Liberator state commands ownership over inverse autonomous mode changes for the full policy TTL. |
| `patches/0046-authoritative-addon-runtime-clearance.patch` | Trusts the authoritative SC2 query for the exact 2x2 add-on footprint instead of rejecting valid adjacent production structures through a broad radius heuristic, while clearing only friendly mobile units whose collision boxes actually overlap those tiles. |
| `patches/0047-banshee-unit-specific-cloak-command.patch` | Submits Banshee cloak and decloak with the SC2 unit-specific executable ability IDs while retaining generic capability-query remap fallback, across explicit DSL execution, unit-role micro, and autonomous RangedManager logic. |
| `patches/0048-allied-cloak-observation-confirmation.patch` | Preserves SC2 `CloakedUnknown` and `CloakedAllied` observations through the patched client SDK, removes per-frame unsupported-cloak log flooding, and teaches Banshee autonomous micro to treat the allied state as cloaked, so explicit cloak commands are confirmed from observed unit state rather than timing out after a successful cast. |
| `patches/0049-explicit-ability-caster-ownership.patch` | Gives the selected explicit-ability caster exclusive movement/action ownership during staging and confirmation, preventing Squad or unit micro from overwriting the order and causing per-frame SC2 command resubmission; direct explicit actions and completed attempts remain unblocked. |
| `patches/0050-explicit-ability-staging-single-flight.patch` | Binds an issued explicit-ability staging MOVE to its caster and target for the route lifetime, suppressing duplicate submissions while position observations show progress and releasing ownership only when bounded stalled-route recovery rejects the route. |
| `scripts/build_macos_local.sh` | Reproducible macOS build script for `s2client-api` plus patched MicroMachine. |
| `scripts/probe_macos_local.sh` | Standalone `s2client-api` bootstrap probe that proves CreateGame/JoinGame produces own starting units before MicroMachine is evaluated. |
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
- `ProductionManager::putImportantBuildOrderItemsInQueue()`
- `ProductionManager::manageBuildOrderQueue()`
- `BuildingManager::assignWorkerToUnassignedBuilding(Building &, bool)`
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

## Build Identity

`scripts/build_macos_local.sh` writes
`$MICROMACHINE_BUILD_DIR/voi_build_identity.json` after a successful build. The
clean build applies the MicroMachine patch bundle in numeric order from `0001`
through `0050`, then copies the blackboard header. The
report includes pinned MicroMachine and `s2client-api` commits, every patch
checksum, config/header checksums, binary path, and binary checksum. A pre-build
source attestation is finalized only after the executable exists, binding its
hash and size to the attested source inputs; replacement or non-executable
binaries fail identity verification. Matrix production signoff consumes that
identity and blocks `unrecorded` or mismatched builds.

## Bootstrap Probe Gate

`scripts/probe_macos_local.sh` runs the patched `s2client-api`
`voi_bootstrap_probe` binary without MicroMachine. It exists to separate a
StarCraft II CreateGame/JoinGame contract failure from a MicroMachine manager or
DSL failure. The probe launches SC2 through the same macOS LaunchServices-aware
s2client path, starts a Terran participant against a low-difficulty computer,
and writes `PROBE_OUTPUT` JSON.

The probe passes only when the first bootstrap window shows all of the
following:

- `ok=true`
- `self_count > 0`
- `self_worker_count > 0`
- `resource_depot_count > 0`

If the SC2 API connects but the participant has only neutral units, the probe
fails closed with `bootstrap_no_start_units`. That failure means MicroMachine
cannot initialize managers, cannot consume live DSL modulation, and must not be
treated as production-ready even if TCP connection and `WaitJoinGame` succeeded.
Set `VOI_SC2_CREATEGAME_MAP_DATA=1` to force the patched `s2client-api`
CreateGame request to attach `.SC2Map` bytes in `local_map.map_data` while
preserving `local_map.map_path`. This is a diagnostic compatibility path for
Base97364 hosts where path-only local maps join successfully but expose no own
starting units through raw observations.

Typical local command after `scripts/build_macos_local.sh`:

```bash
SC2_CLEAN_PORTS_BEFORE_LAUNCH=1 \
VOI_SC2_CREATEGAME_MAP_DATA=1 \
SC2_ATTACH_TIMEOUT_MS=120000 \
PROBE_MAX_FRAME=1200 \
integrations/micromachine/scripts/probe_macos_local.sh
```

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
The macOS direct-launch production default is realtime coordinator mode
(`SMOKE_FORCE_STEP_MODE=0`). Set `SMOKE_FORCE_STEP_MODE=1` only when
diagnosing forced-step behavior; on Base97364 this mode can exit at frame 0
before MicroMachine emits macro or scout evidence.
The smoke wrapper also has a bounded startup retry (`SMOKE_MAX_ATTEMPTS=3`,
`SMOKE_RETRY_SETTLE_SECONDS=15`) for direct-launch setup flakes before
`NO_START_UNITS_FRAME`. This does not hide bot-quality failures: once telemetry
reaches the startup threshold, once any opening macro command appears, or once
the logs show deterministic macro/bootstrap failures, the smoke stops
immediately and writes `smoke_attempts.json` with the failed attempt details.

## Verified macOS Runtime

The local machine smoke completed these boundaries on 2026-06-21:

- StarCraft II install: `/Users/jinminseong/Desktop/StarCraft2/StarCraft II`
- SC2 launcher used by `s2client-api`: `SC2_EXECUTABLE` when provided, otherwise `SC2_LAUNCH_MODE=auto`
- Map: `AcropolisLE.SC2Map`
- `s2client-api` commit: `614acc00abb5355e4c94a1b0279b46e9d845b7ce`
- MicroMachine commit: `eb893161371dab975a0a7e600f9e250ac03ec1ef`
- MicroMachine executable: `/private/tmp/voi-micromachine-runtime/MicroMachine/build-latest-api/bin/MicroMachine`

Launcher contract:

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `SC2_LAUNCH_MODE` | `auto` | `direct` forces a `Versions/Base*/SC2.app/Contents/MacOS/SC2` binary, `battlenet` forces the Battle.net wrapper, and `auto` prefers the pinned Base96883 binary when present, otherwise the latest direct Base binary. |
| `SC2_ATTACH_TIMEOUT_MS` | `120000` | Explicit `s2client-api` attach timeout passed as `-t` so host `ExecuteInfo.txt` cannot shorten the launch window. |
| `SC2_USE_RUNTIME_DIR_ARGS` | `0` | Opt-in direct-launch compatibility mode that passes `-dataDir ${SC2_ROOT_ALIAS} -tempDir ${SC2_TEMP_DIR}` through `VOI_SC2_EXTRA_ARGS`. Leave disabled on Base97364 hosts where those extra args prevent the SC2 API listener from opening. |
| `SC2_ROOT_ALIAS` | `/private/tmp/voi-sc2-root` | Symlink alias for the local StarCraft II install; avoids whitespace splitting in `VOI_SC2_EXTRA_ARGS`. |
| `SC2_TEMP_DIR` | `/private/tmp/voi-sc2-temp-micromachine` | SC2 temp directory passed through `VOI_SC2_EXTRA_ARGS` only when `SC2_USE_RUNTIME_DIR_ARGS=1`. |
| `SC2_CLEAN_PORTS_BEFORE_LAUNCH` | `1` | Kills stale processes bound to the configured SC2 API ports before launch, preventing false passes against an old SC2 session. |
| `SC2_POST_CLEAN_SETTLE_SECONDS` | `5` | Wait after stale SC2 port cleanup before relaunching; this avoids Base97364 teardown/relaunch races in repeated local smoke/probe/soak runs. |
| `SMOKE_MAX_ATTEMPTS` | `3` | Bounded retry count for direct-launch smoke startup flakes before `NO_START_UNITS_FRAME`; every attempt keeps its own `attempt-N/` blackboard. Runtime progress past the startup threshold, macro failures, and bootstrap-no-start-units failures are not retry-masked. |
| `SMOKE_RETRY_SETTLE_SECONDS` | `15` | Parent retry-loop wait before relaunching smoke after a retryable frame-0 startup failure. |
| `SC2_BATTLENET_EXECUTABLE` | `/Applications/Battle.net.app/Contents/MacOS/Battle.net` | Explicit `SC2_LAUNCH_MODE=battlenet` diagnostic launcher only; clean-start production smoke should use direct Base launch. |
| `SC2_BATTLENET_GAME` | `s2_kokr` | Battle.net game selector passed through `VOI_SC2_EXTRA_ARGS` when `SC2_LAUNCH_MODE=battlenet` is forced. |

On this host the old pinned Base96883 direct executable is no longer present.
Fresh launcher isolation on 2026-06-24 showed Base97364 can open the requested
SC2 API listener and complete `s2client-api` join/observation when launched
without direct runtime-dir extra args. The same `voi_probe` launch hangs before
the API listener when `-dataDir ... -tempDir ...` is injected through
`VOI_SC2_EXTRA_ARGS`. A second isolation pass showed Base97364 crashes if
internal `VOI_*` sidecar variables are inherited by the SC2 child process. The
macOS `s2client-api` launch patch therefore preserves the host environment for
LaunchServices compatibility but filters `VOI_*` before `execve`. The production
smoke/soak scripts keep `SC2_LAUNCH_MODE=auto` on direct Base launch, pass `-t
${SC2_ATTACH_TIMEOUT_MS}`, and leave direct runtime-dir args disabled by
default. Set `SC2_USE_RUNTIME_DIR_ARGS=1` only for hosts that require explicit
SC2 data/temp directories. The Battle.net wrapper is not a production fallback
because clean-start testing showed it can launch only the Battle.net shell
without opening the requested SC2 API port; it remains available only as an
explicit diagnostic mode.

Latest host re-check on 2026-06-25: the Python/blackboard contracts still
pass, direct Base97364 opens the SC2 API listener, and the patched
`s2client-api` bootstrap probe passes with `VOI_SC2_CREATEGAME_MAP_DATA=1`
(`self_count=9`, `self_worker_count=8`, `resource_depot_count=1`). The
patched MicroMachine production smoke also passed on the active Base97364 host
with realtime coordinator mode (`SMOKE_FORCE_STEP_MODE=0`, now the default):
`/private/tmp/voi-mm-smoke-issue-67-final` selected attempt 1/3,
`latest_telemetry.json` reached frame 5299, `CombatCommander` consumed the
aggressive modulation profile, `ScoutManager` had a live worker scout, and the
runtime logs showed SupplyDepot, Barracks, Refinery, Marine, mineral income,
and gas income evidence. Forced-step mode remains available as an explicit
diagnostic (`SMOKE_FORCE_STEP_MODE=1`), but it is not the production default on
this macOS direct-launch path because Base97364 can end the coordinator loop at
frame 0 before macro evidence is emitted.

The 12000-frame production soak gate also passed on Acropolis/Base97364 with
the same real MicroMachine binary and no mock runtime:
`/private/tmp/voi-mm-soak/issue-67-gasfix-acropolis-12000/soak_report.json`
selected attempt 1/3 and passed at frame 12060. The selected attempt had
`macro_evidence_ok=true`,
`manager_intervention_ok=true`, `target_reached=true`, no classifier failures,
and active defensive modulation
`soak-defensive_hold-refresh-7054`. The soak classifier consumes
`micromachine_combined.log`, which merges the wrapper log with the latest
MicroMachine runtime data log. The wrappers record each runtime log's pre-run
byte size in `runtime_log_baseline.tsv`, so production/macro evidence is
evaluated only from bytes appended by the current attempt rather than stale
historical bot-play logs.

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
VOI_SC2_CREATEGAME_MAP_DATA=1 \
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
| `SOAK_FORCE_STEP_MODE` | `0` | Realtime coordinator mode is the production default for macOS direct Base launch; set to `1` only for forced-step diagnostics. |
| `SOAK_TIMEOUT_SECONDS` | `1200` | Wall-clock budget before timeout failure. |
| `SOAK_TELEMETRY_STALL_SECONDS` | `90` | Fails if telemetry stops updating before target. |
| `SOAK_PRODUCTION_DEADLOCK_FRAME` | `9000` | Fails if opening production evidence is still missing. |
| `SOAK_PRODUCTION_STALL_FRAMES` | `6000` | Fails if no later production log evidence appears within this frame window after target. |
| `SOAK_INCOME_STALL_FRAMES` | `2000` | Fails if recent mineral/gas income evidence is missing near the target frame, unless recent worker-combat evidence proves the bot is actively defending instead of idle. |
| `SOAK_MAX_PLACEMENT_FAILURES` | `3` | Fails repeated placement/path/cancel loops. |
| `SOAK_MODULATION_CONSUMPTION_GRACE_FRAMES` | `128` | Fails if telemetry does not consume the latest modulation refresh after this frame grace window. |
| `SOAK_PROFILE_SEQUENCE` | `default_defensive_to_aggressive` | Profile schedule: one profile key, the default schedule, or comma-separated `profile@frame` entries. |
| `SOAK_AGGRESSIVE_MIN_FRAME` | `13000` | Keeps the 12000-frame production gate in defensive hold, then permits aggressive-pressure modulation for longer soaks. |
| `SOAK_MAX_ATTEMPTS` | `3` | Bounded retry count for frame-0 direct-launch startup flakes; every attempt keeps its own artifact directory. Runtime crashes, stalls, disconnects, and deterministic play-quality failures after frames progress are not retry-masked. |
| `SOAK_RETRY_SETTLE_SECONDS` | `15` | Parent retry-loop wait before relaunching after a retryable frame-0 startup failure. |
| `SOAK_NON_RETRYABLE_FAILURE_CODES` | deterministic play-quality failures | Failure codes that stop retry immediately instead of hiding deterministic bot/runtime failures behind a later pass. Only frame-0 startup failures limited to `micromachine_crash`, `micromachine_process_stopped`, and `telemetry_missing` are retryable by default. |
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

Named long-horizon profiles:

| Profile | Primary manager bias |
| --- | --- |
| `defensive_hold` | `CombatCommander`, `ScoutManager`, and squad defense/regroup bias. |
| `economic_expansion` | `WorkerManager` and `ProductionManager` economy continuity with safer defense. |
| `aggressive_pressure` | `CombatCommander`, `ScoutManager`, and harassment/main-army pressure. |
| `scouting_map_control` | `ScoutManager` fresh observations and light map-control pressure. |
| `tech_transition` | `ProductionManager`/tech bias toward factory, upgrades, and gas support. |
| `emergency_recovery` | Short-TTL emergency defense, retreat, repair, and regroup bias. |

The Python classifier behind the script is
`starcraft_commander.micromachine_soak.classify_micromachine_soak`. It detects
MicroMachine crash/early process stop, SC2 disconnect signatures, telemetry
stall, repeated placement failures, no-production deadlock, production stall,
recent income stall, missing `CombatCommander`/`ScoutManager` bounded
intervention, stale or inactive modulation, and missing expected strategy
profile tags. The live loop runs the classifier with
`--allow-incomplete` so target-frame progress is allowed while terminal
failures still stop the run immediately. The final pass requires target frame,
macro evidence, CombatCommander and ScoutManager intervention evidence, and no
classifier failures. The default schedule is
`SOAK_PROFILE_SEQUENCE=default_defensive_to_aggressive`: it publishes
`defensive_hold` at frame 0, then publishes `aggressive_pressure` only after
`SOAK_AGGRESSIVE_MIN_FRAME` and required macro evidence are both present. Custom
long-horizon schedules can run a single profile or a comma-separated
`profile@frame` sequence:

```bash
SOAK_PROFILE_SEQUENCE="defensive_hold@0,economic_expansion@6000,scouting_map_control@9000,tech_transition@13000" \
integrations/micromachine/scripts/soak_macos_local.sh
```

Every scheduled profile is built by
`build_micromachine_strategy_profile(...)`, published through
`MicroMachineFilesystemBlackboard`, and recorded in `modulation_updates.jsonl`.
The classifier receives the expected profile tags and fails with
`strategy_profile_missing` if the archive does not prove those bounded profiles
were published. Profile refreshes use frame-suffixed update IDs such as
`soak-aggressive-pressure-refresh-20000`, so final telemetry must prove
MicroMachine consumed the latest refresh rather than merely reporting an older
still-active profile. When the script stops the game after the target frame,
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
The `extended` tier keeps the same required map pool but expands the declared
race/difficulty matrix to Zerg, Protoss, and Terran at difficulties 1 and 2.
Each case writes `preflight_report.json` before SC2 launch. Known unsupported,
missing, geometry-risk, or placement-risk maps are turned into ordinary failed
case artifacts instead of being hidden by a late no-production deadlock.
Set `SOAK_MATRIX_MAP_ROOTS` to a colon-separated list when the preflight should
verify local map availability.

Example:

```bash
SOAK_MATRIX_RUN_ID=production-diversity-001 \
SOAK_MATRIX_QUALIFICATION_TIER=production \
integrations/micromachine/scripts/soak_matrix_macos_local.sh
```

Production qualification must run without `SOAK_MATRIX_ALLOW_FAILURES` and
requires `matrix_report.json.failed == 0`. Set `SOAK_MATRIX_ALLOW_FAILURES=1`
only for diagnostics or negative-control runs; those reports are evidence for
debugging, not production sign-off.
The runner also writes `soak_history_dashboard.json` and
`soak_history_dashboard.md`. Their `production_signoff` section checks the
recent-N enabled production runs against required map/race/difficulty/profile
coverage, failed-case absence, diagnostic/disabled exclusion, and optional
`SOAK_MATRIX_SIGNOFF_REQUIRED_BUILD_IDENTITY` matching. Attach both dashboard
files when asking for final production review.

The final release gate combines these dashboard artifacts with build identity,
unit-test evidence, and triage evidence:

```bash
python3 -m starcraft_commander.micromachine_release_gate \
  --history-root /private/tmp/voi-mm-soak-matrix \
  --build-identity-report /private/tmp/voi-micromachine-runtime/MicroMachine/build-latest-api/voi_build_identity.json \
  --unit-evidence /private/tmp/voi-mm-unit-evidence.json \
  --triage-report /private/tmp/voi-mm-soak-matrix/production-signoff-001/triage_report.json \
  --output-json /private/tmp/voi-mm-release-gate/release_gate.json \
  --output-markdown /private/tmp/voi-mm-release-gate/release_gate.md
```

It exits nonzero for missing, stale, disabled, diagnostic-only, failed, or
build-mismatched production evidence and leaves only manual user QA.

Example expanded required-pool matrix for user-side QA repetition:

```bash
SOAK_MATRIX_RUN_ID=extended-required-pool-001 \
SOAK_MATRIX_QUALIFICATION_TIER=extended \
integrations/micromachine/scripts/soak_matrix_macos_local.sh
```

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
| Matrix diversity | `soak_matrix_macos_local.sh` writes a reviewed `matrix_report.json` with `failed=0` plus `triage_report.md` when failures need owner routing. |
| Final release gate | `python3 -m starcraft_commander.micromachine_release_gate` writes `release_gate.json` and `release_gate.md` with no automated blockers. |
| Neural/provider swap | Callers use `MicroMachineModulationBackend`, `publish_policy_modulation_provider_output(...)`, or `publish_neural_representation_modulation(...)`, so future neural representation providers publish the same bounded vector contract without raw SC2 controls. |
| CI/operations | Hosted CI runs unit contracts and script syntax; real SC2 soak matrices run from the self-hosted macOS workflow. |

Non-blocking risks after sign-off:

| Risk | Mitigation |
| --- | --- |
| AI Arena ladder strength is not automatically proven by local soak. | Run later ladder/evaluation batches using the same artifact report format. |
| The C++ hook remains a patch against a fixed MicroMachine commit. | Re-run patch apply and soak when upstream commit changes. |
| User intent quality depends on provider output. | Invalid or raw-control provider payloads are rejected before reaching MicroMachine. |
