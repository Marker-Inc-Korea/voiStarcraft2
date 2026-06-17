"""Handoff Step 5 acceptance tests for the StarCraft II demo entrypoint.

Only the dry-run (scripted fake BotAI) path is executed: live and interactive
paths require StarCraft II / stdin and are pinned only at their guard
boundaries. No test requires python-sc2, StarCraft II, faster-whisper, or
sounddevice.
"""

import asyncio
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import threading
import time
import types
import unittest
import urllib.request
from unittest import mock

from starcraft_commander import demo_sc2
from starcraft_commander.event_memory import CommanderEventMemory
from starcraft_commander.live_pipeline import SC2CommandSession
from starcraft_commander.llm_interpreter import (
    HybridCommandInterpreter,
    LocalLLMControl,
)
from starcraft_commander.map_resolver import (
    SC2_SUPPORTED_SEMANTIC_TARGETS,
    SC2MapResolver,
)
from starcraft_commander.python_sc2_adapter import PythonSC2BotAdapter
from starcraft_commander.runtime_deps import (
    MissingLLMDependencyError,
    MissingSC2RuntimeError,
    MissingVoiceDependencyError as RuntimeMissingVoiceDependencyError,
)
from starcraft_commander.standing_orders import StandingOrderController
from starcraft_commander.state_resolver import resolve_commander_state
from starcraft_commander.voice_input import (
    MicrophoneListener,
    MissingVoiceDependencyError,
    VoiceTranscription,
)
from starcraft_commander.web_gui import DEFAULT_WEB_GUI_PORT, WebGuiBridgeInterface
from toycraft_commander.interpreter import DEFAULT_COMMAND_INTERPRETER


PYTHON_SC2_INSTALLED = importlib.util.find_spec("sc2") is not None
SOUNDDEVICE_INSTALLED = importlib.util.find_spec("sounddevice") is not None
FASTER_WHISPER_INSTALLED = importlib.util.find_spec("faster_whisper") is not None
VOICE_DEPS_INSTALLED = SOUNDDEVICE_INSTALLED and FASTER_WHISPER_INSTALLED
ANTHROPIC_INSTALLED = importlib.util.find_spec("anthropic") is not None
GUI_POLL_DEADLINE_SECONDS = 10.0
GUI_POLL_INTERVAL_SECONDS = 0.05


def build_fake_sc2_modules(run_game):
    """Build importable fake sc2 modules for run_live wiring tests."""

    sc2_module = types.ModuleType("sc2")
    bot_ai_module = types.ModuleType("sc2.bot_ai")

    class FakeBotAIBase:
        def __init__(self):
            pass

    bot_ai_module.BotAI = FakeBotAIBase

    data_module = types.ModuleType("sc2.data")
    data_module.Race = types.SimpleNamespace(Terran="RACE_TERRAN", Random="RACE_RANDOM")
    data_module.Difficulty = types.SimpleNamespace(
        Easy="DIFF_EASY", Medium="DIFF_MEDIUM", Hard="DIFF_HARD"
    )

    main_module = types.ModuleType("sc2.main")
    main_module.run_game = run_game

    player_module = types.ModuleType("sc2.player")

    class FakeBotPlayer:
        def __init__(self, race, ai):
            self.race = race
            self.ai = ai

    class FakeComputerPlayer:
        def __init__(self, race, difficulty):
            self.race = race
            self.difficulty = difficulty

    player_module.Bot = FakeBotPlayer
    player_module.Computer = FakeComputerPlayer

    maps_module = types.ModuleType("sc2.maps")
    maps_module.get = lambda name: ("MAP", name)

    sc2_module.maps = maps_module
    return {
        "sc2": sc2_module,
        "sc2.bot_ai": bot_ai_module,
        "sc2.data": data_module,
        "sc2.main": main_module,
        "sc2.player": player_module,
        "sc2.maps": maps_module,
    }


def run_main_capturing_stdout(argv):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        exit_code = demo_sc2.main(argv)
    return exit_code, buffer.getvalue()


class FakeListener:
    def __init__(self):
        self.recorded_seconds = []

    def record_seconds(self, seconds):
        self.recorded_seconds.append(seconds)
        return ("waveform", seconds)


class FakeTranscriber:
    def __init__(self, text, confidence=None):
        self._text = text
        self._confidence = confidence
        self.received_audio = []

    def transcribe(self, audio):
        self.received_audio.append(audio)
        return VoiceTranscription(
            text=self._text,
            language="ko",
            duration_seconds=1.0,
            confidence=self._confidence,
        )


class ParseArgsTest(unittest.TestCase):
    def test_defaults(self) -> None:
        args = demo_sc2.parse_args([])
        expected = {
            "dry_run": False,
            "script": None,
            "voice": False,
            "record_seconds": 5.0,
            "map": demo_sc2.DEFAULT_SC2_DEMO_MAP,
            "race": "terran",
            "difficulty": "easy",
            "llm": True,
            "gui": None,
            "gui_host": "127.0.0.1",
            "gui_token": "",
        }
        for name, value in expected.items():
            with self.subTest(argument=name):
                self.assertEqual(value, getattr(args, name))

    def test_script_collects_multiple_commands(self) -> None:
        args = demo_sc2.parse_args(
            ["--dry-run", "--script", "SCV 계속 찍어", "상태 알려줘"]
        )
        self.assertTrue(args.dry_run)
        self.assertEqual(["SCV 계속 찍어", "상태 알려줘"], args.script)

    def test_gui_flag_defaults_to_web_gui_port_and_accepts_override(self) -> None:
        cases = {
            "bare flag": (["--dry-run", "--gui"], DEFAULT_WEB_GUI_PORT),
            "explicit port": (["--dry-run", "--gui", "9000"], 9000),
            "ephemeral port": (["--dry-run", "--gui", "0"], 0),
        }
        for label, (argv, expected_port) in cases.items():
            with self.subTest(case=label):
                self.assertEqual(expected_port, demo_sc2.parse_args(argv).gui)
        self.assertEqual(8350, DEFAULT_WEB_GUI_PORT)

    def test_gui_host_and_token_parse_for_companion_control(self) -> None:
        args = demo_sc2.parse_args(
            [
                "--dry-run",
                "--gui",
                "--gui-host",
                "0.0.0.0",
                "--gui-token",
                "dev-token",
            ]
        )
        self.assertEqual("0.0.0.0", args.gui_host)
        self.assertEqual("dev-token", args.gui_token)

    def test_llm_flag_parses(self) -> None:
        self.assertTrue(demo_sc2.parse_args(["--dry-run", "--llm"]).llm)
        self.assertFalse(demo_sc2.parse_args(["--dry-run", "--no-llm"]).llm)


class DemoFakeBotTest(unittest.TestCase):
    def test_state_observation_is_complete_and_plausible(self) -> None:
        state = resolve_commander_state(demo_sc2.DemoFakeBotAI())
        self.assertTrue(state.observation_complete)
        self.assertEqual(400, state.minerals)
        self.assertEqual(20, state.supply_used)
        self.assertEqual(21, state.supply_cap)
        self.assertEqual(1, state.supply_left)
        self.assertEqual({"SCV": 12, "MARINE": 6}, dict(state.own_units))
        self.assertEqual({"COMMANDCENTER": 1}, dict(state.own_structures))
        self.assertEqual(2, state.idle_worker_count)
        self.assertEqual(6, state.army_count)

    def test_map_resolver_derives_every_semantic_target(self) -> None:
        resolver = SC2MapResolver.from_bot(demo_sc2.DemoFakeBotAI())
        self.assertEqual(SC2_SUPPORTED_SEMANTIC_TARGETS, resolver.available_targets)
        for target in SC2_SUPPORTED_SEMANTIC_TARGETS:
            with self.subTest(target=target):
                self.assertIsNotNone(resolver.resolve_point(target))

    def test_build_dry_run_session_wires_adapter_into_executor(self) -> None:
        session, bot = demo_sc2.build_dry_run_session()
        self.assertIsInstance(session, SC2CommandSession)
        self.assertIsInstance(session.executor.bot, PythonSC2BotAdapter)
        self.assertIs(bot, session.executor.bot.bot)


class DryRunScriptTest(unittest.TestCase):
    def test_dry_run_script_runs_to_completion_and_prints_narrations(self) -> None:
        # The demo pins its own contract (the "[status] " line format and
        # pipeline wiring); the exact narration wording is pinned once in
        # tests/test_sc2_narrator.py, so only stable semantic fragments are
        # asserted here.
        exit_code, output = run_main_capturing_stdout(
            ["--dry-run", "--no-llm", "--script", "SCV 계속 찍어", "상태 알려줘"]
        )
        self.assertEqual(0, exit_code)
        expected_snippets = (
            "StarCraft II Commander 데모 (dry-run)",
            "명령> SCV 계속 찍어",
            "Intent DSL:",
            '"intent": "TRAIN_WORKER"',
            # "계속 찍어" now activates the code-driven standing order instead
            # of honestly degrading to a one-shot partial action.
            "[executed] ",
            "SCV 1기 생산 명령",
            "상비 명령 등록: 지속 SCV 생산",
            "명령> 상태 알려줘",
            "[read_only] ",
            "전장 상태를 확인했습니다",
            "상비 명령: 지속 SCV 생산 활성",
            "최근 명령 1건:",
            "미네랄 400",
        )
        for snippet in expected_snippets:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, output)

    def test_dry_run_mvp_compound_command_prints_both_outcomes(self) -> None:
        exit_code, output = run_main_capturing_stdout(
            ["--dry-run", "--no-llm", "--script", demo_sc2.MVP_DEMO_COMMAND]
        )
        self.assertEqual(0, exit_code)
        # Two outcomes: the marine-move part attack-moves the six scripted
        # Marines onto the ramp, and the SCV part actually trains.
        self.assertEqual(2, output.count("명령: "))
        with self.subTest(part="marine move executed"):
            self.assertIn("명령: 마린 6기 입구로 보내", output)
            self.assertIn('"intent": "DEFEND"', output)
            self.assertIn('"unit_group": "6 Marines"', output)
            self.assertIn("[executed] ", output)
            self.assertIn("마린 6기", output)
            self.assertIn("공격 이동", output)
        with self.subTest(part="SCV production registers standing order"):
            self.assertIn("명령: SCV 계속 찍어", output)
            self.assertIn("[executed] ", output)
            self.assertIn("SCV 1기 생산 명령", output)
            self.assertIn("상비 명령 등록: 지속 SCV 생산", output)
        with self.subTest(part="no untranslated unit group leaks"):
            narration_lines = [
                line for line in output.splitlines() if line.startswith("[")
            ]
            for line in narration_lines:
                self.assertNotIn("Marines", line)

    def test_dry_run_blocked_command_is_never_reported_as_success(self) -> None:
        exit_code, output = run_main_capturing_stdout(
            ["--dry-run", "--no-llm", "--script", "배럭 지어"]
        )
        self.assertEqual(0, exit_code)
        self.assertIn("[blocked]", output)
        self.assertIn("실행하지 않았습니다", output)
        self.assertIn("대안:", output)
        self.assertNotIn("[executed]", output)


class RenderOutcomeLinesTest(unittest.TestCase):
    def test_renders_command_dsl_json_and_narration(self) -> None:
        from starcraft_commander.live_pipeline import SC2CommandOutcome

        outcome = SC2CommandOutcome(
            command_text="피아노 쳐줘",
            status="clarification",
            narration="다시 말해 주세요.",
        )
        lines = demo_sc2.render_outcome_lines(outcome)
        self.assertEqual("명령: 피아노 쳐줘", lines[0])
        self.assertEqual("[clarification] 다시 말해 주세요.", lines[-1])
        self.assertNotIn("Intent DSL:", lines)


class LiveModeGuardTest(unittest.TestCase):
    @unittest.skipUnless(ANTHROPIC_INSTALLED, "anthropic is not installed")
    def test_required_llm_reports_missing_api_key_separately(self) -> None:
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            with self.assertRaises(MissingLLMDependencyError) as context:
                demo_sc2.build_llm_interpreter("anthropic", "claude-sonnet-4-5")
        message = str(context.exception)
        self.assertIn("ANTHROPIC_API_KEY", message)
        self.assertIn("설정", message)
        self.assertNotIn("is not installed", message)

    def test_dry_run_gui_starts_before_web_key_configuration(self) -> None:
        captured = {}

        def fake_gui(session, args, *, llm_control=None):
            captured["session"] = session
            captured["llm_control"] = llm_control
            return 0

        with mock.patch.object(demo_sc2, "_run_dry_run_gui", fake_gui):
            result = demo_sc2.run_dry_run(
                demo_sc2.parse_args(
                    [
                        "--dry-run",
                        "--gui",
                        "0",
                        "--llm-provider",
                        "openai",
                        "--llm-model",
                        "gpt-5.5",
                    ]
                )
            )

        self.assertEqual(result, 0)
        self.assertIsInstance(captured["llm_control"], LocalLLMControl)
        self.assertFalse(captured["llm_control"].is_available())
        self.assertIsInstance(captured["session"].interpreter, HybridCommandInterpreter)

    def test_dry_run_gui_enables_live_auto_launch_after_key_setup(self) -> None:
        captured = {}

        class FakeBridge:
            def __init__(self, **kwargs):
                captured["bridge_kwargs"] = kwargs

            def start(self):
                captured["bridge_started"] = True

            def stop(self):
                captured["bridge_stopped"] = True

        class FakeServer:
            url = "http://127.0.0.1:0"

            def __init__(self, **kwargs):
                captured["server_kwargs"] = kwargs

            def start(self):
                captured["server_started"] = True

            def stop(self):
                captured["server_stopped"] = True

        session, _bot = demo_sc2.build_dry_run_session()
        args = demo_sc2.parse_args(["--dry-run", "--gui", "0"])
        with mock.patch.object(demo_sc2, "SessionLoopBridge", FakeBridge):
            with mock.patch.object(demo_sc2, "WebGuiServer", FakeServer):
                with mock.patch.object(
                    demo_sc2,
                    "_serve_until_interrupt",
                    side_effect=KeyboardInterrupt,
                ):
                    result = demo_sc2._run_dry_run_gui(
                        session,
                        args,
                        llm_control=LocalLLMControl(),
                    )

        self.assertEqual(result, 0)
        self.assertTrue(captured["server_kwargs"]["auto_launch_live"])
        self.assertIsInstance(
            captured["bridge_kwargs"]["llm_control"],
            LocalLLMControl,
        )
        self.assertTrue(captured["server_started"])
        self.assertTrue(captured["server_stopped"])

    def test_live_local_llm_control_requires_provider_key_before_start(self) -> None:
        with mock.patch.object(demo_sc2, "require_openai", mock.Mock()):
            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
                with self.assertRaises(MissingLLMDependencyError) as context:
                    demo_sc2.build_local_llm_control(
                        "openai", demo_sc2.DEFAULT_OPENAI_MODEL
                    )
        message = str(context.exception)
        self.assertIn("OPENAI_API_KEY", message)
        self.assertIn("LLM", message)

    @unittest.skipIf(PYTHON_SC2_INSTALLED, "python-sc2 is installed in this environment")
    def test_run_live_raises_actionable_missing_runtime_error(self) -> None:
        with self.assertRaises(MissingSC2RuntimeError) as context:
            demo_sc2.run_live(demo_sc2.parse_args([]))
        message = str(context.exception)
        self.assertIn("pip install", message)
        self.assertIn("StarCraft II", message)

    @unittest.skipIf(PYTHON_SC2_INSTALLED, "python-sc2 is installed in this environment")
    def test_main_without_dry_run_raises_missing_runtime_error(self) -> None:
        with self.assertRaises(MissingSC2RuntimeError):
            demo_sc2.main([])


class RunLiveWiringTest(unittest.TestCase):
    """run_live wiring pinned with importable fake sc2 modules."""

    def test_run_live_wires_map_race_difficulty_and_realtime(self) -> None:
        captured = {}

        def fake_run_game(map_object, players, realtime=None):
            captured["map"] = map_object
            captured["players"] = players
            captured["realtime"] = realtime

        modules = build_fake_sc2_modules(fake_run_game)
        buffer = io.StringIO()
        llm_control = mock.Mock()
        with mock.patch.dict(sys.modules, modules):
            with mock.patch.object(
                demo_sc2,
                "build_local_llm_control",
                mock.Mock(return_value=llm_control),
            ) as llm_builder:
                with contextlib.redirect_stdout(buffer):
                    demo_sc2.run_live(
                        demo_sc2.parse_args(
                            ["--map", "TestMapLE", "--difficulty", "hard"]
                        )
                    )
        llm_builder.assert_called_once_with("openai", demo_sc2.DEFAULT_OPENAI_MODEL)
        self.assertEqual(("MAP", "TestMapLE"), captured["map"])
        self.assertTrue(captured["realtime"])
        bot_player, computer_player = captured["players"]
        self.assertEqual("RACE_TERRAN", bot_player.race)
        self.assertEqual("CommanderLiveBot", type(bot_player.ai).__name__)
        self.assertEqual("RACE_RANDOM", computer_player.race)
        self.assertEqual("DIFF_HARD", computer_player.difficulty)

    def test_commander_live_bot_processes_queued_commands_in_on_step(self) -> None:
        captured = {}

        def fake_run_game(map_object, players, realtime=None):
            captured["bot"] = players[0].ai

        modules = build_fake_sc2_modules(fake_run_game)
        buffer = io.StringIO()
        llm_control = mock.Mock()
        llm_control.is_available.return_value = True
        llm_control.interpret.side_effect = DEFAULT_COMMAND_INTERPRETER.interpret
        with mock.patch.dict(sys.modules, modules):
            with mock.patch.object(
                demo_sc2,
                "build_local_llm_control",
                mock.Mock(return_value=llm_control),
            ):
                with contextlib.redirect_stdout(buffer):
                    demo_sc2.run_live(demo_sc2.parse_args([]))
        bot = captured["bot"]
        commands = iter(["상태 알려줘", None])

        async def drive():
            with mock.patch.object(
                demo_sc2, "_read_text_command", lambda: next(commands)
            ):
                await bot.on_start()
                await bot._reader_task
            await bot.on_step(0)

        with contextlib.redirect_stdout(buffer):
            asyncio.run(drive())
        output = buffer.getvalue()
        self.assertIsInstance(bot.session, SC2CommandSession)
        self.assertIn("명령 입력을 종료합니다", output)
        self.assertIn("[read_only]", output)
        self.assertTrue(bot.command_queue.empty())

    @unittest.skipIf(VOICE_DEPS_INSTALLED, "voice dependencies are installed")
    def test_run_live_voice_mode_preflights_voice_dependencies(self) -> None:
        # --voice must fail fast with the actionable bilingual hint instead
        # of letting the reader task die mid-game.
        def fail_run_game(map_object, players, realtime=None):
            raise AssertionError("run_game must not be reached without voice deps")

        modules = build_fake_sc2_modules(fail_run_game)
        llm_control = mock.Mock()
        with mock.patch.dict(sys.modules, modules):
            with mock.patch.object(
                demo_sc2,
                "build_local_llm_control",
                mock.Mock(return_value=llm_control),
            ):
                with self.assertRaises(RuntimeMissingVoiceDependencyError) as context:
                    demo_sc2.run_live(demo_sc2.parse_args(["--voice"]))
        self.assertIn("pip install", str(context.exception))


class ReadCommandTest(unittest.TestCase):
    def test_read_text_command_exit_paths_return_none(self) -> None:
        cases = (
            ("eof", mock.Mock(side_effect=EOFError)),
            ("blank", mock.Mock(return_value="   ")),
            ("korean_exit", mock.Mock(return_value="종료")),
            ("english_exit", mock.Mock(return_value="QUIT")),
        )
        for label, fake_input in cases:
            with self.subTest(case=label):
                with mock.patch("builtins.input", fake_input):
                    self.assertIsNone(demo_sc2._read_text_command())

    def test_read_text_command_strips_command_text(self) -> None:
        with mock.patch("builtins.input", mock.Mock(return_value="  상태 알려줘  ")):
            self.assertEqual("상태 알려줘", demo_sc2._read_text_command())

    def test_one_bad_recording_does_not_kill_the_voice_loop(self) -> None:
        # Transient capture/transcription errors print a Korean retry message
        # and return "" so the loop re-prompts.
        buffer = io.StringIO()
        with mock.patch("builtins.input", mock.Mock(return_value="")):
            with mock.patch.object(
                demo_sc2,
                "capture_voice_command",
                mock.Mock(side_effect=RuntimeError("PortAudio device unavailable")),
            ):
                with contextlib.redirect_stdout(buffer):
                    result = demo_sc2._read_voice_command(1.0)
        self.assertEqual("", result)
        self.assertIn("녹음/인식에 실패했습니다", buffer.getvalue())
        self.assertIn("다시 시도해 주세요", buffer.getvalue())

    def test_missing_voice_dependency_exits_gracefully_with_hint(self) -> None:
        buffer = io.StringIO()
        with mock.patch("builtins.input", mock.Mock(return_value="")):
            with mock.patch.object(
                demo_sc2,
                "capture_voice_command",
                mock.Mock(
                    side_effect=MissingVoiceDependencyError("pip install 안내 힌트")
                ),
            ):
                with contextlib.redirect_stdout(buffer):
                    result = demo_sc2._read_voice_command(1.0)
        self.assertIsNone(result)
        self.assertIn("pip install 안내 힌트", buffer.getvalue())

    def test_voice_exit_word_skips_recording(self) -> None:
        capture = mock.Mock()
        with mock.patch("builtins.input", mock.Mock(return_value="종료")):
            with mock.patch.object(demo_sc2, "capture_voice_command", capture):
                self.assertIsNone(demo_sc2._read_voice_command(1.0))
        capture.assert_not_called()


class VoiceCaptureTest(unittest.TestCase):
    def test_capture_voice_command_echoes_recognized_text(self) -> None:
        listener = FakeListener()
        transcriber = FakeTranscriber("SCV 계속 찍어")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            text = demo_sc2.capture_voice_command(
                2.0, listener=listener, transcriber=transcriber
            )
        self.assertEqual("SCV 계속 찍어", text)
        self.assertEqual([2.0], listener.recorded_seconds)
        self.assertEqual([("waveform", 2.0)], transcriber.received_audio)
        self.assertIn("인식된 명령: SCV 계속 찍어", buffer.getvalue())

    def test_low_confidence_transcription_is_reprompted_not_executed(self) -> None:
        # Whisper silence hallucinations come back with low confidence; they
        # must never be forwarded to the interpreter as game commands.
        listener = FakeListener()
        transcriber = FakeTranscriber("시청해 주셔서 감사합니다", confidence=0.12)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            text = demo_sc2.capture_voice_command(
                2.0, listener=listener, transcriber=transcriber
            )
        self.assertEqual("", text)
        self.assertIn("신뢰도가 낮습니다", buffer.getvalue())
        self.assertNotIn("인식된 명령", buffer.getvalue())

    def test_confident_transcription_passes_through(self) -> None:
        listener = FakeListener()
        transcriber = FakeTranscriber("SCV 계속 찍어", confidence=0.93)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            text = demo_sc2.capture_voice_command(
                2.0, listener=listener, transcriber=transcriber
            )
        self.assertEqual("SCV 계속 찍어", text)

    @unittest.skipIf(SOUNDDEVICE_INSTALLED, "sounddevice is installed in this environment")
    def test_capture_voice_command_raises_actionable_error_without_deps(self) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            with self.assertRaises(MissingVoiceDependencyError) as context:
                demo_sc2.capture_voice_command(0.5, listener=MicrophoneListener())
        self.assertIn("pip install", str(context.exception))


class ImportIsolationTest(unittest.TestCase):
    def test_module_imports_without_optional_runtime_dependencies(self) -> None:
        script = (
            "import json, sys; "
            "import starcraft_commander.demo_sc2; "
            "print(json.dumps({"
            "'sc2_loaded': 'sc2' in sys.modules, "
            "'faster_whisper_loaded': 'faster_whisper' in sys.modules, "
            "'sounddevice_loaded': 'sounddevice' in sys.modules"
            "}, sort_keys=True))"
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout)
        for key in ("sc2_loaded", "faster_whisper_loaded", "sounddevice_loaded"):
            with self.subTest(module=key):
                self.assertFalse(payload[key])


if __name__ == "__main__":
    unittest.main()
