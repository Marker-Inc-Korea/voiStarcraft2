"""Map target lookup helpers for the Phase 0 ToyCraft simulator."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Final, Literal


MapLocationName = Literal[
    "main",
    "main base",
    "main ramp",
    "main geyser",
    "natural approach",
    "natural choke",
    "natural expansion",
    "enemy main",
    "enemy natural",
    "enemy front",
    "enemy mineral line",
    "front bunker",
]
MapLocationKind = Literal[
    "base",
    "ramp",
    "resource",
    "approach",
    "choke",
    "enemy_position",
    "repair_target",
]
TargetablePositionKind = Literal[
    "location",
    "resource",
    "enemy_position",
    "repair_target",
]


@dataclass(frozen=True)
class TilePosition:
    """Integer ToyCraft map tile used by the rule engine boundary."""

    x: int
    y: int

    def __post_init__(self) -> None:
        _validate_non_negative_integer("x", self.x)
        _validate_non_negative_integer("y", self.y)

    def to_dict(self) -> dict[str, int]:
        """Return a JSON-ready tile representation."""

        return asdict(self)


@dataclass(frozen=True)
class MapLocation:
    """Named ToyCraft position that Korean commands may reference."""

    name: MapLocationName
    kind: MapLocationKind
    tile: TilePosition
    targetable: bool
    description: str

    def __post_init__(self) -> None:
        if type(self.targetable) is not bool:
            raise ValueError("targetable must be a boolean.")
        if not self.description.strip():
            raise ValueError("description must be a non-empty string.")

    def to_dict(self) -> dict[str, object]:
        """Return a serialization-friendly map location."""

        return {
            "name": self.name,
            "kind": self.kind,
            "tile": self.tile.to_dict(),
            "targetable": self.targetable,
            "description": self.description,
        }


@dataclass(frozen=True)
class TargetablePosition:
    """Resolved command target with a canonical tile and target kind."""

    name: MapLocationName
    kind: TargetablePositionKind
    tile: TilePosition
    description: str

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError("description must be a non-empty string.")

    def to_dict(self) -> dict[str, object]:
        """Return a serialization-friendly targetable position."""

        return {
            "name": self.name,
            "kind": self.kind,
            "tile": self.tile.to_dict(),
            "description": self.description,
        }


def _validate_non_negative_integer(name: str, value: object) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer.")


def _targetable_kind_for_location(location: MapLocation) -> TargetablePositionKind:
    if location.kind == "resource":
        return "resource"
    if location.kind == "enemy_position":
        return "enemy_position"
    if location.kind == "repair_target":
        return "repair_target"
    return "location"


MAP_LOCATIONS: Final[tuple[MapLocation, ...]] = (
    MapLocation(
        name="main",
        kind="base",
        tile=TilePosition(20, 20),
        targetable=True,
        description="Player starting Command Center area.",
    ),
    MapLocation(
        name="main base",
        kind="base",
        tile=TilePosition(24, 24),
        targetable=True,
        description="Player buildable main base area.",
    ),
    MapLocation(
        name="main ramp",
        kind="ramp",
        tile=TilePosition(34, 30),
        targetable=True,
        description="Player ramp and wall-off position for early defense.",
    ),
    MapLocation(
        name="main geyser",
        kind="resource",
        tile=TilePosition(18, 27),
        targetable=True,
        description="Player main-base vespene geyser for Refinery commands.",
    ),
    MapLocation(
        name="natural approach",
        kind="approach",
        tile=TilePosition(48, 42),
        targetable=True,
        description="Path between the main ramp and natural expansion.",
    ),
    MapLocation(
        name="natural choke",
        kind="choke",
        tile=TilePosition(56, 45),
        targetable=True,
        description="Forward defensive choke near the natural expansion.",
    ),
    MapLocation(
        name="natural expansion",
        kind="base",
        tile=TilePosition(64, 48),
        targetable=True,
        description="First expansion location for a future Command Center.",
    ),
    MapLocation(
        name="enemy main",
        kind="enemy_position",
        tile=TilePosition(140, 112),
        targetable=True,
        description="Likely enemy starting base scout target.",
    ),
    MapLocation(
        name="enemy natural",
        kind="enemy_position",
        tile=TilePosition(122, 96),
        targetable=True,
        description="Likely enemy first expansion scout target.",
    ),
    MapLocation(
        name="enemy front",
        kind="enemy_position",
        tile=TilePosition(112, 88),
        targetable=True,
        description="Enemy defensive front for scouting and harassment pressure.",
    ),
    MapLocation(
        name="enemy mineral line",
        kind="enemy_position",
        tile=TilePosition(132, 104),
        targetable=True,
        description="Enemy worker line used for harassment commands.",
    ),
    MapLocation(
        name="front bunker",
        kind="repair_target",
        tile=TilePosition(52, 44),
        targetable=True,
        description="Forward bunker position that SCVs may repair.",
    ),
)

MAP_LOCATION_NAMES: Final[tuple[MapLocationName, ...]] = tuple(
    location.name for location in MAP_LOCATIONS
)
MAP_LOCATION_BY_NAME: Final[dict[MapLocationName, MapLocation]] = {
    location.name: location for location in MAP_LOCATIONS
}
MAP_LOCATION_ALIASES: Final[dict[str, MapLocationName]] = {
    "main": "main",
    "본진": "main base",
    "본진기지": "main base",
    "메인": "main base",
    "mainbase": "main base",
    "입구": "main ramp",
    "본진입구": "main ramp",
    "램프": "main ramp",
    "언덕": "main ramp",
    "ramp": "main ramp",
    "mainramp": "main ramp",
    "가스": "main geyser",
    "본진가스": "main geyser",
    "가스통": "main geyser",
    "geyser": "main geyser",
    "maingeyser": "main geyser",
    "앞마당가는길": "natural approach",
    "앞마당쪽": "natural approach",
    "naturalapproach": "natural approach",
    "앞마당입구": "natural choke",
    "앞마당초크": "natural choke",
    "초크": "natural choke",
    "naturalchoke": "natural choke",
    "내앞마당": "natural expansion",
    "앞마당": "natural expansion",
    "멀티": "natural expansion",
    "확장": "natural expansion",
    "natural": "natural expansion",
    "naturalexpansion": "natural expansion",
    "적본진": "enemy main",
    "상대본진": "enemy main",
    "enemymain": "enemy main",
    "상대앞마당": "enemy natural",
    "적앞마당": "enemy natural",
    "enemynatural": "enemy natural",
    "적입구": "enemy front",
    "상대입구": "enemy front",
    "enemyfront": "enemy front",
    "적미네랄라인": "enemy mineral line",
    "상대일꾼라인": "enemy mineral line",
    "상대미네랄": "enemy mineral line",
    "enemymineralline": "enemy mineral line",
    "전방벙커": "front bunker",
    "앞벙커": "front bunker",
    "frontbunker": "front bunker",
    "mainbasefallback": "main base",
}
TARGETABLE_POSITION_NAMES: Final[tuple[MapLocationName, ...]] = tuple(
    location.name for location in MAP_LOCATIONS if location.targetable
)
TARGETABLE_POSITION_BY_NAME: Final[dict[MapLocationName, TargetablePosition]] = {
    location.name: TargetablePosition(
        name=location.name,
        kind=_targetable_kind_for_location(location),
        tile=location.tile,
        description=location.description,
    )
    for location in MAP_LOCATIONS
    if location.targetable
}


def resolve_location_name(value: object) -> MapLocationName | None:
    """Return a canonical map location name for English or Korean raw input."""

    if type(value) is not str:
        return None
    candidate = value.strip()
    if candidate in MAP_LOCATION_BY_NAME:
        return candidate
    normalized_candidate = _normalize_lookup_key(candidate)
    return MAP_LOCATION_ALIASES.get(normalized_candidate)


def get_map_location(name: MapLocationName) -> MapLocation:
    """Return a canonical ToyCraft map location by name."""

    try:
        return MAP_LOCATION_BY_NAME[name]
    except KeyError as exc:
        raise KeyError(f"Unsupported ToyCraft map location: {name}") from exc


def get_resolved_map_location(value: object) -> MapLocation:
    """Return a map location after normalizing raw validator or interpreter text."""

    location_name = resolve_location_name(value)
    if location_name is None:
        raise KeyError(f"Unsupported ToyCraft map location: {value}")
    return get_map_location(location_name)


def get_location_tile(value: object) -> TilePosition:
    """Return the canonical tile for a named map location."""

    return get_resolved_map_location(value).tile


def resolve_tile_position(value: object) -> TilePosition | None:
    """Return a tile from a TilePosition, tuple/list, mapping, or named location."""

    if isinstance(value, TilePosition):
        return value
    if type(value) is tuple or type(value) is list:
        if len(value) != 2:
            return None
        x, y = value
        if type(x) is int and type(y) is int and x >= 0 and y >= 0:
            return TilePosition(x, y)
        return None
    if isinstance(value, Mapping):
        x = value.get("x")
        y = value.get("y")
        if type(x) is int and type(y) is int and x >= 0 and y >= 0:
            return TilePosition(x, y)
        return None
    location_name = resolve_location_name(value)
    if location_name is None:
        return None
    return get_map_location(location_name).tile


def resolve_targetable_position(value: object) -> TargetablePosition | None:
    """Return a canonical targetable map position, or None for unsupported input."""

    location_name = resolve_location_name(value)
    if location_name is None:
        return None
    return TARGETABLE_POSITION_BY_NAME.get(location_name)


def get_targetable_position(value: object) -> TargetablePosition:
    """Return a targetable position or raise for invalid command targets."""

    target = resolve_targetable_position(value)
    if target is None:
        raise KeyError(f"Unsupported ToyCraft targetable position: {value}")
    return target


def get_target_tile(value: object) -> TilePosition:
    """Return the canonical tile for a targetable command position."""

    return get_targetable_position(value).tile


def is_supported_location_name(value: object) -> bool:
    """Return whether raw text names a known ToyCraft map location."""

    return resolve_location_name(value) is not None


def is_targetable_position(value: object) -> bool:
    """Return whether raw text names a location valid for command targeting."""

    return resolve_targetable_position(value) is not None


def get_map_locations_by_kind(kind: MapLocationKind) -> tuple[MapLocation, ...]:
    """Return stable ordered map locations with the requested kind."""

    locations = tuple(location for location in MAP_LOCATIONS if location.kind == kind)
    if not locations:
        raise KeyError(f"Unsupported ToyCraft map location kind: {kind}")
    return locations


def _normalize_lookup_key(value: str) -> str:
    return "".join(value.casefold().split())
