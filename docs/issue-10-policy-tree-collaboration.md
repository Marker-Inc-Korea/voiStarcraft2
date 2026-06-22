# Issue 10 Policy Tree Collaboration Architecture

Issue #10 builds a production contract for injecting human intent into a strong
StarCraft II bot without turning an LLM into a per-frame unit controller.

## Capability Boundary

```text
User order / UI / replay / neural representation
  -> provider compiler
  -> PolicyModulationVector
  -> MicroMachine sidecar blackboard
  -> MicroMachine manager hooks
  -> MicroMachine keeps owning tactical execution
```

The abstraction level is policy modulation. The system may bias, constrain, or
temporarily direct MicroMachine managers, but it must not expose python-sc2,
s2client-api, unit tags, raw actions, build commands, or attack-move calls to a
provider output.

## Runtime Responsibilities

| Layer | Responsibility |
| --- | --- |
| Provider compiler | Turns LLM/UI/replay/neural output into a bounded `PolicyModulationVector` or an explicit refusal/clarification. |
| Deep DSL | Represents strategy, economy, tech, production, combat, scouting, squad, emergency, confidence, TTL, constraints, and source. |
| Sidecar bridge | Serializes modulation updates, telemetry, rollback, failure envelopes, and stale-update rejection. |
| MicroMachine hooks | Read manager-domain bias from the blackboard while MicroMachine retains unit tactics and build execution. |
| Observability | Exposes active/stale modulation state and evaluation metrics through JSON-ready snapshots. |

## Completed Sub-Issues

| Sub-issue | Outcome |
| --- | --- |
| #12 | Documented why MicroMachine is the practical public non-neural adoption target and why CommandCenter/python-sc2 are not bots. |
| #13 | Added the deep policy modulation DSL and raw-control rejection. |
| #14 | Added the provider compiler for LLM/UI/replay/neural payloads with refusal and clarification results. |
| #15 | Added MicroMachine sidecar/blackboard protocol contracts, manager hook mapping, TTL, rollback, and failure modes. |
| #16 | Adds dashboard observability and baseline-vs-modulated evaluation contracts. |
| #22 | Adds the concrete filesystem runtime bridge and MicroMachine C++ integration kit. |
| #26 | Adds long-run soak/sign-off gates for full-game MicroMachine collaboration. |
| 10.12 | Adds map/race/difficulty matrix operations, self-hosted soak CI, and concrete neural representation adapter attachment. |

## Evaluation Contract

Baseline MicroMachine and modulated MicroMachine must be compared with the same
maps, races, and matchup seeds where possible. Required metrics are:

| Metric | Purpose |
| --- | --- |
| `win_loss` | Detect whether modulation regresses MicroMachine's existing strength. |
| `crash_rate` | Ensure provider, sidecar, and bot errors do not destabilize games. |
| `intent_compliance` | Score whether play follows the user's stated strategic intent. |
| `intervention_latency_ms` | Measure how quickly a user/provider intervention reaches the blackboard. |

Safety gates remain mandatory: no raw SC2 API actions from provider output, no
bridge crash on invalid payload, and emergency rollback must remain available.

## Runtime Integration Kit

The runtime bridge is implemented in
`starcraft_commander/micromachine_runtime.py` and
`integrations/micromachine/`. Python callers should depend on
`MicroMachineModulationBackend` rather than the concrete filesystem class.
`MicroMachineFilesystemBlackboard` remains the local C++ transport, while
`MicroMachineInMemoryBlackboard` supports tests and future neural/model-loop
orchestration. Neural representation providers can publish bounded axes through
`publish_policy_modulation_provider_output(...)`, which compiles into the same
`PolicyModulationVector` contract before writing to any backend.

The Python sidecar writes:

| File | Purpose |
| --- | --- |
| `latest_modulation.json` | Canonical auditable blackboard update. |
| `latest_modulation.kv` | C++ stdlib-readable flat overlay for MicroMachine hooks. |
| `modulation_updates.jsonl` | Append-only modulation audit log. |
| `latest_telemetry.json` | Latest telemetry from the C++ bot. |
| `telemetry.jsonl` | Append-only telemetry audit log. |

The C++ kit includes `voi_policy_blackboard.hpp`, which can be copied into
MicroMachine and read from `GameCommander::onFrame(bool executeMacro)`. The
hook manifest is tied to upstream MicroMachine commit
`eb893161371dab975a0a7e600f9e250ac03ec1ef`.

## Production Soak Boundary

`starcraft_commander.micromachine_soak` is the host-side production classifier
for Issue 10.11. It consumes the patched MicroMachine blackboard directory and
classifies crash/early process exit, SC2 disconnect evidence, telemetry stall,
repeated placement failures, no-production deadlock, production stall, missing
manager intervention, and stale modulation. The local script
`integrations/micromachine/scripts/soak_macos_local.sh` wraps that classifier
around a real StarCraft II run and archives deterministic artifacts under
`SOAK_RUN_DIR`.

The soak keeps the same bounded control model as the rest of Issue #10:

```text
LLM / future neural representation provider
  -> PolicyModulationVector
  -> MicroMachineModulationBackend
  -> latest_modulation.kv
  -> MicroMachine manager bias hooks
  -> CombatCommander / ScoutManager / production code keep tactical authority
```

The soak first publishes defensive hold, waits for real macro evidence, then
publishes aggressive pressure and refreshes it before TTL expiry. A pass
requires `soak_report.json` with `ok: true`, the configured target frame,
macro evidence, `CombatCommander.bounded_intervention=true`,
`ScoutManager.bounded_intervention=true`, telemetry consumption of the latest
frame-suffixed modulation refresh, and no classifier failures.

`starcraft_commander.neural_representation` is the concrete neural/SOTA
attachment surface. A model adapter may infer representation axes, but it cannot
publish raw actions directly; its output is compiled by the same provider
compiler and published through the same `MicroMachineModulationBackend`.

## Stop Conditions

Issue #10 is complete when:

1. MicroMachine adoption rationale is documented.
2. The deep modulation DSL is implemented and validated.
3. Provider output compiles into the DSL without raw runtime control.
4. Sidecar/blackboard contracts cover TTL, rollback, telemetry, failure modes,
   manager hook mapping, and stale/invalid rejection.
5. Dashboard snapshots expose active modulation state without requiring SC2 or
   MicroMachine to be installed.
6. Evaluation contracts compare baseline MicroMachine vs modulated
   MicroMachine using win/loss, crash rate, intent compliance, and intervention
   latency.
7. A concrete filesystem sidecar runtime and MicroMachine C++ integration kit
   exist for local StarCraft II smoke testing.
8. A backend abstraction exists so future neural representation transports can
   be swapped in without changing the MicroMachine manager modulation contract.
9. A long-run soak reaches the configured frame budget with no crash,
   disconnect, telemetry stall, repeated placement failure, no-production
   deadlock, stale modulation, or missing manager-intervention evidence.
10. A map/race/difficulty soak matrix runner exists and preserves both pass
    cases and negative-control failures in an aggregate report.
11. CI covers pure-Python contracts on hosted runners, while a self-hosted
    macOS workflow runs real SC2 soak matrices from the same scripts.
12. Neural/SOTA model outputs have a concrete adapter path into bounded
    representation axes without bypassing the DSL compiler.

## Issue 10.11 Local Sign-Off Evidence

| Run | Result |
| --- | --- |
| `/private/tmp/voi-mm-soak/issue-10-11-final-acropolis-v3/soak_report.json` | Passed on `AcropolisLE.SC2Map` at frame 12056 with macro evidence, manager intervention, consumed aggressive modulation, and `target_frame_reached_cleanup`. |
| `/private/tmp/voi-mm-soak/issue-10-11-final-thunderbird-v2/soak_report.json` | Failed as expected on `Ladder2019Season3/ThunderbirdLE.SC2Map` with non-retryable `no_production_deadlock`, preventing false sign-off. |

## Issue 10.12 Production Completion Evidence

| Gate | Result |
| --- | --- |
| Map/race diversity matrix | `/private/tmp/voi-mm-soak-matrix/issue-10-12-diversity-v1/matrix_report.json` completed six real SC2 cases. `02-AcropolisLE-SC2Map-Protoss-d1` passed at frame 12042 with macro and manager-intervention evidence; five failed cases were preserved with explicit failure codes. |
| Neural/SOTA adapter | `starcraft_commander.neural_representation` adds the concrete model-adapter seam. Model outputs are bounded `representation_axes` compiled by the existing DSL provider compiler before `MicroMachineModulationBackend` publish. |
| CI/operations | Hosted CI covers Python contracts and script syntax. `.github/workflows/micromachine-local-soak.yml` runs the same matrix script on a self-hosted macOS runner with local StarCraft II and patched MicroMachine. |
