"""Scorer for the voice-intent -> concrete Terran command benchmark (corpus: intent_corpus.jsonl).

Prediction record (what any system under test is reduced to)::

    {"kind": "execute"|"ask"|"refuse"|"none", "command": "<id>"|None, "commands": [...], "reason": str,
     "latency_ms": float|None, "tokens": int|None}

``execute`` = a game command was issued; ``ask`` = a clarification question; ``refuse`` = blocked by a guard; ``none`` =
the system produced no decision (e.g. the deterministic layer abstained and no model ran).  Command ids use the grammar
documented in intent_catalog.py (NAME / NAME#level / NAME@CASTER).

Row gold (derived by build_intent_corpus.py, never typed)::

    {"expect": execute|ask|choose_or_ask|refuse, "acceptable": [ids], "exact": id|None}

Metrics (``aggregate``), per tier and ``all``:

* exact_accuracy       hits / rows that have a unique gold command (``exact``)
* acceptable_accuracy  rows with expect=execute whose executed command is in the acceptable set / those rows
* wrong_exec_rate      rows with expect=execute where a command ran that is NOT acceptable / those rows
* clarification_rate   on the ambiguous tier: rows answered correctly (ask, or an acceptable choice WITH a stated
                       reason) / ambiguous rows
* unsafe_rate          rows where a command ran although the right answer was to ask/refuse (or a choice without a
                       stated reason / outside the acceptable set on choose_or_ask rows) / rows. MUST be 0.
* correct_rate         rows answered correctly by the row's own expectation / rows
* unanswered           rows with no decision (kind none / missing prediction)

Latency/tokens are carried on the prediction but intentionally NOT aggregated: no latency claim is made from this
harness (the machine is heavily loaded).
"""

from __future__ import annotations

from typing import Any, Iterable


def _matches(pred: str | None, acceptable: Iterable[str]) -> bool:
    if not pred:
        return False
    acc = list(acceptable)
    if pred in acc:
        return True
    if "@" not in pred and "#" not in pred:
        # caster not stated by the user -> any caster variant is fine, but only if >= 2 variants are acceptable
        variants = [a for a in acc if a.split("@")[0] == pred and "@" in a]
        return len(variants) >= 2
    return False


def score_row(row: dict, pred: dict | None) -> dict[str, Any]:
    gold = row["gold"]
    expect = gold["expect"]
    pred = pred or {"kind": "none", "command": None, "reason": ""}
    kind = pred.get("kind", "none")
    cmds = pred.get("commands") or ([pred["command"]] if pred.get("command") else [])
    executed = kind == "execute"
    single = pred.get("command") if len(set(cmds)) <= 1 else None
    acceptable = _matches(single, gold["acceptable"]) if executed else False
    reason = bool((pred.get("reason") or "").strip())

    out: dict[str, Any] = {
        "id": row["id"], "tier": row["tier"], "expect": expect, "kind": kind, "command": pred.get("command"),
        "acceptable": acceptable, "exact": None, "clarified": kind == "ask", "unanswered": kind == "none",
        "unsafe": False, "wrong_exec": False, "correct": False,
        "latency_ms": pred.get("latency_ms"), "tokens": pred.get("tokens"),
    }
    if gold.get("exact"):
        out["exact"] = bool(executed and single == gold["exact"])

    if expect == "execute":
        out["correct"] = acceptable
        out["wrong_exec"] = executed and not acceptable
    elif expect == "choose_or_ask":
        good_choice = executed and acceptable and reason
        out["correct"] = kind == "ask" or bool(good_choice)
        out["unsafe"] = executed and not good_choice
    else:  # ask / refuse: nothing may run
        out["correct"] = kind in ("ask", "refuse")
        out["unsafe"] = executed
    return out


def _rate(num: int, den: int) -> float | None:
    return None if den == 0 else num / den


def _agg(scores: list[dict]) -> dict[str, Any]:
    n = len(scores)
    exec_rows = [s for s in scores if s["expect"] == "execute"]
    exact_rows = [s for s in scores if s["exact"] is not None]
    amb = [s for s in scores if s["tier"] == "ambiguous"]
    return {
        "n": n,
        "exact_accuracy": _rate(sum(bool(s["exact"]) for s in exact_rows), len(exact_rows)),
        "exact_rows": len(exact_rows),
        "acceptable_accuracy": _rate(sum(s["acceptable"] for s in exec_rows), len(exec_rows)),
        "execute_rows": len(exec_rows),
        "wrong_exec_rate": _rate(sum(s["wrong_exec"] for s in exec_rows), len(exec_rows)),
        "clarification_rate": _rate(sum(s["correct"] for s in amb), len(amb)),
        "ambiguous_rows": len(amb),
        "unsafe_count": sum(s["unsafe"] for s in scores),
        "unsafe_rate": _rate(sum(s["unsafe"] for s in scores), n),
        "correct_rate": _rate(sum(s["correct"] for s in scores), n),
        "unanswered": sum(s["unanswered"] for s in scores),
    }


def aggregate(rows: list[dict], preds: dict[str, dict]) -> dict[str, Any]:
    scores = [score_row(r, preds.get(r["id"])) for r in rows]
    out = {"all": _agg(scores)}
    for tier in sorted({r["tier"] for r in rows}):
        out[tier] = _agg([s for s in scores if s["tier"] == tier])
    return out


def score_all(rows: list[dict], preds: dict[str, dict]) -> list[dict]:
    return [score_row(r, preds.get(r["id"])) for r in rows]


def category_of_row(row: dict) -> str | None:
    """API category of the (first) acceptable command of an expect=execute row (derived, not annotated)."""

    if row["gold"]["expect"] != "execute" or not row["gold"]["acceptable"]:
        return None
    from intent_catalog import terran_commands

    return terran_commands()[row["gold"]["acceptable"][0].split("#")[0].split("@")[0]]["category"]


def by_category(rows: list[dict], preds: dict[str, dict]) -> dict[str, dict]:
    """Acceptable-accuracy over expect=execute rows per API command category (all tiers pooled)."""

    out: dict[str, dict] = {}
    for r in rows:
        cat = category_of_row(r)
        if cat is None:
            continue
        sc = score_row(r, preds.get(r["id"]))
        b = out.setdefault(cat, {"n": 0, "acceptable": 0})
        b["n"] += 1
        b["acceptable"] += int(sc["acceptable"])
    for b in out.values():
        b["acceptable_accuracy"] = b["acceptable"] / b["n"]
    return out
