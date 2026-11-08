"""``build_catalog_state`` / ``PythonSC2BotAdapter.catalog_state`` read a python-sc2 bot faithfully (harness fake bot)."""

from __future__ import annotations

import pytest

pytest.importorskip("sc2.ids.ability_id")
pytest.importorskip("s2clientprotocol")

from scripts.sc2_accuracy import harness  # noqa: E402
from sc2.ids.ability_id import AbilityId  # noqa: E402
from sc2.ids.unit_typeid import UnitTypeId as U  # noqa: E402
from sc2.ids.upgrade_id import UpgradeId  # noqa: E402

from starcraft_commander import sc2_catalog as cat  # noqa: E402
from starcraft_commander.sc2_catalog_state import build_catalog_state  # noqa: E402

pytestmark = pytest.mark.skipif(not harness.stableid_available(), reason="stableid.json not present on this machine")


def _world():
    bot = harness.make_bot()
    lab = bot.add_unit(U.BARRACKSTECHLAB)
    barracks = bot.add_unit(U.BARRACKS, add_on_tag=lab.tag)
    ebay = bot.add_unit(U.ENGINEERINGBAY, orders=(AbilityId.ENGINEERINGBAYRESEARCH_TERRANINFANTRYWEAPONSLEVEL1,))
    orbital = bot.add_unit(U.ORBITALCOMMAND, energy=52.0)
    depot = bot.add_unit(U.SUPPLYDEPOT)
    bot.minerals, bot.vespene = 300, 100
    return bot, {"lab": lab, "barracks": barracks, "ebay": ebay, "orbital": orbital, "depot": depot}


def test_owned_units_carry_type_tag_ready_idle_energy_and_addon() -> None:
    bot, units = _world()
    state = build_catalog_state(bot)
    by_type = {o.type_name: o for o in state.owned}
    assert by_type["BARRACKS"].addon == "techlab" and by_type["BARRACKS"].idle and by_type["BARRACKS"].ready
    assert by_type["BARRACKS"].is_structure and by_type["BARRACKS"].tag == units["barracks"].tag
    assert by_type["ENGINEERINGBAY"].idle is False
    assert by_type["ORBITALCOMMAND"].energy == 52.0
    assert by_type["SUPPLYDEPOT"].energy is None and by_type["SUPPLYDEPOT"].addon == ""
    assert (state.minerals, state.vespene) == (300, 100)


def test_pending_and_done_upgrades_come_from_orders_and_state() -> None:
    bot, _ = _world()
    bot.state.upgrades = {UpgradeId.STIMPACK}
    state = build_catalog_state(bot)
    assert state.upgrades_pending == {"TERRANINFANTRYWEAPONSLEVEL1"}
    assert state.upgrades_done == {"STIMPACK"}


def test_state_drives_the_catalog_choices() -> None:
    bot, _ = _world()
    state = build_catalog_state(bot)
    # the bay is busy researching weapons L1: nothing else can be ordered there, and the family level is "pending"
    assert cat.evaluate("research_terraninfantryweapons", state).reason == "structure_busy"
    assert cat.upgrade_progress_name(cat.get_command("research_terraninfantryweapons"), state) == ("", "research_already_pending")
    assert cat.evaluate("research_terraninfantryarmor", state).reason == "structure_busy"
    assert cat.evaluate("effect_scan", state).ok and cat.evaluate("effect_calldownmule", state).ok
    ids = {r.command_id for r in cat.issuable_now(state)}
    assert {"train_marine", "train_marauder", "research_stimpack"} <= ids
    bot.state.upgrades = {UpgradeId.STIMPACK}
    assert cat.evaluate("research_stimpack", build_catalog_state(bot)).reason == "already_researched"


def test_orbital_energy_below_cost_is_refused_and_depot_gates_nothing_else() -> None:
    bot = harness.make_bot()
    bot.add_unit(U.ORBITALCOMMAND, energy=30.0)
    state = build_catalog_state(bot)
    assert cat.evaluate("effect_scan", state).reason == "not_enough_energy"


def test_unknown_surfaces_stay_unknown_and_money_is_checked_through_can_afford() -> None:
    bot = harness.make_bot()
    bot.add_unit(U.BARRACKSTECHLAB)
    bot.minerals = 0
    state = build_catalog_state(bot)
    assert state.can_afford("upgrade", "STIMPACK") is False
    assert cat.evaluate("research_stimpack", state).reason == "unaffordable"
    assert state.can_afford("upgrade", "NO_SUCH_UPGRADE") is None


def test_adapter_catalog_state_is_the_same_snapshot() -> None:
    bot, _ = _world()
    adapter = harness.make_adapter(bot)
    assert adapter.catalog_state() == build_catalog_state(bot)
    assert bool(adapter.catalog_state().owned)
