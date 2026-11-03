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


def run(ctx: StageContext) -> StageOutcome:
    with ctx.session() as s:
        items = walk_items(ctx, s)
    written = ctx.output(Stage.write_items)["items"]
    todo = [i for i in items if i.id in written and i.evidence and not is_protected(i)
            and written[i.id].get("ok") and not written[i.id].get("skipped")]
    rows: dict[str, Any] = {}
    matched = flagged = failed = 0
    ctx.report({"checked": 0, "total": len(todo)})
    for n, item in enumerate(todo, 1):
        ev = Evidence.model_validate(item.evidence)
        try:
            check, provider = check_one(item, ev)
            rows[item.id] = {"ok": True, "provider": provider, **check.model_dump()}
        except (llm.StructuredOutputError, ApiError) as err:
            log.warning("verify: %s failed: %s", item.id, err)
            check, provider = None, None
            rows[item.id] = {"ok": False, "error": str(err)[:500]}
        with ctx.session() as s:
            row = s.get(type(item), item.id)
            if check is None:
                failed += 1
                row.status, row.review_reason = ItemStatus.needs_review, REVIEW_CHECK_FAILED
                row.verification = Verification(consistent=False, note=REVIEW_CHECK_FAILED, provider=Provider.baseten,
                                                checked_at=utcnow()).model_dump(mode="json")
                add_history(row, HistoryActor.system, HistoryAction.flagged, note=REVIEW_CHECK_FAILED)
            else:
                prov = Provider(provider)
                if check.consistent and check.frame_shows_defect:
                    matched += 1
                    if ItemStatus(row.status) == ItemStatus.needs_review:  # flagged by an earlier check
                        row.status, row.review_reason = ItemStatus.open, None
                    row.verification = Verification(consistent=True, note=None, issues=[], provider=prov,
                                                    checked_at=utcnow()).model_dump(mode="json")
                    add_history(row, HistoryActor.model, HistoryAction.verified,
                                note="Matches the photo and what was said")
                else:
                    flagged += 1
                    note = review_note(check)
                    row.status, row.review_reason = ItemStatus.needs_review, note
                    row.verification = Verification(consistent=False, note=note, issues=check.issues[:5],
                                                    provider=prov, checked_at=utcnow()).model_dump(mode="json")
                    add_history(row, HistoryActor.model, HistoryAction.flagged, note=note)
            s.add(row)
            s.commit()
        ctx.report({"checked": n, "total": len(todo)})
    ctx.save_output(Stage.verify, {"version": 1, "items": rows})
    return StageOutcome(
        counts={"checked": len(todo), "matched": matched, "flagged": flagged, "failed": failed, "total": len(todo)},
        message=f"{matched} of {len(todo)} items match the photo and what was said",
    )
