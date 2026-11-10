"""Minimap route sketch -> validated route decision (pure; no I/O, no SC2, no network).

The user holds a chord and draws on the SC2 minimap; the overlay sends the strokes normalised to the *minimap frame*
(0..1, origin top-left).  This module turns them into a route:

* **Geometry is deterministic** and comes only from the strokes: minimap frame -> world through an aspect-preserving
  letterbox of the playable area (:func:`frame_to_world`; the single place that assumption lives, plus a calibration
  offset), Ramer-Douglas-Peucker simplification to at most :data:`MAX_POINTS_PER_STROKE` points, snapping each point to
  the nearest pathable point, Korean labels.
* **Semantics** (which stroke is a path / target circle / avoid mark / squad selection, the order, move vs attack, which
  squad) come from an injected :class:`VlmReader`, or from a deterministic fallback when no reader is given.
* **The validator never executes what it cannot trace back to the strokes**: a VLM point that is not on a stroke ->
  ``refuse``; an avoid mark crossed by the path, an unresolved squad, a garbled/low-confidence/failed VLM answer ->
  ``ask`` (once, Korean) or ``none``; raw coordinates / prompt-injection in the transcript -> ``refuse``.

What only a live game can confirm: that SC2 really draws the playable area letterboxed and centred in the minimap
square (see ``docs/route-sketch.md``).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Protocol, runtime_checkable

from starcraft_commander.micromachine_terran_capabilities import (
    TERRAN_UNIT_FAMILIES,
    TERRAN_UNIT_FAMILY_BY_ALIAS,
    canonical_terran_unit_family,
)
from starcraft_commander.realtime_intent import transcript_is_raw_control_or_injection

MAX_STROKES: Final[int] = 20
MAX_TOTAL_POINTS: Final[int] = 2000
MAX_IMAGE_BYTES: Final[int] = 1_500_000
MAX_TRANSCRIPT_CHARS: Final[int] = 2000
MAX_POINTS_PER_STROKE: Final[int] = 8
MIN_CONFIDENCE: Final[float] = 0.5
NOISE_LENGTH: Final[float] = 0.03
"""Frame-normalised length below which a stroke is a tap/jitter, not a drawing."""
INVENTED_POINT_TOLERANCE: Final[float] = 0.02
"""A VLM-supplied point further than this (frame units) from every stroke is invented."""
AVOID_CLEARANCE: Final[float] = 0.02
CLUSTER_LINK: Final[float] = 3.0
"""World units: own units closer than this (chained) form one cluster."""
CLUSTER_START_RADIUS: Final[float] = 6.0
"""World units: the path's first point must be this close to exactly one own-unit cluster to name it the squad."""
PLACE_MATCH_RADIUS: Final[float] = 25.0
"""World units: a place the user NAMED must lie this close to where the route ends, else we ask."""
IGNORED_STROKE_FRACTION: Final[float] = 0.35
"""A stroke the VLM calls noise (or leaves unassigned) may be at most this fraction of the longest path stroke."""
SECOND_PATH_END_DISTANCE: Final[float] = 15.0
"""World units: two strokes leaving the squad whose ends are further apart are two different destinations."""
TWO_PATHS_QUESTION: Final[str] = "경로가 두 개로 보여요. 어느 쪽으로 갈까요?"
CHAIN_GAP: Final[float] = 15.0
"""World units: consecutive path strokes must end/start this close together to count as one chained route."""
ARRIVAL_RADIUS: Final[float] = 5.0
"""World units: a leading route point this close to the squad is where it already stands, not a waypoint."""
RDP_EPSILON: Final[float] = 0.012
VALID_ROLES: Final[frozenset[str]] = frozenset({"path", "target", "avoid", "squad_select", "noise"})
VALID_ACTIONS: Final[frozenset[str]] = frozenset({"move", "attack"})
_EDGE_TOLERANCE: Final[float] = 1.0  # world units a point may sit outside the playable area and be clamped
_COUNT_UNITS: Final[str] = "기|마리|명|개|대"


class SketchRequestError(ValueError):
    """The sketch payload is malformed or over a limit (HTTP 400)."""


Stroke = tuple[tuple[float, float, float], ...]


@dataclass(frozen=True)
class SketchRequest:
    update_id: str
    strokes: tuple[Stroke, ...]
    image_png: bytes | None = None
    transcript: str = ""
    minimap_rect: Mapping[str, object] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, body: Mapping[str, object]) -> "SketchRequest":
        if not isinstance(body, Mapping):
            raise SketchRequestError("본문은 JSON 객체여야 합니다.")
        update_id = body.get("update_id")
        if type(update_id) is not str or not update_id.strip() or len(update_id) > 120:
            raise SketchRequestError("update_id는 비어 있지 않은 문자열이어야 합니다.")
        raw_strokes = body.get("strokes", [])
        if not isinstance(raw_strokes, Sequence) or isinstance(raw_strokes, (str, bytes)):
            raise SketchRequestError("strokes는 배열이어야 합니다.")
        if len(raw_strokes) > MAX_STROKES:
            raise SketchRequestError(f"strokes는 최대 {MAX_STROKES}개입니다.")
        strokes: list[Stroke] = []
        total = 0
        for raw in raw_strokes:
            if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
                raise SketchRequestError("각 stroke는 점 배열이어야 합니다.")
            total += len(raw)
            if total > MAX_TOTAL_POINTS:
                raise SketchRequestError(f"점은 모두 합쳐 최대 {MAX_TOTAL_POINTS}개입니다.")
            points: list[tuple[float, float, float]] = []
            for point in raw:
                if (
                    not isinstance(point, Sequence)
                    or isinstance(point, (str, bytes))
                    or len(point) not in (2, 3)
                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in point)
                ):
                    raise SketchRequestError("점은 [nx, ny, t_ms] 숫자여야 합니다.")
                nx, ny = float(point[0]), float(point[1])
                if not (-1e-6 <= nx <= 1 + 1e-6 and -1e-6 <= ny <= 1 + 1e-6):
                    raise SketchRequestError("점은 미니맵 프레임 안(0..1)에 있어야 합니다.")
                t = float(point[2]) if len(point) == 3 else 0.0
                points.append((min(1.0, max(0.0, nx)), min(1.0, max(0.0, ny)), t))
            strokes.append(tuple(points))
        image_png: bytes | None = None
        encoded = body.get("image_png_b64")
        if encoded is not None:
            if type(encoded) is not str:
                raise SketchRequestError("image_png_b64는 문자열이거나 null이어야 합니다.")
            if len(encoded) > MAX_IMAGE_BYTES * 4 // 3 + 8:
                raise SketchRequestError("이미지가 너무 큽니다.")
            try:
                image_png = base64.b64decode(encoded, validate=True) if encoded else None
            except (binascii.Error, ValueError):
                raise SketchRequestError("image_png_b64가 올바른 base64가 아닙니다.") from None
            if image_png is not None and len(image_png) > MAX_IMAGE_BYTES:
                raise SketchRequestError("이미지가 너무 큽니다.")
        transcript = body.get("transcript", "")
        if transcript is None:
            transcript = ""
        if type(transcript) is not str:
            raise SketchRequestError("transcript는 문자열이어야 합니다.")
        rect = body.get("minimap_rect", {})
        return cls(
            update_id=update_id.strip(),
            strokes=tuple(strokes),
            image_png=image_png,
            transcript=" ".join(transcript.split())[:MAX_TRANSCRIPT_CHARS],
            minimap_rect=dict(rect) if isinstance(rect, Mapping) else {},
        )


@dataclass
class SketchWorld:
    """Everything the interpreter may know about the game; a snapshot the caller builds (no live access here)."""

    playable: tuple[float, float, float, float]
    """``(x0, y0, x1, y1)`` world units, SC2 y-up."""
    map_size: tuple[float, float] = (0.0, 0.0)
    pathable: Callable[[float, float], bool] = lambda x, y: True  # noqa: E731
    own_units: Sequence[tuple[str, int, float, float]] = ()
    enemies: Sequence[tuple[str, float, float]] = ()
    places: Sequence[tuple[str, float, float]] = ()
    calibration: tuple[float, float] = (0.0, 0.0)
    """``(dx, dy)`` in minimap-frame units: where the live game's drawn playable area is offset from the assumed one."""
    snap_radius: float = 8.0

    def __post_init__(self) -> None:
        x0, y0, x1, y1 = self.playable
        if not (x1 > x0 and y1 > y0):
            raise ValueError("playable area must have positive extent")


@runtime_checkable
class VlmReader(Protocol):
    def read(self, image_png: bytes | None, prompt: str, schema: dict) -> dict:  # pragma: no cover - protocol
        ...


_OVERRIDE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:그림|스케치|선|획|지시|지침|규칙|명령|프롬프트|제한|안전)\s*(?:은|는|을|를|도)?\s*(?:모두\s*|전부\s*)?무시|"
    r"(?:ignore|disregard|forget|override)\b.{0,40}\b(?:drawing|sketch|strokes?|instructions?|rules?|prompt|previous|above|everything)|"
    r"\bDAN\b|\byou are now\b|</?\s*(?:system|assistant|developer|user|transcript)\b[^>]*>|"
    r"^\s*(?:system|assistant|developer)\s*:|\[\s*(?:system|assistant)\s*\]|"
    r"\bdo\s*not\s*ask\b|\bdon'?t\s*ask\b|\bwithout\s*asking\b|"
    r"질문\s*(?:하지\s*말|없이|말고)|묻지\s*(?:말|마)|물어보지\s*(?:말|마)|안\s*물어봐도|"
    r"\breveal\b.{0,40}\binstructions?\b|\bconfirm\s+execute\b|\bkind\s*(?:는|=|:)\s*execute|그대로\s*따라",
    re.IGNORECASE,
)


def transcript_overrides_instructions(transcript: str) -> bool:
    """True for instruction-override phrasing (ignore the drawing/rules, role tags, 'do not ask', reveal prompt...)."""

    if not isinstance(transcript, str):
        return False
    return bool(_OVERRIDE_RE.search(transcript))


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def named_places(transcript: str, world: SketchWorld) -> list[tuple[str, float, float]]:
    """Semantic places (``world.places``) whose full Korean name the user spoke (whitespace-insensitive)."""

    spoken = _compact(transcript)
    return [pl for pl in world.places if pl[0] and _compact(pl[0]) in spoken]


@dataclass(frozen=True)
class RoutePoint:
    nx: float
    ny: float
    x: float
    y: float
    label_ko: str

    def to_dict(self) -> dict[str, object]:
        return {"nx": self.nx, "ny": self.ny, "x": self.x, "y": self.y, "label_ko": self.label_ko}


@dataclass(frozen=True)
class RouteFinal:
    action: str
    nx: float
    ny: float
    x: float
    y: float
    label_ko: str

    def to_dict(self) -> dict[str, object]:
        return {
            "action": self.action, "nx": self.nx, "ny": self.ny, "x": self.x, "y": self.y, "label_ko": self.label_ko,
        }


@dataclass(frozen=True)
class Route:
    waypoints: tuple[RoutePoint, ...]
    final: RouteFinal

    def to_dict(self) -> dict[str, object]:
        return {"waypoints": [p.to_dict() for p in self.waypoints], "final": self.final.to_dict()}


@dataclass(frozen=True)
class SquadRef:
    label_ko: str
    unit_count: int

    def to_dict(self) -> dict[str, object]:
        return {"label_ko": self.label_ko, "unit_count": self.unit_count}


@dataclass(frozen=True)
class RouteDecision:
    kind: str  # execute | ask | refuse | none
    plan_id: str
    narration_ko: str
    question_ko: str | None = None
    route: Route | None = None
    squad: SquadRef | None = None
    reason: str = ""
    unit_tags: tuple[int, ...] = ()
    """Concrete own-unit tags the squad resolved to (server-side only; never in :meth:`to_dict`)."""

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "plan_id": self.plan_id,
            "narration_ko": self.narration_ko,
            "question_ko": self.question_ko,
            "route": self.route.to_dict() if self.route else None,
            "squad": self.squad.to_dict() if self.squad else None,
        }


# --------------------------------------------------------------------------- geometry


def _frame_scale(world: SketchWorld) -> tuple[float, float, float]:
    x0, y0, x1, y1 = world.playable
    return max(x1 - x0, y1 - y0), (x0 + x1) / 2.0, (y0 + y1) / 2.0


def frame_to_world(nx: float, ny: float, world: SketchWorld) -> tuple[float, float] | None:
    """Minimap-frame point -> world point, or ``None`` when it falls in a letterbox band (not on the map).

    ASSUMPTION (only a live game can confirm): SC2 draws the playable area aspect-preserved and centred in the square
    minimap frame, so one frame unit spans ``max(width, height)`` world units.  ``world.calibration`` shifts the
    drawn area by ``(dx, dy)`` frame units once a live game shows the real offset.  Keep this the ONLY such function.
    """

    side, cx, cy = _frame_scale(world)
    dx, dy = world.calibration
    x = cx + (nx - dx - 0.5) * side
    y = cy - (ny - dy - 0.5) * side
    x0, y0, x1, y1 = world.playable
    if x < x0 - _EDGE_TOLERANCE or x > x1 + _EDGE_TOLERANCE or y < y0 - _EDGE_TOLERANCE or y > y1 + _EDGE_TOLERANCE:
        return None
    return (min(x1, max(x0, x)), min(y1, max(y0, y)))


def world_to_frame(x: float, y: float, world: SketchWorld) -> tuple[float, float]:
    """Inverse of :func:`frame_to_world` (no band check)."""

    side, cx, cy = _frame_scale(world)
    dx, dy = world.calibration
    return ((x - cx) / side + 0.5 + dx, 0.5 - (y - cy) / side + dy)


def _point_segment_distance(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    vx, vy = b[0] - a[0], b[1] - a[1]
    length2 = vx * vx + vy * vy
    if length2 == 0.0:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / length2))
    return math.dist(p, (a[0] + t * vx, a[1] + t * vy))


def simplify_polyline(points: Sequence[tuple[float, float]], epsilon: float) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker; keeps the first and last point exactly."""

    pts = list(points)
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        lo, hi = stack.pop()
        far_index, far = -1, 0.0
        for i in range(lo + 1, hi):
            d = _point_segment_distance(pts[i], pts[lo], pts[hi])
            if d > far:
                far_index, far = i, d
        if far > epsilon and far_index >= 0:
            keep[far_index] = True
            stack.append((lo, far_index))
            stack.append((far_index, hi))
    return [p for p, k in zip(pts, keep) if k]


def simplify_to_at_most(points: Sequence[tuple[float, float]], max_points: int = MAX_POINTS_PER_STROKE,
                        epsilon: float = RDP_EPSILON) -> list[tuple[float, float]]:
    """RDP with the smallest epsilon (>= ``epsilon``) that leaves at most ``max_points`` points."""

    out = simplify_polyline(points, epsilon)
    eps = epsilon
    while len(out) > max_points:
        eps *= 1.5
        out = simplify_polyline(points, eps)
    return out


def snap_to_pathable(
    point: tuple[float, float], pathable: Callable[[float, float], bool], radius: float, step: float = 0.5
) -> tuple[float, float] | None:
    """The point itself when pathable, else the nearest pathable point within ``radius`` (ring search), else ``None``."""

    if pathable(point[0], point[1]):
        return point
    d = step
    while d <= radius + 1e-9:
        count = max(8, math.ceil(2 * math.pi * d / step))
        for i in range(count):
            a = 2 * math.pi * i / count
            cand = (point[0] + d * math.cos(a), point[1] + d * math.sin(a))
            if pathable(cand[0], cand[1]):
                return cand
        d += step
    return None


def label_point(x: float, y: float, world: SketchWorld) -> str:
    """Korean label: nearest semantic place, else 맵 중앙, else a clock direction from the map centre."""

    side, cx, cy = _frame_scale(world)
    best: tuple[float, str] | None = None
    for name, px, py in world.places:
        d = math.hypot(x - px, y - py)
        if best is None or d < best[0]:
            best = (d, name)
    if best is not None and best[0] <= 0.12 * side:
        return f"{best[1]} 근처"
    if math.hypot(x - cx, y - cy) <= 0.1 * side:
        return "맵 중앙"
    bearing = math.degrees(math.atan2(x - cx, y - cy)) % 360.0  # 0 = up (+y), clockwise
    hour = round(bearing / 30.0) % 12 or 12
    return f"맵 중앙 기준 {hour}시 방향"


# --------------------------------------------------------------------------- stroke shape helpers


def _xy(stroke: Stroke) -> list[tuple[float, float]]:
    return [(p[0], p[1]) for p in stroke]


def _length(pts: Sequence[tuple[float, float]]) -> float:
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


def _is_closed(pts: Sequence[tuple[float, float]]) -> bool:
    total = _length(pts)
    return total > 0.08 and math.dist(pts[0], pts[-1]) < 0.25 * total


def _centroid(pts: Sequence[tuple[float, float]]) -> tuple[float, float]:
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def _inside(point: tuple[float, float], polygon: Sequence[tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def _circle_radius(pts: Sequence[tuple[float, float]]) -> float:
    c = _centroid(pts)
    return sum(math.dist(c, p) for p in pts) / len(pts)


def _segments_intersect(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float], d: tuple[float, float]) -> bool:
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    return (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0)


def _segment_segment_distance(a, b, c, d) -> float:
    if _segments_intersect(a, b, c, d):
        return 0.0
    return min(
        _point_segment_distance(a, c, d), _point_segment_distance(b, c, d),
        _point_segment_distance(c, a, b), _point_segment_distance(d, a, b),
    )


def _min_distance_to_polyline(p: tuple[float, float], pts: Sequence[tuple[float, float]]) -> float:
    if len(pts) == 1:
        return math.dist(p, pts[0])
    return min(_point_segment_distance(p, a, b) for a, b in zip(pts, pts[1:]))


def _path_crosses_avoid(path: Sequence[tuple[float, float]], avoid: Sequence[tuple[float, float]]) -> bool:
    closed = _is_closed(avoid)
    for a, b in zip(path, path[1:]):
        if closed and (_inside(a, avoid) or _inside(b, avoid) or _inside(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), avoid)):
            return True
        for c, d in zip(avoid, avoid[1:] + (avoid[:1] if closed else [])):
            if _segment_segment_distance(a, b, c, d) <= AVOID_CLEARANCE:
                return True
    return False


# --------------------------------------------------------------------------- squads


_EXTRA_ALIASES: Final[dict[str, str]] = {"일꾼": "SCV", "scv": "SCV", "에스씨브이": "SCV"}


def _unit_key(unit_type: object) -> str:
    token = str(unit_type or "").strip()
    family = canonical_terran_unit_family(token)
    if family:
        return family
    return token.upper().removeprefix("TERRAN_")


def _ko_name(unit_type: object) -> str:
    key = _unit_key(unit_type)
    for entry in TERRAN_UNIT_FAMILIES:
        if entry.family == key:
            for alias in entry.aliases:
                if re.search(r"[가-힣]", alias):
                    return alias
            return entry.family
    return {"SCV": "일꾼"}.get(key, key)


def parse_squad_from_transcript(transcript: str) -> tuple[str, int | None] | str | None:
    """``(unit_key, count|None)`` for one named unit type; ``"ambiguous"`` for several; ``None`` for none."""

    text = transcript.casefold()
    names = sorted({*TERRAN_UNIT_FAMILY_BY_ALIAS, *_EXTRA_ALIASES}, key=len, reverse=True)
    found: dict[str, tuple[str, int | None]] = {}
    consumed: list[tuple[int, int]] = []
    for name in names:
        pattern = rf"\b{re.escape(name)}\b" if name.isascii() else re.escape(name)
        for match in re.finditer(pattern, text):
            if any(s <= match.start() < e for s, e in consumed):
                continue
            consumed.append((match.start(), match.end()))
            key = _EXTRA_ALIASES.get(name) or canonical_terran_unit_family(name) or name.upper()
            after = re.match(rf"\s*(?:들|를|을|은|는|이|가)?\s*(\d+)\s*(?:{_COUNT_UNITS})", text[match.end():])
            before = re.search(rf"(\d+)\s*(?:{_COUNT_UNITS})\s*(?:의\s*)?$", text[: match.start()])
            count = int((after or before).group(1)) if (after or before) else None
            found[key] = (key, count)
    if not found:
        return None
    if len(found) > 1:
        return "ambiguous"
    return next(iter(found.values()))


def _plural_ko(n: int) -> str:
    return f"{n}기"


def _resolve_squad_from_transcript(
    transcript: str, world: SketchWorld
) -> tuple[list[tuple[str, int, float, float]], str] | tuple[None, str]:
    """``(units, label)`` or ``(None, question_ko)``."""

    parsed = parse_squad_from_transcript(transcript)
    if parsed is None:
        return None, "어느 부대를 보낼까요? 예: \"마린 4기\"처럼 유닛 종류와 수를 말해 주세요."
    if parsed == "ambiguous":
        return None, "한 번에 한 종류의 부대만 보낼 수 있어요. 어느 부대를 보낼까요?"
    key, count = parsed  # type: ignore[misc]
    pool = [u for u in world.own_units if _unit_key(u[0]) == key]
    name = _ko_name(key)
    if not pool:
        return None, f"지금 {name} 유닛이 없습니다. 다른 부대를 말해 주세요."
    if count is not None and count <= 0:
        return None, "몇 기를 보낼까요?"
    if count is not None and count > len(pool):
        return None, f"{name}이(가) {len(pool)}기뿐입니다. {len(pool)}기를 보낼까요, 다른 부대를 말해 주세요."
    return list(pool), f"{name} {_plural_ko(count if count is not None else len(pool))}"


def _own_clusters(units: Sequence[tuple[str, int, float, float]]) -> list[list[tuple[str, int, float, float]]]:
    """Single-linkage clusters of own units (link distance :data:`CLUSTER_LINK`)."""

    remaining = list(units)
    clusters: list[list[tuple[str, int, float, float]]] = []
    while remaining:
        cluster = [remaining.pop()]
        grew = True
        while grew:
            grew = False
            for unit in remaining[:]:
                if any(math.hypot(unit[2] - c[2], unit[3] - c[3]) <= CLUSTER_LINK for c in cluster):
                    cluster.append(unit)
                    remaining.remove(unit)
                    grew = True
        clusters.append(cluster)
    return clusters


def _squad_label(units: Sequence[tuple[str, int, float, float]]) -> str:
    counts: dict[str, int] = {}
    for unit in units:
        name = _ko_name(unit[0])
        counts[name] = counts.get(name, 0) + 1
    return "·".join(f"{name} {_plural_ko(n)}" for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _cluster_at_start(world: SketchWorld, start: tuple[float, float]) -> list[tuple[str, int, float, float]] | None:
    """The single own-unit cluster within :data:`CLUSTER_START_RADIUS` of ``start``; ``None`` if none or several."""

    near = [
        cluster for cluster in _own_clusters(list(world.own_units))
        if min(math.hypot(u[2] - start[0], u[3] - start[1]) for u in cluster) <= CLUSTER_START_RADIUS
    ]
    return near[0] if len(near) == 1 else None


def _pick_units(
    units: Sequence[tuple[str, int, float, float]], count: int | None, near: tuple[float, float]
) -> list[tuple[str, int, float, float]]:
    ordered = sorted(units, key=lambda u: (math.hypot(u[2] - near[0], u[3] - near[1]), u[1]))
    return ordered if count is None else ordered[:count]


# --------------------------------------------------------------------------- semantics


@dataclass
class _Semantics:
    path_strokes: list[int]
    target_stroke: int | None
    avoid_strokes: list[int]
    squad_stroke: int | None
    final_action: str
    squad_kind: str  # "transcript" | "circled_by_stroke"
    ask: str | None = None
    reversed_strokes: frozenset[int] = frozenset()


class _Unusable(Exception):
    def __init__(self, kind: str, reason: str, question: str = "") -> None:
        super().__init__(reason)
        self.kind, self.reason, self.question = kind, reason, question


ANSWER_SCHEMA: Final[dict] = {
    "type": "object",
    "properties": {
        "strokes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "role": {"type": "string", "enum": sorted(VALID_ROLES)},
                    "reversed": {"type": "boolean"},
                },
                "required": ["index", "role"],
            },
        },
        "path_order": {"type": "array", "items": {"type": "integer"}},
        "final_action": {"type": "string", "enum": sorted(VALID_ACTIONS)},
        "squad": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["transcript", "circled_by_stroke", "none"]},
                "stroke": {"type": "integer"},
            },
            "required": ["kind"],
        },
        "confidence": {"type": "number"},
        "ask_ko": {"type": "string"},
    },
    "required": ["strokes", "path_order", "final_action", "squad", "confidence"],
}


def build_prompt(req: SketchRequest, world: SketchWorld, shapes: Sequence[str]) -> str:
    lines = [
        "You read a hand-drawn sketch on a StarCraft II minimap (image attached when available).",
        "Decide ONLY the semantics of each numbered stroke. NEVER output coordinates: geometry is computed elsewhere.",
        "Roles: path (a route to follow), target (a circle/mark around the destination or enemy), "
        "avoid (a mark the route must not cross), squad_select (a circle around own units), noise (stray mark).",
        "path_order: indices of the path strokes in travel order (an arrow head shows direction; set reversed=true on a "
        "stroke drawn against its arrow). final_action: move or attack. squad.kind: transcript (named in the speech), "
        "circled_by_stroke (units inside the squad_select stroke), none.",
        "If anything is ambiguous or contradictory, set ask_ko to ONE short Korean question and keep confidence low.",
        f"사용자 발화: \"{req.transcript}\"",
        f"내 유닛: {len(world.own_units)}기, 보이는 적: {len(world.enemies)}기.",
        "Strokes (minimap frame 0..1, origin top-left):",
        *shapes,
        "Answer with one JSON object matching the schema.",
    ]
    return "\n".join(lines)


def _stroke_shapes(strokes: Sequence[Stroke]) -> list[str]:
    out = []
    for i, stroke in enumerate(strokes):
        pts = _xy(stroke)
        kind = "closed" if _is_closed(pts) else "open"
        out.append(
            f"#{i}: {kind}, length {_length(pts):.2f}, start ({pts[0][0]:.2f},{pts[0][1]:.2f}) "
            f"end ({pts[-1][0]:.2f},{pts[-1][1]:.2f})"
        )
    return out


def _read_semantics(answer: object, strokes: Sequence[Stroke], live: Sequence[int]) -> _Semantics:
    """Validate a VLM answer against the strokes; raises :class:`_Unusable` for anything not safe to use."""

    if not isinstance(answer, Mapping):
        raise _Unusable("ask", "vlm_not_object", "그림을 해석하지 못했어요. 다시 그려 주실래요?")
    # 1) the VLM may not invent coordinates: every point it supplies must lie on a stroke.
    for found in _iter_points(answer):
        if found is None or min(
            (_min_distance_to_polyline(found, _xy(s)) for s in strokes if s), default=1.0
        ) > INVENTED_POINT_TOLERANCE:
            raise _Unusable("refuse", "vlm_invented_point")
    ask = answer.get("ask_ko")
    if isinstance(ask, str) and ask.strip():
        raise _Unusable("ask", "vlm_ask", ask.strip())
    confidence = answer.get("confidence")
    if type(confidence) not in (int, float) or not 0.0 <= confidence <= 1.0 or confidence < MIN_CONFIDENCE:
        raise _Unusable("ask", "vlm_low_confidence", "그림을 확실히 읽지 못했어요. 어디로 보낼지 다시 그려 주실래요?")
    action = answer.get("final_action")
    if action not in VALID_ACTIONS:
        raise _Unusable("ask", "vlm_bad_action", "이동할까요, 공격할까요?")
    roles: dict[int, str] = {}
    reversed_set: set[int] = set()
    listed = answer.get("strokes")
    if not isinstance(listed, Sequence) or isinstance(listed, (str, bytes)):
        raise _Unusable("ask", "vlm_no_strokes", "그림을 해석하지 못했어요. 다시 그려 주실래요?")
    for item in listed:
        if (
            not isinstance(item, Mapping) or type(item.get("index")) is not int
            or not 0 <= item["index"] < len(strokes) or item.get("role") not in VALID_ROLES
            or item["index"] in roles
        ):
            raise _Unusable("ask", "vlm_bad_stroke_entry", "그림을 해석하지 못했어요. 다시 그려 주실래요?")
        roles[item["index"]] = item["role"]
        if item.get("reversed") is True:
            reversed_set.add(item["index"])
    order = answer.get("path_order")
    if not isinstance(order, Sequence) or isinstance(order, (str, bytes)) or any(
        type(i) is not int or not 0 <= i < len(strokes) or roles.get(i) != "path" for i in order
    ) or len(set(order)) != len(order):
        raise _Unusable("ask", "vlm_bad_path_order", "어느 선을 따라 갈지 분명하지 않아요. 다시 그려 주실래요?")
    if any(i not in roles for i in live):
        raise _Unusable("ask", "vlm_unlabelled_stroke", "그림의 일부를 해석하지 못했어요. 다시 그려 주실래요?")
    if {i for i, r in roles.items() if r == "path"} != set(order):
        raise _Unusable("ask", "vlm_path_order_mismatch", "경로 순서가 분명하지 않아요. 다시 그려 주실래요?")
    targets = [i for i, r in roles.items() if r == "target"]
    selects = [i for i, r in roles.items() if r == "squad_select"]
    if len(targets) > 1 or len(selects) > 1:
        raise _Unusable("ask", "vlm_multiple_targets", "목표가 여러 개로 보여요. 어디가 목적지인가요?")
    if not order:
        # A target/avoid/select mark alone is not a route: the geometry only ever comes from a drawn path.
        raise _Unusable("ask", "vlm_no_path", "어디로 보낼지 경로가 보이지 않아요. 가는 길을 선으로 그려 주실래요?")
    squad = answer.get("squad")
    kind = squad.get("kind") if isinstance(squad, Mapping) else None
    if kind == "circled_by_stroke":
        stroke = squad.get("stroke")
        if type(stroke) is not int or roles.get(stroke) != "squad_select":
            raise _Unusable("ask", "vlm_bad_squad_stroke", "어느 부대를 보낼지 분명하지 않아요.")
    elif kind not in ("transcript", "none"):
        raise _Unusable("ask", "vlm_bad_squad", "어느 부대를 보낼까요?")
    return _Semantics(
        path_strokes=list(order),
        target_stroke=targets[0] if targets else None,
        avoid_strokes=[i for i, r in roles.items() if r == "avoid"],
        squad_stroke=selects[0] if kind == "circled_by_stroke" else None,
        final_action=action,
        squad_kind="circled_by_stroke" if kind == "circled_by_stroke" else "transcript",
        reversed_strokes=frozenset(reversed_set),
    )


def _iter_points(node: object):
    """Every coordinate-like pair the VLM put anywhere in its answer (``nx``/``ny`` or ``x``/``y``)."""

    if isinstance(node, Mapping):
        keys = set(node)
        for a, b in (("nx", "ny"), ("x", "y")):
            if a in keys and b in keys:
                va, vb = node[a], node[b]
                if type(va) in (int, float) and type(vb) in (int, float) and a == "nx":
                    yield (float(va), float(vb))
                else:
                    yield None  # raw world x/y or non-numeric: never legitimate output
        for value in node.values():
            yield from _iter_points(value)
    elif isinstance(node, Sequence) and not isinstance(node, (str, bytes)):
        for value in node:
            yield from _iter_points(value)


def _fallback_semantics(strokes: Sequence[Stroke], live: Sequence[int], world: SketchWorld) -> _Semantics:
    """No VLM: a single open stroke is a move route; an open stroke ending at a circle around enemies is an attack."""

    opens = [i for i in live if not _is_closed(_xy(strokes[i]))]
    closed = [i for i in live if _is_closed(_xy(strokes[i]))]
    if len(opens) == 1 and not closed:
        return _Semantics([opens[0]], None, [], None, "move", "transcript")
    if len(opens) == 1 and len(closed) == 1:
        circle = _xy(strokes[closed[0]])
        radius = _circle_radius(circle)
        end = _xy(strokes[opens[0]])[-1]
        enemies_inside = [e for e in world.enemies if _inside(world_to_frame(e[1], e[2], world), circle)]
        if enemies_inside and math.dist(end, _centroid(circle)) <= max(1.5 * radius, 0.05):
            return _Semantics([opens[0]], closed[0], [], None, "attack", "transcript")
    raise _Unusable(
        "ask", "ambiguous_strokes",
        "그린 선이 여러 개라 어떤 것이 경로인지 분명하지 않아요. 경로를 한 줄로 그려 주실래요?",
    )


# --------------------------------------------------------------------------- interpreter


def _plan_id(req: SketchRequest, salt: str = "") -> str:
    digest = hashlib.sha1(
        (req.update_id + salt + repr(req.strokes)).encode("utf-8")
    ).hexdigest()
    return f"route-{digest[:10]}"


def _decision(req: SketchRequest, kind: str, narration: str, *, question: str | None = None, reason: str = "") -> RouteDecision:
    return RouteDecision(kind=kind, plan_id=_plan_id(req, kind), narration_ko=narration, question_ko=question, reason=reason)


def interpret_sketch(req: SketchRequest, world: SketchWorld, *, vlm: VlmReader | None = None) -> RouteDecision:
    """Strokes (+ transcript) -> :class:`RouteDecision`.  Pure: the only side channel is ``vlm.read``.

    Never raises: an unexpected internal error is an ``ask`` (nothing executes), not a 500 on the HUD.
    """

    try:
        return _interpret_sketch(req, world, vlm)
    except Exception:  # noqa: BLE001 - fail closed
        return _decision(
            req, "ask", "그림을 처리하다 문제가 생겼어요. 다시 그려 주실래요?",
            question="그림을 처리하다 문제가 생겼어요. 다시 그려 주실래요?", reason="internal_error",
        )


def _interpret_sketch(req: SketchRequest, world: SketchWorld, vlm: VlmReader | None) -> RouteDecision:

    if transcript_is_raw_control_or_injection(req.transcript) or transcript_overrides_instructions(req.transcript):
        return _decision(
            req, "refuse", "좌표나 지시 무시 같은 요청은 받지 않습니다. 미니맵에 경로를 그리고 부대만 말해 주세요.",
            reason="raw_control_or_injection",
        )
    live = [i for i, s in enumerate(req.strokes) if len(s) >= 2 and _length(_xy(s)) >= NOISE_LENGTH]
    if not live:
        return _decision(req, "none", "이해할 수 있는 경로 그림이 없어서 아무것도 하지 않았습니다.", reason="no_drawing")
    try:
        if vlm is None:
            sem = _fallback_semantics(req.strokes, live, world)
        else:
            try:
                answer = vlm.read(req.image_png, build_prompt(req, world, _stroke_shapes(
                    [s if len(s) >= 1 else ((0.0, 0.0, 0.0),) for s in req.strokes])), ANSWER_SCHEMA)
            except Exception:  # noqa: BLE001 - timeout/transport/garbage: never execute, ask once.
                raise _Unusable("ask", "vlm_unavailable", "그림을 읽지 못했어요. 다시 그려 주실래요?") from None
            sem = _read_semantics(answer, req.strokes, live)
        return _build_decision(req, world, sem)
    except _Unusable as unusable:
        if unusable.kind == "ask":
            return _decision(req, "ask", unusable.question, question=unusable.question, reason=unusable.reason)
        return _decision(
            req, unusable.kind, "그림에서 확인되지 않은 지점이 있어 실행하지 않았습니다.", reason=unusable.reason
        )


def _world_point(frame_pt: tuple[float, float], world: SketchWorld) -> tuple[float, float]:
    point = frame_to_world(frame_pt[0], frame_pt[1], world)
    if point is None:
        raise _Unusable("ask", "outside_map", "미니맵의 지도 영역 밖에 그린 부분이 있어요. 다시 그려 주실래요?")
    return point


def _build_decision(req: SketchRequest, world: SketchWorld, sem: _Semantics) -> RouteDecision:
    strokes = req.strokes
    # ---- path in frame space: per-stroke RDP (<= 8 points), strokes concatenated in the declared order
    frame_path: list[tuple[float, float]] = []
    per_stroke = max(2, MAX_POINTS_PER_STROKE // max(1, len(sem.path_strokes)))
    for index in sem.path_strokes:
        pts = _xy(strokes[index])
        if index in sem.reversed_strokes:
            pts = pts[::-1]
        for p in simplify_to_at_most(pts, per_stroke):
            if not frame_path or math.dist(frame_path[-1], p) > 1e-9:
                frame_path.append(p)
    # ---- chained path strokes must be contiguous (end of one ~ start of the next); two strokes that both start at the
    # squad are two routes, never a chain
    chain = [(_xy(strokes[i])[::-1] if i in sem.reversed_strokes else _xy(strokes[i])) for i in sem.path_strokes]
    for before, after in zip(chain, chain[1:]):
        a_end = frame_to_world(before[-1][0], before[-1][1], world)
        b_start = frame_to_world(after[0][0], after[0][1], world)
        if a_end is None or b_start is None or math.dist(a_end, b_start) > CHAIN_GAP:
            raise _Unusable("ask", "path_strokes_not_contiguous", TWO_PATHS_QUESTION)
    # ---- target circle -> final point (enemy centroid inside the circle, else the circle centre)
    final_frame: tuple[float, float] | None = None
    if sem.target_stroke is not None:
        circle = _xy(strokes[sem.target_stroke])
        radius = _circle_radius(circle)
        if frame_path and math.dist(frame_path[-1], _centroid(circle)) > max(1.5 * radius, 0.05) and not _inside(frame_path[-1], circle):
            raise _Unusable("ask", "path_not_ending_at_target", "경로가 표시한 목표에서 끝나지 않아요. 어디가 목적지인가요?")
        inside = [world_to_frame(e[1], e[2], world) for e in world.enemies]
        inside = [p for p in inside if _inside(p, circle)]
        final_frame = _centroid(inside) if inside and sem.final_action == "attack" else _centroid(circle)
    # ---- world points: map-band check, snap to pathable
    frame_points = list(frame_path)
    if final_frame is not None:
        frame_points.append(final_frame)
    if not frame_points:
        raise _Unusable("ask", "no_route", "어디로 보낼지 그림에서 찾지 못했어요.")
    world_points: list[tuple[float, float]] = []
    for fp in frame_points:
        raw = _world_point(fp, world)
        snapped = snap_to_pathable(raw, world.pathable, world.snap_radius)
        if snapped is None:
            raise _Unusable("refuse", "no_pathable_point")
        world_points.append(snapped)
    snapped_frame = [world_to_frame(x, y, world) for x, y in world_points]
    # ---- validator: every point traces to the strokes (or the target circle)
    problem = _trace_problem(frame_path, final_frame, strokes, sem, world_points, world)
    if problem:
        raise _Unusable("refuse", problem)
    # ---- a place the user NAMED must be where the route ends (else the speech and the drawing disagree: ask once)
    spoken = named_places(req.transcript, world)
    if spoken and not any(
        math.hypot(final_check[0] - pl[1], final_check[1] - pl[2]) <= PLACE_MATCH_RADIUS for pl in spoken
        for final_check in (world_points[-1],)
    ):
        names = ", ".join(pl[0] for pl in spoken)
        raise _Unusable(
            "ask", "named_place_not_at_route_end",
            f"말씀하신 {names}와(과) 그린 경로의 끝이 달라요. 어디로 보낼까요?",
        )
    # ---- squad
    near = world_points[0]
    start_frame = _xy(strokes[sem.path_strokes[0]])
    start_frame = (start_frame[-1] if sem.path_strokes[0] in sem.reversed_strokes else start_frame[0]) if sem.path_strokes else None
    start_world = frame_to_world(start_frame[0], start_frame[1], world) if start_frame is not None else None
    units, label = _resolve_squad(req, world, sem, strokes, near, start_world)
    squad_centroid = (sum(u[2] for u in units) / len(units), sum(u[3] for u in units) / len(units))
    # A stroke usually starts at the squad itself: leading points within the arrival radius of the squad are not
    # waypoints (nothing to pass through there).  The final point is never dropped.
    while len(world_points) > 1 and math.dist(world_points[0], squad_centroid) <= ARRIVAL_RADIUS:
        world_points.pop(0)
        snapped_frame.pop(0)
    # ---- the VLM may not silence a substantial stroke (noise / unassigned): deterministic guard
    if _ignored_stroke_is_substantial(strokes, sem, world, units, world_points[-1]):
        raise _Unusable("ask", "substantial_stroke_ignored", TWO_PATHS_QUESTION)
    # ---- avoid marks vs the planned path (including the leg from the squad to the first waypoint)
    walk = [world_to_frame(*squad_centroid, world), *snapped_frame]
    for index in sem.avoid_strokes:
        if _path_crosses_avoid(walk, _xy(strokes[index])):
            raise _Unusable("ask", "path_crosses_avoid", "경로가 피하라고 표시한 곳을 지나가요. 어떻게 갈까요? 피해서 다시 그려 주실래요?")
    # ---- assemble
    final_world, final_fr = world_points[-1], snapped_frame[-1]
    waypoints = tuple(
        RoutePoint(nx=f[0], ny=f[1], x=w[0], y=w[1], label_ko=label_point(w[0], w[1], world))
        for f, w in zip(snapped_frame[:-1], world_points[:-1])
    )
    final = RouteFinal(
        action=sem.final_action, nx=final_fr[0], ny=final_fr[1], x=final_world[0], y=final_world[1],
        label_ko=label_point(final_world[0], final_world[1], world),
    )
    route = Route(waypoints=waypoints, final=final)
    squad = SquadRef(label_ko=label, unit_count=len(units))
    verb = "공격" if final.action == "attack" else "이동"
    via = " → ".join(p.label_ko for p in waypoints)
    narration = f"{label}가 " + (f"{via}{_eul_reul(via)} 거쳐 " if via else "") + f"{final.label_ko}까지 {verb}합니다."
    return RouteDecision(
        kind="execute", plan_id=_plan_id(req, "execute"), narration_ko=narration, route=route, squad=squad,
        unit_tags=tuple(sorted(u[1] for u in units)),
    )


def _ignored_stroke_is_substantial(
    strokes: Sequence[Stroke],
    sem: _Semantics,
    world: SketchWorld,
    units: Sequence[tuple[str, int, float, float]],
    route_end: tuple[float, float],
) -> bool:
    """True when a stroke that is NOT part of the route (noise / unassigned) is too big to be a stray mark.

    * its world length is >= :data:`IGNORED_STROKE_FRACTION` of the longest path stroke, unless it lies inside / on an
      assigned target, avoid or squad_select stroke; or
    * it is an open stroke that starts at the squad like the path does and ends > :data:`SECOND_PATH_END_DISTANCE`
      world units from both its own start and the route end (a second destination).
    """

    side = _frame_scale(world)[0]
    used = set(sem.path_strokes)
    anchors = [i for i in (sem.target_stroke, sem.squad_stroke, *sem.avoid_strokes) if i is not None]
    used.update(anchors)
    path_len = max((_length(_xy(strokes[i])) * side for i in sem.path_strokes), default=0.0)

    def at_squad(frame_pt: tuple[float, float]) -> bool:
        wp = frame_to_world(frame_pt[0], frame_pt[1], world)
        return wp is not None and any(math.hypot(u[2] - wp[0], u[3] - wp[1]) <= CLUSTER_START_RADIUS for u in units)

    path_starts_at_squad = bool(sem.path_strokes) and at_squad(_xy(strokes[sem.path_strokes[0]])[0])
    for index, stroke in enumerate(strokes):
        if index in used or len(stroke) < 2:
            continue
        pts = _xy(stroke)
        length = _length(pts) * side
        if _length(pts) < NOISE_LENGTH:
            continue
        near_anchor = any(
            (_is_closed(_xy(strokes[a])) and _inside(_centroid(pts), _xy(strokes[a])))
            or sum(_min_distance_to_polyline(p, _xy(strokes[a])) <= 0.02 for p in pts) >= 0.8 * len(pts)
            for a in anchors
        )
        if near_anchor:
            continue
        if path_len > 0.0 and length >= IGNORED_STROKE_FRACTION * path_len:
            return True
        if not _is_closed(pts) and path_starts_at_squad and at_squad(pts[0]):
            end = frame_to_world(pts[-1][0], pts[-1][1], world)
            start = frame_to_world(pts[0][0], pts[0][1], world)
            if (
                end is not None and start is not None
                and math.dist(end, start) > SECOND_PATH_END_DISTANCE
                and math.dist(end, route_end) > SECOND_PATH_END_DISTANCE
            ):
                return True
    return False


def _eul_reul(word: str) -> str:
    last = word.strip()[-1:]
    if "가" <= last <= "힣":
        return "을" if (ord(last) - 0xAC00) % 28 else "를"
    return "를"


def _trace_problem(frame_path, final_frame, strokes, sem, world_points, world) -> str:
    """Re-check (defence in depth) that nothing in the route came from anywhere but the user's strokes."""

    used = [_xy(strokes[i]) for i in sem.path_strokes]
    for p in frame_path:
        if not any(_min_distance_to_polyline(p, pts) <= 1e-6 for pts in used):
            return "waypoint_not_on_stroke"
    if final_frame is not None:
        circle = _xy(strokes[sem.target_stroke])  # type: ignore[index]
        if not (_inside(final_frame, circle) or math.dist(final_frame, _centroid(circle)) <= _circle_radius(circle)):
            return "target_not_in_drawn_circle"
    for (x, y) in world_points:
        if not world.pathable(x, y):
            return "waypoint_not_pathable"
    return ""


def _resolve_squad(req, world, sem, strokes, near, start_world=None):
    if sem.squad_kind == "circled_by_stroke" and sem.squad_stroke is not None:
        polygon = _xy(strokes[sem.squad_stroke])
        picked = [u for u in world.own_units if _inside(world_to_frame(u[2], u[3], world), polygon)]
        if not picked:
            raise _Unusable("ask", "no_units_in_circle", "원 안에 내 유닛이 없어요. 보낼 유닛을 다시 둘러 주실래요?")
        names = {_ko_name(u[0]) for u in picked}
        label = f"{names.pop()} {len(picked)}기" if len(names) == 1 else f"선택한 부대 {len(picked)}기"
        return picked, label
    parsed = parse_squad_from_transcript(req.transcript)
    if parsed is None and start_world is not None:
        # Nothing named and nothing circled: users draw from the units they mean.  Exactly one own-unit cluster at the
        # path's first point is the squad; two or more (or none) keep asking.
        cluster = _cluster_at_start(world, start_world)
        if cluster is not None:
            return cluster, _squad_label(cluster)
    pool, label_or_question = _resolve_squad_from_transcript(req.transcript, world)
    if pool is None:
        raise _Unusable("ask", "squad_unresolved", label_or_question)
    count = parsed[1] if isinstance(parsed, tuple) else None
    chosen = _pick_units(pool, count, near)
    return chosen, label_or_question
