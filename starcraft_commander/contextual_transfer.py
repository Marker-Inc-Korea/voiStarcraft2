"""Typed contextual operation-transfer admission and payload preparation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Final

from starcraft_commander.micromachine_battlefield_projection import (
    BATTLEFIELD_OVERVIEW_AUTHORITY,
    battlefield_overview_fingerprint,
    canonical_battlefield_session_epoch,
)


CONTEXTUAL_TRANSFER_SCHEMA_VERSION: Final[int] = 1
CONTEXTUAL_TRANSFER_ACTIONS: Final[frozenset[str]] = frozenset(
    {"transfer_available_units", "transfer_two_units"}
)
CONTEXTUAL_TRANSFER_REQUEST_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "choice_id",
        "request_id",
        "action",
        "source_operation_id",
        "destination_operation_id",
        "source_generation",
        "destination_generation",
        "requested_count",
        "protected_minimum",
        "source_minimum",
        "blackboard_scope_id",
        "session_epoch",
        "projection_frame",
        "projection_fingerprint",
    }
)

_SAFE_IDENTIFIER_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
)
_FINGERPRINT_RE: Final[re.Pattern[str]] = re.compile(r"^[a-f0-9]{64}$")


class ContextualTransferRejectedError(RuntimeError):
    """Fail-closed canonical admission rejection that must not publish."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, object] | None = None,
    ) -> None:
        self.code = code
        self.details = deepcopy(dict(details or {}))
        super().__init__(message)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "code": self.code,
            "message": str(self),
        }
        if self.details:
            payload["details"] = deepcopy(self.details)
        return payload


@dataclass(frozen=True)
class ContextualTransferRequest:
    """Canonical identity captured when one transfer choice is rendered."""

    schema_version: int
    choice_id: str
    request_id: str
    action: str
    source_operation_id: str
    destination_operation_id: str
    source_generation: int
    destination_generation: int
    requested_count: int
    protected_minimum: int
    source_minimum: int
    blackboard_scope_id: str
    session_epoch: str
    projection_frame: int
    projection_fingerprint: str

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, object],
    ) -> "ContextualTransferRequest":
        if not isinstance(value, Mapping):
            raise ValueError("contextual transfer request must be a JSON object.")
        unknown = sorted(set(value) - CONTEXTUAL_TRANSFER_REQUEST_FIELDS)
        missing = sorted(CONTEXTUAL_TRANSFER_REQUEST_FIELDS - set(value))
        if unknown:
            raise ValueError(
                "contextual transfer request contains unsupported fields: "
                + ", ".join(unknown)
            )
        if missing:
            raise ValueError(
                "contextual transfer request is missing required fields: "
                + ", ".join(missing)
            )
        schema_version = _require_int(
            "schema_version",
            value.get("schema_version"),
            minimum=1,
        )
        if schema_version != CONTEXTUAL_TRANSFER_SCHEMA_VERSION:
            raise ValueError(
                "unsupported contextual transfer schema_version: "
                f"{schema_version}."
            )
        action = _require_text("action", value.get("action"))
        if action not in CONTEXTUAL_TRANSFER_ACTIONS:
            raise ValueError(
                f"action must be one of {sorted(CONTEXTUAL_TRANSFER_ACTIONS)!r}."
            )
        source_operation_id = _require_identifier(
            "source_operation_id",
            value.get("source_operation_id"),
        )
        destination_operation_id = _require_identifier(
            "destination_operation_id",
            value.get("destination_operation_id"),
        )
        if source_operation_id == destination_operation_id:
            raise ValueError(
                "source_operation_id and destination_operation_id must differ."
            )
        projection_fingerprint = _require_text(
            "projection_fingerprint",
            value.get("projection_fingerprint"),
        )
        if _FINGERPRINT_RE.fullmatch(projection_fingerprint) is None:
            raise ValueError(
                "projection_fingerprint must be a lowercase SHA-256 hex digest."
            )
        return cls(
            schema_version=schema_version,
            choice_id=_require_identifier("choice_id", value.get("choice_id")),
            request_id=_require_identifier(
                "request_id",
                value.get("request_id"),
            ),
            action=action,
            source_operation_id=source_operation_id,
            destination_operation_id=destination_operation_id,
            source_generation=_require_int(
                "source_generation",
                value.get("source_generation"),
                minimum=1,
            ),
            destination_generation=_require_int(
                "destination_generation",
                value.get("destination_generation"),
                minimum=1,
            ),
            requested_count=_require_int(
                "requested_count",
                value.get("requested_count"),
                minimum=1,
                maximum=200,
            ),
            protected_minimum=_require_int(
                "protected_minimum",
                value.get("protected_minimum"),
                minimum=0,
                maximum=200,
            ),
            source_minimum=_require_int(
                "source_minimum",
                value.get("source_minimum"),
                minimum=0,
                maximum=200,
            ),
            blackboard_scope_id=_require_identifier(
                "blackboard_scope_id",
                value.get("blackboard_scope_id"),
            ),
            session_epoch=_require_session_epoch(
                "session_epoch",
                value.get("session_epoch"),
            ),
            projection_frame=_require_int(
                "projection_frame",
                value.get("projection_frame"),
                minimum=0,
            ),
            projection_fingerprint=projection_fingerprint,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            field_name: getattr(self, field_name)
            for field_name in CONTEXTUAL_TRANSFER_REQUEST_FIELDS
        }

    def replay_fingerprint(self) -> str:
        payload = self.to_dict()
        payload.pop("request_id", None)
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ContextualTransferPreparation:
    """Validated deterministic provider input for the existing reducer."""

    command_text: str
    provider_output: Mapping[str, object]
    current_frame: int


def prepare_contextual_transfer(
    request: ContextualTransferRequest,
    *,
    status: Mapping[str, object],
    current_vector: Mapping[str, object],
) -> ContextualTransferPreparation:
    """Revalidate one captured choice and build a semantic reciprocal edit."""

    if status.get("operation_registry_authoritative") is not True:
        _reject(
            "operation_registry_not_authoritative",
            "Current operation ownership is not launcher-validated.",
        )
    if status.get("runtime_attached") is not True:
        _reject(
            "runtime_not_attached",
            "A current attached MicroMachine runtime is required.",
        )
    if status.get("telemetry_current_for_process") is not True:
        _reject(
            "runtime_telemetry_not_current",
            "The attached runtime telemetry is not current for this process.",
        )
    if str(status.get("blackboard_scope_id", "") or "") != (
        request.blackboard_scope_id
    ):
        _reject(
            "blackboard_scope_mismatch",
            "The current blackboard scope no longer matches the choice.",
        )
    projection = _mapping(status.get("battlefield_projection"))
    if projection.get("ok") is not True:
        _reject(
            "battlefield_projection_not_authoritative",
            "The current battlefield projection did not pass integrity validation.",
        )
    integrity = _mapping(status.get("battlefield_projection_integrity"))
    if str(integrity.get("status", "") or "") != "valid":
        _reject(
            "battlefield_projection_integrity_mismatch",
            "The current battlefield projection integrity is not valid.",
        )
    overview = _mapping(status.get("battlefield_overview"))
    if not overview:
        _reject(
            "battlefield_projection_unavailable",
            "The current authoritative battlefield overview is unavailable.",
        )
    if str(overview.get("authority", "") or "") != BATTLEFIELD_OVERVIEW_AUTHORITY:
        _reject(
            "battlefield_authority_mismatch",
            "The battlefield overview was not emitted by MicroMachine C++.",
        )
    identity = _mapping(status.get("battlefield_projection_identity"))
    overview_identity = _mapping(overview.get("identity"))
    current_epoch = _required_current_session_epoch(
        "session_epoch",
        identity.get("session_epoch", overview_identity.get("session_epoch")),
    )
    current_frame = _required_current_int(
        "game_frame",
        identity.get("game_frame", overview_identity.get("game_frame")),
        minimum=0,
    )
    if current_epoch != request.session_epoch:
        _reject(
            "session_epoch_mismatch",
            "The battlefield session epoch changed after the choice was rendered.",
            expected=request.session_epoch,
            actual=current_epoch,
        )
    if current_frame != request.projection_frame:
        _reject(
            "projection_frame_mismatch",
            "The battlefield projection frame changed after the choice was rendered.",
            expected=request.projection_frame,
            actual=current_frame,
        )
    actual_fingerprint = battlefield_overview_fingerprint(overview)
    reported_fingerprint = str(
        status.get("battlefield_projection_fingerprint", "") or ""
    )
    if reported_fingerprint and reported_fingerprint != actual_fingerprint:
        _reject(
            "server_projection_fingerprint_mismatch",
            "The server projection fingerprint does not match its overview.",
        )
    if request.projection_fingerprint != actual_fingerprint:
        _reject(
            "projection_fingerprint_mismatch",
            "The battlefield projection payload changed after the choice was rendered.",
        )

    operations = _operation_projection_index(overview)
    source_projection = operations.get(request.source_operation_id)
    destination_projection = operations.get(request.destination_operation_id)
    if source_projection is None:
        _reject(
            "source_operation_missing",
            "The selected source operation is no longer active.",
        )
    if destination_projection is None:
        _reject(
            "destination_operation_missing",
            "The selected destination operation is no longer active.",
        )
    _validate_active_operation(
        source_projection,
        operation_id=request.source_operation_id,
        role="source",
    )
    _validate_active_operation(
        destination_projection,
        operation_id=request.destination_operation_id,
        role="destination",
    )
    source_generation = _operation_generation(source_projection)
    destination_generation = _operation_generation(destination_projection)
    if source_generation != request.source_generation:
        _reject(
            "stale_source_generation",
            "The source operation generation changed after rendering.",
            expected=request.source_generation,
            actual=source_generation,
        )
    if destination_generation != request.destination_generation:
        _reject(
            "stale_destination_generation",
            "The destination operation generation changed after rendering.",
            expected=request.destination_generation,
            actual=destination_generation,
        )

    transfer = _mapping(overview.get("transfer_availability"))
    if transfer.get("atomic_revalidation_required") is not True:
        _reject(
            "atomic_revalidation_not_required",
            "The runtime did not require atomic transfer revalidation.",
        )
    entry = _matching_transfer_entry(request, transfer)
    source_count = _required_current_int(
        "source_owner_count",
        entry.get("source_owner_count"),
        minimum=0,
    )
    source_ownership = _mapping(
        source_projection.get("operation_ownership")
    )
    projected_source_count = _required_current_int(
        "projected_source_owner_count",
        source_ownership.get("owner_count"),
        minimum=0,
    )
    if projected_source_count != source_count:
        _reject(
            "source_ownership_count_mismatch",
            (
                "The transfer entry source count does not match the "
                "authoritative operation ownership count."
            ),
            transfer_entry_count=source_count,
            operation_owner_count=projected_source_count,
        )
    protected_minimum = _required_current_int(
        "protected_minimum",
        entry.get("protected_minimum"),
        minimum=0,
    )
    transferable_count = _required_current_int(
        "transferable_count",
        entry.get("transferable_count"),
        minimum=0,
    )
    launch_policy = _mapping(source_projection.get("operation_launch_policy"))
    source_minimum = _required_current_int(
        "source_minimum",
        launch_policy.get("min_units"),
        minimum=0,
    )
    if protected_minimum != request.protected_minimum:
        _reject(
            "protected_minimum_mismatch",
            "The protected minimum changed after rendering.",
            expected=request.protected_minimum,
            actual=protected_minimum,
        )
    if source_minimum != request.source_minimum:
        _reject(
            "source_minimum_mismatch",
            "The source operation minimum changed after rendering.",
            expected=request.source_minimum,
            actual=source_minimum,
        )
    expected_count = (
        2
        if request.action == "transfer_two_units"
        else transferable_count
    )
    if request.requested_count != expected_count:
        _reject(
            "requested_count_mismatch",
            "The requested count does not match the canonical choice.",
            expected=expected_count,
            actual=request.requested_count,
        )
    if request.requested_count > transferable_count:
        _reject(
            "transferable_count_decreased",
            "The current transferable count is below the requested count.",
            requested=request.requested_count,
            transferable=transferable_count,
        )
    if source_count - request.requested_count < protected_minimum:
        _reject(
            "protected_minimum_violation",
            "The transfer would cross the current protected minimum.",
        )
    if source_count - request.requested_count < source_minimum:
        _reject(
            "source_minimum_violation",
            "The transfer would cross the source operation minimum.",
        )
    if entry.get("transfer_safe") is not True:
        _reject(
            str(entry.get("atomic_runtime_blocker", "") or "transfer_not_safe"),
            "The current runtime no longer marks this transfer safe.",
        )
    if str(entry.get("atomic_runtime_blocker", "") or ""):
        _reject(
            str(entry.get("atomic_runtime_blocker")),
            "The current runtime reported a transfer blocker.",
        )
    safety = _mapping(entry.get("safety_evidence"))
    inputs = _mapping(entry.get("atomic_revalidation_inputs"))
    if safety.get("protected_minimum_respected") is not True:
        _reject(
            "protected_minimum_not_respected",
            "Runtime safety evidence does not preserve the protected minimum.",
        )
    for field_name in (
        "source_active",
        "destination_active",
        "ownership_integrity",
        "operation_assignments_match",
        "squad_assignments_match",
        "action_assignments_match",
        "role_assignments_match",
        "atomic_revalidation_ready",
    ):
        if inputs.get(field_name) is not True:
            _reject(
                f"atomic_{field_name}_mismatch",
                f"Runtime atomic revalidation field {field_name} is not true.",
            )

    current_operations = _vector_operation_index(current_vector)
    source_operation = current_operations.get(request.source_operation_id)
    destination_operation = current_operations.get(
        request.destination_operation_id
    )
    if source_operation is None or destination_operation is None:
        _reject(
            "current_vector_endpoint_missing",
            "The latest blackboard vector does not contain both transfer endpoints.",
        )
    if _operation_generation(source_operation) != request.source_generation:
        _reject(
            "current_vector_source_generation_mismatch",
            "The latest vector source generation does not match the projection.",
        )
    if _operation_generation(destination_operation) != (
        request.destination_generation
    ):
        _reject(
            "current_vector_destination_generation_mismatch",
            "The latest vector destination generation does not match the projection.",
        )

    source_before = _composition(source_operation)
    destination_before = _composition(destination_operation)
    selection, source_after = _take_composition(
        source_before,
        request.requested_count,
    )
    if _composition_count(source_before) != source_count:
        _reject(
            "semantic_source_count_mismatch",
            "The source semantic composition no longer matches runtime ownership.",
            semantic_count=_composition_count(source_before),
            owner_count=source_count,
        )
    if _composition_count(source_after) < source_minimum:
        _reject(
            "semantic_source_minimum_violation",
            "The semantic source composition would cross its minimum.",
        )
    destination_after = _add_composition(destination_before, selection)

    source_payload = deepcopy(source_operation)
    destination_payload = deepcopy(destination_operation)
    source_payload["composition_requirements"] = source_after
    destination_payload["composition_requirements"] = destination_after
    _resize_operation_task(source_payload, _composition_count(source_after))
    _resize_operation_task(
        destination_payload,
        _composition_count(destination_after),
    )
    _ensure_destination_roles(destination_payload, source_payload, selection)
    source_payload["operation_edit"] = _operation_edit(
        action="transfer_out",
        counterpart_operation_id=request.destination_operation_id,
        selection=selection,
        before=source_before,
        after=source_after,
    )
    destination_payload["operation_edit"] = _operation_edit(
        action="transfer_in",
        counterpart_operation_id=request.source_operation_id,
        selection=selection,
        before=destination_before,
        after=destination_after,
    )
    command_text = (
        "Canonical contextual transfer "
        f"{request.source_operation_id} -> "
        f"{request.destination_operation_id} "
        f"({request.requested_count})"
    )
    provider_output = {
        "source": "ui",
        "goal": command_text,
        "assistant_message": (
            "Authoritative contextual transfer identity was revalidated "
            "and submitted as one reciprocal operation edit."
        ),
        "override_level": "directive",
        "command_layer": "operation",
        "confidence": 1.0,
        "ttl_seconds": 300,
        "operations": [source_payload, destination_payload],
        "tags": [
            "web_gui",
            "contextual_transfer",
            "canonical_transfer_identity_v1",
            f"contextual_choice:{request.choice_id}",
        ],
        "rationale": (
            "Typed contextual transfer bypassed natural-language compilation "
            "after server-side authoritative revalidation."
        ),
    }
    return ContextualTransferPreparation(
        command_text=command_text,
        provider_output=provider_output,
        current_frame=current_frame,
    )


def _matching_transfer_entry(
    request: ContextualTransferRequest,
    transfer: Mapping[str, object],
) -> Mapping[str, object]:
    entries = transfer.get("entries")
    if not isinstance(entries, Sequence) or isinstance(
        entries,
        (str, bytes, bytearray),
    ):
        _reject(
            "transfer_entries_unavailable",
            "The authoritative transfer entry list is unavailable.",
        )
    endpoint_entries: list[Mapping[str, object]] = []
    source_entries = 0
    for value in entries:
        if not isinstance(value, Mapping):
            continue
        if str(value.get("source_owner_id", "") or "") != (
            request.source_operation_id
        ):
            continue
        source_entries += 1
        inputs = _mapping(value.get("atomic_revalidation_inputs"))
        counterpart = str(
            inputs.get("counterpart_operation_id", "")
            or value.get("destination_operation_id", "")
            or ""
        )
        if counterpart == request.destination_operation_id:
            endpoint_entries.append(value)
    if len(endpoint_entries) != 1:
        _reject(
            "transfer_endpoint_mismatch",
            "The exact source/destination transfer entry is not uniquely current.",
            source_entry_count=source_entries,
            endpoint_entry_count=len(endpoint_entries),
        )
    entry = endpoint_entries[0]
    choices = entry.get("recommended_resolution_choices")
    if not isinstance(choices, Sequence) or isinstance(
        choices,
        (str, bytes, bytearray),
    ) or request.action not in {str(choice) for choice in choices}:
        _reject(
            "transfer_action_not_recommended",
            "The selected transfer action is not currently recommended.",
        )
    inputs = _mapping(entry.get("atomic_revalidation_inputs"))
    if str(inputs.get("source_owner_id", "") or "") != (
        request.source_operation_id
    ):
        _reject(
            "atomic_source_endpoint_mismatch",
            "Atomic source identity does not match the selected endpoint.",
        )
    if str(inputs.get("counterpart_operation_id", "") or "") != (
        request.destination_operation_id
    ):
        _reject(
            "atomic_destination_endpoint_mismatch",
            "Atomic destination identity does not match the selected endpoint.",
        )
    requested_source_generation = inputs.get(
        "requested_source_generation",
        inputs.get("requested_generation"),
    )
    requested_destination_generation = inputs.get(
        "requested_counterpart_generation",
        inputs.get("counterpart_generation"),
    )
    if requested_source_generation not in {0, request.source_generation}:
        _reject(
            "atomic_source_generation_mismatch",
            "Atomic source request generation conflicts with the choice.",
        )
    if requested_destination_generation not in {
        0,
        request.destination_generation,
    }:
        _reject(
            "atomic_destination_generation_mismatch",
            "Atomic destination request generation conflicts with the choice.",
        )
    return entry


def _validate_active_operation(
    projection: Mapping[str, object],
    *,
    operation_id: str,
    role: str,
) -> None:
    if str(projection.get("operation_id", "") or "") != operation_id:
        _reject(
            f"{role}_operation_identity_mismatch",
            f"The {role} operation identity is inconsistent.",
        )
    ownership = _mapping(projection.get("operation_ownership"))
    if str(ownership.get("integrity_status", "") or "") != "valid":
        _reject(
            f"{role}_ownership_integrity_mismatch",
            f"The {role} ownership partition is not valid.",
        )
    lifetime = _mapping(projection.get("operation_lifetime"))
    completion = _mapping(projection.get("operation_completion"))
    if (
        lifetime.get("completed") is True
        or completion.get("terminal") is True
        or str(completion.get("state", "") or "").lower()
        in {"completed", "failed", "cancelled", "expired", "superseded"}
    ):
        _reject(
            f"{role}_operation_inactive",
            f"The {role} operation is no longer active.",
        )


def _operation_projection_index(
    overview: Mapping[str, object],
) -> dict[str, Mapping[str, object]]:
    operations = overview.get("operation_ownership")
    if not isinstance(operations, Sequence) or isinstance(
        operations,
        (str, bytes, bytearray),
    ):
        return {}
    return {
        str(operation.get("operation_id", "") or ""): operation
        for operation in operations
        if isinstance(operation, Mapping)
        and str(operation.get("operation_id", "") or "")
    }


def _vector_operation_index(
    vector: Mapping[str, object],
) -> dict[str, Mapping[str, object]]:
    operations = vector.get("operations")
    if not isinstance(operations, Sequence) or isinstance(
        operations,
        (str, bytes, bytearray),
    ):
        return {}
    return {
        str(operation.get("operation_id", "") or ""): operation
        for operation in operations
        if isinstance(operation, Mapping)
        and str(operation.get("operation_id", "") or "")
    }


def _operation_generation(operation: Mapping[str, object]) -> int:
    value = operation.get("generation")
    if type(value) is not int or value <= 0:
        return 0
    return value


def _composition(operation: Mapping[str, object]) -> list[dict[str, object]]:
    value = operation.get("composition_requirements")
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        _reject(
            "semantic_composition_unavailable",
            "The operation semantic composition is unavailable.",
        )
    result: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            _reject(
                "semantic_composition_invalid",
                "The operation semantic composition contains an invalid item.",
            )
        count = _required_current_int(
            "composition count",
            item.get("count"),
            minimum=1,
        )
        unit_type = _require_text("composition unit_type", item.get("unit_type"))
        role = str(item.get("role", "") or "")
        result.append(
            {"unit_type": unit_type, "count": count, "role": role}
        )
    return result


def _composition_count(requirements: Sequence[Mapping[str, object]]) -> int:
    return sum(int(requirement.get("count", 0) or 0) for requirement in requirements)


def _take_composition(
    requirements: Sequence[Mapping[str, object]],
    count: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    nonempty = [
        requirement
        for requirement in requirements
        if int(requirement.get("count", 0) or 0) > 0
    ]
    if len(nonempty) != 1:
        _reject(
            "semantic_transfer_selection_ambiguous",
            (
                "The runtime does not expose tag-to-role composition, so a "
                "mixed source composition cannot be selected safely."
            ),
        )
    remaining = count
    selection: list[dict[str, object]] = []
    after: list[dict[str, object]] = []
    for requirement in requirements:
        item = deepcopy(dict(requirement))
        available = int(item.get("count", 0) or 0)
        selected = min(available, remaining)
        if selected:
            chosen = deepcopy(item)
            chosen["count"] = selected
            selection.append(chosen)
            remaining -= selected
        kept = available - selected
        if kept:
            item["count"] = kept
            after.append(item)
    if remaining:
        _reject(
            "semantic_transfer_count_unavailable",
            "The semantic source composition cannot satisfy the requested count.",
            missing=remaining,
        )
    return selection, after


def _add_composition(
    requirements: Sequence[Mapping[str, object]],
    selection: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    result = [deepcopy(dict(item)) for item in requirements]
    index = {
        (str(item.get("unit_type", "")), str(item.get("role", ""))): item
        for item in result
    }
    for selected in selection:
        key = (
            str(selected.get("unit_type", "")),
            str(selected.get("role", "")),
        )
        existing = index.get(key)
        if existing is None:
            existing = deepcopy(dict(selected))
            result.append(existing)
            index[key] = existing
        else:
            existing["count"] = int(existing.get("count", 0) or 0) + int(
                selected.get("count", 0) or 0
            )
    return result


def _resize_operation_task(operation: dict[str, object], count: int) -> None:
    task = _mapping(operation.get("tactical_task"))
    if task:
        resized = deepcopy(dict(task))
        current_min = resized.get("min_units")
        current_max = resized.get("max_units")
        if type(current_min) is int and current_min > count:
            resized["min_units"] = count
        if type(current_max) is int and current_max > 0:
            resized["max_units"] = count
        operation["tactical_task"] = resized
    scope = _mapping(operation.get("scope"))
    if scope:
        resized_scope = deepcopy(dict(scope))
        current_min = resized_scope.get("min_units")
        current_max = resized_scope.get("max_units")
        if type(current_min) is int and current_min > count:
            resized_scope["min_units"] = count
        if type(current_max) is int and current_max > 0:
            resized_scope["max_units"] = count
        operation["scope"] = resized_scope


def _ensure_destination_roles(
    destination: dict[str, object],
    source: Mapping[str, object],
    selection: Sequence[Mapping[str, object]],
) -> None:
    raw_destination_roles = destination.get("unit_roles")
    destination_roles = (
        [deepcopy(dict(item)) for item in raw_destination_roles if isinstance(item, Mapping)]
        if isinstance(raw_destination_roles, Sequence)
        and not isinstance(raw_destination_roles, (str, bytes, bytearray))
        else []
    )
    raw_source_roles = source.get("unit_roles")
    source_roles = (
        [deepcopy(dict(item)) for item in raw_source_roles if isinstance(item, Mapping)]
        if isinstance(raw_source_roles, Sequence)
        and not isinstance(raw_source_roles, (str, bytes, bytearray))
        else []
    )
    present = {
        (str(item.get("unit_type", "")), str(item.get("role", "")))
        for item in destination_roles
    }
    source_index = {
        (str(item.get("unit_type", "")), str(item.get("role", ""))): item
        for item in source_roles
    }
    for selected in selection:
        key = (
            str(selected.get("unit_type", "")),
            str(selected.get("role", "")),
        )
        if key in present:
            continue
        role = source_index.get(key)
        if role is None:
            role = {
                "unit_type": key[0],
                "role": key[1],
                "priority": 0.5,
                "ability_policy": "",
            }
        destination_roles.append(deepcopy(dict(role)))
        present.add(key)
    destination["unit_roles"] = destination_roles


def _operation_edit(
    *,
    action: str,
    counterpart_operation_id: str,
    selection: Sequence[Mapping[str, object]],
    before: Sequence[Mapping[str, object]],
    after: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    return {
        "action": action,
        "counterpart_operation_id": counterpart_operation_id,
        "unit_selection": deepcopy(list(selection)),
        "before_composition": deepcopy(list(before)),
        "after_composition": deepcopy(list(after)),
        "explicit_override": True,
        "confirmation_policy": "auto",
    }


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _require_text(field_name: str, value: object) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _require_identifier(field_name: str, value: object) -> str:
    identifier = _require_text(field_name, value)
    if _SAFE_IDENTIFIER_RE.fullmatch(identifier) is None:
        raise ValueError(
            f"{field_name} must match {_SAFE_IDENTIFIER_RE.pattern!r}."
        )
    return identifier


def _require_session_epoch(field_name: str, value: object) -> str:
    canonical = canonical_battlefield_session_epoch(value)
    if canonical is None:
        raise ValueError(
            f"{field_name} must be a positive canonical decimal string within "
            "uint64 range, or a JSON-safe positive integer."
        )
    return canonical


def _require_int(
    field_name: str,
    value: object,
    *,
    minimum: int,
    maximum: int = 2_147_483_647,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(
            f"{field_name} must be an integer between {minimum} and {maximum}."
        )
    return value


def _required_current_session_epoch(
    field_name: str,
    value: object,
) -> str:
    canonical = canonical_battlefield_session_epoch(value)
    if canonical is None:
        _reject(
            "authoritative_field_invalid",
            f"Current authoritative field {field_name} is invalid.",
        )
    return canonical


def _required_current_int(
    field_name: str,
    value: object,
    *,
    minimum: int = 1,
) -> int:
    if type(value) is not int or value < minimum:
        _reject(
            "authoritative_field_invalid",
            f"Current authoritative field {field_name} is invalid.",
        )
    return value


def _reject(
    code: str,
    message: str,
    **details: object,
) -> None:
    raise ContextualTransferRejectedError(
        code,
        message,
        details=details,
    )
