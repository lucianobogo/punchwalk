"""Rows -> API schemas. Every response body is built here, from `punchwalk/api/schemas.py` classes.

JSON columns are read leniently (`lenient()` drops unknown keys and returns None for broken data),
so one odd row written by a newer pipeline never takes a whole list down.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError
from sqlmodel import Session, col, func, select

from punchwalk.api import schemas as S
from punchwalk.api.progress import (
    HEADLINE_STEPS_INITIAL,
    HEADLINE_STEPS_REWALK,
    STAGE_MODEL,
    current_stage,
    overall_progress,
    stage_rows,
    stage_states,
)
from punchwalk.config import get_settings
from punchwalk.core.enums import (
    NOT_CHECKED_ON_REWALK,
    REVIEW_FALSE_FIX,
    STAGE_TITLE,
    ClosureDecision,
    Grounding,
    ItemStatus,
    JobStatus,
    Orientation,
    Provider,
    Severity,
    Stage,
    StageStatus,
    Trade,
    WalkKind,
    WalkStatus,
    model_label,
)
from punchwalk.core.models import (
    XY,
    Box,
    ClipInfo,
    Closure,
    Dispatch,
    Evidence,
    HistoryEvent,
    Item,
    Job,
    ModelCall,
    Point,
    Project,
    TradeContact,
    Turn,
    Verification,
    Walk,
    WalkMediaPaths,
)
from punchwalk.db import as_utc

log = logging.getLogger("punchwalk.api")

M = TypeVar("M", bound=BaseModel)

SEVERITY_RANK: dict[Severity, int] = {Severity.safety: 0, Severity.functional: 1, Severity.cosmetic: 2}
OPEN_STATUSES = (ItemStatus.open, ItemStatus.dispatched)


# =========================================================================== helpers


def media_url(rel: str | None) -> str | None:
    """DATA_DIR-relative path -> root-relative `/media/...` URL (URLs pass through unchanged)."""
    if not rel:
        return None
    if rel.startswith(("/media/", "http://", "https://")):
        return rel
    return "/media/" + rel.lstrip("/")


def lenient(model: type[M], data: Any) -> M | None:
    """Validate a JSON-column value, ignoring keys the model doesn't know. None if unusable."""
    if not isinstance(data, dict):
        return None
    known = set(model.model_fields)
    known |= {f.alias for f in model.model_fields.values() if f.alias}
    try:
        return model.model_validate({k: v for k, v in data.items() if k in known})
    except ValidationError as exc:
        log.warning("unreadable %s: %s", model.__name__, exc.errors()[:2])
        return None


def box_out(data: Any) -> S.BoxOut | None:
    if isinstance(data, list | tuple) and len(data) == 4:
        data = dict(zip(("x1", "y1", "x2", "y2"), data, strict=True))
    box = lenient(Box, data)
    return S.BoxOut(x1=box.x1, y1=box.y1, x2=box.x2, y2=box.y2) if box else None


def xy_out(data: Any) -> S.XYOut | None:
    if isinstance(data, list | tuple) and len(data) == 2:
        data = {"x": data[0], "y": data[1]}
    xy = lenient(XY, data)
    return S.XYOut(x=xy.x, y=xy.y) if xy else None


def item_counts(statuses: Iterable[ItemStatus]) -> S.ItemCountsOut:
    st = list(statuses)
    opened = sum(1 for s in st if s in OPEN_STATUSES)
    review = sum(1 for s in st if s == ItemStatus.needs_review)
    return S.ItemCountsOut(
        todo=opened + review,
        open=opened,
        needs_review=review,
        signed_off=sum(1 for s in st if s == ItemStatus.closed),
        all=len(st),
    )


def contacts(project: Project) -> list[TradeContact]:
    out = []
    for raw in project.trades or []:
        c = lenient(TradeContact, raw)
        if c:
            out.append(c)
    return out


def contact_for(project: Project, trade: Trade) -> TradeContact | None:
    return next((c for c in contacts(project) if c.trade == trade), None)


def trade_contact_out(c: TradeContact | None) -> S.TradeContactOut | None:
    if c is None:
        return None
    return S.TradeContactOut(
        trade=c.trade,
        company=c.company,
        contact=c.contact,
        email=c.email,
        phone=c.phone,
        slack_channel=c.slack_channel,
        has_slack_webhook=bool(c.slack_webhook),
    )


def live_items(session: Session, *conditions: Any) -> list[Item]:
    stmt = select(Item).where(col(Item.dismissed_at).is_(None), *conditions)
    return list(session.exec(stmt).all())


# =========================================================================== projects


def project_out(session: Session, project: Project) -> S.ProjectOut:
    statuses = session.exec(
        select(Item.status).where(Item.project_id == project.id, col(Item.dismissed_at).is_(None))
    ).all()
    walks = session.exec(select(Walk).where(Walk.project_id == project.id)).all()
    last = max((as_utc(w.recorded_at or w.created_at) for w in walks), default=None)
    return S.ProjectOut(
        id=project.id,
        name=project.name,
        address=project.address,
        trades=[o for c in contacts(project) if (o := trade_contact_out(c))],
        keywords=list(project.keywords or []),
        rooms=list(project.rooms or []),
        counts=item_counts(statuses),
        walk_count=len(walks),
        last_walk_at=last,
        created_at=as_utc(project.created_at),
        updated_at=as_utc(project.updated_at),
    )


# =========================================================================== walks


def read_ingest_meta(walk: Walk) -> dict[str, Any] | None:
    """meta.json (punchwalk.pipeline.ingest.IngestMeta) as a plain dict, or None."""
    media = lenient(WalkMediaPaths, walk.media) or WalkMediaPaths()
    rel = media.meta_json or f"walks/{walk.id}/meta.json"
    path = Path(get_settings().DATA_DIR) / rel
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _video_facts(walk: Walk) -> dict[str, Any]:
    """width/height/fps/duration/orientation from the row, filled from meta.json when missing."""
    facts: dict[str, Any] = {
        "width": walk.width,
        "height": walk.height,
        "fps": walk.fps,
        "duration_s": walk.duration_s,
        "orientation": walk.orientation,
    }
    if walk.width is None or walk.height is None or walk.duration_s is None:
        meta = read_ingest_meta(walk) or {}
        display = meta.get("display") or {}
        facts["width"] = facts["width"] or display.get("width")
        facts["height"] = facts["height"] or display.get("height")
        facts["fps"] = facts["fps"] or display.get("fps")
        facts["duration_s"] = facts["duration_s"] or meta.get("duration_s")
        if facts["orientation"] is None and meta.get("orientation") in ("portrait", "landscape"):
            facts["orientation"] = Orientation(meta["orientation"])
    if facts["duration_s"] is None and walk.clips:
        total = sum(float(c.get("duration_s") or 0) for c in walk.clips)
        facts["duration_s"] = round(total, 3) if total else None
    if facts["orientation"] is None and facts["width"] and facts["height"]:
        facts["orientation"] = (
            Orientation.portrait if facts["height"] > facts["width"] else Orientation.landscape
        )
    return facts


def latest_job(session: Session, walk_id: str) -> Job | None:
    return session.exec(
        select(Job).where(Job.walk_id == walk_id).order_by(col(Job.created_at).desc(), col(Job.id).desc())
    ).first()


def checked_by_rewalk(session: Session, walk: Walk) -> list[Item]:
    """Items whose closure points at this re-walk (any decision), in number order."""
    if walk.kind != WalkKind.rewalk:
        return []
    items = live_items(session, Item.project_id == walk.project_id, col(Item.closure).is_not(None))
    out = [i for i in items if isinstance(i.closure, dict) and i.closure.get("rewalk_id") == walk.id]
    return sorted(out, key=lambda i: i.number)


def _decision(item: Item) -> ClosureDecision | None:
    cl = lenient(Closure, item.closure)
    return cl.decision if cl else None


def walk_counts(session: Session, walk: Walk) -> S.WalkCountsOut:
    points = session.exec(select(func.count()).select_from(Point).where(Point.walk_id == walk.id)).one()
    turns = session.exec(select(func.count()).select_from(Turn).where(Turn.walk_id == walk.id)).one()
    if walk.kind == WalkKind.rewalk:
        decisions = [_decision(i) for i in checked_by_rewalk(session, walk)]
        not_checked = decisions.count(ClosureDecision.not_checked)
        return S.WalkCountsOut(
            items=len(decisions) - not_checked,
            points=points,
            turns=turns,
            needs_review=decisions.count(ClosureDecision.needs_review),
            signed_off=decisions.count(ClosureDecision.signed_off),
            still_open=decisions.count(ClosureDecision.still_open),
            not_checked=not_checked,
        )
    statuses = [i.status for i in live_items(session, Item.origin_walk_id == walk.id)]
    return S.WalkCountsOut(
        items=len(statuses),
        points=points,
        turns=turns,
        needs_review=statuses.count(ItemStatus.needs_review),
        signed_off=statuses.count(ItemStatus.closed),
        still_open=sum(1 for s in statuses if s in OPEN_STATUSES),
        not_checked=0,
    )


def walk_out(session: Session, walk: Walk, project: Project | None = None) -> S.WalkOut:
    project = project or session.get(Project, walk.project_id)
    media = lenient(WalkMediaPaths, walk.media) or WalkMediaPaths()
    clips = [c for raw in walk.clips or [] if (c := lenient(ClipInfo, raw))]
    job = latest_job(session, walk.id)
    facts = _video_facts(walk)
    return S.WalkOut(
        id=walk.id,
        project_id=walk.project_id,
        project_name=project.name if project else "",
        kind=walk.kind,
        status=walk.status,
        rewalk_of=walk.rewalk_of,
        job_id=job.id if job else None,
        recorded_at=as_utc(walk.recorded_at),
        created_at=as_utc(walk.created_at),
        updated_at=as_utc(walk.updated_at),
        clips=[
            S.ClipOut(order=c.order, filename=c.filename, duration_s=c.duration_s, offset_s=c.offset_s)
            for c in sorted(clips, key=lambda c: c.order)
        ],
        stages=stage_rows(walk),
        media=S.WalkMediaOut(display_mp4=media_url(media.display_mp4), poster=media_url(media.poster)),
        duration_s=facts["duration_s"],
        fps=facts["fps"],
        width=facts["width"],
        height=facts["height"],
        orientation=facts["orientation"],
        device=walk.device,
        error=walk.error,
        counts=walk_counts(session, walk),
    )


# =========================================================================== items


@dataclass
class ItemContext:
    """What ItemOut needs beyond the row, loaded once per request."""

    projects: dict[str, Project] = field(default_factory=dict)
    walks: dict[str, Walk] = field(default_factory=dict)
    dispatches: dict[str, list[Dispatch]] = field(default_factory=dict)  # project_id -> sends, oldest first

    @classmethod
    def load(cls, session: Session, project_ids: Iterable[str]) -> ItemContext:
        ctx = cls()
        for pid in set(project_ids):
            project = session.get(Project, pid)
            if project:
                ctx.projects[pid] = project
            for w in session.exec(select(Walk).where(Walk.project_id == pid)).all():
                ctx.walks[w.id] = w
            ctx.dispatches[pid] = list(
                session.exec(
                    select(Dispatch).where(Dispatch.project_id == pid).order_by(col(Dispatch.sent_at))
                ).all()
            )
        return ctx


def evidence_out(data: Any) -> S.EvidenceOut | None:
    ev = lenient(Evidence, data)
    if ev is None:
        return None
    return S.EvidenceOut(
        t=ev.t,
        image_url=media_url(ev.image) or "",
        raw_url=media_url(ev.raw) or "",
        crop_url=media_url(ev.crop) or "",
        width=ev.width,
        height=ev.height,
        defect_svg=ev.defect_svg,
        defect_box=box_out(ev.defect_box.model_dump()) if ev.defect_box else None,
        crop_box=box_out(ev.crop_box.model_dump()) if ev.crop_box else None,
        pointer=xy_out(ev.pointer.model_dump()) if ev.pointer else None,
        pointing_frame=media_url(ev.pointing_frame),
        hand_box=box_out(ev.hand_box.model_dump()) if ev.hand_box else None,
        grounding=ev.grounding or Grounding.none,
        sam_concept=ev.sam_concept,
    )


def verification_out(data: Any) -> S.VerificationOut | None:
    v = lenient(Verification, data)
    if v is None:
        return None
    return S.VerificationOut(
        consistent=v.consistent,
        note=v.note,
        issues=list(v.issues),
        provider=v.provider,
        checked_at=as_utc(v.checked_at),
    )


def closure_out(item: Item) -> S.ClosureOut | None:
    cl = lenient(Closure, item.closure)
    if cl is None:
        return None
    ev = lenient(Evidence, item.evidence)
    return S.ClosureOut(
        rewalk_id=cl.rewalk_id,
        rewalk_turn_id=cl.rewalk_turn_id,
        quote=cl.quote,
        after_t=cl.after_t,
        before_image_url=media_url(ev.crop) if ev else None,
        after_image_url=media_url(cl.after_image),
        after_frame_url=media_url(cl.after_frame),
        voice_status=cl.voice_status,
        vision_status=cl.vision_status,
        decision=cl.decision,
        note=cl.note,
        decided_at=as_utc(cl.decided_at),
    )


def history_out(raw: Sequence[Any]) -> list[S.HistoryEntryOut]:
    out = []
    for entry in raw or []:
        h = lenient(HistoryEvent, entry)
        if h is None:
            continue
        out.append(
            S.HistoryEntryOut(
                at=as_utc(h.at),
                actor=h.actor,
                action=h.action,
                field=h.field,
                from_=h.from_,
                to=h.to,
                note=h.note,
            )
        )
    return sorted(out, key=lambda h: h.at)


def dispatch_summary(item: Item, ctx: ItemContext) -> S.DispatchSummaryOut | None:
    sends = [d for d in ctx.dispatches.get(item.project_id, []) if d.ok and item.id in (d.item_ids or [])]
    if not sends:
        return None
    last = max(sends, key=lambda d: as_utc(d.sent_at))
    project = ctx.projects.get(item.project_id)
    c = contact_for(project, last.trade) if project else None
    return S.DispatchSummaryOut(
        dispatch_id=last.id,
        trade=last.trade,
        company=c.company if c else None,
        channel=last.channel,
        sent_at=as_utc(last.sent_at),
        times_sent=len(sends),
    )


def walk_video_url(walk: Walk | None) -> str | None:
    if walk is None:
        return None
    media = lenient(WalkMediaPaths, walk.media) or WalkMediaPaths()
    return media_url(media.display_mp4)


def item_out(item: Item, ctx: ItemContext) -> S.ItemOut:
    return S.ItemOut(
        id=item.id,
        project_id=item.project_id,
        number=item.number,
        origin_walk_id=item.origin_walk_id,
        point_id=item.point_id,
        turn_ids=list(item.turn_ids or []),
        title=item.title,
        description=item.description or "",
        action=item.action or "",
        trade=item.trade,
        room=item.room,
        location_detail=item.location_detail,
        severity=item.severity,
        status=item.status,
        review_reason=item.review_reason,
        quote=item.quote,
        quote_start_s=item.quote_start_s,
        quote_end_s=item.quote_end_s,
        video_url=walk_video_url(ctx.walks.get(item.origin_walk_id)),
        video_start_s=item.video_start_s,
        video_end_s=item.video_end_s,
        confidence=item.confidence,
        evidence=evidence_out(item.evidence),
        verification=verification_out(item.verification),
        closure=closure_out(item),
        history=history_out(item.history),
        dispatch=dispatch_summary(item, ctx),
        created_at=as_utc(item.created_at),
        updated_at=as_utc(item.updated_at),
    )


def item_moment(item: Item) -> float:
    """When the item was called out in its walk (quote start, else evidence time, else clip start)."""
    if item.quote_start_s is not None:
        return item.quote_start_s
    ev = item.evidence if isinstance(item.evidence, dict) else {}
    if isinstance(ev.get("t"), int | float):
        return float(ev["t"])
    return item.video_start_s


def walk_order_key(item: Item, ctx: ItemContext) -> tuple:
    walk = ctx.walks.get(item.origin_walk_id)
    walk_t = as_utc(walk.recorded_at or walk.created_at).timestamp() if walk else 0.0
    return (walk_t, item_moment(item), item.number)


def severity_order_key(item: Item) -> tuple:
    return (SEVERITY_RANK.get(item.severity, 9), item.number)


def facets(items: Sequence[Item], rooms_in_order: Sequence[str]) -> S.FacetsOut:
    def count(values: Iterable[Any]) -> dict[Any, int]:
        out: dict[Any, int] = {}
        for v in values:
            if v is not None:
                out[v] = out.get(v, 0) + 1
        return out

    trade = count(i.trade for i in items)
    severity = count(i.severity for i in items)
    status = count(i.status for i in items)
    room = count(i.room for i in items)
    known = [r for r in rooms_in_order if r in room]
    others = sorted(r for r in room if r not in rooms_in_order)
    return S.FacetsOut(
        trade={t: trade[t] for t in Trade if t in trade},
        severity={s: severity[s] for s in Severity if s in severity},
        room={r: room[r] for r in [*known, *others]},
        status={s: status[s] for s in ItemStatus if s in status},
    )


def matches_query(item: Item, q: str) -> bool:
    q = q.strip().lower()
    if not q:
        return True
    number = q.lstrip("#").lstrip("0") or "0"
    if number.isdigit() and int(number) == item.number:
        return True
    hay = " ".join(
        x for x in (item.title, item.description, item.action, item.room, item.location_detail, item.quote) if x
    ).lower()
    return q in hay


# =========================================================================== transcript & points


def turn_outs(session: Session, walk: Walk) -> list[S.TurnOut]:
    turns = session.exec(
        select(Turn).where(Turn.walk_id == walk.id).order_by(col(Turn.idx), col(Turn.start_ms))
    ).all()
    links: dict[str, list[str]] = {}
    related = live_items(session, Item.project_id == walk.project_id)
    for it in related:
        for tid in it.turn_ids or []:
            links.setdefault(tid, []).append(it.id)
        if isinstance(it.closure, dict) and it.closure.get("rewalk_turn_id"):
            links.setdefault(it.closure["rewalk_turn_id"], []).append(it.id)
    return [
        S.TurnOut(
            id=t.id,
            walk_id=t.walk_id,
            idx=t.idx,
            start_ms=t.start_ms,
            end_ms=t.end_ms,
            text=t.text,
            kind=t.kind,
            room=t.room,
            claimed=t.claimed,
            item_ids=sorted(set(links.get(t.id, []))),
        )
        for t in turns
    ]


def point_items(session: Session, walk: Walk, points: Sequence[Point]) -> dict[str, Item]:
    """point_id -> the item it created (initial walk) or checked (re-walk).

    Initial walks link through `Item.point_id`. A re-walk's points have no stored link: a point
    matches the checked item whose re-walk quote is the point's transcript line
    (`closure.rewalk_turn_id == point.turn_id`), else the one whose re-walk frame
    (`closure.after_t`) is within 3 s of the point (closest wins).
    """
    out: dict[str, Item] = {}
    ids = {p.id for p in points}
    for it in live_items(session, Item.project_id == walk.project_id, col(Item.point_id).is_not(None)):
        if it.point_id in ids:
            out[it.point_id] = it
    if walk.kind != WalkKind.rewalk:
        return out
    checked = [(i, lenient(Closure, i.closure)) for i in checked_by_rewalk(session, walk)]
    taken = {i.id for i in out.values()}
    for p in points:
        if p.id in out:
            continue
        best: tuple[float, Item] | None = None
        for it, cl in checked:
            if it.id in taken or cl is None:
                continue
            if p.turn_id and cl.rewalk_turn_id == p.turn_id:
                best = (-1.0, it)
                break
            if cl.after_t is not None and abs(cl.after_t - p.t) <= 3.0:
                d = abs(cl.after_t - p.t)
                if best is None or d < best[0]:
                    best = (d, it)
        if best:
            out[p.id] = best[1]
            taken.add(best[1].id)
    return out


def walk_points(session: Session, walk_id: str) -> list[Point]:
    return list(session.exec(select(Point).where(Point.walk_id == walk_id).order_by(col(Point.t))).all())


def point_outs(session: Session, walk: Walk) -> list[S.PointOut]:
    points = walk_points(session, walk.id)
    linked = point_items(session, walk, points)
    out = []
    for p in points:
        it = linked.get(p.id)
        out.append(
            S.PointOut(
                id=p.id,
                walk_id=p.walk_id,
                item_id=it.id if it else None,
                item_number=it.number if it else None,
                number=p.number,
                t=p.t,
                frame=p.frame,
                fingertip_x=p.fingertip_x,
                fingertip_y=p.fingertip_y,
                hand_box=box_out(p.hand_box) or S.BoxOut(x1=0, y1=0, x2=0, y2=0),
                hold_s=p.hold_s,
                sam_object_ids=list(p.sam_object_ids or []),
                mean_conf=p.mean_conf,
                turn_id=p.turn_id,
            )
        )
    return out


def walk_items(session: Session, walk: Walk) -> list[Item]:
    """Initial walk: the items it created. Re-walk: the items it checked."""
    if walk.kind == WalkKind.rewalk:
        return [i for i in checked_by_rewalk(session, walk) if _decision(i) != ClosureDecision.not_checked]
    return sorted(live_items(session, Item.origin_walk_id == walk.id), key=lambda i: (item_moment(i), i.number))


def walk_detail_out(session: Session, walk: Walk) -> S.WalkDetailOut:
    items = walk_items(session, walk)
    ctx = ItemContext.load(session, [walk.project_id])
    return S.WalkDetailOut(
        walk=walk_out(session, walk, ctx.projects.get(walk.project_id)),
        turns=turn_outs(session, walk),
        points=point_outs(session, walk),
        items=[item_out(i, ctx) for i in items],
    )


# =========================================================================== jobs


def job_progress_out(session: Session, job: Job, walk: Walk) -> S.JobProgressOut:
    rows = stage_rows(walk)
    done = job.status == JobStatus.done
    progress = 1.0 if done else overall_progress(rows)
    stage = None if done else current_stage(rows, job.progress)
    items_so_far: list[S.ItemThumbOut] = []
    expected: int | None = None
    message = None
    if walk.kind == WalkKind.initial:
        items = sorted(live_items(session, Item.origin_walk_id == walk.id), key=lambda i: i.number)
        items_so_far = [
            S.ItemThumbOut(
                id=i.id,
                number=i.number,
                title=i.title,
                crop_url=media_url((lenient(Evidence, i.evidence) or _NO_EVIDENCE).crop or None),
            )
            for i in items
        ]
        fuse = stage_states(walk).get(Stage.fuse)
        if fuse and fuse.status == StageStatus.done:
            c = fuse.counts or {}
            expected = c["items"] if "items" in c else c.get("pointed", 0) + c.get("voice_only", 0)
        if done:
            message = f"{len(items)} {'item' if len(items) == 1 else 'items'} ready"
    elif done:
        n = walk_counts(session, walk).items
        message = f"{n} {'item' if n == 1 else 'items'} checked"
    if job.status == JobStatus.running:
        message = (job.progress or {}).get("message") or None
    return S.JobProgressOut(
        job_id=job.id,
        walk_id=walk.id,
        kind=job.kind,
        status=job.status,
        walk_status=walk.status,
        attempts=job.attempts,
        stage=stage,
        stages=rows,
        progress=progress,
        message=message,
        items_so_far=items_so_far,
        items_expected=expected,
        error=job.error if job.status == JobStatus.failed else (walk.error if walk.status == WalkStatus.failed else None),
        created_at=as_utc(job.created_at),
        started_at=as_utc(job.started_at),
        finished_at=as_utc(job.finished_at),
        updated_at=as_utc(job.updated_at),
    )


_NO_EVIDENCE = Evidence(t=0, image="", raw="", crop="", width=0, height=0)


# =========================================================================== model calls & processing


def model_call_out(call: ModelCall) -> S.ModelCallOut:
    from datetime import timedelta

    created = as_utc(call.created_at)
    return S.ModelCallOut(
        id=call.id,
        walk_id=call.walk_id,
        item_id=call.item_id,
        stage=call.stage,
        model=call.model,
        model_label=model_label(call.model),
        provider=call.provider,
        latency_ms=call.latency_ms,
        tokens_in=call.tokens_in,
        tokens_out=call.tokens_out,
        frames=call.frames,
        est_cost_usd=round(call.est_cost_usd or 0.0, 5),
        cached=call.cached,
        ok=call.ok,
        error=call.error,
        # ModelCall rows are written when a call returns; the timeline wants when it started.
        started_at=created - timedelta(milliseconds=call.latency_ms or 0),
    )


def walk_calls(session: Session, walk_id: str) -> list[S.ModelCallOut]:
    rows = session.exec(select(ModelCall).where(ModelCall.walk_id == walk_id)).all()
    return sorted((model_call_out(r) for r in rows), key=lambda c: (c.started_at, c.id))


def _step_detail(walk: Walk, stage: Stage, row: S.StageProgressOut) -> str:
    if row.status != StageStatus.done:
        return row.detail or ""
    c = row.counts
    if stage == Stage.transcribe:
        return f"{c.get('lines', 0)} lines with start and end times"
    if stage == Stage.track_hands:
        points = stage_states(walk).get(Stage.find_points)
        n = (points.counts or {}).get("points") if points else None
        if n is None:
            frames = c.get("frames", 0)
            return f"The hand followed through {frames} frames" if frames else "No hands in this walk"
        return f"{n} {'point' if n == 1 else 'points'} found across the whole video" if n else "No pointing in this walk"
    if stage == Stage.fuse:
        return f"Timing and position, computed in the app. {row.detail}."
    if stage == Stage.write_items:
        n = c.get("written", c.get("total", 0))
        return f"Summary, action, trade, room and severity for {n} {'item' if n == 1 else 'items'}"
    if stage == Stage.ground_defects:
        return f"Spark Open names what to outline near your fingertip, SAM draws it. {c.get('outlined', 0)} outlined."
    if stage == Stage.verify:
        return (row.detail or "").replace(", ", ". ", 1) + "."
    if stage == Stage.classify_turns and walk.kind == WalkKind.rewalk:
        n = c.get("status_updates")
        lines = c.get("lines", 0)
        return f"{n} status updates found in {lines} lines" if n is not None else row.detail or ""
    if stage == Stage.resolve_closures:
        return "Each update matched to its item, then the new frame compared with the original photo"
    return row.detail or ""


def processing_summary_out(session: Session, walk: Walk) -> S.ProcessingSummaryOut:
    calls = walk_calls(session, walk.id)
    rows = {r.stage: r for r in stage_rows(walk)}
    headline = HEADLINE_STEPS_REWALK if walk.kind == WalkKind.rewalk else HEADLINE_STEPS_INITIAL
    steps = []
    for stage in headline:
        row = rows.get(stage)
        if row is None or row.status == StageStatus.pending:
            continue
        mine = [c for c in calls if c.stage == stage]
        providers = [c.provider for c in mine if c.ok]
        steps.append(
            S.ProcessingStepOut(
                stage=stage,
                title=STAGE_TITLE[stage],
                model_label=STAGE_MODEL.get(stage),
                provider=providers[0] if providers else None,
                detail=_step_detail(walk, stage, row),
                duration_ms=row.duration_ms or 0,
                calls=len(mine),
                fallback_calls=sum(1 for c in mine if c.provider == Provider.meta_fallback),
                cost_usd=round(sum(c.est_cost_usd for c in mine), 4),
            )
        )
    models: list[str] = []
    for c in calls:
        if c.provider != Provider.meta_fallback and c.model_label not in models:
            models.append(c.model_label)
    started = [r.started_at for r in rows.values() if r.started_at]
    finished = [r.finished_at for r in rows.values() if r.finished_at]
    if started and finished and all(r.status in (StageStatus.done, StageStatus.skipped) for r in rows.values()):
        total_ms = round((max(finished) - min(started)).total_seconds() * 1000)
    else:
        total_ms = sum(r.duration_ms or 0 for r in rows.values())
    return S.ProcessingSummaryOut(
        walk_id=walk.id,
        kind=walk.kind,
        status=walk.status,
        total_duration_ms=max(0, total_ms),
        total_cost_usd=round(sum(c.est_cost_usd for c in calls), 4),
        models_used=models,
        steps=steps,
        calls=calls,
        eval=None,  # Phase 3 fills this from eval/report.md
    )


# =========================================================================== share & re-walk


def share_item_out(item: Item, ctx: ItemContext) -> S.ShareItemOut:
    project = ctx.projects.get(item.project_id)
    walk = ctx.walks.get(item.origin_walk_id)
    c = contact_for(project, item.trade) if project else None
    return S.ShareItemOut(
        token=item.share_token or "",
        project_name=project.name if project else "",
        project_address=project.address if project else None,
        number=item.number,
        title=item.title,
        description=item.description or "",
        action=item.action or "",
        trade=item.trade,
        company=c.company if c else None,
        room=item.room,
        location_detail=item.location_detail,
        severity=item.severity,
        status=item.status,
        quote=item.quote,
        quote_start_s=item.quote_start_s,
        quote_end_s=item.quote_end_s,
        video_url=walk_video_url(walk),
        video_start_s=item.video_start_s,
        video_end_s=item.video_end_s,
        evidence=evidence_out(item.evidence),
        closure=closure_out(item),
        walked_at=as_utc(walk.recorded_at or walk.created_at) if walk else None,
    )


def rewalk_summary_out(session: Session, walk: Walk) -> S.RewalkSummaryOut:
    initial = session.get(Walk, walk.rewalk_of) if walk.rewalk_of else None
    groups: dict[ClosureDecision, list[S.RewalkItemOut]] = {d: [] for d in ClosureDecision}
    for it in checked_by_rewalk(session, walk):
        cl = closure_out(it)
        if cl is None:
            continue
        reason = {
            ClosureDecision.needs_review: it.review_reason or REVIEW_FALSE_FIX,
            ClosureDecision.not_checked: NOT_CHECKED_ON_REWALK,
        }.get(cl.decision)
        groups[cl.decision].append(
            S.RewalkItemOut(
                item_id=it.id,
                number=it.number,
                title=it.title,
                room=it.room,
                trade=it.trade,
                status=it.status,
                decision=cl.decision,
                reason=reason,
                quote=cl.quote,
                before_image_url=cl.before_image_url,
                after_image_url=cl.after_image_url,
                before_at=as_utc(initial.recorded_at or initial.created_at) if initial else None,
                after_at=as_utc(walk.recorded_at or walk.created_at) if cl.after_image_url else None,
            )
        )
    counts = S.RewalkCountsOut(
        checked=sum(len(groups[d]) for d in ClosureDecision if d != ClosureDecision.not_checked),
        signed_off=len(groups[ClosureDecision.signed_off]),
        still_open=len(groups[ClosureDecision.still_open]),
        needs_a_look=len(groups[ClosureDecision.needs_review]),
        not_checked=len(groups[ClosureDecision.not_checked]),
    )
    look = "needs a look" if counts.needs_a_look == 1 else "need a look"
    return S.RewalkSummaryOut(
        walk_id=walk.id,
        initial_walk_id=walk.rewalk_of or "",
        project_id=walk.project_id,
        recorded_at=as_utc(walk.recorded_at),
        counts=counts,
        headline=f"{counts.signed_off} signed off. {counts.still_open} still open. {counts.needs_a_look} {look}.",
        needs_a_look=groups[ClosureDecision.needs_review],
        signed_off=groups[ClosureDecision.signed_off],
        still_open=groups[ClosureDecision.still_open],
        not_checked=groups[ClosureDecision.not_checked],
    )


# =========================================================================== trades


def trade_list_out(session: Session, project: Project) -> S.TradeListOut:
    items = live_items(session, Item.project_id == project.id)
    sends = session.exec(select(Dispatch).where(Dispatch.project_id == project.id)).all()
    directory = [c.trade for c in contacts(project)]
    trades = directory + [t for t in Trade if t not in directory and any(i.trade == t for i in items)]
    out = []
    for t in trades:
        mine = [i for i in items if i.trade == t]
        out.append(
            S.TradeSummaryOut(
                trade=t,
                contact=trade_contact_out(contact_for(project, t)),
                open_count=sum(1 for i in mine if i.status in OPEN_STATUSES),
                needs_review_count=sum(1 for i in mine if i.status == ItemStatus.needs_review),
                signed_off_count=sum(1 for i in mine if i.status == ItemStatus.closed),
                last_sent_at=max((as_utc(d.sent_at) for d in sends if d.trade == t and d.ok), default=None),
            )
        )
    return S.TradeListOut(project_id=project.id, trades=out)


def dispatch_out(d: Dispatch, project: Project | None) -> S.DispatchOut:
    c = contact_for(project, d.trade) if project else None
    return S.DispatchOut(
        id=d.id,
        project_id=d.project_id,
        trade=d.trade,
        company=c.company if c else None,
        channel=d.channel,
        destination=d.destination,
        item_ids=list(d.item_ids or []),
        sent_at=as_utc(d.sent_at),
        ok=d.ok,
        error=d.error,
        payload=dict(d.payload or {}),
    )
