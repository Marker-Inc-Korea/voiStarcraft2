import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
EXPECTED_EVIDENCE_FILES = {
    "dependency-notices.json",
    "distribution-compliance.final-projection.json",
    "distribution-compliance.final-projection.md",
    "distribution-compliance.json",
    "distribution-compliance.md",
    "installed.METADATA",
    "sdist.entries.txt",
    "secret-scan.json",
    "wheel.entries.txt",
}


class DistributionComplianceWorkflowContractTests(unittest.TestCase):
    def test_distribution_job_has_read_only_permissions_and_unit_gate(
        self,
    ) -> None:
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text())
        job = workflow["jobs"]["distribution-compliance"]

        self.assertEqual({"contents": "read"}, workflow["permissions"])
        self.assertEqual({"contents": "read"}, job["permissions"])
        self.assertEqual(["unit-contracts"], job["needs"])
        self.assertEqual(45, job["timeout-minutes"])

    def test_distribution_job_checks_out_and_verifies_exact_event_sha(self) -> None:
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text())
        steps = workflow["jobs"]["distribution-compliance"]["steps"]
        checkout_steps = [
            step
            for step in steps
            if str(step.get("uses", "")).startswith("actions/checkout@")
        ]

        self.assertEqual(2, len(checkout_steps))
        checkouts = {step["name"]: step for step in checkout_steps}
        self.assertEqual(
            {
                "Check out exact pull request head",
                "Check out exact main push",
            },
            set(checkouts),
        )

        pr_checkout = checkouts["Check out exact pull request head"]
        self.assertEqual(
            "github.event_name == 'pull_request'",
            pr_checkout["if"],
        )
        self.assertEqual(
            "${{ github.event.pull_request.head.sha }}",
            pr_checkout["with"]["ref"],
        )

        push_checkout = checkouts["Check out exact main push"]
        self.assertEqual("github.event_name == 'push'", push_checkout["if"])
        self.assertEqual("${{ github.sha }}", push_checkout["with"]["ref"])

        for checkout in checkout_steps:
            self.assertEqual(0, checkout["with"]["fetch-depth"])
            self.assertIs(False, checkout["with"]["persist-credentials"])

        verification = next(
            step
            for step in steps
            if step.get("name") == "Verify exact release source"
        )
        expected_commit = verification["env"]["EXPECTED_RELEASE_COMMIT"]
        self.assertIn("github.event.pull_request.head.sha", expected_commit)
        self.assertIn("github.sha", expected_commit)
        self.assertIn("git rev-parse HEAD", verification["run"])
        self.assertIn("EXPECTED_RELEASE_COMMIT", verification["run"])

        build = next(
            step
            for step in steps
            if step.get("name") == "Build and verify release artifacts"
        )
        expected_base = build["env"]["EXPECTED_RELEASE_BASE_COMMIT"]
        self.assertIn("github.event.pull_request.base.sha", expected_base)
        self.assertIn("github.event.before", expected_base)
        self.assertIn("--base-commit", build["run"])
        self.assertIn("EXPECTED_RELEASE_BASE_COMMIT", build["run"])

        clean_checks = [
            step
            for step in steps
            if step.get("name")
            in {
                "Verify clean release source",
                "Verify release source remained clean",
            }
        ]
        self.assertEqual(2, len(clean_checks))
        for clean_check in clean_checks:
            self.assertEqual(
                'test -z "$(git status --porcelain=v1 --untracked-files=all)"',
                clean_check["run"],
            )

    def test_failed_evidence_and_qualified_release_uploads_are_separate(self) -> None:
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text())
        steps = workflow["jobs"]["distribution-compliance"]["steps"]
        uploads = [
            (index, step)
            for index, step in enumerate(steps)
            if str(step.get("uses", "")).startswith("actions/upload-artifact@")
        ]
        upload_steps = {
            step["with"]["name"]: (index, step)
            for index, step in uploads
        }

        self.assertEqual(2, len(uploads))
        self.assertEqual(2, len(upload_steps))
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
            [
                "dist/*.whl",
                "dist/*.tar.gz",
                "distribution-compliance-evidence/",
            ],
            self._artifact_paths(qualified_upload),
        )
        self.assertNotIn("dist/", self._artifact_paths(qualified_upload))
        self.assertEqual(
            "error",
            qualified_upload["with"]["if-no-files-found"],
        )

        self.assertGreater(failed_index, clean_tree_index)
        self.assertGreater(qualified_index, clean_tree_index)
        self.assertGreater(failed_index, qualified_index)
        self.assertNotIn(
            "always()",
            {step.get("if") for _, step in upload_steps.values()},
        )

    def test_qualified_upload_has_immediate_fail_closed_integrity_gate(
        self,
    ) -> None:
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text())
        steps = workflow["jobs"]["distribution-compliance"]["steps"]
        by_name = {
            step.get("name"): (index, step)
            for index, step in enumerate(steps)
            if step.get("name")
        }
        build_index, _ = by_name["Build and verify release artifacts"]
        capture_index, capture = by_name["Capture verified upload integrity"]
        gate_index, gate = by_name["Verify qualified upload integrity"]
        qualified_index, qualified = by_name[
            "Upload qualified release artifacts and compliance evidence"
        ]

        self.assertEqual(build_index + 1, capture_index)
        self.assertEqual(gate_index + 1, qualified_index)
        self.assertEqual("capture-upload-integrity", capture["id"])
        self.assertEqual("success()", gate["if"])
        self.assertEqual("success()", qualified["if"])
        self.assertIn(
            "steps.capture-upload-integrity.outputs.manifest",
            gate["env"]["EXPECTED_UPLOAD_INTEGRITY"],
        )

        capture_run = capture["run"]
        gate_run = gate["run"]
        for filename in EXPECTED_EVIDENCE_FILES:
            self.assertIn(filename, capture_run)
            self.assertIn(filename, gate_run)
        for script in (capture_run, gate_run):
            self.assertIn("O_NOFOLLOW", script)
            self.assertIn("stat.S_ISREG", script)
            self.assertIn("observed_names != expected_names", script)
            self.assertIn(
                "distribution-compliance.final-projection.json",
                script,
            )
            self.assertIn('("wheel", ".whl")', script)
            self.assertIn('("sdist", ".tar.gz")', script)
            self.assertIn("artifact digest mismatch", script)

        self.assertIn("GITHUB_OUTPUT", capture_run)
        self.assertIn("hashlib.sha256(payload)", capture_run)
        self.assertIn("base64.b64decode", gate_run)
        self.assertIn("observed_evidence != snapshot", gate_run)
        self.assertIn(
            "snapshot_artifacts != expected_snapshot_artifacts",
            gate_run,
        )

    def test_integrity_gate_rejects_artifact_evidence_and_file_set_changes(
        self,
    ) -> None:
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text())
        steps = workflow["jobs"]["distribution-compliance"]["steps"]
        capture = next(
            step
            for step in steps
            if step.get("name") == "Capture verified upload integrity"
        )
        gate = next(
            step
            for step in steps
            if step.get("name") == "Verify qualified upload integrity"
        )
        capture_script = self._heredoc_python(capture["run"])
        gate_script = self._heredoc_python(gate["run"])

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dist = root / "dist"
            evidence = root / "distribution-compliance-evidence"
            dist.mkdir()
            evidence.mkdir()
            wheel = dist / "package-1.0-py3-none-any.whl"
            sdist = dist / "package-1.0.tar.gz"
            wheel_payload = b"verified wheel bytes\n"
            sdist_payload = b"verified sdist bytes\n"
            wheel.write_bytes(wheel_payload)
            sdist.write_bytes(sdist_payload)
            projection = {
                "artifacts": {
                    "wheel": {
                        "filename": wheel.name,
                        "path": str(wheel),
                        "sha256": hashlib.sha256(wheel_payload).hexdigest(),
                    },
                    "sdist": {
                        "filename": sdist.name,
                        "path": str(sdist),
                        "sha256": hashlib.sha256(sdist_payload).hexdigest(),
                    },
                }
            }
            evidence_payloads = {
                name: f"{name}\n".encode() for name in EXPECTED_EVIDENCE_FILES
            }
            evidence_payloads["distribution-compliance.final-projection.json"] = (
                json.dumps(projection, sort_keys=True).encode()
            )
            for name, payload in evidence_payloads.items():
                (evidence / name).write_bytes(payload)

            github_output = root / "github-output"
            capture_env = {**os.environ, "GITHUB_OUTPUT": str(github_output)}
            captured = self._run_python(
                capture_script,
                root,
                capture_env,
            )
            self.assertEqual(0, captured.returncode, captured.stderr)
            manifest = github_output.read_text().strip().split("=", 1)[1]
            gate_env = {
                **os.environ,
                "EXPECTED_UPLOAD_INTEGRITY": manifest,
            }

            verified = self._run_python(gate_script, root, gate_env)
            self.assertEqual(0, verified.returncode, verified.stderr)

            wheel.write_bytes(b"mutated wheel bytes\n")
            artifact_failure = self._run_python(
                gate_script,
                root,
                gate_env,
            )
            self.assertNotEqual(0, artifact_failure.returncode)
            self.assertIn(
                "artifact digest mismatch",
                artifact_failure.stderr,
            )
            wheel.write_bytes(wheel_payload)

            report = evidence / "distribution-compliance.json"
            report.write_bytes(b"mutated evidence\n")
            evidence_failure = self._run_python(
                gate_script,
                root,
                gate_env,
            )
            self.assertNotEqual(0, evidence_failure.returncode)
            self.assertIn(
                "distribution evidence digest mismatch",
                evidence_failure.stderr,
            )
            report.write_bytes(evidence_payloads["distribution-compliance.json"])

            extra = evidence / "unverified.txt"
            extra.write_text("not verified\n")
            file_set_failure = self._run_python(
                gate_script,
                root,
                gate_env,
            )
            self.assertNotEqual(0, file_set_failure.returncode)
            self.assertIn("unexpected files", file_set_failure.stderr)

    @staticmethod
    def _artifact_paths(step: dict[str, object]) -> list[str]:
        value = step["with"]["path"]
        return [line.strip() for line in str(value).splitlines() if line.strip()]

    @staticmethod
    def _heredoc_python(run: str) -> str:
        marker = "python - <<'PY'\n"
        _, script = run.split(marker, 1)
        script, terminator = script.rsplit("\nPY", 1)
        if terminator.strip():
            raise AssertionError("unexpected content after Python heredoc")
        return script

    @staticmethod
    def _run_python(
        script: str,
        cwd: Path,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", script],
            cwd=cwd,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
