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
3. In `GameCommander::onFrame(bool executeMacro)`, call:

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

## Local Smoke Test Boundary

This repository can test the Python sidecar, file transport, key flattening,
and manifest consistency without StarCraft II. A real game smoke test still
requires a local StarCraft II installation, MicroMachine build dependencies,
and a patched MicroMachine checkout.
