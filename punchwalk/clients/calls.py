"""Call records: every client call (live or cached) emits one `CallRecord`.

Live calls are always appended to the cost ledger. Extra sinks (e.g. the pipeline persisting
`ModelCall` rows) subscribe with `add_sink()`. `call_context()` tags records with walk/stage.

`map_in_order(fn, items)` runs per-item model calls on a small thread pool (PLAN 10.9) and hands back
the results in item order. Each task runs in a copy of the caller's context (so its records carry the
walk and stage), and the sinks get each task's records in item order too, so ModelCall rows come out
exactly as a one-at-a-time loop writes them. The ledger line of a live call is still written at once.
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Generic, TypeVar

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
# Inside a map_in_order task: the task's records, delivered to the sinks later, in item order.
_held: contextvars.ContextVar[list[CallRecord] | None] = contextvars.ContextVar(
    "punchwalk_held_calls", default=None
)

POOL_WORKERS = 4  # PLAN 10.9: per-item Spark / SAM image calls in flight at once


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
    held = _held.get()
    if held is not None:
        held.append(record)
        return record
    _deliver(record)
    return record


def _deliver(record: CallRecord) -> None:
    for sink in list(_sinks):
        sink(record)


# --------------------------------------------------------------------------- ordered thread pool

T = TypeVar("T")
R = TypeVar("R")


@dataclass
class TaskResult(Generic[T, R]):
    item: T
    value: R | None = None
    error: BaseException | None = None
    records: list[CallRecord] = field(default_factory=list)

    def get(self) -> R:
        """The value, or the task's exception raised here (in the caller's thread)."""
        if self.error is not None:
            raise self.error
        return self.value  # type: ignore[return-value]


def _run_held(fn: Callable[[T], R], item: T, out: TaskResult[T, R]) -> TaskResult[T, R]:
    _held.set(out.records)
    try:
        out.value = fn(item)
    except BaseException as err:  # noqa: BLE001 - handed to the caller in item order
        out.error = err
    return out


def map_in_order(
    fn: Callable[[T], R], items: Iterable[T], *, workers: int | None = None
) -> Iterator[TaskResult[T, R]]:
    """Run `fn(item)` for every item on a bounded thread pool; yield TaskResults in item order.

    Before a result is yielded, the CallRecords its task emitted reach the sinks (in emit order), so a
    consumer sees records and results exactly as a sequential loop would produce them. Exceptions are
    captured per item (`.error`, re-raised by `.get()`). If the consumer stops early, tasks not started
    are cancelled, running ones finish, and every finished task's records are still delivered.
    `workers=1` (or one item) runs in the calling thread, one item at a time, without a pool."""
    todo = list(items)
    n = max(1, min(int(workers or POOL_WORKERS), len(todo) or 1))
    if n == 1:
        for item in todo:
            res: TaskResult[T, R] = TaskResult(item)
            try:
                res.value = fn(item)
            except BaseException as err:  # noqa: BLE001
                res.error = err
            yield res
        return
    pool = ThreadPoolExecutor(max_workers=n, thread_name_prefix="punchwalk-call")
    futures: list[Future[TaskResult[T, R]]] = []
    delivered = 0
    try:
        for item in todo:
            ctx = contextvars.copy_context()  # walk / stage tags, per task
            futures.append(pool.submit(ctx.run, _run_held, fn, item, TaskResult(item)))
        for fut in futures:
            res = fut.result()
            delivered += 1
            for record in res.records:
                _deliver(record)
            yield res
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        for fut in futures[delivered:]:  # stopped early: still account for the calls that were made
            if fut.done() and not fut.cancelled() and fut.exception() is None:
                for record in fut.result().records:
                    _deliver(record)
