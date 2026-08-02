"""Tests for deterministic MicroMachine pre-live journey evidence."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from copy import deepcopy
from pathlib import Path

from starcraft_commander.micromachine_pre_live_artifact import (
    canonical_json_bytes,
)
from starcraft_commander.micromachine_pre_live_journeys import (
    DEFAULT_JOURNEY_MANIFEST,
    DETERMINISTIC_ZIP_TIMESTAMP,
    _close_native_path_monitor,
    _execute_tactical_radio_runtime,
    _markdown_report,
    _native_path_monitor_changed,
    _open_native_path_monitor,
    _production_receipt_id,
    _run_native_command,
    _sha256_file,
    _validate_native_output_payload,
    build_pre_live_journey_bundle,
    execute_pre_live_journeys,
    load_pre_live_journey_manifest,
    verify_pre_live_journey_bundle,
    verify_pre_live_journey_events,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCER_POLICY = (
    REPO_ROOT / "integrations" / "micromachine" / "PRE_LIVE_PRODUCERS.json"
)
MICROMACHINE_BINARY = Path(
    os.environ.get(
        "VOI_MICROMACHINE_BINARY",
        (
            "/private/tmp/voi-micromachine-runtime/MicroMachine/"
            "build-latest-api/bin/MicroMachine"
        ),
    )
).resolve()


class NativeExecutableLaunchTest(unittest.TestCase):
    def test_unlinked_descriptor_executes_through_one_shot_snapshot(
        self,
    ) -> None:
        native_true = Path("/usr/bin/true")
        if not native_true.is_file():
            self.skipTest("requires /usr/bin/true")
        payload = native_true.read_bytes()
        expected_sha256 = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            admitted_binary = Path(directory) / "admitted-true"
            admitted_binary.write_bytes(payload)
            admitted_binary.chmod(0o500)
            descriptor = os.open(admitted_binary, os.O_RDONLY)
            admitted_binary.unlink()
            try:
                descriptor_path = Path(f"/dev/fd/{descriptor}")
                initial_offset = os.lseek(
                    descriptor,
                    0,
                    os.SEEK_CUR,
                )
                self.assertEqual(
                    expected_sha256,
                    _sha256_file(descriptor_path),
                )
                self.assertEqual(
                    initial_offset,
                    os.lseek(descriptor, 0, os.SEEK_CUR),
                )
                completed = _run_native_command(
                    descriptor_path,
                    [str(descriptor_path)],
                    expected_sha256=expected_sha256,
                    command_runner=subprocess.run,
                )
                self.assertEqual(
                    expected_sha256,
                    _sha256_file(descriptor_path),
                )
                self.assertEqual(
                    initial_offset,
                    os.lseek(descriptor, 0, os.SEEK_CUR),
                )
            finally:
                os.close(descriptor)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_unlinked_node_descriptor_executes_with_stdin(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("requires Node.js")
        payload = Path(node).read_bytes()
        expected_sha256 = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            admitted_node = Path(directory) / "admitted-node"
            admitted_node.write_bytes(payload)
            admitted_node.chmod(0o500)
            descriptor = os.open(admitted_node, os.O_RDONLY)
            admitted_node.unlink()
            try:
                descriptor_path = Path(f"/dev/fd/{descriptor}")
                completed = _run_native_command(
                    descriptor_path,
                    [
                        str(descriptor_path),
                        "-e",
                        (
                            "process.stdin.on('data',chunk=>"
                            "process.stdout.write(chunk))"
                        ),
                    ],
                    expected_sha256=expected_sha256,
                    command_runner=subprocess.run,
                    input=b"pinned-node-stdin",
                )
            finally:
                os.close(descriptor)
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(b"pinned-node-stdin", completed.stdout)

    @unittest.skipUnless(
        sys.platform == "darwin",
        "requires macOS kqueue vnode monitoring",
    )
    def test_one_shot_monitor_detects_path_swap_and_restore(self) -> None:
        payload = Path("/usr/bin/true").read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "MicroMachine"
            attacker = root / "attacker"
            backup = root / "backup"
            executable.write_bytes(payload)
            executable.chmod(0o500)
            attacker.write_bytes(b"attacker")
            attacker.chmod(0o500)
            descriptor = os.open(executable, os.O_RDONLY)
            root.chmod(0o500)
            monitor = _open_native_path_monitor(descriptor, root)
            try:
                root.chmod(0o700)
                os.replace(executable, backup)
                os.replace(attacker, executable)
                os.replace(executable, attacker)
                os.replace(backup, executable)
                self.assertTrue(_native_path_monitor_changed(monitor))
            finally:
                _close_native_path_monitor(monitor)
                root.chmod(0o700)
                os.close(descriptor)


@unittest.skipUnless(
    MICROMACHINE_BINARY.is_file() and os.access(MICROMACHINE_BINARY, os.X_OK),
    "requires a clean MicroMachine build or VOI_MICROMACHINE_BINARY",
)
class PreLiveJourneyExecutionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_pre_live_journey_manifest()
        cls.suite = execute_pre_live_journeys(MICROMACHINE_BINARY)
        cls.specs = {
            str(spec["id"]): spec
            for spec in cls.manifest["journeys"]
        }
        cls.artifacts = cls.suite["artifacts"]

    def test_all_fourteen_journeys_execute_product_paths(self) -> None:
        self.assertTrue(self.suite["ok"], self.suite["failures"])
        self.assertEqual(14, self.suite["journey_count"])
        self.assertEqual(14, self.suite["passed_count"])
        self.assertEqual(0, self.suite["failed_count"])
        self.assertEqual([], self.suite["failures"])
        required_paths = {
            "compiler_results",
            "bridge_validations",
            "operation_execution_reports",
            "battlefield_projections",
            "web_status",
            "timeline_results",
        }
        for journey in self.suite["journeys"]:
            with self.subTest(journey=journey["id"]):
                self.assertTrue(journey["ok"], journey["blockers"])
                products = journey["product_paths"]
                self.assertTrue(required_paths.issubset(products))
                for name in required_paths:
                    self.assertTrue(products[name], name)

    def test_native_initial_state_and_production_receipts_are_consumed(
        self,
    ) -> None:
        shortage = self.artifacts["shortage_prerequisite_wait"]["products"][
            "native_adapter"
        ]
        self.assertEqual(100, shortage["input"]["initial_state"]["minerals"])
        self.assertEqual(0, shortage["input"]["initial_state"]["vespene"])
        self.assertEqual(500, shortage["output"]["final_state"]["minerals"])
        self.assertEqual(125, shortage["output"]["final_state"]["vespene"])

        reconnect = self.artifacts["event_reconnect_replay"]["products"][
            "native_adapter"
        ]
        self.assertEqual(1, reconnect["input"]["initial_state"]["event_cursor"])
        self.assertEqual(1, reconnect["output"]["final_state"]["event_cursor"])
        reconnect_event = next(
            event
            for event in reconnect["output"]["events"]
            if event["event_type"] == "client_reconnect"
        )
        self.assertEqual(1, reconnect_event["payload"]["after_event_seq"])

        voice = self.artifacts["voice_readback_callout_identity"]["products"][
            "native_adapter"
        ]
        self.assertIs(True, voice["input"]["initial_state"]["voice_enabled"])
        self.assertIs(False, voice["input"]["initial_state"]["muted"])
        self.assertIs(True, voice["output"]["final_state"]["voice_enabled"])
        self.assertIs(False, voice["output"]["final_state"]["muted"])

        native = self.artifacts["safe_partial_launch"]["products"][
            "native_adapter"
        ]["output"]
        receipts = [
            event
            for event in native["events"]
            if event["event_type"] == "production_path_receipt"
        ]
        by_entrypoint = {
            entrypoint: sum(
                event["payload"]["entrypoint"] == entrypoint
                for event in receipts
            )
            for entrypoint in {
                "voiProductionAssignOperationOwner",
                "voiProductionIssueSquadOrder",
                "voiProductionSubmitSc2Action",
            }
        }
        production_path = native["production_path"]
        self.assertEqual(
            "micromachine_concrete_pre_live",
            production_path["executor_kind"],
        )
        self.assertEqual(
            "Squad::setSquadOrder",
            production_path["squad_order_execution_path"],
        )
        self.assertEqual(
            "Micro::*->CCBot::Actions()->UnitCommand",
            production_path["sc2_submission_execution_path"],
        )
        self.assertEqual(
            by_entrypoint["voiProductionAssignOperationOwner"],
            production_path["operation_ownership_receipt_count"],
        )
        self.assertEqual(
            by_entrypoint["voiProductionIssueSquadOrder"],
            production_path["squad_order_receipt_count"],
        )
        self.assertEqual(
            by_entrypoint["voiProductionSubmitSc2Action"],
            production_path["sc2_submission_receipt_count"],
        )
        self.assertEqual(
            production_path["squad_order_receipt_count"],
            len(production_path["applied_squad_orders"]),
        )
        self.assertEqual(
            production_path["sc2_submission_receipt_count"],
            len(production_path["dispatched_sc2_actions"]),
        )
        for receipt in production_path["squad_order_receipts"]:
            self.assertIs(True, receipt["callback_executed"])
            self.assertIs(True, receipt["applied_proof"])
            self.assertTrue(receipt["receipt_id"].startswith("voi-squad-order-"))
        for receipt in production_path["sc2_submission_receipts"]:
            self.assertIs(True, receipt["callback_executed"])
            self.assertIs(True, receipt["dispatch_proof"])
            self.assertTrue(
                receipt["submission_id"].startswith("voi-sc2-submission-")
            )

    def test_native_receipts_fail_closed_without_execution_proof(self) -> None:
        native = deepcopy(
            self.artifacts["safe_partial_launch"]["products"][
                "native_adapter"
            ]["output"]
        )
        native["production_path"]["sc2_submission_receipts"][0][
            "callback_executed"
        ] = False
        with self.assertRaisesRegex(ValueError, "callback was not executed"):
            _validate_native_output_payload(native)

    def test_native_output_rejects_nonconcrete_production_executor(self) -> None:
        native = deepcopy(
            self.artifacts["safe_partial_launch"]["products"][
                "native_adapter"
            ]["output"]
        )
        native["production_path"]["executor_kind"] = "deterministic_test_fake"
        with self.assertRaisesRegex(ValueError, "executor is not concrete"):
            _validate_native_output_payload(native)

    def test_rejected_native_operation_has_no_provisional_owner_receipts(
        self,
    ) -> None:
        native = self.artifacts["protected_minimum_partial_rejection"][
            "products"
        ]["native_adapter"]["output"]
        ownership_receipts = [
            event
            for event in native["events"]
            if event["event_type"] == "production_path_receipt"
            and event["payload"]["entrypoint"]
            == "voiProductionAssignOperationOwner"
        ]
        self.assertEqual([], ownership_receipts)
        self.assertEqual(
            0,
            native["production_path"]["operation_ownership_receipt_count"],
        )

    def test_native_events_fail_closed_without_any_production_receipts(
        self,
    ) -> None:
        native = deepcopy(
            self.artifacts["safe_partial_launch"]["products"][
                "native_adapter"
            ]["output"]
        )
        native["events"] = [
            event
            for event in native["events"]
            if event["event_type"] != "production_path_receipt"
        ]
        production_path = native["production_path"]
        production_path["operation_ownership_receipt_count"] = 0
        production_path["squad_order_receipt_count"] = 0
        production_path["sc2_submission_receipt_count"] = 0
        production_path["applied_squad_orders"] = []
        production_path["dispatched_sc2_actions"] = []
        production_path["squad_order_receipts"] = []
        production_path["sc2_submission_receipts"] = []
        with self.assertRaisesRegex(
            ValueError,
            "ownership receipts do not match ownership snapshots",
        ):
            _validate_native_output_payload(native)

    def test_native_ownership_receipt_is_bound_to_snapshot_identity(
        self,
    ) -> None:
        native = deepcopy(
            self.artifacts["safe_partial_launch"]["products"][
                "native_adapter"
            ]["output"]
        )
        receipt = next(
            event
            for event in native["events"]
            if event["event_type"] == "production_path_receipt"
            and event["payload"]["entrypoint"]
            == "voiProductionAssignOperationOwner"
        )
        receipt["identity"]["operation_id"] = "fabricated-owner"
        receipt["identity"]["generation"] += 1
        with self.assertRaisesRegex(
            ValueError,
            "ownership receipt predates launch admission",
        ):
            _validate_native_output_payload(native)

    def test_native_execution_rows_require_exact_field_sets(self) -> None:
        original = self.artifacts["safe_partial_launch"]["products"][
            "native_adapter"
        ]["output"]
        cases = (
            (
                "applied Squad order",
                "applied_squad_orders",
                lambda row: row.update({"fabricated_proof": True}),
            ),
            (
                "dispatched SC2 action",
                "dispatched_sc2_actions",
                lambda row: row.pop("dispatch_action"),
            ),
        )
        for message, field_name, mutate in cases:
            with self.subTest(field_name=field_name):
                native = deepcopy(original)
                mutate(native["production_path"][field_name][0])
                with self.assertRaisesRegex(ValueError, message):
                    _validate_native_output_payload(native)

    def test_native_effect_proof_is_bound_to_submission_receipts(self) -> None:
        original = self.artifacts["safe_partial_launch"]["products"][
            "native_adapter"
        ]["output"]
        cases = (
            ("unit_tags", [9999]),
            ("submission_ids", ["voi-sc2-submission-forged"]),
            ("dispatch_action", "forged_dispatch"),
        )
        for field_name, value in cases:
            with self.subTest(field_name=field_name):
                native = deepcopy(original)
                receipt = native["production_path"][
                    "sc2_submission_receipts"
                ][0]
                effect = next(
                    event
                    for event in native["events"]
                    if event["event_type"]
                    in {"movement", "engagement", "ability_effect"}
                    and event["identity"]["update_id"] == receipt["update_id"]
                    and event["identity"]["operation_id"]
                    == receipt["operation_id"]
                    and event["identity"]["generation"]
                    == receipt["generation"]
                    and event["payload"]["action"] == receipt["action"]
                )
                effect["payload"][field_name] = value
                with self.assertRaisesRegex(
                    ValueError,
                    "effect lacks exact SC2 receipt proof",
                ):
                    _validate_native_output_payload(native)

    def test_native_sc2_dispatch_tags_match_applied_squad_order(self) -> None:
        native = deepcopy(
            self.artifacts["safe_partial_launch"]["products"][
                "native_adapter"
            ]["output"]
        )
        _rebind_sc2_evidence_unit_tags(native, tag_offset=900_000)
        with self.assertRaisesRegex(
            ValueError,
            "Squad order and SC2 submission unit tags do not match",
        ):
            _validate_native_output_payload(native)

    def test_native_canonical_events_require_exact_receipt_multiplicity(
        self,
    ) -> None:
        original = self.artifacts["safe_partial_launch"]["products"][
            "native_adapter"
        ]["output"]
        cases = (
            (
                "squad-order receipt lacks one canonical order",
                "squad_order",
            ),
            (
                "SC2 receipts lack one canonical submission",
                "submission",
            ),
        )
        for message, event_type in cases:
            with self.subTest(event_type=event_type):
                native = deepcopy(original)
                duplicate = next(
                    event
                    for event in native["events"]
                    if event["event_type"] == event_type
                )
                native["events"].append(deepcopy(duplicate))
                with self.assertRaisesRegex(ValueError, message):
                    _validate_native_output_payload(native)

    def test_native_surplus_effect_requires_receipt_proof(self) -> None:
        native = deepcopy(
            self.artifacts["safe_partial_launch"]["products"][
                "native_adapter"
            ]["output"]
        )
        effect = next(
            event
            for event in native["events"]
            if event["event_type"] in {"movement", "engagement", "ability_effect"}
        )
        surplus = deepcopy(effect)
        surplus["payload"]["submission_ids"] = [
            "voi-sc2-submission-unreceipted"
        ]
        native["events"].append(surplus)
        with self.assertRaisesRegex(
            ValueError,
            "effect lacks exact SC2 receipt proof",
        ):
            _validate_native_output_payload(native)

    def test_native_effect_family_and_receipt_multiplicity_is_exact(
        self,
    ) -> None:
        original = self.artifacts["safe_partial_launch"]["products"][
            "native_adapter"
        ]["output"]

        def duplicate_effect(native: dict[str, object]) -> None:
            effect = next(
                event
                for event in native["events"]
                if event["event_type"] in {
                    "movement",
                    "engagement",
                    "ability_effect",
                }
            )
            native["events"].append(deepcopy(effect))

        def remove_effect(native: dict[str, object]) -> None:
            native["events"] = [
                event
                for event in native["events"]
                if event["event_type"] not in {
                    "movement",
                    "engagement",
                    "ability_effect",
                }
            ]

        def duplicate_family(native: dict[str, object]) -> None:
            evidence = native["operation_director"][0]["family_evidence"]
            evidence.append(deepcopy(evidence[0]))

        def remove_family(native: dict[str, object]) -> None:
            native["operation_director"][0]["family_evidence"] = []

        def duplicate_receipt(native: dict[str, object]) -> None:
            production = native["production_path"]
            receipt = production["sc2_submission_receipts"][0]
            submission_id = receipt["submission_id"]
            production["sc2_submission_receipts"].append(deepcopy(receipt))
            dispatched = next(
                row
                for row in production["dispatched_sc2_actions"]
                if row["submission_id"] == submission_id
            )
            production["dispatched_sc2_actions"].append(
                deepcopy(dispatched)
            )
            receipt_event = next(
                event
                for event in native["events"]
                if event["event_type"] == "production_path_receipt"
                and event["payload"].get("submission_id") == submission_id
            )
            native["events"].append(deepcopy(receipt_event))
            production["sc2_submission_receipt_count"] += 1

        def remove_receipt(native: dict[str, object]) -> None:
            production = native["production_path"]
            removed = production["sc2_submission_receipts"].pop(0)
            submission_id = removed["submission_id"]
            production["dispatched_sc2_actions"] = [
                row
                for row in production["dispatched_sc2_actions"]
                if row["submission_id"] != submission_id
            ]
            native["events"] = [
                event
                for event in native["events"]
                if not (
                    event["event_type"] == "production_path_receipt"
                    and event["payload"].get("submission_id")
                    == submission_id
                )
            ]
            production["sc2_submission_receipt_count"] -= 1

        cases = (
            ("duplicate canonical effects", duplicate_effect),
            ("family evidence lacks one canonical effect", remove_effect),
            ("effect lacks exact family evidence", duplicate_family),
            ("effect lacks exact family evidence", remove_family),
            ("production binding is not unique", duplicate_receipt),
            (
                "Squad order and SC2 submission unit tags do not match",
                remove_receipt,
            ),
        )
        for message, mutate in cases:
            with self.subTest(message=message):
                native = deepcopy(original)
                mutate(native)
                with self.assertRaisesRegex(ValueError, message):
                    _validate_native_output_payload(native)

    def test_native_ownership_receipt_follows_launch_admission(self) -> None:
        native = deepcopy(
            self.artifacts["safe_partial_launch"]["products"][
                "native_adapter"
            ]["output"]
        )
        events = native["events"]
        launch_index = next(
            index
            for index, event in enumerate(events)
            if event["event_type"] == "launch_decision"
            and event["identity"]["stage"] == "launch_admitted"
        )
        receipt_index = next(
            index
            for index, event in enumerate(events)
            if event["event_type"] == "production_path_receipt"
            and event["payload"]["entrypoint"]
            == "voiProductionAssignOperationOwner"
        )
        events[launch_index], events[receipt_index] = (
            events[receipt_index],
            events[launch_index],
        )
        _resequence(events)
        with self.assertRaisesRegex(
            ValueError,
            "ownership receipt predates launch admission",
        ):
            _validate_native_output_payload(native)

    def test_production_tactical_radio_executes_runtime_behavior(self) -> None:
        runtime = self.artifacts["voice_readback_callout_identity"]["products"][
            "tactical_radio_runtime"
        ]
        self.assertEqual(
            "production_web_gui_tactical_radio_js",
            runtime["runtime"],
        )
        self.assertIs(True, runtime["primary_accepted"])
        self.assertIs(True, runtime["secondary_accepted"])
        self.assertIs(False, runtime["duplicate_primary_accepted"])
        self.assertIs(False, runtime["duplicate_secondary_accepted"])
        self.assertIs(False, runtime["stale_accepted"])
        self.assertIs(True, runtime["muted_accepted"])
        self.assertEqual(1, runtime["queue_length_before_drain"])
        self.assertEqual(0, runtime["queue_length_after_drain"])
        self.assertEqual(3, runtime["caption_count"])
        self.assertEqual(2, len(runtime["spoken"]))
        node = shutil.which("node")
        self.assertIsNotNone(node)
        assert node is not None
        self.assertEqual(
            hashlib.sha256(Path(node).read_bytes()).hexdigest(),
            runtime["node_sha256"],
        )
        self.assertEqual(1, runtime["muted_caption_delta"])
        self.assertEqual(0, runtime["muted_speech_delta"])
        self.assertIs(False, runtime["final_muted"])
        self.assertEqual(1460, runtime["frame_high_water"])
        self.assertEqual(6, runtime["timeline_high_water"])
        self.assertEqual(5, runtime["production_announcement_calls"])

    def test_production_tactical_radio_rejects_foreign_timeline_update(
        self,
    ) -> None:
        artifact = self.artifacts["voice_readback_callout_identity"]
        timeline = deepcopy(
            artifact["products"]["timeline_results"][0]["operation_events"]
        )
        primary_event, secondary_event = timeline[-2:]
        for event in (primary_event, secondary_event):
            event["update_id"] = "foreign-timeline-update"
            event["technical"]["update_id"] = "foreign-timeline-update"
        row = artifact["products"]["native_adapter"]["output"][
            "operation_director"
        ][0]
        expected_identity = {
            "update_id": row["policy_update_id"],
            "operation_id": row["operation_id"],
            "generation": row["generation"],
        }
        with self.assertRaisesRegex(
            ValueError,
            "does not match operation identity",
        ):
            _execute_tactical_radio_runtime(
                primary_event,
                secondary_event,
                scope_id="voice_readback_callout_identity",
                expected_identity=expected_identity,
                command_runner=subprocess.run,
            )

    def test_transfer_rejection_preserves_byte_identical_active_endpoints(
        self,
    ) -> None:
        events = self._events("transfer_rejection_preserves_active")
        snapshots = [
            event["payload"]["state"]
            for event in events
            if event["event_type"] == "state_snapshot"
        ]
        self.assertEqual(2, len(snapshots))
        for state in snapshots:
            state.pop("frame", None)
        self.assertEqual(
            canonical_json_bytes(snapshots[0]),
            canonical_json_bytes(snapshots[1]),
        )
        for state in snapshots:
            active = {
                operation["operation_id"]
                for operation in state["operations"]
                if operation["active"] is True
            }
            self.assertTrue({"recon-alpha", "assault-bravo"}.issubset(active))

    def test_bundle_is_byte_identical_and_recomputed_from_raw_evidence(self) -> None:
        first = build_pre_live_journey_bundle(MICROMACHINE_BINARY)
        second = build_pre_live_journey_bundle(MICROMACHINE_BINARY)
        self.assertEqual(first, second)
        verification = verify_pre_live_journey_bundle(first)
        self.assertTrue(verification["ok"], verification)

        entries = _read_zip(first)
        raw_name = "raw/parallel_scout_attack_defend.jsonl"
        raw_events = [
            json.loads(line)
            for line in entries[raw_name].splitlines()
        ]
        movement = next(
            event
            for event in raw_events
            if event["event_type"] == "movement"
        )
        movement["identity"]["generation"] += 1
        entries[raw_name] = b"".join(
            canonical_json_bytes(event) + b"\n" for event in raw_events
        )
        tampered = _rebuild_journey_bundle(entries)
        rejected = verify_pre_live_journey_bundle(tampered)
        self.assertFalse(rejected["ok"], rejected)
        self.assertIn(
            "derived journey matrix was not derived from raw evidence",
            rejected["blockers"],
        )

    def test_forged_product_matrix_and_report_fail_executable_replay(
        self,
    ) -> None:
        entries = _read_zip(build_pre_live_journey_bundle(MICROMACHINE_BINARY))
        journey_id = "safe_partial_launch"
        product_name = f"product/{journey_id}.json"
        products = json.loads(entries[product_name])
        products["compiler_results"][0]["status"] = "forged-published"
        entries[product_name] = canonical_json_bytes(products)

        matrix = json.loads(entries["derived/journey-matrix.json"])
        journey = next(
            item for item in matrix["journeys"] if item["id"] == journey_id
        )
        journey["product_paths"] = products
        entries["derived/journey-matrix.json"] = canonical_json_bytes(matrix)
        entries["report.md"] = _markdown_report(matrix).encode("utf-8")

        rejected = verify_pre_live_journey_bundle(
            _rebuild_journey_bundle(entries)
        )
        self.assertFalse(rejected["ok"], rejected)
        self.assertIn(
            (
                f"{journey_id}: product evidence was not rederived "
                "by the executable product paths"
            ),
            rejected["blockers"],
        )

    def test_forged_node_digest_fails_executable_replay(self) -> None:
        entries = _read_zip(build_pre_live_journey_bundle(MICROMACHINE_BINARY))
        journey_id = "voice_readback_callout_identity"
        product_name = f"product/{journey_id}.json"
        products = json.loads(entries[product_name])
        products["tactical_radio_runtime"]["node_sha256"] = "0" * 64
        entries[product_name] = canonical_json_bytes(products)

        matrix = json.loads(entries["derived/journey-matrix.json"])
        journey = next(
            item for item in matrix["journeys"] if item["id"] == journey_id
        )
        journey["product_paths"] = products
        entries["derived/journey-matrix.json"] = canonical_json_bytes(matrix)
        entries["report.md"] = _markdown_report(matrix).encode("utf-8")

        rejected = verify_pre_live_journey_bundle(
            _rebuild_journey_bundle(entries)
        )
        self.assertFalse(rejected["ok"], rejected)
        self.assertTrue(
            any(
                "Node.js executable digest mismatch" in blocker
                for blocker in rejected["blockers"]
            ),
            rejected,
        )

    def test_checked_in_producer_runs_in_the_isolated_ci_launcher(self) -> None:
        policy = json.loads(PRODUCER_POLICY.read_bytes())
        producer = policy["producers"]["deterministic_journeys"]
        node = shutil.which("node")
        if node is None:
            self.skipTest("requires Node.js")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "journeys.zip"
            replacements = {
                "{python}": str(Path(sys.executable).resolve()),
                "{repository}": str(REPO_ROOT),
                "{output}": str(output),
                "{micromachine_binary}": str(MICROMACHINE_BINARY),
                "{node}": str(Path(node).resolve()),
            }
            argv = [
                replacements.get(value, value)
                for value in producer["argv"]
            ]
            completed = subprocess.run(
                argv,
                cwd=REPO_ROOT,
                check=False,
                capture_output=True,
                text=False,
                shell=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            verification = verify_pre_live_journey_bundle(
                output.read_bytes(),
                node_executable=replacements["{node}"],
            )
            self.assertTrue(verification["ok"], verification)

    def test_negative_raw_stream_matrix_fails_closed(self) -> None:
        cases = {
            "duplicate owner": self._duplicate_owner,
            "protected minimum submission": self._forbidden_submission,
            "published without submission": self._remove_submission,
            "noncanonical submission stage": self._submission_stage,
            "wrong effect identity": self._wrong_effect_identity,
            "noncanonical effect stage": self._effect_stage,
            "stale frame": self._stale_effect_frame,
            "transfer state mutation": self._mutate_transfer_rejection_state,
            "transfer inactive endpoint": self._deactivate_transfer_endpoint,
            "transfer exact tags": self._mutate_transfer_tags,
            "transfer exact generations": self._mutate_transfer_generation,
            "selective cancellation": self._remove_sibling,
            "cancellation released tags": self._mutate_cancellation_tags,
            "cancellation ownership leak": self._leak_cancelled_owner_binding,
            "cancellation sibling binding": self._mutate_sibling_owner_binding,
            "emergency preemption": self._remove_preemption,
            "ability movement": self._ability_movement,
            "reconnect duplicate": self._duplicate_reconnect_event,
            "web source payload": self._mutate_web_source_payload,
            "web source multiplicity": self._remove_web_source,
            "duplicate reconnect marker": self._duplicate_reconnect_marker,
            "duplicate replay batch": self._duplicate_replay_batch,
            "receipt unit tag": self._receipt_unit_tag,
            "receipt action": self._receipt_action,
            "receipt identity": self._receipt_identity,
            "ownership receipt identity": self._ownership_receipt_identity,
            "missing production receipts": self._remove_production_receipts,
            "submission receipt link": self._submission_receipt_link,
            "squad dispatch tag divergence": self._squad_dispatch_tag_divergence,
            "duplicate replay output": self._duplicate_replay_output,
            "replay source identity": self._mutate_replay_source_identity,
            "replay source payload": self._mutate_replay_source_payload,
            "projection mismatch": self._projection_identity_mismatch,
            "callout mismatch": self._callout_identity_mismatch,
            "timeout": self._exceed_timeout,
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                journey_id, events = mutate()
                verdict = verify_pre_live_journey_events(
                    self.specs[journey_id],
                    events,
                )
                self.assertFalse(verdict["ok"], verdict)
                self.assertTrue(verdict["blockers"], verdict)

    def _events(self, journey_id: str) -> list[dict[str, object]]:
        return deepcopy(self.artifacts[journey_id]["events"])

    def _duplicate_owner(self) -> tuple[str, list[dict[str, object]]]:
        journey_id = "parallel_scout_attack_defend"
        events = self._events(journey_id)
        ownership = next(
            event
            for event in events
            if event["event_type"] == "ownership_snapshot"
            and len(event["payload"]["owners"]) > 1
        )
        owners = ownership["payload"]["owners"]
        owner_names = list(owners)
        owners[owner_names[1]].append(owners[owner_names[0]][0])
        return journey_id, events

    def _forbidden_submission(self) -> tuple[str, list[dict[str, object]]]:
        journey_id = "protected_minimum_partial_rejection"
        events = self._events(journey_id)
        rejection = _first_event(events, "rejection")
        events.append(
            {
                "seq": len(events) + 1,
                "event_type": "submission",
                "identity": deepcopy(rejection["identity"]),
                "payload": {
                    "action": rejection["payload"]["action"],
                    "unit_tags": [9999],
                },
            }
        )
        return journey_id, events

    def _remove_submission(self) -> tuple[str, list[dict[str, object]]]:
        journey_id = "safe_partial_launch"
        events = self._events(journey_id)
        events = [
            event
            for event in events
            if event["event_type"] != "submission"
        ]
        _resequence(events)
        return journey_id, events

    def _wrong_effect_identity(self) -> tuple[str, list[dict[str, object]]]:
        journey_id = "safe_partial_launch"
        events = self._events(journey_id)
        movement = _first_event(events, "movement")
        movement["identity"]["update_id"] = "foreign-update"
        return journey_id, events

    def _submission_stage(self) -> tuple[str, list[dict[str, object]]]:
        journey_id = "safe_partial_launch"
        events = self._events(journey_id)
        _first_event(events, "submission")["identity"]["stage"] = "published"
        return journey_id, events

    def _effect_stage(self) -> tuple[str, list[dict[str, object]]]:
        journey_id = "safe_partial_launch"
        events = self._events(journey_id)
        _first_event(events, "movement")["identity"]["stage"] = "submitted"
        return journey_id, events

    def _stale_effect_frame(self) -> tuple[str, list[dict[str, object]]]:
        journey_id = "safe_partial_launch"
        events = self._events(journey_id)
        submission = _first_event(events, "submission")
        movement = _first_event(events, "movement")
        movement["identity"]["game_frame"] = (
            submission["identity"]["game_frame"] - 1
        )
        return journey_id, events

    def _mutate_transfer_rejection_state(
        self,
    ) -> tuple[str, list[dict[str, object]]]:
        journey_id = "transfer_rejection_preserves_active"
        events = self._events(journey_id)
        snapshots = [
            event
            for event in events
            if event["event_type"] == "state_snapshot"
        ]
        snapshots[-1]["payload"]["state"]["owners"]["recon-alpha"] = []
        return journey_id, events

    def _deactivate_transfer_endpoint(
        self,
    ) -> tuple[str, list[dict[str, object]]]:
        journey_id = "transfer_rejection_preserves_active"
        events = self._events(journey_id)
        after = next(
            event
            for event in events
            if event["event_type"] == "state_snapshot"
            and event["payload"]["phase"] == "after"
        )
        operation = next(
            row
            for row in after["payload"]["state"]["operations"]
            if row["operation_id"] == "assault-bravo"
        )
        operation["active"] = False
        return journey_id, events

    def _mutate_transfer_tags(
        self,
    ) -> tuple[str, list[dict[str, object]]]:
        journey_id = "transfer_success"
        events = self._events(journey_id)
        transfer = _first_event(events, "transfer")
        transfer["payload"]["unit_tags"] = [9999]
        return journey_id, events

    def _mutate_transfer_generation(
        self,
    ) -> tuple[str, list[dict[str, object]]]:
        journey_id = "transfer_success"
        events = self._events(journey_id)
        transfer = _first_event(events, "transfer")
        transfer["payload"]["destination_generation_after"] += 1
        return journey_id, events

    def _remove_sibling(self) -> tuple[str, list[dict[str, object]]]:
        journey_id = "selective_cancellation"
        events = self._events(journey_id)
        snapshots = [
            event
            for event in events
            if event["event_type"] == "state_snapshot"
        ]
        sibling = next(
            operation
            for operation in snapshots[-1]["payload"]["state"]["operations"]
            if operation["operation_id"] == "assault-bravo"
        )
        sibling["active"] = False
        return journey_id, events

    def _mutate_cancellation_tags(
        self,
    ) -> tuple[str, list[dict[str, object]]]:
        journey_id = "selective_cancellation"
        events = self._events(journey_id)
        cancellation = _first_event(events, "cancellation")
        cancellation["payload"]["released_unit_tags"] = [9999]
        return journey_id, events

    def _leak_cancelled_owner_binding(
        self,
    ) -> tuple[str, list[dict[str, object]]]:
        journey_id = "selective_cancellation"
        events = self._events(journey_id)
        after = next(
            event
            for event in events
            if event["event_type"] == "state_snapshot"
            and event["payload"]["phase"] == "after"
        )
        after["payload"]["state"]["owner_bindings"]["1000"] = {
            "operation_id": "recon-alpha",
            "generation": 1,
        }
        return journey_id, events

    def _mutate_sibling_owner_binding(
        self,
    ) -> tuple[str, list[dict[str, object]]]:
        journey_id = "selective_cancellation"
        events = self._events(journey_id)
        after = next(
            event
            for event in events
            if event["event_type"] == "state_snapshot"
            and event["payload"]["phase"] == "after"
        )
        after["payload"]["state"]["owner_bindings"]["1002"][
            "generation"
        ] += 1
        return journey_id, events

    def _remove_preemption(self) -> tuple[str, list[dict[str, object]]]:
        journey_id = "emergency_preemption"
        events = self._events(journey_id)
        preemption = _first_event(events, "preemption")
        preemption["identity"]["stage"] = "not_preempted"
        return journey_id, events

    def _ability_movement(self) -> tuple[str, list[dict[str, object]]]:
        journey_id = "all_terran_family_ability_blocker_matrix"
        events = self._events(journey_id)
        effect = _first_event(events, "ability_effect")
        effect["payload"]["action"] = "move_ability"
        return journey_id, events

    def _duplicate_reconnect_event(
        self,
    ) -> tuple[str, list[dict[str, object]]]:
        journey_id = "event_reconnect_replay"
        events = self._events(journey_id)
        web_events = [
            event for event in events if event["event_type"] == "web_event"
        ]
        web_events[1]["payload"]["logical_event_id"] = web_events[0]["payload"][
            "logical_event_id"
        ]
        return journey_id, events

    def _mutate_web_source_payload(
        self,
    ) -> tuple[str, list[dict[str, object]]]:
        journey_id = "event_reconnect_replay"
        events = self._events(journey_id)
        web_event = _first_event(events, "web_event")
        web_event["payload"]["source_payload"]["forged"] = True
        return journey_id, events

    def _remove_web_source(
        self,
    ) -> tuple[str, list[dict[str, object]]]:
        journey_id = "event_reconnect_replay"
        events = self._events(journey_id)
        removed = False
        retained: list[dict[str, object]] = []
        for event in events:
            if event["event_type"] == "web_event" and not removed:
                removed = True
                continue
            retained.append(event)
        _resequence(retained)
        return journey_id, retained

    def _duplicate_reconnect_marker(
        self,
    ) -> tuple[str, list[dict[str, object]]]:
        journey_id = "event_reconnect_replay"
        events = self._events(journey_id)
        events.append(deepcopy(_first_event(events, "client_reconnect")))
        _resequence(events)
        return journey_id, events

    def _duplicate_replay_batch(
        self,
    ) -> tuple[str, list[dict[str, object]]]:
        journey_id = "event_reconnect_replay"
        events = self._events(journey_id)
        events.append(deepcopy(_first_event(events, "replay_batch")))
        _resequence(events)
        return journey_id, events

    def _receipt_unit_tag(self) -> tuple[str, list[dict[str, object]]]:
        journey_id = "safe_partial_launch"
        events = self._events(journey_id)
        receipt = next(
            event
            for event in events
            if event["event_type"] == "production_path_receipt"
            and event["payload"]["entrypoint"]
            == "voiProductionSubmitSc2Action"
        )
        receipt["payload"]["unit_tags"][0] = 9999
        return journey_id, events

    def _receipt_action(self) -> tuple[str, list[dict[str, object]]]:
        journey_id = "safe_partial_launch"
        events = self._events(journey_id)
        receipt = next(
            event
            for event in events
            if event["event_type"] == "production_path_receipt"
            and event["payload"]["entrypoint"]
            == "voiProductionIssueSquadOrder"
        )
        receipt["payload"]["action"] = "squad_order:scout"
        return journey_id, events

    def _receipt_identity(self) -> tuple[str, list[dict[str, object]]]:
        journey_id = "safe_partial_launch"
        events = self._events(journey_id)
        receipt = next(
            event
            for event in events
            if event["event_type"] == "production_path_receipt"
            and event["payload"]["entrypoint"]
            == "voiProductionSubmitSc2Action"
        )
        receipt["payload"]["update_id"] = "foreign-receipt-update"
        return journey_id, events

    def _ownership_receipt_identity(
        self,
    ) -> tuple[str, list[dict[str, object]]]:
        journey_id = "safe_partial_launch"
        events = self._events(journey_id)
        receipt = next(
            event
            for event in events
            if event["event_type"] == "production_path_receipt"
            and event["payload"]["entrypoint"]
            == "voiProductionAssignOperationOwner"
        )
        receipt["identity"]["operation_id"] = "fabricated-owner"
        receipt["identity"]["generation"] += 1
        return journey_id, events

    def _remove_production_receipts(
        self,
    ) -> tuple[str, list[dict[str, object]]]:
        journey_id = "safe_partial_launch"
        events = [
            event
            for event in self._events(journey_id)
            if event["event_type"] != "production_path_receipt"
        ]
        _resequence(events)
        return journey_id, events

    def _submission_receipt_link(
        self,
    ) -> tuple[str, list[dict[str, object]]]:
        journey_id = "safe_partial_launch"
        events = self._events(journey_id)
        submission = _first_event(events, "submission")
        submission["payload"]["submission_ids"][0] = (
            "voi-sc2-submission-forged"
        )
        return journey_id, events

    def _squad_dispatch_tag_divergence(
        self,
    ) -> tuple[str, list[dict[str, object]]]:
        journey_id = "safe_partial_launch"
        events = self._events(journey_id)
        _rebind_sc2_event_unit_tags(events, tag_offset=900_000)
        return journey_id, events

    def _duplicate_replay_output(
        self,
    ) -> tuple[str, list[dict[str, object]]]:
        journey_id = "event_reconnect_replay"
        events = self._events(journey_id)
        replayed = _first_event(events, "replay_deduplicated")
        events.append(deepcopy(replayed))
        _resequence(events)
        return journey_id, events

    def _mutate_replay_source_identity(
        self,
    ) -> tuple[str, list[dict[str, object]]]:
        journey_id = "event_reconnect_replay"
        events = self._events(journey_id)
        replayed = _first_event(events, "replay_deduplicated")
        replayed["payload"]["source_identity"]["operation_id"] = (
            "foreign-operation"
        )
        return journey_id, events

    def _mutate_replay_source_payload(
        self,
    ) -> tuple[str, list[dict[str, object]]]:
        journey_id = "event_reconnect_replay"
        events = self._events(journey_id)
        replayed = _first_event(events, "replay_deduplicated")
        replayed["payload"]["source_payload"]["forged"] = True
        return journey_id, events

    def _projection_identity_mismatch(
        self,
    ) -> tuple[str, list[dict[str, object]]]:
        journey_id = "voice_readback_callout_identity"
        events = self._events(journey_id)
        projection = _first_event(events, "voice_projection")
        projection["identity"]["generation"] += 1
        return journey_id, events

    def _callout_identity_mismatch(
        self,
    ) -> tuple[str, list[dict[str, object]]]:
        journey_id = "voice_readback_callout_identity"
        events = self._events(journey_id)
        callout = _first_event(events, "voice_callout")
        callout["identity"]["update_id"] = "foreign-callout-update"
        return journey_id, events

    def _exceed_timeout(self) -> tuple[str, list[dict[str, object]]]:
        journey_id = "safe_partial_launch"
        events = self._events(journey_id)
        events[-1]["identity"]["game_frame"] = 1201
        return journey_id, events


class PreLiveJourneyManifestTest(unittest.TestCase):
    def test_manifest_is_exactly_versioned_and_tracked(self) -> None:
        payload = load_pre_live_journey_manifest(DEFAULT_JOURNEY_MANIFEST)
        self.assertEqual(1, payload["schema_version"])
        self.assertEqual(14, len(payload["journeys"]))
        self.assertEqual(
            len(payload["journeys"]),
            len({item["id"] for item in payload["journeys"]}),
        )


def _first_event(
    events: list[dict[str, object]],
    event_type: str,
) -> dict[str, object]:
    return next(
        event for event in events if event["event_type"] == event_type
    )


def _rebind_sc2_evidence_unit_tags(
    native: dict[str, object],
    *,
    tag_offset: int,
) -> None:
    production_path = native["production_path"]
    receipts = production_path["sc2_submission_receipts"]
    dispatched = production_path["dispatched_sc2_actions"]
    events = native["events"]
    replacements: dict[str, tuple[str, list[int]]] = {}
    for receipt in receipts:
        old_id = receipt["submission_id"]
        new_tags = [tag + tag_offset for tag in receipt["unit_tags"]]
        binding = (
            receipt["update_id"],
            receipt["operation_id"],
            receipt["generation"],
            receipt["action"],
            tuple(new_tags),
        )
        new_id = _production_receipt_id(
            "voi-sc2-submission",
            binding,
            dispatch_action=receipt["dispatch_action"],
            action_metadata=_receipt_action_metadata(receipt),
        )
        receipt["unit_tags"] = new_tags
        receipt["submission_id"] = new_id
        replacements[old_id] = (new_id, new_tags)
    for row in dispatched:
        new_id, new_tags = replacements[row["submission_id"]]
        row["submission_id"] = new_id
        row["unit_tags"] = new_tags
    _rebind_sc2_event_unit_tags(
        events,
        tag_offset=tag_offset,
        replacements=replacements,
    )


def _rebind_sc2_event_unit_tags(
    events: list[dict[str, object]],
    *,
    tag_offset: int,
    replacements: dict[str, tuple[str, list[int]]] | None = None,
) -> None:
    resolved = replacements or {}
    receipt_events = [
        event
        for event in events
        if event["event_type"] == "production_path_receipt"
        and event["payload"]["entrypoint"]
        == "voiProductionSubmitSc2Action"
    ]
    for event in receipt_events:
        payload = event["payload"]
        old_id = payload["submission_id"]
        if old_id in resolved:
            new_id, new_tags = resolved[old_id]
        else:
            new_tags = [tag + tag_offset for tag in payload["unit_tags"]]
            binding = (
                payload["update_id"],
                payload["operation_id"],
                payload["generation"],
                payload["action"],
                tuple(new_tags),
            )
            new_id = _production_receipt_id(
                "voi-sc2-submission",
                binding,
                dispatch_action=payload["dispatch_action"],
                action_metadata=_receipt_action_metadata(payload),
            )
            resolved[old_id] = (new_id, new_tags)
        payload["submission_id"] = new_id
        payload["unit_tags"] = new_tags
    new_ids = sorted(new_id for new_id, _tags in resolved.values())
    new_tags = sorted(
        tag
        for _new_id, tags in resolved.values()
        for tag in tags
    )
    for event in events:
        if event["event_type"] not in {
            "submission",
            "movement",
            "engagement",
            "ability_effect",
        }:
            continue
        payload = event["payload"]
        if "submission_ids" in payload:
            payload["submission_ids"] = new_ids
        if "unit_tags" in payload:
            payload["unit_tags"] = new_tags


def _resequence(events: list[dict[str, object]]) -> None:
    for index, event in enumerate(events, start=1):
        event["seq"] = index


def _receipt_action_metadata(
    payload: dict[str, object],
) -> tuple[str, str, int, str, object, object]:
    return (
        str(payload["dispatch_action"]),
        str(payload["ability_name"]),
        int(payload["ability_id"]),
        str(payload["target_kind"]),
        payload["target_x"],
        payload["target_y"],
    )


def _read_zip(bundle: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(bundle), mode="r") as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _rebuild_journey_bundle(entries: dict[str, bytes]) -> bytes:
    root = json.loads(entries["manifest.json"])
    payload_names = sorted(name for name in entries if name != "manifest.json")
    root["members"] = [
        {
            "name": name,
            "sha256": hashlib.sha256(entries[name]).hexdigest(),
            "size_bytes": len(entries[name]),
        }
        for name in payload_names
    ]
    root["report_sha256"] = hashlib.sha256(
        entries["derived/journey-matrix.json"]
    ).hexdigest()
    entries["manifest.json"] = canonical_json_bytes(root)
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_STORED) as archive:
        for name in ["manifest.json", *payload_names]:
            info = zipfile.ZipInfo(name, date_time=DETERMINISTIC_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.create_version = 20
            info.extract_version = 20
            info.external_attr = 0o100644 << 16
            archive.writestr(info, entries[name])
    return output.getvalue()


if __name__ == "__main__":
    unittest.main()
