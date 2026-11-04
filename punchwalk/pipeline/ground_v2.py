"""Defect grounding v2 (PLAN 10.3), behind `GROUNDING_V2=1`.

v1 (ground.py) asks SAM for the *object* the defect is on ("stovetop burner glass") and often
outlines the whole fixture. v2 locates the defect itself, then only ships an outline that passed
both a geometric score and a visual check:

a) Locate: one Muse Spark Open call on the evidence frame (fingertip drawn as a small hollow ring
   when the inspector pointed) -> `visible`, a tight `box` around the defect itself, `extent`
   (spot | region | whole_surface) and a 1-3 word defect-level SAM `concept` ("burn mark").
b) Segment: ONE SAM 3.1 request with every candidate as its own prompted object on the single image
   (`<0f>` via build_object_prompt, media-pixel coordinates). Pointed: box; box + fingertip;
   fingertip; fingertip + 4 negatives just outside the box. Unpointed: box; box + 4 negatives.
   Plus ONE concept request with the defect-level concept. Boxes whose short side is under
   V2_CROP_SMALL_FRAC of the frame run on a crop (V2_CROP_SCALE x the box, >= V2_CROP_MIN_SHORT_PX
   on the short side) and the masks are mapped back.
c) Score every returned mask: bbox IoU with the Spark box, area consistent with the box and extent
   (reject > V2_MAX_AREA_RATIO x the box, or > V2_MAX_FRAME_FRAC of the frame unless whole_surface),
   SAM confidence, and (pointed) the fingertip inside / near the mask. Near-duplicates dropped.
d) Check: the top V2_CHECK_TOP smoothed masks + the Spark box, one numbered image each (<= 4); one
   Spark Open call (medium) picks the number that tightly covers the described defect, or 0.
e) Nothing picked -> no red outline (the fingertip dot stays if pointed). A picked box is drawn as
   a rounded rectangle.
f) Smoothing: punchwalk.pipeline.mask_smooth.
g) If the evidence frame is not the pointing frame and the fingertip was not carried into it
   (pick_evidence `aligned: false`), map it with an ORB + RANSAC homography; if that fails, ground
   the item as unpointed.

Grounding mapping into the contract: mask from a box/point prompt -> sam_box; from the concept
request -> sam_concept; Spark box picked -> spark_box; nothing -> pointed_only (pointed) / none.
A model call that hits the cost cap, or fails, leaves the item without an outline (reason in its row).

Only evidence fields change (defect_svg, defect_box, grounding, sam_concept and the renders).

Dry run (reads the DB read-only, writes only under --out):
    GROUNDING_V2=1 PUNCHWALK_CACHE=readwrite uv run python -m punchwalk.pipeline.ground_v2 \\
        --data-dir ./data --project prj_demo --items 3,11,4 --out .verify/v2
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import math
import os
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

from punchwalk.clients import llm, sam
from punchwalk.clients.base import ApiError
from punchwalk.core.enums import Grounding, Stage
from punchwalk.pipeline import mask_smooth
from punchwalk.pipeline import params as P

log = logging.getLogger(__name__)

Point = tuple[float, float]
BoxF = tuple[float, float, float, float]
Extent = Literal["spot", "region", "whole_surface"]
STAGE = Stage.ground_defects.value
OUTPUT_NAME = "ground_v2.json"  # walk-level log: DATA_DIR/walks/<walk>/stages/ground_v2.json

REDLINE = (0xD9, 0x3B, 0x30)
TAPE = (0x2F, 0x86, 0xE0)
WHITE = (255, 255, 255)
GRAPHITE = (0x22, 0x26, 0x2B)


# --------------------------------------------------------------------------- inputs / schemas


@dataclass
class ItemInput:
    item_id: str
    raw: Path  # evidence frame, no outlines (display px)
    width: int
    height: int
    title: str
    description: str = ""
    quote: str | None = None
    pointer: Point | None = None  # fingertip in the evidence frame (display px)
    number: int | None = None
    hand_mask: np.ndarray | None = field(default=None, repr=False)  # 0/1 (height, width): the hand in this frame


class DefectLocate(BaseModel):
    visible: bool = Field(description="true if the described defect is visible in the photo")
    box: list[float] | None = Field(description="[x1, y1, x2, y2] tightly around the defect itself, in photo pixels, "
                                    "or null when not visible")
    extent: Literal["spot", "region", "whole_surface"] = Field(
        description="spot: a small mark/hole/part; region: an area of a surface; whole_surface: the defect covers "
                    "the whole object or surface")
    concept: str = Field(description="1-3 word name of the defect itself for a segmentation model, e.g. "
                         "'burn mark', 'stain', 'mold', 'loose cover', 'missing knob'")
    rationale: str = Field(description="one short sentence")


class OutlinePick(BaseModel):
    pick: int = Field(description="the number of the image whose red outline tightly covers the described defect, "
                      "or 0 if none does")
    rationale: str = Field(description="one short sentence")


@dataclass
class Located:
    visible: bool
    box: BoxF | None  # display px
    extent: Extent
    concept: str | None
    rationale: str
    provider: str | None = None
    model_size: tuple[int, int] | None = None
    raw_box: list[float] | None = None  # as returned, model px

    def as_row(self) -> dict[str, Any]:
        return {"visible": self.visible, "box": _r(self.box), "extent": self.extent, "concept": self.concept,
                "rationale": self.rationale, "provider": self.provider, "model_size": self.model_size,
                "raw_box": self.raw_box}


@dataclass
class Candidate:
    id: int  # SAM object id (1..n) or the concept instance's index offset by 100
    source: str  # box | box+finger | finger | finger+neg | box+neg | concept | spark_box
    grounding: Grounding
    confidence: float | None
    soft: np.ndarray | None = field(default=None, repr=False)  # frame-size 0..1 (None for spark_box)
    mask: np.ndarray | None = field(default=None, repr=False)  # frame-size 0/1
    bbox: BoxF | None = None
    area: int = 0
    iou: float = 0.0
    inside: float = 0.0  # share of mask pixels inside the Spark box
    fill: float = 0.0  # mask area / its own bbox area
    solidity: float = 0.0  # mask area / its convex hull area (a C-shaped arc is low)
    area_ratio: float = 0.0
    frame_frac: float = 0.0
    finger_dist: float | None = None
    score: float = 0.0
    rejected: str | None = None
    svg: str | None = None  # smoothed outline (set for the ones shown to the check call)
    smooth_box: BoxF | None = None
    hand_overlap: float = 0.0  # share of the mask on the tracked hand

    def as_row(self) -> dict[str, Any]:
        return {
            "id": self.id, "source": self.source, "grounding": self.grounding.value,
            "confidence": None if self.confidence is None else round(float(self.confidence), 3),
            "bbox": _r(self.bbox), "area": self.area, "iou": round(self.iou, 3), "inside": round(self.inside, 3),
            "fill": round(self.fill, 3), "solidity": round(self.solidity, 3),
            "area_ratio": round(self.area_ratio, 3), "frame_frac": round(self.frame_frac, 4),
            "finger_dist": None if self.finger_dist is None else round(self.finger_dist, 1),
            "score": round(self.score, 3), "hand_overlap": round(self.hand_overlap, 3), "rejected": self.rejected,
        }


@dataclass
class V2Result:
    grounding: Grounding
    defect_svg: str | None
    defect_box: BoxF | None
    concept: str | None
    row: dict[str, Any]


def _r(box: Sequence[float] | None, nd: int = 1) -> list[float] | None:
    return None if box is None else [round(float(v), nd) for v in box]


# --------------------------------------------------------------------------- geometry helpers


def box_area(b: Sequence[float] | None) -> float:
    if b is None:
        return 0.0
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def box_iou(a: Sequence[float] | None, b: Sequence[float] | None) -> float:
    if a is None or b is None:
        return 0.0
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = box_area(a) + box_area(b) - inter
    return inter / union if union > 0 else 0.0


def mask_iou(a: np.ndarray, b: np.ndarray) -> float:
    inter = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()
    return float(inter / union) if union else 0.0


def mask_bbox(mask: np.ndarray) -> BoxF | None:
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return None
    return float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1)


def point_to_mask(pt: Point, mask: np.ndarray) -> float:
    h, w = mask.shape
    x, y = int(min(max(pt[0], 0), w - 1)), int(min(max(pt[1], 0), h - 1))
    if mask[y, x]:
        return 0.0
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return math.inf
    return float(np.sqrt(((xs + 0.5 - pt[0]) ** 2 + (ys + 0.5 - pt[1]) ** 2).min()))


def mask_inside(mask: np.ndarray, box: Sequence[float]) -> float:
    total = int(mask.sum())
    if not total:
        return 0.0
    x1, y1, x2, y2 = (int(round(v)) for v in box)
    return float(mask[max(0, y1):max(0, y2), max(0, x1):max(0, x2)].sum()) / total


def mask_solidity(mask: np.ndarray) -> float:
    found, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not found:
        return 0.0
    hull = cv2.convexHull(np.vstack(found))
    return float(mask.sum()) / max(1.0, float(cv2.contourArea(hull)))


def clamp_box(box: Sequence[float], w: int, h: int) -> BoxF | None:
    x1, x2 = sorted((min(max(float(box[0]), 0.0), w), min(max(float(box[2]), 0.0), w)))
    y1, y2 = sorted((min(max(float(box[1]), 0.0), h), min(max(float(box[3]), 0.0), h)))
    if x2 - x1 < 2 or y2 - y1 < 2:
        return None
    return x1, y1, x2, y2


def model_view(w: int, h: int) -> tuple[float, int, int]:
    """Scale and size of the image as Spark Open sees it (long side <= llm.IMAGE_LONG_SIDE)."""
    s = min(1.0, llm.IMAGE_LONG_SIDE / max(w, h))
    return s, max(1, round(w * s)), max(1, round(h * s))


# --------------------------------------------------------------------------- drawing (own module: v2 only)


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


def draw_ring(img: Image.Image, pt: Point, radius: float | None = None) -> Image.Image:
    """A small hollow Tape-blue ring with a white halo at `pt` (the fingertip), so the defect under it
    stays visible to the model."""
    out = img.convert("RGB").copy()
    w, h = out.size
    r = radius or max(8.0, min(w, h) / P.V2_RING_RADIUS_DIV)
    lw = max(2, round(r / 5))
    d = ImageDraw.Draw(out)
    x, y = pt
    d.ellipse([x - r - 2, y - r - 2, x + r + 2, y + r + 2], outline=WHITE, width=lw + 4)
    d.ellipse([x - r, y - r, x + r, y + r], outline=TAPE, width=lw)
    return out


def draw_grid(img: Image.Image, step: int | None = None) -> Image.Image:
    """A translucent yellow pixel grid (lines every `step` px, labels every 2 steps) for the locate call.
    Measured on the demo frames (10.3 dev run): Spark Open's boxes on real photos were 150-380 px off
    without it and close to the defect with it (box IoU ~0 -> 0.66-0.72)."""
    step = step or P.V2_GRID_STEP_PX
    out = img.convert("RGB").copy()
    d = ImageDraw.Draw(out, "RGBA")
    w, h = out.size
    f = _font(max(14, step // 4))
    for x in range(0, w, step):
        major = x % (2 * step) == 0
        d.line([(x, 0), (x, h)], fill=(255, 255, 0, 150 if major else 90), width=1)
        if major:
            d.text((x + 3, 3), str(x), fill=(255, 255, 0, 255), font=f, stroke_width=2, stroke_fill=(0, 0, 0, 255))
    for y in range(0, h, step):
        major = y % (2 * step) == 0
        d.line([(0, y), (w, y)], fill=(255, 255, 0, 150 if major else 90), width=1)
        if major and y:
            d.text((3, y + 3), str(y), fill=(255, 255, 0, 255), font=f, stroke_width=2, stroke_fill=(0, 0, 0, 255))
    return out


def _label(d: ImageDraw.ImageDraw, n: int, size: int) -> None:
    pad = size // 4
    d.rounded_rectangle([pad, pad, pad + size, pad + size], radius=size // 5, fill=(*GRAPHITE, 235))
    f = _font(int(size * 0.7))
    d.text((pad + size / 2, pad + size / 2), str(n), fill=WHITE, font=f, anchor="mm")


def draw_candidate(base: Image.Image, n: int, *, svg: str | None = None, box: BoxF | None = None,
                   pointer: Point | None = None, region: tuple[int, int, int, int] | None = None) -> Image.Image:
    """The frame (cropped to `region`) with one candidate outline in red (30% fill) or a rounded box,
    the fingertip ring, and the number `n` in the top-left corner."""
    from punchwalk.pipeline.frames import svg_polygons

    rgba = base.convert("RGBA")
    w, h = rgba.size
    layer = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    lw = max(3, round(min(w, h) / 220))
    path = svg or (mask_smooth.rounded_rect_path(box) if box is not None else "")
    polys = [[(float(x), float(y)) for x, y in p] for p in svg_polygons(path)]
    for pts in polys:
        d.polygon(pts, fill=(*REDLINE, int(255 * 0.30)))
    for pts in polys:
        d.line([*pts, pts[0]], fill=(*WHITE, 230), width=lw + 4, joint="curve")
        d.line([*pts, pts[0]], fill=(*REDLINE, 255), width=lw, joint="curve")
    img = Image.alpha_composite(rgba, layer).convert("RGB")
    if pointer is not None:
        img = draw_ring(img, pointer)
    if region is not None:
        img = img.crop(region)
    d2 = ImageDraw.Draw(img, "RGBA")
    _label(d2, n, max(36, round(min(img.size) / 9)))
    return img


def jpeg_bytes(img: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def load_frame(item: ItemInput, notes: dict[str, Any] | None = None) -> Image.Image:
    """The evidence frame as RGB at exactly (item.width, item.height): every coordinate (pointer, boxes,
    SAM prompts) is in those pixels. A raw file of another size is resized (and noted), never trusted."""
    with Image.open(item.raw) as im:
        img = im.convert("RGB")
    if img.size != (item.width, item.height):
        if notes is not None:
            notes["resized_from"] = list(img.size)
        log.warning("ground_v2: %s raw is %sx%s, expected %sx%s: resizing", item.item_id, *img.size,
                    item.width, item.height)
        img = img.resize((item.width, item.height), Image.LANCZOS)
    return img


# --------------------------------------------------------------------------- (a) locate

LOCATE_SYSTEM = (
    "You help a construction punch-list inspector. Find ONE described defect in a photo and box the defect "
    "itself as tightly as you can: the mark, the damaged patch, the gap, the crooked part or the empty spot "
    "where a part is missing. Do NOT box the whole fixture, appliance, wall or object the defect is on. "
    "Reflections of the room in glossy surfaces are not defects. Coordinates are pixels of the photo as sent, "
    "origin top-left, [x1, y1, x2, y2].\n"
    "extent: 'spot' = a small mark, hole, screw, knob or part; 'region' = an area of a surface (a stain, a "
    "damaged patch, a crooked cover); 'whole_surface' = the defect really covers the entire object (a whole "
    "mirror smeared, a whole panel dirty).\n"
    "concept: 1-3 plain words naming the defect as it looks, for a segmentation model: e.g. 'burn mark', "
    "'stain', 'mold', 'pencil marks', 'crooked cover', 'screw hole', 'missing knob'. Never the item title, "
    "never the fixture alone."
)


def locate_prompt(item: ItemInput, mw: int, mh: int, ring: Point | None) -> str:
    lines = [f"Defect: {item.title}"]
    if item.description:
        lines.append(f"Details: {item.description}")
    if item.quote:
        lines.append(f'The inspector said: "{item.quote}"')
    lines.append(f"The photo is {mw}x{mh} px.")
    if ring is not None:
        lines.append(f"The inspector pointed with a finger; the small hollow blue ring at ({round(ring[0])}, "
                     f"{round(ring[1])}) marks the fingertip. The defect is under or right next to the ring, so "
                     "your box should contain the ring or come within a few dozen pixels of it.")
    else:
        lines.append("Nobody pointed in this photo; find the defect from the description.")
    if P.V2_LOCATE_GRID:
        lines.append(f"A yellow pixel grid is drawn on the photo (lines every {P.V2_GRID_STEP_PX} px, labels every "
                     f"{2 * P.V2_GRID_STEP_PX} px) to help you read coordinates.")
    lines.append("Box the defect itself, not the object it is on. If the defect is not visible, set visible to "
                 "false and box to null.")
    return "\n".join(lines)


_BAD_CONCEPTS = {"punch item", "defect", "item", "damage", "problem", "issue"}


def clean_concept(text: str | None) -> str | None:
    """1-3 lowercase words, or None (no concept request) for empty / generic answers."""
    words = " ".join((text or "").replace('"', " ").split()).split(" ")
    words = [w for w in words if w][:3]
    c = " ".join(words).strip(" .,;:").lower()
    return None if not c or c in _BAD_CONCEPTS else c[:40]


def locate(item: ItemInput, save_dir: Path | None = None) -> Located:
    img = load_frame(item)
    if item.pointer is not None:
        img = draw_ring(img, item.pointer)
    s, mw, mh = model_view(item.width, item.height)
    view = img.resize((mw, mh), Image.LANCZOS) if s < 1 else img
    if P.V2_LOCATE_GRID:
        view = draw_grid(view)
    data = jpeg_bytes(view, llm.JPEG_QUALITY)
    if save_dir is not None:
        save_dir.mkdir(parents=True, exist_ok=True)
        (save_dir / "locate_input.jpg").write_bytes(data)
    ring = (item.pointer[0] * s, item.pointer[1] * s) if item.pointer is not None else None
    result, rec = llm.structured(
        DefectLocate,
        [{"role": "system", "content": LOCATE_SYSTEM}, {"role": "user", "content": locate_prompt(item, mw, mh, ring)}],
        images=[data], reasoning_effort=P.V2_LOCATE_EFFORT, max_tokens=P.V2_LOCATE_MAX_TOKENS, stage=STAGE,
    )
    box = None
    if result.visible and result.box and len(result.box) == 4:
        box = clamp_box([v / s for v in result.box], item.width, item.height)
    return Located(visible=bool(result.visible and box is not None), box=box, extent=result.extent,
                   concept=clean_concept(result.concept), rationale=result.rationale[:300], provider=rec.provider,
                   model_size=(mw, mh), raw_box=result.box)


# --------------------------------------------------------------------------- (b) segment


def crop_for_box(box: BoxF, w: int, h: int, pointer: Point | None = None) -> tuple[int, int, int, int] | None:
    """Crop region for a small box (short side < V2_CROP_SMALL_FRAC of the frame's short side), else None.
    The crop spans V2_CROP_SCALE x the box, >= V2_CROP_MIN_SHORT_PX on its short side, inside the frame,
    grown to keep the fingertip (plus V2_CROP_POINTER_MARGIN_PX) inside it."""
    bw, bh = box[2] - box[0], box[3] - box[1]
    if min(bw, bh) >= P.V2_CROP_SMALL_FRAC * min(w, h):
        return None
    cw, ch = bw * P.V2_CROP_SCALE, bh * P.V2_CROP_SCALE
    short = min(cw, ch)
    if short < P.V2_CROP_MIN_SHORT_PX:
        k = P.V2_CROP_MIN_SHORT_PX / max(short, 1.0)
        cw, ch = cw * k, ch * k
    cw, ch = min(cw, w), min(ch, h)
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    x1 = int(round(min(max(0.0, cx - cw / 2), w - cw)))
    y1 = int(round(min(max(0.0, cy - ch / 2), h - ch)))
    x2, y2 = min(w, x1 + int(round(cw))), min(h, y1 + int(round(ch)))
    if pointer is not None:
        m = P.V2_CROP_POINTER_MARGIN_PX
        x1, y1 = max(0, min(x1, int(pointer[0] - m))), max(0, min(y1, int(pointer[1] - m)))
        x2, y2 = min(w, max(x2, int(pointer[0] + m) + 1)), min(h, max(y2, int(pointer[1] + m) + 1))
    if (x2 - x1) >= w and (y2 - y1) >= h:
        return None
    return x1, y1, x2, y2


def negative_points(box: BoxF, w: int, h: int) -> list[Point]:
    """4 points just outside the box (left, right, above, below its center); ones off the image dropped."""
    x1, y1, x2, y2 = box
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    ox = max(P.V2_NEG_OFFSET_MIN_PX, P.V2_NEG_OFFSET_FRAC * (x2 - x1))
    oy = max(P.V2_NEG_OFFSET_MIN_PX, P.V2_NEG_OFFSET_FRAC * (y2 - y1))
    pts = [(x1 - ox, cy), (x2 + ox, cy), (cx, y1 - oy), (cx, y2 + oy)]
    return [(x, y) for x, y in pts if 0 <= x < w and 0 <= y < h]


def hand_at(pointer: Point | None, hand: np.ndarray | None, radius: float) -> bool:
    """Is the hand within `radius` px of the fingertip? (Then a fingertip point would segment the finger.)"""
    if pointer is None or hand is None or not hand.any():
        return False
    return point_to_mask(pointer, hand) <= radius


def hand_mask_at(walk_dir: Path, t: float, w: int, h: int) -> np.ndarray | None:
    """The tracked hand (stages/track_hands.json, SAM frames) at evidence time `t`, scaled to (h, w)."""
    from punchwalk.pipeline.frames import rasterize_svg

    p = Path(walk_dir) / "stages" / "track_hands.json"
    try:
        track = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    sw, sh, fps = track["sam"]["width"], track["sam"]["height"], float(track["sam"]["fps"])
    frame = int(round(t * fps))
    out = None
    for r in track.get("records") or []:
        if r.get("frame") == frame and r.get("svg"):
            m = rasterize_svg(r["svg"], w, h, scale=(w / sw, h / sh))
            out = m if out is None else np.maximum(out, m)
    return out


def build_objects(box: BoxF, pointer: Point | None, w: int, h: int) -> list[tuple[str, sam.ImageObject]]:
    """Candidate prompts (media px), ids 1..n. See module docstring (b)."""
    neg = tuple(negative_points(box, w, h))
    specs: list[tuple[str, sam.ImageObject]] = []
    if pointer is not None and not (0 <= pointer[0] < w and 0 <= pointer[1] < h):
        pointer = None  # off the media (never clamp a fingertip onto the edge): box prompts only
    if pointer is not None:
        fp = ((pointer[0], pointer[1]),)
        specs = [
            ("box", sam.ImageObject(1, box=box)),
            ("box+finger", sam.ImageObject(2, box=box, positive=fp)),
            ("finger", sam.ImageObject(3, positive=fp)),
            ("finger+neg", sam.ImageObject(4, positive=fp, negative=neg)),
        ]
    else:
        specs = [("box", sam.ImageObject(1, box=box)), ("box+neg", sam.ImageObject(2, box=box, negative=neg))]
    return specs


def to_media(box: BoxF, pointer: Point | None, crop: tuple[int, int, int, int] | None) -> tuple[BoxF, Point | None]:
    if crop is None:
        return box, pointer
    ox, oy = crop[0], crop[1]
    b = (box[0] - ox, box[1] - oy, box[2] - ox, box[3] - oy)
    p = None if pointer is None else (pointer[0] - ox, pointer[1] - oy)
    return b, p


def record_to_frame(rec: sam.SamRecord, media_size: tuple[int, int], crop: tuple[int, int, int, int] | None,
                    w: int, h: int) -> np.ndarray:
    """A SAM record (on the frame or a crop of it) -> frame-size soft mask (0..1)."""
    mw, mh = media_size
    rw, rh = rec.media_size or media_size
    sx, sy = mw / rw, mh / rh
    ox, oy = (crop[0], crop[1]) if crop is not None else (0, 0)
    x1, y1, x2, y2 = rec.box
    fbox = (x1 * sx + ox, y1 * sy + oy, x2 * sx + ox, y2 * sy + oy)
    return mask_smooth.soft_upsample(rec.raster(), fbox, w, h)


def segment(item: ItemInput, loc: Located, notes: dict[str, Any], save_dir: Path | None = None) -> list[Candidate]:
    assert loc.box is not None
    w, h = item.width, item.height
    crop = crop_for_box(loc.box, w, h, item.pointer)
    frame = load_frame(item, notes)
    if crop is None:
        # the raw file itself when it has the right size (byte-identical to what v1 and the cache saw)
        media: bytes | Path = jpeg_bytes(frame, 95) if "resized_from" in notes else item.raw
        media_size = frame.size
    else:
        cimg = frame.crop(crop)
        media, media_size = jpeg_bytes(cimg, 95), cimg.size
    if save_dir is not None and crop is not None:
        (save_dir / "sam_crop.jpg").write_bytes(media)  # type: ignore[arg-type]
    notes["crop"] = list(crop) if crop else None
    mbox, mptr = to_media(loc.box, item.pointer, crop)
    if hand_at(item.pointer, item.hand_mask, P.V2_HAND_NEAR_FINGER_PX):
        notes["finger_prompts"] = "dropped: the hand is at the fingertip in this frame"
        mptr = None
    mw, mh = media_size
    specs = build_objects(mbox, mptr, mw, mh)
    notes["prompts"] = [{"id": o.id, "source": name, "box": _r(o.box), "positive": [list(map(round, p)) for p in o.positive],
                         "negative": [list(map(round, p)) for p in o.negative]} for name, o in specs]
    by_id = {o.id: name for name, o in specs}
    cands: list[Candidate] = []
    try:
        res = sam.objects_on_image(media, [o for _, o in specs])
        recs = [r for r in res.all_records() if r.mask is not None]
        notes["object_instances"] = len(recs)
        if res.diagnostics:
            notes["object_diagnostics"] = res.diagnostics[:3]
        for r in recs:
            cands.append(Candidate(id=int(r.object_id), source=by_id.get(int(r.object_id), f"obj{r.object_id}"),
                                   grounding=Grounding.sam_box, confidence=r.confidence,
                                   soft=record_to_frame(r, media_size, crop, w, h)))
    except (ApiError, ValueError) as err:
        notes["object_error"] = str(err)[:300]
    if loc.concept:
        try:
            res = sam.concept_on_image(media, loc.concept)
            recs = [r for r in res.all_records() if r.mask is not None]
            notes["concept_instances"] = len(recs)
            for i, r in enumerate(recs):
                cands.append(Candidate(id=100 + i, source="concept", grounding=Grounding.sam_concept,
                                       confidence=r.confidence, soft=record_to_frame(r, media_size, crop, w, h)))
        except (ApiError, ValueError) as err:
            notes["concept_error"] = str(err)[:300]
    for c in cands:
        c.mask = (c.soft >= 0.5).astype(np.uint8) if c.soft is not None else None
        if c.mask is not None and item.hand_mask is not None and c.mask.any():
            c.hand_overlap = float(np.logical_and(c.mask, item.hand_mask).sum()) / float(c.mask.sum())
    return cands


# --------------------------------------------------------------------------- (c) score


def score_candidates(cands: list[Candidate], spark_box: BoxF, extent: Extent, pointer: Point | None,
                     w: int, h: int) -> list[Candidate]:
    """Fill in each candidate's measures, score and rejection reason; return them best first
    (accepted ones before rejected ones)."""
    barea = max(1.0, box_area(spark_box))
    frame_area = float(w * h)
    expected = P.V2_EXPECTED_FILL.get(extent, 0.6)
    finger_scale = P.V2_FINGER_SCALE_FRAC * min(w, h)
    for c in cands:
        m = c.mask
        c.area = int(m.sum()) if m is not None else 0
        if c.area == 0:
            c.rejected = "empty mask"
            continue
        c.bbox = mask_bbox(m)
        c.iou = box_iou(c.bbox, spark_box)
        c.inside = mask_inside(m, spark_box)
        c.fill = c.area / max(1.0, box_area(c.bbox))
        c.solidity = mask_solidity(m)
        c.area_ratio = c.area / barea
        c.frame_frac = c.area / frame_area
        if c.area_ratio > P.V2_MAX_AREA_RATIO:
            c.rejected = f"{c.area_ratio:.1f}x the located box (max {P.V2_MAX_AREA_RATIO})"
        elif c.area_ratio < P.V2_MIN_AREA_RATIO:
            c.rejected = f"a speck ({c.area_ratio:.2f}x the located box)"
        elif c.frame_frac > P.V2_MAX_FRAME_FRAC and extent != "whole_surface":
            c.rejected = f"covers {c.frame_frac:.0%} of the frame"
        elif c.hand_overlap > P.V2_MAX_HAND_OVERLAP:
            c.rejected = f"{c.hand_overlap:.0%} of it is the hand"
        elif c.inside < P.V2_MIN_INSIDE_FRAC:
            c.rejected = f"only {c.inside:.0%} of it inside the located box"
        elif c.solidity < P.V2_MIN_SOLIDITY:
            c.rejected = f"wrong shape (solidity {c.solidity:.2f})"
        elif c.fill < P.V2_MIN_BBOX_FILL:
            c.rejected = f"wrong shape (fills {c.fill:.0%} of its box)"
        terms = [(P.V2_W_IOU, c.iou), (P.V2_W_INSIDE, c.inside),
                 (P.V2_W_CONF, float(c.confidence if c.confidence is not None else 0.5))]
        r = c.area_ratio / expected
        terms.append((P.V2_W_AREA, min(r, 1.0 / r) if r > 0 else 0.0))
        if pointer is not None:
            c.finger_dist = point_to_mask(pointer, m)
            if c.rejected is None and c.finger_dist > P.V2_MAX_FINGER_DIST_FRAC * w:
                c.rejected = f"{c.finger_dist:.0f} px from the fingertip"
            terms.append((P.V2_W_FINGER, max(0.0, 1.0 - c.finger_dist / finger_scale)))
        c.score = sum(wt * v for wt, v in terms) / sum(wt for wt, _ in terms)
        if c.rejected is None and c.score < P.V2_MIN_SCORE:
            c.rejected = f"score {c.score:.2f} < {P.V2_MIN_SCORE}"
    ordered = sorted(cands, key=lambda c: (c.rejected is not None, -c.score))
    kept: list[Candidate] = []
    for c in ordered:
        if c.rejected is not None:
            continue
        dup = next((k for k in kept if mask_iou(k.mask, c.mask) > P.V2_DUP_IOU), None)
        if dup is not None:
            c.rejected = f"duplicate of {dup.source} #{dup.id}"
        else:
            kept.append(c)
    return sorted(cands, key=lambda c: (c.rejected is not None, -c.score))


def spark_box_ok(box: BoxF, extent: Extent, w: int, h: int) -> bool:
    """The Spark box is offered to the check call only if it is not most of the frame."""
    return extent == "whole_surface" or box_area(box) / float(w * h) <= P.V2_MAX_FRAME_FRAC


# --------------------------------------------------------------------------- (d) check

CHECK_SYSTEM = (
    "You check defect outlines for a construction punch list. Each image shows the same photo with ONE "
    "candidate outline in red and its number in the top-left corner. Pick the number whose red outline "
    "tightly covers the described defect itself. Wrong: an outline around the whole fixture, appliance, wall "
    "or object when the defect is only part of it; an outline on a different object; an outline that misses "
    "the defect; an outline on plain wall or surface NEXT TO the object the defect is on (look closely at "
    "small outlines: is the red shape really on the mark, hole or part, or just beside it?). Prefer the "
    "outline that covers all of the defect with the least extra. If no outline is clearly right, answer 0: "
    "showing no outline is better than a wrong one."
)


def check_region(shapes: list[BoxF], pointer: Point | None, w: int, h: int) -> tuple[int, int, int, int]:
    xs = [v for b in shapes for v in (b[0], b[2])]
    ys = [v for b in shapes for v in (b[1], b[3])]
    if pointer is not None:
        xs.append(pointer[0])
        ys.append(pointer[1])
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    # zoom to the candidates: small outlines need a close view to tell "on the plate" from "beside it"
    side_min = P.V2_CHECK_MIN_FRAC * min(w, h)
    cw = max((max(xs) - min(xs)) * P.V2_CHECK_CONTEXT_SCALE, side_min)
    ch = max((max(ys) - min(ys)) * P.V2_CHECK_CONTEXT_SCALE, side_min)
    cw, ch = min(cw, w), min(ch, h)
    x1 = int(round(min(max(0.0, cx - cw / 2), w - cw)))
    y1 = int(round(min(max(0.0, cy - ch / 2), h - ch)))
    return x1, y1, min(w, x1 + int(round(cw))), min(h, y1 + int(round(ch)))


def check_prompt(item: ItemInput, n: int) -> str:
    lines = [f"Punch item: {item.title}"]
    if item.description:
        lines.append(f"Description: {item.description}")
    if item.quote:
        lines.append(f'The inspector said: "{item.quote}"')
    if item.pointer is not None:
        lines.append("The small hollow blue ring marks where the inspector's fingertip pointed.")
    lines.append(f"There are {n} candidate image(s), numbered 1 to {n}, all showing the same close-up. Which "
                 "number's red outline tightly covers the described defect? Answer 0 if none does.")
    return "\n".join(lines)


def check(item: ItemInput, offered: list[Candidate], save_dir: Path | None = None) -> tuple[int, str, str | None]:
    """(pick 0..n, rationale, provider). Candidates must carry `svg` (mask) or `bbox` (spark_box)."""
    base = load_frame(item)
    shapes = [c.smooth_box or c.bbox for c in offered if (c.smooth_box or c.bbox) is not None]
    region = check_region(shapes, item.pointer, item.width, item.height)
    images: list[bytes] = []
    for n, c in enumerate(offered, 1):
        img = draw_candidate(base, n, svg=c.svg, box=c.bbox if c.svg is None else None, pointer=item.pointer,
                             region=region)
        short = min(img.size)
        if short < P.V2_CHECK_MIN_SHORT_PX:  # small crops are upscaled so the model sees the detail
            k = P.V2_CHECK_MIN_SHORT_PX / short
            img = img.resize((round(img.width * k), round(img.height * k)), Image.LANCZOS)
        data = jpeg_bytes(img, llm.JPEG_QUALITY)
        images.append(data)
        if save_dir is not None:
            (save_dir / f"check_{n}.jpg").write_bytes(data)
    result, rec = llm.structured(
        OutlinePick,
        [{"role": "system", "content": CHECK_SYSTEM}, {"role": "user", "content": check_prompt(item, len(offered))}],
        images=images, reasoning_effort="medium", max_tokens=P.V2_CHECK_MAX_TOKENS, stage=STAGE,
    )
    pick = result.pick if 0 <= result.pick <= len(offered) else 0
    return pick, result.rationale[:300], rec.provider


# --------------------------------------------------------------------------- (g) pointer mapping


def map_pointer_homography(src: np.ndarray, dst: np.ndarray, pt: Point,
                           src_mask: np.ndarray | None = None, dst_mask: np.ndarray | None = None) -> Point | None:
    """Carry `pt` from image `src` to image `dst` (same size, BGR or gray) with ORB + RANSAC homography.
    `*_mask` are 0/1 arrays of pixels to ignore (the hand). None when the fit is unreliable."""
    g1 = src if src.ndim == 2 else cv2.cvtColor(src, cv2.COLOR_BGR2GRAY)
    g2 = dst if dst.ndim == 2 else cv2.cvtColor(dst, cv2.COLOR_BGR2GRAY)
    m1 = None if src_mask is None else ((1 - src_mask) * 255).astype(np.uint8)
    m2 = None if dst_mask is None else ((1 - dst_mask) * 255).astype(np.uint8)
    orb = cv2.ORB_create(nfeatures=2000)
    k1, d1 = orb.detectAndCompute(g1, m1)
    k2, d2 = orb.detectAndCompute(g2, m2)
    need = P.V2_HOMOGRAPHY_MIN_INLIERS
    if d1 is None or d2 is None or len(k1) < need or len(k2) < need:
        return None
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(d1, d2, k=2)
    good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < 0.75 * p[1].distance]
    if len(good) < need:
        return None
    a = np.float32([k1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    b = np.float32([k2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    H, inl = cv2.findHomography(a, b, cv2.RANSAC, 4.0)
    if H is None or inl is None or int(inl.sum()) < need:
        return None
    q = cv2.perspectiveTransform(np.float32([[pt]]), H)[0, 0]
    h, w = g2.shape[:2]
    if not (0 <= q[0] < w and 0 <= q[1] < h):
        return None
    if math.hypot(q[0] - pt[0], q[1] - pt[1]) > P.V2_HOMOGRAPHY_MAX_SHIFT_FRAC * w:
        return None
    return float(q[0]), float(q[1])


def resolve_pointer(ev_pointer: Point | None, *, aligned: bool | None, same_frame: bool,
                    pointing_raw: Path | None, evidence_raw: Path, fingertip_display: Point | None,
                    notes: dict[str, Any]) -> Point | None:
    """(g): the fingertip in the evidence frame for grounding, or None (ground as unpointed)."""
    if ev_pointer is None:
        return None
    if same_frame or aligned is not False:
        return ev_pointer  # pick_evidence already carried it over (or it is the pointing frame)
    if pointing_raw is None or fingertip_display is None or not pointing_raw.is_file():
        notes["pointer_map"] = "no pointing frame: grounded as unpointed"
        return None
    src, dst = cv2.imread(str(pointing_raw)), cv2.imread(str(evidence_raw))
    if src is None or dst is None or src.shape != dst.shape:
        notes["pointer_map"] = "pointing frame unreadable: grounded as unpointed"
        return None
    mapped = map_pointer_homography(src, dst, fingertip_display)
    if mapped is None:
        notes["pointer_map"] = "homography failed: grounded as unpointed"
        return None
    notes["pointer_map"] = {"homography": [round(mapped[0], 1), round(mapped[1], 1)]}
    return mapped


# --------------------------------------------------------------------------- one item


def _smooth(c: Candidate, item: ItemInput, spark_box: BoxF) -> None:
    if c.soft is None:
        return
    svg, m = mask_smooth.smooth_svg(c.soft, fingertip=item.pointer, box=spark_box)
    c.svg = svg or None
    c.smooth_box = mask_bbox(m) if svg else None


def ground_item(item: ItemInput, save_dir: Path | None = None) -> V2Result:
    """Run (a)-(f) for one item. Never raises on model/API failures or the cost cap: the item gets no
    outline and the reason is in the row."""
    from punchwalk.clients.cost import CostCapReached

    try:
        return _ground_item(item, save_dir)
    except CostCapReached as err:
        nothing = Grounding.pointed_only if item.pointer is not None else Grounding.none
        row = {"number": item.number, "pointed": item.pointer is not None, "cost_cap": str(err)[:300],
               "result": {"grounding": nothing.value, "source": None, "reason": "cost cap reached",
                          "defect_box": None, "defect_svg": None}}
        return V2Result(nothing, None, None, None, row)


def _ground_item(item: ItemInput, save_dir: Path | None = None) -> V2Result:
    nothing = Grounding.pointed_only if item.pointer is not None else Grounding.none
    row: dict[str, Any] = {"number": item.number, "pointed": item.pointer is not None,
                           "pointer": _r(item.pointer) if item.pointer else None}
    try:
        loc = locate(item, save_dir)
    except (ApiError, llm.StructuredOutputError) as err:
        row["locate_error"] = str(err)[:300]
        row["result"] = {"grounding": nothing.value, "source": None, "defect_box": None, "defect_svg": None}
        return V2Result(nothing, None, None, None, row)
    row["locate"] = loc.as_row()
    if not loc.visible or loc.box is None:
        row["result"] = {"grounding": nothing.value, "source": None, "reason": "not visible (10.4 picks a frame)",
                         "defect_box": None, "defect_svg": None}
        return V2Result(nothing, None, None, loc.concept, row)

    notes: dict[str, Any] = {}
    cands = segment(item, loc, notes, save_dir)
    scored = score_candidates(cands, loc.box, loc.extent, item.pointer, item.width, item.height)
    row["segment"] = notes
    accepted = [c for c in scored if c.rejected is None]
    offered: list[Candidate] = []
    for c in accepted:
        if len(offered) >= P.V2_CHECK_TOP:
            break
        _smooth(c, item, loc.box)
        if c.svg:
            offered.append(c)
    if spark_box_ok(loc.box, loc.extent, item.width, item.height):
        offered.append(Candidate(id=0, source="spark_box", grounding=Grounding.spark_box, confidence=None,
                                 bbox=loc.box, iou=1.0, area_ratio=1.0))
    row["candidates"] = [c.as_row() for c in scored]
    row["offered"] = [{"n": n, "source": c.source, "id": c.id} for n, c in enumerate(offered, 1)]
    if not offered:
        row["result"] = {"grounding": nothing.value, "source": None, "reason": "no candidate passed the score",
                         "defect_box": None, "defect_svg": None}
        return V2Result(nothing, None, None, loc.concept, row)
    try:
        pick, why, provider = check(item, offered, save_dir)
    except (ApiError, llm.StructuredOutputError) as err:
        row["check_error"] = str(err)[:300]
        row["result"] = {"grounding": nothing.value, "source": None, "reason": "check call failed",
                         "defect_box": None, "defect_svg": None}
        return V2Result(nothing, None, None, loc.concept, row)
    row["check"] = {"pick": pick, "rationale": why, "provider": provider}
    if pick == 0:
        row["result"] = {"grounding": nothing.value, "source": None, "reason": "check picked none",
                         "defect_box": None, "defect_svg": None}
        return V2Result(nothing, None, None, loc.concept, row)
    chosen = offered[pick - 1]
    if chosen.source == "spark_box":
        svg, box, grounding = mask_smooth.rounded_rect_path(loc.box), loc.box, Grounding.spark_box
    else:
        svg, box, grounding = chosen.svg, chosen.smooth_box or chosen.bbox, chosen.grounding
    row["result"] = {"grounding": grounding.value, "source": chosen.source, "id": chosen.id,
                     "defect_box": _r(box), "defect_svg": svg}
    return V2Result(grounding, svg, box, loc.concept, row)


def save_walk_log(walk_dir: Path, rows: dict[str, Any], merge: bool = False) -> Path:
    """Write stages/ground_v2.json (merge=True: add/replace these rows, keep the others)."""
    from punchwalk.pipeline.context import STAGES_DIR, write_json

    path = Path(walk_dir) / STAGES_DIR / OUTPUT_NAME
    old: dict[str, Any] = {}
    if merge and path.is_file():
        try:
            old = json.loads(path.read_text()).get("items", {})
        except (OSError, json.JSONDecodeError):
            old = {}
    return write_json(path, {"version": 1, "items": {**old, **rows}})


# --------------------------------------------------------------------------- dry run CLI


def clear_jpgs(folder: Path) -> None:
    """Start a dry-run folder clean (old candidate images from an earlier run would mislead)."""
    folder.mkdir(parents=True, exist_ok=True)
    for f in folder.rglob("*.jpg"):
        f.unlink()


def _db_items(db: Path, project: str, numbers: list[int]) -> list[dict[str, Any]]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    marks = ",".join("?" * len(numbers))
    rows = con.execute(
        f"select id, number, origin_walk_id, point_id, title, description, quote, evidence from items "
        f"where project_id = ? and number in ({marks}) order by number", [project, *numbers]).fetchall()
    con.close()
    return [dict(r) for r in rows]


def _stage_json(walk_dir: Path, name: str) -> dict[str, Any]:
    p = walk_dir / "stages" / f"{name}.json"
    return json.loads(p.read_text()) if p.is_file() else {}


def item_input_from_row(row: dict[str, Any], root: Path, notes: dict[str, Any]) -> ItemInput:
    ev = json.loads(row["evidence"])
    wdir = root / "walks" / row["origin_walk_id"]
    pointer = (ev["pointer"]["x"], ev["pointer"]["y"]) if ev.get("pointer") else None
    pick = (_stage_json(wdir, "pick_evidence").get("items") or {}).get(row["id"]) or {}
    events = {e["id"]: e for e in _stage_json(wdir, "find_points").get("events") or []}
    event = events.get(row.get("point_id") or "")
    same = bool(event) and pick.get("frame") is not None and pick.get("frame") == event.get("frame")
    pointer = resolve_pointer(
        pointer, aligned=pick.get("aligned"), same_frame=same,
        pointing_raw=(root / pick["pointing_raw"]) if pick.get("pointing_raw") else None,
        evidence_raw=root / ev["raw"],
        fingertip_display=tuple(event["fingertip_display"]) if event and event.get("fingertip_display") else None,
        notes=notes)
    return ItemInput(item_id=row["id"], raw=root / ev["raw"], width=int(ev["width"]), height=int(ev["height"]),
                     title=row["title"], description=row.get("description") or "", quote=row.get("quote"),
                     pointer=pointer, number=row["number"],
                     hand_mask=hand_mask_at(wdir, float(ev["t"]), int(ev["width"]), int(ev["height"])))


def dry_run(data_dir: Path, project: str, numbers: list[int], out: Path) -> dict[str, Any]:
    from punchwalk.render.evidence import draw_evidence

    rows = _db_items(data_dir / "punchwalk.sqlite3", project, numbers)
    by_walk: dict[str, dict[str, Any]] = {}
    for r in rows:
        notes: dict[str, Any] = {}
        item = item_input_from_row(r, data_dir, notes)
        idir = out / f"{r['number']:03d}_{r['id']}"
        clear_jpgs(idir)
        res = ground_item(item, idir)
        res.row.update(notes)
        with Image.open(item.raw) as im:
            draw_evidence(im, defect_svg=res.defect_svg, pointer=item.pointer).convert("RGB").save(
                idir / "evidence_v2.jpg", quality=85)
        by_walk.setdefault(r["origin_walk_id"], {})[r["id"]] = res.row
        _print_row(r, res)
    for walk, items in by_walk.items():
        p = out / walk / OUTPUT_NAME
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"version": 1, "items": items}, indent=1, default=str))
    return by_walk


def _print_row(r: dict[str, Any], res: V2Result) -> None:
    row = res.row
    print(f"\n#{r['number']:03d} {r['title']}  ({'pointed' if row.get('pointed') else 'unpointed'})")
    if "locate" in row:
        loc = row["locate"]
        print(f"  locate: visible={loc['visible']} box={loc['box']} extent={loc['extent']} concept={loc['concept']!r}")
        print(f"          {loc['rationale']}")
    seg = row.get("segment") or {}
    print(f"  segment: crop={seg.get('crop')} object_instances={seg.get('object_instances')} "
          f"concept_instances={seg.get('concept_instances')} errors={seg.get('object_error') or seg.get('concept_error')}")
    for c in row.get("candidates") or []:
        print(f"   - {c['source']:<10} id={c['id']:<3} conf={c['confidence']} iou={c['iou']} area_ratio={c['area_ratio']} "
              f"frame={c['frame_frac']} finger={c['finger_dist']} score={c['score']} {('REJECTED: ' + c['rejected']) if c['rejected'] else 'ok'}")
    print(f"  offered: {row.get('offered')}")
    print(f"  check: {row.get('check')}")
    print(f"  result: grounding={res.grounding.value} source={row['result'].get('source')} box={_r(res.defect_box)} "
          f"{row['result'].get('reason') or ''}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Grounding v2 dry run on a few items (writes only under --out).")
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    ap.add_argument("--project", default="prj_demo")
    ap.add_argument("--items", required=True, help="item numbers, e.g. 3,11,4")
    ap.add_argument("--out", type=Path, default=Path(".verify/v2"))
    a = ap.parse_args(argv)
    os.environ["DATA_DIR"] = str(a.data_dir.resolve())
    from punchwalk.config import reset_settings

    reset_settings()
    numbers = [int(x) for x in a.items.split(",") if x.strip()]
    dry_run(a.data_dir.resolve(), a.project, numbers, a.out.resolve())
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main())
