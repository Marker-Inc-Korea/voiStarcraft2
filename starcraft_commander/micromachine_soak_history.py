"""Aggregate MicroMachine soak matrix reports into operations dashboards."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final


MATRIX_REPORT_NAME: Final[str] = "matrix_report.json"


@dataclass(frozen=True)
class SoakHistoryConfig:
    """Inputs for deterministic soak history aggregation."""

    roots: tuple[Path, ...]
    output_json: Path | None = None
    output_markdown: Path | None = None
    recent_limit: int = 20
    signoff_tier: str = "production"
    required_map_files: tuple[str, ...] = ()
    required_enemy_races: tuple[str, ...] = ()
    required_enemy_difficulties: tuple[int, ...] = ()
    required_strategy_profiles: tuple[str, ...] = ()
    required_build_identity: str | None = None

    def __post_init__(self) -> None:
        if not self.roots:
            raise ValueError("at least one soak history root is required.")
        for root in self.roots:
            if not isinstance(root, Path):
                raise ValueError("roots must contain Path values.")
        _require_positive_int("recent_limit", self.recent_limit)
        if not isinstance(self.signoff_tier, str) or not self.signoff_tier:
            raise ValueError("signoff_tier must be a non-empty string.")
        object.__setattr__(
            self,
            "required_map_files",
            _string_sequence("required_map_files", self.required_map_files),
        )
        object.__setattr__(
            self,
            "required_enemy_races",
            _string_sequence("required_enemy_races", self.required_enemy_races),
        )
        for value in self.required_enemy_difficulties:
            _require_positive_int("required_enemy_difficulties[]", value)
        object.__setattr__(
            self,
            "required_strategy_profiles",
            _string_sequence(
                "required_strategy_profiles",
                self.required_strategy_profiles,
            ),
        )
        if self.required_build_identity is not None and (
            not isinstance(self.required_build_identity, str)
            or not self.required_build_identity.strip()
        ):
            raise ValueError("required_build_identity must be a non-empty string.")


def aggregate_matrix_run(
    run_dir: Path | str,
    *,
    target_frame: int,
    timeout_seconds: int,
    qualification_tier: str = "production",
    allow_failures: bool = False,
    strategy_profiles: Sequence[str] = (),
    build_identity: str | None = None,
) -> dict[str, object]:
    """Build one deterministic matrix_report.json payload from case reports."""

    root = Path(run_dir)
    target = _require_non_negative_int("target_frame", target_frame)
    timeout = _require_non_negative_int("timeout_seconds", timeout_seconds)
    if not isinstance(qualification_tier, str) or not qualification_tier:
        raise ValueError("qualification_tier must be a non-empty string.")
    if type(allow_failures) is not bool:
        raise ValueError("allow_failures must be a boolean.")
    profiles = _string_sequence("strategy_profiles", strategy_profiles)
    if build_identity is not None and (
        not isinstance(build_identity, str) or not build_identity.strip()
    ):
        raise ValueError("build_identity must be a non-empty string.")
    cases: list[dict[str, object]] = []
    passed = 0
    failed = 0
    for case_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        report_path = case_dir / "soak_report.json"
        preflight_path = case_dir / "preflight_report.json"
        preflight = _read_optional_json_mapping(preflight_path)
        case: dict[str, object] = {
            "case_id": case_dir.name,
            "case_dir": str(case_dir),
            "report": str(report_path),
            "preflight_report": str(preflight_path),
            "preflight": preflight,
            "preflight_status": preflight.get("status") if preflight else None,
            "preflight_ok": preflight.get("ok") if preflight else None,
            "preflight_failure_codes": (
                preflight.get("failure_codes") if preflight else []
            ),
            "target_frame": target,
            "timeout_seconds": timeout,
            "qualification_tier": qualification_tier,
            "allow_failures": allow_failures,
            "strategy_profiles": list(profiles),
            "build_identity": build_identity,
        }
        if not report_path.exists():
            dimensions = _case_dimensions_from_sources(case_dir.name, preflight, {})
            case.update(
                {
                    "status": "missing_report",
                    "ok": False,
                    "failures": [],
                    "failure_codes": ["missing_report"],
                    "failure_phase": "missing_report",
                    **dimensions,
                }
            )
            failed += 1
            cases.append(case)
            continue
        payload = _read_json_mapping(report_path)
        ok = payload.get("ok") is True
        flattened_failures = _flatten_failures(payload)
        failure_codes = sorted(
            {
                failure.get("code")
                for failure in flattened_failures
                if isinstance(failure.get("code"), str)
            }
        )
        case.update(
            {
                "status": payload.get("status"),
                "ok": ok,
                "failure_phase": _case_failure_phase(
                    ok=ok,
                    preflight_ok=(
                        preflight.get("ok")
                        if preflight
                        else _mapping_value(payload, "preflight", "ok")
                    ),
                ),
                "latest_frame": payload.get("latest_frame"),
                "target_reached": payload.get("target_reached"),
                "macro_evidence_ok": payload.get("macro_evidence_ok"),
                "manager_intervention_ok": payload.get("manager_intervention_ok"),
                "config": payload["config"] if isinstance(payload.get("config"), Mapping) else {},
                "observation": (
                    payload["observation"]
                    if isinstance(payload.get("observation"), Mapping)
                    else {}
                ),
                "failures": flattened_failures,
                "failure_codes": failure_codes,
                "preflight": preflight or payload.get("preflight", {}),
                "preflight_status": (
                    preflight.get("status")
                    if preflight
                    else _mapping_value(payload, "preflight", "status")
                ),
                "preflight_ok": (
                    preflight.get("ok")
                    if preflight
                    else _mapping_value(payload, "preflight", "ok")
                ),
                "preflight_failure_codes": (
                    preflight.get("failure_codes")
                    if preflight
                    else _mapping_value(payload, "preflight", "failure_codes", default=[])
                ),
                "attempts": (
                    payload["attempts"] if isinstance(payload.get("attempts"), list) else []
                ),
                "selected_attempt": payload.get("selected_attempt"),
                "artifact_manifest": (
                    payload["artifact_manifest"]
                    if isinstance(payload.get("artifact_manifest"), Mapping)
                    else {}
                ),
                **_case_dimensions_from_sources(case_dir.name, preflight, payload),
            }
        )
        if ok:
            passed += 1
        else:
            failed += 1
        cases.append(case)
    return {
        "status": "passed" if failed == 0 and cases else "failed",
        "ok": failed == 0 and bool(cases),
        "target_frame": target,
        "timeout_seconds": timeout,
        "qualification_tier": qualification_tier,
        "allow_failures": allow_failures,
        "strategy_profiles": list(profiles),
        "build_identity": build_identity,
        "case_count": len(cases),
        "passed": passed,
        "failed": failed,
        "cases": cases,
    }


def aggregate_soak_history(config: SoakHistoryConfig) -> dict[str, object]:
    """Aggregate recent matrix_report.json files into JSON dashboard data."""

    reports = sorted(
        _discover_matrix_reports(config.roots),
        key=_report_recency_key,
        reverse=True,
    )
    runs: list[dict[str, object]] = []
    failure_codes: Counter[str] = Counter()
    maps: Counter[str] = Counter()
    races: Counter[str] = Counter()
    difficulties: Counter[str] = Counter()
    target_frames: Counter[str] = Counter()
    passed_runs = 0
    failed_runs = 0
    total_cases = 0
    total_passed_cases = 0
    total_failed_cases = 0
    signoff_inputs: list[dict[str, object]] = []
    for report_path in reports[: config.recent_limit]:
        payload = _read_json_mapping(report_path)
        cases = payload.get("cases", [])
        case_list = cases if isinstance(cases, list) else []
        run_ok = payload.get("ok") is True
        if run_ok:
            passed_runs += 1
        else:
            failed_runs += 1
        passed_cases = _int_value(payload.get("passed"))
        failed_cases = _int_value(payload.get("failed"))
        total_cases += _int_value(payload.get("case_count"), len(case_list))
        total_passed_cases += passed_cases
        total_failed_cases += failed_cases
        target_frames[str(payload.get("target_frame", ""))] += 1
        for case in case_list:
            if not isinstance(case, Mapping):
                continue
            for code in case.get("failure_codes", []):
                if isinstance(code, str) and code:
                    failure_codes[code] += 1
            for key, counter in (
                ("map_file", maps),
                ("enemy_race", races),
                ("enemy_difficulty", difficulties),
            ):
                value = case.get(key)
                if value not in (None, ""):
                    counter[str(value)] += 1
        run_summary = {
            "run_id": report_path.parent.name,
            "report": str(report_path),
            "artifact_dir": str(report_path.parent),
            "ok": run_ok,
            "status": payload.get("status", "unknown"),
            "enabled": payload.get("enabled", True),
            "case_count": payload.get("case_count", len(case_list)),
            "passed": passed_cases,
            "failed": failed_cases,
            "target_frame": payload.get("target_frame"),
            "timeout_seconds": payload.get("timeout_seconds"),
            "qualification_tier": payload.get("qualification_tier"),
            "allow_failures": payload.get("allow_failures"),
            "strategy_profiles": (
                payload["strategy_profiles"]
                if isinstance(payload.get("strategy_profiles"), list)
                else []
            ),
            "build_identity": payload.get("build_identity"),
            "failure_codes": sorted(
                {
                    code
                    for case in case_list
                    if isinstance(case, Mapping)
                    for code in case.get("failure_codes", [])
                    if isinstance(code, str)
                }
            ),
        }
        runs.append(
            run_summary
        )
        signoff_inputs.append(
            {
                **run_summary,
                "cases": case_list,
            }
        )
    dashboard = {
        "status": "passed" if failed_runs == 0 and runs else "failed",
        "ok": failed_runs == 0 and bool(runs),
        "run_count": len(runs),
        "passed_runs": passed_runs,
        "failed_runs": failed_runs,
        "case_count": total_cases,
        "passed_cases": total_passed_cases,
        "failed_cases": total_failed_cases,
        "failure_codes": _counter_payload(failure_codes),
        "maps": _counter_payload(maps),
        "enemy_races": _counter_payload(races),
        "enemy_difficulties": _counter_payload(difficulties),
        "target_frames": _counter_payload(target_frames),
        "streaks": _run_streaks(runs),
        "production_signoff": _build_production_signoff(signoff_inputs, config),
        "runs": runs,
    }
    return dashboard


def render_soak_history_markdown(dashboard: Mapping[str, object]) -> str:
    """Render a compact Markdown dashboard for artifacts or issue comments."""

    lines = [
        "# MicroMachine Soak History",
        "",
        f"- Status: `{dashboard.get('status', 'unknown')}`",
        f"- Runs: {dashboard.get('run_count', 0)} "
        f"(passed {dashboard.get('passed_runs', 0)}, failed {dashboard.get('failed_runs', 0)})",
        f"- Cases: {dashboard.get('case_count', 0)} "
        f"(passed {dashboard.get('passed_cases', 0)}, failed {dashboard.get('failed_cases', 0)})",
        "",
    ]
    streaks = dashboard.get("streaks")
    if isinstance(streaks, Mapping):
        lines.extend(
            [
                f"- Current pass streak: {streaks.get('current_pass_streak', 0)}",
                f"- Current fail streak: {streaks.get('current_fail_streak', 0)}",
                "",
            ]
        )
    lines.extend([
        "## Production Signoff",
        "",
    ])
    signoff = dashboard.get("production_signoff")
    if isinstance(signoff, Mapping):
        coverage = signoff.get("coverage") if isinstance(signoff.get("coverage"), Mapping) else {}
        lines.extend(
            [
                f"- Status: `{signoff.get('status', 'unknown')}`",
                f"- Tier: `{signoff.get('signoff_tier', 'production')}`",
                f"- Eligible runs: {signoff.get('eligible_run_count', 0)}",
                f"- Coverage: {coverage.get('observed_count', 0)} / "
                f"{coverage.get('required_count', 0)}",
            ]
        )
        blockers = signoff.get("blockers")
        if isinstance(blockers, list) and blockers:
            lines.extend(["", "| Blocker | Detail |", "| --- | --- |"])
            for blocker in blockers[:20]:
                if not isinstance(blocker, Mapping):
                    continue
                detail = ", ".join(
                    f"{key}={value}"
                    for key, value in blocker.items()
                    if key != "code" and value not in (None, "", [])
                )
                lines.append(f"| `{blocker.get('code', '')}` | {detail or '-'} |")
        else:
            lines.append("- No signoff blockers in the selected recent-N window.")
        lines.append("")
    else:
        lines.extend(["No production signoff payload was generated.", ""])
    lines.extend([
        "## Failure Codes",
        "",
    ])
    failure_codes = dashboard.get("failure_codes")
    if isinstance(failure_codes, list) and failure_codes:
        lines.extend(_counter_table(failure_codes, "Failure code"))
    else:
        lines.append("No failure codes in the selected history window.")
    lines.extend(["", "## Recent Runs", "", "| Run | Status | Cases | Failures | Report |", "| --- | --- | ---: | --- | --- |"])
    runs = dashboard.get("runs")
    if isinstance(runs, list):
        for run in runs:
            if not isinstance(run, Mapping):
                continue
            failures = run.get("failure_codes")
            failure_text = (
                ", ".join(str(item) for item in failures)
                if isinstance(failures, list) and failures
                else "-"
            )
            lines.append(
                "| "
                + " | ".join(
                    (
                        str(run.get("run_id", "")),
                        str(run.get("status", "")),
                        str(run.get("case_count", 0)),
                        failure_text,
                        f"`{run.get('report', '')}`",
                    )
                )
                + " |"
            )
    return "\n".join(lines) + "\n"


def write_dashboard_outputs(
    dashboard: Mapping[str, object],
    *,
    output_json: Path | None = None,
    output_markdown: Path | None = None,
) -> None:
    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(dashboard, indent=2, sort_keys=True) + "\n")
    if output_markdown is not None:
        output_markdown.parent.mkdir(parents=True, exist_ok=True)
        output_markdown.write_text(render_soak_history_markdown(dashboard))


def _run_streaks(runs: Sequence[Mapping[str, object]]) -> dict[str, object]:
    pass_streak = 0
    for run in runs:
        if run.get("ok") is True:
            pass_streak += 1
        else:
            break
    fail_streak = 0
    for run in runs:
        if run.get("ok") is not True:
            fail_streak += 1
        else:
            break
    return {
        "current_status": (
            "passed"
            if runs and runs[0].get("ok") is True
            else "failed"
            if runs
            else "none"
        ),
        "current_pass_streak": pass_streak,
        "current_fail_streak": fail_streak,
    }


def _build_production_signoff(
    runs: Sequence[Mapping[str, object]],
    config: SoakHistoryConfig,
) -> dict[str, object]:
    required = _required_coverage(config)
    observed: dict[tuple[str, str, int, str | None], dict[str, object]] = {}
    blockers: list[dict[str, object]] = []
    eligible_runs: list[str] = []
    excluded_runs: list[dict[str, object]] = []
    observed_builds: set[str] = set()

    if not config.required_map_files:
        blockers.append({"code": "missing_required_maps", "message": "No required map files were configured."})
    if not config.required_enemy_races:
        blockers.append({"code": "missing_required_races", "message": "No required enemy races were configured."})
    if not config.required_enemy_difficulties:
        blockers.append(
            {
                "code": "missing_required_difficulties",
                "message": "No required enemy difficulties were configured.",
            }
        )

    for run in runs:
        run_id = str(run.get("run_id", ""))
        exclusion = _signoff_exclusion_reason(run, config.signoff_tier)
        if exclusion is not None:
            excluded_runs.append({"run_id": run_id, "reason": exclusion})
            continue
        eligible_runs.append(run_id)
        build_identity = run.get("build_identity")
        if isinstance(build_identity, str) and build_identity:
            observed_builds.add(build_identity)
        build_matches = True
        if config.required_build_identity is not None and build_identity != config.required_build_identity:
            build_matches = False
            blockers.append(
                {
                    "code": "build_mismatch",
                    "run_id": run_id,
                    "expected": config.required_build_identity,
                    "actual": build_identity,
                }
            )
        if run.get("ok") is not True or _int_value(run.get("failed")) > 0:
            blockers.append(
                {
                    "code": "failed_required_case",
                    "run_id": run_id,
                    "failed": _int_value(run.get("failed")),
                    "failure_codes": (
                        run["failure_codes"]
                        if isinstance(run.get("failure_codes"), list)
                        else []
                    ),
                }
            )
            continue
        if not build_matches:
            continue
        run_profiles = _string_list(run.get("strategy_profiles"))
        cases = run.get("cases")
        if not isinstance(cases, list):
            continue
        for case in cases:
            if not isinstance(case, Mapping) or case.get("ok") is not True:
                continue
            key_base = _case_coverage_base(case)
            if key_base is None:
                continue
            case_profiles = _string_list(case.get("strategy_profiles")) or run_profiles
            profiles = config.required_strategy_profiles or (None,)
            for profile in profiles:
                if profile is not None and profile not in case_profiles:
                    continue
                coverage_key = (*key_base, profile)
                observed[coverage_key] = {
                    "run_id": run_id,
                    "map_file": key_base[0],
                    "enemy_race": key_base[1],
                    "enemy_difficulty": key_base[2],
                    "strategy_profile": profile,
                }

    missing = [
        _coverage_payload(key)
        for key in sorted(required)
        if key not in observed
    ]
    for item in missing:
        blockers.append({"code": "missing_required_coverage", **item})
    if not eligible_runs:
        blockers.append(
            {
                "code": "no_eligible_production_runs",
                "message": "No enabled production runs were found in the selected recent-N window.",
            }
        )

    ok = not blockers and bool(eligible_runs)
    return {
        "status": "passed" if ok else "blocked",
        "ok": ok,
        "signoff_tier": config.signoff_tier,
        "window_size": config.recent_limit,
        "eligible_run_count": len(eligible_runs),
        "excluded_run_count": len(excluded_runs),
        "eligible_runs": eligible_runs,
        "excluded_runs": excluded_runs,
        "required": {
            "map_files": list(config.required_map_files),
            "enemy_races": list(config.required_enemy_races),
            "enemy_difficulties": list(config.required_enemy_difficulties),
            "strategy_profiles": list(config.required_strategy_profiles),
        },
        "coverage": {
            "required_count": len(required),
            "observed_count": len(observed),
            "missing_count": len(missing),
            "missing": missing,
        },
        "build_identity": {
            "required": config.required_build_identity,
            "observed": sorted(observed_builds),
        },
        "blockers": blockers,
    }


def _required_coverage(
    config: SoakHistoryConfig,
) -> set[tuple[str, str, int, str | None]]:
    profiles: tuple[str | None, ...] = config.required_strategy_profiles or (None,)
    return {
        (map_file, race, difficulty, profile)
        for map_file in config.required_map_files
        for race in config.required_enemy_races
        for difficulty in config.required_enemy_difficulties
        for profile in profiles
    }


def _signoff_exclusion_reason(
    run: Mapping[str, object],
    signoff_tier: str,
) -> str | None:
    if run.get("enabled") is False or run.get("status") == "disabled":
        return "disabled"
    if run.get("qualification_tier") != signoff_tier:
        return "non_signoff_tier"
    if run.get("allow_failures") is True:
        return "allow_failures_enabled"
    return None


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


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Aggregate MicroMachine soak matrix history.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    matrix = subparsers.add_parser("matrix-report")
    matrix.add_argument("--run-dir", required=True)
    matrix.add_argument("--output", required=True)
    matrix.add_argument("--target-frame", required=True, type=int)
    matrix.add_argument("--timeout-seconds", required=True, type=int)
    matrix.add_argument("--qualification-tier", default="production")
    matrix.add_argument("--allow-failures", action="store_true")
    matrix.add_argument("--strategy-profiles", default="")
    matrix.add_argument("--build-identity", default="")

    history = subparsers.add_parser("history-dashboard")
    history.add_argument("--root", action="append", required=True)
    history.add_argument("--output-json", required=True)
    history.add_argument("--output-markdown", required=True)
    history.add_argument("--recent-limit", type=int, default=20)
    history.add_argument("--signoff-tier", default="production")
    history.add_argument("--required-map-files", default="")
    history.add_argument("--required-enemy-races", default="")
    history.add_argument("--required-enemy-difficulties", default="")
    history.add_argument("--required-strategy-profiles", default="")
    history.add_argument("--required-build-identity", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    if args.command == "matrix-report":
        payload = aggregate_matrix_run(
            Path(args.run_dir),
            target_frame=args.target_frame,
            timeout_seconds=args.timeout_seconds,
            qualification_tier=args.qualification_tier,
            allow_failures=args.allow_failures,
            strategy_profiles=tuple(
                item for item in args.strategy_profiles.split() if item
            ),
            build_identity=args.build_identity or None,
        )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return 0
    dashboard = aggregate_soak_history(
        SoakHistoryConfig(
            roots=tuple(Path(root) for root in args.root),
            recent_limit=args.recent_limit,
            signoff_tier=args.signoff_tier,
            required_map_files=tuple(
                item for item in args.required_map_files.split() if item
            ),
            required_enemy_races=tuple(
                item for item in args.required_enemy_races.split() if item
            ),
            required_enemy_difficulties=tuple(
                int(item) for item in args.required_enemy_difficulties.split() if item
            ),
            required_strategy_profiles=tuple(
                item for item in args.required_strategy_profiles.split() if item
            ),
            required_build_identity=args.required_build_identity or None,
        )
    )
    write_dashboard_outputs(
        dashboard,
        output_json=Path(args.output_json),
        output_markdown=Path(args.output_markdown),
    )
    return 0


def _discover_matrix_reports(roots: Iterable[Path]) -> tuple[Path, ...]:
    reports: list[Path] = []
    for root in roots:
        if root.is_file() and root.name == MATRIX_REPORT_NAME:
            reports.append(root)
            continue
        if root.is_dir():
            direct = root / MATRIX_REPORT_NAME
            if direct.exists():
                reports.append(direct)
            reports.extend(sorted(root.glob(f"*/{MATRIX_REPORT_NAME}")))
    return tuple(dict.fromkeys(reports))


def _report_recency_key(path: Path) -> tuple[float, str]:
    try:
        timestamp = path.stat().st_mtime
    except OSError:
        timestamp = 0.0
    return (timestamp, str(path))


def _flatten_failures(payload: Mapping[str, object]) -> list[dict[str, object]]:
    flattened: list[dict[str, object]] = []
    direct = payload.get("failures", [])
    if isinstance(direct, list):
        flattened.extend(item for item in direct if isinstance(item, dict))
    attempts = payload.get("attempts", [])
    if isinstance(attempts, list):
        for attempt in attempts:
            if not isinstance(attempt, Mapping):
                continue
            attempt_failures = attempt.get("failures", [])
            if not isinstance(attempt_failures, list):
                continue
            for failure in attempt_failures:
                if isinstance(failure, Mapping):
                    flattened.append(
                        {
                            **failure,
                            "attempt": attempt.get("attempt"),
                            "attempt_status": attempt.get("status"),
                        }
                    )
    return flattened


def _case_value(payload: Mapping[str, object], key: str) -> object:
    if payload.get(key) not in (None, ""):
        return payload.get(key)
    observation = payload.get("observation")
    if isinstance(observation, Mapping) and observation.get(key) not in (None, ""):
        return observation.get(key)
    config = payload.get("config")
    if isinstance(config, Mapping) and config.get(key) not in (None, ""):
        return config.get(key)
    return None


def _case_dimensions_from_sources(
    case_id: str,
    preflight: Mapping[str, object],
    payload: Mapping[str, object],
) -> dict[str, object]:
    map_file = _case_value(payload, "map_file") or preflight.get("map_file")
    enemy_race = _case_value(payload, "enemy_race")
    enemy_difficulty = _case_value(payload, "enemy_difficulty")
    parsed = _case_dimensions_from_id(case_id)
    return {
        "map_file": map_file or parsed["map_file"],
        "enemy_race": enemy_race or parsed["enemy_race"],
        "enemy_difficulty": enemy_difficulty or parsed["enemy_difficulty"],
    }


def _case_dimensions_from_id(case_id: str) -> dict[str, object]:
    parts = case_id.split("-")
    if len(parts) < 4:
        return {"map_file": None, "enemy_race": None, "enemy_difficulty": None}
    race = parts[-2]
    difficulty_token = parts[-1]
    difficulty: int | None = None
    if difficulty_token.startswith("d") and difficulty_token[1:].isdigit():
        difficulty = int(difficulty_token[1:])
    map_tokens = parts[1:-2]
    map_file = "/".join(map_tokens) if map_tokens else None
    if map_file == "AcropolisLE/SC2Map":
        map_file = "AcropolisLE.SC2Map"
    elif map_file == "Ladder2019Season3/ThunderbirdLE/SC2Map":
        map_file = "Ladder2019Season3/ThunderbirdLE.SC2Map"
    return {"map_file": map_file, "enemy_race": race, "enemy_difficulty": difficulty}


def _read_json_mapping(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return payload


def _case_failure_phase(*, ok: bool, preflight_ok: object) -> str:
    if ok:
        return "passed"
    if preflight_ok is False:
        return "preflight_failure"
    if preflight_ok is True:
        return "production_runtime_failure"
    return "production_runtime_failure"


def _read_optional_json_mapping(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return _read_json_mapping(path)


def _mapping_value(
    payload: Mapping[str, object],
    key: str,
    nested_key: str,
    *,
    default: object = None,
) -> object:
    value = payload.get(key)
    if isinstance(value, Mapping):
        return value.get(nested_key, default)
    return default


def _counter_payload(counter: Counter[str]) -> list[dict[str, object]]:
    return [
        {"value": value, "count": count}
        for value, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
        if value
    ]


def _counter_table(rows: Sequence[Mapping[str, object]], label: str) -> list[str]:
    lines = [f"| {label} | Count |", "| --- | ---: |"]
    for row in rows:
        lines.append(f"| `{row.get('value', '')}` | {row.get('count', 0)} |")
    return lines


def _int_value(value: object, default: int = 0) -> int:
    if type(value) is bool:
        return default
    if isinstance(value, int):
        return value
    return default


def _require_non_negative_int(name: str, value: object) -> int:
    if type(value) is bool or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer.")
    if value < 0:
        raise ValueError(f"{name} cannot be negative.")
    return value


def _string_sequence(name: str, value: Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, str):
        raise ValueError(f"{name} must be a sequence of strings.")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{name}[{index}] must be a non-empty string.")
        result.append(item)
    return tuple(result)


def _require_positive_int(name: str, value: object) -> int:
    number = _require_non_negative_int(name, value)
    if number == 0:
        raise ValueError(f"{name} must be positive.")
    return number


if __name__ == "__main__":
    raise SystemExit(main())
