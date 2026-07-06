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

from starcraft_commander.micromachine_tactical_evidence import (
    MicroMachineTacticalEvidence,
    classify_micromachine_tactical_evidence,
)


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
SUPPLY_BLOCK_TERM: Final[str] = "Supply blocked | 0x00000007"
SUPPLY_RECOVERY_TERMS: Final[tuple[str, ...]] = (
    "Supply provider recovery queued after supply block.",
    "Supply provider recovery waiting for existing construction",
)
SUPPLY_PROVIDER_COMMAND_TERMS: Final[tuple[str, ...]] = (
    "build command type=TERRAN_SUPPLYDEPOT",
    "voi supply provider command kind=",
)
LOG_FRAME_PREFIX_RE: Final[re.Pattern[str]] = re.compile(r"^\s*(\d+):\s*(.*)$")
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
    "accepted unit training order=",
)
UNIT_PRODUCTION_TERMS: Final[tuple[str, ...]] = (
    "create unit item=",
    "accepted unit training order=",
)
PRODUCTION_DOCTRINE_EVIDENCE_VALUES: Final[set[str]] = {
    "queued",
    "queued_existing",
    "command_issued",
}
EXPECTED_ACTUAL_PRODUCTION_ITEMS_BY_DOCTRINE: Final[Mapping[str, frozenset[str]]] = {
    "marine_rush": frozenset({"Marine", "Barracks"}),
    "bio_pressure": frozenset({"Marauder", "BarracksTechLab", "Starport", "Medivac"}),
    "tank_defensive_hold": frozenset({"FactoryTechLab", "SiegeTank"}),
    "siege_contain": frozenset({"FactoryTechLab", "SiegeTank"}),
    "contain_enemy_natural": frozenset({"FactoryTechLab", "SiegeTank"}),
    "mech_transition": frozenset({"Hellion", "Cyclone", "SiegeTank", "Thor"}),
    "drop_harassment": frozenset({"Starport", "StarportReactor", "Medivac", "Hellion", "Reaper"}),
    "worker_line_harassment": frozenset({"Starport", "StarportReactor", "Medivac", "Hellion", "Reaper"}),
    "expand_macro": frozenset({"CommandCenter"}),
    "anti_air_response": frozenset({"Starport", "EngineeringBay", "Viking"}),
}
"""Profile-specific actual build/train command items required for sign-off."""

NON_PRODUCTION_STRATEGY_DOCTRINES: Final[frozenset[str]] = frozenset(
    {"scouting_map_control"}
)
"""Strategy profiles that intentionally modulate non-production managers only."""

ACTUAL_PRODUCTION_ITEM_ALIASES: Final[Mapping[str, str]] = {
    "TERRAN_SUPPLYDEPOT": "SupplyDepot",
    "TERRAN_BARRACKS": "Barracks",
    "TERRAN_BARRACKSTECHLAB": "BarracksTechLab",
    "TERRAN_FACTORY": "Factory",
    "TERRAN_FACTORYTECHLAB": "FactoryTechLab",
    "TERRAN_STARPORT": "Starport",
    "TERRAN_STARPORTREACTOR": "StarportReactor",
    "TERRAN_COMMANDCENTER": "CommandCenter",
    "TERRAN_ENGINEERINGBAY": "EngineeringBay",
    "TERRAN_MARINE": "Marine",
    "TERRAN_MARAUDER": "Marauder",
    "TERRAN_REAPER": "Reaper",
    "TERRAN_HELLION": "Hellion",
    "TERRAN_CYCLONE": "Cyclone",
    "TERRAN_THOR": "Thor",
    "TERRAN_SIEGETANK": "SiegeTank",
    "TERRAN_MEDIVAC": "Medivac",
    "TERRAN_VIKINGFIGHTER": "Viking",
}
"""Raw s2client-api/MicroMachine item names normalized to DSL evidence names."""


@dataclass(frozen=True)
class MicroMachineSoakConfig:
    """Configurable production sign-off thresholds for a local soak run."""

    target_frame: int = 12_000
    timeout_seconds: int = 1_200
    telemetry_stall_seconds: int = 90
    production_deadlock_frame: int = 9_000
    production_stall_frames: int = 6_000
    supply_recovery_grace_frames: int = 672
    income_stall_frames: int = 2_000
    bootstrap_no_start_units_frame: int = 1_200
    max_placement_failures: int = 3
    max_worker_self_position_blocks: int = 0
    max_worker_repeat_order_suppressions: int = 0
    modulation_consumption_grace_frames: int = 128
    require_macro_evidence: bool = True
    require_manager_intervention: bool = True
    expected_profile_tags: tuple[str, ...] = ()
    expected_tactical_effects: tuple[str, ...] = ()
    expected_strategy_doctrine: str = ""
    expected_production_actions: tuple[str, ...] = ()
    expected_production_items: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_positive("target_frame", self.target_frame)
        _require_positive("timeout_seconds", self.timeout_seconds)
        _require_positive("telemetry_stall_seconds", self.telemetry_stall_seconds)
        _require_positive("production_deadlock_frame", self.production_deadlock_frame)
        _require_positive("production_stall_frames", self.production_stall_frames)
        _require_positive("supply_recovery_grace_frames", self.supply_recovery_grace_frames)
        _require_positive("income_stall_frames", self.income_stall_frames)
        _require_positive("bootstrap_no_start_units_frame", self.bootstrap_no_start_units_frame)
        _require_positive(
            "modulation_consumption_grace_frames",
            self.modulation_consumption_grace_frames,
        )
        _require_positive("max_placement_failures", self.max_placement_failures)
        if (
            type(self.max_worker_self_position_blocks) is bool
            or not isinstance(self.max_worker_self_position_blocks, int)
            or self.max_worker_self_position_blocks < 0
        ):
            raise ValueError("max_worker_self_position_blocks must be a non-negative integer.")
        if (
            type(self.max_worker_repeat_order_suppressions) is bool
            or not isinstance(self.max_worker_repeat_order_suppressions, int)
            or self.max_worker_repeat_order_suppressions < 0
        ):
            raise ValueError(
                "max_worker_repeat_order_suppressions must be a non-negative integer."
            )
        object.__setattr__(
            self,
            "expected_profile_tags",
            _string_tuple("expected_profile_tags", self.expected_profile_tags),
        )
        object.__setattr__(
            self,
            "expected_tactical_effects",
            _string_tuple("expected_tactical_effects", self.expected_tactical_effects),
        )
        object.__setattr__(
            self,
            "expected_strategy_doctrine",
            _optional_string("expected_strategy_doctrine", self.expected_strategy_doctrine),
        )
        object.__setattr__(
            self,
            "expected_production_actions",
            _string_tuple("expected_production_actions", self.expected_production_actions),
        )
        object.__setattr__(
            self,
            "expected_production_items",
            _string_tuple("expected_production_items", self.expected_production_items),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "target_frame": self.target_frame,
            "timeout_seconds": self.timeout_seconds,
            "telemetry_stall_seconds": self.telemetry_stall_seconds,
            "production_deadlock_frame": self.production_deadlock_frame,
            "production_stall_frames": self.production_stall_frames,
            "supply_recovery_grace_frames": self.supply_recovery_grace_frames,
            "income_stall_frames": self.income_stall_frames,
            "bootstrap_no_start_units_frame": self.bootstrap_no_start_units_frame,
            "max_placement_failures": self.max_placement_failures,
            "max_worker_self_position_blocks": self.max_worker_self_position_blocks,
            "max_worker_repeat_order_suppressions": self.max_worker_repeat_order_suppressions,
            "modulation_consumption_grace_frames": self.modulation_consumption_grace_frames,
            "require_macro_evidence": self.require_macro_evidence,
            "require_manager_intervention": self.require_manager_intervention,
            "expected_profile_tags": list(self.expected_profile_tags),
            "expected_tactical_effects": list(self.expected_tactical_effects),
            "expected_strategy_doctrine": self.expected_strategy_doctrine,
            "expected_production_actions": list(self.expected_production_actions),
            "expected_production_items": list(self.expected_production_items),
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
    tactical_evidence: MicroMachineTacticalEvidence | None = None
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
            "tactical_evidence": (
                self.tactical_evidence.to_dict() if self.tactical_evidence else None
            ),
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
    tactical_evidence = classify_micromachine_tactical_evidence(
        latest_telemetry=telemetry,
        telemetry_archive=telemetry_archive,
        log_text=log_text,
        expected_effects=resolved_config.expected_tactical_effects,
        source_paths={
            "latest_telemetry": observation.latest_telemetry_path,
            "telemetry_archive": observation.telemetry_archive_path,
            "bot_log": observation.bot_log,
        },
    )
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

    no_start_units_failure = _classify_bootstrap_no_start_units(
        telemetry,
        telemetry_archive,
        resolved_config.bootstrap_no_start_units_frame,
    )
    if no_start_units_failure is not None:
        failures.append(no_start_units_failure)

    worker_root_cause_contract_failure = _classify_worker_root_cause_telemetry_contract(
        telemetry,
        telemetry_archive,
    )
    if worker_root_cause_contract_failure is not None:
        failures.append(worker_root_cause_contract_failure)

    worker_repeat_order_failure = _classify_worker_repeat_order_suppressions(
        telemetry,
        telemetry_archive,
        resolved_config.max_worker_repeat_order_suppressions,
    )
    if worker_repeat_order_failure is not None:
        failures.append(worker_repeat_order_failure)

    worker_self_position_failure = _classify_worker_self_position_blocks(
        telemetry,
        telemetry_archive,
        resolved_config.max_worker_self_position_blocks,
    )
    if worker_self_position_failure is not None:
        failures.append(worker_self_position_failure)

    scout_duplicate_worker_move_failure = _classify_scout_duplicate_worker_move(
        telemetry,
        telemetry_archive,
    )
    if scout_duplicate_worker_move_failure is not None:
        failures.append(scout_duplicate_worker_move_failure)

    placement_failure_count = count_placement_failures(log_text)
    if placement_failure_count >= resolved_config.max_placement_failures:
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

    supply_block_failure = _classify_unrecovered_supply_block(
        log_text,
        telemetry,
        telemetry_archive,
        latest_frame,
        resolved_config.target_frame,
        resolved_config.supply_recovery_grace_frames,
    )
    if supply_block_failure is not None:
        failures.append(supply_block_failure)

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

    last_unit_production_frame = _last_log_frame_for_terms(log_text, UNIT_PRODUCTION_TERMS)
    if (
        latest_frame >= resolved_config.target_frame
        and (
            last_unit_production_frame is None
            or latest_frame - last_unit_production_frame > resolved_config.production_stall_frames
        )
    ):
        failures.append(
            MicroMachineSoakFailure(
                code="unit_production_stall",
                message="No recent unit-production evidence appeared near the target frame.",
                evidence={
                    "latest_frame": latest_frame,
                    "last_unit_production_frame": last_unit_production_frame,
                    "production_stall_frames": resolved_config.production_stall_frames,
                },
            )
        )

    recent_income_missing = _missing_recent_positive_income(
        log_text,
        telemetry,
        telemetry_archive,
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

    latest_update = _latest_modulation_update(observation)
    manager_intervention_ok = _manager_intervention_ok(
        telemetry,
        telemetry_archive,
        latest_update=latest_update,
        latest_frame=latest_frame,
        config=resolved_config,
    )
    if resolved_config.require_manager_intervention and latest_frame >= resolved_config.target_frame:
        if not manager_intervention_ok:
            failures.append(
                MicroMachineSoakFailure(
                    code="manager_intervention_missing",
                    message=(
                        "CombatCommander, ScoutManager, and ProductionManager bounded "
                        "intervention evidence is missing."
                    ),
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
    if (
        resolved_config.expected_tactical_effects
        and latest_frame >= resolved_config.target_frame
        and not tactical_evidence.ok
    ):
        failures.append(
            MicroMachineSoakFailure(
                code="tactical_effect_missing",
                message="Expected tactical-effect evidence was not observed by the target frame.",
                evidence=tactical_evidence.to_dict(),
            )
        )
    strategy_consumption_failure = _classify_expected_strategy_consumption(
        telemetry,
        telemetry_archive,
        latest_frame,
        resolved_config,
    )
    if strategy_consumption_failure is not None:
        failures.append(strategy_consumption_failure)
    tactical_actual_command_failure = _classify_expected_tactical_actual_commands(
        observation,
        telemetry,
        telemetry_archive,
        latest_frame,
        resolved_config,
    )
    if tactical_actual_command_failure is not None:
        failures.append(tactical_actual_command_failure)

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
        tactical_evidence=tactical_evidence,
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


def count_placement_failures(log_text: str) -> int:
    """Count framed placement failures once while preserving unframed repeats."""

    count = 0
    framed_seen: set[tuple[str, str, str]] = set()
    for line in log_text.splitlines():
        for term in PLACEMENT_FAILURE_TERMS:
            if term not in line:
                continue
            match = LOG_FRAME_PREFIX_RE.match(line)
            if match is None:
                count += line.count(term)
                continue
            key = (match.group(1), term, match.group(2).strip())
            if key not in framed_seen:
                framed_seen.add(key)
                count += 1
    return count


def missing_macro_evidence(log_text: str) -> list[str]:
    missing = [term for term in DEFAULT_REQUIRED_MACRO_TERMS if term not in log_text]
    if not any(term in log_text for term in DEFAULT_POST_BARRACKS_UNIT_TERMS):
        missing.append("post-Barracks unit creation")
    if not _has_positive_gas_income(log_text):
        missing.append("positive gas income")
    if not _has_positive_mineral_income(log_text):
        missing.append("positive mineral income")
    return missing


def _classify_unrecovered_supply_block(
    log_text: str,
    latest_telemetry: Mapping[str, object],
    telemetry_archive: Sequence[Mapping[str, object]],
    latest_frame: int,
    target_frame: int,
    grace_frames: int,
) -> MicroMachineSoakFailure | None:
    supply_block_frames = _log_frames_for_term(log_text, SUPPLY_BLOCK_TERM)
    latest_log_supply_recovery_frame = _last_log_frame_for_terms(
        log_text,
        SUPPLY_RECOVERY_TERMS,
    )
    latest_log_supply_command_frame = _last_log_frame_for_terms(
        log_text,
        SUPPLY_PROVIDER_COMMAND_TERMS,
    )
    supply_evidence = _production_supply_recovery_evidence(
        [*telemetry_archive, latest_telemetry]
    )
    telemetry_supply_block_frame = _int_value(supply_evidence.get("last_supply_block_frame"))
    telemetry_supply_command_frame = _int_value(
        supply_evidence.get("last_supply_provider_command_frame")
    )
    telemetry_supply_recovery_frame = _int_value(
        supply_evidence.get("last_supply_recovery_frame")
    )
    latest_supply_block_frame = max(
        supply_block_frames[-1] if supply_block_frames else 0,
        telemetry_supply_block_frame,
    )
    if latest_supply_block_frame <= 0:
        return None

    latest_supply_recovery_frame = max(
        latest_log_supply_recovery_frame or 0,
        telemetry_supply_recovery_frame,
    )
    latest_supply_command_frame = max(
        latest_log_supply_command_frame or 0,
        telemetry_supply_command_frame,
    )
    under_construction_count = _int_value(
        supply_evidence.get("supply_provider_under_construction_count")
    )
    supply_blocked_frames = max(
        len(supply_block_frames),
        _int_value(supply_evidence.get("supply_blocked_frames")),
    )
    base_evidence: dict[str, object] = {
        **supply_evidence,
        "latest_frame": latest_frame,
        "supply_block_log_count": len(supply_block_frames),
        "supply_blocked_frames": supply_blocked_frames,
        "latest_supply_block_frame": latest_supply_block_frame,
        "latest_supply_recovery_frame": latest_supply_recovery_frame,
        "latest_supply_provider_command_frame": latest_supply_command_frame,
        "target_frame": target_frame,
        "supply_recovery_grace_frames": grace_frames,
    }
    if latest_frame >= target_frame:
        if latest_supply_command_frame >= latest_supply_block_frame:
            return None
        if under_construction_count > 0 and latest_supply_recovery_frame >= latest_supply_block_frame:
            return None
        if latest_supply_recovery_frame >= latest_supply_block_frame:
            return MicroMachineSoakFailure(
                code="supply_recovery_pending_at_target",
                message=(
                    "ProductionManager reached the target frame while the latest supply block "
                    "only had queued recovery evidence, not a subsequent SupplyDepot command "
                    "or under-construction confirmation."
                ),
                evidence=base_evidence,
            )
        return MicroMachineSoakFailure(
            code="supply_block_unrecovered",
            message=(
                "ProductionManager reached the target frame with unresolved supply-block "
                "evidence and no recovery queue, SupplyDepot build command, or "
                "under-construction evidence."
            ),
            evidence=base_evidence,
        )
    if latest_frame < latest_supply_block_frame + grace_frames:
        return None
    if latest_supply_command_frame >= latest_supply_block_frame:
        return None
    if under_construction_count > 0 and latest_supply_recovery_frame >= latest_supply_block_frame:
        return None
    if latest_supply_recovery_frame >= latest_supply_block_frame:
        return MicroMachineSoakFailure(
            code="supply_recovery_command_missing",
            message=(
                "ProductionManager detected supply block and queued/reported recovery, "
                "but no subsequent SupplyDepot build command or under-construction "
                "evidence appeared within the grace window."
            ),
            evidence=base_evidence,
        )
    return MicroMachineSoakFailure(
        code="supply_block_unrecovered",
        message=(
            "ProductionManager emitted supply-block evidence without recovery queue, "
            "SupplyDepot build command, or under-construction evidence."
        ),
        evidence=base_evidence,
    )


def _production_supply_recovery_evidence(
    telemetry_entries: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    evidence: dict[str, object] = {
        "telemetry_contract_seen": False,
        "last_supply_recovery_status": "none",
        "last_supply_recovery_reason": "none",
        "supply_recovery_queued_count": 0,
        "last_supply_recovery_frame": 0,
        "last_supply_block_frame": 0,
        "supply_provider_under_construction_count": 0,
        "last_supply_provider_command_frame": 0,
        "last_supply_provider_command_kind": "none",
        "last_supply_provider_command_update_id": "",
        "supply_blocked_frames": 0,
    }
    for entry in telemetry_entries:
        managers = entry.get("managers")
        if not isinstance(managers, Mapping):
            continue
        production = managers.get("ProductionManager")
        if not isinstance(production, Mapping):
            continue
        if "last_supply_block_frame" in production or "supply_blocked_frames" in production:
            evidence["telemetry_contract_seen"] = True
        evidence["supply_blocked_frames"] = max(
            _int_value(evidence.get("supply_blocked_frames")),
            _int_value(production.get("supply_blocked_frames")),
        )
        for key in (
            "last_supply_block_frame",
            "last_supply_recovery_frame",
            "last_supply_provider_command_frame",
            "supply_recovery_queued_count",
            "supply_provider_under_construction_count",
        ):
            value = _int_value(production.get(key))
            if value >= _int_value(evidence.get(key)):
                evidence[key] = value
                if key == "last_supply_recovery_frame":
                    evidence["last_supply_recovery_status"] = str(
                        production.get("last_supply_recovery_status", "none") or "none"
                    )
                    evidence["last_supply_recovery_reason"] = str(
                        production.get("last_supply_recovery_reason", "none") or "none"
                    )
                elif key == "last_supply_provider_command_frame":
                    evidence["last_supply_provider_command_kind"] = str(
                        production.get("last_supply_provider_command_kind", "none") or "none"
                    )
                    evidence["last_supply_provider_command_update_id"] = str(
                        production.get("last_supply_provider_command_update_id", "") or ""
                    )
        item = _canonical_actual_production_item(
            production.get("last_actual_production_command_item", "")
        )
        if item == "SupplyDepot":
            frame = _int_value(production.get("last_actual_production_command_frame"))
            if frame >= _int_value(evidence.get("last_supply_provider_command_frame")):
                evidence["last_supply_provider_command_frame"] = frame
                evidence["last_supply_provider_command_kind"] = str(
                    production.get("last_actual_production_command_kind", "none") or "none"
                )
                evidence["last_supply_provider_command_update_id"] = str(
                    production.get("last_actual_production_command_update_id", "") or ""
                )
    return evidence


def _log_frames_for_term(log_text: str, term: str) -> list[int]:
    frames: list[int] = []
    seen: set[int] = set()
    current_frame = 0
    for line in log_text.splitlines():
        parsed_frame = _log_frame(line)
        if parsed_frame is not None:
            current_frame = parsed_frame
        if term not in line or current_frame <= 0 or current_frame in seen:
            continue
        seen.add(current_frame)
        frames.append(current_frame)
    return frames


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
        "--supply-recovery-grace-frames",
        type=int,
        default=defaults.supply_recovery_grace_frames,
    )
    parser.add_argument(
        "--income-stall-frames",
        type=int,
        default=defaults.income_stall_frames,
    )
    parser.add_argument(
        "--bootstrap-no-start-units-frame",
        type=int,
        default=defaults.bootstrap_no_start_units_frame,
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
    parser.add_argument(
        "--max-worker-self-position-blocks",
        type=int,
        default=defaults.max_worker_self_position_blocks,
    )
    parser.add_argument(
        "--max-worker-repeat-order-suppressions",
        type=int,
        default=defaults.max_worker_repeat_order_suppressions,
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
    parser.add_argument("--expected-tactical-effects", default="")
    parser.add_argument("--expected-strategy-doctrine", default="")
    parser.add_argument("--expected-production-actions", default="")
    parser.add_argument("--expected-production-items", default="")
    args = parser.parse_args(argv)

    config = MicroMachineSoakConfig(
        target_frame=args.target_frame,
        timeout_seconds=args.timeout_seconds,
        telemetry_stall_seconds=args.telemetry_stall_seconds,
        production_deadlock_frame=args.production_deadlock_frame,
        production_stall_frames=args.production_stall_frames,
        supply_recovery_grace_frames=args.supply_recovery_grace_frames,
        income_stall_frames=args.income_stall_frames,
        bootstrap_no_start_units_frame=args.bootstrap_no_start_units_frame,
        max_placement_failures=args.max_placement_failures,
        max_worker_self_position_blocks=args.max_worker_self_position_blocks,
        max_worker_repeat_order_suppressions=args.max_worker_repeat_order_suppressions,
        modulation_consumption_grace_frames=args.modulation_consumption_grace_frames,
        expected_profile_tags=tuple(
            item for item in args.expected_profile_tags.split() if item
        ),
        expected_tactical_effects=tuple(
            item for item in args.expected_tactical_effects.split() if item
        ),
        expected_strategy_doctrine=args.expected_strategy_doctrine,
        expected_production_actions=tuple(
            item for item in args.expected_production_actions.split() if item
        ),
        expected_production_items=tuple(
            item for item in args.expected_production_items.split() if item
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


def _optional_string(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string.")
    return value.strip()


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
    threshold_frame: int,
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
            frame >= threshold_frame
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


def _classify_worker_root_cause_telemetry_contract(
    latest_telemetry: Mapping[str, object],
    telemetry_archive: Sequence[Mapping[str, object]],
) -> MicroMachineSoakFailure | None:
    required_fields = (
        "repeat_order_suppressed_count",
        "self_position_command_block_count",
        "root_cause_status",
        "root_cause_reason",
        "trace_contract_version",
        "trace_event_count",
        "last_trace_frame",
        "last_trace_status",
        "last_trace_reason",
        "last_trace_target_kind",
        "last_trace_target_tag",
        "last_trace_distance_sq",
    )
    telemetry_entries = [entry for entry in (*telemetry_archive, latest_telemetry) if entry]
    for telemetry in telemetry_entries:
        managers = telemetry.get("managers")
        if not isinstance(managers, Mapping):
            return MicroMachineSoakFailure(
                code="worker_root_cause_telemetry_missing",
                message=(
                    "Telemetry has no managers block; soak cannot prove worker "
                    "self-position and duplicate worker move bugs are absent."
                ),
                evidence={
                    "frame": _int_value(telemetry.get("frame")),
                    "missing_fields": ["managers.WorkerManager"],
                },
            )
        workers = managers.get("WorkerManager")
        if not isinstance(workers, Mapping):
            return MicroMachineSoakFailure(
                code="worker_root_cause_telemetry_missing",
                message=(
                    "Telemetry has no WorkerManager root-cause contract; soak cannot "
                    "prove self-position and duplicate worker move bugs are absent."
                ),
                evidence={
                    "frame": _int_value(telemetry.get("frame")),
                    "missing_fields": ["WorkerManager"],
                    "managers": sorted(str(key) for key in managers),
                },
            )
        missing = [field for field in required_fields if field not in workers]
        if missing:
            return MicroMachineSoakFailure(
                code="worker_root_cause_telemetry_missing",
                message=(
                    "WorkerManager root-cause telemetry is incomplete; soak cannot "
                    "prove self-position and duplicate worker move bugs are absent."
                ),
                evidence={
                    "frame": _int_value(telemetry.get("frame")),
                    "missing_fields": missing,
                    "workers": dict(workers),
                },
            )
        frame = _int_value(telemetry.get("frame"))
        trace_contract_version = _int_value(workers.get("trace_contract_version"))
        trace_event_count = _int_value(workers.get("trace_event_count"))
        last_trace_frame = _int_value(workers.get("last_trace_frame"))
        last_trace_status = str(workers.get("last_trace_status", "") or "")
        last_trace_reason = str(workers.get("last_trace_reason", "") or "")
        last_trace_target_kind = str(workers.get("last_trace_target_kind", "") or "")
        if trace_contract_version != 1:
            return MicroMachineSoakFailure(
                code="worker_trace_contract_invalid",
                message="WorkerManager trace contract version is invalid.",
                evidence={"frame": frame, "workers": dict(workers)},
            )
        if frame >= 512 and (
            trace_event_count <= 0
            or last_trace_frame <= 0
            or last_trace_frame > frame
            or last_trace_status in ("", "none", "unknown")
            or last_trace_reason in ("", "none", "unknown")
            or last_trace_target_kind in ("", "none", "unknown")
        ):
            return MicroMachineSoakFailure(
                code="worker_trace_contract_invalid",
                message=(
                    "WorkerManager trace fields exist but do not prove live worker "
                    "command tracing."
                ),
                evidence={"frame": frame, "workers": dict(workers)},
            )
    return None


def _classify_worker_self_position_blocks(
    latest_telemetry: Mapping[str, object],
    telemetry_archive: Sequence[Mapping[str, object]],
    max_allowed: int,
) -> MicroMachineSoakFailure | None:
    worst_count = 0
    worst_evidence: dict[str, object] = {}
    for telemetry in (*telemetry_archive, latest_telemetry):
        managers = telemetry.get("managers")
        if not isinstance(managers, Mapping):
            continue
        workers = managers.get("WorkerManager")
        if not isinstance(workers, Mapping):
            continue
        count = _int_value(workers.get("self_position_command_block_count"))
        root_cause_status = workers.get("root_cause_status")
        trace_status = str(workers.get("last_trace_status", "") or "")
        trace_target_kind = str(workers.get("last_trace_target_kind", "") or "")
        trace_target_tag = _int_value(workers.get("last_trace_target_tag"))
        trace_distance_sq = _float_value(workers.get("last_trace_distance_sq"))
        accepted_near_self_position = (
            trace_status == "accepted_candidate"
            and trace_target_tag == 0
            and trace_distance_sq <= 1.0
            and trace_target_kind
            in {
                "micro_smart_move_position",
                "queued_position",
                "unit_move_position",
                "unit_move_tile_position",
                "unit_smart_position",
            }
        )
        if (
            count <= worst_count
            and root_cause_status != "self_position_move_blocked"
            and not accepted_near_self_position
        ):
            continue
        worst_count = max(worst_count, count)
        worst_evidence = {
            "frame": _int_value(telemetry.get("frame")),
            "self_position_command_block_count": count,
            "max_worker_self_position_blocks": max_allowed,
            "root_cause_status": root_cause_status,
            "root_cause_reason": workers.get("root_cause_reason"),
            "worker_tag": workers.get("last_self_position_worker_tag"),
            "ability": workers.get("last_self_position_ability"),
            "target_kind": workers.get("last_self_position_target_kind"),
            "target_x": workers.get("last_self_position_target_x"),
            "target_y": workers.get("last_self_position_target_y"),
            "distance_sq": workers.get("last_self_position_distance_sq"),
            "current_order_ability": workers.get("last_worker_current_order_ability"),
            "current_order_target_tag": workers.get("last_worker_current_order_target_tag"),
            "last_trace_status": trace_status,
            "last_trace_target_kind": trace_target_kind,
            "last_trace_target_tag": trace_target_tag,
            "last_trace_distance_sq": trace_distance_sq,
        }

    if (
        worst_count > max_allowed
        or worst_evidence.get("root_cause_status") == "self_position_move_blocked"
        or worst_evidence.get("last_trace_status") == "accepted_candidate"
    ):
        return MicroMachineSoakFailure(
            code="worker_self_position_command",
            message=(
                "WorkerManager accepted a move/smart position command at the worker's "
                "own position; this is a root-cause bug, not successful evidence."
            ),
            evidence=worst_evidence,
        )
    return None


def _classify_worker_repeat_order_suppressions(
    latest_telemetry: Mapping[str, object],
    telemetry_archive: Sequence[Mapping[str, object]],
    max_allowed: int,
) -> MicroMachineSoakFailure | None:
    worst_count = 0
    worst_evidence: dict[str, object] = {}
    for telemetry in (*telemetry_archive, latest_telemetry):
        managers = telemetry.get("managers")
        if not isinstance(managers, Mapping):
            continue
        workers = managers.get("WorkerManager")
        if not isinstance(workers, Mapping):
            continue
        count = _int_value(workers.get("repeat_order_suppressed_count"))
        if count <= worst_count:
            continue
        worst_count = count
        worst_evidence = {
            "frame": _int_value(telemetry.get("frame")),
            "repeat_order_suppressed_count": count,
            "max_worker_repeat_order_suppressions": max_allowed,
            "root_cause_status": workers.get("root_cause_status"),
            "root_cause_reason": workers.get("root_cause_reason"),
            "worker_tag": workers.get("last_repeat_order_worker_tag"),
            "ability": workers.get("last_repeat_order_ability"),
            "target_kind": workers.get("last_repeat_order_target_kind"),
            "target_tag": workers.get("last_repeat_order_target_tag"),
            "target_x": workers.get("last_repeat_order_target_x"),
            "target_y": workers.get("last_repeat_order_target_y"),
        }

    if worst_count > max_allowed:
        return MicroMachineSoakFailure(
            code="worker_repeat_order_suppression",
            message=(
                "WorkerManager had to suppress repeated worker commands; this means "
                "a root-cause command generator is still reissuing duplicate orders."
            ),
            evidence=worst_evidence,
        )
    return None


def _classify_scout_duplicate_worker_move(
    latest_telemetry: Mapping[str, object],
    telemetry_archive: Sequence[Mapping[str, object]],
) -> MicroMachineSoakFailure | None:
    worst_evidence: dict[str, object] = {}
    for telemetry in (*telemetry_archive, latest_telemetry):
        managers = telemetry.get("managers")
        if not isinstance(managers, Mapping):
            continue
        workers = managers.get("WorkerManager")
        if not isinstance(workers, Mapping):
            continue
        if workers.get("root_cause_status") != "duplicate_command_safety_blocked":
            continue
        root_cause_reason = workers.get("root_cause_reason")
        if not isinstance(root_cause_reason, str) or not root_cause_reason.startswith("scout_"):
            continue
        worst_evidence = {
            "frame": _int_value(telemetry.get("frame")),
            "root_cause_status": workers.get("root_cause_status"),
            "root_cause_reason": root_cause_reason,
            "repeat_order_suppressed_count": _int_value(
                workers.get("repeat_order_suppressed_count")
            ),
            "worker_tag": workers.get("last_repeat_order_worker_tag"),
            "ability": workers.get("last_repeat_order_ability"),
            "target_kind": workers.get("last_repeat_order_target_kind"),
            "target_x": workers.get("last_repeat_order_target_x"),
            "target_y": workers.get("last_repeat_order_target_y"),
        }

    if worst_evidence:
        return MicroMachineSoakFailure(
            code="scout_duplicate_worker_move_command",
            message=(
                "ScoutManager generated repeated worker move commands that were only "
                "stopped by the WorkerManager safety guard; fix the ScoutManager "
                "decision path instead of relying on suppression."
            ),
            evidence=worst_evidence,
        )
    return None


def _int_value(value: object) -> int:
    if type(value) is bool:
        return 0
    if isinstance(value, int):
        return max(value, 0)
    return 0


def _float_value(value: object) -> float:
    if type(value) is bool:
        return 0.0
    if isinstance(value, (int, float)):
        return max(float(value), 0.0)
    return 0.0


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
    latest_telemetry: Mapping[str, object],
    telemetry_archive: Sequence[Mapping[str, object]],
    latest_frame: int,
    income_stall_frames: int,
) -> list[str]:
    min_frame = max(0, latest_frame - income_stall_frames)
    if _has_recent_worker_combat(log_text, min_frame):
        return []
    missing: list[str] = []
    if not _has_recent_positive_income(
        log_text, "Mineral income:", min_frame
    ) and not _has_recent_mining_activity(
        latest_telemetry,
        telemetry_archive,
        min_frame,
    ):
        missing.append("recent positive mineral income")
    if _has_recent_positive_gas_demand(log_text, min_frame) and not _has_recent_positive_income(
        log_text, "Gas income:", min_frame
    ):
        missing.append("recent positive gas income")
    return missing


def _has_recent_mining_activity(
    latest_telemetry: Mapping[str, object],
    telemetry_archive: Sequence[Mapping[str, object]],
    min_frame: int,
) -> bool:
    """Accept SC2 economy telemetry when score collection_rate is unavailable.

    Some latest-client/map combinations keep score_details.collection_rate_minerals
    at zero even while workers hold HARVEST_GATHER orders on real mineral fields.
    This is only used as an income fallback: it requires recent frame telemetry,
    self workers, and active mineral harvest orders or returns.
    """

    for entry in [*telemetry_archive, latest_telemetry]:
        if _int_value(entry.get("frame")) < min_frame:
            continue
        economy = entry.get("economy")
        if not isinstance(economy, Mapping):
            continue
        if _int_value(economy.get("self_worker_count")) <= 0:
            continue
        if _int_value(economy.get("harvest_gather_order_count")) > 0:
            return True
        if _int_value(economy.get("harvest_return_order_count")) > 0:
            return True
    return False


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
    *,
    latest_update: Mapping[str, object],
    latest_frame: int,
    config: MicroMachineSoakConfig,
) -> bool:
    entries = [*telemetry_archive, latest_telemetry]
    combat_seen = False
    production_seen = not _requires_production_intervention(config)
    scout_seen = False
    active_policy_seen = False
    latest_update_id = latest_update.get("update_id")
    expected_update_id = latest_update_id if isinstance(latest_update_id, str) else ""
    issued_at_frame = _int_value(latest_update.get("issued_at_frame"))
    production_consumption_due = bool(expected_update_id) and (
        latest_frame >= issued_at_frame + config.modulation_consumption_grace_frames
    )
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
        production = managers.get("ProductionManager")
        if isinstance(production, Mapping) and _production_doctrine_action_seen(
            production,
            expected_update_id=expected_update_id if production_consumption_due else "",
            min_doctrine_frame=issued_at_frame if production_consumption_due else 0,
        ):
            production_seen = True
        scout = managers.get("ScoutManager")
        if isinstance(scout, Mapping) and scout.get("bounded_intervention") is True:
            scout_seen = True
    return combat_seen and production_seen and scout_seen and active_policy_seen


def _requires_production_intervention(config: MicroMachineSoakConfig) -> bool:
    """Return whether the configured profile must prove ProductionManager action."""

    return not (
        config.expected_strategy_doctrine in NON_PRODUCTION_STRATEGY_DOCTRINES
        and not config.expected_production_actions
        and not config.expected_production_items
    )


def _production_doctrine_action_seen(
    production: Mapping[str, object],
    *,
    expected_update_id: str = "",
    min_doctrine_frame: int = 0,
) -> bool:
    """Return true only when ProductionManager actually changed its queue."""

    if production.get("bounded_intervention") is not True:
        return False
    if production.get("last_doctrine_fresh") is not True:
        return False
    action = str(production.get("last_doctrine_action", "") or "")
    item = str(production.get("last_doctrine_queue_item", "") or "")
    evidence = str(production.get("last_doctrine_evidence", "") or "")
    frame = _int_value(production.get("last_doctrine_frame"))
    policy_update_id = str(production.get("policy_update_id", "") or "")
    last_update_id = str(production.get("last_doctrine_update_id", "") or "")
    strategy_doctrine = str(production.get("strategy_doctrine", "") or "")
    last_doctrine = str(production.get("last_doctrine", "") or "")
    if expected_update_id and (
        policy_update_id != expected_update_id or last_update_id != expected_update_id
    ):
        return False
    if min_doctrine_frame and frame < min_doctrine_frame:
        return False
    return bool(
        action
        and action != "none"
        and item
        and item != "none"
        and evidence in PRODUCTION_DOCTRINE_EVIDENCE_VALUES
        and frame > 0
        and policy_update_id
        and last_update_id == policy_update_id
        and strategy_doctrine
        and last_doctrine == strategy_doctrine
    )


def _classify_expected_strategy_consumption(
    latest_telemetry: Mapping[str, object],
    telemetry_archive: Sequence[Mapping[str, object]],
    latest_frame: int,
    config: MicroMachineSoakConfig,
) -> MicroMachineSoakFailure | None:
    expected_doctrine = config.expected_strategy_doctrine
    expected_actions = set(config.expected_production_actions)
    expected_items = set(config.expected_production_items)
    if (
        latest_frame < config.target_frame
        or not expected_doctrine
        and not expected_actions
        and not expected_items
    ):
        return None
    if not _requires_production_intervention(config):
        return None

    best: Mapping[str, object] | None = None
    observed_actions: set[str] = set()
    observed_items: set[str] = set()
    observed_doctrines: set[str] = set()
    latest_managers = latest_telemetry.get("managers")
    latest_production = (
        latest_managers.get("ProductionManager")
        if isinstance(latest_managers, Mapping)
        else None
    )
    expected_update_id = ""
    min_doctrine_frame = 0
    latest_strategy_doctrine = ""
    if isinstance(latest_production, Mapping):
        expected_update_id = str(latest_production.get("policy_update_id", "") or "")
        min_doctrine_frame = _int_value(latest_production.get("policy_issued_at_frame"))
        latest_strategy_doctrine = str(
            latest_production.get("strategy_doctrine", "") or ""
        )
    if expected_doctrine and latest_strategy_doctrine != expected_doctrine:
        return MicroMachineSoakFailure(
            code="strategy_consumption_mismatch",
            message=(
                "Latest ProductionManager strategy doctrine does not match the "
                "expected MicroMachine strategy."
            ),
            evidence={
                "expected_strategy_doctrine": expected_doctrine,
                "latest_strategy_doctrine": latest_strategy_doctrine,
                "latest_policy_update_id": expected_update_id,
                "observed_doctrines": (
                    [latest_strategy_doctrine] if latest_strategy_doctrine else []
                ),
                "observed_actions": [],
                "observed_items": [],
            },
        )
    for entry in (*telemetry_archive, latest_telemetry):
        managers = entry.get("managers")
        if not isinstance(managers, Mapping):
            continue
        production = managers.get("ProductionManager")
        if not isinstance(production, Mapping):
            continue
        doctrine = str(production.get("strategy_doctrine", "") or "")
        last_doctrine = str(production.get("last_doctrine", "") or "")
        action = str(production.get("last_doctrine_action", "") or "")
        item = str(production.get("last_doctrine_queue_item", "") or "")
        if doctrine:
            observed_doctrines.add(doctrine)
        if last_doctrine:
            observed_doctrines.add(last_doctrine)
        if action and action != "none":
            observed_actions.add(action)
        if item and item != "none":
            observed_items.add(item)

        doctrine_ok = not expected_doctrine or (
            doctrine == expected_doctrine and last_doctrine == expected_doctrine
        )
        action_ok = not expected_actions or action in expected_actions
        item_ok = not expected_items or item in expected_items
        if (
            doctrine_ok
            and action_ok
            and item_ok
            and _production_doctrine_action_seen(
                production,
                expected_update_id=expected_update_id,
                min_doctrine_frame=min_doctrine_frame,
            )
        ):
            best = production
            break

    if best is not None:
        expected_actual_items = _expected_actual_production_items(config)
        actual_seen, observed_actual_items, observed_actual_commands = (
            _expected_actual_production_command_seen(
                (*telemetry_archive, latest_telemetry),
                expected_update_id=expected_update_id,
                min_command_frame=min_doctrine_frame,
                expected_items=expected_actual_items,
            )
        )
        if actual_seen:
            return None
        return MicroMachineSoakFailure(
            code="strategy_actual_command_missing",
            message=(
                "ProductionManager consumed the expected strategy, but no matching "
                "actual build/train/morph/upgrade command was observed for that "
                "strategy and update."
            ),
            evidence={
                "expected_strategy_doctrine": expected_doctrine,
                "expected_actual_production_items": sorted(expected_actual_items),
                "observed_actual_items": sorted(observed_actual_items),
                "observed_actual_commands": sorted(observed_actual_commands),
                "latest_policy_update_id": expected_update_id,
            },
        )

    return MicroMachineSoakFailure(
        code="strategy_consumption_mismatch",
        message=(
            "ProductionManager did not consume the expected MicroMachine strategy "
            "mode/action/item evidence."
        ),
        evidence={
            "expected_strategy_doctrine": expected_doctrine,
            "expected_production_actions": sorted(expected_actions),
            "expected_production_items": sorted(expected_items),
            "observed_doctrines": sorted(observed_doctrines),
            "observed_actions": sorted(observed_actions),
            "observed_items": sorted(observed_items),
        },
    )


def _expected_actual_production_items(config: MicroMachineSoakConfig) -> set[str]:
    if config.expected_strategy_doctrine in EXPECTED_ACTUAL_PRODUCTION_ITEMS_BY_DOCTRINE:
        return set(EXPECTED_ACTUAL_PRODUCTION_ITEMS_BY_DOCTRINE[config.expected_strategy_doctrine])
    return {_canonical_actual_production_item(item) for item in config.expected_production_items}


def _canonical_actual_production_item(item: object) -> str:
    raw = str(item or "").strip()
    return ACTUAL_PRODUCTION_ITEM_ALIASES.get(raw, raw)


def _expected_actual_production_command_seen(
    telemetry_entries: Sequence[Mapping[str, object]],
    *,
    expected_update_id: str,
    min_command_frame: int,
    expected_items: set[str],
) -> tuple[bool, set[str], set[str]]:
    observed_actual_items: set[str] = set()
    observed_actual_commands: set[str] = set()
    for entry in telemetry_entries:
        managers = entry.get("managers")
        if not isinstance(managers, Mapping):
            continue
        production = managers.get("ProductionManager")
        if not isinstance(production, Mapping):
            continue
        item = _canonical_actual_production_item(
            production.get("last_actual_production_command_item", "")
        )
        kind = str(production.get("last_actual_production_command_kind", "") or "")
        update_id = str(production.get("last_actual_production_command_update_id", "") or "")
        frame = _int_value(production.get("last_actual_production_command_frame"))
        count = _int_value(production.get("actual_production_command_issued_count"))
        if item and item != "none":
            observed_actual_items.add(item)
        if item and item != "none" and kind and kind != "none":
            observed_actual_commands.add(f"{kind}|{item}")
        if (
            count > 0
            and (not expected_update_id or update_id == expected_update_id)
            and (not expected_items or item in expected_items)
            and frame > 0
            and (min_command_frame <= 0 or frame >= min_command_frame)
        ):
            return True, observed_actual_items, observed_actual_commands
    return False, observed_actual_items, observed_actual_commands


def _classify_expected_tactical_actual_commands(
    observation: MicroMachineSoakObservation,
    latest_telemetry: Mapping[str, object],
    telemetry_archive: Sequence[Mapping[str, object]],
    latest_frame: int,
    config: MicroMachineSoakConfig,
) -> MicroMachineSoakFailure | None:
    if latest_frame < config.target_frame or not config.expected_tactical_effects:
        return None

    expected_effects = set(config.expected_tactical_effects)
    latest_update = _latest_modulation_update(observation)
    expected_update_id = str(latest_update.get("update_id", "") or "")
    min_command_frame = _int_value(latest_update.get("issued_at_frame"))
    failures: list[str] = []
    evidence: dict[str, object] = {
        "expected_tactical_effects": sorted(expected_effects),
        "latest_policy_update_id": expected_update_id,
        "min_command_frame": min_command_frame,
    }
    pressure_like_effects = {"pressure", "contain", "harass", "target_priority"}
    if expected_effects & pressure_like_effects:
        combat_seen, combat_evidence = _actual_combat_command_seen(
            (*telemetry_archive, latest_telemetry),
            expected_update_id=expected_update_id,
            min_command_frame=min_command_frame,
        )
        evidence["combat"] = combat_evidence
        if not combat_seen:
            failures.append("combat_actual_command")
    if "scout" in expected_effects:
        scout_seen, scout_evidence = _actual_scout_command_seen(
            (*telemetry_archive, latest_telemetry),
            expected_update_id=expected_update_id,
            min_command_frame=min_command_frame,
        )
        evidence["scout"] = scout_evidence
        if not scout_seen:
            failures.append("scout_actual_command")

    if not failures:
        return None
    return MicroMachineSoakFailure(
        code="tactical_actual_command_missing",
        message=(
            "Expected tactical effects were observed as manager bias/intent, but "
            "matching MicroMachine command-level evidence was missing."
        ),
        evidence={**evidence, "missing": failures},
    )


def _actual_combat_command_seen(
    telemetry_entries: Sequence[Mapping[str, object]],
    *,
    expected_update_id: str,
    min_command_frame: int,
) -> tuple[bool, dict[str, object]]:
    best: dict[str, object] = {}
    observed_actions: list[str] = []
    for entry in telemetry_entries:
        managers = entry.get("managers")
        if not isinstance(managers, Mapping):
            continue
        commander = managers.get("GameCommander")
        combat = managers.get("CombatCommander")
        if not isinstance(commander, Mapping) or not isinstance(combat, Mapping):
            continue
        update_id = str(commander.get("update_id", "") or "")
        action = str(combat.get("main_attack_last_issued_action", "") or "")
        frame = _int_value(combat.get("main_attack_last_action_frame"))
        count = _int_value(combat.get("main_attack_actual_command_issued_count"))
        if action:
            observed_actions.append(action)
        best = {
            "frame": _int_value(entry.get("frame")),
            "update_id": update_id,
            "main_attack_actual_command_issued_count": count,
            "main_attack_last_action_frame": frame,
            "main_attack_last_issued_action": action,
            "main_attack_order_status": combat.get("main_attack_order_status"),
        }
        if (
            count > 0
            and action
            and "squad=MainAttack" in action
            and str(combat.get("main_attack_order_status", "") or "") == "Attack"
            and (not expected_update_id or update_id == expected_update_id)
            and frame > 0
            and (min_command_frame <= 0 or frame >= min_command_frame)
        ):
            return True, best
    best["observed_actions"] = observed_actions[-8:]
    return False, best


def _actual_scout_command_seen(
    telemetry_entries: Sequence[Mapping[str, object]],
    *,
    expected_update_id: str,
    min_command_frame: int,
) -> tuple[bool, dict[str, object]]:
    best: dict[str, object] = {}
    observed_commands: list[str] = []
    tactical_scout_requested = any(
        isinstance((entry.get("managers") or {}), Mapping)
        and isinstance((entry.get("managers") or {}).get("TacticalTask"), Mapping)
        and str(
            ((entry.get("managers") or {}).get("TacticalTask") or {}).get(
                "task_type",
                "",
            )
            or ""
        )
        == "scout_with_units"
        for entry in telemetry_entries
    )
    for entry in telemetry_entries:
        managers = entry.get("managers")
        if not isinstance(managers, Mapping):
            continue
        commander = managers.get("GameCommander")
        tactical = managers.get("TacticalTask")
        combat = managers.get("CombatCommander")
        update_id = (
            str(commander.get("update_id", "") or "") if isinstance(commander, Mapping) else ""
        )
        if isinstance(tactical, Mapping) and str(tactical.get("task_type", "") or "") == "scout_with_units":
            command = str(tactical.get("last_actual_command", "") or "")
            frame = _int_value(tactical.get("last_actual_command_frame"))
            count = _int_value(tactical.get("actual_command_issued_count"))
            status = str(tactical.get("status", "") or "")
            if command:
                observed_commands.append(command)
            best = {
                "frame": _int_value(entry.get("frame")),
                "update_id": update_id,
                "source": "TacticalTask scout_with_units",
                "status": status,
                "actual_command_issued_count": count,
                "last_actual_command_frame": frame,
                "last_actual_command": command,
                "reason": tactical.get("reason"),
            }
            if (
                status == "executing"
                and count > 0
                and "squad=Scout" in command
                and (not expected_update_id or update_id == expected_update_id)
                and frame > 0
                and (min_command_frame <= 0 or frame >= min_command_frame)
            ):
                return True, best
        if isinstance(combat, Mapping):
            command = str(combat.get("scout_last_issued_action", "") or "")
            frame = _int_value(combat.get("scout_last_action_frame"))
            count = _int_value(combat.get("scout_actual_command_issued_count"))
            if command:
                observed_commands.append(command)
            if (
                count > 0
                and "squad=Scout" in command
                and (not expected_update_id or update_id == expected_update_id)
                and frame > 0
                and (min_command_frame <= 0 or frame >= min_command_frame)
            ):
                return True, {
                    "frame": _int_value(entry.get("frame")),
                    "update_id": update_id,
                    "source": "CombatCommander scout action",
                    "actual_command_issued_count": count,
                    "last_actual_command_frame": frame,
                    "last_actual_command": command,
                }
        if tactical_scout_requested:
            continue
        scout = managers.get("ScoutManager")
        workers = managers.get("WorkerManager")
        if isinstance(scout, Mapping):
            command = str(scout.get("last_actual_command", "") or "")
            frame = _int_value(scout.get("last_actual_command_frame"))
            count = _int_value(scout.get("actual_command_issued_count"))
            depth_ok, depth_evidence = _scout_depth_progress_satisfied(scout)
            if command:
                observed_commands.append(command)
            best = {
                "frame": _int_value(entry.get("frame")),
                "update_id": update_id,
                "actual_command_issued_count": count,
                "last_actual_command_frame": frame,
                "last_actual_command": command,
                "status": scout.get("status"),
                **depth_evidence,
            }
            if (
                count > 0
                and command
                and depth_ok
                and (not expected_update_id or update_id == expected_update_id)
                and frame > 0
                and (min_command_frame <= 0 or frame >= min_command_frame)
            ):
                return True, best
        if isinstance(workers, Mapping):
            reason = str(workers.get("last_trace_reason", "") or "")
            status = str(workers.get("last_trace_status", "") or "")
            frame = _int_value(workers.get("last_trace_frame"))
            if reason.startswith("scout_"):
                observed_commands.append(f"{status}|{reason}")
            fallback = {
                "frame": _int_value(entry.get("frame")),
                "update_id": update_id,
                "last_trace_frame": frame,
                "last_trace_status": status,
                "last_trace_reason": reason,
                "last_trace_target_kind": workers.get("last_trace_target_kind"),
                "source": "WorkerManager scout trace fallback",
            }
            if (
                reason.startswith("scout_")
                and status == "accepted_candidate"
                and (not expected_update_id or update_id == expected_update_id)
                and frame > 0
                and (min_command_frame <= 0 or frame >= min_command_frame)
            ):
                fallback["depth_progress_required"] = True
            if not best:
                best = fallback
    if tactical_scout_requested:
        best.setdefault("source", "TacticalTask scout_with_units")
        best["required_actual_command"] = "squad=Scout"
    best["observed_commands"] = observed_commands[-8:]
    return False, best


def _scout_depth_progress_satisfied(scout: Mapping[str, object]) -> tuple[bool, dict[str, object]]:
    """Require visible scout progress, not only a command emission.

    A short command near home can still increment MicroMachine command counters.
    Production sign-off needs evidence that the scout either left the main area
    or reached enemy/deep scouting space.
    """

    last_target_distance = _float_value(scout.get("last_target_distance"))
    max_home_distance = _float_value(scout.get("max_home_distance"))
    min_enemy_base_distance = _float_value(scout.get("min_enemy_base_distance"))
    deep_scout_frame_count = _int_value(scout.get("deep_scout_frame_count"))
    evidence = {
        "last_target_distance": last_target_distance,
        "max_home_distance": max_home_distance,
        "min_enemy_base_distance": min_enemy_base_distance,
        "deep_scout_frame_count": deep_scout_frame_count,
    }
    if deep_scout_frame_count >= 16:
        return True, evidence
    if min_enemy_base_distance > 0.0 and min_enemy_base_distance <= 18.0:
        return True, evidence
    if max_home_distance >= 22.0 and last_target_distance >= 18.0:
        return True, evidence
    return False, evidence


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
    active_ids = telemetry_for_policy.get("active_modulation_ids")
    latest_update = _latest_modulation_update(observation)
    latest_update_id = latest_update.get("update_id")
    issued_at_frame = _int_value(latest_update.get("issued_at_frame"))
    expires_at_frame = _int_value(latest_update.get("expires_at_frame"))
    consumption_due = latest_frame >= issued_at_frame + config.modulation_consumption_grace_frames
    if isinstance(commander, Mapping) and commander.get("policy_active") is False and consumption_due:
        return MicroMachineSoakFailure(
            code="stale_modulation",
            message="GameCommander reports inactive policy modulation.",
            evidence={
                "latest_frame": latest_frame,
                "issued_at_frame": issued_at_frame,
                "modulation_consumption_grace_frames": config.modulation_consumption_grace_frames,
            },
        )
    if isinstance(active_ids, list) and not active_ids and consumption_due:
        return MicroMachineSoakFailure(
            code="stale_modulation",
            message="Telemetry has no active modulation ids.",
            evidence={
                "latest_frame": latest_frame,
                "issued_at_frame": issued_at_frame,
                "modulation_consumption_grace_frames": config.modulation_consumption_grace_frames,
            },
        )
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
        and consumption_due
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
