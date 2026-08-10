from pathlib import Path
import re
import unittest

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "final-pre-live.yml"
EXPECTED_JOBS = {
    "build_identity",
    "deterministic_journeys",
    "browser_accessibility",
    "distribution_compliance",
    "pre_live_provenance",
    "seal_child_artifacts",
    "final_release_gate",
}
PRODUCER_JOBS = {
    "build_identity",
    "deterministic_journeys",
    "browser_accessibility",
    "distribution_compliance",
    "pre_live_provenance",
}
EXPECTED_CHILD_ARTIFACTS = {
    "micromachine-build-identity",
    "micromachine-deterministic-journeys",
    "micromachine-browser-accessibility",
    "micromachine-distribution-compliance",
    "micromachine-pre-live-provenance",
}


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def construct_unique_mapping(
    loader: UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    construct_unique_mapping,
)


class FinalPreLiveWorkflowContractTests(unittest.TestCase):
    def test_exact_jobs_and_read_only_permissions(self) -> None:
        workflow = self.workflow()

        self.assertEqual(EXPECTED_JOBS, set(workflow["jobs"]))
        self.assertEqual(
            {
                "actions": "read",
                "contents": "read",
                "issues": "read",
                "pull-requests": "read",
            },
            workflow["permissions"],
        )
        for name, job in workflow["jobs"].items():
            with self.subTest(job=name):
                for permission in job.get("permissions", {}).values():
                    self.assertEqual("read", permission)

    def test_trusted_pr_target_events_and_exact_main_push(self) -> None:
        workflow = self.workflow()
        trigger = workflow.get("on", workflow.get(True))

        self.assertNotIn("pull_request", trigger)
        self.assertEqual(["main"], trigger["pull_request_target"]["branches"])
        self.assertEqual(
            {
                "converted_to_draft",
                "edited",
                "opened",
                "ready_for_review",
                "reopened",
                "synchronize",
            },
            set(trigger["pull_request_target"]["types"]),
        )
        self.assertEqual(["main"], trigger["push"]["branches"])

    def test_admission_fails_foreign_repository_without_job_skip(self) -> None:
        workflow = self.workflow()
        build = workflow["jobs"]["build_identity"]
        boundary = self.step(
            build,
            "Verify event and workflow authority boundary",
        )["run"]

        self.assertNotIn("if", build)
        self.assertIn(
            'test "${EXPECTED_HEAD_REPOSITORY}" = "${GITHUB_REPOSITORY}"',
            boundary,
        )
        self.assertIn("pull_request_target)", boundary)
        self.assertIn(
            'test "${GITHUB_WORKFLOW_SHA}" = "${EXPECTED_WORKFLOW_COMMIT}"',
            boundary,
        )
        self.assertIn("trusted_base_candidate_preflight", boundary)
        self.assertIn("authoritative_exact_main", boundary)

    def test_candidate_and_trusted_checkouts_are_separated(self) -> None:
        workflow = self.workflow()

        for name in {
            "build_identity",
            "deterministic_journeys",
            "browser_accessibility",
            "distribution_compliance",
            "seal_child_artifacts",
        }:
            with self.subTest(job=name):
                checkouts = self.checkouts(workflow["jobs"][name])
                self.assertEqual(1, len(checkouts))
                self.assertEqual("candidate", checkouts[0]["with"]["path"])
                self.assertEqual(
                    "${{ env.EXPECTED_RELEASE_COMMIT }}",
                    checkouts[0]["with"]["ref"],
                )

        for name in {"pre_live_provenance", "final_release_gate"}:
            with self.subTest(job=name):
                checkouts = self.checkouts(workflow["jobs"][name])
                self.assertEqual(2, len(checkouts))
                refs = {
                    checkout["with"]["path"]: checkout["with"]["ref"]
                    for checkout in checkouts
                }
                self.assertEqual(
                    {
                        "candidate": "${{ env.EXPECTED_RELEASE_COMMIT }}",
                        "trusted-verifier": (
                            "${{ env.EXPECTED_WORKFLOW_COMMIT }}"
                        ),
                    },
                    refs,
                )

        for job in workflow["jobs"].values():
            for checkout in self.checkouts(job):
                self.assertRegex(
                    checkout["uses"],
                    r"^actions/checkout@[0-9a-f]{40}$",
                )
                self.assertEqual(0, checkout["with"]["fetch-depth"])
                self.assertFalse(checkout["with"]["persist-credentials"])

    def test_candidate_execution_never_receives_github_token(self) -> None:
        workflow = self.workflow()
        candidate_jobs = {
            name: workflow["jobs"][name]
            for name in PRODUCER_JOBS
        }

        for name, job in candidate_jobs.items():
            source = yaml.safe_dump(job, sort_keys=True)
            with self.subTest(job=name):
                self.assertNotRegex(
                    source,
                    re.compile(
                        r"(?m)^\s*(?:GITHUB_TOKEN|GH_TOKEN)\s*:",
                    ),
                )
                self.assertNotRegex(
                    source,
                    re.compile(
                        r"(?m)(?:^|[\s;])(?:export\s+)?"
                        r"(?:GITHUB_TOKEN|GH_TOKEN)=",
                    ),
                )
                self.assertNotIn("${{ github.token }}", source)

        final = workflow["jobs"]["final_release_gate"]
        generate = self.step(
            final,
            "Generate authenticated final release readiness",
        )
        self.assertEqual("${{ github.token }}", generate["env"]["GITHUB_TOKEN"])
        self.assertIn(
            "${TRUSTED_VERIFIER_ROOT}/starcraft_commander/"
            "micromachine_final_release.py",
            generate["run"],
        )

    def test_final_aggregator_converts_all_non_success_results_to_failure(
        self,
    ) -> None:
        final = self.workflow()["jobs"]["final_release_gate"]
        guard = self.step(final, "Enforce all prerequisite results")

        self.assertEqual("always()", final["if"])
        self.assertEqual(
            {
                "build_identity",
                "deterministic_journeys",
                "browser_accessibility",
                "distribution_compliance",
                "pre_live_provenance",
                "seal_child_artifacts",
            },
            set(final["needs"]),
        )
        self.assertEqual("${{ toJSON(needs) }}", final["env"]["PREREQUISITE_RESULTS"])
        self.assertIn('value.get("result") != "success"', guard["run"])
        self.assertIn("raise SystemExit", guard["run"])

    def test_all_actions_are_immutable_sha_pinned(self) -> None:
        workflow = self.workflow()
        action_uses = [
            step["uses"]
            for job in workflow["jobs"].values()
            for step in job["steps"]
            if "uses" in step
        ]

        self.assertEqual(27, len(action_uses))
        for action in action_uses:
            with self.subTest(action=action):
                self.assertRegex(action, r"^[^@\s]+@[0-9a-f]{40}$")

    def test_browser_gate_is_real_and_non_skippable(self) -> None:
        browser = self.workflow()["jobs"]["browser_accessibility"]
        install = self.step(browser, "Install pinned browser gate environment")
        gate = self.step(
            browser,
            "Run non-skippable browser and accessibility gate",
        )
        upload = self.step(
            browser,
            "Upload browser and accessibility child artifact",
        )

        self.assertNotIn("continue-on-error", install)
        self.assertNotIn("continue-on-error", gate)
        self.assertIn(
            ".venv/bin/playwright install --with-deps chromium",
            install["run"],
        )
        self.assertIn(
            "starcraft_commander.battlefield_browser_gate",
            gate["run"],
        )
        self.assertEqual("error", upload["with"]["if-no-files-found"])

    def test_exact_child_ids_and_digests_are_sealed(self) -> None:
        workflow = self.workflow()
        observed_names = {
            step["with"]["name"]
            for job in workflow["jobs"].values()
            for step in job["steps"]
            if str(step.get("uses", "")).startswith(
                "actions/upload-artifact@"
            )
            and step.get("with", {}).get("name") in EXPECTED_CHILD_ARTIFACTS
        }
        seal = workflow["jobs"]["seal_child_artifacts"]
        seal_source = yaml.safe_dump(seal, sort_keys=True)
        downloads = [
            step
            for step in seal["steps"]
            if str(step.get("uses", "")).startswith(
                "actions/download-artifact@"
            )
        ]

        self.assertEqual(EXPECTED_CHILD_ARTIFACTS, observed_names)
        self.assertEqual(5, len(downloads))
        for name in PRODUCER_JOBS:
            outputs = workflow["jobs"][name]["outputs"]
            self.assertIn("artifact_id", outputs)
            self.assertIn("artifact_digest", outputs)
        for download in downloads:
            self.assertIn("artifact-ids", download["with"])
            self.assertNotIn("name", download["with"])
        self.assertIn("archive_sha256", seal_source)
        self.assertIn("ARTIFACT_DIGEST", seal_source)

    def test_final_gate_uses_sealed_artifact_and_enforces_verdict(self) -> None:
        final = self.workflow()["jobs"]["final_release_gate"]
        download = self.step(final, "Download sealed child artifact tree")
        generate = self.step(
            final,
            "Generate authenticated final release readiness",
        )
        enforce = self.step(final, "Enforce final release verdict")

        self.assertIn("artifact-ids", download["with"])
        self.assertTrue(generate["continue-on-error"])
        self.assertIn("--workflow-sha", generate["run"])
        self.assertIn("--workflow-run-id", generate["run"])
        self.assertIn("--run-attempt", generate["run"])
        self.assertEqual(
            "steps.release-gate.outcome != 'success'",
            enforce["if"],
        )
        self.assertIn("exit 1", enforce["run"])

    def test_workflow_does_not_embed_private_provider_configuration(self) -> None:
        source = WORKFLOW_PATH.read_text(encoding="utf-8")

        for forbidden in (
            "MYPROXY",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "api.openai.com",
            "api.anthropic.com",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        self.assertNotRegex(
            source,
            re.compile(r"permissions:\s*\n(?:.*\n)*?\s+write"),
        )

    @staticmethod
    def workflow() -> dict[str, object]:
        return yaml.load(
            WORKFLOW_PATH.read_text(encoding="utf-8"),
            Loader=UniqueKeyLoader,
        )

    @staticmethod
    def step(job: dict[str, object], name: str) -> dict[str, object]:
        return next(step for step in job["steps"] if step.get("name") == name)

    @staticmethod
    def checkouts(job: dict[str, object]) -> list[dict[str, object]]:
        return [
            step
            for step in job["steps"]
            if str(step.get("uses", "")).startswith("actions/checkout@")
        ]


if __name__ == "__main__":
    unittest.main()
