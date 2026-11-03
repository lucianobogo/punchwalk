"""Evidence renders (PLAN 2.2, DESIGN.md "Item detail"). No burned-in text: the UI draws the number
tab and captions.

- `render_evidence`: the full frame with the defect outlined in Redline (#D93B30, 30% fill, white
  halo underneath) and a small Tape-blue (#2F86E0) dot with a white halo where the finger pointed.
  With no defect outline it is just the dot (this is also the "fingertip marked" copy for the model).
- `crop_box_for` + `render_crop`: the 4:3 card crop centered on the pointer + defect.
- `render_pointing`: the pointing frame with the hand outlined in Tape blue.

Shapes are SVG path strings (`M x y L x y ... Z`) in the image's own pixels.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PIL import Image, ImageDraw

from punchwalk.pipeline import params as P
from punchwalk.pipeline.frames import svg_polygons

REDLINE = (0xD9, 0x3B, 0x30)
TAPE = (0x2F, 0x86, 0xE0)
WHITE = (255, 255, 255)
FILL_ALPHA = 0.30
HAND_FILL_ALPHA = 0.15
JPEG_QUALITY = 85
CROP_ASPECT, CROP_MIN_FRAC, CROP_MARGIN = P.CROP_ASPECT, P.CROP_MIN_FRAC, P.CROP_MARGIN


def line_width(w: int, h: int) -> int:
    return max(3, round(min(w, h) / 220))


def dot_radius(w: int, h: int) -> int:
    lo, hi = P.DOT_RADIUS_PX
    return int(min(hi, max(lo, round(min(w, h) / P.DOT_RADIUS_DIV))))


def _polys(svg: str | None) -> list[list[tuple[float, float]]]:
    return [[(float(x), float(y)) for x, y in p] for p in svg_polygons(svg or "")]


def _outline(layer: Image.Image, polys, color, width: int, fill_alpha: float) -> None:
    d = ImageDraw.Draw(layer)
    for pts in polys:
        d.polygon(pts, fill=(*color, int(255 * fill_alpha)))
    for pts in polys:  # white halo under the colored line
        d.line([*pts, pts[0]], fill=(*WHITE, 230), width=width + 4, joint="curve")
    for pts in polys:
        d.line([*pts, pts[0]], fill=(*color, 255), width=width, joint="curve")


def _dot(layer: Image.Image, x: float, y: float, r: int) -> None:
    d = ImageDraw.Draw(layer)
    halo = r + max(3, r // 3)
    d.ellipse([x - halo, y - halo, x + halo, y + halo], fill=(*WHITE, 255))
    d.ellipse([x - r, y - r, x + r, y + r], fill=(*TAPE, 255))


def _save(img: Image.Image, out: Path) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, quality=JPEG_QUALITY)
    return out


def draw_evidence(base: Image.Image, *, defect_svg: str | None = None,
                  pointer: tuple[float, float] | None = None) -> Image.Image:
    base = base.convert("RGBA")
    w, h = base.size
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    polys = _polys(defect_svg)
    if polys:
        _outline(layer, polys, REDLINE, line_width(w, h), FILL_ALPHA)
    if pointer is not None:
        _dot(layer, pointer[0], pointer[1], dot_radius(w, h))
    return Image.alpha_composite(base, layer)


def render_evidence(raw: Path, out: Path, *, defect_svg: str | None = None,
                    pointer: tuple[float, float] | None = None) -> Path:
    with Image.open(raw) as im:
        return _save(draw_evidence(im, defect_svg=defect_svg, pointer=pointer), out)


def render_pointing(raw: Path, out: Path, *, hand_svg: str | None) -> Path:
    with Image.open(raw) as im:
        base = im.convert("RGBA")
    w, h = base.size
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    polys = _polys(hand_svg)
    if polys:
        _outline(layer, polys, TAPE, line_width(w, h), HAND_FILL_ALPHA)
    return _save(Image.alpha_composite(base, layer), out)


def wide_crop_box(image: Path) -> tuple[int, int, int, int]:
    """Nothing located (voice-only, no outline): the widest 4:3 crop (full width, or full height in a
    landscape frame), placed over the band with the most texture (Laplacian energy) so the described
    defect is most likely in it; the frame center when the frame is flat."""
    import cv2
    import numpy as np

    img = cv2.imread(str(image), cv2.IMREAD_GRAYSCALE)
    h, w = img.shape[:2]
    cw, ch = w, int(round(w / CROP_ASPECT))
    if ch > h:
        ch, cw = h, int(round(h * CROP_ASPECT))
    energy = np.abs(cv2.Laplacian(img, cv2.CV_64F))
    if energy.mean() < 1e-6:
        x1, y1 = (w - cw) // 2, (h - ch) // 2
        return x1, y1, x1 + cw, y1 + ch
    rows = np.convolve(energy.sum(axis=1), np.ones(ch), mode="valid")  # sum over each ch-tall band
    cols = np.convolve(energy.sum(axis=0), np.ones(cw), mode="valid")
    y1 = int(np.argmax(rows)) if ch < h else 0
    x1 = int(np.argmax(cols)) if cw < w else 0
    return x1, y1, x1 + cw, y1 + ch


def crop_box_for(
    w: int, h: int, *, pointer: tuple[float, float] | None = None,
    defect_box: Sequence[float] | None = None,
) -> tuple[int, int, int, int]:
    """4:3 crop [x1, y1, x2, y2) centered on the pointer + defect extent, inside the frame."""
    xs: list[float] = []
    ys: list[float] = []
    if pointer is not None:
        xs.append(pointer[0])
        ys.append(pointer[1])
    if defect_box is not None:
        xs += [defect_box[0], defect_box[2]]
        ys += [defect_box[1], defect_box[3]]
    if not xs:
        xs, ys = [w / 2], [h / 2]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    ext_w, ext_h = (max(xs) - min(xs)) * CROP_MARGIN, (max(ys) - min(ys)) * CROP_MARGIN
    ch = max(CROP_MIN_FRAC * min(w, h), ext_h, ext_w / CROP_ASPECT)
    cw = ch * CROP_ASPECT
    if cw > w:
        cw, ch = w, w / CROP_ASPECT
    if ch > h:
        ch, cw = h, h * CROP_ASPECT
    cw, ch = int(round(cw)), int(round(ch))
    x1 = int(round(min(max(0.0, cx - cw / 2), w - cw)))
    y1 = int(round(min(max(0.0, cy - ch / 2), h - ch)))
    return x1, y1, x1 + cw, y1 + ch


def render_crop(evidence: Path, out: Path, box: tuple[int, int, int, int]) -> Path:
    with Image.open(evidence) as im:
        return _save(im.crop(box), out)
