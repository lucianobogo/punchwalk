"""What every channel returns or raises. Channels are plain functions: `send(message, ...) -> SendResult`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

SEND_TIMEOUT_S = 10.0

# Tests swap this for an httpx.MockTransport (respx also works: it patches httpx itself).
transport: httpx.BaseTransport | None = None


@dataclass
class SendResult:
    destination: str
    payload: dict[str, Any]  # exactly what was sent (the request body), plus share links
    provider_id: str | None = None  # e.g. the Resend email id
    extra: dict[str, Any] = field(default_factory=dict)


class ChannelError(RuntimeError):
    """The channel refused or couldn't be reached. `str(exc)` is a sentence for the PM."""

    def __init__(self, message: str, *, payload: dict[str, Any] | None = None, status: int | None = None) -> None:
        super().__init__(message)
        self.payload = payload or {}
        self.status = status


def http_client() -> httpx.Client:
    return httpx.Client(timeout=SEND_TIMEOUT_S, transport=transport)
