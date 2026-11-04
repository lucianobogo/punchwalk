"""Stage `verify` (PLAN 2.3): Muse Spark Open checks each item against its photo and the quote.

Input: the rendered evidence (outline + dot), the 4:3 crop, the item as written, and the inspector's
original words (reasoning_effort=medium). Output (`ItemCheck`): consistent, frame_shows_defect,
issues[], suggested_fix, rationale. Inconsistent, or a frame that doesn't show the defect → needs_review with a short plain note
(REVIEW_NOT_IN_FRAME when the frame doesn't show the defect, else the first issue). A call that
fails → needs_review "Couldn't check this item against the photo." Items are never dropped.
Items whose writing failed in this run (already needs_review) and items a person touched are not
checked; an item flagged by an earlier check that now passes goes back to open.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from punchwalk.clients import llm
from punchwalk.clients.base import ApiError
from punchwalk.core.enums import (
    REVIEW_NOT_IN_FRAME,
    TRADE_LABEL,
    HistoryAction,
    HistoryActor,
    ItemStatus,
    Provider,
    Stage,
    Trade,
)
from punchwalk.core.models import Evidence, Verification, utcnow
from punchwalk.pipeline.context import StageContext, StageOutcome, data_dir
from punchwalk.pipeline.items import add_history, is_protected, walk_items

log = logging.getLogger(__name__)

MAX_TOKENS = 2500
NOTE_MAX = 140
REVIEW_CHECK_FAILED = "Couldn't check this item against the photo."


class ItemCheck(BaseModel):
    consistent: bool = Field(description="true only if the item matches both the words and the photo")
    frame_shows_defect: bool = Field(description="does photo 1 show the defect described?")
    issues: list[str] = Field(description="short plain-words problems, most important first; empty if none")
    suggested_fix: str | None = Field(description="a corrected title or action, or null")
    rationale: str = Field(description="at most 200 characters")


SYSTEM = """You check construction punch-list items before they are sent to a trade.
You get the inspector's original words, the item as written, and its evidence photo: the defect is
outlined in red (when it could be found) and a blue dot marks where the inspector's finger pointed.
Decide whether the title, description and action match BOTH what the inspector said AND what the photo shows.
- consistent: true only if nothing in the item contradicts the words or the photo, and nothing is invented
  (quantities, sizes, causes, extra defects).
- frame_shows_defect: false if the photo does not show the defect described (wrong spot, hidden, blurry).
- issues: short, plain words a project manager understands (for example "The photo shows a wall, not a cabinet").
- suggested_fix: a corrected title or action if that would fix it, else null.
Keep rationale to one short sentence."""


def short_note(text: str) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip().rstrip(".")
    if len(clean) > NOTE_MAX:
        clean = clean[: NOTE_MAX - 1].rsplit(" ", 1)[0] + "…"
    return (clean + ".") if clean and not clean.endswith("…") else clean


def review_note(check: ItemCheck) -> str:
    if not check.frame_shows_defect:
        return REVIEW_NOT_IN_FRAME
    first = next((i for i in check.issues if i.strip()), None)
    return short_note(first) if first else "The item doesn't match the photo or what was said."


def _item_json(item) -> dict[str, Any]:
    return {
        "title": item.title, "description": item.description, "action": item.action,
        "trade": f"{item.trade} ({TRADE_LABEL[Trade(item.trade)]})", "room": item.room,
        "location_detail": item.location_detail, "severity": item.severity,
    }


def check_one(item, ev: Evidence) -> tuple[ItemCheck, str]:
    import json

    root = data_dir()
    images = [root / ev.image]
    if ev.crop and (root / ev.crop).is_file() and ev.crop != ev.image:
        images.append(root / ev.crop)
    prompt = "\n".join([
        f'The inspector said: "{item.quote or ""}"',
        "The item as written: " + json.dumps(_item_json(item), ensure_ascii=False),
        "Photo 1 is the evidence frame" + (" (blue dot = where the finger pointed)." if ev.pointer else
                                           " (nobody pointed; no dot).")
        + (" Photo 2 is a close-up crop of it." if len(images) > 1 else ""),
    ])
    result, rec = llm.structured(
        ItemCheck,
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        images=images,
        reasoning_effort="medium",
        max_tokens=MAX_TOKENS,
        stage=Stage.verify.value,
    )
    return result, rec.provider


def configured_provider() -> Provider:
    from punchwalk.config import get_settings

    return Provider.baseten if get_settings().REASONING_PROVIDER == "baseten" else Provider.meta


def apply_check(row, check: ItemCheck | None, provider: str | None, *, protected: bool = False) -> str:
    """Write one check's result onto an Item row. Returns "matched" | "flagged" | "failed".

    Protected items (REGROUND_ALL re-grounding) only get their `verification` refreshed: status, review
    note and history stay exactly as people left them."""
    if check is None:
        if protected:
            return "failed"
        row.status, row.review_reason = ItemStatus.needs_review, REVIEW_CHECK_FAILED
        row.verification = Verification(consistent=False, note=REVIEW_CHECK_FAILED, provider=configured_provider(),
                                        checked_at=utcnow()).model_dump(mode="json")
        add_history(row, HistoryActor.system, HistoryAction.flagged, note=REVIEW_CHECK_FAILED)
        return "failed"
    prov = Provider(provider)
    if check.consistent and check.frame_shows_defect:
        if not protected and ItemStatus(row.status) == ItemStatus.needs_review:  # flagged by an earlier check
            row.status, row.review_reason = ItemStatus.open, None
        row.verification = Verification(consistent=True, note=None, issues=[], provider=prov,
                                        checked_at=utcnow()).model_dump(mode="json")
        if not protected:
            add_history(row, HistoryActor.model, HistoryAction.verified, note="Matches the photo and what was said")
        return "matched"
    note = review_note(check)
    row.verification = Verification(consistent=False, note=note, issues=check.issues[:5],
                                    provider=prov, checked_at=utcnow()).model_dump(mode="json")
    if not protected:
        row.status, row.review_reason = ItemStatus.needs_review, note
        add_history(row, HistoryActor.model, HistoryAction.flagged, note=note)
    return "flagged"


def status_locked(item) -> bool:
    """Protected items, and items whose status a re-walk decided (they carry a closure): a check may refresh
    their `verification` but never their status, review note or history."""
    return is_protected(item) or bool(item.closure)


def _check_and_apply(ctx: StageContext, item, *, protected: bool) -> tuple[str, dict[str, Any], ItemCheck | None]:
    ev = Evidence.model_validate(item.evidence)
    try:
        check, provider = check_one(item, ev)
        out: dict[str, Any] = {"ok": True, "provider": provider, **check.model_dump()}
    except (llm.StructuredOutputError, ApiError) as err:
        log.warning("verify: %s failed: %s", item.id, err)
        check, provider = None, None
        out = {"ok": False, "error": str(err)[:500]}
    with ctx.session() as s:
        row = s.get(type(item), item.id)
        outcome = apply_check(row, check, provider, protected=protected)
        s.add(row)
        s.commit()
    if protected:
        out["protected"] = True
    return outcome, out, check


def _regrounded_protected(ctx: StageContext) -> set[str]:
    """Protected items whose frame or outline the v2 ground pass changed (REGROUND_ALL)."""
    from punchwalk.pipeline.context import read_stage_output

    try:
        g = read_stage_output(ctx.walk_dir, Stage.ground_defects).get("items", {})
    except Exception:  # noqa: BLE001 - no ground output yet
        return set()
    return {k for k, v in g.items() if v.get("regrounded_protected") and v.get("changed")}


def run(ctx: StageContext) -> StageOutcome:
    from punchwalk.config import get_settings

    settings = get_settings()
    v2 = settings.GROUNDING_V2
    reground_all = v2 and settings.REGROUND_ALL
    with ctx.session() as s:
        items = walk_items(ctx, s)
    written = ctx.output(Stage.write_items)["items"]
    todo = [i for i in items if i.id in written and i.evidence and not is_protected(i)
            and written[i.id].get("ok") and not written[i.id].get("skipped")]
    if reground_all:  # PLAN 10.4: re-check protected items whose frame or outline changed (verification only)
        changed = _regrounded_protected(ctx)
        todo += [i for i in items if i.id in changed and i.evidence and is_protected(i)]
    rows: dict[str, Any] = {}
    tally = {"matched": 0, "flagged": 0, "failed": 0}
    ctx.report({"checked": 0, "total": len(todo)})
    for n, item in enumerate(todo, 1):
        outcome, rows[item.id], _ = _check_and_apply(ctx, item, protected=status_locked(item))
        tally[outcome] += 1
        ctx.report({"checked": n, "total": len(todo)})
    if v2:
        frame_pass(ctx, items, rows, tally, reground_all)
    ctx.save_output(Stage.verify, {"version": 1, "items": rows})
    matched, flagged, failed = tally["matched"], tally["flagged"], tally["failed"]
    return StageOutcome(
        counts={"checked": len(todo), "matched": matched, "flagged": flagged, "failed": failed, "total": len(todo)},
        message=f"{matched} of {len(todo)} items match the photo and what was said",
    )


def _not_in_frame(item, row: dict[str, Any] | None) -> bool:
    if row is not None:
        return bool(row.get("ok")) and row.get("frame_shows_defect") is False
    note = (item.verification or {}).get("note")
    return note == REVIEW_NOT_IN_FRAME


def frame_pass(ctx: StageContext, items, rows: dict[str, Any], tally: dict[str, int], reground_all: bool) -> None:
    """PLAN 10.4 after the checks: items whose frame doesn't show the defect get the frame picker (unless
    the ground stage already tried it in this run). On a new frame: v2 grounding, one more check, and if
    the frame now shows the defect but the words don't match, the description alone is rewritten (never
    for protected items) and checked once more. Nothing picked: the item stays as it is."""
    from punchwalk.pipeline import frame_pick, ground

    root = data_dir()
    tried = frame_pick.load_log(ctx.walk_dir)
    extras = ctx.output(Stage.pick_evidence)["items"]
    events = {e["id"]: e for e in ctx.output(Stage.find_points)["events"]}
    targets = []
    for item in items:
        protected = is_protected(item)
        if not item.evidence or item.id in tried or (protected and not reground_all):
            continue
        if _not_in_frame(item, rows.get(item.id)):
            targets.append(item)
    if not targets:
        return
    picker = frame_pick.Picker.for_walk(ctx.walk_dir)
    pick_rows: dict[str, Any] = {}
    for item in targets:
        protected = status_locked(item)
        ev = Evidence.model_validate(item.evidence)
        event = events.get(item.point_id) if item.point_id else None
        extra = extras.get(item.id, {})
        new_ev = frame_pick.pick_and_adopt(ctx, root, item, ev, extra, event, picker, frame_pick.REASON_NOT_IN_FRAME,
                                           pick_rows)
        if new_ev.t == ev.t:
            continue  # nothing picked (or the current frame is best): status and photo stay
        new_ev, _, _ = ground.reground_one(ctx, item, new_ev)
        if protected and item.closure:
            frame_pick.refresh_before_after(root, item.closure, root / new_ev.crop)
        with ctx.session() as s:
            fresh = s.get(type(item), item.id)
        outcome, out, check = _check_and_apply(ctx, fresh, protected=protected)
        out["frame_pick"] = True
        if check is not None and check.frame_shows_defect and not check.consistent and not protected:
            try:
                desc, _ = frame_pick.rewrite_description(fresh, root / new_ev.image, root / new_ev.crop)
            except (llm.StructuredOutputError, ApiError) as err:
                out["rewrite_error"] = str(err)[:300]
            else:
                with ctx.session() as s:
                    row = s.get(type(item), item.id)
                    old = row.description
                    row.description = desc
                    add_history(row, HistoryActor.model, HistoryAction.edited, field="description", from_=old,
                                to=desc, note="Rewritten to match the new evidence photo")
                    s.add(row)
                    s.commit()
                    fresh = s.get(type(item), item.id)
                out["description_rewritten"] = True
                outcome, recheck, _ = _check_and_apply(ctx, fresh, protected=False)
                out["recheck"] = recheck
        prev = rows.get(item.id)
        if prev and prev.get("ok"):  # replace the first check's tally with the final one
            tally["flagged" if not (prev.get("consistent") and prev.get("frame_shows_defect")) else "matched"] -= 1
        tally[outcome] += 1
        rows[item.id] = {**out, "first_check": prev}
    frame_pick.save_log(ctx.walk_dir, pick_rows, merge=True)
