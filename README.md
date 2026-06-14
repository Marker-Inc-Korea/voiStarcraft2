# TextCraft Commander (voistarcraft)

말하면 스타가 움직인다.

TextCraft Commander turns Korean text or voice commands into semantic
StarCraft commands. It does **not** emulate mouse clicks or screen input.
Commands are interpreted into a typed Intent DSL, validated against game state,
planned as semantic actions, and executed through game API boundaries.

## Status

| Area | Status |
| --- | --- |
| Dry-run SC2 pipeline | Implemented and tested. Runs without StarCraft II. |
| Live SC2 mode | Implemented and locally connected through python-sc2. |
| Voice input | Implemented behind optional `[voice]` dependencies. |
| LLM command interpreter | Required for live SC2 mode; uses `[llm]` dependencies and Anthropic API key. |
| Web GUI | Implemented as a localhost-first stdlib server with token-protected network mode. |
| Event memory | Implemented and used by state reports and GUI history. |
| Standing orders | Implemented for continuous SCV production and supply-block prevention. |
| Brood War / BWAPI | Semantic executor boundary implemented; real BWAPI adapter still requires a BWAPI machine. |

Current offline verification:

```bash
python3 -m pytest -q
# 812 passed, 1759 subtests passed
```

The suite does not require StarCraft II, `burnysc2`, BWAPI, Anthropic
credentials, or audio hardware.

## Quickstart

Run the full commander pipeline against a scripted fake BotAI:

```bash
python3 -m starcraft_commander.demo_sc2 --dry-run --no-llm --script "마린 6기 입구로 보내고 SCV 계속 찍어" "상황 보고해줘"
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
python3 -m starcraft_commander.demo_sc2 --dry-run --no-llm
```

## Installation

Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate

pip install -e .              # core: dry-run, interpreter, validators, planners
pip install -e '.[sc2]'       # live SC2 mode via burnysc2
pip install -e '.[voice]'     # Korean push-to-talk via faster-whisper + sounddevice
pip install -e '.[llm]'       # required Anthropic LLM interpreter for live play
pip install -e '.[dev]'       # pytest
```

Live SC2 also requires a local StarCraft II installation and maps. See
[docs/sc2-smoke-test.md](docs/sc2-smoke-test.md).

## Run Modes

### Dry-Run

No StarCraft II required. Default dry-run uses the LLM path when
`ANTHROPIC_API_KEY` is available; use `--no-llm` for offline deterministic
development:

```bash
python3 -m starcraft_commander.demo_sc2 --dry-run --no-llm
python3 -m starcraft_commander.demo_sc2 --dry-run --no-llm --script "SCV 계속 찍어" "상황 보고"
```

### Web GUI

Starts a browser UI with command input, state, and history. For local computer
control, keep the default localhost binding and run StarCraft II in windowed or
borderless-window mode so the browser GUI can stay visible beside the game:

```bash
python3 -m starcraft_commander.demo_sc2 --dry-run --gui
python3 -m starcraft_commander.demo_sc2 --dry-run --gui 0
```

`--gui 0` asks the OS for an available port.

For actual local play:

```bash
export ANTHROPIC_API_KEY=...
SC2PATH="/Users/jinminseong/Desktop/StarCraft2/StarCraft II" \
python3 -m starcraft_commander.demo_sc2 \
  --map AcropolisLE --difficulty easy \
  --gui
```

Open the printed `http://127.0.0.1:PORT` URL on the same Mac. If StarCraft II
is exclusive fullscreen, local GUI typing requires switching focus away from
the game; use windowed/borderless mode or a second monitor for stable local
GUI control.

For phone/tablet companion control on the same Wi-Fi:

```bash
export ANTHROPIC_API_KEY=...
SC2PATH="/Users/jinminseong/Desktop/StarCraft2/StarCraft II" \
python3 -m starcraft_commander.demo_sc2 \
  --map AcropolisLE --difficulty easy \
  --gui --gui-host 0.0.0.0 --gui-token "change-me-long-random-token"
```

Open the printed `http://0.0.0.0:PORT/?token=...` URL by replacing
`0.0.0.0` with the Mac's LAN IP address. Non-localhost GUI binding requires
`--gui-token`; without it, the server refuses to start.

### LLM Interpreter

Live SC2 mode requires the Anthropic-backed hybrid interpreter. Rules still
run first, and the LLM is used only for unsupported or ambiguous user
utterances, once per user command. It is never called per game frame.

```bash
export ANTHROPIC_API_KEY=...
python3 -m starcraft_commander.demo_sc2 --dry-run
python3 -m starcraft_commander.demo_sc2 --dry-run --gui
```

LLM output is schema-gated to the 10 canonical intents and revalidated before
execution.

For offline tests or deterministic dry-run development without an API key:

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

This path is implemented but has not yet been verified against a real local
SC2 installation in this development environment. Follow
[docs/sc2-smoke-test.md](docs/sc2-smoke-test.md) for the first real smoke test.

## Supported Intents

The MVP supports 10 canonical intents:

| Intent | Examples |
| --- | --- |
| `GATHER_RESOURCE` | "SCV 4기 미네랄 캐" |
| `BUILD_STRUCTURE` | "보급고 지어", "배럭 지어" |
| `TRAIN_WORKER` | "SCV 계속 찍어", "일꾼 두 기 뽑아" |
| `TRAIN_ARMY` | "마린 3기 뽑아" |
| `SCOUT` | "적 본진 정찰 보내" |
| `SUMMARIZE_STATE` | "상황 보고해줘" |
| `DEFEND` | "마린 6기 입구로 보내" |
| `REPAIR` | "SCV 2기로 벙커 수리해" |
| `EXPAND` | "앞마당 가져가" |
| `HARASS` | "벌처로 일꾼 견제해" |

The executable inventory lives in [docs/intent-inventory.md](docs/intent-inventory.md).

## Architecture

```text
Korean text / voice
  -> rules-first interpreter, optional LLM fallback
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
- `starcraft_commander/web_gui.py` — localhost-first stdlib web UI.
- `starcraft_commander/llm_interpreter.py` — schema-gated Anthropic interpreter.
- `broodwar_commander/bw_executor.py` — BWAPI-style semantic plans and executor.

Detailed design docs:

- [docs/architecture.md](docs/architecture.md)
- [docs/contracts.md](docs/contracts.md)
- [docs/claude-handoff.md](docs/claude-handoff.md)
- [docs/sc2-smoke-test.md](docs/sc2-smoke-test.md)

## Safety And Honesty Contracts

- No mouse automation.
- Optional dependencies are lazy-loaded.
- Blocked commands do not mutate state.
- Partial or skipped work is never narrated as success.
- Rejections include Korean reason and alternative.
- The LLM can only produce schema-validated canonical intents.
- The LLM is called per user utterance, never per game frame.
- Web GUI binds to `127.0.0.1` by default; network companion mode requires a token.

## Development

Run tests:

```bash
.venv/bin/python -m pytest -q
```

Check import hygiene:

```bash
.venv/bin/python -c "import starcraft_commander, toycraft_commander, broodwar_commander; print('imports-ok')"
.venv/bin/python -c "import json, sys; import starcraft_commander, broodwar_commander; print(json.dumps([m for m in ['sc2','anthropic','faster_whisper','sounddevice'] if m in sys.modules]))"
```

Expected output for the second command is `[]`.

## Remaining Real-World Validation

These require external software and are intentionally not claimed as completed:

- Run the live SC2 smoke test on a machine with StarCraft II and maps installed.
- Build and validate a real BWAPI binding adapter on a Brood War + BWAPI setup.
- Run a live Anthropic API check with `ANTHROPIC_API_KEY`.
