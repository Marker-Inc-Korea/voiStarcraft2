"""Load-robust per-stage latency trace of the REAL voice client (electron/voice.js run in plain Node, no Electron).

Per utterance the harness (latency_trace_client.js) streams the TTS audio at real-time pace into the real Realtime proxy
(`curl/8.7.1` UA, certifi CA, key auth only on proxy 429) and into a local `web_gui --dry-run`, and stamps (one clock):

  audio_end           last voiced 50 ms chunk delivered (RMS gate on the clip)
  speech_stopped      server VAD event                       a = speech_stopped - audio_end      (VAD silence wait + net)
  transcript          input transcription completed          b = transcript - speech_stopped     (ASR delay)
  tool_done           function_call_arguments.done           c = tool_done - speech_stopped      (model, model path)
  POST start / end    /api/realtime/intent                   e = POST start - decision event     (client time)
                                                             d = POST end - POST start           (our server; backend_handle inside)
  compile written     latest_modulation_compile_result.json  f = compile - POST end              (async publish)

  end-to-end ack      = POST end   - audio_end   (the command is accepted by the executor)
  end-to-end compile  = compile    - audio_end   (compile result written)

CPU steal proxy: wall time of a fixed 50 ms CPU-time busy loop (1.0 = unloaded) before/after every utterance. CPU-bound stages
(d, e, f) are divided by the mean steal factor for the 'load-normalized' columns; a, b, c are network/model time and are not
rescaled. Absolute claims are refused when load5 > 20 (the summary says so; relative A/B numbers are only valid inside a paired
run, where two configs are executed CONCURRENTLY against two web_gui instances).

  # run: configs are `name:key=value,...` (silence=450, endpoint=energy, asr=whisper-1, ...), all concurrent
  python latency_trace.py run --out docs/bench-results/latency_trace --reps 3 --configs base:silence=450 vad350:silence=350
  python latency_trace.py posts --out ...
  python latency_trace.py cold --out ... --n 4
  python latency_trace.py analyze --out docs/bench-results/latency_trace
"""

from __future__ import annotations

import argparse
import json
import os
import random
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

NODE = os.environ.get("NODE", "node")
PY = sys.executable
LOAD_LIMIT = 20.0
VARIANTS = ("v0", "v1", "v2")

# --- stratified 40-row subset: 8 rule-matched (4 emergency + 4 micro), 8 micro non-rule, 12 operation, 12 macro ---------
RULE_ROWS = ["eme-001", "eme-009", "eme-013", "eme-019", "mic-001", "mic-005", "mic-007", "mic-028"]
MICRO_NONRULE = ["mic-008", "mic-010", "mic-013", "mic-015", "mic-018", "mic-020", "mic-021", "mic-025"]


def pick_subset() -> list[dict]:
    rows = [json.loads(line) for line in (HERE / "corpus.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    by_id = {r["id"]: r for r in rows}
    rng = random.Random(20261106)
    ops = sorted((r["id"] for r in rows if r["tier"] == "operation"))
    macs = sorted((r["id"] for r in rows if r["tier"] == "macro"))
    ids = RULE_ROWS + MICRO_NONRULE + rng.sample(ops, 12) + rng.sample(macs, 12)
    return [by_id[i] for i in ids]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def uptime() -> str:
    return subprocess.run(["uptime"], capture_output=True, text=True).stdout.strip()


def steal_probe() -> float:
    """wall/CPU ratio of a 50 ms CPU-time busy loop (1.0 = no contention)."""
    c0, w0 = time.process_time_ns(), time.perf_counter_ns()
    while time.process_time_ns() - c0 < 50_000_000:
        pass
    return round((time.perf_counter_ns() - w0) / 50_000_000, 2)


def load5() -> float:
    return os.getloadavg()[1]


def net_rtt(n: int = 6) -> dict:
    """Network floor to the proxy by curl timing: TCP connect and TLS handshake (1 and 3 RTT) + TTFB of GET /v1/models."""
    base = os.environ.get("VOI_REALTIME_BASE_URL", "https://proxy.nomadamas.org/v1").rstrip("/")
    key = next((os.environ[k] for k in ("VOI_REALTIME_API_KEY", "MYPROXY_API_KEY", "OPENAI_API_KEY") if os.environ.get(k)), "")
    rows = []
    for _ in range(n):
        out = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-A", "curl/8.7.1", "-H", f"Authorization: Bearer {key}", "-w",
             "%{time_connect} %{time_appconnect} %{time_starttransfer} %{http_code}", base + "/models"],
            capture_output=True, text=True, timeout=30).stdout.split()
        if len(out) == 4:
            rows.append({"connect_ms": float(out[0]) * 1000, "tls_ms": float(out[1]) * 1000, "ttfb_ms": float(out[2]) * 1000, "code": out[3]})
    def med(k):
        v = [r[k] for r in rows]
        return round(statistics.median(v), 1) if v else None
    return {"n": len(rows), "connect_ms_p50": med("connect_ms"), "tls_ms_p50": med("tls_ms"), "ttfb_ms_p50": med("ttfb_ms"),
            "uptime": uptime(), "steal": steal_probe()}


def start_gui(blackboard: str, silence: int) -> tuple[subprocess.Popen, int]:
    port = free_port()
    env = {**os.environ, "VOI_MICROMACHINE_BLACKBOARD_DIR": blackboard, "VOI_REALTIME_VAD_SILENCE_MS": str(silence), "PYTHONPATH": str(HERE.parents[1])}
    proc = subprocess.Popen([PY, "-m", "starcraft_commander.web_gui", "--dry-run", "--port", str(port)], env=env, cwd=str(HERE.parents[1]),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return proc, port
        except OSError:
            time.sleep(0.3)
    proc.kill()
    raise SystemExit("web_gui did not start")


def parse_config(spec: str) -> tuple[str, dict]:
    name, _, rest = spec.partition(":")
    cfg: dict = {}
    for part in filter(None, rest.split(",")):
        k, _, v = part.partition("=")
        cfg[k] = int(v) if v.lstrip("-").isdigit() else (float(v) if v.replace(".", "", 1).isdigit() else v)
    return name, cfg


def session_body_file(directory: Path) -> str:
    import starcraft_commander.realtime_intent as RI

    body = RI._session_body(RI.load_upstream_config(), transcription=True, text_output=True, transcription_hints=False)
    path = directory / "session_body.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return str(path)


def audio_files(row_id: str) -> list[str]:
    return [str(HERE / "audio_cache" / f"{row_id}.{v}.pcm") for v in VARIANTS]


def run_jobs(args, mode: str) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    env_info = {"uptime": uptime(), "load": os.getloadavg(), "steal": [steal_probe() for _ in range(3)], "net": net_rtt()}
    (out / f"env_{mode}_{int(time.time())}.json").write_text(json.dumps(env_info, indent=1))
    rows = pick_subset()
    if args.limit:
        rows = rows[:: max(1, len(rows) // args.limit)][: args.limit]
    tmp = Path(tempfile.mkdtemp(prefix="latency_trace_"))
    sbody = session_body_file(tmp)
    guis, procs = [], []
    try:
        for spec in args.configs:
            name, cfg = parse_config(spec)
            bb = str(tmp / f"bb_{name}")
            os.makedirs(bb, exist_ok=True)
            gui, port = start_gui(bb, int(cfg.get("silence", 450)))
            guis.append(gui)
            job = {"label": name, "base": f"http://127.0.0.1:{port}", "out": str(out / f"{mode}_{name}.jsonl"), "reps": args.reps,
                   "silenceMs": int(cfg.get("silence", 450)), "endpoint": "energy" if cfg.get("endpoint") == "energy" else "server_vad",
                   "energyMs": int(cfg.get("energyms", 400)), "energyShortMs": int(cfg.get("shortms", 150)), "auth": cfg.get("auth", "mint"),
                   "sessionBody": sbody, "blackboardDir": bb, "gapMs": 500, "fastRules": cfg.get("rules", 1) != 0,
                   "prefs": {k: cfg[k] for k in ("earlyRule",) if k in cfg}, "env": {"VOI_TRANSCRIBE_MODEL": cfg["asr"]} if "asr" in cfg else {},
                   "rows": [{"id": r["id"], "tier": r["tier"], "text": r["text"], "files": audio_files(r["id"])} for r in rows]}
            if mode == "cold":
                job.update(coldN=args.n, prefetch=bool(cfg.get("prefetch", 0)))
            if mode == "posts":
                job.update(mode="posts", n=args.n)
            jf = tmp / f"job_{name}.json"
            jf.write_text(json.dumps(job), encoding="utf-8")
            Path(job["out"]).unlink(missing_ok=True)
            procs.append((name, subprocess.Popen([NODE, str(HERE / "latency_trace_client.js"), "--job", str(jf)], env=os.environ.copy())))
        for name, p in procs:
            p.wait()
            print(f"{name}: node exit {p.returncode}", flush=True)
    finally:
        for g in guis:
            g.terminate()
        for g in guis:
            try:
                g.wait(10)
            except subprocess.TimeoutExpired:
                g.kill()
    after = {"uptime": uptime(), "load": os.getloadavg(), "steal": [steal_probe() for _ in range(3)]}
    (out / f"env_{mode}_after_{int(time.time())}.json").write_text(json.dumps(after, indent=1))


# ============================================================================================================================
# analysis
# ============================================================================================================================

def pct(vals, q):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    k = (len(v) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 1)


def stages(u: dict) -> dict | None:
    ev = u["ev"]
    if not u.get("intents"):
        return None
    first = u["intents"][0]
    path = first.get("path") or "model"
    t_end = u["t_stream0"] + u["voiced"]["endMs"]
    posts = [r for r in u["requests"] if r["path"] == "/api/realtime/intent" and "t1" in r and isinstance(r.get("body"), dict) and r["body"].get("path") == path]
    if not posts:
        return None
    post = posts[0]
    decision_event = ev.get("transcript") if path == "rule" else ev.get("tool_done")
    early = first.get("early")  # early-fire (delta) path decides on a partial transcript
    if path == "rule" and early and early.get("t") is not None:
        decision_event = early["t"]
    if decision_event is None:
        return None
    sstop = ev.get("speech_stopped") if ev.get("speech_stopped") is not None else ev.get("client_endpoint")  # energy endpointing stamps its own
    steal = statistics.fmean([u.get("steal_before") or 1.0, u.get("steal_after") or 1.0])
    s = {
        "a_vad": sstop - t_end if sstop is not None else None,
        "b_asr": ev["transcript"] - sstop if ev.get("transcript") is not None and sstop is not None else None,
        "c_model": ev["tool_done"] - sstop if ev.get("tool_done") is not None and sstop is not None else None,
        "e_client": post["t0"] - decision_event,
        "d_server": post["t1"] - post["t0"],
        "d_backend": post.get("backend_handle"),
        "f_publish": (first["compile"]["t"] - post["t1"]) if first.get("compile") else None,
        "e2e_ack": post["t1"] - t_end,
        "e2e_compile": (first["compile"]["t"] - t_end) if first.get("compile") else None,
        "steal": steal,
        "path": path,
        "late_p95": pct(u.get("late", []), 0.95),
    }
    # time spent waiting for something external (network / model / VAD) vs CPU on this box
    local = (s["e_client"] or 0) + (s["d_server"] or 0) + (s["f_publish"] or 0)
    for key in ("e2e_ack", "e2e_compile"):
        if s[key] is not None:
            s[key + "_norm"] = s[key] - local * (1 - 1 / steal) if key == "e2e_compile" else s[key] - ((s["e_client"] or 0) + (s["d_server"] or 0)) * (1 - 1 / steal)
    return s


def accuracy(u: dict, rows_by_id: dict) -> dict:
    import importlib
    os.environ["BENCH_SCHEMA_MODULE"] = "shipped_schema"
    scoring = importlib.import_module("scoring")
    row = rows_by_id[u["id"]]
    intents = u.get("intents") or []
    if not intents:
        sc = scoring.score_prediction(row, None, "no intent", transcript=u.get("asr_text") or "")
        return {"sem": sc["sem"], "safe": sc["safe"], "pred_layer": sc["pred_layer"], "executed": False}
    first = intents[0]
    args = first.get("tool_arguments")
    sc = scoring.score_prediction(row, args if first.get("path") == "model" else None, first.get("error") or None, transcript=first.get("transcript") or u.get("asr_text") or "")
    return {"sem": sc["sem"], "safe": sc["safe"], "pred_layer": sc["pred_layer"], "executed": bool(first.get("accepted"))}


def group_key(u: dict, s: dict | None) -> str:
    path = s["path"] if s else "none"
    return f"{u['tier']}/{path}"


def tier_class(u: dict, s: dict) -> str:
    """micro+emergency as the user hears them: rule-matched words vs model-path."""
    if s["path"] == "rule":
        return "rule words (emergency+micro)"
    return {"emergency": "emergency (model path)", "micro": "micro (model path)"}.get(u["tier"], u["tier"])


def load_utts(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def summarize(utts: list[dict]) -> dict:
    rows = {json.loads(line)["id"]: json.loads(line) for line in (HERE / "corpus.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()}
    utts_u = [u for u in utts if u.get("kind") == "utterance"]
    meta = next((u for u in utts if u.get("kind") == "meta"), {})
    ready = next((u for u in utts if u.get("kind") == "ready"), {})
    per: dict = {}
    for u in utts_u:
        s = stages(u)
        acc = accuracy(u, rows)
        cls = tier_class(u, s) if s else f"{u['tier']} (no intent)"
        per.setdefault(cls, []).append((u, s, acc))
    out = {"label": meta.get("label"), "n_utterances": len(utts_u), "ready": ready, "tiers": {}}
    keys = ["a_vad", "b_asr", "c_model", "e_client", "d_server", "d_backend", "f_publish", "e2e_ack", "e2e_compile", "e2e_ack_norm", "e2e_compile_norm"]
    for cls, items in sorted(per.items()):
        ss = [s for _u, s, _a in items if s]
        entry = {"n": len(items), "n_with_intent": len(ss),
                 "sem_acc": round(sum(a["sem"] for *_x, a in items) / len(items), 3),
                 "unsafe": round(sum(1 for *_x, a in items if not a["safe"]) / len(items), 3),
                 "no_intent": sum(1 for _u, s, _a in items if not s)}
        for k in keys:
            vals = [s.get(k) for s in ss]
            entry[k] = {"p50": pct(vals, 0.5), "p95": pct(vals, 0.95), "n": sum(v is not None for v in vals)}
        entry["steal_p50"] = pct([s["steal"] for s in ss], 0.5)
        stage_p50 = {k: entry[k]["p50"] for k in ("a_vad", "b_asr", "c_model", "e_client", "d_server", "f_publish") if entry[k]["p50"] is not None}
        # dominant: for the model path ASR runs concurrently with the model, so only a/c/e/d/f are on the critical path
        crit = {k: v for k, v in stage_p50.items() if not (k == "b_asr" and any(s["path"] == "model" for s in ss))}
        entry["dominant_stage"] = max(crit, key=crit.get) if crit else None
        out["tiers"][cls] = entry
    return out


def md_tables(summaries: list[dict], targets: dict | None = None) -> str:
    lines = []
    for sm in summaries:
        lines.append(f"\n#### config `{sm['label']}`  (n={sm['n_utterances']})\n")
        lines.append("| tier class | n | sem acc | unsafe | a VAD p50/p95 | b ASR p50/p95 | c model p50/p95 | e client p50 | d server p50 (backend) | f publish p50 | e2e ack p50/p95 | e2e compile p50/p95 | dominant |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for cls, e in sm["tiers"].items():
            def pp(k):
                return f"{e[k]['p50']}/{e[k]['p95']}" if e[k]["p50"] is not None else "-"
            lines.append(f"| {cls} | {e['n']} | {e['sem_acc']:.0%} | {e['unsafe']:.0%} | {pp('a_vad')} | {pp('b_asr')} | {pp('c_model')} | {e['e_client']['p50']} | "
                         f"{e['d_server']['p50']} ({e['d_backend']['p50']}) | {e['f_publish']['p50']} | {pp('e2e_ack')} | {pp('e2e_compile')} | {e['dominant_stage']} |")
    return "\n".join(lines)


def cmd_analyze(args) -> None:
    out = Path(args.out)
    summaries = []
    for f in sorted(out.glob("run_*.jsonl")):
        summaries.append(summarize(load_utts(f)))
    envs = [json.loads(p.read_text()) for p in sorted(out.glob("env_*.json"))]
    result = {"summaries": summaries, "envs": envs, "load_limit": LOAD_LIMIT}
    (out / "analysis.json").write_text(json.dumps(result, ensure_ascii=False, indent=1))
    print(md_tables(summaries))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("run", "cold", "posts", "analyze"))
    ap.add_argument("--out", default=str(HERE.parents[1] / "docs" / "bench-results" / "latency_trace"))
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--n", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="smoke: use only N evenly spaced rows")
    ap.add_argument("--configs", nargs="*", default=["base:silence=450"])
    args = ap.parse_args(argv)
    if args.cmd == "analyze":
        cmd_analyze(args)
        return
    if load5() > LOAD_LIMIT:
        print(f"WARNING load5={load5():.1f} > {LOAD_LIMIT}: results are relative-only (absolute claims refused)", flush=True)
    run_jobs(args, {"run": "run", "cold": "cold", "posts": "posts"}[args.cmd])


if __name__ == "__main__":
    main()
