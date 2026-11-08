"""Research lowering: which structure researches which level of an upgrade, and the exact ability to send.

``resolve_research(subject, bot)`` turns a semantic research subject (``STIMPACK``, ``TERRANINFANTRYWEAPONS``,
``TERRANSHIPWEAPONSLEVEL2``, ``GHOST_CLOAK`` ...) plus the live python-sc2 ``BotAI`` into either a
:class:`ResearchPlan` (the idle, finished researcher, the ``AbilityId`` and the upgrade level chosen from the
game state) or a refusal code.  The decision is the catalog's own ``evaluate`` (``sc2_catalog``: issuer, level,
Armory/previous-level prerequisite, resources) over a snapshot read from the bot, so planner, catalog and executor
cannot disagree.  Nothing here issues an order; the adapter does that.

Ability ids come from the catalog (generated from the s2client-api header + burnysc2) and are cross-checked at run time
against python-sc2 ``UPGRADE_RESEARCHED_FROM`` / ``RESEARCH_INFO``.  Where they name a different researcher
(Advanced Ballistics: header ``StarportTechLabResearch`` 805, burnysc2 ``FusionCoreResearch`` 1533) the structure that
really exists decides; the catalog route wins when both are present.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from starcraft_commander import sc2_catalog as cat

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


@dataclass(frozen=True)
class ResearchPlan:
    """One accepted research order (``refusal == ""``) or a refusal with nothing to send."""

    command_id: str = ""
    structures: tuple[Any, ...] = ()
    ability: Any = None
    """python-sc2 ``AbilityId`` of the research button (sent to the structure)."""
    upgrade: str = ""
    """``UpgradeId`` member name of the level chosen; ``""`` when the game data has no upgrade id (fuel tanks)."""
    level: int = 0
    refusal: str = ""


def find_entry(subject: str) -> tuple[cat.CommandEntry, str] | None:
    """``(catalog entry, pinned upgrade level or "")`` for a research subject, or ``None`` when it is not catalogued."""

    token = _NON_ALNUM.sub("", str(subject or "")).upper().removeprefix("TERRAN") if False else _NON_ALNUM.sub("", str(subject or "")).upper()
    if not token:
        return None
    from starcraft_commander.python_sc2_adapter import _SEMANTIC_UPGRADE_ALIASES

    candidates = (token, _SEMANTIC_UPGRADE_ALIASES.get(token, ""))
    research = cat.commands_by_category("research")
    for wanted in filter(None, candidates):
        for entry in research:
            if entry.subject == wanted:
                return entry, ""
    for wanted in filter(None, candidates):
        for entry in research:
            if wanted in entry.upgrades:
                return entry, wanted if len(entry.upgrades) > 1 else ""
    return None


def _is_python_sc2_bot(bot: object) -> bool:
    return getattr(bot, "game_data", None) is not None and hasattr(getattr(bot, "state", None), "upgrades")


def _order_ability_names(structure: object) -> set[str]:
    names: set[str] = set()
    for order in getattr(structure, "orders", None) or ():
        ability = getattr(order, "ability", None)
        exact = getattr(ability, "exact_id", None)
        name = getattr(exact, "name", None)
        if name:
            names.add(name)
    return names


def _structures(bot: object) -> list[object]:
    seen: dict[int, object] = {}
    for group in (getattr(bot, "structures", None), getattr(bot, "units", None)):
        for unit in list(group or ()):
            seen.setdefault(getattr(unit, "tag", id(unit)), unit)
    return list(seen.values())


def _addon_kind(unit: object, techlab_tags: set[int], reactor_tags: set[int]) -> str:
    tag = getattr(unit, "add_on_tag", 0) or 0
    if not tag:
        return "none"
    return "techlab" if tag in techlab_tags else "reactor" if tag in reactor_tags else ""


def snapshot(bot: Any, entry: cat.CommandEntry, *, only_tags: frozenset[int] | None = None) -> cat.CatalogState:
    """The ``CatalogState`` ``evaluate`` needs, read from a python-sc2 bot (structures, upgrades done/pending, money)."""

    from sc2.ids.upgrade_id import UpgradeId

    techlabs = set(getattr(bot, "techlab_tags", ()) or ())
    reactors = set(getattr(bot, "reactor_tags", ()) or ())
    owned = []
    orders: set[str] = set()
    for unit in _structures(bot):
        tag = getattr(unit, "tag", 0)
        type_name = getattr(getattr(unit, "type_id", None), "name", "") or ""
        orders |= _order_ability_names(unit)
        if only_tags is not None and type_name in entry.valid_issuers and tag not in only_tags:
            continue
        owned.append(
            cat.Owned(
                type_name=type_name, tag=tag, ready=bool(getattr(unit, "is_ready", True)),
                idle=bool(getattr(unit, "is_idle", True)), addon=_addon_kind(unit, techlabs, reactors),
                is_structure=bool(getattr(unit, "is_structure", False)),
            )
        )
    done = frozenset(getattr(up, "name", str(up)) for up in getattr(bot.state, "upgrades", ()) or ())
    pending = frozenset(
        name
        for research in cat.commands_by_category("research")
        for name, ability in zip(research.upgrades, research.upgrade_abilities)
        if ability in orders
    )

    def can_afford(kind: str, name: str) -> bool | None:
        upgrade = UpgradeId.__members__.get(name) if kind == "upgrade" else None
        return None if upgrade is None else bool(bot.can_afford(upgrade))

    return cat.CatalogState(
        owned=tuple(owned), upgrades_done=done, upgrades_pending=pending,
        minerals=getattr(bot, "minerals", None), vespene=getattr(bot, "vespene", None), can_afford=can_afford,
    )


def _route_from_python_sc2(upgrade_name: str) -> tuple[str, str] | None:
    """``(researcher type, exact ability name)`` python-sc2 documents for ``upgrade_name``, if it knows one."""

    try:
        from sc2.dicts.unit_research_abilities import RESEARCH_INFO
        from sc2.dicts.upgrade_researched_from import UPGRADE_RESEARCHED_FROM
        from sc2.ids.upgrade_id import UpgradeId
    except ImportError:  # pragma: no cover - python-sc2 is a hard dependency of the live adapter
        return None
    upgrade = UpgradeId.__members__.get(upgrade_name)
    owner = UPGRADE_RESEARCHED_FROM.get(upgrade) if upgrade is not None else None
    info = RESEARCH_INFO.get(owner, {}).get(upgrade) if owner is not None else None
    return (owner.name, info["ability"].name) if info else None


def _refuse(command_id: str, reason: str) -> ResearchPlan:
    return ResearchPlan(command_id=command_id, refusal=reason)


def resolve_research(subject: str, bot: Any, *, only_tags: frozenset[int] | None = None) -> ResearchPlan | None:
    """Plan the research for ``subject`` on ``bot``; ``None`` when the subject is not a catalogued research or
    ``bot`` is not a python-sc2 ``BotAI`` (the caller keeps its generic path)."""

    found = find_entry(subject)
    if found is None or not _is_python_sc2_bot(bot):
        return None
    entry, pinned = found
    from sc2.ids.ability_id import AbilityId

    state = snapshot(bot, entry, only_tags=only_tags)
    issuer = entry.valid_issuers[0]
    refusal = _pinned_refusal(entry, pinned, state)
    if refusal:
        return _refuse(entry.id, refusal)
    if not entry.upgrades:
        # No UpgradeId exists (HighCapacityFuelTanks: header ability 804 only): done/level cannot be known; an order
        # already running for the button is the only duplicate that can be detected.
        ability_name = next(iter(entry.abilities.values()))
        if any(ability_name in _order_ability_names(u) for u in _structures(bot)):
            return _refuse(entry.id, "research_already_pending")
    result = cat.evaluate(entry.id, state, issuer=issuer, upgrade=pinned or None)
    if result.ok:
        by_tag = {getattr(u, "tag", None): u for u in _structures(bot)}
        structures = tuple(by_tag[t] for t in result.issuer_tags if t in by_tag)
        level = entry.upgrades.index(result.upgrade) + 1 if result.upgrade in entry.upgrades else 0
        return ResearchPlan(
            command_id=entry.id, structures=structures, ability=AbilityId[result.ability],
            upgrade=result.upgrade, level=level,
        )
    reason = result.reason
    if reason == "no_ready_idle_researcher":
        alternative = _alternative_route(entry, pinned, state, bot)
        if alternative is not None:
            return alternative
        parent = issuer.removesuffix("TECHLAB") if issuer.endswith("TECHLAB") else ""
        if parent and state.of_type(parent):
            reason = "producer_needs_techlab"
    return _refuse(entry.id, reason)


def _pinned_refusal(entry: cat.CommandEntry, pinned: str, state: cat.CatalogState) -> str:
    """A level named by the user (``... LEVEL2``) is judged on its own: done / running / predecessor missing."""

    if not pinned:
        return ""
    if pinned in state.upgrades_done:
        return "already_researched"
    if pinned in state.upgrades_pending:
        return "research_already_pending"
    return ""


def _alternative_route(entry: cat.CommandEntry, pinned: str, state: cat.CatalogState, bot: Any) -> ResearchPlan | None:
    """The researcher python-sc2 documents, when the catalog's researcher does not exist but that one does."""

    if len(entry.upgrades) != 1 or not entry.upgrades:
        return None
    upgrade = entry.upgrades[0]
    route = _route_from_python_sc2(upgrade)
    if route is None or route[0] == entry.valid_issuers[0]:
        return None
    owner, ability_name = route
    mine = [o for o in state.of_type(owner) if o.ready and o.idle]
    if not mine or upgrade in state.upgrades_done:
        return None
    from sc2.ids.ability_id import AbilityId

    if any(ability_name in _order_ability_names(u) for u in _structures(bot)):
        return _refuse(entry.id, "research_already_pending")
    if state.can_afford is not None and state.can_afford("upgrade", upgrade) is False:
        return _refuse(entry.id, "unaffordable")
    tags = {o.tag for o in mine}
    structures = tuple(u for u in _structures(bot) if getattr(u, "tag", None) in tags)
    return ResearchPlan(command_id=entry.id, structures=structures, ability=AbilityId[ability_name], upgrade=upgrade, level=1)


__all__ = ["ResearchPlan", "find_entry", "resolve_research", "snapshot"]
_ = field  # dataclass ``field`` kept importable for subclassing callers
