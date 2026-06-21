"""Runtime backend bridge for MicroMachine policy modulation.

This module turns the issue #10 contracts into practical sidecar transports.
The filesystem backend writes canonical JSON plus a flat ``key=value`` overlay
for the C++ MicroMachine hook, while the backend protocol and in-memory backend
let LLM, replay, UI, or future neural representation providers publish the same
bounded modulation vectors without coupling callers to files.
"""

from __future__ import annotations

import json
import os
import uuid
from copy import deepcopy
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, runtime_checkable

from starcraft_commander.micromachine_bridge import (
    MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
    MicroMachineBlackboardUpdate,
    MicroMachineBridgeFailureMode,
    MicroMachineTelemetry,
    validate_micromachine_blackboard_update,
)
from starcraft_commander.policy_modulation import (
    PolicyModulationSource,
    PolicyModulationVector,
    reject_raw_policy_control_keys,
)
from starcraft_commander.policy_modulation_provider import (
    PolicyModulationCompileResult,
    compile_policy_modulation_provider_output,
)
from starcraft_commander.policy_observability import (
    PolicyModulationBridgeStatus,
    PolicyModulationDashboardSnapshot,
    build_policy_modulation_dashboard_snapshot,
)


LATEST_UPDATE_JSON_NAME: Final[str] = "latest_modulation.json"
LATEST_UPDATE_KV_NAME: Final[str] = "latest_modulation.kv"
UPDATE_ARCHIVE_JSONL_NAME: Final[str] = "modulation_updates.jsonl"
LATEST_TELEMETRY_JSON_NAME: Final[str] = "latest_telemetry.json"
TELEMETRY_ARCHIVE_JSONL_NAME: Final[str] = "telemetry.jsonl"


@runtime_checkable
class MicroMachineModulationBackend(Protocol):
    """Transport-independent backend for MicroMachine policy modulation."""

    def publish_vector(
        self,
        vector: PolicyModulationVector,
        *,
        current_frame: int,
        update_id: str | None = None,
        rollback_update_id: str | None = None,
    ) -> MicroMachineBlackboardUpdate:
        """Validate and publish one modulation vector."""

    def publish_update(
        self,
        update: MicroMachineBlackboardUpdate,
        *,
        current_frame: int,
    ) -> MicroMachineBlackboardUpdate:
        """Validate and publish one already-built update."""

    def read_latest_update(
        self,
        *,
        current_frame: int,
    ) -> MicroMachineBlackboardUpdate | None:
        """Return the latest non-stale update, if any."""

    def ingest_telemetry(
        self,
        telemetry: MicroMachineTelemetry | Mapping[str, object],
    ) -> MicroMachineTelemetry:
        """Validate and ingest one telemetry snapshot."""

    def read_latest_telemetry(self) -> MicroMachineTelemetry | None:
        """Return the latest telemetry snapshot, if any."""

    def dashboard_snapshot(
        self,
        *,
        current_frame: int,
        bridge_status: PolicyModulationBridgeStatus | str = (
            PolicyModulationBridgeStatus.SIMULATED
        ),
    ) -> PolicyModulationDashboardSnapshot:
        """Build a transport-independent dashboard snapshot."""

    def write_provider_unavailable(
        self,
        *,
        current_frame: int,
        reason: str,
    ) -> MicroMachineTelemetry:
        """Record a provider-unavailable failure state."""


@dataclass(frozen=True)
class MicroMachineBackendPublishResult:
    """Result of compiling provider output and publishing through a backend."""

    compile_result: PolicyModulationCompileResult
    update: MicroMachineBlackboardUpdate | None = None

    @property
    def ok(self) -> bool:
        return self.compile_result.ok and self.update is not None

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "compile_result": self.compile_result.to_dict(),
            "update": self.update.to_dict() if self.update else None,
        }


@dataclass(frozen=True)
class MicroMachineRuntimePaths:
    """Filesystem locations shared by the Python sidecar and C++ bot."""

    root: Path | str

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root))

    @property
    def latest_update_json(self) -> Path:
        return self.root / LATEST_UPDATE_JSON_NAME

    @property
    def latest_update_kv(self) -> Path:
        return self.root / LATEST_UPDATE_KV_NAME

    @property
    def update_archive_jsonl(self) -> Path:
        return self.root / UPDATE_ARCHIVE_JSONL_NAME

    @property
    def latest_telemetry_json(self) -> Path:
        return self.root / LATEST_TELEMETRY_JSON_NAME

    @property
    def telemetry_archive_jsonl(self) -> Path:
        return self.root / TELEMETRY_ARCHIVE_JSONL_NAME

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def to_dict(self) -> dict[str, str]:
        return {
            "root": str(self.root),
            "latest_update_json": str(self.latest_update_json),
            "latest_update_kv": str(self.latest_update_kv),
            "update_archive_jsonl": str(self.update_archive_jsonl),
            "latest_telemetry_json": str(self.latest_telemetry_json),
            "telemetry_archive_jsonl": str(self.telemetry_archive_jsonl),
        }


class MicroMachineFilesystemBlackboard:
    """Atomic filesystem blackboard for a local MicroMachine sidecar."""

    def __init__(self, root: Path | str) -> None:
        self.paths = MicroMachineRuntimePaths(root)
        self.paths.ensure()

    def publish_vector(
        self,
        vector: PolicyModulationVector,
        *,
        current_frame: int,
        update_id: str | None = None,
        rollback_update_id: str | None = None,
    ) -> MicroMachineBlackboardUpdate:
        """Create and persist one validated modulation update."""

        update = MicroMachineBlackboardUpdate(
            update_id=update_id or _new_update_id(),
            vector=vector,
            issued_at_frame=_non_negative_int("current_frame", current_frame),
            rollback_update_id=rollback_update_id,
        )
        return self.publish_update(update, current_frame=current_frame)

    def publish_update(
        self,
        update: MicroMachineBlackboardUpdate,
        *,
        current_frame: int,
    ) -> MicroMachineBlackboardUpdate:
        """Persist an update after stale/invalid validation."""

        result = validate_micromachine_blackboard_update(
            update.to_dict(),
            current_frame=_non_negative_int("current_frame", current_frame),
        )
        if not result.accepted:
            reason = result.reason or "blackboard update was rejected."
            raise ValueError(reason)
        accepted = result.update
        if accepted is None:
            raise ValueError("blackboard update validation did not return an update.")
        document = accepted.to_dict()
        _atomic_write_text(
            self.paths.latest_update_json,
            json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n",
        )
        _atomic_write_text(self.paths.latest_update_kv, flatten_blackboard_update(accepted))
        _append_jsonl(self.paths.update_archive_jsonl, document)
        return accepted

    def read_latest_update(
        self,
        *,
        current_frame: int,
    ) -> MicroMachineBlackboardUpdate | None:
        """Read and validate the latest update, returning ``None`` if absent."""

        if not self.paths.latest_update_json.exists():
            return None
        payload = _read_json_mapping(self.paths.latest_update_json)
        result = validate_micromachine_blackboard_update(
            payload,
            current_frame=_non_negative_int("current_frame", current_frame),
        )
        if not result.accepted:
            raise ValueError(result.reason)
        return result.update

    def ingest_telemetry(
        self,
        telemetry: MicroMachineTelemetry | Mapping[str, object],
    ) -> MicroMachineTelemetry:
        """Validate and persist telemetry emitted by MicroMachine."""

        parsed = _coerce_telemetry(telemetry)
        document = parsed.to_dict()
        _atomic_write_text(
            self.paths.latest_telemetry_json,
            json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n",
        )
        _append_jsonl(self.paths.telemetry_archive_jsonl, document)
        return parsed

    def read_latest_telemetry(self) -> MicroMachineTelemetry | None:
        if not self.paths.latest_telemetry_json.exists():
            return None
        return MicroMachineTelemetry.from_mapping(
            _read_json_mapping(self.paths.latest_telemetry_json)
        )

    def dashboard_snapshot(
        self,
        *,
        current_frame: int,
        bridge_status: PolicyModulationBridgeStatus | str = PolicyModulationBridgeStatus.SIMULATED,
    ) -> PolicyModulationDashboardSnapshot:
        """Build a dashboard snapshot from latest files only."""

        updates: tuple[MicroMachineBlackboardUpdate, ...] = ()
        failure: MicroMachineBridgeFailureMode | None = None
        try:
            update = self.read_latest_update(current_frame=current_frame)
            if update is not None:
                updates = (update,)
        except ValueError:
            failure = MicroMachineBridgeFailureMode.INVALID_PAYLOAD
        telemetry = self.read_latest_telemetry()
        if telemetry is not None and telemetry.last_failure is not None:
            failure = telemetry.last_failure
        return build_policy_modulation_dashboard_snapshot(
            updates,
            current_frame=current_frame,
            bridge_status=bridge_status,
            telemetry=telemetry,
            last_failure=failure,
        )

    def write_provider_unavailable(
        self,
        *,
        current_frame: int,
        reason: str,
    ) -> MicroMachineTelemetry:
        """Persist a telemetry failure state when the provider cannot produce intent."""

        return self.ingest_telemetry(
            MicroMachineTelemetry(
                frame=_non_negative_int("current_frame", current_frame),
                managers={
                    "Provider": {
                        "status": "unavailable",
                        "unavailable_reason": _require_text("reason", reason),
                    }
                },
                active_modulation_ids=(),
                last_failure=MicroMachineBridgeFailureMode.PROVIDER_UNAVAILABLE,
            )
        )


class MicroMachineInMemoryBlackboard:
    """In-memory backend for tests and future live model-loop orchestration."""

    def __init__(self) -> None:
        self.latest_update: MicroMachineBlackboardUpdate | None = None
        self.update_archive: list[MicroMachineBlackboardUpdate] = []
        self.latest_telemetry: MicroMachineTelemetry | None = None
        self.telemetry_archive: list[MicroMachineTelemetry] = []

    def publish_vector(
        self,
        vector: PolicyModulationVector,
        *,
        current_frame: int,
        update_id: str | None = None,
        rollback_update_id: str | None = None,
    ) -> MicroMachineBlackboardUpdate:
        update = MicroMachineBlackboardUpdate(
            update_id=update_id or _new_update_id(),
            vector=vector,
            issued_at_frame=_non_negative_int("current_frame", current_frame),
            rollback_update_id=rollback_update_id,
        )
        return self.publish_update(update, current_frame=current_frame)

    def publish_update(
        self,
        update: MicroMachineBlackboardUpdate,
        *,
        current_frame: int,
    ) -> MicroMachineBlackboardUpdate:
        result = validate_micromachine_blackboard_update(
            update.to_dict(),
            current_frame=_non_negative_int("current_frame", current_frame),
        )
        if not result.accepted:
            raise ValueError(result.reason or "blackboard update was rejected.")
        accepted = result.update
        if accepted is None:
            raise ValueError("blackboard update validation did not return an update.")
        self.latest_update = accepted
        self.update_archive.append(accepted)
        return accepted

    def read_latest_update(
        self,
        *,
        current_frame: int,
    ) -> MicroMachineBlackboardUpdate | None:
        if self.latest_update is None:
            return None
        result = validate_micromachine_blackboard_update(
            self.latest_update.to_dict(),
            current_frame=_non_negative_int("current_frame", current_frame),
        )
        if not result.accepted:
            raise ValueError(result.reason)
        return result.update

    def ingest_telemetry(
        self,
        telemetry: MicroMachineTelemetry | Mapping[str, object],
    ) -> MicroMachineTelemetry:
        parsed = _coerce_telemetry(telemetry)
        stored = _clone_telemetry(parsed)
        self.latest_telemetry = stored
        self.telemetry_archive.append(stored)
        return _clone_telemetry(stored)

    def read_latest_telemetry(self) -> MicroMachineTelemetry | None:
        if self.latest_telemetry is None:
            return None
        return _clone_telemetry(self.latest_telemetry)

    def dashboard_snapshot(
        self,
        *,
        current_frame: int,
        bridge_status: PolicyModulationBridgeStatus | str = (
            PolicyModulationBridgeStatus.SIMULATED
        ),
    ) -> PolicyModulationDashboardSnapshot:
        updates: tuple[MicroMachineBlackboardUpdate, ...] = ()
        failure: MicroMachineBridgeFailureMode | None = None
        try:
            update = self.read_latest_update(current_frame=current_frame)
            if update is not None:
                updates = (update,)
        except ValueError:
            failure = MicroMachineBridgeFailureMode.INVALID_PAYLOAD
        telemetry = self.read_latest_telemetry()
        if telemetry is not None and telemetry.last_failure is not None:
            failure = telemetry.last_failure
        return build_policy_modulation_dashboard_snapshot(
            updates,
            current_frame=current_frame,
            bridge_status=bridge_status,
            telemetry=telemetry,
            last_failure=failure,
        )

    def write_provider_unavailable(
        self,
        *,
        current_frame: int,
        reason: str,
    ) -> MicroMachineTelemetry:
        return self.ingest_telemetry(
            MicroMachineTelemetry(
                frame=_non_negative_int("current_frame", current_frame),
                managers={
                    "Provider": {
                        "status": "unavailable",
                        "unavailable_reason": _require_text("reason", reason),
                    }
                },
                active_modulation_ids=(),
                last_failure=MicroMachineBridgeFailureMode.PROVIDER_UNAVAILABLE,
            )
        )


def publish_policy_modulation_provider_output(
    provider_output: object,
    backend: MicroMachineModulationBackend,
    *,
    current_frame: int,
    default_source: PolicyModulationSource | str = (
        PolicyModulationSource.NEURAL_REPRESENTATION
    ),
    default_goal: str | None = None,
    update_id: str | None = None,
    rollback_update_id: str | None = None,
) -> MicroMachineBackendPublishResult:
    """Compile provider output and publish it through any modulation backend."""

    compile_result = compile_policy_modulation_provider_output(
        provider_output,
        default_source=default_source,
        default_goal=default_goal,
    )
    if not compile_result.ok or compile_result.vector is None:
        return MicroMachineBackendPublishResult(compile_result=compile_result)
    update = backend.publish_vector(
        compile_result.vector,
        current_frame=current_frame,
        update_id=update_id,
        rollback_update_id=rollback_update_id,
    )
    return MicroMachineBackendPublishResult(
        compile_result=compile_result,
        update=update,
    )


def flatten_blackboard_update(update: MicroMachineBlackboardUpdate) -> str:
    """Return a C++-friendly ``key=value`` representation of an update."""

    document = update.to_dict()
    vector = document["vector"]
    if not isinstance(vector, Mapping):
        raise ValueError("update vector must be a mapping.")
    rows: list[tuple[str, object]] = [
        ("protocol_version", MICROMACHINE_BRIDGE_PROTOCOL_VERSION),
        ("update_id", update.update_id),
        ("issued_at_frame", update.issued_at_frame),
        ("expires_at_frame", update.expires_at_frame),
        ("rollback_update_id", update.rollback_update_id or ""),
        ("goal", vector["goal"]),
        ("source", vector["source"]),
        ("override_level", vector["override_level"]),
        ("confidence", vector["confidence"]),
        ("ttl_seconds", vector["ttl_seconds"]),
        ("manager_bias_domains", ",".join(update.manager_bias_domains)),
    ]
    for domain in (
        "strategy",
        "economy",
        "tech",
        "production",
        "combat",
        "scouting",
        "squad",
        "emergency",
    ):
        value = vector.get(domain, {})
        if isinstance(value, Mapping):
            _flatten_mapping(rows, domain, value)
    constraints = vector.get("constraints", ())
    if isinstance(constraints, list):
        rows.append(("constraints.count", len(constraints)))
        for index, constraint in enumerate(constraints):
            if isinstance(constraint, Mapping):
                for key, value in constraint.items():
                    rows.append((f"constraints.{index}.{key}", value))
    text = "".join(f"{key}={_format_kv_value(value)}\n" for key, value in rows)
    return text


def _flatten_mapping(
    rows: list[tuple[str, object]],
    prefix: str,
    mapping: Mapping[str, object],
) -> None:
    for key, value in mapping.items():
        flat_key = f"{prefix}.{key}"
        if isinstance(value, Mapping):
            _flatten_mapping(rows, flat_key, value)
        elif isinstance(value, list):
            rows.append((flat_key, ",".join(str(item) for item in value)))
        else:
            rows.append((flat_key, value))


def _format_kv_value(value: object) -> str:
    if value is None:
        return ""
    if type(value) is bool:
        return "true" if value else "false"
    return str(value).replace("\n", " ").replace("\r", " ").strip()


def _read_json_mapping(path: Path) -> Mapping[str, object]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object.")
    reject_raw_policy_control_keys(payload)
    return payload


def _coerce_telemetry(
    telemetry: MicroMachineTelemetry | Mapping[str, object],
) -> MicroMachineTelemetry:
    if isinstance(telemetry, MicroMachineTelemetry):
        document = deepcopy(telemetry.to_dict())
        reject_raw_policy_control_keys(document)
        return MicroMachineTelemetry.from_mapping(document)
    if isinstance(telemetry, Mapping):
        document = deepcopy(dict(telemetry))
        reject_raw_policy_control_keys(document)
        return MicroMachineTelemetry.from_mapping(document)
    raise ValueError("telemetry must be a MicroMachineTelemetry or mapping.")


def _clone_telemetry(telemetry: MicroMachineTelemetry) -> MicroMachineTelemetry:
    return _coerce_telemetry(telemetry)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(text)
    os.replace(temporary, path)


def _append_jsonl(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def _new_update_id() -> str:
    return f"voi-mm-{uuid.uuid4().hex}"


def _non_negative_int(field_name: str, value: object) -> int:
    if type(value) is bool or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer.")
    if value < 0:
        raise ValueError(f"{field_name} cannot be negative.")
    return value


def _require_text(field_name: str, value: object) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")
    return value.strip()
