"""Benchmark runner CLI.

  run.py list                         # configs
  run.py models                       # GET /models availability check for every id used
  run.py synth                        # TTS audio cache (+ audio_manifest.json)
  run.py subset                       # (re)build the stratified screening subset -> subset.json
  run.py screen --configs A,B,...     # screening subset, --reps 3
  run.py full --configs A,B,...       # full corpus, --reps 1 (or --reps N); analysis: report.py, make_doc.py

Every call is cached under scripts/bench/runs/<config>/<row>.<rep>.json (resumable, gitignored).
Concurrency is bounded (default 6, hard max 6) across ALL calls in a process.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import random
import sys
import time
from collections import defaultdict

from common import HERE, RUNS_DIR, S, load_corpus
import pipelines as P
from scoring import score_prediction

SUBSET = HERE / "subset.json"
MAX_CONCURRENCY = 6


def cfg_dir(name: str):
    return RUNS_DIR / name.replace(":", "_").replace("/", "_")


def build_subset(rows, n_per_tier=None, seed=20260611) -> list[str]:
    """Stratified: 8 micro, 5 emergency, 10 operation, 6 macro, 11 adversarial (=40), keeping lang/tag diversity."""
    n_per_tier = n_per_tier or {"micro": 8, "emergency": 5, "operation": 10, "macro": 6, "adversarial": 11}
    rng = random.Random(seed)
    picked: list[str] = []
    for tier, n in n_per_tier.items():
        pool = [r for r in rows if r["tier"] == tier]
        rng.shuffle(pool)
        # round-robin over tag groups for diversity
        by_tag: dict[str, list] = defaultdict(list)
        for r in pool:
            by_tag[(r["tags"] or ["plain"])[0]].append(r)
        order = []
        while any(by_tag.values()) and len(order) < n:
            for tag in sorted(by_tag):
                if by_tag[tag] and len(order) < n:
                    order.append(by_tag[tag].pop())
        picked += [r["id"] for r in order]
    return picked


def load_subset(rows) -> list[dict]:
    if not SUBSET.exists():
        SUBSET.write_text(json.dumps(build_subset(rows), indent=1) + "\n")
    ids = set(json.loads(SUBSET.read_text()))
    return [r for r in rows if r["id"] in ids]


def asr_transcripts(source: str = "") -> dict[tuple[str, int], str]:
    """(row id, rep) -> REAL ASR transcript recorded by a realtime config for the SAME audio (rep k -> variant k mod 3).

    Private-schema runs (no --asr-from): scan rt_*asr* dirs, rep-agnostic (kept for the old bench)."""
    out: dict[tuple[str, int], str] = {}
    dirs = [cfg_dir(s) for s in source.split(",") if s] if source else list(RUNS_DIR.glob("rt_*asr*"))
    for d in dirs:
        for f in sorted(d.glob("*.json")):
            if f.name.startswith("_"):
                continue
            rec = json.loads(f.read_text())
            tr = rec.get("transcript")
            if tr:
                out.setdefault((rec["id"], rec["rep"]), tr)
                if not source:
                    for k in range(3):
                        out.setdefault((rec["id"], k), tr)
    return out


COST_W = {"text_in": 1.0, "audio_in": 8.0, "out": 4.0}  # OpenAI realtime list-price ratio proxy (text 4$/M : audio 32$/M : out 16$/M)


def cost_units(tokens: dict) -> float:
    return sum(COST_W[k] * (tokens.get(k) or 0) for k in COST_W)


async def run_config(cfg: P.Config, rows: list[dict], reps: int, sem: asyncio.Semaphore, asr: dict | None, label: str) -> dict:
    d = cfg_dir(cfg.name)
    d.mkdir(parents=True, exist_ok=True)
    skipped = d / "_skipped.json"
    if skipped.exists():
        return {"config": cfg.name, "skipped": json.loads(skipped.read_text())["reason"]}
    stats = {"done": 0, "cached": 0, "fatal": 0}
    fatal_streak = 0
    abort = asyncio.Event()

    async def one(row, rep):
        nonlocal fatal_streak
        f = d / f"{row['id']}.{rep}.json"
        if f.exists():
            stats["cached"] += 1
            return
        if abort.is_set():
            return
        async with sem:
            if abort.is_set():
                return
            res = await P.run(cfg, row, rep, asr)
        err = res.get("error")
        if err and res.get("tool_args") is None and "no ASR transcript" in err:
            return
        if err and any(k in err for k in ("RESOURCE_EXHAUSTED", "model_not_found", "Individual quota", "invalid_request_error")) \
                and res.get("tool_args") is None:
            fatal_streak += 1
            stats["fatal"] += 1
            if fatal_streak >= 4:
                abort.set()
            return
        fatal_streak = 0
        sc = score_prediction(row, res.get("tool_args"), err, transcript=res.get("transcript") if cfg.kind == "rt" else res.get("input_text"))
        rec = {"config": cfg.name, "id": row["id"], "rep": rep, "tier": row["tier"], "text": row["text"],
               "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
               **res, **{k: v for k, v in sc.items() if k not in ("id", "tier")},
               "latency_ms": res.get("t_tool_call_ms"), "tokens_total": (res.get("tokens") or {}).get("total", 0),
               "cost_units": cost_units(res.get("tokens") or {})}
        f.write_text(json.dumps(rec, ensure_ascii=False))
        stats["done"] += 1
        if stats["done"] % 20 == 0:
            print(f"[{label}] {cfg.name}: {stats['done']} done", flush=True)

    await asyncio.gather(*(one(r, k) for k in range(reps) for r in rows))
    if abort.is_set():
        reason = f"aborted after repeated fatal API errors (e.g. quota/model unavailable); last error recorded in stdout"
        skipped.write_text(json.dumps({"reason": reason, "ts": dt.datetime.now(dt.timezone.utc).isoformat()}))
        return {"config": cfg.name, "skipped": reason, **stats}
    return {"config": cfg.name, **stats}


async def amain(args) -> int:
    rows_all = load_corpus()
    if args.cmd == "list":
        for name in P.registry():
            print(name)
        return 0
    if args.cmd == "subset":
        SUBSET.write_text(json.dumps(build_subset(rows_all), indent=1) + "\n")
        print(f"subset: {len(json.loads(SUBSET.read_text()))} rows")
        return 0
    if args.cmd == "synth":
        import audio

        m = await audio.synthesize(concurrency=min(args.concurrency, MAX_CONCURRENCY))
        print(f"audio manifest: {len(m)} rows")
        return 0
    if args.cmd == "models":
        r = await P.http().get("/models")
        have = {m["id"] for m in r.json()["data"]}
        used = sorted(set(P.RT_MODELS) | set(P.TX_MODELS) | {"gpt-4o-mini-tts"})
        for m in used:
            print(f"{m:32s} {'listed' if m in have else 'NOT LISTED in /models'}")
        print(f"/models total={len(have)} realtime-like={sorted(x for x in have if 'realtime' in x)}")
        await P.close_http()
        return 0
    if args.cmd in ("screen", "full"):
        rows = load_subset(rows_all) if args.cmd == "screen" else rows_all
        if args.tiers:
            rows = [r for r in rows if r["tier"] in args.tiers.split(",")]
        if args.limit:
            rows = rows[: args.limit]
        reps = args.reps or (3 if args.cmd == "screen" else 1)
        names = [c for c in args.configs.split(",") if c]
        asr = asr_transcripts(args.asr_from) if any(n.endswith(":asrtxt") for n in names) else None
        cfgs = [P.get_config(n) for n in names]
        sem = asyncio.Semaphore(min(args.concurrency, MAX_CONCURRENCY))
        t0 = time.time()
        print(f"{args.cmd}: {len(rows)} rows x {reps} reps x {len(cfgs)} configs  start={dt.datetime.now().isoformat(timespec='seconds')}", flush=True)
        # run configs concurrently so slow models do not serialize; the shared semaphore bounds calls
        results = await asyncio.gather(*(run_config(c, rows, reps, sem, asr, args.cmd) for c in cfgs))
        for r in results:
            print(json.dumps(r, ensure_ascii=False))
        print(f"elapsed {time.time() - t0:.0f}s")
        await P.close_http()
        return 0
    raise SystemExit(f"unknown cmd {args.cmd}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("list", "models", "synth", "subset", "screen", "full"):
        sp = sub.add_parser(name)
        if name in ("screen", "full"):
            sp.add_argument("--configs", required=True, help="comma-separated config names (see `list`)")
            sp.add_argument("--reps", type=int, default=0, help="repeats per row (default 3 screen / 1 full)")
            sp.add_argument("--tiers", default="", help="restrict to tiers (comma list)")
            sp.add_argument("--limit", type=int, default=0)
            sp.add_argument("--asr-from", default="", help="realtime config whose recorded transcripts feed *:asrtxt configs")
        if name in ("screen", "full", "synth"):
            sp.add_argument("--concurrency", type=int, default=MAX_CONCURRENCY, help=f"<= {MAX_CONCURRENCY}")
    args = ap.parse_args(argv)
    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
