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
becoming raw runtime control. The current patched C++ MicroMachine hook consumes
the stable subset listed in `integrations/micromachine/HOOK_MANIFEST.json`;
newer axes are emitted to the blackboard and remain fail-closed until a manager
hook explicitly reads them.

| DSL domain | Manager-level policy surface |
| --- | --- |
| `strategy` | `StrategyManager` posture, preferred/avoided builds, timing-window bias, transition bias, strategic tags. |
| `economy` | `WorkerManager` and economy-side production pressure: expansion, worker production, gas workers, mineral saturation, repair, supply buffer, MULE priority, and expansion safety. |
| `tech` | Structure, unit, upgrade, and tech-path bias. |
| `production` | `ProductionManager` and `BuildOrderQueue` queue, composition, add-on, facility, continuity, and tech-switch urgency bias. |
| `combat` | `CombatCommander`, `CombatAnalyzer`, and combat-sim thresholds: aggression, engage/retreat deltas, timing, harassment, defense, army preservation, siege positioning, kiting, flanking, and target-priority bias. |
| `scouting` | `ScoutManager` scout priority, cadence, scan priority, hidden-tech scouting, target, and risk modulation. |
| `squad` | `Squad`, `SquadOrder`, and `MicroManager` main-army, defense, harassment, regroup, drop, split, reinforce, contain, and role-allocation bias. |
| `emergency` | Short-lived cancel, retreat, hold, evacuation, repair-priority, expansion-stop, or worker-pull flags. |

The manifest also lists `python_blackboard_emitted_but_not_consumed_by_current_cpp_patch`
for axes that are contract-ready on the Python/provider side but still need a
specific C++ manager hook before they can affect live MicroMachine play.

Raw-control keys such as `python_sc2`, `botai_method`, `raw_action`,
`s2client_api`, `unit_tag`, `attack_move`, `train_unit`, or `build_structure`
are rejected before a vector can be constructed. Emergency vectors are capped at
60 seconds even though normal modulation can last up to 900 seconds.

## Provider Compiler Boundary

Issue 10.3 adds `starcraft_commander/policy_modulation_provider.py` as the
single deterministic compiler between external intent providers and the deep
DSL. Supported provider roles are LLM, human UI, replay imitation, and future
neural representation providers. They may produce bounded semantic mappings,
but they do not get to call MicroMachine, python-sc2, s2client-api, or any raw
unit command surface.

The compiler accepts two practical shapes:

```text
LLM/UI semantic payload
  intent or goal
  posture / economy / tech / combat / scouting / squad / emergency fields
  confidence, ttl, override level, constraints, tags

Neural representation payload
  goal
  representation axes such as economy.expand_bias or tech.unit_biases.SiegeTank
  confidence, ttl, override level
```

Both shapes converge to the same `PolicyModulationVector`. Malformed, unsafe,
or raw-control-bearing outputs become explicit `refused` results. Ambiguous
provider outputs become `clarification_required` results. The runtime caller can
therefore explain the issue to the user without crashing and without handing
unsafe data to a bot bridge.

## Sidecar And Blackboard Protocol

Issue 10.4 adds `starcraft_commander/micromachine_bridge.py` as the production
contract for a MicroMachine sidecar. The contract is still stdlib-only: it
specifies JSON-ready telemetry, modulation updates, rollback commands, and
error envelopes without linking this Python project to the C++ bot.

The blackboard update semantics are:

```text
MicroMachineBlackboardUpdate
  protocol_version: voi-mm-bridge/v1
  update_id
  issued_at_frame
  expires_at_frame = issued_at_frame + ttl_seconds * 22
  vector: PolicyModulationVector
  active_constraints
  manager_bias_domains
  rollback_update_id
```

MicroMachine remains authoritative over real unit actions. The sidecar writes
only bias/constraint/directive/emergency modulation into a blackboard that
manager hooks can read. Stale updates are rejected once `current_frame` exceeds
`expires_at_frame`. Invalid payloads, provider unavailability, bridge
disconnection, stale modulation, and emergency rollback have explicit failure
modes so the GUI/logs can surface what happened without crashing.

Required MicroMachine hook mapping is fixed in `MICROMACHINE_MANAGER_HOOKS`:

| DSL domain | MicroMachine hook |
| --- | --- |
| `strategy` | `StrategyManager` build/posture selection bias. |
| `production` | `ProductionManager` and `BuildOrderQueue` queue/tech deviation bias. |
| `combat` | `CombatCommander` attack/hold/retreat posture bias. |
| `combat` | `CombatAnalyzer` fight acceptance threshold bias. |
| `squad` | `Squad` and `SquadOrder` role allocation and regroup bias. |
| `scouting` | `ScoutManager` target and risk bias. |
| `economy` | `WorkerManager` expansion, repair, and emergency worker bias. |
| `combat` | `libvoxelbot` combat simulation threshold bias. |

## Observability And Evaluation

Issue 10.5 adds `starcraft_commander/policy_observability.py`. It exposes a
JSON-ready dashboard snapshot that separates active modulation updates from
stale updates and can be attached to `CommanderPolicyTree.to_dict()` without
requiring StarCraft II or MicroMachine to be installed.

The evaluation contract compares baseline MicroMachine against MicroMachine
with voi policy modulation. Required metrics are win/loss, crash rate, intent
compliance, and intervention latency. See
[issue-10-policy-tree-collaboration.md](issue-10-policy-tree-collaboration.md)
for the complete architecture and stop conditions.

## Runtime Backend Abstraction

The production bridge is expressed through
`MicroMachineModulationBackend` in
`starcraft_commander/micromachine_runtime.py`. Callers publish validated
`PolicyModulationVector` updates, ingest telemetry, record provider failures,
and build dashboard snapshots through this backend contract rather than
depending on one transport.

The concrete local MicroMachine backend is still
`MicroMachineFilesystemBlackboard`. It writes validated modulation updates
atomically as canonical JSON plus a flattened `latest_modulation.kv` file that
MicroMachine can read with the C++ standard library. Tests and future
model-loop orchestration can use `MicroMachineInMemoryBlackboard` with the same
contract.

Future neural or SOTA representation providers should emit bounded
representation axes into `publish_policy_modulation_provider_output(...)`.
That helper compiles provider output through the same raw-control rejection
path as LLM/UI/replay providers, then publishes through any backend.

For live text commands while patched MicroMachine is running, use
`MicroMachineLiveTextSession` or the module CLI. The session reads the latest
telemetry frame when available, builds a `PolicyModulationProviderRequest`,
compiles provider output through the same DSL safety gate, publishes through
`MicroMachineModulationBackend`, and reports whether telemetry shows the update
id in `active_modulation_ids`.

```bash
python -m starcraft_commander.micromachine_live_session \
  --blackboard-dir /private/tmp/voi-mm-live \
  --command "탱크 중심으로 안전하게 버텨" \
  --provider-output-json '{"goal":"탱크 중심으로 안전하게 버텨","override_level":"constraint","combat":{"defend_bias":0.7,"aggression":-0.2}}' \
  --update-id live-hold-001 \
  --pretty
```

If `--provider-output-json` or `--provider-output-file` is omitted, the CLI uses
a deterministic keyword provider for smoke testing. Production LLM adapters
should implement `PolicyModulationProviderInterface` and return bounded
semantic JSON; they still cannot publish raw SC2 actions directly.

The C++ integration kit lives in `integrations/micromachine/`:

- `HOOK_MANIFEST.json` names the verified upstream MicroMachine commit and the
  real manager hook functions, including the current consumed-key subset and
  the emitted-but-not-yet-consumed DSL axes.
- `voi_policy_blackboard.hpp` is a header-only reader for keys such as
  `combat.defend_bias`, `economy.expand_bias`, and
  `emergency.force_retreat`.
- `README.md` documents the exact `GameCommander::onFrame` polling point and
  local smoke-test boundary.

## Stop Condition

The issue #10 sub-plan is complete only when this repository has:

1. A validated deep modulation DSL.
2. A provider boundary for LLM and future neural representation outputs.
3. A MicroMachine sidecar and blackboard protocol.
4. Observability and evaluation contracts that compare baseline MicroMachine
   against modulated MicroMachine.
5. A backend abstraction, concrete filesystem bridge, in-memory backend, and
   MicroMachine C++ integration kit.
