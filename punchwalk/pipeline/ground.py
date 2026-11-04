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

With GROUNDING_V2=1 (PLAN 10.3) steps 1-3 are replaced by punchwalk/pipeline/ground_v2.py (locate the
defect itself, SAM box/point prompts, score, Spark Open check, smoothing); its per-item log goes to
stages/ground_v2.json. Unpointed items first go through the evidence frame picker (PLAN 10.4,
frame_pick.py), and so do items whose locate answered visible: false; picks go to stages/frame_pick.json.
With GROUNDING_MODE=voice_only the v2 path ignores pointing altogether (PLAN 10.5's talking-only run): the
pointer is cleared, so the picker runs for every item and no fingertip prompt or dot is used.
With REGROUND_ALL=1 protected items are re-grounded too: evidence fields and renders only (text, status,
history and closure decisions untouched); a closed item's before_after.jpg is redrawn from its new crop.

Renders (punchwalk/render/evidence.py, quality 85, no text): evidence.jpg (red outline + 30% fill and
the blue "pointed here" dot), crop.jpg (4:3 around pointer + defect, outlines drawn), pointing.jpg
(the pointing frame with the hand outlined in blue). Coordinates are display pixels.
"""

from __future__ import annotations

import logging
import math
import re
import threading
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from punchwalk.clients import calls, sam
from punchwalk.clients.base import ApiError
from punchwalk.config import get_settings
from punchwalk.core.enums import REVIEW_NOT_IN_FRAME, Grounding, Stage
from punchwalk.core.models import Box, Evidence
from punchwalk.pipeline import frame_pick, ground_v2
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


def _ground_v2(root, raw, ev: Evidence, item, extra: dict[str, Any], event: dict[str, Any] | None,
               concept: str | None, v2_rows: dict[str, Any],
               ) -> tuple[Grounding, str | None, list[float] | None, str | None, dict[str, Any]]:
    """GROUNDING_V2=1 (PLAN 10.3): see ground_v2.py. The scored candidates go to stages/ground_v2.json."""
    notes: dict[str, Any] = {}
    pointer = (ev.pointer.x, ev.pointer.y) if ev.pointer else None
    pointing_raw = extra.get("pointing_raw")
    same = bool(event) and extra.get("frame") is not None and extra.get("frame") == event.get("frame")
    pointer = ground_v2.resolve_pointer(
        pointer, aligned=extra.get("aligned"), same_frame=same,
        pointing_raw=(root / pointing_raw) if pointing_raw else None, evidence_raw=raw,
        fingertip_display=tuple(event["fingertip_display"]) if event and event.get("fingertip_display") else None,
        notes=notes)
    hand = ground_v2.hand_mask_at(Path(raw).parents[2], ev.t, ev.width, ev.height) if pointer else None
    res = ground_v2.ground_item(ground_v2.ItemInput(
        item_id=item.id, raw=raw, width=ev.width, height=ev.height, title=item.title,
        description=item.description or "", quote=item.quote, pointer=pointer, number=item.number, hand_mask=hand))
    res.row.update(notes)
    v2_rows[item.id] = res.row
    if res.grounding == Grounding.pointed_only and ev.pointer is None:
        res.grounding = Grounding.none
    if res.grounding == Grounding.none and ev.pointer is not None:
        res.grounding = Grounding.pointed_only  # the dot is still drawn
    box = [round(v, 1) for v in res.defect_box] if res.defect_box else None
    summary = {"v2": True, "source": res.row["result"].get("source"), "reason": res.row["result"].get("reason")}
    return res.grounding, res.defect_svg, box, res.concept or concept, summary


def card_crop_box(ev: Evidence, raw, pointer: tuple[float, float] | None, defect_box: list[float] | None,
                  focus_box: Sequence[float] | None = None) -> tuple[int, int, int, int]:
    """The 4:3 card crop: around the outline (+ dot); with no outline, around `focus_box` (the region Muse Spark
    Open located in a picked photo), else the dot, else the widest crop over the busiest band."""
    if defect_box is None and focus_box is not None:
        return crop_box_for(ev.width, ev.height, defect_box=list(focus_box))
    if pointer is None and defect_box is None:
        return wide_crop_box(raw)
    return crop_box_for(ev.width, ev.height, pointer=pointer, defect_box=defect_box)


def ground_message(v2: bool, counts: dict[str, int], picked: int = 0) -> str:
    """The ground_defects checklist line (a fresh run and reground's refresh write the same words)."""
    total = counts.get("total", 0)
    outlined = counts.get("outlined", 0)
    if not v2:
        return f"SAM 3.1 outlined {outlined} of {total} defects"
    sam_n = counts.get("concept", 0) + counts.get("box", 0)
    spark_n = counts.get("spark_box", 0)
    line = (f"{outlined} of {total} outlined: Muse Spark Open located each defect, SAM 3.1 drew {sam_n} "
            f"{'outline' if sam_n == 1 else 'outlines'}")
    if spark_n:
        line += f" and Muse Spark Open {spark_n} {'box' if spark_n == 1 else 'boxes'}"
    line += ", each checked by Muse Spark Open"
    if picked:
        line += f"; a clearer photo picked for {picked}"
    return line


def ground_counts(groundings: list[Grounding]) -> dict[str, int]:
    c = {g.value: 0 for g in Grounding}
    for g in groundings:
        c[g.value] += 1
    outlined = c["sam_concept"] + c["sam_box"] + c["spark_box"]
    return {"outlined": outlined, "total": len(groundings), "concept": c["sam_concept"], "box": c["sam_box"],
            "spark_box": c["spark_box"], "pointed_only": c["pointed_only"], "none": c["none"]}


def _render_and_save(ctx: StageContext, root, item, ev: Evidence, grounding: Grounding, defect_svg: str | None,
                     defect_box: list[float] | None, concept: str | None, extra: dict[str, Any],
                     event: dict[str, Any] | None, *, save: bool = True,
                     focus_box: Sequence[float] | None = None) -> Evidence:
    """evidence.jpg, crop.jpg and pointing.jpg for the item's current frame + outline; saves Item.evidence
    (save=False: the caller saves it, see `save_evidence`)."""
    meta = ctx.meta
    sx, sy = meta.scale.sam_to_display_x, meta.scale.sam_to_display_y
    raw = root / ev.raw
    pointer = (ev.pointer.x, ev.pointer.y) if ev.pointer else None
    out = ctx.item_dir(item.id)
    evidence_jpg = render_evidence(raw, out / "evidence.jpg", defect_svg=defect_svg, pointer=pointer)
    cb = card_crop_box(ev, raw, pointer, defect_box, focus_box)
    crop_jpg = render_crop(evidence_jpg, out / "crop.jpg", cb)
    pointing_rel = ev.pointing_frame
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
    new_ev = Evidence.model_validate({**ev.model_dump(), **update})
    if save:
        save_evidence(ctx, item, new_ev)
    return new_ev


def save_evidence(ctx: StageContext, item, ev: Evidence) -> None:
    with ctx.session() as s:
        row = s.get(type(item), item.id)
        row.evidence = ev.model_dump(mode="json")
        s.add(row)
        s.commit()


def ground_and_render(ctx: StageContext, root, item, ev: Evidence, wrow: dict[str, Any], extra: dict[str, Any],
                      event: dict[str, Any] | None, v2: bool, v2_rows: dict[str, Any], *, save: bool = True,
                      picked: bool = False) -> tuple[Evidence, Grounding, dict[str, Any]]:
    """Outline one item on its current frame (v1 or v2), render, save (save=False: the caller saves).
    (new evidence, grounding, notes)."""
    raw = root / ev.raw
    pointer = (ev.pointer.x, ev.pointer.y) if ev.pointer else None
    concept = ev.sam_concept or (wrow.get("result") or {}).get("sam_concept")
    r = wrow.get("result") or {}
    defect_svg = defect_box = None
    if v2:
        grounding, defect_svg, defect_box, concept, notes = _ground_v2(root, raw, ev, item, extra, event,
                                                                       concept, v2_rows)
    else:
        grounding, rec, notes = ground_one(raw, ev.width, ev.height, pointer, concept, wrow.get("defect_box"),
                                           f"{r.get('title', '')} {r.get('description', '')}")
        if rec is not None:
            defect_svg = mask_to_frame_svg(rec, ev.width, ev.height, epsilon_px=P.GROUND_SVG_EPSILON_PX) or None
            defect_box = [round(v, 1) for v in frame_box(rec, ev.width, ev.height)]
            if not defect_svg:
                grounding, defect_box = (Grounding.pointed_only if pointer else Grounding.none), None
    focus = None
    if v2 and picked and grounding in (Grounding.pointed_only, Grounding.none):
        # a photo from the picker with no outline: frame the card on what Muse Spark Open located in it
        focus, notes["crop_focus_from"] = ground_v2.crop_focus(_v2_input(item, ev, root), v2_rows.get(item.id))
        notes["crop_focus"] = [round(v, 1) for v in focus] if focus else None
    new_ev = _render_and_save(ctx, root, item, ev, grounding, defect_svg, defect_box, concept, extra, event,
                              save=save, focus_box=focus)
    return new_ev, grounding, {"grounding": grounding.value, "defect_box": defect_box, "concept": concept, **notes}


def _v2_input(item, ev: Evidence, root) -> ground_v2.ItemInput:
    return ground_v2.ItemInput(item_id=item.id, raw=root / ev.raw, width=ev.width, height=ev.height, title=item.title,
                               description=item.description or "", quote=item.quote,
                               pointer=(ev.pointer.x, ev.pointer.y) if ev.pointer else None, number=item.number)


def was_picked(pick_row: dict[str, Any] | None) -> bool:
    """The item's photo came from the frame picker in this run (frame_pick.json `outcome`)."""
    return (pick_row or {}).get("outcome") == "new frame"


def _not_visible(v2_row: dict[str, Any] | None) -> bool:
    loc = (v2_row or {}).get("locate") or {}
    return loc.get("visible") is False


def _inputs(ctx: StageContext) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    written = ctx.output(Stage.write_items)["items"]
    extras = ctx.output(Stage.pick_evidence)["items"]
    events = {e["id"]: e for e in ctx.output(Stage.find_points)["events"]}
    return written, extras, events


def reground_one(ctx: StageContext, item, ev: Evidence) -> tuple[Evidence, Grounding, dict[str, Any]]:
    """v2 on one item's (new) frame, outside the stage loop (verify's frame pass). Merges its row into
    stages/ground_v2.json."""
    root = data_dir()
    written, extras, events = _inputs(ctx)
    event = events.get(item.point_id) if item.point_id else None
    v2_rows: dict[str, Any] = {}
    out = ground_and_render(ctx, root, item, ev, written.get(item.id) or {}, extras.get(item.id, {}), event,
                            True, v2_rows, picked=True)
    ground_v2.save_walk_log(ctx.walk_dir, v2_rows, merge=True)
    return out


@dataclass
class _Outcome:
    """One item's grounding, computed on the pool; the stage saves it in item order."""

    kept: bool = False  # protected: outline and renders left as they are
    ev: Evidence | None = None
    grounding: Grounding = Grounding.none
    notes: dict[str, Any] = field(default_factory=dict)
    v2_rows: dict[str, Any] = field(default_factory=dict)
    pick_rows: dict[str, Any] = field(default_factory=dict)


def run(ctx: StageContext) -> StageOutcome:
    root = data_dir()
    written, extras, events = _inputs(ctx)
    with ctx.session() as s:
        items = [i for i in walk_items(ctx, s) if i.evidence and i.id in written]
    counts = {g.value: 0 for g in Grounding}
    rows: dict[str, Any] = {}
    settings = get_settings()
    v2 = settings.GROUNDING_V2
    reground_all = v2 and settings.REGROUND_ALL
    talking_only = settings.GROUNDING_MODE == "voice_only"
    v2_rows: dict[str, Any] = {}
    pick_rows: dict[str, Any] = {}
    picker: list[frame_pick.Picker] = []
    picker_lock = threading.Lock()

    def get_picker() -> frame_pick.Picker:
        with picker_lock:
            if not picker:
                picker.append(frame_pick.Picker.for_walk(ctx.walk_dir))
            return picker[0]

    def ground_item(item) -> _Outcome:
        """Everything for one item but the database write (runs on the pool)."""
        ev = Evidence.model_validate(item.evidence)
        protected = is_protected(item)
        if protected and not reground_all:  # sent, closed or edited by a person: keep its outline and renders
            return _Outcome(kept=True, grounding=ev.grounding)
        out = _Outcome()
        before = (ev.t, ev.defect_svg)
        if v2 and talking_only and ev.pointer is not None:  # PLAN 10.5 evaluation: every item as unpointed
            ev = ev.model_copy(update={"pointer": None})
        wrow = written.get(item.id) or {}
        extra = extras.get(item.id, {})
        event = events.get(item.point_id) if item.point_id else None
        if v2 and item.closure:  # resolve_closures keeps deciding on the photo + outline it decided on
            frame_pick.pin_before(ctx.item_dir(item.id), Evidence.model_validate(item.evidence).model_dump(mode="json"),
                                  root)
        came_not_in_frame = (item.verification or {}).get("note") == REVIEW_NOT_IN_FRAME
        if v2 and (ev.pointer is None or came_not_in_frame):  # PLAN 10.4: the picker first
            reason = frame_pick.REASON_UNPOINTED if ev.pointer is None else frame_pick.REASON_NOT_IN_FRAME
            ev = frame_pick.pick_and_adopt(ctx, root, item, ev, extra, event, get_picker(), reason, out.pick_rows,
                                           save=False)
        new_ev, grounding, notes = ground_and_render(ctx, root, item, ev, wrow, extra, event, v2, out.v2_rows,
                                                     save=False, picked=was_picked(out.pick_rows.get(item.id)))
        if v2 and _not_visible(out.v2_rows.get(item.id)) and item.id not in out.pick_rows:
            picked = frame_pick.pick_and_adopt(ctx, root, item, new_ev, extra, event, get_picker(),
                                               frame_pick.REASON_NOT_VISIBLE, out.pick_rows, save=False)
            if picked.t != new_ev.t:
                new_ev, grounding, notes = ground_and_render(ctx, root, item, picked, wrow, extra, event, v2,
                                                             out.v2_rows, save=False, picked=True)
        notes["changed"] = (new_ev.t, new_ev.defect_svg) != before
        if protected:
            notes["regrounded_protected"] = True
            if item.closure and notes["changed"]:  # keep before/after framing consistent (after side kept)
                frame_pick.refresh_before_after(root, item.closure, root / new_ev.crop)
        out.ev, out.grounding, out.notes = new_ev, grounding, notes
        return out

    ctx.report({"outlined": 0, "total": len(items)})
    # PLAN 10.9: items are grounded on a small pool (model calls + renders); each result is saved here, in
    # item order, so rows, logs and the database read exactly as a one-at-a-time loop leaves them.
    with closing(calls.map_in_order(ground_item, items)) as results:
        for n, task in enumerate(results, 1):
            item, o = task.item, task.get()
            counts[o.grounding.value] += 1
            if o.kept:
                rows[item.id] = {"grounding": o.grounding.value, "kept": True}
                continue
            save_evidence(ctx, item, o.ev)
            v2_rows.update(o.v2_rows)
            pick_rows.update(o.pick_rows)
            rows[item.id] = o.notes
            ctx.report({"outlined": counts["sam_concept"] + counts["sam_box"] + counts["spark_box"], "done": n,
                        "total": len(items)})
    ctx.save_output(Stage.ground_defects, {"version": 2 if v2 else 1, "items": rows})
    if v2:
        ground_v2.save_walk_log(ctx.walk_dir, v2_rows)
        frame_pick.save_log(ctx.walk_dir, pick_rows, merge=False)
    totals = ground_counts([Grounding(g) for g, n in counts.items() for _ in range(n)])
    picked_n = sum(1 for r in pick_rows.values() if was_picked(r))
    return StageOutcome(counts=totals, message=ground_message(v2, totals, picked_n))
