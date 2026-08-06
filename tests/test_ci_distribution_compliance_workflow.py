from pathlib import Path
import unittest

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"


class DistributionComplianceWorkflowContractTests(unittest.TestCase):
    def test_failed_evidence_and_qualified_release_uploads_are_separate(self) -> None:
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text())
        steps = workflow["jobs"]["distribution-compliance"]["steps"]
        upload_steps = {
            step["with"]["name"]: (index, step)
            for index, step in enumerate(steps)
            if str(step.get("uses", "")).startswith("actions/upload-artifact@")
        }

        self.assertEqual(
            {
                "distribution-compliance-failed-evidence",
                "distribution-compliance-qualified-release",
            },
            set(upload_steps),
        )

        failed_index, failed_upload = upload_steps[
            "distribution-compliance-failed-evidence"
        ]
        qualified_index, qualified_upload = upload_steps[
            "distribution-compliance-qualified-release"
        ]
        clean_tree_index = next(
            index
            for index, step in enumerate(steps)
            if step.get("name") == "Verify release source remained clean"
        )

        self.assertEqual("failure()", failed_upload["if"])
        self.assertEqual(
            ["distribution-compliance-evidence/"],
            self._artifact_paths(failed_upload),
        )
        self.assertEqual(
            "warn",
            failed_upload["with"]["if-no-files-found"],
        )

        self.assertEqual("success()", qualified_upload["if"])
        self.assertEqual(
            ["dist/", "distribution-compliance-evidence/"],
            self._artifact_paths(qualified_upload),
        )
        self.assertEqual(
            "error",
            qualified_upload["with"]["if-no-files-found"],
        )

        self.assertGreater(failed_index, clean_tree_index)
        self.assertGreater(qualified_index, clean_tree_index)
        self.assertNotIn(
            "always()",
            {step.get("if") for _, step in upload_steps.values()},
        )

    @staticmethod
    def _artifact_paths(step: dict[str, object]) -> list[str]:
        value = step["with"]["path"]
        return [line.strip() for line in str(value).splitlines() if line.strip()]


if __name__ == "__main__":
    unittest.main()
