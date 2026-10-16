"""Deduplicated, owned host callbacks after Direct workflow release."""

from __future__ import annotations

import asyncio
import inspect
import os
from pathlib import Path
from collections.abc import Awaitable, Callable, Sequence

from starcraft_commander.contracts import SC2ExecutionError
from starcraft_commander.direct_command_lifecycle import DirectCommandLease

ReleaseCallback = Callable[[DirectCommandLease], object]


class DirectLeaseEventWriter:
    """Serialize terminal leases for the native MicroMachine bridge.

    The append-only record is intentionally dependency-free and consumed by
    ``voi_direct_lease_bridge.hpp``. Runtime identity is mandatory so a late
    callback from an older game cannot resume a newer owner.
    """

    def __init__(self, path: str | os.PathLike[str], runtime_instance_id: str) -> None:
        self.path = Path(path)
        self.runtime_instance_id = str(runtime_instance_id).strip()
        if len(self.runtime_instance_id) != 32 or any(
            char not in "0123456789abcdef" for char in self.runtime_instance_id
        ):
            raise ValueError("runtime_instance_id must be 32 lowercase hex characters")

    def __call__(self, lease: DirectCommandLease) -> None:
        if lease.state.value not in {"completed", "cancelled", "expired", "failed"}:
            return
        fields = {
            "protocol_version": "voi-mm-bridge/v1",
            "command_id": lease.command_id,
            "state": lease.state.value,
            "release_reason": lease.release_reason,
            "runtime_instance_id": self.runtime_instance_id,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as output:
            output.write(";".join(f"{key}={str(value).replace(';', '_')}" for key, value in fields.items()) + "\n")


def direct_lease_event_writer_from_env() -> DirectLeaseEventWriter | None:
    path = os.environ.get("VOI_MICROMACHINE_RELEASE_EVENTS", "").strip()
    runtime = os.environ.get("VOI_MICROMACHINE_RUNTIME_INSTANCE_ID", "").strip()
    if not path:
        return None
    return DirectLeaseEventWriter(path, runtime)


def same_callback(left: object, right: object) -> bool:
    """Match callback identity, including freshly accessed bound methods."""

    return left is right or (
        inspect.ismethod(left)
        and inspect.ismethod(right)
        and left.__self__ is right.__self__
        and left.__func__ is right.__func__
    )


class DirectReleaseListeners:
    """Own async work and errors; lifecycle/executor still decide when to emit."""

    def __init__(self) -> None:
        self.callbacks: list[ReleaseCallback] = []
        self.errors: list[SC2ExecutionError] = []
        self._tasks: set[asyncio.Task[None]] = set()

    @classmethod
    def preserving(cls, callback: ReleaseCallback | None) -> DirectReleaseListeners:
        owner = getattr(callback, "__self__", None)
        if isinstance(owner, cls) and same_callback(callback, owner.emit):
            return owner
        listeners = cls()
        if callback is not None:
            listeners.add(callback)
        return listeners

    def add(self, callback: ReleaseCallback) -> None:
        if not callable(callback):
            raise TypeError("direct release listener must be callable")
        if not any(same_callback(callback, old) for old in self.callbacks):
            self.callbacks.append(callback)

    @property
    def has_pending(self) -> bool:
        return any(not task.done() for task in self._tasks)

    def emit(
        self, lease: DirectCommandLease, extras: Sequence[ReleaseCallback] = ()
    ) -> None:
        invoked: list[ReleaseCallback] = []
        for callback in (*self.callbacks, *extras):
            if any(same_callback(callback, old) for old in invoked):
                continue
            invoked.append(callback)
            try:
                result = callback(lease)
                if inspect.isawaitable(result):
                    self._schedule(result, lease)
            except Exception as error:
                self._record(error, lease)

    def _record(self, error: BaseException, lease: DirectCommandLease) -> None:
        self.errors.append(SC2ExecutionError(
            message=f"direct release callback failed: {error}",
            exception_type=type(error).__name__,
            metadata={"command_id": lease.command_id, "release_reason": lease.release_reason},
        ))

    def _schedule(self, result: Awaitable[object], lease: DirectCommandLease) -> None:
        async def invoke() -> None:
            try:
                await result
            except (Exception, asyncio.CancelledError) as error:
                self._record(error, lease)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(invoke())
            return
        task = loop.create_task(invoke())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        """Hosts await this before shutdown; emit is nonblocking on a live loop."""

        while self._tasks:
            pending = tuple(self._tasks)
            await asyncio.gather(*pending)
            self._tasks.difference_update(pending)
