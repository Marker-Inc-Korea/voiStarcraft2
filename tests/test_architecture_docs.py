from pathlib import Path
import unittest

from starcraft_commander.contracts import SC2_ACTION_TYPES
from toycraft_commander.intents import CANONICAL_INTENT_NAMES


class ArchitectureDocumentationTest(unittest.TestCase):
    def test_phase_zero_architecture_doc_names_boundaries_and_scope_guard(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        document = (repo_root / "docs" / "architecture.md").read_text()

        required_terms = (
            "CommandInterpreter",
            "typed Intent DSL",
            "IntentFeasibilityValidator",
            "ToyCraftExecutorInterface",
            "ToyCraftRuleEngine",
            "StateNarrator",
            "Command pipeline",
            "SC2 Readiness Boundary",
            "Phase 0 does not implement SC2",
            "exactly these 11 canonical intents",
        )

        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, document)

    def test_readme_links_to_architecture_document(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        readme = (repo_root / "README.md").read_text()

        self.assertIn("[docs/architecture.md](docs/architecture.md)", readme)
        self.assertIn("executor abstraction", readme)

    def test_architecture_doc_traces_end_to_end_command_data_flow(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        document = (repo_root / "docs" / "architecture.md").read_text()

        required_terms = (
            "End-to-End Command Data Flow",
            "Korean input",
            "CommandProcessingRequest.command_text",
            "IntentPayload",
            "common fields `intent`, `priority`, and `constraints`",
            "IntentFeasibilityValidator.validate_intent",
            "IntentValidationResult",
            "ToyCraftExecutorInterface.apply_effects",
            "ToyCraftRuleEngine",
            "ToyCraftExecutionResult",
            "StateNarrator",
            "CommandProcessingResponse",
            "before_state == after_state",
            "reason plus an",
            "alternative",
        )

        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, document)

    def test_readme_and_architecture_link_to_contract_document(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        readme = (repo_root / "README.md").read_text()
        architecture = (repo_root / "docs" / "architecture.md").read_text()

        self.assertIn("[docs/contracts.md](docs/contracts.md)", readme)
        self.assertIn("[contracts.md](contracts.md)", architecture)

    def test_contract_doc_documents_intent_dsl_and_engine_interfaces(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        document = (repo_root / "docs" / "contracts.md").read_text()

        required_terms = (
            "Phase 0 Interface Contracts",
            "typed Intent DSL",
            "toycraft.intent_dsl.v1",
            "IntentPayload",
            "IntentFeasibilityValidator",
            "validate_intent",
            "IntentValidationResult",
            "FeasibilityIssue",
            "FeasibilityErrorReason",
            "ToyCraftExecutorInterface",
            "apply_effects",
            "ToyCraftRuleEngineInterface",
            "execute_intent",
            "ToyCraftExecutionResult",
            "executed_actions",
            "state_delta",
            "before_state == after_state",
            "reason plus alternative",
            "Phase 0 does not implement SC2",
        )

        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, document)

    def test_contract_doc_names_all_canonical_intents(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        document = (repo_root / "docs" / "contracts.md").read_text()

        self.assertIn("exactly 11 canonical Phase 0 intent names", document)
        for intent_name in CANONICAL_INTENT_NAMES:
            with self.subTest(intent=intent_name):
                self.assertIn(f"`{intent_name}`", document)

    def test_contract_doc_defines_public_sc2_action_type_names(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        document = (repo_root / "docs" / "contracts.md").read_text()

        required_terms = (
            "SC2 Command Plan Contract",
            "stable public SC2 action type name set",
            "semantic action categories",
            "not depend on python-sc2 method names",
            "mouse-click automation",
        )
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, document)

        for action_type in SC2_ACTION_TYPES:
            with self.subTest(action_type=action_type):
                self.assertIn(f"`{action_type}`", document)

    def test_issue_10_micro_machine_adoption_doc_names_candidate_boundaries(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        document = (repo_root / "docs" / "micromachine-adoption.md").read_text()
        issue_doc = (repo_root / "docs" / "sc2-collaboration-policy-tree.md").read_text()

        required_terms = (
            "MicroMachine is the only practical adoption candidate",
            "Deimos",
            "Eris",
            "CommandCenter",
            "python-sc2",
            "StrategyManager",
            "ProductionManager",
            "CombatCommander",
            "CombatAnalyzer",
            "SquadOrder",
            "ScoutManager",
            "WorkerManager",
            "libvoxelbot",
            "deep policy modulation DSL",
            "must not directly command SC2 units",
        )

        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, document)

        self.assertIn("[micromachine-adoption.md](micromachine-adoption.md)", issue_doc)


if __name__ == "__main__":
    unittest.main()
