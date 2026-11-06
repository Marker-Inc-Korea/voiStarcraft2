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
