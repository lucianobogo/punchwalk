"""Shared plumbing for the model clients: errors, HTTP clients, retries, hashing."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import httpx

from punchwalk.config import Settings, get_settings

T = TypeVar("T")

PROVIDER_META = "meta"
PROVIDER_BASETEN = "baseten"
PROVIDER_META_FALLBACK = "meta-fallback"


class ApiError(RuntimeError):
    """A model API returned an error. Never carries request headers (no keys)."""

    def __init__(self, provider: str, endpoint: str, status: int | None, body: str, *, model: str = ""):
        self.provider = provider
        self.endpoint = endpoint
        self.status = status
        self.body = body[:2000]
        self.model = model
        where = f"{provider} {endpoint}" + (f" ({model})" if model else "")
        status_text = f"HTTP {status}" if status is not None else "network error"
        super().__init__(f"{where} failed with {status_text}: {self.body}")

    @property
    def transient(self) -> bool:
        """5xx, 429, timeouts and connection errors are worth retrying."""
        return self.status is None or self.status == 429 or self.status >= 500

    @property
    def auth_failed(self) -> bool:
        return self.status in (401, 403)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_bytes(source: bytes | str | Path) -> bytes:
    return source if isinstance(source, bytes) else Path(source).read_bytes()


def meta_headers(settings: Settings | None = None, *, accept: str = "application/json") -> dict[str, str]:
    s = settings or get_settings()
    key = s.MODEL_API_KEY.get_secret_value()
    if not key:
        raise ApiError(PROVIDER_META, "-", None, "MODEL_API_KEY is not set (add it to .env)")
    return {"Authorization": f"Bearer {key}", "Accept": accept}


def http_client(timeout_s: float | None = None) -> httpx.Client:
    s = get_settings()
    t = timeout_s or s.HTTP_TIMEOUT_S
    return httpx.Client(timeout=httpx.Timeout(t, connect=20.0))


def raise_for_status(resp: httpx.Response, provider: str, endpoint: str, model: str = "") -> None:
    if resp.status_code >= 400:
        try:
            body = resp.read().decode("utf-8", "replace")
        except httpx.StreamError:
            body = "<stream body unavailable>"
        raise ApiError(provider, endpoint, resp.status_code, body, model=model)


def with_retries(
    fn: Callable[[], T],
    *,
    should_retry: Callable[[ApiError, int], bool],
    backoff_s: Callable[[int], float],
    sleep: Callable[[float], None] | None = None,
) -> T:
    """Call `fn`; on ApiError ask `should_retry(err, attempt)` (attempt starts at 1)."""
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except ApiError as err:
            if not should_retry(err, attempt):
                raise
            (sleep or time.sleep)(backoff_s(attempt))


def wrap_transport_errors(provider: str, endpoint: str, model: str = ""):
    """Context manager turning httpx timeouts/connection errors into ApiError(status=None)."""
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        try:
            yield
        except httpx.TimeoutException as e:
            raise ApiError(provider, endpoint, None, f"timeout: {e!s}", model=model) from e
        except httpx.TransportError as e:
            raise ApiError(provider, endpoint, None, f"{type(e).__name__}: {e!s}", model=model) from e

    return _ctx()
