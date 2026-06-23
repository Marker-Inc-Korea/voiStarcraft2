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

    def __post_init__(self) -> None:
        if not self.roots:
            raise ValueError("at least one soak history root is required.")
        for root in self.roots:
            if not isinstance(root, Path):
                raise ValueError("roots must contain Path values.")
        _require_positive_int("recent_limit", self.recent_limit)


def aggregate_matrix_run(
    run_dir: Path | str,
    *,
    target_frame: int,
    timeout_seconds: int,
) -> dict[str, object]:
    """Build one deterministic matrix_report.json payload from case reports."""

    root = Path(run_dir)
    target = _require_non_negative_int("target_frame", target_frame)
    timeout = _require_non_negative_int("timeout_seconds", timeout_seconds)
    cases: list[dict[str, object]] = []
    passed = 0
    failed = 0
    for case_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        report_path = case_dir / "soak_report.json"
        case: dict[str, object] = {
            "case_id": case_dir.name,
            "case_dir": str(case_dir),
            "report": str(report_path),
        }
        if not report_path.exists():
            case.update({"status": "missing_report", "ok": False, "failures": []})
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
                "latest_frame": payload.get("latest_frame"),
                "macro_evidence_ok": payload.get("macro_evidence_ok"),
                "manager_intervention_ok": payload.get("manager_intervention_ok"),
                "failures": flattened_failures,
                "failure_codes": failure_codes,
                "attempts": (
                    payload["attempts"] if isinstance(payload.get("attempts"), list) else []
                ),
                "selected_attempt": payload.get("selected_attempt"),
                "artifact_manifest": (
                    payload["artifact_manifest"]
                    if isinstance(payload.get("artifact_manifest"), Mapping)
                    else {}
                ),
                "map_file": _case_value(payload, "map_file"),
                "enemy_race": _case_value(payload, "enemy_race"),
                "enemy_difficulty": _case_value(payload, "enemy_difficulty"),
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
        runs.append(
            {
                "run_id": report_path.parent.name,
                "report": str(report_path),
                "artifact_dir": str(report_path.parent),
                "ok": run_ok,
                "status": payload.get("status", "unknown"),
                "case_count": payload.get("case_count", len(case_list)),
                "passed": passed_cases,
                "failed": failed_cases,
                "target_frame": payload.get("target_frame"),
                "timeout_seconds": payload.get("timeout_seconds"),
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
        "## Failure Codes",
        "",
    ]
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

    history = subparsers.add_parser("history-dashboard")
    history.add_argument("--root", action="append", required=True)
    history.add_argument("--output-json", required=True)
    history.add_argument("--output-markdown", required=True)
    history.add_argument("--recent-limit", type=int, default=20)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    if args.command == "matrix-report":
        payload = aggregate_matrix_run(
            Path(args.run_dir),
            target_frame=args.target_frame,
            timeout_seconds=args.timeout_seconds,
        )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return 0
    dashboard = aggregate_soak_history(
        SoakHistoryConfig(
            roots=tuple(Path(root) for root in args.root),
            recent_limit=args.recent_limit,
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


def _read_json_mapping(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return payload


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


def _require_positive_int(name: str, value: object) -> int:
    number = _require_non_negative_int(name, value)
    if number == 0:
        raise ValueError(f"{name} must be positive.")
    return number


if __name__ == "__main__":
    raise SystemExit(main())
