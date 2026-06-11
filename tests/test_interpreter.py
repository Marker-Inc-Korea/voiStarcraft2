from collections import Counter
import json
import unittest

import toycraft_commander as package_exports
from toycraft_commander.interpreter import (
    ATTACK_COMMAND_PATTERNS,
    BUILD_STRUCTURE_ALIAS,
    BUILD_STRUCTURE_CONSTRAINT,
    BUILD_STRUCTURE_DEFAULT_LOCATIONS,
    BUILD_STRUCTURE_MAPPINGS,
    COMMAND_PATTERN_LEXICON_CATEGORIES,
    COMMAND_PATTERN_LEXICONS,
    DEFEND_RAMP_ALIAS,
    DEFEND_RAMP_CONSTRAINT,
    DEFEND_RAMP_LOCATION,
    DEFEND_RAMP_MAPPINGS,
    DEFEND_RAMP_UNIT_GROUP,
    EXPAND_ALIAS,
    EXPAND_CONSTRAINT,
    EXPAND_MAPPINGS,
    GATHER_RESOURCE_ALIAS,
    GATHER_RESOURCE_CONSTRAINT,
    GATHER_RESOURCE_MAPPINGS,
    HARASS_MINERAL_LINE_ALIAS,
    HARASS_MINERAL_LINE_CONSTRAINT,
    HARASS_MINERAL_LINE_MAPPINGS,
    HARASS_MINERAL_LINE_TARGET,
    HARASS_MINERAL_LINE_UNIT_GROUP,
    INTERPRETER_MAPPINGS,
    KOREAN_COMMAND_TEST_CORPUS,
    KEEP_WORKER_PRODUCTION_ALIAS,
    KEEP_WORKER_PRODUCTION_CONSTRAINT,
    KEEP_WORKER_PRODUCTION_MAPPINGS,
    MOVEMENT_COMMAND_PATTERNS,
    PREVENT_SUPPLY_BLOCK_ALIAS,
    PREVENT_SUPPLY_BLOCK_CONSTRAINT,
    PREVENT_SUPPLY_BLOCK_LOCATION,
    PREVENT_SUPPLY_BLOCK_MAPPINGS,
    PRODUCTION_COMMAND_PATTERNS,
    PRESSURE_ENEMY_EXPANSION_ALIAS,
    PRESSURE_ENEMY_EXPANSION_CONSTRAINT,
    PRESSURE_ENEMY_EXPANSION_MAPPINGS,
    PRESSURE_ENEMY_EXPANSION_TARGET,
    PRESSURE_ENEMY_EXPANSION_UNIT_GROUP,
    REPRESENTATIVE_UTTERANCE_MATRIX,
    REPRESENTATIVE_UTTERANCES_PER_CANONICAL_INTENT,
    REPAIR_ALIAS,
    REPAIR_CONSTRAINT,
    REPAIR_MAPPINGS,
    RETREAT_ARMY_ALIAS,
    RETREAT_ARMY_CONSTRAINT,
    RETREAT_ARMY_LOCATION,
    RETREAT_ARMY_MAPPINGS,
    RETREAT_ARMY_UNIT_GROUP,
    SEND_SCOUT_ALIAS,
    SEND_SCOUT_CONSTRAINT,
    SEND_SCOUT_DEFAULT_TARGET,
    SEND_SCOUT_DEFAULT_UNIT_GROUP,
    SEND_SCOUT_MAPPINGS,
    SUMMARIZE_STATE_ALIAS,
    SUMMARIZE_STATE_CONSTRAINT,
    SUMMARIZE_STATE_MAPPINGS,
    TRAIN_UNIT_ALIAS,
    TRAIN_UNIT_CONSTRAINT,
    TRAIN_UNIT_MAPPINGS,
    UNIT_SELECTION_COMMAND_PATTERNS,
    AMBIGUOUS_COMMAND_CLARIFICATION_ALTERNATIVES,
    AMBIGUOUS_COMMAND_CLARIFICATION_PROMPT,
    AMBIGUOUS_COMMAND_CLARIFICATION_REASON,
    AMBIGUOUS_COMMAND_FAILURE_CODE,
    MALFORMED_COMMAND_CLARIFICATION_PROMPT,
    MALFORMED_COMMAND_CLARIFICATION_REASON,
    MALFORMED_COMMAND_FAILURE_CODE,
    UNSUPPORTED_COMMAND_CLARIFICATION_ALTERNATIVES,
    UNSUPPORTED_COMMAND_CLARIFICATION_PROMPT,
    UNSUPPORTED_COMMAND_CLARIFICATION_REASON,
    UNSUPPORTED_COMMAND_FAILURE_CODE,
    UTTERANCE_MATRIX_CANONICAL_INTENT_NAMES,
    ClarificationCandidate,
    CommandInterpretationResult,
    CommandInterpreter,
    CommandInterpreterInterface,
    CommandPatternLexicon,
    DEFAULT_COMMAND_INTERPRETER,
    InterpreterMapping,
    interpret_command,
    interpret_command_text,
)
from toycraft_commander.failure import CommandFailureStage
from toycraft_commander.intents import (
    BuildStructureIntent,
    CANONICAL_INTENT_NAMES,
    DefendIntent,
    ExpandIntent,
    GatherResourceIntent,
    HarassIntent,
    INTENT_DSL_FORMAT_VERSION,
    INTENT_DSL_PAYLOAD_KEY,
    RepairIntent,
    ScoutIntent,
    SummarizeStateIntent,
    TrainArmyIntent,
    TrainWorkerIntent,
    UTTERANCE_COVERAGE_CANONICAL_INTENT_NAMES,
    validate_intent_payload,
)


KOREAN_UTTERANCE_TO_EXPECTED_DSL_CASES = (
    {
        "command_text": "미네랄에 일꾼 세 기 붙여",
        "expected_dsl": {
            "intent": "GATHER_RESOURCE",
            "priority": "normal",
            "constraints": [GATHER_RESOURCE_CONSTRAINT],
            "resource": "minerals",
            "worker_count": 3,
            "base": "main",
        },
    },
    {
        "command_text": "가스에 SCV 하나 붙여",
        "expected_dsl": {
            "intent": "GATHER_RESOURCE",
            "priority": "high",
            "constraints": [GATHER_RESOURCE_CONSTRAINT],
            "resource": "gas",
            "worker_count": 1,
            "base": "main",
        },
    },
    {
        "command_text": "본진 입구에 서플라이 디포 지어",
        "expected_dsl": {
            "intent": "BUILD_STRUCTURE",
            "priority": "normal",
            "constraints": [BUILD_STRUCTURE_CONSTRAINT],
            "structure": "Supply Depot",
            "location": "main ramp",
        },
    },
    {
        "command_text": "본진에 배럭 지어",
        "expected_dsl": {
            "intent": "BUILD_STRUCTURE",
            "priority": "normal",
            "constraints": [BUILD_STRUCTURE_CONSTRAINT],
            "structure": "Barracks",
            "location": "main base",
        },
    },
    {
        "command_text": "일꾼 계속 찍어",
        "expected_dsl": {
            "intent": "TRAIN_WORKER",
            "priority": "normal",
            "constraints": [KEEP_WORKER_PRODUCTION_CONSTRAINT],
            "count": 1,
        },
    },
    {
        "command_text": "SCV 계속 생산해",
        "expected_dsl": {
            "intent": "TRAIN_WORKER",
            "priority": "normal",
            "constraints": [KEEP_WORKER_PRODUCTION_CONSTRAINT],
            "count": 1,
        },
    },
    {
        "command_text": "마린 계속 뽑아",
        "expected_dsl": {
            "intent": "TRAIN_ARMY",
            "priority": "normal",
            "constraints": [TRAIN_UNIT_CONSTRAINT],
            "unit_type": "Marine",
            "count": 1,
        },
    },
    {
        "command_text": "해병 생산해",
        "expected_dsl": {
            "intent": "TRAIN_ARMY",
            "priority": "normal",
            "constraints": [TRAIN_UNIT_CONSTRAINT],
            "unit_type": "Marine",
            "count": 1,
        },
    },
    {
        "command_text": "SCV 하나로 정찰 보내",
        "expected_dsl": {
            "intent": "SCOUT",
            "priority": "normal",
            "constraints": [SEND_SCOUT_CONSTRAINT],
            "target": SEND_SCOUT_DEFAULT_TARGET,
            "unit_group": SEND_SCOUT_DEFAULT_UNIT_GROUP,
        },
    },
    {
        "command_text": "일꾼 하나 적 앞마당 확인해",
        "expected_dsl": {
            "intent": "SCOUT",
            "priority": "high",
            "constraints": [SEND_SCOUT_CONSTRAINT],
            "target": "enemy natural",
            "unit_group": SEND_SCOUT_DEFAULT_UNIT_GROUP,
        },
    },
    {
        "command_text": "상태 알려줘",
        "expected_dsl": {
            "intent": "SUMMARIZE_STATE",
            "priority": "normal",
            "constraints": [SUMMARIZE_STATE_CONSTRAINT],
        },
    },
    {
        "command_text": "현재 상황 요약해",
        "expected_dsl": {
            "intent": "SUMMARIZE_STATE",
            "priority": "normal",
            "constraints": [SUMMARIZE_STATE_CONSTRAINT],
        },
    },
    {
        "command_text": "입구 막아",
        "expected_dsl": {
            "intent": "DEFEND",
            "priority": "urgent",
            "constraints": [DEFEND_RAMP_CONSTRAINT],
            "location": DEFEND_RAMP_LOCATION,
            "unit_group": DEFEND_RAMP_UNIT_GROUP,
        },
    },
    {
        "command_text": "본진 입구 수비해",
        "expected_dsl": {
            "intent": "DEFEND",
            "priority": "urgent",
            "constraints": [DEFEND_RAMP_CONSTRAINT],
            "location": DEFEND_RAMP_LOCATION,
            "unit_group": DEFEND_RAMP_UNIT_GROUP,
        },
    },
    {
        "command_text": "벙커 수리해",
        "expected_dsl": {
            "intent": "REPAIR",
            "priority": "high",
            "constraints": [REPAIR_CONSTRAINT],
            "target": "front bunker",
            "worker_count": 1,
        },
    },
    {
        "command_text": "SCV 두 기로 앞 벙커 고쳐",
        "expected_dsl": {
            "intent": "REPAIR",
            "priority": "high",
            "constraints": [REPAIR_CONSTRAINT],
            "target": "front bunker",
            "worker_count": 2,
        },
    },
    {
        "command_text": "앞마당 가져가",
        "expected_dsl": {
            "intent": "EXPAND",
            "priority": "normal",
            "constraints": [EXPAND_CONSTRAINT],
            "location": "natural expansion",
        },
    },
    {
        "command_text": "앞마당에 커맨드센터 준비해",
        "expected_dsl": {
            "intent": "EXPAND",
            "priority": "normal",
            "constraints": [EXPAND_CONSTRAINT],
            "location": "natural expansion",
        },
    },
    {
        "command_text": "마린 두 기로 적 미네랄 라인 견제해",
        "expected_dsl": {
            "intent": "HARASS",
            "priority": "high",
            "constraints": [HARASS_MINERAL_LINE_CONSTRAINT],
            "target": HARASS_MINERAL_LINE_TARGET,
            "unit_group": HARASS_MINERAL_LINE_UNIT_GROUP,
        },
    },
    {
        "command_text": "상대 일꾼 라인 흔들어",
        "expected_dsl": {
            "intent": "HARASS",
            "priority": "high",
            "constraints": [HARASS_MINERAL_LINE_CONSTRAINT],
            "target": HARASS_MINERAL_LINE_TARGET,
            "unit_group": HARASS_MINERAL_LINE_UNIT_GROUP,
        },
    },
)


def _normalize_intent_dsl_output(payload: dict[str, object]) -> dict[str, object]:
    """Normalize JSON-like DSL values before interpreter mapping comparison."""

    normalized: dict[str, object] = {}
    for key, value in payload.items():
        if isinstance(value, tuple):
            normalized[key] = list(value)
        else:
            normalized[key] = value
    return normalized


class KoreanInterpreterMappingTest(unittest.TestCase):
    def test_default_command_interpreter_exposes_stable_interface_boundary(
        self,
    ) -> None:
        self.assertIsInstance(DEFAULT_COMMAND_INTERPRETER, CommandInterpreter)
        self.assertIsInstance(
            DEFAULT_COMMAND_INTERPRETER,
            CommandInterpreterInterface,
        )
        self.assertEqual(INTERPRETER_MAPPINGS, DEFAULT_COMMAND_INTERPRETER.mappings)
        self.assertEqual(
            COMMAND_PATTERN_LEXICONS,
            DEFAULT_COMMAND_INTERPRETER.pattern_lexicons,
        )
        self.assertEqual(
            UTTERANCE_MATRIX_CANONICAL_INTENT_NAMES,
            DEFAULT_COMMAND_INTERPRETER.canonical_intents,
        )
        self.assertTrue(hasattr(CommandInterpreterInterface, "interpret_text"))
        self.assertTrue(hasattr(CommandInterpreterInterface, "interpret"))

        payload = DEFAULT_COMMAND_INTERPRETER.interpret_text("상태 알려줘")
        result = DEFAULT_COMMAND_INTERPRETER.interpret("상태 알려줘")

        self.assertEqual(interpret_command_text("상태 알려줘"), payload)
        self.assertEqual(interpret_command("상태 알려줘"), result)
        self.assertIsInstance(payload, SummarizeStateIntent)
        self.assertIsInstance(result, CommandInterpretationResult)
        self.assertFalse(result.clarification_required)

    def test_command_interpreter_supports_injected_mapping_registry(
        self,
    ) -> None:
        custom_mapping = InterpreterMapping(
            alias=GATHER_RESOURCE_ALIAS,
            utterance="미네랄에 일꾼 다섯 붙여",
            payload=GatherResourceIntent(
                priority="normal",
                constraints=(GATHER_RESOURCE_CONSTRAINT,),
                resource="minerals",
                worker_count=5,
                base="main",
            ),
        )
        interpreter = CommandInterpreter(mappings=(custom_mapping,))

        payload = interpreter.interpret_text("미네랄에 일꾼 다섯 붙여")
        result = interpreter.interpret("미네랄에 일꾼 다섯 붙여")

        self.assertEqual(custom_mapping.payload, payload)
        self.assertEqual(custom_mapping.payload, result.payload)
        self.assertFalse(result.clarification_required)

    def test_command_interpreter_rejects_invalid_interface_registries(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "at least one mapping"):
            CommandInterpreter(mappings=())

        with self.assertRaisesRegex(ValueError, "10 MVP intents"):
            CommandInterpreter(canonical_intents=("GATHER_RESOURCE",))

    def test_command_pattern_lexicons_cover_selection_movement_production_and_attack(
        self,
    ) -> None:
        lexicons_by_category = {
            lexicon.category: lexicon for lexicon in COMMAND_PATTERN_LEXICONS
        }

        self.assertEqual(
            ("unit_selection", "movement", "production", "attack"),
            COMMAND_PATTERN_LEXICON_CATEGORIES,
        )
        self.assertEqual(
            set(COMMAND_PATTERN_LEXICON_CATEGORIES),
            set(lexicons_by_category),
        )
        self.assertEqual(
            UNIT_SELECTION_COMMAND_PATTERNS,
            lexicons_by_category["unit_selection"],
        )
        self.assertEqual(MOVEMENT_COMMAND_PATTERNS, lexicons_by_category["movement"])
        self.assertEqual(PRODUCTION_COMMAND_PATTERNS, lexicons_by_category["production"])
        self.assertEqual(ATTACK_COMMAND_PATTERNS, lexicons_by_category["attack"])

        expected_terms = {
            "unit_selection": (("마린", "SCV"), ("Marines", "worker")),
            "movement": (("정찰", "후퇴"), ("scout", "pull back")),
            "production": (("생산", "건설"), ("train", "build")),
            "attack": (("견제", "공격"), ("harass", "attack")),
        }
        for category, (korean_terms, english_terms) in expected_terms.items():
            lexicon = lexicons_by_category[category]
            with self.subTest(category=category):
                self.assertIsInstance(lexicon, CommandPatternLexicon)
                self.assertGreaterEqual(len(lexicon.korean_patterns), 2)
                self.assertGreaterEqual(len(lexicon.english_patterns), 2)
                self.assertTrue(set(korean_terms).issubset(lexicon.korean_patterns))
                self.assertTrue(set(english_terms).issubset(lexicon.english_patterns))

    def test_english_and_korean_command_patterns_map_to_nearest_supported_intents(
        self,
    ) -> None:
        cases = (
            (
                "keep training SCVs",
                TrainWorkerIntent(
                    priority="normal",
                    constraints=(KEEP_WORKER_PRODUCTION_CONSTRAINT,),
                    count=1,
                ),
            ),
            (
                "build barracks at main base",
                BuildStructureIntent(
                    priority="normal",
                    constraints=(BUILD_STRUCTURE_CONSTRAINT,),
                    structure="Barracks",
                    location="main base",
                ),
            ),
            (
                "배럭 하나 본진에 건설",
                BuildStructureIntent(
                    priority="normal",
                    constraints=(BUILD_STRUCTURE_CONSTRAINT,),
                    structure="Barracks",
                    location="main base",
                ),
            ),
            (
                "train two marines",
                TrainArmyIntent(
                    priority="normal",
                    constraints=(TRAIN_UNIT_CONSTRAINT,),
                    unit_type="Marine",
                    count=2,
                ),
            ),
            (
                "send one marine to scout enemy mineral line",
                ScoutIntent(
                    priority="normal",
                    constraints=(SEND_SCOUT_CONSTRAINT,),
                    target="enemy mineral line",
                    unit_group="1 Marine",
                ),
            ),
            (
                "rally marines to ramp and hold",
                DefendIntent(
                    priority="high",
                    constraints=(DEFEND_RAMP_CONSTRAINT,),
                    location=DEFEND_RAMP_LOCATION,
                    unit_group="Marines",
                ),
            ),
            (
                "마린 램프로 이동해서 홀드",
                DefendIntent(
                    priority="high",
                    constraints=(DEFEND_RAMP_CONSTRAINT,),
                    location=DEFEND_RAMP_LOCATION,
                    unit_group="Marines",
                ),
            ),
            (
                "pull back marines",
                DefendIntent(
                    priority="high",
                    constraints=(RETREAT_ARMY_CONSTRAINT,),
                    location=RETREAT_ARMY_LOCATION,
                    unit_group="Marines",
                ),
            ),
            (
                "attack enemy mineral line with marines",
                HarassIntent(
                    priority="high",
                    constraints=(HARASS_MINERAL_LINE_CONSTRAINT,),
                    target=HARASS_MINERAL_LINE_TARGET,
                    unit_group="Marines",
                ),
            ),
            (
                "마린으로 적 미네랄 라인 공격",
                HarassIntent(
                    priority="high",
                    constraints=(HARASS_MINERAL_LINE_CONSTRAINT,),
                    target=HARASS_MINERAL_LINE_TARGET,
                    unit_group="Marines",
                ),
            ),
        )

        for command_text, expected_payload in cases:
            with self.subTest(command_text=command_text):
                payload = interpret_command_text(command_text)

                self.assertEqual(expected_payload, payload)
                validation = validate_intent_payload(payload.to_dict())
                self.assertTrue(validation.executable)
                self.assertEqual(expected_payload, validation.payload)

    def test_package_exports_command_pattern_lexicons(self) -> None:
        self.assertIs(CommandPatternLexicon, package_exports.CommandPatternLexicon)
        self.assertEqual(
            COMMAND_PATTERN_LEXICON_CATEGORIES,
            package_exports.COMMAND_PATTERN_LEXICON_CATEGORIES,
        )
        self.assertIs(COMMAND_PATTERN_LEXICONS, package_exports.COMMAND_PATTERN_LEXICONS)
        self.assertIs(
            UNIT_SELECTION_COMMAND_PATTERNS,
            package_exports.UNIT_SELECTION_COMMAND_PATTERNS,
        )
        self.assertIs(
            MOVEMENT_COMMAND_PATTERNS,
            package_exports.MOVEMENT_COMMAND_PATTERNS,
        )
        self.assertIs(
            PRODUCTION_COMMAND_PATTERNS,
            package_exports.PRODUCTION_COMMAND_PATTERNS,
        )
        self.assertIs(
            ATTACK_COMMAND_PATTERNS,
            package_exports.ATTACK_COMMAND_PATTERNS,
        )

    def test_utterance_matrix_enumerates_canonical_intent_names(self) -> None:
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
        )

        self.assertEqual(expected_intents, UTTERANCE_MATRIX_CANONICAL_INTENT_NAMES)
        self.assertEqual(
            UTTERANCE_COVERAGE_CANONICAL_INTENT_NAMES,
            UTTERANCE_MATRIX_CANONICAL_INTENT_NAMES,
        )
        self.assertEqual(CANONICAL_INTENT_NAMES, UTTERANCE_MATRIX_CANONICAL_INTENT_NAMES)
        self.assertEqual(10, len(UTTERANCE_MATRIX_CANONICAL_INTENT_NAMES))
        self.assertEqual(10, len(set(UTTERANCE_MATRIX_CANONICAL_INTENT_NAMES)))

    def test_representative_utterance_matrix_has_two_korean_rows_per_intent(
        self,
    ) -> None:
        expected_sequence = tuple(
            intent
            for intent in CANONICAL_INTENT_NAMES
            for _ in range(REPRESENTATIVE_UTTERANCES_PER_CANONICAL_INTENT)
        )
        actual_sequence = tuple(
            mapping.payload.intent for mapping in REPRESENTATIVE_UTTERANCE_MATRIX
        )
        counts_by_intent = Counter(actual_sequence)

        self.assertEqual(2, REPRESENTATIVE_UTTERANCES_PER_CANONICAL_INTENT)
        self.assertEqual(20, len(REPRESENTATIVE_UTTERANCE_MATRIX))
        self.assertEqual(expected_sequence, actual_sequence)
        self.assertEqual(
            {
                intent: REPRESENTATIVE_UTTERANCES_PER_CANONICAL_INTENT
                for intent in CANONICAL_INTENT_NAMES
            },
            dict(counts_by_intent),
        )
        self.assertEqual(
            20,
            len({mapping.utterance for mapping in REPRESENTATIVE_UTTERANCE_MATRIX}),
        )

        for mapping in REPRESENTATIVE_UTTERANCE_MATRIX:
            with self.subTest(utterance=mapping.utterance):
                self.assertTrue(
                    any("\uac00" <= character <= "\ud7a3" for character in mapping.utterance)
                )

    def test_each_canonical_intent_has_exactly_two_korean_representative_utterances(
        self,
    ) -> None:
        korean_utterances_by_intent = {
            intent: [] for intent in CANONICAL_INTENT_NAMES
        }
        unexpected_intents = []
        non_korean_rows = []

        for mapping in REPRESENTATIVE_UTTERANCE_MATRIX:
            if mapping.payload.intent not in korean_utterances_by_intent:
                unexpected_intents.append(mapping.payload.intent)
                continue

            has_hangul = any(
                "\uac00" <= character <= "\ud7a3"
                for character in mapping.utterance
            )
            if has_hangul:
                korean_utterances_by_intent[mapping.payload.intent].append(
                    mapping.utterance
                )
            else:
                non_korean_rows.append(mapping.utterance)

        self.assertEqual([], unexpected_intents)
        self.assertEqual([], non_korean_rows)
        self.assertEqual(
            {
                intent: REPRESENTATIVE_UTTERANCES_PER_CANONICAL_INTENT
                for intent in CANONICAL_INTENT_NAMES
            },
            {
                intent: len(utterances)
                for intent, utterances in korean_utterances_by_intent.items()
            },
        )

    def test_representative_utterance_matrix_round_trips_to_valid_dsl(
        self,
    ) -> None:
        for mapping in REPRESENTATIVE_UTTERANCE_MATRIX:
            with self.subTest(utterance=mapping.utterance):
                payload = interpret_command_text(mapping.utterance)

                self.assertEqual(mapping.payload, payload)
                validation = validate_intent_payload(mapping.payload.to_dict())
                self.assertTrue(validation.executable)
                self.assertEqual(mapping.payload, validation.payload)

    def test_korean_command_test_corpus_defines_expected_typed_dsl_outputs(
        self,
    ) -> None:
        self.assertEqual(20, len(KOREAN_COMMAND_TEST_CORPUS))
        self.assertEqual(
            KOREAN_UTTERANCE_TO_EXPECTED_DSL_CASES,
            KOREAN_COMMAND_TEST_CORPUS,
        )
        self.assertEqual(
            len(REPRESENTATIVE_UTTERANCE_MATRIX),
            len(KOREAN_COMMAND_TEST_CORPUS),
        )

        for corpus_row, mapping in zip(
            KOREAN_COMMAND_TEST_CORPUS,
            REPRESENTATIVE_UTTERANCE_MATRIX,
            strict=True,
        ):
            with self.subTest(utterance=mapping.utterance):
                self.assertEqual(
                    {"command_text", "expected_dsl"},
                    set(corpus_row),
                )
                self.assertEqual(mapping.utterance, corpus_row["command_text"])
                self.assertEqual(mapping.payload.to_dict(), corpus_row["expected_dsl"])

                expected_dsl = corpus_row["expected_dsl"]
                self.assertIsInstance(expected_dsl, dict)
                self.assertEqual(mapping.payload.intent, expected_dsl["intent"])
                self.assertIn(expected_dsl["intent"], CANONICAL_INTENT_NAMES)
                self.assertIn("priority", expected_dsl)
                self.assertIn("constraints", expected_dsl)

    def test_korean_command_test_corpus_maps_through_interpreter_to_normalized_dsl(
        self,
    ) -> None:
        minimum_expected_matches = 18
        matched_count = 0
        mismatches = []

        self.assertEqual(20, len(KOREAN_COMMAND_TEST_CORPUS))

        for corpus_row in KOREAN_COMMAND_TEST_CORPUS:
            command_text = corpus_row["command_text"]
            expected_dsl = _normalize_intent_dsl_output(corpus_row["expected_dsl"])
            with self.subTest(command_text=command_text):
                result = interpret_command(command_text)
                actual_dsl = (
                    _normalize_intent_dsl_output(result.payload.to_dict())
                    if result.payload is not None
                    else None
                )

                if (
                    not result.clarification_required
                    and actual_dsl == expected_dsl
                ):
                    matched_count += 1
                else:
                    mismatches.append(
                        {
                            "command_text": command_text,
                            "expected_dsl": expected_dsl,
                            "actual_dsl": actual_dsl,
                            "reason": result.reason,
                        }
                    )

        self.assertGreaterEqual(
            matched_count,
            minimum_expected_matches,
            f"{matched_count}/20 corpus commands matched; mismatches={mismatches}",
        )

    def test_defined_korean_utterance_to_expected_dsl_cases_cover_variations(
        self,
    ) -> None:
        intent_counts = Counter(
            case["expected_dsl"]["intent"]
            for case in KOREAN_UTTERANCE_TO_EXPECTED_DSL_CASES
        )

        self.assertEqual(20, len(KOREAN_UTTERANCE_TO_EXPECTED_DSL_CASES))
        self.assertEqual(
            20,
            len(
                {
                    case["command_text"]
                    for case in KOREAN_UTTERANCE_TO_EXPECTED_DSL_CASES
                }
            ),
        )
        self.assertEqual(
            {
                intent: REPRESENTATIVE_UTTERANCES_PER_CANONICAL_INTENT
                for intent in CANONICAL_INTENT_NAMES
            },
            dict(intent_counts),
        )

        for case in KOREAN_UTTERANCE_TO_EXPECTED_DSL_CASES:
            command_text = case["command_text"]
            expected_dsl = case["expected_dsl"]
            with self.subTest(command_text=command_text):
                self.assertTrue(
                    any("\uac00" <= character <= "\ud7a3" for character in command_text)
                )
                self.assertEqual(
                    {"intent", "priority", "constraints"},
                    set(expected_dsl).intersection(
                        {"intent", "priority", "constraints"}
                    ),
                )

                payload = interpret_command_text(command_text)
                self.assertEqual(expected_dsl, payload.to_dict())

                validation = validate_intent_payload(expected_dsl)
                self.assertTrue(validation.executable)
                self.assertEqual(payload, validation.payload)

    def test_each_representative_korean_utterance_maps_to_expected_typed_dsl(
        self,
    ) -> None:
        expected_utterance_suite_count = 20
        expected_cases = (
            (
                "미네랄에 일꾼 세 기 붙여",
                GatherResourceIntent(
                    priority="normal",
                    constraints=(GATHER_RESOURCE_CONSTRAINT,),
                    resource="minerals",
                    worker_count=3,
                    base="main",
                ),
            ),
            (
                "가스에 SCV 하나 붙여",
                GatherResourceIntent(
                    priority="high",
                    constraints=(GATHER_RESOURCE_CONSTRAINT,),
                    resource="gas",
                    worker_count=1,
                    base="main",
                ),
            ),
            (
                "본진 입구에 서플라이 디포 지어",
                BuildStructureIntent(
                    priority="normal",
                    constraints=(BUILD_STRUCTURE_CONSTRAINT,),
                    structure="Supply Depot",
                    location="main ramp",
                ),
            ),
            (
                "본진에 배럭 지어",
                BuildStructureIntent(
                    priority="normal",
                    constraints=(BUILD_STRUCTURE_CONSTRAINT,),
                    structure="Barracks",
                    location="main base",
                ),
            ),
            (
                "일꾼 계속 찍어",
                TrainWorkerIntent(
                    priority="normal",
                    constraints=(KEEP_WORKER_PRODUCTION_CONSTRAINT,),
                    count=1,
                ),
            ),
            (
                "SCV 계속 생산해",
                TrainWorkerIntent(
                    priority="normal",
                    constraints=(KEEP_WORKER_PRODUCTION_CONSTRAINT,),
                    count=1,
                ),
            ),
            (
                "마린 계속 뽑아",
                TrainArmyIntent(
                    priority="normal",
                    constraints=(TRAIN_UNIT_CONSTRAINT,),
                    unit_type="Marine",
                    count=1,
                ),
            ),
            (
                "해병 생산해",
                TrainArmyIntent(
                    priority="normal",
                    constraints=(TRAIN_UNIT_CONSTRAINT,),
                    unit_type="Marine",
                    count=1,
                ),
            ),
            (
                "SCV 하나로 정찰 보내",
                ScoutIntent(
                    priority="normal",
                    constraints=(SEND_SCOUT_CONSTRAINT,),
                    target=SEND_SCOUT_DEFAULT_TARGET,
                    unit_group=SEND_SCOUT_DEFAULT_UNIT_GROUP,
                ),
            ),
            (
                "일꾼 하나 적 앞마당 확인해",
                ScoutIntent(
                    priority="high",
                    constraints=(SEND_SCOUT_CONSTRAINT,),
                    target="enemy natural",
                    unit_group=SEND_SCOUT_DEFAULT_UNIT_GROUP,
                ),
            ),
            (
                "상태 알려줘",
                SummarizeStateIntent(
                    priority="normal",
                    constraints=(SUMMARIZE_STATE_CONSTRAINT,),
                ),
            ),
            (
                "현재 상황 요약해",
                SummarizeStateIntent(
                    priority="normal",
                    constraints=(SUMMARIZE_STATE_CONSTRAINT,),
                ),
            ),
            (
                "입구 막아",
                DefendIntent(
                    priority="urgent",
                    constraints=(DEFEND_RAMP_CONSTRAINT,),
                    location=DEFEND_RAMP_LOCATION,
                    unit_group=DEFEND_RAMP_UNIT_GROUP,
                ),
            ),
            (
                "본진 입구 수비해",
                DefendIntent(
                    priority="urgent",
                    constraints=(DEFEND_RAMP_CONSTRAINT,),
                    location=DEFEND_RAMP_LOCATION,
                    unit_group=DEFEND_RAMP_UNIT_GROUP,
                ),
            ),
            (
                "벙커 수리해",
                RepairIntent(
                    priority="high",
                    constraints=(REPAIR_CONSTRAINT,),
                    target="front bunker",
                    worker_count=1,
                ),
            ),
            (
                "SCV 두 기로 앞 벙커 고쳐",
                RepairIntent(
                    priority="high",
                    constraints=(REPAIR_CONSTRAINT,),
                    target="front bunker",
                    worker_count=2,
                ),
            ),
            (
                "앞마당 가져가",
                ExpandIntent(
                    priority="normal",
                    constraints=(EXPAND_CONSTRAINT,),
                    location="natural expansion",
                ),
            ),
            (
                "앞마당에 커맨드센터 준비해",
                ExpandIntent(
                    priority="normal",
                    constraints=(EXPAND_CONSTRAINT,),
                    location="natural expansion",
                ),
            ),
            (
                "마린 두 기로 적 미네랄 라인 견제해",
                HarassIntent(
                    priority="high",
                    constraints=(HARASS_MINERAL_LINE_CONSTRAINT,),
                    target=HARASS_MINERAL_LINE_TARGET,
                    unit_group=HARASS_MINERAL_LINE_UNIT_GROUP,
                ),
            ),
            (
                "상대 일꾼 라인 흔들어",
                HarassIntent(
                    priority="high",
                    constraints=(HARASS_MINERAL_LINE_CONSTRAINT,),
                    target=HARASS_MINERAL_LINE_TARGET,
                    unit_group=HARASS_MINERAL_LINE_UNIT_GROUP,
                ),
            ),
        )

        self.assertEqual(expected_utterance_suite_count, len(expected_cases))
        intent_counts = Counter(
            expected_payload.intent for _utterance, expected_payload in expected_cases
        )
        overrepresented_intents = {
            intent: count
            for intent, count in intent_counts.items()
            if count > REPRESENTATIVE_UTTERANCES_PER_CANONICAL_INTENT
        }

        self.assertEqual(set(CANONICAL_INTENT_NAMES), set(intent_counts))
        self.assertEqual({}, overrepresented_intents)
        self.assertEqual(
            {
                intent: REPRESENTATIVE_UTTERANCES_PER_CANONICAL_INTENT
                for intent in CANONICAL_INTENT_NAMES
            },
            dict(intent_counts),
        )

        for utterance, expected_payload in expected_cases:
            with self.subTest(utterance=utterance):
                payload = interpret_command_text(utterance)
                self.assertEqual(expected_payload, payload)

                validation = validate_intent_payload(payload.to_dict())
                self.assertTrue(validation.executable)
                self.assertEqual(expected_payload, validation.payload)
                self.assertEqual(expected_payload.to_dict(), payload.to_dict())

    def test_gather_resource_mappings_target_gather_resource_dsl(self) -> None:
        self.assertEqual(2, len(GATHER_RESOURCE_MAPPINGS))

        for mapping in GATHER_RESOURCE_MAPPINGS:
            with self.subTest(utterance=mapping.utterance):
                self.assertIsInstance(mapping, InterpreterMapping)
                self.assertEqual(GATHER_RESOURCE_ALIAS, mapping.alias)
                self.assertIsInstance(mapping.payload, GatherResourceIntent)
                self.assertEqual("GATHER_RESOURCE", mapping.payload.intent)
                self.assertIn(mapping.payload.resource, ("minerals", "gas"))
                self.assertGreaterEqual(mapping.payload.worker_count, 1)
                self.assertEqual("main", mapping.payload.base)
                self.assertIn(
                    GATHER_RESOURCE_CONSTRAINT,
                    mapping.payload.constraints,
                )

    def test_keep_worker_production_mappings_target_train_worker_dsl(self) -> None:
        self.assertGreaterEqual(len(KEEP_WORKER_PRODUCTION_MAPPINGS), 5)

        for mapping in KEEP_WORKER_PRODUCTION_MAPPINGS:
            with self.subTest(utterance=mapping.utterance):
                self.assertIsInstance(mapping, InterpreterMapping)
                self.assertEqual(KEEP_WORKER_PRODUCTION_ALIAS, mapping.alias)
                self.assertIsInstance(mapping.payload, TrainWorkerIntent)
                self.assertEqual("TRAIN_WORKER", mapping.payload.intent)
                self.assertEqual(1, mapping.payload.count)
                self.assertIn(
                    KEEP_WORKER_PRODUCTION_CONSTRAINT,
                    mapping.payload.constraints,
                )

    def test_keep_worker_production_utterances_interpret_to_valid_payloads(self) -> None:
        expected_payloads = {
            mapping.utterance: mapping.payload
            for mapping in KEEP_WORKER_PRODUCTION_MAPPINGS
        }

        for utterance, expected_payload in expected_payloads.items():
            with self.subTest(utterance=utterance):
                payload = interpret_command_text(utterance)

                self.assertEqual(expected_payload, payload)
                validation = validate_intent_payload(payload.to_dict())
                self.assertTrue(validation.executable)
                self.assertEqual(expected_payload, validation.payload)

    def test_prevent_supply_block_mappings_target_supply_depot_build_dsl(
        self,
    ) -> None:
        self.assertGreaterEqual(len(PREVENT_SUPPLY_BLOCK_MAPPINGS), 5)

        for mapping in PREVENT_SUPPLY_BLOCK_MAPPINGS:
            with self.subTest(utterance=mapping.utterance):
                self.assertIsInstance(mapping, InterpreterMapping)
                self.assertEqual(PREVENT_SUPPLY_BLOCK_ALIAS, mapping.alias)
                self.assertIsInstance(mapping.payload, BuildStructureIntent)
                self.assertEqual("BUILD_STRUCTURE", mapping.payload.intent)
                self.assertEqual("Supply Depot", mapping.payload.structure)
                self.assertEqual(
                    PREVENT_SUPPLY_BLOCK_LOCATION,
                    mapping.payload.location,
                )
                self.assertIn(
                    PREVENT_SUPPLY_BLOCK_CONSTRAINT,
                    mapping.payload.constraints,
                )

    def test_prevent_supply_block_utterances_interpret_to_valid_payloads(
        self,
    ) -> None:
        expected_payloads = {
            mapping.utterance: mapping.payload
            for mapping in PREVENT_SUPPLY_BLOCK_MAPPINGS
        }

        for utterance, expected_payload in expected_payloads.items():
            with self.subTest(utterance=utterance):
                payload = interpret_command_text(utterance)

                self.assertEqual(expected_payload, payload)
                validation = validate_intent_payload(payload.to_dict())
                self.assertTrue(validation.executable)
                self.assertEqual(expected_payload, validation.payload)

    def test_build_structure_mappings_target_build_structure_dsl(self) -> None:
        self.assertGreaterEqual(len(BUILD_STRUCTURE_MAPPINGS), 5)

        mapped_structures = {mapping.payload.structure for mapping in BUILD_STRUCTURE_MAPPINGS}
        self.assertGreaterEqual(
            mapped_structures,
            {"Supply Depot", "Barracks", "Refinery", "Bunker"},
        )

        for mapping in BUILD_STRUCTURE_MAPPINGS:
            with self.subTest(utterance=mapping.utterance):
                self.assertIsInstance(mapping, InterpreterMapping)
                self.assertEqual(BUILD_STRUCTURE_ALIAS, mapping.alias)
                self.assertIsInstance(mapping.payload, BuildStructureIntent)
                self.assertEqual("BUILD_STRUCTURE", mapping.payload.intent)
                self.assertIn(
                    mapping.payload.structure,
                    BUILD_STRUCTURE_DEFAULT_LOCATIONS,
                )
                self.assertTrue(mapping.payload.location.strip())
                self.assertIn(
                    BUILD_STRUCTURE_CONSTRAINT,
                    mapping.payload.constraints,
                )

    def test_build_structure_utterances_interpret_to_valid_payloads(self) -> None:
        expected_payloads = {
            mapping.utterance: mapping.payload for mapping in BUILD_STRUCTURE_MAPPINGS
        }

        for utterance, expected_payload in expected_payloads.items():
            with self.subTest(utterance=utterance):
                payload = interpret_command_text(utterance)

                self.assertEqual(expected_payload, payload)
                validation = validate_intent_payload(payload.to_dict())
                self.assertTrue(validation.executable)
                self.assertEqual(expected_payload, validation.payload)

    def test_train_unit_mappings_target_train_army_dsl(self) -> None:
        self.assertGreaterEqual(len(TRAIN_UNIT_MAPPINGS), 5)

        mapped_units = {mapping.payload.unit_type for mapping in TRAIN_UNIT_MAPPINGS}
        self.assertEqual({"Marine"}, mapped_units)

        for mapping in TRAIN_UNIT_MAPPINGS:
            with self.subTest(utterance=mapping.utterance):
                self.assertIsInstance(mapping, InterpreterMapping)
                self.assertEqual(TRAIN_UNIT_ALIAS, mapping.alias)
                self.assertIsInstance(mapping.payload, TrainArmyIntent)
                self.assertEqual("TRAIN_ARMY", mapping.payload.intent)
                self.assertGreaterEqual(mapping.payload.count, 1)
                self.assertIn(
                    TRAIN_UNIT_CONSTRAINT,
                    mapping.payload.constraints,
                )

    def test_train_unit_utterances_interpret_to_valid_payloads(self) -> None:
        expected_payloads = {
            mapping.utterance: mapping.payload for mapping in TRAIN_UNIT_MAPPINGS
        }

        for utterance, expected_payload in expected_payloads.items():
            with self.subTest(utterance=utterance):
                payload = interpret_command_text(utterance)

                self.assertEqual(expected_payload, payload)
                validation = validate_intent_payload(payload.to_dict())
                self.assertTrue(validation.executable)
                self.assertEqual(expected_payload, validation.payload)

    def test_send_scout_mappings_target_scout_dsl(self) -> None:
        self.assertGreaterEqual(len(SEND_SCOUT_MAPPINGS), 5)

        mapped_targets = {mapping.payload.target for mapping in SEND_SCOUT_MAPPINGS}
        self.assertGreaterEqual(
            mapped_targets,
            {"enemy front", "enemy natural", "enemy main", "enemy mineral line"},
        )

        for mapping in SEND_SCOUT_MAPPINGS:
            with self.subTest(utterance=mapping.utterance):
                self.assertIsInstance(mapping, InterpreterMapping)
                self.assertEqual(SEND_SCOUT_ALIAS, mapping.alias)
                self.assertIsInstance(mapping.payload, ScoutIntent)
                self.assertEqual("SCOUT", mapping.payload.intent)
                self.assertTrue(mapping.payload.target.strip())
                self.assertTrue(mapping.payload.unit_group.strip())
                self.assertIn(
                    SEND_SCOUT_CONSTRAINT,
                    mapping.payload.constraints,
                )

    def test_send_scout_utterances_interpret_to_valid_payloads(self) -> None:
        expected_payloads = {
            mapping.utterance: mapping.payload for mapping in SEND_SCOUT_MAPPINGS
        }

        for utterance, expected_payload in expected_payloads.items():
            with self.subTest(utterance=utterance):
                payload = interpret_command_text(utterance)

                self.assertEqual(expected_payload, payload)
                validation = validate_intent_payload(payload.to_dict())
                self.assertTrue(validation.executable)
                self.assertEqual(expected_payload, validation.payload)

    def test_defend_ramp_mappings_target_defend_dsl(self) -> None:
        self.assertGreaterEqual(len(DEFEND_RAMP_MAPPINGS), 5)

        for mapping in DEFEND_RAMP_MAPPINGS:
            with self.subTest(utterance=mapping.utterance):
                self.assertIsInstance(mapping, InterpreterMapping)
                self.assertEqual(DEFEND_RAMP_ALIAS, mapping.alias)
                self.assertIsInstance(mapping.payload, DefendIntent)
                self.assertEqual("DEFEND", mapping.payload.intent)
                self.assertEqual(DEFEND_RAMP_LOCATION, mapping.payload.location)
                self.assertTrue(mapping.payload.unit_group.strip())
                self.assertIn(
                    DEFEND_RAMP_CONSTRAINT,
                    mapping.payload.constraints,
                )

    def test_defend_ramp_utterances_interpret_to_valid_payloads(self) -> None:
        expected_payloads = {
            mapping.utterance: mapping.payload for mapping in DEFEND_RAMP_MAPPINGS
        }

        for utterance, expected_payload in expected_payloads.items():
            with self.subTest(utterance=utterance):
                payload = interpret_command_text(utterance)

                self.assertEqual(expected_payload, payload)
                validation = validate_intent_payload(payload.to_dict())
                self.assertTrue(validation.executable)
                self.assertEqual(expected_payload, validation.payload)

    def test_retreat_army_mappings_target_defend_dsl(self) -> None:
        self.assertGreaterEqual(len(RETREAT_ARMY_MAPPINGS), 5)

        for mapping in RETREAT_ARMY_MAPPINGS:
            with self.subTest(utterance=mapping.utterance):
                self.assertIsInstance(mapping, InterpreterMapping)
                self.assertEqual(RETREAT_ARMY_ALIAS, mapping.alias)
                self.assertIsInstance(mapping.payload, DefendIntent)
                self.assertEqual("DEFEND", mapping.payload.intent)
                self.assertEqual(RETREAT_ARMY_LOCATION, mapping.payload.location)
                self.assertTrue(mapping.payload.unit_group.strip())
                self.assertIn(
                    RETREAT_ARMY_CONSTRAINT,
                    mapping.payload.constraints,
                )

    def test_retreat_army_utterances_interpret_to_valid_payloads(self) -> None:
        expected_payloads = {
            mapping.utterance: mapping.payload for mapping in RETREAT_ARMY_MAPPINGS
        }

        for utterance, expected_payload in expected_payloads.items():
            with self.subTest(utterance=utterance):
                payload = interpret_command_text(utterance)

                self.assertEqual(expected_payload, payload)
                validation = validate_intent_payload(payload.to_dict())
                self.assertTrue(validation.executable)
                self.assertEqual(expected_payload, validation.payload)

    def test_pressure_enemy_expansion_mappings_target_harass_dsl(self) -> None:
        self.assertGreaterEqual(len(PRESSURE_ENEMY_EXPANSION_MAPPINGS), 5)

        for mapping in PRESSURE_ENEMY_EXPANSION_MAPPINGS:
            with self.subTest(utterance=mapping.utterance):
                self.assertIsInstance(mapping, InterpreterMapping)
                self.assertEqual(PRESSURE_ENEMY_EXPANSION_ALIAS, mapping.alias)
                self.assertIsInstance(mapping.payload, HarassIntent)
                self.assertEqual("HARASS", mapping.payload.intent)
                self.assertEqual(PRESSURE_ENEMY_EXPANSION_TARGET, mapping.payload.target)
                self.assertTrue(mapping.payload.unit_group.strip())
                self.assertIn(
                    PRESSURE_ENEMY_EXPANSION_CONSTRAINT,
                    mapping.payload.constraints,
                )

    def test_pressure_enemy_expansion_utterances_interpret_to_valid_payloads(
        self,
    ) -> None:
        expected_payloads = {
            mapping.utterance: mapping.payload
            for mapping in PRESSURE_ENEMY_EXPANSION_MAPPINGS
        }

        for utterance, expected_payload in expected_payloads.items():
            with self.subTest(utterance=utterance):
                payload = interpret_command_text(utterance)

                self.assertEqual(expected_payload, payload)
                validation = validate_intent_payload(payload.to_dict())
                self.assertTrue(validation.executable)
                self.assertEqual(expected_payload, validation.payload)

    def test_harass_mineral_line_mappings_target_harass_dsl(self) -> None:
        self.assertGreaterEqual(len(HARASS_MINERAL_LINE_MAPPINGS), 5)

        for mapping in HARASS_MINERAL_LINE_MAPPINGS:
            with self.subTest(utterance=mapping.utterance):
                self.assertIsInstance(mapping, InterpreterMapping)
                self.assertEqual(HARASS_MINERAL_LINE_ALIAS, mapping.alias)
                self.assertIsInstance(mapping.payload, HarassIntent)
                self.assertEqual("HARASS", mapping.payload.intent)
                self.assertEqual(HARASS_MINERAL_LINE_TARGET, mapping.payload.target)
                self.assertTrue(mapping.payload.unit_group.strip())
                self.assertIn(
                    HARASS_MINERAL_LINE_CONSTRAINT,
                    mapping.payload.constraints,
                )

    def test_harass_mineral_line_utterances_interpret_to_valid_payloads(
        self,
    ) -> None:
        expected_payloads = {
            mapping.utterance: mapping.payload
            for mapping in HARASS_MINERAL_LINE_MAPPINGS
        }

        for utterance, expected_payload in expected_payloads.items():
            with self.subTest(utterance=utterance):
                payload = interpret_command_text(utterance)

                self.assertEqual(expected_payload, payload)
                validation = validate_intent_payload(payload.to_dict())
                self.assertTrue(validation.executable)
                self.assertEqual(expected_payload, validation.payload)

    def test_keep_worker_production_interpreter_tolerates_spacing_and_case(self) -> None:
        payload = interpret_command_text("  scv   계속   생산해  ")

        self.assertEqual(
            TrainWorkerIntent(
                priority="normal",
                constraints=(KEEP_WORKER_PRODUCTION_CONSTRAINT,),
                count=1,
            ),
            payload,
        )

    def test_prevent_supply_block_interpreter_tolerates_spacing_and_case(self) -> None:
        payload = interpret_command_text("  SUPPLY   막히기 전에   지어  ")

        self.assertEqual(
            BuildStructureIntent(
                priority="high",
                constraints=(PREVENT_SUPPLY_BLOCK_CONSTRAINT,),
                structure="Supply Depot",
                location=PREVENT_SUPPLY_BLOCK_LOCATION,
            ),
            payload,
        )

    def test_keep_worker_production_heuristic_maps_nearby_korean_free_utterance(
        self,
    ) -> None:
        payload = interpret_command_text("일꾼 생산 끊기지 않게 계속 눌러")

        self.assertEqual(
            TrainWorkerIntent(
                priority="normal",
                constraints=(KEEP_WORKER_PRODUCTION_CONSTRAINT,),
                count=1,
            ),
            payload,
        )

    def test_prevent_supply_block_heuristic_maps_nearby_korean_free_utterance(
        self,
    ) -> None:
        payload = interpret_command_text("인구수 부족하지 않게 서플 미리 확보해")

        self.assertEqual(
            BuildStructureIntent(
                priority="high",
                constraints=(PREVENT_SUPPLY_BLOCK_CONSTRAINT,),
                structure="Supply Depot",
                location=PREVENT_SUPPLY_BLOCK_LOCATION,
            ),
            payload,
        )

    def test_build_structure_heuristic_maps_nearby_korean_free_utterance(
        self,
    ) -> None:
        payload = interpret_command_text("앞마당 언덕에 벙커 하나 지어")

        self.assertEqual(
            BuildStructureIntent(
                priority="high",
                constraints=(BUILD_STRUCTURE_CONSTRAINT,),
                structure="Bunker",
                location="natural choke",
            ),
            payload,
        )

    def test_build_structure_heuristic_maps_refinery_location(self) -> None:
        payload = interpret_command_text("가스통 본진 가스에 올려")

        self.assertEqual(
            BuildStructureIntent(
                priority="high",
                constraints=(BUILD_STRUCTURE_CONSTRAINT,),
                structure="Refinery",
                location="main geyser",
            ),
            payload,
        )

    def test_train_unit_heuristic_maps_nearby_korean_free_utterance(self) -> None:
        payload = interpret_command_text("초반 압박 대비해서 마린 두 기 추가해")

        self.assertEqual(
            TrainArmyIntent(
                priority="high",
                constraints=(TRAIN_UNIT_CONSTRAINT,),
                unit_type="Marine",
                count=2,
            ),
            payload,
        )

    def test_train_unit_heuristic_tolerates_spacing_and_case(self) -> None:
        payload = interpret_command_text("  MARINES   3기   생산해  ")

        self.assertEqual(
            TrainArmyIntent(
                priority="normal",
                constraints=(TRAIN_UNIT_CONSTRAINT,),
                unit_type="Marine",
                count=3,
            ),
            payload,
        )

    def test_send_scout_heuristic_maps_nearby_korean_free_utterance(self) -> None:
        payload = interpret_command_text("초반에 일꾼 하나 보내서 상대 앞마당 확인해")

        self.assertEqual(
            ScoutIntent(
                priority="high",
                constraints=(SEND_SCOUT_CONSTRAINT,),
                target="enemy natural",
                unit_group=SEND_SCOUT_DEFAULT_UNIT_GROUP,
            ),
            payload,
        )

    def test_send_scout_heuristic_tolerates_spacing_and_case(self) -> None:
        payload = interpret_command_text("  SCOUT   enemy   front  ")

        self.assertEqual(
            ScoutIntent(
                priority="normal",
                constraints=(SEND_SCOUT_CONSTRAINT,),
                target=SEND_SCOUT_DEFAULT_TARGET,
                unit_group=SEND_SCOUT_DEFAULT_UNIT_GROUP,
            ),
            payload,
        )

    def test_defend_ramp_heuristic_maps_nearby_korean_free_utterance(self) -> None:
        payload = interpret_command_text("초반 저글링 압박 오니까 본진 입구 빨리 막아")

        self.assertEqual(
            DefendIntent(
                priority="urgent",
                constraints=(DEFEND_RAMP_CONSTRAINT,),
                location=DEFEND_RAMP_LOCATION,
                unit_group=DEFEND_RAMP_UNIT_GROUP,
            ),
            payload,
        )

    def test_defend_ramp_heuristic_detects_marine_unit_group(self) -> None:
        payload = interpret_command_text("  MARINE   ramp   hold  ")

        self.assertEqual(
            DefendIntent(
                priority="high",
                constraints=(DEFEND_RAMP_CONSTRAINT,),
                location=DEFEND_RAMP_LOCATION,
                unit_group="Marines",
            ),
            payload,
        )

    def test_retreat_army_heuristic_maps_nearby_korean_free_utterance(self) -> None:
        payload = interpret_command_text("압박 실패했으니까 병력 살려서 뒤로 빼")

        self.assertEqual(
            DefendIntent(
                priority="urgent",
                constraints=(RETREAT_ARMY_CONSTRAINT,),
                location=RETREAT_ARMY_LOCATION,
                unit_group=RETREAT_ARMY_UNIT_GROUP,
            ),
            payload,
        )

    def test_retreat_army_heuristic_detects_marine_unit_group(self) -> None:
        payload = interpret_command_text("  MARINES   pull   back  ")

        self.assertEqual(
            DefendIntent(
                priority="high",
                constraints=(RETREAT_ARMY_CONSTRAINT,),
                location=RETREAT_ARMY_LOCATION,
                unit_group="Marines",
            ),
            payload,
        )

    def test_pressure_enemy_expansion_heuristic_maps_nearby_korean_free_utterance(
        self,
    ) -> None:
        payload = interpret_command_text("지금 해병으로 적 앞마당 확장 압박해")

        self.assertEqual(
            HarassIntent(
                priority="high",
                constraints=(PRESSURE_ENEMY_EXPANSION_CONSTRAINT,),
                target=PRESSURE_ENEMY_EXPANSION_TARGET,
                unit_group="Marines",
            ),
            payload,
        )

    def test_pressure_enemy_expansion_heuristic_tolerates_spacing_and_case(self) -> None:
        payload = interpret_command_text("  MARINE   enemy   natural   pressure  ")

        self.assertEqual(
            HarassIntent(
                priority="high",
                constraints=(PRESSURE_ENEMY_EXPANSION_CONSTRAINT,),
                target=PRESSURE_ENEMY_EXPANSION_TARGET,
                unit_group="Marines",
            ),
            payload,
        )

    def test_harass_mineral_line_heuristic_maps_nearby_korean_free_utterance(
        self,
    ) -> None:
        payload = interpret_command_text("지금 해병으로 상대 일꾼 라인 흔들어")

        self.assertEqual(
            HarassIntent(
                priority="high",
                constraints=(HARASS_MINERAL_LINE_CONSTRAINT,),
                target=HARASS_MINERAL_LINE_TARGET,
                unit_group="Marines",
            ),
            payload,
        )

    def test_harass_mineral_line_heuristic_tolerates_spacing_and_case(self) -> None:
        payload = interpret_command_text("  MARINES   enemy   mineral   line   harass  ")

        self.assertEqual(
            HarassIntent(
                priority="high",
                constraints=(HARASS_MINERAL_LINE_CONSTRAINT,),
                target=HARASS_MINERAL_LINE_TARGET,
                unit_group="Marines",
            ),
            payload,
        )

    def test_summarize_state_mappings_target_summarize_state_dsl(self) -> None:
        self.assertGreaterEqual(len(SUMMARIZE_STATE_MAPPINGS), 5)

        for mapping in SUMMARIZE_STATE_MAPPINGS:
            with self.subTest(utterance=mapping.utterance):
                self.assertIsInstance(mapping, InterpreterMapping)
                self.assertEqual(SUMMARIZE_STATE_ALIAS, mapping.alias)
                self.assertIsInstance(mapping.payload, SummarizeStateIntent)
                self.assertEqual("SUMMARIZE_STATE", mapping.payload.intent)
                self.assertIn(
                    SUMMARIZE_STATE_CONSTRAINT,
                    mapping.payload.constraints,
                )

    def test_summarize_state_utterances_interpret_to_valid_payloads(self) -> None:
        expected_payloads = {
            mapping.utterance: mapping.payload
            for mapping in SUMMARIZE_STATE_MAPPINGS
        }

        for utterance, expected_payload in expected_payloads.items():
            with self.subTest(utterance=utterance):
                payload = interpret_command_text(utterance)

                self.assertEqual(expected_payload, payload)
                validation = validate_intent_payload(payload.to_dict())
                self.assertTrue(validation.executable)
                self.assertEqual(expected_payload, validation.payload)

    def test_summarize_state_heuristic_maps_nearby_korean_free_utterance(
        self,
    ) -> None:
        payload = interpret_command_text("지금 우리 병력하고 자원 상황 좀 정리해서 알려줘")

        self.assertEqual(
            SummarizeStateIntent(
                priority="normal",
                constraints=(SUMMARIZE_STATE_CONSTRAINT,),
            ),
            payload,
        )

    def test_summarize_state_heuristic_tolerates_spacing_and_case(self) -> None:
        payload = interpret_command_text("  SHOW   current   GAME   status  ")

        self.assertEqual(
            SummarizeStateIntent(
                priority="normal",
                constraints=(SUMMARIZE_STATE_CONSTRAINT,),
            ),
            payload,
        )

    def test_parser_normalizes_matched_patterns_into_typed_dsl_objects(self) -> None:
        cases = (
            (
                "앞마당 미네랄에 일꾼 다섯 기 붙여줘",
                GatherResourceIntent(
                    priority="normal",
                    constraints=(GATHER_RESOURCE_CONSTRAINT,),
                    resource="minerals",
                    worker_count=5,
                    base="natural",
                ),
            ),
            (
                "가스 부족하니까 SCV 두 기 가스에 보내",
                GatherResourceIntent(
                    priority="high",
                    constraints=(GATHER_RESOURCE_CONSTRAINT,),
                    resource="gas",
                    worker_count=2,
                    base="main",
                ),
            ),
            (
                "배럭 하나 main base에 build",
                BuildStructureIntent(
                    priority="normal",
                    constraints=(BUILD_STRUCTURE_CONSTRAINT,),
                    structure="Barracks",
                    location="main base",
                ),
            ),
            (
                "방어 급하니까 해병 네 기 queue",
                TrainArmyIntent(
                    priority="high",
                    constraints=(TRAIN_UNIT_CONSTRAINT,),
                    unit_type="Marine",
                    count=4,
                ),
            ),
            (
                "send two marines to scout enemy main",
                ScoutIntent(
                    priority="normal",
                    constraints=(SEND_SCOUT_CONSTRAINT,),
                    target="enemy main",
                    unit_group="2 Marines",
                ),
            ),
            (
                "지금 마린 두 기로 enemy natural hit",
                HarassIntent(
                    priority="high",
                    constraints=(PRESSURE_ENEMY_EXPANSION_CONSTRAINT,),
                    target=PRESSURE_ENEMY_EXPANSION_TARGET,
                    unit_group="2 Marines",
                ),
            ),
            (
                "SCV 세 기로 벙커 빨리 수리해",
                RepairIntent(
                    priority="urgent",
                    constraints=(REPAIR_CONSTRAINT,),
                    target="front bunker",
                    worker_count=3,
                ),
            ),
            (
                "natural expansion 지금 take",
                ExpandIntent(
                    priority="high",
                    constraints=(EXPAND_CONSTRAINT,),
                    location="natural expansion",
                ),
            ),
        )

        for command_text, expected_payload in cases:
            with self.subTest(command_text=command_text):
                payload = interpret_command_text(command_text)

                self.assertEqual(expected_payload, payload)
                validation = validate_intent_payload(payload.to_dict())
                self.assertTrue(validation.executable)
                self.assertEqual(expected_payload, validation.payload)

    def test_supported_utterance_registry_round_trips_to_executable_typed_dsl(
        self,
    ) -> None:
        self.assertGreaterEqual(len(INTERPRETER_MAPPINGS), 50)

        for mapping in INTERPRETER_MAPPINGS:
            with self.subTest(alias=mapping.alias, utterance=mapping.utterance):
                payload = interpret_command_text(mapping.utterance)

                self.assertEqual(mapping.payload, payload)
                self.assertIn(payload.intent, CANONICAL_INTENT_NAMES)
                self.assertIn(payload.priority, ("low", "normal", "high", "urgent"))
                self.assertIsInstance(payload.constraints, tuple)

                dsl_payload = payload.to_dict()
                self.assertTrue(
                    {"intent", "priority", "constraints"}.issubset(dsl_payload)
                )

                validation = validate_intent_payload(dsl_payload)
                self.assertTrue(validation.executable)
                self.assertEqual(mapping.payload, validation.payload)

    def test_entity_normalization_maps_aliases_counts_locations_and_targets(
        self,
    ) -> None:
        cases = (
            (
                "광물에 workers five assign",
                GatherResourceIntent(
                    priority="normal",
                    constraints=(GATHER_RESOURCE_CONSTRAINT,),
                    resource="minerals",
                    worker_count=5,
                    base="main",
                ),
            ),
            (
                "에스시비 둘 gas harvest",
                GatherResourceIntent(
                    priority="high",
                    constraints=(GATHER_RESOURCE_CONSTRAINT,),
                    resource="gas",
                    worker_count=2,
                    base="main",
                ),
            ),
            (
                "병영 main base construct",
                BuildStructureIntent(
                    priority="normal",
                    constraints=(BUILD_STRUCTURE_CONSTRAINT,),
                    structure="Barracks",
                    location="main base",
                ),
            ),
            (
                "cc main base build",
                BuildStructureIntent(
                    priority="normal",
                    constraints=(BUILD_STRUCTURE_CONSTRAINT,),
                    structure="Command Center",
                    location="main base",
                ),
            ),
            (
                "해병 넷 만들어",
                TrainArmyIntent(
                    priority="normal",
                    constraints=(TRAIN_UNIT_CONSTRAINT,),
                    unit_type="Marine",
                    count=4,
                ),
            ),
            (
                "marine one scout enemy mineral line",
                ScoutIntent(
                    priority="normal",
                    constraints=(SEND_SCOUT_CONSTRAINT,),
                    target="enemy mineral line",
                    unit_group="1 Marine",
                ),
            ),
            (
                "마린 둘로 적 앞마당 pressure",
                HarassIntent(
                    priority="high",
                    constraints=(PRESSURE_ENEMY_EXPANSION_CONSTRAINT,),
                    target=PRESSURE_ENEMY_EXPANSION_TARGET,
                    unit_group="2 Marines",
                ),
            ),
            (
                "해병 세 기로 상대 미네랄 라인 견제",
                HarassIntent(
                    priority="high",
                    constraints=(HARASS_MINERAL_LINE_CONSTRAINT,),
                    target=HARASS_MINERAL_LINE_TARGET,
                    unit_group="3 Marines",
                ),
            ),
            (
                "서플 빨리 수리",
                RepairIntent(
                    priority="urgent",
                    constraints=(REPAIR_CONSTRAINT,),
                    target="Supply Depot",
                    worker_count=1,
                ),
            ),
            (
                "내추럴 safe take",
                ExpandIntent(
                    priority="normal",
                    constraints=(EXPAND_CONSTRAINT,),
                    location="natural expansion",
                ),
            ),
        )

        for command_text, expected_payload in cases:
            with self.subTest(command_text=command_text):
                payload = interpret_command_text(command_text)

                self.assertEqual(expected_payload, payload)
                validation = validate_intent_payload(payload.to_dict())
                self.assertTrue(validation.executable)
                self.assertEqual(expected_payload, validation.payload)

    def test_repair_mappings_target_repair_dsl(self) -> None:
        self.assertEqual(2, len(REPAIR_MAPPINGS))

        for mapping in REPAIR_MAPPINGS:
            with self.subTest(utterance=mapping.utterance):
                self.assertIsInstance(mapping, InterpreterMapping)
                self.assertEqual(REPAIR_ALIAS, mapping.alias)
                self.assertIsInstance(mapping.payload, RepairIntent)
                self.assertEqual("REPAIR", mapping.payload.intent)
                self.assertEqual("front bunker", mapping.payload.target)
                self.assertGreaterEqual(mapping.payload.worker_count, 1)
                self.assertIn(REPAIR_CONSTRAINT, mapping.payload.constraints)

    def test_expand_mappings_target_expand_dsl(self) -> None:
        self.assertEqual(2, len(EXPAND_MAPPINGS))

        for mapping in EXPAND_MAPPINGS:
            with self.subTest(utterance=mapping.utterance):
                self.assertIsInstance(mapping, InterpreterMapping)
                self.assertEqual(EXPAND_ALIAS, mapping.alias)
                self.assertIsInstance(mapping.payload, ExpandIntent)
                self.assertEqual("EXPAND", mapping.payload.intent)
                self.assertEqual("natural expansion", mapping.payload.location)
                self.assertIn(EXPAND_CONSTRAINT, mapping.payload.constraints)

    def test_unsupported_command_text_does_not_execute(self) -> None:
        self.assertIsNone(interpret_command_text("핵 쏴"))
        self.assertIsNone(interpret_command_text(""))
        self.assertIsNone(interpret_command_text(7))
        self.assertIsNone(interpret_command_text("벌처 두 기 뽑아"))

    def test_unsupported_command_text_requests_clarification_without_payload(
        self,
    ) -> None:
        unsupported_commands = (
            "핵 쏴",
            "벌처 두 기 뽑아",
            "드론 생산해",
            "저글링 러시 가",
            "맵 전체 자동으로 끝내",
        )

        for command_text in unsupported_commands:
            with self.subTest(command_text=command_text):
                result = interpret_command(command_text)

                self.assertIsInstance(result, CommandInterpretationResult)
                self.assertEqual(command_text, result.command_text)
                self.assertIsNone(result.payload)
                self.assertTrue(result.clarification_required)
                self.assertEqual(
                    UNSUPPORTED_COMMAND_CLARIFICATION_REASON,
                    result.reason,
                )
                self.assertEqual(
                    UNSUPPORTED_COMMAND_CLARIFICATION_PROMPT,
                    result.clarification_prompt,
                )
                self.assertEqual(
                    UNSUPPORTED_COMMAND_CLARIFICATION_ALTERNATIVES,
                    result.alternatives,
                )
                self.assertIn("지원하지", result.clarification_prompt)
                self.assertIn("실행하지 않았습니다", result.clarification_prompt)
                self.assertIn("필요한 정보", result.clarification_prompt)
                self.assertIn("10개 MVP 의도 중 하나", result.clarification_prompt)
                self.assertIn("다시 말해", result.clarification_prompt)

    def test_malformed_command_text_requests_clarification_without_payload(
        self,
    ) -> None:
        malformed_commands = ("", "   ", 7, None)

        for command_text in malformed_commands:
            with self.subTest(command_text=command_text):
                result = interpret_command(command_text)

                self.assertIsInstance(result, CommandInterpretationResult)
                self.assertIsNone(result.payload)
                self.assertTrue(result.clarification_required)
                self.assertEqual(
                    MALFORMED_COMMAND_CLARIFICATION_REASON,
                    result.reason,
                )
                self.assertEqual(
                    MALFORMED_COMMAND_CLARIFICATION_PROMPT,
                    result.clarification_prompt,
                )
                self.assertEqual(
                    UNSUPPORTED_COMMAND_CLARIFICATION_ALTERNATIVES,
                    result.alternatives,
                )
                self.assertIn("실행하지 않았습니다", result.clarification_prompt)
                self.assertIn("필요한 정보", result.clarification_prompt)
                self.assertIn("한국어 명령 문장", result.clarification_prompt)

    def test_ambiguous_command_text_requests_clarification_without_payload(
        self,
    ) -> None:
        ambiguous_commands = (
            "정찰 보내고 입구 막아",
            "마린 계속 뽑고 적 미네랄 라인 견제해",
            "앞마당 가져가고 벙커 지어",
        )

        for command_text in ambiguous_commands:
            with self.subTest(command_text=command_text):
                self.assertIsNone(interpret_command_text(command_text))

                result = interpret_command(command_text)

                self.assertIsInstance(result, CommandInterpretationResult)
                self.assertEqual(command_text, result.command_text)
                self.assertIsNone(result.payload)
                self.assertTrue(result.clarification_required)
                self.assertEqual(
                    AMBIGUOUS_COMMAND_CLARIFICATION_REASON,
                    result.reason,
                )
                self.assertTrue(
                    result.clarification_prompt.startswith(
                        AMBIGUOUS_COMMAND_CLARIFICATION_PROMPT
                    )
                )
                self.assertEqual(
                    AMBIGUOUS_COMMAND_CLARIFICATION_ALTERNATIVES,
                    result.alternatives,
                )
                self.assertGreaterEqual(len(result.candidates), 2)
                self.assertTrue(
                    all(
                        isinstance(candidate, ClarificationCandidate)
                        for candidate in result.candidates
                    )
                )
                self.assertEqual(
                    tuple(candidate.intent for candidate in result.candidates),
                    tuple(candidate.payload.intent for candidate in result.candidates),
                )
                self.assertIn("여러", result.clarification_prompt)
                self.assertIn("실행하지 않았습니다", result.clarification_prompt)
                self.assertIn("필요한 정보", result.clarification_prompt)
                self.assertIn("가능한 해석", result.clarification_prompt)
                for candidate in result.candidates:
                    self.assertIn(candidate.description, result.clarification_prompt)

    def test_clarification_prompts_ask_for_specific_missing_or_ambiguous_info(
        self,
    ) -> None:
        cases = (
            (
                "malformed",
                "",
                ("실행할 한국어 명령 문장", "한 문장"),
            ),
            (
                "unsupported",
                "핵 쏴",
                ("10개 MVP 의도 중 하나", "상태 확인", "구조물 건설"),
            ),
            (
                "ambiguous",
                "정찰 보내고 입구 막아",
                ("이번에 실행할 명령 하나", "적 위치 확인 정찰 명령", "입구 방어 명령"),
            ),
        )

        for case_name, command_text, expected_fragments in cases:
            with self.subTest(case_name=case_name):
                result = interpret_command(command_text)

                self.assertTrue(result.clarification_required)
                self.assertIsNone(result.payload)
                self.assertIn("필요한 정보", result.clarification_prompt)
                for fragment in expected_fragments:
                    self.assertIn(fragment, result.clarification_prompt)

    def test_ambiguous_command_references_expose_competing_interpretations(
        self,
    ) -> None:
        command_text = "정찰 보내고 입구 막아"

        result = interpret_command(command_text)

        self.assertIsNone(result.payload)
        self.assertTrue(result.clarification_required)
        self.assertEqual(
            (SEND_SCOUT_ALIAS, DEFEND_RAMP_ALIAS),
            tuple(candidate.alias for candidate in result.candidates),
        )
        self.assertEqual(
            ("SCOUT", "DEFEND"),
            tuple(candidate.intent for candidate in result.candidates),
        )
        self.assertEqual(
            {
                "intent": "SCOUT",
                "priority": "normal",
                "constraints": [SEND_SCOUT_CONSTRAINT],
                "target": "enemy front",
                "unit_group": SEND_SCOUT_DEFAULT_UNIT_GROUP,
            },
            result.candidates[0].to_dict()["payload"],
        )
        self.assertEqual(
            {
                "intent": "DEFEND",
                "priority": "urgent",
                "constraints": [DEFEND_RAMP_CONSTRAINT],
                "location": DEFEND_RAMP_LOCATION,
                "unit_group": DEFEND_RAMP_UNIT_GROUP,
            },
            result.candidates[1].to_dict()["payload"],
        )

    def test_ambiguous_failure_metadata_contains_candidate_payloads(self) -> None:
        result = interpret_command("마린 계속 뽑고 적 미네랄 라인 견제해")

        self.assertIsNotNone(result.failure)
        metadata = result.failure.primary_reason.metadata
        self.assertIn("candidates", metadata)
        self.assertEqual(
            [candidate.to_dict() for candidate in result.candidates],
            metadata["candidates"],
        )
        self.assertEqual(
            (TRAIN_UNIT_ALIAS, HARASS_MINERAL_LINE_ALIAS),
            tuple(candidate["alias"] for candidate in metadata["candidates"]),
        )
        self.assertEqual(
            ["TRAIN_ARMY", "HARASS"],
            [
                candidate["payload"]["intent"]
                for candidate in metadata["candidates"]
            ],
        )

    def test_failure_cases_do_not_produce_payloads_or_execute(
        self,
    ) -> None:
        cases = (
            (
                "unsupported",
                "탱크 시즈모드 개발해",
                UNSUPPORTED_COMMAND_CLARIFICATION_REASON,
                UNSUPPORTED_COMMAND_CLARIFICATION_ALTERNATIVES,
            ),
            (
                "unsupported",
                "프로토스 질럿 생산해",
                UNSUPPORTED_COMMAND_CLARIFICATION_REASON,
                UNSUPPORTED_COMMAND_CLARIFICATION_ALTERNATIVES,
            ),
            (
                "malformed",
                "   ",
                MALFORMED_COMMAND_CLARIFICATION_REASON,
                UNSUPPORTED_COMMAND_CLARIFICATION_ALTERNATIVES,
            ),
            (
                "malformed",
                None,
                MALFORMED_COMMAND_CLARIFICATION_REASON,
                UNSUPPORTED_COMMAND_CLARIFICATION_ALTERNATIVES,
            ),
            (
                "ambiguous",
                "마린 계속 뽑고 적 미네랄 라인 견제해",
                AMBIGUOUS_COMMAND_CLARIFICATION_REASON,
                AMBIGUOUS_COMMAND_CLARIFICATION_ALTERNATIVES,
            ),
            (
                "ambiguous",
                "상태 알려주고 앞마당 가져가",
                AMBIGUOUS_COMMAND_CLARIFICATION_REASON,
                AMBIGUOUS_COMMAND_CLARIFICATION_ALTERNATIVES,
            ),
        )

        for category, command_text, reason, alternatives in cases:
            with self.subTest(category=category, command_text=command_text):
                self.assertIsNone(interpret_command_text(command_text))

                result = interpret_command(command_text)
                self.assertIsNone(result.payload)
                self.assertTrue(result.clarification_required)
                self.assertEqual(reason, result.reason)
                self.assertEqual(alternatives, result.alternatives)
                self.assertIn("실행하지 않았습니다", result.clarification_prompt)

    def test_parse_failures_surface_structured_reason_reports(self) -> None:
        cases = (
            (
                "핵 쏴",
                UNSUPPORTED_COMMAND_FAILURE_CODE,
                UNSUPPORTED_COMMAND_CLARIFICATION_REASON,
            ),
            (
                "   ",
                MALFORMED_COMMAND_FAILURE_CODE,
                MALFORMED_COMMAND_CLARIFICATION_REASON,
            ),
            (
                "상태 알려주고 앞마당 가져가",
                AMBIGUOUS_COMMAND_FAILURE_CODE,
                AMBIGUOUS_COMMAND_CLARIFICATION_REASON,
            ),
        )

        for command_text, expected_code, expected_message in cases:
            with self.subTest(command_text=command_text):
                result = interpret_command(command_text)

                self.assertIsNotNone(result.failure)
                self.assertEqual(CommandFailureStage.PARSING, result.failure.stage)
                self.assertFalse(result.failure.executed)
                self.assertFalse(result.failure.state_mutated)
                self.assertEqual((expected_code,), result.failure.reason_codes)
                self.assertEqual(expected_message, result.failure.primary_reason.message)
                self.assertTrue(result.failure.primary_reason.alternative.strip())
                self.assertTrue(
                    any(
                        alternative in result.failure.primary_reason.alternative
                        for alternative in result.alternatives
                    )
                )
                self.assertEqual(
                    "parsing",
                    result.failure.to_dict()["reasons"][0]["stage"],
                )

    def test_supported_command_text_does_not_request_clarification(self) -> None:
        result = interpret_command("상태 알려줘")

        self.assertIsInstance(result, CommandInterpretationResult)
        self.assertEqual("상태 알려줘", result.command_text)
        self.assertIsNotNone(result.payload)
        self.assertFalse(result.clarification_required)
        self.assertEqual("", result.clarification_prompt)
        self.assertEqual("", result.reason)
        self.assertEqual((), result.alternatives)
        self.assertEqual((), result.candidates)
        self.assertIsNone(result.failure)

    def test_interpretation_flow_emits_original_command_with_typed_dsl_mapping(
        self,
    ) -> None:
        command_text = "  마린 계속 뽑아  "
        result = interpret_command(command_text)
        expected_payload = {
            "intent": "TRAIN_ARMY",
            "priority": "normal",
            "constraints": [TRAIN_UNIT_CONSTRAINT],
            "unit_type": "Marine",
            "count": 1,
        }

        self.assertEqual(command_text, result.command_text)
        self.assertIsNotNone(result.payload)
        self.assertEqual(expected_payload, result.payload.to_dict())
        self.assertEqual(
            {
                "format": INTENT_DSL_FORMAT_VERSION,
                "command_text": command_text,
                INTENT_DSL_PAYLOAD_KEY: expected_payload,
                "entity_references": [],
            },
            result.to_dsl_document(),
        )

    def test_resolved_interpretation_result_serializes_to_stable_dsl_document(
        self,
    ) -> None:
        command_text = "본진 입구 수비해"
        result = interpret_command(command_text)
        expected_document = {
            "format": INTENT_DSL_FORMAT_VERSION,
            "command_text": command_text,
            INTENT_DSL_PAYLOAD_KEY: {
                "intent": "DEFEND",
                "priority": "urgent",
                "constraints": [DEFEND_RAMP_CONSTRAINT],
                "location": DEFEND_RAMP_LOCATION,
                "unit_group": DEFEND_RAMP_UNIT_GROUP,
            },
            "entity_references": [],
        }

        self.assertIsNotNone(result.payload)
        self.assertEqual(expected_document, result.to_dsl_document())
        self.assertEqual(expected_document, json.loads(result.to_dsl_json()))
        self.assertIn('"command_text": "본진 입구 수비해"', result.to_dsl_json())

    def test_unresolved_interpretation_result_rejects_dsl_serialization(self) -> None:
        result = interpret_command("핵 쏴")

        self.assertTrue(result.clarification_required)
        self.assertIsNone(result.payload)
        with self.assertRaisesRegex(ValueError, "only resolved commands"):
            result.to_dsl_document()
        with self.assertRaisesRegex(ValueError, "only resolved commands"):
            result.to_dsl_json()

    def test_interpreter_registry_includes_keep_worker_production_mappings(self) -> None:
        self.assertEqual(
            tuple(KEEP_WORKER_PRODUCTION_MAPPINGS),
            INTERPRETER_MAPPINGS[: len(KEEP_WORKER_PRODUCTION_MAPPINGS)],
        )

    def test_interpreter_registry_includes_prevent_supply_block_mappings(self) -> None:
        start = len(KEEP_WORKER_PRODUCTION_MAPPINGS)
        end = start + len(PREVENT_SUPPLY_BLOCK_MAPPINGS)

        self.assertEqual(
            tuple(PREVENT_SUPPLY_BLOCK_MAPPINGS),
            INTERPRETER_MAPPINGS[start:end],
        )

    def test_interpreter_registry_includes_build_structure_mappings(self) -> None:
        start = len(KEEP_WORKER_PRODUCTION_MAPPINGS) + len(
            PREVENT_SUPPLY_BLOCK_MAPPINGS
        )
        end = start + len(BUILD_STRUCTURE_MAPPINGS)

        self.assertEqual(
            tuple(BUILD_STRUCTURE_MAPPINGS),
            INTERPRETER_MAPPINGS[start:end],
        )

    def test_interpreter_registry_includes_train_unit_mappings(self) -> None:
        start = (
            len(KEEP_WORKER_PRODUCTION_MAPPINGS)
            + len(PREVENT_SUPPLY_BLOCK_MAPPINGS)
            + len(BUILD_STRUCTURE_MAPPINGS)
        )
        end = start + len(TRAIN_UNIT_MAPPINGS)

        self.assertEqual(
            tuple(TRAIN_UNIT_MAPPINGS),
            INTERPRETER_MAPPINGS[start:end],
        )

    def test_interpreter_registry_includes_defend_ramp_mappings(self) -> None:
        start = (
            len(KEEP_WORKER_PRODUCTION_MAPPINGS)
            + len(PREVENT_SUPPLY_BLOCK_MAPPINGS)
            + len(BUILD_STRUCTURE_MAPPINGS)
            + len(TRAIN_UNIT_MAPPINGS)
            + len(SEND_SCOUT_MAPPINGS)
        )
        end = start + len(DEFEND_RAMP_MAPPINGS)

        self.assertEqual(
            tuple(DEFEND_RAMP_MAPPINGS),
            INTERPRETER_MAPPINGS[start:end],
        )

    def test_interpreter_registry_includes_send_scout_mappings(self) -> None:
        start = (
            len(KEEP_WORKER_PRODUCTION_MAPPINGS)
            + len(PREVENT_SUPPLY_BLOCK_MAPPINGS)
            + len(BUILD_STRUCTURE_MAPPINGS)
            + len(TRAIN_UNIT_MAPPINGS)
        )
        end = start + len(SEND_SCOUT_MAPPINGS)

        self.assertEqual(
            tuple(SEND_SCOUT_MAPPINGS),
            INTERPRETER_MAPPINGS[start:end],
        )

    def test_interpreter_registry_includes_retreat_army_mappings(self) -> None:
        start = (
            len(KEEP_WORKER_PRODUCTION_MAPPINGS)
            + len(PREVENT_SUPPLY_BLOCK_MAPPINGS)
            + len(BUILD_STRUCTURE_MAPPINGS)
            + len(TRAIN_UNIT_MAPPINGS)
            + len(SEND_SCOUT_MAPPINGS)
            + len(DEFEND_RAMP_MAPPINGS)
        )
        end = start + len(RETREAT_ARMY_MAPPINGS)

        self.assertEqual(
            tuple(RETREAT_ARMY_MAPPINGS),
            INTERPRETER_MAPPINGS[start:end],
        )

    def test_interpreter_registry_includes_pressure_enemy_expansion_mappings(
        self,
    ) -> None:
        start = (
            len(KEEP_WORKER_PRODUCTION_MAPPINGS)
            + len(PREVENT_SUPPLY_BLOCK_MAPPINGS)
            + len(BUILD_STRUCTURE_MAPPINGS)
            + len(TRAIN_UNIT_MAPPINGS)
            + len(SEND_SCOUT_MAPPINGS)
            + len(DEFEND_RAMP_MAPPINGS)
            + len(RETREAT_ARMY_MAPPINGS)
        )
        end = start + len(PRESSURE_ENEMY_EXPANSION_MAPPINGS)

        self.assertEqual(
            tuple(PRESSURE_ENEMY_EXPANSION_MAPPINGS),
            INTERPRETER_MAPPINGS[start:end],
        )

    def test_interpreter_registry_includes_harass_mineral_line_mappings(
        self,
    ) -> None:
        start = (
            len(KEEP_WORKER_PRODUCTION_MAPPINGS)
            + len(PREVENT_SUPPLY_BLOCK_MAPPINGS)
            + len(BUILD_STRUCTURE_MAPPINGS)
            + len(TRAIN_UNIT_MAPPINGS)
            + len(SEND_SCOUT_MAPPINGS)
            + len(DEFEND_RAMP_MAPPINGS)
            + len(RETREAT_ARMY_MAPPINGS)
            + len(PRESSURE_ENEMY_EXPANSION_MAPPINGS)
        )
        end = start + len(HARASS_MINERAL_LINE_MAPPINGS)

        self.assertEqual(
            tuple(HARASS_MINERAL_LINE_MAPPINGS),
            INTERPRETER_MAPPINGS[start:end],
        )

    def test_interpreter_registry_includes_summarize_state_mappings(
        self,
    ) -> None:
        start = (
            len(KEEP_WORKER_PRODUCTION_MAPPINGS)
            + len(PREVENT_SUPPLY_BLOCK_MAPPINGS)
            + len(BUILD_STRUCTURE_MAPPINGS)
            + len(TRAIN_UNIT_MAPPINGS)
            + len(SEND_SCOUT_MAPPINGS)
            + len(DEFEND_RAMP_MAPPINGS)
            + len(RETREAT_ARMY_MAPPINGS)
            + len(PRESSURE_ENEMY_EXPANSION_MAPPINGS)
            + len(HARASS_MINERAL_LINE_MAPPINGS)
        )
        end = start + len(SUMMARIZE_STATE_MAPPINGS)

        self.assertEqual(
            tuple(SUMMARIZE_STATE_MAPPINGS),
            INTERPRETER_MAPPINGS[start:end],
        )

    def test_package_exports_interpreter_mapping_surface(self) -> None:
        self.assertIs(INTERPRETER_MAPPINGS, package_exports.INTERPRETER_MAPPINGS)
        self.assertEqual(
            KEEP_WORKER_PRODUCTION_ALIAS,
            package_exports.KEEP_WORKER_PRODUCTION_ALIAS,
        )
        self.assertEqual(
            KEEP_WORKER_PRODUCTION_CONSTRAINT,
            package_exports.KEEP_WORKER_PRODUCTION_CONSTRAINT,
        )
        self.assertIs(
            KEEP_WORKER_PRODUCTION_MAPPINGS,
            package_exports.KEEP_WORKER_PRODUCTION_MAPPINGS,
        )
        self.assertEqual(
            PREVENT_SUPPLY_BLOCK_ALIAS,
            package_exports.PREVENT_SUPPLY_BLOCK_ALIAS,
        )
        self.assertEqual(
            PREVENT_SUPPLY_BLOCK_CONSTRAINT,
            package_exports.PREVENT_SUPPLY_BLOCK_CONSTRAINT,
        )
        self.assertEqual(
            PREVENT_SUPPLY_BLOCK_LOCATION,
            package_exports.PREVENT_SUPPLY_BLOCK_LOCATION,
        )
        self.assertIs(
            PREVENT_SUPPLY_BLOCK_MAPPINGS,
            package_exports.PREVENT_SUPPLY_BLOCK_MAPPINGS,
        )
        self.assertEqual(
            BUILD_STRUCTURE_ALIAS,
            package_exports.BUILD_STRUCTURE_ALIAS,
        )
        self.assertEqual(
            BUILD_STRUCTURE_CONSTRAINT,
            package_exports.BUILD_STRUCTURE_CONSTRAINT,
        )
        self.assertEqual(
            BUILD_STRUCTURE_DEFAULT_LOCATIONS,
            package_exports.BUILD_STRUCTURE_DEFAULT_LOCATIONS,
        )
        self.assertIs(
            BUILD_STRUCTURE_MAPPINGS,
            package_exports.BUILD_STRUCTURE_MAPPINGS,
        )
        self.assertEqual(
            TRAIN_UNIT_ALIAS,
            package_exports.TRAIN_UNIT_ALIAS,
        )
        self.assertEqual(
            TRAIN_UNIT_CONSTRAINT,
            package_exports.TRAIN_UNIT_CONSTRAINT,
        )
        self.assertIs(
            TRAIN_UNIT_MAPPINGS,
            package_exports.TRAIN_UNIT_MAPPINGS,
        )
        self.assertEqual(
            SEND_SCOUT_ALIAS,
            package_exports.SEND_SCOUT_ALIAS,
        )
        self.assertEqual(
            SEND_SCOUT_CONSTRAINT,
            package_exports.SEND_SCOUT_CONSTRAINT,
        )
        self.assertEqual(
            SEND_SCOUT_DEFAULT_TARGET,
            package_exports.SEND_SCOUT_DEFAULT_TARGET,
        )
        self.assertEqual(
            SEND_SCOUT_DEFAULT_UNIT_GROUP,
            package_exports.SEND_SCOUT_DEFAULT_UNIT_GROUP,
        )
        self.assertIs(
            SEND_SCOUT_MAPPINGS,
            package_exports.SEND_SCOUT_MAPPINGS,
        )
        self.assertEqual(
            DEFEND_RAMP_ALIAS,
            package_exports.DEFEND_RAMP_ALIAS,
        )
        self.assertEqual(
            DEFEND_RAMP_CONSTRAINT,
            package_exports.DEFEND_RAMP_CONSTRAINT,
        )
        self.assertEqual(
            DEFEND_RAMP_LOCATION,
            package_exports.DEFEND_RAMP_LOCATION,
        )
        self.assertEqual(
            DEFEND_RAMP_UNIT_GROUP,
            package_exports.DEFEND_RAMP_UNIT_GROUP,
        )
        self.assertIs(
            DEFEND_RAMP_MAPPINGS,
            package_exports.DEFEND_RAMP_MAPPINGS,
        )
        self.assertEqual(
            RETREAT_ARMY_ALIAS,
            package_exports.RETREAT_ARMY_ALIAS,
        )
        self.assertEqual(
            RETREAT_ARMY_CONSTRAINT,
            package_exports.RETREAT_ARMY_CONSTRAINT,
        )
        self.assertEqual(
            RETREAT_ARMY_LOCATION,
            package_exports.RETREAT_ARMY_LOCATION,
        )
        self.assertEqual(
            RETREAT_ARMY_UNIT_GROUP,
            package_exports.RETREAT_ARMY_UNIT_GROUP,
        )
        self.assertIs(
            RETREAT_ARMY_MAPPINGS,
            package_exports.RETREAT_ARMY_MAPPINGS,
        )
        self.assertEqual(
            PRESSURE_ENEMY_EXPANSION_ALIAS,
            package_exports.PRESSURE_ENEMY_EXPANSION_ALIAS,
        )
        self.assertEqual(
            PRESSURE_ENEMY_EXPANSION_CONSTRAINT,
            package_exports.PRESSURE_ENEMY_EXPANSION_CONSTRAINT,
        )
        self.assertEqual(
            PRESSURE_ENEMY_EXPANSION_TARGET,
            package_exports.PRESSURE_ENEMY_EXPANSION_TARGET,
        )
        self.assertEqual(
            PRESSURE_ENEMY_EXPANSION_UNIT_GROUP,
            package_exports.PRESSURE_ENEMY_EXPANSION_UNIT_GROUP,
        )
        self.assertIs(
            PRESSURE_ENEMY_EXPANSION_MAPPINGS,
            package_exports.PRESSURE_ENEMY_EXPANSION_MAPPINGS,
        )
        self.assertEqual(
            HARASS_MINERAL_LINE_ALIAS,
            package_exports.HARASS_MINERAL_LINE_ALIAS,
        )
        self.assertEqual(
            HARASS_MINERAL_LINE_CONSTRAINT,
            package_exports.HARASS_MINERAL_LINE_CONSTRAINT,
        )
        self.assertEqual(
            HARASS_MINERAL_LINE_TARGET,
            package_exports.HARASS_MINERAL_LINE_TARGET,
        )
        self.assertEqual(
            HARASS_MINERAL_LINE_UNIT_GROUP,
            package_exports.HARASS_MINERAL_LINE_UNIT_GROUP,
        )
        self.assertIs(
            HARASS_MINERAL_LINE_MAPPINGS,
            package_exports.HARASS_MINERAL_LINE_MAPPINGS,
        )
        self.assertEqual(
            SUMMARIZE_STATE_ALIAS,
            package_exports.SUMMARIZE_STATE_ALIAS,
        )
        self.assertEqual(
            SUMMARIZE_STATE_CONSTRAINT,
            package_exports.SUMMARIZE_STATE_CONSTRAINT,
        )
        self.assertIs(
            SUMMARIZE_STATE_MAPPINGS,
            package_exports.SUMMARIZE_STATE_MAPPINGS,
        )
        self.assertEqual(
            GATHER_RESOURCE_ALIAS,
            package_exports.GATHER_RESOURCE_ALIAS,
        )
        self.assertEqual(
            GATHER_RESOURCE_CONSTRAINT,
            package_exports.GATHER_RESOURCE_CONSTRAINT,
        )
        self.assertIs(
            GATHER_RESOURCE_MAPPINGS,
            package_exports.GATHER_RESOURCE_MAPPINGS,
        )
        self.assertEqual(
            REPAIR_ALIAS,
            package_exports.REPAIR_ALIAS,
        )
        self.assertEqual(
            REPAIR_CONSTRAINT,
            package_exports.REPAIR_CONSTRAINT,
        )
        self.assertIs(REPAIR_MAPPINGS, package_exports.REPAIR_MAPPINGS)
        self.assertEqual(
            EXPAND_ALIAS,
            package_exports.EXPAND_ALIAS,
        )
        self.assertEqual(
            EXPAND_CONSTRAINT,
            package_exports.EXPAND_CONSTRAINT,
        )
        self.assertIs(EXPAND_MAPPINGS, package_exports.EXPAND_MAPPINGS)
        self.assertEqual(
            REPRESENTATIVE_UTTERANCES_PER_CANONICAL_INTENT,
            package_exports.REPRESENTATIVE_UTTERANCES_PER_CANONICAL_INTENT,
        )
        self.assertIs(
            REPRESENTATIVE_UTTERANCE_MATRIX,
            package_exports.REPRESENTATIVE_UTTERANCE_MATRIX,
        )
        self.assertEqual(
            KOREAN_COMMAND_TEST_CORPUS,
            package_exports.KOREAN_COMMAND_TEST_CORPUS,
        )
        self.assertIs(
            CommandInterpretationResult,
            package_exports.CommandInterpretationResult,
        )
        self.assertIs(CommandInterpreter, package_exports.CommandInterpreter)
        self.assertIs(
            CommandInterpreterInterface,
            package_exports.CommandInterpreterInterface,
        )
        self.assertIs(
            DEFAULT_COMMAND_INTERPRETER,
            package_exports.DEFAULT_COMMAND_INTERPRETER,
        )
        self.assertIs(
            ClarificationCandidate,
            package_exports.ClarificationCandidate,
        )
        self.assertEqual(
            UNSUPPORTED_COMMAND_CLARIFICATION_REASON,
            package_exports.UNSUPPORTED_COMMAND_CLARIFICATION_REASON,
        )
        self.assertEqual(
            MALFORMED_COMMAND_CLARIFICATION_REASON,
            package_exports.MALFORMED_COMMAND_CLARIFICATION_REASON,
        )
        self.assertEqual(
            MALFORMED_COMMAND_CLARIFICATION_PROMPT,
            package_exports.MALFORMED_COMMAND_CLARIFICATION_PROMPT,
        )
        self.assertEqual(
            AMBIGUOUS_COMMAND_CLARIFICATION_REASON,
            package_exports.AMBIGUOUS_COMMAND_CLARIFICATION_REASON,
        )
        self.assertEqual(
            AMBIGUOUS_COMMAND_CLARIFICATION_PROMPT,
            package_exports.AMBIGUOUS_COMMAND_CLARIFICATION_PROMPT,
        )
        self.assertEqual(
            AMBIGUOUS_COMMAND_CLARIFICATION_ALTERNATIVES,
            package_exports.AMBIGUOUS_COMMAND_CLARIFICATION_ALTERNATIVES,
        )
        self.assertEqual(
            UNSUPPORTED_COMMAND_CLARIFICATION_PROMPT,
            package_exports.UNSUPPORTED_COMMAND_CLARIFICATION_PROMPT,
        )
        self.assertEqual(
            UNSUPPORTED_COMMAND_CLARIFICATION_ALTERNATIVES,
            package_exports.UNSUPPORTED_COMMAND_CLARIFICATION_ALTERNATIVES,
        )
        self.assertEqual(
            UTTERANCE_MATRIX_CANONICAL_INTENT_NAMES,
            package_exports.UTTERANCE_MATRIX_CANONICAL_INTENT_NAMES,
        )
        self.assertIs(interpret_command, package_exports.interpret_command)
        self.assertIs(interpret_command_text, package_exports.interpret_command_text)


if __name__ == "__main__":
    unittest.main()
