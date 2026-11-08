"""Build scripts/bench/intent_corpus.jsonl: utterance table (intent_utterances.py) + mechanically derived gold.

Gold = ``intent_catalog.resolve(select, state)`` over s2client_api_enums.json + python-sc2 tables; only negatives carry
a hand-annotated expectation (refuse / ask), and ambiguous rows are relaxed from ``execute`` to ``choose_or_ask``.

Row schema::

    {"id", "text", "lang", "tier", "tags", "state_name", "state": {structures, researched, minerals, gas, energy},
     "select": {...annotation...}, "gold": {"expect", "acceptable", "exact", "why"},
     "measure": {"latency_ms": null, "tokens": null}}     # filled by runs, never claimed

Run:  python3 scripts/bench/build_intent_corpus.py          (writes the file)
      python3 scripts/bench/build_intent_corpus.py --check  (exit 1 when the file is stale)
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

import intent_catalog as C  # noqa: E402
import intent_utterances as U  # noqa: E402

CORPUS_PATH = HERE / "intent_corpus.jsonl"


def _row(n: int, spec: dict) -> dict:
    state = copy.deepcopy(U.STATES[spec["state"]])
    state["structures"] = sorted(state["structures"])
    state["researched"] = sorted(state["researched"])
    if spec.get("expect"):
        gold = {"expect": spec["expect"], "acceptable": [], "exact": None, "why": ["annotated: never execute"]}
    else:
        gold = C.resolve(spec["select"], state)
        if spec["tier"] == "ambiguous":
            if gold["expect"] == "execute":
                gold["expect"] = "choose_or_ask"
            gold["exact"] = None
    return {
        "id": f"{spec['tier'][:3]}-{n:03d}",
        "text": spec["text"],
        "lang": spec["lang"],
        "tier": spec["tier"],
        "tags": spec["tags"],
        "state_name": spec["state"],
        "state": state,
        "select": spec["select"],
        "gold": gold,
        "measure": {"latency_ms": None, "tokens": None},
    }


def build_rows() -> list[dict]:
    counters: dict[str, int] = {}
    rows = []
    for spec in U.UTTERANCES:
        counters[spec["tier"]] = counters.get(spec["tier"], 0) + 1
        rows.append(_row(counters[spec["tier"]], spec))
    return rows


def render(rows: list[dict]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)


def main(argv: list[str]) -> int:
    text = render(build_rows())
    if "--check" in argv:
        return 0 if CORPUS_PATH.exists() and CORPUS_PATH.read_text(encoding="utf-8") == text else 1
    CORPUS_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {CORPUS_PATH} ({text.count(chr(10))} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
