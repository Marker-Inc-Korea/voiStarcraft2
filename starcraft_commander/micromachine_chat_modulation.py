"""Safe SC2 chat-to-MicroMachine modulation boundary.

The boundary here only accepts chat events that a patched MicroMachine sidecar
or telemetry adapter already exposes as structured data. It deliberately does
not read the game screen, capture keyboard input, or issue raw SC2 commands.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final, Protocol, runtime_checkable

from starcraft_commander.micromachine_live_session import (
    LiveTextModulationResult,
    MicroMachineLiveTextSession,
)
from starcraft_commander.policy_modulation import reject_raw_policy_control_keys


_UPDATE_ID_SUFFIX_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9_.:-]{1,80}$"
)


class ChatBoundaryStatus(str, Enum):
    """Batch-level result for one chat telemetry ingestion attempt."""

    ROUTED = "routed"
    NO_NEW_CHAT = "no_new_chat"
    UNSUPPORTED_NO_CHAT_SOURCE = "unsupported_no_chat_source"
    REJECTED_UNSAFE_CHAT_PAYLOAD = "rejected_unsafe_chat_payload"
    ROUTE_FAILED = "route_failed"


class ChatEventStatus(str, Enum):
    """Per-event chat routing outcome."""

    ROUTED = "routed"
    DUPLICATE = "duplicate"
    INELIGIBLE = "ineligible"
    EMPTY = "empty"
    FAILED = "failed"
    NOT_PUBLISHED = "not_published"


@runtime_checkable
class ChatEventSink(Protocol):
    """Duck-typed seam for tests or alternate live modulation sessions."""

    def submit_text(
        self,
        command_text: str,
        *,
        current_frame: int | None = None,
        update_id: str | None = None,
        tags: Sequence[str] = (),
    ) -> LiveTextModulationResult:
        """Submit one eligible chat text into the modulation pipeline."""


@dataclass(frozen=True)
class ChatEvent:
    """One sidecar-supplied chat event eligible for safe filtering."""

    message_id: str
    text: str
    frame: int
    player_id: str = ""
    player_name: str = ""
    from_user: bool = False
    source: str = "sidecar_telemetry_chat"

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> "ChatEvent":
        reject_raw_policy_control_keys(payload)
        text = _optional_text(payload, "text") or _optional_text(payload, "message")
        frame = _optional_non_negative_int(payload, "frame")
        player_id = _optional_text(payload, "player_id")
        player_name = _optional_text(payload, "player_name")
        message_id = _optional_text(payload, "message_id") or _stable_message_id(
            frame=frame,
            player_id=player_id,
            player_name=player_name,
            text=text,
        )
        return cls(
            message_id=message_id,
            text=text,
            frame=frame,
            player_id=player_id,
            player_name=player_name,
            from_user=_optional_bool(payload, "from_user"),
            source=_optional_text(payload, "source") or "sidecar_telemetry_chat",
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "message_id": self.message_id,
            "text": self.text,
            "frame": self.frame,
            "player_id": self.player_id,
            "player_name": self.player_name,
            "from_user": self.from_user,
            "source": self.source,
        }


@dataclass(frozen=True)
class ChatEventRoutingResult:
    """JSON-ready result for one chat event."""

    event: ChatEvent
    status: ChatEventStatus | str
    result: LiveTextModulationResult | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.event, ChatEvent):
            raise ValueError("event must be a ChatEvent.")
        object.__setattr__(self, "status", _coerce_event_status(self.status))

    @property
    def routed(self) -> bool:
        return self.status is ChatEventStatus.ROUTED and self.result is not None

    def to_dict(self) -> dict[str, object]:
        return {
            "event": self.event.to_dict(),
            "status": self.status.value,
            "routed": self.routed,
            "reason": self.reason,
            "result": self.result.to_dict() if self.result else None,
        }


@dataclass(frozen=True)
class ChatIngestionResult:
    """JSON-ready result for one telemetry chat ingestion batch."""

    status: ChatBoundaryStatus | str
    routed_count: int = 0
    skipped_count: int = 0
    events: tuple[ChatEventRoutingResult, ...] = ()
    error: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _coerce_boundary_status(self.status))
        object.__setattr__(
            self,
            "routed_count",
            _non_negative_int("routed_count", self.routed_count),
        )
        object.__setattr__(
            self,
            "skipped_count",
            _non_negative_int("skipped_count", self.skipped_count),
        )
        for event in self.events:
            if not isinstance(event, ChatEventRoutingResult):
                raise ValueError("events must contain ChatEventRoutingResult values.")

    @property
    def ok(self) -> bool:
        return self.status in {
            ChatBoundaryStatus.ROUTED,
            ChatBoundaryStatus.NO_NEW_CHAT,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "status": self.status.value,
            "routed_count": self.routed_count,
            "skipped_count": self.skipped_count,
            "events": [event.to_dict() for event in self.events],
            "error": self.error,
        }


class ChatEventDeduper:
    """In-memory message id deduper for one sidecar bridge process."""

    def __init__(self, seen_message_ids: Iterable[str] = ()) -> None:
        self._seen = {str(message_id) for message_id in seen_message_ids}

    def seen(self, message_id: str) -> bool:
        return message_id in self._seen

    def mark_seen(self, message_id: str) -> None:
        self._seen.add(message_id)


class MicroMachineChatModulationBridge:
    """Route sidecar chat events into MicroMachine live modulation."""

    def __init__(
        self,
        session: ChatEventSink,
        *,
        deduper: ChatEventDeduper | None = None,
        allowed_player_ids: Sequence[str] = (),
        allowed_player_names: Sequence[str] = (),
    ) -> None:
        if not callable(getattr(session, "submit_text", None)):
            raise TypeError("session must implement submit_text().")
        self.session = session
        self.deduper = deduper if deduper is not None else ChatEventDeduper()
        self.allowed_player_ids = frozenset(_clean_string_sequence(allowed_player_ids))
        self.allowed_player_names = frozenset(
            _clean_string_sequence(allowed_player_names)
        )

    @classmethod
    def from_live_session(
        cls,
        session: MicroMachineLiveTextSession,
        **kwargs: object,
    ) -> "MicroMachineChatModulationBridge":
        return cls(session, **kwargs)

    def ingest_telemetry(self, telemetry: Mapping[str, object]) -> ChatIngestionResult:
        try:
            events = extract_chat_events_from_telemetry(telemetry)
        except ValueError as exc:
            return ChatIngestionResult(
                status=ChatBoundaryStatus.REJECTED_UNSAFE_CHAT_PAYLOAD,
                error=str(exc),
            )
        if events is None:
            return ChatIngestionResult(
                status=ChatBoundaryStatus.UNSUPPORTED_NO_CHAT_SOURCE,
                error=(
                    "No sidecar chat_events field is available. OCR, keyboard hooks, "
                    "and screen scraping are intentionally unsupported."
                ),
            )
        routed: list[ChatEventRoutingResult] = []
        skipped_count = 0
        for event in events:
            outcome = self._route_one(event)
            routed.append(outcome)
            if not outcome.routed:
                skipped_count += 1
        routed_count = sum(1 for event in routed if event.routed)
        failed_count = sum(
            1 for event in routed if event.status is ChatEventStatus.FAILED
        )
        not_published_count = sum(
            1 for event in routed if event.status is ChatEventStatus.NOT_PUBLISHED
        )
        return ChatIngestionResult(
            status=(
                ChatBoundaryStatus.ROUTED
                if routed_count
                else (
                    ChatBoundaryStatus.ROUTE_FAILED
                    if failed_count or not_published_count
                    else ChatBoundaryStatus.NO_NEW_CHAT
                )
            ),
            routed_count=routed_count,
            skipped_count=skipped_count,
            events=tuple(routed),
        )

    def _route_one(self, event: ChatEvent) -> ChatEventRoutingResult:
        if not event.text.strip():
            return ChatEventRoutingResult(
                event,
                ChatEventStatus.EMPTY,
                reason="empty chat text",
            )
        if not self._is_eligible_user_event(event):
            return ChatEventRoutingResult(
                event,
                ChatEventStatus.INELIGIBLE,
                reason="chat event is not marked as an eligible user message",
            )
        if self.deduper.seen(event.message_id):
            return ChatEventRoutingResult(
                event,
                ChatEventStatus.DUPLICATE,
                reason="duplicate chat message",
            )
        try:
            result = self.session.submit_text(
                event.text,
                current_frame=event.frame,
                update_id=f"chat-{_safe_update_id_suffix(event.message_id)}",
                tags=("sc2_chat", "sidecar_telemetry"),
            )
        except Exception as exc:  # noqa: BLE001 - fail closed per event.
            return ChatEventRoutingResult(
                event,
                ChatEventStatus.FAILED,
                reason=f"{type(exc).__name__}: {exc}",
            )
        if not bool(getattr(result, "ok", False)):
            return ChatEventRoutingResult(
                event,
                ChatEventStatus.NOT_PUBLISHED,
                result=result,
                reason=str(getattr(result, "status", "not_published")),
            )
        self.deduper.mark_seen(event.message_id)
        return ChatEventRoutingResult(event, ChatEventStatus.ROUTED, result=result)

    def _is_eligible_user_event(self, event: ChatEvent) -> bool:
        if event.from_user:
            return True
        if event.player_id and event.player_id in self.allowed_player_ids:
            return True
        if event.player_name and event.player_name in self.allowed_player_names:
            return True
        return False


def extract_chat_events_from_telemetry(
    telemetry: Mapping[str, object],
) -> tuple[ChatEvent, ...] | None:
    """Extract validated sidecar chat events, or None when unsupported."""

    reject_raw_policy_control_keys(telemetry)
    raw_events = telemetry.get("chat_events")
    if raw_events is None:
        return None
    if isinstance(raw_events, (str, bytes)) or not isinstance(raw_events, Sequence):
        raise ValueError("telemetry chat_events must be a sequence of objects.")
    events: list[ChatEvent] = []
    for raw_event in raw_events:
        if not isinstance(raw_event, Mapping):
            raise ValueError("each telemetry chat event must be an object.")
        events.append(ChatEvent.from_mapping(raw_event))
    return tuple(events)


def _stable_message_id(
    *,
    frame: int,
    player_id: str,
    player_name: str,
    text: str,
) -> str:
    digest = hashlib.sha256(
        f"{frame}\0{player_id}\0{player_name}\0{text}".encode("utf-8")
    ).hexdigest()
    return digest[:16]


def _safe_update_id_suffix(message_id: str) -> str:
    cleaned = message_id.strip()
    if _UPDATE_ID_SUFFIX_PATTERN.fullmatch(cleaned):
        return cleaned
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:16]


def _optional_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key, "")
    if value is None:
        return ""
    if type(value) is not str:
        raise ValueError(f"{key} must be a string.")
    return value.strip()


def _optional_non_negative_int(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key, 0)
    return _non_negative_int(key, value)


def _optional_bool(payload: Mapping[str, object], key: str) -> bool:
    value = payload.get(key, False)
    if type(value) is not bool:
        raise ValueError(f"{key} must be a boolean.")
    return value


def _non_negative_int(field_name: str, value: object) -> int:
    if type(value) is bool or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer.")
    if value < 0:
        raise ValueError(f"{field_name} cannot be negative.")
    return value


def _clean_string_sequence(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError("allowed player identifiers must be a sequence of strings.")
    cleaned: list[str] = []
    for value in values:
        if type(value) is not str:
            raise ValueError("allowed player identifiers must be strings.")
        stripped = value.strip()
        if stripped:
            cleaned.append(stripped)
    return tuple(cleaned)


def _coerce_boundary_status(value: ChatBoundaryStatus | str) -> ChatBoundaryStatus:
    if isinstance(value, ChatBoundaryStatus):
        return value
    if type(value) is not str:
        raise ValueError("chat boundary status must be a string.")
    try:
        return ChatBoundaryStatus(value.strip().lower())
    except ValueError as exc:
        raise ValueError(f"unsupported chat boundary status: {value!r}.") from exc


def _coerce_event_status(value: ChatEventStatus | str) -> ChatEventStatus:
    if isinstance(value, ChatEventStatus):
        return value
    if type(value) is not str:
        raise ValueError("chat event status must be a string.")
    try:
        return ChatEventStatus(value.strip().lower())
    except ValueError as exc:
        raise ValueError(f"unsupported chat event status: {value!r}.") from exc
