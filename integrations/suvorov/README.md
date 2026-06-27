# Suvorov Backend Evaluation Kit

This directory records the issue #73 evaluation of
`alkurbatov/suvorov-bot` as a possible open multi-race rule-based StarCraft II
backend for bounded intent modulation.

## Verdict

Suvorov is a conditional secondary-backend candidate, not a MicroMachine
replacement. In short: Suvorov is not a MicroMachine replacement.

It builds on the local macOS host and completed a real Acropolis game through
SC2 API join, bot macro, combat-unit production, attack-group creation, game
over, and replay save. Its public code also contains explicit Terran, Protoss,
and Zerg strategy branches. However, it does not currently provide the
blackboard, telemetry, deep tactical managers, combat simulation, or
same-environment strength evidence that the MicroMachine production path has.

## Reproducible Local Probe

The local probe used an isolated checkout outside the repo:

```bash
git clone --recursive https://github.com/alkurbatov/suvorov-bot /private/tmp/voi-suvorov-probe/suvorov-bot
cd /private/tmp/voi-suvorov-probe/suvorov-bot
cmake -S . -B build -DCMAKE_POLICY_VERSION_MINIMUM=3.5
cmake --build build --parallel
```

Recorded identity:

| Item | Value |
| --- | --- |
| Suvorov commit | `08a295d71f545d04b047a70ac4e1d7413afed2a4` |
| `contrib/cpp-sc2` | `96d15bab61ec58f58df98af33bfca9199f176cc0` |
| `contrib/cpp-sc2/protocol` | `db142363be5e4da522879b8b43db69c6313bcd57` |
| SC2 API target | `5.0.10` |
| Binary | `/private/tmp/voi-suvorov-probe/suvorov-bot/build/bin/Suvorov` |
| Binary format | `Mach-O 64-bit executable arm64` |

Direct launch against the installed Base97364 SC2 executable using the full
path with spaces failed once with `sc2::ClientConnectionError`. Re-running
through the existing no-space StarCraft II symlink used by the MicroMachine
runtime succeeded:

```bash
/opt/homebrew/bin/gtimeout --preserve-status 150s \
  /private/tmp/voi-suvorov-probe/suvorov-bot/build/bin/Suvorov \
  /private/tmp/voi-sc2-root/Maps/AcropolisLE.SC2Map \
  -e /private/tmp/voi-sc2-root/Versions/Base97364/SC2.app/Contents/MacOS/SC2 \
  -t 120000
```

Observed runtime stdout evidence:

- `WaitJoinGame finished successfully.`

Observed bot evidence in
`/private/tmp/voi-suvorov-probe/suvorov-bot/history.log`:

- `TERRAN_COMMANDCENTER` and starting `TERRAN_SCV` units were created.
- `Started building a SupplyDepot`, `Barracks`, `Refinery`, and
  `OrbitalCommand`.
- `TERRAN_MULE` was created.
- `strategy.marine_push: Schedule Marine training`.
- `strategy: TERRAN_MARINE added to attack group`.
- `dispatcher: Game over!` at frame `8548`.
- `plugin.diagnosis: Replay saved`.

The observed game was Terran versus CheatInsane Random Rush on Acropolis and
ended in a loss. This is useful viability evidence, not strength evidence.

## Architecture Boundary

Suvorov is not an API wrapper. It is a real C++ SC2 bot built on `cpp-sc2`.
The relevant public seams are:

| Surface | Current role | Modulation interpretation |
| --- | --- | --- |
| `Dispatcher::OnStep()` | Per-frame lifecycle for plugins and Builder. | Best central place to poll a bounded blackboard. |
| `Dispatcher::OnGameStart()` | Picks race-specific strategy plugin. | Strategy posture and opening bias, not raw unit control. |
| `Builder` | Obligatory and optional build/order queues. | Production and tech priority bias while Builder validates cost, food, and tech. |
| `Hub` | Race, workers, expansions, larva, geyser ownership. | Economy, expansion, gas, and worker policy bias. |
| `Miner` | Worker production, mineral/gas assignment, MULEs. | Economy saturation, gas priority, worker production, MULE priority. |
| `QuarterMaster` | Proactive supply scheduling. | Supply buffer and continuity bias. |
| `Strategy` | Attack group accumulation and attack threshold. | Aggression, hold, timing, commitment, and emergency retreat/cancel bias. |
| `WarpSmith` | Protoss chronoboost and warpgate support. | Protoss-specific chrono and warpgate bias. |

The important limitation is tactical depth. Suvorov's base `Strategy::OnStep()`
attacks when `m_units.size()` reaches `m_attack_limit`, then sends all combat
units to the first enemy start location. That makes modulation easy to hook but
less expressive than MicroMachine's `CombatCommander`, `CombatAnalyzer`,
`Squad`, and micro-manager surfaces.

## Race Support

The code has three explicit race branches:

| Race | Strategy path | Runtime status |
| --- | --- | --- |
| Terran | `src/strategies/terran/MarinePush.cpp` | Observed locally. |
| Protoss | `src/strategies/protoss/ChargelotPush.cpp` | Source-confirmed, not locally observed. |
| Zerg | `src/strategies/zerg/ZerglingFlood.cpp` | Source-confirmed, not locally observed. |

The non-ladder `main.cpp` currently uses `CreateParticipant(sc2::Race::Random,
&bot, "Suvorov")`. A production-grade race matrix therefore needs a small
race-selectable harness or ladder-mode runner before claiming per-race runtime
support on this host.

## Adapter Requirements

Suvorov can reuse the project-level bounded DSL contracts:

- `PolicyModulationVector`
- `PolicyModulationProviderInterface`
- raw-control rejection
- semantic tactical scope
- fail-closed bridge states

It still needs Suvorov-specific runtime work:

- `SuvorovModulationBackend` or a generic `StrongBotModulationBackend`.
- A stdlib C++ blackboard reader equivalent to the MicroMachine reader.
- `latest_telemetry.json` with `policy_active`, `active_modulation_ids`,
  `consumed_axes`, frame, race, macro evidence, and failure reasons.
- Hook consumption in `Dispatcher`, `Builder`, `Miner`, `Hub`, `QuarterMaster`,
  `Strategy`, and race-specific strategy plugins.
- A per-race smoke/soak matrix before it can become a supported backend.

## Safety Contract

Suvorov must follow the same safety boundary as MicroMachine. LLM, UI, replay,
or future neural providers may bias bot policy, but they must not emit unit
tags, direct attack commands, `python-sc2` calls, or `s2client-api` method
names.

- Providers must not emit raw SC2 actions.
- Suvorov managers remain authoritative over real game actions.
