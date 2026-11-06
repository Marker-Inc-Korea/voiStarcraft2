"""Paired A/B of the web_gui `refresh_lifecycle` skip: two `web_gui --dry-run` servers (a pristine tree and this tree) are driven
CONCURRENTLY with the same sequence of rule intents, so machine load hits both equally. Per request: POST round trip and
"compile after return" (POST end -> latest_modulation_compile_result.json carries this update_id). The cost the skip removes
grows with the number of commands already in the history, so the result is reported per history position.

  python lifecycle_skip_ab.py --pristine /tmp/r3lat-pristine --n 40 --out docs/bench-results/lifecycle_skip_ab.json
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
UTTERANCES = ["후퇴", "스팀팩 써", "시즈 모드", "홀드", "멈춰", "스팀팩"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start(tree: Path, blackboard: str) -> tuple[subprocess.Popen, int]:
    port = free_port()
    env = {**os.environ, "VOI_MICROMACHINE_BLACKBOARD_DIR": blackboard, "PYTHONPATH": str(tree)}
    proc = subprocess.Popen([sys.executable, "-m", "starcraft_commander.web_gui", "--dry-run", "--port", str(port)], env=env, cwd=str(tree),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 90
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return proc, port
        except OSError:
            time.sleep(0.3)
    proc.kill()
    raise SystemExit(f"web_gui at {tree} did not start")


def drive(port: int, blackboard: str, n: int, out: list) -> None:
    result_file = Path(blackboard) / "latest_modulation_compile_result.json"
    for i in range(n):
        text = UTTERANCES[i % len(UTTERANCES)]
        body = json.dumps({"transcript": text, "fast": True, "path": "rule", "latency_ms": {}}).encode()
        t0 = time.perf_counter()
        with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/api/realtime/intent", body, {"content-type": "application/json"}), timeout=30) as r:
            payload = json.load(r)
        t1 = time.perf_counter()
        update_id = payload.get("update_id")
        compile_ms = None
        while update_id and time.perf_counter() - t1 < 15:
            try:
                if json.loads(result_file.read_text(encoding="utf-8")).get("update_id") == update_id:
                    compile_ms = (time.perf_counter() - t1) * 1000
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.002)
        out.append({"i": i, "text": text, "post_ms": (t1 - t0) * 1000, "compile_after_return_ms": compile_ms, "status": payload.get("status")})
        time.sleep(0.15)


def pct(vals: list[float], q: float):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    k = (len(v) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pristine", required=True, help="tree of the baseline code (git archive of origin/main)")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--out", default=str(ROOT / "docs" / "bench-results" / "lifecycle_skip_ab.json"))
    args = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="lifecycle_ab_"))
    arms = {"baseline": Path(args.pristine), "skip": ROOT}
    procs, ports, bbs = {}, {}, {}
    uptime_before = subprocess.run(["uptime"], capture_output=True, text=True).stdout.strip()
    try:
        for name, tree in arms.items():
            bbs[name] = str(tmp / f"bb_{name}")
            os.makedirs(bbs[name])
            procs[name], ports[name] = start(tree, bbs[name])
        results = {name: [] for name in arms}
        threads = [threading.Thread(target=drive, args=(ports[name], bbs[name], args.n, results[name])) for name in arms]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        for p in procs.values():
            p.terminate()
        for p in procs.values():
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
    uptime_after = subprocess.run(["uptime"], capture_output=True, text=True).stdout.strip()
    summary = {"n": args.n, "uptime_before": uptime_before, "uptime_after": uptime_after, "arms": {}}
    for name, rows in results.items():
        buckets = {}
        for label, sl in (("history 0-9", slice(0, 10)), ("history 10-19", slice(10, 20)), ("history 20-29", slice(20, 30)), ("history 30+", slice(30, None))):
            chunk = rows[sl]
            buckets[label] = {"n": len(chunk), "post_ms_p50": pct([r["post_ms"] for r in chunk], 0.5), "compile_after_return_ms_p50": pct([r["compile_after_return_ms"] for r in chunk], 0.5),
                              "compile_after_return_ms_p95": pct([r["compile_after_return_ms"] for r in chunk], 0.95)}
        summary["arms"][name] = {"buckets": buckets, "post_ms_p50": pct([r["post_ms"] for r in rows], 0.5), "compile_after_return_ms_p50": pct([r["compile_after_return_ms"] for r in rows], 0.5),
                                 "compile_after_return_ms_p95": pct([r["compile_after_return_ms"] for r in rows], 0.95), "missing_compile": sum(1 for r in rows if r["compile_after_return_ms"] is None),
                                 "statuses": sorted({str(r["status"]) for r in rows}), "rows": rows}
    Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "rows"} for k, v in summary["arms"].items()}, ensure_ascii=False, indent=1))
    print(uptime_before, "|", uptime_after)


if __name__ == "__main__":
    main()
