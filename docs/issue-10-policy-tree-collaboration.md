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
| Provider compiler | Turns LLM/UI/replay/neural output into a bounded `PolicyModulationVector` or an explicit refusal/clarification. It maps flat aliases and representation axes into the same manager-level DSL. |
| Deep DSL | Represents strategy timing/transition, economy saturation/gas/repair, tech, production composition/add-ons/continuity, combat thresholds/target priorities, scouting cadence/scan/hidden-tech, squad allocation, emergency, confidence, TTL, constraints, and source. |
| Sidecar bridge | Serializes modulation updates, telemetry, rollback, failure envelopes, and stale-update rejection. |
| MicroMachine hooks | Read the consumed-key subset from the blackboard while MicroMachine retains unit tactics and build execution; emitted-only DSL axes require explicit C++ hook extension before they affect live play. |
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
| 10.16 | Adds a stdlib live text session and CLI that compile bounded provider output into MicroMachine blackboard updates and report telemetry consumption. |

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
`eb893161371dab975a0a7e600f9e250ac03ec1ef` and distinguishes currently
consumed keys from Python-emitted DSL axes that are not yet consumed by the
current C++ patch.

`starcraft_commander.micromachine_live_session` is the local live text sidecar.
It accepts user text, invokes a bounded provider adapter, writes only compiled
`PolicyModulationVector` output through `MicroMachineModulationBackend`, and
reports consumption from `MicroMachineTelemetry.active_modulation_ids`. It does
not read the SC2 screen, inject keyboard/mouse input, or call python-sc2 raw
runtime actions.

`starcraft_commander.micromachine_chat_modulation` is the safe in-game chat
boundary. It can route only sidecar/telemetry-supplied `chat_events` into the
same live text session, after user-message filtering, dedupe, and raw-control
key rejection. If patched MicroMachine telemetry does not expose chat events,
the result is `unsupported_no_chat_source`; OCR and global input hooks remain
forbidden.

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

The soak first publishes defensive hold and waits for real macro evidence. For
the default 12k-frame production gate, a pass proves defensive-hold modulation
consumption, macro progress, manager intervention, and no classifier failures.
Longer soaks publish aggressive pressure only after `SOAK_AGGRESSIVE_MIN_FRAME`
and then require telemetry consumption of the latest frame-suffixed aggressive
refresh before TTL expiry.

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
10. A map/race/difficulty soak matrix runner exists and production
    qualification requires `failed=0`.
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
| Map/race diversity matrix | `/private/tmp/voi-mm-soak-matrix/issue-10-13-acropolis-races-zero-v4/matrix_report.json` passed with `SOAK_MAX_ATTEMPTS=1`, `passed=3`, `failed=0`: `AcropolisLE.SC2Map` against `Zerg`, `Protoss`, and `Terran` difficulty 1. |
| Neural/SOTA adapter | `starcraft_commander.neural_representation` adds the concrete model-adapter seam. Model outputs are bounded `representation_axes` compiled by the existing DSL provider compiler before `MicroMachineModulationBackend` publish. |
| CI/operations | Hosted CI covers Python contracts and script syntax. `.github/workflows/micromachine-local-soak.yml` runs the same matrix script on a self-hosted macOS runner with local StarCraft II and patched MicroMachine. |
