"""ToyCraft rule-engine execution and commander response handling."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Final

from toycraft_commander.failure import (
    CommandFailureReport,
    CommandFailureStage,
    DEFAULT_ACTIONABLE_ALTERNATIVE,
    build_validation_failure_report,
)
from toycraft_commander.feasibility import (
    ConstructionOrder,
    ProductionOrder,
    ToyCraftState,
    validate_intent_feasibility,
)
from toycraft_commander.intents import (
    BuildStructureIntent,
    DefendIntent,
    ExpandIntent,
    FeasibilityErrorReason,
    FeasibilityIssue,
    GatherResourceIntent,
    HarassIntent,
    IntentName,
    IntentPayload,
    IntentValidationResult,
    SummarizeStateIntent,
    TrainArmyIntent,
    TrainWorkerIntent,
)
from toycraft_commander.map import get_targetable_position
from toycraft_commander.resources import get_available_supply
from toycraft_commander.structures import get_structure_model
from toycraft_commander.units import UNIT_NAMES, get_unit_model, resolve_unit_name


ToyCraftExecutionRule = Callable[[IntentPayload, ToyCraftState], "ToyCraftExecutionResult"]
StateSummary = dict[str, object]
RESOURCE_GATHER_YIELD_PER_WORKER: Final[dict[str, int]] = {
    "minerals": 8,
    "gas": 4,
}
PHASE_ZERO_SPECIAL_STRUCTURE_COSTS: Final[dict[str, dict[str, int]]] = {
    "Bunker": {"minerals": 100, "gas": 0},
    "Command Center": {"minerals": 400, "gas": 0},
}
PHASE_ZERO_SPECIAL_STRUCTURE_SUPPLY: Final[dict[str, int]] = {
    "Bunker": 0,
    "Command Center": 0,
}
PHASE_ZERO_SPECIAL_STRUCTURE_BUILD_TIMES: Final[dict[str, int]] = {
    "Bunker": 30,
    "Command Center": 100,
}
COMBAT_TARGET_HIT_POINTS: Final[dict[str, int]] = {
    "main ramp": 160,
    "natural choke": 160,
    "enemy front": 160,
    "enemy natural": 120,
    "enemy mineral line": 80,
    "enemy main": 200,
}
COMBAT_TARGET_ARMOR: Final[dict[str, int]] = {
    "main ramp": 1,
    "natural choke": 1,
    "enemy front": 1,
    "enemy natural": 1,
    "enemy mineral line": 0,
    "enemy main": 1,
}
COMBAT_TARGET_COUNTER_DAMAGE: Final[dict[str, int]] = {
    "main ramp": 45,
    "natural choke": 45,
    "enemy front": 24,
    "enemy natural": 24,
    "enemy mineral line": 12,
    "enemy main": 45,
}


@dataclass(frozen=True)
class ToyCraftExecutedAction:
    """One structured action performed by the ToyCraft rule engine."""

    action_type: str
    target: str
    amount: object | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.action_type.strip():
            raise ValueError("executed action type must be non-empty.")
        if not self.target.strip():
            raise ValueError("executed action target must be non-empty.")
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready action record for logs and UI adapters."""

        payload = {
            "action_type": self.action_type,
            "target": self.target,
            "metadata": _json_ready_value(self.metadata),
        }
        if self.amount is not None:
            payload["amount"] = _json_ready_value(self.amount)
        return payload


@dataclass(frozen=True)
class ToyCraftStateDelta:
    """One typed before/after state delta produced by successful execution."""

    path: str
    before: object
    after: object
    delta: int | None = None

    def __post_init__(self) -> None:
        if not self.path.strip():
            raise ValueError("state delta path must be non-empty.")

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready state delta."""

        payload = {
            "path": self.path,
            "before": _json_ready_value(self.before),
            "after": _json_ready_value(self.after),
        }
        if self.delta is not None:
            payload["delta"] = self.delta
        return payload


@dataclass(frozen=True)
class ToyCraftStateDeltaSet:
    """Structured state transition summary for an execution result."""

    changes: tuple[ToyCraftStateDelta, ...] = ()
    raw_changes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "changes", tuple(self.changes))
        object.__setattr__(self, "raw_changes", tuple(self.raw_changes))

    @property
    def has_changes(self) -> bool:
        """Return whether the before/after state contains any detected changes."""

        return bool(self.changes)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready state transition summary."""

        return {
            "changes": [change.to_dict() for change in self.changes],
            "raw_changes": list(self.raw_changes),
            "has_changes": self.has_changes,
        }


@dataclass(frozen=True)
class ToyCraftExecutionResult:
    """Typed response from the ToyCraft executor boundary."""

    intent: str
    validation: IntentValidationResult
    before_state: ToyCraftState
    after_state: ToyCraftState
    executed: bool
    narration: str
    state_changes: tuple[str, ...] = ()
    executed_actions: tuple[ToyCraftExecutedAction, ...] = ()
    state_delta: ToyCraftStateDeltaSet | None = None
    read_only: bool = False
    summary: Mapping[str, object] = field(default_factory=dict)
    failure: CommandFailureReport | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "state_changes", tuple(self.state_changes))
        object.__setattr__(self, "executed_actions", tuple(self.executed_actions))
        state_delta = self.state_delta
        if state_delta is None:
            state_delta = build_toycraft_state_delta(
                self.before_state,
                self.after_state,
                self.state_changes,
            )
        if not isinstance(state_delta, ToyCraftStateDeltaSet):
            raise TypeError("state_delta must be a ToyCraftStateDeltaSet.")
        object.__setattr__(self, "state_delta", state_delta)
        object.__setattr__(self, "summary", dict(self.summary))
        if self.executed and not self.validation.executable:
            raise ValueError("executed results require executable validation.")
        if self.executed and self.failure is not None:
            raise ValueError("executed results cannot include failure reports.")
        if not self.executed and self.failure is None:
            raise ValueError("rejected execution results require failure reports.")
        if self.failure is not None and self.failure.state_mutated:
            raise ValueError("failure reports cannot mark game state as mutated.")
        if self.read_only and self.before_state != self.after_state:
            raise ValueError("read-only execution results cannot change state.")
        if not self.executed and self.before_state != self.after_state:
            raise ValueError("rejected execution results cannot change state.")
        if not self.executed and self.executed_actions:
            raise ValueError("rejected execution results cannot include executed actions.")
        if self.executed and not self.read_only and self.before_state != self.after_state:
            if not self.executed_actions:
                raise ValueError("state-changing execution results require executed actions.")
            if not state_delta.has_changes:
                raise ValueError("state-changing execution results require state deltas.")
        if not self.narration.strip():
            raise ValueError("narration must be a non-empty commander response.")


@dataclass(frozen=True)
class _ProductionProgressResult:
    state: ToyCraftState
    completed_units: Mapping[str, int]
    state_changes: tuple[str, ...]


@dataclass(frozen=True)
class _ConstructionProgressResult:
    state: ToyCraftState
    completed_structures: Mapping[str, int]
    state_changes: tuple[str, ...]


@dataclass(frozen=True)
class _ResolvedCombatGroup:
    unit_name: str
    count: int


def execute_toycraft_intent(
    payload: IntentPayload | Mapping[str, object],
    state: ToyCraftState,
) -> ToyCraftExecutionResult:
    """Validate and execute a ToyCraft intent through the Phase 0 boundary."""

    validation = validate_intent_feasibility(payload, state)
    if not validation.executable:
        return _rejected_execution_result(validation, state, payload)
    if validation.payload is None:
        raise RuntimeError("executable validation returned no payload.")

    rule = TOYCRAFT_EXECUTION_RULES.get(validation.payload.intent)
    if rule is None:
        return _unimplemented_execution_result(validation.payload.intent, validation, state)
    return rule(validation.payload, state)


def execute_summarize_state(
    payload: IntentPayload,
    state: ToyCraftState,
) -> ToyCraftExecutionResult:
    """Execute SUMMARIZE_STATE as a read-only state summary response."""

    if not isinstance(payload, SummarizeStateIntent):
        raise TypeError("execute_summarize_state requires SummarizeStateIntent.")

    validation = IntentValidationResult(executable=True, payload=payload)
    summary = summarize_toycraft_state(state)
    narration = narrate_state_summary(summary)
    return ToyCraftExecutionResult(
        intent=payload.intent,
        validation=validation,
        before_state=state,
        after_state=state,
        executed=True,
        read_only=True,
        state_changes=(),
        summary=summary,
        narration=narration,
    )


def execute_gather_resource(
    payload: IntentPayload,
    state: ToyCraftState,
) -> ToyCraftExecutionResult:
    """Execute GATHER_RESOURCE with a deterministic Phase 0 economy tick."""

    if not isinstance(payload, GatherResourceIntent):
        raise TypeError("execute_gather_resource requires GatherResourceIntent.")

    validation = IntentValidationResult(executable=True, payload=payload)
    gathered_amount = RESOURCE_GATHER_YIELD_PER_WORKER[payload.resource] * payload.worker_count
    after_resources = replace(
        state.resources,
        **{payload.resource: getattr(state.resources, payload.resource) + gathered_amount},
    )
    after_state = replace(
        state,
        resources=after_resources,
        busy_workers=state.busy_workers + payload.worker_count,
    )
    resource_label = "미네랄" if payload.resource == "minerals" else "가스"

    return ToyCraftExecutionResult(
        intent=payload.intent,
        validation=validation,
        before_state=state,
        after_state=after_state,
        executed=True,
        read_only=False,
        state_changes=(
            f"{payload.resource} +{gathered_amount}",
            f"busy_workers +{payload.worker_count}",
        ),
        executed_actions=(
            ToyCraftExecutedAction(
                action_type="assign_workers",
                target=f"{payload.base}.{payload.resource}",
                amount=payload.worker_count,
                metadata={"unit": "SCV"},
            ),
            ToyCraftExecutedAction(
                action_type="gather_resource",
                target=payload.resource,
                amount=gathered_amount,
                metadata={"worker_count": payload.worker_count, "base": payload.base},
            ),
        ),
        narration=(
            f"SCV {payload.worker_count}기를 {payload.base}에서 {resource_label} 채취에 배치했습니다. "
            f"즉시 {resource_label} {gathered_amount}을 확보했고, 현재 자원은 "
            f"미네랄 {after_resources.minerals}, 가스 {after_resources.gas}입니다."
        ),
    )


def execute_build_structure(
    payload: IntentPayload,
    state: ToyCraftState,
) -> ToyCraftExecutionResult:
    """Execute BUILD_STRUCTURE by spending resources and queueing construction."""

    if not isinstance(payload, BuildStructureIntent):
        raise TypeError("execute_build_structure requires BuildStructureIntent.")

    validation = IntentValidationResult(executable=True, payload=payload)
    structure_name = payload.structure
    cost = _get_structure_cost(structure_name)
    build_time_seconds = _get_structure_build_time(structure_name)
    after_state = _apply_construction_start(
        state,
        structure_name=structure_name,
        location=payload.location,
        cost=cost,
        build_time_seconds=build_time_seconds,
    )

    state_changes = [
        *_resource_spend_changes(cost),
        "busy_workers +1",
        f"construction_queue.{structure_name} +1",
        f"construction_time_seconds +{build_time_seconds}",
    ]

    return ToyCraftExecutionResult(
        intent=payload.intent,
        validation=validation,
        before_state=state,
        after_state=after_state,
        executed=True,
        read_only=False,
        state_changes=tuple(state_changes),
        executed_actions=(
            ToyCraftExecutedAction(
                action_type="spend_resources",
                target=structure_name,
                amount=cost,
            ),
            ToyCraftExecutedAction(
                action_type="assign_builder",
                target=payload.location,
                amount=1,
                metadata={"unit": "SCV", "structure": structure_name},
            ),
            ToyCraftExecutedAction(
                action_type="queue_construction",
                target=structure_name,
                amount=1,
                metadata={
                    "location": payload.location,
                    "build_time_seconds": build_time_seconds,
                },
            ),
        ),
        narration=(
            f"{payload.location}에 {structure_name} 건설을 시작했습니다. "
            f"SCV 1기를 건설자로 배정하고 미네랄 {cost['minerals']}, 가스 {cost['gas']}를 사용했습니다. "
            f"완료까지 {build_time_seconds}초 남았고, "
            f"현재 자원은 미네랄 {after_state.resources.minerals}, 가스 {after_state.resources.gas}입니다."
        ),
    )


def execute_train_worker(
    payload: IntentPayload,
    state: ToyCraftState,
) -> ToyCraftExecutionResult:
    """Execute TRAIN_WORKER by spending resources and queueing SCVs."""

    if not isinstance(payload, TrainWorkerIntent):
        raise TypeError("execute_train_worker requires TrainWorkerIntent.")

    return _execute_train_unit(
        payload=payload,
        state=state,
        unit_type="SCV",
        count=payload.count,
        narration_unit_label="SCV",
    )


def execute_train_army(
    payload: IntentPayload,
    state: ToyCraftState,
) -> ToyCraftExecutionResult:
    """Execute TRAIN_ARMY by spending resources and queueing combat units."""

    if not isinstance(payload, TrainArmyIntent):
        raise TypeError("execute_train_army requires TrainArmyIntent.")

    return _execute_train_unit(
        payload=payload,
        state=state,
        unit_type=payload.unit_type,
        count=payload.count,
        narration_unit_label=payload.unit_type,
    )


def execute_defend(
    payload: IntentPayload,
    state: ToyCraftState,
) -> ToyCraftExecutionResult:
    """Execute DEFEND by moving combat units and damaging incoming pressure."""

    if not isinstance(payload, DefendIntent):
        raise TypeError("execute_defend requires DefendIntent.")

    return _execute_combat_resolution(
        payload=payload,
        state=state,
        target_name=payload.location,
        unit_group=payload.unit_group,
        action_label="방어 교전",
        record_pressure_mitigation=True,
        narration=(
            "{unit_count}기의 {unit_name}을 {target_name} 방어 위치로 이동시켰습니다. "
            "적 압박에 {damage} 피해를 적용하고 위협 {mitigated}을 완화해 "
            "누적 피해는 {total_damage}/{target_hp}, 누적 완화는 {total_mitigation}입니다. "
            "{loss_summary} {outcome}"
        ),
    )


def execute_harass(
    payload: IntentPayload,
    state: ToyCraftState,
) -> ToyCraftExecutionResult:
    """Execute HARASS by applying deterministic damage to an enemy target."""

    if not isinstance(payload, HarassIntent):
        raise TypeError("execute_harass requires HarassIntent.")

    return _execute_combat_resolution(
        payload=payload,
        state=state,
        target_name=payload.target,
        unit_group=payload.unit_group,
        action_label="견제 공격",
        narration=(
            "{unit_count}기의 {unit_name}으로 {target_name}에 견제를 걸었습니다. "
            "{damage} 피해를 적용해 목표 누적 피해는 {total_damage}/{target_hp}입니다. "
            "{loss_summary} {outcome}"
        ),
    )


def execute_expand(
    payload: IntentPayload,
    state: ToyCraftState,
) -> ToyCraftExecutionResult:
    """Execute EXPAND by spending for a new Command Center construction order."""

    if not isinstance(payload, ExpandIntent):
        raise TypeError("execute_expand requires ExpandIntent.")

    validation = IntentValidationResult(executable=True, payload=payload)
    cost = _get_structure_cost("Command Center")
    build_time_seconds = _get_structure_build_time("Command Center")
    after_state = _apply_construction_start(
        state,
        structure_name="Command Center",
        location=payload.location,
        cost=cost,
        build_time_seconds=build_time_seconds,
    )

    return ToyCraftExecutionResult(
        intent=payload.intent,
        validation=validation,
        before_state=state,
        after_state=after_state,
        executed=True,
        read_only=False,
        state_changes=(
            *_resource_spend_changes(cost),
            "busy_workers +1",
            "construction_queue.Command Center +1",
            f"construction_time_seconds +{build_time_seconds}",
        ),
        executed_actions=(
            ToyCraftExecutedAction(
                action_type="spend_resources",
                target="Command Center",
                amount=cost,
            ),
            ToyCraftExecutedAction(
                action_type="assign_builder",
                target=payload.location,
                amount=1,
                metadata={"unit": "SCV", "structure": "Command Center"},
            ),
            ToyCraftExecutedAction(
                action_type="queue_construction",
                target="Command Center",
                amount=1,
                metadata={
                    "location": payload.location,
                    "build_time_seconds": build_time_seconds,
                },
            ),
        ),
        narration=(
            f"{payload.location}에 확장을 시작했습니다. Command Center 비용으로 "
            f"SCV 1기를 건설자로 배정하고 미네랄 {cost['minerals']}, 가스 {cost['gas']}를 사용했습니다. "
            f"완료까지 {build_time_seconds}초 남았고, "
            f"현재 자원은 미네랄 {after_state.resources.minerals}, 가스 {after_state.resources.gas}입니다."
        ),
    )


def summarize_toycraft_state(state: ToyCraftState) -> StateSummary:
    """Return a structured commander snapshot of the current ToyCraft state."""

    if not isinstance(state, ToyCraftState):
        raise TypeError("state must be a ToyCraftState.")

    return {
        "resources": state.resources.to_dict(),
        "supply": {
            **state.supply.to_dict(),
            "available_supply": get_available_supply(state.supply),
        },
        "units": dict(state.units),
        "structures": dict(state.structures),
        "busy_workers": state.busy_workers,
        "available_workers": state.available_worker_count(),
        "busy_producers": dict(state.busy_producers),
        "production_queues": dict(state.production_queues),
        "production_orders": tuple(order.to_dict() for order in state.production_orders),
        "construction_queue": tuple(order.to_dict() for order in state.construction_queue),
        "claimed_locations": list(state.claimed_locations),
        "damaged_targets": list(state.damaged_targets),
        "unit_positions": dict(state.unit_positions),
        "target_damage": dict(state.target_damage),
        "pressure_mitigation": dict(state.pressure_mitigation),
        "defeated_targets": list(state.defeated_targets),
    }


def narrate_state_summary(summary: Mapping[str, object]) -> str:
    """Return a concise Korean commander-facing narration for a state summary."""

    resources = _expect_mapping(summary, "resources")
    supply = _expect_mapping(summary, "supply")
    units = _expect_mapping(summary, "units")
    structures = _expect_mapping(summary, "structures")

    minerals = _expect_int(resources, "minerals")
    gas = _expect_int(resources, "gas")
    used_supply = _expect_int(supply, "used_supply")
    supply_capacity = _expect_int(supply, "supply_capacity")
    available_supply = _expect_int(supply, "available_supply")
    available_workers = _expect_int(summary, "available_workers")
    busy_workers = _expect_int(summary, "busy_workers")
    damaged_targets = tuple(str(target) for target in summary.get("damaged_targets", ()))
    production_queues = _expect_mapping(summary, "production_queues")
    construction_queue = tuple(summary.get("construction_queue", ()))

    army_text = _format_counts(
        {
            unit_name: count
            for unit_name, count in units.items()
            if unit_name != "SCV" and isinstance(count, int) and count > 0
        },
        empty_text="전투 병력 없음",
    )
    worker_count = units.get("SCV", 0)
    structure_text = _format_counts(structures, empty_text="구조물 없음")
    queue_text = _format_counts(production_queues, empty_text="대기열 없음")
    construction_text = _format_construction_queue(construction_queue)
    damage_text = ", ".join(damaged_targets) if damaged_targets else "손상 대상 없음"

    return (
        "현재 상황입니다. "
        f"자원은 미네랄 {minerals}, 가스 {gas}. "
        f"보급은 {used_supply}/{supply_capacity}로 여유 {available_supply}입니다. "
        f"일꾼은 SCV {worker_count}기 중 가용 {available_workers}기, 작업 중 {busy_workers}기입니다. "
        f"전투 병력은 {army_text}. "
        f"구조물은 {structure_text}. "
        f"생산 대기열은 {queue_text}. "
        f"건설 중인 구조물은 {construction_text}. "
        f"수리 필요 대상은 {damage_text}."
    )


def build_commander_response(
    result: ToyCraftExecutionResult,
    *,
    command_text: str = "",
) -> str:
    """Return the final narration string that should be shown to the commander."""

    if result.executed:
        from toycraft_commander.narrator import build_execution_narrator_response

        return build_execution_narrator_response(
            result,
            command_text=command_text,
        ).response_text
    return result.narration


def build_toycraft_state_delta(
    before_state: ToyCraftState,
    after_state: ToyCraftState,
    raw_changes: tuple[str, ...] = (),
) -> ToyCraftStateDeltaSet:
    """Build structured before/after state deltas for executor outcomes."""

    if not isinstance(before_state, ToyCraftState):
        raise TypeError("before_state must be a ToyCraftState.")
    if not isinstance(after_state, ToyCraftState):
        raise TypeError("after_state must be a ToyCraftState.")

    before = summarize_toycraft_state(before_state)
    after = summarize_toycraft_state(after_state)
    changes = (
        *_mapping_state_deltas("resources", before["resources"], after["resources"]),
        *_mapping_state_deltas("supply", before["supply"], after["supply"]),
        *_mapping_state_deltas("units", before["units"], after["units"]),
        *_mapping_state_deltas("structures", before["structures"], after["structures"]),
        *_scalar_state_delta(
            "busy_workers",
            before["busy_workers"],
            after["busy_workers"],
        ),
        *_scalar_state_delta(
            "available_workers",
            before["available_workers"],
            after["available_workers"],
        ),
        *_mapping_state_deltas(
            "busy_producers",
            before["busy_producers"],
            after["busy_producers"],
        ),
        *_mapping_state_deltas(
            "production_queues",
            before["production_queues"],
            after["production_queues"],
        ),
        *_scalar_state_delta(
            "production_orders",
            before["production_orders"],
            after["production_orders"],
        ),
        *_scalar_state_delta(
            "construction_queue",
            before["construction_queue"],
            after["construction_queue"],
        ),
        *_scalar_state_delta(
            "claimed_locations",
            before["claimed_locations"],
            after["claimed_locations"],
        ),
        *_scalar_state_delta(
            "damaged_targets",
            before["damaged_targets"],
            after["damaged_targets"],
        ),
        *_mapping_state_deltas(
            "unit_positions",
            before["unit_positions"],
            after["unit_positions"],
        ),
        *_mapping_state_deltas(
            "target_damage",
            before["target_damage"],
            after["target_damage"],
        ),
        *_mapping_state_deltas(
            "pressure_mitigation",
            before["pressure_mitigation"],
            after["pressure_mitigation"],
        ),
        *_scalar_state_delta(
            "defeated_targets",
            before["defeated_targets"],
            after["defeated_targets"],
        ),
    )
    return ToyCraftStateDeltaSet(changes=changes, raw_changes=raw_changes)


def advance_toycraft_time(
    state: ToyCraftState,
    seconds: int,
) -> ToyCraftExecutionResult:
    """Advance timers and materialize any finished production or construction."""

    if not isinstance(state, ToyCraftState):
        raise TypeError("state must be a ToyCraftState.")
    if type(seconds) is not int or seconds < 1:
        raise ValueError("seconds must be a positive integer.")

    validation = IntentValidationResult(executable=True)
    production_result = _advance_production_orders(state, seconds)
    construction_result = _advance_construction_orders(
        production_result.state,
        seconds,
    )

    after_state = construction_result.state
    state_changes = (
        *production_result.state_changes,
        *construction_result.state_changes,
    )
    narration = _narrate_time_advance(
        seconds=seconds,
        completed_units=production_result.completed_units,
        completed_structures=construction_result.completed_structures,
        remaining_production_orders=len(after_state.production_orders),
        remaining_construction_orders=len(after_state.construction_queue),
    )

    return ToyCraftExecutionResult(
        intent="PROGRESS_TIME",
        validation=validation,
        before_state=state,
        after_state=after_state,
        executed=True,
        read_only=False,
        state_changes=state_changes,
        executed_actions=_time_advance_actions(
            seconds=seconds,
            completed_units=production_result.completed_units,
            completed_structures=construction_result.completed_structures,
        ),
        narration=narration,
    )


def _rejected_execution_result(
    validation: IntentValidationResult,
    state: ToyCraftState,
    raw_payload: object | None = None,
    *,
    failure_stage: CommandFailureStage = CommandFailureStage.VALIDATION,
) -> ToyCraftExecutionResult:
    intent = _execution_intent_label(validation, raw_payload)
    reason = validation.reason or "Command is not executable."
    alternative = validation.alternative or DEFAULT_ACTIONABLE_ALTERNATIVE
    failure = build_validation_failure_report(
        validation,
        intent=intent,
        stage=failure_stage,
    )
    return ToyCraftExecutionResult(
        intent=intent,
        validation=validation,
        before_state=state,
        after_state=state,
        executed=False,
        read_only=True,
        failure=failure,
        narration=f"실행하지 않았습니다. 이유: {reason} 대안: {alternative}",
    )


def _execution_intent_label(
    validation: IntentValidationResult,
    raw_payload: object | None,
) -> str:
    if validation.payload is not None:
        return validation.payload.intent
    if isinstance(raw_payload, Mapping):
        raw_intent = raw_payload.get("intent")
        if isinstance(raw_intent, str) and raw_intent.strip():
            return raw_intent
    return "UNKNOWN"


def _unimplemented_execution_result(
    intent: IntentName,
    validation: IntentValidationResult,
    state: ToyCraftState,
) -> ToyCraftExecutionResult:
    issue = FeasibilityIssue(
        reason=FeasibilityErrorReason.UNSUPPORTED_PHASE_ZERO_SCOPE,
        message=f"{intent} execution is not implemented in this Phase 0 slice.",
        alternative="Use GATHER_RESOURCE or SUMMARIZE_STATE for the current implemented executor responses.",
    )
    rejected_validation = IntentValidationResult(
        executable=False,
        payload=validation.payload,
        reason=issue.message,
        alternative=issue.alternative,
        issues=(issue,),
    )
    return _rejected_execution_result(
        rejected_validation,
        state,
        failure_stage=CommandFailureStage.RULE_EXECUTION,
    )


def _format_counts(counts: Mapping[object, object], empty_text: str) -> str:
    positive_counts = [
        f"{name} {count}"
        for name, count in counts.items()
        if isinstance(count, int) and count > 0
    ]
    if not positive_counts:
        return empty_text
    return ", ".join(positive_counts)


def _expect_mapping(mapping: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = mapping.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be a mapping in state summary.")
    return value


def _expect_int(mapping: Mapping[str, object], key: str) -> int:
    value = mapping.get(key)
    if type(value) is not int:
        raise ValueError(f"{key} must be an integer in state summary.")
    return value


def _mapping_state_deltas(
    prefix: str,
    before: object,
    after: object,
) -> tuple[ToyCraftStateDelta, ...]:
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        return _scalar_state_delta(prefix, before, after)

    deltas: list[ToyCraftStateDelta] = []
    keys = sorted({*before.keys(), *after.keys()}, key=str)
    for key in keys:
        before_value = before.get(key, 0)
        after_value = after.get(key, 0)
        if before_value == after_value:
            continue
        numeric_delta = _numeric_delta(before_value, after_value)
        deltas.append(
            ToyCraftStateDelta(
                path=f"{prefix}.{key}",
                before=before_value,
                after=after_value,
                delta=numeric_delta,
            )
        )
    return tuple(deltas)


def _scalar_state_delta(
    path: str,
    before: object,
    after: object,
) -> tuple[ToyCraftStateDelta, ...]:
    if before == after:
        return ()
    return (
        ToyCraftStateDelta(
            path=path,
            before=before,
            after=after,
            delta=_numeric_delta(before, after),
        ),
    )


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
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _json_ready_value(value.to_dict())
    return value


def _time_advance_actions(
    *,
    seconds: int,
    completed_units: Mapping[str, int],
    completed_structures: Mapping[str, int],
) -> tuple[ToyCraftExecutedAction, ...]:
    actions = [
        ToyCraftExecutedAction(
            action_type="advance_time",
            target="ToyCraft",
            amount=seconds,
        )
    ]
    actions.extend(
        ToyCraftExecutedAction(
            action_type="complete_production",
            target=unit_name,
            amount=count,
        )
        for unit_name, count in completed_units.items()
    )
    actions.extend(
        ToyCraftExecutedAction(
            action_type="complete_construction",
            target=structure_name,
            amount=count,
        )
        for structure_name, count in completed_structures.items()
    )
    return tuple(actions)


def _combat_executed_actions(
    *,
    action_label: str,
    unit_name: str,
    unit_count: int,
    target_name: str,
    applied_damage: int,
    applied_mitigation: int,
    unit_losses: int,
    newly_defeated: bool,
    record_pressure_mitigation: bool,
) -> tuple[ToyCraftExecutedAction, ...]:
    actions = [
        ToyCraftExecutedAction(
            action_type="move_units",
            target=target_name,
            amount=unit_count,
            metadata={"unit": unit_name, "combat_action": action_label},
        ),
        ToyCraftExecutedAction(
            action_type="apply_damage",
            target=target_name,
            amount=applied_damage,
            metadata={"unit": unit_name, "unit_count": unit_count},
        ),
    ]
    if record_pressure_mitigation:
        actions.append(
            ToyCraftExecutedAction(
                action_type="mitigate_pressure",
                target=target_name,
                amount=applied_mitigation,
                metadata={"unit": unit_name, "unit_count": unit_count},
            )
        )
    if unit_losses > 0:
        actions.append(
            ToyCraftExecutedAction(
                action_type="record_unit_losses",
                target=unit_name,
                amount=unit_losses,
            )
        )
    if newly_defeated:
        actions.append(
            ToyCraftExecutedAction(
                action_type="mark_target_defeated",
                target=target_name,
                amount=1,
            )
        )
    return tuple(actions)


def _execute_train_unit(
    *,
    payload: IntentPayload,
    state: ToyCraftState,
    unit_type: str,
    count: int,
    narration_unit_label: str,
) -> ToyCraftExecutionResult:
    validation = IntentValidationResult(executable=True, payload=payload)
    unit_model = get_unit_model(unit_type)
    cost = {
        "minerals": unit_model.cost.minerals * count,
        "gas": unit_model.cost.gas * count,
    }
    required_supply = unit_model.cost.supply * count
    after_state = _apply_training_queue(
        state,
        producer=unit_model.producer,
        unit_type=unit_type,
        build_time_seconds=unit_model.cost.build_time_seconds,
        cost=cost,
        required_supply=required_supply,
        count=count,
    )

    return ToyCraftExecutionResult(
        intent=payload.intent,
        validation=validation,
        before_state=state,
        after_state=after_state,
        executed=True,
        read_only=False,
        state_changes=(
            *_resource_spend_changes(cost),
            f"used_supply +{required_supply}",
            f"production_queues.{unit_model.producer} +{count}",
        ),
        executed_actions=(
            ToyCraftExecutedAction(
                action_type="spend_resources",
                target=unit_type,
                amount=cost,
            ),
            ToyCraftExecutedAction(
                action_type="reserve_supply",
                target=unit_type,
                amount=required_supply,
            ),
            ToyCraftExecutedAction(
                action_type="queue_production",
                target=unit_type,
                amount=count,
                metadata={
                    "producer": unit_model.producer,
                    "build_time_seconds": unit_model.cost.build_time_seconds,
                },
            ),
        ),
        narration=(
            f"{unit_model.producer}에 {narration_unit_label} {count}기 생산을 예약했습니다. "
            f"미네랄 {cost['minerals']}, 가스 {cost['gas']}, 보급 {required_supply}을 사용했고, "
            f"현재 자원은 미네랄 {after_state.resources.minerals}, 가스 {after_state.resources.gas}, "
            f"보급 {after_state.supply.used_supply}/{after_state.supply.supply_capacity}입니다."
        ),
    )


def _execute_combat_resolution(
    *,
    payload: IntentPayload,
    state: ToyCraftState,
    target_name: str,
    unit_group: str,
    action_label: str,
    narration: str,
    record_pressure_mitigation: bool = False,
) -> ToyCraftExecutionResult:
    validation = IntentValidationResult(executable=True, payload=payload)
    group = _resolve_combat_group(unit_group, state)
    target = get_targetable_position(target_name)
    unit_model = get_unit_model(group.unit_name)
    target_hp = COMBAT_TARGET_HIT_POINTS.get(target.name, 100)
    target_armor = COMBAT_TARGET_ARMOR.get(target.name, 0)
    counter_damage = COMBAT_TARGET_COUNTER_DAMAGE.get(target.name, 0)
    damage_per_unit = max(1, unit_model.stats.ground_damage - target_armor)
    raw_damage = damage_per_unit * group.count
    previous_damage = state.target_damage.get(target.name, 0)
    remaining_hp = max(0, target_hp - previous_damage)
    applied_damage = min(raw_damage, remaining_hp)
    total_damage = previous_damage + applied_damage
    after_target_damage = dict(state.target_damage)
    after_target_damage[target.name] = total_damage
    previous_mitigation = state.pressure_mitigation.get(target.name, 0)
    applied_mitigation = applied_damage if record_pressure_mitigation else 0
    total_mitigation = previous_mitigation + applied_mitigation
    after_pressure_mitigation = dict(state.pressure_mitigation)
    if record_pressure_mitigation and total_mitigation > 0:
        after_pressure_mitigation[target.name] = total_mitigation
    neutralized = total_damage >= target_hp
    after_defeated_targets = state.defeated_targets
    newly_defeated = neutralized and target.name not in state.defeated_targets
    if newly_defeated:
        after_defeated_targets = (*state.defeated_targets, target.name)
    unit_losses = _calculate_combat_unit_losses(
        group_count=group.count,
        unit_hit_points=unit_model.stats.effective_hit_points,
        counter_damage=counter_damage,
        neutralized=neutralized,
    )
    after_units = _apply_unit_losses(state.units, group.unit_name, unit_losses)
    after_unit_positions = dict(state.unit_positions)
    if after_units.get(group.unit_name, 0) > 0:
        after_unit_positions[group.unit_name] = target.name
    else:
        after_unit_positions.pop(group.unit_name, None)
    after_supply = replace(
        state.supply,
        used_supply=max(0, state.supply.used_supply - unit_model.cost.supply * unit_losses),
    )
    outcome = (
        "목표 압박을 무력화했습니다."
        if neutralized
        else f"남은 목표 내구도는 {target_hp - total_damage}입니다."
    )
    loss_summary = _format_combat_loss_summary(group.unit_name, unit_losses)
    after_state = replace(
        state,
        supply=after_supply,
        units=after_units,
        unit_positions=after_unit_positions,
        target_damage=after_target_damage,
        pressure_mitigation=after_pressure_mitigation,
        defeated_targets=after_defeated_targets,
    )
    state_changes = [
        f"unit_positions.{group.unit_name} -> {target.name}",
        f"target_damage.{target.name} +{applied_damage}",
    ]
    if record_pressure_mitigation:
        state_changes.append(f"pressure_mitigation.{target.name} +{applied_mitigation}")
    if unit_losses > 0:
        state_changes.append(f"unit_losses.{group.unit_name} -{unit_losses}")
        state_changes.append(f"used_supply -{unit_model.cost.supply * unit_losses}")
    state_changes.append(f"combat.{action_label} {group.unit_name}x{group.count}")
    if newly_defeated:
        state_changes.append(f"defeated_targets.{target.name} +1")

    return ToyCraftExecutionResult(
        intent=payload.intent,
        validation=validation,
        before_state=state,
        after_state=after_state,
        executed=True,
        read_only=False,
        state_changes=tuple(state_changes),
        executed_actions=_combat_executed_actions(
            action_label=action_label,
            unit_name=group.unit_name,
            unit_count=group.count,
            target_name=target.name,
            applied_damage=applied_damage,
            applied_mitigation=applied_mitigation,
            unit_losses=unit_losses,
            newly_defeated=newly_defeated,
            record_pressure_mitigation=record_pressure_mitigation,
        ),
        narration=narration.format(
            unit_count=group.count,
            unit_name=group.unit_name,
            target_name=target.name,
            damage=applied_damage,
            mitigated=applied_mitigation,
            total_damage=total_damage,
            total_mitigation=total_mitigation,
            target_hp=target_hp,
            loss_summary=loss_summary,
            outcome=outcome,
        ),
    )


def _calculate_combat_unit_losses(
    *,
    group_count: int,
    unit_hit_points: int,
    counter_damage: int,
    neutralized: bool,
) -> int:
    if neutralized or counter_damage <= 0:
        return 0
    group_durability = unit_hit_points * group_count
    return min(group_count, counter_damage // group_durability)


def _apply_unit_losses(
    units: Mapping[str, int],
    unit_name: str,
    unit_losses: int,
) -> dict[str, int]:
    if unit_losses <= 0:
        return dict(units)
    after_units = dict(units)
    remaining_count = after_units.get(unit_name, 0) - unit_losses
    if remaining_count > 0:
        after_units[unit_name] = remaining_count
    else:
        after_units.pop(unit_name, None)
    return after_units


def _format_combat_loss_summary(unit_name: str, unit_losses: int) -> str:
    if unit_losses <= 0:
        return "아군 손실은 없습니다."
    return f"아군 손실은 {unit_name} {unit_losses}기입니다."


def _resolve_combat_group(unit_group: str, state: ToyCraftState) -> _ResolvedCombatGroup:
    normalized = unit_group.strip().lower()
    if normalized in {"available combat units", "all combat units"}:
        combat_counts = {
            unit_name: state.unit_count(unit_name)
            for unit_name in ("Marine", "Vulture")
            if state.unit_count(unit_name) > 0
        }
        if not combat_counts:
            raise RuntimeError("validated combat group was unavailable at execution time.")
        unit_name = max(combat_counts, key=combat_counts.get)
        return _ResolvedCombatGroup(unit_name=unit_name, count=combat_counts[unit_name])

    requested_count = _extract_leading_count(normalized)
    for candidate in UNIT_NAMES:
        if _unit_group_mentions(unit_group, candidate):
            available_count = state.unit_count(candidate)
            count = available_count if requested_count is None else min(requested_count, available_count)
            if count <= 0:
                raise RuntimeError("validated combat group resolved to zero units.")
            return _ResolvedCombatGroup(unit_name=candidate, count=count)
    raise RuntimeError("validated combat group could not be resolved.")


def _extract_leading_count(value: str) -> int | None:
    parts = value.split(maxsplit=1)
    if not parts or not parts[0].isdigit():
        return None
    return int(parts[0])


def _unit_group_mentions(unit_group: str, unit_name: str) -> bool:
    normalized = unit_group.strip().lower()
    if resolve_unit_name(normalized) == unit_name:
        return True
    words = normalized.replace("-", " ").split()
    return any(resolve_unit_name(word) == unit_name for word in words)


def _apply_construction_start(
    state: ToyCraftState,
    *,
    structure_name: str,
    location: str,
    cost: Mapping[str, int],
    build_time_seconds: int,
) -> ToyCraftState:
    after_resources = _spend_resources(state, cost)
    construction_order = ConstructionOrder(
        structure_name=structure_name,
        location=location,
        remaining_seconds=build_time_seconds,
        assigned_workers=1,
    )

    return replace(
        state,
        resources=after_resources,
        busy_workers=state.busy_workers + 1,
        construction_queue=(*state.construction_queue, construction_order),
    )


def _apply_training_queue(
    state: ToyCraftState,
    *,
    producer: str,
    unit_type: str,
    build_time_seconds: int,
    cost: Mapping[str, int],
    required_supply: int,
    count: int,
) -> ToyCraftState:
    after_resources = _spend_resources(state, cost)
    after_supply = replace(
        state.supply,
        used_supply=state.supply.used_supply + required_supply,
    )
    after_queues = _increment_mapping_count(state.production_queues, producer, count)
    queue_position_offset = state.queued_production_count(producer)
    after_orders = (
        *state.production_orders,
        *(
            ProductionOrder(
                unit_name=unit_type,
                producer=producer,
                remaining_seconds=build_time_seconds * (queue_position_offset + index),
            )
            for index in range(1, count + 1)
        ),
    )

    return replace(
        state,
        resources=after_resources,
        supply=after_supply,
        production_queues=after_queues,
        production_orders=after_orders,
    )


def _spend_resources(state: ToyCraftState, cost: Mapping[str, int]):
    return replace(
        state.resources,
        minerals=state.resources.minerals - cost.get("minerals", 0),
        gas=state.resources.gas - cost.get("gas", 0),
    )


def _increment_mapping_count(
    values: Mapping[str, int],
    key: str,
    amount: int,
) -> dict[str, int]:
    updated = dict(values)
    updated[key] = updated.get(key, 0) + amount
    return updated


def _decrement_mapping_count(
    values: Mapping[str, int],
    key: str,
    amount: int,
) -> dict[str, int]:
    updated = dict(values)
    next_count = max(0, updated.get(key, 0) - amount)
    if next_count == 0:
        updated.pop(key, None)
    else:
        updated[key] = next_count
    return updated


def _advance_production_orders(
    state: ToyCraftState,
    seconds: int,
) -> _ProductionProgressResult:
    remaining_orders: list[ProductionOrder] = []
    completed_units: dict[str, int] = {}
    completed_by_producer: dict[str, int] = {}

    for order in state.production_orders:
        remaining_seconds = order.remaining_seconds - seconds
        if remaining_seconds <= 0:
            completed_units[order.unit_name] = completed_units.get(order.unit_name, 0) + 1
            completed_by_producer[order.producer] = (
                completed_by_producer.get(order.producer, 0) + 1
            )
            continue
        remaining_orders.append(
            ProductionOrder(
                unit_name=order.unit_name,
                producer=order.producer,
                remaining_seconds=remaining_seconds,
            )
        )

    after_units = dict(state.units)
    for unit_name, completed_count in completed_units.items():
        after_units[unit_name] = after_units.get(unit_name, 0) + completed_count

    after_queues = dict(state.production_queues)
    for producer, completed_count in completed_by_producer.items():
        after_queues = _decrement_mapping_count(after_queues, producer, completed_count)

    state_changes = [
        f"production_time_seconds -{seconds}"
        for _order in state.production_orders
    ]
    state_changes.extend(
        f"units.{unit_name} +{completed_count}"
        for unit_name, completed_count in completed_units.items()
    )
    state_changes.extend(
        f"production_queues.{producer} -{completed_count}"
        for producer, completed_count in completed_by_producer.items()
    )

    return _ProductionProgressResult(
        state=replace(
            state,
            units=after_units,
            production_queues=after_queues,
            production_orders=tuple(remaining_orders),
        ),
        completed_units=completed_units,
        state_changes=tuple(state_changes),
    )


def _advance_construction_orders(
    state: ToyCraftState,
    seconds: int,
) -> _ConstructionProgressResult:
    remaining_orders: list[ConstructionOrder] = []
    completed_structures: dict[str, int] = {}
    completed_locations: list[str] = []
    released_workers = 0

    for order in state.construction_queue:
        remaining_seconds = order.remaining_seconds - seconds
        if remaining_seconds <= 0:
            completed_structures[order.structure_name] = (
                completed_structures.get(order.structure_name, 0) + 1
            )
            completed_locations.append(order.location)
            released_workers += order.assigned_workers
            continue
        remaining_orders.append(
            ConstructionOrder(
                structure_name=order.structure_name,
                location=order.location,
                remaining_seconds=remaining_seconds,
                assigned_workers=order.assigned_workers,
            )
        )

    after_structures = dict(state.structures)
    after_supply = state.supply
    for structure_name, completed_count in completed_structures.items():
        after_structures[structure_name] = after_structures.get(structure_name, 0) + completed_count
        supply_provided = _get_structure_supply_provided(structure_name) * completed_count
        if supply_provided:
            after_supply = replace(
                after_supply,
                supply_capacity=after_supply.supply_capacity + supply_provided,
            )

    after_claimed_locations = tuple(
        dict.fromkeys((*state.claimed_locations, *completed_locations))
    )
    after_busy_workers = max(0, state.busy_workers - released_workers)

    state_changes = [
        f"construction_time_seconds -{seconds}"
        for _order in state.construction_queue
    ]
    state_changes.extend(
        f"structures.{structure_name} +{completed_count}"
        for structure_name, completed_count in completed_structures.items()
    )
    state_changes.extend(
        f"construction_queue.{structure_name} -{completed_count}"
        for structure_name, completed_count in completed_structures.items()
    )
    if released_workers:
        state_changes.append(f"busy_workers -{released_workers}")

    return _ConstructionProgressResult(
        state=replace(
            state,
            supply=after_supply,
            structures=after_structures,
            busy_workers=after_busy_workers,
            construction_queue=tuple(remaining_orders),
            claimed_locations=after_claimed_locations,
        ),
        completed_structures=completed_structures,
        state_changes=tuple(state_changes),
    )


def _narrate_time_advance(
    *,
    seconds: int,
    completed_units: Mapping[str, int],
    completed_structures: Mapping[str, int],
    remaining_production_orders: int,
    remaining_construction_orders: int,
) -> str:
    completed_parts = []
    unit_text = _format_counts(completed_units, empty_text="")
    structure_text = _format_counts(completed_structures, empty_text="")
    if unit_text:
        completed_parts.append(f"완료된 유닛은 {unit_text}")
    if structure_text:
        completed_parts.append(f"완료된 구조물은 {structure_text}")
    completed_text = ", ".join(completed_parts) if completed_parts else "아직 완료된 생산이나 건설은 없습니다"
    return (
        f"ToyCraft 시간을 {seconds}초 진행했습니다. {completed_text}. "
        f"남은 생산 주문 {remaining_production_orders}개, "
        f"남은 건설 주문 {remaining_construction_orders}개입니다."
    )


def _get_structure_cost(structure_name: str) -> dict[str, int]:
    if structure_name in PHASE_ZERO_SPECIAL_STRUCTURE_COSTS:
        return dict(PHASE_ZERO_SPECIAL_STRUCTURE_COSTS[structure_name])
    cost = get_structure_model(structure_name).cost
    return {"minerals": cost.minerals, "gas": cost.gas}


def _get_structure_supply_provided(structure_name: str) -> int:
    if structure_name in PHASE_ZERO_SPECIAL_STRUCTURE_SUPPLY:
        return PHASE_ZERO_SPECIAL_STRUCTURE_SUPPLY[structure_name]
    return get_structure_model(structure_name).supply_provided


def _get_structure_build_time(structure_name: str) -> int:
    if structure_name in PHASE_ZERO_SPECIAL_STRUCTURE_BUILD_TIMES:
        return PHASE_ZERO_SPECIAL_STRUCTURE_BUILD_TIMES[structure_name]
    return get_structure_model(structure_name).cost.build_time_seconds


def _resource_spend_changes(cost: Mapping[str, int]) -> tuple[str, ...]:
    return (
        f"minerals -{cost.get('minerals', 0)}",
        f"gas -{cost.get('gas', 0)}",
    )


def _format_construction_queue(values: tuple[object, ...]) -> str:
    formatted: list[str] = []
    for value in values:
        if not isinstance(value, Mapping):
            continue
        structure_name = value.get("structure_name")
        location = value.get("location")
        remaining_seconds = value.get("remaining_seconds")
        assigned_workers = value.get("assigned_workers")
        if (
            isinstance(structure_name, str)
            and isinstance(location, str)
            and isinstance(remaining_seconds, int)
            and isinstance(assigned_workers, int)
        ):
            formatted.append(
                f"{structure_name}({location}, {remaining_seconds}초, SCV {assigned_workers})"
            )
    if not formatted:
        return "건설 없음"
    return ", ".join(formatted)


TOYCRAFT_EXECUTION_RULES: Final[dict[IntentName, ToyCraftExecutionRule]] = {
    "BUILD_STRUCTURE": execute_build_structure,
    "DEFEND": execute_defend,
    "EXPAND": execute_expand,
    "GATHER_RESOURCE": execute_gather_resource,
    "HARASS": execute_harass,
    "SUMMARIZE_STATE": execute_summarize_state,
    "TRAIN_ARMY": execute_train_army,
    "TRAIN_WORKER": execute_train_worker,
}
