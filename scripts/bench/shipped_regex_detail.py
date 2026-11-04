"""Re-measure the CURRENT shipped deterministic paths on the full corpus (gold text and real-ASR text).
Prints answered/correct/wrong rows. Run: BENCH_SCHEMA_MODULE=shipped_schema .venv/bin/python scripts/bench/shipped_regex_detail.py"""
import json
import os
import sys

os.environ["BENCH_SCHEMA_MODULE"] = "shipped_schema"
from common import RUNS_DIR, load_corpus  # noqa: E402
from scoring import score_prediction  # noqa: E402

rows = {r["id"]: r for r in load_corpus()}
out = {}
for cfg in ("fp_shipped_regex", "fp_shipped_fast", "fp_ext_rules", "fp_shipped_regex_asrtxt", "fp_shipped_fast_asrtxt", "fp_ext_rules_asrtxt"):
    d = RUNS_DIR / cfg
    if not d.exists():
        continue
    seen = {}
    for f in d.glob("*.0.json"):
        r = json.loads(f.read_text())
        seen[r["id"]] = r
    ans = [r for r in seen.values() if r.get("tool_args") is not None]
    ok = 0
    wrong = []
    for r in ans:
        tr = r.get("input_text") if cfg.endswith("asrtxt") else rows[r["id"]]["text"]
        sc = score_prediction(rows[r["id"]], r["tool_args"], None, transcript=None)
        if sc["sem"]:
            ok += 1
        else:
            wrong.append((r["id"], r["text"], tr, json.dumps(r["tool_args"], ensure_ascii=False)[:110], sc["reason"][:40], rows[r["id"]]["gold"]["layer"]))
    out[cfg] = {"rows": len(seen), "answered": len(ans), "correct": ok}
    print(f"{cfg}: rows={len(seen)} answered={len(ans)} correct={ok}")
    for w in wrong:
        print("   WRONG", w)
# emergency-tier recall of the shipped regex (gold text): rows whose gold is emergency
d = RUNS_DIR / "fp_shipped_regex"
if d.exists():
    em = [json.loads(f.read_text()) for f in d.glob("*.0.json")]
    em = [r for r in em if r["tier"] == "emergency"]
    got = [r for r in em if r.get("tool_args") is not None]
    ok = [r for r in got if r["sem"]]
    print(f"shipped_regex emergency tier: {len(em)} rows, answered {len(got)}, correct flag sets {len(ok)}")
