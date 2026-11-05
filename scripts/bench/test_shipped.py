"""Shipped-schema checks (run in subprocesses: the schema module is chosen at import time via BENCH_SCHEMA_MODULE)."""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def _run(code: str) -> str:
    env = {**os.environ, "BENCH_SCHEMA_MODULE": "shipped_schema", "PYTHONPATH": f"{HERE}:{ROOT}"}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    return out.stdout.strip()


def test_every_gold_is_expressible_in_the_shipped_contract():
    out = subprocess.run([sys.executable, str(HERE / "gold_check_shipped.py")], env={**os.environ, "PYTHONPATH": f"{HERE}:{ROOT}"},
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout[-800:]
    assert "PASS (160/160" in out.stdout


def test_handler_emulation_matches_web_gui_semantics():
    out = _run(
        "import shipped_schema as S\n"
        "print(S.handler_outcome('후퇴하지 마', None)['kind'],"
        " S.handler_outcome('후퇴하지 마', {'command_layer':'emergency','goal':'g','emergency':{'force_retreat':True}})['source'],"
        " S.handler_outcome('공격 취소', None)['provider_output']['emergency']['force_retreat'],"
        " S.handler_outcome('후퇴', None)['provider_output']['emergency']['force_retreat'])"
    )
    assert out == "blocked negation_blocked False True"


def test_shipped_scoring_distinguishes_wrong_counts_and_accepts_equivalents():
    out = _run(
        "from common import load_corpus\n"
        "from scoring import score_prediction\n"
        "import shipped_schema as S\n"
        "rows={r['text']:r for r in load_corpus()}\n"
        "r=rows['마린 두 기는 정찰하고 네 기는 오른쪽 길로 공격해']\n"
        "ok={'command_layer':'operation','goal':'g','operations':[{'task_type':'scout_with_units','composition':[{'unit_type':'TERRAN_MARINE','count':2}]},"
        "{'task_type':'pressure_with_main_army','composition':[{'unit_type':'TERRAN_MARINE','count':4}],'route_type':'flank_right'}]}\n"
        "bad=ok|{'operations':[ok['operations'][0],{**ok['operations'][1],'composition':[{'unit_type':'TERRAN_MARINE','count':3}]}]}\n"
        "e=rows['마린 여섯 기로 적 앞마당 공격해']\n"
        "eq={'command_layer':'operation','goal':'g','operations':[{'task_type':'pressure_with_main_army','composition':[{'unit_type':'TERRAN_MARINE','count':6}],'target_type':'enemy_natural'}]}\n"
        "print(score_prediction(r,ok,None,transcript=r['text'])['sem'], score_prediction(r,bad,None,transcript=r['text'])['sem'],"
        " score_prediction(e,eq,None,transcript=e['text'])['sem'])"
    )
    assert out == "True False True"
