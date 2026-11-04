"""Channel choice, the preview, and sending a trade its items.

Effective channel (per trade; one rule for the Trades list, the preview default, sends, approve-and-send
and the history): the trade's own channel when it's set up and working (its Slack webhook, else its
contact email when Resend is configured), else Slack when the team webhook SLACK_WEBHOOK_URL is set,
else Copy link (`Channel.outbox`: the send is recorded in the app and the PM copies the link). Only
working channels are offered. The UI never says "Outbox"; nor does any sentence here.

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
from punchwalk.db import as_utc
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
    reason: str | None  # why it isn't available (used in refusals; never listed in the preview)
    webhook: str | None = None  # never leaves the server
    own: bool = False  # the trade's own channel (its webhook / its email), not the team's Slack
    label: str = ""  # "Posts to #punch-electrical." (see channel_label)

    def out(self) -> S.DispatchChannelOut:
        return S.DispatchChannelOut(
            channel=self.channel,
            available=self.available,
            destination=self.destination,
            reason=self.reason,
            label=self.label,
        )


def channel_label(channel: Channel, destination: str | None, *, own: bool, company: str) -> str:
    """The sentence naming where a send goes: "Posts to #punch-electrical." / "Emails x@example.com." ..."""
    if channel == Channel.slack:
        if own and destination and destination.startswith("#"):
            return f"Posts to {destination}."
        if own:
            return f"Posts to {company}'s Slack channel."
        return "Posts to your team's Slack channel."
    if channel == Channel.email:
        return f"Emails {destination}."
    return "Copy a link to share."


def channel_options(project: Project, trade: Trade) -> list[ChannelOption]:
    """Every channel (slack, email, outbox) with whether it works for this trade; see `effective`."""
    c = contact_for(project, trade)
    company = c.company if c else TRADE_LABEL[trade]
    own_hook = (c.slack_webhook if c else None) or None
    team_hook = get_settings().SLACK_WEBHOOK_URL.get_secret_value() or None
    webhook = own_hook or team_hook
    slack_dest = None
    if own_hook:
        slack_dest = (c.slack_channel if c else None) or SLACK_DEFAULT_DESTINATION
    elif team_hook:
        slack_dest = SLACK_DEFAULT_DESTINATION
    slack_opt = ChannelOption(
        Channel.slack,
        available=bool(webhook),
        destination=slack_dest,
        reason=None if webhook else f"No Slack channel set up for {company}.",
        webhook=webhook,
        own=bool(own_hook),
    )
    email = c.email if c else None
    if not email:
        email_opt = ChannelOption(Channel.email, False, None, f"No email on file for {company}.")
    elif not mail.configured():
        email_opt = ChannelOption(Channel.email, False, email, "Email sending isn't set up on this server yet.")
    else:
        email_opt = ChannelOption(Channel.email, True, email, None, own=True)
    outbox_opt = ChannelOption(Channel.outbox, True, outbox.DESTINATION, None)
    options = [slack_opt, email_opt, outbox_opt]
    for o in options:
        o.label = channel_label(o.channel, o.destination, own=o.own, company=company)
    return options


def effective(options: Sequence[ChannelOption]) -> ChannelOption:
    """The trade's own working channel (Slack webhook, else email), else the team's Slack, else Copy link."""
    by = {o.channel: o for o in options}
    slack_opt, email_opt = by[Channel.slack], by[Channel.email]
    if slack_opt.available and slack_opt.own:
        return slack_opt
    if email_opt.available:
        return email_opt
    if slack_opt.available:
        return slack_opt
    return by[Channel.outbox]  # always available


suggested = effective  # older name


def working(options: Sequence[ChannelOption]) -> list[ChannelOption]:
    """Only the channels that work, the effective one first, then slack / email / copy link."""
    first = effective(options)
    return [first, *(o for o in options if o.available and o is not first)]


def effective_for(project: Project, trade: Trade) -> ChannelOption:
    return effective(channel_options(project, trade))


def why(option: ChannelOption, company: str = "", options: Sequence[ChannelOption] = ()) -> str:
    """DispatchPreviewOut.channel_reason: the chosen channel's label."""
    return option.label or channel_label(option.channel, option.destination, own=option.own, company=company)


def _items(n: int) -> str:
    return f"{n} item" if n == 1 else f"{n} items"


def send_summary(channel: Channel, n: int, destination: str | None, company: str, *, ok: bool = True) -> str:
    """The history sentence for a send (no date: the UI shows `at`), e.g. "Posted 2 items to Slack"."""
    if channel == Channel.slack:
        where = destination if destination and destination.startswith("#") else "Slack"
        verb = "Posted" if ok else "Couldn't post"
        return f"{verb} {_items(n)} to {where}"
    if channel == Channel.email:
        verb = "Emailed" if ok else "Couldn't email"
        return f"{verb} {_items(n)} to {company}"
    verb = "Copied a link" if ok else "Couldn't copy a link"
    return f"{verb} for {_items(n)}"


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


def last_sends(session: Session, project_id: str) -> dict[str, Dispatch]:
    """item id -> its latest successful Dispatch in this project."""
    out: dict[str, Dispatch] = {}
    rows = session.exec(
        select(Dispatch).where(Dispatch.project_id == project_id, Dispatch.ok == True)  # noqa: E712
    ).all()
    for d in sorted(rows, key=lambda d: (d.sent_at, d.id)):
        for item_id in d.item_ids or []:
            out[item_id] = d
    return out


def preview(session: Session, project: Project, trade: Trade, channel: Channel | None = None) -> S.DispatchPreviewOut:
    from punchwalk.api.views import trade_contact_out

    options = channel_options(project, trade)
    eff = effective(options)
    chosen = next((o for o in options if o.channel == channel and o.available), None) or eff
    mine = sorted(live_trade_items(session, project.id, trade), key=lambda i: i.number)
    sent_by = last_sends(session, project.id)
    already_sent = [
        S.DispatchSentItemOut(item_id=i.id, number=i.number, title=i.title, sent_at=as_utc(sent_by[i.id].sent_at),
                              channel=sent_by[i.id].channel)
        for i in mine if i.status in SENDABLE and i.id in sent_by  # a reopened (open, sent before) item too
    ]  # fmt: skip
    msg = message_for(session, project, trade, sendable_items(session, project.id, trade))
    return S.DispatchPreviewOut(
        project_id=project.id,
        trade=trade,
        contact=trade_contact_out(contact_for(project, trade)),
        channel=chosen.channel,
        channel_reason=why(chosen),
        effective_channel=eff.channel,
        effective_channel_label=eff.label,
        channels=[o.out() for o in working(options)],
        to_review=[
            S.DispatchItemRefOut(item_id=i.id, number=i.number, title=i.title)
            for i in mine
            if i.status == ItemStatus.needs_review
        ],
        already_sent=already_sent,
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


def send_items(
    session: Session,
    project: Project,
    trade: Trade,
    item_ids: Sequence[str],
    channel: Channel | None = None,
    *,
    actor: HistoryActor = HistoryActor.human,
    note_suffix: str = "",
) -> Dispatch:
    """Send `item_ids` to the trade over `channel` (None = the effective one). See the module docstring.

    Each item's history note is the send's summary ("Posted 2 items to #punch-electrical") plus
    `note_suffix` (" automatically: checked against the photo and the quote.").
    """
    items = _pick_items(session, project, trade, item_ids)
    options = channel_options(project, trade)
    if channel is None:
        option = effective(options)
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
    text = send_summary(option.channel, len(msg.item_ids), result.destination, company) + note_suffix
    for item in items:
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
