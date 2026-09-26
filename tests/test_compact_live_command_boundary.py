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


if __name__ == "__main__":
    unittest.main()
