import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))
compliance_module = importlib.import_module(
    "starcraft_commander.distribution_compliance"
)
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
BUILD_JOB = "distribution_compliance_build"
SEAL_JOB = "distribution-compliance"
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
ALLOW_SYNTHETIC_SEMANTIC_REPORT = """
from starcraft_commander import distribution_compliance as _compliance
_compliance.distribution_report_blockers = lambda report: []
"""
VALIDATE_SYNTHETIC_LICENSE = """
from starcraft_commander import distribution_compliance as _compliance

def _synthetic_license_blockers(report):
    metadata = report.get("metadata", {})
    expected = _compliance.EXPECTED_LICENSE_EXPRESSION
    if (
        metadata.get("license_expressions") != [expected]
        or f"License-Expression: {expected}" not in metadata.get(
            "installed_raw",
            "",
        )
    ):
        return [{"code": "invalid_license_evidence"}]
    return []

_compliance.distribution_report_blockers = _synthetic_license_blockers
"""


class DistributionComplianceWorkflowContractTests(unittest.TestCase):
    def test_fresh_job_is_the_only_enabled_qualified_sealer(self) -> None:
        workflow = self._workflow()
        build = workflow["jobs"][BUILD_JOB]
        seal = workflow["jobs"][SEAL_JOB]

        self.assertEqual({"contents": "read"}, workflow["permissions"])
        self.assertEqual(["unit-contracts"], build["needs"])
        self.assertEqual({"contents": "read"}, build["permissions"])
        self.assertEqual([BUILD_JOB], seal["needs"])
        self.assertEqual(
            {"actions": "read", "contents": "read"},
            seal["permissions"],
        )
        self.assertEqual("ubuntu-latest", build["runs-on"])
        self.assertEqual("ubuntu-latest", seal["runs-on"])

        disabled = self._step(build, "Disabled same-runner qualified sealer")
        self.assertEqual("${{ false }}", disabled["if"])
        for step in build["steps"]:
            if step.get("if") != "${{ false }}":
                self.assertNotIn(
                    "/opt/voi-distribution-upload",
                    str(step.get("run", "")),
                )

        final_steps = seal["steps"]
        verifier_index = self._step_index(
            final_steps,
            "Verify candidate evidence without privileges",
        )
        seal_index = self._step_index(
            final_steps,
            "Create sealed qualified upload bundle",
        )
        digest_index = self._step_index(
            final_steps,
            "Verify exact sealed bytes before upload",
        )
        upload_index = self._step_index(
            final_steps,
            "Upload qualified release artifacts and compliance evidence",
        )
        self.assertLess(verifier_index, seal_index)
        self.assertLess(seal_index, digest_index)
        self.assertLess(digest_index, upload_index)

    def test_both_jobs_bind_pr_and_push_event_context(self) -> None:
        workflow = self._workflow()
        for job_name in (BUILD_JOB, SEAL_JOB):
            with self.subTest(job=job_name):
                job = workflow["jobs"][job_name]
                env = job["env"]
                self.assertIn(
                    "github.event.pull_request.head.sha",
                    env["EXPECTED_RELEASE_COMMIT"],
                )
                self.assertIn("github.sha", env["EXPECTED_RELEASE_COMMIT"])
                self.assertIn(
                    "github.event.pull_request.base.sha",
                    env["EXPECTED_RELEASE_BASE_COMMIT"],
                )
                self.assertIn(
                    "github.event.before",
                    env["EXPECTED_RELEASE_BASE_COMMIT"],
                )
                self.assertEqual(
                    "${{ github.event_name }}",
                    env["EXPECTED_RELEASE_EVENT_NAME"],
                )

                checkouts = [
                    step
                    for step in job["steps"]
                    if str(step.get("uses", "")).startswith(
                        "actions/checkout@"
                    )
                ]
                self.assertEqual(2, len(checkouts))
                refs = {step["with"]["ref"] for step in checkouts}
                self.assertEqual(
                    {
                        "${{ github.event.pull_request.head.sha }}",
                        "${{ github.sha }}",
                    },
                    refs,
                )
                for checkout in checkouts:
                    self.assertEqual(0, checkout["with"]["fetch-depth"])
                    self.assertFalse(
                        checkout["with"]["persist-credentials"]
                    )

        capture_run = self._step(
            workflow["jobs"][BUILD_JOB],
            "Capture verified upload integrity",
        )["run"]
        seal_run = self._step(
            workflow["jobs"][SEAL_JOB],
            "Create sealed qualified upload bundle",
        )["run"]
        for script in (capture_run, seal_run):
            normalized = " ".join(script.split())
            self.assertIn(
                "canonical report repository root does not match "
                '" "GITHUB_WORKSPACE',
                normalized,
            )
            self.assertIn(
                "canonical head_commit does not match "
                '" "EXPECTED_RELEASE_COMMIT',
                normalized,
            )
            self.assertIn(
                "canonical base_commit does not match "
                '" "EXPECTED_RELEASE_BASE_COMMIT',
                normalized,
            )
            self.assertIn(
                "canonical merge_base does not match event range",
                normalized,
            )
            self.assertIn(
                "push before commit is not an ancestor of release HEAD",
                normalized,
            )

    def test_handoff_is_immutable_provenance_checked_input(self) -> None:
        workflow = self._workflow()
        build = workflow["jobs"][BUILD_JOB]
        seal = workflow["jobs"][SEAL_JOB]
        handoff = self._step(build, "Upload untrusted distribution handoff")
        provenance = self._step(
            seal,
            "Verify untrusted handoff artifact provenance",
        )
        download = self._step(
            seal,
            "Download exact untrusted distribution handoff",
        )
        materialize = self._step(
            seal,
            "Materialize bounded root-owned verifier inputs",
        )

        self.assertEqual("success()", handoff["if"])
        self.assertRegex(
            handoff["uses"],
            r"^actions/upload-artifact@[0-9a-f]{40}$",
        )
        self.assertEqual(
            "distribution-compliance-untrusted-handoff",
            handoff["with"]["name"],
        )
        self.assertEqual(0, handoff["with"]["compression-level"])
        self.assertFalse(handoff["with"]["include-hidden-files"])
        self.assertFalse(handoff["with"]["overwrite"])
        self.assertEqual(1, handoff["with"]["retention-days"])
        self.assertEqual(
            ["${{ runner.temp }}/voi-distribution-handoff/"
             "candidate-handoff.tar"],
            self._artifact_paths(handoff),
        )

        self.assertRegex(
            download["uses"],
            r"^actions/download-artifact@[0-9a-f]{40}$",
        )
        self.assertIn("artifact-ids", download["with"])
        self.assertTrue(download["with"]["merge-multiple"])
        self.assertEqual(
            "${{ github.token }}",
            provenance["env"]["GH_TOKEN"],
        )
        for required in (
            "EXPECTED_HANDOFF_ARTIFACT_ID",
            "EXPECTED_HANDOFF_ARTIFACT_DIGEST",
            ".workflow_run.id",
            "GITHUB_RUN_ID",
            ".digest",
            ".expired",
        ):
            self.assertIn(required, provenance["run"])

        materialize_run = materialize["run"]
        for required in (
            "MAX_HANDOFF_BYTES",
            "MAX_MEMBER_BYTES",
            "MAX_TOTAL_BYTES",
            "O_NOFOLLOW",
            "member.isfile()",
            "member.pax_headers",
            "unexpected handoff files",
            "handoff file digest mismatch",
            "handoff tar is not canonical",
            "downloaded handoff digest mismatch",
            "os.O_EXCL",
            "os.chown(handoff_path, 0, 0)",
        ):
            self.assertIn(required, materialize_run)

    def test_runner_hardens_workspace_before_root_materialization(
        self,
    ) -> None:
        workflow = self._workflow()
        steps = workflow["jobs"][SEAL_JOB]["steps"]
        harden_name = (
            "Harden runner-owned workspace before root materialization"
        )
        materialize_name = "Materialize bounded root-owned verifier inputs"
        verifier_name = "Verify candidate evidence without privileges"
        sealer_name = "Create sealed qualified upload bundle"

        harden = self._step(workflow["jobs"][SEAL_JOB], harden_name)
        materialize = self._step(
            workflow["jobs"][SEAL_JOB],
            materialize_name,
        )
        materialize_index = self._step_index(steps, materialize_name)
        self.assertLess(
            self._step_index(steps, harden_name),
            materialize_index,
        )
        self.assertLess(
            materialize_index,
            self._step_index(steps, verifier_name),
        )
        self.assertLess(
            self._step_index(steps, verifier_name),
            self._step_index(steps, sealer_name),
        )
        self.assertIn(
            'chmod -R go-w "${GITHUB_WORKSPACE}"',
            harden["run"],
        )
        self.assertNotIn("sudo", harden["run"])
        self.assertNotIn("chmod -R", materialize["run"])
        hardening_command = 'chmod -R go-w "${GITHUB_WORKSPACE}"'
        self.assertEqual(
            1,
            sum(
                hardening_command in str(step.get("run", ""))
                for step in steps
            ),
        )
        for step in steps[materialize_index:]:
            self.assertNotIn(
                hardening_command,
                str(step.get("run", "")),
            )
        for required in (
            "directory.mkdir(mode=0o700, exist_ok=False)",
            "os.chmod(path, 0o444)",
            "os.chmod(dist_dir, 0o555)",
            "os.chmod(evidence_dir, 0o555)",
        ):
            self.assertIn(required, materialize["run"])

        runner_uid = 1001
        ownership = {"checkout": runner_uid}

        def runner_recursive_chmod() -> None:
            if any(owner != runner_uid for owner in ownership.values()):
                raise PermissionError(
                    "runner cannot chmod root-owned materialization"
                )

        runner_recursive_chmod()
        ownership.update({"dist": 0, "evidence": 0})
        with self.assertRaisesRegex(
            PermissionError,
            "runner cannot chmod root-owned materialization",
        ):
            runner_recursive_chmod()

    def test_distribution_actions_are_pinned_to_exact_commits(self) -> None:
        workflow = self._workflow()
        for job_name in (BUILD_JOB, SEAL_JOB):
            for step in workflow["jobs"][job_name]["steps"]:
                uses = step.get("uses")
                if uses is None:
                    continue
                with self.subTest(job=job_name, uses=uses):
                    self.assertRegex(
                        uses,
                        r"^[^@\s]+@[0-9a-f]{40}$",
                    )

    def test_candidate_verifier_has_no_credentials_or_sudo_capability(
        self,
    ) -> None:
        workflow = self._workflow()
        seal = workflow["jobs"][SEAL_JOB]
        setup = self._step(seal, "Set up trusted verifier runtime")
        verifier = self._step(
            seal,
            "Verify candidate evidence without privileges",
        )
        verifier_run = verifier["run"]

        self.assertIn(
            "if sudo -u voi-verifier sudo -n true",
            setup["run"],
        )
        self.assertIn("sudo -u voi-verifier env -i", verifier_run)
        self.assertIn("pkill -KILL -u", verifier_run)
        self.assertIn("pgrep -u", verifier_run)
        self.assertIn(
            "candidate verifier process survived cleanup",
            verifier_run,
        )
        self.assertNotIn("GITHUB_TOKEN", verifier_run)
        self.assertNotIn("GH_TOKEN", verifier_run)
        self.assertNotIn("ACTIONS_RUNTIME_TOKEN", verifier_run)

        seal_index = self._step_index(
            seal["steps"],
            "Create sealed qualified upload bundle",
        )
        for step in seal["steps"][:seal_index]:
            if step.get("name") != "Disabled same-runner qualified sealer":
                self.assertNotIn(
                    "SEALED_UPLOAD_DIR",
                    str(step.get("run", "")),
                )

    def test_failed_evidence_and_final_uploads_remain_separate(self) -> None:
        workflow = self._workflow()
        build = workflow["jobs"][BUILD_JOB]
        seal = workflow["jobs"][SEAL_JOB]
        build_failed = self._step(
            build,
            "Upload failed distribution compliance evidence",
        )
        seal_failed = self._step(
            seal,
            "Upload failed distribution compliance evidence",
        )
        qualified = self._step(
            seal,
            "Upload qualified release artifacts and compliance evidence",
        )

        for failed in (build_failed, seal_failed):
            self.assertEqual("failure()", failed["if"])
            self.assertEqual(
                ["distribution-compliance-evidence/"],
                self._artifact_paths(failed),
            )
            self.assertEqual("warn", failed["with"]["if-no-files-found"])

        self.assertEqual("success()", qualified["if"])
        self.assertEqual(
            [
                "/opt/voi-distribution-upload/${{ github.run_id }}-${{ "
                "github.run_attempt }}/qualified-release.tar",
            ],
            self._artifact_paths(qualified),
        )
        self.assertEqual("error", qualified["with"]["if-no-files-found"])
        self.assertEqual(0, qualified["with"]["compression-level"])
        self.assertFalse(qualified["with"]["overwrite"])
        self.assertNotIn("dist/*.whl", self._artifact_paths(qualified))
        self.assertNotIn(
            "distribution-compliance-evidence/",
            self._artifact_paths(qualified),
        )

    def test_capture_rejects_coordinated_projection_and_artifact_forgery(
        self,
    ) -> None:
        capture_script = self._capture_script()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self._write_valid_fixture(root, label="projection")
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

            output = root / "forged-capture-output"
            forged = self._run_python(
                capture_script,
                root,
                self._workflow_env(root, fixture, output),
                prelude=ALLOW_SYNTHETIC_SEMANTIC_REPORT,
            )

            self.assertNotEqual(0, forged.returncode)
            self.assertIn(
                "derived evidence mismatch: "
                "distribution-compliance.final-projection.json",
                forged.stderr,
            )
            self.assertEqual(canonical_before, canonical_path.read_bytes())

    def test_capture_rejects_coordinated_canonical_mit_forgery(self) -> None:
        capture_script = self._capture_script()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self._write_valid_fixture(root, label="license")
            evidence = root / "distribution-compliance-evidence"
            canonical_path = evidence / "distribution-compliance.json"
            report = json.loads(canonical_path.read_text())
            report["metadata"]["license_expressions"] = ["MIT"]
            report["metadata"]["installed_raw"] = (
                "Metadata-Version: 2.4\n"
                "License-Expression: MIT\n"
            )
            self._rewrite_evidence(evidence, report)

            output = root / "forged-capture-output"
            forged = self._run_python(
                capture_script,
                root,
                self._workflow_env(root, fixture, output),
                prelude=VALIDATE_SYNTHETIC_LICENSE,
            )

            self.assertNotEqual(0, forged.returncode)
            self.assertIn(
                "canonical compliance report has semantic blockers: "
                "invalid_license_evidence",
                forged.stderr,
            )
            self.assertFalse(output.exists())

    def test_capture_rejects_each_derived_evidence_mutation(self) -> None:
        capture_script = self._capture_script()
        derived_names = EXPECTED_EVIDENCE_FILES - {
            "distribution-compliance.json"
        }

        for name in sorted(derived_names):
            with (
                self.subTest(name=name),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                fixture = self._write_valid_fixture(
                    root,
                    label=f"derived-{name}",
                )
                evidence_path = root / "distribution-compliance-evidence" / name
                evidence_path.write_bytes(evidence_path.read_bytes() + b" ")
                output = root / "forged-capture-output"

                forged = self._run_python(
                    capture_script,
                    root,
                    self._workflow_env(root, fixture, output),
                    prelude=ALLOW_SYNTHETIC_SEMANTIC_REPORT,
                )

                self.assertNotEqual(0, forged.returncode)
                self.assertIn(
                    f"derived evidence mismatch: {name}",
                    forged.stderr,
                )
                self.assertFalse(output.exists())

    def test_capture_rejects_unrelated_rebound_clone(self) -> None:
        capture_script = self._capture_script()

        with (
            tempfile.TemporaryDirectory() as event_temporary,
            tempfile.TemporaryDirectory() as rebound_temporary,
        ):
            event_root = Path(event_temporary)
            rebound_root = Path(rebound_temporary)
            event_fixture = self._write_valid_fixture(
                event_root,
                label="event-source",
            )
            rebound_fixture = self._write_valid_fixture(
                rebound_root,
                label="unrelated-source",
            )
            self.assertNotEqual(
                event_fixture["head_commit"],
                rebound_fixture["head_commit"],
            )

            output = rebound_root / "rebound-output"
            env = self._workflow_env(
                rebound_root,
                event_fixture,
                output,
            )
            rebound = self._run_python(
                capture_script,
                rebound_root,
                env,
                prelude=ALLOW_SYNTHETIC_SEMANTIC_REPORT,
            )

            self.assertNotEqual(0, rebound.returncode)
            self.assertIn(
                "canonical repository before does not match "
                "EXPECTED_RELEASE_COMMIT",
                rebound.stderr,
            )
            self.assertFalse(output.exists())

    def test_capture_rejects_rebound_root_base_and_merge_base(self) -> None:
        capture_script = self._capture_script()
        mutations = {
            "root": (
                lambda report, _fixture: [
                    state.update(
                        {
                            "repository_root": "/unrelated/repository",
                            "source_root": "/unrelated/repository",
                        }
                    )
                    for state in (
                        report["repository"]["before"],
                        report["repository"]["after"],
                    )
                ],
                "canonical report repository root does not match",
            ),
            "base": (
                lambda report, _fixture: report["repository"].update(
                    {"base_commit": "f" * 40}
                ),
                "canonical base_commit does not match "
                "EXPECTED_RELEASE_BASE_COMMIT",
            ),
            "merge-base": (
                lambda report, fixture: report["repository"].update(
                    {"merge_base": fixture["head_commit"]}
                ),
                "canonical merge_base does not match event range",
            ),
        }
        for label, (mutate, expected_error) in mutations.items():
            with (
                self.subTest(label=label),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                fixture = self._write_valid_fixture(
                    root,
                    label=f"rebound-{label}",
                )
                evidence = root / "distribution-compliance-evidence"
                canonical_path = evidence / "distribution-compliance.json"
                report = json.loads(canonical_path.read_text())
                mutate(report, fixture)
                self._rewrite_evidence(evidence, report)
                output = root / "rebound-output"

                rebound = self._run_python(
                    capture_script,
                    root,
                    self._workflow_env(root, fixture, output),
                    prelude=ALLOW_SYNTHETIC_SEMANTIC_REPORT,
                )

                self.assertNotEqual(0, rebound.returncode)
                self.assertIn(expected_error, rebound.stderr)
                self.assertFalse(output.exists())

    def test_materializer_rejects_coordinated_trailing_handoff_data(
        self,
    ) -> None:
        workflow = self._workflow()
        capture_script = self._capture_script()
        materialize_script = self._heredoc_python(
            self._step(
                workflow["jobs"][SEAL_JOB],
                "Materialize bounded root-owned verifier inputs",
            )["run"]
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self._write_valid_fixture(root, label="trailing-data")
            capture_output = root / "capture-output"
            captured = self._run_python(
                capture_script,
                root,
                self._workflow_env(root, fixture, capture_output),
                prelude=ALLOW_SYNTHETIC_SEMANTIC_REPORT,
            )
            self.assertEqual(0, captured.returncode, captured.stderr)
            source_handoff = (
                Path(fixture["runner_temp"])
                / "voi-distribution-handoff"
                / "candidate-handoff.tar"
            )
            forged_handoff = root / "forged-candidate-handoff.tar"
            forged_payload = source_handoff.read_bytes() + (
                b"private-config-after-tar-end"
            )
            forged_handoff.write_bytes(forged_payload)
            shutil.rmtree(root / "dist")
            shutil.rmtree(root / "distribution-compliance-evidence")
            output = root / "materialize-output"

            materialized = self._run_python(
                materialize_script,
                root,
                {
                    **self._workflow_env(root, fixture, output),
                    "EXPECTED_HANDOFF_TAR_SHA256": hashlib.sha256(
                        forged_payload
                    ).hexdigest(),
                    "HANDOFF_PATH": str(forged_handoff),
                },
            )

            self.assertNotEqual(0, materialized.returncode)
            self.assertIn(
                "handoff tar is not canonical",
                materialized.stderr,
            )
            self.assertFalse(output.exists())

    def test_uploaded_handoff_survives_retained_build_writer(self) -> None:
        workflow = self._workflow()
        capture_script = self._capture_script()
        materialize_script = self._heredoc_python(
            self._step(
                workflow["jobs"][SEAL_JOB],
                "Materialize bounded root-owned verifier inputs",
            )["run"]
        )
        seal_script = self._heredoc_python(
            self._step(
                workflow["jobs"][SEAL_JOB],
                "Create sealed qualified upload bundle",
            )["run"]
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self._write_valid_fixture(root, label="retained-writer")
            capture_output = root / "capture-output"
            captured = self._run_python(
                capture_script,
                root,
                self._workflow_env(root, fixture, capture_output),
                prelude=ALLOW_SYNTHETIC_SEMANTIC_REPORT,
            )
            self.assertEqual(0, captured.returncode, captured.stderr)
            handoff_digest = capture_output.read_text().strip().split(
                "=",
                1,
            )[1]
            source_handoff = (
                Path(fixture["runner_temp"])
                / "voi-distribution-handoff"
                / "candidate-handoff.tar"
            )
            uploaded_handoff = root / "downloaded-candidate-handoff.tar"
            uploaded_handoff.write_bytes(source_handoff.read_bytes())

            retained_fd = os.open(fixture["wheel"], os.O_WRONLY)
            try:
                forged_wheel = b"retained root watcher mutation\n"
                os.lseek(retained_fd, 0, os.SEEK_SET)
                os.write(retained_fd, forged_wheel)
                os.ftruncate(retained_fd, len(forged_wheel))
            finally:
                os.close(retained_fd)
            shutil.rmtree(root / "dist")
            shutil.rmtree(root / "distribution-compliance-evidence")

            materialize_output = root / "materialize-output"
            materialized = self._run_python(
                materialize_script,
                root,
                {
                    **self._workflow_env(
                        root,
                        fixture,
                        materialize_output,
                    ),
                    "EXPECTED_HANDOFF_TAR_SHA256": handoff_digest,
                    "HANDOFF_PATH": str(uploaded_handoff),
                },
                prelude="import os\nos.chown = lambda *args: None",
            )
            self.assertEqual(0, materialized.returncode, materialized.stderr)
            self.assertEqual(
                fixture["wheel_payload"],
                fixture["wheel"].read_bytes(),
            )

            snapshot = materialize_output.read_text().strip().split(
                "=",
                1,
            )[1]
            seal_parent = root / "trusted-seals"
            seal_dir = seal_parent / "run-1"
            seal_output = root / "seal-output"
            sealed = self._run_python(
                seal_script,
                root,
                {
                    **self._workflow_env(root, fixture, seal_output),
                    "EXPECTED_UPLOAD_INTEGRITY": snapshot,
                    "SEALED_UPLOAD_PARENT": str(seal_parent),
                    "SEALED_UPLOAD_DIR": str(seal_dir),
                },
            )
            self.assertEqual(0, sealed.returncode, sealed.stderr)

            bundle = seal_dir / "qualified-release.tar"
            with tarfile.open(bundle, "r:") as archive:
                wheel_member = archive.extractfile(
                    f"dist/{fixture['wheel'].name}"
                )
                self.assertIsNotNone(wheel_member)
                self.assertEqual(
                    fixture["wheel_payload"],
                    wheel_member.read(),
                )
            output_digest = seal_output.read_text().strip().split("=", 1)[1]
            self.assertEqual(
                hashlib.sha256(bundle.read_bytes()).hexdigest(),
                output_digest,
            )

            os.chmod(seal_dir, 0o700)
            os.chmod(bundle, 0o600)
            os.chmod(root / "dist", 0o700)
            os.chmod(root / "distribution-compliance-evidence", 0o700)
            os.chmod(source_handoff.parent, 0o700)
            os.chmod(source_handoff, 0o600)

    @staticmethod
    def _workflow() -> dict[str, object]:
        return yaml.safe_load(WORKFLOW_PATH.read_text())

    @classmethod
    def _capture_script(cls) -> str:
        workflow = cls._workflow()
        capture = cls._step(
            workflow["jobs"][BUILD_JOB],
            "Capture verified upload integrity",
        )
        return cls._heredoc_python(capture["run"])

    @staticmethod
    def _step(job: dict[str, object], name: str) -> dict[str, object]:
        return next(step for step in job["steps"] if step.get("name") == name)

    @staticmethod
    def _step_index(steps: list[dict[str, object]], name: str) -> int:
        return next(
            index
            for index, step in enumerate(steps)
            if step.get("name") == name
        )

    @classmethod
    def _write_valid_fixture(
        cls,
        root: Path,
        *,
        label: str,
    ) -> dict[str, object]:
        base_commit, head_commit, merge_base, tree = cls._git_history(
            root,
            label,
        )
        dist = root / "dist"
        evidence = root / "distribution-compliance-evidence"
        runner_temp = root / "runner-temp"
        dist.mkdir()
        evidence.mkdir()
        runner_temp.mkdir()
        wheel = dist / "package-1.0-py3-none-any.whl"
        sdist = dist / "package-1.0.tar.gz"
        wheel_payload = b"verified wheel bytes\n"
        sdist_payload = b"verified sdist bytes\n"
        wheel.write_bytes(wheel_payload)
        sdist.write_bytes(sdist_payload)
        repository_state = {
            "repository_root": str(root.resolve()),
            "source_root": str(root.resolve()),
            "source_root_matches": True,
            "head": head_commit,
            "tree": tree,
            "dirty_entries": [],
            "replacement_refs": [],
            "ok": True,
        }
        projection = {
            "schema_version": (
                compliance_module.DISTRIBUTION_COMPLIANCE_SCHEMA_VERSION
            ),
            "repository": {
                "before": dict(repository_state),
                "after": dict(repository_state),
                "base_commit": base_commit,
                "head_commit": head_commit,
                "merge_base": merge_base,
            },
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
            "dependencies": {},
            "licenses": [],
            "metadata": {
                "installed_raw": (
                    "Metadata-Version: 2.4\n"
                    "License-Expression: "
                    f"{compliance_module.EXPECTED_LICENSE_EXPRESSION}\n"
                ),
                "license_expressions": [
                    compliance_module.EXPECTED_LICENSE_EXPRESSION
                ],
            },
        }
        canonical_report = {
            **projection,
            "blockers": [],
            "ok": True,
            "secret_scan": {
                "finding_count": 0,
                "findings": [],
                "input_manifest": [],
            },
            "status": "passed",
        }
        cls._rebind_projection(canonical_report)
        compliance_module.write_distribution_evidence(
            canonical_report,
            evidence,
            public_report=canonical_report,
        )
        return {
            "base_commit": base_commit,
            "head_commit": head_commit,
            "merge_base": merge_base,
            "runner_temp": runner_temp,
            "sdist": sdist,
            "sdist_payload": sdist_payload,
            "wheel": wheel,
            "wheel_payload": wheel_payload,
        }

    @staticmethod
    def _git_history(
        root: Path,
        label: str,
    ) -> tuple[str, str, str, str]:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(
            ["git", "config", "user.email", "ci@example.invalid"],
            cwd=root,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "CI Fixture"],
            cwd=root,
            check=True,
        )
        marker = root / "fixture.txt"
        marker.write_text(f"base:{label}\n", encoding="utf-8")
        subprocess.run(["git", "add", "fixture.txt"], cwd=root, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", f"base {label}"],
            cwd=root,
            check=True,
        )
        base_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            text=True,
        ).strip()
        marker.write_text(f"head:{label}\n", encoding="utf-8")
        subprocess.run(["git", "add", "fixture.txt"], cwd=root, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", f"head {label}"],
            cwd=root,
            check=True,
        )
        head_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            text=True,
        ).strip()
        merge_base = subprocess.check_output(
            ["git", "merge-base", base_commit, head_commit],
            cwd=root,
            text=True,
        ).strip()
        tree = subprocess.check_output(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=root,
            text=True,
        ).strip()
        return base_commit, head_commit, merge_base, tree

    @staticmethod
    def _workflow_env(
        root: Path,
        fixture: dict[str, object],
        output: Path,
    ) -> dict[str, str]:
        return {
            **os.environ,
            "EXPECTED_RELEASE_EVENT_NAME": "pull_request",
            "EXPECTED_RELEASE_COMMIT": str(fixture["head_commit"]),
            "EXPECTED_RELEASE_BASE_COMMIT": str(fixture["base_commit"]),
            "GITHUB_OUTPUT": str(output),
            "GITHUB_WORKSPACE": str(root.resolve()),
            "RUNNER_TEMP": str(fixture["runner_temp"]),
        }

    @staticmethod
    def _rebind_projection(report: dict[str, object]) -> None:
        payloads = compliance_module._distribution_report_scan_payloads(
            report
        )
        secret_scan = report["secret_scan"]
        manifest = [
            {
                "kind": "report",
                "path": f"report/{name}",
                "sha256": compliance_module.sha256_bytes(payload),
                "size": len(payload),
            }
            for name, payload in sorted(payloads.items())
        ]
        secret_scan["input_manifest"] = manifest
        secret_scan["input_manifest_sha256"] = (
            compliance_module.sha256_bytes(
                compliance_module.canonical_json_text(manifest).encode()
            )
        )
        secret_scan["scanned_file_count"] = len(manifest)

    @classmethod
    def _rewrite_evidence(
        cls,
        evidence: Path,
        report: dict[str, object],
    ) -> None:
        cls._rebind_projection(report)
        for path in evidence.iterdir():
            path.unlink()
        compliance_module.write_distribution_evidence(
            report,
            evidence,
            public_report=report,
        )

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
        return [
            line.strip()
            for line in str(value).splitlines()
            if line.strip()
        ]

    @staticmethod
    def _heredoc_python(run: str) -> str:
        markers = ("python - <<'PY'\n", "python3 - <<'PY'\n")
        marker = next((item for item in markers if item in run), None)
        if marker is None:
            raise AssertionError("Python heredoc not found")
        _, script = run.split(marker, 1)
        script, terminator, _ = script.partition("\nPY")
        if not terminator:
            raise AssertionError("Python heredoc terminator not found")
        return script

    @staticmethod
    def _run_python(
        script: str,
        cwd: Path,
        env: dict[str, str],
        *,
        prelude: str = "",
    ) -> subprocess.CompletedProcess[str]:
        run_env = dict(env)
        existing_pythonpath = run_env.get("PYTHONPATH")
        run_env["PYTHONPATH"] = os.pathsep.join(
            part
            for part in (
                str(REPOSITORY_ROOT),
                existing_pythonpath,
            )
            if part
        )
        return subprocess.run(
            [sys.executable, "-c", f"{prelude}\n{script}"],
            cwd=cwd,
            env=run_env,
            check=False,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
