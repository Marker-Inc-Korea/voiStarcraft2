# StarCraft II Local Smoke Test Guide

This guide documents how a developer with StarCraft II installed runs the
live commander demo locally (handoff Step 6). The same path is referenced by
the `MissingSC2RuntimeError` install hint and `pyproject.toml`.

말하면 스타가 움직인다: 이 문서는 실제 StarCraft II 게임에서 한국어 음성/텍스트
명령 데모를 실행하는 로컬 절차를 설명합니다.

**Honest status up front:** live mode has been smoke-tested on macOS against
the local install at `/Users/jinminseong/Desktop/StarCraft2/StarCraft II` with
`AcropolisLE`. The smoke covered game launch, `Status.in_game`, localhost GUI
state polling, process-local OpenAI key configuration status, SCV production,
SCV scouting, mineral gathering, and Supply Depot construction.

## 1. Requirements

### Python version

- Python **3.10 or newer** (`requires-python = ">=3.10"` in `pyproject.toml`).

### StarCraft II installation

- macOS / Windows: install StarCraft II through the **Battle.net launcher**.
  The free Starter Edition is sufficient for local custom games against the
  built-in AI.
- Linux: use the headless Blizzard StarCraft II Linux package
  (https://github.com/Blizzard/s2client-proto#linux-packages).
- Default install locations that python-sc2 (burnysc2) auto-detects:
  - macOS: `/Applications/StarCraft II`
  - Windows: `C:\Program Files (x86)\StarCraft II`
  - Linux: `~/StarCraftII`
- If your install lives elsewhere, set the `SC2PATH` environment variable to
  the install root before running the demo:

  ```bash
  export SC2PATH="/path/to/StarCraft II"
  ```

### Required maps

- Download a ladder map pack from the official Blizzard map repository
  (https://github.com/Blizzard/s2client-proto#downloads).
- Place the `.SC2Map` files in the `Maps` folder of the StarCraft II install
  root (create the folder if missing), for example
  `/Applications/StarCraft II/Maps/AcropolisLE.SC2Map`.
- The demo defaults to `AcropolisLE`; pass `--map <name>` for any other
  installed map (the name is the file name without the `.SC2Map` suffix).

### Python package install

```bash
pip install 'voiStarcraft2[sc2]'
# or, equivalently:
pip install burnysc2
```

`burnysc2` is the maintained python-sc2 fork providing the importable `sc2`
package. Optional voice input additionally needs:

```bash
pip install 'voiStarcraft2[voice]'
# or: pip install faster-whisper sounddevice
```

Live mode also requires `[llm]`:

```bash
pip install 'voiStarcraft2[llm]'
# installs OpenAI and Anthropic SDK support
```

## 2. How to run

### Dry-run first (no StarCraft II needed)

Always verify the offline pipeline before launching a game:

```bash
python3 -m pytest -q
python3 -m starcraft_commander.demo_sc2 --dry-run --script "마린 6기 입구로 보내고 SCV 계속 찍어" "상황 보고해줘"
```

Real output of the dry-run command (verified; Intent DSL JSON blocks elided
here — the full transcript is in the README Quickstart):

```text
StarCraft II Commander 데모 (dry-run)
가짜 BotAI 상태로 실제 파이프라인을 실행합니다: 해석 -> 검증 -> 계획 -> 실행 -> 내레이션.

명령> 마린 6기 입구로 보내고 SCV 계속 찍어
명령: 마린 6기 입구로 보내
...
[executed] 명령을 실행했습니다. 마린 6기 그룹이 본진 입구로 공격 이동.
명령: SCV 계속 찍어
...
[executed] 명령을 실행했습니다. SCV 1기 생산 명령. 상비 명령 등록: 지속 SCV 생산.

명령> 상황 보고해줘
...
[read_only] 전장 상태를 확인했습니다. 미네랄 400, 가스 0. 보급 20/21 (여유 1). 일꾼 12기 (유휴 2기). 병력: 마린 6기. 건물: 완성 사령부 1동. 발견된 적 없음.
상비 명령: 지속 SCV 생산 활성
최근 명령 2건:
- #1 [executed] 명령을 실행했습니다. 마린 6기 그룹이 본진 입구로 공격 이동.
- #2 [executed] 명령을 실행했습니다. SCV 1기 생산 명령. 상비 명령 등록: 지속 SCV 생산.
```

### Live custom game

Start the live game (launches StarCraft II in a local custom game against
the built-in AI, realtime, Terran MVP):

```bash
python3 -m starcraft_commander.demo_sc2 --map AcropolisLE --difficulty easy
```

- Type Korean commands at the `명령>` prompt while the game runs
  (예: `SCV 계속 찍어`, `보급고 지어`, `마린 6기 입구로 보내`, `상태 알려줘`).
- Commands are placed on an asyncio queue and drained inside the bot's
  `on_step` callback, so each command executes on the next game-loop
  iteration, not instantly at the prompt.
- Exit command input with `종료`, `quit`, or EOF; the game keeps running.

For local browser control of the legacy python-sc2 commander:

```bash
SC2PATH="/Users/jinminseong/Desktop/StarCraft2/StarCraft II" \
OPENAI_API_KEY="sk-..." \
python3 -m starcraft_commander.demo_sc2 \
  --map AcropolisLE --difficulty easy \
  --gui
```

- Open the printed `http://127.0.0.1:PORT` URL.
- Live mode fails before StarCraft II starts unless the selected provider key
  is already available through `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`.
- The **LLM 설정** panel can rotate the running process key after startup. The
  key stays in process memory only; `/api/llm` returns status metadata, never
  the key.
- Defaults: `--llm-provider openai`, `--llm-model gpt-4.1-mini`.
- Use windowed/borderless StarCraft II or a second monitor so the browser
  remains usable while the game is running.
- This path is **not** MicroMachine QA evidence. In the web page, the
  **Legacy python-sc2 commander** mode is the route that posts to
  `/api/command`; the default MicroMachine cockpit posts text/voice intent to
  `/api/micromachine/modulate` and a MicroMachine blackboard instead. The same
  web page has a mode-aware **선택 모드 실행** control: in MicroMachine mode it
  calls `/api/runtime/start` and runs the patched MicroMachine smoke/live script
  with the current blackboard directory; in legacy mode it starts the older
  python-sc2 demo only after a key has been saved.

### Expected behavior (smoke-test acceptance)

Mirrors the MVP completion definition in `docs/claude-handoff.md`:

1. The game launches and the bot loads without errors.
2. `상태 알려줘` or `상태확인` narrates live minerals/supply/army state in Korean.
3. `SCV 계속 찍어` issues a real train order; `SCV 여러개 뽑아` queues 3 SCVs
   when resources and producer availability allow it.
4. `보급고 지어`, `보급고 건설해`, or `SCV로 보급고 설치해` places a Supply
   Depot near the ramp.
5. `정찰보내` moves one SCV toward the enemy front.
6. `자원채취` assigns three workers to minerals.
7. `마린 6기 입구로 보내` attack-moves Marines to your ramp, narrating the
   honest issued count when fewer than six exist.

## 3. Voice mode

Push-to-talk Korean voice input works in both dry-run and live mode:

```bash
python3 -m starcraft_commander.demo_sc2 --dry-run --voice
python3 -m starcraft_commander.demo_sc2 --voice --map AcropolisLE --difficulty easy
```

- Requires the `[voice]` extras (`faster-whisper`, `sounddevice`); missing
  dependencies fail fast with bilingual install hints before the game
  launches.
- Recording is a fixed window per utterance (default 5 seconds; change with
  `--record-seconds`).
- Transcription uses faster-whisper with model size `small` and language
  `ko`. **The model weights download automatically on first use**, so the
  first voice command needs network access and takes noticeably longer.
- macOS: grant microphone permission to the terminal app you run the demo
  from (System Settings -> Privacy & Security -> Microphone). Without it,
  capture returns silence or fails.

## 4. Troubleshooting

### `MissingSC2RuntimeError` / `import sc2` fails

The optional python-sc2 runtime is not installed. The error message itself
carries the fix:

```bash
pip install 'voiStarcraft2[sc2]'   # or: pip install burnysc2
```

Note the importable package is named `sc2` but the pip distribution is
`burnysc2`. Do **not** `pip install sc2` (that is an unrelated package).

### Game fails to launch / install not found

python-sc2 could not locate the StarCraft II install. Set `SC2PATH` to the
install root (see section 1) and retry. On macOS, launch StarCraft II once
through Battle.net first so the install is fully initialized.

### Map not found

`maps.get(<name>)` raises an error naming the missing map when there is no
matching `.SC2Map` file. Check that:

- the file is inside the `Maps` folder of the install root (or `SC2PATH`),
- the `--map` value matches the file name without the `.SC2Map` suffix,
  with exact spelling (e.g. `AcropolisLE`, not `Acropolis LE`).

### `MissingVoiceDependencyError`

faster-whisper or sounddevice is absent. Install with
`pip install 'voiStarcraft2[voice]'`.

### No audio device / PortAudio error

`sounddevice` raises a PortAudio error when no input device is available or
microphone permission is denied. Check your OS input device settings and
(macOS) terminal microphone permission, or fall back to text input by
dropping `--voice`.

### Whisper transcribes nonsense on silence

Known Whisper behavior: silence or background noise can hallucinate text.
Unrecognized commands come back as Korean clarification prompts instead of
executing, so the failure is safe — just re-record.

## 5. Known limitations

Be aware of exactly how small this MVP is:

- **Legacy python-sc2 live mode has only one local smoke environment so far.** The current
  verified setup is macOS + `/Users/jinminseong/Desktop/StarCraft2/StarCraft II`
  + `AcropolisLE`; other OSes, maps, and ladder scenarios can still expose
  python-sc2 or map-derivation edge cases.
- **Terran only**, one fixed map per run, local built-in AI opponent
  (`--race terran` is the only accepted value).
- **Exactly 10 intents.** Rule interpretation and LLM output are both gated to
  the inventory in `docs/intent-inventory.md`; the LLM can run only per user
  utterance, never per frame.
- **One intent per utterance, with a splitting heuristic.** Compound
  commands are split on `그리고`/`하고` and on curated sequential verb
  endings (`...보내고 ...찍어`). The heuristic is conservative (nouns like
  보급고 are protected) but it is still a heuristic: unusual phrasing may
  not split, or may split wrong, and each part is then interpreted as a
  single intent.
- **Best-effort map derivation.** `enemy_ramp` is approximated as the ramp
  top closest to the enemy main, and mineral lines as the centroid of
  mineral fields within a fixed radius of the base. Targets that cannot be
  derived from live map data are reported as explicitly unavailable rather
  than guessed — on some maps some targets will simply be unavailable.
- **Actions are issued from the `on_step` queue.** Commands typed at the
  prompt wait for the next bot step before execution; this adds up to one
  game-loop iteration of latency and means nothing executes while the game
  is paused.
- Continuous-production constraints (`계속 찍어`) are not enforced by the
  runtime: exactly one production order is issued per command and the
  narrator discloses this as a partial execution.
- Gas economy requires explicit steps: build a Refinery first
  (`정제소 지어`), then assign workers to gas; gas gathering without a
  completed Refinery is rejected with a Korean reason.
- The validator is conservative: unknown or incomplete observation state
  rejects mutating commands instead of guessing.
- Voice capture is push-to-talk with a fixed recording window; Whisper may
  hallucinate text on silence, and unrecognized commands come back as
  clarification prompts.
