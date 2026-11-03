"""In-process job worker: polls the `jobs` table and runs the pipeline for each queued walk.

- No Redis, no Celery (CLAUDE.md "Stack"). Jobs are rows; the worker is `MAX_CONCURRENT_JOBS` daemon
  threads started by the app's lifespan. Each thread claims one queued job at a time, so at most
  `MAX_CONCURRENT_JOBS` (2) walks are processed at once (PLAN 4.4).
- Claiming is a conditional UPDATE (`... WHERE status='queued'`), so two threads (or two processes
  sharing the volume) never run the same job.
- The pipeline runner (`punchwalk.pipeline.runner.run_walk`, Track A) persists Turn/Point/Item/
  ModelCall rows and `Walk.stages` itself. The worker only moves the Job and the Walk status
  (uploaded -> processing -> ready | failed), mirrors StageEvents into `Job.progress`, and retries a
  failed job up to `MAX_JOB_ATTEMPTS` times in total (the runner retries each stage on its own and
  skips completed stages, so a retry resumes where it stopped).
- After a successful run of a first walk, verified items are sent automatically
  (`punchwalk.dispatch.policy`, PLAN 6.2; `AUTO_SEND=0` turns it off).
- `PUNCHWALK_WORKER=0` disables the threads (tests); `run_pending_jobs_once()` drives jobs
  synchronously instead. Tests swap the runner with `set_runner()`.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from sqlalchemy import update
from sqlmodel import Session, col, select

from punchwalk.config import get_settings
from punchwalk.core.enums import JobKind, JobStatus, Stage, WalkStatus
from punchwalk.core.models import Job, Walk, utcnow
from punchwalk.db import session_factory

log = logging.getLogger("punchwalk.jobs")

MAX_CONCURRENT_JOBS = 2
MAX_JOB_ATTEMPTS = 3  # first run + 2 retries (PLAN 2.5)
POLL_INTERVAL_S = 1.0
MAX_ERROR_CHARS = 600

# Errors a retry can't fix: bad media, the cost cap, a cache miss in readonly mode.
NON_RETRYABLE = frozenset({"MediaError", "IngestError", "CostCapReached", "CacheMiss", "RunnerUnavailable"})

Runner = Callable[..., None]
_runner_override: Runner | None = None


class RunnerUnavailable(RuntimeError):
    """The pipeline runner module isn't importable (Track A hasn't shipped it in this build)."""


def set_runner(runner: Runner | None) -> None:
    """Use `runner` instead of the pipeline's `run_walk` (tests); None restores the real one."""
    global _runner_override
    _runner_override = runner


def get_runner() -> Runner:
    """`punchwalk.pipeline.runner.run_walk`, imported lazily (or the test override)."""
    if _runner_override is not None:
        return _runner_override
    try:
        from punchwalk.pipeline.runner import run_walk
    except ImportError as exc:
        raise RunnerUnavailable(
            "The processing pipeline isn't installed in this build, so the walk can't be processed yet."
        ) from exc
    return run_walk


def worker_enabled() -> bool:
    return get_settings().PUNCHWALK_WORKER > 0


# --------------------------------------------------------------------------- queue


def enqueue_walk_job(session: Session, walk_id: str, *, force: bool = False) -> Job:
    """Add a queued job for `walk_id` (caller commits). Wakes the worker after commit via `notify()`."""
    job = Job(kind=JobKind.process_walk, walk_id=walk_id, status=JobStatus.queued, force=force)
    session.add(job)
    return job


def active_job(session: Session, walk_id: str) -> Job | None:
    return session.exec(
        select(Job).where(Job.walk_id == walk_id, col(Job.status).in_([JobStatus.queued, JobStatus.running]))
    ).first()


def recover_interrupted_jobs() -> int:
    """Jobs left `running` by a restart go back to the queue (the runner resumes them)."""
    with session_factory() as s:
        jobs = s.exec(select(Job).where(Job.status == JobStatus.running)).all()
        for job in jobs:
            job.status = JobStatus.queued
            job.progress = {**(job.progress or {}), "message": "Resuming after a restart"}
            s.add(job)
        s.commit()
        return len(jobs)


def claim_next_job() -> str | None:
    """Atomically move the oldest queued job to running; returns its id (None if the queue is empty)."""
    with session_factory() as s:
        candidates = s.exec(
            select(Job.id).where(Job.status == JobStatus.queued).order_by(col(Job.created_at), col(Job.id)).limit(5)
        ).all()
        for job_id in candidates:
            now = utcnow()
            result = s.exec(  # type: ignore[call-overload]
                update(Job)
                .where(col(Job.id) == job_id, col(Job.status) == JobStatus.queued)
                .values(status=JobStatus.running, attempts=Job.attempts + 1, started_at=now, updated_at=now)
            )
            s.commit()
            if result.rowcount == 1:
                return job_id
    return None


# --------------------------------------------------------------------------- running one job


def _event_dict(event: Any) -> dict[str, Any]:
    """A StageEvent (dataclass / pydantic / dict) -> {"stage", "status", "counts", "message"}."""
    get = event.get if isinstance(event, dict) else (lambda k, d=None: getattr(event, k, d))
    stage = get("stage")
    stage = stage.value if isinstance(stage, Stage) else (str(stage) if stage is not None else None)
    status = get("status")
    status = getattr(status, "value", status)
    counts = get("counts") or {}
    return {
        "stage": stage,
        "status": str(status) if status is not None else None,
        "counts": {str(k): int(v) for k, v in dict(counts).items() if isinstance(v, int | float)},
        "message": get("message"),
    }


def _progress_callback(job_id: str, walk_id: str) -> Callable[[Any], None]:
    def on_progress(event: Any) -> None:
        try:
            data = _event_dict(event)
            with session_factory() as s:
                job = s.get(Job, job_id)
                if job is not None:
                    job.progress = data
                    s.add(job)
                walk = s.get(Walk, walk_id)
                if walk is not None and walk.status == WalkStatus.uploaded:
                    walk.status = WalkStatus.processing
                    s.add(walk)
                s.commit()
        except Exception:  # progress must never break a run
            log.exception("job %s: could not record progress", job_id)

    return on_progress


def _readable(exc: BaseException) -> str:
    text = str(exc).strip() or type(exc).__name__
    return text if len(text) <= MAX_ERROR_CHARS else text[: MAX_ERROR_CHARS - 1] + "…"


def _retryable(exc: BaseException) -> bool:
    if getattr(exc, "retryable", None) is False:
        return False
    return not any(cls.__name__ in NON_RETRYABLE for cls in type(exc).__mro__)


def run_job(job_id: str) -> JobStatus:
    """Run one claimed (running) job to the end. Returns the job's final status for this attempt."""
    with session_factory() as s:
        job = s.get(Job, job_id)
        if job is None or job.walk_id is None:
            return JobStatus.failed
        walk = s.get(Walk, job.walk_id)
        if walk is None:
            job.status, job.error, job.finished_at = JobStatus.failed, "The walk was deleted.", utcnow()
            s.add(job)
            s.commit()
            return JobStatus.failed
        walk.status = WalkStatus.processing
        walk.error = None
        s.add(walk)
        s.commit()
        walk_id, force, attempts = walk.id, job.force, job.attempts

    try:
        runner = get_runner()
        runner(walk_id, session_factory=session_factory, progress=_progress_callback(job_id, walk_id), force=force)
    except Exception as exc:
        log.exception("job %s (walk %s) attempt %d failed", job_id, walk_id, attempts)
        return _finish_failed(job_id, walk_id, exc, retry=_retryable(exc) and attempts < MAX_JOB_ATTEMPTS)
    return _finish_ok(job_id, walk_id)


def _finish_ok(job_id: str, walk_id: str) -> JobStatus:
    from punchwalk.api.overlay import invalidate_overlay

    with session_factory() as s:
        job = s.get(Job, job_id)
        walk = s.get(Walk, walk_id)
        failed_by_runner = walk is not None and walk.status == WalkStatus.failed
        if job is not None:
            job.status = JobStatus.failed if failed_by_runner else JobStatus.done
            job.error = (walk.error if walk else None) if failed_by_runner else None
            job.finished_at = utcnow()
            if not failed_by_runner:
                job.progress = {**(job.progress or {}), "message": None}
            s.add(job)
        if walk is not None and not failed_by_runner:
            walk.status = WalkStatus.ready
            walk.error = None
            s.add(walk)
        s.commit()
    invalidate_overlay(walk_id)
    if not failed_by_runner:
        auto_send(walk_id)
    return JobStatus.failed if failed_by_runner else JobStatus.done


def auto_send(walk_id: str) -> None:
    """PLAN 6.2: send verified items of a processed first walk (no-op for re-walks or AUTO_SEND=0).

    Idempotent, so it's safe if the pipeline's persist stage already did it. Never fails the job.
    """
    try:
        from punchwalk.dispatch.policy import auto_dispatch_after_processing

        with session_factory() as s:
            report = auto_dispatch_after_processing(s, walk_id)
        if report.sent or report.held or report.failed:
            log.info(
                "walk %s: auto-sent %d item(s), held %d, %d trade(s) failed",
                walk_id,
                len(report.sent_items),
                len(report.held),
                len(report.failed),
            )
    except Exception:
        log.exception("walk %s: auto-send crashed; the items stay open", walk_id)


def _finish_failed(job_id: str, walk_id: str, exc: BaseException, *, retry: bool) -> JobStatus:
    with session_factory() as s:
        job = s.get(Job, job_id)
        walk = s.get(Walk, walk_id)
        # The runner writes a readable "<stage>: <what happened>" to walk.error before re-raising.
        runner_said = walk.error if walk is not None and walk.status == WalkStatus.failed else None
        message = runner_said or _readable(exc)
        if job is not None:
            job.error = message
            if retry:
                job.status = JobStatus.queued
                job.progress = {**(job.progress or {}), "message": "Retrying after an error"}
            else:
                job.status = JobStatus.failed
                job.finished_at = utcnow()
            s.add(job)
        if walk is not None:
            if retry:  # still processing: the next attempt resumes from the failed stage
                walk.status, walk.error = WalkStatus.processing, None
            else:
                walk.status, walk.error = WalkStatus.failed, message
            s.add(walk)
        s.commit()
    return JobStatus.queued if retry else JobStatus.failed


def run_pending_jobs_once(max_jobs: int | None = None) -> int:
    """Run queued jobs in the calling thread until the queue is empty (retries included).

    Returns how many attempts ran. Tests use this with `PUNCHWALK_WORKER=0`.
    """
    ran = 0
    while max_jobs is None or ran < max_jobs:
        job_id = claim_next_job()
        if job_id is None:
            break
        run_job(job_id)
        ran += 1
    return ran


# --------------------------------------------------------------------------- worker threads


class JobWorker:
    """`MAX_CONCURRENT_JOBS` threads, each: claim a job -> run it -> repeat; sleep when idle."""

    def __init__(self, threads: int = MAX_CONCURRENT_JOBS, poll_s: float = POLL_INTERVAL_S) -> None:
        self.n_threads = threads
        self.poll_s = poll_s
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        for i in range(self.n_threads):
            t = threading.Thread(target=self._loop, name=f"punchwalk-job-{i + 1}", daemon=True)
            t.start()
            self._threads.append(t)
        log.info("job worker started (%d threads)", self.n_threads)

    def notify(self) -> None:
        self._wake.set()

    def stop(self, timeout_s: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        for t in self._threads:
            t.join(timeout=timeout_s)
        self._threads.clear()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                job_id = claim_next_job()
            except Exception:
                log.exception("job worker: could not poll the queue")
                job_id = None
            if job_id is None:
                self._wake.wait(self.poll_s)
                self._wake.clear()
                continue
            try:
                run_job(job_id)
            except Exception:  # run_job handles runner errors; this is a bug in bookkeeping
                log.exception("job worker: job %s crashed outside the runner", job_id)


_worker: JobWorker | None = None


def start_worker() -> JobWorker | None:
    global _worker
    if not worker_enabled():
        return None
    if _worker is None:
        _worker = JobWorker()
    _worker.start()
    return _worker


def stop_worker() -> None:
    global _worker
    if _worker is not None:
        _worker.stop()
        _worker = None


def notify_worker() -> None:
    if _worker is not None:
        _worker.notify()
