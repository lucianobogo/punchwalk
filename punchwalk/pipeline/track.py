"""Stage `track_hands` (PLAN 1.2, pointing revision): SAM 3.1 hand tracking on sam.mp4.

The concept is `settings.SAM_HAND_CONCEPT` (default "hand"). A multi-clip walk runs SAM once per
part (`sam_part_<k>.mp4`): SAM object ids reset per part, so ids are namespaced `"<part>:<id>"`
(`a`, `b`, ...) and local frame indices are offset by the part's `sam_frame_offset`. Frame
indices are always read from the stream marker (they can skip).

`stages/track_hands.json` (compact; the overlay endpoint reads it):

    {"version": 1, "concept": "hand",
     "sam": {"width", "height", "fps", "frame_count"},          # SAM-frame pixel space
     "parts": [{"order", "label", "path", "frame_offset", "frame_count", "records", "objects",
                "complete", "incomplete_reason", "diagnostics", "cached", "cache_key", "raw"}],
     "records": [{"frame", "object_id", "box": [x1, y1, x2, y2], "confidence", "svg"}]}

`frame` is the global SAM frame (t = frame / fps); box and svg are SAM-frame pixels (scale by
meta.json `scale` for display pixels). The raw SAM stream text of each part is kept next to it in
`stages/track_hands.<label>.txt`.

Nothing to point at (PLAN 10.9): when classify_turns found nothing to look for (no defect and no status
update was said: a silent walk, or only small talk), SAM is not called at all. The document has no parts
and no records (`"skipped": "nothing said to point at"`), find_points finds nothing, and the stage's
message tells the person what to do (NO_NARRATION / NO_DEFECTS); the processing checklist shows it.
"""

from __future__ import annotations

import logging
import string
from pathlib import Path
from typing import Any

from punchwalk.clients import sam
from punchwalk.config import get_settings
from punchwalk.core.enums import Stage
from punchwalk.pipeline.context import STAGES_DIR, StageContext, StageOutcome
from punchwalk.pipeline.ingest import IngestMeta
from punchwalk.pipeline.params import DEFAULT_HAND_CONCEPT, SVG_EPSILON_PX
from punchwalk.render.masks import mask_to_frame_svg

log = logging.getLogger(__name__)
TRACK_VERSION = 1
NO_NARRATION = "We didn't hear any narration. Say each defect out loud and upload again."
NO_NARRATION_REWALK = "We didn't hear any narration. Say each item's status out loud and upload again."
NO_DEFECTS = "We didn't hear a defect described. Point at each one, say what's wrong, and upload again."


def hand_concept() -> str:
    return get_settings().SAM_HAND_CONCEPT or DEFAULT_HAND_CONCEPT


def part_label(order: int) -> str:
    return string.ascii_lowercase[order] if order < 26 else f"p{order}"


def _record_row(rec: sam.SamRecord, frame: int, label: str, w: int, h: int) -> dict[str, Any]:
    x1, y1, x2, y2 = rec.box
    sx, sy = (w / rec.media_size[0], h / rec.media_size[1]) if rec.media_size else (1.0, 1.0)
    return {
        "frame": frame,
        "object_id": f"{label}:{rec.object_id}",
        "box": [round(x1 * sx, 1), round(y1 * sy, 1), round(x2 * sx, 1), round(y2 * sy, 1)],
        "confidence": round(float(rec.confidence), 4) if rec.confidence is not None else None,
        "svg": mask_to_frame_svg(rec, w, h, epsilon_px=SVG_EPSILON_PX) if rec.mask is not None else "",
    }


def track_walk(walk_dir: Path, meta: IngestMeta, concept: str | None = None) -> dict[str, Any]:
    """Run SAM (cached) on every part of the walk and return the track_hands document.

    Also writes each part's raw stream text to `stages/track_hands.<label>.txt`.
    """
    concept = concept or hand_concept()
    w, h = meta.sam.width, meta.sam.height
    stages_dir = Path(walk_dir) / STAGES_DIR
    stages_dir.mkdir(parents=True, exist_ok=True)
    parts: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for clip in meta.clips:
        label = part_label(clip.order)
        video = Path(walk_dir) / clip.sam_part_path
        res = sam.concept_on_video(video, concept, media_size=(w, h))
        raw_name = f"{Stage.track_hands.value}.{label}.txt"
        (stages_dir / raw_name).write_text(res.raw_text, encoding="utf-8")
        n_before = len(records)
        dropped = 0
        for local, recs in sorted(res.frames.items()):
            if local < 0 or (clip.sam_frame_count and local >= clip.sam_frame_count):
                dropped += len(recs)
                continue
            for rec in recs:
                records.append(_record_row(rec, local + clip.sam_frame_offset, label, w, h))
        diagnostics = list(res.diagnostics)
        if dropped:
            diagnostics.append({"severity": "warning", "code": "frame_out_of_range",
                                "message": f"{dropped} records had frame indices outside the part"})
        for d in diagnostics:
            log.warning("track_hands part %s: %s %s", label, d.get("code"), d.get("message"))
        if not res.complete:
            log.warning("track_hands part %s: stream incomplete (%s)", label, res.incomplete_reason)
        parts.append({
            "order": clip.order,
            "label": label,
            "path": clip.sam_part_path,
            "frame_offset": clip.sam_frame_offset,
            "frame_count": clip.sam_frame_count,
            "records": len(records) - n_before,
            "objects": len(res.object_ids),
            "complete": res.complete,
            "incomplete_reason": res.incomplete_reason,
            "diagnostics": diagnostics,
            "cached": res.cached,
            "cache_key": res.record.cache_key if res.record else None,
            "raw": f"{STAGES_DIR}/{raw_name}",
        })
    records.sort(key=lambda r: (r["frame"], r["object_id"]))
    return {
        "version": TRACK_VERSION,
        "concept": concept,
        "sam": {"width": w, "height": h, "fps": meta.sam.fps, "frame_count": meta.sam.frame_count},
        "parts": parts,
        "records": records,
    }


def nothing_to_point_at(ctx: StageContext) -> tuple[bool, int]:
    """(True when classify_turns found no defect and no status update, number of turns heard). A walk
    without a classify output (older runs) is tracked as before."""
    from punchwalk.pipeline.context import StageInputMissing
    from punchwalk.pipeline.mentions import build_mentions

    try:
        turns = ctx.output(Stage.classify_turns)["turns"]
    except (StageInputMissing, FileNotFoundError, KeyError):
        return False, -1
    return not build_mentions(turns), len(turns)


def empty_track(meta: IngestMeta, concept: str | None = None) -> dict[str, Any]:
    w, h = meta.sam.width, meta.sam.height
    return {
        "version": TRACK_VERSION,
        "concept": concept or hand_concept(),
        "sam": {"width": w, "height": h, "fps": meta.sam.fps, "frame_count": meta.sam.frame_count},
        "parts": [],
        "records": [],
        "skipped": "nothing said to point at",
    }


def run(ctx: StageContext) -> StageOutcome:
    skip, heard = nothing_to_point_at(ctx)
    if skip:  # a silent walk: no SAM call (it would cost ~$0.04 and ~30 s and find nothing to use)
        from punchwalk.core.enums import WalkKind

        ctx.save_output(Stage.track_hands, empty_track(ctx.meta))
        silent = NO_NARRATION_REWALK if ctx.kind == WalkKind.rewalk else NO_NARRATION
        msg = NO_DEFECTS if heard else silent
        log.info("track_hands: %s turn(s), nothing to point at; SAM not called", heard)
        return StageOutcome(counts={"frames": 0, "hands": 0, "parts": 0, "skipped": 1}, message=msg)
    doc = track_walk(ctx.walk_dir, ctx.meta)
    ctx.save_output(Stage.track_hands, doc)
    objects = len({r["object_id"] for r in doc["records"]})
    frames = len({r["frame"] for r in doc["records"]})
    incomplete = sum(1 for p in doc["parts"] if not p["complete"])
    msg = f"SAM 3.1 found a hand in {frames} frames"
    if incomplete:
        msg += f" ({incomplete} part(s) returned an incomplete stream)"
    return StageOutcome(counts={"frames": frames, "hands": objects, "parts": len(doc["parts"])}, message=msg)
