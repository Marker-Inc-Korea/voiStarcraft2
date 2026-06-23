"""Tests for reproducible MicroMachine build identity reports."""

import json
import tempfile
import unittest
from pathlib import Path

from starcraft_commander.micromachine_build_identity import (
    MicroMachineBuildIdentityConfig,
    build_micromachine_build_identity,
    read_build_identity,
    write_build_identity_report,
)


class MicroMachineBuildIdentityTest(unittest.TestCase):
    def test_expected_build_identity_is_stable_and_json_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)

            report = build_micromachine_build_identity(config)
            output = root / "identity.json"
            write_build_identity_report(report, output)

            self.assertTrue(report["ok"], report)
            self.assertEqual(1, report["schema_version"])
            self.assertTrue(str(report["identity"]).startswith("sha256:"))
            self.assertEqual(report["identity"], read_build_identity(output))
            self.assertEqual(
                report["checksums"]["micromachine_patch_sha256"],
                build_micromachine_build_identity(config)["checksums"]["micromachine_patch_sha256"],
            )
            json.dumps(report)

    def test_missing_binary_marks_build_identity_not_ok(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=False)

            report = build_micromachine_build_identity(config)
            output = root / "identity.json"
            write_build_identity_report(report, output)

            self.assertFalse(report["ok"])
            self.assertEqual(report["identity"], read_build_identity(output))
            self.assertIn(
                "missing_binary",
                {failure["code"] for failure in report["failures"]},
            )

    def test_patch_checksum_changes_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.build_config(root, binary=True)
            first = build_micromachine_build_identity(config)
            config.micromachine_patch.write_text("different patch\n")

            second = build_micromachine_build_identity(config)

            self.assertNotEqual(first["identity"], second["identity"])
            self.assertNotEqual(
                first["checksums"]["micromachine_patch_sha256"],
                second["checksums"]["micromachine_patch_sha256"],
            )

    def build_config(
        self,
        root: Path,
        *,
        binary: bool,
    ) -> MicroMachineBuildIdentityConfig:
        micromachine_dir = root / "MicroMachine"
        s2client_dir = root / "s2client-api"
        build_dir = micromachine_dir / "build"
        binary_path = build_dir / "bin" / "MicroMachine"
        binary_path.parent.mkdir(parents=True)
        micromachine_dir.mkdir(exist_ok=True)
        s2client_dir.mkdir()
        if binary:
            binary_path.write_text("fake binary\n")
        micromachine_patch = root / "micromachine.patch"
        s2client_patch = root / "s2client.patch"
        hook_manifest = root / "HOOK_MANIFEST.json"
        map_pool = root / "MICROMACHINE_MAP_POOL.json"
        blackboard_header = root / "voi_policy_blackboard.hpp"
        for path in (
            micromachine_patch,
            s2client_patch,
            hook_manifest,
            map_pool,
            blackboard_header,
        ):
            path.write_text(f"{path.name}\n")
        return MicroMachineBuildIdentityConfig(
            micromachine_dir=micromachine_dir,
            s2client_dir=s2client_dir,
            micromachine_build_dir=build_dir,
            micromachine_patch=micromachine_patch,
            s2client_patch=s2client_patch,
            hook_manifest=hook_manifest,
            map_pool=map_pool,
            blackboard_header=blackboard_header,
        )


if __name__ == "__main__":
    unittest.main()
