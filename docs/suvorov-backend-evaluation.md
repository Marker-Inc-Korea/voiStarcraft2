# Suvorov Backend Evaluation

Issue #73 evaluated `alkurbatov/suvorov-bot` as an open multi-race
rule-based StarCraft II bot candidate for the voiStarcraft2 policy modulation
architecture.

## Capability Boundary

Suvorov is a real C++ bot, not merely a StarCraft II API wrapper. It owns
workers, build queues, race-specific strategies, supply, gas, MULEs,
chronoboost, warpgate support, and attack-group execution through `cpp-sc2`.

It is not currently a production replacement for MicroMachine. The current
public code has no voi blackboard reader, no bounded DSL consumption telemetry,
no manager-level tactical observability, and no same-environment evidence that
it is stronger than the patched MicroMachine path.

```text
User / LLM / UI / future neural representation
  -> PolicyModulationVector
  -> future SuvorovModulationBackend
  -> Suvorov blackboard reader
  -> Dispatcher / Builder / Miner / Hub / Strategy hooks
  -> Suvorov keeps owning SC2 actions
  -> telemetry proves consumed axes or fail-closed refusal
```

## Evidence Summary

| Gate | Result | Evidence |
| --- | --- | --- |
| Source identity | Passed | Suvorov commit `08a295d71f545d04b047a70ac4e1d7413afed2a4`. |
| Submodules | Passed | `contrib/cpp-sc2` at `96d15bab61ec58f58df98af33bfca9199f176cc0`; protocol at `db142363be5e4da522879b8b43db69c6313bcd57`. |
| Configure | Passed with compatibility flag | `-DCMAKE_POLICY_VERSION_MINIMUM=3.5` required for current CMake. |
| Build | Passed | `build/bin/Suvorov`, Mach-O arm64, target SC2 API `5.0.10`. |
| Direct SC2 launch | Mixed | Full path with spaces failed with `ClientConnectionError`; no-space alias `/private/tmp/voi-sc2-root` connected. |
| Runtime | Passed for one full game | Acropolis reached `Game over!` and `Replay saved` at frame `8548`. |
| Runtime race coverage | Partial | Terran observed. Protoss/Zerg are source-confirmed but not locally smoke-confirmed because non-ladder main hardcodes `Race::Random`. |
| Strength claim | Not proven | Observed Terran run lost to CheatInsane Random Rush. |
| Modulation readiness | Not production-ready | Hook seams are clear, but blackboard and telemetry are absent. |

## What Actually Ran

The successful local runtime command was:

```bash
/opt/homebrew/bin/gtimeout --preserve-status 150s \
  /private/tmp/voi-suvorov-probe/suvorov-bot/build/bin/Suvorov \
  /private/tmp/voi-sc2-root/Maps/AcropolisLE.SC2Map \
  -e /private/tmp/voi-sc2-root/Versions/Base97364/SC2.app/Contents/MacOS/SC2 \
  -t 120000
```

Relevant runtime stdout evidence:

- `WaitJoinGame finished successfully.`

Relevant `history.log` evidence:

- Starting Terran command center and SCVs were observed.
- Supply depot, barracks, refinery, orbital command, marine, MULE, and an
  expansion were attempted or created.
- `strategy.marine_push: Schedule Marine training`.
- `strategy: TERRAN_MARINE added to attack group`.
- `dispatcher: Game over!`.
- `plugin.diagnosis: Replay saved`.

The game ended at frame `8548` after the bot lost its base to CheatInsane Rush.
This proves a working full-game loop on the host, but it does not prove a
strong baseline.

## Hook Mapping

| DSL domain | Suvorov seam | Why it matters |
| --- | --- | --- |
| `strategy` | `Dispatcher::OnGameStart()` | Race-specific plugin selection can be biased by posture/opening preferences. |
| `production` | `Builder::OnStep()` and scheduling methods | Order queue priority can be modulated while keeping cost, tech, food, and placement checks inside Suvorov. |
| `economy` | `Miner::OnStep()` | Worker production, gas, minerals, and MULE priority can consume economy bias. |
| `economy` | `Hub::AssignBuildTask()` and `AssignVespeneHarvester()` | Worker assignment and expansion/gas policy can be bounded. |
| `supply` | `QuarterMaster::OnStep()` | Supply-buffer and production-continuity bias can prevent deadlocks. |
| `combat` | `Strategy::OnStep()` | Aggression and attack timing can alter `m_attack_limit` and attack/hold behavior. |
| `combat_scope` | `Strategy::OnUnitCreated()` | Unit-class or army-group scope can filter future attack groups. |
| `protoss_macro` | `WarpSmith::OnStep()` | Chronoboost and warpgate bias can be exposed for Protoss. |

## Critical Comparison Against MicroMachine

| Criterion | MicroMachine | Suvorov |
| --- | --- | --- |
| Public source | Yes | Yes |
| Local build evidence | Yes | Yes |
| Local full-game evidence | Yes | Yes, one Terran random run |
| Multi-race source support | No, Terran-focused | Yes, source-confirmed |
| Deep tactical manager surfaces | Stronger: `CombatCommander`, `CombatAnalyzer`, `Squad`, micro managers | Weaker: base attack-limit strategy |
| Bounded DSL bridge | Implemented | Not implemented |
| Consumption telemetry | Implemented | Not implemented |
| Production default suitability | Current default | Not yet |

The result is therefore not "Suvorov is bad" and not "Suvorov replaces
MicroMachine." The result is narrower: Suvorov is worth keeping as a
multi-race reference/prototype candidate, but only after a dedicated backend
adapter proves per-race runtime, blackboard consumption, telemetry, and
comparative stability.

## Decision

Keep MicroMachine as the production default.

Open a follow-up implementation issue only for a bounded Suvorov prototype:

1. Add race-selectable local harness.
2. Add a Suvorov filesystem blackboard reader.
3. Emit telemetry equivalent to the MicroMachine dashboard contract.
4. Hook a minimal consumed set: combat aggression/defense, production priority,
   economy gas/worker bias, and supply buffer.
5. Run Terran, Protoss, and Zerg smoke before any backend promotion.

Until those gates pass, Suvorov should be treated as a conditional secondary
backend candidate and architecture reference.
