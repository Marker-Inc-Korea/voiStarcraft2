"""Recompute every round-3 latency number from the RAW jsonl (nothing is copied from an earlier summary).

  python round3_report.py            -> docs/bench-results/round3_stage_tables.json  (+ markdown on stdout)

Inputs (all under docs/bench-results/): latency_trace/{r1,r2,r3}/run_*.jsonl, latency_trace/posts, latency_trace/cold,
early_fire_replay.json (recomputed here from asr_words_probe.json by early_fire_replay.py), lifecycle_skip_ab.json.
Every table carries the load5 of the run it came from: with load5 > 20 the absolute milliseconds are LOAD-INFLATED and only relative
comparisons inside one run are claimed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import latency_trace as T  # noqa: E402

RES = HERE.parents[1] / "docs" / "bench-results"
TR = RES / "latency_trace"


def env_load(run_dir: Path) -> dict:
    out = {}
    for p in sorted(run_dir.glob("env_*.json")):
        d = json.loads(p.read_text())
        out["after" if "after" in p.name else "before"] = d.get("load")
    return out


def run_summary(run_dir: Path) -> dict:
    sums = {}
    for f in sorted(run_dir.glob("run_*.jsonl")):
        s = T.summarize(T.load_utts(f))
        sums[s["label"]] = s
    return {"load": env_load(run_dir), "configs": sums}


def pooled_by_silence(dirs: list[Path]) -> dict:
    """accuracy / unsafe per tier class pooled over every run that used the same server_vad silence (accuracy is load independent)."""
    rows = {json.loads(line)["id"]: json.loads(line) for line in (HERE / "corpus.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()}
    acc: dict = {}
    for d in dirs:
        for f in sorted(d.glob("run_*.jsonl")):
            utts = T.load_utts(f)
            meta = next((u for u in utts if u.get("kind") == "meta"), {})
            job = meta.get("job", {})
            if job.get("endpoint") == "energy":
                continue
            sil = job.get("silenceMs")
            for u in utts:
                if u.get("kind") != "utterance":
                    continue
                s = T.stages(u)
                a = T.accuracy(u, rows)
                cls = T.tier_class(u, s) if s else f"{u['tier']} (no intent)"
                bucket = acc.setdefault(str(sil), {}).setdefault(cls, {"n": 0, "sem": 0, "unsafe": 0, "no_intent": 0})
                bucket["n"] += 1
                bucket["sem"] += int(bool(a["sem"]))
                bucket["unsafe"] += int(not a["safe"])
                bucket["no_intent"] += int(s is None)
    for sil in acc.values():
        for b in sil.values():
            b["sem_acc"] = round(b["sem"] / b["n"], 3)
            b["unsafe_rate"] = round(b["unsafe"] / b["n"], 3)
    return acc


def main() -> None:
    dirs = [TR / "r1", TR / "r2", TR / "r3"]
    result = {"runs": {d.name: run_summary(d) for d in dirs if d.exists()}, "pooled_accuracy_by_silence_ms": pooled_by_silence([d for d in dirs if d.exists()])}
    (RES / "round3_stage_tables.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    for name, run in result["runs"].items():
        print(f"\n### run {name}  load(before/after)={run['load']}")
        print(T.md_tables(list(run["configs"].values())))
    print("\n### pooled accuracy by server_vad silence (all runs)")
    for sil, tiers in sorted(result["pooled_accuracy_by_silence_ms"].items()):
        print(sil, {k: f"{v['sem']}/{v['n']} sem, unsafe {v['unsafe']}" for k, v in sorted(tiers.items())})


if __name__ == "__main__":
    main()
