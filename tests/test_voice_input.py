import json
import math
import pathlib
import subprocess
import sys
import types
import unittest
from unittest import mock

from starcraft_commander.voice_input import (
    DEFAULT_VOICE_TRANSCRIBER,
    VOICE_INSTALL_HINT,
    FasterWhisperTranscriber,
    MicrophoneListener,
    MissingVoiceDependencyError,
    VoiceTranscriberInterface,
    VoiceTranscription,
    clear_whisper_model_cache,
    transcribe_command_audio,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


class FakeSegment:
    """Scripted faster-whisper segment exposing only ``.text``."""

    def __init__(self, text: str) -> None:
        self.text = text


class FakeTranscriptionInfo:
    """Scripted faster-whisper info with duration/language fields."""

    def __init__(
        self,
        duration: float,
        language: str,
        language_probability: float | None = None,
    ) -> None:
        self.duration = duration
        self.language = language
        self.language_probability = language_probability


def make_fake_faster_whisper_module(
    segment_texts,
    *,
    info_duration: float = 1.8,
    info_language: str = "ko",
    info_language_probability: float | None = 0.94,
):
    """Build a fake ``faster_whisper`` module plus recorded call logs."""

    calls = {"constructed": [], "transcribed": []}

    class FakeWhisperModel:
        def __init__(self, model_size, device="auto", compute_type="int8"):
            calls["constructed"].append(
                {
                    "model_size": model_size,
                    "device": device,
                    "compute_type": compute_type,
                }
            )

        def transcribe(
            self,
            audio,
            language=None,
            vad_filter=False,
            condition_on_previous_text=True,
        ):
            calls["transcribed"].append(
                {
                    "audio": audio,
                    "language": language,
                    "vad_filter": vad_filter,
                    "condition_on_previous_text": condition_on_previous_text,
                }
            )
            segments = (FakeSegment(text) for text in segment_texts)
            info = FakeTranscriptionInfo(
                info_duration,
                info_language,
                info_language_probability,
            )
            return segments, info

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = FakeWhisperModel
    return module, calls


class FakeRecording:
    """Scripted sounddevice recording buffer with a ``flatten`` method."""

    def __init__(self, frames: int, channels: int) -> None:
        self.frames = frames
        self.channels = channels
        self.flatten_calls = 0

    def flatten(self):
        self.flatten_calls += 1
        return [0.0] * (self.frames * self.channels)


def make_fake_sounddevice_module():
    """Build a fake ``sounddevice`` module plus recorded call logs."""

    calls = {"rec": [], "wait": 0}
    recordings = []
    module = types.ModuleType("sounddevice")

    def rec(frames, samplerate=None, channels=None, dtype=None):
        calls["rec"].append(
            {
                "frames": frames,
                "samplerate": samplerate,
                "channels": channels,
                "dtype": dtype,
            }
        )
        recording = FakeRecording(frames, channels or 1)
        recordings.append(recording)
        return recording

    def wait():
        calls["wait"] += 1

    module.rec = rec
    module.wait = wait
    return module, calls, recordings


class ScriptedTranscriber:
    """Pure-Python transcriber satisfying VoiceTranscriberInterface."""

    def __init__(self, transcription: VoiceTranscription) -> None:
        self.transcription = transcription
        self.audio_calls = []

    def transcribe(self, audio: object) -> VoiceTranscription:
        self.audio_calls.append(audio)
        return self.transcription


class VoiceTranscriptionContractTest(unittest.TestCase):
    def test_valid_transcriptions_normalize_fields(self) -> None:
        cases = (
            (
                "korean command",
                VoiceTranscription(
                    text="마린 다섯 기 뽑아",
                    language="ko",
                    duration_seconds=2,
                    segments=["마린 다섯 기", "뽑아"],
                    confidence=0.97,
                ),
                "마린 다섯 기 뽑아",
                ("마린 다섯 기", "뽑아"),
                2.0,
                0.97,
            ),
            (
                "silence is a valid transcription",
                VoiceTranscription(text="", language="ko", duration_seconds=0.0),
                "",
                (),
                0.0,
                None,
            ),
        )
        for label, transcription, text, segments, duration, confidence in cases:
            with self.subTest(label=label):
                self.assertEqual(transcription.text, text)
                self.assertEqual(transcription.segments, segments)
                self.assertIsInstance(transcription.segments, tuple)
                self.assertEqual(transcription.duration_seconds, duration)
                self.assertIsInstance(transcription.duration_seconds, float)
                self.assertEqual(transcription.confidence, confidence)

    def test_invalid_transcriptions_rejected(self) -> None:
        cases = (
            (
                "negative duration",
                ValueError,
                dict(text="마린", language="ko", duration_seconds=-0.1),
            ),
            (
                "non-finite duration",
                ValueError,
                dict(text="마린", language="ko", duration_seconds=math.nan),
            ),
            (
                "empty language",
                ValueError,
                dict(text="마린", language="  ", duration_seconds=1.0),
            ),
            (
                "non-string text",
                TypeError,
                dict(text=123, language="ko", duration_seconds=1.0),
            ),
            (
                "string segments",
                TypeError,
                dict(text="마린", language="ko", duration_seconds=1.0, segments="마린"),
            ),
            (
                "non-finite confidence",
                ValueError,
                dict(
                    text="마린",
                    language="ko",
                    duration_seconds=1.0,
                    confidence=math.inf,
                ),
            ),
        )
        for label, error_type, kwargs in cases:
            with self.subTest(label=label):
                with self.assertRaises(error_type):
                    VoiceTranscription(**kwargs)

    def test_to_dict_is_json_ready(self) -> None:
        transcription = VoiceTranscription(
            text="마린 다섯 기 뽑아",
            language="ko",
            duration_seconds=1.8,
            segments=("마린 다섯 기", "뽑아"),
            confidence=0.94,
        )
        payload = transcription.to_dict()
        self.assertEqual(
            payload,
            {
                "text": "마린 다섯 기 뽑아",
                "language": "ko",
                "duration_seconds": 1.8,
                "segments": ["마린 다섯 기", "뽑아"],
                "confidence": 0.94,
            },
        )
        round_trip = json.loads(json.dumps(payload, ensure_ascii=False))
        self.assertEqual(round_trip, payload)


class MissingVoiceDependencyTest(unittest.TestCase):
    def setUp(self) -> None:
        clear_whisper_model_cache()
        self.addCleanup(clear_whisper_model_cache)

    def test_missing_faster_whisper_raises_actionable_error(self) -> None:
        with mock.patch.dict(sys.modules, {"faster_whisper": None}):
            with self.assertRaises(MissingVoiceDependencyError) as caught:
                FasterWhisperTranscriber().transcribe("command.wav")
        message = str(caught.exception)
        for hint in (
            "faster_whisper",
            "pip install",
            "voiStarcraft2[voice]",
            "faster-whisper sounddevice",
        ):
            with self.subTest(hint=hint):
                self.assertIn(hint, message)

    def test_missing_sounddevice_raises_actionable_error(self) -> None:
        with mock.patch.dict(sys.modules, {"sounddevice": None}):
            with self.assertRaises(MissingVoiceDependencyError) as caught:
                MicrophoneListener().record_seconds(1.0)
        message = str(caught.exception)
        for hint in ("sounddevice", "pip install", "voiStarcraft2[voice]"):
            with self.subTest(hint=hint):
                self.assertIn(hint, message)

    def test_error_type_and_hint_surface(self) -> None:
        self.assertTrue(issubclass(MissingVoiceDependencyError, RuntimeError))
        self.assertIn("pip install", VOICE_INSTALL_HINT)

    def test_module_imports_without_voice_dependencies(self) -> None:
        code = (
            "import sys\n"
            "sys.modules['faster_whisper'] = None\n"
            "sys.modules['sounddevice'] = None\n"
            "import starcraft_commander.voice_input as voice\n"
            "transcriber = voice.FasterWhisperTranscriber()\n"
            "listener = voice.MicrophoneListener()\n"
            "print(transcriber.model_size, transcriber.language,"
            " listener.sample_rate, listener.channels)\n"
        )
        completed = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.split(), ["small", "ko", "16000", "1"])


class FasterWhisperTranscriberTest(unittest.TestCase):
    def setUp(self) -> None:
        clear_whisper_model_cache()
        self.addCleanup(clear_whisper_model_cache)

    def test_transcribe_joins_stripped_segments_into_korean_text(self) -> None:
        module, calls = make_fake_faster_whisper_module(
            [" 마린 다섯 기 ", "뽑아 ", "   "],
        )
        with mock.patch.dict(sys.modules, {"faster_whisper": module}):
            result = FasterWhisperTranscriber().transcribe("command.wav")
        self.assertEqual(result.text, "마린 다섯 기 뽑아")
        self.assertEqual(result.segments, ("마린 다섯 기", "뽑아"))
        self.assertEqual(result.language, "ko")
        self.assertEqual(result.duration_seconds, 1.8)
        self.assertEqual(result.confidence, 0.94)
        self.assertEqual(
            calls["constructed"],
            [{"model_size": "small", "device": "auto", "compute_type": "int8"}],
        )
        self.assertEqual(
            calls["transcribed"],
            [
                {
                    "audio": "command.wav",
                    "language": "ko",
                    # Anti-hallucination defaults: silence in push-to-talk
                    # clips must never become a Korean game command.
                    "vad_filter": True,
                    "condition_on_previous_text": False,
                }
            ],
        )

    def test_vad_and_conditioning_are_configurable_per_transcriber(self) -> None:
        module, calls = make_fake_faster_whisper_module(["마린 뽑아"])
        with mock.patch.dict(sys.modules, {"faster_whisper": module}):
            FasterWhisperTranscriber(
                vad_filter=False,
                condition_on_previous_text=True,
            ).transcribe("command.wav")
        self.assertFalse(calls["transcribed"][0]["vad_filter"])
        self.assertTrue(calls["transcribed"][0]["condition_on_previous_text"])

    def test_korean_command_text_flows_unaltered(self) -> None:
        commands = (
            "마린 다섯 기 뽑아",
            "SCV 계속 찍어",
            "본진 입구 방어해",
            "앞마당 확장해",
        )
        for command in commands:
            with self.subTest(command=command):
                clear_whisper_model_cache()
                module, _ = make_fake_faster_whisper_module([command])
                with mock.patch.dict(sys.modules, {"faster_whisper": module}):
                    result = transcribe_command_audio("command.wav")
                self.assertEqual(result.text, command)
                self.assertEqual(result.segments, (command,))

    def test_silence_transcribes_to_empty_text(self) -> None:
        module, _ = make_fake_faster_whisper_module([], info_duration=0.4)
        with mock.patch.dict(sys.modules, {"faster_whisper": module}):
            result = FasterWhisperTranscriber().transcribe("silence.wav")
        self.assertEqual(result.text, "")
        self.assertEqual(result.segments, ())
        self.assertEqual(result.duration_seconds, 0.4)

    def test_whisper_model_constructed_once_and_cached_per_config(self) -> None:
        module, calls = make_fake_faster_whisper_module(["마린 다섯 기 뽑아"])
        transcriber = FasterWhisperTranscriber()
        with mock.patch.dict(sys.modules, {"faster_whisper": module}):
            transcriber.transcribe("first.wav")
            transcriber.transcribe("second.wav")
            FasterWhisperTranscriber().transcribe("third.wav")
        self.assertEqual(len(calls["constructed"]), 1)
        self.assertEqual(len(calls["transcribed"]), 3)

    def test_custom_configuration_reaches_model_and_call(self) -> None:
        module, calls = make_fake_faster_whisper_module(
            ["build five marines"],
            info_language="en",
            info_language_probability=None,
        )
        transcriber = FasterWhisperTranscriber(
            model_size="base",
            language="en",
            device="cpu",
            compute_type="float32",
        )
        with mock.patch.dict(sys.modules, {"faster_whisper": module}):
            result = transcriber.transcribe("command.wav")
        self.assertEqual(
            calls["constructed"],
            [{"model_size": "base", "device": "cpu", "compute_type": "float32"}],
        )
        self.assertEqual(calls["transcribed"][0]["language"], "en")
        self.assertEqual(result.language, "en")
        self.assertIsNone(result.confidence)

    def test_invalid_configuration_rejected(self) -> None:
        cases = (
            ("empty model_size", dict(model_size=" ")),
            ("empty language", dict(language="")),
            ("empty device", dict(device="  ")),
            ("empty compute_type", dict(compute_type="")),
        )
        for label, kwargs in cases:
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    FasterWhisperTranscriber(**kwargs)


class MicrophoneListenerTest(unittest.TestCase):
    def test_record_seconds_uses_sounddevice_and_flattens(self) -> None:
        module, calls, recordings = make_fake_sounddevice_module()
        with mock.patch.dict(sys.modules, {"sounddevice": module}):
            audio = MicrophoneListener().record_seconds(1.5)
        self.assertEqual(
            calls["rec"],
            [
                {
                    "frames": 24000,
                    "samplerate": 16000,
                    "channels": 1,
                    "dtype": "float32",
                }
            ],
        )
        self.assertEqual(calls["wait"], 1)
        self.assertEqual(len(recordings), 1)
        self.assertEqual(recordings[0].flatten_calls, 1)
        self.assertEqual(len(audio), 24000)

    def test_custom_listener_configuration_reaches_rec(self) -> None:
        module, calls, _ = make_fake_sounddevice_module()
        listener = MicrophoneListener(sample_rate=44100, channels=2)
        with mock.patch.dict(sys.modules, {"sounddevice": module}):
            audio = listener.record_seconds(0.5)
        self.assertEqual(
            calls["rec"],
            [
                {
                    "frames": 22050,
                    "samplerate": 44100,
                    "channels": 2,
                    "dtype": "float32",
                }
            ],
        )
        self.assertEqual(len(audio), 22050 * 2)

    def test_record_seconds_rejects_non_positive_durations(self) -> None:
        module, calls, _ = make_fake_sounddevice_module()
        listener = MicrophoneListener()
        for seconds in (0, -1.0, math.nan, math.inf):
            with self.subTest(seconds=seconds):
                with mock.patch.dict(sys.modules, {"sounddevice": module}):
                    with self.assertRaises(ValueError):
                        listener.record_seconds(seconds)
        self.assertEqual(calls["rec"], [])

    def test_invalid_listener_configuration_rejected(self) -> None:
        cases = (
            ("zero sample_rate", dict(sample_rate=0)),
            ("negative sample_rate", dict(sample_rate=-16000)),
            ("zero channels", dict(channels=0)),
        )
        for label, kwargs in cases:
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    MicrophoneListener(**kwargs)


class TranscribeCommandAudioTest(unittest.TestCase):
    def setUp(self) -> None:
        clear_whisper_model_cache()
        self.addCleanup(clear_whisper_model_cache)

    def test_default_transcriber_is_korean_faster_whisper(self) -> None:
        self.assertIsInstance(DEFAULT_VOICE_TRANSCRIBER, FasterWhisperTranscriber)
        self.assertEqual(DEFAULT_VOICE_TRANSCRIBER.language, "ko")
        self.assertEqual(DEFAULT_VOICE_TRANSCRIBER.model_size, "small")

    def test_default_path_uses_faster_whisper(self) -> None:
        module, calls = make_fake_faster_whisper_module(["마린 다섯 기 뽑아"])
        with mock.patch.dict(sys.modules, {"faster_whisper": module}):
            result = transcribe_command_audio("command.wav")
        self.assertEqual(result.text, "마린 다섯 기 뽑아")
        self.assertEqual(
            calls["transcribed"],
            [
                {
                    "audio": "command.wav",
                    "language": "ko",
                    "vad_filter": True,
                    "condition_on_previous_text": False,
                }
            ],
        )

    def test_custom_transcriber_is_used_verbatim(self) -> None:
        scripted = ScriptedTranscriber(
            VoiceTranscription(
                text="마린 다섯 기 뽑아",
                language="ko",
                duration_seconds=1.2,
                segments=("마린 다섯 기 뽑아",),
            )
        )
        result = transcribe_command_audio([0.0, 0.1, 0.0], transcriber=scripted)
        self.assertEqual(result.text, "마린 다섯 기 뽑아")
        self.assertEqual(scripted.audio_calls, [[0.0, 0.1, 0.0]])

    def test_transcriber_interface_is_runtime_checkable(self) -> None:
        scripted = ScriptedTranscriber(
            VoiceTranscription(text="", language="ko", duration_seconds=0.0)
        )
        cases = (
            ("scripted fake", scripted, True),
            ("faster whisper transcriber", FasterWhisperTranscriber(), True),
            ("non-transcriber", object(), False),
        )
        for label, candidate, expected in cases:
            with self.subTest(label=label):
                self.assertIs(
                    isinstance(candidate, VoiceTranscriberInterface),
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
