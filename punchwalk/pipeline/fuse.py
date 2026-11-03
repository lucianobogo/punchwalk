"""Stage `fuse` (PLAN 1.5, pointing revision): one item draft per defect mention.

Each defect mention (see `mentions.py`) becomes exactly one draft, carrying the pointing event
`find_points` chose for it (longest hold, then closest to the mention's start) or none (voice
only). The item's clip is [turn.start - 2.0 s, turn.end + 1.0 s], clamped to the walk.

Provisional Item rows are created right away (status open, a provisional title from the defect
text, point_id set or null; re-runs keep status/closure/history, see `save_items`) so the API/UI have rows before Phase 2's write_items fills title,
description, action, trade and severity. Item ids are stable (hash of walk id + mention key) and
numbers are project-scoped (an existing item keeps its number on a re-run). Status-update
mentions (re-walk) do not create items; Phase 7 resolves them.

`stages/fuse.json`: {"version", "drafts": [{"item_id", "number", "mention_key", "kind": "pointed"
| "voice_only", "point_id", "turn_ids", "text", "quote", "room", "quote_start_s", "quote_end_s",
"video_start_s", "video_end_s", "title"}]}
"""

from __future__ import annotations

import re
from typing import Any

from sqlmodel import select

from punchwalk.core.enums import HistoryAction, HistoryActor, ItemStatus, Stage
from punchwalk.core.models import Item
from punchwalk.pipeline import params as P
from punchwalk.pipeline.context import StageContext, StageOutcome, stable_id
from punchwalk.pipeline.items import add_history, is_protected
from punchwalk.pipeline.mentions import Mention, mention_from_dict


def provisional_title(text: str, limit: int = P.TITLE_MAX_CHARS) -> str:
    """'upper cabinet left of sink: knob missing.' -> 'Upper cabinet left of sink: knob missing'."""
    clean = re.sub(r"\s+", " ", text or "").strip().strip(" .,;:-")
    if not clean:
        return "Item from the walk"
    clean = clean[0].upper() + clean[1:]
    if len(clean) <= limit:
        return clean
    cut = clean[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return cut + "…"


def item_id_for(walk_id: str, mention_key: str) -> str:
    return stable_id("itm", walk_id, "mention", mention_key)


def build_drafts(walk_id: str, mentions: list[Mention], point_ids: dict[str, str | None],
                 duration_s: float) -> list[dict[str, Any]]:
    drafts = []
    for m in mentions:
        if m.kind != "defect":
            continue
        pid = point_ids.get(m.key)
        drafts.append({
            "item_id": item_id_for(walk_id, m.key),
            "mention_key": m.key,
            "kind": "pointed" if pid else "voice_only",
            "point_id": pid,
            "turn_ids": list(m.turn_ids),
            "text": m.text,
            "quote": m.quote,
            "room": m.room,
            "quote_start_s": round(m.start_s, 3),
            "quote_end_s": round(m.end_s, 3),
            "video_start_s": round(max(0.0, m.start_s - P.VIDEO_BEFORE_S), 3),
            "video_end_s": round(min(duration_s, m.end_s + P.VIDEO_AFTER_S), 3),
            "title": provisional_title(m.text),
        })
    return drafts


REVIEW_ORPHANED = "This item no longer matches anything said on the walk."


def save_items(ctx: StageContext, drafts: list[dict[str, Any]]) -> None:
    """Upsert provisional Item rows (numbers kept).

    New items start open with a provisional title. Existing items keep their status, closure,
    review note and history on a re-run; their text is reset to the provisional draft (write_items
    rewrites it) unless a person touched the item or it is dispatched/closed (`items.is_protected`),
    in which case the text is kept too. Items of this walk that no draft produces any more are not
    deleted: they become needs_review with REVIEW_ORPHANED (protected ones only get a history note).
    """
    with ctx.session() as s:
        project_items = s.exec(select(Item).where(Item.project_id == ctx.project_id)).all()
        by_id = {i.id: i for i in project_items}
        next_number = max((i.number for i in project_items), default=0) + 1
        keep: set[str] = set()
        for d in drafts:
            keep.add(d["item_id"])
            item = by_id.get(d["item_id"])
            if item is None:
                item = Item(id=d["item_id"], project_id=ctx.project_id, number=next_number,
                            origin_walk_id=ctx.walk_id, title=d["title"], status=ItemStatus.open)
                next_number += 1
                add_history(item, HistoryActor.system, HistoryAction.created, note="Created from the walk")
            d["number"] = item.number
            d["protected"] = is_protected(item)
            if not d["protected"]:
                item.title = d["title"]
                item.description = ""
                item.action = ""
                item.room = d["room"]
            item.turn_ids = d["turn_ids"]
            item.quote = d["quote"]
            item.quote_start_s = d["quote_start_s"]
            item.quote_end_s = d["quote_end_s"]
            item.video_start_s = d["video_start_s"]
            item.video_end_s = d["video_end_s"]
            item.point_id = d["point_id"]
            s.add(item)
        for item in project_items:
            if item.origin_walk_id != ctx.walk_id or item.id in keep or item.review_reason == REVIEW_ORPHANED:
                continue
            if is_protected(item):
                add_history(item, HistoryActor.system, HistoryAction.noted, note=REVIEW_ORPHANED)
            else:
                item.status, item.review_reason = ItemStatus.needs_review, REVIEW_ORPHANED
                add_history(item, HistoryActor.system, HistoryAction.flagged, note=REVIEW_ORPHANED)
            s.add(item)
        s.commit()


def run(ctx: StageContext) -> StageOutcome:
    points_doc = ctx.output(Stage.find_points)
    mentions = [mention_from_dict({k: v for k, v in m.items() if k != "point_id"}) for m in points_doc["mentions"]]
    point_ids = {m["key"]: m.get("point_id") for m in points_doc["mentions"]}
    drafts = build_drafts(ctx.walk_id, mentions, point_ids, ctx.meta.duration_s)
    save_items(ctx, drafts)
    ctx.save_output(Stage.fuse, {"version": 1, "drafts": drafts})
    pointed = sum(1 for d in drafts if d["kind"] == "pointed")
    return StageOutcome(
        counts={"items": len(drafts), "pointed": pointed, "voice_only": len(drafts) - pointed},
        message=f"Matched {pointed} of {len(drafts)} defects to a pointing gesture",
    )
