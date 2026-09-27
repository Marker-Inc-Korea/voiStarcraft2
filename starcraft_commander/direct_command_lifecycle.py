"""Small, runtime-independent lifecycle for direct SC2 command leases.

Direct commands temporarily own a semantic squad or target.  The lease is
explicitly released by a completion observation, cancellation, or TTL expiry;
there is no implicit fallback to MicroMachine while a direct lease is active.
The controller is deliberately independent from python-sc2 so MCP, tests, and
the live game loop can share the same contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class DirectCommandState(str, Enum):
    PENDING = "pending"
    DISPATCHED = "dispatched"
    ACTIVE = "active"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class DirectCommandLease:
    command_id: str
    issued_at_frame: int
    expires_at_frame: int
    completion_conditions: tuple[str, ...] = ()
    state: DirectCommandState = DirectCommandState.ACTIVE
    release_reason: str = ""
    evidence: Mapping[str, object] = field(default_factory=dict)

    @property
    def control_owned(self) -> bool:
        return self.state in {
            DirectCommandState.DISPATCHED,
            DirectCommandState.ACTIVE,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "command_id": self.command_id,
            "issued_at_frame": self.issued_at_frame,
            "expires_at_frame": self.expires_at_frame,
            "completion_conditions": list(self.completion_conditions),
            "state": self.state.value,
            "control_owned": self.control_owned,
            "release_reason": self.release_reason,
            "evidence": dict(self.evidence),
        }


class DirectCommandLifecycle:
    """In-memory authoritative lease registry used by direct tool calls."""

    def __init__(self, *, game_loops_per_second: float = 22.4) -> None:
        if game_loops_per_second <= 0:
            raise ValueError("game_loops_per_second must be positive")
        self.game_loops_per_second = float(game_loops_per_second)
        self._leases: dict[str, DirectCommandLease] = {}

    def dispatch(
        self,
        *,
        command_id: str,
        issued_at_frame: int,
        ttl_seconds: int,
        completion_conditions: tuple[str, ...] = (),
    ) -> DirectCommandLease:
        command_id = str(command_id).strip()
        if not command_id:
            raise ValueError("command_id must be non-empty")
        if issued_at_frame < 0 or ttl_seconds < 1:
            raise ValueError("issued_at_frame must be non-negative and ttl positive")
        expires = issued_at_frame + max(
            1, round(ttl_seconds * self.game_loops_per_second)
        )
        # The externally visible lease is active once the direct executor has
        # accepted the plan.  ``PENDING`` and ``DISPATCHED`` remain explicit
        # contract states for integrations that model the hand-off in finer
        # detail; the synchronous MCP boundary completes that hand-off in one
        # call and therefore does not expose an intermediate lease.
        lease = DirectCommandLease(
            command_id=command_id,
            issued_at_frame=issued_at_frame,
            expires_at_frame=expires,
            completion_conditions=tuple(str(item) for item in completion_conditions),
        )
        self._leases[command_id] = lease
        return lease

    def pending(
        self,
        *,
        command_id: str,
        issued_at_frame: int,
        ttl_seconds: int,
        completion_conditions: tuple[str, ...] = (),
    ) -> DirectCommandLease:
        """Register a command before an executor dispatches it."""

        command_id = str(command_id).strip()
        if not command_id:
            raise ValueError("command_id must be non-empty")
        if issued_at_frame < 0 or ttl_seconds < 1:
            raise ValueError("issued_at_frame must be non-negative and ttl positive")
        expires = issued_at_frame + max(1, round(ttl_seconds * self.game_loops_per_second))
        lease = DirectCommandLease(
            command_id=command_id,
            issued_at_frame=issued_at_frame,
            expires_at_frame=expires,
            completion_conditions=tuple(str(item) for item in completion_conditions),
            state=DirectCommandState.PENDING,
        )
        self._leases[command_id] = lease
        return lease

    def mark_dispatched(self, command_id: str) -> DirectCommandLease:
        """Advance a pending lease after the direct executor accepts it."""

        lease = self._require(command_id)
        if lease.state is not DirectCommandState.PENDING:
            return lease
        return self._replace(lease, DirectCommandState.DISPATCHED, "", lease.evidence)

    def activate(self, command_id: str) -> DirectCommandLease:
        """Mark a dispatched lease as actively owning its squad."""

        lease = self._require(command_id)
        if lease.state is not DirectCommandState.DISPATCHED:
            return lease
        return self._replace(lease, DirectCommandState.ACTIVE, "", lease.evidence)

    def observe(
        self,
        command_id: str,
        *,
        frame: int,
        evidence: Mapping[str, object] | None = None,
    ) -> DirectCommandLease:
        lease = self._require(command_id)
        if lease.state not in {
            DirectCommandState.PENDING,
            DirectCommandState.DISPATCHED,
            DirectCommandState.ACTIVE,
        }:
            return lease
        evidence_map = dict(evidence or {})
        if frame >= lease.expires_at_frame:
            return self._replace(lease, DirectCommandState.EXPIRED, "ttl_expired", evidence_map)
        if _conditions_satisfied(lease.completion_conditions, evidence_map):
            return self._replace(lease, DirectCommandState.COMPLETED, "completion_conditions", evidence_map)
        return self._replace(lease, DirectCommandState.ACTIVE, "", evidence_map)

    def cancel(self, command_id: str, *, reason: str = "cancelled_by_user") -> DirectCommandLease:
        lease = self._require(command_id)
        if lease.state not in {
            DirectCommandState.PENDING,
            DirectCommandState.DISPATCHED,
            DirectCommandState.ACTIVE,
        }:
            return lease
        return self._replace(lease, DirectCommandState.CANCELLED, reason, lease.evidence)

    def fail(
        self,
        command_id: str,
        *,
        reason: str = "direct_execution_failed",
        evidence: Mapping[str, object] | None = None,
    ) -> DirectCommandLease:
        """Release a lease after the runtime refuses or cannot apply a plan."""

        lease = self._require(command_id)
        if lease.state not in {
            DirectCommandState.PENDING,
            DirectCommandState.DISPATCHED,
            DirectCommandState.ACTIVE,
        }:
            return lease
        return self._replace(
            lease,
            DirectCommandState.FAILED,
            reason,
            evidence if evidence is not None else lease.evidence,
        )

    def get(self, command_id: str) -> DirectCommandLease | None:
        return self._leases.get(str(command_id).strip())

    def _require(self, command_id: str) -> DirectCommandLease:
        lease = self.get(command_id)
        if lease is None:
            raise KeyError(f"unknown direct command: {command_id}")
        return lease

    def _replace(
        self,
        lease: DirectCommandLease,
        state: DirectCommandState,
        reason: str,
        evidence: Mapping[str, object],
    ) -> DirectCommandLease:
        updated = DirectCommandLease(
            command_id=lease.command_id,
            issued_at_frame=lease.issued_at_frame,
            expires_at_frame=lease.expires_at_frame,
            completion_conditions=lease.completion_conditions,
            state=state,
            release_reason=reason,
            evidence=dict(evidence),
        )
        self._leases[lease.command_id] = updated
        return updated


def _conditions_satisfied(conditions: tuple[str, ...], evidence: Mapping[str, object]) -> bool:
    if not conditions:
        return False
    return all(bool(evidence.get(condition)) for condition in conditions)
