"""Aggregate intent_run.py result files into the variant comparison tables of docs/voice-intent-benchmark.md (R5-F).

Usage: python3 scripts/bench/intent_compare.py [--glob 'intent_baseline_llm_rtx_*'] 
Per (model, variant): reps pooled (accuracy = mean over reps, unsafe = SUM over reps), n per tier, tokens/latency from the
recorded per-call values with the machine load recorded by the run (``meta.loadavg_1_5_15``).
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[2] / "docs" / "bench-results"
TIERS = ["explicit", "intent", "ambiguous", "state", "negative", "all"]


def _fmt(v, digits=3):
    return "n/a" if v is None else f"{v:.{digits}f}"


def load(pattern: str) -> dict[tuple[str, str], list[dict]]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for path in sorted(RESULTS.glob(pattern + ".json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        meta = data["meta"]
        groups[(meta.get("model", "?"), meta.get("variant", "?"))].append(data)
    return groups


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", default="intent_baseline_llm_*")
    args = ap.parse_args()
    groups = load(args.glob)
    print("| model | variant | reps | n/tier | exact | acceptable | wrong-exec | clarif. correct | UNSAFE (sum over reps) | unanswered | tokens/call | lat p50 / p95 ms (load1) |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for (model, variant), runs in sorted(groups.items()):
        agg = [r["aggregate"]["all"] for r in runs]
        n_tier = "/".join(str(runs[0]["aggregate"][t]["n"]) for t in TIERS[:-1])
        mean = lambda key: statistics.mean(a[key] for a in agg if a[key] is not None) if any(a[key] is not None for a in agg) else None
        cost = [r["meta"].get("cost", {}) for r in runs]
        lat50 = [c["latency_p50_ms"] for c in cost if c.get("latency_p50_ms") is not None]
        lat95 = [c["latency_p95_ms"] for c in cost if c.get("latency_p95_ms") is not None]
        tok = [c["tokens_mean"] for c in cost if c.get("tokens_mean") is not None]
        loads = [r["meta"].get("loadavg_1_5_15", [None])[0] for r in runs]
        print(f"| {model} | {variant} | {len(runs)} | {n_tier} | {_fmt(mean('exact_accuracy'))} | {_fmt(mean('acceptable_accuracy'))} | "
              f"{_fmt(mean('wrong_exec_rate'))} | {_fmt(mean('clarification_rate'))} | {sum(a['unsafe_count'] for a in agg)} | "
              f"{sum(a['unanswered'] for a in agg)} | {_fmt(statistics.mean(tok) if tok else None, 0)} | "
              f"{_fmt(statistics.mean(lat50) if lat50 else None, 0)} / {_fmt(statistics.mean(lat95) if lat95 else None, 0)} "
              f"({', '.join(str(l) for l in loads)}) |")
    print()
    print("| model | variant | " + " | ".join(f"{t} acc (exact/acceptable) · unsafe" for t in TIERS[:-1]) + " |")
    print("|---|---|" + "---|" * (len(TIERS) - 1))
    for (model, variant), runs in sorted(groups.items()):
        cells = []
        for t in TIERS[:-1]:
            ag = [r["aggregate"][t] for r in runs]
            ex = [a["exact_accuracy"] for a in ag if a["exact_accuracy"] is not None]
            ac = [a["acceptable_accuracy"] for a in ag if a["acceptable_accuracy"] is not None]
            cl = [a["clarification_rate"] for a in ag if a["clarification_rate"] is not None]
            primary = (f"{_fmt(statistics.mean(ex), 2) if ex else '-'}/{_fmt(statistics.mean(ac), 2) if ac else '-'}"
                       if ac else f"clar {_fmt(statistics.mean(cl), 2) if cl else '-'}")
            cells.append(f"{primary} · {sum(a['unsafe_count'] for a in ag)}")
        print(f"| {model} | {variant} | " + " | ".join(cells) + " |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
