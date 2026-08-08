"""Fail-closed distribution, licensing, and private-config verification."""

from __future__ import annotations

import argparse
import ast
import base64
import configparser
import csv
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import unicodedata
import venv
import zipfile
import zlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field as dataclass_field
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


DISTRIBUTION_COMPLIANCE_SCHEMA_VERSION: Final[int] = 7
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
MAX_ARCHIVE_STREAM_BYTES: Final[int] = 512 * 1024 * 1024
MAX_ARCHIVE_METADATA_BYTES: Final[int] = 8 * 1024 * 1024
MAX_ARCHIVE_HEADER_FIELD_BYTES: Final[int] = 1024 * 1024
MAX_SCAN_FILE_BYTES: Final[int] = 64 * 1024 * 1024
MAX_SCAN_FINDINGS: Final[int] = 4096
MAX_CONFIGURATION_BYTES: Final[int] = 8 * 1024 * 1024
MAX_CONFIGURATION_BINDINGS: Final[int] = 512
MAX_CONFIGURATION_SNAPSHOTS: Final[int] = 1024
MAX_CONFIGURATION_EXPANDED_CHARACTERS: Final[int] = 256 * 1024
MAX_CONFIGURATION_EXPANSION_SUBSTITUTIONS: Final[int] = 8192
MAX_CONFIGURATION_EXPANSION_WORK: Final[int] = 4 * MAX_CONFIGURATION_BYTES
MAX_CONFIGURATION_STORED_CHARACTERS: Final[int] = MAX_CONFIGURATION_BYTES
MIN_PYTHON_SENSITIVE_ANALYSIS_STEPS: Final[int] = 100_000
MAX_PYTHON_SENSITIVE_ANALYSIS_STEPS: Final[int] = 1_000_000
PYTHON_SENSITIVE_ANALYSIS_STEPS_PER_NODE: Final[int] = 16
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
_ZIP_LOCAL_HEADER: Final[struct.Struct] = struct.Struct("<4s5H3I2H")
_ZIP_LOCAL_HEADER_SIGNATURE: Final[bytes] = b"PK\x03\x04"
_ZIP_CENTRAL_HEADER: Final[struct.Struct] = struct.Struct("<4s6H3I5H2I")
_ZIP_CENTRAL_HEADER_SIGNATURE: Final[bytes] = b"PK\x01\x02"
_ZIP_END_RECORD: Final[struct.Struct] = struct.Struct("<4s4H2IH")
_ZIP_END_RECORD_SIGNATURE: Final[bytes] = b"PK\x05\x06"
_ZIP_DATA_DESCRIPTOR: Final[struct.Struct] = struct.Struct("<III")
_ZIP_DATA_DESCRIPTOR_SIGNATURE: Final[bytes] = b"PK\x07\x08"
_ZIP_MAX_COMMENT_BYTES: Final[int] = (1 << 16) - 1
_GZIP_FIXED_HEADER: Final[struct.Struct] = struct.Struct("<2sBBIBB")
_GZIP_TRAILER: Final[struct.Struct] = struct.Struct("<II")
_GZIP_SIGNATURE: Final[bytes] = b"\x1f\x8b"
_TAR_BLOCK_BYTES: Final[int] = 512
_TAR_ZERO_BLOCK: Final[bytes] = b"\0" * _TAR_BLOCK_BYTES

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
    Mapping[str, Mapping[str, Mapping[str, int]]]
] = {
    "tests/test_llm_interpreter.py": {
        "api_key": {
            "6d7cff6a74f5a16be41aaf6ccc8d51b62357cf1115f4149e6d116b537f96b302": 2
        },
        "api_key_assignment": {
            "b8cbb6abbd7c6ba09041bb128a6907bb20f5464febde6f4d54159a3a1fa8b5a5": 1
        },
    },
    "tests/test_micromachine_pre_live_provenance.py": {
        "bearer_token": {
            "2a452e8451a18651177c8cfeff71b5c6d1e8fd1d1faab95968b77e83cd7efd09": 2
        }
    },
    "tests/test_web_gui.py": {
        "api_key": {
            "3ecb7ee0b6df1921344b76307f224695e417e4a63f638471b2428b6f7c54c355": 2,
            "76bb8593aa73556c30f24f5e7f47d401383e5815b4117c4b0f0430398d93f544": 1,
        }
    },
}
_TEXT_SCAN_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        ".c",
        ".cfg",
        ".cmake",
        ".conf",
        ".cpp",
        ".css",
        ".csv",
        ".diff",
        ".h",
        ".hpp",
        ".html",
        ".ini",
        ".js",
        ".json",
        ".md",
        ".patch",
        ".py",
        ".rst",
        ".sh",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
)
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
    metadata: Mapping[str, bytes] = dataclass_field(default_factory=dict)
    prescanned_inputs: tuple[Mapping[str, object], ...] = ()
    prescanned_findings: tuple[Mapping[str, object], ...] = ()


@dataclass(frozen=True)
class _RawZipEntry:
    """Central-directory values needed to validate one local ZIP record."""

    index: int
    name: str
    raw_name: bytes
    version_needed: int
    flag_bits: int
    compression: int
    modified_time: int
    modified_date: int
    crc32: int
    compressed_size: int
    uncompressed_size: int
    local_offset: int
    central_extra: bytes


class _ArchiveMetadata(dict[str, bytes]):
    """Metadata map with constant-time bounded-size accounting."""

    def __init__(self) -> None:
        super().__init__()
        self.total_bytes = 0
        self.prescanned_inputs: list[dict[str, object]] = []
        self.prescanned_findings: list[dict[str, object]] = []
        self._prescanned_paths: set[str] = set()

    def prescan(
        self,
        *,
        kind: str,
        key: str,
        payload: bytes | memoryview,
    ) -> None:
        scan_path = f"{kind}/{key}"
        if scan_path in self._prescanned_paths:
            raise RuntimeError("Duplicate prescanned archive evidence")
        self._prescanned_paths.add(scan_path)
        self.prescanned_inputs.append(
            {
                "path": scan_path,
                "size": len(payload),
                "sha256": sha256_bytes(payload),
            }
        )
        self.prescanned_findings.extend(
            scan_payload(
                scan_path,
                bytes(payload),
                allow_safe_fixtures=False,
            )
        )


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


def _record_archive_metadata(
    metadata: dict[str, bytes],
    blockers: list[dict[str, object]],
    *,
    key: str,
    payload: bytes | memoryview,
    kind: str,
    entry: str | None = None,
) -> bool:
    if isinstance(metadata, _ArchiveMetadata):
        current_size = metadata.total_bytes - len(metadata.get(key, b""))
    else:
        current_size = sum(len(value) for value in metadata.values())
    projected_size = current_size + len(payload)
    if (
        len(payload) > MAX_ARCHIVE_METADATA_BYTES
        or projected_size > MAX_ARCHIVE_METADATA_BYTES
    ):
        blocker: dict[str, object] = {
            "code": "archive_metadata_limit_exceeded",
            "kind": kind,
            "observed": projected_size,
            "limit": MAX_ARCHIVE_METADATA_BYTES,
        }
        if entry is not None:
            blocker["entry"] = entry
        blockers.append(blocker)
        if isinstance(metadata, _ArchiveMetadata):
            metadata.prescan(kind=kind, key=key, payload=payload)
        return False
    metadata[key] = bytes(payload)
    if isinstance(metadata, _ArchiveMetadata):
        metadata.total_bytes = projected_size
    return True


def _record_unexplained_archive_bytes(
    metadata: dict[str, bytes],
    blockers: list[dict[str, object]],
    *,
    key: str,
    payload: bytes,
    kind: str,
    location: str,
) -> None:
    _record_archive_metadata(
        metadata,
        blockers,
        key=key,
        payload=payload,
        kind=kind,
    )
    blockers.append(
        {
            "code": "unexpected_archive_bytes",
            "kind": kind,
            "location": location,
            "observed": len(payload),
        }
    )


def _fixed_field_projection(payload: bytes, widths: Sequence[int]) -> bytes:
    fields: list[bytes] = []
    cursor = 0
    for width in widths:
        fields.append(payload[cursor : cursor + width])
        cursor += width
    if cursor != len(payload):
        raise ValueError("fixed-field widths do not cover payload")
    return b"\n".join(fields)


def _decode_zip_filename(raw_name: bytes, flag_bits: int) -> str:
    encoding = "utf-8" if flag_bits & 0x800 else "cp437"
    return raw_name.decode(encoding)


def _validate_raw_zip_payload(
    compressed_payload: memoryview,
    entry: _RawZipEntry,
    metadata: dict[str, bytes],
    blockers: list[dict[str, object]],
) -> None:
    """Validate exact ZIP stream consumption without trusting ``zipfile``."""

    directory_entry = entry.name.endswith("/")
    if directory_entry and (
        entry.compressed_size != 0 or entry.uncompressed_size != 0
    ):
        blockers.append(
            {
                "code": "archive_directory_payload",
                "kind": "wheel",
                "entry": entry.name,
                "compressed_size": entry.compressed_size,
                "uncompressed_size": entry.uncompressed_size,
            }
        )
    if entry.compression not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
        _record_unexplained_archive_bytes(
            metadata,
            blockers,
            key=(
                "__archive_metadata__/zip/unsupported-payload/"
                f"{entry.index:04d}"
            ),
            payload=bytes(compressed_payload),
            kind="wheel",
            location="unsupported_zip_compression",
        )
        blockers.append(
            {
                "code": "unsupported_zip_compression",
                "kind": "wheel",
                "entry": entry.name,
                "compression": entry.compression,
            }
        )
        return

    if entry.compression == zipfile.ZIP_STORED:
        if entry.compressed_size != entry.uncompressed_size:
            blockers.append(
                {
                    "code": "invalid_zip_compressed_payload",
                    "kind": "wheel",
                    "entry": entry.name,
                    "reason": "stored_size_mismatch",
                }
            )
            if entry.compressed_size > entry.uncompressed_size:
                _record_unexplained_archive_bytes(
                    metadata,
                    blockers,
                    key=(
                        "__archive_metadata__/zip/compression-slack/"
                        f"{entry.index:04d}"
                    ),
                    payload=bytes(
                        compressed_payload[entry.uncompressed_size :]
                    ),
                    kind="wheel",
                    location="zip_compression_stream_slack",
                )
        observed_payload = compressed_payload[: entry.uncompressed_size]
        observed_size = len(observed_payload)
        observed_crc = zlib.crc32(observed_payload) & 0xFFFFFFFF
        if directory_entry and observed_payload:
            _record_archive_metadata(
                metadata,
                blockers,
                key=(
                    "__archive_metadata__/zip/directory-payload/"
                    f"{entry.index:04d}"
                ),
                payload=bytes(observed_payload),
                kind="wheel",
                entry=entry.name,
            )
    else:
        decompressor = zlib.decompressobj(wbits=-zlib.MAX_WBITS)
        cursor = 0
        observed_size = 0
        observed_crc = 0
        directory_payload = bytearray()
        output_limit = (
            MAX_ARCHIVE_METADATA_BYTES
            if directory_entry
            else entry.uncompressed_size + 1
        )
        try:
            while cursor < len(compressed_payload) and not decompressor.eof:
                chunk = compressed_payload[
                    cursor : min(cursor + 64 * 1024, len(compressed_payload))
                ]
                remaining = max(1, output_limit - observed_size + 1)
                uncompressed = decompressor.decompress(chunk, remaining)
                consumed = (
                    len(chunk)
                    - len(decompressor.unconsumed_tail)
                    - len(decompressor.unused_data)
                )
                if consumed <= 0 and not uncompressed:
                    raise ValueError("invalid ZIP deflate stream")
                cursor += consumed
                observed_size += len(uncompressed)
                observed_crc = zlib.crc32(uncompressed, observed_crc)
                if directory_entry:
                    directory_payload.extend(uncompressed)
                if observed_size > output_limit:
                    raise OverflowError(observed_size)
        except (OverflowError, ValueError, zlib.error):
            blockers.append(
                {
                    "code": "invalid_zip_compressed_payload",
                    "kind": "wheel",
                    "entry": entry.name,
                    "reason": "invalid_deflate_stream",
                }
            )
            if directory_payload:
                _record_archive_metadata(
                    metadata,
                    blockers,
                    key=(
                        "__archive_metadata__/zip/directory-payload/"
                        f"{entry.index:04d}"
                    ),
                    payload=bytes(directory_payload),
                    kind="wheel",
                    entry=entry.name,
                )
            return
        observed_crc &= 0xFFFFFFFF
        if not decompressor.eof:
            blockers.append(
                {
                    "code": "invalid_zip_compressed_payload",
                    "kind": "wheel",
                    "entry": entry.name,
                    "reason": "truncated_deflate_stream",
                }
            )
        if cursor < len(compressed_payload):
            _record_unexplained_archive_bytes(
                metadata,
                blockers,
                key=(
                    "__archive_metadata__/zip/compression-slack/"
                    f"{entry.index:04d}"
                ),
                payload=bytes(compressed_payload[cursor:]),
                kind="wheel",
                location="zip_compression_stream_slack",
            )
            blockers.append(
                {
                    "code": "invalid_zip_compressed_payload",
                    "kind": "wheel",
                    "entry": entry.name,
                    "reason": "deflate_stream_slack",
                }
            )
        if directory_entry and directory_payload:
            _record_archive_metadata(
                metadata,
                blockers,
                key=(
                    "__archive_metadata__/zip/directory-payload/"
                    f"{entry.index:04d}"
                ),
                payload=bytes(directory_payload),
                kind="wheel",
                entry=entry.name,
            )

    if (
        observed_size != entry.uncompressed_size
        or observed_crc != entry.crc32
    ):
        blockers.append(
            {
                "code": "invalid_zip_compressed_payload",
                "kind": "wheel",
                "entry": entry.name,
                "reason": "size_or_crc_mismatch",
            }
        )


def _raw_zip_metadata(
    path: Path,
) -> tuple[dict[str, bytes], list[dict[str, object]]]:
    """Validate and retain every non-payload byte in a bounded ZIP graph."""

    metadata: dict[str, bytes] = _ArchiveMetadata()
    blockers: list[dict[str, object]] = []
    archive_size = path.stat().st_size
    if archive_size > MAX_ARCHIVE_STREAM_BYTES:
        return metadata, [
            {
                "code": "archive_stream_limit_exceeded",
                "kind": "wheel",
                "observed": archive_size,
                "limit": MAX_ARCHIVE_STREAM_BYTES,
            }
        ]
    with path.open("rb") as handle:
        payload = handle.read(MAX_ARCHIVE_STREAM_BYTES + 1)
    if len(payload) > MAX_ARCHIVE_STREAM_BYTES:
        return metadata, [
            {
                "code": "archive_stream_limit_exceeded",
                "kind": "wheel",
                "observed": len(payload),
                "limit": MAX_ARCHIVE_STREAM_BYTES,
            }
        ]

    search_start = max(
        0,
        len(payload) - _ZIP_END_RECORD.size - _ZIP_MAX_COMMENT_BYTES,
    )
    candidates: list[tuple[int, tuple[object, ...]]] = []
    cursor = search_start
    while True:
        offset = payload.find(_ZIP_END_RECORD_SIGNATURE, cursor)
        if offset < 0:
            break
        fixed_end = offset + _ZIP_END_RECORD.size
        if fixed_end <= len(payload):
            fields = _ZIP_END_RECORD.unpack(payload[offset:fixed_end])
            comment_length = int(fields[7])
            if fixed_end + comment_length == len(payload):
                candidates.append((offset, fields))
        cursor = offset + 1
    if len(candidates) != 1:
        _record_unexplained_archive_bytes(
            metadata,
            blockers,
            key="__archive_metadata__/zip/unparsed",
            payload=payload,
            kind="wheel",
            location="zip_record_graph",
        )
        blockers.append(
            {
                "code": "invalid_zip_end_record",
                "kind": "wheel",
                "observed": len(candidates),
            }
        )
        return metadata, blockers

    end_offset, end_fields = candidates[0]
    end_record = payload[end_offset:]
    _record_archive_metadata(
        metadata,
        blockers,
        key="__archive_metadata__/zip/end-record",
        payload=end_record,
        kind="wheel",
    )
    _record_archive_metadata(
        metadata,
        blockers,
        key="__archive_metadata__/zip/end-record-fields",
        payload=_fixed_field_projection(
            end_record[: _ZIP_END_RECORD.size],
            (4, 2, 2, 2, 2, 4, 4, 2),
        ),
        kind="wheel",
    )
    (
        _,
        disk_number,
        central_disk,
        disk_entries,
        total_entries,
        central_size,
        central_offset,
        comment_length,
    ) = end_fields
    disk_number = int(disk_number)
    central_disk = int(central_disk)
    disk_entries = int(disk_entries)
    total_entries = int(total_entries)
    central_size = int(central_size)
    central_offset = int(central_offset)
    comment_length = int(comment_length)
    if comment_length:
        blockers.append(
            {
                "code": "unexpected_archive_metadata",
                "kind": "wheel",
                "metadata": "global_comment",
            }
        )
    if (
        disk_number
        or central_disk
        or disk_entries != total_entries
        or total_entries == 0xFFFF
        or central_size == 0xFFFFFFFF
        or central_offset == 0xFFFFFFFF
    ):
        blockers.append(
            {
                "code": "unsupported_zip_structure",
                "kind": "wheel",
            }
        )
        return metadata, blockers
    if total_entries > MAX_ARCHIVE_ENTRIES:
        blockers.append(
            {
                "code": "archive_entry_limit_exceeded",
                "kind": "wheel",
                "observed": total_entries,
                "limit": MAX_ARCHIVE_ENTRIES,
            }
        )
        return metadata, blockers
    if central_size > MAX_ARCHIVE_METADATA_BYTES:
        _record_archive_metadata(
            metadata,
            blockers,
            key="__archive_metadata__/zip/central-directory",
            payload=memoryview(payload)[
                central_offset : central_offset + central_size
            ],
            kind="wheel",
        )
    central_end = central_offset + central_size
    if central_end != end_offset or central_offset > len(payload):
        if central_end < end_offset and central_offset <= central_end:
            _record_unexplained_archive_bytes(
                metadata,
                blockers,
                key="__archive_metadata__/zip/central-gap",
                payload=payload[central_end:end_offset],
                kind="wheel",
                location="between_central_directory_and_end_record",
            )
        blockers.append(
            {
                "code": "invalid_zip_central_directory",
                "kind": "wheel",
            }
        )
        return metadata, blockers

    central_cursor = central_offset
    entries: list[_RawZipEntry] = []
    local_offsets: set[int] = set()
    total_uncompressed_bytes = 0
    for index in range(total_entries):
        fixed_end = central_cursor + _ZIP_CENTRAL_HEADER.size
        if fixed_end > central_end:
            _record_unexplained_archive_bytes(
                metadata,
                blockers,
                key=f"__archive_metadata__/zip/central/{index:04d}/tail",
                payload=payload[central_cursor:central_end],
                kind="wheel",
                location="central_directory",
            )
            blockers.append(
                {
                    "code": "invalid_zip_central_directory",
                    "kind": "wheel",
                    "entry_index": index,
                }
            )
            return metadata, blockers
        fields = _ZIP_CENTRAL_HEADER.unpack(
            payload[central_cursor:fixed_end]
        )
        if fields[0] != _ZIP_CENTRAL_HEADER_SIGNATURE:
            _record_unexplained_archive_bytes(
                metadata,
                blockers,
                key=f"__archive_metadata__/zip/central/{index:04d}/tail",
                payload=payload[central_cursor:central_end],
                kind="wheel",
                location="central_directory",
            )
            blockers.append(
                {
                    "code": "invalid_zip_central_directory",
                    "kind": "wheel",
                    "entry_index": index,
                }
            )
            return metadata, blockers
        name_length = int(fields[10])
        extra_length = int(fields[11])
        entry_comment_length = int(fields[12])
        record_end = (
            fixed_end
            + name_length
            + extra_length
            + entry_comment_length
        )
        if record_end > central_end:
            _record_unexplained_archive_bytes(
                metadata,
                blockers,
                key=f"__archive_metadata__/zip/central/{index:04d}/tail",
                payload=payload[central_cursor:central_end],
                kind="wheel",
                location="central_directory",
            )
            blockers.append(
                {
                    "code": "invalid_zip_central_directory",
                    "kind": "wheel",
                    "entry_index": index,
                }
            )
            return metadata, blockers
        raw_name = payload[fixed_end : fixed_end + name_length]
        central_extra = payload[
            fixed_end + name_length : fixed_end + name_length + extra_length
        ]
        entry_comment = payload[
            fixed_end + name_length + extra_length : record_end
        ]
        _record_archive_metadata(
            metadata,
            blockers,
            key=f"__archive_metadata__/zip/central/{index:04d}",
            payload=payload[central_cursor:record_end],
            kind="wheel",
        )
        _record_archive_metadata(
            metadata,
            blockers,
            key=(
                "__archive_metadata__/zip/central-fields/"
                f"{index:04d}"
            ),
            payload=_fixed_field_projection(
                payload[central_cursor:fixed_end],
                (4, 2, 2, 2, 2, 2, 2, 4, 4, 4, 2, 2, 2, 2, 2, 4, 4),
            ),
            kind="wheel",
        )
        flag_bits = int(fields[3])
        try:
            name = _decode_zip_filename(raw_name, flag_bits)
        except UnicodeDecodeError:
            name = raw_name.decode("utf-8", errors="surrogateescape")
            blockers.append(
                {
                    "code": "invalid_zip_filename",
                    "kind": "wheel",
                    "entry": name,
                    "reason": "invalid_encoding",
                }
            )
        if b"\0" in raw_name or "\0" in name:
            blockers.append(
                {
                    "code": "invalid_zip_filename",
                    "kind": "wheel",
                    "entry": name,
                    "reason": "embedded_nul",
                }
            )
        if central_extra:
            blockers.append(
                {
                    "code": "unexpected_archive_metadata",
                    "kind": "wheel",
                    "entry": name,
                    "metadata": "extra",
                }
            )
        if entry_comment:
            blockers.append(
                {
                    "code": "unexpected_archive_metadata",
                    "kind": "wheel",
                    "entry": name,
                    "metadata": "comment",
                }
            )
        disk_start = int(fields[13])
        compressed_size = int(fields[8])
        uncompressed_size = int(fields[9])
        local_offset = int(fields[16])
        if (
            disk_start
            or compressed_size == 0xFFFFFFFF
            or uncompressed_size == 0xFFFFFFFF
            or local_offset == 0xFFFFFFFF
        ):
            blockers.append(
                {
                    "code": "unsupported_zip_structure",
                    "kind": "wheel",
                    "entry": name,
                }
            )
        if (
            compressed_size > MAX_ARCHIVE_MEMBER_BYTES
            or uncompressed_size > MAX_ARCHIVE_MEMBER_BYTES
        ):
            blockers.append(
                {
                    "code": "archive_member_limit_exceeded",
                    "kind": "wheel",
                    "entry": name,
                    "observed": max(compressed_size, uncompressed_size),
                    "limit": MAX_ARCHIVE_MEMBER_BYTES,
                }
            )
            return metadata, blockers
        total_uncompressed_bytes += uncompressed_size
        if total_uncompressed_bytes > MAX_ARCHIVE_TOTAL_BYTES:
            blockers.append(
                {
                    "code": "archive_total_limit_exceeded",
                    "kind": "wheel",
                    "observed": total_uncompressed_bytes,
                    "limit": MAX_ARCHIVE_TOTAL_BYTES,
                }
            )
            return metadata, blockers
        if local_offset in local_offsets:
            blockers.append(
                {
                    "code": "duplicate_zip_local_offset",
                    "kind": "wheel",
                    "entry": name,
                }
            )
        local_offsets.add(local_offset)
        entries.append(
            _RawZipEntry(
                index=index,
                name=name,
                raw_name=raw_name,
                version_needed=int(fields[2]),
                flag_bits=flag_bits,
                compression=int(fields[4]),
                modified_time=int(fields[5]),
                modified_date=int(fields[6]),
                crc32=int(fields[7]),
                compressed_size=compressed_size,
                uncompressed_size=uncompressed_size,
                local_offset=local_offset,
                central_extra=central_extra,
            )
        )
        central_cursor = record_end
    if central_cursor != central_end:
        _record_unexplained_archive_bytes(
            metadata,
            blockers,
            key="__archive_metadata__/zip/central/trailing",
            payload=payload[central_cursor:central_end],
            kind="wheel",
            location="central_directory",
        )
        blockers.append(
            {
                "code": "invalid_zip_central_directory",
                "kind": "wheel",
            }
        )
        return metadata, blockers

    local_cursor = 0
    for entry in sorted(entries, key=lambda item: item.local_offset):
        if entry.local_offset != local_cursor:
            if local_cursor < entry.local_offset <= central_offset:
                _record_unexplained_archive_bytes(
                    metadata,
                    blockers,
                    key=(
                        "__archive_metadata__/zip/local/"
                        f"{entry.index:04d}/preamble-or-gap"
                    ),
                    payload=payload[local_cursor : entry.local_offset],
                    kind="wheel",
                    location="local_record_graph",
                )
            else:
                blockers.append(
                    {
                        "code": "overlapping_zip_records",
                        "kind": "wheel",
                        "entry": entry.name,
                    }
                )
                return metadata, blockers
        fixed_end = entry.local_offset + _ZIP_LOCAL_HEADER.size
        if fixed_end > central_offset:
            blockers.append(
                {
                    "code": "invalid_zip_local_header",
                    "kind": "wheel",
                    "entry": entry.name,
                }
            )
            return metadata, blockers
        fields = _ZIP_LOCAL_HEADER.unpack(
            payload[entry.local_offset:fixed_end]
        )
        if fields[0] != _ZIP_LOCAL_HEADER_SIGNATURE:
            blockers.append(
                {
                    "code": "invalid_zip_local_header",
                    "kind": "wheel",
                    "entry": entry.name,
                }
            )
            return metadata, blockers
        name_length = int(fields[9])
        extra_length = int(fields[10])
        header_end = fixed_end + name_length + extra_length
        if header_end > central_offset:
            blockers.append(
                {
                    "code": "invalid_zip_local_header",
                    "kind": "wheel",
                    "entry": entry.name,
                }
            )
            return metadata, blockers
        raw_name = payload[fixed_end : fixed_end + name_length]
        local_extra = payload[fixed_end + name_length : header_end]
        _record_archive_metadata(
            metadata,
            blockers,
            key=f"__archive_metadata__/zip/local/{entry.index:04d}",
            payload=payload[entry.local_offset:header_end],
            kind="wheel",
            entry=entry.name,
        )
        _record_archive_metadata(
            metadata,
            blockers,
            key=(
                "__archive_metadata__/zip/local-fields/"
                f"{entry.index:04d}"
            ),
            payload=_fixed_field_projection(
                payload[entry.local_offset:fixed_end],
                (4, 2, 2, 2, 2, 2, 4, 4, 4, 2, 2),
            ),
            kind="wheel",
            entry=entry.name,
        )
        try:
            local_name = _decode_zip_filename(raw_name, int(fields[2]))
        except UnicodeDecodeError:
            local_name = raw_name.decode("utf-8", errors="surrogateescape")
            blockers.append(
                {
                    "code": "invalid_zip_filename",
                    "kind": "wheel",
                    "entry": local_name,
                    "reason": "invalid_encoding",
                }
            )
        if b"\0" in raw_name or "\0" in local_name:
            blockers.append(
                {
                    "code": "invalid_zip_filename",
                    "kind": "wheel",
                    "entry": local_name,
                    "reason": "embedded_nul",
                }
            )
        local_values = (
            int(fields[1]),
            int(fields[2]),
            int(fields[3]),
            int(fields[4]),
            int(fields[5]),
        )
        central_values = (
            entry.version_needed,
            entry.flag_bits,
            entry.compression,
            entry.modified_time,
            entry.modified_date,
        )
        if (
            raw_name != entry.raw_name
            or local_values != central_values
            or local_extra != entry.central_extra
        ):
            blockers.append(
                {
                    "code": "archive_header_mismatch",
                    "kind": "wheel",
                    "entry": entry.name,
                    "metadata": "local_and_central_headers",
                }
            )
        data_end = header_end + entry.compressed_size
        if data_end > central_offset:
            blockers.append(
                {
                    "code": "invalid_zip_local_header",
                    "kind": "wheel",
                    "entry": entry.name,
                }
            )
            return metadata, blockers
        _validate_raw_zip_payload(
            memoryview(payload)[header_end:data_end],
            entry,
            metadata,
            blockers,
        )
        local_crc = int(fields[6])
        local_compressed_size = int(fields[7])
        local_uncompressed_size = int(fields[8])
        record_end = data_end
        if entry.flag_bits & 0x08:
            descriptor_offset = data_end
            if (
                payload[
                    descriptor_offset : descriptor_offset + 4
                ]
                == _ZIP_DATA_DESCRIPTOR_SIGNATURE
            ):
                descriptor_offset += 4
            descriptor_end = descriptor_offset + _ZIP_DATA_DESCRIPTOR.size
            if descriptor_end > central_offset:
                blockers.append(
                    {
                        "code": "invalid_zip_data_descriptor",
                        "kind": "wheel",
                        "entry": entry.name,
                    }
                )
                return metadata, blockers
            descriptor = _ZIP_DATA_DESCRIPTOR.unpack(
                payload[descriptor_offset:descriptor_end]
            )
            record_end = descriptor_end
            _record_archive_metadata(
                metadata,
                blockers,
                key=(
                    "__archive_metadata__/zip/descriptor/"
                    f"{entry.index:04d}"
                ),
                payload=payload[data_end:descriptor_end],
                kind="wheel",
                entry=entry.name,
            )
            _record_archive_metadata(
                metadata,
                blockers,
                key=(
                    "__archive_metadata__/zip/descriptor-fields/"
                    f"{entry.index:04d}"
                ),
                payload=_fixed_field_projection(
                    payload[descriptor_offset:descriptor_end],
                    (4, 4, 4),
                ),
                kind="wheel",
                entry=entry.name,
            )
            if tuple(int(value) for value in descriptor) != (
                entry.crc32,
                entry.compressed_size,
                entry.uncompressed_size,
            ):
                blockers.append(
                    {
                        "code": "invalid_zip_data_descriptor",
                        "kind": "wheel",
                        "entry": entry.name,
                    }
                )
            if (
                local_crc not in {0, entry.crc32}
                or local_compressed_size not in {0, entry.compressed_size}
                or local_uncompressed_size
                not in {0, entry.uncompressed_size}
            ):
                blockers.append(
                    {
                        "code": "archive_header_mismatch",
                        "kind": "wheel",
                        "entry": entry.name,
                        "metadata": "data_descriptor_sizes",
                    }
                )
        elif (
            local_crc != entry.crc32
            or local_compressed_size != entry.compressed_size
            or local_uncompressed_size != entry.uncompressed_size
        ):
            blockers.append(
                {
                    "code": "archive_header_mismatch",
                    "kind": "wheel",
                    "entry": entry.name,
                    "metadata": "crc_or_sizes",
                }
            )
        local_cursor = record_end
    if local_cursor != central_offset:
        if local_cursor < central_offset:
            _record_unexplained_archive_bytes(
                metadata,
                blockers,
                key="__archive_metadata__/zip/local/trailing",
                payload=payload[local_cursor:central_offset],
                kind="wheel",
                location="local_record_graph",
            )
        else:
            blockers.append(
                {
                    "code": "overlapping_zip_records",
                    "kind": "wheel",
                }
            )
    return metadata, blockers


def _read_gzip_c_string(handle: io.BufferedReader) -> bytes:
    payload = bytearray()
    while len(payload) <= MAX_ARCHIVE_HEADER_FIELD_BYTES:
        character = handle.read(1)
        if not character:
            raise ValueError("truncated gzip header string")
        if character == b"\0":
            return bytes(payload)
        payload.extend(character)
    raise ValueError("gzip header string exceeds limit")


def _gzip_member_end(
    payload: bytes,
    *,
    header_end: int,
    remaining_uncompressed_bytes: int,
) -> tuple[int, bytes]:
    decompressor = zlib.decompressobj(wbits=-zlib.MAX_WBITS)
    cursor = header_end
    checksum = 0
    uncompressed_payload = bytearray()
    while not decompressor.eof:
        if cursor >= len(payload):
            raise ValueError("truncated gzip deflate stream")
        chunk = payload[cursor : cursor + 64 * 1024]
        uncompressed = decompressor.decompress(chunk)
        consumed = len(chunk) - len(decompressor.unused_data)
        if consumed <= 0 and not uncompressed:
            raise ValueError("invalid gzip deflate stream")
        cursor += consumed
        checksum = zlib.crc32(uncompressed, checksum)
        uncompressed_payload.extend(uncompressed)
        if len(uncompressed_payload) > remaining_uncompressed_bytes:
            raise OverflowError(len(uncompressed_payload))
    trailer = payload[cursor : cursor + _GZIP_TRAILER.size]
    if len(trailer) != _GZIP_TRAILER.size:
        raise ValueError("truncated gzip trailer")
    expected_checksum, expected_size = _GZIP_TRAILER.unpack(trailer)
    if (
        expected_checksum != checksum & 0xFFFFFFFF
        or expected_size != len(uncompressed_payload) & 0xFFFFFFFF
    ):
        raise ValueError("invalid gzip trailer")
    return cursor + _GZIP_TRAILER.size, bytes(uncompressed_payload)


def _record_invalid_gzip_remainder(
    metadata: dict[str, bytes],
    blockers: list[dict[str, object]],
    payload: bytes,
    *,
    offset: int,
    member_index: int,
    record_or_prescan: Callable[[str, bytes], bool],
) -> None:
    if offset >= len(payload):
        return
    remainder = payload[offset:]
    record_or_prescan(
        "__archive_metadata__/gzip/unparsed/"
        f"{member_index:04d}",
        remainder,
    )
    blockers.append(
        {
            "code": "unexpected_archive_bytes",
            "kind": "sdist",
            "location": "invalid_gzip_remainder",
            "observed": len(remainder),
        }
    )


def _gzip_header_metadata(
    path: Path,
) -> tuple[
    dict[str, bytes],
    list[dict[str, object]],
    bytes | None,
    list[dict[str, object]],
    list[dict[str, object]],
]:
    metadata = _ArchiveMetadata()
    blockers: list[dict[str, object]] = []
    prescanned_inputs = metadata.prescanned_inputs
    prescanned_findings = metadata.prescanned_findings

    def record_or_prescan(key: str, member_payload: bytes) -> bool:
        return _record_archive_metadata(
            metadata,
            blockers,
            key=key,
            payload=member_payload,
            kind="sdist",
        )

    archive_size = path.stat().st_size
    if archive_size > MAX_ARCHIVE_STREAM_BYTES:
        return (
            metadata,
            [
                {
                    "code": "archive_stream_limit_exceeded",
                    "kind": "sdist",
                    "observed": archive_size,
                    "limit": MAX_ARCHIVE_STREAM_BYTES,
                }
            ],
            None,
            prescanned_inputs,
            prescanned_findings,
        )
    with path.open("rb") as handle:
        payload = handle.read(MAX_ARCHIVE_STREAM_BYTES + 1)
    if len(payload) > MAX_ARCHIVE_STREAM_BYTES:
        return (
            metadata,
            [
                {
                    "code": "archive_stream_limit_exceeded",
                    "kind": "sdist",
                    "observed": len(payload),
                    "limit": MAX_ARCHIVE_STREAM_BYTES,
                }
            ],
            None,
            prescanned_inputs,
            prescanned_findings,
        )
    offset = 0
    member_index = 0
    total_uncompressed_bytes = 0
    first_member_payload: bytes | None = None
    while offset < len(payload):
        if member_index >= MAX_ARCHIVE_ENTRIES:
            blockers.append(
                {
                    "code": "archive_entry_limit_exceeded",
                    "kind": "sdist",
                    "observed": member_index + 1,
                    "limit": MAX_ARCHIVE_ENTRIES,
                }
            )
            _record_invalid_gzip_remainder(
                metadata,
                blockers,
                payload,
                offset=offset,
                member_index=member_index,
                record_or_prescan=record_or_prescan,
            )
            break
        if member_index:
            blockers.append(
                {
                    "code": "unexpected_gzip_member",
                    "kind": "sdist",
                    "member": member_index,
                }
            )
        handle = io.BytesIO(payload)
        handle.seek(offset)
        fixed = handle.read(_GZIP_FIXED_HEADER.size)
        if len(fixed) != _GZIP_FIXED_HEADER.size:
            blockers.append({"code": "invalid_gzip_header", "kind": "sdist"})
            _record_invalid_gzip_remainder(
                metadata,
                blockers,
                payload,
                offset=offset,
                member_index=member_index,
                record_or_prescan=record_or_prescan,
            )
            break
        signature, method, flags, _, _, _ = _GZIP_FIXED_HEADER.unpack(fixed)
        if signature != _GZIP_SIGNATURE or method != 8 or flags & 0xE0:
            blockers.append({"code": "invalid_gzip_header", "kind": "sdist"})
            _record_invalid_gzip_remainder(
                metadata,
                blockers,
                payload,
                offset=offset,
                member_index=member_index,
                record_or_prescan=record_or_prescan,
            )
            break
        metadata_prefix = (
            "__archive_metadata__/gzip"
            if member_index == 0
            else (
                "__archive_metadata__/"
                f"gzip/member/{member_index:04d}"
            )
        )
        try:
            if flags & 0x04:
                raw_length = handle.read(2)
                if len(raw_length) != 2:
                    raise ValueError("truncated gzip extra length")
                extra_length = int.from_bytes(raw_length, "little")
                extra = handle.read(extra_length)
                if len(extra) != extra_length:
                    raise ValueError("truncated gzip extra metadata")
                record_or_prescan(
                    f"{metadata_prefix}/extra",
                    extra,
                )
                blockers.append(
                    {
                        "code": "unexpected_archive_metadata",
                        "kind": "sdist",
                        "metadata": "gzip_extra",
                        "member": member_index,
                    }
                )
            if flags & 0x08:
                filename = _read_gzip_c_string(handle)
                record_or_prescan(
                    f"{metadata_prefix}/filename",
                    filename,
                )
                expected = path.name.removesuffix(".gz").encode("utf-8")
                if member_index or filename != expected:
                    blockers.append(
                        {
                            "code": "unexpected_archive_metadata",
                            "kind": "sdist",
                            "metadata": "gzip_filename",
                            "member": member_index,
                        }
                    )
            if flags & 0x10:
                comment = _read_gzip_c_string(handle)
                record_or_prescan(
                    f"{metadata_prefix}/comment",
                    comment,
                )
                blockers.append(
                    {
                        "code": "unexpected_archive_metadata",
                        "kind": "sdist",
                        "metadata": "gzip_comment",
                        "member": member_index,
                    }
                )
            if flags & 0x02:
                header_checksum_end = handle.tell()
                raw_checksum = handle.read(2)
                if len(raw_checksum) != 2:
                    raise ValueError("truncated gzip header checksum")
                expected_header_checksum = int.from_bytes(
                    raw_checksum,
                    "little",
                )
                observed_header_checksum = (
                    zlib.crc32(payload[offset:header_checksum_end]) & 0xFFFF
                )
                if expected_header_checksum != observed_header_checksum:
                    blockers.append(
                        {
                            "code": "invalid_gzip_header_checksum",
                            "kind": "sdist",
                            "member": member_index,
                        }
                    )
                    _record_invalid_gzip_remainder(
                        metadata,
                        blockers,
                        payload,
                        offset=offset,
                        member_index=member_index,
                        record_or_prescan=record_or_prescan,
                    )
                    break
            record_or_prescan(
                f"{metadata_prefix}/raw-header",
                payload[offset : handle.tell()],
            )
            record_or_prescan(
                f"{metadata_prefix}/fixed-header-fields",
                _fixed_field_projection(
                    fixed,
                    (2, 1, 1, 4, 1, 1),
                ),
            )
            next_offset, uncompressed_payload = _gzip_member_end(
                payload,
                header_end=handle.tell(),
                remaining_uncompressed_bytes=(
                    MAX_ARCHIVE_STREAM_BYTES - total_uncompressed_bytes
                ),
            )
        except OverflowError as error:
            observed = total_uncompressed_bytes + int(error.args[0])
            blockers.append(
                {
                    "code": "archive_stream_limit_exceeded",
                    "kind": "sdist",
                    "observed": observed,
                    "limit": MAX_ARCHIVE_STREAM_BYTES,
                }
            )
            break
        except (ValueError, zlib.error):
            blockers.append({"code": "invalid_gzip_header", "kind": "sdist"})
            _record_invalid_gzip_remainder(
                metadata,
                blockers,
                payload,
                offset=offset,
                member_index=member_index,
                record_or_prescan=record_or_prescan,
            )
            break
        if member_index == 0:
            first_member_payload = uncompressed_payload
        else:
            metadata_key = (
                "__archive_metadata__/gzip/member/"
                f"{member_index:04d}/uncompressed-payload"
            )
            record_or_prescan(metadata_key, uncompressed_payload)
        total_uncompressed_bytes += len(uncompressed_payload)
        offset = next_offset
        member_index += 1
    return (
        metadata,
        blockers,
        first_member_payload,
        prescanned_inputs,
        prescanned_findings,
    )


def _parse_tar_octal(field: bytes) -> int:
    if field and field[0] & 0x80:
        raise ValueError("base-256 TAR number is not canonical")
    value = field.rstrip(b"\0 ").lstrip(b" ")
    if not value:
        return 0
    if any(character not in b"01234567" for character in value):
        raise ValueError("invalid TAR octal number")
    return int(value, 8)


def _parse_pax_records(payload: bytes) -> list[tuple[bytes, bytes]]:
    records: list[tuple[bytes, bytes]] = []
    cursor = 0
    while cursor < len(payload):
        separator = payload.find(b" ", cursor)
        if separator <= cursor:
            raise ValueError("invalid PAX record length")
        raw_length = payload[cursor:separator]
        if not raw_length.isdigit() or raw_length.startswith(b"0"):
            raise ValueError("noncanonical PAX record length")
        record_length = int(raw_length)
        record_end = cursor + record_length
        if record_end > len(payload) or record_length <= len(raw_length) + 3:
            raise ValueError("truncated PAX record")
        record = payload[cursor:record_end]
        if not record.endswith(b"\n"):
            raise ValueError("unterminated PAX record")
        body = record[len(raw_length) + 1 : -1]
        assignment = body.find(b"=")
        if assignment <= 0:
            raise ValueError("invalid PAX assignment")
        records.append((body[:assignment], body[assignment + 1 :]))
        cursor = record_end
    return records


def _raw_tar_metadata(
    payload: bytes,
    metadata: dict[str, bytes],
    blockers: list[dict[str, object]],
) -> None:
    """Validate raw TAR headers, extension records, padding, and terminator."""

    if len(payload) > MAX_ARCHIVE_STREAM_BYTES:
        blockers.append(
            {
                "code": "archive_stream_limit_exceeded",
                "kind": "sdist",
                "observed": len(payload),
                "limit": MAX_ARCHIVE_STREAM_BYTES,
            }
        )
        return
    cursor = 0
    physical_entries = 0
    total_member_bytes = 0
    pending_pax_keys: set[bytes] = set()
    prescan_regular_files = False
    while cursor < len(payload):
        block_end = cursor + _TAR_BLOCK_BYTES
        if block_end > len(payload):
            _record_unexplained_archive_bytes(
                metadata,
                blockers,
                key="__archive_metadata__/tar/truncated-tail",
                payload=payload[cursor:],
                kind="sdist",
                location="tar_record_graph",
            )
            blockers.append({"code": "invalid_tar_structure", "kind": "sdist"})
            return
        block = payload[cursor:block_end]
        if block == _TAR_ZERO_BLOCK:
            second_end = block_end + _TAR_BLOCK_BYTES
            if (
                second_end > len(payload)
                or payload[block_end:second_end] != _TAR_ZERO_BLOCK
            ):
                blockers.append(
                    {
                        "code": "invalid_tar_end_markers",
                        "kind": "sdist",
                    }
                )
                return
            trailing = payload[second_end:]
            if len(trailing) % _TAR_BLOCK_BYTES:
                blockers.append(
                    {
                        "code": "noncanonical_tar_trailing_padding",
                        "kind": "sdist",
                        "observed": len(trailing),
                    }
                )
            if any(trailing):
                _record_unexplained_archive_bytes(
                    metadata,
                    blockers,
                    key="__archive_metadata__/tar/trailing",
                    payload=trailing,
                    kind="sdist",
                    location="after_tar_end_markers",
                )
                blockers.append(
                    {
                        "code": "invalid_tar_trailing_data",
                        "kind": "sdist",
                    }
                )
            if pending_pax_keys:
                blockers.append(
                    {
                        "code": "invalid_tar_extension_chain",
                        "kind": "sdist",
                    }
                )
            return

        if physical_entries >= MAX_ARCHIVE_ENTRIES:
            blockers.append(
                {
                    "code": "archive_entry_limit_exceeded",
                    "kind": "sdist",
                    "observed": physical_entries + 1,
                    "limit": MAX_ARCHIVE_ENTRIES,
                }
            )
            return
        entry_index = physical_entries
        physical_entries += 1
        _record_archive_metadata(
            metadata,
            blockers,
            key=f"__archive_metadata__/tar/header/{entry_index:04d}",
            payload=block,
            kind="sdist",
        )
        _record_archive_metadata(
            metadata,
            blockers,
            key=(
                "__archive_metadata__/tar/header-fields/"
                f"{entry_index:04d}"
            ),
            payload=_fixed_field_projection(
                block,
                (
                    100,
                    8,
                    8,
                    8,
                    12,
                    12,
                    8,
                    1,
                    100,
                    6,
                    2,
                    32,
                    32,
                    8,
                    8,
                    155,
                    12,
                ),
            ),
            kind="sdist",
        )
        try:
            expected_checksum = _parse_tar_octal(block[148:156])
            member_size = _parse_tar_octal(block[124:136])
        except ValueError:
            blockers.append(
                {
                    "code": "invalid_tar_header",
                    "kind": "sdist",
                    "entry_index": entry_index,
                }
            )
            return
        checksum_block = block[:148] + (b" " * 8) + block[156:]
        if expected_checksum != sum(checksum_block):
            blockers.append(
                {
                    "code": "invalid_tar_header_checksum",
                    "kind": "sdist",
                    "entry_index": entry_index,
                }
            )
            return
        for field_name, field in (
            ("name", block[0:100]),
            ("linkname", block[157:257]),
            ("uname", block[265:297]),
            ("gname", block[297:329]),
            ("prefix", block[345:500]),
        ):
            terminator = field.find(b"\0")
            if terminator >= 0 and any(field[terminator + 1 :]):
                blockers.append(
                    {
                        "code": "noncanonical_tar_header_padding",
                        "kind": "sdist",
                        "entry_index": entry_index,
                        "field": field_name,
                    }
                )
        if any(block[500:512]):
            blockers.append(
                {
                    "code": "noncanonical_tar_header_padding",
                    "kind": "sdist",
                    "entry_index": entry_index,
                    "field": "header_padding",
                }
            )
        data_offset = block_end
        data_end = data_offset + member_size
        padded_end = (
            data_end + _TAR_BLOCK_BYTES - 1
        ) // _TAR_BLOCK_BYTES * _TAR_BLOCK_BYTES
        if data_end > len(payload) or padded_end > len(payload):
            blockers.append(
                {
                    "code": "invalid_tar_structure",
                    "kind": "sdist",
                    "entry_index": entry_index,
                }
            )
            return
        type_flag = block[156:157]
        extension_type = type_flag in {
            tarfile.XHDTYPE,
            tarfile.XGLTYPE,
            tarfile.GNUTYPE_LONGNAME,
            tarfile.GNUTYPE_LONGLINK,
        }
        if extension_type and member_size > MAX_ARCHIVE_METADATA_BYTES:
            metadata_kind = (
                "pax"
                if type_flag in {tarfile.XHDTYPE, tarfile.XGLTYPE}
                else "gnu"
            )
            _record_archive_metadata(
                metadata,
                blockers,
                key=(
                    f"__archive_metadata__/tar/{metadata_kind}/"
                    f"{entry_index:04d}"
                ),
                payload=memoryview(payload)[data_offset:data_end],
                kind="sdist",
            )
            cursor = padded_end
            continue
        if type_flag == tarfile.DIRTYPE and member_size:
            prescan_regular_files = True
            blockers.append(
                {
                    "code": "archive_directory_payload",
                    "kind": "sdist",
                    "entry_index": entry_index,
                    "observed": member_size,
                }
            )
            _record_archive_metadata(
                metadata,
                blockers,
                key=(
                    "__archive_metadata__/tar/directory-payload/"
                    f"{entry_index:04d}"
                ),
                payload=memoryview(payload)[data_offset:data_end],
                kind="sdist",
            )
        elif not extension_type:
            if member_size > MAX_ARCHIVE_MEMBER_BYTES:
                blockers.append(
                    {
                        "code": "archive_member_limit_exceeded",
                        "kind": "sdist",
                        "entry_index": entry_index,
                        "observed": member_size,
                        "limit": MAX_ARCHIVE_MEMBER_BYTES,
                    }
                )
                return
            total_member_bytes += member_size
            if total_member_bytes > MAX_ARCHIVE_TOTAL_BYTES:
                blockers.append(
                    {
                        "code": "archive_total_limit_exceeded",
                        "kind": "sdist",
                        "observed": total_member_bytes,
                        "limit": MAX_ARCHIVE_TOTAL_BYTES,
                    }
                )
                return
            if (
                prescan_regular_files
                and type_flag in {tarfile.REGTYPE, tarfile.AREGTYPE}
            ):
                _record_archive_metadata(
                    metadata,
                    blockers,
                    key=(
                        "__archive_metadata__/tar/untrusted-file/"
                        f"{entry_index:04d}"
                    ),
                    payload=memoryview(payload)[data_offset:data_end],
                    kind="sdist",
                )
        padding = payload[data_end:padded_end]
        if any(padding):
            prescan_regular_files = True
            _record_unexplained_archive_bytes(
                metadata,
                blockers,
                key=f"__archive_metadata__/tar/padding/{entry_index:04d}",
                payload=padding,
                kind="sdist",
                location="tar_member_padding",
            )
            blockers.append(
                {
                    "code": "noncanonical_tar_member_padding",
                    "kind": "sdist",
                    "entry_index": entry_index,
                }
            )
        if type_flag in {tarfile.XHDTYPE, tarfile.XGLTYPE}:
            member_payload = payload[data_offset:data_end]
            _record_archive_metadata(
                metadata,
                blockers,
                key=f"__archive_metadata__/tar/pax/{entry_index:04d}",
                payload=member_payload,
                kind="sdist",
            )
            try:
                records = _parse_pax_records(member_payload)
            except ValueError:
                blockers.append(
                    {
                        "code": "invalid_pax_metadata",
                        "kind": "sdist",
                        "entry_index": entry_index,
                    }
                )
                return
            observed_keys: set[bytes] = set()
            for key, value in records:
                if key in observed_keys or (
                    type_flag == tarfile.XHDTYPE and key in pending_pax_keys
                ):
                    blockers.append(
                        {
                            "code": "duplicate_pax_key",
                            "kind": "sdist",
                            "entry_index": entry_index,
                            "key": key.decode("ascii", errors="backslashreplace"),
                        }
                    )
                observed_keys.add(key)
                if b"\0" in key or b"\0" in value:
                    blockers.append(
                        {
                            "code": "invalid_pax_metadata",
                            "kind": "sdist",
                            "entry_index": entry_index,
                        }
                    )
                if type_flag == tarfile.XGLTYPE:
                    continue
                if key == b"path":
                    try:
                        value.decode("utf-8")
                    except UnicodeDecodeError:
                        blockers.append(
                            {
                                "code": "invalid_pax_metadata",
                                "kind": "sdist",
                                "entry_index": entry_index,
                                "key": "path",
                            }
                        )
                elif key == b"mtime":
                    if re.fullmatch(rb"-?\d+(?:\.\d+)?", value) is None:
                        blockers.append(
                            {
                                "code": "invalid_pax_metadata",
                                "kind": "sdist",
                                "entry_index": entry_index,
                                "key": "mtime",
                            }
                        )
                else:
                    blockers.append(
                        {
                            "code": "unexpected_archive_metadata",
                            "kind": "sdist",
                            "metadata": "pax",
                            "key": key.decode(
                                "ascii",
                                errors="backslashreplace",
                            ),
                        }
                    )
            if type_flag == tarfile.XGLTYPE:
                blockers.append(
                    {
                        "code": "unexpected_archive_metadata",
                        "kind": "sdist",
                        "metadata": "global_pax",
                    }
                )
            else:
                pending_pax_keys.update(observed_keys)
        elif type_flag in {tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK}:
            member_payload = payload[data_offset:data_end]
            _record_archive_metadata(
                metadata,
                blockers,
                key=f"__archive_metadata__/tar/gnu/{entry_index:04d}",
                payload=member_payload,
                kind="sdist",
            )
            terminator = member_payload.find(b"\0")
            if terminator < 0 or any(member_payload[terminator + 1 :]):
                blockers.append(
                    {
                        "code": "invalid_gnu_tar_metadata",
                        "kind": "sdist",
                        "entry_index": entry_index,
                    }
                )
            blockers.append(
                {
                    "code": "unexpected_archive_metadata",
                    "kind": "sdist",
                    "metadata": "gnu_long_name",
                }
            )
        else:
            pending_pax_keys.clear()
        cursor = padded_end
    blockers.append({"code": "invalid_tar_end_markers", "kind": "sdist"})


def inspect_wheel(path: Path) -> ArchiveSnapshot:
    """Read one wheel with traversal, duplicate, link, and size checks."""

    metadata, blockers = _raw_zip_metadata(path)
    prescanned_inputs = (
        metadata.prescanned_inputs
        if isinstance(metadata, _ArchiveMetadata)
        else []
    )
    prescanned_findings = (
        metadata.prescanned_findings
        if isinstance(metadata, _ArchiveMetadata)
        else []
    )
    files: dict[str, bytes] = {}
    directories: list[str] = []
    entries: list[str] = []
    total_bytes = 0
    nonfatal_codes = {
        "archive_metadata_limit_exceeded",
        "archive_directory_payload",
        "unexpected_archive_metadata",
    }
    if any(
        blocker.get("code") not in nonfatal_codes
        for blocker in blockers
    ):
        return ArchiveSnapshot(
            kind="wheel",
            path=path,
            digest=sha256_file(path),
            entries=(),
            files={},
            blockers=tuple(blockers),
            directories=(),
            metadata=dict(sorted(metadata.items())),
            prescanned_inputs=tuple(prescanned_inputs),
            prescanned_findings=tuple(prescanned_findings),
        )
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
        for index, info in enumerate(infos[: MAX_ARCHIVE_ENTRIES + 1]):
            name = info.filename
            entries.append(name)
            if info.orig_filename != info.filename or "\0" in info.orig_filename:
                blockers.append(
                    {
                        "code": "invalid_zip_filename",
                        "kind": "wheel",
                        "entry": name,
                        "reason": "normalized_filename_divergence",
                    }
                )
                continue
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
            with archive.open(info) as member:
                member_payload = member.read(MAX_ARCHIVE_MEMBER_BYTES + 1)
            if len(member_payload) != info.file_size:
                blockers.append(
                    {
                        "code": "archive_member_size_mismatch",
                        "kind": "wheel",
                        "entry": name,
                    }
                )
                continue
            files[name] = member_payload
    return ArchiveSnapshot(
        kind="wheel",
        path=path,
        digest=sha256_file(path),
        entries=tuple(sorted(entries)),
        files=files,
        blockers=tuple(blockers),
        directories=tuple(sorted(directories)),
        metadata=dict(sorted(metadata.items())),
        prescanned_inputs=tuple(prescanned_inputs),
        prescanned_findings=tuple(prescanned_findings),
    )


def inspect_sdist(path: Path) -> ArchiveSnapshot:
    """Read one source distribution with strict regular-file semantics."""

    files: dict[str, bytes] = {}
    directories: list[str] = []
    (
        metadata,
        blockers,
        tar_payload,
        prescanned_inputs,
        prescanned_findings,
    ) = _gzip_header_metadata(path)
    entries: list[str] = []
    total_bytes = 0
    expected_root = _expected_sdist_root(path)
    if tar_payload is None:
        blockers.append({"code": "invalid_tar_stream", "kind": "sdist"})
    else:
        _raw_tar_metadata(tar_payload, metadata, blockers)
    if not expected_root:
        blockers.append(
            {
                "code": "invalid_sdist_filename",
                "kind": "sdist",
                "filename": path.name,
            }
        )
    nonfatal_codes = {
        "archive_directory_payload",
        "archive_metadata_limit_exceeded",
        "invalid_sdist_filename",
        "noncanonical_tar_member_padding",
        "unexpected_archive_bytes",
        "unexpected_archive_metadata",
        "unexpected_gzip_member",
    }
    if (
        tar_payload is None
        or any(
            blocker.get("code") not in nonfatal_codes
            for blocker in blockers
        )
    ):
        return ArchiveSnapshot(
            kind="sdist",
            path=path,
            digest=sha256_file(path),
            entries=(),
            files={},
            blockers=tuple(blockers),
            directories=(),
            metadata=dict(sorted(metadata.items())),
            prescanned_inputs=tuple(prescanned_inputs),
            prescanned_findings=tuple(prescanned_findings),
        )
    with tarfile.open(fileobj=io.BytesIO(tar_payload), mode="r:") as archive:
        seen: set[str] = set()
        for member_index, member in enumerate(archive):
            if member_index >= MAX_ARCHIVE_ENTRIES:
                blockers.append(
                    {
                        "code": "archive_entry_limit_exceeded",
                        "kind": "sdist",
                        "observed": member_index + 1,
                        "limit": MAX_ARCHIVE_ENTRIES,
                    }
                )
                break
            name = member.name
            entries.append(name)
            for key, value in sorted(member.pax_headers.items()):
                valid_path = key == "path" and value == name
                valid_mtime = (
                    key == "mtime"
                    and re.fullmatch(r"-?\d+(?:\.\d+)?", value) is not None
                    and float(value) == float(member.mtime)
                )
                if not valid_path and not valid_mtime:
                    blockers.append(
                        {
                            "code": "unexpected_archive_metadata",
                            "kind": "sdist",
                            "entry": name,
                            "metadata": "pax",
                        }
                    )
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
                if member.size:
                    blockers.append(
                        {
                            "code": "archive_directory_payload",
                            "kind": "sdist",
                            "entry": name,
                            "observed": member.size,
                        }
                    )
                    continue
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
            member_payload = extracted.read(MAX_ARCHIVE_MEMBER_BYTES + 1)
            if len(member_payload) != member.size:
                blockers.append(
                    {
                        "code": "archive_member_size_mismatch",
                        "kind": "sdist",
                        "entry": name,
                    }
                )
                continue
            files[name] = member_payload
    return ArchiveSnapshot(
        kind="sdist",
        path=path,
        digest=sha256_file(path),
        entries=tuple(sorted(entries)),
        files=files,
        blockers=tuple(blockers),
        directories=tuple(sorted(directories)),
        metadata=dict(sorted(metadata.items())),
        prescanned_inputs=tuple(prescanned_inputs),
        prescanned_findings=tuple(prescanned_findings),
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

    def add_finding(finding: dict[str, object]) -> bool:
        if len(findings) >= MAX_SCAN_FINDINGS - 1:
            findings.append(
                _path_finding(
                    normalized_path,
                    "scan_finding_limit_exceeded",
                )
            )
            return False
        findings.append(finding)
        return True

    path_parts = PurePosixPath(normalized_path).parts
    lowered_parts = {part.lower() for part in path_parts}
    basename = PurePosixPath(normalized_path).name.lower()
    if any(
        part.lower() == ".env" or part.lower().startswith(".env.")
        for part in path_parts
    ):
        add_finding(_path_finding(normalized_path, "env_file"))
    if _is_credential_path(basename, lowered_parts):
        add_finding(_path_finding(normalized_path, "credential_file"))
    if len(payload) > MAX_SCAN_FILE_BYTES:
        add_finding(
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
        add_finding(_path_finding(normalized_path, decode_failure))
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
    for rule_id, pattern in rules:
        for match in pattern.finditer(normalized_path):
            matched = match.group(0)
            if (
                allow_safe_fixtures
                and _safe_fixture_match(
                    normalized_path,
                    rule_id,
                    matched,
                    occurrence=1,
                )
            ):
                continue
            if not add_finding(
                {
                    "path": normalized_path,
                    "line": 0,
                    "rule_id": rule_id,
                    "fingerprint": sha256_bytes(
                        f"{rule_id}\0{matched}".encode("utf-8")
                    ),
                }
            ):
                return findings
    scan_texts, configuration_failures = _configuration_scan_texts(
        normalized_path,
        text,
    )
    dockerfile_text = _dockerfile_logical_text(normalized_path, text)
    if dockerfile_text not in scan_texts:
        scan_texts = (*scan_texts, dockerfile_text)
    structural_failures = list(configuration_failures)
    for candidate in tuple(scan_texts):
        try:
            python_cli_text, python_cli_failure = _python_cli_argument_text(
                normalized_path,
                candidate,
            )
        except RecursionError:
            python_cli_text = ""
            python_cli_failure = (
                "python_cli_analysis_limit_exceeded:recursion_depth"
            )
        python_sensitive_text, python_sensitive_failure = (
            _python_sensitive_call_text(
                normalized_path,
                candidate,
            )
        )
        if python_cli_failure:
            structural_failures.append(
                python_cli_failure.split(":", 1)[0]
            )
        if python_sensitive_failure:
            structural_failures.append(
                python_sensitive_failure.split(":", 1)[0]
            )
        for normalized in (
            (
                candidate
                if _is_dockerfile_path(normalized_path)
                else _shell_continuation_text(candidate)
            ),
            python_cli_text,
            python_sensitive_text,
        ):
            if normalized and normalized not in scan_texts:
                scan_texts = (*scan_texts, normalized)
    for candidate in tuple(scan_texts):
        joined_literals = _joined_quoted_literal_text(candidate)
        if joined_literals not in scan_texts:
            scan_texts = (*scan_texts, joined_literals)
    for rule_id in dict.fromkeys(structural_failures):
        if not add_finding(_path_finding(normalized_path, rule_id)):
            return findings
    prior_match_counts: dict[tuple[str, str], int] = {}
    for scan_index, scan_text in enumerate(scan_texts):
        local_match_counts: dict[tuple[str, str], int] = {}
        local_fixture_counts: dict[tuple[str, str], int] = {}
        for rule_id, pattern in rules:
            line = 1
            line_cursor = 0
            for match in pattern.finditer(scan_text):
                line += scan_text.count(
                    "\n",
                    line_cursor,
                    match.start(),
                )
                line_cursor = match.start()
                matched = match.group(0)
                captured_index, captured = next(
                    (
                        (group_index, group.strip())
                        for group_index, group in enumerate(
                            match.groups(),
                            start=1,
                        )
                        if group is not None
                    ),
                    (0, ""),
                )
                identity = (rule_id, captured)
                occurrence = local_match_counts.get(identity, 0) + 1
                local_match_counts[identity] = occurrence
                fixture_identity = (rule_id, matched)
                fixture_occurrence = (
                    local_fixture_counts.get(fixture_identity, 0) + 1
                )
                local_fixture_counts[fixture_identity] = fixture_occurrence
                if (
                    scan_index
                    and occurrence <= prior_match_counts.get(identity, 0)
                ):
                    continue
                if _is_configured_locally_placeholder(
                    rule_id,
                    captured,
                    normalized_path,
                    scan_text,
                    (
                        match.start(captured_index)
                        if captured_index
                        else match.start()
                    ),
                    (
                        match.end(captured_index)
                        if captured_index
                        else match.end()
                    ),
                ):
                    continue
                if allow_safe_fixtures and _safe_fixture_match(
                    normalized_path,
                    rule_id,
                    matched,
                    occurrence=fixture_occurrence,
                ):
                    continue
                if not add_finding(
                    {
                        "path": normalized_path,
                        "line": line,
                        "rule_id": rule_id,
                        "fingerprint": sha256_bytes(
                            f"{rule_id}\0{matched}".encode("utf-8")
                        ),
                    }
                ):
                    return findings
        for identity, count in local_match_counts.items():
            prior_match_counts[identity] = max(
                prior_match_counts.get(identity, 0),
                count,
            )
    return findings


def _is_configured_locally_placeholder(
    rule_id: str,
    captured: str,
    path: str,
    text: str,
    captured_start: int,
    captured_end: int,
) -> bool:
    """Allow only the complete non-routable MyProxy model placeholder."""

    if (
        rule_id != "private_model_override"
        or captured != "configured-locally"
    ):
        return False
    quote = text[captured_start - 1] if captured_start else ""
    quoted = quote in {"\"", "'"}
    if quoted:
        if text[captured_end:captured_end + 1] != quote:
            return False
        raw_tail = text[captured_end + 1:]
    else:
        raw_tail = text[captured_end:]
    tail = raw_tail.lstrip(" \t")
    comment = tail.startswith("#") and (
        quoted or len(raw_tail) != len(tail)
    )
    delimiters = ("\r", "\n", ";", "}", "]", ")")
    if quoted:
        delimiters = (*delimiters, ",")
    yaml_flow_mapping_comma = (
        not quoted
        and PurePosixPath(path).suffix.lower() in {".yaml", ".yml"}
        and tail.startswith(",")
        and text.rfind("{", 0, captured_start)
        > text.rfind("}", 0, captured_start)
    )
    return (
        not tail
        or tail.startswith(delimiters)
        or tail.startswith(("--", "&&", "||"))
        or comment
        or yaml_flow_mapping_comma
    )


def _bomless_utf32_encoding(payload: bytes) -> str:
    """Detect long aligned ASCII runs encoded as BOM-less UTF-32."""

    sample = payload[: min(len(payload), 64 * 1024)]
    if len(sample) < 32:
        return ""
    sample = sample[: len(sample) - (len(sample) % 4)]
    longest_le = 0
    longest_be = 0
    le_streak = 0
    be_streak = 0
    for first, second, third, fourth in zip(
        sample[0::4],
        sample[1::4],
        sample[2::4],
        sample[3::4],
        strict=True,
    ):
        first_text = first in {9, 10, 13} or 32 <= first <= 126
        fourth_text = fourth in {9, 10, 13} or 32 <= fourth <= 126
        le_streak = (
            le_streak + 1
            if first_text and second == third == fourth == 0
            else 0
        )
        be_streak = (
            be_streak + 1
            if first == second == third == 0 and fourth_text
            else 0
        )
        longest_le = max(longest_le, le_streak)
        longest_be = max(longest_be, be_streak)
    if longest_le >= 8 and longest_be < 8:
        return "utf-32-le"
    if longest_be >= 8 and longest_le < 8:
        return "utf-32-be"
    return ""


def _bomless_utf16_encoding(payload: bytes) -> str:
    """Detect long aligned ASCII runs encoded as BOM-less UTF-16."""

    sample = payload[: min(len(payload), 64 * 1024)]
    if len(sample) < 16:
        return ""
    sample = sample[: len(sample) - (len(sample) % 2)]
    le_streak = 0
    be_streak = 0
    longest_le = 0
    longest_be = 0
    for first, second in zip(sample[0::2], sample[1::2], strict=True):
        first_text = first in {9, 10, 13} or 32 <= first <= 126
        second_text = second in {9, 10, 13} or 32 <= second <= 126
        le_streak = le_streak + 1 if first_text and second == 0 else 0
        be_streak = be_streak + 1 if first == 0 and second_text else 0
        longest_le = max(longest_le, le_streak)
        longest_be = max(longest_be, be_streak)
    if longest_le >= 8 and longest_be < 8:
        return "utf-16-le"
    if longest_be >= 8 and longest_le < 8:
        return "utf-16-be"
    return ""


def _decode_scan_payload(path: str, payload: bytes) -> tuple[str, str]:
    suffix = PurePosixPath(path).suffix.lower()
    is_configuration = suffix in {".json", ".toml", ".yaml", ".yml"}
    is_textual = _is_text_scan_path(path)
    encoding = "utf-8"
    if payload.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        encoding = "utf-32"
    elif payload.startswith(b"\xef\xbb\xbf"):
        encoding = "utf-8-sig"
    elif payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        encoding = "utf-16"
    else:
        encoding = (
            _bomless_utf32_encoding(payload)
            or _bomless_utf16_encoding(payload)
            or encoding
        )
    try:
        text = payload.decode(
            encoding,
            errors=(
                "strict"
                if (
                    is_configuration
                    or is_textual
                    or encoding.startswith(("utf-16", "utf-32"))
                )
                else "replace"
            ),
        )
        if is_textual and "\x00" in text:
            return text, "opaque_text_payload"
        return text, ""
    except UnicodeError:
        failure = (
            "configuration_decode_failed"
            if is_configuration
            else "payload_decode_failed"
        )
        return payload.decode("utf-8", errors="replace"), failure


def _is_text_scan_path(path: str) -> bool:
    candidate = PurePosixPath(path)
    basename = candidate.name.lower()
    return (
        candidate.suffix.lower() in _TEXT_SCAN_SUFFIXES
        or basename in {
            "containerfile",
            "dockerfile",
            "license",
            "makefile",
            "manifest.in",
        }
        or basename.startswith(("containerfile.", "dockerfile."))
        or basename.endswith((".containerfile", ".dockerfile"))
        or path == "<git-diff>"
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
    dynamic_f_string_argument = "<dynamic-f-string>"
    max_arguments = 96
    max_commands = 128
    max_environment_states = 512
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
                converted = [dynamic_f_string_argument]
                if isinstance(component, ast.FormattedValue):
                    formatted = scalar_alternatives(
                        evaluate(component.value, environment)
                    )
                    format_spec = ""
                    if component.format_spec is not None:
                        format_values = scalar_alternatives(
                            evaluate(component.format_spec, environment)
                        )
                        if len(format_values) == 1:
                            format_spec = format_values[0]
                        else:
                            formatted = []
                    if formatted:
                        converted = []
                        for candidate in formatted:
                            if component.conversion == ord("r"):
                                candidate = repr(candidate)
                            elif component.conversion == ord("a"):
                                candidate = ascii(candidate)
                            elif component.conversion not in {-1, ord("s")}:
                                converted = [dynamic_f_string_argument]
                                break
                            try:
                                converted.append(
                                    format(candidate, format_spec)
                                )
                            except (TypeError, ValueError):
                                converted = [dynamic_f_string_argument]
                                break
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
            function_value = (
                resolve_attribute_value(
                    node.func,
                    environment,
                    strict_dispatch=True,
                )
                if isinstance(node.func, ast.Attribute)
                else evaluate(node.func, environment)
            )
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
            provider_values: list[str] = []
            for index, item in enumerate(sequence):
                if item == "--provider":
                    provider_values.append(
                        (
                            sequence[index + 1]
                            if index + 1 < len(sequence)
                            else dynamic_argument
                        )
                    )
                elif item.startswith("--provider="):
                    provider_values.append(item.split("=", 1)[1])
            myproxy_literal = any(
                re.fullmatch(
                    _MYPROXY_PROVIDER_PATTERN,
                    item.strip(),
                    re.IGNORECASE,
                )
                is not None
                for item in sequence
            )
            has_dynamic_f_string = any(
                dynamic_f_string_argument in item for item in sequence
            )
            if not provider_values:
                if myproxy_literal and has_dynamic_f_string:
                    mark_limited("f_string")
                continue
            sensitive_provider = any(
                dynamic_argument in provider
                or re.fullmatch(
                    _MYPROXY_PROVIDER_PATTERN,
                    provider.strip(),
                    re.IGNORECASE,
                )
                is not None
                for provider in provider_values
            )
            if sensitive_provider and has_dynamic_f_string:
                mark_limited("f_string")
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

    def contains_cli_execution_call(node: ast.AST) -> bool:
        pending = list(ast.iter_child_nodes(node))
        execution_names = {
            "Popen",
            "check_call",
            "check_output",
            "execv",
            "execve",
            "execvp",
            "execvpe",
            "run",
            "spawnl",
            "spawnle",
            "spawnlp",
            "spawnlpe",
            "spawnv",
            "spawnve",
            "spawnvp",
            "spawnvpe",
            "system",
        }
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
                function = candidate.func
                name = (
                    function.id
                    if isinstance(function, ast.Name)
                    else (
                        function.attr
                        if isinstance(function, ast.Attribute)
                        else ""
                    )
                )
                if name in execution_names:
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

    provider_fragment_function_ids = {
        identity
        for identity, definition in function_definitions.items()
        if (
            contains_cli_provider(definition)
            or contains_provider_fragments(definition)
        )
    }
    direct_provider_function_ids = {
        identity
        for identity in provider_fragment_function_ids
        if contains_cli_execution_call(function_definitions[identity])
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
        and not provider_fragment_function_ids
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
        if decorator_names == ["property"]:
            return "property"
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

    def is_external_testcase_helper(
        class_identity: int,
        attribute: str,
    ) -> bool:
        if not (
            attribute.startswith("assert")
            or attribute in {"addCleanup", "fail", "skipTest", "subTest"}
        ):
            return False
        definition_identity = class_value_definitions.get(class_identity)
        definition = class_definitions.get(definition_identity or -1)
        if definition is None:
            return False
        return any(
            (
                isinstance(base, ast.Name)
                and base.id == "TestCase"
            )
            or (
                isinstance(base, ast.Attribute)
                and isinstance(base.value, ast.Name)
                and base.value.id == "unittest"
                and base.attr == "TestCase"
            )
            for base in definition.bases
        )

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
        environment: Mapping[str, cli_value],
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
        if kind == "property":
            return invoke_callable_value(
                bind_callable_value(value, (receiver,)),
                None,
                environment,
                collect_return=True,
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
        strict_dispatch: bool = False,
    ) -> cli_value | None:
        mro = class_mro(class_identity)
        if mro is None:
            search_mro = [class_identity]
        else:
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
                    environment=environment,
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
                environment=environment,
            )
        if (
            mro is None
            and strict_dispatch
            and not is_external_testcase_helper(class_identity, attribute)
        ):
            mark_limited("dispatch")
        return None

    def resolve_attribute_value(
        node: ast.Attribute,
        environment: Mapping[str, cli_value],
        *,
        strict_dispatch: bool = False,
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
                        strict_dispatch=strict_dispatch,
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
                    strict_dispatch=strict_dispatch,
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
                    strict_dispatch=strict_dispatch,
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

    def value_supplies_provider_seed(value: cli_value | None) -> bool:
        return any(
            re.fullmatch(
                _MYPROXY_PROVIDER_PATTERN,
                candidate.strip(),
                re.IGNORECASE,
            )
            is not None
            for candidate in scalar_alternatives(value)
        ) or any(
            any(
                item == "--provider"
                or item.startswith("--provider=")
                for item in sequence
            )
            for sequence in sequence_alternatives(value)
        )

    def call_supplies_provider_seed(
        call: ast.Call | None,
        environment: Mapping[str, cli_value],
    ) -> bool:
        return call is not None and any(
            value_supplies_provider_seed(evaluate(node, environment))
            for node in (
                *call.args,
                *(keyword.value for keyword in call.keywords),
            )
        )

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
            if (
                kind == "function"
                and not active_function_calls
                and not active_lambda_calls
                and definition_identity not in relevant_function_ids
                and not contains_cli_provider(definition)
                and not contains_provider_fragments(definition)
                and not call_supplies_provider_seed(call, environment)
                and not any(
                    value_supplies_provider_seed(captured)
                    for captured in captured_environment.values()
                )
            ):
                continue
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
                    if identity in relevant_function_ids:
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
                                    environment=state,
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


def _python_sensitive_call_text(path: str, text: str) -> tuple[str, str]:
    if PurePosixPath(path).suffix.lower() != ".py":
        return "", ""
    try:
        tree = ast.parse(text)
    except RecursionError:
        return "", "python_sensitive_analysis_limit_exceeded:parse"
    except (SyntaxError, ValueError):
        return "", ""
    calls: list[str] = []
    observed_calls: set[str] = set()
    analysis_limited = False
    analysis_limit_reason = ""
    analysis_steps = 0
    ast_node_count = sum(1 for _ in ast.walk(tree))
    max_analysis_steps = min(
        MAX_PYTHON_SENSITIVE_ANALYSIS_STEPS,
        max(
            MIN_PYTHON_SENSITIVE_ANALYSIS_STEPS,
            ast_node_count * PYTHON_SENSITIVE_ANALYSIS_STEPS_PER_NODE,
        ),
    )
    max_bindings = 512

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

    def record(key: str | None, value: str | None) -> None:
        if key is None:
            return
        normalized = f"{key}={value if value is not None else '<dynamic-value>'}"
        if normalized not in observed_calls:
            observed_calls.add(normalized)
            calls.append(normalized)

    def is_environment_expression(
        node: ast.AST,
        environment_aliases: set[str],
        os_aliases: set[str],
    ) -> bool:
        if isinstance(node, ast.Name):
            return node.id in environment_aliases
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in os_aliases
            and _constant_string_expression(node.args[1]) == "environ"
        ):
            return True
        return (
            isinstance(node, ast.Attribute)
            and node.attr == "environ"
            and isinstance(node.value, ast.Name)
            and node.value.id in os_aliases
        )

    def mapping_expression(
        node: ast.AST,
        strings: Mapping[str, str],
        mappings: Mapping[str, Mapping[str, str | None]],
    ) -> dict[str, str | None] | None:
        if isinstance(node, ast.Name):
            value = mappings.get(node.id)
            return dict(value) if value is not None else None
        if isinstance(node, ast.Dict):
            result: dict[str, str | None] = {}
            for key_node, value_node in zip(
                node.keys,
                node.values,
                strict=True,
            ):
                if key_node is None:
                    nested = mapping_expression(value_node, strings, mappings)
                    if nested is None:
                        return None
                    result.update(nested)
                    continue
                key = _constant_string_expression(key_node, strings)
                if key is None:
                    return None
                result[key] = _constant_string_expression(value_node, strings)
            return result
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "dict"
        ):
            result = {}
            if len(node.args) > 1:
                return None
            if node.args:
                nested = mapping_expression(node.args[0], strings, mappings)
                if nested is None:
                    return None
                result.update(nested)
            for keyword in node.keywords:
                if keyword.arg is None:
                    nested = mapping_expression(
                        keyword.value,
                        strings,
                        mappings,
                    )
                    if nested is None:
                        return None
                    result.update(nested)
                else:
                    result[keyword.arg] = _constant_string_expression(
                        keyword.value,
                        strings,
                    )
            return result
        return None

    def record_call(
        node: ast.Call,
        strings: Mapping[str, str],
        mappings: Mapping[str, Mapping[str, str | None]],
        environment_aliases: set[str],
        os_aliases: set[str],
        putenv_aliases: set[str],
    ) -> None:
        function = node.func
        is_putenv = (
            isinstance(function, ast.Name)
            and function.id in putenv_aliases
        ) or (
            isinstance(function, ast.Attribute)
            and function.attr == "putenv"
            and isinstance(function.value, ast.Name)
            and function.value.id in os_aliases
        )
        if is_putenv and len(node.args) >= 2:
            record(
                _constant_string_expression(node.args[0], strings),
                _constant_string_expression(node.args[1], strings),
            )
            return
        if not isinstance(function, ast.Attribute) or not (
            is_environment_expression(
                function.value,
                environment_aliases,
                os_aliases,
            )
        ):
            return
        if function.attr in {"__setitem__", "setdefault"} and len(node.args) >= 2:
            record(
                _constant_string_expression(node.args[0], strings),
                _constant_string_expression(node.args[1], strings),
            )
        elif function.attr == "update":
            updates: dict[str, str | None] = {}
            if node.args:
                if len(node.args) > 1:
                    return
                mapping = mapping_expression(node.args[0], strings, mappings)
                if mapping is None:
                    return
                updates.update(mapping)
            for keyword in node.keywords:
                if keyword.arg is None:
                    mapping = mapping_expression(
                        keyword.value,
                        strings,
                        mappings,
                    )
                    if mapping is None:
                        return
                    updates.update(mapping)
                else:
                    updates[keyword.arg] = _constant_string_expression(
                        keyword.value,
                        strings,
                    )
            for key, value in updates.items():
                record(key, value)

    def current_statement_nodes(statement: ast.stmt) -> Iterable[ast.AST]:
        pending: list[ast.AST] = [statement]
        while pending:
            if not step():
                return
            node = pending.pop()
            yield node
            pending.extend(
                reversed(
                    tuple(
                        child
                        for child in ast.iter_child_nodes(node)
                        if not isinstance(child, ast.stmt)
                    )
                )
            )

    sensitive_state = tuple[
        dict[str, str],
        dict[str, dict[str, str | None]],
        set[str],
        set[str],
        set[str],
    ]
    max_states = 1024
    try_statement_types = (
        ast.Try,
        getattr(ast, "TryStar", ast.Try),
    )

    def clone_state(state: sensitive_state) -> sensitive_state:
        strings, mappings, environment_aliases, os_aliases, putenv_aliases = (
            state
        )
        return (
            dict(strings),
            {name: dict(value) for name, value in mappings.items()},
            set(environment_aliases),
            set(os_aliases),
            set(putenv_aliases),
        )

    def state_key(state: sensitive_state) -> tuple[object, ...]:
        strings, mappings, environment_aliases, os_aliases, putenv_aliases = (
            state
        )
        return (
            tuple(sorted(strings.items())),
            tuple(
                (
                    name,
                    tuple(sorted(value.items(), key=lambda item: item[0])),
                )
                for name, value in sorted(mappings.items())
            ),
            tuple(sorted(environment_aliases)),
            tuple(sorted(os_aliases)),
            tuple(sorted(putenv_aliases)),
        )

    def deduplicate_states(
        states: Iterable[sensitive_state],
    ) -> list[sensitive_state]:
        result: list[sensitive_state] = []
        observed: set[tuple[object, ...]] = set()
        for state in states:
            key = state_key(state)
            if key in observed:
                continue
            observed.add(key)
            result.append(state)
            if len(result) > max_states:
                result.pop()
                mark_limited("states")
                break
        return result

    def process_block(
        statements: Sequence[ast.stmt],
        initial_states: Iterable[sensitive_state],
    ) -> list[sensitive_state]:
        states = deduplicate_states(
            clone_state(state) for state in initial_states
        )
        for statement in statements:
            if analysis_limited or not step(len(states)):
                break
            next_states: list[sensitive_state] = []
            for state in states:
                next_states.extend(process_statement(statement, state))
            states = deduplicate_states(next_states)
        return states

    def process_statement(
        statement: ast.stmt,
        inherited_state: sensitive_state,
    ) -> list[sensitive_state]:
        (
            strings,
            mappings,
            environment_aliases,
            os_aliases,
            putenv_aliases,
        ) = clone_state(inherited_state)

        def current_state() -> sensitive_state:
            return (
                strings,
                mappings,
                environment_aliases,
                os_aliases,
                putenv_aliases,
            )

        def clear_binding(name: str) -> None:
            strings.pop(name, None)
            mappings.pop(name, None)
            environment_aliases.discard(name)
            os_aliases.discard(name)
            putenv_aliases.discard(name)

        def clear_target(target: ast.AST) -> None:
            if isinstance(target, ast.Name):
                clear_binding(target.id)
            elif isinstance(target, (ast.List, ast.Tuple)):
                for element in target.elts:
                    clear_target(element)

        def bind_name(name: str, value: ast.AST) -> None:
            clear_binding(name)
            if is_environment_expression(
                value,
                environment_aliases,
                os_aliases,
            ):
                environment_aliases.add(name)
                return
            if isinstance(value, ast.Name):
                if value.id in os_aliases:
                    os_aliases.add(name)
                    return
                if value.id in putenv_aliases:
                    putenv_aliases.add(name)
                    return
            string_value = _constant_string_expression(value, strings)
            if string_value is not None:
                if len(strings) + len(mappings) >= max_bindings:
                    mark_limited("bindings")
                    return
                strings[name] = string_value
                return
            mapping_value = mapping_expression(value, strings, mappings)
            if mapping_value is not None:
                if len(strings) + len(mappings) >= max_bindings:
                    mark_limited("bindings")
                    return
                mappings[name] = mapping_value

        for node in current_statement_nodes(statement):
            if isinstance(node, ast.Call):
                record_call(
                    node,
                    strings,
                    mappings,
                    environment_aliases,
                    os_aliases,
                    putenv_aliases,
                )
            elif isinstance(node, ast.Assign):
                value = _constant_string_expression(node.value, strings)
                for target in node.targets:
                    if (
                        isinstance(target, ast.Subscript)
                        and is_environment_expression(
                            target.value,
                            environment_aliases,
                            os_aliases,
                        )
                    ):
                        record(
                            _constant_string_expression(
                                target.slice,
                                strings,
                            ),
                            value,
                        )
            elif (
                isinstance(node, ast.AnnAssign)
                and node.value is not None
                and isinstance(node.target, ast.Subscript)
                and is_environment_expression(
                    node.target.value,
                    environment_aliases,
                    os_aliases,
                )
            ):
                record(
                    _constant_string_expression(
                        node.target.slice,
                        strings,
                    ),
                    _constant_string_expression(node.value, strings),
                )

        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            child_state = clone_state(current_state())
            (
                child_strings,
                child_mappings,
                child_environment_aliases,
                child_os_aliases,
                child_putenv_aliases,
            ) = child_state
            arguments = (
                *statement.args.posonlyargs,
                *statement.args.args,
                *statement.args.kwonlyargs,
            )
            for argument in arguments:
                child_strings.pop(argument.arg, None)
                child_mappings.pop(argument.arg, None)
                child_environment_aliases.discard(argument.arg)
                child_os_aliases.discard(argument.arg)
                child_putenv_aliases.discard(argument.arg)
            if statement.args.vararg is not None:
                name = statement.args.vararg.arg
                child_strings.pop(name, None)
                child_mappings.pop(name, None)
                child_environment_aliases.discard(name)
                child_os_aliases.discard(name)
                child_putenv_aliases.discard(name)
            if statement.args.kwarg is not None:
                name = statement.args.kwarg.arg
                child_strings.pop(name, None)
                child_mappings.pop(name, None)
                child_environment_aliases.discard(name)
                child_os_aliases.discard(name)
                child_putenv_aliases.discard(name)
            process_block(statement.body, (child_state,))
            clear_binding(statement.name)
            return [current_state()]

        if isinstance(statement, ast.ClassDef):
            process_block(statement.body, (clone_state(current_state()),))
            clear_binding(statement.name)
            return [current_state()]

        if isinstance(statement, ast.Import):
            for alias in statement.names:
                name = alias.asname or alias.name.split(".", 1)[0]
                clear_binding(name)
                if alias.name == "os":
                    os_aliases.add(name)
            return [current_state()]

        if isinstance(statement, ast.ImportFrom):
            for alias in statement.names:
                name = alias.asname or alias.name
                clear_binding(name)
                if statement.module == "os":
                    if alias.name == "environ":
                        environment_aliases.add(name)
                    elif alias.name == "putenv":
                        putenv_aliases.add(name)
            return [current_state()]

        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name):
                    bind_name(target.id, statement.value)
                elif isinstance(target, (ast.List, ast.Tuple)):
                    clear_target(target)
        elif isinstance(statement, ast.AnnAssign):
            if isinstance(statement.target, ast.Name):
                if statement.value is None:
                    clear_binding(statement.target.id)
                else:
                    bind_name(statement.target.id, statement.value)
            elif isinstance(statement.target, (ast.List, ast.Tuple)):
                clear_target(statement.target)
        elif isinstance(statement, ast.AugAssign):
            if (
                is_environment_expression(
                    statement.target,
                    environment_aliases,
                    os_aliases,
                )
                and isinstance(statement.op, ast.BitOr)
            ):
                updates = mapping_expression(
                    statement.value,
                    strings,
                    mappings,
                )
                if updates is not None:
                    for key, value in updates.items():
                        record(key, value)
            elif isinstance(statement.target, ast.Name):
                clear_binding(statement.target.id)
        elif isinstance(statement, ast.Delete):
            for target in statement.targets:
                clear_target(target)

        base_state = clone_state(current_state())
        if isinstance(statement, ast.If):
            outcomes = process_block(statement.body, (base_state,))
            outcomes.extend(
                process_block(statement.orelse, (base_state,))
                if statement.orelse
                else (base_state,)
            )
            return outcomes

        if isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
            iteration_state = clone_state(base_state)
            if isinstance(statement, (ast.For, ast.AsyncFor)):
                (
                    strings,
                    mappings,
                    environment_aliases,
                    os_aliases,
                    putenv_aliases,
                ) = iteration_state
                clear_target(statement.target)
                iteration_state = clone_state(current_state())
            outcomes = [base_state]
            outcomes.extend(process_block(statement.body, (iteration_state,)))
            if statement.orelse:
                outcomes = process_block(statement.orelse, outcomes)
            return outcomes

        if isinstance(statement, (ast.With, ast.AsyncWith)):
            body_state = clone_state(base_state)
            (
                strings,
                mappings,
                environment_aliases,
                os_aliases,
                putenv_aliases,
            ) = body_state
            for item in statement.items:
                if item.optional_vars is not None:
                    clear_target(item.optional_vars)
            return process_block(statement.body, (current_state(),))

        if isinstance(statement, try_statement_types):
            outcomes = process_block(statement.body, (base_state,))
            if statement.orelse:
                outcomes = process_block(statement.orelse, outcomes)
            for handler in statement.handlers:
                handler_state = clone_state(base_state)
                if handler.name:
                    (
                        strings,
                        mappings,
                        environment_aliases,
                        os_aliases,
                        putenv_aliases,
                    ) = handler_state
                    clear_binding(handler.name)
                    handler_state = clone_state(current_state())
                outcomes.extend(
                    process_block(handler.body, (handler_state,))
                )
            if statement.finalbody:
                outcomes = process_block(statement.finalbody, outcomes)
            return outcomes

        if isinstance(statement, ast.Match):
            outcomes = [base_state]
            for case in statement.cases:
                outcomes.extend(process_block(case.body, (base_state,)))
            return outcomes

        return [current_state()]

    try:
        process_block(
            tree.body,
            (({}, {}, {"environ"}, {"os"}, {"putenv"}),),
        )
    except RecursionError:
        mark_limited("recursion_depth")
    return (
        "\n".join(calls),
        (
            "python_sensitive_analysis_limit_exceeded:"
            + analysis_limit_reason
            if analysis_limited
            else ""
        ),
    )


def _constant_string_expression(
    node: ast.AST,
    bindings: Mapping[str, str] | None = None,
) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and bindings is not None:
        return bindings.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _constant_string_expression(node.left, bindings)
        right = _constant_string_expression(node.right, bindings)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
                continue
            if not isinstance(value, ast.FormattedValue):
                return None
            formatted = _constant_string_expression(value.value, bindings)
            if formatted is None or value.conversion not in {-1, 115}:
                return None
            parts.append(formatted)
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


@dataclass
class _ConfigurationExpansionBudget:
    substitutions: int = 0
    work: int = 0


_SHELL_VARIABLE_RE: Final[re.Pattern[str]] = re.compile(
    r"\$\{!([A-Za-z_][A-Za-z0-9_]*)\}|"
    r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|"
    r"([A-Za-z_][A-Za-z0-9_]*))"
)
_SHELL_NAME_RE: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*"
)
_SHELL_ASSIGNMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)=(.*)",
    re.DOTALL,
)
_SHELL_ASSIGNMENT_BUILTINS: Final[frozenset[str]] = frozenset(
    {"declare", "export", "local", "readonly", "typeset"}
)
_SEMANTIC_CONFIGURATION_KEYS: Final[frozenset[str]] = frozenset(
    {
        "base_url",
        "endpoint",
        "host",
        "llm_base_url",
        "llm_endpoint",
        "llm_host",
        "llm_model",
        "llm_openai_base_url",
        "llm_port",
        "llm_provider",
        "model",
        "model_provider",
        "openai_base_url",
        "port",
        "provider",
    }
)


def _expand_known_shell_variables(
    text: str,
    bindings: Mapping[str, str],
    budget: _ConfigurationExpansionBudget,
) -> str | None:
    if len(text) > MAX_CONFIGURATION_EXPANDED_CHARACTERS:
        return None
    expanded = text
    for _ in range(min(16, MAX_CONFIGURATION_DEPTH)):
        projected_length = len(expanded)
        projected_resolution_work = 0
        replacements: list[tuple[int, int, str]] = []

        for match in _SHELL_VARIABLE_RE.finditer(expanded):
            indirect_name = match.group(1)
            if indirect_name is not None:
                selected_name = bindings.get(indirect_name)
                projected_resolution_work += len(indirect_name)
                if selected_name is None:
                    continue
                projected_resolution_work += len(selected_name)
                if (
                    budget.work
                    + len(expanded)
                    + projected_resolution_work
                    > MAX_CONFIGURATION_EXPANSION_WORK
                ):
                    return None
                if (
                    len(selected_name)
                    > MAX_CONFIGURATION_EXPANDED_CHARACTERS
                    or _SHELL_NAME_RE.fullmatch(selected_name) is None
                ):
                    continue
                value = bindings.get(selected_name)
            else:
                name = match.group(2) or match.group(3)
                projected_resolution_work += len(name)
                if (
                    budget.work
                    + len(expanded)
                    + projected_resolution_work
                    > MAX_CONFIGURATION_EXPANSION_WORK
                ):
                    return None
                value = bindings.get(name)
            if value is None or value == match.group(0):
                continue
            projected_length += len(value) - len(match.group(0))
            replacements.append((match.start(), match.end(), value))
            if (
                projected_length > MAX_CONFIGURATION_EXPANDED_CHARACTERS
                or budget.substitutions + len(replacements)
                > MAX_CONFIGURATION_EXPANSION_SUBSTITUTIONS
            ):
                return None

        projected_work = (
            len(expanded)
            + projected_resolution_work
            + (projected_length if replacements else 0)
        )
        if budget.work + projected_work > MAX_CONFIGURATION_EXPANSION_WORK:
            return None
        budget.work += projected_work
        if not replacements:
            return expanded

        budget.substitutions += len(replacements)
        fragments: list[str] = []
        cursor = 0
        for start, end, value in replacements:
            fragments.append(expanded[cursor:start])
            fragments.append(value)
            cursor = end
        fragments.append(expanded[cursor:])
        updated = "".join(fragments)
        if updated == expanded:
            return updated
        expanded = updated
    return None


def _store_configuration_binding(
    bindings: dict[str, str],
    name: str,
    value: str,
    stored_character_count: int,
) -> int | None:
    if name not in bindings and len(bindings) >= MAX_CONFIGURATION_BINDINGS:
        return None
    previous_size = (
        len(name) + len(bindings[name])
        if name in bindings
        else 0
    )
    updated_character_count = (
        stored_character_count
        - previous_size
        + len(name)
        + len(value)
    )
    if updated_character_count > MAX_CONFIGURATION_STORED_CHARACTERS:
        return None
    bindings[name] = value
    return updated_character_count


def _normalized_configuration_key(name: str) -> str:
    return re.sub(
        r"[^a-z0-9]+",
        "_",
        name.strip().casefold(),
    ).strip("_")


def _bounded_shlex_tokens(
    text: str,
    *,
    punctuation_chars: str = "",
) -> tuple[tuple[str, ...], str]:
    try:
        lexer = shlex.shlex(
            text,
            posix=True,
            punctuation_chars=punctuation_chars,
        )
        lexer.whitespace_split = True
        lexer.commenters = "#"
        tokens: list[str] = []
        for token in lexer:
            if len(tokens) >= MAX_CONFIGURATION_NODES:
                return (), "limit"
            tokens.append(token)
    except ValueError:
        return (), "parse"
    return tuple(tokens), ""


def _shell_statement_tokens(
    raw_line: str,
) -> tuple[tuple[tuple[str, ...], ...], str]:
    tokens, failure = _bounded_shlex_tokens(
        raw_line,
        punctuation_chars=";",
    )
    if failure:
        return (), failure

    statements: list[tuple[str, ...]] = []
    current: list[str] = []
    for token in tokens:
        if token and set(token) == {";"}:
            if current:
                statements.append(tuple(current))
                current = []
            continue
        current.append(token)
    if current:
        statements.append(tuple(current))
    if len(statements) > MAX_CONFIGURATION_SNAPSHOTS:
        return (), "limit"
    return tuple(statements), ""


def _shell_assignment_token(token: str) -> tuple[str, str] | None:
    match = _SHELL_ASSIGNMENT_RE.fullmatch(token)
    if match is None:
        return None
    return match.group(1), match.group(2)


def _shell_statement_parts(
    tokens: Sequence[str],
) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    if not tokens:
        return (), ()
    if tokens[0] in _SHELL_ASSIGNMENT_BUILTINS:
        assignments: list[tuple[str, str]] = []
        for token in tokens[1:]:
            if token.startswith("-") and "=" not in token:
                continue
            assignment = _shell_assignment_token(token)
            if assignment is not None:
                assignments.append(assignment)
        return tuple(assignments), ()

    assignments = []
    command_index = 0
    for command_index, token in enumerate(tokens):
        assignment = _shell_assignment_token(token)
        if assignment is None:
            break
        assignments.append(assignment)
    else:
        return tuple(assignments), ()
    return tuple(assignments), tuple(tokens[command_index:])


def _shell_semantic_scan_text(
    text: str,
    initial_bindings: Mapping[str, str],
    budget: _ConfigurationExpansionBudget,
) -> tuple[str, str]:
    if len(initial_bindings) > MAX_CONFIGURATION_BINDINGS:
        return "", "limit"
    stored_character_count = sum(
        len(name) + len(value)
        for name, value in initial_bindings.items()
    )
    if stored_character_count > MAX_CONFIGURATION_STORED_CHARACTERS:
        return "", "limit"
    bindings = dict(initial_bindings)
    expanded_lines: list[str] = []
    expanded_character_count = 0
    statement_count = 0
    for raw_line in _shell_continuation_text(text).splitlines():
        statements, token_failure = _shell_statement_tokens(raw_line)
        if token_failure == "limit":
            return "", "limit"
        if token_failure:
            if re.search(
                r"(?i)(?:myproxy|provider|model|base[_-]?url|endpoint)",
                raw_line,
            ):
                return "", "parse"
            continue
        statement_count += len(statements)
        if statement_count > MAX_CONFIGURATION_NODES:
            return "", "limit"
        for statement in statements:
            assignments, command_tokens = _shell_statement_parts(statement)
            for name, raw_value in assignments:
                expanded_value = _expand_known_shell_variables(
                    raw_value,
                    bindings,
                    budget,
                )
                if expanded_value is None:
                    return "", "limit"
                updated_character_count = _store_configuration_binding(
                    bindings,
                    name,
                    expanded_value,
                    stored_character_count,
                )
                if updated_character_count is None:
                    return "", "limit"
                stored_character_count = updated_character_count
            if not command_tokens:
                continue
            command_text = " ".join(command_tokens)
            expanded = _expand_known_shell_variables(
                command_text,
                bindings,
                budget,
            )
            if expanded is None:
                return "", "limit"
            if expanded == command_text:
                continue
            if len(expanded_lines) >= MAX_CONFIGURATION_SNAPSHOTS:
                return "", "limit"
            projected_character_count = (
                expanded_character_count + len(expanded) + 1
            )
            if projected_character_count > MAX_CONFIGURATION_BYTES:
                return "", "limit"
            expanded_lines.append(expanded)
            expanded_character_count = projected_character_count
    return "\n".join(expanded_lines), ""


def _shell_expanded_scan_text(path: str, text: str) -> tuple[str, str]:
    if PurePosixPath(path).suffix.lower() not in {
        ".bash",
        ".command",
        ".sh",
        ".zsh",
    }:
        return "", ""
    expanded, failure = _shell_semantic_scan_text(
        text,
        {},
        _ConfigurationExpansionBudget(),
    )
    if failure == "limit":
        return "", "shell_configuration_limit_exceeded"
    if failure:
        return "", "shell_configuration_parse_failed"
    return expanded, ""


_DOCKER_RUN_OPTION_RE: Final[re.Pattern[str]] = re.compile(
    r"--[A-Za-z][A-Za-z0-9-]*(?:=[^ \t]+)?[ \t]+"
)
_SHELL_EXECUTABLE_NAMES: Final[frozenset[str]] = frozenset(
    {"ash", "bash", "dash", "ksh", "sh", "zsh"}
)


def _docker_run_command(body: str) -> tuple[str, str]:
    position = len(body) - len(body.lstrip(" \t"))
    option_count = 0
    while True:
        match = _DOCKER_RUN_OPTION_RE.match(body, position)
        if match is None:
            break
        option_count += 1
        if option_count > MAX_CONFIGURATION_NODES:
            return "", "limit"
        position = match.end()
    return body[position:], ""


def _shell_command_script(tokens: Sequence[str]) -> str:
    command_index = 0
    while command_index < len(tokens):
        assignment = _shell_assignment_token(tokens[command_index])
        if assignment is None:
            break
        command_index += 1
    if (
        command_index < len(tokens)
        and tokens[command_index].casefold() == "exec"
    ):
        command_index += 1
    if (
        command_index < len(tokens)
        and PurePosixPath(tokens[command_index]).name.casefold() == "env"
    ):
        command_index += 1
        while command_index < len(tokens):
            token = tokens[command_index]
            if token.startswith("-"):
                command_index += 1
                continue
            if _shell_assignment_token(token) is not None:
                command_index += 1
                continue
            break
    if command_index >= len(tokens):
        return ""
    executable = PurePosixPath(tokens[command_index]).name.casefold()
    if executable not in _SHELL_EXECUTABLE_NAMES:
        return ""
    for index in range(command_index + 1, len(tokens) - 1):
        option = tokens[index]
        if option == "-c" or (
            option.startswith("-")
            and not option.startswith("--")
            and "c" in option[1:]
        ):
            return tokens[index + 1]
    return ""


def _dockerfile_semantic_scan_text(text: str) -> tuple[str, str]:
    global_args: dict[str, str] = {}
    bindings: dict[str, str] = {}
    budget = _ConfigurationExpansionBudget()
    snapshots: list[dict[str, str]] = []
    run_snapshots: list[str] = []
    stages: dict[str, dict[str, str]] = {}
    snapshot_character_count = 0
    snapshot_node_count = 0
    retained_binding_count = 0
    retained_character_count = 0
    current_stage_name = ""
    stage_started = False
    instruction_count = 0

    def binding_characters(values: Mapping[str, str]) -> int:
        return sum(len(name) + len(value) for name, value in values.items())

    def release_bindings(values: Mapping[str, str]) -> None:
        nonlocal retained_binding_count, retained_character_count
        retained_binding_count -= len(values)
        retained_character_count -= binding_characters(values)

    def retain_copy(
        values: Mapping[str, str],
    ) -> dict[str, str] | None:
        nonlocal retained_binding_count, retained_character_count
        added_characters = binding_characters(values)
        if (
            retained_binding_count + len(values)
            > MAX_CONFIGURATION_BINDINGS
            or retained_character_count + added_characters
            > MAX_CONFIGURATION_STORED_CHARACTERS
        ):
            return None
        retained_binding_count += len(values)
        retained_character_count += added_characters
        return dict(values)

    def store_binding(
        values: dict[str, str],
        name: str,
        value: str,
    ) -> bool:
        nonlocal retained_binding_count, retained_character_count
        previous = values.get(name)
        added_bindings = 0 if previous is not None else 1
        previous_characters = (
            len(name) + len(previous)
            if previous is not None
            else 0
        )
        updated_characters = len(name) + len(value)
        if (
            retained_binding_count + added_bindings
            > MAX_CONFIGURATION_BINDINGS
            or retained_character_count
            - previous_characters
            + updated_characters
            > MAX_CONFIGURATION_STORED_CHARACTERS
        ):
            return False
        retained_binding_count += added_bindings
        retained_character_count = (
            retained_character_count
            - previous_characters
            + updated_characters
        )
        values[name] = value
        return True

    def capture_snapshot(values: Mapping[str, str]) -> bool:
        nonlocal snapshot_character_count, snapshot_node_count
        snapshot = {
            name: value
            for name, value in values.items()
            if _normalized_configuration_key(name)
            in _SEMANTIC_CONFIGURATION_KEYS
        }
        if not snapshot or (snapshots and snapshots[-1] == snapshot):
            return True
        snapshot_characters = sum(
            len(name) + len(value)
            for name, value in snapshot.items()
        )
        if (
            len(snapshots) + len(run_snapshots)
            >= MAX_CONFIGURATION_SNAPSHOTS
            or snapshot_node_count + len(snapshot) + 1
            > MAX_CONFIGURATION_NODES
            or snapshot_character_count + snapshot_characters
            > MAX_CONFIGURATION_BYTES
        ):
            return False
        snapshots.append(snapshot)
        snapshot_node_count += len(snapshot) + 1
        snapshot_character_count += snapshot_characters
        return True

    def capture_run_snapshot(command: str) -> bool:
        nonlocal snapshot_character_count, snapshot_node_count
        if run_snapshots and run_snapshots[-1] == command:
            return True
        command_characters = len(command) + 1
        if (
            len(snapshots) + len(run_snapshots)
            >= MAX_CONFIGURATION_SNAPSHOTS
            or snapshot_node_count + 1 > MAX_CONFIGURATION_NODES
            or snapshot_character_count + command_characters
            > MAX_CONFIGURATION_BYTES
        ):
            return False
        run_snapshots.append(command)
        snapshot_node_count += 1
        snapshot_character_count += command_characters
        return True

    def finalize_stage() -> None:
        nonlocal bindings
        if current_stage_name:
            previous = stages.get(current_stage_name)
            if previous is not None and previous is not bindings:
                release_bindings(previous)
            stages[current_stage_name] = bindings
        else:
            release_bindings(bindings)
        bindings = {}

    for raw_line in _dockerfile_logical_text("Dockerfile", text).splitlines():
        directive = re.match(
            r"^[ \t]*([A-Za-z]+)(?:[ \t]+(.+?))?[ \t]*$",
            raw_line,
        )
        if directive is None:
            continue
        instruction = directive.group(1).casefold()
        body = directive.group(2) or ""
        instruction_count += 1
        if instruction_count > MAX_CONFIGURATION_NODES:
            return "", "docker_configuration_limit_exceeded"

        if instruction == "from":
            tokens, token_failure = _bounded_shlex_tokens(body)
            if token_failure == "limit":
                return "", "docker_configuration_limit_exceeded"
            if token_failure:
                return "", "docker_configuration_parse_failed"
            base_index = 0
            while (
                base_index < len(tokens)
                and tokens[base_index].startswith("--")
            ):
                base_index += 1
            if base_index >= len(tokens):
                return "", "docker_configuration_parse_failed"
            expanded_base = _expand_known_shell_variables(
                tokens[base_index],
                global_args,
                budget,
            )
            if expanded_base is None:
                return "", "docker_configuration_limit_exceeded"
            alias = ""
            if (
                base_index + 2 < len(tokens)
                and tokens[base_index + 1].casefold() == "as"
                and re.fullmatch(
                    r"[A-Za-z0-9_.-]+",
                    tokens[base_index + 2],
                )
                is not None
            ):
                alias = tokens[base_index + 2].casefold()
            if stage_started:
                finalize_stage()
            stage_started = True
            current_stage_name = alias
            inherited = stages.get(expanded_base.casefold())
            if inherited is not None:
                copied = retain_copy(inherited)
                if copied is None:
                    return "", "docker_configuration_limit_exceeded"
                bindings = copied
            continue

        if instruction == "run":
            if not stage_started or not body:
                continue
            command, command_failure = _docker_run_command(body)
            if command_failure:
                return "", "docker_configuration_limit_exceeded"
            if not command:
                return "", "docker_configuration_parse_failed"

            parsed_command: object | None = None
            if command.startswith("["):
                try:
                    parsed_command = json.loads(command)
                except json.JSONDecodeError:
                    parsed_command = None
            if parsed_command is not None:
                if (
                    not isinstance(parsed_command, list)
                    or len(parsed_command) > MAX_CONFIGURATION_NODES
                    or not all(
                        isinstance(item, str)
                        for item in parsed_command
                    )
                ):
                    return "", "docker_configuration_parse_failed"
                nested_script = _shell_command_script(parsed_command)
                if not nested_script:
                    continue
                command = nested_script

            expanded, shell_failure = _shell_semantic_scan_text(
                command,
                bindings,
                budget,
            )
            if shell_failure == "limit":
                return "", "docker_configuration_limit_exceeded"
            if shell_failure:
                return "", "docker_configuration_parse_failed"
            for expanded_line in expanded.splitlines():
                if not capture_run_snapshot(expanded_line):
                    return "", "docker_configuration_limit_exceeded"

            tokens, token_failure = _bounded_shlex_tokens(command)
            if token_failure == "limit":
                return "", "docker_configuration_limit_exceeded"
            if token_failure:
                if re.search(
                    r"(?i)(?:myproxy|provider|model|base[_-]?url|endpoint)",
                    command,
                ):
                    return "", "docker_configuration_parse_failed"
                continue
            nested_script = _shell_command_script(tokens)
            if nested_script and nested_script != command:
                nested_expanded, nested_failure = (
                    _shell_semantic_scan_text(
                        nested_script,
                        bindings,
                        budget,
                    )
                )
                if nested_failure == "limit":
                    return "", "docker_configuration_limit_exceeded"
                if nested_failure:
                    return "", "docker_configuration_parse_failed"
                for expanded_line in nested_expanded.splitlines():
                    if not capture_run_snapshot(expanded_line):
                        return "", "docker_configuration_limit_exceeded"
            continue

        if instruction not in {"arg", "env"} or not body:
            continue
        tokens, token_failure = _bounded_shlex_tokens(body)
        if token_failure == "limit":
            return "", "docker_configuration_limit_exceeded"
        if token_failure:
            if re.search(
                r"(?i)(?:myproxy|provider|model|base[_-]?url|endpoint)",
                body,
            ):
                return "", "docker_configuration_parse_failed"
            continue
        assignments: list[tuple[str, str | None]] = []
        if tokens and all("=" in token for token in tokens):
            assignments.extend(
                token.split("=", 1) for token in tokens
            )
        elif (
            instruction == "env"
            and len(tokens) >= 2
            and "=" not in tokens[0]
        ):
            assignments.append((tokens[0], " ".join(tokens[1:])))
        elif tokens and "=" in tokens[0]:
            assignments.append(tokens[0].split("=", 1))
        elif instruction == "arg" and len(tokens) == 1:
            assignments.append((tokens[0], None))

        destination = (
            global_args
            if instruction == "arg" and not stage_started
            else bindings
        )
        expanded_assignments: list[tuple[str, str]] = []
        for name, raw_value in assignments:
            if _SHELL_NAME_RE.fullmatch(name) is None:
                continue
            if raw_value is None:
                if name in destination:
                    continue
                inherited_value = global_args.get(name)
                if inherited_value is None:
                    continue
                expanded_assignments.append((name, inherited_value))
                continue
            expanded = _expand_known_shell_variables(
                raw_value,
                destination,
                budget,
            )
            if expanded is None:
                return "", "docker_configuration_limit_exceeded"
            expanded_assignments.append((name, expanded))

        for name, expanded in expanded_assignments:
            if not store_binding(destination, name, expanded):
                return "", "docker_configuration_limit_exceeded"
            if (
                _normalized_configuration_key(name)
                in _SEMANTIC_CONFIGURATION_KEYS
                and not capture_snapshot(destination)
            ):
                return "", "docker_configuration_limit_exceeded"
    try:
        semantic_text = _semantic_mapping_scan_text(tuple(snapshots))
        fragments = [semantic_text] if semantic_text else []
        fragments.extend(run_snapshots)
        projected_characters = sum(len(item) for item in fragments)
        projected_characters += max(0, len(fragments) - 1)
        if projected_characters > MAX_CONFIGURATION_BYTES:
            return "", "docker_configuration_limit_exceeded"
        return "\n".join(fragments), ""
    except (RecursionError, TypeError, ValueError):
        return "", "docker_configuration_limit_exceeded"


def _ini_semantic_scan_text(text: str) -> tuple[str, str]:
    def parse(candidate: str) -> configparser.ConfigParser:
        parser = configparser.ConfigParser(
            interpolation=None,
            strict=True,
            empty_lines_in_values=False,
        )
        parser.optionxform = str
        parser.read_string(candidate)
        return parser

    try:
        try:
            parser = parse(text)
        except configparser.MissingSectionHeaderError:
            parser = parse("[__root__]\n" + text)
        values: list[Mapping[str, object]] = []
        if parser.defaults():
            values.append(dict(parser.defaults()))
        values.extend(
            dict(parser.items(section, raw=True))
            for section in parser.sections()
        )
        return _semantic_mapping_scan_text(values), ""
    except (
        RecursionError,
        TypeError,
        ValueError,
        configparser.Error,
    ):
        return "", "ini_parse_failed"


def _configuration_scan_texts(
    path: str,
    text: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    suffix = PurePosixPath(path).suffix.lower()
    variants = [text]
    failures: list[str] = []
    structured_suffixes = {".ini", ".json", ".toml", ".yaml", ".yml"}
    semantic_path = (
        suffix in structured_suffixes
        or suffix in {".bash", ".command", ".sh", ".zsh"}
        or _is_dockerfile_path(path)
    )
    if (
        semantic_path
        and len(text.encode("utf-8")) > MAX_CONFIGURATION_BYTES
    ):
        return (text,), ("configuration_limit_exceeded",)
    if _is_dockerfile_path(path):
        semantic_text, failure = _dockerfile_semantic_scan_text(text)
        if failure:
            failures.append(failure)
        elif semantic_text and semantic_text not in variants:
            variants.append(semantic_text)
    shell_text, shell_failure = _shell_expanded_scan_text(path, text)
    if shell_failure:
        failures.append(shell_failure)
    elif shell_text and shell_text not in variants:
        variants.append(shell_text)
    if suffix not in structured_suffixes:
        return tuple(variants), tuple(failures)
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
    elif suffix == ".toml":
        semantic_text, failure = _toml_semantic_scan_text(text)
        if failure:
            failures.append(failure)
        elif semantic_text and semantic_text not in variants:
            variants.append(semantic_text)
    else:
        semantic_text, failure = _ini_semantic_scan_text(text)
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

    def visit(
        value: object,
        depth: int,
        inherited_myproxy: bool = False,
    ) -> None:
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
                normalized_entries = {
                    re.sub(
                        r"[^a-z0-9]+",
                        "_",
                        key.strip().casefold(),
                    ).strip("_"): item
                    for key, item in scalar_entries.items()
                }
                provider_values = [
                    normalized_entries[key]
                    for key in (
                        "provider",
                        "llm_provider",
                        "model_provider",
                    )
                    if key in normalized_entries
                ]
                provider = next(
                    (
                        value
                        for value in provider_values
                        if re.fullmatch(
                            _MYPROXY_PROVIDER_PATTERN,
                            value.strip(),
                            re.IGNORECASE,
                        )
                        is not None
                    ),
                    provider_values[0] if provider_values else "",
                )
                provider_declared = bool(provider_values)
                local_myproxy = any(
                    re.fullmatch(
                        _MYPROXY_PROVIDER_PATTERN,
                        value.strip(),
                        re.IGNORECASE,
                    )
                    is not None
                    for value in provider_values
                )
                myproxy_context = (
                    local_myproxy
                    if provider_declared
                    else inherited_myproxy
                )
                if myproxy_context:
                    command = [
                        "commander",
                        "--provider",
                        json.dumps(provider or "myproxy"),
                    ]
                    endpoint = (
                        normalized_entries.get("openai_base_url")
                        or normalized_entries.get("base_url")
                        or normalized_entries.get("llm_base_url")
                        or normalized_entries.get("llm_openai_base_url")
                        or normalized_entries.get("endpoint")
                        or normalized_entries.get("llm_endpoint")
                    )
                    model = (
                        normalized_entries.get("model")
                        or normalized_entries.get("llm_model")
                    )
                    if endpoint is not None:
                        command.extend(("--base-url", json.dumps(endpoint)))
                    if model is not None:
                        command.extend(("--model", json.dumps(model)))
                    if endpoint is not None or model is not None:
                        fragments.append(" ".join(command))
                    for keys, canonical in (
                        (("host", "llm_host"), "VOI_MYPROXY_HOST"),
                        (("port", "llm_port"), "VOI_MYPROXY_PORT"),
                    ):
                        configured_value = next(
                            (
                                normalized_entries[key]
                                for key in keys
                                if key in normalized_entries
                            ),
                            None,
                        )
                        if configured_value is not None:
                            fragments.append(
                                f"{canonical}={json.dumps(configured_value)}"
                            )
            else:
                myproxy_context = inherited_myproxy
            for nested in value.values():
                visit(nested, depth + 1, myproxy_context)
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
                visit(nested, depth + 1, inherited_myproxy)

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
    *,
    base_commit: str,
) -> dict[str, object]:
    """Scan tracked files, the review diff, archives, and generated reports."""

    findings: list[dict[str, object]] = []
    input_manifest: list[dict[str, object]] = []
    input_paths: set[str] = set()
    finding_limit_reached = False

    def merge_findings(
        candidates: Iterable[Mapping[str, object]],
        *,
        path: str,
    ) -> None:
        nonlocal finding_limit_reached
        if finding_limit_reached:
            return
        for raw_finding in candidates:
            finding = dict(raw_finding)
            if len(findings) >= MAX_SCAN_FINDINGS - 1:
                limit_path = finding.get("path")
                findings.append(
                    _path_finding(
                        (
                            str(limit_path)
                            if isinstance(limit_path, str) and limit_path
                            else path
                        ),
                        "scan_finding_limit_exceeded",
                    )
                )
                finding_limit_reached = True
                return
            findings.append(finding)
            if finding.get("rule_id") == "scan_finding_limit_exceeded":
                finding_limit_reached = True
                return

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
        if not finding_limit_reached:
            merge_findings(
                scan_payload(
                    normalized_path,
                    payload,
                    allow_safe_fixtures=allow_safe_fixtures,
                ),
                path=normalized_path,
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
    review_range = _review_range_evidence(repository_root, base_commit)
    diff = _git_output(
        repository_root,
        [
            "diff",
            "--no-ext-diff",
            "--binary",
            f"{review_range['base_commit']}...{review_range['head_commit']}",
            "--",
        ],
    )
    added_diff = _added_diff_payload(diff)
    scan_input(
        "diff",
        "<git-diff>",
        added_diff,
        allow_safe_fixtures=False,
    )
    input_manifest[-1].update(review_range)
    for snapshot in snapshots:
        for entry, payload in snapshot.files.items():
            scan_input(
                snapshot.kind,
                f"{snapshot.kind}/{entry}",
                payload,
                allow_safe_fixtures=False,
            )
        for entry, payload in snapshot.metadata.items():
            scan_input(
                snapshot.kind,
                f"{snapshot.kind}/{entry}",
                payload,
                allow_safe_fixtures=False,
            )
        for raw_entry in snapshot.prescanned_inputs:
            entry = _mapping(raw_entry)
            path = entry.get("path")
            size = entry.get("size")
            digest = entry.get("sha256")
            if (
                not isinstance(path, str)
                or path in input_paths
                or type(size) is not int
                or size < 0
                or not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            ):
                raise RuntimeError("Invalid prescanned archive evidence")
            input_paths.add(path)
            input_manifest.append(
                {
                    "kind": snapshot.kind,
                    "path": path,
                    "size": size,
                    "sha256": digest,
                }
            )
        merge_findings(
            snapshot.prescanned_findings,
            path=f"{snapshot.kind}/<archive-prescan>",
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
    base_commit: str,
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
        review_range = _review_range_evidence(
            repository_root,
            base_commit,
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
                    **review_range,
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
        installed_metadata_raw = _mapping(
            install_smoke.get("payload")
        ).get("installed_metadata")
        if not isinstance(installed_metadata_raw, str):
            installed_metadata_raw = ""
        report = {
            "schema_version": DISTRIBUTION_COMPLIANCE_SCHEMA_VERSION,
            "repository": {
                "before": repository_before,
                "after": repository_after,
                **review_range,
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
                "installed_raw": installed_metadata_raw,
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
            base_commit=review_range["base_commit"],
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
    review_range = {
        key: repository.get(key)
        for key in ("base_commit", "head_commit", "merge_base")
    }
    if (
        any(
            not isinstance(value, str)
            or re.fullmatch(r"[0-9a-f]{40}", value) is None
            for value in review_range.values()
        )
        or review_range["head_commit"] != before_state.get("head")
        or review_range["head_commit"] != after_state.get("head")
    ):
        blockers.append(
            {
                "code": "invalid_review_range_evidence",
                **review_range,
            }
        )
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
                "metadata_manifest",
                "metadata_sizes",
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
        metadata_manifest = _sha256_manifest(
            artifact.get("metadata_manifest"),
            allow_empty=True,
        )
        metadata_sizes = _size_manifest(
            artifact.get("metadata_sizes"),
            allow_empty=True,
        )
        directory_entries = artifact.get("directory_entries")
        if (
            not isinstance(filename, str)
            or not filename
            or file_manifest is None
            or file_sizes is None
            or metadata_manifest is None
            or metadata_sizes is None
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
        if set(metadata_sizes) != set(metadata_manifest):
            blockers.append(
                {
                    "code": "artifact_metadata_size_manifest_mismatch",
                    "kind": kind,
                }
            )
        if set(file_manifest) & set(metadata_manifest):
            blockers.append(
                {
                    "code": "artifact_metadata_path_collision",
                    "kind": kind,
                }
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
    source_pyproject = _mapping(report.get("source_pyproject"))
    source_pyproject_raw = source_pyproject.get("raw")
    if (
        not isinstance(source_pyproject_raw, str)
        or not _is_expected_build_system(source_pyproject_raw)
    ):
        blockers.append({"code": "invalid_build_system_configuration"})
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
        installed_metadata = payload.get("installed_metadata")
        archive_metadata = _mapping(report.get("metadata")).get("raw")
        if not isinstance(installed_metadata, str) or not installed_metadata:
            blockers.append({"code": "installed_metadata_missing"})
        elif installed_metadata != archive_metadata:
            blockers.append({"code": "installed_metadata_mismatch"})
        if payload.get("runtime_data_loaded") is not True:
            blockers.append({"code": "installed_runtime_data_failed"})
        if payload.get("target_runtime_data_loaded") is not True:
            blockers.append({"code": "target_install_runtime_data_failed"})
        if payload.get("packaged_defaults_loaded") is not True:
            blockers.append({"code": "installed_packaged_defaults_failed"})
        if payload.get("source_repository_root_is_none") is not True:
            blockers.append({"code": "installed_source_root_not_isolated"})
        if payload.get("target_packaged_defaults_loaded") is not True:
            blockers.append({"code": "target_install_packaged_defaults_failed"})
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
            elif kind == "diff":
                size = entry.get("size")
                digest = entry.get("sha256")
                invalid_identity = (
                    set(entry)
                    != {
                        "kind",
                        "path",
                        "size",
                        "sha256",
                        "base_commit",
                        "head_commit",
                        "merge_base",
                    }
                    or type(size) is not int
                    or size < 0
                    or not isinstance(digest, str)
                    or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                    or any(
                        entry.get(key) != review_range.get(key)
                        for key in (
                            "base_commit",
                            "head_commit",
                            "merge_base",
                        )
                    )
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
            and type(diff_entry.get("size")) is int
            and int(diff_entry.get("size", -1)) >= 0
            and isinstance(diff_entry.get("sha256"), str)
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
            metadata_manifest = _mapping(artifact.get("metadata_manifest"))
            metadata_sizes = _mapping(artifact.get("metadata_sizes"))
            expected_paths = {
                f"{kind}/{entry}"
                for entry in (*file_manifest, *metadata_manifest)
            }
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
            for metadata_entry, digest in metadata_manifest.items():
                entry = manifest_by_path.get(
                    f"{kind}/{metadata_entry}",
                    {},
                )
                if (
                    entry.get("kind") != kind
                    or entry.get("sha256") != digest
                    or entry.get("size")
                    != metadata_sizes.get(metadata_entry)
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
        and len(findings) <= MAX_SCAN_FINDINGS
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
    *,
    public_report: Mapping[str, object] | None = None,
) -> None:
    """Write the canonical report and reviewer evidence files."""

    directory_fd = _prepare_distribution_evidence_directory(output_dir)
    try:
        public_report = public_report or _external_distribution_report(report)
        _write_evidence_bytes(
            directory_fd,
            "distribution-compliance.json",
            canonical_json_text(public_report).encode("utf-8"),
        )
        _write_evidence_bytes(
            directory_fd,
            "distribution-compliance.md",
            render_distribution_markdown(public_report).encode("utf-8"),
        )
        public_secret_scan = _mapping(public_report.get("secret_scan"))
        _write_evidence_bytes(
            directory_fd,
            "secret-scan.json",
            canonical_json_text(public_secret_scan).encode("utf-8"),
        )
        if public_report is not report:
            return
        artifacts = _mapping(public_report.get("artifacts"))
        for kind in ("wheel", "sdist"):
            entries = _string_list(_mapping(artifacts.get(kind)).get("entries"))
            _write_evidence_bytes(
                directory_fd,
                f"{kind}.entries.txt",
                "".join(f"{entry}\n" for entry in entries).encode("utf-8"),
            )
        metadata_text = str(
            _mapping(public_report.get("metadata")).get(
                "installed_raw",
                "",
            )
        )
        _write_evidence_bytes(
            directory_fd,
            "installed.METADATA",
            metadata_text.encode("utf-8"),
        )
        _write_evidence_bytes(
            directory_fd,
            "dependency-notices.json",
            canonical_json_text(
                _mapping(public_report.get("dependencies"))
            ).encode("utf-8"),
        )
        for name, payload in _distribution_report_scan_payloads(
            public_report
        ).items():
            _write_evidence_bytes(directory_fd, name, payload)
    finally:
        os.close(directory_fd)


def _write_evidence_bytes(
    directory_fd: int,
    filename: str,
    payload: bytes,
) -> None:
    if (
        not filename
        or filename != PurePosixPath(filename).name
        or "/" in filename
        or "\\" in filename
    ):
        raise RuntimeError("invalid distribution evidence filename")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise RuntimeError("secure evidence writes require O_NOFOLLOW")
    flags |= nofollow
    try:
        file_fd = os.open(
            filename,
            flags,
            0o600,
            dir_fd=directory_fd,
        )
    except OSError as error:
        raise RuntimeError(
            f"failed to create distribution evidence file: {filename}"
        ) from error
    try:
        with os.fdopen(file_fd, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
    finally:
        os.close(file_fd)


def _prepare_distribution_evidence_directory(output_dir: Path) -> int:
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        pass
    directory_flags = os.O_RDONLY
    directory_flags |= getattr(os, "O_CLOEXEC", 0)
    directory_flags |= getattr(os, "O_DIRECTORY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow or not getattr(os, "O_DIRECTORY", 0):
        raise RuntimeError(
            "secure evidence writes require O_DIRECTORY and O_NOFOLLOW"
        )
    directory_flags |= nofollow
    try:
        directory_fd = os.open(output_dir, directory_flags)
    except OSError as error:
        raise RuntimeError(
            "distribution evidence output must be a real directory"
        ) from error
    try:
        output_stat = os.fstat(directory_fd)
        if not stat.S_ISDIR(output_stat.st_mode):
            raise RuntimeError(
                "distribution evidence output must be a real directory"
            )
        if os.listdir(directory_fd):
            raise RuntimeError(
                "distribution evidence output directory must be empty"
            )
    except Exception:
        os.close(directory_fd)
        raise
    return directory_fd


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
                "from pathlib import Path\n"
                "from importlib.metadata import distribution\n"
                "from starcraft_commander import micromachine_build_identity "
                "as build_identity\n"
                "from starcraft_commander.micromachine_map_pool import "
                "load_micromachine_map_pool\n"
                "from starcraft_commander.micromachine_pre_live_journeys import "
                "load_pre_live_journey_manifest\n"
                "from starcraft_commander.runtime_data import "
                "micromachine_data_path, micromachine_data_root, "
                "source_repository_root\n"
                "pool = load_micromachine_map_pool()\n"
                "journeys = load_pre_live_journey_manifest()\n"
                "required = ['HOOK_MANIFEST.json', 'PRE_LIVE_PRODUCERS.json']\n"
                "loaded = bool(pool.maps) and bool(journeys.get('journeys')) and "
                "all(micromachine_data_path(name).is_file() for name in required)\n"
                "root = micromachine_data_root().resolve()\n"
                "def packaged_file(path):\n"
                "    candidate = Path(path).resolve()\n"
                "    return candidate.is_file() and root in candidate.parents\n"
                "patch_defaults = [value for name, value in "
                "vars(build_identity).items() if name.startswith('DEFAULT_') "
                "and name.endswith('_PATCH')]\n"
                "default_assets = [build_identity.DEFAULT_HOOK_MANIFEST, "
                "build_identity.DEFAULT_MAP_POOL, "
                "build_identity.DEFAULT_BLACKBOARD_HEADER, *patch_defaults]\n"
                "scripts = ['build_macos_local.sh', 'probe_macos_local.sh', "
                "'smoke_macos_local.sh', 'soak_macos_local.sh', "
                "'soak_matrix_macos_local.sh', "
                "'strategy_matrix_macos_local.sh']\n"
                "packaged_defaults = bool(patch_defaults) and all("
                "packaged_file(path) for path in default_assets) and all("
                "packaged_file(root / 'scripts' / name) for name in scripts)\n"
                "source_isolated = source_repository_root() is None and "
                "build_identity.SOURCE_REPOSITORY_ROOT is None and "
                "build_identity.REPO_ROOT == root.parents[1]\n"
                "installed_distribution = distribution('voiStarcraft2')\n"
                "installed_metadata = "
                "installed_distribution.read_text('METADATA') or ''\n"
                "print(json.dumps({'license_expression': "
                "installed_distribution.metadata.get('License-Expression'), "
                "'installed_metadata': installed_metadata, "
                "'runtime_data_loaded': loaded, "
                "'packaged_defaults_loaded': packaged_defaults, "
                "'source_repository_root_is_none': source_isolated}, "
                "sort_keys=True))\n"
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
                    "from starcraft_commander import "
                    "micromachine_build_identity as build_identity\n"
                    "from starcraft_commander.runtime_data import "
                    "micromachine_data_path, micromachine_data_root, "
                    "source_repository_root\n"
                    "required = ['HOOK_MANIFEST.json', "
                    "'MICROMACHINE_MAP_POOL.json', 'PRE_LIVE_JOURNEYS.json', "
                    "'PRE_LIVE_PRODUCERS.json']\n"
                    "root = micromachine_data_root().resolve()\n"
                    "loaded = all(micromachine_data_path(name).is_file() "
                    "for name in required) and target in root.parents\n"
                    "def packaged_file(path):\n"
                    "    candidate = Path(path).resolve()\n"
                    "    return candidate.is_file() and "
                    "root in candidate.parents\n"
                    "patch_defaults = [value for name, value in "
                    "vars(build_identity).items() if name.startswith('DEFAULT_') "
                    "and name.endswith('_PATCH')]\n"
                    "default_assets = [build_identity.DEFAULT_HOOK_MANIFEST, "
                    "build_identity.DEFAULT_MAP_POOL, "
                    "build_identity.DEFAULT_BLACKBOARD_HEADER, "
                    "*patch_defaults]\n"
                    "scripts = ['build_macos_local.sh', "
                    "'probe_macos_local.sh', 'smoke_macos_local.sh', "
                    "'soak_macos_local.sh', "
                    "'soak_matrix_macos_local.sh', "
                    "'strategy_matrix_macos_local.sh']\n"
                    "defaults = bool(patch_defaults) and all("
                    "packaged_file(path) for path in default_assets) and all("
                    "packaged_file(root / 'scripts' / name) "
                    "for name in scripts) and "
                    "source_repository_root() is None and "
                    "build_identity.SOURCE_REPOSITORY_ROOT is None and "
                    "build_identity.REPO_ROOT == root.parents[1]\n"
                    "print(json.dumps({'loaded': loaded, "
                    "'defaults': defaults}, sort_keys=True))\n"
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
                normalized_payload["target_packaged_defaults_loaded"] = bool(
                    target_result.returncode == 0
                    and isinstance(target_payload, Mapping)
                    and target_payload.get("defaults") is True
                )
            else:
                normalized_payload["target_runtime_data_loaded"] = False
                normalized_payload["target_packaged_defaults_loaded"] = False
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

    build_system = _build_system_configuration(text)
    if build_system is None:
        return ()
    requires = build_system.get("requires")
    if not isinstance(requires, list) or any(
        not isinstance(requirement, str) for requirement in requires
    ):
        return ()
    return tuple(
        sorted(
            requirement
            for requirement in requires
            if normalized_dependency_name(requirement)
        )
    )


def _build_system_configuration(
    text: str,
) -> Mapping[str, object] | None:
    if _toml is None:
        return None
    try:
        document = _toml.loads(text)
    except (TypeError, ValueError):
        return None
    build_system = document.get("build-system")
    return build_system if isinstance(build_system, Mapping) else None


def _is_expected_build_system(text: str) -> bool:
    build_system = _build_system_configuration(text)
    return (
        build_system is not None
        and set(build_system) == {"requires", "build-backend"}
        and build_system.get("requires")
        == [EXPECTED_BUILD_BACKEND_REQUIREMENT]
        and build_system.get("build-backend") == "setuptools.build_meta"
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
    parser.add_argument("--base-commit", required=True)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--dist-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--skip-install-smoke", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    report = build_distribution_report(
        args.repository,
        base_commit=args.base_commit,
        source_root=args.source_root,
        dist_dir=args.dist_dir,
        run_install_smoke=not args.skip_install_smoke,
    )
    public_report = _external_distribution_report(report)
    write_distribution_evidence(
        report,
        args.output_dir,
        public_report=public_report,
    )
    print(canonical_json_text(public_report), end="")
    return 0 if public_report.get("ok") is True else 1


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


def _safe_fixture_match(
    path: str,
    rule_id: str,
    matched: str,
    *,
    occurrence: int,
) -> bool:
    allowed = _SAFE_FIXTURE_FINGERPRINTS.get(path, {}).get(rule_id, {})
    fingerprint = sha256_bytes(f"{rule_id}\0{matched}".encode("utf-8"))
    return 0 < occurrence <= allowed.get(fingerprint, 0)


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


def _sha256_manifest(
    value: object,
    *,
    allow_empty: bool = False,
) -> dict[str, str] | None:
    if not isinstance(value, Mapping) or (not value and not allow_empty):
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


def _size_manifest(
    value: object,
    *,
    allow_empty: bool = False,
) -> dict[str, int] | None:
    if not isinstance(value, Mapping) or (not value and not allow_empty):
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
    installed_raw = metadata.get("installed_raw")
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
    if not isinstance(installed_raw, str) or not installed_raw:
        blockers.append(
            {
                "code": "invalid_installed_metadata_evidence",
                "reason": "missing_raw",
                "entry": "installed.METADATA",
            }
        )
    elif installed_raw != raw:
        blockers.append(
            {
                "code": "invalid_installed_metadata_evidence",
                "reason": "archive_install_mismatch",
                "entry": "installed.METADATA",
            }
        )
    else:
        installed_parsed = BytesParser().parsebytes(
            installed_raw.encode("utf-8")
        )
        blockers.extend(
            _core_metadata_semantic_blockers(
                installed_parsed,
                installed_raw,
                source_metadata_expectations,
                source_readme_digest,
                code="invalid_installed_metadata_evidence",
                entry="installed.METADATA",
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


def _review_range_evidence(
    repository_root: Path,
    base_commit: str,
) -> dict[str, str]:
    """Return one exact, repository-owned review range."""

    if re.fullmatch(r"[0-9a-f]{40}", base_commit) is None:
        raise RuntimeError("review base must be an exact Git commit")
    resolved_base = (
        _git_output(
            repository_root,
            ["rev-parse", "--verify", f"{base_commit}^{{commit}}"],
        )
        .decode("ascii", errors="strict")
        .strip()
    )
    head_commit = (
        _git_output(repository_root, ["rev-parse", "HEAD"])
        .decode("ascii", errors="strict")
        .strip()
    )
    merge_base = (
        _git_output(
            repository_root,
            ["merge-base", resolved_base, head_commit],
        )
        .decode("ascii", errors="strict")
        .strip()
    )
    for value in (resolved_base, head_commit, merge_base):
        if re.fullmatch(r"[0-9a-f]{40}", value) is None:
            raise RuntimeError("review range contains an invalid Git commit")
    if resolved_base != base_commit:
        raise RuntimeError("review base did not resolve to the requested commit")
    return {
        "base_commit": resolved_base,
        "head_commit": head_commit,
        "merge_base": merge_base,
    }


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
    base_commit = repository.get("base_commit")
    if (
        not isinstance(root_value, str)
        or not isinstance(base_commit, str)
        or before != after
    ):
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
        if _review_range_evidence(repository_root, base_commit) != {
            "base_commit": repository.get("base_commit"),
            "head_commit": repository.get("head_commit"),
            "merge_base": repository.get("merge_base"),
        }:
            return None
        return scan_git_and_artifacts(
            repository_root,
            snapshots,
            _distribution_report_scan_payloads(report),
            base_commit=base_commit,
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
        not _is_expected_build_system(pyproject_text)
        or
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
    uv_gitignore = dist_dir / ".gitignore"
    if uv_gitignore.exists():
        if (
            uv_gitignore.is_symlink()
            or not uv_gitignore.is_file()
            or uv_gitignore.read_bytes() != b"*"
        ):
            raise RuntimeError(
                "distribution build produced an invalid uv output marker"
            )
        uv_gitignore.unlink()
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
    metadata_manifest = {
        entry: sha256_bytes(payload)
        for entry, payload in sorted(snapshot.metadata.items())
    }
    metadata_sizes = {
        entry: len(payload)
        for entry, payload in sorted(snapshot.metadata.items())
    }
    path_prefix = f"{snapshot.kind}/"
    for raw_entry in snapshot.prescanned_inputs:
        entry = _mapping(raw_entry)
        path = entry.get("path")
        size = entry.get("size")
        digest = entry.get("sha256")
        if (
            not isinstance(path, str)
            or not path.startswith(path_prefix)
            or path == path_prefix
            or type(size) is not int
            or size < 0
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise RuntimeError("Invalid prescanned archive evidence")
        metadata_entry = path.removeprefix(path_prefix)
        if (
            metadata_entry in metadata_manifest
            or metadata_entry in metadata_sizes
        ):
            raise RuntimeError("Duplicate prescanned archive evidence")
        metadata_manifest[metadata_entry] = digest
        metadata_sizes[metadata_entry] = size
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
        "metadata_manifest": dict(sorted(metadata_manifest.items())),
        "metadata_sizes": dict(sorted(metadata_sizes.items())),
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
