"""Exercise compact provider responses through the final live compiler."""

import json
import types
import unittest

from starcraft_commander.llm_interpreter import (
    LLMCommandInterpreter,
    LLM_POLICY_MODULATION_TOOL_NAME,
    build_compact_policy_modulation_system_prompt,
)
from starcraft_commander.policy_modulation_provider import (
    PolicyModulationProviderRequest,
    compile_policy_modulation_from_provider,
)
from starcraft_commander.policy_modulation import MICROMACHINE_DIRECT_TASK_TYPES
from starcraft_commander.unified_command_router import route_policy_vector


class CompactLiveCommandBoundaryTests(unittest.TestCase):
    def compile_response(self, payload, text):
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return {
                "output": [{
                    "type": "function_call",
                    "name": LLM_POLICY_MODULATION_TOOL_NAME,
                    "arguments": json.dumps(payload),
                }],
            }

        client = types.SimpleNamespace(
            responses=types.SimpleNamespace(create=create),
        )
        interpreter = LLMCommandInterpreter(
            provider="myproxy",
            model="fixture-model",
            client_factory=lambda: client,
        )
        result = compile_policy_modulation_from_provider(
            interpreter,
            PolicyModulationProviderRequest(command_text=text),
        )
        return result, calls

    def test_terminal_responses_preserve_question_or_refusal_and_diagnostics(self):
        for status, field, message in (
            ("clarification_required", "clarification_prompt", "Which structure?"),
            ("refused", "refusal_reason", "Unsupported target."),
        ):
            with self.subTest(status=status):
                result, calls = self.compile_response({
                    "status": status,
                    "assistant_message": message,
                    "clarification_prompt": None,
                    "refusal_reason": None,
                    "command": None,
                    "commands": None,
                    field: message,
                }, "가스터빈 지어")
                self.assertEqual(status, result.status.value, result.to_dict())
                self.assertEqual(message, getattr(result, field))
                self.assertEqual(message, result.assistant_message)
                self.assertEqual(1, result.llm_attempt_count)
                self.assertEqual(1, len(calls))
                self.assertIsNone(result.vector)

    def test_refinery_aliases_lower_to_macro_building_task(self):
        for alias in ("정제소", "가스터빈", "가스 터빈", "refinery"):
            with self.subTest(alias=alias):
                result, calls = self.compile_response({
                    "status": "compiled",
                    "assistant_message": "정제소 건설을 요청합니다.",
                    "command": {
                        "goal": "정제소 건설",
                        "command_layer": "macro",
                        "task_type": "tech_transition",
                        "operation_action": None,
                        "building_tasks": [{
                            "building_type": alias,
                            "count": 1,
                            "anchor": "self_main",
                        }],
                    },
                }, f"{alias} 지어")
                self.assertTrue(result.ok, result.to_dict())
                self.assertEqual("macro", result.vector.command_layer.value)
                self.assertEqual(
                    "TERRAN_REFINERY", result.vector.building_tasks[0].building_type,
                )
                self.assertIn("TERRAN_REFINERY", result.vector.production_plan.targets)
                self.assertEqual((), result.vector.operations)
                self.assertEqual(1, len(calls))

    def test_production_counts_remain_macro_without_squad_operations(self):
        for text, unit, count, canonical in (
            ("SCV 생산해", "scv", 1, "TERRAN_SCV"),
            ("마린 6기 생산해", "marine", 6, "TERRAN_MARINE"),
        ):
            with self.subTest(text=text):
                result, calls = self.compile_response({
                    "status": "compiled",
                    "assistant_message": "생산을 요청합니다.",
                    "command": {
                        "goal": text,
                        "command_layer": "macro",
                        "operation_action": None,
                        "task_type": "sustain_production",
                        "unit_requests": [{"unit_type": unit, "count": count}],
                    },
                }, text)
                self.assertTrue(result.ok, result.to_dict())
                self.assertEqual("macro", result.vector.command_layer.value)
                self.assertEqual(canonical, result.vector.composition_requirements[0].unit_type)
                self.assertEqual(count, result.vector.composition_requirements[0].count)
                self.assertEqual((), result.vector.operations)
                self.assertEqual(1, len(calls))

    def test_raw_controls_still_fail_before_lowering(self):
        for key in ("raw_command", "attack_move", "unit_tags", "commands"):
            with self.subTest(key=key):
                result, _ = self.compile_response({
                    "status": "compiled",
                    "assistant_message": "생산을 요청합니다.",
                    "command": {
                        "goal": "SCV 생산",
                        "command_layer": "macro",
                        "task_type": "sustain_production",
                        "production_targets": ["scv"],
                        key: "unsafe",
                    },
                }, "SCV 생산해")
                self.assertFalse(result.ok)
                self.assertEqual("contract_error", result.failure_kind)
                self.assertIsNone(result.vector)

    def test_terminal_response_cannot_hide_non_null_command(self):
        result, _ = self.compile_response({
            "status": "refused",
            "assistant_message": "No.",
            "refusal_reason": "No.",
            "command": {
                "goal": "SCV",
                "command_layer": "macro",
                "task_type": "sustain_production",
                "production_targets": ["scv"],
            },
        }, "SCV 생산해")
        self.assertEqual("contract_error", result.failure_kind)
        self.assertIsNone(result.vector)

    def test_prompt_distinguishes_production_from_operation_editing(self):
        prompt = build_compact_policy_modulation_system_prompt()
        self.assertIn("intermediate", prompt)
        self.assertIn("For macro and micro, set operation_action=null", prompt)
        self.assertIn("가스터빈", prompt)

    def test_direct_tickets_lower_through_fake_provider(self):
        cases = (
            (
                "repair",
                {"target": "front bunker", "count": 2,
                 "unit_requests": [{"unit_type": "scv", "count": 2}]},
                "repair", "front bunker", 2,
            ),
            (
                "assign_workers",
                {"target": "gas", "count": 3,
                 "unit_requests": [{"unit_type": "scv", "count": 3}]},
                "assign_workers", "gas", 3,
            ),
            (
                "gather_resource",
                {"target": "minerals", "count": 4,
                 "unit_requests": [{"unit_type": "scv", "count": 4}]},
                "gather_resource", "minerals", 4,
            ),
            (
                "build_structure",
                {"production_targets": ["TERRAN_BARRACKS"],
                 "location_intent": "home", "count": 1},
                "build_structure", "", 1,
            ),
            (
                "train_unit",
                {"production_targets": ["TERRAN_MARINE"], "count": 6},
                "train_unit", "", 6,
            ),
        )
        for task_type, fields, expected_type, expected_target, expected_count in cases:
            with self.subTest(task_type=task_type):
                command = {
                    "goal": task_type,
                    "command_layer": "operation",
                    "task_type": task_type,
                    "operation_action": None,
                    "target": None,
                    "count": None,
                    "production_targets": [],
                    "unit_requests": [],
                    "location_intent": None,
                    **fields,
                }
                result, _ = self.compile_response(
                    {"status": "compiled", "assistant_message": "요청을 처리합니다.",
                     "command": command},
                    task_type,
                )
                self.assertTrue(result.ok, result.to_dict())
                self.assertEqual("operation", result.vector.command_layer.value)
                self.assertEqual(expected_type, result.vector.tactical_task.task_type)
                self.assertEqual(expected_target, result.vector.tactical_task.target)
                if task_type == "build_structure":
                    self.assertEqual("home", result.vector.tactical_task.location_intent)
                self.assertEqual(expected_count, result.vector.tactical_task.min_units)

    def test_all_bounded_direct_tickets_reach_router_from_compact_schema(self):
        """Every semantic direct task survives provider parsing and lowering."""

        cases = {
            "assign_workers": {
                "target": "gas", "unit_requests": [{"unit_type": "scv", "count": 3}],
            },
            "gather_resource": {
                "target": "minerals", "unit_requests": [{"unit_type": "scv", "count": 4}],
            },
            "build_structure": {
                "production_targets": ["TERRAN_BARRACKS"], "location_intent": "home",
            },
            "train_unit": {"production_targets": ["TERRAN_MARINE"], "count": 6},
            "research_upgrade": {
                "production_targets": ["TERRAN_INFANTRYWEAPONSLEVEL1"],
            },
            "warp_in": {
                "production_targets": ["PROTOSS_ZEALOT"], "location_intent": "home",
            },
            "move_group": {
                "unit_requests": [{"unit_type": "marine", "count": 2}],
                "location_intent": "enemy_natural",
            },
            "attack_move": {
                "unit_requests": [{"unit_type": "marine", "count": 2}],
                "location_intent": "enemy_natural",
            },
            "smart": {
                "unit_requests": [{"unit_type": "marine", "count": 2}],
                "location_intent": "enemy_natural",
            },
            "patrol": {
                "unit_requests": [{"unit_type": "marine", "count": 2}],
                "location_intent": "enemy_natural",
            },
            "return_resource": {
                "unit_requests": [{"unit_type": "scv", "count": 2}],
            },
            "repair": {
                "target": "front bunker",
                "unit_requests": [{"unit_type": "scv", "count": 2}],
            },
            "observe": {},
            "move_camera": {"location_intent": "enemy_natural"},
            "stop_group": {
                "unit_requests": [{"unit_type": "marine", "count": 2}],
            },
            "hold_position": {
                "unit_requests": [{"unit_type": "marine", "count": 2}],
            },
            "query_ability": {
                "ability": "marine_stimpack",
                "unit_requests": [{"unit_type": "marine", "count": 2}],
            },
            "query_placement": {
                "production_targets": ["TERRAN_BARRACKS"], "location_intent": "home",
            },
            "query_unit": {
                "query": "calculate_speed",
                "unit_requests": [{"unit_type": "marine", "count": 1}],
            },
            "query_game": {
                "query": "can_afford",
                "production_targets": ["TERRAN_MARINE"],
            },
            "query_map": {
                "query": "in_pathing_grid",
                "location_intent": "home",
            },
            "build_gas": {
                "location_intent": "self_geyser",
                "unit_requests": [{"unit_type": "scv", "count": 1}],
            },
            "distribute_workers": {},
            "expand_now": {"location_intent": "safe_expansion"},
        }
        self.assertEqual(
            set(cases), MICROMACHINE_DIRECT_TASK_TYPES - {"execute_ability"},
        )
        for task_type, fields in cases.items():
            with self.subTest(task_type=task_type):
                layer = "emergency" if task_type in {"stop_group", "hold_position"} else "operation"
                command = {
                    "goal": task_type,
                    "command_layer": layer,
                    "task_type": task_type,
                    "operation_action": None,
                    "target": None,
                    "count": None,
                    "production_targets": [],
                    "unit_requests": [],
                    "location_intent": None,
                    **fields,
                }
                result, _ = self.compile_response(
                    {
                        "status": "compiled",
                        "assistant_message": "요청을 처리합니다.",
                        "command": command,
                    },
                    task_type,
                )
                self.assertTrue(result.ok, result.to_dict())
                self.assertIsNotNone(result.vector)
                route = route_policy_vector(result.vector)
                self.assertEqual(["sc2.direct.execute"], [call.name for call in route.tool_calls])
                self.assertEqual(task_type, result.vector.tactical_task.task_type)

    def test_compact_prompt_names_the_bounded_direct_surface(self):
        prompt = build_compact_policy_modulation_system_prompt()
        for task_type in MICROMACHINE_DIRECT_TASK_TYPES:
            with self.subTest(task_type=task_type):
                self.assertIn(task_type, prompt)

    def test_compact_execute_ability_reaches_micro_direct_router(self):
        result, _ = self.compile_response(
            {
                "status": "compiled",
                "assistant_message": "능력을 사용합니다.",
                "command": {
                    "goal": "마린 자극제 사용",
                    "command_layer": "micro",
                    "task_type": "execute_ability",
                    "operation_action": None,
                    "target": None,
                    "count": 2,
                    "production_targets": [],
                    "unit_requests": [{"unit_type": "marine", "count": 2}],
                    "location_intent": "enemy_natural",
                    "ability": "marine_stimpack",
                },
            },
            "마린 자극제 사용",
        )
        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.vector)
        self.assertEqual("micro", result.vector.command_layer.value)
        route = route_policy_vector(result.vector)
        self.assertEqual(["sc2.direct.execute"], [call.name for call in route.tool_calls])
        self.assertEqual("execute_ability", result.vector.tactical_task.task_type)


if __name__ == "__main__":
    unittest.main()
