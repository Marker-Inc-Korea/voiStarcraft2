"""Final MicroMachine production release gate.

This module verifies already-produced evidence. It intentionally does not
launch StarCraft II, mutate the MicroMachine blackboard, or grant providers any
raw action surface.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from starcraft_commander.micromachine_build_identity import (
    DEFAULT_MICROMACHINE_COMMIT,
    DEFAULT_S2CLIENT_COMMIT,
)
from starcraft_commander.micromachine_map_pool import (
    DEFAULT_MAP_POOL_PATH,
    load_micromachine_map_pool,
)
from starcraft_commander.micromachine_soak_history import (
    SoakHistoryConfig,
    aggregate_soak_history,
)


DEFAULT_MAX_EVIDENCE_AGE_SECONDS: Final[int] = 14 * 24 * 60 * 60
DEFAULT_USER_QA_ITEMS: Final[tuple[str, ...]] = (
    "Launch the patched MicroMachine build against the local StarCraft II install.",
    "Submit live text strategy intents through the UI and confirm bounded DSL modulation is consumed.",
    "Watch one full game for human-visible strategic alignment and no unexpected manual-control surface.",
)


@dataclass(frozen=True)
class MicroMachineReleaseGateConfig:
    """Inputs for the final production evidence gate."""

    history_roots: tuple[Path, ...] = ()
    history_dashboard: Path | None = None
    build_identity_report: Path | None = None
    unit_evidence: Path | None = None
    triage_reports: tuple[Path, ...] = ()
    output_json: Path | None = None
    output_markdown: Path | None = None
    map_pool: Path = DEFAULT_MAP_POOL_PATH
    signoff_tier: str = "production"
    recent_limit: int = 20
    required_build_identity: str | None = None
    max_evidence_age_seconds: int | None = DEFAULT_MAX_EVIDENCE_AGE_SECONDS
    user_qa_items: tuple[str, ...] = DEFAULT_USER_QA_ITEMS

    def __post_init__(self) -> None:
        if self.history_dashboard is None and not self.history_roots:
            raise ValueError("history_dashboard or at least one history_root is required.")
        if self.history_dashboard is not None and not isinstance(self.history_dashboard, Path):
            raise ValueError("history_dashboard must be a Path.")
        for root in self.history_roots:
            if not isinstance(root, Path):
                raise ValueError("history_roots must contain Path values.")
        if self.build_identity_report is not None and not isinstance(
            self.build_identity_report,
            Path,
        ):
            raise ValueError("build_identity_report must be a Path.")
        if self.unit_evidence is not None and not isinstance(self.unit_evidence, Path):
            raise ValueError("unit_evidence must be a Path.")
        for report in self.triage_reports:
            if not isinstance(report, Path):
                raise ValueError("triage_reports must contain Path values.")
        if not isinstance(self.signoff_tier, str) or not self.signoff_tier.strip():
            raise ValueError("signoff_tier must be a non-empty string.")
        if type(self.recent_limit) is bool or self.recent_limit <= 0:
            raise ValueError("recent_limit must be a positive integer.")
        if self.required_build_identity is not None and (
            not isinstance(self.required_build_identity, str)
            or not self.required_build_identity.strip()
        ):
            raise ValueError("required_build_identity must be a non-empty string.")
        if self.max_evidence_age_seconds is not None:
            if (
                type(self.max_evidence_age_seconds) is bool
                or not isinstance(self.max_evidence_age_seconds, int)
                or self.max_evidence_age_seconds <= 0
            ):
                raise ValueError("max_evidence_age_seconds must be a positive integer.")


def build_release_gate_report(
    config: MicroMachineReleaseGateConfig,
) -> dict[str, object]:
    """Return the final machine-readable MicroMachine release verdict."""

    blockers: list[dict[str, object]] = []
    map_pool = load_micromachine_map_pool(config.map_pool)
    tier_summary = map_pool.to_summary(config.signoff_tier)
    build_identity = _read_build_identity_report(config.build_identity_report, blockers)
    required_identity = config.required_build_identity or _identity_value(build_identity)
    dashboard, dashboard_path = _load_or_build_dashboard(
        config,
        tier_summary=tier_summary,
        required_build_identity=required_identity,
        blockers=blockers,
    )
    evidence_paths: list[Path] = []
    if dashboard_path is not None:
        evidence_paths.append(dashboard_path)
    if config.build_identity_report is not None:
        evidence_paths.append(config.build_identity_report)
    if config.unit_evidence is not None:
        evidence_paths.append(config.unit_evidence)
    evidence_paths.extend(config.triage_reports)

    unit_evidence = _read_unit_evidence(config.unit_evidence, blockers)
    triage = _read_triage_reports(config.triage_reports, blockers)
    signoff = _dashboard_signoff(dashboard, blockers)
    matrix_verification = _verify_matrix_reports(
        dashboard,
        tier_summary=tier_summary,
        required_build_identity=required_identity,
        signoff_tier=config.signoff_tier,
        blockers=blockers,
    )
    evidence_paths.extend(matrix_verification["paths"])
    _verify_map_pool_alignment(tier_summary, signoff, blockers)
    _verify_production_signoff(signoff, blockers)
    _verify_build_identity(
        build_identity,
        required_identity=required_identity,
        signoff=signoff,
        blockers=blockers,
    )
    _verify_evidence_freshness(
        evidence_paths,
        max_age_seconds=config.max_evidence_age_seconds,
        blockers=blockers,
    )

    ok = not blockers
    report = {
        "schema_version": 1,
        "status": "passed" if ok else "blocked",
        "ok": ok,
        "issue": 51,
        "parent_issue": 48,
        "signoff_tier": config.signoff_tier,
        "map_pool": {
            "path": str(config.map_pool),
            "summary": tier_summary,
        },
        "required_build_identity": required_identity,
        "build_identity": build_identity,
        "unit_evidence": unit_evidence,
        "history_dashboard": {
            "path": str(dashboard_path) if dashboard_path is not None else None,
            "status": dashboard.get("status"),
            "ok": dashboard.get("ok"),
            "run_count": dashboard.get("run_count"),
            "case_count": dashboard.get("case_count"),
            "production_signoff": signoff,
        },
        "matrix_reports": matrix_verification["reports"],
        "matrix_coverage": matrix_verification["coverage"],
        "triage_reports": triage,
        "blockers": blockers,
        "user_qa_remaining": list(config.user_qa_items),
        "provider_boundary": {
            "micromachine_tactical_owner": True,
            "raw_sc2_actions_allowed": False,
            "neural_provider_surface": "bounded_policy_modulation_only",
        },
    }
    return report


def render_release_gate_markdown(report: Mapping[str, object]) -> str:
    """Render reviewer-ready Markdown for the final PR and QA handoff."""

    status = report.get("status", "unknown")
    signoff = _mapping(report.get("history_dashboard")).get("production_signoff", {})
    signoff_map = _mapping(signoff)
    coverage = _mapping(signoff_map.get("coverage"))
    build_identity = _mapping(report.get("build_identity"))
    unit_evidence = _mapping(report.get("unit_evidence"))
    lines = [
        "# MicroMachine Production Release Gate",
        "",
        f"- Status: `{status}`",
        f"- Parent issue: `#{report.get('parent_issue', 48)}`",
        f"- Gate issue: `#{report.get('issue', 51)}`",
        f"- Signoff tier: `{report.get('signoff_tier', 'production')}`",
        f"- Required build identity: `{report.get('required_build_identity') or 'unrecorded'}`",
        f"- Build identity status: `{build_identity.get('status', 'unknown')}`",
        f"- Unit evidence status: `{unit_evidence.get('status', 'unknown')}`",
        f"- Production coverage: {coverage.get('observed_count', 0)} / "
        f"{coverage.get('required_count', 0)}",
        "",
        "## Blockers",
        "",
    ]
    blockers = report.get("blockers")
    if isinstance(blockers, list) and blockers:
        lines.extend(["| Code | Detail |", "| --- | --- |"])
        for blocker in blockers:
            if not isinstance(blocker, Mapping):
                continue
            detail = ", ".join(
                f"{key}={value}"
                for key, value in blocker.items()
                if key != "code" and value not in (None, "", [])
            )
            lines.append(f"| `{blocker.get('code', '')}` | {detail or '-'} |")
    else:
        lines.append("No automated release blockers. Manual user QA remains.")
    lines.extend(["", "## Evidence", ""])
    for report_entry in _sequence(report.get("matrix_reports")):
        if not isinstance(report_entry, Mapping):
            continue
        lines.append(
            f"- Matrix `{report_entry.get('run_id', '')}`: "
            f"`{report_entry.get('status', '')}`, failed={report_entry.get('failed', 0)}, "
            f"path=`{report_entry.get('path', '')}`"
        )
    for triage_entry in _sequence(report.get("triage_reports")):
        if not isinstance(triage_entry, Mapping):
            continue
        lines.append(
            f"- Triage `{triage_entry.get('path', '')}`: "
            f"`{triage_entry.get('status', '')}`, failed={triage_entry.get('failed_case_count', 0)}"
        )
    lines.extend(["", "## User QA Remaining", ""])
    qa_items = _sequence(report.get("user_qa_remaining"))
    for index, item in enumerate(qa_items, start=1):
        lines.append(f"{index}. {item}")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            (
                "MicroMachine remains the tactical owner. UI, LLM, and future neural "
                "providers may only emit bounded policy modulation; raw SC2 actions, "
                "unit tags, and direct s2client/python-sc2 commands are not release "
                "provider outputs."
            ),
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def write_release_gate_outputs(
    report: Mapping[str, object],
    *,
    output_json: Path | None,
    output_markdown: Path | None,
) -> None:
    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if output_markdown is not None:
        output_markdown.parent.mkdir(parents=True, exist_ok=True)
        output_markdown.write_text(render_release_gate_markdown(report))


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify final MicroMachine production release evidence.",
    )
    parser.add_argument("--history-root", action="append", default=[])
    parser.add_argument("--history-dashboard")
    parser.add_argument("--build-identity-report")
    parser.add_argument("--unit-evidence")
    parser.add_argument("--triage-report", action="append", default=[])
    parser.add_argument("--output-json")
    parser.add_argument("--output-markdown")
    parser.add_argument("--map-pool", default=str(DEFAULT_MAP_POOL_PATH))
    parser.add_argument("--signoff-tier", default="production")
    parser.add_argument("--recent-limit", type=int, default=20)
    parser.add_argument("--required-build-identity", default="")
    parser.add_argument(
        "--max-evidence-age-seconds",
        type=int,
        default=DEFAULT_MAX_EVIDENCE_AGE_SECONDS,
    )
    parser.add_argument(
        "--no-evidence-age-limit",
        action="store_true",
        help="Disable freshness checks for deterministic archived-evidence review.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    report = build_release_gate_report(
        MicroMachineReleaseGateConfig(
            history_roots=tuple(Path(root) for root in args.history_root),
            history_dashboard=Path(args.history_dashboard) if args.history_dashboard else None,
            build_identity_report=(
                Path(args.build_identity_report) if args.build_identity_report else None
            ),
            unit_evidence=Path(args.unit_evidence) if args.unit_evidence else None,
            triage_reports=tuple(Path(report) for report in args.triage_report),
            output_json=Path(args.output_json) if args.output_json else None,
            output_markdown=Path(args.output_markdown) if args.output_markdown else None,
            map_pool=Path(args.map_pool),
            signoff_tier=args.signoff_tier,
            recent_limit=args.recent_limit,
            required_build_identity=args.required_build_identity or None,
            max_evidence_age_seconds=(
                None if args.no_evidence_age_limit else args.max_evidence_age_seconds
            ),
        )
    )
    write_release_gate_outputs(
        report,
        output_json=Path(args.output_json) if args.output_json else None,
        output_markdown=Path(args.output_markdown) if args.output_markdown else None,
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


def _load_or_build_dashboard(
    config: MicroMachineReleaseGateConfig,
    *,
    tier_summary: Mapping[str, object],
    required_build_identity: str | None,
    blockers: list[dict[str, object]],
) -> tuple[dict[str, object], Path | None]:
    if config.history_dashboard is not None:
        payload = _read_json_mapping(config.history_dashboard, blockers, "history_dashboard")
        return payload, config.history_dashboard
    dashboard = aggregate_soak_history(
        SoakHistoryConfig(
            roots=config.history_roots,
            recent_limit=config.recent_limit,
            signoff_tier=config.signoff_tier,
            required_map_files=tuple(_string_items(tier_summary.get("map_files"))),
            required_enemy_races=tuple(_string_items(tier_summary.get("enemy_races"))),
            required_enemy_difficulties=tuple(
                _int_items(tier_summary.get("enemy_difficulties"))
            ),
            required_strategy_profiles=tuple(
                _string_items(tier_summary.get("strategy_profiles"))
            ),
            required_build_identity=required_build_identity,
        )
    )
    return dashboard, None


def _read_build_identity_report(
    path: Path | None,
    blockers: list[dict[str, object]],
) -> dict[str, object]:
    if path is None:
        blockers.append({"code": "missing_build_identity_report"})
        return {
            "status": "missing",
            "ok": False,
            "identity": None,
            "expected": {
                "micromachine_commit": DEFAULT_MICROMACHINE_COMMIT,
                "s2client_commit": DEFAULT_S2CLIENT_COMMIT,
            },
            "failures": [{"code": "missing_build_identity_report"}],
        }
    payload = _read_json_mapping(path, blockers, "build_identity_report")
    identity = payload.get("identity")
    failures = _sequence(payload.get("failures"))
    ok = payload.get("ok") is True and isinstance(identity, str) and bool(identity)
    if not ok:
        blockers.append(
            {
                "code": "invalid_build_identity_report",
                "path": str(path),
                "failure_codes": _failure_codes(failures),
            }
        )
    return {
        "status": "passed" if ok else "failed",
        "ok": ok,
        "path": str(path),
        "identity": identity if isinstance(identity, str) else None,
        "expected": _mapping(payload.get("expected")),
        "observed": _mapping(payload.get("observed")),
        "checksums": _mapping(payload.get("checksums")),
        "failures": failures,
    }


def _read_unit_evidence(
    path: Path | None,
    blockers: list[dict[str, object]],
) -> dict[str, object]:
    if path is None:
        blockers.append({"code": "missing_unit_evidence"})
        return {"status": "missing", "ok": False, "path": None}
    payload = _read_json_mapping(path, blockers, "unit_evidence")
    ok = payload.get("ok") is True or payload.get("status") in {"passed", "success"}
    if not ok:
        blockers.append(
            {
                "code": "unit_evidence_failed",
                "path": str(path),
                "status": payload.get("status"),
            }
        )
    return {
        "status": "passed" if ok else "failed",
        "ok": ok,
        "path": str(path),
        "command": payload.get("command"),
        "summary": payload.get("summary"),
    }


def _read_triage_reports(
    paths: Sequence[Path],
    blockers: list[dict[str, object]],
) -> list[dict[str, object]]:
    if not paths:
        blockers.append({"code": "missing_triage_report"})
        return []
    reports: list[dict[str, object]] = []
    for path in paths:
        payload = _read_json_mapping(path, blockers, "triage_report")
        ok = payload.get("ok") is True and _int_value(payload.get("failed_case_count")) == 0
        if not ok:
            blockers.append(
                {
                    "code": "triage_report_failed",
                    "path": str(path),
                    "status": payload.get("status"),
                    "failed_case_count": payload.get("failed_case_count"),
                }
            )
        reports.append(
            {
                "status": "passed" if ok else "failed",
                "ok": ok,
                "path": str(path),
                "case_count": payload.get("case_count"),
                "failed_case_count": payload.get("failed_case_count"),
                "categories": payload.get("categories") if isinstance(payload.get("categories"), list) else [],
            }
        )
    return reports


def _dashboard_signoff(
    dashboard: Mapping[str, object],
    blockers: list[dict[str, object]],
) -> dict[str, object]:
    signoff = dashboard.get("production_signoff")
    if not isinstance(signoff, Mapping):
        blockers.append({"code": "missing_production_signoff"})
        return {}
    return dict(signoff)


def _verify_matrix_reports(
    dashboard: Mapping[str, object],
    *,
    tier_summary: Mapping[str, object],
    required_build_identity: str | None,
    signoff_tier: str,
    blockers: list[dict[str, object]],
) -> dict[str, object]:
    runs = dashboard.get("runs")
    if not isinstance(runs, list) or not runs:
        blockers.append({"code": "missing_matrix_runs"})
        coverage = _build_matrix_coverage(tier_summary, set())
        return {"reports": [], "coverage": coverage, "paths": []}
    results: list[dict[str, object]] = []
    paths: list[Path] = []
    observed_coverage: set[tuple[str, str, int, str | None]] = set()
    eligible_count = 0
    for run in runs:
        if not isinstance(run, Mapping):
            continue
        report_path = run.get("report")
        if not isinstance(report_path, str) or not report_path:
            blockers.append({"code": "missing_matrix_report_path", "run_id": run.get("run_id")})
            continue
        path = Path(report_path)
        if not path.exists():
            blockers.append(
                {
                    "code": "missing_matrix_report",
                    "run_id": run.get("run_id"),
                    "path": report_path,
                }
            )
            continue
        payload = _read_json_mapping(path, blockers, "matrix_report")
        paths.append(path)
        is_eligible = (
            payload.get("enabled", True) is not False
            and payload.get("qualification_tier") == signoff_tier
            and payload.get("allow_failures") is not True
        )
        if is_eligible:
            eligible_count += 1
            failed = _int_value(payload.get("failed"))
            matrix_build_ok = payload.get("build_identity_ok") is True
            if payload.get("ok") is not True or failed != 0:
                blockers.append(
                    {
                        "code": "matrix_report_failed",
                        "run_id": run.get("run_id"),
                        "path": report_path,
                        "failed": failed,
                        "status": payload.get("status"),
                    }
                )
            if required_build_identity is not None and payload.get("build_identity") != required_build_identity:
                blockers.append(
                    {
                        "code": "matrix_build_mismatch",
                        "run_id": run.get("run_id"),
                        "expected": required_build_identity,
                        "actual": payload.get("build_identity"),
                    }
                )
            if not matrix_build_ok:
                blockers.append(
                    {
                        "code": "matrix_build_identity_invalid",
                        "run_id": run.get("run_id"),
                        "identity": payload.get("build_identity"),
                        "failure_codes": _string_items(
                            payload.get("build_identity_failure_codes")
                        ),
                    }
                )
            if (
                payload.get("ok") is True
                and failed == 0
                and matrix_build_ok
                and (
                    required_build_identity is None
                    or payload.get("build_identity") == required_build_identity
                )
            ):
                observed_coverage.update(_matrix_observed_coverage(payload))
        results.append(
            {
                "run_id": run.get("run_id"),
                "path": report_path,
                "status": payload.get("status"),
                "ok": payload.get("ok"),
                "failed": payload.get("failed"),
                "qualification_tier": payload.get("qualification_tier"),
                "allow_failures": payload.get("allow_failures"),
                "build_identity": payload.get("build_identity"),
            }
        )
    if eligible_count == 0:
        blockers.append({"code": "no_eligible_matrix_report"})
    coverage = _build_matrix_coverage(tier_summary, observed_coverage)
    if coverage["missing_count"] != 0 or coverage["observed_count"] != coverage["required_count"]:
        blockers.append(
            {
                "code": "matrix_coverage_incomplete",
                "required_count": coverage["required_count"],
                "observed_count": coverage["observed_count"],
                "missing_count": coverage["missing_count"],
            }
        )
    return {"reports": results, "coverage": coverage, "paths": paths}


def _matrix_observed_coverage(
    matrix_payload: Mapping[str, object],
) -> set[tuple[str, str, int, str | None]]:
    observed: set[tuple[str, str, int, str | None]] = set()
    run_profiles = _string_items(matrix_payload.get("strategy_profiles"))
    cases = matrix_payload.get("cases")
    if not isinstance(cases, list):
        return observed
    for case in cases:
        if not isinstance(case, Mapping) or case.get("ok") is not True:
            continue
        base = _case_coverage_base(case)
        if base is None:
            continue
        profiles = _string_items(case.get("strategy_profiles")) or run_profiles
        if not profiles:
            observed.add((*base, None))
            continue
        for profile in profiles:
            observed.add((*base, profile))
    return observed


def _build_matrix_coverage(
    tier_summary: Mapping[str, object],
    observed: set[tuple[str, str, int, str | None]],
) -> dict[str, object]:
    required_profiles = _string_items(tier_summary.get("strategy_profiles"))
    profiles: list[str | None] = required_profiles or [None]
    required = {
        (map_file, race, difficulty, profile)
        for map_file in _string_items(tier_summary.get("map_files"))
        for race in _string_items(tier_summary.get("enemy_races"))
        for difficulty in _int_items(tier_summary.get("enemy_difficulties"))
        for profile in profiles
    }
    missing = [_coverage_payload(key) for key in sorted(required) if key not in observed]
    return {
        "required_count": len(required),
        "observed_count": len(required.intersection(observed)),
        "missing_count": len(missing),
        "missing": missing,
    }


def _case_coverage_base(case: Mapping[str, object]) -> tuple[str, str, int] | None:
    map_file = case.get("map_file")
    enemy_race = case.get("enemy_race")
    difficulty = case.get("enemy_difficulty")
    if not isinstance(map_file, str) or not map_file:
        return None
    if not isinstance(enemy_race, str) or not enemy_race:
        return None
    if type(difficulty) is bool or not isinstance(difficulty, int):
        return None
    return (map_file, enemy_race, difficulty)


def _coverage_payload(
    key: tuple[str, str, int, str | None],
) -> dict[str, object]:
    map_file, enemy_race, difficulty, profile = key
    payload: dict[str, object] = {
        "map_file": map_file,
        "enemy_race": enemy_race,
        "enemy_difficulty": difficulty,
    }
    if profile is not None:
        payload["strategy_profile"] = profile
    return payload


def _verify_map_pool_alignment(
    tier_summary: Mapping[str, object],
    signoff: Mapping[str, object],
    blockers: list[dict[str, object]],
) -> None:
    required = _mapping(signoff.get("required"))
    expected = {
        "map_files": _string_items(tier_summary.get("map_files")),
        "enemy_races": _string_items(tier_summary.get("enemy_races")),
        "enemy_difficulties": _int_items(tier_summary.get("enemy_difficulties")),
        "strategy_profiles": _string_items(tier_summary.get("strategy_profiles")),
    }
    for key, expected_value in expected.items():
        actual = _int_items(required.get(key)) if key == "enemy_difficulties" else _string_items(required.get(key))
        if actual != expected_value:
            blockers.append(
                {
                    "code": "dashboard_map_pool_mismatch",
                    "field": key,
                    "expected": expected_value,
                    "actual": actual,
                }
            )


def _verify_production_signoff(
    signoff: Mapping[str, object],
    blockers: list[dict[str, object]],
) -> None:
    if signoff.get("ok") is not True or signoff.get("status") != "passed":
        blockers.append(
            {
                "code": "production_signoff_blocked",
                "status": signoff.get("status"),
            }
        )
    for blocker in _sequence(signoff.get("blockers")):
        if isinstance(blocker, Mapping):
            blockers.append(
                {
                    **dict(blocker),
                    "source_code": blocker.get("code"),
                    "code": "production_signoff_" + str(blocker.get("code", "blocked")),
                }
            )
    coverage = _mapping(signoff.get("coverage"))
    required_count = _int_value(coverage.get("required_count"))
    observed_count = _int_value(coverage.get("observed_count"))
    missing_count = _int_value(coverage.get("missing_count"))
    if required_count <= 0 or observed_count != required_count or missing_count != 0:
        blockers.append(
            {
                "code": "production_coverage_incomplete",
                "required_count": required_count,
                "observed_count": observed_count,
                "missing_count": missing_count,
            }
        )


def _verify_build_identity(
    build_identity: Mapping[str, object],
    *,
    required_identity: str | None,
    signoff: Mapping[str, object],
    blockers: list[dict[str, object]],
) -> None:
    if build_identity.get("ok") is not True:
        return
    actual = build_identity.get("identity")
    if required_identity is None:
        blockers.append({"code": "missing_required_build_identity"})
    elif actual != required_identity:
        blockers.append(
            {
                "code": "build_identity_mismatch",
                "expected": required_identity,
                "actual": actual,
            }
        )
    observed = _string_items(_mapping(signoff.get("build_identity")).get("observed"))
    if required_identity is not None and observed and observed != [required_identity]:
        blockers.append(
            {
                "code": "signoff_observed_build_mismatch",
                "expected": required_identity,
                "actual": observed,
            }
        )


def _verify_evidence_freshness(
    paths: Sequence[Path],
    *,
    max_age_seconds: int | None,
    blockers: list[dict[str, object]],
) -> None:
    if max_age_seconds is None:
        return
    now = time.time()
    for path in paths:
        if not path.exists():
            continue
        age = int(now - path.stat().st_mtime)
        if age > max_age_seconds:
            blockers.append(
                {
                    "code": "stale_evidence",
                    "path": str(path),
                    "age_seconds": age,
                    "max_age_seconds": max_age_seconds,
                }
            )


def _read_json_mapping(
    path: Path,
    blockers: list[dict[str, object]],
    artifact_kind: str,
) -> dict[str, object]:
    if not path.exists():
        blockers.append(
            {
                "code": f"missing_{artifact_kind}",
                "path": str(path),
            }
        )
        return {}
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        blockers.append(
            {
                "code": f"invalid_{artifact_kind}",
                "path": str(path),
            }
        )
        return {}
    if not isinstance(payload, dict):
        blockers.append(
            {
                "code": f"invalid_{artifact_kind}",
                "path": str(path),
            }
        )
        return {}
    return payload


def _identity_value(build_identity: Mapping[str, object]) -> str | None:
    identity = build_identity.get("identity")
    return identity if isinstance(identity, str) and identity else None


def _failure_codes(failures: Sequence[object]) -> list[str]:
    codes: list[str] = []
    for failure in failures:
        if not isinstance(failure, Mapping):
            continue
        code = failure.get("code")
        if isinstance(code, str) and code:
            codes.append(code)
    return codes


def _mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _string_items(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _int_items(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    return [item for item in value if type(item) is int]


def _int_value(value: object) -> int:
    if type(value) is bool:
        return 0
    if isinstance(value, int):
        return value
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
