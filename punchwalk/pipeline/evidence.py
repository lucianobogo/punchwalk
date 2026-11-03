"""Stage `pick_evidence` (PLAN 1.6, pointing revision): the clearest photo of each defect.

Pointed items: candidates are the SAM frames within +/- 1.5 s of the pointing event (same clip
part only: no candidates across a camera cut). A candidate qualifies when the hand masks of
that frame cover < 5% of a square (15% of the frame width) around the fingertip, i.e. the hand
is not in the way of the defect. Score = Laplacian variance of a context crop around the
fingertip (hand pixels excluded), x EVIDENCE_AFTER_BONUS for frames after the hold with no hand in view (withdrawn). When no frame qualifies,
the least-covered sharpest frame is used and `hand_clear` is false in the stage output.

The fingertip is carried from the pointing frame into the chosen frame by an ORB + RANSAC
similarity fit on the region around it, else by phase correlation (translation; works on plain
walls), with hand pixels masked out. When both fail the camera is assumed still (`aligned: false`
in the stage output).

Items that are protected (sent, closed or edited by a person) keep their photo, files and outlines.

Voice-only items: 5 frames sampled across the turn window (inside its clip part), the sharpest wins,
pointer null. Context frames never cross a clip boundary.
With GROUNDING_MODE=voice_only every item takes this path (PLAN 2.4).

Files per item (DATA_DIR/walks/<walk>/items/<item>/): raw.jpg (evidence frame, full display
resolution, no outlines), pointing_raw.jpg (the pointing frame; pointed items only; ground_defects
renders pointing.jpg from it with the hand outlined), context_a.jpg /
context_b.jpg (-/+ 0.7 s), crop.jpg (4:3 card crop centered on the pointer, no outlines).
`Item.evidence` gets t, image (= raw until Phase 2 draws outlines), raw, crop, pointing_frame,
hand_box, pointer, width, height, crop_box, grounding (`pointed_only` | `none`).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
from sqlmodel import select

from punchwalk.config import get_settings
from punchwalk.core import models
from punchwalk.core.enums import Stage
from punchwalk.core.models import Item
from punchwalk.pipeline import media
from punchwalk.pipeline import params as P
from punchwalk.pipeline.context import StageContext, StageOutcome, rel_to_data
from punchwalk.pipeline.frames import (
    crop,
    laplacian_variance,
    masked_laplacian_variance,
    rasterize_svg,
    read_frames,
    square_around,
)

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- choosing a frame


@dataclass
class Choice:
    frame: int
    pointer: tuple[float, float] | None  # SAM px in the chosen frame
    score: float
    cover: float | None  # hand-mask fraction around the pointer
    hand_clear: bool
    aligned: bool
    candidates: int


def hand_cover(mask: np.ndarray | None, x: float, y: float, w: int, h: int) -> float:
    """Fraction of hand-mask pixels in the EVIDENCE_CLEAR_BOX_FRAC square around (x, y)."""
    if mask is None:
        return 0.0
    box = square_around(x, y, P.EVIDENCE_CLEAR_BOX_FRAC * w, w, h)
    region = crop(mask, box)
    return float(region.mean()) if region.size else 0.0


def _masked_gray(img: np.ndarray, mask: np.ndarray | None, box: tuple[int, int, int, int]) -> np.ndarray:
    x1, y1, x2, y2 = box
    g = cv2.cvtColor(img[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY).astype(np.float32)
    if mask is not None:
        m = mask[y1:y2, x1:x2].astype(bool)
        if m.any() and (~m).any():
            g[m] = float(g[~m].mean())
    return g


def _orb_shift(g1: np.ndarray, g2: np.ndarray, m1, m2, lx: float, ly: float) -> tuple[float, float] | None:
    orb = cv2.ORB_create(nfeatures=600)
    k1, d1 = orb.detectAndCompute(g1.astype(np.uint8), m1)
    k2, d2 = orb.detectAndCompute(g2.astype(np.uint8), m2)
    if d1 is None or d2 is None or len(k1) < P.ALIGN_MIN_INLIERS or len(k2) < P.ALIGN_MIN_INLIERS:
        return None
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(d1, d2, k=2)
    good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < 0.75 * p[1].distance]
    if len(good) < P.ALIGN_MIN_INLIERS:
        return None
    a = np.float32([k1[m.queryIdx].pt for m in good])
    b = np.float32([k2[m.trainIdx].pt for m in good])
    M, inl = cv2.estimateAffinePartial2D(a, b, method=cv2.RANSAC, ransacReprojThreshold=3.0)
    if M is None or inl is None or int(inl.sum()) < P.ALIGN_MIN_INLIERS:
        return None
    return (M[0, 0] * lx + M[0, 1] * ly + M[0, 2] - lx, M[1, 0] * lx + M[1, 1] * ly + M[1, 2] - ly)


def _phase_shift(g1: np.ndarray, g2: np.ndarray) -> tuple[float, float] | None:
    """Translation from g1 to g2 by phase correlation (works on low-texture walls)."""
    if g1.shape != g2.shape or min(g1.shape) < 16 or min(g1.std(), g2.std()) < P.ALIGN_MIN_TEXTURE_STD:
        return None  # nothing to lock on to
    win = cv2.createHanningWindow((g1.shape[1], g1.shape[0]), cv2.CV_32F)
    (dx, dy), response = cv2.phaseCorrelate(g1, g2, win)
    return (float(dx), float(dy)) if response >= P.ALIGN_MIN_PHASE_RESPONSE else None


def align_point(
    src: np.ndarray, dst: np.ndarray, pt: tuple[float, float],
    src_mask: np.ndarray | None = None, dst_mask: np.ndarray | None = None,
) -> tuple[tuple[float, float], bool]:
    """Carry `pt` from frame `src` into frame `dst`: ORB + RANSAC similarity on the region around the
    point, else phase correlation (translation). Hand pixels are masked out. (pt, aligned)."""
    if src is dst:
        return pt, True
    h, w = src.shape[:2]
    box = square_around(pt[0], pt[1], P.ALIGN_REGION_FRAC * min(w, h) * 2, w, h)
    g1, g2 = _masked_gray(src, src_mask, box), _masked_gray(dst, dst_mask, box)
    m1 = None if src_mask is None else ((1 - src_mask[box[1]:box[3], box[0]:box[2]]) * 255).astype(np.uint8)
    m2 = None if dst_mask is None else ((1 - dst_mask[box[1]:box[3], box[0]:box[2]]) * 255).astype(np.uint8)
    shift = _orb_shift(g1, g2, m1, m2, pt[0] - box[0], pt[1] - box[1]) or _phase_shift(g1, g2)
    if shift is None or math.hypot(*shift) > P.ALIGN_MAX_SHIFT_FRAC * w:
        return pt, False
    nx, ny = pt[0] + shift[0], pt[1] + shift[1]
    return (float(min(max(nx, 0.0), w - 1)), float(min(max(ny, 0.0), h - 1))), True


def choose_pointed_frame(
    frames: dict[int, np.ndarray],
    hand_masks: dict[int, np.ndarray],
    *,
    pointing_frame: int,
    fingertip: tuple[float, float],
    hold_last: int,
) -> Choice:
    """Best evidence frame around a pointing event (see module docstring)."""
    if pointing_frame not in frames:
        raise ValueError(f"pointing frame {pointing_frame} was not decoded")
    src = frames[pointing_frame]
    h, w = src.shape[:2]
    side = P.ALIGN_REGION_FRAC * min(w, h)
    scored: list[tuple[float, float, int, tuple[float, float], bool]] = []
    for f in sorted(frames):
        img = frames[f]
        pt, aligned = (fingertip, True) if f == pointing_frame else align_point(
            src, img, fingertip, hand_masks.get(pointing_frame), hand_masks.get(f))
        mask = hand_masks.get(f)
        cover = hand_cover(mask, pt[0], pt[1], w, h)
        region = square_around(pt[0], pt[1], side, w, h)
        sharp = masked_laplacian_variance(crop(img, region), None if mask is None else crop(mask, region))
        withdrawn = f > hold_last and (mask is None or not mask.any())
        bonus = P.EVIDENCE_AFTER_BONUS if withdrawn else 1.0
        scored.append((sharp * bonus, cover, f, pt, aligned))
    clear = [s for s in scored if s[1] < P.EVIDENCE_MAX_HAND_COVER]
    if clear:
        best = max(clear, key=lambda s: (s[0], -abs(s[2] - pointing_frame)))
    else:
        best = min(scored, key=lambda s: (s[1], -s[0]))
    return Choice(frame=best[2], pointer=best[3], score=best[0], cover=best[1], hand_clear=bool(clear),
                  aligned=best[4], candidates=len(scored))


def choose_sharpest(frames: dict[int, np.ndarray]) -> Choice:
    """Voice-only items: the sharpest whole frame."""
    best = max(sorted(frames), key=lambda f: laplacian_variance(frames[f]))
    return Choice(frame=best, pointer=None, score=laplacian_variance(frames[best]), cover=None,
                  hand_clear=True, aligned=True, candidates=len(frames))


def sample_frames(start_s: float, end_s: float, fps: float, first: int, last: int, n: int) -> list[int]:
    """n SAM frame indices spread evenly over [start_s, end_s], kept inside [first, last] (the clip part)."""
    lo = max(first, min(last, int(round(start_s * fps))))
    hi = max(lo, min(last, int(round(end_s * fps))))
    return sorted({int(round(v)) for v in np.linspace(lo, hi, n)})


def crop_box_4x3(x: float, y: float, w: int, h: int) -> tuple[int, int, int, int]:
    """4:3 card crop centered on (x, y) in display px, shifted to stay inside the frame."""
    ch = int(round(P.CROP_MIN_FRAC * min(w, h)))
    cw = int(round(ch * P.CROP_ASPECT))
    if cw > w:
        cw, ch = w, int(round(w / P.CROP_ASPECT))
    x1 = int(round(min(max(0, x - cw / 2), w - cw)))
    y1 = int(round(min(max(0, y - ch / 2), h - ch)))
    return x1, y1, x1 + cw, y1 + ch


# --------------------------------------------------------------------------- stage


def _hand_masks(records: list[dict[str, Any]], frames: set[int], w: int, h: int) -> dict[int, np.ndarray]:
    masks: dict[int, np.ndarray] = {}
    for r in records:
        if r["frame"] in frames and r.get("svg"):
            m = rasterize_svg(r["svg"], w, h)
            masks[r["frame"]] = np.maximum(masks[r["frame"]], m) if r["frame"] in masks else m
    return masks


def _protected_rows(ctx: StageContext) -> dict[str, dict[str, Any]]:
    """Protected items (items.is_protected) that already have evidence: their previous stage row (or one
    rebuilt from Item.evidence), so later stages see the same photo and nothing is re-rendered."""
    from punchwalk.pipeline.context import read_stage_output
    from punchwalk.pipeline.items import is_protected

    try:
        previous = read_stage_output(ctx.walk_dir, Stage.pick_evidence)["items"]
    except Exception:  # noqa: BLE001 - first run
        previous = {}
    rows: dict[str, dict[str, Any]] = {}
    with ctx.session() as s:
        for item in s.exec(select(Item).where(Item.origin_walk_id == ctx.walk_id)).all():
            if item.evidence and is_protected(item):
                rows[item.id] = previous.get(item.id) or {"evidence": item.evidence, "kept": True}
    return rows


def _part_range(ctx: StageContext, frame: int) -> tuple[int, int]:
    """[first, last] SAM frame of the clip part that holds `frame`."""
    for c in ctx.meta.clips:
        if c.sam_frame_offset <= frame < c.sam_frame_offset + max(1, c.sam_frame_count):
            return c.sam_frame_offset, c.sam_frame_offset + max(1, c.sam_frame_count) - 1
    return 0, ctx.meta.sam.frame_count - 1


def run(ctx: StageContext) -> StageOutcome:
    voice_only = get_settings().GROUNDING_MODE == "voice_only"
    meta = ctx.meta
    drafts = ctx.output(Stage.fuse)["drafts"]
    events = {e["id"]: e for e in ctx.output(Stage.find_points)["events"]}
    track = ctx.output(Stage.track_hands)
    w, h, fps = track["sam"]["width"], track["sam"]["height"], float(track["sam"]["fps"])
    sx, sy = meta.scale.sam_to_display_x, meta.scale.sam_to_display_y
    dw, dh = meta.display.width, meta.display.height
    radius = int(round(P.EVIDENCE_SEARCH_S * fps))

    plans: dict[str, list[int]] = {}
    for d in drafts:
        ev = events.get(d["point_id"]) if d["point_id"] and not voice_only else None
        if ev:
            lo, hi = _part_range(ctx, ev["frame"])
            plans[d["item_id"]] = list(range(max(lo, ev["frame"] - radius), min(hi, ev["frame"] + radius) + 1))
        else:  # the turn window (PLAN 1.6), never across a clip boundary
            lo, hi = _part_range(ctx, int(round(d["quote_start_s"] * fps)))
            plans[d["item_id"]] = sample_frames(d["quote_start_s"], d["quote_end_s"], fps, lo, hi,
                                                P.VOICE_ONLY_SAMPLES)
    needed = {f for fs in plans.values() for f in fs}
    frames = read_frames(ctx.walk_dir / meta.sam.path, needed)
    masks = _hand_masks(track["records"], needed, w, h)

    results: dict[str, dict[str, Any]] = {}
    clear = 0
    display = ctx.walk_dir / meta.display.path
    kept = _protected_rows(ctx)
    for d in drafts:
        item_id = d["item_id"]
        if item_id in kept:  # sent, closed or edited by a person: its photo and outlines stay as they are
            results[item_id] = kept[item_id]
            continue
        got = {f: frames[f] for f in plans[item_id] if f in frames}
        if not got:
            log.warning("pick_evidence: no frames decoded for %s", item_id)
            continue
        ev = events.get(d["point_id"]) if d["point_id"] and not voice_only else None
        if ev and ev["frame"] in got:
            choice = choose_pointed_frame(got, masks, pointing_frame=ev["frame"],
                                          fingertip=tuple(ev["fingertip"]), hold_last=ev["hold_frames"][1])
        else:
            choice = choose_sharpest(got)
        clear += int(choice.hand_clear and ev is not None)
        results[item_id] = _write_item_files(ctx, d, ev, choice, fps, sx, sy, dw, dh, display)

    with ctx.session() as s:
        for item in s.exec(select(Item).where(Item.origin_walk_id == ctx.walk_id)).all():
            if item.id in results and item.id not in kept:
                ev_doc = results[item.id]["evidence"]
                item.evidence = models.Evidence.model_validate(ev_doc).model_dump(mode="json")
                s.add(item)
        s.commit()
    ctx.save_output(Stage.pick_evidence, {"version": 1, "items": results})
    pointed = sum(1 for r in results.values() if r["evidence"].get("pointer"))
    return StageOutcome(
        counts={"photos": len(results), "pointed": pointed, "hand_clear": clear},
        message=f"Picked {len(results)} photos ({clear} of {pointed} with the hand out of the way)",
    )


def _write_item_files(
    ctx: StageContext, d: dict[str, Any], ev: dict[str, Any] | None, choice: Choice,
    fps: float, sx: float, sy: float, dw: int, dh: int, display,
) -> dict[str, Any]:
    out = ctx.item_dir(d["item_id"])
    out.mkdir(parents=True, exist_ok=True)
    t = round(choice.frame / fps, 3)
    clip = ctx.meta.clip_at(t)  # context frames stay inside this clip part (no camera cut)
    first_t = clip.offset_s
    last_t = max(first_t, clip.offset_s + clip.duration_s - 1.0 / ctx.meta.display.fps)
    q = P.EVIDENCE_JPEG_QUALITY
    raw = media.extract_frame(display, t, out / "raw.jpg", quality=q)
    media.extract_frame(display, max(first_t, t - P.CONTEXT_FRAME_OFFSET_S), out / "context_a.jpg", quality=q)
    media.extract_frame(display, min(last_t, t + P.CONTEXT_FRAME_OFFSET_S), out / "context_b.jpg", quality=q)
    pointing_rel = None
    if ev:
        pointing_rel = rel_to_data(media.extract_frame(display, ev["t"], out / "pointing_raw.jpg", quality=q))
    pointer = None
    if choice.pointer is not None:
        pointer = {"x": round(choice.pointer[0] * sx, 1), "y": round(choice.pointer[1] * sy, 1)}
    cx, cy = (pointer["x"], pointer["y"]) if pointer else (dw / 2, dh / 2)
    cb = crop_box_4x3(cx, cy, dw, dh)
    img = cv2.imread(str(raw))
    if img is not None and img.shape[1] == dw and img.shape[0] == dh:
        cv2.imwrite(str(out / "crop.jpg"), crop(img, cb), [cv2.IMWRITE_JPEG_QUALITY, 92])
    else:  # unexpected frame size: fall back to the raw frame
        cv2.imwrite(str(out / "crop.jpg"), img if img is not None else np.zeros((dh, dw, 3), np.uint8))
    hand_box = None
    if ev:
        x1, y1, x2, y2 = ev["hand_box_display"]
        hand_box = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
    raw_rel = rel_to_data(raw)
    evidence = {
        "t": t,
        "image": raw_rel,  # Phase 2 renders the outlined evidence.jpg
        "raw": raw_rel,
        "crop": rel_to_data(out / "crop.jpg"),
        "pointing_frame": pointing_rel,
        "width": dw,
        "height": dh,
        "hand_box": hand_box,
        "pointer": pointer,
        "crop_box": {"x1": cb[0], "y1": cb[1], "x2": cb[2], "y2": cb[3]},
        "grounding": "pointed_only" if pointer else "none",
    }
    return {
        "evidence": evidence,
        "frame": choice.frame,
        "score": round(choice.score, 2),
        "hand_cover": None if choice.cover is None else round(choice.cover, 4),
        "hand_clear": choice.hand_clear,
        "aligned": choice.aligned,
        "candidates": choice.candidates,
        "context_a": rel_to_data(out / "context_a.jpg"),
        "context_b": rel_to_data(out / "context_b.jpg"),
        "pointing_raw": pointing_rel,
    }
