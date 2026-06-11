"""Unit models for the Phase 0 ToyCraft simulation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Final, Literal


UnitName = Literal["SCV", "Marine", "Vulture", "Zealot"]
FactionName = Literal["Terran", "Protoss"]
UnitRole = Literal["worker", "infantry", "vehicle", "enemy_melee"]
ProducerName = Literal["Command Center", "Barracks", "Factory", "Gateway"]


@dataclass(frozen=True)
class UnitCost:
    """Resource, supply, and production cost for one ToyCraft unit."""

    minerals: int
    gas: int
    supply: int
    build_time_seconds: int

    def __post_init__(self) -> None:
        validate_non_negative_integer("minerals", self.minerals)
        validate_non_negative_integer("gas", self.gas)
        validate_positive_integer("supply", self.supply)
        validate_positive_integer("build_time_seconds", self.build_time_seconds)

    def to_dict(self) -> dict[str, int]:
        """Return a plain dict for snapshots, validation, and narration."""

        return asdict(self)


@dataclass(frozen=True)
class UnitStats:
    """Combat and durability stats used by the ToyCraft rule engine."""

    hit_points: int
    shields: int
    armor: int
    ground_damage: int
    attack_range: int

    def __post_init__(self) -> None:
        validate_positive_integer("hit_points", self.hit_points)
        validate_non_negative_integer("shields", self.shields)
        validate_non_negative_integer("armor", self.armor)
        validate_non_negative_integer("ground_damage", self.ground_damage)
        validate_non_negative_integer("attack_range", self.attack_range)

    @property
    def effective_hit_points(self) -> int:
        """Return health plus shields for simple combat resolution."""

        return self.hit_points + self.shields

    def to_dict(self) -> dict[str, int]:
        """Return a plain dict for snapshots, validation, and narration."""

        return asdict(self)


@dataclass(frozen=True)
class UnitModel:
    """Canonical unit definition available to the ToyCraft simulator."""

    name: UnitName
    faction: FactionName
    role: UnitRole
    producer: str
    cost: UnitCost
    stats: UnitStats
    description: str

    def __post_init__(self) -> None:
        if not self.producer.strip():
            raise ValueError("producer must be a non-empty string.")
        if not self.description.strip():
            raise ValueError("description must be a non-empty string.")

    def to_dict(self) -> dict[str, object]:
        """Return a serialization-friendly unit model."""

        return {
            "name": self.name,
            "faction": self.faction,
            "role": self.role,
            "producer": self.producer,
            "cost": self.cost.to_dict(),
            "stats": self.stats.to_dict(),
            "description": self.description,
        }


def validate_non_negative_integer(name: str, value: object) -> None:
    """Reject impossible negative or non-integer model values."""

    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer.")


def validate_positive_integer(name: str, value: object) -> None:
    """Reject impossible zero, negative, or non-integer model values."""

    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer.")


UNIT_MODELS: Final[tuple[UnitModel, ...]] = (
    UnitModel(
        name="SCV",
        faction="Terran",
        role="worker",
        producer="Command Center",
        cost=UnitCost(minerals=50, gas=0, supply=1, build_time_seconds=20),
        stats=UnitStats(
            hit_points=60,
            shields=0,
            armor=0,
            ground_damage=5,
            attack_range=1,
        ),
        description="Terran worker used for gathering, construction, scouting, and repair.",
    ),
    UnitModel(
        name="Marine",
        faction="Terran",
        role="infantry",
        producer="Barracks",
        cost=UnitCost(minerals=50, gas=0, supply=1, build_time_seconds=24),
        stats=UnitStats(
            hit_points=40,
            shields=0,
            armor=0,
            ground_damage=6,
            attack_range=4,
        ),
        description="Core Terran ranged infantry for early defense, pressure, and harassment.",
    ),
    UnitModel(
        name="Vulture",
        faction="Terran",
        role="vehicle",
        producer="Factory",
        cost=UnitCost(minerals=75, gas=0, supply=2, build_time_seconds=30),
        stats=UnitStats(
            hit_points=80,
            shields=0,
            armor=0,
            ground_damage=20,
            attack_range=5,
        ),
        description="Fast Terran vehicle reserved for later harassment and map-control scenarios.",
    ),
    UnitModel(
        name="Zealot",
        faction="Protoss",
        role="enemy_melee",
        producer="Gateway",
        cost=UnitCost(minerals=100, gas=0, supply=2, build_time_seconds=40),
        stats=UnitStats(
            hit_points=100,
            shields=60,
            armor=1,
            ground_damage=16,
            attack_range=1,
        ),
        description="Baseline enemy melee threat for validating defense and combat narration.",
    ),
)

UNIT_NAMES: Final[tuple[UnitName, ...]] = tuple(unit.name for unit in UNIT_MODELS)
UNIT_MODEL_BY_NAME: Final[dict[UnitName, UnitModel]] = {
    unit.name: unit for unit in UNIT_MODELS
}
UNIT_NAME_ALIASES: Final[dict[str, UnitName]] = {
    "scv": "SCV",
    "scvs": "SCV",
    "worker": "SCV",
    "workers": "SCV",
    "에스시비": "SCV",
    "일꾼": "SCV",
    "marine": "Marine",
    "marines": "Marine",
    "마린": "Marine",
    "해병": "Marine",
    "vulture": "Vulture",
    "vultures": "Vulture",
    "벌처": "Vulture",
    "zealot": "Zealot",
    "zealots": "Zealot",
    "질럿": "Zealot",
}
TERRAN_UNIT_NAMES: Final[tuple[UnitName, ...]] = tuple(
    unit.name for unit in UNIT_MODELS if unit.faction == "Terran"
)
PLAYER_CONTROLLED_UNIT_NAMES: Final[tuple[UnitName, ...]] = TERRAN_UNIT_NAMES
ENEMY_UNIT_NAMES: Final[tuple[UnitName, ...]] = tuple(
    unit.name for unit in UNIT_MODELS if unit.faction != "Terran"
)
TRAINABLE_UNIT_NAMES: Final[tuple[UnitName, ...]] = tuple(
    unit.name for unit in UNIT_MODELS if unit.faction == "Terran"
)
COMBAT_UNIT_NAMES: Final[tuple[UnitName, ...]] = tuple(
    unit.name
    for unit in UNIT_MODELS
    if unit.role in ("infantry", "vehicle", "enemy_melee")
)
UNIT_NAMES_BY_PRODUCER: Final[dict[str, tuple[UnitName, ...]]] = {
    producer: tuple(unit.name for unit in UNIT_MODELS if unit.producer == producer)
    for producer in tuple(dict.fromkeys(unit.producer for unit in UNIT_MODELS))
}


def get_unit_model(name: UnitName) -> UnitModel:
    """Return the canonical ToyCraft unit model by name."""

    try:
        return UNIT_MODEL_BY_NAME[name]
    except KeyError as exc:
        raise KeyError(f"Unsupported ToyCraft unit: {name}") from exc


def resolve_unit_name(value: object) -> UnitName | None:
    """Return a canonical unit name for exact, plural, or Korean raw input."""

    if type(value) is not str:
        return None
    candidate = value.strip()
    if candidate in UNIT_MODEL_BY_NAME:
        return candidate
    return UNIT_NAME_ALIASES.get(candidate.lower())


def get_resolved_unit_model(value: object) -> UnitModel:
    """Return a unit model after normalizing a raw validator unit value."""

    unit_name = resolve_unit_name(value)
    if unit_name is None:
        raise KeyError(f"Unsupported ToyCraft unit: {value}")
    return get_unit_model(unit_name)


def is_supported_unit_name(name: object) -> bool:
    """Return whether a raw validator value names a ToyCraft unit."""

    return resolve_unit_name(name) is not None


def is_terran_unit_name(name: object) -> bool:
    """Return whether a raw validator value names a player-controllable Terran unit."""

    return resolve_unit_name(name) in TERRAN_UNIT_NAMES


def is_player_controlled_unit_name(name: object) -> bool:
    """Return whether a raw validator value names a player-controlled unit."""

    return resolve_unit_name(name) in PLAYER_CONTROLLED_UNIT_NAMES


def is_enemy_unit_name(name: object) -> bool:
    """Return whether a raw validator value names an enemy pressure unit."""

    return resolve_unit_name(name) in ENEMY_UNIT_NAMES


def is_trainable_unit_name(name: object) -> bool:
    """Return whether a raw validator value names a Terran unit ToyCraft can produce."""

    return resolve_unit_name(name) in TRAINABLE_UNIT_NAMES


def is_combat_unit_name(name: object) -> bool:
    """Return whether a raw validator value names a unit with combat pressure value."""

    return resolve_unit_name(name) in COMBAT_UNIT_NAMES


def get_unit_models_by_faction(faction: FactionName) -> tuple[UnitModel, ...]:
    """Return canonical unit models for one faction."""

    if faction not in ("Terran", "Protoss"):
        raise KeyError(f"Unsupported ToyCraft unit faction: {faction}")
    return tuple(unit for unit in UNIT_MODELS if unit.faction == faction)


def get_unit_models_by_role(role: UnitRole) -> tuple[UnitModel, ...]:
    """Return canonical unit models for one unit role."""

    allowed_roles = ("worker", "infantry", "vehicle", "enemy_melee")
    if role not in allowed_roles:
        raise KeyError(f"Unsupported ToyCraft unit role: {role}")
    return tuple(unit for unit in UNIT_MODELS if unit.role == role)


def get_unit_names_by_producer(producer: str) -> tuple[UnitName, ...]:
    """Return unit names produced by one canonical structure or building."""

    try:
        return UNIT_NAMES_BY_PRODUCER[producer]
    except KeyError as exc:
        raise KeyError(f"Unsupported ToyCraft unit producer: {producer}") from exc


def get_unit_models_by_producer(producer: str) -> tuple[UnitModel, ...]:
    """Return unit models produced by one canonical structure or building."""

    return tuple(get_unit_model(name) for name in get_unit_names_by_producer(producer))


def is_unit_produced_by(unit_name: object, producer: object) -> bool:
    """Return whether a raw unit value is produced by a raw producer value."""

    resolved_unit_name = resolve_unit_name(unit_name)
    if type(producer) is not str or resolved_unit_name is None:
        return False
    return get_unit_model(resolved_unit_name).producer == producer
