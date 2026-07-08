"""MicroMachine sidecar and blackboard protocol contracts.

The contracts here specify how issue #10 policy modulation crosses into a
MicroMachine-style C++ bot without replacing the bot's autonomous managers.
They are JSON-ready, stdlib-only, and intentionally keep raw StarCraft runtime
commands out of the Python boundary.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from starcraft_commander.policy_modulation import (
    PolicyModulationVector,
    reject_raw_policy_control_keys,
)


MICROMACHINE_BRIDGE_PROTOCOL_VERSION: Final[str] = "voi-mm-bridge/v1"
MICROMACHINE_GAME_LOOPS_PER_SECOND: Final[int] = 22
"""Conservative integer game-loop conversion for blackboard TTL expiry."""

MICROMACHINE_UPDATE_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
)
"""Safe identifier subset shared by JSON telemetry and KV blackboard files."""


class MicroMachineBridgeMessageType(str, Enum):
    """Message types exchanged with the MicroMachine sidecar."""

    TELEMETRY = "telemetry"
    MODULATION_UPDATE = "modulation_update"
    ROLLBACK = "rollback"
    HEARTBEAT = "heartbeat"
    ERROR = "error"


class MicroMachineBridgeFailureMode(str, Enum):
    """Failure modes that must be surfaced without crashing the bridge."""

    STALE_MODULATION = "stale_modulation"
    INVALID_PAYLOAD = "invalid_payload"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    BRIDGE_DISCONNECTED = "bridge_disconnected"
    EMERGENCY_ROLLBACK = "emergency_rollback"


MICROMACHINE_TELEMETRY_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "required": [
        "protocol_version",
        "frame",
        "bot_name",
        "race",
        "managers",
        "active_modulation_ids",
    ],
    "properties": {
        "protocol_version": {"const": MICROMACHINE_BRIDGE_PROTOCOL_VERSION},
        "frame": {"type": "integer", "minimum": 0},
        "bot_name": {"type": "string"},
        "race": {"type": "string"},
        "managers": {"type": "object"},
        "active_modulation_ids": {"type": "array", "items": {"type": "string"}},
        "last_failure": {"type": ["string", "null"]},
    },
}
"""JSON-schema-like telemetry contract; validated by local dataclasses."""


MICROMACHINE_MODULATION_UPDATE_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "required": [
        "protocol_version",
        "update_id",
        "issued_at_frame",
        "expires_at_frame",
        "vector",
        "active_constraints",
        "manager_bias_domains",
        "rollback_update_id",
    ],
    "properties": {
        "protocol_version": {"const": MICROMACHINE_BRIDGE_PROTOCOL_VERSION},
        "update_id": {
            "type": "string",
            "pattern": MICROMACHINE_UPDATE_ID_PATTERN.pattern,
        },
        "issued_at_frame": {"type": "integer", "minimum": 0},
        "expires_at_frame": {"type": "integer", "minimum": 0},
        "vector": {"type": "object"},
        "active_constraints": {"type": "array"},
        "manager_bias_domains": {"type": "array"},
        "rollback_update_id": {
            "type": ["string", "null"],
            "pattern": MICROMACHINE_UPDATE_ID_PATTERN.pattern,
        },
    },
}
"""JSON-schema-like modulation update contract for the sidecar blackboard."""


@dataclass(frozen=True)
class MicroMachineManagerHook:
    """One MicroMachine manager seam that may consume modulation bias."""

    domain: str
    manager: str
    hook: str
    responsibility: str

    def __post_init__(self) -> None:
        for field_name in ("domain", "manager", "hook", "responsibility"):
            object.__setattr__(
                self,
                field_name,
                _require_text(field_name, getattr(self, field_name)),
            )

    def to_dict(self) -> dict[str, str]:
        return {
            "domain": self.domain,
            "manager": self.manager,
            "hook": self.hook,
            "responsibility": self.responsibility,
        }


@dataclass(frozen=True)
class MicroMachineTelemetry:
    """Telemetry snapshot sent from MicroMachine to the Python sidecar."""

    frame: int
    bot_name: str = "MicroMachine"
    race: str = "Terran"
    managers: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    active_modulation_ids: tuple[str, ...] = ()
    last_failure: MicroMachineBridgeFailureMode | str | None = None
    protocol_version: str = MICROMACHINE_BRIDGE_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _require_protocol(self.protocol_version)
        object.__setattr__(self, "frame", _non_negative_int("frame", self.frame))
        object.__setattr__(self, "bot_name", _require_text("bot_name", self.bot_name))
        object.__setattr__(self, "race", _require_text("race", self.race))
        managers = _validate_manager_payloads(self.managers)
        object.__setattr__(self, "managers", managers)
        object.__setattr__(
            self,
            "active_modulation_ids",
            _string_tuple("active_modulation_ids", self.active_modulation_ids),
        )
        failure = self.last_failure
        if failure is not None:
            failure = _coerce_failure_mode(failure)
        object.__setattr__(self, "last_failure", failure)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object]) -> "MicroMachineTelemetry":
        reject_raw_policy_control_keys(mapping)
        return cls(
            protocol_version=str(mapping.get("protocol_version", "")),
            frame=_int_from_mapping(mapping, "frame"),
            bot_name=str(mapping.get("bot_name", "MicroMachine")),
            race=str(mapping.get("race", "Terran")),
            managers=_mapping_from_mapping(mapping, "managers", default={}),
            active_modulation_ids=_string_tuple(
                "active_modulation_ids",
                mapping.get("active_modulation_ids", ()),
            ),
            last_failure=mapping.get("last_failure"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "protocol_version": self.protocol_version,
            "frame": self.frame,
            "bot_name": self.bot_name,
            "race": self.race,
            "managers": {key: dict(value) for key, value in self.managers.items()},
            "active_modulation_ids": list(self.active_modulation_ids),
            "last_failure": self.last_failure.value if self.last_failure else None,
        }


@dataclass(frozen=True)
class MicroMachineBlackboardUpdate:
    """One modulation vector written to the MicroMachine blackboard."""

    update_id: str
    vector: PolicyModulationVector
    issued_at_frame: int
    expires_at_frame: int | None = None
    rollback_update_id: str | None = None
    protocol_version: str = MICROMACHINE_BRIDGE_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _require_protocol(self.protocol_version)
        object.__setattr__(
            self,
            "update_id",
            require_micromachine_update_id("update_id", self.update_id),
        )
        object.__setattr__(
            self,
            "issued_at_frame",
            _non_negative_int("issued_at_frame", self.issued_at_frame),
        )
        if not isinstance(self.vector, PolicyModulationVector):
            raise ValueError("vector must be a PolicyModulationVector.")
        expires_at_frame = self.expires_at_frame
        if expires_at_frame is None:
            expires_at_frame = (
                self.issued_at_frame
                + self.vector.ttl_seconds * MICROMACHINE_GAME_LOOPS_PER_SECOND
            )
        object.__setattr__(
            self,
            "expires_at_frame",
            _non_negative_int("expires_at_frame", expires_at_frame),
        )
        if self.expires_at_frame <= self.issued_at_frame:
            raise ValueError("expires_at_frame must be after issued_at_frame.")
        if self.rollback_update_id is not None:
            object.__setattr__(
                self,
                "rollback_update_id",
                require_micromachine_update_id(
                    "rollback_update_id",
                    self.rollback_update_id,
                ),
            )

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object]) -> "MicroMachineBlackboardUpdate":
        reject_raw_policy_control_keys(mapping)
        vector_payload = mapping.get("vector")
        if not isinstance(vector_payload, Mapping):
            raise ValueError("vector must be a mapping.")
        rollback_update_id = mapping.get("rollback_update_id")
        if rollback_update_id is not None and type(rollback_update_id) is not str:
            raise ValueError("rollback_update_id must be a string or null.")
        expires_at_frame = mapping.get("expires_at_frame")
        return cls(
            protocol_version=str(mapping.get("protocol_version", "")),
            update_id=_text_from_mapping(mapping, "update_id"),
            vector=PolicyModulationVector.from_mapping(vector_payload),
            issued_at_frame=_int_from_mapping(mapping, "issued_at_frame"),
            expires_at_frame=(
                _non_negative_int("expires_at_frame", expires_at_frame)
                if expires_at_frame is not None
                else None
            ),
            rollback_update_id=rollback_update_id,
        )

    @property
    def manager_bias_domains(self) -> tuple[str, ...]:
        return tuple(
            domain
            for domain in (
                "strategy",
                "economy",
                "workers",
                "tech",
                "production",
                "combat",
                "scouting",
                "squad",
                "scope",
                "lifetime",
                "tactical_task",
                "emergency",
            )
            if _domain_has_signal(getattr(self.vector, domain))
        )

    def is_stale(self, current_frame: int) -> bool:
        return _non_negative_int("current_frame", current_frame) > self.expires_at_frame

    def to_dict(self) -> dict[str, object]:
        return {
            "protocol_version": self.protocol_version,
            "update_id": self.update_id,
            "issued_at_frame": self.issued_at_frame,
            "expires_at_frame": self.expires_at_frame,
            "vector": self.vector.to_dict(),
            "active_constraints": [
                constraint.to_dict() for constraint in self.vector.constraints
            ],
            "manager_bias_domains": list(self.manager_bias_domains),
            "rollback_update_id": self.rollback_update_id,
        }


@dataclass(frozen=True)
class MicroMachineRollbackCommand:
    """Command that asks the sidecar to remove or override a prior update."""

    rollback_update_id: str
    requested_at_frame: int
    reason: str
    failure_mode: MicroMachineBridgeFailureMode | str = (
        MicroMachineBridgeFailureMode.EMERGENCY_ROLLBACK
    )
    protocol_version: str = MICROMACHINE_BRIDGE_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _require_protocol(self.protocol_version)
        object.__setattr__(
            self,
            "rollback_update_id",
            _require_text("rollback_update_id", self.rollback_update_id),
        )
        object.__setattr__(
            self,
            "requested_at_frame",
            _non_negative_int("requested_at_frame", self.requested_at_frame),
        )
        object.__setattr__(self, "reason", _require_text("reason", self.reason))
        object.__setattr__(
            self,
            "failure_mode",
            _coerce_failure_mode(self.failure_mode),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "protocol_version": self.protocol_version,
            "rollback_update_id": self.rollback_update_id,
            "requested_at_frame": self.requested_at_frame,
            "reason": self.reason,
            "failure_mode": self.failure_mode.value,
        }


@dataclass(frozen=True)
class MicroMachineBridgeEnvelope:
    """Transport envelope for sidecar messages."""

    message_type: MicroMachineBridgeMessageType | str
    sequence: int
    frame: int
    payload: Mapping[str, object] = field(default_factory=dict)
    protocol_version: str = MICROMACHINE_BRIDGE_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _require_protocol(self.protocol_version)
        object.__setattr__(
            self,
            "message_type",
            _coerce_message_type(self.message_type),
        )
        object.__setattr__(self, "sequence", _non_negative_int("sequence", self.sequence))
        object.__setattr__(self, "frame", _non_negative_int("frame", self.frame))
        if not isinstance(self.payload, Mapping):
            raise ValueError("payload must be a mapping.")
        reject_raw_policy_control_keys(self.payload, path="payload")
        object.__setattr__(self, "payload", dict(self.payload))

    def to_dict(self) -> dict[str, object]:
        return {
            "protocol_version": self.protocol_version,
            "message_type": self.message_type.value,
            "sequence": self.sequence,
            "frame": self.frame,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class MicroMachineBridgeValidationResult:
    """Non-throwing validation result for incoming blackboard updates."""

    accepted: bool
    update: MicroMachineBlackboardUpdate | None = None
    failure_mode: MicroMachineBridgeFailureMode | str | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "accepted", bool(self.accepted))
        failure_mode = self.failure_mode
        if failure_mode is not None:
            failure_mode = _coerce_failure_mode(failure_mode)
        object.__setattr__(self, "failure_mode", failure_mode)
        if self.accepted and self.update is None:
            raise ValueError("accepted validation requires an update.")
        if not self.accepted and self.failure_mode is None:
            raise ValueError("rejected validation requires a failure mode.")
        if self.reason:
            object.__setattr__(self, "reason", _require_text("reason", self.reason))

    def to_dict(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "update": self.update.to_dict() if self.update else None,
            "failure_mode": self.failure_mode.value if self.failure_mode else None,
            "reason": self.reason,
        }


def validate_micromachine_blackboard_update(
    payload: object,
    *,
    current_frame: int,
) -> MicroMachineBridgeValidationResult:
    """Validate a modulation update without throwing across the bridge."""

    try:
        if not isinstance(payload, Mapping):
            return _validation_failure(
                MicroMachineBridgeFailureMode.INVALID_PAYLOAD,
                "blackboard update payload must be a mapping.",
            )
        update = MicroMachineBlackboardUpdate.from_mapping(payload)
        if update.is_stale(current_frame):
            return _validation_failure(
                MicroMachineBridgeFailureMode.STALE_MODULATION,
                "blackboard update is stale at the current frame.",
            )
        return MicroMachineBridgeValidationResult(accepted=True, update=update)
    except (TypeError, ValueError) as exc:
        return _validation_failure(
            MicroMachineBridgeFailureMode.INVALID_PAYLOAD,
            str(exc),
        )


def build_micromachine_bridge_error_envelope(
    *,
    failure_mode: MicroMachineBridgeFailureMode | str,
    reason: str,
    sequence: int,
    frame: int,
) -> MicroMachineBridgeEnvelope:
    """Build a JSON-ready sidecar error envelope for known failure modes."""

    mode = _coerce_failure_mode(failure_mode)
    return MicroMachineBridgeEnvelope(
        message_type=MicroMachineBridgeMessageType.ERROR,
        sequence=sequence,
        frame=frame,
        payload={"failure_mode": mode.value, "reason": _require_text("reason", reason)},
    )


def _validation_failure(
    failure_mode: MicroMachineBridgeFailureMode,
    reason: str,
) -> MicroMachineBridgeValidationResult:
    return MicroMachineBridgeValidationResult(
        accepted=False,
        failure_mode=failure_mode,
        reason=reason,
    )


def _domain_has_signal(domain: object) -> bool:
    to_dict = getattr(domain, "to_dict", None)
    if not callable(to_dict):
        return False
    payload = to_dict()
    try:
        default_payload = type(domain)().to_dict()
    except Exception:  # noqa: BLE001 - unknown domain-like objects use fallback semantics.
        default_payload = {}
    for key, value in payload.items():
        if key in default_payload:
            if value != default_payload[key]:
                return True
            continue
        if _value_has_signal(value):
            return True
    return False


def _value_has_signal(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    if isinstance(value, str):
        return value not in {"", "balanced"}
    return value is not None


def _validate_manager_payloads(
    managers: Mapping[str, Mapping[str, object]],
) -> dict[str, dict[str, object]]:
    if not isinstance(managers, Mapping):
        raise ValueError("managers must be a mapping.")
    result: dict[str, dict[str, object]] = {}
    for manager, payload in managers.items():
        manager_name = _require_text("manager", manager)
        if not isinstance(payload, Mapping):
            raise ValueError("manager payloads must be mappings.")
        reject_raw_policy_control_keys(payload, path=f"managers.{manager_name}")
        result[manager_name] = dict(payload)
    return result


def _coerce_message_type(
    value: MicroMachineBridgeMessageType | str,
) -> MicroMachineBridgeMessageType:
    if isinstance(value, MicroMachineBridgeMessageType):
        return value
    if type(value) is not str:
        raise ValueError("message_type must be a string.")
    try:
        return MicroMachineBridgeMessageType(value.strip().lower())
    except ValueError as exc:
        raise ValueError(f"unsupported bridge message type: {value!r}.") from exc


def _coerce_failure_mode(
    value: MicroMachineBridgeFailureMode | str,
) -> MicroMachineBridgeFailureMode:
    if isinstance(value, MicroMachineBridgeFailureMode):
        return value
    if type(value) is not str:
        raise ValueError("failure_mode must be a string.")
    try:
        return MicroMachineBridgeFailureMode(value.strip().lower())
    except ValueError as exc:
        raise ValueError(f"unsupported bridge failure mode: {value!r}.") from exc


def _mapping_from_mapping(
    mapping: Mapping[str, object],
    key: str,
    *,
    default: Mapping[str, object],
) -> Mapping[str, object]:
    value = mapping.get(key, default)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be a mapping.")
    return value


def _text_from_mapping(mapping: Mapping[str, object], key: str) -> str:
    if key not in mapping:
        raise ValueError(f"{key} is required.")
    return _require_text(key, mapping[key])


def _int_from_mapping(mapping: Mapping[str, object], key: str) -> int:
    if key not in mapping:
        raise ValueError(f"{key} is required.")
    return _non_negative_int(key, mapping[key])


def _non_negative_int(field_name: str, value: object) -> int:
    if type(value) is bool or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer.")
    if value < 0:
        raise ValueError(f"{field_name} cannot be negative.")
    return value


def _require_protocol(value: str) -> None:
    if value != MICROMACHINE_BRIDGE_PROTOCOL_VERSION:
        raise ValueError(
            "protocol_version must be "
            f"{MICROMACHINE_BRIDGE_PROTOCOL_VERSION!r}."
        )


def _require_text(field_name: str, value: object) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")
    return value.strip()


def require_micromachine_update_id(field_name: str, value: object) -> str:
    update_id = _require_text(field_name, value)
    if MICROMACHINE_UPDATE_ID_PATTERN.fullmatch(update_id) is None:
        raise ValueError(
            f"{field_name} must match {MICROMACHINE_UPDATE_ID_PATTERN.pattern!r}."
        )
    return update_id


def _string_tuple(name: str, values: object) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f"{name} must be a sequence of strings.")
    return tuple(_require_text(name, value) for value in values)


MICROMACHINE_MANAGER_HOOKS: Final[tuple[MicroMachineManagerHook, ...]] = (
    MicroMachineManagerHook(
        domain="strategy",
        manager="StrategyManager",
        hook="build/posture selection bias",
        responsibility="Biases strategic posture, preferred builds, and avoided builds.",
    ),
    MicroMachineManagerHook(
        domain="production",
        manager="ProductionManager / BuildOrderQueue",
        hook="queue and tech deviation bias",
        responsibility="Biases production priorities without direct build commands.",
    ),
    MicroMachineManagerHook(
        domain="combat",
        manager="CombatCommander",
        hook="attack, hold, retreat posture bias",
        responsibility="Modulates fight posture while squads still execute tactics.",
    ),
    MicroMachineManagerHook(
        domain="combat",
        manager="CombatAnalyzer",
        hook="fight acceptance threshold bias",
        responsibility="Adjusts combat-sim confidence margins and preserve-army bias.",
    ),
    MicroMachineManagerHook(
        domain="squad",
        manager="Squad / SquadOrder",
        hook="role allocation and regroup bias",
        responsibility="Biases main army, defense, harassment, drops, and regrouping.",
    ),
    MicroMachineManagerHook(
        domain="scope",
        manager="CombatCommander / Squad",
        hook="semantic unit-scope resolution",
        responsibility=(
            "Carries unit-selection-like intent as semantic group, unit class, "
            "location, and duration constraints without raw unit tags."
        ),
    ),
    MicroMachineManagerHook(
        domain="tactical_task",
        manager="CombatCommander / ScoutManager / ProductionManager",
        hook="bounded task lifecycle ticket",
        responsibility=(
            "Accepts/refuses manager-bounded tasks such as scout_with_units, "
            "pressure_with_main_army, sustain_production, tech_transition, and "
            "expand_or_land_command_center."
        ),
    ),
    MicroMachineManagerHook(
        domain="scouting",
        manager="ScoutManager",
        hook="target and risk bias",
        responsibility="Biases scout priority, targets, and fresh-observation needs.",
    ),
    MicroMachineManagerHook(
        domain="economy",
        manager="WorkerManager",
        hook="economy, repair, and emergency worker bias",
        responsibility="Biases expansion, worker production, repair, and worker pull.",
    ),
    MicroMachineManagerHook(
        domain="workers",
        manager="WorkerManager",
        hook="repeat worker order guard",
        responsibility=(
            "Suppresses repeated identical worker commands within a bounded frame "
            "window without issuing replacement unit orders."
        ),
    ),
    MicroMachineManagerHook(
        domain="combat",
        manager="libvoxelbot combat simulation",
        hook="combat simulation threshold bias",
        responsibility="Biases simulated fight acceptance without issuing unit orders.",
    ),
)
"""Required MicroMachine hook mapping for issue #10 sidecar integration."""
