"""Typed contextual-transfer admission and replay acceptance tests."""

from __future__ import annotations

import concurrent.futures
from copy import deepcopy
from dataclasses import replace
import http.client
import json
import tempfile
import threading
import unittest
from unittest import mock

from starcraft_commander.contextual_transfer import (
    ContextualTransferRejectedError,
    ContextualTransferRequest,
    prepare_contextual_transfer,
)
from starcraft_commander.micromachine_battlefield_projection import (
    battlefield_overview_fingerprint,
)
from starcraft_commander.micromachine_live_session import (
    MicroMachineLiveTextSession,
    StaticJsonPolicyModulationProvider,
)
from starcraft_commander.micromachine_runtime import (
    MicroMachineFilesystemBlackboard,
)
from starcraft_commander.web_gui import (
    SessionLoopBridge,
    WebGuiServer,
    _ContextualTransferReplay,
    _ContextualTransferReplayCapacityError,
    _MicroMachineValidatedRuntimeSnapshot,
    _micromachine_blackboard_scope_id,
)


SESSION_EPOCH = 1_700_000_000_000
PROJECTION_FRAME = 140


class _UnusedSession:
    async def process_text(self, text):
        raise AssertionError(f"legacy text path must not run: {text}")


class _ExplodingLLMControl:
    def __init__(self):
        self.calls = 0

    def propose_policy_modulation(self, request):
        self.calls += 1
        raise AssertionError("typed contextual transfer must bypass the LLM")


def _initial_provider_output():
    return {
        "goal": "four independent operations",
        "command_layer": "operation",
        "operations": [
            _operation(
                "source-alpha",
                task_type="scout_with_units",
                count=4,
                role="scout",
            ),
            _operation(
                "destination-bravo",
                task_type="pressure_with_main_army",
                count=4,
                role="frontline",
            ),
            _operation(
                "destination-charlie",
                task_type="defend_with_units",
                count=3,
                role="defensive_hold",
            ),
            _operation(
                "sibling-delta",
                task_type="defend_with_units",
                count=1,
                role="defensive_hold",
                unit_type="TERRAN_SIEGETANK",
            ),
        ],
    }


def _operation(
    operation_id,
    *,
    task_type,
    count,
    role,
    unit_type="TERRAN_MARINE",
):
    return {
        "operation_id": operation_id,
        "goal": f"operate {operation_id}",
        "tactical_task": {
            "task_type": task_type,
            "unit_classes": [unit_type],
            "min_units": count,
            "max_units": count,
            "allow_partial": False,
        },
        "scope": {
            "unit_classes": [unit_type],
            "min_units": count,
            "max_units": count,
            "allow_partial_scope": False,
        },
        "composition_requirements": [
            {
                "unit_type": unit_type,
                "count": count,
                "role": role,
            }
        ],
        "unit_roles": [
            {
                "unit_type": unit_type,
                "role": role,
                "priority": 0.7,
                "ability_policy": "if_available",
            }
        ],
    }


def _operation_projection(operation_id, generation, count, minimum):
    return {
        "identity": {
            "update_id": "battlefield-current",
            "scope": f"operation:{operation_id}",
            "session_epoch": SESSION_EPOCH,
            "operation_id": operation_id,
            "generation": generation,
            "stage": "action_issued",
            "game_frame": PROJECTION_FRAME,
        },
        "operation_id": operation_id,
        "generation": generation,
        "operation_ownership": {
            "owner_count": count,
            "integrity_status": "valid",
        },
        "operation_launch_policy": {
            "min_units": minimum,
            "max_units": count,
        },
        "operation_lifetime": {
            "completed": False,
            "completion_state": "active",
        },
        "operation_completion": {
            "terminal": False,
            "state": "active",
        },
    }


def _transfer_entry(destination_operation_id, destination_generation):
    return {
        "source_owner_id": "source-alpha",
        "source_owner_count": 4,
        "protected_minimum": 2,
        "transferable_count": 2,
        "transfer_safe": True,
        "atomic_runtime_blocker": "",
        "recommended_resolution_choices": [
            "transfer_available_units",
            "transfer_two_units",
        ],
        "safety_evidence": {
            "protected_minimum_respected": True,
            "atomic_revalidation_required": True,
        },
        "atomic_revalidation_inputs": {
            "source_owner_id": "source-alpha",
            "counterpart_operation_id": destination_operation_id,
            "requested_source_generation": 0,
            "requested_counterpart_generation": 0,
            "source_active": True,
            "destination_active": True,
            "ownership_integrity": True,
            "operation_assignments_match": True,
            "squad_assignments_match": True,
            "action_assignments_match": True,
            "role_assignments_match": True,
            "atomic_revalidation_ready": True,
            "counterpart_generation": destination_generation,
        },
    }


def _status(blackboard_dir, *, generation=1):
    overview = {
        "schema_version": 2,
        "authority": "micromachine_cpp",
        "identity": {
            "update_id": "battlefield-current",
            "scope": "battlefield",
            "session_epoch": SESSION_EPOCH,
            "generation": 7,
            "stage": "observed",
            "game_frame": PROJECTION_FRAME,
        },
        "operation_ownership": [
            _operation_projection("source-alpha", generation, 4, 2),
            _operation_projection(
                "destination-bravo",
                generation,
                4,
                1,
            ),
            _operation_projection(
                "destination-charlie",
                generation,
                3,
                1,
            ),
            _operation_projection("sibling-delta", generation, 1, 1),
        ],
        "transfer_availability": {
            "atomic_revalidation_required": True,
            "entries": [
                _transfer_entry("destination-bravo", generation),
                _transfer_entry("destination-charlie", generation),
            ],
        },
    }
    fingerprint = battlefield_overview_fingerprint(overview)
    return {
        "operation_registry_authoritative": True,
        "runtime_attached": True,
        "telemetry_current_for_process": True,
        "blackboard_scope_id": _micromachine_blackboard_scope_id(
            blackboard_dir
        ),
        "battlefield_projection": {"ok": True},
        "battlefield_projection_integrity": {"status": "valid"},
        "battlefield_projection_identity": deepcopy(overview["identity"]),
        "battlefield_projection_fingerprint": fingerprint,
        "battlefield_overview": overview,
    }


def _request(status, *, request_id="voi-ctx-request-charlie"):
    overview = status["battlefield_overview"]
    return ContextualTransferRequest.from_mapping(
        {
            "schema_version": 1,
            "choice_id": "voi-ctx-choice-charlie",
            "request_id": request_id,
            "action": "transfer_two_units",
            "source_operation_id": "source-alpha",
            "destination_operation_id": "destination-charlie",
            "source_generation": 1,
            "destination_generation": 1,
            "requested_count": 2,
            "protected_minimum": 2,
            "source_minimum": 2,
            "blackboard_scope_id": status["blackboard_scope_id"],
            "session_epoch": overview["identity"]["session_epoch"],
            "projection_frame": overview["identity"]["game_frame"],
            "projection_fingerprint": status[
                "battlefield_projection_fingerprint"
            ],
        }
    )


def _initialize_blackboard(root):
    backend = MicroMachineFilesystemBlackboard(root)
    result = MicroMachineLiveTextSession(
        backend,
        StaticJsonPolicyModulationProvider(_initial_provider_output()),
    ).submit_text(
        "initialize operations",
        current_frame=100,
        update_id="contextual-transfer-initial",
    )
    assert result.ok, result.to_dict()
    return backend, result.update


def _archive_count(backend):
    path = backend.paths.update_archive_jsonl
    if not path.exists():
        return 0
    return len(path.read_text(encoding="utf-8").splitlines())


class ContextualTransferAdmissionTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.backend, update = _initialize_blackboard(self.tempdir.name)
        assert update is not None
        self.vector = update.vector.to_dict()
        self.status = _status(self.tempdir.name)
        self.request = _request(self.status)

    def test_exact_destination_builds_one_reciprocal_pair(self):
        prepared = prepare_contextual_transfer(
            self.request,
            status=self.status,
            current_vector=self.vector,
        )
        operations = {
            operation["operation_id"]: operation
            for operation in prepared.provider_output["operations"]
        }
        self.assertEqual(
            {"source-alpha", "destination-charlie"},
            set(operations),
        )
        self.assertEqual(
            "transfer_out",
            operations["source-alpha"]["operation_edit"]["action"],
        )
        self.assertEqual(
            "destination-charlie",
            operations["source-alpha"]["operation_edit"][
                "counterpart_operation_id"
            ],
        )
        self.assertEqual(
            "transfer_in",
            operations["destination-charlie"]["operation_edit"]["action"],
        )
        self.assertNotIn("destination-bravo", operations)
        self.assertNotIn("sibling-delta", operations)

    def test_identity_count_and_minimum_mismatches_fail_closed(self):
        mutations = {
            "stale_source_generation": {"source_generation": 2},
            "stale_destination_generation": {
                "destination_generation": 2
            },
            "transfer_endpoint_mismatch": {
                "destination_operation_id": "sibling-delta"
            },
            "requested_count_mismatch": {"requested_count": 3},
            "blackboard_scope_mismatch": {
                "blackboard_scope_id": "voi-mm-scope-wrong"
            },
            "session_epoch_mismatch": {
                "session_epoch": SESSION_EPOCH + 1
            },
            "projection_frame_mismatch": {
                "projection_frame": PROJECTION_FRAME - 1
            },
            "projection_fingerprint_mismatch": {
                "projection_fingerprint": "0" * 64
            },
            "protected_minimum_mismatch": {"protected_minimum": 1},
            "source_minimum_mismatch": {"source_minimum": 1},
        }
        for expected_code, changes in mutations.items():
            with self.subTest(expected_code=expected_code):
                request = replace(self.request, **changes)
                with self.assertRaises(ContextualTransferRejectedError) as caught:
                    prepare_contextual_transfer(
                        request,
                        status=self.status,
                        current_vector=self.vector,
                    )
                self.assertEqual(expected_code, caught.exception.code)

    def test_transferable_decrease_and_runtime_safety_fail_closed(self):
        status = deepcopy(self.status)
        entry = status["battlefield_overview"]["transfer_availability"][
            "entries"
        ][1]
        entry["transferable_count"] = 1
        status["battlefield_projection_fingerprint"] = (
            battlefield_overview_fingerprint(status["battlefield_overview"])
        )
        request = replace(
            self.request,
            projection_fingerprint=status[
                "battlefield_projection_fingerprint"
            ],
        )
        with self.assertRaises(ContextualTransferRejectedError) as caught:
            prepare_contextual_transfer(
                request,
                status=status,
                current_vector=self.vector,
            )
        self.assertEqual("transferable_count_decreased", caught.exception.code)

        status = deepcopy(self.status)
        entry = status["battlefield_overview"]["transfer_availability"][
            "entries"
        ][1]
        entry["transfer_safe"] = False
        entry["atomic_runtime_blocker"] = "protected_minimum_not_respected"
        status["battlefield_projection_fingerprint"] = (
            battlefield_overview_fingerprint(status["battlefield_overview"])
        )
        request = replace(
            self.request,
            projection_fingerprint=status[
                "battlefield_projection_fingerprint"
            ],
        )
        with self.assertRaises(ContextualTransferRejectedError) as caught:
            prepare_contextual_transfer(
                request,
                status=status,
                current_vector=self.vector,
            )
        self.assertEqual(
            "protected_minimum_not_respected",
            caught.exception.code,
        )

    def test_projection_ownership_count_mismatch_fails_closed(self):
        status = deepcopy(self.status)
        source = status["battlefield_overview"]["operation_ownership"][0]
        source["operation_ownership"]["owner_count"] = 5
        fingerprint = battlefield_overview_fingerprint(
            status["battlefield_overview"]
        )
        status["battlefield_projection_fingerprint"] = fingerprint
        request = replace(
            self.request,
            projection_fingerprint=fingerprint,
        )

        with self.assertRaises(ContextualTransferRejectedError) as caught:
            prepare_contextual_transfer(
                request,
                status=status,
                current_vector=self.vector,
            )

        self.assertEqual(
            "source_ownership_count_mismatch",
            caught.exception.code,
        )

    def test_current_protected_and_source_minimum_violations_fail_closed(self):
        for minimum_field, expected_code in (
            ("protected_minimum", "protected_minimum_violation"),
            ("source_minimum", "source_minimum_violation"),
        ):
            with self.subTest(minimum_field=minimum_field):
                status = deepcopy(self.status)
                overview = status["battlefield_overview"]
                entry = overview["transfer_availability"]["entries"][1]
                if minimum_field == "protected_minimum":
                    entry["protected_minimum"] = 3
                else:
                    source = overview["operation_ownership"][0]
                    source["operation_launch_policy"]["min_units"] = 3
                fingerprint = battlefield_overview_fingerprint(overview)
                status["battlefield_projection_fingerprint"] = fingerprint
                request = replace(
                    self.request,
                    projection_fingerprint=fingerprint,
                    protected_minimum=(
                        3
                        if minimum_field == "protected_minimum"
                        else self.request.protected_minimum
                    ),
                    source_minimum=(
                        3
                        if minimum_field == "source_minimum"
                        else self.request.source_minimum
                    ),
                )
                with self.assertRaises(
                    ContextualTransferRejectedError
                ) as caught:
                    prepare_contextual_transfer(
                        request,
                        status=status,
                        current_vector=self.vector,
                    )
                self.assertEqual(expected_code, caught.exception.code)

    def test_schema_rejects_raw_control_and_unknown_action(self):
        payload = self.request.to_dict()
        for field_name in (
            "unit_tag",
            "unit_tags",
            "selected_unit_tags",
            "frame_script",
            "keyboard",
            "provider_output",
        ):
            with self.subTest(field_name=field_name):
                injected = {**payload, field_name: "forbidden"}
                with self.assertRaises(ValueError):
                    ContextualTransferRequest.from_mapping(injected)
        with self.assertRaises(ValueError):
            ContextualTransferRequest.from_mapping(
                {**payload, "action": "execute_ability"}
            )

    def test_mixed_semantic_composition_fails_closed(self):
        vector = deepcopy(self.vector)
        source = next(
            operation
            for operation in vector["operations"]
            if operation["operation_id"] == "source-alpha"
        )
        source["composition_requirements"] = [
            {
                "unit_type": "TERRAN_MARINE",
                "count": 2,
                "role": "scout",
            },
            {
                "unit_type": "TERRAN_MARAUDER",
                "count": 2,
                "role": "frontline",
            },
        ]

        with self.assertRaises(ContextualTransferRejectedError) as caught:
            prepare_contextual_transfer(
                self.request,
                status=self.status,
                current_vector=vector,
            )

        self.assertEqual(
            "semantic_transfer_selection_ambiguous",
            caught.exception.code,
        )


class ContextualTransferBridgeTest(unittest.TestCase):
    def test_bridge_bypasses_llm_publishes_once_and_replays_result(self):
        with tempfile.TemporaryDirectory() as root:
            backend, _update = _initialize_blackboard(root)
            status = _status(root)
            request = _request(status)
            llm = _ExplodingLLMControl()
            bridge = SessionLoopBridge(
                session=_UnusedSession(),
                llm_control=llm,
                micromachine_blackboard_dir=root,
            )
            bridge.start()
            self.addCleanup(bridge.stop)
            before = _archive_count(backend)

            first = bridge.submit_micromachine_contextual_transfer(
                request,
                blackboard_dir=root,
                status_resolver=lambda: deepcopy(status),
            )
            after_first = _archive_count(backend)
            second = bridge.submit_micromachine_contextual_transfer(
                request,
                blackboard_dir=root,
                status_resolver=lambda: deepcopy(status),
            )
            after_second = _archive_count(backend)

            self.assertTrue(first["ok"], first)
            self.assertEqual(first["result_id"], second["result_id"])
            self.assertEqual(before + 1, after_first)
            self.assertEqual(after_first, after_second)
            self.assertEqual(0, llm.calls)
            latest = backend.read_latest_update(
                current_frame=PROJECTION_FRAME
            )
            assert latest is not None
            operations = {
                operation.operation_id: operation
                for operation in latest.vector.operations
            }
            self.assertEqual(
                {
                    "source-alpha",
                    "destination-bravo",
                    "destination-charlie",
                    "sibling-delta",
                },
                set(operations),
            )
            self.assertEqual(
                "transfer_out",
                operations["source-alpha"].operation_edit.action,
            )
            self.assertEqual(
                "destination-charlie",
                operations[
                    "source-alpha"
                ].operation_edit.counterpart_operation_id,
            )
            self.assertEqual(
                "transfer_in",
                operations["destination-charlie"].operation_edit.action,
            )
            self.assertEqual(
                1,
                operations["destination-bravo"].generation,
            )
            self.assertEqual(1, operations["sibling-delta"].generation)

    def test_rejection_does_not_publish_or_change_latest_update(self):
        with tempfile.TemporaryDirectory() as root:
            backend, initial_update = _initialize_blackboard(root)
            assert initial_update is not None
            status = _status(root)
            request = _request(
                status,
                request_id="voi-ctx-request-rejected",
            )
            request = replace(request, requested_count=3)
            bridge = SessionLoopBridge(
                session=_UnusedSession(),
                micromachine_blackboard_dir=root,
            )
            bridge.start()
            self.addCleanup(bridge.stop)
            before = _archive_count(backend)

            with self.assertRaises(ContextualTransferRejectedError):
                bridge.submit_micromachine_contextual_transfer(
                    request,
                    blackboard_dir=root,
                    status_resolver=lambda: deepcopy(status),
                )

            self.assertEqual(before, _archive_count(backend))
            latest = backend.read_latest_update(
                current_frame=PROJECTION_FRAME
            )
            assert latest is not None
            self.assertEqual(initial_update.to_dict(), latest.to_dict())

    def test_concurrent_duplicate_requests_share_one_publish(self):
        with tempfile.TemporaryDirectory() as root:
            backend, _update = _initialize_blackboard(root)
            status = _status(root)
            request = _request(
                status,
                request_id="voi-ctx-request-concurrent",
            )
            bridge = SessionLoopBridge(
                session=_UnusedSession(),
                micromachine_blackboard_dir=root,
            )
            bridge.start()
            self.addCleanup(bridge.stop)
            resolver_started = threading.Event()
            release_resolver = threading.Event()
            resolver_calls = 0
            resolver_lock = threading.Lock()
            before = _archive_count(backend)

            def resolve_status():
                nonlocal resolver_calls
                with resolver_lock:
                    resolver_calls += 1
                resolver_started.set()
                if not release_resolver.wait(timeout=2):
                    raise AssertionError("timed out waiting to release resolver")
                return deepcopy(status)

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(
                    bridge.submit_micromachine_contextual_transfer,
                    request,
                    blackboard_dir=root,
                    status_resolver=resolve_status,
                )
                self.assertTrue(resolver_started.wait(timeout=2))
                second = pool.submit(
                    bridge.submit_micromachine_contextual_transfer,
                    request,
                    blackboard_dir=root,
                    status_resolver=resolve_status,
                )
                release_resolver.set()
                first_result = first.result(timeout=5)
                second_result = second.result(timeout=5)

            self.assertEqual(
                first_result["result_id"],
                second_result["result_id"],
            )
            self.assertEqual(1, resolver_calls)
            self.assertEqual(before + 1, _archive_count(backend))

    def test_replay_cache_scans_past_inflight_entries_and_stays_bounded(self):
        bridge = SessionLoopBridge(session=_UnusedSession())
        root = "/tmp/contextual-transfer-replay-bound"
        with bridge._contextual_transfer_replay_lock:
            for index in range(300):
                future = concurrent.futures.Future()
                if 1 <= index <= 44:
                    future.set_result({"ok": True, "result_id": str(index)})
                key = (root, f"request-{index}")
                bridge._contextual_transfer_replays[key] = (
                    _ContextualTransferReplay(
                        payload_fingerprint=f"fingerprint-{index}",
                        future=future,
                    )
                )
                bridge._contextual_transfer_replay_order.append(key)

            bridge._prune_contextual_transfer_replays(target_size=256)

            self.assertEqual(256, len(bridge._contextual_transfer_replays))
            self.assertEqual(
                256,
                len(bridge._contextual_transfer_replay_order),
            )
            self.assertIn(
                (root, "request-0"),
                bridge._contextual_transfer_replays,
            )

    def test_full_inflight_replay_cache_rejects_new_identity(self):
        with tempfile.TemporaryDirectory() as root:
            status = _status(root)
            request = _request(
                status,
                request_id="voi-ctx-request-over-capacity",
            )
            bridge = SessionLoopBridge(
                session=_UnusedSession(),
                micromachine_blackboard_dir=root,
            )
            with bridge._contextual_transfer_replay_lock:
                for index in range(256):
                    key = (root, f"inflight-{index}")
                    bridge._contextual_transfer_replays[key] = (
                        _ContextualTransferReplay(
                            payload_fingerprint=f"fingerprint-{index}",
                            future=concurrent.futures.Future(),
                        )
                    )
                    bridge._contextual_transfer_replay_order.append(key)

            with self.assertRaises(
                _ContextualTransferReplayCapacityError
            ):
                bridge.submit_micromachine_contextual_transfer(
                    request,
                    blackboard_dir=root,
                    status_resolver=lambda: deepcopy(status),
                )

            self.assertEqual(256, len(bridge._contextual_transfer_replays))

    def test_prepublication_timeout_releases_identity_for_retry(self):
        with tempfile.TemporaryDirectory() as root:
            status = _status(root)
            request = _request(
                status,
                request_id="voi-ctx-request-timeout-retry",
            )
            bridge = SessionLoopBridge(
                session=_UnusedSession(),
                micromachine_blackboard_dir=root,
            )
            accepted_requests = []

            def accept(request_record):
                accepted_requests.append(request_record)
                if len(accepted_requests) == 2:
                    request_record.future.set_result(
                        {"ok": True, "result_id": "retry-result"}
                    )

            with (
                mock.patch(
                    "starcraft_commander.web_gui."
                    "_MICROMACHINE_REQUEST_TIMEOUT_SECONDS",
                    0.01,
                ),
                mock.patch.object(
                    bridge,
                    "_accept_micromachine_request",
                    side_effect=accept,
                ),
            ):
                with self.assertRaises(concurrent.futures.TimeoutError):
                    bridge.submit_micromachine_contextual_transfer(
                        request,
                        blackboard_dir=root,
                        status_resolver=lambda: deepcopy(status),
                    )
                result = bridge.submit_micromachine_contextual_transfer(
                    request,
                    blackboard_dir=root,
                    status_resolver=lambda: deepcopy(status),
                )

            self.assertEqual("retry-result", result["result_id"])
            self.assertEqual(2, len(accepted_requests))
            self.assertTrue(accepted_requests[0].cancel_event.is_set())
            self.assertEqual(
                1,
                len(bridge._contextual_transfer_replay_order),
            )


class _ValidatedStatusLauncher:
    def __init__(self, root):
        self.root = root
        self.runtime_instance_id = "a" * 32

    def validated_snapshot(self, *, blackboard_dir):
        return _MicroMachineValidatedRuntimeSnapshot(
            metadata={
                "status": "running",
                "runtime_attached": True,
                "telemetry_current_for_process": True,
                "telemetry_stale_or_detached": False,
                "telemetry_present": True,
                "telemetry_frame": PROJECTION_FRAME,
                "runtime_instance_id": self.runtime_instance_id,
                "blackboard_dir": self.root,
            },
            telemetry_document={
                "runtime_instance_id": self.runtime_instance_id,
            },
        )


class ContextualTransferHTTPTest(unittest.TestCase):
    def _post(self, server, payload):
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            server.port,
            timeout=5,
        )
        try:
            body = json.dumps(payload).encode("utf-8")
            connection.request(
                "POST",
                "/api/micromachine/contextual-transfer",
                body=body,
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                },
            )
            response = connection.getresponse()
            document = json.loads(response.read().decode("utf-8"))
            return response.status, document
        finally:
            connection.close()

    def test_typed_http_endpoint_replays_and_rejects_identity_mismatch(self):
        with tempfile.TemporaryDirectory() as root:
            backend, _update = _initialize_blackboard(root)
            status = _status(root)
            request = _request(status)
            bridge = SessionLoopBridge(
                session=_UnusedSession(),
                micromachine_blackboard_dir=root,
            )
            bridge.micromachine_status_for_runtime = (
                lambda **_kwargs: deepcopy(status)
            )
            bridge.start()
            server = WebGuiServer(bridge=bridge, port=0)
            server._micromachine_launcher = _ValidatedStatusLauncher(root)
            server.start()
            self.addCleanup(server.stop)
            self.addCleanup(bridge.stop)
            payload = {**request.to_dict(), "blackboard_dir": root}
            before = _archive_count(backend)

            first_status, first = self._post(server, payload)
            replay_status, replay = self._post(server, payload)
            mismatch_status, mismatch = self._post(
                server,
                {
                    **payload,
                    "destination_operation_id": "destination-bravo",
                },
            )

            self.assertEqual(202, first_status)
            self.assertTrue(first["accepted"])
            self.assertEqual(202, replay_status)
            self.assertEqual(first["result_id"], replay["result_id"])
            self.assertEqual(before + 1, _archive_count(backend))
            self.assertEqual(409, mismatch_status)
            self.assertEqual(
                "request_identity_mismatch",
                mismatch["blocker"]["code"],
            )

    def test_typed_http_endpoint_rejects_raw_control_fields(self):
        with tempfile.TemporaryDirectory() as root:
            backend, _update = _initialize_blackboard(root)
            status = _status(root)
            request = _request(status)
            bridge = SessionLoopBridge(
                session=_UnusedSession(),
                micromachine_blackboard_dir=root,
            )
            bridge.start()
            server = WebGuiServer(bridge=bridge, port=0)
            server.start()
            self.addCleanup(server.stop)
            self.addCleanup(bridge.stop)
            before = _archive_count(backend)

            response_status, response = self._post(
                server,
                {
                    **request.to_dict(),
                    "blackboard_dir": root,
                    "selected_unit_tags": [1, 2],
                },
            )

            self.assertEqual(400, response_status)
            self.assertFalse(response["accepted"])
            self.assertIn("unsupported fields", response["error"])
            self.assertEqual(before, _archive_count(backend))
