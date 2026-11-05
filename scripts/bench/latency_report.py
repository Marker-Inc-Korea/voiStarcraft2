"""Report for the interleaved latency sweep -> docs/bench-results/latency_sweep.{json,md}.

  BENCH_SCHEMA_MODULE=shipped_schema python latency_report.py [--group 'DIR|E0name|E1,E2,...' ...] [--warm runs_shipped/warm_probe_raw.json]

Views (every call is re-scored with the CURRENT product decision layer):
  model        = handler_outcome(transcript, tool_args); latency = t_tool_call_ms (end of speech -> tool-call args complete).
  rule-first   = rule hit (realtime_intent.match_fast_path(transcript) or web_gui._micromachine_request_is_emergency(transcript, None))
                 -> handler_outcome(transcript, None), latency = t_transcript_ms (input transcription completed; the client posts
                 {transcript, fast:true} right then and cancels the response); non-hit -> the model view.
Only (row, rep) pairs completed by EVERY config of a group are used (matched design; incomplete pairs are dropped, never extrapolated).
Paired comparison vs the group's baseline = bootstrap over rows (reps pooled, 2000 resamples, seed 7).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
from collections import defaultdict

os.environ.setdefault("BENCH_SCHEMA_MODULE", "shipped_schema")
import common  # noqa: E402
from common import RESULTS_DIR, load_corpus  # noqa: E402
import shipped_schema as SS  # noqa: E402
from scoring import aggregate, percentile, score_prediction  # noqa: E402
from report_apply import paired_delta  # noqa: E402

ROWS = {r["id"]: r for r in load_corpus()}
TIERS = ["micro", "emergency", "operation", "macro", "adversarial"]
GROUPS_ALL = ["all", *TIERS]
CAP_UNSAFE = 0.02
ACC_FLOOR_PP = -2.0
P95_CAP_MS = 10_000
N_BOOT = 2000
SWEEP_DIR = common.RUNS_DIR / "sweep_20261105"


def short(name: str) -> str:
    return name.split(":", 2)[-1]  # rt:gpt-realtime:vad350:both -> vad350:both


def load_dir(d) -> dict[str, dict]:
    out = {}
    for sub in sorted(d.iterdir()):
        if not sub.is_dir():
            continue
        recs = {}
        for f in sub.glob("*.json"):
            r = json.loads(f.read_text())
            recs[(r["id"], r["rep"])] = r
        if recs:
            out[recs[next(iter(recs))]["config"]] = recs
    return out


def rule_hit(transcript) -> bool:
    import starcraft_commander.realtime_intent as RI
    import starcraft_commander.web_gui as WG

    if not transcript:
        return False
    return RI.match_fast_path(transcript) is not None or bool(WG._micromachine_request_is_emergency(transcript, None))


def score_views(rec) -> tuple[dict, dict, bool]:
    row = ROWS[rec["id"]]
    tr = rec.get("transcript")
    base = {"id": rec["id"], "rep": rec["rep"], "tier": rec["tier"], "tokens_total": rec.get("tokens_total", 0), "cost_units": rec.get("cost_units", 0)}
    sc = score_prediction(row, rec.get("tool_args"), rec.get("error"), transcript=tr or "")
    model = {**base, **{k: v for k, v in sc.items() if k not in ("id", "tier")}, "error": rec.get("error"),
             "latency_ms": rec.get("t_tool_call_ms"), "tool_ms": rec.get("t_tool_call_ms"), "transcript_ms": rec.get("t_transcript_ms")}
    hit = rule_hit(tr) and rec.get("t_transcript_ms") is not None
    if hit:
        sc2 = score_prediction(row, None, None, transcript=tr)
        blended = {**base, **{k: v for k, v in sc2.items() if k not in ("id", "tier")}, "error": None, "latency_ms": rec["t_transcript_ms"]}
    else:
        blended = dict(model)
    return model, blended, hit


def agg(recs, key_latency=None):
    if key_latency:
        recs = [{**r, "latency_ms": r.get(key_latency), "error": None if r.get(key_latency) is not None else "x"} for r in recs]
    return aggregate(recs)


def cell(a: dict, tier: str) -> dict:
    e = a.get(tier)
    if not e:
        return {}
    return {"n": e["n"], "sem": round(e["sem"], 4), "sem_ci95": [round(e["sem_ci"][0], 4), round(e["sem_ci"][1], 4)],
            "unsafe": round(e["unsafe"], 4), "p50_ms": e["p50_ms"], "p95_ms": e["p95_ms"], "mean_tokens": round(e["mean_tokens"], 1),
            "error_rate": round(e["error_rate"], 4)}


def pct(recs, key, q, tiers=None):
    vals = [r[key] for r in recs if r.get(key) is not None and (tiers is None or r["tier"] in tiers)]
    return percentile(vals, q)


def boot_latency_delta(a, b, key, q, tier=None, n_boot=N_BOOT, seed=7):
    """bootstrap over rows of pct_q(b) - pct_q(a) in ms (a=baseline). Records are dicts keyed (id, rep)."""
    ids = sorted({k[0] for k in a if k in b and (tier is None or a[k]["tier"] == tier)})
    if not ids:
        return None
    by = defaultdict(list)
    for k in a:
        if k in b and k[0] in set(ids):
            by[k[0]].append(k)
    rng = random.Random(seed)
    ds = []
    for _ in range(n_boot):
        ks = [k for i in (rng.choice(ids) for _ in ids) for k in by[i]]
        va = [a[k][key] for k in ks if a[k].get(key) is not None]
        vb = [b[k][key] for k in ks if b[k].get(key) is not None]
        if va and vb:
            ds.append(percentile(vb, q) - percentile(va, q))
    ds.sort()
    allk = [k for i in ids for k in by[i]]
    if not ds:
        return None
    pa = percentile([a[k][key] for k in allk if a[k].get(key) is not None], q)
    pb = percentile([b[k][key] for k in allk if b[k].get(key) is not None], q)
    return {"base": round(pa), "exp": round(pb), "delta_ms": round(pb - pa), "ci95_ms": [round(ds[int(.025 * len(ds))]), round(ds[int(.975 * len(ds))])]}


def build_group(d, baseline: str, exps: list[str], invocations: list[dict]):
    raw = load_dir(d)
    names = [baseline, *exps]
    missing = [n for n in names if n not in raw]
    completed_counts = {n: len(raw.get(n, {})) for n in names}
    names = [n for n in names if n in raw]
    common_keys = set.intersection(*(set(raw[n]) for n in names)) if names else set()
    views: dict[str, dict] = {}
    for n in names:
        mv, bv, hits = {}, {}, 0
        for k in common_keys:
            m, b, h = score_views(raw[n][k])
            mv[k], bv[k] = m, b
            hits += h
        views[n] = {"model": mv, "blended": bv, "hits": hits, "raw": {k: raw[n][k] for k in common_keys}}
    out = {"dir": d.name, "baseline": baseline, "configs": {}, "missing_configs": missing, "completed_counts_raw": completed_counts,
           "matched_pairs": len(common_keys), "matched_rows": len({k[0] for k in common_keys}), "reps_in_matched": sorted({k[1] for k in common_keys}),
           "invocations": invocations}
    for n in names:
        v = views[n]
        mrecs, brecs = list(v["model"].values()), list(v["blended"].values())
        a_model, a_blend = agg(mrecs), agg(brecs)
        a_tr = agg(mrecs, "transcript_ms")
        auth = defaultdict(int)
        for r in v["raw"].values():
            auth[r.get("auth") or "?"] += 1
        errs = sum(1 for r in v["raw"].values() if r.get("error"))
        per = {}
        for t in GROUPS_ALL:
            if t not in a_model:
                continue
            per[t] = {"n": a_model[t]["n"],
                      "model": cell(a_model, t), "transcript_p50_ms": a_tr[t]["p50_ms"], "transcript_p95_ms": a_tr[t]["p95_ms"],
                      "rule_first": cell(a_blend, t)}
        out["configs"][n] = {"short": short(n), "n": len(mrecs), "rule_hits": v["hits"], "rule_hit_rate": round(v["hits"] / max(1, len(mrecs)), 3),
                             "auth": dict(auth), "call_errors": errs, "tiers": per,
                             "t_speech_stopped_p50_ms": percentile([r.get("t_speech_stopped_ms") for r in v["raw"].values() if r.get("t_speech_stopped_ms")], .5)}
    # paired vs baseline + verdict
    if baseline in views:
        b = views[baseline]
        for n in names:
            if n == baseline:
                continue
            e = views[n]
            ks = sorted(common_keys)
            pd_model = paired_delta(list(b["model"].values()), list(e["model"].values()), n_boot=N_BOOT)
            pd_blend = paired_delta(list(b["blended"].values()), list(e["blended"].values()), n_boot=N_BOOT)
            lat = {}
            for t, q, key in (("micro", .5, "latency_ms"), ("emergency", .5, "latency_ms"), ("operation", .95, "latency_ms"), ("macro", .95, "latency_ms"),
                              (None, .5, "latency_ms"), (None, .95, "latency_ms")):
                lat[f"{t or 'all'}_p{int(q * 100)}"] = {"model": boot_latency_delta(b["model"], e["model"], key, q, t),
                                                         "rule_first": boot_latency_delta(b["blended"], e["blended"], key, q, t)}
            cfg = out["configs"][n]["tiers"]
            base_cfg = out["configs"][baseline]["tiers"]
            unsafe = cfg["all"]["model"]["unsafe"]
            improves = []
            for t, stat in (("micro", "p50_ms"), ("emergency", "p50_ms"), ("operation", "p95_ms"), ("macro", "p95_ms")):
                if t in cfg and t in base_cfg and cfg[t]["model"][stat] is not None and base_cfg[t]["model"][stat] is not None:
                    new, old = cfg[t]["model"][stat], base_cfg[t]["model"][stat]
                    ok = new < old and (stat == "p50_ms" or new <= P95_CAP_MS)
                    improves.append({"tier": t, "stat": stat, "base": round(old), "exp": round(new), "improves": bool(ok)})
            any_imp = any(i["improves"] for i in improves)
            acc_ok = pd_model["delta_pp"] > ACC_FLOOR_PP
            unsafe_ok = unsafe <= CAP_UNSAFE
            out["configs"][n]["vs_baseline"] = {
                "accuracy_delta_pp_model": pd_model, "accuracy_delta_pp_rule_first": pd_blend, "latency_delta": lat,
                "tier_latency_checks": improves, "unsafe_all": unsafe,
                "criteria": {"accuracy_delta_gt_-2pp": acc_ok, "unsafe_le_2pct": unsafe_ok, "latency_improves": any_imp},
                "verdict": "ADOPT" if (acc_ok and unsafe_ok and any_imp) else "DROP"}
    return out


def fmt_ms(x):
    return "-" if x is None else f"{x / 1000:.2f}"


def md_group(g, title: str) -> str:
    L = [f"### {title}", ""]
    inv = g["invocations"]
    L.append(f"Group dir `{g['dir']}`; baseline `{short(g['baseline'])}`; matched (row, rep) pairs completed by every config: **{g['matched_pairs']}** "
             f"({g['matched_rows']} rows, reps {g['reps_in_matched']}). Raw completed per config: " +
             ", ".join(f"`{short(k)}`={v}" for k, v in g["completed_counts_raw"].items()) + ".")
    for i in inv:
        L.append(f"- invocation {i.get('start')} -> {i.get('end')} ({i.get('elapsed_s')} s, concurrency {i.get('concurrency')}, auth `{i.get('auth')}`): "
                 f"uptime before: `{i.get('uptime_before')}`; after: `{i.get('uptime_after')}`")
    L.append("")
    for view, label in (("model", "Pure-model view (decision on the model tool call; latency = end of speech -> tool-call args)"),
                        ("rule_first", "Rule-first blended view (rule hit -> decision on transcript at t_transcript; else model)")):
        L += [f"#### {label}", "", "| config | tier | n | sem acc [95% Wilson] | unsafe % | p50 s | p95 s | mean tokens |", "|---|---|---|---|---|---|---|---|"]
        for n, c in g["configs"].items():
            for t in GROUPS_ALL:
                if t not in c["tiers"]:
                    continue
                x = c["tiers"][t][view]
                L.append(f"| {c['short']} | {t} | {x['n']} | {100 * x['sem']:.1f} [{100 * x['sem_ci95'][0]:.0f}-{100 * x['sem_ci95'][1]:.0f}] | "
                         f"{100 * x['unsafe']:.1f} | {fmt_ms(x['p50_ms'])} | {fmt_ms(x['p95_ms'])} | {x['mean_tokens']:.0f} |")
        L.append("")
    L += ["#### Transcript-complete latency (t_transcript_ms, end of speech -> input transcription completed)", "",
          "| config | " + " | ".join(f"{t} p50/p95 s" for t in GROUPS_ALL) + " | rule hits |", "|---|" + "---|" * (len(GROUPS_ALL) + 1)]
    for n, c in g["configs"].items():
        L.append(f"| {c['short']} | " + " | ".join(f"{fmt_ms(c['tiers'][t]['transcript_p50_ms'])}/{fmt_ms(c['tiers'][t]['transcript_p95_ms'])}" if t in c["tiers"] else "-" for t in GROUPS_ALL)
                 + f" | {c['rule_hits']}/{c['n']} ({100 * c['rule_hit_rate']:.0f}%) |")
    L.append("")
    L += ["#### Paired comparison vs baseline and verdict", "",
          "Rule: ADOPT iff accuracy delta > -2 pp AND unsafe <= 2 % AND (micro/emergency p50 or operation/macro p95 (<= 10 s) improves, point estimates). "
          "Accuracy/unsafe/latency checks use the pure-model view; deltas are experiment minus baseline, 95% bootstrap CI over rows.", "",
          "| experiment | acc delta pp (model) [CI] | acc delta pp (rule-first) [CI] | unsafe % | micro p50 ms | emerg p50 ms | op p95 ms | macro p95 ms | all p50 ms | all p95 ms | verdict |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for n, c in g["configs"].items():
        v = c.get("vs_baseline")
        if not v:
            continue
        a, a2 = v["accuracy_delta_pp_model"], v["accuracy_delta_pp_rule_first"]

        def d(key):
            x = v["latency_delta"][key]["model"]
            return "-" if not x else f"{x['delta_ms']:+d} [{x['ci95_ms'][0]:+d},{x['ci95_ms'][1]:+d}]"
        L.append(f"| {c['short']} | {a['delta_pp']:+.1f} [{a['ci95_pp'][0]:+.1f},{a['ci95_pp'][1]:+.1f}] | {a2['delta_pp']:+.1f} [{a2['ci95_pp'][0]:+.1f},{a2['ci95_pp'][1]:+.1f}] | "
                 f"{100 * v['unsafe_all']:.1f} | {d('micro_p50')} | {d('emergency_p50')} | {d('operation_p95')} | {d('macro_p95')} | {d('all_p50')} | {d('all_p95')} | **{v['verdict']}** |")
    L.append("")
    return "\n".join(L)


def md_warm(w) -> str:
    cs, s = w["cold_start_summary"], w["summary"]

    def st(x):
        return f"p50 {fmt_ms(x['p50'])} s / p95 {fmt_ms(x['p95'])} s (n={x['n']})"
    L = ["## Cold vs warm probe", "", f"Run {w['start']} -> {w['end']}; uptime before `{w['uptime_before']}`; after `{w['uptime_after']}`. Rows: {', '.join(w['rows'])} (v0 audio).", "",
         "### (a) Cold start components (sequential, product session body)", "",
         f"- POST /realtime/client_secrets: {st(cs['mint_ms'])}; HTTP status counts {cs['mint_http_status']} (non-200 -> that socket used the direct key + session.update)",
         f"- websocket open: {st(cs['ws_open_ms'])}", f"- session.update -> session.updated: {st(cs['session_update_ms'])}", "",
         "### (b) First utterance on a FRESH session vs n-th utterance on ONE warm session (items pruned after each response)", "",
         "| kind | ok/total | t_tool_call p50 / p95 / mean s | t_transcript p50 / p95 s |", "|---|---|---|---|"]
    for k in ("cold_first", "warm"):
        x = s[k]
        t, tr = x["t_tool_call_ms"], x["t_transcript_ms"]
        L.append(f"| {k} | {x['n']}/{x['n_total']} | {fmt_ms(t['p50'])} / {fmt_ms(t['p95'])} / {fmt_ms(t['mean'])} | {fmt_ms(tr['p50'])} / {fmt_ms(tr['p95'])} |")
    p = s["paired_warm_minus_cold_tool_ms"]
    f1 = s["warm_first_vs_rest_tool_ms"]
    L += ["", f"Paired (same row+rep) warm minus cold-first t_tool_call: median {p['median']} ms, mean {p['mean']} ms (n={p['n']}). "
          f"Warm session 1st utterance p50 {fmt_ms(f1['first']['p50'])} s (n={f1['first']['n']}) vs later utterances p50 {fmt_ms(f1['rest']['p50'])} s (n={f1['rest']['n']}).", ""]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", action="append", default=[], help="DIRNAME|BASELINE_CONFIG|EXP1,EXP2 (full config names rt:gpt-realtime:...)")
    ap.add_argument("--warm", default=str(common.RUNS_DIR / "warm_probe_raw.json"))
    ap.add_argument("--out", default=str(RESULTS_DIR / "latency_sweep"))
    a = ap.parse_args()
    groups = []
    for spec in a.group:
        dname, base, exps = spec.split("|")
        inv_file = common.RUNS_DIR / dname / "_sweep_invocations.json"
        invs = json.loads(inv_file.read_text()) if inv_file.exists() else []
        groups.append(build_group(common.RUNS_DIR / dname, base, [e for e in exps.split(",") if e], invs))
    res = {"corpus_rows": len(ROWS), "definition": __doc__, "groups": groups}
    md = ["# Latency sweep (item 4): endpoint / prompt / tool_choice knobs + cold vs warm", ""]
    for i, g in enumerate(groups):
        md.append(md_group(g, f"Group {i + 1}"))
    wp = a.warm
    if os.path.exists(wp):
        w = json.load(open(wp))
        w2 = {k: v for k, v in w.items() if k != "utterances"}
        res["warm_probe"] = w2
        md.append(md_warm(w))
    with open(a.out + ".json", "w") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    with open(a.out + ".md", "w") as f:
        f.write("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
