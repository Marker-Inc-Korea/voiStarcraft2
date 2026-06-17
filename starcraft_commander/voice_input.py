"""Korean voice-input boundary: speech-to-text feeding the text command pipeline.

"말하면 스타가 움직인다." This module turns spoken Korean commands into plain
text that the existing interpreter pipeline consumes unchanged. It is
importable and testable without ``faster-whisper``, ``sounddevice``,
StarCraft II, or ``python-sc2`` installed: optional voice dependencies are
imported lazily inside the methods that need them, and a missing dependency
raises :class:`MissingVoiceDependencyError` with an actionable install hint.

Push-to-talk loops and CLI wiring live in the CLI layer, not here. This module
only owns the minimal capture and transcription seams.
"""

from __future__ import annotations

import importlib
import math
from dataclasses import dataclass
from typing import Any, Final, Protocol, runtime_checkable

__all__ = [
    "DEFAULT_VOICE_TRANSCRIBER",
    "FasterWhisperTranscriber",
    "MicrophoneListener",
    "MissingVoiceDependencyError",
    "VOICE_INSTALL_HINT",
    "VoiceTranscriberInterface",
    "VoiceTranscription",
    "clear_whisper_model_cache",
    "transcribe_command_audio",
]


VOICE_INSTALL_HINT: Final[str] = (
    "pip install 'voiStarcraft2[voice]' 또는 pip install faster-whisper sounddevice"
)
"""User-facing install hint for the optional voice dependency extras."""


class MissingVoiceDependencyError(RuntimeError):
    """Raised when an optional voice dependency is not installed.

    The message always carries the install commands so users can fix the
    environment without reading source code.
    """


def _import_voice_dependency(module_name: str, *, feature: str) -> Any:
    """Import an optional voice dependency or raise an actionable error."""

    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        raise MissingVoiceDependencyError(
            f"{feature} 기능에 필요한 음성 의존성 {module_name!r}이(가) "
            f"설치되어 있지 않습니다. 설치 방법: {VOICE_INSTALL_HINT}"
        ) from exc


@dataclass(frozen=True)
class VoiceTranscription:
    """One speech-to-text result ready for the text command pipeline.

    ``text`` may be empty: silence is a valid transcription and the CLI layer
    decides whether to re-prompt. ``confidence`` is optional and, when present,
    is typically the model's language probability in ``[0, 1]``.
    """

    text: str
    language: str
    duration_seconds: float
    segments: tuple[str, ...] = ()
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("Voice transcription text must be a string.")
        if not str(self.language).strip():
            raise ValueError("Voice transcription language must be non-empty.")
        duration = float(self.duration_seconds)
        if not math.isfinite(duration):
            raise ValueError("Voice transcription duration_seconds must be finite.")
        if duration < 0:
            raise ValueError("Voice transcription duration_seconds cannot be negative.")
        if isinstance(self.segments, (str, bytes)):
            raise TypeError("Voice transcription segments must be a sequence of strings.")
        confidence = self.confidence
        if confidence is not None:
            confidence = float(confidence)
            if not math.isfinite(confidence):
                raise ValueError("Voice transcription confidence must be finite.")
        object.__setattr__(self, "language", str(self.language))
        object.__setattr__(self, "duration_seconds", duration)
        object.__setattr__(self, "segments", tuple(str(item) for item in self.segments))
        object.__setattr__(self, "confidence", confidence)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready transcription payload."""

        return {
            "text": self.text,
            "language": self.language,
            "duration_seconds": self.duration_seconds,
            "segments": list(self.segments),
            "confidence": self.confidence,
        }


@runtime_checkable
class VoiceTranscriberInterface(Protocol):
    """Speech-to-text boundary from raw audio to one command transcription."""

    def transcribe(self, audio: object) -> VoiceTranscription:
        """Transcribe a file path string or numpy-like float waveform."""


_WHISPER_MODEL_CACHE: Final[dict[tuple[str, str, str], Any]] = {}
"""Process-wide WhisperModel cache keyed by (model_size, device, compute_type).

The transcriber dataclass is frozen, so the loaded model lives here instead of
on the instance. Transcribers with identical model configuration share one
loaded model; ``language`` is excluded from the key because it is passed per
``transcribe`` call. Use :func:`clear_whisper_model_cache` to reset.
"""


def clear_whisper_model_cache() -> None:
    """Drop every cached WhisperModel instance (tests and config reloads)."""

    _WHISPER_MODEL_CACHE.clear()


@dataclass(frozen=True)
class FasterWhisperTranscriber:
    """Korean speech-to-text via faster-whisper, loaded lazily on first use.

    ``faster_whisper`` is imported inside :meth:`transcribe`, so constructing
    this dataclass never touches optional dependencies. The underlying
    ``WhisperModel`` is constructed once per configuration and cached in the
    module-level :data:`_WHISPER_MODEL_CACHE`.

    ``vad_filter`` defaults on and ``condition_on_previous_text`` defaults
    off: push-to-talk clips always carry trailing silence, on which Whisper
    notoriously hallucinates Korean filler text that would otherwise be fed
    into the command interpreter as if the commander had spoken it. Both are
    per-call transcription arguments, so they stay out of the model cache key.
    """

    model_size: str = "small"
    language: str = "ko"
    device: str = "auto"
    compute_type: str = "int8"
    vad_filter: bool = True
    condition_on_previous_text: bool = False

    def __post_init__(self) -> None:
        for field_name in ("model_size", "language", "device", "compute_type"):
            value = getattr(self, field_name)
            if not str(value).strip():
                raise ValueError(
                    f"Faster-whisper transcriber {field_name} must be non-empty."
                )

    def transcribe(self, audio: object) -> VoiceTranscription:
        """Transcribe a file path or float waveform into Korean command text.

        Segment texts are stripped of surrounding whitespace, empty segments
        are dropped, and the remaining segments are joined with single spaces.
        """

        model = self._load_model()
        segments, info = model.transcribe(
            audio,
            language=self.language,
            vad_filter=self.vad_filter,
            condition_on_previous_text=self.condition_on_previous_text,
        )
        segment_texts = tuple(
            stripped
            for stripped in (
                str(getattr(segment, "text", "")).strip() for segment in segments
            )
            if stripped
        )
        duration = float(getattr(info, "duration", 0.0) or 0.0)
        language = str(getattr(info, "language", "") or self.language)
        raw_confidence = getattr(info, "language_probability", None)
        confidence = float(raw_confidence) if raw_confidence is not None else None
        return VoiceTranscription(
            text=" ".join(segment_texts),
            language=language,
            duration_seconds=duration,
            segments=segment_texts,
            confidence=confidence,
        )

    def _load_model(self) -> Any:
        """Return the cached WhisperModel, constructing it on first use."""

        cache_key = (self.model_size, self.device, self.compute_type)
        cached = _WHISPER_MODEL_CACHE.get(cache_key)
        if cached is not None:
            return cached
        faster_whisper = _import_voice_dependency(
            "faster_whisper",
            feature="음성 인식(STT)",
        )
        model = faster_whisper.WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type,
        )
        _WHISPER_MODEL_CACHE[cache_key] = model
        return model


@dataclass(frozen=True)
class MicrophoneListener:
    """Minimal one-shot microphone capture for push-to-talk CLI layers.

    ``sounddevice`` is imported inside :meth:`record_seconds`, so constructing
    this dataclass never touches audio hardware or optional dependencies.
    """

    sample_rate: int = 16000
    channels: int = 1

    def __post_init__(self) -> None:
        if int(self.sample_rate) <= 0:
            raise ValueError("Microphone sample_rate must be positive.")
        if int(self.channels) <= 0:
            raise ValueError("Microphone channels must be positive.")
        object.__setattr__(self, "sample_rate", int(self.sample_rate))
        object.__setattr__(self, "channels", int(self.channels))

    def record_seconds(self, seconds: float) -> Any:
        """Record one clip and return the flattened float32 waveform."""

        duration = float(seconds)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError(
                "Microphone recording seconds must be a positive finite number."
            )
        sounddevice = _import_voice_dependency("sounddevice", feature="마이크 녹음")
        frame_count = max(1, int(round(duration * self.sample_rate)))
        recording = sounddevice.rec(
            frame_count,
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="float32",
        )
        sounddevice.wait()
        flatten = getattr(recording, "flatten", None)
        if callable(flatten):
            return flatten()
        return recording


DEFAULT_VOICE_TRANSCRIBER: Final[FasterWhisperTranscriber] = FasterWhisperTranscriber()
"""Default Korean transcriber used by :func:`transcribe_command_audio`."""


def transcribe_command_audio(
    audio: object,
    transcriber: VoiceTranscriberInterface | None = None,
) -> VoiceTranscription:
    """Transcribe one command clip, defaulting to the Korean faster-whisper setup."""

    active = transcriber if transcriber is not None else DEFAULT_VOICE_TRANSCRIBER
    return active.transcribe(audio)
