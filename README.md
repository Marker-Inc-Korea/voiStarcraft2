# voiStarcraft2

말하면 스타가 움직인다.

voiStarcraft2 turns Korean text or voice commands into semantic
StarCraft commands. It does **not** emulate mouse clicks or screen input.
Commands are interpreted into a typed Intent DSL, validated against game state,
planned as semantic actions, and executed through game API boundaries.

## Status

| Area | Status |
| --- | --- |
| Dry-run SC2 pipeline | Implemented and tested. Runs without StarCraft II. |
| Legacy live SC2 commander | Implemented and locally connected through python-sc2. This is compatibility mode, not MicroMachine. |
| MicroMachine policy cockpit | Implemented as the default web text/voice route. It publishes bounded deep DSL modulation to the MicroMachine blackboard. |
| Voice input | Implemented behind optional `[voice]` dependencies. |
| LLM command interpreter | Required for legacy python-sc2 live commands and production MicroMachine free-form text modulation. OpenAI/GPT is the default; Anthropic is still supported. |
| Web GUI | Implemented as a localhost-first stdlib server with token-protected network mode. Default chat/voice mode is MicroMachine; legacy commander is explicit opt-in. |
| Event memory | Implemented and used by state reports and GUI history. |
| Standing orders | Implemented for continuous SCV production and supply-block prevention. |
| Brood War / BWAPI | Semantic executor boundary implemented; real BWAPI adapter still requires a BWAPI machine. |

Current offline verification:

```bash
python3 -m pytest -q
# 820 passed, 4 skipped, 1763 subtests passed
```

The suite does not require StarCraft II, `burnysc2`, BWAPI, LLM credentials,
or audio hardware.

## License

voiStarcraft2 is dual-licensed as `AGPL-3.0-or-later OR commercial`.
Commercial closed-source use requires a paid commercial license from the
copyright holder. If you do not obtain a commercial license, you must comply
with the AGPL source-code disclosure obligations for the covered work.

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
pip install -e '.[dev]'       # pytest
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
keyboard, or mouse. The web cockpit prefers configured LLM forced-tool output,
but tactical live-QA commands such as scout, attack, rush, pressure, defend, and
hold are rescued by a bounded web fallback DSL when the LLM misses tool-call or
JSON output. CLI keyword publishing remains explicit smoke/test-only and is
labeled `source=smoke_keyword`, never `source=llm`.

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
SC2PATH="/Users/jinminseong/Desktop/StarCraft2/StarCraft II" \
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
SC2PATH="/Users/jinminseong/Desktop/StarCraft2/StarCraft II" \
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
execute. The MicroMachine web cockpit is different: it uses the LLM first, then
uses a bounded web fallback DSL for tactical live-QA commands if the provider
does not return tool-call/JSON. The LLM is called once per user command, never
per game frame.

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

This path has been locally smoke-tested against the StarCraft II install at
`/Users/jinminseong/Desktop/StarCraft2/StarCraft II` with `AcropolisLE`,
including the localhost GUI, state polling, OpenAI key status, SCV production,
SCV scouting, mineral gathering, and Supply Depot construction commands. Follow
[docs/sc2-smoke-test.md](docs/sc2-smoke-test.md) to repeat the test.

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
  -> bounded MicroMachine DSL compiler/provider
  -> PolicyModulationVector
  -> MicroMachine blackboard backend
  -> patched MicroMachine C++ managers
  -> telemetry / tactical logs / dashboard

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
  sidecar that publishes bounded modulation to MicroMachine.
- `starcraft_commander/micromachine_runtime.py` — MicroMachine blackboard
  backend and telemetry contract.
- `starcraft_commander/llm_interpreter.py` — schema-gated OpenAI/Anthropic interpreter.
- `broodwar_commander/bw_executor.py` — BWAPI-style semantic plans and executor.

Detailed design docs:

- [docs/architecture.md](docs/architecture.md)
- [docs/contracts.md](docs/contracts.md)
- [docs/claude-handoff.md](docs/claude-handoff.md)
- [docs/sc2-collaboration-policy-tree.md](docs/sc2-collaboration-policy-tree.md)
- [docs/sc2-smoke-test.md](docs/sc2-smoke-test.md)

## Safety And Honesty Contracts

- No mouse automation.
- Optional dependencies are lazy-loaded.
- Blocked commands do not mutate state.
- Partial or skipped work is never narrated as success.
- Rejections include Korean reason and alternative.
- The LLM can only produce schema-validated canonical intents.
- The LLM is called per user utterance, never per game frame.
- MicroMachine cockpit input publishes only bounded DSL modulation; no raw
  unit tags, python-sc2 calls, s2client-api calls, keyboard hooks, OCR, or
  mouse automation are fallback paths.
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

## Remaining Real-World Validation

These require external software and are intentionally not claimed as completed:

- Build and validate a real BWAPI binding adapter on a Brood War + BWAPI setup.
- Run broader live LLM checks across OpenAI and Anthropic models beyond the
  local web-key configuration smoke test.
