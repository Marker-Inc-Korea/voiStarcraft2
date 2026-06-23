"""Failure triage summaries for MicroMachine soak matrix artifacts."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final


TRIAGE_REPORT_JSON_NAME: Final[str] = "triage_report.json"
TRIAGE_REPORT_MD_NAME: Final[str] = "triage_report.md"

LOG_SIGNATURES: Final[tuple[str, ...]] = (
    "Depot build position fallback used",
    "Invalid setup detected",
    "Unusual ramp detected, tiles to block = 0",
    "Failed to place",
    "Path to building is not safe",
    "Connection reset",
    "Connection closed",
    "WaitJoinGame failed",
    "CreateGame failed",
    "JoinGame failed",
    "no_production_deadlock",
)

CATEGORY_BY_CODE: Final[dict[str, tuple[str, str]]] = {
    "missing_map": ("missing_map", "map_installation"),
    "unsupported_map": ("missing_map", "map_pool_contract"),
    "geometry_risk": ("geometry_preflight", "map_geometry"),
    "placement_risk": ("geometry_preflight", "build_placement"),
    "repeated_placement_failures": ("placement_loop", "build_placement"),
    "no_production_deadlock": ("no_production_deadlock", "ramp_walloff_build_placement"),
    "production_stall": ("production_stall", "ProductionManager"),
    "income_stall": ("income_stall", "WorkerManager"),
    "telemetry_missing": ("telemetry_stall", "telemetry_bridge"),
    "telemetry_stall": ("telemetry_stall", "telemetry_bridge"),
    "stale_modulation": ("stale_modulation", "policy_blackboard_bridge"),
    "strategy_profile_missing": ("stale_modulation", "policy_profile_schedule"),
    "manager_intervention_missing": ("missing_manager_intervention", "cpp_policy_hooks"),
    "micromachine_crash": ("process_crash", "MicroMachine_process"),
    "micromachine_process_stopped": ("process_crash", "MicroMachine_process"),
    "missing_report": ("process_crash", "soak_artifact_generation"),
    "sc2_disconnect": ("sc2_disconnect", "SC2_runtime"),
}


@dataclass(frozen=True)
class MicroMachineTriageConfig:
    """Inputs for matrix triage generation."""

    matrix_report: Path
    output_json: Path | None = None
    output_markdown: Path | None = None


def triage_matrix_report(matrix_report: Path | str | Mapping[str, object]) -> dict[str, object]:
    """Return a compact JSON-ready triage summary for one matrix report."""

    if isinstance(matrix_report, Mapping):
        payload = dict(matrix_report)
        report_path = None
    else:
        report_path = Path(matrix_report)
        payload = _read_json_mapping(report_path)
    cases_payload = payload.get("cases", [])
    cases = cases_payload if isinstance(cases_payload, list) else []
    triaged_cases = [
        _triage_case(case, report_path=report_path, matrix_payload=payload)
        for case in cases
        if isinstance(case, Mapping)
    ]
    failed_cases = [case for case in triaged_cases if case["status"] != "passed"]
    failed_cases.sort(key=lambda case: (-_int_value(case.get("impact_score")), str(case.get("case_id", ""))))
    category_counts: Counter[str] = Counter(
        str(case["category"]) for case in failed_cases if case.get("category")
    )
    report = {
        "status": "passed" if not failed_cases and triaged_cases else "failed",
        "ok": not failed_cases and bool(triaged_cases),
        "matrix_report": str(report_path) if report_path else None,
        "qualification_tier": payload.get("qualification_tier"),
        "allow_failures": payload.get("allow_failures"),
        "case_count": len(triaged_cases),
        "failed_case_count": len(failed_cases),
        "categories": [
            {"category": category, "count": count}
            for category, count in sorted(category_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "cases": triaged_cases,
        "ranked_failures": failed_cases,
    }
    return report


def render_triage_markdown(triage: Mapping[str, object]) -> str:
    """Render GitHub-ready Markdown for issue comments or PR evidence."""

    lines = [
        "# MicroMachine Failure Triage",
        "",
        f"- Status: `{triage.get('status', 'unknown')}`",
        f"- Qualification tier: `{triage.get('qualification_tier', 'unknown')}`",
        f"- Failed cases: {triage.get('failed_case_count', 0)} / {triage.get('case_count', 0)}",
        "",
        "## Ranked Failures",
        "",
    ]
    ranked = triage.get("ranked_failures")
    if not isinstance(ranked, list) or not ranked:
        lines.append("No failed cases in this matrix report.")
        return "\n".join(lines) + "\n"
    lines.extend(
        [
            "| Impact | Case | Category | Failure codes | Owner hint | Reproduction |",
            "| ---: | --- | --- | --- | --- | --- |",
        ]
    )
    for case in ranked:
        if not isinstance(case, Mapping):
            continue
        lines.append(
            "| "
            + " | ".join(
                (
                    str(case.get("impact_score", 0)),
                    f"`{case.get('case_id', '')}`",
                    f"`{case.get('category', '')}`",
                    ", ".join(f"`{code}`" for code in _string_list(case.get("failure_codes"))),
                    str(case.get("owner_hint", "")),
                    f"`{case.get('reproduction_command', '')}`",
                )
            )
            + " |"
        )
    lines.extend(["", "## Artifact Paths", ""])
    for case in ranked:
        if not isinstance(case, Mapping):
            continue
        lines.append(f"### `{case.get('case_id', '')}`")
        for label, path in _artifact_pairs(case):
            lines.append(f"- {label}: `{path}`")
        signatures = _string_list(case.get("log_signatures"))
        if signatures:
            lines.append("- Log signatures: " + ", ".join(f"`{item}`" for item in signatures))
        lines.append(f"- Next owner hint: `{case.get('owner_hint', '')}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_triage_outputs(
    triage: Mapping[str, object],
    *,
    output_json: Path | None,
    output_markdown: Path | None,
) -> None:
    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(triage, indent=2, sort_keys=True) + "\n")
    if output_markdown is not None:
        output_markdown.parent.mkdir(parents=True, exist_ok=True)
        output_markdown.write_text(render_triage_markdown(triage))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Triage MicroMachine matrix failures.")
    parser.add_argument("--matrix-report", required=True)
    parser.add_argument("--output-json")
    parser.add_argument("--output-markdown")
    args = parser.parse_args(argv)
    triage = triage_matrix_report(Path(args.matrix_report))
    write_triage_outputs(
        triage,
        output_json=Path(args.output_json) if args.output_json else None,
        output_markdown=Path(args.output_markdown) if args.output_markdown else None,
    )
    print(json.dumps(triage, sort_keys=True))
    return 0


def _triage_case(
    case: Mapping[str, object],
    *,
    report_path: Path | None,
    matrix_payload: Mapping[str, object],
) -> dict[str, object]:
    codes = _string_list(case.get("failure_codes"))
    preflight = case.get("preflight") if isinstance(case.get("preflight"), Mapping) else {}
    category, owner = _category_and_owner(codes)
    artifacts = _case_artifacts(case, report_path=report_path)
    signatures = _collect_signatures(case, preflight, artifacts)
    tier = str(matrix_payload.get("qualification_tier", ""))
    failed = case.get("ok") is not True
    impact = _impact_score(tier=tier, failed=failed, category=category, codes=codes)
    return {
        "case_id": case.get("case_id"),
        "status": "passed" if not failed else "failed",
        "impact_score": impact,
        "production_impact": tier in {"production", "extended"} and failed,
        "qualification_tier": tier,
        "map_file": case.get("map_file"),
        "enemy_race": case.get("enemy_race"),
        "enemy_difficulty": case.get("enemy_difficulty"),
        "preflight_status": case.get("preflight_status"),
        "preflight_ok": case.get("preflight_ok"),
        "failure_phase": case.get("failure_phase"),
        "failure_codes": codes,
        "category": category,
        "owner_hint": owner,
        "log_signatures": signatures,
        "artifacts": artifacts,
        "reproduction_command": _reproduction_command(case, preflight, tier),
    }


def _category_and_owner(codes: Sequence[str]) -> tuple[str, str]:
    for code in codes:
        if code in CATEGORY_BY_CODE:
            return CATEGORY_BY_CODE[code]
    if codes:
        return ("unknown_failure", "MicroMachine_runtime")
    return ("none", "")


def _impact_score(*, tier: str, failed: bool, category: str, codes: Sequence[str]) -> int:
    if not failed:
        return 0
    score = 50
    if tier in {"production", "extended"}:
        score += 100
    if category in {"process_crash", "sc2_disconnect", "no_production_deadlock"}:
        score += 30
    if "missing_map" in codes or "unsupported_map" in codes:
        score += 20
    return score


def _case_artifacts(case: Mapping[str, object], *, report_path: Path | None) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for key in ("case_dir", "report", "preflight_report"):
        value = case.get(key)
        if isinstance(value, str) and value:
            artifacts[key] = value
    case_dir = Path(artifacts["case_dir"]) if "case_dir" in artifacts else None
    manifest = case.get("artifact_manifest")
    if isinstance(manifest, Mapping):
        for key, value in manifest.items():
            if not isinstance(key, str) or not isinstance(value, str) or not value:
                continue
            path = Path(value)
            if not path.is_absolute() and case_dir is not None:
                path = case_dir / path
            artifacts[key] = str(path)
    if report_path is not None:
        artifacts["matrix_report"] = str(report_path)
    return artifacts


def _collect_signatures(
    case: Mapping[str, object],
    preflight: object,
    artifacts: Mapping[str, str],
) -> list[str]:
    signatures: list[str] = []
    if isinstance(preflight, Mapping):
        blocker = preflight.get("blocker")
        if isinstance(blocker, Mapping):
            signatures.extend(_string_list(blocker.get("evidence_signatures")))
    for failure in case.get("failures", []) if isinstance(case.get("failures"), list) else []:
        if isinstance(failure, Mapping):
            signatures.extend(_string_list(failure.get("evidence", {}).get("terms") if isinstance(failure.get("evidence"), Mapping) else []))
    bot_log = artifacts.get("bot_log")
    if bot_log:
        text = _read_text(Path(bot_log))
        signatures.extend(signature for signature in LOG_SIGNATURES if signature in text)
    return sorted(dict.fromkeys(signatures))


def _reproduction_command(case: Mapping[str, object], preflight: object, tier: str) -> str:
    if isinstance(preflight, Mapping):
        blocker = preflight.get("blocker")
        if isinstance(blocker, Mapping) and isinstance(blocker.get("reproduction_command"), str):
            return blocker["reproduction_command"]
    map_file = case.get("map_file") or ""
    race = case.get("enemy_race") or "Zerg"
    difficulty = case.get("enemy_difficulty") or 1
    return (
        f"SOAK_MATRIX_QUALIFICATION_TIER={tier or 'production'} "
        f"SOAK_MATRIX_MAP_FILES=\"{map_file}\" "
        f"SOAK_MATRIX_ENEMY_RACES={race} "
        f"SOAK_MATRIX_ENEMY_DIFFICULTIES={difficulty} "
        "integrations/micromachine/scripts/soak_matrix_macos_local.sh"
    )


def _artifact_pairs(case: Mapping[str, object]) -> list[tuple[str, str]]:
    artifacts = case.get("artifacts")
    if not isinstance(artifacts, Mapping):
        return []
    return [
        (str(key), str(value))
        for key, value in sorted(artifacts.items())
        if isinstance(key, str) and isinstance(value, str)
    ]


def _read_json_mapping(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return payload


def _read_text(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _int_value(value: object) -> int:
    if type(value) is bool:
        return 0
    if isinstance(value, int):
        return value
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
