"""Mask smoothing for defect outlines (PLAN 10.3 f).

SAM's one-bit masks are small, box-local rasters: drawn at frame size they show stair steps and
speckle. Before a mask becomes `Evidence.defect_svg`:

1. upsample the box-local raster to the frame as a *soft* mask (bilinear, values 0..1),
2. blur + threshold at 0.5 (removes stair steps and specks),
3. keep one connected component: the one containing (else nearest) the fingertip; with no
   fingertip, the one overlapping the Spark Open box most; else the largest,
4. fill holes smaller than V2_HOLE_FILL_FRAC of the component,
5. trace the outer contour, simplify it (Douglas–Peucker), round it (Chaikin corner cutting),
6. write an M/L/Z SVG path in frame pixels (what render/evidence.py and the web app draw).

Only the outer contour is written: the evidence renderer fills every subpath, so a hole subpath
would be drawn filled anyway.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import cv2
import numpy as np

from punchwalk.pipeline import params as P

Point = tuple[float, float]
BoxT = Sequence[float]  # x1, y1, x2, y2 (frame px, x2/y2 exclusive)


# --------------------------------------------------------------------------- raster steps


def soft_upsample(local: np.ndarray, box: BoxT, frame_w: int, frame_h: int) -> np.ndarray:
    """Box-local 0/1 raster -> (frame_h, frame_w) float32 soft mask in 0..1.

    `box` is where the raster's cells sit in frame pixels (see punchwalk.render.masks)."""
    out = np.zeros((frame_h, frame_w), dtype=np.float32)
    if local.size == 0 or not local.any():
        return out
    x1, y1, x2, y2 = box
    ox, oy = int(math.floor(x1)), int(math.floor(y1))
    tw, th = max(1, int(math.ceil(x2)) - ox), max(1, int(math.ceil(y2)) - oy)
    up = cv2.resize(local.astype(np.float32), (tw, th), interpolation=cv2.INTER_LINEAR)
    fx0, fy0 = max(ox, 0), max(oy, 0)
    fx1, fy1 = min(ox + tw, frame_w), min(oy + th, frame_h)
    if fx1 > fx0 and fy1 > fy0:
        out[fy0:fy1, fx0:fx1] = up[fy0 - oy : fy1 - oy, fx0 - ox : fx1 - ox]
    return out


def blur_threshold(soft: np.ndarray, sigma_px: float | None = None) -> np.ndarray:
    """Gaussian blur, then threshold at 0.5 -> uint8 0/1. Sigma defaults to a share of the mask's size
    (bigger shapes get a softer edge), at least V2_SMOOTH_MIN_SIGMA_PX."""
    if not soft.any():
        return np.zeros(soft.shape, dtype=np.uint8)
    if sigma_px is None:
        area = float((soft >= 0.5).sum())
        sigma_px = max(P.V2_SMOOTH_MIN_SIGMA_PX, P.V2_SMOOTH_SIGMA_FRAC * math.sqrt(max(area, 1.0)))
    k = int(2 * math.ceil(3 * sigma_px) + 1)
    blurred = cv2.GaussianBlur(soft.astype(np.float32), (k, k), sigma_px)
    return (blurred >= 0.5).astype(np.uint8)


def _component_distance(labels: np.ndarray, label: int, pt: Point) -> float:
    ys, xs = np.nonzero(labels == label)
    if not len(xs):
        return math.inf
    return float(np.sqrt(((xs + 0.5 - pt[0]) ** 2 + (ys + 0.5 - pt[1]) ** 2).min()))


def select_component(mask: np.ndarray, *, fingertip: Point | None = None,
                     box: BoxT | None = None) -> np.ndarray:
    """Keep one 8-connected component (see module docstring). Returns uint8 0/1."""
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    if n <= 1:
        return mask.astype(np.uint8)
    ids = list(range(1, n))
    chosen: int | None = None
    if fingertip is not None:
        h, w = mask.shape
        fx, fy = int(min(max(fingertip[0], 0), w - 1)), int(min(max(fingertip[1], 0), h - 1))
        if labels[fy, fx] > 0:
            chosen = int(labels[fy, fx])
        else:
            chosen = min(ids, key=lambda i: (_component_distance(labels, i, fingertip), -stats[i, cv2.CC_STAT_AREA]))
    elif box is not None:
        x1, y1, x2, y2 = (int(round(v)) for v in box)
        sub = labels[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
        overlap = np.bincount(sub.ravel(), minlength=n) if sub.size else np.zeros(n, dtype=np.int64)
        if overlap[1:].max(initial=0) > 0:
            chosen = int(np.argmax(overlap[1:]) + 1)
    if chosen is None:
        chosen = int(np.argmax(stats[1:, cv2.CC_STAT_AREA]) + 1)
    return (labels == chosen).astype(np.uint8)


def fill_small_holes(mask: np.ndarray, max_frac: float | None = None) -> np.ndarray:
    """Fill holes smaller than `max_frac` of the mask's area (a ring-shaped defect keeps a big hole)."""
    max_frac = P.V2_HOLE_FILL_FRAC if max_frac is None else max_frac
    m = mask.astype(np.uint8)
    area = int(m.sum())
    if area == 0:
        return m
    inv = (1 - m).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=4)
    h, w = m.shape
    out = m.copy()
    for i in range(1, n):
        x, y, bw, bh, a = stats[i]
        touches_border = x == 0 or y == 0 or x + bw >= w or y + bh >= h
        if not touches_border and a <= max_frac * area:
            out[labels == i] = 1
    return out


# --------------------------------------------------------------------------- polygon steps


def douglas_peucker(poly: np.ndarray, epsilon: float) -> np.ndarray:
    """Closed-polygon simplification (cv2.approxPolyDP)."""
    if len(poly) < 4:
        return poly.astype(np.float64)
    return cv2.approxPolyDP(poly.reshape(-1, 1, 2).astype(np.float32), epsilon, True).reshape(-1, 2).astype(np.float64)


def chaikin(poly: np.ndarray, iterations: int = 2) -> np.ndarray:
    """Chaikin corner cutting on a closed polygon (each pass: every edge -> its 1/4 and 3/4 points)."""
    pts = poly.astype(np.float64)
    for _ in range(max(0, iterations)):
        if len(pts) < 3:
            break
        nxt = np.roll(pts, -1, axis=0)
        q = 0.75 * pts + 0.25 * nxt
        r = 0.25 * pts + 0.75 * nxt
        pts = np.empty((2 * len(pts), 2), dtype=np.float64)
        pts[0::2], pts[1::2] = q, r
    return pts


def outer_contour(mask: np.ndarray) -> np.ndarray | None:
    """The largest outer contour as an (N, 2) float array (every boundary pixel), or None."""
    padded = np.pad(mask.astype(np.uint8), 1)
    found, _ = cv2.findContours(padded, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not found:
        return None
    c = max(found, key=cv2.contourArea)
    if cv2.contourArea(c) < 4:
        return None
    return c.reshape(-1, 2).astype(np.float64) - 1.0


def polygon_to_path(poly: np.ndarray, w: int | None = None, h: int | None = None) -> str:
    pts = poly.copy()
    if w is not None:
        pts[:, 0] = pts[:, 0].clip(0, w)
    if h is not None:
        pts[:, 1] = pts[:, 1].clip(0, h)
    rest = " ".join(f"L{x:.1f} {y:.1f}" for x, y in pts[1:])
    return f"M{pts[0][0]:.1f} {pts[0][1]:.1f} {rest} Z"


def mask_to_smooth_polygon(mask: np.ndarray, *, epsilon_px: float | None = None,
                           chaikin_iters: int | None = None) -> np.ndarray | None:
    poly = outer_contour(mask)
    if poly is None:
        return None
    eps = P.V2_DP_EPSILON_PX if epsilon_px is None else epsilon_px
    iters = P.V2_CHAIKIN_ITERS if chaikin_iters is None else chaikin_iters
    simple = douglas_peucker(poly, eps)
    if len(simple) < 3:
        return None
    smooth = chaikin(simple, iters)
    # a light second pass drops the near-collinear points Chaikin adds on long straight edges
    final = douglas_peucker(smooth, eps / 4)
    return final if len(final) >= 3 else smooth


# --------------------------------------------------------------------------- one call


def smooth_mask(soft_or_binary: np.ndarray, *, fingertip: Point | None = None, box: BoxT | None = None,
                sigma_px: float | None = None) -> np.ndarray:
    """Steps 2-4 on a frame-size soft (0..1) or binary mask. Returns uint8 0/1."""
    m = blur_threshold(soft_or_binary.astype(np.float32), sigma_px)
    m = select_component(m, fingertip=fingertip, box=box)
    return fill_small_holes(m)


def smooth_svg(soft_or_binary: np.ndarray, *, fingertip: Point | None = None, box: BoxT | None = None,
               sigma_px: float | None = None) -> tuple[str, np.ndarray]:
    """(SVG path, final 0/1 mask). The path is '' when nothing is left."""
    m = smooth_mask(soft_or_binary, fingertip=fingertip, box=box, sigma_px=sigma_px)
    poly = mask_to_smooth_polygon(m)
    if poly is None:
        return "", m
    h, w = m.shape
    return polygon_to_path(poly, w, h), m


def rounded_rect_path(box: BoxT, radius: float | None = None, corner_points: int = 5) -> str:
    """A rounded rectangle as an M/L/Z path (corners approximated by `corner_points` points each)."""
    x1, y1, x2, y2 = (float(v) for v in box)
    bw, bh = max(0.0, x2 - x1), max(0.0, y2 - y1)
    r = min(bw, bh) * P.V2_BOX_CORNER_FRAC if radius is None else radius
    r = max(0.0, min(r, bw / 2, bh / 2))
    centers = [(x2 - r, y1 + r, -90.0), (x2 - r, y2 - r, 0.0), (x1 + r, y2 - r, 90.0), (x1 + r, y1 + r, 180.0)]
    pts: list[tuple[float, float]] = []
    for cx, cy, start in centers:
        for k in range(corner_points):
            a = math.radians(start + 90.0 * k / max(1, corner_points - 1))
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return polygon_to_path(np.array(pts, dtype=np.float64))
