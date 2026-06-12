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

from starcraft_commander.sc2_executor import SC2_TARGET_ALIASES


SC2_SEMANTIC_TARGETS: Final[tuple[str, ...]] = (
    "self_main",
    "self_ramp",
    "self_natural",
    "enemy_main",
    "enemy_ramp",
    "enemy_natural",
    "enemy_mineral_line",
)
"""The seven handoff Step 4 semantic map targets, in canonical order."""

SC2_EXTRA_SEMANTIC_TARGETS: Final[tuple[str, ...]] = (
    "self_mineral_line",
    "self_geyser",
)
"""Best-effort extra semantic targets the planner may emit."""

SC2_SUPPORTED_SEMANTIC_TARGETS: Final[tuple[str, ...]] = (
    SC2_SEMANTIC_TARGETS + SC2_EXTRA_SEMANTIC_TARGETS
)
"""Full supported semantic map-target vocabulary, in canonical order."""

SC2_MINERAL_LINE_RADIUS: Final[float] = 10.0
"""Maximum distance from a main base for minerals to count as its line."""

_MAIN_EXCLUSION_RADIUS: Final[float] = 1.0
"""Expansion entries within this distance of a main are treated as the main."""

_UNDERIVED_TARGET_REASON: Final[str] = (
    "Target was not derived from BotAI map data for this resolver."
)


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
    alternatives: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.target).strip():
            raise ValueError("Map target resolution target must be non-empty.")
        alternatives = tuple(str(item) for item in self.alternatives)
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
        else:
            if self.position is not None:
                raise ValueError(
                    "Unavailable map target resolution must not carry a position."
                )
            if not str(self.reason).strip():
                raise ValueError(
                    "Unavailable map target resolution must carry a non-empty reason."
                )
        object.__setattr__(self, "target", str(self.target))
        object.__setattr__(self, "available", bool(self.available))
        object.__setattr__(self, "reason", str(self.reason))
        object.__setattr__(self, "alternatives", alternatives)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready resolution payload."""

        return {
            "target": self.target,
            "available": self.available,
            "position": self.position.to_dict() if self.position else None,
            "reason": self.reason,
            "alternatives": list(self.alternatives),
        }


@runtime_checkable
class SC2MapResolverInterface(Protocol):
    """Boundary from semantic map-target names to map coordinates."""

    def resolve(self, target_name: str) -> MapTargetResolution:
        """Resolve one semantic target name into a structured resolution."""

    def resolve_point(self, target_name: str) -> MapPoint | None:
        """Resolve one semantic target name into a point, or ``None``."""


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

        ordered_positions: dict[str, MapPoint] = {}
        ordered_reasons: dict[str, str] = {}
        for name in SC2_SUPPORTED_SEMANTIC_TARGETS:
            if name in positions:
                ordered_positions[name] = positions[name]
            elif name in reasons:
                ordered_reasons[name] = reasons[name]
            else:
                ordered_reasons[name] = _UNDERIVED_TARGET_REASON

        object.__setattr__(self, "positions", ordered_positions)
        object.__setattr__(self, "unavailable_reasons", ordered_reasons)

    @classmethod
    def from_bot(cls, bot: object) -> "SC2MapResolver":
        """Derive the semantic target registry once from a BotAI-like object.

        Positions are read via ``.x``/``.y`` or ``.position.x``/``.position.y``
        duck-typing. Missing or broken attributes never raise: each underivable
        target becomes an explicit unavailable entry with a reason.
        """

        positions: dict[str, MapPoint] = {}
        reasons: dict[str, str] = {}

        def register(target: str, point: MapPoint | None, reason: str) -> None:
            if point is not None:
                positions[target] = point
            else:
                reasons[target] = reason

        self_main = _extract_point(_safe_getattr(bot, "start_location"))
        register(
            "self_main",
            self_main,
            "BotAI start_location is missing or not point-like.",
        )

        enemy_starts = _collect_points(_safe_getattr(bot, "enemy_start_locations"))
        enemy_main = enemy_starts[0] if enemy_starts else None
        register(
            "enemy_main",
            enemy_main,
            "BotAI enemy_start_locations has no point-like entry.",
        )

        register(
            "self_ramp",
            _derive_self_ramp(bot),
            "BotAI main_base_ramp has no point-like top_center or "
            "barracks_correct_placement.",
        )

        expansions = _collect_points(_safe_getattr(bot, "expansion_locations_list"))
        register(
            "self_natural",
            _closest_expansion(expansions, self_main),
            "Cannot derive self_natural: requires a point-like start_location and "
            "a BotAI expansion_locations_list entry distinct from the main base."
            if self_main is None or not expansions
            else "BotAI expansion_locations_list has no expansion distinct from "
            "the own main base.",
        )
        register(
            "enemy_natural",
            _closest_expansion(expansions, enemy_main),
            "Cannot derive enemy_natural: requires a point-like enemy main and "
            "a BotAI expansion_locations_list entry distinct from the enemy main."
            if enemy_main is None or not expansions
            else "BotAI expansion_locations_list has no expansion distinct from "
            "the enemy main base.",
        )

        register(
            "enemy_ramp",
            _derive_enemy_ramp(bot, enemy_main),
            "Cannot derive enemy_ramp: requires a point-like enemy main and a "
            "BotAI game_info.map_ramps entry with a point-like top_center.",
        )

        mineral_points = _collect_points(_safe_getattr(bot, "mineral_field"))
        register(
            "enemy_mineral_line",
            _mineral_line_centroid(mineral_points, enemy_main),
            "Cannot derive enemy_mineral_line: no point-like mineral_field units "
            f"within {SC2_MINERAL_LINE_RADIUS} of the enemy main.",
        )
        register(
            "self_mineral_line",
            _mineral_line_centroid(mineral_points, self_main),
            "Cannot derive self_mineral_line: no point-like mineral_field units "
            f"within {SC2_MINERAL_LINE_RADIUS} of the own main.",
        )

        geyser_points = _collect_points(_safe_getattr(bot, "vespene_geyser"))
        register(
            "self_geyser",
            _closest_point(geyser_points, self_main),
            "Cannot derive self_geyser: requires a point-like start_location and "
            "at least one point-like vespene_geyser unit.",
        )

        return cls(positions=positions, unavailable_reasons=reasons)

    @property
    def available_targets(self) -> tuple[str, ...]:
        """Currently resolvable semantic targets, in canonical order."""

        return tuple(self.positions)

    @property
    def unavailable_targets(self) -> tuple[str, ...]:
        """Known-but-underivable semantic targets, in canonical order."""

        return tuple(self.unavailable_reasons)

    def resolve(self, target_name: str) -> MapTargetResolution:
        """Resolve one semantic target name into a structured resolution.

        Accepts canonical names plus the human phrases from
        ``SC2_TARGET_ALIASES``. Unknown names yield an unavailable resolution
        whose reason marks them unsupported and whose alternatives list the
        currently available targets.
        """

        requested = str(target_name).strip()
        canonical = _canonical_target_name(requested)
        if canonical not in SC2_SUPPORTED_SEMANTIC_TARGETS:
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
            )
        return MapTargetResolution(
            target=canonical,
            available=False,
            position=None,
            reason=self.unavailable_reasons.get(canonical, _UNDERIVED_TARGET_REASON),
            alternatives=self.available_targets,
        )

    def resolve_point(self, target_name: str) -> MapPoint | None:
        """Resolve one semantic target name into a point, or ``None``."""

        return self.resolve(target_name).position

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready snapshot of the full target registry."""

        return {
            "available_targets": list(self.available_targets),
            "positions": {
                name: point.to_dict() for name, point in self.positions.items()
            },
            "unavailable": dict(self.unavailable_reasons),
        }


def _canonical_target_name(target_name: str) -> str:
    normalized = target_name.strip().lower()
    return SC2_TARGET_ALIASES.get(normalized, normalized)


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
    if isinstance(candidate, (tuple, list)) and len(candidate) == 2:
        x, y = candidate
        if _is_real_number(x) and _is_real_number(y):
            return MapPoint(float(x), float(y))
    return None


def _xy_point(candidate: object) -> MapPoint | None:
    if candidate is None:
        return None
    x = _safe_getattr(candidate, "x")
    y = _safe_getattr(candidate, "y")
    if _is_real_number(x) and _is_real_number(y):
        return MapPoint(float(x), float(y))
    return None


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


def _closest_point(points: Sequence[MapPoint], anchor: MapPoint | None) -> MapPoint | None:
    if anchor is None or not points:
        return None
    return min(points, key=lambda point: (anchor.distance_to(point), point.x, point.y))


def _closest_expansion(
    expansions: Sequence[MapPoint],
    main: MapPoint | None,
) -> MapPoint | None:
    if main is None:
        return None
    candidates = [
        point
        for point in expansions
        if point.distance_to(main) > _MAIN_EXCLUSION_RADIUS
    ]
    return _closest_point(candidates, main)


def _derive_self_ramp(bot: object) -> MapPoint | None:
    ramp = _safe_getattr(bot, "main_base_ramp")
    if ramp is None:
        return None
    point = _extract_point(_safe_getattr(ramp, "top_center"))
    if point is not None:
        return point
    return _extract_point(_safe_getattr(ramp, "barracks_correct_placement"))


def _derive_enemy_ramp(bot: object, enemy_main: MapPoint | None) -> MapPoint | None:
    if enemy_main is None:
        return None
    game_info = _safe_getattr(bot, "game_info")
    if game_info is None:
        return None
    ramp_tops = [
        point
        for point in (
            _extract_point(_safe_getattr(ramp, "top_center"))
            for ramp in _safe_iter(_safe_getattr(game_info, "map_ramps"))
        )
        if point is not None
    ]
    return _closest_point(ramp_tops, enemy_main)


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
