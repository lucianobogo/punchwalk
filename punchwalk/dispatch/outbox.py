"""Copy link (`Channel.outbox`): always available. The send is the stored Dispatch row; the PM copies the link or text."""

from __future__ import annotations

from punchwalk.dispatch.channels import SendResult
from punchwalk.dispatch.message import Message

DESTINATION = "Copy link"


def send(message: Message) -> SendResult:
    return SendResult(
        destination=DESTINATION,
        payload={"subject": message.subject, "text": message.text, "links": message.share_links()},
    )
