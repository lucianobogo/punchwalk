import numpy as np
import pytest
from PIL import Image

from punchwalk.clients import sam
from punchwalk.render import masks
from tests.conftest import FIXTURES, PROBE

CLIENTS = FIXTURES / "clients"


def _tape():
    return sam.parse_output((CLIENTS / "sam_image_tape.txt").read_text(), "image").frames[0][0]


def _points(path: str) -> np.ndarray:
    nums = [float(t) for t in path.replace("M", " ").replace("L", " ").replace("Z", " ").split()]
    return np.array(nums).reshape(-1, 2)


def test_mask_is_box_local():
    r = _tape()
    ev = masks.infer_mask_space(r)
    assert ev["looks"] == "box" and ev["scale_x"] == 1.0 and ev["scale_y"] == 1.0


def test_svg_sits_inside_the_box_in_frame_pixels():
    r = _tape()
    pts = _points(masks.mask_to_frame_svg(r, 720, 1280))
    x1, y1, x2, y2 = r.box
    assert pts[:, 0].min() >= x1 - 1 and pts[:, 0].max() <= x2 + 1
    assert pts[:, 1].min() >= y1 - 1 and pts[:, 1].max() <= y2 + 1
    assert 4 <= len(pts) <= 60  # simplified


def test_svg_scales_to_a_larger_display_frame():
    r = _tape()
    small = _points(masks.mask_to_frame_svg(r, 720, 1280))
    big = _points(masks.mask_to_frame_svg(r, 1080, 1920))
    assert big[:, 0].min() == pytest.approx(small[:, 0].min() * 1.5, abs=2)
    assert big[:, 1].max() == pytest.approx(small[:, 1].max() * 1.5, abs=2)


def test_frame_raster_area_matches_mask():
    r = _tape()
    full = masks.mask_to_frame_raster(r, 720, 1280)
    assert full.shape == (1280, 720)
    assert abs(int(full.sum()) - int(r.raster().sum())) <= 0.02 * r.raster().sum()
    ys, xs = np.nonzero(full)
    assert xs.min() >= 362 and xs.max() < 496 and ys.min() >= 923 and ys.max() < 981


def test_stretch_rule_on_a_downscaled_mask():
    """If SAM ever returns a mask smaller than its box, it is stretched over the box."""
    raster = np.zeros((10, 20), dtype=np.uint8)
    raster[:, 10:] = 1  # right half
    r = sam.SamRecord(object_id=0, frame_index=0, box=(100, 200, 140, 220), confidence=1.0,
                      media_size=(400, 400), _raster=raster)
    full = masks.mask_to_frame_raster(r, 400, 400)
    ys, xs = np.nonzero(full)
    assert xs.min() == 120 and xs.max() == 139 and ys.min() == 200 and ys.max() == 219


def test_render_overlay(tmp_path):
    out = masks.render_overlay(PROBE / "media" / "frame.jpg", [_tape()], tmp_path / "o.png")
    im = np.asarray(Image.open(out).convert("RGB")).astype(int)
    # the tape centre is tinted toward the outline blue, far from the wall
    cx, cy = 429, 952
    assert im[cy, cx, 2] > im[cy, cx, 0]
    assert Image.open(out).size == (720, 1280)
