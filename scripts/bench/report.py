"""Analysis: aggregate cached runs, simulate cascades, pick optima, write results.

  report.py --stage screen|full [--out docs/bench-results]

Reads scripts/bench/runs/<config>/*.json. ``screen`` uses rows in subset.json (>=3 reps),
``full`` uses the full corpus. Emits (in docs/bench-results/):
  summary_<stage>.json   all numbers (+ routing_policy when stage == full)
  tables_<stage>.md      markdown tables
  pareto_<stage>.svg     accuracy-vs-p50 plot
  misses_<stage>.md      top-15 failure analysis for the recommended policy

Cascades are SIMULATED offline from the measured per-(row,rep) results of their member
configs (paired), with latencies composed as stated per cascade. They are not separate
API runs; the doc says so.
"""

from __future__ import annotations

import argparse
import re
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from common import RESULTS_DIR, RUNS_DIR, S, load_corpus
from scoring import aggregate, score_prediction, wilson, percentile

TIERS = ["micro", "emergency", "operation", "macro", "adversarial"]
TIER_KO = {"micro": "micro", "emergency": "emergency", "operation": "operation(중간)", "macro": "macro", "adversarial": "adversarial"}


def load_runs(config_dirs=None, ids: set[str] | None = None) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for d in sorted(RUNS_DIR.iterdir()) if RUNS_DIR.exists() else []:
        if not d.is_dir():
            continue
        recs = []
        skipped = None
        for f in d.glob("*.json"):
            if f.name == "_skipped.json":
                skipped = json.loads(f.read_text())["reason"]
                continue
            r = json.loads(f.read_text())
            if ids is None or r["id"] in ids:
                recs.append(r)
        if recs or skipped:
            name = recs[0]["config"] if recs else d.name
            out[name] = recs
            if skipped:
                out.setdefault("_skipped", []).append({"config": name, "reason": skipped})  # type: ignore[arg-type]
    return out


def _lat(rec):
    return rec.get("latency_ms")


def _valid_exec(rec) -> bool:
    return bool(rec.get("schema_valid")) and rec.get("pred_layer") not in ("clarification", "invalid", "none")


def _conf_ok(rec, thr) -> bool:
    a = rec.get("tool_args") or {}
    c = a.get("confidence")
    return c is None or c >= thr


def _merge(base: dict, rec: dict, lat, name: str, extra=None, cost_from=None) -> dict:
    """Build a pseudo-record for a cascade decision from the chosen member record."""
    out = {k: rec[k] for k in ("id", "tier", "sem", "strict", "layer_ok", "schema_valid", "safe", "pred_layer", "reason", "tool_args", "text") if k in rec}
    cf = cost_from or [base]
    out.update(config=name, latency_ms=lat, tokens_total=sum(r.get("tokens_total", 0) or 0 for r in cf),
               cost_units=sum(r.get("cost_units", 0) or 0 for r in cf),
               error=rec.get("error"), rep=rec.get("rep", 0), chosen=extra)
    return out


def build_cascades(runs: dict[str, list[dict]], rows_by_id: dict[str, dict]) -> dict[str, list[dict]]:
    """Return {cascade_name: [records]} built from paired member results."""
    idx = {name: {(r["id"], r["rep"]): r for r in recs} for name, recs in runs.items() if not name.startswith("_")}
    casc: dict[str, list[dict]] = {}

    def fast_hit(fp_name, key, row):
        r = idx.get(fp_name, {}).get((key[0], 0)) or idx.get(fp_name, {}).get((key[0], key[1]))
        if r is None or r.get("tool_args") is None:
            return None
        return r

    rts = [n for n in idx if n.startswith("rt:") or n.startswith("tx:")]

    # C then Realtime: ext_rules first (answered => done; abstain => realtime)
    for fp in ("fp:ext_rules",):
        if fp not in idx:
            continue
        for rt in rts:
            name = f"cascade:{fp[3:]}>{rt[3:]}"
            recs = []
            for key, rr in idx[rt].items():
                hit = fast_hit(fp, key, rows_by_id[key[0]])
                if hit is not None:
                    recs.append(_merge(rr, hit, (hit["latency_ms"] or 0.0), name, "fast"))
                else:
                    recs.append(_merge(rr, rr, (_lat(rr) or 0.0) + 0.1, name, "rt"))
            if recs:
                casc[name] = recs

    # escalate: cheap first; if invalid/clarification/low-confidence/no-call -> run strong (sequential, latencies add)
    pairs = []
    for cheap in rts:
        for strong in rts:
            same_kind_rt = cheap.startswith("rt:") and strong.startswith("rt:") and "mini" in cheap and "mini" not in strong \
                and cheap.split(":")[2:] == strong.split(":")[2:]
            text_pair = cheap.startswith("tx:gemini") and strong.startswith("tx:gpt-5.6-sol")
            rt_text_pair = cheap.startswith("rt:gpt-realtime-2:") and strong.startswith("tx:gpt-5.6-sol")
            gem_rt = cheap.startswith("tx:gemini") and strong.startswith("rt:gpt-realtime-2:")
            if cheap != strong and (same_kind_rt or text_pair or rt_text_pair or gem_rt):
                pairs.append((cheap, strong))
    for cheap, strong in pairs:
        for thr in (0.8,):
            name = f"cascade:esc{thr}:{cheap[3:]}>{strong[3:]}"
            recs = []
            for key, rc in idx[cheap].items():
                rs = idx[strong].get(key) or idx[strong].get((key[0], 0))  # strong may have fewer reps: reuse rep 0
                if rs is None:
                    continue
                accept = _valid_exec(rc) and _conf_ok(rc, thr) and not rc.get("error")
                if accept:
                    recs.append(_merge(rc, rc, _lat(rc), name, "cheap", cost_from=[rc]))
                else:
                    lat = (_lat(rc) or 0.0) + (_lat(rs) or 0.0) if _lat(rs) is not None else None
                    recs.append(_merge(rs, rs, lat, name, "escalated", cost_from=[rc, rs]))
            if recs:
                casc[name] = recs

    # speculative parallel: both run at once; take first schema-valid result (clarification counts as a valid answer
    # only when no executable valid answer arrives within 0 ms; we use first valid of ANY kind except errors)
    for a, b in pairs:
        name = f"cascade:spec:{a[3:]}|{b[3:]}"
        recs = []
        for key, ra in idx[a].items():
            rb = idx[b].get(key) or idx[b].get((key[0], 0))
            if rb is None:
                continue
            cands = [r for r in (ra, rb) if r.get("schema_valid") and not r.get("error") and _lat(r) is not None]
            if cands:
                first = min(cands, key=_lat)
                recs.append(_merge(first, first, _lat(first), name, "first", cost_from=[ra, rb]))
            else:
                recs.append(_merge(rb, rb, None, name, "none", cost_from=[ra, rb]))
        if recs:
            casc[name] = recs
    # veto guard: audio realtime proposes, a cheap text model over the same utterance can only VETO (turn an executable
    # answer into a clarification). Both run in parallel; latency = slower of the two.
    for rt in [n for n in idx if n.startswith("rt:") and not n.endswith(":asr")]:
        for g in [n for n in idx if n.startswith("tx:gemini")]:
            name = f"cascade:veto:{rt[3:]}+{g[3:]}"
            recs = []
            for key, rr in idx[rt].items():
                rg = idx[g].get(key) or idx[g].get((key[0], 0))
                if rg is None:
                    continue
                g_clar = bool(rg.get("schema_valid")) and rg.get("pred_layer") == "clarification"
                lat = max(_lat(rr) or 0.0, _lat(rg) or 0.0)
                recs.append(_merge(rg if g_clar else rr, rg if g_clar else rr, lat, name, "veto" if g_clar else "rt", cost_from=[rr, rg]))
            if recs:
                casc[name] = recs
    # composite: deterministic fast path first; otherwise the veto cascade (realtime proposes, text model may veto)
    for vname in [n for n in list(casc) if n.startswith("cascade:veto:")]:
        if "fp:ext_rules" not in idx:
            break
        name = "cascade:ext_rules>" + vname[len("cascade:"):]
        byk = {(r["id"], r["rep"]): r for r in casc[vname]}
        recs = []
        for key, vr in byk.items():
            hit = fast_hit("fp:ext_rules", key, None)
            if hit is not None:
                recs.append(_merge(hit, hit, (hit["latency_ms"] or 0.0), name, "fast", cost_from=[]))
            else:
                recs.append({**vr, "config": name, "latency_ms": (vr["latency_ms"] or 0.0) + 0.1})
        casc[name] = recs
    return casc


def summarize(runs, cascades, stage_ids=None) -> dict:
    res = {}
    for name, recs in {**{k: v for k, v in runs.items() if not k.startswith("_")}, **cascades}.items():
        if not recs:
            continue
        agg = aggregate(recs)
        reps = Counter(r["id"] for r in recs)
        agg["rows"] = len(reps)
        agg["reps_per_row"] = round(sum(reps.values()) / len(reps), 2)
        # variance: std of per-rep accuracy
        by_rep: dict[int, list[bool]] = defaultdict(list)
        for r in recs:
            by_rep[r.get("rep", 0)].append(bool(r["sem"]))
        accs = [sum(v) / len(v) for v in by_rep.values()]
        agg["rep_acc_std"] = statistics.pstdev(accs) if len(accs) > 1 else None
        # consistency: fraction of rows whose reps agree on sem
        per_row: dict[str, list[bool]] = defaultdict(list)
        for r in recs:
            per_row[r["id"]].append(bool(r["sem"]))
        multi = [v for v in per_row.values() if len(v) > 1]
        agg["row_consistency"] = (sum(len(set(v)) == 1 for v in multi) / len(multi)) if multi else None
        if name.startswith("fp:"):
            ans = [r for r in recs if r.get("tool_args") is not None]
            agg["coverage"] = len(ans) / len(recs)
            agg["precision"] = (sum(r["sem"] for r in ans) / len(ans)) if ans else None
            agg["answered"] = len(ans)
            tier_pr = {}
            for t in TIERS:
                tr = [r for r in recs if r["tier"] == t]
                ta = [r for r in tr if r.get("tool_args") is not None]
                tier_pr[t] = {"recall": sum(r["sem"] for r in tr) / len(tr) if tr else None,
                              "precision": (sum(r["sem"] for r in ta) / len(ta)) if ta else None,
                              "answered": len(ta), "n": len(tr)}
            agg["tier_pr"] = tier_pr
        res[name] = agg
    return res


def fmt_acc(e):
    if not e:
        return "-"
    return f"{100 * e['sem']:.0f}%"


def fmt_lat(e):
    if not e or e.get("p50_ms") is None:
        return "-"
    return f"{e['p50_ms'] / 1000:.1f}/{e['p95_ms'] / 1000:.1f}"


def tables_md(summary: dict, title: str) -> str:
    lines = [f"### {title}", ""]
    lines.append("Cell = exact-semantic accuracy (`sem`) and p50/p95 latency in seconds (end of speech -> tool call). "
                 "n = calls in that cell.\n")
    head = "| pipeline | n | " + " | ".join(TIER_KO[t] for t in TIERS) + " | **all** | schema-valid | unsafe | strict | tok/call |"
    lines += [head, "|" + "---|" * (len(TIERS) + 7)]
    order = sorted(summary, key=lambda n: (-summary[n]["all"]["sem"], summary[n]["all"].get("p50_ms") or 1e9))
    for n in order:
        s = summary[n]
        cells = []
        for t in TIERS:
            e = s.get(t)
            cells.append(f"{fmt_acc(e)} · {fmt_lat(e)}" if e else "-")
        a = s["all"]
        lines.append(f"| `{n}` | {a['n']} | " + " | ".join(cells) +
                     f" | **{100 * a['sem']:.1f}%** [{100 * a['sem_ci'][0]:.0f}–{100 * a['sem_ci'][1]:.0f}] · {fmt_lat(a)} | "
                     f"{100 * a['schema_valid']:.0f}% | {100 * a['unsafe']:.1f}% | {100 * a['strict']:.0f}% | {a['mean_tokens']:.0f} |")
    return "\n".join(lines) + "\n"


def pareto_svg(summary: dict, path: Path, title: str):
    pts = []
    for n, s in summary.items():
        a = s["all"]
        if a.get("p50_ms") is None:
            continue
        pts.append((n, a["p50_ms"] / 1000.0, 100 * a["sem"], a["unsafe"]))
    if not pts:
        return
    W, H, L, R, T, B = 980, 640, 70, 30, 50, 70
    xmax = max(p[1] for p in pts) * 1.05
    xmax = max(xmax, 1.0)
    ymin = max(0.0, min(p[2] for p in pts) - 5)
    ymin = float(int(ymin // 10 * 10))
    ymax = 100.0
    sx = lambda x: L + (W - L - R) * x / xmax  # noqa: E731
    sy = lambda y: H - B - (H - T - B) * (y - ymin) / (ymax - ymin)  # noqa: E731

    def color(n):
        if n.startswith("fp:"):
            return "#7f7f7f"
        if n.startswith("cascade:"):
            return "#d62728"
        if n.startswith("rt:"):
            return "#1f77b4"
        return "#2ca02c"

    frontier = []
    best = -1.0
    for p in sorted(pts, key=lambda p: (p[1], -p[2])):
        if p[2] > best:
            frontier.append(p)
            best = p[2]
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="Helvetica,Arial,sans-serif" font-size="11">',
           f'<rect width="{W}" height="{H}" fill="white"/>',
           f'<text x="{W / 2}" y="24" text-anchor="middle" font-size="16" font-weight="bold">{title}</text>']
    for i in range(0, 11):
        y = ymin + (ymax - ymin) * i / 10
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{sy(y):.1f}" y2="{sy(y):.1f}" stroke="#eee"/>')
        out.append(f'<text x="{L - 8}" y="{sy(y) + 4:.1f}" text-anchor="end">{y:.0f}%</text>')
    step = 1 if xmax <= 10 else 2 if xmax <= 20 else 5
    x = 0.0
    while x <= xmax:
        out.append(f'<line x1="{sx(x):.1f}" x2="{sx(x):.1f}" y1="{T}" y2="{H - B}" stroke="#eee"/>')
        out.append(f'<text x="{sx(x):.1f}" y="{H - B + 16}" text-anchor="middle">{x:g}s</text>')
        x += step
    out.append(f'<line x1="{L}" x2="{W - R}" y1="{H - B}" y2="{H - B}" stroke="#333"/><line x1="{L}" x2="{L}" y1="{T}" y2="{H - B}" stroke="#333"/>')
    out.append(f'<text x="{(L + W - R) / 2}" y="{H - 28}" text-anchor="middle" font-size="13">p50 latency, end of speech → tool call (s)  [lower is better]</text>')
    out.append(f'<text transform="translate(18,{(T + H - B) / 2}) rotate(-90)" text-anchor="middle" font-size="13">exact-semantic accuracy, all tiers (%)  [higher is better]</text>')
    pl = " ".join(f"{sx(p[1]):.1f},{sy(p[2]):.1f}" for p in frontier)
    out.append(f'<polyline points="{pl}" fill="none" stroke="#ff7f0e" stroke-width="2" stroke-dasharray="6 3"/>')
    fnames = {p[0] for p in frontier}
    for n, xv, yv, unsafe in pts:
        r = 6 if n in fnames else 4
        out.append(f'<circle cx="{sx(xv):.1f}" cy="{sy(yv):.1f}" r="{r}" fill="{color(n)}" fill-opacity="0.75" stroke="{"black" if n in fnames else "none"}"><title>{n}: {yv:.1f}% p50 {xv:.2f}s unsafe {100 * unsafe:.1f}%</title></circle>')
        if n in fnames:
            lab = n.replace("cascade:", "c:")
            out.append(f'<text x="{sx(xv) + 8:.1f}" y="{sy(yv) - 6:.1f}" font-size="10">{lab}</text>')
    lx = W - R - 190
    for i, (lab, col) in enumerate([("realtime audio", "#1f77b4"), ("text LLM (transcript)", "#2ca02c"), ("deterministic fast path", "#7f7f7f"), ("cascade (simulated)", "#d62728")]):
        out.append(f'<circle cx="{lx}" cy="{T + 12 + 16 * i}" r="5" fill="{col}"/><text x="{lx + 12}" y="{T + 16 + 16 * i}">{lab}</text>')
    out.append(f'<line x1="{lx - 6}" x2="{lx + 6}" y1="{T + 82}" y2="{T + 82}" stroke="#ff7f0e" stroke-width="2" stroke-dasharray="6 3"/><text x="{lx + 12}" y="{T + 86}">Pareto frontier (labelled)</text>')
    out.append("</svg>")
    path.write_text("\n".join(out), encoding="utf-8")


P95_BUDGET_S = {"emergency": 3.0, "micro": 5.0, "operation": 8.0, "macro": 8.0, "adversarial": 8.0, "all": 8.0}
ASR_MS = 1587.0  # measured p50 of conversation.item.input_audio_transcription.completed after commit (gpt-realtime, 120 calls)


def select(summary: dict, max_unsafe=0.02, tie_pp=0.02, tier=None, p95_budget_s=None, audio_native_only=False):
    """Selection rule (also written in the doc):
    1. eligible: unsafe <= 2% on that slice, error_rate <= 5%, p95 <= tier budget (None = unconstrained).
       audio_native_only: restrict to pipelines that do not depend on a transcript (rt:* and cascades made only of rt:*).
    2. best = max sem among eligible.  tied = eligible with sem >= best - 2pp.
    3. choose min p95 (then p50) among tied.
    """
    key = tier or "all"
    elig = []
    for n, s in summary.items():
        e = s.get(key)
        if not e or e.get("p95_ms") is None or n.startswith("fp:"):
            continue
        if audio_native_only and re.search(r"gemini|gpt-5|claude|ext_rules|asrtxt|^tx:", n):
            continue
        if e["unsafe"] > max_unsafe or e["error_rate"] > 0.05:
            continue
        if p95_budget_s is not None and e["p95_ms"] > p95_budget_s * 1000:
            continue
        elig.append((n, e))
    if not elig:
        return None
    best = max(e["sem"] for _, e in elig)
    tied = [(n, e) for n, e in elig if e["sem"] >= best - tie_pp]
    tied.sort(key=lambda ne: (ne[1]["p95_ms"], ne[1]["p50_ms"]))
    n, e = tied[0]
    return {"config": n, "n": e["n"], "sem": e["sem"], "sem_ci": e["sem_ci"], "p50_ms": e["p50_ms"], "p95_ms": e["p95_ms"],
            "schema_valid": e["schema_valid"], "unsafe": e["unsafe"], "best_sem_in_pool": best,
            "p95_budget_s": p95_budget_s, "tied": [x for x, _ in tied][:6], "eligible": len(elig)}


ROUTING_COMPOSITE = "cascade:ext_rules>veto:gpt-realtime:manual:fewshot+gemini-3.5-flash-lite:fewshot"


def routing_policy(doc: dict) -> dict:
    """Deployable policy built from MEASURED numbers of the composite cascade (no per-tier oracle).
    A router cannot pick a per-tier pipeline before the model has decided the tier, so the per-tier
    `selection_by_tier` entries are reported as an upper bound / diagnostic, not as routes."""
    cfgs = doc["configs"]
    comp = cfgs.get(ROUTING_COMPOSITE)
    fast = cfgs.get("fp:ext_rules")
    rt = cfgs.get("rt:gpt-realtime:manual:fewshot")
    if not (comp and fast and rt):
        return {}
    pick = lambda c, t: {k: c[t][k] for k in ("n", "sem", "sem_ci", "p50_ms", "p95_ms", "schema_valid", "unsafe", "strict")}  # noqa: E731
    return {
        "name": "fastpath-then-realtime-with-text-veto",
        "evidence_stage": "full corpus 160 rows x 3 reps (cascade latencies/accuracy composed offline from measured paired runs)",
        "steps": [
            {"step": 1, "id": "deterministic_fastpath", "impl": "scripts/bench/fastpath.py:ext_rules (conservative: abstains on any negation/raw-control/unit-count/unknown)",
             "runs_on": "transcript (realtime input transcription) OR text", "on_hit": "execute tool args directly (no model call)",
             "measured": {"coverage": fast["coverage"], "precision_on_answered": fast["precision"], "tier_pr": fast["tier_pr"]}},
            {"step": 2, "id": "realtime_audio_proposer", "model": "gpt-realtime", "schema_variant": "fewshot", "endpointing": "manual commit at client-detected end of speech (server_vad500 measured equal accuracy, +~0.6 s p50)",
             "tool_choice": "required", "output_modalities": ["text"], "measured": pick(rt["all"], "all") if False else rt["all"]},
            {"step": 3, "id": "text_veto", "model": "gemini-3.5-flash-lite", "schema_variant": "fewshot", "runs_in_parallel_with": "step 2",
             "rule": "if the text model returns a schema-valid clarification, REPLACE the realtime answer with that clarification (never executes); otherwise keep the realtime answer",
             "why": "adversarial correctness: realtime alone 72%, gemini text alone 94%, veto composite 97%, with 0 unsafe executions in 480 calls"},
        ],
        "tier_guardrails": {
            "emergency": "fast path only when exactly one of force_retreat/cancel_attacks/hold/evacuate is unambiguous; negation or any army verb => abstain; NEVER use the shipped regex output {force_retreat,cancel_attacks} for single-flag requests (measured 0/12 correct flag sets)",
            "operation": "weakest tier (82%): UI MUST show the parsed operations (count, units, route, target) as 'pending/parsed' and allow cancel; do not present as confirmed",
            "adversarial": "any clarification/refusal answer must never reach /api/micromachine/modulate",
        },
        "backend_tolerance": "accept command_layer='clarification' emitted by models when a clarification block is present (6/480 realtime calls did so; strict enum rejects them)",
        "measured_composite": {t: pick(comp[t], t) if False else comp[t] for t in ("all", "micro", "emergency", "operation", "macro", "adversarial")},
        "slo": {"p50_ms": comp["all"]["p50_ms"], "p95_ms": comp["all"]["p95_ms"]},
        "alternatives": {
            "audio_native_only": {"config": doc["selection_all_audio_native"]["config"], "sem": doc["selection_all_audio_native"]["sem"],
                                  "p50_ms": doc["selection_all_audio_native"]["p50_ms"], "p95_ms": doc["selection_all_audio_native"]["p95_ms"]},
            "max_accuracy_unconstrained_latency": {"config": doc["selection_all_unconstrained"]["config"], "sem": doc["selection_all_unconstrained"]["sem"],
                                                   "p50_ms": doc["selection_all_unconstrained"]["p50_ms"], "p95_ms": doc["selection_all_unconstrained"]["p95_ms"]},
        },
        "selection_rule": "eligible: unsafe<=2% and error<=5% and p95<=tier budget (emergency 3s, micro 5s, others 8s); best=max exact-semantic accuracy; tie band 2pp; pick min p95",
        "caveats": [
            "text-based steps were scored on GOLD transcripts (+1.59 s measured ASR p50 added to their latency); real ASR errors lower their accuracy (gemini fewshot 0.87 -> 0.76 on 3x40 screen with recorded ASR text)",
            "corpus is synthetic TTS speech, author-written, 160 rows; ext_rules was written by the corpus author so its coverage/precision is optimistic",
            "latencies measured on a loaded shared machine and shared proxy; use for ranking, not as absolute SLOs",
        ],
    }


def top_misses(recs: list[dict], rows_by_id: dict, k=15):
    by_id: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        by_id[r["id"]].append(r)
    scored = []
    for rid, rs in by_id.items():
        wrong = [r for r in rs if not r["sem"]]
        if wrong:
            scored.append((len(wrong) / len(rs), len(wrong), rid, wrong[0]))
    scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
    return scored[:k], len(scored)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["screen", "full"], required=True)
    ap.add_argument("--out", default=str(RESULTS_DIR))
    ap.add_argument("--finalists", default="", help="comma list of configs to restrict the report to")
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = load_corpus()
    rows_by_id = {r["id"]: r for r in rows}
    ids = None
    if args.stage == "screen":
        from run import SUBSET

        ids = set(json.loads(SUBSET.read_text()))
    runs = load_runs(ids=ids)
    skipped = runs.pop("_skipped", [])
    if args.finalists:
        keep = set(args.finalists.split(","))
        runs = {k: v for k, v in runs.items() if k in keep or k.startswith("fp:")}
    if True:  # always re-score cached tool_args with the current scoring.py (raw responses are the source of truth)
        for recs in runs.values():
            for r in recs:
                sc = score_prediction(rows_by_id[r["id"]], r.get("tool_args"), r.get("error"))
                r.update({k: v for k, v in sc.items() if k not in ("id", "tier")})
    if args.stage == "full":  # configs that have too few rows are screening-only
        runs = {k: v for k, v in runs.items() if len({r["id"] for r in v}) >= len(rows) * 0.95}
    # Transcript-based pipelines (tx:*, fp:*) were fed the gold text. In a real voice app the transcript arrives
    # ~ASR_MS after end of speech, so add it to their latency (accuracy stays optimistic: gold text, no ASR errors).
    raw_lat = {}
    for name, recs in runs.items():
        if name.startswith(("tx:", "fp:")) and not name.endswith(":asrtxt"):
            for r in recs:
                if r.get("latency_ms") is not None:
                    raw_lat.setdefault(name, []).append(r["latency_ms"])
                    r["latency_ms"] = r["latency_ms"] + ASR_MS
        elif name.endswith(":asrtxt"):
            for r in recs:
                if r.get("latency_ms") is not None:
                    r["latency_ms"] = r["latency_ms"] + ASR_MS
    cascades = build_cascades(runs, rows_by_id)
    summary = summarize(runs, cascades)
    # latency semantic note: fast-path-only entries have no all-tier coverage
    sel_all = select(summary, p95_budget_s=P95_BUDGET_S["all"])
    doc = {"stage": args.stage, "n_rows": len(ids) if ids else len(rows), "asr_ms_added_to_transcript_pipelines": ASR_MS,
           "configs": summary, "skipped": skipped,
           "selection_all": sel_all,
           "selection_all_unconstrained": select(summary),
           "selection_all_audio_native": select(summary, p95_budget_s=P95_BUDGET_S["all"], audio_native_only=True),
           "selection_by_tier": {t: select(summary, tier=t, p95_budget_s=P95_BUDGET_S[t]) for t in TIERS},
           "selection_by_tier_unconstrained": {t: select(summary, tier=t) for t in TIERS},
           "selection_by_tier_audio_native": {t: select(summary, tier=t, p95_budget_s=P95_BUDGET_S[t], audio_native_only=True) for t in TIERS}}
    (out / f"summary_{args.stage}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    (out / f"tables_{args.stage}.md").write_text(tables_md(summary, f"{args.stage} ({doc['n_rows']} rows)"))
    pareto_svg(summary, out / f"pareto_{args.stage}.svg", f"Voice intent benchmark ({args.stage}: {doc['n_rows']} rows)")
    if args.stage == "full":
        rp = routing_policy(doc)
        final = {"generated_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(timespec="seconds"),
                 "corpus_rows": len(rows), "routing_policy": rp, "selection_all": doc["selection_all"],
                 "selection_all_audio_native": doc["selection_all_audio_native"],
                 "selection_all_unconstrained": doc["selection_all_unconstrained"],
                 "selection_by_tier": doc["selection_by_tier"], "selection_by_tier_audio_native": doc["selection_by_tier_audio_native"],
                 "selection_by_tier_unconstrained": doc["selection_by_tier_unconstrained"],
                 "skipped": doc["skipped"], "configs": doc["configs"]}
        (out / "summary.json").write_text(json.dumps(final, ensure_ascii=False, indent=1))
        (out / "pareto.svg").write_text((out / "pareto_full.svg").read_text())
    targets = {}
    if sel_all:
        targets["selected"] = sel_all["config"]
    an = doc["selection_all_audio_native"]
    if an:
        targets["audio_native"] = an["config"]
    for label, best_name in targets.items():
        recs = cascades.get(best_name) or runs.get(best_name, [])
        miss, n_bad = top_misses(recs, rows_by_id, 15)
        lines = [f"# Top {len(miss)} misses for `{best_name}` ({label}; {n_bad} of {len({r['id'] for r in recs})} rows have >=1 miss)", ""]
        for frac, nwrong, rid, r in miss:
            row = rows_by_id[rid]
            reps = sum(1 for x in recs if x["id"] == rid)
            lines.append(f"- **{rid}** `{row['text']}` ({row['tier']}; wrong {nwrong}/{reps} reps; pred layer={r.get('pred_layer')}; {r.get('reason', '')[:100]})\n"
                         f"  gold=`{json.dumps(row['gold'], ensure_ascii=False)[:240]}`\n"
                         f"  pred=`{json.dumps(r.get('tool_args'), ensure_ascii=False)[:300]}`")
        (out / f"misses_{args.stage}_{label}.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"selection_all": sel_all, "audio_native": an, "n_configs": len(summary)}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
