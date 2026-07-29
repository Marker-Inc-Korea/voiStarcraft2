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

The web cockpit presents one card per operation rather than one global command
bubble. Each card shows mission, force, target, route, lifecycle, assigned unit
count, last SC2 action, movement or engagement evidence, and terminal state.
Late responses and stale telemetry cannot overwrite a newer operation card.
All-Terran family/action evidence extends the existing Operation card and its
four-stage `해석 → 배정 → 제출 → 관측` rail; it does not introduce a separate
dashboard or a new visual language.

The patched in-game HUD mirrors the same operation identity and evidence inside
StarCraft II so the operator can confirm commands without leaving the game:

```text
[recon-alpha]  2 Marine  enemy_main     assigned -> moving
[assault-bravo] 4 Marine flank_right    action issued -> engaged
```

The web cockpit remains the command surface. The in-game HUD is evidence and
situational feedback, not a hidden mouse or keyboard automation layer.

## Status

| Area | Status |
| --- | --- |
| Dry-run SC2 pipeline | Implemented and tested. Runs without StarCraft II. |
| Legacy live SC2 commander | Implemented and locally connected through python-sc2. This is compatibility mode, not MicroMachine. |
| MicroMachine policy cockpit | Default web text/voice route. Uses forced-tool LLM output, deterministic validation, and fail-closed publishing. |
| Parallel operations | Implemented through explicit `operations[]`, stable IDs, immutable per-generation deadlines, runtime-authoritative lifecycle, live upsert semantics, dynamic operation squads, and exclusive unit ownership. |
| Terran operation runtime support | Implemented for supported Terran combat families by reusing existing MicroMachine Squad and unit ability code paths. |
| Comprehensive all-Terran live qualification | Pending family-by-family evidence beyond the currently qualified Marine/Tank and selected support paths. |
| Web operation UX | Per-operation cards, isolated telemetry, monotonic lifecycle updates, and truthful published/executing distinction. |
| In-game HUD | Patched MicroMachine overlay for operation identity, force, route, target, assignment, action, movement, engagement, and blockers. |
| Voice input | Implemented behind optional `[voice]` dependencies. |
| LLM command interpreter | Required for legacy python-sc2 live commands and production MicroMachine free-form text modulation. OpenAI/GPT is the default; Anthropic is still supported. |
| Web GUI | Implemented as a localhost-first stdlib server with token-protected network mode. Default chat/voice mode is MicroMachine; legacy commander is explicit opt-in. |
| Event memory | Implemented and used by state reports and GUI history. |
| Standing orders | Implemented for continuous SCV production and supply-block prevention. |
| Brood War / BWAPI | Semantic executor boundary implemented; real BWAPI adapter still requires a BWAPI machine. |
| Human multiplayer | Deferred. Current qualification target is local AI/custom-game operation control. |

## Latest Qualification

The current cockpit pre-live regression was refreshed on July 29, 2026.
The latest clean patched-build and live SC2 evidence below was collected on
July 27, 2026:

| Gate | Result |
| --- | --- |
| Current Python suite | `2157 passed, 6363 subtests passed` in the local `dev + llm` environment |
| Historical cross-version baseline | Python 3.10, 3.11, and 3.12 each passed `1904 tests, 5357 subtests` on July 27, 2026 |
| MicroMachine integration kit | `105 passed, 2091 subtests passed` |
| Current web operation UX | `198 passed, 296 subtests passed`, including non-blocking SSE publication, replay rollover recovery, atomic operation/overview snapshot acceptance, exact update/operation/generation execution identity, non-authoritative detached registry and session-epoch preservation, top-level projection epoch rejection, concurrent source high-water rejection, retention-plus-one milestone deduplication, truthful `order_issued` versus `action_issued`, cancellation cleanup waiting, stable 24-card reconciliation, lane-move focus, and accessibility motion fallbacks |
| Actual Chrome cockpit QA | Chrome 150 passed at desktop `1440x1100` and mobile `390x844`: four lanes, five standard actions per card, no duplicate IDs or horizontal overflow, stable card/focus continuity, contextual-control focus fallback, reduced-motion progress/typing/voice fallbacks, forced-colors, and accessibility roles |
| Clean patched build | Build identity schema `56`, `ok=true`, identity `sha256:bdb1a8fbbf4ae8449ae8604e54f3a59fcd7e0a077755f17dc521ff225ccfbe0b`, embedded build-input identity `sha256:123adec4894c856c68df71d5f69e08072c8747e72b617b883de6fecccb638410`, binary SHA-256 `4413cec7eae52c04de31d0586ce42e42509dbf673f81d0c100538a715082f9a3` |
| Fresh live smoke | Difficulty `10`, run ID `20260727T143156Z-27690-3244`, single attempt, final accepted frame `5250`, exit code `0` |
| Tech-gas opening | A required Refinery was queued and promoted at frame `1527`; the first Barracks issued an actual SC2 command at `1561`, the Refinery issued its build command at `2616` and was observed building at `2689`, the second Barracks issued an actual command at `2655`, the Refinery completed at `3118`, and `3` live gas workers were observed by frame `3463` |
| Parallel execution | The parallel update was published at frame `3716` and observed by the manager at `3731`; attack submitted at `3731`, reached `MOVING` at `3745` and `ENGAGED` at `4703`; scout submitted at `5030` and reached `MOVING` at `5046`; the operations used different exclusive unit tags. `MOVING` is set only after observed displacement from the per-unit SC2 submission position. |
| Selective cancellation | Attack cancellation was published at frame `5030`; frame `5046` recorded matching-generation `release_stop`, released `smoke-attack-bravo#1`, and retained an owner-keyed purge event with exactly one exclusively owned queue item removed while the scout remained `MOVING`; every later archived terminal snapshot through frame `5085` preserved the same cleanup action and frame |
| Autonomous restoration | Restore policy was issued at frame `5085` with MainAttack command baseline `4`; a fresh autonomous MainAttack action and same-unit movement were observed under the restore policy by frame `5091`, command count reached `8` at frame `5139`, and the final accepted snapshot recorded `31.1514` maximum home distance |
| Provenance | Runtime manifest covered 36 Python source files and matched the schema-56 embedded build identity through smoke completion |

This qualification proves the tested parallel operation and autonomous
restoration path. It does not replace the family-by-family all-Terran live
matrix described above.

Current verification command:

```bash
python3 -m pytest -q
```

The suite does not require StarCraft II, `burnysc2`, BWAPI, LLM credentials,
or audio hardware.

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

Starts a browser cockpit with command input, voice input, MicroMachine DSL
status, state, and history. The top chat/voice input defaults to
**MicroMachine policy cockpit** mode:

```text
text / voice
  -> bounded MicroMachine DSL compiler
  -> MicroMachine blackboard directory
  -> patched MicroMachine C++ managers
  -> telemetry / tactical logs shown in the dashboard
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

In that page, the **Commander Chat** and browser voice button are the unified
input surface. Select **MicroMachine policy cockpit** or
**Legacy python-sc2 commander**, then use **선택 모드 실행** to start the selected
runtime from the same cockpit. In MicroMachine mode this calls
`POST /api/runtime/start` and launches
`integrations/micromachine/scripts/smoke_macos_local.sh` with the current
blackboard directory, so StarCraft II and patched MicroMachine can be started
from the UI when the local SC2/MicroMachine build prerequisites are present.
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

The right-side **MicroMachine runtime / DSL evidence** panel is a collapsed
advanced/debug panel. It controls the blackboard directory and optional
semantic scope used by the top chat/voice input, and it shows telemetry evidence
that patched MicroMachine consumed the published DSL. The left Commander Chat
remains the primary input surface.

Legacy python-sc2 GUI remains available only as an explicit compatibility mode:

```bash
python3 -m starcraft_commander.demo_sc2 --dry-run --gui
python3 -m starcraft_commander.demo_sc2 --dry-run --gui 0
```

`--gui 0` asks the OS for an available port. In the page, select
**Legacy python-sc2 commander** only when intentionally testing the old
`/api/command` route. It is not MicroMachine QA evidence.

For actual local play through the legacy python-sc2 commander:

```bash
SC2PATH="/path/to/StarCraft II" \
python3 -m starcraft_commander.demo_sc2 \
  --map AcropolisLE --difficulty easy \
  --gui
```

Open the printed `http://127.0.0.1:PORT` URL on the same Mac. If StarCraft II
is exclusive fullscreen, local GUI typing requires switching focus away from
the game; use windowed/borderless mode or a second monitor for stable local
GUI control. Live mode now fails before StarCraft II starts unless the selected
provider key is already available through `OPENAI_API_KEY` or
`ANTHROPIC_API_KEY`. The web GUI's **LLM 설정** panel can rotate the key for the
running local process, but it cannot bypass startup preflight for the legacy
python-sc2 path. Keys are kept only in process memory and are never written to
repo files or returned by `/api/llm`.

The standalone web GUI no longer auto-starts the legacy python-sc2 live GUI
after LLM setup, because that looked like a MicroMachine launch. The explicit
**선택 모드 실행** button is the supported launch path for both modes. If you
explicitly need key-save-time legacy auto-launch for compatibility testing:

```bash
python3 -m starcraft_commander.web_gui --dry-run --auto-launch-legacy-live
```

For phone/tablet companion control on the same Wi-Fi:

```bash
SC2PATH="/path/to/StarCraft II" \
python3 -m starcraft_commander.demo_sc2 \
  --map AcropolisLE --difficulty easy \
  --gui --gui-host 0.0.0.0 --gui-token "change-me-long-random-token"
```

Open the printed `http://0.0.0.0:PORT/?token=...` URL by replacing
`0.0.0.0` with the Mac's LAN IP address. Non-localhost GUI binding requires
`--gui-token`; without it, the server refuses to start.

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
Default MicroMachine cockpit:
Korean text / voice
  -> forced-tool LLM semantic compiler
  -> deterministic PolicyModulationVector validation
  -> Macro state + operations[] registry + Micro/Emergency overlays
  -> flat MicroMachine blackboard
  -> patched C++ OperationDirector / CombatCommander / Squad
  -> RangedManager / MeleeManager / unit ability logic
  -> SC2 API
  -> operation-scoped telemetry
  -> web cards + in-game HUD

Legacy python-sc2 commander mode:
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
- `starcraft_commander/web_gui.py` — localhost-first stdlib web UI; default
  chat/voice route is MicroMachine DSL, legacy python-sc2 commander is opt-in,
  and `/api/runtime/start|status` launch/status the selected runtime.
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
- Legacy python-sc2 commander mode is visibly opt-in and must not be treated as
  MicroMachine production evidence.
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
