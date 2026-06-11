"""Ownership lookup helpers for Phase 0 ToyCraft entities."""

from __future__ import annotations

from typing import Final, Literal

from toycraft_commander.structures import (
    PLAYER_CONTROLLED_STRUCTURE_NAMES,
    STRUCTURE_NAMES,
    StructureName,
    is_player_controlled_structure_name,
    resolve_structure_name,
)
from toycraft_commander.units import (
    ENEMY_UNIT_NAMES,
    PLAYER_CONTROLLED_UNIT_NAMES,
    UnitName,
    is_enemy_unit_name,
    is_player_controlled_unit_name,
    resolve_unit_name,
)


OwnerName = Literal["player", "enemy"]
EntityKind = Literal["unit", "structure"]
OwnedEntityName = UnitName | StructureName

PLAYER_OWNER: Final[OwnerName] = "player"
ENEMY_OWNER: Final[OwnerName] = "enemy"

PLAYER_CONTROLLED_ENTITY_NAMES: Final[tuple[OwnedEntityName, ...]] = (
    *PLAYER_CONTROLLED_UNIT_NAMES,
    *PLAYER_CONTROLLED_STRUCTURE_NAMES,
)
ENEMY_CONTROLLED_ENTITY_NAMES: Final[tuple[OwnedEntityName, ...]] = ENEMY_UNIT_NAMES

ENTITY_NAMES_BY_OWNER: Final[dict[OwnerName, tuple[OwnedEntityName, ...]]] = {
    PLAYER_OWNER: PLAYER_CONTROLLED_ENTITY_NAMES,
    ENEMY_OWNER: ENEMY_CONTROLLED_ENTITY_NAMES,
}
ENTITY_NAMES_BY_KIND: Final[dict[EntityKind, tuple[OwnedEntityName, ...]]] = {
    "unit": (*PLAYER_CONTROLLED_UNIT_NAMES, *ENEMY_UNIT_NAMES),
    "structure": STRUCTURE_NAMES,
}


def resolve_entity_name(value: object) -> OwnedEntityName | None:
    """Return a canonical unit or structure name for raw command input."""

    unit_name = resolve_unit_name(value)
    if unit_name is not None:
        return unit_name
    return resolve_structure_name(value)


def resolve_player_controlled_entity_name(value: object) -> OwnedEntityName | None:
    """Return a canonical entity name only when the player controls it."""

    entity_name = resolve_entity_name(value)
    if entity_name in PLAYER_CONTROLLED_ENTITY_NAMES:
        return entity_name
    return None


def resolve_enemy_controlled_entity_name(value: object) -> OwnedEntityName | None:
    """Return a canonical entity name only when the enemy controls it."""

    entity_name = resolve_entity_name(value)
    if entity_name in ENEMY_CONTROLLED_ENTITY_NAMES:
        return entity_name
    return None


def is_player_controlled_entity_name(name: object) -> bool:
    """Return whether raw input names a ToyCraft entity controlled by the player."""

    return resolve_player_controlled_entity_name(name) is not None


def is_enemy_controlled_entity_name(name: object) -> bool:
    """Return whether raw input names a ToyCraft entity controlled by the enemy."""

    return resolve_enemy_controlled_entity_name(name) is not None


def get_entity_owner(name: object) -> OwnerName:
    """Return the owner for a supported ToyCraft entity name."""

    if is_player_controlled_entity_name(name):
        return PLAYER_OWNER
    if is_enemy_controlled_entity_name(name):
        return ENEMY_OWNER
    raise KeyError(f"Unsupported ToyCraft owned entity: {name}")


def get_entity_names_by_owner(owner: OwnerName) -> tuple[OwnedEntityName, ...]:
    """Return canonical entity names controlled by one owner."""

    try:
        return ENTITY_NAMES_BY_OWNER[owner]
    except KeyError as exc:
        raise KeyError(f"Unsupported ToyCraft entity owner: {owner}") from exc


def get_entity_names_by_kind(kind: EntityKind) -> tuple[OwnedEntityName, ...]:
    """Return canonical entity names for one ToyCraft entity kind."""

    try:
        return ENTITY_NAMES_BY_KIND[kind]
    except KeyError as exc:
        raise KeyError(f"Unsupported ToyCraft entity kind: {kind}") from exc


def is_player_controlled_unit_or_structure(name: object) -> bool:
    """Return whether raw input names a player unit or structure."""

    return is_player_controlled_unit_name(name) or is_player_controlled_structure_name(
        name
    )


def is_enemy_controlled_unit(name: object) -> bool:
    """Return whether raw input names an enemy unit."""

    return is_enemy_unit_name(name)
