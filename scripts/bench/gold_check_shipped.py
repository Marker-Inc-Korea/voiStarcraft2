"""Prove the SHIPPED contract (realtime_intent) can express every corpus gold: gold -> shipped tool args ->
tool_arguments_to_provider_output -> PolicyModulationVector, then re-score it with the shipped scorer.
Run: BENCH_SCHEMA_MODULE=shipped_schema .venv/bin/python scripts/bench/gold_check_shipped.py"""
import os
import sys

os.environ["BENCH_SCHEMA_MODULE"] = "shipped_schema"
from common import S, load_corpus  # noqa: E402
from scoring import score_prediction  # noqa: E402


def main() -> int:
    bad = []
    conflicts = []
    rows = load_corpus()
    for row in rows:
        args = S.gold_to_shipped_args(row["gold"], row["text"])
        # transcript used by the handler emulation = the utterance itself (what real ASR would ideally return)
        sc = score_prediction(row, args, None, transcript="")  # expressibility: bypass the transcript-driven handler guards
        if not (sc["schema_valid"] and sc["sem"]):
            bad.append((row["id"], row["text"], sc["pred_layer"], sc["reason"], sc.get("outcome")))
        # handler conflicts: the shipped handler's regex/negation guards override the correct tool call for this transcript
        hc = score_prediction(row, args, None, transcript=row["text"])
        if not hc["sem"]:
            conflicts.append((row["id"], row["text"], hc["pred_layer"], hc.get("source")))
    print(f"rows={len(rows)}")
    for b in bad:
        print("FAIL", b)
    for c in conflicts:
        print("HANDLER-OVERRIDES-CORRECT-CALL", c)
    print("gold_check_shipped:", "FAIL" if bad else "PASS", f"({len(rows) - len(bad)}/{len(rows)} expressible + self-scoring)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
