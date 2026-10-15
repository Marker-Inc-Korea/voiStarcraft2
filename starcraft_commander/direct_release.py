"""Deduplicated, owned host callbacks after Direct workflow release."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Sequence

from starcraft_commander.contracts import SC2ExecutionError
from starcraft_commander.direct_command_lifecycle import DirectCommandLease

ReleaseCallback = Callable[[DirectCommandLease], object]


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
