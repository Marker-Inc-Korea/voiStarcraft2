"""Every catalog command through the REAL Direct path: provider payload -> policy compiler -> route_policy_vector ->
SC2RuntimeExecutor -> PythonSC2BotAdapter -> python-sc2 ``BotAI.do`` -> raw ``ActionRawUnitCommand`` protobuf.

The parameters are GENERATED FROM THE CATALOG (which is generated from the s2client-api header + burnysc2): the world
(issuer, prerequisites, target) is derived from each entry's data, and the expected ability id is derived from the
header id / ``GENERIC_REDIRECT_ABILITIES`` / stableid remaps, never from a hard-coded copy.  Offline request-shape
evidence only: nothing here proves a live game accepts the order.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sc2.ids.ability_id")
pytest.importorskip("s2clientprotocol")

from scripts.sc2_accuracy import audit, fixtures_effects, harness  # noqa: E402
from scripts.sc2_accuracy.lowering import _plan_actions  # noqa: E402
from sc2.dicts.generic_redirect_abilities import GENERIC_REDIRECT_ABILITIES  # noqa: E402
from sc2.ids.ability_id import AbilityId  # noqa: E402
from sc2.ids.unit_typeid import UnitTypeId as U  # noqa: E402
from sc2.ids.upgrade_id import UpgradeId  # noqa: E402

from starcraft_commander import sc2_catalog as cat  # noqa: E402
from starcraft_commander.narrator import narrate_sc2_plan_result  # noqa: E402
from starcraft_commander.policy_modulation_provider import compile_policy_modulation_provider_output  # noqa: E402
from starcraft_commander.unified_command_router import route_policy_vector  # noqa: E402

pytestmark = pytest.mark.skipif(not harness.stableid_available(), reason="stableid.json not present on this machine")

ENEMIES = ((U.ZEALOT, 60.0, 60.0), (U.IMMORTAL, 62.0, 61.0))
ADDON_LAB = {"BARRACKS": "BARRACKSTECHLAB", "FACTORY": "FACTORYTECHLAB", "STARPORT": "STARPORTTECHLAB"}
STABLE = {a["id"]: a for a in (audit.load_stableid() or {"Abilities": []})["Abilities"]}


def header_ids_for(entry) -> set[int]:
    """Every id the game may legally carry for this header command: itself, or any id that remaps to it."""

    ids = {entry.header_id}
    for member, target in GENERIC_REDIRECT_ABILITIES.items():
        if target.value == entry.header_id:
            ids.add(member.value)
    ids.update(i for i, a in STABLE.items() if a.get("remapid") == entry.header_id)
    return ids


# --------------------------------------------------------------------------- world builder (all data-derived)


def requirement_chain(bot, structure_name: str) -> None:
    """Finished chain for ``structure_name`` from the catalog's own SCV-build data (UnitTypeData.tech_requirement)."""

    needed = {}
    for entry in cat.commands_by_category("build"):
        built = entry.produces.get("SCV")
        if built:
            needed[built[0]] = built[2]
    for entry in cat.commands_by_category("morph"):
        for unit, _, required in entry.produces.values():
            needed[unit] = required
    cursor = structure_name
    while cursor and not any(u.type_id.name == cursor for u in bot.structures):
        bot.add_unit(getattr(U, cursor))
        cursor = needed.get(cursor, "")


def add_issuer(bot, issuer: str, *, count: int = 2, **kw):
    return [bot.add_unit(getattr(U, issuer), x=20.0 + i, y=20.0, **kw) for i in range(count)]


def world(entry, issuer: str, missing: str = ""):
    """``(bot, payload kwargs)``: the fake bot has the issuer, prerequisites and target unless ``missing`` removes one."""

    bot = harness.make_bot()
    kwargs: dict = {}
    category = entry.category
    if category == "train":
        unit, needs_lab, required = entry.produces[issuer]
        if required and missing != "prerequisite":
            requirement_chain(bot, required)
        if missing != "issuer":
            if needs_lab and missing != "techlab":
                lab = bot.add_unit(getattr(U, ADDON_LAB[issuer]))
                bot.add_unit(getattr(U, issuer), add_on_tag=lab.tag)
            else:
                bot.add_unit(getattr(U, issuer))
        return bot, kwargs
    if category == "build" and "SCV" in entry.issuers:
        _, _, required = entry.produces["SCV"]
        if required and missing != "prerequisite":
            requirement_chain(bot, required)
        if missing != "issuer":
            add_issuer(bot, "SCV")
        if entry.target_mode == "geyser":
            kwargs["location"] = "self_geyser"
            if missing != "target":
                geyser = bot.add_unit(U.VESPENEGEYSER, x=24.0, y=16.0)
                bot.vespene_geyser.append(geyser)
        else:
            kwargs["location"] = "natural" if entry.subject == "COMMANDCENTER" else "home"
        return bot, kwargs
    if category == "research":
        if missing != "issuer":
            add_issuer(bot, issuer, count=1)
        if missing == "done":
            bot.state.upgrades = {UpgradeId[entry.upgrades[-1]]}
            if len(entry.upgrades) > 1:
                bot.state.upgrades = {UpgradeId[n] for n in entry.upgrades}
        return bot, kwargs
    # addon / morph-of-structure / ability style
    lowering = entry.lowering
    if lowering == "build_structure":  # add-ons, orbital, planetary: no-target order on the idle parent
        unit, _, required = next(iter(entry.produces.values()), ("", False, ""))
        if required and missing != "prerequisite":
            requirement_chain(bot, required)
        if missing != "issuer":
            bot.add_unit(getattr(U, issuer))
        if missing == "addon":
            bot.add_unit(getattr(U, ADDON_LAB[issuer] if issuer in ADDON_LAB else "BARRACKSTECHLAB"))
        kwargs["location"] = "home"
        return bot, kwargs
    energy = 0.0 if missing == "energy" else None
    if missing != "issuer":
        extra = {"energy": energy} if energy is not None else {}
        if issuer in {"GHOST", "BANSHEE", "RAVEN", "MEDIVAC", "ORBITALCOMMAND"} or entry.energy_cost is not None:
            add_issuer(bot, issuer, **extra)
        else:
            add_issuer(bot, issuer)
    mode = entry.target_mode
    if lowering == "repair":
        if missing != "issuer":
            add_issuer(bot, "SCV")
        if missing != "target":
            bot.add_unit(U.BUNKER, x=30.0, y=30.0, health=40.0, health_max=400.0)
        return bot, kwargs
    if mode in {"enemy_unit", "enemy_point"} and missing != "target":
        for kind, x, y in ENEMIES:
            bot.add_unit(kind, x=x, y=y, enemy=True)
    if mode == "mineral_patch":
        bot.add_unit(U.MINERALFIELD, x=19.0, y=22.0)
        bot.add_unit(U.MINERALFIELD, x=19.0, y=23.0)
    if mode == "supply_depot" and missing != "target":
        bot.add_unit(U.SUPPLYDEPOT, x=24.0, y=24.0)
    if mode == "own_cargo" and missing != "target":
        bot.add_unit(U.MARINE, x=21.0, y=21.0)
        bot.add_unit(U.MARINE, x=21.5, y=21.0)
    if mode == "own_damaged_bio" and missing != "target":
        bot.add_unit(U.MARINE, x=21.0, y=21.0, health=10.0, health_max=45.0)
    if mode == "explicit_point" and missing != "target":
        kwargs["location"] = "enemy_main"
    fixtures_effects.complete(bot, entry, issuer, missing)  # cargo / mineral preconditions of effect, behavior, other rows
    return bot, kwargs


def lower(entry, issuer, kwargs):
    payload = cat.direct_task_payload(entry.id, issuer=issuer, **kwargs)
    result = compile_policy_modulation_provider_output(payload)
    assert result.vector is not None, (entry.id, payload, result)
    plan = route_policy_vector(result.vector).direct_plan
    assert plan is not None, (entry.id, payload)
    return tuple(plan.ordered_actions)


def served(entry):
    return [(entry, issuer) for issuer in entry.valid_issuers]


PAIRS = [pair for entry in cat.all_commands() for pair in served(entry)]
IDS = [f"{e.id}/{i}" for e, i in PAIRS]


def research_issuer_world(entry, issuer):
    return world(entry, issuer)


# --------------------------------------------------------------------------- positive: every entry sends


@pytest.mark.parametrize("entry,issuer", PAIRS, ids=IDS)
def test_catalog_entry_sends_the_api_correct_request(entry, issuer) -> None:
    bot, kwargs = world(entry, issuer)
    actions = lower(entry, issuer, kwargs)
    result, views, warns = harness.run_plan(bot, *actions)
    report = harness.action_reports(result)[0]
    assert views, (entry.id, issuer, report)
    assert warns == [], warns
    legal = header_ids_for(entry)
    sent = {v["ability_id"] for v in views}
    assert sent <= legal, (entry.id, [AbilityId(i).name for i in sent], [AbilityId(i).name for i in legal if i in AbilityId._value2member_map_])
    assert all(harness.target_shape_ok(v) for v in views), views
    wanted_target = {
        "none": {None},
        "point": {"target_world_space_pos"},
        "unit": {"target_unit_tag"},
        "point_or_unit": {"target_world_space_pos", "target_unit_tag"},
    }[entry.target_kind]
    assert {v["target"] for v in views} <= wanted_target, (entry.id, views)
    issuer_tags = {u.tag for u in list(bot.units) + list(bot.structures) if u.type_id.name == issuer}
    if entry.category == "build" and "SCV" in entry.issuers:
        issuer_tags = {u.tag for u in bot.workers}
    if entry.category == "research":
        assert {t for v in views for t in v["unit_tags"]} <= issuer_tags
    elif entry.id != "effect_repair":
        assert {t for v in views for t in v["unit_tags"]} <= issuer_tags, (entry.id, views)
    assert all(v["unit_tags"] for v in views)
    assert all(v["queue"] is False for v in views)


# --------------------------------------------------------------------------- research: level chosen from state


def test_research_levels_climb_with_state_and_send_the_exact_level_ability() -> None:
    families = [e for e in cat.commands_by_category("research") if len(e.upgrades) == 3]
    assert len(families) == 5, [e.id for e in families]
    for entry in families:
        issuer = entry.issuers[0]
        for index, level in enumerate(entry.upgrades):
            bot = harness.make_bot()
            bot.add_unit(getattr(U, issuer))
            if index:
                building = entry.upgrade_requirements[index][0]
                requirement_chain(bot, building)
            bot.state.upgrades = {UpgradeId[name] for name in entry.upgrades[:index]}
            result, views, warns = harness.run_plan(bot, *lower(entry, issuer, {}))
            assert warns == [] and len(views) == 1, (entry.id, level, harness.action_reports(result))
            assert AbilityId(views[0]["ability_id"]).name == entry.upgrade_abilities[index], (entry.id, level)
            assert views[0]["target"] is None


def test_research_refuses_a_level_whose_prerequisite_is_missing_or_done_or_pending() -> None:
    entry = cat.get_command("research_terraninfantryweapons")
    bot = harness.make_bot()
    bot.add_unit(U.ENGINEERINGBAY)
    bot.state.upgrades = {UpgradeId.TERRANINFANTRYWEAPONSLEVEL1}
    result, views, _ = harness.run_plan(bot, *lower(entry, "ENGINEERINGBAY", {}))
    assert views == []
    assert harness.action_reports(result)[0]["detail"] == "missing_prerequisite: ARMORY"
    assert "무기고" in narrate_sc2_plan_result(result).response_text
    done = harness.make_bot()
    done.add_unit(U.ENGINEERINGBAY)
    done.state.upgrades = {UpgradeId[n] for n in entry.upgrades}
    result, views, _ = harness.run_plan(done, *lower(entry, "ENGINEERINGBAY", {}))
    assert views == [] and harness.action_reports(result)[0]["detail"] == "no_next_level"
    pending = harness.make_bot()
    pending.add_unit(U.ENGINEERINGBAY, orders=(AbilityId.ENGINEERINGBAYRESEARCH_TERRANINFANTRYWEAPONSLEVEL1,))
    result, views, _ = harness.run_plan(pending, *lower(entry, "ENGINEERINGBAY", {}))
    assert views == [] and harness.action_reports(result)[0]["detail"] == "research_already_pending"
    assert "이미 연구 중" in narrate_sc2_plan_result(result).response_text


# --------------------------------------------------------------------------- refusals: nothing is sent


def refusal_variants(entry, issuer) -> list[str]:
    variants = ["issuer"]
    category = entry.category
    if category == "train":
        _, needs_lab, required = entry.produces[issuer]
        if needs_lab:
            variants.append("techlab")
        if required:
            variants.append("prerequisite")
    elif category == "build" and "SCV" in entry.issuers:
        if entry.produces["SCV"][2]:
            variants.append("prerequisite")
        if entry.target_mode == "geyser":
            variants.append("target")
    elif category == "research":
        variants.append("done")
    elif entry.lowering == "build_structure":
        if entry.produces and next(iter(entry.produces.values()))[2]:
            variants.append("prerequisite")
    else:
        if entry.energy_cost is not None:
            variants.append("energy")
        if entry.target_mode in {"enemy_unit", "enemy_point", "mineral_patch", "supply_depot", "own_cargo", "own_damaged_bio", "explicit_point"} or entry.lowering == "repair":
            variants.append("target")
    return variants


REFUSALS = [(entry, issuer, variant) for entry, issuer in PAIRS for variant in refusal_variants(entry, issuer)]


@pytest.mark.parametrize("entry,issuer,variant", REFUSALS, ids=[f"{e.id}/{i}/{v}" for e, i, v in REFUSALS])
def test_catalog_entry_with_a_missing_requirement_is_refused_visibly_and_nothing_is_sent(entry, issuer, variant) -> None:
    bot, kwargs = world(entry, issuer, missing=variant)
    actions = lower(entry, issuer, kwargs)
    result, views, warns = harness.run_plan(bot, *actions)
    assert views == [], (entry.id, variant, views)
    report = harness.action_reports(result)[0]
    assert report["applied"] is False and report["detail"], (entry.id, variant, report)
    text = narrate_sc2_plan_result(result).response_text
    assert any("\uac00" <= ch <= "\ud7a3" for ch in text), text
    assert "세부:" not in text, "a raw adapter detail leaked to the user: " + text


# --------------------------------------------------------------------------- coverage guards


def test_every_catalog_entry_is_exercised_by_the_parametrised_tests() -> None:
    assert {e.id for e, _ in PAIRS} == {e.id for e in cat.all_commands()}
    assert len({e.id for e in cat.all_commands()}) == 103


def test_refinery_without_a_geyser_is_refused_and_with_one_it_sends() -> None:
    entry = cat.get_command("build_refinery")
    bot, kwargs = world(entry, "SCV", missing="target")
    result, views, _ = harness.run_plan(bot, *lower(entry, "SCV", kwargs))
    assert views == [] and "no_free_geyser" in harness.action_reports(result)[0]["detail"]
    bot, kwargs = world(entry, "SCV")
    _, views, _ = harness.run_plan(bot, *lower(entry, "SCV", kwargs))
    assert views and views[0]["target"] == "target_unit_tag"


def test_turret_and_sensor_tower_compile_through_the_direct_ticket() -> None:
    for command_id in ("build_missileturret", "build_sensortower"):
        entry = cat.get_command(command_id)
        bot, kwargs = world(entry, "SCV")
        _, views, warns = harness.run_plan(bot, *lower(entry, "SCV", kwargs))
        assert views and warns == [], command_id


def test_catalog_state_is_built_from_the_live_bot_and_agrees_with_the_executor() -> None:
    bot = harness.make_bot()
    lab = bot.add_unit(U.BARRACKSTECHLAB)
    bot.add_unit(U.BARRACKS, add_on_tag=lab.tag)
    bot.add_unit(U.ENGINEERINGBAY)
    bot.add_unit(U.ORBITALCOMMAND, energy=30.0)
    adapter = harness.make_adapter(bot)
    state = adapter.catalog_state()
    ids = {r.command_id for r in cat.issuable_now(state)}
    assert {"research_stimpack", "research_terraninfantryweapons", "train_marauder", "train_marine"} <= ids
    assert "effect_scan" not in ids and cat.evaluate("effect_scan", state).reason == "not_enough_energy"
    assert "build_techlab" not in ids and cat.evaluate("build_techlab", state).reason in {"addon_already_attached", "no_ready_idle_producer", "wrong_issuer: BARRACKS"}
