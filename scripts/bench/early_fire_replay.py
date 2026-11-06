"""Replay candidate (a): fire a rule on a transcription *delta* instead of the completed transcript.

Input: the recorded real-ASR partials of docs/bench-results/asr_words_probe.json (276 calls, every delta with its arrival time).
Guard under test (the one the round-3 brief specified):
  fire at the first cumulative delta text D_i such that
    1. electron/fast_rules.js matchFastRule(D_i) hits (a COMPLETE rule phrase, un-negated by the rule's own NEG),
    2. no negation token (fast_rules NEG) is in D_i                      [implied by 1, kept explicit],
    3. stability: no further delta arrives within STABLE_MS (default 120) of D_i -> fire at t_i + STABLE_MS.
A fire is CORRECT when the FINAL transcript hits the same family/action, PREMATURE when the final does not hit (e.g.
'후퇴' then '후퇴하지 마') or hits a different action. Also reports the time gained versus acting on `completed`.

  python early_fire_replay.py [--stable-ms 120]  -> docs/bench-results/early_fire_replay.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE.parents[1] / "docs" / "bench-results"


class RuleWorker:
    def __init__(self) -> None:
        self.p = subprocess.Popen(["node", str(HERE / "fast_rule_timer.js")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.cache: dict[str, dict] = {}

    def match(self, text: str) -> dict:
        if text not in self.cache:
            assert self.p.stdin and self.p.stdout
            self.p.stdin.write(json.dumps({"id": "x", "text": text}, ensure_ascii=False) + "\n")
            self.p.stdin.flush()
            self.cache[text] = json.loads(self.p.stdout.readline())
        return self.cache[text]

    def close(self) -> None:
        if self.p.stdin:
            self.p.stdin.close()
        self.p.wait(5)


def pct(vals: list[float], q: float):
    v = sorted(vals)
    if not v:
        return None
    k = (len(v) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 1)


def replay(stable_ms: float) -> dict:
    d = json.loads((RESULTS / "asr_words_probe.json").read_text(encoding="utf-8"))
    w = RuleWorker()
    fires, premature, correct, gains, held_back = [], [], [], [], 0
    final_rule_calls = 0
    for c in d["calls"]:
        deltas = c.get("deltas") or []
        final = c.get("transcript") or ""
        if not deltas or not final or c.get("t_completed_ms") is None:
            continue
        fin = w.match(final)
        if fin["hit"]:
            final_rule_calls += 1
        cum = ""
        fired = None
        for i, dl in enumerate(deltas):
            cum += dl["d"]
            m = w.match(cum)
            if not m["hit"]:
                continue
            nxt = deltas[i + 1]["t_ms"] if i + 1 < len(deltas) else c["t_completed_ms"]
            if nxt - dl["t_ms"] >= stable_ms:  # nothing else arrived for `stable_ms` -> stable
                fired = {"text": c["text"], "partial": cum, "final": final, "t_fire": dl["t_ms"] + stable_ms, "t_completed": c["t_completed_ms"],
                         "same_action": fin["hit"] and fin["action"] == m["action"] and fin["family"] == m["family"], "gap_after_ms": round(nxt - dl["t_ms"], 1)}
                break
            held_back += 1
        if fired:
            fires.append(fired)
            (correct if fired["same_action"] else premature).append(fired)
            if fired["same_action"]:
                gains.append(fired["t_completed"] - fired["t_fire"])
    w.close()
    return {
        "stable_ms": stable_ms,
        "calls_with_deltas": sum(1 for c in d["calls"] if c.get("deltas")),
        "final_rule_hits": final_rule_calls,
        "fires": len(fires),
        "correct_fires": len(correct),
        "premature_fires": len(premature),
        "premature_examples": [{k: p[k] for k in ("text", "partial", "final", "gap_after_ms")} for p in premature[:12]],
        "gain_vs_completed_ms": {"n": len(gains), "p50": pct(gains, 0.5), "p95": pct(gains, 0.95), "mean": round(statistics.fmean(gains), 1) if gains else None,
                                  "frac_negative_or_zero": round(sum(1 for g in gains if g <= 0) / len(gains), 3) if gains else None},
        "stability_rejected_candidates": held_back,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stable-ms", type=float, default=120)
    ap.add_argument("--out", default=str(RESULTS / "early_fire_replay.json"))
    a = ap.parse_args()
    results = [replay(s) for s in (0, 120, 250, 400)] if a.stable_ms == 120 else [replay(a.stable_ms)]
    Path(a.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    for r in results:
        print(json.dumps({k: v for k, v in r.items() if k != "premature_examples"}, ensure_ascii=False))
    print(json.dumps(results[1 if len(results) > 1 else 0]["premature_examples"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
