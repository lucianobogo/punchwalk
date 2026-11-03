"""Item helpers shared by fuse / write_items / ground_defects / verify.

`is_protected(item)`: a human touched it (any history entry by a human) or it is dispatched or
closed. Pipeline re-runs never overwrite what people see on such items (title, description,
action, trade, room, severity, status, history); only machine fields like the evidence frame and
the video window are refreshed.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import select

from punchwalk.core.enums import HistoryAction, HistoryActor, ItemStatus
from punchwalk.core.models import HistoryEvent, Item, utcnow
from punchwalk.pipeline.context import StageContext

PROTECTED_STATUSES = frozenset({ItemStatus.dispatched, ItemStatus.closed})


def is_protected(item: Item) -> bool:
    if ItemStatus(item.status) in PROTECTED_STATUSES:
        return True
    return any((h or {}).get("actor") == HistoryActor.human.value for h in item.history or [])


def add_history(item: Item, actor: HistoryActor, action: HistoryAction, note: str | None = None,
                **fields: Any) -> None:
    event = HistoryEvent(at=utcnow(), actor=actor, action=action, note=note, **fields)
    item.history = [*(item.history or []), event.model_dump(mode="json", by_alias=True)]


def walk_items(ctx: StageContext, session) -> list[Item]:
    """This walk's items, in number order."""
    return list(session.exec(select(Item).where(Item.origin_walk_id == ctx.walk_id).order_by(Item.number)).all())
