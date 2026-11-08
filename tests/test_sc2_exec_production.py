"""Train / build / morph catalog rows (47) through the REAL Direct path, with each row's valid preconditions.

provider payload -> policy compiler -> route_policy_vector -> SC2RuntimeExecutor -> PythonSC2BotAdapter -> python-sc2
``BotAI.do`` -> raw ``ActionRawUnitCommand``.  Offline request-shape evidence only; nothing here proves a live game
accepted an order.  Every row also has a refusal test (precondition removed -> nothing sent, Korean reason).
"""

from __future__ import annotations

import pytest

pytest.importorskip("sc2.ids.ability_id")
pytest.importorskip("s2clientprotocol")

from scripts.sc2_accuracy import audit, fixtures_production as fx, harness  # noqa: E402
from sc2.dicts.generic_redirect_abilities import GENERIC_REDIRECT_ABILITIES  # noqa: E402
from sc2.ids.ability_id import AbilityId  # noqa: E402
from sc2.ids.unit_typeid import UnitTypeId as U  # noqa: E402

from starcraft_commander import sc2_catalog as cat  # noqa: E402
from starcraft_commander.narrator import narrate_sc2_plan_result  # noqa: E402
from starcraft_commander.policy_modulation_provider import compile_policy_modulation_provider_output  # noqa: E402
from starcraft_commander.unified_command_router import route_policy_vector  # noqa: E402

pytestmark = pytest.mark.skipif(not harness.stableid_available(), reason="stableid.json not present on this machine")

STABLE = {a["id"]: a for a in (audit.load_stableid() or {"Abilities": []})["Abilities"]}
ROWS = [e for c in ("train", "build", "morph") for e in cat.commands_by_category(c)]
PAIRS = [(e, i) for e in ROWS for i in e.valid_issuers]
IDS = [f"{e.id}/{i}" for e, i in PAIRS]


def legal_ids(entry) -> set[int]:
    ids = {entry.header_id}
    ids.update(m.value for m, t in GENERIC_REDIRECT_ABILITIES.items() if t.value == entry.header_id)
    ids.update(i for i, a in STABLE.items() if a.get("remapid") == entry.header_id)
    return ids


def lower(entry, issuer, kwargs):
    result = compile_policy_modulation_provider_output(cat.direct_task_payload(entry.id, issuer=issuer, **kwargs))
    assert result.vector is not None, (entry.id, result)
    plan = route_policy_vector(result.vector).direct_plan
    assert plan is not None, entry.id
    return tuple(plan.ordered_actions)


def refusal_variants(entry, issuer) -> list[str]:
    variants = ["issuer"]
    if entry.category == "train":
        _, needs_lab, required = entry.produces[issuer]
        variants += (["techlab"] if needs_lab else []) + (["prerequisite"] if required else [])
    elif entry.category == "build" and issuer == "SCV":
        variants += (["prerequisite"] if entry.produces["SCV"][2] else []) + (["target"] if entry.target_mode == "geyser" else [])
    elif entry.lowering == "build_structure":
        variants += ["addon"] if entry.id in {"build_techlab", "build_reactor"} else []
        variants += ["prerequisite"] if entry.produces and next(iter(entry.produces.values()))[2] else []
    elif entry.target_mode in {"enemy_unit", "enemy_point"}:
        variants.append("target")
    return variants


REFUSALS = [(e, i, v) for e, i in PAIRS for v in refusal_variants(e, i)]


def test_the_47_production_rows_are_all_covered() -> None:
    assert len({e.id for e in ROWS}) == 47 == len(ROWS)
    assert {e.id for e, _ in PAIRS} == {e.id for e in ROWS}


@pytest.mark.parametrize("entry,issuer", PAIRS, ids=IDS)
def test_row_sends_an_api_correct_request_when_its_preconditions_hold(entry, issuer) -> None:
    bot, kwargs = fx.production_world(entry, issuer)
    result, views, warns = harness.run_plan(bot, *lower(entry, issuer, kwargs))
    assert views, (entry.id, harness.action_reports(result))
    assert warns == []
    assert {v["ability_id"] for v in views} <= legal_ids(entry), (entry.id, [v["ability_name"] for v in views])
    assert all(harness.target_shape_ok(v) for v in views)
    assert all(v["unit_tags"] and v["queue"] is False for v in views)
    wanted = {"none": {None}, "point": {"target_world_space_pos"}, "unit": {"target_unit_tag"},
              "point_or_unit": {"target_world_space_pos", "target_unit_tag"}}[entry.target_kind]
    assert {v["target"] for v in views} <= wanted, (entry.id, views)
    if entry.category == "build" and issuer == "SCV":
        assert {t for v in views for t in v["unit_tags"]} <= {u.tag for u in bot.workers}
    else:
        assert {t for v in views for t in v["unit_tags"]} <= {
            u.tag for u in list(bot.units) + list(bot.structures) if u.type_id.name == issuer}


@pytest.mark.parametrize("entry,issuer,variant", REFUSALS, ids=[f"{e.id}/{i}/{v}" for e, i, v in REFUSALS])
def test_row_with_a_missing_precondition_sends_nothing_and_says_why_in_korean(entry, issuer, variant) -> None:
    bot, kwargs = fx.production_world(entry, issuer, missing=variant)
    result, views, _ = harness.run_plan(bot, *lower(entry, issuer, kwargs))
    assert views == [], (entry.id, variant, views)
    report = harness.action_reports(result)[0]
    assert report["applied"] is False and report["detail"], report
    text = narrate_sc2_plan_result(result).response_text
    assert any("\uac00" <= ch <= "\ud7a3" for ch in text) and "세부:" not in text, text


# --------------------------------------------------------------------------- the specific gaps this module closes


def test_hellbat_is_trained_at_a_factory_as_helliontank_with_the_armory() -> None:
    entry = cat.get_command("train_hellbat")
    bot, kwargs = fx.production_world(entry, "FACTORY")
    _, views, _ = harness.run_plan(bot, *lower(entry, "FACTORY", kwargs))
    assert [v["ability_name"] for v in views] == ["TRAIN_HELLBAT"] and views[0]["ability_id"] == 596
    no_armory, kwargs = fx.production_world(entry, "FACTORY", missing="prerequisite")
    result, views, _ = harness.run_plan(no_armory, *lower(entry, "FACTORY", kwargs))
    assert views == [] and harness.action_reports(result)[0]["detail"] == "missing_prerequisite: ARMORY"


def test_missile_turret_and_sensor_tower_compile_and_need_an_engineering_bay() -> None:
    for command_id in ("build_missileturret", "build_sensortower"):
        entry = cat.get_command(command_id)
        bot, kwargs = fx.production_world(entry, "SCV")
        _, views, warns = harness.run_plan(bot, *lower(entry, "SCV", kwargs))
        assert views and warns == [], command_id
        bare, kwargs = fx.production_world(entry, "SCV", missing="prerequisite")
        result, views, _ = harness.run_plan(bare, *lower(entry, "SCV", kwargs))
        assert views == [] and harness.action_reports(result)[0]["detail"] == "missing_prerequisite: ENGINEERINGBAY"


def test_engineering_bay_needs_a_command_center_like_the_game_says() -> None:
    entry = cat.get_command("build_engineeringbay")
    bot, kwargs = fx.production_world(entry, "SCV", missing="prerequisite")
    result, views, _ = harness.run_plan(bot, *lower(entry, "SCV", kwargs))
    assert views == [] and harness.action_reports(result)[0]["detail"] == "missing_prerequisite: COMMANDCENTER"


def test_refinery_targets_the_geyser_unit_and_refuses_without_a_free_one() -> None:
    entry = cat.get_command("build_refinery")
    bot, kwargs = fx.production_world(entry, "SCV")
    _, views, _ = harness.run_plan(bot, *lower(entry, "SCV", kwargs))
    assert views[0]["target"] == "target_unit_tag" and views[0]["ability_id"] == 320
    bare, kwargs = fx.production_world(entry, "SCV", missing="target")
    result, views, _ = harness.run_plan(bare, *lower(entry, "SCV", kwargs))
    assert views == [] and "no_free_geyser" in harness.action_reports(result)[0]["detail"]


@pytest.mark.parametrize("command_id,issuer,ability,ability_id", [
    ("morph_supplydepot_lower", "SUPPLYDEPOT", "MORPH_SUPPLYDEPOT_LOWER", 556),
    ("morph_supplydepot_raise", "SUPPLYDEPOTLOWERED", "MORPH_SUPPLYDEPOT_RAISE", 558),
    ("build_nuke", "GHOSTACADEMY", "BUILD_NUKE", 710),
])
def test_structure_cast_abilities_send_the_header_id_from_the_structure(command_id, issuer, ability, ability_id) -> None:
    entry = cat.get_command(command_id)
    bot, kwargs = fx.production_world(entry, issuer)
    _, views, _ = harness.run_plan(bot, *lower(entry, issuer, kwargs))
    assert [(v["ability_name"], v["ability_id"], v["target"]) for v in views] == [(ability, ability_id, None)] * len(views) and views
    assert AbilityId(ability_id).name == ability and ability_id == entry.header_id


def test_a_busy_ghost_academy_does_not_get_a_second_nuke() -> None:
    entry = cat.get_command("build_nuke")
    bot, kwargs = fx.production_world(entry, "GHOSTACADEMY")
    for structure in bot.structures:
        if structure.type_id == U.GHOSTACADEMY:
            structure._proto.orders.add().ability_id = AbilityId.BUILD_NUKE.value
    result, views, _ = harness.run_plan(bot, *lower(entry, "GHOSTACADEMY", kwargs))
    assert views == [] and "structure_busy" in harness.action_reports(result)[0]["detail"]
