"""Typed input and output contracts for ToyCraft state narration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal

from toycraft_commander.feasibility import ToyCraftState
from toycraft_commander.intents import (
    FeasibilityErrorReason,
    IntentValidationResult,
    Priority,
    ValidationStatus,
)
from toycraft_commander.resources import get_available_supply

if TYPE_CHECKING:
    from toycraft_commander.executor import ToyCraftExecutionResult


NarratorResponseKind = Literal["executed", "read_only", "blocked"]

_FAILURE_NARRATION_TEMPLATES: Mapping[
    FeasibilityErrorReason,
    tuple[str, str],
] = {
    FeasibilityErrorReason.MALFORMED_PAYLOAD: (
        "명령 구조를 이해하지 못했습니다",
        "지원되는 명령 형태로 대상과 수량을 함께 다시 말해 주세요",
    ),
    FeasibilityErrorReason.MISSING_REQUIRED_FIELD: (
        "필수 정보가 빠졌습니다",
        "대상, 수량, 위치 중 빠진 값을 포함해 다시 지시해 주세요",
    ),
    FeasibilityErrorReason.UNSUPPORTED_INTENT: (
        "Phase 0에서 지원하지 않는 명령입니다",
        "자원 채취, 건설, 생산, 정찰, 방어, 수리, 확장, 견제, 상태 보고 중 하나로 지시해 주세요",
    ),
    FeasibilityErrorReason.INVALID_FIELD_VALUE: (
        "명령 값이 ToyCraft에서 처리할 수 없는 형태입니다",
        "지원되는 유닛, 구조물, 위치 이름으로 다시 지시해 주세요",
    ),
    FeasibilityErrorReason.INSUFFICIENT_MINERALS: (
        "미네랄이 부족합니다",
        "SCV를 미네랄에 더 붙이거나 더 싼 명령부터 처리해 주세요",
    ),
    FeasibilityErrorReason.INSUFFICIENT_GAS: (
        "가스가 부족합니다",
        "Refinery를 확보한 뒤 가스 채취를 먼저 지시해 주세요",
    ),
    FeasibilityErrorReason.INSUFFICIENT_SUPPLY: (
        "보급이 막혔습니다",
        "Supply Depot을 먼저 건설한 뒤 생산 명령을 다시 내려 주세요",
    ),
    FeasibilityErrorReason.MISSING_PREREQUISITE: (
        "선행 조건이 아직 준비되지 않았습니다",
        "필요한 구조물을 먼저 완성한 뒤 다시 지시해 주세요",
    ),
    FeasibilityErrorReason.UNAVAILABLE_PRODUCER: (
        "생산 건물이 없거나 대기열을 받을 수 없습니다",
        "필요한 생산 건물을 완성하거나 대기열이 비길 기다려 주세요",
    ),
    FeasibilityErrorReason.UNAVAILABLE_WORKER: (
        "사용 가능한 SCV가 없습니다",
        "작업 중인 SCV를 비우거나 더 적은 수의 SCV로 다시 지시해 주세요",
    ),
    FeasibilityErrorReason.UNAVAILABLE_UNIT_GROUP: (
        "명령을 수행할 수 있는 부대가 없습니다",
        "가용 Marine, Vulture, SCV처럼 현재 보유한 부대를 골라 주세요",
    ),
    FeasibilityErrorReason.INVALID_TARGET: (
        "대상이 현재 명령에 맞지 않습니다",
        "방어는 아군 위치, 견제와 정찰은 적 위치, 수리는 손상된 아군 구조물로 지정해 주세요",
    ),
    FeasibilityErrorReason.LOCATION_UNAVAILABLE: (
        "지정한 위치를 사용할 수 없습니다",
        "main ramp, natural choke, natural expansion처럼 알려진 위치로 다시 지시해 주세요",
    ),
    FeasibilityErrorReason.CONSTRAINT_CONFLICT: (
        "조건이 서로 충돌합니다",
        "서로 반대되는 조건을 제거하거나 명령을 둘로 나눠 주세요",
    ),
    FeasibilityErrorReason.UNSUPPORTED_PHASE_ZERO_SCOPE: (
        "Phase 0 ToyCraft 범위를 벗어난 명령입니다",
        "Terran MVP 유닛과 구조물만 사용해 다시 지시해 주세요",
    ),
}

_INTENT_NARRATION_LABELS: Mapping[str, str] = {
    "GATHER_RESOURCE": "자원 채취",
    "BUILD_STRUCTURE": "건설",
    "TRAIN_WORKER": "SCV 생산",
    "TRAIN_ARMY": "병력 생산",
    "SCOUT": "정찰",
    "SUMMARIZE_STATE": "상태 보고",
    "DEFEND": "방어",
    "REPAIR": "수리",
    "EXPAND": "확장",
    "HARASS": "견제",
    "UNKNOWN": "알 수 없는",
}

_DEFAULT_BLOCKED_ACTIONABLE_ALTERNATIVE: Final[str] = (
    "명령을 하나로 구체화해 다시 내려 주세요. "
    "예: 상태 알려줘 / 일꾼 계속 찍어 / 본진에 배럭 지어."
)


@dataclass(frozen=True)
class NarratorFeasibilityIssue:
    """Narrator-safe copy of one feasibility issue."""

    reason: FeasibilityErrorReason
    message: str
    alternative: str
    fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.message.strip():
            raise ValueError("message must be non-empty.")
        if not self.alternative.strip():
            raise ValueError("alternative must be non-empty.")
        object.__setattr__(self, "fields", tuple(self.fields))

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready representation for UI and demo logs."""

        return {
            "reason": self.reason.value,
            "message": self.message,
            "alternative": self.alternative,
            "fields": list(self.fields),
        }


@dataclass(frozen=True)
class NarratorFeasibilityOutcome:
    """Narrator input for the validator's executable/rejected decision."""

    executable: bool
    status: ValidationStatus
    reason: str = ""
    alternative: str = ""
    missing_fields: tuple[str, ...] = ()
    reason_codes: tuple[FeasibilityErrorReason, ...] = ()
    issues: tuple[NarratorFeasibilityIssue, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "missing_fields", tuple(self.missing_fields))
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))
        object.__setattr__(self, "issues", tuple(self.issues))
        if self.executable and self.status != ValidationStatus.EXECUTABLE:
            raise ValueError("executable feasibility must use executable status.")
        if not self.executable and self.status != ValidationStatus.REJECTED:
            raise ValueError("rejected feasibility must use rejected status.")

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready representation for UI and demo logs."""

        return {
            "executable": self.executable,
            "status": self.status.value,
            "reason": self.reason,
            "alternative": self.alternative,
            "missing_fields": list(self.missing_fields),
            "reason_codes": [reason.value for reason in self.reason_codes],
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class StateNarratorSnapshot:
    """Compact ToyCraft state snapshot consumed by the narrator layer."""

    resources: Mapping[str, int]
    supply: Mapping[str, int]
    units: Mapping[str, int]
    structures: Mapping[str, int]
    busy_workers: int
    available_workers: int
    busy_producers: Mapping[str, int] = field(default_factory=dict)
    production_queues: Mapping[str, int] = field(default_factory=dict)
    production_orders: tuple[Mapping[str, object], ...] = ()
    construction_queue: tuple[Mapping[str, object], ...] = ()
    claimed_locations: tuple[str, ...] = ()
    damaged_targets: tuple[str, ...] = ()
    unit_positions: Mapping[str, str] = field(default_factory=dict)
    target_damage: Mapping[str, int] = field(default_factory=dict)
    pressure_mitigation: Mapping[str, int] = field(default_factory=dict)
    defeated_targets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "resources", dict(self.resources))
        object.__setattr__(self, "supply", dict(self.supply))
        object.__setattr__(self, "units", dict(self.units))
        object.__setattr__(self, "structures", dict(self.structures))
        object.__setattr__(self, "busy_producers", dict(self.busy_producers))
        object.__setattr__(self, "production_queues", dict(self.production_queues))
        object.__setattr__(
            self,
            "production_orders",
            tuple(dict(order) for order in self.production_orders),
        )
        object.__setattr__(
            self,
            "construction_queue",
            tuple(dict(order) for order in self.construction_queue),
        )
        object.__setattr__(self, "claimed_locations", tuple(self.claimed_locations))
        object.__setattr__(self, "damaged_targets", tuple(self.damaged_targets))
        object.__setattr__(self, "unit_positions", dict(self.unit_positions))
        object.__setattr__(self, "target_damage", dict(self.target_damage))
        object.__setattr__(self, "pressure_mitigation", dict(self.pressure_mitigation))
        object.__setattr__(self, "defeated_targets", tuple(self.defeated_targets))

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready state snapshot for narrator implementations."""

        return {
            "resources": dict(self.resources),
            "supply": dict(self.supply),
            "units": dict(self.units),
            "structures": dict(self.structures),
            "busy_workers": self.busy_workers,
            "available_workers": self.available_workers,
            "busy_producers": dict(self.busy_producers),
            "production_queues": dict(self.production_queues),
            "production_orders": [dict(order) for order in self.production_orders],
            "construction_queue": [dict(order) for order in self.construction_queue],
            "claimed_locations": list(self.claimed_locations),
            "damaged_targets": list(self.damaged_targets),
            "unit_positions": dict(self.unit_positions),
            "target_damage": dict(self.target_damage),
            "pressure_mitigation": dict(self.pressure_mitigation),
            "defeated_targets": list(self.defeated_targets),
        }


@dataclass(frozen=True)
class StateNarratorDelta:
    """One typed before/after state change consumed by narrator implementations."""

    name: str
    before: object
    after: object
    delta: int | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("delta name must be non-empty.")

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready delta payload."""

        payload = {
            "name": self.name,
            "before": _json_ready_value(self.before),
            "after": _json_ready_value(self.after),
        }
        if self.delta is not None:
            payload["delta"] = self.delta
        return payload


@dataclass(frozen=True)
class StateNarratorChangeSummary:
    """Structured state-change summary grouped by narrator concern."""

    resource_deltas: tuple[StateNarratorDelta, ...] = ()
    entity_deltas: tuple[StateNarratorDelta, ...] = ()
    map_deltas: tuple[StateNarratorDelta, ...] = ()
    raw_changes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "resource_deltas", tuple(self.resource_deltas))
        object.__setattr__(self, "entity_deltas", tuple(self.entity_deltas))
        object.__setattr__(self, "map_deltas", tuple(self.map_deltas))
        object.__setattr__(self, "raw_changes", tuple(self.raw_changes))

    @property
    def has_changes(self) -> bool:
        """Return whether any typed or raw state change is present."""

        return bool(
            self.resource_deltas
            or self.entity_deltas
            or self.map_deltas
            or self.raw_changes
        )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready grouped delta payload."""

        return {
            "resource_deltas": [delta.to_dict() for delta in self.resource_deltas],
            "entity_deltas": [delta.to_dict() for delta in self.entity_deltas],
            "map_deltas": [delta.to_dict() for delta in self.map_deltas],
            "raw_changes": list(self.raw_changes),
            "has_changes": self.has_changes,
        }


@dataclass(frozen=True)
class StateNarratorInput:
    """Complete narrator input for one commander command outcome."""

    command_text: str
    intent: str
    priority: Priority
    constraints: tuple[str, ...]
    feasibility: NarratorFeasibilityOutcome
    before_state: StateNarratorSnapshot
    after_state: StateNarratorSnapshot
    executed: bool
    read_only: bool
    state_changes: tuple[str, ...] = ()
    change_summary: StateNarratorChangeSummary = field(
        default_factory=StateNarratorChangeSummary
    )
    summary: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.intent.strip():
            raise ValueError("intent must be non-empty.")
        object.__setattr__(self, "constraints", tuple(self.constraints))
        object.__setattr__(self, "state_changes", tuple(self.state_changes))
        if not isinstance(self.change_summary, StateNarratorChangeSummary):
            raise TypeError("change_summary must be a StateNarratorChangeSummary.")
        object.__setattr__(self, "summary", dict(self.summary))
        if self.executed and not self.feasibility.executable:
            raise ValueError("executed narrator input requires executable feasibility.")
        if self.read_only and self.before_state != self.after_state:
            raise ValueError("read-only narrator input cannot change state.")
        if not self.executed and self.before_state != self.after_state:
            raise ValueError("rejected narrator input cannot change state.")

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready narrator input payload."""

        return {
            "command_text": self.command_text,
            "intent": self.intent,
            "priority": self.priority,
            "constraints": list(self.constraints),
            "feasibility": self.feasibility.to_dict(),
            "before_state": self.before_state.to_dict(),
            "after_state": self.after_state.to_dict(),
            "executed": self.executed,
            "read_only": self.read_only,
            "state_changes": list(self.state_changes),
            "change_summary": self.change_summary.to_dict(),
            "summary": dict(self.summary),
        }


@dataclass(frozen=True)
class StateNarratorResponseMetadata:
    """Machine-readable metadata attached to commander-facing narration."""

    command_text: str
    intent: str
    priority: Priority
    constraints: tuple[str, ...]
    response_kind: NarratorResponseKind
    validation_status: ValidationStatus
    executed: bool
    read_only: bool
    state_changed: bool
    state_changes: tuple[str, ...] = ()
    reason_codes: tuple[FeasibilityErrorReason, ...] = ()
    summary: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.intent.strip():
            raise ValueError("intent must be non-empty.")
        object.__setattr__(self, "constraints", tuple(self.constraints))
        object.__setattr__(self, "state_changes", tuple(self.state_changes))
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))
        object.__setattr__(self, "summary", dict(self.summary))
        if self.response_kind == "blocked" and self.executed:
            raise ValueError("blocked response metadata cannot be executed.")
        if self.response_kind != "blocked" and not self.executed:
            raise ValueError("non-blocked response metadata must be executed.")
        if self.response_kind == "read_only" and not self.read_only:
            raise ValueError("read_only response metadata must set read_only.")

    def to_dict(self) -> dict[str, object]:
        """Return JSON-ready response metadata for logs or UI adapters."""

        return {
            "command_text": self.command_text,
            "intent": self.intent,
            "priority": self.priority,
            "constraints": list(self.constraints),
            "response_kind": self.response_kind,
            "validation_status": self.validation_status.value,
            "executed": self.executed,
            "read_only": self.read_only,
            "state_changed": self.state_changed,
            "state_changes": list(self.state_changes),
            "reason_codes": [reason.value for reason in self.reason_codes],
            "summary": dict(self.summary),
        }


@dataclass(frozen=True)
class StateNarratorBlockedCommand:
    """Structured error report for a command that did not execute."""

    reason: str
    alternative: str
    reason_codes: tuple[FeasibilityErrorReason, ...] = ()
    missing_fields: tuple[str, ...] = ()
    issues: tuple[NarratorFeasibilityIssue, ...] = ()

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("blocked command reason must be non-empty.")
        if not self.alternative.strip():
            raise ValueError("blocked command alternative must be non-empty.")
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))
        object.__setattr__(self, "missing_fields", tuple(self.missing_fields))
        object.__setattr__(self, "issues", tuple(self.issues))

    def to_dict(self) -> dict[str, object]:
        """Return JSON-ready blocked-command details."""

        return {
            "reason": self.reason,
            "alternative": self.alternative,
            "reason_codes": [reason.value for reason in self.reason_codes],
            "missing_fields": list(self.missing_fields),
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class StateNarratorResponse:
    """Final State Narrator output contract for one commander response."""

    response_text: str
    metadata: StateNarratorResponseMetadata
    blocked_command: StateNarratorBlockedCommand | None = None

    def __post_init__(self) -> None:
        if not self.response_text.strip():
            raise ValueError("response_text must be non-empty.")
        if self.metadata.response_kind == "blocked" and self.blocked_command is None:
            raise ValueError("blocked responses require blocked_command details.")
        if self.metadata.response_kind != "blocked" and self.blocked_command is not None:
            raise ValueError("executed responses cannot include blocked_command details.")

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready narrated response payload."""

        return {
            "response_text": self.response_text,
            "metadata": self.metadata.to_dict(),
            "blocked_command": (
                None
                if self.blocked_command is None
                else self.blocked_command.to_dict()
            ),
        }


def build_feasibility_narrator_outcome(
    validation: IntentValidationResult,
) -> NarratorFeasibilityOutcome:
    """Convert validator output into narrator-safe feasibility input."""

    if not isinstance(validation, IntentValidationResult):
        raise TypeError("validation must be an IntentValidationResult.")

    issues = tuple(
        NarratorFeasibilityIssue(
            reason=issue.reason,
            message=issue.message,
            alternative=issue.alternative,
            fields=issue.fields,
        )
        for issue in validation.issues
    )
    return NarratorFeasibilityOutcome(
        executable=validation.executable,
        status=validation.status,
        reason=validation.reason,
        alternative=validation.alternative,
        missing_fields=validation.missing_fields,
        reason_codes=validation.reason_codes,
        issues=issues,
    )


def build_state_narrator_snapshot(state: ToyCraftState) -> StateNarratorSnapshot:
    """Convert ToyCraft state into the stable narrator state contract."""

    if not isinstance(state, ToyCraftState):
        raise TypeError("state must be a ToyCraftState.")

    return StateNarratorSnapshot(
        resources=state.resources.to_dict(),
        supply={
            **state.supply.to_dict(),
            "available_supply": get_available_supply(state.supply),
        },
        units=dict(state.units),
        structures=dict(state.structures),
        busy_workers=state.busy_workers,
        available_workers=state.available_worker_count(),
        busy_producers=dict(state.busy_producers),
        production_queues=dict(state.production_queues),
        production_orders=tuple(order.to_dict() for order in state.production_orders),
        construction_queue=tuple(order.to_dict() for order in state.construction_queue),
        claimed_locations=state.claimed_locations,
        damaged_targets=state.damaged_targets,
        unit_positions=dict(state.unit_positions),
        target_damage=dict(state.target_damage),
        pressure_mitigation=dict(state.pressure_mitigation),
        defeated_targets=state.defeated_targets,
    )


def build_state_change_summary(
    before_state: StateNarratorSnapshot,
    after_state: StateNarratorSnapshot,
    raw_changes: tuple[str, ...] = (),
) -> StateNarratorChangeSummary:
    """Build typed resource/entity/map deltas from narrator snapshots."""

    if not isinstance(before_state, StateNarratorSnapshot):
        raise TypeError("before_state must be a StateNarratorSnapshot.")
    if not isinstance(after_state, StateNarratorSnapshot):
        raise TypeError("after_state must be a StateNarratorSnapshot.")

    resource_deltas = (
        *_mapping_deltas("resources", before_state.resources, after_state.resources),
        *_mapping_deltas("supply", before_state.supply, after_state.supply),
    )
    entity_deltas = (
        *_mapping_deltas("units", before_state.units, after_state.units),
        *_mapping_deltas("structures", before_state.structures, after_state.structures),
        *_scalar_delta(
            "busy_workers",
            before_state.busy_workers,
            after_state.busy_workers,
        ),
        *_scalar_delta(
            "available_workers",
            before_state.available_workers,
            after_state.available_workers,
        ),
        *_mapping_deltas(
            "busy_producers",
            before_state.busy_producers,
            after_state.busy_producers,
        ),
        *_mapping_deltas(
            "production_queues",
            before_state.production_queues,
            after_state.production_queues,
        ),
        *_scalar_delta(
            "production_orders",
            before_state.production_orders,
            after_state.production_orders,
        ),
        *_scalar_delta(
            "construction_queue",
            before_state.construction_queue,
            after_state.construction_queue,
        ),
    )
    map_deltas = (
        *_scalar_delta(
            "claimed_locations",
            before_state.claimed_locations,
            after_state.claimed_locations,
        ),
        *_scalar_delta(
            "damaged_targets",
            before_state.damaged_targets,
            after_state.damaged_targets,
        ),
        *_mapping_deltas(
            "unit_positions",
            before_state.unit_positions,
            after_state.unit_positions,
        ),
        *_mapping_deltas(
            "target_damage",
            before_state.target_damage,
            after_state.target_damage,
        ),
        *_mapping_deltas(
            "pressure_mitigation",
            before_state.pressure_mitigation,
            after_state.pressure_mitigation,
        ),
        *_scalar_delta(
            "defeated_targets",
            before_state.defeated_targets,
            after_state.defeated_targets,
        ),
    )

    return StateNarratorChangeSummary(
        resource_deltas=resource_deltas,
        entity_deltas=entity_deltas,
        map_deltas=map_deltas,
        raw_changes=raw_changes,
    )


def build_execution_narrator_input(
    result: ToyCraftExecutionResult,
    *,
    command_text: str = "",
) -> StateNarratorInput:
    """Build narrator input from a completed command execution result."""

    priority, constraints = _priority_and_constraints(result.validation)
    before_state = build_state_narrator_snapshot(result.before_state)
    after_state = build_state_narrator_snapshot(result.after_state)
    return StateNarratorInput(
        command_text=command_text,
        intent=result.intent,
        priority=priority,
        constraints=constraints,
        feasibility=build_feasibility_narrator_outcome(result.validation),
        before_state=before_state,
        after_state=after_state,
        executed=result.executed,
        read_only=result.read_only,
        state_changes=result.state_changes,
        change_summary=build_state_change_summary(
            before_state,
            after_state,
            result.state_changes,
        ),
        summary=result.summary,
    )


def build_rejected_narrator_input(
    validation: IntentValidationResult,
    state: ToyCraftState,
    *,
    command_text: str = "",
    intent: str = "UNKNOWN",
) -> StateNarratorInput:
    """Build narrator input directly from a rejected feasibility outcome."""

    if validation.executable:
        raise ValueError("build_rejected_narrator_input requires rejected validation.")

    payload = validation.payload
    if payload is not None:
        intent = payload.intent
    priority, constraints = _priority_and_constraints(validation)
    snapshot = build_state_narrator_snapshot(state)
    change_summary = build_state_change_summary(snapshot, snapshot)
    return StateNarratorInput(
        command_text=command_text,
        intent=intent,
        priority=priority,
        constraints=constraints,
        feasibility=build_feasibility_narrator_outcome(validation),
        before_state=snapshot,
        after_state=snapshot,
        executed=False,
        read_only=True,
        change_summary=change_summary,
    )


def build_state_narrator_metadata(
    narrator_input: StateNarratorInput,
) -> StateNarratorResponseMetadata:
    """Build response metadata from the stable narrator input contract."""

    if not isinstance(narrator_input, StateNarratorInput):
        raise TypeError("narrator_input must be a StateNarratorInput.")

    return StateNarratorResponseMetadata(
        command_text=narrator_input.command_text,
        intent=narrator_input.intent,
        priority=narrator_input.priority,
        constraints=narrator_input.constraints,
        response_kind=_response_kind(narrator_input),
        validation_status=narrator_input.feasibility.status,
        executed=narrator_input.executed,
        read_only=narrator_input.read_only,
        state_changed=narrator_input.change_summary.has_changes,
        state_changes=narrator_input.state_changes,
        reason_codes=narrator_input.feasibility.reason_codes,
        summary=narrator_input.summary,
    )


def build_blocked_command_report(
    narrator_input: StateNarratorInput,
) -> StateNarratorBlockedCommand:
    """Build the structured blocked-command report for rejected commands."""

    if not isinstance(narrator_input, StateNarratorInput):
        raise TypeError("narrator_input must be a StateNarratorInput.")
    if narrator_input.feasibility.executable:
        raise ValueError("blocked command reports require rejected feasibility.")

    return StateNarratorBlockedCommand(
        reason=_blocked_reason(narrator_input.feasibility),
        alternative=_blocked_alternative(narrator_input.feasibility),
        reason_codes=narrator_input.feasibility.reason_codes,
        missing_fields=narrator_input.feasibility.missing_fields,
        issues=narrator_input.feasibility.issues,
    )


def build_state_narrator_response(
    narrator_input: StateNarratorInput,
    *,
    response_text: str | None = None,
) -> StateNarratorResponse:
    """Build the final narrated response contract from narrator input."""

    metadata = build_state_narrator_metadata(narrator_input)
    blocked_command = None
    if metadata.response_kind == "blocked":
        blocked_command = build_blocked_command_report(narrator_input)
        if response_text is None:
            response_text = render_blocked_narration(narrator_input)
        else:
            response_text = _ensure_blocked_response_has_actionable_alternative(
                response_text,
                blocked_command,
            )
    elif response_text is None:
        response_text = render_success_narration(narrator_input)

    return StateNarratorResponse(
        response_text=response_text,
        metadata=metadata,
        blocked_command=blocked_command,
    )


def _ensure_blocked_response_has_actionable_alternative(
    response_text: str,
    blocked_command: StateNarratorBlockedCommand,
) -> str:
    """Keep custom rejected responses from losing the required next step."""

    if "추천 행동" in response_text or blocked_command.alternative in response_text:
        return response_text
    return f"{response_text.rstrip()} 추천 행동: {blocked_command.alternative}."


def build_execution_narrator_response(
    result: ToyCraftExecutionResult,
    *,
    command_text: str = "",
) -> StateNarratorResponse:
    """Build the final narrator output contract from an execution result."""

    narrator_input = build_execution_narrator_input(result, command_text=command_text)
    return build_state_narrator_response(
        narrator_input,
        response_text=render_success_narration(narrator_input),
    )


def render_success_narration(narrator_input: StateNarratorInput) -> str:
    """Render commander-facing Korean text for a successful execution outcome."""

    if not isinstance(narrator_input, StateNarratorInput):
        raise TypeError("narrator_input must be a StateNarratorInput.")
    if not narrator_input.executed:
        raise ValueError("success narration requires executed narrator input.")
    if not narrator_input.feasibility.executable:
        raise ValueError("success narration requires executable feasibility.")

    if narrator_input.read_only:
        return _render_read_only_success(narrator_input)

    if narrator_input.intent == "GATHER_RESOURCE":
        return _render_gather_resource_success(narrator_input)
    if narrator_input.intent == "BUILD_STRUCTURE":
        return _render_build_structure_success(narrator_input)
    if narrator_input.intent in {"TRAIN_WORKER", "TRAIN_ARMY"}:
        return _render_train_unit_success(narrator_input)
    if narrator_input.intent == "EXPAND":
        return _render_expand_success(narrator_input)
    if narrator_input.intent in {"DEFEND", "HARASS"}:
        return _render_combat_success(narrator_input)

    return _render_generic_success(narrator_input)


def render_blocked_narration(narrator_input: StateNarratorInput) -> str:
    """Render player-facing Korean text for a command that did not execute."""

    if not isinstance(narrator_input, StateNarratorInput):
        raise TypeError("narrator_input must be a StateNarratorInput.")
    if narrator_input.executed:
        raise ValueError("blocked narration requires non-executed narrator input.")
    if narrator_input.feasibility.executable:
        raise ValueError("blocked narration requires rejected feasibility.")

    blocked = build_blocked_command_report(narrator_input)
    intent_label = _INTENT_NARRATION_LABELS.get(narrator_input.intent, narrator_input.intent)
    command_text = (
        f" 입력 명령: {narrator_input.command_text}."
        if narrator_input.command_text.strip()
        else ""
    )
    reason_text = _blocked_reason_text(blocked)
    alternative_text = _blocked_alternative_text(blocked)
    state_text = _blocked_state_sentence(narrator_input.after_state)

    return (
        f"실행하지 않았습니다: {intent_label} 명령은 상태를 바꾸지 않았습니다."
        f"{command_text} 막힌 이유: {reason_text}. "
        f"추천 행동: {alternative_text}. {state_text}"
    )


def _blocked_reason_text(blocked: StateNarratorBlockedCommand) -> str:
    reasons = []
    for issue in blocked.issues:
        template = _FAILURE_NARRATION_TEMPLATES.get(issue.reason)
        if template is None:
            reasons.append(issue.message)
        else:
            reasons.append(template[0])

    if not reasons:
        for reason_code in blocked.reason_codes:
            template = _FAILURE_NARRATION_TEMPLATES.get(reason_code)
            reasons.append(template[0] if template is not None else reason_code.value)

    reason_text = _join_or_default(_dedupe_preserve_order(reasons), blocked.reason)
    detail = blocked.reason.strip()
    if detail and detail not in reason_text:
        return f"{reason_text}. 세부 판정: {detail}"
    return reason_text


def _blocked_alternative_text(blocked: StateNarratorBlockedCommand) -> str:
    for issue in blocked.issues:
        template = _FAILURE_NARRATION_TEMPLATES.get(issue.reason)
        if template is not None:
            alternative = template[1]
            detail = issue.alternative.strip()
            if detail and detail != alternative:
                return f"{alternative} ({detail})"
            return alternative

    for reason_code in blocked.reason_codes:
        template = _FAILURE_NARRATION_TEMPLATES.get(reason_code)
        if template is not None:
            alternative = template[1]
            detail = blocked.alternative.strip()
            if detail and detail != alternative:
                return f"{alternative} ({detail})"
            return alternative

    return blocked.alternative


def _blocked_state_sentence(state: StateNarratorSnapshot) -> str:
    return (
        f"현재 상태: 미네랄 {state.resources.get('minerals', 0)}, "
        f"가스 {state.resources.get('gas', 0)}, "
        f"보급 {state.supply.get('used_supply', 0)}/"
        f"{state.supply.get('supply_capacity', 0)}, "
        f"가용 SCV {state.available_workers}기."
    )


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    deduped = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _priority_and_constraints(
    validation: IntentValidationResult,
) -> tuple[Priority, tuple[str, ...]]:
    payload = validation.payload
    if payload is None:
        return "normal", ()
    return payload.priority, tuple(payload.constraints)


def _response_kind(narrator_input: StateNarratorInput) -> NarratorResponseKind:
    if not narrator_input.executed:
        return "blocked"
    if narrator_input.read_only:
        return "read_only"
    return "executed"


def _blocked_reason(feasibility: NarratorFeasibilityOutcome) -> str:
    if feasibility.reason.strip():
        return feasibility.reason
    if feasibility.issues:
        return feasibility.issues[0].message
    return "Command was blocked before execution."


def _blocked_alternative(feasibility: NarratorFeasibilityOutcome) -> str:
    if feasibility.alternative.strip():
        return feasibility.alternative
    if feasibility.issues:
        return feasibility.issues[0].alternative
    return _DEFAULT_BLOCKED_ACTIONABLE_ALTERNATIVE


def _render_read_only_success(narrator_input: StateNarratorInput) -> str:
    state = narrator_input.after_state
    army_text = _format_positive_counts(
        {
            unit_name: count
            for unit_name, count in state.units.items()
            if unit_name != "SCV"
        },
        empty_text="전투 병력 없음",
    )
    structure_text = _format_positive_counts(state.structures, empty_text="구조물 없음")
    queue_text = _format_positive_counts(
        state.production_queues,
        empty_text="대기열 없음",
    )
    damaged_text = (
        ", ".join(state.damaged_targets) if state.damaged_targets else "손상 대상 없음"
    )

    return (
        "실행 완료: 현재 상황을 보고합니다. "
        f"{_resource_sentence(state)} "
        f"{_supply_sentence(state)} "
        f"SCV는 총 {state.units.get('SCV', 0)}기, 가용 {state.available_workers}기, "
        f"작업 중 {state.busy_workers}기입니다. "
        f"전투 병력은 {army_text}. 구조물은 {structure_text}. "
        f"생산 대기열은 {queue_text}. 수리 필요 대상은 {damaged_text}."
    )


def _render_gather_resource_success(narrator_input: StateNarratorInput) -> str:
    resource_changes = _named_deltas(
        narrator_input.change_summary.resource_deltas,
        ("resources.minerals", "resources.gas"),
    )
    worker_delta = _first_delta(
        narrator_input.change_summary.entity_deltas,
        "busy_workers",
    )

    impact_parts = []
    impact_parts.extend(_format_delta(delta) for delta in resource_changes)
    if worker_delta is not None:
        impact_parts.append(f"작업 중 SCV {_format_signed(worker_delta.delta)}")

    return (
        "실행 완료: 자원 채취 지시를 반영했습니다. "
        f"{_join_or_default(impact_parts, '상태 변화가 기록되었습니다')}. "
        f"{_resource_sentence(narrator_input.after_state)} "
        f"{_worker_sentence(narrator_input.after_state)}"
    )


def _render_build_structure_success(narrator_input: StateNarratorInput) -> str:
    order = _last_mapping_item(narrator_input.after_state.construction_queue)
    structure_name = _mapping_text(order, "structure_name", "구조물")
    location = _mapping_text(order, "location", "지정 위치")
    remaining_seconds = _mapping_int(order, "remaining_seconds")
    cost_text = _resource_delta_phrase(narrator_input.change_summary.resource_deltas)
    worker_delta = _first_delta(narrator_input.change_summary.entity_deltas, "busy_workers")
    worker_text = (
        f"작업 중 SCV {_format_signed(worker_delta.delta)}"
        if worker_delta is not None and worker_delta.delta is not None
        else "건설 작업자를 배정했습니다"
    )

    return (
        "실행 완료: 건설 명령을 시작했습니다. "
        f"{location}에 {structure_name} 건설을 예약했고, {worker_text}. "
        f"{cost_text}. 완료까지 {remaining_seconds}초 남았습니다. "
        f"{_resource_sentence(narrator_input.after_state)}"
    )


def _render_expand_success(narrator_input: StateNarratorInput) -> str:
    order = _last_mapping_item(narrator_input.after_state.construction_queue)
    location = _mapping_text(order, "location", "확장 위치")
    remaining_seconds = _mapping_int(order, "remaining_seconds")

    return (
        "실행 완료: 확장 명령을 시작했습니다. "
        f"{location}에 Command Center 건설을 예약했습니다. "
        f"{_resource_delta_phrase(narrator_input.change_summary.resource_deltas)}. "
        f"완료까지 {remaining_seconds}초 남았습니다. "
        f"{_resource_sentence(narrator_input.after_state)}"
    )


def _render_train_unit_success(narrator_input: StateNarratorInput) -> str:
    queue_delta = _first_prefixed_delta(
        narrator_input.change_summary.entity_deltas,
        "production_queues.",
    )
    producer = (
        queue_delta.name.removeprefix("production_queues.")
        if queue_delta is not None
        else "생산 시설"
    )
    queued_count = queue_delta.delta if queue_delta is not None else None
    unit_name = _queued_unit_name(narrator_input)
    supply_delta = _first_delta(narrator_input.change_summary.resource_deltas, "supply.used_supply")
    supply_text = (
        f"보급 {_format_signed(supply_delta.delta)}"
        if supply_delta is not None and supply_delta.delta is not None
        else "보급을 예약했습니다"
    )

    return (
        "실행 완료: 생산 명령을 예약했습니다. "
        f"{producer}에 {unit_name} {_count_text(queued_count)} 대기열을 추가했고, "
        f"{_resource_delta_phrase(narrator_input.change_summary.resource_deltas)}, {supply_text}. "
        f"{_resource_sentence(narrator_input.after_state)} "
        f"{_supply_sentence(narrator_input.after_state)}"
    )


def _render_combat_success(narrator_input: StateNarratorInput) -> str:
    action_text = "방어 명령" if narrator_input.intent == "DEFEND" else "견제 명령"
    position_delta = _first_prefixed_delta(
        narrator_input.change_summary.map_deltas,
        "unit_positions.",
    )
    damage_delta = _first_prefixed_delta(
        narrator_input.change_summary.map_deltas,
        "target_damage.",
    )
    mitigation_delta = _first_prefixed_delta(
        narrator_input.change_summary.map_deltas,
        "pressure_mitigation.",
    )
    defeated_delta = _first_delta(
        narrator_input.change_summary.map_deltas,
        "defeated_targets",
    )

    parts = []
    if position_delta is not None:
        unit_name = position_delta.name.removeprefix("unit_positions.")
        parts.append(f"{unit_name}을 {position_delta.after}로 이동")
    if damage_delta is not None:
        target = damage_delta.name.removeprefix("target_damage.")
        parts.append(f"{target} 피해 {_format_signed(damage_delta.delta)}")
    if mitigation_delta is not None:
        target = mitigation_delta.name.removeprefix("pressure_mitigation.")
        parts.append(f"{target} 압박 완화 {_format_signed(mitigation_delta.delta)}")
    if defeated_delta is not None:
        parts.append("목표를 무력화")

    return (
        f"실행 완료: {action_text}을 수행했습니다. "
        f"{_join_or_default(parts, '전투 상태를 갱신했습니다')}. "
        f"{_combat_position_sentence(narrator_input.after_state)} "
        f"{_supply_sentence(narrator_input.after_state)}"
    )


def _render_generic_success(narrator_input: StateNarratorInput) -> str:
    change_text = _join_or_default(
        list(narrator_input.state_changes),
        "상태 변화 없음",
    )
    return (
        f"실행 완료: {narrator_input.intent} 명령을 처리했습니다. "
        f"변화: {change_text}. {_resource_sentence(narrator_input.after_state)}"
    )


def _resource_sentence(state: StateNarratorSnapshot) -> str:
    return (
        f"현재 자원은 미네랄 {state.resources.get('minerals', 0)}, "
        f"가스 {state.resources.get('gas', 0)}입니다."
    )


def _supply_sentence(state: StateNarratorSnapshot) -> str:
    return (
        f"보급은 {state.supply.get('used_supply', 0)}/"
        f"{state.supply.get('supply_capacity', 0)}"
        f"(여유 {state.supply.get('available_supply', 0)})입니다."
    )


def _worker_sentence(state: StateNarratorSnapshot) -> str:
    return f"가용 SCV는 {state.available_workers}기, 작업 중 SCV는 {state.busy_workers}기입니다."


def _combat_position_sentence(state: StateNarratorSnapshot) -> str:
    if not state.unit_positions:
        return "전투 위치 기록은 아직 없습니다."
    positions = ", ".join(
        f"{unit_name} {location}" for unit_name, location in state.unit_positions.items()
    )
    return f"현재 전투 위치는 {positions}입니다."


def _resource_delta_phrase(deltas: tuple[StateNarratorDelta, ...]) -> str:
    spend_or_gain = [
        _format_delta(delta)
        for delta in _named_deltas(deltas, ("resources.minerals", "resources.gas"))
    ]
    return _join_or_default(spend_or_gain, "자원 변화 없음")


def _format_delta(delta: StateNarratorDelta) -> str:
    label = {
        "resources.minerals": "미네랄",
        "resources.gas": "가스",
        "supply.used_supply": "사용 보급",
        "supply.available_supply": "여유 보급",
        "busy_workers": "작업 중 SCV",
        "available_workers": "가용 SCV",
    }.get(delta.name, delta.name)
    return f"{label} {_format_signed(delta.delta)}"


def _format_signed(value: int | None) -> str:
    if value is None:
        return "변경"
    if value > 0:
        return f"+{value}"
    return str(value)


def _count_text(value: int | None) -> str:
    if value is None:
        return "1기"
    return f"{value}기"


def _queued_unit_name(narrator_input: StateNarratorInput) -> str:
    if narrator_input.intent == "TRAIN_WORKER":
        return "SCV"
    for order in reversed(narrator_input.after_state.production_orders):
        unit_name = order.get("unit_name")
        if isinstance(unit_name, str) and unit_name.strip():
            return unit_name
    return "전투 유닛"


def _first_delta(
    deltas: tuple[StateNarratorDelta, ...],
    name: str,
) -> StateNarratorDelta | None:
    for delta in deltas:
        if delta.name == name:
            return delta
    return None


def _first_prefixed_delta(
    deltas: tuple[StateNarratorDelta, ...],
    prefix: str,
) -> StateNarratorDelta | None:
    for delta in deltas:
        if delta.name.startswith(prefix):
            return delta
    return None


def _named_deltas(
    deltas: tuple[StateNarratorDelta, ...],
    names: tuple[str, ...],
) -> tuple[StateNarratorDelta, ...]:
    return tuple(delta for name in names for delta in deltas if delta.name == name)


def _last_mapping_item(items: tuple[Mapping[str, object], ...]) -> Mapping[str, object]:
    if not items:
        return {}
    return items[-1]


def _mapping_text(mapping: Mapping[str, object], key: str, fallback: str) -> str:
    value = mapping.get(key)
    if isinstance(value, str) and value.strip():
        return value
    return fallback


def _mapping_int(mapping: Mapping[str, object], key: str) -> int:
    value = mapping.get(key)
    if type(value) is int:
        return value
    return 0


def _format_positive_counts(counts: Mapping[str, object], *, empty_text: str) -> str:
    parts = [
        f"{name} {count}기"
        for name, count in counts.items()
        if isinstance(count, int) and count > 0
    ]
    if not parts:
        return empty_text
    return ", ".join(parts)


def _join_or_default(parts: list[str], default: str) -> str:
    cleaned = [part for part in parts if part.strip()]
    if not cleaned:
        return default
    return ", ".join(cleaned)


def _scalar_delta(
    name: str,
    before: object,
    after: object,
) -> tuple[StateNarratorDelta, ...]:
    if before == after:
        return ()
    return (
        StateNarratorDelta(
            name=name,
            before=before,
            after=after,
            delta=_numeric_delta(before, after),
        ),
    )


def _mapping_deltas(
    group: str,
    before: Mapping[str, object],
    after: Mapping[str, object],
) -> tuple[StateNarratorDelta, ...]:
    deltas = []
    for key in sorted(set(before) | set(after)):
        before_value = before.get(key, 0)
        after_value = after.get(key, 0)
        if before_value == after_value:
            continue
        deltas.append(
            StateNarratorDelta(
                name=f"{group}.{key}",
                before=before_value,
                after=after_value,
                delta=_numeric_delta(before_value, after_value),
            )
        )
    return tuple(deltas)


def _numeric_delta(before: object, after: object) -> int | None:
    if type(before) is int and type(after) is int:
        return after - before
    return None


def _json_ready_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_ready_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_ready_value(item) for item in value]
    if isinstance(value, list):
        return [_json_ready_value(item) for item in value]
    return value
