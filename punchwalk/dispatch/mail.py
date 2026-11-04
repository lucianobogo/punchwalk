"""Email through Resend's REST API (https://resend.com/docs/api-reference/emails/send-email).

Named mail.py, not email.py, so it can never shadow the standard library `email` package.

Used only when RESEND_API_KEY and EMAIL_FROM are set and the contact has an email address.
"""

from __future__ import annotations

import contextlib
import html

import httpx

from punchwalk.config import get_settings
from punchwalk.dispatch.channels import ChannelError, SendResult, http_client
from punchwalk.dispatch.message import VIEW_LINK_TEXT, Message, absolute

RESEND_URL = "https://api.resend.com/emails"


def configured() -> bool:
    s = get_settings()
    return bool(s.RESEND_API_KEY.get_secret_value() and s.EMAIL_FROM)


def html_body(message: Message) -> str:
    rows = []
    for ln in message.lines:
        rows.append(
            f"<p><strong>{ln.number}. {html.escape(ln.title)}</strong><br>{html.escape(ln.line)}<br>"
            f'<a href="{html.escape(absolute(ln.url))}">{VIEW_LINK_TEXT}</a></p>'
        )
    return f"<p><strong>{html.escape(message.heading)}</strong></p>" + "".join(rows)


def send(message: Message, *, to: str) -> SendResult:
    settings = get_settings()
    body = {
        "from": settings.EMAIL_FROM,
        "to": [to],
        "subject": message.subject,
        "text": message.text,
        "html": html_body(message),
    }
    headers = {"Authorization": f"Bearer {settings.RESEND_API_KEY.get_secret_value()}"}
    stored = {"to": to, "subject": message.subject, "text": message.text}
    try:
        with http_client() as client:
            res = client.post(RESEND_URL, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise ChannelError(
            "The email service couldn't be reached. Nothing was marked as sent; try again or copy a link instead.",
            payload=stored,
        ) from exc
    if res.status_code >= 300:
        try:
            reason = str(res.json().get("message") or "")[:120]
        except ValueError:
            reason = ""
        raise ChannelError(
            f"The email wasn't sent ({reason or f'HTTP {res.status_code}'}). Nothing was marked as sent.",
            payload=stored,
            status=res.status_code,
        )
    provider_id = None
    with contextlib.suppress(ValueError):
        provider_id = res.json().get("id")
    return SendResult(destination=to, payload={**stored, "links": message.share_links()}, provider_id=provider_id)
