"""punchwalk/render/evidence.py: outlines, dot, crops. No text is ever drawn."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from punchwalk.render.evidence import (
    REDLINE,
    TAPE,
    crop_box_for,
    dot_radius,
    draw_evidence,
    render_crop,
    render_evidence,
    render_pointing,
)

W, H = 720, 1280
SQUARE = "M300 600 L360 600 L360 660 L300 660 Z"


def _near(img: Image.Image, rgb, tol=30) -> np.ndarray:
    a = np.asarray(img.convert("RGB")).astype(int)
    return np.abs(a - np.array(rgb)).max(axis=-1) < tol


@pytest.fixture
def raw(tmp_path):
    p = tmp_path / "raw.jpg"
    Image.new("RGB", (W, H), (0xD9, 0xCB, 0xB0)).save(p, quality=95)
    return p


def test_outline_fill_and_dot(raw, tmp_path):
    out = render_evidence(raw, tmp_path / "evidence.jpg", defect_svg=SQUARE, pointer=(380.0, 630.0))
    img = Image.open(out)
    assert img.size == (W, H)
    red = _near(img, REDLINE)
    ys, xs = np.nonzero(red)
    assert 295 <= xs.min() <= 305 and 355 <= xs.max() <= 366  # outline sits on the square's edge
    inside = np.asarray(img.convert("RGB"))[630, 330].astype(int)
    assert inside[0] > inside[2] + 20  # 30% red fill tints the inside
    blue = _near(img, TAPE)
    by, bx = np.nonzero(blue)
    assert abs(bx.mean() - 380) < 3 and abs(by.mean() - 630) < 3
    halo = np.asarray(img.convert("RGB"))[630, 380 + dot_radius(W, H) + 2].astype(int)  # white halo ring
    assert halo.min() > 200
    far = np.asarray(img.convert("RGB"))[100, 100].astype(int)
    assert np.abs(far - np.array([0xD9, 0xCB, 0xB0])).max() < 8  # rest of the frame untouched (no text)


def test_no_shapes_is_the_plain_frame(raw):
    img = draw_evidence(Image.open(raw))
    assert not _near(img, REDLINE).any() and not _near(img, TAPE).any()


def test_pointing_outline_is_blue(raw, tmp_path):
    img = Image.open(render_pointing(raw, tmp_path / "p.jpg", hand_svg=SQUARE))
    assert _near(img, TAPE).sum() > 100 and not _near(img, REDLINE).any()


def test_crop_box_is_4x3_contains_pointer_and_defect_and_stays_inside():
    box = crop_box_for(W, H, pointer=(380, 630), defect_box=(300, 600, 360, 660))
    x1, y1, x2, y2 = box
    assert (x2 - x1) / (y2 - y1) == pytest.approx(4 / 3, abs=0.01)
    assert x1 <= 300 and x2 >= 380 and y1 <= 600 and y2 >= 660
    for args in ({"pointer": (5, 5)}, {"pointer": (W - 2, H - 2)}, {}):
        x1, y1, x2, y2 = crop_box_for(W, H, **args)
        assert 0 <= x1 < x2 <= W and 0 <= y1 < y2 <= H
    big = crop_box_for(W, H, defect_box=(0, 0, W, H))  # huge defect: clamped to the frame width
    assert big[2] - big[0] <= W


def test_render_crop(raw, tmp_path):
    ev = render_evidence(raw, tmp_path / "e.jpg", defect_svg=SQUARE, pointer=(380.0, 630.0))
    box = crop_box_for(W, H, pointer=(380, 630), defect_box=(300, 600, 360, 660))
    img = Image.open(render_crop(ev, tmp_path / "c.jpg", box))
    assert img.size == (box[2] - box[0], box[3] - box[1])
    assert _near(img, REDLINE).any() and _near(img, TAPE).any()
