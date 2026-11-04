"""Evidence frame picker (PLAN 10.4), behind `GROUNDING_V2=1`.

The normal path for unpointed items (not an error), and for items whose frame does not show the
defect: verify said so (REVIEW_NOT_IN_FRAME / frame_shows_defect false), or grounding v2's locate
answered `visible: false`.

1. Window: the item's own stretch of the turn, turn start - V4_WINDOW_BEFORE_S to turn end +
   V4_WINDOW_AFTER_S. When one turn produced several items, each item gets the part of the turn where
   its phrase falls (the position of its words in the turn text, as a fraction of the turn's
   duration), widened by the same margins. Never across a clip boundary.
2. Candidates: SAM frames (10 fps) in the window. Blurry frames (Laplacian variance below
   V4_BLUR_REL x the sharpest, or below V4_MIN_SHARPNESS) and frames with the hand covering more than
   V4_MAX_HAND_FRAC of the frame are skipped; of the rest, the sharpest frame of each of
   V4_MAX_CANDIDATES time bins is kept. The current evidence frame is always one of them.
3. One Muse Spark Open call (<= 8 numbered images, medium effort) picks the frame that shows the
   described defect best, or 0 for none. The pick + rationale go to stages/frame_pick.json.
4. A pick that is not the current frame: raw.jpg and context frames are re-extracted from display.mp4
   at that time (Evidence.t updated; a pointer is carried in with the ORB + RANSAC homography of
   ground_v2 (g), else the item is grounded as unpointed), then the caller re-grounds it with v2.
   No pick, or the current frame: nothing changes (status included). An item is never flagged
   "Needs a look" because nobody pointed.

Closures read the first-walk evidence (raw, crop, outline). Before a closed/re-walked item's frame is
replaced, its old files are copied to items/<id>/pinned/ and described in items/<id>/pinned_before.json;
resolve_closures uses that pinned evidence for its look, so a re-run of the re-walk decides on the same
photo it decided on before. before_after.jpg is redrawn from the new crop (`refresh_before_after`), the
after side kept as it was.

Dry run (reads the DB read-only, writes only under --out):
    GROUNDING_V2=1 PUNCHWALK_CACHE=readwrite uv run python -m punchwalk.pipeline.frame_pick \\
        --data-dir ./data --project prj_demo --items 4,13,1,5,8 --out .verify/v2-pick
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw
from pydantic import BaseModel, Field

from punchwalk.clients import llm
from punchwalk.clients.base import ApiError
from punchwalk.clients.cost import CostCapReached
from punchwalk.core.enums import Stage
from punchwalk.pipeline import ground_v2, media
from punchwalk.pipeline import params as P
from punchwalk.pipeline.evidence import iter_frames
from punchwalk.pipeline.frames import laplacian_variance, rasterize_svg

log = logging.getLogger(__name__)

OUTPUT_NAME = "frame_pick.json"  # DATA_DIR/walks/<walk>/stages/frame_pick.json
PIN_DIR = "pinned"
PIN_FILE = "pinned_before.json"
REASON_UNPOINTED = "unpointed"
REASON_NOT_VISIBLE = "locate: not visible"
REASON_NOT_IN_FRAME = "verify: frame doesn't show the defect"

_WORD_RE = re.compile(r"[a-z0-9']+")
_STOP = frozenset(("a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from", "has", "have", "here",
                   "in", "is", "it", "its", "of", "on", "or", "so", "that", "the", "there", "this", "to", "was",
                   "with"))


# --------------------------------------------------------------------------- 1. windows


def _words(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


def phrase_fraction(quote: str, text: str) -> float | None:
    """Where `text` (a mention's words) starts in `quote` (the turn text), as a 0..1 fraction of the
    quote's words; None when none of its content words appear in the quote."""
    qw = _words(quote)
    if not qw:
        return None
    content = [w for w in _words(text) if w not in _STOP and len(w) > 2]
    hits = []
    for w in content:
        idx = next((i for i, q in enumerate(qw) if len(q) > 2 and q[:5] == w[:5]), None)  # 5-letter stems
        if idx is not None:
            hits.append(idx)
    if not hits:
        return None
    return float(np.median(hits)) / len(qw)


def item_window(start_s: float, end_s: float, f0: float = 0.0, f1: float = 1.0) -> tuple[float, float]:
    """The item's own window inside a turn [start_s, end_s] spanning fractions [f0, f1] of it."""
    dur = max(0.0, end_s - start_s)
    lo = start_s + f0 * dur - P.V4_WINDOW_BEFORE_S
    hi = start_s + f1 * dur + P.V4_WINDOW_AFTER_S
    return max(0.0, lo), hi


def turn_windows(drafts: list[dict[str, Any]]) -> dict[str, tuple[float, float]]:
    """{item_id: (lo_s, hi_s)} from the fuse drafts. Drafts of one mention group (same mention_key
    prefix) split their turn by where each one's words fall in the quote; when that can't be read
    (no words found, or out of order) the turn is split evenly by mention position."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for d in drafts:
        key = str(d.get("mention_key") or d["item_id"])
        groups.setdefault(key.split(".")[0], []).append(d)
    out: dict[str, tuple[float, float]] = {}
    for members in groups.values():
        members.sort(key=lambda d: float(str(d.get("mention_key") or "0.0").split(".")[-1] or 0))
        k = len(members)
        start, end = float(members[0]["quote_start_s"]), float(members[0]["quote_end_s"])
        if k == 1:
            out[members[0]["item_id"]] = item_window(start, end)
            continue
        fr = [phrase_fraction(m.get("quote") or "", m.get("text") or "") for m in members]
        if any(f is None for f in fr) or any(b <= a for a, b in zip(fr, fr[1:], strict=False)):
            fr = [i / k for i in range(k)]
        fr = [0.0, *fr[1:]]  # the first phrase starts the turn (its words may come after a preamble)
        for i, m in enumerate(members):
            out[m["item_id"]] = item_window(start, end, fr[i], fr[i + 1] if i + 1 < k else 1.0)
    return out


# --------------------------------------------------------------------------- 2. candidates


@dataclass
class FrameInfo:
    frame: int
    sharp: float
    hand_frac: float
    rejected: str | None = None

    def as_row(self) -> dict[str, Any]:
        return {"frame": self.frame, "sharp": round(self.sharp, 1), "hand_frac": round(self.hand_frac, 4),
                "rejected": self.rejected}


def filter_frames(sharp: dict[int, float], hand: dict[int, float]) -> list[FrameInfo]:
    """Mark blurry and hand-occluded frames (see module docstring)."""
    if not sharp:
        return []
    best = max(sharp.values())
    out = []
    for f in sorted(sharp):
        info = FrameInfo(f, sharp[f], hand.get(f, 0.0))
        if info.sharp < max(P.V4_MIN_SHARPNESS, P.V4_BLUR_REL * best):
            info.rejected = "blurry"
        elif info.hand_frac > P.V4_MAX_HAND_FRAC:
            info.rejected = "hand in the way"
        out.append(info)
    return out


def select_candidates(infos: list[FrameInfo], current: int | None, n: int | None = None) -> list[int]:
    """Up to n frames: the sharpest usable frame of each of n equal time bins, plus the current frame
    (replacing the candidate nearest to it in time when full)."""
    n = n or P.V4_MAX_CANDIDATES
    usable = [i for i in infos if i.rejected is None]
    if not usable:
        return [current] if current is not None and any(i.frame == current for i in infos) else []
    lo, hi = usable[0].frame, usable[-1].frame
    span = max(1, hi - lo + 1)
    bins: dict[int, FrameInfo] = {}
    for i in usable:
        b = min(n - 1, int((i.frame - lo) * n / span))
        if b not in bins or i.sharp > bins[b].sharp:
            bins[b] = i
    chosen = sorted(b.frame for b in bins.values())
    if current is not None and current not in chosen and any(i.frame == current for i in infos):
        if len(chosen) >= n:
            chosen.remove(min(chosen, key=lambda f: abs(f - current)))
        chosen = sorted([*chosen, current])
    return chosen


# --------------------------------------------------------------------------- 3. pick call


class FramePickAnswer(BaseModel):
    pick: int = Field(description="the number of the photo that shows the described defect best, or 0 if none "
                      "shows it")
    rationale: str = Field(description="one short sentence")


PICK_SYSTEM = (
    "You help a construction punch-list inspector choose the evidence photo for one defect. The photos are "
    "frames from the inspector's video while they described it, each numbered in its top-left corner. Pick "
    "the number of the photo that shows the described defect best: clearly visible, in focus, not hidden by "
    "a hand, close enough to see. If no photo shows the defect, answer 0."
)


def pick_prompt(title: str, description: str, quote: str | None, n: int) -> str:
    lines = [f"Defect: {title}"]
    if description:
        lines.append(f"Details: {description}")
    if quote:
        lines.append(f'The inspector said: "{quote}"')
    lines.append(f"There are {n} photos, numbered 1 to {n}. Which one shows this defect best? 0 if none does.")
    return "\n".join(lines)


def numbered_jpeg(img_bgr: np.ndarray, n: int) -> bytes:
    rgb = Image.fromarray(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(rgb, "RGBA")
    ground_v2._label(d, n, max(36, round(min(rgb.size) / 9)))
    buf = io.BytesIO()
    rgb.save(buf, format="JPEG", quality=llm.JPEG_QUALITY)
    return buf.getvalue()


def ask_pick(title: str, description: str, quote: str | None, images: list[bytes]) -> tuple[int, str, str]:
    result, rec = llm.structured(
        FramePickAnswer,
        [{"role": "system", "content": PICK_SYSTEM},
         {"role": "user", "content": pick_prompt(title, description, quote, len(images))}],
        images=images, reasoning_effort=P.V4_PICK_EFFORT, max_tokens=P.V4_PICK_MAX_TOKENS,
        stage=Stage.pick_evidence.value,
    )
    pick = result.pick if 0 <= result.pick <= len(images) else 0
    return pick, result.rationale[:300], rec.provider


# --------------------------------------------------------------------------- the picker


@dataclass
class PickInput:
    item_id: str
    title: str
    description: str
    quote: str | None
    t: float  # current evidence time (s)
    pointer: tuple[float, float] | None  # current pointer (display px), None when unpointed
    pointing_raw: Path | None = None  # the pointing frame (display px), for mapping the pointer
    fingertip_display: tuple[float, float] | None = None


@dataclass
class PickResult:
    picked: bool  # the model chose a frame
    changed: bool  # ...and it is not the current one
    t: float | None = None
    frame: int | None = None
    raw: Path | None = None  # new raw / context frames (in <out_dir>/pick/)
    context_a: Path | None = None
    context_b: Path | None = None
    pointer: tuple[float, float] | None = None
    row: dict[str, Any] = field(default_factory=dict)


class Picker:
    """Frame picking for one walk. `choose()` writes only under its `out_dir`."""

    def __init__(self, walk_dir: Path, meta, track: dict[str, Any], drafts: list[dict[str, Any]]):
        self.walk_dir = Path(walk_dir)
        self.meta = meta
        self.records = track.get("records") or []
        self.sam_w, self.sam_h = int(track["sam"]["width"]), int(track["sam"]["height"])
        self.fps = float(track["sam"]["fps"])
        self.windows = turn_windows(drafts)
        # One window's frames in memory at a time (PLAN 10.9): ground_defects runs items on a thread pool.
        self._decode_lock = threading.Lock()

    @classmethod
    def for_walk(cls, walk_dir: Path) -> Picker:
        from punchwalk.pipeline.context import read_stage_output
        from punchwalk.pipeline.ingest import IngestMeta

        meta = IngestMeta.load(walk_dir)
        return cls(walk_dir, meta, read_stage_output(walk_dir, Stage.track_hands),
                   read_stage_output(walk_dir, Stage.fuse)["drafts"])

    # -- frames
    def part_range(self, frame: int) -> tuple[int, int]:
        for c in self.meta.clips:
            if c.sam_frame_offset <= frame < c.sam_frame_offset + max(1, c.sam_frame_count):
                return c.sam_frame_offset, c.sam_frame_offset + max(1, c.sam_frame_count) - 1
        return 0, self.meta.sam.frame_count - 1

    def window_frames(self, item_id: str, t: float) -> list[int]:
        lo_s, hi_s = self.windows.get(item_id, (max(0.0, t - P.V4_WINDOW_BEFORE_S), t + P.V4_WINDOW_AFTER_S))
        first, last = self.part_range(int(round(t * self.fps)))
        lo, hi = max(first, int(round(lo_s * self.fps))), min(last, int(round(hi_s * self.fps)))
        return list(range(lo, hi + 1))

    def frame_stream(self, indices: list[int]):
        """(index, SAM frame) for `indices`, one at a time, from one sequential decode."""
        return iter_frames(self.walk_dir / self.meta.sam.path, indices)

    def frames(self, indices: list[int]) -> dict[int, np.ndarray]:
        """Decoded SAM frames for `indices` (nothing is kept between calls)."""
        return dict(self.frame_stream(indices))

    def candidates(self, window: list[int], current: int) -> tuple[list[FrameInfo], list[int], bool, dict[int, np.ndarray]]:
        """(frame infos, chosen candidate frames, current frame decoded?, {candidate: image}) for a window, from
        one sequential decode that keeps only the frames that can still be chosen: the current frame, and
        frames with the hand out of the way that are not below the blur bar of the sharpest frame so far
        (the final bar can only be higher, so a frame dropped here would have been rejected anyway). The
        result is exactly `select_candidates(filter_frames(...))` over every decoded frame."""
        hand = self.hand_fracs(window)
        sharp: dict[int, float] = {}
        kept: dict[int, np.ndarray] = {}
        best = 0.0

        def bar() -> float:
            return max(P.V4_MIN_SHARPNESS, P.V4_BLUR_REL * best)

        with self._decode_lock:
            for f, img in self.frame_stream(window):
                v = laplacian_variance(img)
                sharp[f] = v
                if v > best:
                    best = v
                    kept = {k: im for k, im in kept.items() if k == current or sharp[k] >= bar()}
                if f == current or (hand.get(f, 0.0) <= P.V4_MAX_HAND_FRAC and v >= bar()):
                    kept[f] = img
            infos = filter_frames(sharp, hand)
            has_current = current in sharp
            chosen = select_candidates(infos, current if has_current else None)
            return infos, chosen, has_current, {f: kept[f] for f in chosen}

    def hand_fracs(self, indices: list[int]) -> dict[int, float]:
        want = set(indices)
        masks: dict[int, np.ndarray] = {}
        for r in self.records:
            if r["frame"] in want and r.get("svg"):
                m = rasterize_svg(r["svg"], self.sam_w, self.sam_h)
                masks[r["frame"]] = np.maximum(masks[r["frame"]], m) if r["frame"] in masks else m
        return {f: float(m.mean()) for f, m in masks.items()}

    # -- the whole pick
    def choose(self, item: PickInput, out_dir: Path, reason: str) -> PickResult:
        window = self.window_frames(item.item_id, item.t)
        current = int(round(item.t * self.fps))
        infos, chosen, _, imgs = self.candidates(window, current)
        lo_s, hi_s = (window[0] / self.fps, window[-1] / self.fps) if window else (None, None)
        row: dict[str, Any] = {"reason": reason, "window_s": [lo_s, hi_s], "current_frame": current,
                               "frames_considered": len(infos),
                               "rejected": {"blurry": sum(i.rejected == "blurry" for i in infos),
                                            "hand": sum(i.rejected == "hand in the way" for i in infos)},
                               "candidates": [{"n": n, "frame": f, "t": round(f / self.fps, 2),
                                               "current": f == current} for n, f in enumerate(chosen, 1)]}
        if not chosen:
            row.update(pick=0, outcome="no usable frame")
            return PickResult(False, False, row=row)
        pick_dir = Path(out_dir) / "pick"
        pick_dir.mkdir(parents=True, exist_ok=True)
        images = []
        for n, f in enumerate(chosen, 1):
            data = numbered_jpeg(imgs[f], n)
            (pick_dir / f"cand_{n}.jpg").write_bytes(data)
            images.append(data)
        del imgs
        try:
            pick, why, provider = ask_pick(item.title, item.description, item.quote, images)
        except (ApiError, llm.StructuredOutputError, CostCapReached) as err:
            row.update(pick=0, outcome="pick call failed", error=str(err)[:300])
            return PickResult(False, False, row=row)
        row.update(pick=pick, rationale=why, provider=provider)
        if pick == 0:
            row["outcome"] = "none: keeps its frame"
            return PickResult(False, False, row=row)
        frame = chosen[pick - 1]
        if frame == current:
            row["outcome"] = "current frame is best"
            return PickResult(True, False, t=item.t, frame=frame, row=row)
        t = round(frame / self.fps, 3)
        raw, ca, cb = self.extract(t, frame, pick_dir)
        pointer = None
        if item.pointer is not None:
            notes: dict[str, Any] = {}
            pointer = ground_v2.resolve_pointer(
                item.pointer, aligned=False, same_frame=False, pointing_raw=item.pointing_raw, evidence_raw=raw,
                fingertip_display=item.fingertip_display, notes=notes)
            row["pointer_map"] = notes.get("pointer_map")
        row.update(outcome="new frame", t=t, frame=frame, pointer=None if pointer is None else
                   [round(pointer[0], 1), round(pointer[1], 1)])
        return PickResult(True, True, t=t, frame=frame, raw=raw, context_a=ca, context_b=cb, pointer=pointer, row=row)

    def extract(self, t: float, frame: int, out: Path) -> tuple[Path, Path, Path]:
        display = self.walk_dir / self.meta.display.path
        q = P.EVIDENCE_JPEG_QUALITY
        raw = media.extract_frame(display, t, out / "raw.jpg", quality=q)
        first, last = self.part_range(frame)
        t0, t1 = first / self.fps, last / self.fps
        paths = []
        for name, ct in (("context_a.jpg", max(t0, t - P.CONTEXT_FRAME_OFFSET_S)),
                         ("context_b.jpg", min(t1, t + P.CONTEXT_FRAME_OFFSET_S))):
            try:
                paths.append(media.extract_frame(display, ct, out / name, quality=q))
            except media.MediaError:
                shutil.copyfile(raw, out / name)
                paths.append(out / name)
        return raw, paths[0], paths[1]


# --------------------------------------------------------------------------- 4. applying a pick


def pin_before(item_dir: Path, evidence: dict[str, Any], root: Path) -> Path | None:
    """Copy the evidence files a closure decided on to items/<id>/pinned/ (once) and describe them in
    pinned_before.json. Returns the pin file (None if the item has no files)."""
    pin = Path(item_dir) / PIN_FILE
    if pin.is_file():
        return pin  # the first pin wins: it is what the closure used
    pdir = Path(item_dir) / PIN_DIR
    pdir.mkdir(parents=True, exist_ok=True)
    doc = dict(evidence)
    for key in ("raw", "image", "crop"):
        rel = evidence.get(key)
        if rel and (root / rel).is_file():
            dst = pdir / Path(rel).name
            shutil.copyfile(root / rel, dst)
            doc[key] = str(dst.relative_to(root)) if dst.is_relative_to(root) else str(dst)
    pin.write_text(json.dumps(doc, indent=1, default=str))
    return pin


def pinned_before(item_dir: Path) -> dict[str, Any] | None:
    """The pinned evidence (as an Evidence dict) for resolve_closures, or None."""
    pin = Path(item_dir) / PIN_FILE
    try:
        return json.loads(pin.read_text()) if pin.is_file() else None
    except (OSError, json.JSONDecodeError):
        return None


def adopt(res: PickResult, item_dir: Path) -> None:
    """Move the picked frames into place (raw.jpg, context_a/b.jpg) in the item's folder."""
    for src, name in ((res.raw, "raw.jpg"), (res.context_a, "context_a.jpg"), (res.context_b, "context_b.jpg")):
        if src is not None and Path(src).is_file():
            shutil.copyfile(src, Path(item_dir) / name)


def refresh_before_after(root: Path, closure: dict[str, Any] | None, new_crop: Path) -> Path | None:
    """Redraw a closed item's before_after.jpg from its new before crop, keeping the after side."""
    from punchwalk.render.before_after import render_before_after

    if not closure or not closure.get("before_after") or not closure.get("after_image"):
        return None
    after = root / closure["after_image"]
    if not after.is_file() or not Path(new_crop).is_file():
        return None
    return render_before_after(Path(new_crop), after, root / closure["before_after"])


def save_log(walk_dir: Path, rows: dict[str, Any], merge: bool = True) -> Path:
    """Write stages/frame_pick.json. The ground stage starts it fresh (merge=False); verify's frame pass
    adds to it, so an item already in it was tried in this run."""
    from punchwalk.pipeline.context import STAGES_DIR, write_json

    path = Path(walk_dir) / STAGES_DIR / OUTPUT_NAME
    old: dict[str, Any] = {}
    if merge and path.is_file():
        try:
            old = json.loads(path.read_text()).get("items", {})
        except (OSError, json.JSONDecodeError):
            old = {}
    return write_json(path, {"version": 1, "items": {**old, **rows}})


def load_log(walk_dir: Path) -> dict[str, Any]:
    path = Path(walk_dir) / "stages" / OUTPUT_NAME
    try:
        return json.loads(path.read_text()).get("items", {}) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def pick_and_adopt(ctx, root: Path, item, ev, extra: dict[str, Any], event: dict[str, Any] | None, picker: Picker,
                   reason: str, rows: dict[str, Any], *, save: bool = True):
    """Run the picker for one pipeline item; on a new frame pin the old evidence (items with a closure),
    move the new frames into place, update Evidence.t / pointer and save (save=False: the caller saves the
    final evidence). Returns the (new) Evidence."""
    from punchwalk.core.models import Evidence

    item_dir = ctx.item_dir(item.id)
    pinput = PickInput(
        item.id, item.title, item.description or "", item.quote, ev.t,
        (ev.pointer.x, ev.pointer.y) if ev.pointer else None,
        (root / extra["pointing_raw"]) if extra.get("pointing_raw") else None,
        tuple(event["fingertip_display"]) if event and event.get("fingertip_display") else None)
    res = picker.choose(pinput, item_dir, reason)
    rows[item.id] = res.row
    if not res.changed:
        return ev
    if item.closure:
        pin_before(item_dir, ev.model_dump(mode="json"), root)
    adopt(res, item_dir)
    from punchwalk.pipeline.context import rel_to_data

    new_ev = Evidence.model_validate({
        **ev.model_dump(), "t": res.t, "raw": rel_to_data(item_dir / "raw.jpg"),
        "pointer": None if res.pointer is None else {"x": round(res.pointer[0], 1), "y": round(res.pointer[1], 1)},
    })
    if not save:
        return new_ev
    with ctx.session() as s:
        row = s.get(type(item), item.id)
        row.evidence = new_ev.model_dump(mode="json")
        s.add(row)
        s.commit()
    return new_ev


# --------------------------------------------------------------------------- description rewrite


class DescriptionRewrite(BaseModel):
    description: str = Field(description="the corrected description, 1-2 plain sentences")
    rationale: str = Field(description="one short sentence")


REWRITE_SYSTEM = (
    "You fix one field of a construction punch-list item: its description. The evidence photo changed. "
    "Rewrite the description so it matches both what the inspector said and what the new photo shows (the "
    "defect is outlined in red when it was found). One or two plain sentences about the defect itself; do "
    "not describe the photo, do not invent sizes, causes or extra defects."
)


def rewrite_description(item, image: Path, crop: Path | None) -> tuple[str, str]:
    """(new description, provider). Title, action, trade, room and severity are not asked for."""
    from punchwalk.pipeline.write_items import clean_description

    prompt = "\n".join([
        f'The inspector said: "{item.quote or ""}"',
        f"Title (unchanged): {item.title}",
        f"Current description: {item.description}",
        "Photo 1 is the new evidence frame" + (", photo 2 a close-up." if crop else "."),
    ])
    images = [image] + ([crop] if crop and Path(crop).is_file() else [])
    result, rec = llm.structured(
        DescriptionRewrite,
        [{"role": "system", "content": REWRITE_SYSTEM}, {"role": "user", "content": prompt}],
        images=images, reasoning_effort="low", max_tokens=P.V4_REWRITE_MAX_TOKENS, stage=Stage.verify.value,
    )
    return clean_description(result.description), rec.provider


# --------------------------------------------------------------------------- dry run CLI


def _dry_item(row: dict[str, Any], data_dir: Path, out: Path) -> dict[str, Any]:
    from punchwalk.core.models import Evidence
    from punchwalk.pipeline import verify
    from punchwalk.render.evidence import crop_box_for, draw_evidence, render_crop, wide_crop_box

    ev = Evidence.model_validate(json.loads(row["evidence"]))
    wdir = data_dir / "walks" / row["origin_walk_id"]
    picker = Picker.for_walk(wdir)
    pick_rows = (json.loads((wdir / "stages" / "pick_evidence.json").read_text()).get("items") or {})
    extra = pick_rows.get(row["id"]) or {}
    events = {e["id"]: e for e in json.loads((wdir / "stages" / "find_points.json").read_text()).get("events") or []}
    event = events.get(row.get("point_id") or "")
    reason = REASON_UNPOINTED if ev.pointer is None else REASON_NOT_IN_FRAME
    idir = out / f"{row['number']:03d}_{row['id']}"
    ground_v2.clear_jpgs(idir)
    pin = PickInput(row["id"], row["title"], row.get("description") or "", row.get("quote"), ev.t,
                    (ev.pointer.x, ev.pointer.y) if ev.pointer else None,
                    (data_dir / extra["pointing_raw"]) if extra.get("pointing_raw") else None,
                    tuple(event["fingertip_display"]) if event and event.get("fingertip_display") else None)
    res = picker.choose(pin, idir, reason)
    report: dict[str, Any] = {"number": row["number"], "title": row["title"], "pick": res.row}
    raw = res.raw if res.changed else data_dir / ev.raw
    pointer = res.pointer if res.changed else pin.pointer
    t_now = res.t if res.changed else ev.t
    hand = ground_v2.hand_mask_at(wdir, t_now, ev.width, ev.height) if pointer is not None else None
    g = ground_v2.ground_item(ground_v2.ItemInput(row["id"], raw, ev.width, ev.height, row["title"],
                                                  row.get("description") or "", row.get("quote"), pointer,
                                                  row["number"], hand), idir / "ground")
    report["ground"] = g.row["result"] | {"locate": g.row.get("locate")}
    with Image.open(raw) as im:
        draw_evidence(im, defect_svg=g.defect_svg, pointer=pointer).convert("RGB").save(idir / "evidence.jpg", quality=85)
    cbox = (crop_box_for(ev.width, ev.height, pointer=pointer, defect_box=g.defect_box)
            if pointer is not None or g.defect_box is not None else wide_crop_box(raw))
    render_crop(idir / "evidence.jpg", idir / "crop.jpg", cbox)
    changed = res.changed or (g.defect_svg or None) != (ev.defect_svg or None)
    if changed:
        item_obj = type("I", (), {"title": row["title"], "description": row.get("description") or "",
                                  "quote": row.get("quote"), "action": row["action"], "trade": row["trade"],
                                  "room": row["room"], "location_detail": row["location_detail"],
                                  "severity": row["severity"]})()
        new_ev = ev.model_copy(update={"image": str(idir / "evidence.jpg"), "crop": str(idir / "crop.jpg"),
                                       "pointer": None if pointer is None else {"x": pointer[0], "y": pointer[1]}})
        try:
            check, _ = verify.check_one(item_obj, new_ev)
            report["check"] = check.model_dump()
            if check.frame_shows_defect and not check.consistent:
                desc, _ = rewrite_description(item_obj, idir / "evidence.jpg", idir / "crop.jpg")
                report["description_rewrite"] = desc
        except (ApiError, llm.StructuredOutputError) as err:
            report["check_error"] = str(err)[:300]
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Frame picker + v2 dry run on a few items (writes only under --out).")
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    ap.add_argument("--project", default="prj_demo")
    ap.add_argument("--items", required=True)
    ap.add_argument("--out", type=Path, default=Path(".verify/v2-pick"))
    a = ap.parse_args(argv)
    data_dir = a.data_dir.resolve()
    os.environ["DATA_DIR"] = str(data_dir)
    from punchwalk.config import reset_settings

    reset_settings()
    numbers = [int(x) for x in a.items.split(",") if x.strip()]
    con = sqlite3.connect(f"file:{data_dir / 'punchwalk.sqlite3'}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        f"select * from items where project_id = ? and number in ({','.join('?' * len(numbers))}) order by number",
        [a.project, *numbers]).fetchall()]
    con.close()
    out = a.out.resolve()
    reports = {}
    for r in rows:
        rep = _dry_item(r, data_dir, out)
        reports[r["id"]] = rep
        p = rep["pick"]
        print(f"\n#{r['number']:03d} {r['title']}  [{p['reason']}]")
        print(f"  window {p['window_s']}  considered {p['frames_considered']}  rejected {p['rejected']}  "
              f"candidates {[c['t'] for c in p['candidates']]}")
        print(f"  pick {p.get('pick')} -> {p.get('outcome')}  {p.get('rationale', '')}")
        gr = rep["ground"]
        loc = gr.get("locate") or {}
        print(f"  v2: grounding={gr['grounding']} source={gr.get('source')} box={gr.get('defect_box')} "
              f"locate={loc.get('box')} {loc.get('extent')} {loc.get('concept')!r} {gr.get('reason') or ''}")
        if "check" in rep:
            c = rep["check"]
            print(f"  check: consistent={c['consistent']} frame_shows_defect={c['frame_shows_defect']} {c['issues'][:2]}")
        if "description_rewrite" in rep:
            print(f"  description -> {rep['description_rewrite']}")
    (out / "frame_pick_dry_run.json").write_text(json.dumps(reports, indent=1, default=str))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main())
