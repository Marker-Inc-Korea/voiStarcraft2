"""Resolve runtime assets from a source checkout or an installed wheel."""

from __future__ import annotations

import os
import re
import selectors
import stat
import subprocess
import sys
import time
from importlib.resources import files
from pathlib import Path
from typing import Final


_SOURCE_MODULE_LOCATION: Final[Path] = Path(__file__)
_SOURCE_MODULE_PATH: Final[Path] = _SOURCE_MODULE_LOCATION.resolve()
_SOURCE_REPOSITORY_ROOT: Final[Path] = _SOURCE_MODULE_PATH.parents[1]
_MICROMACHINE_RELATIVE_ROOT: Final[Path] = Path("integrations/micromachine")
_MICROMACHINE_RESOURCE_PACKAGE: Final[str] = "integrations.micromachine"
_SOURCE_MODULE_RELATIVE_PATH: Final[Path] = Path(
    "starcraft_commander/runtime_data.py"
)
_MICROMACHINE_LAUNCHER_RELATIVE_PATH: Final[Path] = (
    _MICROMACHINE_RELATIVE_ROOT / "scripts/smoke_macos_local.sh"
)
_SOURCE_IDENTITY_PATHS: Final[tuple[Path, ...]] = (
    Path(".github/workflows/ci.yml"),
    Path("MANIFEST.in"),
    Path("integrations/micromachine/HOOK_MANIFEST.json"),
    Path("pyproject.toml"),
    _MICROMACHINE_LAUNCHER_RELATIVE_PATH,
    _SOURCE_MODULE_RELATIVE_PATH,
)
_SOURCE_REPOSITORY_ANCHOR: Final[str] = (
    "23173dbb8d889d8828ddb6fbdab84b0d5e822476"
)
_SOURCE_REMOTE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?:https://github\.com/|ssh://git@github\.com/|git@github\.com:)"
    r"Marker-Inc-Korea/voiStarcraft2(?:\.git)?/?$",
    re.IGNORECASE,
)
_GIT_OBJECT_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{40}$")
_GIT_OUTPUT_LIMIT: Final[int] = 16 * 1024
_GIT_TIMEOUT_SECONDS: Final[float] = 5.0
_TRUSTED_GIT_EXECUTABLE: Final[str] = (
    "/Applications/Xcode.app/Contents/Developer/usr/bin/git"
    if sys.platform == "darwin"
    else "/usr/bin/git"
)


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        pass


def _bounded_git(
    repository_root: Path,
    *arguments: str,
) -> tuple[int, str] | None:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
        }
    )
    try:
        process = subprocess.Popen(
            [
                _TRUSTED_GIT_EXECUTABLE,
                "--no-pager",
                "--no-replace-objects",
                "-C",
                os.fspath(repository_root),
                *arguments,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
        )
    except OSError:
        return None

    output = bytearray()
    deadline = time.monotonic() + _GIT_TIMEOUT_SECONDS
    selector = selectors.DefaultSelector()
    assert process.stdout is not None
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _stop_process(process)
                return None
            events = selector.select(remaining)
            if not events:
                _stop_process(process)
                return None
            for key, _ in events:
                chunk = os.read(
                    key.fd,
                    min(4096, _GIT_OUTPUT_LIMIT + 1 - len(output)),
                )
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                output.extend(chunk)
                if len(output) > _GIT_OUTPUT_LIMIT:
                    _stop_process(process)
                    return None

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _stop_process(process)
            return None
        return_code = process.wait(timeout=remaining)
    except (OSError, subprocess.SubprocessError):
        _stop_process(process)
        return None
    finally:
        selector.close()
        process.stdout.close()

    try:
        return return_code, output.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None


def _git_output(repository_root: Path, *arguments: str) -> str | None:
    result = _bounded_git(repository_root, *arguments)
    if result is None or result[0] != 0:
        return None
    return result[1].strip()


def _git_path_is_absent(repository_root: Path, *arguments: str) -> bool:
    raw_path = _git_output(repository_root, *arguments)
    if not raw_path or "\n" in raw_path or "\x00" in raw_path:
        return False
    path = Path(raw_path)
    if not path.is_absolute():
        path = repository_root / path
    try:
        return not path.exists() and not path.is_symlink()
    except OSError:
        return False


def _head_blob_matches_working_file(
    repository_root: Path,
    relative_path: Path,
) -> bool:
    path = repository_root / relative_path
    try:
        working_stat = os.lstat(path)
        if not stat.S_ISREG(working_stat.st_mode):
            return False
    except OSError:
        return False

    relative = relative_path.as_posix()
    tree_entry = _git_output(
        repository_root,
        "ls-tree",
        "HEAD",
        "--",
        relative,
    )
    if tree_entry is None or "\n" in tree_entry or "\t" not in tree_entry:
        return False
    metadata, listed_path = tree_entry.split("\t", 1)
    fields = metadata.split()
    if (
        len(fields) != 3
        or fields[0] not in {"100644", "100755"}
        or fields[1] != "blob"
        or not _GIT_OBJECT_PATTERN.fullmatch(fields[2])
        or listed_path != relative
    ):
        return False
    expected_mode = 0o755 if fields[0] == "100755" else 0o644
    if stat.S_IMODE(working_stat.st_mode) != expected_mode:
        return False

    working_blob = _git_output(
        repository_root,
        "hash-object",
        "--no-filters",
        "--",
        relative,
    )
    return working_blob == fields[2]


def _source_repository_identity_matches(repository_root: Path) -> bool:
    try:
        if repository_root.resolve(strict=True) != repository_root:
            return False
        if _SOURCE_MODULE_LOCATION.is_symlink():
            return False
        if (
            (repository_root / _SOURCE_MODULE_RELATIVE_PATH).resolve(strict=True)
            != _SOURCE_MODULE_PATH
        ):
            return False
    except OSError:
        return False

    if _git_output(repository_root, "rev-parse", "--is-inside-work-tree") != "true":
        return False
    top_level = _git_output(repository_root, "rev-parse", "--show-toplevel")
    if top_level is None or "\n" in top_level or "\x00" in top_level:
        return False
    try:
        if Path(top_level).resolve(strict=True) != repository_root:
            return False
    except OSError:
        return False

    replace_refs = _git_output(
        repository_root,
        "for-each-ref",
        "--format=%(refname)",
        "refs/replace/",
    )
    if replace_refs is None or replace_refs:
        return False
    if (
        _git_output(repository_root, "rev-parse", "--is-shallow-repository")
        != "false"
    ):
        return False
    if not _git_path_is_absent(
        repository_root,
        "rev-parse",
        "--git-path",
        "info/grafts",
    ):
        return False
    if not _git_path_is_absent(
        repository_root,
        "rev-parse",
        "--git-path",
        "objects/info/alternates",
    ):
        return False

    head = _git_output(
        repository_root,
        "rev-parse",
        "--verify",
        "HEAD^{commit}",
    )
    if head is None or not _GIT_OBJECT_PATTERN.fullmatch(head):
        return False
    anchor = _git_output(
        repository_root,
        "rev-parse",
        "--verify",
        f"{_SOURCE_REPOSITORY_ANCHOR}^{{commit}}",
    )
    if anchor != _SOURCE_REPOSITORY_ANCHOR:
        return False
    ancestry = _bounded_git(
        repository_root,
        "merge-base",
        "--is-ancestor",
        _SOURCE_REPOSITORY_ANCHOR,
        head,
    )
    if ancestry is None or ancestry != (0, ""):
        return False

    remote_urls = _git_output(
        repository_root,
        "config",
        "--local",
        "--get-all",
        "remote.origin.url",
    )
    if (
        remote_urls is None
        or "\n" in remote_urls
        or not _SOURCE_REMOTE_PATTERN.fullmatch(remote_urls)
    ):
        return False

    return all(
        _head_blob_matches_working_file(repository_root, relative_path)
        for relative_path in _SOURCE_IDENTITY_PATHS
    )


def source_repository_root() -> Path | None:
    """Return the source checkout root, or ``None`` for installed packages."""

    if not _source_repository_identity_matches(_SOURCE_REPOSITORY_ROOT):
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
