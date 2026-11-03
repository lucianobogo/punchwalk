#!/usr/bin/env python
"""Generate `fixtures/mock_api/` deterministically: mock API responses + synthetic media.

    uv run python scripts/gen_mock_api.py            # everything (videos take ~1 min)
    uv run python scripts/gen_mock_api.py --no-video # JSON + images only, keep existing videos

Every JSON file is built from the Pydantic schemas in `punchwalk/api/schemas.py`, so it is valid by
construction; `tests/test_contracts.py` re-validates it. The scenario follows FOOTAGE.md: project
"Unit 2B final punch", a first walk (glasses, 2 clips, 12 items), a re-walk (6 signed off, 3 still open,
1 false "fixed" claim), plus project "Unit 3A turnover" with a walk still processing.

The inspector points at each defect with an index finger and holds it ~2 s while describing it. Media is
drawn with Pillow (beige walls, simple defects, a skin-colored hand that comes in from the frame edge,
holds, and leaves; no text) and encoded with ffmpeg.
File names match what Phase 3 (`scripts/build_mock_api.py`) will write from real pipeline outputs, so
real frames can replace these in place. See fixtures/mock_api/README.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from punchwalk.api import schemas as S  # noqa: E402
from punchwalk.core.enums import (  # noqa: E402
    MODEL_ASR,
    MODEL_SAM,
    MODEL_SPARK_HOSTED,
    MODEL_SPARK_OPEN,
    NOT_CHECKED_ON_REWALK,
    REVIEW_FALSE_FIX,
    REVIEW_NOT_IN_FRAME,
    STAGE_LABEL,
    STAGE_TITLE,
    STAGES_INITIAL,
    STAGES_REWALK,
    TRADE_LABEL,
    Channel,
    ClaimedStatus,
    ClosureDecision,
    Device,
    Grounding,
    HistoryAction,
    HistoryActor,
    ItemStatus,
    JobKind,
    JobStatus,
    Orientation,
    Provider,
    ReasoningProvider,
    Severity,
    Stage,
    StageStatus,
    Trade,
    TurnKind,
    VisionStatus,
    VoiceStatus,
    WalkKind,
    WalkStatus,
    model_label,
)

OUT = ROOT / "fixtures" / "mock_api"
MEDIA = OUT / "media"
KEEP = {"README.md"}
SHARE_BASE = "https://punchwalk.example.com"  # absolute base used only inside message text

OVERLAY_FPS = 10.0
VIDEO_FPS = 15
VIDEO_SCALE = 2 / 3  # display.mp4 mock is 720x1280 for a 1080x1920 walk (same aspect)
MATCH_NOTE = "Matches the photo and what was said."

# --------------------------------------------------------------------------- colors

WALL = (218, 211, 200)
TRIM = (241, 239, 233)
TRIM_EDGE = (190, 181, 167)
COUNTER = (186, 178, 163)
TILE = (229, 233, 234)
GROUT = (205, 211, 213)
FLOOR = (186, 152, 116)
SKIN = (221, 170, 136)
SKIN_EDGE = (184, 134, 103)
BLUE = (47, 134, 224)  # DESIGN "Tape" #2F86E0: the "pointed here" dot and hand outlines
REDLINE = (217, 59, 48)  # DESIGN "Redline" #D93B30
WHITE = (255, 255, 255)
POINTER_R = 8  # "pointed here" dot radius at full frame (white halo around it)

Pt = tuple[float, float]
Poly = list[Pt]

# --------------------------------------------------------------------------- geometry


def rot_rect(cx: float, cy: float, length: float, thick: float, angle: float) -> Poly:
    a = math.radians(angle)
    ux, uy = math.cos(a), math.sin(a)
    vx, vy = -math.sin(a), math.cos(a)
    hl, ht = length / 2, thick / 2
    return [
        (cx - ux * hl - vx * ht, cy - uy * hl - vy * ht),
        (cx + ux * hl - vx * ht, cy + uy * hl - vy * ht),
        (cx + ux * hl + vx * ht, cy + uy * hl + vy * ht),
        (cx - ux * hl + vx * ht, cy - uy * hl + vy * ht),
    ]


def ellipse_poly(cx: float, cy: float, rx: float, ry: float, n: int = 20) -> Poly:
    return [
        (cx + rx * math.cos(2 * math.pi * i / n), cy + ry * math.sin(2 * math.pi * i / n)) for i in range(n)
    ]


def rect_poly(x1: float, y1: float, x2: float, y2: float) -> Poly:
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]


def band_poly(p1: Pt, p2: Pt, half: float) -> Poly:
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    n = math.hypot(dx, dy)
    nx, ny = -dy / n * half, dx / n * half
    return [
        (p1[0] + nx, p1[1] + ny),
        (p2[0] + nx, p2[1] + ny),
        (p2[0] - nx, p2[1] - ny),
        (p1[0] - nx, p1[1] - ny),
    ]


def shift(poly: Poly, ox: float, oy: float) -> Poly:
    return [(x + ox, y + oy) for x, y in poly]


def bbox(poly: Iterable[Pt]) -> tuple[float, float, float, float]:
    pts = list(poly)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def box_out(b: tuple[float, float, float, float], w: int, h: int) -> S.BoxOut:
    x1, y1, x2, y2 = b
    return S.BoxOut(
        x1=round(max(0.0, x1), 1), y1=round(max(0.0, y1), 1), x2=round(min(w, x2), 1), y2=round(min(h, y2), 1)
    )


def svg_path(poly: Poly) -> str:
    pts = [f"{round(x)} {round(y)}" for x, y in poly]
    return "M" + " L".join(pts) + " Z"


# --------------------------------------------------------------------------- drawing


class Canvas:
    """Draw in design coordinates (walk pixels) at a scale, with a camera offset."""

    def __init__(self, img: Image.Image, scale: float, off: Pt) -> None:
        self.d = ImageDraw.Draw(img)
        self.s = scale
        self.ox, self.oy = off
        self.size = img.size

    def p(self, x: float, y: float) -> Pt:
        return ((x + self.ox) * self.s, (y + self.oy) * self.s)

    def fill(self, color: tuple[int, int, int]) -> None:
        self.d.rectangle((0, 0, self.size[0], self.size[1]), fill=color)

    def rect(
        self, x1: float, y1: float, x2: float, y2: float, fill: Any, outline: Any = None, width: int = 0
    ) -> None:
        a, b = self.p(x1, y1), self.p(x2, y2)
        self.d.rectangle(
            (a, b), fill=fill, outline=outline, width=max(1, round(width * self.s)) if outline else 0
        )

    def poly(self, pts: Poly, fill: Any, outline: Any = None, width: int = 0) -> None:
        q = [self.p(x, y) for x, y in pts]
        self.d.polygon(q, fill=fill)
        if outline:
            self.d.line(q + [q[0]], fill=outline, width=max(1, round(width * self.s)), joint="curve")

    def circle(self, cx: float, cy: float, r: float, fill: Any, outline: Any = None, width: int = 0) -> None:
        x, y = self.p(cx, cy)
        rr = r * self.s
        self.d.ellipse(
            (x - rr, y - rr, x + rr, y + rr),
            fill=fill,
            outline=outline,
            width=max(1, round(width * self.s)) if outline else 0,
        )

    def line(self, pts: Poly, fill: Any, width: float) -> None:
        self.d.line(
            [self.p(x, y) for x, y in pts], fill=fill, width=max(1, round(width * self.s)), joint="curve"
        )


def _bg_wall(c: Canvas, W: float, H: float, base: float | None = None) -> None:
    c.fill(WALL)
    if base is not None:
        c.rect(-400, base, W + 400, base + 60, TRIM)


def _tiles(c: Canvas, W: float, H: float, step: int = 120) -> None:
    c.fill(TILE)
    for x in range(-480, int(W) + 480, step):
        c.line([(x, -480), (x, H + 480)], GROUT, 4)
    for y in range(-480, int(H) + 480, step):
        c.line([(-480, y), (W + 480, y)], GROUT, 4)


def _arc(cx: float, cy: float, w: float, h: float) -> Poly:
    return [(cx - w / 2 + w * i / 12, cy - h * math.sin(math.pi * i / 12)) for i in range(13)]


# Each kind: draw(c, W, H, state) -> None ; defect(state) -> Poly | None (scene coords) ; focus -> box | None
# state: "before" (as found), "still" (re-walk, not fixed), "fixed" (re-walk, fixed)


def draw_knob(c: Canvas, W: float, H: float, state: str) -> None:
    c.fill(WALL)
    c.rect(-400, 1240, W + 400, 1320, COUNTER)
    c.rect(-400, 1320, W + 400, H + 400, (228, 222, 212))
    c.rect(150, 300, 520, 1120, TRIM, TRIM_EDGE, 8)
    c.rect(560, 300, 930, 1120, TRIM, TRIM_EDGE, 8)
    c.circle(470, 960, 22, (142, 132, 117))
    if state == "fixed":
        c.circle(610, 960, 22, (142, 132, 117))
    else:
        c.circle(610, 960, 9, (95, 80, 66))


def draw_coffee(c: Canvas, W: float, H: float, state: str) -> None:
    c.fill(COUNTER)
    c.rect(-400, -400, W + 400, 960, WALL)
    c.rect(-400, 960, W + 400, 1000, (200, 192, 180))
    if state != "fixed":
        for x, y, r in ((500, 1400, 30), (565, 1445, 19), (515, 1490, 13), (590, 1380, 10), (470, 1455, 8)):
            c.circle(x, y, r, (94, 68, 48))


def draw_bulb(c: Canvas, W: float, H: float, state: str) -> None:
    c.fill(WALL)
    c.rect(-400, -400, W + 400, 1000, (234, 231, 225))
    c.rect(-400, 990, W + 400, 1010, (210, 203, 192))
    if state == "fixed":
        c.circle(540, 560, 175, (250, 242, 214))
    c.circle(540, 560, 150, (246, 244, 239), (170, 161, 146), 6)
    if state == "fixed":
        c.circle(540, 560, 95, (255, 226, 140))
    else:
        c.circle(540, 560, 95, (236, 233, 226), (200, 195, 185), 4)
        c.circle(540, 560, 42, (74, 74, 74))


def _towel_ends(state: str) -> tuple[Pt, Pt]:
    return ((250, 910), (830, 910)) if state == "fixed" else ((250, 900), (830, 948))


def draw_towel(c: Canvas, W: float, H: float, state: str) -> None:
    _tiles(c, W, H)
    p1, p2 = _towel_ends(state)
    for x, y in (p1, p2):
        c.rect(x - 22, y - 34, x + 22, y + 34, (170, 175, 180))
    c.line([p1, p2], (185, 190, 194), 28)


def _splatter() -> list[tuple[float, float, float]]:
    return [
        (0, 0, 22),
        (40, -30, 14),
        (-35, 25, 16),
        (70, 40, 10),
        (-60, -45, 12),
        (20, 60, 11),
        (95, -10, 9),
        (-90, 10, 8),
        (55, 85, 7),
        (-20, -75, 9),
    ]


def draw_mirror(c: Canvas, W: float, H: float, state: str) -> None:
    _tiles(c, W, H)
    c.rect(170, 260, 910, 1160, (200, 214, 221), (150, 160, 166), 10)
    c.poly([(260, 340), (420, 340), (260, 560)], (214, 226, 232))
    if state != "fixed":
        for dx, dy, r in _splatter():
            c.circle(610 + dx, 690 + dy, r, (247, 247, 242))


def draw_door(c: Canvas, W: float, H: float, state: str) -> None:
    c.fill(WALL)
    c.rect(290, 240, 880, H + 400, TRIM, TRIM_EDGE, 8)
    c.rect(320, 270, 850, H + 400, (236, 233, 226), (205, 198, 186), 4)
    c.circle(390, 1060, 26, (150, 140, 124))
    dy = 0 if state == "fixed" else 34
    c.rect(848, 1000 + dy, 876, 1110 + dy, (186, 180, 168), (160, 152, 138), 3)
    c.rect(856, 1035 + dy, 868, 1075 + dy, (120, 112, 100))


def draw_scuff(c: Canvas, W: float, H: float, state: str) -> None:
    c.fill(WALL)
    c.rect(-400, 1700, 760, 1760, TRIM)
    c.rect(760, 200, W + 400, H + 400, TRIM, TRIM_EDGE, 8)
    if state != "fixed":
        for i, (w, h) in enumerate(((230, 40), (190, 32), (150, 26))):
            c.line(_arc(380 + 8 * i, 985 + 32 * i, w, h), (140, 132, 120), 7)


def draw_switch(c: Canvas, W: float, H: float, state: str) -> None:
    c.fill(WALL)
    c.rect(-400, 1700, W + 400, 1760, TRIM)
    angle = 0 if state == "fixed" else 12
    c.poly(rot_rect(540, 920, 240, 150, angle + 90), (245, 243, 237), (184, 176, 162), 4)
    c.poly(rot_rect(540, 920, 60, 26, angle + 90), (225, 220, 210), (184, 176, 162), 2)


def draw_window(c: Canvas, W: float, H: float, state: str) -> None:
    c.fill(WALL)
    c.rect(210, 280, 870, 1320, TRIM, TRIM_EDGE, 10)
    c.rect(245, 315, 835, 1285, (190, 211, 224))
    c.poly([(245, 315), (500, 315), (245, 700)], (205, 222, 233))
    c.rect(528, 315, 552, 1285, TRIM)
    c.rect(180, 1320, 900, 1360, TRIM, TRIM_EDGE, 4)
    if state == "fixed":
        for x in range(245, 835, 28):
            c.line([(x, 315), (x, 1285)], (150, 160, 165), 2)
        for y in range(315, 1285, 28):
            c.line([(245, y), (835, y)], (150, 160, 165), 2)


def _sawdust() -> list[tuple[float, float, float]]:
    rng = random.Random(7)
    pts = []
    for _ in range(70):
        a, r = rng.uniform(0, 2 * math.pi), rng.uniform(0, 1) ** 0.6
        pts.append((520 + 140 * r * math.cos(a), 1360 + 90 * r * math.sin(a), rng.uniform(4, 9)))
    return pts


def draw_debris(c: Canvas, W: float, H: float, state: str) -> None:
    c.fill(FLOOR)
    c.rect(-400, -400, W + 400, 700, WALL)
    c.rect(-400, 700, W + 400, 770, TRIM)
    for y in range(820, int(H) + 400, 110):
        c.line([(-400, y), (W + 400, y)], (170, 138, 104), 3)
    if state != "fixed":
        for x, y, r in _sawdust():
            c.circle(x, y, r, (233, 214, 174))


def draw_outlet(c: Canvas, W: float, H: float, state: str) -> None:
    c.fill(WALL)
    c.rect(-400, 1700, W + 400, 1760, TRIM)
    c.rect(450, 860, 630, 1140, (245, 243, 237), (184, 176, 162), 4)
    for cy in (935, 1065):
        c.rect(500, cy - 28, 580, cy + 28, (232, 229, 221), (200, 194, 182), 2)
        c.rect(515, cy - 14, 523, cy + 8, (80, 76, 70))
        c.rect(557, cy - 14, 565, cy + 8, (80, 76, 70))
    if state != "fixed":
        c.line([(462, 872), (505, 930), (496, 985), (540, 1040), (560, 1090)], (90, 86, 80), 4)


def draw_closet(c: Canvas, W: float, H: float, state: str) -> None:
    """A closet door with nothing wrong on it (item #12: the photo doesn't show the dent described)."""
    c.fill(WALL)
    c.rect(-400, 1700, W + 400, 1760, TRIM)
    c.rect(230, 160, 850, 1720, TRIM, TRIM_EDGE, 8)
    c.rect(262, 192, 818, 1720, (238, 235, 228), (205, 198, 186), 4)
    c.rect(320, 260, 760, 860, (233, 229, 221), (210, 203, 192), 3)
    c.rect(320, 960, 760, 1620, (233, 229, 221), (210, 203, 192), 3)
    c.circle(770, 920, 20, (150, 140, 124))


# Landscape kinds (walk3, 1920x1080).


def draw_grout(c: Canvas, W: float, H: float, state: str) -> None:
    _tiles(c, W, H, 110)
    c.rect(-400, 700, W + 400, H + 400, (240, 240, 236))
    c.rect(-400, 692, W + 400, 712, (215, 215, 210))
    if state != "fixed":
        c.rect(1186, 380, 1204, 694, (150, 140, 126))


def draw_baseboard(c: Canvas, W: float, H: float, state: str) -> None:
    c.fill(FLOOR)
    c.rect(-400, -400, W + 400, 860, WALL)
    c.rect(1380, -400, 1460, 950, TRIM, TRIM_EDGE, 6)
    c.rect(-400, 860, 1380, 940, TRIM, TRIM_EDGE, 3)
    if state != "fixed":
        c.rect(700, 940, 1300, 954, (70, 62, 54))


def draw_drip(c: Canvas, W: float, H: float, state: str) -> None:
    c.fill(WALL)
    c.rect(820, -400, 920, H + 400, TRIM, TRIM_EDGE, 6)
    c.rect(920, -400, W + 400, H + 400, (236, 233, 226))
    if state != "fixed":
        c.poly(
            [(862, 470), (878, 470), (884, 590), (870, 618), (856, 590)], (250, 250, 246), (205, 199, 188), 3
        )


def draw_cabinet(c: Canvas, W: float, H: float, state: str) -> None:
    c.fill((228, 222, 212))
    c.rect(-400, 200, W + 400, 260, COUNTER)
    c.rect(500, 280, 940, 1000, TRIM, TRIM_EDGE, 8)
    c.rect(960, 280, 1400, 1000, TRIM, TRIM_EDGE, 8)
    c.circle(900, 360, 16, (142, 132, 117))
    c.circle(1000, 360, 16, (142, 132, 117))
    if state != "fixed":
        for i in range(5):
            c.line([(930, 300 + 22 * i), (962, 312 + 22 * i)], (160, 150, 134), 4)


KindFn = Callable[[Canvas, float, float, str], None]


@dataclass(frozen=True)
class Kind:
    draw: KindFn
    defect: Poly | None  # scene coords, in the "before" state
    focus: tuple[float, float, float, float] | None = None  # crop focus when nothing is outlined


KINDS: dict[str, Kind] = {
    "knob": Kind(draw_knob, ellipse_poly(610, 960, 46, 46)),
    "coffee": Kind(draw_coffee, ellipse_poly(535, 1435, 110, 95)),
    "bulb": Kind(draw_bulb, ellipse_poly(540, 560, 172, 172, 24)),
    "towel": Kind(draw_towel, band_poly((228, 899), (852, 951), 50)),
    "mirror": Kind(draw_mirror, ellipse_poly(612, 694, 165, 135)),
    "door": Kind(draw_door, rect_poly(832, 1016, 892, 1162)),
    "scuff": Kind(draw_scuff, ellipse_poly(395, 1010, 150, 78)),
    "switch": Kind(draw_switch, rot_rect(540, 920, 280, 190, 102)),
    "window": Kind(draw_window, rect_poly(245, 315, 835, 1285)),
    "debris": Kind(draw_debris, ellipse_poly(520, 1360, 165, 110)),
    "outlet": Kind(draw_outlet, rect_poly(440, 850, 640, 1150), (380, 780, 700, 1220)),
    "closet": Kind(draw_closet, None, (330, 1180, 750, 1600)),
    "grout": Kind(draw_grout, rect_poly(1160, 370, 1230, 704)),
    "baseboard": Kind(draw_baseboard, rect_poly(680, 925, 1320, 970)),
    "drip": Kind(draw_drip, ellipse_poly(870, 545, 42, 92)),
    "cabinet": Kind(draw_cabinet, rect_poly(905, 285, 995, 420)),
}

# --------------------------------------------------------------------------- the pointing hand

# A simple right-hand silhouette in local coordinates: the index fingertip at (0, 0), the arm running
# along +x back to (and past) the frame edge, the thumb on the -y side. Walk pixels.
HAND_LOCAL: Poly = (
    [(23 + 23 * math.cos(math.radians(a)), 23 * math.sin(math.radians(a))) for a in range(90, 271, 15)]
    + [(150, -24), (176, -50), (202, -80), (218, -104), (250, -130), (286, -132), (302, -114), (294, -92)]
    + [(322, -88), (382, -84), (432, -72), (2400, -84), (2400, 84), (432, 74), (372, 100), (302, 106)]
    + [(236, 98), (186, 72), (160, 42), (150, 24)]
)
ENTER_S = 0.7  # the hand moves in from the frame edge
LEAVE_S = 0.35  # ... and back out after the hold
EVIDENCE_AFTER_S = 0.45  # evidence frame = this long after the hold ends (the hand is gone by then)
HOLD_LEAD_S = 0.4  # the hold starts this long before the description starts
MIN_HAND_AREA = 1500.0  # px^2 of the hand inside the frame for SAM to "see" it


def unit(dx: float, dy: float) -> Pt:
    n = math.hypot(dx, dy)
    return dx / n, dy / n


def poly_area(poly: Poly) -> float:
    pairs = zip(poly, poly[1:] + poly[:1], strict=True)
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in pairs)) / 2


def clip_to_frame(poly: Poly, w: float, h: float) -> Poly:
    """Sutherland-Hodgman clip of `poly` to the frame [0, w] x [0, h]."""

    def clip(pts: Poly, inside: Callable[[Pt], bool], cross: Callable[[Pt, Pt], Pt]) -> Poly:
        out: Poly = []
        for i, cur in enumerate(pts):
            prev = pts[i - 1]
            if inside(cur):
                if not inside(prev):
                    out.append(cross(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(cross(prev, cur))
        return out

    def at_x(x: float) -> Callable[[Pt, Pt], Pt]:
        return lambda a, b: (x, a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0]))

    def at_y(y: float) -> Callable[[Pt, Pt], Pt]:
        return lambda a, b: (a[0] + (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]), y)

    pts = list(poly)
    for inside, cross in (
        (lambda p: p[0] >= 0, at_x(0)),
        (lambda p: p[0] <= w, at_x(w)),
        (lambda p: p[1] >= 0, at_y(0)),
        (lambda p: p[1] <= h, at_y(h)),
    ):
        if not pts:
            break
        pts = clip(pts, inside, cross)
    return pts


@dataclass
class Pointing:
    """One pointing event in scene coordinates: the finger comes in, holds still at `tip`, leaves."""

    tip: Pt  # fingertip, scene coords (next to the defect, not covering it)
    arm: Pt  # unit vector from the fingertip back toward where the arm enters the frame
    hold_start: float
    hold_s: float
    travel: float  # how far the hand slides in along `arm` (fully out of frame at the start)
    point_id: str
    sam_ids: list[str]

    @property
    def hold_end(self) -> float:
        return self.hold_start + self.hold_s

    @property
    def t(self) -> float:
        """The pointing event's time: the middle of the hold."""
        return round(self.hold_start + self.hold_s / 2, 1)

    @property
    def evidence_t(self) -> float:
        return round(self.hold_end + EVIDENCE_AFTER_S, 2)

    def holding(self, t: float) -> bool:
        return self.hold_start <= t <= self.hold_end

    def slide(self, t: float) -> float | None:
        """Distance the fingertip is from `tip` along `arm` at time t; None when the hand is out of view."""
        if t < self.hold_start - ENTER_S or t > self.hold_end + LEAVE_S:
            return None
        if t < self.hold_start:
            p = (t - (self.hold_start - ENTER_S)) / ENTER_S
            return self.travel * (1 - p) ** 2
        if t <= self.hold_end:
            return 0.0
        p = (t - self.hold_end) / LEAVE_S
        return self.travel * p * p

    def tip_at(self, t: float) -> Pt | None:
        d = self.slide(t)
        if d is None:
            return None
        jx = 2.0 * math.sin(2 * math.pi * 1.3 * t) if d == 0 else 0.0  # a little tremor while holding
        jy = 1.5 * math.cos(2 * math.pi * 1.1 * t) if d == 0 else 0.0
        return (self.tip[0] + self.arm[0] * d + jx, self.tip[1] + self.arm[1] * d + jy)

    def hand_scene(self, t: float) -> Poly | None:
        tip = self.tip_at(t)
        if tip is None:
            return None
        ux, uy = self.arm
        vx, vy = -uy, ux  # the thumb (local -y) on the left: a right hand seen from behind
        return [(tip[0] + ux * x - vx * y, tip[1] + uy * x - vy * y) for x, y in HAND_LOCAL]


def make_pointing(
    kind: Kind,
    W: int,
    H: int,
    hold_start: float,
    hold_s: float,
    point_id: str,
    sam_ids: list[str],
    tip: Pt | None = None,
) -> Pointing:
    """The hand enters from the bottom-right and the fingertip stops just outside the defect's edge."""
    entry = (W * 0.8, H + 240)
    if tip is None:
        assert kind.defect, "a pointed scene needs a defect polygon or an explicit tip"
        cx = sum(x for x, _ in kind.defect) / len(kind.defect)
        cy = sum(y for _, y in kind.defect) / len(kind.defect)
        v = unit(entry[0] - cx, entry[1] - cy)
        reach = max((x - cx) * v[0] + (y - cy) * v[1] for x, y in kind.defect)
        tip = (cx + v[0] * (reach + 26), cy + v[1] * (reach + 26))
    assert 40 <= tip[0] <= W - 40 and 40 <= tip[1] <= H - 40, f"{point_id}: fingertip {tip} is off-frame"
    arm = unit(entry[0] - tip[0], entry[1] - tip[1])
    exits = [
        (W - tip[0]) / arm[0] if arm[0] > 0 else (-tip[0] / arm[0] if arm[0] < 0 else math.inf),
        (H - tip[1]) / arm[1] if arm[1] > 0 else (-tip[1] / arm[1] if arm[1] < 0 else math.inf),
    ]
    return Pointing(tip, arm, hold_start, hold_s, min(exits) + 90, point_id, sam_ids)


# --------------------------------------------------------------------------- scenario tables


@dataclass
class Scene:
    """One stop of the walk: the camera looks at one spot for [start, end]."""

    kind: str
    start: float
    end: float
    state: str = "before"
    pointing: Pointing | None = None  # None: described without pointing (voice-only)
    pan: Pt = (0.0, 0.0)
    item_id: str | None = None
    number: int | None = None

    def offset(self, t: float) -> Pt:
        tm = (self.start + self.end) / 2
        ox = self.pan[0] * (t - tm) + 6 * math.sin(2 * math.pi * 0.4 * (t - self.start))
        oy = self.pan[1] * (t - tm) + 4 * math.sin(2 * math.pi * 0.3 * (t - self.start))
        return ox, oy

    def hand_display(self, t: float, w: int, h: int) -> Poly | None:
        """The hand outline in display pixels (clipped to the frame), or None when it isn't in view."""
        if not self.pointing or not (self.start <= t < self.end):
            return None
        hand = self.pointing.hand_scene(t)
        if hand is None:
            return None
        ox, oy = self.offset(t)
        clipped = clip_to_frame(shift(hand, ox, oy), w, h)
        return clipped if len(clipped) >= 3 and poly_area(clipped) >= MIN_HAND_AREA else None

    def to_display(self, p: Pt, t: float) -> Pt:
        ox, oy = self.offset(t)
        return (p[0] + ox, p[1] + oy)


PANS: list[Pt] = [(-14, 6), (12, -5), (-10, -7), (9, 8)]


@dataclass
class TurnRow:
    start: float
    end: float
    text: str
    kind: TurnKind
    room: str | None
    claimed: ClaimedStatus | None = None
    item_key: str | None = None


@dataclass
class ItemRow:
    key: str  # e.g. "2b_01"
    number: int
    kind: str
    room: str
    title: str
    description: str
    action: str
    trade: Trade
    location_detail: str
    severity: Severity
    grounding: Grounding
    sam_concept: str | None
    scene: tuple[float, float]
    hold_s: float | None  # how long the finger is held still; None: described without pointing
    turn_idx: int  # index into the walk's turns (the description)
    evidence_dt: float = 0.6  # voice-only items: evidence frame = quote start + dt
    confidence: float | None = 0.9
    sam_ids: list[str] = field(default_factory=list)  # SAM hand-track ids ("<clip>:<id>")
    tip: Pt | None = None  # explicit fingertip (scene coords) when the kind has no defect polygon


@dataclass
class WalkSpec:
    id: str
    project_id: str
    kind: WalkKind
    W: int
    H: int
    duration_s: float
    device: Device
    recorded_at: datetime
    created_at: datetime
    clips: list[tuple[str, float]]  # filename, duration
    turns: list[TurnRow]
    scenes: list[Scene] = field(default_factory=list)
    rewalk_of: str | None = None
    job_id: str = ""
    transit: tuple[int, int, int] = WALL


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


PRJ_2B = "prj_unit2b"
PRJ_3A = "prj_unit3a"
W1 = "wlk_2b_first"
W2 = "wlk_2b_rewalk"
W3 = "wlk_3a_first"

TURNS_W1 = [
    TurnRow(1.2, 2.6, "Okay, starting the final punch for unit 2B.", TurnKind.other, None),
    TurnRow(4.0, 4.8, "Kitchen.", TurnKind.location, "Kitchen"),
    TurnRow(
        14.0,
        17.2,
        "Upper cabinet left of the sink, uh, knob's missing.",
        TurnKind.defect,
        "Kitchen",
        item_key="2b_01",
    ),
    TurnRow(20.0, 22.0, "Moving down to the counter.", TurnKind.other, "Kitchen"),
    TurnRow(
        27.0,
        30.4,
        "Coffee drips on the counter next to the stove, needs a wipe.",
        TurnKind.defect,
        "Kitchen",
        item_key="2b_02",
    ),
    TurnRow(
        39.5,
        42.5,
        "Light over the sink is out, there's no bulb in it.",
        TurnKind.defect,
        "Kitchen",
        item_key="2b_03",
    ),
    TurnRow(50.0, 50.9, "Okay, bathroom.", TurnKind.location, "Bathroom"),
    TurnRow(
        57.0,
        60.6,
        "Towel bar by the shower is loose, it wiggles.",
        TurnKind.defect,
        "Bathroom",
        item_key="2b_04",
    ),
    TurnRow(
        69.5,
        72.8,
        "Mirror's got toothpaste splatter all over it.",
        TurnKind.defect,
        "Bathroom",
        item_key="2b_05",
    ),
    TurnRow(78.0, 80.0, "That's it for the bathroom.", TurnKind.other, "Bathroom"),
    TurnRow(85.0, 85.8, "Bedroom.", TurnKind.location, "Bedroom"),
    TurnRow(
        92.0,
        95.4,
        "Bedroom door won't latch when you close it.",
        TurnKind.defect,
        "Bedroom",
        item_key="2b_06",
    ),
    TurnRow(
        104.0,
        107.0,
        "Scuff marks on the wall left of the closet door.",
        TurnKind.defect,
        "Bedroom",
        item_key="2b_07",
    ),
    TurnRow(
        112.0,
        116.0,
        "Outlet cover left of the bed is cracked, somebody could get a shock.",
        TurnKind.defect,
        "Bedroom",
        item_key="2b_11",
    ),
    TurnRow(
        119.0,
        122.0,
        "Closet door's got a dent down at the bottom.",
        TurnKind.defect,
        "Bedroom",
        item_key="2b_12",
    ),
    TurnRow(124.0, 124.8, "Hallway.", TurnKind.location, "Hallway"),
    TurnRow(
        127.5,
        130.5,
        "Switch plate by the hall closet is crooked.",
        TurnKind.defect,
        "Hallway",
        item_key="2b_08",
    ),
    TurnRow(135.0, 136.0, "Living room.", TurnKind.location, "Living room"),
    TurnRow(
        139.0,
        142.2,
        "Window screen's missing on the left window.",
        TurnKind.defect,
        "Living room",
        item_key="2b_09",
    ),
    TurnRow(
        147.5,
        151.0,
        "And there's sawdust on the floor by the window, needs a sweep.",
        TurnKind.defect,
        "Living room",
        item_key="2b_10",
    ),
    TurnRow(151.6, 153.0, "That's the walk.", TurnKind.other, "Living room"),
]

ITEMS_2B = [
    ItemRow(
        "2b_01",
        1,
        "knob",
        "Kitchen",
        "Missing cabinet knob",
        "The upper cabinet left of the sink is missing its knob, leaving the mounting hole exposed.",
        "Install a matching knob",
        Trade.carpentry_millwork,
        "Upper cabinet left of the sink",
        Severity.cosmetic,
        Grounding.sam_concept,
        "cabinet knob hole",
        (9.0, 18.7),
        1.8,
        2,
        confidence=0.93,
        sam_ids=["a:1"],
    ),
    ItemRow(
        "2b_02",
        2,
        "coffee",
        "Kitchen",
        "Coffee drips on counter",
        "Dried coffee drips on the counter next to the stove.",
        "Clean the counter",
        Trade.cleaning,
        "Counter next to the stove",
        Severity.cosmetic,
        Grounding.sam_concept,
        "coffee stain",
        (22.0, 31.9),
        1.6,
        4,
        confidence=0.91,
        sam_ids=["a:2"],
    ),
    ItemRow(
        "2b_03",
        3,
        "bulb",
        "Kitchen",
        "Bulb out above the sink",
        "The light fixture over the kitchen sink has no bulb, so it doesn't light.",
        "Replace the bulb and test the fixture",
        Trade.electrical,
        "Fixture over the sink",
        Severity.functional,
        Grounding.sam_box,
        "ceiling light fixture",
        (34.5, 44.0),
        2.0,
        5,
        confidence=0.88,
        sam_ids=["a:3"],
    ),
    ItemRow(
        "2b_04",
        4,
        "towel",
        "Bathroom",
        "Loose towel bar",
        "The towel bar by the shower is loose and moves when touched.",
        "Tighten the towel bar mounts",
        Trade.general,
        "Towel bar by the shower",
        Severity.functional,
        Grounding.sam_concept,
        "towel bar",
        (52.0, 62.1),
        1.7,
        7,
        confidence=0.86,
        sam_ids=["a:4"],
    ),
    ItemRow(
        "2b_05",
        5,
        "mirror",
        "Bathroom",
        "Toothpaste splatter on mirror",
        "The bathroom mirror has toothpaste splatter across it.",
        "Clean the mirror",
        Trade.cleaning,
        "Vanity mirror",
        Severity.cosmetic,
        Grounding.sam_concept,
        "toothpaste splatter",
        (64.5, 74.3),
        1.9,
        8,
        confidence=0.92,
        sam_ids=["a:5"],
    ),
    ItemRow(
        "2b_06",
        6,
        "door",
        "Bedroom",
        "Door doesn't latch",
        "The bedroom door doesn't latch when it's closed.",
        "Adjust the strike plate so the door latches",
        Trade.doors_hardware,
        "Bedroom door",
        Severity.functional,
        Grounding.sam_concept,
        "door strike plate",
        (87.0, 96.9),
        2.0,
        11,
        confidence=0.9,
        sam_ids=["b:1"],
    ),
    ItemRow(
        "2b_07",
        7,
        "scuff",
        "Bedroom",
        "Scuff marks on wall",
        "Scuff marks on the bedroom wall to the left of the closet door.",
        "Touch up the paint",
        Trade.painting,
        "Left of the closet door",
        Severity.cosmetic,
        Grounding.sam_concept,
        "wall scuff mark",
        (99.0, 108.3),
        1.8,
        12,
        confidence=0.94,
        sam_ids=["b:2"],
    ),
    ItemRow(
        "2b_08",
        8,
        "switch",
        "Hallway",
        "Switch plate crooked",
        "The switch plate by the hall closet is crooked.",
        "Straighten and tighten the plate",
        Trade.electrical,
        "By the hall closet",
        Severity.cosmetic,
        Grounding.sam_concept,
        "light switch plate",
        (124.6, 132.0),
        2.0,
        16,
        confidence=0.95,
        sam_ids=["b:4"],
    ),
    ItemRow(
        "2b_09",
        9,
        "window",
        "Living room",
        "Window screen missing",
        "The left living room window has no screen.",
        "Install the window screen",
        Trade.windows_glazing,
        "Left window",
        Severity.functional,
        Grounding.sam_box,
        "window",
        (136.2, 143.7),
        1.6,
        18,
        confidence=0.87,
        sam_ids=["b:5"],
    ),
    ItemRow(
        "2b_10",
        10,
        "debris",
        "Living room",
        "Sawdust on floor",
        "Sawdust on the living room floor by the window.",
        "Sweep the floor",
        Trade.cleaning,
        "Floor by the window",
        Severity.cosmetic,
        Grounding.pointed_only,
        "sawdust",
        (144.0, 152.5),
        1.9,
        19,
        confidence=0.83,
        sam_ids=["b:6"],
    ),
    ItemRow(
        "2b_11",
        11,
        "outlet",
        "Bedroom",
        "Outlet cover cracked",
        "The outlet cover left of the bed is cracked, exposing the outlet.",
        "Replace the cover",
        Trade.electrical,
        "Left of the bed",
        Severity.safety,
        Grounding.none,
        "outlet cover plate",
        (108.5, 117.2),
        None,
        13,
        evidence_dt=1.8,
        confidence=0.84,
    ),
    ItemRow(
        "2b_12",
        12,
        "closet",
        "Bedroom",
        "Dent in closet door",
        "The bedroom closet door has a dent near the bottom.",
        "Repair the dent and touch up the door",
        Trade.general,
        "Bottom of the closet door",
        Severity.cosmetic,
        Grounding.pointed_only,
        "door dent",
        (117.6, 123.4),
        1.7,
        14,
        confidence=0.41,
        sam_ids=["b:3"],
        tip=(560.0, 1450.0),
    ),
]

TURNS_W2 = [
    TurnRow(1.0, 1.9, "Re-walk, unit 2B.", TurnKind.other, None),
    TurnRow(3.0, 3.6, "Kitchen.", TurnKind.location, "Kitchen"),
    TurnRow(
        9.0, 10.8, "Cabinet knob, fixed.", TurnKind.status_update, "Kitchen", ClaimedStatus.fixed, "2b_01"
    ),
    TurnRow(
        18.0,
        20.4,
        "Coffee drips on the counter, fixed.",
        TurnKind.status_update,
        "Kitchen",
        ClaimedStatus.fixed,
        "2b_02",
    ),
    TurnRow(
        26.0,
        28.0,
        "Light over the sink, fixed.",
        TurnKind.status_update,
        "Kitchen",
        ClaimedStatus.fixed,
        "2b_03",
    ),
    TurnRow(33.0, 33.8, "Bathroom.", TurnKind.location, "Bathroom"),
    TurnRow(
        38.0, 39.6, "Towel bar, fixed.", TurnKind.status_update, "Bathroom", ClaimedStatus.fixed, "2b_04"
    ),
    TurnRow(
        45.5, 47.4, "Mirror's clean, fixed.", TurnKind.status_update, "Bathroom", ClaimedStatus.fixed, "2b_05"
    ),
    TurnRow(52.0, 52.7, "Bedroom.", TurnKind.location, "Bedroom"),
    TurnRow(
        57.0,
        59.6,
        "Bedroom door latches now, fixed.",
        TurnKind.status_update,
        "Bedroom",
        ClaimedStatus.fixed,
        "2b_06",
    ),
    TurnRow(
        65.0,
        67.0,
        "Bedroom scuffs, still there.",
        TurnKind.status_update,
        "Bedroom",
        ClaimedStatus.not_fixed,
        "2b_07",
    ),
    TurnRow(72.0, 72.6, "Hallway.", TurnKind.location, "Hallway"),
    TurnRow(
        76.0,
        78.2,
        "Switch plate, still crooked.",
        TurnKind.status_update,
        "Hallway",
        ClaimedStatus.not_fixed,
        "2b_08",
    ),
    TurnRow(83.0, 83.7, "Living room.", TurnKind.location, "Living room"),
    TurnRow(
        88.0,
        90.0,
        "Window screen, still missing.",
        TurnKind.status_update,
        "Living room",
        ClaimedStatus.not_fixed,
        "2b_09",
    ),
    TurnRow(
        96.0,
        98.0,
        "Floor's swept, fixed.",
        TurnKind.status_update,
        "Living room",
        ClaimedStatus.fixed,
        "2b_10",
    ),
    TurnRow(101.0, 102.4, "That's the re-walk.", TurnKind.other, "Living room"),
]

# item key -> (scene start, scene end, truly fixed?)
REWALK_SCENES: dict[str, tuple[float, float, bool]] = {
    "2b_01": (5.5, 12.3, True),
    "2b_02": (14.5, 21.9, False),  # claimed fixed, still visible -> needs a look
    "2b_03": (22.5, 29.5, True),
    "2b_04": (34.5, 41.1, True),
    "2b_05": (42.0, 48.9, True),
    "2b_06": (53.5, 61.1, True),
    "2b_07": (61.5, 68.5, False),
    "2b_08": (72.6, 79.7, False),
    "2b_09": (84.3, 91.5, False),
    "2b_10": (92.3, 99.5, True),
}

TURNS_W3 = [
    TurnRow(1.0, 2.4, "Unit 3A turnover walk.", TurnKind.other, None),
    TurnRow(4.0, 4.7, "Bathroom.", TurnKind.location, "Bathroom"),
    TurnRow(
        9.0,
        12.0,
        "Grout's missing at the tub corner, right side.",
        TurnKind.defect,
        "Bathroom",
        item_key="3a_01",
    ),
    TurnRow(18.0, 18.6, "Bedroom.", TurnKind.location, "Bedroom"),
    TurnRow(
        22.0,
        25.4,
        "Gap between the baseboard and the floor by the closet.",
        TurnKind.defect,
        "Bedroom",
        item_key="3a_02",
    ),
    TurnRow(33.0, 33.6, "Hallway.", TurnKind.location, "Hallway"),
    TurnRow(37.0, 40.2, "Paint drip on the door casing here.", TurnKind.defect, "Hallway", item_key="3a_03"),
    TurnRow(48.0, 48.6, "Kitchen.", TurnKind.location, "Kitchen"),
    TurnRow(
        53.0,
        56.8,
        "Cabinet door under the sink rubs the frame when you close it.",
        TurnKind.defect,
        "Kitchen",
        item_key="3a_04",
    ),
    TurnRow(70.0, 71.5, "Okay, that's it.", TurnKind.other, "Kitchen"),
]

ITEMS_3A = [
    ItemRow(
        "3a_01",
        1,
        "grout",
        "Bathroom",
        "Grout missing at tub corner",
        "Grout is missing in the right corner where the tub meets the tile.",
        "Regrout the tub corner",
        Trade.general,
        "Right corner of the tub",
        Severity.functional,
        Grounding.sam_concept,
        "tile grout line",
        (5.5, 14.0),
        1.8,
        2,
        confidence=0.82,
        sam_ids=["a:1"],
    ),
    ItemRow(
        "3a_02",
        2,
        "baseboard",
        "Bedroom",
        "Gap under baseboard",
        "There's a gap between the baseboard and the floor by the bedroom closet.",
        "Close the gap at the baseboard",
        Trade.carpentry_millwork,
        "By the closet",
        Severity.cosmetic,
        Grounding.sam_concept,
        "baseboard",
        (19.0, 27.5),
        2.0,
        4,
        confidence=0.85,
        sam_ids=["a:2"],
        tip=(1180.0, 900.0),  # on the baseboard just above the gap (the computed spot is off-frame)
    ),
    ItemRow(
        "3a_03",
        3,
        "drip",
        "Hallway",
        "Paint drip on door casing",
        "A dried paint drip on the hallway door casing.",
        "Sand and touch up the casing",
        Trade.painting,
        "Door casing",
        Severity.cosmetic,
        Grounding.sam_concept,
        "paint drip",
        (34.0, 42.0),
        1.7,
        6,
        confidence=0.8,
        sam_ids=["a:3"],
    ),
    ItemRow(
        "3a_04",
        4,
        "cabinet",
        "Kitchen",
        "Cabinet door rubs frame",
        "The sink base cabinet door rubs the frame when it closes.",
        "Adjust the hinges so the door clears the frame",
        Trade.carpentry_millwork,
        "Cabinet under the sink",
        Severity.functional,
        Grounding.sam_concept,
        "cabinet door edge",
        (49.5, 58.5),
        1.9,
        8,
        confidence=0.81,
        sam_ids=["a:4"],
    ),
]
W3_ITEMS_WRITTEN = 2  # walk3 is mid-run: items 1-2 written so far

# Items whose evidence frame doesn't support the description (verify flags them, PLAN 2.3): key -> issue.
NOT_IN_FRAME: dict[str, str] = {"2b_12": "No dent is visible on the closet door where the inspector pointed."}

TRADES_2B: list[S.TradeContactIn] = [
    S.TradeContactIn(
        trade=Trade.electrical,
        company="Ace Electric",
        contact="Maria Chen",
        email="maria.chen@example.com",
        phone="(555) 010-2201",
        slack_channel="#punch-electrical",
        slack_webhook="https://hooks.slack.example.com/services/T000/B000/demo-electrical",
    ),
    S.TradeContactIn(
        trade=Trade.carpentry_millwork,
        company="Oakline Millwork",
        contact="Dev Patel",
        email="dev.patel@example.com",
        phone="(555) 010-2202",
    ),
    S.TradeContactIn(
        trade=Trade.cleaning,
        company="Spotless Final Clean",
        contact="Rosa Alvarez",
        email="rosa.alvarez@example.com",
        phone="(555) 010-2203",
    ),
    S.TradeContactIn(
        trade=Trade.general,
        company="Northfield Builders",
        contact="Sam Okafor",
        email=None,
        phone="(555) 010-2204",
    ),
    S.TradeContactIn(
        trade=Trade.doors_hardware,
        company="Keystone Door & Hardware",
        contact="Lena Brooks",
        email="lena.brooks@example.com",
        phone="(555) 010-2205",
    ),
    S.TradeContactIn(
        trade=Trade.painting,
        company="Brightline Painting",
        contact="Tom Reyes",
        email="tom.reyes@example.com",
        phone="(555) 010-2206",
    ),
    S.TradeContactIn(
        trade=Trade.windows_glazing,
        company="Clearview Glass",
        contact="Ana Kowalski",
        email=None,
        phone="(555) 010-2207",
    ),
    S.TradeContactIn(
        trade=Trade.plumbing,
        company="Riverside Plumbing",
        contact="Joe Lindqvist",
        email="joe.lindqvist@example.com",
        phone="(555) 010-2208",
    ),
]
TRADES_3A: list[S.TradeContactIn] = [
    S.TradeContactIn(
        trade=Trade.general,
        company="Northfield Builders",
        contact="Sam Okafor",
        email=None,
        phone="(555) 010-2204",
    ),
    S.TradeContactIn(
        trade=Trade.carpentry_millwork,
        company="Oakline Millwork",
        contact="Dev Patel",
        email="dev.patel@example.com",
        phone="(555) 010-2202",
    ),
    S.TradeContactIn(
        trade=Trade.painting,
        company="Brightline Painting",
        contact="Tom Reyes",
        email="tom.reyes@example.com",
        phone="(555) 010-2206",
    ),
    S.TradeContactIn(
        trade=Trade.plumbing,
        company="Riverside Plumbing",
        contact="Joe Lindqvist",
        email="joe.lindqvist@example.com",
        phone="(555) 010-2208",
    ),
]


@dataclass
class ProjectSpec:
    id: str
    name: str
    address: str
    rooms: list[str]
    keywords: list[str]
    trades: list[S.TradeContactIn]
    created_at: datetime


PROJECTS = [
    ProjectSpec(
        PRJ_2B,
        "Unit 2B final punch",
        "418 Alder Street, Unit 2B",
        ["Kitchen", "Bathroom", "Bedroom", "Hallway", "Living room"],
        ["Unit 2B", "strike plate", "cover plate", "towel bar", "window screen", "escutcheon"],
        TRADES_2B,
        dt("2026-11-03T14:30:00Z"),
    ),
    ProjectSpec(
        PRJ_3A,
        "Unit 3A turnover",
        "418 Alder Street, Unit 3A",
        ["Bathroom", "Bedroom", "Hallway", "Kitchen"],
        ["Unit 3A", "grout", "baseboard", "casing"],
        TRADES_3A,
        dt("2026-11-04T14:45:00Z"),
    ),
]

# Dispatch log for Unit 2B: (id, at, trade, channel, item keys)
DISPATCHES_2B = [
    ("dsp_2b_01", "2026-11-03T15:42:01Z", Trade.carpentry_millwork, Channel.email, ["2b_01", "2b_04"]),
    ("dsp_2b_02", "2026-11-03T15:42:02Z", Trade.cleaning, Channel.email, ["2b_02", "2b_05", "2b_10"]),
    ("dsp_2b_03", "2026-11-03T15:42:03Z", Trade.electrical, Channel.slack, ["2b_03", "2b_08", "2b_11"]),
    ("dsp_2b_04", "2026-11-03T15:42:04Z", Trade.doors_hardware, Channel.email, ["2b_06"]),
    ("dsp_2b_05", "2026-11-03T15:42:05Z", Trade.painting, Channel.email, ["2b_07"]),
    ("dsp_2b_06", "2026-11-03T16:06:00Z", Trade.general, Channel.outbox, ["2b_04"]),
    ("dsp_2b_07", "2026-11-04T13:25:00Z", Trade.electrical, Channel.slack, ["2b_08"]),
    ("dsp_2b_08", "2026-11-04T13:25:30Z", Trade.painting, Channel.email, ["2b_07"]),
]
TRADE_EDIT_2B_04 = ("2026-11-03T16:05:00Z", Trade.carpentry_millwork, Trade.general)

# Pricing assumptions used only for mock cost estimates.
PRICE_SAM_FRAME = 0.20 / 1000
PRICE_SAM_IMAGE = 2.50 / 1000
PRICE_ASR_MIN = 0.006
PRICE_SPARK_IN = 0.40 / 1e6
PRICE_SPARK_OUT = 1.60 / 1e6


def item_id(key: str) -> str:
    return f"itm_{key}"


def share_token(key: str) -> str:
    return "shr_" + hashlib.sha1(key.encode()).hexdigest()[:10]


def media_url(*parts: str) -> str:
    return "/media/" + "/".join(parts)


def media_path(url: str) -> Path:
    return MEDIA / url.removeprefix("/media/")


def r1(x: float) -> float:
    return round(x, 1)


def r2(x: float) -> float:
    return round(x, 2)


# --------------------------------------------------------------------------- walks


def pointing_for(
    it: ItemRow, walk: WalkSpec, point_id: str, turn: TurnRow, hold_s: float, sam_ids: list[str]
) -> Pointing:
    """The hold starts just before the description starts (PLAN 1.3 looks in [start - 2 s, end + 1 s])."""
    return make_pointing(
        KINDS[it.kind], walk.W, walk.H, turn.start - HOLD_LEAD_S, hold_s, point_id, sam_ids, it.tip
    )


def point_ids(prefix: str, rows: list[ItemRow], turns: list[TurnRow]) -> dict[str, str]:
    """Point ids for the pointed rows, numbered in time order (Point.number)."""
    pointed = sorted((it for it in rows if it.hold_s), key=lambda it: turns[it.turn_idx].start)
    return {it.key: f"pnt_{prefix}_{n + 1:02d}" for n, it in enumerate(pointed)}


def build_walks() -> dict[str, WalkSpec]:
    for rows, turns in ((ITEMS_2B, TURNS_W1), (ITEMS_3A, TURNS_W3)):
        for it in rows:
            assert turns[it.turn_idx].item_key == it.key, f"{it.key}: turn_idx points at the wrong line"

    w1 = WalkSpec(
        W1,
        PRJ_2B,
        WalkKind.initial,
        1080,
        1920,
        153.5,
        Device.glasses,
        dt("2026-11-03T15:12:00Z"),
        dt("2026-11-03T15:40:00Z"),
        [("walk1_a.mp4", 82.4), ("walk1_b.mp4", 71.1)],
        TURNS_W1,
        job_id="job_2b_first",
    )
    ids = point_ids("2b1", ITEMS_2B, TURNS_W1)
    for i, it in enumerate(ITEMS_2B):
        pointing = None
        if it.hold_s:
            pointing = pointing_for(it, w1, ids[it.key], TURNS_W1[it.turn_idx], it.hold_s, it.sam_ids)
        w1.scenes.append(
            Scene(
                it.kind, it.scene[0], it.scene[1], "before", pointing, PANS[i % 4], item_id(it.key), it.number
            )
        )
    w1.scenes.sort(key=lambda s: s.start)

    w2 = WalkSpec(
        W2,
        PRJ_2B,
        WalkKind.rewalk,
        1080,
        1920,
        104.0,
        Device.glasses,
        dt("2026-11-04T13:05:00Z"),
        dt("2026-11-04T13:20:00Z"),
        [("walk2.mp4", 104.0)],
        TURNS_W2,
        rewalk_of=W1,
        job_id="job_2b_rewalk",
    )
    by_key = {it.key: it for it in ITEMS_2B}
    for k, (key, (s, e, fixed)) in enumerate(REWALK_SCENES.items()):
        it = by_key[key]
        turn = next(tr for tr in TURNS_W2 if tr.item_key == key)
        # On the re-walk the inspector points at the same spot again and says its status.
        pointing = pointing_for(
            it, w2, f"pnt_2b2_{k + 1:02d}", turn, round(1.6 + 0.1 * (k % 5), 1), [f"a:{k + 1}"]
        )
        w2.scenes.append(
            Scene(
                it.kind,
                s,
                e,
                "fixed" if fixed else "still",
                pointing,
                PANS[(k + 1) % 4],
                item_id(key),
                it.number,
            )
        )

    w3 = WalkSpec(
        W3,
        PRJ_3A,
        WalkKind.initial,
        1920,
        1080,
        78.0,
        Device.phone,
        dt("2026-11-04T14:40:00Z"),
        dt("2026-11-04T14:52:00Z"),
        [("walk3.mov", 78.0)],
        TURNS_W3,
        job_id="job_3a_first",
        transit=(222, 216, 206),
    )
    ids = point_ids("3a1", ITEMS_3A, TURNS_W3)
    for i, it in enumerate(ITEMS_3A):
        assert it.hold_s
        pointing = pointing_for(it, w3, ids[it.key], TURNS_W3[it.turn_idx], it.hold_s, it.sam_ids)
        w3.scenes.append(
            Scene(
                it.kind,
                it.scene[0],
                it.scene[1],
                "before",
                pointing,
                PANS[(i + 2) % 4],
                item_id(it.key),
                it.number,
            )
        )

    for w in (w1, w2, w3):
        for a, b in zip(w.scenes, w.scenes[1:], strict=False):
            assert a.end <= b.start, f"{w.id}: scenes overlap at {a.end} > {b.start}"
        for sc in w.scenes:
            pt = sc.pointing
            if pt is None:
                continue
            assert sc.start + 0.1 <= pt.hold_start - ENTER_S, (
                f"{w.id} {sc.item_id}: hand enters before the scene"
            )
            assert pt.evidence_t <= sc.end - 0.1, f"{w.id} {sc.item_id}: evidence frame after the scene"
            assert abs(pt.evidence_t - pt.t) <= 1.5, (
                f"{w.id} {sc.item_id}: evidence frame too far from the point"
            )
            assert 1.5 <= pt.hold_s <= 2.5
    return {w.id: w for w in (w1, w2, w3)}


def scene_at(walk: WalkSpec, t: float) -> Scene | None:
    for s in walk.scenes:
        if s.start <= t < s.end:
            return s
    return None


def render_frame(walk: WalkSpec, t: float, scale: float, scene: Scene | None = None) -> Image.Image:
    size = (round(walk.W * scale), round(walk.H * scale))
    img = Image.new("RGB", size, walk.transit)
    scene = scene or scene_at(walk, t)
    if scene is None:  # walking between stops: plain wall with a sliding baseboard
        c = Canvas(img, scale, (0, 40 * math.sin(t / 3)))
        c.rect(-400, walk.H * 0.86, walk.W + 400, walk.H * 0.86 + 50, TRIM)
        c.rect(-400, walk.H * 0.86 + 50, walk.W + 400, walk.H + 400, (200, 190, 176))
        return img
    c = Canvas(img, scale, scene.offset(t))
    KINDS[scene.kind].draw(c, walk.W, walk.H, scene.state)
    hand = scene.pointing.hand_scene(t) if scene.pointing else None
    if hand:
        c.poly(hand, SKIN, SKIN_EDGE, 3)
    return img


def draw_outlines(
    img: Image.Image, defect: Poly | None, pointer: Pt | None = None, hand: Poly | None = None
) -> Image.Image:
    """Defect outline (red, 30% fill), hand outline (blue) and the "pointed here" dot, each on a white halo."""
    base = img.convert("RGBA")
    if defect:
        over = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(over).polygon(defect, fill=(*REDLINE, 77))
        base = Image.alpha_composite(base, over)
    d = ImageDraw.Draw(base)
    for poly, color in ((hand, BLUE), (defect, REDLINE)):
        if poly:
            closed = poly + [poly[0]]
            d.line(closed, fill=WHITE, width=14, joint="curve")
            d.line(closed, fill=color, width=7, joint="curve")
    if pointer:
        x, y = pointer
        halo, r = POINTER_R + 4, POINTER_R
        d.ellipse((x - halo, y - halo, x + halo, y + halo), fill=WHITE)
        d.ellipse((x - r, y - r, x + r, y + r), fill=BLUE)
    return base.convert("RGB")


def crop_box_for(walk: WalkSpec, scene: Scene, t: float) -> tuple[float, float, float, float]:
    """4:3 box centered on where the finger pointed + the defect (same framing before and after)."""
    kind = KINDS[scene.kind]
    pts: list[Pt] = []
    if scene.pointing:
        tx, ty = scene.pointing.tip
        pts += rect_poly(tx - 70, ty - 70, tx + 70, ty + 70)
    if kind.defect:
        pts += kind.defect
    if kind.focus:
        pts += rect_poly(*kind.focus)
    if not pts:
        pts = rect_poly(walk.W * 0.3, walk.H * 0.3, walk.W * 0.7, walk.H * 0.7)
    ox, oy = scene.offset(t)
    x1, y1, x2, y2 = bbox(shift(pts, ox, oy))
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    w = max((x2 - x1) * 1.5, (y2 - y1) * 1.5 * 4 / 3, 560)
    w = min(w, walk.W, walk.H * 4 / 3)
    h = w * 3 / 4
    bx1 = min(max(0, cx - w / 2), walk.W - w)
    by1 = min(max(0, cy - h / 2), walk.H - h)
    return (bx1, by1, bx1 + w, by1 + h)


@dataclass
class Shot:
    raw: Image.Image
    outlined: Image.Image
    crop: Image.Image
    defect: Poly | None
    pointer: Pt | None  # "pointed here" marker in this frame (display pixels)
    crop_box: tuple[float, float, float, float]


def shoot(walk: WalkSpec, scene: Scene, t: float, show_defect: bool) -> Shot:
    """An evidence (or re-walk) frame after the hand has left: defect outline + "pointed here" dot."""
    assert scene.hand_display(t, walk.W, walk.H) is None, f"{scene.item_id}: the hand is still in view at {t}"
    raw = render_frame(walk, t, 1.0, scene)
    ox, oy = scene.offset(t)
    kind = KINDS[scene.kind]
    defect = shift(kind.defect, ox, oy) if (show_defect and kind.defect and scene.state != "fixed") else None
    pointer = scene.to_display(scene.pointing.tip, t) if scene.pointing else None
    outlined = draw_outlines(raw, defect, pointer)
    cb = crop_box_for(walk, scene, t)
    crop = outlined.crop(tuple(round(v) for v in cb)).resize((800, 600), Image.Resampling.LANCZOS)
    return Shot(raw, outlined, crop, defect, pointer, cb)


def shoot_pointing(walk: WalkSpec, scene: Scene) -> tuple[Image.Image, Poly]:
    """The frame in the middle of the hold, with the hand outlined in blue (`Evidence.pointing_frame`)."""
    assert scene.pointing
    t = scene.pointing.t
    hand = scene.hand_display(t, walk.W, walk.H)
    assert hand, f"{scene.item_id}: no hand in view at the pointing time {t}"
    return draw_outlines(render_frame(walk, t, 1.0, scene), None, None, hand), hand


def save_jpeg(img: Image.Image, url: str, quality: int = 85) -> None:
    p = media_path(url)
    p.parent.mkdir(parents=True, exist_ok=True)
    img.save(p, "JPEG", quality=quality, optimize=True)


def encode_video(walk: WalkSpec, url: str) -> None:
    out = media_path(url)
    out.parent.mkdir(parents=True, exist_ok=True)
    vw, vh = round(walk.W * VIDEO_SCALE), round(walk.H * VIDEO_SCALE)
    n = round(walk.duration_s * VIDEO_FPS)
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{vw}x{vh}",
        "-r",
        str(VIDEO_FPS),
        "-i",
        "-",
        "-f",
        "lavfi",
        "-t",
        f"{walk.duration_s}",
        "-i",
        "anullsrc=r=16000:cl=mono",
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "30",
        "-maxrate",
        "75k",
        "-bufsize",
        "300k",
        "-pix_fmt",
        "yuv420p",
        "-threads",
        "1",  # multi-threaded x264 with VBV isn't byte-repeatable; one thread keeps the output deterministic
        "-g",
        str(VIDEO_FPS * 4),  # a keyframe every 4 s keeps seeking quick and the file small
        "-c:a",
        "aac",
        "-b:a",
        "8k",  # the mock audio is silence; keep it small
        "-shortest",
        "-movflags",
        "+faststart",
        "-map_metadata",
        "-1",
        "-fflags",
        "+bitexact",
        "-flags:v",
        "+bitexact",
        "-flags:a",
        "+bitexact",
        str(out),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin is not None
    for i in range(n):
        proc.stdin.write(render_frame(walk, i / VIDEO_FPS, VIDEO_SCALE).tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise SystemExit(f"ffmpeg failed while encoding {out}")


# --------------------------------------------------------------------------- JSON output


def write_json(rel: str, model: BaseModel) -> None:
    """Write one mock. Request bodies (requests/) keep only the keys a client would send."""
    p = OUT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    data = model.model_dump(mode="json", by_alias=True, exclude_unset=rel.startswith("requests/"))
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def clean_output(keep_video: bool) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for child in OUT.iterdir():
        if child.name in KEEP:
            continue
        if child.name == "media" and keep_video:
            for f in sorted(child.rglob("*"), reverse=True):
                if f.is_file() and f.suffix != ".mp4":
                    f.unlink()
                elif f.is_dir() and not any(f.iterdir()):
                    f.rmdir()
            continue
        shutil.rmtree(child) if child.is_dir() else child.unlink()


# --------------------------------------------------------------------------- model-call timelines


@dataclass
class CallSpec:
    stage: Stage
    model: str
    provider: Provider
    latency_ms: int
    tokens_in: int | None = None
    tokens_out: int | None = None
    frames: int | None = None
    images: int | None = None
    audio_s: float | None = None
    ok: bool = True
    error: str | None = None
    item_key: str | None = None
    offset_ms: int = 0  # start, relative to the stage start

    def cost(self) -> float:
        if not self.ok:
            return 0.0
        if self.model == MODEL_SAM:
            if self.frames:
                return max(self.frames, 50) * PRICE_SAM_FRAME
            return (self.images or 1) * PRICE_SAM_IMAGE
        if self.model == MODEL_ASR:
            return (self.audio_s or 0) / 60 * PRICE_ASR_MIN
        return (self.tokens_in or 0) * PRICE_SPARK_IN + (self.tokens_out or 0) * PRICE_SPARK_OUT


def spark(
    stage: Stage,
    latency: int,
    tin: int,
    tout: int,
    key: str | None = None,
    off: int = 0,
    provider: Provider = Provider.baseten,
    ok: bool = True,
    error: str | None = None,
) -> CallSpec:
    model = MODEL_SPARK_HOSTED if provider == Provider.meta_fallback else MODEL_SPARK_OPEN
    return CallSpec(
        stage, model, provider, latency, tin, tout, item_key=key, offset_ms=off, ok=ok, error=error
    )


def sam_img(key: str, latency: int, off: int) -> CallSpec:
    return CallSpec(
        Stage.ground_defects, MODEL_SAM, Provider.meta, latency, images=1, item_key=key, offset_ms=off
    )


STAGE_MS_W1: dict[Stage, int] = {
    Stage.ingest: 9200,
    Stage.transcribe: 7100,
    Stage.track_hands: 44400,
    Stage.find_points: 1100,
    Stage.classify_turns: 4800,
    Stage.fuse: 100,
    Stage.pick_evidence: 3900,
    Stage.write_items: 16200,
    Stage.ground_defects: 9600,
    Stage.verify: 14600,
    Stage.persist: 600,
}
STAGE_MS_W2: dict[Stage, int] = {
    Stage.ingest: 6100,
    Stage.transcribe: 5200,
    Stage.track_hands: 31800,
    Stage.find_points: 700,
    Stage.classify_turns: 4100,
    Stage.fuse: 100,
    Stage.pick_evidence: 2900,
    Stage.resolve_closures: 19400,
    Stage.persist: 400,
}
STAGE_MS_W3: dict[Stage, int] = {
    Stage.ingest: 7400,
    Stage.transcribe: 5900,
    Stage.track_hands: 29600,
    Stage.find_points: 400,
    Stage.classify_turns: 3900,
    Stage.fuse: 100,
    Stage.pick_evidence: 3300,
    Stage.write_items: 14800,
    Stage.ground_defects: 6900,
    Stage.verify: 9800,
    Stage.persist: 500,
}


def calls_w1() -> list[CallSpec]:
    calls = [
        CallSpec(Stage.transcribe, MODEL_ASR, Provider.meta, 6900, audio_s=153.5, offset_ms=100),
        # one SAM 3.1 hand-tracking request per clip (ids reset per clip)
        CallSpec(Stage.track_hands, MODEL_SAM, Provider.meta, 41800, frames=824, offset_ms=300),
        CallSpec(Stage.track_hands, MODEL_SAM, Provider.meta, 36900, frames=711, offset_ms=450),
        spark(Stage.classify_turns, 4600, 3100, 1450, None, 100),
    ]
    written = ITEMS_2B
    for i, it in enumerate(written):
        calls.append(
            spark(
                Stage.write_items,
                3200 + 137 * (i % 5),
                5400 + 40 * i,
                410 + 9 * i,
                it.key,
                (i // 4) * 4900 + (i % 4) * 150,
            )
        )
    off = 0
    for it in written:
        # concept prompt first; on a miss, a box prompt from Spark Open's defect_box (PLAN 2.2)
        calls.append(sam_img(it.key, 1350 if it.grounding == Grounding.sam_concept else 1280, off))
        if it.grounding != Grounding.sam_concept:
            calls.append(sam_img(it.key, 1190, off + 1300))
        off += 600
    for i, it in enumerate(written):
        base = (i // 4) * 4100 + (i % 4) * 120
        if it.key == "2b_09":
            calls.append(spark(Stage.verify, 2050, 0, 0, it.key, base, ok=False, error="502 Bad Gateway"))
            calls.append(
                spark(Stage.verify, 3000, 0, 0, it.key, base + 2300, ok=False, error="Timed out after 3.0 s")
            )
            calls.append(
                spark(Stage.verify, 3900, 4250, 640, it.key, base + 5600, provider=Provider.meta_fallback)
            )
        else:
            calls.append(spark(Stage.verify, 3500 + 90 * (i % 4), 4200 + 30 * i, 600 + 11 * i, it.key, base))
    return calls


def calls_w2() -> list[CallSpec]:
    calls = [
        CallSpec(Stage.transcribe, MODEL_ASR, Provider.meta, 5000, audio_s=104.0, offset_ms=100),
        CallSpec(Stage.track_hands, MODEL_SAM, Provider.meta, 31400, frames=1040, offset_ms=200),
        spark(Stage.classify_turns, 3900, 2600, 1180, None, 100),
        spark(Stage.resolve_closures, 4300, 3800, 900, None, 100),
    ]
    for i, key in enumerate(REWALK_SCENES):
        calls.append(
            spark(
                Stage.resolve_closures,
                2900 + 80 * (i % 3),
                3600 + 25 * i,
                260 + 7 * i,
                key,
                4600 + (i // 4) * 4800 + (i % 4) * 140,
            )
        )
    return calls


def calls_w3(written: int) -> list[CallSpec]:
    calls = [
        CallSpec(Stage.transcribe, MODEL_ASR, Provider.meta, 5700, audio_s=78.0, offset_ms=100),
        CallSpec(Stage.track_hands, MODEL_SAM, Provider.meta, 29200, frames=780, offset_ms=200),
        spark(Stage.classify_turns, 3700, 1900, 820, None, 100),
    ]
    for i, it in enumerate(ITEMS_3A[:written]):
        calls.append(spark(Stage.write_items, 3300 + 120 * i, 5100 + 30 * i, 400 + 10 * i, it.key, 150 * i))
    return calls


def stage_windows(
    stages: Iterable[Stage], ms: dict[Stage, int], start: datetime
) -> dict[Stage, tuple[datetime, datetime]]:
    out: dict[Stage, tuple[datetime, datetime]] = {}
    t = start
    for st in stages:
        e = t + timedelta(milliseconds=ms[st])
        out[st] = (t, e)
        t = e
    return out


def model_call_outs(
    walk_id: str, prefix: str, specs: list[CallSpec], windows: dict[Stage, tuple[datetime, datetime]]
) -> list[S.ModelCallOut]:
    outs = []
    for s in specs:
        started = windows[s.stage][0] + timedelta(milliseconds=s.offset_ms)
        outs.append((started, s))
    outs.sort(key=lambda x: x[0])
    result = []
    for i, (started, s) in enumerate(outs):
        result.append(
            S.ModelCallOut(
                id=f"mc_{prefix}_{i + 1:03d}",
                walk_id=walk_id,
                item_id=item_id(s.item_key) if s.item_key else None,
                stage=s.stage,
                model=s.model,
                model_label=model_label(s.model),
                provider=s.provider,
                latency_ms=s.latency_ms,
                tokens_in=s.tokens_in if s.ok else None,
                tokens_out=s.tokens_out if s.ok else None,
                frames=s.frames,
                est_cost_usd=round(s.cost(), 5),
                cached=False,
                ok=s.ok,
                error=s.error,
                started_at=started,
            )
        )
    return result


# --------------------------------------------------------------------------- the generator


class Gen:
    def __init__(self, video: bool) -> None:
        self.video = video
        self.walks = build_walks()
        self.projects = {p.id: p for p in PROJECTS}
        self.items: dict[str, S.ItemOut] = {}
        self.turn_ids: dict[str, list[str]] = {}
        self.dispatches: dict[str, S.DispatchOut] = {}
        self.calls: dict[str, list[S.ModelCallOut]] = {}
        self.windows: dict[str, dict[Stage, tuple[datetime, datetime]]] = {}

    # ---------------------------------------------------------------- helpers

    def turn_id(self, walk_id: str, idx: int) -> str:
        short = {W1: "2b1", W2: "2b2", W3: "3a1"}[walk_id]
        return f"trn_{short}_{idx + 1:02d}"

    def rows(self, project_id: str) -> list[ItemRow]:
        return ITEMS_2B if project_id == PRJ_2B else ITEMS_3A

    def contact(self, project_id: str, trade: Trade) -> S.TradeContactIn | None:
        return next((c for c in self.projects[project_id].trades if c.trade == trade), None)

    @staticmethod
    def contact_out(c: S.TradeContactIn | None) -> S.TradeContactOut | None:
        if c is None:
            return None
        return S.TradeContactOut(
            trade=c.trade,
            company=c.company,
            contact=c.contact,
            email=c.email,
            phone=c.phone,
            slack_channel=c.slack_channel,
            has_slack_webhook=bool(c.slack_webhook),
        )

    # ---------------------------------------------------------------- media

    def make_media(self) -> dict[str, dict[str, Any]]:
        """Render evidence/crops/after images and posters; returns per-item geometry."""
        geo: dict[str, dict[str, Any]] = {}
        for walk_id, rows in ((W1, ITEMS_2B), (W3, ITEMS_3A)):
            walk = self.walks[walk_id]
            for it in rows:
                scene = next(s for s in walk.scenes if s.item_id == item_id(it.key))
                base = f"items/{item_id(it.key)}"
                g: dict[str, Any] = {"walk": walk, "hand_box": None}
                if scene.pointing:  # evidence = a sharp frame just after the hand leaves (PLAN 1.6)
                    t = scene.pointing.evidence_t
                    pointing_img, hand = shoot_pointing(walk, scene)
                    save_jpeg(pointing_img, media_url(base, "pointing.jpg"))
                    g["hand_box"] = box_out(bbox(hand), walk.W, walk.H)
                else:  # voice-only: a frame from the description window, no marker
                    t = walk.turns[it.turn_idx].start + it.evidence_dt
                show_defect = it.grounding in (Grounding.sam_concept, Grounding.sam_box)
                shot = shoot(walk, scene, t, show_defect)
                save_jpeg(shot.raw, media_url(base, "frame.jpg"))
                save_jpeg(shot.outlined, media_url(base, "evidence.jpg"))
                save_jpeg(shot.crop, media_url(base, "crop.jpg"))
                geo[it.key] = {**g, "t": r2(t), "shot": shot}

        w2 = self.walks[W2]
        for key in REWALK_SCENES:
            scene = next(s for s in w2.scenes if s.item_id == item_id(key))
            assert scene.pointing
            t = scene.pointing.evidence_t  # re-walk frame after the inspector points at the spot again
            shot = shoot(w2, scene, t, show_defect=True)
            base = f"items/{item_id(key)}"
            save_jpeg(shot.crop, media_url(base, "after.jpg"))
            save_jpeg(shot.outlined, media_url(base, "after_frame.jpg"))
            geo[key]["after_t"] = r2(t)

        for walk in self.walks.values():
            first = walk.scenes[0]
            poster = render_frame(walk, (first.start + first.end) / 2, VIDEO_SCALE)
            save_jpeg(poster, media_url("walks", walk.id, "poster.jpg"), quality=80)
            video_url = media_url("walks", walk.id, "display.mp4")
            if self.video or not media_path(video_url).exists():
                print(f"encoding {video_url} ({walk.duration_s:.0f} s)")
                encode_video(walk, video_url)
        return geo

    # ---------------------------------------------------------------- items

    def build_items(self, geo: dict[str, dict[str, Any]]) -> None:
        sends_by_item: dict[str, list[S.DispatchOut]] = {}
        for d in self.dispatches.values():
            for iid in d.item_ids:
                sends_by_item.setdefault(iid, []).append(d)
        for project_id, walk_id, rows, spacing_ms in (
            (PRJ_2B, W1, ITEMS_2B, 1300),
            (PRJ_3A, W3, ITEMS_3A, 3400),
        ):
            for n_in_walk, it in enumerate(rows):
                self.items[item_id(it.key)] = self.build_item(
                    it,
                    project_id,
                    self.walks[walk_id],
                    n_in_walk,
                    spacing_ms,
                    geo[it.key],
                    sorted(sends_by_item.get(item_id(it.key), []), key=lambda d: d.sent_at),
                )

    def build_item(
        self,
        it: ItemRow,
        project_id: str,
        walk: WalkSpec,
        n_in_walk: int,
        spacing_ms: int,
        g: dict[str, Any],
        sends: list[S.DispatchOut],
    ) -> S.ItemOut:
        iid = item_id(it.key)
        win = self.windows[walk.id]
        shot: Shot = g["shot"]
        scene = next(s for s in walk.scenes if s.item_id == iid)

        turn = walk.turns[it.turn_idx]
        quote, qs, qe = turn.text, turn.start, turn.end
        turn_ids = [self.turn_id(walk.id, it.turn_idx)]
        v_start, v_end = max(0.0, qs - 2.0), min(walk.duration_s, qe + 1.0)  # PLAN 1.5

        base = f"items/{iid}"
        pointer = S.XYOut(x=r1(shot.pointer[0]), y=r1(shot.pointer[1])) if shot.pointer else None
        evidence = S.EvidenceOut(
            t=g["t"],
            image_url=media_url(base, "evidence.jpg"),
            raw_url=media_url(base, "frame.jpg"),
            crop_url=media_url(base, "crop.jpg"),
            width=walk.W,
            height=walk.H,
            defect_svg=svg_path(shot.defect) if shot.defect else None,
            defect_box=box_out(bbox(shot.defect), walk.W, walk.H) if shot.defect else None,
            crop_box=box_out(shot.crop_box, walk.W, walk.H),
            pointer=pointer,
            pointing_frame=media_url(base, "pointing.jpg") if scene.pointing else None,
            hand_box=g["hand_box"],
            grounding=it.grounding,
            sam_concept=it.sam_concept,
        )

        history: list[S.HistoryEntryOut] = []

        def log(
            at: datetime,
            actor: HistoryActor,
            action: HistoryAction,
            note: str | None = None,
            fld: str | None = None,
            frm: str | None = None,
            to: str | None = None,
        ) -> None:
            history.append(
                S.HistoryEntryOut(at=at, actor=actor, action=action, field=fld, from_=frm, to=to, note=note)
            )

        # ---- created + verified
        status = ItemStatus.open
        review_reason: str | None = None
        verification: S.VerificationOut | None = None
        trade = it.trade
        created_at = win[Stage.write_items][0] + timedelta(milliseconds=spacing_ms * (n_in_walk + 1))
        log(
            created_at,
            HistoryActor.model,
            HistoryAction.created,
            "Written by Muse Spark Open on Baseten.",
            "status",
            None,
            status.value,
        )
        # walk3 is mid-run: its first items are written but not checked yet. Items 3-4 show the
        # state after the run finishes (they appear in the progress replay, not the list).
        pending_check = project_id == PRJ_3A and n_in_walk < W3_ITEMS_WRITTEN
        if not pending_check:
            checked_at = win[Stage.verify][0] + timedelta(milliseconds=1150 * (n_in_walk + 1))
            provider = Provider.meta_fallback if it.key == "2b_09" else Provider.baseten
            if it.key in NOT_IN_FRAME:  # PLAN 2.3: the frame doesn't support the description
                verification = S.VerificationOut(
                    consistent=False,
                    note=REVIEW_NOT_IN_FRAME,
                    issues=[NOT_IN_FRAME[it.key]],
                    provider=provider,
                    checked_at=checked_at,
                )
                status, review_reason = ItemStatus.needs_review, REVIEW_NOT_IN_FRAME
                log(
                    checked_at,
                    HistoryActor.model,
                    HistoryAction.flagged,
                    f"{REVIEW_NOT_IN_FRAME} {NOT_IN_FRAME[it.key]}",
                    "status",
                    ItemStatus.open.value,
                    status.value,
                )
            else:
                verification = S.VerificationOut(
                    consistent=True, note=MATCH_NOTE, issues=[], provider=provider, checked_at=checked_at
                )
                log(
                    checked_at,
                    HistoryActor.model,
                    HistoryAction.verified,
                    MATCH_NOTE
                    if provider == Provider.baseten
                    else MATCH_NOTE + " Checked by Muse Spark 1.3 after Baseten timed out.",
                )
            if it.key == "2b_09":
                log(
                    checked_at + timedelta(seconds=40),
                    HistoryActor.system,
                    HistoryAction.noted,
                    "Not sent automatically: no email or Slack on file for Clearview Glass.",
                )

        # ---- everything after, in time order: sends, the PM's edit, the re-walk check
        events: list[tuple[datetime, str, Any]] = [(d.sent_at, "send", d) for d in sends]
        if it.key == "2b_04":
            events.append((dt(TRADE_EDIT_2B_04[0]), "edit", None))
        closure: S.ClosureOut | None = None
        if project_id == PRJ_2B and it.key in REWALK_SCENES:
            closure, check_note, new_status = self.closure_for(it, g)
            events.append((closure.decided_at, "closure", (check_note, new_status)))
        elif it.key == "2b_11":
            decided = self.windows[W2][Stage.resolve_closures][1]
            closure = S.ClosureOut(
                rewalk_id=W2,
                rewalk_turn_id=None,
                quote=None,
                after_t=None,
                before_image_url=evidence.crop_url,
                after_image_url=None,
                after_frame_url=None,
                voice_status=VoiceStatus.not_mentioned,
                vision_status=VisionStatus.not_checked,
                decision=ClosureDecision.not_checked,
                note=NOT_CHECKED_ON_REWALK,
                decided_at=decided,
            )
            events.append((decided, "not_checked", None))

        times_sent = 0
        for at, kind, payload in sorted(events, key=lambda e: e[0]):
            if kind == "send":
                d: S.DispatchOut = payload
                how = {
                    Channel.slack: f"on Slack ({d.destination})",
                    Channel.email: f"by email ({d.destination})",
                    Channel.outbox: "from the outbox",
                }[d.channel]
                verb = "Sent again" if times_sent else "Sent"
                times_sent += 1
                prev, status = status, ItemStatus.dispatched
                log(
                    at,
                    HistoryActor.system,
                    HistoryAction.dispatched,
                    f"{verb} to {d.company} {how}.",
                    "status",
                    prev.value,
                    status.value,
                )
            elif kind == "edit":
                _, frm, to = TRADE_EDIT_2B_04
                trade = to
                log(
                    at,
                    HistoryActor.human,
                    HistoryAction.edited,
                    "Changed by the project manager.",
                    "trade",
                    frm.value,
                    to.value,
                )
            elif kind == "closure":
                check_note, new_status = payload
                log(at, HistoryActor.model, HistoryAction.rewalk_checked, check_note)
                if new_status != status:
                    action = (
                        HistoryAction.flagged
                        if new_status == ItemStatus.needs_review
                        else HistoryAction.status_changed
                    )
                    msg = {
                        ItemStatus.closed: "Signed off from the re-walk.",
                        ItemStatus.needs_review: REVIEW_FALSE_FIX,
                        ItemStatus.open: "Still open after the re-walk.",
                    }[new_status]
                    log(
                        at + timedelta(milliseconds=200),
                        HistoryActor.system,
                        action,
                        msg,
                        "status",
                        status.value,
                        new_status.value,
                    )
                    status = new_status
                    review_reason = REVIEW_FALSE_FIX if new_status == ItemStatus.needs_review else None
            elif kind == "not_checked":
                log(
                    at,
                    HistoryActor.system,
                    HistoryAction.rewalk_checked,
                    "Not checked on re-walk. The bedroom was visited, but nobody mentioned this item.",
                )

        dispatch = None
        if sends:
            last = sends[-1]
            dispatch = S.DispatchSummaryOut(
                dispatch_id=last.id,
                trade=last.trade,
                company=last.company,
                channel=last.channel,
                sent_at=last.sent_at,
                times_sent=len(sends),
            )
        return S.ItemOut(
            id=iid,
            project_id=project_id,
            number=it.number,
            origin_walk_id=walk.id,
            point_id=scene.pointing.point_id if scene.pointing else None,
            turn_ids=turn_ids,
            title=it.title,
            description=it.description,
            action=it.action,
            trade=trade,
            room=it.room,
            location_detail=it.location_detail,
            severity=it.severity,
            status=status,
            review_reason=review_reason,
            quote=quote,
            quote_start_s=qs,
            quote_end_s=qe,
            video_url=media_url("walks", walk.id, "display.mp4"),
            video_start_s=r1(v_start),
            video_end_s=r1(v_end),
            confidence=it.confidence,
            evidence=evidence,
            verification=verification,
            closure=closure,
            history=history,
            dispatch=dispatch,
            created_at=created_at,
            updated_at=max(e.at for e in history),
        )

    def closure_for(self, it: ItemRow, g: dict[str, Any]) -> tuple[S.ClosureOut, str, ItemStatus]:
        """Re-walk outcome per the PLAN 7.3 decision table. Returns (closure, history note, new status)."""
        w2 = self.walks[W2]
        _, _, fixed = REWALK_SCENES[it.key]
        turn_idx = next(i for i, tr in enumerate(w2.turns) if tr.item_key == it.key)
        turn = w2.turns[turn_idx]
        k = list(REWALK_SCENES).index(it.key)
        decided = self.windows[W2][Stage.resolve_closures][0] + timedelta(milliseconds=4600 + 1700 * (k + 1))
        if fixed:
            voice, vision, decision = VoiceStatus.fixed, VisionStatus.fixed, ClosureDecision.signed_off
            note, new_status = "The defect is gone where the inspector pointed.", ItemStatus.closed
            check_note = "Marked fixed on the re-walk, and the defect is gone."
        elif turn.claimed == ClaimedStatus.fixed:
            voice, vision, decision = (
                VoiceStatus.fixed,
                VisionStatus.still_visible,
                ClosureDecision.needs_review,
            )
            note = "Coffee drips are still visible where the inspector pointed."
            new_status = ItemStatus.needs_review
            check_note = "Marked fixed on the re-walk, but the coffee drips are still visible."
        else:
            voice, vision, decision = (
                VoiceStatus.not_fixed,
                VisionStatus.still_visible,
                ClosureDecision.still_open,
            )
            note, new_status = "Said not fixed, and the defect is still visible.", ItemStatus.open
            check_note = "Said not fixed on the re-walk. Still open."
        base = f"items/{item_id(it.key)}"
        closure = S.ClosureOut(
            rewalk_id=W2,
            rewalk_turn_id=self.turn_id(W2, turn_idx),
            quote=turn.text,
            after_t=g["after_t"],
            before_image_url=media_url(base, "crop.jpg"),
            after_image_url=media_url(base, "after.jpg"),
            after_frame_url=media_url(base, "after_frame.jpg"),
            voice_status=voice,
            vision_status=vision,
            decision=decision,
            note=note,
            decided_at=decided,
        )
        return closure, check_note, new_status

    # ---------------------------------------------------------------- dispatches

    def build_dispatches(self) -> None:
        rows = {it.key: it for it in ITEMS_2B}
        for did, at, trade, channel, keys in DISPATCHES_2B:
            c = self.contact(PRJ_2B, trade)
            assert c is not None
            dest = {Channel.slack: c.slack_channel, Channel.email: c.email, Channel.outbox: "Outbox"}[channel]
            lines = [self.dispatch_line(rows[k]) for k in keys]
            heading = self.heading(PRJ_2B, trade, len(keys))
            text = self.message_text(heading, lines)
            payload: dict[str, Any]
            if channel == Channel.slack:
                payload = {"text": self.slack_text(heading, lines)}
            elif channel == Channel.email:
                payload = {"to": dest, "subject": heading, "text": text}
            else:
                payload = {"text": text}
            self.dispatches[did] = S.DispatchOut(
                id=did,
                project_id=PRJ_2B,
                trade=trade,
                company=c.company,
                channel=channel,
                destination=dest,
                item_ids=[item_id(k) for k in keys],
                sent_at=dt(at),
                ok=True,
                error=None,
                payload=payload,
            )

    def dispatch_line(self, it: ItemRow) -> S.DispatchLineOut:
        where = it.room
        if it.location_detail:
            where = f"{it.room}, {it.location_detail[0].lower()}{it.location_detail[1:]}"
        return S.DispatchLineOut(
            item_id=item_id(it.key),
            number=it.number,
            title=it.title,
            line=f"{where}. {it.action}.",
            url=f"/share/item/{share_token(it.key)}",
        )

    def heading(self, project_id: str, trade: Trade, n: int) -> str:
        noun = "item" if n == 1 else "items"
        return f"{self.projects[project_id].name}: {n} {TRADE_LABEL[trade].lower()} {noun}"

    @staticmethod
    def message_text(heading: str, lines: list[S.DispatchLineOut]) -> str:
        parts = [heading, ""]
        for ln in lines:
            parts += [f"{ln.number}. {ln.title}", ln.line, f"View item and video: {SHARE_BASE}{ln.url}", ""]
        return "\n".join(parts).rstrip() + "\n"

    @staticmethod
    def slack_text(heading: str, lines: list[S.DispatchLineOut]) -> str:
        parts = [f"*{heading}*", ""]
        for ln in lines:
            parts += [
                f"*{ln.number}*  {ln.title}",
                ln.line,
                f"<{SHARE_BASE}{ln.url}|View item and video>",
                "",
            ]
        return "\n".join(parts).rstrip() + "\n"

    # ---------------------------------------------------------------- stage progress

    def stage_rows(self, walk: WalkSpec, upto: float | None) -> list[S.StageProgressOut]:
        """Stage rows for `walk`; with `upto` (seconds since job start) the run is shown mid-way."""
        order = STAGES_REWALK if walk.kind == WalkKind.rewalk else STAGES_INITIAL
        win = self.windows[walk.id]
        job_start = win[order[0]][0]
        rows = []
        for st in order:
            s, e = win[st]
            if upto is None:
                state = StageStatus.done
            else:
                now = job_start + timedelta(seconds=upto)
                state = (
                    StageStatus.done if e <= now else StageStatus.running if s <= now else StageStatus.pending
                )
            frac = None
            if upto is not None and state == StageStatus.running:
                frac = (job_start + timedelta(seconds=upto) - s) / (e - s)
            counts, detail, prog = self.stage_detail(walk, st, state, frac)
            rows.append(
                S.StageProgressOut(
                    stage=st,
                    label=STAGE_LABEL[st],
                    status=state,
                    started_at=s if state != StageStatus.pending else None,
                    finished_at=e if state == StageStatus.done else None,
                    duration_ms=round((e - s).total_seconds() * 1000) if state == StageStatus.done else None,
                    counts=counts,
                    progress=prog,
                    detail=detail,
                    model_label=STAGE_MODEL.get(st),
                    message=None,  # the runner's own line; the API falls back to `detail`
                )
            )
        return rows

    def stage_detail(
        self, walk: WalkSpec, st: Stage, state: StageStatus, frac: float | None
    ) -> tuple[dict[str, int], str | None, float | None]:
        """(counts, plain detail line, progress 0..1) for one checklist row."""
        rows = ITEMS_2B if walk.project_id == PRJ_2B else ITEMS_3A
        n_items = len(rows) if walk.kind == WalkKind.initial else len(REWALK_SCENES)
        n_written = len(rows)  # Spark Open writes every described defect
        n_points = len([s for s in walk.scenes if s.pointing])
        n_lines = len(walk.turns)
        label = STAGE_MODEL.get(st)

        if state == StageStatus.pending:
            return {}, (f"{label}, waiting" if label else None), None
        if state == StageStatus.running:
            f = frac or 0.0
            if st in (Stage.write_items, Stage.verify):
                done = max(0, min(n_written - 1, math.floor(n_written * f)))
                key, verb = ("written", "written") if st == Stage.write_items else ("checked", "checked")
                return (
                    {key: done, "total": n_written},
                    f"Muse Spark Open on Baseten, {done} of {n_written} {verb}",
                    r2(done / n_written),
                )
            frames = round(walk.duration_s * 10)
            running = {
                Stage.ingest: "Joining clips and preparing the video",
                Stage.transcribe: "Muse Voice Transcribe, listening",
                Stage.track_hands: f"SAM 3.1, {round(frames * f)} of {frames} frames",
                Stage.ground_defects: "SAM 3.1, outlining each defect",
            }.get(st, f"{label}, working" if label else "Working")
            return {}, running, r2(f)

        if st == Stage.ingest:
            n = len(walk.clips)
            mins, secs = divmod(round(walk.duration_s), 60)
            return {"clips": n}, f"{n} {'clip' if n == 1 else 'clips'}, {mins} min {secs} s", None
        if st == Stage.transcribe:
            return {"lines": n_lines}, f"Muse Voice Transcribe found {n_lines} lines", None
        if st == Stage.track_hands:
            frames = round(walk.duration_s * 10)
            counts = {"frames": frames, "hands": n_points}
            if n_points == 0:
                return counts, "No pointing found", None
            return counts, f"{n_points} {'point' if n_points == 1 else 'points'} found", None
        if st == Stage.find_points:
            n_mentions = n_items
            voice = n_mentions - n_points if walk.kind == WalkKind.initial else 0
            counts = {"points": n_points, "mentions": n_mentions, "voice_only": voice}
            tail = f", {voice} described without pointing" if voice else ""
            return counts, f"{n_points} places you pointed{tail}", None
        if st == Stage.classify_turns:
            return {"lines": n_lines}, f"Muse Spark Open on Baseten sorted {n_lines} lines", None
        if st == Stage.fuse:
            if walk.kind == WalkKind.rewalk:
                return {"status_updates": n_items}, f"{n_items} status updates", None
            pointed = len([r for r in rows if r.hold_s])
            voice = len(rows) - pointed
            parts = [f"{pointed} matched to where you pointed"] if pointed else []
            if voice:
                parts.append(f"{voice} described without pointing")
            return {"items": len(rows), "pointed": pointed, "voice_only": voice}, ", ".join(parts), None
        if st == Stage.pick_evidence:
            return {"photos": n_items}, f"{n_items} photos picked", None
        if st == Stage.write_items:
            return (
                {"written": n_written, "total": n_written},
                f"Muse Spark Open on Baseten wrote {n_written} items",
                None,
            )
        if st == Stage.ground_defects:
            outlined = len([r for r in rows if r.grounding in (Grounding.sam_concept, Grounding.sam_box)])
            return {"outlined": outlined}, f"SAM 3.1 outlined {outlined} defects", None
        if st == Stage.verify:
            flagged = len([r for r in rows if r.key in NOT_IN_FRAME])
            matched = n_written - flagged
            extra = f", {flagged} needs a look" if flagged else ""
            return (
                {"checked": n_written, "matched": matched, "total": n_written},
                f"{matched} match the photo and what was said{extra}",
                None,
            )
        if st == Stage.resolve_closures:
            return (
                {"signed_off": 6, "still_open": 3, "needs_review": 1},
                "6 signed off, 3 still open, 1 needs a look",
                None,
            )
        return {}, "Saved", None

    # ---------------------------------------------------------------- walk + job outputs

    def walk_out(self, walk: WalkSpec, upto: float | None = None) -> S.WalkOut:
        processing = upto is not None
        clips, off = [], 0.0
        for i, (name, dur) in enumerate(walk.clips):
            clips.append(S.ClipOut(order=i, filename=name, duration_s=dur, offset_s=r1(off)))
            off += dur
        items = [i for i in self.project_items(walk.project_id) if i.origin_walk_id == walk.id]
        if walk.kind == WalkKind.rewalk:
            checked = [
                i
                for i in self.items.values()
                if i.closure
                and i.closure.rewalk_id == walk.id
                and i.closure.decision != ClosureDecision.not_checked
            ]
            dec = [i.closure.decision for i in checked if i.closure]
            counts = S.WalkCountsOut(
                items=len(checked),
                points=len([s for s in walk.scenes if s.pointing]),
                turns=len(walk.turns),
                needs_review=dec.count(ClosureDecision.needs_review),
                signed_off=dec.count(ClosureDecision.signed_off),
                still_open=dec.count(ClosureDecision.still_open),
                not_checked=len(
                    [
                        i
                        for i in self.items.values()
                        if i.closure
                        and i.closure.rewalk_id == walk.id
                        and i.closure.decision == ClosureDecision.not_checked
                    ]
                ),
            )
        else:
            st = [i.status for i in items]
            counts = S.WalkCountsOut(
                items=len(items),
                points=len([s for s in walk.scenes if s.pointing]),
                turns=len(walk.turns),
                needs_review=st.count(ItemStatus.needs_review),
                signed_off=st.count(ItemStatus.closed),
                still_open=st.count(ItemStatus.open) + st.count(ItemStatus.dispatched),
                not_checked=0,
            )
        win = self.windows[walk.id]
        order = STAGES_REWALK if walk.kind == WalkKind.rewalk else STAGES_INITIAL
        updated = (win[order[0]][0] + timedelta(seconds=upto)) if processing else win[order[-1]][1]
        return S.WalkOut(
            id=walk.id,
            project_id=walk.project_id,
            project_name=self.projects[walk.project_id].name,
            kind=walk.kind,
            status=WalkStatus.processing if processing else WalkStatus.ready,
            rewalk_of=walk.rewalk_of,
            job_id=walk.job_id,
            recorded_at=walk.recorded_at,
            created_at=walk.created_at,
            updated_at=updated,
            clips=clips,
            stages=self.stage_rows(walk, upto),
            media=S.WalkMediaOut(
                display_mp4=media_url("walks", walk.id, "display.mp4"),
                poster=media_url("walks", walk.id, "poster.jpg"),
            ),
            duration_s=walk.duration_s,
            fps=30.0,
            width=walk.W,
            height=walk.H,
            orientation=Orientation.portrait if walk.H > walk.W else Orientation.landscape,
            device=walk.device,
            error=None,
            counts=counts,
        )

    def job_out(self, walk: WalkSpec, upto: float | None, *, queued: bool = False) -> S.JobProgressOut:
        order = STAGES_REWALK if walk.kind == WalkKind.rewalk else STAGES_INITIAL
        win = self.windows[walk.id]
        start, end = win[order[0]][0], win[order[-1]][1]
        total_s = (end - start).total_seconds()
        if queued:
            stages = [
                S.StageProgressOut(
                    stage=st,
                    label=STAGE_LABEL[st],
                    status=StageStatus.pending,
                    started_at=None,
                    finished_at=None,
                    duration_ms=None,
                    counts={},
                    progress=None,
                    detail=(f"{STAGE_MODEL[st]}, waiting" if st in STAGE_MODEL else None),
                    model_label=STAGE_MODEL.get(st),
                    message=None,
                )
                for st in order
            ]
            now, status, current, prog = start - timedelta(seconds=4), JobStatus.queued, None, 0.0
        else:
            stages = self.stage_rows(walk, upto)
            done = upto is None or upto >= total_s
            now = end if done else start + timedelta(seconds=upto or 0)
            status = JobStatus.done if done else JobStatus.running
            current = (
                None if done else next((s.stage for s in stages if s.status == StageStatus.running), None)
            )
            prog = 1.0 if done else r2((upto or 0) / total_s)
        rows = ITEMS_2B if walk.project_id == PRJ_2B else ITEMS_3A
        if walk.kind == WalkKind.rewalk:
            thumbs: list[S.ItemThumbOut] = []
            expected = None
        else:
            written = next((s.counts.get("written", 0) for s in stages if s.stage == Stage.write_items), 0)
            done_writing = any(s.stage == Stage.write_items and s.status == StageStatus.done for s in stages)
            visible = rows if done_writing else rows[:written]
            thumbs = [
                S.ItemThumbOut(
                    id=item_id(r.key),
                    number=r.number,
                    title=r.title,
                    crop_url=media_url("items", item_id(r.key), "crop.jpg"),
                )
                for r in visible
            ]
            expected = len(rows)
        finished = status == JobStatus.done
        n = len(rows) if walk.kind == WalkKind.initial else len(REWALK_SCENES)
        message = (
            (f"{n} items ready" if walk.kind == WalkKind.initial else f"{n} items checked")
            if finished
            else None
        )
        return S.JobProgressOut(
            job_id=walk.job_id,
            walk_id=walk.id,
            kind=JobKind.process_walk,
            status=status,
            walk_status=WalkStatus.ready if finished else WalkStatus.processing,
            attempts=0 if queued else 1,
            stage=current,
            stages=stages,
            progress=prog,
            message=message,
            items_so_far=thumbs,
            items_expected=expected,
            error=None,
            created_at=walk.created_at,
            started_at=None if queued else start,
            finished_at=end if finished else None,
            updated_at=now,
        )

    # ---------------------------------------------------------------- turns / points / overlay

    def turns_out(self, walk: WalkSpec) -> list[S.TurnOut]:
        out = []
        for i, tr in enumerate(walk.turns):
            ids = (
                [item_id(tr.item_key)]
                if tr.item_key
                and (walk.id != W3 or [r.key for r in ITEMS_3A].index(tr.item_key) < W3_ITEMS_WRITTEN)
                else []
            )
            out.append(
                S.TurnOut(
                    id=self.turn_id(walk.id, i),
                    walk_id=walk.id,
                    idx=i,
                    start_ms=round(tr.start * 1000),
                    end_ms=round(tr.end * 1000),
                    text=tr.text,
                    kind=tr.kind,
                    room=tr.room,
                    claimed=tr.claimed,
                    item_ids=ids,
                )
            )
        return out

    def points_out(self, walk: WalkSpec) -> list[S.PointOut]:
        """The walk's pointing events in time order (`Point` rows)."""
        written = {item_id(r.key) for r in ITEMS_3A[:W3_ITEMS_WRITTEN]}
        out = []
        pointed = sorted((sc for sc in walk.scenes if sc.pointing), key=lambda sc: sc.pointing.t)  # type: ignore[union-attr]
        for n, sc in enumerate(pointed):
            pt = sc.pointing
            assert pt and sc.item_id
            hand = sc.hand_display(pt.t, walk.W, walk.H)
            assert hand
            tip = sc.to_display(pt.tip_at(pt.t) or pt.tip, pt.t)  # where the fingertip actually is then
            has_item = walk.id != W3 or sc.item_id in written  # walk3 is mid-run
            turn_idx = next(
                i for i, tr in enumerate(walk.turns) if tr.item_key and item_id(tr.item_key) == sc.item_id
            )
            out.append(
                S.PointOut(
                    id=pt.point_id,
                    walk_id=walk.id,
                    item_id=sc.item_id if has_item else None,
                    item_number=sc.number if has_item else None,
                    number=n + 1,
                    t=pt.t,
                    frame=round(pt.t * OVERLAY_FPS),
                    fingertip_x=r1(tip[0]),
                    fingertip_y=r1(tip[1]),
                    hand_box=box_out(bbox(hand), walk.W, walk.H),
                    hold_s=pt.hold_s,
                    sam_object_ids=pt.sam_ids,
                    mean_conf=round(0.86 + 0.01 * (n % 9), 3),
                    turn_id=self.turn_id(walk.id, turn_idx),
                )
            )
            assert pt.point_id.endswith(f"_{n + 1:02d}"), "point ids are numbered in time order"
        return out

    def overlay_out(self, walk: WalkSpec) -> S.OverlayOut:
        frames = []
        n = int(walk.duration_s * OVERLAY_FPS)
        for i in range(n + 1):
            t = r1(i / OVERLAY_FPS)
            hands = []
            for sc in walk.scenes:
                hand = sc.hand_display(t, walk.W, walk.H)
                if hand is None:
                    continue
                pt = sc.pointing
                assert pt
                tip = sc.to_display(pt.tip_at(t) or pt.tip, t)
                holding = pt.holding(t) and 0 <= tip[0] <= walk.W and 0 <= tip[1] <= walk.H
                hands.append(
                    S.OverlayHandOut(
                        point_id=pt.point_id,
                        number=sc.number,
                        box=box_out(bbox(hand), walk.W, walk.H),
                        svg=svg_path(hand),
                        fingertip=S.XYOut(x=r1(tip[0]), y=r1(tip[1])) if holding else None,
                    )
                )
            if hands:
                frames.append(S.OverlayFrameOut(t=t, hands=hands))
        events = []
        for tr in walk.turns:
            if tr.item_key:
                it = self.items[item_id(tr.item_key)]
                events.append(S.OverlayEventOut(t=tr.start, item_id=it.id, number=it.number))
        events.sort(key=lambda e: e.t)
        return S.OverlayOut(
            walk_id=walk.id,
            fps=OVERLAY_FPS,
            width=walk.W,
            height=walk.H,
            duration_s=walk.duration_s,
            frames=frames,
            events=events,
        )

    # ---------------------------------------------------------------- lists / facets

    def counts(self, items: list[S.ItemOut]) -> S.ItemCountsOut:
        st = [i.status for i in items]
        opened = st.count(ItemStatus.open) + st.count(ItemStatus.dispatched)
        nr = st.count(ItemStatus.needs_review)
        return S.ItemCountsOut(
            todo=opened + nr,
            open=opened,
            needs_review=nr,
            signed_off=st.count(ItemStatus.closed),
            all=len(items),
        )

    def project_items(self, project_id: str) -> list[S.ItemOut]:
        items = [i for i in self.items.values() if i.project_id == project_id]
        if project_id == PRJ_3A:  # walk3 is mid-run: only the items written so far are listed
            keep = {item_id(r.key) for r in ITEMS_3A[:W3_ITEMS_WRITTEN]}
            items = [i for i in items if i.id in keep]

        def walk_time(i: S.ItemOut) -> float:
            return i.quote_start_s if i.quote_start_s is not None else (i.evidence.t if i.evidence else 0.0)

        return sorted(items, key=walk_time)

    def item_list(self, project_id: str) -> S.ItemListOut:
        items = self.project_items(project_id)
        rooms = self.projects[project_id].rooms
        facets = S.FacetsOut(
            trade={t: n for t in Trade if (n := sum(1 for i in items if i.trade == t))},
            severity={s: n for s in Severity if (n := sum(1 for i in items if i.severity == s))},
            room={r: n for r in rooms if (n := sum(1 for i in items if i.room == r))},
            status={s: n for s in ItemStatus if (n := sum(1 for i in items if i.status == s))},
        )
        return S.ItemListOut(
            project_id=project_id, items=items, facets=facets, counts=self.counts(items), total=len(items)
        )

    def project_out(self, p: ProjectSpec) -> S.ProjectOut:
        walks = [w for w in self.walks.values() if w.project_id == p.id]
        last = max(w.recorded_at for w in walks) if walks else None
        updated = max([i.updated_at for i in self.items.values() if i.project_id == p.id] + [p.created_at])
        return S.ProjectOut(
            id=p.id,
            name=p.name,
            address=p.address,
            trades=[c for t in p.trades if (c := self.contact_out(t))],
            keywords=p.keywords,
            rooms=p.rooms,
            counts=self.counts(self.project_items(p.id)),
            walk_count=len(walks),
            last_walk_at=last,
            created_at=p.created_at,
            updated_at=updated,
        )

    # ---------------------------------------------------------------- trades

    def trade_outputs(self, project_id: str) -> None:
        items = self.project_items(project_id)
        p = self.projects[project_id]
        directory = [c.trade for c in p.trades]
        trades = directory + [t for t in Trade if t not in directory and any(i.trade == t for i in items)]
        summaries = []
        for t in trades:
            mine = [i for i in items if i.trade == t]
            sends = [d for d in self.dispatches.values() if d.project_id == project_id and d.trade == t]
            summaries.append(
                S.TradeSummaryOut(
                    trade=t,
                    contact=self.contact_out(self.contact(project_id, t)),
                    open_count=sum(1 for i in mine if i.status in (ItemStatus.open, ItemStatus.dispatched)),
                    needs_review_count=sum(1 for i in mine if i.status == ItemStatus.needs_review),
                    signed_off_count=sum(1 for i in mine if i.status == ItemStatus.closed),
                    last_sent_at=max((d.sent_at for d in sends), default=None),
                )
            )
            self.preview_and_history(project_id, t, mine, sends)
        write_json(
            f"projects/{project_id}/trades.json", S.TradeListOut(project_id=project_id, trades=summaries)
        )

    def preview_and_history(
        self, project_id: str, trade: Trade, mine: list[S.ItemOut], sends: list[S.DispatchOut]
    ) -> None:
        rows = {item_id(r.key): r for r in self.rows(project_id)}
        c = self.contact(project_id, trade)
        sendable = [i for i in mine if i.status in (ItemStatus.open, ItemStatus.dispatched)]
        lines = [self.dispatch_line(rows[i.id]) for i in sendable]
        heading = self.heading(project_id, trade, len(lines))
        company = c.company if c else TRADE_LABEL[trade]
        channels = [
            S.DispatchChannelOut(
                channel=Channel.slack,
                available=bool(c and c.slack_webhook),
                destination=c.slack_channel if c and c.slack_webhook else None,
                reason=None if c and c.slack_webhook else f"No Slack channel set up for {company}.",
            ),
            S.DispatchChannelOut(
                channel=Channel.email,
                available=bool(c and c.email),
                destination=c.email if c else None,
                reason=None if c and c.email else f"No email on file for {company}.",
            ),
            S.DispatchChannelOut(channel=Channel.outbox, available=True, destination="Outbox", reason=None),
        ]
        default = next(ch.channel for ch in channels if ch.available)
        # Same sentences as punchwalk/dispatch/service.py::why()
        if default == Channel.slack:
            channel_reason = f"{company} has a Slack channel ({c.slack_channel})." if c and c.slack_channel else (
                f"{company} gets the team's Slack channel."
            )
        elif default == Channel.email:
            channel_reason = f"{company} gets email at {c.email}." if c else None
        elif channels[1].destination:  # an address on file, but email isn't available
            channel_reason = (
                f"Email isn't set up on this server yet, so this goes to the Outbox for {company}, ready to share."
            )
        else:
            channel_reason = f"No Slack channel or email set up for {company}, so this goes to the Outbox, ready to share."
        base = f"projects/{project_id}/trades/{trade.value}"
        write_json(
            f"{base}/preview.json",
            S.DispatchPreviewOut(
                project_id=project_id,
                trade=trade,
                contact=self.contact_out(c),
                channel=default,
                channel_reason=channel_reason,
                channels=channels,
                heading=heading,
                lines=lines,
                subject=heading,
                message_text=self.message_text(heading, lines),
                slack_text=self.slack_text(heading, lines),
                item_ids=[ln.item_id for ln in lines],
            ),
        )
        history = sorted(sends, key=lambda d: d.sent_at, reverse=True)
        write_json(
            f"{base}/dispatches.json",
            S.DispatchListOut(project_id=project_id, trade=trade, dispatches=history),
        )
        if lines:
            dest = next(ch.destination for ch in channels if ch.channel == default)
            text = self.message_text(heading, lines)
            payload: dict[str, Any] = (
                {"text": self.slack_text(heading, lines)}
                if default == Channel.slack
                else (
                    {"to": dest, "subject": heading, "text": text}
                    if default == Channel.email
                    else {"text": text}
                )
            )
            write_json(
                f"{base}/dispatch.post.json",
                S.DispatchOut(
                    id=f"dsp_{project_id.removeprefix('prj_')}_new_{trade.value}",
                    project_id=project_id,
                    trade=trade,
                    company=company,
                    channel=default,
                    destination=dest,
                    item_ids=[ln.item_id for ln in lines],
                    sent_at=dt("2026-11-04T15:00:00Z"),
                    ok=True,
                    error=None,
                    payload=payload,
                ),
            )

    # ---------------------------------------------------------------- re-walk + processing

    def rewalk_summary(self) -> S.RewalkSummaryOut:
        w1, w2 = self.walks[W1], self.walks[W2]
        groups: dict[ClosureDecision, list[S.RewalkItemOut]] = {d: [] for d in ClosureDecision}
        for it in self.project_items(PRJ_2B):
            cl = it.closure
            if not cl or cl.rewalk_id != W2:
                continue
            reason = {
                ClosureDecision.needs_review: REVIEW_FALSE_FIX,
                ClosureDecision.not_checked: NOT_CHECKED_ON_REWALK,
            }.get(cl.decision)
            groups[cl.decision].append(
                S.RewalkItemOut(
                    item_id=it.id,
                    number=it.number,
                    title=it.title,
                    room=it.room,
                    trade=it.trade,
                    status=it.status,
                    decision=cl.decision,
                    reason=reason,
                    quote=cl.quote,
                    before_image_url=cl.before_image_url,
                    after_image_url=cl.after_image_url,
                    before_at=w1.recorded_at,
                    after_at=w2.recorded_at if cl.after_image_url else None,
                )
            )
        c = S.RewalkCountsOut(
            checked=sum(len(groups[d]) for d in ClosureDecision if d != ClosureDecision.not_checked),
            signed_off=len(groups[ClosureDecision.signed_off]),
            still_open=len(groups[ClosureDecision.still_open]),
            needs_a_look=len(groups[ClosureDecision.needs_review]),
            not_checked=len(groups[ClosureDecision.not_checked]),
        )
        look = "needs a look" if c.needs_a_look == 1 else "need a look"
        headline = f"{c.signed_off} signed off. {c.still_open} still open. {c.needs_a_look} {look}."
        return S.RewalkSummaryOut(
            walk_id=W2,
            initial_walk_id=W1,
            project_id=PRJ_2B,
            recorded_at=w2.recorded_at,
            counts=c,
            headline=headline,
            needs_a_look=groups[ClosureDecision.needs_review],
            signed_off=groups[ClosureDecision.signed_off],
            still_open=groups[ClosureDecision.still_open],
            not_checked=groups[ClosureDecision.not_checked],
        )

    def processing(self, walk: WalkSpec, upto: float | None) -> S.ProcessingSummaryOut:
        calls = self.calls[walk.id]
        win = self.windows[walk.id]
        order = STAGES_REWALK if walk.kind == WalkKind.rewalk else STAGES_INITIAL
        stage_rows = {s.stage: s for s in self.stage_rows(walk, upto)}
        headline = HEADLINE_STEPS_REWALK if walk.kind == WalkKind.rewalk else HEADLINE_STEPS_INITIAL
        steps = []
        for st in headline:
            row = stage_rows[st]
            if row.status == StageStatus.pending:
                continue
            mine = [c for c in calls if c.stage == st]
            s, e = win[st]
            if row.status == StageStatus.running and upto is not None:
                e = win[order[0]][0] + timedelta(seconds=upto)
            providers = [c.provider for c in mine if c.ok]
            steps.append(
                S.ProcessingStepOut(
                    stage=st,
                    title=STAGE_TITLE[st],
                    model_label=STAGE_MODEL.get(st) if st != Stage.fuse else None,
                    provider=providers[0] if providers else None,
                    detail=self.summary_detail(walk, st, row),
                    duration_ms=row.duration_ms or round((e - s).total_seconds() * 1000),
                    calls=len(mine),
                    fallback_calls=sum(1 for c in mine if c.provider == Provider.meta_fallback),
                    cost_usd=round(sum(c.est_cost_usd for c in mine), 4),
                )
            )
        models: list[str] = []
        for c in calls:
            if c.model_label not in models and c.provider != Provider.meta_fallback:
                models.append(c.model_label)
        start = win[order[0]][0]
        total = (win[order[-1]][1] - start) if upto is None else timedelta(seconds=upto)
        eval_block = None
        if walk.id == W1 or walk.id == W2:
            eval_block = S.EvalSummaryOut(
                source="mock",
                updated_at=dt("2026-11-04T06:30:00Z"),
                metrics=[
                    S.EvalMetricOut(key="defects_found", label="Defects found", value=11, total=11),
                    S.EvalMetricOut(key="right_trade", label="Right trade", value=10, total=11),
                    S.EvalMetricOut(
                        key="false_claims_caught", label='False "fixed" claims caught', value=1, total=1
                    ),
                ],
            )
        return S.ProcessingSummaryOut(
            walk_id=walk.id,
            kind=walk.kind,
            status=WalkStatus.processing if upto is not None else WalkStatus.ready,
            total_duration_ms=round(total.total_seconds() * 1000),
            total_cost_usd=round(sum(c.est_cost_usd for c in calls), 4),
            models_used=models,
            steps=steps,
            calls=calls,
            eval=eval_block,
        )

    def summary_detail(self, walk: WalkSpec, st: Stage, row: S.StageProgressOut) -> str:
        if row.status != StageStatus.done:
            return row.detail or ""
        n_points = len([s for s in walk.scenes if s.pointing])
        rows = ITEMS_2B if walk.project_id == PRJ_2B else ITEMS_3A
        if st == Stage.transcribe:
            return f"{len(walk.turns)} lines with start and end times"
        if st == Stage.track_hands:
            if not n_points:
                return "No pointing in this walk"
            return f"{n_points} points found across the whole video"
        if st == Stage.fuse:
            return f"Timing and position, computed in the app. {row.detail}."
        if st == Stage.write_items:
            return f"Summary, action, trade, room and severity for {len(rows)} items"
        if st == Stage.ground_defects:
            n = len([r for r in rows if r.grounding in (Grounding.sam_concept, Grounding.sam_box)])
            return f"Spark Open names what to outline near your fingertip, SAM draws it. {n} outlined."
        if st == Stage.verify:
            flagged = len([r for r in rows if r.key in NOT_IN_FRAME])
            n = len(rows) - flagged
            flag = f" {flagged} needs a look: the photo doesn't show the defect described." if flagged else ""
            tail = " One check ran on Muse Spark 1.3 after Baseten timed out." if walk.id == W1 else ""
            return f"{n} match the photo and what was said.{flag}{tail}"
        if st == Stage.classify_turns:
            return f"{len(REWALK_SCENES)} status updates found in {len(walk.turns)} lines"
        if st == Stage.resolve_closures:
            return "Each update matched to its item, then the spot you pointed at compared with the original photo"
        return row.detail or ""

    # ---------------------------------------------------------------- share

    def share_out(self, it: S.ItemOut) -> S.ShareItemOut:
        p = self.projects[it.project_id]
        c = self.contact(it.project_id, it.trade)
        walk = self.walks[it.origin_walk_id]
        return S.ShareItemOut(
            token=share_token(it.id.removeprefix("itm_")),
            project_name=p.name,
            project_address=p.address,
            number=it.number,
            title=it.title,
            description=it.description,
            action=it.action,
            trade=it.trade,
            company=c.company if c else None,
            room=it.room,
            location_detail=it.location_detail,
            severity=it.severity,
            status=it.status,
            quote=it.quote,
            quote_start_s=it.quote_start_s,
            quote_end_s=it.quote_end_s,
            video_url=it.video_url,
            video_start_s=it.video_start_s,
            video_end_s=it.video_end_s,
            evidence=it.evidence,
            closure=it.closure,
            walked_at=walk.recorded_at,
        )

    # ---------------------------------------------------------------- run

    def run(self) -> None:
        for walk, ms in (
            (self.walks[W1], STAGE_MS_W1),
            (self.walks[W2], STAGE_MS_W2),
            (self.walks[W3], STAGE_MS_W3),
        ):
            order = STAGES_REWALK if walk.kind == WalkKind.rewalk else STAGES_INITIAL
            self.windows[walk.id] = stage_windows(order, ms, walk.created_at + timedelta(seconds=5))
        self.calls[W1] = model_call_outs(W1, "2b1", calls_w1(), self.windows[W1])
        self.calls[W2] = model_call_outs(W2, "2b2", calls_w2(), self.windows[W2])
        self.calls[W3] = model_call_outs(W3, "3a1", calls_w3(W3_ITEMS_WRITTEN), self.windows[W3])

        geo = self.make_media()
        self.build_dispatches()
        self.build_items(geo)

        w1, w2, w3 = self.walks[W1], self.walks[W2], self.walks[W3]
        w3_now = (
            self.windows[W3][Stage.write_items][0] - self.windows[W3][Stage.ingest][0]
        ).total_seconds() + 7.6

        write_json(
            "healthz.json",
            S.HealthOut(
                ok=True,
                version="0.1.0",
                demo_mode=True,
                reasoning_provider=ReasoningProvider.baseten,
                time=dt("2026-11-04T15:00:00Z"),
            ),
        )
        write_json("projects.json", S.ProjectListOut(projects=[self.project_out(p) for p in PROJECTS]))
        for p in PROJECTS:
            write_json(f"projects/{p.id}.json", self.project_out(p))
            write_json(f"projects/{p.id}/items.json", self.item_list(p.id))
            self.trade_outputs(p.id)

        walk_outs = {W1: self.walk_out(w1), W2: self.walk_out(w2), W3: self.walk_out(w3, w3_now)}
        newest = sorted(walk_outs.values(), key=lambda w: w.recorded_at or w.created_at, reverse=True)
        write_json("walks.json", S.WalkListOut(walks=newest))
        for p in PROJECTS:
            write_json(
                f"projects/{p.id}/walks.json",
                S.WalkListOut(walks=[w for w in newest if w.project_id == p.id]),
            )
        write_json(
            f"projects/{PRJ_3A}/walks.post.json",
            S.UploadOut(walk_id=W3, job_id=w3.job_id, status=WalkStatus.uploaded),
        )

        for walk in (w1, w2, w3):
            wo = walk_outs[walk.id]
            if walk.kind == WalkKind.rewalk:
                items = [
                    i
                    for i in self.project_items(PRJ_2B)
                    if i.closure
                    and i.closure.rewalk_id == walk.id
                    and i.closure.decision != ClosureDecision.not_checked
                ]
            else:
                items = [i for i in self.project_items(walk.project_id) if i.origin_walk_id == walk.id]
            write_json(
                f"walks/{walk.id}.json",
                S.WalkDetailOut(
                    walk=wo,
                    turns=self.turns_out(walk),
                    points=self.points_out(walk),
                    items=items,
                ),
            )
            upto = w3_now if walk.id == W3 else None
            write_json(f"walks/{walk.id}/progress.json", self.job_out(walk, upto))
            write_json(
                f"walks/{walk.id}/model-calls.json",
                S.ModelCallListOut(walk_id=walk.id, calls=self.calls[walk.id]),
            )
            write_json(f"walks/{walk.id}/processing.json", self.processing(walk, upto))
            if walk.id != W3:
                write_json(f"walks/{walk.id}/overlay.json", self.overlay_out(walk))
        write_json(f"walks/{W2}/rewalk-summary.json", self.rewalk_summary())

        # Replay of walk3's run for the Record screen (mock mode steps through these).
        total = (self.windows[W3][Stage.persist][1] - self.windows[W3][Stage.ingest][0]).total_seconds()
        replay = [None, 3.0, 16.0, 52.5, w3_now, 70.0, 78.0, total]
        for i, upto in enumerate(replay):
            job = self.job_out(w3, upto, queued=upto is None)
            write_json(f"walks/{W3}/progress/{i + 1:02d}.json", job)

        for it in self.items.values():
            write_json(f"items/{it.id}.json", it)
            key = it.id.removeprefix("itm_")
            tok = share_token(key)
            write_json(f"items/{it.id}/share.post.json", S.ShareLinkOut(token=tok, url=f"/share/item/{tok}"))
            write_json(f"share/{tok}.json", self.share_out(it))

        self.write_requests()
        print(
            f"wrote {sum(1 for _ in OUT.rglob('*.json'))} JSON files and "
            f"{sum(1 for f in MEDIA.rglob('*') if f.is_file())} media files to {OUT.relative_to(ROOT)}"
        )

    def write_requests(self) -> None:
        """Example request bodies (validated by the contract test)."""
        write_json(
            "requests/projects.post.json",
            S.ProjectCreate(
                name="Unit 2B final punch",
                address="418 Alder Street, Unit 2B",
                trades=TRADES_2B[:2],
                keywords=["strike plate", "towel bar"],
                rooms=["Kitchen", "Bathroom", "Bedroom", "Hallway", "Living room"],
            ),
        )
        write_json(
            "requests/projects.patch.json",
            S.ProjectPatch(rooms=["Kitchen", "Bathroom", "Bedroom", "Hallway", "Living room", "Closet"]),
        )
        write_json(
            "requests/items.patch.sign_off.json",
            S.ItemPatch(status=ItemStatus.closed, note="Checked in person."),
        )
        write_json(
            "requests/items.patch.edit.json", S.ItemPatch(trade=Trade.general, title="Loose towel bar")
        )
        write_json(
            "requests/dispatch.post.json",
            S.DispatchCreate(channel=Channel.slack, item_ids=["itm_2b_08", "itm_2b_11"]),
        )
        write_json("requests/rewalk-link.post.json", S.RewalkLinkIn(initial_walk_id=W1))


STAGE_MODEL: dict[Stage, str] = {
    Stage.transcribe: "Muse Voice Transcribe",
    Stage.track_hands: "SAM 3.1",
    Stage.classify_turns: "Muse Spark Open on Baseten",
    Stage.write_items: "Muse Spark Open on Baseten",
    Stage.ground_defects: "SAM 3.1",
    Stage.verify: "Muse Spark Open on Baseten",
    Stage.resolve_closures: "Muse Spark Open on Baseten",
}
HEADLINE_STEPS_INITIAL = (
    Stage.transcribe,
    Stage.track_hands,
    Stage.fuse,
    Stage.write_items,
    Stage.ground_defects,
    Stage.verify,
)
HEADLINE_STEPS_REWALK = (Stage.transcribe, Stage.track_hands, Stage.classify_turns, Stage.resolve_closures)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--no-video", action="store_true", help="keep existing display.mp4 files (faster)")
    args = ap.parse_args()
    clean_output(keep_video=args.no_video)
    Gen(video=not args.no_video).run()


if __name__ == "__main__":
    main()
