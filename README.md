# TextCraft Commander (voistarcraft)

말하면 스타가 움직인다 — speak (or type) Korean commands and StarCraft II
executes them as real game API actions.

Natural-language RTS commander layer for real StarCraft II control. The
project never emulates mouse clicks or screen automation: Korean commander
text is interpreted into a typed Intent DSL, validated against live game
state, planned as semantic StarCraft II actions, and issued through a
`python-sc2` (burnysc2) BotAI adapter.

## Honest Status

Be clear about what is and is not proven:

- **Implemented and unit-tested (no StarCraft II needed):** the full live
  pipeline code path — Korean interpreter, Intent DSL, live SC2 feasibility
  validator, SC2 state resolver, semantic map-target resolver, python-sc2
  BotAI adapter, Korean narrator, compound-command splitting, the
  `demo_sc2` entrypoint (dry-run, scripted, interactive, and voice modes),
  and faster-whisper Korean voice input. `python3 -m pytest -q` currently
  passes 636 tests (1475 subtests) without StarCraft II, python-sc2, or
  audio hardware installed.
- **Implemented but NOT yet smoke-tested against a real game:** live mode.
  End-to-end live play requires a local StarCraft II installation plus the
  `burnysc2` package, and that real-game smoke test has not been run in this
  development environment yet. The live executor, on_step command queue, and
  map derivation code exist and are exercised only against BotAI-like fakes.
  Follow [docs/sc2-smoke-test.md](docs/sc2-smoke-test.md) to run the first
  real smoke test yourself.

## Quickstart (dry-run, no StarCraft II needed)

The dry-run mode runs the entire real pipeline (interpret -> validate ->
plan -> execute -> narrate) against a built-in scripted fake BotAI:

```bash
python3 -m starcraft_commander.demo_sc2 --dry-run --script "마린 6기 입구로 보내고 SCV 계속 찍어" "상황 보고해줘"
```

Real output from this command:

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
[partially_executed] 일부만 실행되었습니다. 실행: SCV 1기 생산 명령. 보류: 지속 생산은 아직 지원되지 않아 이번 1회 생산 명령만 실행했습니다. 계속 생산하려면 같은 명령을 다시 말해 주세요.

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
```

Note the honesty contract in action: the compound utterance is split into
two commands, and `SCV 계속 찍어` is narrated as `partially_executed`
because continuous production is a one-shot order today.

Omit `--script` for an interactive `명령>` prompt.

## Installation

Python **3.10+**. The core pipeline is pure stdlib; every runtime
integration is an optional extra (see `pyproject.toml`):

```bash
pip install -e .              # core: interpreter, validators, planners, dry-run demo
pip install -e '.[sc2]'       # + burnysc2 (python-sc2 fork) for live games
pip install -e '.[voice]'     # + faster-whisper, sounddevice for Korean voice input
pip install -e '.[dev]'       # + pytest
```

Live games additionally require a local StarCraft II installation and maps;
see [docs/sc2-smoke-test.md](docs/sc2-smoke-test.md).

## Live StarCraft II Mode

Live mode (the default, no `--dry-run`) launches a local custom game against
the built-in AI and feeds your Korean commands into the bot's `on_step` loop:

```bash
python3 -m starcraft_commander.demo_sc2 --map AcropolisLE --difficulty easy
```

Requirements, map setup, expected behavior, troubleshooting, and the smoke
test checklist live in [docs/sc2-smoke-test.md](docs/sc2-smoke-test.md).
Again: this path is implemented but has not yet been verified against a real
StarCraft II install.

## Voice Mode

Add `--voice` to either dry-run or live mode for push-to-talk Korean voice
input (requires the `[voice]` extra):

```bash
python3 -m starcraft_commander.demo_sc2 --dry-run --voice
python3 -m starcraft_commander.demo_sc2 --voice --map AcropolisLE   # live
```

- Press Enter to record a fixed window (default 5 seconds; tune with
  `--record-seconds`), then the captured audio is transcribed with
  faster-whisper (default model `small`, language `ko`).
- The Whisper model downloads automatically on first use.
- On macOS, grant microphone permission to your terminal app.
- Missing voice dependencies fail fast with bilingual install hints.

## StarCraft II Execution Path

The `starcraft_commander` package is the real StarCraft II boundary. All
modules are importable without StarCraft II or python-sc2 installed (lazy
imports, duck-typed bot objects), so CI verifies the contracts with fakes:

- `contracts.py` — stable semantic SC2 action types (`assign_workers`,
  `build_structure`, `train_unit`, `move_group`, `attack_move`, `repair`,
  `observe`), JSON-ready plan/result contracts.
- `sc2_executor.py` — Intent DSL -> `SC2ExecutionPlan` mapping and the
  lifecycle-aware `SC2RuntimeExecutor` (`start(bot)` / `execute(plan)` /
  `close()`); skipped actions and runtime exceptions become structured
  results, never silent successes.
- `python_sc2_adapter.py` — `PythonSC2BotAdapter` translates the seven
  semantic action types into duck-typed BotAI operations, reporting
  requested-vs-issued counts so partial issuance is never collapsed into a
  bare success.
- `state_resolver.py` — BotAI observations -> `SC2CommanderState`
  (resources, supply, units, structures, threats, production), degrading
  conservatively with observation notes when attributes are missing.
- `map_resolver.py` — semantic map targets (`self_main`, `self_ramp`,
  `enemy_natural`, `enemy_mineral_line`, ...) resolved to Point2-like
  coordinates; underivable targets carry explicit unavailable reasons.
- `feasibility.py` — live validation gate; unknown or incomplete game state
  rejects mutating commands with Korean reasons and alternatives.
- `narrator.py` — Korean narration of execution outcomes; partial or
  blocked work is always disclosed.
- `live_pipeline.py` — `SC2CommandSession` composing all of the above, with
  heuristic Korean compound-command splitting (`...보내고 ...찍어`,
  `그리고`/`하고`).
- `demo_sc2.py` — the runnable entrypoint described above.
- `voice_input.py` / `runtime_deps.py` — Korean speech-to-text seams and
  bilingual optional-dependency guards.

For the original product plan, gap analysis, and the step-by-step handoff
sequence this code implements, see
[docs/claude-handoff.md](docs/claude-handoff.md).

## ToyCraft Offline Harness (Phase 0)

ToyCraft is **not** the product runtime. It is the offline deterministic
harness used to test the parser, validator, rule-engine, and narration
contracts without a StarCraft II installation. Real StarCraft II integration
lives behind the `starcraft_commander` semantic executor abstraction.

### Phase 0 Intent Inventory

The MVP supports exactly 10 canonical intents. See
[docs/intent-inventory.md](docs/intent-inventory.md) and
`toycraft_commander/intents.py` for the executable inventory. Parsed Korean
commands serialize to the stable `toycraft.intent_dsl.v1` JSON document
format via `IntentCommandPayload.to_dsl_document()` / `.to_dsl_json()`.

### Phase 0 Component Architecture

Component boundaries and the end-to-end command data flow (Korean input ->
typed DSL -> validation -> rule execution -> narrated result) are documented
in [docs/architecture.md](docs/architecture.md). `pipeline.py` coordinates
independent interpreter, feasibility-validator, executor/rule-engine, and
narrator layers; rejected or unclear commands stop before state mutation.
The typed Intent DSL, validator, rule-engine, and executor interface
contracts are documented in [docs/contracts.md](docs/contracts.md).

### Phase 0 Models

- **Units:** SCV, Marine, Vulture, Zealot — typed cost and combat stats in
  `toycraft_commander/units.py`.
- **Structures:** Barracks, Factory, Supply Depot, Refinery — construction
  costs, build times, prerequisites, supply impact, and capabilities in
  `toycraft_commander/structures.py`.
- **Map targets:** a small named registry with Korean/English alias
  resolution and integer tile positions in `toycraft_commander/map.py`.

### Phase 0 Boundaries

- **Interpreter** (`toycraft_commander/interpreter.py`): `CommandInterpreter`
  maps Korean text to typed Intent DSL payloads; unsupported, malformed, or
  ambiguous text returns a clarification result with no payload, so rejected
  commands never reach validation or execution.
- **Feasibility** (`toycraft_commander/feasibility.py`):
  `ToyCraftFeasibilityValidator` gates payloads against an immutable
  `ToyCraftState` snapshot — resources, supply, prerequisites, producers,
  workers, unit groups, targets, and conflicting constraints.
- **Executor / rule engine** (`toycraft_commander/executor.py`,
  `tactical_controller.py`): `ToyCraftExecutor` delegates to the
  deterministic ToyCraft rule engine for offline tests; handlers cover all
  10 intents including resource ticks, build/train queues with reserved
  supply, and deterministic defend/harass combat.
- **Pipeline** (`toycraft_commander/pipeline.py`):
  `CommandProcessingPipeline` is the thin coordinator UI/CLI/voice adapters
  call first; parser exceptions become clarification responses and executor
  exceptions become rule-execution failures without mutating state.
- **Narrator** (`toycraft_commander/narrator.py`): `KoreanStateNarrator`
  renders execution results and rejected outcomes into commander-facing
  Korean, with typed metadata and mandatory blocked-command reports (reason,
  alternative, reason codes). Blocked commands are never narrated as
  success.

The full interface details are in [docs/contracts.md](docs/contracts.md);
the README intentionally does not duplicate them.

### Phase 0 Korean Demo

```bash
python -m toycraft_commander.demo
```

A 5-7 minute scripted Korean walkthrough: prints the typed Intent DSL,
validates and executes feasible ToyCraft state changes, advances
deterministic production/build timers, and narrates each result.
