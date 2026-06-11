"""State-aware feasibility checks for ToyCraft Intent DSL payloads."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Final

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
    RepairIntent,
    ScoutIntent,
    SummarizeStateIntent,
    TrainArmyIntent,
    TrainWorkerIntent,
    validate_intent_payload,
)
from toycraft_commander.map import (
    MAP_LOCATION_ALIASES,
    MAP_LOCATION_NAMES,
    get_resolved_map_location,
    resolve_location_name,
    resolve_targetable_position,
)
from toycraft_commander.resources import (
    ResourceState,
    SupplyState,
    get_available_supply,
    get_missing_resources,
    get_missing_supply,
)
from toycraft_commander.state_resolver import resolve_unit_group_reference
from toycraft_commander.structures import (
    STRUCTURE_NAMES,
    get_missing_structure_prerequisites,
    get_structure_model,
    resolve_structure_name,
)
from toycraft_commander.units import (
    UnitCost,
    get_unit_model,
    resolve_unit_name,
)


IntentFeasibilityRule = Callable[[IntentPayload, "ToyCraftState"], tuple[FeasibilityIssue, ...]]

EXTRA_PHASE_ZERO_STRUCTURE_NAMES: Final[tuple[str, ...]] = ("Bunker", "Command Center")
PHASE_ZERO_STRUCTURE_NAMES: Final[tuple[str, ...]] = (
    *STRUCTURE_NAMES,
    *EXTRA_PHASE_ZERO_STRUCTURE_NAMES,
)
PHASE_ZERO_STRUCTURE_COSTS: Final[dict[str, dict[str, int]]] = {
    "Bunker": {"minerals": 100, "gas": 0},
    "Command Center": {"minerals": 400, "gas": 0},
}
PHASE_ZERO_STRUCTURE_PREREQUISITES: Final[dict[str, tuple[str, ...]]] = {
    "Bunker": ("Barracks",),
    "Command Center": (),
}
PRODUCTION_QUEUE_CAPACITY_PER_PRODUCER: Final[int] = 5
WORKER_UNIT_NAME: Final[str] = "SCV"
KNOWN_CONFLICTING_CONSTRAINTS: Final[tuple[tuple[str, str], ...]] = (
    ("attack", "retreat"),
    ("attack", "hold position"),
    ("harass", "do not leave base"),
    ("spend minerals", "save minerals"),
)
BUILD_LOCATION_NAMES_BY_STRUCTURE: Final[dict[str, tuple[str, ...]]] = {
    "Supply Depot": ("main", "main base", "main ramp"),
    "Barracks": ("main", "main base"),
    "Refinery": ("main geyser",),
    "Bunker": ("main ramp", "natural choke"),
    "Command Center": ("natural expansion",),
}
COMBAT_STRENGTH_BY_UNIT: Final[dict[str, int]] = {
    "Marine": 1,
    "Vulture": 2,
}


@dataclass(frozen=True)
class ConstructionOrder:
    """In-progress Terran construction started by a reserved builder SCV."""

    structure_name: str
    location: str
    remaining_seconds: int
    assigned_workers: int = 1

    def __post_init__(self) -> None:
        structure_name = _resolve_phase_zero_structure_name(self.structure_name)
        if structure_name is None:
            raise ValueError(
                f"Unsupported ToyCraft construction structure: {self.structure_name}"
            )
        _validate_non_empty_text("location", self.location)
        _validate_positive_integer("remaining_seconds", self.remaining_seconds)
        _validate_positive_integer("assigned_workers", self.assigned_workers)
        object.__setattr__(self, "structure_name", structure_name)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready construction snapshot for narration and tests."""

        return {
            "structure_name": self.structure_name,
            "location": self.location,
            "remaining_seconds": self.remaining_seconds,
            "assigned_workers": self.assigned_workers,
        }


@dataclass(frozen=True)
class ProductionOrder:
    """In-progress Terran unit production tracked separately from queue counts."""

    unit_name: str
    producer: str
    remaining_seconds: int

    def __post_init__(self) -> None:
        unit_name = resolve_unit_name(self.unit_name)
        if unit_name is None:
            raise ValueError(f"Unsupported ToyCraft production unit: {self.unit_name}")
        producer = _resolve_phase_zero_structure_name(self.producer)
        if producer is None:
            raise ValueError(f"Unsupported ToyCraft production producer: {self.producer}")
        _validate_positive_integer("remaining_seconds", self.remaining_seconds)
        object.__setattr__(self, "unit_name", unit_name)
        object.__setattr__(self, "producer", producer)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready production snapshot for narration and tests."""

        return {
            "unit_name": self.unit_name,
            "producer": self.producer,
            "remaining_seconds": self.remaining_seconds,
        }


@dataclass(frozen=True)
class ToyCraftState:
    """Minimal immutable state snapshot consumed by feasibility rules."""

    resources: ResourceState = field(default_factory=lambda: ResourceState(minerals=50, gas=0))
    supply: SupplyState = field(
        default_factory=lambda: SupplyState(used_supply=4, supply_capacity=15)
    )
    units: Mapping[str, int] = field(default_factory=lambda: {WORKER_UNIT_NAME: 4})
    structures: Mapping[str, int] = field(default_factory=lambda: {"Command Center": 1})
    busy_workers: int = 0
    busy_producers: Mapping[str, int] = field(default_factory=dict)
    production_queues: Mapping[str, int] = field(default_factory=dict)
    production_orders: tuple[ProductionOrder, ...] = ()
    construction_queue: tuple[ConstructionOrder, ...] = ()
    claimed_locations: tuple[str, ...] = ("main", "main base")
    damaged_targets: tuple[str, ...] = ()
    unit_positions: Mapping[str, str] = field(default_factory=dict)
    target_damage: Mapping[str, int] = field(default_factory=dict)
    pressure_mitigation: Mapping[str, int] = field(default_factory=dict)
    defeated_targets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_non_negative_integer("busy_workers", self.busy_workers)
        object.__setattr__(self, "units", _normalize_unit_counts(self.units))
        object.__setattr__(self, "structures", _normalize_structure_counts(self.structures))
        object.__setattr__(
            self,
            "busy_producers",
            _normalize_structure_counts(self.busy_producers),
        )
        object.__setattr__(
            self,
            "production_queues",
            _normalize_structure_counts(self.production_queues),
        )
        object.__setattr__(
            self,
            "production_orders",
            _normalize_production_orders(self.production_orders),
        )
        object.__setattr__(
            self,
            "construction_queue",
            _normalize_construction_queue(self.construction_queue),
        )
        object.__setattr__(
            self,
            "claimed_locations",
            _normalize_location_tuple(self.claimed_locations),
        )
        object.__setattr__(
            self,
            "damaged_targets",
            _normalize_target_tuple(self.damaged_targets),
        )
        object.__setattr__(
            self,
            "unit_positions",
            _normalize_unit_positions(self.unit_positions),
        )
        object.__setattr__(
            self,
            "target_damage",
            _normalize_target_damage(self.target_damage),
        )
        object.__setattr__(
            self,
            "pressure_mitigation",
            _normalize_pressure_mitigation(self.pressure_mitigation),
        )
        object.__setattr__(
            self,
            "defeated_targets",
            _normalize_target_tuple(self.defeated_targets),
        )

    def unit_count(self, unit_name: object) -> int:
        """Return available count for one canonical or aliased unit name."""

        resolved_name = resolve_unit_name(unit_name)
        if resolved_name is None:
            return 0
        return self.units.get(resolved_name, 0)

    def available_worker_count(self) -> int:
        """Return workers not already reserved by another ToyCraft order."""

        return max(0, self.unit_count(WORKER_UNIT_NAME) - self.busy_workers)

    def structure_count(self, structure_name: object) -> int:
        """Return completed count for a modeled or Phase 0 special structure."""

        resolved_name = _resolve_phase_zero_structure_name(structure_name)
        if resolved_name is None:
            return 0
        return self.structures.get(resolved_name, 0)

    def available_producer_count(self, producer_name: object) -> int:
        """Return idle producer count for a training command."""

        resolved_name = _resolve_phase_zero_structure_name(producer_name)
        if resolved_name is None:
            return 0
        completed_count = self.structures.get(resolved_name, 0)
        busy_count = self.busy_producers.get(resolved_name, 0)
        return max(0, completed_count - busy_count)

    def queued_production_count(self, producer_name: object) -> int:
        """Return queued production items for one producer type."""

        resolved_name = _resolve_phase_zero_structure_name(producer_name)
        if resolved_name is None:
            return 0
        return max(
            self.production_queues.get(resolved_name, 0),
            self.production_order_count(resolved_name),
        )

    def available_production_queue_slots(self, producer_name: object) -> int:
        """Return open Phase 0 production queue slots for one producer type."""

        resolved_name = _resolve_phase_zero_structure_name(producer_name)
        if resolved_name is None:
            return 0
        completed_count = self.structures.get(resolved_name, 0)
        busy_count = self.busy_producers.get(resolved_name, 0)
        queued_count = self.queued_production_count(resolved_name)
        total_capacity = completed_count * PRODUCTION_QUEUE_CAPACITY_PER_PRODUCER
        return max(0, total_capacity - busy_count - queued_count)

    def has_structure(self, structure_name: object) -> bool:
        """Return whether at least one completed structure exists."""

        return self.structure_count(structure_name) > 0

    def construction_count(self, structure_name: object) -> int:
        """Return in-progress construction count for a structure type."""

        resolved_name = _resolve_phase_zero_structure_name(structure_name)
        if resolved_name is None:
            return 0
        return sum(
            1 for order in self.construction_queue if order.structure_name == resolved_name
        )

    def production_order_count(self, producer_name: object) -> int:
        """Return timed in-progress production orders for one producer type."""

        resolved_name = _resolve_phase_zero_structure_name(producer_name)
        if resolved_name is None:
            return 0
        return sum(1 for order in self.production_orders if order.producer == resolved_name)


def validate_intent_feasibility(
    payload: IntentPayload | Mapping[str, object],
    state: ToyCraftState,
) -> IntentValidationResult:
    """Validate a typed or raw Intent DSL payload against ToyCraft state."""

    if not isinstance(state, ToyCraftState):
        raise TypeError("state must be a ToyCraftState.")

    typed_payload_result = _coerce_intent_payload(payload)
    if not typed_payload_result.executable:
        return typed_payload_result

    typed_payload = typed_payload_result.payload
    if typed_payload is None:
        raise RuntimeError("executable payload validation returned no payload.")

    issues = (
        *_validate_constraint_conflicts(typed_payload),
        *_validate_patrol_constraints(typed_payload),
        *get_intent_feasibility_rule(typed_payload.intent)(typed_payload, state),
    )
    if issues:
        return _rejected_for_issues(typed_payload, issues)
    return IntentValidationResult(executable=True, payload=typed_payload)


def get_intent_feasibility_rule(intent: IntentName) -> IntentFeasibilityRule:
    """Return the state feasibility rule for one canonical intent."""

    try:
        return INTENT_FEASIBILITY_RULES[intent]
    except KeyError as exc:
        raise KeyError(f"Unsupported ToyCraft feasibility rule: {intent}") from exc


def _validate_gather_resource(
    payload: IntentPayload,
    state: ToyCraftState,
) -> tuple[FeasibilityIssue, ...]:
    intent = _expect_payload_type(payload, GatherResourceIntent)
    issues: list[FeasibilityIssue] = []

    if resolve_location_name(intent.base) is None:
        issues.append(_location_issue(intent.base, ("base",)))
    else:
        base = get_resolved_map_location(intent.base)
        if base.kind != "base":
            issues.append(
                _issue(
                    FeasibilityErrorReason.LOCATION_UNAVAILABLE,
                    f"{base.name} is not a base for gathering assignment.",
                    "Assign gathering from a claimed friendly base such as main base.",
                    ("base",),
                )
            )
        elif base.name not in state.claimed_locations:
            issues.append(
                _issue(
                    FeasibilityErrorReason.LOCATION_UNAVAILABLE,
                    f"{base.name} is not a claimed friendly base.",
                    "Use a claimed base such as main, or expand before assigning workers there.",
                    ("base",),
                )
            )
    if intent.worker_count > state.available_worker_count():
        issues.append(
            _issue(
                FeasibilityErrorReason.UNAVAILABLE_WORKER,
                f"{intent.worker_count} SCV(s) requested but only {state.available_worker_count()} are free.",
                "Free more SCVs or request fewer workers for gathering.",
                ("worker_count",),
            )
        )
    if intent.resource == "gas" and not state.has_structure("Refinery"):
        issues.append(
            _issue(
                FeasibilityErrorReason.MISSING_PREREQUISITE,
                "Gas gathering requires a completed Refinery.",
                "Build a Refinery on the main geyser before assigning gas workers.",
                ("resource",),
            )
        )
    return tuple(issues)


def _validate_build_structure(
    payload: IntentPayload,
    state: ToyCraftState,
) -> tuple[FeasibilityIssue, ...]:
    intent = _expect_payload_type(payload, BuildStructureIntent)
    issues: list[FeasibilityIssue] = []
    structure_name = _resolve_phase_zero_structure_name(intent.structure)
    location = resolve_location_name(intent.location)

    if structure_name is None:
        issues.append(
            _issue(
                FeasibilityErrorReason.UNSUPPORTED_PHASE_ZERO_SCOPE,
                f"{intent.structure} is not buildable in Phase 0 ToyCraft.",
                "Use Supply Depot, Barracks, Refinery, Bunker, or EXPAND for a Command Center.",
                ("structure",),
            )
        )
        return tuple(issues)

    if location is None:
        issues.append(_location_issue(intent.location, ("location",)))
    elif not _is_valid_build_location(structure_name, location):
        issues.append(
            _issue(
                FeasibilityErrorReason.LOCATION_UNAVAILABLE,
                f"{structure_name} cannot be placed at {location}.",
                _build_location_alternative(structure_name),
                ("location",),
            )
        )
    elif structure_name == "Refinery" and state.has_structure("Refinery"):
        issues.append(
            _issue(
                FeasibilityErrorReason.LOCATION_UNAVAILABLE,
                "main geyser already has a completed Refinery.",
                "Use the existing Refinery for gas gathering instead of building another one.",
                ("location",),
            )
        )

    if state.available_worker_count() < 1:
        issues.append(_worker_issue())

    issues.extend(_resource_issues(_get_structure_cost(structure_name), state))
    issues.extend(_construction_state_issues(structure_name, state))
    for prerequisite in _get_structure_prerequisites(structure_name, state):
        issues.append(
            _issue(
                FeasibilityErrorReason.MISSING_PREREQUISITE,
                f"{structure_name} requires a completed {prerequisite}.",
                f"Build or finish {prerequisite} before requesting {structure_name}.",
                ("structure",),
            )
        )
    if structure_name == "Command Center" and location in state.claimed_locations:
        issues.append(
            _issue(
                FeasibilityErrorReason.LOCATION_UNAVAILABLE,
                f"{location} is already claimed by an existing base.",
                "Use EXPAND at an unclaimed expansion such as natural expansion.",
                ("location",),
            )
        )
    return tuple(issues)


def _validate_train_worker(
    payload: IntentPayload,
    state: ToyCraftState,
) -> tuple[FeasibilityIssue, ...]:
    intent = _expect_payload_type(payload, TrainWorkerIntent)
    scv_cost = get_unit_model("SCV").cost
    issues = []

    issues.extend(_production_queue_issues("Command Center", intent.count, state))
    issues.extend(_resource_issues(_multiply_unit_cost(scv_cost, intent.count), state))
    issues.extend(_supply_issues(scv_cost.supply * intent.count, state))
    return tuple(issues)


def _validate_train_army(
    payload: IntentPayload,
    state: ToyCraftState,
) -> tuple[FeasibilityIssue, ...]:
    intent = _expect_payload_type(payload, TrainArmyIntent)
    unit_model = get_unit_model(intent.unit_type)
    issues = []

    issues.extend(_production_queue_issues(unit_model.producer, intent.count, state))
    issues.extend(_resource_issues(_multiply_unit_cost(unit_model.cost, intent.count), state))
    issues.extend(_supply_issues(unit_model.cost.supply * intent.count, state))
    return tuple(issues)


def _validate_scout(
    payload: IntentPayload,
    state: ToyCraftState,
) -> tuple[FeasibilityIssue, ...]:
    intent = _expect_payload_type(payload, ScoutIntent)
    return (
        *_enemy_target_issues(intent.target, "scout target"),
        *_unit_group_issues(intent.unit_group, state),
    )


def _validate_summarize_state(
    payload: IntentPayload,
    state: ToyCraftState,
) -> tuple[FeasibilityIssue, ...]:
    _expect_payload_type(payload, SummarizeStateIntent)
    return ()


def _validate_defend(
    payload: IntentPayload,
    state: ToyCraftState,
) -> tuple[FeasibilityIssue, ...]:
    intent = _expect_payload_type(payload, DefendIntent)
    issues: list[FeasibilityIssue] = []
    location_name = resolve_location_name(intent.location)

    if location_name is None:
        issues.append(_location_issue(intent.location, ("location",)))
    else:
        location = get_resolved_map_location(location_name)
        if location.kind == "enemy_position":
            issues.append(
                _issue(
                    FeasibilityErrorReason.INVALID_TARGET,
                    f"{location.name} is an enemy target, not a friendly defense location.",
                    "Defend a friendly location such as main ramp or natural choke.",
                    ("location",),
                )
            )
    issues.extend(_unit_group_issues(intent.unit_group, state))
    return tuple(issues)


def _validate_repair(
    payload: IntentPayload,
    state: ToyCraftState,
) -> tuple[FeasibilityIssue, ...]:
    intent = _expect_payload_type(payload, RepairIntent)
    issues: list[FeasibilityIssue] = []
    target = resolve_targetable_position(intent.target)

    if target is None:
        issues.append(_target_issue(intent.target))
    elif target.kind != "repair_target":
        issues.append(
            _issue(
                FeasibilityErrorReason.INVALID_TARGET,
                f"{target.name} is not a repairable friendly target.",
                "Repair a damaged Terran structure such as front bunker.",
                ("target",),
            )
        )
    elif target.name not in state.damaged_targets:
        issues.append(
            _issue(
                FeasibilityErrorReason.INVALID_TARGET,
                f"{target.name} is not marked as damaged in the current ToyCraft state.",
                "Repair a damaged Terran structure or wait until the target takes damage.",
                ("target",),
            )
        )
    if intent.worker_count > state.available_worker_count():
        issues.append(
            _issue(
                FeasibilityErrorReason.UNAVAILABLE_WORKER,
                f"{intent.worker_count} repair SCV(s) requested but only {state.available_worker_count()} are free.",
                "Free more SCVs or assign fewer workers to repair.",
                ("worker_count",),
            )
        )
    return tuple(issues)


def _validate_expand(
    payload: IntentPayload,
    state: ToyCraftState,
) -> tuple[FeasibilityIssue, ...]:
    intent = _expect_payload_type(payload, ExpandIntent)
    issues: list[FeasibilityIssue] = []
    location_name = resolve_location_name(intent.location)

    if location_name is None:
        issues.append(_location_issue(intent.location, ("location",)))
    else:
        location = get_resolved_map_location(location_name)
        if location.kind != "base":
            issues.append(
                _issue(
                    FeasibilityErrorReason.LOCATION_UNAVAILABLE,
                    f"{location.name} is not a base location for expansion.",
                    "Expand to a base location such as natural expansion.",
                    ("location",),
                )
            )
        if location.name in state.claimed_locations:
            issues.append(
                _issue(
                    FeasibilityErrorReason.LOCATION_UNAVAILABLE,
                    f"{location.name} is already claimed.",
                    "Choose an unclaimed expansion such as natural expansion.",
                    ("location",),
                )
            )
    if state.available_worker_count() < 1:
        issues.append(_worker_issue())
    issues.extend(_resource_issues(_get_structure_cost("Command Center"), state))
    return tuple(issues)


def _validate_harass(
    payload: IntentPayload,
    state: ToyCraftState,
) -> tuple[FeasibilityIssue, ...]:
    intent = _expect_payload_type(payload, HarassIntent)
    issues: list[FeasibilityIssue] = []
    target = resolve_targetable_position(intent.target)

    if target is None:
        issues.append(_target_issue(intent.target))
    elif target.kind != "enemy_position":
        issues.append(
            _issue(
                FeasibilityErrorReason.INVALID_TARGET,
                f"{target.name} is not an enemy harassment target.",
                "Harass an enemy position such as enemy mineral line.",
                ("target",),
            )
        )
    issues.extend(_unit_group_issues(intent.unit_group, state, combat_only=True))
    return tuple(issues)


INTENT_FEASIBILITY_RULES: Final[dict[IntentName, IntentFeasibilityRule]] = {
    "GATHER_RESOURCE": _validate_gather_resource,
    "BUILD_STRUCTURE": _validate_build_structure,
    "TRAIN_WORKER": _validate_train_worker,
    "TRAIN_ARMY": _validate_train_army,
    "SCOUT": _validate_scout,
    "SUMMARIZE_STATE": _validate_summarize_state,
    "DEFEND": _validate_defend,
    "REPAIR": _validate_repair,
    "EXPAND": _validate_expand,
    "HARASS": _validate_harass,
}


def _coerce_intent_payload(
    payload: IntentPayload | Mapping[str, object],
) -> IntentValidationResult:
    if isinstance(
        payload,
        (
            GatherResourceIntent,
            BuildStructureIntent,
            TrainWorkerIntent,
            TrainArmyIntent,
            ScoutIntent,
            SummarizeStateIntent,
            DefendIntent,
            RepairIntent,
            ExpandIntent,
            HarassIntent,
        ),
    ):
        return IntentValidationResult(executable=True, payload=payload)
    return validate_intent_payload(payload)


def _rejected_for_issues(
    payload: IntentPayload,
    issues: tuple[FeasibilityIssue, ...],
) -> IntentValidationResult:
    reason = issues[0].message
    if len(issues) > 1:
        reason = "Command is not feasible: " + "; ".join(issue.message for issue in issues)
    return IntentValidationResult(
        executable=False,
        payload=payload,
        reason=reason,
        alternative=issues[0].alternative,
        issues=issues,
    )


def _validate_constraint_conflicts(payload: IntentPayload) -> tuple[FeasibilityIssue, ...]:
    text = " ".join(payload.constraints).strip().lower()
    if not text:
        return ()
    for first_keyword, second_keyword in KNOWN_CONFLICTING_CONSTRAINTS:
        if first_keyword in text and second_keyword in text:
            return (
                _issue(
                    FeasibilityErrorReason.CONSTRAINT_CONFLICT,
                    f"Constraint conflict: cannot satisfy both {first_keyword!r} and {second_keyword!r}.",
                    "Remove the conflicting constraint or split the order into two commands.",
                    ("constraints",),
                ),
            )
    return ()


def _validate_patrol_constraints(payload: IntentPayload) -> tuple[FeasibilityIssue, ...]:
    text = " ".join(payload.constraints).strip()
    if "patrol" not in text.lower() and "순찰" not in text:
        return ()

    route_locations = _extract_constraint_locations(text)
    if len(route_locations) < 2:
        return (
            _issue(
                FeasibilityErrorReason.INVALID_TARGET,
                "Patrol constraint needs at least two known friendly route points.",
                "Use a route such as patrol between main ramp and natural choke.",
                ("constraints",),
            ),
        )

    for location_name in route_locations:
        location = get_resolved_map_location(location_name)
        if location.kind == "enemy_position":
            return (
                _issue(
                    FeasibilityErrorReason.INVALID_TARGET,
                    f"Patrol route cannot include {location.name}; it is enemy-controlled.",
                    "Use HARASS for enemy movement, or patrol friendly points.",
                    ("constraints",),
                ),
            )
    return ()


def _resource_issues(
    cost: Mapping[str, int],
    state: ToyCraftState,
) -> tuple[FeasibilityIssue, ...]:
    missing_resources = get_missing_resources(state.resources, cost)
    issues: list[FeasibilityIssue] = []
    if missing_resources.get("minerals", 0) > 0:
        issues.append(
            _issue(
                FeasibilityErrorReason.INSUFFICIENT_MINERALS,
                f"Need {missing_resources['minerals']} more minerals.",
                "Gather more minerals or choose a cheaper command.",
                ("minerals",),
            )
        )
    if missing_resources.get("gas", 0) > 0:
        issues.append(
            _issue(
                FeasibilityErrorReason.INSUFFICIENT_GAS,
                f"Need {missing_resources['gas']} more gas.",
                "Gather gas from a Refinery before issuing this command.",
                ("gas",),
            )
        )
    return tuple(issues)


def _supply_issues(required_supply: int, state: ToyCraftState) -> tuple[FeasibilityIssue, ...]:
    missing_supply = get_missing_supply(state.supply, required_supply)
    if missing_supply <= 0:
        return ()
    return (
        _issue(
            FeasibilityErrorReason.INSUFFICIENT_SUPPLY,
            f"Need {missing_supply} more supply.",
            "Build a Supply Depot before training more units.",
            ("supply",),
        ),
    )


def _target_issues(target: object) -> tuple[FeasibilityIssue, ...]:
    if resolve_targetable_position(target) is not None:
        return ()
    return (_target_issue(target),)


def _enemy_target_issues(target: object, target_label: str) -> tuple[FeasibilityIssue, ...]:
    resolved_target = resolve_targetable_position(target)
    if resolved_target is None:
        return (_target_issue(target),)
    if resolved_target.kind != "enemy_position":
        return (
            _issue(
                FeasibilityErrorReason.INVALID_TARGET,
                f"{resolved_target.name} is not an enemy movement target for {target_label}.",
                "Choose an enemy target such as enemy main, enemy front, enemy natural, or enemy mineral line.",
                ("target",),
            ),
        )
    return ()


def _target_issue(target: object) -> FeasibilityIssue:
    return _issue(
        FeasibilityErrorReason.INVALID_TARGET,
        f"{target!r} is not a known or reachable ToyCraft target.",
        "Choose a known target such as enemy main, enemy front, enemy natural, enemy mineral line, or front bunker.",
        ("target",),
    )


def _location_issue(location: object, fields: tuple[str, ...]) -> FeasibilityIssue:
    return _issue(
        FeasibilityErrorReason.LOCATION_UNAVAILABLE,
        f"{location!r} is not a known ToyCraft location.",
        "Choose a known location such as main ramp, main geyser, natural choke, or natural expansion.",
        fields,
    )


def _unit_group_issues(
    unit_group: str,
    state: ToyCraftState,
    *,
    combat_only: bool = False,
) -> tuple[FeasibilityIssue, ...]:
    group = resolve_unit_group_reference(unit_group, state)
    if group.unit_name is not None and not group.player_controlled:
        return (
            _issue(
                FeasibilityErrorReason.UNSUPPORTED_PHASE_ZERO_SCOPE,
                f"{group.unit_name} is enemy-controlled or outside player unit selection.",
                "Select Phase 0 Terran units such as 1 SCV, Marines, Vultures, or available combat units.",
                ("unit_group",),
            ),
        )

    if not group.available:
        return (
            _issue(
                FeasibilityErrorReason.UNAVAILABLE_UNIT_GROUP,
                f"No available units match {unit_group!r}.",
                "Choose an available group such as 1 SCV, all Marines, or available combat units.",
                ("unit_group",),
            ),
        )
    if combat_only and not group.combat_capable:
        return (
            _issue(
                FeasibilityErrorReason.UNAVAILABLE_UNIT_GROUP,
                f"{unit_group!r} is not a combat unit group.",
                "Use available Marines or Vultures for combat commands.",
                ("unit_group",),
            ),
        )
    return ()


def _worker_issue() -> FeasibilityIssue:
    return _issue(
        FeasibilityErrorReason.UNAVAILABLE_WORKER,
        "No free SCV is available for this command.",
        "Free an SCV or wait for the current worker task to finish.",
        ("worker_count",),
    )


def _producer_issue(producer: str, message: str) -> FeasibilityIssue:
    return _issue(
        FeasibilityErrorReason.UNAVAILABLE_PRODUCER,
        message,
        f"Build, finish, or free queue space on a {producer} before issuing this production command.",
        ("producer",),
    )


def _production_queue_issues(
    producer: str,
    requested_count: int,
    state: ToyCraftState,
) -> tuple[FeasibilityIssue, ...]:
    available_slots = state.available_production_queue_slots(producer)
    if available_slots >= requested_count:
        return ()
    completed_count = state.structure_count(producer)
    if completed_count <= 0:
        message = f"{producer} production requires a completed {producer}."
    else:
        message = (
            f"{producer} queue has {available_slots} open slot(s), "
            f"but {requested_count} item(s) were requested."
        )
    return (
        _producer_issue(
            producer,
            message,
        ),
    )


def _issue(
    reason: FeasibilityErrorReason,
    message: str,
    alternative: str,
    fields: tuple[str, ...] = (),
) -> FeasibilityIssue:
    return FeasibilityIssue(
        reason=reason,
        message=message,
        alternative=alternative,
        fields=fields,
    )


def _expect_payload_type(payload: IntentPayload, expected_type: type) -> IntentPayload:
    if not isinstance(payload, expected_type):
        raise TypeError(f"Expected {expected_type.__name__} for feasibility rule.")
    return payload


def _get_structure_cost(structure_name: str) -> dict[str, int]:
    if structure_name in PHASE_ZERO_STRUCTURE_COSTS:
        return PHASE_ZERO_STRUCTURE_COSTS[structure_name]
    model_cost = get_structure_model(structure_name).cost
    return {"minerals": model_cost.minerals, "gas": model_cost.gas}


def _get_structure_prerequisites(
    structure_name: str,
    state: ToyCraftState,
) -> tuple[str, ...]:
    if structure_name in PHASE_ZERO_STRUCTURE_PREREQUISITES:
        return tuple(
            prerequisite
            for prerequisite in PHASE_ZERO_STRUCTURE_PREREQUISITES[structure_name]
            if not state.has_structure(prerequisite)
        )
    return get_missing_structure_prerequisites(structure_name, state.structures)


def _multiply_unit_cost(cost: UnitCost, count: int) -> dict[str, int]:
    return {"minerals": cost.minerals * count, "gas": cost.gas * count}


def _is_valid_build_location(structure_name: str, location_name: str) -> bool:
    if location_name not in BUILD_LOCATION_NAMES_BY_STRUCTURE.get(structure_name, ()):
        return False
    location = get_resolved_map_location(location_name)
    return location.kind != "enemy_position"


def _build_location_alternative(structure_name: str) -> str:
    allowed_locations = BUILD_LOCATION_NAMES_BY_STRUCTURE.get(structure_name, ())
    if not allowed_locations:
        return "Choose a compatible friendly build location."
    return (
        f"Choose a compatible build location for {structure_name}: "
        f"{', '.join(allowed_locations)}."
    )


def _construction_state_issues(
    structure_name: str,
    state: ToyCraftState,
) -> tuple[FeasibilityIssue, ...]:
    for order in state.construction_queue:
        if order.structure_name == structure_name:
            return (
                _issue(
                    FeasibilityErrorReason.LOCATION_UNAVAILABLE,
                    f"{structure_name} is already under construction at {order.location}.",
                    f"Wait for the current {structure_name} construction to finish before starting another one.",
                    ("structure",),
                ),
            )
    if structure_name == "Command Center" or state.has_structure("Command Center"):
        return ()
    return (
        _issue(
            FeasibilityErrorReason.MISSING_PREREQUISITE,
            f"{structure_name} construction requires a completed Command Center staging base.",
            "Keep or rebuild a Command Center before starting additional construction.",
            ("structure",),
        ),
    )


def _extract_constraint_locations(text: str) -> tuple[str, ...]:
    normalized_text = "".join(text.casefold().split())
    route_locations: list[str] = []
    for location_name in sorted(MAP_LOCATION_NAMES, key=len, reverse=True):
        if _location_name_in_text(location_name, normalized_text):
            route_locations.append(location_name)
    unique_locations = tuple(dict.fromkeys(route_locations))
    return tuple(
        location_name
        for location_name in unique_locations
        if not any(
            location_name != other_location_name
            and "".join(location_name.casefold().split())
            in "".join(other_location_name.casefold().split())
            for other_location_name in unique_locations
        )
    )


def _location_name_in_text(location_name: str, normalized_text: str) -> bool:
    if "".join(location_name.casefold().split()) in normalized_text:
        return True
    for alias, alias_location_name in MAP_LOCATION_ALIASES.items():
        if alias_location_name == location_name and alias in normalized_text:
            return True
    return False


def _resolve_phase_zero_structure_name(value: object) -> str | None:
    resolved_name = resolve_structure_name(value)
    if resolved_name is not None:
        return resolved_name
    if type(value) is not str:
        return None
    candidate = value.strip()
    if candidate in EXTRA_PHASE_ZERO_STRUCTURE_NAMES:
        return candidate
    normalized = "".join(candidate.lower().split())
    if normalized == "commandcenter":
        return "Command Center"
    if normalized == "bunker":
        return "Bunker"
    return None


def _normalize_unit_counts(values: Mapping[str, int]) -> dict[str, int]:
    normalized: dict[str, int] = {}
    for raw_name, raw_count in values.items():
        unit_name = resolve_unit_name(raw_name)
        if unit_name is None:
            raise ValueError(f"Unsupported ToyCraft state unit: {raw_name}")
        _validate_non_negative_integer(str(raw_name), raw_count)
        normalized[unit_name] = normalized.get(unit_name, 0) + raw_count
        if normalized[unit_name] == 0:
            normalized.pop(unit_name)
    return normalized


def _normalize_unit_positions(values: Mapping[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for raw_name, raw_position in values.items():
        unit_name = resolve_unit_name(raw_name)
        if unit_name is None:
            raise ValueError(f"Unsupported ToyCraft unit position unit: {raw_name}")
        if type(raw_position) is not str or not raw_position.strip():
            raise ValueError("unit position must be a non-empty string.")
        normalized[unit_name] = raw_position.strip()
    return normalized


def _normalize_target_damage(values: Mapping[str, int]) -> dict[str, int]:
    normalized: dict[str, int] = {}
    for raw_target, raw_damage in values.items():
        if type(raw_target) is not str or not raw_target.strip():
            raise ValueError("target_damage target must be a non-empty string.")
        _validate_non_negative_integer(str(raw_target), raw_damage)
        if raw_damage > 0:
            normalized[raw_target.strip()] = raw_damage
    return normalized


def _normalize_pressure_mitigation(values: Mapping[str, int]) -> dict[str, int]:
    normalized: dict[str, int] = {}
    for raw_target, raw_mitigation in values.items():
        if type(raw_target) is not str or not raw_target.strip():
            raise ValueError("pressure_mitigation target must be a non-empty string.")
        _validate_non_negative_integer(str(raw_target), raw_mitigation)
        if raw_mitigation > 0:
            normalized[raw_target.strip()] = raw_mitigation
    return normalized


def _normalize_structure_counts(values: Mapping[str, int]) -> dict[str, int]:
    normalized: dict[str, int] = {}
    for raw_name, raw_count in values.items():
        structure_name = _resolve_phase_zero_structure_name(raw_name)
        if structure_name is None:
            raise ValueError(f"Unsupported ToyCraft state structure: {raw_name}")
        _validate_non_negative_integer(str(raw_name), raw_count)
        normalized[structure_name] = normalized.get(structure_name, 0) + raw_count
    return normalized


def _normalize_construction_queue(
    values: tuple[ConstructionOrder | Mapping[str, object], ...],
) -> tuple[ConstructionOrder, ...]:
    normalized: list[ConstructionOrder] = []
    for value in values:
        if isinstance(value, ConstructionOrder):
            normalized.append(value)
            continue
        if isinstance(value, Mapping):
            normalized.append(
                ConstructionOrder(
                    structure_name=str(value.get("structure_name", "")),
                    location=str(value.get("location", "")),
                    remaining_seconds=_mapping_int(value, "remaining_seconds"),
                    assigned_workers=_mapping_int(value, "assigned_workers", default=1),
                )
            )
            continue
        raise ValueError("construction_queue values must be ConstructionOrder or mapping.")
    return tuple(normalized)


def _normalize_production_orders(
    values: tuple[ProductionOrder | Mapping[str, object], ...],
) -> tuple[ProductionOrder, ...]:
    normalized: list[ProductionOrder] = []
    for value in values:
        if isinstance(value, ProductionOrder):
            normalized.append(value)
            continue
        if isinstance(value, Mapping):
            normalized.append(
                ProductionOrder(
                    unit_name=str(value.get("unit_name", "")),
                    producer=str(value.get("producer", "")),
                    remaining_seconds=_mapping_int(
                        value,
                        "remaining_seconds",
                        field_label="production_orders",
                    ),
                )
            )
            continue
        raise ValueError("production_orders values must be ProductionOrder or mapping.")
    return tuple(normalized)


def _normalize_location_tuple(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        location_name = resolve_location_name(value)
        if location_name is None:
            raise ValueError(f"Unsupported ToyCraft state location: {value}")
        normalized.append(location_name)
    return tuple(dict.fromkeys(normalized))


def _normalize_target_tuple(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        target = resolve_targetable_position(value)
        if target is None:
            raise ValueError(f"Unsupported ToyCraft state damaged target: {value}")
        normalized.append(target.name)
    return tuple(dict.fromkeys(normalized))


def _validate_non_negative_integer(name: str, value: object) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer.")


def _validate_positive_integer(name: str, value: object) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer.")


def _validate_non_empty_text(name: str, value: object) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")


def _mapping_int(
    value: Mapping[str, object],
    key: str,
    *,
    default: int | None = None,
    field_label: str = "construction_queue",
) -> int:
    if key not in value:
        if default is None:
            raise ValueError(f"{field_label}.{key} is required.")
        return default
    raw_value = value[key]
    if type(raw_value) is not int:
        raise ValueError(f"{field_label}.{key} must be an integer.")
    return raw_value
