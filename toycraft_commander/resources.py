"""Resource and supply model for Phase 0 ToyCraft simulation state."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Final, Literal


ResourceName = Literal["minerals", "gas"]
SupplyName = Literal["used_supply", "supply_capacity"]
ResourceType = Literal["int"]
SupplyType = Literal["int"]


@dataclass(frozen=True)
class ResourceField:
    """Typed resource field available in the ToyCraft economy model."""

    name: ResourceName
    type_name: ResourceType
    minimum: int
    description: str


@dataclass(frozen=True)
class SupplyField:
    """Typed supply field available in the ToyCraft state model."""

    name: SupplyName
    type_name: SupplyType
    minimum: int
    description: str


@dataclass(frozen=True)
class ResourceState:
    """Current economic resources tracked by the ToyCraft rule engine."""

    minerals: int = 0
    gas: int = 0

    def __post_init__(self) -> None:
        validate_resource_amount("minerals", self.minerals)
        validate_resource_amount("gas", self.gas)

    def to_dict(self) -> dict[ResourceName, int]:
        """Return a plain dict for narration, snapshots, and future DSL payloads."""

        return asdict(self)


@dataclass(frozen=True)
class SupplyState:
    """Current supply usage tracked by the ToyCraft rule engine."""

    used_supply: int = 0
    supply_capacity: int = 15

    def __post_init__(self) -> None:
        validate_supply_amount("used_supply", self.used_supply)
        validate_supply_amount("supply_capacity", self.supply_capacity)
        if self.used_supply > self.supply_capacity:
            raise ValueError("used_supply cannot exceed supply_capacity.")

    def to_dict(self) -> dict[SupplyName, int]:
        """Return a plain dict for narration, snapshots, and future DSL payloads."""

        return asdict(self)


RESOURCE_FIELDS: Final[tuple[ResourceField, ...]] = (
    ResourceField(
        name="minerals",
        type_name="int",
        minimum=0,
        description="Primary ToyCraft resource used for SCVs, Marines, depots, barracks, and expansions.",
    ),
    ResourceField(
        name="gas",
        type_name="int",
        minimum=0,
        description="Tech resource reserved for Terran upgrades and advanced units as the simulator grows.",
    ),
)

SUPPLY_FIELDS: Final[tuple[SupplyField, ...]] = (
    SupplyField(
        name="used_supply",
        type_name="int",
        minimum=0,
        description="Current ToyCraft supply consumed by SCVs and combat units.",
    ),
    SupplyField(
        name="supply_capacity",
        type_name="int",
        minimum=1,
        description="Current ToyCraft supply cap provided by Command Centers and Supply Depots.",
    ),
)

RESOURCE_FIELD_NAMES: Final[tuple[ResourceName, ...]] = tuple(
    field.name for field in RESOURCE_FIELDS
)
RESOURCE_FIELD_BY_NAME: Final[dict[ResourceName, ResourceField]] = {
    field.name: field for field in RESOURCE_FIELDS
}
SUPPLY_FIELD_NAMES: Final[tuple[SupplyName, ...]] = tuple(
    field.name for field in SUPPLY_FIELDS
)
SUPPLY_FIELD_BY_NAME: Final[dict[SupplyName, SupplyField]] = {
    field.name: field for field in SUPPLY_FIELDS
}


def validate_resource_amount(name: ResourceName, amount: object) -> None:
    """Reject impossible resource values before they enter game state."""

    if name not in RESOURCE_FIELD_BY_NAME:
        raise KeyError(f"Unsupported ToyCraft resource: {name}")
    if type(amount) is not int or amount < RESOURCE_FIELD_BY_NAME[name].minimum:
        raise ValueError(f"{name} must be a non-negative integer.")


def validate_supply_amount(name: SupplyName, amount: object) -> None:
    """Reject impossible supply values before they enter game state."""

    if name not in SUPPLY_FIELD_BY_NAME:
        raise KeyError(f"Unsupported ToyCraft supply field: {name}")
    minimum = SUPPLY_FIELD_BY_NAME[name].minimum
    if type(amount) is not int or amount < minimum:
        raise ValueError(f"{name} must be an integer greater than or equal to {minimum}.")


def get_available_resource_amount(
    resource_state: ResourceState,
    name: ResourceName,
) -> int:
    """Return the current amount for one validator resource field."""

    if name not in RESOURCE_FIELD_BY_NAME:
        raise KeyError(f"Unsupported ToyCraft resource: {name}")
    return getattr(resource_state, name)


def get_available_minerals(resource_state: ResourceState) -> int:
    """Return currently spendable minerals for feasibility checks."""

    return get_available_resource_amount(resource_state, "minerals")


def get_available_gas(resource_state: ResourceState) -> int:
    """Return currently spendable gas for feasibility checks."""

    return get_available_resource_amount(resource_state, "gas")


def get_available_supply(supply_state: SupplyState) -> int:
    """Return free supply slots before a production intent would execute."""

    return supply_state.supply_capacity - supply_state.used_supply


def get_required_resource_amount(cost: object, name: ResourceName) -> int:
    """Return one resource requirement from a model cost object or mapping."""

    if isinstance(cost, Mapping):
        amount = cost.get(name, 0)
    else:
        amount = getattr(cost, name, 0)

    validate_resource_amount(name, amount)
    return amount


def has_resource_amount(
    resource_state: ResourceState,
    name: ResourceName,
    required_amount: object,
) -> bool:
    """Return whether one resource threshold is currently available."""

    validate_resource_amount(name, required_amount)
    return get_available_resource_amount(resource_state, name) >= required_amount


def has_available_resources(
    resource_state: ResourceState,
    cost: object | None = None,
    *,
    minerals: object = 0,
    gas: object = 0,
) -> bool:
    """Return whether minerals and gas can pay a validator cost check."""

    required_minerals = _resolve_required_amount(cost, "minerals", minerals)
    required_gas = _resolve_required_amount(cost, "gas", gas)
    return (
        has_resource_amount(resource_state, "minerals", required_minerals)
        and has_resource_amount(resource_state, "gas", required_gas)
    )


def get_missing_resources(
    resource_state: ResourceState,
    cost: object | None = None,
    *,
    minerals: object = 0,
    gas: object = 0,
) -> dict[ResourceName, int]:
    """Return positive mineral or gas shortfalls for a rejected command."""

    required_minerals = _resolve_required_amount(cost, "minerals", minerals)
    required_gas = _resolve_required_amount(cost, "gas", gas)
    shortfalls: dict[ResourceName, int] = {}

    for name, required_amount in (
        ("minerals", required_minerals),
        ("gas", required_gas),
    ):
        available_amount = get_available_resource_amount(resource_state, name)
        if available_amount < required_amount:
            shortfalls[name] = required_amount - available_amount

    return shortfalls


def has_available_supply(
    supply_state: SupplyState,
    required_supply: object,
) -> bool:
    """Return whether enough free supply exists for a production check."""

    validate_supply_requirement(required_supply)
    return get_available_supply(supply_state) >= required_supply


def get_missing_supply(
    supply_state: SupplyState,
    required_supply: object,
) -> int:
    """Return the positive supply shortfall for a rejected production command."""

    validate_supply_requirement(required_supply)
    return max(0, required_supply - get_available_supply(supply_state))


def validate_supply_requirement(required_supply: object) -> None:
    """Reject invalid supply requirements before validator comparison."""

    if type(required_supply) is not int or required_supply < 0:
        raise ValueError("required_supply must be a non-negative integer.")


def _resolve_required_amount(
    cost: object | None,
    name: ResourceName,
    fallback_amount: object,
) -> int:
    if cost is None:
        validate_resource_amount(name, fallback_amount)
        return fallback_amount
    return get_required_resource_amount(cost, name)
