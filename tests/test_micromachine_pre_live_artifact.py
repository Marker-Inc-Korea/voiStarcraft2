"""Tests for immutable MicroMachine pre-live artifact bundles."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import stat
import struct
import tempfile
import unittest
import warnings
import zipfile

from starcraft_commander.micromachine_build_identity import (
    MICROMACHINE_BUILD_IDENTITY_SCHEMA_VERSION,
)
from starcraft_commander.micromachine_pre_live_artifact import (
    DETERMINISTIC_ZIP_TIMESTAMP,
    GITHUB_ARTIFACT_BUNDLE_MEMBER_NAME,
    PRE_LIVE_ARTIFACT_MANIFEST_NAME,
    PRE_LIVE_ARTIFACT_SCHEMA,
    PRE_LIVE_ARTIFACT_SCHEMA_VERSION,
    PreLiveArtifactLimits,
    PreLiveArtifactMetadata,
    PreLiveBuildAdmissionSnapshot,
    build_pre_live_artifact_bundle,
    canonical_ctest_evidence_bytes,
    canonical_json_bytes,
    verify_downloaded_pre_live_artifact,
    verify_pre_live_artifact_bundle,
)


REPOSITORY = "Marker-Inc-Korea/voiStarcraft2"
COMMIT = "a" * 40
REPORT_IDENTITY = "sha256:" + ("b" * 64)
WORKFLOW_PATH = ".github/workflows/pre-live.yml"
WORKFLOW_REF = (
    f"{REPOSITORY}/{WORKFLOW_PATH}"
    "@refs/heads/issue-138-authenticated-prelive-provenance"
)
WORKFLOW_SHA = "e" * 40


class PreLiveArtifactBundleTest(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        repository_input_paths = {
            "hook_manifest": {
                "path": "integrations/micromachine/HOOK_MANIFEST.json",
                "sha256": "c" * 64,
            }
        }
        upstream_commit_policy = {
            "path": "integrations/micromachine/scripts/build_macos_local.sh",
            "sha256": "f" * 64,
            "micromachine_commit": "1" * 40,
            "s2client_commit": "2" * 40,
        }
        repository_input_material = {
            "paths": repository_input_paths,
            "upstream_commit_policy": upstream_commit_policy,
        }
        self.repository_input_identity = "sha256:" + ("d" * 64)
        self.repository_input = canonical_json_bytes(
            {
                "schema_version": 1,
                "repository_commit": COMMIT,
                "build_input_identity": self.repository_input_identity,
                "repository_inputs_digest": "sha256:"
                + sha256(canonical_json_bytes(repository_input_material)),
                "paths": repository_input_paths,
                "upstream_commit_policy": upstream_commit_policy,
            }
        )
        self.binary = b"\x7fELF-micromachine-production-binary"
        self.output = canonical_json_bytes(
            {
                "artifact": "pre-live-evidence",
                "result": "derived-by-verifier",
            }
        )
        self.metadata = PreLiveArtifactMetadata(
            authority_scope="candidate_pr",
            release_authoritative=False,
            authority_event="pull_request",
            pull_request_database_id=30001,
            pull_request_number=138,
            pull_request_head_sha=COMMIT,
            pull_request_head_ref=("issue-138-authenticated-prelive-provenance"),
            pull_request_head_repository_id=812345,
            repository_full_name=REPOSITORY,
            repository_database_id=812345,
            repository_commit=COMMIT,
            workflow_id=9001,
            workflow_path=WORKFLOW_PATH,
            workflow_ref=WORKFLOW_REF,
            workflow_sha=WORKFLOW_SHA,
            run_id=10001,
            run_attempt=2,
            job_id=20001,
            job_name="build-and-attest",
            artifact_logical_name="pre-live-evidence",
            artifact_member="payload/pre-live-evidence.json",
            build_report_identity=REPORT_IDENTITY,
            build_report_member="build/voi_build_identity.json",
            binary_member="build/MicroMachine",
            repository_input_member="build/repository-input.json",
            repository_input_identity=self.repository_input_identity,
            ctest_member="build/ctest-evidence.json",
            producer_policy_id="voi.pre-live.local-producer.v1",
            producer_policy_member="producer/policy.json",
            producer_executable_member="producer/executable",
            producer_argv_member="producer/argv.json",
            producer_output_member="payload/pre-live-evidence.json",
            producer_provenance_member="producer/provenance.json",
        )
        self.members = self.make_members()
        self.admission_snapshot = PreLiveBuildAdmissionSnapshot(
            build_report_bytes=self.members[self.metadata.build_report_member],
            binary_bytes=self.members[self.metadata.binary_member],
            binary_mode=stat.S_IFREG | 0o755,
        )
        self.bundle = build_pre_live_artifact_bundle(
            self.metadata,
            self.members,
            admission_snapshot=self.admission_snapshot,
        )

    def make_members(self) -> dict[str, bytes]:
        policy = canonical_json_bytes(
            {
                "policy_id": "voi.pre-live.local-producer.v1",
                "allowed": ["/opt/voi/bin/pre-live-producer"],
            }
        )
        executable = b"trusted-local-producer-executable"
        argv = canonical_json_bytes(
            [
                "/opt/voi/bin/pre-live-producer",
                "--output",
                "pre-live-evidence.json",
            ]
        )
        report = {
            "schema_version": MICROMACHINE_BUILD_IDENTITY_SCHEMA_VERSION,
            "identity": REPORT_IDENTITY,
            "ok": True,
            "failures": [],
            "observed": {
                "binary_sha256": sha256(self.binary),
                "embedded_build_input_identity": self.repository_input_identity,
            },
        }
        ctest = canonical_ctest_evidence_bytes(
            make_ctest_evidence("/private/tmp/voi/build")
        )
        return {
            "build/voi_build_identity.json": canonical_json_bytes(report),
            "build/MicroMachine": self.binary,
            "build/repository-input.json": self.repository_input,
            "build/ctest-evidence.json": ctest,
            "producer/policy.json": policy,
            "producer/executable": executable,
            "producer/argv.json": argv,
            "payload/pre-live-evidence.json": self.output,
            "producer/provenance.json": canonical_json_bytes(
                {
                    "schema_version": 1,
                    "authority": {
                        "scope": self.metadata.authority_scope,
                        "release_authoritative": (self.metadata.release_authoritative),
                        "event": self.metadata.authority_event,
                        "pull_request": {
                            "database_id": (self.metadata.pull_request_database_id),
                            "number": self.metadata.pull_request_number,
                            "head_sha": self.metadata.pull_request_head_sha,
                            "head_ref": self.metadata.pull_request_head_ref,
                            "head_repository_id": (
                                self.metadata.pull_request_head_repository_id
                            ),
                        },
                    },
                    "producer_id": "voi.pre-live.local-producer.v1",
                    "policy_sha256": sha256(policy),
                    "argv_sha256": sha256(argv),
                    "executable_sha256": sha256(executable),
                    "output_sha256": sha256(self.output),
                    "exit_code": 0,
                    "repository_commit": COMMIT,
                    "started_at": "2026-07-30T00:00:00Z",
                    "ended_at": "2026-07-30T00:00:01Z",
                    "stdout_sha256": sha256(b"producer stdout"),
                    "stderr_sha256": sha256(b""),
                }
            ),
        }

    def test_deterministic_rebuild_and_verified_evidence(self) -> None:
        rebuilt = build_pre_live_artifact_bundle(
            as_nested_metadata(self.metadata),
            dict(reversed(list(self.members.items()))),
            admission_snapshot=self.admission_snapshot,
        )
        report = verify_pre_live_artifact_bundle(
            self.bundle,
            caller_claims={"ok": False, "status": "failure"},
            admission_snapshot=self.admission_snapshot,
        )

        self.assertEqual(self.bundle, rebuilt)
        self.assertTrue(report["ok"], report["blockers"])
        self.assertTrue(report["caller_claims_ignored"])
        self.assertTrue(report["manifest_evidence"]["canonical"])
        self.assertEqual(
            sha256(self.bundle),
            report["manifest_evidence"]["bundle_sha256"],
        )
        self.assertEqual(
            sorted(self.members),
            [item["name"] for item in report["member_evidence"]],
        )
        for evidence in report["member_evidence"]:
            name = evidence["name"]
            self.assertEqual(sha256(self.members[name]), evidence["sha256"])
            self.assertEqual(len(self.members[name]), evidence["size_bytes"])

        with zipfile.ZipFile(io.BytesIO(self.bundle)) as archive:
            infos = archive.infolist()
            self.assertEqual(
                [
                    PRE_LIVE_ARTIFACT_MANIFEST_NAME,
                    *sorted(self.members),
                ],
                [info.filename for info in infos],
            )
            self.assertEqual(b"", archive.comment)
            for info in infos:
                self.assertEqual(DETERMINISTIC_ZIP_TIMESTAMP, info.date_time)
                self.assertEqual(zipfile.ZIP_STORED, info.compress_type)
                self.assertEqual(b"", info.extra)
                self.assertEqual(b"", info.comment)
                self.assertEqual(stat.S_IFREG, stat.S_IFMT(info.external_attr >> 16))
                expected_permissions = (
                    0o755
                    if info.filename == self.metadata.binary_member
                    else 0o644
                )
                self.assertEqual(
                    expected_permissions,
                    (info.external_attr >> 16) & 0o777,
                )

            manifest_bytes = archive.read(PRE_LIVE_ARTIFACT_MANIFEST_NAME)
            manifest = json.loads(manifest_bytes)
            self.assertEqual(canonical_json_bytes(manifest), manifest_bytes)
            self.assertEqual(PRE_LIVE_ARTIFACT_SCHEMA, manifest["schema"])
            self.assertEqual(
                PRE_LIVE_ARTIFACT_SCHEMA_VERSION,
                manifest["schema_version"],
            )
            self.assertEqual(
                {
                    "scope": "candidate_pr",
                    "release_authoritative": False,
                    "event": "pull_request",
                    "pull_request": {
                        "database_id": 30001,
                        "number": 138,
                        "head_sha": COMMIT,
                        "head_ref": ("issue-138-authenticated-prelive-provenance"),
                        "head_repository_id": 812345,
                    },
                },
                manifest["authority"],
            )
            self.assertEqual(
                self.metadata.repository_database_id,
                manifest["repository"]["database_id"],
            )
            self.assertEqual(
                {
                    "id": self.metadata.workflow_id,
                    "path": WORKFLOW_PATH,
                    "ref": WORKFLOW_REF,
                    "sha": WORKFLOW_SHA,
                },
                manifest["workflow"],
            )
            self.assertEqual(
                self.metadata.artifact_logical_name,
                manifest["artifact"]["logical_name"],
            )
            self.assertEqual(
                sha256(self.output),
                manifest["artifact"]["sha256"],
            )
            self.assertEqual(
                manifest["artifact"]["sha256"],
                manifest["producer"]["output_sha256"],
            )

    def test_rejects_toctou_replacement_after_build_admission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report_path = root / "voi_build_identity.json"
            binary_path = root / "MicroMachine"
            report_path.write_bytes(
                self.members[self.metadata.build_report_member]
            )
            binary_path.write_bytes(self.members[self.metadata.binary_member])
            binary_path.chmod(0o755)
            admitted = PreLiveBuildAdmissionSnapshot(
                build_report_bytes=report_path.read_bytes(),
                binary_bytes=binary_path.read_bytes(),
                binary_mode=binary_path.stat().st_mode,
            )

            replacements = {
                "build report": (
                    self.metadata.build_report_member,
                    b'{"attacker":"replacement"}',
                ),
                "binary": (
                    self.metadata.binary_member,
                    b"replacement-micromachine-binary",
                ),
            }
            for name, (member_name, replacement) in replacements.items():
                with self.subTest(name=name):
                    report_path.write_bytes(
                        self.members[self.metadata.build_report_member]
                    )
                    binary_path.write_bytes(
                        self.members[self.metadata.binary_member]
                    )
                    source_path = (
                        report_path
                        if member_name == self.metadata.build_report_member
                        else binary_path
                    )
                    source_path.write_bytes(replacement)
                    reopened_members = dict(self.members)
                    reopened_members[self.metadata.build_report_member] = (
                        report_path.read_bytes()
                    )
                    reopened_members[self.metadata.binary_member] = (
                        binary_path.read_bytes()
                    )

                    with self.assertRaisesRegex(
                        ValueError,
                        "immutable admission snapshot",
                    ):
                        build_pre_live_artifact_bundle(
                            self.metadata,
                            reopened_members,
                            admission_snapshot=admitted,
                        )

    def test_verifier_rejects_coherent_build_replacement_against_admission(
        self,
    ) -> None:
        replacement_binary = b"attacker-controlled-production-binary"
        replacement_report = json.loads(
            self.members[self.metadata.build_report_member]
        )
        replacement_report["observed"]["binary_sha256"] = sha256(
            replacement_binary
        )
        replacement_report["attacker_note"] = "same semantic identity"
        replacement_report_bytes = canonical_json_bytes(replacement_report)
        replacement_members = dict(self.members)
        replacement_members[self.metadata.build_report_member] = (
            replacement_report_bytes
        )
        replacement_members[self.metadata.binary_member] = replacement_binary
        replacement_admission = PreLiveBuildAdmissionSnapshot(
            build_report_bytes=replacement_report_bytes,
            binary_bytes=replacement_binary,
            binary_mode=stat.S_IFREG | 0o755,
        )
        replacement_bundle = build_pre_live_artifact_bundle(
            self.metadata,
            replacement_members,
            admission_snapshot=replacement_admission,
        )

        unpinned = verify_pre_live_artifact_bundle(replacement_bundle)
        pinned = verify_pre_live_artifact_bundle(
            replacement_bundle,
            admission_snapshot=self.admission_snapshot,
        )

        self.assertTrue(unpinned["ok"], unpinned["blockers"])
        self.assertFalse(pinned["ok"])
        self.assertEqual(
            {
                "admitted_build_report_mismatch",
                "admitted_build_binary_mismatch",
            },
            blocker_codes(pinned),
        )

    def test_rejects_non_executable_admission_and_archive_mode(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be executable"):
            PreLiveBuildAdmissionSnapshot(
                build_report_bytes=self.members[
                    self.metadata.build_report_member
                ],
                binary_bytes=self.members[self.metadata.binary_member],
                binary_mode=stat.S_IFREG | 0o644,
            )

        non_executable = rewrite_bundle(
            self.bundle,
            mode_replacements={
                self.metadata.binary_member: stat.S_IFREG | 0o644,
            },
        )
        report = verify_pre_live_artifact_bundle(
            non_executable,
            admission_snapshot=self.admission_snapshot,
        )

        self.assertFalse(report["ok"])
        self.assertIn("build_binary_not_executable", blocker_codes(report))

    def test_rejects_tampered_member_even_with_success_claims(self) -> None:
        tampered = rewrite_bundle(
            self.bundle,
            replacements={self.metadata.artifact_member: b"attacker-controlled-output"},
        )

        report = verify_pre_live_artifact_bundle(
            tampered,
            caller_claims={"ok": True, "status": "success"},
        )

        self.assertFalse(report["ok"])
        self.assertTrue(report["caller_claims_ignored"])
        self.assertIn("member_digest_mismatch", blocker_codes(report))

    def test_rejects_tampered_manifest_digest(self) -> None:
        def mutate(manifest: dict[str, object]) -> None:
            descriptor = member_descriptor(
                manifest,
                self.metadata.artifact_member,
            )
            descriptor["sha256"] = "0" * 64

        report = verify_pre_live_artifact_bundle(
            mutate_manifest(self.bundle, mutate),
        )

        self.assertFalse(report["ok"])
        self.assertIn("role_digest_mismatch", blocker_codes(report))

    def test_rejects_unbound_artifact_build_and_producer_roles(self) -> None:
        cases = {
            "artifact": self.unbound_artifact_manifest,
            "build": self.unbound_build_manifest,
            "producer": self.unbound_producer_manifest,
        }
        for name, mutation in cases.items():
            with self.subTest(name=name):
                report = verify_pre_live_artifact_bundle(
                    mutate_manifest(self.bundle, mutation),
                )
                self.assertFalse(report["ok"])
                self.assertTrue(
                    {
                        "artifact_output_binding_mismatch",
                        "duplicate_role_member",
                        "build_report_binary_digest_mismatch",
                    }
                    & blocker_codes(report),
                    report["blockers"],
                )

    def unbound_artifact_manifest(self, manifest: dict[str, object]) -> None:
        descriptor = member_descriptor(
            manifest,
            self.metadata.binary_member,
        )
        artifact = manifest["artifact"]
        artifact["member"] = self.metadata.binary_member
        artifact["sha256"] = descriptor["sha256"]
        artifact["size_bytes"] = descriptor["size_bytes"]

    def unbound_build_manifest(self, manifest: dict[str, object]) -> None:
        descriptor = member_descriptor(
            manifest,
            self.metadata.producer_executable_member,
        )
        build = manifest["build"]
        build["binary_member"] = self.metadata.producer_executable_member
        build["binary_sha256"] = descriptor["sha256"]

    def unbound_producer_manifest(self, manifest: dict[str, object]) -> None:
        descriptor = member_descriptor(
            manifest,
            self.metadata.producer_executable_member,
        )
        producer = manifest["producer"]
        producer["policy_member"] = self.metadata.producer_executable_member
        producer["policy_sha256"] = descriptor["sha256"]

    def test_rejects_semantically_rebound_build_report_and_binary(self) -> None:
        entries = read_entries(self.bundle)
        report = json.loads(entries[self.metadata.build_report_member])
        report["identity"] = "sha256:" + ("d" * 64)
        replacement_report = canonical_json_bytes(report)

        def mutate_identity(manifest: dict[str, object]) -> None:
            rebind_member(
                manifest,
                self.metadata.build_report_member,
                replacement_report,
                role_path=("build", "report_sha256"),
            )

        identity_bundle = mutate_manifest(
            self.bundle,
            mutate_identity,
            replacements={self.metadata.build_report_member: replacement_report},
        )
        identity_result = verify_pre_live_artifact_bundle(identity_bundle)
        self.assertFalse(identity_result["ok"])
        self.assertIn(
            "build_report_identity_mismatch",
            blocker_codes(identity_result),
        )

        replacement_binary = b"different-production-binary"

        def mutate_binary(manifest: dict[str, object]) -> None:
            rebind_member(
                manifest,
                self.metadata.binary_member,
                replacement_binary,
                role_path=("build", "binary_sha256"),
            )

        binary_bundle = mutate_manifest(
            self.bundle,
            mutate_binary,
            replacements={self.metadata.binary_member: replacement_binary},
        )
        binary_result = verify_pre_live_artifact_bundle(binary_bundle)
        self.assertFalse(binary_result["ok"])
        self.assertIn(
            "build_report_binary_digest_mismatch",
            blocker_codes(binary_result),
        )

    def test_rejects_failed_schema_71_build_report(self) -> None:
        report = json.loads(self.members[self.metadata.build_report_member])
        report["ok"] = False
        report["failures"] = ["fixture failure"]
        replacement = canonical_json_bytes(report)

        def mutate(manifest: dict[str, object]) -> None:
            rebind_member(
                manifest,
                self.metadata.build_report_member,
                replacement,
                role_path=("build", "report_sha256"),
            )

        result = verify_pre_live_artifact_bundle(
            mutate_manifest(
                self.bundle,
                mutate,
                replacements={self.metadata.build_report_member: replacement},
            )
        )

        self.assertFalse(result["ok"])
        self.assertIn("build_report_failed", blocker_codes(result))

    def test_rejects_semantically_rebound_repository_input(self) -> None:
        replacement = canonical_json_bytes({"files": []})

        def mutate(manifest: dict[str, object]) -> None:
            rebind_member(
                manifest,
                self.metadata.repository_input_member,
                replacement,
                role_path=("build", "repository_input_sha256"),
            )

        report = verify_pre_live_artifact_bundle(
            mutate_manifest(
                self.bundle,
                mutate,
                replacements={self.metadata.repository_input_member: replacement},
            )
        )

        self.assertFalse(report["ok"])
        self.assertTrue(
            {
                "invalid_manifest_field",
                "schema_fields_mismatch",
                "schema_value_mismatch",
            }
            & blocker_codes(report),
            report["blockers"],
        )

    def test_rejects_semantically_rebound_producer_provenance(self) -> None:
        provenance = json.loads(self.members[self.metadata.producer_provenance_member])
        provenance["output_sha256"] = "0" * 64
        replacement = canonical_json_bytes(provenance)

        def mutate(manifest: dict[str, object]) -> None:
            rebind_member(
                manifest,
                self.metadata.producer_provenance_member,
                replacement,
                role_path=("producer", "provenance_sha256"),
            )

        report = verify_pre_live_artifact_bundle(
            mutate_manifest(
                self.bundle,
                mutate,
                replacements={self.metadata.producer_provenance_member: replacement},
            )
        )

        self.assertFalse(report["ok"])
        self.assertIn("schema_value_mismatch", blocker_codes(report))

    def test_rejects_semantically_rebound_ctest_evidence(self) -> None:
        for name, change in (
            ("aggregate count", lambda evidence: evidence.update({"passed": 4})),
            (
                "direct failure",
                lambda evidence: evidence["test_executables"][
                    "voi_runtime_convergence"
                ].update({"returncode": 7}),
            ),
            (
                "binary changed",
                lambda evidence: evidence["test_executables"][
                    "voi_runtime_convergence"
                ].update({"sha256_after": "0" * 64}),
            ),
        ):
            with self.subTest(name=name):
                ctest = json.loads(self.members[self.metadata.ctest_member])
                change(ctest)
                replacement = canonical_json_bytes(ctest)

                def mutate(manifest: dict[str, object]) -> None:
                    rebind_member(
                        manifest,
                        self.metadata.ctest_member,
                        replacement,
                        role_path=("build", "ctest_sha256"),
                    )

                report = verify_pre_live_artifact_bundle(
                    mutate_manifest(
                        self.bundle,
                        mutate,
                        replacements={self.metadata.ctest_member: replacement},
                    )
                )

                self.assertFalse(report["ok"])
                self.assertIn(
                    "schema_value_mismatch",
                    blocker_codes(report),
                )

    def test_rejects_duplicate_names(self) -> None:
        entries = read_entries(self.bundle)
        output = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(output, mode="w") as archive:
                for name, payload in entries.items():
                    write_entry(archive, name, payload)
                write_entry(
                    archive,
                    self.metadata.artifact_member,
                    entries[self.metadata.artifact_member],
                )

        report = verify_pre_live_artifact_bundle(output.getvalue())

        self.assertFalse(report["ok"])
        self.assertIn("duplicate_entry_name", blocker_codes(report))

    def test_rejects_absolute_traversal_and_backslash_paths(self) -> None:
        for dangerous in (
            "/absolute.txt",
            "../traversal.txt",
            "nested/../../escape.txt",
            "C:/drive.txt",
            "windows\\escape.txt",
        ):
            with self.subTest(dangerous=dangerous):
                report = verify_pre_live_artifact_bundle(
                    rewrite_bundle(
                        self.bundle,
                        additions={dangerous: b"malicious"},
                    )
                )
                self.assertFalse(report["ok"])
                self.assertIn("unsafe_entry_path", blocker_codes(report))

    def test_rejects_symlinks_and_special_entries(self) -> None:
        for file_type in (
            stat.S_IFLNK,
            stat.S_IFIFO,
            stat.S_IFCHR,
            stat.S_IFBLK,
            stat.S_IFSOCK,
        ):
            with self.subTest(file_type=file_type):
                report = verify_pre_live_artifact_bundle(
                    rewrite_bundle(
                        self.bundle,
                        special_entry=(
                            "unsafe-entry",
                            b"payload",
                            file_type | 0o777,
                        ),
                    )
                )
                self.assertFalse(report["ok"])
                self.assertIn(
                    "unsupported_entry_type",
                    blocker_codes(report),
                )

    def test_rejects_encrypted_flag_before_decompression(self) -> None:
        report = verify_pre_live_artifact_bundle(
            set_encrypted_flags(self.bundle),
        )

        self.assertFalse(report["ok"])
        self.assertIn("encrypted_entry", blocker_codes(report))

    def test_rejects_hidden_framing_and_local_header_disagreement(self) -> None:
        for name, malformed in (
            ("prefix", b"hidden-prefix" + self.bundle),
            ("suffix", self.bundle + b"hidden-suffix"),
        ):
            with self.subTest(name=name):
                report = verify_pre_live_artifact_bundle(malformed)
                self.assertFalse(report["ok"])
                self.assertIn(
                    "noncanonical_zip_framing",
                    blocker_codes(report),
                )

        local_only = verify_pre_live_artifact_bundle(
            set_encrypted_flags(self.bundle, central=False),
        )
        self.assertFalse(local_only["ok"])
        self.assertIn("local_header_mismatch", blocker_codes(local_only))

    def test_rejects_eocd_entry_count_disagreement(self) -> None:
        malformed = bytearray(self.bundle)
        eocd_offset = malformed.rfind(b"PK\x05\x06")
        self.assertGreaterEqual(eocd_offset, 0)
        struct.pack_into("<HH", malformed, eocd_offset + 8, 0, 0)

        report = verify_pre_live_artifact_bundle(bytes(malformed))

        self.assertFalse(report["ok"])
        self.assertIn("noncanonical_zip_framing", blocker_codes(report))

    def test_rejects_noncanonical_zip_encoding_of_valid_content(self) -> None:
        deflated = raw_zip(
            read_entries(self.bundle),
            compression=zipfile.ZIP_DEFLATED,
        )

        report = verify_pre_live_artifact_bundle(deflated)

        self.assertFalse(report["ok"])
        self.assertIn("noncanonical_zip_metadata", blocker_codes(report))

    def test_rejects_decompression_bombs_and_hard_limits(self) -> None:
        bomb = raw_zip(
            {
                PRE_LIVE_ARTIFACT_MANIFEST_NAME: b"A" * 64_000,
            },
            compression=zipfile.ZIP_DEFLATED,
        )
        ratio_result = verify_pre_live_artifact_bundle(
            bomb,
            limits=replace(
                PreLiveArtifactLimits(),
                max_compression_ratio=2,
            ),
        )
        archive_result = verify_pre_live_artifact_bundle(
            self.bundle,
            limits=replace(
                PreLiveArtifactLimits(),
                max_archive_bytes=len(self.bundle) - 1,
            ),
        )
        member_result = verify_pre_live_artifact_bundle(
            self.bundle,
            limits=PreLiveArtifactLimits(
                max_archive_bytes=len(self.bundle) + 1,
                max_manifest_bytes=512,
                max_entries=128,
                max_member_compressed_bytes=512,
                max_member_uncompressed_bytes=512,
                max_total_uncompressed_bytes=4096,
                max_compression_ratio=200,
            ),
        )

        self.assertFalse(ratio_result["ok"])
        self.assertIn(
            "compression_ratio_limit_exceeded",
            blocker_codes(ratio_result),
        )
        self.assertFalse(archive_result["ok"])
        self.assertIn(
            "archive_size_limit_exceeded",
            blocker_codes(archive_result),
        )
        self.assertFalse(member_result["ok"])
        self.assertTrue(
            {
                "compressed_size_limit_exceeded",
                "uncompressed_size_limit_exceeded",
                "manifest_size_limit_exceeded",
            }
            & blocker_codes(member_result)
        )

    def test_rejects_wrong_schema_ids_commit_and_digests(self) -> None:
        def set_schema(manifest: dict[str, object]) -> None:
            manifest["schema_version"] = PRE_LIVE_ARTIFACT_SCHEMA_VERSION + 1

        def set_repository_id(manifest: dict[str, object]) -> None:
            manifest["repository"]["database_id"] = True

        def set_commit(manifest: dict[str, object]) -> None:
            manifest["repository"]["commit_sha"] = COMMIT.upper()

        def set_workflow_sha(manifest: dict[str, object]) -> None:
            manifest["workflow"]["sha"] = "E" * 40

        def set_workflow_ref(manifest: dict[str, object]) -> None:
            manifest["workflow"]["ref"] = (
                f"attacker/example/{WORKFLOW_PATH}"
                "@refs/heads/issue-138-authenticated-prelive-provenance"
            )

        def set_run_attempt(manifest: dict[str, object]) -> None:
            manifest["run"]["attempt"] = 0

        def set_job_id(manifest: dict[str, object]) -> None:
            manifest["job"]["id"] = "20001"

        def set_digest(manifest: dict[str, object]) -> None:
            manifest["producer"]["argv_sha256"] = "F" * 64

        for name, mutation in (
            ("schema", set_schema),
            ("repository_id", set_repository_id),
            ("commit", set_commit),
            ("workflow_ref", set_workflow_ref),
            ("workflow_sha", set_workflow_sha),
            ("run_attempt", set_run_attempt),
            ("job_id", set_job_id),
            ("digest", set_digest),
        ):
            with self.subTest(name=name):
                report = verify_pre_live_artifact_bundle(
                    mutate_manifest(self.bundle, mutation),
                )
                self.assertFalse(report["ok"])
                self.assertTrue(report["blockers"])

    def test_rejects_missing_extra_and_invalid_candidate_authority(self) -> None:
        def missing(manifest: dict[str, object]) -> None:
            manifest.pop("authority")
            manifest["schema_version"] = 1

        def extra(manifest: dict[str, object]) -> None:
            manifest["authority"]["unexpected"] = True

        def wrong_scope(manifest: dict[str, object]) -> None:
            manifest["authority"]["scope"] = "release_post_merge"

        def promoted(manifest: dict[str, object]) -> None:
            manifest["authority"]["release_authoritative"] = True

        def wrong_event(manifest: dict[str, object]) -> None:
            manifest["authority"]["event"] = "push"

        def wrong_head(manifest: dict[str, object]) -> None:
            manifest["authority"]["pull_request"]["head_sha"] = "e" * 40

        for name, mutation in (
            ("legacy_missing", missing),
            ("extra_key", extra),
            ("wrong_scope", wrong_scope),
            ("promoted", promoted),
            ("wrong_event", wrong_event),
            ("wrong_head", wrong_head),
        ):
            with self.subTest(name=name):
                report = verify_pre_live_artifact_bundle(
                    mutate_manifest(self.bundle, mutation),
                )
                self.assertFalse(report["ok"], report)
                self.assertTrue(report["blockers"])

    def test_rejects_coherently_rebuilt_candidate_promoted_to_release(self) -> None:
        entries = read_entries(self.bundle)
        provenance = json.loads(entries[self.metadata.producer_provenance_member])
        provenance["authority"]["scope"] = "release_post_merge"
        provenance["authority"]["release_authoritative"] = True
        replacement = canonical_json_bytes(provenance)

        def promote(manifest: dict[str, object]) -> None:
            manifest["authority"]["scope"] = "release_post_merge"
            manifest["authority"]["release_authoritative"] = True
            rebind_member(
                manifest,
                self.metadata.producer_provenance_member,
                replacement,
                role_path=("producer", "provenance_sha256"),
            )

        report = verify_pre_live_artifact_bundle(
            mutate_manifest(
                self.bundle,
                promote,
                replacements={self.metadata.producer_provenance_member: replacement},
            )
        )

        self.assertFalse(report["ok"], report)
        self.assertIn("schema_value_mismatch", blocker_codes(report))

    def test_rejects_noncanonical_and_duplicate_key_manifest_json(self) -> None:
        entries = read_entries(self.bundle)
        manifest = json.loads(entries[PRE_LIVE_ARTIFACT_MANIFEST_NAME])
        pretty = json.dumps(
            manifest,
            indent=2,
            sort_keys=True,
        ).encode()
        noncanonical = rewrite_bundle(
            self.bundle,
            replacements={PRE_LIVE_ARTIFACT_MANIFEST_NAME: pretty},
        )

        duplicate_key = b'{"schema":"forged",' + canonical_json_bytes(manifest)[1:]
        duplicate = rewrite_bundle(
            self.bundle,
            replacements={PRE_LIVE_ARTIFACT_MANIFEST_NAME: duplicate_key},
        )

        noncanonical_result = verify_pre_live_artifact_bundle(noncanonical)
        duplicate_result = verify_pre_live_artifact_bundle(duplicate)
        self.assertFalse(noncanonical_result["ok"])
        self.assertIn(
            "noncanonical_manifest_json",
            blocker_codes(noncanonical_result),
        )
        self.assertFalse(duplicate_result["ok"])
        self.assertIn("duplicate_json_key", blocker_codes(duplicate_result))

    def test_rejects_extra_missing_and_status_claim_entries(self) -> None:
        extra = rewrite_bundle(
            self.bundle,
            additions={"undeclared.txt": b"not in manifest"},
        )
        missing = rewrite_bundle(
            self.bundle,
            removals={self.metadata.producer_provenance_member},
        )

        def add_status(manifest: dict[str, object]) -> None:
            manifest["ok"] = True
            manifest["status"] = "success"
            manifest["conclusion"] = "success"

        status = mutate_manifest(self.bundle, add_status)

        extra_result = verify_pre_live_artifact_bundle(extra)
        missing_result = verify_pre_live_artifact_bundle(missing)
        status_result = verify_pre_live_artifact_bundle(status)
        self.assertFalse(extra_result["ok"])
        self.assertIn("unexpected_entry", blocker_codes(extra_result))
        self.assertFalse(missing_result["ok"])
        self.assertIn("missing_entry", blocker_codes(missing_result))
        self.assertFalse(status_result["ok"])
        self.assertIn("schema_fields_mismatch", blocker_codes(status_result))

    def test_rejects_invalid_build_report_schema(self) -> None:
        entries = read_entries(self.bundle)
        report = json.loads(entries[self.metadata.build_report_member])
        report["schema_version"] = MICROMACHINE_BUILD_IDENTITY_SCHEMA_VERSION - 1
        replacement_report = canonical_json_bytes(report)

        def mutate(manifest: dict[str, object]) -> None:
            rebind_member(
                manifest,
                self.metadata.build_report_member,
                replacement_report,
                role_path=("build", "report_sha256"),
            )

        result = verify_pre_live_artifact_bundle(
            mutate_manifest(
                self.bundle,
                mutate,
                replacements={self.metadata.build_report_member: replacement_report},
            )
        )

        self.assertFalse(result["ok"])
        self.assertIn(
            "build_report_schema_mismatch",
            blocker_codes(result),
        )

    def test_builder_rejects_invalid_metadata_missing_roles_and_limits(self) -> None:
        invalid_workflow_refs = (
            "refs/heads/issue-138-authenticated-prelive-provenance",
            (
                f"attacker/example/{WORKFLOW_PATH}"
                "@refs/heads/issue-138-authenticated-prelive-provenance"
            ),
            f"{REPOSITORY}/{WORKFLOW_PATH}@refs/",
        )
        for workflow_ref in invalid_workflow_refs:
            with self.subTest(workflow_ref=workflow_ref):
                with self.assertRaisesRegex(
                    ValueError,
                    "workflow_ref_binding_mismatch",
                ):
                    build_pre_live_artifact_bundle(
                        replace(
                            self.metadata,
                            workflow_ref=workflow_ref,
                        ),
                        self.members,
                        admission_snapshot=self.admission_snapshot,
                    )
        with self.assertRaisesRegex(ValueError, "invalid_manifest_field"):
            build_pre_live_artifact_bundle(
                replace(self.metadata, workflow_sha="F" * 40),
                self.members,
                admission_snapshot=self.admission_snapshot,
            )
        with self.assertRaisesRegex(ValueError, "role members are missing"):
            build_pre_live_artifact_bundle(
                self.metadata,
                {
                    name: payload
                    for name, payload in self.members.items()
                    if name != self.metadata.producer_policy_member
                },
                admission_snapshot=self.admission_snapshot,
            )
        nested = as_nested_metadata(self.metadata)
        nested["repository"]["ok"] = True
        with self.assertRaisesRegex(ValueError, "fields mismatch"):
            build_pre_live_artifact_bundle(
                nested,
                self.members,
                admission_snapshot=self.admission_snapshot,
            )
        with self.assertRaisesRegex(ValueError, "max_member_uncompressed_bytes"):
            build_pre_live_artifact_bundle(
                self.metadata,
                self.members,
                limits=PreLiveArtifactLimits(
                    max_archive_bytes=4096,
                    max_manifest_bytes=64,
                    max_entries=128,
                    max_member_compressed_bytes=64,
                    max_member_uncompressed_bytes=64,
                    max_total_uncompressed_bytes=1024,
                    max_compression_ratio=200,
                ),
                admission_snapshot=self.admission_snapshot,
            )

    def test_rejects_malformed_zip(self) -> None:
        report = verify_pre_live_artifact_bundle(b"not-a-zip")

        self.assertFalse(report["ok"])
        self.assertIn("invalid_zip", blocker_codes(report))

    def test_verifies_github_one_file_artifact_wrapper(self) -> None:
        wrapper = io.BytesIO()
        with zipfile.ZipFile(
            wrapper,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            archive.writestr(
                GITHUB_ARTIFACT_BUNDLE_MEMBER_NAME,
                self.bundle,
            )

        report = verify_downloaded_pre_live_artifact(wrapper.getvalue())

        self.assertTrue(report["ok"], report["blockers"])
        self.assertEqual("github_artifact_zip", report["delivery"]["kind"])
        self.assertEqual(
            sha256(self.bundle),
            report["delivery"]["bundle_sha256"],
        )

    def test_rejects_ambiguous_or_wrong_github_artifact_wrapper(self) -> None:
        for name, entries in {
            "extra member": {
                GITHUB_ARTIFACT_BUNDLE_MEMBER_NAME: self.bundle,
                "attacker.txt": b"extra",
            },
            "wrong member": {"other.zip": self.bundle},
        }.items():
            with self.subTest(name=name):
                wrapper = io.BytesIO()
                with zipfile.ZipFile(wrapper, mode="w") as archive:
                    for member, payload in entries.items():
                        archive.writestr(member, payload)

                report = verify_downloaded_pre_live_artifact(wrapper.getvalue())

                self.assertFalse(report["ok"], report)
                self.assertEqual("invalid", report["delivery"]["kind"])


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def as_nested_metadata(
    metadata: PreLiveArtifactMetadata,
) -> dict[str, object]:
    return {
        "authority": {
            "scope": metadata.authority_scope,
            "release_authoritative": metadata.release_authoritative,
            "event": metadata.authority_event,
            "pull_request": {
                "database_id": metadata.pull_request_database_id,
                "number": metadata.pull_request_number,
                "head_sha": metadata.pull_request_head_sha,
                "head_ref": metadata.pull_request_head_ref,
                "head_repository_id": (metadata.pull_request_head_repository_id),
            },
        },
        "repository": {
            "full_name": metadata.repository_full_name,
            "database_id": metadata.repository_database_id,
            "commit_sha": metadata.repository_commit,
        },
        "workflow": {
            "id": metadata.workflow_id,
            "path": metadata.workflow_path,
            "ref": metadata.workflow_ref,
            "sha": metadata.workflow_sha,
        },
        "run": {
            "id": metadata.run_id,
            "attempt": metadata.run_attempt,
        },
        "job": {
            "id": metadata.job_id,
            "name": metadata.job_name,
        },
        "artifact": {
            "logical_name": metadata.artifact_logical_name,
            "member": metadata.artifact_member,
        },
        "build": {
            "report_identity": metadata.build_report_identity,
            "report_member": metadata.build_report_member,
            "binary_member": metadata.binary_member,
            "repository_input_member": metadata.repository_input_member,
            "repository_input_identity": metadata.repository_input_identity,
            "ctest_member": metadata.ctest_member,
        },
        "producer": {
            "policy_id": metadata.producer_policy_id,
            "policy_member": metadata.producer_policy_member,
            "executable_member": metadata.producer_executable_member,
            "argv_member": metadata.producer_argv_member,
            "output_member": metadata.producer_output_member,
            "provenance_member": metadata.producer_provenance_member,
        },
    }


def blocker_codes(report: dict[str, object]) -> set[str]:
    return {blocker["code"] for blocker in report["blockers"]}


def make_ctest_evidence(build_dir: str) -> dict[str, object]:
    executable_names = {
        "voi_operation_transfer_admission": "voi_operation_transfer_admission_test",
        "voi_runtime_convergence": "voi_runtime_convergence_test",
        "voi_family_effect_lifecycle": "voi_family_effect_lifecycle_test",
        "voi_battlefield_projection": "voi_battlefield_projection_test",
        "voi_battlefield_projection_ndebug": "voi_battlefield_projection_ndebug_test",
    }
    test_executables = {
        name: {
            "path": f"{build_dir}/bin/{executable}",
            "sha256": sha256(f"{name}-binary".encode()),
            "sha256_after": sha256(f"{name}-binary".encode()),
            "argv": [f"{build_dir}/bin/{executable}"],
            "returncode": 0,
            "stdout_sha256": sha256(b""),
            "stderr_sha256": sha256(b""),
        }
        for name, executable in sorted(executable_names.items())
    }
    return {
        "schema_version": 1,
        "argv": [
            "/usr/bin/ctest",
            "--test-dir",
            build_dir,
            "--output-on-failure",
        ],
        "discovery_argv": [
            "/usr/bin/ctest",
            "--test-dir",
            build_dir,
            "--show-only=json-v1",
        ],
        "ctest_executable": "/usr/bin/ctest",
        "ctest_executable_sha256": sha256(b"ctest executable"),
        "returncode": 0,
        "passed": 5,
        "total": 5,
        "failures": 0,
        "test_names": sorted(executable_names),
        "test_executables": test_executables,
        "test_manifest_sha256": (
            "sha256:" + sha256(canonical_json_bytes(test_executables))
        ),
        "stdout_sha256": sha256(b"100% tests passed, 0 tests failed out of 5\n"),
        "stderr_sha256": sha256(b""),
    }


def read_entries(bundle: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        return {info.filename: archive.read(info) for info in archive.infolist()}


def write_entry(
    archive: zipfile.ZipFile,
    name: str,
    payload: bytes,
    *,
    mode: int = stat.S_IFREG | 0o644,
    compression: int = zipfile.ZIP_STORED,
) -> None:
    info = zipfile.ZipInfo(
        name,
        date_time=DETERMINISTIC_ZIP_TIMESTAMP,
    )
    info.create_system = 3
    info.create_version = 20
    info.extract_version = 20
    info.external_attr = mode << 16
    info.compress_type = compression
    info.extra = b""
    info.comment = b""
    archive.writestr(info, payload)


def raw_zip(
    entries: dict[str, bytes],
    *,
    compression: int = zipfile.ZIP_STORED,
) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w") as archive:
        for name, payload in entries.items():
            write_entry(
                archive,
                name,
                payload,
                compression=compression,
            )
    return output.getvalue()


def rewrite_bundle(
    bundle: bytes,
    *,
    replacements: dict[str, bytes] | None = None,
    additions: dict[str, bytes] | None = None,
    removals: set[str] | None = None,
    special_entry: tuple[str, bytes, int] | None = None,
    mode_replacements: dict[str, int] | None = None,
) -> bytes:
    replacements = replacements or {}
    additions = additions or {}
    removals = removals or set()
    mode_replacements = mode_replacements or {}
    entries = read_entries(bundle)
    with zipfile.ZipFile(io.BytesIO(bundle)) as source:
        modes = {
            info.filename: (info.external_attr >> 16) & 0xFFFF
            for info in source.infolist()
        }
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w") as archive:
        for name, payload in entries.items():
            if name in removals:
                continue
            write_entry(
                archive,
                name,
                replacements.get(name, payload),
                mode=mode_replacements.get(name, modes[name]),
            )
        for name, payload in additions.items():
            write_entry(archive, name, payload)
        if special_entry is not None:
            name, payload, mode = special_entry
            write_entry(archive, name, payload, mode=mode)
    return output.getvalue()


def mutate_manifest(
    bundle: bytes,
    mutation: object,
    *,
    replacements: dict[str, bytes] | None = None,
) -> bytes:
    entries = read_entries(bundle)
    manifest = json.loads(entries[PRE_LIVE_ARTIFACT_MANIFEST_NAME])
    mutation(manifest)
    all_replacements = dict(replacements or {})
    all_replacements[PRE_LIVE_ARTIFACT_MANIFEST_NAME] = canonical_json_bytes(manifest)
    return rewrite_bundle(bundle, replacements=all_replacements)


def member_descriptor(
    manifest: dict[str, object],
    name: str,
) -> dict[str, object]:
    return next(item for item in manifest["members"] if item["name"] == name)


def rebind_member(
    manifest: dict[str, object],
    name: str,
    payload: bytes,
    *,
    role_path: tuple[str, str],
) -> None:
    digest = sha256(payload)
    descriptor = member_descriptor(manifest, name)
    descriptor["sha256"] = digest
    descriptor["size_bytes"] = len(payload)
    section, digest_key = role_path
    manifest[section][digest_key] = digest


def set_encrypted_flags(
    bundle: bytes,
    *,
    central: bool = True,
) -> bytes:
    mutated = bytearray(bundle)
    headers = [(b"PK\x03\x04", 6)]
    if central:
        headers.append((b"PK\x01\x02", 8))
    for signature, flag_offset in headers:
        cursor = 0
        while True:
            index = mutated.find(signature, cursor)
            if index < 0:
                break
            flags = struct.unpack_from("<H", mutated, index + flag_offset)[0]
            struct.pack_into(
                "<H",
                mutated,
                index + flag_offset,
                flags | 0x1,
            )
            cursor = index + len(signature)
    return bytes(mutated)
