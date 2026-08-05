"""Deterministic immutable artifact bundles for MicroMachine pre-live evidence.

The bundle format has one canonical root ``manifest.json`` and opaque payload
members. The builder derives every digest and size from trusted member bytes.
The verifier derives its verdict from the ZIP and manifest bytes; caller status
claims are accepted only as explicitly ignored compatibility data.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import re
import stat
import struct
import unicodedata
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Final, cast

from starcraft_commander.micromachine_build_identity import (
    MICROMACHINE_BUILD_IDENTITY_SCHEMA_VERSION,
    MICROMACHINE_REQUIRED_NATIVE_TESTS,
    canonical_micromachine_ctest_registry,
)


PRE_LIVE_ARTIFACT_SCHEMA: Final[str] = "voi.micromachine.pre_live.artifact_bundle"
PRE_LIVE_ARTIFACT_SCHEMA_VERSION: Final[int] = 3
PRE_LIVE_CTEST_EVIDENCE_SCHEMA_VERSION: Final[int] = 2
PRE_LIVE_CANDIDATE_AUTHORITY_SCOPE: Final[str] = "candidate_pr"
PRE_LIVE_ARTIFACT_MANIFEST_NAME: Final[str] = "manifest.json"
GITHUB_ARTIFACT_BUNDLE_MEMBER_NAME: Final[str] = "pre-live-provenance.zip"
PRE_LIVE_DETERMINISTIC_JOURNEY_PRODUCER_ID: Final[str] = (
    "deterministic_journeys"
)
PRE_LIVE_DETERMINISTIC_JOURNEY_MEMBER_NAME: Final[str] = (
    "payload/deterministic-journeys.zip"
)
PRE_LIVE_DETERMINISTIC_JOURNEY_BUILD_BINDING_SCHEMA: Final[str] = (
    "voi.micromachine.pre_live.deterministic_journey_build_binding"
)
PRE_LIVE_DETERMINISTIC_JOURNEY_BUILD_BINDING_SCHEMA_VERSION: Final[int] = 1
DETERMINISTIC_ZIP_TIMESTAMP: Final[tuple[int, int, int, int, int, int]] = (
    1980,
    1,
    1,
    0,
    0,
    0,
)
DEFAULT_MAX_ARCHIVE_BYTES: Final[int] = 256 * 1024 * 1024
DEFAULT_MAX_MANIFEST_BYTES: Final[int] = 1024 * 1024
DEFAULT_MAX_ENTRIES: Final[int] = 128
DEFAULT_MAX_MEMBER_COMPRESSED_BYTES: Final[int] = 128 * 1024 * 1024
DEFAULT_MAX_MEMBER_UNCOMPRESSED_BYTES: Final[int] = 128 * 1024 * 1024
DEFAULT_MAX_TOTAL_UNCOMPRESSED_BYTES: Final[int] = 256 * 1024 * 1024
DEFAULT_MAX_COMPRESSION_RATIO: Final[int] = 200
READ_CHUNK_BYTES: Final[int] = 1024 * 1024

_SHA256_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_SHA256_IDENTITY_RE: Final[re.Pattern[str]] = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA40_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{40}$")
_REPOSITORY_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"
)
_SAFE_PATH_PART_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._-]+$")
_SAFE_LOGICAL_NAME_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
)
_ALLOWED_COMPRESSION: Final[frozenset[int]] = frozenset(
    {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
)
_ALLOWED_GENERAL_PURPOSE_FLAGS: Final[int] = 0x0800
_GITHUB_ARTIFACT_ALLOWED_GENERAL_PURPOSE_FLAGS: Final[int] = 0x080E
_REGULAR_FILE_MODE: Final[int] = stat.S_IFREG | 0o644
_EXECUTABLE_FILE_MODE: Final[int] = stat.S_IFREG | 0o755
_CANONICAL_FILE_MODES: Final[frozenset[int]] = frozenset(
    {_REGULAR_FILE_MODE, _EXECUTABLE_FILE_MODE}
)
_CANONICAL_EXTERNAL_ATTRS: Final[frozenset[int]] = frozenset(
    mode << 16 for mode in _CANONICAL_FILE_MODES
)
_LOCAL_FILE_HEADER: Final[struct.Struct] = struct.Struct("<4s5H3L2H")
_CENTRAL_DIRECTORY_HEADER: Final[struct.Struct] = struct.Struct("<4s6H3L5H2L")
_END_CENTRAL_DIRECTORY: Final[struct.Struct] = struct.Struct("<4s4H2LH")
_ZIP_EXTRA_FIELD_HEADER: Final[struct.Struct] = struct.Struct("<HH")
_ZIP64_EXTRA_FIELD_ID: Final[int] = 0x0001
_ZIP64_MINIMUM_EXTRACT_VERSION: Final[int] = 45

_ROOT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "schema",
        "schema_version",
        "authority",
        "repository",
        "workflow",
        "run",
        "job",
        "artifact",
        "build",
        "producer",
        "members",
    }
)
_AUTHORITY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "scope",
        "release_authoritative",
        "event",
        "pull_request",
        "closing_issue",
    }
)
_AUTHORITY_PULL_REQUEST_KEYS: Final[frozenset[str]] = frozenset(
    {
        "database_id",
        "number",
        "head_sha",
        "head_ref",
        "head_repository_id",
    }
)
_AUTHORITY_CLOSING_ISSUE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "repository_full_name",
        "repository_database_id",
        "database_id",
        "number",
    }
)
_REPOSITORY_KEYS: Final[frozenset[str]] = frozenset(
    {"full_name", "database_id", "commit_sha"}
)
_WORKFLOW_KEYS: Final[frozenset[str]] = frozenset({"id", "path", "ref", "sha"})
_RUN_KEYS: Final[frozenset[str]] = frozenset({"id", "attempt"})
_JOB_KEYS: Final[frozenset[str]] = frozenset({"id", "name"})
_ARTIFACT_KEYS: Final[frozenset[str]] = frozenset(
    {"logical_name", "member", "sha256", "size_bytes"}
)
_BUILD_KEYS: Final[frozenset[str]] = frozenset(
    {
        "report_identity",
        "report_member",
        "report_sha256",
        "binary_member",
        "binary_sha256",
        "repository_input_member",
        "repository_input_identity",
        "repository_input_sha256",
        "ctest_member",
        "ctest_sha256",
    }
)
_PRODUCER_KEYS: Final[frozenset[str]] = frozenset(
    {
        "policy_id",
        "policy_member",
        "policy_sha256",
        "executable_member",
        "executable_sha256",
        "argv_member",
        "argv_sha256",
        "output_member",
        "output_sha256",
        "provenance_member",
        "provenance_sha256",
    }
)
_MEMBER_KEYS: Final[frozenset[str]] = frozenset({"name", "sha256", "size_bytes"})
_REPOSITORY_INPUT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "repository_commit",
        "build_input_identity",
        "repository_inputs_digest",
        "paths",
        "upstream_commit_policy",
    }
)
_UPSTREAM_COMMIT_POLICY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "path",
        "sha256",
        "micromachine_commit",
        "s2client_commit",
    }
)
_CTEST_EVIDENCE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "argv",
        "discovery_argv",
        "ctest_executable",
        "ctest_executable_sha256",
        "returncode",
        "passed",
        "total",
        "failures",
        "test_names",
        "test_executables",
        "test_manifest_sha256",
        "registry_sha256",
        "stdout_sha256",
        "stderr_sha256",
    }
)
_CTEST_EXECUTABLE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "path",
        "sha256",
        "sha256_after",
        "argv",
        "returncode",
        "stdout_sha256",
        "stderr_sha256",
    }
)
_REQUIRED_CTEST_EXECUTABLES: Final[dict[str, str]] = dict(
    MICROMACHINE_REQUIRED_NATIVE_TESTS
)
_DETERMINISTIC_JOURNEY_ROOT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "evidence_kind",
        "suite_id",
        "journey_count",
        "failed_count",
        "report_sha256",
        "members",
    }
)
_DETERMINISTIC_JOURNEY_BUILD_BINDING_KEYS: Final[frozenset[str]] = frozenset(
    {
        "schema",
        "schema_version",
        "source",
        "binary_sha256",
        "embedded_build_input_identity",
    }
)


@dataclass(frozen=True)
class PreLiveArtifactLimits:
    """Hard resource limits applied before and during ZIP decompression."""

    max_archive_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES
    max_manifest_bytes: int = DEFAULT_MAX_MANIFEST_BYTES
    max_entries: int = DEFAULT_MAX_ENTRIES
    max_member_compressed_bytes: int = DEFAULT_MAX_MEMBER_COMPRESSED_BYTES
    max_member_uncompressed_bytes: int = DEFAULT_MAX_MEMBER_UNCOMPRESSED_BYTES
    max_total_uncompressed_bytes: int = DEFAULT_MAX_TOTAL_UNCOMPRESSED_BYTES
    max_compression_ratio: int = DEFAULT_MAX_COMPRESSION_RATIO

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{field.name} must be a positive integer")
        if self.max_manifest_bytes > self.max_member_uncompressed_bytes:
            raise ValueError(
                "max_manifest_bytes cannot exceed max_member_uncompressed_bytes"
            )
        if self.max_member_uncompressed_bytes > self.max_total_uncompressed_bytes:
            raise ValueError(
                "max_member_uncompressed_bytes cannot exceed "
                "max_total_uncompressed_bytes"
            )


@dataclass(frozen=True)
class PreLiveArtifactMetadata:
    """Trusted identities and member-role bindings used by the builder."""

    authority_scope: str
    release_authoritative: bool
    authority_event: str
    pull_request_database_id: int
    pull_request_number: int
    pull_request_head_sha: str
    pull_request_head_ref: str
    pull_request_head_repository_id: int
    closing_issue_repository_full_name: str
    closing_issue_repository_database_id: int
    closing_issue_database_id: int
    closing_issue_number: int
    repository_full_name: str
    repository_database_id: int
    repository_commit: str
    workflow_id: int
    workflow_path: str
    workflow_ref: str
    workflow_sha: str
    run_id: int
    run_attempt: int
    job_id: int
    job_name: str
    artifact_logical_name: str
    artifact_member: str
    build_report_identity: str
    build_report_member: str
    binary_member: str
    repository_input_member: str
    repository_input_identity: str
    ctest_member: str
    producer_policy_id: str
    producer_policy_member: str
    producer_executable_member: str
    producer_argv_member: str
    producer_output_member: str
    producer_provenance_member: str


MicroMachinePreLiveArtifactMetadata = PreLiveArtifactMetadata
MicroMachinePreLiveArtifactLimits = PreLiveArtifactLimits


@dataclass(frozen=True)
class PreLiveBuildAdmissionSnapshot:
    """Immutable bytes and source mode accepted by the supported build identity schema."""

    build_report_bytes: bytes
    binary_bytes: bytes
    binary_mode: int

    def __post_init__(self) -> None:
        for field_name in ("build_report_bytes", "binary_bytes"):
            value = getattr(self, field_name)
            if not isinstance(value, (bytes, bytearray, memoryview)):
                raise TypeError(f"{field_name} must be bytes-like")
            object.__setattr__(self, field_name, bytes(value))
        if type(self.binary_mode) is not int:
            raise TypeError("binary_mode must be an integer stat mode")
        if stat.S_IFMT(self.binary_mode) != stat.S_IFREG:
            raise ValueError("admitted MicroMachine binary must be a regular file")
        if self.binary_mode & 0o111 == 0:
            raise ValueError("admitted MicroMachine binary must be executable")

    @property
    def build_report_sha256(self) -> str:
        return hashlib.sha256(self.build_report_bytes).hexdigest()

    @property
    def binary_sha256(self) -> str:
        return hashlib.sha256(self.binary_bytes).hexdigest()


MicroMachinePreLiveBuildAdmissionSnapshot = PreLiveBuildAdmissionSnapshot


class DuplicateJSONKeyError(ValueError):
    """Raised when JSON contains an ambiguous duplicate object key."""


def canonical_json_bytes(value: object) -> bytes:
    """Encode finite JSON with the bundle's canonical representation."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_ctest_evidence_bytes(value: Mapping[str, object]) -> bytes:
    """Build canonical, semantically validated evidence for the exact CTest set."""

    if not isinstance(value, Mapping):
        raise TypeError("CTest evidence must be a mapping")
    payload = {key: value.get(key) for key in sorted(_CTEST_EVIDENCE_KEYS)}
    blockers: list[dict[str, object]] = []
    _validate_ctest_evidence_payload(payload, blockers)
    if blockers:
        raise ValueError(_format_builder_blockers(blockers))
    return canonical_json_bytes(payload)


def bind_deterministic_journey_bundle_to_build(
    bundle: bytes,
    *,
    build_report_bytes: bytes,
    binary_bytes: bytes,
    node_executable: Path | str | None = None,
) -> bytes:
    """Bind verified journey evidence to one admitted MicroMachine build."""

    from starcraft_commander.micromachine_pre_live_journeys import (
        _preflight_pre_live_journey_root,
        _verify_pre_live_journey_payload_cache,
    )

    if not isinstance(bundle, bytes):
        raise TypeError("deterministic journey bundle must be bytes")
    if not isinstance(build_report_bytes, bytes):
        raise TypeError("build report must be bytes")
    if not isinstance(binary_bytes, bytes):
        raise TypeError("MicroMachine binary must be bytes")
    if node_executable is None:
        raise ValueError(
            "deterministic journey replay requires an admitted Node.js "
            "executable or descriptor"
        )
    binding = _deterministic_journey_build_binding(
        build_report_bytes,
        binary_bytes,
    )

    def validate_unbound_root(
        root: dict[str, object],
        infos: Sequence[zipfile.ZipInfo],
    ) -> bool:
        root_blockers: list[str] = []
        _preflight_pre_live_journey_root(
            root,
            member_names=[info.filename for info in infos[1:]],
            member_sizes={
                info.filename: info.file_size for info in infos[1:]
            },
            blockers=root_blockers,
        )
        if root_blockers:
            raise ValueError(
                "deterministic journey root manifest is unsupported: "
                f"{root_blockers!r}"
            )
        return True

    root, payloads = _read_deterministic_journey_archive(
        bundle,
        root_validator=validate_unbound_root,
    )
    verification = _verify_pre_live_journey_payload_cache(
        root,
        payloads,
        node_executable=node_executable,
    )
    if verification.get("ok") is not True:
        raise ValueError(
            "deterministic_journey_bundle_rejected: "
            f"{verification.get('blockers')!r}"
        )
    for key in ("binary_sha256", "embedded_build_input_identity"):
        if verification.get(key) != binding[key]:
            raise ValueError(
                "deterministic_journey_nested_build_identity_mismatch: "
                f"{key}"
            )
    if set(root) != _DETERMINISTIC_JOURNEY_ROOT_KEYS:
        raise ValueError("deterministic journey root manifest is unsupported")
    root["build_binding"] = binding
    return _write_deterministic_journey_archive(root, payloads)


def _deterministic_journey_build_binding(
    build_report_bytes: bytes,
    binary_bytes: bytes | None,
) -> dict[str, object]:
    try:
        report = json.loads(
            build_report_bytes,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"build report is invalid JSON: {exc}") from exc
    if not isinstance(report, Mapping):
        raise ValueError("build report must be a JSON object")
    report_schema_version = report.get("schema_version")
    if (
        type(report_schema_version) is not int
        or report_schema_version != MICROMACHINE_BUILD_IDENTITY_SCHEMA_VERSION
        or report.get("ok") is not True
        or report.get("failures") != []
    ):
        raise ValueError("build report is not an admitted build identity")
    report_identity = report.get("identity")
    observed = report.get("observed")
    if not isinstance(report_identity, str) or not _SHA256_IDENTITY_RE.fullmatch(
        report_identity
    ):
        raise ValueError("build report identity is not canonical")
    if not isinstance(observed, Mapping):
        raise ValueError("build report observed evidence is missing")
    observed_binary_sha256 = observed.get("binary_sha256")
    if (
        not isinstance(observed_binary_sha256, str)
        or not _SHA256_RE.fullmatch(observed_binary_sha256)
    ):
        raise ValueError("build report binary digest is not canonical")
    binary_sha256 = (
        hashlib.sha256(binary_bytes).hexdigest()
        if binary_bytes is not None
        else observed_binary_sha256
    )
    if observed_binary_sha256 != binary_sha256:
        raise ValueError("build report binary digest does not match binary bytes")
    embedded_identity = observed.get("embedded_build_input_identity")
    if not isinstance(embedded_identity, str) or not _SHA256_IDENTITY_RE.fullmatch(
        embedded_identity
    ):
        raise ValueError("embedded build input identity is not canonical")
    return {
        "schema": PRE_LIVE_DETERMINISTIC_JOURNEY_BUILD_BINDING_SCHEMA,
        "schema_version": (
            PRE_LIVE_DETERMINISTIC_JOURNEY_BUILD_BINDING_SCHEMA_VERSION
        ),
        "source": "micromachine_binary_runtime",
        "binary_sha256": binary_sha256,
        "embedded_build_input_identity": embedded_identity,
    }


def _read_deterministic_journey_archive(
    bundle: bytes,
    *,
    limits: PreLiveArtifactLimits | None = None,
    root_validator: (
        Callable[[dict[str, object], Sequence[zipfile.ZipInfo]], bool] | None
    ) = None,
) -> tuple[dict[str, object], dict[str, bytes]]:
    effective_limits = limits or PreLiveArtifactLimits()
    _preflight_deterministic_journey_central_directory(
        bundle,
        effective_limits,
    )
    with zipfile.ZipFile(io.BytesIO(bundle), mode="r") as archive:
        infos = archive.infolist()
        blockers: list[dict[str, object]] = []
        _preflight_archive(
            bundle,
            archive,
            infos,
            effective_limits,
            blockers,
        )
        if blockers:
            first = blockers[0]
            raise ValueError(
                "deterministic journey ZIP preflight rejected: "
                f"{first.get('code')}: {first.get('message')}"
            )
        if len(infos) > effective_limits.max_entries:
            raise ValueError(
                "deterministic journey ZIP exceeds max_entries"
            )
        names = [info.filename for info in infos]
        if (
            not names
            or names[0] != PRE_LIVE_ARTIFACT_MANIFEST_NAME
            or names[1:] != sorted(names[1:])
            or len(names) != len(set(names))
        ):
            raise ValueError(
                "deterministic journey ZIP members are not canonical"
            )
        if archive.comment:
            raise ValueError("deterministic journey ZIP comment is forbidden")
        total_uncompressed = 0
        for info in infos:
            if (
                info.date_time != DETERMINISTIC_ZIP_TIMESTAMP
                or info.compress_type != zipfile.ZIP_STORED
                or info.create_system != 3
                or info.external_attr != _REGULAR_FILE_MODE << 16
                or info.extra
                or info.comment
                or info.flag_bits & ~_ALLOWED_GENERAL_PURPOSE_FLAGS
            ):
                raise ValueError(
                    "deterministic journey ZIP metadata is noncanonical: "
                    f"{info.filename}"
                )
            if (
                info.compress_size
                > effective_limits.max_member_compressed_bytes
            ):
                raise ValueError(
                    "deterministic journey ZIP member exceeds "
                    f"max_member_compressed_bytes: {info.filename}"
                )
            if (
                info.file_size
                > effective_limits.max_member_uncompressed_bytes
            ):
                raise ValueError(
                    "deterministic journey ZIP member exceeds "
                    f"max_member_uncompressed_bytes: {info.filename}"
                )
            if (
                info.filename == PRE_LIVE_ARTIFACT_MANIFEST_NAME
                and info.file_size > effective_limits.max_manifest_bytes
            ):
                raise ValueError(
                    "deterministic journey ZIP manifest exceeds "
                    "max_manifest_bytes"
                )
            total_uncompressed += info.file_size
            if (
                total_uncompressed
                > effective_limits.max_total_uncompressed_bytes
            ):
                raise ValueError(
                    "deterministic journey ZIP exceeds "
                    "max_total_uncompressed_bytes"
                )
            if (
                _compression_ratio(info)
                > effective_limits.max_compression_ratio
            ):
                raise ValueError(
                    "deterministic journey ZIP member exceeds "
                    f"max_compression_ratio: {info.filename}"
                )
        raw_root = archive.read(PRE_LIVE_ARTIFACT_MANIFEST_NAME)
        try:
            root = json.loads(
                raw_root,
                object_pairs_hook=_reject_duplicate_json_keys,
                parse_constant=_reject_nonfinite_json,
            )
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"deterministic journey root manifest is invalid: {exc}"
            ) from exc
        if not isinstance(root, dict):
            raise ValueError(
                "deterministic journey root manifest must be an object"
            )
        if canonical_json_bytes(root) != raw_root:
            raise ValueError(
                "deterministic journey root manifest must use canonical JSON"
            )
        if root_validator is not None and not root_validator(root, infos):
            return root, {}
        payloads: dict[str, bytes] = {}
        for info in infos[1:]:
            payloads[info.filename] = archive.read(info)
    return root, payloads


def _preflight_deterministic_journey_central_directory(
    bundle: bytes,
    limits: PreLiveArtifactLimits,
) -> None:
    if len(bundle) > limits.max_archive_bytes:
        raise ValueError(
            "deterministic journey ZIP exceeds max_archive_bytes"
        )
    if _archive_entry_count_error(
        bundle,
        maximum=limits.max_entries,
    ) is not None:
        raise ValueError(
            "deterministic journey ZIP exceeds max_entries"
        )
    framing_error = _archive_framing_error(
        bundle,
        require_exact_local_flags=True,
        allowed_general_purpose_flags=0,
        require_empty_extra_fields=True,
    )
    if framing_error is not None:
        raise ValueError(
            f"deterministic journey ZIP framing is invalid: {framing_error}"
        )
    eocd_offset = len(bundle) - _END_CENTRAL_DIRECTORY.size
    (
        _,
        _,
        _,
        _,
        total_entries,
        central_size,
        central_offset,
        _,
    ) = _END_CENTRAL_DIRECTORY.unpack_from(bundle, eocd_offset)
    if central_size > len(bundle):
        raise ValueError(
            "deterministic journey ZIP central directory is oversized"
        )

    offset = central_offset
    parsed_entries = 0
    while offset < eocd_offset:
        if parsed_entries >= limits.max_entries:
            raise ValueError("deterministic journey ZIP exceeds max_entries")
        if offset + _CENTRAL_DIRECTORY_HEADER.size > eocd_offset:
            raise ValueError(
                "deterministic journey ZIP central directory is truncated"
            )
        fields = _CENTRAL_DIRECTORY_HEADER.unpack_from(bundle, offset)
        if fields[0] != b"PK\x01\x02":
            raise ValueError(
                "deterministic journey ZIP central directory is invalid"
            )
        filename_size = fields[10]
        extra_size = fields[11]
        comment_size = fields[12]
        entry_size = (
            _CENTRAL_DIRECTORY_HEADER.size
            + filename_size
            + extra_size
            + comment_size
        )
        if entry_size <= _CENTRAL_DIRECTORY_HEADER.size:
            raise ValueError(
                "deterministic journey ZIP central entry name is empty"
            )
        offset += entry_size
        if offset > eocd_offset:
            raise ValueError(
                "deterministic journey ZIP central directory is truncated"
            )
        parsed_entries += 1
    if offset != eocd_offset or parsed_entries != total_entries:
        raise ValueError(
            "deterministic journey ZIP central directory count mismatch"
        )


def _write_deterministic_journey_archive(
    root: Mapping[str, object],
    payloads: Mapping[str, bytes],
) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_STORED,
        allowZip64=False,
    ) as archive:
        _write_deterministic_member(
            archive,
            PRE_LIVE_ARTIFACT_MANIFEST_NAME,
            canonical_json_bytes(root),
        )
        for name in sorted(payloads):
            _write_deterministic_member(archive, name, payloads[name])
    return output.getvalue()


def _verify_bound_deterministic_journey_bundle(
    bundle: bytes,
    *,
    build_report_bytes: bytes | None,
    manifest: Mapping[str, object],
    limits: PreLiveArtifactLimits,
    node_executable: Path | str | None,
) -> dict[str, object]:
    from starcraft_commander.micromachine_pre_live_journeys import (
        _preflight_pre_live_journey_root,
        _verify_pre_live_journey_payload_cache,
    )

    blockers: list[dict[str, object]] = []
    binding: dict[str, object] = {}
    raw_verification: Mapping[str, object] = {}
    unbound_root: dict[str, object] = {}
    build = _mapping(manifest.get("build"))

    if node_executable is None:
        _add_blocker(
            blockers,
            "deterministic_journey_node_executable_missing",
            "$.producer.output_member",
            "deterministic journey replay requires an admitted Node.js "
            "executable or descriptor",
        )
        return {
            "ok": False,
            "blockers": blockers,
            "build_binding": {},
            "raw_evidence": {},
        }
    if build_report_bytes is None:
        _add_blocker(
            blockers,
            "deterministic_journey_outer_build_report_missing",
            "$.build.report_member",
            "outer build report bytes are unavailable for journey binding",
        )
        return {
            "ok": False,
            "blockers": blockers,
            "build_binding": {},
            "raw_evidence": {},
        }
    try:
        expected_binding = _deterministic_journey_build_binding(
            build_report_bytes,
            None,
        )
    except ValueError:
        _add_blocker(
            blockers,
            "deterministic_journey_outer_build_report_rejected",
            "$.build.report_member",
            "outer build report cannot derive the journey build binding",
        )
        return {
            "ok": False,
            "blockers": blockers,
            "build_binding": {},
            "raw_evidence": {},
        }
    expected_binding["binary_sha256"] = build.get("binary_sha256")

    def validate_bound_root(
        root: dict[str, object],
        infos: Sequence[zipfile.ZipInfo],
    ) -> bool:
        if "authority" in root:
            _add_blocker(
                blockers,
                "deterministic_journey_fabricated_authority",
                "$.producer.output.manifest.authority",
                "nested journey evidence cannot declare its own build authority",
            )
        expected_root_keys = _DETERMINISTIC_JOURNEY_ROOT_KEYS | {
            "build_binding"
        }
        if set(root) != expected_root_keys:
            _add_blocker(
                blockers,
                "deterministic_journey_build_binding_schema_mismatch",
                "$.producer.output.manifest",
                "bound journey root manifest has an invalid field set",
                expected=sorted(expected_root_keys),
                actual=sorted(root),
            )
        raw_binding = root.get("build_binding")
        if not isinstance(raw_binding, Mapping):
            _add_blocker(
                blockers,
                "deterministic_journey_build_binding_missing",
                "$.producer.output.manifest.build_binding",
                "nested journey evidence is not bound to the outer build",
            )
        else:
            binding.update(dict(raw_binding))
            if "authority" in raw_binding:
                _add_blocker(
                    blockers,
                    "deterministic_journey_fabricated_authority",
                    "$.producer.output.manifest.build_binding.authority",
                    "nested journey build binding cannot assert authority",
                )
            binding_schema_version = raw_binding.get("schema_version")
            if (
                set(raw_binding)
                != _DETERMINISTIC_JOURNEY_BUILD_BINDING_KEYS
                or raw_binding.get("schema")
                != PRE_LIVE_DETERMINISTIC_JOURNEY_BUILD_BINDING_SCHEMA
                or type(binding_schema_version) is not int
                or binding_schema_version
                != PRE_LIVE_DETERMINISTIC_JOURNEY_BUILD_BINDING_SCHEMA_VERSION
                or raw_binding.get("source") != "micromachine_binary_runtime"
            ):
                _add_blocker(
                    blockers,
                    "deterministic_journey_build_binding_schema_mismatch",
                    "$.producer.output.manifest.build_binding",
                    "nested journey build binding has an invalid schema",
                    expected=sorted(
                        _DETERMINISTIC_JOURNEY_BUILD_BINDING_KEYS
                    ),
                    actual=sorted(raw_binding),
                )

        candidate_unbound_root = {
            key: value for key, value in root.items() if key != "build_binding"
        }
        root_blockers: list[str] = []
        _preflight_pre_live_journey_root(
            candidate_unbound_root,
            member_names=[info.filename for info in infos[1:]],
            member_sizes={
                info.filename: info.file_size for info in infos[1:]
            },
            blockers=root_blockers,
        )
        if root_blockers:
            _add_blocker(
                blockers,
                "deterministic_journey_bundle_rejected",
                "$.producer.output_member",
                "deterministic journey root or member descriptors are invalid",
                inner_blockers=root_blockers,
            )

        mismatch_codes = {
            "binary_sha256": "deterministic_journey_binary_digest_mismatch",
            "embedded_build_input_identity": (
                "deterministic_journey_embedded_build_identity_mismatch"
            ),
        }
        for key, expected in expected_binding.items():
            actual = binding.get(key)
            if actual == expected:
                continue
            _add_blocker(
                blockers,
                mismatch_codes.get(
                    key,
                    "deterministic_journey_build_binding_mismatch",
                ),
                f"$.producer.output.manifest.build_binding.{key}",
                "nested journey build binding does not match outer build evidence",
                expected=expected,
                actual=actual,
            )
        if blockers:
            return False
        unbound_root.update(candidate_unbound_root)
        return True

    try:
        root, payloads = _read_deterministic_journey_archive(
            bundle,
            limits=limits,
            root_validator=validate_bound_root,
        )
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
        _add_blocker(
            blockers,
            "deterministic_journey_bundle_rejected",
            "$.producer.output_member",
            "deterministic journey producer output is not a canonical bound ZIP",
            error=str(exc),
        )
        return {
            "ok": False,
            "blockers": blockers,
            "build_binding": {},
            "raw_evidence": {},
        }
    if blockers:
        return {
            "ok": False,
            "blockers": blockers,
            "build_binding": dict(binding),
            "raw_evidence": {},
        }
    raw_verification = _verify_pre_live_journey_payload_cache(
        unbound_root,
        payloads,
        node_executable=node_executable,
    )
    if raw_verification.get("ok") is not True:
        _add_blocker(
            blockers,
            "deterministic_journey_bundle_rejected",
            "$.producer.output_member",
            "deterministic journey producer output failed "
            "raw-evidence verification",
            inner_blockers=raw_verification.get("blockers"),
        )
    if raw_verification:
        for key, code in (
            (
                "binary_sha256",
                "deterministic_journey_nested_binary_digest_mismatch",
            ),
            (
                "embedded_build_input_identity",
                "deterministic_journey_nested_embedded_identity_mismatch",
            ),
        ):
            nested = raw_verification.get(key)
            expected = binding.get(key)
            if nested == expected:
                continue
            _add_blocker(
                blockers,
                code,
                f"$.producer.output.product.*.native_adapter.{key}",
                "per-journey native adapter identity does not match root binding",
                expected=expected,
                actual=nested,
            )
    return {
        "ok": not blockers,
        "blockers": blockers,
        "build_binding": dict(binding),
        "raw_evidence": dict(raw_verification),
    }


def build_pre_live_artifact_bundle(
    metadata: PreLiveArtifactMetadata | Mapping[str, object],
    members: Mapping[str, bytes | bytearray | memoryview],
    *,
    limits: PreLiveArtifactLimits | None = None,
    admission_snapshot: PreLiveBuildAdmissionSnapshot | None = None,
    node_executable: Path | str | None = None,
) -> bytes:
    """Build deterministic ZIP bytes from trusted metadata and payload bytes."""

    effective_limits = limits or PreLiveArtifactLimits()
    _require_admission_snapshot_type(admission_snapshot)
    normalized_metadata = _coerce_metadata(metadata)
    metadata_blockers = _validate_metadata(normalized_metadata)
    if metadata_blockers:
        raise ValueError(_format_builder_blockers(metadata_blockers))
    normalized_members = _normalize_builder_members(members, effective_limits)
    manifest = _build_manifest(normalized_metadata, normalized_members)
    if admission_snapshot is not None:
        _validate_builder_admission_snapshot(
            normalized_metadata,
            normalized_members,
            admission_snapshot,
        )
    manifest_bytes = canonical_json_bytes(manifest)
    if len(manifest_bytes) > effective_limits.max_manifest_bytes:
        raise ValueError("canonical manifest exceeds max_manifest_bytes")

    total_uncompressed = len(manifest_bytes) + sum(
        len(payload) for payload in normalized_members.values()
    )
    if total_uncompressed > effective_limits.max_total_uncompressed_bytes:
        raise ValueError("bundle members exceed max_total_uncompressed_bytes")

    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_STORED,
        allowZip64=False,
    ) as archive:
        _write_deterministic_member(
            archive,
            PRE_LIVE_ARTIFACT_MANIFEST_NAME,
            manifest_bytes,
        )
        for name in sorted(normalized_members):
            _write_deterministic_member(
                archive,
                name,
                normalized_members[name],
                mode=(
                    _EXECUTABLE_FILE_MODE
                    if name == normalized_metadata.binary_member
                    else _REGULAR_FILE_MODE
                ),
            )
    bundle = output.getvalue()
    if len(bundle) > effective_limits.max_archive_bytes:
        raise ValueError("constructed bundle exceeds max_archive_bytes")

    verification = verify_pre_live_artifact_bundle(
        bundle,
        limits=effective_limits,
        admission_snapshot=admission_snapshot,
        node_executable=node_executable,
    )
    if verification["ok"] is not True:
        blockers = cast(list[Mapping[str, object]], verification["blockers"])
        raise ValueError(
            "constructed bundle failed verification: "
            + _format_builder_blockers(blockers)
        )
    return bundle


def verify_pre_live_artifact_bundle(
    bundle: bytes | bytearray | memoryview,
    *,
    limits: PreLiveArtifactLimits | None = None,
    caller_claims: Mapping[str, object] | None = None,
    admission_snapshot: PreLiveBuildAdmissionSnapshot | None = None,
    node_executable: Path | str | None = None,
) -> dict[str, object]:
    """Verify a bundle from bytes without trusting caller-supplied status."""

    effective_limits = limits or PreLiveArtifactLimits()
    _require_admission_snapshot_type(admission_snapshot)
    blockers: list[dict[str, object]] = []
    manifest: dict[str, object] | None = None
    manifest_evidence: dict[str, object] = {
        "name": PRE_LIVE_ARTIFACT_MANIFEST_NAME,
        "present": False,
        "canonical": False,
        "sha256": None,
        "size_bytes": None,
    }
    member_evidence: list[dict[str, object]] = []
    role_evidence: dict[str, object] = {}

    if not isinstance(bundle, (bytes, bytearray, memoryview)):
        _add_blocker(
            blockers,
            "invalid_bundle_type",
            "$",
            "bundle must be bytes-like",
        )
        return _verification_result(
            blockers,
            manifest,
            manifest_evidence,
            member_evidence,
            caller_claims,
        )
    bundle_bytes = bytes(bundle)
    manifest_evidence["bundle_sha256"] = hashlib.sha256(bundle_bytes).hexdigest()
    manifest_evidence["bundle_size_bytes"] = len(bundle_bytes)
    if len(bundle_bytes) > effective_limits.max_archive_bytes:
        _add_blocker(
            blockers,
            "archive_size_limit_exceeded",
            "$",
            "archive exceeds max_archive_bytes",
            actual=len(bundle_bytes),
            maximum=effective_limits.max_archive_bytes,
        )
        return _verification_result(
            blockers,
            manifest,
            manifest_evidence,
            member_evidence,
            caller_claims,
        )
    if _archive_entry_count_error(
        bundle_bytes,
        maximum=effective_limits.max_entries,
    ) is not None:
        _add_blocker(
            blockers,
            "archive_entry_count_limit_exceeded",
            "$",
            "archive exceeds max_entries",
            maximum=effective_limits.max_entries,
        )
        return _verification_result(
            blockers,
            manifest,
            manifest_evidence,
            member_evidence,
            caller_claims,
        )
    framing_error = _archive_framing_error(bundle_bytes)
    if framing_error is not None:
        framing_code = (
            "noncanonical_zip_framing"
            if b"PK\x03\x04" in bundle_bytes and b"PK\x05\x06" in bundle_bytes
            else "invalid_zip"
        )
        _add_blocker(
            blockers,
            framing_code,
            "$",
            framing_error,
        )
        return _verification_result(
            blockers,
            manifest,
            manifest_evidence,
            member_evidence,
            caller_claims,
        )

    try:
        with zipfile.ZipFile(io.BytesIO(bundle_bytes), mode="r") as archive:
            infos = archive.infolist()
            framing_error = _archive_framing_error(
                bundle_bytes,
                parsed_entry_count=len(infos),
            )
            if framing_error is not None:
                _add_blocker(
                    blockers,
                    "noncanonical_zip_framing",
                    "$",
                    framing_error,
                )
                return _verification_result(
                    blockers,
                    manifest,
                    manifest_evidence,
                    member_evidence,
                    caller_claims,
                )
            _preflight_archive(
                bundle_bytes,
                archive,
                infos,
                effective_limits,
                blockers,
            )
            if blockers:
                return _verification_result(
                    blockers,
                    manifest,
                    manifest_evidence,
                    member_evidence,
                    caller_claims,
                )

            info_by_name = {info.filename: info for info in infos}
            manifest_info = info_by_name[PRE_LIVE_ARTIFACT_MANIFEST_NAME]
            manifest_evidence["present"] = True
            raw_manifest, read_evidence = _read_member(
                archive,
                manifest_info,
                maximum=effective_limits.max_manifest_bytes,
                capture=True,
            )
            manifest_evidence.update(read_evidence)
            if raw_manifest is None:
                _add_blocker(
                    blockers,
                    "manifest_read_failed",
                    "$.manifest",
                    "manifest bytes could not be read safely",
                    error=read_evidence.get("error"),
                )
                return _verification_result(
                    blockers,
                    manifest,
                    manifest_evidence,
                    member_evidence,
                    caller_claims,
                )

            parsed = _parse_json(raw_manifest, blockers, "$.manifest")
            if isinstance(parsed, Mapping):
                manifest = dict(parsed)
                canonical = _canonical_json_or_none(parsed)
                manifest_evidence["canonical"] = canonical == raw_manifest
                if canonical != raw_manifest:
                    _add_blocker(
                        blockers,
                        "noncanonical_manifest_json",
                        "$.manifest",
                        "manifest bytes are not canonical JSON",
                    )
            elif parsed is not None:
                _add_blocker(
                    blockers,
                    "invalid_manifest_type",
                    "$.manifest",
                    "manifest must be a JSON object",
                )

            if manifest is None:
                return _verification_result(
                    blockers,
                    manifest,
                    manifest_evidence,
                    member_evidence,
                    caller_claims,
                )

            manifest_blockers = _validate_manifest(
                manifest,
                effective_limits,
            )
            blockers.extend(manifest_blockers)
            if blockers:
                return _verification_result(
                    blockers,
                    manifest,
                    manifest_evidence,
                    member_evidence,
                    caller_claims,
                )

            descriptors = _manifest_member_descriptors(manifest)
            declared_names = set(descriptors)
            archive_payload_names = set(info_by_name) - {
                PRE_LIVE_ARTIFACT_MANIFEST_NAME
            }
            for name in sorted(declared_names - archive_payload_names):
                _add_blocker(
                    blockers,
                    "missing_entry",
                    f"$.members[{name!r}]",
                    "manifest-declared member is missing from the archive",
                    name=name,
                )
            for name in sorted(archive_payload_names - declared_names):
                _add_blocker(
                    blockers,
                    "unexpected_entry",
                    f"$.archive[{name!r}]",
                    "archive member is not declared by the manifest",
                    name=name,
                )
            if blockers:
                return _verification_result(
                    blockers,
                    manifest,
                    manifest_evidence,
                    member_evidence,
                    caller_claims,
                )

            _validate_archive_member_modes(
                info_by_name,
                manifest,
                blockers,
            )
            if blockers:
                return _verification_result(
                    blockers,
                    manifest,
                    manifest_evidence,
                    member_evidence,
                    caller_claims,
                )

            build = _mapping(manifest.get("build"))
            producer = _mapping(manifest.get("producer"))
            build_report_name = cast(str, build["report_member"])
            captured_role_names = {
                build_report_name,
                cast(str, build["repository_input_member"]),
                cast(str, build["ctest_member"]),
                cast(str, producer["provenance_member"]),
            }
            if (
                producer.get("policy_id")
                == PRE_LIVE_DETERMINISTIC_JOURNEY_PRODUCER_ID
            ):
                captured_role_names.add(cast(str, producer["output_member"]))
            captured_members: dict[str, bytes] = {}
            for name in sorted(declared_names):
                info = info_by_name[name]
                expected = descriptors[name]
                captured, evidence = _read_member(
                    archive,
                    info,
                    maximum=effective_limits.max_member_uncompressed_bytes,
                    capture=name in captured_role_names,
                )
                evidence.update(
                    {
                        "expected_sha256": expected["sha256"],
                        "expected_size_bytes": expected["size_bytes"],
                    }
                )
                member_evidence.append(evidence)
                if captured is not None and name in captured_role_names:
                    captured_members[name] = captured
                if evidence.get("error") is not None:
                    _add_blocker(
                        blockers,
                        "member_read_failed",
                        f"$.members[{name!r}]",
                        "member bytes could not be read safely",
                        name=name,
                        error=evidence["error"],
                    )
                    continue
                if evidence["size_bytes"] != expected["size_bytes"]:
                    _add_blocker(
                        blockers,
                        "member_size_mismatch",
                        f"$.members[{name!r}].size_bytes",
                        "member size does not match the manifest",
                        name=name,
                        expected=expected["size_bytes"],
                        actual=evidence["size_bytes"],
                    )
                if not hmac.compare_digest(
                    cast(str, evidence["sha256"]),
                    cast(str, expected["sha256"]),
                ):
                    _add_blocker(
                        blockers,
                        "member_digest_mismatch",
                        f"$.members[{name!r}].sha256",
                        "member SHA-256 does not match the manifest",
                        name=name,
                        expected=expected["sha256"],
                        actual=evidence["sha256"],
                    )

            evidence_by_name = {
                cast(str, item["name"]): item for item in member_evidence
            }
            _validate_role_bindings(
                manifest,
                descriptors,
                evidence_by_name,
                blockers,
            )
            if admission_snapshot is not None:
                _validate_admission_snapshot_binding(
                    manifest,
                    evidence_by_name,
                    admission_snapshot,
                    blockers,
                )
            report_bytes = captured_members.get(build_report_name)
            if report_bytes is not None:
                _validate_build_report_binding(
                    report_bytes,
                    manifest,
                    blockers,
                )
            repository_input_bytes = captured_members.get(
                cast(str, build["repository_input_member"])
            )
            if repository_input_bytes is not None:
                _validate_repository_input_binding(
                    repository_input_bytes,
                    manifest,
                    blockers,
                )
            ctest_bytes = captured_members.get(cast(str, build["ctest_member"]))
            if ctest_bytes is not None:
                _validate_ctest_evidence_binding(
                    ctest_bytes,
                    blockers,
                )
            if report_bytes is not None and ctest_bytes is not None:
                _validate_build_report_ctest_registry_binding(
                    report_bytes,
                    ctest_bytes,
                    blockers,
                )
            provenance_bytes = captured_members.get(
                cast(str, producer["provenance_member"])
            )
            if provenance_bytes is not None:
                producer_provenance = _validate_producer_provenance_binding(
                    provenance_bytes,
                    manifest,
                    blockers,
                )
                if producer_provenance is not None:
                    role_evidence["producer_provenance"] = producer_provenance
            if blockers:
                return _verification_result(
                    blockers,
                    manifest,
                    manifest_evidence,
                    member_evidence,
                    caller_claims,
                    role_evidence=role_evidence,
                )
            if (
                producer.get("policy_id")
                == PRE_LIVE_DETERMINISTIC_JOURNEY_PRODUCER_ID
            ):
                journey_member = cast(str, producer["output_member"])
                journey_bytes = captured_members.get(journey_member)
                if journey_bytes is None:
                    _add_blocker(
                        blockers,
                        "deterministic_journey_bundle_missing",
                        "$.producer.output_member",
                        "deterministic journey producer output was not captured",
                    )
                else:
                    journey_verification = (
                        _verify_bound_deterministic_journey_bundle(
                            journey_bytes,
                            build_report_bytes=report_bytes,
                            manifest=manifest,
                            limits=effective_limits,
                            node_executable=node_executable,
                        )
                    )
                    role_evidence["deterministic_journeys"] = (
                        journey_verification
                    )
                    if journey_verification.get("ok") is not True:
                        blockers.extend(
                            cast(
                                list[dict[str, object]],
                                journey_verification.get("blockers", []),
                            )
                        )
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
        _add_blocker(
            blockers,
            "invalid_zip",
            "$",
            "bundle is not a readable ZIP archive",
            error=str(exc),
        )

    return _verification_result(
        blockers,
        manifest,
        manifest_evidence,
        member_evidence,
        caller_claims,
        role_evidence=role_evidence,
    )


def verify_downloaded_pre_live_artifact(
    artifact: bytes | bytearray | memoryview,
    *,
    limits: PreLiveArtifactLimits | None = None,
    bundle_member_name: str = GITHUB_ARTIFACT_BUNDLE_MEMBER_NAME,
    admission_snapshot: PreLiveBuildAdmissionSnapshot | None = None,
    node_executable: Path | str | None = None,
) -> dict[str, object]:
    """Verify direct bundle bytes or GitHub's one-file artifact ZIP wrapper."""

    effective_limits = limits or PreLiveArtifactLimits()
    _require_admission_snapshot_type(admission_snapshot)
    direct = verify_pre_live_artifact_bundle(
        artifact,
        limits=effective_limits,
        admission_snapshot=admission_snapshot,
        node_executable=node_executable,
    )
    if direct["ok"] is True:
        return {
            **direct,
            "delivery": {
                "kind": "direct",
                "member": None,
                "sha256": direct["manifest_evidence"]["bundle_sha256"],
                "size_bytes": direct["manifest_evidence"]["bundle_size_bytes"],
            },
        }

    blockers: list[dict[str, object]] = []
    if not isinstance(artifact, (bytes, bytearray, memoryview)):
        _add_blocker(
            blockers,
            "invalid_github_artifact_type",
            "$.delivery",
            "GitHub artifact download must be bytes-like",
        )
        return _download_verification_result(blockers, direct)
    artifact_bytes = bytes(artifact)
    if len(artifact_bytes) > effective_limits.max_archive_bytes:
        _add_blocker(
            blockers,
            "github_artifact_size_limit_exceeded",
            "$.delivery",
            "GitHub artifact wrapper exceeds max_archive_bytes",
            actual=len(artifact_bytes),
            maximum=effective_limits.max_archive_bytes,
        )
        return _download_verification_result(blockers, direct)
    if (
        not isinstance(bundle_member_name, str)
        or _path_error(bundle_member_name) is not None
        or "/" in bundle_member_name
        or not bundle_member_name.endswith(".zip")
    ):
        _add_blocker(
            blockers,
            "invalid_github_bundle_member_name",
            "$.delivery.member",
            "GitHub bundle member name must be one safe root ZIP filename",
        )
        return _download_verification_result(blockers, direct)
    if _archive_entry_count_error(
        artifact_bytes,
        maximum=1,
    ) is not None:
        _add_blocker(
            blockers,
            "github_artifact_entry_count_mismatch",
            "$.delivery",
            "GitHub artifact wrapper must contain exactly one file",
            maximum=1,
        )
        return _download_verification_result(blockers, direct)
    framing_error = _archive_framing_error(
        artifact_bytes,
        require_exact_local_flags=True,
        allowed_general_purpose_flags=(
            _GITHUB_ARTIFACT_ALLOWED_GENERAL_PURPOSE_FLAGS
        ),
    )
    if framing_error is not None:
        _add_blocker(
            blockers,
            "noncanonical_github_artifact_framing",
            "$.delivery",
            framing_error,
        )
        return _download_verification_result(blockers, direct)

    inner_bundle: bytes | None = None
    try:
        with zipfile.ZipFile(io.BytesIO(artifact_bytes), mode="r") as archive:
            infos = archive.infolist()
            framing_error = _archive_framing_error(
                artifact_bytes,
                parsed_entry_count=len(infos),
                require_exact_local_flags=True,
                allowed_general_purpose_flags=(
                    _GITHUB_ARTIFACT_ALLOWED_GENERAL_PURPOSE_FLAGS
                ),
            )
            if framing_error is not None:
                _add_blocker(
                    blockers,
                    "noncanonical_github_artifact_framing",
                    "$.delivery",
                    framing_error,
                )
                return _download_verification_result(blockers, direct)
            if len(infos) != 1:
                _add_blocker(
                    blockers,
                    "github_artifact_entry_count_mismatch",
                    "$.delivery",
                    "GitHub artifact wrapper must contain exactly one file",
                    actual=len(infos),
                )
            else:
                info = infos[0]
                if info.filename != bundle_member_name or info.is_dir():
                    _add_blocker(
                        blockers,
                        "github_artifact_member_mismatch",
                        "$.delivery.member",
                        "GitHub artifact wrapper contains the wrong bundle member",
                        expected=bundle_member_name,
                        actual=info.filename,
                    )
                if info.flag_bits & 0x0001:
                    _add_blocker(
                        blockers,
                        "encrypted_github_artifact_member",
                        "$.delivery.member",
                        "GitHub artifact bundle member must not be encrypted",
                    )
                if info.compress_type not in _ALLOWED_COMPRESSION:
                    _add_blocker(
                        blockers,
                        "unsupported_github_artifact_compression",
                        "$.delivery.member",
                        "GitHub artifact bundle member uses unsupported compression",
                        actual=info.compress_type,
                    )
                if info.file_size > effective_limits.max_archive_bytes:
                    _add_blocker(
                        blockers,
                        "github_bundle_member_size_limit_exceeded",
                        "$.delivery.member",
                        "inner pre-live bundle exceeds max_archive_bytes",
                        actual=info.file_size,
                        maximum=effective_limits.max_archive_bytes,
                    )
                if (
                    info.compress_size > 0
                    and info.file_size
                    > info.compress_size * effective_limits.max_compression_ratio
                ):
                    _add_blocker(
                        blockers,
                        "github_bundle_compression_ratio_exceeded",
                        "$.delivery.member",
                        "inner pre-live bundle exceeds the compression ratio limit",
                    )
                unix_mode = (info.external_attr >> 16) & 0xFFFF
                unix_file_type = stat.S_IFMT(unix_mode)
                if unix_file_type not in {0, stat.S_IFREG}:
                    _add_blocker(
                        blockers,
                        "github_artifact_member_not_regular",
                        "$.delivery.member",
                        "GitHub artifact bundle member must be a regular file",
                    )
                if not blockers:
                    with archive.open(info, mode="r") as member:
                        inner_bundle = member.read(
                            effective_limits.max_archive_bytes + 1
                        )
                    if len(inner_bundle) != info.file_size:
                        _add_blocker(
                            blockers,
                            "github_artifact_member_size_mismatch",
                            "$.delivery.member",
                            "GitHub artifact member size changed while reading",
                            expected=info.file_size,
                            actual=len(inner_bundle),
                        )
                    if len(inner_bundle) > effective_limits.max_archive_bytes:
                        _add_blocker(
                            blockers,
                            "github_bundle_member_size_limit_exceeded",
                            "$.delivery.member",
                            "inner pre-live bundle exceeds max_archive_bytes",
                            actual=len(inner_bundle),
                            maximum=effective_limits.max_archive_bytes,
                        )
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
        _add_blocker(
            blockers,
            "invalid_github_artifact_wrapper",
            "$.delivery",
            "GitHub artifact download is not a valid one-file ZIP wrapper",
            error=str(exc),
        )

    if blockers or inner_bundle is None:
        return _download_verification_result(blockers, direct)
    inner = verify_pre_live_artifact_bundle(
        inner_bundle,
        limits=effective_limits,
        admission_snapshot=admission_snapshot,
        node_executable=node_executable,
    )
    delivery = {
        "kind": "github_artifact_zip",
        "member": bundle_member_name,
        "sha256": hashlib.sha256(artifact_bytes).hexdigest(),
        "size_bytes": len(artifact_bytes),
        "bundle_sha256": hashlib.sha256(inner_bundle).hexdigest(),
        "bundle_size_bytes": len(inner_bundle),
    }
    return {**inner, "delivery": delivery}


def _download_verification_result(
    blockers: list[dict[str, object]],
    direct: Mapping[str, object],
) -> dict[str, object]:
    return {
        "ok": False,
        "status": "blocked",
        "blockers": blockers,
        "manifest": None,
        "manifest_evidence": {},
        "member_evidence": [],
        "role_evidence": {},
        "delivery": {
            "kind": "invalid",
            "direct_blockers": direct.get("blockers"),
        },
    }


build_micromachine_pre_live_artifact_bundle = build_pre_live_artifact_bundle
verify_micromachine_pre_live_artifact_bundle = verify_pre_live_artifact_bundle
build_micromachine_pre_live_artifact = build_pre_live_artifact_bundle
verify_micromachine_pre_live_artifact = verify_pre_live_artifact_bundle


def _coerce_metadata(
    metadata: PreLiveArtifactMetadata | Mapping[str, object],
) -> PreLiveArtifactMetadata:
    if isinstance(metadata, PreLiveArtifactMetadata):
        return metadata
    if not isinstance(metadata, Mapping):
        raise TypeError("metadata must be PreLiveArtifactMetadata or a mapping")

    flat_keys = {field.name for field in fields(PreLiveArtifactMetadata)}
    if set(metadata) == flat_keys:
        return PreLiveArtifactMetadata(**dict(metadata))  # type: ignore[arg-type]

    nested_keys = {
        "authority",
        "repository",
        "workflow",
        "run",
        "job",
        "artifact",
        "build",
        "producer",
    }
    if set(metadata) != nested_keys:
        raise ValueError(
            "metadata fields must match either the flat metadata dataclass or "
            "the nested trusted metadata schema"
        )
    authority = _required_mapping(metadata, "authority")
    authority_pull_request = _required_mapping(authority, "pull_request")
    authority_closing_issue = _required_mapping(authority, "closing_issue")
    repository = _required_mapping(metadata, "repository")
    workflow = _required_mapping(metadata, "workflow")
    run = _required_mapping(metadata, "run")
    job = _required_mapping(metadata, "job")
    artifact = _required_mapping(metadata, "artifact")
    build = _required_mapping(metadata, "build")
    producer = _required_mapping(metadata, "producer")
    _require_builder_keys(authority, _AUTHORITY_KEYS, "metadata.authority")
    _require_builder_keys(
        authority_pull_request,
        _AUTHORITY_PULL_REQUEST_KEYS,
        "metadata.authority.pull_request",
    )
    _require_builder_keys(
        authority_closing_issue,
        _AUTHORITY_CLOSING_ISSUE_KEYS,
        "metadata.authority.closing_issue",
    )
    _require_builder_keys(repository, _REPOSITORY_KEYS, "metadata.repository")
    _require_builder_keys(workflow, _WORKFLOW_KEYS, "metadata.workflow")
    _require_builder_keys(run, _RUN_KEYS, "metadata.run")
    _require_builder_keys(job, _JOB_KEYS, "metadata.job")
    _require_builder_keys(
        artifact,
        frozenset({"logical_name", "member"}),
        "metadata.artifact",
    )
    _require_builder_keys(
        build,
        frozenset(
            {
                "report_identity",
                "report_member",
                "binary_member",
                "repository_input_member",
                "repository_input_identity",
                "ctest_member",
            }
        ),
        "metadata.build",
    )
    _require_builder_keys(
        producer,
        frozenset(
            {
                "policy_id",
                "policy_member",
                "executable_member",
                "argv_member",
                "output_member",
                "provenance_member",
            }
        ),
        "metadata.producer",
    )
    return PreLiveArtifactMetadata(
        authority_scope=cast(str, authority.get("scope")),
        release_authoritative=cast(
            bool,
            authority.get("release_authoritative"),
        ),
        authority_event=cast(str, authority.get("event")),
        pull_request_database_id=cast(
            int,
            authority_pull_request.get("database_id"),
        ),
        pull_request_number=cast(
            int,
            authority_pull_request.get("number"),
        ),
        pull_request_head_sha=cast(
            str,
            authority_pull_request.get("head_sha"),
        ),
        pull_request_head_ref=cast(
            str,
            authority_pull_request.get("head_ref"),
        ),
        pull_request_head_repository_id=cast(
            int,
            authority_pull_request.get("head_repository_id"),
        ),
        closing_issue_repository_full_name=cast(
            str,
            authority_closing_issue.get("repository_full_name"),
        ),
        closing_issue_repository_database_id=cast(
            int,
            authority_closing_issue.get("repository_database_id"),
        ),
        closing_issue_database_id=cast(
            int,
            authority_closing_issue.get("database_id"),
        ),
        closing_issue_number=cast(
            int,
            authority_closing_issue.get("number"),
        ),
        repository_full_name=cast(str, repository.get("full_name")),
        repository_database_id=cast(int, repository.get("database_id")),
        repository_commit=cast(str, repository.get("commit_sha")),
        workflow_id=cast(int, workflow.get("id")),
        workflow_path=cast(str, workflow.get("path")),
        workflow_ref=cast(str, workflow.get("ref")),
        workflow_sha=cast(str, workflow.get("sha")),
        run_id=cast(int, run.get("id")),
        run_attempt=cast(int, run.get("attempt")),
        job_id=cast(int, job.get("id")),
        job_name=cast(str, job.get("name")),
        artifact_logical_name=cast(str, artifact.get("logical_name")),
        artifact_member=cast(str, artifact.get("member")),
        build_report_identity=cast(str, build.get("report_identity")),
        build_report_member=cast(str, build.get("report_member")),
        binary_member=cast(str, build.get("binary_member")),
        repository_input_member=cast(
            str,
            build.get("repository_input_member"),
        ),
        repository_input_identity=cast(
            str,
            build.get("repository_input_identity"),
        ),
        ctest_member=cast(str, build.get("ctest_member")),
        producer_policy_id=cast(str, producer.get("policy_id")),
        producer_policy_member=cast(str, producer.get("policy_member")),
        producer_executable_member=cast(
            str,
            producer.get("executable_member"),
        ),
        producer_argv_member=cast(str, producer.get("argv_member")),
        producer_output_member=cast(str, producer.get("output_member")),
        producer_provenance_member=cast(
            str,
            producer.get("provenance_member"),
        ),
    )


def _required_mapping(
    value: Mapping[str, object],
    key: str,
) -> Mapping[str, object]:
    nested = value.get(key)
    if not isinstance(nested, Mapping):
        raise ValueError(f"metadata.{key} must be a mapping")
    return cast(Mapping[str, object], nested)


def _require_builder_keys(
    value: Mapping[str, object],
    expected: frozenset[str],
    path: str,
) -> None:
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{path} fields mismatch: "
            f"missing={sorted(expected - actual)!r} "
            f"unexpected={sorted(actual - expected)!r}"
        )


def _validate_metadata(
    metadata: PreLiveArtifactMetadata,
) -> list[dict[str, object]]:
    blockers: list[dict[str, object]] = []
    if metadata.authority_scope != PRE_LIVE_CANDIDATE_AUTHORITY_SCOPE:
        _invalid_field(
            blockers,
            "$.authority.scope",
            "pre-live candidate authority scope must be candidate_pr",
        )
    if metadata.release_authoritative is not False:
        _invalid_field(
            blockers,
            "$.authority.release_authoritative",
            "candidate_pr evidence must never be release-authoritative",
        )
    if metadata.authority_event != "pull_request":
        _invalid_field(
            blockers,
            "$.authority.event",
            "candidate_pr evidence must come from a pull_request event",
        )
    _require_positive_int(
        metadata.pull_request_database_id,
        "$.authority.pull_request.database_id",
        blockers,
    )
    _require_positive_int(
        metadata.pull_request_number,
        "$.authority.pull_request.number",
        blockers,
    )
    _require_sha40(
        metadata.pull_request_head_sha,
        "$.authority.pull_request.head_sha",
        blockers,
    )
    _require_text(
        metadata.pull_request_head_ref,
        "$.authority.pull_request.head_ref",
        blockers,
        maximum=256,
    )
    _require_positive_int(
        metadata.pull_request_head_repository_id,
        "$.authority.pull_request.head_repository_id",
        blockers,
    )
    if (
        _REPOSITORY_RE.fullmatch(
            metadata.closing_issue_repository_full_name
        )
        is None
    ):
        _invalid_field(
            blockers,
            "$.authority.closing_issue.repository_full_name",
            "closing issue repository full name must be owner/name",
        )
    _require_positive_int(
        metadata.closing_issue_repository_database_id,
        "$.authority.closing_issue.repository_database_id",
        blockers,
    )
    _require_positive_int(
        metadata.closing_issue_database_id,
        "$.authority.closing_issue.database_id",
        blockers,
    )
    _require_positive_int(
        metadata.closing_issue_number,
        "$.authority.closing_issue.number",
        blockers,
    )
    if _REPOSITORY_RE.fullmatch(metadata.repository_full_name) is None:
        _invalid_field(
            blockers,
            "$.repository.full_name",
            "repository full name must be owner/name",
        )
    _require_positive_int(
        metadata.repository_database_id,
        "$.repository.database_id",
        blockers,
    )
    _require_sha40(
        metadata.repository_commit,
        "$.repository.commit_sha",
        blockers,
    )
    if (
        _SHA40_RE.fullmatch(metadata.pull_request_head_sha) is not None
        and _SHA40_RE.fullmatch(metadata.repository_commit) is not None
        and metadata.pull_request_head_sha != metadata.repository_commit
    ):
        _add_blocker(
            blockers,
            "authority_repository_sha_mismatch",
            "$.authority.pull_request.head_sha",
            "pull-request head SHA must equal the exact repository commit",
        )
    if (
        type(metadata.pull_request_head_repository_id) is int
        and type(metadata.repository_database_id) is int
        and metadata.pull_request_head_repository_id != metadata.repository_database_id
    ):
        _add_blocker(
            blockers,
            "authority_repository_id_mismatch",
            "$.authority.pull_request.head_repository_id",
            "pull-request head repository must be the attested repository",
        )
    if (
        isinstance(metadata.closing_issue_repository_full_name, str)
        and isinstance(metadata.repository_full_name, str)
        and metadata.closing_issue_repository_full_name
        != metadata.repository_full_name
    ):
        _add_blocker(
            blockers,
            "closing_issue_repository_name_mismatch",
            "$.authority.closing_issue.repository_full_name",
            "closing issue must belong to the attested repository",
        )
    if (
        type(metadata.closing_issue_repository_database_id) is int
        and type(metadata.repository_database_id) is int
        and metadata.closing_issue_repository_database_id
        != metadata.repository_database_id
    ):
        _add_blocker(
            blockers,
            "closing_issue_repository_id_mismatch",
            "$.authority.closing_issue.repository_database_id",
            "closing issue must belong to the attested repository",
        )
    _require_positive_int(metadata.workflow_id, "$.workflow.id", blockers)
    if (
        _path_error(metadata.workflow_path) is not None
        or not metadata.workflow_path.startswith(".github/workflows/")
        or not metadata.workflow_path.endswith((".yml", ".yaml"))
    ):
        _invalid_field(
            blockers,
            "$.workflow.path",
            "workflow path must be a safe .github/workflows YAML path",
        )
    _require_text(
        metadata.workflow_ref,
        "$.workflow.ref",
        blockers,
        maximum=1024,
    )
    expected_workflow_ref_prefix = (
        f"{metadata.repository_full_name}/{metadata.workflow_path}@refs/"
    )
    if isinstance(metadata.workflow_ref, str) and (
        not metadata.workflow_ref.startswith(expected_workflow_ref_prefix)
        or len(metadata.workflow_ref) == len(expected_workflow_ref_prefix)
    ):
        _add_blocker(
            blockers,
            "workflow_ref_binding_mismatch",
            "$.workflow.ref",
            "workflow ref must bind the exact repository and workflow path",
            expected_prefix=expected_workflow_ref_prefix,
            actual=metadata.workflow_ref,
        )
    _require_sha40(metadata.workflow_sha, "$.workflow.sha", blockers)
    _require_positive_int(metadata.run_id, "$.run.id", blockers)
    _require_positive_int(metadata.run_attempt, "$.run.attempt", blockers)
    _require_positive_int(metadata.job_id, "$.job.id", blockers)
    _require_text(metadata.job_name, "$.job.name", blockers, maximum=256)
    if (
        not isinstance(metadata.artifact_logical_name, str)
        or _SAFE_LOGICAL_NAME_RE.fullmatch(metadata.artifact_logical_name) is None
    ):
        _invalid_field(
            blockers,
            "$.artifact.logical_name",
            "artifact logical name contains unsafe characters",
        )
    if (
        not isinstance(metadata.build_report_identity, str)
        or _SHA256_IDENTITY_RE.fullmatch(metadata.build_report_identity) is None
    ):
        _invalid_field(
            blockers,
            "$.build.report_identity",
            "build report identity must be sha256:<64 lowercase hex>",
        )
    if (
        not isinstance(metadata.repository_input_identity, str)
        or _SHA256_IDENTITY_RE.fullmatch(metadata.repository_input_identity) is None
    ):
        _invalid_field(
            blockers,
            "$.build.repository_input_identity",
            "repository input identity must be sha256:<64 lowercase hex>",
        )
    _require_text(
        metadata.producer_policy_id,
        "$.producer.policy_id",
        blockers,
        maximum=256,
    )

    member_fields = {
        "$.artifact.member": metadata.artifact_member,
        "$.build.report_member": metadata.build_report_member,
        "$.build.binary_member": metadata.binary_member,
        "$.build.repository_input_member": metadata.repository_input_member,
        "$.build.ctest_member": metadata.ctest_member,
        "$.producer.policy_member": metadata.producer_policy_member,
        "$.producer.executable_member": metadata.producer_executable_member,
        "$.producer.argv_member": metadata.producer_argv_member,
        "$.producer.output_member": metadata.producer_output_member,
        "$.producer.provenance_member": metadata.producer_provenance_member,
    }
    for path, name in member_fields.items():
        error = _path_error(name)
        if error is not None or name == PRE_LIVE_ARTIFACT_MANIFEST_NAME:
            _invalid_field(
                blockers,
                path,
                error or "manifest.json is reserved",
            )
    if (
        isinstance(metadata.artifact_member, str)
        and isinstance(metadata.producer_output_member, str)
        and metadata.artifact_member != metadata.producer_output_member
    ):
        _add_blocker(
            blockers,
            "artifact_output_binding_mismatch",
            "$.artifact.member",
            "artifact member must be the local producer output member",
        )
    distinct_roles = (
        metadata.build_report_member,
        metadata.binary_member,
        metadata.repository_input_member,
        metadata.ctest_member,
        metadata.producer_policy_member,
        metadata.producer_executable_member,
        metadata.producer_argv_member,
        metadata.producer_output_member,
        metadata.producer_provenance_member,
    )
    if all(isinstance(name, str) for name in distinct_roles) and len(
        set(distinct_roles)
    ) != len(distinct_roles):
        _add_blocker(
            blockers,
            "duplicate_role_member",
            "$",
            "build and producer roles must reference distinct payload members",
        )
    return blockers


def _normalize_builder_members(
    members: Mapping[str, bytes | bytearray | memoryview],
    limits: PreLiveArtifactLimits,
) -> dict[str, bytes]:
    if not isinstance(members, Mapping):
        raise TypeError("members must be a mapping")
    if len(members) + 1 > limits.max_entries:
        raise ValueError("bundle exceeds max_entries")
    normalized: dict[str, bytes] = {}
    total = 0
    for name, value in members.items():
        error = _path_error(name)
        if error is not None:
            raise ValueError(f"invalid member path {name!r}: {error}")
        if name == PRE_LIVE_ARTIFACT_MANIFEST_NAME:
            raise ValueError("manifest.json is reserved for the canonical manifest")
        if not isinstance(value, (bytes, bytearray, memoryview)):
            raise TypeError(f"member {name!r} must be bytes-like")
        payload = bytes(value)
        if len(payload) > limits.max_member_uncompressed_bytes:
            raise ValueError(f"member {name!r} exceeds max_member_uncompressed_bytes")
        total += len(payload)
        if total > limits.max_total_uncompressed_bytes:
            raise ValueError("members exceed max_total_uncompressed_bytes")
        normalized[name] = payload
    return normalized


def _require_admission_snapshot_type(
    snapshot: PreLiveBuildAdmissionSnapshot | None,
) -> None:
    if snapshot is not None and not isinstance(
        snapshot,
        PreLiveBuildAdmissionSnapshot,
    ):
        raise TypeError(
            "admission_snapshot must be PreLiveBuildAdmissionSnapshot or None"
        )


def _validate_builder_admission_snapshot(
    metadata: PreLiveArtifactMetadata,
    members: Mapping[str, bytes],
    snapshot: PreLiveBuildAdmissionSnapshot,
) -> None:
    admitted_roles = (
        (
            "supported build identity report",
            metadata.build_report_member,
            snapshot.build_report_bytes,
            snapshot.build_report_sha256,
        ),
        (
            "MicroMachine binary",
            metadata.binary_member,
            snapshot.binary_bytes,
            snapshot.binary_sha256,
        ),
    )
    for role, name, admitted_bytes, admitted_sha256 in admitted_roles:
        payload = members[name]
        payload_sha256 = hashlib.sha256(payload).hexdigest()
        if (
            len(payload) != len(admitted_bytes)
            or not hmac.compare_digest(payload_sha256, admitted_sha256)
            or not hmac.compare_digest(payload, admitted_bytes)
        ):
            raise ValueError(
                f"{role} differs from the immutable admission snapshot"
            )


def _build_manifest(
    metadata: PreLiveArtifactMetadata,
    members: Mapping[str, bytes],
) -> dict[str, object]:
    role_names = {
        metadata.artifact_member,
        metadata.build_report_member,
        metadata.binary_member,
        metadata.repository_input_member,
        metadata.ctest_member,
        metadata.producer_policy_member,
        metadata.producer_executable_member,
        metadata.producer_argv_member,
        metadata.producer_output_member,
        metadata.producer_provenance_member,
    }
    missing_roles = sorted(role_names - set(members))
    if missing_roles:
        raise ValueError(
            "role members are missing from members: " + ", ".join(missing_roles)
        )

    descriptors = [
        {
            "name": name,
            "sha256": hashlib.sha256(members[name]).hexdigest(),
            "size_bytes": len(members[name]),
        }
        for name in sorted(members)
    ]
    by_name = {cast(str, item["name"]): item for item in descriptors}

    def digest(name: str) -> str:
        return cast(str, by_name[name]["sha256"])

    def size(name: str) -> int:
        return cast(int, by_name[name]["size_bytes"])

    return {
        "schema": PRE_LIVE_ARTIFACT_SCHEMA,
        "schema_version": PRE_LIVE_ARTIFACT_SCHEMA_VERSION,
        "authority": {
            "scope": metadata.authority_scope,
            "release_authoritative": metadata.release_authoritative,
            "event": metadata.authority_event,
            "pull_request": {
                "database_id": metadata.pull_request_database_id,
                "number": metadata.pull_request_number,
                "head_sha": metadata.pull_request_head_sha,
                "head_ref": metadata.pull_request_head_ref,
                "head_repository_id": metadata.pull_request_head_repository_id,
            },
            "closing_issue": {
                "repository_full_name": (
                    metadata.closing_issue_repository_full_name
                ),
                "repository_database_id": (
                    metadata.closing_issue_repository_database_id
                ),
                "database_id": metadata.closing_issue_database_id,
                "number": metadata.closing_issue_number,
            },
        },
        "repository": {
            "full_name": metadata.repository_full_name,
            "database_id": metadata.repository_database_id,
            "commit_sha": metadata.repository_commit,
        },
        "workflow": {
            "id": metadata.workflow_id,
            "path": metadata.workflow_path,
            "ref": metadata.workflow_ref,
            "sha": metadata.workflow_sha,
        },
        "run": {
            "id": metadata.run_id,
            "attempt": metadata.run_attempt,
        },
        "job": {
            "id": metadata.job_id,
            "name": metadata.job_name,
        },
        "artifact": {
            "logical_name": metadata.artifact_logical_name,
            "member": metadata.artifact_member,
            "sha256": digest(metadata.artifact_member),
            "size_bytes": size(metadata.artifact_member),
        },
        "build": {
            "report_identity": metadata.build_report_identity,
            "report_member": metadata.build_report_member,
            "report_sha256": digest(metadata.build_report_member),
            "binary_member": metadata.binary_member,
            "binary_sha256": digest(metadata.binary_member),
            "repository_input_member": metadata.repository_input_member,
            "repository_input_identity": metadata.repository_input_identity,
            "repository_input_sha256": digest(metadata.repository_input_member),
            "ctest_member": metadata.ctest_member,
            "ctest_sha256": digest(metadata.ctest_member),
        },
        "producer": {
            "policy_id": metadata.producer_policy_id,
            "policy_member": metadata.producer_policy_member,
            "policy_sha256": digest(metadata.producer_policy_member),
            "executable_member": metadata.producer_executable_member,
            "executable_sha256": digest(metadata.producer_executable_member),
            "argv_member": metadata.producer_argv_member,
            "argv_sha256": digest(metadata.producer_argv_member),
            "output_member": metadata.producer_output_member,
            "output_sha256": digest(metadata.producer_output_member),
            "provenance_member": metadata.producer_provenance_member,
            "provenance_sha256": digest(metadata.producer_provenance_member),
        },
        "members": descriptors,
    }


def _write_deterministic_member(
    archive: zipfile.ZipFile,
    name: str,
    payload: bytes,
    *,
    mode: int = _REGULAR_FILE_MODE,
) -> None:
    info = zipfile.ZipInfo(name, date_time=DETERMINISTIC_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.create_version = 20
    info.extract_version = 20
    info.external_attr = mode << 16
    info.internal_attr = 0
    info.extra = b""
    info.comment = b""
    archive.writestr(info, payload)


@dataclass(frozen=True)
class _ArchiveCentralEntry:
    local_offset: int
    extract_version: int
    flags: int
    compression: int
    modified_time: int
    modified_date: int
    crc32: int
    compressed_size: int
    uncompressed_size: int
    filename: bytes


def _zip_extra_field_error(extra: bytes) -> str | None:
    offset = 0
    while offset < len(extra):
        if offset + _ZIP_EXTRA_FIELD_HEADER.size > len(extra):
            return "ZIP extra field header is truncated"
        field_id, field_size = _ZIP_EXTRA_FIELD_HEADER.unpack_from(
            extra,
            offset,
        )
        offset += _ZIP_EXTRA_FIELD_HEADER.size
        field_end = offset + field_size
        if field_end > len(extra):
            return "ZIP extra field payload is truncated"
        if field_id == _ZIP64_EXTRA_FIELD_ID:
            return "ZIP64 extra fields are not allowed"
        offset = field_end
    return None


def _archive_entry_count_error(
    bundle: bytes,
    *,
    maximum: int,
) -> str | None:
    if maximum < 0:
        return "ZIP entry limit is invalid"
    if len(bundle) < _END_CENTRAL_DIRECTORY.size:
        return None
    eocd_offset = len(bundle) - _END_CENTRAL_DIRECTORY.size
    if bundle[eocd_offset : eocd_offset + 4] != b"PK\x05\x06":
        return None
    total_entries = _END_CENTRAL_DIRECTORY.unpack_from(
        bundle,
        eocd_offset,
    )[4]
    if total_entries > maximum:
        return "ZIP end-of-central-directory entry count exceeds the limit"
    return None


def _archive_framing_error(
    bundle: bytes,
    *,
    parsed_entry_count: int | None = None,
    require_exact_local_flags: bool = False,
    allowed_general_purpose_flags: int | None = None,
    require_empty_extra_fields: bool = False,
) -> str | None:
    if len(bundle) < _END_CENTRAL_DIRECTORY.size:
        return "ZIP is shorter than an end-of-central-directory record"
    if not bundle.startswith(b"PK\x03\x04"):
        return "ZIP must start with a local file header and have no prefix data"
    eocd_offset = len(bundle) - _END_CENTRAL_DIRECTORY.size
    if bundle[eocd_offset : eocd_offset + 4] != b"PK\x05\x06":
        if b"PK\x05\x06" in bundle:
            return "ZIP must not contain trailing bytes after the declared EOCD"
        return "ZIP end-of-central-directory record is missing"
    (
        signature,
        disk_number,
        central_disk,
        disk_entries,
        total_entries,
        central_size,
        central_offset,
        comment_size,
    ) = _END_CENTRAL_DIRECTORY.unpack_from(bundle, eocd_offset)
    if signature != b"PK\x05\x06":
        return "ZIP end-of-central-directory signature is invalid"
    if disk_number != 0 or central_disk != 0 or disk_entries != total_entries:
        return "multi-disk ZIP archives are not allowed"
    if parsed_entry_count is not None and total_entries != parsed_entry_count:
        return (
            "ZIP end-of-central-directory entry count does not match "
            "the parsed central directory"
        )
    if (
        total_entries == 0xFFFF
        or central_size == 0xFFFFFFFF
        or central_offset == 0xFFFFFFFF
    ):
        return "ZIP64 archives are not allowed"
    if comment_size != 0:
        return "ZIP archive comments are not allowed"
    if central_offset + central_size != eocd_offset:
        return "ZIP central directory has hidden or noncanonical framing data"
    offset = central_offset
    central_entries: list[_ArchiveCentralEntry] = []
    for _ in range(total_entries):
        if offset + _CENTRAL_DIRECTORY_HEADER.size > eocd_offset:
            return "ZIP central directory entry is truncated"
        fields = _CENTRAL_DIRECTORY_HEADER.unpack_from(bundle, offset)
        if fields[0] != b"PK\x01\x02":
            return "ZIP central directory entry signature is invalid"
        if (
            fields[2] >= _ZIP64_MINIMUM_EXTRACT_VERSION
            or fields[8] == 0xFFFFFFFF
            or fields[9] == 0xFFFFFFFF
            or fields[13] == 0xFFFF
            or fields[16] == 0xFFFFFFFF
        ):
            return "ZIP64 entries are not allowed"
        if fields[13] != 0:
            return "multi-disk ZIP entries are not allowed"
        if allowed_general_purpose_flags is not None:
            allowed_flags = allowed_general_purpose_flags
            if fields[4] != zipfile.ZIP_DEFLATED:
                allowed_flags &= ~0x0006
            if fields[3] & ~allowed_flags:
                return "ZIP entry uses unsupported general-purpose flags"
        if require_empty_extra_fields and fields[11] != 0:
            return "ZIP central extra fields are not allowed"
        entry_size = (
            _CENTRAL_DIRECTORY_HEADER.size
            + fields[10]
            + fields[11]
            + fields[12]
        )
        offset += entry_size
        if offset > eocd_offset:
            return "ZIP central directory entry is truncated"
        filename_start = (
            offset
            - fields[10]
            - fields[11]
            - fields[12]
        )
        filename_end = filename_start + fields[10]
        extra_end = filename_end + fields[11]
        raw_filename = bundle[filename_start:filename_end]
        if b"\x00" in raw_filename:
            return "ZIP central file name contains NUL"
        extra_error = _zip_extra_field_error(
            bundle[filename_end:extra_end]
        )
        if extra_error is not None:
            return extra_error
        central_entries.append(
            _ArchiveCentralEntry(
                local_offset=fields[16],
                extract_version=fields[2],
                flags=fields[3],
                compression=fields[4],
                modified_time=fields[5],
                modified_date=fields[6],
                crc32=fields[7],
                compressed_size=fields[8],
                uncompressed_size=fields[9],
                filename=raw_filename,
            )
        )
    if offset != eocd_offset:
        return "ZIP central directory has hidden or noncanonical framing data"
    ordered_entries = sorted(
        central_entries,
        key=lambda entry: entry.local_offset,
    )
    if ordered_entries and ordered_entries[0].local_offset != 0:
        return "ZIP local file data has hidden or noncanonical prefix framing"
    for index, entry in enumerate(ordered_entries):
        local_offset = entry.local_offset
        next_offset = (
            ordered_entries[index + 1].local_offset
            if index + 1 < len(ordered_entries)
            else central_offset
        )
        if (
            local_offset + _LOCAL_FILE_HEADER.size > next_offset
            or local_offset + _LOCAL_FILE_HEADER.size > len(bundle)
        ):
            return "ZIP local file header is truncated or overlaps another section"
        local_fields = _LOCAL_FILE_HEADER.unpack_from(bundle, local_offset)
        if local_fields[0] != b"PK\x03\x04":
            return "ZIP local file header signature is invalid"
        (
            _,
            local_extract_version,
            local_flags,
            local_compression,
            local_modified_time,
            local_modified_date,
            local_crc32,
            local_compressed_size,
            local_uncompressed_size,
            local_filename_size,
            local_extra_size,
        ) = local_fields
        if (
            local_extract_version >= _ZIP64_MINIMUM_EXTRACT_VERSION
            or local_compressed_size == 0xFFFFFFFF
            or local_uncompressed_size == 0xFFFFFFFF
        ):
            return "ZIP64 local file headers are not allowed"
        if local_extract_version != entry.extract_version:
            return "ZIP local and central extract versions differ"
        flag_delta = local_flags ^ entry.flags
        if flag_delta & 0x0008:
            return "ZIP local and central data-descriptor flags differ"
        if require_exact_local_flags and flag_delta:
            return "ZIP local and central general-purpose flags differ"
        if require_empty_extra_fields and local_extra_size != 0:
            return "ZIP local extra fields are not allowed"
        if local_compression != entry.compression:
            return "ZIP local and central compression methods differ"
        if (
            local_modified_time != entry.modified_time
            or local_modified_date != entry.modified_date
        ):
            return "ZIP local and central timestamps differ"
        filename_start = local_offset + _LOCAL_FILE_HEADER.size
        filename_end = filename_start + local_filename_size
        extra_end = filename_end + local_extra_size
        payload_end = (
            extra_end
            + entry.compressed_size
        )
        if payload_end > next_offset:
            return "ZIP local file data is truncated or overlaps another section"
        if bundle[filename_start:filename_end] != entry.filename:
            return "ZIP local and central file names differ"
        extra_error = _zip_extra_field_error(bundle[filename_end:extra_end])
        if extra_error is not None:
            return extra_error
        trailing = bundle[payload_end:next_offset]
        if entry.flags & 0x0008:
            local_descriptor_values = (
                local_crc32,
                local_compressed_size,
                local_uncompressed_size,
            )
            central_descriptor_values = (
                entry.crc32,
                entry.compressed_size,
                entry.uncompressed_size,
            )
            if local_descriptor_values not in {
                (0, 0, 0),
                central_descriptor_values,
            }:
                return "ZIP local data-descriptor values are inconsistent"
            descriptor = struct.pack(
                "<III",
                *central_descriptor_values,
            )
            if trailing not in {descriptor, b"PK\x07\x08" + descriptor}:
                return (
                    "ZIP data descriptor has hidden or noncanonical framing data"
                )
        elif (
            local_crc32 != entry.crc32
            or local_compressed_size != entry.compressed_size
            or local_uncompressed_size != entry.uncompressed_size
        ):
            return "ZIP local and central size or CRC-32 values differ"
        elif trailing:
            if index + 1 == len(ordered_entries):
                return (
                    "ZIP local file data is not contiguous with the "
                    "central directory"
                )
            return "ZIP local file data has hidden or noncanonical framing data"
    return None


def _local_header_error(
    bundle: bytes,
    info: zipfile.ZipInfo,
) -> str | None:
    offset = info.header_offset
    if offset < 0 or offset + _LOCAL_FILE_HEADER.size > len(bundle):
        return "local file header is outside the archive"
    (
        signature,
        extract_version,
        flags,
        compression,
        modified_time,
        modified_date,
        crc32,
        compressed_size,
        uncompressed_size,
        filename_size,
        extra_size,
    ) = _LOCAL_FILE_HEADER.unpack_from(bundle, offset)
    if signature != b"PK\x03\x04":
        return "local file header signature is invalid"
    if extract_version != 20 or modified_time != 0 or modified_date != 0x21:
        return "local file header metadata is not deterministic"
    if flags != info.flag_bits:
        return "local and central general-purpose flags differ"
    if flags & ~_ALLOWED_GENERAL_PURPOSE_FLAGS:
        return "local file header uses unsupported flags"
    if compression != info.compress_type:
        return "local and central compression methods differ"
    if crc32 != info.CRC:
        return "local and central CRC-32 values differ"
    if compressed_size != info.compress_size:
        return "local and central compressed sizes differ"
    if uncompressed_size != info.file_size:
        return "local and central uncompressed sizes differ"
    if extra_size != 0:
        return "local file header extra fields are not allowed"
    filename_start = offset + _LOCAL_FILE_HEADER.size
    filename_end = filename_start + filename_size
    payload_end = filename_end + extra_size + info.compress_size
    if payload_end > len(bundle):
        return "local file data extends beyond the archive"
    raw_filename = bundle[filename_start:filename_end]
    if b"\x00" in raw_filename:
        return "local file name contains NUL"
    encoding = "utf-8" if flags & 0x0800 else "cp437"
    try:
        local_filename = raw_filename.decode(encoding)
    except UnicodeDecodeError:
        return "local file name cannot be decoded"
    if local_filename != info.filename:
        return "local and central file names differ"
    return None


def _validate_central_directory(
    bundle: bytes,
    archive: zipfile.ZipFile,
    infos: list[zipfile.ZipInfo],
    blockers: list[dict[str, object]],
) -> None:
    offset = archive.start_dir
    expected_made_by = (3 << 8) | 20
    for index, info in enumerate(infos):
        path = f"$.archive[{index}]"
        if offset + _CENTRAL_DIRECTORY_HEADER.size > len(bundle):
            _add_blocker(
                blockers,
                "central_directory_mismatch",
                path,
                "central directory entry is truncated",
                name=info.filename,
            )
            return
        (
            signature,
            made_by,
            extract_version,
            flags,
            compression,
            modified_time,
            modified_date,
            crc32,
            compressed_size,
            uncompressed_size,
            filename_size,
            extra_size,
            comment_size,
            disk_number,
            internal_attr,
            external_attr,
            local_offset,
        ) = _CENTRAL_DIRECTORY_HEADER.unpack_from(bundle, offset)
        try:
            canonical_name = info.filename.encode("ascii")
        except UnicodeEncodeError:
            canonical_name = b""
        filename_start = offset + _CENTRAL_DIRECTORY_HEADER.size
        filename_end = filename_start + filename_size
        entry_end = filename_end + extra_size + comment_size
        raw_filename = bundle[filename_start:filename_end]
        canonical = (
            signature == b"PK\x01\x02"
            and made_by == expected_made_by
            and extract_version == 20
            and flags == 0
            and compression == zipfile.ZIP_STORED
            and modified_time == 0
            and modified_date == 0x21
            and crc32 == info.CRC
            and compressed_size == info.compress_size
            and uncompressed_size == info.file_size
            and filename_size == len(canonical_name)
            and extra_size == 0
            and comment_size == 0
            and disk_number == 0
            and internal_attr == 0
            and external_attr in _CANONICAL_EXTERNAL_ATTRS
            and local_offset == info.header_offset
            and raw_filename == canonical_name
            and b"\x00" not in raw_filename
            and entry_end <= len(bundle)
        )
        if not canonical:
            _add_blocker(
                blockers,
                "central_directory_mismatch",
                path,
                "central directory entry does not match the deterministic format",
                name=info.filename,
            )
        offset = entry_end
    eocd_offset = bundle.rfind(b"PK\x05\x06")
    if offset != eocd_offset:
        _add_blocker(
            blockers,
            "central_directory_mismatch",
            "$",
            "central directory entries are not contiguous with the ZIP footer",
        )


def _preflight_archive(
    bundle: bytes,
    archive: zipfile.ZipFile,
    infos: list[zipfile.ZipInfo],
    limits: PreLiveArtifactLimits,
    blockers: list[dict[str, object]],
) -> None:
    if archive.comment:
        _add_blocker(
            blockers,
            "archive_comment_forbidden",
            "$",
            "ZIP archive comments are not allowed",
        )
    if not infos:
        _add_blocker(
            blockers,
            "missing_manifest",
            "$",
            "ZIP archive is empty",
        )
        return
    if len(infos) > limits.max_entries:
        _add_blocker(
            blockers,
            "entry_count_limit_exceeded",
            "$",
            "ZIP entry count exceeds max_entries",
            actual=len(infos),
            maximum=limits.max_entries,
        )
    _validate_central_directory(bundle, archive, infos, blockers)

    actual_order = [info.filename for info in infos]
    expected_order = [
        PRE_LIVE_ARTIFACT_MANIFEST_NAME,
        *sorted(
            name for name in actual_order if name != PRE_LIVE_ARTIFACT_MANIFEST_NAME
        ),
    ]
    if actual_order != expected_order:
        _add_blocker(
            blockers,
            "noncanonical_entry_order",
            "$",
            "manifest.json must be first and payload members strictly sorted",
        )

    names: set[str] = set()
    folded_names: set[str] = set()
    manifest_count = 0
    total_uncompressed = 0
    expected_local_offset = 0
    for index, info in enumerate(infos):
        path = f"$.archive[{index}]"
        name = info.filename
        if name == PRE_LIVE_ARTIFACT_MANIFEST_NAME:
            manifest_count += 1
        if name in names or name.casefold() in folded_names:
            _add_blocker(
                blockers,
                "duplicate_entry_name",
                path,
                "ZIP contains a duplicate or case-colliding member name",
                name=name,
            )
        names.add(name)
        folded_names.add(name.casefold())

        path_error = _path_error(name)
        if path_error is not None:
            _add_blocker(
                blockers,
                "unsafe_entry_path",
                path,
                path_error,
                name=name,
            )
        if info.is_dir() or name.endswith("/"):
            _add_blocker(
                blockers,
                "unsupported_entry_type",
                path,
                "directory entries are not allowed",
                name=name,
            )
        file_type = _zip_file_type(info)
        if file_type not in (0, stat.S_IFREG):
            _add_blocker(
                blockers,
                "unsupported_entry_type",
                path,
                "symlink and special ZIP entries are not allowed",
                name=name,
                mode=oct((info.external_attr >> 16) & 0xFFFF),
            )
        if info.flag_bits & 0x1:
            _add_blocker(
                blockers,
                "encrypted_entry",
                path,
                "encrypted ZIP entries are not allowed",
                name=name,
            )
        unsupported_flags = info.flag_bits & ~_ALLOWED_GENERAL_PURPOSE_FLAGS
        if unsupported_flags:
            _add_blocker(
                blockers,
                "unsupported_zip_flags",
                path,
                "ZIP entry uses unsupported general-purpose flags",
                name=name,
                flags=info.flag_bits,
            )
        if info.compress_type not in _ALLOWED_COMPRESSION:
            _add_blocker(
                blockers,
                "unsupported_compression",
                path,
                "ZIP entry uses an unsupported compression method",
                name=name,
                compression=info.compress_type,
            )
        if info.extra:
            _add_blocker(
                blockers,
                "entry_extra_data_forbidden",
                path,
                "ZIP entry extra fields are not allowed",
                name=name,
            )
        if info.comment:
            _add_blocker(
                blockers,
                "entry_comment_forbidden",
                path,
                "ZIP entry comments are not allowed",
                name=name,
            )
        canonical_metadata = (
            info.date_time == DETERMINISTIC_ZIP_TIMESTAMP
            and info.create_system == 3
            and info.create_version == 20
            and info.extract_version == 20
            and info.reserved == 0
            and info.flag_bits == 0
            and info.compress_type == zipfile.ZIP_STORED
            and ((info.external_attr >> 16) & 0xFFFF)
            in _CANONICAL_FILE_MODES
            and info.internal_attr == 0
            and info.volume == 0
        )
        if not canonical_metadata:
            _add_blocker(
                blockers,
                "noncanonical_zip_metadata",
                path,
                "ZIP entry metadata does not match the deterministic format",
                name=name,
            )
        if info.header_offset != expected_local_offset:
            _add_blocker(
                blockers,
                "noncanonical_local_layout",
                path,
                "ZIP local members must be contiguous and ordered",
                name=name,
            )
        local_header_error = _local_header_error(bundle, info)
        if local_header_error is not None:
            _add_blocker(
                blockers,
                "local_header_mismatch",
                path,
                local_header_error,
                name=name,
            )
        filename_encoding = "utf-8" if info.flag_bits & 0x0800 else "cp437"
        try:
            filename_size = len(info.filename.encode(filename_encoding))
        except UnicodeEncodeError:
            filename_size = 0
        expected_local_offset = (
            info.header_offset
            + _LOCAL_FILE_HEADER.size
            + filename_size
            + info.compress_size
        )
        if info.compress_size > limits.max_member_compressed_bytes:
            _add_blocker(
                blockers,
                "compressed_size_limit_exceeded",
                path,
                "compressed member size exceeds the configured limit",
                name=name,
                actual=info.compress_size,
                maximum=limits.max_member_compressed_bytes,
            )
        if info.file_size > limits.max_member_uncompressed_bytes:
            _add_blocker(
                blockers,
                "uncompressed_size_limit_exceeded",
                path,
                "uncompressed member size exceeds the configured limit",
                name=name,
                actual=info.file_size,
                maximum=limits.max_member_uncompressed_bytes,
            )
        if (
            name == PRE_LIVE_ARTIFACT_MANIFEST_NAME
            and info.file_size > limits.max_manifest_bytes
        ):
            _add_blocker(
                blockers,
                "manifest_size_limit_exceeded",
                path,
                "manifest size exceeds max_manifest_bytes",
                actual=info.file_size,
                maximum=limits.max_manifest_bytes,
            )
        total_uncompressed += info.file_size
        if _compression_ratio(info) > limits.max_compression_ratio:
            _add_blocker(
                blockers,
                "compression_ratio_limit_exceeded",
                path,
                "member compression ratio exceeds the configured limit",
                name=name,
                ratio=_compression_ratio(info),
                maximum=limits.max_compression_ratio,
            )
    if total_uncompressed > limits.max_total_uncompressed_bytes:
        _add_blocker(
            blockers,
            "total_uncompressed_size_limit_exceeded",
            "$",
            "total uncompressed ZIP size exceeds the configured limit",
            actual=total_uncompressed,
            maximum=limits.max_total_uncompressed_bytes,
        )
    if expected_local_offset != archive.start_dir:
        _add_blocker(
            blockers,
            "noncanonical_local_layout",
            "$",
            "ZIP local members must be contiguous with the central directory",
        )
    if manifest_count == 0:
        _add_blocker(
            blockers,
            "missing_manifest",
            "$",
            "canonical root manifest.json is missing",
        )
    elif manifest_count != 1:
        _add_blocker(
            blockers,
            "duplicate_manifest",
            "$",
            "ZIP must contain exactly one root manifest.json",
            actual=manifest_count,
        )
    if infos and expected_local_offset != archive.start_dir:
        _add_blocker(
            blockers,
            "noncanonical_local_layout",
            "$",
            "ZIP contains hidden bytes between local members and central directory",
        )


def _read_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    maximum: int,
    capture: bool,
) -> tuple[bytes | None, dict[str, object]]:
    digest = hashlib.sha256()
    size = 0
    captured = bytearray() if capture else None
    error: str | None = None
    try:
        with archive.open(info, mode="r") as stream:
            while True:
                chunk = stream.read(min(READ_CHUNK_BYTES, maximum - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                if size > maximum:
                    raise ValueError("decompressed bytes exceed configured limit")
                digest.update(chunk)
                if captured is not None:
                    captured.extend(chunk)
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
        error = str(exc)
    evidence: dict[str, object] = {
        "name": info.filename,
        "mode": (info.external_attr >> 16) & 0xFFFF,
        "compression": info.compress_type,
        "compressed_size_bytes": info.compress_size,
        "declared_uncompressed_size_bytes": info.file_size,
        "size_bytes": size,
        "sha256": digest.hexdigest(),
        "error": error,
    }
    if error is not None:
        return None, evidence
    return (bytes(captured) if captured is not None else b""), evidence


def _parse_json(
    payload: bytes,
    blockers: list[dict[str, object]],
    path: str,
) -> object | None:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        _add_blocker(
            blockers,
            "invalid_json_utf8",
            path,
            "JSON bytes are not valid UTF-8",
            error=str(exc),
        )
        return None
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json,
        )
    except DuplicateJSONKeyError as exc:
        _add_blocker(
            blockers,
            "duplicate_json_key",
            path,
            "JSON contains a duplicate object key",
            error=str(exc),
        )
    except (ValueError, json.JSONDecodeError) as exc:
        _add_blocker(
            blockers,
            "invalid_json",
            path,
            "JSON could not be parsed",
            error=str(exc),
        )
    return None


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJSONKeyError(f"duplicate key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_json(value: str) -> object:
    raise ValueError(f"non-finite JSON number: {value}")


def _canonical_json_or_none(value: object) -> bytes | None:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError):
        return None


def _validate_manifest(
    manifest: Mapping[str, object],
    limits: PreLiveArtifactLimits,
) -> list[dict[str, object]]:
    blockers: list[dict[str, object]] = []
    _require_exact_keys(manifest, _ROOT_KEYS, "$", blockers)
    _require_equal(
        manifest.get("schema"),
        PRE_LIVE_ARTIFACT_SCHEMA,
        "$.schema",
        blockers,
    )
    _require_equal(
        manifest.get("schema_version"),
        PRE_LIVE_ARTIFACT_SCHEMA_VERSION,
        "$.schema_version",
        blockers,
        exact_int=True,
    )

    authority = _schema_mapping(
        manifest.get("authority"),
        "$.authority",
        _AUTHORITY_KEYS,
        blockers,
    )
    authority_pull_request = _schema_mapping(
        authority.get("pull_request"),
        "$.authority.pull_request",
        _AUTHORITY_PULL_REQUEST_KEYS,
        blockers,
    )
    authority_closing_issue = _schema_mapping(
        authority.get("closing_issue"),
        "$.authority.closing_issue",
        _AUTHORITY_CLOSING_ISSUE_KEYS,
        blockers,
    )
    repository = _schema_mapping(
        manifest.get("repository"),
        "$.repository",
        _REPOSITORY_KEYS,
        blockers,
    )
    workflow = _schema_mapping(
        manifest.get("workflow"),
        "$.workflow",
        _WORKFLOW_KEYS,
        blockers,
    )
    run = _schema_mapping(
        manifest.get("run"),
        "$.run",
        _RUN_KEYS,
        blockers,
    )
    job = _schema_mapping(
        manifest.get("job"),
        "$.job",
        _JOB_KEYS,
        blockers,
    )
    artifact = _schema_mapping(
        manifest.get("artifact"),
        "$.artifact",
        _ARTIFACT_KEYS,
        blockers,
    )
    build = _schema_mapping(
        manifest.get("build"),
        "$.build",
        _BUILD_KEYS,
        blockers,
    )
    producer = _schema_mapping(
        manifest.get("producer"),
        "$.producer",
        _PRODUCER_KEYS,
        blockers,
    )

    _require_equal(
        authority.get("scope"),
        PRE_LIVE_CANDIDATE_AUTHORITY_SCOPE,
        "$.authority.scope",
        blockers,
    )
    _require_equal(
        authority.get("release_authoritative"),
        False,
        "$.authority.release_authoritative",
        blockers,
    )
    _require_equal(
        authority.get("event"),
        "pull_request",
        "$.authority.event",
        blockers,
    )
    _require_positive_int(
        authority_pull_request.get("database_id"),
        "$.authority.pull_request.database_id",
        blockers,
    )
    _require_positive_int(
        authority_pull_request.get("number"),
        "$.authority.pull_request.number",
        blockers,
    )
    _require_sha40(
        authority_pull_request.get("head_sha"),
        "$.authority.pull_request.head_sha",
        blockers,
    )
    _require_text(
        authority_pull_request.get("head_ref"),
        "$.authority.pull_request.head_ref",
        blockers,
        maximum=256,
    )
    _require_positive_int(
        authority_pull_request.get("head_repository_id"),
        "$.authority.pull_request.head_repository_id",
        blockers,
    )
    if (
        _REPOSITORY_RE.fullmatch(
            _string(authority_closing_issue.get("repository_full_name"))
        )
        is None
    ):
        _invalid_field(
            blockers,
            "$.authority.closing_issue.repository_full_name",
            "closing issue repository full name must be owner/name",
        )
    _require_positive_int(
        authority_closing_issue.get("repository_database_id"),
        "$.authority.closing_issue.repository_database_id",
        blockers,
    )
    _require_positive_int(
        authority_closing_issue.get("database_id"),
        "$.authority.closing_issue.database_id",
        blockers,
    )
    _require_positive_int(
        authority_closing_issue.get("number"),
        "$.authority.closing_issue.number",
        blockers,
    )
    if _REPOSITORY_RE.fullmatch(_string(repository.get("full_name"))) is None:
        _invalid_field(
            blockers,
            "$.repository.full_name",
            "repository full name must be owner/name",
        )
    _require_positive_int(
        repository.get("database_id"),
        "$.repository.database_id",
        blockers,
    )
    _require_sha40(
        repository.get("commit_sha"),
        "$.repository.commit_sha",
        blockers,
    )
    if (
        isinstance(authority_pull_request.get("head_sha"), str)
        and isinstance(repository.get("commit_sha"), str)
        and authority_pull_request.get("head_sha") != repository.get("commit_sha")
    ):
        _add_blocker(
            blockers,
            "authority_repository_sha_mismatch",
            "$.authority.pull_request.head_sha",
            "pull-request head SHA must equal the exact repository commit",
        )
    if (
        type(authority_pull_request.get("head_repository_id")) is int
        and type(repository.get("database_id")) is int
        and authority_pull_request.get("head_repository_id")
        != repository.get("database_id")
    ):
        _add_blocker(
            blockers,
            "authority_repository_id_mismatch",
            "$.authority.pull_request.head_repository_id",
            "pull-request head repository must be the attested repository",
        )
    if (
        isinstance(authority_closing_issue.get("repository_full_name"), str)
        and isinstance(repository.get("full_name"), str)
        and authority_closing_issue.get("repository_full_name")
        != repository.get("full_name")
    ):
        _add_blocker(
            blockers,
            "closing_issue_repository_name_mismatch",
            "$.authority.closing_issue.repository_full_name",
            "closing issue must belong to the attested repository",
        )
    if (
        type(authority_closing_issue.get("repository_database_id")) is int
        and type(repository.get("database_id")) is int
        and authority_closing_issue.get("repository_database_id")
        != repository.get("database_id")
    ):
        _add_blocker(
            blockers,
            "closing_issue_repository_id_mismatch",
            "$.authority.closing_issue.repository_database_id",
            "closing issue must belong to the attested repository",
        )
    _require_positive_int(workflow.get("id"), "$.workflow.id", blockers)
    workflow_path = workflow.get("path")
    if (
        _path_error(workflow_path) is not None
        or not _string(workflow_path).startswith(".github/workflows/")
        or not _string(workflow_path).endswith((".yml", ".yaml"))
    ):
        _invalid_field(
            blockers,
            "$.workflow.path",
            "workflow path must be a safe .github/workflows YAML path",
        )
    _require_text(
        workflow.get("ref"),
        "$.workflow.ref",
        blockers,
        maximum=1024,
    )
    expected_workflow_ref_prefix = (
        f"{_string(repository.get('full_name'))}/{_string(workflow_path)}@refs/"
    )
    if (
        isinstance(workflow.get("ref"), str)
        and (
            not _string(workflow.get("ref")).startswith(
                expected_workflow_ref_prefix
            )
            or len(_string(workflow.get("ref")))
            == len(expected_workflow_ref_prefix)
        )
    ):
        _add_blocker(
            blockers,
            "workflow_ref_binding_mismatch",
            "$.workflow.ref",
            "workflow ref must bind the exact repository and workflow path",
            expected_prefix=expected_workflow_ref_prefix,
            actual=workflow.get("ref"),
        )
    _require_sha40(workflow.get("sha"), "$.workflow.sha", blockers)
    _require_positive_int(run.get("id"), "$.run.id", blockers)
    _require_positive_int(run.get("attempt"), "$.run.attempt", blockers)
    _require_positive_int(job.get("id"), "$.job.id", blockers)
    _require_text(job.get("name"), "$.job.name", blockers, maximum=256)
    logical_name = artifact.get("logical_name")
    if (
        not isinstance(logical_name, str)
        or _SAFE_LOGICAL_NAME_RE.fullmatch(logical_name) is None
    ):
        _invalid_field(
            blockers,
            "$.artifact.logical_name",
            "artifact logical name contains unsafe characters",
        )
    _require_text(
        producer.get("policy_id"),
        "$.producer.policy_id",
        blockers,
        maximum=256,
    )
    report_identity = build.get("report_identity")
    if (
        not isinstance(report_identity, str)
        or _SHA256_IDENTITY_RE.fullmatch(report_identity) is None
    ):
        _invalid_field(
            blockers,
            "$.build.report_identity",
            "build report identity must be sha256:<64 lowercase hex>",
        )
    repository_input_identity = build.get("repository_input_identity")
    if (
        not isinstance(repository_input_identity, str)
        or _SHA256_IDENTITY_RE.fullmatch(repository_input_identity) is None
    ):
        _invalid_field(
            blockers,
            "$.build.repository_input_identity",
            "repository input identity must be sha256:<64 lowercase hex>",
        )

    members = manifest.get("members")
    descriptors: dict[str, Mapping[str, object]] = {}
    if not isinstance(members, list):
        _invalid_field(
            blockers,
            "$.members",
            "members must be an ordered JSON array",
        )
    elif len(members) + 1 > limits.max_entries:
        _add_blocker(
            blockers,
            "entry_count_limit_exceeded",
            "$.members",
            "manifest member count exceeds max_entries",
        )
    else:
        previous_name: str | None = None
        for index, item in enumerate(members):
            path = f"$.members[{index}]"
            if not isinstance(item, Mapping):
                _invalid_field(
                    blockers,
                    path,
                    "member descriptor must be an object",
                )
                continue
            descriptor = cast(Mapping[str, object], item)
            _require_exact_keys(descriptor, _MEMBER_KEYS, path, blockers)
            name = descriptor.get("name")
            if _path_error(name) is not None or name == PRE_LIVE_ARTIFACT_MANIFEST_NAME:
                _invalid_field(
                    blockers,
                    f"{path}.name",
                    _path_error(name) or "manifest.json is reserved",
                )
                continue
            name = cast(str, name)
            if previous_name is not None and name <= previous_name:
                _add_blocker(
                    blockers,
                    "noncanonical_member_order",
                    path,
                    "member descriptors must be strictly sorted by name",
                )
            previous_name = name
            if name in descriptors:
                _add_blocker(
                    blockers,
                    "duplicate_manifest_member",
                    path,
                    "manifest declares the same member more than once",
                    name=name,
                )
            descriptors[name] = descriptor
            _require_sha256(
                descriptor.get("sha256"),
                f"{path}.sha256",
                blockers,
            )
            _require_nonnegative_int(
                descriptor.get("size_bytes"),
                f"{path}.size_bytes",
                blockers,
            )
            size = descriptor.get("size_bytes")
            if type(size) is int and size > limits.max_member_uncompressed_bytes:
                _add_blocker(
                    blockers,
                    "uncompressed_size_limit_exceeded",
                    f"{path}.size_bytes",
                    "declared member size exceeds the configured limit",
                    name=name,
                )

    role_specs = (
        (
            "$.artifact",
            artifact,
            "member",
            "sha256",
            "size_bytes",
        ),
        ("$.build", build, "report_member", "report_sha256", None),
        ("$.build", build, "binary_member", "binary_sha256", None),
        (
            "$.build",
            build,
            "repository_input_member",
            "repository_input_sha256",
            None,
        ),
        ("$.build", build, "ctest_member", "ctest_sha256", None),
        (
            "$.producer",
            producer,
            "policy_member",
            "policy_sha256",
            None,
        ),
        (
            "$.producer",
            producer,
            "executable_member",
            "executable_sha256",
            None,
        ),
        (
            "$.producer",
            producer,
            "argv_member",
            "argv_sha256",
            None,
        ),
        (
            "$.producer",
            producer,
            "output_member",
            "output_sha256",
            None,
        ),
        (
            "$.producer",
            producer,
            "provenance_member",
            "provenance_sha256",
            None,
        ),
    )
    role_names: list[str] = []
    for path, section, member_key, digest_key, size_key in role_specs:
        name = section.get(member_key)
        digest = section.get(digest_key)
        if _path_error(name) is not None or name == PRE_LIVE_ARTIFACT_MANIFEST_NAME:
            _invalid_field(
                blockers,
                f"{path}.{member_key}",
                _path_error(name) or "manifest.json is reserved",
            )
            continue
        name = cast(str, name)
        role_names.append(name)
        _require_sha256(digest, f"{path}.{digest_key}", blockers)
        descriptor = descriptors.get(name)
        if descriptor is None:
            _add_blocker(
                blockers,
                "role_member_missing",
                f"{path}.{member_key}",
                "role references a member absent from the manifest",
                name=name,
            )
            continue
        if (
            isinstance(digest, str)
            and isinstance(descriptor.get("sha256"), str)
            and not hmac.compare_digest(
                digest,
                cast(str, descriptor["sha256"]),
            )
        ):
            _add_blocker(
                blockers,
                "role_digest_mismatch",
                f"{path}.{digest_key}",
                "role digest does not match its member descriptor",
                name=name,
            )
        if size_key is not None:
            size_value = section.get(size_key)
            _require_nonnegative_int(
                size_value,
                f"{path}.{size_key}",
                blockers,
            )
            if (
                type(size_value) is int
                and type(descriptor.get("size_bytes")) is int
                and size_value != descriptor.get("size_bytes")
            ):
                _add_blocker(
                    blockers,
                    "role_size_mismatch",
                    f"{path}.{size_key}",
                    "role size does not match its member descriptor",
                    name=name,
                )

    artifact_member = artifact.get("member")
    output_member = producer.get("output_member")
    if (
        isinstance(artifact_member, str)
        and isinstance(output_member, str)
        and artifact_member != output_member
    ):
        _add_blocker(
            blockers,
            "artifact_output_binding_mismatch",
            "$.artifact.member",
            "artifact member must be the local producer output member",
        )
    artifact_digest = artifact.get("sha256")
    output_digest = producer.get("output_sha256")
    if (
        isinstance(artifact_digest, str)
        and isinstance(output_digest, str)
        and not hmac.compare_digest(artifact_digest, output_digest)
    ):
        _add_blocker(
            blockers,
            "artifact_output_digest_mismatch",
            "$.artifact.sha256",
            "artifact digest must equal the local producer output digest",
        )

    distinct_roles = [
        build.get("report_member"),
        build.get("binary_member"),
        build.get("repository_input_member"),
        build.get("ctest_member"),
        producer.get("policy_member"),
        producer.get("executable_member"),
        producer.get("argv_member"),
        producer.get("output_member"),
        producer.get("provenance_member"),
    ]
    if all(isinstance(name, str) for name in distinct_roles) and len(
        set(distinct_roles)
    ) != len(distinct_roles):
        _add_blocker(
            blockers,
            "duplicate_role_member",
            "$",
            "build and producer roles must reference distinct payload members",
        )
    return blockers


def _manifest_member_descriptors(
    manifest: Mapping[str, object],
) -> dict[str, Mapping[str, object]]:
    members = cast(list[Mapping[str, object]], manifest["members"])
    return {cast(str, item["name"]): item for item in members}


def _validate_role_bindings(
    manifest: Mapping[str, object],
    descriptors: Mapping[str, Mapping[str, object]],
    evidence: Mapping[str, Mapping[str, object]],
    blockers: list[dict[str, object]],
) -> None:
    artifact = _mapping(manifest.get("artifact"))
    build = _mapping(manifest.get("build"))
    producer = _mapping(manifest.get("producer"))
    roles = (
        ("$.artifact", artifact, "member", "sha256"),
        ("$.build", build, "report_member", "report_sha256"),
        ("$.build", build, "binary_member", "binary_sha256"),
        (
            "$.build",
            build,
            "repository_input_member",
            "repository_input_sha256",
        ),
        ("$.build", build, "ctest_member", "ctest_sha256"),
        (
            "$.producer",
            producer,
            "policy_member",
            "policy_sha256",
        ),
        (
            "$.producer",
            producer,
            "executable_member",
            "executable_sha256",
        ),
        ("$.producer", producer, "argv_member", "argv_sha256"),
        ("$.producer", producer, "output_member", "output_sha256"),
        (
            "$.producer",
            producer,
            "provenance_member",
            "provenance_sha256",
        ),
    )
    for path, section, member_key, digest_key in roles:
        name = cast(str, section[member_key])
        role_digest = cast(str, section[digest_key])
        descriptor_digest = cast(str, descriptors[name]["sha256"])
        observed_digest = evidence.get(name, {}).get("sha256")
        if (
            not hmac.compare_digest(role_digest, descriptor_digest)
            or not isinstance(observed_digest, str)
            or not hmac.compare_digest(role_digest, observed_digest)
        ):
            _add_blocker(
                blockers,
                "unbound_role_digest",
                f"{path}.{digest_key}",
                "role digest is not bound to verified member bytes",
                name=name,
            )


def _validate_archive_member_modes(
    info_by_name: Mapping[str, zipfile.ZipInfo],
    manifest: Mapping[str, object],
    blockers: list[dict[str, object]],
) -> None:
    build = _mapping(manifest.get("build"))
    binary_name = cast(str, build["binary_member"])
    for name, info in info_by_name.items():
        actual_mode = (info.external_attr >> 16) & 0xFFFF
        expected_mode = (
            _EXECUTABLE_FILE_MODE if name == binary_name else _REGULAR_FILE_MODE
        )
        if actual_mode == expected_mode:
            continue
        if name == binary_name:
            _add_blocker(
                blockers,
                "build_binary_not_executable",
                f"$.archive[{name!r}].mode",
                "MicroMachine binary must have deterministic executable ZIP mode",
                expected=oct(expected_mode),
                actual=oct(actual_mode),
            )
        else:
            _add_blocker(
                blockers,
                "unexpected_executable_entry",
                f"$.archive[{name!r}].mode",
                "non-binary ZIP members must have deterministic regular-file mode",
                expected=oct(expected_mode),
                actual=oct(actual_mode),
            )


def _validate_admission_snapshot_binding(
    manifest: Mapping[str, object],
    evidence: Mapping[str, Mapping[str, object]],
    snapshot: PreLiveBuildAdmissionSnapshot,
    blockers: list[dict[str, object]],
) -> None:
    build = _mapping(manifest.get("build"))
    admitted_roles = (
        (
            "admitted_build_report_mismatch",
            "$.build.report_sha256",
            cast(str, build["report_member"]),
            snapshot.build_report_sha256,
            len(snapshot.build_report_bytes),
            "bundled build report does not match the admitted report bytes",
        ),
        (
            "admitted_build_binary_mismatch",
            "$.build.binary_sha256",
            cast(str, build["binary_member"]),
            snapshot.binary_sha256,
            len(snapshot.binary_bytes),
            "bundled MicroMachine binary does not match the admitted binary bytes",
        ),
    )
    for code, path, name, expected_digest, expected_size, message in admitted_roles:
        observed = evidence.get(name, {})
        actual_digest = observed.get("sha256")
        actual_size = observed.get("size_bytes")
        if (
            not isinstance(actual_digest, str)
            or not hmac.compare_digest(actual_digest, expected_digest)
            or actual_size != expected_size
        ):
            _add_blocker(
                blockers,
                code,
                path,
                message,
                name=name,
                expected_sha256=expected_digest,
                actual_sha256=actual_digest,
                expected_size_bytes=expected_size,
                actual_size_bytes=actual_size,
            )


def _validate_build_report_binding(
    report_bytes: bytes,
    manifest: Mapping[str, object],
    blockers: list[dict[str, object]],
) -> None:
    local_blockers: list[dict[str, object]] = []
    report = _parse_json(
        report_bytes,
        local_blockers,
        "$.build_report",
    )
    blockers.extend(local_blockers)
    if report is None:
        return
    if not isinstance(report, Mapping):
        _invalid_field(
            blockers,
            "$.build_report",
            "build report must be a JSON object",
        )
        return
    build = _mapping(manifest.get("build"))
    schema_version = report.get("schema_version")
    if (
        type(schema_version) is not int
        or schema_version != MICROMACHINE_BUILD_IDENTITY_SCHEMA_VERSION
    ):
        _add_blocker(
            blockers,
            "build_report_schema_mismatch",
            "$.build_report.schema_version",
            "build report schema is not the required MicroMachine schema",
            expected=MICROMACHINE_BUILD_IDENTITY_SCHEMA_VERSION,
            actual=schema_version,
        )
    if report.get("ok") is not True:
        _add_blocker(
            blockers,
            "build_report_failed",
            "$.build_report.ok",
            "build report must record an accepted supported build identity",
        )
    _require_equal(
        report.get("failures"),
        [],
        "$.build_report.failures",
        blockers,
    )
    identity = report.get("identity")
    if not isinstance(identity, str) or not hmac.compare_digest(
        identity,
        cast(str, build["report_identity"]),
    ):
        _add_blocker(
            blockers,
            "build_report_identity_mismatch",
            "$.build_report.identity",
            "build report identity does not match the manifest",
        )
    observed = report.get("observed")
    if not isinstance(observed, Mapping):
        _invalid_field(
            blockers,
            "$.build_report.observed",
            "build report observed evidence must be an object",
        )
        return
    binary_digest = observed.get("binary_sha256")
    if not isinstance(binary_digest, str) or not hmac.compare_digest(
        binary_digest,
        cast(str, build["binary_sha256"]),
    ):
        _add_blocker(
            blockers,
            "build_report_binary_digest_mismatch",
            "$.build_report.observed.binary_sha256",
            "build report binary digest does not match bundled binary bytes",
        )
    repository_input_identity = observed.get("embedded_build_input_identity")
    expected_input_identity = cast(str, build["repository_input_identity"])
    if not isinstance(repository_input_identity, str) or not hmac.compare_digest(
        repository_input_identity,
        expected_input_identity,
    ):
        _add_blocker(
            blockers,
            "build_report_repository_input_digest_mismatch",
            "$.build_report.observed.embedded_build_input_identity",
            "build report input identity does not match repository-input bytes",
        )


def _validate_producer_provenance_binding(
    provenance_bytes: bytes,
    manifest: Mapping[str, object],
    blockers: list[dict[str, object]],
) -> dict[str, object] | None:
    local_blockers: list[dict[str, object]] = []
    provenance = _parse_json(
        provenance_bytes,
        local_blockers,
        "$.producer_provenance",
    )
    blockers.extend(local_blockers)
    if not isinstance(provenance, Mapping):
        _invalid_field(
            blockers,
            "$.producer_provenance",
            "producer provenance must be a JSON object",
        )
        return None
    if canonical_json_bytes(provenance) != provenance_bytes:
        _add_blocker(
            blockers,
            "noncanonical_producer_provenance",
            "$.producer_provenance",
            "producer provenance must use canonical JSON",
        )
    expected_keys = frozenset(
        {
            "schema_version",
            "authority",
            "producer_id",
            "policy_sha256",
            "repository_commit",
            "argv_sha256",
            "executable_sha256",
            "output_sha256",
            "exit_code",
            "started_at",
            "ended_at",
            "stdout_sha256",
            "stderr_sha256",
        }
    )
    _require_exact_keys(
        provenance,
        expected_keys,
        "$.producer_provenance",
        blockers,
    )
    repository = _mapping(manifest.get("repository"))
    authority = _mapping(manifest.get("authority"))
    producer = _mapping(manifest.get("producer"))
    expected_values = {
        "authority": dict(authority),
        "producer_id": producer.get("policy_id"),
        "policy_sha256": producer.get("policy_sha256"),
        "repository_commit": repository.get("commit_sha"),
        "argv_sha256": producer.get("argv_sha256"),
        "executable_sha256": producer.get("executable_sha256"),
        "output_sha256": producer.get("output_sha256"),
    }
    for key, expected in expected_values.items():
        _require_equal(
            provenance.get(key),
            expected,
            f"$.producer_provenance.{key}",
            blockers,
        )
    _require_equal(
        provenance.get("schema_version"),
        1,
        "$.producer_provenance.schema_version",
        blockers,
        exact_int=True,
    )
    _require_equal(
        provenance.get("exit_code"),
        0,
        "$.producer_provenance.exit_code",
        blockers,
        exact_int=True,
    )
    for key in ("stdout_sha256", "stderr_sha256"):
        _require_sha256(
            provenance.get(key),
            f"$.producer_provenance.{key}",
            blockers,
        )
    started_at = _parse_exact_utc(provenance.get("started_at"))
    ended_at = _parse_exact_utc(provenance.get("ended_at"))
    if started_at is None:
        _invalid_field(
            blockers,
            "$.producer_provenance.started_at",
            "started_at must be an exact UTC timestamp",
        )
    if ended_at is None:
        _invalid_field(
            blockers,
            "$.producer_provenance.ended_at",
            "ended_at must be an exact UTC timestamp",
        )
    if started_at is not None and ended_at is not None and started_at > ended_at:
        _add_blocker(
            blockers,
            "producer_timestamp_inversion",
            "$.producer_provenance.ended_at",
            "producer ended_at predates started_at",
        )
    return dict(provenance)


def _validate_repository_input_binding(
    repository_input_bytes: bytes,
    manifest: Mapping[str, object],
    blockers: list[dict[str, object]],
) -> None:
    local_blockers: list[dict[str, object]] = []
    payload = _parse_json(
        repository_input_bytes,
        local_blockers,
        "$.repository_input",
    )
    blockers.extend(local_blockers)
    if not isinstance(payload, Mapping):
        _invalid_field(
            blockers,
            "$.repository_input",
            "repository input evidence must be a JSON object",
        )
        return
    if canonical_json_bytes(payload) != repository_input_bytes:
        _add_blocker(
            blockers,
            "noncanonical_repository_input",
            "$.repository_input",
            "repository input evidence must use canonical JSON",
        )
    _require_exact_keys(
        payload,
        _REPOSITORY_INPUT_KEYS,
        "$.repository_input",
        blockers,
    )
    repository = _mapping(manifest.get("repository"))
    build = _mapping(manifest.get("build"))
    _require_equal(
        payload.get("schema_version"),
        1,
        "$.repository_input.schema_version",
        blockers,
        exact_int=True,
    )
    _require_equal(
        payload.get("repository_commit"),
        repository.get("commit_sha"),
        "$.repository_input.repository_commit",
        blockers,
    )
    _require_equal(
        payload.get("build_input_identity"),
        build.get("repository_input_identity"),
        "$.repository_input.build_input_identity",
        blockers,
    )
    paths = payload.get("paths")
    if not isinstance(paths, Mapping) or not paths:
        _invalid_field(
            blockers,
            "$.repository_input.paths",
            "repository input paths must be a non-empty object",
        )
        return
    upstream_commit_policy = _schema_mapping(
        payload.get("upstream_commit_policy"),
        "$.repository_input.upstream_commit_policy",
        _UPSTREAM_COMMIT_POLICY_KEYS,
        blockers,
    )
    _require_equal(
        upstream_commit_policy.get("path"),
        "integrations/micromachine/scripts/build_macos_local.sh",
        "$.repository_input.upstream_commit_policy.path",
        blockers,
    )
    _require_sha256(
        upstream_commit_policy.get("sha256"),
        "$.repository_input.upstream_commit_policy.sha256",
        blockers,
    )
    for key in ("micromachine_commit", "s2client_commit"):
        _require_sha40(
            upstream_commit_policy.get(key),
            f"$.repository_input.upstream_commit_policy.{key}",
            blockers,
        )
    digest_material = {
        "paths": dict(paths),
        "upstream_commit_policy": dict(upstream_commit_policy),
    }
    expected_digest = (
        "sha256:"
        + hashlib.sha256(canonical_json_bytes(digest_material)).hexdigest()
    )
    _require_equal(
        payload.get("repository_inputs_digest"),
        expected_digest,
        "$.repository_input.repository_inputs_digest",
        blockers,
    )


def _validate_ctest_evidence_binding(
    ctest_bytes: bytes,
    blockers: list[dict[str, object]],
) -> None:
    local_blockers: list[dict[str, object]] = []
    payload = _parse_json(
        ctest_bytes,
        local_blockers,
        "$.ctest_evidence",
    )
    blockers.extend(local_blockers)
    if not isinstance(payload, Mapping):
        _invalid_field(
            blockers,
            "$.ctest_evidence",
            "CTest evidence must be a JSON object",
        )
        return
    if canonical_json_bytes(payload) != ctest_bytes:
        _add_blocker(
            blockers,
            "noncanonical_ctest_evidence",
            "$.ctest_evidence",
            "CTest evidence must use canonical JSON",
        )
    _validate_ctest_evidence_payload(payload, blockers)


def _validate_build_report_ctest_registry_binding(
    report_bytes: bytes,
    ctest_bytes: bytes,
    blockers: list[dict[str, object]],
) -> None:
    local_blockers: list[dict[str, object]] = []
    report = _parse_json(report_bytes, local_blockers, "$.build_report")
    ctest = _parse_json(ctest_bytes, local_blockers, "$.ctest_evidence")
    blockers.extend(local_blockers)
    if not isinstance(report, Mapping) or not isinstance(ctest, Mapping):
        return
    observed = report.get("observed")
    native_tests = (
        observed.get("native_tests") if isinstance(observed, Mapping) else None
    )
    report_tests = (
        native_tests.get("tests") if isinstance(native_tests, Mapping) else None
    )
    evidence_tests = ctest.get("test_executables")
    if not isinstance(report_tests, Mapping) or not isinstance(
        evidence_tests, Mapping
    ):
        _add_blocker(
            blockers,
            "ctest_build_report_binding_missing",
            "$.build_report.observed.native_tests.tests",
            "Build report and CTest evidence must expose native-test artifacts",
        )
    else:
        for name in sorted(_REQUIRED_CTEST_EXECUTABLES):
            report_descriptor = report_tests.get(name)
            evidence_descriptor = evidence_tests.get(name)
            if not isinstance(report_descriptor, Mapping) or not isinstance(
                evidence_descriptor, Mapping
            ):
                _add_blocker(
                    blockers,
                    "ctest_build_report_binding_missing",
                    f"$.build_report.observed.native_tests.tests.{name}",
                    "Each required native test must be present in both artifacts",
                )
                continue
            _require_equal(
                evidence_descriptor.get("path"),
                report_descriptor.get("path"),
                f"$.ctest_evidence.test_executables.{name}.path",
                blockers,
            )
            _require_equal(
                evidence_descriptor.get("sha256"),
                report_descriptor.get("sha256"),
                f"$.ctest_evidence.test_executables.{name}.sha256",
                blockers,
            )

    report_ctest = (
        native_tests.get("ctest") if isinstance(native_tests, Mapping) else None
    )
    if not isinstance(report_ctest, Mapping):
        _add_blocker(
            blockers,
            "ctest_build_report_binding_missing",
            "$.build_report.observed.native_tests.ctest",
            "Build report must bind the CTest executable used by the evidence",
        )
    else:
        _require_equal(
            ctest.get("ctest_executable"),
            report_ctest.get("path"),
            "$.ctest_evidence.ctest_executable",
            blockers,
        )
        _require_equal(
            ctest.get("ctest_executable_sha256"),
            report_ctest.get("sha256"),
            "$.ctest_evidence.ctest_executable_sha256",
            blockers,
        )

    registry = (
        native_tests.get("registry") if isinstance(native_tests, Mapping) else None
    )
    report_registry_sha256 = (
        registry.get("sha256") if isinstance(registry, Mapping) else None
    )
    evidence_registry_sha256 = ctest.get("registry_sha256")
    _require_equal(
        evidence_registry_sha256,
        report_registry_sha256,
        "$.ctest_evidence.registry_sha256",
        blockers,
    )
    checksums = report.get("checksums")
    checksum_registry_sha256 = (
        checksums.get("native_test_registry_sha256")
        if isinstance(checksums, Mapping)
        else None
    )
    _require_equal(
        evidence_registry_sha256,
        checksum_registry_sha256,
        "$.build_report.checksums.native_test_registry_sha256",
        blockers,
    )
    report_manifest_sha256 = (
        native_tests.get("manifest_sha256")
        if isinstance(native_tests, Mapping)
        else None
    )
    checksum_manifest_sha256 = (
        checksums.get("native_test_manifest_sha256")
        if isinstance(checksums, Mapping)
        else None
    )
    _require_equal(
        report_manifest_sha256,
        checksum_manifest_sha256,
        "$.build_report.checksums.native_test_manifest_sha256",
        blockers,
    )


def _validate_ctest_evidence_payload(
    payload: Mapping[str, object],
    blockers: list[dict[str, object]],
) -> None:
    _require_exact_keys(
        payload,
        _CTEST_EVIDENCE_KEYS,
        "$.ctest_evidence",
        blockers,
    )
    _require_equal(
        payload.get("schema_version"),
        PRE_LIVE_CTEST_EVIDENCE_SCHEMA_VERSION,
        "$.ctest_evidence.schema_version",
        blockers,
        exact_int=True,
    )
    _require_equal(
        payload.get("returncode"),
        0,
        "$.ctest_evidence.returncode",
        blockers,
        exact_int=True,
    )
    required_count = len(_REQUIRED_CTEST_EXECUTABLES)
    for key, expected in (
        ("passed", required_count),
        ("total", required_count),
        ("failures", 0),
    ):
        _require_equal(
            payload.get(key),
            expected,
            f"$.ctest_evidence.{key}",
            blockers,
            exact_int=True,
        )
    for key in (
        "ctest_executable_sha256",
        "stdout_sha256",
        "stderr_sha256",
    ):
        _require_sha256(
            payload.get(key),
            f"$.ctest_evidence.{key}",
            blockers,
        )

    expected_names = sorted(_REQUIRED_CTEST_EXECUTABLES)
    names = payload.get("test_names")
    if names != expected_names:
        _add_blocker(
            blockers,
            "ctest_identity_mismatch",
            "$.ctest_evidence.test_names",
            "CTest evidence must contain the exact required tests",
            expected=expected_names,
            actual=names,
        )

    ctest_executable = payload.get("ctest_executable")
    if (
        not isinstance(ctest_executable, str)
        or not ctest_executable.startswith("/")
        or PurePosixPath(ctest_executable).name != "ctest"
    ):
        _invalid_field(
            blockers,
            "$.ctest_evidence.ctest_executable",
            "CTest executable must be an absolute path ending in ctest",
        )

    argv = _validate_ctest_argv(
        payload.get("argv"),
        "$.ctest_evidence.argv",
        "--output-on-failure",
        blockers,
    )
    discovery_argv = _validate_ctest_argv(
        payload.get("discovery_argv"),
        "$.ctest_evidence.discovery_argv",
        "--show-only=json-v1",
        blockers,
    )
    if argv is not None and discovery_argv is not None:
        if argv[:3] != discovery_argv[:3]:
            _add_blocker(
                blockers,
                "ctest_argv_mismatch",
                "$.ctest_evidence.discovery_argv",
                "CTest execution and discovery must use the same executable/build dir",
            )
        if isinstance(ctest_executable, str) and argv[0] != ctest_executable:
            _add_blocker(
                blockers,
                "ctest_executable_binding_mismatch",
                "$.ctest_evidence.ctest_executable",
                "CTest executable must match the recorded argv",
            )

    executables = payload.get("test_executables")
    if not isinstance(executables, Mapping):
        _invalid_field(
            blockers,
            "$.ctest_evidence.test_executables",
            "CTest executable evidence must be an object",
        )
    else:
        executable_names = set(executables)
        if executable_names != set(_REQUIRED_CTEST_EXECUTABLES):
            _add_blocker(
                blockers,
                "ctest_executable_set_mismatch",
                "$.ctest_evidence.test_executables",
                "CTest executable evidence must bind the exact required tests",
                expected=expected_names,
                actual=sorted(str(name) for name in executable_names),
            )
        build_dir = argv[2] if argv is not None else None
        for name in expected_names:
            descriptor = executables.get(name)
            path = f"$.ctest_evidence.test_executables.{name}"
            if not isinstance(descriptor, Mapping):
                _invalid_field(
                    blockers,
                    path,
                    "CTest executable descriptor must be an object",
                )
                continue
            descriptor = cast(Mapping[str, object], descriptor)
            _require_exact_keys(
                descriptor,
                _CTEST_EXECUTABLE_KEYS,
                path,
                blockers,
            )
            executable_path = descriptor.get("path")
            if not isinstance(executable_path, str) or not executable_path.startswith(
                "/"
            ):
                _invalid_field(
                    blockers,
                    f"{path}.path",
                    "CTest command path must be absolute",
                )
            elif build_dir is not None:
                expected_path = str(
                    PurePosixPath(build_dir) / "bin" / _REQUIRED_CTEST_EXECUTABLES[name]
                )
                if executable_path != expected_path:
                    _add_blocker(
                        blockers,
                        "ctest_command_path_mismatch",
                        f"{path}.path",
                        "CTest command path does not match the attested build dir",
                        expected=expected_path,
                        actual=executable_path,
                    )
            _require_sha256(
                descriptor.get("sha256"),
                f"{path}.sha256",
                blockers,
            )
            _require_equal(
                descriptor.get("sha256_after"),
                descriptor.get("sha256"),
                f"{path}.sha256_after",
                blockers,
            )
            _require_equal(
                descriptor.get("argv"),
                [executable_path] if isinstance(executable_path, str) else None,
                f"{path}.argv",
                blockers,
            )
            _require_equal(
                descriptor.get("returncode"),
                0,
                f"{path}.returncode",
                blockers,
                exact_int=True,
            )
            for digest_key in ("stdout_sha256", "stderr_sha256"):
                _require_sha256(
                    descriptor.get(digest_key),
                    f"{path}.{digest_key}",
                    blockers,
                )

        expected_manifest_identity = (
            "sha256:" + hashlib.sha256(canonical_json_bytes(executables)).hexdigest()
        )
        _require_equal(
            payload.get("test_manifest_sha256"),
            expected_manifest_identity,
            "$.ctest_evidence.test_manifest_sha256",
            blockers,
        )
        registry_paths = {
            name: str(cast(Mapping[str, object], executables[name]).get("path"))
            for name in expected_names
            if isinstance(executables.get(name), Mapping)
            and isinstance(
                cast(Mapping[str, object], executables[name]).get("path"),
                str,
            )
        }
        expected_registry_identity = canonical_micromachine_ctest_registry(
            registry_paths
        ).get("sha256")
        _require_equal(
            payload.get("registry_sha256"),
            expected_registry_identity,
            "$.ctest_evidence.registry_sha256",
            blockers,
        )


def _validate_ctest_argv(
    value: object,
    path: str,
    terminal_argument: str,
    blockers: list[dict[str, object]],
) -> list[str] | None:
    if (
        not isinstance(value, list)
        or len(value) != 4
        or not all(isinstance(argument, str) for argument in value)
        or value[1] != "--test-dir"
        or value[3] != terminal_argument
        or not cast(str, value[0]).startswith("/")
        or not cast(str, value[2]).startswith("/")
    ):
        _invalid_field(
            blockers,
            path,
            "CTest argv must bind an absolute executable/build dir and exact mode",
        )
        return None
    return cast(list[str], value)


def _parse_exact_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        return None
    return parsed


def _schema_mapping(
    value: object,
    path: str,
    expected_keys: frozenset[str],
    blockers: list[dict[str, object]],
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _invalid_field(blockers, path, "value must be a JSON object")
        return {}
    result = cast(Mapping[str, object], value)
    _require_exact_keys(result, expected_keys, path, blockers)
    return result


def _require_exact_keys(
    value: Mapping[str, object],
    expected: frozenset[str],
    path: str,
    blockers: list[dict[str, object]],
) -> None:
    actual = set(value)
    if actual != expected:
        _add_blocker(
            blockers,
            "schema_fields_mismatch",
            path,
            "object fields do not match the canonical schema",
            missing=sorted(expected - actual),
            unexpected=sorted(actual - expected),
        )


def _require_equal(
    actual: object,
    expected: object,
    path: str,
    blockers: list[dict[str, object]],
    *,
    exact_int: bool = False,
) -> None:
    matches = actual == expected
    if exact_int:
        matches = type(actual) is int and actual == expected
    if not matches:
        _add_blocker(
            blockers,
            "schema_value_mismatch",
            path,
            "value does not match the canonical schema",
            expected=expected,
            actual=actual,
        )


def _require_positive_int(
    value: object,
    path: str,
    blockers: list[dict[str, object]],
) -> None:
    if type(value) is not int or value <= 0:
        _invalid_field(
            blockers,
            path,
            "value must be a positive integer and not a boolean",
        )


def _require_nonnegative_int(
    value: object,
    path: str,
    blockers: list[dict[str, object]],
) -> None:
    if type(value) is not int or value < 0:
        _invalid_field(
            blockers,
            path,
            "value must be a non-negative integer and not a boolean",
        )


def _require_sha40(
    value: object,
    path: str,
    blockers: list[dict[str, object]],
) -> None:
    if not isinstance(value, str) or _SHA40_RE.fullmatch(value) is None:
        _invalid_field(
            blockers,
            path,
            "value must be an exact lowercase 40-character commit SHA",
        )


def _require_sha256(
    value: object,
    path: str,
    blockers: list[dict[str, object]],
) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        _invalid_field(
            blockers,
            path,
            "value must be an exact lowercase SHA-256 digest",
        )


def _require_text(
    value: object,
    path: str,
    blockers: list[dict[str, object]],
    *,
    maximum: int,
    prefix: str | None = None,
) -> None:
    valid = (
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and value == unicodedata.normalize("NFC", value)
        and all(
            not unicodedata.category(character).startswith("C") for character in value
        )
    )
    if prefix is not None:
        valid = valid and cast(str, value).startswith(prefix)
    if not valid:
        _invalid_field(
            blockers,
            path,
            "value must be bounded NFC text without control characters",
        )


def _path_error(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return "entry path must be a non-empty string"
    if len(value.encode("utf-8")) > 512:
        return "entry path exceeds 512 UTF-8 bytes"
    if value != unicodedata.normalize("NFC", value):
        return "entry path must use NFC Unicode normalization"
    if "\\" in value or "\x00" in value:
        return "entry path contains a backslash or NUL"
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        return "absolute and drive-qualified entry paths are forbidden"
    path = PurePosixPath(value)
    if path.is_absolute():
        return "absolute entry paths are forbidden"
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return "entry path contains an empty, dot, or traversal component"
    if any(_SAFE_PATH_PART_RE.fullmatch(part) is None for part in parts):
        return "entry path contains unsupported characters"
    return None


def _zip_file_type(info: zipfile.ZipInfo) -> int:
    mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(mode)
    if file_type:
        return file_type
    if info.external_attr & 0x10:
        return stat.S_IFDIR
    return 0


def _compression_ratio(info: zipfile.ZipInfo) -> float:
    if info.file_size == 0:
        return 1.0
    if info.compress_size == 0:
        return float("inf")
    return info.file_size / info.compress_size


def _mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return {}


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _invalid_field(
    blockers: list[dict[str, object]],
    path: str,
    message: str,
) -> None:
    _add_blocker(blockers, "invalid_manifest_field", path, message)


def _add_blocker(
    blockers: list[dict[str, object]],
    code: str,
    path: str,
    message: str,
    **evidence: object,
) -> None:
    blocker: dict[str, object] = {
        "code": code,
        "path": path,
        "message": message,
    }
    blocker.update(evidence)
    blockers.append(blocker)


def _verification_result(
    blockers: list[dict[str, object]],
    manifest: dict[str, object] | None,
    manifest_evidence: dict[str, object],
    member_evidence: list[dict[str, object]],
    caller_claims: Mapping[str, object] | None,
    *,
    role_evidence: Mapping[str, object] | None = None,
) -> dict[str, object]:
    return {
        "ok": not blockers,
        "blockers": blockers,
        "manifest": manifest,
        "manifest_evidence": manifest_evidence,
        "member_evidence": member_evidence,
        "role_evidence": dict(role_evidence or {}),
        "caller_claims_ignored": caller_claims is not None,
    }


def _format_builder_blockers(
    blockers: list[Mapping[str, object]],
) -> str:
    return "; ".join(
        f"{blocker.get('code')} at {blocker.get('path')}: {blocker.get('message')}"
        for blocker in blockers
    )
