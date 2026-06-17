import json
import unittest
from types import SimpleNamespace

import starcraft_commander.feasibility as feasibility_module
from starcraft_commander.feasibility import (
    DEFAULT_SC2_FEASIBILITY_VALIDATOR,
    SC2_CANONICAL_INTENT_NAMES,
    SC2_FEASIBILITY_REASON_CODES,
    SC2_STRUCTURE_COSTS,
    SC2_UNIT_COSTS,
    SC2FeasibilityResult,
    SC2FeasibilityValidator,
    SC2FeasibilityValidatorInterface,
    SC2StructureCost,
    SC2UnitCost,
    resolve_sc2_structure_code,
    resolve_sc2_unit_code,
    validate_sc2_feasibility,
)
from starcraft_commander.state_resolver import SC2CommanderState


COMPLETE_STATE_KWARGS = {
    "minerals": 400,
    "vespene": 125,
    "supply_used": 20,
    "supply_cap": 31,
    "supply_left": 11,
    "own_units": {"SCV": 12, "MARINE": 4},
    "own_structures": {
        "COMMANDCENTER": 1,
        "SUPPLYDEPOT": 2,
        "BARRACKS": 1,
        "REFINERY": 1,
    },
    "visible_enemy_units": {"ZERGLING": 4},
    "idle_worker_count": 2,
    "army_count": 4,
    "game_loop": 672,
    "game_time_seconds": 30.0,
}


def build_state(**overrides: object) -> SC2CommanderState:
    kwargs = dict(COMPLETE_STATE_KWARGS)
    kwargs.update(overrides)
    return SC2CommanderState(**kwargs)


def payload_for(intent: str) -> dict[str, object]:
    payloads: dict[str, dict[str, object]] = {
        "GATHER_RESOURCE": {
            "intent": "GATHER_RESOURCE",
            "resource": "minerals",
            "worker_count": 4,
            "base": "main",
        },
        "BUILD_STRUCTURE": {
            "intent": "BUILD_STRUCTURE",
            "structure": "Supply Depot",
            "location": "main",
        },
        "TRAIN_WORKER": {"intent": "TRAIN_WORKER", "count": 1},
        "TRAIN_ARMY": {"intent": "TRAIN_ARMY", "unit_type": "Marine", "count": 1},
        "SCOUT": {"intent": "SCOUT", "unit_group": "1 SCV", "target": "enemy main"},
        "SUMMARIZE_STATE": {"intent": "SUMMARIZE_STATE"},
        "DEFEND": {"intent": "DEFEND", "unit_group": "all Marines", "location": "main ramp"},
        "REPAIR": {"intent": "REPAIR", "target": "front bunker", "worker_count": 1},
        "EXPAND": {"intent": "EXPAND", "location": "natural expansion"},
        "HARASS": {
            "intent": "HARASS",
            "unit_group": "all Marines",
            "target": "enemy mineral line",
        },
        "MOVE_CAMERA": {"intent": "MOVE_CAMERA", "target": "main base"},
    }
    return dict(payloads[intent])


class CostRegistryTest(unittest.TestCase):
    def test_terran_unit_cost_table(self) -> None:
        expected = {
            "SCV": (50, 0, 1, "COMMANDCENTER"),
            "MARINE": (50, 0, 1, "BARRACKS"),
            "HELLION": (100, 0, 2, "FACTORY"),
        }
        self.assertEqual(set(SC2_UNIT_COSTS), set(expected))
        for unit_code, (minerals, vespene, supply, producer) in expected.items():
            with self.subTest(unit=unit_code):
                cost = SC2_UNIT_COSTS[unit_code]
                self.assertEqual(cost.minerals, minerals)
                self.assertEqual(cost.vespene, vespene)
                self.assertEqual(cost.supply, supply)
                self.assertEqual(cost.producer, producer)
                self.assertEqual(
                    cost.to_dict(),
                    {
                        "minerals": minerals,
                        "vespene": vespene,
                        "supply": supply,
                        "producer": producer,
                    },
                )

    def test_terran_structure_cost_table(self) -> None:
        expected = {
            "SUPPLYDEPOT": (100, 0, ()),
            "BARRACKS": (150, 0, ("SUPPLYDEPOT",)),
            "FACTORY": (150, 100, ("BARRACKS",)),
            "REFINERY": (75, 0, ()),
            "COMMANDCENTER": (400, 0, ()),
            "BUNKER": (100, 0, ("BARRACKS",)),
        }
        self.assertEqual(set(SC2_STRUCTURE_COSTS), set(expected))
        for structure_code, (minerals, vespene, requirements) in expected.items():
            with self.subTest(structure=structure_code):
                cost = SC2_STRUCTURE_COSTS[structure_code]
                self.assertEqual(cost.minerals, minerals)
                self.assertEqual(cost.vespene, vespene)
                self.assertEqual(cost.tech_requirements, requirements)
                self.assertEqual(
                    cost.to_dict(),
                    {
                        "minerals": minerals,
                        "vespene": vespene,
                        "tech_requirements": list(requirements),
                    },
                )

    def test_cost_dataclasses_reject_invalid_values(self) -> None:
        cases = (
            ("negative minerals", lambda: SC2UnitCost(-1, 0, 1, "BARRACKS"), ValueError),
            ("empty producer", lambda: SC2UnitCost(50, 0, 1, " "), ValueError),
            ("non-int vespene", lambda: SC2StructureCost(100, "0"), TypeError),
            ("negative structure", lambda: SC2StructureCost(-100, 0), ValueError),
        )
        for label, build, expected_error in cases:
            with self.subTest(case=label):
                with self.assertRaises(expected_error):
                    build()

    def test_vocabulary_resolution(self) -> None:
        cases = (
            ("Marine", resolve_sc2_unit_code, "MARINE"),
            ("Vulture", resolve_sc2_unit_code, "HELLION"),
            ("SCV", resolve_sc2_unit_code, "SCV"),
            ("Dragoon", resolve_sc2_unit_code, None),
            ("Supply Depot", resolve_sc2_structure_code, "SUPPLYDEPOT"),
            ("Command Center", resolve_sc2_structure_code, "COMMANDCENTER"),
            ("Barracks", resolve_sc2_structure_code, "BARRACKS"),
            ("Pylon", resolve_sc2_structure_code, None),
        )
        for raw_name, resolver, expected in cases:
            with self.subTest(name=raw_name):
                self.assertEqual(resolver(raw_name), expected)


class SC2FeasibilityResultContractTest(unittest.TestCase):
    def test_executable_result_cannot_carry_reasons(self) -> None:
        cases = (
            ("reason codes", {"reason_codes": ("no_workers",)}),
            ("reasons", {"reasons": ("이유",)}),
        )
        for label, extra in cases:
            with self.subTest(case=label):
                with self.assertRaises(ValueError):
                    SC2FeasibilityResult(
                        executable=True,
                        intent_name="TRAIN_WORKER",
                        **extra,
                    )

    def test_rejected_result_requires_reasons_and_alternative(self) -> None:
        cases = (
            ("missing reason codes", {"reasons": ("이유",), "alternative": "대안"}),
            (
                "missing reasons",
                {"reason_codes": ("no_workers",), "alternative": "대안"},
            ),
            (
                "blank alternative",
                {
                    "reason_codes": ("no_workers",),
                    "reasons": ("이유",),
                    "alternative": "  ",
                },
            ),
        )
        for label, fields in cases:
            with self.subTest(case=label):
                with self.assertRaises(ValueError):
                    SC2FeasibilityResult(
                        executable=False,
                        intent_name="TRAIN_WORKER",
                        **fields,
                    )

    def test_intent_name_must_be_non_empty(self) -> None:
        with self.assertRaises(ValueError):
            SC2FeasibilityResult(executable=True, intent_name="  ")

    def test_to_dict_is_json_ready(self) -> None:
        result = SC2FeasibilityResult(
            executable=False,
            intent_name="TRAIN_ARMY",
            reason_codes=("insufficient_minerals",),
            reasons=("미네랄 10 부족: 현재 40, 필요 50.",),
            alternative="SCV로 미네랄을 더 채취한 뒤 다시 시도하세요.",
            checked=("state", "intent", "observation", "minerals"),
        )
        payload = result.to_dict()
        self.assertEqual(
            payload,
            {
                "executable": False,
                "intent_name": "TRAIN_ARMY",
                "reason_codes": ["insufficient_minerals"],
                "reasons": ["미네랄 10 부족: 현재 40, 필요 50."],
                "alternative": "SCV로 미네랄을 더 채취한 뒤 다시 시도하세요.",
                "checked": ["state", "intent", "observation", "minerals"],
            },
        )
        json.dumps(payload, ensure_ascii=False)


class StateGateTest(unittest.TestCase):
    def test_none_state_rejects_every_intent(self) -> None:
        for intent in SC2_CANONICAL_INTENT_NAMES:
            with self.subTest(intent=intent):
                result = validate_sc2_feasibility(payload_for(intent), None)
                self.assertFalse(result.executable)
                self.assertEqual(result.reason_codes, ("unknown_state",))
                self.assertIn("상태를 확인할 수 없어", result.reasons[0])
                self.assertTrue(result.alternative.strip())

    def test_incomplete_observation_rejects_mutating_intents(self) -> None:
        state = build_state(observation_notes=("bot.minerals is missing; defaulted to 0.",))
        self.assertFalse(state.observation_complete)
        for intent in SC2_CANONICAL_INTENT_NAMES:
            if intent == "SUMMARIZE_STATE":
                continue
            with self.subTest(intent=intent):
                result = validate_sc2_feasibility(payload_for(intent), state)
                self.assertFalse(result.executable)
                self.assertEqual(result.reason_codes, ("incomplete_observation",))
                self.assertIn("관측이 불완전", result.reasons[0])
                self.assertIn("SUMMARIZE_STATE", result.alternative)

    def test_summarize_state_always_executable(self) -> None:
        cases = (
            ("complete observation", build_state()),
            (
                "incomplete observation",
                build_state(observation_notes=("bot.minerals could not be read.",)),
            ),
            ("empty default state", SC2CommanderState()),
        )
        for label, state in cases:
            with self.subTest(case=label):
                result = validate_sc2_feasibility(payload_for("SUMMARIZE_STATE"), state)
                self.assertTrue(result.executable)
                self.assertEqual(result.reason_codes, ())
                self.assertEqual(result.reasons, ())
                self.assertIn("read_only", result.checked)

    def test_unknown_intent_rejected_with_canonical_names(self) -> None:
        for intent_value in ("DANCE", "", "train_worker"):
            with self.subTest(intent=intent_value):
                result = validate_sc2_feasibility({"intent": intent_value}, build_state())
                self.assertFalse(result.executable)
                self.assertEqual(result.reason_codes, ("unsupported_intent",))
                for canonical_name in SC2_CANONICAL_INTENT_NAMES:
                    self.assertIn(canonical_name, result.reasons[0])

    def test_wrong_state_type_raises_type_error(self) -> None:
        with self.assertRaises(TypeError):
            validate_sc2_feasibility(payload_for("TRAIN_WORKER"), {"minerals": 50})


class TrainFeasibilityTest(unittest.TestCase):
    def test_train_rejections_by_single_shortfall(self) -> None:
        cases = (
            (
                "worker minerals",
                payload_for("TRAIN_WORKER"),
                build_state(minerals=40),
                "insufficient_minerals",
                ("미네랄 10 부족", "현재 40", "필요 50"),
            ),
            (
                "worker supply",
                payload_for("TRAIN_WORKER"),
                build_state(supply_used=31, supply_left=0),
                "insufficient_supply",
                ("보급 1 부족", "남은 보급 0", "필요 1"),
            ),
            (
                "worker missing producer",
                payload_for("TRAIN_WORKER"),
                build_state(own_structures={"SUPPLYDEPOT": 1, "BARRACKS": 1}),
                "missing_producer",
                ("사령부(COMMANDCENTER)",),
            ),
            (
                "marine missing producer",
                payload_for("TRAIN_ARMY"),
                build_state(own_structures={"COMMANDCENTER": 1, "SUPPLYDEPOT": 1}),
                "missing_producer",
                ("해병(MARINE)", "병영(BARRACKS)"),
            ),
            (
                "marine batch minerals",
                {"intent": "TRAIN_ARMY", "unit_type": "Marine", "count": 5},
                build_state(minerals=200),
                "insufficient_minerals",
                ("미네랄 50 부족", "현재 200", "필요 250"),
            ),
            (
                "vulture missing factory",
                {"intent": "TRAIN_ARMY", "unit_type": "Vulture", "count": 1},
                build_state(),
                "missing_producer",
                ("화염차(HELLION)", "군수공장(FACTORY)"),
            ),
        )
        for label, payload, state, expected_code, reason_fragments in cases:
            with self.subTest(case=label):
                result = validate_sc2_feasibility(payload, state)
                self.assertFalse(result.executable)
                self.assertEqual(result.reason_codes, (expected_code,))
                for fragment in reason_fragments:
                    self.assertIn(fragment, result.reasons[0])
                self.assertTrue(result.alternative.strip())

    def test_affordable_train_commands_are_executable(self) -> None:
        vulture_state = build_state(
            own_structures={
                "COMMANDCENTER": 1,
                "SUPPLYDEPOT": 1,
                "BARRACKS": 1,
                "FACTORY": 1,
            },
        )
        cases = (
            ("train worker", payload_for("TRAIN_WORKER"), build_state()),
            ("train marine", payload_for("TRAIN_ARMY"), build_state()),
            (
                "train vulture as hellion",
                {"intent": "TRAIN_ARMY", "unit_type": "Vulture", "count": 2},
                vulture_state,
            ),
            (
                "exact minerals and supply",
                {"intent": "TRAIN_ARMY", "unit_type": "Marine", "count": 4},
                build_state(minerals=200, supply_used=27, supply_left=4),
            ),
        )
        for label, payload, state in cases:
            with self.subTest(case=label):
                result = validate_sc2_feasibility(payload, state)
                self.assertTrue(result.executable)
                self.assertEqual(result.reason_codes, ())
                for check_name in ("minerals", "vespene", "supply", "producer"):
                    self.assertIn(check_name, result.checked)

    def test_unknown_unit_type_rejected(self) -> None:
        result = validate_sc2_feasibility(
            {"intent": "TRAIN_ARMY", "unit_type": "Dragoon", "count": 1},
            build_state(),
        )
        self.assertFalse(result.executable)
        self.assertEqual(result.reason_codes, ("unsupported_unit",))
        self.assertIn("Dragoon", result.reasons[0])

    def test_invalid_count_rejected(self) -> None:
        for bad_count in (0, -3, "many"):
            with self.subTest(count=bad_count):
                result = validate_sc2_feasibility(
                    {"intent": "TRAIN_WORKER", "count": bad_count},
                    build_state(),
                )
                self.assertFalse(result.executable)
                self.assertEqual(result.reason_codes, ("invalid_payload",))


class BuildFeasibilityTest(unittest.TestCase):
    def test_build_rejections(self) -> None:
        cases = (
            (
                "factory without barracks",
                {"intent": "BUILD_STRUCTURE", "structure": "Factory", "location": "main"},
                build_state(own_structures={"COMMANDCENTER": 1, "SUPPLYDEPOT": 1}),
                "missing_tech_requirement",
                ("군수공장(FACTORY)", "병영(BARRACKS)"),
            ),
            (
                "factory without vespene",
                {"intent": "BUILD_STRUCTURE", "structure": "Factory", "location": "main"},
                build_state(vespene=25),
                "insufficient_vespene",
                ("가스 75 부족", "현재 25", "필요 100"),
            ),
            (
                "build without workers",
                payload_for("BUILD_STRUCTURE"),
                build_state(own_units={"MARINE": 4}),
                "no_workers",
                ("사용할 수 있는 SCV가 없습니다",),
            ),
            (
                "expand checks command center cost",
                payload_for("EXPAND"),
                build_state(minerals=399),
                "insufficient_minerals",
                ("미네랄 1 부족", "현재 399", "필요 400"),
            ),
            (
                "expand without workers",
                payload_for("EXPAND"),
                build_state(own_units={"MARINE": 4}),
                "no_workers",
                ("SCV",),
            ),
        )
        for label, payload, state, expected_code, reason_fragments in cases:
            with self.subTest(case=label):
                result = validate_sc2_feasibility(payload, state)
                self.assertFalse(result.executable)
                self.assertIn(expected_code, result.reason_codes)
                joined_reasons = " ".join(result.reasons)
                for fragment in reason_fragments:
                    self.assertIn(fragment, joined_reasons)
                self.assertTrue(result.alternative.strip())

    def test_barracks_rejection_suggests_supply_depot_first(self) -> None:
        result = validate_sc2_feasibility(
            {"intent": "BUILD_STRUCTURE", "structure": "Barracks", "location": "main"},
            build_state(own_structures={"COMMANDCENTER": 1}),
        )
        self.assertFalse(result.executable)
        self.assertEqual(result.reason_codes, ("missing_tech_requirement",))
        self.assertIn("먼저 보급고를 건설하세요", result.alternative)

    def test_affordable_builds_are_executable(self) -> None:
        cases = (
            ("supply depot", payload_for("BUILD_STRUCTURE"), build_state()),
            (
                "factory with barracks and gas",
                {"intent": "BUILD_STRUCTURE", "structure": "Factory", "location": "main"},
                build_state(),
            ),
            (
                "bunker with barracks",
                {"intent": "BUILD_STRUCTURE", "structure": "Bunker", "location": "main ramp"},
                build_state(),
            ),
            ("expand", payload_for("EXPAND"), build_state()),
        )
        for label, payload, state in cases:
            with self.subTest(case=label):
                result = validate_sc2_feasibility(payload, state)
                self.assertTrue(result.executable)
                for check_name in (
                    "minerals",
                    "vespene",
                    "tech_requirements",
                    "worker_availability",
                ):
                    self.assertIn(check_name, result.checked)

    def test_unknown_structure_rejected(self) -> None:
        result = validate_sc2_feasibility(
            {"intent": "BUILD_STRUCTURE", "structure": "Pylon", "location": "main"},
            build_state(),
        )
        self.assertFalse(result.executable)
        self.assertEqual(result.reason_codes, ("unsupported_structure",))
        self.assertIn("Pylon", result.reasons[0])


class UnitPresenceTest(unittest.TestCase):
    def test_unit_presence_rules(self) -> None:
        no_army_state = build_state(own_units={"SCV": 10}, army_count=0)
        no_units_state = build_state(own_units={}, army_count=0, idle_worker_count=0)
        marines_only_state = build_state(own_units={"MARINE": 4})
        cases = (
            ("defend without army", payload_for("DEFEND"), no_army_state, "no_units"),
            ("harass without army", payload_for("HARASS"), no_army_state, "no_units"),
            ("scout with no units", payload_for("SCOUT"), no_units_state, "no_units"),
            ("repair without scv", payload_for("REPAIR"), marines_only_state, "no_workers"),
            ("gather without scv", payload_for("GATHER_RESOURCE"), marines_only_state, "no_workers"),
            ("defend with marines", payload_for("DEFEND"), build_state(), None),
            ("harass with marines", payload_for("HARASS"), build_state(), None),
            ("scout with scv only", payload_for("SCOUT"), no_army_state, None),
            ("repair with scv", payload_for("REPAIR"), build_state(), None),
            ("gather with scv", payload_for("GATHER_RESOURCE"), build_state(), None),
        )
        for label, payload, state, expected_code in cases:
            with self.subTest(case=label):
                result = validate_sc2_feasibility(payload, state)
                if expected_code is None:
                    self.assertTrue(result.executable)
                    self.assertEqual(result.reason_codes, ())
                else:
                    self.assertFalse(result.executable)
                    self.assertEqual(result.reason_codes, (expected_code,))
                    self.assertTrue(result.alternative.strip())

    def test_gas_gather_requires_completed_refinery(self) -> None:
        # The live game silently rejects gather orders on a bare geyser, so
        # gas gathering without a completed Refinery is rejected with a
        # Korean reason and an actionable alternative.
        gas_payload = {
            "intent": "GATHER_RESOURCE",
            "resource": "gas",
            "worker_count": 3,
            "base": "main",
        }
        with self.subTest(case="no refinery"):
            no_refinery_state = build_state(
                own_structures={"COMMANDCENTER": 1, "SUPPLYDEPOT": 2, "BARRACKS": 1},
            )
            result = validate_sc2_feasibility(gas_payload, no_refinery_state)
            self.assertFalse(result.executable)
            self.assertEqual(("missing_refinery",), result.reason_codes)
            self.assertIn("정제소", result.reasons[0])
            self.assertIn("정제소", result.alternative)
        with self.subTest(case="refinery present"):
            result = validate_sc2_feasibility(gas_payload, build_state())
            self.assertTrue(result.executable)
            self.assertIn("refinery_presence", result.checked)
        with self.subTest(case="minerals never need refinery"):
            no_refinery_state = build_state(
                own_structures={"COMMANDCENTER": 1},
            )
            result = validate_sc2_feasibility(
                payload_for("GATHER_RESOURCE"),
                no_refinery_state,
            )
            self.assertTrue(result.executable)

    def test_gather_capped_request_stays_executable(self) -> None:
        result = validate_sc2_feasibility(
            {
                "intent": "GATHER_RESOURCE",
                "resource": "minerals",
                "worker_count": 99,
                "base": "main",
            },
            build_state(),
        )
        self.assertTrue(result.executable)
        self.assertEqual(result.reason_codes, ())
        self.assertEqual(result.reasons, ())
        self.assertIn("worker_count_capped", result.checked)


class ValidatorBoundaryTest(unittest.TestCase):
    def test_default_validator_satisfies_protocol(self) -> None:
        self.assertIsInstance(
            DEFAULT_SC2_FEASIBILITY_VALIDATOR,
            SC2FeasibilityValidatorInterface,
        )
        self.assertIsInstance(SC2FeasibilityValidator(), SC2FeasibilityValidatorInterface)

    def test_attribute_object_payloads_match_mapping_payloads(self) -> None:
        state = build_state(minerals=40)
        mapping_result = validate_sc2_feasibility(payload_for("TRAIN_WORKER"), state)
        attribute_result = validate_sc2_feasibility(
            SimpleNamespace(intent="TRAIN_WORKER", count=1),
            state,
        )
        self.assertEqual(attribute_result.to_dict(), mapping_result.to_dict())

    def test_all_emitted_reason_codes_are_registered(self) -> None:
        state_cases = (
            (payload_for("TRAIN_WORKER"), None),
            (payload_for("TRAIN_WORKER"), build_state(observation_notes=("note",))),
            (payload_for("TRAIN_WORKER"), build_state(minerals=0, vespene=0, supply_left=0)),
            ({"intent": "DANCE"}, build_state()),
            (payload_for("DEFEND"), build_state(own_units={"SCV": 1}, army_count=0)),
            (payload_for("BUILD_STRUCTURE"), build_state(own_units={})),
        )
        for index, (payload, state) in enumerate(state_cases):
            with self.subTest(case=index):
                result = validate_sc2_feasibility(payload, state)
                self.assertFalse(result.executable)
                for code in result.reason_codes:
                    self.assertIn(code, SC2_FEASIBILITY_REASON_CODES)

    def test_multiple_shortfalls_report_every_reason(self) -> None:
        result = validate_sc2_feasibility(
            {"intent": "TRAIN_ARMY", "unit_type": "Marine", "count": 3},
            build_state(
                minerals=0,
                supply_used=31,
                supply_left=0,
                own_structures={"COMMANDCENTER": 1},
            ),
        )
        self.assertFalse(result.executable)
        self.assertEqual(
            result.reason_codes,
            ("insufficient_minerals", "insufficient_supply", "missing_producer"),
        )
        self.assertEqual(len(result.reasons), 3)
        self.assertTrue(result.alternative.strip())
        json.dumps(result.to_dict(), ensure_ascii=False)

    def test_module_source_stays_free_of_optional_runtimes(self) -> None:
        with open(feasibility_module.__file__, encoding="utf-8") as source_file:
            source = source_file.read()
        for forbidden in ("toycraft", "import sc2\n", "from sc2", "faster_whisper", "sounddevice"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
