import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
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
            step for step in steps if step.get("name") == "Verify exact release source"
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
        upload_steps = {step["with"]["name"]: (index, step) for index, step in uploads}

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
                "/opt/voi-distribution-upload/${{ github.run_id }}-${{ "
                "github.run_attempt }}/qualified-release.tar",
            ],
            self._artifact_paths(qualified_upload),
        )
        self.assertNotIn("dist/*.whl", self._artifact_paths(qualified_upload))
        self.assertNotIn(
            "distribution-compliance-evidence/",
            self._artifact_paths(qualified_upload),
        )
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

    def test_qualified_upload_is_created_by_final_root_private_sealer(
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
        clean_index, _ = by_name["Verify release source remained clean"]
        seal_index, seal = by_name["Create sealed qualified upload bundle"]
        qualified_index, qualified = by_name[
            "Upload qualified release artifacts and compliance evidence"
        ]

        self.assertEqual(build_index + 1, capture_index)
        self.assertEqual(capture_index + 1, clean_index)
        self.assertEqual(clean_index + 1, seal_index)
        self.assertEqual(seal_index + 1, qualified_index)
        self.assertEqual("capture-upload-integrity", capture["id"])
        self.assertEqual("seal-qualified-upload", seal["id"])
        self.assertEqual("success()", seal["if"])
        self.assertEqual("success()", qualified["if"])
        self.assertIn(
            "steps.capture-upload-integrity.outputs.manifest",
            seal["env"]["EXPECTED_UPLOAD_INTEGRITY"],
        )

        capture_run = capture["run"]
        seal_run = seal["run"]
        for script in (capture_run, seal_run):
            self.assertIn("distribution-compliance.json", script)
            self.assertIn(
                "final projection does not match canonical report",
                script,
            )
            self.assertIn(
                "canonical report does not bind",
                script,
            )
            self.assertIn("O_NOFOLLOW", script)
            self.assertIn("stat.S_ISREG", script)
            self.assertIn('("wheel", ".whl")', script)
            self.assertIn('("sdist", ".tar.gz")', script)

        self.assertIn("sudo env", seal_run)
        self.assertIn("SEALED_UPLOAD_PARENT", seal_run)
        self.assertIn("tarfile.USTAR_FORMAT", seal_run)
        self.assertIn("archive.addfile", seal_run)
        self.assertIn("os.O_EXCL", seal_run)
        self.assertIn("read_regular(bundle_path) != bundle_bytes", seal_run)
        self.assertIn("os.chmod(bundle_path, 0o444)", seal_run)
        self.assertIn("os.chmod(seal_dir, 0o555)", seal_run)
        self.assertNotIn("chown -R", seal_run)
        self.assertEqual(
            [
                "/opt/voi-distribution-upload/${{ github.run_id }}-${{ "
                "github.run_attempt }}/qualified-release.tar",
            ],
            self._artifact_paths(qualified),
        )

    def test_capture_rejects_coordinated_projection_and_artifact_forgery(
        self,
    ) -> None:
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text())
        steps = workflow["jobs"]["distribution-compliance"]["steps"]
        capture = next(
            step
            for step in steps
            if step.get("name") == "Capture verified upload integrity"
        )
        capture_script = self._heredoc_python(capture["run"])

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self._write_valid_fixture(root)
            canonical_path = (
                root
                / "distribution-compliance-evidence"
                / "distribution-compliance.json"
            )
            canonical_before = canonical_path.read_bytes()

            forged_wheel = b"coordinated forged wheel bytes\n"
            fixture["wheel"].write_bytes(forged_wheel)
            projection_path = (
                root
                / "distribution-compliance-evidence"
                / "distribution-compliance.final-projection.json"
            )
            projection = json.loads(projection_path.read_text())
            projection["artifacts"]["wheel"]["sha256"] = hashlib.sha256(
                forged_wheel
            ).hexdigest()
            projection_json = json.dumps(
                projection,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            projection_path.write_bytes(projection_json)
            (
                root
                / "distribution-compliance-evidence"
                / "distribution-compliance.final-projection.md"
            ).write_bytes(self._projection_markdown(projection_json))

            github_output = root / "forged-capture-output"
            forged = self._run_python(
                capture_script,
                root,
                {**os.environ, "GITHUB_OUTPUT": str(github_output)},
            )

            self.assertNotEqual(0, forged.returncode)
            self.assertIn(
                "final projection does not match canonical report",
                forged.stderr,
            )
            self.assertEqual(canonical_before, canonical_path.read_bytes())

    def test_retained_source_fd_cannot_mutate_sealed_upload_bundle(
        self,
    ) -> None:
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text())
        steps = workflow["jobs"]["distribution-compliance"]["steps"]
        capture = next(
            step
            for step in steps
            if step.get("name") == "Capture verified upload integrity"
        )
        seal = next(
            step
            for step in steps
            if step.get("name") == "Create sealed qualified upload bundle"
        )
        capture_script = self._heredoc_python(capture["run"])
        seal_script = self._heredoc_python(seal["run"])

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self._write_valid_fixture(root)
            capture_output = root / "capture-output"
            captured = self._run_python(
                capture_script,
                root,
                {**os.environ, "GITHUB_OUTPUT": str(capture_output)},
            )
            self.assertEqual(0, captured.returncode, captured.stderr)
            snapshot = capture_output.read_text().strip().split("=", 1)[1]

            seal_parent = root / "trusted-seals"
            seal_dir = seal_parent / "run-1"
            seal_output = root / "seal-output"
            retained_fd = os.open(fixture["wheel"], os.O_WRONLY)
            try:
                sealed = self._run_python(
                    seal_script,
                    root,
                    {
                        **os.environ,
                        "EXPECTED_UPLOAD_INTEGRITY": snapshot,
                        "GITHUB_OUTPUT": str(seal_output),
                        "GITHUB_WORKSPACE": str(root),
                        "SEALED_UPLOAD_PARENT": str(seal_parent),
                        "SEALED_UPLOAD_DIR": str(seal_dir),
                    },
                )
                self.assertEqual(0, sealed.returncode, sealed.stderr)

                forged_wheel = b"retained fd post-gate mutation\n"
                os.lseek(retained_fd, 0, os.SEEK_SET)
                os.write(retained_fd, forged_wheel)
                os.ftruncate(retained_fd, len(forged_wheel))
            finally:
                os.close(retained_fd)

            bundle = seal_dir / "qualified-release.tar"
            self.assertEqual(forged_wheel, fixture["wheel"].read_bytes())
            with self.assertRaises(PermissionError):
                os.open(bundle, os.O_WRONLY)
            with tarfile.open(bundle, "r") as archive:
                wheel_member = archive.extractfile(f"dist/{fixture['wheel'].name}")
                self.assertIsNotNone(wheel_member)
                self.assertEqual(
                    fixture["wheel_payload"],
                    wheel_member.read(),
                )
                manifest_member = archive.extractfile("SEALED-UPLOAD-MANIFEST.json")
                self.assertIsNotNone(manifest_member)
                sealed_manifest = json.load(manifest_member)
            wheel_manifest = sealed_manifest["files"][f"dist/{fixture['wheel'].name}"]
            self.assertEqual(
                hashlib.sha256(fixture["wheel_payload"]).hexdigest(),
                wheel_manifest["sha256"],
            )
            output_digest = seal_output.read_text().strip().split("=", 1)[1]
            self.assertEqual(
                hashlib.sha256(bundle.read_bytes()).hexdigest(),
                output_digest,
            )

            os.chmod(seal_dir, 0o700)
            os.chmod(bundle, 0o600)

    @classmethod
    def _write_valid_fixture(
        cls,
        root: Path,
    ) -> dict[str, object]:
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
            "schema_version": 6,
            "artifacts": {
                "wheel": {
                    "filename": wheel.name,
                    "path": str(wheel.resolve()),
                    "sha256": hashlib.sha256(wheel_payload).hexdigest(),
                },
                "sdist": {
                    "filename": sdist.name,
                    "path": str(sdist.resolve()),
                    "sha256": hashlib.sha256(sdist_payload).hexdigest(),
                },
            },
        }
        projection_json = json.dumps(
            projection,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        projection_markdown = cls._projection_markdown(projection_json)
        input_manifest = [
            {
                "kind": "report",
                "path": ("report/distribution-compliance.final-projection.json"),
                "sha256": hashlib.sha256(projection_json).hexdigest(),
                "size": len(projection_json),
            },
            {
                "kind": "report",
                "path": ("report/distribution-compliance.final-projection.md"),
                "sha256": hashlib.sha256(projection_markdown).hexdigest(),
                "size": len(projection_markdown),
            },
        ]
        canonical_report = {
            **projection,
            "blockers": [],
            "ok": True,
            "secret_scan": {"input_manifest": input_manifest},
            "status": "passed",
        }
        evidence_payloads = {
            name: f"{name}\n".encode() for name in EXPECTED_EVIDENCE_FILES
        }
        evidence_payloads["distribution-compliance.json"] = json.dumps(
            canonical_report,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        evidence_payloads["distribution-compliance.final-projection.json"] = (
            projection_json
        )
        evidence_payloads["distribution-compliance.final-projection.md"] = (
            projection_markdown
        )
        evidence_payloads["secret-scan.json"] = json.dumps(
            canonical_report["secret_scan"],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        for name, payload in evidence_payloads.items():
            (evidence / name).write_bytes(payload)
        return {
            "wheel": wheel,
            "wheel_payload": wheel_payload,
        }

    @staticmethod
    def _projection_markdown(projection_json: bytes) -> bytes:
        return (
            b"# Distribution Compliance Final Projection\n\n"
            b"The canonical self-reference-free report payload follows."
            b"\n\n```json\n" + projection_json + b"```\n"
        )

    @staticmethod
    def _artifact_paths(step: dict[str, object]) -> list[str]:
        value = step["with"]["path"]
        return [line.strip() for line in str(value).splitlines() if line.strip()]

    @staticmethod
    def _heredoc_python(run: str) -> str:
        markers = ("python - <<'PY'\n", "python3 - <<'PY'\n")
        marker = next((item for item in markers if item in run), None)
        if marker is None:
            raise AssertionError("Python heredoc not found")
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
