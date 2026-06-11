"""Read-only Intent DSL reference resolution against ToyCraft state."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Protocol

from toycraft_commander.intents import (
    BuildStructureIntent,
    DefendIntent,
    ExpandIntent,
    GatherResourceIntent,
    HarassIntent,
    IntentPayload,
    RepairIntent,
    ScoutIntent,
    SummarizeStateIntent,
    TrainArmyIntent,
    TrainWorkerIntent,
)
from toycraft_commander.map import (
    MapLocation,
    TargetablePosition,
    get_resolved_map_location,
    resolve_location_name,
    resolve_targetable_position,
)
from toycraft_commander.resources import get_available_supply
from toycraft_commander.structures import STRUCTURE_NAMES, resolve_structure_name
from toycraft_commander.units import (
    COMBAT_UNIT_NAMES,
    PLAYER_CONTROLLED_UNIT_NAMES,
    UNIT_NAMES,
    get_unit_model,
    resolve_unit_name,
)


PHASE_ZERO_RESOLVABLE_STRUCTURE_NAMES: Final[tuple[str, ...]] = (
    *STRUCTURE_NAMES,
    "Bunker",
    "Command Center",
)
"""Structure names the Phase 0 state resolver can canonicalize."""

COMBAT_UNIT_GROUP_NAMES: Final[tuple[str, ...]] = tuple(
    unit_name for unit_name in ("Marine", "Vulture") if unit_name in COMBAT_UNIT_NAMES
)
"""Player combat units selectable by aggregate combat group references."""


class ToyCraftStateView(Protocol):
    """Read-only state surface required by the resolver boundary."""

    resources: object
    supply: object
    claimed_locations: tuple[str, ...]
    damaged_targets: tuple[str, ...]
    unit_positions: Mapping[str, str]

    def unit_count(self, unit_name: object) -> int:
        """Return count for a canonical or aliased unit name."""

    def available_worker_count(self) -> int:
        """Return currently unreserved workers."""

    def structure_count(self, structure_name: object) -> int:
        """Return completed structure count."""

    def available_producer_count(self, producer_name: object) -> int:
        """Return idle producer count."""

    def available_production_queue_slots(self, producer_name: object) -> int:
        """Return open production queue slots."""

    def construction_count(self, structure_name: object) -> int:
        """Return in-progress construction count for a structure."""


@dataclass(frozen=True)
class ResolvedStateReference:
    """One Intent DSL reference resolved against the current ToyCraft state."""

    field_name: str
    kind: str
    requested: object
    canonical_name: str | None
    available: bool
    reason: str = ""
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.field_name.strip():
            raise ValueError("field_name must be non-empty.")
        if not self.kind.strip():
            raise ValueError("kind must be non-empty.")
        if self.available and self.canonical_name is None:
            raise ValueError("available references require canonical_name.")
        if not self.available and not self.reason.strip():
            raise ValueError("unavailable references require reason.")
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready resolution record."""

        payload: dict[str, object] = {
            "field_name": self.field_name,
            "kind": self.kind,
            "requested": self.requested,
            "canonical_name": self.canonical_name,
            "available": self.available,
            "metadata": dict(self.metadata),
        }
        if self.reason:
            payload["reason"] = self.reason
        return payload


@dataclass(frozen=True)
class ResolvedUnitGroup:
    """State-aware resolution for a unit_group DSL field."""

    requested: str
    unit_name: str | None
    available_count: int
    selected_count: int
    requested_count: int | None = None
    combat_capable: bool = False
    player_controlled: bool = False
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.requested.strip():
            raise ValueError("requested unit group must be non-empty.")
        if self.unit_name is None and not self.reason.strip():
            raise ValueError("unresolved unit groups require reason.")
        if self.available_count < 0:
            raise ValueError("available_count must be non-negative.")
        if self.selected_count < 0:
            raise ValueError("selected_count must be non-negative.")
        if self.requested_count is not None and self.requested_count < 1:
            raise ValueError("requested_count must be positive when present.")

    @property
    def available(self) -> bool:
        """Return whether this unit group can select at least one unit."""

        return self.unit_name is not None and self.selected_count > 0

    def to_reference(self, field_name: str = "unit_group") -> ResolvedStateReference:
        """Return the unit group as a generic state reference record."""

        metadata: dict[str, object] = {
            "available_count": self.available_count,
            "selected_count": self.selected_count,
            "combat_capable": self.combat_capable,
            "player_controlled": self.player_controlled,
        }
        if self.requested_count is not None:
            metadata["requested_count"] = self.requested_count
        return ResolvedStateReference(
            field_name=field_name,
            kind="unit_group",
            requested=self.requested,
            canonical_name=self.unit_name,
            available=self.available,
            reason=self.reason,
            metadata=metadata,
        )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready unit-group resolution record."""

        payload: dict[str, object] = {
            "requested": self.requested,
            "unit_name": self.unit_name,
            "available_count": self.available_count,
            "selected_count": self.selected_count,
            "available": self.available,
            "combat_capable": self.combat_capable,
            "player_controlled": self.player_controlled,
        }
        if self.requested_count is not None:
            payload["requested_count"] = self.requested_count
        if self.reason:
            payload["reason"] = self.reason
        return payload


@dataclass(frozen=True)
class IntentStateResolution:
    """Complete read-only state resolution for one typed Intent DSL payload."""

    intent: str
    references: tuple[ResolvedStateReference, ...] = ()
    unit_group: ResolvedUnitGroup | None = None
    resource_snapshot: Mapping[str, int] = field(default_factory=dict)
    supply_snapshot: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "references", tuple(self.references))
        object.__setattr__(self, "resource_snapshot", dict(self.resource_snapshot))
        object.__setattr__(self, "supply_snapshot", dict(self.supply_snapshot))

    @property
    def all_references_available(self) -> bool:
        """Return whether every referenced DSL object exists in current state."""

        return all(reference.available for reference in self.references)

    @property
    def unresolved_references(self) -> tuple[ResolvedStateReference, ...]:
        """Return references that failed state resolution."""

        return tuple(reference for reference in self.references if not reference.available)

    def get_reference(self, field_name: str) -> ResolvedStateReference | None:
        """Return the first reference record for a DSL field."""

        return next(
            (
                reference
                for reference in self.references
                if reference.field_name == field_name
            ),
            None,
        )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready intent resolution snapshot."""

        return {
            "intent": self.intent,
            "references": [reference.to_dict() for reference in self.references],
            "unit_group": self.unit_group.to_dict() if self.unit_group else None,
            "resource_snapshot": dict(self.resource_snapshot),
            "supply_snapshot": dict(self.supply_snapshot),
            "all_references_available": self.all_references_available,
        }


def resolve_intent_state_references(
    payload: IntentPayload,
    state: ToyCraftStateView,
) -> IntentStateResolution:
    """Resolve every state reference named by one typed Intent DSL payload."""

    references: list[ResolvedStateReference] = []
    unit_group: ResolvedUnitGroup | None = None

    if isinstance(payload, GatherResourceIntent):
        references.append(resolve_resource_reference(payload.resource, state))
        references.append(resolve_base_reference(payload.base, state, "base"))
    elif isinstance(payload, BuildStructureIntent):
        references.append(resolve_structure_reference(payload.structure, state))
        references.append(resolve_location_reference(payload.location, state, "location"))
    elif isinstance(payload, TrainWorkerIntent):
        references.append(resolve_producer_reference("Command Center", state))
    elif isinstance(payload, TrainArmyIntent):
        unit_reference = resolve_unit_type_reference(payload.unit_type, state)
        references.append(unit_reference)
        if unit_reference.available:
            producer = str(unit_reference.metadata["producer"])
            references.append(resolve_producer_reference(producer, state))
    elif isinstance(payload, ScoutIntent):
        references.append(resolve_target_reference(payload.target, state, "target"))
        unit_group = resolve_unit_group_reference(payload.unit_group, state)
        references.append(unit_group.to_reference())
    elif isinstance(payload, SummarizeStateIntent):
        pass
    elif isinstance(payload, DefendIntent):
        references.append(resolve_location_reference(payload.location, state, "location"))
        unit_group = resolve_unit_group_reference(payload.unit_group, state)
        references.append(unit_group.to_reference())
    elif isinstance(payload, RepairIntent):
        references.append(resolve_repair_target_reference(payload.target, state))
        references.append(resolve_worker_reference(payload.worker_count, state))
    elif isinstance(payload, ExpandIntent):
        references.append(resolve_location_reference(payload.location, state, "location"))
    elif isinstance(payload, HarassIntent):
        references.append(resolve_target_reference(payload.target, state, "target"))
        unit_group = resolve_unit_group_reference(payload.unit_group, state)
        references.append(unit_group.to_reference())
    else:
        raise TypeError("payload must be a supported Intent DSL payload.")

    return IntentStateResolution(
        intent=payload.intent,
        references=tuple(references),
        unit_group=unit_group,
        resource_snapshot=_resource_snapshot(state),
        supply_snapshot=_supply_snapshot(state),
    )


def resolve_resource_reference(
    resource_name: str,
    state: ToyCraftStateView,
    field_name: str = "resource",
) -> ResolvedStateReference:
    """Resolve a resource field and include current available amount."""

    amount = getattr(state.resources, resource_name, None)
    if type(amount) is not int:
        return ResolvedStateReference(
            field_name=field_name,
            kind="resource",
            requested=resource_name,
            canonical_name=None,
            available=False,
            reason=f"{resource_name!r} is not tracked by ToyCraft resources.",
        )
    return ResolvedStateReference(
        field_name=field_name,
        kind="resource",
        requested=resource_name,
        canonical_name=resource_name,
        available=True,
        metadata={"amount": amount},
    )


def resolve_base_reference(
    base_name: object,
    state: ToyCraftStateView,
    field_name: str = "base",
) -> ResolvedStateReference:
    """Resolve a friendly claimed base reference."""

    location = _resolve_map_location(base_name)
    if location is None:
        return _unresolved_location_reference(base_name, field_name)
    if location.kind != "base":
        return ResolvedStateReference(
            field_name=field_name,
            kind="base",
            requested=base_name,
            canonical_name=location.name,
            available=False,
            reason=f"{location.name} is not a base location.",
            metadata=_location_metadata(location),
        )
    if location.name not in state.claimed_locations:
        return ResolvedStateReference(
            field_name=field_name,
            kind="base",
            requested=base_name,
            canonical_name=location.name,
            available=False,
            reason=f"{location.name} is not currently claimed.",
            metadata=_location_metadata(location),
        )
    return ResolvedStateReference(
        field_name=field_name,
        kind="base",
        requested=base_name,
        canonical_name=location.name,
        available=True,
        metadata={**_location_metadata(location), "claimed": True},
    )


def resolve_location_reference(
    location_name: object,
    state: ToyCraftStateView,
    field_name: str = "location",
) -> ResolvedStateReference:
    """Resolve a map location and annotate ownership in the current state."""

    location = _resolve_map_location(location_name)
    if location is None:
        return _unresolved_location_reference(location_name, field_name)
    metadata = _location_metadata(location)
    metadata["claimed"] = location.name in state.claimed_locations
    metadata["occupied_by"] = tuple(
        unit_name
        for unit_name, unit_location in state.unit_positions.items()
        if unit_location == location.name
    )
    return ResolvedStateReference(
        field_name=field_name,
        kind="location",
        requested=location_name,
        canonical_name=location.name,
        available=True,
        metadata=metadata,
    )


def resolve_target_reference(
    target_name: object,
    state: ToyCraftStateView,
    field_name: str = "target",
) -> ResolvedStateReference:
    """Resolve a targetable map position and annotate current target state."""

    target = resolve_targetable_position(target_name)
    if target is None:
        return ResolvedStateReference(
            field_name=field_name,
            kind="target",
            requested=target_name,
            canonical_name=None,
            available=False,
            reason=f"{target_name!r} is not a known targetable ToyCraft position.",
        )
    metadata = _target_metadata(target)
    metadata["damaged"] = target.name in state.damaged_targets
    return ResolvedStateReference(
        field_name=field_name,
        kind="target",
        requested=target_name,
        canonical_name=target.name,
        available=True,
        metadata=metadata,
    )


def resolve_repair_target_reference(
    target_name: object,
    state: ToyCraftStateView,
    field_name: str = "target",
) -> ResolvedStateReference:
    """Resolve a target that must be currently repairable."""

    reference = resolve_target_reference(target_name, state, field_name)
    if not reference.available:
        return reference
    if reference.metadata.get("kind") != "repair_target":
        return ResolvedStateReference(
            field_name=field_name,
            kind="repair_target",
            requested=target_name,
            canonical_name=reference.canonical_name,
            available=False,
            reason=f"{reference.canonical_name} is not a repair target.",
            metadata=reference.metadata,
        )
    if reference.canonical_name not in state.damaged_targets:
        return ResolvedStateReference(
            field_name=field_name,
            kind="repair_target",
            requested=target_name,
            canonical_name=reference.canonical_name,
            available=False,
            reason=f"{reference.canonical_name} is not currently damaged.",
            metadata=reference.metadata,
        )
    return ResolvedStateReference(
        field_name=field_name,
        kind="repair_target",
        requested=target_name,
        canonical_name=reference.canonical_name,
        available=True,
        metadata=reference.metadata,
    )


def resolve_structure_reference(
    structure_name: object,
    state: ToyCraftStateView,
    field_name: str = "structure",
) -> ResolvedStateReference:
    """Resolve a structure name and include current completed/construction counts."""

    canonical_name = resolve_phase_zero_structure_name(structure_name)
    if canonical_name is None:
        return ResolvedStateReference(
            field_name=field_name,
            kind="structure",
            requested=structure_name,
            canonical_name=None,
            available=False,
            reason=f"{structure_name!r} is not a Phase 0 ToyCraft structure.",
        )
    return ResolvedStateReference(
        field_name=field_name,
        kind="structure",
        requested=structure_name,
        canonical_name=canonical_name,
        available=True,
        metadata={
            "completed_count": state.structure_count(canonical_name),
            "construction_count": state.construction_count(canonical_name),
        },
    )


def resolve_producer_reference(
    producer_name: object,
    state: ToyCraftStateView,
    field_name: str = "producer",
) -> ResolvedStateReference:
    """Resolve a production structure and include availability/queue slots."""

    reference = resolve_structure_reference(producer_name, state, field_name)
    if not reference.available:
        return reference
    canonical_name = str(reference.canonical_name)
    completed_count = state.structure_count(canonical_name)
    return ResolvedStateReference(
        field_name=field_name,
        kind="producer",
        requested=producer_name,
        canonical_name=canonical_name,
        available=completed_count > 0,
        reason="" if completed_count > 0 else f"No completed {canonical_name} is available.",
        metadata={
            **reference.metadata,
            "available_count": state.available_producer_count(canonical_name),
            "queue_slots": state.available_production_queue_slots(canonical_name),
        },
    )


def resolve_unit_type_reference(
    unit_name: object,
    state: ToyCraftStateView,
    field_name: str = "unit_type",
) -> ResolvedStateReference:
    """Resolve a unit type and include current count and producer."""

    canonical_name = resolve_unit_name(unit_name)
    if canonical_name is None:
        return ResolvedStateReference(
            field_name=field_name,
            kind="unit",
            requested=unit_name,
            canonical_name=None,
            available=False,
            reason=f"{unit_name!r} is not a known ToyCraft unit.",
        )
    unit_model = get_unit_model(canonical_name)
    return ResolvedStateReference(
        field_name=field_name,
        kind="unit",
        requested=unit_name,
        canonical_name=canonical_name,
        available=True,
        metadata={
            "current_count": state.unit_count(canonical_name),
            "producer": unit_model.producer,
            "combat_capable": canonical_name in COMBAT_UNIT_NAMES,
            "player_controlled": canonical_name in PLAYER_CONTROLLED_UNIT_NAMES,
        },
    )


def resolve_worker_reference(
    worker_count: int,
    state: ToyCraftStateView,
    field_name: str = "worker_count",
) -> ResolvedStateReference:
    """Resolve an SCV worker-count request against current free workers."""

    available_workers = state.available_worker_count()
    return ResolvedStateReference(
        field_name=field_name,
        kind="unit",
        requested=worker_count,
        canonical_name="SCV",
        available=worker_count <= available_workers,
        reason=(
            ""
            if worker_count <= available_workers
            else f"{worker_count} SCV(s) requested but only {available_workers} are free."
        ),
        metadata={
            "requested_count": worker_count,
            "available_count": available_workers,
            "selected_count": min(worker_count, available_workers),
            "player_controlled": True,
        },
    )


def resolve_unit_group_reference(
    unit_group: str,
    state: ToyCraftStateView,
) -> ResolvedUnitGroup:
    """Resolve a unit_group phrase into a concrete selectable state group."""

    normalized = unit_group.strip().lower()
    requested_count = _extract_leading_count(normalized)

    if normalized in {"available combat units", "all combat units"}:
        combat_counts = {
            unit_name: state.unit_count(unit_name)
            for unit_name in COMBAT_UNIT_GROUP_NAMES
            if state.unit_count(unit_name) > 0
        }
        if not combat_counts:
            return ResolvedUnitGroup(
                requested=unit_group,
                unit_name=None,
                available_count=0,
                selected_count=0,
                combat_capable=True,
                player_controlled=True,
                reason="No available combat units are present in state.",
            )
        unit_name = max(combat_counts, key=combat_counts.get)
        return _resolved_unit_group(
            unit_group,
            unit_name,
            combat_counts[unit_name],
            combat_counts[unit_name],
            requested_count=None,
        )

    mentioned_unit_name = _mentioned_unit_name(unit_group)
    if mentioned_unit_name is not None:
        available_count = state.unit_count(mentioned_unit_name)
        selected_count = available_count
        if requested_count is not None:
            selected_count = min(requested_count, available_count)
        reason = ""
        if mentioned_unit_name not in PLAYER_CONTROLLED_UNIT_NAMES:
            reason = f"{mentioned_unit_name} is not player-controlled in Phase 0."
            selected_count = 0
        elif available_count <= 0:
            reason = f"No available {mentioned_unit_name} units are present in state."
        elif requested_count is not None and requested_count > available_count:
            reason = (
                f"{requested_count} {mentioned_unit_name}(s) requested but only "
                f"{available_count} are available."
            )
            selected_count = 0
        return _resolved_unit_group(
            unit_group,
            mentioned_unit_name,
            available_count,
            selected_count,
            requested_count=requested_count,
            reason=reason,
        )

    return ResolvedUnitGroup(
        requested=unit_group,
        unit_name=None,
        available_count=0,
        selected_count=0,
        requested_count=requested_count,
        reason=f"{unit_group!r} does not name a supported ToyCraft unit group.",
    )


def resolve_phase_zero_structure_name(value: object) -> str | None:
    """Return the canonical Phase 0 structure name for raw DSL text."""

    resolved_name = resolve_structure_name(value)
    if resolved_name is not None:
        return resolved_name
    if type(value) is not str:
        return None
    candidate = value.strip()
    if candidate in PHASE_ZERO_RESOLVABLE_STRUCTURE_NAMES:
        return candidate
    normalized = "".join(candidate.casefold().split())
    if normalized == "commandcenter":
        return "Command Center"
    if normalized in {"bunker", "벙커"}:
        return "Bunker"
    return None


def _resolved_unit_group(
    requested: str,
    unit_name: str,
    available_count: int,
    selected_count: int,
    *,
    requested_count: int | None,
    reason: str = "",
) -> ResolvedUnitGroup:
    return ResolvedUnitGroup(
        requested=requested,
        unit_name=unit_name,
        available_count=available_count,
        selected_count=selected_count,
        requested_count=requested_count,
        combat_capable=unit_name in COMBAT_UNIT_NAMES,
        player_controlled=unit_name in PLAYER_CONTROLLED_UNIT_NAMES,
        reason=reason,
    )


def _resolve_map_location(value: object) -> MapLocation | None:
    if resolve_location_name(value) is None:
        return None
    return get_resolved_map_location(value)


def _unresolved_location_reference(
    location_name: object,
    field_name: str,
) -> ResolvedStateReference:
    return ResolvedStateReference(
        field_name=field_name,
        kind="location",
        requested=location_name,
        canonical_name=None,
        available=False,
        reason=f"{location_name!r} is not a known ToyCraft location.",
    )


def _location_metadata(location: MapLocation) -> dict[str, object]:
    return {
        "kind": location.kind,
        "tile": location.tile.to_dict(),
        "targetable": location.targetable,
        "description": location.description,
    }


def _target_metadata(target: TargetablePosition) -> dict[str, object]:
    return {
        "kind": target.kind,
        "tile": target.tile.to_dict(),
        "description": target.description,
    }


def _resource_snapshot(state: ToyCraftStateView) -> dict[str, int]:
    resources = state.resources
    return {
        "minerals": getattr(resources, "minerals", 0),
        "gas": getattr(resources, "gas", 0),
    }


def _supply_snapshot(state: ToyCraftStateView) -> dict[str, int]:
    supply = state.supply
    return {
        "used_supply": getattr(supply, "used_supply", 0),
        "supply_capacity": getattr(supply, "supply_capacity", 0),
        "available_supply": get_available_supply(supply),
    }


def _extract_leading_count(value: str) -> int | None:
    match = re.match(r"\s*(\d+)\b", value)
    if match is None:
        return None
    return int(match.group(1))


def _mentioned_unit_name(unit_group: str) -> str | None:
    normalized = unit_group.strip().lower()
    direct_match = resolve_unit_name(normalized)
    if direct_match is not None:
        return direct_match
    words = re.sub(r"[^0-9a-zA-Z가-힣 ]+", " ", normalized).split()
    for word in words:
        unit_name = resolve_unit_name(word)
        if unit_name is not None:
            return unit_name
    for candidate in UNIT_NAMES:
        if _unit_group_mentions(unit_group, candidate):
            return candidate
    return None


def _unit_group_mentions(unit_group: str, unit_name: str) -> bool:
    normalized = unit_group.strip().lower()
    words = re.sub(r"[^0-9a-zA-Z ]+", " ", normalized).split()
    return any(resolve_unit_name(word) == unit_name for word in words)
