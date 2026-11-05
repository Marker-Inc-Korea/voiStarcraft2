"""Offline SC2 id/ability/upgrade accuracy audit of every token the product accepts.

Three independent data sources are cross-checked; none needs StarCraft II:

* ``stableid.json`` (Blizzard's id table shipped with the game client; path from
  ``VOI_SC2_STABLEID`` or the macOS default) - Units/Abilities/Upgrades/Buffs.
* ``sc2.ids`` / ``sc2.dicts`` from burnysc2 (python-sc2), the library the Direct
  adapter drives at runtime.
* ``s2client_api_enums.json`` - the Blizzard s2client-api C++ ``sc2_typeenums.h``
  (MIT) the MicroMachine bot compiles against (ids and ability *target kinds*).

Usage::

    python -m scripts.sc2_accuracy.audit --json out.json [--markdown out.md]

Everything printed here is OFFLINE evidence. Nothing in this module proves an
ability fired in a live game.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DEFAULT_STABLEID = Path.home() / "Library/Application Support/Blizzard/StarCraft II/stableid.json"
SC2_UNIT_PREFIX = "TERRAN_"


def stableid_path() -> Path:
    return Path(os.environ.get("VOI_SC2_STABLEID", "") or DEFAULT_STABLEID)


def load_stableid(path: Path | None = None) -> dict[str, Any] | None:
    path = path or stableid_path()
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_header_enums() -> dict[str, Any]:
    return json.loads((HERE / "s2client_api_enums.json").read_text(encoding="utf-8"))


def _sc2():
    """Import python-sc2 pieces lazily; ``None`` when burnysc2 is not installed."""

    try:
        from sc2.dicts.generic_redirect_abilities import GENERIC_REDIRECT_ABILITIES
        from sc2.dicts.unit_abilities import UNIT_ABILITIES
        from sc2.dicts.unit_research_abilities import RESEARCH_INFO
        from sc2.dicts.unit_train_build_abilities import TRAIN_INFO
        from sc2.dicts.unit_trained_from import UNIT_TRAINED_FROM
        from sc2.dicts.upgrade_researched_from import UPGRADE_RESEARCHED_FROM
        from sc2.ids.ability_id import AbilityId
        from sc2.ids.id_version import ID_VERSION_STRING
        from sc2.ids.unit_typeid import UnitTypeId
        from sc2.ids.upgrade_id import UpgradeId
    except ImportError:
        return None
    return {
        "AbilityId": AbilityId,
        "UnitTypeId": UnitTypeId,
        "UpgradeId": UpgradeId,
        "UNIT_ABILITIES": UNIT_ABILITIES,
        "GENERIC": GENERIC_REDIRECT_ABILITIES,
        "RESEARCH_INFO": RESEARCH_INFO,
        "TRAIN_INFO": TRAIN_INFO,
        "TRAINED_FROM": UNIT_TRAINED_FROM,
        "RESEARCHED_FROM": UPGRADE_RESEARCHED_FROM,
        "ID_VERSION": ID_VERSION_STRING,
    }


def terran_unit_closure(sc2: dict[str, Any]) -> set[Any]:
    """Every UnitTypeId reachable from SCV through python-sc2's trained-from data."""

    unit = sc2["UnitTypeId"]
    reach = {unit.SCV}
    changed = True
    while changed:
        changed = False
        for produced, producers in sc2["TRAINED_FROM"].items():
            if produced not in reach and producers & reach:
                reach.add(produced)
                changed = True
    return reach


def python_unit_name(token: str) -> str:
    """DSL token -> python-sc2 ``UnitTypeId`` name (TERRAN_X -> X, ADDON_X -> ADDONX)."""

    name = token[len(SC2_UNIT_PREFIX):] if token.startswith(SC2_UNIT_PREFIX) else token
    return name.replace("_", "")


# --------------------------------------------------------------------------- tokens


def enumerate_product_tokens() -> dict[str, Any]:
    """Collect every id-bearing token the product accepts, per table."""

    from starcraft_commander import micromachine_command_execution as mce
    from starcraft_commander import micromachine_terran_capabilities as caps
    from starcraft_commander import policy_modulation as pm
    from starcraft_commander import policy_modulation_provider as provider
    from starcraft_commander import realtime_intent as ri

    unit_tokens: dict[str, set[str]] = defaultdict(set)

    def add(source: str, tokens) -> None:
        for token in tokens:
            unit_tokens[token].add(source)

    add("policy_modulation.MICROMACHINE_ALLOWED_UNIT_TOKENS", pm.MICROMACHINE_ALLOWED_UNIT_TOKENS)
    add("policy_modulation.MICROMACHINE_ALLOWED_BUILDING_TOKENS", pm.MICROMACHINE_ALLOWED_BUILDING_TOKENS)
    add("policy_modulation.MICROMACHINE_ALLOWED_TASK_TOKENS", pm.MICROMACHINE_ALLOWED_TASK_TOKENS)
    add(
        "policy_modulation.MICROMACHINE_CANONICAL_TASK_TOKEN_ALIASES(values)",
        set(pm.MICROMACHINE_CANONICAL_TASK_TOKEN_ALIASES.values()),
    )
    add("policy_modulation.MICROMACHINE_CANONICAL_UNIT_FORM_TOKENS", pm.MICROMACHINE_CANONICAL_UNIT_FORM_TOKENS)
    add("policy_modulation.MICROMACHINE_CANONICAL_UNIT_FORM_TOKENS(values)", pm.MICROMACHINE_CANONICAL_UNIT_FORM_TOKENS.values())
    add("realtime_intent.UNIT_TYPES", ri.UNIT_TYPES)
    add("realtime_intent.PRODUCTION_TARGETS", ri.PRODUCTION_TARGETS)
    for family in caps.TERRAN_UNIT_FAMILIES:
        add(f"micromachine_terran_capabilities.family[{family.family}]", family.unit_types)

    ability_tokens: dict[str, set[str]] = defaultdict(set)
    for token in pm.MICROMACHINE_TACTICAL_ABILITIES:
        if token:
            ability_tokens[token].add("policy_modulation.MICROMACHINE_TACTICAL_ABILITIES")
    for token in ri.ABILITIES:
        ability_tokens[token].add("realtime_intent.ABILITIES")
    for state in caps.TERRAN_ABILITY_CASTER_STATES:
        ability_tokens[state.ability].add("micromachine_terran_capabilities.TERRAN_ABILITY_CASTER_STATES")

    return {
        "unit_tokens": {k: sorted(v) for k, v in sorted(unit_tokens.items())},
        "ability_tokens": {k: sorted(v) for k, v in sorted(ability_tokens.items())},
        "cpp_markers": {k: list(v) for k, v in mce._TERRAN_ABILITY_ACTION_MARKERS.items()},
        "caster_states": {s.ability: [s.unit_type, s.cloak_state] for s in caps.TERRAN_ABILITY_CASTER_STATES},
        "ability_aliases": dict(provider._TACTICAL_ABILITY_ALIASES),
        "task_token_aliases": dict(pm.MICROMACHINE_CANONICAL_TASK_TOKEN_ALIASES),
        "family_aliases": {f.family: list(f.aliases) for f in caps.TERRAN_UNIT_FAMILIES},
    }


# --------------------------------------------------------------------------- resolution


def audit_unit_tokens(tokens, stable, sc2, header) -> list[dict[str, Any]]:
    units_by_name = {u["name"].upper().replace(" ", "_"): u for u in stable["Units"]} if stable else {}
    header_units = {name: int(num) for num, name in header["units"].items()}
    closure = terran_unit_closure(sc2) if sc2 else set()
    unit_enum = sc2["UnitTypeId"] if sc2 else None
    rows = []
    for token, sources in tokens.items():
        name = python_unit_name(token)
        row: dict[str, Any] = {"token": token, "sources": sources, "python_sc2_name": name}
        enum_member = getattr(unit_enum, name, None) if unit_enum else None
        row["enum_id"] = enum_member.value if enum_member is not None else None
        sid = units_by_name.get(name)
        row["stableid_id"] = sid["id"] if sid else None
        row["stableid_name"] = sid["name"] if sid else None
        row["s2client_header_id"] = header_units.get(f"TERRAN_{name}")
        row["terran"] = (enum_member in closure) if enum_member is not None and sc2 else None
        ids = {v for v in (row["enum_id"], row["stableid_id"], row["s2client_header_id"]) if v is not None}
        if row["enum_id"] is None and row["stableid_id"] is None:
            row["status"] = "NO_SC2_ID"  # not a unit type: upgrade/ability/addon pseudo token
        elif len(ids) > 1:
            row["status"] = "ID_MISMATCH"
        elif row["terran"] is False:
            row["status"] = "NOT_TERRAN"
        else:
            row["status"] = "ok"
        rows.append(row)
    return rows


def audit_pseudo_tokens(sc2, stable) -> list[dict[str, Any]]:
    """Research/ability pseudo tokens carried in ``production_targets`` (STIMPACK, ...)."""

    pseudo = ("STIMPACK", "COMBATSHIELD", "GHOST_CLOAK", "BANSHEE_CLOAK", "YAMATO_CANNON", "TERRAN_NUKE")
    upgrades = {u["name"].upper(): u for u in stable["Upgrades"]} if stable else {}
    rows = []
    adapter = _adapter()
    for token in pseudo:
        row: dict[str, Any] = {"token": token}
        resolved = adapter._resolve_upgrade(token) if adapter else None
        row["adapter_resolves_to"] = _enum_label(resolved)
        row["adapter_upgrade_id"] = getattr(resolved, "value", None)
        researcher = sc2["RESEARCHED_FROM"].get(resolved) if sc2 and resolved is not None else None
        row["researched_from"] = researcher.name if researcher is not None else None
        row["stableid_upgrade"] = (upgrades.get(getattr(resolved, "name", "")) or {}).get("name")
        if token == "TERRAN_NUKE":
            row["status"] = "unit_token(see unit table)"
        elif resolved is None:
            row["status"] = "UNRESOLVED_UPGRADE"
        elif researcher is None:
            row["status"] = "NO_RESEARCH_BUILDING(adapter id has no research ability)"
        else:
            row["status"] = "ok"
        rows.append(row)
    return rows


def _adapter():
    try:
        from starcraft_commander.python_sc2_adapter import PythonSC2BotAdapter
    except Exception:  # noqa: BLE001
        return None
    return PythonSC2BotAdapter(bot=object())


def _enum_label(value: Any) -> str | None:
    return None if value is None else f"{type(value).__name__}.{getattr(value, 'name', value)}={getattr(value, 'value', value)}"


def audit_abilities(product, stable, sc2, header) -> list[dict[str, Any]]:
    """Resolve each semantic ability the way the product does, then verify the caster."""

    adapter = _adapter()
    abilities_by_id = {a["id"]: a for a in stable["Abilities"]} if stable else {}
    header_abilities = {int(num): (name, target) for num, (name, target) in header["abilities"].items()}
    ability_enum = sc2["AbilityId"] if sc2 else None
    unit_enum = sc2["UnitTypeId"] if sc2 else None
    unit_abilities = sc2["UNIT_ABILITIES"] if sc2 else {}
    generic = sc2["GENERIC"] if sc2 else {}
    rows = []
    for token in sorted(product["ability_tokens"]):
        resolved = adapter._resolve_ability(token) if adapter else None
        row: dict[str, Any] = {
            "ability_token": token,
            "adapter_resolves_to": _enum_label(resolved),
            "ability_id": getattr(resolved, "value", None),
        }
        caster_token, cloak = product["caster_states"].get(token, [None, ""])
        row["caster_unit_type"] = caster_token
        row["caster_cloak_state"] = cloak
        sid = abilities_by_id.get(row["ability_id"]) if row["ability_id"] is not None else None
        row["stableid_ability"] = None if sid is None else {k: sid[k] for k in ("id", "name", "buttonname") if k in sid}
        header_entry = header_abilities.get(row["ability_id"]) if row["ability_id"] is not None else None
        row["s2client_header"] = list(header_entry) if header_entry else None
        row["target_kind"] = header_entry[1] if header_entry else None
        if resolved is None:
            row["status"] = "UNRESOLVED"
        elif caster_token and ability_enum is not None:
            caster = getattr(unit_enum, python_unit_name(caster_token), None)
            allowed = unit_abilities.get(caster, set()) if caster is not None else set()
            redirected = {generic.get(a, a) for a in allowed}
            row["caster_abilities_contains_exact"] = resolved in allowed
            row["caster_abilities_contains_via_generic"] = resolved in redirected
            row["status"] = "ok" if (resolved in allowed or resolved in redirected) else "CASTER_CANNOT_CAST"
        else:
            row["status"] = "ok(no caster check)"
        rows.append(row)
    return rows


def audit_cpp_markers(product, header, sc2) -> list[dict[str, Any]]:
    """C++ ``AbilityTypeToName`` strings the Python evidence matcher waits for."""

    by_name = {name: (int(num), target) for num, (name, target) in header["abilities"].items()}
    adapter = _adapter()
    rows = []
    for token, markers in sorted(product["cpp_markers"].items()):
        resolved = adapter._resolve_ability(token) if adapter else None
        for marker in markers:
            row: dict[str, Any] = {"ability_token": token, "marker": marker}
            if marker.isdigit():
                row["marker_id"] = int(marker)
                row["in_s2client_header"] = int(marker) in {v[0] for v in by_name.values()}
            else:
                hit = by_name.get(marker)
                row["marker_id"] = hit[0] if hit else None
                row["in_s2client_header"] = hit is not None
            row["adapter_ability_id"] = getattr(resolved, "value", None)
            row["same_id_as_adapter"] = (
                None if resolved is None or row["marker_id"] is None else resolved.value == row["marker_id"]
            )
            rows.append(row)
    return rows


def audit_alias_collisions(product) -> dict[str, Any]:
    """Aliases that point at two different entities, across and within tables."""

    entries: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for alias, value in product["task_token_aliases"].items():
        entries[alias.casefold()].add(("task_token", value))
    for alias, value in product["ability_aliases"].items():
        entries[alias.casefold()].add(("ability", value))
    for family, aliases in product["family_aliases"].items():
        for alias in aliases:
            entries[alias.casefold()].add(("unit_family", family))
    cross_kind, same_kind = [], []
    for alias, targets in sorted(entries.items()):
        kinds = defaultdict(set)
        for kind, value in targets:
            kinds[kind].add(value)
        # Same kind, different entity is a genuine collision.
        for kind, values in kinds.items():
            if len(values) > 1:
                same_kind.append({"alias": alias, "kind": kind, "targets": sorted(values)})
        if len(kinds) > 1:
            cross_kind.append({"alias": alias, "targets": sorted(targets)})
    # unit_family aliases vs task_token unit entities should name the same unit.
    mismatched_units = []
    for alias, targets in sorted(entries.items()):
        tokens = {v for k, v in targets if k == "task_token" and v.startswith(SC2_UNIT_PREFIX)}
        families = {v for k, v in targets if k == "unit_family"}
        if tokens and families:
            from starcraft_commander.micromachine_terran_capabilities import canonical_terran_unit_family

            if {canonical_terran_unit_family(t) for t in tokens} != families:
                mismatched_units.append({"alias": alias, "task_tokens": sorted(tokens), "families": sorted(families)})
    return {"same_kind_collisions": same_kind, "cross_kind_dual_use": cross_kind, "unit_vs_family_mismatch": mismatched_units}


def audit_research_and_build(sc2) -> dict[str, Any]:
    """Producer/researcher metadata the router emits vs python-sc2 truth."""

    if not sc2:
        return {}
    from starcraft_commander import unified_command_router as router  # noqa: F401

    unit = sc2["UnitTypeId"]
    out: dict[str, Any] = {"train_producers": [], "build_abilities": []}
    trained_from = sc2["TRAINED_FROM"]
    for name in (
        "SCV MARINE MARAUDER REAPER GHOST HELLION SIEGETANK CYCLONE THOR WIDOWMINE MEDIVAC "
        "VIKINGFIGHTER LIBERATOR BANSHEE RAVEN BATTLECRUISER"
    ).split():
        member = getattr(unit, name)
        out["train_producers"].append(
            {
                "unit": name,
                "sc2_trained_from": sorted(p.name for p in trained_from.get(member, ())),
                "router_producer": _router_producer(name),
            }
        )
    train = sc2["TRAIN_INFO"].get(unit.SCV, {})
    for built, info in sorted(train.items(), key=lambda item: item[0].name):
        out["build_abilities"].append(
            {"structure": built.name, "ability": info["ability"].name, "requires_placement_position": info.get("requires_placement_position", False)}
        )
    return out


def _router_producer(unit_name: str) -> str:
    """The producer the router attaches for a unit, read from the shipped table."""

    from starcraft_commander import unified_command_router as router

    table = getattr(router, "SC2_TRAIN_PRODUCERS", None)
    if table is not None:
        return table.get(unit_name, "")
    # pre-refactor routers kept the table inline; call the lowering for a probe
    from starcraft_commander.policy_modulation_provider import compile_policy_modulation_provider_output as compile_output

    result = compile_output(
        {
            "goal": "audit",
            "command_layer": "operation",
            "tactical_task": {"task_type": "train_unit", "production_targets": [f"TERRAN_{unit_name}"]},
        }
    )
    if result.vector is None:
        return f"<refused:{getattr(result, 'refusal_reason', '')}>"
    plan = router.route_policy_vector(result.vector).direct_plan
    if plan is None or not plan.ordered_actions:
        return "<no action>"
    return str(plan.ordered_actions[-1].metadata.get("producer", ""))


def source_versions(stable_path: Path, stable, sc2) -> dict[str, Any]:
    import importlib.metadata as md

    def ver(name: str) -> str | None:
        try:
            return md.version(name)
        except md.PackageNotFoundError:
            return None

    info: dict[str, Any] = {
        "stableid_path": str(stable_path),
        "stableid_present": stable is not None,
        "burnysc2": ver("burnysc2"),
        "s2clientprotocol_dist": ver("s2clientprotocol") or ver("pys2clientprotocol"),
        "protobuf": ver("protobuf"),
        "python_sc2_id_version_string": sc2["ID_VERSION"] if sc2 else None,
        "s2client_api_header_commit": load_header_enums()["_provenance"]["commit"],
    }
    if stable is not None:
        info["stableid_counts"] = {k: len(v) for k, v in stable.items()}
        info["stableid_mtime_epoch"] = int(stable_path.stat().st_mtime)
    return info


def run_audit() -> dict[str, Any]:
    stable_path = stableid_path()
    stable = load_stableid(stable_path)
    sc2 = _sc2()
    header = load_header_enums()
    product = enumerate_product_tokens()
    report: dict[str, Any] = {
        "versions": source_versions(stable_path, stable, sc2),
        "unit_tokens": audit_unit_tokens(product["unit_tokens"], stable, sc2, header),
        "pseudo_tokens": audit_pseudo_tokens(sc2, stable),
        "abilities": audit_abilities(product, stable, sc2, header),
        "cpp_markers": audit_cpp_markers(product, header, sc2),
        "alias_collisions": audit_alias_collisions(product),
        "research_and_build": audit_research_and_build(sc2),
    }
    report["summary"] = summarize(report)
    return report


def summarize(report: dict[str, Any]) -> dict[str, Any]:
    def count(rows, key="status"):
        tally: dict[str, int] = defaultdict(int)
        for row in rows:
            tally[str(row.get(key))] += 1
        return dict(sorted(tally.items()))

    return {
        "unit_tokens": count(report["unit_tokens"]),
        "pseudo_tokens": count(report["pseudo_tokens"]),
        "abilities": count(report["abilities"]),
        "cpp_markers_not_in_header": [r["marker"] for r in report["cpp_markers"] if not r["in_s2client_header"]],
        "cpp_markers_id_differs_from_adapter": [
            r["marker"] for r in report["cpp_markers"] if r["same_id_as_adapter"] is False
        ],
        "alias_same_kind_collisions": len(report["alias_collisions"]["same_kind_collisions"]),
    }


def to_markdown(report: dict[str, Any]) -> str:
    lines = ["| ability token | adapter resolves to | caster | status |", "|---|---|---|---|"]
    for row in report["abilities"]:
        lines.append(
            f"| `{row['ability_token']}` | {row['adapter_resolves_to']} | {row['caster_unit_type'] or ''} | {row['status']} |"
        )
    lines += ["", "| pseudo token | adapter resolves to | researched from | status |", "|---|---|---|---|"]
    for row in report["pseudo_tokens"]:
        lines.append(f"| `{row['token']}` | {row['adapter_resolves_to']} | {row['researched_from']} | {row['status']} |")
    lines += ["", "| unit token | python-sc2 | stableid id | header id | status |", "|---|---|---|---|---|"]
    for row in report["unit_tokens"]:
        if row["status"] != "ok":
            lines.append(
                f"| `{row['token']}` | {row['enum_id']} | {row['stableid_id']} | {row['s2client_header_id']} | {row['status']} |"
            )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, help="write the machine-readable report here")
    parser.add_argument("--markdown", type=Path, help="write the mismatch tables here")
    args = parser.parse_args(argv)
    report = run_audit()
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    if args.markdown:
        args.markdown.write_text(to_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
