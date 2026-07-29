"""Authoritative MicroMachine battlefield projection validation.

The browser and LLM are not allowed to derive battlefield ownership or safety.
This module validates the projection emitted by the patched C++ runtime and
passes the accepted document through without filling in missing values.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
import math
from typing import Final


BATTLEFIELD_OVERVIEW_SCHEMA_VERSION: Final[int] = 2
BATTLEFIELD_OVERVIEW_AUTHORITY: Final[str] = "micromachine_cpp"

_TERMINAL_COMPLETION_STATES: Final[frozenset[str]] = frozenset(
    {"completed", "failed", "cancelled", "expired", "superseded"}
)
_NON_TERMINAL_COMPLETION_STATES: Final[frozenset[str]] = frozenset(
    {"active", "blocked"}
)
_SAFE_TRANSFER_ADMISSIONS: Final[frozenset[str]] = frozenset(
    {"accepted", "allowed", "not_required"}
)
_INACTIVE_EMERGENCY_STATES: Final[frozenset[str]] = frozenset(
    {"none", "inactive", "not_required"}
)


@dataclass(frozen=True)
class BattlefieldProjectionIdentity:
    """Identity shared by telemetry, the in-game HUD, and the web projection."""

    update_id: str
    scope: str
    session_epoch: int
    generation: int
    stage: str
    game_frame: int

    def to_dict(self) -> dict[str, object]:
        return {
            "update_id": self.update_id,
            "scope": self.scope,
            "session_epoch": self.session_epoch,
            "generation": self.generation,
            "stage": self.stage,
            "game_frame": self.game_frame,
        }


@dataclass(frozen=True)
class BattlefieldProjectionBlocker:
    """One fail-closed integrity or freshness violation."""

    code: str
    path: str
    message: str
    details: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }
        if self.details:
            payload["details"] = deepcopy(dict(self.details))
        return payload


@dataclass(frozen=True)
class BattlefieldProjectionRejection:
    """An archive candidate that was not allowed to replace the projection."""

    source: str
    source_index: int | None
    identity: BattlefieldProjectionIdentity | None
    blockers: tuple[BattlefieldProjectionBlocker, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "source_index": self.source_index,
            "identity": self.identity.to_dict() if self.identity else None,
            "blockers": [blocker.to_dict() for blocker in self.blockers],
        }


@dataclass(frozen=True)
class BattlefieldProjectionResult:
    """Validated passthrough payload and web-facing integrity metadata."""

    battlefield_overview: Mapping[str, object] | None
    identity: BattlefieldProjectionIdentity | None
    blockers: tuple[BattlefieldProjectionBlocker, ...]
    integrity: Mapping[str, object]
    source: str = ""
    source_index: int | None = None
    rejected_candidates: tuple[BattlefieldProjectionRejection, ...] = ()

    @property
    def ok(self) -> bool:
        return (
            self.battlefield_overview is not None
            and self.identity is not None
            and not self.blockers
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "battlefield_overview": (
                deepcopy(dict(self.battlefield_overview))
                if self.battlefield_overview is not None
                else None
            ),
            "identity": self.identity.to_dict() if self.identity else None,
            "integrity": deepcopy(dict(self.integrity)),
            "blockers": [blocker.to_dict() for blocker in self.blockers],
            "source": self.source,
            "source_index": self.source_index,
            "rejected_candidates": [
                rejection.to_dict() for rejection in self.rejected_candidates
            ],
        }


class _Validation:
    def __init__(self) -> None:
        self.blockers: list[BattlefieldProjectionBlocker] = []
        self.checks: dict[str, object] = {
            "authority": "cpp_passthrough",
            "identity_valid": False,
            "ownership_partition_valid": False,
            "owner_counts_valid": False,
            "duplicate_owners_valid": False,
            "operation_blocks_valid": False,
            "base_readiness_valid": False,
            "launch_safety_valid": False,
            "transfer_safety_valid": False,
            "completion_valid": False,
            "monotonic": False,
        }

    def block(
        self,
        code: str,
        path: str,
        message: str,
        **details: object,
    ) -> None:
        self.blockers.append(
            BattlefieldProjectionBlocker(
                code=code,
                path=path,
                message=message,
                details=details,
            )
        )

    def finish(
        self,
        *,
        overview: Mapping[str, object] | None,
        identity: BattlefieldProjectionIdentity | None,
        source: str,
        source_index: int | None,
    ) -> BattlefieldProjectionResult:
        status = "valid" if not self.blockers else "blocked"
        integrity = {
            "status": status,
            **self.checks,
            "blocker_count": len(self.blockers),
        }
        return BattlefieldProjectionResult(
            battlefield_overview=(
                deepcopy(dict(overview))
                if overview is not None and not self.blockers
                else None
            ),
            identity=identity,
            blockers=tuple(self.blockers),
            integrity=integrity,
            source=source,
            source_index=source_index,
        )


@dataclass(frozen=True)
class _OwnershipEvidence:
    partition_tags: Mapping[str, frozenset[int]]
    owner_tags_by_id: Mapping[str, frozenset[int]]
    owner_generations_by_id: Mapping[str, int]


def validate_battlefield_overview(
    telemetry: Mapping[str, object] | None,
    *,
    expected_scope: str,
    previous_identity: BattlefieldProjectionIdentity | Mapping[str, object] | None = None,
    source: str = "latest",
    source_index: int | None = None,
) -> BattlefieldProjectionResult:
    """Validate one C++ ``battlefield_overview`` without deriving missing data."""

    validation = _Validation()
    scope = str(expected_scope or "").strip()
    if not scope:
        validation.block(
            "missing_expected_scope",
            "$.expected_scope",
            "A non-empty expected scope is required for authoritative selection.",
        )

    if not isinstance(telemetry, Mapping):
        validation.block(
            "missing_telemetry",
            "$",
            "Telemetry must be a mapping containing battlefield_overview.",
        )
        return validation.finish(
            overview=None,
            identity=None,
            source=source,
            source_index=source_index,
        )

    overview_value = telemetry.get("battlefield_overview")
    if not isinstance(overview_value, Mapping):
        validation.block(
            "missing_battlefield_overview",
            "$.battlefield_overview",
            "C++ telemetry did not provide an authoritative battlefield overview.",
        )
        return validation.finish(
            overview=None,
            identity=None,
            source=source,
            source_index=source_index,
        )
    overview = overview_value

    schema_version = _exact_int(overview.get("schema_version"))
    if schema_version != BATTLEFIELD_OVERVIEW_SCHEMA_VERSION:
        validation.block(
            "unsupported_schema_version",
            "$.battlefield_overview.schema_version",
            "The battlefield overview schema version is missing or unsupported.",
            expected=BATTLEFIELD_OVERVIEW_SCHEMA_VERSION,
            actual=overview.get("schema_version"),
        )
    authority = str(overview.get("authority", "") or "").strip()
    if authority != BATTLEFIELD_OVERVIEW_AUTHORITY:
        validation.block(
            "invalid_authority",
            "$.battlefield_overview.authority",
            "Only a C++ MicroMachine authoritative projection is accepted.",
            expected=BATTLEFIELD_OVERVIEW_AUTHORITY,
            actual=authority,
        )

    identity = _read_identity(
        overview,
        path="$.battlefield_overview",
        validation=validation,
        require_operation_id=False,
    )
    if identity is not None:
        if scope and identity.scope != scope:
            validation.block(
                "scope_mismatch",
                "$.battlefield_overview.identity.scope",
                "The battlefield projection belongs to a different scope.",
                expected=scope,
                actual=identity.scope,
            )
        validation.checks["identity_valid"] = not any(
            blocker.code.startswith(("missing_identity", "invalid_identity"))
            or blocker.code == "identity_field_mismatch"
            for blocker in validation.blockers
        )

    previous = _coerce_previous_identity(previous_identity, validation)
    if identity is not None and previous is not None:
        _validate_monotonic_identity(
            identity,
            previous,
            validation=validation,
            path="$.battlefield_overview.identity",
        )
    elif identity is not None:
        validation.checks["monotonic"] = True

    overview_frame = identity.game_frame if identity is not None else None
    owner_tags = _validate_ownership_partition(
        overview,
        overview_frame=overview_frame,
        validation=validation,
    )
    _validate_bases(
        overview.get("bases"),
        overview_frame=overview_frame,
        validation=validation,
    )
    _validate_transfer_availability(
        overview.get("transfer_availability"),
        overview_frame=overview_frame,
        owner_tags=owner_tags,
        validation=validation,
    )

    return validation.finish(
        overview=overview,
        identity=identity,
        source=source,
        source_index=source_index,
    )


def select_latest_battlefield_projection(
    *,
    latest_telemetry: Mapping[str, object] | None = None,
    telemetry_archive: Sequence[Mapping[str, object]] = (),
    expected_scope: str,
    previous_identity: BattlefieldProjectionIdentity | Mapping[str, object] | None = None,
) -> BattlefieldProjectionResult:
    """Select the latest monotonic projection for one scope.

    Invalid or stale archive records are retained as rejection metadata and
    cannot replace the current projection. A malformed or stale
    ``latest_telemetry`` record blocks instead of silently falling back.
    """

    selected: BattlefieldProjectionResult | None = None
    rejected: list[BattlefieldProjectionRejection] = []
    baseline = previous_identity

    for index, entry in enumerate(telemetry_archive):
        if not isinstance(entry, Mapping):
            rejected.append(
                BattlefieldProjectionRejection(
                    source="archive",
                    source_index=index,
                    identity=None,
                    blockers=(
                        BattlefieldProjectionBlocker(
                            code="invalid_archive_entry",
                            path=f"$.telemetry_archive[{index}]",
                            message="Telemetry archive entries must be mappings.",
                        ),
                    ),
                )
            )
            continue
        overview = entry.get("battlefield_overview")
        if overview is None:
            continue
        candidate_scope = _identity_scope(overview)
        if candidate_scope and candidate_scope != expected_scope:
            continue
        candidate = validate_battlefield_overview(
            entry,
            expected_scope=expected_scope,
            previous_identity=(
                selected.identity if selected is not None else baseline
            ),
            source="archive",
            source_index=index,
        )
        if not candidate.ok:
            rejected.append(_as_rejection(candidate))
            continue
        if (
            selected is not None
            and candidate.identity == selected.identity
            and candidate.battlefield_overview
            != selected.battlefield_overview
        ):
            rejected.append(
                BattlefieldProjectionRejection(
                    source="archive",
                    source_index=index,
                    identity=candidate.identity,
                    blockers=(
                        BattlefieldProjectionBlocker(
                            code="identity_collision",
                            path=(
                                f"$.telemetry_archive[{index}]"
                                ".battlefield_overview.identity"
                            ),
                            message=(
                                "The same projection identity carried a different "
                                "payload."
                            ),
                        ),
                    ),
                )
            )
            continue
        selected = candidate

    if latest_telemetry is not None:
        latest = validate_battlefield_overview(
            latest_telemetry,
            expected_scope=expected_scope,
            previous_identity=(
                selected.identity if selected is not None else baseline
            ),
            source="latest",
        )
        if not latest.ok:
            return _with_rejections(latest, rejected)
        if (
            selected is not None
            and latest.identity == selected.identity
            and latest.battlefield_overview != selected.battlefield_overview
        ):
            collision = BattlefieldProjectionResult(
                battlefield_overview=None,
                identity=latest.identity,
                blockers=(
                    BattlefieldProjectionBlocker(
                        code="identity_collision",
                        path="$.battlefield_overview.identity",
                        message=(
                            "The latest projection reused an identity with a "
                            "different payload."
                        ),
                    ),
                ),
                integrity={
                    **dict(latest.integrity),
                    "status": "blocked",
                    "blocker_count": 1,
                },
                source="latest",
            )
            return _with_rejections(collision, rejected)
        selected = latest

    if selected is None:
        result = BattlefieldProjectionResult(
            battlefield_overview=None,
            identity=None,
            blockers=(
                BattlefieldProjectionBlocker(
                    code="no_valid_battlefield_projection",
                    path="$.battlefield_overview",
                    message=(
                        "No valid authoritative battlefield projection was "
                        "available for the requested scope."
                    ),
                ),
            ),
            integrity={
                "status": "blocked",
                "authority": "cpp_passthrough",
                "identity_valid": False,
                "ownership_partition_valid": False,
                "owner_counts_valid": False,
                "duplicate_owners_valid": False,
                "operation_blocks_valid": False,
                "base_readiness_valid": False,
                "launch_safety_valid": False,
                "transfer_safety_valid": False,
                "completion_valid": False,
                "monotonic": False,
                "blocker_count": 1,
            },
        )
        return _with_rejections(result, rejected)

    return _with_rejections(selected, rejected)


def _validate_ownership_partition(
    overview: Mapping[str, object],
    *,
    overview_frame: int | None,
    validation: _Validation,
) -> _OwnershipEvidence:
    count_fields: dict[str, int | None] = {}
    for field_name in (
        "eligible_combat_count",
        "explicit_operation_owned_count",
        "autonomous_owned_count",
        "unassigned_count",
        "duplicate_owner_count",
    ):
        count_fields[field_name] = _required_nonnegative_int(
            overview.get(field_name),
            path=f"$.battlefield_overview.{field_name}",
            validation=validation,
        )

    operation_rows = _required_mapping_sequence(
        overview.get("operation_ownership"),
        path="$.battlefield_overview.operation_ownership",
        validation=validation,
    )
    autonomous_rows = _required_mapping_sequence(
        overview.get("autonomous_ownership"),
        path="$.battlefield_overview.autonomous_ownership",
        validation=validation,
    )
    unassigned_tags = _required_unit_tags(
        overview.get("unassigned_unit_tags"),
        path="$.battlefield_overview.unassigned_unit_tags",
        validation=validation,
    )

    explicit_tags: list[int] = []
    owner_tags_by_id: dict[str, frozenset[int]] = {}
    owner_generations_by_id: dict[str, int] = {}
    operation_count_sum = 0
    operation_blocks_start = len(validation.blockers)
    launch_blockers_start = len(validation.blockers)
    completion_blockers_start = len(validation.blockers)
    for index, operation in enumerate(operation_rows):
        path = f"$.battlefield_overview.operation_ownership[{index}]"
        operation_identity = _read_operation_identity(
            operation,
            path=path,
            overview_frame=overview_frame,
            validation=validation,
        )
        route = _required_mapping(
            operation.get("operation_route"),
            path=f"{path}.operation_route",
            validation=validation,
        )
        lifetime = _required_mapping(
            operation.get("operation_lifetime"),
            path=f"{path}.operation_lifetime",
            validation=validation,
        )
        ownership = _required_mapping(
            operation.get("operation_ownership"),
            path=f"{path}.operation_ownership",
            validation=validation,
        )
        launch = _required_mapping(
            operation.get("operation_launch_policy"),
            path=f"{path}.operation_launch_policy",
            validation=validation,
        )
        completion = _required_mapping(
            operation.get("operation_completion"),
            path=f"{path}.operation_completion",
            validation=validation,
        )

        if route is not None:
            _validate_operation_route(
                route,
                path=f"{path}.operation_route",
                validation=validation,
            )
        if ownership is not None:
            tags, owner_count = _validate_owner_block(
                ownership,
                path=f"{path}.operation_ownership",
                validation=validation,
            )
            explicit_tags.extend(tags)
            if owner_count is not None:
                operation_count_sum += owner_count
            operation_id = str(operation.get("operation_id", "") or "").strip()
            if operation_id:
                _record_owner_tags(
                    owner_tags_by_id,
                    owner_id=operation_id,
                    tags=tags,
                    path=f"{path}.operation_id",
                    validation=validation,
                )
                if operation_identity is not None:
                    owner_generations_by_id[operation_id] = (
                        operation_identity.generation
                    )
        if launch is not None:
            _validate_launch_policy(
                launch,
                path=f"{path}.operation_launch_policy",
                overview_frame=overview_frame,
                validation=validation,
            )
        if lifetime is not None and completion is not None:
            _validate_completion(
                lifetime,
                completion,
                path=path,
                overview_frame=overview_frame,
                operation_identity=operation_identity,
                validation=validation,
            )

    validation.checks["operation_blocks_valid"] = (
        len(validation.blockers) == operation_blocks_start
    )
    validation.checks["launch_safety_valid"] = not any(
        blocker.path.endswith("operation_launch_policy")
        or ".operation_launch_policy." in blocker.path
        for blocker in validation.blockers[launch_blockers_start:]
    )
    validation.checks["completion_valid"] = not any(
        ".operation_lifetime" in blocker.path
        or ".operation_completion" in blocker.path
        for blocker in validation.blockers[completion_blockers_start:]
    )

    autonomous_tags: list[int] = []
    autonomous_count_sum = 0
    for index, owner in enumerate(autonomous_rows):
        path = f"$.battlefield_overview.autonomous_ownership[{index}]"
        owner_id = str(owner.get("owner_id", "") or "").strip()
        if not owner_id:
            validation.block(
                "missing_autonomous_owner_id",
                f"{path}.owner_id",
                "Autonomous ownership rows require a stable owner_id.",
            )
        tags, owner_count = _validate_owner_block(
            owner,
            path=path,
            validation=validation,
        )
        autonomous_tags.extend(tags)
        if owner_count is not None:
            autonomous_count_sum += owner_count
        if owner_id:
            _record_owner_tags(
                owner_tags_by_id,
                owner_id=owner_id,
                tags=tags,
                path=f"{path}.owner_id",
                validation=validation,
            )

    explicit_count = count_fields["explicit_operation_owned_count"]
    autonomous_count = count_fields["autonomous_owned_count"]
    unassigned_count = count_fields["unassigned_count"]
    eligible_count = count_fields["eligible_combat_count"]
    reported_duplicate_count = count_fields["duplicate_owner_count"]

    if explicit_count is not None and operation_count_sum != explicit_count:
        validation.block(
            "explicit_owner_count_mismatch",
            "$.battlefield_overview.explicit_operation_owned_count",
            "The explicit owner summary does not match operation owner rows.",
            reported=explicit_count,
            owner_rows=operation_count_sum,
        )
    if autonomous_count is not None and autonomous_count_sum != autonomous_count:
        validation.block(
            "autonomous_owner_count_mismatch",
            "$.battlefield_overview.autonomous_owned_count",
            "The autonomous owner summary does not match autonomous owner rows.",
            reported=autonomous_count,
            owner_rows=autonomous_count_sum,
        )
    if unassigned_count is not None and len(unassigned_tags) != unassigned_count:
        validation.block(
            "unassigned_owner_count_mismatch",
            "$.battlefield_overview.unassigned_count",
            "The unassigned count does not match authoritative unassigned tags.",
            reported=unassigned_count,
            owner_tags=len(unassigned_tags),
        )

    counts = (explicit_count, autonomous_count, unassigned_count, eligible_count)
    if all(value is not None for value in counts):
        partition_sum = (
            int(explicit_count)
            + int(autonomous_count)
            + int(unassigned_count)
        )
        if partition_sum != eligible_count:
            validation.block(
                "ownership_equation_mismatch",
                "$.battlefield_overview.eligible_combat_count",
                (
                    "eligible combat must equal explicit operation owned plus "
                    "autonomous owned plus unassigned."
                ),
                eligible_combat_count=eligible_count,
                partition_sum=partition_sum,
            )

    all_tags = [*explicit_tags, *autonomous_tags, *unassigned_tags]
    actual_duplicate_count = sum(
        count - 1 for count in Counter(all_tags).values() if count > 1
    )
    if reported_duplicate_count is not None:
        if reported_duplicate_count != actual_duplicate_count:
            validation.block(
                "duplicate_owner_evidence_mismatch",
                "$.battlefield_overview.duplicate_owner_count",
                "Reported duplicate ownership does not match owner-tag evidence.",
                reported=reported_duplicate_count,
                observed=actual_duplicate_count,
            )
        if reported_duplicate_count != 0:
            validation.block(
                "duplicate_owners",
                "$.battlefield_overview.duplicate_owner_count",
                "Duplicate runtime ownership must be zero.",
                duplicate_owner_count=reported_duplicate_count,
            )

    if eligible_count is not None and len(all_tags) != eligible_count:
        validation.block(
            "eligible_owner_tag_count_mismatch",
            "$.battlefield_overview.eligible_combat_count",
            "Eligible combat count does not match all ownership tag evidence.",
            eligible_combat_count=eligible_count,
            owner_tag_assignments=len(all_tags),
        )

    ownership_codes = {
        "invalid_count",
        "missing_count",
        "owner_count_mismatch",
        "owner_integrity_invalid",
        "explicit_owner_count_mismatch",
        "autonomous_owner_count_mismatch",
        "unassigned_owner_count_mismatch",
        "eligible_owner_tag_count_mismatch",
    }
    validation.checks["owner_counts_valid"] = not any(
        blocker.code in ownership_codes for blocker in validation.blockers
    )
    validation.checks["ownership_partition_valid"] = not any(
        blocker.code == "ownership_equation_mismatch"
        for blocker in validation.blockers
    )
    validation.checks["duplicate_owners_valid"] = not any(
        blocker.code
        in {"duplicate_owner_evidence_mismatch", "duplicate_owners", "duplicate_unit_tag"}
        for blocker in validation.blockers
    )
    return _OwnershipEvidence(
        partition_tags={
            "explicit": frozenset(explicit_tags),
            "autonomous": frozenset(autonomous_tags),
            "unassigned": frozenset(unassigned_tags),
        },
        owner_tags_by_id=owner_tags_by_id,
        owner_generations_by_id=owner_generations_by_id,
    )


def _record_owner_tags(
    owner_tags_by_id: dict[str, frozenset[int]],
    *,
    owner_id: str,
    tags: Sequence[int],
    path: str,
    validation: _Validation,
) -> None:
    if owner_id in owner_tags_by_id:
        validation.block(
            "duplicate_owner_id",
            path,
            "Runtime owner IDs must be unique across explicit and autonomous owners.",
            owner_id=owner_id,
        )
        return
    owner_tags_by_id[owner_id] = frozenset(tags)


def _validate_owner_block(
    owner: Mapping[str, object],
    *,
    path: str,
    validation: _Validation,
) -> tuple[list[int], int | None]:
    owner_count = _required_nonnegative_int(
        owner.get("owner_count"),
        path=f"{path}.owner_count",
        validation=validation,
    )
    tags = _required_unit_tags(
        owner.get("owner_tags"),
        path=f"{path}.owner_tags",
        validation=validation,
    )
    if owner_count is not None and owner_count != len(tags):
        validation.block(
            "owner_count_mismatch",
            f"{path}.owner_count",
            "Owner count must equal the number of unique owner tags.",
            reported=owner_count,
            unique_owner_tags=len(tags),
        )
    integrity_status = str(owner.get("integrity_status", "") or "").strip()
    if integrity_status != "valid":
        validation.block(
            "owner_integrity_invalid",
            f"{path}.integrity_status",
            "C++ ownership integrity must be explicitly valid.",
            actual=integrity_status,
        )
    return tags, owner_count


def _validate_operation_route(
    route: Mapping[str, object],
    *,
    path: str,
    validation: _Validation,
) -> None:
    for field_name in (
        "requested_route_type",
        "applied_route_type",
        "location_intent",
        "target_type",
        "resolved_target_label",
        "target_evidence",
    ):
        _required_string(
            route.get(field_name),
            path=f"{path}.{field_name}",
            validation=validation,
        )
    for field_name in ("target_x", "target_y"):
        _required_number(
            route.get(field_name),
            path=f"{path}.{field_name}",
            validation=validation,
        )


def _validate_launch_policy(
    launch: Mapping[str, object],
    *,
    path: str,
    overview_frame: int | None,
    validation: _Validation,
) -> None:
    int_fields = (
        "min_units",
        "max_units",
        "launch_count",
        "missing_count",
    )
    counts = {
        field_name: _required_nonnegative_int(
            launch.get(field_name),
            path=f"{path}.{field_name}",
            validation=validation,
        )
        for field_name in int_fields
    }
    bool_fields = (
        "allow_partial_requested",
        "strict_scope",
        "partial_launch_allowed",
        "partial_launch_safe",
    )
    bool_values = {
        field_name: _required_bool(
            launch.get(field_name),
            path=f"{path}.{field_name}",
            validation=validation,
        )
        for field_name in bool_fields
    }
    decision = _required_string(
        launch.get("decision"),
        path=f"{path}.decision",
        validation=validation,
    )
    _required_string_allow_empty(
        launch.get("blocker"),
        path=f"{path}.blocker",
        validation=validation,
    )
    _required_sequence(
        launch.get("recommended_choices"),
        path=f"{path}.recommended_choices",
        validation=validation,
    )

    minimum = counts["min_units"]
    maximum = counts["max_units"]
    launch_count = counts["launch_count"]
    missing_count = counts["missing_count"]
    if minimum is not None and maximum is not None and minimum > maximum:
        validation.block(
            "launch_bounds_mismatch",
            path,
            "Operation launch min_units cannot exceed max_units.",
            min_units=minimum,
            max_units=maximum,
        )
    if (
        minimum is not None
        and launch_count is not None
        and missing_count is not None
        and missing_count != max(0, minimum - launch_count)
    ):
        validation.block(
            "launch_count_evidence_mismatch",
            f"{path}.missing_count",
            "C++ launch_count and missing_count evidence is internally inconsistent.",
            min_units=minimum,
            launch_count=launch_count,
            missing_count=missing_count,
        )

    evidence = _required_mapping(
        launch.get("safety_evidence"),
        path=f"{path}.safety_evidence",
        validation=validation,
    )
    if evidence is None:
        validation.block(
            "missing_launch_safety_evidence",
            f"{path}.safety_evidence",
            "Partial-launch safety cannot be inferred by Python or the browser.",
        )
        return

    evaluated_frame = _required_nonnegative_int(
        evidence.get("evaluated_at_frame"),
        path=f"{path}.safety_evidence.evaluated_at_frame",
        validation=validation,
    )
    protected_defense = _required_bool(
        evidence.get("protected_defense_minimum_respected"),
        path=(
            f"{path}.safety_evidence."
            "protected_defense_minimum_respected"
        ),
        validation=validation,
    )
    source_minimum = _required_bool(
        evidence.get("source_operation_minimum_respected"),
        path=(
            f"{path}.safety_evidence."
            "source_operation_minimum_respected"
        ),
        validation=validation,
    )
    transfer_admission = _required_string(
        evidence.get("transfer_admission"),
        path=f"{path}.safety_evidence.transfer_admission",
        validation=validation,
    )
    emergency_preemption = _required_string(
        evidence.get("emergency_preemption"),
        path=f"{path}.safety_evidence.emergency_preemption",
        validation=validation,
    )
    if (
        overview_frame is not None
        and evaluated_frame is not None
        and evaluated_frame != overview_frame
    ):
        validation.block(
            "stale_launch_safety_evidence",
            f"{path}.safety_evidence.evaluated_at_frame",
            "Launch safety evidence must be evaluated at the projection frame.",
            projection_frame=overview_frame,
            evaluated_at_frame=evaluated_frame,
        )

    safe = bool_values["partial_launch_safe"]
    safety_contradiction = (
        protected_defense is not True
        or source_minimum is not True
        or transfer_admission not in _SAFE_TRANSFER_ADMISSIONS
        or emergency_preemption not in _INACTIVE_EMERGENCY_STATES
    )
    if safe is True and safety_contradiction:
        validation.block(
            "contradictory_launch_safety",
            f"{path}.partial_launch_safe",
            "A safe launch claim contradicts its C++ safety evidence.",
        )
    is_partial = (
        minimum is not None
        and launch_count is not None
        and launch_count < minimum
    )
    if (
        is_partial
        and decision == "launch"
        and (
            bool_values["allow_partial_requested"] is not True
            or bool_values["partial_launch_allowed"] is not True
            or safe is not True
        )
    ):
        validation.block(
            "unsafe_launch_decision",
            f"{path}.decision",
            (
                "A partial launch requires explicit requested, allowed, and safe "
                "runtime evidence."
            ),
        )
    if decision == "launch" and safety_contradiction:
        validation.block(
            "unsafe_launch_decision",
            f"{path}.decision",
            "A launch decision contradicts its authoritative safety evidence.",
        )


def _validate_bases(
    value: object,
    *,
    overview_frame: int | None,
    validation: _Validation,
) -> None:
    start = len(validation.blockers)
    bases = _required_mapping_sequence(
        value,
        path="$.battlefield_overview.bases",
        validation=validation,
    )
    for index, base in enumerate(bases):
        path = f"$.battlefield_overview.bases[{index}]"
        _required_string(
            base.get("base_id"),
            path=f"{path}.base_id",
            validation=validation,
        )
        _required_string(
            base.get("semantic_anchor"),
            path=f"{path}.semantic_anchor",
            validation=validation,
        )
        readiness = _required_mapping(
            base.get("base_readiness"),
            path=f"{path}.base_readiness",
            validation=validation,
        )
        if readiness is None:
            continue
        _required_string(
            readiness.get("readiness_state"),
            path=f"{path}.base_readiness.readiness_state",
            validation=validation,
        )
        _required_string(
            readiness.get("reason"),
            path=f"{path}.base_readiness.reason",
            validation=validation,
        )
        for field_name in (
            "ground_threat",
            "air_threat",
            "observed_enemy_strength",
        ):
            _required_nonnegative_number(
                readiness.get(field_name),
                path=f"{path}.base_readiness.{field_name}",
                validation=validation,
            )
        evidence_frame = _required_nonnegative_int(
            readiness.get("last_evidence_frame"),
            path=f"{path}.base_readiness.last_evidence_frame",
            validation=validation,
        )
        _required_string(
            readiness.get("evidence_class"),
            path=f"{path}.base_readiness.evidence_class",
            validation=validation,
        )
        assigned_defender_count = _required_nonnegative_int(
            readiness.get("assigned_defender_count"),
            path=f"{path}.base_readiness.assigned_defender_count",
            validation=validation,
        )
        ground_capable_count = _required_nonnegative_int(
            readiness.get("ground_capable_defender_count"),
            path=(
                f"{path}.base_readiness."
                "ground_capable_defender_count"
            ),
            validation=validation,
        )
        air_capable_count = _required_nonnegative_int(
            readiness.get("air_capable_defender_count"),
            path=(
                f"{path}.base_readiness."
                "air_capable_defender_count"
            ),
            validation=validation,
        )
        required_defender_count = _required_nonnegative_int(
            readiness.get("required_defender_count"),
            path=f"{path}.base_readiness.required_defender_count",
            validation=validation,
        )
        required_ground_count = _required_nonnegative_int(
            readiness.get("required_ground_defender_count"),
            path=(
                f"{path}.base_readiness."
                "required_ground_defender_count"
            ),
            validation=validation,
        )
        required_air_count = _required_nonnegative_int(
            readiness.get("required_air_defender_count"),
            path=(
                f"{path}.base_readiness."
                "required_air_defender_count"
            ),
            validation=validation,
        )
        protected = _required_mapping_sequence(
            readiness.get("protected_minimum"),
            path=f"{path}.base_readiness.protected_minimum",
            validation=validation,
        )
        for protected_index, minimum in enumerate(protected):
            minimum_path = (
                f"{path}.base_readiness.protected_minimum[{protected_index}]"
            )
            _required_string(
                minimum.get("family"),
                path=f"{minimum_path}.family",
                validation=validation,
            )
            _required_string(
                minimum.get("role"),
                path=f"{minimum_path}.role",
                validation=validation,
            )
            _required_nonnegative_int(
                minimum.get("count"),
                path=f"{minimum_path}.count",
                validation=validation,
            )
        if (
            overview_frame is not None
            and evidence_frame is not None
            and evidence_frame > overview_frame
        ):
            validation.block(
                "future_base_readiness_evidence",
                f"{path}.base_readiness.last_evidence_frame",
                "Base readiness evidence cannot be newer than the projection.",
                projection_frame=overview_frame,
                evidence_frame=evidence_frame,
            )
        ground_threat = readiness.get("ground_threat")
        air_threat = readiness.get("air_threat")
        if (
            assigned_defender_count is not None
            and ground_capable_count is not None
            and ground_capable_count > assigned_defender_count
        ):
            validation.block(
                "defender_capability_count_mismatch",
                f"{path}.base_readiness.ground_capable_defender_count",
                "Ground-capable defenders cannot exceed assigned defenders.",
                assigned=assigned_defender_count,
                capable=ground_capable_count,
            )
        if (
            assigned_defender_count is not None
            and air_capable_count is not None
            and air_capable_count > assigned_defender_count
        ):
            validation.block(
                "defender_capability_count_mismatch",
                f"{path}.base_readiness.air_capable_defender_count",
                "Air-capable defenders cannot exceed assigned defenders.",
                assigned=assigned_defender_count,
                capable=air_capable_count,
            )
        if (
            required_defender_count is not None
            and required_ground_count is not None
            and required_air_count is not None
            and required_defender_count
            < required_ground_count + required_air_count
        ):
            validation.block(
                "defender_requirement_count_mismatch",
                f"{path}.base_readiness.required_defender_count",
                (
                    "Total required defenders must cover ground and air "
                    "requirements without double-counting one unit."
                ),
                total=required_defender_count,
                ground=required_ground_count,
                air=required_air_count,
            )
        if (
            isinstance(ground_threat, (int, float))
            and not isinstance(ground_threat, bool)
            and required_ground_count is not None
            and required_ground_count < math.ceil(float(ground_threat))
        ):
            validation.block(
                "ground_threat_requirement_mismatch",
                f"{path}.base_readiness.required_ground_defender_count",
                "Visible ground threats require ground-capable defenders.",
                threat=ground_threat,
                required=required_ground_count,
            )
        if (
            isinstance(air_threat, (int, float))
            and not isinstance(air_threat, bool)
            and required_air_count is not None
            and required_air_count < math.ceil(float(air_threat))
        ):
            validation.block(
                "air_threat_requirement_mismatch",
                f"{path}.base_readiness.required_air_defender_count",
                "Visible air threats require air-capable defenders.",
                threat=air_threat,
                required=required_air_count,
            )
        readiness_state = str(readiness.get("readiness_state", "") or "")
        if readiness_state == "ready":
            capability_shortfall = (
                assigned_defender_count is None
                or ground_capable_count is None
                or air_capable_count is None
                or required_defender_count is None
                or required_ground_count is None
                or required_air_count is None
                or assigned_defender_count < required_defender_count
                or ground_capable_count < required_ground_count
                or air_capable_count < required_air_count
            )
            if capability_shortfall:
                validation.block(
                    "incompatible_defender_readiness",
                    f"{path}.base_readiness.readiness_state",
                    (
                        "A base cannot be ready when assigned defenders cannot "
                        "attack every observed threat domain."
                    ),
                )
    validation.checks["base_readiness_valid"] = len(validation.blockers) == start


def _validate_transfer_availability(
    value: object,
    *,
    overview_frame: int | None,
    owner_tags: _OwnershipEvidence,
    validation: _Validation,
) -> None:
    start = len(validation.blockers)
    transfer = _required_mapping(
        value,
        path="$.battlefield_overview.transfer_availability",
        validation=validation,
    )
    if transfer is None:
        validation.block(
            "missing_transfer_safety_evidence",
            "$.battlefield_overview.transfer_availability",
            "Transfer safety cannot be inferred by Python or the browser.",
        )
        return

    evaluated_frame = _required_nonnegative_int(
        transfer.get("evaluated_at_frame"),
        path="$.battlefield_overview.transfer_availability.evaluated_at_frame",
        validation=validation,
    )
    atomic_required = _required_bool(
        transfer.get("atomic_revalidation_required"),
        path=(
            "$.battlefield_overview.transfer_availability."
            "atomic_revalidation_required"
        ),
        validation=validation,
    )
    if atomic_required is not True:
        validation.block(
            "atomic_transfer_revalidation_disabled",
            (
                "$.battlefield_overview.transfer_availability."
                "atomic_revalidation_required"
            ),
            "Transfer admission must be atomically revalidated by C++.",
        )
    if (
        overview_frame is not None
        and evaluated_frame is not None
        and evaluated_frame != overview_frame
    ):
        validation.block(
            "stale_transfer_safety_evidence",
            "$.battlefield_overview.transfer_availability.evaluated_at_frame",
            "Transfer availability must be evaluated at the projection frame.",
            projection_frame=overview_frame,
            evaluated_at_frame=evaluated_frame,
        )

    entries = _required_mapping_sequence(
        transfer.get("entries"),
        path="$.battlefield_overview.transfer_availability.entries",
        validation=validation,
    )
    known_owner_tags = (
        set().union(*owner_tags.partition_tags.values())
        if owner_tags.partition_tags
        else set()
    )
    for index, entry in enumerate(entries):
        path = f"$.battlefield_overview.transfer_availability.entries[{index}]"
        source_owner_id = _required_string(
            entry.get("source_owner_id"),
            path=f"{path}.source_owner_id",
            validation=validation,
        )
        source_count = _required_nonnegative_int(
            entry.get("source_owner_count"),
            path=f"{path}.source_owner_count",
            validation=validation,
        )
        protected_minimum = _required_nonnegative_int(
            entry.get("protected_minimum"),
            path=f"{path}.protected_minimum",
            validation=validation,
        )
        transferable_count = _required_nonnegative_int(
            entry.get("transferable_count"),
            path=f"{path}.transferable_count",
            validation=validation,
        )
        transfer_safe = _required_bool(
            entry.get("transfer_safe"),
            path=f"{path}.transfer_safe",
            validation=validation,
        )
        blocker = _required_string_allow_empty(
            entry.get("atomic_runtime_blocker"),
            path=f"{path}.atomic_runtime_blocker",
            validation=validation,
        )
        _required_sequence(
            entry.get("recommended_resolution_choices"),
            path=f"{path}.recommended_resolution_choices",
            validation=validation,
        )
        candidate_tags = _required_unit_tags(
            entry.get("transferable_unit_tags"),
            path=f"{path}.transferable_unit_tags",
            validation=validation,
        )
        if transferable_count is not None and len(candidate_tags) != transferable_count:
            validation.block(
                "transferable_count_mismatch",
                f"{path}.transferable_count",
                "Transferable count must match authoritative transferable tags.",
                reported=transferable_count,
                owner_tags=len(candidate_tags),
            )
        unknown_tags = sorted(set(candidate_tags) - known_owner_tags)
        if unknown_tags:
            validation.block(
                "unknown_transferable_owner",
                f"{path}.transferable_unit_tags",
                "Transfer candidates must already belong to the ownership partition.",
                unknown_tags=unknown_tags,
            )
        source_tags = (
            owner_tags.owner_tags_by_id.get(source_owner_id)
            if source_owner_id is not None
            else None
        )
        if source_owner_id is not None and source_tags is None:
            validation.block(
                "unknown_transfer_source_owner",
                f"{path}.source_owner_id",
                "Transfer source must match an authoritative runtime owner.",
                source_owner_id=source_owner_id,
            )
        if (
            source_tags is not None
            and source_count is not None
            and source_count != len(source_tags)
        ):
            validation.block(
                "transfer_source_owner_count_mismatch",
                f"{path}.source_owner_count",
                "Transfer source count must match that owner's authoritative tags.",
                source_owner_id=source_owner_id,
                reported=source_count,
                owner_tags=len(source_tags),
            )
        wrong_source_tags = (
            sorted(set(candidate_tags) - set(source_tags))
            if source_tags is not None
            else []
        )
        if wrong_source_tags:
            validation.block(
                "transfer_source_owner_mismatch",
                f"{path}.transferable_unit_tags",
                "Transfer candidates must belong to the named source owner.",
                source_owner_id=source_owner_id,
                mismatched_tags=wrong_source_tags,
            )
        _validate_atomic_revalidation_inputs(
            entry.get("atomic_revalidation_inputs"),
            path=f"{path}.atomic_revalidation_inputs",
            entry_source_owner_id=source_owner_id,
            transferable_tags=candidate_tags,
            owner_tags=owner_tags,
            validation=validation,
        )
        if (
            source_count is not None
            and protected_minimum is not None
            and transferable_count is not None
            and transferable_count > max(0, source_count - protected_minimum)
        ):
            validation.block(
                "transfer_protected_minimum_violation",
                f"{path}.transferable_count",
                "Transfer availability exceeds the C++ protected minimum bound.",
                source_owner_count=source_count,
                protected_minimum=protected_minimum,
                transferable_count=transferable_count,
            )
        if transfer_safe is True and blocker:
            validation.block(
                "contradictory_transfer_safety",
                f"{path}.transfer_safe",
                "A safe transfer claim cannot carry an atomic runtime blocker.",
                blocker=blocker,
            )

        evidence = _required_mapping(
            entry.get("safety_evidence"),
            path=f"{path}.safety_evidence",
            validation=validation,
        )
        if evidence is None:
            validation.block(
                "missing_transfer_safety_evidence",
                f"{path}.safety_evidence",
                "Transfer safety cannot be inferred by Python or the browser.",
            )
            continue
        evidence_frame = _required_nonnegative_int(
            evidence.get("evaluated_at_frame"),
            path=f"{path}.safety_evidence.evaluated_at_frame",
            validation=validation,
        )
        minimum_respected = _required_bool(
            evidence.get("protected_minimum_respected"),
            path=f"{path}.safety_evidence.protected_minimum_respected",
            validation=validation,
        )
        entry_atomic = _required_bool(
            evidence.get("atomic_revalidation_required"),
            path=f"{path}.safety_evidence.atomic_revalidation_required",
            validation=validation,
        )
        if (
            overview_frame is not None
            and evidence_frame is not None
            and evidence_frame != overview_frame
        ):
            validation.block(
                "stale_transfer_safety_evidence",
                f"{path}.safety_evidence.evaluated_at_frame",
                "Transfer safety evidence must match the projection frame.",
                projection_frame=overview_frame,
                evaluated_at_frame=evidence_frame,
            )
        if transfer_safe is True and (
            minimum_respected is not True or entry_atomic is not True
        ):
            validation.block(
                "contradictory_transfer_safety",
                f"{path}.transfer_safe",
                "A safe transfer claim contradicts its C++ safety evidence.",
            )
    validation.checks["transfer_safety_valid"] = len(validation.blockers) == start


def _validate_atomic_revalidation_inputs(
    value: object,
    *,
    path: str,
    entry_source_owner_id: str | None,
    transferable_tags: Sequence[int],
    owner_tags: _OwnershipEvidence,
    validation: _Validation,
) -> None:
    inputs = _required_mapping(
        value,
        path=path,
        validation=validation,
    )
    if inputs is None:
        validation.block(
            "missing_atomic_revalidation_inputs",
            path,
            "C++ must provide the exact atomic transfer admission inputs.",
        )
        return

    required_fields = (
        "requested",
        "selected_unit_tags",
        "requested_count",
        "source_owner_id",
        "action",
        "requested_generation",
        "counterpart_operation_id",
        "counterpart_action",
        "counterpart_generation",
    )
    for field_name in required_fields:
        if field_name not in inputs:
            validation.block(
                "missing_atomic_revalidation_input",
                f"{path}.{field_name}",
                "C++ omitted a required atomic transfer admission input.",
                field=field_name,
            )

    requested = _required_bool(
        inputs.get("requested"),
        path=f"{path}.requested",
        validation=validation,
    )
    selected_tags = _required_unit_tags(
        inputs.get("selected_unit_tags"),
        path=f"{path}.selected_unit_tags",
        validation=validation,
    )
    requested_count = _required_nonnegative_int(
        inputs.get("requested_count"),
        path=f"{path}.requested_count",
        validation=validation,
    )
    source_owner_id = _required_string(
        inputs.get("source_owner_id"),
        path=f"{path}.source_owner_id",
        validation=validation,
    )
    source_action = _required_string(
        inputs.get("action"),
        path=f"{path}.action",
        validation=validation,
    )
    source_generation = _required_nonnegative_int(
        inputs.get("requested_generation"),
        path=f"{path}.requested_generation",
        validation=validation,
    )
    counterpart_operation_id = _required_string_allow_empty(
        inputs.get("counterpart_operation_id"),
        path=f"{path}.counterpart_operation_id",
        validation=validation,
    )
    counterpart_action = _required_string_allow_empty(
        inputs.get("counterpart_action"),
        path=f"{path}.counterpart_action",
        validation=validation,
    )
    counterpart_generation = _required_nonnegative_int(
        inputs.get("counterpart_generation"),
        path=f"{path}.counterpart_generation",
        validation=validation,
    )

    if selected_tags != list(transferable_tags):
        validation.block(
            "atomic_selected_tags_mismatch",
            f"{path}.selected_unit_tags",
            (
                "Atomic selected tags must exactly match the authoritative "
                "transferable tag selection."
            ),
            selected_unit_tags=selected_tags,
            transferable_unit_tags=list(transferable_tags),
        )
    if requested_count is not None:
        expected_requested_count = len(selected_tags) if requested is True else 0
        if requested_count != expected_requested_count:
            validation.block(
                "atomic_requested_count_mismatch",
                f"{path}.requested_count",
                "Requested count does not match the exact selected tag contract.",
                requested=requested,
                reported=requested_count,
                expected=expected_requested_count,
            )
    if (
        source_owner_id is not None
        and entry_source_owner_id is not None
        and source_owner_id != entry_source_owner_id
    ):
        validation.block(
            "atomic_source_owner_mismatch",
            f"{path}.source_owner_id",
            "Atomic source owner must match the transfer availability entry.",
            entry_source_owner_id=entry_source_owner_id,
            atomic_source_owner_id=source_owner_id,
        )

    source_tags = (
        owner_tags.owner_tags_by_id.get(source_owner_id)
        if source_owner_id is not None
        else None
    )
    if source_owner_id is not None and source_tags is None:
        validation.block(
            "atomic_source_owner_mismatch",
            f"{path}.source_owner_id",
            "Atomic source owner is not present in authoritative ownership.",
            source_owner_id=source_owner_id,
        )
    source_selection_mismatch = (
        sorted(set(selected_tags) - set(source_tags))
        if source_tags is not None
        else []
    )
    if source_selection_mismatch:
        validation.block(
            "atomic_selected_source_ownership_mismatch",
            f"{path}.selected_unit_tags",
            "Atomic selected tags must all belong to the exact source owner.",
            source_owner_id=source_owner_id,
            mismatched_tags=source_selection_mismatch,
        )

    source_owner_generation = (
        owner_tags.owner_generations_by_id.get(source_owner_id)
        if source_owner_id is not None
        else None
    )
    if requested is True:
        if source_action != "transfer_out":
            validation.block(
                "atomic_source_action_mismatch",
                f"{path}.action",
                (
                    "The named source owner must carry the exact transfer_out "
                    "action."
                ),
                actual=source_action,
                expected="transfer_out",
            )
        if (
            source_owner_generation is None
            or source_generation != source_owner_generation
        ):
            validation.block(
                "atomic_source_generation_mismatch",
                f"{path}.requested_generation",
                "Atomic source generation must match the source operation.",
                source_owner_id=source_owner_id,
                reported=source_generation,
                operation_generation=source_owner_generation,
            )
        if not counterpart_operation_id:
            validation.block(
                "atomic_counterpart_operation_mismatch",
                f"{path}.counterpart_operation_id",
                "Requested transfer requires an explicit counterpart operation.",
            )
        elif counterpart_operation_id == source_owner_id:
            validation.block(
                "atomic_counterpart_operation_mismatch",
                f"{path}.counterpart_operation_id",
                "Transfer source and counterpart operation must be different.",
                operation_id=counterpart_operation_id,
            )
        counterpart_owner_generation = (
            owner_tags.owner_generations_by_id.get(counterpart_operation_id)
            if counterpart_operation_id
            else None
        )
        if counterpart_owner_generation is None:
            validation.block(
                "atomic_counterpart_operation_mismatch",
                f"{path}.counterpart_operation_id",
                "Counterpart operation is not present in authoritative ownership.",
                counterpart_operation_id=counterpart_operation_id,
            )
        expected_counterpart_action = "transfer_in"
        if counterpart_action != expected_counterpart_action:
            validation.block(
                "atomic_counterpart_action_mismatch",
                f"{path}.counterpart_action",
                "Counterpart action must be the reciprocal transfer action.",
                source_action=source_action,
                reported=counterpart_action,
                expected=expected_counterpart_action,
            )
        if (
            counterpart_owner_generation is None
            or counterpart_generation != counterpart_owner_generation
        ):
            validation.block(
                "atomic_counterpart_generation_mismatch",
                f"{path}.counterpart_generation",
                "Atomic counterpart generation must match the counterpart operation.",
                counterpart_operation_id=counterpart_operation_id,
                reported=counterpart_generation,
                operation_generation=counterpart_owner_generation,
            )
    elif requested is False:
        if source_action != "availability":
            validation.block(
                "atomic_source_action_mismatch",
                f"{path}.action",
                "Non-requested transfer projection must use availability action.",
                actual=source_action,
            )
        if counterpart_operation_id or counterpart_action or counterpart_generation:
            validation.block(
                "atomic_unrequested_counterpart_mismatch",
                path,
                "Availability-only projection cannot carry counterpart identity.",
            )
        if source_owner_generation is not None and source_generation not in {
            0,
            source_owner_generation,
        }:
            validation.block(
                "atomic_source_generation_mismatch",
                f"{path}.requested_generation",
                "Availability source generation conflicts with the source operation.",
                reported=source_generation,
                operation_generation=source_owner_generation,
            )


def _validate_completion(
    lifetime: Mapping[str, object],
    completion: Mapping[str, object],
    *,
    path: str,
    overview_frame: int | None,
    operation_identity: BattlefieldProjectionIdentity | None,
    validation: _Validation,
) -> None:
    lifetime_state = _required_string(
        lifetime.get("completion_state"),
        path=f"{path}.operation_lifetime.completion_state",
        validation=validation,
    )
    _required_string(
        lifetime.get("mode"),
        path=f"{path}.operation_lifetime.mode",
        validation=validation,
    )
    _required_sequence(
        lifetime.get("completion_conditions"),
        path=f"{path}.operation_lifetime.completion_conditions",
        validation=validation,
    )
    issued_at_frame = _required_nonnegative_int(
        lifetime.get("issued_at_frame"),
        path=f"{path}.operation_lifetime.issued_at_frame",
        validation=validation,
    )
    _required_nonnegative_int(
        lifetime.get("duration_seconds"),
        path=f"{path}.operation_lifetime.duration_seconds",
        validation=validation,
    )
    deadline_frame = _required_nonnegative_int(
        lifetime.get("deadline_frame"),
        path=f"{path}.operation_lifetime.deadline_frame",
        validation=validation,
    )
    _required_bool(
        lifetime.get("standing"),
        path=f"{path}.operation_lifetime.standing",
        validation=validation,
    )
    lifetime_completed = _required_bool(
        lifetime.get("completed"),
        path=f"{path}.operation_lifetime.completed",
        validation=validation,
    )
    lifetime_reason = _required_string_allow_empty(
        lifetime.get("completion_reason"),
        path=f"{path}.operation_lifetime.completion_reason",
        validation=validation,
    )
    lifetime_frame = _required_nonnegative_int(
        lifetime.get("completed_frame"),
        path=f"{path}.operation_lifetime.completed_frame",
        validation=validation,
    )

    for field_name in (
        "movement_observed",
        "engagement_observed",
        "target_reached",
        "terminal",
    ):
        _required_bool(
            completion.get(field_name),
            path=f"{path}.operation_completion.{field_name}",
            validation=validation,
        )
    terminal = completion.get("terminal")
    completion_state = _required_string(
        completion.get("state"),
        path=f"{path}.operation_completion.state",
        validation=validation,
    )
    completion_reason = _required_string_allow_empty(
        completion.get("reason"),
        path=f"{path}.operation_completion.reason",
        validation=validation,
    )
    completion_frame = _required_nonnegative_int(
        completion.get("frame"),
        path=f"{path}.operation_completion.frame",
        validation=validation,
    )
    completion_generation = _required_positive_int(
        completion.get("generation"),
        path=f"{path}.operation_completion.generation",
        validation=validation,
    )

    if (
        operation_identity is not None
        and completion_generation is not None
        and completion_generation != operation_identity.generation
    ):
        validation.block(
            "completion_generation_mismatch",
            f"{path}.operation_completion.generation",
            "Completion evidence must match the operation generation.",
            operation_generation=operation_identity.generation,
            completion_generation=completion_generation,
        )
    if lifetime_state and completion_state and lifetime_state != completion_state:
        validation.block(
            "completion_state_mismatch",
            f"{path}.operation_completion.state",
            "Lifetime and completion blocks disagree about operation state.",
            lifetime_state=lifetime_state,
            completion_state=completion_state,
        )
    if lifetime_reason is not None and completion_reason is not None:
        if lifetime_reason != completion_reason:
            validation.block(
                "completion_reason_mismatch",
                f"{path}.operation_completion.reason",
                "Lifetime and completion blocks disagree about terminal reason.",
            )
    if lifetime_frame is not None and completion_frame is not None:
        if lifetime_frame != completion_frame:
            validation.block(
                "completion_frame_mismatch",
                f"{path}.operation_completion.frame",
                "Lifetime and completion blocks disagree about terminal frame.",
            )

    if completion_state in _TERMINAL_COMPLETION_STATES:
        if terminal is not True or lifetime_completed is not True:
            validation.block(
                "terminal_completion_not_marked",
                f"{path}.operation_completion.terminal",
                "Terminal operation state requires explicit terminal/completed flags.",
            )
        if not completion_reason or not completion_frame:
            validation.block(
                "missing_terminal_completion_identity",
                f"{path}.operation_completion",
                "Terminal completion requires a reason and non-zero frame.",
            )
    elif completion_state in _NON_TERMINAL_COMPLETION_STATES:
        if terminal is not False or lifetime_completed is not False:
            validation.block(
                "nonterminal_completion_marked_terminal",
                f"{path}.operation_completion.terminal",
                "Active or blocked operations cannot be marked terminal.",
            )
        if completion_reason or completion_frame:
            validation.block(
                "nonterminal_completion_has_terminal_identity",
                f"{path}.operation_completion",
                "Non-terminal operations cannot carry terminal reason/frame.",
            )
    elif completion_state:
        validation.block(
            "invalid_completion_state",
            f"{path}.operation_completion.state",
            "Completion state is not part of the authoritative contract.",
            actual=completion_state,
        )

    if (
        overview_frame is not None
        and completion_frame is not None
        and completion_frame > overview_frame
    ):
        validation.block(
            "future_completion_frame",
            f"{path}.operation_completion.frame",
            "Completion evidence cannot be newer than the projection.",
            projection_frame=overview_frame,
            completion_frame=completion_frame,
        )
    if (
        overview_frame is not None
        and issued_at_frame is not None
        and issued_at_frame > overview_frame
    ):
        validation.block(
            "future_operation_issue_frame",
            f"{path}.operation_lifetime.issued_at_frame",
            "Operation issue frame cannot be newer than the projection.",
            projection_frame=overview_frame,
            issued_at_frame=issued_at_frame,
        )
    if (
        issued_at_frame is not None
        and deadline_frame is not None
        and deadline_frame != 0
        and deadline_frame < issued_at_frame
    ):
        validation.block(
            "invalid_operation_deadline",
            f"{path}.operation_lifetime.deadline_frame",
            "A non-zero operation deadline cannot precede its issue frame.",
            issued_at_frame=issued_at_frame,
            deadline_frame=deadline_frame,
        )


def _read_operation_identity(
    operation: Mapping[str, object],
    *,
    path: str,
    overview_frame: int | None,
    validation: _Validation,
) -> BattlefieldProjectionIdentity | None:
    identity, operation_id = _read_identity_components(
        operation,
        path=path,
        validation=validation,
        require_operation_id=True,
    )
    if identity is None:
        return None
    if operation_id is None:
        return identity
    direct_operation_id = operation.get("operation_id")
    if not isinstance(direct_operation_id, str) or not direct_operation_id.strip():
        validation.block(
            "missing_operation_id",
            f"{path}.operation_id",
            "Operation projection rows require a direct operation_id.",
        )
    elif direct_operation_id.strip() != operation_id:
        validation.block(
            "identity_field_mismatch",
            f"{path}.operation_id",
            "Operation identity conflicts with the operation payload.",
            identity=operation_id,
            payload=direct_operation_id,
        )
    direct_generation = operation.get("generation")
    if _exact_int(direct_generation) is None or int(direct_generation) <= 0:
        validation.block(
            "missing_operation_generation",
            f"{path}.generation",
            "Operation projection rows require a positive direct generation.",
            actual=direct_generation,
        )
    elif int(direct_generation) != identity.generation:
        validation.block(
            "identity_field_mismatch",
            f"{path}.generation",
            "Operation identity conflicts with the operation generation.",
            identity=identity.generation,
            payload=direct_generation,
        )
    if overview_frame is not None and identity.game_frame > overview_frame:
        validation.block(
            "future_operation_identity",
            f"{path}.identity.game_frame",
            "Operation identity cannot be newer than the battlefield projection.",
            projection_frame=overview_frame,
            operation_frame=identity.game_frame,
        )
    return identity


def _read_identity(
    value: Mapping[str, object],
    *,
    path: str,
    validation: _Validation,
    require_operation_id: bool,
) -> BattlefieldProjectionIdentity | None:
    identity, _ = _read_identity_components(
        value,
        path=path,
        validation=validation,
        require_operation_id=require_operation_id,
    )
    return identity


def _read_identity_components(
    value: Mapping[str, object],
    *,
    path: str,
    validation: _Validation,
    require_operation_id: bool,
) -> tuple[BattlefieldProjectionIdentity | None, str | None]:
    nested = value.get("identity")
    if not isinstance(nested, Mapping):
        validation.block(
            "missing_identity",
            f"{path}.identity",
            "The C++ projection must provide a complete identity block.",
        )
        return (None, None)

    update_id = _identity_string(
        nested.get("update_id"),
        path=f"{path}.identity.update_id",
        validation=validation,
    )
    scope = _identity_string(
        nested.get("scope"),
        path=f"{path}.identity.scope",
        validation=validation,
    )
    session_epoch = _identity_positive_int(
        nested.get("session_epoch"),
        path=f"{path}.identity.session_epoch",
        validation=validation,
    )
    generation = _identity_positive_int(
        nested.get("generation"),
        path=f"{path}.identity.generation",
        validation=validation,
    )
    stage = _identity_string(
        nested.get("stage"),
        path=f"{path}.identity.stage",
        validation=validation,
    )
    game_frame = _identity_nonnegative_int(
        nested.get("game_frame"),
        path=f"{path}.identity.game_frame",
        validation=validation,
    )
    operation_id: str | None = None
    if require_operation_id:
        operation_id = _identity_string(
            nested.get("operation_id"),
            path=f"{path}.identity.operation_id",
            validation=validation,
        )

    for field_name, identity_value in (
        ("update_id", update_id),
        ("scope", scope),
        ("session_epoch", session_epoch),
        ("generation", generation),
        ("stage", stage),
        ("game_frame", game_frame),
    ):
        direct = value.get(field_name)
        if direct is not None and direct != identity_value:
            validation.block(
                "identity_field_mismatch",
                f"{path}.{field_name}",
                "Projection identity conflicts with a duplicated payload field.",
                identity=identity_value,
                payload=direct,
            )

    if None in (
        update_id,
        scope,
        session_epoch,
        generation,
        stage,
        game_frame,
    ):
        return (None, operation_id)
    return (
        BattlefieldProjectionIdentity(
            update_id=str(update_id),
            scope=str(scope),
            session_epoch=int(session_epoch),
            generation=int(generation),
            stage=str(stage),
            game_frame=int(game_frame),
        ),
        operation_id,
    )


def _validate_monotonic_identity(
    identity: BattlefieldProjectionIdentity,
    previous: BattlefieldProjectionIdentity,
    *,
    validation: _Validation,
    path: str,
) -> None:
    if identity.scope != previous.scope:
        validation.block(
            "scope_mismatch",
            f"{path}.scope",
            "Monotonic comparison requires the same battlefield scope.",
            previous=previous.scope,
            actual=identity.scope,
        )
        return
    if identity.session_epoch < previous.session_epoch:
        validation.block(
            "stale_session_epoch",
            f"{path}.session_epoch",
            "A retired runtime or match session cannot replace the current one.",
            previous=previous.session_epoch,
            actual=identity.session_epoch,
        )
    if identity.session_epoch > previous.session_epoch:
        validation.checks["monotonic"] = True
        return
    if identity.generation < previous.generation:
        validation.block(
            "stale_generation",
            f"{path}.generation",
            "A stale generation cannot replace the current projection.",
            previous=previous.generation,
            actual=identity.generation,
        )
    if identity.game_frame < previous.game_frame:
        validation.block(
            "stale_game_frame",
            f"{path}.game_frame",
            "A stale game frame cannot replace the current projection.",
            previous=previous.game_frame,
            actual=identity.game_frame,
        )
    if (
        identity.generation == previous.generation
        and identity.game_frame == previous.game_frame
        and (
            identity.update_id != previous.update_id
            or identity.stage != previous.stage
        )
    ):
        validation.block(
            "identity_collision",
            path,
            "The same generation/frame was reused for a different identity.",
            previous=previous.to_dict(),
            actual=identity.to_dict(),
        )
    validation.checks["monotonic"] = not any(
        blocker.code
        in {
            "stale_generation",
            "stale_game_frame",
            "stale_session_epoch",
            "identity_collision",
            "scope_mismatch",
        }
        for blocker in validation.blockers
    )


def _coerce_previous_identity(
    value: BattlefieldProjectionIdentity | Mapping[str, object] | None,
    validation: _Validation,
) -> BattlefieldProjectionIdentity | None:
    if value is None:
        return None
    if isinstance(value, BattlefieldProjectionIdentity):
        return value
    if not isinstance(value, Mapping):
        validation.block(
            "invalid_previous_identity",
            "$.previous_identity",
            "Previous identity must be a projection identity or mapping.",
        )
        return None
    nested = {"identity": value}
    return _read_identity(
        nested,
        path="$.previous_identity",
        validation=validation,
        require_operation_id=False,
    )


def _identity_scope(value: object) -> str:
    if not isinstance(value, Mapping):
        return ""
    identity = value.get("identity")
    if not isinstance(identity, Mapping):
        return ""
    return str(identity.get("scope", "") or "").strip()


def _required_mapping(
    value: object,
    *,
    path: str,
    validation: _Validation,
) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        validation.block(
            "missing_safety_or_state_block",
            path,
            "The C++ projection must provide this authoritative block.",
        )
        return None
    return value


def _required_mapping_sequence(
    value: object,
    *,
    path: str,
    validation: _Validation,
) -> list[Mapping[str, object]]:
    if not _is_sequence(value):
        validation.block(
            "invalid_projection_sequence",
            path,
            "The C++ projection must provide an array of mappings.",
        )
        return []
    rows: list[Mapping[str, object]] = []
    for index, row in enumerate(value):
        if not isinstance(row, Mapping):
            validation.block(
                "invalid_projection_row",
                f"{path}[{index}]",
                "Projection array entries must be mappings.",
            )
            continue
        rows.append(row)
    return rows


def _required_sequence(
    value: object,
    *,
    path: str,
    validation: _Validation,
) -> Sequence[object] | None:
    if not _is_sequence(value):
        validation.block(
            "invalid_projection_sequence",
            path,
            "The C++ projection must provide an array.",
        )
        return None
    return value


def _required_unit_tags(
    value: object,
    *,
    path: str,
    validation: _Validation,
) -> list[int]:
    if not _is_sequence(value):
        validation.block(
            "missing_owner_tags",
            path,
            "Authoritative owner-tag evidence is required.",
        )
        return []
    tags: list[int] = []
    seen: set[int] = set()
    for index, raw_tag in enumerate(value):
        tag = _exact_int(raw_tag)
        if tag is None or tag <= 0:
            validation.block(
                "invalid_unit_tag",
                f"{path}[{index}]",
                "Unit tags must be positive integers.",
                actual=raw_tag,
            )
            continue
        if tag in seen:
            validation.block(
                "duplicate_unit_tag",
                f"{path}[{index}]",
                "An owner block cannot contain the same unit tag twice.",
                unit_tag=tag,
            )
            continue
        seen.add(tag)
        tags.append(tag)
    return tags


def _required_nonnegative_int(
    value: object,
    *,
    path: str,
    validation: _Validation,
) -> int | None:
    parsed = _exact_int(value)
    if parsed is None:
        validation.block(
            "missing_count",
            path,
            "C++ must provide an integer count; Python will not infer it.",
            actual=value,
        )
        return None
    if parsed < 0:
        validation.block(
            "invalid_count",
            path,
            "Counts cannot be negative.",
            actual=parsed,
        )
        return None
    return parsed


def _required_positive_int(
    value: object,
    *,
    path: str,
    validation: _Validation,
) -> int | None:
    parsed = _required_nonnegative_int(
        value,
        path=path,
        validation=validation,
    )
    if parsed is not None and parsed <= 0:
        validation.block(
            "invalid_count",
            path,
            "The value must be a positive integer.",
            actual=parsed,
        )
        return None
    return parsed


def _required_nonnegative_number(
    value: object,
    *,
    path: str,
    validation: _Validation,
) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        validation.block(
            "missing_safety_evidence_value",
            path,
            "C++ must provide a numeric safety evidence value.",
            actual=value,
        )
        return None
    parsed = float(value)
    if parsed < 0:
        validation.block(
            "invalid_safety_evidence_value",
            path,
            "Safety evidence values cannot be negative.",
            actual=value,
        )
        return None
    return parsed


def _required_number(
    value: object,
    *,
    path: str,
    validation: _Validation,
) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        validation.block(
            "missing_state_value",
            path,
            "C++ must provide a numeric state value.",
            actual=value,
        )
        return None
    return float(value)


def _required_bool(
    value: object,
    *,
    path: str,
    validation: _Validation,
) -> bool | None:
    if type(value) is not bool:
        validation.block(
            "missing_safety_evidence_value",
            path,
            "C++ must provide an explicit boolean; Python will not infer it.",
            actual=value,
        )
        return None
    return value


def _required_string(
    value: object,
    *,
    path: str,
    validation: _Validation,
) -> str | None:
    if not isinstance(value, str) or not value.strip():
        validation.block(
            "missing_safety_evidence_value",
            path,
            "C++ must provide a non-empty string value.",
            actual=value,
        )
        return None
    return value.strip()


def _required_string_allow_empty(
    value: object,
    *,
    path: str,
    validation: _Validation,
) -> str | None:
    if not isinstance(value, str):
        validation.block(
            "missing_safety_evidence_value",
            path,
            "C++ must explicitly provide this string value.",
            actual=value,
        )
        return None
    return value


def _identity_string(
    value: object,
    *,
    path: str,
    validation: _Validation,
) -> str | None:
    if not isinstance(value, str) or not value.strip():
        validation.block(
            "invalid_identity",
            path,
            "Identity string fields must be non-empty.",
            actual=value,
        )
        return None
    return value.strip()


def _identity_positive_int(
    value: object,
    *,
    path: str,
    validation: _Validation,
) -> int | None:
    parsed = _exact_int(value)
    if parsed is None or parsed <= 0:
        validation.block(
            "invalid_identity",
            path,
            "Identity generation must be a positive integer.",
            actual=value,
        )
        return None
    return parsed


def _identity_nonnegative_int(
    value: object,
    *,
    path: str,
    validation: _Validation,
) -> int | None:
    parsed = _exact_int(value)
    if parsed is None or parsed < 0:
        validation.block(
            "invalid_identity",
            path,
            "Identity game_frame must be a non-negative integer.",
            actual=value,
        )
        return None
    return parsed


def _exact_int(value: object) -> int | None:
    if type(value) is not int:
        return None
    return value


def _is_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    )


def _as_rejection(
    result: BattlefieldProjectionResult,
) -> BattlefieldProjectionRejection:
    return BattlefieldProjectionRejection(
        source=result.source,
        source_index=result.source_index,
        identity=result.identity,
        blockers=result.blockers,
    )


def _with_rejections(
    result: BattlefieldProjectionResult,
    rejected: Sequence[BattlefieldProjectionRejection],
) -> BattlefieldProjectionResult:
    integrity = {
        **dict(result.integrity),
        "rejected_candidate_count": len(rejected),
    }
    if result.ok and rejected:
        integrity["status"] = "valid_with_rejections"
    return BattlefieldProjectionResult(
        battlefield_overview=result.battlefield_overview,
        identity=result.identity,
        blockers=result.blockers,
        integrity=integrity,
        source=result.source,
        source_index=result.source_index,
        rejected_candidates=tuple(rejected),
    )
