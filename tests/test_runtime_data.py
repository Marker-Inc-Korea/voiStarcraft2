"""Tests for source-checkout and installed runtime-data resolution."""

import builtins
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from starcraft_commander import runtime_data
from starcraft_commander.runtime_data import (
    micromachine_data_path,
    source_repository_root,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_REMOTE = "https://github.com/Marker-Inc-Korea/voiStarcraft2.git"
TARGET_RUNTIME_PROBE = """
import importlib.util
import json
import os
import sys
from pathlib import Path

runtime_path = Path(os.environ["TARGET_RUNTIME_DATA_PATH"])
sys.path.insert(0, str(runtime_path.parent.parent))
spec = importlib.util.spec_from_file_location("target_runtime_data", runtime_path)
assert spec is not None and spec.loader is not None
runtime_data = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime_data)
source_root = runtime_data.source_repository_root()
print(json.dumps({
    "source_repository_root": (
        str(source_root)
        if source_root is not None
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


def _run(*arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(arguments),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _copy_runtime_install(install_root: Path) -> Path:
    package_root = install_root / "starcraft_commander"
    resource_root = install_root / "integrations" / "micromachine"
    scripts_root = resource_root / "scripts"
    package_root.mkdir(parents=True)
    scripts_root.mkdir(parents=True)
    shutil.copy2(
        REPOSITORY_ROOT / "starcraft_commander" / "runtime_data.py",
        package_root / "runtime_data.py",
    )
    shutil.copy2(
        REPOSITORY_ROOT / "integrations" / "__init__.py",
        install_root / "integrations" / "__init__.py",
    )
    shutil.copy2(
        REPOSITORY_ROOT / "integrations" / "micromachine" / "__init__.py",
        resource_root / "__init__.py",
    )
    shutil.copy2(
        REPOSITORY_ROOT / "integrations" / "micromachine" / "HOOK_MANIFEST.json",
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
    return package_root / "runtime_data.py"


def _copy_superficial_source_markers(target_root: Path) -> Path:
    runtime_path = _copy_runtime_install(target_root)
    for relative_path in (
        Path(".github/workflows/ci.yml"),
        Path("MANIFEST.in"),
        Path("pyproject.toml"),
    ):
        target = target_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPOSITORY_ROOT / relative_path, target)
    return runtime_path


def _initialize_repository(repository_root: Path) -> None:
    _run("git", "init", "--quiet", cwd=repository_root)
    _run("git", "config", "user.name", "Runtime Data Test", cwd=repository_root)
    _run(
        "git",
        "config",
        "user.email",
        "runtime-data@example.invalid",
        cwd=repository_root,
    )


def _commit_all(repository_root: Path, message: str = "fixture") -> None:
    _run("git", "add", ".", cwd=repository_root)
    _run("git", "commit", "--quiet", "-m", message, cwd=repository_root)


def _probe(
    runtime_path: Path,
    *,
    cwd: Path,
    path: str | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["TARGET_RUNTIME_DATA_PATH"] = os.fspath(runtime_path)
    if path is not None:
        environment["PATH"] = path
    return subprocess.run(
        [sys.executable, "-c", TARGET_RUNTIME_PROBE],
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


def _payload(completed: subprocess.CompletedProcess[str]) -> dict[str, object]:
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return json.loads(completed.stdout)


class RuntimeDataTest(unittest.TestCase):
    def test_real_source_checkout_is_detected(self) -> None:
        self.assertEqual(REPOSITORY_ROOT, source_repository_root())
        self.assertTrue(micromachine_data_path("HOOK_MANIFEST.json").is_file())

    def test_resource_lookup_does_not_import_integrations_package(self) -> None:
        real_import = builtins.__import__

        def guarded_import(
            name: str,
            globals: object = None,
            locals: object = None,
            fromlist: object = (),
            level: int = 0,
        ) -> object:
            if name == "integrations" or name.startswith("integrations."):
                raise AssertionError("runtime data performed a package import")
            return real_import(name, globals, locals, fromlist, level)

        with mock.patch.object(
            builtins,
            "__import__",
            side_effect=guarded_import,
        ):
            observed = runtime_data.micromachine_data_root()

        self.assertEqual(
            REPOSITORY_ROOT / "integrations" / "micromachine",
            observed,
        )

    def test_symlinked_resource_directory_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            install_root = Path(directory) / "site-packages"
            resource_parent = install_root / "integrations"
            resource_parent.mkdir(parents=True)
            target = Path(directory) / "attacker-resources"
            target.mkdir()
            (resource_parent / "micromachine").symlink_to(
                target,
                target_is_directory=True,
            )

            with (
                mock.patch.object(
                    runtime_data,
                    "_SOURCE_REPOSITORY_ROOT",
                    install_root,
                ),
                self.assertRaisesRegex(RuntimeError, "without symlinks"),
            ):
                runtime_data.micromachine_data_root()

    def test_symlinked_resource_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            install_root = Path(directory) / "site-packages"
            resource_root = install_root / "integrations" / "micromachine"
            resource_root.mkdir(parents=True)
            target = Path(directory) / "attacker-manifest.json"
            target.write_text("{}\n")
            (resource_root / "PRE_LIVE_JOURNEYS.json").symlink_to(target)

            with (
                mock.patch.object(
                    runtime_data,
                    "_SOURCE_REPOSITORY_ROOT",
                    install_root,
                ),
                self.assertRaisesRegex(RuntimeError, "without symlinks"),
            ):
                runtime_data.micromachine_data_path(
                    "PRE_LIVE_JOURNEYS.json"
                )

    def test_committed_superficial_clone_with_canonical_remote_is_not_source(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target_root = Path(directory)
            runtime_path = _copy_superficial_source_markers(target_root)
            _initialize_repository(target_root)
            _run(
                "git",
                "remote",
                "add",
                "origin",
                CANONICAL_REMOTE,
                cwd=target_root,
            )
            _commit_all(target_root)
            completed = _probe(runtime_path, cwd=target_root)

        payload = _payload(completed)
        self.assertIsNone(payload["source_repository_root"])
        self.assertTrue(payload["manifest_exists"])
        self.assertTrue(payload["smoke_script_exists"])

    def test_untracked_superficial_markers_are_not_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target_root = Path(directory)
            _initialize_repository(target_root)
            (target_root / "README.md").write_text("unrelated\n", encoding="utf-8")
            _commit_all(target_root)
            runtime_path = _copy_superficial_source_markers(target_root)
            completed = _probe(runtime_path, cwd=target_root)

        self.assertIsNone(_payload(completed)["source_repository_root"])

    def test_install_below_unrelated_git_root_is_not_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target_root = Path(directory)
            _initialize_repository(target_root)
            (target_root / "README.md").write_text("unrelated\n", encoding="utf-8")
            _commit_all(target_root)
            install_root = target_root / "vendor" / "site-packages"
            runtime_path = _copy_runtime_install(install_root)
            completed = _probe(runtime_path, cwd=target_root)

        payload = _payload(completed)
        self.assertIsNone(payload["source_repository_root"])
        self.assertTrue(payload["manifest_exists"])

    def test_missing_git_fails_closed(self) -> None:
        with mock.patch.object(
            runtime_data,
            "_TRUSTED_GIT_EXECUTABLE",
            "/missing/voi-trusted-git",
        ):
            observed = runtime_data.source_repository_root()

        self.assertIsNone(observed)

    def test_inherited_path_cannot_override_trusted_git(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bin_root = Path(directory)
            sentinel = bin_root / "fake-git-invoked"
            fake_git = bin_root / "git"
            fake_git.write_text(
                "#!/bin/sh\n"
                f"/usr/bin/touch {sentinel}\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_git.chmod(0o755)
            completed = _probe(
                REPOSITORY_ROOT / "starcraft_commander" / "runtime_data.py",
                cwd=REPOSITORY_ROOT,
                path=os.fspath(bin_root),
            )
            fake_git_invoked = sentinel.exists()

        self.assertEqual(
            REPOSITORY_ROOT,
            Path(str(_payload(completed)["source_repository_root"])),
        )
        self.assertFalse(fake_git_invoked)

    def test_symlinked_runtime_module_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target_root = Path(directory)
            runtime_path = target_root / "starcraft_commander" / "runtime_data.py"
            runtime_path.parent.mkdir(parents=True)
            runtime_path.symlink_to(
                REPOSITORY_ROOT / "starcraft_commander" / "runtime_data.py"
            )
            resource_root = target_root / "integrations" / "micromachine"
            resource_root.mkdir(parents=True)
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
            scripts_root = resource_root / "scripts"
            scripts_root.mkdir()
            shutil.copy2(
                REPOSITORY_ROOT
                / "integrations"
                / "micromachine"
                / "scripts"
                / "smoke_macos_local.sh",
                scripts_root / "smoke_macos_local.sh",
            )
            completed = _probe(runtime_path, cwd=target_root)

        self.assertIsNone(_payload(completed)["source_repository_root"])

    def test_real_clone_rejects_dirty_identity_and_replacement_refs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clone_root = Path(directory) / "checkout"
            _run(
                "git",
                "clone",
                "--quiet",
                "--no-hardlinks",
                os.fspath(REPOSITORY_ROOT),
                os.fspath(clone_root),
                cwd=Path(directory),
            )
            head = _run(
                "git",
                "rev-parse",
                "HEAD",
                cwd=REPOSITORY_ROOT,
            ).stdout.strip()
            _run("git", "checkout", "--quiet", "--detach", head, cwd=clone_root)
            runtime_path = clone_root / "starcraft_commander" / "runtime_data.py"
            local_remote = _probe(runtime_path, cwd=clone_root)
            _run(
                "git",
                "remote",
                "set-url",
                "origin",
                CANONICAL_REMOTE,
                cwd=clone_root,
            )
            clean = _probe(runtime_path, cwd=clone_root)

            manifest_path = clone_root / "MANIFEST.in"
            manifest_path.write_text(
                manifest_path.read_text(encoding="utf-8") + "\n# dirty\n",
                encoding="utf-8",
            )
            dirty = _probe(runtime_path, cwd=clone_root)
            _run(
                "git",
                "checkout",
                "--quiet",
                "HEAD",
                "--",
                "MANIFEST.in",
                cwd=clone_root,
            )

            launcher_path = (
                clone_root
                / "integrations"
                / "micromachine"
                / "scripts"
                / "smoke_macos_local.sh"
            )
            launcher_path.write_text(
                launcher_path.read_text(encoding="utf-8")
                + "\n# dirty launcher\n",
                encoding="utf-8",
            )
            dirty_launcher = _probe(runtime_path, cwd=clone_root)
            _run(
                "git",
                "checkout",
                "--quiet",
                "HEAD",
                "--",
                "integrations/micromachine/scripts/smoke_macos_local.sh",
                cwd=clone_root,
            )

            launcher_path.unlink()
            launcher_path.symlink_to(clone_root / "MANIFEST.in")
            symlinked_launcher = _probe(runtime_path, cwd=clone_root)
            _run(
                "git",
                "checkout",
                "--quiet",
                "HEAD",
                "--",
                "integrations/micromachine/scripts/smoke_macos_local.sh",
                cwd=clone_root,
            )

            anchor = "23173dbb8d889d8828ddb6fbdab84b0d5e822476"
            _run("git", "replace", anchor, head, cwd=clone_root)
            replaced = _probe(runtime_path, cwd=clone_root)

        self.assertIsNone(_payload(local_remote)["source_repository_root"])
        self.assertEqual(
            clone_root.resolve(),
            Path(str(_payload(clean)["source_repository_root"])),
        )
        self.assertIsNone(_payload(dirty)["source_repository_root"])
        self.assertIsNone(_payload(dirty_launcher)["source_repository_root"])
        self.assertIsNone(_payload(symlinked_launcher)["source_repository_root"])
        self.assertIsNone(_payload(replaced)["source_repository_root"])


if __name__ == "__main__":
    unittest.main()
