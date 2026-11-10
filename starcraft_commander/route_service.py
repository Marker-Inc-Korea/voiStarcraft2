"""Server-side glue for ``POST /api/route/sketch``: parse -> snapshot the game -> interpret -> execute -> events.

The HTTP layer (``web_gui``) only authenticates, reads the body and serialises the answer; everything else lives here so
it is testable without a socket.  ``runner`` runs the executor coroutine on the loop that owns the live BotAI (the
bridge loop); the default is ``asyncio.run``, which is right for offline fakes only.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Mapping
from dataclasses import replace

from starcraft_commander import route_execution as rx
from starcraft_commander import route_sketch as rs

Runner = Callable[[object], object]


def _default_runner(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


class RouteService:
    def __init__(
        self,
        *,
        executor: object | None,
        vlm: rs.VlmReader | None = None,
        runner: Runner | None = None,
        calibration: tuple[float, float] = (0.0, 0.0),
        events: rx.RouteEventLog | None = None,
    ) -> None:
        self.executor = executor
        self.vlm = vlm
        self.runner = runner or _default_runner
        self.calibration = calibration
        self.events = events or rx.RouteEventLog()
        self._controller: rx.RouteController | None = None
        self._lock = threading.Lock()

    def set_publisher(self, publisher: Callable[[str, Mapping[str, object]], object]) -> None:
        self.events.publisher = publisher

    def _controller_for(self) -> rx.RouteController:
        with self._lock:
            if self._controller is None:
                self._controller = rx.RouteController(executor=self.executor, events=self.events)
                self._controller.attach()
            return self._controller

    def handle(self, body: Mapping[str, object]) -> dict[str, object]:
        """Return the 202 body; raises :class:`route_sketch.SketchRequestError` for a malformed request (400)."""

        req = rs.SketchRequest.from_payload(body)
        executor = self.executor
        adapter = getattr(executor, "bot", None)
        if executor is None or adapter is None:
            return self._plain(req, "none", "게임에 연결되어 있지 않아 아무것도 하지 않았습니다.", "game_not_attached")
        world = rx.world_from_bot(adapter, calibration=self.calibration)
        if world is None:
            return self._plain(req, "none", "아직 지도 정보를 읽지 못해 아무것도 하지 않았습니다.", "no_game_info")
        controller = self._controller_for()
        lifecycle = executor.direct_lifecycle  # type: ignore[attr-defined]
        # Units another Direct command already owns are not available to a new route.
        world.own_units = [u for u in world.own_units if lifecycle.unit_owner(u[1]) is None]
        decision = rs.interpret_sketch(req, world, vlm=self.vlm)
        if decision.kind == "execute":
            frame_of = getattr(adapter, "_game_frame", None)
            frame = frame_of() if callable(frame_of) else 0
            start = self.runner(controller.start(decision, world, frame=frame))
            if not start.ok:  # type: ignore[attr-defined]
                decision = replace(
                    decision, kind="refuse", route=None, squad=None, unit_tags=(), reason=start.reason,  # type: ignore[attr-defined]
                    narration_ko=_START_FAILURE_KO.get(start.reason, "명령을 실행하지 못해 아무것도 하지 않았습니다."),  # type: ignore[attr-defined]
                )
        return decision.to_dict()

    @staticmethod
    def _plain(req: rs.SketchRequest, kind: str, narration: str, reason: str) -> dict[str, object]:
        return {
            "kind": kind, "plan_id": rs._plan_id(req, kind), "narration_ko": narration, "question_ko": None,
            "route": None, "squad": None, "reason": reason,
        }


_START_FAILURE_KO = {
    "direct_control_conflict": "그 유닛들은 이미 다른 직접 명령을 수행 중이라 새 경로를 주지 않았습니다.",
    "squad_missing": "선택한 유닛 중 일부가 사라져서 실행하지 않았습니다.",
    "execution_refused": "게임이 경로 명령을 받아들이지 않아 실행하지 않았습니다.",
    "execution_exception": "경로 명령을 보내는 중 오류가 나서 실행하지 않았습니다.",
}
