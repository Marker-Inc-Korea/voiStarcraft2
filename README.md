# voiStarcraft2

**말하면 스타가 움직인다.**

voiStarcraft2 is a human-in-the-loop StarCraft II battlefield commander. Korean
or English text and voice instructions are compiled into bounded semantic
operations that MicroMachine executes through the official SC2 API.

The product goal is not "chat beside a bot." The user must be able to divide
forces, issue simultaneous missions, reinforce or cancel one mission, and see
truthful evidence that the selected units received and carried out those
orders.

## Capability Boundary

```text
User intent
   |
   v
LLM semantic compiler          Called once per utterance, not once per frame
   |
   v
Validated Macro / Operation / Micro / Emergency DSL
   |
   v
MicroMachine managers          Own production, assignment, safety, and micro
   |
   v
SC2 API command submission     Move, attack, build, train, and valid abilities
   |
   v
Observed game effect           Movement, engagement, cast, completion, failure
```

voiStarcraft2 is **bounded HITL**, not a frame-by-frame RTS remote control.
The user specifies outcomes, force composition, roles, routes, targets,
abilities, building placement intent, and persistence. MicroMachine still
checks unit availability, prerequisites, pathing, threats, ability legality,
and concrete SC2 targets before issuing commands.

| Boundary | Supported |
| --- | --- |
| "마린 2기는 정찰, 4기는 우회 공격" | Yes. Independent operation IDs, squads, unit ownership, targets, and evidence. |
| "탱크를 생산하고 공성 모드로 압박" | Yes. Production prerequisites plus bounded operation and ability policy. |
| "바이킹은 정찰, 지상군은 공격" | Yes. Different unit compositions can run concurrently. |
| "적 본진에 핵을 사용" | Yes, when Ghost, payload, vision, range, and safety gates are satisfied. |
| Raw unit tags, arbitrary coordinates, mouse clicks | No. These bypass the semantic and safety boundary. |
| Guaranteed success regardless of resources or game state | No. Blocked reasons and missing prerequisites remain visible. |
| Human multiplayer qualification | Not yet. It is an explicit follow-up milestone. |

## Command Model

| Layer | Lifetime and responsibility |
| --- | --- |
| `Macro` | Economy, supply, production, tech, expansion, and standing composition. Usually persists until completed, superseded, or cancelled. |
| `Operation` | Independent scout, attack, defense, contain, harass, or regroup missions. Multiple operation IDs may coexist. |
| `Micro` | Explicit bounded ability or mode request such as siege, cloak, burrow, stim, unload, or tactical nuke. |
| `Emergency` | Retreat, hold, cancel attacks, worker evacuation, or repair priority. Explicitly preempts affected lower layers. |

Each operation carries its own:

```text
operation_id
goal
task type
unit composition and count
unit roles
route intent
target intent
lifetime and completion conditions
```

The live reducer uses `operation_id` as an upsert key. A follow-up with the same
ID reinforces or redirects that operation. A different ID remains parallel.
An emergency is never silently treated as another ordinary operation.

## Parallel Operations

Representative command:

```text
마린 2기는 적 본진을 정찰하고,
마린 4기는 오른쪽 길로 적 멀티를 공격해.
```

Runtime model:

```text
operations[]
   |
   +-> recon-alpha   -> VoiOp:recon-alpha   -> exclusive unit tags -> Scout order
   |
   +-> assault-bravo -> VoiOp:assault-bravo -> exclusive unit tags -> Attack order
```

One authoritative ownership map prevents a unit from belonging to two active
operations. Autonomous `MainAttack`, `Scout`, defense, and support logic may not
steal operation-owned units. Updating one operation increments its generation
without deleting unrelated operations.

## Terran Unit Coverage

The operation model is not Marine-specific. It reuses MicroMachine's existing
Squad, RangedManager, MeleeManager, detector, transport, siege, cloak, and
ability code paths for 15 canonical Terran families:

| Family | Units and behavior |
| --- | --- |
| Bio | Marine, Marauder, Reaper, Ghost: focus fire, kite, stim/ability policy, scouting, assault, defense. |
| Factory | Hellion/Hellbat, Widow Mine, Cyclone, Siege Tank, Thor: morph or deploy states, range control, siege support, target priority. |
| Support air | Medivac, Raven: healing, transport/unload, detection, and supported utility abilities under squad ownership. |
| Combat air | Viking, Banshee, Liberator, Battlecruiser: air or ground mode policy, cloak, siege/deploy, kiting, target selection, and operation following. |

Production requests include deterministic prerequisite lowering. For example,
requesting Tanks may add Factory, Refinery, Tech Lab, supply, and resource
priorities before the operation waits for eligible Tanks. This lowering is
aggregated across every active operation rather than reading only a legacy
top-level task, so a Marine scout can run while an independent Tank, Viking,
Ghost, or capital-ship operation builds its own prerequisite lane. Building
placement uses semantic anchors and SC2 placement/pathing queries, not random
coordinates.

This is a runtime support statement, not a claim that every family has passed
the same live qualification matrix. Marine/Tank operation paths and selected
support paths have direct live evidence. Ghost, Widow Mine, Raven, Liberator,
Banshee, Viking, Thor, and Battlecruiser still require family-by-family
`production -> exclusive assignment -> SC2 action -> observed effect -> HUD`
qualification before the project can claim comprehensive all-Terran live
coverage.

## Truthful Execution Evidence

The UI and telemetry distinguish transport from execution:

| Stage | Meaning |
| --- | --- |
| `parsed` | The utterance became valid semantic DSL. |
| `published` | The update was written to the blackboard. No gameplay success is claimed. |
| `consumed_by_manager` | Patched MicroMachine read the matching update and operation ID. |
| `queued_or_assigned` | An exclusive eligible unit set was assigned. |
| `order_issued` | A concrete Squad order was created. |
| `action_issued` | The SC2 API command path accepted an action. |
| `effect_observed` | An operation-level effect such as movement, engagement, target arrival, or completion was observed. |

`published` is never rendered as "executing." Operation telemetry is keyed by
`update_id + operation_id + generation`, uses monotonic frames, detects
duplicate ownership, and records the first blocking manager and reason.

An operation-level effect and a family ability effect are different evidence
classes. A Tank operation moving away from home can satisfy the operation
travel requirement, but it does not prove `siege_mode`; Banshee movement does
not prove cloak; Widow Mine movement does not prove burrow; and a caster moving
does not prove a spell. A family ability effect requires its requested
action-specific runtime observation, effect kind, count, and frame.

Each family row carries attempted, submitted, effect, and blocker evidence
under the identity
`update_id + operation_id + generation + family + action + attempt_generation`.
Lower operation generations, mismatched update/operation identities, and older
attempts for the same family/action are stale and cannot replace newer
evidence. Mixed-family partial success remains explicit: one family may show an
observed effect while another shows its blocking manager and reason.

## Operator UX

`/`, `/index.html`, and `/companion` all serve the same single compact
controller. It shows runtime connection state, one current command and
composition summary, short status captions, text/voice input, and emergency
retreat. The removed dashboard, operation-card board, four lanes, four-stage
rail, LLM settings panel, strategy briefing panel, and Tactical Radio/TTS UI
are not part of the current product surface.
Historical verifier text may still use the exact label `existing Operation card`
and the exact phrase four-stage `해석 → 배정 → 제출 → 관측` rail. Those labels
describe a removed surface, not the compact controller.

The installed macOS app owns native runtime bootstrap. It can provide the
fresh SC2 launch receipt and start MicroMachine automatically without opening a
second controller window. The same page in an ordinary browser has no native
launch bridge: it may queue and publish commands, and it may observe a runtime
started elsewhere, but it does not auto-start StarCraft II.

The compact controller keeps transport and gameplay truth separate.
`queued`, compiled, or `published` means that the command entered or reached
the blackboard path. Assignment and submission are intermediate runtime
evidence, not gameplay success by themselves. Actual success requires current
identity-matched MicroMachine telemetry plus the command-specific observed
effect, such as production, movement, engagement, target arrival, completion,
or `effect_observed`.

The patched in-game HUD mirrors the same operation identity and evidence inside
StarCraft II so the operator can confirm commands without leaving the game:

```text
[recon-alpha]  2 Marine  enemy_main     assigned -> moving
[assault-bravo] 4 Marine flank_right    action issued -> engaged
```

The compact controller remains the command surface. The in-game HUD is evidence and
situational feedback, not a hidden mouse or keyboard automation layer.

## Status

| Area | Status |
| --- | --- |
| Dry-run SC2 pipeline | Implemented and tested. Runs without StarCraft II. |
| Legacy live SC2 commander | Implemented and locally connected through python-sc2. This is compatibility mode, not MicroMachine. |
| MicroMachine compact controller | The only web/app text and voice route. Uses forced-tool LLM output, deterministic validation, and fail-closed publishing. |
| Parallel operations | Implemented through explicit `operations[]`, stable IDs, immutable per-generation deadlines, runtime-authoritative lifecycle, live upsert semantics, dynamic operation squads, and exclusive unit ownership. |
| Terran operation runtime support | Implemented for supported Terran combat families by reusing existing MicroMachine Squad and unit ability code paths. |
| Comprehensive all-Terran live qualification | Pending family-by-family evidence beyond the currently qualified Marine/Tank and selected support paths. |
| Web operation UX | One compact current-command surface. There is no operation-card board, four-lane view, LLM settings panel, or strategy briefing panel. |
| In-game HUD | Patched MicroMachine overlay for operation identity, force, route, target, assignment, action, movement, engagement, and blockers. |
| Voice input | The compact controller uses browser speech recognition when available and submits final text through the same bounded command gateway. CLI microphone transcription remains behind optional `[voice]` dependencies. |
| Tactical captions | The compact controller retains short text status captions. The former Tactical Radio priority TTS/readback UI is not shipped; its identity fixtures remain pre-live test evidence only. |
| LLM command interpreter | Required for legacy python-sc2 live commands and production MicroMachine free-form text modulation. OpenAI/GPT is the default; Anthropic is still supported. |
| Web GUI | One localhost-first compact controller instance with token-protected network mode and no duplicate browser/cockpit window. The app may hide it while SC2 is frontmost. The legacy dashboard is removed. |
| Event memory | Implemented and used by state reports and GUI history. |
| Standing orders | Implemented for continuous SCV production and supply-block prevention. |
| Brood War / BWAPI | Semantic executor boundary implemented; real BWAPI adapter still requires a BWAPI machine. |
| Human multiplayer | Deferred. Current qualification target is local AI/custom-game operation control. |

## Release Status And Qualification

Release readiness is generated, not maintained as prose. The authoritative
status contract is
`integrations/micromachine/PRE_LIVE_RELEASE_STATUS.json`, and
`.github/workflows/final-pre-live.yml` binds every automated result to one
repository SHA, one admitted MicroMachine build identity, one workflow run,
and one run attempt.

The bootstrap PR #168 introduced the workflow, closed #141, and established the
release identity. Its first exact-main run exposed hosted-runner portability
failures before readiness could be generated. The one-shot issue #169
qualification merge preserves PR #168 as the release-closing identity, proves
that its merge is an ancestor, reruns all five child gates on the exact repaired
`main` SHA, and produces `ready_for_live_qa.json` and
`ready_for_live_qa.md`. Ordinary PRs and later pushes are not applicable.
These generated reports, not manually edited test counts, browser versions,
hashes, run IDs, or dated claims in this README, are the release source of
truth.

The final manual procedure is generated from the same structured status and
the exact fourteen-journey manifest in
`docs/micromachine-final-live-qa.md`. Automated readiness always preserves
`manual_live_qa_remaining=true`; actual StarCraft II visual, movement,
engagement, compact-controller/HUD consistency, and telemetry identity
observations remain required.
Human multiplayer, ladder, Battle.net qualification, and competitive balance
signoff remain deferred.

Local regression command:

```bash
python3 -m pytest -q
```

The ordinary unit suite does not require StarCraft II, `burnysc2`, BWAPI, LLM
credentials, or audio hardware. Passing it alone is not a release verdict.

## License

voiStarcraft2 is dual-licensed as `AGPL-3.0-or-later OR commercial`.
Commercial closed-source use requires a paid commercial license from the
copyright holder. If you do not obtain a commercial license, you must comply
with the AGPL source-code disclosure obligations for the covered work. The
project notice is in `LICENSE`; the complete AGPL text is included in
`LICENSES/AGPL-3.0-or-later.txt`. MicroMachine and Blizzard s2client-api retain
their upstream MIT notices in `THIRD_PARTY_NOTICES.md`; commercial licensing
does not remove those attribution and notice obligations.

## Quickstart

Run the full commander pipeline against a scripted fake BotAI:

```bash
export OPENAI_API_KEY=...
python3 -m starcraft_commander.demo_sc2 --dry-run --script "마린 6기 입구로 보내고 SCV 계속 찍어" "상황 보고해줘"
```

Expected output:

```text
StarCraft II Commander 데모 (dry-run)
가짜 BotAI 상태로 실제 파이프라인을 실행합니다: 해석 -> 검증 -> 계획 -> 실행 -> 내레이션.

명령> 마린 6기 입구로 보내고 SCV 계속 찍어
명령: 마린 6기 입구로 보내
Intent DSL:
  {
    "intent": "DEFEND",
    "priority": "high",
    "constraints": [
      "hold ramp against early pressure"
    ],
    "location": "main ramp",
    "unit_group": "6 Marines"
  }
[executed] 명령을 실행했습니다. 마린 6기 그룹이 본진 입구로 공격 이동.
명령: SCV 계속 찍어
Intent DSL:
  {
    "intent": "TRAIN_WORKER",
    "priority": "normal",
    "constraints": [
      "keep SCV production continuous"
    ],
    "count": 1
  }
[executed] 명령을 실행했습니다. SCV 1기 생산 명령. 상비 명령 등록: 지속 SCV 생산.

명령> 상황 보고해줘
명령: 상황 보고해줘
Intent DSL:
  {
    "intent": "SUMMARIZE_STATE",
    "priority": "normal",
    "constraints": [
      "summarize current ToyCraft state"
    ]
  }
[read_only] 전장 상태를 확인했습니다. 미네랄 400, 가스 0. 보급 20/21 (여유 1). 일꾼 12기 (유휴 2기). 병력: 마린 6기. 건물: 완성 사령부 1동. 발견된 적 없음.
상비 명령: 지속 SCV 생산 활성
최근 명령 2건:
- #1 [executed] 명령을 실행했습니다. 마린 6기 그룹이 본진 입구로 공격 이동.
- #2 [executed] 명령을 실행했습니다. SCV 1기 생산 명령. 상비 명령 등록: 지속 SCV 생산.
```

Interactive dry-run:

```bash
export OPENAI_API_KEY=...
python3 -m starcraft_commander.demo_sc2 --dry-run
```

## Installation

Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate

pip install -e .              # core: dry-run, interpreter, validators, planners
pip install -e '.[sc2]'       # live SC2 mode via burnysc2
pip install -e '.[voice]'     # Korean push-to-talk via faster-whisper + sounddevice
pip install -e '.[llm]'       # required LLM interpreter for live play
pip install -e '.[dev]'       # pytest + wheel/sdist build verification
```

Live SC2 also requires a local StarCraft II installation and maps. See
[docs/sc2-smoke-test.md](docs/sc2-smoke-test.md).

## Run Modes

### Dry-Run

No StarCraft II required. Default dry-run uses the same LLM-mandatory
interpretation path as live play when a provider API key is available.
`--no-llm` is deprecated and should be used only for offline regression tests
that intentionally exercise the legacy deterministic compatibility layer:

```bash
python3 -m starcraft_commander.demo_sc2 --dry-run --no-llm
python3 -m starcraft_commander.demo_sc2 --dry-run --no-llm --script "SCV 계속 찍어" "상황 보고"
```

### Web GUI

Starts the compact controller with text/voice command input, current command
state, short status captions, runtime status, and emergency retreat:

```text
text / voice
  -> bounded MicroMachine DSL compiler
  -> MicroMachine blackboard directory
  -> patched MicroMachine C++ managers
  -> telemetry summarized in the compact current-command surface
```

This default path does not call python-sc2 and does not emulate the SC2 screen,
keyboard, or mouse. The web cockpit publishes only validated configured-LLM
forced-tool output. Missing or invalid tool-call/JSON output fails closed and is
not replaced by a rule-derived tactical command. CLI keyword publishing remains
explicit smoke/test-only and is labeled `source=smoke_keyword`, never
`source=llm`.

Standalone local UI:

```bash
python3 -m starcraft_commander.web_gui --dry-run
python3 -m starcraft_commander.web_gui --dry-run --port 0
```

These commands print a localhost URL but do not open another browser window.
An ordinary browser can queue commands and observe a runtime started elsewhere,
but it cannot produce the native SC2 launch receipt and therefore does not
auto-start StarCraft II. Use the installed **voiStarcraft2** app for native
runtime bootstrap and the enforced single-controller-window UI.

MyProxy one-click setup on macOS:

```bash
.venv/bin/python scripts/start_local_cockpit.py \
  --store-key-from-env --install-app --launch-app --prepare-only
```

This one setup command stores only the current MyProxy API key outside the
repository in owner-only local cockpit storage (`0700` directory, `0600`
file), reads the endpoint and model from `~/.codex/config.toml`, installs the
declared LLM dependencies, and builds patched MicroMachine with
`BUILD_JOBS=2` when a passing local build is absent, installs the app, and
opens it. It never writes the key, private endpoint, or private model into this
repository. Later launches only require double-clicking **voiStarcraft2** in
`~/Applications`; preparation is idempotent and the installed app displays the
controller after the localhost service is healthy.
The installed app is the only controller path that can obtain native launch
proof and auto-start StarCraft II/MicroMachine. Its start action and first
disconnected command use that bridge. The ordinary-browser page only queues the
command when no runtime is attached.
Every controller route renders the same compact page and never creates a second
controller window. The controller keeps only runtime truth, one current
operation/composition summary, short status captions, text/voice command input,
and emergency retreat. The previous full dashboard is no longer shipped.

Text input and the voice button are the unified command surface. Browser speech
recognition, when available, places final text into the same queueing path.
The status-caption panel is text-only; the former Tactical Radio TTS scheduler,
mute control, waveform, and multi-operation readback UI are not shipped.
Captions never promote `published` to gameplay success. Movement or engagement
is shown only after matching-generation runtime evidence.
The installed app's native start path calls `POST /api/runtime/start` with a
fresh launch receipt and launches
`integrations/micromachine/scripts/smoke_macos_local.sh` with the current
blackboard directory. An ordinary browser cannot invoke this native bootstrap
path and remains queue-only when the runtime is disconnected.
Each UI launch starts a fresh tactical command session, so a detached prior
game's hold, production, or attack command cannot leak into the new game.

CLI QA can keep that same MicroMachine runtime alive after the manual live
preflight verifies worker guard telemetry:

```bash
integrations/micromachine/scripts/smoke_macos_local.sh \
  --live-hold \
  --blackboard-dir /private/tmp/voi-mm-live \
  --max-attempts 1
```

Then publish a live text intervention into the same blackboard:

```bash
python3 -m starcraft_commander.micromachine_live_session \
  --blackboard-dir /private/tmp/voi-mm-live \
  --command "공격적으로 마린 탐색해서 적발견시 바로 공격해" \
  --update-id manual-live-attack-now \
  --pretty
```

Legacy python-sc2 remains available only as an explicit command-line
compatibility path; it is not exposed in the compact controller:

```bash
python3 -m starcraft_commander.demo_sc2 --dry-run --gui
python3 -m starcraft_commander.demo_sc2 --dry-run --gui 0
```

`--gui 0` asks the OS for an available port, but the served page remains the
compact MicroMachine controller. The old `/api/command` route is compatibility
infrastructure and is not MicroMachine QA evidence.

For actual local play through the legacy python-sc2 commander:

```bash
SC2PATH="/path/to/StarCraft II" \
python3 -m starcraft_commander.demo_sc2 \
  --map AcropolisLE --difficulty easy
```

Use the terminal prompt for this compatibility runtime. Adding `--gui` only
serves the compact MicroMachine controller; it does not restore browser control
of the legacy game. Live mode fails before StarCraft II starts unless the
selected provider key is already available through `OPENAI_API_KEY` or
`ANTHROPIC_API_KEY`.

For phone/tablet compact queueing and observation on the same Wi-Fi:

```bash
python3 -m starcraft_commander.web_gui --dry-run \
  --host 0.0.0.0 --token "change-me-long-random-token"
```

Open the printed `http://0.0.0.0:PORT/?token=...` URL by replacing
`0.0.0.0` with the Mac's LAN IP address. This is the same queue-only compact
page, not a remote native launcher. Non-localhost binding requires `--token`;
without it, the server refuses to start.

### LLM Interpreter

Legacy python-sc2 live mode requires the LLM interpreter. Every legacy user
utterance goes through the selected provider before any mutating action can
execute. The MicroMachine web cockpit also requires validated LLM tool-call/JSON
output and fails closed when that contract is not met. The LLM is called once
per user command, never per game frame.

```bash
export OPENAI_API_KEY=...
python3 -m starcraft_commander.demo_sc2 --dry-run
python3 -m starcraft_commander.demo_sc2 --dry-run --gui
```

Live mode requires the selected key before startup. Defaults are
`--llm-provider openai`, `OPENAI_API_KEY`, and `--llm-model gpt-5.5`;
Anthropic remains available with `--llm-provider anthropic` and
`ANTHROPIC_API_KEY`.

LLM output is schema-gated to the 10 canonical intents and revalidated before
execution.

For legacy offline tests without an API key:

```bash
python3 -m starcraft_commander.demo_sc2 --dry-run --no-llm
```

### Voice

Push-to-talk Korean input:

```bash
python3 -m starcraft_commander.demo_sc2 --dry-run --voice
python3 -m starcraft_commander.demo_sc2 --dry-run --voice --record-seconds 3
```

Notes:

- Press Enter to record a fixed window.
- Default transcription model is faster-whisper `small`, language `ko`.
- The model downloads on first use.
- macOS users must grant microphone permission to the terminal app.
- Low-confidence transcriptions are re-prompted instead of executed.

### Live StarCraft II

Requires StarCraft II, maps, and `[sc2]`:

```bash
python3 -m starcraft_commander.demo_sc2 --map AcropolisLE --difficulty easy
python3 -m starcraft_commander.demo_sc2 --map AcropolisLE --difficulty easy --voice
python3 -m starcraft_commander.demo_sc2 --map AcropolisLE --difficulty easy --gui
```

This path has been locally smoke-tested against a macOS StarCraft II install
with `AcropolisLE`, including the localhost GUI, state polling, OpenAI key
status, SCV production, SCV scouting, mineral gathering, and Supply Depot
construction commands. Follow [docs/sc2-smoke-test.md](docs/sc2-smoke-test.md)
to repeat the test.

## Supported Intents

The MVP supports 10 canonical intents:

| Intent | Examples |
| --- | --- |
| `GATHER_RESOURCE` | "SCV 4기 미네랄 캐", "자원채취" |
| `BUILD_STRUCTURE` | "보급고 지어", "배럭 지어" |
| `TRAIN_WORKER` | "SCV 계속 찍어", "일꾼 두 기 뽑아", "SCV 여러개 뽑아" |
| `TRAIN_ARMY` | "마린 3기 뽑아" |
| `SCOUT` | "적 본진 정찰 보내", "정찰보내" |
| `SUMMARIZE_STATE` | "상황 보고해줘", "상태확인" |
| `DEFEND` | "마린 6기 입구로 보내" |
| `REPAIR` | "SCV 2기로 벙커 수리해" |
| `EXPAND` | "앞마당 가져가" |
| `HARASS` | "벌처로 일꾼 견제해" |

The executable inventory lives in [docs/intent-inventory.md](docs/intent-inventory.md).

## Architecture

```text
Current compact MicroMachine controller:
Korean text / voice
  -> forced-tool LLM semantic compiler
  -> deterministic PolicyModulationVector validation
  -> Macro state + operations[] registry + Micro/Emergency overlays
  -> flat MicroMachine blackboard
  -> patched C++ OperationDirector / CombatCommander / Squad
  -> RangedManager / MeleeManager / unit ability logic
  -> SC2 API
  -> operation-scoped telemetry
  -> compact controller + in-game HUD

Legacy python-sc2 command-line compatibility runtime:
Korean text / voice
  -> LLM-mandatory interpreter
  -> deprecated offline rules only when explicitly using --no-llm
  -> typed Intent DSL
  -> game-state resolver
  -> feasibility validator
  -> semantic action planner
  -> runtime executor
  -> game API adapter
  -> Korean narrator + event memory
```

Key packages:

- `starcraft_commander` — real SC2 commander boundary, demo entrypoint, and
  semantic executor abstraction.
- `broodwar_commander` — Brood War semantic executor boundary, pre-real-adapter.
- `toycraft_commander` — offline deterministic harness used for parser,
  validation, rule-engine, and narration tests.

Important modules:

- `starcraft_commander/demo_sc2.py` — CLI for dry-run, live, voice, required live LLM, GUI.
- `starcraft_commander/live_pipeline.py` — session orchestration and compound commands.
- `starcraft_commander/sc2_executor.py` — Intent DSL to semantic SC2 plans.
- `starcraft_commander/python_sc2_adapter.py` — semantic actions to BotAI calls.
- `starcraft_commander/event_memory.py` — bounded thread-safe command history.
- `starcraft_commander/standing_orders.py` — per-frame code policies, never LLM.
- `starcraft_commander/web_gui.py` — localhost-first stdlib server whose three
  controller routes render the same compact MicroMachine page. Legacy
  `/api/command` remains compatibility infrastructure but has no UI mode.
- `starcraft_commander/local_cockpit.py` — installed macOS app, single-window
  WebKit host, native SC2 launch proof, and MicroMachine auto-start boundary.
- `starcraft_commander/micromachine_live_session.py` — text/LLM/UI provider
  sidecar that merges standing Macro state, upserts independent operations,
  applies dynamic lifetimes, and publishes bounded modulation.
- `starcraft_commander/micromachine_runtime.py` — MicroMachine blackboard
  backend, indexed `operations.N.*` KV protocol, and telemetry contract.
- `starcraft_commander/micromachine_command_execution.py` — operation-scoped
  execution classifier from parse through observed gameplay effect.
- `starcraft_commander/llm_interpreter.py` — schema-gated OpenAI/Anthropic interpreter.
- `integrations/micromachine/patches/` — ordered C++ integration patches,
  including dynamic operation squads, exclusive ownership, all-unit scout and
  combat behavior, abilities, production closure, and in-game HUD.
- `broodwar_commander/bw_executor.py` — BWAPI-style semantic plans and executor.

Detailed design docs:

- [docs/architecture.md](docs/architecture.md)
- [docs/battlefield-command-ux-design.md](docs/battlefield-command-ux-design.md)
- [docs/contracts.md](docs/contracts.md)
- [docs/sc2-collaboration-policy-tree.md](docs/sc2-collaboration-policy-tree.md)
- [docs/sc2-smoke-test.md](docs/sc2-smoke-test.md)

## Safety And Honesty Contracts

- No mouse automation.
- Optional dependencies are lazy-loaded.
- Blocked commands do not mutate state.
- Partial or skipped work is never narrated as success.
- Rejections include Korean reason and alternative.
- The LLM can only produce schema-validated intents or policy operations.
- The LLM is called per user utterance, never per game frame.
- MicroMachine cockpit input publishes only bounded DSL modulation; no raw
  unit tags, python-sc2 calls, s2client-api calls, keyboard hooks, OCR, or
  mouse automation are fallback paths.
- Missing forced-tool or structured JSON output fails closed. Smoke keyword
  lowering is test-only and is never presented as LLM execution.
- A unit tag has one authoritative operation owner. Duplicate ownership blocks
  the affected operations instead of silently double-commanding the unit.
- Unknown enemy locations are resolved from allowed map/start-location
  information and observed game state. Runtime evidence must not claim fresh
  enemy observation before it exists.
- The legacy python-sc2 commander runtime is command-line opt-in and must not be
  treated as MicroMachine production evidence.
- Web GUI binds to `127.0.0.1` by default; network companion mode requires a token.

## Development

Run tests:

```bash
.venv/bin/python -m pytest -q
```

Check import hygiene:

```bash
.venv/bin/python -c "import starcraft_commander, toycraft_commander, broodwar_commander; print('imports-ok')"
.venv/bin/python -c "import json, sys; import starcraft_commander, broodwar_commander; print(json.dumps([m for m in ['sc2','anthropic','openai','faster_whisper','sounddevice'] if m in sys.modules]))"
```

Expected output for the second command is `[]`.

Clean patched MicroMachine build:

```bash
integrations/micromachine/scripts/build_macos_local.sh
```

The build is not accepted unless
`/private/tmp/voi-micromachine-runtime/MicroMachine/build-latest-api/voi_build_identity.json`
exists and verifies the binary, pinned source commits, patch checksums, and
attestations.

Local live QA:

```bash
integrations/micromachine/scripts/smoke_macos_local.sh \
  --live-hold \
  --blackboard-dir /private/tmp/voi-mm-live \
  --max-attempts 1
```

Then issue a parallel command against the same blackboard from the web cockpit
or the live-session CLI. A pass requires separate operation IDs, non-overlapping
assigned unit tags, SC2 action submission, and observed movement or engagement.

## Distribution Compliance

Release artifacts are qualified from the built wheel and sdist rather than from
source-tree claims:

```bash
python -m starcraft_commander.distribution_compliance \
  --repository . \
  --base-commit "$(git merge-base HEAD origin/main)" \
  --dist-dir dist \
  --output-dir distribution-compliance-evidence
```

The gate verifies the exact package allowlist, installed license expression,
license and third-party notice files, runtime manifests, dependency notices,
isolated wheel loading, and redacted secret/private-configuration scan results.
Qualification requires a clean Git checkout before and after the build and
records the exact HEAD and tree identities. `--skip-install-smoke` is
diagnostic-only and always produces a non-qualifying report. Installed wheels
provide relocatable read-only MicroMachine assets; launching the provenance-
bound local MicroMachine runtime remains available only from a source checkout.
Hosted CI uploads the qualified `dist/` bytes together with their compliance
evidence as one artifact; any future publication workflow must consume those
exact archives and verify their recorded SHA-256 digests instead of rebuilding.
The separate commercial license applies only to voiStarcraft2 copyrights. It
does not replace or waive MicroMachine, s2client-api, Python dependency, or
other third-party license and attribution obligations.

## Project Direction

The project has deliberately moved through four stages:

1. **Semantic boundary first.** Commands were separated from mouse clicks and
   raw API calls through typed intents and executor abstractions.
2. **MicroMachine integration.** The production path moved from legacy
   python-sc2 control to manager-level blackboard modulation.
3. **Execution honesty.** Telemetry and UI stopped treating "published" or
   "manager mentioned the command" as proof that units moved.
4. **Battlefield command.** The current architecture supports persistent Macro
   intent plus multiple independently owned operations, explicit micro, and
   emergency steering.

The design principles going forward are:

- LLM interpretation once per utterance, never inside the game-frame loop.
- Deterministic schema validation and prerequisite lowering after the LLM.
- MicroMachine remains responsible for tactical safety and unit micro.
- User intent remains visible as stable operation identities instead of
  dissolving into one global aggression bias.
- Runtime claims require SC2 command and observed-effect evidence.
- Local credentials, MyProxy configuration, API keys, and private secret
  configuration never enter source control.

## Roadmap

| Milestone | Scope |
| --- | --- |
| Live parallel-operation qualification | Repeatable matrix across scout, attack, defense, mixed ground/air, abilities, cancellation, reinforcement, and blockers. |
| Richer tactical decomposition | Multi-stage routes, regroup conditions, synchronized support, and context-aware operation reinforcement without frame scripting. |
| Replay and evaluation learning | Compare intended operation outcomes with replay evidence and improve deterministic planning policies. |
| Multiplayer qualification | Custom-game protocol, fair-information review, disconnect/recovery policy, anti-cheat constraints, and human-match test signoff. |
| Brood War adapter | Real BWAPI execution behind the existing semantic boundary. |

## Remaining Real-World Validation

These require external software and are intentionally not claimed as completed:

- Build and validate a real BWAPI binding adapter on a Brood War + BWAPI setup.
- Run broader live LLM checks across OpenAI and Anthropic models beyond the
  local web-key configuration smoke test.
- Qualify the operation system against human multiplayer. Current work does not
  claim ladder or human-match readiness.
