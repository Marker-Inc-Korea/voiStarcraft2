"""Assemble docs/voice-intent-benchmark.md from the narrative template + generated tables.
Run after report.py --stage screen/full:  .venv/bin/python scripts/bench/make_doc.py"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RES = ROOT / "docs" / "bench-results"
tmpl = (Path(__file__).with_name("doc_template.md")).read_text(encoding="utf-8")
screen = json.loads((RES / "summary_screen.json").read_text())["configs"]
full = json.loads((RES / "summary_full.json").read_text())["configs"]


def single_rows(cfgs, prefix=("rt:", "tx:", "fp:")):
    rows = []
    for n, c in sorted(cfgs.items(), key=lambda kv: -kv[1]["all"]["sem"]):
        if not n.startswith(prefix):
            continue
        a = c["all"]
        p = f"{a['p50_ms'] / 1000:.1f}/{a['p95_ms'] / 1000:.1f}" if a.get("p50_ms") else "-"
        adv = c.get("adversarial", {}).get("sem")
        rows.append(f"| `{n}` | {a['n']} | {100 * a['sem']:.0f}% | {100 * a['strict']:.0f}% | {100 * a['schema_valid']:.0f}% | "
                    f"{100 * a['unsafe']:.1f}% | {100 * adv:.0f}% | {p} | {a['mean_tokens']:.0f} |"
                    if adv is not None else f"| `{n}` | {a['n']} | {100 * a['sem']:.0f}% | - | - | - | - | {p} | - |")
    head = "| config | n calls | sem | strict | schema-valid | unsafe | adversarial sem | p50/p95 s | tok/call |\n|---|---|---|---|---|---|---|---|---|"
    return head + "\n" + "\n".join(rows)


out = tmpl
out = out.replace("{{SCREEN_SINGLE}}", single_rows(screen))
out = out.replace("{{FULL_TABLE}}", (RES / "tables_full.md").read_text().split("\n", 3)[3].strip())
out = out.replace("{{MISSES_SELECTED}}", (RES / "misses_full_selected.md").read_text())
out = out.replace("{{MISSES_AUDIO}}", (RES / "misses_full_audio_native.md").read_text())
(ROOT / "docs" / "voice-intent-benchmark.md").write_text(out, encoding="utf-8")
print("doc written", len(out))
