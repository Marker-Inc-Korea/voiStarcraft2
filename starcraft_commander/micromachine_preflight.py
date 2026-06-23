"""Preflight checks for MicroMachine map qualification runs.

The preflight is deliberately conservative and stdlib-only. It does not try to
replace MicroMachine geometry logic or inject build positions; it turns known
manifest state and optional local map availability into auditable evidence
before a long SC2 soak starts.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from starcraft_commander.micromachine_map_pool import (
    DEFAULT_MAP_POOL_PATH,
    MicroMachineMapEntry,
    load_micromachine_map_pool,
)


PREFLIGHT_REPORT_NAME: Final[str] = "preflight_report.json"


@dataclass(frozen=True)
class MicroMachineMapPreflightConfig:
    """Inputs for one map preflight check."""

    map_file: str
    qualification_tier: str = "production"
    manifest_path: Path = DEFAULT_MAP_POOL_PATH
    map_roots: tuple[Path, ...] = ()


def preflight_micromachine_map(config: MicroMachineMapPreflightConfig) -> dict[str, object]:
    """Return a JSON-ready preflight report for one matrix case."""

    pool = load_micromachine_map_pool(config.manifest_path)
    tier = pool.tier(config.qualification_tier)
    entry = _find_entry(pool.maps, config.map_file)
    checks: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    map_path = _find_map_path(config.map_file, config.map_roots)

    if entry is None:
        failures.append(
            _failure(
                "unsupported_map",
                f"{config.map_file} is not declared in the MicroMachine map pool.",
            )
        )
        checks.append({"name": "manifest_class", "ok": False, "status": "unknown"})
        classification = "unknown"
        manifest_status = "unknown"
        expected_start_locations = None
        risk_codes: tuple[str, ...] = ()
        notes = ""
        promotion_rule = ""
        reason = ""
        blocker = None
    else:
        classification = entry.classification
        manifest_status = entry.status
        expected_start_locations = entry.expected_start_locations
        risk_codes = entry.preflight_risk_codes
        notes = entry.preflight_notes
        promotion_rule = entry.promotion_rule
        reason = entry.reason
        blocker = entry.blocker
        tier_allows_class = classification in tier.map_classifications
        checks.append(
            {
                "name": "manifest_class",
                "ok": tier_allows_class,
                "classification": classification,
                "tier": tier.name,
            }
        )
        if not tier_allows_class:
            failures.append(
                _failure(
                    "unsupported_map",
                    f"{config.map_file} is {classification}, not part of {tier.name} tier.",
                )
            )
        if classification == "excluded":
            failures.append(
                _failure("unsupported_map", f"{config.map_file} is explicitly excluded.")
            )
        for code in risk_codes:
            if code == "unsupported_map":
                failures.append(_failure(code, reason or "Map is unsupported."))
            elif code in {"geometry_risk", "placement_risk"}:
                failures.append(_failure(code, notes or reason or f"Known {code}."))
            else:
                failures.append(_failure(code, notes or reason or f"Known {code}."))

    if config.map_roots:
        map_exists = map_path is not None
        checks.append(
            {
                "name": "map_availability",
                "ok": map_exists,
                "map_roots": [str(path) for path in config.map_roots],
                "resolved_path": str(map_path) if map_path is not None else None,
            }
        )
        if not map_exists:
            failures.append(
                _failure(
                    "missing_map",
                    f"{config.map_file} was not found in configured map roots.",
                )
            )
    else:
        checks.append(
            {
                "name": "map_availability",
                "ok": True,
                "status": "not_checked",
                "reason": "No map roots configured.",
            }
        )

    if expected_start_locations is not None:
        checks.append(
            {
                "name": "expected_start_locations",
                "ok": expected_start_locations >= 2,
                "expected_start_locations": expected_start_locations,
            }
        )
        if expected_start_locations < 2:
            failures.append(
                _failure(
                    "geometry_risk",
                    "MicroMachine production maps require at least two expected starts.",
                )
            )

    failure_codes = sorted(
        {failure["code"] for failure in failures if isinstance(failure.get("code"), str)}
    )
    ok = not failures
    return {
        "status": "passed" if ok else "failed",
        "ok": ok,
        "map_file": config.map_file,
        "qualification_tier": tier.name,
        "classification": classification,
        "manifest_status": manifest_status,
        "production_blocking": tier.name == "production" and not ok,
        "skip_runtime": not ok,
        "failure_codes": failure_codes,
        "failures": failures,
        "checks": checks,
        "map_path": str(map_path) if map_path is not None else None,
        "expected_start_locations": expected_start_locations,
        "preflight_risk_codes": list(risk_codes),
        "preflight_notes": notes,
        "promotion_rule": promotion_rule,
        "reason": reason,
        "blocker": dict(blocker) if blocker is not None else None,
    }


def write_preflight_failure_soak_report(
    preflight: Mapping[str, object],
    output: Path,
    *,
    enemy_race: str,
    enemy_difficulty: int,
    target_frame: int,
    timeout_seconds: int,
) -> None:
    """Write a soak_report.json-compatible failure for skipped runtime cases."""

    failures = preflight.get("failures", [])
    failure_list = failures if isinstance(failures, list) else []
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "status": "failed",
                "ok": False,
                "latest_frame": 0,
                "map_file": preflight.get("map_file"),
                "enemy_race": enemy_race,
                "enemy_difficulty": enemy_difficulty,
                "target_frame": target_frame,
                "timeout_seconds": timeout_seconds,
                "macro_evidence_ok": False,
                "manager_intervention_ok": False,
                "preflight": preflight,
                "failures": failure_list,
                "failure_codes": preflight.get("failure_codes", []),
                "termination_reason": "preflight_failed",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preflight a MicroMachine matrix map.")
    parser.add_argument("--map-file", required=True)
    parser.add_argument("--qualification-tier", default="production")
    parser.add_argument("--manifest", default=str(DEFAULT_MAP_POOL_PATH))
    parser.add_argument("--map-root", action="append", default=[])
    parser.add_argument("--output")
    parser.add_argument("--write-soak-report")
    parser.add_argument("--enemy-race", default="Zerg")
    parser.add_argument("--enemy-difficulty", type=int, default=1)
    parser.add_argument("--target-frame", type=int, default=12000)
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    report = preflight_micromachine_map(
        MicroMachineMapPreflightConfig(
            map_file=args.map_file,
            qualification_tier=args.qualification_tier,
            manifest_path=Path(args.manifest),
            map_roots=tuple(Path(root) for root in args.map_root if root),
        )
    )
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    if args.write_soak_report and not report["ok"]:
        write_preflight_failure_soak_report(
            report,
            Path(args.write_soak_report),
            enemy_race=args.enemy_race,
            enemy_difficulty=args.enemy_difficulty,
            target_frame=args.target_frame,
            timeout_seconds=args.timeout_seconds,
        )
    return 0 if report["ok"] else 1


def _find_entry(
    entries: Sequence[MicroMachineMapEntry],
    map_file: str,
) -> MicroMachineMapEntry | None:
    for entry in entries:
        if entry.map_file == map_file:
            return entry
    return None


def _find_map_path(map_file: str, map_roots: Sequence[Path]) -> Path | None:
    candidate = Path(map_file)
    if candidate.is_absolute() and candidate.exists():
        return candidate
    for root in map_roots:
        path = root / map_file
        if path.exists():
            return path
    return None


def _failure(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message, "severity": "terminal"}


if __name__ == "__main__":
    raise SystemExit(main())
