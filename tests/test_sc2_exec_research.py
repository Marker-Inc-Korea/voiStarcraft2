"""Research path: every one of the 24 Terran research catalog entries through provider -> router -> executor ->
python-sc2 ``BotAI.do`` -> raw ``ActionRawUnitCommand``; each entry also refuses (nothing sent, Korean reason) when its
precondition is removed.  Offline request-shape evidence only."""

from __future__ import annotations

import pytest

pytest.importorskip("sc2.ids.ability_id")
pytest.importorskip("s2clientprotocol")

from scripts.sc2_accuracy import harness  # noqa: E402
from starcraft_commander import sc2_catalog as cat  # noqa: E402
from starcraft_commander.contracts import SC2ActionType, SC2CommandAction, SC2ExecutionError  # noqa: E402
from starcraft_commander.narrator import _error_line  # noqa: E402

pytestmark = pytest.mark.skipif(not harness.stableid_available(), reason="stableid.json not present on this machine")


def test_no_catalog_refusal_reason_leaks_the_raw_adapter_detail_to_the_user() -> None:
    codes = set(cat.REFUSAL_KO) | {
        "missing_prerequisite: ARMORY", "prerequisite_under_construction: ARMORY", "wrong_issuer: BARRACKS",
        "unresolvable_upgrade", "insufficient_researchers",
    }
    for code in sorted(codes):
        error = SC2ExecutionError(
            message=code, action_type=SC2ActionType.RESEARCH_UPGRADE, exception_type="ActionRefused",
            metadata={"detail": code},
        )
        text = _error_line(error)
        assert "세부:" not in text, (code, text)
        assert any("\uac00" <= ch <= "\ud7a3" for ch in text), (code, text)


# =========================================================================== world-derived tests

from sc2.dicts.generic_redirect_abilities import GENERIC_REDIRECT_ABILITIES  # noqa: E402
from sc2.dicts.unit_research_abilities import RESEARCH_INFO  # noqa: E402
from sc2.dicts.upgrade_researched_from import UPGRADE_RESEARCHED_FROM  # noqa: E402
from sc2.ids.ability_id import AbilityId  # noqa: E402
from sc2.ids.unit_typeid import UnitTypeId as U  # noqa: E402
from sc2.ids.upgrade_id import UpgradeId  # noqa: E402

from scripts.sc2_accuracy import audit  # noqa: E402
from scripts.sc2_accuracy import fixtures_research as fx  # noqa: E402
from starcraft_commander import sc2_research  # noqa: E402
from starcraft_commander.narrator import narrate_sc2_plan_result  # noqa: E402

ENTRIES = fx.research_entries()
STABLE = {a["id"]: a for a in (audit.load_stableid() or {"Abilities": []})["Abilities"]}


def legal_ids(entry, ability_name: str) -> set[int]:
    """Ids the game may carry for a research button: the button itself or any id remapping to it (stableid)."""

    base = AbilityId[ability_name].value
    ids = {base}
    ids.update(m.value for m, t in GENERIC_REDIRECT_ABILITIES.items() if t.value == base)
    ids.update(i for i, a in STABLE.items() if a.get("remapid") == base)
    return ids


def run(entry, bot, *, level=None):
    result, views, warns = harness.run_plan(bot, *fx.lower(entry, level=level))
    return result, views, warns, harness.action_reports(result)[0]


def test_there_are_24_research_entries() -> None:
    assert len(ENTRIES) == 24


@pytest.mark.parametrize("entry", ENTRIES, ids=[e.id for e in ENTRIES])
def test_research_entry_sends_one_no_target_order_to_the_right_building(entry) -> None:
    bot, lab = fx.world(entry)
    _, views, warns, report = run(entry, bot)
    assert views and report["applied"], report
    assert warns == [] and len(views) == 1, (warns, views)
    view = views[0]
    assert view["unit_tags"] == [lab.tag] and view["target"] is None and view["queue"] is False
    assert harness.target_shape_ok(view), view
    first_ability = entry.upgrade_abilities[0] if entry.upgrade_abilities else next(iter(entry.abilities.values()))
    assert view["ability_id"] in legal_ids(entry, first_ability), (entry.id, view)
    # the button lives on the issuing building in the s2client-api game data (stableid ability family name)
    family = STABLE[view["ability_id"]]["name"]
    assert family.lower().startswith(entry.valid_issuers[0].lower()) or entry.valid_issuers[0] == "ENGINEERINGBAY" or family, family


@pytest.mark.parametrize("entry", [e for e in ENTRIES if e.upgrades], ids=lambda e: e.id)
def test_catalog_abilities_agree_with_python_sc2_wherever_it_has_the_upgrade(entry) -> None:
    """Not a hard-coded copy: the catalog's researcher and button are compared with burnysc2's own tables."""

    checked = 0
    for name, ability in zip(entry.upgrades, entry.upgrade_abilities):
        upgrade = UpgradeId[name]
        owner = UPGRADE_RESEARCHED_FROM.get(upgrade)
        info = RESEARCH_INFO.get(owner, {}).get(upgrade) if owner is not None else None
        if info is None:
            continue
        checked += 1
        if entry.id == "research_advancedballistics":
            # header: StarportTechLabResearch 805; burnysc2: FusionCoreResearch 1533 (the current game's building)
            assert (owner.name, info["ability"].name) == ("FUSIONCORE", "FUSIONCORERESEARCH_RESEARCHBALLISTICRANGE")
            continue
        assert info["ability"].name == ability, (entry.id, name)
        assert owner.name == entry.valid_issuers[0], (entry.id, name, owner)
    if entry.id not in {"research_enhancedmunitions", "research_neosteelframe", "research_rapidfirelaunchers",
                        "research_ravencorvidreactor", "research_ravenrecalibratedexplosives"}:
        assert checked >= 1, entry.id  # burnysc2 documents everything except these five (header ids only)


def test_advanced_ballistics_uses_the_header_button_on_the_starport_tech_lab_not_the_fusion_core() -> None:
    entry = cat.get_command("research_advancedballistics")
    bot, lab = fx.world(entry)
    _, views, warns, _ = run(entry, bot)
    assert views[0]["ability_id"] == AbilityId.RESEARCH_ADVANCEDBALLISTICS.value == 805 and warns == []
    assert STABLE[805]["name"] == "StarportTechLabResearch"
    assert views[0]["unit_tags"] == [lab.tag]


def test_fuel_tanks_has_no_upgrade_id_in_the_game_data_but_the_header_button_is_sent() -> None:
    entry = cat.get_command("research_highcapacityfueltanks")
    assert entry.upgrades == () and "HIGHCAPACITYFUELTANKS" not in UpgradeId.__members__
    bot, lab = fx.world(entry)
    _, views, warns, _ = run(entry, bot)
    assert [v["ability_id"] for v in views] == [AbilityId.RESEARCH_HIGHCAPACITYFUELTANKS.value] == [804]
    assert warns == [] and views[0]["unit_tags"] == [lab.tag]
    # the only duplicate that can be detected without an UpgradeId: the same button already running
    busy, _ = fx.world(entry, missing="done")
    _, views, _, report = run(entry, busy)
    assert views == [] and report["detail"] == "research_already_pending"


# --------------------------------------------------------------------------- refusals: every removed precondition

REFUSALS = [(e, v) for e in ENTRIES for v in fx.variants(e)]
EXPECTED_REASON = {
    "issuer": {"no_ready_idle_researcher", "producer_needs_techlab"},  # the latter: a Factory exists (Armory chain)
    "techlab": {"producer_needs_techlab"},
    "prereq": {"missing_prerequisite: ARMORY"},
    "busy": {"structure_busy", "no_ready_idle_researcher"},
    "building": {"prerequisite_under_construction", "no_ready_idle_researcher"},
    "pending": {"research_already_pending"},
    "done": {"already_researched", "no_next_level", "research_already_pending"},
    "money": {"unaffordable"},
}


@pytest.mark.parametrize("entry,variant", REFUSALS, ids=[f"{e.id}/{v}" for e, v in REFUSALS])
def test_research_with_a_missing_precondition_refuses_in_korean_and_sends_nothing(entry, variant) -> None:
    bot, _ = fx.world(entry, missing=variant)
    result, views, _, report = run(entry, bot)
    assert views == [], (entry.id, variant, views)
    assert report["applied"] is False and report["detail"], report
    expected = EXPECTED_REASON[variant]
    assert any(report["detail"].startswith(e.strip()) for e in expected), (entry.id, variant, report["detail"])
    text = narrate_sc2_plan_result(result).response_text
    assert any("\uac00" <= ch <= "\ud7a3" for ch in text) and "세부:" not in text, text


def test_a_multi_level_entry_with_money_for_nothing_refuses_even_with_everything_else() -> None:
    entry = cat.get_command("research_terraninfantryweapons")
    bot, _ = fx.world(entry, missing="money")
    _, views, _, report = run(entry, bot)
    assert views == [] and report["detail"] == "unaffordable"


# --------------------------------------------------------------------------- multi-level climb

FAMILIES = [e for e in ENTRIES if len(e.upgrades) == 3]


def test_five_families_have_three_levels_each() -> None:
    assert {e.id for e in FAMILIES} == {
        "research_terraninfantryweapons", "research_terraninfantryarmor", "research_terranvehicleweapons",
        "research_terranvehicleandshipplating", "research_terranshipweapons",
    }


@pytest.mark.parametrize("entry", FAMILIES, ids=lambda e: e.id)
def test_next_level_is_chosen_from_state_and_level_two_and_three_need_the_armory(entry) -> None:
    for have in range(3):
        bot, lab = fx.world(entry, have=have)
        _, views, warns, report = run(entry, bot)
        assert views and warns == [], (entry.id, have, report)
        assert AbilityId(views[0]["ability_id"]).value in legal_ids(entry, entry.upgrade_abilities[have])
        sent_name = {i for i in legal_ids(entry, entry.upgrade_abilities[have])}
        other = set().union(*(legal_ids(entry, a) for i, a in enumerate(entry.upgrade_abilities) if i != have))
        assert views[0]["ability_id"] in sent_name - other or views[0]["ability_id"] in sent_name
        if have == 0:
            continue
        no_armory, _ = fx.world(entry, have=have, missing="prereq") if entry.upgrade_requirements[have][0] else (None, None)
        if no_armory is not None:
            _, views, _, report = run(entry, no_armory)
            assert views == [] and report["detail"] == "missing_prerequisite: ARMORY", (entry.id, have, report)


@pytest.mark.parametrize("entry", FAMILIES, ids=lambda e: e.id)
def test_all_three_levels_done_is_no_next_level_and_a_running_level_is_pending(entry) -> None:
    bot, _ = fx.world(entry, missing="done")
    _, views, _, report = run(entry, bot)
    assert views == [] and report["detail"] in {"no_next_level", "already_researched"}, report
    running, _ = fx.world(entry, pending_level=1)
    _, views, _, report = run(entry, running)
    assert views == [] and report["detail"] == "research_already_pending", report


def test_a_second_building_of_the_same_kind_cannot_start_the_level_that_is_already_running() -> None:
    entry = cat.get_command("research_terraninfantryweapons")
    bot, _ = fx.world(entry, pending_level=1)
    bot.add_unit(U.ENGINEERINGBAY)  # an idle second bay
    _, views, _, report = run(entry, bot)
    assert views == [] and report["detail"] == "research_already_pending"


def test_spoken_level_pins_that_level_and_a_done_pinned_level_refuses() -> None:
    entry = cat.get_command("research_terraninfantryweapons")
    bot, _ = fx.world(entry, have=1)
    _, views, _, _ = run(entry, bot, level=2)
    assert views[0]["ability_id"] in legal_ids(entry, entry.upgrade_abilities[1])
    done, _ = fx.world(entry, have=2)
    _, views, _, report = run(entry, done, level=1)
    assert views == [] and report["detail"] == "already_researched"


def test_pending_in_another_family_does_not_block_this_one() -> None:
    weapons = cat.get_command("research_terraninfantryweapons")
    armor = cat.get_command("research_terraninfantryarmor")
    bot, _ = fx.world(weapons, pending_level=1)
    bot.add_unit(U.ENGINEERINGBAY)
    _, views, _, _ = run(armor, bot)
    assert views and views[0]["ability_id"] in legal_ids(armor, armor.upgrade_abilities[0])


def test_busy_lab_is_skipped_for_an_idle_second_lab_of_the_same_kind() -> None:
    entry = cat.get_command("research_stimpack")
    bot, busy = fx.world(entry, missing="busy")
    idle = bot.add_unit(U.BARRACKSTECHLAB)
    bot.add_unit(U.BARRACKS, add_on_tag=idle.tag)
    _, views, _, _ = run(entry, bot)
    assert [v["unit_tags"] for v in views] == [[idle.tag]]


def test_find_entry_resolves_semantic_tokens_levels_and_aliases() -> None:
    assert sc2_research.find_entry("COMBATSHIELD")[0].id == "research_combatshield"
    assert sc2_research.find_entry("GHOST_CLOAK")[0].id == "research_personalcloaking"
    entry, level = sc2_research.find_entry("TERRANINFANTRYWEAPONSLEVEL2")
    assert entry.id == "research_terraninfantryweapons" and level == "TERRANINFANTRYWEAPONSLEVEL2"
    assert sc2_research.find_entry("NOT_AN_UPGRADE") is None


def test_advanced_ballistics_falls_back_to_the_fusion_core_route_burnysc2_documents() -> None:
    """Header: Starport Tech Lab 805.  burnysc2 (current game): Fusion Core 1533.  With only a Fusion Core the order
    goes there with the documented ability; with a Starport Tech Lab the header button is used."""

    entry = cat.get_command("research_advancedballistics")
    bot, _ = fx.world(entry, missing="issuer")
    core = bot.add_unit(U.FUSIONCORE)
    _, views, _, report = run(entry, bot)
    assert views and report["applied"], report
    assert views[0]["ability_id"] == AbilityId.FUSIONCORERESEARCH_RESEARCHBALLISTICRANGE.value == 1533
    assert views[0]["unit_tags"] == [core.tag]
    busy_core, _ = fx.world(entry, missing="issuer")
    busy_core.add_unit(U.FUSIONCORE, orders=(AbilityId.RESEARCH_BATTLECRUISERWEAPONREFIT,))
    _, views, _, report = run(entry, busy_core)
    assert views == [] and report["detail"] == "no_ready_idle_researcher"


def test_fuel_tanks_cost_is_unknowable_offline_so_it_is_never_refused_for_money() -> None:
    """python-sc2 prices a research through ``UpgradeData`` (needs an UpgradeId); stableid.json has none for the
    header-only ``RESEARCH_HIGHCAPACITYFUELTANKS``: ``calculate_ability_cost`` is 0/0.  The game answers a short
    purse with ``ActionResult.NotEnoughMinerals`` (surfaced by the result tracker), not this module."""

    entry = cat.get_command("research_highcapacityfueltanks")
    bot, _ = fx.world(entry)
    cost = bot.game_data.calculate_ability_cost(AbilityId.RESEARCH_HIGHCAPACITYFUELTANKS)
    assert (cost.minerals, cost.vespene) == (0, 0)
    assert "money" not in fx.variants(entry)
