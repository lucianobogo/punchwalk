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

Turned off with `AUTO_SEND=0` (env; default on).
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field

from sqlmodel import Session, col, select

from punchwalk.core.enums import ClosureDecision, HistoryAction, HistoryActor, ItemStatus, Trade, WalkKind
from punchwalk.core.models import HistoryEvent, Item, Project, Walk, utcnow
from punchwalk.dispatch import service

log = logging.getLogger("punchwalk.dispatch")

AUTO_NOTE = "checked against the photo and the quote."
HOLD_PREFIX = "Held for a look: "
NOT_SENT_PREFIX = "Not sent automatically: "


def auto_send_enabled() -> bool:
    return os.environ.get("AUTO_SEND", "1").strip().lower() not in ("0", "false", "no", "off")


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
        via = {"slack": f"in Slack ({option.destination})", "email": f"by email ({option.destination})"}.get(
            option.channel.value, "to the outbox"
        )
        note = f"Sent automatically to {company} {via}: {AUTO_NOTE}"
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


def resend_open_items(session: Session, project_id: str, rewalk_id: str) -> AutoSendReport:
    """PLAN 7.4: after a re-walk, send each trade its items that are still open, again.

    Picks items whose closure from this re-walk decided `still_open` and that are open or dispatched.
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
                note=f"Sent again to {company} after the re-walk: still open.",
            )
        except (service.DispatchFailed, service.DispatchRefused) as exc:
            report.failed[trade.value] = str(exc)
            continue
        report.sent[dispatch.id] = list(dispatch.item_ids)
    return report
