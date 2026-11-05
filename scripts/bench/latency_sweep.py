"""Interleaved latency sweep driver (item 4). ALL configs share ONE semaphore and are scheduled round-robin
(rep-major, then row, then config) so machine / proxy load hits every config equally.

  BENCH_SCHEMA_MODULE=shipped_schema python latency_sweep.py --configs A,B,... --reps 2 --concurrency 6 [--max-minutes 30]

Records are cached exactly like run.py (runs_shipped/<config>/<row>.<rep>.json, resumable). Each invocation appends
{start, end, uptime_before, uptime_after, calls_done} to runs_shipped/_sweep_invocations.json. With --max-minutes the
scheduler stops starting new calls after the deadline (in-flight calls finish); incomplete (row, rep) grids are dropped by
latency_report.py, never extrapolated.

Config name grammar (rt:<model>:<endpoint>:<variant>):  endpoint = vadNNN | semhigh | semauto;
variant = (both|lean1|lean2|shipped|rules|fewshot)[_h][_tcn]   (see shipped_schema.parse_variant).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import random
import subprocess
import sys
import time

import common
from common import load_corpus
import pipelines as P
from run import MAX_CONCURRENCY, cost_units
from scoring import score_prediction

# Own run root: the older (load-inflated) runs_shipped/<config> dirs must NOT be reused as cache hits for the interleaved sweep.
SWEEP_DIR = common.RUNS_DIR / "sweep_20261105"
INVOCATIONS = SWEEP_DIR / "_sweep_invocations.json"


def cfg_dir(name: str):
    return SWEEP_DIR / name.replace(":", "_").replace("/", "_")

FATAL = ("RESOURCE_EXHAUSTED", "model_not_found", "Individual quota", "invalid_request_error")


def uptime() -> str:
    return subprocess.run(["uptime"], capture_output=True, text=True).stdout.strip()


async def amain(args) -> int:
    rows = load_corpus()
    random.Random(20261105).shuffle(rows)  # tiers spread over the whole run, same order for every config
    names = [c for c in args.configs.split(",") if c]
    cfgs = [P.get_config(n) for n in names]
    workers = min(args.concurrency, MAX_CONCURRENCY)
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    for c in cfgs:
        cfg_dir(c.name).mkdir(parents=True, exist_ok=True)
    deadline = time.time() + args.max_minutes * 60 if args.max_minutes else None
    stats = {c.name: {"done": 0, "cached": 0, "skipped_deadline": 0, "fatal": 0} for c in cfgs}
    inv = {"configs": names, "reps": args.reps, "concurrency": workers, "start": dt.datetime.now().isoformat(timespec="seconds"),
           "uptime_before": uptime(), "max_minutes": args.max_minutes, "auth": args.auth}
    print(f"sweep: {len(rows)} rows x {args.reps} reps x {len(cfgs)} configs  uptime: {inv['uptime_before']}", flush=True)
    t_start = time.time()

    async def premint(cfg):
        """--auth mint: mint the NEXT call's secret while the current call runs. --auth direct: no mint at all (the proxy
        answers 429 realtime_client_secret_capacity_exhausted under load; every call then uses the long-lived key + the SAME
        session via session.update, so all configs are treated identically). Session setup happens before the timer."""
        if args.auth == "direct":
            return {"token": None, "info": {"mint_429": 0, "mint_ms": None, "mint_skipped_direct": True}}
        try:
            tok, info = await P._mint_shipped(cfg)
            return {"token": tok, "info": info}
        except Exception:  # noqa: BLE001 - the call itself will mint again inside its retry loop
            return None

    async def one(cfg, row, rep, pre_task):
        st = stats[cfg.name]
        f = cfg_dir(cfg.name) / f"{row['id']}.{rep}.json"
        pre = await pre_task if pre_task is not None else None
        if deadline and time.time() > deadline:
            st["skipped_deadline"] += 1
            return
        P.PREMINTED.set(pre)
        res = await P.run(cfg, row, rep, None)
        err = res.get("error")
        if err and res.get("tool_args") is None and any(k in err for k in FATAL):
            st["fatal"] += 1
            return
        sc = score_prediction(row, res.get("tool_args"), err, transcript=res.get("transcript"))
        rec = {"config": cfg.name, "id": row["id"], "rep": rep, "tier": row["tier"], "text": row["text"],
               "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
               **res, **{k: v for k, v in sc.items() if k not in ("id", "tier")},
               "latency_ms": res.get("t_tool_call_ms"), "tokens_total": (res.get("tokens") or {}).get("total", 0),
               "cost_units": cost_units(res.get("tokens") or {})}
        f.write_text(json.dumps(rec, ensure_ascii=False))
        st["done"] += 1
        n = sum(s["done"] for s in stats.values())
        if n % 40 == 0:
            print(f"[{time.time() - t_start:6.0f}s] {n} calls done; " + " ".join(f"{k.split(':',2)[-1]}={v['done']+v['cached']}" for k, v in stats.items()), flush=True)

    # rep-major, row (shuffled), config: one shared queue consumed by `concurrency` workers; each worker mints the NEXT job's
    # secret while its current call runs (so at most `concurrency` measured sessions exist at any time).
    jobs: asyncio.Queue = asyncio.Queue()
    for k in range(args.reps):
        for r in rows:
            for c in cfgs:
                if (cfg_dir(c.name) / f"{r['id']}.{k}.json").exists():
                    stats[c.name]["cached"] += 1
                else:
                    jobs.put_nowait((c, r, k))

    def take():
        try:
            c, r, k = jobs.get_nowait()
        except asyncio.QueueEmpty:
            return None
        return c, r, k, asyncio.create_task(premint(c))

    async def worker():
        cur = take()
        while cur is not None:
            nxt = take() if not (deadline and time.time() > deadline) else None
            c, r, k, pt = cur
            await one(c, r, k, pt)
            cur = nxt

    await asyncio.gather(*(worker() for _ in range(min(args.concurrency, MAX_CONCURRENCY))))
    inv.update(end=dt.datetime.now().isoformat(timespec="seconds"), uptime_after=uptime(), elapsed_s=round(time.time() - t_start),
               stats=stats)
    hist = json.loads(INVOCATIONS.read_text()) if INVOCATIONS.exists() else []
    INVOCATIONS.write_text(json.dumps(hist + [inv], indent=1))
    print(json.dumps(inv, ensure_ascii=False, indent=1))
    await P.close_http()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--configs", required=True)
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--concurrency", type=int, default=MAX_CONCURRENCY)
    ap.add_argument("--auth", choices=("mint", "direct"), default="direct")
    ap.add_argument("--max-minutes", type=float, default=0.0)
    return asyncio.run(amain(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
