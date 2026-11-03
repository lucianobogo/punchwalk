"""Pipeline runner: runs a walk's stages in order, persisting state after each one.

    run_walk(walk_id, session_factory=..., progress=..., force=False, stop_after=None)

- Loads the Walk and its Project (keywords, rooms, trades) and works under DATA_DIR/walks/<id>/.
- Stage order comes from `STAGES_INITIAL` / `STAGES_REWALK` (core/enums.py), with one dependency
  rule enforced: `classify_turns` runs before `find_points`, which needs the defect windows.
- After each stage: `Walk.stages[stage] = StageState(status, started_at, finished_at, duration_ms,
  counts)`. A stage already `done` is skipped unless `force`.
- Every external call made during a stage is stored as a ModelCall row (walk_id + stage), via a
  `calls` sink filtered on this walk, flushed when the stage ends (also when it fails).
- Any exception marks the stage `failed` with a readable error, sets the walk `failed`, emits a
  `failed` event and re-raises. A full successful run sets the walk `ready`.
- Stages from later phases (write_items, ground_defects, verify, resolve_closures, persist) are
  registered as no-ops that finish `done` with counts {} until they are implemented.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from punchwalk.clients import calls
from punchwalk.core.enums import (
    STAGES_INITIAL,
    STAGES_REWALK,
    Provider,
    Stage,
    StageStatus,
    WalkKind,
    WalkStatus,
)
from punchwalk.core.models import ModelCall, Project, StageState, Walk, utcnow
from punchwalk.pipeline import classify, evidence, fuse, points, prepare, track, transcribe
from punchwalk.pipeline.context import SessionFactory, StageContext, StageOutcome, walk_dir

log = logging.getLogger(__name__)

StageFn = Callable[[StageContext], StageOutcome]


@dataclass
class StageEvent:
    stage: Stage
    status: Literal["running", "done", "failed"]
    counts: dict[str, int] = field(default_factory=dict)
    message: str | None = None


def _not_implemented(ctx: StageContext) -> StageOutcome:
    return StageOutcome(counts={}, message=None)


# Keyed by stage value so this module imports even while stage names are being renamed.
STAGE_FUNCTIONS: dict[str, StageFn] = {
    "ingest": prepare.run,
    "transcribe": transcribe.run,
    "classify_turns": classify.run,
    "track_hands": track.run,
    "find_points": points.run,
    "fuse": fuse.run,
    "pick_evidence": evidence.run,
}


def stage_order(kind: WalkKind) -> list[Stage]:
    order = list(STAGES_REWALK if kind == WalkKind.rewalk else STAGES_INITIAL)
    names = [s.value for s in order]
    if "find_points" in names and "classify_turns" in names and names.index("classify_turns") > names.index(
        "find_points"
    ):
        cls = order.pop(names.index("classify_turns"))
        order.insert([s.value for s in order].index("transcribe") + 1, cls)
    return order


class _CallBuffer:
    """Collects this walk's CallRecords; `flush` writes them as ModelCall rows."""

    def __init__(self, walk_id: str):
        self.walk_id = walk_id
        self.records: list[calls.CallRecord] = []

    def __call__(self, record: calls.CallRecord) -> None:
        if record.walk_id == self.walk_id:
            self.records.append(record)

    def flush(self, session_factory: SessionFactory) -> int:
        if not self.records:
            return 0
        pending, self.records = self.records, []
        with session_factory() as s:
            for r in pending:
                try:
                    stage = Stage(r.stage) if r.stage else None
                except ValueError:
                    stage = None
                s.add(ModelCall(
                    walk_id=r.walk_id, stage=stage, model=r.model or "meta-files", provider=Provider(r.provider),
                    endpoint=r.endpoint, latency_ms=r.latency_ms, tokens_in=r.tokens_in, tokens_out=r.tokens_out,
                    frames=r.frames, units=r.units, unit_kind=r.unit_kind or None, est_cost_usd=r.est_cost_usd,
                    cached=r.cached, ok=r.ok, error=r.error, cache_key=r.cache_key,
                ))
            s.commit()
        return len(pending)


def _load_context(walk_id: str, session_factory: SessionFactory, force: bool) -> StageContext:
    with session_factory() as s:
        walk = s.get(Walk, walk_id)
        if walk is None:
            raise LookupError(f"walk {walk_id} not found")
        project = s.get(Project, walk.project_id)
        if project is None:
            raise LookupError(f"project {walk.project_id} of walk {walk_id} not found")
        return StageContext(
            walk_id=walk.id, project_id=project.id, kind=WalkKind(walk.kind), walk_dir=walk_dir(walk.id),
            session_factory=session_factory, keywords=list(project.keywords or []),
            rooms=list(project.rooms or []), trades=list(project.trades or []), rewalk_of=walk.rewalk_of,
            force=force,
        )


def _set_stage(session_factory: SessionFactory, walk_id: str, state: StageState, *,
               walk_status: WalkStatus | None = None, error: str | None | bool = False) -> None:
    with session_factory() as s:
        walk = s.get(Walk, walk_id)
        walk.stages = {**(walk.stages or {}), state.stage.value: state.model_dump(mode="json")}
        if walk_status is not None:
            walk.status = walk_status
        if error is not False:
            walk.error = error
        s.add(walk)
        s.commit()


def _stage_done(session_factory: SessionFactory, walk_id: str, stage: Stage) -> StageState | None:
    with session_factory() as s:
        raw = (s.get(Walk, walk_id).stages or {}).get(stage.value)
    if not raw:
        return None
    state = StageState.model_validate(raw)
    return state if state.status == StageStatus.done else None


def _emit(progress: Callable[[StageEvent], None] | None, event: StageEvent) -> None:
    if progress is None:
        return
    try:
        progress(event)
    except Exception:  # noqa: BLE001 - a broken progress listener must not stop the walk
        log.exception("progress callback failed for %s", event.stage)


def readable_error(stage: Stage, err: BaseException) -> str:
    text = str(err).strip() or type(err).__name__
    return f"{stage.value} failed: {type(err).__name__}: {text}"[:1500]


def run_walk(
    walk_id: str,
    *,
    session_factory: SessionFactory,
    progress: Callable[[StageEvent], None] | None = None,
    force: bool = False,
    stop_after: Stage | None = None,
) -> None:
    """Run (or resume) every stage of a walk. See the module docstring."""
    ctx = _load_context(walk_id, session_factory, force)
    buffer = _CallBuffer(walk_id)
    calls.add_sink(buffer)
    try:
        with session_factory() as s:
            walk = s.get(Walk, walk_id)
            walk.status, walk.error = WalkStatus.processing, None
            s.add(walk)
            s.commit()
        rerun = force  # once a stage re-runs, everything after it re-runs too (its inputs changed)
        for stage in stage_order(ctx.kind):
            done = None if rerun else _stage_done(session_factory, walk_id, stage)
            if done is not None:
                _emit(progress, StageEvent(stage, "done", dict(done.counts), "already done"))
            else:
                _run_stage(ctx, stage, buffer, progress)
                rerun = True
            if stop_after is not None and stage == stop_after:
                return
        with session_factory() as s:
            walk = s.get(Walk, walk_id)
            walk.status, walk.error = WalkStatus.ready, None
            s.add(walk)
            s.commit()
    finally:
        calls.remove_sink(buffer)
        buffer.flush(session_factory)


def _run_stage(ctx: StageContext, stage: Stage, buffer: _CallBuffer,
               progress: Callable[[StageEvent], None] | None) -> None:
    fn = STAGE_FUNCTIONS.get(stage.value, _not_implemented)
    started = utcnow()
    t0 = time.monotonic()
    _set_stage(ctx.session_factory, ctx.walk_id,
               StageState(stage=stage, status=StageStatus.running, started_at=started))
    _emit(progress, StageEvent(stage, "running"))
    try:
        with calls.call_context(walk_id=ctx.walk_id, stage=stage.value):
            outcome = fn(ctx)
    except BaseException as err:
        buffer.flush(ctx.session_factory)
        message = readable_error(stage, err)
        log.error("walk %s: %s", ctx.walk_id, message)
        _set_stage(ctx.session_factory, ctx.walk_id, StageState(
            stage=stage, status=StageStatus.failed, started_at=started, finished_at=utcnow(),
            duration_ms=int((time.monotonic() - t0) * 1000), error=message,
        ), walk_status=WalkStatus.failed, error=message)
        _emit(progress, StageEvent(stage, "failed", {}, message))
        raise
    buffer.flush(ctx.session_factory)
    _set_stage(ctx.session_factory, ctx.walk_id, StageState(
        stage=stage, status=StageStatus.done, started_at=started, finished_at=utcnow(),
        duration_ms=int((time.monotonic() - t0) * 1000), counts=dict(outcome.counts),
    ))
    _emit(progress, StageEvent(stage, "done", dict(outcome.counts), outcome.message))
