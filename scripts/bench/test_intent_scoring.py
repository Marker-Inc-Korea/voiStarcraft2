"""Scorer for the voice-intent -> concrete command benchmark.

Run: python3 -m pytest scripts/bench/test_intent_scoring.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

import intent_scoring as S  # noqa: E402


def row(expect, acceptable, exact=None, tier="intent", rid="r1"):
    return {"id": rid, "tier": tier, "gold": {"expect": expect, "acceptable": acceptable, "exact": exact}}


def ex(cmd, reason=""):
    return {"kind": "execute", "command": cmd, "reason": reason}


ASK = {"kind": "ask", "command": None, "reason": ""}
NONE = {"kind": "none", "command": None, "reason": ""}
REFUSE = {"kind": "refuse", "command": None, "reason": ""}


def test_exact_and_acceptable_on_execute_rows():
    r = row("execute", ["RESEARCH_TERRANINFANTRYWEAPONS#1"], exact="RESEARCH_TERRANINFANTRYWEAPONS#1")
    ok = S.score_row(r, ex("RESEARCH_TERRANINFANTRYWEAPONS#1"))
    assert ok["exact"] and ok["acceptable"] and not ok["unsafe"] and ok["correct"]
    lvl = S.score_row(r, ex("RESEARCH_TERRANINFANTRYWEAPONS#2"))
    assert not lvl["acceptable"] and not lvl["unsafe"] and lvl["wrong_exec"]
    assert not S.score_row(r, ASK)["correct"]
    assert not S.score_row(r, NONE)["correct"]


def test_acceptable_set_without_unique_exact():
    r = row("execute", ["RESEARCH_TERRANINFANTRYARMOR#1", "RESEARCH_TERRANINFANTRYWEAPONS#2"])
    s = S.score_row(r, ex("RESEARCH_TERRANINFANTRYARMOR#1"))
    assert s["acceptable"] and s["exact"] is None and s["correct"]


def test_casterless_prediction_matches_any_caster_variant_only_when_caster_unspecified():
    anyc = row("execute", ["BUILD_TECHLAB@BARRACKS", "BUILD_TECHLAB@FACTORY"])
    assert S.score_row(anyc, ex("BUILD_TECHLAB"))["acceptable"]
    one = row("execute", ["BUILD_TECHLAB@FACTORY"], exact="BUILD_TECHLAB@FACTORY")
    assert not S.score_row(one, ex("BUILD_TECHLAB"))["acceptable"]
    assert S.score_row(one, ex("BUILD_TECHLAB@FACTORY"))["exact"]


def test_executing_when_the_right_answer_was_to_ask_or_refuse_is_unsafe():
    assert S.score_row(row("ask", [], tier="ambiguous"), ex("RESEARCH_STIMPACK"))["unsafe"]
    assert S.score_row(row("refuse", [], tier="negative"), ex("EFFECT_SCAN"))["unsafe"]
    assert not S.score_row(row("ask", [], tier="ambiguous"), ASK)["unsafe"]
    assert not S.score_row(row("refuse", [], tier="negative"), REFUSE)["unsafe"]
    assert S.score_row(row("refuse", [], tier="negative"), ASK)["correct"]
    assert not S.score_row(row("refuse", [], tier="negative"), NONE)["correct"]


def test_choose_or_ask_requires_a_stated_reason_and_an_acceptable_command():
    r = row("choose_or_ask", ["RESEARCH_TERRANINFANTRYARMOR#1", "RESEARCH_TERRANINFANTRYWEAPONS#1"], tier="ambiguous")
    assert S.score_row(r, ASK)["correct"] and S.score_row(r, ASK)["clarified"]
    reasoned = S.score_row(r, ex("RESEARCH_TERRANINFANTRYARMOR#1", reason="weapons first is standard"))
    assert reasoned["correct"] and not reasoned["unsafe"] and not reasoned["clarified"]
    silent = S.score_row(r, ex("RESEARCH_TERRANINFANTRYARMOR#1"))
    assert silent["unsafe"] and not silent["correct"]
    other = S.score_row(r, ex("RESEARCH_STIMPACK", reason="because"))
    assert other["unsafe"] and not other["correct"]


def test_none_is_never_unsafe_and_never_a_clarification():
    s = S.score_row(row("ask", [], tier="ambiguous"), NONE)
    assert not s["unsafe"] and not s["clarified"] and not s["correct"] and s["unanswered"]


def test_aggregate_reports_metrics_per_tier_and_overall():
    rows = [
        row("execute", ["A"], exact="A", tier="explicit", rid="1"),
        row("execute", ["B"], exact="B", tier="intent", rid="2"),
        row("ask", [], tier="ambiguous", rid="3"),
        row("ask", [], tier="ambiguous", rid="4"),
        row("refuse", [], tier="negative", rid="5"),
    ]
    preds = {"1": ex("A"), "2": ex("X"), "3": ASK, "4": ex("A"), "5": REFUSE}
    agg = S.aggregate(rows, preds)
    assert agg["all"]["n"] == 5
    assert agg["all"]["unsafe_count"] == 1 and agg["all"]["unsafe_rate"] == 0.2
    assert agg["explicit"]["exact_accuracy"] == 1.0
    assert agg["intent"]["exact_accuracy"] == 0.0 and agg["intent"]["wrong_exec_rate"] == 1.0
    assert agg["ambiguous"]["clarification_rate"] == 0.5
    assert agg["all"]["exact_accuracy"] == 0.5  # 2 rows carry an exact gold: 1 hit
    assert agg["negative"]["correct_rate"] == 1.0
    assert agg["all"]["acceptable_accuracy"] == 0.5


def test_latency_fields_are_carried_not_aggregated_into_claims():
    rows = [row("execute", ["A"], exact="A", tier="explicit", rid="1")]
    preds = {"1": {**ex("A"), "latency_ms": 123.0}}
    agg = S.aggregate(rows, preds)
    assert "latency_ms" not in agg["all"]  # recorded per row, deliberately not summarised


def test_missing_prediction_counts_as_unanswered():
    agg = S.aggregate([row("execute", ["A"], exact="A", tier="explicit")], {})
    assert agg["all"]["unanswered"] == 1 and agg["all"]["correct_rate"] == 0.0


def test_by_category_pools_execute_rows_by_api_category():
    rows = [
        row("execute", ["RESEARCH_STIMPACK"], exact="RESEARCH_STIMPACK", rid="1"),
        row("execute", ["RESEARCH_COMBATSHIELD"], exact="RESEARCH_COMBATSHIELD", rid="2"),
        row("execute", ["TRAIN_MARINE"], exact="TRAIN_MARINE", rid="3"),
        row("ask", [], tier="ambiguous", rid="4"),
    ]
    preds = {"1": ex("RESEARCH_STIMPACK"), "2": ASK, "3": ex("TRAIN_MARINE")}
    cats = S.by_category(rows, preds)
    assert cats["RESEARCH"] == {"n": 2, "acceptable": 1, "acceptable_accuracy": 0.5}
    assert cats["TRAIN"]["acceptable_accuracy"] == 1.0 and set(cats) == {"RESEARCH", "TRAIN"}
