"""Re-ground a project's walks with grounding v2 (PLAN 10.5): frame picker -> v2 outline -> re-check.

    uv run python -m punchwalk.pipeline.reground --data-dir DIR --project prj_demo [--walks W ...]
        [--ignore-pointing]

For every walk of the project (first walks before re-walks), with GROUNDING_V2=1 and REGROUND_ALL=1:
  1. ground_defects (ground.run): unpointed items go through the frame picker first; every item gets a
     v2 outline (or none); items whose locate says "not visible" get the picker too;
  2. verify (verify.run): items whose frame or outline changed are re-checked; items whose frame still
     doesn't show the defect get the picker, a re-ground, a re-check and, when only the words are off,
     a description rewrite (never on protected or re-walk-decided items);
  3. after all walks, the re-walk images of items with a closure are redrawn for display only:
     after.jpg is cut again at the size of the item's new card crop (centered on the re-walk dot), and
     before_after.jpg is the new card crop | that after crop. after_frame.jpg (the re-walk frame + dot)
     is unchanged. Closure decisions are never recomputed; resolve_closures keeps its pinned photo.
Writes stages/ground_v2.json and stages/frame_pick.json per walk, and every model call to model_calls.
Text, status, history and closure decisions are never changed on protected items (REGROUND_ALL rules).

--ignore-pointing (evaluation only): GROUNDING_MODE=voice_only for the run: every item is treated as
unpointed (no fingertip prompts, no ring in the locate image, no dot in the renders, the picker runs for
all), and the pointer is cleared in that copy's evidence. Never run it on data you keep.
Sends nothing (AUTO_SEND=false).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np

log = logging.getLogger(__name__)


def _env(data_dir: Path, ignore_pointing: bool) -> None:
    os.environ["DATA_DIR"] = str(data_dir)
    os.environ["GROUNDING_V2"] = "1"
    os.environ["REGROUND_ALL"] = "1"
    os.environ["AUTO_SEND"] = "false"
    os.environ["GROUNDING_MODE"] = "voice_only" if ignore_pointing else "point+voice"
    from punchwalk.config import reset_settings

    reset_settings()


def dot_position(frame_with_dot: Path, raw: Path, min_diff: int = 60) -> tuple[float, float] | None:
    """Where the blue dot was drawn: the centroid of the pixels that differ between the re-walk frame with
    the dot (after_frame.jpg) and without it (after_raw.jpg). None when they don't differ (no dot)."""
    a, b = cv2.imread(str(frame_with_dot)), cv2.imread(str(raw))
    if a is None or b is None or a.shape != b.shape:
        return None
    diff = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2)
    ys, xs = np.nonzero(diff > min_diff)
    if len(xs) < 12:
        return None
    return float(np.median(xs)), float(np.median(ys))


def rerender_after(root: Path, item) -> dict[str, Any] | None:
    """Display-only redraw of after.jpg + before_after.jpg for one item with a closure (see module doc)."""
    from punchwalk.core.models import Evidence
    from punchwalk.pipeline.closures import after_crop_box
    from punchwalk.render.before_after import render_before_after
    from punchwalk.render.evidence import render_crop

    c = item.closure or {}
    if not item.evidence or not c.get("after_image") or not c.get("after_frame"):
        return None
    ev = Evidence.model_validate(item.evidence)
    after_frame = root / c["after_frame"]
    after_raw = after_frame.with_name("after_raw.jpg")
    if not after_frame.is_file() or not (root / ev.crop).is_file():
        return None
    img = cv2.imread(str(after_frame))
    h, w = img.shape[:2]
    pointer = dot_position(after_frame, after_raw) if after_raw.is_file() else None
    cb = after_crop_box({"width": w, "height": h, "pointer": pointer}, ev)
    after = render_crop(after_frame, root / c["after_image"], cb)
    out = {"after_image": c["after_image"], "crop_box": list(cb), "pointer": pointer}
    if c.get("before_after"):
        render_before_after(root / ev.crop, after, root / c["before_after"])
        out["before_after"] = c["before_after"]
    return out


def reground_project(data_dir: Path, project: str, walks: list[str] | None = None,
                     ignore_pointing: bool = False) -> dict[str, Any]:
    _env(Path(data_dir).resolve(), ignore_pointing)
    from sqlmodel import select

    from punchwalk import db
    from punchwalk.clients import calls
    from punchwalk.core.enums import Stage
    from punchwalk.core.models import Item, Walk
    from punchwalk.pipeline import ground, runner, verify
    from punchwalk.pipeline.context import data_dir as root_dir

    sf = db.session_factory
    with sf() as s:
        q = select(Walk).where(Walk.project_id == project).order_by(Walk.created_at)
        rows = [w for w in s.exec(q).all() if not walks or w.id in walks]
    ordered = sorted(rows, key=lambda w: (w.kind == "rewalk", w.created_at))
    report: dict[str, Any] = {"project": project, "ignore_pointing": ignore_pointing, "walks": {}}
    for walk in ordered:
        with sf() as s:
            own = s.exec(select(Item.id).where(Item.origin_walk_id == walk.id)).first()
        if own is None:  # a re-walk that found no new defects: nothing to ground (its images are redrawn below)
            report["walks"][walk.id] = {"skipped": "no items from this walk"}
            print(f"{walk.id}: no items of its own, skipped")
            continue
        ctx = runner._load_context(walk.id, sf, False)
        buffer = runner._CallBuffer(walk.id)
        calls.add_sink(buffer)
        try:
            with calls.call_context(walk_id=walk.id, stage=Stage.ground_defects.value):
                g = ground.run(ctx)
            with calls.call_context(walk_id=walk.id, stage=Stage.verify.value):
                v = verify.run(ctx)
        finally:
            calls.remove_sink(buffer)
            n = buffer.flush(sf)
        report["walks"][walk.id] = {"ground": g.counts, "verify": v.counts, "model_calls": n}
        print(f"{walk.id}: {g.message}; {v.message} ({n} model calls)")
    root = root_dir()
    redrawn = {}
    with sf() as s:
        items = s.exec(select(Item).where(Item.project_id == project)).all()
    for item in items:
        if walks and item.origin_walk_id not in walks:
            continue
        try:
            out = rerender_after(root, item)
        except (OSError, ValueError, cv2.error) as err:
            log.warning("reground: after images of %s: %s", item.id, err)
            out = None
        if out:
            redrawn[item.id] = out
    report["after_redrawn"] = len(redrawn)
    print(f"re-walk images redrawn for {len(redrawn)} item(s)")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Re-ground a project's walks with grounding v2 (PLAN 10.5).")
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--walks", nargs="*")
    ap.add_argument("--ignore-pointing", action="store_true", help="talking-only evaluation (never on kept data)")
    a = ap.parse_args(argv)
    reground_project(a.data_dir, a.project, a.walks, a.ignore_pointing)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main())
