#!/usr/bin/env python
"""Re-render a seeded walk's display media with the fixed HDR→SDR conversion (COLOR-FIX). No model calls.

    PUNCHWALK_CACHE=readonly uv run python scripts/rerender_display.py --data-dir ./data \\
        --walks wlk_demo_walk1 wlk_demo_walk2 wlk_demo_walk3 [--dry-run] [--keep-work]

Before INGEST_VERSION 4, HLG footage went through swscale's relative-colorimetric conversion, which
clipped everything above HLG reference white: display.mp4 and every photo cut from it looked washed out.
This rebuilds, per walk:

1. display.mp4 from the ORIGINAL clips (footage/<Walk.clips filename>, in order) with ingest's own
   normalize + concat code, into a work dir. It must match the existing meta.json exactly (size, fps,
   frame count, duration, clip offsets) and show the same frames (gradient NCC at sample times beats
   the neighbouring frames), or the walk is skipped.
2. poster.jpg (same SAM-frame choice: sam.mp4 is unchanged).
3. Every item photo, re-extracted at the times stored on the Item and re-drawn with the stored geometry
   through the pipeline's own render functions: raw / context_a / context_b / pointing_raw / marked /
   evidence / crop / pointing (Item.evidence + find_points events), and for a re-walk after_raw /
   after_frame / after / before_after (Item.closure, the re-walk frame choice recomputed from sam.mp4 and
   checked against Closure.after_t) plus closures/<mention>/*.jpg.
   The recipe is first replayed on the OLD raw frames and must reproduce the old renders (else skip).

Never touched: sam.mp4, sam_part_*.mp4, audio.wav, meta.json, overlay.*, stages/, DB rows, the cache,
DATA_DIR/record. Replaced files are backed up once (the first run's originals are kept) under
DATA_DIR/_pre_colorfix/<walk_id>/ - outside walks/, so `seed.py --export` doesn't pack the backups.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

WORK_NAME = "_colorfix_work"
BACKUP_NAME = "_pre_colorfix"
NCC_MIN = 0.60  # absolute floor; the real test is "the same frame beats its neighbours" (tone change lowers NCC)
RECIPE_MAX_MAE = 1.0  # replaying the render recipe on the old frames must reproduce the old renders


class RerenderError(RuntimeError):
    """This walk can't be re-rendered safely; nothing of it was replaced."""


@dataclass
class WalkPlan:
    walk_id: str
    wdir: Path
    work: Path  # staging walk dir: symlinks to the real inputs + the new files
    new_files: dict[str, Path] = field(default_factory=dict)  # rel path in wdir -> staged file
    notes: list[str] = field(default_factory=list)
    lum: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- image helpers


def _rgb(path: Path, size: tuple[int, int] | None = None) -> np.ndarray:
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        if size:
            im = im.resize(size)
        return np.asarray(im, dtype=np.float64)


def _frame_rgb(video: Path, t: float, width: int = 360) -> np.ndarray:
    from punchwalk.pipeline import media

    proc = subprocess.run(
        [media.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(video),
         "-frames:v", "1", "-vf", f"scale={width}:-2,format=rgb24", "-f", "rawvideo", "-"],
        capture_output=True, check=True,
    )  # fmt: skip
    info = media.probe(video)
    h = round(info.display_height * width / info.display_width / 2) * 2
    return np.frombuffer(proc.stdout, np.uint8)[: width * h * 3].reshape(h, width, 3).astype(np.float64)


def luma_stats(rgb: np.ndarray) -> tuple[float, float]:
    y = rgb @ np.array([0.2126, 0.7152, 0.0722])
    return float(y.mean()), float((y >= 250).mean())


def _grad(rgb: np.ndarray) -> np.ndarray:
    import cv2

    g = (rgb @ np.array([0.299, 0.587, 0.114])).astype(np.float32)
    g = (g - g.mean()) / (g.std() + 1e-6)  # brightness/contrast-independent (old vs new tone curve)
    return np.hypot(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))


def ncc(a: np.ndarray, b: np.ndarray) -> float:
    ga, gb = _grad(a), _grad(b)
    ga, gb = ga - ga.mean(), gb - gb.mean()
    return float((ga * gb).sum() / (np.sqrt((ga * ga).sum() * (gb * gb).sum()) + 1e-9))


def mae(a: Path, b: Path) -> float:
    x, y = _rgb(a), _rgb(b)
    if x.shape != y.shape:
        return float("inf")
    return float(np.abs(x - y).mean())


def image_size(path: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as im:
        return im.size


# --------------------------------------------------------------------------- per-walk steps


def _stage_dir(plan: WalkPlan) -> None:
    """Work dir that looks like the walk dir to the pipeline helpers: inputs symlinked, outputs real."""
    shutil.rmtree(plan.work, ignore_errors=True)
    plan.work.mkdir(parents=True)
    for name in os.listdir(plan.wdir):
        src = plan.wdir / name
        if name in ("items", "closures", "display.mp4", "poster.jpg", "_work") or not (src.is_file() or src.is_dir()):
            continue
        (plan.work / name).symlink_to(src.resolve())


def build_display(plan: WalkPlan, footage: Path, meta) -> Path:
    """display.mp4 from the original clips with ingest's normalize+concat; must match meta.json exactly."""
    from punchwalk.pipeline.ingest import ingest

    clips = [footage / c.filename for c in meta.clips]
    missing = [str(c) for c in clips if not c.is_file()]
    if missing:
        raise RerenderError(f"original clip(s) not found: {', '.join(missing)}")
    out = plan.work.parent / f"{plan.walk_id}_ingest"
    new = ingest(clips, out, force=True)
    old_d, new_d = meta.display, new.display
    checks = {
        "display size": ((old_d.width, old_d.height), (new_d.width, new_d.height)),
        "display fps": (old_d.fps, new_d.fps),
        "display frame_count": (old_d.frame_count, new_d.frame_count),
        "duration_s": (meta.duration_s, new.duration_s),
        "sam frame_count": (meta.sam.frame_count, new.sam.frame_count),
        "clip offsets": ([c.offset_s for c in meta.clips], [c.offset_s for c in new.clips]),
        "clip frames": ([c.display_frame_count for c in meta.clips], [c.display_frame_count for c in new.clips]),
    }
    bad = {k: v for k, v in checks.items() if v[0] != v[1]}
    if bad:
        raise RerenderError(f"rebuilt display.mp4 doesn't match meta.json: {bad}")
    if not new.hdr_tonemapped and new.hdr_detected:
        raise RerenderError(f"no tonemap path was available (hdr_method={new.hdr_method})")
    plan.notes.append(f"display.mp4 rebuilt: {new_d.width}x{new_d.height} @ {new_d.fps} fps, "
                      f"{new_d.frame_count} frames, hdr_method={new.hdr_method}")  # fmt: skip
    staged = plan.work / "display.mp4"
    shutil.move(str(out / "display.mp4"), staged)
    shutil.rmtree(out, ignore_errors=True)
    plan.new_files["display.mp4"] = staged
    return staged


def check_alignment(plan: WalkPlan, old: Path, new: Path, times: list[float], fps: int) -> None:
    """The rebuilt video shows the same frame at each sample time (not one frame early or late)."""
    worst = 1.0
    for t in times:
        a = _frame_rgb(old, t)
        same = ncc(a, _frame_rgb(new, t))
        nbrs = [ncc(a, _frame_rgb(new, t + d)) for d in (-1.0 / fps, 1.0 / fps) if t + d >= 0]
        worst = min(worst, same)
        if same < NCC_MIN or (nbrs and same < max(nbrs) - 0.01):
            raise RerenderError(f"frame at {t:.2f}s doesn't line up: NCC same={same:.3f} neighbours={nbrs}")
    plan.notes.append(f"frames line up at {len(times)} sample times (min gradient NCC {worst:.3f})")


def lum_report(plan: WalkPlan, old: Path, new: Path, times: list[float]) -> None:
    o = [luma_stats(_frame_rgb(old, t)) for t in times]
    n = [luma_stats(_frame_rgb(new, t)) for t in times]
    plan.lum = {
        "old_mean": round(float(np.mean([x[0] for x in o])), 1), "new_mean": round(float(np.mean([x[0] for x in n])), 1),
        "old_clip": round(float(np.mean([x[1] for x in o])) * 100, 2), "new_clip": round(float(np.mean([x[1] for x in n])) * 100, 2),
    }  # fmt: skip


def _extract(display: Path, t: float, out: Path) -> Path:
    from punchwalk.pipeline import media
    from punchwalk.pipeline import params as P

    return media.extract_frame(display, t, out, quality=P.EVIDENCE_JPEG_QUALITY)


def _context_frames(display: Path, meta, t: float, out: Path, raw: Path) -> None:
    """context_a/b.jpg exactly as pick_evidence makes them (clip-bounded, retry near a clip end)."""
    from punchwalk.pipeline import media
    from punchwalk.pipeline import params as P

    clip = meta.clip_at(t)
    first_t, end_t = clip.offset_s, clip.offset_s + clip.duration_s
    last_t = max(first_t, end_t - 1.0 / meta.display.fps)
    for name, ct in (("context_a.jpg", max(first_t, t - P.CONTEXT_FRAME_OFFSET_S)),
                     ("context_b.jpg", min(last_t, t + P.CONTEXT_FRAME_OFFSET_S))):  # fmt: skip
        for attempt in (ct, max(first_t, min(ct, end_t - P.CONTEXT_END_MARGIN_S))):
            try:
                media.extract_frame(display, attempt, out / name, quality=P.EVIDENCE_JPEG_QUALITY)
                break
            except media.MediaError:
                continue
        else:
            (out / name).write_bytes(raw.read_bytes())


def render_item(item, ev, event: dict[str, Any] | None, sx: float, sy: float, src: Path, out: Path,
                old: Path) -> list[str]:
    """Draw evidence/crop/pointing/marked for one item from its raw frames in `src` into `out`, the way
    ground_defects / write_items drew them. Only files that exist in `old` are made. Returns their names."""
    import cv2

    from punchwalk.pipeline.frames import crop, scale_svg_path
    from punchwalk.render.evidence import render_crop, render_evidence, render_pointing

    out.mkdir(parents=True, exist_ok=True)
    made: list[str] = []
    raw = src / "raw.jpg"
    pointer = (ev.pointer.x, ev.pointer.y) if ev.pointer else None
    if (old / "marked.jpg").is_file() and pointer is not None:
        render_evidence(raw, out / "marked.jpg", pointer=pointer)
        made.append("marked.jpg")
    grounded = Path(ev.image).name == "evidence.jpg"
    if grounded:
        render_evidence(raw, out / "evidence.jpg", defect_svg=ev.defect_svg, pointer=pointer)
        made.append("evidence.jpg")
    if Path(ev.crop).name == "crop.jpg" and ev.crop_box is not None:
        cb = (int(ev.crop_box.x1), int(ev.crop_box.y1), int(ev.crop_box.x2), int(ev.crop_box.y2))
        if grounded:
            render_crop(out / "evidence.jpg", out / "crop.jpg", cb)
        else:  # pick_evidence's own crop of the raw frame
            cv2.imwrite(str(out / "crop.jpg"), crop(cv2.imread(str(raw)), cb), [cv2.IMWRITE_JPEG_QUALITY, 92])
        made.append("crop.jpg")
    if (old / "pointing.jpg").is_file() and (src / "pointing_raw.jpg").is_file():
        hand_svg = scale_svg_path(event["svg"], sx, sy) if event and event.get("svg") else None
        render_pointing(src / "pointing_raw.jpg", out / "pointing.jpg", hand_svg=hand_svg)
        made.append("pointing.jpg")
    return made


def rerender_items(plan: WalkPlan, session, meta, display: Path) -> None:
    from sqlmodel import select

    from punchwalk.core.models import Evidence, Item

    stages = plan.wdir / "stages"
    events = {e["id"]: e for e in json.loads((stages / "find_points.json").read_text())["events"]}
    sx, sy = meta.scale.sam_to_display_x, meta.scale.sam_to_display_y
    items = session.exec(select(Item).where(Item.origin_walk_id == plan.walk_id)).all()
    check_root = plan.work.parent / f"{plan.walk_id}_recipe"
    for item in sorted(items, key=lambda i: i.number):
        if not item.evidence:
            continue
        ev = Evidence.model_validate(item.evidence)
        old = plan.wdir / "items" / item.id
        new = plan.work / "items" / item.id
        new.mkdir(parents=True, exist_ok=True)
        event = events.get(item.point_id) if item.point_id else None
        # 1. the recipe must reproduce the old renders from the old raw frames
        made = render_item(item, ev, event, sx, sy, old, check_root / item.id, old)
        for name in made:
            err = mae(check_root / item.id / name, old / name)
            if err > RECIPE_MAX_MAE:
                raise RerenderError(f"item #{item.number} {name}: re-drawing from the old raw frame differs "
                                    f"from the stored render (MAE {err:.2f}); not replacing anything")  # fmt: skip
        # 2. fresh frames from the new display.mp4
        raw = _extract(display, ev.t, new / "raw.jpg")
        staged = ["raw.jpg"]
        if (old / "context_a.jpg").is_file() or (old / "context_b.jpg").is_file():
            _context_frames(display, meta, ev.t, new, raw)
            staged += ["context_a.jpg", "context_b.jpg"]
        if (old / "pointing_raw.jpg").is_file():
            if event is None:
                raise RerenderError(f"item #{item.number} has pointing_raw.jpg but no find_points event")
            _extract(display, event["t"], new / "pointing_raw.jpg")
            staged.append("pointing_raw.jpg")
        staged += render_item(item, ev, event, sx, sy, new, new, old)
        for name in staged:
            plan.new_files[f"items/{item.id}/{name}"] = new / name
        o, n = luma_stats(_rgb(old / "raw.jpg")), luma_stats(_rgb(raw))
        plan.notes.append(f"item #{item.number:02d} {item.id} t={ev.t:.1f}s: {len(staged)} files; raw.jpg mean luma "
                          f"{o[0]:.0f} -> {n[0]:.0f}, clipped {o[1] * 100:.1f}% -> {n[1] * 100:.1f}%")  # fmt: skip
    shutil.rmtree(check_root, ignore_errors=True)


def draw_closure(after_raw: Path, before_crop: Path | None, out: Path, pointer, cb: tuple[int, int, int, int],
                 before_after: bool) -> list[str]:
    """after_raw / after_frame (dot) / after (crop) / before_after.jpg, as resolve_closures draws them."""
    from punchwalk.render.before_after import render_before_after
    from punchwalk.render.evidence import render_crop, render_evidence

    out.mkdir(parents=True, exist_ok=True)
    if after_raw != out / "after_raw.jpg":
        (out / "after_raw.jpg").write_bytes(after_raw.read_bytes())
    full = render_evidence(out / "after_raw.jpg", out / "after_frame.jpg", pointer=pointer)
    crop_jpg = render_crop(full, out / "after.jpg", cb)
    names = ["after_raw.jpg", "after_frame.jpg", "after.jpg"]
    if before_after and before_crop is not None and before_crop.is_file():
        render_before_after(before_crop, crop_jpg, out / "before_after.jpg")
        names.append("before_after.jpg")
    return names


def rerender_closures(plan: WalkPlan, session, walk, data: Path) -> None:
    """Re-walk renders: closures/<mention>/*.jpg (after_frames) and items/<id>/after*.jpg + before_after.jpg."""
    from sqlmodel import select

    from punchwalk.core.enums import WalkKind
    from punchwalk.core.models import Evidence, Item
    from punchwalk.pipeline import closures
    from punchwalk.pipeline.context import StageContext
    from punchwalk.pipeline.mentions import mention_from_dict

    stages = plan.wdir / "stages"
    points_doc = json.loads((stages / "find_points.json").read_text())
    track = json.loads((stages / "track_hands.json").read_text())
    resolved = json.loads((stages / "resolve_closures.json").read_text())
    mentions = [mention_from_dict({k: v for k, v in m.items() if k != "point_id"}) for m in points_doc["mentions"]]
    by_id = {e["id"]: e for e in points_doc["events"]}
    events = {m["key"]: by_id[m["point_id"]] for m in points_doc["mentions"] if m.get("point_id")}
    ctx = StageContext(walk_id=plan.walk_id, project_id=walk.project_id, kind=WalkKind.rewalk, walk_dir=plan.work,
                       session_factory=None)  # type: ignore[arg-type]  # fmt: skip
    frames = closures.after_frames(ctx, mentions, events, track)  # writes work/closures/<key>/*.jpg
    for key in frames:
        for name in ("after_raw.jpg", "after_frame.jpg", "pointing_raw.jpg", "pointing.jpg"):
            if (plan.work / "closures" / key / name).is_file() and (plan.wdir / "closures" / key / name).is_file():
                plan.new_files[f"closures/{key}/{name}"] = plan.work / "closures" / key / name
    items = session.exec(select(Item)).all()
    for item in sorted(items, key=lambda i: i.number):
        cl = item.closure or {}
        if cl.get("rewalk_id") != plan.walk_id or not cl.get("after_image"):
            continue
        row = (resolved.get("items") or {}).get(item.id) or {}
        frame = frames.get(row.get("mention"))
        if frame is None:
            raise RerenderError(f"item #{item.number}: no re-walk frame for mention {row.get('mention')!r}")
        if abs(float(frame["t"]) - float(cl["after_t"])) > 1e-6:
            raise RerenderError(f"item #{item.number}: recomputed re-walk frame t={frame['t']} != after_t={cl['after_t']}")
        before = Evidence.model_validate(item.evidence) if item.evidence else None
        old = plan.wdir / "items" / item.id
        new = plan.work / "items" / item.id
        new.mkdir(parents=True, exist_ok=True)
        cb = closures.after_crop_box(frame, before)

        # recipe check on the old frames (old first-walk crop: its backup if walk1 was already replaced)
        old_before_crop = None
        if before is not None:
            backup = data / BACKUP_NAME / Path(before.crop).relative_to("walks")
            old_before_crop = backup if backup.is_file() else data / before.crop
        check = plan.work.parent / f"{plan.walk_id}_recipe" / item.id
        want_ba = (old / "before_after.jpg").is_file()
        for name in draw_closure(old / "after_raw.jpg", old_before_crop, check, frame["pointer"], cb, want_ba):
            err = mae(check / name, old / name)
            if err > RECIPE_MAX_MAE:
                raise RerenderError(f"item #{item.number} {name}: recipe doesn't reproduce the stored render (MAE {err:.2f})")
        shutil.rmtree(check.parent, ignore_errors=True)
        made = draw_closure(Path(frame["raw"]), data / before.crop if before else None, new, frame["pointer"], cb,
                            want_ba)
        for name in made:
            plan.new_files[f"items/{item.id}/{name}"] = new / name
        o, n = luma_stats(_rgb(old / "after_raw.jpg")), luma_stats(_rgb(new / "after_raw.jpg"))
        plan.notes.append(f"closure of #{item.number:02d} after_t={cl['after_t']:.1f}s: {len(made)} files; after_raw "
                          f"mean luma {o[0]:.0f} -> {n[0]:.0f}, clipped {o[1] * 100:.1f}% -> {n[1] * 100:.1f}%")  # fmt: skip


def validate(plan: WalkPlan) -> None:
    """Every staged file replaces an existing one of the same pixel size (geometry unchanged)."""
    from punchwalk.pipeline import media

    for rel, staged in plan.new_files.items():
        target = plan.wdir / rel
        if not target.is_file():
            raise RerenderError(f"{rel}: there's no existing file to replace")
        if rel.endswith(".mp4"):
            a, b = media.probe(target), media.probe(staged)
            if (a.width, a.height) != (b.width, b.height):
                raise RerenderError(f"{rel}: size {a.width}x{a.height} -> {b.width}x{b.height}")
        elif image_size(target) != image_size(staged):
            raise RerenderError(f"{rel}: size {image_size(target)} -> {image_size(staged)}")


def swap_in(plan: WalkPlan, data: Path) -> None:
    backup_root = data / BACKUP_NAME / plan.walk_id
    for rel, staged in sorted(plan.new_files.items()):
        target = plan.wdir / rel
        backup = backup_root / rel
        if not backup.exists():  # keep the very first originals across re-runs
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
        os.replace(staged, target)


def rerender_walk(walk_id: str, data: Path, footage: Path, *, dry_run: bool, keep_work: bool) -> WalkPlan:
    from sqlmodel import select

    from punchwalk.core.enums import WalkKind
    from punchwalk.core.models import Item, Walk
    from punchwalk.db import session_factory
    from punchwalk.pipeline.context import POSTER_NAME
    from punchwalk.pipeline.ingest import IngestMeta
    from punchwalk.pipeline.prepare import original_name, write_poster

    wdir = data / "walks" / walk_id
    plan = WalkPlan(walk_id=walk_id, wdir=wdir, work=data / WORK_NAME / walk_id / "walk")
    meta = IngestMeta.load(wdir)
    with session_factory() as s:
        walk = s.get(Walk, walk_id)
        if walk is None:
            raise RerenderError(f"no walk {walk_id} in {data}")
        names = [c["filename"] for c in sorted(walk.clips or [], key=lambda c: c["order"])]
        if names != [original_name(c.filename) for c in meta.clips]:
            raise RerenderError(f"Walk.clips {names} doesn't match meta.json clips {[c.filename for c in meta.clips]}")
        _stage_dir(plan)
        display = build_display(plan, footage, meta)
        items = s.exec(select(Item)).all()
        times = [float(i.evidence["t"]) for i in items if i.origin_walk_id == walk_id and i.evidence]
        times += [float(i.closure["after_t"]) for i in items
                  if (i.closure or {}).get("rewalk_id") == walk_id and i.closure.get("after_t") is not None]
        times += [round(c.offset_s + c.duration_s / 2, 2) for c in meta.clips]
        check_alignment(plan, wdir / "display.mp4", display, sorted(set(times)), meta.display.fps)
        lum_report(plan, wdir / "display.mp4", display, sorted(set(times)))
        plan.new_files[POSTER_NAME] = write_poster(plan.work, meta)
        rerender_items(plan, s, meta, display)
        if walk.kind == WalkKind.rewalk:
            rerender_closures(plan, s, walk, data)
    validate(plan)
    if not dry_run:
        swap_in(plan, data)
    if not keep_work:
        shutil.rmtree(data / WORK_NAME / walk_id, ignore_errors=True)
    return plan


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    ap.add_argument("--footage", type=Path, default=REPO_ROOT / "footage", help="where the original clips are")
    ap.add_argument("--walks", nargs="+", required=True, help="walk ids, e.g. wlk_demo_walk1 (first walks before re-walks)")
    ap.add_argument("--dry-run", action="store_true", help="build and check everything, replace nothing")
    ap.add_argument("--keep-work", action="store_true", help=f"keep DATA_DIR/{WORK_NAME}/ for inspection")
    args = ap.parse_args(argv)

    data = args.data_dir.resolve()
    os.environ["DATA_DIR"] = str(data)
    os.environ["PUNCHWALK_CACHE"] = "readonly"  # nothing here calls a model; make sure nothing could
    from punchwalk.config import reset_settings

    reset_settings()
    failed = 0
    for walk_id in args.walks:
        try:
            plan = rerender_walk(walk_id, data, args.footage.resolve(), dry_run=args.dry_run, keep_work=args.keep_work)
        except RerenderError as exc:
            failed += 1
            print(f"{walk_id}: SKIPPED, nothing replaced: {exc}", file=sys.stderr)
            continue
        verb = "would replace" if args.dry_run else "replaced"
        print(f"{walk_id}: {verb} {len(plan.new_files)} files"
              f"{'' if args.dry_run else f' (originals in {data / BACKUP_NAME / walk_id})'}")  # fmt: skip
        lum = plan.lum
        print(f"  display.mp4 mean luma {lum['old_mean']} -> {lum['new_mean']}, "
              f"clipped highlights {lum['old_clip']}% -> {lum['new_clip']}%")  # fmt: skip
        for note in plan.notes:
            print(f"  {note}")
    if not args.keep_work:
        shutil.rmtree(data / WORK_NAME, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
