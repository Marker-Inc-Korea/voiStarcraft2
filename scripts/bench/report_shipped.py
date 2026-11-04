"""Analysis for the SHIPPED-schema benchmark (BENCH_SCHEMA_MODULE=shipped_schema).

  BENCH_SCHEMA_MODULE=shipped_schema .venv/bin/python scripts/bench/report_shipped.py

Headline policy numbers use REAL ASR text (transcript of the same audio from realtime input transcription,
gpt-4o-mini-transcribe) for every transcript-fed step, the shipped backend handler emulation (web_gui regex override,
classify_tier, negation guards) and the shipped lowering + PolicyModulationVector.from_mapping.
Cascades are composed offline from the paired measured calls of their members (same row, same rep, same audio).
Latency (ms after end of speech): realtime = commit/last-speech-sample -> tool-call args (vad350 includes the 350 ms
server silence window); transcript steps = ASR completion time (measured) + step time.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

os.environ.setdefault("BENCH_SCHEMA_MODULE", "shipped_schema")
from common import RESULTS_DIR, RUNS_DIR, load_corpus  # noqa: E402
from report import P95_BUDGET_S, TIERS, pareto_svg, select, tables_md, top_misses  # noqa: E402
from scoring import aggregate, percentile, score_prediction  # noqa: E402

OUT = RESULTS_DIR
ROWS = load_corpus()
BY_ID = {r["id"]: r for r in ROWS}
RT_VAD = "rt:gpt-realtime:vad350:shipped"


def load_all() -> dict[str, list[dict]]:
    runs: dict[str, list[dict]] = {}
    for d in sorted(RUNS_DIR.iterdir()):
        if not d.is_dir():
            continue
        recs = [json.loads(f.read_text()) for f in d.glob("*.json") if not f.name.startswith("_")]
        if recs:
            runs[recs[0]["config"]] = recs
    return runs


def rescore(runs):
    for name, recs in runs.items():
        for r in recs:
            if name.startswith("rt:"):
                tr = r.get("transcript") or ""
            elif name.startswith("fp:"):
                tr = None  # measure the deterministic path itself, not the surrounding handler
            else:
                tr = r.get("input_text") or ""
            sc = score_prediction(BY_ID[r["id"]], r.get("tool_args"), r.get("error"), transcript=tr)
            r.update({k: v for k, v in sc.items() if k not in ("id", "tier")})
            r["latency_ms"] = r.get("t_tool_call_ms")
    return runs


def index(runs):
    return {n: {(r["id"], r["rep"]): r for r in recs} for n, recs in runs.items()}


def _pseudo(src, name, lat, tool_args, transcript, extra, cost=0.0, tokens=0):
    sc = score_prediction(BY_ID[src["id"]], tool_args, None, transcript=transcript)
    rec = {"id": src["id"], "tier": src["tier"], "rep": src["rep"], "config": name, "text": src.get("text"),
           "tool_args": tool_args, "latency_ms": lat, "cost_units": cost, "tokens_total": tokens, "error": None,
           "chosen": extra, "transcript": transcript}
    rec.update({k: v for k, v in sc.items() if k not in ("id", "tier")})
    return rec


def cascades(runs, idx):
    out: dict[str, list[dict]] = {}
    rt_names = [n for n in runs if n.startswith("rt:")]
    gem_asr = {v: f"tx:gemini-3.5-flash-lite:{v}:asrtxt" for v in ("shipped", "shipped_fewshot")}
    for rt in rt_names:
        variant = rt.split(":")[3]
        for fast_name, fp in (("ext", "fp:ext_rules:asrtxt"), ("shipfast", "fp:shipped_fast:asrtxt")):
            if fp not in idx:
                continue
            name = f"cascade:{fast_name}>{rt[3:]}"
            recs = []
            for key, rr in idx[rt].items():
                fr = idx[fp].get(key)
                if fr is not None and fr.get("tool_args") is not None and rr.get("t_transcript_ms"):
                    lat = rr["t_transcript_ms"] + (fr["latency_ms"] or 0.0)
                    recs.append(_pseudo(rr, name, lat, fr["tool_args"], fr.get("input_text") or "", "fast"))
                else:
                    recs.append({**rr, "config": name, "chosen": "rt"})
            out[name] = recs
        for g in ([gem_asr[variant]] if variant in gem_asr else []):
            if g not in idx:
                continue
            name = f"cascade:veto:{rt[3:]}+gemini:{variant}"
            recs = []
            for key, rr in idx[rt].items():
                gr = idx[g].get(key)
                if gr is None or not rr.get("t_transcript_ms"):
                    # no ASR transcript arrived for this call: nothing to veto with -> realtime answer alone (what the app would do)
                    recs.append({**rr, "config": name, "chosen": "rt_no_asr"})
                    continue
                g_lat = rr["t_transcript_ms"] + (gr["latency_ms"] or 0.0)
                clar = gr.get("tool_args") is not None and gr.get("pred_layer") == "clarification"
                lat = max(rr["latency_ms"] or 0.0, g_lat)
                cost = (rr.get("cost_units") or 0) + (gr.get("cost_units") or 0)
                if clar:
                    recs.append({**gr, "config": name, "latency_ms": lat, "chosen": "veto", "cost_units": cost})
                else:
                    recs.append({**rr, "config": name, "latency_ms": lat, "chosen": "rt", "cost_units": cost})
            out[name] = recs
            for fast_name, fp in (("ext", "fp:ext_rules:asrtxt"), ("shipfast", "fp:shipped_fast:asrtxt")):
                if fp not in idx:
                    continue
                n2 = f"cascade:{fast_name}>veto:{rt[3:]}+gemini:{variant}"
                vk = {(r["id"], r["rep"]): r for r in recs}
                recs2 = []
                for key, vr in vk.items():
                    fr = idx[fp].get(key)
                    rr = idx[rt][key]
                    if fr is not None and fr.get("tool_args") is not None and rr.get("t_transcript_ms"):
                        recs2.append(_pseudo(rr, n2, rr["t_transcript_ms"] + (fr["latency_ms"] or 0.0), fr["tool_args"],
                                             fr.get("input_text") or "", "fast"))
                    else:
                        recs2.append({**vr, "config": n2})
                out[n2] = recs2
    # sol as parallel judge (finalist, 1 rep): ext > sol(ASR)
    for solv in ("shipped", "shipped_fewshot"):
        s = f"tx:gpt-5.6-sol:{solv}:asrtxt"
        if s in idx and "fp:ext_rules:asrtxt" in idx:
            name = f"cascade:ext>sol:{solv}"
            recs = []
            for key, sr in idx[s].items():
                rr = idx[RT_VAD].get(key)
                fr = idx["fp:ext_rules:asrtxt"].get(key)
                if rr is None or not rr.get("t_transcript_ms"):
                    continue
                if fr is not None and fr.get("tool_args") is not None:
                    recs.append(_pseudo(rr, name, rr["t_transcript_ms"], fr["tool_args"], fr.get("input_text") or "", "fast"))
                else:
                    recs.append({**sr, "config": name, "latency_ms": rr["t_transcript_ms"] + (sr["latency_ms"] or 0.0), "chosen": "sol"})
            out[name] = recs
    return out


def summarize(sets: dict[str, list[dict]]) -> dict:
    res = {}
    for name, recs in sets.items():
        if not recs:
            continue
        agg = aggregate(recs)
        ids = {r["id"] for r in recs}
        agg["rows"] = len(ids)
        agg["reps_per_row"] = round(len(recs) / len(ids), 2)
        by_rep = defaultdict(list)
        for r in recs:
            by_rep[r["rep"]].append(bool(r["sem"]))
        accs = [sum(v) / len(v) for v in by_rep.values()]
        agg["rep_acc"] = [round(a, 4) for a in accs]
        pr = defaultdict(list)
        for r in recs:
            pr[r["id"]].append(bool(r["sem"]))
        multi = [v for v in pr.values() if len(v) > 1]
        agg["row_consistency"] = sum(len(set(v)) == 1 for v in multi) / len(multi) if multi else None
        adv = [r for r in recs if r["tier"] == "adversarial"]
        if adv:
            agg["adversarial_executed_rate"] = sum(r.get("pred_layer") not in ("clarification", "none") for r in adv) / len(adv)
        if name.startswith("fp:"):
            ans = [r for r in recs if r.get("tool_args") is not None]
            agg["coverage"] = len(ans) / len(recs)
            agg["answered"] = len(ans)
            agg["precision"] = sum(r["sem"] for r in ans) / len(ans) if ans else None
            agg["tier_pr"] = {}
            for t in TIERS:
                tr_ = [r for r in recs if r["tier"] == t]
                ta = [r for r in tr_ if r.get("tool_args") is not None]
                agg["tier_pr"][t] = {"n": len(tr_), "answered": len(ta), "correct": sum(r["sem"] for r in ta),
                                     "recall": sum(r["sem"] for r in tr_) / len(tr_) if tr_ else None,
                                     "precision": sum(r["sem"] for r in ta) / len(ta) if ta else None}
        res[name] = agg
    return res


def infra_stats(runs):
    rt = [r for n, recs in runs.items() if n.startswith("rt:") for r in recs]
    m429 = sum((r.get("mint_429") or 0) for r in rt)
    calls_with = sum(1 for r in rt if (r.get("mint_429") or 0) > 0)
    mint = [r["mint_ms"] for r in rt if r.get("mint_ms")]
    errs = Counter((r.get("error") or "")[:60] for r in rt if r.get("error"))
    return {"realtime_calls": len(rt), "mint_http_429_total": m429, "calls_that_hit_429": calls_with,
            "mint_p50_ms": percentile(mint, .5), "mint_p95_ms": percentile(mint, .95),
            "auth_modes": dict(Counter(r.get("auth") for r in rt)),
            "latency_includes_secret_minting": False,
            "errors": dict(errs),
            "asr_available_rate": sum(1 for r in rt if r.get("transcript")) / max(1, len(rt))}


POLICY = "cascade:ext>gpt-realtime:vad350:shipped_fewshot"
POLICY_VETO = "cascade:ext>veto:gpt-realtime:vad350:shipped_fewshot+gemini:shipped_fewshot"


def routing_policy(doc: dict) -> dict:
    """Policy = the config chosen by the selection rule over deployable candidates (real ASR text, shipped handler)."""
    cfgs = doc["configs"]
    chosen = doc["selection_all"]["config"] if doc["selection_all"] else POLICY
    pick = lambda n, t: {k: cfgs[n][t][k] for k in ("n", "sem", "sem_ci", "p50_ms", "p95_ms", "schema_valid", "unsafe", "strict")}  # noqa: E731
    tiers = ("all",) + tuple(TIERS)
    return {
        "name": "ext_rules fast path, else shipped-schema Realtime (server_vad 350 ms) with few-shot instructions",
        "chosen_config": chosen,
        "evidence": "160 rows x 3 reps (n=479 calls), shipped realtime_intent schema + web_gui handler emulation, REAL ASR text for the fast path, ephemeral-secret path with 429 backoff (see infra)",
        "selection_rule": "eligible: unsafe<=2% and error<=5% and p95<=tier budget (emergency 3s, micro 5s, others 8s); best = max exact-semantic accuracy; tie band 2pp; pick min p95 then p50. Selection is over deployable pipelines only (realtime audio alone and cascades); transcript-only text models are reported but not selectable because they need an ASR first.",
        "steps": [
            {"step": 1, "id": "deterministic_fast_path", "input": "real ASR transcript (realtime input transcription, gpt-4o-mini-transcribe)",
             "impl": "scripts/bench/fastpath.py:ext_rules (NOT shipped yet; superset of web_gui emergency regex + realtime_intent.match_micro_fast_path with research/expand/production and single-flag emergency)",
             "on_hit": "execute without waiting for the model call; cancel/ignore the realtime response",
             "measured_on_real_asr": {k: cfgs["fp:ext_rules:asrtxt"][k] for k in ("coverage", "precision", "answered", "tier_pr")},
             "measured_on_gold_text": {k: cfgs["fp:ext_rules"][k] for k in ("coverage", "precision", "answered", "tier_pr")}},
            {"step": 2, "id": "realtime_proposer", "model": "gpt-realtime", "session": "realtime_intent._session_body (server_vad 350 ms, transcription on) + build_realtime_instructions() + few-shot examples (shipped_schema.FEWSHOT_EXAMPLES)",
             "tool": "realtime_intent.build_submit_command_tool()", "tool_choice": "required",
             "handler": "web_gui._handle_realtime_intent unchanged (emergency regex override, classify_tier, negation guards, lowering)",
             "measured_alone": pick("rt:gpt-realtime:vad350:shipped_fewshot", "all")},
        ],
        "measured": {t: pick(chosen, t) for t in tiers if t in cfgs[chosen]},
        "slo": {"p50_ms": cfgs[chosen]["all"]["p50_ms"], "p95_ms": cfgs[chosen]["all"]["p95_ms"], "latency_definition": "end of speech (last audio sample sent, so the 350 ms VAD window is included) -> tool-call arguments; fast-path hits = measured ASR completion time + rule time; excludes secret minting"},
        "safety_option": {
            "config": POLICY_VETO, "when": "if adversarial/unsafe execution matters more than +1.2 s p50: add a parallel gemini-3.5-flash-lite text step on the ASR transcript that may only turn an executable answer into a clarification",
            "measured": {t: pick(POLICY_VETO, t) for t in tiers}},
        "not_recommended": {
            "gpt-5.6-sol text": "82.1% real-ASR / 1 rep, 140 rows / p50 10.8 s (+1.5 s ASR)", "realtime shipped prompt as-is": "84.9% (86.6% with ext fast path)",
            "shipped regex alone": "answers 12/160 rows (7.5%), 11 correct", "gemini text alone on the shipped schema": "75.0% gold text / 68.3% real ASR, 14-17% of calls schema-invalid (invents ability names)"},
        "infra": doc["infra"],
        "caveats": ["synthetic TTS speech, author-written 160-row corpus", "ext_rules precision/coverage tuned on this corpus", "cascades composed offline from paired measured calls", "latencies measured on a loaded machine through a shared proxy", "one adversarial injection row (adv-030) executes a retreat in 2/3 reps without the veto step"],
    }


def main():
    runs = rescore(load_all())
    idx = index(runs)
    casc = cascades(runs, idx)
    sets = {**runs, **casc}
    summary = summarize(sets)
    # fast-path precision/recall on gold text vs ASR text
    sel_kwargs = dict(max_unsafe=0.02)
    cand = {n: s for n, s in summary.items() if not n.startswith("fp:")}
    # headline candidates: deployable policies only (ASR text, shipped handler)
    deploy = {n: s for n, s in cand.items() if (n.startswith("rt:") or n.startswith("cascade:")) and "gold" not in n}
    sel_all = select(deploy, p95_budget_s=P95_BUDGET_S["all"])
    sel_unc = select(deploy)
    sel_tier = {t: select(deploy, tier=t, p95_budget_s=P95_BUDGET_S[t]) for t in TIERS}
    doc = {"generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "schema": "shipped (starcraft_commander/realtime_intent.py)",
           "asr": "real: gpt-4o-mini-transcribe via realtime input transcription of the same audio", "corpus_rows": len(ROWS),
           "infra": infra_stats(runs), "selection_all": sel_all, "selection_all_unconstrained": sel_unc,
           "selection_by_tier": sel_tier, "configs": summary}
    OUT.mkdir(parents=True, exist_ok=True)
    doc["routing_policy"] = routing_policy(doc)
    doc["previous_private_schema_gold_transcripts"] = "docs/bench-results/private-schema-gold-transcripts/summary.json (superseded: private schema, gold transcripts, long-lived-key fallback)"
    (OUT / "summary.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    (OUT / "summary_shipped_raw.json").unlink(missing_ok=True)
    (OUT / "tables_shipped.md").write_text(tables_md(summary, "shipped schema, real ASR text"))
    pareto_svg({n: s for n, s in summary.items() if n.startswith(("rt:", "cascade:"))}, OUT / "pareto.svg",
               "Voice intent benchmark - shipped schema, real ASR (160 rows)")
    if sel_all:
        recs = sets[sel_all["config"]]
        miss, n_bad = top_misses(recs, BY_ID, 15)
        lines = [f"# Top {len(miss)} misses for `{sel_all['config']}` ({n_bad} of {len({r['id'] for r in recs})} rows have >=1 miss)", ""]
        for frac, nwrong, rid, r in miss:
            row = BY_ID[rid]
            reps = sum(1 for x in recs if x["id"] == rid)
            lines.append(f"- **{rid}** `{row['text']}` ASR=`{(r.get('transcript') or r.get('input_text') or '')}` ({row['tier']}; wrong {nwrong}/{reps}; "
                         f"pred layer={r.get('pred_layer')}; src={r.get('source')}; {str(r.get('reason', ''))[:90]})\n"
                         f"  gold=`{json.dumps(row['gold'], ensure_ascii=False)[:240]}`\n"
                         f"  pred=`{json.dumps(r.get('tool_args'), ensure_ascii=False)[:320]}`")
        (OUT / "misses_shipped.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"infra": doc["infra"], "selection_all": sel_all}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
