"""Slack incoming webhook: POST {"text", "blocks"} to the trade's webhook URL. Slack answers 200 "ok"."""

from __future__ import annotations

import httpx

from punchwalk.dispatch.channels import ChannelError, SendResult, http_client
from punchwalk.dispatch.message import Message


def payload_for(message: Message) -> dict:
    return {"text": message.slack_text, "blocks": message.slack_blocks()}


def send(message: Message, *, webhook_url: str, destination: str) -> SendResult:
    body = payload_for(message)
    try:
        with http_client() as client:
            res = client.post(webhook_url, json=body)
    except httpx.HTTPError as exc:
        raise ChannelError(
            "Slack couldn't be reached. Nothing was marked as sent; try again or send it from the Outbox.",
            payload=body,
        ) from exc
    if res.status_code != 200:
        reason = (res.text or "").strip()[:80] or f"HTTP {res.status_code}"
        raise ChannelError(
            f"Slack didn't accept the message ({reason}). Nothing was marked as sent; "
            "check the webhook in the trades directory or send it from the Outbox.",
            payload=body,
            status=res.status_code,
        )
    return SendResult(destination=destination, payload={**body, "links": message.share_links()})
