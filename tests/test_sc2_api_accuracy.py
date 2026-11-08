"""Offline SC2 API accuracy: ids, abilities, upgrades and raw request shapes.

Data sources (none needs StarCraft II): ``stableid.json`` from the game client,
burnysc2's ``sc2.ids``/``sc2.dicts`` and ``s2clientprotocol`` protobufs, plus the
MIT s2client-api ``sc2_typeenums.h`` extract in ``scripts/sc2_accuracy``.

Tests that need burnysc2 or stableid.json skip when they are absent (CI does not
install either); the count/target semantics tests use plain fakes and always run.
This is OFFLINE evidence of request shape and id correctness only.
"""

from __future__ import annotations

import asyncio
import types
import warnings

import pytest

from starcraft_commander.contracts import SC2ActionType, SC2CommandAction
from starcraft_commander.map_resolver import MapPoint, SC2MapResolver
from starcraft_commander.policy_modulation_provider import (
    compile_policy_modulation_provider_output,
)
from starcraft_commander.python_sc2_adapter import PythonSC2BotAdapter
from starcraft_commander.realtime_intent import tool_arguments_to_provider_output
from starcraft_commander.unified_command_router import route_policy_vector

sc2_ids = pytest.importorskip("sc2.ids.ability_id")
pytest.importorskip("s2clientprotocol")

from scripts.sc2_accuracy import audit, harness  # noqa: E402
from sc2.ids.ability_id import AbilityId  # noqa: E402
from sc2.ids.unit_typeid import UnitTypeId as U  # noqa: E402
from sc2.ids.upgrade_id import UpgradeId  # noqa: E402

needs_stableid = pytest.mark.skipif(
    not harness.stableid_available(), reason="stableid.json not present on this machine"
)

pytestmark = needs_stableid


# --------------------------------------------------------------------------- helpers


def lower_micro(ability: str, unit_classes: list[str] | None = None) -> tuple[SC2CommandAction, ...]:
    item: dict[str, object] = {"ability": ability}
    if unit_classes is not None:
        item["unit_classes"] = unit_classes
    output = tool_arguments_to_provider_output({"goal": f"micro {ability}", "micro": [item]})
    result = compile_policy_modulation_provider_output(output)
    assert result.vector is not None, result
    plan = route_policy_vector(result.vector).direct_plan
    assert plan is not None
    return tuple(plan.ordered_actions)


def lower_direct(task_type: str, production_targets: list[str] | None = None, **task) -> tuple[SC2CommandAction, ...]:
    payload = {
        "goal": f"direct {task_type}",
        "command_layer": "operation",
        "tactical_task": {"task_type": task_type, "production_targets": production_targets or [], **task},
    }
    result = compile_policy_modulation_provider_output(payload)
    assert result.vector is not None, result
    plan = route_policy_vector(result.vector).direct_plan
    assert plan is not None
    return tuple(plan.ordered_actions)


def tags(units) -> list[int]:
    return sorted(unit.tag for unit in units)


def run_voice(ability: str, *units, enemies=(), own_extra=(), unit_classes=None):
    bot = harness.make_bot(*units)
    for enemy_type, x, y in enemies:
        bot.add_unit(enemy_type, x=x, y=y, enemy=True)
    for extra_type, x, y in own_extra:
        bot.add_unit(extra_type, x=x, y=y)
    result, views, warns = harness.run_plan(bot, *lower_micro(ability, unit_classes))
    return bot, result, views, warns


ENEMY_ARMY = ((U.ZEALOT, 60.0, 60.0), (U.IMMORTAL, 62.0, 61.0))


# --------------------------------------------------------------------------- data integrity


def test_stableid_matches_documented_counts_and_sc2_ids() -> None:
    stable = audit.load_stableid()
    assert {k: len(v) for k, v in stable.items() if k in {"Units", "Abilities", "Upgrades", "Buffs"}} == {
        "Units": 2010,
        "Abilities": 4137,
        "Upgrades": 306,
        "Buffs": 307,
    }
    abilities = {a["id"] for a in stable["Abilities"]}
    assert all(member.value in abilities for member in AbilityId), "sc2.ids has ids stableid lacks"


def test_unit_tokens_have_consistent_ids_in_all_three_sources() -> None:
    report = audit.run_audit()
    bad = [
        row
        for row in report["unit_tokens"]
        if row["status"] in {"ID_MISMATCH"}
    ]
    assert bad == []
    # Every real unit-type token is Terran, except explicit transient forms / addons.
    not_terran = {row["token"] for row in report["unit_tokens"] if row["status"] == "NOT_TERRAN"}
    allowed_forms = {
        "TERRAN_SIEGETANKSIEGED", "TERRAN_WIDOWMINEBURROWED", "TERRAN_THORAP", "TERRAN_VIKINGASSAULT",
        "TERRAN_LIBERATORAG", "TERRAN_NUKE",
        "BARRACKS_TECHLAB", "BARRACKS_REACTOR", "FACTORY_TECHLAB", "FACTORY_REACTOR",
        "STARPORT_TECHLAB", "STARPORT_REACTOR",
    }
    assert not_terran <= allowed_forms, sorted(not_terran - allowed_forms)


def test_every_semantic_ability_resolves_to_an_id_its_caster_can_cast() -> None:
    report = audit.run_audit()
    problems = [
        (r["ability_token"], r["status"])
        for r in report["abilities"]
        if not r["status"].startswith("ok")
    ]
    assert problems == []
    for row in report["abilities"]:
        # Resolved id exists in stableid AND in the s2client-api header when the header knows it.
        assert row["stableid_ability"] is not None, row
        assert row["stableid_ability"]["id"] == row["ability_id"]


def test_ability_ids_are_the_exact_ids_the_game_documents() -> None:
    expected = {
        "stimpack": 3675, "marine_stimpack": 380, "marauder_stimpack": 253, "kd8_charge": 2588, "emp": 1628,
        "snipe": 2714, "ghost_cloak": 382, "ghost_decloak": 383, "tactical_nuke": 1622,
        "widow_mine_burrow": 2095, "widow_mine_unburrow": 2097, "lock_on": 2350, "siege_mode": 388,
        "unsiege": 390, "hellbat_mode": 1998, "hellion_mode": 1978, "thor_high_impact_mode": 2362,
        "thor_explosive_mode": 2364, "medivac_afterburners": 2116, "medivac_heal": 386, "medivac_load": 394,
        "medivac_unload_all": 396, "viking_fighter_mode": 405, "viking_assault_mode": 403,
        "liberator_defender_mode": 2558, "liberator_fighter_mode": 2560, "banshee_cloak": 392,
        "banshee_decloak": 393, "auto_turret": 1764, "interference_matrix": 3747, "anti_armor_missile": 3753,
        "yamato": 401, "tactical_jump": 2358,
    }
    adapter = PythonSC2BotAdapter(bot=object())
    got = {name: getattr(adapter._resolve_ability(name), "value", None) for name in expected}
    assert got == expected


def test_research_tokens_resolve_to_upgrades_with_a_research_ability_and_building() -> None:
    adapter = PythonSC2BotAdapter(bot=object())
    expected = {
        "STIMPACK": (UpgradeId.STIMPACK, U.BARRACKSTECHLAB, AbilityId.BARRACKSTECHLABRESEARCH_STIMPACK),
        "COMBATSHIELD": (UpgradeId.SHIELDWALL, U.BARRACKSTECHLAB, AbilityId.RESEARCH_COMBATSHIELD),
        "GHOST_CLOAK": (UpgradeId.PERSONALCLOAKING, U.GHOSTACADEMY, AbilityId.RESEARCH_PERSONALCLOAKING),
        "BANSHEE_CLOAK": (UpgradeId.BANSHEECLOAK, U.STARPORTTECHLAB, AbilityId.RESEARCH_BANSHEECLOAKINGFIELD),
    }
    sc2 = audit._sc2()
    for token, (upgrade, building, ability) in expected.items():
        assert adapter._resolve_upgrade(token) == upgrade, token
        assert sc2["RESEARCHED_FROM"][upgrade] == building
        assert sc2["RESEARCH_INFO"][building][upgrade]["ability"] == ability


def test_no_two_aliases_name_different_entities() -> None:
    collisions = audit.run_audit()["alias_collisions"]
    assert collisions["same_kind_collisions"] == []
    assert collisions["unit_vs_family_mismatch"] == []


# --------------------------------------------------------------------------- request shapes (>= 40 commands)


def _assert_shape(views, warns, *, ability: str | None = None) -> None:
    assert views, "no raw action reached BotAI.do"
    assert warns == [], warns
    for view in views:
        assert view["queue"] is False
        assert view["unit_tags"], view
        assert harness.target_shape_ok(view), view
        if ability is not None:
            assert view["ability_name"] == ability, view


VOICE_CASES = [
    # (ability token, own units, enemies, expected ability name, target kind)
    ("stimpack", ((U.MARINE, 3), (U.MARAUDER, 2)), (), "EFFECT_STIM", None),
    ("marine_stimpack", ((U.MARINE, 3),), (), "EFFECT_STIM_MARINE", None),
    ("marauder_stimpack", ((U.MARAUDER, 2),), (), "EFFECT_STIM_MARAUDER", None),
    ("siege_mode", ((U.SIEGETANK, 3),), (), "SIEGEMODE_SIEGEMODE", None),
    ("unsiege", ((U.SIEGETANKSIEGED, 2),), (), "UNSIEGE_UNSIEGE", None),
    ("ghost_cloak", ((U.GHOST, 2),), (), "BEHAVIOR_CLOAKON_GHOST", None),
    ("ghost_decloak", ((U.GHOST, 2),), (), "BEHAVIOR_CLOAKOFF_GHOST", None),
    ("banshee_cloak", ((U.BANSHEE, 2),), (), "BEHAVIOR_CLOAKON_BANSHEE", None),
    ("banshee_decloak", ((U.BANSHEE, 2),), (), "BEHAVIOR_CLOAKOFF_BANSHEE", None),
    ("yamato", ((U.BATTLECRUISER, 2),), ENEMY_ARMY, "YAMATO_YAMATOGUN", "target_unit_tag"),
    ("emp", ((U.GHOST, 2),), ENEMY_ARMY, "EMP_EMP", "target_world_space_pos"),
    ("snipe", ((U.GHOST, 1),), ENEMY_ARMY, "EFFECT_GHOSTSNIPE", "target_unit_tag"),
    ("tactical_nuke", ((U.GHOST, 1),), ENEMY_ARMY, "TACNUKESTRIKE_NUKECALLDOWN", "target_world_space_pos"),
    ("kd8_charge", ((U.REAPER, 2),), ENEMY_ARMY, "KD8CHARGE_KD8CHARGE", "target_unit_tag"),
    ("widow_mine_burrow", ((U.WIDOWMINE, 2),), (), "BURROWDOWN_WIDOWMINE", None),
    ("widow_mine_unburrow", ((U.WIDOWMINEBURROWED, 2),), (), "BURROWUP_WIDOWMINE", None),
    ("lock_on", ((U.CYCLONE, 2),), ENEMY_ARMY, "LOCKON_LOCKON", "target_unit_tag"),
    ("hellbat_mode", ((U.HELLION, 2),), (), "MORPH_HELLBAT", None),
    ("hellion_mode", ((U.HELLIONTANK, 2),), (), "MORPH_HELLION", None),
    ("thor_high_impact_mode", ((U.THOR, 1),), (), "MORPH_THORHIGHIMPACTMODE", None),
    ("thor_explosive_mode", ((U.THORAP, 1),), (), "MORPH_THOREXPLOSIVEMODE", None),
    ("medivac_afterburners", ((U.MEDIVAC, 2),), (), "EFFECT_MEDIVACIGNITEAFTERBURNERS", None),
    ("viking_assault_mode", ((U.VIKINGFIGHTER, 2),), (), "MORPH_VIKINGASSAULTMODE", None),
    ("viking_fighter_mode", ((U.VIKINGASSAULT, 2),), (), "MORPH_VIKINGFIGHTERMODE", None),
    ("liberator_defender_mode", ((U.LIBERATOR, 2),), ENEMY_ARMY, "MORPH_LIBERATORAGMODE", "target_world_space_pos"),
    ("liberator_fighter_mode", ((U.LIBERATORAG, 2),), (), "MORPH_LIBERATORAAMODE", None),
    ("auto_turret", ((U.RAVEN, 1),), ENEMY_ARMY, "BUILDAUTOTURRET_AUTOTURRET", "target_world_space_pos"),
    ("interference_matrix", ((U.RAVEN, 1),), ENEMY_ARMY, "EFFECT_INTERFERENCEMATRIX", "target_unit_tag"),
    ("anti_armor_missile", ((U.RAVEN, 1),), ENEMY_ARMY, "EFFECT_ANTIARMORMISSILE", "target_world_space_pos"),
]


@pytest.mark.parametrize("ability,units,enemies,expected,target_kind", VOICE_CASES, ids=[c[0] for c in VOICE_CASES])
def test_voice_ability_lowers_to_the_exact_raw_command(ability, units, enemies, expected, target_kind) -> None:
    bot, result, views, warns = run_voice(ability, *units, enemies=enemies)
    _assert_shape(views, warns)
    assert {v["ability_name"] for v in views} == {expected}, (ability, views)
    assert {v["target"] for v in views} == {target_kind}, (ability, views)
    if ability not in {"medivac_load"}:
        casters = {u.tag for u in bot.units if u.type_id in {c for c, _ in [(t, n) for t, n in units]}}
        assert {t for v in views for t in v["unit_tags"]} == casters, (ability, views)


def test_tactical_jump_needs_an_explicit_destination_and_sends_a_point() -> None:
    bot = harness.make_bot((U.BATTLECRUISER, 2))
    bare = SC2CommandAction(SC2ActionType.EXECUTE_ABILITY, "BATTLECRUISER", count=0, metadata={"ability": "tactical_jump"})
    result, views, _ = harness.run_plan(bot, bare)
    assert views == [] and "target_required" in harness.action_reports(result)[0]["detail"]
    aimed = SC2CommandAction(
        SC2ActionType.EXECUTE_ABILITY, "BATTLECRUISER", target="enemy_main", count=0, metadata={"ability": "tactical_jump"}
    )
    _, views, warns = harness.run_plan(bot, aimed)
    _assert_shape(views, warns, ability="EFFECT_TACTICALJUMP")
    assert {v["target"] for v in views} == {"target_world_space_pos"}


def test_medivac_load_heal_unload_use_the_documented_target_kinds() -> None:
    own = ((U.MARINE, 20.0, 21.0), (U.MARINE, 21.0, 21.0))
    bot, _, load, warns = run_voice("medivac_load", (U.MEDIVAC, 2), own_extra=own)
    _assert_shape(load, warns, ability="LOAD_MEDIVAC")
    assert {v["target"] for v in load} == {"target_unit_tag"}
    marines = {u.tag for u in bot.units if u.type_id == U.MARINE}
    assert {v["unit_tag"] for v in load} == marines, "each medivac must load a different marine"
    # NoCargoToUnload (error.proto): a real unload order needs passengers, so the medivacs carry cargo here.
    loaded = harness.make_bot()
    loaded.add_unit(U.MEDIVAC, cargo_taken=2)
    loaded.add_unit(U.MEDIVAC, x=21.0, cargo_taken=3)
    _, unload, warns = harness.run_plan(loaded, *lower_micro("medivac_unload_all"))
    _assert_shape(unload, warns, ability="UNLOADALLAT_MEDIVAC")
    bot = harness.make_bot((U.MEDIVAC, 1))
    hurt = bot.add_unit(U.MARINE, x=21.0, y=21.0, health=10.0, health_max=45.0)
    _, heal, warns = harness.run_plan(bot, *lower_micro("medivac_heal"))
    _assert_shape(heal, warns, ability="MEDIVACHEAL_HEAL")
    assert heal[0]["target"] == "target_unit_tag" and heal[0]["unit_tag"] == hurt.tag


def test_unit_target_ability_without_any_enemy_is_refused_not_sent_with_a_point() -> None:
    bot, result, views, warns = run_voice("yamato", (U.BATTLECRUISER, 1))
    assert views == []
    report = harness.action_reports(result)[0]
    assert report["applied"] is False and "target" in report["detail"]


def test_siege_unsiege_only_address_the_right_form() -> None:
    bot = harness.make_bot((U.SIEGETANK, 2), (U.SIEGETANKSIEGED, 2))
    unsieged = [u for u in bot.units if u.type_id == U.SIEGETANK]
    sieged = [u for u in bot.units if u.type_id == U.SIEGETANKSIEGED]
    _, siege, _ = harness.run_plan(bot, *lower_micro("siege_mode", ["TERRAN_SIEGETANK"]))
    assert {t for v in siege for t in v["unit_tags"]} == set(tags(unsieged))
    _, unsiege, _ = harness.run_plan(bot, *lower_micro("unsiege", ["TERRAN_SIEGETANK"]))
    assert {t for v in unsiege for t in v["unit_tags"]} == set(tags(sieged))
    assert {v["ability_id"] for v in unsiege} == {AbilityId.UNSIEGE_UNSIEGE.value}


def test_mixed_stim_default_covers_every_unit_class_the_user_named() -> None:
    """"스팀팩" with no unit named means every stim-capable unit class (marine AND marauder)."""

    bot = harness.make_bot((U.MARINE, 3), (U.MARAUDER, 2), (U.SIEGETANK, 1))
    _, views, warns = harness.run_plan(bot, *lower_micro("stimpack"))
    _assert_shape(views, warns, ability="EFFECT_STIM")
    expected = {u.tag for u in bot.units if u.type_id in {U.MARINE, U.MARAUDER}}
    assert {t for v in views for t in v["unit_tags"]} == expected


# --------------------------------------------------------------------------- operation / macro / emergency shapes


def _scout_like(action_type: SC2ActionType, subject: str, target: str, count: int = 0):
    return SC2CommandAction(action_type, subject, target=target, count=count)


@pytest.mark.parametrize(
    "action_type,ability_name",
    [
        (SC2ActionType.MOVE_GROUP, "MOVE_MOVE"),
        (SC2ActionType.ATTACK_MOVE, "ATTACK"),
        (SC2ActionType.SMART, "SMART"),
        (SC2ActionType.PATROL, "PATROL"),
    ],
)
@pytest.mark.parametrize("target", ["enemy_natural", "enemy_main", "self_main", "self_natural"])
def test_group_orders_carry_a_world_point_and_all_requested_tags(action_type, ability_name, target) -> None:
    bot = harness.make_bot((U.MARINE, 4))
    result, views, warns = harness.run_plan(bot, _scout_like(action_type, "MARINE", target))
    _assert_shape(views, warns)
    assert {v["target"] for v in views} == {"target_world_space_pos"}
    assert {t for v in views for t in v["unit_tags"]} == {u.tag for u in bot.units}
    assert {v["ability_name"] for v in views} <= {ability_name, "ATTACK_ATTACK", "ATTACK", "MOVE", "MOVE_MOVE", "SMART"}


def test_emergency_stop_hold_retreat_shapes() -> None:
    bot = harness.make_bot((U.MARINE, 3), (U.SIEGETANK, 2))
    all_tags = {u.tag for u in bot.units}
    for kind, expected, target in (
        (SC2ActionType.STOP_GROUP, "STOP", None),
        (SC2ActionType.HOLD_POSITION, "HOLDPOSITION", None),
    ):
        action = SC2CommandAction(kind, "available combat units", count=0, metadata={"emergency": True})
        _, views, warns = harness.run_plan(bot, action)
        _assert_shape(views, warns)
        assert {v["target"] for v in views} == {target}
        assert {v["ability_name"] for v in views} == {expected}
        assert {t for v in views for t in v["unit_tags"]} == all_tags
    retreat = SC2CommandAction(
        SC2ActionType.MOVE_GROUP, "available combat units", target="self_main", count=0,
        metadata={"emergency": True, "retreat": True},
    )
    _, views, warns = harness.run_plan(bot, retreat)
    _assert_shape(views, warns)
    assert {v["ability_name"] for v in views} == {"MOVE_MOVE"}
    assert {v["target"] for v in views} == {"target_world_space_pos"}
    assert {t for v in views for t in v["unit_tags"]} == all_tags


@pytest.mark.parametrize(
    "structure,ability_name,kind",
    [
        ("SUPPLYDEPOT", "TERRANBUILD_SUPPLYDEPOT", "target_world_space_pos"),
        ("BARRACKS", "TERRANBUILD_BARRACKS", "target_world_space_pos"),
        ("COMMANDCENTER", "TERRANBUILD_COMMANDCENTER", "target_world_space_pos"),
        ("FACTORY", "TERRANBUILD_FACTORY", "target_world_space_pos"),
        ("BUNKER", "TERRANBUILD_BUNKER", "target_world_space_pos"),
        ("ENGINEERINGBAY", "TERRANBUILD_ENGINEERINGBAY", "target_world_space_pos"),
    ],
)
def test_build_structure_issues_a_point_targeted_terranbuild_on_an_scv(structure, ability_name, kind) -> None:
    bot = harness.add_prerequisites(harness.make_bot((U.SCV, 3)), structure)
    if structure == "ENGINEERINGBAY":  # UnitTypeData.tech_requirement: Command Center (starcraft_commander.sc2_production)
        bot.add_unit(U.COMMANDCENTER)
    target = "self_natural" if structure == "COMMANDCENTER" else "self_main"
    action = SC2CommandAction(SC2ActionType.BUILD_STRUCTURE, structure, target=target, metadata={"building_type": structure})
    result, views, warns = harness.run_plan(bot, action)
    _assert_shape(views, warns, ability=ability_name)
    assert {v["target"] for v in views} == {kind}
    assert len(views) == 1 and len(views[0]["unit_tags"]) == 1
    assert views[0]["unit_tags"][0] in {u.tag for u in bot.workers}


@pytest.mark.parametrize(
    "subject,parent,ability_name",
    [
        ("BARRACKS_TECHLAB", U.BARRACKS, "BUILD_TECHLAB_BARRACKS"),
        ("BARRACKS_REACTOR", U.BARRACKS, "BUILD_REACTOR_BARRACKS"),
        ("FACTORY_TECHLAB", U.FACTORY, "BUILD_TECHLAB_FACTORY"),
        ("FACTORY_REACTOR", U.FACTORY, "BUILD_REACTOR_FACTORY"),
        ("STARPORT_TECHLAB", U.STARPORT, "BUILD_TECHLAB_STARPORT"),
        ("STARPORT_REACTOR", U.STARPORT, "BUILD_REACTOR_STARPORT"),
        ("ORBITALCOMMAND", U.COMMANDCENTER, "UPGRADETOORBITAL_ORBITALCOMMAND"),
        ("PLANETARYFORTRESS", U.COMMANDCENTER, "UPGRADETOPLANETARYFORTRESS_PLANETARYFORTRESS"),
    ],
)
def test_addons_and_townhall_morphs_are_no_target_orders_on_the_idle_parent_not_an_scv(subject, parent, ability_name) -> None:
    bot = harness.add_prerequisites(harness.make_bot((U.SCV, 2), (parent, 1)), subject)
    scv_tags = {u.tag for u in bot.workers}
    action = SC2CommandAction(
        SC2ActionType.BUILD_STRUCTURE, subject, target="self_main", metadata={"building_type": subject}
    )
    result, views, warns = harness.run_plan(bot, action)
    _assert_shape(views, warns, ability=ability_name)
    assert views[0]["target"] is None
    parent_tag = next(s.tag for s in bot.structures if s.type_id == parent)
    assert views[0]["unit_tags"] == [parent_tag] and not (set(views[0]["unit_tags"]) & scv_tags)


def test_refinery_targets_a_geyser_unit_not_a_point() -> None:
    bot = harness.make_bot((U.SCV, 2))
    geyser = bot.add_unit(U.VESPENEGEYSER, x=24.0, y=16.0, enemy=False)
    bot.vespene_geyser.append(geyser)
    action = SC2CommandAction(
        SC2ActionType.BUILD_STRUCTURE, "REFINERY", target="self_geyser", metadata={"building_type": "REFINERY"}
    )
    _, views, warns = harness.run_plan(bot, action)
    _assert_shape(views, warns, ability="TERRANBUILD_REFINERY")
    assert views[0]["target"] == "target_unit_tag" and views[0]["unit_tag"] == geyser.tag


@pytest.mark.parametrize(
    "unit,producer_units,ability_name",
    [
        ("SCV", (U.COMMANDCENTER,), "COMMANDCENTERTRAIN_SCV"),
        ("SCV", (U.ORBITALCOMMAND,), "COMMANDCENTERTRAIN_SCV"),
        ("SCV", (U.PLANETARYFORTRESS,), "COMMANDCENTERTRAIN_SCV"),
        ("MARINE", (U.BARRACKS,), "BARRACKSTRAIN_MARINE"),
        ("MARAUDER", (U.BARRACKS,), "BARRACKSTRAIN_MARAUDER"),
        ("SIEGETANK", (U.FACTORY,), "FACTORYTRAIN_SIEGETANK"),
        ("MEDIVAC", (U.STARPORT,), "STARPORTTRAIN_MEDIVAC"),
    ],
)
def test_train_unit_uses_the_creation_ability_on_every_structure_that_can_train_it(unit, producer_units, ability_name) -> None:
    bot = harness.make_bot()
    lab = {U.BARRACKS: U.BARRACKSTECHLAB, U.FACTORY: U.FACTORYTECHLAB}.get(producer_units[0])
    if unit in {"MARAUDER", "SIEGETANK"}:  # UnitTypeData.require_attached: these need a Tech Lab on the producer
        lab_unit = bot.add_unit(lab)
        bot.add_unit(producer_units[0], add_on_tag=lab_unit.tag)
        bot.structures.sort(key=lambda s: s.type_id == lab)  # the producer stays structures[0]
    else:
        for p in producer_units:
            bot.add_unit(p)
    actions = lower_direct("train_unit", [f"TERRAN_{unit}"])
    _, views, warns = harness.run_plan(bot, *actions)
    _assert_shape(views, warns, ability=ability_name)
    assert {v["target"] for v in views} == {None}
    assert views[0]["unit_tags"] == [producer_units and bot.structures[0].tag]


TRAINABLE = [
    "SCV", "MARINE", "MARAUDER", "REAPER", "GHOST", "HELLION", "SIEGETANK", "CYCLONE", "THOR", "WIDOWMINE",
    "MEDIVAC", "VIKINGFIGHTER", "LIBERATOR", "BANSHEE", "RAVEN", "BATTLECRUISER",
]


def _needs_lab(unit: str) -> bool:
    from sc2.dicts.unit_train_build_abilities import TRAIN_INFO

    return any(bool(t[getattr(U, unit)].get("requires_techlab")) for t in TRAIN_INFO.values() if getattr(U, unit) in t)


@pytest.mark.parametrize("unit", TRAINABLE)
def test_every_trainable_dsl_unit_gets_the_producer_python_sc2_says_trains_it(unit) -> None:
    """The router's producer metadata must agree with python-sc2 UNIT_TRAINED_FROM for every unit it can lower."""

    sc2 = audit._sc2()
    producers = sc2["TRAINED_FROM"][getattr(U, unit)]
    (action,) = lower_direct("train_unit", [f"TERRAN_{unit}"])
    assert action.metadata.get("producer") in {p.name for p in producers}, (unit, action.metadata)
    producer = next(iter(producers))
    bot = harness.add_prerequisites(harness.make_bot(), unit)
    lab = {U.BARRACKS: U.BARRACKSTECHLAB, U.FACTORY: U.FACTORYTECHLAB, U.STARPORT: U.STARPORTTECHLAB}.get(producer)
    if lab is not None and harness.build_game_data().units[getattr(U, unit).value].creation_ability is not None and _needs_lab(unit):
        # UnitTypeData.require_attached: tech-lab units are only trainable from a producer that carries one.
        bot.add_unit(producer, add_on_tag=bot.add_unit(lab).tag)
    else:
        bot.add_unit(producer)
    _, views, warns = harness.run_plan(bot, action)
    _assert_shape(views, warns)
    assert AbilityId(views[0]["ability_id"]) == bot.game_data.units[getattr(U, unit).value].creation_ability.exact_id


def test_research_is_a_no_target_command_on_the_techlab_not_the_barracks() -> None:
    for token, ability_name in (("STIMPACK", "BARRACKSTECHLABRESEARCH_STIMPACK"), ("COMBATSHIELD", "RESEARCH_COMBATSHIELD")):
        bot = harness.make_bot((U.BARRACKS, 1), (U.BARRACKSTECHLAB, 1))
        techlab = next(s for s in bot.structures if s.type_id == U.BARRACKSTECHLAB)
        for researcher in ("TERRAN_BARRACKS", "BARRACKS_TECHLAB", None):
            targets = [token, researcher] if researcher else [token]
            actions = lower_direct("research_upgrade", targets)
            _, views, warns = harness.run_plan(bot, *actions)
            _assert_shape(views, warns, ability=ability_name)
            assert views[0]["target"] is None
            assert views[0]["unit_tags"] == [techlab.tag], (token, researcher, views)


def test_repair_is_a_unit_targeted_scv_order() -> None:
    bot = harness.make_bot((U.SCV, 2))
    bunker = bot.add_unit(U.BUNKER, x=30.0, y=30.0, health=40.0, health_max=400.0)
    action = SC2CommandAction(SC2ActionType.REPAIR, "SCV", target="bunker", count=1)
    _, views, warns = harness.run_plan(bot, action)
    _assert_shape(views, warns, ability="EFFECT_REPAIR")
    assert views[0]["target"] == "target_unit_tag" and views[0]["unit_tag"] == bunker.tag


# --------------------------------------------------------------------------- count semantics (no sc2 data needed)


def _fake_bot(unit_names: list[str]):
    class _Unit:
        def __init__(self, name: str, tag: int) -> None:
            self.name = name
            self.tag = tag
            self.position = MapPoint(10.0, 10.0)
            self.calls: list[tuple] = []

        def use_ability(self, ability, target=None):
            self.calls.append((ability, target))
            return ("cmd", self.tag)

    units = [_Unit(name, 100 + index) for index, name in enumerate(unit_names)]
    bot = types.SimpleNamespace(
        units=units,
        structures=[],
        workers=[],
        enemy_units=[],
        ability_id_resolver=lambda name: f"ABILITY:{name}",
        do=lambda command: None,
    )
    return bot, units


def _adapter(bot):
    return PythonSC2BotAdapter(
        bot=bot,
        map_resolver=SC2MapResolver(positions={"self_main": MapPoint(10.0, 10.0)}),
        unit_type_resolver=lambda name: name,
    )


def test_bare_ability_means_every_eligible_caster_and_explicit_number_still_counts() -> None:
    bot, units = _fake_bot(["SiegeTank"] * 4)
    adapter = _adapter(bot)
    bare = SC2CommandAction(SC2ActionType.EXECUTE_ABILITY, "SIEGETANK", count=0, metadata={"ability": "siege_mode"})
    bound = adapter.bind_direct_action(bare)
    assert len(bound.metadata["_direct_unit_tags"]) == 4
    report = asyncio.run(adapter.execute_ability(bound))
    assert report.applied is True and report.issued_count == 4
    two = SC2CommandAction(SC2ActionType.EXECUTE_ABILITY, "SIEGETANK", count=2, metadata={"ability": "siege_mode"})
    report = asyncio.run(adapter.execute_ability(adapter.bind_direct_action(two)))
    assert report.issued_count == 2


def test_router_defaults_ability_count_to_all_and_keeps_explicit_counts() -> None:
    (bare,) = lower_micro("siege_mode", ["TERRAN_SIEGETANK"])
    assert bare.count == 0
    result = compile_policy_modulation_provider_output(
        {
            "goal": "two tanks siege",
            "command_layer": "micro",
            "tactical_task": {
                "task_type": "execute_ability",
                "ability": "siege_mode",
                "unit_classes": ["TERRAN_SIEGETANK"],
                "min_units": 2,
            },
        }
    )
    plan = route_policy_vector(result.vector).direct_plan
    assert [a.count for a in plan.ordered_actions] == [2]


def test_router_emits_one_ability_action_per_named_unit_class() -> None:
    actions = lower_micro("stimpack")
    assert {a.subject for a in actions} == {"MARINE", "MARAUDER"}
    assert {a.count for a in actions} == {0}
    assert {a.metadata["ability"] for a in actions} == {"stimpack"}
