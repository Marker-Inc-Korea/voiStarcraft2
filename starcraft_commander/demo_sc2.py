"""Runnable StarCraft II commander demo: ``python -m starcraft_commander.demo_sc2``.

"말하면 스타가 움직인다." This is the handoff Step 5 demo entrypoint wiring
text (or voice) commands through the live pipeline:

```
text/voice -> interpreter -> live validator -> planner -> adapter -> narration
```

Two modes exist:

- ``--dry-run`` exercises the full real pipeline against a built-in scripted
  :class:`DemoFakeBotAI` (a plausible early Terran state), so the demo runs
  without StarCraft II, python-sc2, or audio hardware. This is the testable
  path.
- Live mode (the default) requires the optional python-sc2 runtime plus a
  local StarCraft II installation. All ``sc2`` imports happen lazily inside
  :func:`run_live`, so importing this module never needs python-sc2 and a
  missing runtime raises the actionable bilingual
  :class:`~starcraft_commander.runtime_deps.MissingSC2RuntimeError`.

``--voice`` switches the input loop to push-to-talk microphone capture via
``MicrophoneListener`` + ``FasterWhisperTranscriber``; missing voice
dependencies raise the voice guard's actionable error.

Live mode always uses an LLM-mandatory interpreter. Deprecated deterministic
rule/keyword matching is kept only for explicit offline ``--no-llm`` tests and
never rescues a configured LLM failure. A missing provider SDK or API key fails
fast with the actionable bilingual hint BEFORE any command loop starts.
``--gui [PORT]`` additionally serves the local
web GUI: in dry-run mode the GUI bridge owns the session (scripted commands
run through the same bridge), and in live mode the GUI submits into the same
``on_step`` command queue the terminal reader feeds while history reads the
shared :class:`CommanderEventMemory`.

Both dry-run and live sessions wire a :class:`CommanderEventMemory` (every
outcome recorded with game time) and a :class:`StandingOrderController`
(continuous SCV production / supply-block guard, ticked from ``on_step``
every :data:`STANDING_ORDER_TICK_INTERVAL_STEPS` steps in live mode).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections.abc import Mapping, Sequence
from types import SimpleNamespace
from typing import Final

from starcraft_commander.event_memory import CommanderEventMemory
from starcraft_commander.live_pipeline import SC2CommandOutcome, SC2CommandSession
from starcraft_commander.llm_interpreter import (
    ANTHROPIC_API_KEY_ENV_VAR,
    DEFAULT_LLM_PROVIDER,
    DEFAULT_OPENAI_MODEL,
    GEMINI_API_KEY_ENV_VAR,
    GROK_API_KEY_ENV_VAR,
    HybridCommandInterpreter,
    LocalLLMControl,
    OPENAI_API_KEY_ENV_VAR,
)
from starcraft_commander.python_sc2_adapter import PythonSC2BotAdapter
from starcraft_commander.runtime_deps import (
    MissingLLMDependencyError,
    require_anthropic,
    require_openai,
    require_faster_whisper,
    require_python_sc2,
    require_sounddevice,
)
from starcraft_commander.sc2_executor import SC2RuntimeExecutor
from starcraft_commander.standing_orders import (
    STANDING_ORDER_KOREAN_LABELS,
    StandingOrderController,
)
from starcraft_commander.state_resolver import (
    DEFAULT_SC2_STATE_RESOLVER,
    SC2StateResolverInterface,
)
from starcraft_commander.voice_input import (
    FasterWhisperTranscriber,
    MicrophoneListener,
    MissingVoiceDependencyError,
    VoiceTranscriberInterface,
)
from starcraft_commander.web_gui import (
    DEFAULT_WEB_GUI_PORT,
    WEB_GUI_HOST,
    SessionLoopBridge,
    WebGuiServer,
)


DEFAULT_SC2_DEMO_MAP: Final[str] = "AcropolisLE"
"""Common ladder map used by the live demo unless ``--map`` overrides it."""

DEFAULT_VOICE_RECORD_SECONDS: Final[float] = 5.0
"""Default push-to-talk recording window for ``--voice`` mode."""

MIN_VOICE_TRANSCRIPTION_CONFIDENCE: Final[float] = 0.5
"""Transcriptions below this confidence are re-prompted, never executed.

Whisper hallucinations on silence or noise typically come back with low
language probability; forwarding them to the interpreter could trigger real
game commands the commander never spoke.
"""

EXIT_COMMAND_WORDS: Final[frozenset[str]] = frozenset({"종료", "quit"})
"""Interactive loop exit words (besides empty input / EOF)."""

COMMAND_PROMPT: Final[str] = "명령> "
"""Interactive Korean command prompt."""

MVP_DEMO_COMMAND: Final[str] = "마린 6기 입구로 보내고 SCV 계속 찍어"
"""The handoff Step 5 minimum demo command (compound: move + keep training)."""

STANDING_ORDER_TICK_INTERVAL_STEPS: Final[int] = 8
"""Live ``on_step`` iterations between standing-order ticks (code, not LLM)."""

_BRIDGE_DEDUP_WINDOW: Final[int] = 8
"""Recent-event window checked when deduplicating bridge re-records."""

_DRY_RUN_BANNER_LINES: Final[tuple[str, ...]] = (
    "StarCraft II Commander 데모 (dry-run)",
    "가짜 BotAI 상태로 실제 파이프라인을 실행합니다: "
    "해석 -> 검증 -> 계획 -> 실행 -> 내레이션.",
    "",
)


class DemoPoint:
    """Minimal Point2-like coordinate for the scripted fake bot."""

    def __init__(self, x: float, y: float) -> None:
        self.x = float(x)
        self.y = float(y)

    def __repr__(self) -> str:
        return f"DemoPoint({self.x}, {self.y})"


class DemoUnit:
    """Recording unit/structure fake exposing the duck-typed BotAI surface."""

    def __init__(
        self,
        name: str,
        x: float,
        y: float,
        *,
        is_idle: bool = True,
        is_ready: bool = True,
        health: float = 100.0,
        health_max: float = 100.0,
    ) -> None:
        self.name = name
        self.position = DemoPoint(x, y)
        self.is_idle = is_idle
        self.is_ready = is_ready
        self.health = health
        self.health_max = health_max
        self.issued_orders: list[tuple[str, object]] = []

    def __repr__(self) -> str:
        return f"DemoUnit({self.name!r})"

    def _record(self, kind: str, payload: object) -> tuple[str, str, object]:
        self.issued_orders.append((kind, payload))
        return (kind, self.name, payload)

    def gather(self, target: object) -> tuple[str, str, object]:
        return self._record("gather", target)

    def move(self, point: object) -> tuple[str, str, object]:
        return self._record("move", point)

    def attack(self, point: object) -> tuple[str, str, object]:
        return self._record("attack", point)

    def repair(self, target: object) -> tuple[str, str, object]:
        return self._record("repair", target)

    def train(self, type_id: object) -> tuple[str, str, object]:
        return self._record("train", type_id)


class DemoUnitGroup(list):
    """Units-like list with the ``idle``/``ready`` chains the adapter probes."""

    @property
    def idle(self) -> "DemoUnitGroup":
        return DemoUnitGroup(unit for unit in self if getattr(unit, "is_idle", False))

    @property
    def ready(self) -> "DemoUnitGroup":
        return DemoUnitGroup(unit for unit in self if getattr(unit, "is_ready", False))


class DemoFakeBotAI:
    """Scripted BotAI-like fake with a plausible early Terran start state.

    The state is intentionally honest: 400 minerals, supply 20/21, twelve SCVs,
    six Marines, and one Command Center, so the MVP compound demo command
    ("마린 6기 입구로 보내고 SCV 계속 찍어") can actually execute both parts.
    Map attributes (start location, ramps, expansions, mineral fields, geysers)
    are complete so the semantic map resolver derives every target, and the
    observation surface is complete so the live validator sees
    ``observation_complete`` state.
    """

    def __init__(self) -> None:
        # Map geometry for SC2MapResolver.from_bot.
        self.start_location = DemoPoint(30.0, 30.0)
        self.enemy_start_locations = [DemoPoint(130.0, 130.0)]
        self.main_base_ramp = SimpleNamespace(top_center=DemoPoint(38.0, 36.0))
        self.game_info = SimpleNamespace(
            map_ramps=(
                SimpleNamespace(top_center=DemoPoint(38.0, 36.0)),
                SimpleNamespace(top_center=DemoPoint(122.0, 124.0)),
            )
        )
        self.expansion_locations_list = [
            DemoPoint(30.0, 30.0),
            DemoPoint(45.0, 52.0),
            DemoPoint(65.0, 80.0),
            DemoPoint(95.0, 96.0),
            DemoPoint(115.0, 108.0),
            DemoPoint(130.0, 130.0),
        ]
        self.scout_location = DemoPoint(95.0, 96.0)
        self.last_seen_enemy_area = DemoPoint(120.0, 124.0)
        self.mineral_field = DemoUnitGroup(
            (
                DemoUnit("MineralField", 22.0, 28.0),
                DemoUnit("MineralField", 22.0, 32.0),
                DemoUnit("MineralField", 24.0, 26.0),
                DemoUnit("MineralField", 24.0, 34.0),
                DemoUnit("MineralField", 136.0, 128.0),
                DemoUnit("MineralField", 136.0, 132.0),
                DemoUnit("MineralField", 138.0, 126.0),
                DemoUnit("MineralField", 138.0, 134.0),
            )
        )
        self.vespene_geyser = DemoUnitGroup(
            (
                DemoUnit("VespeneGeyser", 36.0, 24.0),
                DemoUnit("VespeneGeyser", 124.0, 136.0),
            )
        )

        # Own forces: 12 SCVs (two idle), 6 Marines, and one finished
        # Command Center. The Marines record move/attack orders so the MVP
        # demo command can execute its ramp-defense part in dry-run mode.
        workers = [
            DemoUnit("SCV", 26.0 + index * 0.5, 28.0, is_idle=index < 2)
            for index in range(12)
        ]
        marines = [
            DemoUnit("Marine", 33.0 + index * 0.5, 31.0) for index in range(6)
        ]
        self.workers = DemoUnitGroup(workers)
        self.units = DemoUnitGroup((*workers, *marines))
        self.structures = DemoUnitGroup((DemoUnit("CommandCenter", 30.0, 30.0),))
        self.enemy_units = DemoUnitGroup()
        self.enemy_structures = DemoUnitGroup()

        # Observation surface for SC2StateResolver (complete on purpose).
        self.minerals = 400
        self.vespene = 0
        self.supply_used = 20
        self.supply_cap = 21
        self.supply_left = 1
        self.supply_army = 6
        self.state = SimpleNamespace(game_loop=448)
        self.time = 20.0

        # Recorders for tests and demo transparency.
        self.issued_commands: list[object] = []
        self.build_calls: list[tuple[object, object]] = []
        self.expand_calls = 0

    def unit_type_id_resolver(self, type_name: str) -> str:
        """Resolve UnitTypeId names offline: the name itself is the id."""

        return type_name

    def can_afford(self, item: object) -> bool:
        """Affordability stays scripted-true; the validator gates real costs."""

        return True

    def do(self, command: object) -> None:
        self.issued_commands.append(command)
        return None

    async def build(self, type_id: object, near: object = None) -> None:
        self.build_calls.append((type_id, near))
        return None

    async def expand_now(self) -> None:
        self.expand_calls += 1
        return None


def build_llm_interpreter(
    provider: str = DEFAULT_LLM_PROVIDER,
    model: str = DEFAULT_OPENAI_MODEL,
) -> HybridCommandInterpreter:
    """Build the LLM-mandatory interpreter for real command sessions.

    The demo's ``--llm`` flag must fail fast before any command loop instead
    of degrading silently mid-session. The selected provider SDK and key are
    validated by :func:`build_local_llm_control`.
    """

    return HybridCommandInterpreter(
        llm_interpreter=build_local_llm_control(provider, model)
    )


def build_local_llm_control(provider: str, model: str) -> LocalLLMControl:
    """Build required process-local LLM control from environment credentials."""

    normalized = provider.strip().lower()
    if normalized in {"openai", "gpt", "chatgpt"}:
        require_openai()
        env_var = OPENAI_API_KEY_ENV_VAR
        normalized = "openai"
    elif normalized == "anthropic":
        require_anthropic()
        env_var = ANTHROPIC_API_KEY_ENV_VAR
    elif normalized in {"gemini", "google", "google-gemini"}:
        require_openai()
        env_var = GEMINI_API_KEY_ENV_VAR
        normalized = "gemini"
    elif normalized in {"grok", "xai", "x-ai", "x.ai"}:
        require_openai()
        env_var = GROK_API_KEY_ENV_VAR
        normalized = "grok"
    else:
        raise MissingLLMDependencyError(
            "LLM provider must be openai, anthropic, gemini, or grok."
        )
    api_key = os.environ.get(env_var, "").strip()
    if not api_key:
        raise MissingLLMDependencyError(
            f"{env_var} is not set. Live StarCraft II control now requires "
            "a configured LLM before the game starts. Export a valid key first: "
            f"export {env_var}=... "
            f"{env_var} 환경 변수가 설정되어 있지 않습니다. 이제 실제 StarCraft II "
            "제어는 LLM 연결이 먼저 필요합니다. 실행 전에 유효한 키를 설정하세요: "
            f"export {env_var}=..."
        )
    control = LocalLLMControl(provider=normalized, model=model)
    control.configure(normalized, api_key, model)
    return control


def build_dry_run_session(
    interpreter: object | None = None,
) -> tuple[SC2CommandSession, DemoFakeBotAI]:
    """Wire the scripted fake bot through adapter, executor, and session.

    The session carries a fresh :class:`CommanderEventMemory` (every outcome
    recorded with game time, readable via ``session.event_memory``) and a
    fresh :class:`StandingOrderController` (``session.standing_orders``).
    ``interpreter=None`` keeps the session's default rule interpreter.
    """

    bot = DemoFakeBotAI()
    adapter = PythonSC2BotAdapter(bot=bot)
    session_kwargs: dict[str, object] = {
        "executor": SC2RuntimeExecutor(bot=adapter),
        "event_memory": CommanderEventMemory(),
        "standing_orders": StandingOrderController(),
    }
    if interpreter is not None:
        session_kwargs["interpreter"] = interpreter
    session = SC2CommandSession(**session_kwargs)
    return session, bot


class _SessionRecordedHistory:
    """``SessionLoopBridge`` history seam over a session-recorded memory.

    In ``--gui`` dry-run mode the session itself records every outcome into
    the shared :class:`CommanderEventMemory` (stamped with game time), and
    the bridge then offers the same outcomes to ``record`` again. Storing
    them twice would double every GUI log line, so ``record`` skips any
    outcome whose command text, status, and narration already match a recent
    memory event. Outcomes the session never recorded — the bridge-built
    session-failure reports — find no match and are still stored, so nothing
    is dropped silently.
    """

    def __init__(self, memory: CommanderEventMemory) -> None:
        self._memory = memory

    def record(self, outcome: object) -> object:
        """Record one outcome unless the session already recorded it."""

        fingerprint = _outcome_fingerprint(outcome)
        for event in self._memory.recent(_BRIDGE_DEDUP_WINDOW):
            if (event.command_text, event.status, event.narration) == fingerprint:
                return event
        return self._memory.record(outcome)

    def since(self, seq: int) -> tuple[object, ...]:
        """Return every memory event recorded after sequence ``seq``."""

        return self._memory.since(int(seq))

    def latest_seq(self) -> int:
        """Return the memory's highest assigned sequence number."""

        return self._memory.latest_seq()


def _outcome_fingerprint(outcome: object) -> tuple[str, str, str]:
    """Build the (command_text, status, narration) dedup key for one outcome."""

    if isinstance(outcome, Mapping):
        return (
            str(outcome.get("command_text", "")),
            str(outcome.get("status", "")),
            str(outcome.get("narration", "")),
        )
    return (
        str(getattr(outcome, "command_text", "")),
        str(getattr(outcome, "status", "")),
        str(getattr(outcome, "narration", "")),
    )


class CommanderGameBridge:
    """Live-mode web GUI bridge over the bot's ``on_step`` command queue.

    ``submit_command`` enqueues into the SAME asyncio queue the terminal
    reader feeds (thread-safe via ``loop.call_soon_threadsafe``), so browser
    and terminal commands drain through one sequential ``on_step`` path and
    the LLM-free per-frame invariant holds. History is read directly from
    the shared :class:`CommanderEventMemory` the live session records into;
    the bridge itself never executes or records anything.
    """

    def __init__(
        self,
        bot: object,
        loop: asyncio.AbstractEventLoop,
        command_queue: "asyncio.Queue[str]",
        event_memory: CommanderEventMemory,
        state_resolver: SC2StateResolverInterface = DEFAULT_SC2_STATE_RESOLVER,
        llm_control: LocalLLMControl | None = None,
    ) -> None:
        self._bot = bot
        self._loop = loop
        self._queue = command_queue
        self._event_memory = event_memory
        self._state_resolver = state_resolver
        self._llm_control = llm_control

    def submit_command(self, text: str) -> None:
        """Enqueue one browser utterance into the on_step command queue."""

        if not isinstance(text, str):
            raise TypeError("Web GUI command text must be a string.")
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("Web GUI command text must be non-empty.")
        self._loop.call_soon_threadsafe(self._queue.put_nowait, cleaned)

    def state_snapshot(self) -> Mapping[str, object] | None:
        """Resolve the live bot into a JSON-ready commander state snapshot."""

        state = self._state_resolver.resolve(self._bot)
        to_dict = getattr(state, "to_dict", None)
        if callable(to_dict):
            return dict(to_dict())
        if isinstance(state, Mapping):
            return dict(state)
        return None

    def history_since(self, seq: int) -> tuple[dict[str, object], ...]:
        """Return JSON-ready shared-memory events after sequence ``seq``."""

        return tuple(
            event.to_dict() for event in self._event_memory.since(int(seq))
        )

    def latest_seq(self) -> int:
        """Return the shared memory's highest assigned sequence number."""

        return int(self._event_memory.latest_seq())

    def llm_settings_snapshot(self) -> Mapping[str, object]:
        control = self._llm_control
        if control is None:
            return {"provider": "", "model": "", "configured": False, "key_present": False}
        return dict(control.snapshot())

    def configure_llm(
        self,
        provider: str,
        api_key: str,
        model: str = "",
    ) -> Mapping[str, object]:
        control = self._llm_control
        if control is None:
            raise RuntimeError("이 세션은 웹 LLM 키 설정을 지원하지 않습니다.")
        return dict(control.configure(provider, api_key, model))


def render_outcome_lines(outcome: SC2CommandOutcome) -> tuple[str, ...]:
    """Render one command outcome into printable demo transcript lines."""

    lines = [f"명령: {outcome.command_text}"]
    if outcome.intent_dsl is not None:
        dsl_json = json.dumps(dict(outcome.intent_dsl), ensure_ascii=False, indent=2)
        lines.append("Intent DSL:")
        lines.extend(f"  {dsl_line}" for dsl_line in dsl_json.splitlines())
    lines.append(f"[{outcome.status}] {outcome.narration}")
    return tuple(lines)


def capture_voice_command(
    record_seconds: float,
    *,
    listener: MicrophoneListener | None = None,
    transcriber: VoiceTranscriberInterface | None = None,
) -> str:
    """Record one push-to-talk clip and return the transcribed command text.

    Missing voice dependencies raise the voice guard's actionable
    ``MissingVoiceDependencyError`` (install hints included) from
    ``MicrophoneListener.record_seconds`` / ``FasterWhisperTranscriber``.
    """

    active_listener = listener if listener is not None else MicrophoneListener()
    active_transcriber = (
        transcriber if transcriber is not None else FasterWhisperTranscriber()
    )
    print(f"{record_seconds:g}초 동안 녹음합니다...")
    waveform = active_listener.record_seconds(record_seconds)
    transcription = active_transcriber.transcribe(waveform)
    confidence = transcription.confidence
    if confidence is not None and confidence < MIN_VOICE_TRANSCRIPTION_CONFIDENCE:
        # Low-confidence text is a hallucination risk: re-prompt instead of
        # forwarding it to the interpreter as a game command.
        print(
            f"음성 인식 신뢰도가 낮습니다 (신뢰도 {confidence:.2f}). "
            "다시 또박또박 말해 주세요."
        )
        return ""
    print(f"인식된 명령: {transcription.text}")
    return transcription.text


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the demo argument parser."""

    parser = argparse.ArgumentParser(
        prog="python -m starcraft_commander.demo_sc2",
        description=(
            "StarCraft II Korean commander demo. "
            "기본은 실제 게임(라이브) 모드이며 python-sc2와 StarCraft II 설치가 "
            "필요합니다. --dry-run은 내장 가짜 BotAI로 전체 파이프라인을 실행합니다."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run against the built-in scripted DemoFakeBotAI (no StarCraft II needed)",
    )
    parser.add_argument(
        "--script",
        nargs="+",
        default=None,
        metavar="COMMAND",
        help="non-interactive Korean command list processed in order",
    )
    parser.add_argument(
        "--voice",
        action="store_true",
        help="push-to-talk voice input (requires faster-whisper + sounddevice)",
    )
    parser.add_argument(
        "--llm",
        dest="llm",
        action="store_true",
        default=True,
        help="enable the required LLM-first interpreter (default; live mode requires it)",
    )
    parser.add_argument(
        "--no-llm",
        dest="llm",
        action="store_false",
        help=(
            "DEPRECATED: dry-run/testing compatibility only; deterministic "
            "rule/keyword matching is not used for live command understanding"
        ),
    )
    parser.add_argument(
        "--llm-provider",
        default=DEFAULT_LLM_PROVIDER,
        choices=("openai", "anthropic"),
        help="LLM provider for live/web API key settings (default: openai)",
    )
    parser.add_argument(
        "--llm-model",
        default=DEFAULT_OPENAI_MODEL,
        help="LLM model for live/web API key settings",
    )
    parser.add_argument(
        "--gui",
        nargs="?",
        type=int,
        const=DEFAULT_WEB_GUI_PORT,
        default=None,
        metavar="PORT",
        help=(
            "serve the local browser GUI on 127.0.0.1 "
            f"(default port: {DEFAULT_WEB_GUI_PORT}; 0 for an ephemeral port)"
        ),
    )
    parser.add_argument(
        "--gui-host",
        default=WEB_GUI_HOST,
        help=(
            "web GUI bind host (default: 127.0.0.1). Use 0.0.0.0 for "
            "phone/tablet companion control, together with --gui-token."
        ),
    )
    parser.add_argument(
        "--gui-token",
        default="",
        help="auth token required when exposing the web GUI beyond localhost",
    )
    parser.add_argument(
        "--record-seconds",
        type=float,
        default=DEFAULT_VOICE_RECORD_SECONDS,
        help="voice recording window in seconds (default: 5)",
    )
    parser.add_argument(
        "--map",
        default=DEFAULT_SC2_DEMO_MAP,
        help=f"StarCraft II map name for live mode (default: {DEFAULT_SC2_DEMO_MAP})",
    )
    parser.add_argument(
        "--race",
        default="terran",
        choices=("terran",),
        help="commander race (Terran MVP only)",
    )
    parser.add_argument(
        "--difficulty",
        default="easy",
        choices=("easy", "medium", "hard"),
        help="computer opponent difficulty for live mode",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse demo command-line arguments."""

    return build_argument_parser().parse_args(argv)


async def _process_and_print(session: SC2CommandSession, command_text: str) -> None:
    """Process one utterance and print every resulting outcome."""

    outcomes = await session.process_text(command_text)
    for outcome in outcomes:
        for line in render_outcome_lines(outcome):
            print(line)


async def _run_script(session: SC2CommandSession, commands: Sequence[str]) -> None:
    """Process a non-interactive command list in order."""

    for command_text in commands:
        print(f"{COMMAND_PROMPT}{command_text}")
        await _process_and_print(session, command_text)
        print()


def _read_text_command() -> str | None:
    """Read one interactive text command; ``None`` requests loop exit."""

    try:
        text = input(COMMAND_PROMPT)
    except EOFError:
        return None
    stripped = text.strip()
    if not stripped or stripped.lower() in EXIT_COMMAND_WORDS:
        return None
    return stripped


def _read_voice_command(record_seconds: float) -> str | None:
    """Read one push-to-talk voice command; ``None`` requests loop exit.

    One bad recording must never kill the session: transient capture or
    transcription failures print a Korean retry message and return ``""`` so
    the loop re-prompts. Missing voice dependencies print their actionable
    bilingual install hint and request a graceful exit (``None``).
    """

    try:
        typed = input(
            f"Enter 키를 누르면 {record_seconds:g}초 동안 녹음합니다 "
            "(종료하려면 '종료' 입력 후 Enter): "
        )
    except EOFError:
        return None
    if typed.strip().lower() in EXIT_COMMAND_WORDS:
        return None
    try:
        text = capture_voice_command(record_seconds).strip()
    except MissingVoiceDependencyError as error:
        print(str(error))
        print("음성 의존성이 없어 음성 입력을 종료합니다.")
        return None
    except Exception as error:  # noqa: BLE001 - one bad recording must not kill the loop.
        print(f"녹음/인식에 실패했습니다: {error}. 다시 시도해 주세요.")
        return ""
    if not text:
        print("음성이 인식되지 않았습니다. 다시 시도해 주세요.")
        return ""
    return text


async def _run_interactive(session: SC2CommandSession, args: argparse.Namespace) -> None:
    """Run the interactive command loop (text or push-to-talk voice)."""

    print("한국어 명령을 입력하세요. 종료: 종료 / quit / 빈 입력.")
    while True:
        if args.voice:
            command_text = _read_voice_command(args.record_seconds)
        else:
            command_text = _read_text_command()
        if command_text is None:
            print("데모를 종료합니다.")
            return
        if not command_text:
            continue
        await _process_and_print(session, command_text)


def _serve_until_interrupt() -> None:
    """Block the main thread until KeyboardInterrupt (Ctrl+C)."""

    while True:
        time.sleep(0.5)


def _run_dry_run_gui(
    session: SC2CommandSession,
    args: argparse.Namespace,
    *,
    llm_control: LocalLLMControl | None = None,
) -> int:
    """Serve the dry-run session through the web GUI until interrupted.

    The GUI bridge owns the session: scripted ``--script`` commands are
    submitted through the same bridge first, then the server keeps serving
    browser commands until Ctrl+C. History is the session's own
    :class:`CommanderEventMemory` (the bridge never records a session
    outcome twice).
    """

    memory = session.event_memory
    history = (
        _SessionRecordedHistory(memory)
        if isinstance(memory, CommanderEventMemory)
        else None
    )
    bridge = SessionLoopBridge(
        session=session,
        history=history,
        llm_control=llm_control,
    )
    server = WebGuiServer(
        bridge=bridge,
        port=args.gui,
        host=args.gui_host,
        auth_token=args.gui_token,
        auto_launch_live=bool(args.llm),
    )
    bridge.start()
    try:
        try:
            server.start()
        except OSError as error:
            print(
                f"포트 {args.gui}에 바인딩하지 못했습니다 (이유: {error}). "
                "--gui에 다른 포트를 지정하거나 --gui 0으로 임시 포트를 사용해 주세요."
            )
            return 1
        print(f"VoiStarCraft 커맨더 웹 GUI 시작: {server.url}")
        if args.script:
            for command_text in args.script:
                print(f"{COMMAND_PROMPT}{command_text}")
                bridge.submit_command(command_text)
        print(
            "브라우저에서 위 주소를 열고 한국어 명령을 입력하세요. "
            f"예: {MVP_DEMO_COMMAND} (종료: Ctrl+C)"
        )
        _serve_until_interrupt()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        bridge.stop()
    print("웹 GUI를 종료합니다.")
    return 0


def run_dry_run(args: argparse.Namespace) -> int:
    """Run the full pipeline against the built-in scripted fake bot."""

    llm_control: LocalLLMControl | None = None
    if args.gui is not None:
        llm_control = LocalLLMControl(
            provider=args.llm_provider,
            model=args.llm_model,
        )
        interpreter = (
            HybridCommandInterpreter(llm_interpreter=llm_control)
            if args.llm
            else None
        )
    else:
        interpreter = (
            build_llm_interpreter(args.llm_provider, args.llm_model)
            if args.llm
            else None
        )
    session, _bot = build_dry_run_session(interpreter=interpreter)
    for line in _DRY_RUN_BANNER_LINES:
        print(line)
    if args.gui is not None:
        return _run_dry_run_gui(session, args, llm_control=llm_control)
    if args.script:
        asyncio.run(_run_script(session, tuple(args.script)))
    else:
        asyncio.run(_run_interactive(session, args))
    return 0


def run_live(args: argparse.Namespace) -> None:
    """Run a live python-sc2 custom game wired to the command pipeline.

    Raises:
        MissingSC2RuntimeError: When the optional python-sc2 runtime is not
            installed (actionable bilingual install guidance included).
        MissingLLMDependencyError: When the anthropic SDK or a resolvable API
            key is unavailable (fail fast, before the game starts).
    """

    require_python_sc2()
    # Provider SDK and key must both exist before the match starts. The GUI can
    # rotate the process-local key later, but cannot bypass startup preflight.
    llm_control = build_local_llm_control(args.llm_provider, args.llm_model)
    interpreter = HybridCommandInterpreter(llm_interpreter=llm_control)
    if args.voice:
        require_faster_whisper()
        require_sounddevice()
    # Lazy imports: this module must stay importable without python-sc2.
    from sc2 import maps
    from sc2.bot_ai import BotAI
    from sc2.data import Difficulty, Race
    from sc2.main import run_game
    from sc2.player import Bot, Computer

    use_voice = bool(args.voice)
    record_seconds = float(args.record_seconds)
    gui_port = args.gui

    class CommanderLiveBot(BotAI):
        """Live BotAI draining queued commander texts inside ``on_step``.

        Standing orders tick every
        :data:`STANDING_ORDER_TICK_INTERVAL_STEPS` ``on_step`` iterations —
        deterministic code policies, never the LLM. Every command outcome is
        recorded into the shared :class:`CommanderEventMemory`, which the
        optional ``--gui`` server reads for its history endpoint.
        """

        def __init__(self) -> None:
            super().__init__()
            self.session: SC2CommandSession | None = None
            self.command_queue: "asyncio.Queue[str]" = asyncio.Queue()
            self.event_memory = CommanderEventMemory()
            self.standing_orders = StandingOrderController()
            self.gui_server: WebGuiServer | None = None
            self._reader_task: object | None = None

        async def on_start(self) -> None:
            adapter = PythonSC2BotAdapter(bot=self)
            session_kwargs: dict[str, object] = {
                "executor": SC2RuntimeExecutor(bot=adapter),
                "event_memory": self.event_memory,
                "standing_orders": self.standing_orders,
            }
            if interpreter is not None:
                session_kwargs["interpreter"] = interpreter
            self.session = SC2CommandSession(**session_kwargs)
            loop = asyncio.get_running_loop()
            self._reader_task = loop.create_task(self._feed_commands(loop))
            if gui_port is not None:
                self._start_gui_server(loop)
            print("말하면 스타가 움직인다. 한국어 명령을 입력하세요 (종료: 종료/quit).")

        def _start_gui_server(self, loop: asyncio.AbstractEventLoop) -> None:
            """Serve the web GUI over the live command queue and memory."""

            bridge = CommanderGameBridge(
                bot=self,
                loop=loop,
                command_queue=self.command_queue,
                event_memory=self.event_memory,
                llm_control=llm_control,
            )
            server = WebGuiServer(
                bridge=bridge,
                port=gui_port,
                host=args.gui_host,
                auth_token=args.gui_token,
            )
            try:
                server.start()
            except OSError as error:
                print(
                    f"웹 GUI 포트 {gui_port}에 바인딩하지 못했습니다 (이유: {error}). "
                    "게임은 GUI 없이 계속 진행됩니다. 다음에는 --gui에 다른 포트를 "
                    "지정해 주세요."
                )
                return
            self.gui_server = server
            print(f"VoiStarCraft 커맨더 웹 GUI 시작: {server.url}")

        async def _feed_commands(self, loop: asyncio.AbstractEventLoop) -> None:
            # The whole loop is guarded: an unexpected reader exception must
            # never kill this never-awaited task silently while the game
            # keeps running without any way to enter commands.
            try:
                while True:
                    if use_voice:
                        command_text = await loop.run_in_executor(
                            None, _read_voice_command, record_seconds
                        )
                    else:
                        command_text = await loop.run_in_executor(
                            None, _read_text_command
                        )
                    if command_text is None:
                        print("명령 입력을 종료합니다. 게임은 계속 진행됩니다.")
                        return
                    if command_text:
                        await self.command_queue.put(command_text)
            except Exception as error:  # noqa: BLE001 - surface, never die silently.
                print(
                    "명령 입력 루프가 오류로 중단되었습니다: "
                    f"{error}. 게임은 계속 진행됩니다."
                )

        async def on_step(self, iteration: int) -> None:
            if self.session is None:
                return
            while not self.command_queue.empty():
                command_text = self.command_queue.get_nowait()
                await _process_and_print(self.session, command_text)
            if iteration % STANDING_ORDER_TICK_INTERVAL_STEPS == 0:
                for tick in await self.standing_orders.tick(self):
                    label = STANDING_ORDER_KOREAN_LABELS.get(tick.kind, tick.kind)
                    for action in tick.actions_issued:
                        print(f"[상비 명령] {label}: {action}")

    race_by_name = {"terran": Race.Terran}
    difficulty_by_name = {
        "easy": Difficulty.Easy,
        "medium": Difficulty.Medium,
        "hard": Difficulty.Hard,
    }
    run_game(
        maps.get(args.map),
        [
            Bot(race_by_name[args.race.lower()], CommanderLiveBot()),
            Computer(Race.Random, difficulty_by_name[args.difficulty.lower()]),
        ],
        realtime=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Console entrypoint for ``python -m starcraft_commander.demo_sc2``."""

    args = parse_args(argv)
    if args.dry_run:
        return run_dry_run(args)
    run_live(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
