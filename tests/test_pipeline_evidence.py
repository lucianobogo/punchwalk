"""pick_evidence (PLAN 1.6, pointing): frame choice, pointer alignment, helpers. Numpy frames, no media."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from punchwalk.pipeline import params as P
from punchwalk.pipeline.evidence import (
    align_point,
    choose_pointed_frame,
    choose_sharpest,
    crop_box_4x3,
    hand_cover,
    sample_frames,
)
from punchwalk.pipeline.frames import masked_laplacian_variance, rasterize_svg, scale_svg_path

W, H = 400, 600


def wall(seed=0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    tex = cv2.GaussianBlur((rng.random((H, W)) * 255).astype(np.uint8), (0, 0), 2.5)
    return cv2.cvtColor(tex, cv2.COLOR_GRAY2BGR)


def with_hand(img: np.ndarray, x1: int, y1: int) -> tuple[np.ndarray, np.ndarray]:
    """Paint a hand-colored block from (x1, y1) to the right edge; return (image, mask)."""
    out = img.copy()
    mask = np.zeros((H, W), np.uint8)
    mask[y1:y1 + 60, x1:W] = 1
    out[mask.astype(bool)] = (122, 160, 214)
    cv2.rectangle(out, (x1, y1), (W - 1, y1 + 59), (40, 60, 90), 2)  # sharp hand edges
    return out, mask


def blurred(img: np.ndarray) -> np.ndarray:
    return cv2.GaussianBlur(img, (0, 0), 4)


TIP = (150.0, 300.0)


def test_picks_frame_where_hand_does_not_cover_the_fingertip():
    base = wall()
    frames, masks = {}, {}
    for f in range(10):  # hold 0..5 covers the tip; 6..9 the hand is gone
        if f <= 5:
            frames[f], masks[f] = with_hand(base, int(TIP[0]) - 5, int(TIP[1]) - 30)
        else:
            frames[f] = base.copy()
    frames[7] = blurred(base)  # one withdrawn frame is blurry
    c = choose_pointed_frame(frames, masks, pointing_frame=3, fingertip=TIP, hold_last=5)
    assert c.frame in (6, 8, 9) and c.hand_clear
    assert c.cover is not None and c.cover < P.EVIDENCE_MAX_HAND_COVER
    assert c.pointer == pytest.approx(TIP, abs=1.5)  # camera still: the pointer stays


def test_hand_edges_do_not_count_as_sharpness():
    base = blurred(wall(1))
    near, mask = with_hand(base, 230, 280)  # hand close to (not over) the tip, adds sharp edges
    frames = {0: near, 1: near, 2: base}
    masks = {0: mask, 1: mask}
    c = choose_pointed_frame(frames, masks, pointing_frame=0, fingertip=TIP, hold_last=1)
    assert c.frame == 2  # withdrawn wins over a frame whose only sharp content is the hand


def test_all_frames_covered_falls_back_to_least_covered():
    base = wall()
    frames, masks = {}, {}
    for f, x in enumerate((140, 145, 120)):
        frames[f], masks[f] = with_hand(base, x, int(TIP[1]) - 30)
    c = choose_pointed_frame(frames, masks, pointing_frame=0, fingertip=TIP, hold_last=2)
    assert not c.hand_clear and c.frame == 1  # x=145 leaves the most of the square uncovered


def test_pointer_follows_camera_motion():
    base = wall(2)
    M = np.float32([[1, 0, 9], [0, 1, -6]])
    moved = cv2.warpAffine(base, M, (W, H), borderMode=cv2.BORDER_REFLECT)
    (x, y), ok = align_point(base, moved, TIP)
    assert ok and x == pytest.approx(TIP[0] + 9, abs=1.5) and y == pytest.approx(TIP[1] - 6, abs=1.5)
    plain = np.full((H, W, 3), 200, np.uint8)  # low texture: phase correlation fallback
    cv2.rectangle(plain, (140, 290), (150, 300), (60, 60, 60), -1)
    cv2.rectangle(plain, (220, 380), (260, 440), (240, 240, 240), -1)
    (x, y), ok = align_point(plain, cv2.warpAffine(plain, M, (W, H), borderMode=cv2.BORDER_REPLICATE), TIP)
    assert ok and x == pytest.approx(TIP[0] + 9, abs=1.5) and y == pytest.approx(TIP[1] - 6, abs=1.5)
    flat = np.full((H, W, 3), 128, np.uint8)
    assert align_point(flat, flat.copy(), TIP) == (TIP, False)  # nothing to align: camera assumed still


def test_voice_only_picks_sharpest_and_samples():
    base = wall(3)
    frames = {10: blurred(base), 20: base, 30: blurred(blurred(base))}
    c = choose_sharpest(frames)
    assert c.frame == 20 and c.pointer is None
    assert sample_frames(1.0, 5.0, 10.0, 0, 99, 5) == [10, 20, 30, 40, 50]
    assert sample_frames(9.0, 12.0, 10.0, 0, 99, 5)[-1] == 99  # clamped to the last frame
    assert sample_frames(13.9, 16.7, 10.0, 140, 209, 5)[0] == 140  # never before its clip part


def test_helpers():
    m = np.zeros((H, W), np.uint8)
    m[:, :200] = 1
    assert hand_cover(m, 100, 300, W, H) == 1.0 and hand_cover(m, 350, 300, W, H) == 0.0
    assert hand_cover(None, 1, 1, W, H) == 0.0
    x1, y1, x2, y2 = crop_box_4x3(10, 10, 720, 1280)
    assert (x1, y1) == (0, 0) and (x2 - x1) / (y2 - y1) == pytest.approx(4 / 3, abs=0.01)
    assert crop_box_4x3(710, 1270, 720, 1280)[2:] == (720, 1280)
    d = "M10 10 L50 10 L50 30 L10 30 Z"
    assert int(rasterize_svg(d, 100, 100).sum()) == 41 * 21  # boundary pixels included
    assert scale_svg_path(d, 2.0, 0.5).startswith("M20.0 5.0 L100.0 5.0")
    assert masked_laplacian_variance(wall(), np.ones((H, W), np.uint8)) == 0.0
