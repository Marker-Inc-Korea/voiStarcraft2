"""Canonical intent inventory for the Phase 0 ToyCraft Commander MVP."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Final, Literal

from toycraft_commander.compat import StrEnum
from toycraft_commander.resources import ResourceName


class CanonicalIntentName(StrEnum):
    """Runtime enum for the 10 supported Phase 0 intent names."""

    GATHER_RESOURCE = "GATHER_RESOURCE"
    BUILD_STRUCTURE = "BUILD_STRUCTURE"
    TRAIN_WORKER = "TRAIN_WORKER"
    TRAIN_ARMY = "TRAIN_ARMY"
    SCOUT = "SCOUT"
    SUMMARIZE_STATE = "SUMMARIZE_STATE"
    DEFEND = "DEFEND"
    REPAIR = "REPAIR"
    EXPAND = "EXPAND"
    HARASS = "HARASS"
    MOVE_CAMERA = "MOVE_CAMERA"


class PriorityLevel(StrEnum):
    """Shared priority enum accepted by every Intent DSL payload."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class IntentFieldType(StrEnum):
    """Shared primitive field types for the MVP Intent DSL schema."""

    INTENT = "intent"
    PRIORITY = "priority"
    CONSTRAINT_LIST = "constraint_list"
    RESOURCE = "resource"
    INTEGER = "integer"
    BASE = "base"
    STRUCTURE = "structure"
    LOCATION = "location"
    UNIT = "unit"
    UNIT_GROUP = "unit_group"
    TARGET = "target"


class ValidationStatus(StrEnum):
    """Typed execution gate status returned by the validator."""

    EXECUTABLE = "executable"
    REJECTED = "rejected"


class EntityReferenceKind(StrEnum):
    """Typed reference kinds named inside an Intent DSL command."""

    RESOURCE = "resource"
    BASE = "base"
    STRUCTURE = "structure"
    LOCATION = "location"
    UNIT = "unit"
    UNIT_GROUP = "unit_group"
    TARGET = "target"


class EntityOwner(StrEnum):
    """Known ownership side for referenced ToyCraft entities."""

    PLAYER = "player"
    ENEMY = "enemy"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


class IntentCommandResultStatus(StrEnum):
    """Typed command-level result status before narrator rendering."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"


class FeasibilityErrorReason(StrEnum):
    """Machine-readable reasons that block ToyCraft intent execution."""

    MALFORMED_PAYLOAD = "malformed_payload"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    UNSUPPORTED_INTENT = "unsupported_intent"
    INVALID_FIELD_VALUE = "invalid_field_value"
    INSUFFICIENT_MINERALS = "insufficient_minerals"
    INSUFFICIENT_GAS = "insufficient_gas"
    INSUFFICIENT_SUPPLY = "insufficient_supply"
    MISSING_PREREQUISITE = "missing_prerequisite"
    UNAVAILABLE_PRODUCER = "unavailable_producer"
    UNAVAILABLE_WORKER = "unavailable_worker"
    UNAVAILABLE_UNIT_GROUP = "unavailable_unit_group"
    INVALID_TARGET = "invalid_target"
    LOCATION_UNAVAILABLE = "location_unavailable"
    CONSTRAINT_CONFLICT = "constraint_conflict"
    UNSUPPORTED_PHASE_ZERO_SCOPE = "unsupported_phase_zero_scope"


IntentName = Literal[
    "GATHER_RESOURCE",
    "BUILD_STRUCTURE",
    "TRAIN_WORKER",
    "TRAIN_ARMY",
    "SCOUT",
    "SUMMARIZE_STATE",
    "DEFEND",
    "REPAIR",
    "EXPAND",
    "HARASS",
    "MOVE_CAMERA",
]
FieldType = Literal[
    "intent",
    "priority",
    "constraint_list",
    "resource",
    "integer",
    "base",
    "structure",
    "location",
    "unit",
    "unit_group",
    "target",
]
ValidationStatusName = Literal["executable", "rejected"]
EntityReferenceKindName = Literal[
    "resource",
    "base",
    "structure",
    "location",
    "unit",
    "unit_group",
    "target",
]
EntityOwnerName = Literal["player", "enemy", "neutral", "unknown"]
IntentCommandResultStatusName = Literal["accepted", "rejected"]
FeasibilityReasonName = Literal[
    "malformed_payload",
    "missing_required_field",
    "unsupported_intent",
    "invalid_field_value",
    "insufficient_minerals",
    "insufficient_gas",
    "insufficient_supply",
    "missing_prerequisite",
    "unavailable_producer",
    "unavailable_worker",
    "unavailable_unit_group",
    "invalid_target",
    "location_unavailable",
    "constraint_conflict",
    "unsupported_phase_zero_scope",
]
Priority = Literal["low", "normal", "high", "urgent"]
ConstraintList = tuple[str, ...]
StructureName = Literal[
    "Supply Depot",
    "Barracks",
    "Refinery",
    "Bunker",
    "Command Center",
]
TrainableArmyUnitName = Literal["Marine"]
EconomyIntentName = Literal["GATHER_RESOURCE"]
ProductionIntentName = Literal["BUILD_STRUCTURE", "TRAIN_WORKER", "TRAIN_ARMY"]
ScoutingIntentName = Literal["SCOUT"]
BuildingIntentName = Literal["BUILD_STRUCTURE"]
TechProgressionIntentName = Literal["EXPAND"]
UnitControlCombatIntentName = Literal[
    "SCOUT",
    "SUMMARIZE_STATE",
    "DEFEND",
    "REPAIR",
    "HARASS",
    "MOVE_CAMERA",
]


ALLOWED_STRUCTURE_NAMES: Final[tuple[StructureName, ...]] = (
    "Supply Depot",
    "Barracks",
    "Refinery",
    "Bunker",
    "Command Center",
)
TRAINABLE_ARMY_UNIT_NAMES: Final[tuple[TrainableArmyUnitName, ...]] = ("Marine",)


PRIORITY_LEVELS: Final[tuple[Priority, ...]] = tuple(
    priority.value for priority in PriorityLevel
)
INTENT_FIELD_TYPES: Final[tuple[FieldType, ...]] = tuple(
    field_type.value for field_type in IntentFieldType
)
VALIDATION_STATUSES: Final[tuple[ValidationStatusName, ...]] = tuple(
    status.value for status in ValidationStatus
)
ENTITY_REFERENCE_KINDS: Final[tuple[EntityReferenceKindName, ...]] = tuple(
    kind.value for kind in EntityReferenceKind
)
ENTITY_OWNERS: Final[tuple[EntityOwnerName, ...]] = tuple(
    owner.value for owner in EntityOwner
)
INTENT_COMMAND_RESULT_STATUSES: Final[
    tuple[IntentCommandResultStatusName, ...]
] = tuple(status.value for status in IntentCommandResultStatus)
FEASIBILITY_ERROR_REASONS: Final[tuple[FeasibilityReasonName, ...]] = tuple(
    reason.value for reason in FeasibilityErrorReason
)
COMMON_INTENT_FIELD_NAMES: Final[tuple[str, ...]] = ("intent", "priority", "constraints")
INTENT_DSL_FORMAT_VERSION: Final[str] = "toycraft.intent_dsl.v1"
"""Stable display and serialization format for parsed Phase 0 commands."""

INTENT_DSL_PAYLOAD_KEY: Final[str] = "intent_dsl"
"""Envelope key that carries the typed intent-specific DSL payload."""


@dataclass(frozen=True)
class IntentFieldSchema:
    """Minimal typed field contract for an Intent DSL payload."""

    name: str
    type_name: FieldType
    required: bool
    description: str
    allowed_values: tuple[str, ...] = ()
    minimum: int | None = None


@dataclass(frozen=True)
class CanonicalIntent:
    """Supported Korean command target for the typed Intent DSL."""

    name: IntentName
    semantics: str


@dataclass(frozen=True)
class IntentSchema:
    """Common and intent-specific fields required by one canonical intent."""

    intent: IntentName
    common_fields: tuple[IntentFieldSchema, ...]
    intent_fields: tuple[IntentFieldSchema, ...]

    @property
    def required_field_names(self) -> tuple[str, ...]:
        """Return the complete required payload shape in DSL field order."""

        return tuple(
            field.name
            for field in (*self.common_fields, *self.intent_fields)
            if field.required
        )


@dataclass(frozen=True, kw_only=True)
class EntityReference:
    """Typed entity mention extracted from a command payload."""

    kind: EntityReferenceKindName
    name: str
    owner: EntityOwnerName = "unknown"
    quantity: int | None = None
    role: str = ""

    def __post_init__(self) -> None:
        _validate_allowed_value("kind", self.kind, ENTITY_REFERENCE_KINDS)
        _validate_allowed_value("owner", self.owner, ENTITY_OWNERS)
        _validate_non_empty_text("name", self.name)
        if self.quantity is not None:
            _validate_positive_integer("quantity", self.quantity)
        if type(self.role) is not str:
            raise ValueError("role must be a string.")

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready entity reference."""

        payload: dict[str, object] = {
            "kind": str(self.kind),
            "name": self.name,
            "owner": str(self.owner),
        }
        if self.quantity is not None:
            payload["quantity"] = self.quantity
        if self.role:
            payload["role"] = self.role
        return payload


@dataclass(frozen=True)
class FeasibilityIssue:
    """Typed blocking issue that prevents a command from reaching execution."""

    reason: FeasibilityErrorReason
    message: str
    alternative: str
    fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.message.strip():
            raise ValueError("message must be a non-empty string.")
        if not self.alternative.strip():
            raise ValueError("alternative must be a non-empty string.")
        object.__setattr__(self, "fields", tuple(self.fields))


@dataclass(frozen=True)
class IntentValidationResult:
    """Typed validation boundary result used before ToyCraft execution."""

    executable: bool
    payload: IntentPayload | None = None
    reason: str = ""
    alternative: str = ""
    missing_fields: tuple[str, ...] = ()
    status: ValidationStatus | None = None
    issues: tuple[FeasibilityIssue, ...] = ()

    def __post_init__(self) -> None:
        status = self.status
        if status is None:
            status = (
                ValidationStatus.EXECUTABLE
                if self.executable
                else ValidationStatus.REJECTED
            )
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "missing_fields", tuple(self.missing_fields))
        object.__setattr__(self, "issues", tuple(self.issues))

        if self.executable and self.issues:
            raise ValueError("executable validation results cannot include issues.")
        if not self.executable and status != ValidationStatus.REJECTED:
            raise ValueError("non-executable validation results must be rejected.")
        if self.executable and status != ValidationStatus.EXECUTABLE:
            raise ValueError("executable validation results must be executable.")

    @property
    def reason_code(self) -> FeasibilityErrorReason | None:
        """Return the primary typed rejection reason, if validation failed."""

        return self.issues[0].reason if self.issues else None

    @property
    def reason_codes(self) -> tuple[FeasibilityErrorReason, ...]:
        """Return all typed rejection reasons in evaluation order."""

        return tuple(issue.reason for issue in self.issues)


@dataclass(frozen=True, kw_only=True)
class BaseIntentPayload:
    """Common typed Intent DSL fields shared by every executable payload."""

    intent: IntentName
    priority: Priority = "normal"
    constraints: ConstraintList = ()

    def __post_init__(self) -> None:
        _validate_allowed_value("intent", self.intent, CANONICAL_INTENT_NAMES)
        _validate_allowed_value("priority", self.priority, PRIORITY_LEVELS)
        object.__setattr__(
            self,
            "constraints",
            _normalize_constraints(self.constraints),
        )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready Intent DSL payload for validation/execution."""

        return serialize_intent_payload(self)


@dataclass(frozen=True, kw_only=True)
class GatherResourceIntent(BaseIntentPayload):
    """Typed economy intent for assigning SCVs to minerals or gas."""

    intent: EconomyIntentName = "GATHER_RESOURCE"
    resource: ResourceName
    worker_count: int
    base: str

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_exact_intent(self.intent, "GATHER_RESOURCE")
        _validate_allowed_value("resource", self.resource, ("minerals", "gas"))
        _validate_positive_integer("worker_count", self.worker_count)
        _validate_non_empty_text("base", self.base)


@dataclass(frozen=True, kw_only=True)
class BuildStructureIntent(BaseIntentPayload):
    """Typed production intent for Terran structure construction."""

    intent: Literal["BUILD_STRUCTURE"] = "BUILD_STRUCTURE"
    structure: StructureName
    location: str
    placement_policy: Mapping[str, object] | None = field(
        default=None,
        compare=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_exact_intent(self.intent, "BUILD_STRUCTURE")
        _validate_allowed_value("structure", self.structure, ALLOWED_STRUCTURE_NAMES)
        _validate_non_empty_text("location", self.location)
        if self.placement_policy is None:
            return
        if not isinstance(self.placement_policy, Mapping):
            raise ValueError("placement_policy must be a mapping when provided.")
        placement_policy = {
            str(key): value for key, value in self.placement_policy.items()
        }
        if any(not key.strip() for key in placement_policy):
            raise ValueError("placement_policy keys must be non-empty strings.")
        object.__setattr__(self, "placement_policy", placement_policy)


BuildingIntentPayload = BuildStructureIntent


@dataclass(frozen=True, kw_only=True)
class TrainWorkerIntent(BaseIntentPayload):
    """Typed production intent for SCV queueing from Command Centers."""

    intent: Literal["TRAIN_WORKER"] = "TRAIN_WORKER"
    count: int

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_exact_intent(self.intent, "TRAIN_WORKER")
        _validate_positive_integer("count", self.count)


@dataclass(frozen=True, kw_only=True)
class TrainArmyIntent(BaseIntentPayload):
    """Typed production intent for Phase 0 Terran combat-unit queueing."""

    intent: Literal["TRAIN_ARMY"] = "TRAIN_ARMY"
    unit_type: TrainableArmyUnitName
    count: int

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_exact_intent(self.intent, "TRAIN_ARMY")
        _validate_allowed_value("unit_type", self.unit_type, TRAINABLE_ARMY_UNIT_NAMES)
        _validate_positive_integer("count", self.count)


EconomyIntentPayload = GatherResourceIntent
ProductionIntentPayload = BuildStructureIntent | TrainWorkerIntent | TrainArmyIntent
EconomyProductionIntentPayload = EconomyIntentPayload | ProductionIntentPayload


@dataclass(frozen=True, kw_only=True)
class ScoutIntent(BaseIntentPayload):
    """Typed unit-control intent for revealing map or enemy information."""

    intent: Literal["SCOUT"] = "SCOUT"
    target: str
    unit_group: str

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_exact_intent(self.intent, "SCOUT")
        _validate_non_empty_text("target", self.target)
        _validate_non_empty_text("unit_group", self.unit_group)


ScoutingIntentPayload = ScoutIntent


@dataclass(frozen=True, kw_only=True)
class SummarizeStateIntent(BaseIntentPayload):
    """Typed commander intent for narrating the current ToyCraft state."""

    intent: Literal["SUMMARIZE_STATE"] = "SUMMARIZE_STATE"

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_exact_intent(self.intent, "SUMMARIZE_STATE")


@dataclass(frozen=True, kw_only=True)
class DefendIntent(BaseIntentPayload):
    """Typed combat intent for repositioning units to protect a location."""

    intent: Literal["DEFEND"] = "DEFEND"
    location: str
    unit_group: str

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_exact_intent(self.intent, "DEFEND")
        _validate_non_empty_text("location", self.location)
        _validate_non_empty_text("unit_group", self.unit_group)


@dataclass(frozen=True, kw_only=True)
class RepairIntent(BaseIntentPayload):
    """Typed unit-control intent for assigning SCVs to repair a target."""

    intent: Literal["REPAIR"] = "REPAIR"
    target: str
    worker_count: int

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_exact_intent(self.intent, "REPAIR")
        _validate_non_empty_text("target", self.target)
        _validate_positive_integer("worker_count", self.worker_count)


@dataclass(frozen=True, kw_only=True)
class ExpandIntent(BaseIntentPayload):
    """Typed tech/progression intent for preparing a new Terran base."""

    intent: Literal["EXPAND"] = "EXPAND"
    location: str

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_exact_intent(self.intent, "EXPAND")
        _validate_non_empty_text("location", self.location)


TechProgressionIntentPayload = ExpandIntent


@dataclass(frozen=True, kw_only=True)
class HarassIntent(BaseIntentPayload):
    """Typed combat intent for disrupting enemy economy without full commitment."""

    intent: Literal["HARASS"] = "HARASS"
    target: str
    unit_group: str

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_exact_intent(self.intent, "HARASS")
        _validate_non_empty_text("target", self.target)
        _validate_non_empty_text("unit_group", self.unit_group)


@dataclass(frozen=True, kw_only=True)
class MoveCameraIntent(BaseIntentPayload):
    """Typed UI intent for moving the live StarCraft camera."""

    intent: Literal["MOVE_CAMERA"] = "MOVE_CAMERA"
    target: str
    target_slot: str = field(default="", compare=False, repr=False)

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_exact_intent(self.intent, "MOVE_CAMERA")
        _validate_non_empty_text("target", self.target)
        if not isinstance(self.target_slot, str):
            raise ValueError("target_slot must be a string when provided.")
        object.__setattr__(self, "target_slot", self.target_slot.strip())


UnitControlCombatIntentPayload = (
    ScoutIntent
    | SummarizeStateIntent
    | DefendIntent
    | RepairIntent
    | HarassIntent
    | MoveCameraIntent
)
IntentPayload = (
    GatherResourceIntent
    | BuildStructureIntent
    | TrainWorkerIntent
    | TrainArmyIntent
    | ScoutIntent
    | SummarizeStateIntent
    | DefendIntent
    | RepairIntent
    | ExpandIntent
    | HarassIntent
    | MoveCameraIntent
)

INTENT_PAYLOAD_TYPES: Final[dict[IntentName, type[IntentPayload]]] = {
    "GATHER_RESOURCE": GatherResourceIntent,
    "BUILD_STRUCTURE": BuildStructureIntent,
    "TRAIN_WORKER": TrainWorkerIntent,
    "TRAIN_ARMY": TrainArmyIntent,
    "SCOUT": ScoutIntent,
    "SUMMARIZE_STATE": SummarizeStateIntent,
    "DEFEND": DefendIntent,
    "REPAIR": RepairIntent,
    "EXPAND": ExpandIntent,
    "HARASS": HarassIntent,
    "MOVE_CAMERA": MoveCameraIntent,
}


@dataclass(frozen=True, kw_only=True)
class IntentCommandPayload:
    """Full typed command envelope passed across interpreter boundaries."""

    command_text: str
    payload: IntentPayload
    entity_references: tuple[EntityReference, ...] = ()

    def __post_init__(self) -> None:
        _validate_non_empty_text("command_text", self.command_text)
        if not isinstance(self.payload, BaseIntentPayload):
            raise ValueError("payload must be an Intent DSL payload.")
        object.__setattr__(self, "entity_references", tuple(self.entity_references))
        if any(
            not isinstance(reference, EntityReference)
            for reference in self.entity_references
        ):
            raise ValueError("entity_references must contain EntityReference values.")

    @property
    def intent(self) -> IntentName:
        """Return the canonical intent name carried by this command."""

        return self.payload.intent

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready command envelope."""

        return {
            "command_text": self.command_text,
            "payload": self.payload.to_dict(),
            "entity_references": [
                reference.to_dict() for reference in self.entity_references
            ],
        }

    def to_dsl_document(self) -> dict[str, object]:
        """Return the stable v1 parsed-command display/serialization document."""

        return serialize_intent_command(self)

    def to_dsl_json(self) -> str:
        """Render the stable v1 parsed-command document as deterministic JSON."""

        return render_intent_command(self)


@dataclass(frozen=True, kw_only=True)
class IntentCommandResult:
    """Typed command-level success or rejection result for orchestrators."""

    status: IntentCommandResultStatusName
    command: IntentCommandPayload | None = None
    validation: IntentValidationResult | None = None
    issues: tuple[FeasibilityIssue, ...] = ()
    narration: str = ""

    def __post_init__(self) -> None:
        _validate_allowed_value("status", self.status, INTENT_COMMAND_RESULT_STATUSES)
        object.__setattr__(self, "issues", tuple(self.issues))
        if any(not isinstance(issue, FeasibilityIssue) for issue in self.issues):
            raise ValueError("issues must contain FeasibilityIssue values.")
        if self.status == IntentCommandResultStatus.ACCEPTED.value:
            if self.command is None:
                raise ValueError("accepted command results require a command.")
            if self.issues:
                raise ValueError("accepted command results cannot include issues.")
        if self.status == IntentCommandResultStatus.REJECTED.value and not self.issues:
            raise ValueError("rejected command results require at least one issue.")
        if type(self.narration) is not str:
            raise ValueError("narration must be a string.")

    @classmethod
    def accepted(
        cls,
        command: IntentCommandPayload,
        *,
        validation: IntentValidationResult | None = None,
        narration: str = "",
    ) -> IntentCommandResult:
        """Build an accepted command result."""

        return cls(
            status=IntentCommandResultStatus.ACCEPTED.value,
            command=command,
            validation=validation,
            narration=narration,
        )

    @classmethod
    def rejected(
        cls,
        issue: FeasibilityIssue,
        *,
        validation: IntentValidationResult | None = None,
        narration: str = "",
    ) -> IntentCommandResult:
        """Build a rejected command result with a typed blocking issue."""

        return cls(
            status=IntentCommandResultStatus.REJECTED.value,
            validation=validation,
            issues=(issue,),
            narration=narration,
        )

    @property
    def reason_code(self) -> FeasibilityErrorReason | None:
        """Return the primary typed rejection reason, if any."""

        return self.issues[0].reason if self.issues else None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready command result."""

        return {
            "status": str(self.status),
            "command": self.command.to_dict() if self.command else None,
            "issues": [
                {
                    "reason": issue.reason.value,
                    "message": issue.message,
                    "alternative": issue.alternative,
                    "fields": list(issue.fields),
                }
                for issue in self.issues
            ],
            "narration": self.narration,
        }


SCOUTING_PAYLOAD_TYPES: Final[
    dict[
        ScoutingIntentName,
        type[ScoutingIntentPayload],
    ]
] = {
    "SCOUT": ScoutIntent,
}

BUILDING_PAYLOAD_TYPES: Final[
    dict[
        BuildingIntentName,
        type[BuildingIntentPayload],
    ]
] = {
    "BUILD_STRUCTURE": BuildStructureIntent,
}

TECH_PROGRESSION_PAYLOAD_TYPES: Final[
    dict[
        TechProgressionIntentName,
        type[TechProgressionIntentPayload],
    ]
] = {
    "EXPAND": ExpandIntent,
}

UNIT_CONTROL_COMBAT_PAYLOAD_TYPES: Final[
    dict[
        UnitControlCombatIntentName,
        type[UnitControlCombatIntentPayload],
    ]
] = {
    "SCOUT": ScoutIntent,
    "SUMMARIZE_STATE": SummarizeStateIntent,
    "DEFEND": DefendIntent,
    "REPAIR": RepairIntent,
    "HARASS": HarassIntent,
    "MOVE_CAMERA": MoveCameraIntent,
}

ECONOMY_PRODUCTION_PAYLOAD_TYPES: Final[
    dict[
        EconomyIntentName | ProductionIntentName,
        type[EconomyProductionIntentPayload],
    ]
] = {
    "GATHER_RESOURCE": GatherResourceIntent,
    "BUILD_STRUCTURE": BuildStructureIntent,
    "TRAIN_WORKER": TrainWorkerIntent,
    "TRAIN_ARMY": TrainArmyIntent,
}


CANONICAL_INTENTS: Final[tuple[CanonicalIntent, ...]] = (
    CanonicalIntent(
        "GATHER_RESOURCE",
        "Assign Terran workers to collect minerals or vespene gas at a known base.",
    ),
    CanonicalIntent(
        "BUILD_STRUCTURE",
        "Order an SCV to construct a Terran structure such as a Supply Depot, Barracks, Refinery, Bunker, or Command Center.",
    ),
    CanonicalIntent(
        "TRAIN_WORKER",
        "Queue SCV production from an available Command Center when supply and minerals allow it.",
    ),
    CanonicalIntent(
        "TRAIN_ARMY",
        "Queue combat units from available production structures, starting with Marines for the Phase 0 Terran MVP.",
    ),
    CanonicalIntent(
        "SCOUT",
        "Send a selected worker or squad to reveal enemy location, expansion timing, or incoming pressure.",
    ),
    CanonicalIntent(
        "SUMMARIZE_STATE",
        "Summarize the current ToyCraft economy, supply, army, structures, and pressure state for commander awareness.",
    ),
    CanonicalIntent(
        "DEFEND",
        "Rally or reposition units to protect a base, structure, worker line, or choke point from enemy pressure.",
    ),
    CanonicalIntent(
        "REPAIR",
        "Assign SCVs to restore hit points on damaged mechanical units or Terran structures.",
    ),
    CanonicalIntent(
        "EXPAND",
        "Create or prepare a new Terran base by building a Command Center at a feasible expansion location.",
    ),
    CanonicalIntent(
        "HARASS",
        "Send a small force to disrupt enemy workers or economy while avoiding a full committed fight.",
    ),
    CanonicalIntent(
        "MOVE_CAMERA",
        "Move the live StarCraft camera to a semantic map target without issuing unit orders.",
    ),
)


CANONICAL_INTENT_NAMES: Final[tuple[IntentName, ...]] = tuple(
    intent.name for intent in CANONICAL_INTENTS
)
UTTERANCE_COVERAGE_CANONICAL_INTENT_NAMES: Final[tuple[IntentName, ...]] = (
    CANONICAL_INTENT_NAMES
)
"""Canonical intent inventory that Korean utterance coverage guards must use."""

CANONICAL_INTENT_ENUM_VALUES: Final[tuple[IntentName, ...]] = tuple(
    intent.value for intent in CanonicalIntentName
)

COMMON_INTENT_FIELDS: Final[tuple[IntentFieldSchema, ...]] = (
    IntentFieldSchema(
        name=COMMON_INTENT_FIELD_NAMES[0],
        type_name=IntentFieldType.INTENT.value,
        required=True,
        description="Canonical MVP intent name selected by the Korean command interpreter.",
        allowed_values=CANONICAL_INTENT_NAMES,
    ),
    IntentFieldSchema(
        name=COMMON_INTENT_FIELD_NAMES[1],
        type_name=IntentFieldType.PRIORITY.value,
        required=True,
        description="Commander priority used by validation and execution ordering.",
        allowed_values=PRIORITY_LEVELS,
    ),
    IntentFieldSchema(
        name=COMMON_INTENT_FIELD_NAMES[2],
        type_name=IntentFieldType.CONSTRAINT_LIST.value,
        required=True,
        description="List of natural-language or normalized conditions that must hold before execution.",
    ),
)

INTENT_SCHEMAS: Final[dict[IntentName, IntentSchema]] = {
    "GATHER_RESOURCE": IntentSchema(
        intent="GATHER_RESOURCE",
        common_fields=COMMON_INTENT_FIELDS,
        intent_fields=(
            IntentFieldSchema(
                name="resource",
                type_name="resource",
                required=True,
                description="Resource line to saturate or rebalance.",
                allowed_values=("minerals", "gas"),
            ),
            IntentFieldSchema(
                name="worker_count",
                type_name="integer",
                required=True,
                description="Number of SCVs to assign to resource gathering.",
                minimum=1,
            ),
            IntentFieldSchema(
                name="base",
                type_name="base",
                required=True,
                description="Known friendly base whose workers should be reassigned.",
            ),
        ),
    ),
    "BUILD_STRUCTURE": IntentSchema(
        intent="BUILD_STRUCTURE",
        common_fields=COMMON_INTENT_FIELDS,
        intent_fields=(
            IntentFieldSchema(
                name="structure",
                type_name="structure",
                required=True,
                description="Terran structure to construct.",
                allowed_values=(
                    "Supply Depot",
                    "Barracks",
                    "Refinery",
                    "Bunker",
                    "Command Center",
                ),
            ),
            IntentFieldSchema(
                name="location",
                type_name="location",
                required=True,
                description="Build placement such as main ramp, mineral line, or expansion.",
            ),
        ),
    ),
    "TRAIN_WORKER": IntentSchema(
        intent="TRAIN_WORKER",
        common_fields=COMMON_INTENT_FIELDS,
        intent_fields=(
            IntentFieldSchema(
                name="count",
                type_name="integer",
                required=True,
                description="Number of SCVs to queue from available Command Centers.",
                minimum=1,
            ),
        ),
    ),
    "TRAIN_ARMY": IntentSchema(
        intent="TRAIN_ARMY",
        common_fields=COMMON_INTENT_FIELDS,
        intent_fields=(
            IntentFieldSchema(
                name="unit_type",
                type_name="unit",
                required=True,
                description="Terran combat unit to train.",
                allowed_values=("Marine",),
            ),
            IntentFieldSchema(
                name="count",
                type_name="integer",
                required=True,
                description="Number of combat units to queue.",
                minimum=1,
            ),
        ),
    ),
    "SCOUT": IntentSchema(
        intent="SCOUT",
        common_fields=COMMON_INTENT_FIELDS,
        intent_fields=(
            IntentFieldSchema(
                name="target",
                type_name="target",
                required=True,
                description="Enemy base, expansion, path, or pressure vector to reveal.",
            ),
            IntentFieldSchema(
                name="unit_group",
                type_name="unit_group",
                required=True,
                description="Worker or squad assigned to the scouting route.",
            ),
        ),
    ),
    "SUMMARIZE_STATE": IntentSchema(
        intent="SUMMARIZE_STATE",
        common_fields=COMMON_INTENT_FIELDS,
        intent_fields=(),
    ),
    "DEFEND": IntentSchema(
        intent="DEFEND",
        common_fields=COMMON_INTENT_FIELDS,
        intent_fields=(
            IntentFieldSchema(
                name="location",
                type_name="location",
                required=True,
                description="Friendly area to protect from enemy pressure.",
            ),
            IntentFieldSchema(
                name="unit_group",
                type_name="unit_group",
                required=True,
                description="Units repositioned for defense.",
            ),
        ),
    ),
    "REPAIR": IntentSchema(
        intent="REPAIR",
        common_fields=COMMON_INTENT_FIELDS,
        intent_fields=(
            IntentFieldSchema(
                name="target",
                type_name="target",
                required=True,
                description="Damaged mechanical unit or Terran structure to repair.",
            ),
            IntentFieldSchema(
                name="worker_count",
                type_name="integer",
                required=True,
                description="Number of SCVs assigned to repair.",
                minimum=1,
            ),
        ),
    ),
    "EXPAND": IntentSchema(
        intent="EXPAND",
        common_fields=COMMON_INTENT_FIELDS,
        intent_fields=(
            IntentFieldSchema(
                name="location",
                type_name="location",
                required=True,
                description="Feasible expansion location for a new Command Center.",
            ),
        ),
    ),
    "HARASS": IntentSchema(
        intent="HARASS",
        common_fields=COMMON_INTENT_FIELDS,
        intent_fields=(
            IntentFieldSchema(
                name="target",
                type_name="target",
                required=True,
                description="Enemy economy or vulnerable position to disrupt.",
            ),
            IntentFieldSchema(
                name="unit_group",
                type_name="unit_group",
                required=True,
                description="Small force assigned to harassment without full commitment.",
            ),
        ),
    ),
    "MOVE_CAMERA": IntentSchema(
        intent="MOVE_CAMERA",
        common_fields=COMMON_INTENT_FIELDS,
        intent_fields=(
            IntentFieldSchema(
                name="target",
                type_name="target",
                required=True,
                description="Semantic map target to center the live camera on.",
            ),
        ),
    ),
}
INTENT_SCHEMA_REGISTRY: Final[dict[IntentName, IntentSchema]] = INTENT_SCHEMAS

INTENT_DSL_FIELD_ORDER_BY_INTENT: Final[dict[IntentName, tuple[str, ...]]] = {
    intent_name: schema.required_field_names
    for intent_name, schema in INTENT_SCHEMA_REGISTRY.items()
}
"""Canonical v1 field order for each typed Intent DSL payload."""


def get_intent_schema(name: IntentName) -> IntentSchema:
    """Return the minimal required DSL schema for a canonical intent."""

    try:
        return INTENT_SCHEMAS[name]
    except KeyError as exc:
        raise KeyError(f"Unsupported canonical intent schema: {name}") from exc


def get_intent_payload_type(name: IntentName) -> type[IntentPayload]:
    """Return the concrete typed payload class for any canonical intent."""

    try:
        return INTENT_PAYLOAD_TYPES[name]
    except KeyError as exc:
        raise KeyError(f"Unsupported canonical intent payload: {name}") from exc


def get_intent_dsl_field_order(name: IntentName) -> tuple[str, ...]:
    """Return the stable v1 display/serialization field order for an intent."""

    try:
        return INTENT_DSL_FIELD_ORDER_BY_INTENT[name]
    except KeyError as exc:
        raise KeyError(f"Unsupported canonical intent field order: {name}") from exc


def serialize_intent_payload(payload: IntentPayload) -> dict[str, object]:
    """Return a stable ordered v1 Intent DSL payload for JSON serialization."""

    if not isinstance(payload, BaseIntentPayload):
        raise ValueError("payload must be an Intent DSL payload.")

    raw_payload = asdict(payload)
    raw_payload["constraints"] = list(payload.constraints)
    ordered_payload: dict[str, object] = {}
    for field_name in get_intent_dsl_field_order(payload.intent):
        ordered_payload[field_name] = raw_payload[field_name]
    return ordered_payload


def render_intent_payload(payload: IntentPayload) -> str:
    """Render a stable v1 Intent DSL payload as deterministic display JSON."""

    return json.dumps(
        serialize_intent_payload(payload),
        ensure_ascii=False,
        indent=2,
    )


def serialize_intent_command(command: IntentCommandPayload) -> dict[str, object]:
    """Return a stable v1 parsed-command document for Korean command display."""

    if not isinstance(command, IntentCommandPayload):
        raise ValueError("command must be an IntentCommandPayload.")

    return {
        "format": INTENT_DSL_FORMAT_VERSION,
        "command_text": command.command_text,
        INTENT_DSL_PAYLOAD_KEY: serialize_intent_payload(command.payload),
        "entity_references": [
            reference.to_dict() for reference in command.entity_references
        ],
    }


def render_intent_command(command: IntentCommandPayload) -> str:
    """Render a stable v1 parsed-command document as deterministic JSON."""

    return json.dumps(
        serialize_intent_command(command),
        ensure_ascii=False,
        indent=2,
    )


def validate_intent_payload(payload: object) -> IntentValidationResult:
    """Validate a raw Intent DSL object before rule-engine execution."""

    if not isinstance(payload, Mapping):
        return _rejected_intent_payload(
            reason_code=FeasibilityErrorReason.MALFORMED_PAYLOAD,
            reason="Intent payload must be an object.",
            alternative=(
                "Provide an Intent DSL object with intent, priority, constraints, "
                "and the required intent-specific fields."
            ),
        )

    if "intent" not in payload:
        return _rejected_intent_payload(
            reason_code=FeasibilityErrorReason.MISSING_REQUIRED_FIELD,
            reason="Intent payload is missing required field(s): intent.",
            alternative=(
                "Include required fields: intent, priority, constraints, plus the "
                "intent-specific fields."
            ),
            missing_fields=("intent",),
        )

    intent = payload.get("intent")
    if intent not in CANONICAL_INTENT_NAMES:
        return _rejected_intent_payload(
            reason_code=FeasibilityErrorReason.UNSUPPORTED_INTENT,
            reason="Intent payload has an unsupported or missing intent.",
            alternative=(
                "Use one of the canonical intents: "
                f"{', '.join(CANONICAL_INTENT_NAMES)}."
            ),
        )

    schema = get_intent_schema(intent)
    missing_fields = tuple(
        field_name
        for field_name in schema.required_field_names
        if field_name not in payload
    )
    if missing_fields:
        required_fields = ", ".join(schema.required_field_names)
        missing = ", ".join(missing_fields)
        return _rejected_intent_payload(
            reason_code=FeasibilityErrorReason.MISSING_REQUIRED_FIELD,
            reason=f"{intent} is missing required field(s): {missing}.",
            alternative=f"Include required fields for {intent}: {required_fields}.",
            missing_fields=missing_fields,
        )

    payload_type = get_intent_payload_type(intent)
    try:
        typed_payload = payload_type(**dict(payload))
    except (TypeError, ValueError) as exc:
        return _rejected_intent_payload(
            reason_code=FeasibilityErrorReason.INVALID_FIELD_VALUE,
            reason=str(exc),
            alternative=(
                f"Correct the {intent} payload and include required fields: "
                f"{', '.join(schema.required_field_names)}."
            ),
        )

    return IntentValidationResult(executable=True, payload=typed_payload)


def _rejected_intent_payload(
    *,
    reason_code: FeasibilityErrorReason,
    reason: str,
    alternative: str,
    missing_fields: tuple[str, ...] = (),
) -> IntentValidationResult:
    """Build a rejected validation result with typed and narrative fields."""

    return IntentValidationResult(
        executable=False,
        reason=reason,
        alternative=alternative,
        missing_fields=missing_fields,
        issues=(
            FeasibilityIssue(
                reason=reason_code,
                message=reason,
                alternative=alternative,
                fields=missing_fields,
            ),
        ),
    )


def get_economy_production_payload_type(
    name: EconomyIntentName | ProductionIntentName,
) -> type[EconomyProductionIntentPayload]:
    """Return the concrete payload class for economy/production intents."""

    try:
        return ECONOMY_PRODUCTION_PAYLOAD_TYPES[name]
    except KeyError as exc:
        raise KeyError(f"Unsupported economy/production intent payload: {name}") from exc


def get_scouting_payload_type(
    name: ScoutingIntentName,
) -> type[ScoutingIntentPayload]:
    """Return the concrete payload class for scouting intents."""

    try:
        return SCOUTING_PAYLOAD_TYPES[name]
    except KeyError as exc:
        raise KeyError(f"Unsupported scouting intent payload: {name}") from exc


def get_building_payload_type(
    name: BuildingIntentName,
) -> type[BuildingIntentPayload]:
    """Return the concrete payload class for building intents."""

    try:
        return BUILDING_PAYLOAD_TYPES[name]
    except KeyError as exc:
        raise KeyError(f"Unsupported building intent payload: {name}") from exc


def get_tech_progression_payload_type(
    name: TechProgressionIntentName,
) -> type[TechProgressionIntentPayload]:
    """Return the concrete payload class for tech/progression intents."""

    try:
        return TECH_PROGRESSION_PAYLOAD_TYPES[name]
    except KeyError as exc:
        raise KeyError(f"Unsupported tech/progression intent payload: {name}") from exc


def get_unit_control_combat_payload_type(
    name: UnitControlCombatIntentName,
) -> type[UnitControlCombatIntentPayload]:
    """Return the concrete payload class for unit-control and combat intents."""

    try:
        return UNIT_CONTROL_COMBAT_PAYLOAD_TYPES[name]
    except KeyError as exc:
        raise KeyError(f"Unsupported unit-control/combat intent payload: {name}") from exc


def _validate_exact_intent(actual: object, expected: str) -> None:
    if actual != expected:
        raise ValueError(f"intent must be {expected}.")


def _validate_allowed_value(
    name: str,
    value: object,
    allowed_values: tuple[str, ...],
) -> None:
    if value not in allowed_values:
        allowed = ", ".join(allowed_values)
        raise ValueError(f"{name} must be one of: {allowed}.")


def _validate_positive_integer(name: str, value: object) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer.")


def _validate_non_empty_text(name: str, value: object) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")


def _normalize_constraints(value: object) -> ConstraintList:
    if not isinstance(value, (tuple, list)):
        raise ValueError("constraints must be a list of strings.")
    if any(type(constraint) is not str for constraint in value):
        raise ValueError("constraints must be a list of strings.")
    return tuple(value)
