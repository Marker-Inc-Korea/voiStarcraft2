"""Resolve runtime assets from a source checkout or an installed wheel."""

from __future__ import annotations

import os
from importlib.resources import files
from pathlib import Path
from typing import Final


_SOURCE_REPOSITORY_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
_MICROMACHINE_RELATIVE_ROOT: Final[Path] = Path("integrations/micromachine")
_MICROMACHINE_RESOURCE_PACKAGE: Final[str] = "integrations.micromachine"


def source_repository_root() -> Path | None:
    """Return the source checkout root, or ``None`` for installed packages."""

    if not (_SOURCE_REPOSITORY_ROOT / ".git").exists():
        return None
    source_assets = _SOURCE_REPOSITORY_ROOT / _MICROMACHINE_RELATIVE_ROOT
    if not (source_assets / "HOOK_MANIFEST.json").is_file():
        return None
    return _SOURCE_REPOSITORY_ROOT


def micromachine_data_root() -> Path:
    """Return the packaged MicroMachine asset root for this installation."""

    resource_root = files(_MICROMACHINE_RESOURCE_PACKAGE)
    try:
        return Path(os.fspath(resource_root))
    except TypeError as error:
        raise RuntimeError(
            "MicroMachine resources require an unpacked wheel installation."
        ) from error


def micromachine_data_path(relative_path: Path | str) -> Path:
    """Return one validated path below the MicroMachine asset root."""

    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts or relative == Path("."):
        raise ValueError("runtime data path must be a safe relative path")
    return micromachine_data_root() / relative
