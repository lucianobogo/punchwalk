"""SAM mask -> frame mapping (one helper, used everywhere).

Mapping rule (confirmed in Phase 0 on fixtures/probe/mask_check.png; see DECISIONS.md):
a SAM mask is a **box-local** raster. Its H x W cells cover exactly the record's box
[x1, x2) x [y1, y2) in source pixels, stretched independently on each axis:

    src_x = x1 + (col + 0.5) * (x2 - x1) / W
    src_y = y1 + (row + 0.5) * (y2 - y1) / H

The mask token's `x=0;y=0` are always zero and carry no offset. Source pixels are then
scaled to the target frame by (frame_w / media_w, frame_h / media_h).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
from PIL import Image, ImageDraw

from punchwalk.clients.sam import SamRecord

MaskSpace = Literal["box", "frame"]
MASK_SPACE: MaskSpace = "box"


def _scale(record: SamRecord, frame_w: int, frame_h: int) -> tuple[float, float]:
    mw, mh = record.media_size or (frame_w, frame_h)
    return frame_w / mw, frame_h / mh


def frame_box(record: SamRecord, frame_w: int, frame_h: int) -> tuple[float, float, float, float]:
    """The record's box in target-frame pixels (x2/y2 exclusive)."""
    sx, sy = _scale(record, frame_w, frame_h)
    x1, y1, x2, y2 = record.box
    return x1 * sx, y1 * sy, x2 * sx, y2 * sy


def mask_to_frame_raster(record: SamRecord, frame_w: int, frame_h: int) -> np.ndarray:
    """Full-frame (frame_h, frame_w) uint8 0/1 mask for the record."""
    out = np.zeros((frame_h, frame_w), dtype=np.uint8)
    local, (ox, oy) = _box_local_raster(record, frame_w, frame_h)
    if local.size == 0:
        return out
    h, w = local.shape
    x0, y0 = max(ox, 0), max(oy, 0)
    x1, y1 = min(ox + w, frame_w), min(oy + h, frame_h)
    if x1 > x0 and y1 > y0:
        out[y0:y1, x0:x1] = local[y0 - oy : y1 - oy, x0 - ox : x1 - ox]
    return out


def _box_local_raster(record: SamRecord, frame_w: int, frame_h: int) -> tuple[np.ndarray, tuple[int, int]]:
    """Mask resampled to the box's size in target-frame px, plus its integer top-left offset."""
    raster = record.raster()
    if raster.size == 0:
        return raster, (0, 0)
    if MASK_SPACE == "frame":  # kept for completeness; Phase 0 found masks are box-local
        full = cv2.resize(raster.astype(np.float32), (frame_w, frame_h), interpolation=cv2.INTER_LINEAR)
        return (full >= 0.5).astype(np.uint8), (0, 0)
    bx1, by1, bx2, by2 = frame_box(record, frame_w, frame_h)
    ox, oy = int(np.floor(bx1)), int(np.floor(by1))
    tw, th = max(1, int(np.ceil(bx2)) - ox), max(1, int(np.ceil(by2)) - oy)
    local = cv2.resize(raster.astype(np.float32), (tw, th), interpolation=cv2.INTER_LINEAR)
    return (local >= 0.5).astype(np.uint8), (ox, oy)


def _contours(record: SamRecord, frame_w: int, frame_h: int, epsilon_px: float) -> list[np.ndarray]:
    local, (ox, oy) = _box_local_raster(record, frame_w, frame_h)
    if local.size == 0 or not local.any():
        return []
    padded = np.pad(local, 1)  # so contours touching the box edge close properly
    found, _ = cv2.findContours(padded, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    polys = []
    for c in found:
        if cv2.contourArea(c) < 4:
            continue
        approx = cv2.approxPolyDP(c, epsilon_px, True).reshape(-1, 2).astype(np.float64)
        if len(approx) < 3:
            continue
        approx[:, 0] += ox - 1
        approx[:, 1] += oy - 1
        approx[:, 0] = approx[:, 0].clip(0, frame_w)
        approx[:, 1] = approx[:, 1].clip(0, frame_h)
        polys.append(approx)
    return polys


def mask_polygons(record: SamRecord, frame_w: int, frame_h: int, epsilon_px: float = 1.0) -> list[list[tuple[float, float]]]:
    return [[(float(x), float(y)) for x, y in p] for p in _contours(record, frame_w, frame_h, epsilon_px)]


def mask_to_frame_svg(record: SamRecord, frame_w: int, frame_h: int, epsilon_px: float = 1.0) -> str:
    """Simplified SVG path (M/L/Z, one subpath per region) in target-frame pixel coordinates.

    `frame_w`/`frame_h` is the frame you draw on; the record's own media size (the frame
    SAM saw) is taken from `record.media_size`, so a SAM result on the 720p `sam.mp4`
    maps straight onto a 1080p `display.mp4` frame.
    """
    parts = []
    for poly in _contours(record, frame_w, frame_h, epsilon_px):
        pts = " ".join(f"L{x:.1f} {y:.1f}" for x, y in poly[1:])
        parts.append(f"M{poly[0][0]:.1f} {poly[0][1]:.1f} {pts} Z")
    return " ".join(parts)


def infer_mask_space(record: SamRecord) -> dict:
    """Evidence for the mapping: compare the mask raster's aspect with the box's and the frame's."""
    mw, mh = record.mask_size or (0, 0)
    fw, fh = record.media_size or (0, 0)
    bw, bh = record.box_w, record.box_h
    box_aspect = bw / bh if bh else 0
    frame_aspect = fw / fh if fh else 0
    mask_aspect = mw / mh if mh else 0
    return {
        "mask_wh": [mw, mh],
        "box_wh": [round(bw, 1), round(bh, 1)],
        "frame_wh": [fw, fh],
        "mask_aspect": round(mask_aspect, 3),
        "box_aspect": round(box_aspect, 3),
        "frame_aspect": round(frame_aspect, 3),
        "scale_x": round(bw / mw, 4) if mw else None,
        "scale_y": round(bh / mh, 4) if mh else None,
        "looks": "box" if abs(mask_aspect - box_aspect) <= abs(mask_aspect - frame_aspect) else "frame",
    }


def _hex(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def render_overlay(
    frame_path: str | Path,
    records: Iterable[SamRecord],
    out_path: str | Path,
    tape_color: str = "#2F86E0",
    *,
    fill_alpha: float = 0.30,
    draw_box: bool = True,
    extra: Sequence[tuple[Iterable[SamRecord], str]] = (),
) -> Path:
    """Draw each record's mask (filled at `fill_alpha` + outline) and box over the frame."""
    base = Image.open(frame_path).convert("RGB")
    fw, fh = base.size
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    width = max(2, round(min(fw, fh) / 300))
    for recs, color in [(records, tape_color), *extra]:
        rgb = _hex(color)
        for r in recs:
            for poly in _contours(r, fw, fh, 1.0):
                pts = [tuple(p) for p in poly]
                draw.polygon(pts, fill=(*rgb, int(255 * fill_alpha)))
                draw.line([*pts, pts[0]], fill=(*rgb, 255), width=width, joint="curve")
            if draw_box:
                x1, y1, x2, y2 = frame_box(r, fw, fh)
                draw.rectangle([x1, y1, x2 - 1, y2 - 1], outline=(255, 214, 0, 255), width=max(1, width // 2))
    out = Image.alpha_composite(base.convert("RGBA"), layer).convert("RGB")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.save(out_path, quality=92)
    return out_path
