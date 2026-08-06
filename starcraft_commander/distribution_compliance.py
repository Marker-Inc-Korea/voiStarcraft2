"""Fail-closed distribution, licensing, and private-config verification."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unicodedata
import venv
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Final

try:
    import yaml as _yaml
except ImportError:  # pragma: no cover - fail-closed behavior is tested by mock.
    _yaml = None

try:
    import tomllib as _toml
except ImportError:  # pragma: no cover - Python 3.10 uses the dev dependency.
    try:
        import tomli as _toml
    except ImportError:  # pragma: no cover - fail-closed behavior is tested.
        _toml = None


DISTRIBUTION_COMPLIANCE_SCHEMA_VERSION: Final[int] = 2
EXPECTED_LICENSE_EXPRESSION: Final[str] = (
    "AGPL-3.0-or-later OR LicenseRef-Commercial"
)
EXPECTED_PROJECT_NAME: Final[str] = "voiStarcraft2"
EXPECTED_DISTRIBUTION_NAME: Final[str] = "voistarcraft2"
EXPECTED_TOP_LEVEL_PACKAGES: Final[tuple[str, ...]] = (
    "broodwar_commander",
    "integrations",
    "starcraft_commander",
    "toycraft_commander",
)
EXPECTED_LICENSE_FILE_SHA256: Final[Mapping[str, str]] = {
    "LICENSE": "888136505768579bc729c27a60a5adc9360ef41fb0b05fc3a0bb2a49bfad8b9a",
    "LICENSES/AGPL-3.0-or-later.txt": (
        "0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0"
    ),
    "THIRD_PARTY_NOTICES.md": (
        "cfa0d0ed9d877198f700febedb4162ce55df8f8a1702d5c0063625222fed3d41"
    ),
}
PRODUCT_PACKAGE_ROOTS: Final[frozenset[str]] = frozenset(
    {"broodwar_commander", "starcraft_commander", "toycraft_commander"}
)
REQUIRED_LICENSE_FILES: Final[tuple[str, ...]] = (
    "LICENSE",
    "LICENSES/AGPL-3.0-or-later.txt",
    "THIRD_PARTY_NOTICES.md",
)
REQUIRED_RUNTIME_FILES: Final[tuple[str, ...]] = (
    "HOOK_MANIFEST.json",
    "MICROMACHINE_MAP_POOL.json",
    "PRE_LIVE_JOURNEYS.json",
    "PRE_LIVE_PRODUCERS.json",
)
EXPECTED_PROJECT_DISTRIBUTIONS: Final[frozenset[str]] = frozenset(
    {
        "anthropic",
        "build",
        "burnysc2",
        "faster-whisper",
        "openai",
        "pytest",
        "pyyaml",
        "sounddevice",
        "tomli",
    }
)
EXPECTED_BUILD_DISTRIBUTIONS: Final[frozenset[str]] = frozenset({"setuptools"})
EXPECTED_BUILD_BACKEND_VERSION: Final[str] = "82.0.1"
EXPECTED_BUILD_BACKEND_REQUIREMENT: Final[str] = (
    f"setuptools=={EXPECTED_BUILD_BACKEND_VERSION}"
)
EXPECTED_BUILD_BACKEND_GENERATOR: Final[str] = (
    f"setuptools ({EXPECTED_BUILD_BACKEND_VERSION})"
)
EXPECTED_METADATA_VERSION: Final[str] = "2.4"
EXPECTED_DYNAMIC_METADATA_FIELDS: Final[tuple[str, ...]] = ("license-file",)
EXPECTED_DIRECT_DISTRIBUTIONS: Final[frozenset[str]] = (
    EXPECTED_PROJECT_DISTRIBUTIONS | EXPECTED_BUILD_DISTRIBUTIONS
)
EXPECTED_NOTICE_LICENSES: Final[Mapping[str, str]] = {
    "anthropic": "MIT",
    "build": "MIT",
    "burnysc2": "MIT",
    "faster-whisper": "MIT",
    "openai": "Apache-2.0",
    "pytest": "MIT",
    "pyyaml": "MIT",
    "setuptools": "MIT",
    "sounddevice": "MIT",
    "tomli": "MIT",
}
MAX_ARCHIVE_ENTRIES: Final[int] = 4096
MAX_ARCHIVE_MEMBER_BYTES: Final[int] = 64 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES: Final[int] = 256 * 1024 * 1024
MAX_SCAN_FILE_BYTES: Final[int] = 64 * 1024 * 1024
MAX_CONFIGURATION_BYTES: Final[int] = 8 * 1024 * 1024
MAX_CONFIGURATION_DEPTH: Final[int] = 64
MAX_CONFIGURATION_NODES: Final[int] = 16384
MAX_GIT_OUTPUT_BYTES: Final[int] = 128 * 1024 * 1024
MAX_YAML_ALIASES: Final[int] = 128
MAX_YAML_DEPTH: Final[int] = 64
MAX_YAML_DOCUMENTS: Final[int] = 64
MAX_YAML_NODES: Final[int] = 16384
_SDIST_ROOT_FILES: Final[frozenset[str]] = frozenset(
    {
        "LICENSE",
        "MANIFEST.in",
        "PKG-INFO",
        "README.md",
        "THIRD_PARTY_NOTICES.md",
        "pyproject.toml",
        "setup.cfg",
    }
)
_EGG_INFO_FILES: Final[frozenset[str]] = frozenset(
    {
        "PKG-INFO",
        "SOURCES.txt",
        "dependency_links.txt",
        "requires.txt",
        "top_level.txt",
    }
)
_DIST_INFO_FILES: Final[frozenset[str]] = frozenset(
    {"METADATA", "RECORD", "WHEEL", "top_level.txt"}
)

if _yaml is not None:

    class _BoundedSafeLoader(_yaml.SafeLoader):
        def __init__(self, stream: object) -> None:
            super().__init__(stream)
            self._voi_alias_count = 0
            self._voi_depth = 0
            self._voi_node_count = 0

        def compose_node(self, parent: object, index: object) -> object:
            if self.check_event(_yaml.events.AliasEvent):
                self._voi_alias_count += 1
                if self._voi_alias_count > MAX_YAML_ALIASES:
                    raise _yaml.YAMLError("YAML alias limit exceeded")
            self._voi_node_count += 1
            if self._voi_node_count > MAX_YAML_NODES:
                raise _yaml.YAMLError("YAML node limit exceeded")
            self._voi_depth += 1
            if self._voi_depth > MAX_YAML_DEPTH:
                raise _yaml.YAMLError("YAML nesting limit exceeded")
            try:
                return super().compose_node(parent, index)
            finally:
                self._voi_depth -= 1

else:
    _BoundedSafeLoader = None
_DENIED_PATH_COMPONENTS: Final[frozenset[str]] = frozenset(
    {
        ".codex",
        ".codex-recovery",
        ".git",
        ".github",
        ".ouroboros",
        "__pycache__",
        "docs",
        "tests",
    }
)
_NOTICE_DISTRIBUTION_RE: Final[re.Pattern[str]] = re.compile(
    r"(?m)^### Python distribution: `([A-Za-z0-9_.-]+)`\s*$"
)
_NOTICE_DISTRIBUTION_LICENSE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?ms)^### Python distribution: `([A-Za-z0-9_.-]+)`\s*$"
    r"(.*?)(?=^### Python distribution: |\Z)"
)
_REQUIREMENT_NAME_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)"
)
_CONFIG_ASSIGNMENT_PREFIX: Final[str] = (
    r"(?:^|[{\[(,;]|&&|\|\||^[ \t]*-[ \t]+|"
    r"(?:^|[ \t])(?:ENV|ARG|env)[ \t]+|"
    r"os\.environ\[[ \t]*)"
    r"[ \t]*(?:(?:RUN|then|do)[ \t]+)*(?:export[ \t]+)?[\"']?"
)
_CONFIG_ASSIGNMENT_KEY_SUFFIX: Final[str] = (
    r"[\"']?(?:[ \t]*\])?(?![A-Za-z0-9_])"
)
_CONFIG_ASSIGNMENT_SEPARATOR: Final[str] = (
    r"[ \t]*(?:(?::[^=\n,}]+)?=|:(?![^\n,}]*=))[ \t]*"
)
_PRIVATE_MODEL_KEY_PATTERN: Final[str] = (
    r"(?:(?:DEFAULT|VOI|CODEX)_)?MYPROXY_MODEL"
)
_PRIVATE_ENDPOINT_KEY_PATTERN: Final[str] = (
    r"(?:(?:DEFAULT|VOI|CODEX)_)?MYPROXY_"
    r"(?:OPENAI_BASE_URL|BASE_URL|HOST|PORT)"
)
_PRIVATE_MODEL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?im)"
    + _CONFIG_ASSIGNMENT_PREFIX
    + _PRIVATE_MODEL_KEY_PATTERN
    + _CONFIG_ASSIGNMENT_KEY_SUFFIX
    + _CONFIG_ASSIGNMENT_SEPARATOR
    + r"[\"']?([^\"'\s,#}\n]+)[\"']?"
)
_PRIVATE_ENDPOINT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?im)"
    + _CONFIG_ASSIGNMENT_PREFIX
    + _PRIVATE_ENDPOINT_KEY_PATTERN
    + _CONFIG_ASSIGNMENT_KEY_SUFFIX
    + _CONFIG_ASSIGNMENT_SEPARATOR
    + r"[\"']?([^\"'\s,#}\n]+)[\"']?"
)
_PRIVATE_MODEL_DOCKER_RE: Final[re.Pattern[str]] = re.compile(
    r"(?im)^[ \t]*(?:ENV|ARG)[ \t]+(?:"
    + _PRIVATE_MODEL_KEY_PATTERN
    + _CONFIG_ASSIGNMENT_KEY_SUFFIX
    + r"[ \t]+|[^\n]*?[ \t]+"
    + _PRIVATE_MODEL_KEY_PATTERN
    + _CONFIG_ASSIGNMENT_KEY_SUFFIX
    + r"[ \t]*=[ \t]*)[\"']?([^\"'\s,#}\n]+)[\"']?"
)
_PRIVATE_ENDPOINT_DOCKER_RE: Final[re.Pattern[str]] = re.compile(
    r"(?im)^[ \t]*(?:ENV|ARG)[ \t]+(?:"
    + _PRIVATE_ENDPOINT_KEY_PATTERN
    + _CONFIG_ASSIGNMENT_KEY_SUFFIX
    + r"[ \t]+|[^\n]*?[ \t]+"
    + _PRIVATE_ENDPOINT_KEY_PATTERN
    + _CONFIG_ASSIGNMENT_KEY_SUFFIX
    + r"[ \t]*=[ \t]*)[\"']?([^\"'\s,#}\n]+)[\"']?"
)
_PRIVATE_MODEL_ENV_CALL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?im)\b(?:os\.)?(?:putenv|environ\.setdefault)[ \t]*\([ \t]*"
    r"[\"']"
    + _PRIVATE_MODEL_KEY_PATTERN
    + r"[\"'][ \t]*,[ \t]*[\"']([^\"'\n]+)[\"']"
)
_PRIVATE_ENDPOINT_ENV_CALL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?im)\b(?:os\.)?(?:putenv|environ\.setdefault)[ \t]*\([ \t]*"
    r"[\"']"
    + _PRIVATE_ENDPOINT_KEY_PATTERN
    + r"[\"'][ \t]*,[ \t]*[\"']([^\"'\n]+)[\"']"
)
_MYPROXY_CLI_MODEL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?im)^(?=[^\n]*(?:--provider(?:[ \t]+|=)[\"']?myproxy\b))"
    r"[^\n]*?--model(?:[ \t]+|=)[\"']?([^\"'\s\\]+)"
)
_MYPROXY_CLI_ENDPOINT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?im)^(?=[^\n]*(?:--provider(?:[ \t]+|=)[\"']?myproxy\b))"
    r"[^\n]*?--(?:base-url|openai-base-url)(?:[ \t]+|=)"
    r"[\"']?([^\"'\s\\]+)"
)
_PRIVATE_MODEL_KUBERNETES_RE: Final[re.Pattern[str]] = re.compile(
    r"(?im)(?:"
    r"\{"
    r"(?=[^{}]*[\"']?name[\"']?[ \t]*:[ \t]*[\"']?"
    + _PRIVATE_MODEL_KEY_PATTERN
    + r"[\"']?(?![A-Za-z0-9_]))"
    r"(?=[^{}]*[\"']?value[\"']?[ \t]*:[ \t]*[\"']?"
    r"([^\"'\s,#}\n]+)[\"']?)[^{}]*\}"
    r"|^[ \t]*-[ \t]*[\"']?name[\"']?[ \t]*:[ \t]*[\"']?"
    + _PRIVATE_MODEL_KEY_PATTERN
    + r"[\"']?[ \t]*(?:#[^\n]*)?\r?\n"
    r"(?:(?![ \t]*-[ \t])[ \t]+[^\n]*\r?\n)*?"
    r"[ \t]+[\"']?value[\"']?[ \t]*:[ \t]*[\"']?"
    r"([^\"'\s,#}\n]+)[\"']?"
    r"|^[ \t]*-[ \t]*[\"']?value[\"']?[ \t]*:[ \t]*[\"']?"
    r"([^\"'\s,#}\n]+)[\"']?[ \t]*(?:#[^\n]*)?\r?\n"
    r"(?:(?![ \t]*-[ \t])[ \t]+[^\n]*\r?\n)*?"
    r"[ \t]+[\"']?name[\"']?[ \t]*:[ \t]*[\"']?"
    + _PRIVATE_MODEL_KEY_PATTERN
    + r"[\"']?(?![A-Za-z0-9_]))"
)
_PRIVATE_ENDPOINT_KUBERNETES_RE: Final[re.Pattern[str]] = re.compile(
    r"(?im)(?:"
    r"\{"
    r"(?=[^{}]*[\"']?name[\"']?[ \t]*:[ \t]*[\"']?"
    + _PRIVATE_ENDPOINT_KEY_PATTERN
    + r"[\"']?(?![A-Za-z0-9_]))"
    r"(?=[^{}]*[\"']?value[\"']?[ \t]*:[ \t]*[\"']?"
    r"([^\"'\s,#}\n]+)[\"']?)[^{}]*\}"
    r"|^[ \t]*-[ \t]*[\"']?name[\"']?[ \t]*:[ \t]*[\"']?"
    + _PRIVATE_ENDPOINT_KEY_PATTERN
    + r"[\"']?[ \t]*(?:#[^\n]*)?\r?\n"
    r"(?:(?![ \t]*-[ \t])[ \t]+[^\n]*\r?\n)*?"
    r"[ \t]+[\"']?value[\"']?[ \t]*:[ \t]*[\"']?"
    r"([^\"'\s,#}\n]+)[\"']?"
    r"|^[ \t]*-[ \t]*[\"']?value[\"']?[ \t]*:[ \t]*[\"']?"
    r"([^\"'\s,#}\n]+)[\"']?[ \t]*(?:#[^\n]*)?\r?\n"
    r"(?:(?![ \t]*-[ \t])[ \t]+[^\n]*\r?\n)*?"
    r"[ \t]+[\"']?name[\"']?[ \t]*:[ \t]*[\"']?"
    + _PRIVATE_ENDPOINT_KEY_PATTERN
    + r"[\"']?(?![A-Za-z0-9_]))"
)
_API_KEY_RE: Final[re.Pattern[str]] = re.compile(
    r"\b("
    r"sk-(?:proj-)?[A-Za-z0-9_-]{12,}|"
    r"gh[pousr]_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}"
    r")\b"
)
_BEARER_TOKEN_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)\bBearer\s+([A-Za-z0-9._~+/=-]{12,})"
)
_AWS_ACCESS_KEY_ID_RE: Final[re.Pattern[str]] = re.compile(
    r"\b((?:AKIA|ASIA)[A-Z0-9]{16})\b"
)
_SECRET_ASSIGNMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?im)"
    + _CONFIG_ASSIGNMENT_PREFIX
    + r"(?:AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN|"
    + r"AZURE_CLIENT_SECRET|GOOGLE_API_KEY)"
    + _CONFIG_ASSIGNMENT_KEY_SUFFIX
    + _CONFIG_ASSIGNMENT_SEPARATOR
    + r"[\"']?([A-Za-z0-9._~+/=-]{12,})"
)
_PRIVATE_KEY_RE: Final[re.Pattern[str]] = re.compile(
    r"-----BEGIN[ \t]+("
    r"(?:RSA|DSA|EC|OPENSSH|PGP)[ \t]+)?PRIVATE[ \t]+KEY-----"
)
_ENV_KEY_ASSIGNMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?im)"
    + _CONFIG_ASSIGNMENT_PREFIX
    + r"(?:[A-Z][A-Z0-9_]{1,63}_)?API_KEY"
    + _CONFIG_ASSIGNMENT_KEY_SUFFIX
    + _CONFIG_ASSIGNMENT_SEPARATOR
    + r"[\"']?([A-Za-z0-9._~+/=-]{12,})"
)
_ENV_KEY_CALL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?im)\b(?:os\.)?(?:putenv|environ\.setdefault)[ \t]*\([ \t]*"
    r"[\"'](?:[A-Z][A-Z0-9_]{1,63}_)?API_KEY[\"'][ \t]*,[ \t]*"
    r"[\"']([A-Za-z0-9._~+/=-]{12,})[\"']"
)
_CREDENTIAL_PATH_RE: Final[re.Pattern[str]] = re.compile(
    r"(?im)(?:"
    + _CONFIG_ASSIGNMENT_PREFIX
    + r"(?:credential(?:s)?_?(?:file|path)|"
    + r"GOOGLE_APPLICATION_CREDENTIALS|AWS_SHARED_CREDENTIALS_FILE)"
    + _CONFIG_ASSIGNMENT_KEY_SUFFIX
    + _CONFIG_ASSIGNMENT_SEPARATOR
    + r"(?:"
    + r"[\"'][^\"'\n]*(?:\.aws[\\/]credentials|\.netrc|\.pypirc|_netrc|"
    + r"id_(?:rsa|ed25519)|credentials\.json|"
    + r"[A-Za-z0-9_.-]+\.credentials\.json)[^\"'\n]*[\"']|"
    + r"[^\"'\s,#}\n]*(?:\.aws[\\/]credentials|\.netrc|\.pypirc|_netrc|"
    + r"id_(?:rsa|ed25519)|credentials\.json|"
    + r"[A-Za-z0-9_.-]+\.credentials\.json)[^\"'\s,#}\n]*)|"
    + r"\b(?:Path|open)[ \t]*\([ \t]*(?:[rubf]{0,2})?[\"']"
    + r"[^\"'\n]*(?:\.aws[\\/]credentials|\.netrc|\.pypirc|_netrc|"
    + r"id_(?:rsa|ed25519)|credentials\.json|"
    + r"[A-Za-z0-9_.-]+\.credentials\.json)[^\"'\n]*[\"'])"
)
_SAFE_FIXTURE_FINGERPRINTS: Final[
    Mapping[str, Mapping[str, frozenset[str]]]
] = {
    "tests/test_llm_interpreter.py": {
        "api_key": frozenset(
            {"6d7cff6a74f5a16be41aaf6ccc8d51b62357cf1115f4149e6d116b537f96b302"}
        ),
        "api_key_assignment": frozenset(
            {"822682667b73f0a72661c0b19f9c79d6cdb98e57559309482bd5b15e0dc7325b"}
        ),
    },
    "tests/test_micromachine_pre_live_provenance.py": {
        "bearer_token": frozenset(
            {"2a452e8451a18651177c8cfeff71b5c6d1e8fd1d1faab95968b77e83cd7efd09"}
        )
    },
    "tests/test_web_gui.py": {
        "api_key": frozenset(
            {
                "3ecb7ee0b6df1921344b76307f224695e417e4a63f638471b2428b6f7c54c355",
                "76bb8593aa73556c30f24f5e7f47d401383e5815b4117c4b0f0430398d93f544",
            }
        )
    },
}
_CREDENTIAL_FILENAMES: Final[frozenset[str]] = frozenset(
    {
        ".netrc",
        ".pypirc",
        "_netrc",
        "credentials.json",
        "id_ed25519",
        "id_rsa",
    }
)
_WINDOWS_RESERVED_COMPONENTS: Final[frozenset[str]] = frozenset(
    {
        "aux",
        "con",
        "nul",
        "prn",
        *(f"com{number}" for number in range(1, 10)),
        *(f"lpt{number}" for number in range(1, 10)),
    }
)


@dataclass(frozen=True)
class ArchiveSnapshot:
    """Bounded archive contents plus structural blockers."""

    kind: str
    path: Path
    digest: str
    entries: tuple[str, ...]
    files: Mapping[str, bytes]
    blockers: tuple[Mapping[str, object], ...]
    directories: tuple[str, ...] = ()


def canonical_json_text(value: object) -> str:
    """Return deterministic, human-readable JSON."""

    return json.dumps(
        value,
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
        separators=(",", ": "),
    ) + "\n"


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def inspect_wheel(path: Path) -> ArchiveSnapshot:
    """Read one wheel with traversal, duplicate, link, and size checks."""

    blockers: list[dict[str, object]] = []
    files: dict[str, bytes] = {}
    directories: list[str] = []
    entries: list[str] = []
    total_bytes = 0
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            blockers.append(
                {
                    "code": "archive_entry_limit_exceeded",
                    "kind": "wheel",
                    "observed": len(infos),
                    "limit": MAX_ARCHIVE_ENTRIES,
                }
            )
        seen: set[str] = set()
        for info in infos[: MAX_ARCHIVE_ENTRIES + 1]:
            name = info.filename
            entries.append(name)
            path_error = _archive_path_error(name)
            if path_error:
                blockers.append(
                    {
                        "code": "unsafe_archive_entry",
                        "kind": "wheel",
                        "entry": name,
                        "reason": path_error,
                    }
                )
                continue
            canonical_name = _canonical_archive_name(name)
            portable_key = _portable_archive_key(canonical_name)
            if portable_key in seen:
                blockers.append(
                    {
                        "code": "duplicate_archive_entry",
                        "kind": "wheel",
                        "entry": name,
                    }
                )
                continue
            seen.add(portable_key)
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                blockers.append(
                    {
                        "code": "archive_link_entry",
                        "kind": "wheel",
                        "entry": name,
                    }
                )
                continue
            if mode not in {0, stat.S_IFREG, stat.S_IFDIR}:
                blockers.append(
                    {
                        "code": "archive_non_regular_entry",
                        "kind": "wheel",
                        "entry": name,
                        "mode": oct(mode),
                    }
                )
                continue
            if info.is_dir():
                if mode == stat.S_IFREG:
                    blockers.append(
                        {
                            "code": "archive_entry_type_mismatch",
                            "kind": "wheel",
                            "entry": name,
                        }
                    )
                    continue
                directories.append(name)
                continue
            if mode == stat.S_IFDIR:
                blockers.append(
                    {
                        "code": "archive_entry_type_mismatch",
                        "kind": "wheel",
                        "entry": name,
                    }
                )
                continue
            if info.flag_bits & 0x1:
                blockers.append(
                    {
                        "code": "encrypted_archive_entry",
                        "kind": "wheel",
                        "entry": name,
                    }
                )
                continue
            if info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                blockers.append(
                    {
                        "code": "archive_member_limit_exceeded",
                        "kind": "wheel",
                        "entry": name,
                        "observed": info.file_size,
                        "limit": MAX_ARCHIVE_MEMBER_BYTES,
                    }
                )
                continue
            total_bytes += info.file_size
            if total_bytes > MAX_ARCHIVE_TOTAL_BYTES:
                blockers.append(
                    {
                        "code": "archive_total_limit_exceeded",
                        "kind": "wheel",
                        "observed": total_bytes,
                        "limit": MAX_ARCHIVE_TOTAL_BYTES,
                    }
                )
                break
            files[name] = archive.read(info)
    return ArchiveSnapshot(
        kind="wheel",
        path=path,
        digest=sha256_file(path),
        entries=tuple(sorted(entries)),
        files=files,
        blockers=tuple(blockers),
        directories=tuple(sorted(directories)),
    )


def inspect_sdist(path: Path) -> ArchiveSnapshot:
    """Read one source distribution with strict regular-file semantics."""

    blockers: list[dict[str, object]] = []
    files: dict[str, bytes] = {}
    directories: list[str] = []
    entries: list[str] = []
    total_bytes = 0
    expected_root = _expected_sdist_root(path)
    if not expected_root:
        blockers.append(
            {
                "code": "invalid_sdist_filename",
                "kind": "sdist",
                "filename": path.name,
            }
        )
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        if len(members) > MAX_ARCHIVE_ENTRIES:
            blockers.append(
                {
                    "code": "archive_entry_limit_exceeded",
                    "kind": "sdist",
                    "observed": len(members),
                    "limit": MAX_ARCHIVE_ENTRIES,
                }
            )
        seen: set[str] = set()
        for member in members[: MAX_ARCHIVE_ENTRIES + 1]:
            name = member.name
            entries.append(name)
            path_error = _archive_path_error(name)
            if path_error:
                blockers.append(
                    {
                        "code": "unsafe_archive_entry",
                        "kind": "sdist",
                        "entry": name,
                        "reason": path_error,
                    }
                )
                continue
            canonical_name = _canonical_archive_name(name)
            portable_key = _portable_archive_key(canonical_name)
            archive_path = PurePosixPath(canonical_name)
            if (
                not expected_root
                or not archive_path.parts
                or archive_path.parts[0] != expected_root
            ):
                blockers.append(
                    {
                        "code": "invalid_archive_root",
                        "kind": "sdist",
                        "entry": name,
                        "expected_root": expected_root,
                    }
                )
                continue
            if portable_key in seen:
                blockers.append(
                    {
                        "code": "duplicate_archive_entry",
                        "kind": "sdist",
                        "entry": name,
                    }
                )
                continue
            seen.add(portable_key)
            if member.isdir():
                directories.append(name)
                continue
            if member.issym() or member.islnk():
                blockers.append(
                    {
                        "code": "archive_link_entry",
                        "kind": "sdist",
                        "entry": name,
                    }
                )
                continue
            if not member.isfile():
                blockers.append(
                    {
                        "code": "archive_special_entry",
                        "kind": "sdist",
                        "entry": name,
                    }
                )
                continue
            if member.size > MAX_ARCHIVE_MEMBER_BYTES:
                blockers.append(
                    {
                        "code": "archive_member_limit_exceeded",
                        "kind": "sdist",
                        "entry": name,
                        "observed": member.size,
                        "limit": MAX_ARCHIVE_MEMBER_BYTES,
                    }
                )
                continue
            total_bytes += member.size
            if total_bytes > MAX_ARCHIVE_TOTAL_BYTES:
                blockers.append(
                    {
                        "code": "archive_total_limit_exceeded",
                        "kind": "sdist",
                        "observed": total_bytes,
                        "limit": MAX_ARCHIVE_TOTAL_BYTES,
                    }
                )
                break
            extracted = archive.extractfile(member)
            if extracted is None:
                blockers.append(
                    {
                        "code": "archive_member_unreadable",
                        "kind": "sdist",
                        "entry": name,
                    }
                )
                continue
            files[name] = extracted.read()
    return ArchiveSnapshot(
        kind="sdist",
        path=path,
        digest=sha256_file(path),
        entries=tuple(sorted(entries)),
        files=files,
        blockers=tuple(blockers),
        directories=tuple(sorted(directories)),
    )


def archive_content_blockers(snapshot: ArchiveSnapshot) -> list[dict[str, object]]:
    """Return explicit package allowlist and denylist violations."""

    blockers: list[dict[str, object]] = []
    for entry in snapshot.directories:
        path_error = _archive_path_error(entry)
        if path_error:
            blockers.append(
                {
                    "code": "unsafe_archive_entry",
                    "kind": snapshot.kind,
                    "entry": entry,
                    "reason": path_error,
                }
            )
            continue
        directory_findings = scan_payload(
            "<archive-directory>",
            entry.encode("utf-8"),
            allow_safe_fixtures=False,
        )
        if directory_findings:
            blockers.extend(
                {
                    "code": "sensitive_archive_directory",
                    "kind": snapshot.kind,
                    "rule_id": finding.get("rule_id"),
                    "fingerprint": finding.get("fingerprint"),
                }
                for finding in directory_findings
            )
            continue
        canonical_name = _canonical_archive_name(entry)
        if (
            snapshot.kind == "sdist"
            and canonical_name == _expected_sdist_root(snapshot.path)
        ):
            continue
        relative = _archive_relative_path(
            snapshot.kind,
            snapshot.path,
            entry,
        )
        if relative is None:
            blockers.append(
                {
                    "code": "invalid_archive_root",
                    "kind": snapshot.kind,
                    "entry": entry,
                }
            )
            continue
        denied = _denied_distribution_path(relative)
        if denied:
            blockers.append(
                {
                    "code": "denied_archive_entry",
                    "kind": snapshot.kind,
                    "entry": entry,
                    "reason": denied,
                }
            )
            continue
        allowed = (
            _allowed_wheel_directory(relative, snapshot.path)
            if snapshot.kind == "wheel"
            else _allowed_sdist_directory(relative)
        )
        if not allowed:
            blockers.append(
                {
                    "code": "unexpected_archive_entry",
                    "kind": snapshot.kind,
                    "entry": entry,
                }
            )
    for entry in snapshot.files:
        path_error = _archive_path_error(entry)
        if path_error:
            blockers.append(
                {
                    "code": "unsafe_archive_entry",
                    "kind": snapshot.kind,
                    "entry": entry,
                    "reason": path_error,
                }
            )
            continue
        relative = _archive_relative_path(
            snapshot.kind,
            snapshot.path,
            entry,
        )
        if relative is None:
            blockers.append(
                {
                    "code": "invalid_archive_root",
                    "kind": snapshot.kind,
                    "entry": entry,
                }
            )
            continue
        denied = _denied_distribution_path(relative)
        if denied:
            blockers.append(
                {
                    "code": "denied_archive_entry",
                    "kind": snapshot.kind,
                    "entry": entry,
                    "reason": denied,
                }
            )
            continue
        allowed = (
            _allowed_wheel_path(relative, snapshot.path)
            if snapshot.kind == "wheel"
            else _allowed_sdist_path(relative)
        )
        if not allowed:
            blockers.append(
                {
                    "code": "unexpected_archive_entry",
                    "kind": snapshot.kind,
                    "entry": entry,
                }
            )
    return blockers


def scan_payload(
    path: str,
    payload: bytes,
    *,
    allow_safe_fixtures: bool = True,
) -> list[dict[str, object]]:
    """Return redacted findings for one path and payload."""

    normalized_path = path.replace("\\", "/")
    if normalized_path.startswith("./"):
        normalized_path = normalized_path[2:]
    findings: list[dict[str, object]] = []
    path_parts = PurePosixPath(normalized_path).parts
    lowered_parts = {part.lower() for part in path_parts}
    basename = PurePosixPath(normalized_path).name.lower()
    if any(
        part.lower() == ".env" or part.lower().startswith(".env.")
        for part in path_parts
    ):
        findings.append(_path_finding(normalized_path, "env_file"))
    if _is_credential_path(basename, lowered_parts):
        findings.append(_path_finding(normalized_path, "credential_file"))
    if len(payload) > MAX_SCAN_FILE_BYTES:
        findings.append(
            {
                "path": normalized_path,
                "line": 0,
                "rule_id": "scan_file_limit_exceeded",
                "fingerprint": sha256_bytes(
                    f"scan_file_limit_exceeded\0{normalized_path}".encode()
                ),
            }
        )
        return findings
    text, decode_failure = _decode_scan_payload(normalized_path, payload)
    if decode_failure:
        findings.append(_path_finding(normalized_path, decode_failure))
    rules: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("api_key", _API_KEY_RE),
        ("aws_access_key_id", _AWS_ACCESS_KEY_ID_RE),
        ("secret_assignment", _SECRET_ASSIGNMENT_RE),
        ("bearer_token", _BEARER_TOKEN_RE),
        ("private_key", _PRIVATE_KEY_RE),
        ("api_key_assignment", _ENV_KEY_ASSIGNMENT_RE),
        ("api_key_assignment", _ENV_KEY_CALL_RE),
        ("private_endpoint", _PRIVATE_ENDPOINT_RE),
        ("private_endpoint", _PRIVATE_ENDPOINT_DOCKER_RE),
        ("private_endpoint", _PRIVATE_ENDPOINT_ENV_CALL_RE),
        ("private_endpoint", _MYPROXY_CLI_ENDPOINT_RE),
        ("private_endpoint", _PRIVATE_ENDPOINT_KUBERNETES_RE),
        ("private_model_override", _PRIVATE_MODEL_RE),
        ("private_model_override", _PRIVATE_MODEL_DOCKER_RE),
        ("private_model_override", _PRIVATE_MODEL_ENV_CALL_RE),
        ("private_model_override", _MYPROXY_CLI_MODEL_RE),
        ("private_model_override", _PRIVATE_MODEL_KUBERNETES_RE),
        ("credential_path", _CREDENTIAL_PATH_RE),
    )
    scan_texts, configuration_failures = _configuration_scan_texts(
        normalized_path,
        text,
    )
    dockerfile_text = _dockerfile_logical_text(normalized_path, text)
    if dockerfile_text not in scan_texts:
        scan_texts = (*scan_texts, dockerfile_text)
    for candidate in tuple(scan_texts):
        joined_literals = _joined_quoted_literal_text(candidate)
        if joined_literals not in scan_texts:
            scan_texts = (*scan_texts, joined_literals)
    findings.extend(
        _path_finding(normalized_path, rule_id)
        for rule_id in configuration_failures
    )
    prior_match_counts: dict[tuple[str, str], int] = {}
    for scan_index, scan_text in enumerate(scan_texts):
        local_match_counts: dict[tuple[str, str], int] = {}
        for rule_id, pattern in rules:
            for match in pattern.finditer(scan_text):
                matched = match.group(0)
                captured = next(
                    (
                        group.strip()
                        for group in match.groups()
                        if group is not None
                    ),
                    "",
                )
                identity = (rule_id, captured)
                occurrence = local_match_counts.get(identity, 0) + 1
                local_match_counts[identity] = occurrence
                line = scan_text.count("\n", 0, match.start()) + 1
                if (
                    scan_index
                    and occurrence <= prior_match_counts.get(identity, 0)
                ):
                    continue
                if (
                    rule_id == "private_model_override"
                    and captured == "configured-locally"
                ):
                    continue
                if allow_safe_fixtures and _safe_fixture_match(
                    normalized_path,
                    rule_id,
                    matched,
                ):
                    continue
                findings.append(
                    {
                        "path": normalized_path,
                        "line": line,
                        "rule_id": rule_id,
                        "fingerprint": sha256_bytes(
                            f"{rule_id}\0{matched}".encode("utf-8")
                        ),
                    }
                )
        for identity, count in local_match_counts.items():
            prior_match_counts[identity] = max(
                prior_match_counts.get(identity, 0),
                count,
            )
    return findings


def _decode_scan_payload(path: str, payload: bytes) -> tuple[str, str]:
    suffix = PurePosixPath(path).suffix.lower()
    is_configuration = suffix in {".json", ".toml", ".yaml", ".yml"}
    encoding = "utf-8"
    if payload.startswith(b"\xef\xbb\xbf"):
        encoding = "utf-8-sig"
    elif payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        encoding = "utf-16"
    elif is_configuration and len(payload) >= 4 and len(payload) % 2 == 0:
        sample = payload[: min(len(payload), 128)]
        even_nuls = sample[0::2].count(0)
        odd_nuls = sample[1::2].count(0)
        if odd_nuls > len(sample[1::2]) // 3 and not even_nuls:
            encoding = "utf-16-le"
        elif even_nuls > len(sample[0::2]) // 3 and not odd_nuls:
            encoding = "utf-16-be"
    try:
        return payload.decode(
            encoding,
            errors="strict" if is_configuration else "replace",
        ), ""
    except UnicodeError:
        return payload.decode("utf-8", errors="replace"), (
            "configuration_decode_failed"
        )


def _joined_quoted_literal_text(text: str) -> str:
    pattern = re.compile(
        r"""(["'])([^"'\\\r\n]*)\1[ \t]*(["'])([^"'\\\r\n]*)\3"""
    )
    normalized = text
    for _ in range(128):
        updated, count = pattern.subn(
            lambda match: (
                f'{match.group(1)}{match.group(2)}'
                f'{match.group(4)}{match.group(1)}'
            ),
            normalized,
        )
        normalized = updated
        if not count:
            break
    return normalized


def _dockerfile_logical_text(path: str, text: str) -> str:
    basename = PurePosixPath(path).name.lower()
    if not (
        basename in {"containerfile", "dockerfile"}
        or basename.startswith(("containerfile.", "dockerfile."))
        or basename.endswith((".containerfile", ".dockerfile"))
    ):
        return text
    directive_pattern = re.compile(
        r"^[ \t]*#[ \t]*(syntax|escape|check)[ \t]*="
        r"[ \t]*(.*?)[ \t]*$",
        re.IGNORECASE,
    )
    escape = "\\"
    directive_lines: set[int] = set()
    physical_lines = text.splitlines()
    for line_number, raw_line in enumerate(physical_lines):
        match = directive_pattern.fullmatch(raw_line)
        if match is None:
            break
        directive_lines.add(line_number)
        if (
            match.group(1).lower() == "escape"
            and match.group(2) in {"\\", "`"}
        ):
            escape = match.group(2)

    logical_lines: list[str] = []
    buffer = ""
    continuing = False
    for line_number, raw_line in enumerate(physical_lines):
        if not continuing and line_number in directive_lines:
            logical_lines.append(raw_line)
            continue
        if raw_line.lstrip(" \t").startswith("#"):
            if continuing:
                continue
            logical_lines.append(raw_line)
            continue
        if continuing and not raw_line.strip():
            continue
        fragment = raw_line.lstrip(" \t") if continuing else raw_line
        buffer += fragment
        stripped = buffer.rstrip(" \t")
        if stripped.endswith(escape):
            buffer = stripped[:-1]
            continuing = True
            continue
        logical_lines.append(buffer)
        buffer = ""
        continuing = False
    if buffer:
        logical_lines.append(buffer)
    normalized = "\n".join(logical_lines)
    if text.endswith(("\n", "\r")):
        normalized += "\n"
    return normalized


def _configuration_scan_texts(
    path: str,
    text: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    suffix = PurePosixPath(path).suffix.lower()
    if suffix not in {".json", ".toml", ".yaml", ".yml"}:
        return (text,), ()

    variants = [text]
    failures: list[str] = []
    if len(text.encode("utf-8")) > MAX_CONFIGURATION_BYTES:
        return (text,), ("configuration_limit_exceeded",)
    decoded_text = _decoded_configuration_escapes(path, text)
    if decoded_text != text:
        variants.append(decoded_text)
    if suffix == ".json":
        parsed = None
        for candidate in tuple(variants):
            try:
                parsed = json.loads(candidate)
                semantic_text = _semantic_mapping_scan_text((parsed,))
            except (RecursionError, TypeError, ValueError, json.JSONDecodeError):
                continue
            canonical = json.dumps(
                parsed,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            for normalized in (canonical, semantic_text):
                if normalized and normalized not in variants:
                    variants.append(normalized)
            break
        if parsed is None:
            failures.append("json_parse_failed")
    elif suffix in {".yaml", ".yml"}:
        semantic_text, failure = _yaml_semantic_scan_text(text)
        if failure:
            failures.append(failure)
        elif semantic_text and semantic_text not in variants:
            variants.append(semantic_text)
        for candidate in tuple(variants):
            normalized = _yaml_double_quoted_line_continuations(candidate)
            if normalized not in variants:
                variants.append(normalized)
    else:
        semantic_text, failure = _toml_semantic_scan_text(text)
        if failure:
            failures.append(failure)
        elif semantic_text and semantic_text not in variants:
            variants.append(semantic_text)
    return tuple(variants), tuple(failures)


def _toml_semantic_scan_text(text: str) -> tuple[str, str]:
    if _toml is None:
        return "", "toml_parser_unavailable"
    try:
        parsed = _toml.loads(text)
        return _semantic_mapping_scan_text((parsed,)), ""
    except (RecursionError, TypeError, ValueError):
        return "", "toml_parse_failed"


def _yaml_semantic_scan_text(text: str) -> tuple[str, str]:
    if _yaml is None or _BoundedSafeLoader is None:
        return "", "yaml_parser_unavailable"
    try:
        documents: list[object] = []
        for document_count, document in enumerate(
            _yaml.load_all(text, Loader=_BoundedSafeLoader),
            start=1,
        ):
            if document_count > MAX_YAML_DOCUMENTS:
                raise _yaml.YAMLError("YAML document limit exceeded")
            documents.append(document)
        return _semantic_mapping_scan_text(documents), ""
    except (
        RecursionError,
        TypeError,
        ValueError,
        _yaml.YAMLError,
    ):
        return "", "yaml_parse_failed"


def _semantic_mapping_scan_text(values: Sequence[object]) -> str:
    fragments: list[str] = []
    visited: set[int] = set()
    node_count = 0

    def visit(value: object, depth: int) -> None:
        nonlocal node_count
        node_count += 1
        if node_count > MAX_CONFIGURATION_NODES:
            raise ValueError("semantic configuration node limit exceeded")
        if depth > MAX_CONFIGURATION_DEPTH:
            raise ValueError("semantic configuration nesting limit exceeded")
        if isinstance(value, Mapping):
            identity = id(value)
            if identity in visited:
                return
            visited.add(identity)
            scalar_entries: dict[str, str] = {}
            for raw_key, raw_value in value.items():
                key = _configuration_scalar_text(raw_key)
                item = _configuration_scalar_text(raw_value)
                if key is not None and item is not None:
                    scalar_entries[key] = item
            if scalar_entries:
                fragments.append(
                    json.dumps(
                        scalar_entries,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                )
            for nested in value.values():
                visit(nested, depth + 1)
            return
        if isinstance(value, Sequence) and not isinstance(
            value,
            (str, bytes, bytearray),
        ):
            identity = id(value)
            if identity in visited:
                return
            visited.add(identity)
            for nested in value:
                visit(nested, depth + 1)

    for value in values:
        visit(value, 0)
    return "\n".join(fragments)


def _configuration_scalar_text(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if (
        value is None
        or isinstance(value, Mapping)
        or (
            isinstance(value, Sequence)
            and not isinstance(value, (str, bytes, bytearray))
        )
    ):
        return None
    return str(value)


def _decoded_configuration_escapes(path: str, text: str) -> str:
    if PurePosixPath(path).suffix.lower() not in {".json", ".yaml", ".yml"}:
        return text

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        width = token[1]
        codepoint = int(token[2:], 16)
        if codepoint > 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
            return token
        expected_digits = {"x": 2, "u": 4, "U": 8}[width]
        if len(token) != expected_digits + 2:
            return token
        return chr(codepoint)

    return re.sub(
        r"\\(?:x[0-9A-Fa-f]{2}|u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8})",
        replace,
        text,
    )


def _yaml_double_quoted_line_continuations(text: str) -> str:
    result: list[str] = []
    in_double = False
    in_single = False
    in_comment = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_comment:
            result.append(char)
            index += 1
            if char in "\r\n":
                in_comment = False
            continue
        if in_double and char == "\\":
            newline_width = 0
            if text.startswith("\r\n", index + 1):
                newline_width = 2
            elif index + 1 < len(text) and text[index + 1] in "\r\n":
                newline_width = 1
            if newline_width:
                index += 1 + newline_width
                while index < len(text) and text[index] in " \t":
                    index += 1
                continue
            result.append(char)
            index += 1
            if index < len(text):
                result.append(text[index])
                index += 1
            continue
        if not in_single and char == '"':
            in_double = not in_double
        elif not in_double and char == "'":
            if in_single and index + 1 < len(text) and text[index + 1] == "'":
                result.extend(("'", "'"))
                index += 2
                continue
            in_single = not in_single
        elif not in_double and not in_single and char == "#":
            in_comment = True
        result.append(char)
        index += 1
    return "".join(result)


def scan_git_and_artifacts(
    repository_root: Path,
    snapshots: Sequence[ArchiveSnapshot],
    generated_payloads: Mapping[str, bytes] | None = None,
) -> dict[str, object]:
    """Scan tracked files, the current diff, archives, and generated reports."""

    findings: list[dict[str, object]] = []
    scanned_files = 0
    tracked_paths: set[str] = set()
    tracked = _git_output(repository_root, ["ls-files", "--stage", "-z"])
    for raw_record in tracked.split(b"\0"):
        if not raw_record:
            continue
        metadata, separator, raw_path = raw_record.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 3 or fields[2] != b"0":
            raise RuntimeError("Git index contains an unsupported tracked entry")
        relative = raw_path.decode("utf-8", errors="strict")
        if relative in tracked_paths:
            raise RuntimeError("Git index contains a duplicate tracked path")
        tracked_paths.add(relative)
        payload = _git_output(
            repository_root,
            ["cat-file", "blob", fields[1].decode("ascii", errors="strict")],
        )
        scanned_files += 1
        findings.extend(scan_payload(relative, payload))
    untracked = _git_output(
        repository_root,
        ["ls-files", "--others", "--exclude-standard", "-z"],
    )
    for raw_path in untracked.split(b"\0"):
        if not raw_path:
            continue
        relative = raw_path.decode("utf-8", errors="strict")
        candidate = repository_root / relative
        if candidate.is_symlink():
            scanned_files += 1
            findings.extend(
                scan_payload(relative, os.readlink(candidate).encode("utf-8"))
            )
        elif candidate.is_file():
            scanned_files += 1
            findings.extend(scan_payload(relative, candidate.read_bytes()))
    diff = _git_output(
        repository_root,
        ["diff", "--no-ext-diff", "--binary", "HEAD", "--"],
    )
    scanned_files += 1
    findings.extend(
        scan_payload(
            "<git-diff>",
            _added_diff_payload(diff),
            allow_safe_fixtures=False,
        )
    )
    for snapshot in snapshots:
        for entry, payload in snapshot.files.items():
            scanned_files += 1
            findings.extend(
                scan_payload(
                    f"{snapshot.kind}/{entry}",
                    payload,
                    allow_safe_fixtures=False,
                )
            )
    for name, payload in sorted((generated_payloads or {}).items()):
        scanned_files += 1
        findings.extend(
            scan_payload(
                f"report/{name}",
                payload,
                allow_safe_fixtures=False,
            )
        )
    findings.sort(
        key=lambda item: (
            str(item.get("path", "")),
            int(item.get("line", 0)),
            str(item.get("rule_id", "")),
            str(item.get("fingerprint", "")),
        )
    )
    return {
        "scanned_file_count": scanned_files,
        "finding_count": len(findings),
        "findings": findings,
    }


def build_distribution_report(
    repository_root: Path,
    *,
    source_root: Path | None = None,
    dist_dir: Path | None = None,
    run_install_smoke: bool = True,
) -> dict[str, object]:
    """Build and independently verify wheel and sdist artifacts."""

    repository_root = repository_root.resolve()
    source_root = (source_root or repository_root).resolve()
    owned_temporary: tempfile.TemporaryDirectory[str] | None = None
    if dist_dir is None:
        owned_temporary = tempfile.TemporaryDirectory(
            prefix="voi-distribution-compliance-"
        )
        dist_dir = Path(owned_temporary.name) / "dist"
    dist_dir = dist_dir.resolve()
    dist_dir.mkdir(parents=True, exist_ok=True)
    try:
        repository_before = repository_state_evidence(
            repository_root,
            source_root,
        )
        _build_distributions(source_root, dist_dir)
        repository_after = repository_state_evidence(
            repository_root,
            source_root,
        )
        wheel_paths = sorted(dist_dir.glob("*.whl"))
        sdist_paths = sorted(dist_dir.glob("*.tar.gz"))
        if len(wheel_paths) != 1 or len(sdist_paths) != 1:
            report: dict[str, object] = {
                "schema_version": DISTRIBUTION_COMPLIANCE_SCHEMA_VERSION,
                "repository": {
                    "before": repository_before,
                    "after": repository_after,
                },
                "artifacts": {
                    "wheel_count": len(wheel_paths),
                    "sdist_count": len(sdist_paths),
                },
                "archive_blockers": [
                    {
                        "code": "unexpected_artifact_count",
                        "wheel_count": len(wheel_paths),
                        "sdist_count": len(sdist_paths),
                    }
                ],
            }
            return _with_derived_verdict(report)
        wheel = inspect_wheel(wheel_paths[0])
        sdist = inspect_sdist(sdist_paths[0])
        metadata_entry, metadata_bytes = _single_file_with_suffix(
            wheel.files,
            ".dist-info/METADATA",
        )
        metadata = BytesParser().parsebytes(metadata_bytes or b"")
        license_expressions = metadata.get_all("License-Expression", [])
        requires_dist = metadata.get_all("Requires-Dist", [])
        generated_metadata = {
            "wheel": _generated_metadata_evidence(wheel),
            "sdist": _generated_metadata_evidence(sdist),
        }
        sdist_root = _expected_sdist_root(sdist.path)
        sdist_metadata: list[dict[str, str]] = []
        for entry in (
            f"{sdist_root}/PKG-INFO",
            f"{sdist_root}/{EXPECTED_PROJECT_NAME}.egg-info/PKG-INFO",
        ):
            payload = sdist.files.get(entry, b"")
            sdist_metadata.append(
                {
                    "entry": entry,
                    "raw": payload.decode("utf-8", errors="replace"),
                }
            )
        metadata_dependencies = sorted(
            {
                normalized_dependency_name(requirement)
                for requirement in requires_dist
                if normalized_dependency_name(requirement)
            }
        )
        pyproject_text = (source_root / "pyproject.toml").read_text(
            encoding="utf-8"
        )
        lock_text = (source_root / "uv.lock").read_text(encoding="utf-8")
        declared_dependencies = sorted(
            declared_dependencies_from_pyproject(pyproject_text)
        )
        build_requirements = sorted(
            build_requirements_from_pyproject(pyproject_text)
        )
        build_dependencies = sorted(
            build_dependencies_from_pyproject(pyproject_text)
        )
        lock_dependencies = sorted(
            direct_dependencies_from_uv_lock(lock_text)
        )
        build_locked_versions = locked_distribution_versions_from_uv_lock(
            lock_text,
            EXPECTED_BUILD_DISTRIBUTIONS,
        )
        notice_text = (source_root / "THIRD_PARTY_NOTICES.md").read_text(
            encoding="utf-8"
        )
        notice_dependencies = sorted(
            normalized_dependency_name(name)
            for name in _NOTICE_DISTRIBUTION_RE.findall(notice_text)
        )
        notice_licenses = notice_dependency_licenses(notice_text)
        licenses = _license_evidence(source_root, wheel, sdist)
        runtime_data = _runtime_data_evidence(source_root, wheel, sdist)
        expected_payloads, expected_payload_sizes = (
            _expected_archive_payload_evidence(
                repository_root,
                str(repository_before.get("head", "")),
            )
        )
        install_smoke = (
            isolated_wheel_install_smoke(wheel.path)
            if run_install_smoke
            else {"attempted": False, "returncode": None, "payload": {}}
        )
        report = {
            "schema_version": DISTRIBUTION_COMPLIANCE_SCHEMA_VERSION,
            "repository": {
                "before": repository_before,
                "after": repository_after,
            },
            "artifacts": {
                "wheel": _artifact_evidence(wheel),
                "sdist": _artifact_evidence(sdist),
            },
            "archive_blockers": [
                *wheel.blockers,
                *sdist.blockers,
                *archive_content_blockers(wheel),
                *archive_content_blockers(sdist),
                *archive_manifest_blockers(
                    wheel,
                    expected_payloads["wheel"],
                ),
                *archive_manifest_blockers(
                    sdist,
                    expected_payloads["sdist"],
                ),
            ],
            "archive_manifests": {
                kind: dict(sorted(paths.items()))
                for kind, paths in expected_payloads.items()
            },
            "archive_size_manifests": {
                kind: dict(sorted(paths.items()))
                for kind, paths in expected_payload_sizes.items()
            },
            "metadata": {
                "entry": metadata_entry,
                "license_expressions": license_expressions,
                "requires_dist": sorted(requires_dist),
                "raw": (metadata_bytes or b"").decode(
                    "utf-8",
                    errors="replace",
                ),
                "sdist": sdist_metadata,
                "generated": generated_metadata,
            },
            "source_pyproject": {
                "raw": pyproject_text,
                "sha256": sha256_bytes(pyproject_text.encode("utf-8")),
            },
            "licenses": licenses,
            "runtime_data": runtime_data,
            "dependencies": {
                "expected": sorted(EXPECTED_DIRECT_DISTRIBUTIONS),
                "expected_project": sorted(EXPECTED_PROJECT_DISTRIBUTIONS),
                "expected_build": sorted(EXPECTED_BUILD_DISTRIBUTIONS),
                "declared": declared_dependencies,
                "build_system": build_dependencies,
                "build_requirements": build_requirements,
                "build_locked_versions": build_locked_versions,
                "build_backend_generator": _wheel_generator(wheel),
                "lock": lock_dependencies,
                "metadata": metadata_dependencies,
                "notices": notice_dependencies,
                "notice_licenses": notice_licenses,
            },
            "install_smoke": install_smoke,
        }
        preliminary = _with_derived_verdict(report)
        preliminary_json = canonical_json_text(preliminary).encode("utf-8")
        preliminary_markdown = render_distribution_markdown(preliminary).encode(
            "utf-8"
        )
        report["secret_scan"] = scan_git_and_artifacts(
            repository_root,
            (wheel, sdist),
            {
                "distribution-compliance.json": preliminary_json,
                "distribution-compliance.md": preliminary_markdown,
            },
        )
        return _with_derived_verdict(report)
    finally:
        if owned_temporary is not None:
            owned_temporary.cleanup()


def distribution_report_blockers(
    report: Mapping[str, object],
) -> list[dict[str, object]]:
    """Derive the verdict from raw evidence, never caller-provided booleans."""

    blockers: list[dict[str, object]] = []
    repository = _mapping(report.get("repository"))
    repository_states: dict[str, Mapping[str, object]] = {}
    for phase in ("before", "after"):
        state = _mapping(repository.get(phase))
        repository_states[phase] = state
        head = state.get("head")
        tree = state.get("tree")
        dirty_entries = state.get("dirty_entries")
        repository_root = state.get("repository_root")
        source_root = state.get("source_root")
        replacement_refs = state.get("replacement_refs")
        raw_state_valid = (
            state.get("ok") is True
            and state.get("source_root_matches") is True
            and dirty_entries == []
            and replacement_refs == []
            and isinstance(head, str)
            and re.fullmatch(r"[0-9a-f]{40,64}", head) is not None
            and isinstance(tree, str)
            and re.fullmatch(r"[0-9a-f]{40,64}", tree) is not None
            and isinstance(repository_root, str)
            and isinstance(source_root, str)
            and repository_root == source_root
        )
        if not raw_state_valid:
            blockers.append(
                {
                    "code": "repository_not_clean_commit",
                    "phase": phase,
                    "head": state.get("head"),
                    "tree": state.get("tree"),
                    "dirty_entries": state.get("dirty_entries"),
                    "replacement_refs": state.get("replacement_refs"),
                    "source_root_matches": state.get("source_root_matches"),
                }
            )
    before_state = repository_states["before"]
    after_state = repository_states["after"]
    for identity in ("head", "tree"):
        before_identity = before_state.get(identity)
        after_identity = after_state.get(identity)
        if before_identity != after_identity:
            blockers.append(
                {
                    "code": "repository_identity_changed",
                    "identity": identity,
                    "before": before_identity,
                    "after": after_identity,
                }
            )
    trusted_archive_evidence = _trusted_archive_evidence(report)
    if trusted_archive_evidence is None:
        blockers.append({"code": "unverified_exact_sha_provenance"})
        trusted_archive_manifests: Mapping[str, Mapping[str, str]] = {}
        trusted_archive_sizes: Mapping[str, Mapping[str, int]] = {}
    else:
        trusted_archive_manifests, trusted_archive_sizes = (
            trusted_archive_evidence
        )
    archive_blockers = report.get("archive_blockers")
    if not isinstance(archive_blockers, list):
        blockers.append({"code": "invalid_archive_blocker_evidence"})
    else:
        for item in archive_blockers:
            if isinstance(item, Mapping):
                blockers.append(dict(item))
            else:
                blockers.append({"code": "invalid_archive_blocker_evidence"})
    archive_manifests_value = report.get("archive_manifests")
    archive_manifests = (
        archive_manifests_value
        if isinstance(archive_manifests_value, Mapping)
        else {}
    )
    if set(archive_manifests) != {"wheel", "sdist"}:
        blockers.append({"code": "invalid_archive_manifest_evidence"})
    archive_size_manifests_value = report.get("archive_size_manifests")
    archive_size_manifests = (
        archive_size_manifests_value
        if isinstance(archive_size_manifests_value, Mapping)
        else {}
    )
    if set(archive_size_manifests) != {"wheel", "sdist"}:
        blockers.append({"code": "invalid_archive_size_manifest_evidence"})
    artifacts = _mapping(report.get("artifacts"))
    trusted_artifacts = _trusted_artifact_evidence(report)
    if trusted_artifacts is None:
        blockers.append({"code": "unverified_artifact_provenance"})
        trusted_artifacts = {}
    artifact_paths: dict[str, Path] = {}
    artifact_file_manifests: dict[str, Mapping[str, str]] = {}
    artifact_file_sizes: dict[str, Mapping[str, int]] = {}
    expected_archive_sizes: dict[str, Mapping[str, int]] = {}
    for kind in ("wheel", "sdist"):
        artifact = _mapping(artifacts.get(kind))
        artifact_path = artifact.get("path")
        filename = artifact.get("filename")
        digest = artifact.get("sha256")
        entry_count = artifact.get("entry_count")
        entries = artifact.get("entries")
        if (
            not isinstance(artifact_path, str)
            or not artifact_path
            or not Path(artifact_path).is_absolute()
        ):
            blockers.append({"code": "invalid_artifact_path", "kind": kind})
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            blockers.append({"code": "missing_artifact_digest", "kind": kind})
        if not isinstance(entries, list) or not entries:
            blockers.append({"code": "missing_artifact_entries", "kind": kind})
        elif type(entry_count) is not int or entry_count != len(entries):
            blockers.append(
                {"code": "artifact_entry_count_mismatch", "kind": kind}
            )
        trusted_artifact = _mapping(trusted_artifacts.get(kind))
        if trusted_artifact:
            for field in (
                "path",
                "filename",
                "sha256",
                "entry_count",
                "entries",
                "file_manifest",
                "file_sizes",
                "directory_entries",
            ):
                if artifact.get(field) != trusted_artifact.get(field):
                    blockers.append(
                        {
                            "code": "artifact_archive_evidence_mismatch",
                            "kind": kind,
                            "field": field,
                        }
                    )
            for item in _sequence(
                trusted_artifact.get("archive_blockers")
            ):
                if isinstance(item, Mapping):
                    blockers.append(dict(item))
                else:
                    blockers.append(
                        {
                            "code": "invalid_trusted_archive_blocker",
                            "kind": kind,
                        }
                    )
        elif trusted_artifacts:
            blockers.append(
                {"code": "missing_trusted_artifact_evidence", "kind": kind}
            )
        expected_manifest = _sha256_manifest(archive_manifests.get(kind))
        if expected_manifest is None:
            blockers.append(
                {"code": "invalid_archive_manifest_evidence", "kind": kind}
            )
        elif expected_manifest != trusted_archive_manifests.get(kind):
            blockers.append(
                {
                    "code": "archive_manifest_provenance_mismatch",
                    "kind": kind,
                }
            )
        expected_size_manifest = _size_manifest(
            archive_size_manifests.get(kind)
        )
        if (
            expected_size_manifest is None
            or expected_manifest is None
            or set(expected_size_manifest) != set(expected_manifest)
        ):
            blockers.append(
                {
                    "code": "invalid_archive_size_manifest_evidence",
                    "kind": kind,
                }
            )
        else:
            expected_archive_sizes[kind] = expected_size_manifest
            if expected_size_manifest != trusted_archive_sizes.get(kind):
                blockers.append(
                    {
                        "code": "archive_size_provenance_mismatch",
                        "kind": kind,
                    }
                )
        file_manifest = _sha256_manifest(artifact.get("file_manifest"))
        file_sizes = _size_manifest(artifact.get("file_sizes"))
        directory_entries = artifact.get("directory_entries")
        if (
            not isinstance(filename, str)
            or not filename
            or file_manifest is None
            or file_sizes is None
            or not isinstance(directory_entries, list)
            or any(not isinstance(entry, str) for entry in directory_entries)
        ):
            blockers.append(
                {"code": "invalid_artifact_file_manifest", "kind": kind}
            )
            continue
        if set(file_sizes) != set(file_manifest):
            blockers.append(
                {"code": "artifact_file_size_manifest_mismatch", "kind": kind}
            )
        entry_names = (
            [entry for entry in entries if isinstance(entry, str)]
            if isinstance(entries, list)
            else []
        )
        if (
            set(file_manifest) & set(directory_entries)
            or set(file_manifest) | set(directory_entries) != set(entry_names)
            or len(file_manifest) + len(directory_entries) != len(entry_names)
        ):
            blockers.append(
                {"code": "artifact_entry_manifest_mismatch", "kind": kind}
            )
        required_generated = _required_generated_archive_files(
            kind,
            Path(filename),
        )
        missing_generated = sorted(required_generated - set(file_manifest))
        if missing_generated:
            blockers.append(
                {
                    "code": "missing_generated_archive_evidence",
                    "kind": kind,
                    "entries": missing_generated,
                }
            )
        blockers.extend(
            _archive_entry_evidence_blockers(
                kind,
                Path(filename),
                entry_names,
            )
        )
        artifact_paths[kind] = Path(filename)
        artifact_file_manifests[kind] = file_manifest
        artifact_file_sizes[kind] = file_sizes
        snapshot = ArchiveSnapshot(
            kind=kind,
            path=Path(filename),
            digest=str(digest),
            entries=tuple(),
            files={entry: b"" for entry in file_manifest},
            blockers=(),
            directories=tuple(directory_entries),
        )
        blockers.extend(archive_content_blockers(snapshot))
        if expected_manifest is not None:
            blockers.extend(
                _archive_manifest_digest_blockers(
                    kind,
                    snapshot.path,
                    file_manifest,
                    expected_manifest,
                )
            )
    metadata = _mapping(report.get("metadata"))
    blockers.extend(
        _metadata_evidence_blockers(
            metadata,
            _mapping(report.get("source_pyproject")),
            artifact_paths.get("wheel"),
            artifact_paths.get("sdist"),
            artifact_file_manifests.get("wheel"),
            artifact_file_manifests.get("sdist"),
            artifact_file_sizes.get("wheel"),
            artifact_file_sizes.get("sdist"),
            _sha256_manifest(archive_manifests.get("sdist")),
            expected_archive_sizes.get("wheel"),
            expected_archive_sizes.get("sdist"),
            _mapping(report.get("dependencies")),
        )
    )
    expressions = metadata.get("license_expressions")
    if expressions != [EXPECTED_LICENSE_EXPRESSION]:
        blockers.append(
            {
                "code": "wrong_metadata_license",
                "observed": expressions,
                "expected": EXPECTED_LICENSE_EXPRESSION,
            }
        )
    for item in _sequence(report.get("licenses")):
        evidence = _mapping(item)
        path = str(evidence.get("path", ""))
        expected_digest = EXPECTED_LICENSE_FILE_SHA256.get(path)
        if (
            evidence.get("source_sha256") != evidence.get("wheel_sha256")
            or evidence.get("source_sha256") != evidence.get("sdist_sha256")
        ):
            blockers.append(
                {
                    "code": "license_file_mismatch",
                    "path": evidence.get("path"),
                }
            )
        if (
            expected_digest is None
            or evidence.get("expected_sha256") != expected_digest
            or evidence.get("source_sha256") != expected_digest
        ):
            blockers.append(
                {
                    "code": "license_content_mismatch",
                    "path": evidence.get("path"),
                    "observed": evidence.get("source_sha256"),
                    "expected": expected_digest,
                }
            )
    observed_license_paths = {
        str(_mapping(item).get("path", ""))
        for item in _sequence(report.get("licenses"))
    }
    for required in REQUIRED_LICENSE_FILES:
        if required not in observed_license_paths:
            blockers.append(
                {"code": "missing_license_file", "path": required}
            )
    for item in _sequence(report.get("runtime_data")):
        evidence = _mapping(item)
        if not evidence.get("wheel_present") or not evidence.get("sdist_present"):
            blockers.append(
                {
                    "code": "missing_runtime_data",
                    "path": evidence.get("path"),
                    "wheel_present": evidence.get("wheel_present"),
                    "sdist_present": evidence.get("sdist_present"),
                }
            )
        if (
            evidence.get("source_sha256") != evidence.get("wheel_sha256")
            or evidence.get("source_sha256") != evidence.get("sdist_sha256")
        ):
            blockers.append(
                {
                    "code": "runtime_data_mismatch",
                    "path": evidence.get("path"),
                }
            )
    observed_runtime_paths = {
        str(_mapping(item).get("path", ""))
        for item in _sequence(report.get("runtime_data"))
    }
    for required in REQUIRED_RUNTIME_FILES:
        if required not in observed_runtime_paths:
            blockers.append(
                {"code": "missing_runtime_data_evidence", "path": required}
            )
    dependencies = _mapping(report.get("dependencies"))
    expected = sorted(EXPECTED_DIRECT_DISTRIBUTIONS)
    project_expected = sorted(EXPECTED_PROJECT_DISTRIBUTIONS)
    build_expected = sorted(EXPECTED_BUILD_DISTRIBUTIONS)
    dependency_expectations = {
        "expected": expected,
        "expected_project": project_expected,
        "expected_build": build_expected,
        "declared": project_expected,
        "build_system": build_expected,
        "build_requirements": [EXPECTED_BUILD_BACKEND_REQUIREMENT],
        "build_locked_versions": {
            "setuptools": EXPECTED_BUILD_BACKEND_VERSION,
        },
        "build_backend_generator": EXPECTED_BUILD_BACKEND_GENERATOR,
        "lock": project_expected,
        "metadata": project_expected,
        "notices": expected,
        "notice_licenses": dict(sorted(EXPECTED_NOTICE_LICENSES.items())),
    }
    for source_name, source_expected in dependency_expectations.items():
        observed = dependencies.get(source_name)
        if observed != source_expected:
            blockers.append(
                {
                    "code": "dependency_notice_drift",
                    "source": source_name,
                    "observed": observed,
                    "expected": source_expected,
                }
            )
    install_smoke = _mapping(report.get("install_smoke"))
    if install_smoke.get("attempted") is not True:
        blockers.append({"code": "isolated_install_not_attempted"})
    else:
        payload = _mapping(install_smoke.get("payload"))
        if install_smoke.get("returncode") != 0:
            blockers.append(
                {
                    "code": "isolated_install_failed",
                    "returncode": install_smoke.get("returncode"),
                }
            )
        if payload.get("license_expression") != EXPECTED_LICENSE_EXPRESSION:
            blockers.append(
                {
                    "code": "installed_metadata_license_mismatch",
                    "observed": payload.get("license_expression"),
                }
            )
        if payload.get("runtime_data_loaded") is not True:
            blockers.append({"code": "installed_runtime_data_failed"})
        if payload.get("target_runtime_data_loaded") is not True:
            blockers.append({"code": "target_install_runtime_data_failed"})
    secret_scan = _mapping(report.get("secret_scan"))
    findings = secret_scan.get("findings")
    finding_count = secret_scan.get("finding_count")
    scanned_file_count = secret_scan.get("scanned_file_count")
    if isinstance(findings, list) and findings:
        blockers.append(
            {
                "code": "secret_or_private_config_detected",
                "finding_count": len(findings),
            }
        )
    elif (
        findings != []
        or finding_count != 0
        or type(scanned_file_count) is not int
        or scanned_file_count <= 0
    ):
        blockers.append({"code": "invalid_secret_scan_evidence"})
    return _deduplicate_blockers(blockers)


def render_distribution_markdown(report: Mapping[str, object]) -> str:
    """Render a deterministic reviewer-facing compliance summary."""

    artifacts = _mapping(report.get("artifacts"))
    dependencies = _mapping(report.get("dependencies"))
    secret_scan = _mapping(report.get("secret_scan"))
    lines = [
        "# Distribution Compliance",
        "",
        f"- Status: `{report.get('status', 'unknown')}`",
        f"- License expression: `{EXPECTED_LICENSE_EXPRESSION}`",
        f"- Secret/private-config findings: "
        f"`{secret_scan.get('finding_count', 0)}`",
        "",
        "## Artifacts",
        "",
        "| Kind | SHA-256 | Entries |",
        "| --- | --- | ---: |",
    ]
    for kind in ("wheel", "sdist"):
        artifact = _mapping(artifacts.get(kind))
        lines.append(
            f"| `{kind}` | `{artifact.get('sha256', '')}` | "
            f"{artifact.get('entry_count', 0)} |"
        )
    lines.extend(
        [
            "",
            "## Dependency Notices",
            "",
            f"- Declared: `{', '.join(_string_list(dependencies.get('declared')))}`",
            f"- METADATA: `{', '.join(_string_list(dependencies.get('metadata')))}`",
            f"- Notices: `{', '.join(_string_list(dependencies.get('notices')))}`",
            "",
            "## Blockers",
            "",
        ]
    )
    blockers = _sequence(report.get("blockers"))
    if blockers:
        for blocker in blockers:
            item = _mapping(blocker)
            lines.append(f"- `{item.get('code', 'unknown')}`")
    else:
        lines.append("No distribution compliance blockers.")
    return "\n".join(lines) + "\n"


def write_distribution_evidence(
    report: Mapping[str, object],
    output_dir: Path,
) -> None:
    """Write the canonical report and reviewer evidence files."""

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "distribution-compliance.json").write_text(
        canonical_json_text(report),
        encoding="utf-8",
    )
    (output_dir / "distribution-compliance.md").write_text(
        render_distribution_markdown(report),
        encoding="utf-8",
    )
    artifacts = _mapping(report.get("artifacts"))
    for kind in ("wheel", "sdist"):
        entries = _string_list(_mapping(artifacts.get(kind)).get("entries"))
        (output_dir / f"{kind}.entries.txt").write_text(
            "".join(f"{entry}\n" for entry in entries),
            encoding="utf-8",
        )
    metadata_text = str(_mapping(report.get("metadata")).get("raw", ""))
    (output_dir / "installed.METADATA").write_text(
        metadata_text,
        encoding="utf-8",
    )
    (output_dir / "dependency-notices.json").write_text(
        canonical_json_text(_mapping(report.get("dependencies"))),
        encoding="utf-8",
    )
    (output_dir / "secret-scan.json").write_text(
        canonical_json_text(_mapping(report.get("secret_scan"))),
        encoding="utf-8",
    )


def isolated_wheel_install_smoke(wheel_path: Path) -> dict[str, object]:
    """Install one wheel without dependencies and load packaged runtime data."""

    temporary_root = (
        "/private/tmp"
        if sys.platform == "darwin" and Path("/private/tmp").is_dir()
        else None
    )
    with tempfile.TemporaryDirectory(
        prefix="voi-wheel-install-",
        dir=temporary_root,
    ) as temporary:
        environment_root = Path(temporary) / "venv"
        failure_stage = "create_venv"
        try:
            _isolated_venv_builder().create(environment_root)
            python_path = (
                environment_root / "Scripts" / "python.exe"
                if os.name == "nt"
                else environment_root / "bin" / "python"
            )
            environment = os.environ.copy()
            environment.pop("PYTHONPATH", None)
            failure_stage = "install_wheel"
            install = subprocess.run(
                [
                    str(python_path),
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--no-deps",
                    "--no-index",
                    str(wheel_path),
                ],
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=180,
            )
            if install.returncode != 0:
                return {
                    "attempted": True,
                    "returncode": install.returncode,
                    "payload": {},
                    "stderr_fingerprint": sha256_bytes(
                        install.stderr.encode("utf-8", errors="replace")
                    ),
                }
            script = (
                "import json\n"
                "from importlib.metadata import metadata\n"
                "from starcraft_commander.micromachine_map_pool import "
                "load_micromachine_map_pool\n"
                "from starcraft_commander.micromachine_pre_live_journeys import "
                "load_pre_live_journey_manifest\n"
                "from starcraft_commander.runtime_data import micromachine_data_path\n"
                "pool = load_micromachine_map_pool()\n"
                "journeys = load_pre_live_journey_manifest()\n"
                "required = ['HOOK_MANIFEST.json', 'PRE_LIVE_PRODUCERS.json']\n"
                "loaded = bool(pool.maps) and bool(journeys.get('journeys')) and "
                "all(micromachine_data_path(name).is_file() for name in required)\n"
                "print(json.dumps({'license_expression': "
                "metadata('voiStarcraft2').get('License-Expression'), "
                "'runtime_data_loaded': loaded}, sort_keys=True))\n"
            )
            failure_stage = "load_installed_package"
            result = subprocess.run(
                [str(python_path), "-I", "-c", script],
                cwd=temporary,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=180,
            )
            payload: object = {}
            if result.stdout.strip():
                try:
                    payload = json.loads(result.stdout)
                except json.JSONDecodeError:
                    payload = {}
            normalized_payload = (
                dict(payload) if isinstance(payload, Mapping) else {}
            )
            target_root = Path(temporary) / "target"
            failure_stage = "install_wheel_target"
            target_install = subprocess.run(
                [
                    str(python_path),
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--no-deps",
                    "--no-index",
                    "--target",
                    str(target_root),
                    str(wheel_path),
                ],
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=180,
            )
            if target_install.returncode == 0:
                failure_stage = "load_target_package"
                target_script = (
                    "import json,sys\n"
                    "from pathlib import Path\n"
                    "target = Path(sys.argv[1]).resolve()\n"
                    "sys.path.insert(0, str(target))\n"
                    "from starcraft_commander.runtime_data import "
                    "micromachine_data_path, micromachine_data_root\n"
                    "required = ['HOOK_MANIFEST.json', "
                    "'MICROMACHINE_MAP_POOL.json', 'PRE_LIVE_JOURNEYS.json', "
                    "'PRE_LIVE_PRODUCERS.json']\n"
                    "root = micromachine_data_root().resolve()\n"
                    "loaded = all(micromachine_data_path(name).is_file() "
                    "for name in required) and target in root.parents\n"
                    "print(json.dumps({'loaded': loaded}, sort_keys=True))\n"
                )
                target_result = subprocess.run(
                    [
                        str(python_path),
                        "-I",
                        "-c",
                        target_script,
                        str(target_root),
                    ],
                    cwd=temporary,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                try:
                    target_payload = json.loads(target_result.stdout)
                except json.JSONDecodeError:
                    target_payload = {}
                normalized_payload["target_runtime_data_loaded"] = bool(
                    target_result.returncode == 0
                    and isinstance(target_payload, Mapping)
                    and target_payload.get("loaded") is True
                )
            else:
                normalized_payload["target_runtime_data_loaded"] = False
            return {
                "attempted": True,
                "returncode": result.returncode,
                "payload": normalized_payload,
                "stderr_fingerprint": (
                    sha256_bytes(result.stderr.encode("utf-8", errors="replace"))
                    if result.stderr
                    else ""
                ),
            }
        except (OSError, subprocess.SubprocessError) as exc:
            return {
                "attempted": True,
                "returncode": -1,
                "payload": {},
                "failure_stage": failure_stage,
                "error_type": type(exc).__name__,
                "stderr_fingerprint": sha256_bytes(
                    f"{type(exc).__name__}:{exc}".encode("utf-8", errors="replace")
                ),
            }


def _isolated_venv_builder() -> venv.EnvBuilder:
    """Preserve managed-Python layouts when creating POSIX smoke venvs."""

    return venv.EnvBuilder(
        with_pip=True,
        clear=True,
        symlinks=os.name != "nt",
    )


def project_version_from_pyproject(text: str) -> str:
    """Return the exact static project version declared in TOML."""

    return _project_string_from_pyproject(text, "version")


def _project_string_from_pyproject(text: str, key: str) -> str:
    section = _pyproject_section(text, "project")
    match = re.search(
        rf"(?m)^\s*{re.escape(key)}\s*=\s*(?:"
        r'"((?:\\.|[^"\\])*)"|'
        r"'([^']*)'"
        r")\s*$",
        section,
    )
    if match is None:
        return ""
    return _decode_toml_string(match.group(1), match.group(2))


def metadata_requirements_from_pyproject(text: str) -> tuple[str, ...]:
    """Derive exact METADATA Requires-Dist values from project TOML."""

    project_section = _pyproject_section(text, "project")
    requirements = list(
        _toml_array_assignment_values(project_section, "dependencies")
    )
    optional_section = _pyproject_section(
        text,
        "project.optional-dependencies",
    )
    assignment_pattern = re.compile(
        r"(?ms)^\s*([A-Za-z0-9_.-]+)\s*=\s*\[(.*?)\]"
        r"(?=\s*(?:^[A-Za-z0-9_.-]+\s*=|\Z))"
    )
    for match in assignment_pattern.finditer(optional_section):
        extra = re.sub(r"[-_.]+", "-", match.group(1).lower())
        for requirement in _toml_quoted_values(match.group(2)):
            base, separator, marker = requirement.partition(";")
            extra_marker = f'extra == "{extra}"'
            if separator:
                requirements.append(
                    f"{base.strip()}; ({marker.strip()}) and {extra_marker}"
                )
            else:
                requirements.append(f"{base.strip()}; {extra_marker}")
    return tuple(sorted(requirements))


def project_metadata_expectations_from_pyproject(
    text: str,
) -> dict[str, object]:
    """Derive the complete static core-metadata contract from project TOML."""

    if _toml is None:
        return {}
    try:
        document = _toml.loads(text)
    except (RecursionError, TypeError, ValueError):
        return {}
    project = document.get("project")
    if not isinstance(project, Mapping):
        return {}
    required_strings = {
        "name": project.get("name"),
        "version": project.get("version"),
        "summary": project.get("description"),
        "requires_python": project.get("requires-python"),
        "license_expression": project.get("license"),
        "readme": project.get("readme"),
    }
    if any(
        not isinstance(value, str) or not value
        for value in required_strings.values()
    ):
        return {}
    keywords = project.get("keywords")
    license_files = project.get("license-files")
    optional = project.get("optional-dependencies")
    dependencies = project.get("dependencies")
    if (
        not isinstance(keywords, list)
        or any(not isinstance(value, str) or not value for value in keywords)
        or not isinstance(license_files, list)
        or any(
            not isinstance(value, str) or not value
            for value in license_files
        )
        or not isinstance(optional, Mapping)
        or any(
            not isinstance(name, str)
            or not name
            or not isinstance(values, list)
            or any(
                not isinstance(value, str) or not value
                for value in values
            )
            for name, values in optional.items()
        )
        or not isinstance(dependencies, list)
        or any(
            not isinstance(value, str) or not value
            for value in dependencies
        )
    ):
        return {}
    readme = str(required_strings["readme"])
    content_type = {
        ".md": "text/markdown",
        ".rst": "text/x-rst",
        ".txt": "text/plain",
    }.get(PurePosixPath(readme).suffix.lower(), "")
    if not content_type:
        return {}
    provides_extra = tuple(
        re.sub(r"[-_.]+", "-", str(name).lower())
        for name in optional
    )
    if len(set(provides_extra)) != len(provides_extra):
        return {}
    return {
        **required_strings,
        "keywords": tuple(keywords),
        "license_files": tuple(license_files),
        "description_content_type": content_type,
        "provides_extra": provides_extra,
        "requires_dist": metadata_requirements_from_pyproject(text),
    }


def declared_dependencies_from_pyproject(text: str) -> frozenset[str]:
    """Extract direct core and optional dependencies from project TOML."""

    return frozenset(
        name
        for requirement in metadata_requirements_from_pyproject(text)
        if (name := normalized_dependency_name(requirement))
    )


def build_dependencies_from_pyproject(text: str) -> frozenset[str]:
    """Extract direct build backend requirements from project TOML."""

    return frozenset(
        name
        for requirement in build_requirements_from_pyproject(text)
        if (name := normalized_dependency_name(requirement))
    )


def build_requirements_from_pyproject(text: str) -> tuple[str, ...]:
    """Extract exact direct build backend requirements from project TOML."""

    sanitized = "\n".join(
        _strip_toml_line_comment(line) for line in text.splitlines()
    )
    match = re.search(
        r"(?ms)^\[build-system\]\s*$"
        r"(.*?)(?=^\[[^\n]+\]\s*$|\Z)",
        sanitized,
    )
    if match is None:
        return ()
    requires = re.search(
        r"(?ms)^\s*requires\s*=\s*\[(.*?)\]",
        match.group(1),
    )
    if requires is None:
        return ()
    return tuple(
        sorted(
            requirement
            for requirement in re.findall(
                r"[\"']([^\"']+)[\"']",
                requires.group(1),
            )
            if normalized_dependency_name(requirement)
        )
    )


def _strip_toml_line_comment(line: str) -> str:
    """Strip a TOML comment without treating quoted hashes as comments."""

    quote = ""
    escaped = False
    for index, character in enumerate(line):
        if quote:
            if quote == '"' and escaped:
                escaped = False
                continue
            if quote == '"' and character == "\\":
                escaped = True
                continue
            if character == quote:
                quote = ""
            continue
        if character in {"'", '"'}:
            quote = character
            continue
        if character == "#":
            return line[:index]
    return line


def _pyproject_section(text: str, section_name: str) -> str:
    sanitized = "\n".join(
        _strip_toml_line_comment(line) for line in text.splitlines()
    )
    match = re.search(
        rf"(?ms)^\[{re.escape(section_name)}\]\s*$"
        r"(.*?)(?=^\[[^\n]+\]\s*$|\Z)",
        sanitized,
    )
    return match.group(1) if match is not None else ""


def _toml_array_assignment_values(
    section: str,
    key: str,
) -> tuple[str, ...]:
    match = re.search(
        rf"(?ms)^\s*{re.escape(key)}\s*=\s*\[(.*?)\]",
        section,
    )
    return _toml_quoted_values(match.group(1)) if match is not None else ()


def _toml_quoted_values(text: str) -> tuple[str, ...]:
    values: list[str] = []
    for match in re.finditer(
        r'"((?:\\.|[^"\\])*)"|'
        r"'([^']*)'",
        text,
    ):
        value = _decode_toml_string(match.group(1), match.group(2))
        if value:
            values.append(value)
    return tuple(values)


def _decode_toml_string(
    double_quoted: str | None,
    single_quoted: str | None,
) -> str:
    if double_quoted is not None:
        try:
            decoded = json.loads(f'"{double_quoted}"')
        except json.JSONDecodeError:
            return ""
        return decoded if isinstance(decoded, str) else ""
    return single_quoted or ""


def direct_dependencies_from_uv_lock(text: str) -> frozenset[str]:
    """Extract the root package's locked direct dependency inventory."""

    match = re.search(
        r"(?ms)^\[\[package\]\]\s*\n"
        r"name = \"voistarcraft2\"\s*\n"
        r"(.*?)(?=^\[\[package\]\]|\Z)",
        text,
    )
    if match is None:
        return frozenset()
    return frozenset(
        normalized_dependency_name(name)
        for name in re.findall(r"\{\s*name = \"([^\"]+)\"", match.group(1))
        if normalized_dependency_name(name)
    )


def locked_distribution_versions_from_uv_lock(
    text: str,
    distributions: Iterable[str],
) -> dict[str, str]:
    """Return exact locked versions for the requested distributions."""

    requested = {
        normalized_dependency_name(distribution)
        for distribution in distributions
        if normalized_dependency_name(distribution)
    }
    versions: dict[str, str] = {}
    for raw_name, version in re.findall(
        r'(?ms)^\[\[package\]\]\s*\nname = "([^"]+)"\s*\n'
        r'version = "([^"]+)"',
        text,
    ):
        name = normalized_dependency_name(raw_name)
        if name in requested and name not in versions:
            versions[name] = version
    return dict(sorted(versions.items()))


def _locked_distribution_hashes_from_uv_lock(
    text: str,
    distribution: str,
) -> tuple[str, ...]:
    normalized = normalized_dependency_name(distribution)
    for match in re.finditer(
        r'(?ms)^\[\[package\]\]\s*\nname = "([^"]+)"\s*\n'
        r'(.*?)(?=^\[\[package\]\]|\Z)',
        text,
    ):
        if normalized_dependency_name(match.group(1)) != normalized:
            continue
        return tuple(
            sorted(set(re.findall(r'hash = "(sha256:[0-9a-f]{64})"', match.group(2))))
        )
    return ()


def notice_dependency_licenses(text: str) -> dict[str, str]:
    """Return one normalized declared license for each Python distribution."""

    licenses: dict[str, str] = {}
    for raw_name, section in _NOTICE_DISTRIBUTION_LICENSE_RE.findall(text):
        name = normalized_dependency_name(raw_name)
        matches = re.findall(r"(?m)^License:\s*([^\n]+?)\s*$", section)
        if not name or len(matches) != 1 or name in licenses:
            continue
        licenses[name] = matches[0]
    return dict(sorted(licenses.items()))


def normalized_dependency_name(requirement: str) -> str:
    """Return the normalized leading distribution name from a requirement."""

    match = _REQUIREMENT_NAME_RE.match(requirement)
    if match is None:
        return ""
    return re.sub(r"[-_.]+", "-", match.group(1).lower())


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and verify release distribution compliance."
    )
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--dist-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--skip-install-smoke", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    report = build_distribution_report(
        args.repository,
        source_root=args.source_root,
        dist_dir=args.dist_dir,
        run_install_smoke=not args.skip_install_smoke,
    )
    write_distribution_evidence(report, args.output_dir)
    print(canonical_json_text(report), end="")
    return 0 if report.get("ok") is True else 1


def _archive_path_error(name: str) -> str:
    if not name or "\x00" in name or "\\" in name:
        return "invalid_name"
    canonical_name = _canonical_archive_name(name)
    if not canonical_name:
        return "invalid_name"
    raw_parts = canonical_name.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        return "path_traversal"
    for part in raw_parts:
        if (
            ":" in part
            or part.endswith((" ", "."))
            or any(ord(character) < 32 for character in part)
        ):
            return "non_portable_component"
        reserved_stem = part.split(".", 1)[0].casefold()
        if reserved_stem in _WINDOWS_RESERVED_COMPONENTS:
            return "reserved_component"
    path = PurePosixPath(canonical_name)
    if path.is_absolute():
        return "absolute_path"
    return ""


def _canonical_archive_name(name: str) -> str:
    return name[:-1] if name.endswith("/") else name


def _portable_archive_key(name: str) -> str:
    return "/".join(
        unicodedata.normalize("NFKC", part).casefold()
        for part in name.split("/")
    )


def _archive_entry_evidence_blockers(
    kind: str,
    archive_path: Path,
    entries: Sequence[object],
) -> list[dict[str, object]]:
    blockers: list[dict[str, object]] = []
    expected_sdist_root = (
        _expected_sdist_root(archive_path) if kind == "sdist" else ""
    )
    if kind == "wheel" and not _expected_wheel_dist_info_root(archive_path):
        blockers.append(
            {
                "code": "invalid_wheel_filename",
                "filename": archive_path.name,
            }
        )
    if kind == "sdist" and not expected_sdist_root:
        blockers.append(
            {
                "code": "invalid_sdist_filename",
                "filename": archive_path.name,
            }
        )
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, str):
            blockers.append(
                {"code": "invalid_artifact_entry_evidence", "kind": kind}
            )
            continue
        path_error = _archive_path_error(entry)
        if path_error:
            blockers.append(
                {
                    "code": "unsafe_archive_entry",
                    "kind": kind,
                    "entry": entry,
                    "reason": path_error,
                }
            )
            continue
        canonical_name = _canonical_archive_name(entry)
        portable_key = _portable_archive_key(canonical_name)
        if portable_key in seen:
            blockers.append(
                {
                    "code": "duplicate_archive_entry",
                    "kind": kind,
                    "entry": entry,
                }
            )
            continue
        seen.add(portable_key)
        if kind == "sdist":
            path = PurePosixPath(canonical_name)
            if (
                not expected_sdist_root
                or not path.parts
                or path.parts[0] != expected_sdist_root
            ):
                blockers.append(
                    {
                        "code": "invalid_archive_root",
                        "kind": kind,
                        "entry": entry,
                        "expected_root": expected_sdist_root,
                    }
                )
    return blockers


def _archive_relative_path(
    kind: str,
    archive_path: Path,
    entry: str,
) -> PurePosixPath | None:
    path = PurePosixPath(_canonical_archive_name(entry))
    if kind == "wheel":
        return path
    expected_root = _expected_sdist_root(archive_path)
    if (
        len(path.parts) < 2
        or not expected_root
        or path.parts[0] != expected_root
    ):
        return None
    return PurePosixPath(*path.parts[1:])


def _expected_sdist_root(path: Path) -> str:
    filename = path.name
    if not filename.endswith(".tar.gz"):
        return ""
    root = filename[: -len(".tar.gz")]
    if not root.startswith(f"{EXPECTED_DISTRIBUTION_NAME}-"):
        return ""
    return root


def _expected_wheel_dist_info_root(path: Path) -> str:
    filename = path.name
    if not filename.endswith(".whl"):
        return ""
    components = filename[: -len(".whl")].split("-")
    if len(components) < 5:
        return ""
    distribution, version = components[:2]
    if (
        not version
        or normalized_dependency_name(distribution)
        != normalized_dependency_name(EXPECTED_DISTRIBUTION_NAME)
    ):
        return ""
    return f"{distribution}-{version}.dist-info"


def _denied_distribution_path(path: PurePosixPath) -> str:
    lowered = tuple(part.lower() for part in path.parts)
    for part in lowered:
        if part in _DENIED_PATH_COMPONENTS:
            return f"denied_component:{part}"
        if part == ".env" or part.startswith(".env."):
            return "local_environment_file"
        if _is_credential_path(part, set(lowered)):
            return "credential_file"
    if path.name.endswith((".pyc", ".pyo")):
        return "python_bytecode"
    return ""


def _allowed_integration_path(path: PurePosixPath) -> bool:
    prefix = ("integrations", "micromachine")
    if path.parts[:2] != prefix:
        return False
    tail = path.parts[2:]
    if len(tail) == 1:
        return tail[0] in {
            "HOOK_MANIFEST.json",
            "MICROMACHINE_MAP_POOL.json",
            "PRE_LIVE_JOURNEYS.json",
            "PRE_LIVE_PRODUCERS.json",
            "voi_policy_blackboard.hpp",
        }
    if len(tail) == 2 and tail[0] == "patches":
        return bool(re.fullmatch(r"\d{4}-[a-z0-9-]+\.patch", tail[1]))
    if len(tail) == 2 and tail[0] == "scripts":
        return tail[1] in {
            "build_macos_local.sh",
            "probe_macos_local.sh",
            "smoke_macos_local.sh",
            "soak_macos_local.sh",
            "soak_matrix_macos_local.sh",
            "strategy_matrix_macos_local.sh",
        }
    return False


def _allowed_wheel_source_path(path: PurePosixPath) -> bool:
    if path.parts and path.parts[0] in PRODUCT_PACKAGE_ROOTS:
        return path.suffix == ".py"
    if path.as_posix() in {
        "integrations/__init__.py",
        "integrations/micromachine/__init__.py",
    }:
        return True
    return _allowed_integration_path(path)


def _allowed_wheel_path(path: PurePosixPath, archive_path: Path) -> bool:
    if _allowed_wheel_source_path(path):
        return True
    expected_root = _expected_wheel_dist_info_root(archive_path)
    if (
        expected_root
        and len(path.parts) >= 2
        and path.parts[0] == expected_root
    ):
        tail = PurePosixPath(*path.parts[1:]).as_posix()
        if tail in _DIST_INFO_FILES:
            return True
        return tail in {
            "licenses/LICENSE",
            "licenses/LICENSES/AGPL-3.0-or-later.txt",
            "licenses/THIRD_PARTY_NOTICES.md",
        }
    return False


def _allowed_wheel_directory(path: PurePosixPath, archive_path: Path) -> bool:
    expected_dist_info = _expected_wheel_dist_info_root(archive_path)
    return path.parts in {
        *((root,) for root in PRODUCT_PACKAGE_ROOTS),
        ("integrations",),
        ("integrations", "micromachine"),
        ("integrations", "micromachine", "patches"),
        ("integrations", "micromachine", "scripts"),
        (expected_dist_info,),
        (expected_dist_info, "licenses"),
        (expected_dist_info, "licenses", "LICENSES"),
    }


def _allowed_sdist_source_path(path: PurePosixPath) -> bool:
    if (
        len(path.parts) == 1
        and path.name in _SDIST_ROOT_FILES - {"PKG-INFO", "setup.cfg"}
    ):
        return True
    if path.as_posix() == "LICENSES/AGPL-3.0-or-later.txt":
        return True
    if path.parts and path.parts[0] in PRODUCT_PACKAGE_ROOTS:
        return path.suffix == ".py"
    if path.as_posix() in {
        "integrations/__init__.py",
        "integrations/micromachine/__init__.py",
    }:
        return True
    return _allowed_integration_path(path)


def _allowed_sdist_path(path: PurePosixPath) -> bool:
    if _allowed_sdist_source_path(path):
        return True
    if len(path.parts) == 1 and path.name in {"PKG-INFO", "setup.cfg"}:
        return True
    expected_egg_info = EXPECTED_PROJECT_NAME + ".egg-info"
    return (
        len(path.parts) == 2
        and path.parts[0] == expected_egg_info
        and path.parts[1] in _EGG_INFO_FILES
    )


def _allowed_sdist_directory(path: PurePosixPath) -> bool:
    expected_egg_info = EXPECTED_PROJECT_NAME + ".egg-info"
    return path.parts in {
        *((root,) for root in PRODUCT_PACKAGE_ROOTS),
        ("LICENSES",),
        ("integrations",),
        ("integrations", "micromachine"),
        ("integrations", "micromachine", "patches"),
        ("integrations", "micromachine", "scripts"),
        (expected_egg_info,),
    }


def expected_archive_payloads(
    repository_root: Path,
    head: str,
) -> dict[str, dict[str, str]]:
    """Return exact path and content manifests from one immutable Git commit."""

    manifests, _ = _expected_archive_payload_evidence(repository_root, head)
    return manifests


def _expected_archive_payload_evidence(
    repository_root: Path,
    head: str,
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, int]]]:
    """Return Git-derived release payload digest and size manifests."""

    if re.fullmatch(r"[0-9a-f]{40,64}", head) is None:
        raise ValueError("release manifest requires an exact Git commit")
    tree = _git_output(
        repository_root,
        ["ls-tree", "-r", "-z", "--full-tree", head],
    )
    wheel: dict[str, str] = {}
    sdist: dict[str, str] = {}
    wheel_sizes: dict[str, int] = {}
    sdist_sizes: dict[str, int] = {}
    blob_cache: dict[str, tuple[str, int]] = {}
    for raw_record in tree.split(b"\0"):
        if not raw_record:
            continue
        metadata, separator, raw_path = raw_record.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 3:
            raise RuntimeError("Git tree contains an unsupported release entry")
        path_text = raw_path.decode("utf-8", errors="strict")
        path = PurePosixPath(path_text)
        wheel_allowed = _allowed_wheel_source_path(path)
        sdist_allowed = _allowed_sdist_source_path(path)
        if not wheel_allowed and not sdist_allowed:
            continue
        if fields[1] != b"blob":
            raise RuntimeError("release payload is not a Git blob")
        object_id = fields[2].decode("ascii", errors="strict")
        blob_evidence = blob_cache.get(object_id)
        if blob_evidence is None:
            payload = _git_output(
                repository_root,
                ["cat-file", "blob", object_id],
            )
            blob_evidence = (sha256_bytes(payload), len(payload))
            blob_cache[object_id] = blob_evidence
        digest, size = blob_evidence
        if wheel_allowed:
            wheel[path_text] = digest
            wheel_sizes[path_text] = size
        if sdist_allowed:
            sdist[path_text] = digest
            sdist_sizes[path_text] = size
    return (
        {"wheel": wheel, "sdist": sdist},
        {"wheel": wheel_sizes, "sdist": sdist_sizes},
    )


def archive_manifest_blockers(
    snapshot: ArchiveSnapshot,
    expected_paths: Mapping[str, str],
) -> list[dict[str, object]]:
    """Return blockers for payload path or content drift from the Git tree."""

    return _archive_manifest_digest_blockers(
        snapshot.kind,
        snapshot.path,
        {
            entry: sha256_bytes(payload)
            for entry, payload in snapshot.files.items()
        },
        expected_paths,
    )


def _archive_manifest_digest_blockers(
    kind: str,
    archive_path: Path,
    file_manifest: Mapping[str, str],
    expected_paths: Mapping[str, str],
) -> list[dict[str, object]]:
    blockers: list[dict[str, object]] = []
    observed: dict[str, str] = {}
    for entry, digest in file_manifest.items():
        path_error = _archive_path_error(entry)
        if path_error:
            blockers.append(
                {
                    "code": "unsafe_archive_entry",
                    "kind": kind,
                    "entry": entry,
                    "reason": path_error,
                }
            )
            continue
        relative = _archive_relative_path(kind, archive_path, entry)
        if relative is None:
            blockers.append(
                {
                    "code": "invalid_archive_root",
                    "kind": kind,
                    "entry": entry,
                }
            )
            continue
        if _generated_archive_path(kind, archive_path, relative):
            continue
        relative_path = relative.as_posix()
        if relative_path in observed:
            blockers.append(
                {
                    "code": "duplicate_archive_payload_path",
                    "kind": kind,
                    "entry": entry,
                    "relative_path": relative_path,
                }
            )
            continue
        observed[relative_path] = digest
    expected = dict(expected_paths)
    for path in sorted(set(expected) - set(observed)):
        blockers.append(
            {
                "code": "missing_archive_entry",
                "kind": kind,
                "entry": path,
            }
        )
    for path in sorted(set(observed) - set(expected)):
        blockers.append(
            {
                "code": "unexpected_archive_payload",
                "kind": kind,
                "entry": path,
            }
        )
    for path in sorted(set(expected) & set(observed)):
        observed_digest = observed[path]
        if observed_digest != expected[path]:
            blockers.append(
                {
                    "code": "archive_payload_mismatch",
                    "kind": kind,
                    "entry": path,
                    "observed": observed_digest,
                    "expected": expected[path],
                }
            )
    return blockers


def _safe_fixture_match(path: str, rule_id: str, matched: str) -> bool:
    allowed = _SAFE_FIXTURE_FINGERPRINTS.get(path, {}).get(rule_id, frozenset())
    fingerprint = sha256_bytes(f"{rule_id}\0{matched}".encode("utf-8"))
    return fingerprint in allowed


def _generated_archive_path(
    kind: str,
    archive_path: Path,
    path: PurePosixPath,
) -> bool:
    if kind == "wheel":
        expected_root = _expected_wheel_dist_info_root(archive_path)
        return bool(
            expected_root and path.parts and path.parts[0] == expected_root
        )
    expected_egg_info = EXPECTED_PROJECT_NAME + ".egg-info"
    return (
        (len(path.parts) == 1 and path.name in {"PKG-INFO", "setup.cfg"})
        or bool(path.parts and path.parts[0] == expected_egg_info)
    )


def _required_generated_archive_files(
    kind: str,
    archive_path: Path,
) -> frozenset[str]:
    if kind == "wheel":
        root = _expected_wheel_dist_info_root(archive_path)
        if not root:
            return frozenset()
        return frozenset(
            {
                *(f"{root}/{name}" for name in _DIST_INFO_FILES),
                f"{root}/licenses/LICENSE",
                f"{root}/licenses/LICENSES/AGPL-3.0-or-later.txt",
                f"{root}/licenses/THIRD_PARTY_NOTICES.md",
            }
        )
    root = _expected_sdist_root(archive_path)
    if not root:
        return frozenset()
    egg_info_root = f"{root}/{EXPECTED_PROJECT_NAME}.egg-info"
    return frozenset(
        {
            f"{root}/PKG-INFO",
            f"{root}/setup.cfg",
            *(f"{egg_info_root}/{name}" for name in _EGG_INFO_FILES),
        }
    )


def _required_generated_metadata_files(
    kind: str,
    archive_path: Path,
) -> frozenset[str]:
    if kind == "wheel":
        root = _expected_wheel_dist_info_root(archive_path)
        if not root:
            return frozenset()
        return frozenset(f"{root}/{name}" for name in _DIST_INFO_FILES)
    root = _expected_sdist_root(archive_path)
    if not root:
        return frozenset()
    egg_info_root = f"{root}/{EXPECTED_PROJECT_NAME}.egg-info"
    return frozenset(
        {
            f"{root}/PKG-INFO",
            f"{root}/setup.cfg",
            *(f"{egg_info_root}/{name}" for name in _EGG_INFO_FILES),
        }
    )


def _sha256_manifest(value: object) -> dict[str, str] | None:
    if not isinstance(value, Mapping) or not value:
        return None
    manifest: dict[str, str] = {}
    for raw_path, raw_digest in value.items():
        if (
            not isinstance(raw_path, str)
            or not raw_path
            or not isinstance(raw_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", raw_digest) is None
        ):
            return None
        manifest[raw_path] = raw_digest
    return manifest


def _size_manifest(value: object) -> dict[str, int] | None:
    if not isinstance(value, Mapping) or not value:
        return None
    manifest: dict[str, int] = {}
    for raw_path, raw_size in value.items():
        if (
            not isinstance(raw_path, str)
            or not raw_path
            or type(raw_size) is not int
            or raw_size < 0
        ):
            return None
        manifest[raw_path] = raw_size
    return manifest


def _metadata_description_payload(raw: str) -> str:
    normalized = raw.replace("\r\n", "\n")
    _, separator, payload = normalized.partition("\n\n")
    return payload if separator else ""


def _core_metadata_semantic_blockers(
    parsed: object,
    raw: str,
    expectations: Mapping[str, object],
    readme_digest: str,
    *,
    code: str,
    entry: str,
) -> list[dict[str, object]]:
    blockers: list[dict[str, object]] = []
    if not expectations:
        return [
            {
                "code": code,
                "reason": "missing_source_metadata_contract",
                "entry": entry,
            }
        ]
    expected_fields: dict[str, list[str]] = {
        "Metadata-Version": [EXPECTED_METADATA_VERSION],
        "Name": [str(expectations.get("name", ""))],
        "Version": [str(expectations.get("version", ""))],
        "Summary": [str(expectations.get("summary", ""))],
        "License-Expression": [
            str(expectations.get("license_expression", ""))
        ],
        "Keywords": [
            ",".join(
                str(value)
                for value in _sequence(expectations.get("keywords"))
            )
        ],
        "Requires-Python": [
            str(expectations.get("requires_python", ""))
        ],
        "Description-Content-Type": [
            str(expectations.get("description_content_type", ""))
        ],
        "License-File": [
            str(value)
            for value in _sequence(expectations.get("license_files"))
        ],
        "Provides-Extra": [
            str(value)
            for value in _sequence(expectations.get("provides_extra"))
        ],
        "Requires-Dist": [
            str(value)
            for value in _sequence(expectations.get("requires_dist"))
        ],
        "Dynamic": list(EXPECTED_DYNAMIC_METADATA_FIELDS),
    }
    expected_keys = {
        field for field, values in expected_fields.items() if values
    }
    actual_keys = set(parsed.keys()) if hasattr(parsed, "keys") else set()
    if actual_keys != expected_keys:
        blockers.append(
            {
                "code": code,
                "reason": "metadata_header_set_mismatch",
                "entry": entry,
                "expected": sorted(expected_keys),
                "observed": sorted(actual_keys),
            }
        )
    reason_by_field = {
        "Metadata-Version": "wrong_metadata_version",
        "Name": "wrong_project_name",
        "Version": "wrong_project_version",
        "Summary": "wrong_summary",
        "License-Expression": "wrong_license_expression",
        "Keywords": "wrong_keywords",
        "Requires-Python": "wrong_requires_python",
        "Description-Content-Type": "wrong_description_content_type",
        "License-File": "wrong_license_files",
        "Provides-Extra": "wrong_provides_extra",
        "Requires-Dist": "source_requires_dist_mismatch",
        "Dynamic": "wrong_dynamic_fields",
    }
    for field, expected in expected_fields.items():
        observed = list(parsed.get_all(field, []))
        if field == "Requires-Dist":
            observed = sorted(observed)
            expected = sorted(expected)
        if observed != expected:
            blockers.append(
                {
                    "code": code,
                    "reason": reason_by_field[field],
                    "entry": entry,
                    "expected": expected,
                    "observed": observed,
                }
            )
    description = _metadata_description_payload(raw)
    if (
        not readme_digest
        or sha256_bytes(description.encode("utf-8")) != readme_digest
    ):
        blockers.append(
            {
                "code": code,
                "reason": "description_payload_mismatch",
                "entry": entry,
            }
        )
    return blockers


def _metadata_evidence_blockers(
    metadata: Mapping[str, object],
    source_pyproject: Mapping[str, object],
    wheel_path: Path | None,
    sdist_path: Path | None,
    wheel_file_manifest: Mapping[str, str] | None,
    sdist_file_manifest: Mapping[str, str] | None,
    wheel_file_sizes: Mapping[str, int] | None,
    sdist_file_sizes: Mapping[str, int] | None,
    sdist_expected_manifest: Mapping[str, str] | None,
    wheel_expected_sizes: Mapping[str, int] | None,
    sdist_expected_sizes: Mapping[str, int] | None,
    dependencies: Mapping[str, object],
) -> list[dict[str, object]]:
    blockers: list[dict[str, object]] = []
    if (
        wheel_path is None
        or sdist_path is None
        or wheel_file_manifest is None
        or sdist_file_manifest is None
        or wheel_file_sizes is None
        or sdist_file_sizes is None
        or sdist_expected_manifest is None
        or wheel_expected_sizes is None
        or sdist_expected_sizes is None
    ):
        return [
            {
                "code": "invalid_metadata_evidence",
                "reason": "missing_artifact_manifest",
            }
        ]
    source_raw = source_pyproject.get("raw")
    source_digest = source_pyproject.get("sha256")
    source_version = ""
    source_requires_python = ""
    source_summary = ""
    source_requires_dist: list[str] = []
    source_metadata_expectations: dict[str, object] = {}
    source_readme_digest = ""
    if not isinstance(source_raw, str) or not source_raw:
        blockers.append(
            {
                "code": "invalid_source_pyproject_evidence",
                "reason": "missing_raw",
            }
        )
    else:
        observed_source_digest = sha256_bytes(source_raw.encode("utf-8"))
        if (
            source_digest != observed_source_digest
            or sdist_expected_manifest.get("pyproject.toml")
            != observed_source_digest
        ):
            blockers.append(
                {
                    "code": "invalid_source_pyproject_evidence",
                    "reason": "digest_mismatch",
                }
            )
        source_metadata_expectations = (
            project_metadata_expectations_from_pyproject(source_raw)
        )
        source_version = str(
            source_metadata_expectations.get("version", "")
        )
        source_requires_python = str(
            source_metadata_expectations.get("requires_python", "")
        )
        source_summary = str(
            source_metadata_expectations.get("summary", "")
        )
        source_requires_dist = [
            str(value)
            for value in _sequence(
                source_metadata_expectations.get("requires_dist")
            )
        ]
        source_readme = str(
            source_metadata_expectations.get("readme", "")
        )
        source_readme_digest = str(
            sdist_expected_manifest.get(source_readme, "")
        )
        if (
            not source_metadata_expectations
            or source_metadata_expectations.get("name")
            != EXPECTED_PROJECT_NAME
            or source_metadata_expectations.get("license_expression")
            != EXPECTED_LICENSE_EXPRESSION
            or tuple(
                _sequence(
                    source_metadata_expectations.get("license_files")
                )
            )
            != REQUIRED_LICENSE_FILES
            or not source_readme_digest
        ):
            blockers.append(
                {
                    "code": "invalid_source_pyproject_evidence",
                    "reason": "invalid_metadata_contract",
                }
            )
        if not source_version:
            blockers.append(
                {
                    "code": "invalid_source_pyproject_evidence",
                    "reason": "missing_version",
                }
            )
        if not source_requires_python:
            blockers.append(
                {
                    "code": "invalid_source_pyproject_evidence",
                    "reason": "missing_requires_python",
                }
            )
        if not source_summary:
            blockers.append(
                {
                    "code": "invalid_source_pyproject_evidence",
                    "reason": "missing_summary",
                }
            )
    blockers.extend(
        _generated_metadata_evidence_blockers(
            metadata.get("generated"),
            source_raw if isinstance(source_raw, str) else "",
            wheel_path,
            sdist_path,
            wheel_file_manifest,
            sdist_file_manifest,
            wheel_file_sizes,
            sdist_file_sizes,
            sdist_expected_manifest,
            wheel_expected_sizes,
            sdist_expected_sizes,
        )
    )
    expected_root = _expected_wheel_dist_info_root(wheel_path)
    expected_entry = f"{expected_root}/METADATA" if expected_root else ""
    entry = metadata.get("entry")
    raw = metadata.get("raw")
    reported_expressions = metadata.get("license_expressions")
    reported_requires_dist = metadata.get("requires_dist")
    if entry != expected_entry:
        blockers.append(
            {
                "code": "invalid_metadata_evidence",
                "reason": "wrong_entry",
                "expected": expected_entry,
            }
        )
    if not isinstance(raw, str) or not raw:
        blockers.append(
            {"code": "invalid_metadata_evidence", "reason": "missing_raw"}
        )
        return blockers
    if (
        not isinstance(reported_expressions, list)
        or any(not isinstance(item, str) for item in reported_expressions)
    ):
        blockers.append(
            {
                "code": "invalid_metadata_evidence",
                "reason": "invalid_license_projection",
            }
        )
    if (
        not isinstance(reported_requires_dist, list)
        or any(not isinstance(item, str) for item in reported_requires_dist)
    ):
        blockers.append(
            {
                "code": "invalid_metadata_evidence",
                "reason": "invalid_requires_dist_projection",
            }
        )
    raw_bytes = raw.encode("utf-8")
    if wheel_file_manifest.get(expected_entry) != sha256_bytes(raw_bytes):
        blockers.append(
            {"code": "metadata_raw_digest_mismatch", "entry": expected_entry}
        )
    parsed = BytesParser().parsebytes(raw_bytes)
    raw_expressions = parsed.get_all("License-Expression", [])
    raw_requires_dist = sorted(parsed.get_all("Requires-Dist", []))
    blockers.extend(
        _core_metadata_semantic_blockers(
            parsed,
            raw,
            source_metadata_expectations,
            source_readme_digest,
            code="invalid_metadata_evidence",
            entry=expected_entry,
        )
    )
    if raw_expressions != reported_expressions:
        blockers.append(
            {
                "code": "invalid_metadata_evidence",
                "reason": "license_projection_mismatch",
            }
        )
    if raw_requires_dist != reported_requires_dist:
        blockers.append(
            {
                "code": "invalid_metadata_evidence",
                "reason": "requires_dist_projection_mismatch",
            }
        )
    if parsed.get_all("Name", []) != [EXPECTED_PROJECT_NAME]:
        blockers.append(
            {
                "code": "invalid_metadata_evidence",
                "reason": "wrong_project_name",
            }
        )
    wheel_filename_components = wheel_path.name[: -len(".whl")].split("-")
    wheel_filename_version = (
        wheel_filename_components[1]
        if wheel_path.name.endswith(".whl")
        and len(wheel_filename_components) >= 5
        else ""
    )
    sdist_filename = sdist_path.name.removesuffix(".tar.gz")
    sdist_filename_components = sdist_filename.split("-", 1)
    sdist_filename_version = (
        sdist_filename_components[1]
        if sdist_path.name.endswith(".tar.gz")
        and len(sdist_filename_components) == 2
        else ""
    )
    for kind, observed_version in (
        ("wheel", wheel_filename_version),
        ("sdist", sdist_filename_version),
    ):
        if not source_version or observed_version != source_version:
            blockers.append(
                {
                    "code": "invalid_metadata_evidence",
                    "reason": "artifact_version_mismatch",
                    "kind": kind,
                    "expected": source_version,
                    "observed": observed_version,
                }
            )
    if (
        not source_version
        or parsed.get_all("Version", []) != [source_version]
    ):
        blockers.append(
            {
                "code": "invalid_metadata_evidence",
                "reason": "wrong_project_version",
                "expected": source_version,
            }
        )
    if (
        not source_requires_python
        or parsed.get_all("Requires-Python", [])
        != [source_requires_python]
    ):
        blockers.append(
            {
                "code": "invalid_metadata_evidence",
                "reason": "wrong_requires_python",
                "expected": source_requires_python,
            }
        )
    if not source_summary or parsed.get_all("Summary", []) != [source_summary]:
        blockers.append(
            {
                "code": "invalid_metadata_evidence",
                "reason": "wrong_summary",
                "expected": source_summary,
            }
        )
    if source_raw and raw_requires_dist != source_requires_dist:
        blockers.append(
            {
                "code": "invalid_metadata_evidence",
                "reason": "source_requires_dist_mismatch",
            }
        )
    sdist_root = _expected_sdist_root(sdist_path)
    expected_sdist_entries = (
        f"{sdist_root}/PKG-INFO",
        f"{sdist_root}/{EXPECTED_PROJECT_NAME}.egg-info/PKG-INFO",
    )
    sdist_metadata = metadata.get("sdist")
    sdist_by_entry: dict[str, Mapping[str, object]] = {}
    if not isinstance(sdist_metadata, list):
        blockers.append(
            {
                "code": "invalid_sdist_metadata_evidence",
                "reason": "missing_projection",
            }
        )
    else:
        for item in sdist_metadata:
            evidence = _mapping(item)
            entry_value = evidence.get("entry")
            if (
                not isinstance(entry_value, str)
                or entry_value in sdist_by_entry
            ):
                blockers.append(
                    {
                        "code": "invalid_sdist_metadata_evidence",
                        "reason": "invalid_or_duplicate_entry",
                    }
                )
                continue
            sdist_by_entry[entry_value] = evidence
        if set(sdist_by_entry) != set(expected_sdist_entries):
            blockers.append(
                {
                    "code": "invalid_sdist_metadata_evidence",
                    "reason": "wrong_entry_set",
                    "expected": list(expected_sdist_entries),
                }
            )
    for sdist_entry in expected_sdist_entries:
        evidence = sdist_by_entry.get(sdist_entry)
        if evidence is None:
            continue
        sdist_raw = evidence.get("raw")
        if not isinstance(sdist_raw, str) or not sdist_raw:
            blockers.append(
                {
                    "code": "invalid_sdist_metadata_evidence",
                    "reason": "missing_raw",
                    "entry": sdist_entry,
                }
            )
            continue
        sdist_raw_bytes = sdist_raw.encode("utf-8")
        if sdist_file_manifest.get(sdist_entry) != sha256_bytes(sdist_raw_bytes):
            blockers.append(
                {
                    "code": "invalid_sdist_metadata_evidence",
                    "reason": "raw_digest_mismatch",
                    "entry": sdist_entry,
                }
            )
        parsed_sdist = BytesParser().parsebytes(sdist_raw_bytes)
        blockers.extend(
            _core_metadata_semantic_blockers(
                parsed_sdist,
                sdist_raw,
                source_metadata_expectations,
                source_readme_digest,
                code="invalid_sdist_metadata_evidence",
                entry=sdist_entry,
            )
        )
        if parsed_sdist.get_all("Name", []) != [EXPECTED_PROJECT_NAME]:
            blockers.append(
                {
                    "code": "invalid_sdist_metadata_evidence",
                    "reason": "wrong_project_name",
                    "entry": sdist_entry,
                }
            )
        if (
            not source_version
            or parsed_sdist.get_all("Version", []) != [source_version]
        ):
            blockers.append(
                {
                    "code": "invalid_sdist_metadata_evidence",
                    "reason": "wrong_project_version",
                    "entry": sdist_entry,
                }
            )
        if (
            not source_requires_python
            or parsed_sdist.get_all("Requires-Python", [])
            != [source_requires_python]
        ):
            blockers.append(
                {
                    "code": "invalid_sdist_metadata_evidence",
                    "reason": "wrong_requires_python",
                    "entry": sdist_entry,
                    "expected": source_requires_python,
                }
            )
        if (
            not source_summary
            or parsed_sdist.get_all("Summary", []) != [source_summary]
        ):
            blockers.append(
                {
                    "code": "invalid_sdist_metadata_evidence",
                    "reason": "wrong_summary",
                    "entry": sdist_entry,
                    "expected": source_summary,
                }
            )
        if parsed_sdist.get_all("License-Expression", []) != [
            EXPECTED_LICENSE_EXPRESSION
        ]:
            blockers.append(
                {
                    "code": "invalid_sdist_metadata_evidence",
                    "reason": "wrong_license_expression",
                    "entry": sdist_entry,
                }
            )
        if sorted(parsed_sdist.get_all("Requires-Dist", [])) != source_requires_dist:
            blockers.append(
                {
                    "code": "invalid_sdist_metadata_evidence",
                    "reason": "source_requires_dist_mismatch",
                    "entry": sdist_entry,
                }
            )
    raw_dependencies = sorted(
        {
            normalized_dependency_name(requirement)
            for requirement in raw_requires_dist
            if normalized_dependency_name(requirement)
        }
    )
    if dependencies.get("metadata") != raw_dependencies:
        blockers.append(
            {
                "code": "invalid_metadata_evidence",
                "reason": "dependency_projection_mismatch",
            }
        )
    return blockers


def _generated_metadata_evidence_blockers(
    generated_value: object,
    source_pyproject_raw: str,
    wheel_path: Path,
    sdist_path: Path,
    wheel_file_manifest: Mapping[str, str],
    sdist_file_manifest: Mapping[str, str],
    wheel_file_sizes: Mapping[str, int],
    sdist_file_sizes: Mapping[str, int],
    sdist_expected_manifest: Mapping[str, str],
    wheel_expected_sizes: Mapping[str, int],
    sdist_expected_sizes: Mapping[str, int],
) -> list[dict[str, object]]:
    blockers: list[dict[str, object]] = []
    generated = _mapping(generated_value)
    if set(generated) != {"wheel", "sdist"}:
        blockers.append(
            {
                "code": "invalid_generated_metadata_evidence",
                "reason": "wrong_kind_set",
            }
        )
    raw_by_kind: dict[str, dict[str, str]] = {}
    for kind, artifact_path, file_manifest, file_sizes in (
        (
            "wheel",
            wheel_path,
            wheel_file_manifest,
            wheel_file_sizes,
        ),
        (
            "sdist",
            sdist_path,
            sdist_file_manifest,
            sdist_file_sizes,
        ),
    ):
        expected_entries = _required_generated_metadata_files(
            kind,
            artifact_path,
        )
        by_entry: dict[str, str] = {}
        evidence_items = generated.get(kind)
        if not isinstance(evidence_items, list):
            blockers.append(
                {
                    "code": "invalid_generated_metadata_evidence",
                    "reason": "missing_projection",
                    "kind": kind,
                }
            )
        else:
            for item in evidence_items:
                evidence = _mapping(item)
                entry = evidence.get("entry")
                raw = evidence.get("raw")
                if (
                    not isinstance(entry, str)
                    or entry in by_entry
                    or not isinstance(raw, str)
                ):
                    blockers.append(
                        {
                            "code": "invalid_generated_metadata_evidence",
                            "reason": "invalid_or_duplicate_entry",
                            "kind": kind,
                        }
                    )
                    continue
                by_entry[entry] = raw
        if set(by_entry) != set(expected_entries):
            blockers.append(
                {
                    "code": "invalid_generated_metadata_evidence",
                    "reason": "wrong_entry_set",
                    "kind": kind,
                    "expected": sorted(expected_entries),
                }
            )
        for entry in sorted(expected_entries & set(by_entry)):
            raw_digest = sha256_bytes(by_entry[entry].encode("utf-8"))
            if (
                file_manifest.get(entry) != raw_digest
                or file_sizes.get(entry)
                != len(by_entry[entry].encode("utf-8"))
            ):
                blockers.append(
                    {
                        "code": "invalid_generated_metadata_evidence",
                        "reason": "raw_digest_mismatch",
                        "kind": kind,
                        "entry": entry,
                    }
                )
        raw_by_kind[kind] = by_entry
    expected_artifact_sizes = _expected_artifact_file_sizes(
        wheel_path,
        sdist_path,
        wheel_expected_sizes,
        sdist_expected_sizes,
        raw_by_kind,
    )
    for kind, observed_sizes in (
        ("wheel", wheel_file_sizes),
        ("sdist", sdist_file_sizes),
    ):
        if observed_sizes != expected_artifact_sizes[kind]:
            blockers.append(
                {
                    "code": "invalid_generated_metadata_evidence",
                    "reason": "artifact_file_size_provenance_mismatch",
                    "kind": kind,
                }
            )
    blockers.extend(
        _wheel_generated_metadata_blockers(
            wheel_path,
            wheel_file_manifest,
            expected_artifact_sizes["wheel"],
            raw_by_kind.get("wheel", {}),
        )
    )
    blockers.extend(
        _sdist_generated_metadata_blockers(
            source_pyproject_raw,
            sdist_path,
            sdist_expected_manifest,
            raw_by_kind.get("sdist", {}),
        )
    )
    return blockers


def _expected_artifact_file_sizes(
    wheel_path: Path,
    sdist_path: Path,
    wheel_source_sizes: Mapping[str, int],
    sdist_source_sizes: Mapping[str, int],
    raw_by_kind: Mapping[str, Mapping[str, str]],
) -> dict[str, dict[str, int]]:
    wheel_sizes = dict(wheel_source_sizes)
    wheel_root = _expected_wheel_dist_info_root(wheel_path)
    if wheel_root:
        for relative in REQUIRED_LICENSE_FILES:
            source_size = sdist_source_sizes.get(relative)
            if source_size is not None:
                wheel_sizes[
                    f"{wheel_root}/licenses/{relative}"
                ] = source_size
    wheel_sizes.update(
        {
            entry: len(raw.encode("utf-8"))
            for entry, raw in raw_by_kind.get("wheel", {}).items()
        }
    )
    sdist_root = _expected_sdist_root(sdist_path)
    sdist_sizes = {
        f"{sdist_root}/{entry}": size
        for entry, size in sdist_source_sizes.items()
    }
    sdist_sizes.update(
        {
            entry: len(raw.encode("utf-8"))
            for entry, raw in raw_by_kind.get("sdist", {}).items()
        }
    )
    return {"wheel": wheel_sizes, "sdist": sdist_sizes}


def _wheel_generated_metadata_blockers(
    wheel_path: Path,
    file_manifest: Mapping[str, str],
    file_sizes: Mapping[str, int],
    raw_by_entry: Mapping[str, str],
) -> list[dict[str, object]]:
    blockers: list[dict[str, object]] = []
    root = _expected_wheel_dist_info_root(wheel_path)
    if not root:
        return [
            {
                "code": "invalid_generated_metadata_evidence",
                "reason": "invalid_wheel_namespace",
                "kind": "wheel",
            }
        ]
    wheel_entry = f"{root}/WHEEL"
    wheel_raw = raw_by_entry.get(wheel_entry)
    if wheel_raw is not None:
        parsed = BytesParser().parsebytes(wheel_raw.encode("utf-8"))
        if (
            set(parsed.keys())
            != {"Wheel-Version", "Generator", "Root-Is-Purelib", "Tag"}
            or parsed.get_all("Wheel-Version", []) != ["1.0"]
            or parsed.get_all("Root-Is-Purelib", []) != ["true"]
            or parsed.get_all("Tag", []) != ["py3-none-any"]
            or parsed.get_all("Generator", [])
            != [EXPECTED_BUILD_BACKEND_GENERATOR]
            or str(parsed.get_payload()).strip()
        ):
            blockers.append(
                {
                    "code": "invalid_generated_metadata_evidence",
                    "reason": "invalid_wheel_metadata",
                    "kind": "wheel",
                    "entry": wheel_entry,
                }
            )
    top_level_entry = f"{root}/top_level.txt"
    top_level_raw = raw_by_entry.get(top_level_entry)
    if (
        top_level_raw is not None
        and _nonempty_lines(top_level_raw) != EXPECTED_TOP_LEVEL_PACKAGES
    ):
        blockers.append(
            {
                "code": "invalid_generated_metadata_evidence",
                "reason": "top_level_mismatch",
                "kind": "wheel",
                "entry": top_level_entry,
            }
        )
    record_entry = f"{root}/RECORD"
    record_raw = raw_by_entry.get(record_entry)
    if record_raw is not None:
        blockers.extend(
            _wheel_record_blockers(
                record_entry,
                record_raw,
                file_manifest,
                file_sizes,
            )
        )
    return blockers


def _wheel_record_blockers(
    record_entry: str,
    record_raw: str,
    file_manifest: Mapping[str, str],
    file_sizes: Mapping[str, int],
) -> list[dict[str, object]]:
    invalid = {
        "code": "invalid_generated_metadata_evidence",
        "reason": "invalid_wheel_record",
        "kind": "wheel",
        "entry": record_entry,
    }
    try:
        rows = list(csv.reader(io.StringIO(record_raw, newline="")))
    except csv.Error:
        return [invalid]
    if any(len(row) != 3 for row in rows):
        return [invalid]
    by_path: dict[str, tuple[str, str]] = {}
    for path, digest, size in rows:
        if not path or path in by_path:
            return [invalid]
        by_path[path] = (digest, size)
    if set(by_path) != set(file_manifest):
        return [invalid]
    for path, expected_digest in file_manifest.items():
        recorded_digest, recorded_size = by_path[path]
        if path == record_entry:
            if recorded_digest or recorded_size:
                return [invalid]
            continue
        if (
            recorded_digest != _record_hash_from_sha256(expected_digest)
            or recorded_size != str(file_sizes.get(path, -1))
        ):
            return [invalid]
    return []


def _record_hash_from_sha256(digest: str) -> str:
    encoded = base64.urlsafe_b64encode(bytes.fromhex(digest)).decode("ascii")
    return f"sha256={encoded.rstrip('=')}"


def _sdist_generated_metadata_blockers(
    source_pyproject_raw: str,
    sdist_path: Path,
    expected_source_manifest: Mapping[str, str],
    raw_by_entry: Mapping[str, str],
) -> list[dict[str, object]]:
    blockers: list[dict[str, object]] = []
    root = _expected_sdist_root(sdist_path)
    if not root:
        return [
            {
                "code": "invalid_generated_metadata_evidence",
                "reason": "invalid_sdist_namespace",
                "kind": "sdist",
            }
        ]
    egg_root = f"{root}/{EXPECTED_PROJECT_NAME}.egg-info"
    setup_entry = f"{root}/setup.cfg"
    setup_raw = raw_by_entry.get(setup_entry)
    if setup_raw is not None and _nonempty_lines(setup_raw) != (
        "[egg_info]",
        "tag_build =",
        "tag_date = 0",
    ):
        blockers.append(
            {
                "code": "invalid_generated_metadata_evidence",
                "reason": "invalid_setup_cfg",
                "kind": "sdist",
                "entry": setup_entry,
            }
        )
    dependency_entry = f"{egg_root}/dependency_links.txt"
    dependency_raw = raw_by_entry.get(dependency_entry)
    if dependency_raw is not None and dependency_raw.strip():
        blockers.append(
            {
                "code": "invalid_generated_metadata_evidence",
                "reason": "unexpected_dependency_link",
                "kind": "sdist",
                "entry": dependency_entry,
            }
        )
    top_level_entry = f"{egg_root}/top_level.txt"
    top_level_raw = raw_by_entry.get(top_level_entry)
    if (
        top_level_raw is not None
        and _nonempty_lines(top_level_raw) != EXPECTED_TOP_LEVEL_PACKAGES
    ):
        blockers.append(
            {
                "code": "invalid_generated_metadata_evidence",
                "reason": "top_level_mismatch",
                "kind": "sdist",
                "entry": top_level_entry,
            }
        )
    requires_entry = f"{egg_root}/requires.txt"
    requires_raw = raw_by_entry.get(requires_entry)
    expected_groups = _requirement_groups_from_pyproject(
        source_pyproject_raw
    )
    if (
        requires_raw is not None
        and _requires_txt_groups(requires_raw) != expected_groups
    ):
        blockers.append(
            {
                "code": "invalid_generated_metadata_evidence",
                "reason": "source_requires_txt_mismatch",
                "kind": "sdist",
                "entry": requires_entry,
            }
        )
    sources_entry = f"{egg_root}/SOURCES.txt"
    sources_raw = raw_by_entry.get(sources_entry)
    expected_sources = {
        *expected_source_manifest,
        *(
            f"{EXPECTED_PROJECT_NAME}.egg-info/{name}"
            for name in _EGG_INFO_FILES
        ),
    }
    if sources_raw is not None:
        source_lines = sources_raw.splitlines()
        if (
            any(
                not line
                or line != PurePosixPath(line).as_posix()
                or _archive_path_error(line)
                for line in source_lines
            )
            or len(source_lines) != len(set(source_lines))
            or set(source_lines) != expected_sources
        ):
            blockers.append(
                {
                    "code": "invalid_generated_metadata_evidence",
                    "reason": "source_manifest_mismatch",
                    "kind": "sdist",
                    "entry": sources_entry,
                }
            )
    return blockers


def _requirement_groups_from_pyproject(
    text: str,
) -> dict[str, tuple[str, ...]]:
    groups: dict[str, tuple[str, ...]] = {
        "": _toml_array_assignment_values(
            _pyproject_section(text, "project"),
            "dependencies",
        )
    }
    optional_section = _pyproject_section(
        text,
        "project.optional-dependencies",
    )
    assignment_pattern = re.compile(
        r"(?ms)^\s*([A-Za-z0-9_.-]+)\s*=\s*\[(.*?)\]"
        r"(?=\s*(?:^[A-Za-z0-9_.-]+\s*=|\Z))"
    )
    for match in assignment_pattern.finditer(optional_section):
        extra = re.sub(r"[-_.]+", "-", match.group(1).lower())
        groups[extra] = _toml_quoted_values(match.group(2))
    return {key: value for key, value in groups.items() if value}


def _requires_txt_groups(raw: str) -> dict[str, tuple[str, ...]] | None:
    groups: dict[str, list[str]] = {}
    current = ""
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        section = re.fullmatch(r"\[([A-Za-z0-9_.-]+)\]", line)
        if section is not None:
            current = re.sub(r"[-_.]+", "-", section.group(1).lower())
            if current in groups:
                return None
            groups[current] = []
            continue
        groups.setdefault(current, []).append(line)
    normalized = {
        key: tuple(values)
        for key, values in groups.items()
        if values
    }
    if any(len(values) != len(set(values)) for values in normalized.values()):
        return None
    return normalized


def _nonempty_lines(raw: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in raw.splitlines() if line.strip())


def _is_credential_path(basename: str, lowered_parts: set[str]) -> bool:
    return (
        basename in _CREDENTIAL_FILENAMES
        or basename.endswith(".credentials.json")
        or ".aws" in lowered_parts
        or (
            ".ssh" in lowered_parts
            and basename.startswith(("id_", "identity"))
        )
    )


def _path_finding(path: str, rule_id: str) -> dict[str, object]:
    return {
        "path": path,
        "line": 0,
        "rule_id": rule_id,
        "fingerprint": sha256_bytes(f"{rule_id}\0{path}".encode("utf-8")),
    }


def _git_output(repository_root: Path, arguments: Sequence[str]) -> bytes:
    environment = os.environ.copy()
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository_root,
        env=environment,
        check=False,
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(arguments)} failed with status {result.returncode}"
        )
    if len(result.stdout) > MAX_GIT_OUTPUT_BYTES:
        raise RuntimeError("git output exceeds compliance scan limit")
    return result.stdout


def _git_replacement_refs(repository_root: Path) -> list[str]:
    return (
        _git_output(
            repository_root,
            ["for-each-ref", "--format=%(refname)", "refs/replace/"],
        )
        .decode("utf-8", errors="strict")
        .splitlines()
    )


def _trusted_archive_evidence(
    report: Mapping[str, object],
) -> tuple[
    dict[str, dict[str, str]],
    dict[str, dict[str, int]],
] | None:
    repository = _mapping(report.get("repository"))
    before = _mapping(repository.get("before"))
    root_value = before.get("repository_root")
    head = before.get("head")
    tree = before.get("tree")
    if (
        not isinstance(root_value, str)
        or not isinstance(head, str)
        or not isinstance(tree, str)
    ):
        return None
    repository_root = Path(root_value)
    try:
        actual_root = Path(
            _git_output(
                repository_root,
                ["rev-parse", "--show-toplevel"],
            )
            .decode("utf-8", errors="strict")
            .strip()
        ).resolve()
        actual_head = (
            _git_output(repository_root, ["rev-parse", "HEAD"])
            .decode("ascii", errors="strict")
            .strip()
        )
        actual_tree = (
            _git_output(repository_root, ["rev-parse", "HEAD^{tree}"])
            .decode("ascii", errors="strict")
            .strip()
        )
        if (
            _git_replacement_refs(repository_root)
            or before.get("replacement_refs") != []
            or actual_root != repository_root.resolve()
            or actual_head != head
            or actual_tree != tree
        ):
            return None
        return _expected_archive_payload_evidence(repository_root, head)
    except (OSError, RuntimeError, UnicodeError, ValueError):
        return None


def _trusted_artifact_evidence(
    report: Mapping[str, object],
) -> dict[str, dict[str, object]] | None:
    artifacts = _mapping(report.get("artifacts"))
    trusted: dict[str, dict[str, object]] = {}
    for kind, inspector in (
        ("wheel", inspect_wheel),
        ("sdist", inspect_sdist),
    ):
        artifact = _mapping(artifacts.get(kind))
        path_value = artifact.get("path")
        filename = artifact.get("filename")
        if (
            not isinstance(path_value, str)
            or not isinstance(filename, str)
            or not Path(path_value).is_absolute()
        ):
            return None
        candidate = Path(path_value)
        try:
            if candidate.is_symlink():
                return None
            resolved = candidate.resolve(strict=True)
            if (
                str(resolved) != path_value
                or resolved.name != filename
                or not resolved.is_file()
            ):
                return None
            snapshot = inspector(resolved)
        except (
            OSError,
            RuntimeError,
            ValueError,
            tarfile.TarError,
            zipfile.BadZipFile,
        ):
            return None
        evidence = _artifact_evidence(snapshot)
        evidence["archive_blockers"] = [
            dict(item) for item in snapshot.blockers
        ]
        trusted[kind] = evidence
    return trusted


def repository_state_evidence(
    repository_root: Path,
    source_root: Path,
) -> dict[str, object]:
    """Bind release evidence to one clean Git commit and source root."""

    repository_top = Path(
        _git_output(repository_root, ["rev-parse", "--show-toplevel"])
        .decode("utf-8", errors="strict")
        .strip()
    ).resolve()
    source_top = Path(
        _git_output(source_root, ["rev-parse", "--show-toplevel"])
        .decode("utf-8", errors="strict")
        .strip()
    ).resolve()
    dirty_entries = (
        _git_output(
            repository_root,
            ["status", "--porcelain=v1", "--untracked-files=all"],
        )
        .decode("utf-8", errors="replace")
        .splitlines()
    )
    head = (
        _git_output(repository_root, ["rev-parse", "HEAD"])
        .decode("ascii", errors="strict")
        .strip()
    )
    tree = (
        _git_output(repository_root, ["rev-parse", "HEAD^{tree}"])
        .decode("ascii", errors="strict")
        .strip()
    )
    replacement_refs = _git_replacement_refs(repository_root)
    source_root_matches = (
        repository_top == repository_root.resolve()
        and source_top == repository_top
        and source_root.resolve() == repository_top
    )
    return {
        "repository_root": str(repository_top),
        "source_root": str(source_root.resolve()),
        "source_root_matches": source_root_matches,
        "head": head,
        "tree": tree,
        "dirty_entries": dirty_entries,
        "replacement_refs": replacement_refs,
        "ok": source_root_matches and not dirty_entries and not replacement_refs,
    }


def _added_diff_payload(diff: bytes) -> bytes:
    """Return only added content lines from a unified Git diff."""

    return b"\n".join(
        line[1:]
        for line in diff.splitlines()
        if line.startswith(b"+") and not line.startswith(b"+++")
    )


def _build_distributions(source_root: Path, dist_dir: Path) -> None:
    for existing in (*dist_dir.glob("*.whl"), *dist_dir.glob("*.tar.gz")):
        existing.unlink()
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    uv_executable = shutil.which("uv")
    if uv_executable is None:
        raise RuntimeError("uv is required for locked distribution builds")
    pyproject_text = (source_root / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    lock_text = (source_root / "uv.lock").read_text(encoding="utf-8")
    build_requirements = build_requirements_from_pyproject(pyproject_text)
    locked_versions = locked_distribution_versions_from_uv_lock(
        lock_text,
        EXPECTED_BUILD_DISTRIBUTIONS,
    )
    locked_hashes = _locked_distribution_hashes_from_uv_lock(
        lock_text,
        "setuptools",
    )
    if (
        build_requirements != (EXPECTED_BUILD_BACKEND_REQUIREMENT,)
        or locked_versions
        != {"setuptools": EXPECTED_BUILD_BACKEND_VERSION}
        or not locked_hashes
    ):
        raise RuntimeError("build backend is not bound to the expected lock")
    with tempfile.TemporaryDirectory(
        prefix="voi-build-constraints-"
    ) as temporary:
        constraints_path = Path(temporary) / "build-constraints.txt"
        constraints_path.write_text(
            EXPECTED_BUILD_BACKEND_REQUIREMENT
            + "".join(f" --hash={digest}" for digest in locked_hashes)
            + "\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                uv_executable,
                "build",
                "--build-constraints",
                str(constraints_path),
                "--require-hashes",
                "--out-dir",
                str(dist_dir),
                str(source_root),
            ],
            cwd=source_root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
    if result.returncode != 0:
        raise RuntimeError(
            "distribution build failed: "
            + result.stderr[-4000:].replace("\n", " ")
        )


def _single_file_with_suffix(
    files: Mapping[str, bytes],
    suffix: str,
) -> tuple[str, bytes]:
    matches = [(name, payload) for name, payload in files.items() if name.endswith(suffix)]
    if len(matches) != 1:
        return "", b""
    return matches[0]


def _artifact_evidence(snapshot: ArchiveSnapshot) -> dict[str, object]:
    return {
        "path": str(snapshot.path.resolve()),
        "filename": snapshot.path.name,
        "sha256": snapshot.digest,
        "entry_count": len(snapshot.entries),
        "entries": list(snapshot.entries),
        "file_manifest": {
            entry: sha256_bytes(payload)
            for entry, payload in sorted(snapshot.files.items())
        },
        "file_sizes": {
            entry: len(payload)
            for entry, payload in sorted(snapshot.files.items())
        },
        "directory_entries": list(snapshot.directories),
    }


def _generated_metadata_evidence(
    snapshot: ArchiveSnapshot,
) -> list[dict[str, str]]:
    required = _required_generated_metadata_files(
        snapshot.kind,
        snapshot.path,
    )
    return [
        {
            "entry": entry,
            "raw": snapshot.files.get(entry, b"").decode(
                "utf-8",
                errors="replace",
            ),
        }
        for entry in sorted(required)
    ]


def _license_evidence(
    source_root: Path,
    wheel: ArchiveSnapshot,
    sdist: ArchiveSnapshot,
) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []
    for relative in REQUIRED_LICENSE_FILES:
        source = (source_root / relative).read_bytes()
        wheel_suffix = (
            f".dist-info/licenses/{relative}"
            if relative.startswith("LICENSES/")
            else f".dist-info/licenses/{relative}"
        )
        _, wheel_payload = _single_file_with_suffix(wheel.files, wheel_suffix)
        _, sdist_payload = _single_file_with_suffix(sdist.files, f"/{relative}")
        evidence.append(
            {
                "path": relative,
                "expected_sha256": EXPECTED_LICENSE_FILE_SHA256[relative],
                "source_sha256": sha256_bytes(source),
                "wheel_sha256": sha256_bytes(wheel_payload),
                "sdist_sha256": sha256_bytes(sdist_payload),
            }
        )
    return evidence


def _wheel_generator(snapshot: ArchiveSnapshot) -> str:
    entries = [
        payload
        for entry, payload in snapshot.files.items()
        if entry.endswith(".dist-info/WHEEL")
    ]
    if len(entries) != 1:
        return ""
    parsed = BytesParser().parsebytes(entries[0])
    generators = parsed.get_all("Generator", [])
    return generators[0] if len(generators) == 1 else ""


def _runtime_data_evidence(
    source_root: Path,
    wheel: ArchiveSnapshot,
    sdist: ArchiveSnapshot,
) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []
    for relative in REQUIRED_RUNTIME_FILES:
        source = (
            source_root / "integrations" / "micromachine" / relative
        ).read_bytes()
        wheel_suffix = (
            "integrations/micromachine/" + relative
        )
        _, wheel_payload = _single_file_with_suffix(wheel.files, wheel_suffix)
        _, sdist_payload = _single_file_with_suffix(
            sdist.files,
            "/integrations/micromachine/" + relative,
        )
        evidence.append(
            {
                "path": relative,
                "wheel_present": bool(wheel_payload),
                "sdist_present": bool(sdist_payload),
                "source_sha256": sha256_bytes(source),
                "wheel_sha256": sha256_bytes(wheel_payload),
                "sdist_sha256": sha256_bytes(sdist_payload),
            }
        )
    return evidence


def _with_derived_verdict(report: Mapping[str, object]) -> dict[str, object]:
    derived = dict(report)
    blockers = distribution_report_blockers(derived)
    derived["blockers"] = blockers
    derived["ok"] = not blockers
    derived["status"] = "passed" if not blockers else "blocked"
    return derived


def _deduplicate_blockers(
    blockers: Iterable[Mapping[str, object]],
) -> list[dict[str, object]]:
    observed: set[str] = set()
    result: list[dict[str, object]] = []
    for blocker in blockers:
        item = dict(blocker)
        identity = json.dumps(item, ensure_ascii=True, sort_keys=True)
        if identity in observed:
            continue
        observed.add(identity)
        result.append(item)
    return result


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return value
    return ()


def _string_list(value: object) -> list[str]:
    return [str(item) for item in _sequence(value) if isinstance(item, str)]


if __name__ == "__main__":
    raise SystemExit(main())
