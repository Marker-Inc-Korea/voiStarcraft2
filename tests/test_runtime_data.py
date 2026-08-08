"""Tests for source-checkout and installed runtime-data resolution."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from starcraft_commander.runtime_data import (
    micromachine_data_path,
    source_repository_root,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TARGET_RUNTIME_PROBE = """
import importlib.util
import json
from pathlib import Path

runtime_path = Path("starcraft_commander/runtime_data.py").resolve()
spec = importlib.util.spec_from_file_location("target_runtime_data", runtime_path)
assert spec is not None and spec.loader is not None
runtime_data = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime_data)
print(json.dumps({
    "source_repository_root": (
        str(runtime_data.source_repository_root())
        if runtime_data.source_repository_root() is not None
        else None
    ),
    "manifest_exists": runtime_data.micromachine_data_path(
        "HOOK_MANIFEST.json"
    ).is_file(),
    "smoke_script_exists": runtime_data.micromachine_data_path(
        "scripts/smoke_macos_local.sh"
    ).is_file(),
}))
"""


class RuntimeDataTest(unittest.TestCase):
    def test_real_source_checkout_is_detected(self) -> None:
        self.assertEqual(REPOSITORY_ROOT, source_repository_root())
        self.assertTrue(micromachine_data_path("HOOK_MANIFEST.json").is_file())

    def test_target_like_install_inside_unrelated_git_repo_is_not_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target_root = Path(directory)
            subprocess.run(
                ["git", "init", "--quiet", str(target_root)],
                check=True,
                capture_output=True,
                text=True,
            )
            package_root = target_root / "starcraft_commander"
            resource_root = target_root / "integrations" / "micromachine"
            scripts_root = resource_root / "scripts"
            package_root.mkdir()
            scripts_root.mkdir(parents=True)
            shutil.copy2(
                REPOSITORY_ROOT / "starcraft_commander" / "runtime_data.py",
                package_root / "runtime_data.py",
            )
            shutil.copy2(
                REPOSITORY_ROOT / "integrations" / "__init__.py",
                target_root / "integrations" / "__init__.py",
            )
            shutil.copy2(
                REPOSITORY_ROOT / "integrations" / "micromachine" / "__init__.py",
                resource_root / "__init__.py",
            )
            shutil.copy2(
                REPOSITORY_ROOT
                / "integrations"
                / "micromachine"
                / "HOOK_MANIFEST.json",
                resource_root / "HOOK_MANIFEST.json",
            )
            shutil.copy2(
                REPOSITORY_ROOT
                / "integrations"
                / "micromachine"
                / "scripts"
                / "smoke_macos_local.sh",
                scripts_root / "smoke_macos_local.sh",
            )
            (target_root / "pyproject.toml").write_text(
                '[project]\nname = "unrelated-project"\n',
                encoding="utf-8",
            )
            shutil.copy2(
                REPOSITORY_ROOT / "MANIFEST.in",
                target_root / "MANIFEST.in",
            )

            environment = os.environ.copy()
            environment.pop("PYTHONPATH", None)
            completed = subprocess.run(
                [sys.executable, "-c", TARGET_RUNTIME_PROBE],
                cwd=target_root,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            (target_root / "pyproject.toml").write_text(
                '[project]\nname = "voiStarcraft2"\n',
                encoding="utf-8",
            )
            matching_metadata = subprocess.run(
                [sys.executable, "-c", TARGET_RUNTIME_PROBE],
                cwd=target_root,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )

        for probe in (completed, matching_metadata):
            with self.subTest(stdout=probe.stdout):
                self.assertEqual(0, probe.returncode, probe.stderr)
                payload = json.loads(probe.stdout)
                self.assertIsNone(payload["source_repository_root"])
                self.assertTrue(payload["manifest_exists"])
                self.assertTrue(payload["smoke_script_exists"])


if __name__ == "__main__":
    unittest.main()
