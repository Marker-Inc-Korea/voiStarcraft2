from __future__ import annotations

import asyncio
import time

from starcraft_commander.policy_modulation import PolicyModulationVector
from starcraft_commander.jev_router import (
    JEV_PROTOCOL_VERSION,
    JEVRouter,
    route_policy_vector_with_jev,
)


def _operation_vector() -> PolicyModulationVector:
    return PolicyModulationVector.from_mapping(
        {
            "goal": "pressure enemy natural",
            "command_layer": "operation",
            "operations": [
                {
                    "operation_id": "op-jev-1",
                    "tactical_task": {
                        "task_type": "pressure_with_main_army",
                        "location_intent": "enemy_natural",
                    },
                }
            ],
        }
    )


class FakeJEV:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.requests = []

    async def decide(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.response


def test_disabled_jev_preserves_existing_local_route() -> None:
    result = asyncio.run(
        route_policy_vector_with_jev(
            _operation_vector(), request_id="local-only", eligible_layers=("operation",)
        )
    )
    assert result.status == "disabled"
    assert result.allowed is True
    assert result.route is not None
    assert [call.name for call in result.route.tool_calls] == ["sc2.direct.execute"]


def test_jev_allow_only_authorizes_existing_route() -> None:
    fake = FakeJEV(
        {
            "protocol_version": JEV_PROTOCOL_VERSION,
            "request_id": "allow-1",
            "decision": "allow",
            "reason": "approved by fake endpoint",
            "audit": {"review_id": "review-1"},
            # An endpoint cannot add these; the test keeps response bounded.
        }
    )
    result = asyncio.run(
        route_policy_vector_with_jev(
            _operation_vector(),
            endpoint=fake,
            eligible_layers=("operation",),
            request_id="allow-1",
        )
    )
    assert result.status == "allowed"
    assert result.route is not None
    assert result.audit["jev_decision"] == "allow"
    assert fake.requests[0]["allowed_tools"] == ["sc2.direct.execute"]
    assert fake.requests[0]["route"]["tool_calls"][0]["name"] == "sc2.direct.execute"


def test_macro_request_advertises_the_macro_route_tool_only() -> None:
    fake = FakeJEV(
        {
            "protocol_version": JEV_PROTOCOL_VERSION,
            "request_id": "macro-1",
            "decision": "allow",
        }
    )
    vector = PolicyModulationVector.from_mapping(
        {"goal": "keep making marines", "command_layer": "macro"}
    )
    result = asyncio.run(
        route_policy_vector_with_jev(
            vector,
            endpoint=fake,
            eligible_layers=("macro",),
            request_id="macro-1",
        )
    )
    assert result.status == "allowed"
    assert fake.requests[0]["allowed_tools"] == ["micromachine.policy"]


def test_jev_deny_fails_closed_without_a_route() -> None:
    fake = FakeJEV(
        {
            "protocol_version": JEV_PROTOCOL_VERSION,
            "request_id": "deny-1",
            "decision": "deny",
            "reason": "risk review denied",
        }
    )
    result = asyncio.run(
        route_policy_vector_with_jev(
            _operation_vector(),
            endpoint=fake,
            eligible_layers=("operation",),
            request_id="deny-1",
        )
    )
    assert result.status == "denied"
    assert result.allowed is False
    assert result.route is None


def test_invalid_jev_response_rejects_raw_execution_fields() -> None:
    fake = FakeJEV(
        {
            "protocol_version": JEV_PROTOCOL_VERSION,
            "request_id": "invalid-1",
            "decision": "allow",
            "tool_calls": [{"name": "raw.sc2.method"}],
        }
    )
    result = asyncio.run(
        route_policy_vector_with_jev(
            _operation_vector(),
            endpoint=fake,
            eligible_layers=("operation",),
            request_id="invalid-1",
        )
    )
    assert result.status == "invalid"
    assert result.allowed is False
    assert result.route is None
    assert "unsupported fields" in result.error


def test_invalid_jev_response_rejects_non_object_audit() -> None:
    fake = FakeJEV(
        {
            "protocol_version": JEV_PROTOCOL_VERSION,
            "request_id": "invalid-audit-1",
            "decision": "allow",
            "audit": "not-an-object",
        }
    )
    result = asyncio.run(
        route_policy_vector_with_jev(
            _operation_vector(),
            endpoint=fake,
            eligible_layers=("operation",),
            request_id="invalid-audit-1",
        )
    )
    assert result.status == "invalid"
    assert "audit must be an object" in result.error


def test_timeout_or_transport_failure_is_fail_closed_with_bounded_retry() -> None:
    fake = FakeJEV(error=TimeoutError("fake timeout"))
    result = asyncio.run(
        route_policy_vector_with_jev(
            _operation_vector(),
            endpoint=fake,
            eligible_layers=("operation",),
            request_id="timeout-1",
            retries=2,
            timeout_seconds=0.01,
        )
    )
    assert result.status == "unavailable"
    assert result.allowed is False
    assert len(fake.requests) == 3


def test_router_authorize_itself_returns_unavailable_without_wrapper() -> None:
    fake = FakeJEV(error=ConnectionError("offline"))
    result = asyncio.run(
        JEVRouter(fake, eligible_layers=("operation",), retries=0).authorize(
            _operation_vector(), request_id="direct-unavailable"
        )
    )
    assert result.status == "unavailable"
    assert result.allowed is False
    assert result.route is None


def test_synchronous_transport_timeout_is_also_bounded() -> None:
    class SlowJEV:
        def __init__(self):
            self.calls = 0

        def decide(self, request):
            self.calls += 1
            time.sleep(0.05)
            return {"protocol_version": JEV_PROTOCOL_VERSION, "request_id": "slow-1", "decision": "allow"}

    fake = SlowJEV()
    result = asyncio.run(
        route_policy_vector_with_jev(
            _operation_vector(),
            endpoint=fake,
            eligible_layers=("operation",),
            request_id="slow-1",
            retries=1,
            timeout_seconds=0.005,
        )
    )
    assert result.status == "unavailable"
    assert result.allowed is False
    assert fake.calls == 2


def test_unconfigured_layer_does_not_call_jev_endpoint() -> None:
    fake = FakeJEV({})
    result = asyncio.run(
        JEVRouter(fake, eligible_layers=("micro",)).authorize(
            _operation_vector(), request_id="not-eligible"
        )
    )
    assert result.status == "not_eligible"
    assert result.route is not None
    assert fake.requests == []
