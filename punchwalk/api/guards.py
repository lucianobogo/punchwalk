"""Guards (PLAN 4.4): request size limits, the DEMO_MODE per-IP upload limit, client IPs.

The "<= 2 concurrent jobs" guard lives in `jobs.py` (a pool of `MAX_CONCURRENT_JOBS` worker threads).
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from punchwalk.api.errors import ApiError, error_body
from punchwalk.config import get_settings

MiB = 1024 * 1024
MAX_UPLOAD_BYTES = 400 * MiB  # all clips of one walk together
MAX_WALK_DURATION_S = 600.0  # 10 minutes (Muse Voice Transcribe limit)
MAX_CLIPS = 12
UPLOAD_OVERHEAD_BYTES = 2 * MiB  # multipart headers and form fields on top of the clip bytes
MAX_JSON_BODY_BYTES = 1 * MiB  # every other request

# DEMO_MODE: uploads per client IP per window (in memory; resets on restart).
DEMO_UPLOADS_PER_WINDOW = 3
DEMO_UPLOAD_WINDOW_S = 3600.0


def is_upload_path(path: str) -> bool:
    return path.startswith("/api/projects/") and path.rstrip("/").endswith("/walks")


def body_limit_for(scope: Scope) -> int:
    if scope.get("method") == "POST" and is_upload_path(scope.get("path", "")):
        return MAX_UPLOAD_BYTES + UPLOAD_OVERHEAD_BYTES
    return MAX_JSON_BODY_BYTES


def too_large_detail(limit: int) -> str:
    if limit > MAX_JSON_BODY_BYTES:
        return f"This walk is larger than {MAX_UPLOAD_BYTES // MiB} MB. Split it into shorter walks and upload each one."
    return "This request is too large."


class BodySizeLimitMiddleware:
    """Rejects bodies over the limit: early from Content-Length, and while streaming otherwise."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = body_limit_for(scope)
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None:
            try:
                too_big = int(declared) > limit
            except ValueError:
                too_big = False
            if too_big:
                await _send_json(send, 413, error_body(413, too_large_detail(limit)))
                return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise ApiError(413, too_large_detail(limit))
            return message

        await self.app(scope, limited_receive, send)


async def _send_json(send: Send, status: int, body: dict) -> None:
    payload = json.dumps(body).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": payload})


def client_ip(request: Request) -> str:
    """The client's IP for rate limiting. Proxy headers are only trusted behind a proxy we know:
    Fly-Client-IP when running on Fly (FLY_APP_NAME is set; Fly's edge overwrites the header), and
    X-Forwarded-For only with TRUST_PROXY=1. Otherwise the socket peer, so headers can't be spoofed."""
    if os.environ.get("FLY_APP_NAME"):
        fly = request.headers.get("fly-client-ip")
        if fly:
            return fly.strip()
    if get_settings().TRUST_PROXY:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class UploadRateLimiter:
    """Sliding-window counter of uploads per IP. A slot is reserved before the body streams in
    (so parallel uploads from one device can't all slip through) and released if the upload fails."""

    def __init__(self, limit: int = DEMO_UPLOADS_PER_WINDOW, window_s: float = DEMO_UPLOAD_WINDOW_S) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, ip: str, now: float) -> deque[float]:
        hits = self._hits.setdefault(ip, deque())
        while hits and now - hits[0] >= self.window_s:
            hits.popleft()
        return hits

    def check_and_reserve(self, ip: str) -> float:
        """Take one slot for `ip` or raise 429. Returns the reservation, for `release()`."""
        now = time.monotonic()
        with self._lock:
            hits = self._prune(ip, now)
            if len(hits) >= self.limit:
                wait_min = max(1, round((self.window_s - (now - hits[0])) / 60))
                raise ApiError(
                    429,
                    f"This demo accepts {self.limit} uploads per hour from each device. "
                    f"Try again in about {wait_min} min.",
                    headers={"Retry-After": str(wait_min * 60)},
                )
            hits.append(now)
            return now

    def release(self, ip: str, reservation: float) -> None:
        """Give back a slot taken by `check_and_reserve` (the upload was refused or failed)."""
        with self._lock:
            hits = self._hits.get(ip)
            if hits and reservation in hits:
                hits.remove(reservation)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


upload_limiter = UploadRateLimiter()
