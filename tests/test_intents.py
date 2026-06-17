import json
import unittest
from enum import auto

from toycraft_commander.compat import StrEnum
from toycraft_commander.intents import (
    CANONICAL_INTENT_ENUM_VALUES,
    CANONICAL_INTENT_NAMES,
    CANONICAL_INTENTS,
    COMMON_INTENT_FIELD_NAMES,
    COMMON_INTENT_FIELDS,
    ECONOMY_PRODUCTION_PAYLOAD_TYPES,
    ENTITY_OWNERS,
    ENTITY_REFERENCE_KINDS,
    FEASIBILITY_ERROR_REASONS,
    INTENT_COMMAND_RESULT_STATUSES,
    INTENT_DSL_FIELD_ORDER_BY_INTENT,
    INTENT_DSL_FORMAT_VERSION,
    INTENT_DSL_PAYLOAD_KEY,
    INTENT_FIELD_TYPES,
    INTENT_PAYLOAD_TYPES,
    INTENT_SCHEMA_REGISTRY,
    INTENT_SCHEMAS,
    PRIORITY_LEVELS,
    UNIT_CONTROL_COMBAT_PAYLOAD_TYPES,
    UTTERANCE_COVERAGE_CANONICAL_INTENT_NAMES,
    VALIDATION_STATUSES,
    ALLOWED_STRUCTURE_NAMES,
    TRAINABLE_ARMY_UNIT_NAMES,
    BuildStructureIntent,
    CanonicalIntentName,
    DefendIntent,
    EntityOwner,
    EntityReference,
    EntityReferenceKind,
    ExpandIntent,
    FeasibilityErrorReason,
    FeasibilityIssue,
    GatherResourceIntent,
    HarassIntent,
    IntentCommandPayload,
    IntentCommandResult,
    IntentCommandResultStatus,
    IntentFieldType,
    MoveCameraIntent,
    PriorityLevel,
    RepairIntent,
    ScoutIntent,
    SummarizeStateIntent,
    ValidationStatus,
    BUILDING_PAYLOAD_TYPES,
    SCOUTING_PAYLOAD_TYPES,
    TECH_PROGRESSION_PAYLOAD_TYPES,
    TrainArmyIntent,
    TrainWorkerIntent,
    get_building_payload_type,
    get_economy_production_payload_type,
    get_intent_dsl_field_order,
    get_intent_payload_type,
    get_intent_schema,
    get_scouting_payload_type,
    get_tech_progression_payload_type,
    get_unit_control_combat_payload_type,
    render_intent_command,
    render_intent_payload,
    serialize_intent_command,
    serialize_intent_payload,
    validate_intent_payload,
)
import toycraft_commander as package_exports


VALID_RAW_INTENT_PAYLOADS = {
    "GATHER_RESOURCE": {
        "intent": "GATHER_RESOURCE",
        "priority": "normal",
        "constraints": [],
        "resource": "minerals",
        "worker_count": 3,
        "base": "main",
    },
    "BUILD_STRUCTURE": {
        "intent": "BUILD_STRUCTURE",
        "priority": "normal",
        "constraints": [],
        "structure": "Supply Depot",
        "location": "main ramp",
    },
    "TRAIN_WORKER": {
        "intent": "TRAIN_WORKER",
        "priority": "normal",
        "constraints": [],
        "count": 1,
    },
    "TRAIN_ARMY": {
        "intent": "TRAIN_ARMY",
        "priority": "normal",
        "constraints": [],
        "unit_type": "Marine",
        "count": 2,
    },
    "SCOUT": {
        "intent": "SCOUT",
        "priority": "normal",
        "constraints": [],
        "target": "enemy natural",
        "unit_group": "1 SCV",
    },
    "SUMMARIZE_STATE": {
        "intent": "SUMMARIZE_STATE",
        "priority": "normal",
        "constraints": [],
    },
    "DEFEND": {
        "intent": "DEFEND",
        "priority": "urgent",
        "constraints": ["hold ramp"],
        "location": "main ramp",
        "unit_group": "all Marines",
    },
    "REPAIR": {
        "intent": "REPAIR",
        "priority": "high",
        "constraints": [],
        "target": "front bunker",
        "worker_count": 2,
    },
    "EXPAND": {
        "intent": "EXPAND",
        "priority": "high",
        "constraints": [],
        "location": "natural expansion",
    },
    "HARASS": {
        "intent": "HARASS",
        "priority": "normal",
        "constraints": ["retreat below half health"],
        "target": "enemy mineral line",
        "unit_group": "2 Marines",
    },
    "MOVE_CAMERA": {
        "intent": "MOVE_CAMERA",
        "priority": "normal",
        "constraints": [],
        "target": "main base",
    },
}

WRONG_RAW_FIELD_TYPE_CASES = {
    "GATHER_RESOURCE": (
        "worker_count",
        "three",
        "worker_count must be a positive integer",
    ),
    "BUILD_STRUCTURE": ("location", 7, "location must be a non-empty string"),
    "TRAIN_WORKER": ("count", "one", "count must be a positive integer"),
    "TRAIN_ARMY": ("count", [], "count must be a positive integer"),
    "SCOUT": ("target", 1, "target must be a non-empty string"),
    "SUMMARIZE_STATE": ("constraints", "safe", "constraints must be a list of strings"),
    "DEFEND": ("location", None, "location must be a non-empty string"),
    "REPAIR": ("worker_count", True, "worker_count must be a positive integer"),
    "EXPAND": ("location", 2, "location must be a non-empty string"),
    "HARASS": ("unit_group", False, "unit_group must be a non-empty string"),
    "MOVE_CAMERA": ("target", 1, "target must be a non-empty string"),
}

INVALID_INTENT_SPECIFIC_RAW_PAYLOAD_CASES = {
    "GATHER_RESOURCE": (
        {"resource": "wood"},
        "resource must be one of",
    ),
    "BUILD_STRUCTURE": (
        {"structure": "Starport"},
        "structure must be one of",
    ),
    "TRAIN_WORKER": (
        {"count": 0},
        "count must be a positive integer",
    ),
    "TRAIN_ARMY": (
        {"unit_type": "Vulture", "count": 0},
        "unit_type must be one of",
    ),
    "SCOUT": (
        {"target": " "},
        "target must be a non-empty string",
    ),
    "SUMMARIZE_STATE": (
        {"constraints": ("safe", 1)},
        "constraints must be a list of strings",
    ),
    "DEFEND": (
        {"location": "\t"},
        "location must be a non-empty string",
    ),
    "REPAIR": (
        {"target": "", "worker_count": -1},
        "target must be a non-empty string",
    ),
    "EXPAND": (
        {"location": ""},
        "location must be a non-empty string",
    ),
    "HARASS": (
        {"unit_group": " "},
        "unit_group must be a non-empty string",
    ),
    "MOVE_CAMERA": (
        {"target": ""},
        "target must be a non-empty string",
    ),
}


def validator_rejected_payloads_by_intent():
    """Return one or more rejected raw validator cases for each DSL variant."""

    rejected_payloads = {intent_name: [] for intent_name in INTENT_PAYLOAD_TYPES}

    for intent_name, (field_name, malformed_value, _reason) in (
        WRONG_RAW_FIELD_TYPE_CASES.items()
    ):
        payload = dict(VALID_RAW_INTENT_PAYLOADS[intent_name])
        payload[field_name] = malformed_value
        rejected_payloads.setdefault(intent_name, []).append(payload)

    for intent_name, (mutations, _reason) in (
        INVALID_INTENT_SPECIFIC_RAW_PAYLOAD_CASES.items()
    ):
        payload = dict(VALID_RAW_INTENT_PAYLOADS[intent_name])
        payload.update(mutations)
        rejected_payloads.setdefault(intent_name, []).append(payload)

    return rejected_payloads


class CompatStrEnumTest(unittest.TestCase):
    """Guard the Python 3.10 StrEnum fallback against stdlib 3.11+ divergence."""

    INTENT_STR_ENUM_TYPES = (
        CanonicalIntentName,
        PriorityLevel,
        IntentFieldType,
        ValidationStatus,
        EntityReferenceKind,
        EntityOwner,
        IntentCommandResultStatus,
        FeasibilityErrorReason,
    )

    def test_str_enum_members_stringify_to_their_values(self) -> None:
        for enum_type in self.INTENT_STR_ENUM_TYPES:
            for member in enum_type:
                with self.subTest(enum=enum_type.__name__, member=member.name):
                    self.assertEqual(member.value, str(member))
                    self.assertEqual(member.value, f"{member}")
                    self.assertEqual(member.value, "{}".format(member))
                    self.assertEqual(member.value, format(member))

    def test_str_enum_members_are_plain_string_equal_and_json_ready(self) -> None:
        self.assertEqual("SCOUT", CanonicalIntentName.SCOUT)
        self.assertEqual("urgent", PriorityLevel.URGENT)
        self.assertEqual(
            '"executable"',
            json.dumps(str(ValidationStatus.EXECUTABLE)),
        )

    def test_str_enum_auto_values_lowercase_member_names(self) -> None:
        class SampleStrEnum(StrEnum):
            FIRST = auto()
            SECOND_VALUE = auto()

        self.assertEqual("first", SampleStrEnum.FIRST.value)
        self.assertEqual("second_value", SampleStrEnum.SECOND_VALUE.value)
        self.assertEqual("first", str(SampleStrEnum.FIRST))
        self.assertEqual("second_value", f"{SampleStrEnum.SECOND_VALUE}")


class CanonicalIntentInventoryTest(unittest.TestCase):
    def test_inventory_has_exactly_eleven_unique_intents(self) -> None:
        self.assertEqual(11, len(CANONICAL_INTENTS))
        self.assertEqual(11, len(set(CANONICAL_INTENT_NAMES)))

    def test_utterance_coverage_guard_uses_canonical_inventory(self) -> None:
        expected_intents = (
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
        )

        self.assertEqual(expected_intents, UTTERANCE_COVERAGE_CANONICAL_INTENT_NAMES)
        self.assertEqual(
            CANONICAL_INTENT_NAMES,
            UTTERANCE_COVERAGE_CANONICAL_INTENT_NAMES,
        )
        self.assertEqual(11, len(UTTERANCE_COVERAGE_CANONICAL_INTENT_NAMES))
        self.assertEqual(
            11,
            len(set(UTTERANCE_COVERAGE_CANONICAL_INTENT_NAMES)),
        )

    def test_each_intent_has_brief_semantics(self) -> None:
        for intent in CANONICAL_INTENTS:
            with self.subTest(intent=intent.name):
                self.assertTrue(intent.name)
                self.assertTrue(intent.semantics.strip())

    def test_each_canonical_intent_has_a_schema(self) -> None:
        self.assertEqual(set(CANONICAL_INTENT_NAMES), set(INTENT_SCHEMAS))
        self.assertIs(INTENT_SCHEMAS, INTENT_SCHEMA_REGISTRY)

    def test_package_exports_canonical_schema_and_payload_registries(self) -> None:
        self.assertEqual(CANONICAL_INTENT_NAMES, package_exports.CANONICAL_INTENT_NAMES)
        self.assertIs(INTENT_SCHEMAS, package_exports.INTENT_SCHEMAS)
        self.assertIs(INTENT_SCHEMA_REGISTRY, package_exports.INTENT_SCHEMA_REGISTRY)
        self.assertIs(INTENT_PAYLOAD_TYPES, package_exports.INTENT_PAYLOAD_TYPES)
        self.assertIs(
            INTENT_DSL_FIELD_ORDER_BY_INTENT,
            package_exports.INTENT_DSL_FIELD_ORDER_BY_INTENT,
        )
        self.assertEqual(
            INTENT_DSL_FORMAT_VERSION,
            package_exports.INTENT_DSL_FORMAT_VERSION,
        )
        self.assertEqual(INTENT_DSL_PAYLOAD_KEY, package_exports.INTENT_DSL_PAYLOAD_KEY)
        self.assertEqual(
            UTTERANCE_COVERAGE_CANONICAL_INTENT_NAMES,
            package_exports.UTTERANCE_COVERAGE_CANONICAL_INTENT_NAMES,
        )
        self.assertIs(get_intent_schema, package_exports.get_intent_schema)
        self.assertIs(get_intent_payload_type, package_exports.get_intent_payload_type)
        self.assertIs(
            get_intent_dsl_field_order,
            package_exports.get_intent_dsl_field_order,
        )
        self.assertIs(serialize_intent_payload, package_exports.serialize_intent_payload)
        self.assertIs(render_intent_payload, package_exports.render_intent_payload)
        self.assertIs(serialize_intent_command, package_exports.serialize_intent_command)
        self.assertIs(render_intent_command, package_exports.render_intent_command)

    def test_runtime_enums_match_shared_dsl_primitives(self) -> None:
        self.assertEqual(CANONICAL_INTENT_NAMES, CANONICAL_INTENT_ENUM_VALUES)
        self.assertEqual(
            CANONICAL_INTENT_NAMES,
            tuple(intent.value for intent in CanonicalIntentName),
        )
        self.assertEqual(
            ("low", "normal", "high", "urgent"),
            tuple(priority.value for priority in PriorityLevel),
        )
        self.assertEqual(
            ("low", "normal", "high", "urgent"),
            PRIORITY_LEVELS,
        )
        self.assertEqual(
            (
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
            ),
            tuple(field_type.value for field_type in IntentFieldType),
        )
        self.assertEqual(
            tuple(field_type.value for field_type in IntentFieldType),
            INTENT_FIELD_TYPES,
        )
        self.assertEqual(
            ("executable", "rejected"),
            tuple(status.value for status in ValidationStatus),
        )
        self.assertEqual(
            tuple(status.value for status in ValidationStatus),
            VALIDATION_STATUSES,
        )
        self.assertEqual(
            (
                "resource",
                "base",
                "structure",
                "location",
                "unit",
                "unit_group",
                "target",
            ),
            tuple(kind.value for kind in EntityReferenceKind),
        )
        self.assertEqual(
            tuple(kind.value for kind in EntityReferenceKind),
            ENTITY_REFERENCE_KINDS,
        )
        self.assertEqual(
            ("player", "enemy", "neutral", "unknown"),
            tuple(owner.value for owner in EntityOwner),
        )
        self.assertEqual(
            tuple(owner.value for owner in EntityOwner),
            ENTITY_OWNERS,
        )
        self.assertEqual(
            ("accepted", "rejected"),
            tuple(status.value for status in IntentCommandResultStatus),
        )
        self.assertEqual(
            tuple(status.value for status in IntentCommandResultStatus),
            INTENT_COMMAND_RESULT_STATUSES,
        )

    def test_feasibility_error_reason_inventory_covers_execution_blockers(self) -> None:
        expected_reasons = (
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
        )

        self.assertEqual(
            expected_reasons,
            tuple(reason.value for reason in FeasibilityErrorReason),
        )
        self.assertEqual(expected_reasons, FEASIBILITY_ERROR_REASONS)
        self.assertEqual(
            len(expected_reasons),
            len(set(FEASIBILITY_ERROR_REASONS)),
        )

    def test_package_exports_validation_result_types(self) -> None:
        self.assertIs(ValidationStatus, package_exports.ValidationStatus)
        self.assertIs(EntityReferenceKind, package_exports.EntityReferenceKind)
        self.assertIs(EntityOwner, package_exports.EntityOwner)
        self.assertIs(
            IntentCommandResultStatus,
            package_exports.IntentCommandResultStatus,
        )
        self.assertIs(EntityReference, package_exports.EntityReference)
        self.assertIs(IntentCommandPayload, package_exports.IntentCommandPayload)
        self.assertIs(IntentCommandResult, package_exports.IntentCommandResult)
        self.assertIs(FeasibilityErrorReason, package_exports.FeasibilityErrorReason)
        self.assertIs(FeasibilityIssue, package_exports.FeasibilityIssue)
        self.assertEqual(VALIDATION_STATUSES, package_exports.VALIDATION_STATUSES)
        self.assertEqual(
            ENTITY_REFERENCE_KINDS,
            package_exports.ENTITY_REFERENCE_KINDS,
        )
        self.assertEqual(ENTITY_OWNERS, package_exports.ENTITY_OWNERS)
        self.assertEqual(
            INTENT_COMMAND_RESULT_STATUSES,
            package_exports.INTENT_COMMAND_RESULT_STATUSES,
        )
        self.assertEqual(
            FEASIBILITY_ERROR_REASONS,
            package_exports.FEASIBILITY_ERROR_REASONS,
        )

    def test_entity_references_serialize_command_mentions(self) -> None:
        references = (
            EntityReference(
                kind="unit_group",
                name="2 Marines",
                owner="player",
                quantity=2,
                role="harass force",
            ),
            EntityReference(kind="target", name="enemy mineral line", owner="enemy"),
        )

        self.assertEqual(
            {
                "kind": "unit_group",
                "name": "2 Marines",
                "owner": "player",
                "quantity": 2,
                "role": "harass force",
            },
            references[0].to_dict(),
        )
        self.assertEqual(
            {"kind": "target", "name": "enemy mineral line", "owner": "enemy"},
            references[1].to_dict(),
        )

    def test_entity_references_reject_malformed_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "kind must be one of"):
            EntityReference(kind="upgrade", name="stimpack")
        with self.assertRaisesRegex(ValueError, "owner must be one of"):
            EntityReference(kind="unit", name="Marine", owner="ally")
        with self.assertRaisesRegex(ValueError, "name must be a non-empty string"):
            EntityReference(kind="target", name=" ")
        with self.assertRaisesRegex(ValueError, "quantity must be a positive integer"):
            EntityReference(kind="unit", name="Marine", quantity=0)
        with self.assertRaisesRegex(ValueError, "role must be a string"):
            EntityReference(kind="unit", name="Marine", role=1)

    def test_intent_command_payload_wraps_command_text_payload_and_references(
        self,
    ) -> None:
        payload = HarassIntent(
            constraints=("retreat below half health",),
            target="enemy mineral line",
            unit_group="2 Marines",
        )
        command = IntentCommandPayload(
            command_text="마린 두 기로 적 미네랄 라인 견제해",
            payload=payload,
            entity_references=(
                EntityReference(
                    kind="unit_group",
                    name="2 Marines",
                    owner="player",
                    quantity=2,
                ),
                EntityReference(
                    kind="target",
                    name="enemy mineral line",
                    owner="enemy",
                ),
            ),
        )

        self.assertEqual("HARASS", command.intent)
        self.assertEqual(
            {
                "command_text": "마린 두 기로 적 미네랄 라인 견제해",
                "payload": {
                    "intent": "HARASS",
                    "priority": "normal",
                    "constraints": ["retreat below half health"],
                    "target": "enemy mineral line",
                    "unit_group": "2 Marines",
                },
                "entity_references": [
                    {
                        "kind": "unit_group",
                        "name": "2 Marines",
                        "owner": "player",
                        "quantity": 2,
                    },
                    {
                        "kind": "target",
                        "name": "enemy mineral line",
                        "owner": "enemy",
                    },
                ],
            },
            command.to_dict(),
        )

    def test_intent_dsl_v1_field_order_is_schema_defined(self) -> None:
        expected_field_order = {
            "GATHER_RESOURCE": (
                "intent",
                "priority",
                "constraints",
                "resource",
                "worker_count",
                "base",
            ),
            "BUILD_STRUCTURE": (
                "intent",
                "priority",
                "constraints",
                "structure",
                "location",
            ),
            "TRAIN_WORKER": ("intent", "priority", "constraints", "count"),
            "TRAIN_ARMY": (
                "intent",
                "priority",
                "constraints",
                "unit_type",
                "count",
            ),
            "SCOUT": ("intent", "priority", "constraints", "target", "unit_group"),
            "SUMMARIZE_STATE": ("intent", "priority", "constraints"),
            "DEFEND": ("intent", "priority", "constraints", "location", "unit_group"),
            "REPAIR": ("intent", "priority", "constraints", "target", "worker_count"),
            "EXPAND": ("intent", "priority", "constraints", "location"),
            "HARASS": ("intent", "priority", "constraints", "target", "unit_group"),
            "MOVE_CAMERA": ("intent", "priority", "constraints", "target"),
        }

        self.assertEqual("toycraft.intent_dsl.v1", INTENT_DSL_FORMAT_VERSION)
        self.assertEqual("intent_dsl", INTENT_DSL_PAYLOAD_KEY)
        self.assertEqual(expected_field_order, INTENT_DSL_FIELD_ORDER_BY_INTENT)

        for intent_name, field_order in expected_field_order.items():
            with self.subTest(intent=intent_name):
                self.assertEqual(field_order, get_intent_dsl_field_order(intent_name))
                self.assertEqual(
                    get_intent_schema(intent_name).required_field_names,
                    get_intent_dsl_field_order(intent_name),
                )

    def test_intent_payload_serialization_uses_stable_v1_order(self) -> None:
        payload = GatherResourceIntent(
            priority="high",
            constraints=("assign workers to requested resource",),
            resource="gas",
            worker_count=1,
            base="main",
        )
        expected_payload = {
            "intent": "GATHER_RESOURCE",
            "priority": "high",
            "constraints": ["assign workers to requested resource"],
            "resource": "gas",
            "worker_count": 1,
            "base": "main",
        }
        expected_json = (
            "{\n"
            '  "intent": "GATHER_RESOURCE",\n'
            '  "priority": "high",\n'
            '  "constraints": [\n'
            '    "assign workers to requested resource"\n'
            "  ],\n"
            '  "resource": "gas",\n'
            '  "worker_count": 1,\n'
            '  "base": "main"\n'
            "}"
        )

        self.assertEqual(expected_payload, serialize_intent_payload(payload))
        self.assertEqual(expected_payload, payload.to_dict())
        self.assertEqual(
            list(get_intent_dsl_field_order("GATHER_RESOURCE")),
            list(payload.to_dict()),
        )
        self.assertEqual(expected_json, render_intent_payload(payload))
        self.assertEqual(expected_payload, json.loads(render_intent_payload(payload)))

    def test_parsed_korean_command_serializes_as_stable_v1_document(self) -> None:
        command = IntentCommandPayload(
            command_text="본진 입구 수비해",
            payload=DefendIntent(
                priority="urgent",
                constraints=("hold ramp against early pressure",),
                location="main ramp",
                unit_group="available combat units",
            ),
            entity_references=(
                EntityReference(
                    kind="location",
                    name="main ramp",
                    owner="player",
                ),
                EntityReference(
                    kind="unit_group",
                    name="available combat units",
                    owner="player",
                ),
            ),
        )
        expected_document = {
            "format": "toycraft.intent_dsl.v1",
            "command_text": "본진 입구 수비해",
            "intent_dsl": {
                "intent": "DEFEND",
                "priority": "urgent",
                "constraints": ["hold ramp against early pressure"],
                "location": "main ramp",
                "unit_group": "available combat units",
            },
            "entity_references": [
                {
                    "kind": "location",
                    "name": "main ramp",
                    "owner": "player",
                },
                {
                    "kind": "unit_group",
                    "name": "available combat units",
                    "owner": "player",
                },
            ],
        }

        self.assertEqual(expected_document, serialize_intent_command(command))
        self.assertEqual(expected_document, command.to_dsl_document())
        self.assertEqual(render_intent_command(command), command.to_dsl_json())
        self.assertEqual(expected_document, json.loads(command.to_dsl_json()))
        self.assertIn('"command_text": "본진 입구 수비해"', command.to_dsl_json())
        self.assertEqual(
            list(get_intent_dsl_field_order("DEFEND")),
            list(expected_document["intent_dsl"]),
        )

    def test_intent_command_payload_rejects_invalid_envelope_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "command_text must be a non-empty string"):
            IntentCommandPayload(
                command_text="",
                payload=SummarizeStateIntent(),
            )
        with self.assertRaisesRegex(ValueError, "payload must be an Intent DSL payload"):
            IntentCommandPayload(command_text="상태 알려줘", payload=object())
        with self.assertRaisesRegex(
            ValueError,
            "entity_references must contain EntityReference",
        ):
            IntentCommandPayload(
                command_text="상태 알려줘",
                payload=SummarizeStateIntent(),
                entity_references=("main",),
            )

    def test_intent_command_result_models_accepted_and_rejected_outcomes(self) -> None:
        validation = validate_intent_payload(
            {
                "intent": "SUMMARIZE_STATE",
                "priority": "normal",
                "constraints": [],
            }
        )
        command = IntentCommandPayload(
            command_text="상태 알려줘",
            payload=validation.payload,
        )
        accepted = IntentCommandResult.accepted(
            command,
            validation=validation,
            narration="현재 상태를 요약합니다.",
        )

        self.assertEqual("accepted", accepted.status)
        self.assertIs(command, accepted.command)
        self.assertIs(validation, accepted.validation)
        self.assertIsNone(accepted.reason_code)
        self.assertEqual(
            {
                "status": "accepted",
                "command": command.to_dict(),
                "issues": [],
                "narration": "현재 상태를 요약합니다.",
            },
            accepted.to_dict(),
        )

        issue = FeasibilityIssue(
            reason=FeasibilityErrorReason.UNSUPPORTED_PHASE_ZERO_SCOPE,
            message="Phase 0 does not support cloaked unit control.",
            alternative="Use SCOUT or HARASS with visible Phase 0 units.",
            fields=("unit_type",),
        )
        rejected = IntentCommandResult.rejected(
            issue,
            narration="은폐 유닛 명령은 아직 실행하지 않습니다.",
        )

        self.assertEqual("rejected", rejected.status)
        self.assertIsNone(rejected.command)
        self.assertEqual(
            FeasibilityErrorReason.UNSUPPORTED_PHASE_ZERO_SCOPE,
            rejected.reason_code,
        )
        self.assertEqual(
            {
                "status": "rejected",
                "command": None,
                "issues": [
                    {
                        "reason": "unsupported_phase_zero_scope",
                        "message": "Phase 0 does not support cloaked unit control.",
                        "alternative": "Use SCOUT or HARASS with visible Phase 0 units.",
                        "fields": ["unit_type"],
                    }
                ],
                "narration": "은폐 유닛 명령은 아직 실행하지 않습니다.",
            },
            rejected.to_dict(),
        )

    def test_intent_command_result_rejects_invalid_state(self) -> None:
        issue = FeasibilityIssue(
            reason=FeasibilityErrorReason.MALFORMED_PAYLOAD,
            message="Intent payload must be an object.",
            alternative="Provide an Intent DSL object.",
        )

        with self.assertRaisesRegex(ValueError, "status must be one of"):
            IntentCommandResult(status="pending", issues=(issue,))
        with self.assertRaisesRegex(ValueError, "accepted command results require"):
            IntentCommandResult(status="accepted")
        with self.assertRaisesRegex(ValueError, "accepted command results cannot include"):
            IntentCommandResult(
                status="accepted",
                command=IntentCommandPayload(
                    command_text="상태 알려줘",
                    payload=SummarizeStateIntent(),
                ),
                issues=(issue,),
            )
        with self.assertRaisesRegex(ValueError, "rejected command results require"):
            IntentCommandResult(status="rejected")
        with self.assertRaisesRegex(ValueError, "issues must contain FeasibilityIssue"):
            IntentCommandResult(status="rejected", issues=("bad",))
        with self.assertRaisesRegex(ValueError, "narration must be a string"):
            IntentCommandResult(status="rejected", issues=(issue,), narration=1)

    def test_common_schema_fields_are_required_for_every_intent(self) -> None:
        self.assertEqual(
            ("intent", "priority", "constraints"),
            COMMON_INTENT_FIELD_NAMES,
        )
        self.assertEqual(
            COMMON_INTENT_FIELD_NAMES,
            tuple(field.name for field in COMMON_INTENT_FIELDS),
        )

        for intent_name in CANONICAL_INTENT_NAMES:
            with self.subTest(intent=intent_name):
                schema = get_intent_schema(intent_name)
                self.assertEqual(intent_name, schema.intent)
                self.assertEqual(COMMON_INTENT_FIELDS, schema.common_fields)
                self.assertEqual(
                    ("intent", "priority", "constraints"),
                    tuple(field.name for field in schema.common_fields if field.required),
                )

    def test_intent_specific_schema_fields_are_minimal_and_required(self) -> None:
        expected_fields = {
            "GATHER_RESOURCE": ("resource", "worker_count", "base"),
            "BUILD_STRUCTURE": ("structure", "location"),
            "TRAIN_WORKER": ("count",),
            "TRAIN_ARMY": ("unit_type", "count"),
            "SCOUT": ("target", "unit_group"),
            "SUMMARIZE_STATE": (),
            "DEFEND": ("location", "unit_group"),
            "REPAIR": ("target", "worker_count"),
            "EXPAND": ("location",),
            "HARASS": ("target", "unit_group"),
            "MOVE_CAMERA": ("target",),
        }

        for intent_name, field_names in expected_fields.items():
            with self.subTest(intent=intent_name):
                schema = get_intent_schema(intent_name)
                self.assertEqual(
                    field_names,
                    tuple(field.name for field in schema.intent_fields),
                )
                self.assertEqual(
                    field_names,
                    tuple(field.name for field in schema.intent_fields if field.required),
                )
                self.assertEqual(
                    ("intent", "priority", "constraints", *field_names),
                    schema.required_field_names,
                )

    def test_schema_fields_have_type_metadata(self) -> None:
        for schema in INTENT_SCHEMAS.values():
            for field in (*schema.common_fields, *schema.intent_fields):
                with self.subTest(intent=schema.intent, field=field.name):
                    self.assertIn(field.type_name, INTENT_FIELD_TYPES)
                    self.assertTrue(field.description.strip())
                    if field.type_name == "integer":
                        self.assertEqual(1, field.minimum)

    def test_economy_and_production_payload_types_are_registered(self) -> None:
        self.assertEqual(
            {
                "GATHER_RESOURCE": GatherResourceIntent,
                "BUILD_STRUCTURE": BuildStructureIntent,
                "TRAIN_WORKER": TrainWorkerIntent,
                "TRAIN_ARMY": TrainArmyIntent,
            },
            ECONOMY_PRODUCTION_PAYLOAD_TYPES,
        )

        for intent_name, payload_type in ECONOMY_PRODUCTION_PAYLOAD_TYPES.items():
            with self.subTest(intent=intent_name):
                self.assertIs(
                    payload_type,
                    get_economy_production_payload_type(intent_name),
                )

    def test_unit_control_and_combat_payload_types_are_registered(self) -> None:
        self.assertEqual(
            {
                "SCOUT": ScoutIntent,
                "SUMMARIZE_STATE": SummarizeStateIntent,
                "DEFEND": DefendIntent,
                "REPAIR": RepairIntent,
                "HARASS": HarassIntent,
                "MOVE_CAMERA": MoveCameraIntent,
            },
            UNIT_CONTROL_COMBAT_PAYLOAD_TYPES,
        )

        for intent_name, payload_type in UNIT_CONTROL_COMBAT_PAYLOAD_TYPES.items():
            with self.subTest(intent=intent_name):
                self.assertIs(
                    payload_type,
                    get_unit_control_combat_payload_type(intent_name),
                )

    def test_scouting_building_and_progression_payload_types_are_registered(self) -> None:
        self.assertEqual({"SCOUT": ScoutIntent}, SCOUTING_PAYLOAD_TYPES)
        self.assertIs(ScoutIntent, get_scouting_payload_type("SCOUT"))

        self.assertEqual({"BUILD_STRUCTURE": BuildStructureIntent}, BUILDING_PAYLOAD_TYPES)
        self.assertIs(
            BuildStructureIntent,
            get_building_payload_type("BUILD_STRUCTURE"),
        )

        self.assertEqual({"EXPAND": ExpandIntent}, TECH_PROGRESSION_PAYLOAD_TYPES)
        self.assertIs(ExpandIntent, get_tech_progression_payload_type("EXPAND"))

    def test_canonical_payload_registry_covers_every_intent_schema(self) -> None:
        expected_payload_types = {
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

        self.assertEqual(expected_payload_types, INTENT_PAYLOAD_TYPES)
        self.assertEqual(set(CANONICAL_INTENT_NAMES), set(INTENT_PAYLOAD_TYPES))
        self.assertEqual(set(INTENT_SCHEMAS), set(INTENT_PAYLOAD_TYPES))

        for intent_name, payload_type in INTENT_PAYLOAD_TYPES.items():
            with self.subTest(intent=intent_name):
                self.assertIs(payload_type, get_intent_payload_type(intent_name))
                self.assertEqual(intent_name, get_intent_schema(intent_name).intent)

    def test_economy_and_production_payloads_serialize_common_fields(self) -> None:
        payloads = (
            GatherResourceIntent(
                priority="high",
                constraints=("keep one SCV scouting",),
                resource="minerals",
                worker_count=4,
                base="main",
            ),
            BuildStructureIntent(
                priority="normal",
                constraints=["wall off ramp"],
                structure="Supply Depot",
                location="main ramp",
            ),
            TrainWorkerIntent(priority="normal", constraints=(), count=2),
            TrainArmyIntent(
                priority="urgent",
                constraints=("after barracks completes",),
                unit_type="Marine",
                count=3,
            ),
        )

        expected_payloads = (
            {
                "intent": "GATHER_RESOURCE",
                "priority": "high",
                "constraints": ["keep one SCV scouting"],
                "resource": "minerals",
                "worker_count": 4,
                "base": "main",
            },
            {
                "intent": "BUILD_STRUCTURE",
                "priority": "normal",
                "constraints": ["wall off ramp"],
                "structure": "Supply Depot",
                "location": "main ramp",
            },
            {
                "intent": "TRAIN_WORKER",
                "priority": "normal",
                "constraints": [],
                "count": 2,
            },
            {
                "intent": "TRAIN_ARMY",
                "priority": "urgent",
                "constraints": ["after barracks completes"],
                "unit_type": "Marine",
                "count": 3,
            },
        )

        for payload, expected_payload in zip(payloads, expected_payloads, strict=True):
            with self.subTest(intent=payload.intent):
                self.assertEqual(expected_payload, payload.to_dict())
                self.assertEqual(tuple(expected_payload["constraints"]), payload.constraints)

    def test_unit_control_and_combat_payloads_serialize_common_fields(self) -> None:
        payloads = (
            ScoutIntent(
                priority="normal",
                constraints=("avoid enemy ramp",),
                target="enemy natural",
                unit_group="1 SCV",
            ),
            SummarizeStateIntent(
                priority="normal",
                constraints=("brief current economy and army",),
            ),
            DefendIntent(
                priority="urgent",
                constraints=("hold ramp",),
                location="main ramp",
                unit_group="all Marines",
            ),
            RepairIntent(
                priority="high",
                constraints=("keep SCVs near bunker",),
                target="front bunker",
                worker_count=2,
            ),
            HarassIntent(
                priority="normal",
                constraints=("retreat below half health",),
                target="enemy mineral line",
                unit_group="2 Marines",
            ),
            MoveCameraIntent(
                priority="normal",
                constraints=("follow command center",),
                target="main base",
            ),
        )

        expected_payloads = (
            {
                "intent": "SCOUT",
                "priority": "normal",
                "constraints": ["avoid enemy ramp"],
                "target": "enemy natural",
                "unit_group": "1 SCV",
            },
            {
                "intent": "SUMMARIZE_STATE",
                "priority": "normal",
                "constraints": ["brief current economy and army"],
            },
            {
                "intent": "DEFEND",
                "priority": "urgent",
                "constraints": ["hold ramp"],
                "location": "main ramp",
                "unit_group": "all Marines",
            },
            {
                "intent": "REPAIR",
                "priority": "high",
                "constraints": ["keep SCVs near bunker"],
                "target": "front bunker",
                "worker_count": 2,
            },
            {
                "intent": "HARASS",
                "priority": "normal",
                "constraints": ["retreat below half health"],
                "target": "enemy mineral line",
                "unit_group": "2 Marines",
            },
            {
                "intent": "MOVE_CAMERA",
                "priority": "normal",
                "constraints": ["follow command center"],
                "target": "main base",
            },
        )

        for payload, expected_payload in zip(payloads, expected_payloads, strict=True):
            with self.subTest(intent=payload.intent):
                self.assertEqual(expected_payload, payload.to_dict())
                self.assertEqual(tuple(expected_payload["constraints"]), payload.constraints)

    def test_tech_progression_payloads_serialize_common_fields(self) -> None:
        payload = ExpandIntent(
            priority="high",
            constraints=("after natural is scouted",),
            location="natural expansion",
        )

        self.assertEqual(
            {
                "intent": "EXPAND",
                "priority": "high",
                "constraints": ["after natural is scouted"],
                "location": "natural expansion",
            },
            payload.to_dict(),
        )
        self.assertEqual(("after natural is scouted",), payload.constraints)

    def test_economy_and_production_payloads_reject_malformed_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "resource must be one of"):
            GatherResourceIntent(resource="wood", worker_count=1, base="main")
        with self.assertRaisesRegex(ValueError, "worker_count must be a positive integer"):
            GatherResourceIntent(resource="gas", worker_count=0, base="main")
        with self.assertRaisesRegex(ValueError, "base must be a non-empty string"):
            GatherResourceIntent(resource="minerals", worker_count=1, base=" ")

        with self.assertRaisesRegex(ValueError, "structure must be one of"):
            BuildStructureIntent(structure="Starport", location="main")
        with self.assertRaisesRegex(ValueError, "location must be a non-empty string"):
            BuildStructureIntent(structure="Barracks", location="")
        with self.assertRaisesRegex(ValueError, "count must be a positive integer"):
            TrainWorkerIntent(count=True)
        with self.assertRaisesRegex(ValueError, "unit_type must be one of"):
            TrainArmyIntent(unit_type="Vulture", count=1)
        with self.assertRaisesRegex(ValueError, "priority must be one of"):
            TrainArmyIntent(priority="panic", unit_type="Marine", count=1)
        with self.assertRaisesRegex(ValueError, "constraints must be a list of strings"):
            TrainArmyIntent(constraints=("safe", 1), unit_type="Marine", count=1)
        with self.assertRaisesRegex(ValueError, "intent must be TRAIN_ARMY"):
            TrainArmyIntent(intent="TRAIN_WORKER", unit_type="Marine", count=1)

    def test_unit_control_and_combat_payloads_reject_malformed_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "target must be a non-empty string"):
            ScoutIntent(target="", unit_group="1 SCV")
        with self.assertRaisesRegex(ValueError, "unit_group must be a non-empty string"):
            ScoutIntent(target="enemy main", unit_group=" ")
        with self.assertRaisesRegex(ValueError, "intent must be SCOUT"):
            ScoutIntent(intent="DEFEND", target="enemy main", unit_group="1 SCV")

        with self.assertRaisesRegex(ValueError, "intent must be SUMMARIZE_STATE"):
            SummarizeStateIntent(intent="HARASS")

        with self.assertRaisesRegex(ValueError, "location must be a non-empty string"):
            DefendIntent(location=" ", unit_group="all Marines")
        with self.assertRaisesRegex(ValueError, "unit_group must be a non-empty string"):
            DefendIntent(location="main ramp", unit_group=())
        with self.assertRaisesRegex(ValueError, "intent must be DEFEND"):
            DefendIntent(intent="SCOUT", location="main ramp", unit_group="all Marines")

        with self.assertRaisesRegex(ValueError, "target must be a non-empty string"):
            RepairIntent(target=None, worker_count=2)
        with self.assertRaisesRegex(ValueError, "worker_count must be a positive integer"):
            RepairIntent(target="front bunker", worker_count=0)
        with self.assertRaisesRegex(ValueError, "intent must be REPAIR"):
            RepairIntent(intent="DEFEND", target="front bunker", worker_count=2)

        with self.assertRaisesRegex(ValueError, "target must be a non-empty string"):
            HarassIntent(target=" ", unit_group="2 Marines")
        with self.assertRaisesRegex(ValueError, "unit_group must be a non-empty string"):
            HarassIntent(target="enemy mineral line", unit_group=False)
        with self.assertRaisesRegex(ValueError, "intent must be HARASS"):
            HarassIntent(intent="SUMMARIZE_STATE", target="enemy mineral line", unit_group="2 Marines")

        with self.assertRaisesRegex(ValueError, "target must be a non-empty string"):
            MoveCameraIntent(target="")
        with self.assertRaisesRegex(ValueError, "intent must be MOVE_CAMERA"):
            MoveCameraIntent(intent="SCOUT", target="main base")

    def test_tech_progression_payloads_reject_malformed_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "location must be a non-empty string"):
            ExpandIntent(location="")
        with self.assertRaisesRegex(ValueError, "intent must be EXPAND"):
            ExpandIntent(intent="BUILD_STRUCTURE", location="natural expansion")

    def test_canonical_payload_registry_validates_every_intent(self) -> None:
        valid_payload_kwargs = {
            "GATHER_RESOURCE": {
                "resource": "minerals",
                "worker_count": 3,
                "base": "main",
            },
            "BUILD_STRUCTURE": {
                "structure": "Supply Depot",
                "location": "main ramp",
            },
            "TRAIN_WORKER": {"count": 1},
            "TRAIN_ARMY": {"unit_type": "Marine", "count": 2},
            "SCOUT": {"target": "enemy natural", "unit_group": "1 SCV"},
            "SUMMARIZE_STATE": {},
            "DEFEND": {"location": "main ramp", "unit_group": "all Marines"},
            "REPAIR": {"target": "front bunker", "worker_count": 2},
            "EXPAND": {"location": "natural expansion"},
            "HARASS": {"target": "enemy mineral line", "unit_group": "2 Marines"},
            "MOVE_CAMERA": {"target": "main base"},
        }
        malformed_payload_kwargs = {
            "GATHER_RESOURCE": {
                "resource": "minerals",
                "worker_count": 0,
                "base": "main",
            },
            "BUILD_STRUCTURE": {
                "structure": "Starport",
                "location": "main ramp",
            },
            "TRAIN_WORKER": {"count": 0},
            "TRAIN_ARMY": {"unit_type": "Vulture", "count": 2},
            "SCOUT": {"target": "", "unit_group": "1 SCV"},
            "SUMMARIZE_STATE": {"constraints": ("safe", 1)},
            "DEFEND": {"location": " ", "unit_group": "all Marines"},
            "REPAIR": {"target": "front bunker", "worker_count": 0},
            "EXPAND": {"location": ""},
            "HARASS": {"target": "enemy mineral line", "unit_group": False},
            "MOVE_CAMERA": {"target": ""},
        }

        for intent_name in CANONICAL_INTENT_NAMES:
            payload_type = get_intent_payload_type(intent_name)
            with self.subTest(intent=intent_name, case="valid"):
                payload = payload_type(**valid_payload_kwargs[intent_name])
                self.assertEqual(intent_name, payload.intent)
                self.assertEqual("normal", payload.priority)
                self.assertEqual((), payload.constraints)

            with self.subTest(intent=intent_name, case="malformed"):
                with self.assertRaises(ValueError):
                    payload_type(**malformed_payload_kwargs[intent_name])

    def test_raw_payload_validation_accepts_complete_minimal_intent_payloads(self) -> None:
        valid_payloads = {
            "GATHER_RESOURCE": {
                "intent": "GATHER_RESOURCE",
                "priority": "normal",
                "constraints": [],
                "resource": "minerals",
                "worker_count": 3,
                "base": "main",
            },
            "BUILD_STRUCTURE": {
                "intent": "BUILD_STRUCTURE",
                "priority": "normal",
                "constraints": [],
                "structure": "Supply Depot",
                "location": "main ramp",
            },
            "TRAIN_WORKER": {
                "intent": "TRAIN_WORKER",
                "priority": "normal",
                "constraints": [],
                "count": 1,
            },
            "TRAIN_ARMY": {
                "intent": "TRAIN_ARMY",
                "priority": "normal",
                "constraints": [],
                "unit_type": "Marine",
                "count": 2,
            },
            "SCOUT": {
                "intent": "SCOUT",
                "priority": "normal",
                "constraints": [],
                "target": "enemy natural",
                "unit_group": "1 SCV",
            },
            "SUMMARIZE_STATE": {
                "intent": "SUMMARIZE_STATE",
                "priority": "normal",
                "constraints": [],
            },
            "DEFEND": {
                "intent": "DEFEND",
                "priority": "urgent",
                "constraints": ["hold ramp"],
                "location": "main ramp",
                "unit_group": "all Marines",
            },
            "REPAIR": {
                "intent": "REPAIR",
                "priority": "high",
                "constraints": [],
                "target": "front bunker",
                "worker_count": 2,
            },
            "EXPAND": {
                "intent": "EXPAND",
                "priority": "high",
                "constraints": [],
                "location": "natural expansion",
            },
            "HARASS": {
                "intent": "HARASS",
                "priority": "normal",
                "constraints": ["retreat below half health"],
                "target": "enemy mineral line",
                "unit_group": "2 Marines",
            },
            "MOVE_CAMERA": {
                "intent": "MOVE_CAMERA",
                "priority": "normal",
                "constraints": [],
                "target": "main base",
            },
        }
        expected_typed_payloads = {
            "GATHER_RESOURCE": GatherResourceIntent(
                priority="normal",
                constraints=(),
                resource="minerals",
                worker_count=3,
                base="main",
            ),
            "BUILD_STRUCTURE": BuildStructureIntent(
                priority="normal",
                constraints=(),
                structure="Supply Depot",
                location="main ramp",
            ),
            "TRAIN_WORKER": TrainWorkerIntent(
                priority="normal",
                constraints=(),
                count=1,
            ),
            "TRAIN_ARMY": TrainArmyIntent(
                priority="normal",
                constraints=(),
                unit_type="Marine",
                count=2,
            ),
            "SCOUT": ScoutIntent(
                priority="normal",
                constraints=(),
                target="enemy natural",
                unit_group="1 SCV",
            ),
            "SUMMARIZE_STATE": SummarizeStateIntent(
                priority="normal",
                constraints=(),
            ),
            "DEFEND": DefendIntent(
                priority="urgent",
                constraints=("hold ramp",),
                location="main ramp",
                unit_group="all Marines",
            ),
            "REPAIR": RepairIntent(
                priority="high",
                constraints=(),
                target="front bunker",
                worker_count=2,
            ),
            "EXPAND": ExpandIntent(
                priority="high",
                constraints=(),
                location="natural expansion",
            ),
            "HARASS": HarassIntent(
                priority="normal",
                constraints=("retreat below half health",),
                target="enemy mineral line",
                unit_group="2 Marines",
            ),
            "MOVE_CAMERA": MoveCameraIntent(
                priority="normal",
                constraints=(),
                target="main base",
            ),
        }

        for intent_name, payload in valid_payloads.items():
            with self.subTest(intent=intent_name):
                result = validate_intent_payload(payload)
                self.assertTrue(result.executable)
                self.assertIsInstance(result.payload, get_intent_payload_type(intent_name))
                self.assertEqual(expected_typed_payloads[intent_name], result.payload)
                self.assertEqual(payload, result.payload.to_dict())
                self.assertEqual("", result.reason)
                self.assertEqual((), result.missing_fields)
                self.assertEqual(ValidationStatus.EXECUTABLE, result.status)
                self.assertIsNone(result.reason_code)
                self.assertEqual((), result.reason_codes)
                self.assertEqual((), result.issues)

    def test_raw_payload_validation_returns_typed_rejection_reasons(self) -> None:
        cases = (
            (
                "malformed",
                "not an object",
                FeasibilityErrorReason.MALFORMED_PAYLOAD,
                (),
            ),
            (
                "missing intent",
                {"priority": "normal", "constraints": []},
                FeasibilityErrorReason.MISSING_REQUIRED_FIELD,
                ("intent",),
            ),
            (
                "unsupported intent",
                {"intent": "NUKE", "priority": "urgent", "constraints": []},
                FeasibilityErrorReason.UNSUPPORTED_INTENT,
                (),
            ),
            (
                "invalid field",
                {
                    "intent": "TRAIN_ARMY",
                    "priority": "panic",
                    "constraints": [],
                    "unit_type": "Marine",
                    "count": 1,
                },
                FeasibilityErrorReason.INVALID_FIELD_VALUE,
                (),
            ),
        )

        for case_name, payload, expected_reason, expected_fields in cases:
            with self.subTest(case=case_name):
                result = validate_intent_payload(payload)

                self.assertFalse(result.executable)
                self.assertIsNone(result.payload)
                self.assertEqual(ValidationStatus.REJECTED, result.status)
                self.assertEqual(expected_reason, result.reason_code)
                self.assertEqual((expected_reason,), result.reason_codes)
                self.assertEqual(1, len(result.issues))
                self.assertEqual(expected_reason, result.issues[0].reason)
                self.assertEqual(result.reason, result.issues[0].message)
                self.assertEqual(result.alternative, result.issues[0].alternative)
                self.assertEqual(expected_fields, result.issues[0].fields)

    def test_raw_payload_validation_rejects_missing_required_minimal_fields(self) -> None:
        complete_payloads = {
            "GATHER_RESOURCE": {
                "intent": "GATHER_RESOURCE",
                "priority": "normal",
                "constraints": [],
                "resource": "minerals",
                "worker_count": 3,
                "base": "main",
            },
            "BUILD_STRUCTURE": {
                "intent": "BUILD_STRUCTURE",
                "priority": "normal",
                "constraints": [],
                "structure": "Supply Depot",
                "location": "main ramp",
            },
            "TRAIN_WORKER": {
                "intent": "TRAIN_WORKER",
                "priority": "normal",
                "constraints": [],
                "count": 1,
            },
            "TRAIN_ARMY": {
                "intent": "TRAIN_ARMY",
                "priority": "normal",
                "constraints": [],
                "unit_type": "Marine",
                "count": 2,
            },
            "SCOUT": {
                "intent": "SCOUT",
                "priority": "normal",
                "constraints": [],
                "target": "enemy natural",
                "unit_group": "1 SCV",
            },
            "SUMMARIZE_STATE": {
                "intent": "SUMMARIZE_STATE",
                "priority": "normal",
                "constraints": [],
            },
            "DEFEND": {
                "intent": "DEFEND",
                "priority": "urgent",
                "constraints": ["hold ramp"],
                "location": "main ramp",
                "unit_group": "all Marines",
            },
            "REPAIR": {
                "intent": "REPAIR",
                "priority": "high",
                "constraints": [],
                "target": "front bunker",
                "worker_count": 2,
            },
            "EXPAND": {
                "intent": "EXPAND",
                "priority": "high",
                "constraints": [],
                "location": "natural expansion",
            },
            "HARASS": {
                "intent": "HARASS",
                "priority": "normal",
                "constraints": ["retreat below half health"],
                "target": "enemy mineral line",
                "unit_group": "2 Marines",
            },
            "MOVE_CAMERA": {
                "intent": "MOVE_CAMERA",
                "priority": "normal",
                "constraints": [],
                "target": "main base",
            },
        }

        for intent_name, complete_payload in complete_payloads.items():
            schema = get_intent_schema(intent_name)
            for missing_field in schema.required_field_names:
                with self.subTest(intent=intent_name, missing_field=missing_field):
                    incomplete_payload = dict(complete_payload)
                    del incomplete_payload[missing_field]

                    result = validate_intent_payload(incomplete_payload)

                    self.assertFalse(result.executable)
                    self.assertIsNone(result.payload)
                    self.assertEqual((missing_field,), result.missing_fields)
                    self.assertIn("missing required field", result.reason)
                    self.assertIn(missing_field, result.reason)
                    self.assertIn("Include required fields", result.alternative)

    def test_raw_payload_validation_lists_all_missing_required_fields_for_clarification(
        self,
    ) -> None:
        payload = {
            "intent": "TRAIN_ARMY",
            "priority": "normal",
            "constraints": [],
        }

        result = validate_intent_payload(payload)

        self.assertFalse(result.executable)
        self.assertIsNone(result.payload)
        self.assertEqual(ValidationStatus.REJECTED, result.status)
        self.assertEqual(
            FeasibilityErrorReason.MISSING_REQUIRED_FIELD,
            result.reason_code,
        )
        self.assertEqual(("unit_type", "count"), result.missing_fields)
        self.assertEqual(1, len(result.issues))
        self.assertEqual(("unit_type", "count"), result.issues[0].fields)
        self.assertIn("unit_type, count", result.reason)
        self.assertIn(
            "intent, priority, constraints, unit_type, count",
            result.alternative,
        )

    def test_raw_payload_validation_rejects_wrong_field_types_for_every_intent(self) -> None:
        self.assertEqual(
            set(CANONICAL_INTENT_NAMES),
            set(WRONG_RAW_FIELD_TYPE_CASES),
        )

        for intent_name in CANONICAL_INTENT_NAMES:
            field_name, malformed_value, expected_reason = WRONG_RAW_FIELD_TYPE_CASES[
                intent_name
            ]
            with self.subTest(intent=intent_name, field=field_name):
                payload = dict(VALID_RAW_INTENT_PAYLOADS[intent_name])
                payload[field_name] = malformed_value

                result = validate_intent_payload(payload)

                self.assertFalse(result.executable)
                self.assertIsNone(result.payload)
                self.assertIn(expected_reason, result.reason)
                self.assertIn(f"Correct the {intent_name} payload", result.alternative)

    def test_raw_payload_validation_rejects_intent_specific_invalid_payloads(
        self,
    ) -> None:
        self.assertEqual(
            set(CANONICAL_INTENT_NAMES),
            set(INVALID_INTENT_SPECIFIC_RAW_PAYLOAD_CASES),
        )

        for intent_name in CANONICAL_INTENT_NAMES:
            mutations, expected_reason = INVALID_INTENT_SPECIFIC_RAW_PAYLOAD_CASES[
                intent_name
            ]
            with self.subTest(intent=intent_name, mutations=mutations):
                payload = dict(VALID_RAW_INTENT_PAYLOADS[intent_name])
                payload.update(mutations)

                result = validate_intent_payload(payload)

                self.assertFalse(result.executable)
                self.assertIsNone(result.payload)
                self.assertEqual((), result.missing_fields)
                self.assertIn(expected_reason, result.reason)
                self.assertIn(f"Correct the {intent_name} payload", result.alternative)

    def test_validator_coverage_guard_covers_every_intent_dsl_variant(self) -> None:
        """Fail when a canonical DSL variant lacks validator pass/fail coverage."""

        intent_variants = set(INTENT_PAYLOAD_TYPES)
        executable_covered_intents = {
            intent_name
            for intent_name, payload in VALID_RAW_INTENT_PAYLOADS.items()
            if validate_intent_payload(payload).executable
        }
        rejected_covered_intents = set()
        for intent_name, payloads in validator_rejected_payloads_by_intent().items():
            for payload in payloads:
                if not validate_intent_payload(payload).executable:
                    rejected_covered_intents.add(intent_name)
                    break

        self.assertEqual(set(CANONICAL_INTENT_NAMES), intent_variants)
        self.assertEqual(
            set(),
            intent_variants - executable_covered_intents,
            "Intent DSL variants without executable validator coverage",
        )
        self.assertEqual(
            set(),
            intent_variants - rejected_covered_intents,
            "Intent DSL variants without rejected validator coverage",
        )

    def test_raw_payload_validation_rejects_invalid_priority_enum_for_every_intent(
        self,
    ) -> None:
        self.assertEqual(set(CANONICAL_INTENT_NAMES), set(VALID_RAW_INTENT_PAYLOADS))

        for intent_name in CANONICAL_INTENT_NAMES:
            with self.subTest(intent=intent_name):
                payload = dict(VALID_RAW_INTENT_PAYLOADS[intent_name])
                payload["priority"] = "panic"

                result = validate_intent_payload(payload)

                self.assertFalse(result.executable)
                self.assertIsNone(result.payload)
                self.assertIn("priority must be one of", result.reason)
                self.assertIn(f"Correct the {intent_name} payload", result.alternative)

    def test_raw_payload_validation_rejects_invalid_intent_discriminator_for_every_intent(
        self,
    ) -> None:
        self.assertEqual(set(CANONICAL_INTENT_NAMES), set(VALID_RAW_INTENT_PAYLOADS))

        for intent_name in CANONICAL_INTENT_NAMES:
            with self.subTest(source_intent=intent_name):
                payload = dict(VALID_RAW_INTENT_PAYLOADS[intent_name])
                payload["intent"] = f"INVALID_{intent_name}"

                result = validate_intent_payload(payload)

                self.assertFalse(result.executable)
                self.assertIsNone(result.payload)
                self.assertEqual(ValidationStatus.REJECTED, result.status)
                self.assertEqual(
                    FeasibilityErrorReason.UNSUPPORTED_INTENT,
                    result.reason_code,
                )
                self.assertEqual(
                    (FeasibilityErrorReason.UNSUPPORTED_INTENT,),
                    result.reason_codes,
                )
                self.assertEqual(1, len(result.issues))
                self.assertEqual(
                    FeasibilityErrorReason.UNSUPPORTED_INTENT,
                    result.issues[0].reason,
                )
                self.assertEqual(result.reason, result.issues[0].message)
                self.assertEqual(result.alternative, result.issues[0].alternative)
                self.assertEqual((), result.issues[0].fields)
                self.assertIn("unsupported or missing intent", result.reason)
                self.assertIn("canonical intents", result.alternative)

    def test_raw_payload_validation_rejects_intent_specific_invalid_enum_values(
        self,
    ) -> None:
        invalid_enum_cases = {
            "GATHER_RESOURCE": ("resource", "wood", "resource must be one of"),
            "BUILD_STRUCTURE": ("structure", "Starport", "structure must be one of"),
            "TRAIN_ARMY": ("unit_type", "Vulture", "unit_type must be one of"),
        }

        for intent_name, (field_name, invalid_value, expected_reason) in (
            invalid_enum_cases.items()
        ):
            with self.subTest(intent=intent_name, field=field_name):
                payload = dict(VALID_RAW_INTENT_PAYLOADS[intent_name])
                payload[field_name] = invalid_value

                result = validate_intent_payload(payload)

                self.assertFalse(result.executable)
                self.assertIsNone(result.payload)
                self.assertIn(expected_reason, result.reason)
                self.assertIn(f"Correct the {intent_name} payload", result.alternative)

    def test_raw_payload_validation_rejects_unsupported_and_malformed_payloads(self) -> None:
        unsupported_result = validate_intent_payload(
            {"intent": "NUKE", "priority": "urgent", "constraints": []}
        )
        self.assertFalse(unsupported_result.executable)
        self.assertIsNone(unsupported_result.payload)
        self.assertIn("unsupported or missing intent", unsupported_result.reason)
        self.assertIn("canonical intents", unsupported_result.alternative)

        malformed_result = validate_intent_payload(
            {
                "intent": "TRAIN_ARMY",
                "priority": "panic",
                "constraints": [],
                "unit_type": "Marine",
                "count": 1,
            }
        )
        self.assertFalse(malformed_result.executable)
        self.assertIsNone(malformed_result.payload)
        self.assertIn("priority must be one of", malformed_result.reason)
        self.assertIn("Correct the TRAIN_ARMY payload", malformed_result.alternative)

    def test_production_payloads_are_terran_focused_for_phase_zero(self) -> None:
        self.assertEqual(
            (
                "Supply Depot",
                "Barracks",
                "Refinery",
                "Bunker",
                "Command Center",
            ),
            ALLOWED_STRUCTURE_NAMES,
        )
        self.assertEqual(("Marine",), TRAINABLE_ARMY_UNIT_NAMES)


if __name__ == "__main__":
    unittest.main()
