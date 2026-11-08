"""Terran command catalog: data drift guard, lookup API and state-aware evaluation.

Offline evidence only (burnysc2 + the s2client-api header extract).  Nothing here proves a live game accepted an order.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sc2.ids.ability_id")

from scripts.sc2_accuracy import audit, gen_catalog_facts  # noqa: E402
from sc2.ids.ability_id import AbilityId  # noqa: E402
from sc2.ids.upgrade_id import UpgradeId  # noqa: E402

from starcraft_commander import sc2_catalog as cat  # noqa: E402

EXPECTED_COUNTS = {"train": 17, "build": 16, "research": 24, "morph": 14, "effect": 20, "behavior": 4, "other": 8}


# --------------------------------------------------------------------------- drift guard


def _header_commands() -> set[str]:
    header = audit.load_header_enums()
    names: set[str] = set()
    for unit, abilities in header["unit_abilities"].items():
        if unit.startswith("TERRAN_"):
            for ability in abilities:
                if ability in gen_catalog_facts.GENERIC_HEADER_ABILITIES or ability.startswith(gen_catalog_facts.EXCLUDED_PREFIXES):
                    continue
                names.add(ability)
    return names


def test_catalog_has_one_entry_per_header_command_and_the_documented_counts() -> None:
    entries = cat.all_commands()
    assert {e.header_name for e in entries} == _header_commands()
    assert len(entries) == len({e.id for e in entries}) == 103
    counts = {c: len(cat.commands_by_category(c)) for c in cat.CATEGORIES}
    assert counts == EXPECTED_COUNTS


def test_generated_facts_are_fresh() -> None:
    """A data refresh that adds/changes a Terran command is red until the module is regenerated and annotated."""

    assert gen_catalog_facts.render(gen_catalog_facts.build_facts()) == gen_catalog_facts.OUTPUT.read_text(encoding="utf-8")


def test_every_entry_ability_is_a_real_burnysc2_id_for_its_issuer() -> None:
    for entry in cat.all_commands():
        assert set(entry.abilities) == set(entry.issuers), entry.id
        for issuer, name in entry.abilities.items():
            member = AbilityId[name]
            if issuer not in entry.unconfirmed_issuers:
                assert member.value, (entry.id, issuer)
        for name in entry.upgrades:
            assert UpgradeId[name]


def test_every_entry_has_korean_and_english_aliases_and_text() -> None:
    for entry in cat.all_commands():
        assert entry.aliases_ko and entry.aliases_en and entry.description_ko and entry.effect, entry.id


# --------------------------------------------------------------------------- lookup


def test_lookup_by_id_alias_and_issuer() -> None:
    assert cat.get_command("research_terraninfantryweapons").upgrades == (
        "TERRANINFANTRYWEAPONSLEVEL1", "TERRANINFANTRYWEAPONSLEVEL2", "TERRANINFANTRYWEAPONSLEVEL3",
    )
    assert cat.find_by_alias("보병 공격력 업그레이드")[0].id == "research_terraninfantryweapons"
    assert cat.find_by_alias("infantry weapons")[0].id == "research_terraninfantryweapons"
    assert [e.id for e in cat.search("밴시 은신 연구", 1)] == ["research_bansheecloakingfield"]
    assert cat.search("탱크 시즈모드 켜줘", 1)[0].id == "morph_siegemode"
    assert "morph_siegemode" in {e.id for e in cat.commands_for_issuer("TERRAN_SIEGETANK")}
    assert {e.id for e in cat.commands_for_issuer("ORBITALCOMMAND")} >= {"effect_scan", "effect_calldownmule", "effect_supplydrop", "train_scv", "lift"}
    with pytest.raises(KeyError):
        cat.get_command("nope")


def test_ambiguous_phrase_returns_every_reading() -> None:
    ids = {e.id for e in cat.find_by_alias("스팀팩")}
    assert {"effect_stim"} <= ids


# --------------------------------------------------------------------------- state evaluation


def S(*owned, **kw):
    return cat.CatalogState(owned=tuple(owned), **kw)


def O(type_name, tag=1, **kw):
    kw.setdefault("is_structure", type_name in cat._STRUCTURE_ISSUER_TYPES)
    return cat.Owned(type_name, tag=tag, **kw)


def test_research_chooses_the_next_level_from_state() -> None:
    bay = O("ENGINEERINGBAY", 7)
    armory = O("ARMORY", 8)
    assert cat.evaluate("research_terraninfantryweapons", S(bay)).upgrade == "TERRANINFANTRYWEAPONSLEVEL1"
    done1 = frozenset({"TERRANINFANTRYWEAPONSLEVEL1"})
    refused = cat.evaluate("research_terraninfantryweapons", S(bay, upgrades_done=done1))
    assert not refused.ok and refused.reason == "missing_prerequisite: ARMORY" and "무기고" in refused.reason_ko
    assert cat.evaluate("research_terraninfantryweapons", S(bay, armory, upgrades_done=done1)).upgrade == "TERRANINFANTRYWEAPONSLEVEL2"
    pending = cat.evaluate("research_terraninfantryweapons", S(bay, armory, upgrades_done=done1, upgrades_pending=frozenset({"TERRANINFANTRYWEAPONSLEVEL2"})))
    assert pending.reason == "research_already_pending"
    allv = frozenset(cat.get_command("research_terraninfantryweapons").upgrades)
    assert cat.evaluate("research_terraninfantryweapons", S(bay, armory, upgrades_done=allv)).reason == "no_next_level"


def test_single_research_already_done_and_techlab_hosted() -> None:
    lab = O("BARRACKSTECHLAB", 3)
    assert cat.evaluate("research_stimpack", S(lab)).ok
    assert cat.evaluate("research_stimpack", S(lab, upgrades_done=frozenset({"STIMPACK"}))).reason == "already_researched"
    assert cat.evaluate("research_stimpack", S()).reason == "no_ready_idle_researcher"
    assert cat.evaluate("research_stimpack", S(O("BARRACKSTECHLAB", 3, idle=False))).reason == "structure_busy"


def test_energy_and_enemy_and_prerequisite_refusals_are_korean() -> None:
    low = cat.evaluate("effect_scan", S(O("ORBITALCOMMAND", 5, energy=10.0)))
    assert low.reason == "not_enough_energy" and low.reason_ko
    assert cat.evaluate("effect_scan", S(O("ORBITALCOMMAND", 5, energy=60.0))).ok
    assert cat.evaluate("effect_emp", S(O("GHOST", 6, energy=100.0), enemies_visible=0)).reason == "no_enemy_target"
    assert cat.evaluate("train_ghost", S(O("BARRACKS", 2, addon="techlab"))).reason == "missing_prerequisite: GHOSTACADEMY"
    assert cat.evaluate("train_marauder", S(O("BARRACKS", 2, addon="none"))).reason == "producer_needs_techlab"
    assert cat.evaluate("build_techlab", S(O("BARRACKS", 2, addon="reactor"))).reason == "addon_already_attached"
    assert cat.evaluate("build_reactor", S(O("BARRACKS", 2, addon="none"))).ok
    assert cat.evaluate("build_refinery", S(O("SCV", 9), free_geysers=0)).reason == "no_free_geyser"
    assert cat.evaluate("morph_orbitalcommand", S(O("COMMANDCENTER", 4))).reason == "missing_prerequisite: BARRACKS"


def test_issuable_now_lists_only_what_the_state_allows() -> None:
    state = S(O("SCV", 1), O("COMMANDCENTER", 2), O("SUPPLYDEPOT", 3))
    ids = {r.command_id for r in cat.issuable_now(state)}
    assert {"train_scv", "build_barracks", "morph_supplydepot_lower"} <= ids or {"train_scv", "build_barracks"} <= ids
    assert "effect_scan" not in ids and "research_stimpack" not in ids


# --------------------------------------------------------------------------- plain-language intent quality

import json  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402

import os  # noqa: E402

INTENT_CORPUS = Path(os.environ.get("VOI_INTENT_CORPUS") or Path(__file__).resolve().parents[1] / "scripts" / "bench" / "intent_corpus.jsonl")


def test_every_entry_has_two_korean_aliases_a_description_and_english_text() -> None:
    for entry in cat.all_commands():
        assert len(entry.aliases_ko) >= 2, entry.id
        assert entry.description_ko.strip() and entry.effect.strip() and entry.aliases_en, entry.id


def test_every_entry_is_reachable_from_its_own_first_alias_and_has_cues() -> None:
    from starcraft_commander.sc2_catalog_cues import CUES

    assert set(CUES) <= {e.id for e in cat.all_commands()}
    unreachable = []
    for entry in cat.all_commands():
        top = {e.id for e in cat.search(entry.aliases_ko[0], 5)}
        if entry.id not in top:
            unreachable.append((entry.id, entry.aliases_ko[0], sorted(top)))
    assert unreachable == []


# Held-out phrases: written AFTER the cues and NOT in scripts/bench/intent_corpus.jsonl (no tuning on them).
HELD_OUT = [
    ("탱크 사거리 늘려줘", {"morph_siegemode", "research_terranvehicleweapons"}),
    ("밴시 안 보이게 연구해", {"research_bansheecloakingfield"}),
    ("유령 투명해지는 거 연구", {"research_personalcloaking"}),
    ("보병 방어 업글", {"research_terraninfantryarmor"}),
    ("배틀크루저 순간이동", {"effect_tacticaljump"}),
    ("레이븐 터렛 깔아", {"effect_autoturret"}),
    ("벙커 팔아버려", {"effect_salvage"}),
    ("병영에 리액터 달아줘", {"build_reactor"}),
    ("군수공장에 테크랩 붙여", {"build_techlab"}),
    ("서플 내려줘", {"morph_supplydepot_lower"}),
    ("해병 피 깎고 달려", {"effect_stim"}),
    ("scan their natural", {"effect_scan"}),
    ("call down a mule", {"effect_calldownmule"}),
    ("lift the barracks", {"lift"}),
    ("siege the tanks", {"morph_siegemode"}),
    ("research combat shield", {"research_combatshield"}),
    ("vehicle weapons upgrade", {"research_terranvehicleweapons"}),
    ("토르 폭발 모드로", {"morph_thorexplosivemode"}),
    ("해방선 대공 모드로 돌아와", {"morph_liberatoraamode"}),
    ("벤시 클로킹 켜", {"behavior_cloakon"}),
]


def test_held_out_phrases_return_the_right_command_in_the_top_three() -> None:
    misses = []
    for text, expected in HELD_OUT:
        top = [e.id for e in cat.search(text, 3)]
        if not set(top) & expected:
            misses.append((text, sorted(expected), top))
    assert len(misses) <= 3, misses  # report-the-rate guard, not a perfection claim


def _gold_id(token: str) -> str:
    return re.split(r"[#@]", token)[0].lower()


@pytest.mark.skipif(not INTENT_CORPUS.is_file(), reason="scripts/bench/intent_corpus.jsonl is not on this branch")
def test_intent_corpus_rows_resolve_to_an_acceptable_command() -> None:
    rows = [json.loads(line) for line in INTENT_CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]
    intent = [r for r in rows if r["id"].startswith("int-")]
    assert len(intent) == 57
    top1 = top3 = 0
    for row in intent:
        acceptable = {_gold_id(g) for g in row["gold"]["acceptable"]}
        ranked = [e.id for e in cat.search(row["text"], 3)]
        top1 += bool(ranked) and ranked[0] in acceptable
        top3 += bool(set(ranked) & acceptable)
    assert top3 / len(intent) >= 0.85, (top1, top3)


# --------------------------------------------------------------------------- search_scored / disambiguation (WireAgent)


def test_search_scored_matches_search_and_is_ranked() -> None:
    from starcraft_commander import sc2_catalog as catalog

    for phrase in ("upgrade infantry armor", "탱크 사거리 늘려줘", "보병 업 해줘"):
        scored = catalog.search_scored(phrase)
        assert tuple(entry for entry, _ in scored) == catalog.search(phrase)
        assert [score for _, score in scored] == sorted((score for _, score in scored), reverse=True)


@pytest.mark.parametrize("phrase", ["시즈탱크 뽑아", "시즈탱크 생산", "시즈탱크 만들어", "탱크 뽑아", "시즈탱크 양산"])
def test_tank_production_verbs_beat_siege_morph(phrase) -> None:
    from starcraft_commander import sc2_catalog as catalog

    ids = [entry.id for entry in catalog.search(phrase)]
    assert ids[:1] == ["train_siegetank"], (phrase, ids)
    assert "morph_siegemode" not in ids and "morph_unsiege" not in ids


@pytest.mark.parametrize(
    "phrase,winner",
    [
        ("upgrade infantry armor", "research_terraninfantryarmor"),
        ("upgrade infantry weapons", "research_terraninfantryweapons"),
        ("upgrade infantry attack", "research_terraninfantryweapons"),
    ],
)
def test_infantry_upgrade_names_the_right_family(phrase, winner) -> None:
    from starcraft_commander import sc2_catalog as catalog

    scored = catalog.search_scored(phrase)
    assert scored[0][0].id == winner and scored[0][1] > scored[1][1], scored


def test_bare_infantry_upgrade_stays_a_tie() -> None:
    from starcraft_commander import sc2_catalog as catalog

    scored = catalog.search_scored("보병 업 해줘")
    assert {e.id for e, _ in scored[:2]} == {"research_terraninfantryarmor", "research_terraninfantryweapons"}
    assert scored[0][1] == scored[1][1]
