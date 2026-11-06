"""Round 4 vocabulary contracts (handoff items 1, 2, 3, 4 of docs/sc2-api-doc-verification.md section 6).

Every test drives the product's REAL path: realtime ``submit_command`` arguments (what the model emits for the
spoken utterance) -> ``tool_arguments_to_provider_output`` -> policy compiler -> ``route_policy_vector`` ->
``SC2RuntimeExecutor`` -> ``PythonSC2BotAdapter`` -> real python-sc2 ``BotAI.do`` -> ``ActionRawUnitCommand`` ->
serialised ``RequestAction``.  Request-shape evidence only; nothing here proves a live game accepted an order.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("sc2.ids.ability_id")
pytest.importorskip("s2clientprotocol")

from scripts.sc2_accuracy import harness  # noqa: E402
from scripts.sc2_accuracy.lowering import lower_tool_args, plan_for_tool_args  # noqa: E402
from sc2.ids.ability_id import AbilityId  # noqa: E402
from sc2.ids.unit_typeid import UnitTypeId as U  # noqa: E402

from starcraft_commander.contracts import SC2ActionType, SC2CommandAction, SC2ExecutionPlan  # noqa: E402

pytestmark = pytest.mark.skipif(not harness.stableid_available(), reason="stableid.json not present on this machine")

ENEMY = ((U.ZEALOT, 60.0, 60.0), (U.IMMORTAL, 62.0, 61.0))


def run(bot, *actions):
    result, views, warns = harness.run_plan(bot, *actions)
    assert warns == [], warns
    return result, views


def reports(result):
    return harness.action_reports(result)


def tags_of(views):
    return {t for v in views for t in v["unit_tags"]}


def pairs(views):
    return {(v["ability_name"], v["queue"]) for v in views}


def with_enemies(bot):
    for kind, x, y in ENEMY:
        bot.add_unit(kind, x=x, y=y, enemy=True)
    return bot


def micro(ability, *classes, queue=None):
    item = {"ability": ability}
    if classes:
        item["unit_classes"] = list(classes)
    if queue is not None:
        item["queue"] = queue
    return item


def micro_call(goal, *items):
    return {"command_layer": "micro", "goal": goal, "micro": list(items)}


def op(task_type, location, count, unit="TERRAN_MARINE", queue=None, **extra):
    item = {
        "task_type": task_type,
        "location": location,
        "composition": [{"unit_type": unit, "count": count}],
        **extra,
    }
    if queue is not None:
        item["queue"] = queue
    return item


def op_call(goal, *ops):
    return {"command_layer": "operation", "goal": goal, "operations": list(ops)}


def macro_builds(goal, *tasks):
    return {
        "command_layer": "macro",
        "goal": goal,
        "macro": {"task_type": "tech_transition", "building_tasks": list(tasks)},
    }


def bt(building, queue=None, **extra):
    item = {"building_type": building, **extra}
    if queue is not None:
        item["queue"] = queue
    return item


# =========================================================================== ITEM 1: QUEUEING
# Docs: ActionInterface::UnitCommand(queued_command=false) default REPLACES orders; queue_command=true APPENDS
# (raw.proto ActionRawUnitCommand.queue_command).  Spoken '다음에/이어서/그 다음/끝나면/and then/after that' = append.

QUEUE_FIXTURES = [
    # (utterance, tool arguments the model emits, units, expected {(ability, queue)})
    ("탱크 시즈모드 다음에 마린 스팀팩",
     micro_call("시즈 다음에 스팀팩", micro("siege_mode", "TERRAN_SIEGETANK"), micro("stimpack", "TERRAN_MARINE", queue=True)),
     ((U.SIEGETANK, 2), (U.MARINE, 3)),
     {("SIEGEMODE_SIEGEMODE", False), ("EFFECT_STIM", True)}),
    ("마린 스팀팩 이어서 탱크 시즈",
     micro_call("스팀팩 이어서 시즈", micro("stimpack", "TERRAN_MARINE"), micro("siege_mode", "TERRAN_SIEGETANK", queue=True)),
     ((U.SIEGETANK, 1), (U.MARINE, 2)),
     {("EFFECT_STIM", False), ("SIEGEMODE_SIEGEMODE", True)}),
    ("스팀팩 그 다음 시즈모드",
     micro_call("스팀팩 그 다음 시즈", micro("marine_stimpack", "TERRAN_MARINE"), micro("siege_mode", "TERRAN_SIEGETANK", queue=True)),
     ((U.SIEGETANK, 1), (U.MARINE, 2)),
     {("EFFECT_STIM_MARINE", False), ("SIEGEMODE_SIEGEMODE", True)}),
    ("시즈모드 끝나면 스팀팩",
     micro_call("시즈 끝나면 스팀팩", micro("siege_mode", "TERRAN_SIEGETANK"), micro("marauder_stimpack", "TERRAN_MARAUDER", queue=True)),
     ((U.SIEGETANK, 1), (U.MARAUDER, 2)),
     {("SIEGEMODE_SIEGEMODE", False), ("EFFECT_STIM_MARAUDER", True)}),
    ("siege the tanks and then stim the marines",
     micro_call("siege then stim", micro("siege_mode", "TERRAN_SIEGETANK"), micro("stimpack", "TERRAN_MARINE", queue=True)),
     ((U.SIEGETANK, 2), (U.MARINE, 2)),
     {("SIEGEMODE_SIEGEMODE", False), ("EFFECT_STIM", True)}),
    ("stim the marines, after that siege the tanks",
     micro_call("stim then siege", micro("stimpack", "TERRAN_MARINE"), micro("siege_mode", "TERRAN_SIEGETANK", queue=True)),
     ((U.SIEGETANK, 2), (U.MARINE, 2)),
     {("EFFECT_STIM", False), ("SIEGEMODE_SIEGEMODE", True)}),
    ("시즈모드 (no then-word) is a fresh order",
     micro_call("시즈모드", micro("siege_mode", "TERRAN_SIEGETANK")),
     ((U.SIEGETANK, 2),),
     {("SIEGEMODE_SIEGEMODE", False)}),
    ("queue:false spelled out stays fresh",
     micro_call("시즈모드", micro("siege_mode", "TERRAN_SIEGETANK", queue=False)),
     ((U.SIEGETANK, 2),),
     {("SIEGEMODE_SIEGEMODE", False)}),
    ("정찰 갔다가 본진으로 돌아와",
     op_call("정찰 갔다가 본진으로 돌아와",
             op("scout_with_units", "enemy_natural", 2),
             op("regroup_with_units", "home", 2, queue=True)),
     ((U.MARINE, 4),),
     {("MOVE_MOVE", False), ("MOVE_MOVE", True)}),
    ("앞마당 공격하고 다음에 본진 공격",
     op_call("앞마당 다음에 본진",
             op("pressure_with_main_army", "enemy_natural", 3),
             op("pressure_with_main_army", "enemy_main", 3, queue=True, operation_id="attack-main")),
     ((U.MARINE, 3),),
     {("ATTACK", False), ("ATTACK", True)}),
    ("scout the natural and then come back home",
     op_call("scout then home", op("scout_with_units", "enemy_natural", 1), op("regroup_with_units", "home", 1, queue=True)),
     ((U.MARINE, 2),),
     {("MOVE_MOVE", False), ("MOVE_MOVE", True)}),
    ("공격 가자 (fresh) replaces",
     op_call("공격", op("pressure_with_main_army", "enemy_main", 2)),
     ((U.MARINE, 2),),
     {("ATTACK", False)}),
    ("야마토 다음에 야마토 (single queued ability)",
     micro_call("야마토 이어서", micro("yamato", "TERRAN_BATTLECRUISER", queue=True)),
     ((U.BATTLECRUISER, 1),),
     {("YAMATO_YAMATOGUN", True)}),
]


@pytest.mark.parametrize("utterance,args,units,expected", QUEUE_FIXTURES, ids=[f[0][:40] for f in QUEUE_FIXTURES])
def test_spoken_then_words_reach_the_wire_as_queue_command(utterance, args, units, expected) -> None:
    bot = with_enemies(harness.make_bot(*units))
    result, views = run(bot, *lower_tool_args(args))
    assert views, (utterance, reports(result))
    assert pairs(views) == expected, (utterance, views)


def test_a_queued_follow_up_addresses_the_same_units_as_the_order_it_follows() -> None:
    """'정찰 갔다가 돌아와': operations normally get DISJOINT units ('2기 정찰, 4기 공격'); a queued one is the SAME
    force, so it must reuse the preceding order's units and append behind it."""

    args = op_call("정찰 갔다가 본진으로 돌아와", op("scout_with_units", "enemy_natural", 2), op("regroup_with_units", "home", 2, queue=True))
    bot = harness.make_bot((U.MARINE, 5))
    _, views = run(bot, *lower_tool_args(args))
    fresh = [v for v in views if not v["queue"]]
    queued = [v for v in views if v["queue"]]
    assert tags_of(fresh) and tags_of(fresh) == tags_of(queued)
    assert len(tags_of(fresh)) == 2
    assert fresh[0]["pos"] != queued[0]["pos"]


def test_a_non_queued_operation_pair_still_gets_disjoint_units() -> None:
    args = op_call("2기 정찰, 2기 공격", op("scout_with_units", "enemy_natural", 2), op("pressure_with_main_army", "enemy_main", 2))
    bot = harness.make_bot((U.MARINE, 5))
    _, views = run(bot, *lower_tool_args(args))
    assert {v["queue"] for v in views} == {False}
    assert len(tags_of(views)) == 4


def test_the_queue_flag_survives_the_vector_and_the_plan_serialisation() -> None:
    plan = plan_for_tool_args(op_call("g", op("scout_with_units", "enemy_natural", 1), op("regroup_with_units", "home", 1, queue=True)))
    assert [a.queue for a in plan.ordered_actions] == [False, True]
    from starcraft_commander.unified_command_router import execution_plan_from_mapping

    again = execution_plan_from_mapping(plan.to_dict())
    assert [a.queue for a in again.ordered_actions] == [False, True]


def test_queue_must_be_a_boolean_and_is_rejected_loudly_otherwise() -> None:
    from starcraft_commander.realtime_intent import tool_arguments_to_provider_output

    with pytest.raises(ValueError, match="queue"):
        tool_arguments_to_provider_output(micro_call("g", {"ability": "siege_mode", "queue": "yes"}))


def test_tool_schema_and_instructions_teach_the_queue_words() -> None:
    from starcraft_commander.realtime_intent import build_realtime_instructions, build_submit_command_tool

    tool = build_submit_command_tool()["parameters"]["properties"]
    assert tool["micro"]["items"]["properties"]["queue"]["type"] == "boolean"
    assert tool["operations"]["items"]["properties"]["queue"]["type"] == "boolean"
    assert tool["macro"]["properties"]["building_tasks"]["items"]["properties"]["queue"]["type"] == "boolean"
    text = build_realtime_instructions()
    for word in ("다음에", "이어서", "그 다음", "끝나면", "and then", "after that"):
        assert word in text, word


# ---- build orders: '배럭 짓고 다음에 서플' = shift-queue on the SAME SCV; a structure whose prerequisite the first
# one provides ('배럭 짓고 다음에 벙커') must wait for building_completed, because the game answers
# BuildTechRequirementsNotMet to a queued Bunker while the Barracks does not exist yet.
# NOTE: a realtime *macro* call is published to MicroMachine (which consumes only building_tasks.0 and orders its own
# builds); the Direct SC2 build path is the operation-layer vector (text LLM lane / MCP / one-off build vectors).


def direct_build_plan(goal, *tasks):
    from starcraft_commander.policy_modulation_provider import compile_policy_modulation_provider_output
    from starcraft_commander.unified_command_router import route_policy_vector

    result = compile_policy_modulation_provider_output(
        {"goal": goal, "command_layer": "operation", "building_tasks": list(tasks)}
    )
    assert result.vector is not None, result
    plan = route_policy_vector(result.vector).direct_plan
    assert plan is not None
    return plan


def test_two_independent_builds_are_one_scv_with_the_second_queued() -> None:
    plan = direct_build_plan("서플 짓고 다음에 정제소", bt("TERRAN_SUPPLYDEPOT", anchor="self_main"), bt("TERRAN_ENGINEERINGBAY", queue=True, anchor="self_main"))
    assert [a.queue for a in plan.ordered_actions] == [False, True]
    assert all(a.action_type is SC2ActionType.BUILD_STRUCTURE for a in plan.ordered_actions)
    bot = harness.make_bot((U.SCV, 3))
    result, views = run(bot, *plan.ordered_actions)
    assert [v["queue"] for v in views] == [False, True], reports(result)
    assert len(tags_of(views)) == 1, "a queued build continues on the SAME SCV"
    assert [v["ability_name"] for v in views] == ["TERRANBUILD_SUPPLYDEPOT", "TERRANBUILD_ENGINEERINGBAY"]


def test_build_then_dependent_build_waits_for_the_prerequisite_instead_of_queueing() -> None:
    plan = direct_build_plan("배럭 짓고 다음에 벙커", bt("TERRAN_BARRACKS"), bt("TERRAN_BUNKER", queue=True))
    assert [a.subject for a in plan.ordered_actions] == ["BARRACKS"]
    dependent = plan.audit["dependent_plans"]
    assert dependent[0]["resume_on"] == ["building_completed"]
    step = dependent[0]["plan"]["ordered_actions"][0]
    assert step["subject"] == "BUNKER" and not step.get("queue", False)


# ---- independent abilities in one utterance ("시즈하고 스팀팩"): two simultaneous fresh orders, not a refusal

def test_two_different_abilities_without_a_then_word_are_two_fresh_orders() -> None:
    args = micro_call("시즈하고 스팀팩", micro("siege_mode", "TERRAN_SIEGETANK"), micro("stimpack", "TERRAN_MARINE"))
    bot = harness.make_bot((U.SIEGETANK, 2), (U.MARINE, 2))
    result, views = run(bot, *lower_tool_args(args))
    assert pairs(views) == {("SIEGEMODE_SIEGEMODE", False), ("EFFECT_STIM", False)}, reports(result)


def test_an_unmarked_ability_inside_operations_is_still_refused() -> None:
    from starcraft_commander.policy_modulation_provider import compile_policy_modulation_provider_output

    result = compile_policy_modulation_provider_output(
        {"source": "llm", "goal": "g", "operations": [{"operation_id": "a", "tactical_task": {
            "task_type": "execute_ability", "ability": "unsiege", "unit_classes": ["TERRAN_SIEGETANKSIEGED"]}}]}
    )
    assert not result.ok and "live operation director" in result.refusal_reason


# =========================================================================== ITEM 2: MISSING ABILITIES
# stableid.json / sc2.ids ids: SCANNERSWEEP_SCAN 399 (Point), CALLDOWNMULE_CALLDOWNMULE 171 (Unit,Point),
# SUPPLYDROP_SUPPLYDROP 255 (Unit), LIFT_{BARRACKS 452, FACTORY 485, STARPORT 518, COMMANDCENTER 417,
# ORBITALCOMMAND 1522} (None), LAND_{BARRACKS 554, FACTORY 520, STARPORT 522, COMMANDCENTER 419,
# ORBITALCOMMAND 1524} (Point).  Scan / MULE / Supply Drop cost 50 energy (Orbital Command, public rule).

POINTS = {"home": (20.0, 20.0), "natural": (50.0, 50.0), "enemy_main": (180.0, 180.0), "enemy_natural": (150.0, 150.0)}


def ability_call(goal, ability, *classes, location=None, queue=None):
    item = micro(ability, *classes, queue=queue)
    if location:
        item["location"] = location
    return micro_call(goal, item)


def orbital_world(*, energy=100.0, orbitals=1):
    bot = harness.make_bot()
    for index in range(orbitals):
        bot.add_unit(U.ORBITALCOMMAND, x=20.0 + index, y=20.0, energy=energy)
    for index in range(4):
        bot.add_unit(U.MINERALFIELD, x=22.0 + index, y=24.0)
    bot.add_unit(U.SUPPLYDEPOT, x=26.0, y=18.0)
    return bot


def ids(name):
    return AbilityId[name].value


ABILITY_FIXTURES = [
    # (utterance, call, expected ability, expected target field, expected pos or None)
    ("적 본진 스캔", ability_call("적 본진 스캔", "scanner_sweep", location="enemy_main"), "SCANNERSWEEP_SCAN", "target_world_space_pos", POINTS["enemy_main"]),
    ("스캐너 적 앞마당", ability_call("스캐너 적 앞마당", "scanner_sweep", location="enemy_natural"), "SCANNERSWEEP_SCAN", "target_world_space_pos", POINTS["enemy_natural"]),
    ("scan the enemy natural", ability_call("scan the enemy natural", "scanner_sweep", "TERRAN_ORBITALCOMMAND", location="enemy_natural"), "SCANNERSWEEP_SCAN", "target_world_space_pos", POINTS["enemy_natural"]),
    ("우리 본진 스캔 (cloaked units at home)", ability_call("우리 본진 스캔", "scanner_sweep", location="home"), "SCANNERSWEEP_SCAN", "target_world_space_pos", POINTS["home"]),
    ("뮤마 써", ability_call("뮤마 써", "calldown_mule"), "CALLDOWNMULE_CALLDOWNMULE", "target_unit_tag", None),
    ("자원 채취 (MULE)", ability_call("자원 채취 뮤마", "calldown_mule", "TERRAN_ORBITALCOMMAND"), "CALLDOWNMULE_CALLDOWNMULE", "target_unit_tag", None),
    ("call down a MULE", ability_call("call down a MULE", "calldown_mule"), "CALLDOWNMULE_CALLDOWNMULE", "target_unit_tag", None),
    ("보급 드롭", ability_call("보급 드롭", "supply_drop"), "SUPPLYDROP_SUPPLYDROP", "target_unit_tag", None),
    ("supply drop", ability_call("supply drop", "supply_drop", "TERRAN_ORBITALCOMMAND"), "SUPPLYDROP_SUPPLYDROP", "target_unit_tag", None),
    ("배럭 이륙", ability_call("배럭 이륙", "lift_off", "TERRAN_BARRACKS"), "LIFT_BARRACKS", None, None),
    ("군수공장 띄워", ability_call("군수공장 띄워", "lift_off", "TERRAN_FACTORY"), "LIFT_FACTORY", None, None),
    ("우주공항 이륙", ability_call("우주공항 이륙", "lift_off", "TERRAN_STARPORT"), "LIFT_STARPORT", None, None),
    ("사령부 이륙", ability_call("사령부 이륙", "lift_off", "TERRAN_COMMANDCENTER"), "LIFT_COMMANDCENTER", None, None),
    ("배럭 본진에 착륙", ability_call("배럭 본진에 착륙", "land", "TERRAN_BARRACKSFLYING", location="home"), "LAND_BARRACKS", "target_world_space_pos", POINTS["home"]),
    ("군수공장 앞마당에 내려안아", ability_call("군수공장 앞마당에 내려안아", "land", "TERRAN_FACTORYFLYING", location="natural"), "LAND_FACTORY", "target_world_space_pos", POINTS["natural"]),
    ("land the starport at the natural", ability_call("land the starport", "land", "TERRAN_STARPORTFLYING", location="natural"), "LAND_STARPORT", "target_world_space_pos", POINTS["natural"]),
    ("사령부 착륙 앞마당", ability_call("사령부 착륙", "land", "TERRAN_COMMANDCENTERFLYING", location="natural"), "LAND_COMMANDCENTER", "target_world_space_pos", POINTS["natural"]),
]

WORLD_FOR = {
    "SCANNERSWEEP_SCAN": lambda: orbital_world(),
    "CALLDOWNMULE_CALLDOWNMULE": lambda: orbital_world(),
    "SUPPLYDROP_SUPPLYDROP": lambda: orbital_world(),
    "LIFT_BARRACKS": lambda: harness.make_bot((U.BARRACKS, 1)),
    "LIFT_FACTORY": lambda: harness.make_bot((U.FACTORY, 1)),
    "LIFT_STARPORT": lambda: harness.make_bot((U.STARPORT, 1)),
    "LIFT_COMMANDCENTER": lambda: harness.make_bot((U.COMMANDCENTER, 1)),
    "LAND_BARRACKS": lambda: harness.make_bot((U.BARRACKSFLYING, 1)),
    "LAND_FACTORY": lambda: harness.make_bot((U.FACTORYFLYING, 1)),
    "LAND_STARPORT": lambda: harness.make_bot((U.STARPORTFLYING, 1)),
    "LAND_COMMANDCENTER": lambda: harness.make_bot((U.COMMANDCENTERFLYING, 1)),
}


@pytest.mark.parametrize("utterance,args,ability,target,pos", ABILITY_FIXTURES, ids=[f[0][:40] for f in ABILITY_FIXTURES])
def test_new_abilities_reach_the_wire_with_the_documented_id_and_target_kind(utterance, args, ability, target, pos) -> None:
    bot = WORLD_FOR[ability]()
    result, views = run(bot, *lower_tool_args(args))
    assert views, (utterance, reports(result))
    assert {v["ability_name"] for v in views} == {ability}, views
    assert {v["ability_id"] for v in views} == {ids(ability)}
    assert {v["target"] for v in views} == {target}, views
    assert {v["queue"] for v in views} == {False}
    if pos is not None:
        assert {v["pos"] for v in views} == {pos}
    if target == "target_unit_tag":
        by_tag = {u.tag: u for u in list(bot.units) + list(bot.structures) + list(bot.mineral_field)}
        kind = by_tag[views[0]["unit_tag"]].type_id
        assert kind == (U.MINERALFIELD if ability.startswith("CALLDOWNMULE") else U.SUPPLYDEPOT)


def test_scan_is_one_cast_even_with_two_orbitals_and_uses_the_one_with_most_energy() -> None:
    bot = harness.make_bot()
    low = bot.add_unit(U.ORBITALCOMMAND, energy=60.0)
    high = bot.add_unit(U.ORBITALCOMMAND, energy=150.0)
    _, views = run(bot, *lower_tool_args(ability_call("스캔", "scanner_sweep", location="enemy_main")))
    assert len(views) == 1 and tags_of(views) == {high.tag}, low.tag


def test_mule_goes_to_every_ready_orbital_on_different_patches() -> None:
    bot = orbital_world(orbitals=2)
    _, views = run(bot, *lower_tool_args(ability_call("뮤마", "calldown_mule")))
    assert len(views) == 2 and len({v["unit_tag"] for v in views}) == 2


def test_a_queued_scan_appends() -> None:
    args = micro_call("스캔 다음에 뮤마", micro("scanner_sweep"), micro("calldown_mule", queue=True))
    args["micro"][0]["location"] = "enemy_main"
    bot = orbital_world(orbitals=1)
    _, views = run(bot, *lower_tool_args(args))
    assert pairs(views) == {("SCANNERSWEEP_SCAN", False), ("CALLDOWNMULE_CALLDOWNMULE", True)}


# ---- refusals with a user-visible reason (adapter detail -> narrator Korean text)

REFUSALS = [
    ("no orbital at all", lambda: harness.make_bot((U.COMMANDCENTER, 1)), ability_call("스캔", "scanner_sweep", location="enemy_main"), "no_orbital_command", "궤도 사령부"),
    ("scan: energy 30", lambda: orbital_world(energy=30.0), ability_call("스캔", "scanner_sweep", location="enemy_main"), "no_ready_caster: not_enough_energy", "에너지"),
    ("mule: energy 49", lambda: orbital_world(energy=49.0), ability_call("뮤마", "calldown_mule"), "no_ready_caster: not_enough_energy", "에너지"),
    ("drop: energy 0", lambda: orbital_world(energy=0.0), ability_call("보급 드롭", "supply_drop"), "no_ready_caster: not_enough_energy", "에너지"),
    ("scan without a place", lambda: orbital_world(), ability_call("스캔", "scanner_sweep"), "target_required: explicit_point_required", "어디"),
    ("mule without minerals", lambda: _without(orbital_world(), U.MINERALFIELD), ability_call("뮤마", "calldown_mule"), "target_required: no_mineral_patch", "미네랄"),
    ("drop without a depot", lambda: _without(orbital_world(), U.SUPPLYDEPOT), ability_call("보급 드롭", "supply_drop"), "target_required: no_supply_depot", "보급고"),
    ("lift with no such structure", lambda: harness.make_bot((U.MARINE, 2)), ability_call("배럭 이륙", "lift_off", "TERRAN_BARRACKS"), "no_matching_caster", "유닛/건물"),
    ("land without a flying structure", lambda: harness.make_bot((U.BARRACKS, 1)), ability_call("착륙", "land", "TERRAN_BARRACKSFLYING", location="home"), "no_matching_caster", "유닛/건물"),
    ("land without a point", lambda: harness.make_bot((U.BARRACKSFLYING, 1)), ability_call("착륙", "land", "TERRAN_BARRACKSFLYING"), "target_required: explicit_point_required", "어디"),
    ("lift while still training", lambda: _busy_barracks(), ability_call("배럭 이륙", "lift_off", "TERRAN_BARRACKS"), "no_ready_caster: structure_busy", "생산"),
    ("lift an unfinished barracks", lambda: _unfinished_barracks(), ability_call("배럭 이륙", "lift_off", "TERRAN_BARRACKS"), "no_ready_caster: under_construction", "건설"),
]


def _without(bot, unit_type):
    for group in (bot.units, bot.structures, bot.mineral_field):
        for unit in [u for u in group if u.type_id == unit_type]:
            group.remove(unit)
    return bot


def _busy_barracks():
    bot = harness.make_bot()
    bot.add_unit(U.BARRACKS, orders=(AbilityId.BARRACKSTRAIN_MARINE,))
    return bot


def _unfinished_barracks():
    bot = harness.make_bot()
    bot.add_unit(U.BARRACKS, build_progress=0.4)
    return bot


@pytest.mark.parametrize("name,world,args,detail,korean", REFUSALS, ids=[r[0] for r in REFUSALS])
def test_infeasible_ability_is_refused_with_a_visible_korean_reason(name, world, args, detail, korean) -> None:
    from starcraft_commander.narrator import narrate_sc2_plan_result

    bot = world()
    result, views = run(bot, *lower_tool_args(args))
    assert views == [], views
    assert reports(result)[0]["applied"] is False
    assert reports(result)[0]["detail"] == detail
    text = narrate_sc2_plan_result(result).response_text
    assert korean in text, text
    assert "세부:" not in text, "a raw adapter detail leaked to the user: " + text


def test_every_new_token_is_in_the_realtime_enum_the_instructions_and_has_korean_aliases() -> None:
    from starcraft_commander import realtime_intent as ri

    new = {"scanner_sweep", "calldown_mule", "supply_drop", "lift_off", "land"}
    assert new <= set(ri.ABILITIES)
    tool = ri.build_submit_command_tool()["parameters"]["properties"]["micro"]["items"]["properties"]
    assert new <= set(tool["ability"]["enum"])
    assert set(tool["location"]["enum"]) == set(ri.LOCATION_INTENTS)
    text = ri.build_realtime_instructions()
    for word in ("스캐너", "스캔", "뮤마", "자원 채취", "보급 드롭", "이륙", "착륙", "띄워", "내려안아"):
        assert word in text, word


@pytest.mark.parametrize("ability", ["scanner_sweep", "calldown_mule", "supply_drop", "lift_off", "land"])
def test_new_tokens_are_semantic_abilities_the_c_plus_plus_kit_does_not_claim(ability) -> None:
    """They are Direct-only: the MicroMachine explicit-ability patch contract (tests/test_micromachine_integration_kit)
    enumerates MICROMACHINE_TACTICAL_ABILITIES and must stay unchanged."""

    from starcraft_commander.policy_modulation import MICROMACHINE_DIRECT_ONLY_ABILITIES, MICROMACHINE_TACTICAL_ABILITIES

    assert ability in MICROMACHINE_DIRECT_ONLY_ABILITIES and ability not in MICROMACHINE_TACTICAL_ABILITIES


@pytest.mark.parametrize(
    "text",
    ["스캔 하지 마", "뮤마 쓰지 마", "보급 드롭 하지 마", "이륙하지 마", "착륙 취소"],
)
def test_a_negated_new_ability_phrase_executes_nothing(text) -> None:
    from starcraft_commander.realtime_intent import micro_phrase_is_negated

    assert micro_phrase_is_negated(text)


def test_the_fast_rule_cap_is_untouched() -> None:
    from starcraft_commander.realtime_intent import MAX_PROMOTED_FAST_PATH_FAMILIES, PROMOTED_FAST_PATH_FAMILIES, match_fast_path

    assert len(PROMOTED_FAST_PATH_FAMILIES) <= MAX_PROMOTED_FAST_PATH_FAMILIES == 5
    for text in ("스캔", "뮤마", "보급 드롭", "이륙", "착륙", "배럭 띄워"):
        assert match_fast_path(text) is None, text
