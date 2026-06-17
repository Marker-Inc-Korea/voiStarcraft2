# Roadmap: LLM Interpreter + Web GUI + Event Memory

Continuation checkpoint for this work phase. If a session dies mid-way, the
next agent resumes from here: each item below is updated as it completes, and
each milestone is committed to git so `git log` + this file are the source of
truth. Start by running `python3 -m pytest -q` and `git status --short`.

## Goal (user directive, 2026-06-13)

Complete ALL remaining stages of the original product plan
(docs/claude-handoff.md): the user commands in free-form Korean (text or
voice), the system interprets it and maps it to StarCraft II API actions.

1. **LLM-based interpretation** — free-form Korean → typed Intent DSL via a
   required provider LLM. The deterministic rule interpreter is deprecated for
   production/live command understanding and remains only for explicit
   `--no-llm` offline compatibility tests.
2. **Web GUI** — human-viewable interface: command input, narration log with
   statuses, live commander-state panel. Works in dry-run and live mode.
3. **Event memory** — the original flow's final stage ("narrator / event
   memory"): record command outcomes for the GUI history and future context.

## Environment facts (verified 2026-06-13)

- Python 3.10.11; `anthropic` NOT installed; `fastapi` NOT installed;
  `ANTHROPIC_API_KEY` NOT set. faster-whisper installed; sounddevice not.
- Therefore: LLM module must lazy-import `anthropic` (new optional extra
  `llm`), accept an injected client for tests, and degrade to the existing
  clarification path when unavailable. GUI must be stdlib-only
  (`http.server`), bound to 127.0.0.1.
- Baseline suite at start of this phase: 636 passed, 1475 subtests.
- Final offline suite after this phase: 811 passed, 1757 subtests.

## Follow-up verification (2026-06-14)

- `[llm]` now includes both OpenAI and Anthropic SDK support.
- Live GUI defaults to OpenAI/GPT (`gpt-4.1-mini`) and accepts API keys through
  the localhost web UI without writing them to disk.
- Final offline suite after local web-key and Korean alias work: 820 passed,
  4 skipped, 1763 subtests.

## Work items and status

- [x] **W1. LLM interpreter** (`starcraft_commander/llm_interpreter.py` +
  `tests/test_llm_interpreter.py` + `pyproject.toml` llm extra +
  `runtime_deps.require_anthropic` / `runtime_deps.require_openai`)
  - `LLMCommandInterpreter` implements the existing
    `CommandInterpreterInterface` seam (interpret(text) →
    `CommandInterpretationResult`). Forced tool-use against a JSON schema
    derived from the 10-intent `INTENT_SCHEMAS`; output validated through
    `validate_intent_payload` and typed payload construction — the LLM can
    never inject an out-of-vocabulary command.
  - `HybridCommandInterpreter`: configured sessions are LLM-first and
    LLM-mandatory; LLM failure returns Korean clarification instead of falling
    back to deterministic keyword matching.
  - OpenAI/GPT is the default local GUI provider (`gpt-4.1-mini`), with
    Anthropic still supported. Live startup requires the selected key in local
    env (`OPENAI_API_KEY` or `ANTHROPIC_API_KEY`); the localhost web GUI can
    rotate the running process key, and web-entered keys stay in process
    memory.
- [x] **W2. Event memory** (`starcraft_commander/event_memory.py` +
  `tests/test_event_memory.py`) — thread-safe ring buffer of command
  outcomes with game time; feeds GUI history.
- [x] **W3. Web GUI** (`starcraft_commander/web_gui.py` +
  `tests/test_web_gui.py`) — stdlib ThreadingHTTPServer, embedded Korean
  single-page UI; endpoints `GET /` (HTML), `GET /api/state`,
  `GET /api/history?after=N`, `GET/POST /api/llm`, `POST /api/command`;
  command POST enqueues, UI polls history (no cross-loop futures). 127.0.0.1
  by default.
- [x] **W4. Integration** — `live_pipeline.py` records outcomes into event
  memory; `demo_sc2.py` gains `--llm` and `--gui [PORT]` flags (work in
  dry-run AND live mode); package exports; full suite green.
- [x] **W5. Docs** — README, docs/sc2-smoke-test.md, claude-handoff.md,
  architecture.md, contracts.md updated; this file's checkboxes ticked.
- [x] **W6. Adversarial review + fixes** — lenses: contract honesty, web
  server security (localhost binding, input handling), LLM output safety
  (schema enforcement, prompt injection via game text), UX.
- [x] **W7. Final verification + semantic commits + push**

## Extended scope (user directive: fill EVERY unfilled original-plan item)

- [x] **W8. Standing orders / tactical policies**
  (`starcraft_commander/standing_orders.py` + tests) — the original plan's
  `keep_worker_production` / `prevent_supply_block` semantics as in-game-loop
  code policies (never LLM-per-frame): continuous SCV production while
  active, automatic Supply Depot when supply nearly blocked. Wired into the
  live bot's `on_step` and dry-run; narrator discloses activation honestly
  (replaced the previous "지속 생산은 아직 지원되지 않아" disclosure).
- [x] **W9. Original-plan audit** — re-verify the ENTIRE original plan
  (including the pre-session handoff text via
  `git show f990509:docs/claude-handoff.md`) against current code; any
  remaining unfilled item becomes a work item in this phase.
- [x] **W10. Brood War / BWAPI executor boundary**
  (`broodwar_commander/` package + tests) — mirrors the SC2 boundary at the
  pre-adapter level: Brood War Terran vocabulary (Vulture maps DIRECTLY, no
  Hellion stand-in), Intent DSL → semantic BW command plans, duck-typed
  runtime executor with fake-based tests. Honest limitation: a real BWAPI
  binding adapter (the python_sc2_adapter equivalent) still requires a
  Brood War + BWAPI environment, which does not exist here.

## Explicitly deferred (cannot be done in this environment)

- **Real-game smoke tests** — require machines with StarCraft II
  (see docs/sc2-smoke-test.md) / Brood War + BWAPI installed.

## Resume instructions for the next agent

1. `git log --oneline -15` — inspect the final commit that landed this phase.
2. `python3 -m pytest -q` — expected offline result: 811 passed, 1757 subtests.
3. Run the dry-run transcript command from README to verify user-facing output.
4. Hard contracts that must survive: package imports clean with ZERO optional
   deps installed; never report skipped/partial work as success; rejected
   commands never mutate state and always carry Korean 이유+대안; no mouse
   automation; LLM is never called per game frame — only per user utterance.
