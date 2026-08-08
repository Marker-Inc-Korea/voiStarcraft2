"""Resolve runtime assets from a source checkout or an installed wheel."""

from __future__ import annotations

import os
from importlib.resources import files
from pathlib import Path
from typing import Final


_SOURCE_REPOSITORY_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
_MICROMACHINE_RELATIVE_ROOT: Final[Path] = Path("integrations/micromachine")
_MICROMACHINE_RESOURCE_PACKAGE: Final[str] = "integrations.micromachine"
_SOURCE_PROJECT_NAME: Final[str] = "voiStarcraft2"
_SOURCE_MANIFEST_MARKER: Final[str] = (
    "recursive-include integrations/micromachine *.json *.hpp *.patch *.sh"
)
_SOURCE_ONLY_REPOSITORY_MARKER: Final[Path] = Path(".github/workflows/ci.yml")


def _source_repository_identity_matches(repository_root: Path) -> bool:
    if not (repository_root / _SOURCE_ONLY_REPOSITORY_MARKER).is_file():
        return False

    pyproject_path = repository_root / "pyproject.toml"
    manifest_path = repository_root / "MANIFEST.in"
    try:
        pyproject_lines = pyproject_path.read_text(encoding="utf-8").splitlines()
        manifest_lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return False

    section = ""
    project_name_matches = False
    for raw_line in pyproject_lines:
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line
            continue
        if section != "[project]" or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "name":
            project_name_matches = value.strip() in {
                f'"{_SOURCE_PROJECT_NAME}"',
                f"'{_SOURCE_PROJECT_NAME}'",
            }
            break

    return project_name_matches and _SOURCE_MANIFEST_MARKER in {
        line.strip() for line in manifest_lines
    }


def source_repository_root() -> Path | None:
    """Return the source checkout root, or ``None`` for installed packages."""

    if not (_SOURCE_REPOSITORY_ROOT / ".git").exists():
        return None
    if not _source_repository_identity_matches(_SOURCE_REPOSITORY_ROOT):
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
