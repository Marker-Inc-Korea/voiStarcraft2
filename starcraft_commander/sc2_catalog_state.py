"""``CatalogState`` snapshot of a live python-sc2 ``BotAI`` (what ``sc2_catalog.evaluate`` / ``issuable_now`` read).

Pure reads: nothing here issues an order.  Everything comes from the documented ``BotAI`` / ``Unit`` surface
(``units``, ``structures``, ``state.upgrades``, ``already_pending_upgrade``, ``can_afford``, ``vespene_geyser``,
``mineral_field``, ``enemy_units``/``enemy_structures``); a bot that lacks a surface leaves the matching field at the
catalog's "unknown" value (``None``), which ``evaluate`` never turns into a refusal.
"""

from __future__ import annotations

from typing import Any

from starcraft_commander import sc2_catalog as cat

_ADDON_PARENTS = frozenset({"BARRACKS", "FACTORY", "STARPORT", "BARRACKSFLYING", "FACTORYFLYING", "STARPORTFLYING"})
_REFINERIES = frozenset({"REFINERY", "REFINERYRICH"})


def _group(bot: Any, name: str) -> list[Any]:
    try:
        return list(getattr(bot, name, None) or ())
    except TypeError:
        return []


def _type_name(unit: Any) -> str:
    return str(getattr(getattr(unit, "type_id", None), "name", "") or "")


def _addon(unit: Any, techlabs: set[int], reactors: set[int]) -> str:
    """``techlab`` / ``reactor`` / ``none`` for a production structure; ``""`` (unknown / not applicable) otherwise."""

    if _type_name(unit) not in _ADDON_PARENTS:
        return ""
    tag = getattr(unit, "add_on_tag", 0) or 0
    if not tag:
        return "none"
    return "techlab" if tag in techlabs else "reactor" if tag in reactors else ""


def _energy(unit: Any) -> float | None:
    """Energy only for units that have an energy pool (a real observation carries ``energy_max`` for them)."""

    maximum = getattr(unit, "energy_max", 0) or 0
    return float(getattr(unit, "energy", 0.0)) if maximum > 0 else None


def order_ability_names(unit: Any) -> set[str]:
    """``AbilityId`` names of the orders ``unit`` is executing (exact ids, not remapped generals)."""

    names: set[str] = set()
    for order in getattr(unit, "orders", None) or ():
        ability = getattr(order, "ability", None)
        exact = getattr(ability, "exact_id", None) or getattr(ability, "id", None)
        name = getattr(exact, "name", None)
        if name:
            names.add(str(name))
    return names


def _owned(bot: Any) -> tuple[tuple[cat.Owned, ...], set[str]]:
    techlabs = set(getattr(bot, "techlab_tags", ()) or ())
    reactors = set(getattr(bot, "reactor_tags", ()) or ())
    seen: dict[Any, Any] = {}
    for group in ("structures", "units"):
        for unit in _group(bot, group):
            seen.setdefault(getattr(unit, "tag", id(unit)), unit)
    owned: list[cat.Owned] = []
    orders: set[str] = set()
    for unit in seen.values():
        orders |= order_ability_names(unit)
        owned.append(
            cat.Owned(
                type_name=_type_name(unit), tag=int(getattr(unit, "tag", 0) or 0),
                ready=bool(getattr(unit, "is_ready", True)), idle=bool(getattr(unit, "is_idle", True)),
                energy=_energy(unit), addon=_addon(unit, techlabs, reactors),
                is_structure=bool(getattr(unit, "is_structure", False)),
            )
        )
    return tuple(owned), orders


def _upgrade_ids() -> Any:
    from sc2.ids.upgrade_id import UpgradeId

    return UpgradeId


def _pending_upgrades(bot: Any, orders: set[str], done: frozenset[str]) -> frozenset[str]:
    """Upgrades some structure is researching right now: its research ability is in an order queue (catalog data),
    or python-sc2 reports progress strictly between 0 and 1."""

    pending: set[str] = set()
    ids = _upgrade_ids()
    for entry in cat.commands_by_category("research"):
        for name, ability in zip(entry.upgrades, entry.upgrade_abilities):
            if ability in orders:
                pending.add(name)
    probe = getattr(bot, "already_pending_upgrade", None)
    if callable(probe):
        for entry in cat.commands_by_category("research"):
            for name in entry.upgrades:
                member = ids.__members__.get(name)
                if member is None or name in done or name in pending:
                    continue
                try:
                    progress = probe(member)
                except (AssertionError, KeyError, IndexError, AttributeError):
                    continue
                if isinstance(progress, (int, float)) and 0 < progress < 1:
                    pending.add(name)
    return frozenset(pending - done)


def _free_geysers(bot: Any) -> int | None:
    geysers = _group(bot, "vespene_geyser")
    if not hasattr(bot, "vespene_geyser"):
        return None
    refineries = [s for s in _group(bot, "structures") if _type_name(s) in _REFINERIES]
    free = 0
    for geyser in geysers:
        position = getattr(geyser, "position", None)
        if position is None or not any(r.position.distance_to(position) < 1.0 for r in refineries):
            free += 1
    return free


def _can_afford(bot: Any) -> Any:
    from sc2.ids.unit_typeid import UnitTypeId

    ids = _upgrade_ids()
    probe = getattr(bot, "can_afford", None)

    def can_afford(kind: str, name: str) -> bool | None:
        if not callable(probe):
            return None
        table = ids if kind == "upgrade" else UnitTypeId if kind in {"unit", "structure"} else None
        member = table.__members__.get(name) if table is not None else None
        if member is None:
            return None
        try:
            return bool(probe(member))
        except (AssertionError, KeyError, IndexError, AttributeError):
            return None

    return can_afford


def build_catalog_state(bot: Any) -> cat.CatalogState:
    """The evaluator's snapshot of ``bot``: own units/structures, researched and running upgrades, money, targets."""

    owned, orders = _owned(bot)
    done = frozenset(getattr(up, "name", str(up)) for up in (getattr(getattr(bot, "state", None), "upgrades", None) or ()))
    enemies = None
    if hasattr(bot, "enemy_units") or hasattr(bot, "enemy_structures"):
        enemies = len(_group(bot, "enemy_units")) + len(_group(bot, "enemy_structures"))
    return cat.CatalogState(
        owned=owned, upgrades_done=done, upgrades_pending=_pending_upgrades(bot, orders, done), enemies_visible=enemies,
        free_geysers=_free_geysers(bot),
        mineral_patches=len(_group(bot, "mineral_field")) if hasattr(bot, "mineral_field") else None,
        minerals=getattr(bot, "minerals", None), vespene=getattr(bot, "vespene", None), can_afford=_can_afford(bot),
    )
