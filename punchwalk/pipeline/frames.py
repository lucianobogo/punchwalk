"""Frame access and small image helpers shared by the pointing and evidence stages.

- `read_frames` decodes a video once, sequentially, and keeps only the requested frame indices
  (SAM frame `i` of `sam.mp4` is the `i`-th decoded frame: it is CFR 10 fps).
- Outlines are stored as SVG path strings (`M x y L x y ... Z`, one subpath per region, as written
  by `punchwalk.render.masks.mask_to_frame_svg`); `rasterize_svg` turns them back into masks.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

import cv2
import numpy as np

from punchwalk.pipeline.media import MediaError

_NUM = r"-?\d+(?:\.\d+)?"
_SUBPATH_RE = re.compile(r"M[^M]*")
_POINT_RE = re.compile(rf"({_NUM})[ ,]+({_NUM})")


def read_frames(video: Path, indices: Iterable[int]) -> dict[int, np.ndarray]:
    """BGR frames for `indices` (decoded in one sequential pass). Missing indices are skipped."""
    wanted = sorted({int(i) for i in indices if int(i) >= 0})
    if not wanted:
        return {}
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise MediaError(f"OpenCV could not open {video}")
    out: dict[int, np.ndarray] = {}
    try:
        targets = iter(wanted)
        target = next(targets)
        idx = 0
        while True:
            if not cap.grab():
                break
            if idx == target:
                ok, frame = cap.retrieve()
                if ok:
                    out[idx] = frame
                try:
                    target = next(targets)
                except StopIteration:
                    break
            idx += 1
    finally:
        cap.release()
    return out


def laplacian_variance(img: np.ndarray) -> float:
    """Sharpness: variance of the Laplacian of the grayscale image (higher = sharper)."""
    if img.size == 0:
        return 0.0
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def masked_laplacian_variance(img: np.ndarray, exclude: np.ndarray | None, pad_px: int = 5) -> float:
    """Laplacian variance over the pixels outside `exclude` (grown by `pad_px`), so the sharp edges
    of a hand in front of the wall do not count as a sharp photo of the wall."""
    if exclude is None or not exclude.any():
        return laplacian_variance(img)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    grown = cv2.dilate(exclude.astype(np.uint8), np.ones((2 * pad_px + 1, 2 * pad_px + 1), np.uint8))
    keep = grown == 0
    return float(lap[keep].var()) if keep.sum() > 16 else 0.0


def square_around(x: float, y: float, side: float, w: int, h: int) -> tuple[int, int, int, int]:
    """Integer square [x1, x2) x [y1, y2) of `side` px centered on (x, y), shifted to stay inside."""
    side = max(2, min(int(round(side)), w, h))
    x1 = int(round(x - side / 2))
    y1 = int(round(y - side / 2))
    x1 = min(max(0, x1), w - side)
    y1 = min(max(0, y1), h - side)
    return x1, y1, x1 + side, y1 + side


def crop(img: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    x1, y1, x2, y2 = box
    return img[y1:y2, x1:x2]


def svg_polygons(d: str) -> list[np.ndarray]:
    """Polygons (N x 2 float arrays) of an M/L/Z SVG path."""
    polys = []
    for sub in _SUBPATH_RE.findall(d or ""):
        pts = [(float(a), float(b)) for a, b in _POINT_RE.findall(sub)]
        if len(pts) >= 3:
            polys.append(np.array(pts, dtype=np.float64))
    return polys


def rasterize_svg(d: str, w: int, h: int, *, scale: tuple[float, float] = (1.0, 1.0)) -> np.ndarray:
    """(h, w) uint8 0/1 mask of the path's regions (coordinates multiplied by `scale` first)."""
    mask = np.zeros((h, w), dtype=np.uint8)
    polys = [np.round(p * np.array(scale)).astype(np.int32) for p in svg_polygons(d)]
    if polys:
        cv2.fillPoly(mask, polys, 1)
    return mask


def scale_svg_path(d: str, sx: float, sy: float) -> str:
    """The same M/L/Z path with x multiplied by `sx` and y by `sy` (one decimal)."""
    parts = []
    for poly in svg_polygons(d):
        pts = poly * np.array([sx, sy])
        rest = " ".join(f"L{x:.1f} {y:.1f}" for x, y in pts[1:])
        parts.append(f"M{pts[0][0]:.1f} {pts[0][1]:.1f} {rest} Z")
    return " ".join(parts)


def to_jpeg(img: np.ndarray, path: Path, quality: int = 92) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, quality]):
        raise MediaError(f"could not write {path}")
    return path
