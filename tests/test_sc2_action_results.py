"""SC2 ``ActionResult`` handling: every protocol value is described, and a rejection by the GAME reaches the user.

Offline only.  The fakes replay scripted answers through python-sc2's real ``combine_actions`` /
``BotAI._do_actions`` (see ``scripts/sc2_accuracy/harness.py``).  Nothing here proves what a live game answers
for a given order; it proves that whatever the game answers is read, mapped, surfaced - and that silence stays
``accepted=None`` rather than turning into success.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sc2.ids.ability_id")
error_pb2 = pytest.importorskip("s2clientprotocol.error_pb2")

from scripts.sc2_accuracy import harness  # noqa: E402
from scripts.sc2_accuracy.harness import FakeActionError  # noqa: E402
from scripts.sc2_accuracy.lowering import lower_direct, lower_micro  # noqa: E402
from sc2.ids.ability_id import AbilityId  # noqa: E402
from sc2.ids.unit_typeid import UnitTypeId as U  # noqa: E402

from starcraft_commander import sc2_action_results as ar  # noqa: E402
from starcraft_commander.contracts import SC2ActionType, SC2CommandAction  # noqa: E402
from starcraft_commander.narrator import narrate_game_rejection, narrate_sc2_plan_result  # noqa: E402

pytestmark = pytest.mark.skipif(
    not harness.stableid_available(), reason="stableid.json not present on this machine"
)

ENUM = list(error_pb2.ActionResult.items())


# =========================================================================== vocabulary


@pytest.mark.parametrize("name,code", ENUM)
def test_every_protocol_value_is_described_with_a_stable_vocabulary(name, code) -> None:
    info = ar.describe_action_result(code)
    assert info.name == name and info.code == code
    assert info.known, f"{name}={code} has no mapping in sc2_action_results (protocol upgrade?)"
    assert info.category in ar.CATEGORIES
    assert info.reason_ko.strip() and info.reason_en.strip()
    assert isinstance(info.user_fixable, bool) and isinstance(info.retryable, bool)
    assert info.accepted is (name == "Success")
    if name != "Success":
        assert info.category != "success"
    assert ar.describe_action_result_name(name, code) == info


def test_unmapped_names_would_be_flagged_unknown_so_the_enum_test_cannot_pass_silently() -> None:
    fake = ar.describe_action_result_name("BrandNewResult", 9999)
    assert fake.known is False and "9999" in fake.reason_ko and "9999" in fake.reason_en
    assert fake.accepted is False


@pytest.mark.parametrize("code", [0, 215, 4096, 10**6])
def test_unknown_future_codes_map_to_a_safe_generic_rejection(code) -> None:
    info = ar.describe_action_result(code)
    assert info.accepted is False and info.known is False
    assert str(code) in info.reason_en and str(code) in info.reason_ko
    assert (info.category, info.user_fixable, info.retryable) == ("other", False, False)


def test_only_success_is_accepted_and_accepts_python_sc2_enums() -> None:
    from sc2.data import ActionResult

    assert ar.describe_action_result(ActionResult.Success).accepted is True
    assert ar.describe_action_result(ActionResult.NotEnoughMinerals).accepted is False
    assert [n for n, c in ENUM if ar.describe_action_result(c).accepted] == ["Success"]
    with pytest.raises(TypeError):
        ar.describe_action_result("Success")


@pytest.mark.parametrize(
    "name,category,fixable,retryable",
    [
        ("NotEnoughMinerals", "resources", True, True),
        ("NotEnoughVespene", "resources", True, True),
        ("NotEnoughFood", "supply", True, True),
        ("NotEnoughEnergy", "energy", True, True),
        ("CantBuildLocationInvalid", "placement", True, False),
        ("CantFindPlacementLocation", "placement", True, True),
        ("TargetIsOutOfRange", "range", True, True),
        ("TechRequirementsNotMet", "tech", True, False),
        ("BuildTechRequirementsNotMet", "tech", True, False),
        ("QueueIsFull", "queue", True, True),
        ("NotWhileOccupied", "busy", True, True),
        ("CantTargetCloakedUnits", "target", True, False),
        ("MustTargetEnemyUnits", "target", True, False),
    ],
)
def test_selected_mappings_have_the_right_category_and_advice(name, category, fixable, retryable) -> None:
    info = ar.describe_action_result(error_pb2.ActionResult.Value(name))
    assert (info.category, info.user_fixable, info.retryable) == (category, fixable, retryable)
    assert info.reason_ko != info.reason_en


# =========================================================================== tracker


def test_tracker_never_reports_accepted_without_an_explicit_success() -> None:
    tracker = ar.GameResultTracker()
    tracker.register(7, [AbilityId.EFFECT_STIM.value], 1)
    summary = tracker.summarize()
    assert summary["accepted"] is None and summary["pending_count"] == 1 and summary["code"] is None
    tracker.record_response(1, [((7,), AbilityId.EFFECT_STIM.value, 1)])
    assert tracker.summarize()["accepted"] is True
    assert ar.GameResultTracker().summarize() is None


def test_an_observation_error_overrides_an_earlier_success_but_a_rejection_is_never_downgraded() -> None:
    tracker = ar.GameResultTracker()
    tracker.register(7, [AbilityId.BARRACKSTRAIN_MARINE.value], 10)
    tracker.record_response(10, [((7,), AbilityId.BARRACKSTRAIN_MARINE.value, 1)])
    assert tracker.ingest_action_errors(11, [(7, AbilityId.BARRACKSTRAIN_MARINE.value, 9)]) == 1
    summary = tracker.summarize()
    assert summary["accepted"] is False and summary["name"] == "NotEnoughMinerals"
    assert summary["channel"] == "observation_action_errors"
    tracker.record_response(12, [((7,), AbilityId.BARRACKSTRAIN_MARINE.value, 1)])  # a late Success must not win
    assert tracker.summarize()["accepted"] is False


def test_action_errors_outside_the_response_window_or_for_other_units_are_ignored() -> None:
    tracker = ar.GameResultTracker(response_window_loops=100)
    tracker.register(7, [AbilityId.BARRACKSTRAIN_MARINE.value], 10)
    assert tracker.ingest_action_errors(500, [(7, AbilityId.BARRACKSTRAIN_MARINE.value, 9)]) == 0
    assert tracker.ingest_action_errors(20, [(8, AbilityId.BARRACKSTRAIN_MARINE.value, 9)]) == 0
    assert tracker.ingest_action_errors(20, [(7, AbilityId.ATTACK.value, 9)]) == 0
    assert tracker.summarize()["accepted"] is None


# =========================================================================== scenarios


def _build(subject: str, target: str = "self_main") -> SC2CommandAction:
    return SC2CommandAction(SC2ActionType.BUILD_STRUCTURE, subject, target=target, metadata={"building_type": subject})


def _train_marine_bot():
    bot = harness.make_bot((U.SCV, 2))
    barracks = bot.add_unit(U.BARRACKS)
    return bot, barracks, lower_direct("train_unit", ["TERRAN_MARINE"]), AbilityId.BARRACKSTRAIN_MARINE


def _siege_bot():
    bot = harness.make_bot()
    tank = bot.add_unit(U.SIEGETANK)
    return bot, tank, lower_micro("siege_mode"), AbilityId.SIEGEMODE_SIEGEMODE


def _stim_bot():
    bot = harness.make_bot()
    marine = bot.add_unit(U.MARINE, health=55.0, health_max=55.0)
    return bot, marine, lower_micro("stimpack"), AbilityId.EFFECT_STIM


def _cloak_bot():
    bot = harness.make_bot()
    banshee = bot.add_unit(U.BANSHEE, energy=100.0)
    return bot, banshee, lower_micro("banshee_cloak"), AbilityId.BEHAVIOR_CLOAKON_BANSHEE


def _depot_bot():
    bot = harness.make_bot((U.SCV, 2))
    return bot, None, [_build("SUPPLYDEPOT")], AbilityId.TERRANBUILD_SUPPLYDEPOT


def _techlab_bot():
    bot = harness.make_bot()
    barracks = bot.add_unit(U.BARRACKS)
    return bot, barracks, lower_direct("train_unit", ["TERRAN_MARINE"]), AbilityId.BARRACKSTRAIN_MARINE


def _yamato_bot():
    bot = harness.make_bot()
    bc = bot.add_unit(U.BATTLECRUISER)
    bot.add_unit(U.ZEALOT, x=60.0, y=60.0, enemy=True)
    return bot, bc, lower_micro("yamato"), AbilityId.YAMATO_YAMATOGUN


# (scenario id, bot factory, game answer, expected category, expected korean fragment)
SCENARIOS = [
    ("cant-afford-marine", _train_marine_bot, "NotEnoughMinerals", "resources", "미네랄"),
    ("supply-blocked", _train_marine_bot, "NotEnoughFood", "supply", "인구수"),
    ("no-energy-banshee-cloak", _cloak_bot, "NotEnoughEnergy", "energy", "에너지"),
    ("no-energy-stim-hp", _stim_bot, "NotEnoughLife", "unit_state", "생명력"),
    ("cant-place-depot", _depot_bot, "CantBuildLocationInvalid", "placement", "건설 위치"),
    ("depot-cant-see-location", _depot_bot, "CantSeeBuildLocation", "placement", "보이지"),
    ("yamato-target-out-of-range", _yamato_bot, "TargetIsOutOfRange", "range", "사거리"),
    ("need-tech-lab", _techlab_bot, "TechRequirementsNotMet", "tech", "선행"),
    ("unit-busy", _siege_bot, "NotWhileOccupied", "busy", "다른 일"),
    ("queue-full", _train_marine_bot, "QueueIsFull", "queue", "대기열"),
    ("order-queue-full", _stim_bot, "OrderQueueIsFull", "queue", "대기열"),
    ("invalid-target-cloaked", _yamato_bot, "CantTargetCloakedUnits", "target", "대상"),
    ("unit-cant-move", _siege_bot, "UnitCantMove", "unit_state", "움직일"),
    ("generic-error", _stim_bot, "Error", "other", "거부"),
]


def _first_game_result(run) -> dict:
    results = run.result.audit["game_results"]
    return results[sorted(results, key=int)[0]]


def _answer_in_request_channel(factory, answer):
    bot, _, actions, ability = factory()
    return harness.run_with_game(bot, *actions, answers={ability.name: answer, "*": answer})


@pytest.mark.parametrize("sid,factory,answer,category,fragment", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_a_rejection_in_the_request_response_is_blocked_with_the_games_reason(sid, factory, answer, category, fragment) -> None:
    run = _answer_in_request_channel(factory, answer)
    assert run.bot.actions, "scenario must have queued at least one real order"
    # Before the frame is committed the game cannot have answered: unknown, never accepted.
    assert _first_game_result(run)["accepted"] is None
    run.commit_frame(frame=2)
    summary = run.adapter.game_result_since(0)
    assert summary["accepted"] is False and summary["name"] == answer
    assert summary["category"] == category and fragment in summary["reason_ko"]
    assert summary["channel"] == "request_response"
    assert summary["reason_en"] and summary["code"] == error_pb2.ActionResult.Value(answer)


@pytest.mark.parametrize("sid,factory,answer,category,fragment", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_a_rejection_in_the_observation_channel_fails_the_lease_with_the_games_reason(sid, factory, answer, category, fragment) -> None:
    bot, _, actions, ability = factory()
    run = harness.run_with_game(bot, *actions)  # request channel says Success for everything
    seen = []
    run.executor.add_game_rejection_listener(seen.append)
    lease = run.lease(frame=1)
    run.commit_frame(frame=2)
    assert run.adapter.game_result_since(0)["accepted"] is True  # the only thing known so far is Success
    run.next_observation(3, [FakeActionError(tag, ability, answer) for tag in sorted(set(run.tags))])
    # Frame 3 sees the red text: the lease must fail instead of completing on ``order_issued``.
    states = run.executor.tick_direct_commands(3)
    failed = run.executor.direct_lifecycle.get("cmd-1")
    assert failed.state.value == "failed" and failed.release_reason == "game_rejected", states
    game_result = failed.evidence["game_result"]
    assert game_result["accepted"] is False and game_result["name"] == answer
    assert game_result["category"] == category and fragment in game_result["reason_ko"]
    assert game_result["channel"] == "observation_action_errors"
    assert [item["command_id"] for item in seen] == ["cmd-1"]
    assert narrate_game_rejection(game_result).startswith("게임이 거부함: ")


def test_a_command_with_no_response_yet_does_not_complete_on_order_issued_and_stays_unknown() -> None:
    bot, _, actions, ability = _stim_bot()
    run = harness.run_with_game(bot, *actions)
    run.lease(frame=1)
    # nothing was ever sent to the game (no commit): there is no answer at all
    run.executor.tick_direct_commands(2)
    lease = run.executor.direct_lifecycle.get("cmd-1")
    assert lease.state.value == "active"
    assert lease.evidence["game_result"]["accepted"] is None
    assert lease.evidence["game_result"]["pending_count"] >= 1


def test_success_completes_only_after_a_later_observation_confirms_no_action_error() -> None:
    bot, _, actions, ability = _stim_bot()
    run = harness.run_with_game(bot, *actions)
    run.lease(frame=1)
    run.commit_frame(frame=2)
    run.executor.tick_direct_commands(2)  # answered Success this very loop: error may still arrive next loop
    assert run.executor.direct_lifecycle.get("cmd-1").state.value == "active"
    run.next_observation(3, [])
    run.executor.tick_direct_commands(3)
    lease = run.executor.direct_lifecycle.get("cmd-1")
    assert lease.state.value == "completed" and lease.evidence["game_result"]["accepted"] is True


def test_a_protocol_error_or_short_reply_leaves_the_order_unknown_not_accepted() -> None:
    for kwargs in ({"protocol_error": True}, {"short": True}):
        bot, _, actions, ability = _stim_bot()
        client = harness.FakeActionClient({"*": "NotEnoughLife"}, **kwargs)
        run = harness.run_with_game(bot, *actions, client=client)
        run.commit_frame(frame=2)
        summary = run.adapter.game_result_since(0)
        assert summary["accepted"] is None and summary["pending_count"] >= 1, kwargs


def test_an_all_success_reply_is_the_only_way_to_accepted_true() -> None:
    bot, _, actions, _ = _train_marine_bot()
    run = harness.run_with_game(bot, *actions)
    run.commit_frame(frame=2)
    summary = run.adapter.game_result_since(0)
    assert summary["accepted"] is True and summary["code"] == 1 and summary["rejected_count"] == 0


def test_one_rejected_order_among_many_rejects_the_summary_and_counts_each() -> None:
    bot = harness.make_bot()
    first = bot.add_unit(U.MARINE, health=55.0, health_max=55.0)
    second = bot.add_unit(U.MARINE, health=55.0, health_max=55.0)
    run = harness.run_with_game(bot, *lower_micro("stimpack"))
    assert len(set(run.tags)) == 2
    run.bot.state.game_loop = 2
    run.next_observation(3, [FakeActionError(second.tag, AbilityId.EFFECT_STIM, "NotEnoughLife")])
    run.commit_frame(frame=2)
    run.adapter.collect_game_results(3)
    summary = run.adapter.game_result_since(0)
    assert summary["accepted"] is False and summary["rejected_count"] == 1 and summary["accepted_count"] == 1
    assert first.tag != second.tag


def test_orders_are_registered_per_unit_so_unrelated_errors_do_not_leak_between_commands() -> None:
    bot, tank, actions, ability = _siege_bot()
    stranger = bot.add_unit(U.MARINE)
    run = harness.run_with_game(bot, *actions)
    run.commit_frame(frame=2)
    run.next_observation(3, [FakeActionError(stranger.tag, AbilityId.EFFECT_STIM, "NotEnoughLife")])
    run.adapter.collect_game_results(3)
    assert run.adapter.game_result_since(0)["accepted"] is True


# =========================================================================== report + narration


def test_plan_result_audit_carries_the_game_result_per_action_and_is_absent_without_orders() -> None:
    bot, _, actions, _ = _train_marine_bot()
    run = harness.run_with_game(bot, *actions)
    assert _first_game_result(run)["accepted"] is None
    assert all(r["game_result"]["accepted"] is None for r in run.result.audit["action_reports"].values() if "game_result" in r)
    # a refusal before any order was queued has no game_result at all (additive field)
    bot = harness.make_bot()
    bot.add_unit(U.BARRACKS, orders=(AbilityId.BARRACKSTRAIN_MARINE,))
    refused = harness.run_with_game(bot, *lower_direct("train_unit", ["TERRAN_MARINE"]))
    assert "game_results" not in refused.result.audit
    assert all("game_result" not in r for r in refused.result.audit["action_reports"].values())


def test_narration_says_unconfirmed_until_the_game_answers_and_never_says_accepted() -> None:
    bot, _, actions, _ = _train_marine_bot()
    run = harness.run_with_game(bot, *actions)
    text = narrate_sc2_plan_result(run.result).response_text
    assert "아직 확인되지 않았습니다" in text


def test_narrate_game_rejection_is_blocked_vocabulary_with_the_korean_reason() -> None:
    result = ar.game_result_for(error_pb2.ActionResult.Value("NotEnoughMinerals"))
    assert narrate_game_rejection(result) == "게임이 거부함: 미네랄이(가) 부족합니다"
    assert narrate_game_rejection({"code": 77}) == "게임이 거부함: 게임이 주문을 거부했습니다 (코드 77)"


def test_a_rejection_lands_in_the_commander_event_memory_as_a_blocked_event() -> None:
    from starcraft_commander.event_memory import CommanderEventMemory
    from starcraft_commander.live_pipeline import SC2CommandSession

    bot, _, actions, ability = _train_marine_bot()
    run = harness.run_with_game(bot, *actions)
    memory = CommanderEventMemory()
    SC2CommandSession(executor=run.executor, event_memory=memory)
    run.lease(frame=1)
    run.commit_frame(frame=2)
    run.next_observation(3, [FakeActionError(tag, ability, "NotEnoughMinerals") for tag in set(run.tags)])
    run.executor.tick_direct_commands(3)
    events = memory.recent(5)
    assert [e.status for e in events] == ["blocked"]
    assert events[0].narration == "게임이 거부함: 미네랄이(가) 부족합니다"
    assert events[0].detail["game_result"]["accepted"] is False
