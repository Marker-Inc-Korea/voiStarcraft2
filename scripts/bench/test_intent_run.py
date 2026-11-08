"""Product-output -> command-id mapping used by the baseline runner (needs the shipped schema; runs in-process)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# The runner resolves research commands through python-sc2 data at import time; CI installs without the `sc2` extra.
pytest.importorskip("sc2.dicts.unit_research_abilities")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

import intent_run as R  # noqa: E402


def test_research_tokens_resolve_through_python_sc2_data():
    assert R.RESEARCH_TOKENS["STIMPACK"] == "RESEARCH_STIMPACK"
    assert R.RESEARCH_TOKENS["COMBATSHIELD"] == "RESEARCH_COMBATSHIELD"
    assert R.RESEARCH_TOKENS["BANSHEE_CLOAK"] == "RESEARCH_BANSHEECLOAKINGFIELD"
    assert R.RESEARCH_TOKENS["GHOST_CLOAK"] == "RESEARCH_PERSONALCLOAKING"
    assert R.RESEARCH_TOKENS["YAMATO_CANNON"] == "RESEARCH_BATTLECRUISERWEAPONREFIT"


def test_decide_maps_the_current_product_paths():
    assert R.decide("스팀팩 써", None)["commands"] == ["EFFECT_STIM"]
    assert R.decide("좌표 클릭해", None)["kind"] == "refuse"
    assert R.decide("보병 공격력 올려줘", None)["kind"] == "none"  # deterministic layer abstains: model lane
    ask = R.decide("x", {"command_layer": "macro", "goal": "g", "clarification": "무엇을?"})
    assert ask["kind"] == "ask"


def test_tool_call_to_command_ids():
    unit = R.decide("마린", {"command_layer": "macro", "goal": "g", "macro": {
        "task_type": "sustain_production", "production_targets": ["TERRAN_MARINE"], "standing_order": True}})
    assert unit["commands"] == ["TRAIN_MARINE"], unit
    lab = R.decide("x", {"command_layer": "macro", "goal": "g", "macro": {
        "task_type": "tech_transition", "production_targets": ["BARRACKS_TECHLAB"]}})
    assert lab["commands"] == ["BUILD_TECHLAB@BARRACKS"], lab
    cloak = R.decide("x", {"command_layer": "macro", "goal": "g", "macro": {
        "task_type": "tech_transition", "production_targets": ["BANSHEE_CLOAK"]}})
    assert cloak["commands"] == ["RESEARCH_BANSHEECLOAKINGFIELD"], cloak
