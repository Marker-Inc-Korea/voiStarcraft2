"""Fail-closed distribution, licensing, and private-config verification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Final


DISTRIBUTION_COMPLIANCE_SCHEMA_VERSION: Final[int] = 1
EXPECTED_LICENSE_EXPRESSION: Final[str] = (
    "AGPL-3.0-or-later OR LicenseRef-Commercial"
)
EXPECTED_DISTRIBUTION_NAME: Final[str] = "voistarcraft2"
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
EXPECTED_DIRECT_DISTRIBUTIONS: Final[frozenset[str]] = frozenset(
    {
        "anthropic",
        "build",
        "burnysc2",
        "faster-whisper",
        "openai",
        "pytest",
        "sounddevice",
    }
)
MAX_ARCHIVE_ENTRIES: Final[int] = 4096
MAX_ARCHIVE_MEMBER_BYTES: Final[int] = 64 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES: Final[int] = 256 * 1024 * 1024
MAX_SCAN_FILE_BYTES: Final[int] = 64 * 1024 * 1024
MAX_GIT_OUTPUT_BYTES: Final[int] = 128 * 1024 * 1024
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
_REQUIREMENT_NAME_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)"
)
_PRIVATE_MODEL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)(?:myproxy.{0,100}model|DEFAULT_MYPROXY_MODEL|VOI_MYPROXY_MODEL)"
    r"[^\n]{0,80}[\"']((?:gpt|claude|gemini|grok)-[A-Za-z0-9_.-]+)[\"']"
)
_PRIVATE_ENDPOINT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)(?:myproxy|proxy)[^\n]{0,120}"
    r"(https?://(?:localhost|127(?:\.\d{1,3}){3}|10(?:\.\d{1,3}){3}|"
    r"192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])"
    r"(?:\.\d{1,3}){2}|[^/\s\"']*nomadamas[^/\s\"']*)"
    r"(?::\d+)?(?:/[^\s\"']*)?)"
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
_ENV_KEY_ASSIGNMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?im)^\s*(?:export\s+)?"
    r"(?:OPENAI|ANTHROPIC|GEMINI|XAI|MYPROXY|CODEX_MYPROXY)_API_KEY"
    r"\s*=\s*[\"']?([A-Za-z0-9._~+/=-]{12,})"
)
_CREDENTIAL_PATH_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)(?:credential(?:s)?_?(?:file|path)|"
    r"GOOGLE_APPLICATION_CREDENTIALS|AWS_SHARED_CREDENTIALS_FILE)"
    r"\s*[:=]\s*[\"']"
    r"([^\n\"']*(?:\.aws/credentials|\.netrc|id_rsa|credentials\.json|"
    r"[A-Za-z0-9_.-]+\.credentials\.json))[\"']"
)
_SAFE_FIXTURE_RULES: Final[Mapping[str, frozenset[str]]] = {
    "tests/test_llm_interpreter.py": frozenset(
        {"api_key", "bearer_token", "credential_path"}
    ),
    "tests/test_micromachine_pre_live_provenance.py": frozenset(
        {"api_key", "bearer_token", "credential_path"}
    ),
    "tests/test_web_gui.py": frozenset(
        {"api_key", "bearer_token", "credential_path"}
    ),
}
_SAFE_FIXTURE_MARKERS: Final[tuple[str, ...]] = (
    "example",
    "fake",
    "fixture",
    "secret",
    "test",
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
            if name in seen:
                blockers.append(
                    {
                        "code": "duplicate_archive_entry",
                        "kind": "wheel",
                        "entry": name,
                    }
                )
                continue
            seen.add(name)
            if info.is_dir():
                continue
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
    )


def inspect_sdist(path: Path) -> ArchiveSnapshot:
    """Read one source distribution with strict regular-file semantics."""

    blockers: list[dict[str, object]] = []
    files: dict[str, bytes] = {}
    entries: list[str] = []
    total_bytes = 0
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
            if name in seen:
                blockers.append(
                    {
                        "code": "duplicate_archive_entry",
                        "kind": "sdist",
                        "entry": name,
                    }
                )
                continue
            seen.add(name)
            if member.isdir():
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
    )


def archive_content_blockers(snapshot: ArchiveSnapshot) -> list[dict[str, object]]:
    """Return explicit package allowlist and denylist violations."""

    blockers: list[dict[str, object]] = []
    for entry in snapshot.files:
        relative = _archive_relative_path(snapshot.kind, entry)
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
            _allowed_wheel_path(relative)
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
    lowered_parts = {part.lower() for part in PurePosixPath(normalized_path).parts}
    basename = PurePosixPath(normalized_path).name.lower()
    if basename == ".env" or basename.startswith(".env."):
        findings.append(_path_finding(normalized_path, "env_file"))
    if (
        "credentials.json" == basename
        or basename.endswith(".credentials.json")
        or ".aws" in lowered_parts
    ):
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
    text = payload.decode("utf-8", errors="replace")
    rules: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("api_key", _API_KEY_RE),
        ("bearer_token", _BEARER_TOKEN_RE),
        ("api_key_assignment", _ENV_KEY_ASSIGNMENT_RE),
        ("private_endpoint", _PRIVATE_ENDPOINT_RE),
        ("private_model_override", _PRIVATE_MODEL_RE),
        ("credential_path", _CREDENTIAL_PATH_RE),
    )
    for rule_id, pattern in rules:
        for match in pattern.finditer(text):
            matched = match.group(0)
            if allow_safe_fixtures and _safe_fixture_match(
                normalized_path,
                rule_id,
                matched,
            ):
                continue
            findings.append(
                {
                    "path": normalized_path,
                    "line": text.count("\n", 0, match.start()) + 1,
                    "rule_id": rule_id,
                    "fingerprint": sha256_bytes(
                        f"{rule_id}\0{matched}".encode("utf-8")
                    ),
                }
            )
    return findings


def scan_git_and_artifacts(
    repository_root: Path,
    snapshots: Sequence[ArchiveSnapshot],
    generated_payloads: Mapping[str, bytes] | None = None,
) -> dict[str, object]:
    """Scan tracked files, the current diff, archives, and generated reports."""

    findings: list[dict[str, object]] = []
    scanned_files = 0
    repository_paths: set[str] = set()
    for arguments in (
        ["ls-files", "-z"],
        ["ls-files", "--others", "--exclude-standard", "-z"],
    ):
        output = _git_output(repository_root, arguments)
        for raw_path in output.split(b"\0"):
            if raw_path:
                repository_paths.add(
                    raw_path.decode("utf-8", errors="strict")
                )
    for relative in sorted(repository_paths):
        candidate = repository_root / relative
        if candidate.is_file():
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
        _build_distributions(source_root, dist_dir)
        wheel_paths = sorted(dist_dir.glob("*.whl"))
        sdist_paths = sorted(dist_dir.glob("*.tar.gz"))
        if len(wheel_paths) != 1 or len(sdist_paths) != 1:
            report: dict[str, object] = {
                "schema_version": DISTRIBUTION_COMPLIANCE_SCHEMA_VERSION,
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
        metadata_dependencies = sorted(
            {
                normalized_dependency_name(requirement)
                for requirement in requires_dist
                if normalized_dependency_name(requirement)
            }
        )
        declared_dependencies = sorted(
            declared_dependencies_from_pyproject(
                (source_root / "pyproject.toml").read_text(encoding="utf-8")
            )
        )
        lock_dependencies = sorted(
            direct_dependencies_from_uv_lock(
                (source_root / "uv.lock").read_text(encoding="utf-8")
            )
        )
        notice_text = (source_root / "THIRD_PARTY_NOTICES.md").read_text(
            encoding="utf-8"
        )
        notice_dependencies = sorted(
            normalized_dependency_name(name)
            for name in _NOTICE_DISTRIBUTION_RE.findall(notice_text)
        )
        licenses = _license_evidence(source_root, wheel, sdist)
        runtime_data = _runtime_data_evidence(source_root, wheel, sdist)
        install_smoke = (
            isolated_wheel_install_smoke(wheel.path)
            if run_install_smoke
            else {"attempted": False, "returncode": None, "payload": {}}
        )
        report = {
            "schema_version": DISTRIBUTION_COMPLIANCE_SCHEMA_VERSION,
            "artifacts": {
                "wheel": _artifact_evidence(wheel),
                "sdist": _artifact_evidence(sdist),
            },
            "archive_blockers": [
                *wheel.blockers,
                *sdist.blockers,
                *archive_content_blockers(wheel),
                *archive_content_blockers(sdist),
            ],
            "metadata": {
                "entry": metadata_entry,
                "license_expressions": license_expressions,
                "requires_dist": sorted(requires_dist),
                "raw": (metadata_bytes or b"").decode(
                    "utf-8",
                    errors="replace",
                ),
            },
            "licenses": licenses,
            "runtime_data": runtime_data,
            "dependencies": {
                "expected": sorted(EXPECTED_DIRECT_DISTRIBUTIONS),
                "declared": declared_dependencies,
                "lock": lock_dependencies,
                "metadata": metadata_dependencies,
                "notices": notice_dependencies,
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
    archive_blockers = report.get("archive_blockers")
    if isinstance(archive_blockers, Sequence) and not isinstance(
        archive_blockers,
        (str, bytes, bytearray),
    ):
        for item in archive_blockers:
            if isinstance(item, Mapping):
                blockers.append(dict(item))
    artifacts = _mapping(report.get("artifacts"))
    for kind in ("wheel", "sdist"):
        artifact = _mapping(artifacts.get(kind))
        digest = artifact.get("sha256")
        entries = artifact.get("entries")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            blockers.append({"code": "missing_artifact_digest", "kind": kind})
        if not isinstance(entries, list) or not entries:
            blockers.append({"code": "missing_artifact_entries", "kind": kind})
    metadata = _mapping(report.get("metadata"))
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
    for source_name in ("expected", "declared", "lock", "metadata", "notices"):
        observed = dependencies.get(source_name)
        if observed != expected:
            blockers.append(
                {
                    "code": "dependency_notice_drift",
                    "source": source_name,
                    "observed": observed,
                    "expected": expected,
                }
            )
    install_smoke = _mapping(report.get("install_smoke"))
    if install_smoke.get("attempted") is not False:
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
    secret_scan = _mapping(report.get("secret_scan"))
    findings = secret_scan.get("findings")
    if isinstance(findings, list) and findings:
        blockers.append(
            {
                "code": "secret_or_private_config_detected",
                "finding_count": len(findings),
            }
        )
    elif "secret_scan" in report and findings != []:
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
            environment = os.environ.copy()
            environment.pop("PYTHONPATH", None)
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
            return {
                "attempted": True,
                "returncode": result.returncode,
                "payload": payload if isinstance(payload, Mapping) else {},
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


def declared_dependencies_from_pyproject(text: str) -> frozenset[str]:
    """Extract direct optional dependencies from the project TOML section."""

    sanitized = "\n".join(
        _strip_toml_line_comment(line) for line in text.splitlines()
    )
    match = re.search(
        r"(?ms)^\[project\.optional-dependencies\]\s*$"
        r"(.*?)(?=^\[[^\n]+\]\s*$|\Z)",
        sanitized,
    )
    if match is None:
        return frozenset()
    requirements = re.findall(r"[\"']([^\"']+)[\"']", match.group(1))
    return frozenset(
        name
        for requirement in requirements
        if (name := normalized_dependency_name(requirement))
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
    path = PurePosixPath(name)
    if path.is_absolute():
        return "absolute_path"
    if any(part in {"", ".", ".."} for part in path.parts):
        return "path_traversal"
    return ""


def _archive_relative_path(kind: str, entry: str) -> PurePosixPath | None:
    path = PurePosixPath(entry)
    if kind == "wheel":
        return path
    if len(path.parts) < 2:
        return None
    return PurePosixPath(*path.parts[1:])


def _denied_distribution_path(path: PurePosixPath) -> str:
    lowered = tuple(part.lower() for part in path.parts)
    for part in lowered:
        if part in _DENIED_PATH_COMPONENTS:
            return f"denied_component:{part}"
        if part == ".env" or part.startswith(".env."):
            return "local_environment_file"
        if part == "credentials.json" or part.endswith(".credentials.json"):
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


def _allowed_wheel_path(path: PurePosixPath) -> bool:
    if path.parts and path.parts[0] in PRODUCT_PACKAGE_ROOTS:
        return path.suffix == ".py"
    if len(path.parts) >= 2 and path.parts[0].endswith(".dist-info"):
        tail = PurePosixPath(*path.parts[1:]).as_posix()
        if tail in _DIST_INFO_FILES:
            return True
        return tail in {
            "licenses/LICENSE",
            "licenses/LICENSES/AGPL-3.0-or-later.txt",
            "licenses/THIRD_PARTY_NOTICES.md",
        }
    marker = (
        "data",
        "share",
        "voiStarcraft2",
        "integrations",
        "micromachine",
    )
    if len(path.parts) >= 7 and path.parts[0].endswith(".data"):
        if path.parts[1:6] != marker:
            return False
        return _allowed_integration_path(
            PurePosixPath("integrations", "micromachine", *path.parts[6:])
        )
    return False


def _allowed_sdist_path(path: PurePosixPath) -> bool:
    if len(path.parts) == 1 and path.name in _SDIST_ROOT_FILES:
        return True
    if path.as_posix() == "LICENSES/AGPL-3.0-or-later.txt":
        return True
    if path.parts and path.parts[0] in PRODUCT_PACKAGE_ROOTS:
        return path.suffix == ".py"
    if path.parts and path.parts[0].lower().endswith(".egg-info"):
        return len(path.parts) == 2 and path.parts[1] in _EGG_INFO_FILES
    return _allowed_integration_path(path)


def _safe_fixture_match(path: str, rule_id: str, matched: str) -> bool:
    allowed_rules = _SAFE_FIXTURE_RULES.get(path)
    if allowed_rules is None or rule_id not in allowed_rules:
        return False
    lowered = matched.lower()
    return any(marker in lowered for marker in _SAFE_FIXTURE_MARKERS)


def _path_finding(path: str, rule_id: str) -> dict[str, object]:
    return {
        "path": path,
        "line": 0,
        "rule_id": rule_id,
        "fingerprint": sha256_bytes(f"{rule_id}\0{path}".encode("utf-8")),
    }


def _git_output(repository_root: Path, arguments: Sequence[str]) -> bytes:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository_root,
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
    command = (
        [
            uv_executable,
            "build",
            "--out-dir",
            str(dist_dir),
            str(source_root),
        ]
        if uv_executable
        else [
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            "--outdir",
            str(dist_dir),
            str(source_root),
        ]
    )
    result = subprocess.run(
        command,
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
        "filename": snapshot.path.name,
        "sha256": snapshot.digest,
        "entry_count": len(snapshot.entries),
        "entries": list(snapshot.entries),
    }


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
                "source_sha256": sha256_bytes(source),
                "wheel_sha256": sha256_bytes(wheel_payload),
                "sdist_sha256": sha256_bytes(sdist_payload),
            }
        )
    return evidence


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
            "/share/voiStarcraft2/integrations/micromachine/" + relative
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
