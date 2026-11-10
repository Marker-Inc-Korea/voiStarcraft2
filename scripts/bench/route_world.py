"""Synthetic SC2-like minimap for the R6 route-sketch benchmark: world description + PIL renderer.

Everything is deterministic from a seed (own RNG helpers; only random()/uniform()/gauss() of ``random.Random`` are used so the
sequence does not depend on the Python minor version).

World (the fields of the shared ``SketchWorld`` contract, as plain JSON-able data):
  playable   [x0, y0, x1, y1] world units, SC2 y-up
  map_size   [w, h]
  blobs      unpathable ellipses [cx, cy, rx, ry, angle_rad]   -> ``pathable(x, y)``
  own_units  [[type, tag, x, y], ...]
  enemies    [[type, x, y], ...]
  places     [[name_ko, x, y], ...]   (map_resolver-like semantic places)
  bases      [[kind, x, y], ...]      own / enemy / neutral expansion markers (renderer only; also feeds ``places``)

Frame <-> world: the benchmark's OWN statement of the assumption in the shared contract - SC2 fits the playable area into
the square minimap frame, aspect preserving, centered (letterboxed).  The backend has its own copy of this function; the
two are independent on purpose, so a disagreement shows up as waypoint error instead of cancelling out.
"""

from __future__ import annotations

import io
import math
import random
from dataclasses import dataclass, field


# --------------------------------------------------------------------------- rng helpers (version-stable)


def rint(rng: random.Random, lo: int, hi: int) -> int:
    """Uniform integer in [lo, hi] (inclusive)."""

    return lo + int(rng.random() * (hi - lo + 1))


def pick(rng: random.Random, seq):
    return seq[int(rng.random() * len(seq))]


def shuffled(rng: random.Random, seq) -> list:
    out = list(seq)
    for i in range(len(out) - 1, 0, -1):
        j = int(rng.random() * (i + 1))
        out[i], out[j] = out[j], out[i]
    return out


# --------------------------------------------------------------------------- geometry


@dataclass(frozen=True)
class Geometry:
    """Playable area (world, y-up) <-> square minimap frame (normalized 0..1, origin top-left), letterboxed."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    @property
    def scale(self) -> float:  # frame units per world unit
        return 1.0 / max(self.w, self.h)

    @property
    def off(self) -> tuple[float, float]:
        s = self.scale
        return (1.0 - self.w * s) / 2.0, (1.0 - self.h * s) / 2.0

    def to_frame(self, x: float, y: float) -> tuple[float, float]:
        ox, oy = self.off
        s = self.scale
        return ox + (x - self.x0) * s, oy + (self.y1 - y) * s

    def to_world(self, nx: float, ny: float) -> tuple[float, float]:
        ox, oy = self.off
        s = self.scale
        return self.x0 + (nx - ox) / s, self.y1 - (ny - oy) / s


def dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def seg_dist(p, a, b) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = dx * dx + dy * dy
    t = 0.0 if n == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / n))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def poly_dist(p, pts) -> float:
    if not pts:
        return math.inf
    if len(pts) == 1:
        return dist(p, pts[0])
    return min(seg_dist(p, pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def point_in_poly(p, pts) -> bool:
    inside = False
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        if (a[1] > p[1]) != (b[1] > p[1]) and p[0] < (b[0] - a[0]) * (p[1] - a[1]) / (b[1] - a[1]) + a[0]:
            inside = not inside
    return inside


# --------------------------------------------------------------------------- world


@dataclass
class World:
    playable: list
    map_size: list
    blobs: list = field(default_factory=list)
    own_units: list = field(default_factory=list)
    enemies: list = field(default_factory=list)
    places: list = field(default_factory=list)
    bases: list = field(default_factory=list)

    @property
    def geom(self) -> Geometry:
        return Geometry(*self.playable)

    def pathable(self, x: float, y: float) -> bool:
        x0, y0, x1, y1 = self.playable
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            return False
        for cx, cy, rx, ry, ang in self.blobs:
            dx, dy = x - cx, y - cy
            c, s = math.cos(ang), math.sin(ang)
            u, v = dx * c + dy * s, -dx * s + dy * c
            if (u / rx) ** 2 + (v / ry) ** 2 <= 1.0:
                return False
        return True

    def to_dict(self) -> dict:
        r = lambda seq: [[round(v, 2) if isinstance(v, float) else v for v in row] for row in seq]  # noqa: E731
        return {"playable": [round(v, 2) for v in self.playable], "map_size": list(self.map_size), "blobs": r(self.blobs),
                "own_units": r(self.own_units), "enemies": r(self.enemies), "places": r(self.places), "bases": r(self.bases)}

    @classmethod
    def from_dict(cls, d: dict) -> "World":
        return cls(playable=list(d["playable"]), map_size=list(d["map_size"]), blobs=[list(b) for b in d["blobs"]],
                   own_units=[list(u) for u in d["own_units"]], enemies=[list(e) for e in d["enemies"]],
                   places=[list(p) for p in d["places"]], bases=[list(b) for b in d["bases"]])


UNIT_KO = {"MARINE": "마린", "MARAUDER": "불곰", "SIEGETANK": "탱크", "MEDIVAC": "의료선", "HELLION": "화염차",
           "CYCLONE": "사이클론", "VIKING": "바이킹", "THOR": "토르"}
UNIT_ALIASES = {"MARINE": ("마린",), "MARAUDER": ("불곰", "마루더"), "SIEGETANK": ("탱크", "시즈"), "MEDIVAC": ("의료선", "메디박"),
                "HELLION": ("화염차", "헬리온"), "CYCLONE": ("사이클론",), "VIKING": ("바이킹",), "THOR": ("토르",)}
ENEMY_TYPES = ["ZERGLING", "ROACH", "HYDRALISK", "ZEALOT", "STALKER", "MARINE", "SIEGETANK"]

# four layouts: (own main corner, enemy main corner) as fractions of the playable box
_LAYOUTS = [((0.14, 0.16), (0.86, 0.84)), ((0.14, 0.84), (0.86, 0.16)), ((0.86, 0.16), (0.14, 0.84)), ((0.86, 0.84), (0.14, 0.16))]


def _frac_point(box, fx, fy):
    x0, y0, x1, y1 = box
    return x0 + (x1 - x0) * fx, y0 + (y1 - y0) * fy


def make_world(seed: int, *, n_clusters: int = 1, squad_sizes: tuple[int, ...] | None = None) -> tuple[World, dict]:
    """Deterministic world.  Returns (world, info) where info lists the generated clusters for the corpus builder:
    info = {"clusters": [{"center": [x, y], "tags": [...], "types": [...]}], "enemy_clusters": [{"center":..., "radius":...}]}."""

    rng = random.Random(f"world:{seed}")
    w = 128 + 8 * rint(rng, 0, 7)  # 128..184
    h = pick(rng, [w, w, w - 24, w - 40, w - 56])
    pad = 8
    box = [float(pad), float(pad), float(pad + w), float(pad + h)]
    map_size = [w + 2 * pad, h + 2 * pad]
    own_f, enemy_f = _LAYOUTS[seed % 4]
    own_main = _frac_point(box, *own_f)
    enemy_main = _frac_point(box, *enemy_f)
    own_nat = _frac_point(box, own_f[0] + (0.5 - own_f[0]) * 0.28, own_f[1] + (0.5 - own_f[1]) * 0.28)
    enemy_nat = _frac_point(box, enemy_f[0] + (0.5 - enemy_f[0]) * 0.28, enemy_f[1] + (0.5 - enemy_f[1]) * 0.28)
    center = _frac_point(box, 0.5, 0.5)
    third_a = _frac_point(box, own_f[0], 0.5 + (0.18 if own_f[1] < 0.5 else -0.18))
    third_b = _frac_point(box, 0.5 + (0.18 if own_f[0] < 0.5 else -0.18), own_f[1])
    places = [["내 본진", *own_main], ["내 앞마당", *own_nat], ["적 본진", *enemy_main], ["적 앞마당", *enemy_nat], ["중앙", *center],
              ["내 세 번째 기지", *third_a], ["내 오른쪽 확장" if third_b[0] > own_main[0] else "내 왼쪽 확장", *third_b]]
    mirror = lambda p: (box[0] + box[2] - p[0], box[1] + box[3] - p[1])  # noqa: E731  (point reflection through the center)
    bases = [["own", *own_main], ["own", *own_nat], ["enemy", *enemy_main], ["enemy", *enemy_nat], ["neutral", *third_a],
             ["neutral", *third_b], ["neutral", *mirror(third_a)], ["neutral", *mirror(third_b)]]
    keep_out = [own_main, own_nat, enemy_main, enemy_nat, center, third_a, third_b, mirror(third_a), mirror(third_b)]

    # terrain: unpathable ellipses (cliffs / ravines) away from the key places
    blobs: list[list[float]] = []
    tries = 0
    while len(blobs) < rint(rng, 5, 9) and tries < 200:
        tries += 1
        rx, ry = rng.uniform(4, 13), rng.uniform(3, 9)
        c = _frac_point(box, rng.uniform(0.08, 0.92), rng.uniform(0.08, 0.92))
        if any(dist(c, k) < max(rx, ry) + 11 for k in keep_out):
            continue
        blobs.append([round(c[0], 2), round(c[1], 2), round(rx, 2), round(ry, 2), round(rng.uniform(0, math.pi), 2)])
    world = World(playable=box, map_size=map_size, blobs=blobs, places=places, bases=bases)

    # own army clusters (marine-heavy mix) on the own side, enemy clusters in front of the enemy bases
    tag = 4_000_000_000 + seed * 1000
    clusters = []
    sizes = squad_sizes or tuple(rint(rng, 5, 10) for _ in range(n_clusters))
    spots = [_frac_point(box, own_f[0] + (0.5 - own_f[0]) * 0.55, own_f[1] + (0.5 - own_f[1]) * 0.55)]
    for k in range(1, len(sizes)):  # second / third squad: clearly separated from the first
        a = own_main
        spots.append((a[0] + (center[0] - a[0]) * 0.1 + (22 if k % 2 else -22) * (1 if own_main[0] < center[0] else -1),
                      a[1] + (center[1] - a[1]) * 0.95 - (14 if k % 2 else -14) * (1 if own_main[1] < center[1] else -1)))
    type_mixes = [["MARINE"] * 6 + ["MARAUDER"] * 2 + ["MEDIVAC"] * 2, ["SIEGETANK"] * 3 + ["MARINE"] * 3, ["HELLION"] * 4 + ["CYCLONE"] * 2,
                  ["MARINE"] * 8]
    for k, n in enumerate(sizes):
        spot = spots[k]
        for _ in range(200):  # nudge the squad onto open ground (the whole 5-unit disc must be open)
            if all(world.pathable(spot[0] + 5 * math.cos(a), spot[1] + 5 * math.sin(a)) for a in (0, 1.57, 3.14, 4.71)) and world.pathable(*spot):
                break
            spot = (spot[0] + rng.uniform(-9, 9), spot[1] + rng.uniform(-9, 9))
        spot = (min(max(spot[0], box[0] + 6), box[2] - 6), min(max(spot[1], box[1] + 6), box[3] - 6))
        mix = type_mixes[(seed + k) % len(type_mixes)]
        tags, types = [], []
        for i in range(n):
            others = [t for t in mix if t != "MARINE"] or ["MARAUDER"]
            n_marine = max(3, (n * 3 + 4) // 5)  # ~60% marines, the rest one or two other types (>= 2 of them)
            typ = ("MARINE" if i < n_marine else others[i % len(others)]) if k == 0 else mix[i % len(mix)]
            ang, rad = rng.uniform(0, 2 * math.pi), rng.uniform(0.5, 3.2)
            x, y = spot[0] + rad * math.cos(ang), spot[1] + rad * math.sin(ang)
            tag += 1
            world.own_units.append([typ, tag, round(x, 2), round(y, 2)])
            tags.append(tag)
            types.append(typ)
        clusters.append({"center": [round(spot[0], 2), round(spot[1], 2)], "tags": tags, "types": types, "radius": 3.5})
    enemy_clusters = []
    for k in range(rint(rng, 1, 2)):
        base = enemy_nat if k == 0 else enemy_main
        c = (base[0] + rng.uniform(-9, 9), base[1] + rng.uniform(-9, 9))
        for _ in range(60):
            if world.pathable(*c):
                break
            c = (base[0] + rng.uniform(-12, 12), base[1] + rng.uniform(-12, 12))
        n = rint(rng, 4, 9)
        for _ in range(n):
            ang, rad = rng.uniform(0, 2 * math.pi), rng.uniform(0.4, 4.0)
            world.enemies.append([pick(rng, ENEMY_TYPES[:5]), round(c[0] + rad * math.cos(ang), 2), round(c[1] + rad * math.sin(ang), 2)])
        enemy_clusters.append({"center": [round(c[0], 2), round(c[1], 2)], "radius": 4.5, "n": n})
    return world, {"clusters": clusters, "enemy_clusters": enemy_clusters}


def sample_open_point(world: World, rng: random.Random, *, avoid: list | None = None, min_gap: float = 14.0, margin: float = 7.0):
    x0, y0, x1, y1 = world.playable
    for _ in range(400):
        p = (rng.uniform(x0 + margin, x1 - margin), rng.uniform(y0 + margin, y1 - margin))
        if world.pathable(*p) and all(dist(p, a) >= min_gap for a in (avoid or [])):
            return p
    raise RuntimeError("no open point")


def segment_open(world: World, a, b, step: float = 1.5) -> bool:
    n = max(2, int(dist(a, b) / step))
    return all(world.pathable(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(n + 1))


# --------------------------------------------------------------------------- renderer

STROKE_COLOR = (255, 238, 64)  # the overlay's drawing colour (bright yellow, never used by the terrain/units)


def render_minimap(world: World, strokes: list | None = None, *, size: int = 512) -> bytes:
    """PNG of the minimap frame incl. the user's strokes (what the product would crop from the screen).  Needs Pillow."""

    from PIL import Image, ImageDraw

    g = world.geom
    S = size
    img = Image.new("RGB", (S, S), (0, 0, 0))
    d = ImageDraw.Draw(img)
    ox, oy = g.off
    px = lambda nx, ny: (nx * S, ny * S)  # noqa: E731
    fx0, fy0 = px(ox, oy)
    fx1, fy1 = px(ox + g.w * g.scale, oy + g.h * g.scale)
    d.rectangle([fx0, fy0, fx1, fy1], fill=(74, 82, 62))
    # subtle height bands so the terrain is not flat
    rng = random.Random(f"tex:{world.playable}:{len(world.blobs)}")
    for _ in range(26):
        cx, cy = rng.uniform(fx0, fx1), rng.uniform(fy0, fy1)
        r = rng.uniform(20, 60)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(82 + int(rng.random() * 10), 90 + int(rng.random() * 10), 68))
    for box in ([0, 0, S, fy0 - 1], [0, fy1 + 1, S, S], [0, 0, fx0 - 1, S], [fx1 + 1, 0, S, S]):  # re-black the letterbox
        if box[2] >= box[0] and box[3] >= box[1]:
            d.rectangle(box, fill=(0, 0, 0))
    d.rectangle([fx0, fy0, fx1, fy1], outline=(30, 30, 30))
    # cliffs / ravines: dark fill with a light rim
    for cx, cy, rx, ry, ang in world.blobs:
        pts = []
        for i in range(36):
            t = 2 * math.pi * i / 36
            u, v = rx * math.cos(t), ry * math.sin(t)
            x = cx + u * math.cos(ang) - v * math.sin(ang)
            y = cy + u * math.sin(ang) + v * math.cos(ang)
            pts.append(px(*g.to_frame(x, y)))
        d.polygon(pts, fill=(28, 30, 34), outline=(150, 150, 140))
    # bases: neutral expansions are light-blue crosses, own/enemy bases are squares
    for kind, x, y in world.bases:
        cx, cy = px(*g.to_frame(x, y))
        if kind == "neutral":
            d.line([cx - 5, cy, cx + 5, cy], fill=(120, 200, 255), width=2)
            d.line([cx, cy - 5, cx, cy + 5], fill=(120, 200, 255), width=2)
        else:
            col = (40, 200, 70) if kind == "own" else (225, 50, 50)
            d.rectangle([cx - 8, cy - 8, cx + 8, cy + 8], outline=col, width=3)
    for _typ, _tag, x, y in world.own_units:
        cx, cy = px(*g.to_frame(x, y))
        d.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=(60, 255, 90))
    for _typ, x, y in world.enemies:
        cx, cy = px(*g.to_frame(x, y))
        d.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=(255, 60, 60))
    for stroke in strokes or []:
        pts = [px(p[0], p[1]) for p in stroke]
        if len(pts) == 1:
            x, y = pts[0]
            d.ellipse([x - 3, y - 3, x + 3, y + 3], fill=STROKE_COLOR)
        else:
            d.line(pts, fill=STROKE_COLOR, width=4, joint="curve")
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=False, compress_level=6)
    return buf.getvalue()
