"""Rank deterministic-rule families on the RECORDED real-ASR transcripts and compute the rule-first blend.

Data: scripts/bench/runs_shipped/rt_* (every realtime call: real ASR transcript, the model's tool call, t_transcript_ms,
t_tool_call_ms). Nothing here calls the network.

Part 1 (ranking): `candidate_families.match_fast_path` = the frozen 20-family grammar of the previous round. Per family:
answers n, correct c (scored against the corpus gold with the shipped scorer), precision, coverage of its tier's calls and the
latency a rule-first client saves per answer (t_tool_call - t_transcript, i.e. the time the model call took after the
transcript was available; the transcript is the earliest moment any rule can fire).
Part 2 (product): the shipped five-family matcher, same measures, plus the rule-first blend: calls the rule answers use the
rule's outcome at t_transcript, everything else keeps the model call at t_tool_call.

  BENCH_SCHEMA_MODULE=shipped_schema .venv/bin/python scripts/bench/family_ranking.py
"""

from __future__ import annotations

import json
import os
import statistics
import sys
from collections import defaultdict

os.environ.setdefault("BENCH_SCHEMA_MODULE", "shipped_schema")
from common import RESULTS_DIR, RUNS_DIR, load_corpus  # noqa: E402
import candidate_families as CF  # noqa: E402
import scoring  # noqa: E402
import shipped_schema as SS  # noqa: E402
from scoring import aggregate, percentile, score_prediction  # noqa: E402
from starcraft_commander import realtime_intent as RI  # noqa: E402
from starcraft_commander import web_gui as WG  # noqa: E402

ROWS = {r["id"]: r for r in load_corpus()}
TIERS = ["micro", "emergency", "operation", "macro", "adversarial"]
RUN_DIRS = ("rt_gpt-realtime_vad350_both", "rt_gpt-realtime_vad350_shipped", "rt_gpt-realtime_vad350_both_h", "rt_gpt-realtime_vad350_shipped_h")


def load_calls() -> list[dict]:
    calls = []
    for name in RUN_DIRS:
        d = RUNS_DIR / name
        if not d.exists():
            continue
        for f in sorted(d.glob("*.json")):
            if f.name.startswith("_"):
                continue
            r = json.loads(f.read_text())
            if r.get("transcript") and r.get("t_tool_call_ms") and r.get("t_transcript_ms"):
                calls.append(r)
    return calls


def score_outcome(row: dict, outcome: dict) -> dict:
    canon, layer, valid, why, _ = SS.canonicalize(None, "", outcome)
    res = {"sem": False, "safe": True}
    if not valid:
        return res
    gl = scoring._gold_layer(row["gold"])
    res["safe"] = scoring._safe(row["gold"], canon, layer)
    res["sem"] = bool(scoring._sem(row["gold"], canon, False, layer) and res["safe"])
    return res


def candidate_outcome(match) -> dict:
    return {"kind": "executed", "tier": RI.classify_tier(match.arguments), "source": "fast_path", "provider_output": RI.tool_arguments_to_provider_output(match.arguments)}


def product_outcome(transcript: str):
    d = WG._decide_realtime_intent(transcript, None, fast=True)
    if d.kind == "blocked":
        return {"kind": "blocked", "tier": d.tier, "source": d.source}
    if d.kind != "executed":
        return None
    return {"kind": "executed", "tier": d.tier, "source": d.source, "family": d.family, "provider_output": d.provider_output}


def rank(calls: list[dict]) -> list[dict]:
    by_family: dict[str, dict] = defaultdict(lambda: {"n": 0, "ok": 0, "unsafe": 0, "saved": [], "tiers": defaultdict(int), "wrong": set()})
    tier_total = defaultdict(int)
    for r in calls:
        tier_total[r["tier"]] += 1
        m = CF.match_fast_path(r["transcript"])
        if m is None:
            continue
        sc = score_outcome(ROWS[r["id"]], candidate_outcome(m))
        s = by_family[m.family]
        s["n"] += 1
        s["ok"] += sc["sem"]
        s["unsafe"] += not sc["safe"]
        s["tiers"][r["tier"]] += 1
        s["saved"].append(r["t_tool_call_ms"] - r["t_transcript_ms"])
        if not sc["sem"]:
            s["wrong"].add((r["id"], r["transcript"]))
    out = []
    for fam, s in by_family.items():
        tier = fam.split(":")[0]
        denom = tier_total.get(tier, 0) or 1
        out.append({
            "family": fam, "answers": s["n"], "correct": s["ok"], "precision": s["ok"] / s["n"], "unsafe": s["unsafe"],
            "coverage_of_tier_calls": s["n"] / denom, "median_saved_ms": statistics.median(s["saved"]),
            "total_saved_s": sum(s["saved"]) / 1000.0, "wrong": sorted(s["wrong"])[:4],
        })
    out.sort(key=lambda x: (-(x["precision"] == 1.0), -x["total_saved_s"]))
    return out


def blend(calls: list[dict]) -> dict:
    """before = model-only decision (what main did); rule_first = promoted rules answer at transcript time."""
    SS.set_mode("new")
    before, after = [], []
    hits = defaultdict(int)
    for r in calls:
        row = ROWS[r["id"]]
        base = score_prediction(row, r.get("tool_args"), r.get("error"), transcript=r.get("transcript") or "")
        rec = {"id": r["id"], "rep": r["rep"], "tier": r["tier"], **{k: v for k, v in base.items() if k not in ("id", "tier")}, "latency_ms": r["t_tool_call_ms"], "tokens_total": 0}
        before.append(rec)
        out = product_outcome(r["transcript"])
        if out is None:
            after.append(rec)
            continue
        hits[out.get("family", out["source"])] += 1
        sc = score_outcome(row, out) if out["kind"] == "executed" else {"sem": row["gold"]["layer"] == "clarification", "safe": True}
        merged = dict(rec)
        merged.update(sem=sc["sem"], safe=sc["safe"], strict=sc["sem"], latency_ms=r["t_transcript_ms"])
        after.append(merged)
    return {"before": aggregate(before), "rule_first": aggregate(after), "rule_hits": dict(hits)}


def main() -> int:
    calls = load_calls()
    ranking = rank(calls)
    blended = blend(calls)
    result = {"calls": len(calls), "candidate_ranking": ranking, "product_families": sorted(RI.PROMOTED_FAST_PATH_FAMILIES), "blend": blended}
    (RESULTS_DIR / "family_ranking.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    lines = [f"### Candidate families on {len(calls)} recorded real-ASR calls (4 prompt variants x 160 rows x reps)", "",
             "| family | answers | correct | precision | coverage of tier calls | median saved ms | total saved s | in product |", "|---|---|---|---|---|---|---|---|"]
    for r in ranking:
        lines.append(f"| `{r['family']}` | {r['answers']} | {r['correct']} | {100*r['precision']:.0f}% | {100*r['coverage_of_tier_calls']:.1f}% | {r['median_saved_ms']:.0f} | {r['total_saved_s']:.1f} | {'**yes**' if r['family'] in RI.PROMOTED_FAST_PATH_FAMILIES else ''} |")
    lines += ["", "### Rule-first blend of the shipped five families (offline, same recorded calls)", "",
              "| tier | n | model-only acc | rule-first acc | model-only p50 / p95 s | rule-first p50 / p95 s | unsafe before -> after |", "|---|---|---|---|---|---|---|"]
    for t in ["all", *TIERS]:
        b, a = blended["before"].get(t), blended["rule_first"].get(t)
        if not b:
            continue
        lines.append(f"| {t} | {b['n']} | {100*b['sem']:.1f}% | {100*a['sem']:.1f}% | {b['p50_ms']/1000:.2f} / {b['p95_ms']/1000:.2f} | {a['p50_ms']/1000:.2f} / {a['p95_ms']/1000:.2f} | {100*b['unsafe']:.2f}% -> {100*a['unsafe']:.2f}% |")
    lines += ["", "Rule hits by family: " + ", ".join(f"`{k}` {v}" for k, v in sorted(blended["rule_hits"].items()))]
    (RESULTS_DIR / "family_ranking.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
