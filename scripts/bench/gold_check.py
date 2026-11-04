"""Prove every corpus gold converts to a schema-legal PolicyModulationVector.

For each row the canonical gold is rendered to submit_command args, validated against
the tool JSON schema, mapped to a provider_output and passed through
PolicyModulationVector.from_mapping. Clarification golds must be recognised as
non-executing. Also checks that the derived layer equals the gold layer and that
gold scores itself as correct (sem + strict) with scoring.py.

Run: .venv/bin/python scripts/bench/gold_check.py
"""

from __future__ import annotations

import sys

from common import S, load_corpus
from scoring import gold_to_args, score_prediction


def main() -> int:
    from starcraft_commander.policy_modulation import PolicyModulationVector

    rows = load_corpus()
    bad = []
    layer_counts: dict[str, int] = {}
    for row in rows:
        gold = row["gold"]
        args = gold_to_args(gold, row["text"])
        errs = S.validate_args(args)
        layer = S.derive_layer(args)
        problem = None
        if errs:
            problem = "schema: " + "; ".join(errs[:3])
        elif layer != gold["layer"]:
            problem = f"derived layer {layer} != gold {gold['layer']}"
        elif layer == "clarification":
            pass
        else:
            try:
                vec = PolicyModulationVector.from_mapping(S.tool_args_to_provider_output(args))
                if vec.command_layer.value != layer:
                    problem = f"vector layer {vec.command_layer.value} != {layer}"
                if layer == "operation" and "operations" in gold and len(vec.operations) != len(gold["operations"]):
                    problem = "operation count mismatch"
            except ValueError as exc:
                problem = f"from_mapping: {exc}"
        if problem is None:
            sc = score_prediction(row, args)
            if not (sc["sem"] and sc["strict"]) and layer != "clarification":
                problem = f"gold does not self-score: {sc}"
            elif layer == "clarification" and not sc["sem"]:
                problem = f"clarification gold does not self-score: {sc}"
        if problem:
            bad.append((row["id"], row["text"], problem))
        layer_counts[gold["layer"]] = layer_counts.get(gold["layer"], 0) + 1
    print(f"rows={len(rows)} layers={layer_counts}")
    for rid, text, why in bad:
        print(f"FAIL {rid} {text!r}: {why}")
    print("gold_check:", "FAIL" if bad else "PASS", f"({len(rows) - len(bad)}/{len(rows)} legal)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
