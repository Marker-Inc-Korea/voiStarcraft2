"""Optional runtime dependency guards for TextCraft Commander.

The commander packages must stay importable without StarCraft II, python-sc2
(the maintained ``burnysc2`` fork providing the ``sc2`` package),
faster-whisper, or sounddevice installed. Code that needs one of those
optional runtimes calls the ``require_*`` guards below, which lazily import
the dependency and raise an actionable bilingual error when it is absent.

This module is pure stdlib and safe to import everywhere.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Final

__all__ = [
    "FASTER_WHISPER_INSTALL_HINT",
    "FASTER_WHISPER_MODULE_NAME",
    "MissingSC2RuntimeError",
    "MissingVoiceDependencyError",
    "PYTHON_SC2_INSTALL_HINT",
    "PYTHON_SC2_MODULE_NAME",
    "SOUNDDEVICE_INSTALL_HINT",
    "SOUNDDEVICE_MODULE_NAME",
    "is_faster_whisper_available",
    "is_python_sc2_available",
    "is_sounddevice_available",
    "require_faster_whisper",
    "require_python_sc2",
    "require_sounddevice",
]

PYTHON_SC2_MODULE_NAME: Final[str] = "sc2"
"""Importable module name provided by the burnysc2 distribution."""

FASTER_WHISPER_MODULE_NAME: Final[str] = "faster_whisper"
"""Importable module name provided by the faster-whisper distribution."""

SOUNDDEVICE_MODULE_NAME: Final[str] = "sounddevice"
"""Importable module name provided by the sounddevice distribution."""

PYTHON_SC2_INSTALL_HINT: Final[str] = (
    "The python-sc2 runtime (importable package 'sc2') is not installed. "
    "Install it with: pip install 'voistarcraft[sc2]' (or: pip install burnysc2). "
    "Running a real game also requires a local StarCraft II installation plus "
    "the map files. See docs/sc2-smoke-test.md for the local smoke-test guide. "
    "python-sc2 런타임('sc2' 패키지)이 설치되어 있지 않습니다. "
    "pip install 'voistarcraft[sc2]' 또는 pip install burnysc2 명령으로 설치하세요. "
    "실제 게임 실행에는 StarCraft II 본체와 맵 파일 설치도 필요합니다. "
    "자세한 절차는 docs/sc2-smoke-test.md 문서를 참고하세요."
)
"""Actionable bilingual guidance shown when the python-sc2 runtime is absent."""


def _build_voice_install_hint(pip_name: str, korean_role: str) -> str:
    """Return bilingual install guidance for one optional voice dependency."""

    return (
        f"The optional voice dependency '{pip_name}' is not installed. "
        "Install voice support with: pip install 'voistarcraft[voice]' "
        f"(or: pip install {pip_name}). "
        f"음성 명령의 {korean_role} 기능에 필요한 '{pip_name}' 패키지가 "
        "설치되어 있지 않습니다. pip install 'voistarcraft[voice]' 명령으로 "
        "음성 의존성을 설치하세요."
    )


FASTER_WHISPER_INSTALL_HINT: Final[str] = _build_voice_install_hint(
    "faster-whisper", "음성 인식(STT)"
)
"""Actionable bilingual guidance shown when faster-whisper is absent."""

SOUNDDEVICE_INSTALL_HINT: Final[str] = _build_voice_install_hint(
    "sounddevice", "마이크 입력"
)
"""Actionable bilingual guidance shown when sounddevice is absent."""


class MissingSC2RuntimeError(RuntimeError):
    """Raised when the optional python-sc2 (burnysc2) runtime is absent."""


class MissingVoiceDependencyError(RuntimeError):
    """Raised when an optional voice-input dependency is absent."""


def _import_optional_module(module_name: str) -> "ModuleType | None":
    """Import an optional module lazily, returning ``None`` when absent."""

    try:
        return importlib.import_module(module_name)
    except ImportError:
        return None


def is_python_sc2_available() -> bool:
    """Return whether the python-sc2 runtime ('sc2' package) is importable."""

    return _import_optional_module(PYTHON_SC2_MODULE_NAME) is not None


def require_python_sc2() -> ModuleType:
    """Return the lazily imported ``sc2`` package or raise an actionable error.

    Raises:
        MissingSC2RuntimeError: When the python-sc2 runtime is not installed.
    """

    try:
        return importlib.import_module(PYTHON_SC2_MODULE_NAME)
    except ImportError as error:
        raise MissingSC2RuntimeError(PYTHON_SC2_INSTALL_HINT) from error


def is_faster_whisper_available() -> bool:
    """Return whether the faster-whisper speech-to-text package is importable."""

    return _import_optional_module(FASTER_WHISPER_MODULE_NAME) is not None


def require_faster_whisper() -> ModuleType:
    """Return the lazily imported ``faster_whisper`` package or raise.

    Raises:
        MissingVoiceDependencyError: When faster-whisper is not installed.
    """

    try:
        return importlib.import_module(FASTER_WHISPER_MODULE_NAME)
    except ImportError as error:
        raise MissingVoiceDependencyError(FASTER_WHISPER_INSTALL_HINT) from error


def is_sounddevice_available() -> bool:
    """Return whether the sounddevice microphone-capture package is importable."""

    return _import_optional_module(SOUNDDEVICE_MODULE_NAME) is not None


def require_sounddevice() -> ModuleType:
    """Return the lazily imported ``sounddevice`` package or raise.

    Raises:
        MissingVoiceDependencyError: When sounddevice is not installed.
    """

    try:
        return importlib.import_module(SOUNDDEVICE_MODULE_NAME)
    except ImportError as error:
        raise MissingVoiceDependencyError(SOUNDDEVICE_INSTALL_HINT) from error
