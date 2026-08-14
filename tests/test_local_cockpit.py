"""Tests for the idempotent local MyProxy cockpit bootstrap."""

from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import signal
import tempfile
import unittest
from unittest import mock

from starcraft_commander.local_cockpit import (
    DEFAULT_BUILD_JOBS,
    DEFAULT_MYPROXY_TIMEOUT_SECONDS,
    DEFAULT_SC2_API_PORT,
    LocalCockpitPaths,
    MyProxySettings,
    REQUIRED_SC2_BASE,
    child_environment,
    cockpit_is_ready,
    ensure_python_environment,
    ensure_micromachine_build,
    install_macos_application,
    install_local_runtime,
    micromachine_build_ready,
    read_codex_myproxy_settings,
    read_local_secret,
    read_sc2_launch_receipt,
    resolve_required_sc2_executable,
    resolve_myproxy_key,
    _sc2_receipt_process_matches,
    _stop_owned_app,
    _stop_owned_cockpit,
    store_local_secret,
)
from starcraft_commander.micromachine_build_identity import (
    build_runtime_install_provenance,
    write_runtime_install_provenance,
)
from starcraft_commander.llm_interpreter import (
    LLM_TIMEOUT_SECONDS_ENV_VAR,
    MYPROXY_API_KEY_ENV_VAR,
    MYPROXY_MODEL_ENV_VAR,
    MYPROXY_OPENAI_BASE_URL_ENV_VAR,
)


class LocalCockpitTest(unittest.TestCase):
    @staticmethod
    def _write_sc2_launch_receipt(
        path: Path,
        executable: Path,
        *,
        now_unix: float = 1_700_000_000.0,
        **overrides: object,
    ) -> dict[str, object]:
        document: dict[str, object] = {
            "schema": "voi-sc2-visible-launch/v1",
            "nonce": "unit-test-nonce",
            "created_at_unix_ms": int(now_unix * 1000),
            "accepted": True,
            "bootstrap_accepted": True,
            "pid": 4321,
            "port": DEFAULT_SC2_API_PORT,
            "base": REQUIRED_SC2_BASE,
            "executable_path": str(executable),
            "process_created": True,
            "api_ready": True,
            "window_created": True,
            "window_onscreen": True,
            "frontmost": True,
            "screen_locked": False,
            "screen_capture_authorized": False,
            "render_verified": False,
            "window_id": 99,
            "window_width": 1280,
            "window_height": 720,
        }
        document.update(overrides)
        path.write_text(json.dumps(document), encoding="utf-8")
        path.chmod(0o600)
        return document

    def test_reads_active_codex_myproxy_settings_without_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text(
                'model = "private-test-model"\n'
                'model_provider = "myproxy"\n'
                "[model_providers.myproxy]\n"
                'base_url = "https://proxy.test.invalid/v1"\n'
                'env_key = "MYPROXY_API_KEY"\n',
                encoding="utf-8",
            )

            settings = read_codex_myproxy_settings(config)

        self.assertEqual("private-test-model", settings.model)
        self.assertEqual("https://proxy.test.invalid/v1", settings.base_url)
        self.assertEqual("MYPROXY_API_KEY", settings.env_key)
        self.assertNotIn("api_key", settings.__dict__)

    def test_rejects_remote_plain_http_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text(
                'model = "private-test-model"\n'
                'model_provider = "myproxy"\n'
                "[model_providers.myproxy]\n"
                'base_url = "http://proxy.test.invalid/v1"\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "HTTPS"):
                read_codex_myproxy_settings(config)

    def test_environment_key_precedes_local_storage_and_child_env_is_complete(self) -> None:
        settings = MyProxySettings(
            model="private-test-model",
            base_url="https://proxy.test.invalid/v1",
            env_key="CUSTOM_PROXY_KEY",
        )
        environment = {"CUSTOM_PROXY_KEY": "unit-test-secret"}
        with mock.patch(
            "starcraft_commander.local_cockpit.read_local_secret",
            side_effect=AssertionError("Local storage should not be read"),
        ):
            secret = resolve_myproxy_key(
                settings,
                environment,
                local_secret_path=Path("/unused"),
            )
        child = child_environment(settings, secret, environment)

        self.assertEqual("unit-test-secret", child[MYPROXY_API_KEY_ENV_VAR])
        self.assertEqual("private-test-model", child[MYPROXY_MODEL_ENV_VAR])
        self.assertEqual(
            "https://proxy.test.invalid/v1",
            child[MYPROXY_OPENAI_BASE_URL_ENV_VAR],
        )
        self.assertEqual(str(DEFAULT_BUILD_JOBS), child["BUILD_JOBS"])
        self.assertEqual(
            str(DEFAULT_MYPROXY_TIMEOUT_SECONDS),
            child[LLM_TIMEOUT_SECONDS_ENV_VAR],
        )

    def test_local_secret_store_is_owner_only_and_reusable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "myproxy.key"
            store_local_secret("unit-test-secret", path)

            self.assertEqual("unit-test-secret", read_local_secret(path))
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
            self.assertEqual(0o700, path.parent.stat().st_mode & 0o777)

    def test_local_secret_reader_rejects_broad_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "myproxy.key"
            path.write_text("unit-test-secret\n", encoding="utf-8")
            path.chmod(0o644)

            with self.assertRaisesRegex(RuntimeError, "0600"):
                read_local_secret(path)

    def test_sc2_launch_receipt_accepts_complete_live_bootstrap_proof(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "SC2"
            executable.write_text("", encoding="ascii")
            receipt = root / "sc2-launch-receipt.json"
            expected = self._write_sc2_launch_receipt(receipt, executable)
            with (
                mock.patch(
                    "starcraft_commander.local_cockpit.resolve_required_sc2_executable",
                    return_value=executable,
                ),
                mock.patch(
                    "starcraft_commander.local_cockpit._sc2_receipt_process_matches",
                    return_value=True,
                ) as process_matches,
            ):
                actual = read_sc2_launch_receipt(
                    receipt,
                    "unit-test-nonce",
                    now_unix=1_700_000_000.0,
                )

        self.assertEqual(expected, actual)
        process_matches.assert_called_once_with(
            4321,
            executable,
            DEFAULT_SC2_API_PORT,
        )

    def test_sc2_process_match_preserves_spaces_in_executable_path(self) -> None:
        executable = Path(
            "/Users/test/Desktop/StarCraft2/StarCraft II/"
            "Versions/Base97364/SC2.app/Contents/MacOS/SC2"
        )
        command = (
            f"{executable} -listen 127.0.0.1 "
            f"-port {DEFAULT_SC2_API_PORT} -displayMode 0\n"
        )
        with mock.patch(
            "starcraft_commander.local_cockpit.subprocess.run",
            side_effect=(
                mock.Mock(returncode=0, stdout=f"{executable}\n"),
                mock.Mock(returncode=0, stdout=command),
            ),
        ):
            matches = _sc2_receipt_process_matches(
                4321,
                executable,
                DEFAULT_SC2_API_PORT,
            )

        self.assertTrue(matches)

    def test_sc2_launch_receipt_rejects_stale_nonce_and_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "SC2"
            receipt = root / "sc2-launch-receipt.json"
            self._write_sc2_launch_receipt(receipt, executable)
            with self.assertRaisesRegex(RuntimeError, "nonce does not match"):
                read_sc2_launch_receipt(
                    receipt,
                    "different-nonce",
                    now_unix=1_700_000_000.0,
                    require_live_process=False,
                )

            self._write_sc2_launch_receipt(
                receipt,
                executable,
                now_unix=1_699_999_000.0,
            )
            with self.assertRaisesRegex(RuntimeError, "stale"):
                read_sc2_launch_receipt(
                    receipt,
                    "unit-test-nonce",
                    now_unix=1_700_000_000.0,
                    require_live_process=False,
                )

            self._write_sc2_launch_receipt(receipt, executable)
            receipt.chmod(0o644)
            with self.assertRaisesRegex(RuntimeError, "0600"):
                read_sc2_launch_receipt(
                    receipt,
                    "unit-test-nonce",
                    now_unix=1_700_000_000.0,
                    require_live_process=False,
                )

    def test_sc2_launch_receipt_rejects_missing_bootstrap_or_live_proof(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "SC2"
            receipt = root / "sc2-launch-receipt.json"
            with mock.patch(
                "starcraft_commander.local_cockpit.resolve_required_sc2_executable",
                return_value=executable,
            ):
                for field_name, value, expected_error in (
                    ("process_created", False, "process_created"),
                    ("api_ready", False, "api_ready"),
                    ("window_created", False, "window_created"),
                    ("window_onscreen", False, "window_onscreen"),
                    ("frontmost", False, "frontmost"),
                    ("screen_locked", True, "screen_unlocked"),
                ):
                    with self.subTest(field_name=field_name):
                        self._write_sc2_launch_receipt(
                            receipt,
                            executable,
                            **{field_name: value},
                        )
                        with self.assertRaisesRegex(
                            RuntimeError,
                            expected_error,
                        ):
                            read_sc2_launch_receipt(
                                receipt,
                                "unit-test-nonce",
                                now_unix=1_700_000_000.0,
                                require_live_process=False,
                            )

                self._write_sc2_launch_receipt(receipt, executable)
                with (
                    mock.patch(
                        "starcraft_commander.local_cockpit._sc2_receipt_process_matches",
                        return_value=False,
                    ),
                    self.assertRaisesRegex(RuntimeError, "no longer live"),
                ):
                    read_sc2_launch_receipt(
                        receipt,
                        "unit-test-nonce",
                        now_unix=1_700_000_000.0,
                    )

    def test_sc2_launch_receipt_allows_optional_render_fields_to_be_false_or_missing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "SC2"
            receipt = root / "sc2-launch-receipt.json"
            self._write_sc2_launch_receipt(
                receipt,
                executable,
                screen_capture_authorized=False,
                render_verified=False,
            )
            with (
                mock.patch(
                    "starcraft_commander.local_cockpit.resolve_required_sc2_executable",
                    return_value=executable,
                ),
                mock.patch(
                    "starcraft_commander.local_cockpit._sc2_receipt_process_matches",
                    return_value=True,
                ),
            ):
                rendered_false = read_sc2_launch_receipt(
                    receipt,
                    "unit-test-nonce",
                    now_unix=1_700_000_000.0,
                )
                self.assertFalse(rendered_false["screen_capture_authorized"])
                self.assertFalse(rendered_false["render_verified"])

                without_render_fields = dict(rendered_false)
                without_render_fields.pop("screen_capture_authorized")
                without_render_fields.pop("render_verified")
                receipt.write_text(
                    json.dumps(without_render_fields),
                    encoding="utf-8",
                )
                receipt.chmod(0o600)

                rendered_missing = read_sc2_launch_receipt(
                    receipt,
                    "unit-test-nonce",
                    now_unix=1_700_000_000.0,
                )

        self.assertNotIn("screen_capture_authorized", rendered_missing)
        self.assertNotIn("render_verified", rendered_missing)

    def test_build_is_skipped_when_binary_and_identity_are_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._paths(root)
            paths.micromachine_binary.parent.mkdir(parents=True)
            paths.micromachine_binary.write_text("binary", encoding="utf-8")
            paths.micromachine_binary.chmod(0o755)
            paths.build_identity_report.write_text(
                json.dumps({"ok": True}),
                encoding="utf-8",
            )
            with mock.patch(
                "starcraft_commander.local_cockpit.strict_micromachine_build_ready",
                return_value=True,
            ) as strict_ready:
                self.assertTrue(micromachine_build_ready(paths))

                with mock.patch(
                    "starcraft_commander.local_cockpit.subprocess.run"
                ) as run:
                    ensure_micromachine_build(paths)

        run.assert_not_called()
        strict_ready.assert_called()

    def test_build_rejects_bare_ok_receipt_when_strict_identity_is_stale(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._paths(Path(directory))
            paths.micromachine_binary.parent.mkdir(parents=True)
            paths.micromachine_binary.write_text("binary", encoding="utf-8")
            paths.micromachine_binary.chmod(0o755)
            paths.build_identity_report.write_text(
                json.dumps({"ok": True}),
                encoding="utf-8",
            )
            with mock.patch(
                "starcraft_commander.local_cockpit.strict_micromachine_build_ready",
                return_value=False,
            ) as strict_ready:
                self.assertFalse(micromachine_build_ready(paths))

        strict_ready.assert_called_once()

    def test_missing_pip_is_repaired_before_llm_install(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._paths(Path(directory))
            paths.venv_python.parent.mkdir(parents=True)
            paths.venv_python.write_text("", encoding="utf-8")
            paths.venv_python.chmod(0o755)
            with (
                mock.patch(
                    "starcraft_commander.local_cockpit._python_has_pip",
                    return_value=False,
                ),
                mock.patch(
                    "starcraft_commander.local_cockpit._python_has_openai",
                    return_value=False,
                ),
                mock.patch(
                    "starcraft_commander.local_cockpit.subprocess.run"
                ) as run,
            ):
                ensure_python_environment(paths)

        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(
            [str(paths.venv_python), "-m", "ensurepip", "--upgrade"],
            commands[0],
        )
        self.assertEqual(
            [str(paths.venv_python), "-m", "pip", "install"],
            commands[1][:4],
        )

    def test_build_uses_low_memory_parallelism(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._paths(Path(directory))

            def complete_build(*args, **kwargs):
                paths.micromachine_binary.parent.mkdir(parents=True, exist_ok=True)
                paths.micromachine_binary.write_text("binary", encoding="utf-8")
                paths.micromachine_binary.chmod(0o755)
                paths.build_identity_report.write_text(
                    json.dumps({"ok": True}),
                    encoding="utf-8",
                )
                return mock.Mock(returncode=0)

            with (
                mock.patch(
                    "starcraft_commander.local_cockpit.strict_micromachine_build_ready",
                    return_value=True,
                ),
                mock.patch(
                    "starcraft_commander.local_cockpit.subprocess.run",
                    side_effect=complete_build,
                ) as run,
            ):
                ensure_micromachine_build(paths)

        self.assertEqual("2", run.call_args.kwargs["env"]["BUILD_JOBS"])

    def test_installed_app_contains_no_private_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._create_runtime_source(root)
            destination = root / "Applications" / "voiStarcraft2.app"
            runtime = root / "state" / "runtime"
            swiftc_commands: list[list[str]] = []
            compiled_launcher_sources: list[str] = []
            codesign_commands: list[list[str]] = []

            def run_install_command(
                command: list[str],
                **kwargs: object,
            ) -> mock.Mock:
                if command[0].endswith("/.venv/bin/python"):
                    return mock.Mock(returncode=0)
                if command[0] == "/usr/bin/swiftc":
                    launcher_input = Path(command[command.index("-o") - 1])
                    executable = Path(command[command.index("-o") + 1])
                    compiled_launcher_sources.append(
                        launcher_input.read_text(encoding="utf-8")
                    )
                    executable.write_bytes(b"mock compiled launcher\n")
                    executable.chmod(0o755)
                    swiftc_commands.append(command)
                    return mock.Mock(returncode=0, stderr="")
                if command[0] == "/usr/bin/codesign":
                    codesign_commands.append(command)
                    return mock.Mock(returncode=0, stderr="")
                raise AssertionError(f"Unexpected install command: {command}")

            with mock.patch(
                "starcraft_commander.local_cockpit.subprocess.run",
                side_effect=run_install_command,
            ):
                app_path = install_macos_application(
                    root,
                    destination=destination,
                    runtime_destination=runtime,
                )
            launcher = (
                app_path / "Contents" / "Resources" / "bootstrap.sh"
            ).read_text(encoding="utf-8")
            launcher_source_path = (
                app_path / "Contents" / "Resources" / "launcher.swift"
            )
            launcher_source = launcher_source_path.read_text(encoding="utf-8")
            with (app_path / "Contents" / "Info.plist").open("rb") as handle:
                info = plistlib.load(handle)
            executable = os.access(
                destination
                / "Contents"
                / "MacOS"
                / str(info["CFBundleExecutable"]),
                os.X_OK,
            )

        self.assertIn("start_local_cockpit.py", launcher)
        self.assertIn("--no-open", launcher)
        self.assertIn(str(runtime), launcher)
        self.assertIn("WKWebView", launcher_source)
        self.assertIn("WKScriptMessageHandler", launcher_source)
        self.assertIn('name: "sc2Launch"', launcher_source)
        self.assertIn("applicationShouldHandleReopen", launcher_source)
        self.assertIn(
            "applicationShouldTerminateAfterLastWindowClosed",
            launcher_source,
        )
        self.assertIn("return false", launcher_source)
        self.assertIn("app.pid", launcher_source)
        self.assertIn("http://127.0.0.1:8350", launcher_source)
        self.assertIn("--auto-start-micromachine", launcher_source)
        self.assertIn("--auto-command", launcher_source)
        self.assertIn("runtime-start-button", launcher_source)
        self.assertIn("form.requestSubmit()", launcher_source)
        self.assertIn("telemetry_current_for_process", launcher_source)
        self.assertIn('webView.url?.host == "127.0.0.1"', launcher_source)
        self.assertIn("hideCockpitAndFocusSC2", launcher_source)
        self.assertIn("window.orderOut(nil)", launcher_source)
        self.assertIn("bootstrap_accepted", launcher_source)
        self.assertNotIn("verifyRenderedSC2", launcher_source)
        self.assertIn("if state.accepted", launcher_source)
        self.assertRegex(
            launcher_source,
            r"var accepted: Bool \{\s+bootstrapAccepted\s+\}",
        )
        self.assertIn("selectedWidth >= 480", launcher_source)
        self.assertNotIn("CGPreflightScreenCaptureAccess()", launcher_source)
        self.assertNotIn("CGRequestScreenCaptureAccess()", launcher_source)
        self.assertNotIn("CGWindowListCreateImage", launcher_source)
        self.assertNotIn("CGColorSpaceCreateDeviceGray()", launcher_source)
        self.assertNotIn("CGImageAlphaInfo.none.rawValue", launcher_source)
        self.assertIn("layer == 0, alpha > 0.01", launcher_source)
        self.assertNotIn("화면 기록 권한이 없어", launcher_source)
        self.assertIn(
            "if companion, autoCommandArgument != nil",
            launcher_source,
        )
        self.assertIn("백엔드 준비를 계속 기다리고", launcher_source)
        self.assertNotIn("self.readinessAttempt >= 120", launcher_source)
        self.assertIn("NSWorkspace.OpenConfiguration", launcher_source)
        self.assertIn("NSWorkspace.shared.openApplication", launcher_source)
        self.assertIn(f"private let sc2Port = {DEFAULT_SC2_API_PORT}", launcher_source)
        self.assertIn(f"private let sc2Base = {REQUIRED_SC2_BASE}", launcher_source)
        for field_name in (
            "process_created",
            "api_ready",
            "window_created",
            "window_onscreen",
            "frontmost",
            "screen_locked",
            "screen_capture_authorized",
            "render_verified",
        ):
            self.assertIn(field_name, launcher_source)
        self.assertIn("posixPermissions: 0o600", launcher_source)
        self.assertNotIn(str(root / ".venv"), launcher)
        self.assertNotIn("VOI_MYPROXY", launcher)
        self.assertNotIn("MYPROXY_API_KEY", launcher)
        self.assertEqual("local.voi.starcraft2.cockpit", info["CFBundleIdentifier"])
        self.assertTrue(info["LSMultipleInstancesProhibited"])
        self.assertTrue(executable)
        self.assertEqual(1, len(swiftc_commands))
        self.assertEqual([launcher_source], compiled_launcher_sources)
        self.assertEqual(1, len(codesign_commands))

    def test_default_app_update_stops_owned_cockpit_before_runtime_replace(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                mock.patch(
                    "starcraft_commander.local_cockpit._stop_owned_app",
                    return_value=True,
                ),
                mock.patch(
                    "pathlib.Path.exists",
                    return_value=True,
                ),
                mock.patch(
                    "starcraft_commander.local_cockpit._stop_owned_cockpit",
                    return_value=True,
                ) as stop,
                mock.patch(
                    "starcraft_commander.local_cockpit._port_is_bound",
                    return_value=False,
                ),
                mock.patch(
                    "starcraft_commander.local_cockpit.install_local_runtime",
                    side_effect=RuntimeError("stop after restart check"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "restart check"):
                    install_macos_application(root)

        stop.assert_called_once()
        self.assertIsNone(stop.call_args.args[1])

    def test_stops_only_recorded_matching_app(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._paths(root)
            app_path = root / "Applications" / "voiStarcraft2.app"
            executable = app_path / "Contents" / "MacOS" / "voiStarcraft2"
            paths.state_dir.mkdir(parents=True)
            (paths.state_dir / "app.pid").write_text("4321\n", encoding="ascii")
            with (
                mock.patch(
                    "starcraft_commander.local_cockpit.subprocess.run",
                    return_value=mock.Mock(
                        returncode=0,
                        stdout=str(executable.resolve()),
                    ),
                ),
                mock.patch(
                    "starcraft_commander.local_cockpit.os.kill",
                    side_effect=[None, ProcessLookupError()],
                ) as kill_process,
            ):
                stopped = _stop_owned_app(paths, app_path)

        self.assertTrue(stopped)
        self.assertEqual(mock.call(4321, signal.SIGTERM), kill_process.call_args_list[0])

    def test_stops_matching_app_started_with_runtime_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._paths(root)
            app_path = root / "Applications" / "voiStarcraft2.app"
            executable = app_path / "Contents" / "MacOS" / "voiStarcraft2"
            paths.state_dir.mkdir(parents=True)
            (paths.state_dir / "app.pid").write_text("4321\n", encoding="ascii")

            def inspect_process(command, **_kwargs):
                field = command[-1]
                stdout = (
                    str(executable.resolve())
                    if field == "comm="
                    else f"{executable.resolve()} --auto-start-micromachine"
                )
                return mock.Mock(returncode=0, stdout=stdout)

            with (
                mock.patch(
                    "starcraft_commander.local_cockpit.subprocess.run",
                    side_effect=inspect_process,
                ),
                mock.patch(
                    "starcraft_commander.local_cockpit.os.kill",
                    side_effect=[None, ProcessLookupError()],
                ) as kill_process,
            ):
                stopped = _stop_owned_app(paths, app_path)

        self.assertTrue(stopped)
        self.assertEqual(mock.call(4321, signal.SIGTERM), kill_process.call_args_list[0])

    def test_cockpit_health_requires_configured_and_available_llm(self) -> None:
        class Response:
            status = 200

            def __init__(self, payload: dict[str, object]) -> None:
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self) -> bytes:
                return json.dumps(self.payload).encode("utf-8")

        for payload, expected in (
            ({"configured": True, "available": True}, True),
            ({"configured": True, "available": False}, False),
            ({"configured": True}, False),
        ):
            with self.subTest(payload=payload), mock.patch(
                "starcraft_commander.local_cockpit.urllib.request.urlopen",
                return_value=Response(payload),
            ):
                self.assertEqual(cockpit_is_ready(8350), expected)

    def test_stops_only_recorded_matching_unhealthy_cockpit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._paths(Path(directory))
            paths.state_dir.mkdir(parents=True)
            (paths.state_dir / "cockpit.pid").write_text("4321\n", encoding="ascii")
            command = (
                "python -m starcraft_commander.web_gui --dry-run --port 8350 "
                f"--micromachine-cwd {paths.repo_root}"
            )
            with (
                mock.patch(
                    "starcraft_commander.local_cockpit.subprocess.run",
                    return_value=mock.Mock(returncode=0, stdout=command),
                ),
                mock.patch(
                    "starcraft_commander.local_cockpit.os.kill"
                ) as kill_process,
                mock.patch(
                    "starcraft_commander.local_cockpit._port_is_bound",
                    return_value=False,
                ),
            ):
                stopped = _stop_owned_cockpit(paths, 8350)

        self.assertTrue(stopped)
        kill_process.assert_called_once()

    def test_stops_recorded_cockpit_on_legacy_port_during_app_update(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._paths(Path(directory))
            paths.state_dir.mkdir(parents=True)
            (paths.state_dir / "cockpit.pid").write_text("4321\n", encoding="ascii")
            command = (
                "python -m starcraft_commander.web_gui --dry-run --port 8351 "
                f"--micromachine-cwd {paths.repo_root}"
            )
            with (
                mock.patch(
                    "starcraft_commander.local_cockpit.subprocess.run",
                    return_value=mock.Mock(returncode=0, stdout=command),
                ),
                mock.patch(
                    "starcraft_commander.local_cockpit.os.kill",
                    side_effect=[None, ProcessLookupError()],
                ) as kill_process,
            ):
                stopped = _stop_owned_cockpit(paths, None)

        self.assertTrue(stopped)
        self.assertEqual(
            mock.call(4321, signal.SIGTERM),
            kill_process.call_args_list[0],
        )

    def test_refuses_to_stop_pid_with_unrelated_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._paths(Path(directory))
            paths.state_dir.mkdir(parents=True)
            (paths.state_dir / "cockpit.pid").write_text("4321\n", encoding="ascii")
            with (
                mock.patch(
                    "starcraft_commander.local_cockpit.subprocess.run",
                    return_value=mock.Mock(
                        returncode=0,
                        stdout="python unrelated_server.py --port 8350",
                    ),
                ),
                mock.patch(
                    "starcraft_commander.local_cockpit.os.kill"
                ) as kill_process,
            ):
                stopped = _stop_owned_cockpit(paths, 8350)

        self.assertFalse(stopped)
        kill_process.assert_not_called()

    def test_local_runtime_removes_editable_checkout_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._create_runtime_source(root)
            destination = root / "installed" / "runtime"

            runtime = install_local_runtime(root, destination=destination)

            site_packages = runtime / ".venv" / "lib" / "python3.10" / "site-packages"
            self.assertFalse(tuple(site_packages.glob("__editable__*")))
            self.assertFalse(tuple(site_packages.glob("voistarcraft2-*.dist-info")))
            self.assertTrue(
                (
                    runtime
                    / "integrations"
                    / "micromachine"
                    / "scripts"
                    / "smoke_macos_local.sh"
                ).is_file()
            )

    @staticmethod
    def _create_runtime_source(root: Path) -> None:
        for directory in (
            "broodwar_commander",
            "integrations/micromachine/scripts",
            "scripts",
            "starcraft_commander",
            "toycraft_commander",
        ):
            (root / directory).mkdir(parents=True, exist_ok=True)
        for package in (
            "broodwar_commander",
            "integrations",
            "integrations/micromachine",
            "starcraft_commander",
            "toycraft_commander",
        ):
            (root / package / "__init__.py").write_text("", encoding="utf-8")
        for name in (
            "LICENSE",
            "MANIFEST.in",
            "README.md",
            "THIRD_PARTY_NOTICES.md",
            "pyproject.toml",
        ):
            (root / name).write_text(name, encoding="utf-8")
        smoke = (
            root
            / "integrations"
            / "micromachine"
            / "scripts"
            / "smoke_macos_local.sh"
        )
        smoke.write_text("#!/bin/bash\n", encoding="ascii")
        smoke.chmod(0o755)
        (root / "scripts" / "start_local_cockpit.py").write_text(
            "#!/usr/bin/env python3\n",
            encoding="ascii",
        )
        site_packages = (
            root / ".venv" / "lib" / "python3.10" / "site-packages"
        )
        site_packages.mkdir(parents=True)
        (site_packages / "__editable__.voistarcraft2-0.1.0.pth").write_text(
            str(root),
            encoding="utf-8",
        )
        editable = site_packages / "voistarcraft2-0.1.0.dist-info"
        editable.mkdir()
        (editable / "direct_url.json").write_text(
            json.dumps({"url": root.as_uri()}),
            encoding="utf-8",
        )
        python = root / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        python.chmod(0o755)
        provenance = build_runtime_install_provenance(
            root,
            repo_head_sha="a" * 40,
        )
        write_runtime_install_provenance(root, provenance)

    @staticmethod
    def _paths(root: Path) -> LocalCockpitPaths:
        build_dir = root / "runtime" / "build"
        return LocalCockpitPaths(
            repo_root=root,
            venv_python=root / ".venv" / "bin" / "python",
            build_script=root / "build.sh",
            micromachine_binary=build_dir / "bin" / "MicroMachine",
            build_identity_report=build_dir / "voi_build_identity.json",
            state_dir=root / "state",
            log_dir=root / "logs",
            secret_file=root / "state" / "myproxy.key",
        )


if __name__ == "__main__":
    unittest.main()
