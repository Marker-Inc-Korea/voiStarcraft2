"""Render the command table of ``docs/sc2-command-catalog.md`` from the catalog and a MEASURED offline run.

    python -m scripts.sc2_accuracy.gen_catalog_doc            # rewrite the generated block in the doc
    python -m scripts.sc2_accuracy.gen_catalog_doc --check    # exit 1 when the doc block is stale

Status per (command, issuer) is what ``tests/test_sc2_catalog_exec.py`` observes: the catalog-derived fake world goes
through the real compile -> route -> SC2RuntimeExecutor -> python-sc2 ``BotAI.do`` -> raw protobuf path.  ``sent`` means a
raw ``ActionRawUnitCommand`` with a legal ability id and target kind left the executor.  It is request-shape evidence only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "sc2-command-catalog.md"
BEGIN = "<!-- BEGIN GENERATED TABLE -->"
END = "<!-- END GENERATED TABLE -->"


def _measure() -> dict[tuple[str, str], str]:
    sys.path.insert(0, str(ROOT / "tests"))
    sys.path.insert(0, str(ROOT))
    import test_sc2_catalog_exec as ex  # noqa: PLC0415
    from scripts.sc2_accuracy import harness  # noqa: PLC0415

    out: dict[tuple[str, str], str] = {}
    for entry, issuer in ex.PAIRS:
        try:
            bot, kwargs = ex.world(entry, issuer)
            actions = ex.lower(entry, issuer, kwargs)
            result, views, _ = harness.run_plan(bot, *actions)
            if views:
                legal = ex.header_ids_for(entry)
                ok = {v["ability_id"] for v in views} <= legal and all(harness.target_shape_ok(v) for v in views)
                out[(entry.id, issuer)] = "sent" if ok else "sent-WRONG-SHAPE"
            else:
                out[(entry.id, issuer)] = "refused: " + str(harness.action_reports(result)[0]["detail"])
        except BaseException as error:  # noqa: BLE001 - a doc generator reports, it does not hide
            out[(entry.id, issuer)] = f"not lowered: {type(error).__name__}"
    return out


def render() -> str:
    from starcraft_commander import sc2_catalog as cat  # noqa: PLC0415

    status = _measure()
    lines = [
        BEGIN,
        "",
        "| # | id | category | issuer -> exact ability | target | needs | Korean intent (alias) | offline status |",
        "|--:|----|----------|------------------------|--------|-------|-----------------------|----------------|",
    ]
    for index, entry in enumerate(cat.all_commands(), 1):
        abilities = "<br>".join(f"{i} -> {entry.abilities[i]}" for i in entry.valid_issuers)
        target = entry.target_kind + (f" ({entry.target_noun})" if entry.target_noun else "")
        needs: list[str] = []
        for issuer, (unit, lab, required) in entry.produces.items():
            if lab:
                needs.append("Tech Lab")
            if required:
                needs.append(cat.TYPE_NAME_KO.get(required, required))
            break
        if entry.upgrade_requirements and any(b or u for b, u in entry.upgrade_requirements):
            needs.append("level>1: " + ", ".join(sorted({cat.TYPE_NAME_KO.get(b, b) for b, _ in entry.upgrade_requirements if b})) + " + previous level")
        if entry.energy_cost is not None:
            needs.append(f"energy {entry.energy_cost:g}")
        statuses = "<br>".join(f"{i}: {status[(entry.id, i)]}" for i in entry.valid_issuers)
        lines.append(
            f"| {index} | `{entry.id}` | {entry.category} | {abilities} | {target} | {', '.join(dict.fromkeys(needs)) or '-'} "
            f"| {entry.aliases_ko[0]} | {statuses} |"
        )
    sent = sum(1 for v in status.values() if v == "sent")
    lines += ["", f"Offline status summary: **{sent} of {len(status)} (command, issuer) pairs sent**; "
              f"{len({k[0] for k, v in status.items() if v == 'sent'})} of {len(cat.all_commands())} commands sent for at least one issuer.", "", END]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    block = render()
    text = DOC.read_text(encoding="utf-8")
    if BEGIN in text and END in text:
        head, rest = text.split(BEGIN, 1)
        _, tail = rest.split(END, 1)
        new = head + block + tail
    else:
        new = text.rstrip("\n") + "\n\n## Command table\n\n" + block + "\n"
    if args.check:
        return 0 if new == text else 1
    DOC.write_text(new, encoding="utf-8")
    print(block.splitlines()[-3])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
