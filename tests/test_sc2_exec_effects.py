"""Effect / behavior / other catalog rows: derived adapter tokens, readiness refusals and the catalog id drift guard.

Request-shape evidence only (python-sc2 + s2client-api data + fake bots); a live SC2 is NOT involved.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("sc2.ids.ability_id")
pytest.importorskip("s2clientprotocol")

from scripts.sc2_accuracy import audit, fixtures_effects, harness  # noqa: E402
from sc2.ids.ability_id import AbilityId  # noqa: E402
from sc2.ids.unit_typeid import UnitTypeId as U  # noqa: E402

from starcraft_commander import sc2_catalog as cat  # noqa: E402
from starcraft_commander import sc2_effects  # noqa: E402
from starcraft_commander.narrator import narrate_sc2_plan_result  # noqa: E402
from starcraft_commander.policy_modulation_provider import compile_policy_modulation_provider_output  # noqa: E402
from starcraft_commander.python_sc2_adapter import SC2_SEMANTIC_ABILITIES  # noqa: E402
from starcraft_commander.unified_command_router import route_policy_vector  # noqa: E402

pytestmark = pytest.mark.skipif(not harness.stableid_available(), reason="stableid.json not present on this machine")

CATEGORIES = ("effect", "behavior", "other")
ROWS = [(e, i) for c in CATEGORIES for e in cat.commands_by_category(c) for i in e.valid_issuers]
ROW_IDS = [f"{e.id}/{i}" for e, i in ROWS]


def lower(entry, issuer, **kwargs):
    payload = cat.direct_task_payload(entry.id, issuer=issuer, **kwargs)
    result = compile_policy_modulation_provider_output(payload)
    assert result.vector is not None, (entry.id, result)
    return tuple(route_policy_vector(result.vector).direct_plan.ordered_actions)


def bot_with(*units):
    bot = harness.make_bot()
    for unit_type, kwargs in units:
        bot.add_unit(unit_type, **kwargs)
    return bot


def run(bot, *actions):
    result, views, warns = harness.run_plan(bot, *actions)
    return result, views, warns, harness.action_reports(result)[0]


def action(subject, token, target=""):
    from starcraft_commander.contracts import SC2ActionType, SC2CommandAction

    return SC2CommandAction(SC2ActionType.EXECUTE_ABILITY, subject, target=target, count=0, metadata={"ability": token})


# --------------------------------------------------------------------------- coverage and derivation


def test_the_three_categories_hold_all_32_entries_and_every_token_is_an_adapter_token() -> None:
    entries = [e for c in CATEGORIES for e in cat.commands_by_category(c)]
    assert len(entries) == 32 and {e.lowering for e in entries} <= {"execute_ability", "repair"}
    for entry in entries:
        if entry.lowering != "execute_ability":
            continue
        for issuer in entry.valid_issuers:
            spec = SC2_SEMANTIC_ABILITIES[entry.token_for(issuer)]
            assert issuer in spec[2], (entry.id, issuer)
            assert spec[1] == entry.target_mode, (entry.id, spec)


def test_derived_tokens_carry_the_catalog_ability_names_not_hand_typed_ones() -> None:
    derived = sc2_effects.effect_abilities(set())
    for token, (ability, mode, casters) in derived.items():
        assert getattr(AbilityId, ability, None) is not None, (token, ability)
    assert SC2_SEMANTIC_ABILITIES["ghost_hold_fire"][0] == "BEHAVIOR_HOLDFIREON_GHOST"
    assert SC2_SEMANTIC_ABILITIES["bunker_salvage"][0] == "EFFECT_SALVAGE"
    assert SC2_SEMANTIC_ABILITIES["command_center_load_all"][2] == ("COMMANDCENTER", "COMMANDCENTERFLYING", "PLANETARYFORTRESS")


# --------------------------------------------------------------------------- one positive per row (energy, unit, point, cargo)


def row_world(entry, issuer, *, energy=None):
    """Valid preconditions for one row: caster (with energy), enemy / ally / point target, cargo, depots, patches."""

    from tests.test_sc2_catalog_exec import world  # the shared catalog-derived builder

    bot, kwargs = world(entry, issuer)
    return bot, kwargs


@pytest.mark.parametrize("entry,issuer", ROWS, ids=ROW_IDS)
def test_row_sends_exactly_one_api_correct_request_to_its_caster(entry, issuer) -> None:
    bot, kwargs = row_world(entry, issuer)
    _, views, warns, report = run(bot, *lower(entry, issuer, **kwargs))
    assert views and warns == [], (entry.id, report)
    assert all(v["unit_tags"] and v["queue"] is False and harness.target_shape_ok(v) for v in views)


# --------------------------------------------------------------------------- energy: documented costs only


ENERGY_ROWS = [(e, i) for e, i in ROWS if e.energy_cost is not None]


@pytest.mark.parametrize("entry,issuer", ENERGY_ROWS, ids=[f"{e.id}/{i}" for e, i in ENERGY_ROWS])
def test_energy_cast_refuses_below_the_documented_cost_and_sends_at_it(entry, issuer) -> None:
    from tests.test_sc2_catalog_exec import world

    bot, kwargs = world(entry, issuer, missing="energy")
    _, views, _, report = run(bot, *lower(entry, issuer, **kwargs))
    assert views == [] and "energy" in report["detail"]
    ready, kwargs = world(entry, issuer)
    for unit in list(ready.units) + list(ready.structures):
        if unit.type_id.name == issuer:
            unit._proto.energy = entry.energy_cost
    _, views, _, _ = run(ready, *lower(entry, issuer, **kwargs))
    assert views, "a caster holding exactly the documented energy must be ordered"


# --------------------------------------------------------------------------- refusals the generic exec test cannot build


def test_hold_fire_pair_targets_the_ghost_only_and_refuses_without_one() -> None:
    for command, ability in (("behavior_holdfireon", "BEHAVIOR_HOLDFIREON_GHOST"), ("behavior_holdfireoff", "BEHAVIOR_HOLDFIREOFF_GHOST")):
        entry = cat.get_command(command)
        bot = bot_with((U.GHOST, {}), (U.MARINE, {}))
        _, views, warns, _ = run(bot, *lower(entry, "GHOST"))
        assert warns == [] and {v["ability_name"] for v in views} == {ability}
        _, views, _, report = run(bot_with((U.MARINE, {})), *lower(entry, "GHOST"))
        assert views == [] and report["detail"] == "no_matching_caster"


def test_load_all_needs_scvs_and_a_command_center_with_room() -> None:
    entry = cat.get_command("loadall")
    cc = (U.COMMANDCENTER, {})
    _, views, _, report = run(bot_with(cc), *lower(entry, "COMMANDCENTER"))
    assert views == [] and report["detail"] == "target_required: no_scv_to_load"
    ok = bot_with(cc, (U.SCV, {}))
    fixtures_effects.complete(ok, entry, "COMMANDCENTER")
    _, views, _, _ = run(ok, *lower(entry, "COMMANDCENTER"))
    assert [v["ability_name"] for v in views] == ["LOADALL_COMMANDCENTER"]
    full = bot_with(cc, (U.SCV, {}))
    fixtures_effects.complete(full, entry, "COMMANDCENTER", "full")
    _, views, _, report = run(full, *lower(entry, "COMMANDCENTER"))
    assert views == [] and report["detail"] == "no_ready_caster: no_cargo_space"


def test_bunker_load_and_unload_respect_observed_cargo() -> None:
    load, unload = cat.get_command("load"), cat.get_command("unloadall")
    bunker = (U.BUNKER, {})
    full = bot_with(bunker, (U.MARINE, {}))
    fixtures_effects.complete(full, load, "BUNKER", "full")
    _, views, _, report = run(full, *lower(load, "BUNKER"))
    assert views == [] and report["detail"] == "no_ready_caster: no_cargo_space"
    empty = bot_with(bunker)
    fixtures_effects.complete(empty, unload, "BUNKER", "cargo")
    _, views, _, report = run(empty, *lower(unload, "BUNKER"))
    assert views == [] and report["detail"] == "no_ready_caster: no_cargo_to_unload"
    loaded = bot_with(bunker)
    fixtures_effects.complete(loaded, unload, "BUNKER")
    _, views, _, _ = run(loaded, *lower(unload, "BUNKER"))
    assert [v["ability_name"] for v in views] == ["UNLOADALL_BUNKER"]


def test_bunker_stim_needs_infantry_inside() -> None:
    entry = cat.get_command("effect_stim")
    empty = bot_with((U.BUNKER, {}))
    fixtures_effects.complete(empty, entry, "BUNKER", "cargo")
    _, views, _, report = run(empty, *lower(entry, "BUNKER"))
    assert views == [] and report["detail"] == "no_ready_caster: empty_transport"


def test_medivac_unload_at_needs_cargo_and_a_point() -> None:
    entry = cat.get_command("unloadallat")
    bot = bot_with((U.MEDIVAC, {}))
    fixtures_effects.complete(bot, entry, "MEDIVAC", "cargo")
    _, views, _, report = run(bot, *lower(entry, "MEDIVAC"))
    assert views == [] and report["detail"] == "no_ready_caster: no_cargo_to_unload"
    bot = bot_with((U.MEDIVAC, {"cargo_taken": 2}))
    _, views, _, _ = run(bot, *lower(entry, "MEDIVAC"))
    assert [v["target"] for v in views] == ["target_world_space_pos"]


def test_nuke_is_refused_with_no_visible_enemy_even_when_a_point_is_named() -> None:
    entry = cat.get_command("effect_nukecalldown")
    _, views, _, report = run(bot_with((U.GHOST, {})), action("GHOST", "tactical_nuke", "enemy_main"))
    assert views == [] and report["detail"] == "target_required: no_enemy_target"
    bot = bot_with((U.GHOST, {}), (U.ZEALOT, {"x": 60.0, "y": 60.0, "enemy": True}))
    _, views, _, _ = run(bot, *lower(entry, "GHOST"))
    assert [v["ability_name"] for v in views] == ["TACNUKESTRIKE_NUKECALLDOWN"]


def test_mule_without_a_mineral_patch_is_refused() -> None:
    entry = cat.get_command("effect_calldownmule")
    bot = bot_with((U.ORBITALCOMMAND, {}))
    _, views, _, report = run(bot, *lower(entry, "ORBITALCOMMAND"))
    assert views == [] and report["detail"] == "target_required: no_mineral_patch"


# --------------------------------------------------------------------------- Korean refusal text, no raw detail


REFUSAL_CASES = [
    ("effect_calldownmule", "ORBITALCOMMAND", lambda: bot_with((U.ORBITALCOMMAND, {}))),
    ("effect_nukecalldown", "GHOST", lambda: bot_with((U.GHOST, {}))),
    ("loadall", "COMMANDCENTER", lambda: bot_with((U.COMMANDCENTER, {}))),
    ("effect_stim", "BUNKER", lambda: fixtures_effects.complete(bot_with((U.BUNKER, {})), cat.get_command("effect_stim"), "BUNKER", "cargo")),
    ("unloadallat", "MEDIVAC", lambda: fixtures_effects.complete(bot_with((U.MEDIVAC, {})), cat.get_command("unloadallat"), "MEDIVAC", "cargo")),
    ("effect_scan", "ORBITALCOMMAND", lambda: bot_with((U.ORBITALCOMMAND, {"energy": 10.0}))),
]


@pytest.mark.parametrize("command_id,issuer,make_bot", REFUSAL_CASES, ids=[c[0] for c in REFUSAL_CASES])
def test_refusals_are_korean_and_leak_no_raw_detail(command_id, issuer, make_bot) -> None:
    entry = cat.get_command(command_id)
    kwargs = {"location": "enemy_main"} if entry.target_mode == "explicit_point" else {}
    result, views, _, report = run(make_bot(), *lower(entry, issuer, **kwargs))
    assert views == []
    text = narrate_sc2_plan_result(result).response_text
    assert re.search("[가-힣]", text) and "세부:" not in text, text


# --------------------------------------------------------------------------- catalog ability id drift guard


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def test_every_catalog_ability_id_resolves_to_the_stableid_name_it_claims() -> None:
    """The member name is ``LINK_BUTTON`` (or the friendly name); stableid.json must name that id the same way.

    This is what caught ``SALVAGEEFFECT_SALVAGE`` (4128), which stableid.json calls ``MothershipCloak``.
    """

    stable = {a["id"]: a for a in audit.load_stableid()["Abilities"]}
    drift = []
    for entry in cat.all_commands():
        for issuer, name in entry.abilities.items():
            member = getattr(AbilityId, name)
            row = stable.get(member.value)
            if row is None:
                drift.append((entry.id, issuer, name, member.value, "id missing in stableid"))
                continue
            member_name = _norm(name)
            friendly = _norm(row.get("friendlyname", ""))
            link = _norm(row["name"])
            ok = friendly == member_name if friendly else (member_name.startswith(link) or link in member_name or member_name in link)
            if not ok:
                drift.append((entry.id, issuer, name, member.value, row["name"]))
    assert drift == []
