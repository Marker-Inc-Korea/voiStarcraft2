"""Offline checks for the latency-sweep prompt/tool variants (no network). Runs in a subprocess because the schema module is
selected at import time via BENCH_SCHEMA_MODULE (same pattern as test_shipped.py)."""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def _run(code: str):
    env = {**os.environ, "BENCH_SCHEMA_MODULE": "shipped_schema", "PYTHONPATH": f"{HERE}:{ROOT}"}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    return json.loads(out.stdout.strip().splitlines()[-1])


_WALK = '''
def walk(o, enums, req, path=""):
    if isinstance(o, dict):
        if "enum" in o: enums[path] = list(o["enum"])
        if "required" in o: req[path] = list(o["required"])
        for k, v in o.items(): walk(v, enums, req, path + "/" + k)
    elif isinstance(o, list):
        for i, v in enumerate(o): walk(v, enums, req, path + f"[{i}]")
'''


def test_lean_tools_keep_every_enum_value_and_required_field():
    res = _run(_WALK + '''
import json, shipped_schema as S
base = S.build_tool("both")
out = {}
for v in ("lean1", "lean2", "lean1_tcn"):
    e0, r0, e1, r1 = {}, {}, {}, {}
    walk(base, e0, r0); walk(S.build_tool(v), e1, r1)
    out[v] = [e0 == e1, r0 == r1, len(e0), len(json.dumps(S.build_tool(v))) < len(json.dumps(base)),
              base["parameters"]["properties"].keys() == S.build_tool(v)["parameters"]["properties"].keys()]
print(json.dumps(out))
''')
    for v, (enums_same, req_same, n_enums, smaller, same_props) in res.items():
        assert enums_same and req_same and same_props and n_enums >= 15 and smaller, v


def test_lean_variants_still_lower_every_gold_call():
    res = _run('''
import json, shipped_schema as S
from common import load_corpus
bad = [r["id"] for r in load_corpus() if not S.check_with_dsl(S.gold_to_shipped_args(r["gold"], r["text"]))[0]]
print(json.dumps(bad))
''')
    assert res == []


def test_lean_instructions_are_shorter_than_both():
    res = _run('''
import json, shipped_schema as S
print(json.dumps({v: len(S.build_instructions(v)) for v in ("both", "lean1", "lean2")}))
''')
    assert res["lean2"] < res["lean1"] < res["both"]


def test_tcn_sends_the_named_function_tool_choice_in_both_session_paths():
    res = _run('''
import json, shipped_schema as S
b = S.realtime_session_body("gpt-realtime", "both_tcn", endpoint="vad350")["session"]
p = S.realtime_session_body("gpt-realtime", "both", endpoint="vad350")["session"]
print(json.dumps([b["tool_choice"], p["tool_choice"], S.variant_tool_choice("lean1_tcn")]))
''')
    assert res[0] == {"type": "function", "name": "submit_command"}
    assert res[1] == "required"
    assert res[2] == {"type": "function", "name": "submit_command"}


def test_endpoint_knob_is_parsed_generically():
    res = _run('''
import json, shipped_schema as S
td = lambda e: S.realtime_session_body("gpt-realtime", "both", endpoint=e)["session"]["audio"]["input"]["turn_detection"]
print(json.dumps([td("vad200"), td("vad500")["silence_duration_ms"], td("semhigh"), td("semauto")["eagerness"]]))
''')
    assert res[0]["type"] == "server_vad" and res[0]["silence_duration_ms"] == 200 and res[0]["interrupt_response"] is False
    assert res[1] == 500
    assert res[2] == {"type": "semantic_vad", "eagerness": "high", "create_response": True, "interrupt_response": False}
    assert res[3] == "auto"
