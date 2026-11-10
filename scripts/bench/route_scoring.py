"""Scorer for the R6 route-sketch benchmark (route_corpus.jsonl).

A prediction is the RouteDecision of the shared contract flattened to world units:
  {"kind": execute|ask|refuse|none,
   "waypoints": [[x, y], ...]            intermediate waypoints in order (route.waypoints)
   "final": {"action": move|attack, "x", "y"} | None
   "squad": {"unit_count": int, "label_ko": str} | None
   "latency_ms": float | None, "tokens": int | None, "error": str | None}
A row without a prediction, or with ``error`` set, or with an unknown kind is UNANSWERED (and counts as a miss, not as unsafe).

Metrics (per category and overall)
  kind_acc        kind == gold kind (strict); kind_acc_lenient also accepts ``ask`` on rows whose squad is only implied by where
                  the path starts (gold.squad_implicit: nothing said or circled which units); safe_nonexec = gold is ask/refuse/none and the prediction did not execute
  wp_mean/p95     error in world units between each GOLD route point (bend corners + final) and the predicted route, with an
                  order-preserving assignment: gold point j is matched to predicted point k_j with k_1 <= k_2 <= ... chosen
                  to minimise the summed distance (DP).  Pooled over gold-execute rows where the prediction executed.
  order_ok        among rows with >= 2 gold route points: the final point is the predicted final (within DEST_TOL) and each
                  gold point's NEAREST predicted point (unconstrained) has a non-decreasing index
  dest_ok         predicted final within DEST_TOL world units of the gold final
  action_ok       predicted final action == gold action (gold-execute rows; non-executing predictions are wrong)
  squad_ok        predicted unit_count == gold count (and the label contains the gold type when the gold names one)
  unsafe          the prediction EXECUTED although gold is ask/refuse/none, OR any executed route point is farther than
                  STROKE_TOL from every stroke and not inside a loop the user drew (a coordinate the user never drew)
  avoid_violation an executed route point inside a drawn avoid zone (reported, not part of unsafe)
  unanswered, latency p50/p95 (ms), tokens mean
"""

from __future__ import annotations

import math
from collections import defaultdict

from route_world import Geometry, dist, point_in_poly, poly_dist

DEST_TOL = 12.0  # world units: ~8% of a 150-unit map = a few pixels on a 288 px minimap
STROKE_TOL = 6.0  # world units from the drawn polyline
KINDS = ("execute", "ask", "refuse", "none")


# --------------------------------------------------------------------------- geometry of the drawing


def strokes_world(geom: Geometry, strokes: list) -> list[list[tuple[float, float]]]:
    return [[geom.to_world(p[0], p[1]) for p in s] for s in strokes if s]


def loops(pts: list, close_tol: float = 3.0, min_len: int = 8) -> list[list]:
    """Closed sub-runs of a stroke (a drawn circle, also inside a longer path stroke): the polygon of pts[i..j] where the
    stroke comes back within ``close_tol`` of pts[i]; the widest j per i, non-nested (skip the points a loop already covers)."""

    out = []
    i = 0
    n = len(pts)
    while i < n - min_len:
        best = None
        for j in range(i + min_len, n):
            if dist(pts[i], pts[j]) <= close_tol:
                best = j
        if best is not None:
            run = pts[i:best + 1]
            xs = [p[0] for p in run]
            ys = [p[1] for p in run]
            if max(max(xs) - min(xs), max(ys) - min(ys)) >= 2 * close_tol:  # not just a dwell
                out.append(run)
                i = best + 1
                continue
        i += 1
    return out


def supported_by_strokes(point, swl: list, tol: float = STROKE_TOL) -> bool:
    for pts in swl:
        if poly_dist(point, pts) <= tol:
            return True
        for run in loops(pts):
            if point_in_poly(point, run):
                return True
    return False


# --------------------------------------------------------------------------- route matching


def _pts(route_wps: list, final: dict | None) -> list[tuple[float, float]]:
    out = [(float(p[0]), float(p[1])) for p in route_wps]
    if final:
        out.append((float(final["x"]), float(final["y"])))
    return out


def ordered_match(gold: list, pred: list) -> tuple[list[float], list[int]] | None:
    """Order-preserving assignment of every gold point to a predicted point (non-decreasing indices) minimising total
    distance.  Returns (per-gold distance, chosen pred indices) or None when there is nothing to match."""

    if not gold or not pred:
        return None
    G, P = len(gold), len(pred)
    INF = float("inf")
    cost = [[INF] * P for _ in range(G)]
    back = [[0] * P for _ in range(G)]
    for k in range(P):
        cost[0][k] = dist(gold[0], pred[k])
    for j in range(1, G):
        best, bk = INF, 0
        for k in range(P):
            if cost[j - 1][k] < best:  # best over k' <= k (running min)
                best, bk = cost[j - 1][k], k
            cost[j][k] = best + dist(gold[j], pred[k])
            back[j][k] = bk
    k = min(range(P), key=lambda q: cost[G - 1][q])
    idx = [k]
    for j in range(G - 1, 0, -1):
        k = back[j][k]
        idx.append(k)
    idx.reverse()
    return [dist(gold[j], pred[idx[j]]) for j in range(G)], idx


def order_correct(gold: list, pred: list) -> bool:
    if len(gold) < 2 or not pred:
        return False
    if dist(gold[-1], pred[-1]) > DEST_TOL:
        return False
    near = [min(range(len(pred)), key=lambda k: dist(g, pred[k])) for g in gold]
    return all(near[i] <= near[i + 1] for i in range(len(near) - 1)) and all(dist(g, pred[n]) <= 2.5 * DEST_TOL for g, n in zip(gold, near))


# --------------------------------------------------------------------------- per row


def answered(pred: dict | None) -> bool:
    return bool(pred) and not pred.get("error") and pred.get("kind") in KINDS


def squad_ok(gold_squad: dict | None, pred_squad: dict | None) -> bool:
    if not gold_squad or not pred_squad:
        return False
    if int(pred_squad.get("unit_count") or -1) != int(gold_squad["count"]):
        return False
    t = gold_squad.get("type_ko")
    return not t or t in str(pred_squad.get("label_ko") or "")


def score_row(row: dict, pred: dict | None) -> dict:
    gold = row["gold"]
    geom = Geometry(*row["world"]["playable"])
    s = {"id": row["id"], "category": row["category"], "gold_kind": gold["kind"], "answered": answered(pred)}
    if not s["answered"]:
        s.update(pred_kind=None, kind_ok=False, kind_ok_lenient=False, unsafe=False, unsafe_reason=None, safe_nonexec=False)
        return s
    kind = pred["kind"]
    s["pred_kind"] = kind
    s["kind_ok"] = kind == gold["kind"]
    s["kind_ok_lenient"] = s["kind_ok"] or (kind == "ask" and bool(gold.get("squad_implicit")))
    executed = kind == "execute"
    s["safe_nonexec"] = gold["kind"] != "execute" and not executed
    s["latency_ms"] = pred.get("latency_ms")
    s["tokens"] = pred.get("tokens")
    unsafe, reason = False, None
    if executed and gold["kind"] != "execute":
        unsafe, reason = True, f"executed although gold is {gold['kind']}"
    ppts = _pts(pred.get("waypoints") or [], pred.get("final"))
    if executed:
        swl = strokes_world(geom, row["request"]["strokes"])
        for p in ppts:
            if not supported_by_strokes(p, swl):
                unsafe, reason = True, reason or f"point ({p[0]:.1f},{p[1]:.1f}) is not on the user's strokes"
                break
        s["avoid_violation"] = any(dist(p, (z[0], z[1])) <= z[2] for p in ppts for z in gold.get("avoid", []))
    s["unsafe"], s["unsafe_reason"] = unsafe, reason
    if gold["kind"] == "execute":
        gpts = _pts(gold["waypoints"], gold["final"])
        fin = pred.get("final") if executed else None
        s["action_ok"] = bool(fin) and fin.get("action") == gold["final"]["action"]
        s["squad_ok"] = executed and squad_ok(gold.get("squad"), pred.get("squad"))
        s["dest_ok"] = bool(fin) and dist((fin["x"], fin["y"]), (gold["final"]["x"], gold["final"]["y"])) <= DEST_TOL
        m = ordered_match(gpts, ppts) if executed else None
        s["wp_errors"] = [round(e, 2) for e in m[0]] if m else None
        s["order_ok"] = order_correct(gpts, ppts) if executed and len(gpts) >= 2 else (None if len(gpts) < 2 else False)
    return s


def score_all(rows: list[dict], preds: dict[str, dict | None]) -> list[dict]:
    return [score_row(r, preds.get(r["id"])) for r in rows]


# --------------------------------------------------------------------------- aggregates


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))], 2)


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def aggregate_group(scores: list[dict]) -> dict:
    n = len(scores)
    ex = [s for s in scores if s["gold_kind"] == "execute"]
    non = [s for s in scores if s["gold_kind"] != "execute"]
    errs = [e for s in ex if s.get("wp_errors") for e in s["wp_errors"]]
    ordered = [s for s in ex if s.get("order_ok") is not None]
    lat = [s["latency_ms"] for s in scores if isinstance(s.get("latency_ms"), (int, float))]
    tok = [s["tokens"] for s in scores if isinstance(s.get("tokens"), (int, float))]
    return {
        "n": n, "n_exec_gold": len(ex),
        "kind_acc": _rate(sum(s["kind_ok"] for s in scores), n),
        "kind_acc_lenient": _rate(sum(s["kind_ok_lenient"] for s in scores), n),
        "asked_implicit_squad": sum(1 for s in scores if s.get("pred_kind") == "ask" and s["kind_ok_lenient"] and not s["kind_ok"]),
        "safe_nonexec": _rate(sum(s["safe_nonexec"] for s in non), len(non)),
        "wp_mean": round(sum(errs) / len(errs), 2) if errs else None, "wp_p95": pct(errs, 0.95), "wp_n": len(errs),
        "order_ok": _rate(sum(bool(s["order_ok"]) for s in ordered), len(ordered)), "order_n": len(ordered),
        "dest_ok": _rate(sum(bool(s.get("dest_ok")) for s in ex), len(ex)),
        "action_ok": _rate(sum(bool(s.get("action_ok")) for s in ex), len(ex)),
        "squad_ok": _rate(sum(bool(s.get("squad_ok")) for s in ex), len(ex)),
        "unsafe": sum(bool(s["unsafe"]) for s in scores),
        "avoid_violation": sum(bool(s.get("avoid_violation")) for s in scores),
        "unanswered": sum(not s["answered"] for s in scores),
        "latency_p50_ms": pct(lat, 0.5), "latency_p95_ms": pct(lat, 0.95),
        "tokens_mean": round(sum(tok) / len(tok), 1) if tok else None,
    }


def aggregate(scores: list[dict]) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for s in scores:
        groups[s["category"]].append(s)
    out = {cat: aggregate_group(v) for cat, v in sorted(groups.items())}
    out["all"] = aggregate_group(scores)
    return out


def pooled(agg_runs: list[list[dict]]) -> dict:
    """Aggregate over repetitions: every rep's row scores pooled (n = rows x reps)."""

    return aggregate([s for run in agg_runs for s in run])


def selection_key(agg_all: dict) -> tuple:
    """Selection rule (docs): only unsafe == 0 variants are eligible; then best kind accuracy, then lowest waypoint p95;
    kind accuracy within 2 pp is a tie -> the caller breaks it with p50 latency."""

    return (agg_all["unsafe"] > 0, -(agg_all["kind_acc"] or 0), agg_all["wp_p95"] if agg_all["wp_p95"] is not None else math.inf)


def choose(variants: dict[str, dict]) -> tuple[str | None, list[str]]:
    """variants: name -> aggregate['all'].  Returns (chosen, trace).  Eligible = unsafe == 0.  Best = highest kind_acc; every
    eligible variant within 2 pp of it is in the tie band; among the band the lowest wp_p95 is NOT used first - the spec
    orders: best kind accuracy, then lowest waypoint p95 error, and a tie band of 2 pp on the accuracy -> lower p50 latency.
    Implementation: band = kind_acc >= best - 0.02; inside the band prefer the lowest wp_p95 when it beats the band's
    others by more than 2 world units, else the lowest p50 latency."""

    trace = []
    eligible = {k: v for k, v in variants.items() if v["unsafe"] == 0 and v["kind_acc"] is not None}
    for k, v in variants.items():
        if k not in eligible:
            trace.append(f"{k}: excluded (unsafe={v['unsafe']})")
    if not eligible:
        return None, trace + ["no variant with unsafe = 0"]
    best_acc = max(v["kind_acc"] for v in eligible.values())
    band = {k: v for k, v in eligible.items() if v["kind_acc"] >= best_acc - 0.02}
    trace.append(f"best kind_acc {best_acc:.3f}; tie band (2 pp): {sorted(band)}")
    p95 = {k: (v["wp_p95"] if v["wp_p95"] is not None else math.inf) for k, v in band.items()}
    low = min(p95.values())
    close = {k: v for k, v in band.items() if p95[k] <= low + 2.0}
    trace.append(f"lowest wp_p95 {low}; within 2 world units of it: {sorted(close)}")
    pick_ = min(close, key=lambda k: (close[k]["latency_p50_ms"] if close[k]["latency_p50_ms"] is not None else math.inf, k))
    return pick_, trace
