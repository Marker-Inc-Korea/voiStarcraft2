"""Auditable building placement constraints for Commander safety layers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from toycraft_commander.map import MapLocationKind, MapLocationName


PlacementAnchorType = Literal[
    "buildable_ground",
    "expansion_base",
    "vespene_geyser",
]
ResourceAdjacencyRule = Literal[
    "none",
    "avoid_resource_line",
    "requires_base_resource_cluster",
    "requires_free_geyser",
]
BuildabilityRule = Literal[
    "requires_buildable_terrain",
    "requires_townhall_placement",
    "requires_geyser_resource",
]


@dataclass(frozen=True)
class BuildingFootprint:
    """Tile footprint occupied by one structure placement."""

    width: int
    height: int

    def __post_init__(self) -> None:
        if type(self.width) is not int or self.width <= 0:
            raise ValueError("width must be a positive integer.")
        if type(self.height) is not int or self.height <= 0:
            raise ValueError("height must be a positive integer.")

    def to_dict(self) -> dict[str, int]:
        """Return a JSON-ready footprint payload."""

        return {"width": self.width, "height": self.height}


@dataclass(frozen=True)
class PlacementClearanceRules:
    """Static clearance checks required before choosing a build tile."""

    require_unoccupied_footprint: bool = True
    avoid_mineral_line_overlap: bool = False
    avoid_geyser_overlap: bool = False
    require_unclaimed_base: bool = False
    require_free_resource_node: bool = False
    min_tiles_from_townhall: float = 0.0
    min_tiles_from_resources: float = 0.0
    allow_wall_anchor: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "require_unoccupied_footprint",
            "avoid_mineral_line_overlap",
            "avoid_geyser_overlap",
            "require_unclaimed_base",
            "require_free_resource_node",
            "allow_wall_anchor",
        ):
            if type(getattr(self, field_name)) is not bool:
                raise ValueError(f"{field_name} must be a boolean.")
        for field_name in ("min_tiles_from_townhall", "min_tiles_from_resources"):
            value = getattr(self, field_name)
            if type(value) not in (int, float) or value < 0.0:
                raise ValueError(f"{field_name} must be a non-negative number.")

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready clearance contract."""

        return {
            "require_unoccupied_footprint": self.require_unoccupied_footprint,
            "avoid_mineral_line_overlap": self.avoid_mineral_line_overlap,
            "avoid_geyser_overlap": self.avoid_geyser_overlap,
            "require_unclaimed_base": self.require_unclaimed_base,
            "require_free_resource_node": self.require_free_resource_node,
            "min_tiles_from_townhall": float(self.min_tiles_from_townhall),
            "min_tiles_from_resources": float(self.min_tiles_from_resources),
            "allow_wall_anchor": self.allow_wall_anchor,
        }


@dataclass(frozen=True)
class BuildingPlacementConstraint:
    """Per-building placement contract shared by validation and adapters."""

    structure_name: str
    footprint: BuildingFootprint
    required_anchor_type: PlacementAnchorType
    allowed_location_names: tuple[MapLocationName, ...]
    allowed_location_kinds: tuple[MapLocationKind, ...]
    buildability_rule: BuildabilityRule
    resource_adjacency: ResourceAdjacencyRule
    clearance: PlacementClearanceRules

    def __post_init__(self) -> None:
        structure_name = self.structure_name.strip()
        if not structure_name:
            raise ValueError("structure_name must be a non-empty string.")
        if not self.allowed_location_names:
            raise ValueError("allowed_location_names must not be empty.")
        if not self.allowed_location_kinds:
            raise ValueError("allowed_location_kinds must not be empty.")
        object.__setattr__(self, "structure_name", structure_name)
        object.__setattr__(
            self,
            "allowed_location_names",
            tuple(self.allowed_location_names),
        )
        object.__setattr__(
            self,
            "allowed_location_kinds",
            tuple(self.allowed_location_kinds),
        )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready placement constraint payload."""

        return {
            "structure_name": self.structure_name,
            "footprint": self.footprint.to_dict(),
            "required_anchor_type": self.required_anchor_type,
            "allowed_location_names": list(self.allowed_location_names),
            "allowed_location_kinds": list(self.allowed_location_kinds),
            "buildability_rule": self.buildability_rule,
            "resource_adjacency": self.resource_adjacency,
            "clearance": self.clearance.to_dict(),
        }


SUPPLY_DEPOT_PLACEMENT_CONSTRAINT: Final[BuildingPlacementConstraint] = (
    BuildingPlacementConstraint(
        structure_name="Supply Depot",
        footprint=BuildingFootprint(width=2, height=2),
        required_anchor_type="buildable_ground",
        allowed_location_names=("main", "main base", "main ramp"),
        allowed_location_kinds=("base", "ramp"),
        buildability_rule="requires_buildable_terrain",
        resource_adjacency="avoid_resource_line",
        clearance=PlacementClearanceRules(
            avoid_mineral_line_overlap=True,
            avoid_geyser_overlap=True,
            min_tiles_from_townhall=2.0,
            min_tiles_from_resources=1.0,
            allow_wall_anchor=True,
        ),
    )
)
COMMAND_CENTER_PLACEMENT_CONSTRAINT: Final[BuildingPlacementConstraint] = (
    BuildingPlacementConstraint(
        structure_name="Command Center",
        footprint=BuildingFootprint(width=5, height=5),
        required_anchor_type="expansion_base",
        allowed_location_names=("main", "main base", "natural expansion"),
        allowed_location_kinds=("base",),
        buildability_rule="requires_townhall_placement",
        resource_adjacency="requires_base_resource_cluster",
        clearance=PlacementClearanceRules(
            avoid_mineral_line_overlap=True,
            avoid_geyser_overlap=True,
            require_unclaimed_base=True,
            min_tiles_from_resources=4.0,
        ),
    )
)
REFINERY_PLACEMENT_CONSTRAINT: Final[BuildingPlacementConstraint] = (
    BuildingPlacementConstraint(
        structure_name="Refinery",
        footprint=BuildingFootprint(width=3, height=3),
        required_anchor_type="vespene_geyser",
        allowed_location_names=("main geyser",),
        allowed_location_kinds=("resource",),
        buildability_rule="requires_geyser_resource",
        resource_adjacency="requires_free_geyser",
        clearance=PlacementClearanceRules(
            require_free_resource_node=True,
            min_tiles_from_resources=0.0,
        ),
    )
)

BUILD_PLACEMENT_CONSTRAINTS: Final[tuple[BuildingPlacementConstraint, ...]] = (
    SUPPLY_DEPOT_PLACEMENT_CONSTRAINT,
    COMMAND_CENTER_PLACEMENT_CONSTRAINT,
    REFINERY_PLACEMENT_CONSTRAINT,
)
BUILD_PLACEMENT_CONSTRAINTS_BY_STRUCTURE: Final[
    dict[str, BuildingPlacementConstraint]
] = {
    constraint.structure_name: constraint for constraint in BUILD_PLACEMENT_CONSTRAINTS
}


def get_build_placement_constraint(
    structure_name: str,
) -> BuildingPlacementConstraint | None:
    """Return the placement contract for a structure when one is defined."""

    return BUILD_PLACEMENT_CONSTRAINTS_BY_STRUCTURE.get(structure_name)
