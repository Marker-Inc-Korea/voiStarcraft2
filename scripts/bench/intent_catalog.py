"""Terran command catalog + state-dependent gold resolution, derived ONLY from API data.

Sources (no hand-typed command lists):
* ``scripts/sc2_accuracy/s2client_api_enums.json`` ``unit_abilities``/``abilities`` (Blizzard s2client-api header):
  the 103 non-generic Terran commands (TRAIN 17, BUILD 16, RESEARCH 24, MORPH 14, EFFECT 20, BEHAVIOR 4, OTHER 8)
  and the unit types that can issue each.
* python-sc2 (burnysc2) ``RESEARCH_INFO`` / ``TRAIN_INFO`` / ``GENERIC_REDIRECT_ABILITIES``: research building,
  required building, required upgrade (= level chain), techlab/producer requirements.
* ``starcraft_commander.python_sc2_adapter._ABILITY_ENERGY_COST``: the product's own energy table.

Command id grammar (what the gold and the scorer speak)::

    NAME            API command name, e.g. RESEARCH_STIMPACK, TRAIN_MARINE, MORPH_SIEGEMODE
    NAME#N          multi-level upgrade, level N        (RESEARCH_TERRANINFANTRYWEAPONS#2)
    NAME@CASTER     caster-qualified command             (BUILD_TECHLAB@BARRACKS, BEHAVIOR_CLOAKON@GHOST)

``resolve(selector, state)`` -> {"expect", "acceptable", "exact", "why"}.  ``expect`` is one of
``execute`` (>=1 command is possible now), ``choose_or_ask`` (nothing is possible but the missing prerequisite can be
built: the acceptable set is the build commands), ``ask`` (nothing to do: already researched / no energy / no
caster).  Resources (minerals/gas) are carried in the state for the Planner but never gate the gold: SC2 itself
queues or refuses on cost and the offline data has no complete cost table.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
API_PATH = REPO_ROOT / "scripts" / "sc2_accuracy" / "s2client_api_enums.json"

GENERIC_NAMES = frozenset({
    "ATTACK", "MOVE", "STOP", "PATROL", "HOLDPOSITION", "SMART", "HALT", "RALLY_WORKERS", "RALLY_UNITS",
    "HARVEST_GATHER", "HARVEST_RETURN", "Zerg", "CANCEL", "CANCEL_LAST",
})
CATEGORIES = ("TRAIN", "BUILD", "RESEARCH", "MORPH", "EFFECT", "BEHAVIOR")
# Commands whose caster identity changes what the user means (which structure / which unit): the id carries @CASTER.
QUALIFIED = frozenset({"BUILD_TECHLAB", "BUILD_REACTOR", "BEHAVIOR_CLOAKON", "BEHAVIOR_CLOAKOFF", "LIFT", "LAND"})
ADDON_PARENTS = ("BARRACKS", "FACTORY", "STARPORT")


@functools.lru_cache(maxsize=1)
def api() -> dict:
    return json.loads(API_PATH.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def terran_commands() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for unit, names in api()["unit_abilities"].items():
        if not unit.startswith("TERRAN_"):
            continue
        for name in names:
            if name in GENERIC_NAMES:
                continue
            spec = out.setdefault(name, {"casters": [], "category": name.split("_")[0] if name.split("_")[0] in CATEGORIES else "OTHER"})
            spec["casters"].append(unit[len("TERRAN_"):])
    for spec in out.values():
        spec["casters"] = sorted(spec["casters"])
    return dict(sorted(out.items()))


def categories_of(names) -> dict[str, list[str]]:
    cat = terran_commands()
    out: dict[str, list[str]] = {}
    for n in names:
        out.setdefault(cat[n]["category"], []).append(n)
    return out


# --------------------------------------------------------------------------- python-sc2 indices


@functools.lru_cache(maxsize=1)
def _sc2():
    from sc2.dicts.generic_redirect_abilities import GENERIC_REDIRECT_ABILITIES
    from sc2.dicts.unit_research_abilities import RESEARCH_INFO
    from sc2.dicts.unit_train_build_abilities import TRAIN_INFO
    from sc2.ids.upgrade_id import UpgradeId

    return GENERIC_REDIRECT_ABILITIES, RESEARCH_INFO, TRAIN_INFO, UpgradeId


def header_name(ability) -> str | None:
    """python-sc2 ``AbilityId`` -> s2client-api header command name (through the generic redirect)."""

    generic, *_ = _sc2()
    names = {int(k): v[0] for k, v in api()["abilities"].items()}
    return names.get(generic.get(ability, ability).value) or names.get(ability.value)


@functools.lru_cache(maxsize=1)
def research_entries() -> dict[str, list[dict]]:
    """header command -> ordered entries {building, upgrade, level, required_building, required_upgrade}."""

    _, research_info, _, _ = _sc2()
    by_cmd: dict[str, list[dict]] = {}
    for building, upgrades in research_info.items():
        for upgrade, info in upgrades.items():
            cmd = header_name(info["ability"])
            if not cmd:
                continue
            by_cmd.setdefault(cmd, []).append({
                "building": building.name,
                "upgrade": upgrade.name,
                "required_building": info["required_building"].name if "required_building" in info else None,
                "required_upgrade": info["required_upgrade"].name if "required_upgrade" in info else None,
            })
    for entries in by_cmd.values():
        by_up = {e["upgrade"]: e for e in entries}

        def depth(e, seen=0):
            ru = e["required_upgrade"]
            return 0 if not ru or ru not in by_up else 1 + depth(by_up[ru])

        entries.sort(key=depth)
        multi = len(entries) > 1 and any(e["required_upgrade"] for e in entries)
        for e in entries:
            e["level"] = depth(e) + 1 if multi else None
    return by_cmd


@functools.lru_cache(maxsize=1)
def train_entries() -> dict[str, list[dict]]:
    """header command -> [{producer, product, requires_techlab, required_building}] (TRAIN_/BUILD_/MORPH_ via TRAIN_INFO)."""

    _, _, train_info, _ = _sc2()
    by_cmd: dict[str, list[dict]] = {}
    for producer, products in train_info.items():
        for product, info in products.items():
            cmd = header_name(info["ability"])
            if not cmd:
                continue
            by_cmd.setdefault(cmd, []).append({
                "producer": producer.name,
                "product": product.name,
                "requires_techlab": bool(info.get("requires_techlab")),
                "required_building": info["required_building"].name if "required_building" in info else None,
            })
    return by_cmd


@functools.lru_cache(maxsize=1)
def upgrade_ids() -> frozenset[str]:
    return frozenset(u.name for u in _sc2()[3])


@functools.lru_cache(maxsize=1)
def structure_names() -> frozenset[str]:
    """Terran structure unit names: what the SCV builds + add-ons + morphs (+ FLYING / LOWERED variants)."""

    base = {e["product"] for cmd, es in train_entries().items() if cmd.startswith("BUILD_") for e in es if e["producer"] == "SCV"}
    base |= {f"{p}{a}" for p in ADDON_PARENTS for a in ("TECHLAB", "REACTOR")}
    base |= {e["product"] for e in train_entries().get("MORPH_ORBITALCOMMAND", [])}
    base |= {e["product"] for e in train_entries().get("MORPH_PLANETARYFORTRESS", [])}
    base |= {f"{b}FLYING" for b in ("BARRACKS", "FACTORY", "STARPORT", "COMMANDCENTER", "ORBITALCOMMAND")}
    base.add("SUPPLYDEPOTLOWERED")
    return frozenset(base)


@functools.lru_cache(maxsize=1)
def energy_costs() -> dict[str, int]:
    """API command name -> energy cost, from the product adapter's own table (token -> header command)."""

    from sc2.ids.ability_id import AbilityId
    from starcraft_commander import python_sc2_adapter as A

    out: dict[str, int] = {}
    for token, cost in A._ABILITY_ENERGY_COST.items():  # noqa: SLF001 - the product's table is the single source
        spec = A.SC2_SEMANTIC_ABILITIES.get(token)
        if not spec:
            continue
        cmd = header_name(AbilityId[spec[0]])
        if cmd:
            out[cmd] = int(cost)
    return out


@functools.lru_cache(maxsize=1)
def token_to_command() -> dict[str, tuple[str, str | None]]:
    """Product micro token (realtime_intent.ABILITIES) -> (API command, caster|None) via the adapter's ability table."""

    from sc2.ids.ability_id import AbilityId
    from starcraft_commander import python_sc2_adapter as A

    out: dict[str, tuple[str, str | None]] = {}
    for token, spec in A.SC2_SEMANTIC_ABILITIES.items():
        cmd = header_name(AbilityId[spec[0]])
        if cmd and cmd in terran_commands():
            caster = spec[2][0] if cmd in QUALIFIED and len(spec[2]) == 1 else None
            out[token] = (cmd, caster)
    return out


# --------------------------------------------------------------------------- state-dependent resolution


def _build_for(building: str, owned: set[str]) -> str | None:
    """First BUILD command that moves the player towards ``building`` (None when owned / not buildable)."""

    if building in owned:
        return None
    for parent in ADDON_PARENTS:
        for addon, cmd in (("TECHLAB", "BUILD_TECHLAB"), ("REACTOR", "BUILD_REACTOR")):
            if building == parent + addon:
                return _build_for(parent, owned) or f"{cmd}@{parent}"
    for cmd, entries in train_entries().items():
        if not cmd.startswith("BUILD_"):
            continue
        for e in entries:
            if e["producer"] == "SCV" and e["product"] == building:
                rb = e["required_building"]
                return (_build_for(rb, owned) if rb and rb not in owned else None) or cmd
    return None


@functools.lru_cache(maxsize=1)
def _tech_alias() -> dict[str, frozenset[str]]:
    from sc2.dicts.unit_tech_alias import UNIT_TECH_ALIAS

    return {k.name: frozenset(v.name for v in vs) for k, vs in UNIT_TECH_ALIAS.items()}


def _owned(state: dict) -> set[str]:
    """Owned structures + python-sc2 tech aliases (an ORBITALCOMMAND satisfies 'requires COMMANDCENTER')."""

    owned = set(state["structures"])
    for s in list(owned):
        owned |= _tech_alias().get(s, frozenset())
    return owned


def _is_structure(unit: str) -> bool:
    return unit in structure_names()


def _resolve_one(name: str, sel: dict, state: dict) -> tuple[list[str], list[str], str]:
    """-> (acceptable ids, blocker build commands, why)."""

    spec = terran_commands()[name]
    owned, researched = _owned(state), set(state["researched"])
    caster_sel = sel.get("caster")
    cat = spec["category"]
    why = ""

    if cat == "RESEARCH":
        entries = research_entries().get(name)
        if entries:
            want = sel.get("level")
            pick = None
            for e in entries:
                if want is not None:
                    if e["level"] == want:
                        pick = e
                elif e["upgrade"] not in researched:
                    pick = e
                    break
            if pick is None or pick["upgrade"] in researched:
                return [], [], f"{name}: nothing left to research"
            if pick["required_upgrade"] and pick["required_upgrade"] not in researched:
                return [], [], f"{name}: previous level not researched"
            blockers = [b for b in (_build_for(pick["building"], owned), _build_for(pick["required_building"], owned) if pick["required_building"] else None) if b]
            cid = f"{name}#{pick['level']}" if pick["level"] else name
            return ([] if blockers else [cid]), blockers, why
        # header-only research (no python-sc2 table): the issuing building comes from the header
        suffix = name[len("RESEARCH_"):]
        if suffix in researched and suffix in upgrade_ids():
            return [], [], f"{name}: already researched"
        caster = spec["casters"][0]
        return ([name] if caster in owned else []), ([] if caster in owned else [b for b in [_build_for(caster, owned)] if b]), why

    if cat in ("TRAIN", "BUILD", "MORPH") and name in train_entries():
        acceptable, blockers = [], []
        for e in train_entries()[name]:
            miss = []
            if e["producer"] != "SCV" and e["producer"] not in owned:
                miss.append(_build_for(e["producer"], owned))
            elif e["requires_techlab"] and e["producer"] + "TECHLAB" not in owned:
                miss.append(_build_for(e["producer"] + "TECHLAB", owned))
            if e["required_building"] and e["required_building"] not in owned:
                miss.append(_build_for(e["required_building"], owned))
            miss = [m for m in miss if m]
            if miss:
                blockers += miss
            else:
                acceptable.append(name)
        return sorted(set(acceptable)), sorted(set(blockers)), why

    # add-ons, nukes, effects, morph modes, behaviors, other: caster presence (+ energy)
    casters = [c for c in spec["casters"] if not (name in ("BUILD_TECHLAB", "BUILD_REACTOR") and c.endswith("FLYING"))]
    if caster_sel:
        casters = [c for c in casters if c == caster_sel] if caster_sel in casters else []
    present = [c for c in casters if (c in owned) or not _is_structure(c)]
    if name == "LIFT":
        present = [c for c in present if c in owned]
    cost = energy_costs().get(name, 0)
    if cost and int(state.get("energy", 0)) < cost:
        return [], [], f"{name}: needs {cost} energy"
    if not present:
        blockers = [b for b in (_build_for(c, owned) for c in casters if _is_structure(c) and not c.endswith("FLYING")) if b]
        return [], blockers, f"{name}: no caster"
    if name in QUALIFIED:
        return sorted(f"{name}@{c}" for c in present), [], why
    return [name], [], why


def resolve(sel: dict, state: dict) -> dict:
    names = list(sel.get("commands") or [])
    if sel.get("category"):
        names += [n for n, s in terran_commands().items() if s["category"] == sel["category"]]
    acceptable: set[str] = set()
    blockers: set[str] = set()
    why: list[str] = []
    for n in names:
        a, b, w = _resolve_one(n, sel, state)
        acceptable |= set(a)
        blockers |= set(b)
        if w:
            why.append(w)
    if acceptable:
        out = sorted(acceptable)
        return {"expect": "execute", "acceptable": out, "exact": out[0] if len(out) == 1 else None, "why": why}
    if blockers:
        return {"expect": "choose_or_ask", "acceptable": sorted(blockers), "exact": None, "why": why + ["prerequisite missing"]}
    return {"expect": "ask", "acceptable": [], "exact": None, "why": why}
