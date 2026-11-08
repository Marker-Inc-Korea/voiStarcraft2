"""Run a system under test over intent_corpus.jsonl and score it (intent_scoring.py).

Systems
  offline   the CURRENT product's deterministic layer on the transcript only (no model): web_gui._decide_realtime_intent
            with tool_arguments=None  = raw-control/injection guard + emergency regex + fast rules + legacy micro fast
            path + negation guard.  A row it does not answer is ``none`` (in the product it would fall to the model).
  llm       the CURRENT shipped prompt + tool (realtime_intent.build_realtime_instructions / build_submit_command_tool,
            variant "both" = product prompt) answered by a TEXT chat model through the same proxy the other benches use,
            then the same web_gui decision function (tool call + guards).  The product's realtime audio model is not
            callable with text over chat/completions, so this is a text-model PROXY for the shipped prompt, not the
            shipped model.  The prompt carries no game state (the product prompt has none).

Variants (--variant, suffix flags on the shipped prompt names): ``both`` = V0 shipped; ``both_cv1`` = V1 (the model picks a
catalog id from the enum listed in the prompt, the server still evaluates it against the state); ``both_cv2`` = V2 (the
model passes the user's phrase, the server resolver ``realtime_catalog.resolve_intent`` picks / asks / refuses).  The
catalog variants are answered with the row's game state resolved server side (``catalog_state_for_row``); the prompt never
carries state.  ``--kind rtx`` drives the REAL Realtime model over a websocket with the transcript as TEXT input
(``gpt-realtime`` / ``gpt-realtime-mini``); ``--kind tx`` is the chat-completions proxy.

Usage (from repo root; the schema module must be the shipped one):
  python3 scripts/bench/intent_run.py offline
  source ~/.zshenv; python3 scripts/bench/intent_run.py llm --model gemini-3.5-flash-lite --reps 1
  python3 scripts/bench/intent_run.py report --pred docs/bench-results/intent_baseline_offline.json

Outputs: docs/bench-results/intent_baseline_<system>[_<model>].json (per-row predictions + scores + aggregate).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))
import intent_catalog as C  # noqa: E402
import intent_scoring as SC  # noqa: E402

CORPUS = HERE / "intent_corpus.jsonl"
RESULTS = HERE.parents[1] / "docs" / "bench-results"
STRUCT_TARGET_PREFIX = "TERRAN_"
# Research-like production tokens the product's MicroMachine DSL accepts (realtime_intent.PRODUCTION_TARGETS minus units/
# structures/add-ons) -> API command.  Resolved through python-sc2 data below, not typed per-command: token -> upgrade.
_RESEARCH_TOKEN_UPGRADE = {"STIMPACK": "STIMPACK", "COMBATSHIELD": "SHIELDWALL", "BANSHEE_CLOAK": "BANSHEECLOAK",
                           "GHOST_CLOAK": "PERSONALCLOAKING", "YAMATO_CANNON": "BATTLECRUISERENABLESPECIALIZATIONS"}


def _research_tokens() -> dict[str, str]:
    out = {}
    for tok, up in _RESEARCH_TOKEN_UPGRADE.items():
        for cmd, entries in C.research_entries().items():
            if any(e["upgrade"] == up for e in entries):
                out[tok] = cmd
    return out


RESEARCH_TOKENS = _research_tokens()


def load_rows(path: Path = CORPUS) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# --------------------------------------------------------------------------- product output -> command id


def _target_command(token: str) -> str | None:
    """A production/building target token the product accepts -> API command id (None when it names no command)."""

    tok = str(token)
    if tok in RESEARCH_TOKENS:
        return RESEARCH_TOKENS[tok]
    for parent in C.ADDON_PARENTS:
        if tok == f"{parent}_TECHLAB":
            return f"BUILD_TECHLAB@{parent}"
        if tok == f"{parent}_REACTOR":
            return f"BUILD_REACTOR@{parent}"
    name = tok[len(STRUCT_TARGET_PREFIX):] if tok.startswith(STRUCT_TARGET_PREFIX) else tok
    for cmd in (f"TRAIN_{name}", f"BUILD_{name}"):
        if cmd in C.terran_commands():
            return cmd
    return None


def provider_output_to_commands(po: dict) -> list[str]:
    """What the Direct/MicroMachine plan would be asked to do, as API command ids.  Non-command plans (operations,
    emergencies) become ``OPERATION:<task>`` / ``EMERGENCY`` so they are scored as an executed WRONG command."""

    layer = po.get("command_layer")
    task = po.get("tactical_task") or {}
    if layer == "emergency":
        return ["EMERGENCY"]
    cmds: list[str] = []
    if task.get("task_type") == "execute_ability":
        cmd, caster = C.token_to_command().get(str(task.get("ability")), (None, None))
        if cmd:
            cmds.append(f"{cmd}@{caster}" if caster else cmd)
        else:
            cmds.append(f"UNMAPPED_ABILITY:{task.get('ability')}")
    elif layer == "operation" or (task.get("task_type") not in (None, "", "tech_transition", "sustain_production",
                                                                 "expand_or_land_command_center")):
        cmds.append(f"OPERATION:{task.get('task_type')}")
    for bt in po.get("building_tasks") or []:
        c = _target_command(bt.get("building_type", ""))
        cmds.append(c or f"UNMAPPED_BUILDING:{bt.get('building_type')}")
    plan = (po.get("production_plan") or {}).get("targets") or task.get("production_targets") or []
    built = {bt.get("building_type") for bt in po.get("building_tasks") or []}
    for t in plan:
        if t in built:
            continue
        c = _target_command(t)
        cmds.append(c or f"UNMAPPED_TARGET:{t}")
    if task.get("task_type") == "expand_or_land_command_center":
        cmds.append("BUILD_COMMANDCENTER")
    return sorted(dict.fromkeys(cmds))


def catalog_state_for_row(row: dict):
    """The row's game state as a ``sc2_catalog.CatalogState`` (what ``PythonSC2BotAdapter.catalog_state()`` supplies live).

    Same world the gold is derived from (intent_catalog.resolve): the listed structures (an add-on structure marks its parent
    as equipped), every non-structure caster present with the row's energy, the researched upgrades.  Resources are not a
    gate in the gold, so no ``can_afford``; targets (enemy / geyser / patch) are unknown = not a gate either.
    """

    from starcraft_commander import sc2_catalog as cat

    st = row["state"]
    structures = list(st["structures"])
    owned = []
    tag = 0
    for name in structures:
        tag += 1
        addon = ""
        if name in C.ADDON_PARENTS:
            addon = "techlab" if f"{name}TECHLAB" in structures else "reactor" if f"{name}REACTOR" in structures else "none"
        owned.append(cat.Owned(name, tag=tag, addon=addon, is_structure=True))
    structure_names = set(structures) | set(C.structure_names())
    casters = {i for e in cat.all_commands() for i in e.valid_issuers} | {"SCV"}
    for name in sorted(casters - structure_names):
        tag += 1
        owned.append(cat.Owned(name, tag=tag, energy=float(st.get("energy", 0))))
    energy_structs = {"ORBITALCOMMAND", "ORBITALCOMMANDFLYING"}
    owned = [o if o.type_name not in energy_structs else cat.Owned(o.type_name, tag=o.tag, addon=o.addon, is_structure=True,
                                                                     energy=float(st.get("energy", 0))) for o in owned]
    return cat.CatalogState(owned=tuple(owned), upgrades_done=frozenset(st["researched"]), minerals=st.get("minerals"),
                            vespene=st.get("gas"))


def _gold_id(command_id: str, level: int | None, issuer: str) -> str:
    from starcraft_commander import sc2_catalog as cat

    entry = cat.get_command(command_id)
    name = entry.header_name
    if level:
        return f"{name}#{level}"
    if name in C.QUALIFIED and issuer:
        return f"{name}@{issuer.removesuffix('FLYING') if name in ('BUILD_TECHLAB', 'BUILD_REACTOR') else issuer}"
    return name


def decision_to_prediction(decision) -> dict:
    kind = decision.kind
    command_id = getattr(decision, "command_id", "")
    narration = getattr(decision, "narration", "")
    if kind == "clarification":
        return {"kind": "ask", "command": None, "commands": [], "reason": "", "chosen": command_id, "narration": narration}
    if kind == "blocked":
        return {"kind": "refuse", "command": None, "commands": [], "reason": decision.source}
    if kind == "executed" and command_id:
        res = (decision.provider_output or {}).get("catalog_resolution") or {}
        cid = _gold_id(command_id, res.get("level"), res.get("issuer", ""))
        return {"kind": "execute", "command": cid, "commands": [cid], "reason": narration, "source": decision.source,
                "chosen": command_id, "narration": narration}
    if kind == "executed":
        cmds = provider_output_to_commands(decision.provider_output or {})
        return {"kind": "execute", "command": cmds[0] if len(cmds) == 1 else (None if not cmds else "MULTI"),
                "commands": cmds, "reason": "", "source": decision.source}
    return {"kind": "none", "command": None, "commands": [], "reason": decision.kind}


def decide(transcript: str, tool_args: dict | None, row: dict | None = None, state_guard: bool = True) -> dict:
    """``state_guard=False`` = V0 (the shipped product: no server-side state, no catalog)."""

    from starcraft_commander import web_gui as WG

    transcript = " ".join((transcript or "").split())[:2000]
    state = catalog_state_for_row(row) if (row is not None and state_guard) else None
    try:
        return decision_to_prediction(WG._decide_realtime_intent(transcript, tool_args, catalog_state=state))
    except (ValueError, TypeError, KeyError) as exc:
        return {"kind": "none", "command": None, "commands": [], "reason": f"invalid_tool_call: {str(exc)[:80]}"}


# --------------------------------------------------------------------------- systems


def run_offline(rows: list[dict]) -> dict[str, dict]:
    return {r["id"]: decide(r["text"], None, r) for r in rows}


def run_resolver_only(rows: list[dict]) -> dict[str, dict]:
    """V2 with a PERFECT model: ``catalog.intent`` = the user's exact words, the rest is the server (rule layers first,
    then the resolver against the row's state).  Upper bound of V2 and the cost-free regression gate of the resolver."""

    out = {}
    for r in rows:
        pred = decide(r["text"], None, r)
        if pred["kind"] == "none":  # the deterministic layer abstained: the model lane would now be asked
            call = {"command_layer": "operation", "goal": r["text"][:200], "catalog": {"intent": r["text"]}}
            pred = decide(r["text"], call, r)
        out[r["id"]] = pred
    return out


async def run_llm(rows: list[dict], model: str, reps: int, variant: str, concurrency: int,
                  kind: str = "tx") -> dict[str, list[dict]]:
    import pipelines as P  # noqa: E402 (after BENCH_SCHEMA_MODULE is set in main)

    C_variant = variant
    cfg = P.tx_config(model, variant) if kind == "tx" else P.rtx_config(model, variant)
    run_one = P.run_text if kind == "tx" else P.run_realtime_text
    sem = asyncio.Semaphore(concurrency)
    out: dict[str, list[dict]] = {r["id"]: [] for r in rows}

    async def one(row, rep):
        async with sem:
            res = await run_one(cfg, row, rep)
        args = res.get("tool_args")
        # An abstained fast rule is not a model answer: the product asks the model only when the deterministic layer
        # is silent, but a tool call with the same transcript goes through the very same decision function.
        pred = decide(row["text"], args, row, state_guard=(C_variant != "both")) if isinstance(args, dict) else {"kind": "none", "command": None, "commands": [],
                                                                              "reason": res.get("error") or "no tool call"}
        pred["latency_ms"] = res.get("total_ms")
        pred["tokens"] = (res.get("tokens") or {}).get("total")
        pred["tool_args"] = args
        out[row["id"]].append(pred)

    await asyncio.gather(*(one(r, k) for k in range(reps) for r in rows))
    await P.close_http()
    return out


def rescore_llm(path: Path) -> Path:
    """Re-run the CURRENT decision function over the stored raw tool calls (no model calls)."""

    data = json.loads(path.read_text(encoding="utf-8"))
    rows = load_rows()
    preds = {}
    for r in rows:
        old = data["predictions"].get(r["id"], {})
        args = old.get("tool_args")
        pred = decide(r["text"], args, r, state_guard=(data["meta"].get("variant") != "both")) if isinstance(args, dict) else {"kind": "none", "command": None, "commands": [],
                                                                          "reason": old.get("reason", "no tool call")}
        pred.update({k: old.get(k) for k in ("latency_ms", "tokens", "tool_args")})
        preds[r["id"]] = pred
    return save(path.stem.replace("intent_baseline_", ""), rows, preds, data["meta"])


# --------------------------------------------------------------------------- reporting


def fmt(v) -> str:
    return "n/a" if v is None else f"{v:.3f}"


def table(agg: dict) -> str:
    cols = ["n", "exact_accuracy", "acceptable_accuracy", "wrong_exec_rate", "clarification_rate", "unsafe_count",
            "correct_rate", "unanswered"]
    lines = ["| tier | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    for tier in ["explicit", "intent", "ambiguous", "state", "negative", "all"]:
        if tier not in agg:
            continue
        a = agg[tier]
        lines.append(f"| {tier} | " + " | ".join(str(a[c]) if c in ("n", "unsafe_count", "unanswered") else fmt(a[c]) for c in cols) + " |")
    return "\n".join(lines)


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))], 1)


def cost_summary(preds: dict[str, dict]) -> dict:
    """Tokens and latency of the model calls in this run (the scorer does not aggregate them)."""

    lat = [p["latency_ms"] for p in preds.values() if isinstance(p.get("latency_ms"), (int, float))]
    tok = [p["tokens"] for p in preds.values() if isinstance(p.get("tokens"), (int, float))]
    return {"calls": len(preds), "latency_n": len(lat), "latency_p50_ms": _pct(lat, 0.5), "latency_p95_ms": _pct(lat, 0.95),
            "tokens_mean": round(sum(tok) / len(tok), 1) if tok else None, "tokens_n": len(tok)}


def save(name: str, rows: list[dict], preds: dict[str, dict], meta: dict) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    scores = SC.score_all(rows, preds)
    agg = SC.aggregate(rows, preds)
    meta = {**meta, "cost": cost_summary(preds), "loadavg_1_5_15": [round(x, 2) for x in os.getloadavg()]}
    path = RESULTS / f"intent_baseline_{name}.json"
    cats = SC.by_category(rows, preds)
    path.write_text(json.dumps({"meta": meta, "aggregate": agg, "by_category": cats, "scores": scores, "predictions": preds},
                               ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(table(agg))
    print("by API category (expect=execute rows): " + ", ".join(f"{k} {v['acceptable']}/{v['n']}" for k, v in sorted(cats.items())))
    print(f"wrote {path}")
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("system", choices=["offline", "resolver", "llm", "report", "rescore"])
    ap.add_argument("--model", default="gemini-3.5-flash-lite")
    ap.add_argument("--variant", default="both", help="shipped prompt variant; 'both' = product prompt")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--pred", default="")
    ap.add_argument("--tiers", default="")
    ap.add_argument("--kind", choices=["tx", "rtx"], default="tx",
                    help="tx = chat-completions proxy; rtx = real Realtime model over a websocket with TEXT input")
    args = ap.parse_args(argv)
    if args.system in ("llm", "rtx"):
        os.environ["BENCH_SCHEMA_MODULE"] = "shipped_schema"  # pipelines.py builds the SHIPPED tool/prompt (read at import)
    rows = load_rows()
    if args.tiers:
        rows = [r for r in rows if r["tier"] in args.tiers.split(",")]
    if args.system == "rescore":
        rescore_llm(Path(args.pred))
        return 0
    if args.system == "report":
        data = json.loads(Path(args.pred).read_text(encoding="utf-8"))
        print(table(data["aggregate"]))
        return 0
    if args.system == "resolver":
        save("resolver_v2_perfect_model", rows, run_resolver_only(rows),
             {"system": "V2 resolver with a perfect model (intent = user's words); deterministic layer first", "rows": len(rows)})
        return 0
    if args.system == "offline":
        save("offline", rows, run_offline(rows), {"system": "offline deterministic layer (no model)", "rows": len(rows)})
        return 0
    reps = asyncio.run(run_llm(rows, args.model, args.reps, args.variant, args.concurrency, args.kind))
    system = ("shipped prompt via the real Realtime model, transcript as TEXT input" if args.kind == "rtx"
              else "shipped prompt via text chat model (proxy)")
    for rep in range(args.reps):
        preds = {rid: p[rep] for rid, p in reps.items() if len(p) > rep}
        save(f"llm_{args.kind}_{args.model}_{args.variant}_rep{rep}", rows, preds,
             {"system": system, "kind": args.kind, "model": args.model, "variant": args.variant,
              "rep": rep, "reps": args.reps, "rows": len(rows), "concurrency": args.concurrency})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
