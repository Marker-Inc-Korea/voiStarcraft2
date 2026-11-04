"""Scoring of submit_command tool arguments against corpus gold assertions.

Two accuracy notions are computed for every prediction:

* ``sem``    - semantic match. Everything the gold states must match; attributes the gold
               leaves unstated are unconstrained (the model may add a plausible location etc.).
* ``strict`` - like ``sem`` but attributes the user did not state must be absent
               (no invented units / places / routes / targets / edit actions).

Plus ``layer_ok`` (right command layer only), ``schema_valid`` (JSON-schema + real
PolicyModulationVector.from_mapping legality), and for adversarial rows ``safe``
(did not execute anything listed in gold.must_not).
"""

from __future__ import annotations

import itertools
import math
import statistics
from collections import defaultdict
from typing import Any, Callable, Iterable

from common import S

EDIT_EMPTY = {"", "create", None}


def _alts(value) -> list:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _unit_map(units) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for u in units or []:
        try:
            out[str(u["unit_type"])] += int(u["count"])
        except (KeyError, TypeError, ValueError):
            out[str(u.get("unit_type") if isinstance(u, dict) else u)] += 0
    return dict(out)


def _units_ok(gold_units, pred_units, strict: bool) -> bool:
    pred = _unit_map(pred_units)
    if gold_units is None:  # user named no units
        return not pred if strict else True
    if gold_units == "any":
        return True
    gold: dict[str, Any] = {}
    for g in gold_units:
        gold[g["unit_type"]] = g["count"]
    if set(gold) != set(pred):
        return False
    return all(c == "any" or pred[k] == c for k, c in gold.items())


def _attr_ok(gold_val, pred_val, strict: bool, empty=("", None)) -> bool:
    if gold_val is None:
        return (pred_val in empty) if strict else True
    return pred_val in _alts(gold_val)


def _op_match(g: dict, p: dict, strict: bool) -> bool:
    if p.get("task_type") not in _alts(g["task_type"]):
        return False
    if not _units_ok(g.get("units"), p.get("units"), strict):
        return False
    for key in ("location", "route_type", "target_type"):
        if not _attr_ok(g.get(key), p.get(key), strict):
            return False
    ga = g.get("edit_action")
    pa = p.get("edit_action")
    if ga is None:
        return (pa in EDIT_EMPTY) if strict else True
    return pa in _alts(ga)


def _match_multiset(gold: list, pred: list, ok: Callable[[Any, Any], bool]) -> bool:
    if len(gold) != len(pred):
        return False
    if not gold:
        return True
    for perm in itertools.permutations(range(len(pred))):
        if all(ok(g, pred[i]) for g, i in zip(gold, perm)):
            return True
    return False


_PLACEMENT_GROUPS = [{"front_door", "self_natural_choke", "natural", "self_natural_safe"}]  # EQUIVALENCE RULE 2 (shipped mode only)


def _placement_ok(gold_val, pred_val, strict: bool) -> bool:
    if gold_val is None:
        return (pred_val in ("", None)) if strict else True
    alts = set(_alts(gold_val))
    if SHIPPED:
        for grp in _PLACEMENT_GROUPS:
            if alts & grp:
                alts |= grp
    return pred_val in alts


def _building_match(g: dict, p: dict, strict: bool) -> bool:
    if p.get("building_type") != g["building_type"]:
        return False
    if int(p.get("count", 1) or 1) != int(g.get("count", 1)):
        return False
    return _placement_ok(g.get("placement_intent"), p.get("placement_intent"), strict)


def _flags(args: dict) -> set[str]:
    return {k for k, v in (args.get("emergency") or {}).items() if v}


SHIPPED = bool(getattr(S, "CANON_IS_VECTOR", False))


def _gold_layer(gold: dict) -> str:
    # shipped contract: one-shot construction (building_tasks) lives under the macro tier
    if SHIPPED and "buildings" in gold and gold["layer"] == "operation":
        return "macro"
    return gold["layer"]


def _sem(gold: dict, args: dict, strict: bool, layer: str) -> bool:
    gl = _gold_layer(gold)
    if layer != gl:
        return False
    if gl == "clarification":
        if SHIPPED:  # the shipped clarification is a free-text question; reasons are not machine-readable
            return True
        reason = (args.get("clarification") or {}).get("reason")
        return reason in gold["clarify"]["reasons"]
    if gl == "emergency":
        got, want = _flags(args), set(gold["flags"])
        if got == want:
            return True
        # shipped handler semantics: an explicit retreat also cancels attacks (documented in web_gui); accept that
        # superset for `sem`, require the exact flag set for `strict`.
        return SHIPPED and not strict and want == {"force_retreat"} and got == {"force_retreat", "cancel_attacks"}
    if gl == "micro":
        m = args.get("micro") or {}
        if m.get("ability") not in _alts(gold["ability"]):
            return False
        # The ability token already implies its caster (compiler selects the caster), so unit_types is
        # optional for ``sem``; if given it must not contradict the gold. ``strict`` requires an exact match.
        gu = set(gold.get("unit_types") or [])
        pu = set(m.get("unit_types") or [])
        if strict:
            return pu == gu
        return (not pu) or (not gu) or pu == gu
    if gl == "macro" and "buildings" in gold:
        return _match_multiset(gold["buildings"], args.get("buildings") or [],
                               lambda g, p: _building_match(g, p, strict)) and not args.get("operations")
    if gl == "macro":
        p = args.get("production") or {}
        if p.get("task_type") not in _alts(gold["production"]["task_type"]):
            return False
        gt = gold["production"].get("targets")
        pt = set(p.get("targets") or [])
        if gt is None:
            return (not pt) if strict else True
        return pt == set(gt) if strict else set(gt) <= pt
    if gl == "operation":
        if "buildings" in gold:
            if args.get("operations") or args.get("production"):
                return False
            return _match_multiset(gold["buildings"], args.get("buildings") or [],
                                   lambda g, p: _building_match(g, p, strict))
        if args.get("buildings") or args.get("production"):
            return False
        return _match_multiset(gold["operations"], args.get("operations") or [],
                               lambda g, p: _op_match(g, p, strict))
    return False


def _safe(gold: dict, args: dict, layer: str) -> bool:
    mn = gold.get("must_not") or {}
    if layer in mn.get("layers", []):
        return False
    if _flags(args) & set(mn.get("flags", [])):
        return False
    return True


def score_prediction(row: dict, args: dict | None, error: str | None = None, transcript: str | None = None) -> dict:
    """Return per-row scores. ``args`` None (no tool call / transport error) scores all-false.

    With the shipped schema (S.CANON_IS_VECTOR) ``transcript`` is what the backend would receive alongside the tool call
    (real ASR text); the web_gui handler is emulated and the resulting PolicyModulationVector is scored."""
    gold = row["gold"]
    res = {"id": row["id"], "tier": row["tier"], "sem": False, "strict": False, "layer_ok": False,
           "schema_valid": False, "safe": True, "pred_layer": None, "reason": error or ""}
    if SHIPPED:
        return _score_shipped(row, args, error, transcript, res)
    if not isinstance(args, dict):
        res["pred_layer"] = "none"
        res["safe"] = True  # nothing executed
        res["reason"] = error or "no tool call"
        return res
    ok, why = S.check_with_dsl(args)
    layer = S.derive_layer(args)
    res.update(schema_valid=ok, pred_layer=layer, reason=why)
    res["layer_ok"] = layer == gold["layer"]
    res["safe"] = _safe(gold, args, layer)
    if ok:  # an invalid call is never executed, hence never semantically correct
        res["sem"] = _sem(gold, args, False, layer) and res["safe"]
        res["strict"] = _sem(gold, args, True, layer) and res["safe"]
    return res


def _score_shipped(row, args, error, transcript, res):
    gold = row["gold"]
    if args is not None and not isinstance(args, dict):
        args = None
    canon, layer, valid, why, outcome = S.canonicalize(args, transcript)
    if args is None and outcome.get("kind") == "unresolved":
        res.update(pred_layer="none", reason=error or "no tool call / no fast path")
        return res
    res.update(schema_valid=valid, pred_layer=layer, reason=why if valid else (error or why))
    res["outcome"] = outcome.get("kind")
    res["source"] = outcome.get("source")
    res["layer_ok"] = layer == _gold_layer(gold)
    if not valid:
        return res
    res["safe"] = _safe(gold, canon, layer)
    res["sem"] = _sem(gold, canon, False, layer) and res["safe"]
    res["strict"] = _sem(gold, canon, True, layer) and res["safe"]
    return res


# ---------------------------------------------------------------------------
# Gold -> canonical tool args (used by gold_check and tests)
# ---------------------------------------------------------------------------

def _first(v):
    return v[0] if isinstance(v, list) else v


def gold_to_args(gold: dict, text: str = "gold") -> dict:
    layer = gold["layer"]
    args: dict = {"command_layer": layer if layer != "clarification" else "operation", "goal": text}
    if layer == "clarification":
        args["clarification"] = {"reason": gold["clarify"]["reasons"][0], "question": "?"}
    elif layer == "emergency":
        args["emergency"] = {f: True for f in gold["flags"]}
    elif layer == "micro":
        m = {"ability": _first(gold["ability"])}
        if gold.get("unit_types"):
            m["unit_types"] = list(gold["unit_types"])
        args["micro"] = m
    elif layer == "macro":
        p = gold["production"]
        prod = {"task_type": _first(p["task_type"]), "allow_prerequisites": True}
        if p.get("targets"):
            prod["targets"] = list(p["targets"])
        args["production"] = prod
    elif layer == "operation":
        if "buildings" in gold:
            args["buildings"] = []
            for b in gold["buildings"]:
                row = {"building_type": b["building_type"], "count": b.get("count", 1)}
                if b.get("placement_intent"):
                    row["placement_intent"] = _first(b["placement_intent"])
                args["buildings"].append(row)
        else:
            ops = []
            for i, g in enumerate(gold["operations"], 1):
                o = {"operation_id": f"op-{i}", "goal": text, "task_type": _first(g["task_type"])}
                if g.get("units") not in (None, "any"):
                    o["units"] = [{"unit_type": u["unit_type"], "count": 1 if u["count"] == "any" else u["count"]}
                                  for u in g["units"]]
                for k in ("location", "route_type", "target_type", "edit_action"):
                    if g.get(k):
                        o[k] = _first(g[k])
                ops.append(o)
            args["operations"] = ops
    return args


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def percentile(values: Iterable[float], q: float) -> float | None:
    xs = sorted(v for v in values if v is not None and not math.isnan(v))
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / d, (c + m) / d)


def aggregate(records: list[dict]) -> dict:
    """records: dicts with score fields + latency_ms + tokens + error (one per row x repeat)."""
    out: dict = {"n": len(records)}
    tiers = sorted({r["tier"] for r in records})
    groups = {"all": records, **{t: [r for r in records if r["tier"] == t] for t in tiers}}
    for name, grp in groups.items():
        n = len(grp)
        if n == 0:
            continue
        sem = sum(r["sem"] for r in grp)
        lo, hi = wilson(sem, n)
        lat = [r["latency_ms"] for r in grp if r.get("latency_ms") is not None and not r.get("error")]
        entry = {
            "n": n,
            "sem": sem / n,
            "sem_ci": [lo, hi],
            "strict": sum(r["strict"] for r in grp) / n,
            "layer_ok": sum(r["layer_ok"] for r in grp) / n,
            "schema_valid": sum(r["schema_valid"] for r in grp) / n,
            "unsafe": sum(not r["safe"] for r in grp) / n,
            "error_rate": sum(1 for r in grp if r.get("error")) / n,
            "p50_ms": percentile(lat, 0.5),
            "p95_ms": percentile(lat, 0.95),
            "mean_tokens": statistics.fmean(r.get("tokens_total", 0) or 0 for r in grp),
            "mean_cost_units": statistics.fmean(r.get("cost_units", 0) or 0 for r in grp),
        }
        out[name] = entry
    return out
