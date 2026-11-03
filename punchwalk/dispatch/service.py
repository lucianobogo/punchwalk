"""Channel choice, the preview, and sending a trade its items.

Channel policy (per trade): Slack when a webhook exists (the trade's own from the trades directory,
else the server-wide SLACK_WEBHOOK_URL); else email when Resend is configured and the contact has an
address; else the in-app Outbox, which is always available.

A send (`send_items`):
1. checks every item belongs to the project and trade and is open (or already sent and still open);
2. gives each item a share token, builds the message (`message.py`), sends it through the channel;
3. on success stores a `Dispatch` row (payload = exactly what was sent, plus the share links), moves
   `open` items to `dispatched` and appends a `dispatched` history entry to every item;
4. on a channel failure stores a `Dispatch` row with ok=false and the error, changes no item, and
   raises `DispatchFailed` (the API answers 502 with the sentence).
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from dataclasses import dataclass

from sqlmodel import Session, col, select

from punchwalk.api import schemas as S
from punchwalk.config import get_settings
from punchwalk.core.enums import (
    TRADE_LABEL,
    Channel,
    HistoryAction,
    HistoryActor,
    ItemStatus,
    Trade,
    format_item_number,
)
from punchwalk.core.models import Dispatch, HistoryEvent, Item, Project, TradeContact, utcnow
from punchwalk.dispatch import mail, outbox, slack
from punchwalk.dispatch.channels import ChannelError, SendResult
from punchwalk.dispatch.message import Message, build_message

SENDABLE = (ItemStatus.open, ItemStatus.dispatched)
CHANNEL_ORDER = (Channel.slack, Channel.email, Channel.outbox)
SLACK_DEFAULT_DESTINATION = "Slack"  # the server-wide webhook, when the trade has no channel name


class DispatchRefused(ValueError):
    """The request can't be sent as asked (wrong items, unavailable channel). `status` is the HTTP code."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class DispatchFailed(RuntimeError):
    """The channel failed; `dispatch` is the stored (ok=false) row."""

    def __init__(self, message: str, dispatch: Dispatch) -> None:
        super().__init__(message)
        self.dispatch = dispatch


# --------------------------------------------------------------------------- directory


def contact_for(project: Project, trade: Trade) -> TradeContact | None:
    for raw in project.trades or []:
        try:
            c = TradeContact.model_validate(raw)
        except ValueError:
            continue
        if c.trade == trade:
            return c
    return None


def company_for(project: Project, trade: Trade) -> str:
    c = contact_for(project, trade)
    return c.company if c else TRADE_LABEL[trade]


@dataclass
class ChannelOption:
    channel: Channel
    available: bool
    destination: str | None
    reason: str | None
    webhook: str | None = None  # never leaves the server

    def out(self) -> S.DispatchChannelOut:
        return S.DispatchChannelOut(
            channel=self.channel, available=self.available, destination=self.destination, reason=self.reason
        )


def channel_options(project: Project, trade: Trade) -> list[ChannelOption]:
    c = contact_for(project, trade)
    company = c.company if c else TRADE_LABEL[trade]
    webhook = (c.slack_webhook if c else None) or get_settings().SLACK_WEBHOOK_URL.get_secret_value() or None
    slack_opt = ChannelOption(
        Channel.slack,
        available=bool(webhook),
        destination=((c.slack_channel if c else None) or SLACK_DEFAULT_DESTINATION) if webhook else None,
        reason=None if webhook else f"No Slack channel set up for {company}.",
        webhook=webhook,
    )
    email = c.email if c else None
    if not email:
        email_opt = ChannelOption(Channel.email, False, None, f"No email on file for {company}.")
    elif not mail.configured():
        email_opt = ChannelOption(Channel.email, False, email, "Email sending isn't set up on this server yet.")
    else:
        email_opt = ChannelOption(Channel.email, True, email, None)
    outbox_opt = ChannelOption(Channel.outbox, True, outbox.DESTINATION, None)
    return [slack_opt, email_opt, outbox_opt]


def suggested(options: Sequence[ChannelOption]) -> ChannelOption:
    return next(o for o in options if o.available)  # the outbox is always available


def why(option: ChannelOption, company: str, options: Sequence[ChannelOption] = ()) -> str:
    """One plain sentence on the chosen channel (DispatchPreviewOut.channel_reason).

    `options` (all of the trade's channels) explains an Outbox pick: an email address on file that
    can't be used yet is named as the reason, not reported as missing.
    """
    if option.channel == Channel.slack:
        if option.destination and option.destination != SLACK_DEFAULT_DESTINATION:
            return f"{company} has a Slack channel ({option.destination})."
        return f"{company} gets the team's Slack channel."
    if option.channel == Channel.email:
        return f"{company} gets email at {option.destination}."
    others = [o for o in options if o.channel != Channel.outbox]
    if any(o.available for o in others):  # the Outbox was picked on purpose (?channel=outbox)
        return f"This goes to the Outbox for {company}, ready to share."
    if any(o.channel == Channel.email and o.destination for o in others):
        return f"Email isn't set up on this server yet, so this goes to the Outbox for {company}, ready to share."
    return f"No Slack channel or email set up for {company}, so this goes to the Outbox, ready to share."


# --------------------------------------------------------------------------- items


def live_trade_items(session: Session, project_id: str, trade: Trade) -> list[Item]:
    return list(
        session.exec(
            select(Item).where(Item.project_id == project_id, Item.trade == trade, col(Item.dismissed_at).is_(None))
        ).all()
    )


def sendable_items(session: Session, project_id: str, trade: Trade) -> list[Item]:
    """What would go out now: open, and sent-but-still-open items. Needs-a-look items wait."""
    return [i for i in live_trade_items(session, project_id, trade) if i.status in SENDABLE]


def ensure_share_tokens(session: Session, items: Sequence[Item]) -> None:
    changed = False
    for item in items:
        if not item.share_token:
            while True:
                token = f"shr_{secrets.token_hex(5)}"
                if session.exec(select(Item.id).where(Item.share_token == token)).first() is None:
                    break
            item.share_token = token
            session.add(item)
            changed = True
    if changed:
        session.commit()


def message_for(session: Session, project: Project, trade: Trade, items: Sequence[Item]) -> Message:
    ensure_share_tokens(session, items)
    return build_message(project.name, trade, company_for(project, trade), list(items))


# --------------------------------------------------------------------------- preview


def preview(session: Session, project: Project, trade: Trade, channel: Channel | None = None) -> S.DispatchPreviewOut:
    from punchwalk.api.views import trade_contact_out

    options = channel_options(project, trade)
    chosen = next((o for o in options if o.channel == channel and o.available), None) or suggested(options)
    msg = message_for(session, project, trade, sendable_items(session, project.id, trade))
    return S.DispatchPreviewOut(
        project_id=project.id,
        trade=trade,
        contact=trade_contact_out(contact_for(project, trade)),
        channel=chosen.channel,
        channel_reason=why(chosen, company_for(project, trade), options),
        channels=[o.out() for o in options],
        heading=msg.heading,
        lines=[
            S.DispatchLineOut(item_id=ln.item_id, number=ln.number, title=ln.title, line=ln.line, url=ln.url)
            for ln in msg.lines
        ],
        subject=msg.subject,
        message_text=msg.text,
        slack_text=msg.slack_text,
        item_ids=msg.item_ids,
    )


# --------------------------------------------------------------------------- send


def _pick_items(session: Session, project: Project, trade: Trade, item_ids: Sequence[str]) -> list[Item]:
    if not item_ids:
        raise DispatchRefused("Pick at least one item to send.")
    items: list[Item] = []
    for item_id in dict.fromkeys(item_ids):
        item = session.get(Item, item_id)
        if item is None or item.project_id != project.id or item.dismissed_at is not None:
            raise DispatchRefused("One of these items isn't in this project any more. Refresh and try again.", 404)
        num = format_item_number(item.number)
        if item.trade != trade:
            raise DispatchRefused(f"Item {num} belongs to {TRADE_LABEL[item.trade]}, not {TRADE_LABEL[trade]}.")
        if item.status == ItemStatus.needs_review:
            raise DispatchRefused(f"Item {num} needs a look first. Approve it, then send it.", 409)
        if item.status == ItemStatus.closed:
            raise DispatchRefused(f"Item {num} is signed off, so there's nothing to send.", 409)
        items.append(item)
    return items


def via(option: ChannelOption) -> str:
    """'in Slack (#punch-electrical)', 'in Slack', 'by email (x@example.com)', 'from the outbox'."""
    if option.channel == Channel.slack:
        named = option.destination and option.destination != SLACK_DEFAULT_DESTINATION
        return f"in Slack ({option.destination})" if named else "in Slack"
    if option.channel == Channel.email:
        return f"by email ({option.destination})"
    return "from the outbox"


def send_items(
    session: Session,
    project: Project,
    trade: Trade,
    item_ids: Sequence[str],
    channel: Channel | None = None,
    *,
    actor: HistoryActor = HistoryActor.human,
    note: str | None = None,
) -> Dispatch:
    """Send `item_ids` to the trade over `channel` (None = the suggested one). See the module docstring.

    `note` replaces the default history note ("Sent to Ace Electric in Slack (#punch-electrical).").
    """
    items = _pick_items(session, project, trade, item_ids)
    options = channel_options(project, trade)
    if channel is None:
        option = suggested(options)
    else:
        option = next(o for o in options if o.channel == channel)
        if not option.available:
            raise DispatchRefused(option.reason or "That channel isn't available for this trade.")
    company = company_for(project, trade)
    msg = message_for(session, project, trade, items)

    try:
        result = _deliver(msg, option)
    except ChannelError as exc:
        failed = Dispatch(
            project_id=project.id,
            trade=trade,
            item_ids=msg.item_ids,
            channel=option.channel,
            destination=option.destination,
            ok=False,
            error=str(exc),
            payload={**exc.payload, "links": msg.share_links()},
        )
        session.add(failed)
        session.commit()
        raise DispatchFailed(str(exc), failed) from exc

    now = utcnow()
    dispatch = Dispatch(
        project_id=project.id,
        trade=trade,
        item_ids=msg.item_ids,
        channel=option.channel,
        destination=result.destination,
        sent_at=now,
        ok=True,
        payload=result.payload,
    )
    session.add(dispatch)
    for item in items:
        again = item.status == ItemStatus.dispatched
        if note:
            text = note
        elif option.channel == Channel.outbox:  # nothing left the app yet: the PM shares the link
            text = f"{'Put in the Outbox again' if again else 'Put in the Outbox'} for {company}, ready to share."
        else:
            text = f"Sent {'again ' if again else ''}to {company} {via(option)}."
        entry = HistoryEvent(
            at=now,
            actor=actor,
            action=HistoryAction.dispatched,
            field="status",
            from_=item.status.value,
            to=ItemStatus.dispatched.value,
            note=text,
        ).model_dump(mode="json", by_alias=True)
        item.history = [*(item.history or []), entry]
        item.status = ItemStatus.dispatched
        item.updated_at = now
        session.add(item)
    session.commit()
    return dispatch


def _deliver(msg: Message, option: ChannelOption) -> SendResult:
    if option.channel == Channel.slack:
        assert option.webhook
        return slack.send(msg, webhook_url=option.webhook, destination=option.destination or "Slack")
    if option.channel == Channel.email:
        assert option.destination
        return mail.send(msg, to=option.destination)
    return outbox.send(msg)


# --------------------------------------------------------------------------- approve and send


def approve_and_send(session: Session, item: Item, channel: Channel | None = None) -> Dispatch:
    """"Approve and send to Ace Electric": a needs-a-look item becomes open (logged), then goes out.

    The approval only sticks if the send succeeds: when the channel refuses or fails, the item goes
    back to needs-a-look with its reason and history unchanged, and the error is re-raised.
    """
    project = session.get(Project, item.project_id)
    assert project is not None
    if item.status != ItemStatus.needs_review:
        return send_items(session, project, item.trade, [item.id], channel)
    before = (item.status, item.review_reason, list(item.history or []), item.updated_at)
    now = utcnow()
    entry = HistoryEvent(
        at=now,
        actor=HistoryActor.human,
        action=HistoryAction.status_changed,
        field="status",
        from_=ItemStatus.needs_review.value,
        to=ItemStatus.open.value,
        note="Approved by the project manager.",
    ).model_dump(mode="json", by_alias=True)
    item.history = [*before[2], entry]
    item.status = ItemStatus.open
    item.review_reason = None
    item.updated_at = now
    session.add(item)
    session.commit()
    try:
        return send_items(session, project, item.trade, [item.id], channel)
    except (DispatchRefused, DispatchFailed):
        session.refresh(item)
        item.status, item.review_reason, item.history, item.updated_at = before
        session.add(item)
        session.commit()
        raise
