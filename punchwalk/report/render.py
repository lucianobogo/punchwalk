"""PDF punch report (PLAN 6.3): Jinja2 HTML -> WeasyPrint, portrait US Letter.

Page 1 is the cover: project, address, date, totals by status and by trade. Then the items, grouped
by room in the project's walk order, each with its evidence crop, number, title, AI summary, action,
trade, severity and status. The last block is a sign-off with lines for the PM and the contractor.

Images are embedded from DATA_DIR by file URL. WeasyPrint needs pango/gobject system libraries
(installed in the Docker image); when they're missing, `render_pdf` raises `ReportUnavailable`
and the HTML still renders (`render_html`).
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlmodel import Session, col, select

from punchwalk.config import get_settings
from punchwalk.core.enums import (
    SEVERITY_LABEL,
    STATUS_LABEL,
    TRADE_LABEL,
    ItemStatus,
    Severity,
    Trade,
    format_item_number,
)
from punchwalk.core.models import Item, Project, Walk

TEMPLATES = Path(__file__).parent / "templates"
NO_ROOM = "Other"
_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html", "j2"]))


class ReportUnavailable(RuntimeError):
    """WeasyPrint can't run here (its system libraries aren't installed)."""


@dataclass
class ReportItem:
    number: str
    title: str
    description: str
    action: str
    trade: str
    severity: str
    is_safety: bool
    status: str
    status_key: str
    location: str | None
    quote: str | None
    image_uri: str | None


def _image_uri(item: Item) -> str | None:
    ev = item.evidence if isinstance(item.evidence, dict) else {}
    data_dir = Path(get_settings().DATA_DIR)
    for key in ("crop", "image"):
        rel = ev.get(key)
        if isinstance(rel, str) and rel:
            path = (data_dir / rel.lstrip("/")).resolve()
            if path.is_file() and data_dir.resolve() in path.parents:
                return path.as_uri()
    return None


def _status_key(status: ItemStatus) -> str:
    return {ItemStatus.closed: "signed_off", ItemStatus.needs_review: "needs_review"}.get(status, "open")


def build_context(session: Session, project: Project, *, now: datetime | None = None) -> dict[str, Any]:
    items = sorted(
        session.exec(select(Item).where(Item.project_id == project.id, col(Item.dismissed_at).is_(None))).all(),
        key=lambda i: i.number,
    )
    walks = session.exec(select(Walk).where(Walk.project_id == project.id)).all()
    last_walk = max((w.recorded_at or w.created_at for w in walks), default=None)

    rooms: OrderedDict[str, list[ReportItem]] = OrderedDict((r, []) for r in project.rooms or [])
    for it in items:
        room = it.room or NO_ROOM
        rooms.setdefault(room, []).append(
            ReportItem(
                number=format_item_number(it.number),
                title=it.title,
                description=it.description or "",
                action=it.action or "",
                trade=TRADE_LABEL[it.trade],
                severity=SEVERITY_LABEL[it.severity],
                is_safety=it.severity == Severity.safety,
                status=STATUS_LABEL[it.status],
                status_key=_status_key(it.status),
                location=it.location_detail,
                quote=it.quote,
                image_uri=_image_uri(it),
            )
        )
    by_status = OrderedDict(
        [
            ("Open", sum(1 for i in items if i.status in (ItemStatus.open, ItemStatus.dispatched))),
            ("Needs a look", sum(1 for i in items if i.status == ItemStatus.needs_review)),
            ("Signed off", sum(1 for i in items if i.status == ItemStatus.closed)),
        ]
    )
    by_trade = []
    for trade in Trade:
        mine = [i for i in items if i.trade == trade]
        if mine:
            by_trade.append(
                {
                    "trade": TRADE_LABEL[trade],
                    "open": sum(1 for i in mine if i.status in (ItemStatus.open, ItemStatus.dispatched)),
                    "needs_review": sum(1 for i in mine if i.status == ItemStatus.needs_review),
                    "signed_off": sum(1 for i in mine if i.status == ItemStatus.closed),
                    "total": len(mine),
                }
            )
    now = now or datetime.now(UTC)
    return {
        "project": {"name": project.name, "address": project.address},
        "generated": now.strftime("%B %-d, %Y"),
        "last_walk": last_walk.strftime("%B %-d, %Y") if last_walk else None,
        "total": len(items),
        "by_status": by_status,
        "by_trade": by_trade,
        "rooms": [(room, entries) for room, entries in rooms.items() if entries],
    }


def render_html(context: dict[str, Any]) -> str:
    return _env.get_template("report.html.j2").render(**context)


def render_pdf(html: str) -> bytes:
    try:
        from weasyprint import HTML  # lazy: needs pango/gobject system libraries
    except (OSError, ImportError) as exc:
        raise ReportUnavailable(
            "PDF reports need the WeasyPrint system libraries (Pango), which aren't installed on this server."
        ) from exc
    return HTML(string=html, base_url=str(Path(get_settings().DATA_DIR))).write_pdf()


def project_report_pdf(session: Session, project: Project) -> bytes:
    return render_pdf(render_html(build_context(session, project)))


def report_filename(project: Project) -> str:
    slug = "".join(c if c.isalnum() else "-" for c in project.name.lower()).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return f"punch-report-{slug or 'project'}.pdf"
