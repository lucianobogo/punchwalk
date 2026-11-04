"""Pipeline runner: runs a walk's stages in order, persisting state after each one.

    run_walk(walk_id, session_factory=..., progress=..., force=False, stop_after=None)

- Loads the Walk and its Project (keywords, rooms, trades) and works under DATA_DIR/walks/<id>/.
- Stage order comes from `STAGES_INITIAL` / `STAGES_REWALK` (core/enums.py); `classify_turns` comes
  before `find_points`, which needs the defect windows.
- After each stage: `Walk.stages[stage] = StageState(status, started_at, finished_at, duration_ms,
  counts)`. A stage already `done` is skipped unless `force`.
- Every external call made during a stage is stored as a ModelCall row (walk_id + stage), via a
  `calls` sink filtered on this walk, flushed when the stage ends (also when it fails).
- Any exception marks the stage `failed` with a readable error, sets the walk `failed`, emits a
  `failed` event and re-raises. A full successful run sets the walk `ready`.
- Retryable errors (5xx, 429, timeouts) re-run the stage up to STAGE_RETRIES times with backoff;
  cache misses, the cost cap and missing inputs fail at once. Stages report running counts
  (`ctx.report({"written": 3, "total": 12})`), stored in Walk.stages and emitted as events.
- walk.error is a plain sentence ("Hand tracking failed: ..."); StageState.error has the detail.
- A stage without a function is saved as `skipped`, never `done`, so a later resume runs it.
- persist on a re-walk sends each trade its still-open items again (dispatch.policy.resend_open_items,
  once per closure decision).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from punchwalk.clients import calls
from punchwalk.clients.base import ApiError
from punchwalk.clients.cache import CacheMiss
from punchwalk.clients.cost import CostCapReached
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
from punchwalk.pipeline import (
    classify,
    closures,
    evidence,
    fuse,
    ground,
    points,
    prepare,
    track,
    transcribe,
    verify,
    write_items,
)
from punchwalk.pipeline import params as P
from punchwalk.pipeline.context import SessionFactory, StageContext, StageInputMissing, StageOutcome, walk_dir

log = logging.getLogger(__name__)

StageFn = Callable[[StageContext], StageOutcome]


@dataclass
class StageEvent:
    stage: Stage
    status: Literal["running", "done", "failed", "skipped"]
    counts: dict[str, int] = field(default_factory=dict)
    message: str | None = None


class NotImplementedStage(Exception):
    """Raised by placeholder stages; the runner records them `skipped` (re-run on the next resume)."""


def _not_implemented(ctx: StageContext) -> StageOutcome:
    raise NotImplementedStage


def _persist(ctx: StageContext) -> StageOutcome:
    """Final step: every stage already saved its rows; report the punch list's totals."""
    from punchwalk.core.enums import ItemStatus
    from punchwalk.pipeline.items import walk_items

    with ctx.session() as s:
        items = walk_items(ctx, s)
    review = sum(1 for i in items if ItemStatus(i.status) == ItemStatus.needs_review)
    counts = {"items": len(items), "needs_review": review}
    if ctx.kind == WalkKind.rewalk:  # PLAN 7.4: send each trade its still-open items again
        # resend_open_items sends each still-open item once per closure (closure.decided_at); a re-run
        # that reaches the same decision keeps decided_at, so nothing is sent twice and failed trades retry.
        from punchwalk.dispatch.policy import resend_open_items

        with ctx.session() as s:
            report = resend_open_items(s, ctx.project_id, ctx.walk_id)
            s.commit()
        counts["resent"] = sum(len(v) for v in report.sent.values())
        counts["resend_failed"] = len(report.failed)
    return StageOutcome(counts=counts, message=f"{len(items)} items saved, {review} need a look")


# Keyed by stage value so this module imports even while stage names are being renamed.
STAGE_FUNCTIONS: dict[str, StageFn] = {
    "ingest": prepare.run,
    "transcribe": transcribe.run,
    "classify_turns": classify.run,
    "track_hands": track.run,
    "find_points": points.run,
    "fuse": fuse.run,
    "pick_evidence": evidence.run,
    "write_items": write_items.run,
    "ground_defects": ground.run,
    "verify": verify.run,
    "resolve_closures": closures.run,
    "persist": _persist,
}

# walk.error in plain words (the technical detail stays in the stage's StageState.error).
FAILURE_WORDS: dict[str, str] = {
    "ingest": "Couldn't prepare the video",
    "transcribe": "Transcription failed",
    "track_hands": "Hand tracking failed",
    "find_points": "Couldn't find where you pointed",
    "classify_turns": "Couldn't sort what was said",
    "fuse": "Couldn't match what was said to where you pointed",
    "pick_evidence": "Couldn't pick the photos",
    "write_items": "Writing the items failed",
    "ground_defects": "Outlining the defects failed",
    "verify": "Checking the items failed",
    "resolve_closures": "Checking the fixes failed",
    "persist": "Saving the punch list failed",
}


def stage_order(kind: WalkKind) -> list[Stage]:
    return list(STAGES_REWALK if kind == WalkKind.rewalk else STAGES_INITIAL)


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
    """Technical detail for StageState.error."""
    text = str(err).strip() or type(err).__name__
    return f"{stage.value} failed: {type(err).__name__}: {text}"[:1500]


def plain_error(stage: Stage, err: BaseException) -> str:
    """walk.error: what failed, in words a project manager understands, plus a hint when there is one."""
    words = FAILURE_WORDS.get(stage.value, "Processing failed")
    if isinstance(err, CostCapReached):
        return f"{words}: the live model budget for this build is used up."
    if isinstance(err, ApiError):
        if err.auth_failed or "is not set" in err.body:
            return f"{words}: the model service rejected our key."
        if err.transient:
            return f"{words}: the model service is not responding. Try again in a few minutes."
        return f"{words}: the model service returned an error."
    if isinstance(err, CacheMiss):
        return f"{words}: a recorded model answer is missing (offline mode)."
    from punchwalk.pipeline.ingest import IngestError

    if isinstance(err, IngestError):
        detail = str(err).splitlines()[0][:200]
        return f"{words}: {detail}"
    return f"{words}."


def is_retryable(err: BaseException) -> bool:
    """5xx / 429 / timeouts / connection errors. Never cache misses, the cost cap or missing inputs."""
    if isinstance(err, CacheMiss | CostCapReached | StageInputMissing):
        return False
    if isinstance(err, ApiError):
        return err.transient
    import httpx

    return isinstance(err, httpx.TimeoutException | httpx.TransportError | TimeoutError | ConnectionError)


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

    def on_progress(counts: dict[str, int]) -> None:
        _set_stage(ctx.session_factory, ctx.walk_id,
                   StageState(stage=stage, status=StageStatus.running, started_at=started, counts=counts))
        _emit(progress, StageEvent(stage, "running", counts))

    ctx.on_progress = on_progress
    attempt = 0
    try:
        while True:
            attempt += 1
            try:
                with calls.call_context(walk_id=ctx.walk_id, stage=stage.value):
                    outcome = fn(ctx)
                break
            except NotImplementedStage:
                _set_stage(ctx.session_factory, ctx.walk_id, StageState(
                    stage=stage, status=StageStatus.skipped, started_at=started, finished_at=utcnow(),
                    duration_ms=int((time.monotonic() - t0) * 1000)))
                _emit(progress, StageEvent(stage, "skipped", {}, "not built yet"))
                return
            except Exception as err:
                if attempt > P.STAGE_RETRIES or not is_retryable(err):
                    raise
                wait = P.STAGE_RETRY_BACKOFF_S[min(attempt - 1, len(P.STAGE_RETRY_BACKOFF_S) - 1)]
                log.warning("walk %s: %s attempt %d failed (%s); retrying in %.0fs",
                            ctx.walk_id, stage.value, attempt, err, wait)
                _emit(progress, StageEvent(stage, "running", {}, "Retrying after an error"))
                time.sleep(wait)
    except BaseException as err:
        buffer.flush(ctx.session_factory)
        message = readable_error(stage, err)  # technical: StageState.error and the log only
        plain = plain_error(stage, err)  # plain words: walk.error, StageState.message, the event
        log.error("walk %s: %s", ctx.walk_id, message)
        _set_stage(ctx.session_factory, ctx.walk_id, StageState(
            stage=stage, status=StageStatus.failed, started_at=started, finished_at=utcnow(),
            duration_ms=int((time.monotonic() - t0) * 1000), error=message, message=plain,
        ), walk_status=WalkStatus.failed, error=plain)
        _emit(progress, StageEvent(stage, "failed", {}, plain))
        raise
    finally:
        ctx.on_progress = None
    buffer.flush(ctx.session_factory)
    _set_stage(ctx.session_factory, ctx.walk_id, StageState(
        stage=stage, status=StageStatus.done, started_at=started, finished_at=utcnow(),
        duration_ms=int((time.monotonic() - t0) * 1000), counts=dict(outcome.counts), message=outcome.message,
    ))
    _emit(progress, StageEvent(stage, "done", dict(outcome.counts), outcome.message))
