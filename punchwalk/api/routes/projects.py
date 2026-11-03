"""Projects, the trades directory, walk upload and the project's punch list."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlmodel import Session, col, select

from punchwalk.api import schemas as S
from punchwalk.api import uploads, views
from punchwalk.api.errors import ApiError, not_found
from punchwalk.api.guards import client_ip, upload_limiter
from punchwalk.api.jobs import enqueue_walk_job, notify_worker
from punchwalk.config import get_settings
from punchwalk.core.enums import TRADE_LABEL, ItemStatus, Severity, Trade, WalkKind, WalkStatus
from punchwalk.core.models import ClipInfo, Item, Project, TradeContact, Walk, new_id, utcnow
from punchwalk.db import get_session

router = APIRouter(prefix="/api/projects", tags=["projects"])


def get_project(session: Session, project_id: str) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise not_found("This project")
    return project


def _directory(entries: list[S.TradeContactIn], existing: list[TradeContact]) -> list[dict]:
    """Validate a trades directory. A missing slack_webhook keeps the stored one; "" removes it."""
    seen: set[Trade] = set()
    out = []
    for e in entries:
        if e.trade in seen:
            raise ApiError(400, f"{TRADE_LABEL[e.trade]} is listed twice in the trades directory.")
        seen.add(e.trade)
        if not e.company.strip():
            raise ApiError(400, f"Add a company name for {TRADE_LABEL[e.trade]}.")
        webhook = e.slack_webhook
        if webhook is None:
            webhook = next((c.slack_webhook for c in existing if c.trade == e.trade), None)
        if webhook and not webhook.startswith("https://"):
            raise ApiError(400, f"The Slack webhook for {TRADE_LABEL[e.trade]} must start with https://.")
        contact = TradeContact(
            trade=e.trade,
            company=e.company.strip(),
            contact=e.contact or None,
            email=(e.email or "").strip() or None,
            phone=e.phone or None,
            slack_channel=e.slack_channel or None,
            slack_webhook=webhook or None,
        )
        out.append(contact.model_dump(mode="json"))
    return out


def _clean_list(values: list[str]) -> list[str]:
    out: list[str] = []
    for v in values:
        v = v.strip()
        if v and v not in out:
            out.append(v)
    return out


# --------------------------------------------------------------------------- projects


@router.get("", response_model=S.ProjectListOut)
def list_projects(session: Session = Depends(get_session)) -> S.ProjectListOut:
    projects = session.exec(select(Project).order_by(col(Project.created_at).desc())).all()
    return S.ProjectListOut(projects=[views.project_out(session, p) for p in projects])


@router.post("", response_model=S.ProjectOut, status_code=201)
def create_project(body: S.ProjectCreate, session: Session = Depends(get_session)) -> S.ProjectOut:
    project = Project(
        name=body.name.strip(),
        address=(body.address or "").strip() or None,
        trades=_directory(body.trades, []),
        keywords=_clean_list(body.keywords),
        rooms=_clean_list(body.rooms),
    )
    if not project.name:
        raise ApiError(400, "Give the project a name.")
    session.add(project)
    session.commit()
    return views.project_out(session, project)


@router.get("/{project_id}", response_model=S.ProjectOut)
def read_project(project_id: str, session: Session = Depends(get_session)) -> S.ProjectOut:
    return views.project_out(session, get_project(session, project_id))


@router.patch("/{project_id}", response_model=S.ProjectOut)
def patch_project(project_id: str, body: S.ProjectPatch, session: Session = Depends(get_session)) -> S.ProjectOut:
    project = get_project(session, project_id)
    sent = body.model_fields_set
    if "name" in sent:
        if not (body.name or "").strip():
            raise ApiError(400, "Give the project a name.")
        project.name = (body.name or "").strip()
    if "address" in sent:
        project.address = (body.address or "").strip() or None
    if "trades" in sent:
        project.trades = _directory(body.trades or [], views.contacts(project))
    if "keywords" in sent:
        project.keywords = _clean_list(body.keywords or [])
    if "rooms" in sent:
        project.rooms = _clean_list(body.rooms or [])
    project.updated_at = utcnow()
    session.add(project)
    session.commit()
    return views.project_out(session, project)


# --------------------------------------------------------------------------- walks


@router.get("/{project_id}/walks", response_model=S.WalkListOut)
def list_project_walks(project_id: str, session: Session = Depends(get_session)) -> S.WalkListOut:
    project = get_project(session, project_id)
    walks = session.exec(
        select(Walk).where(Walk.project_id == project_id).order_by(col(Walk.created_at).desc())
    ).all()
    return S.WalkListOut(walks=[views.walk_out(session, w, project) for w in walks])


def _resolve_rewalk_of(session: Session, project: Project, kind: WalkKind, rewalk_of: str | None) -> str | None:
    if rewalk_of:
        target = session.get(Walk, rewalk_of)
        if target is None or target.project_id != project.id:
            raise ApiError(400, "The walk this re-walk checks isn't in this project.")
        if target.kind != WalkKind.initial:
            raise ApiError(400, "A re-walk checks a first walk, not another re-walk.")
        return target.id
    if kind != WalkKind.rewalk:
        return None
    latest = session.exec(
        select(Walk)
        .where(Walk.project_id == project.id, Walk.kind == WalkKind.initial)
        .order_by(col(Walk.created_at).desc())
    ).first()
    return latest.id if latest else None


@router.post("/{project_id}/walks", response_model=S.UploadOut, status_code=201)
async def upload_walk(project_id: str, request: Request, session: Session = Depends(get_session)) -> S.UploadOut:
    """multipart: kind=initial|rewalk, rewalk_of=<walk id>?, clips=<file> (repeated, in walk order)."""
    project = get_project(session, project_id)
    ip = client_ip(request)
    if get_settings().DEMO_MODE:
        upload_limiter.check(ip)

    walk_id = new_id("wlk")
    parsed = await uploads.receive_upload(request, walk_id)
    try:
        kind = WalkKind(parsed.fields.get("kind") or WalkKind.initial)
    except ValueError:
        uploads.discard(walk_id)
        raise ApiError(400, "kind must be “initial” (first walk) or “rewalk”.") from None
    try:
        rewalk_of = _resolve_rewalk_of(session, project, kind, parsed.fields.get("rewalk_of") or None)
    except ApiError:
        uploads.discard(walk_id)
        raise
    if rewalk_of:
        kind = WalkKind.rewalk
    await uploads.validate_clips(walk_id, parsed)

    clips, offset = [], 0.0
    for c in parsed.clips:
        clips.append(
            ClipInfo(order=c.order, filename=c.filename, duration_s=round(c.duration_s, 3), offset_s=round(offset, 3))
        )
        offset += c.duration_s
    recorded = next((c.created_at for c in parsed.clips if c.created_at), None)
    walk = Walk(
        id=walk_id,
        project_id=project.id,
        kind=kind,
        status=WalkStatus.uploaded,
        rewalk_of=rewalk_of,
        clips=[c.model_dump(mode="json") for c in clips],
        duration_s=round(offset, 3),
        recorded_at=(recorded or datetime.now(UTC)).astimezone(UTC),
    )
    session.add(walk)
    session.flush()
    job = enqueue_walk_job(session, walk.id)
    session.commit()
    if get_settings().DEMO_MODE:
        upload_limiter.record(ip)
    notify_worker()
    return S.UploadOut(walk_id=walk.id, job_id=job.id, status=walk.status)


# --------------------------------------------------------------------------- items


@router.get("/{project_id}/items", response_model=S.ItemListOut)
def list_items(
    project_id: str,
    trade: list[Trade] | None = Query(None),
    severity: list[Severity] | None = Query(None),
    room: list[str] | None = Query(None),
    status: list[ItemStatus] | None = Query(None),
    q: str | None = Query(None, max_length=200),
    sort: Literal["walk", "severity"] = Query("walk"),
    session: Session = Depends(get_session),
) -> S.ItemListOut:
    project = get_project(session, project_id)
    everything = views.live_items(session, Item.project_id == project.id)
    ctx = views.ItemContext.load(session, [project.id])
    picked = [
        i
        for i in everything
        if (not trade or i.trade in trade)
        and (not severity or i.severity in severity)
        and (not room or i.room in room)
        and (not status or i.status in status)
        and (not q or views.matches_query(i, q))
    ]
    if sort == "severity":
        picked.sort(key=views.severity_order_key)
    else:
        picked.sort(key=lambda i: views.walk_order_key(i, ctx))
    return S.ItemListOut(
        project_id=project.id,
        items=[views.item_out(i, ctx) for i in picked],
        facets=views.facets(everything, project.rooms or []),
        counts=views.item_counts(i.status for i in everything),
        total=len(picked),
    )


# --------------------------------------------------------------------------- report


@router.get("/{project_id}/report.pdf", include_in_schema=False)
def report_pdf(project_id: str, session: Session = Depends(get_session)):
    get_project(session, project_id)
    # Seam for PLAN 6.3: render punchwalk/render report HTML -> WeasyPrint -> application/pdf.
    raise ApiError(501, "The PDF punch report is coming in 6.3.")
