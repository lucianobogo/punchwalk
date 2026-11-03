"""Item detail, edits (logged as human overrides), dismiss, and public share links."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Response
from sqlmodel import Session, select

from punchwalk.api import schemas as S
from punchwalk.api import views
from punchwalk.api.errors import ApiError, not_found
from punchwalk.core.enums import (
    HistoryAction,
    HistoryActor,
    ItemStatus,
    format_item_number,
)
from punchwalk.core.models import HistoryEvent, Item, utcnow
from punchwalk.db import get_session

router = APIRouter(prefix="/api", tags=["items"])

EDITABLE = ("title", "description", "action", "trade", "room", "location_detail", "severity", "status", "review_reason")
REQUIRED = {"title", "trade", "severity", "status"}  # can't be set to null
MAX_LEN = {"title": 120, "description": 2000, "action": 400, "room": 80, "location_detail": 200, "review_reason": 300}
EDIT_NOTE = "Changed by the project manager."
STATUS_NOTES = {
    ItemStatus.closed: "Signed off by the project manager.",
    ItemStatus.open: "Reopened by the project manager.",
    ItemStatus.needs_review: "Marked as needs a look by the project manager.",
    ItemStatus.dispatched: "Marked as sent by the project manager.",
}


def get_item(session: Session, item_id: str) -> Item:
    item = session.get(Item, item_id)
    if item is None:
        raise not_found("This item")
    if item.dismissed_at is not None:
        raise ApiError(404, f"Item {format_item_number(item.number)} was dismissed.")
    return item


def _as_text(value: object) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _history(action: HistoryAction, *, field: str | None, old: object, new: object, note: str | None) -> dict:
    return HistoryEvent(
        at=utcnow(),
        actor=HistoryActor.human,
        action=action,
        field=field,
        from_=_as_text(old),
        to=_as_text(new),
        note=note,
    ).model_dump(mode="json", by_alias=True)


def _clean(field: str, value: object) -> object:
    """Trim text, enforce lengths; "" or null clears optional fields and is refused for required ones."""
    if isinstance(value, str):
        value = value.strip()
        if field in MAX_LEN and len(value) > MAX_LEN[field]:
            label = field.replace("_", " ").capitalize()
            raise ApiError(400, f"{label} is too long (max {MAX_LEN[field]} characters).")
    if value is None or value == "":
        if field in REQUIRED:
            raise ApiError(400, f"{field.capitalize()} can't be empty.")
        return "" if field in ("description", "action") else None
    return value


@router.get("/items/{item_id}", response_model=S.ItemOut)
def read_item(item_id: str, session: Session = Depends(get_session)) -> S.ItemOut:
    item = get_item(session, item_id)
    return views.item_out(item, views.ItemContext.load(session, [item.project_id]))


@router.patch("/items/{item_id}", response_model=S.ItemOut)
def patch_item(item_id: str, body: S.ItemPatch, session: Session = Depends(get_session)) -> S.ItemOut:
    """Every changed field becomes a history entry (actor human, from/to). No-op values are skipped."""
    item = get_item(session, item_id)
    history = list(item.history or [])
    changed = False
    for field in EDITABLE:
        if field not in body.model_fields_set:
            continue
        new = _clean(field, getattr(body, field))
        old = getattr(item, field)
        if _as_text(old) == _as_text(new):
            continue
        setattr(item, field, new)
        changed = True
        if field == "status":
            history.append(
                _history(
                    HistoryAction.status_changed,
                    field="status",
                    old=old,
                    new=new,
                    note=body.note or STATUS_NOTES.get(new),  # type: ignore[arg-type]
                )
            )
            if new != ItemStatus.needs_review and "review_reason" not in body.model_fields_set:
                item.review_reason = None  # the reason no longer applies
        else:
            history.append(_history(HistoryAction.edited, field=field, old=old, new=new, note=body.note or EDIT_NOTE))
    if not changed and body.note:
        history.append(_history(HistoryAction.noted, field=None, old=None, new=None, note=body.note))
        changed = True
    if changed:
        item.history = history
        item.updated_at = utcnow()
        session.add(item)
        session.commit()
    return views.item_out(item, views.ItemContext.load(session, [item.project_id]))


@router.delete("/items/{item_id}", status_code=204)
def dismiss_item(item_id: str, session: Session = Depends(get_session)) -> Response:
    """Soft delete: the item leaves every list; the row and its history stay."""
    item = session.get(Item, item_id)
    if item is None:
        raise not_found("This item")
    if item.dismissed_at is None:
        now = utcnow()
        item.dismissed_at = now
        item.history = [
            *(item.history or []),
            _history(HistoryAction.dismissed, field=None, old=None, new=None, note="Dismissed by the project manager."),
        ]
        item.updated_at = now
        session.add(item)
        session.commit()
    return Response(status_code=204)


# --------------------------------------------------------------------------- share links


def new_share_token(session: Session) -> str:
    while True:
        token = f"shr_{secrets.token_hex(5)}"
        if session.exec(select(Item.id).where(Item.share_token == token)).first() is None:
            return token


@router.post("/items/{item_id}/share", response_model=S.ShareLinkOut)
def share_item(item_id: str, session: Session = Depends(get_session)) -> S.ShareLinkOut:
    """Create (or return the existing) public read-only link for an item."""
    item = get_item(session, item_id)
    if not item.share_token:
        item.share_token = new_share_token(session)
        session.add(item)
        session.commit()
    return S.ShareLinkOut(token=item.share_token, url=f"/share/item/{item.share_token}")


@router.get("/share/{token}", response_model=S.ShareItemOut)
def read_share(token: str, session: Session = Depends(get_session)) -> S.ShareItemOut:
    item = session.exec(select(Item).where(Item.share_token == token)).first()
    if item is None or item.dismissed_at is not None:
        raise ApiError(404, "This link doesn't work any more. Ask the project manager for a new one.")
    return views.share_item_out(item, views.ItemContext.load(session, [item.project_id]))
