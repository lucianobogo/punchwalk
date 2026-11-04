"""Contact sheet of every item's evidence frame with its outline (PLAN 10.2 / 10.5).

A verifier rates each tile correct / partial / wrong. Tiles are ordered by item number; the caption
under each is "#003 · <title> · pointed|unpointed" plus the grounding on a second line.

    uv run python scripts/contact_sheet.py --data-dir ./data --project prj_demo --out .verify/sheets/v1.jpg
    uv run python scripts/contact_sheet.py ... --overlay-from stages/ground_v2.json --out .verify/sheets/v2.jpg

Without --overlay-from each tile is the item's evidence.jpg as rendered (outline + dot). With it, the
tile is redrawn from raw.jpg with the outline in that ground_v2.json (`items[id].result.defect_svg`);
the path is a file, or relative to each walk directory (DATA_DIR/walks/<walk>/). Items missing from
the overlay are marked "not in overlay". The DB is opened read-only; nothing under DATA_DIR changes.
Also writes <out>.json: item -> file, pointed flag, grounding.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from punchwalk.render.evidence import draw_evidence  # noqa: E402

CAPTION_H = 64
PAD = 12
BG = (246, 245, 242)
INK = (34, 38, 43)
MUTED = (110, 114, 120)


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def load_items(db: Path, project: str, walks: list[str] | None) -> list[dict[str, Any]]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    sql = "select id, number, origin_walk_id, title, evidence from items where project_id = ?"
    args: list[Any] = [project]
    if walks:
        sql += f" and origin_walk_id in ({','.join('?' * len(walks))})"
        args += walks
    rows = [dict(r) for r in con.execute(sql + " order by number", args).fetchall()]
    con.close()
    return [r for r in rows if r.get("evidence")]


def load_overlay(spec: str | None, data_dir: Path, walk: str, cache: dict[str, dict]) -> dict[str, Any] | None:
    if not spec:
        return None
    p = Path(spec)
    path = p if p.is_absolute() or p.is_file() else data_dir / "walks" / walk / spec
    key = str(path)
    if key not in cache:
        cache[key] = json.loads(path.read_text()).get("items", {}) if path.is_file() else {}
    return cache[key]


def truncate(text: str, font, width: int) -> str:
    d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    if d.textlength(text, font=font) <= width:
        return text
    while text and d.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def tile_image(item: dict[str, Any], data_dir: Path, overlay: dict[str, Any] | None) -> tuple[Image.Image, str, str]:
    """(image, grounding label, source file)."""
    ev = json.loads(item["evidence"])
    pointer = (ev["pointer"]["x"], ev["pointer"]["y"]) if ev.get("pointer") else None
    if overlay is None:
        f = data_dir / ev["image"]
        with Image.open(f) as im:
            return im.convert("RGB"), ev.get("grounding") or "none", str(f)
    row = overlay.get(item["id"])
    f = data_dir / ev["raw"]
    with Image.open(f) as im:
        if row is None:
            with Image.open(data_dir / ev["image"]) as im2:
                return im2.convert("RGB"), "not in overlay", str(data_dir / ev["image"])
        res = row.get("result") or {}
        img = draw_evidence(im, defect_svg=res.get("defect_svg"), pointer=pointer).convert("RGB")
    return img, res.get("grounding") or "none", str(f)


def build_sheet(items: list[dict[str, Any]], data_dir: Path, overlay_spec: str | None, cols: int,
                tile_w: int) -> tuple[Image.Image, list[dict[str, Any]]]:
    cache: dict[str, dict] = {}
    tiles: list[tuple[Image.Image, str, str]] = []
    index: list[dict[str, Any]] = []
    f1, f2 = _font(20), _font(16)
    for it in items:
        overlay = load_overlay(overlay_spec, data_dir, it["origin_walk_id"], cache)
        img, grounding, src = tile_image(it, data_dir, overlay)
        ev = json.loads(it["evidence"])
        pointed = bool(ev.get("pointer"))
        img = img.resize((tile_w, round(img.height * tile_w / img.width)), Image.LANCZOS)
        line1 = truncate(f"#{it['number']:03d} · {it['title']} · {'pointed' if pointed else 'unpointed'}", f1, tile_w)
        tiles.append((img, line1, grounding))
        index.append({"item_id": it["id"], "number": it["number"], "walk": it["origin_walk_id"], "title": it["title"],
                      "file": src, "pointed": pointed, "grounding": grounding})
    if not tiles:
        raise SystemExit("no items with evidence for that project / walks")
    tile_h = max(t[0].height for t in tiles) + CAPTION_H
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tile_w + (cols + 1) * PAD, rows * tile_h + (rows + 1) * PAD), BG)
    d = ImageDraw.Draw(sheet)
    for i, (img, line1, grounding) in enumerate(tiles):
        r, c = divmod(i, cols)
        x, y = PAD + c * (tile_w + PAD), PAD + r * (tile_h + PAD)
        sheet.paste(img, (x, y))
        cy = y + img.height + 8
        d.text((x, cy), line1, fill=INK, font=f1)
        d.text((x, cy + 28), f"grounding: {grounding}", fill=MUTED, font=f2)
    return sheet, index


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Contact sheet of every item's evidence frame + outline.")
    ap.add_argument("--data-dir", type=Path, default=REPO / "data")
    ap.add_argument("--project", default="prj_demo")
    ap.add_argument("--out", type=Path, default=REPO / ".verify/sheets/v1.jpg")
    ap.add_argument("--walks", nargs="*", help="only these walk ids")
    ap.add_argument("--overlay-from", help="ground_v2.json (a file, or a path relative to each walk dir)")
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--tile-width", type=int, default=520)
    a = ap.parse_args(argv)
    data_dir = a.data_dir.resolve()
    items = load_items(data_dir / "punchwalk.sqlite3", a.project, a.walks)
    sheet, index = build_sheet(items, data_dir, a.overlay_from, a.cols, a.tile_width)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(a.out, quality=88)
    a.out.with_suffix(".json").write_text(json.dumps({"project": a.project, "overlay": a.overlay_from,
                                                      "items": index}, indent=1))
    print(f"{a.out} ({len(index)} items) + {a.out.with_suffix('.json').name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
