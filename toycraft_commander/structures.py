"""Structure models for the Phase 0 ToyCraft simulation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Final, Literal


StructureName = Literal["Barracks", "Factory", "Supply Depot", "Refinery"]
BuildingName = StructureName
FactionName = Literal["Terran"]
StructureCapability = Literal[
    "train_infantry",
    "train_vehicle",
    "increase_supply",
    "enable_gas_harvest",
    "tech_prerequisite",
    "production_prerequisite",
]


@dataclass(frozen=True)
class StructureCost:
    """Resource and construction cost for one ToyCraft structure."""

    minerals: int
    gas: int
    build_time_seconds: int

    def __post_init__(self) -> None:
        validate_non_negative_integer("minerals", self.minerals)
        validate_non_negative_integer("gas", self.gas)
        validate_positive_integer("build_time_seconds", self.build_time_seconds)

    def to_dict(self) -> dict[str, int]:
        """Return a plain dict for snapshots, validation, and narration."""

        return asdict(self)


@dataclass(frozen=True)
class StructureModel:
    """Canonical Terran structure definition available to ToyCraft."""

    name: StructureName
    faction: FactionName
    cost: StructureCost
    capabilities: tuple[StructureCapability, ...]
    prerequisites: tuple[StructureName, ...]
    supply_provided: int
    description: str

    def __post_init__(self) -> None:
        if not self.capabilities:
            raise ValueError("capabilities must include at least one capability.")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("capabilities must not contain duplicates.")
        validate_non_negative_integer("supply_provided", self.supply_provided)
        if not self.description.strip():
            raise ValueError("description must be a non-empty string.")

    def to_dict(self) -> dict[str, object]:
        """Return a serialization-friendly structure model."""

        return {
            "name": self.name,
            "faction": self.faction,
            "cost": self.cost.to_dict(),
            "capabilities": self.capabilities,
            "prerequisites": self.prerequisites,
            "supply_provided": self.supply_provided,
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


STRUCTURE_MODELS: Final[tuple[StructureModel, ...]] = (
    StructureModel(
        name="Barracks",
        faction="Terran",
        cost=StructureCost(minerals=150, gas=0, build_time_seconds=60),
        capabilities=("train_infantry", "production_prerequisite"),
        prerequisites=("Supply Depot",),
        supply_provided=0,
        description="Terran infantry production structure for Marines and early defense.",
    ),
    StructureModel(
        name="Factory",
        faction="Terran",
        cost=StructureCost(minerals=200, gas=100, build_time_seconds=60),
        capabilities=("train_vehicle", "tech_prerequisite"),
        prerequisites=("Barracks",),
        supply_provided=0,
        description="Terran vehicle production structure that unlocks Vulture harassment.",
    ),
    StructureModel(
        name="Supply Depot",
        faction="Terran",
        cost=StructureCost(minerals=100, gas=0, build_time_seconds=30),
        capabilities=("increase_supply", "tech_prerequisite"),
        prerequisites=(),
        supply_provided=8,
        description="Terran supply structure that raises the army and worker capacity.",
    ),
    StructureModel(
        name="Refinery",
        faction="Terran",
        cost=StructureCost(minerals=100, gas=0, build_time_seconds=30),
        capabilities=("enable_gas_harvest",),
        prerequisites=(),
        supply_provided=0,
        description="Terran gas structure that lets SCVs gather vespene from a geyser.",
    ),
)

STRUCTURE_NAMES: Final[tuple[StructureName, ...]] = tuple(
    structure.name for structure in STRUCTURE_MODELS
)
BUILDING_NAMES: Final[tuple[BuildingName, ...]] = STRUCTURE_NAMES
PLAYER_CONTROLLED_STRUCTURE_NAMES: Final[tuple[StructureName, ...]] = STRUCTURE_NAMES
STRUCTURE_MODEL_BY_NAME: Final[dict[StructureName, StructureModel]] = {
    structure.name: structure for structure in STRUCTURE_MODELS
}
BUILDING_MODEL_BY_NAME: Final[dict[BuildingName, StructureModel]] = STRUCTURE_MODEL_BY_NAME
STRUCTURE_NAME_ALIASES: Final[dict[str, StructureName]] = {
    "barracks": "Barracks",
    "rax": "Barracks",
    "배럭": "Barracks",
    "배럭스": "Barracks",
    "병영": "Barracks",
    "factory": "Factory",
    "factories": "Factory",
    "팩토리": "Factory",
    "군수공장": "Factory",
    "supply depot": "Supply Depot",
    "supplydepot": "Supply Depot",
    "depot": "Supply Depot",
    "depots": "Supply Depot",
    "서플": "Supply Depot",
    "서플라이": "Supply Depot",
    "서플라이디포": "Supply Depot",
    "보급고": "Supply Depot",
    "refinery": "Refinery",
    "refineries": "Refinery",
    "리파이너리": "Refinery",
    "정제소": "Refinery",
    "가스통": "Refinery",
}
BUILDING_NAME_ALIASES: Final[dict[str, BuildingName]] = STRUCTURE_NAME_ALIASES
STRUCTURE_NAMES_BY_CAPABILITY: Final[dict[StructureCapability, tuple[StructureName, ...]]] = {
    capability: tuple(
        structure.name for structure in STRUCTURE_MODELS if capability in structure.capabilities
    )
    for capability in (
        "train_infantry",
        "train_vehicle",
        "increase_supply",
        "enable_gas_harvest",
        "tech_prerequisite",
        "production_prerequisite",
    )
}
SUPPLY_PROVIDER_STRUCTURE_NAMES: Final[tuple[StructureName, ...]] = tuple(
    structure.name for structure in STRUCTURE_MODELS if structure.supply_provided > 0
)


def get_structure_model(name: StructureName) -> StructureModel:
    """Return the canonical ToyCraft structure model by name."""

    try:
        return STRUCTURE_MODEL_BY_NAME[name]
    except KeyError as exc:
        raise KeyError(f"Unsupported ToyCraft structure: {name}") from exc


def get_building_model(name: BuildingName) -> StructureModel:
    """Return the canonical ToyCraft building model by name."""

    return get_structure_model(name)


def resolve_structure_name(value: object) -> StructureName | None:
    """Return a canonical structure name for exact, English, or Korean input."""

    if type(value) is not str:
        return None
    candidate = value.strip()
    if candidate in STRUCTURE_MODEL_BY_NAME:
        return candidate
    normalized_candidate = candidate.lower()
    return STRUCTURE_NAME_ALIASES.get(normalized_candidate) or STRUCTURE_NAME_ALIASES.get(
        "".join(normalized_candidate.split())
    )


def resolve_building_name(value: object) -> BuildingName | None:
    """Return a canonical building name for validator-facing raw input."""

    return resolve_structure_name(value)


def get_resolved_structure_model(value: object) -> StructureModel:
    """Return a structure model after normalizing a raw validator value."""

    structure_name = resolve_structure_name(value)
    if structure_name is None:
        raise KeyError(f"Unsupported ToyCraft structure: {value}")
    return get_structure_model(structure_name)


def get_resolved_building_model(value: object) -> StructureModel:
    """Return a building model after normalizing a raw validator value."""

    return get_resolved_structure_model(value)


def is_supported_structure_name(name: object) -> bool:
    """Return whether a raw validator value names a ToyCraft structure."""

    return resolve_structure_name(name) is not None


def is_supported_building_name(name: object) -> bool:
    """Return whether a raw validator value names a ToyCraft building."""

    return is_supported_structure_name(name)


def is_player_controlled_structure_name(name: object) -> bool:
    """Return whether a raw validator value names a player-controlled structure."""

    return resolve_structure_name(name) in PLAYER_CONTROLLED_STRUCTURE_NAMES


def get_structure_names_by_capability(
    capability: StructureCapability,
) -> tuple[StructureName, ...]:
    """Return canonical structure names that expose one validator capability."""

    try:
        return STRUCTURE_NAMES_BY_CAPABILITY[capability]
    except KeyError as exc:
        raise KeyError(f"Unsupported ToyCraft structure capability: {capability}") from exc


def get_structure_models_by_capability(
    capability: StructureCapability,
) -> tuple[StructureModel, ...]:
    """Return canonical structure models that expose one validator capability."""

    return tuple(
        get_structure_model(name) for name in get_structure_names_by_capability(capability)
    )


def is_structure_capable_of(
    structure_name: object,
    capability: object,
) -> bool:
    """Return whether a raw structure value has a ToyCraft validator capability."""

    resolved_name = resolve_structure_name(structure_name)
    if resolved_name is None:
        return False
    return capability in get_structure_model(resolved_name).capabilities


def is_supply_provider_structure_name(name: object) -> bool:
    """Return whether a raw structure value increases ToyCraft supply capacity."""

    return resolve_structure_name(name) in SUPPLY_PROVIDER_STRUCTURE_NAMES


def get_structure_prerequisites(name: object) -> tuple[StructureName, ...]:
    """Return prerequisite structures for a raw structure value."""

    return get_resolved_structure_model(name).prerequisites


def get_missing_structure_prerequisites(
    name: object,
    available_structures: object,
) -> tuple[StructureName, ...]:
    """Return prerequisites not present in a raw completed-structure collection."""

    required = get_structure_prerequisites(name)
    available = _resolve_structure_name_set(available_structures)
    return tuple(prerequisite for prerequisite in required if prerequisite not in available)


def are_structure_prerequisites_satisfied(
    name: object,
    available_structures: object,
) -> bool:
    """Return whether a raw completed-structure collection unlocks a structure."""

    return not get_missing_structure_prerequisites(name, available_structures)


def _resolve_structure_name_set(values: object) -> frozenset[StructureName]:
    if type(values) is str:
        raw_values = (values,)
    elif isinstance(values, Iterable):
        raw_values = values
    else:
        raw_values = ()

    resolved_values: set[StructureName] = set()
    for value in raw_values:
        resolved_name = resolve_structure_name(value)
        if resolved_name is not None:
            resolved_values.add(resolved_name)
    return frozenset(resolved_values)
