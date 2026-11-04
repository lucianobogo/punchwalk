"""Re-walk loop end to end (PLAN 7.1-7.4) on recorded synthetic walks, fully offline.

First walk: fixtures/pipeline/synth_walk (3 items: kitchen knob #1, hallway switch plate #2, bedroom
scuff #3). Re-walk: fixtures/pipeline/synth_rewalk, the same three scenes: the kitchen defect is gone
("Kitchen cabinet knob, fixed."), the hallway one is still drawn but called fixed (the false claim),
the bedroom one is still there ("still there"). Every model call of both walks replays from the
recorded caches (`scripts/record.py --full --rooms Kitchen Hallway Bedroom`, walk2 as the re-walk).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlmodel import select

from punchwalk.config import get_settings
from punchwalk.core.enums import (
    REVIEW_FALSE_FIX,
    ClosureDecision,
    HistoryAction,
    HistoryActor,
    ItemStatus,
    Stage,
    StageStatus,
    VisionStatus,
    VoiceStatus,
    WalkKind,
    WalkStatus,
)
from punchwalk.core.models import Closure, Dispatch, Item, ModelCall, Point, Walk
from punchwalk.pipeline.closures import REVIEW_FIX_UNCLEAR
from punchwalk.pipeline.items import add_history
from punchwalk.pipeline.runner import run_walk
from tests.conftest import FIXTURES
from tests.test_pipeline_integration import db, make_walk  # noqa: F401  (db is a fixture)

SYNTH_RW = FIXTURES / "pipeline" / "synth_rewalk"

if shutil.which("ffmpeg") is None:
    pytest.skip("ffmpeg not installed", allow_module_level=True)


def make_rewalk(factory, initial_id: str) -> str:
    data = Path(get_settings().DATA_DIR)
    with factory() as s:
        first = s.get(Walk, initial_id)
        walk = Walk(project_id=first.project_id, kind=WalkKind.rewalk, rewalk_of=initial_id)
        s.add(walk)
        s.commit()
        walk_id = walk.id
    shutil.copytree(SYNTH_RW / "media", data / "walks" / walk_id)
    shutil.copytree(SYNTH_RW / "cache", data / "cache", dirs_exist_ok=True)
    return walk_id


def first_walk_reviewed(factory) -> str:
    """Process the first walk, then a PM approves its two flagged items (Needs a look → Open)."""
    walk_id = make_walk(factory)
    run_walk(walk_id, session_factory=factory)
    with factory() as s:
        for item in s.exec(select(Item).where(Item.status == ItemStatus.needs_review)).all():
            item.status, item.review_reason = ItemStatus.open, None
            add_history(item, HistoryActor.human, HistoryAction.status_changed, field="status",
                        from_="needs_review", to="open")
            s.add(item)
        s.commit()
    return walk_id


def _items(s) -> list[Item]:
    return list(s.exec(select(Item).order_by(Item.number)).all())


def test_rewalk_signs_off_flags_false_claim_and_resends(db):  # noqa: F811
    first = first_walk_reviewed(db)
    rewalk = make_rewalk(db, first)
    run_walk(rewalk, session_factory=db)
    data = Path(get_settings().DATA_DIR)

    with db() as s:
        walk = s.get(Walk, rewalk)
        assert walk.status == WalkStatus.ready
        assert walk.stages["resolve_closures"]["status"] == StageStatus.done
        assert walk.stages["resolve_closures"]["counts"] == {
            "checked": 3, "signed_off": 1, "still_open": 1, "needs_review": 1, "not_checked": 0, "new_items": 0}
        assert walk.stages["persist"]["counts"]["resent"] == 1
        knob, plate, scuff = _items(s)
        assert len(_items(s)) == 3  # the re-walk created no items
        cl = {i.number: Closure.model_validate(i.closure) for i in (knob, plate, scuff)}

        # 7.3: claimed fixed + fixed on camera -> signed off
        assert knob.status == ItemStatus.closed and cl[1].decision == ClosureDecision.signed_off
        assert (cl[1].voice_status, cl[1].vision_status) == (VoiceStatus.fixed, VisionStatus.fixed)
        # claimed fixed but the defect is still there -> needs a look, never signed off. (On this synthetic scene
        # the photo shows a scuff while the words say "switch plate", so the check can't confirm the fix:
        # REVIEW_FIX_UNCLEAR; the real re-walk's false claims get REVIEW_FALSE_FIX, see test_pipeline_rewalk_units.)
        assert plate.status == ItemStatus.needs_review
        assert plate.review_reason in (REVIEW_FALSE_FIX, REVIEW_FIX_UNCLEAR)
        assert cl[2].decision == ClosureDecision.needs_review
        assert cl[2].vision_status in (VisionStatus.still_visible, VisionStatus.unclear)
        assert cl[2].voice_status == VoiceStatus.fixed
        # said not fixed -> still open, sent again
        assert scuff.status == ItemStatus.dispatched and cl[3].decision == ClosureDecision.still_open
        assert cl[3].voice_status == VoiceStatus.not_fixed
        sent = s.exec(select(Dispatch)).all()
        assert [d.item_ids for d in sent] == [[scuff.id]]

        # closure fields the API reads (rewalk summary, overlay links via the point's turn)
        points = {p.turn_id: p for p in s.exec(select(Point).where(Point.walk_id == rewalk)).all()}
        for item in (knob, plate, scuff):
            c = cl[item.number]
            assert c.rewalk_id == rewalk and c.quote and c.decided_at
            assert c.rewalk_turn_id in points and abs(points[c.rewalk_turn_id].t - c.after_t) <= 3.0
            assert (data / c.after_image).is_file() and (data / c.after_frame).is_file()
            assert c.after_image.startswith(f"walks/{rewalk}/items/{item.id}/")
            assert (data / "walks" / rewalk / "items" / item.id / "before_after.jpg").is_file()
            if "before_after" in Closure.model_fields:
                assert c.before_after == f"walks/{rewalk}/items/{item.id}/before_after.jpg"
        assert any(h["note"] == "Signed off on the re-walk: fixed on camera." for h in knob.history)
        mc = s.exec(select(ModelCall).where(ModelCall.walk_id == rewalk)).all()
        assert mc and all(m.cached for m in mc)
        # 3 matches + 3 visual checks (+ a confirming second look for a sign-off the pixels didn't confirm)
        assert 6 <= sum(1 for m in mc if m.stage == Stage.resolve_closures) <= 8

        # the API's re-walk summary sees the same thing
        from punchwalk.api.views import rewalk_summary_out

        summary = rewalk_summary_out(s, walk)
        assert (summary.counts.signed_off, summary.counts.still_open, summary.counts.needs_a_look,
                summary.counts.not_checked) == (1, 1, 1, 0)
        assert summary.needs_a_look[0].reason in (REVIEW_FALSE_FIX, REVIEW_FIX_UNCLEAR)
        assert summary.signed_off[0].before_image_url and summary.signed_off[0].after_image_url

    # re-running the re-walk recomputes from scratch: same decisions, nothing sent twice, no duplicate history
    with db() as s:
        hist_before = {i.id: len(i.history) for i in _items(s)}
        decided_before = {i.id: i.closure["decided_at"] for i in _items(s)}
    run_walk(rewalk, session_factory=db, force=True)
    with db() as s:
        knob, plate, scuff = _items(s)
        assert (knob.status, plate.status, scuff.status) == (ItemStatus.closed, ItemStatus.needs_review,
                                                            ItemStatus.dispatched)
        assert [len(d.item_ids) for d in s.exec(select(Dispatch)).all()] == [1]
        assert s.get(Walk, rewalk).stages["persist"]["counts"]["resent"] == 0
        assert {i.id: len(i.history) for i in _items(s)} == hist_before
        assert {i.id: i.closure["decided_at"] for i in _items(s)} == decided_before  # same decisions, same time


def test_rewalk_keeps_a_persons_later_decision(db):  # noqa: F811
    first = first_walk_reviewed(db)
    rewalk = make_rewalk(db, first)
    run_walk(rewalk, session_factory=db)
    with db() as s:
        plate = _items(s)[1]
        plate.status, plate.review_reason = ItemStatus.closed, None  # PM signs it off by hand
        add_history(plate, HistoryActor.human, HistoryAction.status_changed, field="status",
                    from_="needs_review", to="closed")
        s.add(plate)
        s.commit()
    run_walk(rewalk, session_factory=db, force=True)
    with db() as s:
        assert _items(s)[1].status == ItemStatus.closed  # not overwritten by the re-run
