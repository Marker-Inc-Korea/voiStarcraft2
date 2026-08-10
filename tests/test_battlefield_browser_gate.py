from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import starcraft_commander.battlefield_browser_gate as browser_gate
from starcraft_commander.battlefield_browser_gate import (
    BrowserGateConfig,
    PIXEL_CHANNEL_TOLERANCE,
    VISUAL_DIFF_THRESHOLD,
    _CandidateFixtureProcess,
    _FIXTURE_BOOTSTRAP,
    _BrowserFixtureBridge,
    _BrowserFixtureLauncher,
    _pixel_diff,
    _status_payload,
    _write_rgba_png,
)


REPOSITORY_SHA = "a" * 40
BUILD_IDENTITY = "sha256:" + "b" * 64


class BattlefieldBrowserGateContractTest(unittest.TestCase):
    def test_config_requires_exact_repository_and_build_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            BrowserGateConfig(
                repository_sha=REPOSITORY_SHA,
                build_identity=BUILD_IDENTITY,
                artifact_dir=root,
            )
            for repository_sha, build_identity in (
                ("A" * 40, BUILD_IDENTITY),
                ("a" * 39, BUILD_IDENTITY),
                (REPOSITORY_SHA, "b" * 64),
                (REPOSITORY_SHA, "sha256:" + "G" * 64),
            ):
                with self.subTest(
                    repository_sha=repository_sha,
                    build_identity=build_identity,
                ):
                    with self.assertRaises(ValueError):
                        BrowserGateConfig(
                            repository_sha=repository_sha,
                            build_identity=build_identity,
                            artifact_dir=root,
                        )

    def test_config_rejects_missing_or_non_executable_browser_override(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "missing-chromium"
            non_executable = root / "chromium"
            non_executable.write_text("#!/bin/sh\n")

            for executable in (missing, non_executable):
                with self.subTest(executable=executable):
                    with self.assertRaises(ValueError):
                        BrowserGateConfig(
                            repository_sha=REPOSITORY_SHA,
                            build_identity=BUILD_IDENTITY,
                            artifact_dir=root,
                            chromium_executable=executable,
                        )

    def test_candidate_fixture_command_is_an_isolated_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = BrowserGateConfig(
                repository_sha=REPOSITORY_SHA,
                build_identity=BUILD_IDENTITY,
                artifact_dir=Path(directory),
            )

            command = _CandidateFixtureProcess(config)._command()

            self.assertEqual(str(config.candidate_python), command[0])
            self.assertEqual(["-I", "-B", "-c"], command[1:4])
            self.assertEqual(_FIXTURE_BOOTSTRAP, command[4])
            self.assertEqual(str(config.candidate_root), command[5])
            self.assertEqual(
                str(Path(browser_gate.__file__).resolve()),
                command[6],
            )

    def test_candidate_fixture_dedicated_identity_uses_numeric_sudo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = BrowserGateConfig(
                repository_sha=REPOSITORY_SHA,
                build_identity=BUILD_IDENTITY,
                artifact_dir=Path(directory),
                candidate_uid=65001,
                candidate_gid=65001,
            )

            command = _CandidateFixtureProcess(config)._command()

            self.assertEqual("/usr/bin/sudo", command[0])
            self.assertIn("--user=#65001", command)
            self.assertIn("--group=#65001", command)
            self.assertIn("--", command)

    def test_release_cli_cannot_update_tracked_baselines(self) -> None:
        parser = browser_gate.build_argument_parser()
        option_strings = {
            option
            for action in parser._actions
            for option in action.option_strings
        }

        self.assertNotIn("--update-baselines", option_strings)

    def test_fixture_status_preserves_four_lane_inputs(self) -> None:
        bridge = _BrowserFixtureBridge()
        payload = bridge.micromachine_status()
        operations = payload["operations"]

        self.assertEqual(4, len(operations))
        self.assertEqual(
            {
                "planning-alpha",
                "assault-bravo",
                "completed-charlie",
                "waiting-delta",
            },
            {operation["operation_id"] for operation in operations},
        )
        self.assertEqual(
            "published",
            operations[0]["transport_status"],
        )
        self.assertEqual(
            "queued_or_assigned",
            operations[0]["intervention"]["command_execution"]["state"],
        )
        self.assertEqual(
            "completed",
            operations[2]["battlefield_operation"]["operation_completion"][
                "state"
            ],
        )
        self.assertEqual(
            "composition_prerequisites_pending",
            operations[3]["battlefield_operation"]["operation_launch_policy"][
                "blocker"
            ],
        )

    def test_voice_fixture_adds_independent_operation_identities(self) -> None:
        bridge = _BrowserFixtureBridge()
        first = bridge.submit_micromachine_modulation("first")
        second = bridge.submit_micromachine_modulation("second")
        operations = second["operations"]
        operation_ids = [operation["operation_id"] for operation in operations]

        self.assertEqual(len(operation_ids), len(set(operation_ids)))
        self.assertIn("voice-1-recon", operation_ids)
        self.assertIn("voice-1-attack", operation_ids)
        self.assertIn("voice-2-recon", operation_ids)
        self.assertIn("voice-2-attack", operation_ids)
        self.assertEqual(6, len(first["operations"]))
        self.assertEqual(8, len(second["operations"]))

    def test_status_payload_has_authoritative_overview_identity(self) -> None:
        bridge = _BrowserFixtureBridge()
        status = bridge.micromachine_status()
        rebuilt = _status_payload(status["operations"])

        self.assertTrue(rebuilt["ok"])
        self.assertTrue(rebuilt["operation_registry_authoritative"])
        self.assertEqual(
            "micromachine_cpp",
            rebuilt["battlefield_overview"]["authority"],
        )
        self.assertEqual(
            0,
            rebuilt["battlefield_overview"]["duplicate_owner_count"],
        )

    def test_runtime_fixture_exposes_public_and_validated_snapshots(self) -> None:
        launcher = _BrowserFixtureLauncher("/tmp/browser-fixture")

        public = launcher.snapshot()
        validated = launcher.validated_snapshot()

        self.assertEqual("connected", public["status"])
        self.assertEqual("/tmp/browser-fixture", public["blackboard_dir"])
        self.assertEqual(
            launcher.runtime_instance_id,
            public["runtime_instance_id"],
        )
        self.assertEqual(public, validated.metadata)
        self.assertEqual(
            launcher.runtime_instance_id,
            validated.telemetry_document["runtime_instance_id"],
        )

    def test_pixel_diff_ignores_bounded_channel_noise(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = root / "expected.png"
            actual = root / "actual.png"
            diff = root / "diff.png"
            _write_rgba_png(
                expected,
                10,
                10,
                bytes((10, 20, 30, 255)) * 100,
            )
            _write_rgba_png(
                actual,
                10,
                10,
                bytes(
                    (
                    10 + PIXEL_CHANNEL_TOLERANCE,
                    20,
                    30,
                    255,
                    )
                )
                * 100,
            )

            self.assertEqual(0.0, _pixel_diff(expected, actual, diff))
            self.assertTrue(diff.is_file())

    def test_pixel_diff_reports_changed_pixel_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = root / "expected.png"
            actual = root / "actual.png"
            diff = root / "diff.png"
            _write_rgba_png(
                expected,
                10,
                10,
                bytes((0, 0, 0, 255)) * 100,
            )
            pixels = bytearray(bytes((0, 0, 0, 255)) * 100)
            for y in range(10):
                for x in range(2):
                    offset = (y * 10 + x) * 4
                    pixels[offset : offset + 4] = b"\xff\xff\xff\xff"
            _write_rgba_png(actual, 10, 10, bytes(pixels))

            ratio = _pixel_diff(expected, actual, diff)

            self.assertAlmostEqual(0.2, ratio)
            self.assertGreater(ratio, VISUAL_DIFF_THRESHOLD)


if __name__ == "__main__":
    unittest.main()
