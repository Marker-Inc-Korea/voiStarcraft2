"""Fail-closed distribution, licensing, and private-config verification."""

from __future__ import annotations

import argparse
import ast
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


DISTRIBUTION_COMPLIANCE_SCHEMA_VERSION: Final[int] = 5
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
    r"(?:^|[ \t])(?:(?i:ENV|ARG)|env)[ \t]+|"
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
    r"(?im)\b(?:os\.)?(?:putenv|environ\.setdefault)\s*\(\s*"
    r"[\"']"
    + _PRIVATE_MODEL_KEY_PATTERN
    + r"[\"']\s*,\s*[\"']([^\"'\n]+)[\"']"
)
_PRIVATE_ENDPOINT_ENV_CALL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?im)\b(?:os\.)?(?:putenv|environ\.setdefault)\s*\(\s*"
    r"[\"']"
    + _PRIVATE_ENDPOINT_KEY_PATTERN
    + r"[\"']\s*,\s*[\"']([^\"'\n]+)[\"']"
)
_MYPROXY_PROVIDER_PATTERN: Final[str] = (
    r"(?:myproxy|proxy|nomadamas|my-proxy)"
)
_CLI_OPTION_VALUE_SEPARATOR: Final[str] = (
    r"(?:[ \t]*=[ \t]*|[ \t]+|[\"'][ \t]*,[ \t]*[\"']?)"
)
_MYPROXY_CLI_MODEL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?im)^(?=[^\n]*--provider"
    + _CLI_OPTION_VALUE_SEPARATOR
    + r"[\"']?"
    + _MYPROXY_PROVIDER_PATTERN
    + r"\b)[^\n]*?--model"
    + _CLI_OPTION_VALUE_SEPARATOR
    + r"[\"']?([^\"'\s,\\\])}]+)"
)
_MYPROXY_CLI_ENDPOINT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?im)^(?=[^\n]*--provider"
    + _CLI_OPTION_VALUE_SEPARATOR
    + r"[\"']?"
    + _MYPROXY_PROVIDER_PATTERN
    + r"\b)[^\n]*?--(?:base-url|openai-base-url)"
    + _CLI_OPTION_VALUE_SEPARATOR
    + r"[\"']?([^\"'\s,\\\])}]+)"
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
_ENV_API_KEY_NAME_PATTERN: Final[str] = r"[A-Z][A-Z0-9_]{0,63}_API_KEY"
_ENV_API_KEY_VALUE_PATTERN: Final[str] = (
    r"(?:\"([^\"\r\n]{12,})\"|'([^'\r\n]{12,})'|"
    r"([A-Za-z0-9._~+/=!@#%^&*()-]{12,}))"
)
_ENV_KEY_ASSIGNMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?m)"
    + _CONFIG_ASSIGNMENT_PREFIX
    + _ENV_API_KEY_NAME_PATTERN
    + _CONFIG_ASSIGNMENT_KEY_SUFFIX
    + _CONFIG_ASSIGNMENT_SEPARATOR
    + _ENV_API_KEY_VALUE_PATTERN
)
_ENV_KEY_CALL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?m)\b(?:os\.)?(?:putenv|environ\.setdefault)\s*\(\s*"
    r"[\"']"
    + _ENV_API_KEY_NAME_PATTERN
    + r"[\"']\s*,\s*"
    + _ENV_API_KEY_VALUE_PATTERN
)
_ENV_KEY_DOCKER_RE: Final[re.Pattern[str]] = re.compile(
    r"(?im)^[ \t]*(?i:ENV|ARG)[ \t]+(?:"
    + _ENV_API_KEY_NAME_PATTERN
    + _CONFIG_ASSIGNMENT_KEY_SUFFIX
    + r"[ \t]+|[^\n]*?[ \t]+"
    + _ENV_API_KEY_NAME_PATTERN
    + _CONFIG_ASSIGNMENT_KEY_SUFFIX
    + r"[ \t]*=[ \t]*)"
    + _ENV_API_KEY_VALUE_PATTERN
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
            {"b8cbb6abbd7c6ba09041bb128a6907bb20f5464febde6f4d54159a3a1fa8b5a5"}
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
        ("api_key_assignment", _ENV_KEY_DOCKER_RE),
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
    structural_failures = list(configuration_failures)
    for candidate in tuple(scan_texts):
        python_cli_text, python_cli_failure = _python_cli_argument_text(
            normalized_path,
            candidate,
        )
        if python_cli_failure:
            structural_failures.append(
                python_cli_failure.split(":", 1)[0]
            )
        for normalized in (
            (
                candidate
                if _is_dockerfile_path(normalized_path)
                else _shell_continuation_text(candidate)
            ),
            python_cli_text,
            _python_sensitive_call_text(normalized_path, candidate),
        ):
            if normalized and normalized not in scan_texts:
                scan_texts = (*scan_texts, normalized)
    for candidate in tuple(scan_texts):
        joined_literals = _joined_quoted_literal_text(candidate)
        if joined_literals not in scan_texts:
            scan_texts = (*scan_texts, joined_literals)
    findings.extend(
        _path_finding(normalized_path, rule_id)
        for rule_id in dict.fromkeys(structural_failures)
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


def _shell_continuation_text(text: str) -> str:
    return re.sub(r"\\\r?\n[ \t]*", " ", text)


def _python_cli_argument_text(path: str, text: str) -> tuple[str, str]:
    if PurePosixPath(path).suffix.lower() != ".py":
        return "", ""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return "", ""

    commands: list[str] = []
    command_set: set[str] = set()
    cli_value = str | tuple[str, ...]
    cli_environment = dict[str, cli_value]
    dynamic_argument = "<dynamic-argument>"
    max_arguments = 96
    max_commands = 128
    max_environment_states = max_commands
    max_environment_values = 256
    max_literal_characters = 256
    max_analysis_steps = 1_000_000
    analysis_steps = 0
    analysis_limited = False
    analysis_limit_reason = ""
    known_function_prefix = "<known-function:"
    known_class_prefix = "<known-class:"
    known_lambda_prefix = "<known-lambda:"
    known_sequence_prefix = "<known-sequence:"
    known_mapping_prefix = "<known-mapping:"
    known_alternatives_prefix = "<known-alternatives:"
    known_object_prefix = "<known-object:"
    known_super_prefix = "<known-super:"
    function_definitions: dict[
        int,
        ast.FunctionDef | ast.AsyncFunctionDef,
    ] = {}
    function_definition_ids_by_name: dict[str, set[int]] = {}
    class_definitions: dict[int, ast.ClassDef] = {}
    class_method_definitions: dict[int, dict[str, int]] = {}
    lambda_definitions: dict[int, ast.Lambda] = {}
    function_references: dict[
        int,
        tuple[
            int,
            cli_environment,
            dict[str, cli_value],
            tuple[cli_value, ...],
            int | None,
        ],
    ] = {}
    lambda_references: dict[
        int,
        tuple[
            int,
            cli_environment,
            dict[str, cli_value],
            tuple[cli_value, ...],
            int | None,
        ],
    ] = {}
    class_value_definitions: dict[int, int] = {}
    class_value_bases: dict[int, tuple[int, ...] | None] = {}
    class_value_methods: dict[
        int,
        dict[str, tuple[cli_value, str]],
    ] = {}
    sequence_values: dict[int, tuple[str, ...]] = {}
    mapping_values: dict[int, dict[str, cli_value]] = {}
    alternative_values: dict[int, tuple[cli_value, ...]] = {}
    object_values: dict[int, dict[str, cli_value]] = {}
    object_class_identities: dict[int, int | None] = {}
    super_values: dict[int, tuple[int, cli_value]] = {}
    bound_callable_cache: dict[
        tuple[str, tuple[cli_value, ...], int | None],
        cli_value,
    ] = {}
    active_function_calls: set[int] = set()
    active_lambda_calls: set[int] = set()
    active_call_depth = 0
    max_call_depth = 48
    next_runtime_identity = 0
    lexical_names_key = "__python_cli_lexical_names__"

    def runtime_identity() -> int:
        nonlocal next_runtime_identity
        next_runtime_identity += 1
        return next_runtime_identity

    def mark_limited(reason: str) -> None:
        nonlocal analysis_limited, analysis_limit_reason
        analysis_limited = True
        if not analysis_limit_reason:
            analysis_limit_reason = reason

    def step(amount: int = 1) -> bool:
        nonlocal analysis_steps
        analysis_steps += amount
        if analysis_steps > max_analysis_steps:
            mark_limited("steps")
            return False
        return True

    def bounded_string(value: str) -> str:
        if len(value) <= max_literal_characters:
            return value
        half = max_literal_characters // 2
        return value[:half] + dynamic_argument + value[-half:]

    def bounded_sequence(values: Sequence[str]) -> tuple[str, ...]:
        if len(values) <= max_arguments:
            return tuple(values)
        full_text = " ".join(values)
        if re.search(
            r"(?i)--provider(?:[ \t]+|=)"
            + _MYPROXY_PROVIDER_PATTERN
            + r"\b",
            full_text,
        ):
            mark_limited("arguments")
        keep = set(range(max_arguments // 4))
        keep.update(
            range(
                max(0, len(values) - (max_arguments // 4)),
                len(values),
            )
        )
        markers = (
            "--provider",
            "--model",
            "--base-url",
            "--openai-base-url",
        )
        first_last: dict[str, list[int]] = {
            marker: [] for marker in markers
        }
        for index, value in enumerate(values):
            for marker in markers:
                if marker in value:
                    indexes = first_last[marker]
                    if not indexes:
                        indexes.append(index)
                    elif len(indexes) == 1:
                        indexes.append(index)
                    else:
                        indexes[-1] = index
        for indexes in first_last.values():
            for index in indexes:
                keep.update(
                    range(
                        max(0, index - 1),
                        min(len(values), index + 2),
                    )
                )
        selected = [
            values[index]
            for index in sorted(keep)[: max_arguments - 1]
        ]
        selected.append(dynamic_argument)
        return tuple(selected)

    def combine(
        left: tuple[str, ...],
        right: tuple[str, ...],
    ) -> tuple[str, ...]:
        return bounded_sequence((*left, *right))

    def stored_identity(
        value: cli_value | None,
        prefix: str,
    ) -> int | None:
        if not isinstance(value, str):
            return None
        if not value.startswith(prefix) or not value.endswith(">"):
            return None
        try:
            return int(value[len(prefix):-1])
        except ValueError:
            return None

    def store_sequence(values: tuple[str, ...]) -> str:
        identity = id(values)
        sequence_values[identity] = values
        return f"{known_sequence_prefix}{identity}>"

    def stored_sequence(value: cli_value | None) -> tuple[str, ...] | None:
        if isinstance(value, tuple):
            return value
        if not isinstance(value, str):
            return None
        identity = stored_identity(value, known_sequence_prefix)
        if identity is None:
            return None
        return sequence_values.get(identity)

    def store_mapping(values: dict[str, cli_value]) -> str:
        identity = id(values)
        mapping_values[identity] = values
        return f"{known_mapping_prefix}{identity}>"

    def stored_mapping(
        value: cli_value | None,
    ) -> dict[str, cli_value] | None:
        if not isinstance(value, str):
            return None
        identity = stored_identity(value, known_mapping_prefix)
        if identity is None:
            return None
        return mapping_values.get(identity)

    def store_object(class_identity: int | None = None) -> str:
        values: dict[str, cli_value] = {}
        identity = id(values)
        object_values[identity] = values
        object_class_identities[identity] = class_identity
        return f"{known_object_prefix}{identity}>"

    def stored_object_identity(value: cli_value | None) -> int | None:
        if not isinstance(value, str):
            return None
        return stored_identity(value, known_object_prefix)

    def stored_object(value: cli_value | None) -> dict[str, cli_value] | None:
        identity = stored_object_identity(value)
        if identity is None:
            return None
        return object_values.get(identity)

    def stored_class_identity(value: cli_value | None) -> int | None:
        if not isinstance(value, str):
            return None
        identity = stored_identity(value, known_class_prefix)
        if identity not in class_value_definitions:
            return None
        return identity

    def class_token(identity: int) -> str:
        return f"{known_class_prefix}{identity}>"

    def store_super(
        declaring_class_identity: int,
        receiver: cli_value,
    ) -> str:
        identity = runtime_identity()
        super_values[identity] = (declaring_class_identity, receiver)
        return f"{known_super_prefix}{identity}>"

    def stored_super(
        value: cli_value | None,
    ) -> tuple[int, cli_value] | None:
        if not isinstance(value, str):
            return None
        identity = stored_identity(value, known_super_prefix)
        if identity is None:
            return None
        return super_values.get(identity)

    def mapping_alternatives(
        value: cli_value | None,
    ) -> list[dict[str, cli_value]]:
        alternatives = stored_alternatives(value)
        if alternatives is not None:
            return [
                mapping
                for alternative in alternatives
                for mapping in mapping_alternatives(alternative)
            ]
        mapping = stored_mapping(value)
        return [mapping] if mapping is not None else []

    def stored_alternatives(
        value: cli_value | None,
    ) -> tuple[cli_value, ...] | None:
        if not isinstance(value, str):
            return None
        identity = stored_identity(value, known_alternatives_prefix)
        if identity is None:
            return None
        return alternative_values.get(identity)

    def store_alternatives(
        values: Sequence[cli_value],
    ) -> cli_value:
        unique: list[cli_value] = []
        for value in values:
            nested = stored_alternatives(value)
            candidates = nested if nested is not None else (value,)
            for candidate in candidates:
                if candidate not in unique:
                    unique.append(candidate)
        if len(unique) == 1:
            return unique[0]
        stored = tuple(unique)
        identity = id(stored)
        alternative_values[identity] = stored
        return f"{known_alternatives_prefix}{identity}>"

    def attribute_key(
        node: ast.AST,
        environment: Mapping[str, cli_value],
    ) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            owner_value = evaluate(node.value, environment)
            if (
                stored_object(owner_value) is not None
                or stored_class_identity(owner_value) is not None
                or stored_super(owner_value) is not None
            ):
                return f"{owner_value}.{node.attr}"
            owner_key = attribute_key(node.value, environment)
            if owner_key is not None:
                return f"{owner_key}.{node.attr}"
        return None

    def flatten_sequence(value: cli_value | None) -> tuple[str, ...] | None:
        sequence = stored_sequence(value)
        if sequence is None:
            return None
        result: tuple[str, ...] = ()
        for item in sequence:
            nested = stored_sequence(item)
            if nested is None:
                result = combine(result, (item,))
            else:
                flattened = flatten_sequence(item)
                result = combine(
                    result,
                    flattened if flattened is not None else (dynamic_argument,),
                )
        return result

    def sequence_alternatives(
        value: cli_value | None,
    ) -> list[tuple[str, ...]]:
        alternatives = stored_alternatives(value)
        if alternatives is not None:
            return [
                sequence
                for alternative in alternatives
                for sequence in sequence_alternatives(alternative)
            ]
        sequence = stored_sequence(value)
        if sequence is None:
            return []
        nested = [
            item
            for item in sequence
            if sequence_alternatives(item)
        ]
        if nested:
            return [
                candidate
                for item in nested
                for candidate in sequence_alternatives(item)
            ]
        states: list[tuple[str, ...]] = [()]
        for item in sequence:
            item_alternatives = stored_alternatives(item)
            scalar_values = (
                [
                    candidate
                    for alternative in item_alternatives
                    for candidate in scalar_alternatives(alternative)
                ]
                if item_alternatives is not None
                else scalar_alternatives(item)
            )
            if not scalar_values:
                scalar_values = [dynamic_argument]
            states = [
                combine(state, (candidate,))
                for state in states
                for candidate in scalar_values
            ]
            if len(states) > max_commands:
                mark_limited("commands")
                states = states[:max_commands]
                break
        return states

    def scalar_alternatives(value: cli_value | None) -> list[str]:
        alternatives = stored_alternatives(value)
        if alternatives is not None:
            return [
                candidate
                for alternative in alternatives
                for candidate in scalar_alternatives(alternative)
            ]
        if isinstance(value, str) and not has_stored_reference(value):
            return [value]
        return []

    def has_stored_reference(value: cli_value) -> bool:
        alternatives = stored_alternatives(value)
        if alternatives is not None:
            return any(
                has_stored_reference(alternative)
                for alternative in alternatives
            )
        return (
            stored_sequence(value) is not None
            or stored_mapping(value) is not None
            or stored_object(value) is not None
            or (
                isinstance(value, str)
                and (
                    (
                        value.startswith(known_function_prefix)
                        and value.endswith(">")
                    )
                    or stored_identity(value, known_class_prefix) is not None
                    or stored_identity(value, known_lambda_prefix) is not None
                    or stored_identity(value, known_super_prefix) is not None
                )
            )
        )

    def evaluate(
        node: ast.AST,
        environment: Mapping[str, cli_value],
    ) -> cli_value | None:
        value = _constant_string_expression(node)
        if value is not None:
            return bounded_string(value)
        if isinstance(node, ast.Lambda):
            identity = id(node)
            lambda_definitions[identity] = node
            return store_lambda_reference(identity, environment)
        if isinstance(node, ast.Name):
            return environment.get(node.id)
        if isinstance(node, ast.Attribute):
            key = attribute_key(node, environment)
            if key is not None and key in environment:
                return environment[key]
            return resolve_attribute_value(node, environment)
        if isinstance(node, ast.JoinedStr):
            states = [""]
            for component in node.values:
                if (
                    isinstance(component, ast.Constant)
                    and isinstance(component.value, str)
                ):
                    states = [
                        bounded_string(prefix + component.value)
                        for prefix in states
                    ]
                    continue
                if not isinstance(component, ast.FormattedValue):
                    mark_limited("f_string")
                    return None
                formatted = scalar_alternatives(
                    evaluate(component.value, environment)
                )
                if not formatted:
                    mark_limited("f_string")
                    return None
                format_spec = ""
                if component.format_spec is not None:
                    format_values = scalar_alternatives(
                        evaluate(component.format_spec, environment)
                    )
                    if len(format_values) != 1:
                        mark_limited("f_string")
                        return None
                    format_spec = format_values[0]
                converted: list[str] = []
                for candidate in formatted:
                    if component.conversion == ord("r"):
                        candidate = repr(candidate)
                    elif component.conversion == ord("a"):
                        candidate = ascii(candidate)
                    elif component.conversion not in {-1, ord("s")}:
                        mark_limited("f_string")
                        return None
                    try:
                        converted.append(format(candidate, format_spec))
                    except (TypeError, ValueError):
                        mark_limited("f_string")
                        return None
                states = [
                    bounded_string(prefix + suffix)
                    for prefix in states
                    for suffix in converted
                ]
                if len(states) > max_commands:
                    mark_limited("commands")
                    states = states[:max_commands]
            return store_alternatives(states)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            states: list[tuple[str, ...]] = [()]
            for element in node.elts:
                if isinstance(element, ast.Starred):
                    expanded = evaluate(element.value, environment)
                    expansions = sequence_alternatives(expanded)
                    if not expansions:
                        expansions = [(dynamic_argument,)]
                    states = [
                        combine(state, expansion)
                        for state in states
                        for expansion in expansions
                    ]
                    continue
                item = evaluate(element, environment)
                stored_item = (
                    item
                    if isinstance(item, str)
                    else (
                        store_sequence(item)
                        if isinstance(item, tuple)
                        else dynamic_argument
                    )
                )
                states = [
                    combine(state, (stored_item,))
                    for state in states
                ]
            return (
                states[0]
                if len(states) == 1
                else store_alternatives(states)
            )
        if isinstance(node, ast.Dict):
            result: dict[str, cli_value] = {}
            for key_node, value_node in zip(node.keys, node.values):
                if key_node is None:
                    expanded = stored_mapping(
                        evaluate(value_node, environment)
                    )
                    if expanded is None:
                        return None
                    result.update(expanded)
                    continue
                key = _constant_string_expression(key_node)
                if key is None:
                    return None
                result[key] = (
                    evaluate(value_node, environment) or dynamic_argument
                )
            return store_mapping(result)
        if isinstance(
            node,
            (ast.ListComp, ast.SetComp, ast.GeneratorExp),
        ):
            return evaluate_comprehension(
                node.elt,
                node.generators,
                environment,
            )
        if isinstance(node, ast.Subscript):
            container = evaluate(node.value, environment)
            key = _constant_string_expression(node.slice)
            if key is not None:
                mapping_values_for_key = [
                    mapping[key]
                    for mapping in mapping_alternatives(container)
                    if key in mapping
                ]
                if mapping_values_for_key:
                    return merge_values(mapping_values_for_key)
            sequence = stored_sequence(container)
            index = (
                node.slice.value
                if isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, int)
                else None
            )
            if (
                isinstance(sequence, tuple)
                and index is not None
                and -len(sequence) <= index < len(sequence)
            ):
                return sequence[index]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = evaluate(node.left, environment)
            right = evaluate(node.right, environment)
            if isinstance(left, str) and isinstance(right, str):
                return bounded_string(left + right)
            left_sequence = flatten_sequence(left)
            right_sequence = flatten_sequence(right)
            if left_sequence is not None and right_sequence is not None:
                return combine(left_sequence, right_sequence)
        if isinstance(node, ast.IfExp):
            alternatives = [
                value
                for value in (
                    evaluate(node.body, environment),
                    evaluate(node.orelse, environment),
                )
                if value is not None
            ]
            return store_alternatives(alternatives) if alternatives else None
        if isinstance(node, ast.NamedExpr):
            named_value = evaluate(node.value, environment)
            if isinstance(environment, dict):
                bind(environment, node.target, named_value)
            return named_value
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"list", "tuple"}
            and len(node.args) == 1
            and not node.keywords
        ):
            value = evaluate(node.args[0], environment)
            sequence = flatten_sequence(value)
            if sequence is not None:
                return sequence
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "super":
                return evaluate_super_call(node, environment)
            function_value = evaluate(node.func, environment)
            class_identities = [
                identity
                for alternative in (
                    stored_alternatives(function_value)
                    or ((function_value,) if function_value is not None else ())
                )
                if (identity := stored_class_identity(alternative)) is not None
            ]
            if class_identities:
                objects = [
                    store_object(class_identity)
                    for class_identity in class_identities
                ]
                return store_alternatives(objects)
            return invoke_callable_value(
                function_value,
                node,
                environment,
                collect_return=True,
            )
        return None

    def record(value: cli_value | None) -> None:
        for sequence in sequence_alternatives(value):
            if not any("--provider" in item for item in sequence):
                continue
            command = " ".join(sequence)
            if command in command_set:
                continue
            if len(commands) >= max_commands:
                mark_limited("commands")
                return
            command_set.add(command)
            commands.append(command)

    def assigned_names(target: ast.AST) -> tuple[str, ...]:
        if isinstance(target, ast.Name):
            return (target.id,)
        if isinstance(target, (ast.List, ast.Tuple)):
            return tuple(
                name
                for element in target.elts
                for name in assigned_names(element)
            )
        return ()

    def bind(
        environment: cli_environment,
        target: ast.AST,
        value: cli_value | None,
    ) -> None:
        if isinstance(target, ast.Name):
            if value is None:
                environment.pop(target.id, None)
                return
            if (
                target.id not in environment
                and len(environment) >= max_environment_values
            ):
                mark_limited("environment_values")
                return
            environment[target.id] = value
            return
        if isinstance(target, ast.Attribute):
            key = attribute_key(target, environment)
            if key is None:
                return
            if value is None:
                environment.pop(key, None)
                return
            if (
                key not in environment
                and len(environment) >= max_environment_values
            ):
                mark_limited("environment_values")
                return
            environment[key] = value
            return
        if isinstance(target, (ast.List, ast.Tuple)):
            sequence = stored_sequence(value)
            starred_indexes = [
                index
                for index, element in enumerate(target.elts)
                if isinstance(element, ast.Starred)
            ]
            if (
                sequence is not None
                and len(target.elts) == len(sequence)
                and not starred_indexes
            ):
                for element, item in zip(target.elts, sequence):
                    bind(environment, element, item)
            elif (
                sequence is not None
                and len(starred_indexes) == 1
                and len(sequence) >= len(target.elts) - 1
            ):
                star_index = starred_indexes[0]
                suffix_count = len(target.elts) - star_index - 1
                for element, item in zip(
                    target.elts[:star_index],
                    sequence[:star_index],
                ):
                    bind(environment, element, item)
                starred_target = target.elts[star_index]
                assert isinstance(starred_target, ast.Starred)
                middle_end = len(sequence) - suffix_count
                bind(
                    environment,
                    starred_target.value,
                    tuple(sequence[star_index:middle_end]),
                )
                if suffix_count:
                    for element, item in zip(
                        target.elts[star_index + 1:],
                        sequence[middle_end:],
                    ):
                        bind(environment, element, item)
            else:
                for name in assigned_names(target):
                    environment.pop(name, None)

    def shadowed_arguments(arguments: ast.arguments) -> tuple[str, ...]:
        result = [
            argument.arg
            for argument in (
                *arguments.posonlyargs,
                *arguments.args,
                *arguments.kwonlyargs,
            )
        ]
        if arguments.vararg is not None:
            result.append(arguments.vararg.arg)
        if arguments.kwarg is not None:
            result.append(arguments.kwarg.arg)
        return tuple(result)

    def contains_cli_provider(node: ast.AST) -> bool:
        pending = list(ast.iter_child_nodes(node))
        while pending:
            candidate = pending.pop()
            if isinstance(
                candidate,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                    ast.ClassDef,
                    ast.Lambda,
                ),
            ):
                continue
            value = _constant_string_expression(candidate)
            if (
                value is not None
                and re.search(
                    r"(?<![\w-])--provider(?:=|\s|$)",
                    value,
                )
            ):
                return True
            pending.extend(ast.iter_child_nodes(candidate))
        return False

    def contains_provider_fragments(node: ast.AST) -> bool:
        literals: list[str] = []
        pending = list(ast.iter_child_nodes(node))
        while pending:
            candidate = pending.pop()
            if isinstance(
                candidate,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                    ast.ClassDef,
                    ast.Lambda,
                ),
            ):
                continue
            value = _constant_string_expression(candidate)
            if value is not None:
                literals.append(value.strip().lower())
            pending.extend(ast.iter_child_nodes(candidate))
        return "--" in literals and "provider" in literals

    def contains_myproxy_literal(node: ast.AST) -> bool:
        pending = list(ast.iter_child_nodes(node))
        while pending:
            candidate = pending.pop()
            if isinstance(
                candidate,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                    ast.ClassDef,
                    ast.Lambda,
                ),
            ):
                continue
            value = _constant_string_expression(candidate)
            if (
                value is not None
                and re.fullmatch(
                    _MYPROXY_PROVIDER_PATTERN,
                    value.strip(),
                    re.IGNORECASE,
                )
                is not None
            ):
                return True
            pending.extend(ast.iter_child_nodes(candidate))
        return False

    for candidate in ast.walk(tree):
        if isinstance(
            candidate,
            (ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            identity = id(candidate)
            function_definitions[identity] = candidate
            function_definition_ids_by_name.setdefault(
                candidate.name,
                set(),
            ).add(identity)
        elif isinstance(candidate, ast.ClassDef):
            class_identity = id(candidate)
            class_definitions[class_identity] = candidate
            class_method_definitions[class_identity] = {
                method.name: id(method)
                for method in candidate.body
                if isinstance(
                    method,
                    (ast.FunctionDef, ast.AsyncFunctionDef),
                )
            }

    direct_provider_function_ids = {
        identity
        for identity, definition in function_definitions.items()
        if (
            contains_cli_provider(definition)
            or contains_provider_fragments(definition)
        )
    }
    function_calls: dict[int, set[str]] = {}
    relevant_function_ids = {
        identity
        for identity, definition in function_definitions.items()
        if (
            identity in direct_provider_function_ids
            or contains_myproxy_literal(definition)
        )
    }
    for identity, definition in function_definitions.items():
        called: set[str] = set()
        parameter_names = set(shadowed_arguments(definition.args))
        pending = list(ast.iter_child_nodes(definition))
        while pending:
            candidate = pending.pop()
            if isinstance(
                candidate,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                    ast.ClassDef,
                    ast.Lambda,
                ),
            ):
                continue
            if isinstance(candidate, ast.Call):
                forwarded_nodes = [
                    *candidate.args,
                    *(keyword.value for keyword in candidate.keywords),
                ]
                forwards_parameter_or_literal = any(
                    (
                        _constant_string_expression(argument)
                        is not None
                    )
                    or any(
                        isinstance(child, ast.Name)
                        and child.id in parameter_names
                        for child in ast.walk(argument)
                    )
                    for argument in forwarded_nodes
                )
                if not forwarded_nodes or forwards_parameter_or_literal:
                    if isinstance(candidate.func, ast.Name):
                        called.add(candidate.func.id)
                    elif isinstance(candidate.func, ast.Attribute):
                        called.add(candidate.func.attr)
            pending.extend(ast.iter_child_nodes(candidate))
        function_calls[identity] = called
    while True:
        relevant_function_names = {
            function_definitions[identity].name
            for identity in relevant_function_ids
        }
        expanded = {
            identity
            for identity, called in function_calls.items()
            if called & relevant_function_names
        }
        if expanded <= relevant_function_ids:
            break
        relevant_function_ids.update(expanded)

    if (
        not relevant_function_ids
        and not contains_cli_provider(tree)
        and not contains_provider_fragments(tree)
        and not any(
            (
                contains_cli_provider(candidate)
                or contains_provider_fragments(candidate)
                or contains_myproxy_literal(candidate)
            )
            for candidate in ast.walk(tree)
            if isinstance(candidate, ast.Lambda)
        )
    ):
        return "", ""

    def evaluate_keyword_mapping(
        node: ast.AST,
        environment: Mapping[str, cli_value],
    ) -> dict[str, cli_value] | None:
        mappings = mapping_alternatives(evaluate(node, environment))
        if not mappings:
            return None
        keys = {
            key
            for mapping in mappings
            for key in mapping
        }
        return {
            key: merge_values(
                [
                    mapping.get(key, dynamic_argument)
                    for mapping in mappings
                ]
            )
            for key in keys
        }

    def bound_arguments_environments(
        arguments: ast.arguments,
        environment: Mapping[str, cli_value],
        call: ast.Call | None,
        implicit_positional: Sequence[cli_value] = (),
        defaults: Mapping[str, cli_value] | None = None,
    ) -> list[cli_environment]:
        call_states: list[
            tuple[list[cli_value], dict[str, cli_value], bool]
        ] = [(list(implicit_positional), {}, False)]
        if call is not None:
            for item in call.args:
                if isinstance(item, ast.Starred):
                    expanded = evaluate(item.value, environment)
                    expansions = sequence_alternatives(expanded)
                    if not expansions:
                        expansions = [(dynamic_argument,)]
                    call_states = [
                        (
                            [*positional_values, *expansion],
                            dict(keyword_values),
                            unknown_keywords,
                        )
                        for (
                            positional_values,
                            keyword_values,
                            unknown_keywords,
                        ) in call_states
                        for expansion in expansions
                    ]
                    continue
                value = evaluate(item, environment) or dynamic_argument
                call_states = [
                    (
                        [*positional_values, value],
                        dict(keyword_values),
                        unknown_keywords,
                    )
                    for (
                        positional_values,
                        keyword_values,
                        unknown_keywords,
                    ) in call_states
                ]
            for keyword in call.keywords:
                if keyword.arg is None:
                    mappings = mapping_alternatives(
                        evaluate(keyword.value, environment)
                    )
                    if not mappings:
                        call_states = [
                            (
                                list(positional_values),
                                dict(keyword_values),
                                True,
                            )
                            for (
                                positional_values,
                                keyword_values,
                                _unknown_keywords,
                            ) in call_states
                        ]
                    else:
                        call_states = [
                            (
                                list(positional_values),
                                {**keyword_values, **mapping},
                                unknown_keywords,
                            )
                            for (
                                positional_values,
                                keyword_values,
                                unknown_keywords,
                            ) in call_states
                            for mapping in mappings
                        ]
                    continue
                value = (
                    evaluate(keyword.value, environment)
                    or dynamic_argument
                )
                call_states = [
                    (
                        list(positional_values),
                        {**keyword_values, keyword.arg: value},
                        unknown_keywords,
                    )
                    for (
                        positional_values,
                        keyword_values,
                        unknown_keywords,
                    ) in call_states
                ]
            if len(call_states) > max_environment_states:
                mark_limited("environment_states")
                call_states = call_states[:max_environment_states]

        positional_parameters = (
            *arguments.posonlyargs,
            *arguments.args,
        )
        default_values = dict(defaults or {})
        results: list[cli_environment] = []
        for positional_values, keyword_values, unknown_keywords in call_states:
            result = dict(environment)
            for name in shadowed_arguments(arguments):
                result.pop(name, None)
            for index, parameter in enumerate(positional_parameters):
                if index < len(positional_values):
                    value = positional_values[index]
                elif parameter.arg in keyword_values:
                    value = keyword_values[parameter.arg]
                else:
                    value = default_values.get(
                        parameter.arg,
                        dynamic_argument,
                    )
                bind(result, ast.Name(id=parameter.arg), value)

            extra_positional = positional_values[
                len(positional_parameters):
            ]
            if arguments.vararg is not None:
                bind(
                    result,
                    ast.Name(id=arguments.vararg.arg),
                    tuple(extra_positional),
                )

            for parameter in arguments.kwonlyargs:
                value = keyword_values.get(
                    parameter.arg,
                    default_values.get(
                        parameter.arg,
                        dynamic_argument,
                    ),
                )
                bind(result, ast.Name(id=parameter.arg), value)

            if arguments.kwarg is not None:
                consumed_keywords = {
                    parameter.arg
                    for parameter in (
                        *positional_parameters,
                        *arguments.kwonlyargs,
                    )
                }
                extra_keywords = {
                    key: value
                    for key, value in keyword_values.items()
                    if key not in consumed_keywords
                }
                bind(
                    result,
                    ast.Name(id=arguments.kwarg.arg),
                    (
                        dynamic_argument
                        if unknown_keywords
                        else store_mapping(extra_keywords)
                    ),
                )
            results.append(result)
        return deduplicate_states(results)

    def record_expression(
        node: ast.AST,
        environment: Mapping[str, cli_value],
    ) -> None:
        if not step():
            return
        record(evaluate(node, environment))
        if isinstance(node, ast.Lambda):
            defaults = evaluated_default_values(node.args, environment)
            for nested_environment in bound_arguments_environments(
                node.args,
                environment,
                None,
                defaults=defaults,
            ):
                record_expression(node.body, nested_environment)
            return
        for child in ast.iter_child_nodes(node):
            if isinstance(
                child,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            ):
                continue
            record_expression(child, environment)

    def environment_key(
        environment: Mapping[str, cli_value],
    ) -> tuple[tuple[str, str, cli_value], ...]:
        return tuple(
            (
                name,
                "sequence" if isinstance(value, tuple) else "string",
                value,
            )
            for name, value in sorted(environment.items())
        )

    def merge_values(values: Sequence[cli_value]) -> cli_value:
        unique: list[cli_value] = []
        for value in values:
            if value not in unique:
                unique.append(value)
        if len(unique) == 1:
            return unique[0]
        return store_alternatives(unique)

    def evaluated_default_values(
        arguments: ast.arguments,
        environment: Mapping[str, cli_value],
    ) -> dict[str, cli_value]:
        positional_parameters = (
            *arguments.posonlyargs,
            *arguments.args,
        )
        default_offset = len(positional_parameters) - len(arguments.defaults)
        result = {
            positional_parameters[index].arg: (
                evaluate(
                    arguments.defaults[index - default_offset],
                    environment,
                )
                or dynamic_argument
            )
            for index in range(default_offset, len(positional_parameters))
        }
        result.update(
            {
                parameter.arg: evaluate(default, environment)
                or dynamic_argument
                for parameter, default in zip(
                    arguments.kwonlyargs,
                    arguments.kw_defaults,
                )
                if default is not None
            }
        )
        return result

    def captured_lexical_environment(
        environment: Mapping[str, cli_value],
    ) -> cli_environment:
        names = environment.get(lexical_names_key)
        if not isinstance(names, tuple):
            return {}
        return {
            name: environment[name]
            for name in names
            if name in environment
        }

    def function_local_names(
        definition: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
    ) -> tuple[str, ...]:
        names = set(shadowed_arguments(definition.args))
        global_names: set[str] = set()
        nonlocal_names: set[str] = set()
        pending = (
            [definition.body]
            if isinstance(definition, ast.Lambda)
            else list(definition.body)
        )
        while pending:
            candidate = pending.pop()
            if isinstance(candidate, ast.Global):
                global_names.update(candidate.names)
                continue
            if isinstance(candidate, ast.Nonlocal):
                nonlocal_names.update(candidate.names)
                continue
            if isinstance(
                candidate,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            ):
                names.add(candidate.name)
                continue
            if isinstance(candidate, ast.Lambda):
                continue
            if (
                isinstance(candidate, ast.Name)
                and isinstance(candidate.ctx, ast.Store)
            ):
                names.add(candidate.id)
            if isinstance(candidate, (ast.Import, ast.ImportFrom)):
                names.update(
                    alias.asname or alias.name.split(".", 1)[0]
                    for alias in candidate.names
                )
            if (
                isinstance(candidate, ast.ExceptHandler)
                and isinstance(candidate.name, str)
            ):
                names.add(candidate.name)
            pending.extend(ast.iter_child_nodes(candidate))
        names.difference_update(global_names)
        names.difference_update(nonlocal_names)
        return tuple(sorted(names))

    def store_function_reference(
        definition_identity: int,
        environment: Mapping[str, cli_value],
        *,
        implicit_positional: Sequence[cli_value] = (),
        declaring_class_identity: int | None = None,
        defaults: Mapping[str, cli_value] | None = None,
        captured_environment: Mapping[str, cli_value] | None = None,
    ) -> str:
        definition = function_definitions[definition_identity]
        identity = runtime_identity()
        function_references[identity] = (
            definition_identity,
            (
                dict(captured_environment)
                if captured_environment is not None
                else captured_lexical_environment(environment)
            ),
            (
                dict(defaults)
                if defaults is not None
                else evaluated_default_values(definition.args, environment)
            ),
            tuple(implicit_positional),
            declaring_class_identity,
        )
        return f"{known_function_prefix}{identity}>"

    def store_lambda_reference(
        definition_identity: int,
        environment: Mapping[str, cli_value],
        *,
        implicit_positional: Sequence[cli_value] = (),
        declaring_class_identity: int | None = None,
        defaults: Mapping[str, cli_value] | None = None,
        captured_environment: Mapping[str, cli_value] | None = None,
    ) -> str:
        definition = lambda_definitions[definition_identity]
        identity = runtime_identity()
        lambda_references[identity] = (
            definition_identity,
            (
                dict(captured_environment)
                if captured_environment is not None
                else captured_lexical_environment(environment)
            ),
            (
                dict(defaults)
                if defaults is not None
                else evaluated_default_values(definition.args, environment)
            ),
            tuple(implicit_positional),
            declaring_class_identity,
        )
        return f"{known_lambda_prefix}{identity}>"

    def method_kind(
        definition: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> str:
        if not definition.decorator_list:
            return "instance"
        decorator_names = [
            (
                decorator.id
                if isinstance(decorator, ast.Name)
                else (
                    decorator.attr
                    if isinstance(decorator, ast.Attribute)
                    else ""
                )
            )
            for decorator in definition.decorator_list
        ]
        if decorator_names == ["staticmethod"]:
            return "static"
        if decorator_names == ["classmethod"]:
            return "class"
        return "unsupported"

    def class_alternatives(value: cli_value | None) -> list[int]:
        alternatives = stored_alternatives(value)
        if alternatives is not None:
            return [
                identity
                for alternative in alternatives
                for identity in class_alternatives(alternative)
            ]
        identity = stored_class_identity(value)
        return [identity] if identity is not None else []

    def store_class_values(
        definition_identity: int,
        environment: Mapping[str, cli_value],
    ) -> cli_value:
        definition = class_definitions[definition_identity]
        base_states: list[tuple[int, ...]] = [()]
        bases_supported = True
        for base_node in definition.bases:
            base_identities = class_alternatives(
                evaluate(base_node, environment)
            )
            if not base_identities:
                bases_supported = False
                base_states = [()]
                break
            base_states = [
                (*state, base_identity)
                for state in base_states
                for base_identity in base_identities
            ]
            if len(base_states) > max_environment_states:
                mark_limited("environment_states")
                base_states = base_states[:max_environment_states]
                break

        values: list[cli_value] = []
        for bases in base_states:
            class_identity = runtime_identity()
            class_value_definitions[class_identity] = definition_identity
            class_value_bases[class_identity] = (
                bases if bases_supported else None
            )
            methods: dict[str, tuple[cli_value, str]] = {}
            for statement in definition.body:
                if not isinstance(
                    statement,
                    (ast.FunctionDef, ast.AsyncFunctionDef),
                ):
                    continue
                function_identity = id(statement)
                methods[statement.name] = (
                    store_function_reference(
                        function_identity,
                        environment,
                        declaring_class_identity=class_identity,
                    ),
                    method_kind(statement),
                )
            class_value_methods[class_identity] = methods
            values.append(class_token(class_identity))
        return store_alternatives(values)

    def class_mro(
        class_identity: int,
        active: frozenset[int] = frozenset(),
    ) -> tuple[int, ...] | None:
        if class_identity in active:
            return None
        bases = class_value_bases.get(class_identity)
        if bases is None:
            return None
        if not bases:
            return (class_identity,)
        sequences: list[list[int]] = []
        for base_identity in bases:
            base_mro = class_mro(
                base_identity,
                active | {class_identity},
            )
            if base_mro is None:
                return None
            sequences.append(list(base_mro))
        sequences.append(list(bases))
        merged: list[int] = []
        while any(sequences):
            sequences = [sequence for sequence in sequences if sequence]
            candidate = next(
                (
                    sequence[0]
                    for sequence in sequences
                    if all(
                        sequence[0] not in other[1:]
                        for other in sequences
                    )
                ),
                None,
            )
            if candidate is None:
                return None
            merged.append(candidate)
            for sequence in sequences:
                if sequence and sequence[0] == candidate:
                    sequence.pop(0)
        return (class_identity, *merged)

    def bind_callable_value(
        value: cli_value,
        implicit_positional: Sequence[cli_value],
    ) -> cli_value:
        alternatives = stored_alternatives(value)
        if alternatives is not None:
            return store_alternatives(
                [
                    bind_callable_value(alternative, implicit_positional)
                    for alternative in alternatives
                ]
            )
        if not isinstance(value, str):
            return value
        cache_key = (
            value,
            tuple(implicit_positional),
            None,
        )
        if cache_key in bound_callable_cache:
            return bound_callable_cache[cache_key]
        function_identity = stored_identity(value, known_function_prefix)
        if function_identity in function_references:
            (
                definition_identity,
                captured_environment,
                defaults,
                existing_implicit,
                declaring_class_identity,
            ) = function_references[function_identity]
            bound = store_function_reference(
                definition_identity,
                captured_environment,
                implicit_positional=(
                    *implicit_positional,
                    *existing_implicit,
                ),
                declaring_class_identity=declaring_class_identity,
                defaults=defaults,
                captured_environment=captured_environment,
            )
            bound_callable_cache[cache_key] = bound
            return bound
        lambda_identity = stored_identity(value, known_lambda_prefix)
        if lambda_identity in lambda_references:
            (
                definition_identity,
                captured_environment,
                defaults,
                existing_implicit,
                declaring_class_identity,
            ) = lambda_references[lambda_identity]
            bound = store_lambda_reference(
                definition_identity,
                captured_environment,
                implicit_positional=(
                    *implicit_positional,
                    *existing_implicit,
                ),
                declaring_class_identity=declaring_class_identity,
                defaults=defaults,
                captured_environment=captured_environment,
            )
            bound_callable_cache[cache_key] = bound
            return bound
        return value

    def descriptor_value(
        value: cli_value,
        *,
        receiver: cli_value,
        owner_class_identity: int,
        kind: str,
    ) -> cli_value | None:
        if kind == "unsupported":
            mark_limited("dispatch")
            return None
        if kind == "static":
            return value
        if kind == "class":
            return bind_callable_value(
                value,
                (class_token(owner_class_identity),),
            )
        if stored_object(receiver) is not None:
            return bind_callable_value(value, (receiver,))
        return value

    def resolve_class_attribute(
        class_identity: int,
        attribute: str,
        receiver: cli_value,
        environment: Mapping[str, cli_value],
        *,
        start_after: int | None = None,
    ) -> cli_value | None:
        mro = class_mro(class_identity)
        if mro is None:
            mark_limited("dispatch")
            return None
        search_mro = list(mro)
        if start_after is not None:
            if start_after not in search_mro:
                mark_limited("dispatch")
                return None
            search_mro = search_mro[search_mro.index(start_after) + 1:]
        for candidate in search_mro:
            monkeypatch_key = f"{class_token(candidate)}.{attribute}"
            if monkeypatch_key in environment:
                monkeypatch = environment[monkeypatch_key]
                return descriptor_value(
                    monkeypatch,
                    receiver=receiver,
                    owner_class_identity=class_identity,
                    kind="instance",
                )
            method = class_value_methods.get(candidate, {}).get(attribute)
            if method is None:
                continue
            method_value, kind = method
            return descriptor_value(
                method_value,
                receiver=receiver,
                owner_class_identity=class_identity,
                kind=kind,
            )
        return None

    def resolve_attribute_value(
        node: ast.Attribute,
        environment: Mapping[str, cli_value],
    ) -> cli_value | None:
        owner_value = evaluate(node.value, environment)
        owner_alternatives = (
            stored_alternatives(owner_value)
            or ((owner_value,) if owner_value is not None else ())
        )
        results: list[cli_value] = []
        for owner in owner_alternatives:
            if not isinstance(owner, str):
                continue
            direct_key = f"{owner}.{node.attr}"
            if direct_key in environment:
                results.append(environment[direct_key])
                continue
            object_identity = stored_object_identity(owner)
            if object_identity is not None:
                class_identity = object_class_identities.get(object_identity)
                if class_identity is not None:
                    resolved = resolve_class_attribute(
                        class_identity,
                        node.attr,
                        owner,
                        environment,
                    )
                    if resolved is not None:
                        results.append(resolved)
                continue
            class_identity = stored_class_identity(owner)
            if class_identity is not None:
                resolved = resolve_class_attribute(
                    class_identity,
                    node.attr,
                    owner,
                    environment,
                )
                if resolved is not None:
                    results.append(resolved)
                continue
            super_value = stored_super(owner)
            if super_value is not None:
                declaring_class_identity, receiver = super_value
                receiver_class_identity = stored_class_identity(receiver)
                if receiver_class_identity is None:
                    receiver_object_identity = stored_object_identity(receiver)
                    if receiver_object_identity is not None:
                        receiver_class_identity = (
                            object_class_identities.get(
                                receiver_object_identity
                            )
                        )
                if receiver_class_identity is None:
                    mark_limited("dispatch")
                    continue
                resolved = resolve_class_attribute(
                    receiver_class_identity,
                    node.attr,
                    receiver,
                    environment,
                    start_after=declaring_class_identity,
                )
                if resolved is not None:
                    results.append(resolved)
        return merge_values(results) if results else None

    def evaluate_super_call(
        call: ast.Call,
        environment: Mapping[str, cli_value],
    ) -> cli_value | None:
        if call.keywords:
            mark_limited("dispatch")
            return None
        if not call.args:
            declaring = stored_class_identity(
                environment.get("__python_cli_declaring_class__")
            )
            receiver = environment.get("__python_cli_method_receiver__")
        elif len(call.args) == 2:
            declaring = stored_class_identity(
                evaluate(call.args[0], environment)
            )
            receiver = evaluate(call.args[1], environment)
        else:
            mark_limited("dispatch")
            return None
        if declaring is None or receiver is None:
            mark_limited("dispatch")
            return None
        return store_super(declaring, receiver)

    def callable_reference_records(
        value: cli_value | None,
    ) -> list[
        tuple[
            str,
            int,
            tuple[
                int,
                cli_environment,
                dict[str, cli_value],
                tuple[cli_value, ...],
                int | None,
            ],
        ]
    ]:
        alternatives = stored_alternatives(value)
        if alternatives is not None:
            return [
                record
                for alternative in alternatives
                for record in callable_reference_records(alternative)
            ]
        if not isinstance(value, str):
            return []
        function_identity = stored_identity(value, known_function_prefix)
        if function_identity in function_references:
            return [
                (
                    "function",
                    function_identity,
                    function_references[function_identity],
                )
            ]
        lambda_identity = stored_identity(value, known_lambda_prefix)
        if lambda_identity in lambda_references:
            return [
                (
                    "lambda",
                    lambda_identity,
                    lambda_references[lambda_identity],
                )
            ]
        return []

    def invoke_callable_value(
        value: cli_value | None,
        call: ast.Call | None,
        environment: Mapping[str, cli_value],
        *,
        collect_return: bool,
    ) -> cli_value | None:
        nonlocal active_call_depth
        returned_values: list[cli_value] = []
        observed: set[tuple[str, int]] = set()
        for kind, reference_identity, reference in (
            callable_reference_records(value)
        ):
            reference_key = (kind, reference_identity)
            if reference_key in observed:
                continue
            observed.add(reference_key)
            (
                definition_identity,
                captured_environment,
                defaults,
                implicit_positional,
                declaring_class_identity,
            ) = reference
            active_calls = (
                active_function_calls
                if kind == "function"
                else active_lambda_calls
            )
            if reference_identity in active_calls:
                continue
            if active_call_depth >= max_call_depth:
                mark_limited("call_depth")
                continue
            definition = (
                function_definitions.get(definition_identity)
                if kind == "function"
                else lambda_definitions.get(definition_identity)
            )
            if definition is None:
                mark_limited("dispatch")
                continue
            invocation_environment = dict(environment)
            invocation_environment.update(captured_environment)
            if declaring_class_identity is not None:
                invocation_environment[
                    "__python_cli_declaring_class__"
                ] = class_token(declaring_class_identity)
                if implicit_positional:
                    invocation_environment[
                        "__python_cli_method_receiver__"
                    ] = implicit_positional[0]
            bound_states = bound_arguments_environments(
                definition.args,
                invocation_environment,
                call,
                implicit_positional,
                defaults,
            )
            lexical_names = tuple(
                sorted(
                    {
                        *function_local_names(definition),
                        *captured_environment,
                    }
                )
            )
            for bound_state in bound_states:
                bound_state[lexical_names_key] = lexical_names
            active_calls.add(reference_identity)
            active_call_depth += 1
            try:
                if kind == "lambda":
                    assert isinstance(definition, ast.Lambda)
                    for bound_environment in bound_states:
                        record_expression(
                            definition.body,
                            bound_environment,
                        )
                        result = evaluate(
                            definition.body,
                            bound_environment,
                        )
                        if result is not None:
                            returned_values.append(result)
                else:
                    assert isinstance(
                        definition,
                        (ast.FunctionDef, ast.AsyncFunctionDef),
                    )
                    function_returns: list[cli_value] = []
                    process_scope(
                        definition.body,
                        bound_states,
                        return_values=function_returns,
                    )
                    returned_values.extend(function_returns)
            finally:
                active_call_depth -= 1
                active_calls.remove(reference_identity)
        if not collect_return or not returned_values:
            return None
        return merge_values(returned_values)

    def value_contains_sensitive_cli_component(
        value: cli_value | None,
    ) -> bool:
        alternatives = stored_alternatives(value)
        if alternatives is not None:
            return any(
                value_contains_sensitive_cli_component(alternative)
                for alternative in alternatives
            )
        sequence = stored_sequence(value)
        if sequence is not None:
            return any(
                value_contains_sensitive_cli_component(item)
                for item in sequence
            )
        mapping = stored_mapping(value)
        if mapping is not None:
            return any(
                value_contains_sensitive_cli_component(item)
                for item in mapping.values()
            )
        if not isinstance(value, str) or has_stored_reference(value):
            return False
        provider_option = "--" + "provider"
        endpoint_option = "--base" + "-url"
        model_option = "--" + "model"
        endpoint_probe = " ".join(
            (provider_option, "myproxy", endpoint_option, value)
        )
        model_probe = " ".join(
            (provider_option, "myproxy", model_option, value)
        )
        return (
            re.fullmatch(
                _MYPROXY_PROVIDER_PATTERN,
                value.strip(),
                re.IGNORECASE,
            )
            is not None
            or _MYPROXY_CLI_ENDPOINT_RE.search(endpoint_probe) is not None
            or _MYPROXY_CLI_MODEL_RE.search(model_probe) is not None
        )

    def deduplicate_states(
        states: Iterable[cli_environment],
    ) -> list[cli_environment]:
        result: list[cli_environment] = []
        observed: set[tuple[tuple[str, str, cli_value], ...]] = set()
        for environment in states:
            key = environment_key(environment)
            if key in observed:
                continue
            observed.add(key)
            result.append(environment)
        if len(result) <= max_environment_states:
            return result
        sensitive = [
            environment
            for environment in result
            if any(
                value_contains_sensitive_cli_component(value)
                for value in environment.values()
            )
        ]
        if len(sensitive) > max_environment_states:
            mark_limited("environment_states")
            return sensitive[:max_environment_states]
        sensitive_keys = {
            environment_key(environment)
            for environment in sensitive
        }
        remaining = [
            environment
            for environment in result
            if environment_key(environment) not in sensitive_keys
        ]
        return [
            *sensitive,
            *remaining[: max_environment_states - len(sensitive)],
        ]

    def iterable_values(
        node: ast.AST,
        environment: Mapping[str, cli_value],
    ) -> list[cli_value]:
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            result: list[cli_value] = []
            for element in node.elts:
                if isinstance(element, ast.Starred):
                    expanded = evaluate(element.value, environment)
                    flattened = flatten_sequence(expanded)
                    if flattened is not None:
                        result.extend(flattened)
                    else:
                        result.append(dynamic_argument)
                    continue
                value = evaluate(element, environment)
                result.append(
                    (
                        store_sequence(value)
                        if isinstance(value, tuple)
                        else value or dynamic_argument
                    )
                )
            if len(result) > max_arguments:
                if all(isinstance(item, str) for item in result):
                    return list(
                        bounded_sequence(
                            tuple(
                                item
                                for item in result
                                if isinstance(item, str)
                            )
                        )
                    )
                full_text = " ".join(
                    item
                    if isinstance(item, str)
                    else " ".join(item)
                    for item in result
                )
                if re.search(
                    r"(?i)--provider(?:[ \t]+|=)"
                    + _MYPROXY_PROVIDER_PATTERN
                    + r"\b",
                    full_text,
                ):
                    mark_limited("arguments")
                return result[:max_arguments]
            return result
        value = evaluate(node, environment)
        alternatives = stored_alternatives(value)
        if alternatives is not None:
            result: list[cli_value] = []
            for alternative in alternatives:
                sequence = stored_sequence(alternative)
                if sequence is not None:
                    result.extend(sequence)
                else:
                    result.append(alternative)
            return result
        sequence = stored_sequence(value)
        if sequence is not None:
            return list(sequence)
        return [dynamic_argument]

    def evaluate_comprehension(
        element: ast.AST,
        generators: Sequence[ast.comprehension],
        environment: Mapping[str, cli_value],
    ) -> tuple[str, ...]:
        states = [dict(environment)]
        for generator in generators:
            next_states: list[cli_environment] = []
            for state in states:
                for item in iterable_values(generator.iter, state):
                    iteration = dict(state)
                    bind(iteration, generator.target, item)
                    # Ignoring filters is conservative: values from a
                    # potentially skipped branch still remain scannable.
                    next_states.append(iteration)
            states = deduplicate_states(next_states)
            if not states:
                break
        result: tuple[str, ...] = ()
        for state in states:
            value = evaluate(element, state)
            result = combine(
                result,
                (
                    value
                    if isinstance(value, str)
                    else (
                        store_sequence(value)
                        if isinstance(value, tuple)
                        else dynamic_argument
                    ),
                ),
            )
        return result

    def process_scope(
        statements: Sequence[ast.stmt],
        inherited_states: Sequence[Mapping[str, cli_value]],
        shadowed: Iterable[str] = (),
        return_values: list[cli_value] | None = None,
    ) -> list[cli_environment]:
        states = []
        for inherited in inherited_states:
            environment = dict(inherited)
            for name in shadowed:
                environment.pop(name, None)
            states.append(environment)
        states = deduplicate_states(states)
        try_statement_types = (
            ast.Try,
            getattr(ast, "TryStar", ast.Try),
        )

        def apply_simple(
            statement: ast.stmt,
            environment: Mapping[str, cli_value],
        ) -> cli_environment:
            result = dict(environment)
            if isinstance(statement, ast.Assign):
                value = evaluate(statement.value, result)
                for target in statement.targets:
                    bind(result, target, value)
            elif isinstance(statement, ast.AnnAssign):
                value = (
                    evaluate(statement.value, result)
                    if statement.value is not None
                    else None
                )
                bind(result, statement.target, value)
            elif (
                isinstance(statement, ast.AugAssign)
                and isinstance(statement.target, ast.Name)
                and isinstance(statement.op, ast.Add)
            ):
                current = result.get(statement.target.id)
                addition = evaluate(statement.value, result)
                if isinstance(current, str) and isinstance(addition, str):
                    bind(
                        result,
                        statement.target,
                        bounded_string(current + addition),
                    )
                elif isinstance(current, tuple) and isinstance(addition, tuple):
                    bind(
                        result,
                        statement.target,
                        combine(current, addition),
                    )
                else:
                    bind(result, statement.target, None)
            elif (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Call)
                and isinstance(statement.value.func, ast.Attribute)
                and isinstance(statement.value.func.value, ast.Name)
            ):
                call = statement.value
                target = call.func.value
                current = result.get(target.id)
                if isinstance(current, tuple) and len(call.args) == 1:
                    addition = evaluate(call.args[0], result)
                    if call.func.attr == "append":
                        bind(
                            result,
                            target,
                            combine(
                                current,
                                (
                                    addition
                                    if isinstance(addition, str)
                                    else dynamic_argument,
                                ),
                            ),
                        )
                    elif call.func.attr == "extend":
                        bind(
                            result,
                            target,
                            combine(
                                current,
                                (
                                    addition
                                    if isinstance(addition, tuple)
                                    else (dynamic_argument,)
                                ),
                            ),
                        )
            elif isinstance(statement, ast.Delete):
                for target in statement.targets:
                    bind(result, target, None)
            record_expression(statement, result)
            return result

        def process_block(
            block: Sequence[ast.stmt],
            current_states: Sequence[cli_environment],
        ) -> list[cli_environment]:
            result_states = list(current_states)
            for statement in block:
                if not step(len(result_states)):
                    return result_states
                if isinstance(statement, ast.Return):
                    for state in result_states:
                        if statement.value is None:
                            continue
                        returned = evaluate(statement.value, state)
                        record(returned)
                        if return_values is not None and returned is not None:
                            return_values.append(returned)
                    return []
                if isinstance(
                    statement,
                    (ast.FunctionDef, ast.AsyncFunctionDef),
                ):
                    identity = id(statement)
                    function_definitions[identity] = statement
                    references: list[tuple[cli_environment, cli_value]] = []
                    for state in result_states:
                        reference = store_function_reference(
                            identity,
                            state,
                        )
                        bind(
                            state,
                            ast.Name(id=statement.name),
                            reference,
                        )
                        references.append((state, reference))
                    if identity in direct_provider_function_ids:
                        for state, reference in references:
                            invoke_callable_value(
                                reference,
                                None,
                                state,
                                collect_return=False,
                            )
                    continue
                if isinstance(statement, ast.ClassDef):
                    definition_identity = id(statement)
                    class_definitions[definition_identity] = statement
                    class_values: list[
                        tuple[cli_environment, cli_value]
                    ] = []
                    for state in result_states:
                        value = store_class_values(
                            definition_identity,
                            state,
                        )
                        bind(
                            state,
                            ast.Name(id=statement.name),
                            value,
                        )
                        class_values.append((state, value))
                    for state, value in class_values:
                        for class_identity in class_alternatives(value):
                            for method_value, kind in (
                                class_value_methods.get(
                                    class_identity,
                                    {},
                                ).values()
                            ):
                                records = callable_reference_records(
                                    method_value
                                )
                                if not records:
                                    continue
                                method_definition_identity = records[0][2][0]
                                if (
                                    method_definition_identity
                                    not in direct_provider_function_ids
                                ):
                                    continue
                                receiver = (
                                    class_token(class_identity)
                                    if kind == "class"
                                    else store_object(class_identity)
                                )
                                bound_method = descriptor_value(
                                    method_value,
                                    receiver=receiver,
                                    owner_class_identity=class_identity,
                                    kind=kind,
                                )
                                invoke_callable_value(
                                    bound_method,
                                    None,
                                    state,
                                    collect_return=False,
                                )
                    continue
                if isinstance(statement, ast.If):
                    next_states = process_block(
                        statement.body,
                        [dict(state) for state in result_states],
                    )
                    next_states.extend(
                        process_block(
                            statement.orelse,
                            [dict(state) for state in result_states],
                        )
                        if statement.orelse
                        else [dict(state) for state in result_states]
                    )
                    result_states = deduplicate_states(next_states)
                    continue
                if isinstance(statement, try_statement_types):
                    next_states: list[cli_environment] = []
                    successful = process_block(
                        statement.body,
                        [dict(state) for state in result_states],
                    )
                    if statement.orelse:
                        successful = process_block(
                            statement.orelse,
                            successful,
                        )
                    next_states.extend(successful)
                    for handler in statement.handlers:
                        next_states.extend(
                            process_block(
                                handler.body,
                                [dict(state) for state in result_states],
                            )
                        )
                    next_states = deduplicate_states(next_states)
                    if statement.finalbody:
                        next_states = process_block(
                            statement.finalbody,
                            next_states,
                        )
                    result_states = deduplicate_states(next_states)
                    continue
                if isinstance(
                    statement,
                    (ast.For, ast.AsyncFor, ast.While),
                ):
                    zero_iteration = [
                        dict(state) for state in result_states
                    ]
                    one_iteration: list[cli_environment] = []
                    if isinstance(statement, (ast.For, ast.AsyncFor)):
                        for state in result_states:
                            for item in iterable_values(
                                statement.iter,
                                state,
                            ):
                                iteration = dict(state)
                                bind(
                                    iteration,
                                    statement.target,
                                    item,
                                )
                                one_iteration.append(iteration)
                    else:
                        one_iteration = [
                            dict(state) for state in result_states
                        ]
                    one_iteration = process_block(
                        statement.body,
                        deduplicate_states(one_iteration),
                    )
                    loop_states = deduplicate_states(
                        (*zero_iteration, *one_iteration)
                    )
                    if statement.orelse:
                        loop_states = process_block(
                            statement.orelse,
                            loop_states,
                        )
                    result_states = deduplicate_states(loop_states)
                    continue
                if isinstance(statement, (ast.With, ast.AsyncWith)):
                    result_states = process_block(
                        statement.body,
                        [dict(state) for state in result_states],
                    )
                    continue
                if isinstance(statement, ast.Match):
                    next_states = [
                        dict(state) for state in result_states
                    ]
                    for case in statement.cases:
                        next_states.extend(
                            process_block(
                                case.body,
                                [dict(state) for state in result_states],
                            )
                        )
                    result_states = deduplicate_states(next_states)
                    continue
                result_states = deduplicate_states(
                    apply_simple(statement, state)
                    for state in result_states
                )
            return result_states

        return process_block(statements, states)

    try:
        process_scope(tree.body, ({},))
    except RecursionError:
        mark_limited("recursion_depth")
    return (
        "\n".join(commands),
        (
            "python_cli_analysis_limit_exceeded:"
            + analysis_limit_reason
            if analysis_limited
            else ""
        ),
    )


def _python_sensitive_call_text(path: str, text: str) -> str:
    if PurePosixPath(path).suffix.lower() != ".py":
        return ""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return ""
    calls: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        function = node.func
        is_putenv = (
            (
                isinstance(function, ast.Name)
                and function.id == "putenv"
            )
            or (
                isinstance(function, ast.Attribute)
                and function.attr == "putenv"
            )
        )
        is_setdefault = (
            isinstance(function, ast.Attribute)
            and function.attr == "setdefault"
            and (
                (
                    isinstance(function.value, ast.Name)
                    and function.value.id == "environ"
                )
                or (
                    isinstance(function.value, ast.Attribute)
                    and function.value.attr == "environ"
                )
            )
        )
        if not (is_putenv or is_setdefault):
            continue
        key = _constant_string_expression(node.args[0])
        value = _constant_string_expression(node.args[1])
        if key is None or value is None:
            continue
        function_name = "os.putenv" if is_putenv else "os.environ.setdefault"
        calls.append(
            f"{function_name}({json.dumps(key)}, {json.dumps(value)})"
        )
    return "\n".join(calls)


def _constant_string_expression(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _constant_string_expression(node.left)
        right = _constant_string_expression(node.right)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if not (
                isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                return None
            parts.append(value.value)
        return "".join(parts)
    return None


def _dockerfile_logical_text(path: str, text: str) -> str:
    if not _is_dockerfile_path(path):
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


def _is_dockerfile_path(path: str) -> bool:
    basename = PurePosixPath(path).name.lower()
    return (
        basename in {"containerfile", "dockerfile"}
        or basename.startswith(("containerfile.", "dockerfile."))
        or basename.endswith((".containerfile", ".dockerfile"))
    )


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
    input_manifest: list[dict[str, object]] = []
    input_paths: set[str] = set()

    def scan_input(
        kind: str,
        path: str,
        payload: bytes,
        *,
        allow_safe_fixtures: bool,
        identity: Mapping[str, str] | None = None,
    ) -> None:
        normalized_path = path.replace("\\", "/")
        if normalized_path in input_paths:
            raise RuntimeError(f"Duplicate secret scan input: {normalized_path}")
        input_paths.add(normalized_path)
        entry: dict[str, object] = {
            "kind": kind,
            "path": normalized_path,
        }
        if identity is None:
            entry.update(
                {
                    "size": len(payload),
                    "sha256": sha256_bytes(payload),
                }
            )
        else:
            entry.update(identity)
        input_manifest.append(entry)
        findings.extend(
            scan_payload(
                normalized_path,
                payload,
                allow_safe_fixtures=allow_safe_fixtures,
            )
        )

    tracked_paths: set[str] = set()
    tracked = _git_output(repository_root, ["ls-files", "--stage", "-z"])
    for raw_record in tracked.split(b"\0"):
        if not raw_record:
            continue
        metadata, separator, raw_path = raw_record.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 3 or fields[2] != b"0":
            raise RuntimeError("Git index contains an unsupported tracked entry")
        mode = fields[0].decode("ascii", errors="strict")
        oid = fields[1].decode("ascii", errors="strict")
        if mode not in {"100644", "100755", "120000"}:
            raise RuntimeError(f"Unsupported tracked file mode: {mode}")
        if re.fullmatch(r"[0-9a-f]{40}", oid) is None:
            raise RuntimeError("Unsupported Git object format")
        relative = raw_path.decode("utf-8", errors="strict")
        if relative in tracked_paths:
            raise RuntimeError("Git index contains a duplicate tracked path")
        tracked_paths.add(relative)
        payload = _git_output(
            repository_root,
            ["cat-file", "blob", oid],
        )
        scan_input(
            "tracked",
            relative,
            payload,
            allow_safe_fixtures=True,
            identity={"mode": mode, "oid": oid},
        )
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
            scan_input(
                "untracked",
                relative,
                os.readlink(candidate).encode("utf-8"),
                allow_safe_fixtures=True,
            )
        elif candidate.is_file():
            scan_input(
                "untracked",
                relative,
                candidate.read_bytes(),
                allow_safe_fixtures=True,
            )
    diff = _git_output(
        repository_root,
        ["diff", "--no-ext-diff", "--binary", "HEAD", "--"],
    )
    scan_input(
        "diff",
        "<git-diff>",
        _added_diff_payload(diff),
        allow_safe_fixtures=False,
    )
    for snapshot in snapshots:
        for entry, payload in snapshot.files.items():
            scan_input(
                snapshot.kind,
                f"{snapshot.kind}/{entry}",
                payload,
                allow_safe_fixtures=False,
            )
    for name, payload in sorted((generated_payloads or {}).items()):
        scan_input(
            "report",
            f"report/{name}",
            payload,
            allow_safe_fixtures=False,
        )
    input_manifest.sort(key=lambda item: str(item["path"]))
    findings.sort(
        key=lambda item: (
            str(item.get("path", "")),
            int(item.get("line", 0)),
            str(item.get("rule_id", "")),
            str(item.get("fingerprint", "")),
        )
    )
    return {
        "scanned_file_count": len(input_manifest),
        "input_manifest_sha256": sha256_bytes(
            canonical_json_text(input_manifest).encode("utf-8")
        ),
        "input_manifest": input_manifest,
        "finding_count": len(findings),
        "findings": findings,
    }


_REPORT_SCAN_JSON_NAME: Final[str] = (
    "distribution-compliance.final-projection.json"
)
_REPORT_SCAN_MARKDOWN_NAME: Final[str] = (
    "distribution-compliance.final-projection.md"
)
_REPORT_SCAN_EXCLUDED_FIELDS: Final[frozenset[str]] = frozenset(
    {"blockers", "ok", "secret_scan", "status"}
)
_DISTRIBUTION_REPORT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "archive_blockers",
        "archive_manifests",
        "archive_size_manifests",
        "artifacts",
        "blockers",
        "dependencies",
        "install_smoke",
        "licenses",
        "metadata",
        "ok",
        "repository",
        "runtime_data",
        "schema_version",
        "secret_scan",
        "source_pyproject",
        "status",
    }
)


def _distribution_report_scan_projection(
    report: Mapping[str, object],
) -> dict[str, object]:
    return {
        str(key): value
        for key, value in report.items()
        if key not in _REPORT_SCAN_EXCLUDED_FIELDS
    }


def _distribution_report_scan_payloads(
    report: Mapping[str, object],
) -> dict[str, bytes]:
    projection = _distribution_report_scan_projection(report)
    json_text = canonical_json_text(projection)
    markdown_text = (
        "# Distribution Compliance Final Projection\n\n"
        "The canonical self-reference-free report payload follows.\n\n"
        "```json\n"
        f"{json_text}"
        "```\n"
    )
    return {
        _REPORT_SCAN_JSON_NAME: json_text.encode("utf-8"),
        _REPORT_SCAN_MARKDOWN_NAME: markdown_text.encode("utf-8"),
    }


def _git_tree_oid_from_manifest(
    manifest: Sequence[Mapping[str, object]],
) -> str:
    root: dict[str, object] = {}
    for raw_entry in manifest:
        entry = _mapping(raw_entry)
        path = entry.get("path")
        mode = entry.get("mode")
        oid = entry.get("oid")
        if (
            not isinstance(path, str)
            or not path
            or path.startswith("/")
            or "\\" in path
            or path.endswith("/")
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or not isinstance(mode, str)
            or mode not in {"100644", "100755", "120000"}
            or not isinstance(oid, str)
            or re.fullmatch(r"[0-9a-f]{40}", oid) is None
        ):
            raise ValueError("Invalid tracked source manifest entry")
        parts = PurePosixPath(path).parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise ValueError("Invalid tracked source path")
        node = root
        for part in parts[:-1]:
            child = node.setdefault(part, {})
            if not isinstance(child, dict):
                raise ValueError("Tracked source path collision")
            node = child
        if parts[-1] in node:
            raise ValueError("Duplicate tracked source path")
        node[parts[-1]] = (mode, oid)

    def tree_oid(node: Mapping[str, object]) -> str:
        records: list[tuple[bytes, bytes]] = []
        for name, value in node.items():
            name_bytes = name.encode("utf-8")
            if isinstance(value, dict):
                mode = "40000"
                oid = tree_oid(value)
                sort_key = name_bytes + b"/"
            else:
                mode, oid = value
                sort_key = name_bytes + b"\0"
            record = (
                f"{mode} ".encode("ascii")
                + name_bytes
                + b"\0"
                + bytes.fromhex(oid)
            )
            records.append((sort_key, record))
        payload = b"".join(record for _, record in sorted(records))
        header = f"tree {len(payload)}\0".encode("ascii")
        return hashlib.sha1(header + payload).hexdigest()

    return tree_oid(root)


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
        report["secret_scan"] = scan_git_and_artifacts(
            repository_root,
            (wheel, sdist),
            _distribution_report_scan_payloads(report),
        )
        return _with_derived_verdict(report)
    finally:
        if owned_temporary is not None:
            owned_temporary.cleanup()


def distribution_report_blockers(
    report: Mapping[str, object],
    *,
    require_derived_verdict: bool = True,
) -> list[dict[str, object]]:
    """Derive the verdict from raw evidence, never caller-provided booleans."""

    blockers: list[dict[str, object]] = []
    unexpected_fields = sorted(
        str(field)
        for field in report
        if field not in _DISTRIBUTION_REPORT_FIELDS
    )
    if unexpected_fields:
        blockers.append(
            {
                "code": "unexpected_distribution_compliance_fields",
                "fields": unexpected_fields,
            }
        )
    if (
        report.get("schema_version")
        != DISTRIBUTION_COMPLIANCE_SCHEMA_VERSION
    ):
        blockers.append(
            {
                "code": "unsupported_distribution_compliance_schema",
                "expected": DISTRIBUTION_COMPLIANCE_SCHEMA_VERSION,
                "observed": report.get("schema_version"),
            }
        )
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
    if set(artifacts) != {"wheel", "sdist"}:
        blockers.append({"code": "invalid_artifact_evidence"})
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
    trusted_secret_scan = _trusted_secret_scan_evidence(report)
    if trusted_secret_scan is None:
        blockers.append({"code": "unverified_secret_scan_provenance"})
    elif secret_scan != trusted_secret_scan:
        blockers.append({"code": "secret_scan_provenance_mismatch"})
    findings = secret_scan.get("findings")
    finding_count = secret_scan.get("finding_count")
    scanned_file_count = secret_scan.get("scanned_file_count")
    input_manifest_value = secret_scan.get("input_manifest")
    input_manifest_sha256 = secret_scan.get("input_manifest_sha256")
    valid_manifest = (
        isinstance(input_manifest_value, list)
        and set(secret_scan)
        == {
            "scanned_file_count",
            "input_manifest_sha256",
            "input_manifest",
            "finding_count",
            "findings",
        }
    )
    manifest_by_path: dict[str, Mapping[str, object]] = {}
    if valid_manifest:
        for raw_entry in input_manifest_value:
            entry = _mapping(raw_entry)
            kind = entry.get("kind")
            path = entry.get("path")
            invalid_common = (
                kind
                not in {
                    "tracked",
                    "untracked",
                    "diff",
                    "wheel",
                    "sdist",
                    "report",
                }
                or not isinstance(path, str)
                or not path
                or "\\" in path
                or path.startswith("./")
                or path.startswith("/")
                or path.endswith("/")
                or any(
                    part in {"", ".", ".."}
                    for part in path.split("/")
                )
                or path in manifest_by_path
            )
            if kind == "tracked":
                invalid_identity = (
                    set(entry) != {"kind", "path", "mode", "oid"}
                    or entry.get("mode") not in {"100644", "100755", "120000"}
                    or not isinstance(entry.get("oid"), str)
                    or re.fullmatch(
                        r"[0-9a-f]{40}",
                        str(entry.get("oid")),
                    )
                    is None
                )
            else:
                size = entry.get("size")
                digest = entry.get("sha256")
                invalid_identity = (
                    set(entry) != {"kind", "path", "size", "sha256"}
                    or type(size) is not int
                    or size < 0
                    or not isinstance(digest, str)
                    or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                )
            if invalid_common or invalid_identity:
                valid_manifest = False
                break
            manifest_by_path[path] = entry
    if valid_manifest:
        expected_manifest_digest = sha256_bytes(
            canonical_json_text(input_manifest_value).encode("utf-8")
        )
        diff_paths = {
            path
            for path, entry in manifest_by_path.items()
            if entry.get("kind") == "diff"
        }
        valid_manifest = (
            input_manifest_sha256 == expected_manifest_digest
            and scanned_file_count == len(input_manifest_value)
            and diff_paths == {"<git-diff>"}
        )
        diff_entry = manifest_by_path.get("<git-diff>", {})
        valid_manifest = valid_manifest and (
            diff_entry.get("kind") == "diff"
            and diff_entry.get("size") == 0
            and diff_entry.get("sha256") == sha256_bytes(b"")
        )
    if valid_manifest:
        tracked_manifest = [
            entry
            for entry in input_manifest_value
            if _mapping(entry).get("kind") == "tracked"
        ]
        untracked_paths = {
            path
            for path, entry in manifest_by_path.items()
            if entry.get("kind") == "untracked"
        }
        valid_manifest = bool(tracked_manifest) and not untracked_paths
    if valid_manifest:
        try:
            tracked_tree = _git_tree_oid_from_manifest(
                tracked_manifest
            )
        except ValueError:
            valid_manifest = False
        else:
            valid_manifest = all(
                state.get("tree") == tracked_tree
                for state in repository_states.values()
            )
    if valid_manifest:
        expected_report_payloads = _distribution_report_scan_payloads(report)
        projection_findings = [
            finding
            for name, payload in expected_report_payloads.items()
            for finding in scan_payload(
                f"report/{name}",
                payload,
                allow_safe_fixtures=False,
            )
        ]
        if projection_findings:
            blockers.append(
                {
                    "code": "secret_or_private_config_detected",
                    "finding_count": len(projection_findings),
                    "source": "report_projection",
                }
            )
        expected_report_paths = {
            f"report/{name}" for name in expected_report_payloads
        }
        observed_report_paths = {
            path
            for path, entry in manifest_by_path.items()
            if entry.get("kind") == "report"
        }
        valid_manifest = observed_report_paths == expected_report_paths
        for name, payload in expected_report_payloads.items():
            entry = manifest_by_path.get(f"report/{name}", {})
            if (
                entry.get("kind") != "report"
                or entry.get("size") != len(payload)
                or entry.get("sha256") != sha256_bytes(payload)
            ):
                valid_manifest = False
                break
    if valid_manifest:
        for kind in ("wheel", "sdist"):
            artifact = _mapping(_mapping(report.get("artifacts")).get(kind))
            file_manifest = _mapping(artifact.get("file_manifest"))
            file_sizes = _mapping(artifact.get("file_sizes"))
            expected_paths = {f"{kind}/{entry}" for entry in file_manifest}
            observed_paths = {
                path
                for path, entry in manifest_by_path.items()
                if entry.get("kind") == kind
            }
            if observed_paths != expected_paths:
                valid_manifest = False
                break
            for archive_entry, digest in file_manifest.items():
                entry = manifest_by_path.get(f"{kind}/{archive_entry}", {})
                if (
                    entry.get("kind") != kind
                    or entry.get("sha256") != digest
                    or entry.get("size") != file_sizes.get(archive_entry)
                ):
                    valid_manifest = False
                    break
            if not valid_manifest:
                break
    valid_findings = isinstance(findings, list)
    if valid_findings:
        for raw_finding in findings:
            finding = _mapping(raw_finding)
            if (
                set(finding) != {"path", "line", "rule_id", "fingerprint"}
                or not isinstance(finding.get("path"), str)
                or not finding.get("path")
                or finding.get("path") not in manifest_by_path
                or type(finding.get("line")) is not int
                or int(finding.get("line", -1)) < 0
                or not isinstance(finding.get("rule_id"), str)
                or not finding.get("rule_id")
                or not isinstance(finding.get("fingerprint"), str)
                or re.fullmatch(
                    r"[0-9a-f]{64}",
                    str(finding.get("fingerprint")),
                )
                is None
            ):
                valid_findings = False
                break
    valid_findings = (
        valid_findings
        and type(finding_count) is int
        and finding_count == len(findings)
    )
    if isinstance(findings, list) and findings:
        blockers.append(
            {
                "code": "secret_or_private_config_detected",
                "finding_count": len(findings),
            }
        )
    if (
        not valid_findings
        or type(scanned_file_count) is not int
        or scanned_file_count <= 0
        or not valid_manifest
    ):
        blockers.append({"code": "invalid_secret_scan_evidence"})
    derived_blockers = _deduplicate_blockers(blockers)
    derived_fields = {
        field
        for field in ("blockers", "ok", "status")
        if field in report
    }
    if require_derived_verdict:
        expected_status = (
            "blocked" if derived_blockers else "passed"
        )
        if (
            derived_fields != {"blockers", "ok", "status"}
            or report.get("blockers") != derived_blockers
            or report.get("ok") is not (not derived_blockers)
            or report.get("status") != expected_status
        ):
            derived_blockers.append(
                {"code": "invalid_distribution_compliance_verdict"}
            )
    return _deduplicate_blockers(derived_blockers)


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
    for existing in output_dir.iterdir():
        if existing.is_symlink() or existing.is_file():
            existing.unlink()
        elif existing.is_dir():
            shutil.rmtree(existing)
    public_report = _external_distribution_report(report)
    (output_dir / "distribution-compliance.json").write_text(
        canonical_json_text(public_report),
        encoding="utf-8",
    )
    (output_dir / "distribution-compliance.md").write_text(
        render_distribution_markdown(public_report),
        encoding="utf-8",
    )
    public_secret_scan = _mapping(public_report.get("secret_scan"))
    (output_dir / "secret-scan.json").write_text(
        canonical_json_text(public_secret_scan),
        encoding="utf-8",
    )
    if public_report is not report:
        return
    artifacts = _mapping(public_report.get("artifacts"))
    for kind in ("wheel", "sdist"):
        entries = _string_list(_mapping(artifacts.get(kind)).get("entries"))
        (output_dir / f"{kind}.entries.txt").write_text(
            "".join(f"{entry}\n" for entry in entries),
            encoding="utf-8",
        )
    metadata_text = str(_mapping(public_report.get("metadata")).get("raw", ""))
    (output_dir / "installed.METADATA").write_text(
        metadata_text,
        encoding="utf-8",
    )
    (output_dir / "dependency-notices.json").write_text(
        canonical_json_text(_mapping(public_report.get("dependencies"))),
        encoding="utf-8",
    )
    for name, payload in _distribution_report_scan_payloads(
        public_report
    ).items():
        (output_dir / name).write_bytes(payload)


def _external_distribution_report(
    report: Mapping[str, object],
) -> Mapping[str, object]:
    """Return a report safe to write, upload, or print."""

    payload = canonical_json_text(report).encode("utf-8")
    direct_findings = scan_payload(
        "distribution-compliance.json",
        payload,
        allow_safe_fixtures=False,
    )
    secret_scan = _mapping(report.get("secret_scan"))
    reported_findings = secret_scan.get("findings")
    reported_count = secret_scan.get("finding_count")
    has_reported_findings = (
        isinstance(reported_findings, list) and bool(reported_findings)
    ) or (type(reported_count) is int and reported_count > 0)
    if not direct_findings and not has_reported_findings:
        return report

    blocker_codes = [
        str(_mapping(item).get("code", ""))
        for item in _sequence(report.get("blockers"))
    ]
    safe_codes = [
        code
        for code in blocker_codes
        if re.fullmatch(r"[a-z0-9_]+", code)
    ]
    safe_codes.append("secret_or_private_config_detected")
    redacted_blockers = [
        {"code": code}
        for code in dict.fromkeys(safe_codes)
    ]
    finding_count = max(
        len(direct_findings),
        (
            reported_count
            if type(reported_count) is int and reported_count >= 0
            else 0
        ),
        len(reported_findings) if isinstance(reported_findings, list) else 0,
    )
    return {
        "schema_version": DISTRIBUTION_COMPLIANCE_SCHEMA_VERSION,
        "status": "blocked",
        "ok": False,
        "evidence_redacted": True,
        "blockers": redacted_blockers,
        "secret_scan": {
            "finding_count": finding_count,
            "findings_redacted": True,
        },
    }


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
    print(canonical_json_text(_external_distribution_report(report)), end="")
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
    snapshots = _trusted_artifact_snapshots(report)
    if snapshots is None:
        return None
    trusted: dict[str, dict[str, object]] = {}
    for snapshot in snapshots:
        evidence = _artifact_evidence(snapshot)
        evidence["archive_blockers"] = [
            dict(item) for item in snapshot.blockers
        ]
        trusted[snapshot.kind] = evidence
    return trusted


def _trusted_artifact_snapshots(
    report: Mapping[str, object],
) -> tuple[ArchiveSnapshot, ArchiveSnapshot] | None:
    artifacts = _mapping(report.get("artifacts"))
    if set(artifacts) != {"wheel", "sdist"}:
        return None
    snapshots: list[ArchiveSnapshot] = []
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
        snapshots.append(snapshot)
    return snapshots[0], snapshots[1]


def _trusted_secret_scan_evidence(
    report: Mapping[str, object],
) -> dict[str, object] | None:
    repository = _mapping(report.get("repository"))
    before = _mapping(repository.get("before"))
    after = _mapping(repository.get("after"))
    root_value = before.get("repository_root")
    if not isinstance(root_value, str) or before != after:
        return None
    repository_root = Path(root_value)
    snapshots = _trusted_artifact_snapshots(report)
    if snapshots is None:
        return None
    try:
        current = repository_state_evidence(
            repository_root,
            repository_root,
        )
        if current != before:
            return None
        return scan_git_and_artifacts(
            repository_root,
            snapshots,
            _distribution_report_scan_payloads(report),
        )
    except (
        OSError,
        RuntimeError,
        UnicodeError,
        ValueError,
        tarfile.TarError,
        zipfile.BadZipFile,
    ):
        return None


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
    existing_entries = sorted(path.name for path in dist_dir.iterdir())
    if existing_entries:
        raise RuntimeError(
            "distribution output directory must be empty before build"
        )
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
    wheel_paths = sorted(dist_dir.glob("*.whl"))
    sdist_paths = sorted(dist_dir.glob("*.tar.gz"))
    output_entries = sorted(dist_dir.iterdir())
    if (
        len(wheel_paths) != 1
        or len(sdist_paths) != 1
        or len(output_entries) != 2
        or any(not path.is_file() or path.is_symlink() for path in output_entries)
    ):
        raise RuntimeError(
            "distribution build must produce exactly one wheel and one sdist"
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
    blockers = distribution_report_blockers(
        derived,
        require_derived_verdict=False,
    )
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
