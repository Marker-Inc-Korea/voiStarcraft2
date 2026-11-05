"""Unit tests for scoring/aggregation/fast paths. Run: .venv/bin/python -m pytest scripts/bench/test_scoring.py -q"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402

from common import S, load_corpus  # noqa: E402
from fastpath import ext_rules, prod_regex  # noqa: E402
from scoring import aggregate, gold_to_args, percentile, score_prediction, wilson  # noqa: E402

ROWS = {r["id"]: r for r in load_corpus()}


def row_by_text(text):
    return next(r for r in ROWS.values() if r["text"] == text)


def test_corpus_size_and_tiers():
    assert len(ROWS) >= 120
    tiers = {r["tier"] for r in ROWS.values()}
    assert tiers == {"micro", "operation", "macro", "emergency", "adversarial"}
    assert sum(r["lang"] != "ko" for r in ROWS.values()) >= 15
    assert len({r["text"] for r in ROWS.values()}) == len(ROWS)


def test_every_gold_scores_itself():
    for row in ROWS.values():
        sc = score_prediction(row, gold_to_args(row["gold"], row["text"]))
        assert sc["sem"] and sc["strict"] and sc["schema_valid"], (row["id"], sc)


def test_parallel_operations_order_independent():
    row = row_by_text("마린 두 기는 정찰하고 네 기는 오른쪽 길로 공격해")
    args = gold_to_args(row["gold"])
    args["operations"].reverse()
    assert score_prediction(row, args)["sem"]


def test_wrong_count_fails():
    row = row_by_text("마린 두 기는 정찰하고 네 기는 오른쪽 길로 공격해")
    args = gold_to_args(row["gold"])
    args["operations"][0]["units"][0]["count"] = 3
    assert not score_prediction(row, args)["sem"]


def test_wrong_route_fails_and_single_op_for_parallel_fails():
    row = row_by_text("마린 두 기는 정찰하고 네 기는 오른쪽 길로 공격해")
    args = gold_to_args(row["gold"])
    args["operations"][1]["route_type"] = "flank_left"
    assert not score_prediction(row, args)["sem"]
    args = gold_to_args(row["gold"])
    args["operations"] = args["operations"][:1]
    assert not score_prediction(row, args)["sem"]


def test_extra_attribute_sem_vs_strict():
    row = row_by_text("공격해")
    args = gold_to_args(row["gold"])
    args["operations"][0]["location"] = "enemy_main"  # invented place
    sc = score_prediction(row, args)
    assert sc["sem"] and not sc["strict"]


def test_negation_executing_retreat_is_unsafe():
    row = row_by_text("후퇴하지 마")
    bad = {"command_layer": "emergency", "goal": "x", "emergency": {"force_retreat": True}}
    sc = score_prediction(row, bad)
    assert sc["schema_valid"] and not sc["safe"] and not sc["sem"]
    ok = {"command_layer": "emergency", "goal": "x", "clarification": {"reason": "negated_or_contradictory"}}
    sc = score_prediction(row, ok)
    assert sc["sem"] and sc["safe"]


def test_raw_control_requires_refusal_not_execution():
    row = row_by_text("x=100 y=50 으로 이동해")
    exec_args = {"command_layer": "operation", "goal": "x",
                 "operations": [{"operation_id": "a", "goal": "g", "task_type": "pressure_with_main_army"}]}
    assert not score_prediction(row, exec_args)["sem"]
    refuse = {"command_layer": "operation", "goal": "x", "clarification": {"reason": "refuse_raw_control"}}
    assert score_prediction(row, refuse)["sem"]
    # wrong clarification reason is not a pass
    assert not score_prediction(row, {"command_layer": "operation", "goal": "x",
                                     "clarification": {"reason": "unsupported"}})["sem"]


def test_schema_invalid_never_correct():
    row = row_by_text("스팀팩 써")
    sc = score_prediction(row, {"command_layer": "micro", "goal": "x", "micro": {"ability": "not_an_ability"}})
    assert not sc["schema_valid"] and not sc["sem"]
    sc = score_prediction(row, None, "timeout")
    assert not sc["sem"] and sc["pred_layer"] == "none"


def test_dsl_rejects_mixed_layers_and_accepts_building_tasks():
    mixed = {"command_layer": "macro", "goal": "g",
             "production": {"task_type": "sustain_production", "targets": ["marine"]},
             "micro": {"ability": "stimpack"}}
    ok, why = S.check_with_dsl(mixed)
    assert not ok and "invalid" in why or "dsl" in why or not ok
    row = row_by_text("앞마당 입구에 벙커 지어")
    assert score_prediction(row, gold_to_args(row["gold"]))["sem"]


def test_aggregate_percentiles_and_wilson():
    recs = [{"tier": "micro", "sem": i % 2 == 0, "strict": False, "layer_ok": True, "schema_valid": True, "safe": True,
             "latency_ms": 100.0 * (i + 1), "tokens_total": 10, "cost_units": 5.0} for i in range(10)]
    agg = aggregate(recs)
    assert agg["all"]["sem"] == 0.5
    assert agg["all"]["p50_ms"] == pytest.approx(550.0)
    assert agg["all"]["p95_ms"] == pytest.approx(955.0)
    lo, hi = wilson(5, 10)
    assert 0.2 < lo < 0.5 < hi < 0.8
    assert percentile([], 0.5) is None


def test_prod_regex_matches_shipped_behaviour():
    assert prod_regex("후퇴")["emergency"] == {"force_retreat": True, "cancel_attacks": True}
    assert prod_regex("후퇴하지 마") is None
    assert prod_regex("마린 후퇴해") is None  # shipped regex is anchored: misses natural phrasing
    assert prod_regex("공격 취소하지 마") is None


def test_ext_rules_conservative():
    assert ext_rules("스팀팩 써")["micro"]["ability"] == "stimpack"
    assert ext_rules("후퇴하지 마") is None
    assert ext_rules("x=100 y=50 으로 이동해") is None
    assert ext_rules("마린 네 기로 공격해") is None
    assert ext_rules("공격 취소")["emergency"] == {"cancel_attacks": True}
    assert ext_rules("탱크 시즈모드 해")["micro"]["ability"] == "siege_mode"
    assert ext_rules("시즈모드 풀어")["micro"]["ability"] == "unsiege"


def test_tool_schema_variants_valid():
    for v in S.VARIANTS:
        tool = S.build_tool(v)
        assert tool["name"] == "submit_command"
        assert tool["parameters"]["properties"]["command_layer"]["enum"] == ["macro", "operation", "micro", "emergency"]
        assert S.build_instructions(v)
    assert len(S.build_instructions("compact")) < len(S.build_instructions("full"))
