from __future__ import annotations

import os
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import starcraft_commander.battlefield_browser_gate as browser_gate
from starcraft_commander.battlefield_browser_gate import (
    BrowserGateConfig,
    PIXEL_CHANNEL_TOLERANCE,
    STANDARD_OPERATION_ACTIONS,
    VISUAL_DIFF_THRESHOLD,
    _CandidateFixtureProcess,
    _FIXTURE_BOOTSTRAP,
    _BrowserFixtureBridge,
    _BrowserFixtureLauncher,
    _assert_visible_structure,
    _pixel_diff,
    _status_payload,
    _write_rgba_png,
)


REPOSITORY_SHA = "a" * 40
BUILD_IDENTITY = "sha256:" + "b" * 64


def _visible_metrics() -> dict[str, object]:
    return {
        "hidden": False,
        "display": "block",
        "visibility": "visible",
        "content_visibility": "visible",
        "transparent": False,
        "width": 100,
        "height": 40,
        "client_rects": 1,
    }


def _visible_structure() -> dict[str, object]:
    return {
        "lanes": [_visible_metrics() for _ in range(4)],
        "cards": [
            {
                "visibility": _visible_metrics(),
                "stages": [_visible_metrics() for _ in range(4)],
                "actions": [
                    {"name": action, **_visible_metrics()}
                    for action in STANDARD_OPERATION_ACTIONS
                ],
            }
            for _ in range(4)
        ],
    }


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
            staged_root = Path(directory) / "staged"
            staged_root.mkdir()
            staged = staged_root / "fixture.py"
            staged.write_text("# staged fixture\n", encoding="utf-8")
            config = BrowserGateConfig(
                repository_sha=REPOSITORY_SHA,
                build_identity=BUILD_IDENTITY,
                artifact_dir=Path(directory),
                candidate_uid=65001,
                candidate_gid=65001,
            )

            command = _CandidateFixtureProcess(config)._command(
                staged,
                candidate_root=staged_root,
            )

            self.assertEqual("/usr/bin/sudo", command[0])
            self.assertIn("--user=#65001", command)
            self.assertIn("--group=#65001", command)
            self.assertIn("--", command)
            self.assertEqual(str(staged_root), command[-2])
            self.assertEqual(str(staged), command[-1])

    def test_candidate_fixture_stages_exact_read_only_traversable_source(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging_root = Path(directory).resolve()
            staging_root.chmod(0o755)
            config = BrowserGateConfig(
                repository_sha=REPOSITORY_SHA,
                build_identity=BUILD_IDENTITY,
                artifact_dir=staging_root,
                candidate_uid=65001,
                candidate_gid=65001,
            )
            fixture = _CandidateFixtureProcess(config)

            with mock.patch.object(
                browser_gate,
                "_FIXTURE_STAGING_ROOT",
                staging_root,
            ):
                staged = fixture._prepare_fixture_script()

            staged_candidate_root = fixture._staged_candidate_root
            self.assertIsNotNone(staged_candidate_root)
            assert staged_candidate_root is not None
            self.assertEqual(staging_root, staged_candidate_root.parent)
            self.assertEqual(staged_candidate_root, staged.parent)
            self.assertFalse(staged.is_symlink())
            self.assertEqual(
                Path(browser_gate.__file__).read_bytes(),
                staged.read_bytes(),
            )
            self.assertEqual(0o444, stat.S_IMODE(staged.stat().st_mode))
            self.assertEqual(
                0o555,
                stat.S_IMODE(staged_candidate_root.stat().st_mode),
            )
            self.assertEqual(
                Path(browser_gate.__file__).stat().st_uid,
                staged.stat().st_uid,
            )
            staged_web_gui = (
                staged_candidate_root
                / "starcraft_commander"
                / "web_gui.py"
            )
            self.assertEqual(
                config.candidate_root.joinpath(
                    "starcraft_commander",
                    "web_gui.py",
                ).read_bytes(),
                staged_web_gui.read_bytes(),
            )
            self.assertEqual(
                0o444,
                stat.S_IMODE(staged_web_gui.stat().st_mode),
            )

            fixture._cleanup_fixture_script()
            self.assertFalse(staged.exists())
            self.assertFalse(staged_candidate_root.exists())

    def test_candidate_fixture_accepts_verified_sudo_descendant_pid(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = BrowserGateConfig(
                repository_sha=REPOSITORY_SHA,
                build_identity=BUILD_IDENTITY,
                artifact_dir=Path(directory),
                candidate_uid=65001,
                candidate_gid=65001,
            )
            fixture = _CandidateFixtureProcess(config)
            process = mock.Mock(pid=4321)
            process.poll.return_value = None
            process.stdout.readline.return_value = (
                '{"candidate_sha":"'
                + REPOSITORY_SHA
                + '","nonce":"'
                + fixture._nonce
                + '","origin":"http://127.0.0.1:8765/",'
                '"pid":9876,"schema_version":1,'
                '"type":"battlefield-webgui-ready"}\n'
            )
            selector = mock.Mock()
            selector.select.return_value = [(process.stdout, 1)]

            with (
                mock.patch.object(browser_gate.sys, "platform", "linux"),
                mock.patch.object(
                    fixture,
                    "_linux_process_identity",
                    side_effect=[
                        (7000, (65001,) * 4, (65001,) * 4),
                        (4321, (0,) * 4, (0,) * 4),
                    ],
                ) as process_identity,
                mock.patch.object(
                    browser_gate.selectors,
                    "DefaultSelector",
                    return_value=selector,
                ),
                mock.patch.object(fixture, "_start_drain"),
            ):
                origin = fixture._wait_until_ready(process)

            self.assertEqual("http://127.0.0.1:8765/", origin)
            self.assertEqual(
                [mock.call(9876), mock.call(7000)],
                process_identity.call_args_list,
            )
            selector.close.assert_called_once_with()

    def test_candidate_fixture_accepts_verified_direct_sudo_exec_pid(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _CandidateFixtureProcess(
                BrowserGateConfig(
                    repository_sha=REPOSITORY_SHA,
                    build_identity=BUILD_IDENTITY,
                    artifact_dir=Path(directory),
                    candidate_uid=65001,
                    candidate_gid=65001,
                )
            )
            process = mock.Mock(pid=4321)

            with (
                mock.patch.object(browser_gate.sys, "platform", "linux"),
                mock.patch.object(
                    fixture,
                    "_linux_process_identity",
                    return_value=(1234, (65001,) * 4, (65001,) * 4),
                ),
            ):
                matches = fixture._readiness_process_matches(process, 4321)

            self.assertTrue(matches)

    def test_staged_fixture_imports_candidate_package_with_isolated_python(
        self,
    ) -> None:
        with (
            tempfile.TemporaryDirectory() as source_directory,
            tempfile.TemporaryDirectory() as staging_directory,
        ):
            source_root = Path(source_directory).resolve()
            candidate_root = source_root / "candidate"
            package_root = candidate_root / "starcraft_commander"
            package_root.mkdir(parents=True)
            package_root.joinpath("__init__.py").write_text(
                "",
                encoding="utf-8",
            )
            package_root.joinpath("web_gui.py").write_text(
                "class WebGuiServer:\n"
                "    pass\n",
                encoding="utf-8",
            )
            staging_root = Path(staging_directory).resolve()
            staging_root.chmod(0o755)
            config = BrowserGateConfig(
                repository_sha=REPOSITORY_SHA,
                build_identity=BUILD_IDENTITY,
                artifact_dir=source_root / "artifacts",
                candidate_root=candidate_root,
                candidate_uid=65001,
                candidate_gid=65001,
            )
            fixture = _CandidateFixtureProcess(config)

            with mock.patch.object(
                browser_gate,
                "_FIXTURE_STAGING_ROOT",
                staging_root,
            ):
                staged_script = fixture._prepare_fixture_script()
                staged_candidate_root = fixture._staged_candidate_root
                assert staged_candidate_root is not None
                sudo_command = fixture._command(
                    staged_script,
                    candidate_root=staged_candidate_root,
                )
                isolated_command = sudo_command[
                    sudo_command.index("--") + 1 :
                ]
                environment = {
                    "HOME": "/tmp",
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "PATH": "/usr/bin:/bin",
                    "PYTHONDONTWRITEBYTECODE": "1",
                }

                result = subprocess.run(
                    isolated_command,
                    cwd=staged_candidate_root,
                    env=environment,
                    input="",
                    text=True,
                    capture_output=True,
                    check=False,
                )

                self.assertEqual(1, result.returncode)
                self.assertIn("-I", isolated_command)
                self.assertNotIn("PYTHONPATH", environment)
                self.assertNotIn("No module named", result.stderr)
                self.assertIn(
                    "candidate browser fixture failed: Expecting value",
                    result.stderr,
                )
                fixture._cleanup_fixture_script()

            self.assertEqual([], list(staging_root.iterdir()))

    def test_candidate_fixture_rejects_transitive_package_symlink(self) -> None:
        with (
            tempfile.TemporaryDirectory() as source_directory,
            tempfile.TemporaryDirectory() as staging_directory,
        ):
            source_root = Path(source_directory).resolve()
            candidate_root = source_root / "candidate"
            package_root = candidate_root / "starcraft_commander"
            package_root.mkdir(parents=True)
            package_root.joinpath("__init__.py").write_text(
                "",
                encoding="utf-8",
            )
            package_root.joinpath("web_gui.py").write_text(
                "class WebGuiServer:\n"
                "    pass\n",
                encoding="utf-8",
            )
            outside = source_root / "outside.py"
            outside.write_text("ESCAPED = True\n", encoding="utf-8")
            package_root.joinpath("escaped.py").symlink_to(outside)
            staging_root = Path(staging_directory).resolve()
            staging_root.chmod(0o755)
            fixture = _CandidateFixtureProcess(
                BrowserGateConfig(
                    repository_sha=REPOSITORY_SHA,
                    build_identity=BUILD_IDENTITY,
                    artifact_dir=source_root / "artifacts",
                    candidate_root=candidate_root,
                    candidate_uid=65001,
                    candidate_gid=65001,
                )
            )

            with (
                mock.patch.object(
                    browser_gate,
                    "_FIXTURE_STAGING_ROOT",
                    staging_root,
                ),
                self.assertRaisesRegex(
                    ValueError,
                    "candidate package contains a symlink",
                ),
            ):
                fixture._prepare_fixture_script()

            self.assertEqual([], list(staging_root.iterdir()))
            self.assertIsNone(fixture._staged_candidate_root)
            self.assertIsNone(fixture._staged_fixture_script)

    def test_candidate_fixture_cleans_staging_when_trusted_copy_fails(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging_root = Path(directory).resolve()
            staging_root.chmod(0o755)
            fixture = _CandidateFixtureProcess(
                BrowserGateConfig(
                    repository_sha=REPOSITORY_SHA,
                    build_identity=BUILD_IDENTITY,
                    artifact_dir=staging_root,
                    candidate_uid=65001,
                    candidate_gid=65001,
                )
            )

            with (
                mock.patch.object(
                    browser_gate,
                    "_FIXTURE_STAGING_ROOT",
                    staging_root,
                ),
                mock.patch.object(
                    fixture,
                    "_copy_staged_file",
                    side_effect=OSError("trusted copy failed"),
                ),
                self.assertRaisesRegex(OSError, "trusted copy failed"),
            ):
                fixture._prepare_fixture_script()

            self.assertEqual([], list(staging_root.iterdir()))
            self.assertIsNone(fixture._staged_candidate_root)
            self.assertIsNone(fixture._staged_fixture_script)

    def test_candidate_fixture_retains_retry_state_when_prepare_cleanup_fails(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging_root = Path(directory).resolve()
            staging_root.chmod(0o755)
            fixture = _CandidateFixtureProcess(
                BrowserGateConfig(
                    repository_sha=REPOSITORY_SHA,
                    build_identity=BUILD_IDENTITY,
                    artifact_dir=staging_root,
                    candidate_uid=65001,
                    candidate_gid=65001,
                )
            )

            with (
                mock.patch.object(
                    browser_gate,
                    "_FIXTURE_STAGING_ROOT",
                    staging_root,
                ),
                mock.patch.object(
                    fixture,
                    "_copy_staged_file",
                    side_effect=OSError("trusted copy failed"),
                ),
                mock.patch.object(
                    fixture,
                    "_discard_staged_fixture_tree",
                    side_effect=OSError("delete failed"),
                ),
                self.assertRaisesRegex(OSError, "delete failed"),
            ):
                fixture._prepare_fixture_script()

            staged_root = fixture._staged_candidate_root
            self.assertIsNotNone(staged_root)
            assert staged_root is not None
            self.assertTrue(staged_root.exists())
            fixture._cleanup_fixture_script()

            self.assertFalse(staged_root.exists())
            self.assertEqual([], list(staging_root.iterdir()))
            self.assertIsNone(fixture._staged_candidate_root)
            self.assertIsNone(fixture._staged_fixture_script)

    def test_candidate_fixture_entry_limit_stops_directory_iteration(
        self,
    ) -> None:
        with (
            tempfile.TemporaryDirectory() as source_directory,
            tempfile.TemporaryDirectory() as staging_directory,
        ):
            source_root = Path(source_directory).resolve()
            trusted_fixture = source_root / "fixture.py"
            trusted_fixture.write_text("# ok\n", encoding="utf-8")
            candidate_root = source_root / "candidate"
            package_root = candidate_root / "starcraft_commander"
            package_root.mkdir(parents=True)
            package_root.joinpath("__init__.py").write_text("", encoding="utf-8")
            package_root.joinpath("web_gui.py").write_text("", encoding="utf-8")
            for index in range(10):
                package_root.joinpath(f"nested-{index}").mkdir()
            staging_root = Path(staging_directory).resolve()
            staging_root.chmod(0o755)
            fixture = _CandidateFixtureProcess(
                BrowserGateConfig(
                    repository_sha=REPOSITORY_SHA,
                    build_identity=BUILD_IDENTITY,
                    artifact_dir=source_root / "artifacts",
                    candidate_root=candidate_root,
                    candidate_uid=65001,
                    candidate_gid=65001,
                )
            )
            real_scandir = os.scandir
            consumed_entries: list[str] = []
            package_snapshot = package_root.stat()

            class MonitoredScandir:
                def __init__(self, directory: int | Path) -> None:
                    self._scandir = real_scandir(directory)
                    self._iterator: object | None = None

                def __enter__(self) -> MonitoredScandir:
                    self._iterator = self._scandir.__enter__()
                    return self

                def __exit__(self, *args: object) -> object:
                    return self._scandir.__exit__(*args)

                def __iter__(self) -> MonitoredScandir:
                    return self

                def __next__(self) -> os.DirEntry[str]:
                    assert self._iterator is not None
                    entry = next(self._iterator)  # type: ignore[arg-type]
                    consumed_entries.append(entry.name)
                    if len(consumed_entries) > 1:
                        raise AssertionError(
                            "candidate directory was materialized before limit"
                        )
                    return entry

            def monitored_scandir(
                directory: (
                    int
                    | str
                    | bytes
                    | os.PathLike[str]
                    | os.PathLike[bytes]
                ),
            ) -> object:
                if isinstance(directory, int):
                    snapshot = os.fstat(directory)
                    if (
                        snapshot.st_dev,
                        snapshot.st_ino,
                    ) == (
                        package_snapshot.st_dev,
                        package_snapshot.st_ino,
                    ):
                        return MonitoredScandir(directory)
                    return real_scandir(directory)
                path = Path(directory).resolve()
                if path == package_root:
                    return MonitoredScandir(path)
                return real_scandir(directory)

            with (
                mock.patch.object(browser_gate, "__file__", str(trusted_fixture)),
                mock.patch.object(
                    browser_gate,
                    "_FIXTURE_STAGING_ROOT",
                    staging_root,
                ),
                mock.patch.object(
                    browser_gate,
                    "_FIXTURE_STAGING_ENTRY_LIMIT",
                    2,
                ),
                mock.patch.object(browser_gate.os, "scandir", monitored_scandir),
                self.assertRaisesRegex(RuntimeError, "entry limit exceeded"),
            ):
                fixture._prepare_fixture_script()

            self.assertEqual(1, len(consumed_entries))
            self.assertEqual([], list(staging_root.iterdir()))

    def test_candidate_fixture_rejects_parent_directory_symlink_swap(
        self,
    ) -> None:
        with (
            tempfile.TemporaryDirectory() as source_directory,
            tempfile.TemporaryDirectory() as staging_directory,
            tempfile.TemporaryDirectory() as outside_directory,
        ):
            source_root = Path(source_directory).resolve()
            trusted_fixture = source_root / "fixture.py"
            trusted_fixture.write_text("# ok\n", encoding="utf-8")
            candidate_root = source_root / "candidate"
            package_root = candidate_root / "starcraft_commander"
            nested_root = package_root / "nested"
            nested_root.mkdir(parents=True)
            package_root.joinpath("__init__.py").write_text("", encoding="utf-8")
            package_root.joinpath("web_gui.py").write_text("", encoding="utf-8")
            nested_root.joinpath("safe.py").write_text("# safe\n", encoding="utf-8")
            outside_root = Path(outside_directory).resolve()
            outside_root.joinpath("escaped.py").write_text(
                "# outside\n",
                encoding="utf-8",
            )
            staging_root = Path(staging_directory).resolve()
            staging_root.chmod(0o755)
            fixture = _CandidateFixtureProcess(
                BrowserGateConfig(
                    repository_sha=REPOSITORY_SHA,
                    build_identity=BUILD_IDENTITY,
                    artifact_dir=source_root / "artifacts",
                    candidate_root=candidate_root,
                    candidate_uid=65001,
                    candidate_gid=65001,
                )
            )
            real_open = os.open
            swapped = False

            def swapping_open(
                path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                nonlocal swapped
                if (
                    not swapped
                    and dir_fd is not None
                    and os.fspath(path) == "nested"
                    and flags & getattr(os, "O_DIRECTORY", 0)
                ):
                    swapped = True
                    nested_root.rename(package_root / "nested-original")
                    nested_root.symlink_to(outside_root, target_is_directory=True)
                return real_open(path, flags, mode, dir_fd=dir_fd)

            with (
                mock.patch.object(browser_gate, "__file__", str(trusted_fixture)),
                mock.patch.object(
                    browser_gate,
                    "_FIXTURE_STAGING_ROOT",
                    staging_root,
                ),
                mock.patch.object(browser_gate.os, "open", swapping_open),
                self.assertRaisesRegex(
                    RuntimeError,
                    "directory changed or linked",
                ),
            ):
                fixture._prepare_fixture_script()

            self.assertTrue(swapped)
            self.assertEqual([], list(staging_root.iterdir()))
            self.assertIsNone(fixture._staged_candidate_root)
            self.assertIsNone(fixture._staged_fixture_script)

    def test_candidate_fixture_closes_child_directory_when_fstat_fails(
        self,
    ) -> None:
        with (
            tempfile.TemporaryDirectory() as source_directory,
            tempfile.TemporaryDirectory() as staging_directory,
        ):
            source_root = Path(source_directory).resolve()
            trusted_fixture = source_root / "fixture.py"
            trusted_fixture.write_text("# ok\n", encoding="utf-8")
            candidate_root = source_root / "candidate"
            package_root = candidate_root / "starcraft_commander"
            nested_root = package_root / "nested"
            nested_root.mkdir(parents=True)
            package_root.joinpath("__init__.py").write_text("", encoding="utf-8")
            package_root.joinpath("web_gui.py").write_text("", encoding="utf-8")
            nested_root.joinpath("safe.py").write_text("# safe\n", encoding="utf-8")
            staging_root = Path(staging_directory).resolve()
            staging_root.chmod(0o755)
            fixture = _CandidateFixtureProcess(
                BrowserGateConfig(
                    repository_sha=REPOSITORY_SHA,
                    build_identity=BUILD_IDENTITY,
                    artifact_dir=source_root / "artifacts",
                    candidate_root=candidate_root,
                    candidate_uid=65001,
                    candidate_gid=65001,
                )
            )
            real_open = os.open
            real_fstat = os.fstat
            real_close = os.close
            child_descriptors: list[int] = []
            closed_descriptors: list[int] = []

            def recording_open(
                path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
                if (
                    dir_fd is not None
                    and os.fspath(path) == "nested"
                    and flags & getattr(os, "O_DIRECTORY", 0)
                ):
                    child_descriptors.append(descriptor)
                return descriptor

            def failing_fstat(descriptor: int) -> os.stat_result:
                if descriptor in child_descriptors:
                    raise OSError("child fstat failed")
                return real_fstat(descriptor)

            def recording_close(descriptor: int) -> None:
                if descriptor in child_descriptors:
                    closed_descriptors.append(descriptor)
                real_close(descriptor)

            with (
                mock.patch.object(browser_gate, "__file__", str(trusted_fixture)),
                mock.patch.object(
                    browser_gate,
                    "_FIXTURE_STAGING_ROOT",
                    staging_root,
                ),
                mock.patch.object(browser_gate.os, "open", recording_open),
                mock.patch.object(browser_gate.os, "fstat", failing_fstat),
                mock.patch.object(browser_gate.os, "close", recording_close),
                self.assertRaisesRegex(OSError, "child fstat failed"),
            ):
                fixture._prepare_fixture_script()

            self.assertEqual(1, len(child_descriptors))
            self.assertEqual(child_descriptors, closed_descriptors)
            self.assertEqual([], list(staging_root.iterdir()))
            self.assertIsNone(fixture._staged_candidate_root)
            self.assertIsNone(fixture._staged_fixture_script)

    def test_candidate_fixture_preserves_fdopen_failure_without_double_close(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.py"
            destination = root / "destination.py"
            source.write_text("# source\n", encoding="utf-8")
            real_fdopen = os.fdopen

            def failing_destination_fdopen(
                descriptor: int,
                mode: str,
            ) -> object:
                if mode == "wb":
                    raise OSError("destination fdopen failed")
                return real_fdopen(descriptor, mode)

            with (
                mock.patch.object(
                    browser_gate.os,
                    "fdopen",
                    failing_destination_fdopen,
                ),
                self.assertRaisesRegex(
                    OSError,
                    "destination fdopen failed",
                ),
            ):
                _CandidateFixtureProcess._copy_staged_file(
                    source,
                    destination,
                    byte_limit=1024,
                )

            self.assertFalse(destination.exists())

    def test_candidate_fixture_file_reads_are_chunk_bounded(
        self,
    ) -> None:
        with (
            tempfile.TemporaryDirectory() as source_directory,
            tempfile.TemporaryDirectory() as staging_directory,
        ):
            source_root = Path(source_directory).resolve()
            trusted_fixture = source_root / "fixture.py"
            trusted_fixture.write_bytes(b"0123456789")
            candidate_root = source_root / "candidate"
            package_root = candidate_root / "starcraft_commander"
            package_root.mkdir(parents=True)
            package_root.joinpath("__init__.py").write_bytes(b"abcdefghij")
            package_root.joinpath("web_gui.py").write_bytes(b"klmnopqrst")
            staging_root = Path(staging_directory).resolve()
            staging_root.chmod(0o755)
            fixture = _CandidateFixtureProcess(
                BrowserGateConfig(
                    repository_sha=REPOSITORY_SHA,
                    build_identity=BUILD_IDENTITY,
                    artifact_dir=source_root / "artifacts",
                    candidate_root=candidate_root,
                    candidate_uid=65001,
                    candidate_gid=65001,
                )
            )
            real_fdopen = os.fdopen
            read_sizes: list[int] = []

            class RecordingReader:
                def __init__(self, stream: object) -> None:
                    self._stream = stream

                def __enter__(self) -> RecordingReader:
                    return self

                def __exit__(self, *args: object) -> object:
                    return self._stream.__exit__(*args)

                def read(self, size: int = -1) -> bytes:
                    read_sizes.append(size)
                    if size != 4:
                        raise AssertionError("candidate file read was unbounded")
                    return self._stream.read(size)

                def fileno(self) -> int:
                    return self._stream.fileno()

                def close(self) -> None:
                    self._stream.close()

            def monitored_fdopen(
                descriptor: int,
                mode: str,
            ) -> object:
                stream = real_fdopen(descriptor, mode)
                if mode == "rb":
                    return RecordingReader(stream)
                return stream

            with (
                mock.patch.object(browser_gate, "__file__", str(trusted_fixture)),
                mock.patch.object(
                    browser_gate,
                    "_FIXTURE_STAGING_ROOT",
                    staging_root,
                ),
                mock.patch.object(
                    browser_gate,
                    "_FIXTURE_STAGING_CHUNK_SIZE",
                    4,
                ),
                mock.patch.object(browser_gate.os, "fdopen", monitored_fdopen),
                mock.patch.object(
                    Path,
                    "read_bytes",
                    side_effect=AssertionError(
                        "candidate source was materialized"
                    ),
                ),
            ):
                fixture._prepare_fixture_script()
                fixture._cleanup_fixture_script()

            self.assertGreater(len(read_sizes), 3)
            self.assertEqual({4}, set(read_sizes))
            self.assertEqual([], list(staging_root.iterdir()))

    def test_candidate_fixture_byte_limit_is_cumulative(self) -> None:
        with (
            tempfile.TemporaryDirectory() as source_directory,
            tempfile.TemporaryDirectory() as staging_directory,
        ):
            source_root = Path(source_directory).resolve()
            trusted_fixture = source_root / "fixture.py"
            trusted_fixture.write_bytes(b"1234")
            candidate_root = source_root / "candidate"
            package_root = candidate_root / "starcraft_commander"
            package_root.mkdir(parents=True)
            package_root.joinpath("__init__.py").write_bytes(b"567")
            package_root.joinpath("web_gui.py").write_bytes(b"890")
            staging_root = Path(staging_directory).resolve()
            staging_root.chmod(0o755)
            fixture = _CandidateFixtureProcess(
                BrowserGateConfig(
                    repository_sha=REPOSITORY_SHA,
                    build_identity=BUILD_IDENTITY,
                    artifact_dir=source_root / "artifacts",
                    candidate_root=candidate_root,
                    candidate_uid=65001,
                    candidate_gid=65001,
                )
            )

            with (
                mock.patch.object(browser_gate, "__file__", str(trusted_fixture)),
                mock.patch.object(
                    browser_gate,
                    "_FIXTURE_STAGING_ROOT",
                    staging_root,
                ),
                mock.patch.object(
                    browser_gate,
                    "_FIXTURE_STAGING_BYTE_LIMIT",
                    8,
                ),
                self.assertRaisesRegex(RuntimeError, "byte limit exceeded"),
            ):
                fixture._prepare_fixture_script()

            self.assertEqual([], list(staging_root.iterdir()))
            self.assertIsNone(fixture._staged_candidate_root)
            self.assertIsNone(fixture._staged_fixture_script)

    def test_candidate_fixture_removes_tree_after_integrity_failure(
        self,
    ) -> None:
        with (
            tempfile.TemporaryDirectory() as source_directory,
            tempfile.TemporaryDirectory() as staging_directory,
        ):
            source_root = Path(source_directory).resolve()
            trusted_fixture = source_root / "fixture.py"
            trusted_fixture.write_text("# ok\n", encoding="utf-8")
            candidate_root = source_root / "candidate"
            package_root = candidate_root / "starcraft_commander"
            package_root.mkdir(parents=True)
            package_root.joinpath("__init__.py").write_text("", encoding="utf-8")
            package_root.joinpath("web_gui.py").write_text("", encoding="utf-8")
            staging_root = Path(staging_directory).resolve()
            staging_root.chmod(0o755)
            fixture = _CandidateFixtureProcess(
                BrowserGateConfig(
                    repository_sha=REPOSITORY_SHA,
                    build_identity=BUILD_IDENTITY,
                    artifact_dir=source_root / "artifacts",
                    candidate_root=candidate_root,
                    candidate_uid=65001,
                    candidate_gid=65001,
                )
            )

            with (
                mock.patch.object(browser_gate, "__file__", str(trusted_fixture)),
                mock.patch.object(
                    browser_gate,
                    "_FIXTURE_STAGING_ROOT",
                    staging_root,
                ),
            ):
                staged_script = fixture._prepare_fixture_script()
                staged_root = fixture._staged_candidate_root
                assert staged_root is not None
                staged_script.chmod(0o600)
                staged_script.write_text("# changed\n", encoding="utf-8")
                with self.assertRaisesRegex(
                    RuntimeError,
                    "integrity checks",
                ):
                    fixture._cleanup_fixture_script()

            self.assertFalse(staged_root.exists())
            self.assertIsNone(fixture._staged_candidate_root)
            self.assertIsNone(fixture._staged_fixture_script)

    def test_candidate_fixture_verification_closes_scandir_on_failure(
        self,
    ) -> None:
        with (
            tempfile.TemporaryDirectory() as source_directory,
            tempfile.TemporaryDirectory() as staging_directory,
        ):
            source_root = Path(source_directory).resolve()
            trusted_fixture = source_root / "fixture.py"
            trusted_fixture.write_text("# ok\n", encoding="utf-8")
            candidate_root = source_root / "candidate"
            package_root = candidate_root / "starcraft_commander"
            package_root.mkdir(parents=True)
            package_root.joinpath("__init__.py").write_text("", encoding="utf-8")
            package_root.joinpath("web_gui.py").write_text("", encoding="utf-8")
            staging_root = Path(staging_directory).resolve()
            staging_root.chmod(0o755)
            fixture = _CandidateFixtureProcess(
                BrowserGateConfig(
                    repository_sha=REPOSITORY_SHA,
                    build_identity=BUILD_IDENTITY,
                    artifact_dir=source_root / "artifacts",
                    candidate_root=candidate_root,
                    candidate_uid=65001,
                    candidate_gid=65001,
                )
            )

            with (
                mock.patch.object(browser_gate, "__file__", str(trusted_fixture)),
                mock.patch.object(
                    browser_gate,
                    "_FIXTURE_STAGING_ROOT",
                    staging_root,
                ),
            ):
                staged_script = fixture._prepare_fixture_script()
                staged_script.chmod(0o600)
                real_scandir = os.scandir
                scans: list[object] = []

                class RecordingScandir:
                    def __init__(self, directory: Path) -> None:
                        self._inner = real_scandir(directory)
                        self.closed = False

                    def __enter__(self) -> RecordingScandir:
                        return self

                    def __exit__(self, *args: object) -> object:
                        self.closed = True
                        return self._inner.__exit__(*args)

                    def __iter__(self) -> RecordingScandir:
                        return self

                    def __next__(self) -> os.DirEntry[str]:
                        return next(self._inner)

                def recording_scandir(directory: Path) -> RecordingScandir:
                    scan = RecordingScandir(directory)
                    scans.append(scan)
                    return scan

                with (
                    mock.patch.object(
                        browser_gate.os,
                        "scandir",
                        recording_scandir,
                    ),
                    self.assertRaisesRegex(
                        RuntimeError,
                        "integrity checks",
                    ),
                ):
                    fixture._cleanup_fixture_script()

            self.assertGreaterEqual(len(scans), 1)
            self.assertTrue(scans[0].closed)

    def test_candidate_fixture_cleanup_can_retry_after_delete_failure(
        self,
    ) -> None:
        with (
            tempfile.TemporaryDirectory() as source_directory,
            tempfile.TemporaryDirectory() as staging_directory,
        ):
            source_root = Path(source_directory).resolve()
            trusted_fixture = source_root / "fixture.py"
            trusted_fixture.write_text("# ok\n", encoding="utf-8")
            candidate_root = source_root / "candidate"
            package_root = candidate_root / "starcraft_commander"
            package_root.mkdir(parents=True)
            package_root.joinpath("__init__.py").write_text("", encoding="utf-8")
            package_root.joinpath("web_gui.py").write_text("", encoding="utf-8")
            staging_root = Path(staging_directory).resolve()
            staging_root.chmod(0o755)
            fixture = _CandidateFixtureProcess(
                BrowserGateConfig(
                    repository_sha=REPOSITORY_SHA,
                    build_identity=BUILD_IDENTITY,
                    artifact_dir=source_root / "artifacts",
                    candidate_root=candidate_root,
                    candidate_uid=65001,
                    candidate_gid=65001,
                )
            )

            with (
                mock.patch.object(browser_gate, "__file__", str(trusted_fixture)),
                mock.patch.object(
                    browser_gate,
                    "_FIXTURE_STAGING_ROOT",
                    staging_root,
                ),
            ):
                fixture._prepare_fixture_script()
                staged_root = fixture._staged_candidate_root
                assert staged_root is not None
                with (
                    mock.patch.object(
                        fixture,
                        "_discard_staged_fixture_tree",
                        side_effect=OSError("delete failed"),
                    ),
                    self.assertRaisesRegex(OSError, "delete failed"),
                ):
                    fixture._cleanup_fixture_script()

                self.assertEqual(staged_root, fixture._staged_candidate_root)
                fixture._cleanup_fixture_script()

            self.assertFalse(staged_root.exists())
            self.assertIsNone(fixture._staged_candidate_root)
            self.assertIsNone(fixture._staged_fixture_script)

    def test_candidate_fixture_cleanup_retry_skips_partial_tree_verification(
        self,
    ) -> None:
        with (
            tempfile.TemporaryDirectory() as source_directory,
            tempfile.TemporaryDirectory() as staging_directory,
        ):
            source_root = Path(source_directory).resolve()
            trusted_fixture = source_root / "fixture.py"
            trusted_fixture.write_text("# ok\n", encoding="utf-8")
            candidate_root = source_root / "candidate"
            package_root = candidate_root / "starcraft_commander"
            package_root.mkdir(parents=True)
            package_root.joinpath("__init__.py").write_text("", encoding="utf-8")
            package_root.joinpath("web_gui.py").write_text("", encoding="utf-8")
            staging_root = Path(staging_directory).resolve()
            staging_root.chmod(0o755)
            fixture = _CandidateFixtureProcess(
                BrowserGateConfig(
                    repository_sha=REPOSITORY_SHA,
                    build_identity=BUILD_IDENTITY,
                    artifact_dir=source_root / "artifacts",
                    candidate_root=candidate_root,
                    candidate_uid=65001,
                    candidate_gid=65001,
                )
            )

            with (
                mock.patch.object(browser_gate, "__file__", str(trusted_fixture)),
                mock.patch.object(
                    browser_gate,
                    "_FIXTURE_STAGING_ROOT",
                    staging_root,
                ),
            ):
                fixture._prepare_fixture_script()
                staged_root = fixture._staged_candidate_root
                assert staged_root is not None
                real_discard = fixture._discard_staged_fixture_tree
                discard_attempts = 0

                def partially_failing_discard(root: Path) -> None:
                    nonlocal discard_attempts
                    discard_attempts += 1
                    if discard_attempts == 1:
                        package = root / "starcraft_commander"
                        package.chmod(0o700)
                        package.joinpath("web_gui.py").unlink()
                        raise OSError("partial delete failed")
                    real_discard(root)

                with mock.patch.object(
                    fixture,
                    "_discard_staged_fixture_tree",
                    side_effect=partially_failing_discard,
                ):
                    with self.assertRaisesRegex(
                        OSError,
                        "partial delete failed",
                    ):
                        fixture._cleanup_fixture_script()
                    fixture._cleanup_fixture_script()

            self.assertEqual(2, discard_attempts)
            self.assertFalse(staged_root.exists())
            self.assertIsNone(fixture._staged_candidate_root)
            self.assertIsNone(fixture._staged_fixture_script)

    def test_real_linux_sudo_candidate_process_boundary(self) -> None:
        if os.environ.get("VOI_REQUIRE_LINUX_SUDO_PID_TEST") != "1":
            self.skipTest("dedicated Linux sudo PID test runs in hosted CI")
        self.assertEqual("linux", sys.platform)
        candidate_uid = int(os.environ["VOI_TEST_CANDIDATE_UID"])
        candidate_gid = int(os.environ["VOI_TEST_CANDIDATE_GID"])
        candidate_python = Path("/usr/bin/python3").resolve()
        self.assertTrue(candidate_python.is_file())
        self.assertFalse(candidate_python.is_symlink())

        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            staging_root = Path(directory).resolve()
            staging_root.chmod(0o755)
            fixture = _CandidateFixtureProcess(
                BrowserGateConfig(
                    repository_sha=REPOSITORY_SHA,
                    build_identity=BUILD_IDENTITY,
                    artifact_dir=staging_root / "artifacts",
                    candidate_python=candidate_python,
                    candidate_uid=candidate_uid,
                    candidate_gid=candidate_gid,
                )
            )

            with mock.patch.object(
                browser_gate,
                "_FIXTURE_STAGING_ROOT",
                staging_root,
            ):
                try:
                    origin = fixture.start()
                    self.assertRegex(
                        origin,
                        r"^http://127[.]0[.]0[.]1:[0-9]+/$",
                    )
                    fixture.assert_quiet()
                finally:
                    fixture.stop()

            self.assertEqual([], list(staging_root.iterdir()))

    def test_candidate_fixture_cleans_staged_source_when_spawn_fails(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging_root = Path(directory).resolve()
            staging_root.chmod(0o755)
            config = BrowserGateConfig(
                repository_sha=REPOSITORY_SHA,
                build_identity=BUILD_IDENTITY,
                artifact_dir=staging_root,
                candidate_uid=65001,
                candidate_gid=65001,
            )
            fixture = _CandidateFixtureProcess(config)

            with (
                mock.patch.object(
                    browser_gate,
                    "_FIXTURE_STAGING_ROOT",
                    staging_root,
                ),
                mock.patch.object(
                    browser_gate.subprocess,
                    "Popen",
                    side_effect=OSError("spawn failed"),
                ),
                self.assertRaisesRegex(OSError, "spawn failed"),
            ):
                fixture.start()

            self.assertEqual([], list(staging_root.iterdir()))
            self.assertIsNone(fixture._staged_fixture_script)

    def test_candidate_fixture_cleans_staged_source_when_handshake_fails(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging_root = Path(directory).resolve()
            staging_root.chmod(0o755)
            config = BrowserGateConfig(
                repository_sha=REPOSITORY_SHA,
                build_identity=BUILD_IDENTITY,
                artifact_dir=staging_root,
                candidate_uid=65001,
                candidate_gid=65001,
            )
            fixture = _CandidateFixtureProcess(config)
            process = mock.Mock(pid=4321, stdout=None, stderr=None)
            process.poll.return_value = 1
            process.stdin.write.side_effect = BrokenPipeError("handshake failed")

            with (
                mock.patch.object(
                    browser_gate,
                    "_FIXTURE_STAGING_ROOT",
                    staging_root,
                ),
                mock.patch.object(
                    browser_gate.subprocess,
                    "Popen",
                    return_value=process,
                ),
                mock.patch.object(fixture, "_cleanup_dedicated_uid"),
                self.assertRaisesRegex(BrokenPipeError, "handshake failed"),
            ):
                fixture.start()

            self.assertEqual([], list(staging_root.iterdir()))
            self.assertIsNone(fixture._staged_fixture_script)
            self.assertIsNone(fixture._process)

    def test_candidate_fixture_cleans_staged_source_when_uid_cleanup_fails(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging_root = Path(directory).resolve()
            staging_root.chmod(0o755)
            config = BrowserGateConfig(
                repository_sha=REPOSITORY_SHA,
                build_identity=BUILD_IDENTITY,
                artifact_dir=staging_root,
                candidate_uid=65001,
                candidate_gid=65001,
            )
            fixture = _CandidateFixtureProcess(config)
            process = mock.Mock(pid=4321, stdout=None, stderr=None)
            process.poll.return_value = 1
            fixture._process = process

            with mock.patch.object(
                browser_gate,
                "_FIXTURE_STAGING_ROOT",
                staging_root,
            ):
                fixture._prepare_fixture_script()
            with (
                mock.patch.object(
                    fixture,
                    "_cleanup_dedicated_uid",
                    side_effect=RuntimeError("uid cleanup failed"),
                ),
                self.assertRaisesRegex(RuntimeError, "uid cleanup failed"),
            ):
                fixture.stop()

            self.assertEqual([], list(staging_root.iterdir()))
            self.assertIsNone(fixture._staged_fixture_script)
            self.assertIsNone(fixture._process)

    def test_candidate_fixture_cleans_up_after_invalid_readiness_origin(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging_root = Path(directory).resolve()
            staging_root.chmod(0o755)
            config = BrowserGateConfig(
                repository_sha=REPOSITORY_SHA,
                build_identity=BUILD_IDENTITY,
                artifact_dir=staging_root,
                candidate_uid=65001,
                candidate_gid=65001,
            )
            fixture = _CandidateFixtureProcess(config)
            process = mock.Mock(pid=4321)
            process.poll.return_value = None
            process.stdout.readline.return_value = (
                '{"candidate_sha":"'
                + REPOSITORY_SHA
                + '","nonce":"'
                + fixture._nonce
                + '","origin":"http://127.0.0.1:99999/",'
                '"pid":4321,"schema_version":1,'
                '"type":"battlefield-webgui-ready"}\n'
            )
            selector = mock.Mock()
            selector.select.return_value = [(process.stdout, 1)]

            with (
                mock.patch.object(
                    browser_gate,
                    "_FIXTURE_STAGING_ROOT",
                    staging_root,
                ),
                mock.patch.object(
                    browser_gate.subprocess,
                    "Popen",
                    return_value=process,
                ),
                mock.patch.object(
                    browser_gate.selectors,
                    "DefaultSelector",
                    return_value=selector,
                ),
                mock.patch.object(fixture, "_start_drain"),
                mock.patch.object(fixture, "_signal_group"),
                mock.patch.object(fixture, "_cleanup_dedicated_uid"),
                self.assertRaisesRegex(
                    RuntimeError,
                    "readiness contract failed",
                ),
            ):
                fixture.start()

            self.assertEqual([], list(staging_root.iterdir()))
            self.assertIsNone(fixture._staged_fixture_script)
            self.assertIsNone(fixture._process)
            selector.close.assert_called_once_with()

    def test_candidate_fixture_retains_process_when_forced_stop_times_out(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging_root = Path(directory).resolve()
            staging_root.chmod(0o755)
            config = BrowserGateConfig(
                repository_sha=REPOSITORY_SHA,
                build_identity=BUILD_IDENTITY,
                artifact_dir=staging_root,
                candidate_uid=65001,
                candidate_gid=65001,
            )
            fixture = _CandidateFixtureProcess(config)
            process = mock.Mock(pid=4321, stdout=None, stderr=None)
            process.poll.return_value = None
            process.wait.side_effect = subprocess.TimeoutExpired(
                cmd="candidate fixture",
                timeout=browser_gate._FIXTURE_STOP_TIMEOUT_SECONDS,
            )
            fixture._process = process

            with mock.patch.object(
                browser_gate,
                "_FIXTURE_STAGING_ROOT",
                staging_root,
            ):
                fixture._prepare_fixture_script()
            with (
                mock.patch.object(fixture, "_signal_group") as signal_group,
                mock.patch.object(
                    fixture,
                    "_cleanup_dedicated_uid",
                ) as cleanup_uid,
                self.assertRaises(subprocess.TimeoutExpired),
            ):
                fixture.stop()

            self.assertEqual(
                [
                    mock.call(process, signal.SIGTERM),
                    mock.call(process, signal.SIGKILL),
                ],
                signal_group.call_args_list,
            )
            self.assertEqual(3, process.wait.call_count)
            cleanup_uid.assert_called_once_with()
            self.assertIs(process, fixture._process)
            self.assertEqual([], list(staging_root.iterdir()))
            self.assertIsNone(fixture._staged_fixture_script)

    def test_candidate_fixture_stop_signals_original_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = BrowserGateConfig(
                repository_sha=REPOSITORY_SHA,
                build_identity=BUILD_IDENTITY,
                artifact_dir=Path(directory),
            )
            fixture = _CandidateFixtureProcess(config)
            process = mock.Mock(pid=4321, stdout=None, stderr=None)
            process.poll.return_value = None
            fixture._process = process

            with (
                mock.patch.object(fixture, "_signal_group") as signal_group,
                mock.patch.object(fixture, "_cleanup_dedicated_uid"),
            ):
                fixture.stop()

            signal_group.assert_called_once_with(process, signal.SIGTERM)
            process.wait.assert_called_once_with(
                timeout=browser_gate._FIXTURE_STOP_TIMEOUT_SECONDS
            )
            self.assertIsNone(fixture._process)

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

    def test_visible_structure_requires_rendered_lanes_cards_stages_and_actions(
        self,
    ) -> None:
        result = _assert_visible_structure(_visible_structure())

        self.assertEqual(
            {
                "actions": 20,
                "all_visible": True,
                "cards": 4,
                "lanes": 4,
                "stages": 16,
            },
            result,
        )

        mutations = (
            (
                "hidden lane",
                lambda structure: structure["lanes"][0].update(hidden=True),
            ),
            (
                "display-none card",
                lambda structure: structure["cards"][0]["visibility"].update(
                    display="none"
                ),
            ),
            (
                "zero-size stage",
                lambda structure: structure["cards"][0]["stages"][0].update(
                    height=0
                ),
            ),
            (
                "non-rendered action",
                lambda structure: structure["cards"][0]["actions"][0].update(
                    client_rects=0
                ),
            ),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                structure = _visible_structure()
                mutate(structure)
                with self.assertRaisesRegex(AssertionError, "not visible"):
                    _assert_visible_structure(structure)

    def test_visual_diff_threshold_is_one_percent(self) -> None:
        self.assertEqual(0.01, VISUAL_DIFF_THRESHOLD)
        self.assertLessEqual(VISUAL_DIFF_THRESHOLD, 0.01)

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
