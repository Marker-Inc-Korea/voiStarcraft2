"""Deterministic MicroMachine pre-live journeys from raw runtime evidence."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import select
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, cast

from starcraft_commander import web_gui
from starcraft_commander.micromachine_battlefield_projection import (
    select_latest_battlefield_projection,
    validate_battlefield_overview,
)
from starcraft_commander.micromachine_bridge import (
    validate_micromachine_blackboard_update,
)
from starcraft_commander.micromachine_command_execution import (
    classify_micromachine_operation_executions,
)
from starcraft_commander.micromachine_live_session import (
    MicroMachineLiveTextSession,
    StaticJsonPolicyModulationProvider,
)
from starcraft_commander.micromachine_pre_live_artifact import (
    _archive_framing_error,
    canonical_json_bytes,
)
from starcraft_commander.micromachine_runtime import (
    MicroMachineInMemoryBlackboard,
)
from starcraft_commander.micromachine_tactical_evidence import (
    classify_micromachine_tactical_evidence,
)
from starcraft_commander.micromachine_terran_capabilities import (
    all_terran_capability_matrix,
    canonical_terran_unit_family,
    lower_terran_natural_language_units,
    operation_family_evidence,
    terran_ability_caster_state,
    terran_production_targets,
)
from starcraft_commander.policy_modulation import PolicyModulationVector


PRE_LIVE_JOURNEY_SCHEMA_VERSION: Final[int] = 1
PRE_LIVE_JOURNEY_BUNDLE_SCHEMA_VERSION: Final[int] = 1
PRE_LIVE_JOURNEY_REPORT_SCHEMA_VERSION: Final[int] = 1
PRE_LIVE_NATIVE_ADAPTER_SCHEMA_VERSION: Final[int] = 2
PRE_LIVE_JOURNEY_EVIDENCE_KIND: Final[str] = (
    "deterministic_micromachine_pre_live_journeys"
)
MAX_JOURNEY_BUNDLE_BYTES: Final[int] = 64 * 1024 * 1024
MAX_JOURNEY_BUNDLE_ENTRIES: Final[int] = 64
MAX_JOURNEY_MEMBER_BYTES: Final[int] = 32 * 1024 * 1024
MAX_NATIVE_ADAPTER_OUTPUT_BYTES: Final[int] = 32 * 1024 * 1024
MAX_NATIVE_EXECUTABLE_BYTES: Final[int] = 256 * 1024 * 1024
MAX_TACTICAL_RADIO_OUTPUT_BYTES: Final[int] = 256 * 1024
PINNED_NATIVE_EXEC_ROOT_ENV: Final[str] = "VOI_PINNED_NATIVE_EXEC_ROOT"
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
DEFAULT_JOURNEY_MANIFEST: Final[Path] = (
    REPO_ROOT / "integrations" / "micromachine" / "PRE_LIVE_JOURNEYS.json"
)
DETERMINISTIC_ZIP_TIMESTAMP: Final[tuple[int, int, int, int, int, int]] = (
    1980,
    1,
    1,
    0,
    0,
    0,
)
_REGULAR_FILE_MODE: Final[int] = 0o100644
_REQUIRED_JOURNEY_IDS: Final[frozenset[str]] = frozenset(
    {
        "all_terran_family_ability_blocker_matrix",
        "autonomous_defense_restoration",
        "emergency_preemption",
        "event_reconnect_replay",
        "parallel_scout_attack_defend",
        "protected_minimum_partial_rejection",
        "reinforcement_generation_update",
        "retarget",
        "safe_partial_launch",
        "selective_cancellation",
        "shortage_prerequisite_wait",
        "transfer_rejection_preserves_active",
        "transfer_success",
        "voice_readback_callout_identity",
    }
)
_EFFECT_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {"movement", "engagement", "ability_effect"}
)
_WEB_LIFECYCLE_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {"ownership_snapshot", "submission", "movement", "engagement"}
)
_NATIVE_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "ability_effect",
        "autonomous_defense",
        "cancellation",
        "client_reconnect",
        "engagement",
        "family_action_attempt",
        "launch_decision",
        "movement",
        "ownership_snapshot",
        "preemption",
        "prerequisite_wait",
        "production_decision",
        "production_path_receipt",
        "rejection",
        "squad_order",
        "submission",
        "transfer",
    }
)
_NATIVE_ACTION_METADATA_FIELDS: Final[tuple[str, ...]] = (
    "unit_type",
    "dispatch_action",
    "ability_name",
    "ability_id",
    "target_kind",
    "target_x",
    "target_y",
)
_ALL_TERRAN_COMPOSITION_BLOCKER: Final[str] = (
    "composition_prerequisites_pending"
)
_NATIVE_ACTION_PROOF_FIELDS: Final[tuple[str, ...]] = (
    *_NATIVE_ACTION_METADATA_FIELDS,
    "unit_tags",
    "receipt_id",
    "submission_ids",
)
_OPERATION_IDENTITY_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "ownership_snapshot",
        "squad_order",
        "submission",
        "movement",
        "engagement",
        "ability_effect",
        "launch_decision",
        "production_decision",
        "prerequisite_wait",
        "production_path_receipt",
        "transfer",
        "generation_change",
        "cancellation",
        "preemption",
        "autonomous_defense",
        "family_action_attempt",
        "web_projection",
        "hud_projection",
        "voice_projection",
        "voice_callout",
    }
)
_GENERATION_ACTIVATION_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "ownership_snapshot",
        "family_action_attempt",
        "squad_order",
        "submission",
        "movement",
        "engagement",
        "ability_effect",
    }
)
_GENERATION_LIFECYCLE_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        *_GENERATION_ACTIVATION_EVENT_TYPES,
        "launch_decision",
        "production_decision",
        "prerequisite_wait",
        "production_path_receipt",
        "transfer",
        "cancellation",
        "preemption",
        "autonomous_defense",
    }
)
_NATIVE_OUTPUT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "events",
        "snapshots",
        "final_state",
        "operation_director",
        "battlefield_overview",
        "hud",
        "production_path",
        "telemetry",
    }
)
_NATIVE_INPUT_FIELDS: Final[frozenset[str]] = frozenset(
    {"schema_version", "journey_id", "initial_state", "steps"}
)
_NATIVE_PRODUCTION_PATH_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "executor_kind",
        "squad_order_execution_path",
        "sc2_submission_execution_path",
        "operation_manager_entrypoint",
        "squad_order_entrypoint",
        "sc2_submission_entrypoint",
        "operation_ownership_receipt_count",
        "squad_order_receipt_count",
        "sc2_submission_receipt_count",
        "applied_squad_orders",
        "dispatched_sc2_actions",
        "squad_order_receipts",
        "sc2_submission_receipts",
    }
)
_NATIVE_PRODUCTION_ENTRYPOINTS: Final[dict[str, str]] = {
    "operation_ownership_receipt_count": (
        "voiProductionAssignOperationOwner"
    ),
    "squad_order_receipt_count": "voiProductionIssueSquadOrder",
    "sc2_submission_receipt_count": "voiProductionSubmitSc2Action",
}
_NATIVE_PRODUCTION_ENTRYPOINT_FIELDS: Final[dict[str, str]] = {
    "operation_ownership_receipt_count": "operation_manager_entrypoint",
    "squad_order_receipt_count": "squad_order_entrypoint",
    "sc2_submission_receipt_count": "sc2_submission_entrypoint",
}
_NATIVE_ADAPTER_PRODUCT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "binary_sha256",
        "embedded_build_input_identity",
        "input",
        "output",
    }
)
_RAW_EVENT_FIELDS: Final[frozenset[str]] = frozenset(
    {"seq", "event_type", "identity", "payload"}
)
_RAW_EVENT_IDENTITY_FIELDS: Final[frozenset[str]] = frozenset(
    {"update_id", "operation_id", "generation", "stage", "game_frame"}
)
_DERIVED_MATRIX_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "suite_id",
        "manifest_sha256",
        "journey_count",
        "passed_count",
        "failed_count",
        "failures",
        "ok",
        "status",
        "journeys",
    }
)
_DERIVED_MATRIX_JOURNEY_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "title",
        "event_count",
        "event_types",
        "ownership_snapshot_count",
        "blockers",
        "ok",
        "status",
        "product_paths",
    }
)
_INITIAL_STATE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "units",
        "structures",
        "protected_minimum",
        "minerals",
        "vespene",
        "prerequisites",
        "event_cursor",
        "voice_enabled",
        "muted",
    }
)
_CANONICAL_EVENT_STAGES: Final[dict[str, frozenset[str]]] = {
    "ability_effect": frozenset({"effect_observed"}),
    "autonomous_defense": frozenset({"preempted", "restored"}),
    "blackboard_update": frozenset({"published"}),
    "cancellation": frozenset({"cancelled"}),
    "client_reconnect": frozenset({"observed"}),
    "command_input": frozenset({"input"}),
    "engagement": frozenset({"effect_observed"}),
    "family_action_attempt": frozenset({"attempted"}),
    "generation_change": frozenset({"published"}),
    "hud_projection": frozenset({"assigned", "effect_observed"}),
    "launch_decision": frozenset({"launch_admitted", "launch_rejected"}),
    "movement": frozenset({"effect_observed"}),
    "ownership_snapshot": frozenset({"assigned"}),
    "preemption": frozenset({"preempted"}),
    "prerequisite_wait": frozenset({"prerequisite_wait"}),
    "production_path_receipt": frozenset({"accepted"}),
    "production_decision": frozenset({"production_wait"}),
    "rejection": frozenset({"blocked"}),
    "replay_batch": frozenset({"replayed"}),
    "replay_deduplicated": frozenset({"replayed"}),
    "squad_order": frozenset({"order_issued"}),
    "state_snapshot": frozenset({"state_before", "state_after"}),
    "submission": frozenset({"submitted"}),
    "terran_lowering": frozenset({"parsed"}),
    "transfer": frozenset({"applied"}),
    "voice_callout": frozenset({"effect_observed"}),
    "voice_projection": frozenset({"effect_observed"}),
    "web_event": frozenset({"web_event"}),
    "web_projection": frozenset({"assigned", "effect_observed"}),
}
_SHA256_IDENTITY_PREFIX: Final[str] = "sha256:"
_TACTICAL_RADIO_VARIABLE_ANCHORS: Final[tuple[str, ...]] = (
    "var TACTICAL_RADIO_MAX_QUEUE =",
    "var TACTICAL_RADIO_MAX_CAPTION_HISTORY =",
    "var TACTICAL_RADIO_MAX_SPEECH_CHARS =",
    "var TACTICAL_RADIO_MAX_OPERATION_HIGH_WATER =",
    "var TACTICAL_RADIO_PRIORITY_INTERVAL_MS =",
    "var TACTICAL_RADIO_DEDUPE_TTL_MS =",
    "var TACTICAL_RADIO_REPLAY_MAX_AGE_MS =",
    "var tacticalRadio =",
)
_TACTICAL_RADIO_FUNCTION_NAMES: Final[tuple[str, ...]] = (
    "tacticalRadioNow",
    "tacticalRadioUiState",
    "renderTacticalRadioState",
    "renderTacticalRadioCaptions",
    "appendTacticalRadioCaption",
    "clearTacticalRadioTimer",
    "interruptTacticalRadioSpeech",
    "cancelTacticalRadioSpeechAndQueue",
    "resetTacticalRadio",
    "ensureTacticalRadioScope",
    "rememberBoundedTacticalRadioValue",
    "tacticalRadioOperationKey",
    "rememberTacticalRadioHighWater",
    "tacticalRadioDedupeExpired",
    "tacticalRadioSpeechText",
    "tacticalRadioQueueSort",
    "compactTacticalRadioQueue",
    "speakNextTacticalRadioCallout",
    "queueTacticalRadioCallout",
    "tacticalRadioSetMuted",
    "normalizedTacticalReason",
    "operationEventMatchesRecordUpdate",
    "tacticalLifecycleCallout",
    "announceOperationLifecycleEvent",
)
_TACTICAL_RADIO_CALLOUT_KINDS: Final[frozenset[str]] = frozenset(
    {
        "assigned",
        "partially_assigned",
        "movement_observed",
        "moving",
        "engagement_observed",
        "engaged",
        "target_reached",
        "reached",
        "completed",
        "blocked",
        "waiting",
        "emergency_retreat",
        "base_under_attack",
        "critical_ability_failure",
        "force_loss",
        "submitted",
    }
)
_TACTICAL_RADIO_RESULT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "primary_accepted",
        "secondary_accepted",
        "duplicate_primary_accepted",
        "duplicate_secondary_accepted",
        "stale_accepted",
        "production_announcement_calls",
        "muted_accepted",
        "queue_length_before_drain",
        "queue_length_after_drain",
        "caption_count",
        "spoken",
        "muted_caption_delta",
        "muted_speech_delta",
        "final_muted",
        "primary_callout",
        "secondary_callout",
        "frame_high_water",
        "timeline_high_water",
    }
)
_TACTICAL_RADIO_RUNTIME_FIELDS: Final[frozenset[str]] = frozenset(
    {
        *_TACTICAL_RADIO_RESULT_FIELDS,
        "runtime",
        "node_sha256",
        "source_sha256",
    }
)
CommandRunner = Callable[..., subprocess.CompletedProcess[Any]]


@dataclass
class _JourneyExecution:
    spec: Mapping[str, object]
    backend: MicroMachineInMemoryBlackboard = field(
        default_factory=MicroMachineInMemoryBlackboard
    )
    timeline: object = field(
        default_factory=web_gui._OperationSemanticTimelineReducer
    )
    journal: object = field(default_factory=web_gui._WebEventJournal)
    events: list[dict[str, object]] = field(default_factory=list)
    products: dict[str, object] = field(default_factory=dict)
    compiled_updates: list[dict[str, object]] = field(default_factory=list)
    native_steps: list[dict[str, object]] = field(default_factory=list)
    initial_units: list[dict[str, object]] = field(default_factory=list)
    matrix_ability_by_tag: dict[int, str] = field(default_factory=dict)
    next_tag: int = 1000

    @property
    def journey_id(self) -> str:
        return str(self.spec["id"])

    def emit(
        self,
        event_type: str,
        *,
        update_id: str,
        operation_id: str = "",
        generation: int = 0,
        stage: str,
        game_frame: int,
        payload: Mapping[str, object] | None = None,
        order: int = 10,
    ) -> None:
        self.events.append(
            {
                "_order": order,
                "event_type": event_type,
                "identity": {
                    "update_id": update_id,
                    "operation_id": operation_id,
                    "generation": generation,
                    "stage": stage,
                    "game_frame": game_frame,
                },
                "payload": deepcopy(dict(payload or {})),
            }
        )

    def allocate_unit(
        self,
        unit_type: str,
        *,
        cloak_state: str = "",
        matrix_ability: str = "",
    ) -> dict[str, object]:
        unit = {
            "tag": self.next_tag,
            "unit_type": unit_type,
            "home_distance": 0.0,
        }
        if cloak_state:
            unit["cloak_state"] = cloak_state
        if matrix_ability:
            self.matrix_ability_by_tag[self.next_tag] = matrix_ability
        self.next_tag += 1
        self.initial_units.append(unit)
        return unit


def load_pre_live_journey_manifest(
    path: Path | str = DEFAULT_JOURNEY_MANIFEST,
) -> dict[str, object]:
    payload = json.loads(
        Path(path).read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_json_object_keys,
        parse_constant=_reject_nonfinite_json,
    )
    return _validate_pre_live_journey_manifest_payload(payload)


def _validate_pre_live_journey_manifest_payload(
    payload: object,
) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError("pre-live journey manifest must be a JSON object")
    schema_version = payload.get("schema_version")
    if (
        type(schema_version) is not int
        or schema_version != PRE_LIVE_JOURNEY_SCHEMA_VERSION
    ):
        raise ValueError("pre-live journey manifest schema_version is unsupported")
    if set(payload) != {
        "schema_version",
        "suite_id",
        "identity_fields",
        "journeys",
    }:
        raise ValueError("pre-live journey manifest has unexpected top-level fields")
    if payload.get("identity_fields") != [
        "update_id",
        "operation_id",
        "generation",
        "stage",
        "game_frame",
    ]:
        raise ValueError("pre-live journey identity_fields contract drifted")
    suite_id = payload.get("suite_id")
    if type(suite_id) is not str or not suite_id:
        raise ValueError("pre-live journey manifest suite_id must be a string")
    journeys = payload.get("journeys")
    if not isinstance(journeys, list) or len(journeys) != 14:
        raise ValueError("pre-live journey manifest must define exactly 14 journeys")
    required = {
        "id",
        "title",
        "kind",
        "initial_state",
        "ordered_inputs",
        "expected_raw_event_types",
        "stop_condition",
        "timeout_frames",
        "allowed_nondeterminism",
    }
    seen: set[str] = set()
    for index, journey in enumerate(journeys):
        if not isinstance(journey, dict) or set(journey) != required:
            raise ValueError(f"journey {index} has an invalid field set")
        for field_name in ("id", "title", "kind"):
            value = journey.get(field_name)
            if type(value) is not str or not value:
                raise ValueError(
                    f"journey {index} {field_name} must be a non-empty string"
                )
        journey_id = cast(str, journey["id"])
        if not journey_id or journey_id in seen:
            raise ValueError("journey ids must be non-empty and unique")
        seen.add(journey_id)
        if not isinstance(journey.get("initial_state"), Mapping):
            raise ValueError(f"{journey_id} initial_state must be an object")
        _validate_initial_state(
            journey_id,
            str(journey.get("kind", "")),
            cast(Mapping[str, object], journey["initial_state"]),
        )
        ordered_inputs = journey.get("ordered_inputs")
        if not isinstance(ordered_inputs, list) or not ordered_inputs:
            raise ValueError(f"{journey_id} requires ordered_inputs")
        frames = [
            item.get("frame")
            for item in ordered_inputs
            if isinstance(item, Mapping)
        ]
        if (
            len(frames) != len(ordered_inputs)
            or any(type(frame) is not int or frame < 0 for frame in frames)
            or frames != sorted(frames)
        ):
            raise ValueError(f"{journey_id} ordered input frames are invalid")
        for item in ordered_inputs:
            if not isinstance(item, Mapping):
                raise ValueError(f"{journey_id} ordered input must be an object")
            command = "command_text" in item or "preset" in item
            observation = type(item.get("kind")) is str
            if command == observation:
                raise ValueError(
                    f"{journey_id} input must be one command or one observation"
                )
            for field_name in ("command_text", "preset"):
                if field_name in item and (
                    type(item[field_name]) is not str or not item[field_name]
                ):
                    raise ValueError(
                        f"{journey_id} input {field_name} must be a "
                        "non-empty string"
                    )
            if "kind" in item and not item["kind"]:
                raise ValueError(
                    f"{journey_id} input kind must be a non-empty string"
                )
            if "base_id" in item and (
                type(item["base_id"]) is not str or not item["base_id"]
            ):
                raise ValueError(
                    f"{journey_id} input base_id must be a non-empty string"
                )
            if "structures" in item:
                _string_list(
                    item["structures"],
                    label=f"{journey_id} input structures",
                )
            input_units = item.get("units")
            if isinstance(input_units, Mapping) and any(
                type(unit_type) is not str or not unit_type
                for unit_type in input_units
            ):
                raise ValueError(
                    f"{journey_id} input unit types must be strings"
                )
            if isinstance(input_units, list):
                for descriptor in input_units:
                    if (
                        not isinstance(descriptor, Mapping)
                        or type(descriptor.get("unit_type")) is not str
                        or not descriptor.get("unit_type")
                    ):
                        raise ValueError(
                            f"{journey_id} input unit descriptor is invalid"
                        )
        expected = journey.get("expected_raw_event_types")
        if (
            not isinstance(expected, list)
            or not expected
            or any(not isinstance(item, str) or not item for item in expected)
            or len(expected) != len(set(expected))
        ):
            raise ValueError(f"{journey_id} expected raw events are invalid")
        stop = journey.get("stop_condition")
        if (
            not isinstance(stop, Mapping)
            or "type" not in stop
            or "count" not in stop
            or type(stop.get("type")) is not str
            or not stop.get("type")
            or type(stop.get("count")) is not int
            or cast(int, stop["count"]) < 0
        ):
            raise ValueError(f"{journey_id} stop_condition is invalid")
        for field_name in (
            "destination_operation_id",
            "expected_rejection_reason",
            "operation_id",
            "selected_operation_id",
            "sibling_operation_id",
            "source_operation_id",
        ):
            if field_name in stop and (
                type(stop[field_name]) is not str or not stop[field_name]
            ):
                raise ValueError(
                    f"{journey_id} stop_condition.{field_name} is invalid"
                )
        for field_name in (
            "preserved_active_operation_ids",
            "preserved_operation_ids",
            "preserved_state_fields",
        ):
            if field_name in stop:
                _string_list(
                    stop[field_name],
                    label=f"{journey_id} stop_condition.{field_name}",
                )
        if (
            type(journey.get("timeout_frames")) is not int
            or cast(int, journey["timeout_frames"]) <= 0
        ):
            raise ValueError(f"{journey_id} timeout_frames must be positive")
        allowed = journey.get("allowed_nondeterminism")
        if (
            not isinstance(allowed, list)
            or len(allowed) != len(set(allowed))
            or any(
                item not in {"wall_clock_timestamp_removed"}
                for item in allowed
            )
        ):
            raise ValueError(f"{journey_id} allowed_nondeterminism is invalid")
    if seen != _REQUIRED_JOURNEY_IDS:
        raise ValueError("pre-live manifest required journey ids drifted")
    return payload


def _validate_initial_state(
    journey_id: str,
    kind: str,
    initial_state: Mapping[str, object],
) -> None:
    unexpected = set(initial_state) - _INITIAL_STATE_FIELDS
    if unexpected:
        raise ValueError(
            f"{journey_id} initial_state has unexpected fields: "
            + ", ".join(sorted(unexpected))
        )
    units = initial_state.get("units")
    if not isinstance(units, Mapping) and units != "all_terran_families":
        raise ValueError(f"{journey_id} initial_state.units is unsupported")
    if isinstance(units, Mapping) and any(
        not isinstance(unit_type, str)
        or not unit_type
        or type(count) is not int
        or count < 0
        for unit_type, count in units.items()
    ):
        raise ValueError(f"{journey_id} initial unit counts are invalid")
    if "structures" in initial_state:
        _string_list(
            initial_state["structures"],
            label=f"{journey_id} initial structures",
        )
    protected = initial_state.get("protected_minimum", {})
    if not isinstance(protected, Mapping) or any(
        not isinstance(unit_type, str)
        or not unit_type
        or type(count) is not int
        or count < 0
        for unit_type, count in protected.items()
    ):
        raise ValueError(f"{journey_id} protected_minimum is invalid")
    for field_name in ("minerals", "vespene", "event_cursor"):
        if field_name in initial_state and (
            type(initial_state[field_name]) is not int
            or cast(int, initial_state[field_name]) < 0
        ):
            raise ValueError(
                f"{journey_id} initial_state.{field_name} must be a "
                "non-negative int"
            )
    for field_name in ("voice_enabled", "muted"):
        if field_name in initial_state and type(initial_state[field_name]) is not bool:
            raise ValueError(
                f"{journey_id} initial_state.{field_name} must be a bool"
            )
    if (
        "prerequisites" in initial_state
        and initial_state["prerequisites"] != "all_available"
    ):
        raise ValueError(
            f"{journey_id} initial_state.prerequisites is unsupported"
        )
    required_by_kind = {
        "shortage_wait": {"minerals", "vespene"},
        "event_reconnect": {"event_cursor"},
        "voice_identity": {"voice_enabled", "muted"},
    }
    missing = required_by_kind.get(kind, set()) - set(initial_state)
    if missing:
        raise ValueError(
            f"{journey_id} initial_state is missing: "
            + ", ".join(sorted(missing))
        )


def execute_pre_live_journeys(
    micromachine_binary: Path | str,
    manifest_path: Path | str = DEFAULT_JOURNEY_MANIFEST,
    *,
    command_runner: CommandRunner = subprocess.run,
    node_executable: Path | str | None = None,
) -> dict[str, object]:
    manifest = load_pre_live_journey_manifest(manifest_path)
    binary = _validate_micromachine_binary(micromachine_binary)
    binary_sha256 = _sha256_file(binary)
    node = _validate_node_executable(node_executable)
    node_sha256 = _sha256_file(node)
    embedded_identity = _query_embedded_build_identity(
        binary,
        expected_sha256=binary_sha256,
        command_runner=command_runner,
    )
    reports: list[dict[str, object]] = []
    artifacts: dict[str, dict[str, object]] = {}
    for spec in cast(list[dict[str, object]], manifest["journeys"]):
        execution = _JourneyExecution(spec)
        try:
            native_input = _compile_native_input(execution)
            native_output = _invoke_native_adapter(
                binary,
                native_input,
                expected_sha256=binary_sha256,
                command_runner=command_runner,
            )
            _consume_native_output(
                execution,
                native_output,
                command_runner=command_runner,
                node_executable=node,
                node_sha256=node_sha256,
            )
            _finalize_events(execution)
            execution.products["native_adapter"] = {
                "schema_version": PRE_LIVE_NATIVE_ADAPTER_SCHEMA_VERSION,
                "binary_sha256": binary_sha256,
                "embedded_build_input_identity": embedded_identity,
                "input": native_input,
                "output": native_output,
            }
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            execution.products["execution_error"] = str(exc)
            _finalize_events(execution)
        verdict = verify_pre_live_journey_events(spec, execution.events)
        blockers = [
            *cast(list[str], verdict["blockers"]),
            *_product_path_blockers(execution.products, spec=spec),
        ]
        report = {
            **verdict,
            "ok": not blockers,
            "status": "passed" if not blockers else "failed",
            "blockers": blockers,
            "product_paths": dict(execution.products),
        }
        reports.append(report)
        artifacts[execution.journey_id] = {
            "events": execution.events,
            "products": execution.products,
        }
    failures = [report["id"] for report in reports if report["ok"] is not True]
    return {
        "schema_version": PRE_LIVE_JOURNEY_REPORT_SCHEMA_VERSION,
        "suite_id": manifest["suite_id"],
        "manifest_sha256": hashlib.sha256(
            canonical_json_bytes(manifest)
        ).hexdigest(),
        "journey_count": len(reports),
        "passed_count": len(reports) - len(failures),
        "failed_count": len(failures),
        "failures": failures,
        "ok": not failures,
        "status": "passed" if not failures else "failed",
        "journeys": reports,
        "artifacts": artifacts,
        "binary_sha256": binary_sha256,
        "embedded_build_input_identity": embedded_identity,
    }


def _compile_native_input(execution: _JourneyExecution) -> dict[str, object]:
    initial_state = _expand_initial_state(execution)
    previous_operations: dict[str, dict[str, object]] = {}
    command_index = 0
    for item in cast(Sequence[Mapping[str, object]], execution.spec["ordered_inputs"]):
        frame = int(item["frame"])
        if "command_text" in item or "preset" in item:
            update_id = f"{execution.journey_id}-update-{command_index + 1}"
            update = _compile_command(
                execution,
                item,
                frame=frame,
                update_id=update_id,
            )
            execution.native_steps.append(
                {
                    "frame": frame,
                    "kind": "policy_update",
                    "update": update,
                }
            )
            current_operations = {
                str(operation["operation_id"]): operation
                for operation in _update_operations(update)
            }
            _emit_generation_changes(
                execution,
                previous_operations,
                current_operations,
                update_id=update_id,
                frame=frame,
            )
            previous_operations = current_operations
            command_index += 1
        else:
            execution.native_steps.append(
                _expand_observation_step(execution, item)
            )
    return {
        "schema_version": PRE_LIVE_NATIVE_ADAPTER_SCHEMA_VERSION,
        "journey_id": execution.journey_id,
        "initial_state": initial_state,
        "steps": execution.native_steps,
    }


def _expand_initial_state(
    execution: _JourneyExecution,
) -> dict[str, object]:
    source = cast(Mapping[str, object], execution.spec["initial_state"])
    raw_units = source.get("units", {})
    if isinstance(raw_units, Mapping):
        for unit_type, raw_count in sorted(raw_units.items()):
            if type(raw_count) is not int or raw_count < 0:
                raise ValueError("initial unit counts must be non-negative ints")
            for _ in range(raw_count):
                execution.allocate_unit(str(unit_type))
    elif raw_units == "all_terran_families":
        for row in all_terran_capability_matrix():
            for ability in cast(Sequence[object], row["abilities"]):
                caster_state = terran_ability_caster_state(ability)
                if caster_state is None:
                    raise ValueError(
                        f"ability caster state is missing: {ability}"
                    )
                execution.allocate_unit(
                    caster_state.unit_type,
                    cloak_state=caster_state.cloak_state,
                    matrix_ability=caster_state.ability,
                )
    else:
        raise ValueError("initial_state.units is unsupported")
    raw_structures = source.get("structures", ())
    structures = _string_list(raw_structures, label="initial structures")
    if source.get("prerequisites") == "all_available":
        structures = sorted(
            {
                *structures,
                *{
                    str(prerequisite)
                    for row in all_terran_capability_matrix()
                    for prerequisite in cast(Sequence[object], row["prerequisites"])
                },
            }
        )
    protected = source.get("protected_minimum", {})
    if not isinstance(protected, Mapping):
        raise ValueError("protected_minimum must be an object")
    expanded: dict[str, object] = {
        "units": deepcopy(execution.initial_units),
        "structures": structures,
        "protected_minimum": {
            str(key): int(value)
            for key, value in sorted(protected.items())
        },
    }
    for field_name in (
        "minerals",
        "vespene",
        "prerequisites",
        "event_cursor",
        "voice_enabled",
        "muted",
    ):
        if field_name in source:
            expanded[field_name] = deepcopy(source[field_name])
    return expanded


def _compile_command(
    execution: _JourneyExecution,
    item: Mapping[str, object],
    *,
    frame: int,
    update_id: str,
) -> dict[str, object]:
    preset = str(item.get("preset", ""))
    command_text = str(item.get("command_text", ""))
    execution.emit(
        "command_input",
        update_id=update_id,
        stage="input",
        game_frame=frame,
        payload={"command_text": command_text, "preset": preset},
        order=0,
    )
    command_queue: Mapping[str, object] = {}
    if preset == "all_terran_matrix":
        update = _compile_all_terran_matrix_update(
            execution,
            command_text=command_text,
            frame=frame,
            update_id=update_id,
        )
    else:
        provider_output = _provider_output(preset)
        session = MicroMachineLiveTextSession(
            execution.backend,
            StaticJsonPolicyModulationProvider(provider_output),
        )
        result = session.submit_text(
            command_text,
            current_frame=frame,
            update_id=update_id,
        )
        result_dict = result.to_dict()
        execution.products.setdefault("compiler_results", [])
        cast(list[object], execution.products["compiler_results"]).append(
            result_dict
        )
        if not result.ok or result.update is None:
            raise ValueError(f"{preset} did not compile: {result_dict!r}")
        update = result.update.to_dict()
        command_queue = result.command_queue or {}
    execution.compiled_updates.append(update)
    execution.emit(
        "blackboard_update",
        update_id=update_id,
        stage="published",
        game_frame=frame,
        payload={"update": update, "command_queue": dict(command_queue)},
        order=1,
    )
    validation = validate_micromachine_blackboard_update(
        update,
        current_frame=frame,
    )
    validation_dict = validation.to_dict()
    execution.products.setdefault("bridge_validations", [])
    cast(list[object], execution.products["bridge_validations"]).append(
        validation_dict
    )
    if not validation.accepted:
        raise ValueError(
            f"published update failed bridge validation: {validation.reason}"
        )
    if preset == "all_terran_matrix":
        _emit_all_terran_lowering(execution, update, frame=frame)
    return update


def _compile_all_terran_matrix_update(
    execution: _JourneyExecution,
    *,
    command_text: str,
    frame: int,
    update_id: str,
) -> dict[str, object]:
    operations: list[dict[str, object]] = []
    ability_index = 0
    for row in all_terran_capability_matrix():
        family = str(row["family"])
        role = str(row["default_role"])
        for ability in cast(Sequence[object], row["abilities"]):
            ability_name = str(ability)
            caster_state = terran_ability_caster_state(ability_name)
            if caster_state is None:
                raise ValueError(
                    f"ability caster state is missing: {ability_name}"
                )
            operation_id = f"matrix-{family}-{ability_name}"
            provider_output = _top_level_ability_provider(
                operation_id,
                ability_name,
                caster_state.unit_type,
                role,
            )
            session = MicroMachineLiveTextSession(
                MicroMachineInMemoryBlackboard(),
                StaticJsonPolicyModulationProvider(provider_output),
            )
            ability_index += 1
            result = session.submit_text(
                command_text,
                current_frame=frame,
                update_id=f"{update_id}-ability-{ability_index}",
            )
            result_dict = result.to_dict()
            execution.products.setdefault("compiler_results", [])
            cast(list[object], execution.products["compiler_results"]).append(
                result_dict
            )
            if not result.ok or result.update is None:
                raise ValueError(
                    f"all_terran_matrix/{ability_name} did not compile: "
                    f"{result_dict!r}"
                )
            compiled_update = result.update.to_dict()
            validation = validate_micromachine_blackboard_update(
                compiled_update,
                current_frame=frame,
            )
            validation_dict = validation.to_dict()
            execution.products.setdefault("bridge_validations", [])
            cast(list[object], execution.products["bridge_validations"]).append(
                validation_dict
            )
            if not validation.accepted:
                raise ValueError(
                    "compiled all-Terran ability failed bridge validation: "
                    f"{validation.reason}"
                )
            vector = cast(Mapping[str, object], compiled_update["vector"])
            operation = {
                "operation_id": operation_id,
                "goal": operation_id.replace("-", " "),
                "command_layer": "micro",
            }
            for field_name in (
                "tactical_task",
                "scope",
                "lifetime",
                "composition_requirements",
                "unit_roles",
                "route_intent",
                "target_intent",
            ):
                operation[field_name] = deepcopy(vector[field_name])
            operations.append(operation)

    aggregate_vector = PolicyModulationVector.from_mapping(
        {
            "source": "llm",
            "goal": "all Terran family abilities",
            "command_layer": "micro",
            "operations": operations,
        }
    )
    accepted = execution.backend.publish_vector(
        aggregate_vector,
        current_frame=frame,
        update_id=update_id,
    )
    return accepted.to_dict()


def _emit_generation_changes(
    execution: _JourneyExecution,
    previous: Mapping[str, Mapping[str, object]],
    current: Mapping[str, Mapping[str, object]],
    *,
    update_id: str,
    frame: int,
) -> None:
    for operation_id, operation in current.items():
        prior = previous.get(operation_id)
        generation = int(operation.get("generation", 0) or 0)
        prior_generation = (
            int(prior.get("generation", 0) or 0) if prior is not None else 0
        )
        if prior is None or generation <= prior_generation:
            continue
        execution.emit(
            "generation_change",
            update_id=update_id,
            operation_id=operation_id,
            generation=generation,
            stage="published",
            game_frame=frame,
            payload={
                "action": _generation_change_action(prior, operation),
                "previous_generation": prior_generation,
                "generation": generation,
            },
            order=2,
        )


def _generation_change_action(
    previous: Mapping[str, object],
    current: Mapping[str, object],
) -> str:
    current_edit = current.get("operation_edit")
    if isinstance(current_edit, Mapping) and current_edit.get("action"):
        return str(current_edit["action"])
    previous_task = cast(Mapping[str, object], previous.get("tactical_task", {}))
    current_task = cast(Mapping[str, object], current.get("tactical_task", {}))
    if previous_task.get("location_intent") != current_task.get("location_intent"):
        return "retarget"
    if previous_task.get("max_units") != current_task.get("max_units"):
        return "reinforce"
    return "update"


def _emit_all_terran_lowering(
    execution: _JourneyExecution,
    update: Mapping[str, object],
    *,
    frame: int,
) -> None:
    for operation in _update_operations(update):
        task = cast(Mapping[str, object], operation.get("tactical_task", {}))
        ability = str(task.get("ability", ""))
        requirements = _operation_requirements(operation)
        if not ability or not requirements:
            continue
        unit_type = str(requirements[0]["unit_type"])
        family = canonical_terran_unit_family(unit_type)
        matrix_row = next(
            (
                row
                for row in all_terran_capability_matrix()
                if row["family"] == family
            ),
            None,
        )
        alias = (
            str(cast(Sequence[object], matrix_row["aliases"])[0])
            if matrix_row is not None
            else unit_type
        )
        intents = lower_terran_natural_language_units(
            f"1 {alias}",
            default_count=1,
        )
        execution.emit(
            "terran_lowering",
            update_id=str(update["update_id"]),
            operation_id=str(operation["operation_id"]),
            generation=int(operation["generation"]),
            stage="parsed",
            game_frame=frame,
            payload={
                "action": f"ability:{ability}",
                "family": family,
                "ability": ability,
                "unit_type": unit_type,
                "intent_count": len(intents),
                "production_targets": list(terran_production_targets(intents)),
            },
            order=2,
        )


def _expand_observation_step(
    execution: _JourneyExecution,
    item: Mapping[str, object],
) -> dict[str, object]:
    frame = int(item["frame"])
    kind = str(item["kind"])
    if kind == "unit_observation":
        return {
            "frame": frame,
            "kind": kind,
            "units": _expand_unit_observations(execution, item),
        }
    if kind == "resource_observation":
        units: list[dict[str, object]] = []
        raw_units = item.get("units", {})
        if not isinstance(raw_units, Mapping):
            raise ValueError("resource observation units must be an object")
        for unit_type, raw_count in sorted(raw_units.items()):
            if type(raw_count) is not int or raw_count < 0:
                raise ValueError("resource observation counts are invalid")
            for _ in range(raw_count):
                units.append(execution.allocate_unit(str(unit_type)))
        expanded: dict[str, object] = {
            "frame": frame,
            "kind": kind,
            "structures": _string_list(
                item.get("structures", ()),
                label="resource observation structures",
            ),
            "units": units,
        }
        for field_name in ("minerals", "vespene"):
            if field_name in item:
                value = item[field_name]
                if type(value) is not int or value < 0:
                    raise ValueError(
                        f"resource observation {field_name} is invalid"
                    )
                expanded[field_name] = value
        return expanded
    if kind in {"base_threat_observation", "base_clear_observation"}:
        return {
            "frame": frame,
            "kind": kind,
            "base_id": str(item.get("base_id", "self_main")),
            "required_defenders": int(item.get("required_defenders", 0) or 0),
            "threat_strength": float(item.get("threat_strength", 0.0) or 0.0),
        }
    if kind == "client_reconnect":
        expanded = {
            "frame": frame,
            "kind": kind,
        }
        if "after_event_seq" in item:
            cursor = item["after_event_seq"]
            if type(cursor) is not int or cursor < 0:
                raise ValueError("client reconnect cursor is invalid")
            expanded["after_event_seq"] = cursor
        return expanded
    raise ValueError(f"unsupported journey observation kind: {kind}")


def _expand_unit_observations(
    execution: _JourneyExecution,
    item: Mapping[str, object],
) -> list[dict[str, object]]:
    if item.get("matrix_observed_abilities") is True:
        return [
            {
                "tag": int(unit["tag"]),
                "observed_abilities": [
                    execution.matrix_ability_by_tag[int(unit["tag"])]
                ],
            }
            for unit in execution.initial_units
            if int(unit["tag"]) in execution.matrix_ability_by_tag
        ]
    raw_units = item.get("units")
    if not isinstance(raw_units, Sequence) or isinstance(
        raw_units,
        (str, bytes, bytearray),
    ):
        raise ValueError("unit observation units must be an array")
    observations: list[dict[str, object]] = []
    for descriptor in raw_units:
        if not isinstance(descriptor, Mapping):
            raise ValueError("unit observation descriptor must be an object")
        unit_type = str(descriptor.get("unit_type", ""))
        raw_tags = descriptor.get("tags")
        tags = (
            [int(value) for value in cast(Sequence[object], raw_tags)]
            if isinstance(raw_tags, Sequence)
            and not isinstance(raw_tags, (str, bytes, bytearray))
            else [
                int(unit["tag"])
                for unit in execution.initial_units
                if unit["unit_type"] == unit_type
            ]
        )
        if not tags:
            raise ValueError(f"unit observation matched no units: {unit_type}")
        for tag in tags:
            row: dict[str, object] = {"tag": tag}
            for key in (
                "home_distance",
                "engaged",
                "alive",
                "complete",
                "observed_abilities",
            ):
                if key in descriptor:
                    row[key] = deepcopy(descriptor[key])
            observations.append(row)
    return observations


def _validate_micromachine_binary(path: Path | str) -> Path:
    raw = Path(path)
    if not raw.is_absolute():
        raise ValueError("MicroMachine binary path must be absolute")
    inherited_fd = _inherited_executable_fd(raw)
    if inherited_fd is not None:
        file_stat = os.fstat(inherited_fd)
        if (
            not stat.S_ISREG(file_stat.st_mode)
            or file_stat.st_mode & 0o111 == 0
        ):
            raise ValueError(
                "inherited MicroMachine descriptor must be executable"
            )
        return raw
    if raw.is_symlink():
        raise ValueError("MicroMachine binary path must not be a symlink")
    file_stat = raw.stat()
    if not stat.S_ISREG(file_stat.st_mode):
        raise ValueError("MicroMachine binary must be a regular file")
    if file_stat.st_mode & 0o111 == 0:
        raise ValueError("MicroMachine binary must be executable")
    return raw.resolve()


def _validate_node_executable(path: Path | str | None) -> Path:
    auto_discovered = path is None
    resolved = str(path) if path is not None else shutil.which("node")
    if not resolved:
        raise ValueError(
            "Node.js is required for production Tactical Radio replay"
        )
    raw = Path(resolved)
    if not raw.is_absolute():
        raise ValueError("Node.js executable path must be absolute")
    if auto_discovered:
        raw = raw.resolve(strict=True)
    inherited_fd = _inherited_executable_fd(raw)
    if inherited_fd is not None:
        file_stat = os.fstat(inherited_fd)
        if (
            not stat.S_ISREG(file_stat.st_mode)
            or file_stat.st_mode & 0o111 == 0
        ):
            raise ValueError(
                "inherited Node.js descriptor must be executable"
            )
        return raw
    if raw.is_symlink():
        raise ValueError("Node.js executable path must not be a symlink")
    file_stat = raw.stat()
    if not stat.S_ISREG(file_stat.st_mode):
        raise ValueError("Node.js executable must be a regular file")
    if file_stat.st_mode & 0o111 == 0:
        raise ValueError("Node.js executable must be executable")
    return raw.resolve()


def _inherited_executable_fd(path: Path) -> int | None:
    if path.parent != Path("/dev/fd") or not path.name.isdecimal():
        return None
    descriptor = int(path.name)
    try:
        descriptor_stat = os.fstat(descriptor)
        path_descriptor = os.open(path, os.O_RDONLY)
    except OSError as exc:
        raise ValueError(
            "inherited executable descriptor is unavailable"
        ) from exc
    try:
        path_stat = os.fstat(path_descriptor)
        if (
            descriptor_stat.st_dev,
            descriptor_stat.st_ino,
        ) != (
            path_stat.st_dev,
            path_stat.st_ino,
        ):
            raise ValueError(
                "inherited executable descriptor path changed identity"
            )
    finally:
        os.close(path_descriptor)
    return descriptor


def _run_native_command(
    binary: Path,
    argv: Sequence[str],
    *,
    expected_sha256: str,
    command_runner: CommandRunner,
    input: bytes | None = None,
) -> subprocess.CompletedProcess[Any]:
    descriptor = _inherited_executable_fd(binary)
    if descriptor is None or command_runner is not subprocess.run:
        kwargs: dict[str, object] = {
            "check": False,
            "capture_output": True,
            "text": False,
            "shell": False,
        }
        if input is not None:
            kwargs["input"] = input
        return command_runner(list(argv), **kwargs)
    return _run_inherited_native_command(
        descriptor,
        argv,
        expected_sha256=expected_sha256,
        input=input,
    )


def _run_inherited_native_command(
    descriptor: int,
    argv: Sequence[str],
    *,
    expected_sha256: str,
    input: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    source_before = _native_executable_snapshot(descriptor)
    if (
        source_before[3] <= 0
        or source_before[3] > MAX_NATIVE_EXECUTABLE_BYTES
    ):
        raise OSError("inherited native executable size is invalid")
    if sys.platform.startswith("linux"):
        return _run_linux_sealed_native_command(
            descriptor,
            argv,
            source_before=source_before,
            expected_sha256=expected_sha256,
            input=input,
        )

    with tempfile.TemporaryDirectory(
        prefix=".voi-native-exec-",
        dir=_pinned_native_execution_root(),
    ) as directory:
        execution_root = Path(directory)
        os.chmod(execution_root, 0o700)
        executable_path = execution_root / "native-executable"
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        writable_descriptor: int | None = os.open(
            executable_path,
            flags,
            0o500,
        )
        executable_descriptor: int | None = None
        process: subprocess.Popen[bytes] | None = None
        path_monitor: tuple[Any, int] | None = None
        try:
            source_digest = _copy_native_executable(
                descriptor,
                writable_descriptor,
                source_before[3],
            )
            os.fsync(writable_descriptor)
            os.fchmod(writable_descriptor, 0o500)
            if source_digest != expected_sha256:
                raise OSError(
                    "inherited native executable digest changed before exec"
                )
            source_after_copy = _native_executable_snapshot(descriptor)
            if source_after_copy != source_before:
                raise OSError(
                    "inherited native executable changed before exec"
                )
            clone_snapshot = _native_executable_snapshot(
                writable_descriptor
            )
            if (
                clone_snapshot[3] != source_before[3]
                or _sha256_descriptor(
                    writable_descriptor,
                    clone_snapshot[3],
                )
                != expected_sha256
            ):
                raise OSError(
                    "one-shot native executable differs from admitted bytes"
                )
            os.chmod(execution_root, 0o500)
            _require_descriptor_path_identity(
                writable_descriptor,
                executable_path,
            )
            read_flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                read_flags |= os.O_NOFOLLOW
            executable_descriptor = os.open(
                executable_path,
                read_flags,
            )
            _require_descriptor_path_identity(
                executable_descriptor,
                executable_path,
            )
            if (
                _native_executable_snapshot(executable_descriptor)
                != clone_snapshot
                or _sha256_descriptor(
                    executable_descriptor,
                    clone_snapshot[3],
                )
                != expected_sha256
            ):
                raise OSError(
                    "read-only native executable differs from admitted bytes"
                )
            os.close(writable_descriptor)
            writable_descriptor = None
            path_monitor = _open_native_path_monitor(
                executable_descriptor,
                execution_root,
            )
            launch_argv = [str(executable_path), *argv[1:]]
            process = subprocess.Popen(
                launch_argv,
                executable=str(executable_path),
                stdin=subprocess.PIPE if input is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                shell=False,
            )
            _require_descriptor_path_identity(
                executable_descriptor,
                executable_path,
            )
            stdout, stderr = process.communicate(input)
            if _native_path_monitor_changed(path_monitor):
                raise OSError(
                    "one-shot native executable changed during exec"
                )
            _require_descriptor_path_identity(
                executable_descriptor,
                executable_path,
            )
            if (
                _native_executable_snapshot(executable_descriptor)
                != clone_snapshot
            ):
                raise OSError(
                    "one-shot native executable changed during exec"
                )
            if _native_executable_snapshot(descriptor) != source_before:
                raise OSError(
                    "inherited native executable changed during exec"
                )
        except BaseException:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
            raise
        finally:
            _close_native_path_monitor(path_monitor)
            os.chmod(execution_root, 0o700)
            executable_path.unlink(missing_ok=True)
            if executable_descriptor is not None:
                os.close(executable_descriptor)
            if writable_descriptor is not None:
                os.close(writable_descriptor)
    return subprocess.CompletedProcess(
        list(argv),
        int(process.returncode),
        stdout,
        stderr,
    )


def _run_linux_sealed_native_command(
    source_descriptor: int,
    argv: Sequence[str],
    *,
    source_before: tuple[int, int, int, int, int],
    expected_sha256: str,
    input: bytes | None,
) -> subprocess.CompletedProcess[bytes]:
    import fcntl

    if (
        not hasattr(os, "memfd_create")
        or not Path("/proc/self/fd").is_dir()
    ):
        raise OSError("sealed descriptor-native execution is unavailable")
    mfd_cloexec = int(getattr(os, "MFD_CLOEXEC", 0x0001))
    mfd_allow_sealing = int(
        getattr(os, "MFD_ALLOW_SEALING", 0x0002)
    )
    f_add_seals = int(getattr(fcntl, "F_ADD_SEALS", 1033))
    f_get_seals = int(getattr(fcntl, "F_GET_SEALS", 1034))
    f_seal_seal = int(getattr(fcntl, "F_SEAL_SEAL", 0x0001))
    f_seal_shrink = int(getattr(fcntl, "F_SEAL_SHRINK", 0x0002))
    f_seal_grow = int(getattr(fcntl, "F_SEAL_GROW", 0x0004))
    f_seal_write = int(getattr(fcntl, "F_SEAL_WRITE", 0x0008))

    writable_descriptor: int | None = os.memfd_create(
        "voi-native-executable",
        flags=mfd_cloexec | mfd_allow_sealing,
    )
    executable_descriptor: int | None = None
    process: subprocess.Popen[bytes] | None = None
    try:
        source_digest = _copy_native_executable(
            source_descriptor,
            writable_descriptor,
            source_before[3],
        )
        os.fsync(writable_descriptor)
        os.fchmod(writable_descriptor, 0o500)
        if source_digest != expected_sha256:
            raise OSError(
                "inherited native executable digest changed before exec"
            )
        if _native_executable_snapshot(source_descriptor) != source_before:
            raise OSError(
                "inherited native executable changed before exec"
            )
        clone_snapshot = _native_executable_snapshot(writable_descriptor)
        if (
            clone_snapshot[3] != source_before[3]
            or _sha256_descriptor(
                writable_descriptor,
                clone_snapshot[3],
            )
            != expected_sha256
        ):
            raise OSError(
                "one-shot native executable differs from admitted bytes"
            )

        required_seals = (
            f_seal_grow
            | f_seal_seal
            | f_seal_shrink
            | f_seal_write
        )
        fcntl.fcntl(
            writable_descriptor,
            f_add_seals,
            required_seals,
        )
        observed_seals = int(
            fcntl.fcntl(writable_descriptor, f_get_seals)
        )
        if observed_seals & required_seals != required_seals:
            raise OSError("one-shot native executable sealing failed")

        writable_path = Path(
            f"/proc/self/fd/{writable_descriptor}"
        )
        _require_descriptor_target_identity(
            writable_descriptor,
            writable_path,
        )
        executable_descriptor = os.open(writable_path, os.O_RDONLY)
        executable_path = Path(
            f"/proc/self/fd/{executable_descriptor}"
        )
        _require_descriptor_target_identity(
            executable_descriptor,
            executable_path,
        )
        if (
            _native_executable_snapshot(executable_descriptor)
            != clone_snapshot
            or _sha256_descriptor(
                executable_descriptor,
                clone_snapshot[3],
            )
            != expected_sha256
        ):
            raise OSError(
                "read-only native executable differs from admitted bytes"
            )
        os.close(writable_descriptor)
        writable_descriptor = None

        launch_argv = [str(executable_path), *argv[1:]]
        process = subprocess.Popen(
            launch_argv,
            executable=str(executable_path),
            pass_fds=(executable_descriptor,),
            stdin=subprocess.PIPE if input is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            shell=False,
        )
        stdout, stderr = process.communicate(input)
        _require_descriptor_target_identity(
            executable_descriptor,
            executable_path,
        )
        if (
            _native_executable_snapshot(executable_descriptor)
            != clone_snapshot
        ):
            raise OSError(
                "one-shot native executable changed during exec"
            )
        if _native_executable_snapshot(source_descriptor) != source_before:
            raise OSError(
                "inherited native executable changed during exec"
            )
    except BaseException:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        raise
    finally:
        if executable_descriptor is not None:
            os.close(executable_descriptor)
        if writable_descriptor is not None:
            os.close(writable_descriptor)
    return subprocess.CompletedProcess(
        list(argv),
        int(process.returncode),
        stdout,
        stderr,
    )


def _copy_native_executable(
    source_descriptor: int,
    target_descriptor: int,
    size: int,
) -> str:
    digest = hashlib.sha256()
    offset = 0
    while offset < size:
        chunk = os.pread(
            source_descriptor,
            min(1024 * 1024, size - offset),
            offset,
        )
        if not chunk:
            raise OSError("inherited native executable is truncated")
        digest.update(chunk)
        view = memoryview(chunk)
        while view:
            written = os.write(target_descriptor, view)
            if written <= 0:
                raise OSError("one-shot native executable write failed")
            view = view[written:]
        offset += len(chunk)
    return digest.hexdigest()


def _pinned_native_execution_root() -> str | None:
    raw_root = os.environ.get(PINNED_NATIVE_EXEC_ROOT_ENV)
    if raw_root is None:
        return None
    path = Path(raw_root)
    if not path.is_absolute() or path.is_symlink():
        raise OSError("pinned native execution root is invalid")
    resolved = path.resolve(strict=True)
    if resolved != path:
        raise OSError("pinned native execution root contains a symlink")
    root_stat = resolved.stat()
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or root_stat.st_uid != os.getuid()
        or stat.S_IMODE(root_stat.st_mode) & 0o077
    ):
        raise OSError("pinned native execution root is not private")
    return str(resolved)


def _sha256_descriptor(descriptor: int, size: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    while offset < size:
        chunk = os.pread(
            descriptor,
            min(1024 * 1024, size - offset),
            offset,
        )
        if not chunk:
            raise OSError("native executable descriptor is truncated")
        digest.update(chunk)
        offset += len(chunk)
    return digest.hexdigest()


def _native_executable_snapshot(
    descriptor: int,
) -> tuple[int, int, int, int, int]:
    file_stat = os.fstat(descriptor)
    if (
        not stat.S_ISREG(file_stat.st_mode)
        or file_stat.st_mode & 0o111 == 0
    ):
        raise OSError("native executable descriptor is not executable")
    return (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_mode,
        file_stat.st_size,
        file_stat.st_mtime_ns,
    )


def _require_descriptor_path_identity(
    descriptor: int,
    path: Path,
) -> None:
    descriptor_stat = os.fstat(descriptor)
    path_stat = path.stat(follow_symlinks=False)
    if (
        descriptor_stat.st_dev,
        descriptor_stat.st_ino,
        descriptor_stat.st_mode,
        descriptor_stat.st_size,
        descriptor_stat.st_mtime_ns,
    ) != (
        path_stat.st_dev,
        path_stat.st_ino,
        path_stat.st_mode,
        path_stat.st_size,
        path_stat.st_mtime_ns,
    ):
        raise OSError("one-shot native executable path changed identity")


def _require_descriptor_target_identity(
    descriptor: int,
    path: Path,
) -> None:
    descriptor_stat = os.fstat(descriptor)
    path_stat = path.stat()
    if (
        descriptor_stat.st_dev,
        descriptor_stat.st_ino,
        descriptor_stat.st_mode,
        descriptor_stat.st_size,
        descriptor_stat.st_mtime_ns,
    ) != (
        path_stat.st_dev,
        path_stat.st_ino,
        path_stat.st_mode,
        path_stat.st_size,
        path_stat.st_mtime_ns,
    ):
        raise OSError(
            "one-shot native executable descriptor alias changed identity"
        )


def _open_native_path_monitor(
    executable_descriptor: int,
    execution_root: Path,
) -> tuple[Any, int] | None:
    required = (
        "kqueue",
        "kevent",
        "KQ_FILTER_VNODE",
        "KQ_EV_ADD",
        "KQ_EV_CLEAR",
        "KQ_NOTE_DELETE",
        "KQ_NOTE_WRITE",
        "KQ_NOTE_EXTEND",
        "KQ_NOTE_ATTRIB",
        "KQ_NOTE_LINK",
        "KQ_NOTE_RENAME",
        "KQ_NOTE_REVOKE",
    )
    if any(not hasattr(select, name) for name in required):
        return None
    directory_descriptor = os.open(execution_root, os.O_RDONLY)
    queue = select.kqueue()
    event_flags = select.KQ_EV_ADD | select.KQ_EV_CLEAR
    executable_vnode_flags = (
        select.KQ_NOTE_DELETE
        | select.KQ_NOTE_WRITE
        | select.KQ_NOTE_EXTEND
        | select.KQ_NOTE_LINK
        | select.KQ_NOTE_RENAME
        | select.KQ_NOTE_REVOKE
    )
    directory_vnode_flags = (
        executable_vnode_flags
        | select.KQ_NOTE_ATTRIB
    )
    try:
        queue.control(
            [
                select.kevent(
                    executable_descriptor,
                    filter=select.KQ_FILTER_VNODE,
                    flags=event_flags,
                    fflags=executable_vnode_flags,
                ),
                select.kevent(
                    directory_descriptor,
                    filter=select.KQ_FILTER_VNODE,
                    flags=event_flags,
                    fflags=directory_vnode_flags,
                ),
            ],
            0,
            0,
        )
    except BaseException:
        queue.close()
        os.close(directory_descriptor)
        raise
    return queue, directory_descriptor


def _native_path_monitor_changed(
    monitor: tuple[Any, int] | None,
) -> bool:
    if monitor is None:
        return False
    queue, _ = monitor
    return bool(queue.control(None, 2, 0))


def _close_native_path_monitor(
    monitor: tuple[Any, int] | None,
) -> None:
    if monitor is None:
        return
    queue, directory_descriptor = monitor
    queue.close()
    os.close(directory_descriptor)


def _query_embedded_build_identity(
    binary: Path,
    *,
    expected_sha256: str,
    command_runner: CommandRunner,
) -> str:
    completed = _run_native_command(
        binary,
        [str(binary), "--voi-build-input-identity"],
        expected_sha256=expected_sha256,
        command_runner=command_runner,
    )
    if int(completed.returncode) != 0:
        raise ValueError("MicroMachine build-input identity query failed")
    identity = _as_bytes(completed.stdout).decode("utf-8").strip()
    if (
        not identity.startswith(_SHA256_IDENTITY_PREFIX)
        or len(identity) != len(_SHA256_IDENTITY_PREFIX) + 64
        or any(character not in "0123456789abcdef" for character in identity[7:])
    ):
        raise ValueError("MicroMachine embedded build-input identity is invalid")
    if _sha256_file(binary) != expected_sha256:
        raise ValueError("MicroMachine binary changed during identity query")
    return identity


def _invoke_native_adapter(
    binary: Path,
    native_input: Mapping[str, object],
    *,
    expected_sha256: str,
    command_runner: CommandRunner,
) -> dict[str, object]:
    completed = _run_native_command(
        binary,
        [
            str(binary),
            "--voi-pre-live-journey-adapter",
            "-",
        ],
        expected_sha256=expected_sha256,
        command_runner=command_runner,
        input=canonical_json_bytes(native_input),
    )
    if int(completed.returncode) != 0:
        stderr = _as_bytes(completed.stderr).decode("utf-8", errors="replace")
        raise ValueError(f"native pre-live adapter failed: {stderr.strip()}")
    stdout = _as_bytes(completed.stdout)
    if not stdout or len(stdout) > MAX_NATIVE_ADAPTER_OUTPUT_BYTES:
        raise ValueError("native pre-live adapter output size is invalid")
    output = json.loads(
        stdout,
        object_pairs_hook=_reject_duplicate_json_object_keys,
    )
    output = _validate_native_output_payload(
        output,
        expected_input=native_input,
    )
    if _sha256_file(binary) != expected_sha256:
        raise ValueError("MicroMachine binary changed during adapter execution")
    return output


def _validate_native_output_payload(
    output: object,
    *,
    expected_input: Mapping[str, object] | None = None,
) -> dict[str, object]:
    if not isinstance(output, dict) or set(output) != _NATIVE_OUTPUT_FIELDS:
        raise ValueError("native pre-live adapter output field set is invalid")
    schema_version = output.get("schema_version")
    if (
        type(schema_version) is not int
        or schema_version != PRE_LIVE_NATIVE_ADAPTER_SCHEMA_VERSION
    ):
        raise ValueError("native pre-live adapter schema is unsupported")
    _validate_native_final_state(output)
    if expected_input is not None:
        _validate_native_lifecycle_sequence(
            output.get("events"),
            validate_causality=False,
        )
        _validate_native_compiler_identity_bindings(output, expected_input)
        _validate_native_reconnect_cursor(output, expected_input)
    _validate_native_lifecycle_sequence(output.get("events"))
    _validate_native_production_path(
        output,
        expected_input=expected_input,
    )
    return output


def _validate_native_final_state(output: Mapping[str, object]) -> None:
    snapshots = output.get("snapshots")
    final_state = output.get("final_state")
    if (
        not isinstance(snapshots, Sequence)
        or isinstance(snapshots, (str, bytes, bytearray))
        or not snapshots
        or not isinstance(snapshots[-1], Mapping)
        or not isinstance(cast(Mapping[str, object], snapshots[-1]).get("state"), Mapping)
        or not isinstance(final_state, Mapping)
    ):
        raise ValueError("native terminal snapshot is malformed")
    terminal_state = cast(
        Mapping[str, object],
        cast(Mapping[str, object], snapshots[-1])["state"],
    )
    if canonical_json_bytes(final_state) != canonical_json_bytes(terminal_state):
        raise ValueError("native final_state does not match the terminal snapshot")


def _validate_native_reconnect_cursor(
    output: Mapping[str, object],
    expected_input: Mapping[str, object],
) -> None:
    initial_state = expected_input.get("initial_state")
    if not isinstance(initial_state, Mapping) or "event_cursor" not in initial_state:
        return
    expected_cursor = initial_state.get("event_cursor")
    if type(expected_cursor) is not int or expected_cursor < 0:
        raise ValueError("manifest reconnect cursor is invalid")
    reconnects = [
        event
        for event in _mapping_sequence(output.get("events"))
        if event.get("event_type") == "client_reconnect"
    ]
    final_state = output.get("final_state")
    if (
        len(reconnects) != 1
        or not isinstance(reconnects[0].get("payload"), Mapping)
        or cast(Mapping[str, object], reconnects[0]["payload"]).get(
            "after_event_seq"
        )
        != expected_cursor
        or not isinstance(final_state, Mapping)
        or final_state.get("event_cursor") != expected_cursor
    ):
        raise ValueError(
            "native reconnect cursor does not match the manifest initial cursor"
        )


def _validate_native_lifecycle_sequence(
    events: object,
    *,
    validate_causality: bool = True,
) -> None:
    if not isinstance(events, Sequence) or isinstance(
        events,
        (str, bytes, bytearray),
    ):
        raise ValueError("native event stream is malformed")
    canonical = [
        event
        for event in events
        if isinstance(event, Mapping)
    ]
    if len(canonical) != len(events):
        raise ValueError("native event stream contains a non-object event")
    frames: list[int] = []
    ownership: dict[tuple[str, str, int], int] = {}
    admission: dict[tuple[str, str, int], int] = {}
    preemption: dict[tuple[str, str, int], int] = {}
    squad_orders: dict[tuple[str, str, int, str], int] = {}
    submissions: dict[tuple[str, str, int, str], int] = {}
    for expected_seq, event in enumerate(canonical, start=1):
        if set(event) != _RAW_EVENT_FIELDS:
            raise ValueError("native lifecycle event field set is invalid")
        seq = event.get("seq")
        if type(seq) is not int or seq != expected_seq:
            raise ValueError("native event sequence is not contiguous")
        identity = event.get("identity")
        payload = event.get("payload")
        if not isinstance(identity, Mapping) or not isinstance(payload, Mapping):
            raise ValueError("native lifecycle event is malformed")
        if set(identity) != _RAW_EVENT_IDENTITY_FIELDS:
            raise ValueError("native lifecycle identity field set is invalid")
        update_id = identity.get("update_id")
        operation_id = identity.get("operation_id")
        generation = identity.get("generation")
        stage = identity.get("stage")
        frame = identity.get("game_frame")
        if (
            not isinstance(update_id, str)
            or not isinstance(operation_id, str)
            or type(generation) is not int
            or generation < 0
            or not isinstance(stage, str)
            or not stage
            or type(frame) is not int
            or frame < 0
        ):
            raise ValueError("native lifecycle identity is malformed")
        frames.append(frame)
        identity_key = (update_id, operation_id, generation)
        action_key = (*identity_key, str(payload.get("action", "") or ""))
        event_type = event.get("event_type")
        if (
            not isinstance(event_type, str)
            or event_type not in _NATIVE_EVENT_TYPES
        ):
            raise ValueError("native lifecycle event type is invalid")
        index = expected_seq - 1
        if not validate_causality:
            continue
        if (
            event_type == "launch_decision"
            and identity.get("stage") == "launch_admitted"
        ):
            admission[identity_key] = index
        elif event_type == "ownership_snapshot":
            if admission.get(identity_key, index) >= index:
                raise ValueError("native ownership predates launch admission")
            ownership[identity_key] = index
        elif event_type == "preemption":
            preemption[identity_key] = index
        elif event_type == "squad_order":
            if max(
                ownership.get(identity_key, -1),
                preemption.get(identity_key, -1),
            ) >= index:
                raise ValueError("native Squad order predates ownership")
            if identity_key not in ownership and identity_key not in preemption:
                raise ValueError("native Squad order lacks prior ownership")
            squad_orders[action_key] = index
        elif event_type == "submission":
            if squad_orders.get(action_key, index) >= index:
                raise ValueError("native submission predates Squad order")
            submissions[action_key] = index
        elif event_type in _EFFECT_EVENT_TYPES:
            if submissions.get(action_key, index) >= index:
                raise ValueError("native effect predates submission")
    if frames != sorted(frames):
        raise ValueError("native lifecycle frames are regressive")


def _validate_native_compiler_identity_bindings(
    output: Mapping[str, object],
    native_input: Mapping[str, object],
) -> None:
    allowed = _compiler_requested_operation_identities(native_input)
    caster_unit_types = _compiler_requested_caster_unit_types(native_input)
    identities: list[tuple[str, str, int]] = []
    for event in _mapping_sequence(output.get("events")):
        identity = event.get("identity")
        if not isinstance(identity, Mapping):
            raise ValueError("native event identity is malformed")
        if str(identity.get("operation_id", "") or ""):
            identity_key = _native_identity_tuple(identity)
            identities.append(identity_key)
            payload = event.get("payload")
            event_type = event.get("event_type")
            is_sc2_receipt = (
                event_type == "production_path_receipt"
                and isinstance(payload, Mapping)
                and payload.get("entrypoint")
                == "voiProductionSubmitSc2Action"
            )
            if event_type in {
                "submission",
                *_EFFECT_EVENT_TYPES,
            } or is_sc2_receipt:
                if (
                    not isinstance(payload, Mapping)
                    or payload.get("unit_type")
                    != caster_unit_types.get(identity_key)
                ):
                    raise ValueError(
                        "native SC2 caster unit type is not compiler-bound"
                    )
    for row in _mapping_sequence(output.get("operation_director")):
        policy_update_id = row.get("policy_update_id")
        operation_id = row.get("operation_id")
        generation = row.get("generation")
        if (
            not isinstance(policy_update_id, str)
            or not isinstance(operation_id, str)
            or type(generation) is not int
            or generation < 0
        ):
            raise ValueError("native operation director identity is invalid")
        identities.append((policy_update_id, operation_id, generation))
        for evidence in _mapping_sequence(row.get("family_evidence")):
            identities.append(_native_identity_tuple(evidence))
    production_path = output.get("production_path")
    if isinstance(production_path, Mapping):
        for field_name in (
            "applied_squad_orders",
            "squad_order_receipts",
        ):
            identities.extend(
                _native_identity_tuple(row)
                for row in _mapping_sequence(production_path.get(field_name))
            )
        for field_name in (
            "dispatched_sc2_actions",
            "sc2_submission_receipts",
        ):
            for row in _mapping_sequence(production_path.get(field_name)):
                identity_key = _native_identity_tuple(row)
                identities.append(identity_key)
                if row.get("unit_type") != caster_unit_types.get(identity_key):
                    raise ValueError(
                        "native SC2 caster unit type is not compiler-bound"
                    )
    if any(identity not in allowed for identity in identities):
        raise ValueError(
            "native evidence is not bound to a compiler-requested operation identity"
        )


def _native_identity_tuple(
    identity: Mapping[str, object],
) -> tuple[str, str, int]:
    update_id = identity.get("update_id")
    operation_id = identity.get("operation_id")
    generation = identity.get("generation")
    if (
        not isinstance(update_id, str)
        or not isinstance(operation_id, str)
        or type(generation) is not int
        or generation < 0
    ):
        raise ValueError("native operation identity is malformed")
    return update_id, operation_id, generation


def _compiler_requested_operation_identities(
    native_input: Mapping[str, object],
) -> set[tuple[str, str, int]]:
    steps = native_input.get("steps")
    if not isinstance(steps, Sequence) or isinstance(
        steps,
        (str, bytes, bytearray),
    ):
        raise ValueError("native input steps are malformed")
    allowed: set[tuple[str, str, int]] = set()
    active: dict[str, int] = {}
    for step in steps:
        if not isinstance(step, Mapping) or step.get("kind") != "policy_update":
            continue
        update = step.get("update")
        if not isinstance(update, Mapping):
            raise ValueError("native policy update is malformed")
        update_id = str(update.get("update_id", "") or "")
        operations = _update_operations(update)
        if operations:
            active = {
                str(operation.get("operation_id", "") or ""): int(
                    operation.get("generation", 0) or 0
                )
                for operation in operations
            }
            requested = active
        else:
            vector = update.get("vector")
            emergency = (
                vector.get("emergency")
                if isinstance(vector, Mapping)
                else None
            )
            requested = (
                active
                if isinstance(emergency, Mapping)
                and any(value is True for value in emergency.values())
                else {}
            )
        for operation_id, generation in requested.items():
            if not update_id or not operation_id or generation <= 0:
                raise ValueError("compiler-requested operation identity is malformed")
            allowed.add((update_id, operation_id, generation))
    return allowed


def _compiler_requested_caster_unit_types(
    native_input: Mapping[str, object],
) -> dict[tuple[str, str, int], str]:
    steps = native_input.get("steps")
    if not isinstance(steps, Sequence) or isinstance(
        steps,
        (str, bytes, bytearray),
    ):
        raise ValueError("native input steps are malformed")
    caster_unit_types: dict[tuple[str, str, int], str] = {}
    active: dict[str, tuple[int, str]] = {}
    for step in steps:
        if not isinstance(step, Mapping) or step.get("kind") != "policy_update":
            continue
        update = step.get("update")
        if not isinstance(update, Mapping):
            raise ValueError("native policy update is malformed")
        update_id = str(update.get("update_id", "") or "")
        operations = _update_operations(update)
        if operations:
            requested: dict[str, tuple[int, str]] = {}
            for operation in operations:
                requirements = _operation_requirements(operation)
                if not requirements:
                    raise ValueError(
                        "compiler-requested operation lacks a caster unit type"
                    )
                operation_id = operation.get("operation_id")
                generation = operation.get("generation")
                unit_type = requirements[0].get("unit_type")
                if (
                    type(operation_id) is not str
                    or not operation_id
                    or type(generation) is not int
                    or generation <= 0
                    or type(unit_type) is not str
                    or not unit_type
                ):
                    raise ValueError(
                        "compiler-requested caster unit type is malformed"
                    )
                requested[operation_id] = (generation, unit_type)
            active = requested
        else:
            vector = update.get("vector")
            emergency = (
                vector.get("emergency")
                if isinstance(vector, Mapping)
                else None
            )
            requested = (
                active
                if isinstance(emergency, Mapping)
                and any(value is True for value in emergency.values())
                else {}
            )
        for operation_id, (generation, unit_type) in requested.items():
            identity = (update_id, operation_id, generation)
            if not update_id:
                raise ValueError(
                    "compiler-requested caster unit type is malformed"
                )
            prior = caster_unit_types.get(identity)
            if prior is not None and prior != unit_type:
                raise ValueError(
                    "compiler-requested caster unit type is ambiguous"
                )
            caster_unit_types[identity] = unit_type
    return caster_unit_types


def _compiled_operation_action(operation: Mapping[str, object]) -> str:
    task = operation.get("tactical_task")
    if not isinstance(task, Mapping):
        raise ValueError("compiled operation tactical task is malformed")
    ability = task.get("ability")
    task_type = task.get("task_type")
    if type(ability) is not str or type(task_type) is not str or not task_type:
        raise ValueError("compiled operation action is malformed")
    if ability:
        return f"ability:{ability}"
    return {
        "scout_with_units": "squad_order:scout",
        "defend_with_units": "squad_order:defend",
        "harass_with_units": "squad_order:harass",
    }.get(task_type, "squad_order:attack")


def _validate_native_production_path(
    output: Mapping[str, object],
    *,
    expected_input: Mapping[str, object] | None = None,
) -> None:
    production_path = output.get("production_path")
    if (
        not isinstance(production_path, Mapping)
        or set(production_path) != _NATIVE_PRODUCTION_PATH_FIELDS
    ):
        raise ValueError("native production path field set is invalid")
    concrete_paths = {
        "executor_kind": "micromachine_concrete_pre_live",
        "squad_order_execution_path": "Squad::setSquadOrder",
        "sc2_submission_execution_path": (
            "Micro::*->CCBot::Actions()->UnitCommand"
        ),
    }
    if any(
        production_path.get(field_name) != expected
        for field_name, expected in concrete_paths.items()
    ):
        raise ValueError("native production executor is not concrete")
    observed = dict.fromkeys(_NATIVE_PRODUCTION_ENTRYPOINTS.values(), 0)
    events = output.get("events")
    if not isinstance(events, Sequence) or isinstance(
        events,
        (str, bytes, bytearray),
    ):
        raise ValueError("native production path lacks event evidence")
    receipt_events: dict[str, list[dict[str, object]]] = {
        "voiProductionAssignOperationOwner": [],
        "voiProductionIssueSquadOrder": [],
        "voiProductionSubmitSc2Action": [],
    }
    for event in events:
        if not isinstance(event, Mapping):
            raise ValueError("native production path event is malformed")
        if event.get("event_type") != "production_path_receipt":
            continue
        identity = event.get("identity")
        payload = event.get("payload")
        if (
            not isinstance(identity, Mapping)
            or identity.get("stage") != "accepted"
            or not isinstance(payload, Mapping)
        ):
            raise ValueError("native production path receipt is malformed")
        entrypoint = str(payload.get("entrypoint", ""))
        if entrypoint not in observed:
            raise ValueError("native production path receipt is unsupported")
        if entrypoint == "voiProductionAssignOperationOwner":
            _validate_native_ownership_receipt_event(event)
            receipt_events[entrypoint].append(dict(event))
        elif entrypoint == "voiProductionIssueSquadOrder":
            _validate_native_squad_receipt_payload(
                payload,
                identity=identity,
                event_payload=True,
            )
            receipt_events[entrypoint].append(dict(payload))
        else:
            _validate_native_submission_receipt_payload(
                payload,
                identity=identity,
                event_payload=True,
            )
            receipt_events[entrypoint].append(dict(payload))
        observed[entrypoint] += 1
    for count_field, entrypoint in _NATIVE_PRODUCTION_ENTRYPOINTS.items():
        entrypoint_field = _NATIVE_PRODUCTION_ENTRYPOINT_FIELDS[count_field]
        if production_path.get(entrypoint_field) != entrypoint:
            raise ValueError("native production path entrypoint is invalid")
        declared = production_path.get(count_field)
        if type(declared) is not int or declared < 0:
            raise ValueError("native production path receipt count is invalid")
        if declared != observed[entrypoint]:
            raise ValueError(
                "native production path receipt count does not match raw events"
            )
    squad_receipts = _native_production_rows(
        production_path,
        "squad_order_receipts",
    )
    applied_orders = _native_production_rows(
        production_path,
        "applied_squad_orders",
    )
    submission_receipts = _native_production_rows(
        production_path,
        "sc2_submission_receipts",
    )
    dispatched_actions = _native_production_rows(
        production_path,
        "dispatched_sc2_actions",
    )
    if len(squad_receipts) != production_path["squad_order_receipt_count"]:
        raise ValueError("native squad-order receipt rows do not match count")
    if len(applied_orders) != len(squad_receipts):
        raise ValueError("native applied Squad orders do not match receipts")
    if len(submission_receipts) != production_path["sc2_submission_receipt_count"]:
        raise ValueError("native SC2 receipt rows do not match count")
    if len(dispatched_actions) != len(submission_receipts):
        raise ValueError("native dispatched SC2 actions do not match receipts")
    squad_ids: set[str] = set()
    for receipt in squad_receipts:
        _validate_native_squad_receipt_payload(
            receipt,
            identity=receipt,
            event_payload=False,
        )
        receipt_id = str(receipt["receipt_id"])
        if receipt_id in squad_ids:
            raise ValueError("native squad-order receipt id is duplicated")
        squad_ids.add(receipt_id)
        event_receipt = _unique_native_row(
            receipt_events["voiProductionIssueSquadOrder"],
            "receipt_id",
            receipt_id,
        )
        applied = _unique_native_row(
            applied_orders,
            "receipt_id",
            receipt_id,
        )
        _validate_native_applied_squad_order_payload(applied)
        _require_native_binding_fields(
            receipt,
            event_receipt,
            (
                "receipt_id",
                "update_id",
                "operation_id",
                "generation",
                "action",
                "squad_order_type",
                "target_x",
                "target_y",
                "radius",
                "unit_tags",
                "applied_proof",
                "membership_proof",
            ),
        )
        _require_native_binding_fields(
            receipt,
            applied,
            (
                "receipt_id",
                "update_id",
                "operation_id",
                "generation",
                "squad_name",
                "action",
                "squad_order_type",
                "target_x",
                "target_y",
                "radius",
                "unit_tags",
            ),
        )
    submission_ids: set[str] = set()
    for receipt in submission_receipts:
        _validate_native_submission_receipt_payload(
            receipt,
            identity=receipt,
            event_payload=False,
        )
        submission_id = str(receipt["submission_id"])
        if submission_id in submission_ids:
            raise ValueError("native SC2 submission id is duplicated")
        submission_ids.add(submission_id)
        event_receipt = _unique_native_row(
            receipt_events["voiProductionSubmitSc2Action"],
            "submission_id",
            submission_id,
        )
        dispatched = _unique_native_row(
            dispatched_actions,
            "submission_id",
            submission_id,
        )
        _validate_native_dispatched_sc2_action_payload(dispatched)
        _require_native_binding_fields(
            receipt,
            event_receipt,
            (
                "submission_id",
                "update_id",
                "operation_id",
                "generation",
                "action",
                "unit_type",
                "dispatch_action",
                "ability_name",
                "ability_id",
                "target_kind",
                "target_x",
                "target_y",
                "cloak_state",
                "unit_tags",
                "dispatch_proof",
            ),
        )
        _require_native_binding_fields(
            receipt,
            dispatched,
            (
                "submission_id",
                "update_id",
                "operation_id",
                "generation",
                "action",
                "unit_type",
                "dispatch_action",
                "ability_name",
                "ability_id",
                "target_kind",
                "target_x",
                "target_y",
                "cloak_state",
                "unit_tags",
            ),
        )
    _validate_native_ownership_causality(
        events,
        receipt_events["voiProductionAssignOperationOwner"],
    )
    _validate_native_submission_causality(events, squad_receipts, submission_receipts)
    _validate_native_family_effect_causality(
        events,
        output.get("operation_director"),
        require_submitted_effects=(
            expected_input is not None
            and expected_input.get("journey_id")
            == "all_terran_family_ability_blocker_matrix"
        ),
    )


def _validate_native_applied_squad_order_payload(
    payload: Mapping[str, object],
) -> None:
    expected = {
        "receipt_id",
        "update_id",
        "operation_id",
        "generation",
        "squad_name",
        "action",
        "squad_order_type",
        "target_x",
        "target_y",
        "radius",
        "unit_tags",
    }
    if set(payload) != expected:
        raise ValueError("native applied Squad order field set is invalid")
    if (
        not str(payload.get("receipt_id", "") or "")
        or not str(payload.get("update_id", "") or "")
        or not str(payload.get("operation_id", "") or "")
        or type(payload.get("generation")) is not int
        or cast(int, payload["generation"]) <= 0
        or not str(payload.get("squad_name", "") or "")
        or not str(payload.get("action", "") or "")
        or type(payload.get("squad_order_type")) is not int
        or cast(int, payload["squad_order_type"]) < 0
        or not _native_finite_number(payload.get("target_x"))
        or not _native_finite_number(payload.get("target_y"))
        or not _native_finite_number(payload.get("radius"), minimum=0.0)
    ):
        raise ValueError("native applied Squad order is malformed")
    _native_unit_tags(payload.get("unit_tags"))


def _validate_native_dispatched_sc2_action_payload(
    payload: Mapping[str, object],
) -> None:
    expected = {
        "submission_id",
        "update_id",
        "operation_id",
        "generation",
        "action",
        "unit_type",
        "dispatch_action",
        "ability_name",
        "ability_id",
        "target_kind",
        "target_x",
        "target_y",
        "cloak_state",
        "unit_tags",
    }
    if set(payload) != expected:
        raise ValueError("native dispatched SC2 action field set is invalid")
    if (
        not str(payload.get("submission_id", "") or "")
        or not str(payload.get("update_id", "") or "")
        or not str(payload.get("operation_id", "") or "")
        or type(payload.get("generation")) is not int
        or cast(int, payload["generation"]) <= 0
        or not str(payload.get("action", "") or "")
    ):
        raise ValueError("native dispatched SC2 action is malformed")
    _native_action_metadata(payload)
    _native_unit_tags(payload.get("unit_tags"))


def _native_production_rows(
    production_path: Mapping[str, object],
    field_name: str,
) -> list[dict[str, object]]:
    value = production_path.get(field_name)
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise ValueError(f"native production path {field_name} is not an array")
    if any(not isinstance(row, Mapping) for row in value):
        raise ValueError(f"native production path {field_name} has malformed rows")
    return [dict(cast(Mapping[str, object], row)) for row in value]


def _validate_native_squad_receipt_payload(
    payload: Mapping[str, object],
    *,
    identity: Mapping[str, object],
    event_payload: bool,
) -> None:
    expected = {
        "entrypoint",
        "receipt_id",
        "update_id",
        "operation_id",
        "generation",
        "action",
        "squad_order_type",
        "target_x",
        "target_y",
        "radius",
        "unit_tags",
        "applied_proof",
        "membership_proof",
    }
    if not event_payload:
        expected = {
            "receipt_id",
            "update_id",
            "operation_id",
            "generation",
            "squad_name",
            "action",
            "squad_order_type",
            "target_x",
            "target_y",
            "radius",
            "unit_tags",
            "callback_executed",
            "applied_proof",
            "membership_proof",
        }
    if set(payload) != expected:
        raise ValueError("native squad-order receipt field set is invalid")
    binding = _native_receipt_binding(payload, identity)
    if payload.get("applied_proof") is not True:
        raise ValueError("native squad-order receipt lacks applied proof")
    if payload.get("membership_proof") is not True:
        raise ValueError("native squad-order receipt lacks membership proof")
    if not event_payload and payload.get("callback_executed") is not True:
        raise ValueError("native squad-order callback was not executed")
    if not event_payload and not str(payload.get("squad_name", "") or ""):
        raise ValueError("native squad-order receipt lacks squad name")
    if (
        type(payload.get("squad_order_type")) is not int
        or cast(int, payload["squad_order_type"]) < 0
        or not _native_finite_number(payload.get("target_x"))
        or not _native_finite_number(payload.get("target_y"))
        or not _native_finite_number(payload.get("radius"), minimum=0.0)
    ):
        raise ValueError("native squad-order receipt metadata is invalid")
    expected_id = _production_receipt_id(
        "voi-squad-order",
        binding,
        dispatch_action="",
    )
    if payload.get("receipt_id") != expected_id:
        raise ValueError("native squad-order receipt id is invalid")


def _validate_native_submission_receipt_payload(
    payload: Mapping[str, object],
    *,
    identity: Mapping[str, object],
    event_payload: bool,
) -> None:
    expected = {
        "entrypoint",
        "submission_id",
        "update_id",
        "operation_id",
        "generation",
        "action",
        "unit_type",
        "dispatch_action",
        "ability_name",
        "ability_id",
        "target_kind",
        "target_x",
        "target_y",
        "cloak_state",
        "unit_tags",
        "dispatch_proof",
    }
    if not event_payload:
        expected = {
            "submission_id",
            "update_id",
            "operation_id",
            "generation",
            "action",
            "unit_type",
            "dispatch_action",
            "ability_name",
            "ability_id",
            "target_kind",
            "target_x",
            "target_y",
            "cloak_state",
            "unit_tags",
            "callback_executed",
            "dispatch_proof",
        }
    if set(payload) != expected:
        raise ValueError("native SC2 submission receipt field set is invalid")
    binding = _native_receipt_binding(payload, identity)
    dispatch_action = _native_action_metadata(payload)[1]
    if payload.get("dispatch_proof") is not True:
        raise ValueError("native SC2 submission receipt lacks dispatch proof")
    if not event_payload and payload.get("callback_executed") is not True:
        raise ValueError("native SC2 submission callback was not executed")
    expected_id = _production_receipt_id(
        "voi-sc2-submission",
        binding,
        dispatch_action=dispatch_action,
        action_metadata=_native_action_metadata(payload),
    )
    if payload.get("submission_id") != expected_id:
        raise ValueError("native SC2 submission id is invalid")


def _native_receipt_binding(
    payload: Mapping[str, object],
    identity: Mapping[str, object],
) -> tuple[str, str, int, str, tuple[int, ...]]:
    update_id = str(payload.get("update_id", "") or "")
    operation_id = str(payload.get("operation_id", "") or "")
    generation = payload.get("generation")
    action = str(payload.get("action", "") or "")
    unit_tags = _native_unit_tags(payload.get("unit_tags"))
    if (
        not update_id
        or not operation_id
        or type(generation) is not int
        or generation <= 0
        or not action
        or update_id != str(identity.get("update_id", "") or "")
        or operation_id != str(identity.get("operation_id", "") or "")
        or generation != identity.get("generation")
    ):
        raise ValueError("native production receipt identity is invalid")
    return update_id, operation_id, generation, action, unit_tags


def _native_finite_number(
    value: object,
    *,
    minimum: float | None = None,
) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    numeric = float(value)
    return math.isfinite(numeric) and (
        minimum is None or numeric >= minimum
    )


def _native_action_metadata(
    payload: Mapping[str, object],
) -> tuple[str, str, str, int, str, object, object, str]:
    unit_type = str(payload.get("unit_type", "") or "")
    dispatch_action = str(payload.get("dispatch_action", "") or "")
    ability_name = str(payload.get("ability_name", "") or "")
    ability_id = payload.get("ability_id")
    target_kind = str(payload.get("target_kind", "") or "")
    target_x = payload.get("target_x")
    target_y = payload.get("target_y")
    cloak_state = payload.get("cloak_state")
    supported_dispatches = {
        "attack_move",
        "move",
        "ability",
        "ability_position",
        "ability_target",
    }
    ability_dispatches = {
        "ability",
        "ability_position",
        "ability_target",
    }
    if (
        not unit_type
        or dispatch_action not in supported_dispatches
        or type(ability_id) is not int
        or ability_id < 0
        or not _native_finite_number(target_x)
        or not _native_finite_number(target_y)
        or type(cloak_state) is not str
        or cloak_state not in {"", "not_cloaked", "cloaked", "unknown"}
    ):
        raise ValueError("native SC2 action metadata is invalid")
    if dispatch_action in ability_dispatches:
        if not ability_name or ability_id <= 0 or not target_kind:
            raise ValueError("native SC2 ability metadata is invalid")
        if dispatch_action == "ability" and target_kind != "none":
            raise ValueError("native SC2 no-target ability metadata is invalid")
        if dispatch_action != "ability" and target_kind == "none":
            raise ValueError("native SC2 targeted ability metadata is invalid")
    elif ability_name or ability_id != 0 or target_kind:
        raise ValueError("native non-ability action has ability metadata")
    return (
        unit_type,
        dispatch_action,
        ability_name,
        ability_id,
        target_kind,
        target_x,
        target_y,
        cloak_state,
    )


def _native_unit_tags(value: object) -> tuple[int, ...]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise ValueError("native production unit tags are malformed")
    tags = tuple(value)
    if (
        not tags
        or any(type(tag) is not int or tag <= 0 for tag in tags)
        or tuple(sorted(tags)) != tags
        or len(set(tags)) != len(tags)
    ):
        raise ValueError("native production unit tags are invalid")
    return cast(tuple[int, ...], tags)


def _native_optional_unit_tags(value: object) -> tuple[int, ...]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise ValueError("native production unit tags are malformed")
    tags = tuple(value)
    if (
        any(type(tag) is not int or tag <= 0 for tag in tags)
        or tuple(sorted(tags)) != tags
        or len(set(tags)) != len(tags)
    ):
        raise ValueError("native production unit tags are invalid")
    return cast(tuple[int, ...], tags)


def _production_receipt_id(
    prefix: str,
    binding: tuple[str, str, int, str, tuple[int, ...]],
    *,
    dispatch_action: str,
    action_metadata: (
        tuple[str, str, str, int, str, object, object, str] | None
    ) = None,
) -> str:
    update_id, operation_id, generation, action, unit_tags = binding
    fields = (prefix, update_id, operation_id)
    canonical = "".join(f"{len(value)}:{value}|" for value in fields)
    canonical += f"{generation}|"
    canonical += f"{len(action)}:{action}|"
    canonical += f"{len(dispatch_action)}:{dispatch_action}|"
    canonical += "".join(f"{tag}," for tag in unit_tags)
    canonical += "|"
    if action_metadata is not None:
        (
            unit_type,
            _metadata_dispatch,
            ability_name,
            ability_id,
            target_kind,
            target_x,
            target_y,
            cloak_state,
        ) = action_metadata
        canonical += f"{len(unit_type)}:{unit_type}|"
        canonical += f"{len(ability_name)}:{ability_name}|"
        canonical += f"{ability_id}|"
        canonical += f"{len(target_kind)}:{target_kind}|"
        canonical += f"{float(target_x):.17g}|{float(target_y):.17g}|"
        canonical += f"{len(cloak_state)}:{cloak_state}|"
    digest = 1_469_598_103_934_665_603
    for value in canonical.encode("utf-8"):
        digest ^= value
        digest = (digest * 1_099_511_628_211) & 0xFFFFFFFFFFFFFFFF
    return f"{prefix}-{digest:016x}"


def _unique_native_row(
    rows: Sequence[Mapping[str, object]],
    field_name: str,
    expected: str,
) -> Mapping[str, object]:
    matching = [row for row in rows if row.get(field_name) == expected]
    if len(matching) != 1:
        raise ValueError(f"native production binding is not unique: {field_name}")
    return matching[0]


def _require_native_binding_fields(
    expected: Mapping[str, object],
    observed: Mapping[str, object],
    fields: Sequence[str],
) -> None:
    if any(expected.get(field) != observed.get(field) for field in fields):
        raise ValueError("native production receipt binding is inconsistent")


def _validate_native_ownership_receipt_event(
    event: Mapping[str, object],
) -> tuple[str, str, int, int, int]:
    identity = event.get("identity")
    payload = event.get("payload")
    if (
        not isinstance(identity, Mapping)
        or not isinstance(payload, Mapping)
        or set(payload) != {"entrypoint", "unit_tag"}
        or payload.get("entrypoint") != "voiProductionAssignOperationOwner"
    ):
        raise ValueError("native ownership receipt is malformed")
    update_id = str(identity.get("update_id", "") or "")
    operation_id = str(identity.get("operation_id", "") or "")
    generation = identity.get("generation")
    game_frame = identity.get("game_frame")
    unit_tag = payload.get("unit_tag")
    if (
        identity.get("stage") != "accepted"
        or not update_id
        or not operation_id
        or type(generation) is not int
        or generation <= 0
        or type(game_frame) is not int
        or game_frame < 0
        or type(unit_tag) is not int
        or unit_tag <= 0
    ):
        raise ValueError("native ownership receipt identity is invalid")
    return update_id, operation_id, generation, game_frame, unit_tag


def _validate_native_ownership_causality(
    events: Sequence[object],
    ownership_receipts: Sequence[Mapping[str, object]],
) -> None:
    canonical_events = [
        event for event in events if isinstance(event, Mapping)
    ]
    receipt_bindings = [
        _validate_native_ownership_receipt_event(receipt)
        for receipt in ownership_receipts
    ]
    if len(receipt_bindings) != len(set(receipt_bindings)):
        raise ValueError("native ownership receipt is duplicated")
    admitted_by_identity: dict[tuple[str, str, int], list[int]] = {}
    for index, event in enumerate(canonical_events):
        if event.get("event_type") != "launch_decision":
            continue
        identity = event.get("identity")
        if (
            not isinstance(identity, Mapping)
            or identity.get("stage") != "launch_admitted"
        ):
            continue
        key = (
            str(identity.get("update_id", "") or ""),
            str(identity.get("operation_id", "") or ""),
            int(identity.get("generation", 0) or 0),
        )
        admitted_by_identity.setdefault(key, []).append(index)
    for receipt in ownership_receipts:
        identity = cast(Mapping[str, object], receipt.get("identity", {}))
        key = (
            str(identity.get("update_id", "") or ""),
            str(identity.get("operation_id", "") or ""),
            int(identity.get("generation", 0) or 0),
        )
        receipt_index = next(
            (
                index
                for index, event in enumerate(canonical_events)
                if event is receipt
                or (
                    event.get("event_type") == "production_path_receipt"
                    and event.get("seq") == receipt.get("seq")
                    and event.get("identity") == receipt.get("identity")
                    and event.get("payload") == receipt.get("payload")
                )
            ),
            -1,
        )
        if (
            receipt_index < 0
            or not any(
                admitted_index < receipt_index
                for admitted_index in admitted_by_identity.get(key, ())
            )
        ):
            raise ValueError(
                "native ownership receipt predates launch admission"
            )

    snapshot_bindings: list[tuple[str, str, int, int, int]] = []
    for event in canonical_events:
        if event.get("event_type") != "ownership_snapshot":
            continue
        identity = event.get("identity")
        payload = event.get("payload")
        if not isinstance(identity, Mapping) or not isinstance(payload, Mapping):
            raise ValueError("native ownership snapshot is malformed")
        update_id = str(identity.get("update_id", "") or "")
        operation_id = str(identity.get("operation_id", "") or "")
        generation = identity.get("generation")
        game_frame = identity.get("game_frame")
        assigned_tags = _native_optional_unit_tags(
            payload.get("assigned_unit_tags")
        )
        owners = payload.get("owners")
        owner_tags = (
            _native_optional_unit_tags(owners.get(operation_id, ()))
            if isinstance(owners, Mapping)
            else ()
        )
        if (
            not isinstance(owners, Mapping)
            or owner_tags != assigned_tags
            or not update_id
            or type(generation) is not int
            or generation <= 0
            or type(game_frame) is not int
            or game_frame < 0
        ):
            raise ValueError("native ownership snapshot identity is invalid")
        snapshot_bindings.extend(
            (
                update_id,
                operation_id,
                generation,
                game_frame,
                unit_tag,
            )
            for unit_tag in assigned_tags
        )
    if len(snapshot_bindings) != len(set(snapshot_bindings)):
        raise ValueError("native ownership snapshot binding is duplicated")
    if sorted(snapshot_bindings) != sorted(receipt_bindings):
        raise ValueError(
            "native ownership receipts do not match ownership snapshots"
        )


def _validate_native_submission_causality(
    events: Sequence[object],
    squad_receipts: Sequence[Mapping[str, object]],
    submission_receipts: Sequence[Mapping[str, object]],
) -> None:
    canonical_events = [
        event for event in events if isinstance(event, Mapping)
    ]
    squad_action_tags: dict[tuple[str, str, int, str], set[int]] = {}
    squad_receipts_by_id: dict[str, Mapping[str, object]] = {}
    for receipt in squad_receipts:
        receipt_id = str(receipt["receipt_id"])
        squad_receipts_by_id[receipt_id] = receipt
        squad_key = (
            str(receipt["update_id"]),
            str(receipt["operation_id"]),
            int(receipt["generation"]),
            str(receipt["action"]),
        )
        squad_action_tags.setdefault(squad_key, set()).update(
            _native_unit_tags(receipt["unit_tags"])
        )
    matched_squad_receipts: dict[str, int] = dict.fromkeys(
        squad_receipts_by_id,
        0,
    )
    for event in canonical_events:
        if event.get("event_type") != "squad_order":
            continue
        payload = event.get("payload")
        receipt_id = (
            str(payload.get("receipt_id", "") or "")
            if isinstance(payload, Mapping)
            else ""
        )
        receipt = squad_receipts_by_id.get(receipt_id)
        if receipt is None or not _native_action_event_matches(event, receipt):
            raise ValueError("canonical Squad order lacks a production receipt")
        matched_squad_receipts[receipt_id] += 1
    if any(count != 1 for count in matched_squad_receipts.values()):
        raise ValueError("native squad-order receipt lacks one canonical order")

    grouped_tags: dict[tuple[object, ...], set[int]] = {}
    grouped_ids: dict[tuple[object, ...], set[str]] = {}
    submission_action_tags: dict[tuple[str, str, int, str], set[int]] = {}
    for receipt in submission_receipts:
        key = (
            str(receipt["update_id"]),
            str(receipt["operation_id"]),
            int(receipt["generation"]),
            str(receipt["action"]),
            *_native_action_metadata(receipt),
        )
        receipt_tags = _native_unit_tags(receipt["unit_tags"])
        grouped_tags.setdefault(key, set()).update(receipt_tags)
        grouped_ids.setdefault(key, set()).add(
            str(receipt["submission_id"])
        )
        submission_action_tags.setdefault(key[:4], set()).update(receipt_tags)
    if set(squad_action_tags) != set(submission_action_tags):
        raise ValueError(
            "native Squad orders and SC2 submissions have different action bindings"
        )
    if any(
        squad_action_tags[key] != submission_action_tags[key]
        for key in squad_action_tags
    ):
        raise ValueError(
            "native Squad order and SC2 submission unit tags do not match"
        )
    submission_bindings: list[dict[str, object]] = []
    for key, unit_tags in grouped_tags.items():
        (
            update_id,
            operation_id,
            generation,
            action,
            unit_type,
            dispatch_action,
            ability_name,
            ability_id,
            target_kind,
            target_x,
            target_y,
            cloak_state,
        ) = key
        binding = {
            "update_id": update_id,
            "operation_id": operation_id,
            "generation": generation,
            "action": action,
            "unit_type": unit_type,
            "dispatch_action": dispatch_action,
            "ability_name": ability_name,
            "ability_id": ability_id,
            "target_kind": target_kind,
            "target_x": target_x,
            "target_y": target_y,
            "cloak_state": cloak_state,
            "submission_ids": sorted(
                grouped_ids[key]
            ),
            "unit_tags": sorted(unit_tags),
        }
        submission_bindings.append(binding)

    matched_submission_bindings = [0] * len(submission_bindings)
    for event in canonical_events:
        if event.get("event_type") != "submission":
            continue
        matching = [
            index
            for index, binding in enumerate(submission_bindings)
            if _native_action_event_matches(event, binding)
        ]
        if len(matching) != 1:
            raise ValueError("canonical submission lacks exact SC2 receipts")
        matched_submission_bindings[matching[0]] += 1
    if any(count != 1 for count in matched_submission_bindings):
        raise ValueError("native SC2 receipts lack one canonical submission")

    matched_effect_bindings = [0] * len(submission_bindings)
    for event in canonical_events:
        if event.get("event_type") not in _EFFECT_EVENT_TYPES:
            continue
        matching = [
            index
            for index, binding in enumerate(submission_bindings)
            if _native_action_event_matches(event, binding)
        ]
        if len(matching) != 1:
            raise ValueError("native effect lacks exact SC2 receipt proof")
        matched_effect_bindings[matching[0]] += 1
    if any(count > 1 for count in matched_effect_bindings):
        raise ValueError("native SC2 receipt has duplicate canonical effects")


def _validate_native_family_effect_causality(
    events: Sequence[object],
    operation_director: object,
    *,
    require_submitted_effects: bool = False,
) -> None:
    rows = _mapping_sequence(operation_director)
    if not isinstance(operation_director, Sequence) or isinstance(
        operation_director,
        (str, bytes, bytearray),
    ):
        raise ValueError("native operation director is malformed")
    canonical_events = [
        event for event in events if isinstance(event, Mapping)
    ]
    effect_events = [
        event
        for event in canonical_events
        if event.get("event_type") in _EFFECT_EVENT_TYPES
    ]
    current_action_keys: set[tuple[str, str, int, str]] = set()
    family_bindings: list[dict[str, object]] = []
    effect_types = {
        "movement": "movement",
        "engagement": "engagement",
        "ability_state": "ability_effect",
    }
    for row in rows:
        update_id = row.get("policy_update_id")
        operation_id = row.get("operation_id")
        generation = row.get("generation")
        action = row.get("last_action")
        if (
            not isinstance(update_id, str)
            or not update_id
            or not isinstance(operation_id, str)
            or not operation_id
            or type(generation) is not int
            or generation <= 0
            or not isinstance(action, str)
        ):
            raise ValueError("native operation director identity is invalid")
        if action:
            current_action_keys.add(
                (update_id, operation_id, generation, action)
            )
        raw_family = row.get("family_evidence")
        if not isinstance(raw_family, Sequence) or isinstance(
            raw_family,
            (str, bytes, bytearray),
        ):
            raise ValueError("native family evidence is malformed")
        for raw_evidence in raw_family:
            if not isinstance(raw_evidence, Mapping):
                raise ValueError("native family evidence row is malformed")
            effect_count = raw_evidence.get("effect_count")
            if type(effect_count) is not int or effect_count < 0:
                raise ValueError("native family effect count is invalid")
            submitted_count = raw_evidence.get("submitted_count")
            if type(submitted_count) is not int or submitted_count < 0:
                raise ValueError("native family submitted count is invalid")
            if require_submitted_effects and (
                (submitted_count > 0 and effect_count == 0)
                or (effect_count > 0 and submitted_count == 0)
            ):
                raise ValueError(
                    "native submitted all-Terran family action lacks an effect"
                )
            if effect_count == 0 or submitted_count == 0:
                continue
            effect_kind = str(raw_evidence.get("effect_kind", "") or "")
            event_type = effect_types.get(effect_kind)
            unit_type = str(raw_evidence.get("unit_type", "") or "")
            evidence_generation = raw_evidence.get("generation")
            evidence_frame = raw_evidence.get("effect_frame")
            evidence_tags = _native_unit_tags(
                raw_evidence.get("effect_unit_tags")
            )
            submitted_tags = _native_unit_tags(
                raw_evidence.get("submitted_unit_tags")
            )
            if (
                event_type is None
                or not unit_type
                or raw_evidence.get("update_id") != update_id
                or raw_evidence.get("operation_id") != operation_id
                or evidence_generation != generation
                or raw_evidence.get("action") != action
                or effect_count != len(evidence_tags)
                or submitted_count != len(submitted_tags)
                or evidence_tags != submitted_tags
                or type(evidence_frame) is not int
                or evidence_frame <= 0
            ):
                raise ValueError("native family effect binding is invalid")
            family_bindings.append(
                {
                    "event_type": event_type,
                    "update_id": update_id,
                    "operation_id": operation_id,
                    "generation": generation,
                    "action": action,
                    "unit_type": unit_type,
                    "game_frame": evidence_frame,
                    "effect_kind": effect_kind,
                    "unit_tags": list(evidence_tags),
                }
            )
    matched_family = [0] * len(family_bindings)
    for event in effect_events:
        matching = [
            index
            for index, binding in enumerate(family_bindings)
            if _native_family_effect_event_matches(event, binding)
        ]
        identity = cast(Mapping[str, object], event.get("identity", {}))
        payload = cast(Mapping[str, object], event.get("payload", {}))
        action_key = (
            str(identity.get("update_id", "") or ""),
            str(identity.get("operation_id", "") or ""),
            int(identity.get("generation", 0) or 0),
            str(payload.get("action", "") or ""),
        )
        if action_key in current_action_keys and len(matching) != 1:
            raise ValueError("native effect lacks exact family evidence")
        for index in matching:
            matched_family[index] += 1
    if any(count != 1 for count in matched_family):
        raise ValueError("native family evidence lacks one canonical effect")


def _native_family_effect_event_matches(
    event: Mapping[str, object],
    binding: Mapping[str, object],
) -> bool:
    identity = event.get("identity")
    payload = event.get("payload")
    return bool(
        event.get("event_type") == binding.get("event_type")
        and isinstance(identity, Mapping)
        and isinstance(payload, Mapping)
        and identity.get("update_id") == binding.get("update_id")
        and identity.get("operation_id") == binding.get("operation_id")
        and identity.get("generation") == binding.get("generation")
        and identity.get("game_frame") == binding.get("game_frame")
        and payload.get("action") == binding.get("action")
        and payload.get("unit_type") == binding.get("unit_type")
        and payload.get("effect_kind") == binding.get("effect_kind")
        and payload.get("unit_tags") == binding.get("unit_tags")
    )


def _matching_native_action_event(
    events: Sequence[Mapping[str, object]],
    event_type: str,
    binding: Mapping[str, object],
) -> bool:
    return any(
        event.get("event_type") == event_type
        and _native_action_event_matches(event, binding)
        for event in events
    )


def _native_action_event_matches(
    event: Mapping[str, object],
    binding: Mapping[str, object],
) -> bool:
    payload = event.get("payload")
    return bool(
        _native_action_event_identity_matches(event, binding)
        and isinstance(payload, Mapping)
        and all(
            payload.get(field_name) == binding.get(field_name)
            for field_name in _NATIVE_ACTION_PROOF_FIELDS
            if field_name in binding
        )
    )


def _native_action_event_identity_matches(
    event: Mapping[str, object],
    binding: Mapping[str, object],
) -> bool:
    identity = event.get("identity")
    payload = event.get("payload")
    return bool(
        isinstance(identity, Mapping)
        and isinstance(payload, Mapping)
        and identity.get("update_id") == binding.get("update_id")
        and identity.get("operation_id") == binding.get("operation_id")
        and identity.get("generation") == binding.get("generation")
        and payload.get("action") == binding.get("action")
    )


def _consume_native_output(
    execution: _JourneyExecution,
    native: Mapping[str, object],
    *,
    command_runner: CommandRunner = subprocess.run,
    node_executable: Path | str | None = None,
    node_sha256: str | None = None,
) -> None:
    raw_events = native.get("events")
    if not isinstance(raw_events, Sequence) or isinstance(
        raw_events,
        (str, bytes, bytearray),
    ):
        raise ValueError("native events must be an array")
    if [event.get("seq") for event in raw_events if isinstance(event, Mapping)] != list(
        range(1, len(raw_events) + 1)
    ):
        raise ValueError("native event sequence is not contiguous")
    for event in raw_events:
        if not isinstance(event, Mapping):
            raise ValueError("native event must be an object")
        normalized = deepcopy(dict(event))
        normalized["_order"] = 20
        normalized["_native_seq"] = int(normalized["seq"])
        normalized.pop("seq", None)
        execution.events.append(normalized)
    _derive_state_snapshot_events(execution, native)
    _derive_blocked_launch_events(execution, native)
    _consume_runtime_products(execution, native)
    _consume_web_event_reconnect(execution, native)
    _consume_projection_events(
        execution,
        native,
        command_runner=command_runner,
        node_executable=node_executable,
        node_sha256=node_sha256,
    )


def _derive_state_snapshot_events(
    execution: _JourneyExecution,
    native: Mapping[str, object],
) -> None:
    stop = cast(Mapping[str, object], execution.spec["stop_condition"])
    if stop.get("type") not in {
        "forbidden_submission_and_state_preserved",
        "transfer_applied_and_siblings_preserved",
        "selected_operation_cancelled_sibling_active",
    }:
        return
    snapshots = native.get("snapshots")
    if not isinstance(snapshots, Sequence) or len(snapshots) != len(
        execution.native_steps
    ) + 1:
        raise ValueError("native snapshots do not align with adapter steps")
    policy_indices = [
        index
        for index, step in enumerate(execution.native_steps)
        if step.get("kind") == "policy_update"
    ]
    if not policy_indices:
        raise ValueError("state-preservation journey has no policy step")
    step_index = policy_indices[-1]
    step = execution.native_steps[step_index]
    update = cast(Mapping[str, object], step["update"])
    operation_id = str(stop.get("operation_id", ""))
    operation = (
        _operation_by_id(update, operation_id)
        if operation_id
        else (_update_operations(update)[0] if _update_operations(update) else {})
    )
    identity = {
        "update_id": str(update["update_id"]),
        "operation_id": str(operation.get("operation_id", "")),
        "generation": int(operation.get("generation", 0) or 0),
    }
    before = cast(Mapping[str, object], snapshots[step_index])
    after = cast(Mapping[str, object], snapshots[step_index + 1])
    for phase, snapshot, order in (
        ("before", before, 10),
        ("after", after, 30),
    ):
        state = snapshot.get("state")
        if not isinstance(state, Mapping):
            raise ValueError("native state snapshot is malformed")
        execution.emit(
            "state_snapshot",
            update_id=identity["update_id"],
            operation_id=identity["operation_id"],
            generation=identity["generation"],
            stage=f"state_{phase}",
            game_frame=int(step["frame"]),
            payload={"phase": phase, "state": dict(state)},
            order=order,
        )


def _derive_blocked_launch_events(
    execution: _JourneyExecution,
    native: Mapping[str, object],
) -> None:
    rows = _mapping_sequence(native.get("operation_director"))
    native_events = _mapping_sequence(native.get("events"))
    native_event_types = {
        str(event.get("event_type", "")) for event in native_events
    }
    if "launch_decision" in native_event_types and "rejection" in native_event_types:
        return
    prerequisite_evidence: dict[
        tuple[str, str, int, str, str],
        set[str],
    ] = {}
    for event in native_events:
        event_type = str(event.get("event_type", "") or "")
        if event_type not in {"production_decision", "prerequisite_wait"}:
            continue
        identity = event.get("identity")
        payload = event.get("payload")
        if not isinstance(identity, Mapping) or not isinstance(payload, Mapping):
            continue
        key = (
            str(identity.get("update_id", "") or ""),
            str(identity.get("operation_id", "") or ""),
            int(identity.get("generation", 0) or 0),
            str(payload.get("action", "") or ""),
            str(payload.get("blocker", "") or ""),
        )
        prerequisite_evidence.setdefault(key, set()).add(event_type)
    for row in rows:
        blocker = str(row.get("blocker", "") or "")
        submitted = row.get("submission_observed") is True
        if not blocker or submitted:
            continue
        identity = _operation_row_identity(execution, row)
        action = str(row.get("last_action", "") or "launch")
        prerequisite_key = (
            str(identity["update_id"]),
            str(identity["operation_id"]),
            int(identity["generation"]),
            action,
            blocker,
        )
        if prerequisite_evidence.get(prerequisite_key) == {
            "production_decision",
            "prerequisite_wait",
        }:
            continue
        if "launch_decision" not in native_event_types:
            execution.emit(
                "launch_decision",
                **identity,
                stage="launch_rejected",
                game_frame=int(row.get("last_action_frame", 0) or 0),
                payload={
                    "action": "wait",
                    "launch_count": 0,
                    "blocker": blocker,
                },
                order=21,
            )
        if "rejection" not in native_event_types:
            execution.emit(
                "rejection",
                **identity,
                stage="blocked",
                game_frame=int(row.get("last_action_frame", 0) or 0),
                payload={
                    "action": action,
                    "reason": blocker,
                },
                order=22,
            )


def _consume_runtime_products(
    execution: _JourneyExecution,
    native: Mapping[str, object],
) -> None:
    telemetry = native.get("telemetry")
    if not isinstance(telemetry, Mapping):
        raise ValueError("native telemetry is missing")
    execution.backend.ingest_telemetry(telemetry)
    execution.products["native_snapshots"] = deepcopy(native["snapshots"])
    execution.products["native_final_state"] = deepcopy(native["final_state"])
    execution.products["native_hud"] = deepcopy(native["hud"])
    reports: list[object] = []
    for update in execution.compiled_updates:
        reports.append(
            [
                report.to_dict()
                for report in classify_micromachine_operation_executions(
                    latest_update=update,
                    latest_telemetry=telemetry,
                    latest_frame=int(telemetry.get("frame", 0) or 0),
                )
            ]
        )
    execution.products["operation_execution_reports"] = reports
    expected_effects = _expected_tactical_effects(
        _mapping_sequence(native.get("events"))
    )
    tactical = classify_micromachine_tactical_evidence(
        latest_telemetry=telemetry,
        expected_effects=expected_effects,
    )
    execution.products["tactical_evidence"] = [tactical.to_dict()]
    native_rows = {
        (
            str(row.get("operation_id", "")),
            int(row.get("generation", 0) or 0),
        ): row
        for row in _mapping_sequence(native.get("operation_director"))
    }
    family_batches: list[dict[str, object]] = []
    for update in execution.compiled_updates:
        for operation in _update_operations(update):
            identity = {
                "update_id": str(update["update_id"]),
                "operation_id": str(operation["operation_id"]),
                "generation": int(operation["generation"]),
            }
            row = native_rows.get(
                (identity["operation_id"], identity["generation"])
            )
            evidence = (
                list(
                    operation_family_evidence(
                        row,
                        expected_update_id=identity["update_id"],
                        expected_operation_id=identity["operation_id"],
                        expected_generation=identity["generation"],
                        snapshot_frame=int(telemetry.get("frame", 0) or 0),
                    )
                )
                if row is not None
                else []
            )
            family_batches.append(
                {
                    **identity,
                    "native_row_present": row is not None,
                    "evidence": evidence,
                }
            )
    execution.products["family_evidence"] = family_batches
    validated = validate_battlefield_overview(
        telemetry,
        expected_scope="battlefield",
    )
    selected = select_latest_battlefield_projection(
        latest_telemetry=telemetry,
        expected_scope="battlefield",
    )
    execution.products["battlefield_projections"] = [
        {
            "validated": validated.to_dict(),
            "selected": selected.to_dict(),
        }
    ]
    dashboard = execution.backend.dashboard_snapshot(
        current_frame=int(telemetry.get("frame", 0) or 0)
    ).to_dict()
    overview = cast(Mapping[str, object], telemetry.get("battlefield_overview", {}))
    overview_identity = cast(Mapping[str, object], overview.get("identity", {}))
    session_epoch = str(overview_identity.get("session_epoch", "") or "")
    if not session_epoch:
        raise ValueError("native battlefield overview lacks a session epoch")
    result_stream = [
        {
            "status": "published",
            "update": deepcopy(update),
            "battlefield_session_epoch": session_epoch,
        }
        for update in execution.compiled_updates
    ]
    status = web_gui._micromachine_status_payload(
        dashboard,
        telemetry=telemetry,
        battlefield_projection=selected,
        result_stream=result_stream,
    )
    reduced = cast(
        web_gui._OperationSemanticTimelineReducer,
        execution.timeline,
    ).observe(
        status,
        blackboard_scope_id=execution.journey_id,
    )
    execution.products["web_status"] = [status]
    execution.products["timeline_results"] = [reduced]


def _consume_web_event_reconnect(
    execution: _JourneyExecution,
    native: Mapping[str, object],
) -> None:
    reconnects = [
        event
        for event in _mapping_sequence(native.get("events"))
        if event.get("event_type") == "client_reconnect"
    ]
    if not reconnects:
        return
    if len(reconnects) != 1:
        raise ValueError("native reconnect marker is not unique")
    reconnect = reconnects[0]
    journal = cast(web_gui._WebEventJournal, execution.journal)
    lifecycle_events = [
        event
        for event in _mapping_sequence(native.get("events"))
        if event.get("event_type") in _WEB_LIFECYCLE_EVENT_TYPES
    ]
    published_events: list[dict[str, object]] = []
    for event in lifecycle_events:
        identity = cast(Mapping[str, object], event["identity"])
        logical_id = f"native:{event['seq']}:{event['event_type']}"
        source_payload = {
            "logical_event_id": logical_id,
            "source_event_seq": int(event["seq"]),
            "source_event_type": str(event["event_type"]),
            "source_identity": deepcopy(dict(identity)),
            "source_payload": deepcopy(
                dict(cast(Mapping[str, object], event["payload"]))
            ),
        }
        published = journal.publish(
            str(event["event_type"]),
            source_payload,
            update_id=str(identity["update_id"]),
            operation_id=str(identity["operation_id"]),
            generation=int(identity["generation"]),
            game_frame=int(identity["game_frame"]),
            blackboard_scope_id=execution.journey_id,
        )
        published_events.append(published)
        execution.emit(
            "web_event",
            update_id=str(identity["update_id"]),
            operation_id=str(identity["operation_id"]),
            generation=int(identity["generation"]),
            stage="web_event",
            game_frame=int(identity["game_frame"]),
            payload={
                "action": str(event["event_type"]),
                "event_seq": int(published["event_seq"]),
                **deepcopy(source_payload),
            },
            order=40,
        )
    reconnect_identity = cast(Mapping[str, object], reconnect["identity"])
    reconnect_payload = cast(Mapping[str, object], reconnect["payload"])
    initial_state = execution.spec.get("initial_state")
    expected_cursor = (
        initial_state.get("event_cursor")
        if isinstance(initial_state, Mapping)
        else None
    )
    if type(expected_cursor) is not int or expected_cursor < 0:
        raise ValueError("manifest reconnect cursor is invalid")
    if reconnect_payload.get("after_event_seq") != expected_cursor:
        raise ValueError(
            "native reconnect cursor does not match the manifest initial cursor"
        )
    cursor = expected_cursor
    available, replay = journal.replay_batch(cursor)
    execution.emit(
        "replay_batch",
        update_id=str(reconnect_identity["update_id"]),
        stage="replayed",
        game_frame=int(reconnect_identity["game_frame"]),
        payload={
            "action": "replay",
            "available": available,
            "event_seqs": [int(event["event_seq"]) for event in replay],
        },
        order=41,
    )
    replay_payloads = [
        cast(Mapping[str, object], event["payload"])
        for event in replay
        if isinstance(event.get("payload"), Mapping)
    ]
    if len(replay_payloads) != len(replay):
        raise ValueError("native replay journal payload is malformed")
    replay_ids = [
        str(payload["logical_event_id"])
        for payload in replay_payloads
    ]
    if len(replay_ids) != len(set(replay_ids)):
        raise ValueError("native replay journal contains duplicate logical events")
    for replayed_event, payload in zip(replay, replay_payloads, strict=True):
        source_identity = cast(
            Mapping[str, object],
            payload.get("source_identity", {}),
        )
        execution.emit(
            "replay_deduplicated",
            update_id=str(source_identity.get("update_id", "")),
            operation_id=str(source_identity.get("operation_id", "")),
            generation=int(source_identity.get("generation", 0) or 0),
            stage="replayed",
            game_frame=int(source_identity.get("game_frame", 0) or 0),
            payload={
                "action": "dedupe",
                "event_seq": int(replayed_event["event_seq"]),
                **deepcopy(dict(payload)),
            },
            order=42,
        )
    execution.products["event_journal_replay"] = {
        "available": available,
        "source_count": len(published_events),
        "replay_count": len(replay),
    }


def _consume_projection_events(
    execution: _JourneyExecution,
    native: Mapping[str, object],
    *,
    command_runner: CommandRunner,
    node_executable: Path | str | None = None,
    node_sha256: str | None = None,
) -> None:
    rows = _mapping_sequence(native.get("operation_director"))
    final_frame = int(
        cast(Mapping[str, object], native["telemetry"]).get("frame", 0) or 0
    )
    timeline = cast(
        Sequence[Mapping[str, object]],
        cast(Sequence[object], execution.products["timeline_results"]),
    )[0]
    timeline_events = _mapping_sequence(timeline.get("operation_events"))
    for row in rows:
        identity = _operation_row_identity(execution, row)
        execution.emit(
            "web_projection",
            **identity,
            stage=(
                "effect_observed"
                if row.get("completed") is True
                else "assigned"
            ),
            game_frame=final_frame,
            payload={
                "action": "project",
                "timeline_event_count": sum(
                    1
                    for event in timeline_events
                    if event.get("update_id") == identity["update_id"]
                    and event.get("operation_id") == identity["operation_id"]
                    and int(event.get("generation", 0) or 0)
                    == identity["generation"]
                ),
            },
            order=50,
        )
    hud = native.get("hud")
    if not isinstance(hud, Mapping):
        raise ValueError("native HUD output is malformed")
    hud_ids = {
        str(row.get("operation_id", ""))
        for row in _mapping_sequence(hud.get("rows"))
        if row.get("marker_visible") is True
    }
    for row in rows:
        if str(row.get("operation_id", "")) not in hud_ids:
            continue
        identity = _operation_row_identity(execution, row)
        execution.emit(
            "hud_projection",
            **identity,
            stage=(
                "effect_observed"
                if row.get("completed") is True
                else "assigned"
            ),
            game_frame=final_frame,
            payload={"action": "project", "marker_visible": True},
            order=51,
        )
    if execution.spec.get("kind") != "voice_identity":
        return
    final_state = native.get("final_state")
    if not isinstance(final_state, Mapping):
        raise ValueError("voice journey lacks native final state")
    if (
        final_state.get("voice_enabled") is not True
        or final_state.get("muted") is not False
    ):
        raise ValueError("voice journey initial state disabled its callout path")
    if not rows or not timeline_events:
        raise ValueError("voice journey lacks runtime timeline evidence")
    row = rows[0]
    identity = _operation_row_identity(execution, row)
    matching = [
        event
        for event in timeline_events
        if event.get("update_id") == identity["update_id"]
        and event.get("operation_id") == identity["operation_id"]
        and int(event.get("generation", 0) or 0) == identity["generation"]
    ]
    if not matching:
        raise ValueError("voice journey lacks a matching semantic timeline event")
    callout_events = [
        event
        for event in matching
        if str(event.get("kind", "")).lower()
        in _TACTICAL_RADIO_CALLOUT_KINDS
    ]
    if len(callout_events) < 2:
        raise ValueError(
            "voice journey lacks two production-admissible lifecycle events"
        )
    primary_event, secondary_event = callout_events[-2:]
    radio_result = _execute_tactical_radio_runtime(
        primary_event,
        secondary_event,
        scope_id=execution.journey_id,
        expected_identity=identity,
        command_runner=command_runner,
        node_executable=node_executable,
        expected_node_sha256=node_sha256,
    )
    execution.products["tactical_radio_runtime"] = radio_result
    secondary_callout = cast(
        Mapping[str, object],
        radio_result["secondary_callout"],
    )
    stage = (
        "effect_observed" if row.get("completed") is True else "assigned"
    )
    execution.emit(
        "voice_projection",
        **identity,
        stage=stage,
        game_frame=final_frame,
        payload={
            "action": "project",
            "timeline_seq": int(secondary_event["timeline_seq"]),
            "kind": str(secondary_event["kind"]),
        },
        order=52,
    )
    execution.emit(
        "voice_callout",
        **identity,
        stage=stage,
        game_frame=final_frame,
        payload={
            "action": "speak",
            "timeline_seq": int(secondary_event["timeline_seq"]),
            "caption": str(secondary_callout["caption"]),
            "text": str(secondary_callout["speech"]),
        },
        order=53,
    )


def _execute_tactical_radio_runtime(
    primary_event: Mapping[str, object],
    secondary_event: Mapping[str, object],
    *,
    scope_id: str,
    expected_identity: Mapping[str, object],
    command_runner: CommandRunner,
    node_executable: Path | str | None = None,
    expected_node_sha256: str | None = None,
) -> dict[str, object]:
    node = _validate_node_executable(node_executable)
    node_sha256 = _sha256_file(node)
    if (
        expected_node_sha256 is not None
        and node_sha256 != expected_node_sha256
    ):
        raise ValueError("Node.js executable digest mismatch")
    primary_identity = _timeline_event_identity(primary_event)
    secondary_identity = _timeline_event_identity(secondary_event)
    if primary_identity[:3] != secondary_identity[:3]:
        raise ValueError("Tactical Radio lifecycle identities do not match")
    expected = (
        str(expected_identity.get("update_id", "") or ""),
        str(expected_identity.get("operation_id", "") or ""),
        expected_identity.get("generation"),
    )
    if (
        not expected[0]
        or not expected[1]
        or type(expected[2]) is not int
        or cast(int, expected[2]) <= 0
    ):
        raise ValueError("Tactical Radio operation identity is malformed")
    if primary_identity[:3] != expected:
        raise ValueError(
            "Tactical Radio lifecycle identity does not match operation identity"
        )
    if (
        secondary_identity[3] <= primary_identity[3]
        or secondary_identity[4] < primary_identity[4]
    ):
        raise ValueError("Tactical Radio lifecycle evidence is stale")
    session_epoch = str(primary_event.get("session_epoch", "") or "")
    if not session_epoch or session_epoch != str(
        secondary_event.get("session_epoch", "") or ""
    ):
        raise ValueError("Tactical Radio lifecycle session epoch is invalid")
    runtime_source = _production_tactical_radio_source()
    completed = _run_native_command(
        node,
        [str(node), "-e", _tactical_radio_node_harness(runtime_source)],
        expected_sha256=node_sha256,
        command_runner=command_runner,
        input=canonical_json_bytes(
            {
                "schema_version": 1,
                "now_unix_ms": 1_700_000_000_000,
                "scope_id": scope_id,
                "session_epoch": session_epoch,
                "expected_identity": dict(expected_identity),
                "primary_event": dict(primary_event),
                "secondary_event": dict(secondary_event),
            }
        ),
    )
    if _sha256_file(node) != node_sha256:
        raise ValueError("Node.js executable changed during Tactical Radio replay")
    if int(completed.returncode) != 0:
        stderr = _as_bytes(completed.stderr).decode(
            "utf-8",
            errors="replace",
        )
        raise ValueError(
            f"production Tactical Radio replay failed: {stderr.strip()}"
        )
    stdout = _as_bytes(completed.stdout)
    if not stdout or len(stdout) > MAX_TACTICAL_RADIO_OUTPUT_BYTES:
        raise ValueError("production Tactical Radio output size is invalid")
    result = json.loads(
        stdout,
        object_pairs_hook=_reject_duplicate_json_object_keys,
    )
    validated = _validate_tactical_radio_result(
        result,
        primary_event=primary_event,
        secondary_event=secondary_event,
    )
    return {
        "schema_version": 1,
        "runtime": "production_web_gui_tactical_radio_js",
        "node_sha256": node_sha256,
        "source_sha256": hashlib.sha256(
            runtime_source.encode("utf-8")
        ).hexdigest(),
        **validated,
    }


def _timeline_event_identity(
    event: Mapping[str, object],
) -> tuple[str, str, int, int, int]:
    update_id = str(event.get("update_id", "") or "")
    operation_id = str(event.get("operation_id", "") or "")
    generation = event.get("generation")
    timeline_seq = event.get("timeline_seq")
    game_frame = event.get("game_frame")
    if (
        not update_id
        or not operation_id
        or type(generation) is not int
        or generation <= 0
        or type(timeline_seq) is not int
        or timeline_seq <= 0
        or type(game_frame) is not int
        or game_frame < 0
    ):
        raise ValueError("Tactical Radio lifecycle identity is malformed")
    return update_id, operation_id, generation, timeline_seq, game_frame


def _production_tactical_radio_source() -> str:
    page = web_gui.render_web_gui_page()
    declarations = [
        *(
            _extract_javascript_statement(page, anchor)
            for anchor in _TACTICAL_RADIO_VARIABLE_ANCHORS
        ),
        *(
            _extract_javascript_function(page, name)
            for name in _TACTICAL_RADIO_FUNCTION_NAMES
        ),
    ]
    return "\n\n".join(declarations) + "\n"


def _extract_javascript_statement(source: str, anchor: str) -> str:
    start = source.find(anchor)
    if start < 0:
        raise ValueError(f"production JavaScript statement is missing: {anchor}")
    end = source.find(";\n", start)
    if end < 0:
        raise ValueError(f"production JavaScript statement is unterminated: {anchor}")
    return source[start : end + 1]


def _extract_javascript_function(source: str, name: str) -> str:
    anchor = f"function {name}("
    start = source.find(anchor)
    if start < 0:
        raise ValueError(f"production JavaScript function is missing: {name}")
    body_start = source.find("{", start)
    if body_start < 0:
        raise ValueError(f"production JavaScript function is malformed: {name}")
    depth = 0
    quote = ""
    escaped = False
    line_comment = False
    block_comment = False
    index = body_start
    while index < len(source):
        character = source[index]
        next_character = source[index + 1] if index + 1 < len(source) else ""
        if line_comment:
            if character == "\n":
                line_comment = False
            index += 1
            continue
        if block_comment:
            if character == "*" and next_character == "/":
                block_comment = False
                index += 2
            else:
                index += 1
            continue
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = ""
            index += 1
            continue
        if character in {"'", '"', "`"}:
            quote = character
            index += 1
            continue
        if character == "/" and next_character == "/":
            line_comment = True
            index += 2
            continue
        if character == "/" and next_character == "*":
            block_comment = True
            index += 2
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
        index += 1
    raise ValueError(f"production JavaScript function is unterminated: {name}")


def _tactical_radio_node_harness(runtime_source: str) -> str:
    preamble = r"""
"use strict";
var fs = require("fs");
var input = JSON.parse(fs.readFileSync(0, "utf8"));
var nowMs = Number(input.now_unix_ms || 0);
Date.now = function() { return nowMs; };
var spoken = [];
var utterances = [];
function FakeSpeechSynthesisUtterance(text) {
  this.text = String(text || "");
  this.lang = "";
  this.onend = null;
  this.onerror = null;
}
var window = {
  SpeechSynthesisUtterance: FakeSpeechSynthesisUtterance,
  speechSynthesis: {
    speak: function(utterance) {
      spoken.push(utterance.text);
      utterances.push(utterance);
    },
    cancel: function() {}
  },
  setTimeout: function(callback, delay) {
    nowMs += Math.max(0, Number(delay || 0));
    callback();
    return 1;
  },
  clearTimeout: function() {}
};
var document = {
  getElementById: function() { return null; },
  createElement: function() {
    return {
      appendChild: function() {},
      setAttribute: function() {},
      textContent: "",
      className: ""
    };
  }
};
var currentLang = "en";
var operationConsoleSessionEpoch = String(input.session_epoch || "");
function t(key) {
  var labels = {
    tacticalForceAssigned: "Force assigned",
    tacticalForcePartiallyAssigned: "Force partially assigned",
    tacticalMoving: "Operation moving",
    tacticalEngaged: "Operation engaged",
    tacticalTargetReached: "Target reached",
    tacticalCompleted: "Operation completed",
    tacticalRouteUnavailable: "Route unavailable",
    tacticalBlocked: "Operation blocked",
    tacticalEmergencyRetreat: "Emergency retreat",
    tacticalBaseAttack: "Base under attack",
    tacticalCriticalAbilityFailure: "Critical ability failure",
    tacticalForceLoss: "Operation force loss",
    tacticalSubmittedCaption: "Action submitted",
    tacticalRadioSpeaking: "Speaking",
    tacticalRadioMuted: "Muted",
    tacticalRadioUnavailable: "Unavailable",
    tacticalRadioReady: "Ready",
    tacticalRadioMute: "Mute",
    tacticalRadioUnmute: "Unmute"
  };
  return labels[key] || String(key || "");
}
"""
    suffix = r"""
function clone(value) {
  return JSON.parse(JSON.stringify(value));
}
function envelopeFor(payload) {
  return {
    update_id: String(payload.update_id || ""),
    created_at_unix_ms: Number(input.now_unix_ms || 0)
  };
}
var record = {
  updateId: String(input.expected_identity.update_id || ""),
  operationGeneration: Number(input.expected_identity.generation || 0),
  requestedOperationGeneration: Number(
    input.primary_event.requested_generation ||
    input.expected_identity.generation ||
    0
  ),
  sessionEpoch: String(input.session_epoch || ""),
  data: {
    operation_console_execution_owner_update_id: String(
      input.expected_identity.update_id || ""
    )
  }
};
var productionAnnouncementCalls = 0;
function announceThroughProduction(envelope, payload, scopeId, operationRecord) {
  productionAnnouncementCalls += 1;
  return announceOperationLifecycleEvent(
    envelope,
    payload,
    scopeId,
    operationRecord
  );
}
var primaryPayload = clone(input.primary_event);
var primaryEnvelope = envelopeFor(primaryPayload);
var primaryAccepted = announceThroughProduction(
  primaryEnvelope,
  primaryPayload,
  String(input.scope_id || ""),
  record
);
var primaryCallout = clone(tacticalRadio.current || {});
var duplicatePrimaryAccepted = announceThroughProduction(
  primaryEnvelope,
  clone(primaryPayload),
  String(input.scope_id || ""),
  record
);
var stalePayload = clone(primaryPayload);
stalePayload.timeline_seq = Math.max(
  1,
  Number(stalePayload.timeline_seq || 1) - 1
);
stalePayload.game_frame = Math.max(
  0,
  Number(stalePayload.game_frame || 0) - 1
);
var staleAccepted = announceThroughProduction(
  envelopeFor(stalePayload),
  stalePayload,
  String(input.scope_id || ""),
  record
);
var secondaryPayload = clone(input.secondary_event);
var secondaryEnvelope = envelopeFor(secondaryPayload);
var secondaryAccepted = announceThroughProduction(
  secondaryEnvelope,
  secondaryPayload,
  String(input.scope_id || ""),
  record
);
var secondaryCallout = clone(
  tacticalRadio.queue[tacticalRadio.queue.length - 1] || {}
);
var duplicateSecondaryAccepted = announceThroughProduction(
  secondaryEnvelope,
  clone(secondaryPayload),
  String(input.scope_id || ""),
  record
);
var queueLengthBeforeDrain = tacticalRadio.queue.length;
if (utterances[0] && utterances[0].onend) {
  utterances[0].onend();
}
var queueLengthAfterDrain = tacticalRadio.queue.length;
if (utterances[1] && utterances[1].onend) {
  utterances[1].onend();
}
var captionCountBeforeMute = tacticalRadio.captions.length;
var speechCountBeforeMute = spoken.length;
tacticalRadioSetMuted(true);
var mutedAccepted = queueTacticalRadioCallout({
  priority: 1,
  caption: "Muted deterministic caption",
  speech: "Muted speech must not play",
  dedupeKey: "muted-deterministic-caption",
  createdAt: Number(input.now_unix_ms || 0)
});
var mutedCaptionDelta = tacticalRadio.captions.length - captionCountBeforeMute;
var mutedSpeechDelta = spoken.length - speechCountBeforeMute;
tacticalRadioSetMuted(false);
var operationKey = tacticalRadioOperationKey(
  String(input.scope_id || ""),
  String(input.session_epoch || ""),
  String(input.expected_identity.operation_id || ""),
  Number(input.expected_identity.generation || 0)
);
process.stdout.write(JSON.stringify({
  schema_version: 1,
  primary_accepted: primaryAccepted,
  secondary_accepted: secondaryAccepted,
  duplicate_primary_accepted: duplicatePrimaryAccepted,
  duplicate_secondary_accepted: duplicateSecondaryAccepted,
  stale_accepted: staleAccepted,
  production_announcement_calls: productionAnnouncementCalls,
  muted_accepted: mutedAccepted,
  queue_length_before_drain: queueLengthBeforeDrain,
  queue_length_after_drain: queueLengthAfterDrain,
  caption_count: tacticalRadio.captions.length,
  spoken: spoken,
  muted_caption_delta: mutedCaptionDelta,
  muted_speech_delta: mutedSpeechDelta,
  final_muted: tacticalRadio.muted,
  primary_callout: primaryCallout,
  secondary_callout: secondaryCallout,
  frame_high_water: Number(
    tacticalRadio.frameHighWater[operationKey] || -1
  ),
  timeline_high_water: Number(
    tacticalRadio.timelineHighWater[operationKey] || 0
  )
}));
"""
    return preamble + "\n" + runtime_source + "\n" + suffix


def _validate_tactical_radio_result(
    result: object,
    *,
    primary_event: Mapping[str, object],
    secondary_event: Mapping[str, object],
) -> dict[str, object]:
    if not isinstance(result, dict) or set(result) != _TACTICAL_RADIO_RESULT_FIELDS:
        raise ValueError("production Tactical Radio result field set is invalid")
    schema_version = result.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
        raise ValueError("production Tactical Radio result schema is unsupported")
    expected_bools = {
        "primary_accepted": True,
        "secondary_accepted": True,
        "duplicate_primary_accepted": False,
        "duplicate_secondary_accepted": False,
        "stale_accepted": False,
        "muted_accepted": True,
        "final_muted": False,
    }
    if any(result.get(key) is not value for key, value in expected_bools.items()):
        raise ValueError("production Tactical Radio admission behavior is invalid")
    expected_counts = {
        "queue_length_before_drain": 1,
        "queue_length_after_drain": 0,
        "caption_count": 3,
        "muted_caption_delta": 1,
        "muted_speech_delta": 0,
        "production_announcement_calls": 5,
    }
    if any(
        type(result.get(key)) is not int or result.get(key) != value
        for key, value in expected_counts.items()
    ):
        raise ValueError("production Tactical Radio queue/mute behavior is invalid")
    spoken = result.get("spoken")
    if (
        not isinstance(spoken, list)
        or len(spoken) != 2
        or any(not isinstance(item, str) or not item for item in spoken)
    ):
        raise ValueError("production Tactical Radio speech behavior is invalid")
    primary_callout = result.get("primary_callout")
    secondary_callout = result.get("secondary_callout")
    if not isinstance(primary_callout, Mapping) or not isinstance(
        secondary_callout,
        Mapping,
    ):
        raise ValueError("production Tactical Radio callout evidence is missing")
    for callout in (primary_callout, secondary_callout):
        caption = callout.get("caption")
        speech = callout.get("speech")
        dedupe_key = callout.get("dedupeKey")
        if (
            not isinstance(caption, str)
            or not caption
            or not isinstance(speech, str)
            or not speech
            or not isinstance(dedupe_key, str)
            or not dedupe_key
            or callout.get("fromReplay") is not True
        ):
            raise ValueError("production Tactical Radio callout is malformed")
    frame_high_water = result.get("frame_high_water")
    timeline_high_water = result.get("timeline_high_water")
    if (
        type(frame_high_water) is not int
        or frame_high_water != secondary_event.get("game_frame")
    ):
        raise ValueError("production Tactical Radio frame high-water is invalid")
    if (
        type(timeline_high_water) is not int
        or timeline_high_water != secondary_event.get("timeline_seq")
    ):
        raise ValueError("production Tactical Radio timeline high-water is invalid")
    if str(primary_event.get("kind", "")).lower() not in (
        str(primary_callout.get("dedupeKey", "")).lower()
    ):
        raise ValueError("primary Tactical Radio callout lost lifecycle identity")
    if str(secondary_event.get("kind", "")).lower() not in (
        str(secondary_callout.get("dedupeKey", "")).lower()
    ):
        raise ValueError("secondary Tactical Radio callout lost lifecycle identity")
    return deepcopy(result)


def _operation_row_identity(
    execution: _JourneyExecution,
    row: Mapping[str, object],
) -> dict[str, object]:
    operation_id = str(row.get("operation_id", ""))
    generation = int(row.get("generation", 0) or 0)
    family = _mapping_sequence(row.get("family_evidence"))
    update_id = (
        str(family[0].get("update_id", ""))
        if family
        else ""
    )
    if not update_id:
        for update in reversed(execution.compiled_updates):
            try:
                operation = _operation_by_id(update, operation_id)
            except ValueError:
                continue
            if int(operation.get("generation", 0) or 0) == generation:
                update_id = str(update["update_id"])
                break
    if not update_id:
        raise ValueError(f"native operation row lacks update identity: {operation_id}")
    return {
        "update_id": update_id,
        "operation_id": operation_id,
        "generation": generation,
    }


def _expected_tactical_effects(
    events: Sequence[Mapping[str, object]],
) -> list[str]:
    effects: set[str] = set()
    for event in events:
        event_type = str(event.get("event_type", ""))
        payload = event.get("payload")
        action = (
            str(payload.get("action", ""))
            if isinstance(payload, Mapping)
            else ""
        )
        if event_type == "ability_effect":
            effects.add("ability_cast")
        elif event_type == "engagement":
            effects.add("hold")
        elif event_type == "movement":
            effects.add("scout" if action.endswith(":scout") else "pressure")
    return sorted(effects)


def _finalize_events(execution: _JourneyExecution) -> None:
    execution.events.sort(
        key=lambda event: (
            int(cast(Mapping[str, object], event.get("identity", {})).get(
                "game_frame",
                0,
            )),
            int(event.get("_order", 10)),
            int(event.get("_native_seq", 0)),
            str(event.get("event_type", "")),
            str(
                cast(Mapping[str, object], event.get("identity", {})).get(
                    "operation_id",
                    "",
                )
            ),
        )
    )
    for index, event in enumerate(execution.events, start=1):
        event.pop("_order", None)
        event.pop("_native_seq", None)
        event["seq"] = index


def verify_pre_live_journey_events(
    spec: Mapping[str, object],
    events: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    blockers: list[str] = []
    normalized = [dict(event) for event in events if isinstance(event, Mapping)]
    if len(normalized) != len(events):
        blockers.append("raw event stream contains a non-object event")
    if [event.get("seq") for event in normalized] != list(
        range(1, len(normalized) + 1)
    ):
        blockers.append("raw event sequence is not contiguous")
    event_types = [str(event.get("event_type", "")) for event in normalized]
    required_types = [
        str(item)
        for item in cast(Sequence[object], spec.get("expected_raw_event_types", ()))
    ]
    missing_types = sorted(set(required_types) - set(event_types))
    if missing_types:
        blockers.append("missing raw event types: " + ", ".join(missing_types))
    _verify_event_identities(spec, normalized, blockers)
    stop = cast(Mapping[str, object], spec.get("stop_condition", {}))
    stop_type = str(stop.get("type", ""))
    if stop_type == "forbidden_submission_and_state_preserved":
        _verify_forbidden_submission(stop, normalized, blockers)
    elif stop_type in {
        "all_operations_effect_observed",
        "effect_after_prerequisite_convergence",
        "matching_effect_observed",
        "updated_generation_effect_observed",
        "offense_restored_after_defense",
    }:
        _verify_required_effects(
            spec,
            stop_type,
            stop,
            normalized,
            blockers,
        )
    elif stop_type == "transfer_applied_and_siblings_preserved":
        _verify_transfer(stop, normalized, blockers)
    elif stop_type == "selected_operation_cancelled_sibling_active":
        _verify_selective_cancellation(stop, normalized, blockers)
    elif stop_type == "affected_offense_preempted":
        _verify_emergency_preemption(stop, normalized, blockers)
    elif stop_type == "all_terran_families_accounted":
        blockers.extend(_verify_all_terran_events(spec, normalized))
    elif stop_type == "replayed_events_counted_once":
        _verify_reconnect(spec, normalized, blockers)
    elif stop_type == "projection_identity_consistent":
        _verify_projection_identity(normalized, blockers)
    else:
        blockers.append(f"unsupported stop condition: {stop_type}")
    return {
        "id": str(spec.get("id", "")),
        "title": str(spec.get("title", "")),
        "event_count": len(normalized),
        "event_types": sorted(set(event_types)),
        "ownership_snapshot_count": sum(
            event.get("event_type") == "ownership_snapshot"
            for event in normalized
        ),
        "blockers": blockers,
        "ok": not blockers,
        "status": "passed" if not blockers else "failed",
    }


def _observed_generation_activation_timeline(
    events: Sequence[Mapping[str, object]],
) -> dict[str, list[tuple[int, int]]]:
    timeline: dict[str, list[tuple[int, int]]] = {}
    for event in events:
        event_type = event.get("event_type")
        identity = event.get("identity")
        payload = event.get("payload")
        if (
            type(event_type) is not str
            or not isinstance(identity, Mapping)
            or not isinstance(payload, Mapping)
        ):
            continue
        activates = event_type in _GENERATION_ACTIVATION_EVENT_TYPES
        if event_type == "launch_decision":
            activates = identity.get("stage") == "launch_admitted"
        elif event_type == "transfer":
            activates = payload.get("resolution") == "applied"
        if not activates:
            continue
        operation_id = identity.get("operation_id")
        generation = identity.get("generation")
        frame = identity.get("game_frame")
        if (
            type(operation_id) is not str
            or not operation_id
            or type(generation) is not int
            or generation <= 0
            or type(frame) is not int
            or frame < 0
        ):
            continue
        activation = (frame, generation)
        entries = timeline.setdefault(operation_id, [])
        if activation not in entries:
            entries.append(activation)
    return {
        operation_id: sorted(entries)
        for operation_id, entries in timeline.items()
    }


def _verify_event_identities(
    spec: Mapping[str, object],
    events: Sequence[Mapping[str, object]],
    blockers: list[str],
) -> None:
    try:
        compiler_execution = _JourneyExecution(spec)
        compiler_input = _compile_native_input(compiler_execution)
        allowed_identities = _compiler_requested_operation_identities(
            compiler_input
        )
    except (KeyError, TypeError, ValueError) as exc:
        blockers.append(f"compiler identity derivation failed closed: {exc}")
        allowed_identities = set()
    generation_timeline = _observed_generation_activation_timeline(events)
    raw_inputs = cast(Sequence[Mapping[str, object]], spec.get("ordered_inputs", ()))
    start_frame = int(raw_inputs[0]["frame"]) if raw_inputs else 0
    timeout = int(spec.get("timeout_frames", 0) or 0)
    last_frame: dict[tuple[str, str, int], int] = {}
    submissions: dict[tuple[str, str, int, str], list[tuple[int, str]]] = {}
    for event in events:
        event_type = str(event.get("event_type", ""))
        identity = event.get("identity")
        payload = event.get("payload")
        if not isinstance(identity, Mapping) or not isinstance(payload, Mapping):
            blockers.append(f"{event_type or '<missing>'} has malformed evidence")
            continue
        update_id = str(identity.get("update_id", ""))
        operation_id = str(identity.get("operation_id", ""))
        generation = identity.get("generation")
        stage = str(identity.get("stage", ""))
        frame = identity.get("game_frame")
        if (
            not update_id
            or type(generation) is not int
            or generation < 0
            or not stage
            or type(frame) is not int
            or frame < 0
        ):
            blockers.append(f"{event_type} has invalid canonical identity")
            continue
        allowed_stages = _CANONICAL_EVENT_STAGES.get(event_type)
        if allowed_stages is None or stage not in allowed_stages:
            blockers.append(
                f"{event_type or '<missing>'} has non-canonical stage: {stage}"
            )
        if timeout and frame > start_frame + timeout:
            blockers.append("journey exceeded its deterministic frame timeout")
        if event_type in _OPERATION_IDENTITY_EVENT_TYPES and (
            not operation_id or generation <= 0
        ):
            blockers.append(f"{event_type} lacks operation identity")
        if operation_id and (update_id, operation_id, generation) not in (
            allowed_identities
        ):
            blockers.append(
                f"{event_type} is not bound to a compiler-requested "
                "operation identity"
            )
        active_generation = max(
            (
                candidate_generation
                for activation_frame, candidate_generation in (
                    generation_timeline.get(operation_id, ())
                )
                if activation_frame <= frame
            ),
            default=0,
        )
        if (
            event_type in _GENERATION_LIFECYCLE_EVENT_TYPES
            and operation_id
            and active_generation > generation
        ):
            blockers.append(
                f"{event_type} uses superseded generation "
                f"{operation_id}#{generation} after #{active_generation} "
                "became active"
            )
        key = (update_id, operation_id, generation)
        previous = last_frame.get(key, -1)
        if frame < previous:
            blockers.append(
                f"stale/regressive frame for {update_id}/{operation_id}#{generation}"
            )
        last_frame[key] = max(previous, frame)
        action = str(payload.get("action", ""))
        lifecycle = (*key, action)
        if event_type == "submission":
            submissions.setdefault(lifecycle, []).append((frame, stage))
        if event_type in _EFFECT_EVENT_TYPES:
            matching = submissions.get(lifecycle, [])
            if not matching:
                blockers.append(
                    f"{event_type} lacks a matching prior submission for "
                    f"{update_id}/{operation_id}#{generation}:{action}"
                )
            elif min(submission[0] for submission in matching) > frame:
                blockers.append(f"{event_type} predates its matching submission")
            elif not any(
                submission_stage == "submitted"
                and submission_frame <= frame
                for submission_frame, submission_stage in matching
            ):
                blockers.append(
                    f"{event_type} lacks a canonical submitted-to-effect transition"
                )
            if event_type == "ability_effect" and action.startswith("move"):
                blockers.append("ability movement was counted as an ability effect")
        if event_type == "ownership_snapshot":
            owners = payload.get("owners")
            if not isinstance(owners, Mapping):
                blockers.append("ownership snapshot owners must be an object")
                continue
            owner_by_tag: dict[int, str] = {}
            for owner, raw_tags in owners.items():
                if not isinstance(raw_tags, Sequence) or isinstance(
                    raw_tags,
                    (str, bytes, bytearray),
                ):
                    blockers.append("ownership snapshot tags must be arrays")
                    continue
                for raw_tag in raw_tags:
                    if type(raw_tag) is not int or raw_tag <= 0:
                        blockers.append("ownership snapshot has invalid unit tag")
                    elif (
                        raw_tag in owner_by_tag
                        and owner_by_tag[raw_tag] != str(owner)
                    ):
                        blockers.append(f"duplicate owner for unit tag {raw_tag}")
                    else:
                        owner_by_tag[raw_tag] = str(owner)
    _verify_production_receipt_bindings(events, blockers)


def _verify_production_receipt_bindings(
    events: Sequence[Mapping[str, object]],
    blockers: list[str],
) -> None:
    ownership_receipts: list[dict[str, object]] = []
    squad_receipts: list[dict[str, object]] = []
    submission_receipts: list[dict[str, object]] = []
    try:
        for event in events:
            if event.get("event_type") != "production_path_receipt":
                continue
            identity = event.get("identity")
            payload = event.get("payload")
            if not isinstance(identity, Mapping) or not isinstance(
                payload,
                Mapping,
            ):
                raise ValueError("production receipt event is malformed")
            entrypoint = str(payload.get("entrypoint", "") or "")
            if entrypoint == "voiProductionAssignOperationOwner":
                _validate_native_ownership_receipt_event(event)
                ownership_receipts.append(dict(event))
            elif entrypoint == "voiProductionIssueSquadOrder":
                _validate_native_squad_receipt_payload(
                    payload,
                    identity=identity,
                    event_payload=True,
                )
                squad_receipts.append(dict(payload))
            elif entrypoint == "voiProductionSubmitSc2Action":
                _validate_native_submission_receipt_payload(
                    payload,
                    identity=identity,
                    event_payload=True,
                )
                submission_receipts.append(dict(payload))
            else:
                raise ValueError("production receipt entrypoint is unsupported")
        squad_ids = [str(receipt["receipt_id"]) for receipt in squad_receipts]
        submission_ids = [
            str(receipt["submission_id"]) for receipt in submission_receipts
        ]
        if len(squad_ids) != len(set(squad_ids)):
            raise ValueError("squad-order receipt id is duplicated")
        if len(submission_ids) != len(set(submission_ids)):
            raise ValueError("SC2 submission id is duplicated")
        _validate_native_ownership_causality(
            list(events),
            ownership_receipts,
        )
        _validate_native_submission_causality(
            list(events),
            squad_receipts,
            submission_receipts,
        )
    except (KeyError, TypeError, ValueError) as exc:
        blockers.append(f"production receipt binding invalid: {exc}")


def _verify_forbidden_submission(
    stop: Mapping[str, object],
    events: Sequence[Mapping[str, object]],
    blockers: list[str],
) -> None:
    rejections = [event for event in events if event.get("event_type") == "rejection"]
    if not rejections:
        blockers.append("forbidden action was not rejected")
        return
    rejected = {
        (
            str(cast(Mapping[str, object], event["identity"]).get("update_id", "")),
            str(cast(Mapping[str, object], event["identity"]).get("operation_id", "")),
            int(cast(Mapping[str, object], event["identity"]).get("generation", 0)),
        )
        for event in rejections
    }
    if any(
        event.get("event_type") == "submission"
        and (
            str(cast(Mapping[str, object], event["identity"]).get("update_id", "")),
            str(cast(Mapping[str, object], event["identity"]).get("operation_id", "")),
            int(cast(Mapping[str, object], event["identity"]).get("generation", 0)),
        )
        in rejected
        for event in events
        if isinstance(event.get("identity"), Mapping)
    ):
        blockers.append("forbidden action reached the submission adapter")
    expected_reason = str(stop.get("expected_rejection_reason", "") or "")
    if expected_reason and not any(
        cast(Mapping[str, object], event.get("payload", {})).get("reason")
        == expected_reason
        for event in rejections
    ):
        blockers.append("forbidden action was rejected for the wrong reason")
    states = _before_after_states(events)
    if states is None:
        blockers.append("forbidden action lacks native before/after state")
        return
    fields = stop.get(
        "preserved_state_fields",
        ["units", "owners", "operations", "structures", "base_threatened"],
    )
    if not isinstance(fields, Sequence) or isinstance(
        fields,
        (str, bytes, bytearray),
    ):
        blockers.append("preserved_state_fields is invalid")
        return
    before, after = states
    before_state = dict(before)
    after_state = dict(after)
    before_state.pop("frame", None)
    after_state.pop("frame", None)
    if canonical_json_bytes(before_state) != canonical_json_bytes(after_state):
        blockers.append(
            "forbidden action did not preserve byte-equivalent runtime state"
        )
    if any(before.get(str(field)) != after.get(str(field)) for field in fields):
        blockers.append("forbidden action did not preserve runtime state")
    active_ids = stop.get("preserved_active_operation_ids", ())
    if not isinstance(active_ids, Sequence) or isinstance(
        active_ids,
        (str, bytes, bytearray),
    ):
        blockers.append("preserved_active_operation_ids is invalid")
        return
    for state in (before, after):
        operations = {
            str(row.get("operation_id", "")): row
            for row in _mapping_sequence(state.get("operations"))
        }
        if any(
            operations.get(str(operation_id), {}).get("active") is not True
            for operation_id in active_ids
        ):
            blockers.append(
                "forbidden action did not preserve both active operations"
            )
            break


def _verify_required_effects(
    spec: Mapping[str, object],
    stop_type: str,
    stop: Mapping[str, object],
    events: Sequence[Mapping[str, object]],
    blockers: list[str],
) -> None:
    effects = [event for event in events if event.get("event_type") in _EFFECT_EVENT_TYPES]
    keys = {_event_action_binding(event) for event in effects}
    if len(keys) < int(stop.get("count", 1) or 1):
        blockers.append("required matching effects were not observed")
    required_bindings = _required_effect_identities(spec, stop_type)
    if not required_bindings.issubset(keys):
        blockers.append(
            "required compiler-requested operation actions lack exact "
            "effect coverage"
        )
    if stop_type == "effect_after_prerequisite_convergence":
        waits = _event_frames(events, "prerequisite_wait")
        effect_frames = _event_frames(effects)
        if not waits or not effect_frames or min(effect_frames) <= max(waits):
            blockers.append("effect did not follow prerequisite convergence")
    if stop_type == "updated_generation_effect_observed":
        generations = {
            (
                str(cast(Mapping[str, object], event["identity"])["update_id"]),
                str(cast(Mapping[str, object], event["identity"])["operation_id"]),
                int(cast(Mapping[str, object], event["identity"])["generation"]),
            )
            for event in events
            if event.get("event_type") == "generation_change"
        }
        if not any(key[:3] in generations for key in keys):
            blockers.append("updated generation lacks a matching effect")
    if stop_type == "offense_restored_after_defense":
        defenses = [
            (_event_action_binding(event)[:3], _event_frame(event))
            for event in events
            if event.get("event_type") == "autonomous_defense"
            and cast(Mapping[str, object], event["payload"]).get("action")
            == "defend"
        ]
        restorations = [
            (_event_action_binding(event)[:3], _event_frame(event))
            for event in events
            if event.get("event_type") == "autonomous_defense"
            and cast(Mapping[str, object], event["payload"]).get("action")
            == "restore_offense"
        ]
        if not defenses:
            blockers.append("autonomous defense was not observed")
        valid_restorations = [
            (identity, frame)
            for identity, frame in restorations
            if any(
                defense_identity == identity and defense_frame < frame
                for defense_identity, defense_frame in defenses
            )
        ]
        if restorations and not valid_restorations:
            blockers.append("offense restoration lacks prior autonomous defense")
        if not valid_restorations or not any(
            effect_binding[:3] == identity
            and effect_binding in required_bindings
            and _event_frame(effect) > restoration_frame
            for identity, restoration_frame in valid_restorations
            for effect in effects
            for effect_binding in [_event_action_binding(effect)]
        ):
            blockers.append("offense effect did not follow defense restoration")


def _event_action_binding(
    event: Mapping[str, object],
) -> tuple[str, str, int, str]:
    identity = cast(Mapping[str, object], event["identity"])
    payload = cast(Mapping[str, object], event["payload"])
    return (
        str(identity["update_id"]),
        str(identity["operation_id"]),
        int(identity["generation"]),
        str(payload.get("action", "")),
    )


def _event_frame(event: Mapping[str, object]) -> int:
    return int(cast(Mapping[str, object], event["identity"])["game_frame"])


def _required_effect_identities(
    spec: Mapping[str, object],
    stop_type: str,
) -> set[tuple[str, str, int, str]]:
    execution = _JourneyExecution(spec)
    native_input = _compile_native_input(execution)
    policy_updates = [
        cast(Mapping[str, object], step["update"])
        for step in cast(
            Sequence[Mapping[str, object]],
            native_input["steps"],
        )
        if step.get("kind") == "policy_update"
    ]
    if stop_type == "updated_generation_effect_observed":
        previous: dict[str, int] = {}
        changed: set[tuple[str, str, int, str]] = set()
        for update in policy_updates:
            operations = {
                str(operation["operation_id"]): operation
                for operation in _update_operations(update)
            }
            current = {
                operation_id: int(operation["generation"])
                for operation_id, operation in operations.items()
            }
            changed.update(
                (
                    str(update["update_id"]),
                    operation_id,
                    generation,
                    _compiled_operation_action(operations[operation_id]),
                )
                for operation_id, generation in current.items()
                if operation_id in previous
                and generation > previous[operation_id]
            )
            previous = current
        return changed
    for update in reversed(policy_updates):
        operations = _update_operations(update)
        if operations:
            return {
                (
                    str(update["update_id"]),
                    str(operation["operation_id"]),
                    int(operation["generation"]),
                    _compiled_operation_action(operation),
                )
                for operation in operations
            }
    return set()


def _verify_transfer(
    stop: Mapping[str, object],
    events: Sequence[Mapping[str, object]],
    blockers: list[str],
) -> None:
    transfers = [
        event
        for event in events
        if event.get("event_type") == "transfer"
        and cast(Mapping[str, object], event.get("payload", {})).get("resolution")
        == "applied"
    ]
    if len(transfers) != 1:
        blockers.append("exactly one applied transfer is required")
    states = _before_after_states(events)
    if states is None:
        blockers.append("transfer lacks native before/after state")
        return
    before, after = states
    before_owners = cast(Mapping[str, object], before.get("owners", {}))
    after_owners = cast(Mapping[str, object], after.get("owners", {}))
    source = str(stop.get("source_operation_id", ""))
    destination = str(stop.get("destination_operation_id", ""))
    if len(transfers) != 1:
        return
    transfer = transfers[0]
    identity = cast(Mapping[str, object], transfer.get("identity", {}))
    payload = cast(Mapping[str, object], transfer.get("payload", {}))
    expected_payload_fields = {
        "action",
        "counterpart_operation_id",
        "destination_generation_after",
        "destination_generation_before",
        "destination_unit_tags_after",
        "destination_unit_tags_before",
        "resolution",
        "source_generation_after",
        "source_generation_before",
        "source_unit_tags_after",
        "source_unit_tags_before",
        "unit_tags",
    }
    try:
        selected_tags = _native_unit_tags(payload.get("unit_tags"))
        before_source_tags = _native_optional_unit_tags(
            before_owners.get(source, ())
        )
        before_destination_tags = _native_optional_unit_tags(
            before_owners.get(destination, ())
        )
        after_source_tags = _native_optional_unit_tags(
            after_owners.get(source, ())
        )
        after_destination_tags = _native_optional_unit_tags(
            after_owners.get(destination, ())
        )
        before_operations = _state_operation_rows(before)
        after_operations = _state_operation_rows(after)
        before_source = before_operations[source]
        before_destination = before_operations[destination]
        after_source = after_operations[source]
        after_destination = after_operations[destination]
        source_before_generation = int(before_source["generation"])
        source_after_generation = int(after_source["generation"])
        destination_before_generation = int(
            before_destination["generation"]
        )
        destination_after_generation = int(after_destination["generation"])
        if (
            set(payload) != expected_payload_fields
            or payload.get("action") != "transfer_out"
            or payload.get("counterpart_operation_id") != destination
            or identity.get("operation_id") != source
            or identity.get("generation") != source_after_generation
            or payload.get("source_generation_before")
            != source_before_generation
            or payload.get("source_generation_after")
            != source_after_generation
            or payload.get("destination_generation_before")
            != destination_before_generation
            or payload.get("destination_generation_after")
            != destination_after_generation
            or payload.get("source_unit_tags_before")
            != list(before_source_tags)
            or payload.get("source_unit_tags_after")
            != list(after_source_tags)
            or payload.get("destination_unit_tags_before")
            != list(before_destination_tags)
            or payload.get("destination_unit_tags_after")
            != list(after_destination_tags)
            or source_after_generation <= source_before_generation
            or destination_after_generation
            <= destination_before_generation
            or tuple(
                tag
                for tag in before_source_tags
                if tag not in selected_tags
            )
            != after_source_tags
            or tuple(sorted((*before_destination_tags, *selected_tags)))
            != after_destination_tags
            or tuple(
                tag
                for tag in before_source_tags
                if tag not in after_source_tags
            )
            != selected_tags
            or tuple(
                tag
                for tag in after_destination_tags
                if tag not in before_destination_tags
            )
            != selected_tags
            or _native_optional_unit_tags(
                before_source.get("assigned_unit_tags")
            )
            != before_source_tags
            or _native_optional_unit_tags(
                before_destination.get("assigned_unit_tags")
            )
            != before_destination_tags
            or _native_optional_unit_tags(
                after_source.get("assigned_unit_tags")
            )
            != after_source_tags
            or _native_optional_unit_tags(
                after_destination.get("assigned_unit_tags")
            )
            != after_destination_tags
            or not _state_owner_bindings_match(
                before,
                before_source_tags,
                source,
                source_before_generation,
            )
            or not _state_owner_bindings_match(
                before,
                before_destination_tags,
                destination,
                destination_before_generation,
            )
            or not _state_owner_bindings_match(
                after,
                after_source_tags,
                source,
                source_after_generation,
            )
            or not _state_owner_bindings_match(
                after,
                after_destination_tags,
                destination,
                destination_after_generation,
            )
        ):
            blockers.append(
                "transfer tags and generations are not exactly bound"
            )
    except (KeyError, TypeError, ValueError):
        blockers.append("transfer exact ownership evidence is malformed")
    for sibling in cast(Sequence[object], stop.get("preserved_operation_ids", ())):
        sibling_id = str(sibling)
        try:
            before_sibling = _state_operation_rows(before)[sibling_id]
            after_sibling = _state_operation_rows(after)[sibling_id]
            sibling_tags = _native_optional_unit_tags(
                before_owners.get(sibling_id, ())
            )
            sibling_generation = int(before_sibling["generation"])
            preserved = (
                before_owners.get(sibling_id) == after_owners.get(sibling_id)
                and before_sibling.get("active") is True
                and after_sibling.get("active") is True
                and after_sibling.get("generation") == sibling_generation
                and before_sibling.get("assigned_unit_tags")
                == after_sibling.get("assigned_unit_tags")
                and _state_owner_bindings_match(
                    before,
                    sibling_tags,
                    sibling_id,
                    sibling_generation,
                )
                and _state_owner_bindings_match(
                    after,
                    sibling_tags,
                    sibling_id,
                    sibling_generation,
                )
            )
        except (KeyError, TypeError, ValueError):
            preserved = False
        if not preserved:
            blockers.append("transfer did not preserve sibling ownership")


def _verify_selective_cancellation(
    stop: Mapping[str, object],
    events: Sequence[Mapping[str, object]],
    blockers: list[str],
) -> None:
    cancellations = [
        event for event in events if event.get("event_type") == "cancellation"
    ]
    if len(cancellations) != 1:
        blockers.append("exactly one selective cancellation is required")
    states = _before_after_states(events)
    if states is None:
        blockers.append("selective cancellation lacks native state evidence")
        return
    before, after = states
    selected_id = str(stop.get("selected_operation_id", ""))
    sibling_id = str(stop.get("sibling_operation_id", ""))
    if len(cancellations) != 1:
        return
    cancellation = cancellations[0]
    identity = cast(Mapping[str, object], cancellation.get("identity", {}))
    payload = cast(Mapping[str, object], cancellation.get("payload", {}))
    try:
        before_operations = _state_operation_rows(before)
        after_operations = _state_operation_rows(after)
        selected_before = before_operations[selected_id]
        selected_after = after_operations[selected_id]
        sibling_before = before_operations[sibling_id]
        sibling_after = after_operations[sibling_id]
        selected_tags = _native_optional_unit_tags(
            cast(Mapping[str, object], before.get("owners", {})).get(
                selected_id,
                (),
            )
        )
        released_tags = _native_optional_unit_tags(
            payload.get("released_unit_tags")
        )
        selected_generation = int(selected_before["generation"])
        sibling_generation = int(sibling_before["generation"])
        sibling_tags = _native_optional_unit_tags(
            cast(Mapping[str, object], before.get("owners", {})).get(
                sibling_id,
                (),
            )
        )
        after_owners = cast(Mapping[str, object], after.get("owners", {}))
        after_bindings = cast(
            Mapping[str, object],
            after.get("owner_bindings", {}),
        )
        selected_released = (
            set(payload)
            == {
                "action",
                "reason",
                "released_generation",
                "released_unit_tags",
            }
            and identity.get("operation_id") == selected_id
            and identity.get("generation") == selected_generation
            and payload.get("action") == "cancel"
            and payload.get("reason") == "cancelled_by_user"
            and payload.get("released_generation") == selected_generation
            and released_tags == selected_tags
            and selected_after.get("active") is False
            and _native_optional_unit_tags(
                selected_after.get("assigned_unit_tags")
            )
            == ()
            and _native_optional_unit_tags(
                after_owners.get(selected_id, ())
            )
            == ()
            and all(str(tag) not in after_bindings for tag in released_tags)
            and not any(
                isinstance(binding, Mapping)
                and binding.get("operation_id") == selected_id
                for binding in after_bindings.values()
            )
        )
        sibling_preserved = (
            sibling_before.get("active") is True
            and sibling_after.get("active") is True
            and sibling_after.get("generation") == sibling_generation
            and sibling_before.get("assigned_unit_tags")
            == sibling_after.get("assigned_unit_tags")
            and cast(Mapping[str, object], before.get("owners", {})).get(
                sibling_id
            )
            == after_owners.get(sibling_id)
            and _state_owner_bindings_match(
                before,
                sibling_tags,
                sibling_id,
                sibling_generation,
            )
            and _state_owner_bindings_match(
                after,
                sibling_tags,
                sibling_id,
                sibling_generation,
            )
        )
    except (KeyError, TypeError, ValueError):
        selected_released = False
        sibling_preserved = False
    if not selected_released:
        blockers.append(
            "selective cancellation did not release exact selected ownership"
        )
    if not sibling_preserved:
        blockers.append("selective cancellation removed an unrelated operation")


def _verify_emergency_preemption(
    stop: Mapping[str, object],
    events: Sequence[Mapping[str, object]],
    blockers: list[str],
) -> None:
    preemptions = [
        event for event in events if event.get("event_type") == "preemption"
    ]
    expected_count = int(stop.get("count", 1) or 1)
    if len(preemptions) != expected_count:
        blockers.append("emergency did not preempt the affected offense")
        return
    for preemption in preemptions:
        identity = cast(Mapping[str, object], preemption.get("identity", {}))
        payload = cast(Mapping[str, object], preemption.get("payload", {}))
        update_id = str(identity.get("update_id", ""))
        operation_id = str(identity.get("operation_id", ""))
        generation = int(identity.get("generation", 0) or 0)
        frame = int(identity.get("game_frame", 0) or 0)
        if (
            identity.get("stage") != "preempted"
            or payload.get("action") != "retreat"
            or payload.get("reason") != "emergency_policy"
        ):
            blockers.append("emergency preemption evidence is not canonical")
        prior_attack = any(
            event.get("event_type") == "submission"
            and str(
                cast(Mapping[str, object], event.get("identity", {})).get(
                    "operation_id",
                    "",
                )
            )
            == operation_id
            and int(
                cast(Mapping[str, object], event.get("identity", {})).get(
                    "game_frame",
                    0,
                )
                or 0
            )
            <= frame
            and cast(Mapping[str, object], event.get("payload", {})).get(
                "action"
            )
            == "squad_order:attack"
            for event in events
        )
        if not prior_attack:
            blockers.append("emergency preemption lacks affected offense evidence")
        retreat_lifecycle = [
            event
            for event in events
            if str(
                cast(Mapping[str, object], event.get("identity", {})).get(
                    "update_id",
                    "",
                )
            )
            == update_id
            and str(
                cast(Mapping[str, object], event.get("identity", {})).get(
                    "operation_id",
                    "",
                )
            )
            == operation_id
            and int(
                cast(Mapping[str, object], event.get("identity", {})).get(
                    "generation",
                    0,
                )
                or 0
            )
            == generation
            and cast(Mapping[str, object], event.get("payload", {})).get(
                "action"
            )
            == "retreat"
        ]
        if not any(
            event.get("event_type") == "submission"
            for event in retreat_lifecycle
        ):
            blockers.append("emergency retreat was not submitted")
        if not any(
            event.get("event_type") == "movement"
            for event in retreat_lifecycle
        ):
            blockers.append("emergency retreat effect was not observed")


def _verify_all_terran_events(
    spec: Mapping[str, object],
    events: Sequence[Mapping[str, object]],
) -> list[str]:
    blockers: list[str] = []
    expected = {
        str(item["family"]): {
            str(ability)
            for ability in cast(Sequence[object], item["abilities"])
        }
        for item in all_terran_capability_matrix()
    }
    try:
        bindings = _all_terran_compiler_bindings(spec)
    except (KeyError, TypeError, ValueError) as exc:
        return [f"all-Terran compiler binding failed closed: {exc}"]

    expected_pairs = {
        (family, ability)
        for family, abilities in expected.items()
        for ability in abilities
    }
    if {
        (str(binding["family"]), str(binding["ability"]))
        for binding in bindings.values()
    } != expected_pairs:
        blockers.append("all-Terran compiler bindings do not match the matrix")

    observed: dict[
        tuple[str, str, int],
        dict[str, list[Mapping[str, object]]],
    ] = {
        identity: {}
        for identity in bindings
    }
    for event in events:
        payload = event.get("payload")
        identity = event.get("identity")
        if not isinstance(payload, Mapping) or not isinstance(identity, Mapping):
            continue
        key = (
            str(identity.get("update_id", "")),
            str(identity.get("operation_id", "")),
            int(identity.get("generation", 0) or 0),
        )
        binding = bindings.get(key)
        if binding is None:
            continue
        event_type = str(event.get("event_type", "") or "")
        observed[key].setdefault(event_type, []).append(event)
        family = str(binding["family"])
        ability = str(binding["ability"])
        unit_type = str(binding["unit_type"])
        expected_action = str(binding["action"])
        action = str(payload.get("action", ""))
        if "family" in payload and payload.get("family") != family:
            blockers.append(f"{key[1]} family label is not compiler-bound")
        if "ability" in payload and payload.get("ability") != ability:
            blockers.append(f"{key[1]} ability label is not compiler-bound")
        if payload.get("ability_name") not in (None, "", ability):
            blockers.append(f"{key[1]} ability_name is not compiler-bound")
        if event_type in {
            "terran_lowering",
            "family_action_attempt",
            "submission",
            "ability_effect",
        } and payload.get("unit_type") != unit_type:
            blockers.append(f"{family}/{ability} unit type is not compiler-bound")
        if (
            event_type == "production_path_receipt"
            and payload.get("entrypoint") == "voiProductionSubmitSc2Action"
            and payload.get("unit_type") != unit_type
        ):
            blockers.append(
                f"{family}/{ability} receipt unit type is not compiler-bound"
            )
        if event_type in {
            "terran_lowering",
            "family_action_attempt",
            "submission",
            "ability_effect",
            "production_decision",
            "prerequisite_wait",
        } and action != expected_action:
            blockers.append(f"{family}/{ability} action is not compiler-bound")

    for identity, binding in bindings.items():
        family = str(binding["family"])
        ability = str(binding["ability"])
        rows = observed[identity]
        lowerings = rows.get("terran_lowering", [])
        attempts = rows.get("family_action_attempt", [])
        submissions = rows.get("submission", [])
        effects = rows.get("ability_effect", [])
        decisions = rows.get("production_decision", [])
        waits = rows.get("prerequisite_wait", [])
        rejections = rows.get("rejection", [])
        if len(lowerings) != 1:
            blockers.append(
                f"{family}/{ability} lowering evidence is not unique"
            )
        if binding["blocked"] is True:
            expected_blocker = str(binding["blocker"])
            if attempts or submissions or effects:
                blockers.append(
                    f"{family}/{ability} blocked action reached execution"
                )
            if len(decisions) != 1 or len(waits) != 1:
                blockers.append(
                    f"{family}/{ability} lacks exact prerequisite blocker evidence"
                )
            for row in (*decisions, *waits):
                payload = cast(Mapping[str, object], row.get("payload", {}))
                if payload.get("blocker") != expected_blocker:
                    blockers.append(
                        f"{family}/{ability} blocker is not compiler-derived"
                    )
            if rejections:
                blockers.append(
                    f"{family}/{ability} used rejection instead of prerequisite wait"
                )
        else:
            if len(attempts) != 1:
                blockers.append(
                    f"{family}/{ability} family attempt is not unique"
                )
            if len(submissions) != 1:
                blockers.append(
                    f"{family}/{ability} submission is not unique"
                )
            if len(effects) != 1:
                blockers.append(f"{family}/{ability} effect is not unique")
            blocker_rows = [
                row
                for row in (*decisions, *waits, *rejections)
                if str(
                    cast(Mapping[str, object], row.get("payload", {})).get(
                        "blocker",
                        "",
                    )
                    or cast(Mapping[str, object], row.get("payload", {})).get(
                        "reason",
                        "",
                    )
                )
            ]
            if blocker_rows:
                blockers.append(
                    f"{family}/{ability} has unexpected blocker evidence"
                )
    return blockers


def _all_terran_compiler_bindings(
    spec: Mapping[str, object],
) -> dict[tuple[str, str, int], dict[str, object]]:
    execution = _JourneyExecution(spec)
    native_input = _compile_native_input(execution)
    return _all_terran_compiler_bindings_from_native_input(native_input)


def _all_terran_compiler_bindings_from_native_input(
    native_input: Mapping[str, object],
) -> dict[tuple[str, str, int], dict[str, object]]:
    initial_state = native_input.get("initial_state")
    if not isinstance(initial_state, Mapping):
        raise ValueError("all-Terran initial state is malformed")
    available: dict[tuple[str, str], int] = {}
    available_by_type: dict[str, int] = {}
    for unit in _mapping_sequence(initial_state.get("units")):
        unit_type = unit.get("unit_type")
        if type(unit_type) is not str or not unit_type:
            raise ValueError("all-Terran initial unit type is malformed")
        cloak_state = unit.get("cloak_state", "")
        if type(cloak_state) is not str:
            raise ValueError("all-Terran initial cloak state is malformed")
        key = (unit_type, cloak_state)
        available[key] = available.get(key, 0) + 1
        available_by_type[unit_type] = available_by_type.get(unit_type, 0) + 1
    bindings: dict[tuple[str, str, int], dict[str, object]] = {}
    for step in cast(
        Sequence[Mapping[str, object]],
        native_input["steps"],
    ):
        if step.get("kind") != "policy_update":
            continue
        update = cast(Mapping[str, object], step["update"])
        for operation in _update_operations(update):
            task = operation.get("tactical_task")
            requirements = _operation_requirements(operation)
            if not isinstance(task, Mapping) or not requirements:
                raise ValueError("all-Terran operation is malformed")
            ability = task.get("ability")
            unit_type = requirements[0].get("unit_type")
            required: dict[str, int] = {}
            for requirement in requirements:
                required_type = requirement.get("unit_type")
                count = requirement.get("count")
                if (
                    type(required_type) is not str
                    or not required_type
                    or type(count) is not int
                    or count <= 0
                ):
                    raise ValueError(
                        "all-Terran composition requirement is malformed"
                    )
                required[required_type] = (
                    required.get(required_type, 0) + count
                )
            if (
                type(ability) is not str
                or not ability
                or type(unit_type) is not str
                or not unit_type
            ):
                raise ValueError("all-Terran operation binding is malformed")
            caster_state = terran_ability_caster_state(ability)
            if (
                caster_state is None
                or caster_state.unit_type != unit_type
            ):
                raise ValueError(
                    "all-Terran operation uses the wrong ability caster form"
                )
            caster_count = required.get(unit_type, 0)
            caster_inventory_key = (
                caster_state.unit_type,
                caster_state.cloak_state,
            )
            if (
                available_by_type.get(unit_type, 0) >= caster_count
                and available.get(caster_inventory_key, 0) < caster_count
            ):
                raise ValueError(
                    "all-Terran operation uses the wrong ability cloak state"
                )
            blocked = any(
                (
                    available.get(caster_inventory_key, 0) < count
                    if required_type == unit_type
                    else available_by_type.get(required_type, 0) < count
                )
                for required_type, count in required.items()
            )
            if not blocked:
                for required_type, count in required.items():
                    if required_type == unit_type:
                        available[caster_inventory_key] -= count
                    available_by_type[required_type] -= count
            identity = (
                str(update["update_id"]),
                str(operation["operation_id"]),
                int(operation["generation"]),
            )
            binding = {
                "family": canonical_terran_unit_family(unit_type),
                "ability": ability,
                "unit_type": unit_type,
                "action": _compiled_operation_action(operation),
                "blocked": blocked,
                "blocker": (
                    _ALL_TERRAN_COMPOSITION_BLOCKER if blocked else ""
                ),
            }
            if identity in bindings and bindings[identity] != binding:
                raise ValueError("all-Terran operation binding is ambiguous")
            bindings[identity] = binding
    return bindings


def _verify_reconnect(
    spec: Mapping[str, object],
    events: Sequence[Mapping[str, object]],
    blockers: list[str],
) -> None:
    native_sources = [
        event
        for event in events
        if event.get("event_type") in _WEB_LIFECYCLE_EVENT_TYPES
        and isinstance(event.get("identity"), Mapping)
        and isinstance(event.get("payload"), Mapping)
    ]
    web_events = [
        event
        for event in events
        if event.get("event_type") == "web_event"
        and isinstance(event.get("identity"), Mapping)
        and isinstance(event.get("payload"), Mapping)
    ]
    web_rows: list[dict[str, object]] = []
    expected_web_fields = {
        "action",
        "event_seq",
        "logical_event_id",
        "source_event_seq",
        "source_event_type",
        "source_identity",
        "source_payload",
    }
    for event in web_events:
        identity = cast(Mapping[str, object], event["identity"])
        payload = cast(Mapping[str, object], event["payload"])
        source_identity = payload.get("source_identity")
        source_payload = payload.get("source_payload")
        source_type = str(payload.get("source_event_type", "") or "")
        source_seq = payload.get("source_event_seq")
        event_seq = payload.get("event_seq")
        logical_id = str(payload.get("logical_event_id", "") or "")
        expected_identity = (
            {
                **dict(source_identity),
                "stage": "web_event",
            }
            if isinstance(source_identity, Mapping)
            else {}
        )
        if (
            set(payload) != expected_web_fields
            or not isinstance(source_identity, Mapping)
            or not isinstance(source_payload, Mapping)
            or source_type not in _WEB_LIFECYCLE_EVENT_TYPES
            or type(source_seq) is not int
            or source_seq <= 0
            or type(event_seq) is not int
            or event_seq <= 0
            or logical_id != f"native:{source_seq}:{source_type}"
            or payload.get("action") != source_type
            or dict(identity) != expected_identity
        ):
            blockers.append(
                "web event is not exactly bound to its native source"
            )
            continue
        web_rows.append(
            {
                "event_seq": event_seq,
                "logical_event_id": logical_id,
                "source_event_seq": source_seq,
                "source_event_type": source_type,
                "source_identity": dict(source_identity),
                "source_payload": dict(source_payload),
            }
        )
    native_rows = [
        {
            "source_event_type": str(event["event_type"]),
            "source_identity": dict(
                cast(Mapping[str, object], event["identity"])
            ),
            "source_payload": dict(
                cast(Mapping[str, object], event["payload"])
            ),
        }
        for event in native_sources
    ]
    web_source_rows = [
        {
            field_name: row[field_name]
            for field_name in (
                "source_event_type",
                "source_identity",
                "source_payload",
            )
        }
        for row in web_rows
    ]
    if canonical_json_bytes(web_source_rows) != canonical_json_bytes(native_rows):
        blockers.append(
            "web events do not preserve native source order and multiplicity"
        )
    logical_ids = [
        str(row.get("logical_event_id", ""))
        for row in web_rows
    ]
    event_seqs = [row.get("event_seq") for row in web_rows]
    source_seqs = [row.get("source_event_seq") for row in web_rows]
    replay_events = [
        event
        for event in events
        if event.get("event_type") == "replay_deduplicated"
        and isinstance(event.get("identity"), Mapping)
        and isinstance(event.get("payload"), Mapping)
    ]
    reconnect_events = [
        event
        for event in events
        if event.get("event_type") == "client_reconnect"
        and isinstance(event.get("payload"), Mapping)
        and isinstance(event.get("identity"), Mapping)
    ]
    if len(reconnect_events) != 1:
        blockers.append("exactly one reconnect marker is required")
        reconnect_event: Mapping[str, object] = {}
    else:
        reconnect_event = reconnect_events[0]
    reconnect = cast(
        Mapping[str, object],
        reconnect_event.get("payload", {}),
    )
    reconnect_identity = cast(
        Mapping[str, object],
        reconnect_event.get("identity", {}),
    )
    reconnect_binding = tuple(
        reconnect_identity.get(field_name)
        for field_name in (
            "update_id",
            "operation_id",
            "generation",
            "game_frame",
        )
    )
    initial_state = spec.get("initial_state")
    expected_cursor = (
        initial_state.get("event_cursor")
        if isinstance(initial_state, Mapping)
        else None
    )
    if type(expected_cursor) is not int or expected_cursor < 0:
        blockers.append("manifest reconnect cursor is missing or invalid")
        expected_cursor = 0
    cursor = reconnect.get("after_event_seq")
    if type(cursor) is not int or cursor < 0:
        blockers.append("reconnect cursor is missing or invalid")
        cursor = 0
    elif cursor != expected_cursor:
        blockers.append(
            "reconnect cursor does not match the manifest initial cursor"
        )
    valid_source_sequences = (
        bool(web_rows)
        and all(type(event_seq) is int and event_seq > 0 for event_seq in event_seqs)
        and len(set(event_seqs)) == len(event_seqs)
        and event_seqs == sorted(event_seqs)
        and all(
            type(source_seq) is int and source_seq > 0
            for source_seq in source_seqs
        )
        and len(set(source_seqs)) == len(source_seqs)
        and source_seqs == sorted(source_seqs)
    )
    if not valid_source_sequences:
        blockers.append("web event source sequence is invalid")
    expected_rows = [
        row
        for row in web_rows
        if type(row.get("event_seq")) is int
        and cast(int, row["event_seq"]) > cursor
    ]
    expected_replay = [
        str(payload.get("logical_event_id", ""))
        for payload in expected_rows
    ]
    expected_event_seqs = [
        cast(int, payload["event_seq"])
        for payload in expected_rows
    ]
    if (
        not logical_ids
        or any(not logical_id for logical_id in logical_ids)
        or len(set(logical_ids)) != len(logical_ids)
    ):
        blockers.append("web event source contains duplicate logical events")
    replay_batches = [
        event
        for event in events
        if event.get("event_type") == "replay_batch"
        and isinstance(event.get("payload"), Mapping)
        and isinstance(event.get("identity"), Mapping)
    ]
    if len(replay_batches) != 1:
        blockers.append("exactly one replay batch is required")
    else:
        replay_batch = replay_batches[0]
        replay_payload = cast(Mapping[str, object], replay_batch["payload"])
        replay_identity = cast(Mapping[str, object], replay_batch["identity"])
        replay_binding = tuple(
            replay_identity.get(field_name)
            for field_name in (
                "update_id",
                "operation_id",
                "generation",
                "game_frame",
            )
        )
        if (
            replay_payload.get("action") != "replay"
            or replay_payload.get("available") is not True
            or replay_payload.get("event_seqs") != expected_event_seqs
            or replay_binding != reconnect_binding
        ):
            blockers.append("replay batch is not bound to the reconnect cursor")
    replay_rows: list[dict[str, object]] = []
    expected_replay_fields = expected_web_fields
    for event in replay_events:
        identity = cast(Mapping[str, object], event["identity"])
        payload = cast(Mapping[str, object], event["payload"])
        source_identity = payload.get("source_identity")
        source_payload = payload.get("source_payload")
        expected_identity = (
            {
                **dict(source_identity),
                "stage": "replayed",
            }
            if isinstance(source_identity, Mapping)
            else {}
        )
        if (
            set(payload) != expected_replay_fields
            or payload.get("action") != "dedupe"
            or not isinstance(source_identity, Mapping)
            or not isinstance(source_payload, Mapping)
            or dict(identity) != expected_identity
        ):
            blockers.append(
                "replay output did not preserve native source identity"
            )
            continue
        replay_rows.append(
            {
                field_name: deepcopy(payload[field_name])
                for field_name in (
                    "event_seq",
                    "logical_event_id",
                    "source_event_seq",
                    "source_event_type",
                    "source_identity",
                    "source_payload",
                )
            }
        )
    expected_replay_rows = [
        {
            field_name: deepcopy(row[field_name])
            for field_name in (
                "event_seq",
                "logical_event_id",
                "source_event_seq",
                "source_event_type",
                "source_identity",
                "source_payload",
            )
        }
        for row in expected_rows
    ]
    if canonical_json_bytes(replay_rows) != canonical_json_bytes(
        expected_replay_rows
    ):
        blockers.append(
            "reconnect replay did not preserve source order and multiplicity"
        )
    replayed = [
        str(row.get("logical_event_id", ""))
        for row in replay_rows
    ]
    if replayed != expected_replay:
        blockers.append("reconnect replay was not deduplicated exactly once")


def _verify_projection_identity(
    events: Sequence[Mapping[str, object]],
    blockers: list[str],
) -> None:
    projections = [
        dict(cast(Mapping[str, object], event["identity"]))
        for event in events
        if event.get("event_type")
        in {
            "web_projection",
            "hud_projection",
            "voice_projection",
            "voice_callout",
        }
    ]
    if len(projections) < 4:
        blockers.append("web/HUD/voice/callout projections are incomplete")
    elif any(identity != projections[0] for identity in projections[1:]):
        blockers.append("web/HUD/voice/callout projection identity mismatch")


def _state_operation_rows(
    state: Mapping[str, object],
) -> dict[str, dict[str, object]]:
    rows = _mapping_sequence(state.get("operations"))
    operations = {
        str(row.get("operation_id", "") or ""): row
        for row in rows
    }
    if (
        not operations
        or "" in operations
        or len(operations) != len(rows)
    ):
        raise ValueError("native state operations are malformed")
    return operations


def _state_owner_bindings_match(
    state: Mapping[str, object],
    unit_tags: Sequence[int],
    operation_id: str,
    generation: int,
) -> bool:
    bindings = state.get("owner_bindings")
    if not isinstance(bindings, Mapping):
        return False
    return all(
        isinstance(bindings.get(str(unit_tag)), Mapping)
        and cast(Mapping[str, object], bindings[str(unit_tag)]).get(
            "operation_id"
        )
        == operation_id
        and cast(Mapping[str, object], bindings[str(unit_tag)]).get(
            "generation"
        )
        == generation
        for unit_tag in unit_tags
    )


def _before_after_states(
    events: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, object], Mapping[str, object]] | None:
    snapshots = [
        event
        for event in events
        if event.get("event_type") == "state_snapshot"
    ]
    if len(snapshots) != 2:
        return None
    before_event, after_event = snapshots
    before_identity = before_event.get("identity")
    after_identity = after_event.get("identity")
    before_payload = before_event.get("payload")
    after_payload = after_event.get("payload")
    if (
        not isinstance(before_identity, Mapping)
        or not isinstance(after_identity, Mapping)
        or set(before_identity) != _RAW_EVENT_IDENTITY_FIELDS
        or set(after_identity) != _RAW_EVENT_IDENTITY_FIELDS
        or not isinstance(before_payload, Mapping)
        or not isinstance(after_payload, Mapping)
        or set(before_payload) != {"phase", "state"}
        or set(after_payload) != {"phase", "state"}
        or before_payload.get("phase") != "before"
        or after_payload.get("phase") != "after"
        or before_identity.get("stage") != "state_before"
        or after_identity.get("stage") != "state_after"
        or any(
            before_identity.get(field_name)
            != after_identity.get(field_name)
            for field_name in (
                "update_id",
                "operation_id",
                "generation",
                "game_frame",
            )
        )
        or not isinstance(before_payload.get("state"), Mapping)
        or not isinstance(after_payload.get("state"), Mapping)
    ):
        return None
    return (
        cast(Mapping[str, object], before_payload["state"]),
        cast(Mapping[str, object], after_payload["state"]),
    )


def _event_frames(
    events: Sequence[Mapping[str, object]],
    event_type: str | None = None,
) -> list[int]:
    return [
        int(cast(Mapping[str, object], event["identity"])["game_frame"])
        for event in events
        if event_type is None or event.get("event_type") == event_type
    ]


def build_pre_live_journey_bundle(
    micromachine_binary: Path | str,
    manifest_path: Path | str = DEFAULT_JOURNEY_MANIFEST,
    *,
    command_runner: CommandRunner = subprocess.run,
    node_executable: Path | str | None = None,
) -> bytes:
    manifest = load_pre_live_journey_manifest(manifest_path)
    suite = execute_pre_live_journeys(
        micromachine_binary,
        manifest_path,
        command_runner=command_runner,
        node_executable=node_executable,
    )
    members: dict[str, bytes] = {
        "input/PRE_LIVE_JOURNEYS.json": canonical_json_bytes(manifest),
        "derived/journey-matrix.json": canonical_json_bytes(
            {
                key: value
                for key, value in suite.items()
                if key not in {"artifacts", "binary_sha256", "embedded_build_input_identity"}
            }
        ),
        "report.md": _markdown_report(suite).encode("utf-8"),
    }
    artifacts = cast(dict[str, dict[str, object]], suite["artifacts"])
    for journey_id, artifact in artifacts.items():
        events = cast(list[dict[str, object]], artifact["events"])
        members[f"raw/{journey_id}.jsonl"] = b"".join(
            canonical_json_bytes(event) + b"\n" for event in events
        )
        members[f"product/{journey_id}.json"] = canonical_json_bytes(
            artifact["products"]
        )
    descriptors = [
        {
            "name": name,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
        }
        for name, payload in sorted(members.items())
    ]
    root_manifest = {
        "schema_version": PRE_LIVE_JOURNEY_BUNDLE_SCHEMA_VERSION,
        "evidence_kind": PRE_LIVE_JOURNEY_EVIDENCE_KIND,
        "suite_id": manifest["suite_id"],
        "journey_count": suite["journey_count"],
        "failed_count": suite["failed_count"],
        "report_sha256": hashlib.sha256(
            members["derived/journey-matrix.json"]
        ).hexdigest(),
        "members": descriptors,
    }
    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_STORED,
        allowZip64=False,
    ) as archive:
        _write_zip_member(archive, "manifest.json", canonical_json_bytes(root_manifest))
        for name in sorted(members):
            _write_zip_member(archive, name, members[name])
    return output.getvalue()


def write_pre_live_journey_bundle(
    output_path: Path | str,
    micromachine_binary: Path | str,
    manifest_path: Path | str = DEFAULT_JOURNEY_MANIFEST,
    *,
    command_runner: CommandRunner = subprocess.run,
    node_executable: Path | str | None = None,
) -> None:
    path = Path(output_path).absolute()
    if not path.parent.is_dir():
        raise ValueError("journey bundle output parent is missing")
    payload = build_pre_live_journey_bundle(
        micromachine_binary,
        manifest_path,
        command_runner=command_runner,
        node_executable=node_executable,
    )
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def verify_pre_live_journey_bundle(
    bundle: bytes,
    *,
    node_executable: Path | str | None = None,
) -> dict[str, object]:
    blockers: list[str] = []
    native_identities: set[tuple[str, str]] = set()
    if not isinstance(bundle, bytes):
        return _verification_result(["journey bundle must be bytes"])
    if len(bundle) > MAX_JOURNEY_BUNDLE_BYTES:
        return _verification_result(["journey bundle exceeds the size limit"])
    if (
        _archive_framing_error(
            bundle,
            require_exact_local_flags=True,
            allowed_general_purpose_flags=0,
            require_empty_extra_fields=True,
        )
        is not None
    ):
        return _verification_result(
            ["journey bundle ZIP framing is invalid"]
        )
    try:
        with zipfile.ZipFile(io.BytesIO(bundle), mode="r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(infos) > MAX_JOURNEY_BUNDLE_ENTRIES:
                blockers.append("journey bundle contains too many ZIP members")
            if not names or names[0] != "manifest.json":
                return _verification_result(["manifest.json must be first"])
            if names[1:] != sorted(names[1:]) or len(names) != len(set(names)):
                blockers.append("ZIP payload members must be unique and sorted")
            for info in infos:
                if (
                    info.date_time != DETERMINISTIC_ZIP_TIMESTAMP
                    or info.compress_type != zipfile.ZIP_STORED
                    or info.create_system != 3
                    or info.create_version != 20
                    or info.extract_version != 20
                    or info.reserved != 0
                    or info.flag_bits != 0
                    or info.external_attr != _REGULAR_FILE_MODE << 16
                    or info.internal_attr != 0
                    or info.volume != 0
                    or info.extra
                    or info.comment
                ):
                    blockers.append(f"non-deterministic ZIP metadata: {info.filename}")
                if info.file_size > MAX_JOURNEY_MEMBER_BYTES:
                    blockers.append(f"ZIP member exceeds size limit: {info.filename}")
            if blockers:
                return _verification_result(blockers)
            root = _load_canonical_json_object(
                archive.read("manifest.json"),
                label="root manifest",
                blockers=blockers,
            )
            validated_descriptors = _preflight_pre_live_journey_root(
                root,
                member_names=names[1:],
                member_sizes={
                    info.filename: info.file_size for info in infos[1:]
                },
                blockers=blockers,
            )
            if blockers:
                return _verification_result(blockers)
            payloads: dict[str, bytes] = {}
            for item in validated_descriptors:
                name = cast(str, item["name"])
                payload = archive.read(name)
                payloads[name] = payload
                if hashlib.sha256(payload).hexdigest() != item["sha256"]:
                    blockers.append(f"member digest mismatch: {name}")
                if len(payload) != item["size_bytes"]:
                    blockers.append(f"member size mismatch: {name}")
            if blockers:
                return _verification_result(blockers)
            return _verify_pre_live_journey_payload_cache(
                root,
                payloads,
                node_executable=node_executable,
            )
    except (KeyError, OSError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        blockers.append(f"journey bundle could not be verified: {exc}")
    identity = next(iter(native_identities), ("", ""))
    return _verification_result(
        blockers,
        binary_sha256=identity[0],
        embedded_build_input_identity=identity[1],
    )


def _preflight_pre_live_journey_root(
    root: Mapping[str, object],
    *,
    member_names: Sequence[str],
    member_sizes: Mapping[str, int],
    blockers: list[str],
) -> list[dict[str, object]]:
    if set(root) != {
        "schema_version",
        "evidence_kind",
        "suite_id",
        "journey_count",
        "failed_count",
        "report_sha256",
        "members",
    }:
        blockers.append("root manifest has an invalid field set")
    schema_version = root.get("schema_version")
    if (
        type(schema_version) is not int
        or schema_version != PRE_LIVE_JOURNEY_BUNDLE_SCHEMA_VERSION
    ):
        blockers.append("root manifest schema_version is unsupported")
    if root.get("evidence_kind") != PRE_LIVE_JOURNEY_EVIDENCE_KIND:
        blockers.append("root manifest evidence_kind is invalid")
    suite_id = root.get("suite_id")
    if not isinstance(suite_id, str) or not suite_id:
        blockers.append("root manifest suite_id is invalid")
    journey_count = root.get("journey_count")
    failed_count = root.get("failed_count")
    if type(journey_count) is not int or journey_count < 0:
        blockers.append("root manifest journey_count is invalid")
    if type(failed_count) is not int or failed_count < 0:
        blockers.append("root manifest failed_count is invalid")
    if (
        type(journey_count) is int
        and type(failed_count) is int
        and failed_count > journey_count
    ):
        blockers.append("root manifest failed_count exceeds journey_count")
    if not _is_lower_hex_sha256(root.get("report_sha256")):
        blockers.append("root manifest report_sha256 is invalid")
    descriptors = root.get("members")
    if not isinstance(descriptors, list):
        blockers.append("root manifest members must be a list")
        descriptors = []
    descriptor_names: list[str] = []
    validated_descriptors: list[dict[str, object]] = []
    for item in descriptors:
        if not isinstance(item, Mapping) or set(item) != {
            "name",
            "sha256",
            "size_bytes",
        }:
            blockers.append("invalid root manifest member descriptor")
            continue
        name = item.get("name")
        digest = item.get("sha256")
        size = item.get("size_bytes")
        if (
            not isinstance(name, str)
            or not name
            or not _is_lower_hex_sha256(digest)
            or type(size) is not int
            or size < 0
        ):
            blockers.append("invalid root manifest member descriptor")
            continue
        descriptor_names.append(name)
        if name in member_sizes and member_sizes[name] != size:
            blockers.append(f"member size mismatch: {name}")
        validated_descriptors.append(
            {
                "name": name,
                "sha256": digest,
                "size_bytes": size,
            }
        )
    if descriptor_names != list(member_names):
        blockers.append("root manifest member list does not match ZIP")
    return validated_descriptors


def _verify_pre_live_journey_payload_cache(
    root: Mapping[str, object],
    payloads: Mapping[str, bytes],
    *,
    node_executable: Path | str | None,
) -> dict[str, object]:
    blockers: list[str] = []
    native_identities: set[tuple[str, str]] = set()
    validated_descriptors = _preflight_pre_live_journey_root(
        root,
        member_names=sorted(payloads),
        member_sizes={name: len(payload) for name, payload in payloads.items()},
        blockers=blockers,
    )
    for item in validated_descriptors:
        name = cast(str, item["name"])
        payload = payloads.get(name)
        if payload is None:
            continue
        if hashlib.sha256(payload).hexdigest() != item["sha256"]:
            blockers.append(f"member digest mismatch: {name}")
        if len(payload) != item["size_bytes"]:
            blockers.append(f"member size mismatch: {name}")
    if blockers:
        return _verification_result(blockers)
    try:
        manifest_bytes = payloads["input/PRE_LIVE_JOURNEYS.json"]
        checked_in_manifest = canonical_json_bytes(
            load_pre_live_journey_manifest(DEFAULT_JOURNEY_MANIFEST)
        )
        if manifest_bytes != checked_in_manifest:
            blockers.append(
                "journey input manifest does not match the checked-in contract"
            )
        manifest = _load_canonical_json_object(
            manifest_bytes,
            label="journey input manifest",
            blockers=blockers,
        )
        try:
            validated = _validate_pre_live_journey_manifest_payload(manifest)
        except ValueError as exc:
            blockers.append(f"journey input manifest is invalid: {exc}")
            validated = {}
        specs = cast(list[dict[str, object]], validated.get("journeys", []))
        expected_names = sorted(
            {
                "input/PRE_LIVE_JOURNEYS.json",
                "derived/journey-matrix.json",
                "report.md",
                *{f"raw/{spec['id']}.jsonl" for spec in specs},
                *{f"product/{spec['id']}.json" for spec in specs},
            }
        )
        if sorted(payloads) != expected_names:
            blockers.append("journey bundle member set does not match input")
        if blockers:
            return _verification_result(blockers)

        matrix_bytes = payloads["derived/journey-matrix.json"]
        matrix = _load_canonical_json_object(
            matrix_bytes,
            label="derived journey matrix",
            blockers=blockers,
        )
        _preflight_derived_journey_matrix(
            matrix,
            specs=specs,
            suite_id=validated.get("suite_id"),
            blockers=blockers,
        )
        if hashlib.sha256(matrix_bytes).hexdigest() != root.get("report_sha256"):
            blockers.append("derived report digest mismatch")
        if blockers:
            return _verification_result(blockers)

        parsed_journeys: list[
            tuple[
                dict[str, object],
                list[dict[str, object]],
                dict[str, object],
            ]
        ] = []
        for spec in specs:
            journey_id = str(spec["id"])
            parsing_blocker_count = len(blockers)
            events = _load_canonical_json_lines(
                payloads[f"raw/{journey_id}.jsonl"],
                label=f"raw/{journey_id}.jsonl",
                blockers=blockers,
            )
            products = _load_canonical_json_object(
                payloads[f"product/{journey_id}.json"],
                label=f"product/{journey_id}.json",
                blockers=blockers,
            )
            if len(blockers) == parsing_blocker_count:
                blockers.extend(
                    f"{journey_id}: {blocker}"
                    for blocker in [
                        *_preflight_raw_event_structure(events),
                        *_preflight_product_path_blockers(
                            products,
                            spec=spec,
                        ),
                    ]
                )
            parsed_journeys.append((spec, events, products))
        if blockers:
            return _verification_result(blockers)

        preflighted_journeys: list[
            tuple[
                dict[str, object],
                list[dict[str, object]],
                dict[str, object],
                dict[str, object],
            ]
        ] = []
        for spec, events, products in parsed_journeys:
            journey_id = str(spec["id"])
            verdict = verify_pre_live_journey_events(spec, events)
            raw_blockers = cast(list[str], verdict["blockers"])
            preflighted_journeys.append((spec, events, products, verdict))
            blockers.extend(
                f"{journey_id}: {blocker}" for blocker in raw_blockers
            )
        pre_replay_reports = [
            {
                **verdict,
                "ok": not verdict["blockers"],
                "status": (
                    "passed" if not verdict["blockers"] else "failed"
                ),
                "blockers": list(cast(list[str], verdict["blockers"])),
                "product_paths": products,
            }
            for _, _, products, verdict in preflighted_journeys
        ]
        pre_replay_failures = [
            report["id"]
            for report in pre_replay_reports
            if report["ok"] is not True
        ]
        pre_replay_matrix = {
            "schema_version": PRE_LIVE_JOURNEY_REPORT_SCHEMA_VERSION,
            "suite_id": validated.get("suite_id"),
            "manifest_sha256": hashlib.sha256(
                canonical_json_bytes(validated)
            ).hexdigest(),
            "journey_count": len(pre_replay_reports),
            "passed_count": (
                len(pre_replay_reports) - len(pre_replay_failures)
            ),
            "failed_count": len(pre_replay_failures),
            "failures": pre_replay_failures,
            "ok": not pre_replay_failures,
            "status": "passed" if not pre_replay_failures else "failed",
            "journeys": pre_replay_reports,
        }
        if canonical_json_bytes(matrix) != canonical_json_bytes(
            pre_replay_matrix
        ):
            blockers.append(
                "derived journey matrix was not derived from raw evidence"
            )
        if blockers:
            return _verification_result(blockers)

        reports: list[dict[str, object]] = []
        for spec, events, products, verdict in preflighted_journeys:
            journey_id = str(spec["id"])
            derived_blockers = _rederive_product_path_blockers(
                spec,
                events,
                products,
                node_executable=node_executable,
            )
            identity = _native_adapter_identity(products, derived_blockers)
            if identity is not None:
                native_identities.add(identity)
            reports.append(
                {
                    **verdict,
                    "ok": not derived_blockers,
                    "status": "passed" if not derived_blockers else "failed",
                    "blockers": derived_blockers,
                    "product_paths": products,
                }
            )
            blockers.extend(
                f"{journey_id}: {blocker}" for blocker in derived_blockers
            )
        failures = [report["id"] for report in reports if not report["ok"]]
        recomputed = {
            "schema_version": PRE_LIVE_JOURNEY_REPORT_SCHEMA_VERSION,
            "suite_id": validated.get("suite_id"),
            "manifest_sha256": hashlib.sha256(
                canonical_json_bytes(validated)
            ).hexdigest(),
            "journey_count": len(reports),
            "passed_count": len(reports) - len(failures),
            "failed_count": len(failures),
            "failures": failures,
            "ok": not failures,
            "status": "passed" if not failures else "failed",
            "journeys": reports,
        }
        if canonical_json_bytes(matrix) != canonical_json_bytes(recomputed):
            blockers.append(
                "derived journey matrix was not derived from raw evidence"
            )
        if recomputed["ok"] is not True:
            blockers.append("recomputed journey matrix is not green")
        if payloads["report.md"] != _markdown_report(recomputed).encode("utf-8"):
            blockers.append("Markdown report was not derived from raw evidence")
        if root.get("suite_id") != validated.get("suite_id"):
            blockers.append("root manifest suite_id mismatch")
        if root.get("journey_count") != len(reports):
            blockers.append("root manifest journey_count mismatch")
        if root.get("failed_count") != len(failures):
            blockers.append("root manifest failed_count mismatch")
        if len(native_identities) != 1:
            blockers.append(
                "journey native adapter identities are missing or inconsistent"
            )
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        blockers.append(f"journey bundle could not be verified: {exc}")
    identity = next(iter(native_identities), ("", ""))
    return _verification_result(
        blockers,
        binary_sha256=identity[0],
        embedded_build_input_identity=identity[1],
    )


def _load_canonical_json_object(
    payload: bytes,
    *,
    label: str,
    blockers: list[str],
) -> dict[str, object]:
    try:
        value = json.loads(
            payload,
            object_pairs_hook=_reject_duplicate_json_object_keys,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        blockers.append(f"{label} is invalid JSON: {exc}")
        return {}
    if not isinstance(value, dict):
        blockers.append(f"{label} must be a JSON object")
        return {}
    if canonical_json_bytes(value) != payload:
        blockers.append(f"{label} is not canonical JSON")
    return value


def _is_lower_hex_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _load_canonical_json_lines(
    payload: bytes,
    *,
    label: str,
    blockers: list[str],
) -> list[dict[str, object]]:
    if not payload or not payload.endswith(b"\n"):
        blockers.append(f"{label} must be non-empty newline-terminated JSONL")
        return []
    return [
        _load_canonical_json_object(
            raw_line,
            label=f"{label}:{index}",
            blockers=blockers,
        )
        for index, raw_line in enumerate(payload.splitlines(), start=1)
    ]


def _provider_output(preset: str) -> dict[str, object]:
    if preset == "parallel_scout_attack_defend":
        return _operations_provider(
            "parallel scout attack defend",
            (
                _operation_provider("recon-alpha", "scout_with_units", "TERRAN_MARINE", 2, "scout"),
                _operation_provider("assault-bravo", "pressure_with_main_army", "TERRAN_MARINE", 4, "frontline"),
                _operation_provider("defense-charlie", "defend_with_units", "TERRAN_SIEGETANK", 1, "defensive_hold"),
            ),
        )
    if preset == "single_tank_attack":
        return _operations_provider(
            "tank attack after prerequisites",
            (_operation_provider("assault-tank", "pressure_with_main_army", "TERRAN_SIEGETANK", 1, "siege_support"),),
        )
    if preset == "safe_partial_attack":
        return _operations_provider(
            "safe partial attack",
            (
                _operation_provider(
                    "partial-assault",
                    "pressure_with_main_army",
                    "TERRAN_MARINE",
                    3,
                    "frontline",
                    minimum=3,
                    maximum=4,
                    allow_partial=True,
                ),
            ),
        )
    if preset == "strict_marine_attack":
        return _operations_provider(
            "strict marine attack",
            (_operation_provider("strict-assault", "pressure_with_main_army", "TERRAN_MARINE", 4, "frontline"),),
        )
    if preset in {"transfer_baseline", "transfer_rejection_baseline"}:
        operations = [
            _operation_provider("recon-alpha", "scout_with_units", "TERRAN_MARINE", 2, "scout"),
            _operation_provider("assault-bravo", "pressure_with_main_army", "TERRAN_MARINE", 4, "frontline"),
        ]
        if preset == "transfer_baseline":
            operations.append(
                _operation_provider("defense-charlie", "defend_with_units", "TERRAN_SIEGETANK", 1, "defensive_hold")
            )
        return _operations_provider("transfer baseline", operations)
    if preset in {"transfer_one_marine", "unsafe_transfer_all"}:
        transfer_count = 1
        source_count = 1 if preset == "transfer_one_marine" else 2
        source_before_count = 2 if preset == "transfer_one_marine" else 3
        destination_count = 5
        source = _operation_provider(
            "recon-alpha",
            "scout_with_units",
            "TERRAN_MARINE",
            source_count,
            "scout",
        )
        source["operation_edit"] = {
            "action": "transfer_out",
            "counterpart_operation_id": "assault-bravo",
            "unit_selection": [
                {
                    "unit_type": "TERRAN_MARINE",
                    "count": transfer_count,
                    "role": "scout",
                }
            ],
            "before_composition": [
                {
                    "unit_type": "TERRAN_MARINE",
                    "count": source_before_count,
                    "role": "scout",
                }
            ],
            "after_composition": (
                [
                    {
                        "unit_type": "TERRAN_MARINE",
                        "count": source_count,
                        "role": "scout",
                    }
                ]
                if source_count
                else []
            ),
            "explicit_override": True,
            "confirmation_policy": "auto",
        }
        source["composition_requirements"] = deepcopy(
            source["operation_edit"]["after_composition"]
        )
        source_task = cast(dict[str, object], source["tactical_task"])
        source_task["min_units"] = source_count
        source_task["max_units"] = source_count
        source_scope = cast(dict[str, object], source["scope"])
        source_scope["min_units"] = source_count
        source_scope["max_units"] = source_count
        destination = _operation_provider(
            "assault-bravo",
            "pressure_with_main_army",
            "TERRAN_MARINE",
            destination_count,
            "frontline",
        )
        destination["operation_edit"] = {
            "action": "transfer_in",
            "counterpart_operation_id": "recon-alpha",
            "unit_selection": [
                {
                    "unit_type": "TERRAN_MARINE",
                    "count": transfer_count,
                    "role": "scout",
                }
            ],
            "before_composition": [
                {"unit_type": "TERRAN_MARINE", "count": 4, "role": "frontline"}
            ],
            "after_composition": [
                {
                    "unit_type": "TERRAN_MARINE",
                    "count": 4,
                    "role": "frontline",
                },
                {
                    "unit_type": "TERRAN_MARINE",
                    "count": transfer_count,
                    "role": "scout",
                }
            ],
            "explicit_override": True,
            "confirmation_policy": "auto",
        }
        destination["composition_requirements"] = deepcopy(
            destination["operation_edit"]["after_composition"]
        )
        destination["unit_roles"] = [
            {
                "unit_type": "TERRAN_MARINE",
                "role": "frontline",
                "priority": 0.7,
                "ability_policy": "if_available",
            },
            {
                "unit_type": "TERRAN_MARINE",
                "role": "scout",
                "priority": 0.8,
                "ability_policy": "escape",
            },
        ]
        return _operations_provider(preset.replace("_", " "), (source, destination))
    if preset in {"reinforcement_baseline", "selective_cancel_baseline"}:
        return _operations_provider(
            "parallel recon and assault",
            (
                _operation_provider(
                    "recon-alpha",
                    "scout_with_units",
                    "TERRAN_MARINE",
                    2 if preset == "reinforcement_baseline" else 1,
                    "scout",
                ),
                _operation_provider(
                    "assault-bravo",
                    "pressure_with_main_army",
                    "TERRAN_MARINE" if preset == "reinforcement_baseline" else "TERRAN_SIEGETANK",
                    4 if preset == "reinforcement_baseline" else 1,
                    "frontline" if preset == "reinforcement_baseline" else "siege_support",
                ),
            ),
        )
    if preset == "reinforce_assault":
        return _operations_provider(
            "reinforce assault",
            (_operation_provider("assault-bravo", "pressure_with_main_army", "TERRAN_MARINE", 6, "frontline"),),
        )
    if preset in {"retarget_baseline", "retarget_enemy_main"}:
        return _operations_provider(
            "retarget assault",
            (
                _operation_provider(
                    "assault-bravo",
                    "pressure_with_main_army",
                    "TERRAN_MARINE",
                    4,
                    "frontline",
                    location=(
                        "enemy_natural"
                        if preset == "retarget_baseline"
                        else "enemy_main"
                    ),
                ),
            ),
        )
    if preset == "cancel_recon_only":
        operation = _operation_provider(
            "recon-alpha",
            "scout_with_units",
            "TERRAN_MARINE",
            1,
            "scout",
        )
        operation["lifetime"] = {
            "mode": "until_cancelled",
            "completion_conditions": ["cancelled_by_user"],
            "completion_state": "cancelled",
        }
        return _operations_provider("cancel recon only", (operation,))
    if preset in {"emergency_attack_baseline", "autonomous_defense_attack"}:
        return _operations_provider(
            "marine assault",
            (_operation_provider("assault-bravo", "pressure_with_main_army", "TERRAN_MARINE", 4, "frontline"),),
        )
    if preset == "emergency_retreat":
        return {
            "source": "llm",
            "goal": "retreat now",
            "override_level": "emergency",
            "ttl_seconds": 45,
            "emergency": {"cancel_attacks": True, "force_retreat": True},
        }
    if preset in {"reconnect_recon", "voice_recon"}:
        return _operations_provider(
            "marine recon",
            (
                _operation_provider(
                    "recon-alpha",
                    "scout_with_units",
                    "TERRAN_MARINE",
                    1 if preset == "reconnect_recon" else 2,
                    "scout",
                ),
            ),
        )
    if preset == "all_terran_matrix":
        raise ValueError(
            "all_terran_matrix is compiled as top-level live ability tasks"
        )
    raise ValueError(f"unknown pre-live journey preset: {preset}")


def _top_level_ability_provider(
    task_id: str,
    ability: str,
    unit_type: str,
    role: str,
) -> dict[str, object]:
    operation = _operation_provider(
        task_id,
        "execute_ability",
        unit_type,
        1,
        role,
    )
    tactical_task = cast(dict[str, object], operation["tactical_task"])
    tactical_task["task_id"] = task_id
    tactical_task["ability"] = ability
    return {
        "source": "llm",
        "goal": str(operation["goal"]),
        "command_layer": "micro",
        "tactical_task": tactical_task,
        "scope": operation["scope"],
        "composition_requirements": operation["composition_requirements"],
        "unit_roles": operation["unit_roles"],
    }


def _operation_provider(
    operation_id: str,
    task_type: str,
    unit_type: str,
    count: int,
    role: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    allow_partial: bool = False,
    location: str = "enemy_main",
) -> dict[str, object]:
    minimum = count if minimum is None else minimum
    maximum = count if maximum is None else maximum
    return {
        "operation_id": operation_id,
        "goal": operation_id.replace("-", " "),
        "tactical_task": {
            "task_type": task_type,
            "unit_classes": [unit_type],
            "location_intent": location,
            "min_units": minimum,
            "max_units": maximum,
            "allow_partial": allow_partial,
            "duration_seconds": 300,
        },
        "scope": {
            "army_group": "scout" if task_type == "scout_with_units" else "main",
            "unit_classes": [unit_type],
            "location_intent": location,
            "min_units": minimum,
            "max_units": maximum,
            "allow_partial_scope": allow_partial,
        },
        "composition_requirements": [
            {"unit_type": unit_type, "count": count, "role": role}
        ],
        "unit_roles": [
            {
                "unit_type": unit_type,
                "role": role,
                "priority": 0.7,
                "ability_policy": "if_available",
            }
        ],
    }


def _operations_provider(
    goal: str,
    operations: Iterable[Mapping[str, object]],
    *,
    command_layer: str = "operation",
) -> dict[str, object]:
    return {
        "source": "llm",
        "goal": goal,
        "command_layer": command_layer,
        "operations": [deepcopy(dict(operation)) for operation in operations],
    }


def _update_operations(
    update: Mapping[str, object],
) -> list[dict[str, object]]:
    vector = update.get("vector")
    operations = vector.get("operations") if isinstance(vector, Mapping) else None
    return _mapping_sequence(operations)


def _operation_by_id(
    update: Mapping[str, object],
    operation_id: str,
) -> dict[str, object]:
    for operation in _update_operations(update):
        if operation.get("operation_id") == operation_id:
            return operation
    raise ValueError(f"operation is missing from update: {operation_id}")


def _operation_requirements(
    operation: Mapping[str, object],
) -> list[dict[str, object]]:
    requirements = _mapping_sequence(operation.get("composition_requirements"))
    if requirements:
        return requirements
    task = cast(Mapping[str, object], operation.get("tactical_task", {}))
    classes = cast(Sequence[object], task.get("unit_classes", ()))
    return [
        {
            "unit_type": str(classes[0]) if classes else "TERRAN_MARINE",
            "count": int(task.get("min_units", 1) or 1),
            "role": "frontline",
        }
    ]


def _product_path_blockers(
    products: Mapping[str, object],
    *,
    spec: Mapping[str, object] | None = None,
) -> list[str]:
    blockers: list[str] = []
    if products.get("execution_error"):
        blockers.append(f"journey execution failed: {products['execution_error']}")
    required = (
        "native_adapter",
        "compiler_results",
        "bridge_validations",
        "operation_execution_reports",
        "tactical_evidence",
        "family_evidence",
        "battlefield_projections",
        "web_status",
        "timeline_results",
    )
    for name in required:
        value = products.get(name)
        if name == "native_adapter":
            if not isinstance(value, Mapping):
                blockers.append(f"product path was not exercised: {name}")
        elif not isinstance(value, list) or not value:
            blockers.append(f"product path was not exercised: {name}")
    validations = products.get("bridge_validations")
    if isinstance(validations, list) and any(
        not isinstance(item, Mapping) or item.get("accepted") is not True
        for item in validations
    ):
        blockers.append("one or more bridge validations were rejected")
    projections = products.get("battlefield_projections")
    if isinstance(projections, list):
        for item in projections:
            if (
                not isinstance(item, Mapping)
                or not isinstance(item.get("validated"), Mapping)
                or cast(Mapping[str, object], item["validated"]).get("ok")
                is not True
                or not isinstance(item.get("selected"), Mapping)
                or cast(Mapping[str, object], item["selected"]).get("ok")
                is not True
            ):
                blockers.append("one or more battlefield projections were rejected")
                break
    if spec is not None and spec.get("kind") == "voice_identity":
        blockers.extend(_preflight_tactical_radio_product(products))
    return blockers


def _preflight_tactical_radio_product(
    products: Mapping[str, object],
) -> list[str]:
    tactical_radio = products.get("tactical_radio_runtime")
    if (
        not isinstance(tactical_radio, Mapping)
        or set(tactical_radio) != _TACTICAL_RADIO_RUNTIME_FIELDS
    ):
        return ["production Tactical Radio product field set is invalid"]
    schema_version = tactical_radio.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
        return ["production Tactical Radio product schema is unsupported"]
    if tactical_radio.get("runtime") != "production_web_gui_tactical_radio_js":
        return ["production Tactical Radio runtime identity is invalid"]
    for field_name in ("node_sha256", "source_sha256"):
        if not _is_lower_hex_sha256(tactical_radio.get(field_name)):
            return [f"production Tactical Radio {field_name} is invalid"]
    timeline_results = products.get("timeline_results")
    if not isinstance(timeline_results, list) or not timeline_results:
        return ["production Tactical Radio timeline evidence is missing"]
    timeline = timeline_results[0]
    if not isinstance(timeline, Mapping):
        return ["production Tactical Radio timeline evidence is malformed"]
    operation_events = timeline.get("operation_events")
    if (
        not isinstance(operation_events, Sequence)
        or isinstance(operation_events, (str, bytes, bytearray))
        or len(operation_events) < 2
        or not isinstance(operation_events[-2], Mapping)
        or not isinstance(operation_events[-1], Mapping)
    ):
        return ["production Tactical Radio timeline evidence is malformed"]
    result = {
        key: tactical_radio[key] for key in _TACTICAL_RADIO_RESULT_FIELDS
    }
    try:
        _validate_tactical_radio_result(
            result,
            primary_event=cast(Mapping[str, object], operation_events[-2]),
            secondary_event=cast(Mapping[str, object], operation_events[-1]),
        )
    except ValueError as exc:
        return [f"production Tactical Radio product is invalid: {exc}"]
    return []


def _preflight_raw_event_structure(
    events: Sequence[Mapping[str, object]],
) -> list[str]:
    blockers: list[str] = []
    for expected_seq, event in enumerate(events, start=1):
        if set(event) != _RAW_EVENT_FIELDS:
            blockers.append("raw event has an invalid field set")
            continue
        seq = event.get("seq")
        event_type = event.get("event_type")
        identity = event.get("identity")
        payload = event.get("payload")
        if type(seq) is not int or seq != expected_seq:
            blockers.append("raw event sequence is not canonical")
        if (
            not isinstance(event_type, str)
            or not event_type
            or event_type not in _CANONICAL_EVENT_STAGES
        ):
            blockers.append("raw event_type is unsupported")
        if not isinstance(identity, Mapping):
            blockers.append("raw event identity must be an object")
        elif set(identity) != _RAW_EVENT_IDENTITY_FIELDS:
            blockers.append("raw event identity has an invalid field set")
        else:
            update_id = identity.get("update_id")
            operation_id = identity.get("operation_id")
            generation = identity.get("generation")
            stage = identity.get("stage")
            game_frame = identity.get("game_frame")
            if (
                not isinstance(update_id, str)
                or not update_id
                or not isinstance(operation_id, str)
                or type(generation) is not int
                or generation < 0
                or not isinstance(stage, str)
                or stage not in _CANONICAL_EVENT_STAGES.get(
                    cast(str, event_type),
                    frozenset(),
                )
                or type(game_frame) is not int
                or game_frame < 0
            ):
                blockers.append("raw event identity is not canonical")
        if not isinstance(payload, Mapping):
            blockers.append("raw event payload must be an object")
    return blockers


def _preflight_derived_journey_matrix(
    matrix: Mapping[str, object],
    *,
    specs: Sequence[Mapping[str, object]],
    suite_id: object,
    blockers: list[str],
) -> None:
    if set(matrix) != _DERIVED_MATRIX_FIELDS:
        blockers.append("derived journey matrix has an invalid field set")
    schema_version = matrix.get("schema_version")
    if (
        type(schema_version) is not int
        or schema_version != PRE_LIVE_JOURNEY_REPORT_SCHEMA_VERSION
    ):
        blockers.append("derived journey matrix schema_version is unsupported")
    if not isinstance(matrix.get("suite_id"), str) or matrix.get(
        "suite_id"
    ) != suite_id:
        blockers.append("derived journey matrix suite_id is invalid")
    if not _is_lower_hex_sha256(matrix.get("manifest_sha256")):
        blockers.append("derived journey matrix manifest_sha256 is invalid")
    journey_count = matrix.get("journey_count")
    passed_count = matrix.get("passed_count")
    failed_count = matrix.get("failed_count")
    if (
        type(journey_count) is not int
        or journey_count != len(specs)
        or type(passed_count) is not int
        or passed_count < 0
        or type(failed_count) is not int
        or failed_count < 0
        or (
            type(passed_count) is int
            and type(failed_count) is int
            and passed_count + failed_count != len(specs)
        )
    ):
        blockers.append("derived journey matrix counts are invalid")
    failures = matrix.get("failures")
    if (
        not isinstance(failures, list)
        or any(not isinstance(item, str) or not item for item in failures)
        or len(failures) != len(set(failures))
    ):
        blockers.append("derived journey matrix failures are invalid")
    matrix_ok = matrix.get("ok")
    matrix_status = matrix.get("status")
    if type(matrix_ok) is not bool or matrix_status not in {"passed", "failed"}:
        blockers.append("derived journey matrix verdict is invalid")
    elif matrix_status != ("passed" if matrix_ok else "failed"):
        blockers.append("derived journey matrix verdict is contradictory")
    journeys = matrix.get("journeys")
    if not isinstance(journeys, list) or len(journeys) != len(specs):
        blockers.append("derived journey matrix journeys are invalid")
        return
    expected_ids = [str(spec.get("id", "")) for spec in specs]
    observed_ids: list[str] = []
    for row in journeys:
        if not isinstance(row, Mapping):
            blockers.append("derived journey matrix row must be an object")
            continue
        if set(row) != _DERIVED_MATRIX_JOURNEY_FIELDS:
            blockers.append("derived journey matrix row has an invalid field set")
        journey_id = row.get("id")
        observed_ids.append(str(journey_id) if isinstance(journey_id, str) else "")
        row_blockers = row.get("blockers")
        row_ok = row.get("ok")
        row_status = row.get("status")
        if (
            not isinstance(journey_id, str)
            or not journey_id
            or not isinstance(row.get("title"), str)
            or type(row.get("event_count")) is not int
            or cast(int, row.get("event_count")) < 0
            or type(row.get("ownership_snapshot_count")) is not int
            or cast(int, row.get("ownership_snapshot_count")) < 0
            or not isinstance(row.get("event_types"), list)
            or any(
                not isinstance(item, str) or not item
                for item in cast(list[object], row.get("event_types"))
            )
            or not isinstance(row_blockers, list)
            or any(
                not isinstance(item, str) or not item
                for item in cast(list[object], row_blockers)
            )
            or type(row_ok) is not bool
            or row_status not in {"passed", "failed"}
            or not isinstance(row.get("product_paths"), Mapping)
        ):
            blockers.append("derived journey matrix row is malformed")
        elif (
            row_status != ("passed" if row_ok else "failed")
            or row_ok != (not row_blockers)
        ):
            blockers.append("derived journey matrix row verdict is contradictory")
    if observed_ids != expected_ids:
        blockers.append("derived journey matrix journey order is invalid")


def _preflight_product_path_blockers(
    products: Mapping[str, object],
    *,
    spec: Mapping[str, object],
) -> list[str]:
    blockers = _product_path_blockers(products, spec=spec)
    native_adapter = products.get("native_adapter")
    if not isinstance(native_adapter, Mapping):
        return blockers
    if set(native_adapter) != _NATIVE_ADAPTER_PRODUCT_FIELDS:
        blockers.append("native adapter product has an invalid field set")
        return blockers
    native_schema_version = native_adapter.get("schema_version")
    if (
        type(native_schema_version) is not int
        or native_schema_version != PRE_LIVE_NATIVE_ADAPTER_SCHEMA_VERSION
    ):
        blockers.append("native adapter product schema_version is unsupported")
        return blockers
    native_input = native_adapter.get("input")
    if not isinstance(native_input, Mapping) or set(native_input) != (
        _NATIVE_INPUT_FIELDS
    ):
        blockers.append("native adapter input has an invalid field set")
        return blockers
    input_schema_version = native_input.get("schema_version")
    if (
        type(input_schema_version) is not int
        or input_schema_version != PRE_LIVE_NATIVE_ADAPTER_SCHEMA_VERSION
    ):
        blockers.append("native adapter input schema_version is unsupported")
        return blockers
    try:
        expected_input = _compile_native_input(_JourneyExecution(spec))
        if canonical_json_bytes(native_input) != canonical_json_bytes(
            expected_input
        ):
            blockers.append(
                "native adapter input was not derived from the journey compiler"
            )
        _validate_native_output_payload(
            deepcopy(native_adapter.get("output")),
            expected_input=expected_input,
        )
    except (KeyError, TypeError, ValueError) as exc:
        blockers.append(f"native adapter structure is invalid: {exc}")
    _native_adapter_identity(products, blockers)
    return blockers


def _rederive_product_path_blockers(
    spec: Mapping[str, object],
    events: Sequence[Mapping[str, object]],
    products: Mapping[str, object],
    *,
    node_executable: Path | str | None = None,
) -> list[str]:
    blockers = _preflight_product_path_blockers(products, spec=spec)
    native_adapter = products.get("native_adapter")
    if not isinstance(native_adapter, Mapping):
        return blockers
    if blockers:
        return blockers
    try:
        execution = _JourneyExecution(spec)
        expected_input = _compile_native_input(execution)
        if canonical_json_bytes(native_adapter.get("input")) != (
            canonical_json_bytes(expected_input)
        ):
            blockers.append(
                "native adapter input was not derived from the journey compiler"
            )
        native_output = _validate_native_output_payload(
            deepcopy(native_adapter.get("output")),
            expected_input=expected_input,
        )
        _consume_native_output(
            execution,
            native_output,
            node_executable=node_executable,
            node_sha256=(
                str(
                    cast(
                        Mapping[str, object],
                        products.get("tactical_radio_runtime", {}),
                    ).get("node_sha256", "")
                )
                if spec.get("kind") == "voice_identity"
                else None
            ),
        )
        _finalize_events(execution)
        execution.products["native_adapter"] = {
            "schema_version": PRE_LIVE_NATIVE_ADAPTER_SCHEMA_VERSION,
            "binary_sha256": native_adapter.get("binary_sha256"),
            "embedded_build_input_identity": native_adapter.get(
                "embedded_build_input_identity"
            ),
            "input": deepcopy(expected_input),
            "output": deepcopy(native_output),
        }
        normalized_events = [deepcopy(dict(event)) for event in events]
        if canonical_json_bytes(execution.events) != canonical_json_bytes(
            normalized_events
        ):
            blockers.append(
                "raw events were not causally derived from native and product paths"
            )
        if canonical_json_bytes(execution.products) != canonical_json_bytes(
            dict(products)
        ):
            blockers.append(
                "product evidence was not rederived by the executable product paths"
            )
    except (KeyError, TypeError, ValueError) as exc:
        blockers.append(f"product evidence replay failed closed: {exc}")
    return blockers


def _native_adapter_identity(
    products: Mapping[str, object],
    blockers: list[str],
) -> tuple[str, str] | None:
    native_adapter = products.get("native_adapter")
    if not isinstance(native_adapter, Mapping):
        return None
    binary_sha256 = native_adapter.get("binary_sha256")
    embedded_identity = native_adapter.get("embedded_build_input_identity")
    if (
        not isinstance(binary_sha256, str)
        or len(binary_sha256) != 64
        or any(character not in "0123456789abcdef" for character in binary_sha256)
    ):
        blockers.append("native adapter binary_sha256 is invalid")
        return None
    if (
        not isinstance(embedded_identity, str)
        or not embedded_identity.startswith(_SHA256_IDENTITY_PREFIX)
        or len(embedded_identity) != len(_SHA256_IDENTITY_PREFIX) + 64
        or any(
            character not in "0123456789abcdef"
            for character in embedded_identity[len(_SHA256_IDENTITY_PREFIX) :]
        )
    ):
        blockers.append("native adapter embedded build identity is invalid")
        return None
    return binary_sha256, embedded_identity


def _mapping_sequence(value: object) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _string_list(value: object, *, label: str) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise ValueError(f"{label} must be an array")
    if any(type(item) is not str or not item for item in value):
        raise ValueError(f"{label} contains invalid values")
    result = cast(list[str], list(value))
    if len(result) != len(set(result)):
        raise ValueError(f"{label} contains invalid values")
    return sorted(result)


def _reject_duplicate_json_object_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_json(value: str) -> object:
    raise ValueError(f"non-finite JSON number is not supported: {value}")


def _sha256_file(path: Path) -> str:
    inherited_descriptor = _inherited_executable_fd(path)
    if inherited_descriptor is not None:
        snapshot = _native_executable_snapshot(inherited_descriptor)
        return _sha256_descriptor(
            inherited_descriptor,
            snapshot[3],
        )
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return bytes(value or b"")


def _markdown_report(suite: Mapping[str, object]) -> str:
    lines = [
        "# MicroMachine deterministic pre-live journeys",
        "",
        f"- Suite: `{suite['suite_id']}`",
        f"- Journeys: {suite['journey_count']}",
        f"- Passed: {suite['passed_count']}",
        f"- Failed: {suite['failed_count']}",
        "",
        "| Journey | Status | Raw events |",
        "|---|---:|---:|",
    ]
    for journey in cast(Sequence[Mapping[str, object]], suite["journeys"]):
        lines.append(
            f"| `{journey['id']}` | {journey['status']} | {journey['event_count']} |"
        )
    lines.append("")
    return "\n".join(lines)


def _write_zip_member(
    archive: zipfile.ZipFile,
    name: str,
    payload: bytes,
) -> None:
    info = zipfile.ZipInfo(name, date_time=DETERMINISTIC_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.create_version = 20
    info.extract_version = 20
    info.external_attr = _REGULAR_FILE_MODE << 16
    info.internal_attr = 0
    info.extra = b""
    info.comment = b""
    archive.writestr(info, payload)


def _verification_result(
    blockers: Sequence[str],
    *,
    binary_sha256: str = "",
    embedded_build_input_identity: str = "",
) -> dict[str, object]:
    return {
        "ok": not blockers,
        "status": "accepted" if not blockers else "blocked",
        "blockers": list(blockers),
        "binary_sha256": binary_sha256,
        "embedded_build_input_identity": embedded_build_input_identity,
    }


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Execute deterministic MicroMachine pre-live journeys."
    )
    parser.add_argument("--manifest", default=str(DEFAULT_JOURNEY_MANIFEST))
    parser.add_argument("--micromachine-binary", required=True)
    parser.add_argument("--node-executable")
    parser.add_argument("--emit-bundle", metavar="OUTPUT")
    parser.add_argument("--report", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        if args.emit_bundle:
            write_pre_live_journey_bundle(
                args.emit_bundle,
                args.micromachine_binary,
                args.manifest,
                node_executable=args.node_executable,
            )
            verification = verify_pre_live_journey_bundle(
                Path(args.emit_bundle).read_bytes(),
                node_executable=args.node_executable,
            )
            print(canonical_json_bytes(verification).decode("utf-8"))
            return 0 if verification["ok"] is True else 1
        report = execute_pre_live_journeys(
            args.micromachine_binary,
            args.manifest,
            node_executable=args.node_executable,
        )
        print(
            _markdown_report(report)
            if args.report
            else canonical_json_bytes(
                {
                    key: value
                    for key, value in report.items()
                    if key != "artifacts"
                }
            ).decode("utf-8")
        )
        return 0 if report["ok"] is True else 1
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"pre-live journey execution failed: {exc}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
