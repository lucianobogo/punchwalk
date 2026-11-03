"""fuse (PLAN 1.5, pointing): one item draft per defect mention; provisional Item rows."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from punchwalk.core.enums import ItemStatus, WalkKind
from punchwalk.core.models import Item, Point, Project, Walk
from punchwalk.pipeline.context import StageContext
from punchwalk.pipeline.fuse import REVIEW_ORPHANED, build_drafts, item_id_for, provisional_title, save_items
from punchwalk.pipeline.mentions import build_mentions


def _turn(idx, start, end, text, kind="defect", defects=None):
    return {"id": f"trn_{idx}", "idx": idx, "start_ms": int(start * 1000), "end_ms": int(end * 1000), "text": text,
            "kind": kind, "room": "Kitchen", "defects": [text] if defects is None else defects,
            "continues_previous": False, "status_target": None, "claimed": "fixed" if kind == "status_update" else None}


TURNS = [
    _turn(0, 1.0, 3.0, "upper cabinet: knob missing."),
    _turn(1, 5.0, 6.0, "Kitchen.", kind="location", defects=[]),
    _turn(2, 8.0, 9.5, "two", defects=["outlet cover cracked", "baseboard scuffed"]),
    _turn(3, 28.5, 29.8, "hallway light, fixed", kind="status_update", defects=[]),
]


def test_one_draft_per_defect_mention_with_or_without_point():
    mentions = build_mentions(TURNS)
    drafts = build_drafts("wlk_1", mentions, {"0.0": "pnt_a", "2.1": "pnt_b"}, duration_s=30.0)
    assert [d["mention_key"] for d in drafts] == ["0.0", "2.0", "2.1"]  # no item for location/status turns
    assert [d["kind"] for d in drafts] == ["pointed", "voice_only", "pointed"]
    assert [d["point_id"] for d in drafts] == ["pnt_a", None, "pnt_b"]
    first = drafts[0]
    assert first["video_start_s"] == 0.0  # 1.0 - 2.0 clamped
    assert first["video_end_s"] == 4.0
    assert drafts[1]["video_start_s"] == 6.0 and drafts[1]["video_end_s"] == 10.5
    assert first["title"] == "Upper cabinet: knob missing"
    assert first["item_id"] == item_id_for("wlk_1", "0.0")


def test_video_end_clamped_to_walk():
    drafts = build_drafts("w", build_mentions([_turn(0, 28.0, 29.5, "chip")]), {}, duration_s=30.0)
    assert drafts[0]["video_end_s"] == 30.0


def test_provisional_title():
    assert provisional_title("  scuff marks on the wall by the door. ") == "Scuff marks on the wall by the door"
    long = provisional_title("word " * 30)
    assert len(long) <= 60 and long.endswith("…")
    assert provisional_title("") == "Item from the walk"


@pytest.fixture
def ctx(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'f.sqlite3'}")
    SQLModel.metadata.create_all(engine)

    def factory():
        return Session(engine, expire_on_commit=False)

    with factory() as s:
        p = Project(id="prj_1", name="P")
        s.add(p)
        s.add(Walk(id="wlk_0", project_id="prj_1"))
        s.add(Walk(id="wlk_1", project_id="prj_1"))
        s.add(Item(project_id="prj_1", number=7, origin_walk_id="wlk_0", title="older walk item"))
        s.add(Point(id="pnt_a", walk_id="wlk_1", number=1, t=1.5, frame=15, fingertip_x=1, fingertip_y=2, hold_s=1.0))
        s.add(Point(id="pnt_b", walk_id="wlk_1", number=2, t=8.5, frame=85, fingertip_x=1, fingertip_y=2, hold_s=1.0))
        s.commit()
    return StageContext(walk_id="wlk_1", project_id="prj_1", kind=WalkKind.initial, walk_dir=tmp_path,
                        session_factory=factory)


def test_save_items_numbers_continue_and_rerun_keeps_numbers(ctx):
    mentions = build_mentions(TURNS)
    drafts = build_drafts("wlk_1", mentions, {"0.0": "pnt_a", "2.1": "pnt_b"}, duration_s=30.0)
    save_items(ctx, drafts)
    with ctx.session() as s:
        items = s.exec(select(Item).where(Item.origin_walk_id == "wlk_1").order_by(Item.number)).all()
        assert [i.number for i in items] == [8, 9, 10]  # project-scoped, after the older walk's #7
        assert [i.point_id for i in items] == ["pnt_a", None, "pnt_b"]
        assert all(i.status == ItemStatus.open and i.history for i in items)
        assert items[0].quote == "upper cabinet: knob missing." and items[0].room == "Kitchen"
        numbers = {i.id: i.number for i in items}

    # a verified status survives a re-run
    with ctx.session() as s:
        first = s.get(Item, drafts[0]["item_id"])
        first.status = ItemStatus.needs_review
        first.review_reason = "The frame doesn't show the defect described."
        s.add(first)
        s.commit()
    # re-run without the middle mention: numbers stay, the dropped item is flagged, not deleted
    save_items(ctx, [drafts[0], drafts[2]])
    with ctx.session() as s:
        items = {i.id: i for i in s.exec(select(Item).where(Item.origin_walk_id == "wlk_1")).all()}
        assert {k: i.number for k, i in items.items()} == numbers
        orphan = items[drafts[1]["item_id"]]
        assert orphan.status == ItemStatus.needs_review and orphan.review_reason == REVIEW_ORPHANED
        assert items[drafts[0]["item_id"]].status == ItemStatus.needs_review  # kept, not reset to open
        assert len(s.exec(select(Item)).all()) == 4  # the older walk's item is untouched
