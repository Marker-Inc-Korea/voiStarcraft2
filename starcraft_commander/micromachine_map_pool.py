"""MicroMachine production map-pool contract.

The map pool is intentionally explicit: production support means the required
pool in the manifest, not an unbounded claim over every SC2 custom map.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final


REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
DEFAULT_MAP_POOL_PATH: Final[Path] = (
    REPO_ROOT / "integrations" / "micromachine" / "MICROMACHINE_MAP_POOL.json"
)
MAP_CLASSIFICATIONS: Final[frozenset[str]] = frozenset(
    {"required", "diagnostic", "excluded"}
)
ENEMY_RACES: Final[frozenset[str]] = frozenset(
    {"Terran", "Protoss", "Zerg", "Random"}
)


@dataclass(frozen=True)
class MicroMachineMapEntry:
    """One map in the production qualification contract."""

    map_file: str
    display_name: str
    classification: str
    status: str
    reason: str
    promotion_rule: str
    expected_start_locations: int | None = None
    preflight_risk_codes: tuple[str, ...] = ()
    preflight_notes: str = ""
    blocker: Mapping[str, object] | None = None


@dataclass(frozen=True)
class MicroMachineQualificationTier:
    """Named matrix defaults for a MicroMachine qualification run."""

    name: str
    description: str
    map_classifications: tuple[str, ...]
    enemy_races: tuple[str, ...]
    enemy_difficulties: tuple[int, ...]
    target_frame: int
    timeout_seconds: int
    strategy_profiles: tuple[str, ...]
    allow_failures: bool = False


@dataclass(frozen=True)
class MicroMachineMapPool:
    """Validated MicroMachine production map-pool manifest."""

    schema_version: int
    parent_issue: int
    default_tier: str
    qualification_requires_failed_zero: bool
    production_allows_failures: bool
    maps: tuple[MicroMachineMapEntry, ...]
    tiers: Mapping[str, MicroMachineQualificationTier]

    def maps_for_tier(self, tier_name: str | None = None) -> tuple[MicroMachineMapEntry, ...]:
        tier = self.tier(tier_name)
        allowed = set(tier.map_classifications)
        return tuple(entry for entry in self.maps if entry.classification in allowed)

    def map_files_for_tier(self, tier_name: str | None = None) -> tuple[str, ...]:
        return tuple(entry.map_file for entry in self.maps_for_tier(tier_name))

    def tier(self, tier_name: str | None = None) -> MicroMachineQualificationTier:
        name = tier_name or self.default_tier
        try:
            return self.tiers[name]
        except KeyError as exc:
            raise ValueError(f"unknown MicroMachine qualification tier: {name}") from exc

    def to_summary(self, tier_name: str | None = None) -> dict[str, object]:
        tier = self.tier(tier_name)
        maps = self.maps_for_tier(tier.name)
        return {
            "schema_version": self.schema_version,
            "parent_issue": self.parent_issue,
            "default_tier": self.default_tier,
            "selected_tier": tier.name,
            "qualification_requires_failed_zero": self.qualification_requires_failed_zero,
            "production_allows_failures": self.production_allows_failures,
            "map_files": [entry.map_file for entry in maps],
            "maps": [
                {
                    "map_file": entry.map_file,
                    "display_name": entry.display_name,
                    "classification": entry.classification,
                    "status": entry.status,
                    "reason": entry.reason,
                    "promotion_rule": entry.promotion_rule,
                    "expected_start_locations": entry.expected_start_locations,
                    "preflight_risk_codes": list(entry.preflight_risk_codes),
                    "preflight_notes": entry.preflight_notes,
                    "blocker": dict(entry.blocker) if entry.blocker is not None else None,
                }
                for entry in maps
            ],
            "enemy_races": list(tier.enemy_races),
            "enemy_difficulties": list(tier.enemy_difficulties),
            "target_frame": tier.target_frame,
            "timeout_seconds": tier.timeout_seconds,
            "strategy_profiles": list(tier.strategy_profiles),
            "allow_failures": tier.allow_failures,
        }


def load_micromachine_map_pool(
    path: Path | str = DEFAULT_MAP_POOL_PATH,
) -> MicroMachineMapPool:
    """Load and validate the MicroMachine map-pool manifest."""

    manifest_path = Path(path)
    payload = json.loads(manifest_path.read_text())
    if not isinstance(payload, Mapping):
        raise ValueError("MicroMachine map-pool manifest must be a JSON object.")
    return parse_micromachine_map_pool(payload)


def parse_micromachine_map_pool(payload: Mapping[str, object]) -> MicroMachineMapPool:
    """Validate a MicroMachine map-pool mapping."""

    schema_version = _require_int(payload, "schema_version", minimum=1)
    contract = _require_mapping(payload, "contract")
    default_tier = _require_string(contract, "default_tier")
    parent_issue = _require_int(contract, "parent_issue", minimum=1)
    qualification_requires_failed_zero = _require_bool(
        contract, "qualification_requires_failed_zero"
    )
    production_allows_failures = _require_bool(contract, "production_allows_failures")
    maps = _parse_maps(payload.get("maps"))
    tiers = _parse_tiers(payload.get("tiers"))
    if default_tier not in tiers:
        raise ValueError("contract.default_tier must reference a tier.")
    if not any(entry.classification == "required" for entry in maps):
        raise ValueError("map pool must contain at least one required map.")
    if not qualification_requires_failed_zero:
        raise ValueError("production qualification must require failed=0.")
    production_tier = tiers.get("production")
    if production_tier is None:
        raise ValueError("map pool must define a production tier.")
    if default_tier != "production":
        raise ValueError("contract.default_tier must be production.")
    if production_tier.map_classifications != ("required",):
        raise ValueError("production tier must include only required maps.")
    for entry in maps:
        if entry.blocker is not None and entry.classification != "diagnostic":
            raise ValueError(
                "maps with active blocker metadata must remain diagnostic until promoted."
            )
    if not any(entry.classification == "diagnostic" for entry in maps):
        raise ValueError("map pool must contain at least one diagnostic map.")
    if not any(entry.classification == "excluded" for entry in maps):
        raise ValueError("map pool must contain at least one excluded map.")
    if production_tier.allow_failures or production_allows_failures:
        raise ValueError("production qualification cannot allow failures.")
    if any(entry.classification == "diagnostic" for entry in maps):
        for entry in maps:
            if entry.classification == "diagnostic" and not entry.promotion_rule:
                raise ValueError("diagnostic maps must include a promotion rule.")
    return MicroMachineMapPool(
        schema_version=schema_version,
        parent_issue=parent_issue,
        default_tier=default_tier,
        qualification_requires_failed_zero=qualification_requires_failed_zero,
        production_allows_failures=production_allows_failures,
        maps=maps,
        tiers=tiers,
    )


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read the MicroMachine map-pool contract.")
    parser.add_argument("--manifest", default=str(DEFAULT_MAP_POOL_PATH))
    parser.add_argument("--tier", default=None)
    parser.add_argument(
        "--field",
        choices=(
            "map_files",
            "enemy_races",
            "enemy_difficulties",
            "target_frame",
            "timeout_seconds",
            "strategy_profiles",
            "allow_failures",
        ),
        help="Print one shell-friendly matrix field instead of JSON.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    pool = load_micromachine_map_pool(args.manifest)
    summary = pool.to_summary(args.tier)
    if args.field is not None:
        value = summary[args.field]
        if isinstance(value, list):
            print(" ".join(str(item) for item in value))
        elif isinstance(value, bool):
            print("1" if value else "0")
        else:
            print(value)
        return 0
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _parse_maps(value: object) -> tuple[MicroMachineMapEntry, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("maps must be a non-empty list.")
    seen: set[str] = set()
    entries: list[MicroMachineMapEntry] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"maps[{index}] must be an object.")
        map_file = _require_string(item, "map_file")
        if map_file in seen:
            raise ValueError(f"duplicate map_file in map pool: {map_file}")
        seen.add(map_file)
        classification = _require_string(item, "classification")
        if classification not in MAP_CLASSIFICATIONS:
            raise ValueError(f"unsupported map classification: {classification}")
        entries.append(
            _parse_map_entry(item, map_file=map_file, classification=classification)
        )
    return tuple(entries)


def _parse_map_entry(
    item: Mapping[str, object],
    *,
    map_file: str,
    classification: str,
) -> MicroMachineMapEntry:
    preflight = item.get("preflight", {})
    if preflight is None:
        preflight = {}
    if not isinstance(preflight, Mapping):
        raise ValueError(f"preflight for {map_file} must be an object.")
    expected_start_locations = preflight.get("expected_start_locations")
    if expected_start_locations is not None:
        if type(expected_start_locations) is bool or not isinstance(
            expected_start_locations, int
        ):
            raise ValueError(f"preflight.expected_start_locations for {map_file} must be an integer.")
        if expected_start_locations <= 0:
            raise ValueError(f"preflight.expected_start_locations for {map_file} must be positive.")
    risk_codes = preflight.get("risk_codes", [])
    if not isinstance(risk_codes, list):
        raise ValueError(f"preflight.risk_codes for {map_file} must be a list.")
    parsed_risk_codes: list[str] = []
    for index, code in enumerate(risk_codes):
        if not isinstance(code, str) or not code:
            raise ValueError(f"preflight.risk_codes[{index}] for {map_file} must be a string.")
        parsed_risk_codes.append(code)
    notes = preflight.get("notes", "")
    if not isinstance(notes, str):
        raise ValueError(f"preflight.notes for {map_file} must be a string.")
    blocker = _parse_blocker(item.get("blocker"), map_file=map_file)
    return MicroMachineMapEntry(
        map_file=map_file,
        display_name=_require_string(item, "display_name"),
        classification=classification,
        status=_require_string(item, "status"),
        reason=_require_string(item, "reason"),
        promotion_rule=_require_string(item, "promotion_rule"),
        expected_start_locations=expected_start_locations,
        preflight_risk_codes=tuple(parsed_risk_codes),
        preflight_notes=notes,
        blocker=blocker,
    )


def _parse_blocker(value: object, *, map_file: str) -> Mapping[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"blocker for {map_file} must be an object.")

    blocker: dict[str, object] = {
        "code": _require_string(value, "code"),
        "runtime_failure_code": _require_string(value, "runtime_failure_code"),
        "artifact_path": _require_string(value, "artifact_path"),
        "root_cause_area": _require_string(value, "root_cause_area"),
        "root_cause_candidates": list(_string_tuple(value, "root_cause_candidates")),
        "less_likely_candidates": list(_string_tuple(value, "less_likely_candidates")),
        "evidence_signatures": list(_string_tuple(value, "evidence_signatures")),
        "reproduction_command": _require_string(value, "reproduction_command"),
        "promotion_criteria": list(_string_tuple(value, "promotion_criteria")),
    }
    return blocker


def _parse_tiers(value: object) -> dict[str, MicroMachineQualificationTier]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("tiers must be a non-empty object.")
    tiers: dict[str, MicroMachineQualificationTier] = {}
    for name, item in value.items():
        if not isinstance(name, str) or not name:
            raise ValueError("tier names must be non-empty strings.")
        if not isinstance(item, Mapping):
            raise ValueError(f"tier {name} must be an object.")
        classifications = _string_tuple(item, "map_classifications")
        invalid_classifications = set(classifications) - MAP_CLASSIFICATIONS
        if invalid_classifications:
            raise ValueError(
                f"tier {name} has unsupported map classification(s): "
                + ", ".join(sorted(invalid_classifications))
            )
        races = _string_tuple(item, "enemy_races")
        invalid_races = set(races) - ENEMY_RACES
        if invalid_races:
            raise ValueError(
                f"tier {name} has unsupported enemy race(s): "
                + ", ".join(sorted(invalid_races))
            )
        difficulties = _int_tuple(item, "enemy_difficulties", minimum=1, maximum=10)
        tiers[name] = MicroMachineQualificationTier(
            name=name,
            description=_require_string(item, "description"),
            map_classifications=classifications,
            enemy_races=races,
            enemy_difficulties=difficulties,
            target_frame=_require_int(item, "target_frame", minimum=1),
            timeout_seconds=_require_int(item, "timeout_seconds", minimum=1),
            strategy_profiles=_string_tuple(item, "strategy_profiles"),
            allow_failures=_optional_bool(item, "allow_failures", default=False),
        )
    return tiers


def _require_mapping(payload: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be an object.")
    return value


def _require_string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string.")
    return value


def _require_bool(payload: Mapping[str, object], key: str) -> bool:
    value = payload.get(key)
    if type(value) is not bool:
        raise ValueError(f"{key} must be a boolean.")
    return value


def _optional_bool(payload: Mapping[str, object], key: str, *, default: bool) -> bool:
    if key not in payload:
        return default
    return _require_bool(payload, key)


def _require_int(
    payload: Mapping[str, object],
    key: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    value = payload.get(key)
    if type(value) is bool or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer.")
    if minimum is not None and value < minimum:
        raise ValueError(f"{key} must be at least {minimum}.")
    if maximum is not None and value > maximum:
        raise ValueError(f"{key} must be at most {maximum}.")
    return value


def _string_tuple(payload: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"{key} must be a non-empty list.")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{key}[{index}] must be a non-empty string.")
        result.append(item)
    return tuple(result)


def _int_tuple(
    payload: Mapping[str, object],
    key: str,
    *,
    minimum: int,
    maximum: int,
) -> tuple[int, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"{key} must be a non-empty list.")
    result: list[int] = []
    for index, item in enumerate(value):
        if type(item) is bool or not isinstance(item, int):
            raise ValueError(f"{key}[{index}] must be an integer.")
        if item < minimum or item > maximum:
            raise ValueError(f"{key}[{index}] must be between {minimum} and {maximum}.")
        result.append(item)
    return tuple(result)


if __name__ == "__main__":
    raise SystemExit(main())
