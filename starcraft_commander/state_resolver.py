"""Real StarCraft II observation resolution into commander semantic state.

This module is intentionally importable without StarCraft II or python-sc2
installed. ``SC2StateResolver`` duck-types a python-sc2 ``BotAI``-like object
through ``getattr`` only and never raises on weird runtime objects: numeric
fields degrade to ``0``, count mappings degrade to empty, and every missing or
unreadable bot attribute is recorded as an observation note so downstream
validators can treat incomplete observations conservatively.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Final, Protocol, runtime_checkable


SC2_WORKER_TYPE_NAME: Final[str] = "SCV"
"""Terran MVP worker type name used by idle-worker and army-count fallbacks."""

_NON_NEGATIVE_INT_FIELDS: Final[tuple[str, ...]] = (
    "minerals",
    "vespene",
    "supply_used",
    "supply_cap",
    "supply_left",
    "idle_worker_count",
    "army_count",
    "game_loop",
)
"""SC2CommanderState int fields validated as non-negative."""

_COUNT_MAPPING_FIELDS: Final[tuple[str, ...]] = (
    "own_units",
    "own_structures",
    "structures_in_progress",
    "visible_enemy_units",
    "visible_enemy_structures",
)
"""SC2CommanderState mapping fields validated as name -> non-negative count."""


class _Sentinel:
    """Internal marker distinguishing missing from unreadable attributes."""

    __slots__ = ("_label",)

    def __init__(self, label: str) -> None:
        self._label = label

    def __repr__(self) -> str:
        return f"<{self._label}>"


_MISSING: Final[_Sentinel] = _Sentinel("missing attribute")
_UNREADABLE: Final[_Sentinel] = _Sentinel("unreadable attribute")


@dataclass(frozen=True)
class SC2CommanderState:
    """Semantic commander snapshot resolved from raw BotAI observations.

    Unit and structure counts are keyed by UPPERCASE type names with spaces
    removed (for example ``SCV``, ``MARINE``, ``COMMANDCENTER``). Non-empty
    ``observation_notes`` mean at least one bot attribute was missing or
    unreadable, so downstream validators should stay conservative.
    """

    minerals: int = 0
    vespene: int = 0
    supply_used: int = 0
    supply_cap: int = 0
    supply_left: int = 0
    own_units: Mapping[str, int] = field(default_factory=dict)
    own_structures: Mapping[str, int] = field(default_factory=dict)
    structures_in_progress: Mapping[str, int] = field(default_factory=dict)
    visible_enemy_units: Mapping[str, int] = field(default_factory=dict)
    visible_enemy_structures: Mapping[str, int] = field(default_factory=dict)
    idle_worker_count: int = 0
    army_count: int = 0
    game_loop: int = 0
    game_time_seconds: float = 0.0
    observation_notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in _NON_NEGATIVE_INT_FIELDS:
            value = getattr(self, field_name)
            if type(value) is not int:
                raise TypeError(f"SC2 commander state {field_name} must be an int.")
            if value < 0:
                raise ValueError(f"SC2 commander state {field_name} cannot be negative.")
        game_time = self.game_time_seconds
        if type(game_time) not in (int, float):
            raise TypeError("SC2 commander state game_time_seconds must be a float.")
        if game_time < 0:
            raise ValueError("SC2 commander state game_time_seconds cannot be negative.")
        object.__setattr__(self, "game_time_seconds", float(game_time))
        for mapping_name in _COUNT_MAPPING_FIELDS:
            object.__setattr__(
                self,
                mapping_name,
                _validated_counts(mapping_name, getattr(self, mapping_name)),
            )
        object.__setattr__(
            self,
            "observation_notes",
            tuple(str(note) for note in self.observation_notes),
        )

    @property
    def observation_complete(self) -> bool:
        """Return whether every bot attribute was readable during resolution."""

        return not self.observation_notes

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready semantic state snapshot."""

        return {
            "minerals": self.minerals,
            "vespene": self.vespene,
            "supply_used": self.supply_used,
            "supply_cap": self.supply_cap,
            "supply_left": self.supply_left,
            "own_units": dict(self.own_units),
            "own_structures": dict(self.own_structures),
            "structures_in_progress": dict(self.structures_in_progress),
            "visible_enemy_units": dict(self.visible_enemy_units),
            "visible_enemy_structures": dict(self.visible_enemy_structures),
            "idle_worker_count": self.idle_worker_count,
            "army_count": self.army_count,
            "game_loop": self.game_loop,
            "game_time_seconds": self.game_time_seconds,
            "observation_notes": list(self.observation_notes),
            "observation_complete": self.observation_complete,
        }


@runtime_checkable
class SC2StateResolverInterface(Protocol):
    """Resolver boundary from a BotAI-like runtime to commander state."""

    def resolve(self, bot: object) -> SC2CommanderState:
        """Resolve commander semantic state from raw bot observations."""


@dataclass(frozen=True)
class SC2StateResolver:
    """Default duck-typed resolver for python-sc2 ``BotAI``-like objects.

    Every attribute access is guarded: a weird bot object can never make
    ``resolve`` raise. Each degradation appends an observation note naming the
    missing or unreadable bot attribute.
    """

    def resolve(self, bot: object) -> SC2CommanderState:
        """Resolve one commander semantic snapshot from a BotAI-like object."""

        notes: list[str] = []
        minerals = _read_non_negative_int(bot, "minerals", notes)
        vespene = _read_non_negative_int(bot, "vespene", notes)
        supply_used = _read_non_negative_int(bot, "supply_used", notes)
        supply_cap = _read_non_negative_int(bot, "supply_cap", notes)
        # Negative supply_left is a normal supply-blocked game state in real
        # python-sc2 (supply_cap - supply_used after losing depots), not an
        # observation failure: clamp silently so it never gates commands.
        supply_left = _read_non_negative_int(
            bot,
            "supply_left",
            notes,
            negative_is_normal=True,
        )

        own_unit_entries = _materialize_group(bot, "units", notes)
        own_units = _count_unit_types(own_unit_entries, "bot.units", notes)
        structure_entries = _materialize_group(bot, "structures", notes)
        own_structures, structures_in_progress = _split_ready_structures(
            structure_entries,
            "bot.structures",
            notes,
        )
        enemy_unit_entries = _materialize_group(bot, "enemy_units", notes)
        visible_enemy_units = _count_unit_types(enemy_unit_entries, "bot.enemy_units", notes)
        enemy_structure_entries = _materialize_group(bot, "enemy_structures", notes)
        visible_enemy_structures = _count_unit_types(
            enemy_structure_entries,
            "bot.enemy_structures",
            notes,
        )

        idle_worker_count = _resolve_idle_worker_count(bot, own_unit_entries, notes)
        army_count = _resolve_army_count(bot, own_units, notes)
        game_loop = _resolve_game_loop(bot, notes)
        game_time_seconds = _resolve_game_time_seconds(bot, notes)

        return SC2CommanderState(
            minerals=minerals,
            vespene=vespene,
            supply_used=supply_used,
            supply_cap=supply_cap,
            supply_left=supply_left,
            own_units=own_units,
            own_structures=own_structures,
            structures_in_progress=structures_in_progress,
            visible_enemy_units=visible_enemy_units,
            visible_enemy_structures=visible_enemy_structures,
            idle_worker_count=idle_worker_count,
            army_count=army_count,
            game_loop=game_loop,
            game_time_seconds=game_time_seconds,
            observation_notes=_deduplicated(notes),
        )


DEFAULT_SC2_STATE_RESOLVER: Final[SC2StateResolver] = SC2StateResolver()
"""Shared default resolver used by the module-level convenience function."""


def resolve_commander_state(bot: object) -> SC2CommanderState:
    """Resolve commander semantic state with the default SC2 state resolver."""

    return DEFAULT_SC2_STATE_RESOLVER.resolve(bot)


def _validated_counts(field_name: str, mapping: Mapping[str, int]) -> dict[str, int]:
    validated: dict[str, int] = {}
    for key, value in dict(mapping).items():
        if type(key) is not str or not key.strip():
            raise ValueError(
                f"SC2 commander state {field_name} keys must be non-empty strings."
            )
        if type(value) is not int:
            raise TypeError(f"SC2 commander state {field_name}[{key!r}] must be an int.")
        if value < 0:
            raise ValueError(
                f"SC2 commander state {field_name}[{key!r}] cannot be negative."
            )
        validated[key] = value
    return validated


def _read_attribute(obj: object, attribute: str) -> object:
    """Read one attribute without ever raising on weird runtime objects."""

    try:
        return getattr(obj, attribute, _MISSING)
    except Exception:
        return _UNREADABLE


def _coerce_int(value: object) -> int | None:
    if type(value) is bool:
        return None
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return int(value)
    return None


def _read_non_negative_int(
    bot: object,
    attribute: str,
    notes: list[str],
    *,
    label: str | None = None,
    negative_is_normal: bool = False,
) -> int:
    """Read one int attribute, noting failures so validators stay conservative.

    ``negative_is_normal`` clamps negative values to 0 silently: a present,
    readable, negative value (for example ``supply_left`` while supply
    blocked) is valid game state, not an observation failure, so it must not
    flip ``observation_complete`` and block every mutating command.
    """

    resolved_label = label if label is not None else f"bot.{attribute}"
    value = _read_attribute(bot, attribute)
    if value is _MISSING:
        notes.append(f"{resolved_label} is missing; defaulted to 0.")
        return 0
    if value is _UNREADABLE:
        notes.append(f"{resolved_label} could not be read; defaulted to 0.")
        return 0
    coerced = _coerce_int(value)
    if coerced is None:
        notes.append(f"{resolved_label} has non-numeric value {value!r}; defaulted to 0.")
        return 0
    if coerced < 0:
        if not negative_is_normal:
            notes.append(
                f"{resolved_label} reported negative value {coerced}; clamped to 0."
            )
        return 0
    return coerced


def _materialize_group(bot: object, attribute: str, notes: list[str]) -> list[object]:
    label = f"bot.{attribute}"
    value = _read_attribute(bot, attribute)
    if value is _MISSING:
        notes.append(f"{label} is missing; counts defaulted to empty.")
        return []
    if value is _UNREADABLE:
        notes.append(f"{label} could not be read; counts defaulted to empty.")
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        notes.append(f"{label} is not iterable; counts defaulted to empty.")
        return []
    try:
        return list(value)
    except Exception:
        notes.append(f"{label} could not be iterated; counts defaulted to empty.")
        return []


def _unit_type_name(entry: object) -> str | None:
    """Derive the UPPERCASE space-free type name from a unit-like object."""

    normalized = _normalized_type_name(_read_attribute(entry, "name"))
    if normalized is not None:
        return normalized
    type_id = _read_attribute(entry, "type_id")
    if type_id is _MISSING or type_id is _UNREADABLE:
        return None
    return _normalized_type_name(_read_attribute(type_id, "name"))


def _normalized_type_name(value: object) -> str | None:
    if type(value) is not str:
        return None
    normalized = "".join(value.split()).upper()
    return normalized or None


def _count_unit_types(
    entries: list[object],
    label: str,
    notes: list[str],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        type_name = _unit_type_name(entry)
        if type_name is None:
            notes.append(f"{label} entry has no readable unit type name; entry skipped.")
            continue
        counts[type_name] = counts.get(type_name, 0) + 1
    return {type_name: counts[type_name] for type_name in sorted(counts)}


def _split_ready_structures(
    entries: list[object],
    label: str,
    notes: list[str],
) -> tuple[dict[str, int], dict[str, int]]:
    ready: dict[str, int] = {}
    in_progress: dict[str, int] = {}
    for entry in entries:
        type_name = _unit_type_name(entry)
        if type_name is None:
            notes.append(f"{label} entry has no readable unit type name; entry skipped.")
            continue
        is_ready_value = _read_attribute(entry, "is_ready")
        if is_ready_value is _UNREADABLE:
            notes.append(
                f"{label} entry is_ready could not be read; counted as ready."
            )
            is_ready = True
        elif is_ready_value is _MISSING:
            is_ready = True
        else:
            is_ready = _safe_bool(is_ready_value, default=True)
        bucket = ready if is_ready else in_progress
        bucket[type_name] = bucket.get(type_name, 0) + 1
    return (
        {type_name: ready[type_name] for type_name in sorted(ready)},
        {type_name: in_progress[type_name] for type_name in sorted(in_progress)},
    )


def _resolve_idle_worker_count(
    bot: object,
    own_unit_entries: list[object],
    notes: list[str],
) -> int:
    workers = _read_attribute(bot, "workers")
    if workers is _MISSING or workers is _UNREADABLE:
        notes.append(
            "bot.workers is missing or unreadable; "
            "idle workers counted from bot.units SCV entries."
        )
        return _count_idle_workers(own_unit_entries)
    idle = _read_attribute(workers, "idle")
    if idle is _MISSING or idle is _UNREADABLE:
        notes.append(
            "bot.workers.idle is missing or unreadable; "
            "idle workers counted from bot.units SCV entries."
        )
        return _count_idle_workers(own_unit_entries)
    size = _group_size(idle)
    if size is None:
        notes.append(
            "bot.workers.idle size could not be read; "
            "idle workers counted from bot.units SCV entries."
        )
        return _count_idle_workers(own_unit_entries)
    if size < 0:
        notes.append(f"bot.workers.idle reported negative size {size}; clamped to 0.")
        return 0
    return size


def _group_size(value: object) -> int | None:
    try:
        return int(len(value))  # type: ignore[arg-type]
    except Exception:
        pass
    amount = _read_attribute(value, "amount")
    if amount is _MISSING or amount is _UNREADABLE:
        return _coerce_int(value)
    return _coerce_int(amount)


def _count_idle_workers(own_unit_entries: list[object]) -> int:
    idle_workers = 0
    for entry in own_unit_entries:
        if _unit_type_name(entry) != SC2_WORKER_TYPE_NAME:
            continue
        is_idle = _read_attribute(entry, "is_idle")
        if is_idle is _MISSING or is_idle is _UNREADABLE:
            continue
        if _safe_bool(is_idle, default=False):
            idle_workers += 1
    return idle_workers


def _safe_bool(value: object, *, default: bool) -> bool:
    try:
        return bool(value)
    except Exception:
        return default


def _resolve_army_count(
    bot: object,
    own_units: Mapping[str, int],
    notes: list[str],
) -> int:
    computed = sum(
        count
        for type_name, count in own_units.items()
        if type_name != SC2_WORKER_TYPE_NAME
    )
    value = _read_attribute(bot, "supply_army")
    if value is _MISSING or value is _UNREADABLE:
        notes.append(
            "bot.supply_army is missing or unreadable; "
            "army count computed from non-SCV own units."
        )
        return computed
    coerced = _coerce_int(value)
    if coerced is None:
        notes.append(
            f"bot.supply_army has non-numeric value {value!r}; "
            "army count computed from non-SCV own units."
        )
        return computed
    if coerced < 0:
        notes.append(f"bot.supply_army reported negative value {coerced}; clamped to 0.")
        return 0
    return coerced


def _resolve_game_loop(bot: object, notes: list[str]) -> int:
    state = _read_attribute(bot, "state")
    if state is _MISSING or state is _UNREADABLE:
        notes.append("bot.state is missing or unreadable; game_loop defaulted to 0.")
        return 0
    return _read_non_negative_int(state, "game_loop", notes, label="bot.state.game_loop")


def _resolve_game_time_seconds(bot: object, notes: list[str]) -> float:
    value = _read_attribute(bot, "time")
    if value is _MISSING:
        notes.append("bot.time is missing; game_time_seconds defaulted to 0.0.")
        return 0.0
    if value is _UNREADABLE:
        notes.append("bot.time could not be read; game_time_seconds defaulted to 0.0.")
        return 0.0
    if type(value) is bool or not isinstance(value, (int, float)):
        notes.append(
            f"bot.time has non-numeric value {value!r}; game_time_seconds defaulted to 0.0."
        )
        return 0.0
    if value < 0:
        notes.append(f"bot.time reported negative value {value!r}; clamped to 0.0.")
        return 0.0
    return float(value)


def _deduplicated(notes: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for note in notes:
        if note in seen:
            continue
        seen.add(note)
        ordered.append(note)
    return tuple(ordered)
