# MicroMachine Adoption Plan

Issue #10 is not looking for another SC2 API wrapper. It is looking for the
strongest practical non-neural StarCraft II bot that can be studied and
modulated by human intent without discarding its existing autonomous strength.

## Decision

MicroMachine is the only practical adoption candidate found so far.

The selection criteria are intentionally strict:

1. The bot must have a credible strong-bot history.
2. The bot logic must be publicly inspectable.
3. The bot must expose policy-level seams that can accept intent modulation.
4. The integration must preserve the existing safety rule: models never issue
   raw SC2 API calls.

Under those constraints, MicroMachine is the only candidate that satisfies all
four. Deimos and Eris are stronger current AI Arena references, but their bot
zips are not publicly downloadable, so they cannot be used as the product core.
`python-sc2` is an API client, not a bot. CommandCenter is a useful historical
base and architecture reference, but it is not the strong-bot candidate.

## Candidate Matrix

| Candidate | Role | Adoption result |
| --- | --- | --- |
| MicroMachine | Public C++ Terran bot with historical SC2AI, AI Arena, and ProBots wins. | Adopt as the non-neural strong-bot reference and modulation target. |
| Deimos | Current high-ELO AI Arena Protoss bot built with `ares-sc2`. | Do not adopt directly; bot logic is not public. Use only as evidence that `ares-sc2` is a strong modern pattern. |
| Eris | Long-running high-ELO reactive Zerg bot. | Do not adopt directly; bot logic is not public. Use its reactive strategy description as design inspiration. |
| SharpenedEdge | Scripted Protoss bot using Sharpy/python-sc2. | Do not adopt directly; bot zip is not public. Use Sharpy concepts only as reference. |
| CommandCenter | C++ SC2/BW bot framework that MicroMachine originally diverged from. | Do not integrate as a product dependency. It is useful only for reading manager-style architecture. |
| python-sc2 / burnysc2 | Python API client used by many bots and this project. | Keep for the current live SC2 adapter, but do not describe it as a strong bot. |

## MicroMachine Surfaces

The modulation target should keep MicroMachine's normal play loop intact and
inject intent only through bounded policy-level surfaces:

| MicroMachine surface | Modulation domain |
| --- | --- |
| `StrategyManager` | Strategy and build selection bias. |
| `ProductionManager`, `BuildOrderQueue` | Production, tech, expansion, and build-order priority bias. |
| `CombatCommander`, `CombatAnalyzer` | Attack, hold, retreat, and combat-simulation threshold bias. |
| `Squad`, `SquadOrder`, `MicroManager` | Main-army, defense, harassment, and regroup intent. |
| `ScoutManager` | Scout target, risk tolerance, and information priority. |
| `WorkerManager` | Economy, repair, emergency pull, and defense-worker bias. |
| `libvoxelbot` combat simulation | Fight acceptance threshold and tactical risk modulation. |

## Non-Goals

- Do not replace MicroMachine's tactical code with direct LLM orders.
- Do not expose `python-sc2`, `s2client-api`, or BotAI method names in user or
  model outputs.
- Do not integrate CommandCenter as a separate product dependency.
- Do not claim Deimos or Eris can be adopted unless their bot logic becomes
  public and inspectable.

## Required Integration Shape

```text
Korean user order or future neural representation
  -> bounded provider output
  -> deep policy modulation DSL
  -> MicroMachine sidecar / blackboard
  -> MicroMachine manager hooks
  -> MicroMachine keeps playing, biased by human intent
```

The modulation layer must be provider-agnostic. A Korean LLM parser, a UI
control, replay imitation, or an AlphaStar-like representation model should all
emit the same policy modulation vector. The vector may bias or constrain
MicroMachine; it must not directly command SC2 units.

## Deep Modulation DSL

The first production contract for this layer lives in
`starcraft_commander/policy_modulation.py`. It is deliberately stdlib-only and
does not import MicroMachine, python-sc2, or StarCraft II runtime packages.

The top-level payload is `PolicyModulationVector`:

```text
PolicyModulationVector
  goal
  source: human | llm | ui | replay_imitation | neural_representation | system
  override_level: bias | constraint | directive | emergency
  confidence: 0.0..1.0
  ttl_seconds: 1..900
  strategy / economy / tech / production / combat / scouting / squad / emergency
  constraints
  tags
  rationale
```

The DSL is deep enough to express MicroMachine manager modulation without
becoming raw runtime control:

| DSL domain | Intended MicroMachine hook |
| --- | --- |
| `strategy` | `StrategyManager` posture, preferred builds, avoided builds, strategic tags. |
| `economy` | `WorkerManager` and economy-side production pressure. |
| `tech` | Structure, unit, upgrade, and tech-path bias. |
| `production` | `ProductionManager` and `BuildOrderQueue` bias. |
| `combat` | `CombatCommander`, `CombatAnalyzer`, and combat-sim thresholds. |
| `scouting` | `ScoutManager` target and risk modulation. |
| `squad` | `Squad`, `SquadOrder`, and `MicroManager` role allocation. |
| `emergency` | Short-lived cancel, retreat, hold, evacuation, or worker-pull flags. |

Raw-control keys such as `python_sc2`, `botai_method`, `raw_action`,
`s2client_api`, `unit_tag`, `attack_move`, `train_unit`, or `build_structure`
are rejected before a vector can be constructed. Emergency vectors are capped at
60 seconds even though normal modulation can last up to 900 seconds.

## Stop Condition

The issue #10 sub-plan is complete only when this repository has:

1. A validated deep modulation DSL.
2. A provider boundary for LLM and future neural representation outputs.
3. A MicroMachine sidecar and blackboard protocol.
4. Observability and evaluation contracts that compare baseline MicroMachine
   against modulated MicroMachine.
