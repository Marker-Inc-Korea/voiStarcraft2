"""Tests for safe SC2 chat-to-MicroMachine modulation boundary."""

from starcraft_commander.micromachine_chat_modulation import (
    ChatBoundaryStatus,
    ChatEventDeduper,
    ChatEventStatus,
    MicroMachineChatModulationBridge,
    extract_chat_events_from_telemetry,
)
from starcraft_commander.micromachine_live_session import (
    KeywordPolicyModulationProvider,
    MicroMachineLiveTextSession,
)
from starcraft_commander.micromachine_runtime import MicroMachineInMemoryBlackboard


def test_chat_telemetry_without_chat_events_reports_unsupported() -> None:
    session = MicroMachineLiveTextSession(
        MicroMachineInMemoryBlackboard(),
        KeywordPolicyModulationProvider(),
    )
    bridge = MicroMachineChatModulationBridge(session)

    result = bridge.ingest_telemetry({"frame": 12, "managers": {}})

    assert result.status is ChatBoundaryStatus.UNSUPPORTED_NO_CHAT_SOURCE
    assert result.ok is False
    assert result.routed_count == 0
    assert "OCR" in result.error
    assert not result.to_dict()["events"]


def test_chat_event_routes_eligible_user_text_to_live_modulation() -> None:
    backend = MicroMachineInMemoryBlackboard()
    session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())
    bridge = MicroMachineChatModulationBridge(session)

    result = bridge.ingest_telemetry(
        {
            "frame": 20,
            "chat_events": [
                {
                    "message_id": "msg-1",
                    "text": "탱크로 안전하게 수비해",
                    "frame": 20,
                    "player_id": "1",
                    "player_name": "human",
                    "from_user": True,
                }
            ],
        }
    )

    assert result.status is ChatBoundaryStatus.ROUTED
    assert result.routed_count == 1
    assert result.events[0].status is ChatEventStatus.ROUTED
    assert backend.latest_update is not None
    assert backend.latest_update.update_id == "chat-msg-1"
    assert backend.latest_update.vector.goal == "탱크로 안전하게 수비해"


def test_chat_event_dedupe_skips_repeated_message_id() -> None:
    backend = MicroMachineInMemoryBlackboard()
    session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())
    bridge = MicroMachineChatModulationBridge(session, deduper=ChatEventDeduper())
    telemetry = {
        "chat_events": [
            {
                "message_id": "repeat-1",
                "text": "공격 준비",
                "frame": 30,
                "from_user": True,
            }
        ],
    }

    first = bridge.ingest_telemetry(telemetry)
    second = bridge.ingest_telemetry(telemetry)

    assert first.status is ChatBoundaryStatus.ROUTED
    assert second.status is ChatBoundaryStatus.NO_NEW_CHAT
    assert second.routed_count == 0
    assert second.skipped_count == 1
    assert second.events[0].status is ChatEventStatus.DUPLICATE
    assert len(backend.update_archive) == 1


def test_chat_event_update_id_suffix_is_sanitized() -> None:
    backend = MicroMachineInMemoryBlackboard()
    session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())
    bridge = MicroMachineChatModulationBridge(session)

    result = bridge.ingest_telemetry(
        {
            "chat_events": [
                {
                    "message_id": "unsafe id/with spaces",
                    "text": "수비해",
                    "frame": 35,
                    "from_user": True,
                }
            ],
        }
    )

    assert result.status is ChatBoundaryStatus.ROUTED
    assert backend.latest_update is not None
    assert backend.latest_update.update_id.startswith("chat-")
    assert " " not in backend.latest_update.update_id
    assert "/" not in backend.latest_update.update_id


def test_chat_route_failure_is_event_scoped_and_not_deduped() -> None:
    class FailingSession:
        def __init__(self) -> None:
            self.calls = 0

        def submit_text(self, command_text, **kwargs):
            self.calls += 1
            raise RuntimeError("provider unavailable")

    session = FailingSession()
    bridge = MicroMachineChatModulationBridge(session)
    telemetry = {
        "chat_events": [
            {
                "message_id": "retry-1",
                "text": "수비해",
                "frame": 36,
                "from_user": True,
            }
        ],
    }

    first = bridge.ingest_telemetry(telemetry)
    second = bridge.ingest_telemetry(telemetry)

    assert first.status is ChatBoundaryStatus.ROUTE_FAILED
    assert first.ok is False
    assert first.events[0].status is ChatEventStatus.FAILED
    assert second.status is ChatBoundaryStatus.ROUTE_FAILED
    assert session.calls == 2


def test_chat_non_published_result_is_not_routed_or_deduped() -> None:
    class ClarifyingSession:
        def __init__(self) -> None:
            self.calls = 0

        def submit_text(self, command_text, **kwargs):
            self.calls += 1
            return MicroMachineLiveTextSession(
                MicroMachineInMemoryBlackboard(),
                KeywordPolicyModulationProvider(),
            ).submit_text("어떻게?", current_frame=37, update_id="clarify-1")

    session = ClarifyingSession()
    bridge = MicroMachineChatModulationBridge(session)
    telemetry = {
        "chat_events": [
            {
                "message_id": "clarify-1",
                "text": "어떻게?",
                "frame": 37,
                "from_user": True,
            }
        ],
    }

    first = bridge.ingest_telemetry(telemetry)
    second = bridge.ingest_telemetry(telemetry)

    assert first.status is ChatBoundaryStatus.ROUTE_FAILED
    assert first.ok is False
    assert first.routed_count == 0
    assert first.events[0].status is ChatEventStatus.NOT_PUBLISHED
    assert first.events[0].result is not None
    assert second.events[0].status is ChatEventStatus.NOT_PUBLISHED
    assert session.calls == 2


def test_chat_allowlist_rejects_plain_string_configuration() -> None:
    session = MicroMachineLiveTextSession(
        MicroMachineInMemoryBlackboard(),
        KeywordPolicyModulationProvider(),
    )

    try:
        MicroMachineChatModulationBridge(
            session,
            allowed_player_names="Commander",
        )
    except ValueError as exc:
        assert "sequence of strings" in str(exc)
    else:
        raise AssertionError("plain string allowlist should be rejected")


def test_chat_event_requires_user_marker_or_allowlist() -> None:
    backend = MicroMachineInMemoryBlackboard()
    session = MicroMachineLiveTextSession(backend, KeywordPolicyModulationProvider())
    bridge = MicroMachineChatModulationBridge(
        session,
        allowed_player_names=("Commander",),
    )

    result = bridge.ingest_telemetry(
        {
            "chat_events": [
                {
                    "message_id": "enemy-1",
                    "text": "수비해",
                    "frame": 40,
                    "player_name": "Enemy",
                },
                {
                    "message_id": "human-1",
                    "text": "수비해",
                    "frame": 41,
                    "player_name": "Commander",
                },
            ],
        }
    )

    assert result.status is ChatBoundaryStatus.ROUTED
    assert result.routed_count == 1
    assert result.skipped_count == 1
    assert result.events[0].status is ChatEventStatus.INELIGIBLE
    assert result.events[1].status is ChatEventStatus.ROUTED
    assert backend.latest_update is not None
    assert backend.latest_update.update_id == "chat-human-1"


def test_chat_event_extraction_rejects_raw_control_keys() -> None:
    result = MicroMachineChatModulationBridge(
        MicroMachineLiveTextSession(
            MicroMachineInMemoryBlackboard(),
            KeywordPolicyModulationProvider(),
        )
    ).ingest_telemetry(
        {
            "chat_events": [
                {
                    "message_id": "bad-1",
                    "text": "수비해",
                    "frame": 50,
                    "from_user": True,
                    "raw_actions": [{"unit_tag": 1, "attack_move": [1, 2]}],
                }
            ],
        }
    )

    assert result.status is ChatBoundaryStatus.REJECTED_UNSAFE_CHAT_PAYLOAD
    assert "raw runtime control" in result.error


def test_chat_events_can_be_extracted_with_stable_generated_ids() -> None:
    events = extract_chat_events_from_telemetry(
        {
            "chat_events": [
                {
                    "text": "공격",
                    "frame": 7,
                    "player_id": "p1",
                    "player_name": "Commander",
                    "from_user": True,
                }
            ]
        }
    )

    assert events is not None
    assert len(events) == 1
    assert events[0].message_id
    assert events[0].message_id == extract_chat_events_from_telemetry(
        {
            "chat_events": [
                {
                    "text": "공격",
                    "frame": 7,
                    "player_id": "p1",
                    "player_name": "Commander",
                    "from_user": True,
                }
            ]
        }
    )[0].message_id
