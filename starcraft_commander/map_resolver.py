"""Real StarCraft II semantic map-target resolver.

This module turns BotAI-like live map data into a fixed registry of semantic
map targets ("self_main", "enemy_natural", ...) resolved to Point2-like
coordinates. It is intentionally importable without StarCraft II or python-sc2:
bot objects are duck-typed via ``getattr`` and never isinstance-checked against
python-sc2 types, so unit tests can use pure-Python fakes.

Resolution is conservative and honest: every semantic target the resolver
cannot derive from the bound bot becomes an explicit unavailable entry with a
human-readable reason, and unknown target names are rejected with the list of
currently available alternatives. ``SC2MapResolver.from_bot`` never raises on
missing or broken bot attributes.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Protocol, runtime_checkable

from starcraft_commander.sc2_executor import SC2_TARGET_ALIASES, resolve_sc2_target_name


SC2_SEMANTIC_TARGETS: Final[tuple[str, ...]] = (
    "self_main",
    "self_ramp",
    "self_natural",
    "enemy_main",
    "enemy_ramp",
    "enemy_front",
    "enemy_natural",
    "enemy_mineral_line",
)
"""Core semantic map targets, in canonical order."""

SC2_EXTRA_SEMANTIC_TARGETS: Final[tuple[str, ...]] = (
    "self_choke",
    "self_third",
    "self_mineral_line",
    "self_geyser",
    "enemy_choke",
    "enemy_third",
    "scout_location",
    "last_seen_enemy_area",
)
"""Best-effort extra semantic targets the planner may emit."""

SC2_SUPPORTED_SEMANTIC_TARGETS: Final[tuple[str, ...]] = (
    SC2_SEMANTIC_TARGETS + SC2_EXTRA_SEMANTIC_TARGETS
)
"""Full supported semantic map-target vocabulary, in canonical order."""

SC2_CANONICAL_TARGET_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    target: tuple(
        alias
        for alias, canonical in sorted(SC2_TARGET_ALIASES.items())
        if canonical == target
    )
    for target in SC2_SUPPORTED_SEMANTIC_TARGETS
}
"""Canonical semantic target names mapped to supported natural-language aliases."""

SC2_MINERAL_LINE_RADIUS: Final[float] = 10.0
"""Maximum distance from a main base for minerals to count as its line."""

SC2_BASE_CLUSTER_RESOURCE_RADIUS: Final[float] = 15.0
"""Maximum distance for minerals/geysers to attach to a base cluster."""

SC2_NEAR_PLACEMENT_RADIUS: Final[float] = 6.0
"""Default search radius for relative ``near`` structure placement anchors."""

SC2_GEOMETRY_VISIBILITY_VALUES: Final[frozenset[str]] = frozenset(
    {"visible", "inferred", "unseen", "unknown"}
)
"""Stable visibility labels for map-geometry inference evidence."""

SC2_GEOMETRY_KIND_VALUES: Final[frozenset[str]] = frozenset(
    {"start_location", "base_cluster", "ramp", "mineral_patch", "geyser"}
)
"""Stable geometry evidence categories exposed to dashboard/debug consumers."""

_MAIN_EXCLUSION_RADIUS: Final[float] = 1.0
"""Expansion entries within this distance of a main are treated as the main."""

_UNDERIVED_TARGET_REASON: Final[str] = (
    "Target was not derived from BotAI map data for this resolver."
)

_ENEMY_TOWNHALL_TYPE_NAMES: Final[frozenset[str]] = frozenset(
    {
        "COMMANDCENTER",
        "ORBITALCOMMAND",
        "PLANETARYFORTRESS",
        "NEXUS",
        "HATCHERY",
        "LAIR",
        "HIVE",
    }
)
"""Enemy structures that safely identify a scouted base anchor."""

_OWN_TOWNHALL_SOURCE: Final[str] = "python-sc2 own townhall observations"
"""Source label for dynamic own-base selectors derived from known townhalls."""

_ENEMY_NATURAL_DISCOVERY_RADIUS: Final[float] = 10.0
"""Maximum distance for a visible enemy townhall to confirm enemy_natural."""

_NATURAL_MIN_DISTANCE: Final[float] = 8.0
"""Minimum main-to-expansion distance for a natural expansion inference."""

_NATURAL_MAX_DISTANCE: Final[float] = 60.0
"""Maximum main-to-expansion distance for a natural expansion inference."""

_NATURAL_AMBIGUITY_MARGIN: Final[float] = 3.0
"""Distance margin below which two natural candidates are considered ambiguous."""

_RAMP_MAX_DISTANCE: Final[float] = 25.0
"""Maximum main-to-ramp distance for safe ramp inference."""

_RAMP_AMBIGUITY_MARGIN: Final[float] = 3.0
"""Distance margin below which two ramp candidates are considered ambiguous."""

_DEFAULT_CATALOG_SOURCE: Final[str] = "python-sc2 observations"

_NEAR_PLACEMENT_STEP: Final[float] = 3.0
"""Preferred near-placement offset from the anchor before widening search."""

_PLACEMENT_OBSTACLE_RADIUS: Final[float] = 1.5
"""Minimum spacing from known base/resource/ramp geometry for candidate points."""

_RESOURCE_GEOMETRY_CATALOG_SOURCE: Final[str] = (
    "python-sc2 validated base/resource geometry"
)

_ENEMY_VISION_CATALOG_SOURCE: Final[str] = (
    "python-sc2 enemy vision/scouting observations"
)

_ENEMY_FRONT_SCOUTING_ATTRS: Final[tuple[str, ...]] = (
    "enemy_ramp",
    "enemy_ramp_location",
    "scouted_enemy_ramp",
    "enemy_front",
    "enemy_front_location",
    "scouted_enemy_front",
    "enemy_front_access_location",
    "scouted_enemy_front_access_location",
)
"""Duck-typed bot fields that can expose a scouted enemy ramp/front point."""

_SCOUT_LOCATION_ATTRS: Final[tuple[str, ...]] = (
    "scout_location",
    "scouted_location",
    "last_scout_location",
    "last_scouted_location",
    "last_scout_position",
)
"""Duck-typed bot fields that expose the latest explicit scout location."""

_LAST_SEEN_ENEMY_AREA_ATTRS: Final[tuple[str, ...]] = (
    "last_seen_enemy_area",
    "last_seen_enemy_position",
    "last_enemy_seen_position",
    "last_enemy_position",
    "recent_enemy_position",
)
"""Duck-typed bot fields that expose the latest known enemy area."""

_ENEMY_MAIN_HISTORY_ATTRS: Final[tuple[str, ...]] = (
    "enemy_main_position",
    "enemy_base_position",
    "opponent_base_position",
    "inferred_enemy_main",
    "inferred_enemy_main_position",
    "inferred_enemy_main_positions",
    "inferred_opponent_base",
    "inferred_opponent_base_position",
    "inferred_opponent_base_positions",
    "last_seen_enemy_main",
    "last_seen_enemy_base",
    "last_scouted_enemy_main",
    "last_scouted_enemy_base",
    "known_enemy_main",
    "known_enemy_base",
)
"""Duck-typed bot fields that expose an inferred or remembered enemy main."""

_LAST_SEEN_ENEMY_STRUCTURE_ATTRS: Final[tuple[str, ...]] = (
    "last_seen_enemy_structures",
    "remembered_enemy_structures",
    "known_enemy_structures",
    "previous_enemy_structures",
)
"""Duck-typed bot fields with remembered enemy structure observations."""

_LAST_SEEN_ENEMY_UNIT_ATTRS: Final[tuple[str, ...]] = (
    "last_seen_enemy_units",
    "remembered_enemy_units",
    "known_enemy_units",
    "previous_enemy_units",
)
"""Duck-typed bot fields with remembered enemy unit observations."""

_ENEMY_CREEP_ATTRS: Final[tuple[str, ...]] = (
    "enemy_creep",
    "enemy_creep_positions",
    "creep_positions",
    "creep_tumors",
    "enemy_creep_tumors",
    "last_seen_creep",
    "last_seen_enemy_creep",
)
"""Duck-typed bot fields that expose enemy-side creep observations."""

_BASE_HISTORY_EXPANSION_MATCH_RADIUS: Final[float] = 18.0
"""Maximum distance for history evidence to snap to a known expansion."""


@dataclass(frozen=True)
class MapPoint:
    """One Point2-like map coordinate with JSON-ready serialization."""

    x: float
    y: float

    def __post_init__(self) -> None:
        for field_name, value in (("x", self.x), ("y", self.y)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"MapPoint {field_name} must be a real number.")
            if not math.isfinite(float(value)):
                raise ValueError(f"MapPoint {field_name} must be finite.")
        object.__setattr__(self, "x", float(self.x))
        object.__setattr__(self, "y", float(self.y))

    def distance_to(self, other: "MapPoint") -> float:
        """Return the Euclidean distance to another map point."""

        return math.hypot(self.x - other.x, self.y - other.y)

    def to_tuple(self) -> tuple[float, float]:
        """Return the ``(x, y)`` coordinate tuple."""

        return (self.x, self.y)

    def to_dict(self) -> dict[str, float]:
        """Return a JSON-ready coordinate payload."""

        return {"x": self.x, "y": self.y}


@dataclass(frozen=True)
class MapTargetResolution:
    """Outcome of resolving one semantic map target name."""

    target: str
    available: bool
    position: MapPoint | None
    reason: str = ""
    reason_code: str = ""
    source: str = ""
    alternatives: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.target).strip():
            raise ValueError("Map target resolution target must be non-empty.")
        source = str(self.source).strip()
        alternatives = tuple(str(item) for item in self.alternatives)
        reason_code = str(self.reason_code).strip()
        if self.available:
            if not isinstance(self.position, MapPoint):
                raise ValueError(
                    "Available map target resolution must carry a MapPoint position."
                )
            if str(self.reason).strip():
                raise ValueError(
                    "Available map target resolution must not carry a reason."
                )
            if alternatives:
                raise ValueError(
                    "Available map target resolution must not carry alternatives."
                )
            if reason_code:
                raise ValueError(
                    "Available map target resolution must not carry a reason code."
                )
        else:
            if self.position is not None:
                raise ValueError(
                    "Unavailable map target resolution must not carry a position."
                )
            if not str(self.reason).strip():
                raise ValueError(
                    "Unavailable map target resolution must carry a non-empty reason."
                )
            if not reason_code:
                reason_code = _map_failure_reason_code(
                    target=str(self.target),
                    reason=str(self.reason),
                    scope="semantic_target",
                )
        object.__setattr__(self, "target", str(self.target))
        object.__setattr__(self, "available", bool(self.available))
        object.__setattr__(self, "reason", str(self.reason))
        object.__setattr__(self, "reason_code", reason_code)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "alternatives", alternatives)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready resolution payload."""

        return {
            "target": self.target,
            "available": self.available,
            "position": self.position.to_dict() if self.position else None,
            "reason": self.reason,
            "reason_code": self.reason_code,
            "source": self.source,
            "alternatives": list(self.alternatives),
        }


@dataclass(frozen=True)
class MapAnchorPositionResolution:
    """Outcome of resolving a placement/camera anchor to a world map point."""

    anchor: str
    available: bool
    position: MapPoint | None
    reason: str = ""
    reason_code: str = ""
    source: str = ""
    target: str = ""
    alternatives: tuple[str, ...] = ()
    placement_policy: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        anchor = str(self.anchor).strip() or "unknown"
        alternatives = tuple(str(item) for item in self.alternatives)
        source = str(self.source).strip()
        target = str(self.target).strip()
        placement_policy = dict(self.placement_policy)
        reason_code = str(self.reason_code).strip()
        if self.available and placement_policy:
            if source:
                placement_policy.setdefault("anchor_source", source)
            if target:
                placement_policy.setdefault("anchor_target", target)
            if self.position is not None:
                placement_policy.setdefault("resolved_position", self.position.to_dict())
        if self.available:
            if not isinstance(self.position, MapPoint):
                raise ValueError(
                    "Available map anchor resolution must carry a MapPoint."
                )
            if str(self.reason).strip():
                raise ValueError(
                    "Available map anchor resolution must not carry a reason."
                )
            if reason_code:
                raise ValueError(
                    "Available map anchor resolution must not carry a reason code."
                )
            if not source:
                raise ValueError(
                    "Available map anchor resolution must carry a source."
                )
        else:
            if self.position is not None:
                raise ValueError(
                    "Unavailable map anchor resolution must not carry a position."
                )
            if not str(self.reason).strip():
                raise ValueError(
                    "Unavailable map anchor resolution must carry a reason."
                )
            if not reason_code:
                reason_code = _map_failure_reason_code(
                    target=target or anchor,
                    reason=str(self.reason),
                    scope="anchor",
                )
        object.__setattr__(self, "anchor", anchor)
        object.__setattr__(self, "available", bool(self.available))
        object.__setattr__(self, "reason", str(self.reason))
        object.__setattr__(self, "reason_code", reason_code)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "alternatives", alternatives)
        object.__setattr__(self, "placement_policy", placement_policy)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready anchor resolution payload."""

        payload: dict[str, object] = {
            "anchor": self.anchor,
            "available": self.available,
            "position": self.position.to_dict() if self.position else None,
            "reason": self.reason,
            "reason_code": self.reason_code,
            "source": self.source,
            "target": self.target,
            "alternatives": list(self.alternatives),
        }
        if self.placement_policy:
            payload["placement_policy"] = dict(self.placement_policy)
        return payload


@dataclass(frozen=True)
class SemanticTargetCatalogEntry:
    """Auditable catalog entry for one canonical semantic map target."""

    target: str
    aliases: tuple[str, ...] = ()
    available: bool = False
    position: MapPoint | None = None
    failure_reason: str = ""
    failure_reason_code: str = ""
    source: str = "python-sc2 observations"

    def __post_init__(self) -> None:
        target = str(self.target)
        if target not in SC2_SUPPORTED_SEMANTIC_TARGETS:
            raise ValueError(f"Unsupported semantic catalog target: {target!r}.")
        aliases = tuple(str(alias) for alias in self.aliases)
        failure_reason_code = str(self.failure_reason_code).strip()
        if self.available:
            if not isinstance(self.position, MapPoint):
                raise ValueError(
                    "Available semantic catalog entry must carry a MapPoint."
                )
            if str(self.failure_reason).strip():
                raise ValueError(
                    "Available semantic catalog entry must not carry a failure reason."
                )
            if failure_reason_code:
                raise ValueError(
                    "Available semantic catalog entry must not carry a failure reason code."
                )
        else:
            if self.position is not None:
                raise ValueError(
                    "Unavailable semantic catalog entry must not carry a position."
                )
            if not str(self.failure_reason).strip():
                raise ValueError(
                    "Unavailable semantic catalog entry must carry a failure reason."
                )
            if not failure_reason_code:
                failure_reason_code = _map_failure_reason_code(
                    target=target,
                    reason=str(self.failure_reason),
                    scope="semantic_target",
                )
        if not str(self.source).strip():
            raise ValueError("Semantic catalog entry source must be non-empty.")
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "aliases", aliases)
        object.__setattr__(self, "failure_reason", str(self.failure_reason))
        object.__setattr__(self, "failure_reason_code", failure_reason_code)
        object.__setattr__(self, "source", str(self.source))

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready semantic target catalog entry."""

        return {
            "target": self.target,
            "aliases": list(self.aliases),
            "available": self.available,
            "position": self.position.to_dict() if self.position else None,
            "failure_reason": self.failure_reason,
            "failure_reason_code": self.failure_reason_code,
            "source": self.source,
        }


@dataclass(frozen=True)
class MapGeometryObservation:
    """One auditable map-geometry evidence point.

    ``confidence`` is a deterministic resolver confidence from 0.0 to 1.0,
    not an LLM score. ``visibility`` states whether the point is currently
    visible, inferred from stable map metadata, explicitly unseen, or unknown.
    """

    kind: str
    key: str
    position: MapPoint
    confidence: float
    visibility: str
    source: str
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        kind = str(self.kind)
        if kind not in SC2_GEOMETRY_KIND_VALUES:
            raise ValueError(f"Unsupported map geometry observation kind: {kind!r}.")
        key = str(self.key)
        if not key.strip():
            raise ValueError("Map geometry observation key must be non-empty.")
        position = self.position
        if not isinstance(position, MapPoint):
            extracted = _extract_point(position)
            if extracted is None:
                raise TypeError(
                    "Map geometry observation position must be point-like."
                )
            position = extracted
        confidence = self.confidence
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
        ):
            raise TypeError("Map geometry observation confidence must be finite.")
        confidence = float(confidence)
        if confidence < 0.0 or confidence > 1.0:
            raise ValueError("Map geometry observation confidence must be 0..1.")
        visibility = str(self.visibility)
        if visibility not in SC2_GEOMETRY_VISIBILITY_VALUES:
            raise ValueError(
                f"Unsupported map geometry visibility label: {visibility!r}."
            )
        source = str(self.source)
        if not source.strip():
            raise ValueError("Map geometry observation source must be non-empty.")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "position", position)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "visibility", visibility)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready geometry observation payload."""

        return {
            "kind": self.kind,
            "key": self.key,
            "position": self.position.to_dict(),
            "confidence": self.confidence,
            "visibility": self.visibility,
            "source": self.source,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class MapBaseCluster:
    """One inferred base cluster with attached nearby resources and ramp."""

    key: str
    anchor: MapPoint
    confidence: float
    visibility: str
    source: str
    mineral_patches: Sequence[MapGeometryObservation] = ()
    geysers: Sequence[MapGeometryObservation] = ()
    ramp: MapGeometryObservation | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        key = str(self.key)
        if not key.strip():
            raise ValueError("Map base cluster key must be non-empty.")
        anchor = self.anchor
        if not isinstance(anchor, MapPoint):
            extracted = _extract_point(anchor)
            if extracted is None:
                raise TypeError("Map base cluster anchor must be point-like.")
            anchor = extracted
        confidence = self.confidence
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
        ):
            raise TypeError("Map base cluster confidence must be finite.")
        confidence = float(confidence)
        if confidence < 0.0 or confidence > 1.0:
            raise ValueError("Map base cluster confidence must be 0..1.")
        visibility = str(self.visibility)
        if visibility not in SC2_GEOMETRY_VISIBILITY_VALUES:
            raise ValueError(f"Unsupported base cluster visibility: {visibility!r}.")
        source = str(self.source)
        if not source.strip():
            raise ValueError("Map base cluster source must be non-empty.")
        minerals = tuple(self.mineral_patches)
        geysers = tuple(self.geysers)
        for observation in minerals:
            if (
                not isinstance(observation, MapGeometryObservation)
                or observation.kind != "mineral_patch"
            ):
                raise TypeError("Base cluster minerals must be mineral observations.")
        for observation in geysers:
            if (
                not isinstance(observation, MapGeometryObservation)
                or observation.kind != "geyser"
            ):
                raise TypeError("Base cluster geysers must be geyser observations.")
        if self.ramp is not None and (
            not isinstance(self.ramp, MapGeometryObservation)
            or self.ramp.kind != "ramp"
        ):
            raise TypeError("Base cluster ramp must be a ramp observation.")
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "anchor", anchor)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "visibility", visibility)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "mineral_patches", minerals)
        object.__setattr__(self, "geysers", geysers)
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready base cluster payload."""

        return {
            "key": self.key,
            "anchor": self.anchor.to_dict(),
            "confidence": self.confidence,
            "visibility": self.visibility,
            "source": self.source,
            "mineral_patches": [
                observation.to_dict() for observation in self.mineral_patches
            ],
            "geysers": [observation.to_dict() for observation in self.geysers],
            "ramp": self.ramp.to_dict() if self.ramp else None,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class MapGeometryInference:
    """Full map-geometry inference snapshot derived from BotAI map data."""

    start_locations: Sequence[MapGeometryObservation] = ()
    base_clusters: Sequence[MapBaseCluster] = ()
    player_main_base: MapBaseCluster | None = None
    ramps: Sequence[MapGeometryObservation] = ()
    mineral_patches: Sequence[MapGeometryObservation] = ()
    geysers: Sequence[MapGeometryObservation] = ()

    def __post_init__(self) -> None:
        starts = _coerce_geometry_observations(
            self.start_locations,
            expected_kind="start_location",
            field_name="start_locations",
        )
        clusters = tuple(self.base_clusters)
        for cluster in clusters:
            if not isinstance(cluster, MapBaseCluster):
                raise TypeError("Map geometry base_clusters must be MapBaseCluster.")
        player_main_base = self.player_main_base
        if player_main_base is not None and not isinstance(
            player_main_base,
            MapBaseCluster,
        ):
            raise TypeError("Map geometry player_main_base must be a MapBaseCluster.")
        ramps = _coerce_geometry_observations(
            self.ramps,
            expected_kind="ramp",
            field_name="ramps",
        )
        minerals = _coerce_geometry_observations(
            self.mineral_patches,
            expected_kind="mineral_patch",
            field_name="mineral_patches",
        )
        geysers = _coerce_geometry_observations(
            self.geysers,
            expected_kind="geyser",
            field_name="geysers",
        )
        object.__setattr__(self, "start_locations", starts)
        object.__setattr__(self, "base_clusters", clusters)
        object.__setattr__(self, "player_main_base", player_main_base)
        object.__setattr__(self, "ramps", ramps)
        object.__setattr__(self, "mineral_patches", minerals)
        object.__setattr__(self, "geysers", geysers)

    @classmethod
    def empty(cls) -> "MapGeometryInference":
        """Return an empty, valid geometry snapshot."""

        return cls()

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready geometry inference payload."""

        return {
            "start_locations": [
                observation.to_dict() for observation in self.start_locations
            ],
            "base_clusters": [cluster.to_dict() for cluster in self.base_clusters],
            "player_main_base": (
                self.player_main_base.to_dict() if self.player_main_base else None
            ),
            "ramps": [observation.to_dict() for observation in self.ramps],
            "mineral_patches": [
                observation.to_dict() for observation in self.mineral_patches
            ],
            "geysers": [observation.to_dict() for observation in self.geysers],
        }


@runtime_checkable
class SC2MapResolverInterface(Protocol):
    """Boundary from semantic map-target names to map coordinates."""

    @property
    def player_main_base(self) -> MapBaseCluster | None:
        """Return the player's main-base cluster derived from start location."""

    def lookup(self, target_name: str) -> MapTargetResolution:
        """Look up one semantic target in the current target catalog."""

    def resolve(self, target_name: str) -> MapTargetResolution:
        """Resolve one semantic target name into a structured resolution."""

    def resolve_point(self, target_name: str) -> MapPoint | None:
        """Resolve one semantic target name into a point, or ``None``."""

    def resolve_anchor_position(self, anchor: object) -> MapAnchorPositionResolution:
        """Resolve one anchor object/name into a map/world coordinate."""


@dataclass(frozen=True)
class SC2MapResolver:
    """Default deterministic semantic map resolver for one fixed SC2 map.

    The target registry is derived exactly once (in :meth:`from_bot` or at
    construction) and never re-reads the bot afterwards, so resolution stays
    computation-light, deterministic, and free of caching surprises. Every
    supported target is always classified as either available (with a
    coordinate) or unavailable (with a reason).
    """

    positions: Mapping[str, MapPoint] = field(default_factory=dict)
    unavailable_reasons: Mapping[str, str] = field(default_factory=dict)
    sources: Mapping[str, str] = field(default_factory=dict)
    geometry: MapGeometryInference = field(default_factory=MapGeometryInference.empty)

    def __post_init__(self) -> None:
        supported = set(SC2_SUPPORTED_SEMANTIC_TARGETS)
        positions: dict[str, MapPoint] = {}
        for raw_name, raw_point in dict(self.positions).items():
            name = str(raw_name)
            if name not in supported:
                raise ValueError(
                    f"Unsupported semantic map target in positions: {name!r}."
                )
            point = raw_point if isinstance(raw_point, MapPoint) else _extract_point(raw_point)
            if point is None:
                raise TypeError(
                    f"Map target {name!r} position must be point-like (.x/.y)."
                )
            positions[name] = point

        reasons: dict[str, str] = {}
        for raw_name, raw_reason in dict(self.unavailable_reasons).items():
            name = str(raw_name)
            if name not in supported:
                raise ValueError(
                    f"Unsupported semantic map target in unavailable_reasons: {name!r}."
                )
            if name in positions:
                raise ValueError(
                    f"Map target {name!r} cannot be both available and unavailable."
                )
            reason = str(raw_reason)
            if not reason.strip():
                raise ValueError(
                    f"Unavailable map target {name!r} must carry a non-empty reason."
                )
            reasons[name] = reason

        sources: dict[str, str] = {}
        for raw_name, raw_source in dict(self.sources).items():
            name = str(raw_name)
            if name not in supported:
                raise ValueError(
                    f"Unsupported semantic map target in sources: {name!r}."
                )
            source = str(raw_source)
            if not source.strip():
                raise ValueError(
                    f"Semantic map target {name!r} source must be non-empty."
                )
            sources[name] = source

        ordered_positions: dict[str, MapPoint] = {}
        ordered_reasons: dict[str, str] = {}
        ordered_sources: dict[str, str] = {}
        for name in SC2_SUPPORTED_SEMANTIC_TARGETS:
            if name in positions:
                ordered_positions[name] = positions[name]
                ordered_sources[name] = sources.get(name, _DEFAULT_CATALOG_SOURCE)
            elif name in reasons:
                ordered_reasons[name] = reasons[name]
            else:
                ordered_reasons[name] = _UNDERIVED_TARGET_REASON

        object.__setattr__(self, "positions", ordered_positions)
        object.__setattr__(self, "unavailable_reasons", ordered_reasons)
        object.__setattr__(self, "sources", ordered_sources)
        if self.geometry is None:
            geometry = MapGeometryInference.empty()
        elif isinstance(self.geometry, MapGeometryInference):
            geometry = self.geometry
        else:
            raise TypeError("SC2MapResolver geometry must be MapGeometryInference.")
        object.__setattr__(self, "geometry", geometry)

    @classmethod
    def from_bot(cls, bot: object) -> "SC2MapResolver":
        """Derive the semantic target registry once from a BotAI-like object.

        Positions are read via ``.x``/``.y`` or ``.position.x``/``.position.y``
        duck-typing. Missing or broken attributes never raise: each underivable
        target becomes an explicit unavailable entry with a reason.
        """

        positions: dict[str, MapPoint] = {}
        reasons: dict[str, str] = {}
        sources: dict[str, str] = {}

        def register(
            target: str,
            point: MapPoint | None,
            reason: str,
            *,
            source: str = _DEFAULT_CATALOG_SOURCE,
        ) -> None:
            if point is not None:
                positions[target] = point
                sources[target] = source
            else:
                reasons[target] = reason

        self_main = _extract_point(_safe_getattr(bot, "start_location"))
        register(
            "self_main",
            self_main,
            "BotAI start_location is missing or not point-like.",
        )

        enemy_starts = _collect_points(_safe_getattr(bot, "enemy_start_locations"))
        expansions = _collect_points(_safe_getattr(bot, "expansion_locations_list"))
        visible_enemy_main = _derive_visible_enemy_main(bot, enemy_starts)
        history_enemy_main = (
            None
            if visible_enemy_main is not None
            else _derive_history_enemy_main(bot, enemy_starts, expansions)
        )
        enemy_main = (
            visible_enemy_main
            or history_enemy_main
            or _unambiguous_enemy_start(enemy_starts)
        )
        enemy_vision_source = (
            _ENEMY_VISION_CATALOG_SOURCE
            if visible_enemy_main is not None or history_enemy_main is not None
            else _DEFAULT_CATALOG_SOURCE
        )
        enemy_main_visibility = (
            "visible"
            if visible_enemy_main is not None
            else "unseen"
            if history_enemy_main is not None
            else "inferred"
        )
        register(
            "enemy_main",
            enemy_main,
            "Cannot derive enemy_main: requires exactly one point-like BotAI "
            "enemy_start_locations entry, a visible enemy townhall, or "
            "auditable last-seen/scouting enemy base evidence.",
            source=enemy_vision_source,
        )

        self_ramp = _derive_self_ramp(bot, self_main)
        register(
            "self_ramp",
            self_ramp,
            "BotAI main_base_ramp has no point-like top_center or "
            f"barracks_correct_placement within {_RAMP_MAX_DISTANCE:g} of "
            "start_location.",
        )
        register(
            "self_choke",
            self_ramp,
            "Cannot derive self_choke: requires the same BotAI main_base_ramp "
            "point needed for self_ramp.",
        )

        self_natural = _nearest_natural_expansion(
            expansions,
            self_main,
            require_unambiguous=False,
        )
        register(
            "self_natural",
            self_natural,
            "Cannot derive self_natural: requires a point-like start_location and "
            "one clear BotAI expansion_locations_list entry within natural "
            "distance bounds."
            if self_main is None or not expansions
            else "BotAI expansion_locations_list has no natural expansion "
            "distinct from the own main base within distance bounds.",
        )
        inferred_enemy_natural = _nearest_natural_expansion(
            expansions,
            enemy_main,
            require_unambiguous=True,
        )
        visible_enemy_natural = _derive_visible_enemy_natural(
            bot,
            enemy_main,
            inferred_enemy_natural,
        )
        enemy_natural = visible_enemy_natural or inferred_enemy_natural
        enemy_natural_source = (
            _ENEMY_VISION_CATALOG_SOURCE
            if visible_enemy_natural is not None
            or history_enemy_main is not None
            else _DEFAULT_CATALOG_SOURCE
        )
        enemy_natural_visibility = (
            "visible"
            if visible_enemy_natural is not None
            else "inferred"
        )
        register(
            "enemy_natural",
            enemy_natural,
            "Cannot derive enemy_natural: requires a point-like enemy main and "
            "one clear BotAI expansion_locations_list entry within natural "
            "distance bounds."
            if enemy_main is None or not expansions
            else "BotAI expansion_locations_list has no unambiguous natural "
            "expansion distinct from the enemy main base within distance bounds.",
            source=enemy_natural_source,
        )
        self_third = _derive_third_expansion(
            expansions,
            main=self_main,
            natural=self_natural,
            excluded=(enemy_main, enemy_natural),
        )
        register(
            "self_third",
            self_third,
            "Cannot derive self_third: requires a point-like start_location, "
            "self_natural, and one additional expansion beyond the natural.",
        )
        enemy_third = _derive_third_expansion(
            expansions,
            main=enemy_main,
            natural=enemy_natural,
            excluded=(self_main, self_natural),
        )
        register(
            "enemy_third",
            enemy_third,
            "Cannot derive enemy_third: requires enemy_main, enemy_natural, "
            "and one additional expansion beyond the enemy natural.",
        )

        scouted_enemy_front = _derive_scouted_enemy_front(bot)
        enemy_ramp = scouted_enemy_front or _derive_enemy_ramp(bot, enemy_main)
        enemy_front = scouted_enemy_front or enemy_ramp
        enemy_front_source = (
            _ENEMY_VISION_CATALOG_SOURCE
            if scouted_enemy_front is not None or visible_enemy_main is not None
            else _DEFAULT_CATALOG_SOURCE
        )
        enemy_front_reason = (
            "Cannot derive enemy_ramp: requires a point-like enemy main and a "
            f"single BotAI game_info.map_ramps top_center within {_RAMP_MAX_DISTANCE:g}, "
            "or a point-like scouted enemy ramp/front access location."
        )
        register(
            "enemy_ramp",
            enemy_ramp,
            enemy_front_reason,
            source=enemy_front_source,
        )
        register(
            "enemy_front",
            enemy_front,
            "Cannot derive enemy_front: requires a discovered or safely inferred "
            "enemy ramp/front access point.",
            source=enemy_front_source,
        )
        register(
            "enemy_choke",
            enemy_front,
            "Cannot derive enemy_choke: requires a discovered or safely inferred "
            "enemy ramp/front access point.",
            source=enemy_front_source,
        )

        scout_location = _derive_named_observation_point(bot, _SCOUT_LOCATION_ATTRS)
        register(
            "scout_location",
            scout_location,
            "Cannot derive scout_location: no point-like scout location observation "
            "field was available.",
            source=_ENEMY_VISION_CATALOG_SOURCE,
        )
        last_seen_enemy_area = _derive_last_seen_enemy_area(bot)
        register(
            "last_seen_enemy_area",
            last_seen_enemy_area,
            "Cannot derive last_seen_enemy_area: requires a point-like last-seen "
            "enemy observation or visible enemy unit/structure position.",
            source=_ENEMY_VISION_CATALOG_SOURCE,
        )

        mineral_points = _collect_points(_safe_getattr(bot, "mineral_field"))
        geyser_points = _collect_points(_safe_getattr(bot, "vespene_geyser"))
        own_townhall_points = _derive_own_townhall_points(bot)
        geometry = _build_geometry_inference(
            self_main=self_main,
            enemy_starts=enemy_starts,
            enemy_main=enemy_main,
            visible_enemy_main=visible_enemy_main,
            enemy_main_source=enemy_vision_source,
            enemy_main_visibility=enemy_main_visibility,
            expansions=expansions,
            self_natural=self_natural,
            enemy_natural=enemy_natural,
            visible_enemy_natural=visible_enemy_natural,
            enemy_natural_source=enemy_natural_source,
            enemy_natural_visibility=enemy_natural_visibility,
            self_ramp=self_ramp,
            enemy_ramp=enemy_ramp,
            scouted_enemy_front=scouted_enemy_front,
            mineral_points=mineral_points,
            geyser_points=geyser_points,
            own_townhall_points=own_townhall_points,
        )

        enemy_mineral_line, enemy_mineral_line_reason = (
            _mineral_line_from_validated_geometry(
                geometry,
                base_key="enemy_main",
                target="enemy_mineral_line",
            )
        )
        register(
            "enemy_mineral_line",
            enemy_mineral_line,
            enemy_mineral_line_reason,
            source=_RESOURCE_GEOMETRY_CATALOG_SOURCE,
        )

        self_mineral_line, self_mineral_line_reason = (
            _mineral_line_from_validated_geometry(
                geometry,
                base_key="self_main",
                target="self_mineral_line",
            )
        )
        register(
            "self_mineral_line",
            self_mineral_line,
            self_mineral_line_reason,
            source=_RESOURCE_GEOMETRY_CATALOG_SOURCE,
        )

        self_geyser, self_geyser_reason = _geyser_from_validated_geometry(
            geometry,
            base_key="self_main",
            target="self_geyser",
        )
        register(
            "self_geyser",
            self_geyser,
            self_geyser_reason,
            source=_RESOURCE_GEOMETRY_CATALOG_SOURCE,
        )

        return cls(
            positions=positions,
            unavailable_reasons=reasons,
            sources=sources,
            geometry=geometry,
        )

    @property
    def available_targets(self) -> tuple[str, ...]:
        """Currently resolvable semantic targets, in canonical order."""

        return tuple(self.positions)

    @property
    def unavailable_targets(self) -> tuple[str, ...]:
        """Known-but-underivable semantic targets, in canonical order."""

        return tuple(self.unavailable_reasons)

    @property
    def semantic_target_catalog(self) -> tuple[SemanticTargetCatalogEntry, ...]:
        """Full canonical semantic target catalog refreshed from observations."""

        entries: list[SemanticTargetCatalogEntry] = []
        for target in SC2_SUPPORTED_SEMANTIC_TARGETS:
            position = self.positions.get(target)
            if position is not None:
                entries.append(
                    SemanticTargetCatalogEntry(
                        target=target,
                        aliases=SC2_CANONICAL_TARGET_ALIASES[target],
                        available=True,
                        position=position,
                        source=self.sources.get(target, _DEFAULT_CATALOG_SOURCE),
                    )
                )
            else:
                entries.append(
                    SemanticTargetCatalogEntry(
                        target=target,
                        aliases=SC2_CANONICAL_TARGET_ALIASES[target],
                        available=False,
                        failure_reason=self.unavailable_reasons.get(
                            target,
                            _UNDERIVED_TARGET_REASON,
                        ),
                    )
                )
        return tuple(entries)

    @property
    def player_main_base(self) -> MapBaseCluster | None:
        """Player main-base cluster derived from the initial start location."""

        return self.geometry.player_main_base

    def resolve(self, target_name: str) -> MapTargetResolution:
        """Resolve one semantic target name into a structured resolution.

        Accepts canonical names plus the human phrases from
        ``SC2_TARGET_ALIASES``. Unknown names yield an unavailable resolution
        whose reason marks them unsupported and whose alternatives list the
        currently available targets.
        """

        requested = _semantic_target_request_label(target_name)
        lookup_name = _semantic_target_lookup_name(target_name)
        canonical = _canonical_target_name(lookup_name, self.semantic_target_catalog)
        if canonical not in SC2_SUPPORTED_SEMANTIC_TARGETS:
            dynamic_base = _resolve_base_selection_target(self, target_name)
            if dynamic_base is None and lookup_name != requested:
                dynamic_base = _resolve_base_selection_target(self, lookup_name)
            if dynamic_base is not None:
                return dynamic_base[0]
            supported = ", ".join(SC2_SUPPORTED_SEMANTIC_TARGETS)
            return MapTargetResolution(
                target=requested or "unknown",
                available=False,
                position=None,
                reason=(
                    f"Unsupported semantic map target: {requested!r}. "
                    f"Supported targets: {supported}."
                ),
                alternatives=self.available_targets,
            )
        position = self.positions.get(canonical)
        if position is not None:
            return MapTargetResolution(
                target=canonical,
                available=True,
                position=position,
                source=self.sources.get(canonical, _DEFAULT_CATALOG_SOURCE),
            )
        return MapTargetResolution(
            target=canonical,
            available=False,
            position=None,
            reason=self.unavailable_reasons.get(canonical, _UNDERIVED_TARGET_REASON),
            alternatives=self.available_targets,
        )

    def lookup(self, target_name: str) -> MapTargetResolution:
        """Look up one semantic target in this resolver snapshot."""

        return self.resolve(target_name)

    def resolve_point(self, target_name: str) -> MapPoint | None:
        """Resolve one semantic target name into a point, or ``None``."""

        return self.resolve(target_name).position

    def resolve_anchor_position(self, anchor: object) -> MapAnchorPositionResolution:
        """Resolve a semantic/geometry anchor to a concrete world map position.

        Anchors may be point-like objects, placement-policy mappings with an
        ``anchor_target``/``target``/``anchor`` field, semantic target aliases,
        base-cluster keys, or geometry observation keys. The resolver stays
        read-only and returns an explicit unavailable reason instead of
        guessing when the anchor is unsupported or ambiguous.
        """

        spatial_relation = _placement_spatial_relation(anchor)
        has_named_anchor = _has_named_anchor_metadata(anchor)
        direct_point = _extract_point(anchor)
        if direct_point is None and isinstance(anchor, Mapping) and not has_named_anchor:
            for key in ("position", "point", "anchor_position"):
                direct_point = _extract_point(anchor.get(key))
                if direct_point is not None:
                    break
        if direct_point is not None:
            placement_point, placement_policy = _relative_placement_position(
                self,
                direct_point,
                anchor=anchor,
                spatial_relation=spatial_relation,
                search_radius=_placement_search_radius(anchor),
            )
            return MapAnchorPositionResolution(
                anchor=_anchor_label(anchor),
                available=True,
                position=placement_point,
                source="explicit point-like anchor",
                placement_policy=placement_policy,
            )

        raw_anchor = _anchor_name(anchor)
        prefer_semantic_anchor = _prefer_semantic_anchor_before_base_selection(anchor)
        if not raw_anchor and not _has_explicit_base_selection_metadata(anchor):
            return MapAnchorPositionResolution(
                anchor="unknown",
                available=False,
                position=None,
                reason=(
                    "Anchor is empty or not point-like; provide a supported "
                    "semantic target or map-geometry key."
                ),
                alternatives=_anchor_alternatives(self),
            )

        if raw_anchor and prefer_semantic_anchor:
            semantic_anchor = _semantic_anchor_lookup_name(
                raw_anchor,
                self.semantic_target_catalog,
            )
            target_resolution = self.resolve(semantic_anchor)
            if target_resolution.target in SC2_SUPPORTED_SEMANTIC_TARGETS:
                if target_resolution.available:
                    placement_point, placement_policy = _relative_placement_position(
                        self,
                        target_resolution.position,
                        anchor=anchor,
                        spatial_relation=spatial_relation,
                        search_radius=_placement_search_radius(anchor),
                    )
                    return MapAnchorPositionResolution(
                        anchor=raw_anchor,
                        available=True,
                        position=placement_point,
                        source=self.sources.get(
                            target_resolution.target,
                            _DEFAULT_CATALOG_SOURCE,
                        ),
                        target=target_resolution.target,
                        placement_policy=placement_policy,
                    )
                return MapAnchorPositionResolution(
                    anchor=raw_anchor,
                    available=False,
                    position=None,
                    reason=target_resolution.reason,
                    target=target_resolution.target,
                    alternatives=target_resolution.alternatives,
                )

        base_selection = (
            _resolve_base_selection_target(self, anchor)
            if _has_explicit_base_selection_metadata(anchor)
            else None
        )
        if base_selection is not None:
            base_resolution, source = base_selection
            if base_resolution.available:
                placement_point, placement_policy = _relative_placement_position(
                    self,
                    base_resolution.position,
                    anchor=anchor,
                    spatial_relation=spatial_relation,
                    search_radius=_placement_search_radius(anchor),
                )
                return MapAnchorPositionResolution(
                    anchor=base_resolution.target,
                    available=True,
                    position=placement_point,
                    source=source,
                    target=base_resolution.target,
                    placement_policy=placement_policy,
                )
            return MapAnchorPositionResolution(
                anchor=base_resolution.target,
                available=False,
                position=None,
                reason=base_resolution.reason,
                target=base_resolution.target,
                alternatives=base_resolution.alternatives,
            )

        if not raw_anchor:
            return MapAnchorPositionResolution(
                anchor="unknown",
                available=False,
                position=None,
                reason=(
                    "Anchor is empty or not point-like; provide a supported "
                    "semantic target or map-geometry key."
                ),
                alternatives=_anchor_alternatives(self),
            )

        semantic_anchor = _semantic_anchor_lookup_name(
            raw_anchor,
            self.semantic_target_catalog,
        )
        target_resolution = self.resolve(semantic_anchor)
        if target_resolution.target in SC2_SUPPORTED_SEMANTIC_TARGETS:
            if target_resolution.available:
                placement_point, placement_policy = _relative_placement_position(
                    self,
                    target_resolution.position,
                    anchor=anchor,
                    spatial_relation=spatial_relation,
                    search_radius=_placement_search_radius(anchor),
                )
                return MapAnchorPositionResolution(
                    anchor=raw_anchor,
                    available=True,
                    position=placement_point,
                    source=self.sources.get(
                        target_resolution.target,
                        _DEFAULT_CATALOG_SOURCE,
                    ),
                    target=target_resolution.target,
                    placement_policy=placement_policy,
                )
            return MapAnchorPositionResolution(
                anchor=raw_anchor,
                available=False,
                position=None,
                reason=target_resolution.reason,
                target=target_resolution.target,
                alternatives=target_resolution.alternatives,
            )

        base_selection = _resolve_base_selection_target(self, anchor)
        if base_selection is not None:
            base_resolution, source = base_selection
            if base_resolution.available:
                placement_point, placement_policy = _relative_placement_position(
                    self,
                    base_resolution.position,
                    anchor=anchor,
                    spatial_relation=spatial_relation,
                    search_radius=_placement_search_radius(anchor),
                )
                return MapAnchorPositionResolution(
                    anchor=base_resolution.target,
                    available=True,
                    position=placement_point,
                    source=source,
                    target=base_resolution.target,
                    placement_policy=placement_policy,
                )
            return MapAnchorPositionResolution(
                anchor=base_resolution.target,
                available=False,
                position=None,
                reason=base_resolution.reason,
                target=base_resolution.target,
                alternatives=base_resolution.alternatives,
            )

        cluster_matches = tuple(
            cluster for cluster in self.geometry.base_clusters if cluster.key == raw_anchor
        )
        if len(cluster_matches) == 1:
            cluster = cluster_matches[0]
            placement_point, placement_policy = _relative_placement_position(
                self,
                cluster.anchor,
                anchor=anchor,
                spatial_relation=spatial_relation,
                search_radius=_placement_search_radius(anchor),
            )
            return MapAnchorPositionResolution(
                anchor=raw_anchor,
                available=True,
                position=placement_point,
                source=cluster.source,
                target=cluster.key,
                placement_policy=placement_policy,
            )
        if len(cluster_matches) > 1:
            return MapAnchorPositionResolution(
                anchor=raw_anchor,
                available=False,
                position=None,
                reason=(
                    f"Ambiguous map anchor {raw_anchor!r}: "
                    f"{len(cluster_matches)} base clusters share that key."
                ),
                alternatives=_anchor_alternatives(self),
            )

        observations = _geometry_observations(self.geometry)
        observation_matches = tuple(
            observation for observation in observations if observation.key == raw_anchor
        )
        if len(observation_matches) == 1:
            observation = observation_matches[0]
            placement_point, placement_policy = _relative_placement_position(
                self,
                observation.position,
                anchor=anchor,
                spatial_relation=spatial_relation,
                search_radius=_placement_search_radius(anchor),
            )
            return MapAnchorPositionResolution(
                anchor=raw_anchor,
                available=True,
                position=placement_point,
                source=observation.source,
                target=observation.key,
                placement_policy=placement_policy,
            )
        if len(observation_matches) > 1:
            return MapAnchorPositionResolution(
                anchor=raw_anchor,
                available=False,
                position=None,
                reason=(
                    f"Ambiguous map anchor {raw_anchor!r}: "
                    f"{len(observation_matches)} geometry observations share that key."
                ),
                alternatives=_anchor_alternatives(self),
            )

        return MapAnchorPositionResolution(
            anchor=raw_anchor,
            available=False,
            position=None,
            reason=(
                f"Unsupported map anchor: {raw_anchor!r}. Provide a supported "
                "semantic target, base-cluster key, geometry key, or point."
            ),
            alternatives=_anchor_alternatives(self),
        )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready snapshot of the full target registry."""

        return {
            "available_targets": list(self.available_targets),
            "positions": {
                name: point.to_dict() for name, point in self.positions.items()
            },
            "unavailable": dict(self.unavailable_reasons),
            "semantic_target_catalog": [
                entry.to_dict() for entry in self.semantic_target_catalog
            ],
            "geometry": self.geometry.to_dict(),
        }


@dataclass(frozen=True)
class SC2RuntimeMapResolver:
    """Runtime semantic target catalog that refreshes from BotAI per lookup.

    ``SC2MapResolver`` is a deterministic snapshot. This wrapper is the live
    runtime API: each catalog lookup derives a fresh snapshot from the bound
    BotAI-like object, so actions use current world positions instead of
    coordinates captured at adapter construction or first use.
    """

    bot: object

    def snapshot(self) -> SC2MapResolver:
        """Return a fresh semantic target snapshot from the current bot state."""

        return SC2MapResolver.from_bot(self.bot)

    @property
    def available_targets(self) -> tuple[str, ...]:
        """Currently resolvable semantic targets, in canonical order."""

        return self.snapshot().available_targets

    @property
    def unavailable_targets(self) -> tuple[str, ...]:
        """Known-but-underivable semantic targets, in canonical order."""

        return self.snapshot().unavailable_targets

    @property
    def semantic_target_catalog(self) -> tuple[SemanticTargetCatalogEntry, ...]:
        """Full canonical catalog derived from the latest observations."""

        return self.snapshot().semantic_target_catalog

    @property
    def geometry(self) -> MapGeometryInference:
        """Latest auditable map-geometry inference snapshot."""

        return self.snapshot().geometry

    @property
    def player_main_base(self) -> MapBaseCluster | None:
        """Latest player main-base cluster from live BotAI observations."""

        return self.snapshot().player_main_base

    def lookup(self, target_name: str) -> MapTargetResolution:
        """Look up one semantic target against the latest bot observations."""

        return self.snapshot().resolve(target_name)

    def resolve(self, target_name: str) -> MapTargetResolution:
        """Resolve one semantic target against the latest bot observations."""

        return self.lookup(target_name)

    def resolve_point(self, target_name: str) -> MapPoint | None:
        """Resolve one semantic target into the latest point, or ``None``."""

        return self.lookup(target_name).position

    def resolve_anchor_position(self, anchor: object) -> MapAnchorPositionResolution:
        """Resolve one anchor against the latest bot observations."""

        return self.snapshot().resolve_anchor_position(anchor)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready fresh snapshot of the runtime target catalog."""

        return self.snapshot().to_dict()


def _canonical_target_name(
    target_name: str,
    catalog_entries: object = (),
) -> str:
    return resolve_sc2_target_name(target_name, catalog_entries) or target_name.strip()


def _semantic_target_lookup_name(target: object) -> str:
    """Extract a semantic target name from parsed target objects.

    LLM/tool outputs may carry a structured semantic target plus an anchor
    point. The point is only evidence; the command coordinate must still be
    resolved from live map state via the semantic key.
    """

    parsed = _parsed_target_name(target)
    if parsed:
        return parsed
    if type(target) is str:
        return target.strip()
    return ""


def _semantic_target_request_label(target: object) -> str:
    parsed = _semantic_target_lookup_name(target)
    if parsed:
        return parsed
    if type(target) is str:
        return target.strip()
    return "unknown"


def _map_failure_reason_code(*, target: str, reason: str, scope: str) -> str:
    """Return a stable machine-readable code for resolver failure output."""

    normalized_reason = str(reason).strip()
    if not normalized_reason:
        return ""
    lowered = normalized_reason.casefold()
    if "unsupported semantic map target" in lowered:
        return "unsupported_semantic_target"
    if "unsupported map anchor" in lowered:
        return "unsupported_map_anchor"
    if lowered.startswith("anchor is empty"):
        return "empty_anchor"
    if "ambiguous map anchor" in lowered:
        return "ambiguous_map_anchor"
    if "no known base instance" in lowered:
        return "no_known_base_instance"
    if "provide selected_semantic_base" in lowered:
        return "missing_selected_base_metadata"
    if normalized_reason == _UNDERIVED_TARGET_REASON:
        return "semantic_target_underived"
    normalized_target = str(target).strip()
    if normalized_target in SC2_SUPPORTED_SEMANTIC_TARGETS:
        return f"cannot_derive_{normalized_target}"
    return f"{scope}_unavailable"


def _resolve_base_selection_target(
    resolver: SC2MapResolver,
    anchor: object,
) -> tuple[MapTargetResolution, str] | None:
    selector = _base_selection_selector(anchor)
    if selector is None:
        return None
    target = _base_selection_target(selector)
    alternatives = _base_selection_alternatives(resolver)

    if selector in {"selected", "current", "currently_selected"}:
        selected_target = _selected_semantic_base_name(anchor)
        if selected_target:
            selected_resolution = _resolve_selected_semantic_base(
                resolver,
                selected_target,
            )
            if selected_resolution is not None:
                return selected_resolution
        return (
            MapTargetResolution(
                target=target,
                available=False,
                position=None,
                reason=(
                    "Cannot resolve selected base: provide selected_semantic_base "
                    "or current_semantic_base metadata."
                ),
                alternatives=alternatives,
            ),
            _OWN_TOWNHALL_SOURCE,
        )

    cluster = _base_cluster_for_selector(resolver, selector)
    if cluster is None:
        return (
            MapTargetResolution(
                target=target,
                available=False,
                position=None,
                reason=(
                    f"Cannot resolve {target}: no known base instance matches "
                    f"selector {selector!r}."
                ),
                alternatives=alternatives,
            ),
            _OWN_TOWNHALL_SOURCE,
        )
    return (
        MapTargetResolution(
            target=target,
            available=True,
            position=cluster.anchor,
            source=cluster.source,
        ),
        cluster.source,
    )


def _resolve_selected_semantic_base(
    resolver: SC2MapResolver,
    selected_target: str,
) -> tuple[MapTargetResolution, str] | None:
    dynamic = _resolve_base_selection_target(resolver, selected_target)
    if dynamic is not None:
        return dynamic
    canonical = _canonical_target_name(selected_target, resolver.semantic_target_catalog)
    if canonical in SC2_SUPPORTED_SEMANTIC_TARGETS:
        resolution = resolver.resolve(canonical)
        return (resolution, resolver.sources.get(canonical, _DEFAULT_CATALOG_SOURCE))
    return None


def _base_selection_selector(anchor: object) -> str | None:
    if isinstance(anchor, Mapping):
        base_selection = anchor.get("base_selection")
        if isinstance(base_selection, Mapping):
            selector = _normalized_base_selector(base_selection.get("selector"))
            if selector is not None:
                return selector
            for key in ("target", "location", "label"):
                selector = _base_selector_from_text(base_selection.get(key))
                if selector is not None:
                    return selector
        for key in ("base_selector", "selector"):
            selector = _normalized_base_selector(anchor.get(key))
            if selector is not None:
                return selector
        for key in (
            "anchor_target",
            "target",
            "semantic_target",
            "anchor",
            "location",
            "base",
        ):
            selector = _base_selector_from_text(anchor.get(key))
            if selector is not None:
                return selector
        if _selected_semantic_base_name(anchor):
            return "selected"
        return None
    return _base_selector_from_text(anchor)


def _has_explicit_base_selection_metadata(anchor: object) -> bool:
    if not isinstance(anchor, Mapping):
        return False
    if isinstance(anchor.get("base_selection"), Mapping):
        return True
    if _selected_semantic_base_name(anchor):
        return True
    return any(
        type(anchor.get(key)) is str and bool(str(anchor.get(key)).strip())
        for key in ("base_selector", "selector")
    )


def _selected_semantic_base_name(anchor: object) -> str:
    if not isinstance(anchor, Mapping):
        return ""
    for key in (
        "selected_semantic_base",
        "current_semantic_base",
        "selected_base_target",
        "current_base_target",
    ):
        value = anchor.get(key)
        if type(value) is str and value.strip():
            return value.strip()
    base_selection = anchor.get("base_selection")
    if isinstance(base_selection, Mapping):
        for key in (
            "selected_semantic_base",
            "current_semantic_base",
            "selected_base_target",
            "current_base_target",
        ):
            value = base_selection.get(key)
            if type(value) is str and value.strip():
                return value.strip()
    return ""


def _normalized_base_selector(value: object) -> str | None:
    if type(value) is not str:
        return None
    compact = _compact_base_text(value)
    if compact in {"main", "selfmain", "mainbase", "본진", "메인"}:
        return "main"
    if compact in {
        "natural",
        "selfnatural",
        "naturalbase",
        "naturalexpansion",
        "앞마당",
        "내추럴",
    }:
        return "natural"
    if compact in {"third", "selfthird", "thirdbase", "3rdbase", "삼룡이"}:
        return "third"
    if compact in {
        "newest",
        "selfnewest",
        "newestbase",
        "latestbase",
        "새로지은사령부",
        "최근사령부",
    }:
        return "newest"
    if compact in {
        "selected",
        "current",
        "currentlyselected",
        "selectedbase",
        "currentbase",
        "현재선택",
    }:
        return "selected"
    for prefix in ("additionalbase", "additional", "selfadditional", "추가사령부", "추가커맨드"):
        if compact.startswith(prefix):
            suffix = compact[len(prefix) :]
            if suffix.isdigit() and int(suffix) > 0:
                return f"additional_{int(suffix)}"
    if compact.startswith("selfadditional") and compact[len("selfadditional") :].isdigit():
        return f"additional_{int(compact[len('selfadditional') :])}"
    return None


def _base_selector_from_text(value: object) -> str | None:
    if type(value) is not str:
        return None
    selector = _normalized_base_selector(value)
    if selector is not None:
        return selector
    compact = _compact_base_text(value)
    if any(token in compact for token in ("enemy", "opponent", "적", "상대")):
        return None
    if "본진" in compact or "메인" in compact:
        return "main"
    if "앞마당" in compact or "내추럴" in compact:
        return "natural"
    if "삼룡이" in compact or "세번째" in compact or "셋째" in compact:
        return "third"
    if (
        "새로지은" in compact
        or "최근" in compact
        or "막지은" in compact
        or "latest" in compact
        or "newest" in compact
    ):
        return "newest"
    if compact.startswith("selfadditional") and compact[len("selfadditional") :].isdigit():
        return f"additional_{int(compact[len('selfadditional') :])}"
    if compact.startswith("self") and compact.endswith("base"):
        return _normalized_base_selector(compact[4:])
    return None


def _compact_base_text(value: str) -> str:
    return "".join(ch for ch in value.casefold() if ch.isalnum())


def _base_selection_target(selector: str) -> str:
    if selector == "main":
        return "self_main"
    if selector == "natural":
        return "self_natural"
    if selector == "third":
        return "self_third"
    if selector == "newest":
        return "self_newest"
    if selector in {"selected", "current", "currently_selected"}:
        return "self_selected"
    if selector.startswith("additional_"):
        return f"self_{selector}"
    return f"self_{selector}"


def _base_cluster_for_selector(
    resolver: SC2MapResolver,
    selector: str,
) -> MapBaseCluster | None:
    clusters = _ordered_self_base_clusters(resolver)
    by_key = {cluster.key: cluster for cluster in clusters}
    if selector == "main":
        return by_key.get("self_main")
    if selector == "natural":
        return by_key.get("self_natural")
    if selector == "third":
        return _additional_base_cluster(clusters, 1)
    if selector == "newest":
        observed = [
            cluster
            for cluster in clusters
            if _is_real_number(cluster.metadata.get("own_townhall_order"))
        ]
        if observed:
            return max(
                observed,
                key=lambda cluster: float(cluster.metadata["own_townhall_order"]),
            )
        extras = [
            cluster
            for cluster in clusters
            if cluster.key not in {"self_main", "self_natural"}
        ]
        return extras[-1] if extras else None
    if selector.startswith("additional_"):
        suffix = selector.removeprefix("additional_")
        if suffix.isdigit():
            return _additional_base_cluster(clusters, int(suffix))
    return None


def _ordered_self_base_clusters(resolver: SC2MapResolver) -> tuple[MapBaseCluster, ...]:
    clusters = tuple(resolver.geometry.base_clusters)
    by_key = {cluster.key: cluster for cluster in clusters}
    ordered: list[MapBaseCluster] = []
    for key in ("self_main", "self_natural"):
        cluster = by_key.get(key)
        if cluster is not None:
            ordered.append(cluster)
    main = by_key.get("self_main")

    def extra_sort_key(cluster: MapBaseCluster) -> tuple[int, float, float, float]:
        observed = 0 if _is_real_number(cluster.metadata.get("own_townhall_order")) else 1
        distance = main.anchor.distance_to(cluster.anchor) if main is not None else 0.0
        return (observed, distance, cluster.anchor.x, cluster.anchor.y)

    extras = sorted(
        (
            cluster
            for cluster in clusters
            if cluster.key not in {"self_main", "self_natural"}
            and not cluster.key.startswith("enemy_")
            and str(cluster.metadata.get("owner", "")) != "enemy"
        ),
        key=extra_sort_key,
    )
    return tuple(_unique_clusters((*ordered, *extras)))


def _additional_base_cluster(
    ordered_clusters: Sequence[MapBaseCluster],
    index: int,
) -> MapBaseCluster | None:
    if index <= 0:
        return None
    extras = tuple(
        cluster
        for cluster in ordered_clusters
        if cluster.key not in {"self_main", "self_natural"}
    )
    if index > len(extras):
        return None
    return extras[index - 1]


def _unique_clusters(clusters: Sequence[MapBaseCluster]) -> tuple[MapBaseCluster, ...]:
    unique: list[MapBaseCluster] = []
    for cluster in clusters:
        if any(cluster.anchor.distance_to(existing.anchor) <= _MAIN_EXCLUSION_RADIUS for existing in unique):
            continue
        unique.append(cluster)
    return tuple(unique)


def _base_selection_alternatives(resolver: SC2MapResolver) -> tuple[str, ...]:
    dynamic = tuple(
        _base_selection_target(selector)
        for selector in ("main", "natural", "third", "newest")
        if _base_cluster_for_selector(resolver, selector) is not None
    )
    additional_count = len(
        tuple(
            cluster
            for cluster in _ordered_self_base_clusters(resolver)
            if cluster.key not in {"self_main", "self_natural"}
        )
    )
    additional = tuple(
        f"self_additional_{index}" for index in range(1, additional_count + 1)
    )
    return _unique_strings((*resolver.available_targets, *dynamic, *additional))


def _safe_getattr(obj: object, name: str) -> object | None:
    """Read one attribute without ever raising (python-sc2 properties can)."""

    if obj is None:
        return None
    try:
        return getattr(obj, name, None)
    except Exception:
        return None


def _safe_iter(value: object) -> list[object]:
    if value is None or isinstance(value, (str, bytes)):
        return []
    try:
        return list(value)
    except Exception:
        return []


def _has_named_anchor_metadata(anchor: object) -> bool:
    if not isinstance(anchor, Mapping):
        return False
    if _parsed_target_name(anchor):
        return True
    return any(
        type(anchor.get(key)) is str and bool(str(anchor.get(key)).strip())
        for key in (
            "anchor_target",
            "target",
            "semantic_target",
            "anchor_key",
            "base_key",
            "base_cluster_key",
            "expansion_key",
            "geometry_key",
            "start_location_key",
            "key",
            "anchor",
            "name",
        )
    )


def _anchor_name(anchor: object) -> str:
    parsed = _parsed_target_name(anchor)
    if parsed:
        return parsed
    if isinstance(anchor, Mapping):
        for key in (
            "target_key",
            "canonical_target",
            "anchor_target",
            "target",
            "semantic_target",
            "anchor_key",
            "base_key",
            "base_cluster_key",
            "expansion_key",
            "geometry_key",
            "start_location_key",
            "key",
            "anchor",
            "name",
        ):
            value = anchor.get(key)
            if type(value) is str and value.strip():
                return value.strip()
            nested = _parsed_target_name(value)
            if nested:
                return nested
        for key in ("base_location", "expansion", "location", "map_target"):
            nested = _parsed_target_name(anchor.get(key))
            if nested:
                return nested
        for key in ("position", "point", "anchor_position"):
            point = _extract_point(anchor.get(key))
            if point is not None:
                return _anchor_label(point)
        return ""
    if type(anchor) is str:
        return anchor.strip()
    key = _safe_getattr(anchor, "key")
    if type(key) is str and key.strip():
        return key.strip()
    target = _safe_getattr(anchor, "target")
    if type(target) is str and target.strip():
        return target.strip()
    name = _safe_getattr(anchor, "name")
    if type(name) is str and name.strip():
        return name.strip()
    return ""


def _parsed_target_name(value: object, *, _depth: int = 0) -> str:
    if _depth > 3 or value is None:
        return ""
    if type(value) is str:
        return value.strip()
    if isinstance(value, Mapping):
        for key in (
            "target_key",
            "canonical_target",
            "semantic_target",
            "semantic_key",
            "anchor_target",
            "base_key",
            "base_cluster_key",
            "expansion_key",
            "geometry_key",
            "start_location_key",
            "target",
            "key",
            "name",
        ):
            item = value.get(key)
            if type(item) is str and item.strip():
                return item.strip()
            nested = _parsed_target_name(item, _depth=_depth + 1)
            if nested:
                return nested
        for key in ("base_location", "expansion", "location", "map_target", "anchor"):
            nested = _parsed_target_name(value.get(key), _depth=_depth + 1)
            if nested:
                return nested
        return ""
    for attr_name in (
        "target_key",
        "canonical_target",
        "semantic_target",
        "base_key",
        "base_cluster_key",
        "expansion_key",
        "geometry_key",
        "start_location_key",
        "target",
        "key",
        "name",
    ):
        item = _safe_getattr(value, attr_name)
        if type(item) is str and item.strip():
            return item.strip()
        nested = _parsed_target_name(item, _depth=_depth + 1)
        if nested:
            return nested
    return ""


def _anchor_label(anchor: object) -> str:
    if type(anchor) is str:
        return anchor.strip() or "point"
    point = _extract_point(anchor)
    if point is not None:
        return f"point({point.x:g}, {point.y:g})"
    name = _anchor_name(anchor)
    return name or type(anchor).__name__


def _prefer_semantic_anchor_before_base_selection(anchor: object) -> bool:
    """Return True when an explicit semantic target should override base metadata."""

    if not isinstance(anchor, Mapping):
        return True
    if _placement_spatial_relation(anchor) == "on":
        return True
    for key in ("anchor_target", "semantic_target", "target"):
        value = anchor.get(key)
        if type(value) is str and value.strip() in {
            "self_geyser",
            "self_mineral_line",
            "enemy_mineral_line",
        }:
            return True
    return False


def _anchor_alternatives(resolver: SC2MapResolver) -> tuple[str, ...]:
    return _unique_strings(
        (
            *resolver.available_targets,
            *(cluster.key for cluster in resolver.geometry.base_clusters),
            *(observation.key for observation in _geometry_observations(resolver.geometry)),
        )
    )


def _placement_spatial_relation(anchor: object) -> str:
    if not isinstance(anchor, Mapping):
        return ""
    for key in ("spatial_relation", "relation", "relative_modifier"):
        value = anchor.get(key)
        if type(value) is str and value.strip():
            return value.strip()
    return ""


def _placement_search_radius(anchor: object) -> float:
    if not isinstance(anchor, Mapping):
        return SC2_NEAR_PLACEMENT_RADIUS
    for key in ("search_radius", "radius"):
        value = anchor.get(key)
        if _is_real_number(value) and float(value) > 0.0:
            return max(1.0, min(float(value), 20.0))
    return SC2_NEAR_PLACEMENT_RADIUS


def _relative_placement_position(
    resolver: SC2MapResolver,
    anchor_position: MapPoint | None,
    *,
    anchor: object,
    spatial_relation: str,
    search_radius: float,
) -> tuple[MapPoint | None, dict[str, object]]:
    """Resolve relative placement modifiers into a concrete candidate point."""

    if anchor_position is None:
        return (anchor_position, {})
    if _is_toward_spatial_relation(spatial_relation):
        (
            selected,
            origin_position,
            rejection_reasons,
        ) = _select_toward_placement_candidate(
            resolver,
            anchor,
            anchor_position,
            search_radius=search_radius,
        )
        placement_policy = {
            "spatial_relation": "toward",
            "anchor_position": anchor_position.to_dict(),
            "origin_position": (
                origin_position.to_dict() if origin_position is not None else None
            ),
            "search_radius": float(search_radius),
            "selected_tile": selected.to_dict(),
            "rejection_reasons": list(rejection_reasons),
            "search_result": _placement_search_result(
                selected,
                anchor_position=anchor_position,
                search_radius=search_radius,
                rejection_reasons=rejection_reasons,
                source="map resolver toward placement search",
            ),
        }
        return (selected, placement_policy)
    if _is_away_from_spatial_relation(spatial_relation):
        (
            selected,
            reference_position,
            rejection_reasons,
        ) = _select_away_from_placement_candidate(
            resolver,
            anchor,
            anchor_position,
            search_radius=search_radius,
        )
        placement_policy = {
            "spatial_relation": _canonical_away_from_relation(spatial_relation),
            "anchor_position": anchor_position.to_dict(),
            "reference_position": (
                reference_position.to_dict() if reference_position is not None else None
            ),
            "search_radius": float(search_radius),
            "selected_tile": selected.to_dict(),
            "rejection_reasons": list(rejection_reasons),
            "search_result": _placement_search_result(
                selected,
                anchor_position=anchor_position,
                search_radius=search_radius,
                rejection_reasons=rejection_reasons,
                source="map resolver away-from placement search",
            ),
        }
        return (selected, placement_policy)
    if not _is_near_spatial_relation(spatial_relation):
        return (anchor_position, {})
    selected, rejection_reasons = _select_near_placement_candidate(
        resolver,
        anchor_position,
        search_radius=search_radius,
    )
    placement_policy = {
        "spatial_relation": "near",
        "anchor_position": anchor_position.to_dict(),
        "search_radius": float(search_radius),
        "selected_tile": selected.to_dict(),
        "rejection_reasons": list(rejection_reasons),
        "search_result": _placement_search_result(
            selected,
            anchor_position=anchor_position,
            search_radius=search_radius,
            rejection_reasons=rejection_reasons,
            source="map resolver near placement search",
        ),
    }
    return (selected, placement_policy)


def _placement_search_result(
    selected: MapPoint | None,
    *,
    anchor_position: MapPoint,
    search_radius: float,
    rejection_reasons: Sequence[str],
    source: str,
) -> dict[str, object]:
    """Return a compact audit payload for resolver-side placement search."""

    selected_tile = selected.to_dict() if selected is not None else None
    rejected_count = len(rejection_reasons)
    selected_result = (
        {
            "tile": selected_tile,
            "reason_code": "",
            "rejected_before_selection": rejected_count,
            "distance_from_anchor": selected.distance_to(anchor_position),
            "source": source,
        }
        if selected is not None
        else None
    )
    no_match = (
        None
        if selected is not None
        else {
            "reason": "no placement candidate selected by map resolver",
            "reason_code": "no_placement_candidate",
            "search_radius": float(search_radius),
            "rejected_count": rejected_count,
        }
    )
    return {
        "status": "selected" if selected is not None else "no_match",
        "reason_code": "" if selected is not None else "no_placement_candidate",
        "selected_tile": selected_tile,
        "selected_result": selected_result,
        "no_match": no_match,
        "search_radius": float(search_radius),
        "rejection_reasons": list(rejection_reasons),
        "rejected_count": rejected_count,
    }


def _is_near_spatial_relation(spatial_relation: str) -> bool:
    compact = "".join(spatial_relation.casefold().split())
    return compact in {"near", "nearby", "근처", "근처에", "가까이", "주변"}


def _is_toward_spatial_relation(spatial_relation: str) -> bool:
    compact = "".join(spatial_relation.casefold().split())
    return compact in {
        "toward",
        "towards",
        "to",
        "쪽",
        "쪽으로",
        "쪽에",
        "방향",
        "방향으로",
    }


def _is_away_from_spatial_relation(spatial_relation: str) -> bool:
    compact = "".join(spatial_relation.casefold().split())
    return compact in {
        "away",
        "awayfrom",
        "away_from",
        "farfrom",
        "far_from",
        "멀리",
        "멀게",
        "떨어져",
        "떨어지게",
        "떨어진",
    }


def _canonical_away_from_relation(spatial_relation: str) -> str:
    compact = "".join(spatial_relation.casefold().split())
    if compact in {"farfrom", "far_from"}:
        return "far_from"
    return "away_from"


def _select_toward_placement_candidate(
    resolver: SC2MapResolver,
    anchor: object,
    anchor_position: MapPoint,
    *,
    search_radius: float,
) -> tuple[MapPoint, MapPoint | None, tuple[str, ...]]:
    origin_position = _placement_origin_position(resolver, anchor)
    if origin_position is None:
        return (
            anchor_position,
            None,
            ("missing actor/current position for toward placement; using anchor",),
        )
    dx = anchor_position.x - origin_position.x
    dy = anchor_position.y - origin_position.y
    distance = math.hypot(dx, dy)
    if distance <= 0.0:
        return (
            anchor_position,
            origin_position,
            ("actor/current position is already at the anchor; using anchor",),
        )
    step = min(float(search_radius), distance)
    selected = MapPoint(
        origin_position.x + (dx / distance) * step,
        origin_position.y + (dy / distance) * step,
    )
    return (selected, origin_position, ())


def _select_away_from_placement_candidate(
    resolver: SC2MapResolver,
    anchor: object,
    anchor_position: MapPoint,
    *,
    search_radius: float,
) -> tuple[MapPoint, MapPoint | None, tuple[str, ...]]:
    reference_position = _placement_away_reference_position(
        resolver,
        anchor,
        anchor_position,
    )
    if reference_position is None:
        selected, rejection_reasons = _select_near_placement_candidate(
            resolver,
            anchor_position,
            search_radius=search_radius,
        )
        return (
            selected,
            None,
            (
                "missing away-from reference position; using bounded near candidate",
                *rejection_reasons,
            ),
        )

    dx = reference_position.x - anchor_position.x
    dy = reference_position.y - anchor_position.y
    distance = math.hypot(dx, dy)
    if distance <= 0.0:
        selected, rejection_reasons = _select_near_placement_candidate(
            resolver,
            anchor_position,
            search_radius=search_radius,
        )
        return (
            selected,
            reference_position,
            (
                "away-from reference is already at the anchor; using bounded near candidate",
                *rejection_reasons,
            ),
        )

    obstacles = _placement_obstacle_points(resolver)
    rejection_reasons: list[str] = []
    for candidate in _directional_placement_candidates(
        anchor_position,
        dx=dx,
        dy=dy,
        search_radius=search_radius,
    ):
        rejection_reason = _near_candidate_rejection(
            candidate,
            anchor_position=anchor_position,
            obstacles=obstacles,
            search_radius=search_radius,
        )
        if rejection_reason:
            rejection_reasons.append(
                f"point({candidate.x:g}, {candidate.y:g}): {rejection_reason}"
            )
            continue
        return (candidate, reference_position, tuple(rejection_reasons))

    selected, near_rejection_reasons = _select_near_placement_candidate(
        resolver,
        anchor_position,
        search_radius=search_radius,
    )
    return (
        selected,
        reference_position,
        (
            *rejection_reasons,
            "all preferred away-from candidates failed map/pathing checks; "
            "using bounded near candidate for python-sc2 validation",
            *near_rejection_reasons,
        ),
    )


def _placement_away_reference_position(
    resolver: SC2MapResolver,
    anchor: object,
    anchor_position: MapPoint,
) -> MapPoint | None:
    explicit_direction = _placement_direction_position(resolver, anchor)
    if (
        explicit_direction is not None
        and explicit_direction.distance_to(anchor_position) > 0.0
    ):
        return explicit_direction

    origin_position = _placement_origin_position(resolver, anchor)
    if (
        origin_position is not None
        and origin_position.distance_to(anchor_position) > 0.0
    ):
        return origin_position

    for target in ("self_natural", "self_ramp", "enemy_main"):
        fallback = resolver.positions.get(target)
        if fallback is not None and fallback.distance_to(anchor_position) > 0.0:
            return fallback
    return None


def _placement_direction_position(
    resolver: SC2MapResolver,
    anchor: object,
) -> MapPoint | None:
    if not isinstance(anchor, Mapping):
        return None
    for key in ("direction_position", "target_position"):
        point = _extract_point(anchor.get(key))
        if point is not None:
            return point
    for key in ("direction_target", "direction"):
        value = anchor.get(key)
        if type(value) is str and value.strip():
            resolution = resolver.resolve(value)
            if resolution.available:
                return resolution.position
    return None


def _directional_placement_candidates(
    anchor_position: MapPoint,
    *,
    dx: float,
    dy: float,
    search_radius: float,
) -> tuple[MapPoint, ...]:
    distance = math.hypot(dx, dy)
    if distance <= 0.0:
        return ()
    unit_x = dx / distance
    unit_y = dy / distance
    perpendicular_x = -unit_y
    perpendicular_y = unit_x
    step = min(_NEAR_PLACEMENT_STEP, search_radius)
    candidates: list[MapPoint] = []
    for candidate_distance in _unique_numbers((step, search_radius)):
        side = candidate_distance / 2.0
        for forward, sideways in (
            (candidate_distance, 0.0),
            (candidate_distance, side),
            (candidate_distance, -side),
        ):
            candidates.append(
                MapPoint(
                    anchor_position.x + unit_x * forward + perpendicular_x * sideways,
                    anchor_position.y + unit_y * forward + perpendicular_y * sideways,
                )
            )
    return tuple(candidates)


def _placement_origin_position(
    resolver: SC2MapResolver,
    anchor: object,
) -> MapPoint | None:
    if isinstance(anchor, Mapping):
        for key in (
            "actor_position",
            "current_position",
            "from_position",
            "origin_position",
            "source_position",
        ):
            point = _extract_point(anchor.get(key))
            if point is not None:
                return point
        for key in (
            "actor_target",
            "current_target",
            "from_target",
            "origin_target",
            "source_target",
        ):
            value = anchor.get(key)
            if type(value) is str and value.strip():
                resolution = resolver.resolve(value)
                if resolution.available:
                    return resolution.position
    return resolver.positions.get("self_main")


def _select_near_placement_candidate(
    resolver: SC2MapResolver,
    anchor_position: MapPoint,
    *,
    search_radius: float,
) -> tuple[MapPoint, tuple[str, ...]]:
    obstacles = _placement_obstacle_points(resolver)
    rejection_reasons: list[str] = []
    for candidate in _near_placement_candidates(anchor_position, search_radius):
        rejection_reason = _near_candidate_rejection(
            candidate,
            anchor_position=anchor_position,
            obstacles=obstacles,
            search_radius=search_radius,
        )
        if rejection_reason:
            rejection_reasons.append(
                f"point({candidate.x:g}, {candidate.y:g}): {rejection_reason}"
            )
            continue
        return (candidate, tuple(rejection_reasons))
    # If every safe offset is blocked by observed geometry, preserve liveness by
    # returning the nearest bounded point. python-sc2 still performs final
    # placement validation around this ``near`` point.
    fallback = _near_placement_candidates(anchor_position, search_radius)[0]
    rejection_reasons.append(
        "all preferred near candidates overlap known geometry; "
        "falling back to first bounded candidate for python-sc2 validation"
    )
    return (fallback, tuple(rejection_reasons))


def _near_placement_candidates(
    anchor_position: MapPoint,
    search_radius: float,
) -> tuple[MapPoint, ...]:
    step = min(_NEAR_PLACEMENT_STEP, search_radius)
    distances = _unique_numbers((step, search_radius))
    candidates: list[MapPoint] = []
    for distance in distances:
        diagonal = distance / math.sqrt(2.0)
        for dx, dy in (
            (0.0, -distance),
            (distance, 0.0),
            (0.0, distance),
            (-distance, 0.0),
            (diagonal, -diagonal),
            (diagonal, diagonal),
            (-diagonal, diagonal),
            (-diagonal, -diagonal),
        ):
            candidates.append(
                MapPoint(anchor_position.x + dx, anchor_position.y + dy)
            )
    return tuple(candidates)


def _near_candidate_rejection(
    candidate: MapPoint,
    *,
    anchor_position: MapPoint,
    obstacles: Sequence[MapPoint],
    search_radius: float,
) -> str:
    distance = anchor_position.distance_to(candidate)
    if distance <= 0.0:
        return "candidate is the anchor"
    if distance > search_radius:
        return f"candidate is outside search radius {search_radius:g}"
    nearest_obstacle = min(
        (candidate.distance_to(obstacle) for obstacle in obstacles),
        default=math.inf,
    )
    if nearest_obstacle <= _PLACEMENT_OBSTACLE_RADIUS:
        return (
            "candidate overlaps observed base/resource/ramp geometry within "
            f"{_PLACEMENT_OBSTACLE_RADIUS:g}"
        )
    return ""


def _placement_obstacle_points(resolver: SC2MapResolver) -> tuple[MapPoint, ...]:
    return _unique_points(
        (
            *(cluster.anchor for cluster in resolver.geometry.base_clusters),
            *(
                observation.position
                for observation in _geometry_observations(resolver.geometry)
            ),
        )
    )


def _geometry_observations(
    geometry: MapGeometryInference,
) -> tuple[MapGeometryObservation, ...]:
    return (
        *geometry.start_locations,
        *geometry.ramps,
        *geometry.mineral_patches,
        *geometry.geysers,
    )


def _unique_strings(values: Sequence[str]) -> tuple[str, ...]:
    unique: list[str] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return tuple(unique)


def _unique_numbers(values: Sequence[float]) -> tuple[float, ...]:
    unique: list[float] = []
    for value in values:
        number = float(value)
        if not any(math.isclose(number, existing) for existing in unique):
            unique.append(number)
    return tuple(unique)


def _extract_point(candidate: object) -> MapPoint | None:
    """Duck-type one Point2/Unit-like object into a ``MapPoint``."""

    if candidate is None:
        return None
    if isinstance(candidate, MapPoint):
        return candidate
    point = _xy_point(candidate)
    if point is not None:
        return point
    point = _xy_point(_safe_getattr(candidate, "position"))
    if point is not None:
        return point
    if isinstance(candidate, Mapping):
        x = candidate.get("x")
        y = candidate.get("y")
        if _is_real_number(x) and _is_real_number(y):
            return MapPoint(float(x), float(y))
    if isinstance(candidate, (tuple, list)) and len(candidate) == 2:
        x, y = candidate
        if _is_real_number(x) and _is_real_number(y):
            return MapPoint(float(x), float(y))
    return None


def _xy_point(candidate: object) -> MapPoint | None:
    if candidate is None:
        return None
    if isinstance(candidate, Mapping):
        x = candidate.get("x")
        y = candidate.get("y")
        if _is_real_number(x) and _is_real_number(y):
            return MapPoint(float(x), float(y))
        return None
    x = _safe_getattr(candidate, "x")
    y = _safe_getattr(candidate, "y")
    if _is_real_number(x) and _is_real_number(y):
        return MapPoint(float(x), float(y))
    return None


def _semantic_anchor_lookup_name(
    anchor: str,
    catalog_entries: object = (),
) -> str:
    """Reduce Korean relative placement phrases to their semantic anchor."""

    stripped = anchor.strip()
    if _canonical_target_name(stripped, catalog_entries) in SC2_SUPPORTED_SEMANTIC_TARGETS:
        return stripped
    compact = "".join(stripped.casefold().split())
    for suffix in (
        "에서떨어지게",
        "에서떨어져",
        "에서떨어진",
        "에서멀게",
        "근처에",
        "근처",
        "쪽으로",
        "쪽에",
        "쪽",
    ):
        if compact.endswith(suffix):
            compact = compact[: -len(suffix)]
            break
    if compact in {"미네랄", "광물", "미네랄라인", "광물라인"}:
        return "self_mineral_line"
    if compact in {"가스", "가스통", "베스핀", "정제소"}:
        return "self_geyser"
    if compact in {"앞마당", "내추럴", "멀티", "확장"}:
        return "self_natural"
    if compact in {"본진", "메인"}:
        return "self_main"
    if compact in {"입구", "본진입구", "램프", "본진램프"}:
        return "self_ramp"
    return stripped


def _is_real_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and (
        math.isfinite(float(value))
    )


def _collect_points(candidates: object) -> list[MapPoint]:
    points: list[MapPoint] = []
    for candidate in _safe_iter(candidates):
        point = _extract_point(candidate)
        if point is not None:
            points.append(point)
    return points


def _coerce_geometry_observations(
    observations: Sequence[MapGeometryObservation],
    *,
    expected_kind: str,
    field_name: str,
) -> tuple[MapGeometryObservation, ...]:
    coerced = tuple(observations)
    for observation in coerced:
        if not isinstance(observation, MapGeometryObservation):
            raise TypeError(
                f"Map geometry {field_name} must contain MapGeometryObservation."
            )
        if observation.kind != expected_kind:
            raise ValueError(
                f"Map geometry {field_name} must contain {expected_kind} evidence."
            )
    return coerced


def _build_geometry_inference(
    *,
    self_main: MapPoint | None,
    enemy_starts: Sequence[MapPoint],
    enemy_main: MapPoint | None,
    visible_enemy_main: MapPoint | None,
    enemy_main_source: str,
    enemy_main_visibility: str,
    expansions: Sequence[MapPoint],
    self_natural: MapPoint | None,
    enemy_natural: MapPoint | None,
    visible_enemy_natural: MapPoint | None,
    enemy_natural_source: str,
    enemy_natural_visibility: str,
    self_ramp: MapPoint | None,
    enemy_ramp: MapPoint | None,
    scouted_enemy_front: MapPoint | None,
    mineral_points: Sequence[MapPoint],
    geyser_points: Sequence[MapPoint],
    own_townhall_points: Sequence[MapPoint],
) -> MapGeometryInference:
    """Build the explicit geometry evidence model behind semantic targets."""

    starts: list[MapGeometryObservation] = []
    if self_main is not None:
        starts.append(
            _geometry_observation(
                "start_location",
                "self_start_location",
                self_main,
                confidence=1.0,
                visibility="visible",
                source=_DEFAULT_CATALOG_SOURCE,
                metadata={"owner": "self"},
            )
        )
    for index, point in enumerate(enemy_starts, start=1):
        starts.append(
            _geometry_observation(
                "start_location",
                f"enemy_start_location_{index}",
                point,
                confidence=0.75,
                visibility="inferred",
                source=_DEFAULT_CATALOG_SOURCE,
                metadata={"owner": "enemy", "candidate_index": index},
            )
        )

    ramps: list[MapGeometryObservation] = []
    if self_ramp is not None:
        ramps.append(
            _geometry_observation(
                "ramp",
                "self_ramp",
                self_ramp,
                confidence=0.95,
                visibility="visible",
                source=_DEFAULT_CATALOG_SOURCE,
                metadata={"owner": "self"},
            )
        )
    if enemy_ramp is not None:
        enemy_ramp_visible = scouted_enemy_front is not None
        ramps.append(
            _geometry_observation(
                "ramp",
                "enemy_ramp",
                enemy_ramp,
                confidence=0.95 if enemy_ramp_visible else 0.7,
                visibility="visible" if enemy_ramp_visible else "inferred",
                source=(
                    _ENEMY_VISION_CATALOG_SOURCE
                    if enemy_ramp_visible
                    else _DEFAULT_CATALOG_SOURCE
                ),
                metadata={"owner": "enemy"},
            )
        )

    minerals = [
        _geometry_observation(
            "mineral_patch",
            f"mineral_patch_{index}",
            point,
            confidence=1.0,
            visibility=_resource_visibility(
                point,
                self_main=self_main,
                enemy_main=enemy_main,
                visible_enemy_main=visible_enemy_main,
            ),
            source=_DEFAULT_CATALOG_SOURCE,
            metadata={"resource": "mineral"},
        )
        for index, point in enumerate(mineral_points, start=1)
    ]
    geysers = [
        _geometry_observation(
            "geyser",
            f"geyser_{index}",
            point,
            confidence=1.0,
            visibility=_resource_visibility(
                point,
                self_main=self_main,
                enemy_main=enemy_main,
                visible_enemy_main=visible_enemy_main,
            ),
            source=_DEFAULT_CATALOG_SOURCE,
            metadata={"resource": "vespene"},
        )
        for index, point in enumerate(geyser_points, start=1)
    ]

    cluster_anchors = _unique_points((*expansions, self_main, enemy_main))
    clusters: list[MapBaseCluster] = []
    for index, anchor in enumerate(cluster_anchors, start=1):
        key, confidence, visibility, source, owner = _base_cluster_identity(
            anchor,
            neutral_index=index,
            self_main=self_main,
            enemy_main=enemy_main,
            visible_enemy_main=visible_enemy_main,
            enemy_main_source=enemy_main_source,
            enemy_main_visibility=enemy_main_visibility,
            self_natural=self_natural,
            enemy_natural=enemy_natural,
            visible_enemy_natural=visible_enemy_natural,
            enemy_natural_source=enemy_natural_source,
            enemy_natural_visibility=enemy_natural_visibility,
        )
        cluster_minerals = tuple(
            observation
            for observation in minerals
            if observation.position.distance_to(anchor) <= SC2_BASE_CLUSTER_RESOURCE_RADIUS
        )
        cluster_geysers = tuple(
            observation
            for observation in geysers
            if observation.position.distance_to(anchor) <= SC2_BASE_CLUSTER_RESOURCE_RADIUS
        )
        own_townhall_orders = tuple(
            townhall_index
            for townhall_index, townhall_point in enumerate(own_townhall_points, start=1)
            if townhall_point.distance_to(anchor) <= SC2_BASE_CLUSTER_RESOURCE_RADIUS
        )
        if own_townhall_orders and owner != "enemy":
            owner = "self"
        clusters.append(
            MapBaseCluster(
                key=key,
                anchor=anchor,
                confidence=confidence,
                visibility="visible" if own_townhall_orders else visibility,
                source=_OWN_TOWNHALL_SOURCE if own_townhall_orders else source,
                mineral_patches=cluster_minerals,
                geysers=cluster_geysers,
                ramp=_nearest_observation(ramps, anchor, max_distance=20.0),
                metadata={
                    "owner": owner,
                    "mineral_patch_count": len(cluster_minerals),
                    "geyser_count": len(cluster_geysers),
                    **(
                        {
                            "own_townhall_order": max(own_townhall_orders),
                            "own_townhall_count": len(own_townhall_orders),
                        }
                        if own_townhall_orders
                        else {}
                    ),
                },
            )
        )

    player_main_base = next(
        (cluster for cluster in clusters if cluster.key == "self_main"),
        None,
    )
    return MapGeometryInference(
        start_locations=starts,
        base_clusters=clusters,
        player_main_base=player_main_base,
        ramps=ramps,
        mineral_patches=minerals,
        geysers=geysers,
    )


def _geometry_observation(
    kind: str,
    key: str,
    point: MapPoint,
    *,
    confidence: float,
    visibility: str,
    source: str,
    metadata: Mapping[str, object] | None = None,
) -> MapGeometryObservation:
    return MapGeometryObservation(
        kind=kind,
        key=key,
        position=point,
        confidence=confidence,
        visibility=visibility,
        source=source,
        metadata=metadata or {},
    )


def _resource_visibility(
    point: MapPoint,
    *,
    self_main: MapPoint | None,
    enemy_main: MapPoint | None,
    visible_enemy_main: MapPoint | None,
) -> str:
    if self_main is not None and point.distance_to(self_main) <= SC2_BASE_CLUSTER_RESOURCE_RADIUS:
        return "visible"
    if (
        visible_enemy_main is not None
        and enemy_main is not None
        and point.distance_to(enemy_main) <= SC2_BASE_CLUSTER_RESOURCE_RADIUS
    ):
        return "visible"
    return "inferred"


def _unique_points(points: Sequence[MapPoint | None]) -> tuple[MapPoint, ...]:
    unique: list[MapPoint] = []
    for point in points:
        if point is None:
            continue
        if any(point.distance_to(existing) <= _MAIN_EXCLUSION_RADIUS for existing in unique):
            continue
        unique.append(point)
    return tuple(unique)


def _base_cluster_identity(
    anchor: MapPoint,
    *,
    neutral_index: int,
    self_main: MapPoint | None,
    enemy_main: MapPoint | None,
    visible_enemy_main: MapPoint | None,
    enemy_main_source: str,
    enemy_main_visibility: str,
    self_natural: MapPoint | None,
    enemy_natural: MapPoint | None,
    visible_enemy_natural: MapPoint | None,
    enemy_natural_source: str,
    enemy_natural_visibility: str,
) -> tuple[str, float, str, str, str]:
    if self_main is not None and anchor.distance_to(self_main) <= _MAIN_EXCLUSION_RADIUS:
        return ("self_main", 1.0, "visible", _DEFAULT_CATALOG_SOURCE, "self")
    if (
        self_natural is not None
        and anchor.distance_to(self_natural) <= _MAIN_EXCLUSION_RADIUS
    ):
        return ("self_natural", 0.85, "inferred", _DEFAULT_CATALOG_SOURCE, "self")
    if enemy_main is not None and anchor.distance_to(enemy_main) <= _MAIN_EXCLUSION_RADIUS:
        if visible_enemy_main is not None:
            return (
                "enemy_main",
                0.95,
                "visible",
                _ENEMY_VISION_CATALOG_SOURCE,
                "enemy",
            )
        return (
            "enemy_main",
            0.85 if enemy_main_source == _ENEMY_VISION_CATALOG_SOURCE else 0.75,
            enemy_main_visibility,
            enemy_main_source,
            "enemy",
        )
    if (
        enemy_natural is not None
        and anchor.distance_to(enemy_natural) <= _MAIN_EXCLUSION_RADIUS
    ):
        if visible_enemy_natural is not None:
            return (
                "enemy_natural",
                0.9,
                "visible",
                _ENEMY_VISION_CATALOG_SOURCE,
                "enemy",
            )
        return (
            "enemy_natural",
            0.75 if enemy_natural_source == _ENEMY_VISION_CATALOG_SOURCE else 0.65,
            enemy_natural_visibility,
            enemy_natural_source,
            "enemy",
        )
    return (
        f"neutral_base_{neutral_index}",
        0.55,
        "inferred",
        _DEFAULT_CATALOG_SOURCE,
        "neutral",
    )


def _nearest_observation(
    observations: Sequence[MapGeometryObservation],
    anchor: MapPoint,
    *,
    max_distance: float,
) -> MapGeometryObservation | None:
    if not observations:
        return None
    nearest = min(
        observations,
        key=lambda observation: (
            anchor.distance_to(observation.position),
            observation.position.x,
            observation.position.y,
        ),
    )
    if anchor.distance_to(nearest.position) > max_distance:
        return None
    return nearest


def _derive_visible_enemy_main(
    bot: object,
    enemy_starts: Sequence[MapPoint],
) -> MapPoint | None:
    townhall_points = [
        point
        for structure in _safe_iter(_safe_getattr(bot, "enemy_structures"))
        if _unit_type_name(structure) in _ENEMY_TOWNHALL_TYPE_NAMES
        for point in (_extract_point(structure),)
        if point is not None
    ]
    if not townhall_points:
        return None
    if enemy_starts:
        return _closest_point(townhall_points, enemy_starts[0])
    if len(townhall_points) == 1:
        return townhall_points[0]
    return None


def _derive_history_enemy_main(
    bot: object,
    enemy_starts: Sequence[MapPoint],
    expansions: Sequence[MapPoint],
) -> MapPoint | None:
    explicit = _derive_named_or_iterated_observation_point(
        bot,
        _ENEMY_MAIN_HISTORY_ATTRS,
    )
    if explicit is not None:
        return _snap_to_known_base(explicit, expansions, enemy_starts) or explicit

    remembered_townhalls = tuple(
        point
        for attr_name in _LAST_SEEN_ENEMY_STRUCTURE_ATTRS
        for structure in _safe_iter(_safe_getattr(bot, attr_name))
        if _unit_type_name(structure) in _ENEMY_TOWNHALL_TYPE_NAMES
        for point in (_extract_point(structure),)
        if point is not None
    )
    if remembered_townhalls:
        return _single_history_base_candidate(
            remembered_townhalls,
            expansions,
            enemy_starts,
            allow_unsnapped=True,
        )

    remembered_structures = _collect_history_points(
        bot,
        _LAST_SEEN_ENEMY_STRUCTURE_ATTRS,
    )
    remembered_units = _collect_history_points(bot, _LAST_SEEN_ENEMY_UNIT_ATTRS)
    creep_points = _collect_history_points(bot, _ENEMY_CREEP_ATTRS)
    return _single_history_base_candidate(
        (*remembered_structures, *remembered_units, *creep_points),
        expansions,
        enemy_starts,
        allow_unsnapped=False,
    )


def _derive_visible_enemy_natural(
    bot: object,
    enemy_main: MapPoint | None,
    inferred_enemy_natural: MapPoint | None,
) -> MapPoint | None:
    if enemy_main is None or inferred_enemy_natural is None:
        return None
    for structure in _safe_iter(_safe_getattr(bot, "enemy_structures")):
        if _unit_type_name(structure) not in _ENEMY_TOWNHALL_TYPE_NAMES:
            continue
        point = _extract_point(structure)
        if point is None:
            continue
        if point.distance_to(enemy_main) <= _MAIN_EXCLUSION_RADIUS:
            continue
        if point.distance_to(inferred_enemy_natural) <= _ENEMY_NATURAL_DISCOVERY_RADIUS:
            return inferred_enemy_natural
    return None


def _derive_third_expansion(
    expansions: Sequence[MapPoint],
    *,
    main: MapPoint | None,
    natural: MapPoint | None,
    excluded: Sequence[MapPoint | None] = (),
) -> MapPoint | None:
    """Return the nearest expansion beyond the natural for a known main."""

    if main is None or natural is None:
        return None
    excluded_points = tuple(point for point in excluded if point is not None)
    candidates = [
        point
        for point in expansions
        if point.distance_to(main) > _MAIN_EXCLUSION_RADIUS
        and point.distance_to(natural) > _MAIN_EXCLUSION_RADIUS
        and all(
            point.distance_to(excluded_point) > _MAIN_EXCLUSION_RADIUS
            for excluded_point in excluded_points
        )
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda point: (point.distance_to(main), point.x, point.y),
    )


def _derive_named_observation_point(
    bot: object,
    attr_names: Sequence[str],
) -> MapPoint | None:
    for attr_name in attr_names:
        point = _extract_point(_safe_getattr(bot, attr_name))
        if point is not None:
            return point
    return None


def _derive_named_or_iterated_observation_point(
    bot: object,
    attr_names: Sequence[str],
) -> MapPoint | None:
    for attr_name in attr_names:
        value = _safe_getattr(bot, attr_name)
        point = _extract_point(value)
        if point is not None:
            return point
        for item in _safe_iter(value):
            point = _extract_point(item)
            if point is not None:
                return point
    return None


def _derive_last_seen_enemy_area(bot: object) -> MapPoint | None:
    explicit = _derive_named_observation_point(bot, _LAST_SEEN_ENEMY_AREA_ATTRS)
    if explicit is not None:
        return explicit
    enemy_points = [
        *_collect_history_points(bot, ("enemy_units", "enemy_structures")),
        *_collect_history_points(bot, _LAST_SEEN_ENEMY_UNIT_ATTRS),
        *_collect_history_points(bot, _LAST_SEEN_ENEMY_STRUCTURE_ATTRS),
        *_collect_history_points(bot, _ENEMY_CREEP_ATTRS),
        *_collect_history_points(bot, _ENEMY_MAIN_HISTORY_ATTRS),
    ]
    if not enemy_points:
        return None
    return MapPoint(
        sum(point.x for point in enemy_points) / len(enemy_points),
        sum(point.y for point in enemy_points) / len(enemy_points),
    )


def _collect_history_points(
    bot: object,
    attr_names: Sequence[str],
) -> tuple[MapPoint, ...]:
    return tuple(
        point
        for attr_name in attr_names
        for entry in _history_entries(_safe_getattr(bot, attr_name))
        for point in (_extract_point(entry),)
        if point is not None
    )


def _history_entries(value: object) -> tuple[object, ...]:
    direct = _extract_point(value)
    if direct is not None:
        return (direct,)
    return tuple(_safe_iter(value))


def _single_history_base_candidate(
    evidence_points: Sequence[MapPoint],
    expansions: Sequence[MapPoint],
    enemy_starts: Sequence[MapPoint],
    *,
    allow_unsnapped: bool,
) -> MapPoint | None:
    snapped = tuple(
        point
        for evidence_point in evidence_points
        for point in (_snap_to_known_base(evidence_point, expansions, enemy_starts),)
        if point is not None
    )
    candidates = _unique_points(snapped)
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        return None
    if allow_unsnapped:
        unique_evidence = _unique_points(tuple(evidence_points))
        if len(unique_evidence) == 1:
            return unique_evidence[0]
    return None


def _snap_to_known_base(
    evidence_point: MapPoint,
    expansions: Sequence[MapPoint],
    enemy_starts: Sequence[MapPoint],
) -> MapPoint | None:
    known_bases = _unique_points((*expansions, *enemy_starts))
    if not known_bases:
        return None
    nearest = min(
        known_bases,
        key=lambda point: (evidence_point.distance_to(point), point.x, point.y),
    )
    if evidence_point.distance_to(nearest) <= _BASE_HISTORY_EXPANSION_MATCH_RADIUS:
        return nearest
    return None


def _derive_own_townhall_points(bot: object) -> tuple[MapPoint, ...]:
    """Return observed own townhall positions in BotAI collection order."""

    points: list[MapPoint] = []
    for attr_name in ("townhalls", "structures", "owned_townhalls"):
        for structure in _safe_iter(_safe_getattr(bot, attr_name)):
            if _unit_type_name(structure) not in _ENEMY_TOWNHALL_TYPE_NAMES:
                continue
            point = _extract_point(structure)
            if point is not None:
                points.append(point)
    return _unique_points(points)


def _unit_type_name(entry: object) -> str | None:
    normalized = _normalized_type_name(_safe_getattr(entry, "name"))
    if normalized is not None:
        return normalized
    type_id = _safe_getattr(entry, "type_id")
    if type_id is None:
        return None
    return _normalized_type_name(_safe_getattr(type_id, "name"))


def _normalized_type_name(value: object) -> str | None:
    if type(value) is not str:
        return None
    normalized = "".join(value.split()).upper()
    return normalized or None


def _closest_point(
    points: Sequence[MapPoint],
    anchor: MapPoint | None,
) -> MapPoint | None:
    if anchor is None or not points:
        return None
    return min(
        points,
        key=lambda point: (anchor.distance_to(point), point.x, point.y),
    )


def _unambiguous_enemy_start(enemy_starts: Sequence[MapPoint]) -> MapPoint | None:
    if len(enemy_starts) == 1:
        return enemy_starts[0]
    return None


def _nearest_natural_expansion(
    expansions: Sequence[MapPoint],
    main: MapPoint | None,
    *,
    require_unambiguous: bool,
) -> MapPoint | None:
    if main is None:
        return None
    candidates = _sorted_points_by_distance(
        [
            point
            for point in _unique_points(expansions)
            if _NATURAL_MIN_DISTANCE
            <= point.distance_to(main)
            <= _NATURAL_MAX_DISTANCE
        ],
        main,
    )
    if not require_unambiguous:
        return candidates[0] if candidates else None
    return _single_clear_nearest(
        candidates,
        main,
        ambiguity_margin=_NATURAL_AMBIGUITY_MARGIN,
    )


def _derive_self_ramp(bot: object, self_main: MapPoint | None) -> MapPoint | None:
    ramp = _safe_getattr(bot, "main_base_ramp")
    if ramp is None or self_main is None:
        return None
    point = _extract_point(_safe_getattr(ramp, "top_center"))
    if point is None:
        point = _extract_point(_safe_getattr(ramp, "barracks_correct_placement"))
    if point is None or point.distance_to(self_main) > _RAMP_MAX_DISTANCE:
        return None
    return point


def _derive_scouted_enemy_front(bot: object) -> MapPoint | None:
    for attr_name in _ENEMY_FRONT_SCOUTING_ATTRS:
        point = _extract_point(_safe_getattr(bot, attr_name))
        if point is not None:
            return point
    return None


def _derive_enemy_ramp(bot: object, enemy_main: MapPoint | None) -> MapPoint | None:
    if enemy_main is None:
        return None
    game_info = _safe_getattr(bot, "game_info")
    if game_info is None:
        return None
    ramp_candidates: list[MapPoint] = []
    for ramp in _safe_iter(_safe_getattr(game_info, "map_ramps")):
        point = _extract_point(_safe_getattr(ramp, "top_center"))
        if (
            point is not None
            and point.distance_to(enemy_main) <= _RAMP_MAX_DISTANCE
        ):
            ramp_candidates.append(point)
    ramp_tops = _sorted_points_by_distance(
        _unique_points(ramp_candidates),
        enemy_main,
    )
    return _single_clear_nearest(
        ramp_tops,
        enemy_main,
        ambiguity_margin=_RAMP_AMBIGUITY_MARGIN,
    )


def _sorted_points_by_distance(
    points: Sequence[MapPoint],
    anchor: MapPoint,
) -> list[MapPoint]:
    return sorted(
        points,
        key=lambda point: (anchor.distance_to(point), point.x, point.y),
    )


def _single_clear_nearest(
    candidates: Sequence[MapPoint],
    anchor: MapPoint,
    *,
    ambiguity_margin: float,
) -> MapPoint | None:
    if not candidates:
        return None
    nearest = candidates[0]
    if len(candidates) == 1:
        return nearest
    if (
        anchor.distance_to(candidates[1]) - anchor.distance_to(nearest)
        <= ambiguity_margin
    ):
        return None
    return nearest


def _mineral_line_from_validated_geometry(
    geometry: MapGeometryInference,
    *,
    base_key: str,
    target: str,
) -> tuple[MapPoint | None, str]:
    cluster, reason = _validated_base_cluster(geometry, base_key=base_key, target=target)
    if cluster is None:
        return (None, reason)
    minerals = tuple(
        observation
        for observation in cluster.mineral_patches
        if observation.position.distance_to(cluster.anchor) <= SC2_MINERAL_LINE_RADIUS
    )
    if not minerals:
        return (
            None,
            f"Cannot derive {target}: validated {base_key} base cluster has no "
            "mineral_field/mineral_patch resources within "
            f"{SC2_MINERAL_LINE_RADIUS:g}.",
        )
    shared = _shared_resource_keys(geometry, cluster=cluster, resources=minerals)
    if shared:
        return (
            None,
            f"Cannot derive {target}: ambiguous base/resource geometry; "
            f"mineral_patch resources {', '.join(shared)} also attach to another "
            "base cluster.",
        )
    return (_resource_centroid(minerals), "")


def _geyser_from_validated_geometry(
    geometry: MapGeometryInference,
    *,
    base_key: str,
    target: str,
) -> tuple[MapPoint | None, str]:
    cluster, reason = _validated_base_cluster(geometry, base_key=base_key, target=target)
    if cluster is None:
        return (None, reason)
    geysers = tuple(cluster.geysers)
    if not geysers:
        return (
            None,
            f"Cannot derive {target}: validated {base_key} base cluster has no "
            "vespene_geyser resources.",
        )
    shared = _shared_resource_keys(geometry, cluster=cluster, resources=geysers)
    if shared:
        return (
            None,
            f"Cannot derive {target}: ambiguous base/resource geometry; "
            f"geyser resources {', '.join(shared)} also attach to another base "
            "cluster.",
        )
    nearest = min(
        geysers,
        key=lambda observation: (
            cluster.anchor.distance_to(observation.position),
            observation.position.x,
            observation.position.y,
        ),
    )
    return (nearest.position, "")


def _validated_base_cluster(
    geometry: MapGeometryInference,
    *,
    base_key: str,
    target: str,
) -> tuple[MapBaseCluster | None, str]:
    clusters = tuple(
        cluster for cluster in geometry.base_clusters if cluster.key == base_key
    )
    if not clusters:
        return (
            None,
            f"Cannot derive {target}: validated base/resource geometry has no "
            f"{base_key} base cluster.",
        )
    if len(clusters) > 1:
        return (
            None,
            f"Cannot derive {target}: ambiguous base/resource geometry has "
            f"{len(clusters)} {base_key} base clusters.",
        )
    return (clusters[0], "")


def _shared_resource_keys(
    geometry: MapGeometryInference,
    *,
    cluster: MapBaseCluster,
    resources: Sequence[MapGeometryObservation],
) -> tuple[str, ...]:
    shared: list[str] = []
    for resource in resources:
        owners = tuple(
            candidate.key
            for candidate in geometry.base_clusters
            if candidate.key != cluster.key
            and any(
                resource.key == candidate_resource.key
                for candidate_resource in (
                    *candidate.mineral_patches,
                    *candidate.geysers,
                )
            )
        )
        if owners:
            shared.append(resource.key)
    return tuple(shared)


def _resource_centroid(resources: Sequence[MapGeometryObservation]) -> MapPoint:
    return MapPoint(
        sum(observation.position.x for observation in resources) / len(resources),
        sum(observation.position.y for observation in resources) / len(resources),
    )


def _mineral_line_centroid(
    mineral_points: Sequence[MapPoint],
    main: MapPoint | None,
) -> MapPoint | None:
    if main is None:
        return None
    nearby = [
        point
        for point in mineral_points
        if point.distance_to(main) <= SC2_MINERAL_LINE_RADIUS
    ]
    if not nearby:
        return None
    return MapPoint(
        sum(point.x for point in nearby) / len(nearby),
        sum(point.y for point in nearby) / len(nearby),
    )
