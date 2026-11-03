"""Call records: every client call (live or cached) emits one `CallRecord`.

Live calls are always appended to the cost ledger. Extra sinks (e.g. the pipeline persisting
`ModelCall` rows) subscribe with `add_sink()`. `call_context()` tags records with walk/stage.
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from pydantic import BaseModel, Field


class CallRecord(BaseModel):
    ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="milliseconds"))
    provider: str  # meta | baseten | meta-fallback
    model: str
    endpoint: str
    stage: str = ""
    walk_id: str | None = None
    latency_ms: int = 0
    tokens_in: int | None = None
    tokens_out: int | None = None
    frames: int | None = None
    units: float = 0.0
    unit_kind: str = ""  # frames | images | audio_seconds | tokens | bytes | requests
    est_cost_usd: float = 0.0
    cached: bool = False
    ok: bool = True
    error: str | None = None
    cache_key: str | None = None


Sink = Callable[[CallRecord], None]

_sinks: list[Sink] = []
_context: contextvars.ContextVar[dict[str, str | None] | None] = contextvars.ContextVar(
    "punchwalk_call_context", default=None
)


def add_sink(sink: Sink) -> None:
    _sinks.append(sink)


def remove_sink(sink: Sink) -> None:
    if sink in _sinks:
        _sinks.remove(sink)


def clear_sinks() -> None:
    _sinks.clear()


@contextmanager
def call_context(*, walk_id: str | None = None, stage: str | None = None) -> Iterator[None]:
    """Tag every CallRecord emitted inside this block with walk_id / stage."""
    current = dict(_context.get() or {})
    if walk_id is not None:
        current["walk_id"] = walk_id
    if stage is not None:
        current["stage"] = stage
    token = _context.set(current)
    try:
        yield
    finally:
        _context.reset(token)


def emit(record: CallRecord) -> CallRecord:
    ctx = _context.get() or {}
    if record.walk_id is None and ctx.get("walk_id"):
        record.walk_id = ctx["walk_id"]
    if not record.stage and ctx.get("stage"):
        record.stage = ctx["stage"] or ""
    if not record.cached:
        from punchwalk.clients.cost import append_ledger

        append_ledger(record)
    for sink in list(_sinks):
        sink(record)
    return record
