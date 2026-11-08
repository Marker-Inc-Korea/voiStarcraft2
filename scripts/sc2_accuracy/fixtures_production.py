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


# --------------------------------------------------------------------------- per-entry worlds (train / build / morph)

ADDON_LAB = {"BARRACKS": "BARRACKSTECHLAB", "FACTORY": "FACTORYTECHLAB", "STARPORT": "STARPORTTECHLAB"}
ENEMIES = ((UnitTypeId.ZEALOT, 60.0, 60.0), (UnitTypeId.IMMORTAL, 62.0, 61.0))


def prerequisite_chain(bot, structure_name: str) -> None:
    """Finished chain for ``structure_name`` (UnitTypeData.tech_requirement), including the Engineering Bay's CC."""

    from starcraft_commander.terran_prerequisites import required_structure

    cursor = required_structure(structure_name)
    while cursor:
        if not any(u.type_id.name == cursor for u in list(bot.structures) + list(bot.units)):
            bot.add_unit(getattr(UnitTypeId, cursor))
        cursor = required_structure(cursor)


def production_world(entry, issuer: str, missing: str = ""):
    """``(bot, payload kwargs)`` for a train/build/morph catalog row.

    ``missing`` removes exactly one precondition: ``issuer``, ``prerequisite``, ``techlab`` (producer without the lab),
    ``addon`` (producer already carrying an add-on), ``target`` (geyser / enemy).
    """

    from scripts.sc2_accuracy import harness

    bot = harness.make_bot()
    kwargs: dict = {}
    category, lowering = entry.category, entry.lowering
    produced, needs_lab, required = next(iter(entry.produces.values()), ("", False, ""))
    if category == "train":
        produced, needs_lab, required = entry.produces[issuer]
        if missing != "prerequisite":
            prerequisite_chain(bot, produced)
        if needs_lab and missing != "techlab":
            lab = bot.add_unit(getattr(UnitTypeId, ADDON_LAB[issuer]))
            bot.add_unit(getattr(UnitTypeId, issuer), add_on_tag=lab.tag)
        else:
            bot.add_unit(getattr(UnitTypeId, issuer))
        if missing == "issuer":
            remove_type(bot, issuer)
        return bot, kwargs
    if category == "build" and issuer == "SCV":
        if missing != "prerequisite":
            prerequisite_chain(bot, entry.subject)
        for index in range(2):
            bot.add_unit(UnitTypeId.SCV, x=20.0 + index, y=20.0)
        if missing == "issuer":
            for worker in list(bot.workers):
                bot.workers.remove(worker)
                bot.units.remove(worker)
        if entry.target_mode == "geyser":
            kwargs["location"] = "self_geyser"
            if missing != "target":
                bot.vespene_geyser.append(bot.add_unit(UnitTypeId.VESPENEGEYSER, x=24.0, y=16.0))
        else:
            kwargs["location"] = "natural" if entry.subject == "COMMANDCENTER" else "home"
        return bot, kwargs
    if lowering == "build_structure":  # add-ons, Orbital, Planetary: a no-target order on the idle parent
        if required and missing != "prerequisite":
            prerequisite_chain(bot, produced or entry.subject)
        lab = bot.add_unit(getattr(UnitTypeId, ADDON_LAB[issuer])) if missing == "addon" and issuer in ADDON_LAB else None
        bot.add_unit(getattr(UnitTypeId, issuer), **({"add_on_tag": lab.tag} if lab else {}))
        if missing == "issuer":
            remove_type(bot, issuer)
        kwargs["location"] = "home"
        return bot, kwargs
    # execute_ability rows: the caster (plus an enemy for the Liberator's defender-mode circle)
    bot.add_unit(getattr(UnitTypeId, issuer))
    bot.add_unit(getattr(UnitTypeId, issuer), x=21.0)
    if missing == "issuer":
        remove_type(bot, issuer)
    if entry.target_mode in {"enemy_unit", "enemy_point"} and missing != "target":
        for kind, x, y in ENEMIES:
            bot.add_unit(kind, x=x, y=y, enemy=True)
    return bot, kwargs


def probe() -> dict[str, list[str]]:
    """Run every train/build/morph row with its valid preconditions; return ``{"sent": [...], "refused": [...]}``."""

    from scripts.sc2_accuracy import harness
    from starcraft_commander import sc2_catalog as cat
    from starcraft_commander.policy_modulation_provider import compile_policy_modulation_provider_output
    from starcraft_commander.unified_command_router import route_policy_vector

    out: dict[str, list[str]] = {"sent": [], "refused": []}
    for category in ("train", "build", "morph"):
        for entry in cat.commands_by_category(category):
            issuer = entry.valid_issuers[0]
            bot, kwargs = production_world(entry, issuer)
            result = compile_policy_modulation_provider_output(cat.direct_task_payload(entry.id, issuer=issuer, **kwargs))
            plan = route_policy_vector(result.vector).direct_plan
            _, views, _ = harness.run_plan(bot, *plan.ordered_actions)
            out["sent" if views else "refused"].append(entry.id)
    return out


if __name__ == "__main__":
    import sys

    sys.path.insert(0, ".")
    report = probe()
    print(f"PRODUCTION SENT {len(report['sent'])} of {len(report['sent']) + len(report['refused'])}", report["refused"])
