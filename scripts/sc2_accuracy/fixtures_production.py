"""Fixture helpers for the train / build / morph catalog rows (additive; ``harness.py`` is shared and untouched)."""

from __future__ import annotations

from sc2.ids.unit_typeid import UnitTypeId


def remove_type(bot, type_name: str) -> None:
    """Drop every own unit/structure of ``type_name``.

    A prerequisite chain can contain the issuer itself (Hellbat needs an Armory, which needs a Factory: the Factory is
    both prerequisite and producer), so "issuer missing" must remove it AFTER the chain is built.
    """

    wanted = UnitTypeId[type_name]
    for group in (bot.units, bot.structures, bot.all_own_units):
        for unit in [u for u in group if u.type_id == wanted]:
            group.remove(unit)
