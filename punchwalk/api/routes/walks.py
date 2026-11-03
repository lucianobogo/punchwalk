"""Walks: list, detail, processing progress (poll + SSE), retry, re-walk link, overlay, model calls."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlmodel import Session, col, select

from punchwalk.api import schemas as S
from punchwalk.api import views
from punchwalk.api.errors import ApiError, not_found
from punchwalk.api.jobs import active_job, enqueue_walk_job, notify_worker
from punchwalk.api.overlay import get_overlay
from punchwalk.core.enums import JobStatus, WalkKind, WalkStatus
from punchwalk.core.models import Item, Walk, utcnow
from punchwalk.db import get_session, session_factory

router = APIRouter(prefix="/api/walks", tags=["walks"])

SSE_POLL_S = 0.5
SSE_KEEPALIVE_S = 15.0
SSE_MAX_S = 30 * 60.0


def get_walk(session: Session, walk_id: str) -> Walk:
    walk = session.get(Walk, walk_id)
    if walk is None:
        raise not_found("This walk")
    return walk


@router.get("", response_model=S.WalkListOut)
def list_walks(project_id: str | None = Query(None), session: Session = Depends(get_session)) -> S.WalkListOut:
    stmt = select(Walk).order_by(col(Walk.created_at).desc())
    if project_id:
        stmt = stmt.where(Walk.project_id == project_id)
    return S.WalkListOut(walks=[views.walk_out(session, w) for w in session.exec(stmt).all()])


@router.get("/{walk_id}", response_model=S.WalkDetailOut)
def read_walk(walk_id: str, session: Session = Depends(get_session)) -> S.WalkDetailOut:
    return views.walk_detail_out(session, get_walk(session, walk_id))


# --------------------------------------------------------------------------- progress


def progress_for(session: Session, walk: Walk) -> S.JobProgressOut:
    job = views.latest_job(session, walk.id)
    if job is None:
        raise ApiError(404, "This walk has no processing job yet.")
    return views.job_progress_out(session, job, walk)


@router.get("/{walk_id}/progress", response_model=S.JobProgressOut)
def read_progress(walk_id: str, session: Session = Depends(get_session)) -> S.JobProgressOut:
    return progress_for(session, get_walk(session, walk_id))


def _snapshot(walk_id: str) -> S.JobProgressOut | None:
    with session_factory() as s:
        walk = s.get(Walk, walk_id)
        if walk is None:
            return None
        job = views.latest_job(s, walk_id)
        return views.job_progress_out(s, job, walk) if job else None


@router.get("/{walk_id}/events", include_in_schema=False)
async def stream_progress(walk_id: str, request: Request, session: Session = Depends(get_session)):
    """SSE: a `data:` line (JobProgressOut JSON) whenever progress changes; ends when the job does."""
    progress_for(session, get_walk(session, walk_id))  # 404s before the stream starts

    async def events() -> AsyncIterator[str]:
        last, last_sent, started = "", time.monotonic(), time.monotonic()
        while time.monotonic() - started < SSE_MAX_S:
            if await request.is_disconnected():
                return
            snap = await asyncio.to_thread(_snapshot, walk_id)
            if snap is None:
                yield "event: end\ndata: {}\n\n"
                return
            body = snap.model_dump_json()
            if body != last:
                last, last_sent = body, time.monotonic()
                yield f"data: {body}\n\n"
            elif time.monotonic() - last_sent > SSE_KEEPALIVE_S:
                last_sent = time.monotonic()
                yield ": keepalive\n\n"
            if snap.status in (JobStatus.done, JobStatus.failed):
                yield "event: end\ndata: {}\n\n"
                return
            await asyncio.sleep(SSE_POLL_S)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{walk_id}/retry", response_model=S.JobProgressOut)
def retry_walk(
    walk_id: str, force: bool = Query(False), session: Session = Depends(get_session)
) -> S.JobProgressOut:
    """Queue a new processing job for a failed walk (completed stages are skipped unless force)."""
    walk = get_walk(session, walk_id)
    running = active_job(session, walk.id)
    if running is not None:
        raise ApiError(409, "This walk is already being processed.")
    if walk.status != WalkStatus.failed and not force:
        raise ApiError(409, "Only a walk that failed can be retried.")
    walk.status = WalkStatus.uploaded
    walk.error = None
    walk.updated_at = utcnow()
    session.add(walk)
    enqueue_walk_job(session, walk.id, force=force)
    session.commit()
    notify_worker()
    return progress_for(session, walk)


@router.post("/{walk_id}/rewalk-link", response_model=S.WalkOut)
def link_rewalk(walk_id: str, body: S.RewalkLinkIn, session: Session = Depends(get_session)) -> S.WalkOut:
    """Mark this walk as a re-walk of `initial_walk_id` (same project, a first walk)."""
    walk = get_walk(session, walk_id)
    target = session.get(Walk, body.initial_walk_id)
    if target is None or target.project_id != walk.project_id:
        raise ApiError(400, "That first walk isn't in this project.")
    if target.id == walk.id:
        raise ApiError(400, "A walk can't be a re-walk of itself.")
    if target.kind != WalkKind.initial:
        raise ApiError(400, "A re-walk checks a first walk, not another re-walk.")
    if walk.kind == WalkKind.initial:
        created = views.live_items(session, Item.origin_walk_id == walk.id)
        if created:
            raise ApiError(409, f"This walk already created {len(created)} items, so it can't become a re-walk.")
    walk.kind = WalkKind.rewalk
    walk.rewalk_of = target.id
    walk.updated_at = utcnow()
    session.add(walk)
    session.commit()
    return views.walk_out(session, walk)


# --------------------------------------------------------------------------- overlay, calls, summary


@router.get("/{walk_id}/overlay", response_model=S.OverlayOut)
def read_overlay(walk_id: str, session: Session = Depends(get_session)) -> S.OverlayOut:
    return get_overlay(session, get_walk(session, walk_id))


@router.get("/{walk_id}/model-calls", response_model=S.ModelCallListOut)
def read_model_calls(walk_id: str, session: Session = Depends(get_session)) -> S.ModelCallListOut:
    walk = get_walk(session, walk_id)
    return S.ModelCallListOut(walk_id=walk.id, calls=views.walk_calls(session, walk.id))


@router.get("/{walk_id}/processing", response_model=S.ProcessingSummaryOut)
def read_processing(walk_id: str, session: Session = Depends(get_session)) -> S.ProcessingSummaryOut:
    return views.processing_summary_out(session, get_walk(session, walk_id))


@router.get("/{walk_id}/rewalk-summary", response_model=S.RewalkSummaryOut)
def read_rewalk_summary(walk_id: str, session: Session = Depends(get_session)) -> S.RewalkSummaryOut:
    walk = get_walk(session, walk_id)
    if walk.kind != WalkKind.rewalk:
        raise ApiError(404, "This is a first walk; only re-walks have a re-walk summary.")
    return views.rewalk_summary_out(session, walk)
