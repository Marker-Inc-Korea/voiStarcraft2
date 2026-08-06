"""Tests for release distribution and private-configuration compliance."""

from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
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
requires = ["setuptools==82.0.1"]
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

    def test_smoke_removes_pythonpath_from_all_isolated_processes(self) -> None:
        successful_result = mock.Mock(returncode=0, stdout="", stderr="")
        installed_result = mock.Mock(
            returncode=0,
            stdout=(
                '{"license_expression": "'
                + EXPECTED_LICENSE_EXPRESSION
                + '", "runtime_data_loaded": true}'
            ),
            stderr="",
        )
        target_result = mock.Mock(
            returncode=0,
            stdout='{"loaded": true}',
            stderr="",
        )
        builder = mock.Mock()
        with (
            mock.patch.dict(
                os.environ,
                {"PYTHONPATH": "/private/source-tree"},
            ),
            mock.patch.object(
                compliance_module,
                "_isolated_venv_builder",
                return_value=builder,
            ),
            mock.patch.object(
                compliance_module.subprocess,
                "run",
                side_effect=(
                    successful_result,
                    installed_result,
                    successful_result,
                    target_result,
                ),
            ) as run,
        ):
            result = compliance_module.isolated_wheel_install_smoke(
                Path("candidate.whl")
            )

        self.assertEqual(0, result["returncode"])
        self.assertTrue(result["payload"]["target_runtime_data_loaded"])
        self.assertEqual(4, run.call_count)
        for call in run.call_args_list:
            environment = call.kwargs.get("env")
            self.assertIsNotNone(environment)
            self.assertNotIn("PYTHONPATH", environment)


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

    def test_wheel_inspection_rejects_nonportable_and_colliding_entries(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            wheel_path = Path(temporary) / "candidate.whl"
            with zipfile.ZipFile(wheel_path, "w") as archive:
                archive.writestr(
                    "starcraft_commander/runtime_data.py",
                    "safe",
                )
                archive.writestr(
                    "STARCRAFT_COMMANDER/RUNTIME_DATA.PY",
                    "case collision",
                )
                archive.writestr(
                    "starcraft_commander/runtime_data.py:payload.py",
                    "alternate stream",
                )
                archive.writestr(
                    "starcraft_commander/caf\u00e9.py",
                    "normalized",
                )
                archive.writestr(
                    "starcraft_commander/cafe\u0301.py",
                    "decomposed",
                )

            snapshot = inspect_wheel(wheel_path)

        codes = [str(item["code"]) for item in snapshot.blockers]
        reasons = {str(item.get("reason")) for item in snapshot.blockers}
        self.assertEqual(2, codes.count("duplicate_archive_entry"))
        self.assertIn("unsafe_archive_entry", codes)
        self.assertIn("non_portable_component", reasons)

    def test_wheel_inspection_rejects_directory_symlink_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            wheel_path = Path(temporary) / "candidate.whl"
            link = zipfile.ZipInfo("starcraft_commander/")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(wheel_path, "w") as archive:
                archive.writestr(link, "target")

            snapshot = inspect_wheel(wheel_path)

        self.assertIn(
            "archive_link_entry",
            {str(item["code"]) for item in snapshot.blockers},
        )
        self.assertEqual((), snapshot.directories)

    def test_wheel_inspection_rejects_non_regular_unix_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            wheel_path = Path(temporary) / "candidate.whl"
            fifo = zipfile.ZipInfo("starcraft_commander/runtime_data.py")
            fifo.create_system = 3
            fifo.external_attr = (stat.S_IFIFO | 0o644) << 16
            with zipfile.ZipFile(wheel_path, "w") as archive:
                archive.writestr(fifo, "attacker")

            snapshot = inspect_wheel(wheel_path)

        self.assertIn(
            "archive_non_regular_entry",
            {str(item["code"]) for item in snapshot.blockers},
        )
        self.assertEqual({}, snapshot.files)

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

    def test_archive_directories_follow_policy_and_secret_scan(self) -> None:
        secret_directory = "sk-" + "liveabcdefghijklmnop"
        snapshot = ArchiveSnapshot(
            kind="wheel",
            path=Path("voistarcraft2-0.1.0-py3-none-any.whl"),
            digest="c" * 64,
            entries=(),
            files={},
            blockers=(),
            directories=(
                "tests",
                ".env.private",
                secret_directory,
            ),
        )

        blockers = archive_content_blockers(snapshot)
        codes = {str(item["code"]) for item in blockers}
        reasons = {str(item.get("reason")) for item in blockers}

        self.assertIn("denied_archive_entry", codes)
        self.assertIn("sensitive_archive_directory", codes)
        self.assertIn("denied_component:tests", reasons)
        self.assertIn("local_environment_file", reasons)

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

    def test_repository_state_rejects_commit_and_blob_replacements(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def git(*arguments: str) -> str:
                result = subprocess.run(
                    ["git", *arguments],
                    cwd=root,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                return result.stdout.strip()

            git("init", "-q")
            git("config", "user.email", "compliance@example.invalid")
            git("config", "user.name", "Compliance Test")
            tracked = root / "tracked.py"
            tracked.write_text("value = 'original'\n", encoding="utf-8")
            git("add", "tracked.py")
            git("commit", "-qm", "original")
            target_commit = git("rev-parse", "HEAD")
            target_tree = git("rev-parse", "HEAD^{tree}")
            target_blob = git("rev-parse", "HEAD:tracked.py")

            tracked.write_text("value = 'replacement'\n", encoding="utf-8")
            git("add", "tracked.py")
            git("commit", "-qm", "replacement")
            replacement_commit = git("rev-parse", "HEAD")
            replacement_blob = git("rev-parse", "HEAD:tracked.py")
            git("checkout", "--detach", "-q", target_commit)

            replacements = (
                (target_commit, replacement_commit),
                (target_blob, replacement_blob),
            )
            for original, replacement in replacements:
                with self.subTest(original=original):
                    git("replace", original, replacement)

                    state = compliance_module.repository_state_evidence(
                        root,
                        root,
                    )

                    self.assertFalse(state["ok"])
                    self.assertEqual(target_commit, state["head"])
                    self.assertEqual(target_tree, state["tree"])
                    self.assertEqual(
                        [f"refs/replace/{original}"],
                        state["replacement_refs"],
                    )
                    git("replace", "-d", original)


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

    def test_detects_myproxy_host_port_docker_and_kubernetes_assignments(
        self,
    ) -> None:
        host_key = "VOI_MYPROXY_" + "HOST"
        port_key = "VOI_MYPROXY_" + "PORT"
        payload = (
            f"{host_key}=10.20.30.40\n"
            f"ENV {port_key} 8443\n"
            f"- name: {host_key}\n"
            "  value: proxy.corp.example\n"
            f"- name: {port_key}\n"
            '  value: "9443"\n'
        ).encode()

        findings = scan_payload("deployment.yaml", payload)

        endpoint_findings = [
            item
            for item in findings
            if item["rule_id"] == "private_endpoint"
        ]
        self.assertEqual(4, len(endpoint_findings))
        self.assertEqual(
            {"private_endpoint"},
            {str(item["rule_id"]) for item in endpoint_findings},
        )

    def test_detects_docker_and_kubernetes_layout_variants(self) -> None:
        host_key = "VOI_MYPROXY_" + "HOST"
        payload = (
            f"ENV SAFE=x {host_key}=10.20.30.40\n"
            f"- {{name: {host_key}, value: 10.20.30.40}}\n"
            "- value: 10.20.30.40\n"
            f"  name: {host_key}\n"
        ).encode()

        findings = scan_payload("deployment.yaml", payload)

        endpoint_findings = [
            item
            for item in findings
            if item["rule_id"] == "private_endpoint"
        ]
        self.assertEqual(3, len(endpoint_findings))
        self.assertEqual(
            {"private_endpoint"},
            {str(item["rule_id"]) for item in endpoint_findings},
        )
        unrelated_value = (
            f"- name: {host_key}\n"
            "- name: SAFE\n"
            "  value: 10.20.30.40\n"
        ).encode()
        self.assertEqual([], scan_payload("deployment.yaml", unrelated_value))

    def test_detects_continued_docker_env_assignments(self) -> None:
        host_key = "VOI_MYPROXY_" + "HOST"
        model_key = "VOI_MYPROXY_" + "MODEL"
        payloads = (
            (
                "Dockerfile",
                (
                    "ENV \\\n"
                    f"  {host_key} 10.20.30.40\n"
                ).encode(),
                "private_endpoint",
            ),
            (
                "Dockerfile.windows",
                (
                    "# escape=`\n"
                    "ENV `\n"
                    f"  {model_key}=private-model\n"
                ).encode(),
                "private_model_override",
            ),
        )
        for path, payload, expected_rule in payloads:
            with self.subTest(path=path):
                findings = scan_payload(
                    path,
                    payload,
                    allow_safe_fixtures=False,
                )

                self.assertEqual(1, len(findings))
                self.assertEqual(expected_rule, findings[0]["rule_id"])

    def test_late_docker_escape_comments_do_not_change_continuations(
        self,
    ) -> None:
        host_key = "VOI_MYPROXY_" + "HOST"
        model_key = "VOI_MYPROXY_" + "MODEL"
        payloads = (
            (
                "FROM scratch\n"
                "# escape=`\n"
                "ENV \\\n"
                f"  {host_key} 10.20.30.40\n"
            ).encode(),
            (
                "\n"
                "# escape=`\n"
                "ARG \\\n"
                f"  {model_key}=private-model\n"
            ).encode(),
            (
                "# ordinary comment\n"
                "# escape=`\n"
                "ENV \\\n"
                f"  {model_key}=private-model\n"
            ).encode(),
            (
                "FROM scratch\n"
                "# escape=\\\n"
                "ENV \\\n"
                "# continued instruction comment\n"
                "\n"
                f"  {host_key} 10.20.30.40\n"
            ).encode(),
        )
        expected_rules = (
            "private_endpoint",
            "private_model_override",
            "private_model_override",
            "private_endpoint",
        )
        for payload, expected_rule in zip(
            payloads,
            expected_rules,
            strict=True,
        ):
            with self.subTest(payload=payload[:30]):
                findings = scan_payload(
                    "Dockerfile",
                    payload,
                    allow_safe_fixtures=False,
                )

                self.assertEqual(1, len(findings))
                self.assertEqual(expected_rule, findings[0]["rule_id"])

    def test_detects_json_and_quoted_yaml_kubernetes_env(self) -> None:
        host_key = "VOI_MYPROXY_" + "HOST"
        port_key = "VOI_MYPROXY_" + "PORT"
        model_key = "VOI_MYPROXY_" + "MODEL"
        payload = (
            '{"spec":{"containers":[{"env":[\n'
            f'{{"name":"{host_key}","value":"10.20.30.40"}},\n'
            f'{{"value":"8443","name":"{port_key}"}},\n'
            f'{{"name":"{model_key}","value":"private-model"}}\n'
            "] }]}}\n"
            f'- "name": "{host_key}"\n'
            '  "value": "proxy.corp.example"\n'
        ).encode()

        findings = scan_payload(
            "deployment.json",
            payload,
            allow_safe_fixtures=False,
        )

        self.assertEqual(5, len(findings))
        self.assertEqual(
            {
                "json_parse_failed",
                "private_endpoint",
                "private_model_override",
            },
            {str(item["rule_id"]) for item in findings},
        )

    def test_detects_unicode_escaped_kubernetes_env(self) -> None:
        payloads = (
            b'{"na\\u006de":"VOI_MYPROXY_HOST","value":"10.20.30.40"}',
            b'{"name":"VOI_MYPROXY_H\\u004fST","value":"10.20.30.40"}',
            b'{"name":"VOI_MYPR\\x4fXY_HOST","value":"10.20.30.40"}',
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                findings = scan_payload(
                    "deployment.json",
                    payload,
                    allow_safe_fixtures=False,
                )

                self.assertIn(
                    "private_endpoint",
                    {str(item["rule_id"]) for item in findings},
                )

    def test_detects_utf16_json_and_semantic_toml_configuration(self) -> None:
        host_key = "VOI_MYPROXY_" + "HOST"
        model_key = "VOI_MYPROXY_" + "MODEL"
        json_payload = (
            '{"env":[{"name":"'
            + host_key
            + '","value":"10.20.30.40"}]}'
        ).encode("utf-16")
        toml_payload = (
            f'"{host_key}" = "10.20.30.40"\n'
            f'"{model_key}" = """private-\\\n    model"""\n'
        ).encode()

        json_findings = scan_payload(
            "deployment.json",
            json_payload,
            allow_safe_fixtures=False,
        )
        toml_findings = scan_payload(
            "private.toml",
            toml_payload,
            allow_safe_fixtures=False,
        )

        self.assertIn(
            "private_endpoint",
            {str(item["rule_id"]) for item in json_findings},
        )
        self.assertEqual(
            {"private_endpoint", "private_model_override"},
            {str(item["rule_id"]) for item in toml_findings},
        )

    def test_json_and_toml_parser_failures_are_blocking_findings(self) -> None:
        json_findings = scan_payload(
            "private.json",
            b'{"safe": true',
            allow_safe_fixtures=False,
        )
        toml_findings = scan_payload(
            "private.toml",
            b"safe = [",
            allow_safe_fixtures=False,
        )

        self.assertIn(
            "json_parse_failed",
            {str(item["rule_id"]) for item in json_findings},
        )
        self.assertIn(
            "toml_parse_failed",
            {str(item["rule_id"]) for item in toml_findings},
        )
        with mock.patch.object(compliance_module, "_toml", None):
            unavailable = scan_payload(
                "private.toml",
                b"safe = true\n",
                allow_safe_fixtures=False,
            )
        self.assertEqual("toml_parser_unavailable", unavailable[0]["rule_id"])

    def test_detects_executable_and_standard_secret_forms(self) -> None:
        host_key = "VOI_MYPROXY_" + "HOST"
        api_prefix = "sk-"
        aws_access_key = "AKIA" + ("A" * 16)
        aws_secret = "secret" + "abcdefghijklmnopqrstuv"
        private_key_marker = "-----BEGIN " + "PRIVATE KEY-----"
        payload = (
            "RUN if true; then export "
            f"{host_key}=10.20.30.40; fi\n"
            f'token = "{api_prefix}" "liveabcdefghijklmnop"\n'
            f"AWS_ACCESS_KEY_ID={aws_access_key}\n"
            f"AWS_SECRET_ACCESS_KEY={aws_secret}\n"
            f"{private_key_marker}\n"
        ).encode()

        findings = scan_payload(
            "Dockerfile",
            payload,
            allow_safe_fixtures=False,
        )

        self.assertEqual(
            {
                "api_key",
                "aws_access_key_id",
                "private_endpoint",
                "private_key",
                "secret_assignment",
            },
            {str(item["rule_id"]) for item in findings},
        )
        env_directory = scan_payload(
            "config/.env.production/settings.toml",
            b"safe = true\n",
            allow_safe_fixtures=False,
        )
        self.assertIn(
            "env_file",
            {str(item["rule_id"]) for item in env_directory},
        )

    def test_detects_env_calls_cli_myproxy_and_generic_api_keys(self) -> None:
        endpoint_key = "VOI_MYPROXY_OPENAI_BASE_" + "URL"
        model_key = "VOI_MYPROXY_" + "MODEL"
        generic_api_key = "SERVICE_" + "API_KEY"
        payload = (
            f'os.putenv("{endpoint_key}", "https://private.example/v1")\n'
            f'os.environ.setdefault("{model_key}", "private-model")\n'
            "commander --provider myproxy "
            "--base-url https://proxy.corp.example/v1 "
            "--model internal-deployment-alpha\n"
            f"{generic_api_key}=abcdefghijklmnopqrstuvwx\n"
        ).encode()

        findings = scan_payload(
            "launch.sh",
            payload,
            allow_safe_fixtures=False,
        )

        self.assertEqual(
            {
                "api_key_assignment",
                "private_endpoint",
                "private_model_override",
            },
            {str(item["rule_id"]) for item in findings},
        )

    def test_detects_multiline_env_calls_and_all_myproxy_cli_aliases(
        self,
    ) -> None:
        endpoint_key = "VOI_MYPROXY_OPENAI_BASE_" + "URL"
        model_key = "VOI_MYPROXY_" + "MODEL"
        endpoint = "https://private." + "example/v1"
        model = "internal-" + "deployment-alpha"
        aliases = (
            "my" + "proxy",
            "pro" + "xy",
            "nomada" + "mas",
            "my-" + "proxy",
        )
        for alias in aliases:
            with self.subTest(alias=alias):
                payload = (
                    "os.putenv(\n"
                    f'    "{endpoint_key}",\n'
                    f'    "{endpoint}",\n'
                    ")\n"
                    "os.environ.setdefault(\n"
                    f'    "{model_key}",\n'
                    f'    "{model}",\n'
                    ")\n"
                    f"commander --provider={alias} \\\n"
                    f"    --base-url {endpoint} \\\n"
                    f"    --model={model}\n"
                ).encode()

                findings = scan_payload(
                    "launch.sh",
                    payload,
                    allow_safe_fixtures=False,
                )

                self.assertEqual(
                    {"private_endpoint", "private_model_override"},
                    {str(item["rule_id"]) for item in findings},
                )

    def test_detects_myproxy_python_argument_lists(self) -> None:
        alias = "nomada" + "mas"
        endpoint = "https://proxy." + "corp.example/v1"
        model = "private-" + "deployment"
        payload = (
            'subprocess.run([\n'
            "    sys.executable,\n"
            '    "commander",\n'
            '    "--provider",\n'
            f'    "{alias}",\n'
            '    "--openai-base-url",\n'
            f'    "{endpoint}",\n'
            '    "--model",\n'
            f'    "{model}",\n'
            "    extra_arg,\n"
            "])\n"
        ).encode()

        findings = scan_payload(
            "launcher.py",
            payload,
            allow_safe_fixtures=False,
        )

        self.assertEqual(
            {"private_endpoint", "private_model_override"},
            {str(item["rule_id"]) for item in findings},
        )

    def test_detects_composed_myproxy_python_argument_lists(self) -> None:
        alias = "my" + "proxy"
        endpoint = "https://proxy." + "corp.example/v1"
        model = "private-" + "deployment"
        payloads = {
            "starred": (
                f'provider_args = ["--provider", "{alias}"]\n'
                "args = [\n"
                '    "commander",\n'
                "    *provider_args,\n"
                '    "--openai-base-url",\n'
                f'    "{endpoint}",\n'
                '    "--model",\n'
                f'    "{model}",\n'
                "]\n"
            ),
            "augmented": (
                'args = ["commander"]\n'
                f'args += ["--provider", "{alias}"]\n'
                "args += [\n"
                '    "--openai-base-url",\n'
                f'    "{endpoint}",\n'
                '    "--model",\n'
                f'    "{model}",\n'
                "]\n"
            ),
            "extended": (
                'args = ["commander"]\n'
                f'args.extend(["--provider", "{alias}"])\n'
                "args.extend([\n"
                '    "--openai-base-url",\n'
                f'    "{endpoint}",\n'
                '    "--model",\n'
                f'    "{model}",\n'
                "])\n"
            ),
            "constant-provider": (
                f'provider = "{alias}"\n'
                "args = [\n"
                '    "commander",\n'
                '    "--provider",\n'
                "    provider,\n"
                '    "--openai-base-url",\n'
                f'    "{endpoint}",\n'
                '    "--model",\n'
                f'    "{model}",\n'
                "]\n"
            ),
            "compound-statement": (
                'args = ["commander"]\n'
                "if enabled:\n"
                f'    args += ["--provider", "{alias}"]\n'
                "else:\n"
                "    args = make_args()\n"
                "args.extend([\n"
                '    "--openai-base-url",\n'
                f'    "{endpoint}",\n'
                '    "--model",\n'
                f'    "{model}",\n'
                "])\n"
            ),
            "try-except": (
                'args = ["commander"]\n'
                "try:\n"
                f'    args += ["--provider", "{alias}"]\n'
                "except Exception:\n"
                "    args = make_args()\n"
                "args += [\n"
                '    "--openai-base-url",\n'
                f'    "{endpoint}",\n'
                '    "--model",\n'
                f'    "{model}",\n'
                "]\n"
            ),
            "call-before-rebind": (
                f'provider = "{alias}"\n'
                "\n"
                "def launch():\n"
                "    args = [\n"
                '        "commander",\n'
                '        "--provider",\n'
                "        provider,\n"
                '        "--openai-base-url",\n'
                f'        "{endpoint}",\n'
                '        "--model",\n'
                f'        "{model}",\n'
                "    ]\n"
                "\n"
                "launch()\n"
                "provider = resolve_provider()\n"
            ),
            "destructured-constant": (
                "provider, endpoint, model = (\n"
                f'    "{alias}",\n'
                f'    "{endpoint}",\n'
                f'    "{model}",\n'
                ")\n"
                "args = [\n"
                '    "commander",\n'
                '    "--provider",\n'
                "    provider,\n"
                '    "--openai-base-url",\n'
                "    endpoint,\n"
                '    "--model",\n'
                "    model,\n"
                "]\n"
            ),
            "multiline-lambda": (
                "launch = lambda: run([\n"
                '    "commander",\n'
                '    "--provider",\n'
                f'    "{alias}",\n'
                '    "--openai-base-url",\n'
                f'    "{endpoint}",\n'
                '    "--model",\n'
                f'    "{model}",\n'
                "])\n"
            ),
        }

        for name, payload in payloads.items():
            with self.subTest(name=name):
                findings = scan_payload(
                    "launcher.py",
                    payload.encode(),
                    allow_safe_fixtures=False,
                )

                self.assertEqual(
                    {"private_endpoint", "private_model_override"},
                    {str(item["rule_id"]) for item in findings},
                )

    def test_python_cli_reconstruction_is_bounded(self) -> None:
        alias = "my" + "proxy"
        model = "private-" + "deployment"
        payload = (
            f'args = ["commander", "--provider", "{alias}", '
            f'"--model", "{model}"]\n'
            + "args += args\n" * 18
        )

        reconstructed, failure = (
            compliance_module._python_cli_argument_text(
                "launcher.py",
                payload,
            )
        )
        findings = scan_payload(
            "launcher.py",
            payload.encode(),
            allow_safe_fixtures=False,
        )

        self.assertEqual(
            "python_cli_analysis_limit_exceeded:arguments",
            failure,
        )
        self.assertLess(len(reconstructed), 30_000)
        self.assertEqual(
            {
                "private_model_override",
                "python_cli_analysis_limit_exceeded",
            },
            {str(item["rule_id"]) for item in findings},
        )

    def test_python_cli_argument_truncation_fails_closed(self) -> None:
        for provider_arguments in (
            ('"--provider"', '"my" + "proxy"'),
            ('"--provider=" + "my" + "proxy"',),
        ):
            with self.subTest(provider_arguments=provider_arguments):
                arguments = ['"commander"']
                for index in range(20):
                    if index == 4:
                        arguments.extend(
                            (
                                *provider_arguments,
                                '"--openai-base-url"',
                                '"https://proxy." + "corp.example/v1"',
                                '"--model"',
                                '"private-" + "deployment"',
                            )
                        )
                    else:
                        arguments.extend(
                            (
                                '"--provider"',
                                '"openai"',
                                '"--openai-base-url"',
                                '"https://api.openai.com/v1"',
                                '"--model"',
                                '"public-model"',
                            )
                        )
                payload = (
                    "args = [" + ", ".join(arguments) + "]\n"
                    "run(args)\n"
                )

                reconstructed, failure = (
                    compliance_module._python_cli_argument_text(
                        "launcher.py",
                        payload,
                    )
                )
                findings = scan_payload(
                    "launcher.py",
                    payload.encode(),
                    allow_safe_fixtures=False,
                )

                self.assertNotIn("myproxy", reconstructed)
                self.assertEqual(
                    "python_cli_analysis_limit_exceeded:arguments",
                    failure,
                )
                self.assertIn(
                    "python_cli_analysis_limit_exceeded",
                    {str(item["rule_id"]) for item in findings},
                )

    def test_python_cli_reconstruction_binds_callable_forms(self) -> None:
        endpoint = '"https://proxy." + "corp.example/v1"'
        model = '"private-" + "deployment"'
        command = (
            'run(["commander", "--provider", provider, '
            f'"--openai-base-url", {endpoint}, '
            f'"--model", {model}])'
        )
        payloads = {
            "lambda-default": (
                f'launch = lambda provider="my" + "proxy": {command}\n'
                "launch()\n"
            ),
            "lambda-call": (
                f"launch = lambda provider: {command}\n"
                'launch("my" + "proxy")\n'
            ),
            "function-alias": (
                "def launch(provider):\n"
                f"    {command}\n"
                "runner = launch\n"
                'runner("my" + "proxy")\n'
            ),
            "expanded-keyword": (
                "def launch(provider):\n"
                f"    {command}\n"
                'launch(**{"provider": "my" + "proxy"})\n'
            ),
        }

        for name, payload in payloads.items():
            with self.subTest(name=name):
                reconstructed, failure = (
                    compliance_module._python_cli_argument_text(
                        "launcher.py",
                        payload,
                    )
                )
                findings = scan_payload(
                    "launcher.py",
                    payload.encode(),
                    allow_safe_fixtures=False,
                )

                self.assertEqual("", failure)
                self.assertIn("myproxy", reconstructed)
                self.assertEqual(
                    {
                        "private_endpoint",
                        "private_model_override",
                    },
                    {str(item["rule_id"]) for item in findings},
                )

    def test_python_cli_reconstruction_binds_iterable_forms(self) -> None:
        endpoint = '"https://proxy." + "corp.example/v1"'
        model = '"private-" + "deployment"'
        payloads = {
            "destructured-loop": (
                "for provider, endpoint, model in ((\n"
                '    "my" + "proxy",\n'
                f"    {endpoint},\n"
                f"    {model},\n"
                "),):\n"
                "    run([\n"
                '        "commander", "--provider", provider,\n'
                '        "--openai-base-url", endpoint,\n'
                '        "--model", model,\n'
                "    ])\n"
            ),
            "list-comprehension": (
                "args = [\n"
                "    item\n"
                '    for provider in ("my" + "proxy",)\n'
                "    for item in (\n"
                '        "commander", "--provider", provider,\n'
                f'        "--openai-base-url", {endpoint},\n'
                f'        "--model", {model},\n'
                "    )\n"
                "]\n"
                "run(args)\n"
            ),
            "set-iteration": (
                'for provider in {"my" + "proxy"}:\n'
                "    run([\n"
                '        "commander", "--provider", provider,\n'
                f'        "--openai-base-url", {endpoint},\n'
                f'        "--model", {model},\n'
                "    ])\n"
            ),
        }

        for name, payload in payloads.items():
            with self.subTest(name=name):
                reconstructed, failure = (
                    compliance_module._python_cli_argument_text(
                        "launcher.py",
                        payload,
                    )
                )
                findings = scan_payload(
                    "launcher.py",
                    payload.encode(),
                    allow_safe_fixtures=False,
                )

                self.assertEqual("", failure)
                self.assertIn("myproxy", reconstructed)
                self.assertEqual(
                    {
                        "private_endpoint",
                        "private_model_override",
                    },
                    {str(item["rule_id"]) for item in findings},
                )

    def test_python_cli_reconstruction_binds_defaults_calls_and_loops(
        self,
    ) -> None:
        endpoint = '"https://proxy." + "corp.example/v1"'
        model = '"private-" + "deployment"'
        payloads = {
            "function-default": (
                'def launch(provider="my" + "proxy"):\n'
                "    run([\n"
                '        "commander", "--provider", provider,\n'
                f'        "--openai-base-url", {endpoint},\n'
                f'        "--model", {model},\n'
                "    ])\n"
                "launch()\n"
            ),
            "call-binding": (
                "def launch(provider):\n"
                "    run([\n"
                '        "commander", "--provider", provider,\n'
                f'        "--openai-base-url", {endpoint},\n'
                f'        "--model", {model},\n'
                "    ])\n"
                'launch("my" + "proxy")\n'
            ),
            "loop-binding": (
                f"endpoint = {endpoint}\n"
                f"model = {model}\n"
                'for provider in ("my" + "proxy",):\n'
                "    run([\n"
                '        "commander", "--provider", provider,\n'
                '        "--openai-base-url", endpoint,\n'
                '        "--model", model,\n'
                "    ])\n"
            ),
        }

        for name, payload in payloads.items():
            with self.subTest(name=name):
                reconstructed, failure = (
                    compliance_module._python_cli_argument_text(
                        "launcher.py",
                        payload,
                    )
                )
                findings = scan_payload(
                    "launcher.py",
                    payload.encode(),
                    allow_safe_fixtures=False,
                )

                self.assertEqual("", failure)
                self.assertIn("myproxy", reconstructed)
                self.assertEqual(
                    {
                        "private_endpoint",
                        "private_model_override",
                    },
                    {str(item["rule_id"]) for item in findings},
                )

    def test_detects_short_generic_api_key_names_and_punctuation(
        self,
    ) -> None:
        key_name = "X_" + "API_KEY"
        value = "abcdefghijkl!" + "mnopqrst"
        payload = (
            f"Env {key_name}={value}\n"
            "os.environ.setdefault(\n"
            f'    "{key_name}",\n'
            f'    "{value}",\n'
            ")\n"
        ).encode()

        findings = scan_payload(
            "Dockerfile",
            payload,
            allow_safe_fixtures=False,
        )

        self.assertEqual(
            {"api_key_assignment"},
            {str(item["rule_id"]) for item in findings},
        )

    def test_detects_python_comments_prefixes_and_docker_legacy_env(
        self,
    ) -> None:
        endpoint_key = "VOI_MYPROXY_OPENAI_BASE_" + "URL"
        model_key = "VOI_MYPROXY_" + "MODEL"
        api_key_name = "X_" + "API_KEY"
        endpoint = "https://commented." + "private.example/v1"
        model = "commented-" + "private-model"
        api_key = "abcdefghijkl!" + "mnopqrst"
        payload = (
            "os.putenv(\n"
            f'    r"{endpoint_key}",  # key comment\n'
            f'    r"{endpoint}",\n'
            ")\n"
            "os.environ.setdefault(\n"
            f'    R"{model_key}",  # key comment\n'
            f'    u"{model}",\n'
            ")\n"
            "os.environ.setdefault(\n"
            f'    r"{api_key_name}",  # key comment\n'
            f'    r"{api_key}",\n'
            ")\n"
        ).encode()

        findings = scan_payload(
            "launcher.py",
            payload,
            allow_safe_fixtures=False,
        )

        self.assertEqual(
            {
                "api_key_assignment",
                "private_endpoint",
                "private_model_override",
            },
            {str(item["rule_id"]) for item in findings},
        )

        docker_findings = scan_payload(
            "Dockerfile",
            f"Env {api_key_name} {api_key}\n".encode(),
            allow_safe_fixtures=False,
        )
        self.assertEqual(
            {"api_key_assignment"},
            {str(item["rule_id"]) for item in docker_findings},
        )

    def test_detects_folded_python_cli_and_long_shell_commands(self) -> None:
        endpoint = "https://folded." + "private.example/v1"
        model = "folded-" + "private-model"
        payload = (
            'subprocess.run([\n'
            '    "commander",\n'
            '    "--provider",\n'
            '    "my" + "proxy",\n'
            '    "--base-url",\n'
            f'    "{endpoint}",\n'
            '    "--model",\n'
            f'    "{model}",\n'
            "])\n"
        ).encode()
        findings = scan_payload(
            "launcher.py",
            payload,
            allow_safe_fixtures=False,
        )
        self.assertEqual(
            {"private_endpoint", "private_model_override"},
            {str(item["rule_id"]) for item in findings},
        )

        padding = "x" * 5000
        shell_findings = scan_payload(
            "launch.sh",
            (
                f"commander --padding {padding} --provider myproxy "
                f"--base-url {endpoint} --model {model}\n"
            ).encode(),
            allow_safe_fixtures=False,
        )
        self.assertEqual(
            {"private_endpoint", "private_model_override"},
            {str(item["rule_id"]) for item in shell_findings},
        )

    def test_detects_structurally_reconstructed_kubernetes_env(self) -> None:
        payloads = (
            (
                "deployment.json",
                b'{"name"\n:\n"VOI_MYPROXY_HOST","value"\n:\n"10.20.30.40"}',
            ),
            (
                "deployment.yaml",
                (
                    b'- name: "VOI_MYPROXY_\\\n'
                    b'    HOST"\n'
                    b'  value: "10.20.30.40"\n'
                ),
            ),
        )
        for path, payload in payloads:
            with self.subTest(path=path):
                findings = scan_payload(
                    path,
                    payload,
                    allow_safe_fixtures=False,
                )

                self.assertEqual(1, len(findings))
                self.assertEqual(
                    "private_endpoint",
                    findings[0]["rule_id"],
                )

    def test_detects_semantic_yaml_kubernetes_env_variants(self) -> None:
        host_key = "VOI_MYPROXY_" + "HOST"
        payloads = (
            (
                "- ? name\n"
                f"  : {host_key}\n"
                "  ? value\n"
                "  : 10.20.30.40\n"
            ).encode(),
            (
                "- ? >-\n    name\n"
                f"  : >-\n    {host_key}\n"
                "  ? >-\n    value\n"
                "  : >-\n    10.20.30.40\n"
            ).encode(),
            (
                "private: &private\n"
                f"  name: {host_key}\n"
                "  value: 10.20.30.40\n"
                "env:\n"
                "  - *private\n"
            ).encode(),
            (
                "- !!map\n"
                "  ? !!str name\n"
                f"  : !!str {host_key}\n"
                "  ? !!str value\n"
                "  : !!str 10.20.30.40\n"
            ).encode(),
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                findings = scan_payload(
                    "deployment.yaml",
                    payload,
                    allow_safe_fixtures=False,
                )

                self.assertIn(
                    "private_endpoint",
                    {str(item["rule_id"]) for item in findings},
                )

    def test_yaml_parser_failures_are_blocking_findings(self) -> None:
        host_key = "VOI_MYPROXY_" + "HOST"
        unsupported_tag = (
            f"!PrivateConfig {{name: {host_key}, value: 10.20.30.40}}"
        ).encode()
        aliases = ", ".join("*private" for _ in range(129))
        excessive_aliases = (
            "private: &private {safe: true}\n"
            f"aliases: [{aliases}]\n"
        ).encode()

        for payload in (unsupported_tag, excessive_aliases):
            with self.subTest(payload=payload[:40]):
                rule_ids = {
                    str(item["rule_id"])
                    for item in scan_payload(
                        "deployment.yaml",
                        payload,
                        allow_safe_fixtures=False,
                    )
                }
                self.assertIn("yaml_parse_failed", rule_ids)

        with mock.patch.object(compliance_module, "_yaml", None):
            findings = scan_payload(
                "deployment.yaml",
                b"safe: true\n",
                allow_safe_fixtures=False,
            )

        self.assertEqual("yaml_parser_unavailable", findings[0]["rule_id"])

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

    def test_detects_compact_mapping_and_colon_assignments(self) -> None:
        api_key_name = "MYPROXY_" + "API_KEY"
        openai_key_name = "OPENAI_" + "API_KEY"
        credential_key = "AWS_SHARED_CREDENTIALS_" + "FILE"
        model_key = "VOI_MYPROXY_" + "MODEL"
        endpoint_key = "VOI_MYPROXY_OPENAI_BASE_" + "URL"
        credential_path = "/home/user/" + ".netrc"
        payload = (
            "{"
            f'"{api_key_name}": "abcdefghijklmnop", '
            f'"{credential_key}": "{credential_path}", '
            f'"{model_key}": "internal-deployment-alpha", '
            f'"{endpoint_key}": "https://proxy.corp.example:8443/v1"'
            "}\n"
            f"{openai_key_name}: abcdefghijklmnop\n"
        ).encode()

        self.assertEqual(
            {
                "api_key_assignment",
                "credential_path",
                "json_parse_failed",
                "private_endpoint",
                "private_model_override",
            },
            {
                str(item["rule_id"])
                for item in scan_payload("private-config.json", payload)
            },
        )

    def test_detects_process_list_and_path_constructor_configuration(
        self,
    ) -> None:
        api_key_name = "OPENAI_" + "API_KEY"
        myproxy_key_name = "MYPROXY_" + "API_KEY"
        model_key = "VOI_MYPROXY_" + "MODEL"
        endpoint_key = "VOI_MYPROXY_OPENAI_BASE_" + "URL"
        path_constructor = "Pa" + "th"
        credential_path = "/home/user/" + ".netrc"
        payload = (
            f"- {api_key_name}=liveabcdefghijklmnop\n"
            f"ENV {model_key}=internal-deployment-alpha\n"
            f"env {endpoint_key}=https://proxy.corp.example:8443/v1\n"
            f'os.environ["{myproxy_key_name}"] = "liveabcdefghijklmnop"\n'
            f'{path_constructor}("{credential_path}")\n'
        ).encode()

        self.assertEqual(
            {
                "api_key_assignment",
                "credential_path",
                "private_endpoint",
                "private_model_override",
            },
            {
                str(item["rule_id"])
                for item in scan_payload("deployment-config", payload)
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
        fake_assignment = 'CODEX_MYPROXY_API_KEY: "proxy-alias-key"'
        allowed_path = "tests/test_llm_interpreter.py"
        fake_key_fingerprint = scan_payload(
            allowed_path,
            f"value = '{fake_key}'".encode(),
            allow_safe_fixtures=False,
        )[0]["fingerprint"]
        fake_assignment_fingerprint = scan_payload(
            allowed_path,
            fake_assignment.encode(),
            allow_safe_fixtures=False,
        )[0]["fingerprint"]
        allowed = {
            allowed_path: {
                "api_key": frozenset({str(fake_key_fingerprint)}),
                "api_key_assignment": frozenset(
                    {str(fake_assignment_fingerprint)}
                ),
            }
        }

        with mock.patch.object(
            compliance_module,
            "_SAFE_FIXTURE_FINGERPRINTS",
            allowed,
        ):
            self.assertEqual(
                [],
                scan_payload(
                    allowed_path,
                    f"value = '{fake_key}'".encode(),
                ),
            )
            self.assertEqual(
                [],
                scan_payload(allowed_path, fake_assignment.encode()),
            )
            self.assertTrue(
                scan_payload(
                    allowed_path,
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
                    allowed_path,
                    f"value = '{fake_key}changed'".encode(),
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
        tracked_blob = "a" * 40
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
                if arguments == ["ls-files", "--stage", "-z"]:
                    return (
                        f"100644 {tracked_blob} 0\ttracked.py\0".encode()
                    )
                if arguments == ["cat-file", "blob", tracked_blob]:
                    return b"safe = True\n"
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

    def test_repository_scan_reads_tracked_dangling_symlink_blob(self) -> None:
        tracked_blob = "b" * 40
        bearer = "Bearer " + "opaqueabcdefghijklmnop"

        def git_output(
            _repository_root: Path,
            arguments: list[str],
        ) -> bytes:
            if arguments == ["ls-files", "--stage", "-z"]:
                return (
                    f"120000 {tracked_blob} 0\tprivate-endpoint-link\0".encode()
                )
            if arguments == ["cat-file", "blob", tracked_blob]:
                return bearer.encode()
            if arguments == [
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
            ]:
                return b""
            if arguments[:2] == ["diff", "--no-ext-diff"]:
                return b""
            raise AssertionError(arguments)

        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.object(
                compliance_module,
                "_git_output",
                side_effect=git_output,
            ),
        ):
            report = scan_git_and_artifacts(Path(temporary), ())

        self.assertEqual(1, report["finding_count"])
        self.assertEqual("bearer_token", report["findings"][0]["rule_id"])
        self.assertEqual(
            "private-endpoint-link",
            report["findings"][0]["path"],
        )


class DerivedVerdictTest(unittest.TestCase):
    @staticmethod
    def _secret_scan_evidence(
        report: dict[str, object],
    ) -> dict[str, object]:
        source_payload = b"synthetic source\n"
        source_header = f"blob {len(source_payload)}\0".encode()
        source_oid = hashlib.sha1(source_header + source_payload).hexdigest()
        tracked_entry = {
            "kind": "tracked",
            "path": "tests/test_distribution_compliance.py",
            "mode": "100644",
            "oid": source_oid,
        }
        source_tree = compliance_module._git_tree_oid_from_manifest(
            [tracked_entry]
        )
        repository = report["repository"]
        assert isinstance(repository, dict)
        for state in repository.values():
            assert isinstance(state, dict)
            state["tree"] = source_tree
        manifest = [
            tracked_entry,
            {
                "kind": "diff",
                "path": "<git-diff>",
                "size": 0,
                "sha256": compliance_module.sha256_bytes(b""),
            },
        ]
        artifacts = report["artifacts"]
        assert isinstance(artifacts, dict)
        for kind in ("wheel", "sdist"):
            artifact = artifacts[kind]
            assert isinstance(artifact, dict)
            file_manifest = artifact["file_manifest"]
            file_sizes = artifact["file_sizes"]
            assert isinstance(file_manifest, dict)
            assert isinstance(file_sizes, dict)
            for path, digest in file_manifest.items():
                manifest.append(
                    {
                        "kind": kind,
                        "path": f"{kind}/{path}",
                        "size": file_sizes[path],
                        "sha256": digest,
                    }
                )
        generated = compliance_module._distribution_report_scan_payloads(
            report
        )
        for name, payload in generated.items():
            manifest.append(
                {
                    "kind": "report",
                    "path": f"report/{name}",
                    "size": len(payload),
                    "sha256": compliance_module.sha256_bytes(payload),
                }
            )
        manifest.sort(key=lambda item: str(item["path"]))
        return {
            "scanned_file_count": len(manifest),
            "input_manifest_sha256": compliance_module.sha256_bytes(
                compliance_module.canonical_json_text(manifest).encode()
            ),
            "input_manifest": manifest,
            "finding_count": 0,
            "findings": [],
        }

    def setUp(self) -> None:
        digest = "d" * 64
        wheel_root = "voistarcraft2-0.1.0.dist-info"
        metadata_entry = f"{wheel_root}/METADATA"
        source_readme_raw = "# Synthetic fixture\n"
        source_pyproject_raw = """[project]
name = "voiStarcraft2"
version = "0.1.0"
description = "Synthetic distribution compliance fixture."
readme = "README.md"
requires-python = ">=3.10"
license = "AGPL-3.0-or-later OR LicenseRef-Commercial"
license-files = [
    "LICENSE",
    "LICENSES/AGPL-3.0-or-later.txt",
    "THIRD_PARTY_NOTICES.md",
]
keywords = ["starcraft", "sc2", "natural-language", "voice", "commander"]
dependencies = []

[project.optional-dependencies]
sc2 = ["burnysc2>=6.5"]
voice = ["faster-whisper>=1.0", "sounddevice>=0.4.6"]
llm = ["anthropic>=0.40", "openai>=1.0"]
dev = ["build>=1.2", "pytest>=7", "pyyaml>=6.0.3", "tomli>=2.4.1"]
"""
        source_pyproject_digest = compliance_module.sha256_bytes(
            source_pyproject_raw.encode()
        )
        metadata_requires_dist = list(
            compliance_module.metadata_requirements_from_pyproject(
                source_pyproject_raw
            )
        )
        metadata_expectations = (
            compliance_module.project_metadata_expectations_from_pyproject(
                source_pyproject_raw
            )
        )
        metadata_raw = (
            "Metadata-Version: 2.4\n"
            "Name: voiStarcraft2\n"
            "Version: 0.1.0\n"
            "Summary: Synthetic distribution compliance fixture.\n"
            f"License-Expression: {EXPECTED_LICENSE_EXPRESSION}\n"
            "Keywords: starcraft,sc2,natural-language,voice,commander\n"
            "Requires-Python: >=3.10\n"
            "Description-Content-Type: text/markdown\n"
            + "".join(
                f"License-File: {path}\n"
                for path in REQUIRED_LICENSE_FILES
            )
            + "".join(
                f"Provides-Extra: {extra}\n"
                for extra in metadata_expectations["provides_extra"]
            )
            + "".join(
                f"Requires-Dist: {requirement}\n"
                for requirement in metadata_requires_dist
            )
            + "Dynamic: license-file\n\n"
            + source_readme_raw
        )
        top_level_raw = (
            "broodwar_commander\n"
            "integrations\n"
            "starcraft_commander\n"
            "toycraft_commander\n"
        )
        wheel_raw_by_entry = {
            metadata_entry: metadata_raw,
            f"{wheel_root}/WHEEL": (
                "Wheel-Version: 1.0\n"
                "Generator: setuptools (82.0.1)\n"
                "Root-Is-Purelib: true\n"
                "Tag: py3-none-any\n\n"
            ),
            f"{wheel_root}/top_level.txt": top_level_raw,
        }
        wheel_license_entries = {
            f"{wheel_root}/licenses/LICENSE",
            f"{wheel_root}/licenses/LICENSES/AGPL-3.0-or-later.txt",
            f"{wheel_root}/licenses/THIRD_PARTY_NOTICES.md",
        }
        wheel_file_manifest = {
            "starcraft_commander/runtime_data.py": digest,
            **{entry: digest for entry in wheel_license_entries},
            **{
                entry: compliance_module.sha256_bytes(raw.encode())
                for entry, raw in wheel_raw_by_entry.items()
            },
        }
        wheel_file_sizes = {
            "starcraft_commander/runtime_data.py": 1,
            **{entry: 1 for entry in wheel_license_entries},
            **{
                entry: len(raw.encode())
                for entry, raw in wheel_raw_by_entry.items()
            },
        }
        wheel_record_entry = f"{wheel_root}/RECORD"
        wheel_record_raw = "".join(
            (
                f"{entry},,"
                if entry == wheel_record_entry
                else (
                    f"{entry},"
                    f"{compliance_module._record_hash_from_sha256(entry_digest)},"
                    f"{wheel_file_sizes[entry]}"
                )
            )
            + "\n"
            for entry, entry_digest in sorted(
                {
                    **wheel_file_manifest,
                    wheel_record_entry: digest,
                }.items()
            )
        )
        wheel_raw_by_entry[wheel_record_entry] = wheel_record_raw
        wheel_file_manifest[wheel_record_entry] = (
            compliance_module.sha256_bytes(wheel_record_raw.encode())
        )
        wheel_file_sizes[wheel_record_entry] = len(wheel_record_raw.encode())
        sdist_root = "voistarcraft2-0.1.0"
        sdist_egg_info = f"{sdist_root}/voiStarcraft2.egg-info"
        sdist_source = (
            f"{sdist_root}/starcraft_commander/runtime_data.py"
        )
        sdist_pyproject = f"{sdist_root}/pyproject.toml"
        sdist_expected_manifest = {
            "LICENSE": EXPECTED_LICENSE_FILE_SHA256["LICENSE"],
            "LICENSES/AGPL-3.0-or-later.txt": (
                EXPECTED_LICENSE_FILE_SHA256[
                    "LICENSES/AGPL-3.0-or-later.txt"
                ]
            ),
            "THIRD_PARTY_NOTICES.md": EXPECTED_LICENSE_FILE_SHA256[
                "THIRD_PARTY_NOTICES.md"
            ],
            "README.md": compliance_module.sha256_bytes(
                source_readme_raw.encode()
            ),
            "pyproject.toml": source_pyproject_digest,
            "starcraft_commander/runtime_data.py": digest,
        }
        sdist_expected_sizes = {
            "LICENSE": 1,
            "LICENSES/AGPL-3.0-or-later.txt": 1,
            "THIRD_PARTY_NOTICES.md": 1,
            "README.md": len(source_readme_raw.encode()),
            "pyproject.toml": len(source_pyproject_raw.encode()),
            "starcraft_commander/runtime_data.py": 1,
        }
        sdist_requires_raw = (
            "[dev]\n"
            "build>=1.2\n"
            "pytest>=7\n\n"
            "pyyaml>=6.0.3\n"
            "tomli>=2.4.1\n\n"
            "[llm]\n"
            "anthropic>=0.40\n"
            "openai>=1.0\n\n"
            "[sc2]\n"
            "burnysc2>=6.5\n\n"
            "[voice]\n"
            "faster-whisper>=1.0\n"
            "sounddevice>=0.4.6\n"
        )
        sdist_sources_raw = "\n".join(
            sorted(
                {
                    *sdist_expected_manifest,
                    *(
                        f"voiStarcraft2.egg-info/{name}"
                        for name in (
                            "PKG-INFO",
                            "SOURCES.txt",
                            "dependency_links.txt",
                            "requires.txt",
                            "top_level.txt",
                        )
                    ),
                }
            )
        ) + "\n"
        sdist_raw_by_entry = {
            f"{sdist_root}/PKG-INFO": metadata_raw,
            f"{sdist_root}/setup.cfg": (
                "\n[egg_info]\ntag_build = \ntag_date = 0\n\n"
            ),
            f"{sdist_egg_info}/PKG-INFO": metadata_raw,
            f"{sdist_egg_info}/SOURCES.txt": sdist_sources_raw,
            f"{sdist_egg_info}/dependency_links.txt": "",
            f"{sdist_egg_info}/requires.txt": sdist_requires_raw,
            f"{sdist_egg_info}/top_level.txt": top_level_raw,
        }
        sdist_file_manifest = {
            **{
                f"{sdist_root}/{entry}": entry_digest
                for entry, entry_digest in sdist_expected_manifest.items()
            },
            sdist_source: digest,
            sdist_pyproject: source_pyproject_digest,
            **{
                entry: compliance_module.sha256_bytes(raw.encode())
                for entry, raw in sdist_raw_by_entry.items()
            },
        }
        sdist_file_sizes = {
            **{
                f"{sdist_root}/{entry}": size
                for entry, size in sdist_expected_sizes.items()
            },
            sdist_source: 1,
            sdist_pyproject: len(source_pyproject_raw.encode()),
            **{
                entry: len(raw.encode())
                for entry, raw in sdist_raw_by_entry.items()
            },
        }
        sdist_metadata_entries = (
            f"{sdist_root}/PKG-INFO",
            f"{sdist_egg_info}/PKG-INFO",
        )
        sdist_directories = [sdist_root, sdist_egg_info]
        self.report = {
            "schema_version": (
                compliance_module.DISTRIBUTION_COMPLIANCE_SCHEMA_VERSION
            ),
            "repository": {
                phase: {
                    "ok": True,
                    "head": digest[:40],
                    "tree": digest[:40],
                    "dirty_entries": [],
                    "replacement_refs": [],
                    "source_root_matches": True,
                    "repository_root": "/release",
                    "source_root": "/release",
                }
                for phase in ("before", "after")
            },
            "artifacts": {
                "wheel": {
                    "path": "/release/dist/voistarcraft2-0.1.0-py3-none-any.whl",
                    "filename": "voistarcraft2-0.1.0-py3-none-any.whl",
                    "sha256": digest,
                    "entry_count": len(wheel_file_manifest),
                    "entries": list(wheel_file_manifest),
                    "file_manifest": wheel_file_manifest,
                    "file_sizes": wheel_file_sizes,
                    "directory_entries": [],
                },
                "sdist": {
                    "path": "/release/dist/voistarcraft2-0.1.0.tar.gz",
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
                    "file_sizes": sdist_file_sizes,
                    "directory_entries": sdist_directories,
                },
            },
            "archive_blockers": [],
            "archive_manifests": {
                "wheel": {
                    "starcraft_commander/runtime_data.py": digest,
                },
                "sdist": {
                    **sdist_expected_manifest,
                },
            },
            "archive_size_manifests": {
                "wheel": {
                    "starcraft_commander/runtime_data.py": 1,
                },
                "sdist": {
                    **sdist_expected_sizes,
                },
            },
            "metadata": {
                "entry": metadata_entry,
                "license_expressions": [EXPECTED_LICENSE_EXPRESSION],
                "requires_dist": metadata_requires_dist,
                "raw": metadata_raw,
                "sdist": [
                    {"entry": entry, "raw": metadata_raw}
                    for entry in sdist_metadata_entries
                ],
                "generated": {
                    "wheel": [
                        {"entry": entry, "raw": raw}
                        for entry, raw in sorted(wheel_raw_by_entry.items())
                    ],
                    "sdist": [
                        {"entry": entry, "raw": raw}
                        for entry, raw in sorted(sdist_raw_by_entry.items())
                    ],
                },
            },
            "source_pyproject": {
                "raw": source_pyproject_raw,
                "sha256": source_pyproject_digest,
            },
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
                "build_requirements": [
                    compliance_module.EXPECTED_BUILD_BACKEND_REQUIREMENT
                ],
                "build_locked_versions": {
                    "setuptools": (
                        compliance_module.EXPECTED_BUILD_BACKEND_VERSION
                    ),
                },
                "build_backend_generator": (
                    compliance_module.EXPECTED_BUILD_BACKEND_GENERATOR
                ),
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
        }
        self.report["secret_scan"] = self._secret_scan_evidence(self.report)
        trusted_evidence = (
            copy.deepcopy(self.report["archive_manifests"]),
            copy.deepcopy(self.report["archive_size_manifests"]),
        )
        trusted_patcher = mock.patch.object(
            compliance_module,
            "_trusted_archive_evidence",
            return_value=trusted_evidence,
        )
        trusted_patcher.start()
        self.addCleanup(trusted_patcher.stop)
        trusted_artifacts = copy.deepcopy(self.report["artifacts"])
        for artifact in trusted_artifacts.values():
            artifact["archive_blockers"] = []
        trusted_artifact_patcher = mock.patch.object(
            compliance_module,
            "_trusted_artifact_evidence",
            return_value=trusted_artifacts,
        )
        trusted_artifact_patcher.start()
        self.addCleanup(trusted_artifact_patcher.stop)

    def test_accepts_complete_raw_evidence(self) -> None:
        self.assertEqual([], distribution_report_blockers(self.report))

    def test_rejects_missing_or_unknown_schema_after_projection_rebind(
        self,
    ) -> None:
        for schema_version in (None, 999):
            with self.subTest(schema_version=schema_version):
                report = copy.deepcopy(self.report)
                if schema_version is None:
                    report.pop("schema_version")
                else:
                    report["schema_version"] = schema_version
                payloads = (
                    compliance_module._distribution_report_scan_payloads(
                        report
                    )
                )
                manifest = report["secret_scan"]["input_manifest"]
                for name, payload in payloads.items():
                    entry = next(
                        item
                        for item in manifest
                        if item["path"] == f"report/{name}"
                    )
                    entry["size"] = len(payload)
                    entry["sha256"] = compliance_module.sha256_bytes(payload)
                report["secret_scan"]["input_manifest_sha256"] = (
                    compliance_module.sha256_bytes(
                        compliance_module.canonical_json_text(
                            manifest
                        ).encode()
                    )
                )

                codes = {
                    str(item["code"])
                    for item in distribution_report_blockers(report)
                }

                self.assertIn(
                    "unsupported_distribution_compliance_schema",
                    codes,
                )

    def test_rejects_build_backend_identity_drift(self) -> None:
        mutations = (
            ("build_requirements", ["setuptools>=77.0.3"]),
            ("build_locked_versions", {"setuptools": "83.0.0"}),
            ("build_backend_generator", "setuptools (83.0.0)"),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                report = copy.deepcopy(self.report)
                report["dependencies"][field] = value

                blockers = distribution_report_blockers(report)

                self.assertIn(
                    {
                        "code": "dependency_notice_drift",
                        "source": field,
                        "observed": value,
                        "expected": self.report["dependencies"][field],
                    },
                    blockers,
                )

    def test_rejects_forged_exact_sha_archive_provenance(self) -> None:
        report = copy.deepcopy(self.report)
        target = "starcraft_commander/runtime_data.py"
        old_digest = report["artifacts"]["wheel"]["file_manifest"][target]
        old_size = report["artifacts"]["wheel"]["file_sizes"][target]
        new_digest = "e" * 64
        new_size = old_size + 1
        report["archive_manifests"]["wheel"][target] = new_digest
        report["archive_size_manifests"]["wheel"][target] = new_size
        report["artifacts"]["wheel"]["file_manifest"][target] = new_digest
        report["artifacts"]["wheel"]["file_sizes"][target] = new_size
        record = next(
            item
            for item in report["metadata"]["generated"]["wheel"]
            if str(item["entry"]).endswith("/RECORD")
        )
        record["raw"] = str(record["raw"]).replace(
            (
                f"{target},"
                f"{compliance_module._record_hash_from_sha256(old_digest)},"
                f"{old_size}"
            ),
            (
                f"{target},"
                f"{compliance_module._record_hash_from_sha256(new_digest)},"
                f"{new_size}"
            ),
        )
        record_entry = str(record["entry"])
        report["artifacts"]["wheel"]["file_manifest"][record_entry] = (
            compliance_module.sha256_bytes(str(record["raw"]).encode())
        )
        report["artifacts"]["wheel"]["file_sizes"][record_entry] = len(
            str(record["raw"]).encode()
        )
        report["artifacts"]["wheel"]["sha256"] = "f" * 64

        codes = {
            str(item["code"]) for item in distribution_report_blockers(report)
        }

        self.assertIn("archive_manifest_provenance_mismatch", codes)
        self.assertIn("archive_size_provenance_mismatch", codes)

    def test_rejects_artifact_digest_not_matching_actual_archive(self) -> None:
        report = copy.deepcopy(self.report)
        report["artifacts"]["wheel"]["sha256"] = "f" * 64

        blockers = distribution_report_blockers(report)

        self.assertIn(
            {
                "code": "artifact_archive_evidence_mismatch",
                "kind": "wheel",
                "field": "sha256",
            },
            blockers,
        )

    def test_rejects_unverified_exact_sha_provenance(self) -> None:
        with mock.patch.object(
            compliance_module,
            "_trusted_archive_evidence",
            return_value=None,
        ):
            codes = {
                str(item["code"])
                for item in distribution_report_blockers(self.report)
            }

        self.assertIn("unverified_exact_sha_provenance", codes)

    def test_rejects_missing_or_mutated_metadata_raw_evidence(self) -> None:
        for field in ("entry", "raw", "requires_dist"):
            with self.subTest(field=field):
                report = dict(self.report)
                metadata = dict(self.report["metadata"])
                metadata.pop(field)
                report["metadata"] = metadata

                codes = {
                    str(item["code"])
                    for item in distribution_report_blockers(report)
                }

                self.assertIn("invalid_metadata_evidence", codes)

        report = dict(self.report)
        metadata = dict(self.report["metadata"])
        mutated_raw = str(metadata["raw"]).replace(
            "Name: voiStarcraft2",
            "Name: attacker",
        )
        metadata["raw"] = mutated_raw
        report["metadata"] = metadata
        artifacts = {
            kind: dict(value)
            for kind, value in self.report["artifacts"].items()
        }
        wheel_manifest = dict(artifacts["wheel"]["file_manifest"])
        wheel_manifest[str(metadata["entry"])] = (
            compliance_module.sha256_bytes(mutated_raw.encode())
        )
        artifacts["wheel"]["file_manifest"] = wheel_manifest
        report["artifacts"] = artifacts

        codes = {
            str(item["code"]) for item in distribution_report_blockers(report)
        }

        self.assertIn("invalid_metadata_evidence", codes)

        report = dict(self.report)
        report.pop("source_pyproject")
        codes = {
            str(item["code"]) for item in distribution_report_blockers(report)
        }
        self.assertIn("invalid_source_pyproject_evidence", codes)

        report = dict(self.report)
        source_pyproject = dict(self.report["source_pyproject"])
        source_pyproject["raw"] = str(source_pyproject["raw"]).replace(
            'version = "0.1.0"',
            'version = "9.9.9"',
        )
        report["source_pyproject"] = source_pyproject
        codes = {
            str(item["code"]) for item in distribution_report_blockers(report)
        }
        self.assertIn("invalid_source_pyproject_evidence", codes)

    def test_metadata_is_bound_to_source_version_and_requirement_semantics(
        self,
    ) -> None:
        source_pyproject = self.report["source_pyproject"]
        metadata = dict(self.report["metadata"])
        wrong_version_raw = str(metadata["raw"]).replace(
            "Version: 0.1.0",
            "Version: 9.9.9",
        )
        wrong_version_entry = "voistarcraft2-9.9.9.dist-info/METADATA"
        metadata["entry"] = wrong_version_entry
        metadata["raw"] = wrong_version_raw
        blockers = compliance_module._metadata_evidence_blockers(
            metadata,
            source_pyproject,
            Path("voistarcraft2-9.9.9-py3-none-any.whl"),
            Path("voistarcraft2-9.9.9.tar.gz"),
            {
                wrong_version_entry: compliance_module.sha256_bytes(
                    wrong_version_raw.encode()
                )
            },
            {
                str(item["entry"]): compliance_module.sha256_bytes(
                    str(item["raw"]).encode()
                )
                for item in self.report["metadata"]["sdist"]
            },
            {wrong_version_entry: len(wrong_version_raw.encode())},
            {
                str(item["entry"]): len(str(item["raw"]).encode())
                for item in self.report["metadata"]["sdist"]
            },
            {"pyproject.toml": source_pyproject["sha256"]},
            {"starcraft_commander/runtime_data.py": 1},
            {"pyproject.toml": len(str(source_pyproject["raw"]).encode())},
            self.report["dependencies"],
        )
        reasons = {str(item.get("reason")) for item in blockers}
        self.assertIn("wrong_project_version", reasons)
        self.assertIn("artifact_version_mismatch", reasons)

        report = dict(self.report)
        metadata = dict(self.report["metadata"])
        source_requirement = 'openai>=1.0; extra == "llm"'
        private_requirement = (
            "openai @ https://packages.example.invalid/openai.whl; "
            'extra == "llm"'
        )
        mutated_raw = str(metadata["raw"]).replace(
            f"Requires-Dist: {source_requirement}",
            f"Requires-Dist: {private_requirement}",
        )
        metadata["raw"] = mutated_raw
        metadata["requires_dist"] = sorted(
            private_requirement if item == source_requirement else item
            for item in metadata["requires_dist"]
        )
        report["metadata"] = metadata
        artifacts = {
            kind: dict(value)
            for kind, value in self.report["artifacts"].items()
        }
        wheel_manifest = dict(artifacts["wheel"]["file_manifest"])
        wheel_manifest[str(metadata["entry"])] = (
            compliance_module.sha256_bytes(mutated_raw.encode())
        )
        artifacts["wheel"]["file_manifest"] = wheel_manifest
        report["artifacts"] = artifacts

        reasons = {
            str(item.get("reason"))
            for item in distribution_report_blockers(report)
        }
        self.assertIn("source_requires_dist_mismatch", reasons)

    def test_metadata_binds_extras_license_files_and_readme_payload(
        self,
    ) -> None:
        mutations = (
            (
                "Provides-Extra: llm\n",
                "",
                "wrong_provides_extra",
            ),
            (
                "License-File: THIRD_PARTY_NOTICES.md\n",
                "",
                "wrong_license_files",
            ),
            (
                "# Synthetic fixture\n",
                "# Mutated artifact description\n",
                "description_payload_mismatch",
            ),
        )
        targets = (
            ("wheel", None),
            ("sdist", 0),
            ("sdist", 1),
        )
        for kind, index in targets:
            for old, new, expected_reason in mutations:
                with self.subTest(
                    kind=kind,
                    index=index,
                    expected_reason=expected_reason,
                ):
                    report = copy.deepcopy(self.report)
                    metadata = report["metadata"]
                    artifacts = report["artifacts"]
                    if kind == "wheel":
                        entry = str(metadata["entry"])
                        mutated_raw = str(metadata["raw"]).replace(
                            old,
                            new,
                            1,
                        )
                        metadata["raw"] = mutated_raw
                        generated_target = next(
                            item
                            for item in metadata["generated"]["wheel"]
                            if item["entry"] == entry
                        )
                        generated_target["raw"] = mutated_raw
                    else:
                        target = metadata["sdist"][index]
                        entry = str(target["entry"])
                        mutated_raw = str(target["raw"]).replace(
                            old,
                            new,
                            1,
                        )
                        target["raw"] = mutated_raw
                        generated_target = next(
                            item
                            for item in metadata["generated"]["sdist"]
                            if item["entry"] == entry
                        )
                        generated_target["raw"] = mutated_raw
                    artifacts[kind]["file_manifest"][entry] = (
                        compliance_module.sha256_bytes(
                            mutated_raw.encode()
                        )
                    )
                    artifacts[kind]["file_sizes"][entry] = len(
                        mutated_raw.encode()
                    )
                    artifacts[kind]["sha256"] = "e" * 64

                    blockers = distribution_report_blockers(report)
                    reasons = {
                        str(item.get("reason"))
                        for item in blockers
                        if item.get("code")
                        in {
                            "invalid_metadata_evidence",
                            "invalid_sdist_metadata_evidence",
                        }
                    }

                    self.assertIn(expected_reason, reasons)

    def test_sdist_metadata_is_bound_to_source_semantics(self) -> None:
        for target_index in range(2):
            with self.subTest(target_index=target_index):
                report = dict(self.report)
                metadata = dict(self.report["metadata"])
                sdist_metadata = [
                    dict(item) for item in metadata["sdist"]
                ]
                target = sdist_metadata[target_index]
                mutated_raw = str(target["raw"]).replace(
                    f"License-Expression: {EXPECTED_LICENSE_EXPRESSION}",
                    "License-Expression: MIT",
                ).replace(
                    "\n\n",
                    "\nRequires-Dist: attacker-runtime>=1\n\n",
                    1,
                )
                target["raw"] = mutated_raw
                metadata["sdist"] = sdist_metadata
                report["metadata"] = metadata
                artifacts = {
                    kind: dict(value)
                    for kind, value in self.report["artifacts"].items()
                }
                sdist_manifest = dict(artifacts["sdist"]["file_manifest"])
                sdist_manifest[str(target["entry"])] = (
                    compliance_module.sha256_bytes(mutated_raw.encode())
                )
                artifacts["sdist"]["file_manifest"] = sdist_manifest
                report["artifacts"] = artifacts

                blockers = distribution_report_blockers(report)
                reasons = {
                    str(item.get("reason"))
                    for item in blockers
                    if item.get("code") == "invalid_sdist_metadata_evidence"
                }

                self.assertIn("wrong_license_expression", reasons)
                self.assertIn("source_requires_dist_mismatch", reasons)

    def test_sdist_metadata_binds_python_and_summary_semantics(self) -> None:
        mutations = (
            (
                "Requires-Python: >=3.10",
                "Requires-Python: >=99",
                "wrong_requires_python",
            ),
            (
                "Summary: Synthetic distribution compliance fixture.",
                "Summary: attacker summary",
                "wrong_summary",
            ),
        )
        for target_index in range(2):
            for old, new, expected_reason in mutations:
                with self.subTest(
                    target_index=target_index,
                    expected_reason=expected_reason,
                ):
                    report = copy.deepcopy(self.report)
                    metadata = report["metadata"]
                    target = metadata["sdist"][target_index]
                    target["raw"] = str(target["raw"]).replace(old, new)
                    generated_target = next(
                        item
                        for item in metadata["generated"]["sdist"]
                        if item["entry"] == target["entry"]
                    )
                    generated_target["raw"] = target["raw"]
                    entry = str(target["entry"])
                    report["artifacts"]["sdist"]["file_manifest"][entry] = (
                        compliance_module.sha256_bytes(
                            str(target["raw"]).encode()
                        )
                    )
                    report["artifacts"]["sdist"]["file_sizes"][entry] = len(
                        str(target["raw"]).encode()
                    )
                    report["artifacts"]["sdist"]["sha256"] = "e" * 64

                    reasons = {
                        str(item.get("reason"))
                        for item in distribution_report_blockers(report)
                        if item.get("code")
                        == "invalid_sdist_metadata_evidence"
                    }

                    self.assertIn(expected_reason, reasons)

    def test_generated_sdist_metadata_is_bound_to_source_provenance(
        self,
    ) -> None:
        mutations = (
            (
                "requires.txt",
                lambda raw: raw + "\nattacker-runtime>=1\n",
                "source_requires_txt_mismatch",
            ),
            (
                "SOURCES.txt",
                lambda raw: raw + "attacker_payload.py\n",
                "source_manifest_mismatch",
            ),
        )
        for suffix, mutate, expected_reason in mutations:
            with self.subTest(suffix=suffix):
                report = dict(self.report)
                metadata = dict(self.report["metadata"])
                generated = {
                    kind: [dict(item) for item in items]
                    for kind, items in metadata["generated"].items()
                }
                target = next(
                    item
                    for item in generated["sdist"]
                    if str(item["entry"]).endswith(suffix)
                )
                target["raw"] = mutate(str(target["raw"]))
                metadata["generated"] = generated
                report["metadata"] = metadata
                artifacts = {
                    kind: dict(value)
                    for kind, value in self.report["artifacts"].items()
                }
                sdist_manifest = dict(
                    artifacts["sdist"]["file_manifest"]
                )
                sdist_manifest[str(target["entry"])] = (
                    compliance_module.sha256_bytes(
                        str(target["raw"]).encode()
                    )
                )
                artifacts["sdist"]["file_manifest"] = sdist_manifest
                sdist_sizes = dict(artifacts["sdist"]["file_sizes"])
                sdist_sizes[str(target["entry"])] = len(
                    str(target["raw"]).encode()
                )
                artifacts["sdist"]["file_sizes"] = sdist_sizes
                artifacts["sdist"]["sha256"] = "e" * 64
                report["artifacts"] = artifacts

                reasons = {
                    str(item.get("reason"))
                    for item in distribution_report_blockers(report)
                    if item.get("code")
                    == "invalid_generated_metadata_evidence"
                }

                self.assertIn(expected_reason, reasons)

    def test_generated_wheel_record_is_bound_to_file_evidence(self) -> None:
        mutations = (
            (
                lambda raw: raw.replace("sha256=", "sha256=attacker", 1),
                False,
            ),
            (
                lambda raw: re.sub(
                    r",(\d+)\n",
                    lambda match: f",{int(match.group(1)) + 1}\n",
                    raw,
                    count=1,
                ),
                True,
            ),
        )
        for mutate, mutate_file_size in mutations:
            with self.subTest(mutate_file_size=mutate_file_size):
                report = dict(self.report)
                metadata = dict(self.report["metadata"])
                generated = {
                    kind: [dict(item) for item in items]
                    for kind, items in metadata["generated"].items()
                }
                record = next(
                    item
                    for item in generated["wheel"]
                    if str(item["entry"]).endswith("/RECORD")
                )
                record["raw"] = mutate(str(record["raw"]))
                metadata["generated"] = generated
                report["metadata"] = metadata
                artifacts = {
                    kind: dict(value)
                    for kind, value in self.report["artifacts"].items()
                }
                wheel_manifest = dict(
                    artifacts["wheel"]["file_manifest"]
                )
                wheel_manifest[str(record["entry"])] = (
                    compliance_module.sha256_bytes(
                        str(record["raw"]).encode()
                    )
                )
                artifacts["wheel"]["file_manifest"] = wheel_manifest
                wheel_sizes = dict(artifacts["wheel"]["file_sizes"])
                wheel_sizes[str(record["entry"])] = len(
                    str(record["raw"]).encode()
                )
                if mutate_file_size:
                    first_path = str(record["raw"]).split(",", 1)[0]
                    wheel_sizes[first_path] += 1
                artifacts["wheel"]["file_sizes"] = wheel_sizes
                artifacts["wheel"]["sha256"] = "e" * 64
                report["artifacts"] = artifacts

                reasons = {
                    str(item.get("reason"))
                    for item in distribution_report_blockers(report)
                    if item.get("code")
                    == "invalid_generated_metadata_evidence"
                }

                self.assertIn("invalid_wheel_record", reasons)

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
        self.assertIn("invalid_secret_scan_evidence", codes)

        report = copy.deepcopy(self.report)
        report["secret_scan"]["finding_count"] = 1
        codes = {
            str(item["code"]) for item in distribution_report_blockers(report)
        }
        self.assertIn("invalid_secret_scan_evidence", codes)

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

    def test_rejects_secret_scan_count_and_manifest_tampering(self) -> None:
        for count in (1, 587, 999999):
            with self.subTest(count=count):
                report = copy.deepcopy(self.report)
                report["secret_scan"]["scanned_file_count"] = count
                codes = {
                    str(item["code"])
                    for item in distribution_report_blockers(report)
                }
                self.assertIn("invalid_secret_scan_evidence", codes)

        mutations = ("missing", "extra", "digest")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                report = copy.deepcopy(self.report)
                secret_scan = report["secret_scan"]
                manifest = secret_scan["input_manifest"]
                if mutation == "missing":
                    manifest.pop()
                elif mutation == "extra":
                    manifest.append(
                        {
                            "kind": "report",
                            "path": "report/unscanned-extra.json",
                            "size": 2,
                            "sha256": compliance_module.sha256_bytes(b"{}"),
                        }
                    )
                else:
                    manifest[0]["sha256"] = "e" * 64
                secret_scan["scanned_file_count"] = len(manifest)
                secret_scan["input_manifest_sha256"] = (
                    compliance_module.sha256_bytes(
                        compliance_module.canonical_json_text(manifest).encode()
                    )
                )

                codes = {
                    str(item["code"])
                    for item in distribution_report_blockers(report)
                }

                self.assertIn("invalid_secret_scan_evidence", codes)

    def test_rejects_tracked_source_scan_omission(self) -> None:
        report = copy.deepcopy(self.report)
        secret_scan = report["secret_scan"]
        secret_scan["input_manifest"] = [
            item
            for item in secret_scan["input_manifest"]
            if item["kind"] != "tracked"
        ]
        secret_scan["scanned_file_count"] = len(
            secret_scan["input_manifest"]
        )
        secret_scan["input_manifest_sha256"] = (
            compliance_module.sha256_bytes(
                compliance_module.canonical_json_text(
                    secret_scan["input_manifest"]
                ).encode()
            )
        )

        codes = {
            str(item["code"]) for item in distribution_report_blockers(report)
        }

        self.assertIn("invalid_secret_scan_evidence", codes)

    def test_rejects_noncanonical_tracked_source_paths(self) -> None:
        aliases = (
            "tests//test_distribution_compliance.py",
            "tests/./test_distribution_compliance.py",
        )
        for alias in aliases:
            with self.subTest(alias=alias):
                report = copy.deepcopy(self.report)
                secret_scan = report["secret_scan"]
                entry = next(
                    item
                    for item in secret_scan["input_manifest"]
                    if item["path"]
                    == "tests/test_distribution_compliance.py"
                )
                entry["path"] = alias
                secret_scan["input_manifest"].sort(
                    key=lambda item: str(item["path"])
                )
                secret_scan["scanned_file_count"] = len(
                    secret_scan["input_manifest"]
                )
                secret_scan["input_manifest_sha256"] = (
                    compliance_module.sha256_bytes(
                        compliance_module.canonical_json_text(
                            secret_scan["input_manifest"]
                        ).encode()
                    )
                )

                codes = {
                    str(item["code"])
                    for item in distribution_report_blockers(report)
                }

                self.assertIn("invalid_secret_scan_evidence", codes)

    def test_rejects_forged_tracked_source_size_and_digest(self) -> None:
        report = copy.deepcopy(self.report)
        secret_scan = report["secret_scan"]
        tracked = next(
            item
            for item in secret_scan["input_manifest"]
            if item["kind"] == "tracked"
        )
        tracked["size"] = 1
        tracked["sha256"] = "e" * 64
        secret_scan["input_manifest_sha256"] = (
            compliance_module.sha256_bytes(
                compliance_module.canonical_json_text(
                    secret_scan["input_manifest"]
                ).encode()
            )
        )

        codes = {
            str(item["code"]) for item in distribution_report_blockers(report)
        }

        self.assertIn("invalid_secret_scan_evidence", codes)

    def test_rejects_report_projection_and_archive_scan_drift(self) -> None:
        for prefix in ("report/", "wheel/", "sdist/"):
            with self.subTest(prefix=prefix):
                report = copy.deepcopy(self.report)
                secret_scan = report["secret_scan"]
                manifest = secret_scan["input_manifest"]
                target = next(
                    item
                    for item in manifest
                    if str(item["path"]).startswith(prefix)
                )
                target["size"] = int(target["size"]) + 1
                secret_scan["input_manifest_sha256"] = (
                    compliance_module.sha256_bytes(
                        compliance_module.canonical_json_text(manifest).encode()
                    )
                )

                codes = {
                    str(item["code"])
                    for item in distribution_report_blockers(report)
                }

                self.assertIn("invalid_secret_scan_evidence", codes)

    def test_rejects_missing_or_malformed_archive_evidence(self) -> None:
        for field in (
            "archive_blockers",
            "archive_manifests",
            "archive_size_manifests",
        ):
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
                        else (
                            "invalid_archive_manifest_evidence"
                            if field == "archive_manifests"
                            else "invalid_archive_size_manifest_evidence"
                        )
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

        missing_generated = (
            ("wheel", "voistarcraft2-0.1.0.dist-info/RECORD"),
            ("sdist", "voistarcraft2-0.1.0/PKG-INFO"),
            (
                "sdist",
                (
                    "voistarcraft2-0.1.0/"
                    "voiStarcraft2.egg-info/SOURCES.txt"
                ),
            ),
        )
        for kind, entry in missing_generated:
            with self.subTest(kind=kind, entry=entry):
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
