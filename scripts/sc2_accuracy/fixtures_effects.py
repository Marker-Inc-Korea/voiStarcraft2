"""Fixture helpers for the effect / behavior / other catalog rows (pure additions on top of ``harness``).

``complete(bot, entry, issuer, missing)`` gives a fake bot the preconditions the generic world builder in
``tests/test_sc2_catalog_exec.py`` does not know: cargo carried by Medivacs / Bunkers / Command Centers and the
absence of a mineral patch.  ``missing`` is one of ``""`` (everything valid), ``"cargo"`` (empty transporter),
``"target"`` (no mineral patch / no SCV for Load All) -- other variants are handled by the generic builder.

Cargo capacities are public game rules (Medivac 8, Bunker 4, Command Center 5 SCVs; stableid.json has none).
"""

from __future__ import annotations

from typing import Any

CARGO_CAPACITY = {"MEDIVAC": 8, "BUNKER": 4, "COMMANDCENTER": 5, "COMMANDCENTERFLYING": 5, "PLANETARYFORTRESS": 5}
CARRIED = 2
"""Cargo space used by a loaded transporter in the positive fixtures."""

_NEEDS_CARGO_IDS = {"unloadallat", "unloadall", "effect_stim"}
"""Rows whose caster is a transporter that must already carry passengers (Stim of a Bunker needs infantry inside)."""


def _carriers(bot: Any, issuer: str) -> list[Any]:
    return [u for u in list(bot.units) + list(bot.structures) if u.type_id.name == issuer]


def set_cargo(unit: Any, *, used: int, capacity: int | None = None) -> None:
    """Write the cargo observation fields python-sc2 reads (``cargo_space_max`` / ``cargo_space_taken``)."""

    proto = unit._proto
    proto.cargo_space_max = capacity if capacity is not None else CARGO_CAPACITY.get(unit.type_id.name, proto.cargo_space_max)
    proto.cargo_space_taken = used


def complete(bot: Any, entry: Any, issuer: str, missing: str = "") -> Any:
    """Add the cargo / target preconditions of ``entry`` (returns ``bot``)."""

    if entry.target_mode == "mineral_patch" and missing == "target":
        bot.mineral_field.clear()
    if entry.id == "loadall" and missing != "target":
        from sc2.ids.unit_typeid import UnitTypeId

        for index in range(2):
            bot.add_unit(UnitTypeId.SCV, x=21.0 + index, y=21.0)
    capacity = CARGO_CAPACITY.get(issuer)
    if capacity is None:
        return bot
    for carrier in _carriers(bot, issuer):
        if entry.id in _NEEDS_CARGO_IDS:
            set_cargo(carrier, used=0 if missing == "cargo" else CARRIED, capacity=capacity)
        else:
            set_cargo(carrier, used=capacity if missing == "full" else 0, capacity=capacity)
    return bot
