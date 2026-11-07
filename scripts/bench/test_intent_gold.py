"""Gold derivation for the voice-intent -> concrete Terran command corpus.

Run: python3 -m pytest scripts/bench/test_intent_gold.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

import build_intent_corpus as B  # noqa: E402
import intent_catalog as C  # noqa: E402

API = json.loads((HERE.parents[1] / "scripts/sc2_accuracy/s2client_api_enums.json").read_text(encoding="utf-8"))
TERRAN_API_NAMES = {n for u, a in API["unit_abilities"].items() if u.startswith("TERRAN_") for n in a}


def test_catalog_has_the_103_terran_commands_by_category():
    cat = C.terran_commands()
    assert len(cat) == 103
    counts: dict[str, int] = {}
    for spec in cat.values():
        counts[spec["category"]] = counts.get(spec["category"], 0) + 1
    assert counts == {"TRAIN": 17, "BUILD": 16, "RESEARCH": 24, "MORPH": 14, "EFFECT": 20, "BEHAVIOR": 4, "OTHER": 8}
    assert set(cat) <= TERRAN_API_NAMES


def test_gold_derivation_is_deterministic():
    assert B.build_rows() == B.build_rows()


def test_committed_corpus_equals_regenerated_corpus():
    on_disk = [json.loads(line) for line in B.CORPUS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert on_disk == B.build_rows()


def test_every_gold_command_exists_in_the_api_data():
    for row in B.build_rows():
        for cmd in row["gold"]["acceptable"]:
            base = cmd.split("#")[0].split("@")[0]
            assert base in TERRAN_API_NAMES, (row["id"], cmd)
            caster = cmd.split("@")[1] if "@" in cmd else None
            if caster:
                assert base in API["unit_abilities"]["TERRAN_" + caster], (row["id"], cmd)
        exact = row["gold"].get("exact")
        if exact:
            assert exact in row["gold"]["acceptable"], row["id"]


def test_corpus_shape_and_tiers():
    rows = B.build_rows()
    assert len(rows) >= 200
    assert len({r["id"] for r in rows}) == len(rows)
    assert len({(r["text"], r["state_name"]) for r in rows}) == len(rows)
    assert {r["tier"] for r in rows} == {"explicit", "intent", "ambiguous", "state", "negative"}
    for r in rows:
        assert r["gold"]["expect"] in {"execute", "ask", "choose_or_ask", "refuse"}
        assert set(r["state"]) >= {"structures", "researched", "minerals", "gas", "energy"}
        assert {"latency_ms", "tokens"} <= set(r["measure"])  # latency fields exist in the schema, never claimed
    assert sum(r["lang"] != "ko" for r in rows) >= 20


def test_every_catalog_category_is_reachable_by_some_row():
    seen = set()
    for row in B.build_rows():
        for cmd in row["gold"]["acceptable"]:
            seen.add(C.terran_commands()[cmd.split("#")[0].split("@")[0]]["category"])
    assert seen >= {"TRAIN", "BUILD", "RESEARCH", "MORPH", "EFFECT", "BEHAVIOR"}


def _state(structures, researched=()):
    return {"structures": list(structures), "researched": list(researched), "minerals": 2000, "gas": 2000, "energy": 100}


def test_multilevel_upgrade_resolves_the_next_unresearched_level_from_state():
    sel = {"commands": ["RESEARCH_TERRANINFANTRYWEAPONS"]}
    base = ["ENGINEERINGBAY", "ARMORY"]
    assert C.resolve(sel, _state(base))["acceptable"] == ["RESEARCH_TERRANINFANTRYWEAPONS#1"]
    one = C.resolve(sel, _state(base, ["TERRANINFANTRYWEAPONSLEVEL1"]))
    assert one["acceptable"] == ["RESEARCH_TERRANINFANTRYWEAPONS#2"] and one["expect"] == "execute"
    two = C.resolve(sel, _state(base, ["TERRANINFANTRYWEAPONSLEVEL1", "TERRANINFANTRYWEAPONSLEVEL2"]))
    assert two["acceptable"] == ["RESEARCH_TERRANINFANTRYWEAPONS#3"]


def test_fully_researched_family_has_nothing_to_execute():
    full = _state(["ENGINEERINGBAY", "ARMORY"], [f"TERRANINFANTRYWEAPONSLEVEL{i}" for i in (1, 2, 3)])
    out = C.resolve({"commands": ["RESEARCH_TERRANINFANTRYWEAPONS"]}, full)
    assert out["expect"] == "ask" and out["acceptable"] == []


def test_level_two_without_armory_asks_or_builds_the_armory():
    st = _state(["ENGINEERINGBAY", "FACTORY"], ["TERRANINFANTRYWEAPONSLEVEL1"])  # next missing step = the Armory
    out = C.resolve({"commands": ["RESEARCH_TERRANINFANTRYWEAPONS"]}, st)
    assert out["expect"] == "choose_or_ask"
    assert out["acceptable"] == ["BUILD_ARMORY"]


def test_missing_research_building_asks_or_builds_it():
    out = C.resolve({"commands": ["RESEARCH_STIMPACK"]}, _state(["BARRACKS"]))
    assert out["expect"] == "choose_or_ask"
    assert out["acceptable"] == ["BUILD_TECHLAB@BARRACKS"]
    ok = C.resolve({"commands": ["RESEARCH_STIMPACK"]}, _state(["BARRACKS", "BARRACKSTECHLAB"]))
    assert ok["expect"] == "execute" and ok["acceptable"] == ["RESEARCH_STIMPACK"] and ok["exact"] == "RESEARCH_STIMPACK"


def test_already_researched_single_upgrade_is_not_executable():
    st = _state(["BARRACKS", "BARRACKSTECHLAB"], ["STIMPACK"])
    out = C.resolve({"commands": ["RESEARCH_STIMPACK"]}, st)
    assert out["expect"] == "ask" and out["acceptable"] == []


def test_set_selector_collects_every_available_member():
    st = _state(["ENGINEERINGBAY", "ARMORY"], ["TERRANINFANTRYWEAPONSLEVEL1"])
    out = C.resolve({"commands": ["RESEARCH_TERRANINFANTRYWEAPONS", "RESEARCH_TERRANINFANTRYARMOR"]}, st)
    assert out["acceptable"] == ["RESEARCH_TERRANINFANTRYARMOR#1", "RESEARCH_TERRANINFANTRYWEAPONS#2"]
    assert out["exact"] is None and out["expect"] == "execute"


def test_caster_unspecified_lists_every_ground_caster_and_specified_one():
    st = _state(["BARRACKS", "FACTORY", "STARPORT"])
    anyc = C.resolve({"commands": ["BUILD_TECHLAB"]}, st)
    assert anyc["acceptable"] == ["BUILD_TECHLAB@BARRACKS", "BUILD_TECHLAB@FACTORY", "BUILD_TECHLAB@STARPORT"]
    one = C.resolve({"commands": ["BUILD_TECHLAB"], "caster": "FACTORY"}, st)
    assert one["acceptable"] == ["BUILD_TECHLAB@FACTORY"] and one["exact"] == "BUILD_TECHLAB@FACTORY"


def test_train_prerequisites_come_from_python_sc2_train_info():
    no_lab = C.resolve({"commands": ["TRAIN_SIEGETANK"]}, _state(["FACTORY"]))
    assert no_lab["expect"] == "choose_or_ask" and no_lab["acceptable"] == ["BUILD_TECHLAB@FACTORY"]
    ok = C.resolve({"commands": ["TRAIN_SIEGETANK"]}, _state(["FACTORY", "FACTORYTECHLAB"]))
    assert ok["expect"] == "execute" and ok["exact"] == "TRAIN_SIEGETANK"


def test_effect_needs_energy_from_the_adapter_cost_table():
    low = {**_state(["ORBITALCOMMAND"]), "energy": 20}
    out = C.resolve({"commands": ["EFFECT_SCAN"]}, low)
    assert out["expect"] == "ask" and out["acceptable"] == []
    assert C.resolve({"commands": ["EFFECT_SCAN"]}, _state(["ORBITALCOMMAND"]))["exact"] == "EFFECT_SCAN"


def test_explicit_and_intent_gold_never_typed_by_hand():
    # the utterance table carries selectors (API command names), never concrete '#level'/'@caster' strings
    import intent_utterances as U

    for spec in U.UTTERANCES:
        blob = json.dumps(spec.get("select", {}))
        assert "#" not in blob and "@" not in blob, spec["text"]
