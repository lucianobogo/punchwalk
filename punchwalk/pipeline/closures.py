"""Stage `resolve_closures` (PLAN 7.1-7.4): what the re-walk says about each open item.

Runs on re-walks only (Walk.kind == rewalk), after stages ingest..pick_evidence of the re-walk.

7.1 Match. Every re-walk mention (status updates, and defect turns that may re-describe an item) gets
    one Muse Spark Open call (low effort): the project's items from earlier walks as [{number, title, room,
    location_detail, description}], the words, the room in effect, and, when the inspector pointed,
    the re-walk frame with a blue dot → {item_number | null, claimed: fixed | not_fixed | unclear,
    confidence, rationale}. An item takes its most confident mention (>= CLOSURE_MIN_MATCH_CONF).
7.2 Look. For each matched item: the re-walk frame of its pointing event (hand out of the way,
    pointer carried across camera motion; else the sharpest frame of the turn window) and a 4:3 crop
    around the pointer. Muse Spark Open (medium) answers {defect_still_present: yes | no | unclear, note}:
      - the original has an outline: the first-walk frame is lined up with the re-walk frame (ORB, else
        phase correlation) and the old outline is redrawn on the re-walk frame ("aligned": is the defect
        still inside it?); if they can't be lined up, the four photos are compared ("compare");
      - no outline (voice-only original): is the described defect visible at the re-walk dot ("presence").
    When the frames line up, a pixel check also measures the outlined mark's contrast against its
    surroundings in both frames (`pixel_presence`); `combine` merges it with the model's answer.
7.3 Decide (`decide()`):
      claimed fixed + fixed on camera      → signed_off, status closed
      claimed fixed + still visible        → needs_review, REVIEW_FALSE_FIX (the headline case)
      claimed fixed + can't tell           → needs_review, "...the photo doesn't clearly show it."
      said not fixed (any photo)           → still_open (sent again by persist)
      unclear words + still visible/unclear→ still_open
      unclear words + fixed on camera      → needs_review, "...looks fixed, but nobody said so."
    Open items in rooms the re-walk visited that nobody mentioned → not_checked, NOT_CHECKED_ON_REWALK,
    status unchanged.
7.4 Render items/<item_id>/after_raw.jpg, after_frame.jpg (dot), after.jpg (4:3 crop, dot) and
    before_after.jpg (original crop | re-walk crop) in the re-walk's folder; Closure points at them.

Re-runs recompute from scratch: the status/review note/closure each item had before this re-walk is
kept in stages/resolve_closures.json and restored first, except for items a person changed after the
re-walk decided them. Defect mentions that match no item stay new items and go through
write_items → ground_defects → verify; ones that re-describe an item are folded into it.
Prompts carry item numbers, never ids, so recorded calls replay for any walk of the same project.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np
from pydantic import BaseModel, Field
from sqlmodel import col, select

from punchwalk.clients import llm
from punchwalk.clients.base import ApiError
from punchwalk.core.enums import (
    NOT_CHECKED_ON_REWALK,
    REVIEW_FALSE_FIX,
    ClosureDecision,
    HistoryAction,
    HistoryActor,
    ItemStatus,
    Stage,
    VisionStatus,
    VoiceStatus,
    WalkKind,
)
from punchwalk.core.models import Closure, Evidence, Item, utcnow
from punchwalk.pipeline import evidence as ev_mod
from punchwalk.pipeline import ground, media, verify, write_items
from punchwalk.pipeline import params as P
from punchwalk.pipeline.context import StageContext, StageOutcome, data_dir, rel_to_data
from punchwalk.pipeline.frames import rasterize_svg, read_frames, svg_polygons
from punchwalk.pipeline.items import add_history, is_protected
from punchwalk.pipeline.mentions import Mention, mention_from_dict
from punchwalk.render.before_after import render_before_after
from punchwalk.render.evidence import crop_box_for, render_crop, render_evidence

log = logging.getLogger(__name__)

REVIEW_FIX_UNCLEAR = "Marked fixed on the re-walk, but the photo doesn't clearly show it."
REVIEW_LOOKS_FIXED = "Looks fixed on the re-walk, but nobody said so."
NOTE_FIXED = "Signed off on the re-walk: fixed on camera."
NOTE_STILL_OPEN = "Still open on the re-walk."
NOTE_NO_COMPARE = "Couldn't compare the photos."
MAP_MAX_TOKENS = 1200
LOOK_MAX_TOKENS = 2000


# --------------------------------------------------------------------------- schemas


class StatusMatch(BaseModel):
    item_number: int | None = Field(description="number of the open item this is about, or null if none fits")
    claimed: Literal["fixed", "not_fixed", "unclear"] = Field(description="what the inspector says about it")
    confidence: float = Field(description="0 to 1")
    rationale: str = Field(description="at most 200 characters")


class FixCheck(BaseModel):
    defect_still_present: Literal["yes", "no", "unclear"]
    note: str = Field(description="one short plain sentence about what photo 2 shows")
    rationale: str = Field(description="at most 200 characters")


MAP_SYSTEM = """You match what an inspector says on a construction re-walk to the open punch-list items.
On the re-walk the inspector goes back to each item, points at it, and says its status
("Kitchen cabinet knob, fixed." / "Bedroom scuffs, still there.").
Return the number of the one open item the words are about, or null if none fits (a new defect, or
nothing to do with an item). Use the room, the words and where the finger pointed.
claimed: "fixed" if the inspector says it is fixed, done or good; "not_fixed" if still there, not done,
or if they describe the defect again; "unclear" otherwise. Keep rationale to one short sentence."""

LOOK_SYSTEM = """You compare photos of the same spot on a construction site, taken on two walks.
Photos 1 and 3 are from the first walk: the defect is outlined in red when it was found, and a blue dot
marks where the inspector pointed (photo 1 is a close-up of photo 3). Photos 2 and 4 are from the re-walk,
of the same spot: the blue dot marks where the inspector pointed this time (photo 2 is a close-up of photo 4).
Question: is the thing that was marked as the defect in photos 1/3 still there at the same spot in photos 2/4?
"yes": still there. "no": the spot is now clean, repaired or the item is in place. "unclear": the re-walk
photos don't show that spot. Judge by comparing the photos; the description is only a hint.
Keep note and rationale short and plain."""

LOOK_ALIGNED_SYSTEM = """You check whether a construction defect was fixed.
Photo 1 is from the first walk: the defect is outlined in red. Photo 2 is the same spot on the re-walk,
lined up with photo 1: the red outline in photo 2 only marks where the defect WAS.
The outline is drawn on top of photo 2, so look at what is under and inside it.
Question: is the same thing that is inside the outline in photo 1 still inside the outline in photo 2?
"yes": the same mark/damage/problem is still there (even if it looks a little different or the outline
is slightly off). "no": under the outline it now looks clean, repaired or complete, like the surrounding
surface. "unclear": photo 2 doesn't show that spot.
Judge by comparing what is inside the outline in both photos; the description is only a hint.
Keep note and rationale short and plain."""

LOOK_PRESENCE_SYSTEM = """You check whether a construction defect is still there on a re-walk.
The photos are from the re-walk: photo 1 is a close-up around the blue dot, which marks where the
inspector pointed; photo 2 is the whole frame. The finger may land a little beside the defect.
Question: within about a finger's length of the blue dot, is there anything matching the defect described
(a mark, stain, damage, a missing or crooked part)? Simple, flat-looking marks count.
"yes": something matching it is there. "no": the area around the dot is clean and complete.
"unclear": the spot isn't shown well enough.
Keep note and rationale short and plain."""


# --------------------------------------------------------------------------- decision table


def decide(claimed: str, vision: VisionStatus) -> tuple[ClosureDecision, ItemStatus | None, str]:
    """(decision, new status or None to keep it, note) per PLAN 7.3."""
    if claimed == "fixed":
        if vision == VisionStatus.fixed:
            return ClosureDecision.signed_off, ItemStatus.closed, NOTE_FIXED
        if vision == VisionStatus.still_visible:
            return ClosureDecision.needs_review, ItemStatus.needs_review, REVIEW_FALSE_FIX
        return ClosureDecision.needs_review, ItemStatus.needs_review, REVIEW_FIX_UNCLEAR
    if claimed == "unclear" and vision == VisionStatus.fixed:
        return ClosureDecision.needs_review, ItemStatus.needs_review, REVIEW_LOOKS_FIXED
    return ClosureDecision.still_open, None, NOTE_STILL_OPEN


VISION = {"yes": VisionStatus.still_visible, "no": VisionStatus.fixed, "unclear": VisionStatus.unclear}


# --------------------------------------------------------------------------- helpers


def _dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        d = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def human_changed_after(item: Item, since: Any) -> bool:
    """A person changed the item after `since` (the re-walk's previous decision)."""
    t0 = _dt(since)
    if t0 is None:
        return False
    for h in item.history or []:
        if (h or {}).get("actor") == HistoryActor.human.value and (_dt(h.get("at")) or t0) > t0:
            return True
    return False


def _room_key(room: str | None) -> str:
    return " ".join((room or "").lower().split())


def candidate_items(session, ctx: StageContext) -> list[Item]:
    """The project's items from earlier walks (not dismissed), in number order. All of them are shown to
    the model (so the prompt doesn't change when a person closes one); only open ones get decisions."""
    q = select(Item).where(Item.project_id == ctx.project_id, col(Item.dismissed_at).is_(None),
                           Item.origin_walk_id != ctx.walk_id)
    return list(session.exec(q.order_by(Item.number)).all())


def actionable(item: Item) -> bool:
    return ItemStatus(item.status) != ItemStatus.closed


def _items_text(items: list[Item]) -> str:
    rows = [{"number": i.number, "title": i.title, "room": i.room, "location_detail": i.location_detail,
             "description": i.description} for i in items]
    return json.dumps(rows, ensure_ascii=False)


# --------------------------------------------------------------------------- frames


def plan_frames(ctx: StageContext, mentions: list[Mention], events: dict[str, dict[str, Any]],
                fps: float) -> dict[str, list[int]]:
    radius = int(round(P.EVIDENCE_SEARCH_S * fps))
    plans = {}
    for m in mentions:
        ev = events.get(m.key)
        if ev:
            lo, hi = ev_mod._part_range(ctx, ev["frame"])
            plans[m.key] = list(range(max(lo, ev["frame"] - radius), min(hi, ev["frame"] + radius) + 1))
        else:
            lo, hi = ev_mod._part_range(ctx, int(round(m.start_s * fps)))
            plans[m.key] = ev_mod.sample_frames(m.start_s, m.end_s, fps, lo, hi, P.VOICE_ONLY_SAMPLES)
    return plans


def after_frames(ctx: StageContext, mentions: list[Mention], events: dict[str, dict[str, Any]],
                 track: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per mention: the re-walk frame (raw + with the dot) under walks/<rewalk>/closures/<key>/."""
    meta = ctx.meta
    w, h, fps = track["sam"]["width"], track["sam"]["height"], float(track["sam"]["fps"])
    sx, sy = meta.scale.sam_to_display_x, meta.scale.sam_to_display_y
    plans = plan_frames(ctx, mentions, events, fps)
    needed = {f for fs in plans.values() for f in fs}
    frames = read_frames(ctx.walk_dir / meta.sam.path, needed)
    masks = ev_mod._hand_masks(track["records"], needed, w, h)
    display = ctx.walk_dir / meta.display.path
    out: dict[str, dict[str, Any]] = {}
    for m in mentions:
        got = {f: frames[f] for f in plans[m.key] if f in frames}
        if not got:
            continue
        ev = events.get(m.key)
        if ev and ev["frame"] in got:
            choice = ev_mod.choose_pointed_frame(got, masks, pointing_frame=ev["frame"],
                                                 fingertip=tuple(ev["fingertip"]), hold_last=ev["hold_frames"][1])
        else:
            choice = ev_mod.choose_sharpest(got)
        t = round(choice.frame / fps, 3)
        d = ctx.walk_dir / "closures" / m.key
        raw = media.extract_frame(display, t, d / "after_raw.jpg", quality=P.EVIDENCE_JPEG_QUALITY)
        pointer = (round(choice.pointer[0] * sx, 1), round(choice.pointer[1] * sy, 1)) if choice.pointer else None
        marked = render_evidence(raw, d / "after_frame.jpg", pointer=pointer)
        out[m.key] = {"t": t, "frame": choice.frame, "raw": raw, "marked": marked, "pointer": pointer,
                      "width": meta.display.width, "height": meta.display.height, "hand_clear": choice.hand_clear}
    return out


# --------------------------------------------------------------------------- model calls


def match_mention(m: Mention, items: list[Item], frame: dict[str, Any] | None) -> StatusMatch:
    lines = [
        f'The inspector said on the re-walk: "{m.quote}"',
        f"Room in effect: {m.room or 'not said'}.",
        "Open items: " + _items_text(items),
    ]
    images = []
    if frame and frame.get("pointer"):
        images.append(frame["marked"])
        lines.append("The photo is the re-walk frame; the blue dot marks where the finger pointed.")
    result, _ = llm.structured(
        StatusMatch,
        [{"role": "system", "content": MAP_SYSTEM}, {"role": "user", "content": "\n".join(lines)}],
        images=images, reasoning_effort="low", max_tokens=MAP_MAX_TOKENS, stage=Stage.resolve_closures.value,
    )
    return result


def look(item: Item, images: list[Path], mode: str) -> FixCheck:
    """mode: "aligned" (photo 1 = first walk outlined, photo 2 = re-walk with the old outline),
    "presence" (re-walk close-up + frame only), "compare" (four photos, no alignment)."""
    system = {"aligned": LOOK_ALIGNED_SYSTEM, "presence": LOOK_PRESENCE_SYSTEM}.get(mode, LOOK_SYSTEM)
    lines = [f"The defect: {item.title}." + (f" {item.description}" if mode == "presence" and item.description
                                              else "")]
    if mode == "compare":
        lines.append("Photo 1: first-walk close-up. Photo 2: re-walk close-up. Photo 3: first-walk frame. "
                     "Photo 4: re-walk frame.")
    result, _ = llm.structured(
        FixCheck,
        [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(lines)}],
        images=images, reasoning_effort="medium", max_tokens=LOOK_MAX_TOKENS, stage=Stage.resolve_closures.value,
    )
    return result


def align_frames(before: Path, after: Path) -> np.ndarray | None:
    """2x3 similarity transform mapping first-walk frame pixels onto the re-walk frame (ORB + RANSAC,
    else phase correlation of gradient images for a pure shift, checked by gradient NCC), or None when
    the two frames can't be lined up."""
    a, b = cv2.imread(str(before)), cv2.imread(str(after))
    if a is None or b is None or a.shape != b.shape:
        return None
    h, w = a.shape[:2]
    ga, gb = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    orb = cv2.ORB_create(nfeatures=2000)
    k1, d1 = orb.detectAndCompute(ga, None)
    k2, d2 = orb.detectAndCompute(gb, None)
    if d1 is not None and d2 is not None and len(k1) >= 20 and len(k2) >= 20:
        pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(d1, d2, k=2)
        good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < 0.75 * p[1].distance]
        if len(good) >= P.CLOSURE_ALIGN_MIN_INLIERS:
            M, inl = cv2.estimateAffinePartial2D(np.float32([k1[m.queryIdx].pt for m in good]),
                                                 np.float32([k2[m.trainIdx].pt for m in good]),
                                                 method=cv2.RANSAC, ransacReprojThreshold=4.0)
            if M is not None and inl is not None and int(inl.sum()) >= P.CLOSURE_ALIGN_MIN_INLIERS:
                scale = float(np.hypot(M[0, 0], M[1, 0]))
                if 0.7 <= scale <= 1.4:
                    return M
    if min(ga.std(), gb.std()) < 2.0:
        return None
    # Plain walls: phase correlation on gradient magnitude (edges of casings, outlets, marks), accepted
    # only if the shifted gradients actually line up (normalized cross-correlation).
    ea, eb = _gradient(ga), _gradient(gb)
    win = cv2.createHanningWindow((w, h), cv2.CV_32F)
    (dx, dy), _ = cv2.phaseCorrelate(ea, eb, win)
    if np.hypot(dx, dy) > P.CLOSURE_ALIGN_MAX_SHIFT_FRAC * w:
        return None
    M = np.float64([[1, 0, dx], [0, 1, dy]])
    ncc = _gradient_ncc(ea, eb, M)
    if ncc < P.CLOSURE_ALIGN_MIN_NCC:
        return None
    if np.hypot(dx, dy) >= 2 and ncc < _gradient_ncc(ea, eb, np.float64([[1, 0, 0], [0, 1, 0]])) + 0.05:
        return None  # the shift doesn't line things up better than not moving at all
    return M


def _gradient(gray: np.ndarray) -> np.ndarray:
    g = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 2)
    return cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))


def _gradient_ncc(ea: np.ndarray, eb: np.ndarray, M: np.ndarray) -> float:
    """NCC between `eb` and `ea` moved by M, over the pixels both frames cover."""
    h, w = ea.shape
    moved = cv2.warpAffine(ea, M.astype(np.float32), (w, h), borderValue=-1)
    valid = moved >= 0
    if valid.sum() < 0.3 * w * h:
        return 0.0
    a, b = moved[valid] - moved[valid].mean(), eb[valid] - eb[valid].mean()
    denom = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / denom) if denom > 0 else 0.0


def pixel_presence(before_raw: Path, after_raw: Path, M: np.ndarray, defect_svg: str | None,
                   defect_box: list[float] | None) -> tuple[str | None, float | None]:
    """Is the outlined mark still there? Contrast of the defect region against a ring around it, in the
    first-walk frame and (after alignment) in the re-walk frame. ("yes" | "no" | None, ratio)."""
    a = cv2.imread(str(before_raw), cv2.IMREAD_GRAYSCALE)
    b = cv2.imread(str(after_raw), cv2.IMREAD_GRAYSCALE)
    if a is None or b is None or a.shape != b.shape:
        return None, None
    h, w = a.shape
    if defect_svg:
        mask = rasterize_svg(defect_svg, w, h)
    elif defect_box:
        mask = np.zeros((h, w), np.uint8)
        x1, y1, x2, y2 = (int(round(v)) for v in defect_box)
        mask[max(0, y1):y2, max(0, x1):x2] = 1
    else:
        return None, None
    if mask.sum() < 9:
        return None, None
    r = P.PIXEL_RING_PX
    inner = cv2.dilate(mask, np.ones((5, 5), np.uint8))
    ring = cv2.dilate(mask, np.ones((2 * r + 1, 2 * r + 1), np.uint8)) - inner

    def contrast(img, m, rg) -> float | None:
        if m.sum() < 9 or rg.sum() < 9:
            return None
        return abs(float(img[m > 0].mean()) - float(img[rg > 0].mean()))

    c_before = contrast(a, mask, ring)
    warp = lambda m: cv2.warpAffine(m, M, (w, h), flags=cv2.INTER_NEAREST)  # noqa: E731
    c_after = contrast(b, warp(mask), warp(ring))
    if c_before is None or c_after is None or c_before < P.PIXEL_MIN_CONTRAST:
        return None, None
    ratio = round(c_after / c_before, 3)
    if ratio >= P.PIXEL_STILL_RATIO:
        return "yes", ratio
    if ratio <= P.PIXEL_GONE_RATIO:
        return "no", ratio
    return None, ratio


def combine(model: str, pixels: str | None) -> tuple[str, str]:
    """Final answer + its source. The mark still being there wins over a model "no"/"unclear" (a wrong
    sign-off is worse than an extra look); a clear "gone" only settles a model "unclear"."""
    if pixels == "yes" and model != "yes":
        return "yes", "pixels"
    if pixels == "no" and model == "unclear":
        return "no", "pixels"
    return model, "model"


def transform_svg(d: str, M: np.ndarray) -> str:
    parts = []
    for poly in svg_polygons(d):
        pts = np.c_[poly, np.ones(len(poly))] @ M.T
        rest = " ".join(f"L{x:.1f} {y:.1f}" for x, y in pts[1:])
        parts.append(f"M{pts[0][0]:.1f} {pts[0][1]:.1f} {rest} Z")
    return " ".join(parts)


def transform_box(box, M: np.ndarray) -> list[float]:
    pts = np.array([[box[0], box[1], 1], [box[2], box[1], 1], [box[0], box[3], 1], [box[2], box[3], 1]]) @ M.T
    return [float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max())]


# --------------------------------------------------------------------------- stage


def _look_inputs(before: Evidence, after_raw: Path, after_crop: Path, after_full: Path, frame: dict[str, Any],
                 out: Path) -> tuple[list[Path], str, np.ndarray | None]:
    """Images, mode and the first-walk → re-walk alignment (or None) for the visual check (see `look`)."""
    root = data_dir()
    if before.defect_svg or before.defect_box:
        M = align_frames(root / before.raw, after_raw)
        if M is not None:
            svg = transform_svg(before.defect_svg, M) if before.defect_svg else None
            box = transform_box([before.defect_box.x1, before.defect_box.y1, before.defect_box.x2,
                                 before.defect_box.y2], M) if before.defect_box else None
            check = render_evidence(after_raw, out / "after_check.jpg", defect_svg=svg)
            cb = crop_box_for(frame["width"], frame["height"], defect_box=box)
            check_crop = render_crop(check, out / "after_check_crop.jpg", cb)
            return [root / before.crop, check_crop], "aligned", M
        return [root / before.crop, after_crop, root / before.image, after_full], "compare", None
    if frame.get("pointer"):
        return [after_crop, after_full], "presence", None
    return [root / before.crop, after_crop, root / before.image, after_full], "compare", None


def _restore(ctx: StageContext, previous: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Undo this re-walk's earlier decisions (except where a person changed the item since).
    Returns the snapshot of pre-re-walk state to keep."""
    snapshot: dict[str, dict[str, Any]] = dict(previous.get("restore") or {})
    with ctx.session() as s:
        for item_id, before in list(snapshot.items()):
            item = s.get(Item, item_id)
            if item is None:
                snapshot.pop(item_id)
                continue
            cl = item.closure or {}
            if cl.get("rewalk_id") != ctx.walk_id:
                continue
            if human_changed_after(item, cl.get("decided_at")):
                snapshot.pop(item_id)  # a person owns this decision now; leave it alone
                continue
            if cl.get("decision") in (ClosureDecision.signed_off.value, ClosureDecision.needs_review.value):
                item.status = ItemStatus(before["status"])  # only statuses this re-walk set are undone
                item.review_reason = before.get("review_reason")  # (a re-send after "still open" stays)
            item.closure = before.get("closure")
            s.add(item)
        s.commit()
    return snapshot


def run(ctx: StageContext) -> StageOutcome:
    if ctx.kind != WalkKind.rewalk:
        return StageOutcome(counts={})
    try:
        previous = ctx.output(Stage.resolve_closures)
    except Exception:  # noqa: BLE001 - first run
        previous = {}
    snapshot = _restore(ctx, previous)
    points_doc = ctx.output(Stage.find_points)
    track = ctx.output(Stage.track_hands)
    mentions = [mention_from_dict({k: v for k, v in m.items() if k != "point_id"}) for m in points_doc["mentions"]]
    events_by_id = {e["id"]: e for e in points_doc["events"]}
    events = {m["key"]: events_by_id[m["point_id"]] for m in points_doc["mentions"] if m.get("point_id")}
    frames = after_frames(ctx, mentions, events, track) if mentions else {}

    with ctx.session() as s:
        items = candidate_items(s, ctx)
        locked = {i.id for i in items if (i.closure or {}).get("rewalk_id") == ctx.walk_id
                  and human_changed_after(i, (i.closure or {}).get("decided_at"))}
        for i in items:
            snapshot.setdefault(i.id, {"status": ItemStatus(i.status).value, "review_reason": i.review_reason,
                                       "closure": i.closure})
    ctx.save_output(Stage.resolve_closures, {"version": 1, "restore": snapshot, "partial": True})
    by_number = {i.number: i for i in items}
    calls_left = P.CLOSURE_MAX_CALLS
    matches: dict[str, dict[str, Any]] = {}
    for m in mentions:
        if not items or calls_left <= 0:
            break
        calls_left -= 1
        try:
            res = match_mention(m, items, frames.get(m.key))
        except (llm.StructuredOutputError, ApiError) as err:
            log.warning("resolve_closures: matching %s failed: %s", m.key, err)
            continue
        item = by_number.get(res.item_number) if res.item_number is not None else None
        matches[m.key] = {"item_number": res.item_number, "claimed": res.claimed,
                          "confidence": round(float(res.confidence), 3), "rationale": res.rationale,
                          "item_id": item.id if item else None}
    # each item takes its most confident mention
    best: dict[str, tuple[Mention, dict[str, Any]]] = {}
    for m in mentions:
        mt = matches.get(m.key)
        if not mt or not mt["item_id"] or mt["confidence"] < P.CLOSURE_MIN_MATCH_CONF:
            continue
        cur = best.get(mt["item_id"])
        if cur is None or mt["confidence"] > cur[1]["confidence"]:
            best[mt["item_id"]] = (m, mt)

    root = data_dir()
    rows: dict[str, Any] = {}
    counts = {d.value: 0 for d in ClosureDecision}
    item_by_id = {i.id: i for i in items}
    for item_id, (m, mt) in best.items():
        item = item_by_id[item_id]
        if item_id in locked or not actionable(item):
            continue
        frame = frames.get(m.key)
        before = Evidence.model_validate(item.evidence) if item.evidence else None
        vision, note_v, after_rel, after_frame_rel, ba_rel, after_t = VisionStatus.not_checked, None, None, None, None, None
        look_mode = None
        if frame is not None:
            out = ctx.item_dir(item_id)
            out.mkdir(parents=True, exist_ok=True)
            after_raw = out / "after_raw.jpg"
            after_raw.write_bytes(Path(frame["raw"]).read_bytes())
            after_full = render_evidence(after_raw, out / "after_frame.jpg", pointer=frame["pointer"])
            cb = crop_box_for(frame["width"], frame["height"], pointer=frame["pointer"])
            after_crop = render_crop(after_full, out / "after.jpg", cb)
            after_rel, after_frame_rel, after_t = rel_to_data(after_crop), rel_to_data(after_full), frame["t"]
            if before is not None and (root / before.crop).is_file():
                ba_rel = rel_to_data(render_before_after(root / before.crop, after_crop, out / "before_after.jpg"))
                if calls_left > 0:
                    calls_left -= 1
                    images, mode, M = _look_inputs(before, after_raw, after_crop, after_full, frame, out)
                    pixels, ratio = (None, None)
                    if M is not None:
                        box = [before.defect_box.x1, before.defect_box.y1, before.defect_box.x2,
                               before.defect_box.y2] if before.defect_box else None
                        pixels, ratio = pixel_presence(root / before.raw, after_raw, M, before.defect_svg, box)
                    try:
                        chk = look(item, images, mode)
                        answer, note_v = chk.defect_still_present, chk.note
                    except (llm.StructuredOutputError, ApiError) as err:
                        log.warning("resolve_closures: visual check of %s failed: %s", item_id, err)
                        answer, note_v = "unclear", NOTE_NO_COMPARE
                    final, source = combine(answer, pixels)
                    vision = VISION[final]
                    look_mode = {"mode": mode, "model": answer, "pixels": pixels, "ratio": ratio, "source": source}
                else:
                    vision, note_v = VisionStatus.unclear, NOTE_NO_COMPARE
            else:
                vision, note_v = VisionStatus.unclear, NOTE_NO_COMPARE
        decision, new_status, note = decide(mt["claimed"], vision)
        counts[decision.value] += 1
        closure = Closure(
            rewalk_id=ctx.walk_id, rewalk_turn_id=m.turn_ids[0], quote=m.quote, after_t=after_t,
            after_image=after_rel, after_frame=after_frame_rel, voice_status=VoiceStatus(mt["claimed"]),
            vision_status=vision, decision=decision, note=note if decision != ClosureDecision.still_open else
            (note_v or note), decided_at=utcnow(),
        )
        with ctx.session() as s:
            row = s.get(Item, item_id)
            old_status = ItemStatus(row.status)
            row.closure = closure.model_dump(mode="json")
            if new_status is not None:
                row.status = new_status
                row.review_reason = note if new_status == ItemStatus.needs_review else None
            action = {ClosureDecision.signed_off: HistoryAction.status_changed,
                      ClosureDecision.needs_review: HistoryAction.flagged}.get(decision, HistoryAction.rewalk_checked)
            add_history(row, HistoryActor.model, action, note=note,
                        **({"field": "status", "from_": old_status.value, "to": new_status.value}
                           if new_status is not None and new_status != old_status else {}))
            s.add(row)
            s.commit()
        rows[item_id] = {"mention": m.key, "match": mt, "vision": vision.value, "vision_note": note_v, "look": look_mode,
                         "decision": decision.value, "before_after": ba_rel}

    # open items in visited rooms that nobody mentioned
    visited = {_room_key(t.get("room")) for t in ctx.output(Stage.classify_turns)["turns"] if t.get("room")}
    for item in items:
        if item.id in best or item.id in locked or not actionable(item) or _room_key(item.room) not in visited:
            continue
        counts[ClosureDecision.not_checked.value] += 1
        with ctx.session() as s:
            row = s.get(Item, item.id)
            row.closure = Closure(rewalk_id=ctx.walk_id, decision=ClosureDecision.not_checked,
                                  note=NOT_CHECKED_ON_REWALK, decided_at=utcnow()).model_dump(mode="json")
            add_history(row, HistoryActor.system, HistoryAction.rewalk_checked, note=NOT_CHECKED_ON_REWALK)
            s.add(row)
            s.commit()
        rows[item.id] = {"decision": ClosureDecision.not_checked.value}

    new_items = _fold_and_write_new(ctx, mentions, matches)
    ctx.save_output(Stage.resolve_closures, {"version": 1, "restore": snapshot, "matches": matches, "items": rows,
                                             "new_items": new_items})
    checked = sum(counts[d] for d in ("signed_off", "still_open", "needs_review"))
    return StageOutcome(
        counts={"checked": checked, "signed_off": counts["signed_off"], "still_open": counts["still_open"],
                "needs_review": counts["needs_review"], "not_checked": counts["not_checked"],
                "new_items": len(new_items)},
        message=f"{counts['signed_off']} signed off, {counts['still_open']} still open, "
                f"{counts['needs_review']} need a look",
    )


def _fold_and_write_new(ctx: StageContext, mentions: list[Mention], matches: dict[str, Any]) -> list[str]:
    """Defect mentions: re-descriptions of an item lose their provisional item; the rest are new items
    and go through write_items → ground_defects → verify."""
    try:
        drafts = {d["mention_key"]: d for d in ctx.output(Stage.fuse)["drafts"]}
    except Exception:  # noqa: BLE001
        return []
    new_ids = []
    with ctx.session() as s:
        for m in mentions:
            d = drafts.get(m.key)
            if d is None:
                continue
            mt = matches.get(m.key) or {}
            row = s.get(Item, d["item_id"])
            if row is None:
                continue
            if mt.get("item_id") and mt["confidence"] >= P.CLOSURE_MIN_MATCH_CONF and not is_protected(row):
                s.delete(row)  # the inspector described an existing item again: folded into its closure
            else:
                new_ids.append(row.id)
        s.commit()
    if new_ids:
        write_items.run(ctx)
        ground.run(ctx)
        verify.run(ctx)
    return new_ids
