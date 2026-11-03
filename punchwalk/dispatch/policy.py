"""Automatic sends (PLAN 6.2) and re-sends after a re-walk (PLAN 7.4 seam).

`auto_dispatch_after_processing(session, walk_id)` runs after a first walk is processed (the job
worker calls it; the pipeline's persist stage may call it too, which is safe: it's idempotent):
- open items whose self-check passed (`verification.consistent`) are grouped by trade and sent
  through the channel policy, with the history note "Sent automatically ...: checked against the
  photo and the quote.";
- needs-a-look items, and open items that were never checked, are held, with one history note each
  ("Held for a look: <reason>");
- items already sent (dispatched), signed off or dismissed are left alone, and a held item is never
  noted twice.
A channel failure never breaks the walk: the item stays open with a note saying it wasn't sent.

Turned off with `AUTO_SEND=false` (Settings; default on).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, col, select

from punchwalk.config import get_settings
from punchwalk.core.enums import (
    Channel,
    ClosureDecision,
    HistoryAction,
    HistoryActor,
    ItemStatus,
    Trade,
    WalkKind,
)
from punchwalk.core.models import HistoryEvent, Item, Project, Walk, utcnow
from punchwalk.dispatch import service

log = logging.getLogger("punchwalk.dispatch")

AUTO_NOTE = "checked against the photo and the quote."
HOLD_PREFIX = "Held for a look: "
NOT_SENT_PREFIX = "Not sent automatically: "


def auto_send_enabled() -> bool:
    return get_settings().AUTO_SEND


@dataclass
class AutoSendReport:
    sent: dict[str, list[str]] = field(default_factory=dict)  # dispatch id -> item ids
    held: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)  # trade -> error

    @property
    def sent_items(self) -> list[str]:
        return [i for ids in self.sent.values() for i in ids]


def _consistent(item: Item) -> bool:
    return isinstance(item.verification, dict) and item.verification.get("consistent") is True


def _note(item: Item, text: str) -> None:
    """Append a system `noted` entry unless the item already carries this exact note."""
    if any(isinstance(h, dict) and h.get("note") == text for h in item.history or []):
        return
    entry = HistoryEvent(at=utcnow(), actor=HistoryActor.system, action=HistoryAction.noted, note=text)
    item.history = [*(item.history or []), entry.model_dump(mode="json", by_alias=True)]


def _hold_reason(item: Item) -> str:
    if item.status == ItemStatus.needs_review:
        return item.review_reason or "the self-check flagged it"
    if not isinstance(item.verification, dict):
        return "it hasn't been checked against its photo yet"
    note = item.verification.get("note")
    return note or "the photo and the quote don't agree"


def auto_dispatch_after_processing(session: Session, walk_id: str) -> AutoSendReport:
    report = AutoSendReport()
    walk = session.get(Walk, walk_id)
    if walk is None or walk.kind != WalkKind.initial or not auto_send_enabled():
        return report
    project = session.get(Project, walk.project_id)
    if project is None:
        return report
    items = session.exec(
        select(Item).where(Item.origin_walk_id == walk_id, col(Item.dismissed_at).is_(None))
    ).all()

    by_trade: dict[Trade, list[Item]] = defaultdict(list)
    for item in sorted(items, key=lambda i: i.number):
        if item.status == ItemStatus.open and _consistent(item):
            by_trade[item.trade].append(item)
        elif item.status == ItemStatus.needs_review or (item.status == ItemStatus.open and not _consistent(item)):
            _note(item, HOLD_PREFIX + _hold_reason(item).rstrip(".") + ".")
            session.add(item)
            report.held.append(item.id)
    session.commit()

    for trade, group in by_trade.items():
        option = service.suggested(service.channel_options(project, trade))
        company = service.company_for(project, trade)
        if option.channel == Channel.outbox:
            note = f"Put in the Outbox for {company} automatically: {AUTO_NOTE}"
        else:
            note = f"Sent automatically to {company} {service.via(option)}: {AUTO_NOTE}"
        try:
            dispatch = service.send_items(
                session, project, trade, [i.id for i in group], option.channel, actor=HistoryActor.system, note=note
            )
        except (service.DispatchFailed, service.DispatchRefused) as exc:
            log.warning("auto-send for %s/%s failed: %s", walk_id, trade, exc)
            report.failed[trade.value] = str(exc)
            for item in group:
                session.refresh(item)
                _note(item, NOT_SENT_PREFIX + str(exc))
                session.add(item)
            session.commit()
            continue
        report.sent[dispatch.id] = list(dispatch.item_ids)
    return report


RESEND_NOTE_PREFIX = "Sent again to "
RESEND_NOTE_SUFFIX = " after the re-walk: still open."


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _resent_for_closure(item: Item, closure: dict[str, Any]) -> bool:
    """True when this item was already re-sent after its current re-walk closure was written.

    The anchor is `closure.decided_at` (the re-walk stage always sets it); without one, any earlier
    re-walk re-send counts. A re-walk that is processed again writes a new closure, so it re-sends.
    """
    decided = _as_utc(closure.get("decided_at"))
    for event in item.history or []:
        if not isinstance(event, dict) or event.get("action") != HistoryAction.dispatched.value:
            continue
        note = event.get("note") or ""
        if not (note.startswith(RESEND_NOTE_PREFIX) and note.endswith(RESEND_NOTE_SUFFIX)):
            continue
        at = _as_utc(event.get("at"))
        if decided is None or (at is not None and at >= decided):
            return True
    return False


def resend_open_items(session: Session, project_id: str, rewalk_id: str) -> AutoSendReport:
    """PLAN 7.4: after a re-walk, send each trade its items that are still open, again.

    Picks items whose closure from this re-walk decided `still_open` and that are open or dispatched.
    Idempotent per re-walk: an item already re-sent after that closure was written is skipped, so
    calling this again (a resumed job, a second persist) sends nothing twice.
    """
    report = AutoSendReport()
    project = session.get(Project, project_id)
    if project is None:
        return report
    items = session.exec(select(Item).where(Item.project_id == project_id, col(Item.dismissed_at).is_(None))).all()
    by_trade: dict[Trade, list[Item]] = defaultdict(list)
    for item in sorted(items, key=lambda i: i.number):
        cl = item.closure if isinstance(item.closure, dict) else {}
        if (
            cl.get("rewalk_id") == rewalk_id
            and cl.get("decision") == ClosureDecision.still_open.value
            and item.status in service.SENDABLE
            and not _resent_for_closure(item, cl)
        ):
            by_trade[item.trade].append(item)
    for trade, group in by_trade.items():
        company = service.company_for(project, trade)
        try:
            dispatch = service.send_items(
                session,
                project,
                trade,
                [i.id for i in group],
                None,
                actor=HistoryActor.system,
                note=f"{RESEND_NOTE_PREFIX}{company}{RESEND_NOTE_SUFFIX}",
            )
        except (service.DispatchFailed, service.DispatchRefused) as exc:
            report.failed[trade.value] = str(exc)
            continue
        report.sent[dispatch.id] = list(dispatch.item_ids)
    return report
