"""Trades screen: per-trade summary, dispatch preview and send, sent history (PLAN 6.1).

The logic lives in `punchwalk/dispatch/service.py`; these routes translate its errors:
`DispatchRefused` -> 400/404/409 with its sentence, `DispatchFailed` (Slack or email refused) -> 502.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, col, select

from punchwalk.api import schemas as S
from punchwalk.api import views
from punchwalk.api.errors import ApiError
from punchwalk.api.routes.projects import get_project
from punchwalk.core.enums import Channel, Trade
from punchwalk.core.models import Dispatch
from punchwalk.db import get_session
from punchwalk.dispatch import service

router = APIRouter(prefix="/api/projects/{project_id}/trades", tags=["trades"])


def send_errors(exc: service.DispatchRefused | service.DispatchFailed) -> ApiError:
    if isinstance(exc, service.DispatchRefused):
        return ApiError(exc.status, str(exc))
    return ApiError(502, str(exc), error="channel_failed")


@router.get("", response_model=S.TradeListOut)
def list_trades(project_id: str, session: Session = Depends(get_session)) -> S.TradeListOut:
    return views.trade_list_out(session, get_project(session, project_id))


@router.get("/{trade}/preview", response_model=S.DispatchPreviewOut)
def dispatch_preview(
    project_id: str,
    trade: Trade,
    channel: Channel | None = Query(None),
    session: Session = Depends(get_session),
) -> S.DispatchPreviewOut:
    """Exactly what would be sent now: open + sent-but-still-open items (needs-a-look items wait)."""
    return service.preview(session, get_project(session, project_id), trade, channel)


@router.post("/{trade}/dispatch", response_model=S.DispatchOut, status_code=201)
def dispatch_send(
    project_id: str, trade: Trade, body: S.DispatchCreate, session: Session = Depends(get_session)
) -> S.DispatchOut:
    project = get_project(session, project_id)
    try:
        dispatch = service.send_items(session, project, trade, body.item_ids, body.channel)
    except (service.DispatchRefused, service.DispatchFailed) as exc:
        raise send_errors(exc) from exc
    return views.dispatch_out(dispatch, project)


@router.get("/{trade}/dispatches", response_model=S.DispatchListOut)
def list_dispatches(project_id: str, trade: Trade, session: Session = Depends(get_session)) -> S.DispatchListOut:
    project = get_project(session, project_id)
    rows = session.exec(
        select(Dispatch)
        .where(Dispatch.project_id == project.id, Dispatch.trade == trade)
        .order_by(col(Dispatch.sent_at).desc())
    ).all()
    return S.DispatchListOut(
        project_id=project.id, trade=trade, dispatches=[views.dispatch_out(d, project) for d in rows]
    )
