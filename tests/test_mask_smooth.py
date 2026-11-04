"""PLAN 10.3 f: mask smoothing (upsample, blur+threshold, component, holes, DP + Chaikin, SVG)."""

from __future__ import annotations

import cv2
import numpy as np

from punchwalk.pipeline import mask_smooth as ms
from punchwalk.pipeline.frames import rasterize_svg, svg_polygons


def _jagged_disc(w=400, h=300, cx=200, cy=150, r=80, seed=0) -> np.ndarray:
    """A disc at 1/8 resolution, upsampled nearest (stair steps) with speckle on the edge."""
    rng = np.random.default_rng(seed)
    small = np.zeros((h // 8, w // 8), np.uint8)
    cv2.circle(small, (cx // 8, cy // 8), r // 8, 1, -1)
    big = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
    edge = cv2.morphologyEx(big, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)).astype(bool)
    flip = edge & (rng.random(big.shape) < 0.3)
    big[flip] = 1 - big[flip]
    return big


def _raw_points(mask: np.ndarray) -> int:
    found, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    return max(len(c) for c in found)


def test_jagged_mask_becomes_a_smooth_path_with_fewer_points():
    m = _jagged_disc()
    svg, final = ms.smooth_svg(m)
    polys = svg_polygons(svg)
    assert len(polys) == 1
    assert len(polys[0]) < _raw_points(m) / 3  # far fewer points than the raw contour
    # still the same disc: IoU with the ideal circle stays high
    ideal = np.zeros_like(m)
    cv2.circle(ideal, (200, 150), 80, 1, -1)
    drawn = rasterize_svg(svg, m.shape[1], m.shape[0])
    iou = (drawn & ideal).sum() / (drawn | ideal).sum()
    assert iou > 0.9
    # smooth: no sharp turns left (every turn angle under 35 degrees)
    p = polys[0]
    v1, v2 = np.roll(p, -1, 0) - p, p - np.roll(p, 1, 0)
    cos = (v1 * v2).sum(1) / (np.linalg.norm(v1, axis=1) * np.linalg.norm(v2, axis=1) + 1e-9)
    assert np.degrees(np.arccos(np.clip(cos, -1, 1))).max() < 35


def test_component_selection_by_fingertip_then_box_then_largest():
    m = np.zeros((200, 300), np.uint8)
    m[20:60, 20:60] = 1  # small, top-left
    m[100:190, 150:290] = 1  # big, bottom-right
    near_small = ms.select_component(m, fingertip=(70, 40))  # just outside the small one
    assert near_small[40, 40] and not near_small[150, 200]
    inside_big = ms.select_component(m, fingertip=(200, 150))
    assert inside_big[150, 200] and not inside_big[40, 40]
    by_box = ms.select_component(m, box=(10, 10, 70, 70))
    assert by_box[40, 40] and not by_box[150, 200]
    largest = ms.select_component(m)
    assert largest[150, 200] and not largest[40, 40]


def test_small_holes_filled_big_hole_kept():
    m = np.zeros((200, 200), np.uint8)
    cv2.circle(m, (100, 100), 80, 1, -1)
    m[60:64, 60:64] = 0  # small pinhole
    filled = ms.fill_small_holes(m)
    assert filled[61, 61] == 1
    ring = np.zeros((200, 200), np.uint8)
    cv2.circle(ring, (100, 100), 80, 1, -1)
    cv2.circle(ring, (100, 100), 60, 0, -1)  # a burner ring: the middle is a big hole
    kept = ms.fill_small_holes(ring)
    assert kept[100, 100] == 0


def test_soft_upsample_places_box_local_raster():
    local = np.ones((4, 4), np.uint8)
    soft = ms.soft_upsample(local, (100, 50, 140, 90), 300, 200)
    assert soft.shape == (200, 300)
    assert soft[70, 120] == 1.0 and soft[10, 10] == 0.0
    ys, xs = np.nonzero(soft >= 0.5)
    assert xs.min() == 100 and xs.max() == 139 and ys.min() == 50 and ys.max() == 89


def test_chaikin_and_douglas_peucker():
    sq = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], float)
    c = ms.chaikin(sq, 1)
    assert len(c) == 8 and np.allclose(c[0], [2.5, 0])
    line = np.array([[0, 0], [5, 0.1], [10, 0], [10, 10], [0, 10]], float)
    assert len(ms.douglas_peucker(line, 1.0)) == 4


def test_rounded_rect_path_stays_in_box_and_rounds_corners():
    path = ms.rounded_rect_path((100, 50, 300, 150))
    p = svg_polygons(path)[0]
    assert p[:, 0].min() >= 100 - 1e-6 and p[:, 0].max() <= 300 + 1e-6
    assert p[:, 1].min() >= 50 - 1e-6 and p[:, 1].max() <= 150 + 1e-6
    assert not any(np.allclose(q, [100, 50]) for q in p)  # the corner itself is cut


def test_empty_mask_gives_no_path():
    svg, m = ms.smooth_svg(np.zeros((50, 50), np.float32))
    assert svg == "" and not m.any()
