# Claude Code Handoff: voiStarcraft2

This document is the continuation brief for Claude Code or another coding agent.
It captures the original product intent, the current repository state, what is
done, what is not done, and the next implementation sequence.

## TLDR

The product is **real StarCraft II natural-language control**, not ToyCraft.
ToyCraft exists only as an offline deterministic harness for parser, validator,
rule-engine, and narration tests.

Handoff Steps 1-6 are now DONE. The full live pipeline exists: Korean text (or
push-to-talk voice) -> interpreter -> live feasibility validator -> semantic SC2
planner -> runtime executor -> `PythonSC2BotAdapter` -> python-sc2 `BotAI` ->
Korean narration. The dry-run demo executes the MVP compound command end to end
against a scripted fake BotAI. What has **not** happened yet is a real-game
smoke test against an installed StarCraft II (the procedure is documented in
`docs/sc2-smoke-test.md` but has not been executed on a machine with SC2).

## Original Product Positioning

voiStarcraft2 should let a human command StarCraft by natural language:

```text
User text or voice
  -> command interpreter
  -> typed Intent DSL
  -> game-state resolver
  -> validator
  -> tactical controller
  -> StarCraft II API executor
  -> game
  -> narrator / event memory
```

Core rule:

```text
Do not emulate mouse clicks.
Translate natural-language commands into semantic game actions and execute them
through StarCraft APIs.
```

The user-facing message is:

```text
말하면 스타가 움직인다.
```

## Current Repository State

Latest pushed commits at the time of this handoff:

```text
f990509 docs: add Claude Code handoff plan
6a9d2d9 feat: harden StarCraft II executor contracts
a6e5d32 feat: add real StarCraft II executor boundary
279b2dd feat: complete ToyCraft commander pipeline
107d3c9 feat: add ToyCraft commander MVP
```

The working tree additionally contains the (uncommitted) LLM/GUI/event-memory,
standing-order, and Brood War boundary integration described below.

Validation status:

```bash
python3 -m pytest -q
```

Expected current result:

```text
811 passed, 1757 subtests passed
```

## Implemented Components

### Intent and Korean Command Layer

Location:

```text
toycraft_commander/intents.py
toycraft_commander/interpreter.py
toycraft_commander/aliases.py
```

Implemented:

- 10 canonical intents.
- Korean free-form phrase mapping to typed Intent DSL.
- Reject/clarification behavior for unsupported or ambiguous text.
- Stable JSON DSL serialization.

Current canonical intents:

```text
GATHER_RESOURCE
BUILD_STRUCTURE
TRAIN_WORKER
TRAIN_ARMY
SCOUT
SUMMARIZE_STATE
DEFEND
REPAIR
EXPAND
HARASS
```

Important naming note:

The original planning language used phrases such as `keep_worker_production`,
`prevent_supply_block`, `defend_ramp`, and `harass_mineral_line`. The current
code normalized these into the uppercase canonical intent names above. Preserve
backward-compatible tests if changing the names.

### ToyCraft Offline Harness

Location:

```text
toycraft_commander/
```

Implemented:

- Toy resources, supply, units, structures, map aliases, feasibility validation,
  rule execution, tactical controller, pipeline, and Korean narration.
- Deterministic tests for parser, validator, execution, narration, and demo flow.

Important boundary:

ToyCraft is not the product runtime. Do not spend major effort making ToyCraft
more game-realistic unless it directly supports SC2 integration tests. The one
legitimate toycraft import inside `starcraft_commander` is the Korean
interpreter reused by `live_pipeline.py`.

### Real StarCraft II Boundary

Location:

```text
starcraft_commander/contracts.py
starcraft_commander/sc2_executor.py
```

Implemented:

- Stable semantic SC2 action type contract.
- Intent DSL to SC2 command-plan mapping.
- JSON-ready plan and result contracts, including per-action `SC2ActionReport`
  (requested vs issued order counts; `bool(report)` is true only for a full,
  shortfall-free application).
- Async `SC2RuntimeExecutor` with lifecycle methods.
- Fake BotAI-style execution tests.
- Lazy package imports so tests do not require StarCraft II or `python-sc2`
  (`starcraft_commander/__init__.py` loads everything except the stdlib-only
  contracts on first attribute access).

Stable public SC2 action types:

```text
assign_workers
build_structure
train_unit
move_group
attack_move
repair
observe
```

Current contract:

- `SC2ActionPlanner.build_plan(payload)` creates an `SC2ExecutionPlan`.
- `SC2RuntimeExecutor.start(bot)` binds a BotAI-like runtime.
- `SC2RuntimeExecutor.execute(plan)` applies ordered actions.
- `SC2RuntimeExecutor.close()` ends lifecycle.
- Missing runtime capability skips the action and returns `success == false`.
- Runtime exceptions are captured as structured errors.

## Completed Handoff Steps

### Step 1. Real SC2 Runtime Dependency Surface — DONE

Files:

```text
pyproject.toml                          # optional extras: sc2=[burnysc2>=6.5], voice=[faster-whisper, sounddevice], dev=[pytest]
starcraft_commander/runtime_deps.py     # require_python_sc2 / require_faster_whisper / require_sounddevice guards
tests/test_runtime_deps.py
```

The core package has zero runtime dependencies. Every optional runtime is
guarded by an `is_*_available()` probe and a `require_*()` call that raises an
actionable bilingual error (`MissingSC2RuntimeError`,
`MissingVoiceDependencyError`) pointing at the install command and
`docs/sc2-smoke-test.md`.

### Step 2. BotAI Adapter Methods — DONE

Files:

```text
starcraft_commander/python_sc2_adapter.py   # PythonSC2BotAdapter, SC2BotAdapterInterface
tests/test_python_sc2_adapter_contract.py   # contract tests against pure-Python fakes
```

`PythonSC2BotAdapter` implements exactly the seven semantic action method names
the executor dispatches to (`assign_workers`, `build_structure`, `train_unit`,
`move_group`, `attack_move`, `repair`, `observe`) and translates them into
duck-typed BotAI operations. It deliberately defines none of the executor
lifecycle hook names (`start`, `close`, `stop`, `on_start`, `on_end`) so they
can never collide with python-sc2 `BotAI` lifecycle semantics. Counted methods
return `SC2ActionReport` so partial issuance is never collapsed into a boolean
success; `observe` returns a JSON-ready mapping.

### Step 3. SC2 State Resolver — DONE

Files:

```text
starcraft_commander/state_resolver.py   # SC2CommanderState, SC2StateResolver
tests/test_sc2_state_resolver.py
```

`SC2StateResolver.resolve(bot)` duck-types a BotAI-like object via `getattr`
only and never raises: numeric fields degrade to 0, count mappings degrade to
empty, and every missing/unreadable attribute is recorded in
`SC2CommanderState.observation_notes` so the validator can stay conservative
(`observation_complete` is false). Counts are keyed by UPPERCASE space-free
type names (`SCV`, `MARINE`, `COMMANDCENTER`).

### Step 4. Map Resolver — DONE

Files:

```text
starcraft_commander/map_resolver.py   # SC2MapResolver, MapPoint, MapTargetResolution
tests/test_sc2_map_resolver.py
```

Resolves the seven Step 4 semantic targets (`self_main`, `self_ramp`,
`self_natural`, `enemy_main`, `enemy_ramp`, `enemy_natural`,
`enemy_mineral_line`) plus two best-effort extras (`self_mineral_line`,
`self_geyser`) from BotAI map data. Underivable targets become explicit
unavailable entries with reasons; unknown names are rejected with the list of
currently available alternatives. `SC2MapResolver.from_bot` never raises.

### Step 5. Live Command Pipeline + Demo — DONE

Files:

```text
starcraft_commander/feasibility.py      # SC2FeasibilityValidator against SC2CommanderState
starcraft_commander/narrator.py         # SC2KoreanNarrator, SC2NarrationResponse
starcraft_commander/live_pipeline.py    # SC2CommandSession, SC2CommandOutcome, split_compound_command
starcraft_commander/voice_input.py      # MicrophoneListener, FasterWhisperTranscriber
starcraft_commander/demo_sc2.py         # python -m starcraft_commander.demo_sc2
tests/test_sc2_feasibility.py
tests/test_sc2_narrator.py
tests/test_live_pipeline.py
tests/test_voice_input.py
tests/test_demo_sc2.py
```

The MVP compound command now executes in dry-run mode:

```bash
python3 -m starcraft_commander.demo_sc2 --dry-run --script "마린 6기 입구로 보내고 SCV 계속 찍어"
```

This splits into two parts via `split_compound_command`: the DEFEND part
attack-moves the Marines to `self_ramp` (`executed`), and the TRAIN_WORKER part
issues one SCV train order while registering the deterministic
`keep_worker_production` standing order. Live mode (the default, no
`--dry-run`) lazily imports `sc2` and launches a local custom game; `--voice`
enables push-to-talk capture with a transcription confidence gate
(low-confidence Whisper output is re-prompted, never executed).

### Step 7. LLM Interpreter, GUI, Event Memory, Standing Orders, BW Boundary — DONE

Files:

```text
starcraft_commander/llm_interpreter.py
starcraft_commander/event_memory.py
starcraft_commander/web_gui.py
starcraft_commander/standing_orders.py
broodwar_commander/
tests/test_llm_interpreter.py
tests/test_event_memory.py
tests/test_web_gui.py
tests/test_standing_orders.py
tests/test_bw_executor.py
docs/roadmap-llm-gui.md
```

Implemented:

- LLM-mandatory command interpretation via the optional `[llm]` extra,
  schema-gated to the 10 canonical intents and called only once per user
  utterance. Deprecated deterministic rule/keyword matching is retained only
  for explicit offline `--no-llm` compatibility. OpenAI/GPT is the default
  local GUI provider; Anthropic remains supported.
- Thread-safe bounded event memory for GUI history and state-report
  enrichment.
- Stdlib localhost-first web GUI for dry-run and live sessions, including
  process-local API key configuration via `GET/POST /api/llm`.
- Code-driven standing orders for continuous SCV production and supply-block
  prevention, ticked from `on_step` rather than the LLM.
- Brood War semantic executor boundary with BWAPI Terran vocabulary and
  fake-based tests. A real BWAPI binding adapter remains environment-bound.

### Step 6. Local Smoke Test Instructions — DONE

`docs/sc2-smoke-test.md` documents the SC2 install paths (`SC2PATH`), map
download/placement, `pip install 'voiStarcraft2[sc2]'` / `[voice]`, the dry-run
and live commands, the five-step smoke-test acceptance list, and known
limitations. A local macOS smoke run has reached `Status.in_game` and verified
localhost GUI state polling plus command execution for state check, SCV
production, scouting, mineral gathering, and Supply Depot construction.

## Hardening Decisions From This Session

Record of contract decisions the next agent must preserve:

- **MissingBotCapability structured errors.** When the bound runtime implements
  neither the action's method name nor `execute_commander_action`, the executor
  records an `SC2ExecutionError` with `exception_type="MissingBotCapability"`
  and `metadata={"expected_method": ...}`, skips the action, and the result is
  not a success. No silent skipping.
- **Strict target validation.** The planner rejects unknown location targets
  with a `ValueError` listing every supported alias and semantic target name
  (`SC2_TARGET_ALIASES` + `SC2_SEMANTIC_TARGET_NAMES`); unknown targets are
  never passed through to execution. The live pipeline converts that refusal
  into a blocked Korean narration.
- **Strict priority validation.** `SC2ExecutionPlan` rejects unknown priority
  labels with the supported list instead of silently defaulting to `normal`.
- **Lifecycle error reset per start cycle.** `SC2RuntimeExecutor.start()`
  clears errors captured by a previous cycle's hooks; captured lifecycle errors
  are drained into exactly one (the next) execution result so a transient hook
  failure cannot poison every later result.
- **Observation channel.** `observe` results are JSON-ready mappings stored
  under `result.audit["observations"]` keyed by action index; per-action
  adapter reports live under `result.audit["action_reports"]`. The narrator
  reads observations from the audit channel for `SUMMARIZE_STATE`.
- **Voice boundary.** Voice input is an isolated seam
  (`starcraft_commander/voice_input.py`) that only produces plain text for the
  unchanged interpreter pipeline. Optional dependencies are imported lazily
  inside methods; missing dependencies raise `MissingVoiceDependencyError` with
  install hints. The demo gates transcriptions below confidence 0.5 so Whisper
  hallucinations never reach execution.
- **Honest partial execution.** A plan result with any skipped action or
  partial `SC2ActionReport` is narrated as `partially_executed` or `blocked`,
  never `executed`. Unenforced plan constraints (continuous production) are
  disclosed in the narration.

## What Is Not Done Yet

The project compiles, tests, and dry-runs the full pipeline, but:

- **Broader real-game smoke tests.** One macOS local SC2 smoke run is verified;
  repeat `docs/sc2-smoke-test.md` on other machines, maps, and OSes because
  python-sc2 API and map-derivation edge cases can still differ from the local
  setup.
- **Real BWAPI adapter.** The Brood War semantic executor boundary exists, but
  a live BWAPI binding adapter still requires a Brood War + BWAPI machine.
- **Broader live LLM API verification.** Web key configuration has been
  verified locally without exposing the key; broader real model checks across
  OpenAI and Anthropic still depend on the user's available API credentials.
- **Multi-race.** Terran-only MVP: costs, producers, and Korean vocabulary
  cover SCV/Marine/Hellion and the basic Terran structures.

## Known Limitations (open low-severity review findings)

Found in review this session; none block the dry-run path, but fix or verify
during real-game work:

- The live demo's stdin reader task is never cancelled; the blocked `input()`
  thread keeps the process alive after the game ends.
- Spoken `종료` cannot exit the voice loop — exit words only work when typed.
- Free-text army selection sweeps every non-SCV own unit from `bot.units`,
  including MULEs.
- `BUILD_STRUCTURE` of `Command Center` always reroutes to `expand_now`,
  ignoring the requested location.
- `enemy_main` uses `enemy_start_locations[0]` and freezes the registry; wrong
  on maps with more than one candidate spawn. `enemy_ramp` derivation has no
  distance bound.
- Repair matches under-construction structures where the REPAIR order is
  invalid in-game; the feasibility GATHER/REPAIR branches let unknown gather
  resources and invalid `worker_count` pass (no negative tests).
- `SC2CommandSession.process_text` can raise against a hostile bot object —
  the never-raise state-resolution contract is broken one frame above the
  resolver.
- `state_resolver` stores `bot.supply_army` (a supply total) as `army_count`
  (a unit count).
- Two distinct `MissingVoiceDependencyError` classes exist (`runtime_deps` vs
  `voice_input`); package-level except clauses miss errors raised by the
  `runtime_deps` guards.
- `MicrophoneListener` sample rate is not tied to Whisper's required 16 kHz;
  multi-channel flatten interleaves frames. Voice loop lacks recording-state
  cues (no start/stop/recognizing feedback).
- Rejection narration can emit double periods and ungrammatical joins
  ("...필요합니다.. 대안:").
- Two hand-maintained semantic-target registries (`sc2_executor` and
  `map_resolver`) have no cross-consistency test.
- The executor return-value semantics test cements over-permissive behavior;
  fakes never exercise `None`/falsy unit-order returns the real python-sc2
  runtime produces.

## MVP Completion Definition

The MVP is complete only when this real-game demo works locally:

```text
1. Start local SC2 custom game or python-sc2 bot match.
2. System reads current state.
3. User enters Korean command.
4. Command becomes Intent DSL.
5. Validator checks live state.
6. Executor issues real SC2 API commands.
7. In-game units/buildings actually change behavior.
8. Narrator explains the result.
```

The first real-game success criterion should be small:

```text
Terran
one fixed map
local AI opponent
text input
SCV production
Marine production
Supply Depot or Barracks build
Marine defend ramp / attack_move
state summary narration
```

All of this works in dry-run today; only the real-game run remains unverified.

## Known Risks

- `python-sc2` API details may differ from the duck-typed fake interface; the
  real-game smoke test is where this surfaces.
- SC2 local install and map paths are environment-sensitive (`SC2PATH`).
- Live validators must stay conservative: reject if state is unknown.
- LLM should not be called per-frame; keep fast micro in code policies.
- Do not bind the public DSL to python-sc2 method names.

## Commands For Next Agent

Start with:

```bash
git status --short --branch
python3 -m pytest -q          # expect 811 passed, 1757 subtests passed
python3 -m starcraft_commander.demo_sc2 --dry-run --script "마린 6기 입구로 보내고 SCV 계속 찍어" "상황 보고해줘"
sed -n '1,130p' starcraft_commander/__init__.py
sed -n '1,110p' docs/sc2-smoke-test.md
```

Then, on a machine with StarCraft II installed:

```bash
pip install 'voiStarcraft2[sc2]'
python3 -m starcraft_commander.demo_sc2 --map AcropolisLE --difficulty easy
```

and work through the smoke-test acceptance list in `docs/sc2-smoke-test.md`,
fixing the known limitations above as the real runtime exposes them.

## Do Not Do

- Do not present ToyCraft as the final product.
- Do not replace SC2 API execution with mouse-click automation.
- Do not require StarCraft II installation for normal unit tests.
- Do not hide unsupported live commands as successful results.
- Do not make `success == true` if any planned action was skipped.
