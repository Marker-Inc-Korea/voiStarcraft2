"""Behaviour contracts derived from the OFFICIAL SC2 documentation (offline).

Sources (see ``docs/sc2-api-doc-verification.md`` for the row each test backs):
https://blizzard.github.io/s2client-api/ (ActionInterface::UnitCommand, ActionRaw, QueryInterface,
AbilityData, UnitTypeData, Unit, UnitOrder) and Blizzard/s2client-proto (raw.proto
``ActionRawUnitCommand``, query.proto, data.proto, error.proto ``ActionResult``).

Every test drives the product's REAL lowering path (voice payload -> compiler -> router -> executor ->
``PythonSC2BotAdapter`` -> real python-sc2 ``BotAI.do`` -> ``ActionRawUnitCommand`` -> serialised
``RequestAction``) through ``scripts/sc2_accuracy/harness.py``.  This is request-shape evidence only;
nothing here proves a live game accepted an order.  Skips (never fails) without burnysc2/stableid.json.
"""

from __future__ import annotations

import pytest

from starcraft_commander.contracts import SC2ActionType, SC2CommandAction

pytest.importorskip("sc2.ids.ability_id")
pytest.importorskip("s2clientprotocol")

from scripts.sc2_accuracy import harness  # noqa: E402
from scripts.sc2_accuracy.lowering import (  # noqa: E402
    lower_direct,
    lower_emergency,
    lower_micro,
    lower_spoken,
)
from sc2.dicts.unit_abilities import UNIT_ABILITIES  # noqa: E402
from sc2.ids.ability_id import AbilityId  # noqa: E402
from sc2.ids.buff_id import BuffId  # noqa: E402
from sc2.ids.unit_typeid import UnitTypeId as U  # noqa: E402

pytestmark = pytest.mark.skipif(
    not harness.stableid_available(), reason="stableid.json not present on this machine"
)

ENEMY = ((U.ZEALOT, 60.0, 60.0), (U.IMMORTAL, 62.0, 61.0))


# --------------------------------------------------------------------------- helpers


def run(bot, *actions):
    result, views, warns = harness.run_plan(bot, *actions)
    assert warns == [], warns
    return result, views


def report(result, index: int = 0) -> dict:
    return harness.action_reports(result)[index]


def tags_of(views) -> set[int]:
    return {t for v in views for t in v["unit_tags"]}


def with_enemies(bot, enemies=ENEMY):
    for kind, x, y in enemies:
        bot.add_unit(kind, x=x, y=y, enemy=True)
    return bot


def build(subject: str, target: str = "self_main") -> SC2CommandAction:
    return SC2CommandAction(SC2ActionType.BUILD_STRUCTURE, subject, target=target, metadata={"building_type": subject})


# =========================================================================== MICRO
# ---- queueing: ActionInterface::UnitCommand(queued_command=false) default; raw.proto L192 queue_command


@pytest.mark.parametrize(
    "ability,units,enemies",
    [
        ("siege_mode", ((U.SIEGETANK, 2),), ()),
        ("stimpack", ((U.MARINE, 2),), ()),
        ("yamato", ((U.BATTLECRUISER, 1),), ENEMY),
        ("emp", ((U.GHOST, 1),), ENEMY),
    ],
)
def test_a_fresh_voice_command_replaces_orders_and_never_sets_queue_command(ability, units, enemies) -> None:
    bot = with_enemies(harness.make_bot(*units), enemies)
    _, views = run(bot, *lower_micro(ability))
    assert views and all(v["queue"] is False for v in views)


def test_operation_commands_never_queue_either() -> None:
    bot = harness.make_bot((U.MARINE, 3))
    for kind in (SC2ActionType.MOVE_GROUP, SC2ActionType.ATTACK_MOVE, SC2ActionType.SMART, SC2ActionType.PATROL):
        _, views = run(bot, SC2CommandAction(kind, "MARINE", target="enemy_main", count=0))
        assert views and {v["queue"] for v in views} == {False}, kind


def test_spoken_then_phrase_reaches_the_dsl_as_a_visible_rejection_not_a_silent_fresh_order() -> None:
    """Docs: ``queue_command=true`` appends.  The DSL has NO queue field (``realtime_intent._MICRO_KEYS``);
    a payload that tries to carry one must be rejected loudly rather than dropped so that "탱크 시즈 다음에
    스팀팩" can never silently become two *replacing* orders.  (Queue support itself is a handoff item.)"""

    from starcraft_commander.realtime_intent import tool_arguments_to_provider_output

    with pytest.raises(ValueError, match="queue"):
        tool_arguments_to_provider_output({"goal": "x", "micro": [{"ability": "siege_mode", "queue": True}]})


# ---- target kind per ability: AbilityData::Target (data.proto L21-27) vs the ActionRaw target field


def test_every_semantic_ability_sends_the_target_field_its_documented_target_kind_requires() -> None:
    from starcraft_commander.python_sc2_adapter import SC2_SEMANTIC_ABILITIES

    expected_field = {
        "none": None, "enemy_unit": "target_unit_tag", "own_cargo": "target_unit_tag",
        "own_damaged_bio": "target_unit_tag", "enemy_point": "target_world_space_pos",
        "caster_point": "target_world_space_pos", "explicit_point": "target_world_space_pos",
    }
    caster_unit = {
        "ghost_cloak": U.GHOST, "tactical_nuke": U.GHOST, "marine_stimpack": U.MARINE, "marauder_stimpack": U.MARAUDER,
        "stimpack": U.MARINE, "widow_mine_unburrow": U.WIDOWMINEBURROWED, "unsiege": U.SIEGETANKSIEGED,
        "hellion_mode": U.HELLIONTANK, "thor_explosive_mode": U.THORAP, "viking_fighter_mode": U.VIKINGASSAULT,
        "liberator_fighter_mode": U.LIBERATORAG,
    }
    for token, (ability_name, mode, casters) in SC2_SEMANTIC_ABILITIES.items():
        caster = caster_unit.get(token) or getattr(U, casters[0])
        bot = with_enemies(harness.make_bot((caster, 1)))
        if token == "medivac_load":
            bot.add_unit(U.MARINE, x=21.0, y=21.0)
        if token == "medivac_heal":
            bot.add_unit(U.MARINE, x=21.0, y=21.0, health=5.0, health_max=45.0)
        if mode == "own_cargo":
            bot.add_unit(U.MARINE, x=21.0, y=21.0)
        if token == "medivac_unload_all":
            bot = harness.make_bot()
            bot.add_unit(U.MEDIVAC, cargo_taken=2)
        action = SC2CommandAction(
            SC2ActionType.EXECUTE_ABILITY, casters[0], target="enemy_main" if mode == "explicit_point" else "",
            count=0, metadata={"ability": token},
        )
        result, views = run(bot, action)
        assert views, (token, report(result))
        assert {v["target"] for v in views} == {expected_field[mode]}, (token, views)
        assert AbilityId(views[0]["ability_id"]).name == ability_name


# ---- caster eligibility (Unit.health / energy / buffs / unit_type; E: NotEnoughLife, NotEnoughEnergy)


STIM_BUFFS = {"marine": 27, "marauder": 24}


def test_stim_needs_hp_above_its_cost_and_skips_units_that_are_already_stimmed() -> None:
    assert BuffId(STIM_BUFFS["marine"]).name == "STIMPACK" and BuffId(STIM_BUFFS["marauder"]).name == "STIMPACKMARAUDER"
    bot = harness.make_bot()
    low = bot.add_unit(U.MARINE, health=10.0, health_max=45.0)
    ok = bot.add_unit(U.MARINE, health=45.0, health_max=45.0)
    buffed = bot.add_unit(U.MARINE, health=45.0, health_max=45.0, buff_ids=(STIM_BUFFS["marine"],))
    weak_marauder = bot.add_unit(U.MARAUDER, health=20.0, health_max=125.0)
    strong_marauder = bot.add_unit(U.MARAUDER, health=125.0, health_max=125.0)
    stimmed_marauder = bot.add_unit(U.MARAUDER, health=125.0, health_max=125.0, buff_ids=(STIM_BUFFS["marauder"],))
    result, views = run(bot, *lower_micro("stimpack"))
    got = tags_of(views)
    assert got == {ok.tag, strong_marauder.tag}, {
        "sent_to_hp<=cost": got & {low.tag, weak_marauder.tag},
        "sent_to_already_stimmed": got & {buffed.tag, stimmed_marauder.tag},
    }


def test_stim_with_nobody_eligible_is_a_visible_refusal() -> None:
    bot = harness.make_bot()
    bot.add_unit(U.MARINE, health=10.0, health_max=45.0)
    result, views = run(bot, *lower_micro("marine_stimpack"))
    assert views == []
    assert report(result)["applied"] is False and report(result)["detail"]


def test_siege_only_unsieged_tanks_and_unsiege_only_sieged_tanks() -> None:
    bot = harness.make_bot((U.SIEGETANK, 2), (U.SIEGETANKSIEGED, 1))
    plain = {u.tag for u in bot.units if u.type_id == U.SIEGETANK}
    sieged = {u.tag for u in bot.units if u.type_id == U.SIEGETANKSIEGED}
    _, views = run(bot, *lower_micro("siege_mode"))
    assert tags_of(views) == plain and {v["ability_id"] for v in views} == {AbilityId.SIEGEMODE_SIEGEMODE.value}
    _, views = run(bot, *lower_micro("unsiege"))
    assert tags_of(views) == sieged and {v["ability_id"] for v in views} == {AbilityId.UNSIEGE_UNSIEGE.value}


@pytest.mark.parametrize("unit_type,ability", [(U.GHOST, "ghost_cloak"), (U.BANSHEE, "banshee_cloak")])
def test_cloak_is_only_ordered_for_casters_that_have_the_energy(unit_type, ability) -> None:
    bot = harness.make_bot()
    empty = bot.add_unit(unit_type, energy=5.0)
    full = bot.add_unit(unit_type, energy=100.0)
    _, views = run(bot, *lower_micro(ability))
    assert tags_of(views) == {full.tag}, f"energy-5 caster {empty.tag} was ordered"


def test_decloak_needs_no_energy() -> None:
    bot = harness.make_bot()
    drained = bot.add_unit(U.GHOST, energy=0.0)
    _, views = run(bot, *lower_micro("ghost_decloak"))
    assert tags_of(views) == {drained.tag}


def test_tactical_nuke_is_a_ghost_only_point_order_and_refuses_without_a_ghost() -> None:
    bot = with_enemies(harness.make_bot((U.MARINE, 3), (U.SIEGETANK, 1)))
    result, views = run(bot, *lower_micro("tactical_nuke"))
    assert views == [] and report(result)["detail"] == "no_matching_caster"
    bot = with_enemies(harness.make_bot((U.GHOST, 1), (U.MARINE, 3)))
    ghost = next(u for u in bot.units if u.type_id == U.GHOST)
    _, views = run(bot, *lower_micro("tactical_nuke"))
    assert tags_of(views) == {ghost.tag} and {v["target"] for v in views} == {"target_world_space_pos"}


def test_abilities_the_dsl_cannot_express_are_loudly_rejected_not_silently_dropped() -> None:
    """Scanner Sweep (Orbital, 50 energy), Lift/Land, MULE and Supply Drop have no DSL token (handoff)."""

    for token in ("scanner_sweep", "lift", "land", "calldown_mule", "supply_drop"):
        with pytest.raises(ValueError, match="supported abilities"):
            lower_micro(token)


# ---- medivac: LOAD_MEDIVAC (unit), UNLOADALLAT (point), NotEnoughRoomToLoadUnit, NoCargoToUnload


def test_medivac_load_skips_a_full_medivac_and_never_overfills_cargo() -> None:
    bot = harness.make_bot()
    full = bot.add_unit(U.MEDIVAC, cargo_taken=8)
    roomy = bot.add_unit(U.MEDIVAC, cargo_taken=0)
    bot.add_unit(U.MARINE, x=20.5, y=20.0)
    bot.add_unit(U.MARINE, x=20.6, y=20.0)
    _, views = run(bot, *lower_micro("medivac_load"))
    assert full.tag not in tags_of(views), "load ordered on a medivac with zero cargo space left"
    assert roomy.tag in tags_of(views)


def test_medivac_unload_all_needs_cargo() -> None:
    bot = harness.make_bot()
    empty = bot.add_unit(U.MEDIVAC, cargo_taken=0)
    loaded = bot.add_unit(U.MEDIVAC, cargo_taken=4)
    _, views = run(bot, *lower_micro("medivac_unload_all"))
    assert tags_of(views) == {loaded.tag}, f"unload ordered on empty medivac {empty.tag}"
    assert {v["target"] for v in views} == {"target_world_space_pos"}


def test_medivac_heal_targets_a_damaged_biological_unit_never_a_mech_or_a_healthy_one() -> None:
    bot = harness.make_bot((U.MEDIVAC, 1))
    bot.add_unit(U.SIEGETANK, x=20.2, y=20.0, health=10.0, health_max=175.0)  # mechanical: not healable
    bot.add_unit(U.MARINE, x=20.3, y=20.0)  # healthy
    hurt = bot.add_unit(U.MARINE, x=30.0, y=30.0, health=5.0, health_max=45.0)
    _, views = run(bot, *lower_micro("medivac_heal"))
    assert [v["unit_tag"] for v in views] == [hurt.tag]


# ---- ability target unit vs point: refusal is visible, not a wrong-shape send


@pytest.mark.parametrize("ability,caster", [("yamato", U.BATTLECRUISER), ("snipe", U.GHOST), ("lock_on", U.CYCLONE)])
def test_unit_targeted_ability_without_a_target_is_a_visible_refusal(ability, caster) -> None:
    bot = harness.make_bot((caster, 1))
    result, views = run(bot, *lower_micro(ability))
    assert views == []
    assert report(result)["applied"] is False and "target_required" in report(result)["detail"]


# =========================================================================== OPERATION


def test_attack_is_attack_attack_with_a_point_like_tutorial_3_and_smart_is_a_right_click() -> None:
    bot = harness.make_bot((U.MARINE, 4))
    _, views = run(bot, SC2CommandAction(SC2ActionType.ATTACK_MOVE, "MARINE", target="enemy_natural", count=0))
    assert {v["ability_name"] for v in views} <= {"ATTACK_ATTACK", "ATTACK"} and {v["target"] for v in views} == {"target_world_space_pos"}
    _, views = run(bot, SC2CommandAction(SC2ActionType.SMART, "MARINE", target="enemy_natural", count=0))
    assert {v["ability_name"] for v in views} == {"SMART"}


def test_counted_operations_from_one_utterance_order_exactly_those_units_and_no_unit_twice() -> None:
    """"마린 두 기는 정찰, 네 기는 공격": the REAL compile path.  One unit, one operation (a second order to the
    same tag would REPLACE the first: AI ``UnitCommand`` default ``queued_command=false``)."""

    from starcraft_commander.policy_modulation_provider import compile_policy_modulation_provider_output
    from starcraft_commander.realtime_intent import tool_arguments_to_provider_output
    from starcraft_commander.unified_command_router import route_policy_vector

    args = {
        "goal": "scout two, attack four", "command_layer": "operation",
        "operations": [
            {"task_type": "scout_with_units", "composition": [{"unit_type": "TERRAN_MARINE", "count": 2}]},
            {"task_type": "pressure_with_main_army", "composition": [{"unit_type": "TERRAN_MARINE", "count": 4}],
             "route_type": "flank_right"},
        ],
    }
    result = compile_policy_modulation_provider_output(tool_arguments_to_provider_output(args))
    plan = route_policy_vector(result.vector).direct_plan
    bot = harness.make_bot((U.MARINE, 6))
    _, views = run(bot, *plan.ordered_actions)
    flat = [t for v in views for t in v["unit_tags"]]
    assert len(flat) == 6 and len(set(flat)) == 6, views
    by_kind = {}
    for v in views:
        by_kind.setdefault(v["ability_name"], set()).update(v["unit_tags"])
    assert {k: len(t) for k, t in by_kind.items()} == {"MOVE_MOVE": 2, "ATTACK": 4}


def test_repair_needs_a_damaged_target_and_is_a_unit_order() -> None:
    bot = harness.make_bot((U.SCV, 2))
    bot.add_unit(U.BUNKER, x=30.0, y=30.0)  # undamaged
    result, views = run(bot, SC2CommandAction(SC2ActionType.REPAIR, "SCV", target="bunker", count=1))
    assert views == [] and report(result)["detail"] == "no_damaged_repair_target"


# =========================================================================== MACRO: producer selection


def test_train_uses_only_idle_ready_producers_of_the_right_building() -> None:
    bot = harness.make_bot()
    busy = bot.add_unit(U.BARRACKS, orders=(AbilityId.BARRACKSTRAIN_MARINE,))
    building = bot.add_unit(U.BARRACKS, build_progress=0.4)
    idle = bot.add_unit(U.BARRACKS)
    bot.add_unit(U.FACTORY)
    _, views = run(bot, *lower_direct("train_unit", ["TERRAN_MARINE"]))
    assert tags_of(views) == {idle.tag}, (busy.tag, building.tag)


def test_train_with_only_busy_producers_is_a_visible_refusal() -> None:
    bot = harness.make_bot()
    bot.add_unit(U.BARRACKS, orders=(AbilityId.BARRACKSTRAIN_MARINE,))
    result, views = run(bot, *lower_direct("train_unit", ["TERRAN_MARINE"]))
    assert views == [] and report(result)["detail"] == "no_ready_idle_producer"


@pytest.mark.parametrize("unit", ["MARAUDER", "GHOST", "SIEGETANK", "CYCLONE", "THOR", "BANSHEE", "RAVEN", "BATTLECRUISER"])
def test_units_that_need_a_tech_lab_are_only_ordered_from_a_producer_with_one(unit) -> None:
    from sc2.dicts.unit_train_build_abilities import TRAIN_INFO

    producer = next(p for p, table in TRAIN_INFO.items() if getattr(U, unit) in table and table[getattr(U, unit)].get("requires_techlab"))
    lab = {U.BARRACKS: U.BARRACKSTECHLAB, U.FACTORY: U.FACTORYTECHLAB, U.STARPORT: U.STARPORTTECHLAB}[producer]
    bot = harness.make_bot()
    naked = bot.add_unit(producer)
    labbed_lab = bot.add_unit(lab)
    labbed = bot.add_unit(producer, add_on_tag=labbed_lab.tag)
    result, views = run(bot, *lower_direct("train_unit", [f"TERRAN_{unit}"]))
    assert tags_of(views) == {labbed.tag}, f"order sent to tech-lab-less {producer.name} {naked.tag}"
    # and with ONLY a naked producer the user is told why instead of getting a silent order
    bot = harness.make_bot()
    bot.add_unit(producer)
    result, views = run(bot, *lower_direct("train_unit", [f"TERRAN_{unit}"]))
    assert views == [] and report(result)["applied"] is False and report(result)["detail"]


def test_production_queue_is_capped_per_producer_at_five_or_eight_with_reactor() -> None:
    """error.proto ``QueueIsFull``; python-sc2 itself models 1 order per idle building (+1 reactor)."""

    bot = harness.make_bot()
    bot.add_unit(U.BARRACKS)
    act = SC2CommandAction(SC2ActionType.TRAIN_UNIT, "MARINE", count=8, metadata={"producer": "BARRACKS"})
    result, views = run(bot, act)
    assert len(views) <= 5, f"{len(views)} marines queued on ONE barracks"
    rep = report(result)
    assert rep["issued_count"] == len(views) and rep["requested_count"] == 8 and rep["detail"]
    bot = harness.make_bot()
    reactor = bot.add_unit(U.BARRACKSREACTOR)
    bot.add_unit(U.BARRACKS, add_on_tag=reactor.tag)
    _, views = run(bot, act)
    assert len(views) == 8


def test_training_follows_the_remaining_budget_and_reports_a_partial_not_success() -> None:
    bot = harness.make_bot()
    bot.add_unit(U.BARRACKS)
    bot.minerals = 100  # two marines
    act = SC2CommandAction(SC2ActionType.TRAIN_UNIT, "MARINE", count=4, metadata={"producer": "BARRACKS"})
    result, views = run(bot, act)
    assert len(views) == 2
    assert report(result)["issued_count"] == 2 and report(result)["requested_count"] == 4
    assert report(result)["detail"] == "unaffordable"


# ---- gating must be a VISIBLE and CORRECT reason


def test_supply_block_is_reported_as_supply_not_as_missing_minerals() -> None:
    bot = harness.make_bot()
    bot.add_unit(U.BARRACKS)
    bot.supply_left = 0
    result, views = run(bot, *lower_direct("train_unit", ["TERRAN_MARINE"]))
    assert views == []
    assert report(result)["detail"] == "supply_blocked"


def test_a_build_the_player_cannot_afford_is_a_visible_refusal_not_a_silent_skip() -> None:
    bot = harness.make_bot((U.SCV, 2))
    bot.minerals = 0
    result, views = run(bot, build("SUPPLYDEPOT"))
    assert views == []
    assert result.audit["action_reports"] and report(result)["applied"] is False
    assert report(result)["detail"] == "unaffordable"
    assert any(e.exception_type == "ActionRefused" for e in result.errors)


def test_research_is_gated_by_resources() -> None:
    bot = harness.make_bot()
    bot.add_unit(U.BARRACKSTECHLAB)
    bot.minerals = 0
    result, views = run(bot, *lower_direct("research_upgrade", ["STIMPACK"]))
    assert views == [] and report(result)["detail"] == "unaffordable"


def test_addon_order_is_gated_by_resources() -> None:
    bot = harness.make_bot()
    bot.add_unit(U.BARRACKS)
    bot.minerals = 0
    result, views = run(bot, build("BARRACKS_TECHLAB"))
    assert views == [] and report(result)["detail"] == "unaffordable"


def test_placement_rejection_is_surfaced_with_a_reason() -> None:
    import types

    bot = harness.make_bot((U.SCV, 2))

    async def refuse_one(self, *a, **k):
        return False

    async def refuse_many(self, building, positions):
        return [False for _ in positions]

    bot.can_place_single = types.MethodType(refuse_one, bot)
    bot.can_place = types.MethodType(refuse_many, bot)
    result, views = run(bot, build("SUPPLYDEPOT"))
    assert views == [] and "no_safe_placement" in report(result)["detail"]


def test_addon_goes_on_an_idle_parent_and_a_busy_or_unfinished_parent_is_refused() -> None:
    bot = harness.make_bot()
    bot.add_unit(U.BARRACKS, orders=(AbilityId.BARRACKSTRAIN_MARINE,))
    bot.add_unit(U.BARRACKS, build_progress=0.5)
    result, views = run(bot, build("BARRACKS_TECHLAB"))
    assert views == [] and report(result)["detail"].startswith("no_ready_idle_")


def test_addon_is_not_ordered_on_a_barracks_that_already_has_one() -> None:
    """Docs: ``Unit.add_on_tag`` is the attached add-on; one add-on per parent."""

    bot = harness.make_bot()
    lab = bot.add_unit(U.BARRACKSTECHLAB)
    bot.add_unit(U.BARRACKS, add_on_tag=lab.tag)
    result, views = run(bot, build("BARRACKS_REACTOR"))
    assert views == [], "reactor ordered on a barracks that already carries a tech lab"
    assert report(result)["applied"] is False and report(result)["detail"]


# ---- research on the right building


@pytest.mark.parametrize(
    "token,lab,ability",
    [("STIMPACK", U.BARRACKSTECHLAB, "BARRACKSTECHLABRESEARCH_STIMPACK"), ("COMBATSHIELD", U.BARRACKSTECHLAB, "RESEARCH_COMBATSHIELD")],
)
def test_research_goes_to_the_tech_lab_that_is_idle_and_not_to_a_busy_one(token, lab, ability) -> None:
    bot = harness.make_bot()
    bot.add_unit(lab, orders=(AbilityId.BARRACKSTECHLABRESEARCH_STIMPACK,))
    idle = bot.add_unit(lab)
    _, views = run(bot, *lower_direct("research_upgrade", [token]))
    assert tags_of(views) == {idle.tag} and {v["ability_name"] for v in views} == {ability}


# =========================================================================== EMERGENCY


def _unit_names(bot, views) -> set[str]:
    by_tag = {u.tag: u.type_id.name for u in bot.all_own_units}
    return {by_tag[t] for t in tags_of(views)}


def _mixed_army():
    bot = harness.make_bot((U.MARINE, 3), (U.SIEGETANK, 2), (U.MEDIVAC, 1), (U.SCV, 4))
    bot.add_unit(U.MULE)
    return bot


@pytest.mark.parametrize(
    "word,flags,ability,target",
    [
        ("후퇴", {"force_retreat": True}, "MOVE_MOVE", "target_world_space_pos"),
        ("홀드", {"hold_position": True}, "HOLDPOSITION", None),
        ("멈춰", {"cancel_attacks": True}, "STOP", None),
    ],
)
def test_the_three_emergency_words_lower_to_their_sc2_meaning(word, flags, ability, target) -> None:
    """후퇴 = MOVE the army to the home point; 홀드 = HOLDPOSITION; 멈춰 = STOP (all target-kind per AbilityData)."""

    for actions in (lower_spoken(word), lower_emergency(**flags)):
        bot = _mixed_army()
        _, views = run(bot, *actions)
        assert views, word
        assert {v["ability_name"] for v in views} == {ability}, (word, views)
        assert {v["target"] for v in views} == {target}
        assert {v["queue"] for v in views} == {False}, "an emergency order must REPLACE, never queue behind the attack"
        # NB python-sc2 7.3.0 only batches the *generic* MOVE id (3674); the product's MOVE_MOVE (16) therefore leaves as
        # one command per unit.  STOP/HOLDPOSITION/ATTACK batch into one command.  Both are legal per the docs.
        assert tags_of(views) == {u.tag for u in bot.units if u.type_id in {U.MARINE, U.SIEGETANK, U.MEDIVAC}}


@pytest.mark.parametrize("word", ["후퇴", "홀드", "멈춰"])
def test_emergency_words_address_every_combat_unit_but_neither_workers_nor_mules(word) -> None:
    bot = _mixed_army()
    _, views = run(bot, *lower_spoken(word))
    names = _unit_names(bot, views)
    assert names == {"MARINE", "SIEGETANK", "MEDIVAC"}, names


def test_retreat_moves_to_the_home_point_not_to_the_enemy() -> None:
    bot = _mixed_army()
    _, views = run(bot, *lower_spoken("후퇴"))
    assert views[0]["pos"] == (20.0, 20.0)  # semantic ``self_main`` in the harness map


def test_model_path_retreat_stops_first_then_moves_so_the_move_is_the_last_word() -> None:
    """The LLM emits ``force_retreat`` + ``cancel_attacks``.  Both are non-queued orders to the same units in
    one RequestAction; if STOP came after MOVE the army would freeze instead of retreating."""

    bot = _mixed_army()
    _, views = run(bot, *lower_emergency(force_retreat=True, cancel_attacks=True))
    names = [v["ability_name"] for v in views]
    assert names[0] == "STOP" and set(names[1:]) == {"MOVE_MOVE"}, names
    assert all(v["queue"] is False for v in views)


@pytest.mark.parametrize("text", ["후퇴하지 마", "홀드하지 마", "멈추지 마", "멈추지 말고 공격해"])
def test_negation_wins_over_every_emergency_word(text) -> None:
    from starcraft_commander.realtime_intent import match_fast_path

    assert match_fast_path(text) is None


def test_emergency_still_orders_units_that_cannot_move() -> None:
    """Pinned limitation (documented, not a claim of correctness): python-sc2's ability table has no MOVE for a
    sieged tank / burrowed mine / AG liberator, so a retreat batch carries tags the game may refuse.  The live
    checklist (doc section 5) records what SC2 does with them."""

    for unit in (U.SIEGETANKSIEGED, U.WIDOWMINEBURROWED, U.LIBERATORAG):
        assert AbilityId.MOVE_MOVE not in UNIT_ABILITIES[unit]
        assert AbilityId.STOP_STOP in UNIT_ABILITIES[unit] or unit is U.WIDOWMINEBURROWED
