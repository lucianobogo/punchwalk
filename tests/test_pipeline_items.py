"""Phase 2 decisions with mocked clients: write_items (2.1), ground_defects (2.2), verify (2.3),
GROUNDING_MODE (2.4), runner retries / skipped stages / plain errors (2.5)."""

from __future__ import annotations

import numpy as np
import pytest

from punchwalk.clients import calls, cost, llm, sam
from punchwalk.clients.base import ApiError
from punchwalk.clients.cache import CacheMiss
from punchwalk.core.enums import (
    REVIEW_NOT_IN_FRAME,
    Grounding,
    HistoryAction,
    HistoryActor,
    ItemStatus,
    Stage,
)
from punchwalk.core.models import Item
from punchwalk.pipeline import ground, runner, verify, write_items
from punchwalk.pipeline.context import StageInputMissing
from punchwalk.pipeline.items import add_history, is_protected
from punchwalk.pipeline.verify import ItemCheck, review_note
from punchwalk.pipeline.write_items import clean_box, model_view

W, H = 720, 1280


# --------------------------------------------------------------------------- 2.1


def test_model_view_and_box_scaling():
    assert model_view(720, 1280) == (1.0, 720, 1280)
    s, mw, mh = model_view(1080, 1920)
    assert mh == 1600 and mw == 900 and s == pytest.approx(1600 / 1920)
    assert clean_box([90, 160, 180, 320], s, 1080, 1920) == [108.0, 192.0, 216.0, 384.0]
    assert clean_box([180, 320, 90, 160], 1.0, W, H) == [90.0, 160.0, 180.0, 320.0]  # swapped corners
    assert clean_box([-50, -50, 9000, 9000], 1.0, W, H) == [0.0, 0.0, 720.0, 1280.0]  # clamped
    assert clean_box([10, 10, 12, 12], 1.0, W, H) is None  # tiny
    assert clean_box(None, 1.0, W, H) is None and clean_box([1, 2, 3], 1.0, W, H) is None


def test_item_write_schema_is_strict_and_flat():
    schema = llm.strict_schema(write_items.ItemWrite)
    props = schema["properties"]
    assert schema["additionalProperties"] is False and set(schema["required"]) == set(props)
    assert props["defect_box"]["type"] == ["array", "null"]
    assert "carpentry_millwork" in props["trade"]["enum"] and props["severity"]["enum"] == [
        "cosmetic", "functional", "safety"]
    assert "anyOf" not in str(schema)


def test_protected_items():
    item = Item(project_id="p", number=1, origin_walk_id="w", title="t")
    assert not is_protected(item)
    add_history(item, HistoryActor.model, HistoryAction.edited)
    assert not is_protected(item)
    add_history(item, HistoryActor.human, HistoryAction.edited, field="title")
    assert is_protected(item)
    assert is_protected(Item(project_id="p", number=2, origin_walk_id="w", title="t", status=ItemStatus.dispatched))


# --------------------------------------------------------------------------- 2.2


def _rec(box, oid=0, conf=0.9) -> sam.SamRecord:
    x1, y1, x2, y2 = box
    w, h = int(x2 - x1), int(y2 - y1)
    r = sam.SamRecord(object_id=oid, frame_index=0, box=box, confidence=conf, media_size=(W, H))
    r.mask = object()  # stands in for a decoded mask; the raster below is what the code reads
    r._raster = np.ones((h, w), np.uint8)
    return r


def test_pick_instance_nearest_within_radius_and_not_a_wall():
    near, far = _rec((300, 600, 340, 640)), _rec((600, 100, 640, 140), oid=1)
    wall = _rec((0, 0, 720, 1000), oid=2)
    radius = 0.25 * W
    assert ground.pick_instance([far, near, wall], W, H, (350, 650), radius) is near
    assert ground.pick_instance([far], W, H, (350, 650), radius) is None  # too far from the fingertip
    assert ground.pick_instance([wall], W, H, (350, 650), radius) is None  # a whole wall is not a defect
    assert ground.pick_instance([near], W, H, None, radius) is near  # no pointer: the only instance
    assert ground.pick_instance([near, far], W, H, None, radius) is None


def test_ground_one_concept_then_box_then_pointer_only(monkeypatch, tmp_path):
    near = _rec((300, 600, 340, 640))
    box_rec = _rec((310, 610, 330, 630))

    def result(recs):
        return sam.SamResult(media="image", raw_text="", frames={0: recs}, diagnostics=[], media_size=(W, H))

    asked = []
    monkeypatch.setattr(sam, "concept_on_image", lambda img, c: (asked.append(("concept", c)), result([near]))[1])
    monkeypatch.setattr(sam, "boxes_on_image", lambda img, b: (asked.append(("box", b)), result([box_rec]))[1])
    g, rec, _ = ground.ground_one("raw.jpg", W, H, (345, 650), "wall scuff", [300, 600, 340, 640])
    assert g == Grounding.sam_concept and rec is near and asked == [("concept", "wall scuff")]

    monkeypatch.setattr(sam, "concept_on_image", lambda img, c: result([]))
    asked.clear()
    g, rec, _ = ground.ground_one("raw.jpg", W, H, (345, 650), "wall scuff", [300, 600, 340, 640])
    assert g == Grounding.sam_box and rec is box_rec
    assert asked == [("box", [(298, 598, 342, 642)])]  # defect_box grown 10% (2 px per side)

    g, rec, notes = ground.ground_one("raw.jpg", W, H, (345, 650), "wall scuff", [600, 1100, 650, 1150])
    assert g == Grounding.pointed_only and rec is None and "box_skipped" in notes  # box far from the finger
    g, rec, _ = ground.ground_one("raw.jpg", W, H, None, None, None)
    assert g == Grounding.none and rec is None


# --------------------------------------------------------------------------- 2.3


def _check(**kw) -> ItemCheck:
    base = {"consistent": False, "frame_shows_defect": True, "issues": [], "suggested_fix": None, "rationale": "r"}
    return ItemCheck(**{**base, **kw})


def test_review_notes():
    assert review_note(_check(frame_shows_defect=False, issues=["x"])) == REVIEW_NOT_IN_FRAME
    assert review_note(_check(issues=["Action says replace, the inspector said re-secure"])) == \
        "Action says replace, the inspector said re-secure."
    long = review_note(_check(issues=["word " * 60]))
    assert len(long) <= 140 and long.endswith("…")
    assert review_note(_check()) == "The item doesn't match the photo or what was said."


def test_verify_check_one_sends_evidence_and_quote(monkeypatch, tmp_path):
    from punchwalk.core.models import Evidence

    (tmp_path / "e.jpg").write_bytes(b"x")
    monkeypatch.setattr(verify, "data_dir", lambda: tmp_path)
    seen = {}

    def fake(model, messages, images=(), reasoning_effort="", max_tokens=0, stage=""):
        seen.update(model=model, effort=reasoning_effort, images=list(images), text=messages[-1]["content"],
                    max_tokens=max_tokens, stage=stage)
        return _check(consistent=True), calls.CallRecord(provider="meta-fallback", model="m", endpoint="/x")

    monkeypatch.setattr(llm, "structured", fake)
    item = Item(project_id="p", number=1, origin_walk_id="w", title="Knob missing", quote="knob missing",
                trade="carpentry_millwork")
    ev = Evidence(t=1.0, image="e.jpg", raw="e.jpg", crop="e.jpg", width=W, height=H)
    check, provider = verify.check_one(item, ev)
    assert check.consistent and provider == "meta-fallback"
    assert seen["model"] is ItemCheck and seen["effort"] == "medium" and seen["stage"] == "verify"
    assert len(seen["images"]) == 1 and '"knob missing"' in seen["text"] and "Carpentry & millwork" in seen["text"]


# --------------------------------------------------------------------------- 2.5 runner


def test_retryable_errors():
    assert runner.is_retryable(ApiError("baseten", "/x", 503, "down"))
    assert runner.is_retryable(ApiError("meta", "/x", None, "timeout"))
    assert not runner.is_retryable(ApiError("meta", "/x", 400, "bad"))
    assert not runner.is_retryable(CacheMiss("k", "d"))
    assert not runner.is_retryable(cost.CostCapReached("cap"))
    assert not runner.is_retryable(StageInputMissing("x"))


def test_plain_errors():
    assert runner.plain_error(Stage.track_hands, ApiError("meta", "/responses", 502, "bad gateway")) == \
        "Hand tracking failed: the model service is not responding. Try again in a few minutes."
    assert runner.plain_error(Stage.transcribe, ApiError("meta", "/asr", 401, "no")) == \
        "Transcription failed: the model service rejected our key."
    assert runner.plain_error(Stage.write_items, cost.CostCapReached("x")).startswith("Writing the items failed: ")
    assert runner.plain_error(Stage.verify, ValueError("boom")) == "Checking the items failed."


def test_stage_retries_then_succeeds_or_gives_up(monkeypatch, tmp_path):
    from sqlmodel import Session, SQLModel, create_engine

    from punchwalk.core.models import Project, Walk

    engine = create_engine(f"sqlite:///{tmp_path / 'r.sqlite3'}")
    SQLModel.metadata.create_all(engine)

    def factory():
        return Session(engine, expire_on_commit=False)

    with factory() as s:
        s.add(Project(id="prj_r", name="r"))
        s.add(Walk(id="wlk_r", project_id="prj_r"))
        s.commit()
    attempts = {"n": 0}

    def flaky(ctx):
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise ApiError("meta", "/asr", 503, "busy")
        return runner.StageOutcome(counts={"lines": 1})

    ok = runner.StageOutcome(counts={})
    fns = {k: (lambda ctx: ok) for k in runner.STAGE_FUNCTIONS}
    fns["transcribe"] = flaky
    monkeypatch.setattr(runner, "STAGE_FUNCTIONS", fns)
    events = []
    runner.run_walk("wlk_r", session_factory=factory, progress=events.append)
    assert attempts["n"] == 3
    assert sum(1 for e in events if e.message == "Retrying after an error") == 2
    with factory() as s:
        walk = s.get(Walk, "wlk_r")
        assert walk.status == "ready" and walk.stages["transcribe"]["counts"] == {"lines": 1}

    def always(ctx):
        raise ApiError("meta", "/asr", 503, "busy")

    fns["transcribe"] = always
    attempts["n"] = 0
    with pytest.raises(ApiError):
        runner.run_walk("wlk_r", session_factory=factory, force=True)
    with factory() as s:
        walk = s.get(Walk, "wlk_r")
        assert walk.status == "failed" and walk.error.startswith("Transcription failed")
        assert "HTTP 503" in walk.stages["transcribe"]["error"]


def test_unbuilt_stage_is_skipped_not_done(monkeypatch, tmp_path):
    from sqlmodel import Session, SQLModel, create_engine

    from punchwalk.core.enums import WalkKind
    from punchwalk.core.models import Project, Walk

    engine = create_engine(f"sqlite:///{tmp_path / 's.sqlite3'}")
    SQLModel.metadata.create_all(engine)

    def factory():
        return Session(engine, expire_on_commit=False)

    with factory() as s:
        s.add(Project(id="prj_s", name="s"))
        s.add(Walk(id="wlk_s", project_id="prj_s", kind=WalkKind.rewalk))
        s.commit()
    ok = runner.StageOutcome(counts={})
    fns = {k: (lambda ctx: ok) for k in runner.STAGE_FUNCTIONS if k != "resolve_closures"}
    monkeypatch.setattr(runner, "STAGE_FUNCTIONS", fns)  # pretend resolve_closures is not built yet
    runner.run_walk("wlk_s", session_factory=factory)
    with factory() as s:
        stages = s.get(Walk, "wlk_s").stages
        assert stages["resolve_closures"]["status"] == "skipped"
        assert stages["persist"]["status"] == "done"
    ran = []
    fns2 = {k: (lambda ctx, k=k: (ran.append(k), ok)[1]) for k in fns}
    monkeypatch.setattr(runner, "STAGE_FUNCTIONS", fns2)
    runner.run_walk("wlk_s", session_factory=factory)  # resume: the skipped stage (and what follows) runs again
    assert ran == ["persist"]


def test_writer_text_cleaning():
    from punchwalk.pipeline.write_items import clean_description, clean_text

    assert clean_text("upper cabinet left of left of the sink") == "upper cabinet left of the sink"
    assert clean_text("  the the knob  ") == "the knob"
    assert clean_description("Knob missing on the upper cabinet. The knob is not clearly visible in the photos.") \
        == "Knob missing on the upper cabinet."
    assert clean_description("The door frame is cracked near the hinge.") == "The door frame is cracked near the hinge."


def test_skipped_stage_event_and_orphan_note_once(monkeypatch, tmp_path):
    from sqlmodel import Session, SQLModel, create_engine, select

    from punchwalk.core.enums import WalkKind
    from punchwalk.core.models import Project, Walk
    from punchwalk.pipeline.context import StageContext
    from punchwalk.pipeline.fuse import REVIEW_ORPHANED, save_items

    engine = create_engine(f"sqlite:///{tmp_path / 'o.sqlite3'}")
    SQLModel.metadata.create_all(engine)

    def factory():
        return Session(engine, expire_on_commit=False)

    with factory() as s:
        s.add(Project(id="prj_o", name="o"))
        s.add(Walk(id="wlk_o", project_id="prj_o", kind=WalkKind.rewalk))
        s.add(Item(id="itm_o", project_id="prj_o", number=1, origin_walk_id="wlk_o", title="sent",
                   status=ItemStatus.dispatched))
        s.commit()
    ctx = StageContext(walk_id="wlk_o", project_id="prj_o", kind=WalkKind.initial, walk_dir=tmp_path,
                       session_factory=factory)
    save_items(ctx, [])
    save_items(ctx, [])
    with factory() as s:
        item = s.exec(select(Item)).one()
        assert item.status == ItemStatus.dispatched  # protected: not flagged, not deleted
        assert [h["note"] for h in item.history].count(REVIEW_ORPHANED) == 1

    ok = runner.StageOutcome(counts={})
    monkeypatch.setattr(runner, "STAGE_FUNCTIONS",
                        {k: (lambda ctx: ok) for k in runner.STAGE_FUNCTIONS if k != "resolve_closures"})
    events = []
    runner.run_walk("wlk_o", session_factory=factory, progress=events.append)
    assert [e.status for e in events if e.stage == Stage.resolve_closures] == ["running", "skipped"]


def test_failed_stage_message_is_plain_words(monkeypatch, tmp_path):
    from sqlmodel import Session, SQLModel, create_engine

    from punchwalk.core.models import Project, Walk

    engine = create_engine(f"sqlite:///{tmp_path / 'm.sqlite3'}")
    SQLModel.metadata.create_all(engine)

    def factory():
        return Session(engine, expire_on_commit=False)

    with factory() as s:
        s.add(Project(id="prj_m", name="m"))
        s.add(Walk(id="wlk_m", project_id="prj_m"))
        s.commit()

    def miss(ctx):
        raise CacheMiss("c8f0deadbeef", "ASR audio.wav")

    ok = runner.StageOutcome(counts={})
    fns = {k: (lambda ctx: ok) for k in runner.STAGE_FUNCTIONS}
    fns["transcribe"] = miss
    monkeypatch.setattr(runner, "STAGE_FUNCTIONS", fns)
    events = []
    with pytest.raises(CacheMiss):
        runner.run_walk("wlk_m", session_factory=factory, progress=events.append)
    plain = "Transcription failed: a recorded model answer is missing (offline mode)."
    with factory() as s:
        st = s.get(Walk, "wlk_m").stages["transcribe"]
        assert st["message"] == plain and "c8f0deadbeef" in st["error"]  # detail only in `error`
    assert [e.message for e in events if e.status == "failed"] == [plain]
