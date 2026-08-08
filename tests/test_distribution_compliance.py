"""Tests for release distribution and private-configuration compliance."""

from __future__ import annotations

import copy
import gzip
import hashlib
import io
import json
import os
from collections import Counter
from pathlib import Path
import re
import stat
import struct
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile
import zlib

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
        installed_metadata = (
            "Metadata-Version: 2.4\n"
            "Name: voiStarcraft2\n"
            f"License-Expression: {EXPECTED_LICENSE_EXPRESSION}\n"
        )
        installed_result = mock.Mock(
            returncode=0,
            stdout=json.dumps(
                {
                    "installed_metadata": installed_metadata,
                    "license_expression": EXPECTED_LICENSE_EXPRESSION,
                    "packaged_defaults_loaded": True,
                    "runtime_data_loaded": True,
                    "source_repository_root_is_none": True,
                }
            ),
            stderr="",
        )
        target_result = mock.Mock(
            returncode=0,
            stdout='{"defaults": true, "loaded": true}',
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
        self.assertEqual(
            installed_metadata,
            result["payload"]["installed_metadata"],
        )
        self.assertTrue(result["payload"]["target_runtime_data_loaded"])
        self.assertTrue(result["payload"]["target_packaged_defaults_loaded"])
        self.assertEqual(4, run.call_count)
        for call in run.call_args_list:
            environment = call.kwargs.get("env")
            self.assertIsNotNone(environment)
            self.assertNotIn("PYTHONPATH", environment)
        installed_script = run.call_args_list[1].args[0][-1]
        target_script = run.call_args_list[3].args[0][-2]
        self.assertIn("read_text('METADATA')", installed_script)
        for script in (installed_script, target_script):
            self.assertIn("source_repository_root() is None", script)
            self.assertIn("DEFAULT_BLACKBOARD_HEADER", script)
            self.assertIn("DEFAULT_HOOK_MANIFEST", script)
            self.assertIn("name.endswith('_PATCH')", script)
            self.assertIn("strategy_matrix_macos_local.sh", script)
            self.assertIn("bool(patch_defaults)", script)
            self.assertIn("root in candidate.parents", script)


class DistributionBuildBoundaryTest(unittest.TestCase):
    def test_build_rejects_untrusted_backend_configuration_before_uv(
        self,
    ) -> None:
        source_root = Path(__file__).resolve().parents[1]
        original = (source_root / "pyproject.toml").read_text(
            encoding="utf-8"
        )
        variants = {
            "custom_backend": original.replace(
                'build-backend = "setuptools.build_meta"',
                'build-backend = "local_backend"',
                1,
            ),
            "missing_backend": original.replace(
                'build-backend = "setuptools.build_meta"\n',
                "",
                1,
            ),
            "backend_path": original.replace(
                'build-backend = "setuptools.build_meta"',
                'build-backend = "setuptools.build_meta"\nbackend-path = ["."]',
                1,
            ),
            "extra_requirement": original.replace(
                'requires = ["setuptools==82.0.1"]',
                'requires = ["setuptools==82.0.1", "build>=1.2"]',
                1,
            ),
        }
        for name, pyproject in variants.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                temporary_root = Path(temporary)
                candidate_source = temporary_root / "source"
                candidate_source.mkdir()
                (candidate_source / "pyproject.toml").write_text(
                    pyproject,
                    encoding="utf-8",
                )
                (candidate_source / "uv.lock").write_bytes(
                    (source_root / "uv.lock").read_bytes()
                )
                dist_dir = temporary_root / "dist"
                dist_dir.mkdir()
                with (
                    mock.patch.object(
                        compliance_module.shutil,
                        "which",
                        return_value="/usr/bin/uv",
                    ),
                    mock.patch.object(
                        compliance_module.subprocess,
                        "run",
                    ) as run,
                    self.assertRaisesRegex(
                        RuntimeError,
                        "build backend is not bound",
                    ),
                ):
                    compliance_module._build_distributions(
                        candidate_source,
                        dist_dir,
                    )
                run.assert_not_called()

    def test_build_requires_an_empty_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            dist_dir = Path(temporary)
            existing = dist_dir / "attacker.txt"
            existing.write_text("must survive\n", encoding="utf-8")

            with self.assertRaisesRegex(
                RuntimeError,
                "must be empty before build",
            ):
                compliance_module._build_distributions(
                    Path(__file__).resolve().parents[1],
                    dist_dir,
                )

            self.assertEqual("must survive\n", existing.read_text())

    def test_build_rejects_any_output_beyond_one_wheel_and_one_sdist(
        self,
    ) -> None:
        source_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            dist_dir = Path(temporary)

            def build(*_args: object, **_kwargs: object) -> mock.Mock:
                (dist_dir / "voistarcraft2-0.1.0-py3-none-any.whl").write_bytes(
                    b"wheel"
                )
                (dist_dir / "voistarcraft2-0.1.0.tar.gz").write_bytes(b"sdist")
                (dist_dir / "attacker.bin").write_bytes(b"extra")
                return mock.Mock(returncode=0, stdout="", stderr="")

            with (
                mock.patch.object(
                    compliance_module.shutil,
                    "which",
                    return_value="/usr/bin/uv",
                ),
                mock.patch.object(
                    compliance_module.subprocess,
                    "run",
                    side_effect=build,
                ),
                self.assertRaisesRegex(
                    RuntimeError,
                    "exactly one wheel and one sdist",
                ),
            ):
                compliance_module._build_distributions(
                    source_root,
                    dist_dir,
                )

    def test_build_removes_only_the_exact_uv_output_marker(self) -> None:
        source_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            dist_dir = Path(temporary)

            def build(*_args: object, **_kwargs: object) -> mock.Mock:
                (dist_dir / "voistarcraft2-0.1.0-py3-none-any.whl").write_bytes(
                    b"wheel"
                )
                (dist_dir / "voistarcraft2-0.1.0.tar.gz").write_bytes(b"sdist")
                (dist_dir / ".gitignore").write_bytes(b"*")
                return mock.Mock(returncode=0, stdout="", stderr="")

            with (
                mock.patch.object(
                    compliance_module.shutil,
                    "which",
                    return_value="/usr/bin/uv",
                ),
                mock.patch.object(
                    compliance_module.subprocess,
                    "run",
                    side_effect=build,
                ),
            ):
                compliance_module._build_distributions(
                    source_root,
                    dist_dir,
                )

            self.assertEqual(
                {
                    "voistarcraft2-0.1.0-py3-none-any.whl",
                    "voistarcraft2-0.1.0.tar.gz",
                },
                {path.name for path in dist_dir.iterdir()},
            )

    def test_build_rejects_a_modified_uv_output_marker(self) -> None:
        source_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            dist_dir = Path(temporary)

            def build(*_args: object, **_kwargs: object) -> mock.Mock:
                (dist_dir / "voistarcraft2-0.1.0-py3-none-any.whl").write_bytes(
                    b"wheel"
                )
                (dist_dir / "voistarcraft2-0.1.0.tar.gz").write_bytes(b"sdist")
                (dist_dir / ".gitignore").write_bytes(b"*\nattacker")
                return mock.Mock(returncode=0, stdout="", stderr="")

            with (
                mock.patch.object(
                    compliance_module.shutil,
                    "which",
                    return_value="/usr/bin/uv",
                ),
                mock.patch.object(
                    compliance_module.subprocess,
                    "run",
                    side_effect=build,
                ),
                self.assertRaisesRegex(
                    RuntimeError,
                    "invalid uv output marker",
                ),
            ):
                compliance_module._build_distributions(
                    source_root,
                    dist_dir,
                )


class ArchivePolicyTest(unittest.TestCase):
    @staticmethod
    def _gzip_with_header_metadata(
        payload: bytes,
        *,
        extra: bytes = b"",
        filename: bytes = b"",
        comment: bytes = b"",
        fhcrc: bool = False,
        corrupt_fhcrc: bool = False,
    ) -> bytes:
        flags = (
            (0x04 if extra else 0)
            | (0x08 if filename else 0)
            | (0x10 if comment else 0)
            | (0x02 if fhcrc else 0)
        )
        header = struct.pack(
            "<2sBBIBB",
            b"\x1f\x8b",
            8,
            flags,
            0,
            0,
            255,
        )
        if extra:
            header += len(extra).to_bytes(2, "little") + extra
        if filename:
            header += filename + b"\0"
        if comment:
            header += comment + b"\0"
        if fhcrc:
            checksum = zlib.crc32(header) & 0xFFFF
            if corrupt_fhcrc:
                checksum ^= 0xFFFF
            header += checksum.to_bytes(2, "little")
        compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
        compressed = compressor.compress(payload) + compressor.flush()
        trailer = struct.pack(
            "<II",
            zlib.crc32(payload) & 0xFFFFFFFF,
            len(payload) & 0xFFFFFFFF,
        )
        return header + compressed + trailer

    @staticmethod
    def _write_zip_with_local_only_extra(
        path: Path,
        *,
        filename: str,
        payload: bytes,
        extra: bytes,
    ) -> None:
        filename_bytes = filename.encode("utf-8")
        crc = zlib.crc32(payload) & 0xFFFFFFFF
        local_header = struct.pack(
            "<4s5H3I2H",
            b"PK\x03\x04",
            20,
            0x800,
            0,
            0,
            0,
            crc,
            len(payload),
            len(payload),
            len(filename_bytes),
            len(extra),
        )
        local_record = local_header + filename_bytes + extra + payload
        central_header = struct.pack(
            "<4s6H3I5H2I",
            b"PK\x01\x02",
            20,
            20,
            0x800,
            0,
            0,
            0,
            crc,
            len(payload),
            len(payload),
            len(filename_bytes),
            0,
            0,
            0,
            0,
            0,
            0,
        )
        central_record = central_header + filename_bytes
        end_record = struct.pack(
            "<4s4H2IH",
            b"PK\x05\x06",
            0,
            0,
            1,
            1,
            len(central_record),
            len(local_record),
            0,
        )
        path.write_bytes(local_record + central_record + end_record)

    @staticmethod
    def _write_raw_zip(
        path: Path,
        *,
        local_name: bytes,
        central_name: bytes | None = None,
        payload: bytes = b"safe",
        compression: int = zipfile.ZIP_STORED,
        compression_slack: bytes = b"",
        preamble: bytes = b"",
        trailing: bytes = b"",
        data_descriptor: bool = False,
        concealed_local_fixed: bytes = b"",
    ) -> None:
        central_name = central_name if central_name is not None else local_name
        flags = 0x800 | (0x08 if data_descriptor else 0)
        crc = zlib.crc32(payload) & 0xFFFFFFFF
        if compression == zipfile.ZIP_STORED:
            encoded_payload = payload
        elif compression == zipfile.ZIP_DEFLATED:
            compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
            encoded_payload = (
                compressor.compress(payload) + compressor.flush()
            )
        else:
            encoded_payload = payload
        encoded_payload += compression_slack
        compressed_size = len(encoded_payload)
        uncompressed_size = len(payload)
        if concealed_local_fixed:
            if len(concealed_local_fixed) != 16:
                raise ValueError("concealed local fields must be 16 bytes")
            (
                local_time,
                local_date,
                local_crc,
                local_compressed_size,
                local_uncompressed_size,
            ) = struct.unpack("<HHIII", concealed_local_fixed)
        else:
            local_time = 0
            local_date = 0
            local_crc = 0 if data_descriptor else crc
            local_compressed_size = (
                0 if data_descriptor else compressed_size
            )
            local_uncompressed_size = (
                0 if data_descriptor else uncompressed_size
            )
        local_header = struct.pack(
            "<4s5H3I2H",
            b"PK\x03\x04",
            20,
            flags,
            compression,
            local_time,
            local_date,
            local_crc,
            local_compressed_size,
            local_uncompressed_size,
            len(local_name),
            0,
        )
        descriptor = (
            b"PK\x07\x08"
            + struct.pack(
                "<III",
                crc,
                compressed_size,
                uncompressed_size,
            )
            if data_descriptor
            else b""
        )
        local_record = (
            local_header + local_name + encoded_payload + descriptor
        )
        central_header = struct.pack(
            "<4s6H3I5H2I",
            b"PK\x01\x02",
            20,
            20,
            flags,
            compression,
            0,
            0,
            crc,
            compressed_size,
            uncompressed_size,
            len(central_name),
            0,
            0,
            0,
            0,
            0,
            len(preamble),
        )
        central_record = central_header + central_name
        end_record = struct.pack(
            "<4s4H2IH",
            b"PK\x05\x06",
            0,
            0,
            1,
            1,
            len(central_record),
            len(preamble) + len(local_record),
            0,
        )
        path.write_bytes(
            preamble + local_record + central_record + end_record + trailing
        )

    @staticmethod
    def _tar_payload(*members: tuple[str, bytes]) -> bytes:
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w") as archive:
            for name, payload in members:
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
        return output.getvalue()

    @staticmethod
    def _tar_padding(payload: bytes) -> bytes:
        padding = (-len(payload)) % 512
        return payload + (b"\0" * padding)

    @staticmethod
    def _pax_record(key: bytes, value: bytes) -> bytes:
        body = key + b"=" + value + b"\n"
        length = len(body) + 2
        while True:
            encoded = str(length).encode()
            adjusted = len(encoded) + 1 + len(body)
            if adjusted == length:
                return encoded + b" " + body
            length = adjusted

    @classmethod
    def _tar_with_extension(
        cls,
        *,
        extension_type: bytes,
        extension_payload: bytes,
        member_name: str,
        member_payload: bytes = b"safe",
    ) -> bytes:
        extension = tarfile.TarInfo("././@PaxHeader")
        extension.type = extension_type
        extension.size = len(extension_payload)
        member = tarfile.TarInfo(member_name)
        member.size = len(member_payload)
        return (
            extension.tobuf(format=tarfile.USTAR_FORMAT)
            + cls._tar_padding(extension_payload)
            + member.tobuf(format=tarfile.USTAR_FORMAT)
            + cls._tar_padding(member_payload)
            + (b"\0" * 1024)
        )

    @staticmethod
    def _replace_tar_checksum(block: bytearray) -> None:
        block[148:156] = b" " * 8
        checksum = sum(block)
        block[148:156] = f"{checksum:06o}\0 ".encode("ascii")

    @staticmethod
    def _metadata_findings(snapshot: ArchiveSnapshot) -> list[dict[str, object]]:
        return [
            finding
            for name, payload in snapshot.metadata.items()
            for finding in scan_payload(
                f"{snapshot.kind}/{name}",
                payload,
                allow_safe_fixtures=False,
            )
        ] + [
            dict(finding)
            for finding in snapshot.prescanned_findings
        ]

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

    def test_wheel_inspection_rejects_and_scans_global_comments(self) -> None:
        secret = ("sk-" + "liveabcdefghijklmnop").encode()
        with tempfile.TemporaryDirectory() as temporary:
            wheel_path = Path(temporary) / "candidate.whl"
            with zipfile.ZipFile(wheel_path, "w") as archive:
                archive.writestr("starcraft_commander/runtime_data.py", "safe")
                archive.comment = secret

            snapshot = inspect_wheel(wheel_path)

        self.assertIn(
            "unexpected_archive_metadata",
            {str(item["code"]) for item in snapshot.blockers},
        )
        findings = self._metadata_findings(snapshot)
        self.assertEqual({"api_key"}, {item["rule_id"] for item in findings})
        self.assertEqual(1, len({item["fingerprint"] for item in findings}))

    def test_wheel_metadata_blocker_does_not_suppress_payload_scan(self) -> None:
        secret = ("sk-" + "liveabcdefghijklmnop").encode()
        entry = "starcraft_commander/runtime_data.py"
        with tempfile.TemporaryDirectory() as temporary:
            wheel_path = Path(temporary) / "candidate.whl"
            with zipfile.ZipFile(wheel_path, "w") as archive:
                archive.writestr(entry, secret)
                archive.comment = b"forbidden comment"

            snapshot = inspect_wheel(wheel_path)

        self.assertIn(
            "unexpected_archive_metadata",
            {str(item["code"]) for item in snapshot.blockers},
        )
        self.assertEqual(secret, snapshot.files[entry])
        self.assertEqual(
            {"api_key"},
            {
                finding["rule_id"]
                for finding in scan_payload(
                    f"wheel/{entry}",
                    snapshot.files[entry],
                    allow_safe_fixtures=False,
                )
            },
        )

    def test_wheel_prescans_metadata_cap_overflow_and_keeps_files(self) -> None:
        metadata_secret = ("sk-" + "centralabcdefghijklmnop").encode()
        payload_secret = ("sk-" + "payloadabcdefghijklmnop").encode()
        extra_payload_size = 65_531
        safe_extra = struct.pack(
            "<HH",
            0xCAFE,
            extra_payload_size,
        ) + (b"X" * extra_payload_size)
        secret_extra_payload = (
            b"X" * (extra_payload_size - len(metadata_secret) - 1)
        ) + b"\n" + metadata_secret
        secret_extra = struct.pack(
            "<HH",
            0xCAFE,
            len(secret_extra_payload),
        ) + secret_extra_payload
        final_entry = "starcraft_commander/runtime_data.py"

        with tempfile.TemporaryDirectory() as temporary:
            wheel_path = Path(temporary) / "candidate.whl"
            with zipfile.ZipFile(wheel_path, "w") as archive:
                for index in range(128):
                    info = zipfile.ZipInfo(
                        f"starcraft_commander/padding/{index:04d}.dat"
                    )
                    info.extra = safe_extra
                    archive.writestr(info, b"safe")
                info = zipfile.ZipInfo(final_entry)
                info.extra = secret_extra
                archive.writestr(info, payload_secret)

            snapshot = inspect_wheel(wheel_path)

        blocker_codes = {str(item["code"]) for item in snapshot.blockers}
        self.assertIn("archive_metadata_limit_exceeded", blocker_codes)
        self.assertEqual(payload_secret, snapshot.files[final_entry])
        metadata_findings = self._metadata_findings(snapshot)
        self.assertIn(
            compliance_module.sha256_bytes(
                f"api_key\0{metadata_secret.decode()}".encode()
            ),
            {str(item["fingerprint"]) for item in metadata_findings},
        )
        self.assertEqual(
            {"api_key"},
            {
                str(item["rule_id"])
                for item in scan_payload(
                    f"wheel/{final_entry}",
                    snapshot.files[final_entry],
                    allow_safe_fixtures=False,
                )
            },
        )
        artifact_evidence = compliance_module._artifact_evidence(snapshot)
        self.assertTrue(
            {
                str(item["path"]).removeprefix("wheel/")
                for item in snapshot.prescanned_inputs
            }.issubset(artifact_evidence["metadata_manifest"])
        )

    def test_wheel_inspection_rejects_and_scans_entry_extras(self) -> None:
        secret = ("sk-" + "liveabcdefghijklmnop").encode()
        info = zipfile.ZipInfo("starcraft_commander/runtime_data.py")
        info.extra = (
            b"\xfe\xca"
            + len(secret).to_bytes(2, "little")
            + secret
        )
        with tempfile.TemporaryDirectory() as temporary:
            wheel_path = Path(temporary) / "candidate.whl"
            with zipfile.ZipFile(wheel_path, "w") as archive:
                archive.writestr(info, "safe")

            snapshot = inspect_wheel(wheel_path)

        self.assertIn(
            "unexpected_archive_metadata",
            {str(item["code"]) for item in snapshot.blockers},
        )
        findings = self._metadata_findings(snapshot)
        self.assertEqual({"api_key"}, {item["rule_id"] for item in findings})
        self.assertEqual(1, len({item["fingerprint"] for item in findings}))

    def test_wheel_rejects_and_scans_local_header_only_extra(self) -> None:
        secret = ("sk-" + "liveabcdefghijklmnop").encode()
        extra = (
            b"\xfe\xca"
            + len(secret).to_bytes(2, "little")
            + secret
        )
        with tempfile.TemporaryDirectory() as temporary:
            wheel_path = Path(temporary) / "candidate.whl"
            self._write_zip_with_local_only_extra(
                wheel_path,
                filename="starcraft_commander/runtime_data.py",
                payload=b"safe",
                extra=extra,
            )

            snapshot = inspect_wheel(wheel_path)

        self.assertIn(
            "archive_header_mismatch",
            {str(item["code"]) for item in snapshot.blockers},
        )
        findings = self._metadata_findings(snapshot)
        self.assertEqual({"api_key"}, {item["rule_id"] for item in findings})
        self.assertEqual(1, len({item["fingerprint"] for item in findings}))

    def test_sdist_rejects_and_scans_noncanonical_pax_metadata(self) -> None:
        secret = "sk-" + "liveabcdefghijklmnop"
        with tempfile.TemporaryDirectory() as temporary:
            sdist_path = Path(temporary) / "voistarcraft2-0.1.0.tar.gz"
            payload = b"safe"
            info = tarfile.TarInfo(
                "voistarcraft2-0.1.0/"
                "starcraft_commander/runtime_data.py"
            )
            info.size = len(payload)
            info.pax_headers = {"comment": secret}
            with tarfile.open(
                sdist_path,
                "w:gz",
                format=tarfile.PAX_FORMAT,
            ) as archive:
                archive.addfile(info, io.BytesIO(payload))

            snapshot = compliance_module.inspect_sdist(sdist_path)

        self.assertIn(
            "unexpected_archive_metadata",
            {str(item["code"]) for item in snapshot.blockers},
        )
        findings = self._metadata_findings(snapshot)
        self.assertEqual({"api_key"}, {item["rule_id"] for item in findings})
        self.assertEqual(1, len({item["fingerprint"] for item in findings}))

    def test_sdist_scans_tar_owner_and_group_metadata(self) -> None:
        secret = "sk-" + "liveabcdefghijklmnop"
        with tempfile.TemporaryDirectory() as temporary:
            sdist_path = Path(temporary) / "voistarcraft2-0.1.0.tar.gz"
            payload = b"safe"
            info = tarfile.TarInfo(
                "voistarcraft2-0.1.0/"
                "starcraft_commander/runtime_data.py"
            )
            info.size = len(payload)
            info.uname = secret
            info.gname = "wheel"
            with tarfile.open(sdist_path, "w:gz") as archive:
                archive.addfile(info, io.BytesIO(payload))

            snapshot = compliance_module.inspect_sdist(sdist_path)

        findings = self._metadata_findings(snapshot)
        self.assertEqual({"api_key"}, {item["rule_id"] for item in findings})
        self.assertEqual(1, len({item["fingerprint"] for item in findings}))

    def test_sdist_rejects_and_scans_noncanonical_gzip_filename(self) -> None:
        secret = "sk-" + "liveabcdefghijklmnop"
        tar_payload = io.BytesIO()
        member_payload = b"safe"
        member = tarfile.TarInfo(
            "voistarcraft2-0.1.0/"
            "starcraft_commander/runtime_data.py"
        )
        member.size = len(member_payload)
        with tarfile.open(fileobj=tar_payload, mode="w") as archive:
            archive.addfile(member, io.BytesIO(member_payload))
        compressed = io.BytesIO()
        with gzip.GzipFile(
            fileobj=compressed,
            mode="wb",
            filename=secret,
        ) as archive:
            archive.write(tar_payload.getvalue())

        with tempfile.TemporaryDirectory() as temporary:
            sdist_path = Path(temporary) / "voistarcraft2-0.1.0.tar.gz"
            sdist_path.write_bytes(compressed.getvalue())

            snapshot = compliance_module.inspect_sdist(sdist_path)

        self.assertIn(
            "unexpected_archive_metadata",
            {str(item["code"]) for item in snapshot.blockers},
        )
        findings = self._metadata_findings(snapshot)
        self.assertEqual({"api_key"}, {item["rule_id"] for item in findings})
        self.assertEqual(1, len({item["fingerprint"] for item in findings}))

    def test_sdist_rejects_and_scans_gzip_extra_and_comment(self) -> None:
        extra_secret = ("sk-" + "liveabcdefghijklmnop").encode()
        comment_secret = ("sk-" + "testabcdefghijklmnop").encode()
        tar_payload = io.BytesIO()
        member_payload = b"safe"
        member = tarfile.TarInfo(
            "voistarcraft2-0.1.0/"
            "starcraft_commander/runtime_data.py"
        )
        member.size = len(member_payload)
        with tarfile.open(fileobj=tar_payload, mode="w") as archive:
            archive.addfile(member, io.BytesIO(member_payload))

        with tempfile.TemporaryDirectory() as temporary:
            sdist_path = Path(temporary) / "voistarcraft2-0.1.0.tar.gz"
            sdist_path.write_bytes(
                self._gzip_with_header_metadata(
                    tar_payload.getvalue(),
                    extra=extra_secret,
                    comment=comment_secret,
                )
            )

            snapshot = compliance_module.inspect_sdist(sdist_path)

        metadata_blockers = {
            str(item.get("metadata", ""))
            for item in snapshot.blockers
            if item.get("code") == "unexpected_archive_metadata"
        }
        self.assertEqual(
            {"gzip_comment", "gzip_extra"},
            metadata_blockers,
        )
        findings = self._metadata_findings(snapshot)
        self.assertEqual({"api_key"}, {item["rule_id"] for item in findings})
        self.assertTrue(
            {
                compliance_module.sha256_bytes(
                    f"api_key\0{secret.decode()}".encode()
                )
                for secret in (extra_secret, comment_secret)
            }.issubset({item["fingerprint"] for item in findings})
        )

    def test_sdist_metadata_blocker_does_not_suppress_payload_scan(self) -> None:
        secret = ("sk-" + "liveabcdefghijklmnop").encode()
        entry = (
            "voistarcraft2-0.1.0/"
            "starcraft_commander/runtime_data.py"
        )
        tar_payload = self._tar_payload((entry, secret))
        with tempfile.TemporaryDirectory() as temporary:
            sdist_path = Path(temporary) / "voistarcraft2-0.1.0.tar.gz"
            sdist_path.write_bytes(
                self._gzip_with_header_metadata(
                    tar_payload,
                    comment=b"forbidden comment",
                )
            )

            snapshot = compliance_module.inspect_sdist(sdist_path)

        self.assertIn(
            "unexpected_archive_metadata",
            {str(item["code"]) for item in snapshot.blockers},
        )
        self.assertEqual(secret, snapshot.files[entry])
        self.assertEqual(
            {"api_key"},
            {
                finding["rule_id"]
                for finding in scan_payload(
                    f"sdist/{entry}",
                    snapshot.files[entry],
                    allow_safe_fixtures=False,
                )
            },
        )

    def test_sdist_rejects_and_scans_concatenated_gzip_metadata(self) -> None:
        comment_secret = ("sk-" + "liveabcdefghijklmnop").encode()
        payload_secret = ("sk-" + "memberabcdefghijklmnop").encode()
        large_member_payload = (
            (b"A" * (compliance_module.MAX_ARCHIVE_METADATA_BYTES + 1))
            + b"\n"
            + payload_secret
        )
        tar_payload = io.BytesIO()
        member_payload = b"safe"
        member = tarfile.TarInfo(
            "voistarcraft2-0.1.0/"
            "starcraft_commander/runtime_data.py"
        )
        member.size = len(member_payload)
        with tarfile.open(fileobj=tar_payload, mode="w") as archive:
            archive.addfile(member, io.BytesIO(member_payload))

        with tempfile.TemporaryDirectory() as temporary:
            sdist_path = Path(temporary) / "voistarcraft2-0.1.0.tar.gz"
            sdist_path.write_bytes(
                self._gzip_with_header_metadata(tar_payload.getvalue())
                + self._gzip_with_header_metadata(
                    large_member_payload,
                    comment=comment_secret,
                )
            )

            snapshot = compliance_module.inspect_sdist(sdist_path)

        blocker_codes = {str(item["code"]) for item in snapshot.blockers}
        self.assertIn("unexpected_gzip_member", blocker_codes)
        self.assertIn("unexpected_archive_metadata", blocker_codes)
        self.assertIn("archive_metadata_limit_exceeded", blocker_codes)
        self.assertEqual(1, len(snapshot.prescanned_inputs))
        findings = self._metadata_findings(snapshot)
        self.assertEqual({"api_key"}, {item["rule_id"] for item in findings})
        self.assertEqual(2, len({item["fingerprint"] for item in findings}))
        self.assertTrue(
            {
                compliance_module.sha256_bytes(
                    f"api_key\0{secret.decode()}".encode()
                )
                for secret in (comment_secret, payload_secret)
            }.issubset({item["fingerprint"] for item in findings})
        )

    def test_sdist_scans_members_after_cumulative_gzip_metadata_exhaustion(
        self,
    ) -> None:
        payload_secret = ("sk-" + "payloadabcdefghijklmnop").encode()
        header_secret = ("sk-" + "headerabcdefghijklmnop").encode()
        comment = b"C" * 1_000_000
        tar_payload = self._tar_payload(
            (
                "voistarcraft2-0.1.0/"
                "starcraft_commander/runtime_data.py",
                b"safe",
            )
        )
        members = [
            self._gzip_with_header_metadata(tar_payload, comment=comment),
            *(
                self._gzip_with_header_metadata(b"", comment=comment)
                for _ in range(3)
            ),
            self._gzip_with_header_metadata(
                (b"A" * 400_000) + b"\n" + payload_secret,
                comment=(b"C" * 500_000) + b"\n" + header_secret,
            ),
        ]

        with tempfile.TemporaryDirectory() as temporary:
            sdist_path = Path(temporary) / "voistarcraft2-0.1.0.tar.gz"
            sdist_path.write_bytes(b"".join(members))

            snapshot = compliance_module.inspect_sdist(sdist_path)

        blocker_codes = {str(item["code"]) for item in snapshot.blockers}
        self.assertIn("archive_metadata_limit_exceeded", blocker_codes)
        prescanned_paths = {
            str(item["path"]) for item in snapshot.prescanned_inputs
        }
        self.assertTrue(
            any(path.endswith("/comment") for path in prescanned_paths)
        )
        self.assertTrue(
            any(
                path.endswith("/uncompressed-payload")
                for path in prescanned_paths
            )
        )
        findings = self._metadata_findings(snapshot)
        self.assertTrue(
            {
                compliance_module.sha256_bytes(
                    f"api_key\0{secret.decode()}".encode()
                )
                for secret in (header_secret, payload_secret)
            }.issubset({str(item["fingerprint"]) for item in findings})
        )

    def test_sdist_prescans_oversized_pax_and_keeps_files(self) -> None:
        metadata_secret = ("sk-" + "paxabcdefghijklmnop").encode()
        payload_secret = ("sk-" + "payloadabcdefghijklmnop").encode()
        pax_value = (
            b"P"
            * (
                compliance_module.MAX_ARCHIVE_METADATA_BYTES
                + 1
                - len(metadata_secret)
                - 1
            )
        ) + b"\n" + metadata_secret
        pax_payload = self._pax_record(b"comment", pax_value)
        entry = (
            "voistarcraft2-0.1.0/"
            "starcraft_commander/runtime_data.py"
        )
        tar_payload = self._tar_with_extension(
            extension_type=tarfile.XHDTYPE,
            extension_payload=pax_payload,
            member_name=entry,
            member_payload=payload_secret,
        )

        with tempfile.TemporaryDirectory() as temporary:
            sdist_path = Path(temporary) / "voistarcraft2-0.1.0.tar.gz"
            sdist_path.write_bytes(
                self._gzip_with_header_metadata(tar_payload)
            )

            snapshot = compliance_module.inspect_sdist(sdist_path)

        blocker_codes = {str(item["code"]) for item in snapshot.blockers}
        self.assertIn("archive_metadata_limit_exceeded", blocker_codes)
        self.assertEqual(payload_secret, snapshot.files[entry])
        metadata_findings = self._metadata_findings(snapshot)
        self.assertIn(
            compliance_module.sha256_bytes(
                f"api_key\0{metadata_secret.decode()}".encode()
            ),
            {str(item["fingerprint"]) for item in metadata_findings},
        )
        self.assertEqual(
            {"api_key"},
            {
                str(item["rule_id"])
                for item in scan_payload(
                    f"sdist/{entry}",
                    snapshot.files[entry],
                    allow_safe_fixtures=False,
                )
            },
        )
        artifact_evidence = compliance_module._artifact_evidence(snapshot)
        self.assertTrue(
            {
                str(item["path"]).removeprefix("sdist/")
                for item in snapshot.prescanned_inputs
            }.issubset(artifact_evidence["metadata_manifest"])
        )

    def test_sdist_keeps_files_after_oversized_directory_payload(self) -> None:
        metadata_secret = ("sk-" + "directoryabcdefghijklmnop").encode()
        payload_secret = ("sk-" + "payloadabcdefghijklmnop").encode()
        root = "voistarcraft2-0.1.0"
        directory_payload = (
            b"D" * compliance_module.MAX_ARCHIVE_METADATA_BYTES
        ) + b"\n" + metadata_secret
        directory = tarfile.TarInfo(f"{root}/oversized/")
        directory.type = tarfile.DIRTYPE
        directory.size = len(directory_payload)
        entry = f"{root}/starcraft_commander/runtime_data.py"
        member = tarfile.TarInfo(entry)
        member.size = len(payload_secret)
        tar_payload = (
            directory.tobuf(format=tarfile.USTAR_FORMAT)
            + self._tar_padding(directory_payload)
            + member.tobuf(format=tarfile.USTAR_FORMAT)
            + self._tar_padding(payload_secret)
            + (b"\0" * 1024)
        )

        with tempfile.TemporaryDirectory() as temporary:
            sdist_path = Path(temporary) / "voistarcraft2-0.1.0.tar.gz"
            sdist_path.write_bytes(
                self._gzip_with_header_metadata(tar_payload)
            )

            snapshot = compliance_module.inspect_sdist(sdist_path)

        blocker_codes = {str(item["code"]) for item in snapshot.blockers}
        self.assertIn("archive_directory_payload", blocker_codes)
        self.assertIn("archive_metadata_limit_exceeded", blocker_codes)
        findings = self._metadata_findings(snapshot)
        self.assertTrue(
            {
                compliance_module.sha256_bytes(
                    f"api_key\0{secret.decode()}".encode()
                )
                for secret in (metadata_secret, payload_secret)
            }.issubset({str(item["fingerprint"]) for item in findings})
        )

    def test_sdist_keeps_files_after_metadata_cap_padding_overflow(self) -> None:
        metadata_secret = ("sk-" + "paddingabcdefghijklmnop").encode()
        payload_secret = ("sk-" + "payloadabcdefghijklmnop").encode()
        root = "voistarcraft2-0.1.0"
        pax_value = b"P" * (
            compliance_module.MAX_ARCHIVE_METADATA_BYTES - 2_300
        )
        pax_payload = self._pax_record(b"comment", pax_value)
        extension = tarfile.TarInfo("././@PaxHeader")
        extension.type = tarfile.XHDTYPE
        extension.size = len(pax_payload)
        padded_entry = tarfile.TarInfo(f"{root}/padding-source.bin")
        padded_entry.size = 1
        padding = metadata_secret + b"\n" + (
            b"Q" * (510 - len(metadata_secret))
        )
        final_entry = f"{root}/starcraft_commander/runtime_data.py"
        member = tarfile.TarInfo(final_entry)
        member.size = len(payload_secret)
        tar_payload = (
            extension.tobuf(format=tarfile.USTAR_FORMAT)
            + self._tar_padding(pax_payload)
            + padded_entry.tobuf(format=tarfile.USTAR_FORMAT)
            + b"x"
            + padding
            + member.tobuf(format=tarfile.USTAR_FORMAT)
            + self._tar_padding(payload_secret)
            + (b"\0" * 1024)
        )

        with tempfile.TemporaryDirectory() as temporary:
            sdist_path = Path(temporary) / "voistarcraft2-0.1.0.tar.gz"
            sdist_path.write_bytes(
                self._gzip_with_header_metadata(tar_payload)
            )

            snapshot = compliance_module.inspect_sdist(sdist_path)

        blocker_codes = {str(item["code"]) for item in snapshot.blockers}
        self.assertIn("archive_metadata_limit_exceeded", blocker_codes)
        self.assertIn("noncanonical_tar_member_padding", blocker_codes)
        self.assertEqual(payload_secret, snapshot.files[final_entry])
        findings = self._metadata_findings(snapshot)
        self.assertIn(
            compliance_module.sha256_bytes(
                f"api_key\0{metadata_secret.decode()}".encode()
            ),
            {str(item["fingerprint"]) for item in findings},
        )

    def test_sdist_rejects_and_scans_invalid_trailing_gzip_bytes(self) -> None:
        secret = ("sk-" + "liveabcdefghijklmnop").encode()
        tar_payload = self._tar_payload(
            (
                "voistarcraft2-0.1.0/"
                "starcraft_commander/runtime_data.py",
                b"safe",
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            sdist_path = Path(temporary) / "voistarcraft2-0.1.0.tar.gz"
            sdist_path.write_bytes(
                self._gzip_with_header_metadata(tar_payload) + secret
            )

            snapshot = compliance_module.inspect_sdist(sdist_path)

        blocker_codes = {str(item["code"]) for item in snapshot.blockers}
        self.assertIn("invalid_gzip_header", blocker_codes)
        self.assertIn("unexpected_archive_bytes", blocker_codes)
        findings = self._metadata_findings(snapshot)
        self.assertEqual({"api_key"}, {item["rule_id"] for item in findings})

    def test_sdist_prescans_invalid_remainder_after_metadata_exhaustion(
        self,
    ) -> None:
        secret = ("sk-" + "remainderabcdefghijklmnop").encode()
        comment = b"C" * 1_045_000
        tar_payload = self._tar_payload(
            (
                "voistarcraft2-0.1.0/"
                "starcraft_commander/runtime_data.py",
                b"safe",
            )
        )
        members = [
            self._gzip_with_header_metadata(tar_payload, comment=comment),
            *(
                self._gzip_with_header_metadata(b"", comment=comment)
                for _ in range(3)
            ),
        ]
        invalid_remainder = (b"R" * 100_000) + b"\n" + secret

        with tempfile.TemporaryDirectory() as temporary:
            sdist_path = Path(temporary) / "voistarcraft2-0.1.0.tar.gz"
            sdist_path.write_bytes(
                b"".join(members) + invalid_remainder
            )

            snapshot = compliance_module.inspect_sdist(sdist_path)

        blocker_codes = {str(item["code"]) for item in snapshot.blockers}
        self.assertIn("archive_metadata_limit_exceeded", blocker_codes)
        self.assertIn("invalid_gzip_header", blocker_codes)
        self.assertTrue(
            any(
                str(item["path"]).endswith("/unparsed/0004")
                for item in snapshot.prescanned_inputs
            )
        )
        findings = self._metadata_findings(snapshot)
        self.assertIn(
            compliance_module.sha256_bytes(
                f"api_key\0{secret.decode()}".encode()
            ),
            {str(item["fingerprint"]) for item in findings},
        )

    def test_wheel_scans_data_descriptor_local_fixed_fields(self) -> None:
        secret = ("sk-" + "abcdefghijkl").encode()
        with tempfile.TemporaryDirectory() as temporary:
            wheel_path = Path(temporary) / "candidate.whl"
            self._write_raw_zip(
                wheel_path,
                local_name=b"starcraft_commander/runtime_data.py",
                data_descriptor=True,
                concealed_local_fixed=secret.ljust(16, b"x"),
            )

            snapshot = inspect_wheel(wheel_path)

        self.assertIn(
            "archive_header_mismatch",
            {str(item["code"]) for item in snapshot.blockers},
        )
        findings = self._metadata_findings(snapshot)
        self.assertEqual({"api_key"}, {item["rule_id"] for item in findings})
        self.assertEqual(1, len({item["fingerprint"] for item in findings}))

    def test_wheel_rejects_and_scans_unexplained_outer_bytes(self) -> None:
        secret = ("sk-" + "liveabcdefghijklmnop").encode()
        variants = {
            "preamble": {"preamble": secret},
            "post_eocd": {"trailing": secret},
        }
        for name, arguments in variants.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                wheel_path = Path(temporary) / "candidate.whl"
                self._write_raw_zip(
                    wheel_path,
                    local_name=b"starcraft_commander/runtime_data.py",
                    **arguments,
                )

                snapshot = inspect_wheel(wheel_path)

            self.assertIn(
                "unexpected_archive_bytes",
                {str(item["code"]) for item in snapshot.blockers},
            )
            findings = self._metadata_findings(snapshot)
            self.assertEqual(
                {"api_key"},
                {item["rule_id"] for item in findings},
            )
            self.assertEqual(
                1,
                len({item["fingerprint"] for item in findings}),
            )

    def test_wheel_rejects_and_scans_compression_stream_slack(self) -> None:
        secret = ("sk-" + "liveabcdefghijklmnop").encode()
        for compression in (
            zipfile.ZIP_STORED,
            zipfile.ZIP_DEFLATED,
        ):
            with (
                self.subTest(compression=compression),
                tempfile.TemporaryDirectory() as temporary,
            ):
                wheel_path = Path(temporary) / "candidate.whl"
                self._write_raw_zip(
                    wheel_path,
                    local_name=b"starcraft_commander/runtime_data.py",
                    compression=compression,
                    compression_slack=secret,
                )

                snapshot = inspect_wheel(wheel_path)

            blocker_codes = {
                str(item["code"]) for item in snapshot.blockers
            }
            self.assertIn("invalid_zip_compressed_payload", blocker_codes)
            self.assertIn("unexpected_archive_bytes", blocker_codes)
            findings = self._metadata_findings(snapshot)
            self.assertEqual(
                {"api_key"},
                {item["rule_id"] for item in findings},
            )
            self.assertEqual(
                1,
                len({item["fingerprint"] for item in findings}),
            )

    def test_wheel_rejects_and_scans_directory_payload(self) -> None:
        secret = ("sk-" + "liveabcdefghijklmnop").encode()
        with tempfile.TemporaryDirectory() as temporary:
            wheel_path = Path(temporary) / "candidate.whl"
            self._write_raw_zip(
                wheel_path,
                local_name=b"starcraft_commander/",
                payload=secret,
            )

            snapshot = inspect_wheel(wheel_path)

        self.assertIn(
            "archive_directory_payload",
            {str(item["code"]) for item in snapshot.blockers},
        )
        findings = self._metadata_findings(snapshot)
        self.assertEqual({"api_key"}, {item["rule_id"] for item in findings})

    def test_wheel_rejects_nul_in_local_and_central_names(self) -> None:
        safe = b"starcraft_commander/runtime_data.py"
        secret = ("sk-" + "liveabcdefghijklmnop").encode()
        hidden = safe + b"\0" + secret
        variants = {
            "identical": (hidden, hidden),
            "central_only": (safe, hidden),
            "local_only": (hidden, safe),
        }
        for name, (local_name, central_name) in variants.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                wheel_path = Path(temporary) / "candidate.whl"
                self._write_raw_zip(
                    wheel_path,
                    local_name=local_name,
                    central_name=central_name,
                )

                snapshot = inspect_wheel(wheel_path)

            self.assertIn(
                "invalid_zip_filename",
                {str(item["code"]) for item in snapshot.blockers},
            )
            findings = self._metadata_findings(snapshot)
            self.assertEqual(
                {"api_key"},
                {item["rule_id"] for item in findings},
            )
            self.assertEqual(
                1,
                len({item["fingerprint"] for item in findings}),
            )

    def test_sdist_rejects_and_scans_tar_directory_payload(self) -> None:
        secret = ("sk-" + "liveabcdefghijklmnop").encode()
        directory = tarfile.TarInfo("voistarcraft2-0.1.0/starcraft_commander/")
        directory.type = tarfile.DIRTYPE
        directory.size = len(secret)
        tar_payload = (
            directory.tobuf(format=tarfile.USTAR_FORMAT)
            + self._tar_padding(secret)
            + (b"\0" * 1024)
        )
        with tempfile.TemporaryDirectory() as temporary:
            sdist_path = Path(temporary) / "voistarcraft2-0.1.0.tar.gz"
            sdist_path.write_bytes(
                self._gzip_with_header_metadata(tar_payload)
            )

            snapshot = compliance_module.inspect_sdist(sdist_path)

        self.assertIn(
            "archive_directory_payload",
            {str(item["code"]) for item in snapshot.blockers},
        )
        findings = self._metadata_findings(snapshot)
        self.assertEqual({"api_key"}, {item["rule_id"] for item in findings})

    def test_tar_limits_precede_regular_and_extension_payload_slices(
        self,
    ) -> None:
        class GuardedTarBytes(bytes):
            guarded_start = 512

            def __getitem__(self, key: object) -> object:
                if isinstance(key, slice):
                    start = 0 if key.start is None else key.start
                    stop = len(self) if key.stop is None else key.stop
                    if start == self.guarded_start and stop - start > 512:
                        raise AssertionError(
                            "oversized TAR payload was materialized"
                        )
                return super().__getitem__(key)

        cases = (
            ("regular", tarfile.REGTYPE, 1024, "member"),
            ("pax", tarfile.XHDTYPE, 8192, "metadata"),
            ("gnu", tarfile.GNUTYPE_LONGNAME, 8192, "metadata"),
        )
        for name, type_flag, size, expected_limit in cases:
            with self.subTest(name=name):
                info = tarfile.TarInfo("oversized")
                info.type = type_flag
                info.size = size
                raw = GuardedTarBytes(
                    info.tobuf(format=tarfile.USTAR_FORMAT)
                    + (b"x" * size)
                    + (b"\0" * 1024)
                )
                metadata: dict[str, bytes] = (
                    compliance_module._ArchiveMetadata()
                )
                blockers: list[dict[str, object]] = []
                patches = (
                    mock.patch.object(
                        compliance_module,
                        "MAX_ARCHIVE_MEMBER_BYTES",
                        512,
                    )
                    if expected_limit == "member"
                    else mock.patch.object(
                        compliance_module,
                        "MAX_ARCHIVE_METADATA_BYTES",
                        4096,
                    )
                )
                with patches:
                    compliance_module._raw_tar_metadata(
                        raw,
                        metadata,
                        blockers,
                    )

                self.assertIn(
                    f"archive_{expected_limit}_limit_exceeded",
                    {str(item["code"]) for item in blockers},
                )

    def test_wheel_limits_stop_before_zipfile_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            wheel_path = Path(temporary) / "candidate.whl"
            with zipfile.ZipFile(wheel_path, "w") as archive:
                archive.writestr(
                    "starcraft_commander/runtime_data.py",
                    "safe",
                )

            with (
                mock.patch.object(
                    compliance_module,
                    "MAX_ARCHIVE_ENTRIES",
                    0,
                ),
                mock.patch.object(
                    compliance_module.zipfile,
                    "ZipFile",
                ) as zip_parser,
            ):
                snapshot = inspect_wheel(wheel_path)

        self.assertIn(
            "archive_entry_limit_exceeded",
            {str(item["code"]) for item in snapshot.blockers},
        )
        zip_parser.assert_not_called()

    def test_wheel_stream_limit_stops_before_zipfile_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            wheel_path = Path(temporary) / "candidate.whl"
            with zipfile.ZipFile(wheel_path, "w") as archive:
                archive.writestr(
                    "starcraft_commander/runtime_data.py",
                    "safe",
                )

            with (
                mock.patch.object(
                    compliance_module,
                    "MAX_ARCHIVE_STREAM_BYTES",
                    8,
                ),
                mock.patch.object(
                    compliance_module.zipfile,
                    "ZipFile",
                ) as zip_parser,
            ):
                snapshot = inspect_wheel(wheel_path)

        self.assertIn(
            "archive_stream_limit_exceeded",
            {str(item["code"]) for item in snapshot.blockers},
        )
        zip_parser.assert_not_called()

    def test_sdist_validates_fhcrc_in_each_gzip_member(self) -> None:
        tar_payload = self._tar_payload(
            (
                "voistarcraft2-0.1.0/"
                "starcraft_commander/runtime_data.py",
                b"safe",
            )
        )
        valid = self._gzip_with_header_metadata(tar_payload, fhcrc=True)
        invalid = self._gzip_with_header_metadata(
            tar_payload,
            fhcrc=True,
            corrupt_fhcrc=True,
        )
        truncated = valid[:11]
        valid_empty = self._gzip_with_header_metadata(b"", fhcrc=True)
        invalid_empty = self._gzip_with_header_metadata(
            b"",
            fhcrc=True,
            corrupt_fhcrc=True,
        )
        truncated_empty = valid_empty[:11]
        variants = {
            "valid_first": (valid, set()),
            "invalid_first": (invalid, {"invalid_gzip_header_checksum"}),
            "truncated_first": (truncated, {"invalid_gzip_header"}),
            "invalid_second": (
                valid + invalid_empty,
                {
                    "unexpected_gzip_member",
                    "invalid_gzip_header_checksum",
                },
            ),
            "truncated_second": (
                valid + truncated_empty,
                {"unexpected_gzip_member", "invalid_gzip_header"},
            ),
        }
        for name, (archive_payload, expected_codes) in variants.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                sdist_path = Path(temporary) / "voistarcraft2-0.1.0.tar.gz"
                sdist_path.write_bytes(archive_payload)

                snapshot = compliance_module.inspect_sdist(sdist_path)

            codes = {str(item["code"]) for item in snapshot.blockers}
            self.assertTrue(expected_codes.issubset(codes))
            if name == "valid_first":
                self.assertEqual(set(), codes)
                self.assertIn(
                    (
                        "voistarcraft2-0.1.0/"
                        "starcraft_commander/runtime_data.py"
                    ),
                    snapshot.files,
                )

    def test_sdist_rejects_and_scans_tar_bytes_after_uname_nul(self) -> None:
        secret = ("sk-" + "liveabcdefghijklmnop").encode()
        tar_payload = bytearray(
            self._tar_payload(
                (
                    "voistarcraft2-0.1.0/"
                    "starcraft_commander/runtime_data.py",
                    b"safe",
                )
            )
        )
        header = bytearray(tar_payload[:512])
        header[265:297] = (b"wheel\0" + secret).ljust(32, b"\0")
        self._replace_tar_checksum(header)
        tar_payload[:512] = header
        with tempfile.TemporaryDirectory() as temporary:
            sdist_path = Path(temporary) / "voistarcraft2-0.1.0.tar.gz"
            sdist_path.write_bytes(
                self._gzip_with_header_metadata(bytes(tar_payload))
            )

            snapshot = compliance_module.inspect_sdist(sdist_path)

        self.assertIn(
            "noncanonical_tar_header_padding",
            {str(item["code"]) for item in snapshot.blockers},
        )
        findings = self._metadata_findings(snapshot)
        self.assertEqual({"api_key"}, {item["rule_id"] for item in findings})
        self.assertEqual(1, len({item["fingerprint"] for item in findings}))

    def test_sdist_rejects_duplicate_pax_keys_without_normalizing(self) -> None:
        secret = ("sk-" + "liveabcdefghijklmnop").encode()
        safe_path = (
            b"voistarcraft2-0.1.0/"
            b"starcraft_commander/runtime_data.py"
        )
        pax_payload = (
            self._pax_record(b"path", safe_path + b"/" + secret)
            + self._pax_record(b"path", safe_path)
        )
        tar_payload = self._tar_with_extension(
            extension_type=tarfile.XHDTYPE,
            extension_payload=pax_payload,
            member_name=safe_path.decode(),
        )
        with tempfile.TemporaryDirectory() as temporary:
            sdist_path = Path(temporary) / "voistarcraft2-0.1.0.tar.gz"
            sdist_path.write_bytes(
                self._gzip_with_header_metadata(tar_payload)
            )

            snapshot = compliance_module.inspect_sdist(sdist_path)

        self.assertIn(
            "duplicate_pax_key",
            {str(item["code"]) for item in snapshot.blockers},
        )
        findings = self._metadata_findings(snapshot)
        self.assertEqual({"api_key"}, {item["rule_id"] for item in findings})
        self.assertEqual(1, len({item["fingerprint"] for item in findings}))

    def test_sdist_rejects_and_scans_gnu_long_name_records(self) -> None:
        secret = ("sk-" + "liveabcdefghijklmnop").encode()
        tar_payload = self._tar_with_extension(
            extension_type=tarfile.GNUTYPE_LONGNAME,
            extension_payload=secret + b"\0",
            member_name=(
                "voistarcraft2-0.1.0/"
                "starcraft_commander/runtime_data.py"
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            sdist_path = Path(temporary) / "voistarcraft2-0.1.0.tar.gz"
            sdist_path.write_bytes(
                self._gzip_with_header_metadata(tar_payload)
            )

            snapshot = compliance_module.inspect_sdist(sdist_path)

        self.assertIn(
            "unexpected_archive_metadata",
            {str(item["code"]) for item in snapshot.blockers},
        )
        findings = self._metadata_findings(snapshot)
        self.assertEqual({"api_key"}, {item["rule_id"] for item in findings})
        self.assertEqual(1, len({item["fingerprint"] for item in findings}))

    def test_sdist_rejects_and_scans_nonzero_member_padding(self) -> None:
        secret = ("sk-" + "liveabcdefghijklmnop").encode()
        tar_payload = bytearray(
            self._tar_payload(
                (
                    "voistarcraft2-0.1.0/"
                    "starcraft_commander/runtime_data.py",
                    b"safe",
                )
            )
        )
        tar_payload[516 : 516 + len(secret)] = secret
        with tempfile.TemporaryDirectory() as temporary:
            sdist_path = Path(temporary) / "voistarcraft2-0.1.0.tar.gz"
            sdist_path.write_bytes(
                self._gzip_with_header_metadata(bytes(tar_payload))
            )

            snapshot = compliance_module.inspect_sdist(sdist_path)

        self.assertIn(
            "noncanonical_tar_member_padding",
            {str(item["code"]) for item in snapshot.blockers},
        )
        findings = self._metadata_findings(snapshot)
        self.assertEqual({"api_key"}, {item["rule_id"] for item in findings})
        self.assertEqual(1, len({item["fingerprint"] for item in findings}))

    def test_sdist_rejects_and_scans_data_after_tar_end_markers(self) -> None:
        secret = ("sk-" + "liveabcdefghijklmnop").encode()
        tar_payload = bytearray(
            self._tar_payload(
                (
                    "voistarcraft2-0.1.0/"
                    "starcraft_commander/runtime_data.py",
                    b"safe",
                )
            )
        )
        end_offset = next(
            offset
            for offset in range(0, len(tar_payload) - 512, 512)
            if tar_payload[offset : offset + 1024] == b"\0" * 1024
        )
        trailing_offset = end_offset + 1024
        tar_payload[
            trailing_offset : trailing_offset + len(secret)
        ] = secret
        with tempfile.TemporaryDirectory() as temporary:
            sdist_path = Path(temporary) / "voistarcraft2-0.1.0.tar.gz"
            sdist_path.write_bytes(
                self._gzip_with_header_metadata(bytes(tar_payload))
            )

            snapshot = compliance_module.inspect_sdist(sdist_path)

        self.assertIn(
            "invalid_tar_trailing_data",
            {str(item["code"]) for item in snapshot.blockers},
        )
        findings = self._metadata_findings(snapshot)
        self.assertEqual({"api_key"}, {item["rule_id"] for item in findings})
        self.assertEqual(1, len({item["fingerprint"] for item in findings}))

    def test_sdist_limits_stop_before_tarfile_parsing(self) -> None:
        tar_payload = self._tar_payload(
            (
                "voistarcraft2-0.1.0/"
                "starcraft_commander/runtime_data.py",
                b"safe",
            ),
            (
                "voistarcraft2-0.1.0/"
                "starcraft_commander/runtime_data_2.py",
                b"safe",
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            sdist_path = Path(temporary) / "voistarcraft2-0.1.0.tar.gz"
            sdist_path.write_bytes(
                self._gzip_with_header_metadata(tar_payload)
            )
            with (
                mock.patch.object(
                    compliance_module,
                    "MAX_ARCHIVE_ENTRIES",
                    1,
                ),
                mock.patch.object(
                    compliance_module.tarfile,
                    "open",
                ) as tar_parser,
            ):
                snapshot = compliance_module.inspect_sdist(sdist_path)

        self.assertIn(
            "archive_entry_limit_exceeded",
            {str(item["code"]) for item in snapshot.blockers},
        )
        tar_parser.assert_not_called()

    def test_sdist_stream_limit_stops_before_tarfile_parsing(self) -> None:
        tar_payload = self._tar_payload(
            (
                "voistarcraft2-0.1.0/"
                "starcraft_commander/runtime_data.py",
                b"safe",
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            sdist_path = Path(temporary) / "voistarcraft2-0.1.0.tar.gz"
            sdist_path.write_bytes(
                self._gzip_with_header_metadata(tar_payload)
            )
            with (
                mock.patch.object(
                    compliance_module,
                    "MAX_ARCHIVE_STREAM_BYTES",
                    8,
                ),
                mock.patch.object(
                    compliance_module.tarfile,
                    "open",
                ) as tar_parser,
            ):
                snapshot = compliance_module.inspect_sdist(sdist_path)

        self.assertIn(
            "archive_stream_limit_exceeded",
            {str(item["code"]) for item in snapshot.blockers},
        )
        tar_parser.assert_not_called()

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

    def test_detects_semantic_lowercase_myproxy_configuration(self) -> None:
        endpoint = "https://proxy." + "corp.example/v1"
        model = "private-" + "deployment"
        payloads = {
            "private.json": json.dumps(
                {
                    "provider": "my" + "proxy",
                    "base_url": endpoint,
                    "model": model,
                }
            ).encode(),
            "private.toml": (
                'provider = "my' + 'proxy"\n'
                f'base_url = "{endpoint}"\n'
                f'model = "{model}"\n'
            ).encode(),
            "private.yaml": (
                "provider: my" + "proxy\n"
                f"base-url: {endpoint}\n"
                f"model: {model}\n"
            ).encode(),
            "nested.json": json.dumps(
                {
                    "provider": "my" + "proxy",
                    "settings": {
                        "base_url": endpoint,
                        "model": model,
                    },
                }
            ).encode(),
            "nested.toml": (
                'provider = "my' + 'proxy"\n'
                "[settings]\n"
                f'base_url = "{endpoint}"\n'
                f'model = "{model}"\n'
            ).encode(),
            "nested.yaml": (
                "provider: my" + "proxy\n"
                "settings:\n"
                f"  base-url: {endpoint}\n"
                f"  model: {model}\n"
            ).encode(),
        }

        for path, payload in payloads.items():
            with self.subTest(path=path):
                findings = scan_payload(
                    path,
                    payload,
                    allow_safe_fixtures=False,
                )

                self.assertEqual(
                    {"private_endpoint", "private_model_override"},
                    {str(item["rule_id"]) for item in findings},
                )

    def test_detects_ini_indirect_shell_and_generic_docker_myproxy(
        self,
    ) -> None:
        endpoint = "https://10.0." + "0.8:7443/v1"
        model = "secret-" + "model-140"
        payloads = {
            "settings.ini": (
                "provider=myproxy\n"
                f"model={model}\n"
                f"base_url={endpoint}\n"
            ),
            "launch.sh": (
                "provider=myproxy\n"
                f"model={model}\n"
                f"base_url={endpoint}\n"
                'commander --provider "$provider" --model "$model" '
                '--base-url "$base_url"\n'
            ),
            "Dockerfile": (
                "ENV LLM_PROVIDER=myproxy "
                f"LLM_MODEL={model} LLM_BASE_URL={endpoint}\n"
            ),
        }

        for path, payload in payloads.items():
            with self.subTest(path=path):
                findings = scan_payload(
                    path,
                    payload.encode(),
                    allow_safe_fixtures=False,
                )

                self.assertEqual(
                    {"private_endpoint", "private_model_override"},
                    {str(item["rule_id"]) for item in findings},
                )

    def test_detects_shell_multi_assignment_and_declaration_indirection(
        self,
    ) -> None:
        endpoint = "https://10.0." + "0.8:7443/v1"
        model = "secret-" + "model-140"
        command = (
            'commander --provider "$provider" --model "$model" '
            '--base-url "$base_url"\n'
        )
        payloads = {
            "multi-export.sh": (
                "export provider=myproxy "
                f"model={model} base_url={endpoint}\n"
                + command
            ),
            "semicolon.zsh": (
                f"provider=myproxy; model={model}; base_url={endpoint}; "
                + command
            ),
            "declare.bash": (
                "declare -x provider=myproxy "
                f"model={model} base_url={endpoint}\n"
                + command
            ),
            "typeset.zsh": (
                "typeset -gx provider=myproxy "
                f"model={model} base_url={endpoint}\n"
                + command
            ),
        }

        for path, payload in payloads.items():
            with self.subTest(path=path):
                findings = scan_payload(
                    path,
                    payload.encode(),
                    allow_safe_fixtures=False,
                )

                self.assertEqual(
                    {
                        "private_endpoint",
                        "private_model_override",
                    },
                    {str(item["rule_id"]) for item in findings},
                )

    def test_public_shell_multi_assignment_and_declarations_stay_clean(
        self,
    ) -> None:
        command = (
            'commander --provider "$provider" --model "$model" '
            '--base-url "$base_url"\n'
        )
        public_values = (
            "provider=openai model=gpt-public "
            "base_url=https://api.openai.com/v1"
        )
        payloads = {
            "multi-export.sh": f"export {public_values}\n{command}",
            "semicolon.zsh": (
                "provider=openai; model=gpt-public; "
                "base_url=https://api.openai.com/v1; "
                + command
            ),
            "declare.bash": f"declare -x {public_values}\n{command}",
            "typeset.zsh": f"typeset -gx {public_values}\n{command}",
        }

        for path, payload in payloads.items():
            with self.subTest(path=path):
                self.assertEqual(
                    [],
                    scan_payload(
                        path,
                        payload.encode(),
                        allow_safe_fixtures=False,
                    ),
                )

    def test_docker_semantic_snapshots_preserve_private_overrides(
        self,
    ) -> None:
        endpoint = "https://10.0." + "0.8:7443/v1"
        model = "secret-" + "model-140"
        payloads = {
            "same-stage": (
                "FROM scratch\n"
                f"ENV provider=myproxy model={model} base_url={endpoint}\n"
                "ENV provider=openai model=gpt-public "
                "base_url=https://api.openai.com/v1\n"
            ),
            "multi-stage": (
                "FROM scratch AS private-stage\n"
                f"ARG provider=myproxy model={model} base_url={endpoint}\n"
                "FROM scratch AS public-stage\n"
                "ARG provider=openai model=gpt-public "
                "base_url=https://api.openai.com/v1\n"
            ),
        }

        for name, payload in payloads.items():
            with self.subTest(name=name):
                findings = scan_payload(
                    "Dockerfile",
                    payload.encode(),
                    allow_safe_fixtures=False,
                )

                self.assertEqual(
                    {
                        "private_endpoint",
                        "private_model_override",
                    },
                    {str(item["rule_id"]) for item in findings},
                )

    def test_configuration_expansion_budgets_fail_closed(self) -> None:
        shell_lines = ["v0=x"]
        docker_lines = ["ENV v0=x"]
        for index in range(1, 21):
            shell_lines.append(
                f"v{index}=$v{index - 1}$v{index - 1}"
            )
            docker_lines.append(
                f"ENV v{index}=$v{index - 1}$v{index - 1}"
            )
        amplification_cases = (
            (
                "amplify.sh",
                "\n".join(shell_lines) + "\n",
                "shell_configuration_limit_exceeded",
            ),
            (
                "Dockerfile",
                "\n".join(docker_lines) + "\n",
                "docker_configuration_limit_exceeded",
            ),
        )
        for path, payload, expected_rule in amplification_cases:
            with self.subTest(path=path):
                self.assertLess(len(payload), 1024)
                findings = scan_payload(
                    path,
                    payload.encode(),
                    allow_safe_fixtures=False,
                )
                self.assertIn(
                    expected_rule,
                    {str(item["rule_id"]) for item in findings},
                )

        substitution_payload = (
            "seed=x\nvalue="
            + "$seed"
            * (compliance_module.MAX_CONFIGURATION_EXPANSION_SUBSTITUTIONS + 1)
            + "\n"
        )
        substitution_findings = scan_payload(
            "substitutions.sh",
            substitution_payload.encode(),
            allow_safe_fixtures=False,
        )
        self.assertIn(
            "shell_configuration_limit_exceeded",
            {
                str(item["rule_id"])
                for item in substitution_findings
            },
        )

        with mock.patch.object(
            compliance_module,
            "MAX_CONFIGURATION_EXPANSION_WORK",
            32,
        ):
            work_findings = scan_payload(
                "work.sh",
                b'value=safe\nprintf "$value"\nprintf "$value"\n',
                allow_safe_fixtures=False,
            )
        self.assertIn(
            "shell_configuration_limit_exceeded",
            {str(item["rule_id"]) for item in work_findings},
        )

    def test_configuration_binding_and_snapshot_caps_fail_closed(
        self,
    ) -> None:
        shell_bindings = "\n".join(
            f"value_{index}=safe"
            for index in range(
                compliance_module.MAX_CONFIGURATION_BINDINGS + 1
            )
        )
        docker_bindings = "\n".join(
            f"ENV value_{index}=safe"
            for index in range(
                compliance_module.MAX_CONFIGURATION_BINDINGS + 1
            )
        )
        docker_snapshots = "\n".join(
            f"ENV provider=openai-{index}"
            for index in range(
                compliance_module.MAX_CONFIGURATION_SNAPSHOTS + 1
            )
        )
        cases = (
            (
                "bindings.sh",
                shell_bindings,
                "shell_configuration_limit_exceeded",
            ),
            (
                "Dockerfile",
                docker_bindings,
                "docker_configuration_limit_exceeded",
            ),
            (
                "service.Dockerfile",
                docker_snapshots,
                "docker_configuration_limit_exceeded",
            ),
        )

        for path, payload, expected_rule in cases:
            with self.subTest(path=path, expected_rule=expected_rule):
                findings = scan_payload(
                    path,
                    payload.encode(),
                    allow_safe_fixtures=False,
                )
                self.assertIn(
                    expected_rule,
                    {str(item["rule_id"]) for item in findings},
                )

    def test_shell_expansion_preserves_unknown_variables(self) -> None:
        endpoint = "https://10.0." + "0.8:7443/v1"
        model = "secret-" + "model-140"
        payload = (
            "selected=myproxy\n"
            f"deployment={model}\n"
            f"gateway={endpoint}\n"
            'commander --log "$HOME/commander.log" '
            '--provider "$selected" --model "$deployment" '
            '--base-url "$gateway"\n'
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

    def test_nested_provider_overrides_inherited_myproxy_context(self) -> None:
        payload = (
            "provider: myproxy\n"
            "fallback:\n"
            "  provider: openai\n"
            "  host: api.openai.com\n"
            "  port: 443\n"
            "  model: gpt-public\n"
        ).encode()

        self.assertEqual(
            [],
            scan_payload(
                "providers.yaml",
                payload,
                allow_safe_fixtures=False,
            ),
        )

    def test_detects_utf32_and_blocks_opaque_text_payloads(self) -> None:
        host_key = "VOI_MYPROXY_" + "HOST"
        model_key = "VOI_MYPROXY_" + "MODEL"
        source = (
            f'{host_key} = "10.20.30.40"\n'
            f'{model_key} = "private-model"\n'
        )
        cases = (
            ("module.py", source.encode("utf-32")),
            ("script.sh", source.encode("utf-32-be")),
        )
        for path, payload in cases:
            with self.subTest(path=path):
                findings = scan_payload(
                    path,
                    payload,
                    allow_safe_fixtures=False,
                )

                self.assertEqual(
                    {"private_endpoint", "private_model_override"},
                    {str(item["rule_id"]) for item in findings},
                )
        opaque = scan_payload(
            "opaque.py",
            b"safe\x00payload\x00with\x00embedded\x00nuls",
            allow_safe_fixtures=False,
        )
        self.assertIn(
            "opaque_text_payload",
            {str(item["rule_id"]) for item in opaque},
        )

    def test_detects_bomless_utf16_in_all_scanned_source_types(self) -> None:
        host_key = "VOI_MYPROXY_" + "HOST"
        model_key = "VOI_MYPROXY_" + "MODEL"
        source = (
            f'{host_key} = "10.20.30.40"\n'
            f'{model_key} = "private-model"\n'
        )
        cases = (
            ("module.py", "utf-16-le"),
            ("script.sh", "utf-16-be"),
            ("header.hpp", "utf-16-le"),
            ("change.patch", "utf-16-be"),
        )
        for path, encoding in cases:
            with self.subTest(path=path, encoding=encoding):
                findings = scan_payload(
                    path,
                    source.encode(encoding),
                    allow_safe_fixtures=False,
                )

                self.assertEqual(
                    {"private_endpoint", "private_model_override"},
                    {str(item["rule_id"]) for item in findings},
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

    def test_detects_indirect_and_aliased_python_environment_writes(
        self,
    ) -> None:
        host_key = "VOI_MYPROXY_" + "HOST"
        model_key = "VOI_MYPROXY_" + "MODEL"
        endpoint = "10.20." + "30.40"
        model = "private-" + "model"
        payload = (
            "import os as operating_system\n"
            f'key = "{host_key}"\n'
            f'value = "{endpoint}"\n'
            "environment = operating_system.environ\n"
            "environment[key] = value\n"
            f'model_key = "{model_key}"\n'
            f'model_value = "{model}"\n'
            "updates = {model_key: model_value}\n"
            "environment.update(updates)\n"
            "from os import environ as imported_environment\n"
            f'imported_environment.update({{{json.dumps(host_key)}: '
            f'{json.dumps(endpoint)}}})\n'
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

    def test_python_environment_alias_shadowing_does_not_false_positive(
        self,
    ) -> None:
        payload = (
            "from os import environ\n"
            'key = "VOI_MYPROXY_" + "HOST"\n'
            'value = "10.20." + "30.40"\n'
            "if True:\n"
            "    environ = {}\n"
            "    environ[key] = value\n"
        ).encode()

        self.assertEqual(
            [],
            scan_payload(
                "benign.py",
                payload,
                allow_safe_fixtures=False,
            ),
        )

    def test_detects_environment_aliases_across_control_flow(self) -> None:
        host_payload = (
            'key = "VOI_MYPROXY_" + "HOST"\n'
            'value = "10.20." + "30.40"\n'
            "if enabled:\n"
            "    import os as operating_system\n"
            "operating_system.environ[key] = value\n"
        )
        model_payload = (
            'key = "VOI_MYPROXY_" + "MODEL"\n'
            'value = "private-" + "model"\n'
            "try:\n"
            "    from os import environ as environment\n"
            "except ImportError:\n"
            "    environment = {}\n"
            "environment[key] = value\n"
        )

        for payload, expected in (
            (host_payload, "private_endpoint"),
            (model_payload, "private_model_override"),
        ):
            with self.subTest(expected=expected):
                findings = scan_payload(
                    "launcher.py",
                    payload.encode(),
                    allow_safe_fixtures=False,
                )

                self.assertIn(
                    expected,
                    {str(item["rule_id"]) for item in findings},
                )

    def test_python_sensitive_analysis_limits_fail_closed(self) -> None:
        deep_expression = "key = " + " + ".join(
            '"a"' for _ in range(1000)
        )
        excessive_bindings = "\n".join(
            f'name_{index} = "value_{index}"'
            for index in range(600)
        )
        state_explosion = "\n".join(
            line
            for index in range(11)
            for line in (
                f"if condition_{index}:",
                f'    value_{index} = "left_{index}"',
                "else:",
                f'    value_{index} = "right_{index}"',
            )
        )
        for payload in (
            deep_expression,
            excessive_bindings,
            state_explosion,
        ):
            with self.subTest(payload_size=len(payload)):
                findings = scan_payload(
                    "bounded.py",
                    payload.encode(),
                    allow_safe_fixtures=False,
                )

                self.assertIn(
                    "python_sensitive_analysis_limit_exceeded",
                    {str(item["rule_id"]) for item in findings},
                )

    def test_secret_finding_count_is_bounded(self) -> None:
        payload = "\n".join(
            "SERVICE_API_KEY=" + ("a" * 32)
            for _ in range(compliance_module.MAX_SCAN_FINDINGS + 100)
        ).encode()

        findings = scan_payload(
            "bounded.env.txt",
            payload,
            allow_safe_fixtures=False,
        )

        self.assertEqual(
            compliance_module.MAX_SCAN_FINDINGS,
            len(findings),
        )
        self.assertEqual(
            "scan_finding_limit_exceeded",
            findings[-1]["rule_id"],
        )

    def test_reasonable_python_branching_does_not_hit_analysis_limit(
        self,
    ) -> None:
        payload = "\n".join(
            line
            for index in range(9)
            for line in (
                f"if condition_{index}:",
                f'    value_{index} = "left_{index}"',
                "else:",
                f'    value_{index} = "right_{index}"',
            )
        )

        self.assertEqual(
            [],
            scan_payload(
                "branches.py",
                payload.encode(),
                allow_safe_fixtures=False,
            ),
        )

    def test_large_product_python_files_stay_within_analysis_budget(
        self,
    ) -> None:
        source_root = Path(__file__).resolve().parents[1]
        for relative_path in (
            "starcraft_commander/llm_interpreter.py",
            "starcraft_commander/micromachine_pre_live_provenance.py",
            "starcraft_commander/web_gui.py",
        ):
            with self.subTest(path=relative_path):
                findings = scan_payload(
                    relative_path,
                    (source_root / relative_path).read_bytes(),
                    allow_safe_fixtures=False,
                )

                self.assertNotIn(
                    "python_sensitive_analysis_limit_exceeded",
                    {str(item["rule_id"]) for item in findings},
                )

    def test_detects_python_environment_key_composition_and_escapes(
        self,
    ) -> None:
        payloads = (
            (
                'os.environ["VOI_MYPROXY_" + "HOST"] = '
                '"10.20." + "30.40"\n'
            ),
            (
                'key = "VOI_MYPROXY_" + "MODEL"\n'
                'os.environ[key] = "private-" + "model"\n'
            ),
            (
                'os.environ["VOI_MYPROXY_\\x48OST"] = '
                '"10.20.30.40"\n'
            ),
            (
                'from os import environ\n'
                'environ |= {"VOI_MYPROXY_" + "PORT": "8443"}\n'
            ),
            (
                'key = "VOI_MYPROXY_" + "HOST"\n'
                'value = "10.20." + "30.40"\n'
                "os.environ |= {key: value}\n"
            ),
            (
                'key = "VOI_MYPROXY_" + "MODEL"\n'
                'value = "private-" + "model"\n'
                "os.environ.__setitem__(key, value)\n"
            ),
            (
                'key = "VOI_MYPROXY_" + "HOST"\n'
                'value = "10.20." + "30.40"\n'
                'getattr(os, "en" + "viron")[key] = value\n'
            ),
        )
        expected = (
            "private_endpoint",
            "private_model_override",
            "private_endpoint",
            "private_endpoint",
            "private_endpoint",
            "private_model_override",
            "private_endpoint",
        )
        for payload, rule_id in zip(payloads, expected, strict=True):
            with self.subTest(rule_id=rule_id):
                findings = scan_payload(
                    "launcher.py",
                    payload.encode(),
                    allow_safe_fixtures=False,
                )

                self.assertIn(
                    rule_id,
                    {str(item["rule_id"]) for item in findings},
                )

    def test_configured_locally_exemption_requires_complete_value(self) -> None:
        model_key = "DEFAULT_MYPROXY_" + "MODEL"
        exact_values = (
            ("defaults.py", f'{model_key} = "configured-locally"\n'),
            (
                "defaults.py",
                f"{model_key} = configured-locally # local override\n",
            ),
            (
                "defaults.yaml",
                "{"
                f"{model_key}: configured-locally, "
                "safe: true}\n",
            ),
        )
        for path, exact in exact_values:
            with self.subTest(path=path, exact=exact):
                self.assertEqual(
                    [],
                    scan_payload(
                        path,
                        exact.encode(),
                        allow_safe_fixtures=False,
                    ),
                )

        suffixed_values = (
            ("defaults.py", f'{model_key} = "configured-locally" + "-private"\n'),
            ("defaults.py", f'{model_key} = "configured-locally#secret-model"\n'),
            ("defaults.py", f'{model_key} = "configured-locally,secret-model"\n'),
            ("defaults.py", f"{model_key} = configured-locally#secret-model\n"),
            ("defaults.yaml", f"{model_key}: configured-locally,secret-model\n"),
        )
        for path, suffixed in suffixed_values:
            with self.subTest(path=path, suffixed=suffixed):
                self.assertIn(
                    "private_model_override",
                    {
                        str(item["rule_id"])
                        for item in scan_payload(
                            path,
                            suffixed.encode(),
                            allow_safe_fixtures=False,
                        )
                    },
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
            "immediate-lambda-call": (
                f"(lambda provider: {command})"
                '("my" + "proxy")\n'
            ),
            "conditional-callee": (
                "def launch(provider):\n"
                f"    {command}\n"
                "def noop(provider):\n"
                "    pass\n"
                '(launch if True else noop)("my" + "proxy")\n'
            ),
            "walrus-callee": (
                "def launch(provider):\n"
                f"    {command}\n"
                '(runner := launch)("my" + "proxy")\n'
            ),
            "variable-expanded-keyword": (
                "def launch(provider):\n"
                f"    {command}\n"
                'kwargs = {"provider": "my" + "proxy"}\n'
                "launch(**kwargs)\n"
            ),
            "attribute-function-alias": (
                "class Holder:\n"
                "    pass\n"
                "holder = Holder()\n"
                "def launch(provider):\n"
                f"    {command}\n"
                "holder.execute = launch\n"
                'holder.execute("my" + "proxy")\n'
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

    def test_python_cli_reconstruction_preserves_callable_when_states_bound(
        self,
    ) -> None:
        payload = (
            "def launch(provider):\n"
            "    run([\n"
            '        "commander", "--provider", provider,\n'
            '        "--openai-base-url", '
            '"https://proxy." + "corp.example/v1",\n'
            '        "--model", "private-" + "deployment",\n'
            "    ])\n"
            "def noop(provider):\n"
            "    pass\n"
            "if True:\n"
            "    runner = launch\n"
            "else:\n"
            "    runner = noop\n"
            "if True:\n"
            '    a = "0"\n'
            "else:\n"
            '    a = "1"\n'
            "if True:\n"
            '    b = "0"\n'
            "else:\n"
            '    b = "1"\n'
            "if True:\n"
            '    c = "0"\n'
            "else:\n"
            '    c = "1"\n'
            'runner("my" + "proxy")\n'
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

        self.assertEqual("", failure)
        self.assertIn("myproxy", reconstructed)
        self.assertEqual(
            {
                "private_endpoint",
                "private_model_override",
            },
            {str(item["rule_id"]) for item in findings},
        )

    def test_python_cli_reconstruction_preserves_kwargs_when_states_bound(
        self,
    ) -> None:
        payload = (
            "def launch(provider):\n"
            "    run([\n"
            '        "commander", "--provider", provider,\n'
            '        "--openai-base-url", '
            '"https://proxy." + "corp.example/v1",\n'
            '        "--model", "private-" + "deployment",\n'
            "    ])\n"
            "if True:\n"
            '    kwargs = {"provider": "my" + "proxy"}\n'
            "else:\n"
            "    kwargs = resolve_kwargs()\n"
            "if True:\n"
            '    a = "0"\n'
            "else:\n"
            '    a = "1"\n'
            "if True:\n"
            '    b = "0"\n'
            "else:\n"
            '    b = "1"\n'
            "if True:\n"
            '    c = "0"\n'
            "else:\n"
            '    c = "1"\n'
            "launch(**kwargs)\n"
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

        self.assertEqual("", failure)
        self.assertIn("myproxy", reconstructed)
        self.assertEqual(
            {
                "private_endpoint",
                "private_model_override",
            },
            {str(item["rule_id"]) for item in findings},
        )

    def test_python_cli_reconstruction_preserves_stored_provider_values(
        self,
    ) -> None:
        endpoint = '"https://x." + "private.example/v1"'
        model = '"private-" + "m"'
        command = (
            'run(["c", "--provider", provider, "--base-url", '
            f"{endpoint}, \"--model\", {model}])"
        )
        payloads = {
            "mapping-subscript": (
                'config = {"provider": "my" + "proxy"}\n'
                f"provider = config[\"provider\"]\n{command}\n"
            ),
            "starred-conditional": (
                "def launch(provider):\n"
                f"    {command}\n"
                'launch(*(["my" + "proxy"] if True else ["openai"]))\n'
            ),
            "kwargs-subscript": (
                "def launch(**kw):\n"
                '    provider = kw["provider"]\n'
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
                    {"private_endpoint", "private_model_override"},
                    {str(item["rule_id"]) for item in findings},
                )

    def test_python_cli_reconstruction_preserves_aliases_and_forwarding(
        self,
    ) -> None:
        command = (
            'run(["c", "--provider", provider, "--base-url", '
            '"https://x." + "private.example/v1", "--model", '
            '"private-" + "m"])'
        )
        provider_branches = "".join(
            (
                ("if" if index == 0 else "elif")
                + f" choice == {index}:\n"
                + (
                    '    provider = "my" + "proxy"\n'
                    if index == 4
                    else (
                        '    provider = "public-provider-'
                        + str(index)
                        + "-"
                        + ("x" * 80)
                        + '"\n'
                    )
                )
            )
            for index in range(9)
        )
        provider_branches += (
            "else:\n"
            '    provider = "public-provider-fallback-'
            + ("x" * 80)
            + '"\n'
        )
        payloads = {
            "attribute-object-alias": (
                "class Holder:\n"
                "    pass\n"
                "h = Holder()\n"
                "def launch(provider):\n"
                f"    {command}\n"
                "h.go = launch\n"
                "alias = h\n"
                'alias.go("my" + "proxy")\n'
            ),
            "forwarding-wrapper": (
                "def launch(provider):\n"
                f"    {command}\n"
                "def wrapper(provider):\n"
                "    launch(provider)\n"
                'wrapper("my" + "proxy")\n'
            ),
            "forwarding-local-alias": (
                "def launch(provider):\n"
                f"    {command}\n"
                "def wrapper(provider):\n"
                "    forwarded = provider\n"
                "    launch(forwarded)\n"
                'wrapper("my" + "proxy")\n'
            ),
            "bounded-provider-state": (
                "def launch(provider):\n"
                f"    {command}\n"
                + provider_branches
                + "launch(provider)\n"
            ),
            "starred-destructuring": (
                "def launch(provider):\n"
                f"    {command}\n"
                'for *_, provider in (("ignored", "my" + "proxy"),):\n'
                "    launch(provider)\n"
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
                    {"private_endpoint", "private_model_override"},
                    {str(item["rule_id"]) for item in findings},
                )

    def test_python_cli_reconstruction_follows_zero_argument_wrappers(
        self,
    ) -> None:
        command = (
            'run(["c", "--provider", provider, "--base-url", '
            '"https://x." + "private.example/v1", "--model", '
            '"private-" + "m"])'
        )
        payloads = {
            "local": (
                "def wrapper():\n"
                '    provider = "my" + "proxy"\n'
                f"    {command}\n"
                "wrapper()\n"
            ),
            "global": (
                'provider = "my" + "proxy"\n'
                "def wrapper():\n"
                f"    {command}\n"
                "wrapper()\n"
            ),
            "closure": (
                "def outer():\n"
                '    provider = "my" + "proxy"\n'
                "    def wrapper():\n"
                f"        {command}\n"
                "    wrapper()\n"
                "outer()\n"
            ),
            "forwarded-local": (
                "def launch(provider):\n"
                f"    {command}\n"
                "def wrapper():\n"
                '    provider = "my" + "proxy"\n'
                "    launch(provider)\n"
                "wrapper()\n"
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
                    {"private_endpoint", "private_model_override"},
                    {str(item["rule_id"]) for item in findings},
                )

    def test_python_cli_reconstruction_preserves_returned_closures(self) -> None:
        payload = (
            "def submit(provider):\n"
            "    run([\n"
            '        "c", "--provider", provider,\n'
            '        "--base-url", '
            '"https://x." + "private.example/v1",\n'
            '        "--model", "private-" + "m",\n'
            "    ])\n"
            "def build_launcher(provider):\n"
            "    def launch():\n"
            "        submit(provider)\n"
            "    return launch\n"
            'runner = build_launcher("my" + "proxy")\n'
            "runner()\n"
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

        self.assertEqual("", failure)
        self.assertIn("myproxy", reconstructed)
        self.assertEqual(
            {"private_endpoint", "private_model_override"},
            {str(item["rule_id"]) for item in findings},
        )

    def test_python_cli_reconstruction_follows_local_callable_aliases(
        self,
    ) -> None:
        payload = (
            "def launch(provider):\n"
            "    run([\n"
            '        "c", "--provider", provider,\n'
            '        "--base-url", "https://x." + "private.example/v1",\n'
            '        "--model", "private-" + "m",\n'
            "    ])\n"
            "def wrapper():\n"
            "    local_alias = launch\n"
            '    local_alias("my" + "proxy")\n'
            "wrapper()\n"
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

        self.assertEqual("", failure)
        self.assertIn("myproxy", reconstructed)
        self.assertEqual(
            {"private_endpoint", "private_model_override"},
            {str(item["rule_id"]) for item in findings},
        )

    def test_python_cli_defaults_are_evaluated_at_definition_time(self) -> None:
        payloads = {
            "sensitive-default": (
                'provider = "my" + "proxy"\n'
                "def launch(selected=provider):\n"
                "    run([\n"
                '        "c", "--provider", selected,\n'
                '        "--base-url", '
                '"https://x." + "private.example/v1",\n'
                '        "--model", "private-" + "m",\n'
                "    ])\n"
                'provider = "openai"\n'
                "launch()\n"
            ),
            "safe-default": (
                'provider = "openai"\n'
                "def launch(selected=provider):\n"
                "    run([\n"
                '        "c", "--provider", selected,\n'
                '        "--base-url", '
                '"https://x." + "private.example/v1",\n'
                '        "--model", "private-" + "m",\n'
                "    ])\n"
                'provider = "my" + "proxy"\n'
                "launch()\n"
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
                if name == "sensitive-default":
                    self.assertIn("myproxy", reconstructed)
                    self.assertEqual(
                        {"private_endpoint", "private_model_override"},
                        {str(item["rule_id"]) for item in findings},
                    )
                else:
                    self.assertNotIn("myproxy", reconstructed)
                    self.assertEqual([], findings)

    def test_python_cli_lambda_defaults_use_definition_time_values(self) -> None:
        payload = (
            'provider = "my" + "proxy"\n'
            "launch = lambda selected=provider: run([\n"
            '    "c", "--provider", selected,\n'
            '    "--base-url", "https://x." + "private.example/v1",\n'
            '    "--model", "private-" + "m",\n'
            "])\n"
            'provider = "openai"\n'
            "launch()\n"
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

        self.assertEqual("", failure)
        self.assertIn("myproxy", reconstructed)
        self.assertEqual(
            {"private_endpoint", "private_model_override"},
            {str(item["rule_id"]) for item in findings},
        )

    def test_python_cli_function_globals_use_call_time_values(self) -> None:
        payload = (
            'provider = "openai"\n'
            "def launch():\n"
            "    run([\n"
            '        "c", "--provider", provider,\n'
            '        "--base-url", "https://x." + "private.example/v1",\n'
            '        "--model", "private-" + "m",\n'
            "    ])\n"
            'provider = "my" + "proxy"\n'
            "launch()\n"
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

        self.assertEqual("", failure)
        self.assertIn("myproxy", reconstructed)
        self.assertEqual(
            {"private_endpoint", "private_model_override"},
            {str(item["rule_id"]) for item in findings},
        )

    def test_python_cli_reconstruction_evaluates_f_string_values(self) -> None:
        payload = (
            'option = "provider"\n'
            'provider_suffix = "proxy"\n'
            'host = "private.example"\n'
            'model_suffix = "m"\n'
            "run([\n"
            '    "c", f"--{option}", f"my{provider_suffix}",\n'
            '    "--base-url", f"https://x.{host}/v1",\n'
            '    "--model", f"private-{model_suffix}",\n'
            "])\n"
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

        self.assertEqual("", failure)
        self.assertIn("--provider myproxy", reconstructed)
        self.assertEqual(
            {"private_endpoint", "private_model_override"},
            {str(item["rule_id"]) for item in findings},
        )

    def test_python_cli_unknown_f_strings_fail_closed_only_in_cli_flow(
        self,
    ) -> None:
        payloads = {
            "unrelated": (
                "message = f'operation {operation_id}: {status!r}'\n"
                "render(message)\n"
            ),
            "sensitive-cli": (
                'provider = "my" + "proxy"\n'
                "endpoint = f'https://{runtime_host}/v1'\n"
                'run(["c", "--provider", provider, '
                '"--base-url", endpoint])\n'
            ),
        }

        for name, payload in payloads.items():
            with self.subTest(name=name):
                _reconstructed, failure = (
                    compliance_module._python_cli_argument_text(
                        "launcher.py",
                        payload,
                    )
                )

                if name == "unrelated":
                    self.assertEqual("", failure)
                else:
                    self.assertEqual(
                        "python_cli_analysis_limit_exceeded:f_string",
                        failure,
                    )

    def test_python_cli_reconstruction_binds_instance_methods(self) -> None:
        payload = (
            "class Client:\n"
            "    def execute(self, provider):\n"
            "        run([\n"
            '            "c", "--provider", provider,\n'
            '            "--base-url", '
            '"https://x." + "private.example/v1",\n'
            '            "--model", "private-" + "m",\n'
            "        ])\n"
            "client = Client()\n"
            'client.execute("my" + "proxy")\n'
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

        self.assertEqual("", failure)
        self.assertIn("myproxy", reconstructed)
        self.assertEqual(
            {"private_endpoint", "private_model_override"},
            {str(item["rule_id"]) for item in findings},
        )

    def test_python_cli_reconstruction_resolves_python_method_dispatch(
        self,
    ) -> None:
        command = (
            "        run([\n"
            '            "c", "--provider", provider,\n'
            '            "--base-url", '
            '"https://x." + "private.example/v1",\n'
            '            "--model", "private-" + "m",\n'
            "        ])\n"
        )
        payloads = {
            "staticmethod-class": (
                "class Client:\n"
                "    @staticmethod\n"
                "    def execute(provider):\n"
                + command
                + 'Client.execute("my" + "proxy")\n'
            ),
            "staticmethod-instance": (
                "class Client:\n"
                "    @staticmethod\n"
                "    def execute(provider):\n"
                + command
                + "client = Client()\n"
                + 'client.execute("my" + "proxy")\n'
            ),
            "classmethod": (
                "class Client:\n"
                "    @classmethod\n"
                "    def execute(cls, provider):\n"
                + command
                + 'Client.execute("my" + "proxy")\n'
            ),
            "inherited-instance-method": (
                "class Base:\n"
                "    def execute(self, provider):\n"
                + command
                + "class Client(Base):\n"
                + "    pass\n"
                + "client = Client()\n"
                + 'client.execute("my" + "proxy")\n'
            ),
            "super-dispatch": (
                "class Base:\n"
                "    def execute(self, provider):\n"
                + command
                + "class Client(Base):\n"
                + "    def execute(self, provider):\n"
                + "        super().execute(provider)\n"
                + "client = Client()\n"
                + 'client.execute("my" + "proxy")\n'
            ),
            "bound-method-alias": (
                "class Client:\n"
                "    def execute(self, provider):\n"
                + command
                + "client = Client()\n"
                + "runner = client.execute\n"
                + 'runner("my" + "proxy")\n'
            ),
            "conditional-instance": (
                "class PrivateClient:\n"
                "    def execute(self, provider):\n"
                + command
                + "class PublicClient:\n"
                + "    def execute(self, provider):\n"
                + '        run(["c", "--provider", provider, "status"])\n'
                + "client = PrivateClient() if enabled else PublicClient()\n"
                + 'client.execute("my" + "proxy")\n'
            ),
            "class-monkeypatch": (
                "class Client:\n"
                "    def execute(self, provider):\n"
                + '        run(["c", "--provider", provider, "status"])\n'
                + "def patched(self, provider):\n"
                + command.replace("        run(", "    run(")
                + "Client.execute = patched\n"
                + "client = Client()\n"
                + 'client.execute("my" + "proxy")\n'
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
                    {"private_endpoint", "private_model_override"},
                    {str(item["rule_id"]) for item in findings},
                )

    def test_python_cli_unsupported_method_dispatch_fails_closed(self) -> None:
        payload = (
            "def decorate(function):\n"
            "    return function\n"
            "class Client:\n"
            "    @decorate\n"
            "    def execute(self, provider):\n"
            "        run([\n"
            '            "c", "--provider", provider,\n'
            '            "--base-url", '
            '"https://x." + "private.example/v1",\n'
            '            "--model", "private-" + "m",\n'
            "        ])\n"
            "client = Client()\n"
            'client.execute("my" + "proxy")\n'
        )

        _reconstructed, failure = (
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
            "python_cli_analysis_limit_exceeded:dispatch",
            failure,
        )
        self.assertIn(
            "python_cli_analysis_limit_exceeded",
            {str(item["rule_id"]) for item in findings},
        )

    def test_python_cli_unittest_helpers_do_not_create_dispatch_failures(
        self,
    ) -> None:
        payload = (
            "class ScannerTest(unittest.TestCase):\n"
            "    def test_fixture(self):\n"
            '        option_prefix = "--"\n'
            '        option_name = "provider"\n'
            "        self.assertEqual(option_prefix, option_name)\n"
        )

        reconstructed, failure = (
            compliance_module._python_cli_argument_text(
                "test_scanner.py",
                payload,
            )
        )

        self.assertEqual("", failure)
        self.assertEqual("", reconstructed)

    def test_python_cli_external_base_data_attributes_are_not_dispatch(
        self,
    ) -> None:
        payload = (
            "class Client(ExternalBase):\n"
            "    def launch(self):\n"
            "        enabled = self.enabled\n"
            '        run(["c", "--provider", "openai", str(enabled)])\n'
            "Client().launch()\n"
        )

        reconstructed, failure = (
            compliance_module._python_cli_argument_text(
                "launcher.py",
                payload,
            )
        )

        self.assertEqual("", failure)
        self.assertIn("--provider openai", reconstructed)

    def test_python_cli_external_base_method_calls_fail_closed(self) -> None:
        payload = (
            "class Client(ExternalBase):\n"
            "    pass\n"
            "client = Client()\n"
            'client.execute(["c", "--provider", "my" + "proxy"])\n'
        )

        _reconstructed, failure = (
            compliance_module._python_cli_argument_text(
                "launcher.py",
                payload,
            )
        )

        self.assertEqual(
            "python_cli_analysis_limit_exceeded:dispatch",
            failure,
        )

    def test_python_cli_property_values_flow_into_commands(self) -> None:
        payload = (
            "class Client:\n"
            "    @property\n"
            "    def provider(self):\n"
            '        return "my" + "proxy"\n'
            "client = Client()\n"
            "run([\n"
            '    "c", "--provider", client.provider,\n'
            '    "--base-url", "https://x." + "private.example/v1",\n'
            '    "--model", "private-" + "m",\n'
            "])\n"
        )

        reconstructed, failure = (
            compliance_module._python_cli_argument_text(
                "launcher.py",
                payload,
            )
        )

        self.assertEqual("", failure)
        self.assertIn("--provider myproxy", reconstructed)

    def test_python_cli_irrelevant_entrypoint_is_not_symbolically_run(
        self,
    ) -> None:
        branches = "".join(
            f"    if flag_{index}:\n        value = {index!r}\n"
            for index in range(12)
        )
        payload = (
            "def launch():\n"
            "    run([\n"
            '        "c", "--provider", "my" + "proxy",\n'
            '        "--base-url", "https://x." + "private.example/v1",\n'
            '        "--model", "private-" + "m",\n'
            "    ])\n"
            "def unrelated_main():\n"
            "    value = 0\n"
            f"{branches}"
            "    return value\n"
            "unrelated_main()\n"
        )

        reconstructed, failure = (
            compliance_module._python_cli_argument_text(
                "launcher.py",
                payload,
            )
        )

        self.assertEqual("", failure)
        self.assertIn("--provider myproxy", reconstructed)

    def test_python_cli_called_argument_builder_uses_pre_rebind_global(
        self,
    ) -> None:
        payload = (
            'provider = "my" + "proxy"\n'
            "def launch():\n"
            "    args = [\n"
            '        "c", "--provider", provider,\n'
            '        "--base-url", "https://x." + "private.example/v1",\n'
            '        "--model", "private-" + "m",\n'
            "    ]\n"
            "launch()\n"
            "provider = resolve_provider()\n"
        )

        reconstructed, failure = (
            compliance_module._python_cli_argument_text(
                "launcher.py",
                payload,
            )
        )

        self.assertEqual("", failure)
        self.assertIn("--provider myproxy", reconstructed)

    def test_python_cli_uninvoked_execution_function_is_still_scanned(
        self,
    ) -> None:
        payload = (
            "def launch():\n"
            "    run([\n"
            '        "c", "--provider", "my" + "proxy",\n'
            '        "--base-url", '
            '"https://x." + "private.example/v1",\n'
            '        "--model", "private-" + "m",\n'
            "    ])\n"
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

        self.assertEqual("", failure)
        self.assertIn("myproxy", reconstructed)
        self.assertEqual(
            {"private_endpoint", "private_model_override"},
            {str(item["rule_id"]) for item in findings},
        )

    def test_python_cli_star_expansions_preserve_correlated_branches(
        self,
    ) -> None:
        payloads = {
            "args": (
                "def launch(*args):\n"
                '    run(["c", "--provider", *args])\n'
                + "launch(*(\n"
                + '    ("my" + "proxy",)\n'
                + "    if private_provider else\n"
                + '    ("openai", "--base-url", "https://x." '
                '+ "private.example/v1", "--model", "private-" + "m")\n'
                + "))\n"
            ),
            "kwargs": (
                "def launch(**kwargs):\n"
                "    run([\n"
                '        "c", "--provider", kwargs["provider"],\n'
                '        *kwargs["extras"],\n'
                "    ])\n"
                + "launch(**(\n"
                + '    {"provider": "my" + "proxy", "extras": ()}\n'
                + "    if private_provider else\n"
                + '    {"provider": "openai", '
                '"extras": ("--base-url", "https://x." '
                '+ "private.example/v1", "--model", "private-" + "m")}\n'
                + "))\n"
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
                self.assertIn("private.example", reconstructed)
                self.assertNotIn(
                    "--provider myproxy --base-url",
                    reconstructed,
                )
                self.assertNotIn(
                    "--provider myproxy --model",
                    reconstructed,
                )
                self.assertEqual([], findings)

    def test_python_cli_reconstruction_keeps_lexical_callables_distinct(
        self,
    ) -> None:
        payload = (
            "def safe():\n"
            "    pass\n"
            "def container():\n"
            "    def safe():\n"
            "        run([\n"
            '            "c", "--provider", "my" + "proxy",\n'
            '            "--base-url", '
            '"https://x." + "private.example/v1",\n'
            '            "--model", "private-" + "m",\n'
            "        ])\n"
            "safe()\n"
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

        self.assertEqual("", failure)
        self.assertEqual("", reconstructed)
        self.assertEqual([], findings)

    def test_python_cli_reconstruction_keeps_class_methods_distinct(
        self,
    ) -> None:
        payload = (
            'prefix = "--"\n'
            'option_name = "provider"\n'
            "private_option = prefix + option_name\n"
            'private_endpoint = "https://x." + "private.example/v1"\n'
            'private_model = "private-" + "m"\n'
            "class PrivateClient:\n"
            "    def execute(provider):\n"
            "        run([\n"
            '            "c", private_option, provider,\n'
            '            "--base-url", private_endpoint,\n'
            '            "--model", private_model,\n'
            "        ])\n"
            "class PublicClient:\n"
            "    def execute(provider):\n"
            '        run(["c", "--provider", provider, "status"])\n'
            'PublicClient.execute("my" + "proxy")\n'
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

        self.assertEqual("", failure)
        self.assertIn("myproxy", reconstructed)
        self.assertEqual([], findings)

    def test_python_cli_reconstruction_detects_assembled_provider_option(
        self,
    ) -> None:
        payload = (
            "def launch():\n"
            '    prefix = "--"\n'
            '    option = "provider"\n'
            "    provider_option = prefix + option\n"
            "    run([\n"
            '        "c", provider_option, "my" + "proxy",\n'
            '        "--base-url", "https://x." + "private.example/v1",\n'
            '        "--model", "private-" + "m",\n'
            "    ])\n"
            "launch()\n"
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

        self.assertEqual("", failure)
        self.assertIn("--provider myproxy", reconstructed)
        self.assertEqual(
            {"private_endpoint", "private_model_override"},
            {str(item["rule_id"]) for item in findings},
        )

    def test_python_cli_reconstruction_preserves_ten_correlated_states(
        self,
    ) -> None:
        branches = []
        for index in range(10):
            branches.append(
                ("if" if index == 0 else "elif")
                + f" choice == {index}:\n"
                + f'    provider = "public-{index}"\n'
                + f'    endpoint = "https://api{index}.example.com/v1"\n'
                + f'    model = "public-model-{index}"\n'
            )
        payload = (
            "".join(branches)
            + "run([\n"
            + '    "c", "--provider", provider,\n'
            + '    "--base-url", endpoint, "--model", model,\n'
            + "])\n"
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

        self.assertEqual("", failure)
        self.assertEqual(11, len(reconstructed.splitlines()))
        self.assertEqual([], findings)

    def test_python_cli_reconstruction_keeps_independent_commands_separate(
        self,
    ) -> None:
        payload = (
            "commands = [\n"
            '    ["c", "--provider", "my" + "proxy", "status"],\n'
            '    ["c", "--provider", "openai", "--base-url",\n'
            '     "https://x." + "private.example/v1", "--model",\n'
            '     "private-" + "m"],\n'
            "]\n"
            "for args in commands:\n"
            "    run(args)\n"
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

        self.assertEqual("", failure)
        self.assertIn("myproxy", reconstructed)
        self.assertEqual([], findings)

    def test_python_cli_forwarding_depth_fails_closed_without_crashing(
        self,
    ) -> None:
        definitions = [
            (
                f"def forward_{index}(provider):\n"
                f"    forward_{index + 1}(provider)\n"
            )
            for index in range(200)
        ]
        definitions.append(
            "def forward_200(provider):\n"
            '    run(["c", "--provider", provider])\n'
        )
        payload = "".join(definitions) + 'forward_0("my" + "proxy")\n'

        _reconstructed, failure = (
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

        self.assertIn(
            failure,
            {
                "python_cli_analysis_limit_exceeded:call_depth",
                "python_cli_analysis_limit_exceeded:recursion_depth",
            },
        )
        self.assertIn(
            "python_cli_analysis_limit_exceeded",
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
            "nested-list-comprehension": (
                "commands = [[\n"
                '    "commander", "--provider", provider,\n'
                f'    "--openai-base-url", {endpoint},\n'
                f'    "--model", {model},\n'
                '] for provider in ("my" + "proxy",)]\n'
            ),
            "nested-list-comprehension-loop": (
                "for args in [[\n"
                '    "commander", "--provider", provider,\n'
                f'    "--openai-base-url", {endpoint},\n'
                f'    "--model", {model},\n'
                '] for provider in ("my" + "proxy",)]:\n'
                "    run(args)\n"
            ),
            "nested-chunk-flattening": (
                "chunks = [\n"
                '    ["commander", "--provider", "my" + "proxy"],\n'
                '    ["--openai-base-url", '
                '"https://proxy." + "corp.example/v1"],\n'
                '    ["--model", "private-" + "deployment"],\n'
                "]\n"
                "run([item for chunk in chunks for item in chunk])\n"
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
                "api_key": {str(fake_key_fingerprint): 1},
                "api_key_assignment": {
                    str(fake_assignment_fingerprint): 1
                },
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
            duplicate_findings = scan_payload(
                allowed_path,
                (
                    f"first = '{fake_key}'\n"
                    f"second = '{fake_key}'\n"
                ).encode(),
            )
            self.assertEqual(1, len(duplicate_findings))
            self.assertEqual("api_key", duplicate_findings[0]["rule_id"])
            distinct_assignment_findings = scan_payload(
                allowed_path,
                (
                    f'SERVICE_API_KEY: "proxy-alias-key"\n'
                    f"{fake_assignment}\n"
                ).encode(),
            )
            self.assertEqual(1, len(distinct_assignment_findings))
            self.assertEqual(
                "api_key_assignment",
                distinct_assignment_findings[0]["rule_id"],
            )

    def test_fixture_allowlist_inventory_and_occurrences_are_exact(self) -> None:
        inventory = compliance_module._SAFE_FIXTURE_FINGERPRINTS

        for path, rules in inventory.items():
            with self.subTest(path=path):
                observed = Counter(
                    (
                        str(finding["rule_id"]),
                        str(finding["fingerprint"]),
                    )
                    for finding in scan_payload(
                        path,
                        Path(path).read_bytes(),
                        allow_safe_fixtures=False,
                    )
                )
                expected = Counter(
                    {
                        (rule_id, fingerprint): count
                        for rule_id, occurrences in rules.items()
                        for fingerprint, count in occurrences.items()
                    }
                )

                self.assertEqual(expected, observed)

    def test_credential_detector_requires_an_assignment(self) -> None:
        scanner_documentation = (
            b"Detect credential_path references and strings such as "
            b".aws/credentials without treating documentation as a configured path."
        )

        self.assertEqual([], scan_payload("scanner.py", scanner_documentation))

    def test_secret_detector_scans_normalized_path_text(self) -> None:
        secret = "sk-" + "liveabcdefghijklmnop"

        findings = scan_payload(
            f"safe/{secret}.txt",
            b"safe payload\n",
            allow_safe_fixtures=False,
        )

        self.assertEqual(1, len(findings))
        self.assertEqual(0, findings[0]["line"])
        self.assertEqual("api_key", findings[0]["rule_id"])

    def test_repository_scan_binds_committed_review_diff(self) -> None:
        tracked_blob = "a" * 40
        base_commit = "b" * 40
        head_commit = "c" * 40
        merge_base = "d" * 40
        diff = b"diff --git a/safe.txt b/safe.txt\n+review delta\n"

        def git_output(
            _repository_root: Path,
            arguments: list[str],
        ) -> bytes:
            if arguments == ["ls-files", "--stage", "-z"]:
                return f"100644 {tracked_blob} 0\tsafe.txt\0".encode()
            if arguments == ["cat-file", "blob", tracked_blob]:
                return b"safe\n"
            if arguments == [
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
            ]:
                return b""
            if arguments == [
                "rev-parse",
                "--verify",
                f"{base_commit}^{{commit}}",
            ]:
                return f"{base_commit}\n".encode()
            if arguments == ["rev-parse", "HEAD"]:
                return f"{head_commit}\n".encode()
            if arguments == [
                "merge-base",
                base_commit,
                head_commit,
            ]:
                return f"{merge_base}\n".encode()
            if arguments == [
                "diff",
                "--no-ext-diff",
                "--binary",
                f"{base_commit}...{head_commit}",
                "--",
            ]:
                return diff
            raise AssertionError(arguments)

        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.object(
                compliance_module,
                "_git_output",
                side_effect=git_output,
            ),
        ):
            report = scan_git_and_artifacts(
                Path(temporary),
                (),
                base_commit=base_commit,
            )

        diff_entry = next(
            entry
            for entry in report["input_manifest"]
            if entry["kind"] == "diff"
        )
        self.assertGreater(diff_entry["size"], 0)
        self.assertEqual(base_commit, diff_entry["base_commit"])
        self.assertEqual(head_commit, diff_entry["head_commit"])
        self.assertEqual(merge_base, diff_entry["merge_base"])

    def test_review_diff_rejects_non_exact_base_commit(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "exact Git commit"):
            compliance_module._review_range_evidence(Path.cwd(), "main")

    def test_repository_scan_includes_untracked_nonignored_files(self) -> None:
        secret = "sk-" + "liveabcdefghijklmnop"
        tracked_blob = "a" * 40
        base_commit = "b" * 40
        head_commit = "c" * 40
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
                if arguments == [
                    "rev-parse",
                    "--verify",
                    f"{base_commit}^{{commit}}",
                ]:
                    return f"{base_commit}\n".encode()
                if arguments == ["rev-parse", "HEAD"]:
                    return f"{head_commit}\n".encode()
                if arguments == [
                    "merge-base",
                    base_commit,
                    head_commit,
                ]:
                    return f"{base_commit}\n".encode()
                raise AssertionError(arguments)

            with mock.patch.object(
                compliance_module,
                "_git_output",
                side_effect=git_output,
            ):
                report = scan_git_and_artifacts(
                    root,
                    (),
                    base_commit=base_commit,
                )

        self.assertEqual(1, report["finding_count"])
        self.assertEqual("candidate.py", report["findings"][0]["path"])

    def test_repository_scan_stops_after_global_finding_limit(self) -> None:
        base_commit = "b" * 40
        head_commit = "c" * 40
        snapshot = ArchiveSnapshot(
            kind="wheel",
            path=Path("candidate.whl"),
            digest="d" * 64,
            entries=("starcraft_commander/later.py",),
            files={"starcraft_commander/later.py": b"safe = True\n"},
            blockers=(),
        )
        synthetic_findings = [
            {
                "path": "<git-diff>",
                "line": index + 1,
                "rule_id": "api_key_assignment",
                "fingerprint": f"{index:064x}",
            }
            for index in range(compliance_module.MAX_SCAN_FINDINGS)
        ]

        def git_output(
            _repository_root: Path,
            arguments: list[str],
        ) -> bytes:
            if arguments in (
                ["ls-files", "--stage", "-z"],
                ["ls-files", "--others", "--exclude-standard", "-z"],
            ):
                return b""
            if arguments[:2] == ["diff", "--no-ext-diff"]:
                return b""
            if arguments == [
                "rev-parse",
                "--verify",
                f"{base_commit}^{{commit}}",
            ]:
                return f"{base_commit}\n".encode()
            if arguments == ["rev-parse", "HEAD"]:
                return f"{head_commit}\n".encode()
            if arguments == [
                "merge-base",
                base_commit,
                head_commit,
            ]:
                return f"{base_commit}\n".encode()
            raise AssertionError(arguments)

        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.object(
                compliance_module,
                "_git_output",
                side_effect=git_output,
            ),
            mock.patch.object(
                compliance_module,
                "scan_payload",
                return_value=synthetic_findings,
            ) as scan,
        ):
            report = scan_git_and_artifacts(
                Path(temporary),
                (snapshot,),
                base_commit=base_commit,
            )

        self.assertEqual(1, scan.call_count)
        self.assertEqual(
            compliance_module.MAX_SCAN_FINDINGS,
            report["finding_count"],
        )
        self.assertEqual(
            1,
            sum(
                item["rule_id"] == "scan_finding_limit_exceeded"
                for item in report["findings"]
            ),
        )

    def test_artifact_scan_detects_bomless_utf16_le_and_be(self) -> None:
        host_key = "VOI_MYPROXY_" + "HOST"
        model_key = "VOI_MYPROXY_" + "MODEL"
        source = (
            f'{host_key} = "10.20.30.40"\n'
            f'{model_key} = "private-model"\n'
        )
        snapshot = ArchiveSnapshot(
            kind="wheel",
            path=Path("candidate.whl"),
            digest="a" * 64,
            entries=(
                "starcraft_commander/le.py",
                "starcraft_commander/be.py",
            ),
            files={
                "starcraft_commander/le.py": source.encode("utf-16-le"),
                "starcraft_commander/be.py": source.encode("utf-16-be"),
            },
            blockers=(),
        )
        base_commit = "b" * 40
        head_commit = "c" * 40

        def git_output(
            _repository_root: Path,
            arguments: list[str],
        ) -> bytes:
            if arguments == ["ls-files", "--stage", "-z"]:
                return b""
            if arguments == [
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
            ]:
                return b""
            if arguments[:2] == ["diff", "--no-ext-diff"]:
                return b""
            if arguments == [
                "rev-parse",
                "--verify",
                f"{base_commit}^{{commit}}",
            ]:
                return f"{base_commit}\n".encode()
            if arguments == ["rev-parse", "HEAD"]:
                return f"{head_commit}\n".encode()
            if arguments == [
                "merge-base",
                base_commit,
                head_commit,
            ]:
                return f"{base_commit}\n".encode()
            raise AssertionError(arguments)

        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.object(
                compliance_module,
                "_git_output",
                side_effect=git_output,
            ),
        ):
            report = scan_git_and_artifacts(
                Path(temporary),
                (snapshot,),
                base_commit=base_commit,
            )

        findings = report["findings"]
        self.assertEqual(4, len(findings))
        self.assertEqual(
            {"private_endpoint", "private_model_override"},
            {str(item["rule_id"]) for item in findings},
        )
        self.assertEqual(
            {
                "wheel/starcraft_commander/le.py",
                "wheel/starcraft_commander/be.py",
            },
            {str(item["path"]) for item in findings},
        )

    def test_repository_scan_reads_tracked_dangling_symlink_blob(self) -> None:
        tracked_blob = "b" * 40
        bearer = "Bearer " + "opaqueabcdefghijklmnop"
        base_commit = "c" * 40
        head_commit = "d" * 40

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
            if arguments == [
                "rev-parse",
                "--verify",
                f"{base_commit}^{{commit}}",
            ]:
                return f"{base_commit}\n".encode()
            if arguments == ["rev-parse", "HEAD"]:
                return f"{head_commit}\n".encode()
            if arguments == [
                "merge-base",
                base_commit,
                head_commit,
            ]:
                return f"{base_commit}\n".encode()
            raise AssertionError(arguments)

        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.object(
                compliance_module,
                "_git_output",
                side_effect=git_output,
            ),
        ):
            report = scan_git_and_artifacts(
                Path(temporary),
                (),
                base_commit=base_commit,
            )

        self.assertEqual(1, report["finding_count"])
        self.assertEqual("bearer_token", report["findings"][0]["rule_id"])
        self.assertEqual(
            "private-endpoint-link",
            report["findings"][0]["path"],
        )


class DerivedVerdictTest(unittest.TestCase):
    @staticmethod
    def _rebind_report_projection(report: dict[str, object]) -> None:
        payloads = compliance_module._distribution_report_scan_payloads(
            report
        )
        secret_scan = report["secret_scan"]
        assert isinstance(secret_scan, dict)
        manifest = secret_scan["input_manifest"]
        assert isinstance(manifest, list)
        for name, payload in payloads.items():
            entry = next(
                item
                for item in manifest
                if item["path"] == f"report/{name}"
            )
            entry["size"] = len(payload)
            entry["sha256"] = compliance_module.sha256_bytes(payload)
        secret_scan["input_manifest_sha256"] = (
            compliance_module.sha256_bytes(
                compliance_module.canonical_json_text(manifest).encode()
            )
        )

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
        for phase in ("before", "after"):
            state = repository[phase]
            assert isinstance(state, dict)
            state["tree"] = source_tree
        repository["head_commit"] = repository["before"]["head"]
        review_diff = b"+synthetic review delta\n"
        manifest = [
            tracked_entry,
            {
                "kind": "diff",
                "path": "<git-diff>",
                "size": len(review_diff),
                "sha256": compliance_module.sha256_bytes(review_diff),
                "base_commit": repository["base_commit"],
                "head_commit": repository["head_commit"],
                "merge_base": repository["merge_base"],
            },
        ]
        artifacts = report["artifacts"]
        assert isinstance(artifacts, dict)
        for kind in ("wheel", "sdist"):
            artifact = artifacts[kind]
            assert isinstance(artifact, dict)
            file_manifest = artifact["file_manifest"]
            file_sizes = artifact["file_sizes"]
            metadata_manifest = artifact["metadata_manifest"]
            metadata_sizes = artifact["metadata_sizes"]
            assert isinstance(file_manifest, dict)
            assert isinstance(file_sizes, dict)
            assert isinstance(metadata_manifest, dict)
            assert isinstance(metadata_sizes, dict)
            for path, digest in file_manifest.items():
                manifest.append(
                    {
                        "kind": kind,
                        "path": f"{kind}/{path}",
                        "size": file_sizes[path],
                        "sha256": digest,
                    }
                )
            for path, digest in metadata_manifest.items():
                manifest.append(
                    {
                        "kind": kind,
                        "path": f"{kind}/{path}",
                        "size": metadata_sizes[path],
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
        source_pyproject_raw = """[build-system]
requires = ["setuptools==82.0.1"]
build-backend = "setuptools.build_meta"

[project]
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
                "base_commit": "1" * 40,
                "head_commit": digest[:40],
                "merge_base": "1" * 40,
                **{
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
                    "metadata_manifest": {},
                    "metadata_sizes": {},
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
                    "metadata_manifest": {},
                    "metadata_sizes": {},
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
                "installed_raw": metadata_raw,
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
                    "installed_metadata": metadata_raw,
                    "license_expression": EXPECTED_LICENSE_EXPRESSION,
                    "packaged_defaults_loaded": True,
                    "runtime_data_loaded": True,
                    "source_repository_root_is_none": True,
                    "target_packaged_defaults_loaded": True,
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
        trusted_secret_scan_patcher = mock.patch.object(
            compliance_module,
            "_trusted_secret_scan_evidence",
            return_value=copy.deepcopy(self.report["secret_scan"]),
        )
        trusted_secret_scan_patcher.start()
        self.addCleanup(trusted_secret_scan_patcher.stop)
        self.report = compliance_module._with_derived_verdict(self.report)

    def test_accepts_complete_derived_evidence(self) -> None:
        self.assertEqual([], distribution_report_blockers(self.report))

    def test_accepts_prescanned_archive_metadata_in_secret_scan_manifest(
        self,
    ) -> None:
        comment = b"C" * 900_000
        tar_payload = ArchivePolicyTest._tar_payload(
            (
                "voistarcraft2-0.1.0/"
                "starcraft_commander/runtime_data.py",
                b"safe",
            )
        )
        members = [
            ArchivePolicyTest._gzip_with_header_metadata(
                tar_payload,
                comment=comment,
            ),
            *(
                ArchivePolicyTest._gzip_with_header_metadata(
                    b"",
                    comment=comment,
                )
                for _ in range(3)
            ),
            ArchivePolicyTest._gzip_with_header_metadata(
                b"A" * 400_000,
                comment=comment,
            ),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            sdist_path = Path(temporary) / "voistarcraft2-0.1.0.tar.gz"
            sdist_path.write_bytes(b"".join(members))
            snapshot = compliance_module.inspect_sdist(sdist_path)

        self.assertTrue(snapshot.prescanned_inputs)
        artifact_evidence = compliance_module._artifact_evidence(snapshot)

        report = copy.deepcopy(self.report)
        artifact = report["artifacts"]["sdist"]
        secret_scan = report["secret_scan"]
        for prescanned in snapshot.prescanned_inputs:
            prescanned_path = str(prescanned["path"])
            metadata_path = prescanned_path.removeprefix("sdist/")
            metadata_digest = artifact_evidence["metadata_manifest"][
                metadata_path
            ]
            metadata_size = artifact_evidence["metadata_sizes"][
                metadata_path
            ]
            artifact["metadata_manifest"][metadata_path] = metadata_digest
            artifact["metadata_sizes"][metadata_path] = metadata_size
            secret_scan["input_manifest"].append(
                {
                    "kind": "sdist",
                    "path": prescanned_path,
                    "size": metadata_size,
                    "sha256": metadata_digest,
                }
            )
        secret_scan["input_manifest"].sort(
            key=lambda item: str(item["path"])
        )
        secret_scan["scanned_file_count"] = len(
            secret_scan["input_manifest"]
        )
        self._rebind_report_projection(report)
        trusted_artifacts = copy.deepcopy(report["artifacts"])
        for trusted_artifact in trusted_artifacts.values():
            trusted_artifact["archive_blockers"] = []

        with (
            mock.patch.object(
                compliance_module,
                "_trusted_artifact_evidence",
                return_value=trusted_artifacts,
            ),
            mock.patch.object(
                compliance_module,
                "_trusted_secret_scan_evidence",
                return_value=copy.deepcopy(secret_scan),
            ),
        ):
            report = compliance_module._with_derived_verdict(report)
            codes = {
                str(item["code"])
                for item in distribution_report_blockers(report)
            }

        self.assertNotIn("invalid_secret_scan_evidence", codes)

    def test_rejects_missing_derived_verdict(self) -> None:
        report = copy.deepcopy(self.report)
        for field in ("blockers", "ok", "status"):
            report.pop(field)

        codes = {
            str(item["code"]) for item in distribution_report_blockers(report)
        }

        self.assertIn("invalid_distribution_compliance_verdict", codes)

    def test_rejects_secret_in_nested_report_projection_after_hash_rebind(
        self,
    ) -> None:
        mutations = (
            ("artifacts", "wheel"),
            ("repository", "before"),
            ("metadata", None),
        )
        for section, child in mutations:
            with self.subTest(section=section, child=child):
                report = copy.deepcopy(self.report)
                target = report[section]
                assert isinstance(target, dict)
                if child is not None:
                    target = target[child]
                    assert isinstance(target, dict)
                target["X_" + "API_KEY"] = "abcdefghijkl!" + "mnopqrst"
                self._rebind_report_projection(report)

                codes = {
                    str(item["code"])
                    for item in distribution_report_blockers(report)
                }

                self.assertIn(
                    "secret_or_private_config_detected",
                    codes,
                )

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

    def test_rejects_unknown_top_level_field_after_projection_rebind(
        self,
    ) -> None:
        report = copy.deepcopy(self.report)
        report["publication_authorized"] = True
        payloads = compliance_module._distribution_report_scan_payloads(
            report
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
                compliance_module.canonical_json_text(manifest).encode()
            )
        )

        blockers = distribution_report_blockers(report)

        self.assertIn(
            {
                "code": "unexpected_distribution_compliance_fields",
                "fields": ["publication_authorized"],
            },
            blockers,
        )

    def test_rejects_unscanned_secret_scan_fields_and_forged_verdict(
        self,
    ) -> None:
        key_name = "X_" + "API_KEY"
        secret_value = "private-" + "secret-value"
        report = copy.deepcopy(self.report)
        report["secret_scan"][key_name] = secret_value

        codes = {
            str(item["code"])
            for item in distribution_report_blockers(report)
        }

        self.assertIn("invalid_secret_scan_evidence", codes)

        report = compliance_module._with_derived_verdict(
            copy.deepcopy(self.report)
        )
        report["blockers"].append(
            {
                "code": "attacker",
                key_name: secret_value,
            }
        )
        report["ok"] = True
        report["status"] = "passed"

        codes = {
            str(item["code"])
            for item in distribution_report_blockers(report)
        }

        self.assertIn("invalid_distribution_compliance_verdict", codes)

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

    def test_rejects_extra_artifact_evidence_keys(self) -> None:
        report = copy.deepcopy(self.report)
        report["artifacts"]["attacker"] = copy.deepcopy(
            report["artifacts"]["wheel"]
        )

        codes = {
            str(item["code"]) for item in distribution_report_blockers(report)
        }

        self.assertIn("invalid_artifact_evidence", codes)

    def test_rejects_secret_scan_findings_deleted_from_submitted_report(
        self,
    ) -> None:
        trusted = copy.deepcopy(self.report["secret_scan"])
        trusted["findings"] = [
            {
                "path": "wheel/starcraft_commander/runtime_data.py",
                "line": 1,
                "rule_id": "api_key",
                "fingerprint": "f" * 64,
            }
        ]
        trusted["finding_count"] = 1

        with mock.patch.object(
            compliance_module,
            "_trusted_secret_scan_evidence",
            return_value=trusted,
        ):
            codes = {
                str(item["code"])
                for item in distribution_report_blockers(self.report)
            }

        self.assertIn("secret_scan_provenance_mismatch", codes)

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

    def test_rejects_missing_or_mismatched_installed_metadata(self) -> None:
        report = copy.deepcopy(self.report)
        report["metadata"].pop("installed_raw")
        codes = {
            str(item["code"]) for item in distribution_report_blockers(report)
        }
        self.assertIn("invalid_installed_metadata_evidence", codes)

        report = copy.deepcopy(self.report)
        report["metadata"]["installed_raw"] = "Metadata-Version: 2.4\n"
        codes = {
            str(item["code"]) for item in distribution_report_blockers(report)
        }
        self.assertIn("invalid_installed_metadata_evidence", codes)

        for installed_metadata, expected_code in (
            (None, "installed_metadata_missing"),
            ("Metadata-Version: 2.4\n", "installed_metadata_mismatch"),
        ):
            with self.subTest(expected_code=expected_code):
                report = copy.deepcopy(self.report)
                if installed_metadata is None:
                    report["install_smoke"]["payload"].pop(
                        "installed_metadata"
                    )
                else:
                    report["install_smoke"]["payload"][
                        "installed_metadata"
                    ] = installed_metadata

                codes = {
                    str(item["code"])
                    for item in distribution_report_blockers(report)
                }

                self.assertIn(expected_code, codes)

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
        repository = copy.deepcopy(self.report["repository"])
        repository["before"]["ok"] = False
        repository["before"]["dirty_entries"] = [" M README.md"]
        report["repository"] = repository
        report["install_smoke"] = {"attempted": False}

        codes = {
            str(item["code"]) for item in distribution_report_blockers(report)
        }

        self.assertIn("repository_not_clean_commit", codes)
        self.assertIn("isolated_install_not_attempted", codes)

    def test_rejects_incomplete_installed_package_smoke_contract(self) -> None:
        expected_codes = {
            "packaged_defaults_loaded": "installed_packaged_defaults_failed",
            "source_repository_root_is_none": (
                "installed_source_root_not_isolated"
            ),
            "target_packaged_defaults_loaded": (
                "target_install_packaged_defaults_failed"
            ),
        }
        for field, code in expected_codes.items():
            with self.subTest(field=field):
                report = copy.deepcopy(self.report)
                report["install_smoke"]["payload"][field] = False

                codes = {
                    str(item["code"])
                    for item in distribution_report_blockers(report)
                }

                self.assertIn(code, codes)

    def test_rejects_repository_identity_drift_and_inconsistent_raw_state(
        self,
    ) -> None:
        report = dict(self.report)
        repository = copy.deepcopy(self.report["repository"])
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
                    manifest[0]["sha256"] = "not-a-digest"
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


class DistributionEvidenceWritingTest(unittest.TestCase):
    def test_installed_metadata_evidence_uses_installed_payload(self) -> None:
        report = {
            "schema_version": (
                compliance_module.DISTRIBUTION_COMPLIANCE_SCHEMA_VERSION
            ),
            "status": "passed",
            "ok": True,
            "metadata": {
                "raw": "WHEEL-ARCHIVE-METADATA",
                "installed_raw": "INSTALLED-DISTRIBUTION-METADATA",
            },
            "artifacts": {
                "wheel": {"entries": []},
                "sdist": {"entries": []},
            },
            "dependencies": {},
            "secret_scan": {
                "finding_count": 0,
                "findings": [],
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary)

            compliance_module.write_distribution_evidence(
                report,
                output_dir,
                public_report=report,
            )

            self.assertEqual(
                "INSTALLED-DISTRIBUTION-METADATA",
                (output_dir / "installed.METADATA").read_text(),
            )

    def test_sensitive_report_writes_only_minimal_redacted_evidence(self) -> None:
        secret = "sk-" + "liveabcdefghijklmnop"
        report = {
            "schema_version": (
                compliance_module.DISTRIBUTION_COMPLIANCE_SCHEMA_VERSION
            ),
            "status": "blocked",
            "ok": False,
            "metadata": {"raw": f"Authorization: Bearer {secret}"},
            "blockers": [
                {
                    "code": "invalid_metadata_evidence",
                    "observed": secret,
                }
            ],
            "secret_scan": {
                "finding_count": 0,
                "findings": [],
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary)

            compliance_module.write_distribution_evidence(report, output_dir)

            self.assertEqual(
                {
                    "distribution-compliance.json",
                    "distribution-compliance.md",
                    "secret-scan.json",
                },
                {path.name for path in output_dir.iterdir()},
            )
            combined = b"".join(
                path.read_bytes()
                for path in output_dir.iterdir()
                if path.is_file()
            )
            self.assertNotIn(secret.encode(), combined)
            public = compliance_module._external_distribution_report(report)
            self.assertTrue(public["evidence_redacted"])
            self.assertEqual("blocked", public["status"])

    def test_output_directory_symlink_is_rejected_without_deleting_target(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            sentinel = target / "sentinel.txt"
            sentinel.write_text("survives\n", encoding="utf-8")
            output_dir = root / "distribution-compliance-evidence"
            output_dir.symlink_to(target, target_is_directory=True)

            with self.assertRaisesRegex(RuntimeError, "real directory"):
                compliance_module.write_distribution_evidence(
                    {"ok": False, "status": "blocked"},
                    output_dir,
                )

            self.assertEqual("survives\n", sentinel.read_text())

    def test_output_directory_replacement_cannot_redirect_writes(self) -> None:
        report = {"ok": False, "status": "blocked"}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output_dir = root / "distribution-compliance-evidence"
            target = root / "target"
            target.mkdir()
            detached = root / "detached-evidence"
            original_write = compliance_module._write_evidence_bytes
            replaced = False

            def replace_then_write(
                directory_fd: int,
                filename: str,
                payload: bytes,
            ) -> None:
                nonlocal replaced
                if not replaced:
                    output_dir.rename(detached)
                    output_dir.symlink_to(target, target_is_directory=True)
                    replaced = True
                original_write(directory_fd, filename, payload)

            with mock.patch.object(
                compliance_module,
                "_write_evidence_bytes",
                side_effect=replace_then_write,
            ):
                compliance_module.write_distribution_evidence(
                    report,
                    output_dir,
                )

            self.assertEqual([], list(target.iterdir()))
            self.assertTrue(
                (detached / "distribution-compliance.json").is_file()
            )

    def test_output_leaf_replacement_is_rejected_without_following_symlink(
        self,
    ) -> None:
        report = {"ok": False, "status": "blocked"}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output_dir = root / "distribution-compliance-evidence"
            target = root / "target.txt"
            target.write_text("survives\n", encoding="utf-8")
            original_open = os.open
            injected = False

            def replace_leaf(
                path: object,
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                nonlocal injected
                if (
                    not injected
                    and dir_fd is not None
                    and path == "distribution-compliance.json"
                ):
                    (output_dir / str(path)).symlink_to(target)
                    injected = True
                return original_open(path, flags, mode, dir_fd=dir_fd)

            with (
                mock.patch.object(
                    compliance_module.os,
                    "open",
                    side_effect=replace_leaf,
                ),
                self.assertRaisesRegex(
                    RuntimeError,
                    "failed to create distribution evidence file",
                ),
            ):
                compliance_module.write_distribution_evidence(
                    report,
                    output_dir,
                )

            self.assertEqual("survives\n", target.read_text())

    def test_nonempty_output_directory_is_rejected_without_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary)
            sentinel = output_dir / "sentinel.txt"
            sentinel.write_text("survives\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "must be empty"):
                compliance_module.write_distribution_evidence(
                    {"ok": False, "status": "blocked"},
                    output_dir,
                )

            self.assertEqual("survives\n", sentinel.read_text())

    def test_main_exits_nonzero_when_public_report_is_redacted(self) -> None:
        secret = "sk-" + "liveabcdefghijklmnop"
        internal_report = {
            "ok": True,
            "status": "passed",
            "blockers": [],
            "secret_scan": {
                "finding_count": 0,
                "findings": [],
            },
            "artifact_path": f"/tmp/{secret}.whl",
        }
        with (
            mock.patch.object(
                compliance_module,
                "build_distribution_report",
                return_value=internal_report,
            ),
            mock.patch.object(
                compliance_module,
                "write_distribution_evidence",
            ) as write,
        ):
            result = compliance_module.main(
                [
                    "--repository",
                    ".",
                    "--base-commit",
                    "a" * 40,
                    "--dist-dir",
                    "dist",
                    "--output-dir",
                    "evidence",
                ]
            )

        self.assertEqual(1, result)
        public_report = write.call_args.kwargs["public_report"]
        self.assertFalse(public_report["ok"])
        self.assertEqual("blocked", public_report["status"])


if __name__ == "__main__":
    unittest.main()
