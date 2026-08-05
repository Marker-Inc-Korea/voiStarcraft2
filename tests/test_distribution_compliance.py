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
    EXPECTED_BUILD_DISTRIBUTIONS,
    EXPECTED_DIRECT_DISTRIBUTIONS,
    EXPECTED_LICENSE_FILE_SHA256,
    EXPECTED_LICENSE_EXPRESSION,
    EXPECTED_NOTICE_LICENSES,
    EXPECTED_PROJECT_DISTRIBUTIONS,
    REQUIRED_LICENSE_FILES,
    REQUIRED_RUNTIME_FILES,
    ArchiveSnapshot,
    _isolated_venv_builder,
    archive_content_blockers,
    archive_manifest_blockers,
    build_dependencies_from_pyproject,
    declared_dependencies_from_pyproject,
    distribution_report_blockers,
    expected_archive_payloads,
    inspect_wheel,
    scan_git_and_artifacts,
    scan_payload,
)


class DependencyInventoryTest(unittest.TestCase):
    def test_declared_dependencies_ignore_comments_and_preserve_quoted_hashes(
        self,
    ) -> None:
        pyproject = """
[build-system]
requires = ["setuptools>=77.0.3"]
build-backend = "setuptools.build_meta"

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
        self.assertEqual(
            {"setuptools"},
            set(build_dependencies_from_pyproject(pyproject)),
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
            path=Path("voistarcraft2-0.1.0.tar.gz"),
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

    def test_wheel_inspection_rejects_canonical_duplicate_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            wheel_path = Path(temporary) / "candidate.whl"
            with zipfile.ZipFile(wheel_path, "w") as archive:
                archive.writestr("starcraft_commander/runtime_data.py", "safe")
                archive.writestr(
                    "starcraft_commander/./runtime_data.py",
                    "attacker",
                )

            snapshot = inspect_wheel(wheel_path)

        self.assertIn(
            "unsafe_archive_entry",
            {str(item["code"]) for item in snapshot.blockers},
        )

    def test_sdist_rejects_alternate_root_payloads(self) -> None:
        expected_payload = b"expected runtime"
        snapshot = ArchiveSnapshot(
            kind="sdist",
            path=Path("voistarcraft2-0.1.0.tar.gz"),
            digest="c" * 64,
            entries=(),
            files={
                "attacker-9.9/starcraft_commander/runtime_data.py": b"attacker",
                (
                    "voistarcraft2-0.1.0/"
                    "starcraft_commander/runtime_data.py"
                ): expected_payload,
            },
            blockers=(),
        )
        expected = {
            "starcraft_commander/runtime_data.py": (
                compliance_module.sha256_bytes(expected_payload)
            )
        }

        content_codes = {
            str(item["code"]) for item in archive_content_blockers(snapshot)
        }
        manifest_codes = {
            str(item["code"])
            for item in archive_manifest_blockers(snapshot, expected)
        }

        self.assertIn("invalid_archive_root", content_codes)
        self.assertIn("invalid_archive_root", manifest_codes)

    def test_wheel_rejects_alternate_dist_info_namespace(self) -> None:
        expected_payload = b"expected runtime"
        snapshot = ArchiveSnapshot(
            kind="wheel",
            path=Path("voistarcraft2-0.1.0-py3-none-any.whl"),
            digest="c" * 64,
            entries=(),
            files={
                "starcraft_commander/runtime_data.py": expected_payload,
                "attacker-9.9.dist-info/RECORD": b"attacker",
            },
            blockers=(),
        )
        expected = {
            "starcraft_commander/runtime_data.py": (
                compliance_module.sha256_bytes(expected_payload)
            )
        }

        content_codes = {
            str(item["code"]) for item in archive_content_blockers(snapshot)
        }
        manifest_codes = {
            str(item["code"])
            for item in archive_manifest_blockers(snapshot, expected)
        }

        self.assertIn("unexpected_archive_entry", content_codes)
        self.assertIn("unexpected_archive_payload", manifest_codes)

    def test_sdist_rejects_alternate_egg_info_namespace(self) -> None:
        expected_payload = b"expected runtime"
        snapshot = ArchiveSnapshot(
            kind="sdist",
            path=Path("voistarcraft2-0.1.0.tar.gz"),
            digest="c" * 64,
            entries=(),
            files={
                (
                    "voistarcraft2-0.1.0/"
                    "starcraft_commander/runtime_data.py"
                ): expected_payload,
                "voistarcraft2-0.1.0/attacker.egg-info/PKG-INFO": b"attacker",
            },
            blockers=(),
        )
        expected = {
            "starcraft_commander/runtime_data.py": (
                compliance_module.sha256_bytes(expected_payload)
            )
        }

        content_codes = {
            str(item["code"]) for item in archive_content_blockers(snapshot)
        }
        manifest_codes = {
            str(item["code"])
            for item in archive_manifest_blockers(snapshot, expected)
        }

        self.assertIn("unexpected_archive_entry", content_codes)
        self.assertIn("unexpected_archive_payload", manifest_codes)

    def test_archive_manifest_rejects_missing_extra_and_modified_payloads(
        self,
    ) -> None:
        expected = {
            "starcraft_commander/runtime_data.py": compliance_module.sha256_bytes(
                b"expected runtime"
            ),
            "integrations/micromachine/voi_policy_blackboard.hpp": (
                compliance_module.sha256_bytes(b"expected header")
            ),
        }
        snapshot = ArchiveSnapshot(
            kind="wheel",
            path=Path("candidate.whl"),
            digest="c" * 64,
            entries=(),
            files={
                "starcraft_commander/runtime_data.py": b"modified runtime",
                "starcraft_commander/unapproved_payload.py": b"attacker",
            },
            blockers=(),
        )

        blockers = archive_manifest_blockers(snapshot, expected)

        self.assertEqual(
            {
                "archive_payload_mismatch",
                "missing_archive_entry",
                "unexpected_archive_payload",
            },
            {str(item["code"]) for item in blockers},
        )

    def test_expected_archive_manifest_uses_only_git_blobs(self) -> None:
        head = "a" * 40
        runtime_blob = "b" * 40
        tree = (
            f"100644 blob {runtime_blob}\t"
            "starcraft_commander/runtime_data.py\0"
            f"100644 blob {'d' * 40}\tdocs/private.md\0"
        ).encode()

        def git_output(_root: Path, arguments: list[str]) -> bytes:
            if arguments == ["ls-tree", "-r", "-z", "--full-tree", head]:
                return tree
            if arguments == ["cat-file", "blob", runtime_blob]:
                return b"committed runtime"
            raise AssertionError(arguments)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            untracked = root / "starcraft_commander" / "untracked_on_disk.py"
            untracked.parent.mkdir()
            untracked.write_text("attacker = True\n")
            with mock.patch.object(
                compliance_module,
                "_git_output",
                side_effect=git_output,
            ):
                manifests = expected_archive_payloads(root, head)

        self.assertEqual(
            compliance_module.sha256_bytes(b"committed runtime"),
            manifests["wheel"]["starcraft_commander/runtime_data.py"],
        )
        self.assertNotIn(
            "starcraft_commander/untracked_on_disk.py",
            manifests["wheel"],
        )
        self.assertNotIn("docs/private.md", manifests["sdist"])


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

    def test_detects_generic_myproxy_model_and_endpoint_assignments(self) -> None:
        model_key = "VOI_MYPROXY_" + "MODEL"
        endpoint_key = "VOI_MYPROXY_OPENAI_BASE_" + "URL"
        payload = (
            f"export {model_key}=internal-deployment-alpha\n"
            f"{endpoint_key}=https://proxy.corp.example:8443/v1\n"
        ).encode()

        findings = scan_payload("config.py", payload)

        self.assertEqual(
            {"private_endpoint", "private_model_override"},
            {str(item["rule_id"]) for item in findings},
        )

    def test_private_config_scanner_ignores_names_and_empty_constants(self) -> None:
        endpoint_key = "MYPROXY_OPENAI_BASE_" + "URL"
        payload = (
            f'{endpoint_key}_ENV_VAR: Final[str] = "{endpoint_key}"\n'
            f'{endpoint_key}: Final[str] = ""\n'
        ).encode()

        self.assertEqual([], scan_payload("llm_interpreter.py", payload))

    def test_detects_typed_and_mapping_private_config_assignments(self) -> None:
        model_key = "DEFAULT_MYPROXY_" + "MODEL"
        endpoint_key = "MYPROXY_OPENAI_BASE_" + "URL"
        payload = (
            f'{model_key}: Final[str] = "internal-deployment-alpha"\n'
            f'"{endpoint_key}": "https://proxy.corp.example:8443/v1"\n'
        ).encode()

        self.assertEqual(
            {"private_endpoint", "private_model_override"},
            {
                str(item["rule_id"])
                for item in scan_payload("private-config.yaml", payload)
            },
        )

    def test_detects_environment_and_credential_filenames(self) -> None:
        env_findings = scan_payload(".env.local", b"SAFE=value")
        credential_findings = scan_payload("config/service.credentials.json", b"{}")
        netrc_findings = scan_payload(".netrc", b"machine private.example")
        netrc_path = "/home/user/" + ".netrc"
        aws_path = "/home/user/.aws/" + "credentials"
        unquoted_findings = scan_payload(
            "config.py",
            (
                f"credential_path={netrc_path}\n"
                f"AWS_SHARED_CREDENTIALS_FILE={aws_path}\n"
            ).encode(),
        )

        self.assertEqual("env_file", env_findings[0]["rule_id"])
        self.assertEqual("credential_file", credential_findings[0]["rule_id"])
        self.assertEqual("credential_file", netrc_findings[0]["rule_id"])
        self.assertEqual(
            ["credential_path", "credential_path"],
            [str(item["rule_id"]) for item in unquoted_findings],
        )

    def test_fixture_allowlist_is_bound_to_path_rule_and_safe_marker(self) -> None:
        fake_key = "sk-" + "testfixtureabcdefghijkl"
        real_key = "sk-" + "liveabcdefghijklmnop"
        embedded_marker_key = "sk-" + "prodtestabcdefghijkl"

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
        self.assertTrue(
            scan_payload(
                "tests/test_llm_interpreter.py",
                f"value = '{embedded_marker_key}'".encode(),
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
        wheel_root = "voistarcraft2-0.1.0.dist-info"
        wheel_generated = {
            *(f"{wheel_root}/{name}" for name in ("METADATA", "RECORD", "WHEEL")),
            f"{wheel_root}/top_level.txt",
            f"{wheel_root}/licenses/LICENSE",
            f"{wheel_root}/licenses/LICENSES/AGPL-3.0-or-later.txt",
            f"{wheel_root}/licenses/THIRD_PARTY_NOTICES.md",
        }
        wheel_file_manifest = {
            "starcraft_commander/runtime_data.py": digest,
            **{entry: digest for entry in wheel_generated},
        }
        sdist_root = "voistarcraft2-0.1.0"
        sdist_egg_info = f"{sdist_root}/voiStarcraft2.egg-info"
        sdist_generated = {
            f"{sdist_root}/PKG-INFO",
            f"{sdist_root}/setup.cfg",
            *(
                f"{sdist_egg_info}/{name}"
                for name in (
                    "PKG-INFO",
                    "SOURCES.txt",
                    "dependency_links.txt",
                    "requires.txt",
                    "top_level.txt",
                )
            ),
        }
        sdist_source = (
            f"{sdist_root}/starcraft_commander/runtime_data.py"
        )
        sdist_file_manifest = {
            sdist_source: digest,
            **{entry: digest for entry in sdist_generated},
        }
        sdist_directories = [sdist_root, sdist_egg_info]
        self.report = {
            "repository": {
                phase: {
                    "ok": True,
                    "head": digest[:40],
                    "tree": digest[:40],
                    "dirty_entries": [],
                    "source_root_matches": True,
                    "repository_root": "/release",
                    "source_root": "/release",
                }
                for phase in ("before", "after")
            },
            "artifacts": {
                "wheel": {
                    "filename": "voistarcraft2-0.1.0-py3-none-any.whl",
                    "sha256": digest,
                    "entry_count": len(wheel_file_manifest),
                    "entries": list(wheel_file_manifest),
                    "file_manifest": wheel_file_manifest,
                    "directory_entries": [],
                },
                "sdist": {
                    "filename": "voistarcraft2-0.1.0.tar.gz",
                    "sha256": digest,
                    "entry_count": (
                        len(sdist_file_manifest) + len(sdist_directories)
                    ),
                    "entries": [
                        *sdist_file_manifest,
                        *sdist_directories,
                    ],
                    "file_manifest": sdist_file_manifest,
                    "directory_entries": sdist_directories,
                },
            },
            "archive_blockers": [],
            "archive_manifests": {
                kind: {"starcraft_commander/runtime_data.py": digest}
                for kind in ("wheel", "sdist")
            },
            "metadata": {"license_expressions": [EXPECTED_LICENSE_EXPRESSION]},
            "licenses": [
                {
                    "path": path,
                    "expected_sha256": EXPECTED_LICENSE_FILE_SHA256[path],
                    "source_sha256": EXPECTED_LICENSE_FILE_SHA256[path],
                    "wheel_sha256": EXPECTED_LICENSE_FILE_SHA256[path],
                    "sdist_sha256": EXPECTED_LICENSE_FILE_SHA256[path],
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
                "expected": sorted(EXPECTED_DIRECT_DISTRIBUTIONS),
                "expected_project": sorted(EXPECTED_PROJECT_DISTRIBUTIONS),
                "expected_build": sorted(EXPECTED_BUILD_DISTRIBUTIONS),
                "declared": sorted(EXPECTED_PROJECT_DISTRIBUTIONS),
                "build_system": sorted(EXPECTED_BUILD_DISTRIBUTIONS),
                "lock": sorted(EXPECTED_PROJECT_DISTRIBUTIONS),
                "metadata": sorted(EXPECTED_PROJECT_DISTRIBUTIONS),
                "notices": sorted(EXPECTED_DIRECT_DISTRIBUTIONS),
                "notice_licenses": dict(sorted(EXPECTED_NOTICE_LICENSES.items())),
            },
            "install_smoke": {
                "attempted": True,
                "returncode": 0,
                "payload": {
                    "license_expression": EXPECTED_LICENSE_EXPRESSION,
                    "runtime_data_loaded": True,
                    "target_runtime_data_loaded": True,
                },
            },
            "secret_scan": {
                "scanned_file_count": 10,
                "finding_count": 0,
                "findings": [],
            },
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

    def test_rejects_dirty_repository_and_skipped_install_smoke(self) -> None:
        report = dict(self.report)
        repository = {
            phase: dict(value)
            for phase, value in self.report["repository"].items()
        }
        repository["before"]["ok"] = False
        repository["before"]["dirty_entries"] = [" M README.md"]
        report["repository"] = repository
        report["install_smoke"] = {"attempted": False}

        codes = {
            str(item["code"]) for item in distribution_report_blockers(report)
        }

        self.assertIn("repository_not_clean_commit", codes)
        self.assertIn("isolated_install_not_attempted", codes)

    def test_rejects_repository_identity_drift_and_inconsistent_raw_state(
        self,
    ) -> None:
        report = dict(self.report)
        repository = {
            phase: dict(value)
            for phase, value in self.report["repository"].items()
        }
        repository["before"]["dirty_entries"] = [" M pyproject.toml"]
        repository["before"]["ok"] = True
        repository["after"]["head"] = "e" * 40
        repository["after"]["tree"] = "f" * 40
        report["repository"] = repository

        codes = {
            str(item["code"]) for item in distribution_report_blockers(report)
        }

        self.assertIn("repository_not_clean_commit", codes)
        self.assertIn("repository_identity_changed", codes)

    def test_rejects_mutated_license_and_notice_license_assignment(self) -> None:
        report = dict(self.report)
        licenses = [dict(item) for item in self.report["licenses"]]
        licenses[0]["source_sha256"] = "e" * 64
        licenses[0]["wheel_sha256"] = "e" * 64
        licenses[0]["sdist_sha256"] = "e" * 64
        report["licenses"] = licenses
        dependencies = dict(self.report["dependencies"])
        notice_licenses = dict(dependencies["notice_licenses"])
        notice_licenses["openai"] = "MIT"
        dependencies["notice_licenses"] = notice_licenses
        report["dependencies"] = dependencies

        codes = {
            str(item["code"]) for item in distribution_report_blockers(report)
        }

        self.assertIn("license_content_mismatch", codes)
        self.assertIn("dependency_notice_drift", codes)

    def test_rejects_missing_secret_scan_evidence(self) -> None:
        report = dict(self.report)
        report.pop("secret_scan")

        codes = {
            str(item["code"]) for item in distribution_report_blockers(report)
        }

        self.assertIn("invalid_secret_scan_evidence", codes)

    def test_rejects_missing_or_malformed_archive_evidence(self) -> None:
        for field in ("archive_blockers", "archive_manifests"):
            with self.subTest(field=field):
                report = dict(self.report)
                report.pop(field)

                codes = {
                    str(item["code"])
                    for item in distribution_report_blockers(report)
                }

                self.assertIn(
                    (
                        "invalid_archive_blocker_evidence"
                        if field == "archive_blockers"
                        else "invalid_archive_manifest_evidence"
                    ),
                    codes,
                )

        report = dict(self.report)
        artifacts = {
            kind: dict(value)
            for kind, value in self.report["artifacts"].items()
        }
        wheel_manifest = dict(artifacts["wheel"]["file_manifest"])
        wheel_manifest["starcraft_commander/runtime_data.py"] = "e" * 64
        artifacts["wheel"]["file_manifest"] = wheel_manifest
        report["artifacts"] = artifacts

        codes = {
            str(item["code"]) for item in distribution_report_blockers(report)
        }

        self.assertIn("archive_payload_mismatch", codes)

    def test_rejects_unmanifested_entries_and_missing_generated_files(
        self,
    ) -> None:
        report = dict(self.report)
        artifacts = {
            kind: dict(value)
            for kind, value in self.report["artifacts"].items()
        }
        wheel_entries = list(artifacts["wheel"]["entries"])
        wheel_entries.append("starcraft_commander/attacker_payload.py")
        artifacts["wheel"]["entries"] = wheel_entries
        artifacts["wheel"]["entry_count"] = len(wheel_entries)
        report["artifacts"] = artifacts

        codes = {
            str(item["code"]) for item in distribution_report_blockers(report)
        }

        self.assertIn("artifact_entry_manifest_mismatch", codes)

        missing_generated = {
            "wheel": "voistarcraft2-0.1.0.dist-info/RECORD",
            "sdist": "voistarcraft2-0.1.0/PKG-INFO",
        }
        for kind, entry in missing_generated.items():
            with self.subTest(kind=kind):
                report = dict(self.report)
                artifacts = {
                    name: dict(value)
                    for name, value in self.report["artifacts"].items()
                }
                file_manifest = dict(artifacts[kind]["file_manifest"])
                file_manifest.pop(entry)
                artifacts[kind]["file_manifest"] = file_manifest
                report["artifacts"] = artifacts

                codes = {
                    str(item["code"])
                    for item in distribution_report_blockers(report)
                }

                self.assertIn("artifact_entry_manifest_mismatch", codes)
                self.assertIn("missing_generated_archive_evidence", codes)

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
