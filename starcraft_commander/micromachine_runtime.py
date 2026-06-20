"""Filesystem runtime bridge for MicroMachine policy modulation.

This module turns the issue #10 contracts into a practical sidecar transport:
validated Python modulation updates are written as canonical JSON plus a flat
``key=value`` overlay that a C++ MicroMachine hook can read with only the C++
standard library.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from starcraft_commander.micromachine_bridge import (
    MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
    MicroMachineBlackboardUpdate,
    MicroMachineBridgeFailureMode,
    MicroMachineTelemetry,
    validate_micromachine_blackboard_update,
)
from starcraft_commander.policy_modulation import (
    PolicyModulationVector,
    reject_raw_policy_control_keys,
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

    def ingest_telemetry(self, telemetry: MicroMachineTelemetry | Mapping[str, object]) -> MicroMachineTelemetry:
        """Validate and persist telemetry emitted by MicroMachine."""

        if isinstance(telemetry, MicroMachineTelemetry):
            document = telemetry.to_dict()
            parsed = telemetry
        elif isinstance(telemetry, Mapping):
            reject_raw_policy_control_keys(telemetry)
            parsed = MicroMachineTelemetry.from_mapping(telemetry)
            document = parsed.to_dict()
        else:
            raise ValueError("telemetry must be a MicroMachineTelemetry or mapping.")
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
