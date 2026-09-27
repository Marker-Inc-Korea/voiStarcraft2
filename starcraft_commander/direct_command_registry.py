"""Semantic squad and target-pin registry for Direct SC2 commands.

Providers address squads and map locations by stable names.  Resolution to
unit tags or coordinates remains a runtime concern; this registry only keeps
the user-facing names deterministic and rejects unknown names instead of
silently selecting an arbitrary unit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping


@dataclass(frozen=True)
class SquadDefinition:
    name: str
    unit_query: str
    description: str = ""
    metadata: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "unit_query": self.unit_query,
            "description": self.description,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class TargetPin:
    name: str
    target: str
    metadata: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "target": self.target, "metadata": dict(self.metadata)}


class DirectCommandRegistry:
    """Named squads and semantic target pins used by MCP adapters."""

    def __init__(
        self,
        squads: tuple[SquadDefinition, ...] = (),
        target_pins: tuple[TargetPin, ...] = (),
    ) -> None:
        self._squads: dict[str, SquadDefinition] = {}
        self._pins: dict[str, TargetPin] = {}
        for squad in squads:
            self.register_squad(squad)
        for pin in target_pins:
            self.register_target_pin(pin)

    @staticmethod
    def _key(value: str) -> str:
        return " ".join(str(value).strip().casefold().split())

    def register_squad(self, squad: SquadDefinition) -> None:
        key = self._key(squad.name)
        if not key:
            raise ValueError("squad name must be non-empty")
        if key in self._squads:
            raise ValueError(f"duplicate squad: {squad.name}")
        self._squads[key] = squad

    def register_target_pin(self, pin: TargetPin) -> None:
        key = self._key(pin.name)
        if not key:
            raise ValueError("target pin name must be non-empty")
        if key in self._pins:
            raise ValueError(f"duplicate target pin: {pin.name}")
        self._pins[key] = pin

    def resolve_squad(self, name: str) -> SquadDefinition:
        squad = self._squads.get(self._key(name))
        if squad is None:
            raise KeyError(f"unknown squad: {name}")
        return squad

    def resolve_target(self, target: str) -> str:
        pin = self._pins.get(self._key(target))
        return pin.target if pin is not None else str(target).strip()

    def resolve_target_pin(self, name: str) -> TargetPin:
        pin = self._pins.get(self._key(name))
        if pin is None:
            raise KeyError(f"unknown target pin: {name}")
        return pin

    def list_squads(self) -> tuple[SquadDefinition, ...]:
        return tuple(self._squads.values())

    def list_target_pins(self) -> tuple[TargetPin, ...]:
        return tuple(self._pins.values())

    @property
    def has_squads(self) -> bool:
        return bool(self._squads)

    @property
    def has_target_pins(self) -> bool:
        return bool(self._pins)

    def to_dict(self) -> dict[str, object]:
        return {
            "squads": [item.to_dict() for item in self.list_squads()],
            "target_pins": [item.to_dict() for item in self.list_target_pins()],
        }
