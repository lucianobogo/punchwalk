"""End-to-end runner test on a recorded synthetic walk (fixtures/pipeline/synth_walk/).

The walk: three 7 s portrait parts (scripts/synth_clip.py). In parts a and b a drawn hand slides in
from the right edge, points at a scuff for ~2 s and withdraws while the narration describes a
defect; part c has narration but no hand (a voice-only mention). Every model call of the full
pipeline (Files, SAM 3.1 video + image, Muse Voice Transcribe, Muse Spark Open classify / write /
verify) was recorded live with `scripts/record.py --full --rooms Kitchen Hallway Bedroom` into the
fixture's cache/, so the whole run replays offline in readonly mode.

The narration of part b ("switch plate is crooked") doesn't match what its photo shows (a scuff on
a plain wall), so Spark Open's check flags it: the natural contradictory case.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from punchwalk.config import get_settings, reset_settings
from punchwalk.core.enums import (
    REVIEW_NOT_IN_FRAME,
    Grounding,
    HistoryAction,
    HistoryActor,
    ItemStatus,
    Severity,
    Stage,
    StageStatus,
    Trade,
    TurnKind,
    WalkStatus,
)
from punchwalk.core.models import Evidence, Item, ModelCall, Point, Project, Turn, Walk
from punchwalk.pipeline.items import add_history
from punchwalk.pipeline.runner import StageEvent, run_walk, stage_order
from tests.conftest import FIXTURES

SYNTH = FIXTURES / "pipeline" / "synth_walk"

if shutil.which("ffmpeg") is None:
    pytest.skip("ffmpeg not installed", allow_module_level=True)


def _scuff_center(path: Path) -> tuple[float, float]:
    """Centroid of the synthetic scuff (dark gray #6F6457) in a frame."""
    img = cv2.imread(str(path)).astype(int)
    b, g, r = img[..., 0], img[..., 1], img[..., 2]
    ys, xs = np.nonzero((abs(r - 0x6F) < 25) & (abs(g - 0x64) < 25) & (abs(b - 0x57) < 25))
    assert len(xs) > 20, "scuff not visible in the evidence frame"
    return float(xs.mean()), float(ys.mean())


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.sqlite3'}")
    SQLModel.metadata.create_all(engine)

    def factory() -> Session:
        return Session(engine, expire_on_commit=False)

    return factory


def make_walk(factory, rooms=("Kitchen", "Hallway", "Bedroom")) -> str:
    data = Path(get_settings().DATA_DIR)
    with factory() as s:
        project = Project(name="Synthetic unit", rooms=list(rooms))
        s.add(project)
        s.commit()
        walk = Walk(project_id=project.id)
        s.add(walk)
        s.commit()
        walk_id = walk.id
    wdir = data / "walks" / walk_id
    shutil.copytree(SYNTH / "media", wdir)  # prepared media, no clips: ingest adopts it
    shutil.copytree(SYNTH / "cache", data / "cache", dirs_exist_ok=True)
    return walk_id


def test_stage_order_classifies_before_pointing():
    order = [s.value for s in stage_order("initial")]
    assert order.index("classify_turns") < order.index("find_points")
    assert order[:2] == ["ingest", "transcribe"] and order[-1] == "persist"


RED = (0xD9, 0x3B, 0x30)
BLUE = (0x2F, 0x86, 0xE0)


def _has_color(path: Path, rgb, tol: int = 40, at=None, radius: int = 30) -> bool:
    img = cv2.imread(str(path))[..., ::-1].astype(int)
    if at is not None:
        x, y = int(at[0]), int(at[1])
        img = img[max(0, y - radius): y + radius, max(0, x - radius): x + radius]
    diff = np.abs(img - np.array(rgb)).max(axis=-1)
    return int((diff < tol).sum()) > 15


def test_runner_end_to_end_and_readonly_replay(db):
    walk_id = make_walk(db)
    events: list[StageEvent] = []
    run_walk(walk_id, session_factory=db, progress=events.append)

    with db() as s:
        walk = s.get(Walk, walk_id)
        assert walk.status == WalkStatus.ready and walk.error is None
        assert walk.duration_s == pytest.approx(21.0)
        assert (walk.width, walk.height) == (720, 1280) and walk.orientation == "portrait"
        assert walk.media["poster"] == f"walks/{walk_id}/poster.jpg"
        assert [c["filename"] for c in walk.clips] == ["walk1_a.mp4", "walk1_b.mp4", "walk1_c.mp4"]
        for stage in stage_order(walk.kind):
            st = walk.stages[stage.value]
            assert st["status"] == StageStatus.done, stage
            assert st["duration_ms"] is not None and st["finished_at"]

        turns = s.exec(select(Turn).where(Turn.walk_id == walk_id).order_by(Turn.idx)).all()
        assert [t.kind for t in turns] == [TurnKind.defect] * 3
        assert [t.room for t in turns] == ["Kitchen", "Hallway", "Bedroom"]

        points = s.exec(select(Point).where(Point.walk_id == walk_id).order_by(Point.number)).all()
        assert [p.number for p in points] == [1, 2]
        assert [p.sam_object_ids for p in points] == [["a:0"], ["b:0"]]
        assert points[0].t < 7.0 <= points[1].t < 14.0  # part b's frames are offset by its start
        assert all(p.hold_s >= 0.4 for p in points)
        assert [p.turn_id for p in points] == [turns[0].id, turns[1].id]

        items = s.exec(select(Item).where(Item.origin_walk_id == walk_id).order_by(Item.number)).all()
        assert [i.number for i in items] == [1, 2, 3]
        assert [i.point_id for i in items] == [points[0].id, points[1].id, None]
        assert items[2].video_start_s == pytest.approx(max(0.0, turns[2].start_ms / 1000 - 2.0))
        assert items[2].video_end_s == pytest.approx(turns[2].end_ms / 1000 + 1.0)

        # 2.1: every item written by Spark Open
        for item in items:
            assert item.title and len(item.title) <= 60 and item.action and len(item.action) <= 80
            assert item.description and item.trade in set(Trade) and item.severity in set(Severity)
            assert item.room in ("Kitchen", "Hallway", "Bedroom") and item.confidence is not None
        assert items[0].trade != Trade.general and items[1].trade == Trade.electrical

        data = Path(get_settings().DATA_DIR)
        evs = [Evidence.model_validate(i.evidence) for i in items]
        for item, ev in zip(items, evs, strict=True):
            assert (ev.width, ev.height) == (720, 1280)
            assert ev.grounding in {Grounding.sam_concept, Grounding.sam_box, Grounding.pointed_only, Grounding.none}
            assert ev.image.endswith("evidence.jpg") and ev.crop.endswith("crop.jpg")
            for rel in (ev.image, ev.raw, ev.crop):
                assert (data / rel).is_file()
            for name in ("context_a.jpg", "context_b.jpg"):
                assert (data / "walks" / walk_id / "items" / item.id / name).is_file()
        # 2.2: pointed items: defect outlined (red) in the evidence image, blue dot at the pointer,
        # hand outlined in the pointing frame; the raw frame has neither
        for ev in evs[:2]:
            assert ev.pointer and ev.pointing_frame.endswith("pointing.jpg")
            assert ev.grounding in (Grounding.sam_concept, Grounding.sam_box) and ev.defect_svg and ev.defect_box
            assert _has_color(data / ev.image, RED) and _has_color(data / ev.image, BLUE, at=(ev.pointer.x, ev.pointer.y))
            assert not _has_color(data / ev.raw, RED) and not _has_color(data / ev.raw, BLUE)
            assert _has_color(data / ev.pointing_frame, BLUE)
            sx, sy = _scuff_center(data / ev.raw)  # the finger points at the scuff
            assert abs(ev.pointer.x - sx) < 35 and abs(ev.pointer.y - sy) < 35, (ev.pointer, (sx, sy))
            b = ev.defect_box
            assert b.x1 - 30 <= sx <= b.x2 + 30 and b.y1 - 30 <= sy <= b.y2 + 30  # outline on the scuff
        voice = evs[2]
        assert voice.pointer is None and voice.pointing_frame is None and voice.grounding == Grounding.none
        assert voice.t >= 14.0  # photo from its own clip part (c), during its turn
        assert not _has_color(data / voice.image, BLUE)

        # 2.3: checked against the photo; the contradictory one needs a look, nothing is dropped
        assert all(i.verification for i in items)
        assert items[0].status == ItemStatus.open and items[0].verification["consistent"] is True
        assert items[1].status == ItemStatus.needs_review and items[1].review_reason == REVIEW_NOT_IN_FRAME
        assert items[1].verification["consistent"] is False and items[1].verification["provider"] == "baseten"

        mc = s.exec(select(ModelCall).where(ModelCall.walk_id == walk_id)).all()
        stages = {m.stage for m in mc}
        assert {Stage.transcribe, Stage.track_hands, Stage.classify_turns, Stage.write_items,
                Stage.ground_defects, Stage.verify} <= stages
        assert all(m.cached for m in mc)  # readonly replay
        assert any(m.frames == 70 for m in mc if m.stage == Stage.track_hands and m.endpoint == "/responses")

    done = [e for e in events if e.status == "done"]
    assert [e.stage for e in done] == stage_order("initial")
    assert next(e for e in done if e.stage == Stage.find_points).counts["points"] == 2
    running = [e.counts for e in events if e.status == "running" and e.stage == Stage.write_items and e.counts]
    assert running[0] == {"written": 0, "total": 3} and running[-1] == {"written": 3, "total": 3}

    # ---- forced re-run: same results, all from the cache
    with db() as s:
        before = {i.id: (i.number, i.point_id, i.evidence["t"], i.title, i.status) for i in s.exec(select(Item)).all()}
        n_calls = len(s.exec(select(ModelCall)).all())
    run_walk(walk_id, session_factory=db, force=True)
    with db() as s:
        after = {i.id: (i.number, i.point_id, i.evidence["t"], i.title, i.status) for i in s.exec(select(Item)).all()}
        assert after == before
        new = s.exec(select(ModelCall)).all()[n_calls:]
        assert new and all(m.cached for m in new)


def test_forced_rerun_keeps_human_edits_and_dispatch_state(db):
    walk_id = make_walk(db)
    run_walk(walk_id, session_factory=db)
    with db() as s:
        a, b, c = s.exec(select(Item).where(Item.origin_walk_id == walk_id).order_by(Item.number)).all()
        a.title = "Knob missing, kitchen upper cabinet (edited)"
        add_history(a, HistoryActor.human, HistoryAction.edited, field="title")
        b.status = ItemStatus.dispatched
        s.add(a)
        s.add(b)
        s.commit()
        a_hist, b_hist = len(a.history), len(b.history)
        data = Path(get_settings().DATA_DIR)
        before = {i.id: (dict(i.evidence), (data / i.evidence["image"]).read_bytes()) for i in (a, b)}
    run_walk(walk_id, session_factory=db, force=True)
    with db() as s:
        a2, b2, c2 = s.exec(select(Item).where(Item.origin_walk_id == walk_id).order_by(Item.number)).all()
        assert a2.title == "Knob missing, kitchen upper cabinet (edited)" and len(a2.history) == a_hist
        assert b2.status == ItemStatus.dispatched and len(b2.history) == b_hist
        assert c2.status == ItemStatus.needs_review  # machine-managed item re-checked the same way
        for item in (a2, b2):  # protected items keep their outline, grounding and rendered evidence
            ev0, img0 = before[item.id]
            assert item.evidence["grounding"] == ev0["grounding"] and item.evidence["defect_svg"] == ev0["defect_svg"]
            assert item.evidence["defect_svg"] and (data / item.evidence["image"]).read_bytes() == img0


def test_resume_skips_done_stages_and_failure_is_readable(db):
    walk_id = make_walk(db)
    run_walk(walk_id, session_factory=db, stop_after=Stage.transcribe)
    with db() as s:
        walk = s.get(Walk, walk_id)
        assert walk.status == WalkStatus.processing
        assert walk.stages["transcribe"]["status"] == "done" and "track_hands" not in walk.stages
        first_ingest = walk.stages["ingest"]["finished_at"]

    events: list[StageEvent] = []
    run_walk(walk_id, session_factory=db, progress=events.append)
    assert [e.message for e in events if e.stage == Stage.ingest] == ["already done"]
    with db() as s:
        assert s.get(Walk, walk_id).stages["ingest"]["finished_at"] == first_ingest

    # a broken input marks the stage failed: plain words on the walk, the detail on the stage
    wdir = Path(get_settings().DATA_DIR) / "walks" / walk_id
    (wdir / "stages" / "track_hands.json").unlink()
    with db() as s:
        walk = s.get(Walk, walk_id)
        stages = dict(walk.stages)
        stages.pop("find_points")
        walk.stages = stages
        s.add(walk)
        s.commit()
    with pytest.raises(RuntimeError, match="track_hands.json is missing"):
        run_walk(walk_id, session_factory=db)
    with db() as s:
        walk = s.get(Walk, walk_id)
        assert walk.status == WalkStatus.failed
        assert walk.stages["find_points"]["status"] == "failed"
        assert "track_hands.json is missing" in walk.stages["find_points"]["error"]
        assert walk.error == "Couldn't find where you pointed."


def test_voice_only_mode_ignores_pointing(db, monkeypatch):
    monkeypatch.setenv("GROUNDING_MODE", "voice_only")
    reset_settings()
    walk_id = make_walk(db)
    run_walk(walk_id, session_factory=db, stop_after=Stage.pick_evidence)
    with db() as s:
        items = s.exec(select(Item).where(Item.origin_walk_id == walk_id).order_by(Item.number)).all()
        evs = [Evidence.model_validate(i.evidence) for i in items]
        assert len(items) == 3 and items[0].point_id is not None  # the point is still recorded...
        assert all(e.pointer is None and e.pointing_frame is None and e.grounding == Grounding.none for e in evs)
        turns = s.exec(select(Turn).where(Turn.walk_id == walk_id).order_by(Turn.idx)).all()
        for e, t in zip(evs, turns, strict=True):  # ...but the photo is the sharpest frame of the turn window
            assert t.start_ms / 1000 - 0.11 <= e.t <= t.end_ms / 1000 + 0.11


def _silent_doc(turns: list[dict] | None = None) -> dict:
    return {"version": 1, "model": "muse-voice-transcribe-1.0", "mode": "ENDPOINTING", "keywords": [],
            "transcript": " ".join(t["text"] for t in turns or []), "audio_duration_ms": 21000, "turns": turns or []}


def test_a_silent_walk_skips_hand_tracking_and_says_why(db, monkeypatch):
    """PLAN 10.9: nothing was said, so nothing can be pointed at: no SAM call (no upload either), 0 points,
    0 items, the walk is ready, and the processing checklist tells the person what to do."""
    from punchwalk.api.progress import stage_rows
    from punchwalk.clients import sam
    from punchwalk.pipeline import track, transcribe

    walk_id = make_walk(db)
    monkeypatch.setattr(transcribe, "transcribe_audio", lambda wav, keywords: _silent_doc())

    def no_sam(*a, **k):
        raise AssertionError("SAM must not be called for a silent walk")

    monkeypatch.setattr(sam, "concept_on_video", no_sam)
    run_walk(walk_id, session_factory=db)
    with db() as s:
        walk = s.get(Walk, walk_id)
        assert walk.status == WalkStatus.ready and walk.error is None
        assert not s.exec(select(Point).where(Point.walk_id == walk_id)).all()
        assert not s.exec(select(Item).where(Item.origin_walk_id == walk_id)).all()
        models = [c.model for c in s.exec(select(ModelCall).where(ModelCall.walk_id == walk_id)).all()]
    assert "sam-3.1" not in models and "meta-files" not in models
    rows = {r.stage: r for r in stage_rows(walk)}  # what GET /api/walks/{id}/progress returns as `stages`
    assert rows[Stage.track_hands].status == StageStatus.done
    assert rows[Stage.track_hands].detail == rows[Stage.track_hands].message == track.NO_NARRATION
    assert track.NO_NARRATION == "We didn't hear any narration. Say each defect out loud and upload again."
    from punchwalk.pipeline.context import read_stage_output

    doc = read_stage_output(Path(get_settings().DATA_DIR) / "walks" / walk_id, Stage.track_hands)
    assert doc["parts"] == [] and doc["records"] == [] and doc["sam"]["width"] == 720


def test_small_talk_without_a_defect_also_skips_hand_tracking(db, monkeypatch):
    from punchwalk.clients import sam
    from punchwalk.pipeline import classify, track, transcribe

    walk_id = make_walk(db)
    turns = [{"idx": 0, "start_ms": 500, "end_ms": 2500, "text": "Okay, starting in the kitchen."}]
    monkeypatch.setattr(transcribe, "transcribe_audio", lambda wav, keywords: _silent_doc(turns))
    monkeypatch.setattr(classify, "label_turns", lambda t, rooms, kind: (
        [{"idx": 0, "kind": "location", "room": "Kitchen", "defects": [], "continues_previous": False,
          "status_target": None, "claimed": None}], {"provider": "test"}))
    monkeypatch.setattr(sam, "concept_on_video", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no SAM")))
    run_walk(walk_id, session_factory=db)
    with db() as s:
        walk = s.get(Walk, walk_id)
        assert walk.status == WalkStatus.ready
        assert walk.stages["track_hands"]["message"] == track.NO_DEFECTS
