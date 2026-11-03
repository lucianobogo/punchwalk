"""Before/after render for re-walk closures (PLAN 7.4): two 4:3 panels side by side, no text.

Left: the original evidence crop (defect outline + blue dot). Right: the re-walk crop (blue dot).
Both are fitted to the same 4:3 panel (cover crop), with a thin white divider.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

PANEL_W, PANEL_H = 800, 600  # 4:3
DIVIDER_PX = 6
DIVIDER = (255, 255, 255)
JPEG_QUALITY = 85


def _panel(path: Path) -> Image.Image:
    with Image.open(path) as im:
        return ImageOps.fit(im.convert("RGB"), (PANEL_W, PANEL_H), method=Image.LANCZOS)


def render_before_after(before: Path, after: Path, out: Path) -> Path:
    canvas = Image.new("RGB", (PANEL_W * 2 + DIVIDER_PX, PANEL_H), DIVIDER)
    canvas.paste(_panel(before), (0, 0))
    canvas.paste(_panel(after), (PANEL_W + DIVIDER_PX, 0))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, quality=JPEG_QUALITY)
    return out
