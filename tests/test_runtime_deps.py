"""Tests for the optional runtime dependency guard module.

These tests never require StarCraft II, python-sc2 (burnysc2), faster-whisper,
or sounddevice. Absence is simulated deterministically by blocking the module
name in ``sys.modules`` (a ``None`` entry makes ``importlib`` raise
``ImportError``), and presence is simulated by injecting pure-Python fake
modules. This keeps the suite stable even on machines where some optional
dependencies happen to be installed.
"""

import importlib.util
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
import zipfile
from unittest import mock

from starcraft_commander.runtime_deps import (
    ANTHROPIC_INSTALL_HINT,
    ANTHROPIC_MODULE_NAME,
    FASTER_WHISPER_INSTALL_HINT,
    FASTER_WHISPER_MODULE_NAME,
    MissingLLMDependencyError,
    MissingSC2RuntimeError,
    MissingVoiceDependencyError,
    PYTHON_SC2_INSTALL_HINT,
    PYTHON_SC2_MODULE_NAME,
    SOUNDDEVICE_INSTALL_HINT,
    SOUNDDEVICE_MODULE_NAME,
    is_anthropic_available,
    is_faster_whisper_available,
    is_python_sc2_available,
    is_sounddevice_available,
    require_anthropic,
    require_faster_whisper,
    require_python_sc2,
    require_sounddevice,
)

GUARD_CASES = (
    (
        PYTHON_SC2_MODULE_NAME,
        is_python_sc2_available,
        require_python_sc2,
        MissingSC2RuntimeError,
    ),
    (
        FASTER_WHISPER_MODULE_NAME,
        is_faster_whisper_available,
        require_faster_whisper,
        MissingVoiceDependencyError,
    ),
    (
        SOUNDDEVICE_MODULE_NAME,
        is_sounddevice_available,
        require_sounddevice,
        MissingVoiceDependencyError,
    ),
    (
        ANTHROPIC_MODULE_NAME,
        is_anthropic_available,
        require_anthropic,
        MissingLLMDependencyError,
    ),
)


def _block_module(module_name):
    """Patch sys.modules so importing module_name raises ImportError."""

    return mock.patch.dict(sys.modules, {module_name: None})


class RuntimeDepsErrorHierarchyTest(unittest.TestCase):
    def test_guard_errors_subclass_runtime_error(self) -> None:
        guard_error_types = (
            MissingSC2RuntimeError,
            MissingVoiceDependencyError,
            MissingLLMDependencyError,
        )
        for error_type in guard_error_types:
            with self.subTest(error_type=error_type.__name__):
                self.assertTrue(issubclass(error_type, RuntimeError))


class RuntimeDepsUnavailableTest(unittest.TestCase):
    @unittest.skipIf(
        importlib.util.find_spec("sc2") is not None,
        "burnysc2 is installed locally; absence behavior is still covered by "
        "the blocked-module tests below",
    )
    def test_python_sc2_unavailable_in_this_test_environment(self) -> None:
        self.assertFalse(is_python_sc2_available())
        with self.assertRaises(MissingSC2RuntimeError):
            require_python_sc2()

    def test_availability_checks_return_false_when_module_absent(self) -> None:
        for module_name, available_func, _require_func, _error_type in GUARD_CASES:
            with self.subTest(module=module_name):
                with _block_module(module_name):
                    self.assertFalse(available_func())

    def test_require_guards_raise_dedicated_errors_when_module_absent(self) -> None:
        for module_name, _available_func, require_func, error_type in GUARD_CASES:
            with self.subTest(module=module_name):
                with _block_module(module_name):
                    with self.assertRaises(error_type) as context:
                        require_func()
                self.assertIsInstance(context.exception, RuntimeError)
                self.assertIsInstance(context.exception.__cause__, ImportError)

    def test_missing_sc2_runtime_error_message_is_actionable(self) -> None:
        with _block_module(PYTHON_SC2_MODULE_NAME):
            with self.assertRaises(MissingSC2RuntimeError) as context:
                require_python_sc2()
        message = str(context.exception)
        expected_fragments = (
            "pip install 'voiStarcraft2[sc2]'",
            "pip install burnysc2",
            "StarCraft II",
            "docs/sc2-smoke-test.md",
            "설치",
        )
        for fragment in expected_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, message)
        self.assertEqual(message, PYTHON_SC2_INSTALL_HINT)

    def test_referenced_smoke_test_guide_exists_with_promised_content(self) -> None:
        # The install hint sends users to docs/sc2-smoke-test.md at the exact
        # moment they need setup help; the document must exist and cover the
        # handoff Step 6 topics it promises.
        repo_root = pathlib.Path(__file__).resolve().parent.parent
        guide_path = repo_root / "docs" / "sc2-smoke-test.md"
        self.assertTrue(
            guide_path.is_file(),
            "PYTHON_SC2_INSTALL_HINT references docs/sc2-smoke-test.md, "
            "so the guide must exist.",
        )
        guide = guide_path.read_text(encoding="utf-8")
        expected_topics = (
            "StarCraft II",
            "Maps",
            "3.10",
            "pip install burnysc2",
            "demo_sc2",
            "Known limitations",
        )
        for topic in expected_topics:
            with self.subTest(topic=topic):
                self.assertIn(topic, guide)

    def test_missing_voice_dependency_error_messages_are_actionable(self) -> None:
        voice_cases = (
            (
                FASTER_WHISPER_MODULE_NAME,
                require_faster_whisper,
                "faster-whisper",
                FASTER_WHISPER_INSTALL_HINT,
            ),
            (
                SOUNDDEVICE_MODULE_NAME,
                require_sounddevice,
                "sounddevice",
                SOUNDDEVICE_INSTALL_HINT,
            ),
        )
        for module_name, require_func, pip_name, expected_hint in voice_cases:
            with self.subTest(module=module_name):
                with _block_module(module_name):
                    with self.assertRaises(MissingVoiceDependencyError) as context:
                        require_func()
                message = str(context.exception)
                self.assertIn("pip install 'voiStarcraft2[voice]'", message)
                self.assertIn(pip_name, message)
                self.assertIn("설치", message)
                self.assertEqual(message, expected_hint)

    def test_missing_llm_dependency_error_message_is_actionable(self) -> None:
        with _block_module(ANTHROPIC_MODULE_NAME):
            with self.assertRaises(MissingLLMDependencyError) as context:
                require_anthropic()
        message = str(context.exception)
        expected_fragments = (
            "pip install 'voiStarcraft2[llm]'",
            "pip install anthropic",
            "ANTHROPIC_API_KEY",
            "설치",
        )
        for fragment in expected_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, message)
        self.assertEqual(message, ANTHROPIC_INSTALL_HINT)


class RuntimeDepsFakeInjectionTest(unittest.TestCase):
    def test_injected_fake_modules_are_returned_and_reported_available(self) -> None:
        for module_name, available_func, require_func, _error_type in GUARD_CASES:
            with self.subTest(module=module_name):
                fake_module = types.ModuleType(module_name)
                with mock.patch.dict(sys.modules, {module_name: fake_module}):
                    self.assertTrue(available_func())
                    self.assertIs(require_func(), fake_module)

    def test_guards_do_not_cache_fake_modules_after_patch_exit(self) -> None:
        for module_name, available_func, _require_func, _error_type in GUARD_CASES:
            with self.subTest(module=module_name):
                fake_module = types.ModuleType(module_name)
                with mock.patch.dict(sys.modules, {module_name: fake_module}):
                    self.assertTrue(available_func())
                with _block_module(module_name):
                    self.assertFalse(available_func())


class DistributionLicenseFilesTest(unittest.TestCase):
    def test_wheel_and_sdist_include_third_party_notices(self) -> None:
        repo_root = pathlib.Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = pathlib.Path(temporary_directory)
            source_root = temporary_root / "source"
            dist_root = temporary_root / "dist"
            source_root.mkdir()
            dist_root.mkdir()

            for filename in (
                "pyproject.toml",
                "README.md",
                "LICENSE",
                "THIRD_PARTY_NOTICES.md",
            ):
                shutil.copy2(repo_root / filename, source_root / filename)
            for directory in (
                "LICENSES",
                "toycraft_commander",
                "starcraft_commander",
                "broodwar_commander",
            ):
                shutil.copytree(
                    repo_root / directory,
                    source_root / directory,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                )

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "build",
                    "--outdir",
                    str(dist_root),
                    str(source_root),
                ],
                cwd=repo_root,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            wheel_path = next(dist_root.glob("*.whl"))
            with zipfile.ZipFile(wheel_path) as wheel:
                self.assertTrue(
                    any(
                        name.endswith("/licenses/THIRD_PARTY_NOTICES.md")
                        for name in wheel.namelist()
                    ),
                    wheel.namelist(),
                )

            sdist_path = next(dist_root.glob("*.tar.gz"))
            with tarfile.open(sdist_path, "r:gz") as sdist:
                self.assertTrue(
                    any(
                        name.endswith("/THIRD_PARTY_NOTICES.md")
                        for name in sdist.getnames()
                    ),
                    sdist.getnames(),
                )


if __name__ == "__main__":
    unittest.main()
