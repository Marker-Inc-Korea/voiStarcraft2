"""Tests for live text-to-MicroMachine modulation sessions."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from starcraft_commander.micromachine_bridge import (
    MICROMACHINE_GAME_LOOPS_PER_SECOND,
    MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
    MicroMachineBlackboardUpdate,
    MicroMachineBridgeFailureMode,
    MicroMachineTelemetry,
)
from starcraft_commander.micromachine_live_session import (
    KeywordPolicyModulationProvider,
    LiveModulationConsumptionStatus,
    LiveModulationStatus,
    MicroMachineLiveTextSession,
    StaticJsonPolicyModulationProvider,
    main,
)
from starcraft_commander.micromachine_runtime import (
    LATEST_TELEMETRY_JSON_NAME,
    LATEST_UPDATE_JSON_NAME,
    LATEST_UPDATE_KV_NAME,
    MicroMachineFilesystemBlackboard,
    MicroMachineInMemoryBlackboard,
)
from starcraft_commander.policy_modulation_provider import (
    PolicyModulationCompileStatus,
)


class AutoConsumingBlackboard(MicroMachineInMemoryBlackboard):
    def publish_update(
        self,
        update: MicroMachineBlackboardUpdate,
        *,
        current_frame: int,
    ) -> MicroMachineBlackboardUpdate:
        accepted = super().publish_update(update, current_frame=current_frame)
        self.ingest_telemetry(
            MicroMachineTelemetry(
                frame=accepted.issued_at_frame + 1,
                active_modulation_ids=(accepted.update_id,),
            )
        )
        return accepted


class FailingPublishBlackboard(MicroMachineInMemoryBlackboard):
    def publish_vector(self, *args, **kwargs):
        raise OSError("blackboard directory unavailable")


class EventuallyReadableTelemetryBlackboard(MicroMachineInMemoryBlackboard):
    def __init__(self) -> None:
        super().__init__()
        self.read_count = 0

    def read_latest_telemetry(self) -> MicroMachineTelemetry | None:
        self.read_count += 1
        if self.read_count == 1:
            return None
        return MicroMachineTelemetry(frame=321)


class MicroMachineLiveTextSessionTest(unittest.TestCase):
    def test_keyword_provider_does_not_publish_plain_greeting(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text("안녕", current_frame=7, update_id="hello-noop")

        self.assertFalse(result.ok, result.to_dict())
        self.assertEqual(LiveModulationStatus.CLARIFICATION_REQUIRED, result.status)
        self.assertIsNone(result.update)
        self.assertIsNone(backend.latest_update)
        self.assertEqual(
            LiveModulationConsumptionStatus.NOT_PUBLISHED,
            result.consumption_status,
        )
        self.assertEqual(
            PolicyModulationCompileStatus.CLARIFICATION_REQUIRED,
            result.compile_result.status,
        )
        self.assertEqual("smoke_keyword", result.compile_result.source.value)
        self.assertEqual("smoke_keyword", result.to_dict()["provider_source"])
        self.assertIn("전술 의도", result.compile_result.clarification_prompt)

    def test_keyword_provider_maps_attack_intent_to_offensive_gate_biases(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text(
            "공격적으로 마린 탐색해서 적발견시 바로 공격해",
            current_frame=100,
            update_id="attack-now",
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.update)
        assert result.update is not None
        self.assertEqual("smoke_keyword", result.update.vector.source.value)
        self.assertEqual("smoke_keyword", result.to_dict()["provider_source"])
        vector = result.update.vector
        self.assertEqual("force_when_threshold_met", vector.combat.attack_condition_override)
        self.assertGreaterEqual(vector.combat.attack_timing_bias, 0.6)
        self.assertGreaterEqual(vector.combat.commitment_level, 0.5)
        self.assertGreaterEqual(vector.combat.retreat_patience_bias, 0.4)
        self.assertGreaterEqual(vector.squad.main_army_bias, 0.5)
        self.assertGreaterEqual(vector.squad.contain_bias, 0.3)
        self.assertEqual("main", vector.scope.army_group)
        self.assertEqual("enemy_natural", vector.scope.location_intent)
        self.assertEqual(1, vector.scope.min_units)
        self.assertGreaterEqual(vector.ttl_seconds, 600)
        self.assertGreaterEqual(vector.production.production_continuity_bias, 0.6)
        self.assertEqual(32, vector.workers.repeat_order_guard_frames)
        self.assertIn("workers", result.update.manager_bias_domains)
        self.assertIn("worker_line", vector.combat.target_priority_biases.to_dict())
        self.assertIn("aggressive_pressure", vector.tags)

    def test_live_provider_cannot_weaken_worker_repeat_order_guard(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "goal": "공격적으로 압박해",
                    "combat": {"aggression": 0.5},
                    "workers": {"repeat_order_guard_frames": 4},
                }
            ),
        )

        result = session.submit_text("공격적으로 압박해", current_frame=100)

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.update)
        assert result.update is not None
        self.assertEqual(32, result.update.vector.workers.repeat_order_guard_frames)
        self.assertIn("workers", result.update.manager_bias_domains)
        self.assertIn(
            "live_worker_repeat_order_guard_frames_clamped=4->32",
            result.compile_result.warnings,
        )

    def test_live_provider_can_strengthen_worker_repeat_order_guard(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "goal": "수비적으로 버텨",
                    "combat": {"defend_bias": 0.5},
                    "workers": {"repeat_order_guard_frames": 48},
                }
            ),
        )

        result = session.submit_text("수비적으로 버텨", current_frame=100)

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.update)
        assert result.update is not None
        self.assertEqual(48, result.update.vector.workers.repeat_order_guard_frames)

    def test_retries_transient_telemetry_read_before_issuing_live_update(self) -> None:
        backend = EventuallyReadableTelemetryBlackboard()
        session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {"goal": "공격적으로 압박해", "combat": {"aggression": 0.5}}
            ),
        )

        result = session.submit_text("공격적으로 압박해", update_id="frame-race")

        self.assertTrue(result.ok, result.to_dict())
        self.assertEqual(321, result.current_frame)
        self.assertIsNotNone(result.update)
        assert result.update is not None
        self.assertEqual(321, result.update.issued_at_frame)
        self.assertGreaterEqual(backend.read_count, 2)

    def test_text_provider_output_publishes_modulation_update(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        backend.ingest_telemetry(MicroMachineTelemetry(frame=42))
        session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "goal": "탱크 중심으로 안전하게 버텨",
                    "override_level": "constraint",
                    "confidence": 0.8,
                    "ttl_seconds": 90,
                    "posture": "defensive",
                    "combat": {"defend_bias": 0.7, "aggression": -0.2},
                    "tags": ["live_text"],
                }
            ),
        )

        result = session.submit_text(
            "탱크 중심으로 안전하게 버텨",
            update_id="live-42",
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertEqual(LiveModulationStatus.PUBLISHED, result.status)
        self.assertEqual(42, result.current_frame)
        self.assertIsNotNone(result.update)
        assert result.update is not None
        self.assertEqual("live-42", result.update.update_id)
        self.assertEqual(42, result.update.issued_at_frame)
        self.assertEqual(32, result.update.vector.workers.repeat_order_guard_frames)
        self.assertIn("workers", result.update.manager_bias_domains)
        self.assertEqual(
            42 + 90 * MICROMACHINE_GAME_LOOPS_PER_SECOND,
            result.update.expires_at_frame,
        )
        self.assertEqual(
            LiveModulationConsumptionStatus.PENDING_CONSUMPTION,
            result.consumption_status,
        )
        latest = backend.read_latest_update(current_frame=42)
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual("live-42", latest.update_id)

    def test_does_not_report_consumed_from_pre_publish_telemetry(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        backend.ingest_telemetry(
            MicroMachineTelemetry(
                frame=100,
                active_modulation_ids=("known-live-id",),
            )
        )
        session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {"goal": "공격적으로 압박해", "combat": {"aggression": 0.5}}
            ),
        )

        result = session.submit_text("공격적으로 압박해", update_id="known-live-id")

        self.assertTrue(result.ok, result.to_dict())
        self.assertFalse(result.consumed)
        self.assertEqual(
            LiveModulationConsumptionStatus.PENDING_CONSUMPTION,
            result.consumption_status,
        )

    def test_reports_consumed_only_from_post_publish_telemetry(self) -> None:
        backend = AutoConsumingBlackboard()
        session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {"goal": "공격적으로 압박해", "combat": {"aggression": 0.5}}
            ),
        )

        consumed = session.submit_text(
            "공격적으로 압박해",
            current_frame=100,
            update_id="known-live-id",
        )

        self.assertTrue(consumed.ok, consumed.to_dict())
        self.assertTrue(consumed.consumed)
        self.assertEqual(
            LiveModulationConsumptionStatus.CONSUMED,
            consumed.consumption_status,
        )

    def test_oserror_publish_failure_returns_structured_result(self) -> None:
        backend = FailingPublishBlackboard()
        session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {"goal": "hold", "combat": {"defend_bias": 0.2}}
            ),
        )

        result = session.submit_text("hold", current_frame=3)

        self.assertFalse(result.ok)
        self.assertEqual(LiveModulationStatus.PUBLISH_FAILED, result.status)
        self.assertTrue(result.provider_failure_recorded)
        telemetry = backend.read_latest_telemetry()
        self.assertIsNotNone(telemetry)
        assert telemetry is not None
        self.assertEqual(MicroMachineBridgeFailureMode.PROVIDER_UNAVAILABLE, telemetry.last_failure)

    def test_refused_provider_output_does_not_publish_and_records_failure(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "status": "refused",
                    "refusal_reason": "strategy objective is unsafe",
                }
            ),
        )

        result = session.submit_text("unsafe", current_frame=77)

        self.assertFalse(result.ok)
        self.assertEqual(LiveModulationStatus.REFUSED, result.status)
        self.assertEqual(PolicyModulationCompileStatus.REFUSED, result.compile_result.status)
        self.assertIsNone(result.update)
        self.assertTrue(result.provider_failure_recorded)
        self.assertIsNone(backend.read_latest_update(current_frame=77))
        telemetry = backend.read_latest_telemetry()
        self.assertIsNotNone(telemetry)
        assert telemetry is not None
        self.assertEqual(MicroMachineBridgeFailureMode.PROVIDER_UNAVAILABLE, telemetry.last_failure)

    def test_clarification_required_does_not_publish_or_mark_provider_down(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {
                    "status": "clarification_required",
                    "clarification_prompt": "어느 타이밍까지 수비할까요?",
                }
            ),
        )

        result = session.submit_text("수비?", current_frame=11)

        self.assertFalse(result.ok)
        self.assertEqual(LiveModulationStatus.CLARIFICATION_REQUIRED, result.status)
        self.assertFalse(result.provider_failure_recorded)
        self.assertIsNone(result.update)
        self.assertIsNone(backend.read_latest_update(current_frame=11))
        self.assertIsNone(backend.read_latest_telemetry())

    def test_publish_failure_returns_failure_result_without_latest_update(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = MicroMachineFilesystemBlackboard(directory)
            session = MicroMachineLiveTextSession(
                backend,
                StaticJsonPolicyModulationProvider(
                    {
                        "goal": "unsafe dynamic key",
                        "combat": {
                            "target_priority_biases": {
                                "BANELING\ncombat.aggression": 0.9,
                            }
                        },
                    }
                ),
            )

            result = session.submit_text("unsafe dynamic key", current_frame=17)

            self.assertFalse(result.ok)
            self.assertEqual(LiveModulationStatus.PUBLISH_FAILED, result.status)
            self.assertTrue(result.provider_failure_recorded)
            self.assertIsNone(result.update)
            self.assertFalse((Path(directory) / LATEST_UPDATE_JSON_NAME).exists())
            self.assertFalse((Path(directory) / LATEST_UPDATE_KV_NAME).exists())
            telemetry = backend.read_latest_telemetry()
            self.assertIsNotNone(telemetry)
            assert telemetry is not None
            self.assertEqual(MicroMachineBridgeFailureMode.PROVIDER_UNAVAILABLE, telemetry.last_failure)

    def test_archive_failure_does_not_leave_latest_files_visible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "modulation_updates.jsonl").mkdir()
            backend = MicroMachineFilesystemBlackboard(directory)
            session = MicroMachineLiveTextSession(
                backend,
                StaticJsonPolicyModulationProvider(
                    {
                        "goal": "archive blocked",
                        "combat": {"defend_bias": 0.2},
                    }
                ),
            )

            result = session.submit_text("archive blocked", current_frame=19)

            self.assertFalse(result.ok)
            self.assertEqual(LiveModulationStatus.PUBLISH_FAILED, result.status)
            self.assertFalse((Path(directory) / LATEST_UPDATE_JSON_NAME).exists())
            self.assertFalse((Path(directory) / LATEST_UPDATE_KV_NAME).exists())
            telemetry = backend.read_latest_telemetry()
            self.assertIsNotNone(telemetry)
            assert telemetry is not None
            self.assertEqual(MicroMachineBridgeFailureMode.PROVIDER_UNAVAILABLE, telemetry.last_failure)

    def test_broken_latest_json_path_still_returns_publish_failed_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, LATEST_UPDATE_JSON_NAME).mkdir()
            backend = MicroMachineFilesystemBlackboard(directory)
            session = MicroMachineLiveTextSession(
                backend,
                StaticJsonPolicyModulationProvider(
                    {
                        "goal": "broken latest json",
                        "combat": {"defend_bias": 0.2},
                    }
                ),
            )

            result = session.submit_text("broken latest json", current_frame=21)

            self.assertFalse(result.ok)
            self.assertEqual(LiveModulationStatus.PUBLISH_FAILED, result.status)
            self.assertTrue(result.provider_failure_recorded)
            self.assertEqual(
                MicroMachineBridgeFailureMode.PROVIDER_UNAVAILABLE,
                result.dashboard.last_failure,
            )
            self.assertFalse((Path(directory) / LATEST_UPDATE_KV_NAME).exists())

    def test_broken_latest_telemetry_path_does_not_escape_submit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, LATEST_TELEMETRY_JSON_NAME).mkdir()
            backend = MicroMachineFilesystemBlackboard(directory)
            session = MicroMachineLiveTextSession(
                backend,
                StaticJsonPolicyModulationProvider(
                    {
                        "goal": "ignore broken telemetry",
                        "combat": {"defend_bias": 0.2},
                    }
                ),
            )

            result = session.submit_text("ignore broken telemetry", update_id="broken-telemetry")

            self.assertTrue(result.ok, result.to_dict())
            self.assertEqual(0, result.current_frame)
            self.assertEqual(
                LiveModulationConsumptionStatus.PENDING_TELEMETRY,
                result.consumption_status,
            )
            self.assertEqual(
                MicroMachineBridgeFailureMode.INVALID_PAYLOAD,
                result.dashboard.last_failure,
            )

    def test_refusal_with_broken_telemetry_path_stays_structured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, LATEST_TELEMETRY_JSON_NAME).mkdir()
            backend = MicroMachineFilesystemBlackboard(directory)
            session = MicroMachineLiveTextSession(
                backend,
                StaticJsonPolicyModulationProvider(
                    {
                        "status": "refused",
                        "refusal_reason": "provider refused",
                    }
                ),
            )

            result = session.submit_text("refused", current_frame=5)

            self.assertFalse(result.ok)
            self.assertEqual(LiveModulationStatus.REFUSED, result.status)
            self.assertFalse(result.provider_failure_recorded)
            self.assertEqual(
                MicroMachineBridgeFailureMode.INVALID_PAYLOAD,
                result.dashboard.last_failure,
            )

    def test_malformed_latest_telemetry_type_does_not_escape_submit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, LATEST_TELEMETRY_JSON_NAME).write_text(
                json.dumps(
                    {
                        "protocol_version": MICROMACHINE_BRIDGE_PROTOCOL_VERSION,
                        "frame": "bad",
                        "bot_name": "MicroMachine",
                        "race": "Terran",
                        "managers": {},
                        "active_modulation_ids": [],
                        "last_failure": None,
                    }
                )
            )
            backend = MicroMachineFilesystemBlackboard(directory)
            session = MicroMachineLiveTextSession(
                backend,
                StaticJsonPolicyModulationProvider(
                    {
                        "goal": "ignore malformed telemetry",
                        "combat": {"defend_bias": 0.2},
                    }
                ),
            )

            result = session.submit_text("ignore malformed telemetry", current_frame=1)

            self.assertTrue(result.ok, result.to_dict())
            self.assertEqual(
                LiveModulationConsumptionStatus.PENDING_TELEMETRY,
                result.consumption_status,
            )
            self.assertEqual(
                MicroMachineBridgeFailureMode.INVALID_PAYLOAD,
                result.dashboard.last_failure,
            )

    def test_ttl_expiry_is_enforced_by_backend_after_live_publish(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(
            backend,
            StaticJsonPolicyModulationProvider(
                {"goal": "short hold", "ttl_seconds": 1, "combat": {"defend_bias": 0.2}}
            ),
        )

        result = session.submit_text("short hold", current_frame=5, update_id="short")

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(backend.read_latest_update(current_frame=27))
        with self.assertRaisesRegex(ValueError, "stale"):
            backend.read_latest_update(current_frame=28)

    def test_filesystem_session_writes_json_kv_and_telemetry_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = MicroMachineFilesystemBlackboard(directory)
            backend.ingest_telemetry(MicroMachineTelemetry(frame=640))
            session = MicroMachineLiveTextSession(
                backend,
                StaticJsonPolicyModulationProvider(
                    {
                        "goal": "hold natural",
                        "override_level": "constraint",
                        "combat": {"defend_bias": 0.6},
                    }
                ),
            )

            result = session.submit_text("hold natural", update_id="fs-live")

            self.assertTrue(result.ok, result.to_dict())
            root = Path(directory)
            latest_json = root / LATEST_UPDATE_JSON_NAME
            latest_kv = root / LATEST_UPDATE_KV_NAME
            latest_telemetry = root / LATEST_TELEMETRY_JSON_NAME
            self.assertTrue(latest_json.exists())
            self.assertTrue(latest_kv.exists())
            self.assertTrue(latest_telemetry.exists())
            self.assertEqual("fs-live", json.loads(latest_json.read_text())["update_id"])
            self.assertIn("combat.defend_bias=0.6", latest_kv.read_text())

    def test_keyword_provider_allows_no_sdk_text_publish(self) -> None:
        backend = MicroMachineInMemoryBlackboard()
        session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())

        result = session.submit_text("탱크로 수비하면서 버텨", current_frame=9)

        self.assertTrue(result.ok, result.to_dict())
        self.assertIsNotNone(result.update)
        assert result.update is not None
        self.assertLess(result.update.vector.combat.aggression, 0)
        self.assertGreater(result.update.vector.combat.defend_bias, 0)
        self.assertEqual(32, result.update.vector.workers.repeat_order_guard_frames)
        self.assertIn("workers", result.update.manager_bias_domains)
        self.assertEqual("smoke_keyword", result.update.vector.source.value)

    def test_cli_without_provider_output_fails_closed_instead_of_keyword_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "--blackboard-dir",
                        directory,
                        "--command",
                        "탱크로 수비하면서 버텨",
                        "--current-frame",
                        "9",
                    ]
                )

            self.assertEqual(2, exit_code)
            result = json.loads(stdout.getvalue())
            self.assertFalse(result["ok"], result)
            self.assertEqual("refused", result["compile_result"]["status"])
            self.assertEqual("llm", result["provider_source"])
            self.assertIsNone(result["update"])
            self.assertFalse((Path(directory) / LATEST_UPDATE_JSON_NAME).exists())

    def test_cli_allows_keyword_provider_only_with_explicit_smoke_flag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "--blackboard-dir",
                        directory,
                        "--command",
                        "탱크로 수비하면서 버텨",
                        "--current-frame",
                        "9",
                        "--allow-smoke-keyword-provider",
                    ]
                )

            self.assertEqual(0, exit_code)
            result = json.loads(stdout.getvalue())
            self.assertTrue(result["ok"], result)
            self.assertEqual("smoke_keyword", result["provider_source"])
            self.assertTrue((Path(directory) / LATEST_UPDATE_JSON_NAME).exists())

    def test_cli_writes_result_and_filesystem_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stdout = io.StringIO()
            payload = json.dumps(
                {
                    "goal": "cli hold",
                    "override_level": "constraint",
                    "combat": {"defend_bias": 0.55},
                }
            )

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "--blackboard-dir",
                        directory,
                        "--command",
                        "cli hold",
                        "--current-frame",
                        "13",
                        "--update-id",
                        "cli-live",
                        "--provider-output-json",
                        payload,
                    ]
                )

            self.assertEqual(0, exit_code)
            result = json.loads(stdout.getvalue())
            self.assertTrue(result["ok"], result)
            self.assertEqual("cli-live", result["update"]["update_id"])
            self.assertTrue((Path(directory) / LATEST_UPDATE_JSON_NAME).exists())
            self.assertTrue((Path(directory) / LATEST_UPDATE_KV_NAME).exists())


if __name__ == "__main__":
    unittest.main()
