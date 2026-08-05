"""Tests for release distribution and private-configuration compliance."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

from starcraft_commander import distribution_compliance as compliance_module
from starcraft_commander.distribution_compliance import (
    EXPECTED_DIRECT_DISTRIBUTIONS,
    EXPECTED_LICENSE_EXPRESSION,
    REQUIRED_LICENSE_FILES,
    REQUIRED_RUNTIME_FILES,
    ArchiveSnapshot,
    _isolated_venv_builder,
    archive_content_blockers,
    declared_dependencies_from_pyproject,
    distribution_report_blockers,
    inspect_wheel,
    scan_git_and_artifacts,
    scan_payload,
)


class DependencyInventoryTest(unittest.TestCase):
    def test_declared_dependencies_ignore_comments_and_preserve_quoted_hashes(
        self,
    ) -> None:
        pyproject = """
[project.optional-dependencies]
# The importable package is called 'sc2'; this is not a distribution.
sc2 = ["burnysc2>=6.5"]
llm = ["openai>=1.0", "anthropic>=0.40"] # comment with "fake-dependency"
fixture = ["package-with-hash#fragment"]

[tool.setuptools]
include-package-data = true
"""

        self.assertEqual(
            {
                "anthropic",
                "burnysc2",
                "openai",
                "package-with-hash",
            },
            set(declared_dependencies_from_pyproject(pyproject)),
        )


class IsolatedInstallTest(unittest.TestCase):
    def test_venv_preserves_managed_python_layout_on_posix(self) -> None:
        self.assertEqual(os.name != "nt", _isolated_venv_builder().symlinks)


class ArchivePolicyTest(unittest.TestCase):
    def test_wheel_allowlist_rejects_tests_docs_and_local_configuration(
        self,
    ) -> None:
        snapshot = ArchiveSnapshot(
            kind="wheel",
            path=Path("candidate.whl"),
            digest="a" * 64,
            entries=(),
            files={
                "starcraft_commander/runtime_data.py": b"",
                "tests/test_runtime_data.py": b"",
                "docs/private.md": b"",
                "starcraft_commander/.env.local": b"",
            },
            blockers=(),
        )

        blockers = archive_content_blockers(snapshot)

        self.assertEqual(
            {
                "denied_component:tests",
                "denied_component:docs",
                "local_environment_file",
            },
            {str(item.get("reason")) for item in blockers},
        )

    def test_sdist_allows_generated_setup_cfg_but_rejects_tests(self) -> None:
        snapshot = ArchiveSnapshot(
            kind="sdist",
            path=Path("candidate.tar.gz"),
            digest="b" * 64,
            entries=(),
            files={
                "voistarcraft2-0.1.0/setup.cfg": b"",
                "voistarcraft2-0.1.0/tests/test_private.py": b"",
            },
            blockers=(),
        )

        blockers = archive_content_blockers(snapshot)

        self.assertEqual(1, len(blockers))
        self.assertEqual("denied_archive_entry", blockers[0]["code"])
        self.assertEqual("denied_component:tests", blockers[0]["reason"])

    def test_wheel_inspection_rejects_traversal_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            wheel_path = Path(temporary) / "candidate.whl"
            with zipfile.ZipFile(wheel_path, "w") as archive:
                archive.writestr("../credentials.json", "{}")

            snapshot = inspect_wheel(wheel_path)

        self.assertEqual("unsafe_archive_entry", snapshot.blockers[0]["code"])


class PrivateConfigurationScannerTest(unittest.TestCase):
    def test_detects_required_secret_and_private_configuration_classes(self) -> None:
        api_key = "sk-" + "liveabcdefghijklmnop"
        bearer = "Bearer " + "opaqueabcdefghijklmnop"
        endpoint = "http://192.168." + "50.12:8443/v1"
        model = "gpt-" + "private-release-model"
        credential_path = "/home/user/.aws/" + "credentials"
        payload = (
            f"api_key = '{api_key}'\n"
            f"Authorization: {bearer}\n"
            "OPENAI_API_KEY=" + "abcdefghijklmnop\n"
            f"MYPROXY_OPENAI_BASE_URL = '{endpoint}'\n"
            f"DEFAULT_MYPROXY_MODEL = '{model}'\n"
            f"credential_path = '{credential_path}'\n"
        ).encode()

        findings = scan_payload("candidate.py", payload)

        self.assertEqual(
            {
                "api_key",
                "api_key_assignment",
                "bearer_token",
                "credential_path",
                "private_endpoint",
                "private_model_override",
            },
            {str(item["rule_id"]) for item in findings},
        )
        serialized = repr(findings)
        for sensitive in (api_key, bearer, endpoint, model, credential_path):
            self.assertNotIn(sensitive, serialized)

    def test_detects_environment_and_credential_filenames(self) -> None:
        env_findings = scan_payload(".env.local", b"SAFE=value")
        credential_findings = scan_payload("config/service.credentials.json", b"{}")

        self.assertEqual("env_file", env_findings[0]["rule_id"])
        self.assertEqual("credential_file", credential_findings[0]["rule_id"])

    def test_fixture_allowlist_is_bound_to_path_rule_and_safe_marker(self) -> None:
        fake_key = "sk-" + "testfixtureabcdefghijkl"
        real_key = "sk-" + "liveabcdefghijklmnop"

        self.assertEqual(
            [],
            scan_payload(
                "tests/test_llm_interpreter.py",
                f"value = '{fake_key}'".encode(),
            ),
        )
        self.assertTrue(
            scan_payload(
                "tests/test_llm_interpreter.py",
                f"value = '{real_key}'".encode(),
            )
        )
        self.assertTrue(
            scan_payload(
                "tests/test_unlisted_fixture.py",
                f"value = '{fake_key}'".encode(),
            )
        )

    def test_credential_detector_requires_an_assignment(self) -> None:
        scanner_documentation = (
            b"Detect credential_path references and strings such as "
            b".aws/credentials without treating documentation as a configured path."
        )

        self.assertEqual([], scan_payload("scanner.py", scanner_documentation))

    def test_repository_scan_includes_untracked_nonignored_files(self) -> None:
        secret = "sk-" + "liveabcdefghijklmnop"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tracked.py").write_text("safe = True\n", encoding="utf-8")
            (root / "candidate.py").write_text(
                f"key = '{secret}'\n",
                encoding="utf-8",
            )

            def git_output(
                _repository_root: Path,
                arguments: list[str],
            ) -> bytes:
                if arguments == ["ls-files", "-z"]:
                    return b"tracked.py\0"
                if arguments == [
                    "ls-files",
                    "--others",
                    "--exclude-standard",
                    "-z",
                ]:
                    return b"candidate.py\0"
                if arguments[:2] == ["diff", "--no-ext-diff"]:
                    return b""
                raise AssertionError(arguments)

            with mock.patch.object(
                compliance_module,
                "_git_output",
                side_effect=git_output,
            ):
                report = scan_git_and_artifacts(root, ())

        self.assertEqual(1, report["finding_count"])
        self.assertEqual("candidate.py", report["findings"][0]["path"])


class DerivedVerdictTest(unittest.TestCase):
    def setUp(self) -> None:
        digest = "d" * 64
        self.report = {
            "artifacts": {
                "wheel": {"sha256": digest, "entries": ["package.py"]},
                "sdist": {"sha256": digest, "entries": ["root/package.py"]},
            },
            "archive_blockers": [],
            "metadata": {"license_expressions": [EXPECTED_LICENSE_EXPRESSION]},
            "licenses": [
                {
                    "path": path,
                    "source_sha256": digest,
                    "wheel_sha256": digest,
                    "sdist_sha256": digest,
                }
                for path in REQUIRED_LICENSE_FILES
            ],
            "runtime_data": [
                {
                    "path": path,
                    "wheel_present": True,
                    "sdist_present": True,
                    "source_sha256": digest,
                    "wheel_sha256": digest,
                    "sdist_sha256": digest,
                }
                for path in REQUIRED_RUNTIME_FILES
            ],
            "dependencies": {
                source: sorted(EXPECTED_DIRECT_DISTRIBUTIONS)
                for source in ("expected", "declared", "lock", "metadata", "notices")
            },
            "install_smoke": {"attempted": False},
            "secret_scan": {"findings": []},
        }

    def test_accepts_complete_raw_evidence(self) -> None:
        self.assertEqual([], distribution_report_blockers(self.report))

    def test_rejects_wrong_license_missing_notices_and_runtime_data(self) -> None:
        report = dict(self.report)
        report["metadata"] = {"license_expressions": ["MIT"]}
        report["licenses"] = list(self.report["licenses"])[1:]
        report["runtime_data"] = list(self.report["runtime_data"])[1:]

        codes = {
            str(item["code"]) for item in distribution_report_blockers(report)
        }

        self.assertIn("wrong_metadata_license", codes)
        self.assertIn("missing_license_file", codes)
        self.assertIn("missing_runtime_data_evidence", codes)

    def test_rejects_dependency_drift_and_secret_findings(self) -> None:
        report = dict(self.report)
        dependencies = dict(self.report["dependencies"])
        dependencies["notices"] = ["openai"]
        report["dependencies"] = dependencies
        report["secret_scan"] = {
            "findings": [
                {
                    "path": "wheel/candidate.py",
                    "line": 1,
                    "rule_id": "api_key",
                    "fingerprint": "f" * 64,
                }
            ]
        }

        codes = {
            str(item["code"]) for item in distribution_report_blockers(report)
        }

        self.assertIn("dependency_notice_drift", codes)
        self.assertIn("secret_or_private_config_detected", codes)

    def test_rejects_wheel_only_and_sdist_only_runtime_data(self) -> None:
        for missing_key in ("wheel_present", "sdist_present"):
            with self.subTest(missing_key=missing_key):
                report = dict(self.report)
                runtime_data = [
                    dict(item) for item in self.report["runtime_data"]
                ]
                runtime_data[0][missing_key] = False
                report["runtime_data"] = runtime_data

                codes = {
                    str(item["code"])
                    for item in distribution_report_blockers(report)
                }

                self.assertIn("missing_runtime_data", codes)


if __name__ == "__main__":
    unittest.main()
