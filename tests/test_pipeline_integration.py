"""End-to-end runner test on a recorded synthetic walk (fixtures/pipeline/synth_walk/).

The walk: three 7 s portrait parts (scripts/synth_clip.py). In parts a and b a drawn hand slides in
from the right edge, points at a scuff for ~2 s and withdraws while the narration describes a
defect; part c has narration but no hand (a voice-only mention). Its SAM 3.1 hand tracks, Files
uploads and Muse Voice Transcribe response were recorded live with scripts/record.py and live in
the fixture's cache/, so they replay offline.

Muse Spark Open (classify_turns) is answered by an offline mock on the first run (readwrite into
the tmp cache); the second run re-runs every stage with force=True in readonly mode, so every
model call replays from the cache.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import cv2
import httpx2
import numpy as np
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from punchwalk.clients import llm
from punchwalk.config import get_settings, reset_settings
from punchwalk.core.enums import Grounding, ItemStatus, Stage, StageStatus, TurnKind, WalkStatus
from punchwalk.core.models import Evidence, Item, ModelCall, Point, Project, Turn, Walk
from punchwalk.pipeline.runner import StageEvent, run_walk, stage_order
from tests.conftest import FIXTURES

SYNTH = FIXTURES / "pipeline" / "synth_walk"

if shutil.which("ffmpeg") is None:
    pytest.skip("ffmpeg not installed", allow_module_level=True)


def _fake_classify(request: httpx2.Request) -> httpx2.Response:
    """Stand-in for Spark Open: label each turn from its words."""
    body = json.loads(request.content)
    user = next(m for m in reversed(body["messages"]) if m["role"] == "user")["content"]
    turns = json.loads(user.split("Turns:\n", 1)[1])
    labels = []
    for t in turns:
        text = t["text"]
        room, _, rest = text.partition(",")
        defect = re.search(r"missing|crooked|scuff", text)
        labels.append({
            "idx": t["idx"], "kind": "defect" if defect else "other", "room": room.strip().title(),
            "continues_previous": False, "defects": [rest.strip(" .")] if defect else [],
            "status_target": None, "claimed": None,
        })
    content = json.dumps({"turns": labels, "rationale": "keyword stand-in"})
    return httpx2.Response(200, json={
        "id": "x", "object": "chat.completion", "created": 1, "model": body["model"],
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 900, "completion_tokens": 200, "total_tokens": 1100},
    })


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


@pytest.fixture
def spark_mock(monkeypatch):
    calls: list[httpx2.Request] = []

    def handle(request):
        calls.append(request)
        return _fake_classify(request)

    monkeypatch.setattr(llm, "http_client_factory", lambda: httpx2.Client(transport=httpx2.MockTransport(handle)))
    return calls


def test_stage_order_classifies_before_pointing():
    order = [s.value for s in stage_order("initial")]
    assert order.index("classify_turns") < order.index("find_points")
    assert order[:2] == ["ingest", "transcribe"] and order[-1] == "persist"


def test_runner_end_to_end_and_readonly_replay(db, spark_mock, monkeypatch):
    monkeypatch.setenv("PUNCHWALK_CACHE", "readwrite")
    reset_settings()
    walk_id = make_walk(db)
    events: list[StageEvent] = []
    run_walk(walk_id, session_factory=db, progress=events.append)
    assert len(spark_mock) == 1  # one batched classify call

    with db() as s:
        walk = s.get(Walk, walk_id)
        assert walk.status == WalkStatus.ready
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
        assert all(i.status == ItemStatus.open for i in items)
        assert items[2].video_start_s == pytest.approx(max(0.0, turns[2].start_ms / 1000 - 2.0))
        assert items[2].video_end_s == pytest.approx(turns[2].end_ms / 1000 + 1.0)
        assert items[0].quote and items[0].title[0].isupper()

        data = Path(get_settings().DATA_DIR)
        for item in items:
            ev = Evidence.model_validate(item.evidence)
            assert (ev.width, ev.height) == (720, 1280)
            for rel in (ev.raw, ev.crop):
                assert (data / rel).is_file()
            for name in ("context_a.jpg", "context_b.jpg"):
                assert (data / "walks" / walk_id / "items" / item.id / name).is_file()
        pointed = [Evidence.model_validate(i.evidence) for i in items[:2]]
        assert all(e.grounding == Grounding.pointed_only and e.pointer and e.pointing_frame for e in pointed)
        voice = Evidence.model_validate(items[2].evidence)
        assert voice.grounding == Grounding.none and voice.pointer is None and voice.pointing_frame is None
        # the finger points at the scuff: in the (camera-motion compensated) evidence frame the pointer
        # lands next to the scuff's dark pixels, and the hand is out of the way
        for e in pointed:
            sx, sy = _scuff_center(data / e.raw)
            assert abs(e.pointer.x - sx) < 35 and abs(e.pointer.y - sy) < 35, (e.pointer, (sx, sy))

        mc = s.exec(select(ModelCall).where(ModelCall.walk_id == walk_id)).all()
        stages = {m.stage for m in mc}
        assert {Stage.transcribe, Stage.track_hands, Stage.classify_turns} <= stages
        assert any(m.provider == "baseten" and not m.cached for m in mc)  # the mocked live classify call

    done = [e for e in events if e.status == "done"]
    assert [e.stage for e in done] == stage_order("initial")
    assert next(e for e in done if e.stage == Stage.find_points).counts["points"] == 2

    # ---- replay: every stage again, readonly, all calls from the cache, same results
    monkeypatch.setenv("PUNCHWALK_CACHE", "readonly")
    reset_settings()
    with db() as s:
        before = {i.id: (i.number, i.point_id, i.evidence["t"]) for i in s.exec(select(Item)).all()}
        n_calls = len(s.exec(select(ModelCall)).all())
    run_walk(walk_id, session_factory=db, force=True)
    assert len(spark_mock) == 1  # no new model request
    with db() as s:
        after = {i.id: (i.number, i.point_id, i.evidence["t"]) for i in s.exec(select(Item)).all()}
        assert after == before
        new = s.exec(select(ModelCall)).all()[n_calls:]
        assert new and all(m.cached for m in new)


def test_resume_skips_done_stages_and_failure_is_readable(db, spark_mock, monkeypatch):
    monkeypatch.setenv("PUNCHWALK_CACHE", "readwrite")
    reset_settings()
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

    # a broken input marks the stage failed with a readable error and re-raises
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
        assert "find_points failed" in walk.error and "track_hands" in walk.error
