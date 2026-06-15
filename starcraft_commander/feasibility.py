"""Live StarCraft II feasibility gating for commander Intent DSL payloads.

This module gates commander intents against a resolved
:class:`~starcraft_commander.state_resolver.SC2CommanderState` snapshot before
planning or execution. It is intentionally importable without StarCraft II or
python-sc2 installed and follows the conservative house rule: when game state
is unknown (``state is None``) or incompletely observed, mutating commands are
rejected with a precise Korean reason and an actionable Korean alternative
instead of being optimistically guessed. Only the read-only SUMMARIZE_STATE
intent stays executable under incomplete observation.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Protocol, runtime_checkable

from starcraft_commander.sc2_executor import SC2_UNIT_TYPE_IDS
from starcraft_commander.state_resolver import SC2_WORKER_TYPE_NAME, SC2CommanderState


@dataclass(frozen=True)
class SC2UnitCost:
    """Terran MVP unit production cost plus its required producer structure."""

    minerals: int
    vespene: int
    supply: int
    producer: str

    def __post_init__(self) -> None:
        for field_name in ("minerals", "vespene", "supply"):
            value = getattr(self, field_name)
            if type(value) is not int:
                raise TypeError(f"SC2 unit cost {field_name} must be an int.")
            if value < 0:
                raise ValueError(f"SC2 unit cost {field_name} cannot be negative.")
        if type(self.producer) is not str or not self.producer.strip():
            raise ValueError("SC2 unit cost producer must be a non-empty string.")

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready unit cost payload."""

        return {
            "minerals": self.minerals,
            "vespene": self.vespene,
            "supply": self.supply,
            "producer": self.producer,
        }


@dataclass(frozen=True)
class SC2StructureCost:
    """Terran MVP structure cost plus its tech prerequisite structures."""

    minerals: int
    vespene: int
    tech_requirements: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("minerals", "vespene"):
            value = getattr(self, field_name)
            if type(value) is not int:
                raise TypeError(f"SC2 structure cost {field_name} must be an int.")
            if value < 0:
                raise ValueError(f"SC2 structure cost {field_name} cannot be negative.")
        requirements = tuple(str(item) for item in self.tech_requirements)
        for requirement in requirements:
            if not requirement.strip():
                raise ValueError(
                    "SC2 structure cost tech_requirements must be non-empty strings."
                )
        object.__setattr__(self, "tech_requirements", requirements)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready structure cost payload."""

        return {
            "minerals": self.minerals,
            "vespene": self.vespene,
            "tech_requirements": list(self.tech_requirements),
        }


SC2_UNIT_COSTS: Final[dict[str, SC2UnitCost]] = {
    "SCV": SC2UnitCost(minerals=50, vespene=0, supply=1, producer="COMMANDCENTER"),
    "MARINE": SC2UnitCost(minerals=50, vespene=0, supply=1, producer="BARRACKS"),
    "HELLION": SC2UnitCost(minerals=100, vespene=0, supply=2, producer="FACTORY"),
}
"""Terran MVP unit costs keyed by UPPERCASE space-free SC2 type name."""

SC2_STRUCTURE_COSTS: Final[dict[str, SC2StructureCost]] = {
    "SUPPLYDEPOT": SC2StructureCost(minerals=100, vespene=0),
    "BARRACKS": SC2StructureCost(minerals=150, vespene=0, tech_requirements=("SUPPLYDEPOT",)),
    "FACTORY": SC2StructureCost(minerals=150, vespene=100, tech_requirements=("BARRACKS",)),
    "REFINERY": SC2StructureCost(minerals=75, vespene=0),
    "COMMANDCENTER": SC2StructureCost(minerals=400, vespene=0),
    "BUNKER": SC2StructureCost(minerals=100, vespene=0, tech_requirements=("BARRACKS",)),
}
"""Terran MVP structure costs keyed by UPPERCASE space-free SC2 type name."""

SC2_UNIT_KOREAN_NAMES: Final[dict[str, str]] = {
    "SCV": "SCV",
    "MARINE": "해병",
    "HELLION": "화염차",
}
"""Korean narration names for Terran MVP units."""

SC2_STRUCTURE_KOREAN_NAMES: Final[dict[str, str]] = {
    "SUPPLYDEPOT": "보급고",
    "BARRACKS": "병영",
    "FACTORY": "군수공장",
    "REFINERY": "정제소",
    "COMMANDCENTER": "사령부",
    "BUNKER": "벙커",
}
"""Korean narration names for Terran MVP structures."""

SC2_FEASIBILITY_REASON_CODES: Final[frozenset[str]] = frozenset(
    {
        "unknown_state",
        "incomplete_observation",
        "insufficient_minerals",
        "insufficient_vespene",
        "insufficient_supply",
        "missing_producer",
        "missing_refinery",
        "missing_tech_requirement",
        "no_workers",
        "no_units",
        "unsupported_unit",
        "unsupported_structure",
        "unsupported_intent",
        "invalid_payload",
    }
)
"""Stable machine-readable reason codes emitted by the default validator."""


@dataclass(frozen=True)
class SC2FeasibilityIssue:
    """One machine-readable rejection with Korean reason and alternative."""

    code: str
    reason: str
    alternative: str

    def __post_init__(self) -> None:
        for field_name in ("code", "reason", "alternative"):
            value = getattr(self, field_name)
            if type(value) is not str or not value.strip():
                raise ValueError(
                    f"SC2 feasibility issue {field_name} must be a non-empty string."
                )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready issue payload."""

        return {
            "code": self.code,
            "reason": self.reason,
            "alternative": self.alternative,
        }


@dataclass(frozen=True)
class SC2FeasibilityResult:
    """Outcome of gating one commander intent against live SC2 state.

    Invariants: an executable result carries no reason codes or reasons; a
    rejected result carries at least one reason code, at least one Korean
    reason, and a non-empty Korean actionable alternative.
    """

    executable: bool
    intent_name: str
    reason_codes: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    alternative: str = ""
    checked: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.executable) is not bool:
            raise TypeError("SC2 feasibility result executable must be a bool.")
        if type(self.intent_name) is not str or not self.intent_name.strip():
            raise ValueError("SC2 feasibility result intent_name must be non-empty.")
        if type(self.alternative) is not str:
            raise TypeError("SC2 feasibility result alternative must be a string.")
        reason_codes = tuple(str(code) for code in self.reason_codes)
        reasons = tuple(str(reason) for reason in self.reasons)
        checked = tuple(str(check) for check in self.checked)
        for code in reason_codes:
            if not code.strip():
                raise ValueError("SC2 feasibility result reason codes must be non-empty.")
        for reason in reasons:
            if not reason.strip():
                raise ValueError("SC2 feasibility result reasons must be non-empty.")
        if self.executable:
            if reason_codes or reasons:
                raise ValueError(
                    "executable SC2 feasibility result cannot carry reason codes or reasons."
                )
        else:
            if not reason_codes:
                raise ValueError(
                    "rejected SC2 feasibility result requires at least one reason code."
                )
            if not reasons:
                raise ValueError(
                    "rejected SC2 feasibility result requires at least one reason."
                )
            if not self.alternative.strip():
                raise ValueError(
                    "rejected SC2 feasibility result requires a non-empty alternative."
                )
        object.__setattr__(self, "reason_codes", reason_codes)
        object.__setattr__(self, "reasons", reasons)
        object.__setattr__(self, "checked", checked)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready feasibility result payload."""

        return {
            "executable": self.executable,
            "intent_name": self.intent_name,
            "reason_codes": list(self.reason_codes),
            "reasons": list(self.reasons),
            "alternative": self.alternative,
            "checked": list(self.checked),
        }


SC2IntentFeasibilityRule = Callable[
    [Any, SC2CommanderState, "list[str]"],
    "tuple[SC2FeasibilityIssue, ...]",
]
"""One per-intent rule: (payload, state, checked accumulator) -> issues."""


@runtime_checkable
class SC2FeasibilityValidatorInterface(Protocol):
    """Boundary gating commander intent payloads against live SC2 state."""

    def validate_payload(
        self,
        payload: object | Mapping[str, object],
        state: SC2CommanderState | None,
    ) -> SC2FeasibilityResult:
        """Return an executable or rejected result without mutating state."""


_GAS_RESOURCE_NAMES: Final[frozenset[str]] = frozenset({"gas", "vespene", "vespene_gas"})
"""Gather-resource payload values that require a completed Refinery."""


def _check_gather_resource(
    payload: object | Mapping[str, object],
    state: SC2CommanderState,
    checked: list[str],
) -> tuple[SC2FeasibilityIssue, ...]:
    checked.append("worker_count")
    worker_count, count_issue = _read_positive_count(payload, "worker_count")
    if count_issue is not None:
        return (count_issue,)
    checked.append("worker_availability")
    available_workers = state.own_units.get(SC2_WORKER_TYPE_NAME, 0)
    if available_workers < 1:
        return (_no_workers_issue(),)
    resource = str(_payload_field(payload, "resource", "")).strip().lower()
    if resource in _GAS_RESOURCE_NAMES:
        # Gas gathering requires a completed Refinery: the live game silently
        # rejects gather orders on a bare geyser, so reject honestly here.
        checked.append("refinery_presence")
        if state.own_structures.get("REFINERY", 0) < 1:
            return (_missing_refinery_issue(),)
    if worker_count > available_workers:
        # Conservative cap: still executable, recorded as a checked entry only.
        checked.append("worker_count_capped")
    return ()


def _check_build_structure(
    payload: object | Mapping[str, object],
    state: SC2CommanderState,
    checked: list[str],
) -> tuple[SC2FeasibilityIssue, ...]:
    checked.append("structure_vocabulary")
    structure = str(_payload_field(payload, "structure", ""))
    structure_code = resolve_sc2_structure_code(structure)
    if structure_code is None:
        return (_unsupported_structure_issue(structure),)
    return _structure_issues(structure_code, state, checked)


def _check_train_worker(
    payload: object | Mapping[str, object],
    state: SC2CommanderState,
    checked: list[str],
) -> tuple[SC2FeasibilityIssue, ...]:
    checked.append("count")
    count, count_issue = _read_positive_count(payload, "count")
    if count_issue is not None:
        return (count_issue,)
    return _train_issues(SC2_WORKER_TYPE_NAME, count, state, checked)


def _check_train_army(
    payload: object | Mapping[str, object],
    state: SC2CommanderState,
    checked: list[str],
) -> tuple[SC2FeasibilityIssue, ...]:
    checked.append("unit_vocabulary")
    unit_type = str(_payload_field(payload, "unit_type", ""))
    unit_code = resolve_sc2_unit_code(unit_type)
    if unit_code is None:
        return (_unsupported_unit_issue(unit_type),)
    checked.append("count")
    count, count_issue = _read_positive_count(payload, "count")
    if count_issue is not None:
        return (count_issue,)
    return _train_issues(unit_code, count, state, checked)


def _check_scout(
    payload: object | Mapping[str, object],
    state: SC2CommanderState,
    checked: list[str],
) -> tuple[SC2FeasibilityIssue, ...]:
    checked.append("unit_availability")
    # An SCV may substitute as a scout, so any own unit qualifies.
    if sum(state.own_units.values()) > 0 or state.army_count > 0:
        return ()
    return (
        SC2FeasibilityIssue(
            code="no_units",
            reason="정찰을 보낼 아군 유닛이 없습니다.",
            alternative="먼저 사령부에서 SCV를 생산하세요.",
        ),
    )


def _check_summarize_state(
    payload: object | Mapping[str, object],
    state: SC2CommanderState,
    checked: list[str],
) -> tuple[SC2FeasibilityIssue, ...]:
    checked.append("read_only")
    return ()


def _check_defend(
    payload: object | Mapping[str, object],
    state: SC2CommanderState,
    checked: list[str],
) -> tuple[SC2FeasibilityIssue, ...]:
    return _combat_unit_issues(state, checked)


def _check_repair(
    payload: object | Mapping[str, object],
    state: SC2CommanderState,
    checked: list[str],
) -> tuple[SC2FeasibilityIssue, ...]:
    checked.append("worker_availability")
    if state.own_units.get(SC2_WORKER_TYPE_NAME, 0) >= 1:
        return ()
    return (_no_workers_issue(),)


def _check_expand(
    payload: object | Mapping[str, object],
    state: SC2CommanderState,
    checked: list[str],
) -> tuple[SC2FeasibilityIssue, ...]:
    checked.append("structure_vocabulary")
    return _structure_issues("COMMANDCENTER", state, checked)


def _check_harass(
    payload: object | Mapping[str, object],
    state: SC2CommanderState,
    checked: list[str],
) -> tuple[SC2FeasibilityIssue, ...]:
    return _combat_unit_issues(state, checked)


def _check_move_camera(
    payload: object | Mapping[str, object],
    state: SC2CommanderState,
    checked: list[str],
) -> tuple[SC2FeasibilityIssue, ...]:
    checked.append("camera_target")
    checked.append("camera_capability_deferred_to_runtime")
    return ()


SC2_INTENT_FEASIBILITY_RULES: Final[dict[str, SC2IntentFeasibilityRule]] = {
    "GATHER_RESOURCE": _check_gather_resource,
    "BUILD_STRUCTURE": _check_build_structure,
    "TRAIN_WORKER": _check_train_worker,
    "TRAIN_ARMY": _check_train_army,
    "SCOUT": _check_scout,
    "SUMMARIZE_STATE": _check_summarize_state,
    "DEFEND": _check_defend,
    "REPAIR": _check_repair,
    "EXPAND": _check_expand,
    "HARASS": _check_harass,
    "MOVE_CAMERA": _check_move_camera,
}
"""One feasibility rule per canonical commander intent."""

SC2_CANONICAL_INTENT_NAMES: Final[tuple[str, ...]] = tuple(SC2_INTENT_FEASIBILITY_RULES)
"""The canonical commander intent names gated by this module."""


@dataclass(frozen=True)
class SC2FeasibilityValidator:
    """Default conservative rule-table validator for live SC2 state gating."""

    rules: Mapping[str, SC2IntentFeasibilityRule] = field(
        default_factory=lambda: SC2_INTENT_FEASIBILITY_RULES
    )

    def validate_payload(
        self,
        payload: object | Mapping[str, object],
        state: SC2CommanderState | None,
    ) -> SC2FeasibilityResult:
        """Gate one Intent DSL payload (mapping or attribute object)."""

        intent_name = _intent_name(payload)
        result_intent = intent_name if intent_name else "UNKNOWN"
        checked: list[str] = ["state"]

        if state is None:
            return _rejected_result(result_intent, (_unknown_state_issue(),), checked)
        if not isinstance(state, SC2CommanderState):
            raise TypeError("state must be an SC2CommanderState or None.")

        checked.append("intent")
        rule = self.rules.get(intent_name)
        if rule is None:
            return _rejected_result(
                result_intent,
                (_unsupported_intent_issue(intent_name),),
                checked,
            )

        if intent_name != "SUMMARIZE_STATE":
            checked.append("observation")
            if not state.observation_complete:
                return _rejected_result(
                    result_intent,
                    (_incomplete_observation_issue(state),),
                    checked,
                )

        issues = rule(payload, state, checked)
        if issues:
            return _rejected_result(result_intent, issues, checked)
        return _executable_result(result_intent, checked)


DEFAULT_SC2_FEASIBILITY_VALIDATOR: Final[SC2FeasibilityValidator] = SC2FeasibilityValidator()
"""Shared default validator used by the module-level convenience function."""


def validate_sc2_feasibility(
    payload: object | Mapping[str, object],
    state: SC2CommanderState | None,
) -> SC2FeasibilityResult:
    """Gate one commander intent payload with the default SC2 validator."""

    return DEFAULT_SC2_FEASIBILITY_VALIDATOR.validate_payload(payload, state)


def resolve_sc2_unit_code(unit_type: object) -> str | None:
    """Resolve a planner unit name (for example ``Vulture``) to a cost key."""

    if type(unit_type) is not str:
        return None
    mapped = SC2_UNIT_TYPE_IDS.get(unit_type)
    if mapped is not None and mapped in SC2_UNIT_COSTS:
        return mapped
    normalized = _normalized_type_code(unit_type)
    if normalized in SC2_UNIT_COSTS:
        return normalized
    return None


def resolve_sc2_structure_code(structure: object) -> str | None:
    """Resolve a planner structure name (for example ``Supply Depot``)."""

    if type(structure) is not str:
        return None
    normalized = _normalized_type_code(structure)
    if normalized in SC2_STRUCTURE_COSTS:
        return normalized
    return None


_MISSING: Final[object] = object()


def _payload_field(
    payload: object | Mapping[str, object],
    field_name: str,
    default: object = _MISSING,
) -> Any:
    if isinstance(payload, Mapping):
        return payload.get(field_name, default)
    return getattr(payload, field_name, default)


def _intent_name(payload: object | Mapping[str, object]) -> str:
    return str(_payload_field(payload, "intent", "")).strip()


def _normalized_type_code(value: str) -> str:
    return "".join(value.split()).upper()


def _read_positive_count(
    payload: object | Mapping[str, object],
    field_name: str,
) -> tuple[int, SC2FeasibilityIssue | None]:
    value = _payload_field(payload, field_name, 1)
    if type(value) is bool:
        return 0, _invalid_count_issue(field_name, value)
    try:
        count = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0, _invalid_count_issue(field_name, value)
    if count < 1:
        return 0, _invalid_count_issue(field_name, value)
    return count, None


def _train_issues(
    unit_code: str,
    count: int,
    state: SC2CommanderState,
    checked: list[str],
) -> tuple[SC2FeasibilityIssue, ...]:
    cost = SC2_UNIT_COSTS[unit_code]
    issues = list(
        _resource_issues(cost.minerals * count, cost.vespene * count, state, checked)
    )
    checked.append("supply")
    required_supply = cost.supply * count
    if required_supply > state.supply_left:
        issues.append(_insufficient_supply_issue(state.supply_left, required_supply))
    checked.append("producer")
    if state.own_structures.get(cost.producer, 0) < 1:
        issues.append(_missing_producer_issue(unit_code, cost.producer))
    return tuple(issues)


def _structure_issues(
    structure_code: str,
    state: SC2CommanderState,
    checked: list[str],
) -> tuple[SC2FeasibilityIssue, ...]:
    cost = SC2_STRUCTURE_COSTS[structure_code]
    issues = list(_resource_issues(cost.minerals, cost.vespene, state, checked))
    checked.append("tech_requirements")
    for requirement in cost.tech_requirements:
        if state.own_structures.get(requirement, 0) < 1:
            issues.append(_missing_tech_requirement_issue(structure_code, requirement))
    checked.append("worker_availability")
    if state.own_units.get(SC2_WORKER_TYPE_NAME, 0) < 1:
        issues.append(_no_workers_issue())
    return tuple(issues)


def _combat_unit_issues(
    state: SC2CommanderState,
    checked: list[str],
) -> tuple[SC2FeasibilityIssue, ...]:
    checked.append("unit_availability")
    non_worker_units = sum(
        count
        for type_name, count in state.own_units.items()
        if type_name != SC2_WORKER_TYPE_NAME
    )
    if state.army_count > 0 or non_worker_units > 0:
        return ()
    return (
        SC2FeasibilityIssue(
            code="no_units",
            reason="명령을 수행할 아군 전투 유닛이 없습니다.",
            alternative="병영에서 해병을 생산한 뒤 다시 시도하세요.",
        ),
    )


def _resource_issues(
    required_minerals: int,
    required_vespene: int,
    state: SC2CommanderState,
    checked: list[str],
) -> tuple[SC2FeasibilityIssue, ...]:
    issues: list[SC2FeasibilityIssue] = []
    checked.append("minerals")
    if required_minerals > state.minerals:
        shortfall = required_minerals - state.minerals
        issues.append(
            SC2FeasibilityIssue(
                code="insufficient_minerals",
                reason=(
                    f"미네랄 {shortfall} 부족: 현재 {state.minerals}, "
                    f"필요 {required_minerals}."
                ),
                alternative="SCV로 미네랄을 더 채취한 뒤 다시 시도하세요.",
            )
        )
    checked.append("vespene")
    if required_vespene > state.vespene:
        shortfall = required_vespene - state.vespene
        issues.append(
            SC2FeasibilityIssue(
                code="insufficient_vespene",
                reason=(
                    f"가스 {shortfall} 부족: 현재 {state.vespene}, "
                    f"필요 {required_vespene}."
                ),
                alternative="정제소를 건설해 가스를 채취한 뒤 다시 시도하세요.",
            )
        )
    return tuple(issues)


def _insufficient_supply_issue(supply_left: int, required: int) -> SC2FeasibilityIssue:
    shortfall = required - supply_left
    return SC2FeasibilityIssue(
        code="insufficient_supply",
        reason=f"보급 {shortfall} 부족: 남은 보급 {supply_left}, 필요 {required}.",
        alternative="먼저 보급고를 건설하세요.",
    )


def _missing_producer_issue(unit_code: str, producer_code: str) -> SC2FeasibilityIssue:
    unit_label = _unit_label(unit_code)
    producer_korean = SC2_STRUCTURE_KOREAN_NAMES.get(producer_code, producer_code)
    producer_label = _structure_label(producer_code)
    return SC2FeasibilityIssue(
        code="missing_producer",
        reason=f"{unit_label} 생산에 필요한 {producer_label} 건물이 없습니다.",
        alternative=f"먼저 {producer_korean}{_object_particle(producer_korean)} 건설하세요.",
    )


def _missing_tech_requirement_issue(
    structure_code: str,
    requirement_code: str,
) -> SC2FeasibilityIssue:
    requirement_korean = SC2_STRUCTURE_KOREAN_NAMES.get(requirement_code, requirement_code)
    return SC2FeasibilityIssue(
        code="missing_tech_requirement",
        reason=(
            f"{_structure_label(structure_code)} 건설에는 "
            f"{_structure_label(requirement_code)} 건물이 먼저 필요합니다."
        ),
        alternative=(
            f"먼저 {requirement_korean}{_object_particle(requirement_korean)} 건설하세요."
        ),
    )


def _missing_refinery_issue() -> SC2FeasibilityIssue:
    return SC2FeasibilityIssue(
        code="missing_refinery",
        reason="가스 채취에는 완성된 정제소가 필요합니다. 현재 정제소가 없습니다.",
        alternative="먼저 본진 가스 간헐천에 정제소를 건설하세요.",
    )


def _no_workers_issue() -> SC2FeasibilityIssue:
    return SC2FeasibilityIssue(
        code="no_workers",
        reason="사용할 수 있는 SCV가 없습니다.",
        alternative="사령부에서 SCV를 생산하세요.",
    )


def _unsupported_unit_issue(unit_type: str) -> SC2FeasibilityIssue:
    supported = ", ".join(SC2_UNIT_TYPE_IDS)
    return SC2FeasibilityIssue(
        code="unsupported_unit",
        reason=f"지원하지 않는 생산 유닛입니다: {unit_type!r}. 지원 유닛: {supported}.",
        alternative="Marine 또는 Vulture 생산을 요청하세요.",
    )


def _unsupported_structure_issue(structure: str) -> SC2FeasibilityIssue:
    supported = ", ".join(SC2_STRUCTURE_KOREAN_NAMES.values())
    return SC2FeasibilityIssue(
        code="unsupported_structure",
        reason=f"지원하지 않는 건물입니다: {structure!r}. 지원 건물: {supported}.",
        alternative="보급고, 병영, 군수공장, 정제소, 사령부, 벙커 중에서 선택하세요.",
    )


def _unsupported_intent_issue(intent_name: str) -> SC2FeasibilityIssue:
    supported = ", ".join(SC2_CANONICAL_INTENT_NAMES)
    return SC2FeasibilityIssue(
        code="unsupported_intent",
        reason=f"지원하지 않는 명령입니다: {intent_name!r}. 지원 명령: {supported}.",
        alternative="지원되는 명령 중 하나로 다시 말씀해 주세요.",
    )


def _unknown_state_issue() -> SC2FeasibilityIssue:
    return SC2FeasibilityIssue(
        code="unknown_state",
        reason=(
            "상태를 확인할 수 없어 명령을 보류합니다. "
            "실시간 StarCraft II 관측 상태가 아직 연결되지 않았습니다."
        ),
        alternative="게임 관측이 연결된 뒤 다시 명령해 주세요.",
    )


def _incomplete_observation_issue(state: SC2CommanderState) -> SC2FeasibilityIssue:
    note_count = len(state.observation_notes)
    return SC2FeasibilityIssue(
        code="incomplete_observation",
        reason=f"관측이 불완전하여 명령을 보류합니다 (관측 메모 {note_count}건).",
        alternative=(
            "관측이 복구된 뒤 다시 시도하거나, "
            "먼저 현재 상황 요약(SUMMARIZE_STATE)을 요청하세요."
        ),
    )


def _invalid_count_issue(field_name: str, value: object) -> SC2FeasibilityIssue:
    return SC2FeasibilityIssue(
        code="invalid_payload",
        reason=f"{field_name} 값이 올바르지 않습니다: {value!r}. 1 이상의 정수가 필요합니다.",
        alternative="1 이상의 개수로 다시 명령해 주세요.",
    )


def _unit_label(unit_code: str) -> str:
    korean = SC2_UNIT_KOREAN_NAMES.get(unit_code, unit_code)
    if korean == unit_code:
        return unit_code
    return f"{korean}({unit_code})"


def _structure_label(structure_code: str) -> str:
    korean = SC2_STRUCTURE_KOREAN_NAMES.get(structure_code, structure_code)
    if korean == structure_code:
        return structure_code
    return f"{korean}({structure_code})"


def _object_particle(word: str) -> str:
    """Return the Korean object particle (을/를) for a noun's final syllable."""

    if not word:
        return "를"
    last = word[-1]
    if "가" <= last <= "힣" and (ord(last) - ord("가")) % 28 != 0:
        return "을"
    return "를"


def _executable_result(intent_name: str, checked: list[str]) -> SC2FeasibilityResult:
    return SC2FeasibilityResult(
        executable=True,
        intent_name=intent_name,
        checked=_deduplicated(checked),
    )


def _rejected_result(
    intent_name: str,
    issues: tuple[SC2FeasibilityIssue, ...],
    checked: list[str],
) -> SC2FeasibilityResult:
    return SC2FeasibilityResult(
        executable=False,
        intent_name=intent_name,
        reason_codes=tuple(issue.code for issue in issues),
        reasons=tuple(issue.reason for issue in issues),
        alternative=issues[0].alternative,
        checked=_deduplicated(checked),
    )


def _deduplicated(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return tuple(ordered)
