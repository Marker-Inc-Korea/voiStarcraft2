import json
import subprocess
import sys
import unittest
from pathlib import Path

from toycraft_commander.demo import (
    DEFAULT_KOREAN_DEMO_COMMANDS,
    KoreanDemoCommand,
    build_demo_initial_state,
    run_korean_demo,
)
from toycraft_commander.feasibility import ToyCraftState
from toycraft_commander.intents import CANONICAL_INTENT_NAMES, INTENT_DSL_FORMAT_VERSION
from toycraft_commander.interpreter import KOREAN_COMMAND_TEST_CORPUS


class KoreanCommanderDemoTest(unittest.TestCase):
    def test_default_demo_commands_are_korean_rts_actions(self) -> None:
        command_texts = tuple(command.command_text for command in DEFAULT_KOREAN_DEMO_COMMANDS)

        self.assertGreaterEqual(len(command_texts), 8)
        self.assertTrue(all(_contains_korean(text) for text in command_texts))
        self.assertIn("미네랄에 일꾼 세 기 붙여", command_texts)
        self.assertIn("일꾼 계속 찍어", command_texts)
        self.assertIn("마린 계속 뽑아", command_texts)
        self.assertIn("입구 막아", command_texts)
        self.assertIn("마린 두 기로 적 미네랄 라인 견제해", command_texts)

    def test_demo_initial_state_supports_actionable_walkthrough(self) -> None:
        state = build_demo_initial_state()

        self.assertIsInstance(state, ToyCraftState)
        self.assertGreaterEqual(state.resources.minerals, 900)
        self.assertGreaterEqual(state.resources.gas, 100)
        self.assertGreaterEqual(state.unit_count("SCV"), 10)
        self.assertGreaterEqual(state.unit_count("Marine"), 6)
        self.assertGreaterEqual(state.structure_count("Barracks"), 1)
        self.assertGreaterEqual(state.structure_count("Refinery"), 1)
        self.assertIn("front bunker", state.damaged_targets)

    def test_run_korean_demo_emits_dsl_execution_narration_and_time_progress(self) -> None:
        transcript = run_korean_demo()

        self.assertIn("ToyCraft Commander Phase 0 Korean Demo", transcript)
        self.assertIn("Commander: 미네랄에 일꾼 세 기 붙여", transcript)
        self.assertIn('"command_text": "미네랄에 일꾼 세 기 붙여"', transcript)
        self.assertIn('"intent_dsl": {', transcript)
        self.assertIn('"intent": "GATHER_RESOURCE"', transcript)
        self.assertIn('"intent": "TRAIN_WORKER"', transcript)
        self.assertIn('"intent": "DEFEND"', transcript)
        self.assertIn('"intent": "HARASS"', transcript)
        self.assertIn("- executed: True", transcript)
        self.assertIn("ToyCraft time +20s", transcript)
        self.assertIn("ToyCraft time +30s", transcript)
        self.assertIn("Final ToyCraft State", transcript)
        self.assertIn("claimed_locations", transcript)

    def test_every_supported_korean_command_displays_corresponding_intent_dsl(
        self,
    ) -> None:
        displayed_intents = set()

        self.assertEqual(20, len(KOREAN_COMMAND_TEST_CORPUS))
        for corpus_row in KOREAN_COMMAND_TEST_CORPUS:
            command_text = corpus_row["command_text"]
            expected_dsl = corpus_row["expected_dsl"]

            with self.subTest(command_text=command_text):
                transcript = run_korean_demo(
                    commands=(KoreanDemoCommand(command_text),),
                    initial_state=build_demo_initial_state(),
                )
                displayed_document = _extract_first_displayed_dsl_document(transcript)

                self.assertIn(f"Commander: {command_text}", transcript)
                self.assertEqual(INTENT_DSL_FORMAT_VERSION, displayed_document["format"])
                self.assertEqual(command_text, displayed_document["command_text"])
                self.assertEqual(expected_dsl, displayed_document["intent_dsl"])
                self.assertEqual([], displayed_document["entity_references"])
                self.assertIn("- executed:", transcript)
                self.assertNotIn("- status: blocked_before_validation", transcript)
                displayed_intents.add(expected_dsl["intent"])

        self.assertEqual(set(CANONICAL_INTENT_NAMES), displayed_intents)

    def test_demo_module_is_runnable_from_command_line(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            [sys.executable, "-m", "toycraft_commander.demo"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn("Commander: 상태 알려줘", completed.stdout)
        self.assertIn('"intent": "SUMMARIZE_STATE"', completed.stdout)
        self.assertIn("Final ToyCraft State", completed.stdout)

    def test_demo_command_rejects_invalid_step_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "command_text"):
            KoreanDemoCommand(" ")
        with self.assertRaisesRegex(ValueError, "advance_seconds_after"):
            KoreanDemoCommand("상태 알려줘", advance_seconds_after=-1)


def _contains_korean(text: str) -> bool:
    return any("가" <= character <= "힣" for character in text)


def _extract_first_displayed_dsl_document(transcript: str) -> dict[str, object]:
    marker = "- Intent DSL:\n"
    start = transcript.index(marker) + len(marker)
    end = transcript.index("\n- executed:", start)
    json_block = "\n".join(
        line[2:] if line.startswith("  ") else line
        for line in transcript[start:end].splitlines()
    )
    return json.loads(json_block)


if __name__ == "__main__":
    unittest.main()
