"""Long-run soak classification for patched MicroMachine collaborations.

The soak gate is intentionally stdlib-only so it can run on the same local
machine that launches StarCraft II. It classifies runtime artifacts emitted by
the patched C++ bot without importing SC2, s2client-api, or MicroMachine.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Mapping, Sequence


DEFAULT_REQUIRED_MACRO_TERMS: Final[tuple[str, ...]] = (
    "build command type=TERRAN_SUPPLYDEPOT",
    "TERRAN_SUPPLYDEPOT UnderConstruction",
    "build command type=TERRAN_BARRACKS",
    "TERRAN_BARRACKS UnderConstruction",
    "build command type=TERRAN_REFINERY",
)
DEFAULT_POST_BARRACKS_UNIT_TERMS: Final[tuple[str, ...]] = (
    "create unit item=Marine result=1",
    "create unit item=Reaper result=1",
)
PLACEMENT_FAILURE_TERMS: Final[tuple[str, ...]] = (
    "Failed to place",
    "Path to building is not safe",
    "Cancel building TERRAN_SUPPLYDEPOT :",
    "Cancel building TERRAN_BARRACKS :",
    "Cancel building TERRAN_REFINERY :",
)
DISCONNECT_TERMS: Final[tuple[str, ...]] = (
    "Connection closed",
    "Connection reset",
    "Disconnected",
    "WaitJoinGame failed",
    "Protocol error",
    "CreateGame failed",
    "JoinGame failed",
)
PRODUCTION_TERMS: Final[tuple[str, ...]] = (
    "build command type=",
    "create unit item=",
    "create upgrade item=",
)


@dataclass(frozen=True)
class MicroMachineSoakConfig:
    """Configurable production sign-off thresholds for a local soak run."""

    target_frame: int = 12_000
    timeout_seconds: int = 1_200
    telemetry_stall_seconds: int = 90
    production_deadlock_frame: int = 9_000
    production_stall_frames: int = 8_000
    income_stall_frames: int = 2_000
    max_placement_failures: int = 3
    modulation_consumption_grace_frames: int = 128
    require_macro_evidence: bool = True
    require_manager_intervention: bool = True
    expected_profile_tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_positive("target_frame", self.target_frame)
        _require_positive("timeout_seconds", self.timeout_seconds)
        _require_positive("telemetry_stall_seconds", self.telemetry_stall_seconds)
        _require_positive("production_deadlock_frame", self.production_deadlock_frame)
        _require_positive("production_stall_frames", self.production_stall_frames)
        _require_positive("income_stall_frames", self.income_stall_frames)
        _require_positive(
            "modulation_consumption_grace_frames",
            self.modulation_consumption_grace_frames,
        )
        if type(self.max_placement_failures) is bool or self.max_placement_failures < 0:
            raise ValueError("max_placement_failures must be a non-negative integer.")
        object.__setattr__(
            self,
            "expected_profile_tags",
            _string_tuple("expected_profile_tags", self.expected_profile_tags),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "target_frame": self.target_frame,
            "timeout_seconds": self.timeout_seconds,
            "telemetry_stall_seconds": self.telemetry_stall_seconds,
            "production_deadlock_frame": self.production_deadlock_frame,
            "production_stall_frames": self.production_stall_frames,
            "income_stall_frames": self.income_stall_frames,
            "max_placement_failures": self.max_placement_failures,
            "modulation_consumption_grace_frames": self.modulation_consumption_grace_frames,
            "require_macro_evidence": self.require_macro_evidence,
            "require_manager_intervention": self.require_manager_intervention,
            "expected_profile_tags": list(self.expected_profile_tags),
        }


@dataclass(frozen=True)
class MicroMachineSoakObservation:
    """Snapshot of the runtime artifacts used by the soak classifiers."""

    blackboard_dir: Path
    bot_log: Path
    artifact_dir: Path | None = None
    bot_exit_code: int | None = None
    bot_running: bool = True
    termination_reason: str | None = None
    now_seconds: float = field(default_factory=time.time)

    @property
    def latest_telemetry_path(self) -> Path:
        return self.blackboard_dir / "latest_telemetry.json"

    @property
    def telemetry_archive_path(self) -> Path:
        return self.blackboard_dir / "telemetry.jsonl"

    @property
    def latest_modulation_path(self) -> Path:
        return self.blackboard_dir / "latest_modulation.json"

    @property
    def modulation_archive_path(self) -> Path:
        return self.blackboard_dir / "modulation_updates.jsonl"

    def to_dict(self) -> dict[str, object]:
        return {
            "blackboard_dir": str(self.blackboard_dir),
            "bot_log": str(self.bot_log),
            "artifact_dir": str(self.artifact_dir) if self.artifact_dir else None,
            "bot_exit_code": self.bot_exit_code,
            "bot_running": self.bot_running,
            "termination_reason": self.termination_reason,
            "now_seconds": self.now_seconds,
        }


@dataclass(frozen=True)
class MicroMachineSoakFailure:
    """One terminal or blocking soak failure."""

    code: str
    message: str
    severity: str = "terminal"
    evidence: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class MicroMachineSoakReport:
    """JSON-ready report consumed by scripts, PR evidence, and sign-off docs."""

    status: str
    config: MicroMachineSoakConfig
    observation: MicroMachineSoakObservation
    latest_frame: int
    target_reached: bool
    macro_evidence_ok: bool
    manager_intervention_ok: bool
    failures: tuple[MicroMachineSoakFailure, ...] = ()
    artifact_manifest: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "passed" and not self.failures

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "ok": self.ok,
            "config": self.config.to_dict(),
            "observation": _observation_report_payload(self.observation),
            "latest_frame": self.latest_frame,
            "target_reached": self.target_reached,
            "macro_evidence_ok": self.macro_evidence_ok,
            "manager_intervention_ok": self.manager_intervention_ok,
            "failures": [failure.to_dict() for failure in self.failures],
            "artifact_manifest": self.artifact_manifest,
        }

    def write_json(self, path: Path | str) -> None:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n")


def _observation_report_payload(
    observation: MicroMachineSoakObservation,
) -> dict[str, object]:
    payload = observation.to_dict()
    telemetry = _read_json_mapping(observation.latest_telemetry_path)
    active_ids = telemetry.get("active_modulation_ids")
    if isinstance(active_ids, list):
        payload["active_modulation_ids"] = [
            value for value in active_ids if isinstance(value, str) and value
        ]
    return payload


def classify_micromachine_soak(
    observation: MicroMachineSoakObservation,
    config: MicroMachineSoakConfig | None = None,
) -> MicroMachineSoakReport:
    """Classify the current MicroMachine soak artifacts."""

    resolved_config = config or MicroMachineSoakConfig()
    telemetry = _read_json_mapping(observation.latest_telemetry_path)
    telemetry_archive = _read_jsonl_mappings(observation.telemetry_archive_path)
    latest_frame = _latest_frame(telemetry, telemetry_archive)
    log_text = _read_text(observation.bot_log)
    failures: list[MicroMachineSoakFailure] = []

    if observation.bot_exit_code not in (None, 0) and latest_frame < resolved_config.target_frame:
        failures.append(
            MicroMachineSoakFailure(
                code="micromachine_crash",
                message="MicroMachine process exited before the target frame.",
                evidence={
                    "bot_exit_code": observation.bot_exit_code,
                    "latest_frame": latest_frame,
                    "target_frame": resolved_config.target_frame,
                },
            )
        )
    if not observation.bot_running and latest_frame < resolved_config.target_frame:
        failures.append(
            MicroMachineSoakFailure(
                code="micromachine_process_stopped",
                message="MicroMachine process stopped before the target frame.",
                evidence={"latest_frame": latest_frame, "target_frame": resolved_config.target_frame},
            )
        )

    disconnects = _matching_terms(log_text, DISCONNECT_TERMS)
    if disconnects:
        failures.append(
            MicroMachineSoakFailure(
                code="sc2_disconnect",
                message="SC2 API connection emitted disconnect/failure evidence.",
                evidence={"terms": disconnects},
            )
        )

    if not telemetry and not telemetry_archive:
        failures.append(
            MicroMachineSoakFailure(
                code="telemetry_missing",
                message="MicroMachine did not emit latest_telemetry.json.",
                evidence={"path": str(observation.latest_telemetry_path)},
            )
        )
    elif not _telemetry_recent(observation, resolved_config):
        failures.append(
            MicroMachineSoakFailure(
                code="telemetry_stall",
                message="Telemetry file stopped updating before the target frame.",
                evidence={
                    "latest_frame": latest_frame,
                    "target_frame": resolved_config.target_frame,
                    "telemetry_stall_seconds": resolved_config.telemetry_stall_seconds,
                },
            )
        )

    no_start_units_failure = _classify_bootstrap_no_start_units(telemetry, telemetry_archive)
    if no_start_units_failure is not None:
        failures.append(no_start_units_failure)

    placement_failure_count = sum(log_text.count(term) for term in PLACEMENT_FAILURE_TERMS)
    if placement_failure_count > resolved_config.max_placement_failures:
        failures.append(
            MicroMachineSoakFailure(
                code="repeated_placement_failures",
                message="Repeated placement/path/cancel failures exceeded threshold.",
                evidence={
                    "count": placement_failure_count,
                    "max_placement_failures": resolved_config.max_placement_failures,
                },
            )
        )

    macro_evidence_ok = has_required_macro_evidence(log_text)
    if (
        resolved_config.require_macro_evidence
        and latest_frame >= resolved_config.production_deadlock_frame
        and not macro_evidence_ok
    ):
        failures.append(
            MicroMachineSoakFailure(
                code="no_production_deadlock",
                message="Opening production evidence did not appear by the deadlock frame.",
                evidence={
                    "latest_frame": latest_frame,
                    "production_deadlock_frame": resolved_config.production_deadlock_frame,
                    "missing_terms": missing_macro_evidence(log_text),
                },
            )
        )

    last_production_frame = _last_log_frame_for_terms(log_text, PRODUCTION_TERMS)
    if (
        latest_frame >= resolved_config.target_frame
        and last_production_frame is not None
        and latest_frame - last_production_frame > resolved_config.production_stall_frames
        and not _has_recent_combat_activity(
            log_text,
            max(0, latest_frame - resolved_config.production_stall_frames),
        )
    ):
        failures.append(
            MicroMachineSoakFailure(
                code="production_stall",
                message="No production log evidence appeared in the configured frame window.",
                evidence={
                    "latest_frame": latest_frame,
                    "last_production_frame": last_production_frame,
                    "production_stall_frames": resolved_config.production_stall_frames,
                },
            )
        )

    recent_income_missing = _missing_recent_positive_income(
        log_text,
        latest_frame,
        resolved_config.income_stall_frames,
    )
    if latest_frame >= resolved_config.target_frame and recent_income_missing:
        failures.append(
            MicroMachineSoakFailure(
                code="income_stall",
                message="Recent mineral/gas income evidence is missing near the target frame.",
                evidence={
                    "latest_frame": latest_frame,
                    "income_stall_frames": resolved_config.income_stall_frames,
                    "missing": recent_income_missing,
                },
            )
        )

    manager_intervention_ok = _manager_intervention_ok(telemetry, telemetry_archive)
    if resolved_config.require_manager_intervention and latest_frame >= resolved_config.target_frame:
        if not manager_intervention_ok:
            failures.append(
                MicroMachineSoakFailure(
                    code="manager_intervention_missing",
                    message="CombatCommander and ScoutManager bounded intervention evidence is missing.",
                    evidence={"latest_frame": latest_frame},
                )
            )

    stale_failure = _classify_stale_modulation(
        observation,
        telemetry,
        telemetry_archive,
        latest_frame,
        resolved_config,
    )
    if stale_failure is not None:
        failures.append(stale_failure)
    profile_failure = _classify_expected_profile_tags(
        observation,
        latest_frame,
        resolved_config,
    )
    if profile_failure is not None:
        failures.append(profile_failure)

    target_reached = latest_frame >= resolved_config.target_frame
    status = "passed" if target_reached and macro_evidence_ok and manager_intervention_ok and not failures else "failed"
    return MicroMachineSoakReport(
        status=status,
        config=resolved_config,
        observation=observation,
        latest_frame=latest_frame,
        target_reached=target_reached,
        macro_evidence_ok=macro_evidence_ok,
        manager_intervention_ok=manager_intervention_ok,
        failures=tuple(failures),
        artifact_manifest=build_artifact_manifest(observation),
    )


def has_required_macro_evidence(log_text: str) -> bool:
    """Return whether opening macro evidence proves real MicroMachine play."""

    if any(term not in log_text for term in DEFAULT_REQUIRED_MACRO_TERMS):
        return False
    if not any(term in log_text for term in DEFAULT_POST_BARRACKS_UNIT_TERMS):
        return False
    return _has_positive_gas_income(log_text) and _has_positive_mineral_income(log_text)


def missing_macro_evidence(log_text: str) -> list[str]:
    missing = [term for term in DEFAULT_REQUIRED_MACRO_TERMS if term not in log_text]
    if not any(term in log_text for term in DEFAULT_POST_BARRACKS_UNIT_TERMS):
        missing.append("post-Barracks unit creation")
    if not _has_positive_gas_income(log_text):
        missing.append("positive gas income")
    if not _has_positive_mineral_income(log_text):
        missing.append("positive mineral income")
    return missing


def build_artifact_manifest(observation: MicroMachineSoakObservation) -> dict[str, str]:
    """Return deterministic relative artifact names for archive/sign-off."""

    root = observation.artifact_dir or observation.blackboard_dir
    candidates = {
        "bot_log": observation.bot_log,
        "latest_telemetry": observation.latest_telemetry_path,
        "telemetry_archive": observation.telemetry_archive_path,
        "latest_modulation": observation.latest_modulation_path,
        "modulation_archive": observation.modulation_archive_path,
    }
    manifest: dict[str, str] = {}
    for key, path in candidates.items():
        if not path.exists():
            continue
        try:
            manifest[key] = str(path.relative_to(root))
        except ValueError:
            manifest[key] = str(path)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Classify a MicroMachine soak run.")
    parser.add_argument("--blackboard-dir", required=True)
    parser.add_argument("--bot-log", required=True)
    parser.add_argument("--artifact-dir")
    parser.add_argument("--report")
    defaults = MicroMachineSoakConfig()
    parser.add_argument("--target-frame", type=int, default=defaults.target_frame)
    parser.add_argument("--timeout-seconds", type=int, default=defaults.timeout_seconds)
    parser.add_argument(
        "--telemetry-stall-seconds",
        type=int,
        default=defaults.telemetry_stall_seconds,
    )
    parser.add_argument(
        "--production-deadlock-frame",
        type=int,
        default=defaults.production_deadlock_frame,
    )
    parser.add_argument(
        "--production-stall-frames",
        type=int,
        default=defaults.production_stall_frames,
    )
    parser.add_argument(
        "--income-stall-frames",
        type=int,
        default=defaults.income_stall_frames,
    )
    parser.add_argument(
        "--modulation-consumption-grace-frames",
        type=int,
        default=defaults.modulation_consumption_grace_frames,
    )
    parser.add_argument(
        "--max-placement-failures",
        type=int,
        default=defaults.max_placement_failures,
    )
    parser.add_argument("--bot-exit-code", type=int)
    parser.add_argument("--bot-stopped", action="store_true")
    parser.add_argument("--termination-reason")
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Return success while the run is still below target if no terminal failures exist.",
    )
    parser.add_argument("--expected-profile-tags", default="")
    args = parser.parse_args(argv)

    config = MicroMachineSoakConfig(
        target_frame=args.target_frame,
        timeout_seconds=args.timeout_seconds,
        telemetry_stall_seconds=args.telemetry_stall_seconds,
        production_deadlock_frame=args.production_deadlock_frame,
        production_stall_frames=args.production_stall_frames,
        income_stall_frames=args.income_stall_frames,
        max_placement_failures=args.max_placement_failures,
        modulation_consumption_grace_frames=args.modulation_consumption_grace_frames,
        expected_profile_tags=tuple(
            item for item in args.expected_profile_tags.split() if item
        ),
    )
    observation = MicroMachineSoakObservation(
        blackboard_dir=Path(args.blackboard_dir),
        bot_log=Path(args.bot_log),
        artifact_dir=Path(args.artifact_dir) if args.artifact_dir else None,
        bot_exit_code=args.bot_exit_code,
        bot_running=not args.bot_stopped,
        termination_reason=args.termination_reason,
    )
    report = classify_micromachine_soak(observation, config)
    if args.report:
        report.write_json(args.report)
    print(json.dumps(report.to_dict(), sort_keys=True))
    if args.allow_incomplete and not report.failures:
        return 0
    return 0 if report.ok else 1


def _require_positive(name: str, value: int) -> None:
    if type(value) is bool or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer.")


def _string_tuple(name: str, value: Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, str):
        raise ValueError(f"{name} must be a sequence of strings.")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{name}[{index}] must be a non-empty string.")
        result.append(item.strip())
    return tuple(result)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except FileNotFoundError:
        return ""


def _read_json_mapping(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_jsonl_mappings(path: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for line in _read_text(path).splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            entries.append(payload)
    return entries


def _latest_frame(
    latest_telemetry: Mapping[str, object],
    telemetry_archive: Sequence[Mapping[str, object]],
) -> int:
    frames = [_int_value(latest_telemetry.get("frame"))]
    frames.extend(_int_value(entry.get("frame")) for entry in telemetry_archive)
    return max(frames, default=0)


def _classify_bootstrap_no_start_units(
    latest_telemetry: Mapping[str, object],
    telemetry_archive: Sequence[Mapping[str, object]],
) -> MicroMachineSoakFailure | None:
    for telemetry in (latest_telemetry, *reversed(tuple(telemetry_archive))):
        managers = telemetry.get("managers")
        if not isinstance(managers, Mapping):
            continue
        ccbot = managers.get("CCBot")
        if not isinstance(ccbot, Mapping):
            continue
        if ccbot.get("bootstrap_status") != "waiting_for_initial_observation":
            continue
        frame = _int_value(telemetry.get("frame"))
        player_id = _int_value(ccbot.get("player_id"))
        self_count = _int_value(ccbot.get("self_count"))
        resource_depot_count = _int_value(ccbot.get("resource_depot_count"))
        game_info_width = _int_value(ccbot.get("game_info_width"))
        game_info_height = _int_value(ccbot.get("game_info_height"))
        enemy_start_locations = _int_value(ccbot.get("enemy_start_location_count"))
        if (
            frame > 0
            and player_id > 0
            and self_count == 0
            and resource_depot_count == 0
            and game_info_width > 0
            and game_info_height > 0
        ):
            return MicroMachineSoakFailure(
                code="bootstrap_no_start_units",
                message=(
                    "SC2 API joined and map info loaded, but the participant has no "
                    "starting self units or resource depot."
                ),
                evidence={
                    "frame": frame,
                    "player_id": player_id,
                    "self_count": self_count,
                    "resource_depot_count": resource_depot_count,
                    "game_info_width": game_info_width,
                    "game_info_height": game_info_height,
                    "enemy_start_location_count": enemy_start_locations,
                },
            )
    return None


def _int_value(value: object) -> int:
    if type(value) is bool:
        return 0
    if isinstance(value, int):
        return max(value, 0)
    return 0


def _telemetry_recent(
    observation: MicroMachineSoakObservation,
    config: MicroMachineSoakConfig,
) -> bool:
    telemetry_path = observation.latest_telemetry_path
    if not telemetry_path.exists() and observation.telemetry_archive_path.exists():
        telemetry_path = observation.telemetry_archive_path
    if not telemetry_path.exists():
        return False
    latest = _read_json_mapping(observation.latest_telemetry_path)
    archive = _read_jsonl_mappings(observation.telemetry_archive_path)
    if _latest_frame(latest, archive) >= config.target_frame:
        return True
    age = observation.now_seconds - telemetry_path.stat().st_mtime
    return age <= config.telemetry_stall_seconds


def _matching_terms(log_text: str, terms: Sequence[str]) -> list[str]:
    return [term for term in terms if term in log_text]


def _has_positive_gas_income(log_text: str) -> bool:
    for line in log_text.splitlines():
        label = "Gas income:"
        if label not in line:
            continue
        if _has_positive_number_after_marker(line, label):
            return True
    return False


def _has_positive_mineral_income(log_text: str) -> bool:
    for line in log_text.splitlines():
        label = "Mineral income:"
        if label not in line:
            continue
        if _has_positive_number_after_marker(line, label):
            return True
    return False


def _missing_recent_positive_income(
    log_text: str,
    latest_frame: int,
    income_stall_frames: int,
) -> list[str]:
    min_frame = max(0, latest_frame - income_stall_frames)
    missing: list[str] = []
    if not _has_recent_worker_combat(log_text, min_frame) and not _has_recent_positive_income(
        log_text, "Mineral income:", min_frame
    ):
        missing.append("recent positive mineral income")
    if _has_recent_positive_gas_demand(log_text, min_frame) and not _has_recent_positive_income(
        log_text, "Gas income:", min_frame
    ):
        missing.append("recent positive gas income")
    return missing


def _has_recent_positive_income(log_text: str, label: str, min_frame: int) -> bool:
    current_frame = 0
    for line in log_text.splitlines():
        parsed_frame = _log_frame(line)
        if parsed_frame is not None:
            current_frame = parsed_frame
        if label not in line or current_frame < min_frame:
            continue
        if _has_positive_number_after_marker(line, label):
            return True
    return False


def _has_recent_positive_gas_demand(log_text: str, min_frame: int) -> bool:
    current_frame = 0
    for line in log_text.splitlines():
        parsed_frame = _log_frame(line)
        if parsed_frame is not None:
            current_frame = parsed_frame
        label = "Gas Worker Target:"
        if label not in line or current_frame < min_frame:
            continue
        if _has_positive_number_after_marker(line, label):
            return True
    return False


def _has_positive_number_after_marker(line: str, marker: str) -> bool:
    _, _, value_text = line.partition(marker)
    return any(int(value) > 0 for value in re.findall(r"\d+", value_text))


def _has_recent_worker_combat(log_text: str, min_frame: int) -> bool:
    current_frame = 0
    for line in log_text.splitlines():
        parsed_frame = _log_frame(line)
        if parsed_frame is not None:
            current_frame = parsed_frame
        if "Worker jobs M/G/B/C/I/S/N:" not in line or current_frame < min_frame:
            continue
        _, _, counts_text = line.partition("M/G/B/C/I/S/N:")
        counts = counts_text.split()[0].split("/")
        if len(counts) >= 4:
            try:
                if int(counts[3]) > 0:
                    return True
            except ValueError:
                continue
    return False


def _has_recent_combat_activity(log_text: str, min_frame: int) -> bool:
    if _has_recent_worker_combat(log_text, min_frame):
        return True
    for line in log_text.splitlines():
        parsed_frame = _log_frame(line)
        if parsed_frame is None or parsed_frame < min_frame:
            continue
        if "updateAttackSquads |" in line:
            return True
    return False


def _last_log_frame_for_terms(log_text: str, terms: Sequence[str]) -> int | None:
    latest: int | None = None
    for line in log_text.splitlines():
        if not any(term in line for term in terms):
            continue
        frame = _log_frame(line)
        if frame is None:
            continue
        latest = frame if latest is None else max(latest, frame)
    return latest


def _log_frame(line: str) -> int | None:
    prefix, _, _ = line.partition(":")
    try:
        return int(prefix.strip())
    except ValueError:
        return None


def _manager_intervention_ok(
    latest_telemetry: Mapping[str, object],
    telemetry_archive: Sequence[Mapping[str, object]],
) -> bool:
    entries = [*telemetry_archive, latest_telemetry]
    combat_seen = False
    scout_seen = False
    active_policy_seen = False
    for entry in entries:
        managers = entry.get("managers")
        if not isinstance(managers, Mapping):
            continue
        commander = managers.get("GameCommander")
        if isinstance(commander, Mapping) and commander.get("policy_active") is True:
            active_policy_seen = True
        combat = managers.get("CombatCommander")
        if isinstance(combat, Mapping) and combat.get("bounded_intervention") is True:
            combat_seen = True
        scout = managers.get("ScoutManager")
        if isinstance(scout, Mapping) and scout.get("bounded_intervention") is True:
            scout_seen = True
    return combat_seen and scout_seen and active_policy_seen


def _classify_stale_modulation(
    observation: MicroMachineSoakObservation,
    latest_telemetry: Mapping[str, object],
    telemetry_archive: Sequence[Mapping[str, object]],
    latest_frame: int,
    config: MicroMachineSoakConfig,
) -> MicroMachineSoakFailure | None:
    telemetry_for_policy = latest_telemetry or (
        telemetry_archive[-1] if telemetry_archive else {}
    )
    managers = telemetry_for_policy.get("managers")
    commander = managers.get("GameCommander") if isinstance(managers, Mapping) else None
    if isinstance(commander, Mapping) and commander.get("policy_active") is False:
        return MicroMachineSoakFailure(
            code="stale_modulation",
            message="GameCommander reports inactive policy modulation.",
            evidence={"latest_frame": latest_frame},
        )
    active_ids = telemetry_for_policy.get("active_modulation_ids")
    if latest_frame > 0 and isinstance(active_ids, list) and not active_ids:
        return MicroMachineSoakFailure(
            code="stale_modulation",
            message="Telemetry has no active modulation ids.",
            evidence={"latest_frame": latest_frame},
        )
    latest_update = _latest_modulation_update(observation)
    latest_update_id = latest_update.get("update_id")
    issued_at_frame = _int_value(latest_update.get("issued_at_frame"))
    expires_at_frame = _int_value(latest_update.get("expires_at_frame"))
    if latest_frame >= config.target_frame and not latest_update:
        return MicroMachineSoakFailure(
            code="stale_modulation",
            message="Latest modulation artifact is missing or unreadable.",
            evidence={
                "latest_frame": latest_frame,
                "latest_modulation_path": str(observation.latest_modulation_path),
                "modulation_archive_path": str(observation.modulation_archive_path),
            },
        )
    if (
        isinstance(latest_update_id, str)
        and latest_update_id
        and latest_frame >= issued_at_frame + config.modulation_consumption_grace_frames
    ):
        active_id_values = active_ids if isinstance(active_ids, list) else []
        commander_update_id = (
            commander.get("update_id") if isinstance(commander, Mapping) else None
        )
        if latest_update_id not in active_id_values and commander_update_id != latest_update_id:
            return MicroMachineSoakFailure(
                code="stale_modulation",
                message="Latest modulation update has not been consumed by telemetry.",
                evidence={
                    "latest_frame": latest_frame,
                    "latest_update_id": latest_update_id,
                    "commander_update_id": commander_update_id,
                    "active_modulation_ids": active_id_values,
                },
            )
    if expires_at_frame and latest_frame > expires_at_frame:
        return MicroMachineSoakFailure(
            code="stale_modulation",
            message="Latest modulation update expired before the soak target.",
            evidence={"latest_frame": latest_frame, "expires_at_frame": expires_at_frame},
        )
    return None


def _classify_expected_profile_tags(
    observation: MicroMachineSoakObservation,
    latest_frame: int,
    config: MicroMachineSoakConfig,
) -> MicroMachineSoakFailure | None:
    expected = set(config.expected_profile_tags)
    if latest_frame < config.target_frame or not expected:
        return None
    observed = _observed_modulation_tags(observation)
    missing = sorted(expected - observed)
    if not missing:
        return None
    return MicroMachineSoakFailure(
        code="strategy_profile_missing",
        message="Expected long-horizon strategy profile tags were not published.",
        evidence={"expected": sorted(expected), "observed": sorted(observed), "missing": missing},
    )


def _observed_modulation_tags(observation: MicroMachineSoakObservation) -> set[str]:
    tags: set[str] = set()
    updates = _read_jsonl_mappings(observation.modulation_archive_path)
    latest = _read_json_mapping(observation.latest_modulation_path)
    if latest:
        updates.append(latest)
    for update in updates:
        vector = update.get("vector")
        if not isinstance(vector, Mapping):
            continue
        vector_tags = vector.get("tags")
        if isinstance(vector_tags, list):
            tags.update(tag for tag in vector_tags if isinstance(tag, str) and tag)
    return tags


def _latest_modulation_update(
    observation: MicroMachineSoakObservation,
) -> dict[str, object]:
    latest_update = _read_json_mapping(observation.latest_modulation_path)
    if isinstance(latest_update.get("update_id"), str):
        return latest_update
    archive = _read_jsonl_mappings(observation.modulation_archive_path)
    for entry in reversed(archive):
        if isinstance(entry.get("update_id"), str):
            return dict(entry)
    return {}


if __name__ == "__main__":
    raise SystemExit(main())
