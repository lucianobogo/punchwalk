"""Trades screen: per-trade summary, dispatch preview/send, sent history.

Seam for PLAN 6.1/6.2: `preview` and `dispatch` return 501 until `punchwalk/dispatch/` exists.
Plug it in here: build the DispatchPreviewOut from the trade's open + dispatched items, and on send
write a `Dispatch` row, move items to `dispatched`, and append a `dispatched` history entry each.
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

router = APIRouter(prefix="/api/projects/{project_id}/trades", tags=["trades"])


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
    get_project(session, project_id)
    raise ApiError(501, "Sending to trades is coming in 6.1.")


@router.post("/{trade}/dispatch", response_model=S.DispatchOut, status_code=201)
def dispatch_send(
    project_id: str, trade: Trade, body: S.DispatchCreate, session: Session = Depends(get_session)
) -> S.DispatchOut:
    get_project(session, project_id)
    raise ApiError(501, "Sending to trades is coming in 6.1.")


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
