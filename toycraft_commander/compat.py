"""Compatibility helpers for supported Python runtimes."""

from __future__ import annotations

from enum import Enum

try:
    from enum import StrEnum as StrEnum
except ImportError:  # pragma: no cover - exercised on Python < 3.11

    class StrEnum(str, Enum):
        """Small Python 3.10-compatible subset of enum.StrEnum."""

