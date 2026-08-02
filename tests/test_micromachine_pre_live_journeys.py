"""Tests for deterministic MicroMachine pre-live journey evidence."""

from __future__ import annotations

import hashlib
import io
import json
import os
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
    _markdown_report,
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

    def test_checked_in_producer_runs_in_the_isolated_ci_launcher(self) -> None:
        policy = json.loads(PRODUCER_POLICY.read_bytes())
        producer = policy["producers"]["deterministic_journeys"]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "journeys.zip"
            replacements = {
                "{python}": str(Path(sys.executable).resolve()),
                "{repository}": str(REPO_ROOT),
                "{output}": str(output),
                "{micromachine_binary}": str(MICROMACHINE_BINARY),
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
            verification = verify_pre_live_journey_bundle(output.read_bytes())
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
            "selective cancellation": self._remove_sibling,
            "emergency preemption": self._remove_preemption,
            "ability movement": self._ability_movement,
            "reconnect duplicate": self._duplicate_reconnect_event,
            "projection mismatch": self._projection_identity_mismatch,
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

    def _projection_identity_mismatch(
        self,
    ) -> tuple[str, list[dict[str, object]]]:
        journey_id = "voice_readback_callout_identity"
        events = self._events(journey_id)
        projection = _first_event(events, "voice_projection")
        projection["identity"]["generation"] += 1
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


def _resequence(events: list[dict[str, object]]) -> None:
    for index, event in enumerate(events, start=1):
        event["seq"] = index


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
