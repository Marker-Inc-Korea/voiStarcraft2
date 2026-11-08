"""Research fixtures: a fake bot holding exactly what a research catalog entry needs (or one thing less).

Everything is derived from the catalog entry (issuer, ``upgrade_requirements``, ``upgrades``), whose data is generated
from the s2client-api header + burnysc2.  ``missing`` removes ONE precondition:

``issuer``    no researcher structure at all
``techlab``   the parent (Barracks/Factory/Starport) exists but carries no Tech Lab (tech-lab researches only)
``prereq``    the Armory (``upgrade_requirements``) is absent
``busy``      the researcher is already researching something else
``building``  the researcher is still under construction
``done``      every level (or the single upgrade) is already researched / its button already running
``pending``   the entry's first level is being researched right now
``money``     the player cannot pay
"""

from __future__ import annotations

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.ids.upgrade_id import UpgradeId

from scripts.sc2_accuracy import harness
from starcraft_commander import sc2_catalog as cat
from starcraft_commander.terran_prerequisites import TERRAN_TECH_REQUIREMENTS

PARENT_OF_LAB = {"BARRACKSTECHLAB": "BARRACKS", "FACTORYTECHLAB": "FACTORY", "STARPORTTECHLAB": "STARPORT"}
BUSY_ORDER = AbilityId.BARRACKSTRAIN_MARINE  # any order that is not this entry's research


def research_entries() -> tuple[cat.CommandEntry, ...]:
    return cat.commands_by_category("research")


def add_chain(bot, structure: str) -> None:
    """Finished prerequisite chain for ``structure`` (``TERRAN_TECH_REQUIREMENTS``: Armory -> Factory -> Barracks ...)."""

    cursor = structure
    while cursor and not any(u.type_id.name == cursor for u in bot.structures):
        bot.add_unit(getattr(U, cursor))
        cursor = TERRAN_TECH_REQUIREMENTS.get(cursor, "")


def variants(entry: cat.CommandEntry) -> list[str]:
    """Every precondition that can be removed for ``entry`` (each must make the order refuse, nothing sent)."""

    out = ["issuer", "busy", "building", "money"]
    if entry.valid_issuers[0] in PARENT_OF_LAB:
        out.append("techlab")
    if entry.upgrade_requirements and entry.upgrade_requirements[0][0]:
        out.append("prereq")
    out.append("done")
    if len(entry.upgrades) > 1:
        out.append("pending")
    return out


def world(entry: cat.CommandEntry, *, missing: str = "", have: int = 0, pending_level: int = 0):
    """``bot`` with the researcher for ``entry``.

    ``have`` = number of levels already researched (multi-level families); ``pending_level`` = 1-based level being
    researched right now.  A level >= 2 needs the Armory (``upgrade_requirements``), which this adds unless
    ``missing == "prereq"``.
    """

    bot = harness.make_bot()
    bot.minerals = bot.vespene = 5000
    issuer = entry.valid_issuers[0]
    next_index = have + (1 if pending_level else 0)
    building = entry.upgrade_requirements[min(next_index, len(entry.upgrade_requirements) - 1)][0] if entry.upgrade_requirements else ""
    if building and missing != "prereq":
        add_chain(bot, building)
    if missing == "money":
        bot.minerals = bot.vespene = 0
    kwargs: dict = {}
    if missing == "building":
        kwargs["build_progress"] = 0.5
    if missing == "busy":
        kwargs["orders"] = (BUSY_ORDER,)
    if missing == "done" and not entry.upgrades:
        kwargs["orders"] = (AbilityId[next(iter(entry.abilities.values()))],)
    if pending_level:
        kwargs["orders"] = (AbilityId[entry.upgrade_abilities[pending_level - 1]],)
    lab = None
    if missing != "issuer":
        if issuer in PARENT_OF_LAB:
            parent = PARENT_OF_LAB[issuer]
            if missing == "techlab":
                bot.add_unit(getattr(U, parent))
            else:
                lab = bot.add_unit(getattr(U, issuer), **kwargs)
                bot.add_unit(getattr(U, parent), add_on_tag=lab.tag)
        else:
            lab = bot.add_unit(getattr(U, issuer), **kwargs)
    done = entry.upgrades if missing == "done" else entry.upgrades[:have]
    bot.state.upgrades = {UpgradeId[name] for name in done}
    return bot, lab


def lower(entry: cat.CommandEntry, *, level: int | None = None):
    """The Direct actions the product lowers a spoken research to (provider payload -> router)."""

    from starcraft_commander.policy_modulation_provider import compile_policy_modulation_provider_output
    from starcraft_commander.unified_command_router import route_policy_vector

    payload = cat.direct_task_payload(entry.id, issuer=entry.valid_issuers[0], level=level)
    result = compile_policy_modulation_provider_output(payload)
    assert result.vector is not None, (entry.id, payload, result)
    plan = route_policy_vector(result.vector).direct_plan
    assert plan is not None, entry.id
    return tuple(plan.ordered_actions)
