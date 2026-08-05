"""Resolve runtime assets from a source checkout or an installed wheel."""

from __future__ import annotations

import sysconfig
from pathlib import Path
from typing import Final


_SOURCE_REPOSITORY_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
_MICROMACHINE_RELATIVE_ROOT: Final[Path] = Path("integrations/micromachine")
_INSTALLED_DATA_ROOT: Final[Path] = (
    Path(sysconfig.get_path("data"))
    / "share"
    / "voiStarcraft2"
    / _MICROMACHINE_RELATIVE_ROOT
)


def micromachine_data_root() -> Path:
    """Return the packaged MicroMachine asset root for this installation."""

    source_root = _SOURCE_REPOSITORY_ROOT / _MICROMACHINE_RELATIVE_ROOT
    if source_root.is_dir():
        return source_root
    return _INSTALLED_DATA_ROOT


def micromachine_data_path(relative_path: Path | str) -> Path:
    """Return one validated path below the MicroMachine asset root."""

    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts or relative == Path("."):
        raise ValueError("runtime data path must be a safe relative path")
    return micromachine_data_root() / relative
