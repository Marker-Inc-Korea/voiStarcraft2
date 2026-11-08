"""Generate ``starcraft_commander/sc2_catalog_data.py``: the DATA half of the Terran command catalog.

Sources (all offline): the s2client-api header extract ``s2client_api_enums.json`` (``unit_abilities`` = which ability
each unit lists, ``abilities`` = id/name/target kind), burnysc2 ``UNIT_ABILITIES`` / ``GENERIC_REDIRECT_ABILITIES`` /
``RESEARCH_INFO`` / ``TRAIN_INFO`` (exact ability per issuer, upgrade levels), and burnysc2 ``UpgradeId``.

    python -m scripts.sc2_accuracy.gen_catalog_facts           # rewrite the data module
    python -m scripts.sc2_accuracy.gen_catalog_facts --check   # exit 1 when the checked-in module is stale

``tests/test_sc2_catalog.py`` runs the ``--check`` equivalent, so a data refresh that adds or changes a Terran command
is a red test until the catalog is regenerated and annotated.
"""

from __future__ import annotations

import argparse
import pprint
import sys
from pathlib import Path
from typing import Any

from scripts.sc2_accuracy import audit

OUTPUT = Path(__file__).resolve().parents[2] / "starcraft_commander" / "sc2_catalog_data.py"
RACE_PREFIX = "TERRAN_"
# Abilities every unit has: movement/targeting/queue plumbing, not a gameplay command the user names.
GENERIC_HEADER_ABILITIES = frozenset(
    {"ATTACK", "CANCEL", "CANCEL_LAST", "SMART", "STOP", "MOVE", "PATROL", "HOLDPOSITION", "RALLY_UNITS", "HALT", "Zerg"}
)
# Header families outside the user-facing command catalog (economy plumbing handled by dedicated actions).
EXCLUDED_PREFIXES = ("HARVEST_", "RALLY_")
# python-sc2 research table gaps: ability name -> UpgradeId member names (levels).  Verified against stableid.json
# upgrade names (NeosteelFrame, CycloneRapidFireLaunchers, RavenCorvidReactor, RavenRecalibratedExplosives,
# RavenEnhancedMunitions, LiberatorAGRangeUpgrade).  HIGHCAPACITYFUELTANKS has no upgrade in stableid.json.
EXTRA_UPGRADES: dict[str, tuple[str, ...]] = {
    "RESEARCH_NEOSTEELFRAME": ("NEOSTEELFRAME",),
    "RESEARCH_RAPIDFIRELAUNCHERS": ("CYCLONERAPIDFIRELAUNCHERS",),
    "RESEARCH_RAVENCORVIDREACTOR": ("RAVENCORVIDREACTOR",),
    "RESEARCH_RAVENRECALIBRATEDEXPLOSIVES": ("RAVENRECALIBRATEDEXPLOSIVES",),
    "RESEARCH_ENHANCEDMUNITIONS": ("RAVENENHANCEDMUNITIONS",),
    "RESEARCH_ADVANCEDBALLISTICS": ("LIBERATORAGRANGEUPGRADE",),
    "RESEARCH_HIGHCAPACITYFUELTANKS": (),
}


def category_of(name: str) -> str:
    head = name.split("_", 1)[0]
    mapping = {
        "TRAIN": "train", "BUILD": "build", "RESEARCH": "research", "MORPH": "morph", "EFFECT": "effect",
        "BEHAVIOR": "behavior",
    }
    return mapping.get(head, "other")


def _resolve_issuer_ability(sc2: dict[str, Any], issuer: str, header_id: int, header_name: str) -> str | None:
    unit = getattr(sc2["UnitTypeId"], issuer, None)
    ability_id = sc2["AbilityId"]
    generic = sc2["GENERIC"]
    if unit is not None:
        for member in sorted(sc2["UNIT_ABILITIES"].get(unit, ()), key=lambda m: m.name):
            if member.value == header_id or (generic.get(member) is not None and generic[member].value == header_id):
                return member.name
    # burnysc2 lists SALVAGEEFFECT_SALVAGE (4128) for the Bunker, but the local stableid.json names id 4128
    # "MothershipCloak" (SalvageEffect is 4131), so that id is not a Bunker salvage in the shipped game data.  The
    # s2client-api header documents EFFECT_SALVAGE (32, Target None): send that generic id.
    if unit is not None and header_name == "EFFECT_SALVAGE" and getattr(ability_id, "EFFECT_SALVAGE", None) is not None:
        return "EFFECT_SALVAGE"
    # Header lists it, python-sc2's per-unit table does not: look for an issuer-suffixed exact member.
    for member in ability_id:
        remap = generic.get(member)
        if remap is not None and remap.value == header_id and member.name.endswith("_" + issuer):
            return member.name
    return None


def build_facts() -> list[dict[str, Any]]:
    sc2 = audit._sc2()
    if sc2 is None:
        raise SystemExit("burnysc2 is required to regenerate the catalog facts")
    header = audit.load_header_enums()
    name_to_id = {entry[0]: int(key) for key, entry in header["abilities"].items()}
    kind = {entry[0]: entry[1] for entry in header["abilities"].values()}
    issuers: dict[str, list[str]] = {}
    for unit, abilities in header["unit_abilities"].items():
        if not unit.startswith(RACE_PREFIX):
            continue
        for ability in abilities:
            if ability in GENERIC_HEADER_ABILITIES or ability.startswith(EXCLUDED_PREFIXES):
                continue
            issuers.setdefault(ability, []).append(unit[len(RACE_PREFIX):])
    upgrade_id = sc2["UpgradeId"]
    research_info = sc2["RESEARCH_INFO"]
    generic = sc2["GENERIC"]
    facts: list[dict[str, Any]] = []
    for name in sorted(issuers):
        header_id = name_to_id[name]
        row: dict[str, Any] = {
            "id": name.lower(),
            "category": category_of(name),
            "header_name": name,
            "header_id": header_id,
            "header_target": kind[name],
            "issuers": tuple(sorted(issuers[name])),
            "abilities": {},
            "not_in_python_sc2_unit_abilities": (),
            "upgrades": (),
            "upgrade_abilities": (),
            "upgrade_requirements": (),
            "produces": {},
        }
        for issuer in row["issuers"]:
            resolved = _resolve_issuer_ability(sc2, issuer, header_id, name)
            if resolved is not None:
                row["abilities"][issuer] = resolved
            else:
                # The 2019 header lists it for this unit but burnysc2's (4.11.4) per-unit table does not: the id
                # itself (stableid.json / AbilityId) is sent; the doc flags the entry as unconfirmed for this issuer.
                row["abilities"][issuer] = sc2["AbilityId"](header_id).name
                row["not_in_python_sc2_unit_abilities"] += (issuer,)
        if row["category"] in {"train", "build", "morph"}:
            # TRAIN_INFO[issuer][unit] = creation ability + tech_requirement + require_attached (tech lab).
            for issuer, table in sc2["TRAIN_INFO"].items():
                if issuer.name not in row["issuers"]:
                    continue
                for unit, info in table.items():
                    exact = info["ability"]
                    if exact.value == header_id or (generic.get(exact) is not None and generic[exact].value == header_id):
                        row["produces"][issuer.name] = (
                            unit.name,
                            bool(info.get("requires_techlab", False)),
                            info["required_building"].name if info.get("required_building") else "",
                        )
        if row["category"] == "research":
            levels: list[tuple[str, str]] = []
            for building, table in research_info.items():
                if building.name not in row["issuers"]:
                    continue
                for upgrade, info in table.items():
                    exact = info["ability"]
                    if exact.value == header_id or (generic.get(exact) is not None and generic[exact].value == header_id):
                        required_building = info.get("required_building")
                        required_upgrade = info.get("required_upgrade")
                        levels.append(
                            (
                                upgrade.value, upgrade.name, exact.name,
                                required_building.name if required_building else "",
                                required_upgrade.name if required_upgrade else "",
                            )
                        )
            levels.sort()
            if levels:
                row["upgrades"] = tuple(item[1] for item in levels)
                row["upgrade_abilities"] = tuple(item[2] for item in levels)
                row["upgrade_requirements"] = tuple((item[3], item[4]) for item in levels)
            else:
                row["upgrades"] = EXTRA_UPGRADES[name]
                row["upgrade_abilities"] = (sc2["AbilityId"](header_id).name,) * len(row["upgrades"])
                row["upgrade_requirements"] = (("", ""),) * len(row["upgrades"])
            for upgrade_name in row["upgrades"]:
                assert upgrade_name in upgrade_id.__members__, upgrade_name
        facts.append(row)
    return facts


def render(facts: list[dict[str, Any]]) -> str:
    body = pprint.pformat(facts, width=118, sort_dicts=False)
    return (
        '"""GENERATED Terran command facts - do not edit by hand.\n\n'
        "Regenerate with ``python -m scripts.sc2_accuracy.gen_catalog_facts`` (see that module for the data sources).\n"
        "``tests/test_sc2_catalog.py`` fails when this file no longer matches the s2client-api header extract and\n"
        "burnysc2 tables, so a new Terran command cannot appear without a catalog entry.\n"
        '"""\n\nfrom __future__ import annotations\n\nfrom typing import Any, Final\n\n'
        f"TERRAN_COMMAND_FACTS: Final[tuple[dict[str, Any], ...]] = tuple({body})\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    text = render(build_facts())
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != text:
            print("sc2_catalog_data.py is stale; run: python -m scripts.sc2_accuracy.gen_catalog_facts", file=sys.stderr)
            return 1
        return 0
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUTPUT} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
