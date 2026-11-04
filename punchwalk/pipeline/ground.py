"""Stage `ground_defects` (PLAN 2.2): SAM 3.1 outlines the defect, then the evidence is rendered.

Per item, in order, recording which worked in `Evidence.grounding`:
  1. `sam_concept`: SAM concept prompt (Spark Open's `sam_concept`) on the evidence frame; the instance
     nearest the fingertip, within GROUND_RADIUS_FRAC x frame width (no pointer: nearest the center of
     Spark Open's defect_box, else the only instance). Instances covering more than
     GROUND_MAX_AREA_FRAC of the frame (a whole wall) are ignored.
  2. `sam_box`: SAM box prompt from Spark Open's defect_box grown 10%, if the box comes within
     GROUND_BOX_MAX_DIST_FRAC x width of the fingertip (3% for missing / loose / crooked defects, whose
     spot is exactly where the finger points), or there is no pointer: a box far from the finger is on
     the wrong object. When the concept instance is > 4x that box (the whole object), the box goes first.
  3. `pointed_only` (a pointer, no outline) or `none` (voice-only, no outline, no dot).

Items that are protected (sent, closed or edited by a person) keep their outline and renders.

Renders (punchwalk/render/evidence.py, quality 85, no text): evidence.jpg (red outline + 30% fill and
the blue "pointed here" dot), crop.jpg (4:3 around pointer + defect, outlines drawn), pointing.jpg
(the pointing frame with the hand outlined in blue). Coordinates are display pixels.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

import numpy as np

from punchwalk.clients import sam
from punchwalk.clients.base import ApiError
from punchwalk.core.enums import Grounding, Stage
from punchwalk.core.models import Box, Evidence
from punchwalk.pipeline import params as P
from punchwalk.pipeline.context import StageContext, StageOutcome, data_dir, rel_to_data
from punchwalk.pipeline.frames import scale_svg_path
from punchwalk.pipeline.items import is_protected, walk_items
from punchwalk.render.evidence import (
    crop_box_for,
    render_crop,
    render_evidence,
    render_pointing,
    wide_crop_box,
)
from punchwalk.render.masks import frame_box, mask_to_frame_raster, mask_to_frame_svg

log = logging.getLogger(__name__)


def point_to_box(x: float, y: float, box) -> float:
    x1, y1, x2, y2 = box
    dx = max(x1 - x, 0.0, x - x2)
    dy = max(y1 - y, 0.0, y - y2)
    return math.hypot(dx, dy)


def point_to_mask(x: float, y: float, mask: np.ndarray) -> float:
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return math.inf
    return float(np.sqrt(((xs + 0.5 - x) ** 2 + (ys + 0.5 - y) ** 2).min()))


def expand_box(box, frac: float, w: int, h: int) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = box
    gx, gy = (x2 - x1) * frac / 2, (y2 - y1) * frac / 2
    return (int(max(0, math.floor(x1 - gx))), int(max(0, math.floor(y1 - gy))),
            int(min(w, math.ceil(x2 + gx))), int(min(h, math.ceil(y2 + gy))))


def pick_instance(records: list[sam.SamRecord], w: int, h: int, anchor: tuple[float, float] | None,
                  radius: float) -> sam.SamRecord | None:
    """The instance nearest `anchor` within `radius` (no anchor: the only plausible instance)."""
    plausible = []
    for r in records:
        mask = mask_to_frame_raster(r, w, h)
        area = float(mask.mean()) if mask.size else 0.0
        if r.mask is None or area <= 0 or area > P.GROUND_MAX_AREA_FRAC:
            continue
        plausible.append((r, mask))
    if anchor is None:
        return plausible[0][0] if len(plausible) == 1 else None
    best = min(plausible, key=lambda rm: point_to_mask(anchor[0], anchor[1], rm[1]), default=None)
    if best is None or point_to_mask(anchor[0], anchor[1], best[1]) > radius:
        return None
    return best[0]


_ABSENT_RE = re.compile(r"\b(missing|loose|crooked|twisted|detached|not (attached|tight|placed|seated|secured))\b",
                        re.IGNORECASE)


def is_absent_kind(text: str | None) -> bool:
    """Missing / loose / crooked defects: the defect is the spot itself (an empty hole, a gap), so the
    finger points right at it and a box away from the dot is on a neighbouring object."""
    return bool(_ABSENT_RE.search(text or ""))


def box_ok(defect_box: list[float] | None, pointer: tuple[float, float] | None, w: int, absent: bool) -> bool:
    if not defect_box:
        return False
    if pointer is None:
        return True
    frac = P.GROUND_BOX_MAX_DIST_FRAC_ABSENT if absent else P.GROUND_BOX_MAX_DIST_FRAC
    return point_to_box(pointer[0], pointer[1], defect_box) <= frac * w


def _box_area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _box_prompt(raw, defect_box: list[float], w: int, h: int, notes: dict[str, Any]) -> sam.SamRecord | None:
    box = expand_box(defect_box, P.GROUND_BOX_EXPAND, w, h)
    try:
        res = sam.boxes_on_image(raw, [box])
        recs = [r for r in res.all_records() if r.mask is not None]
        notes["box_instances"] = len(recs)
        return max(recs, key=lambda r: r.confidence or 0.0) if recs else None
    except (ApiError, ValueError) as err:
        notes["box_error"] = str(err)[:300]
        return None


def ground_one(raw, w: int, h: int, pointer: tuple[float, float] | None, concept: str | None,
               defect_box: list[float] | None, defect_text: str | None = None,
               ) -> tuple[Grounding, sam.SamRecord | None, dict[str, Any]]:
    """Try concept, then box. Returns (grounding, record or None, notes for the stage output)."""
    radius = P.GROUND_RADIUS_FRAC * w
    notes: dict[str, Any] = {}
    anchor = pointer
    if anchor is None and defect_box:
        anchor = ((defect_box[0] + defect_box[2]) / 2, (defect_box[1] + defect_box[3]) / 2)
    usable_box = box_ok(defect_box, pointer, w, is_absent_kind(defect_text))
    if defect_box and not usable_box:
        notes["box_skipped"] = "defect_box is too far from where the finger pointed"
    box_tried = False
    if concept:
        try:
            res = sam.concept_on_image(raw, concept)
            recs = res.all_records()
            notes["concept_instances"] = len(recs)
            rec = pick_instance(recs, w, h, anchor, radius)
            if rec is not None:
                if usable_box and _box_area(rec.box) > P.GROUND_CONCEPT_MAX_BOX_RATIO * _box_area(defect_box):
                    # the concept found the whole object (a cooktop for a damaged burner): try the box first
                    notes["concept_too_big"] = round(_box_area(rec.box) / max(1.0, _box_area(defect_box)), 1)
                    box_tried = True
                    brec = _box_prompt(raw, defect_box, w, h, notes)
                    if brec is not None:
                        return Grounding.sam_box, brec, notes
                return Grounding.sam_concept, rec, notes
        except (ApiError, ValueError) as err:
            notes["concept_error"] = str(err)[:300]
    if usable_box and not box_tried:
        brec = _box_prompt(raw, defect_box, w, h, notes)
        if brec is not None:
            return Grounding.sam_box, brec, notes
    return (Grounding.pointed_only if pointer is not None else Grounding.none), None, notes


def run(ctx: StageContext) -> StageOutcome:
    root = data_dir()
    written = ctx.output(Stage.write_items)["items"]
    extras = ctx.output(Stage.pick_evidence)["items"]
    events = {e["id"]: e for e in ctx.output(Stage.find_points)["events"]}
    meta = ctx.meta
    sx, sy = meta.scale.sam_to_display_x, meta.scale.sam_to_display_y
    with ctx.session() as s:
        items = [i for i in walk_items(ctx, s) if i.evidence and i.id in written]
    counts = {g.value: 0 for g in Grounding}
    rows: dict[str, Any] = {}
    ctx.report({"outlined": 0, "total": len(items)})
    for n, item in enumerate(items, 1):
        ev = Evidence.model_validate(item.evidence)
        if is_protected(item):  # sent, closed or edited by a person: keep its outline and renders
            counts[ev.grounding.value] += 1
            rows[item.id] = {"grounding": ev.grounding.value, "kept": True}
            continue
        raw = root / ev.raw
        pointer = (ev.pointer.x, ev.pointer.y) if ev.pointer else None
        wrow = written.get(item.id) or {}
        concept = ev.sam_concept or (wrow.get("result") or {}).get("sam_concept")
        r = wrow.get("result") or {}
        grounding, rec, notes = ground_one(raw, ev.width, ev.height, pointer, concept, wrow.get("defect_box"),
                                           f"{r.get('title', '')} {r.get('description', '')}")
        defect_svg = defect_box = None
        if rec is not None:
            defect_svg = mask_to_frame_svg(rec, ev.width, ev.height, epsilon_px=P.GROUND_SVG_EPSILON_PX) or None
            defect_box = [round(v, 1) for v in frame_box(rec, ev.width, ev.height)]
            if not defect_svg:
                grounding, defect_box = (Grounding.pointed_only if pointer else Grounding.none), None
        counts[grounding.value] += 1

        out = ctx.item_dir(item.id)
        evidence_jpg = render_evidence(raw, out / "evidence.jpg", defect_svg=defect_svg, pointer=pointer)
        if pointer is None and defect_box is None:  # nothing located: widest 4:3 crop over the busiest band
            cb = wide_crop_box(raw)
        else:
            cb = crop_box_for(ev.width, ev.height, pointer=pointer, defect_box=defect_box)
        crop_jpg = render_crop(evidence_jpg, out / "crop.jpg", cb)
        pointing_rel = ev.pointing_frame
        extra = extras.get(item.id, {})
        event = events.get(item.point_id) if item.point_id else None
        if extra.get("pointing_raw") and (root / extra["pointing_raw"]).is_file():
            hand_svg = scale_svg_path(event["svg"], sx, sy) if event and event.get("svg") else None
            pointing_rel = rel_to_data(render_pointing(root / extra["pointing_raw"], out / "pointing.jpg",
                                                       hand_svg=hand_svg))
        update = {
            "image": rel_to_data(evidence_jpg),
            "crop": rel_to_data(crop_jpg),
            "crop_box": Box(x1=cb[0], y1=cb[1], x2=cb[2], y2=cb[3]),
            "defect_svg": defect_svg,
            "defect_box": Box(x1=defect_box[0], y1=defect_box[1], x2=defect_box[2], y2=defect_box[3])
            if defect_box else None,
            "grounding": grounding,
            "sam_concept": concept,
            "pointing_frame": pointing_rel,
        }
        new_ev = Evidence.model_validate({**ev.model_dump(), **{k: v for k, v in update.items()}})
        with ctx.session() as s:
            row = s.get(type(item), item.id)
            row.evidence = new_ev.model_dump(mode="json")
            s.add(row)
            s.commit()
        rows[item.id] = {"grounding": grounding.value, "defect_box": defect_box, "concept": concept, **notes}
        ctx.report({"outlined": counts["sam_concept"] + counts["sam_box"], "done": n, "total": len(items)})
    ctx.save_output(Stage.ground_defects, {"version": 1, "items": rows})
    outlined = counts["sam_concept"] + counts["sam_box"]
    return StageOutcome(
        counts={"outlined": outlined, "total": len(items), "concept": counts["sam_concept"], "box": counts["sam_box"],
                "pointed_only": counts["pointed_only"], "none": counts["none"]},
        message=f"SAM 3.1 outlined {outlined} of {len(items)} defects",
    )
