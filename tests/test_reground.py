"""PLAN 10.5: the re-ground entry point (frame_pick -> v2 ground -> verify re-check) on the synthetic walk."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image
from sqlmodel import select

from punchwalk.config import get_settings, reset_settings
from punchwalk.core.enums import Grounding
from punchwalk.core.models import Item, ModelCall, Project, Walk
from punchwalk.pipeline import frame_pick, ground_v2, reground, verify
from tests.conftest import FIXTURES

SYNTH = FIXTURES / "pipeline" / "synth_walk"
ENV = ("DATA_DIR", "GROUNDING_V2", "REGROUND_ALL", "AUTO_SEND", "GROUNDING_MODE")


@pytest.fixture
def synth_walk(monkeypatch):
    from punchwalk import db
    from punchwalk.pipeline.runner import run_walk

    for k in ENV:  # reground sets these; monkeypatch restores them afterwards
        monkeypatch.setenv(k, {"DATA_DIR": str(get_settings().DATA_DIR)}.get(k, ""))
    monkeypatch.delenv("GROUNDING_MODE")
    monkeypatch.delenv("GROUNDING_V2")
    monkeypatch.delenv("REGROUND_ALL")
    monkeypatch.delenv("AUTO_SEND")
    reset_settings()
    db.reset_engines()
    data = Path(get_settings().DATA_DIR)
    with db.session_factory() as s:
        project = Project(name="Synthetic unit", rooms=["Kitchen", "Hallway", "Bedroom"])
        s.add(project)
        s.commit()
        walk = Walk(project_id=project.id)
        s.add(walk)
        s.commit()
        pid, wid = project.id, walk.id
    shutil.copytree(SYNTH / "media", data / "walks" / wid)
    shutil.copytree(SYNTH / "cache", data / "cache", dirs_exist_ok=True)
    run_walk(wid, session_factory=db.session_factory)
    yield data, pid, wid
    db.reset_engines()


def _fake_models(monkeypatch, calls_seen):
    def fake_ground(item, save_dir=None):
        calls_seen.setdefault("ground", []).append((item.item_id, item.pointer is not None))
        x, y = (item.pointer or (item.width / 2, item.height / 2))
        box = (x - 20, y - 20, x + 20, y + 20)
        svg = f"M{box[0]} {box[1]} L{box[2]} {box[1]} L{box[2]} {box[3]} L{box[0]} {box[3]} Z"
        row = {"locate": {"visible": True}, "result": {"grounding": "sam_box", "source": "box", "defect_svg": svg,
                                                       "defect_box": list(box)}}
        return ground_v2.V2Result(Grounding.sam_box, svg, box, "scuff mark", row)

    monkeypatch.setattr(ground_v2, "ground_item", fake_ground)

    def fake_pick(title, description, quote, images):
        calls_seen.setdefault("pick", []).append(len(images))
        return 0, "none", "baseten"

    monkeypatch.setattr(frame_pick, "ask_pick", fake_pick)
    ok = verify.ItemCheck(consistent=True, frame_shows_defect=True, issues=[], suggested_fix=None, rationale="ok")
    monkeypatch.setattr(verify, "check_one", lambda item, ev: (ok, "baseten"))


def _items(pid):
    from punchwalk import db

    with db.session_factory() as s:
        return {i.number: i for i in s.exec(select(Item).where(Item.project_id == pid)).all()}


def test_reground_cli_runs_picker_v2_and_verify_and_keeps_text(synth_walk, monkeypatch):
    data, pid, wid = synth_walk
    before = _items(pid)
    seen: dict = {}
    _fake_models(monkeypatch, seen)
    assert reground.main(["--data-dir", str(data), "--project", pid]) == 0
    after = _items(pid)
    assert set(after) == set(before)
    for n, a in after.items():
        b = before[n]
        assert (a.title, a.description, a.action, a.trade, a.room, a.severity) == \
            (b.title, b.description, b.action, b.trade, b.room, b.severity)
        # status only moves the way verify moves it: a check flag that now passes goes back to open
        flagged_by_check = b.status == "needs_review" and b.review_reason == (b.verification or {}).get("note")
        assert a.status == b.status or (flagged_by_check and a.status == "open"), (n, b.status, a.status)
        assert a.evidence["grounding"] == "sam_box" and a.evidence["sam_concept"] == "scuff mark"
    stages = data / "walks" / wid / "stages"
    g2 = json.loads((stages / "ground_v2.json").read_text())["items"]
    assert set(g2) == {i.id for i in after.values()}
    picks = json.loads((stages / "frame_pick.json").read_text())["items"]
    unpointed = [i for i in before.values() if not i.evidence.get("pointer")]
    assert unpointed and {i.id for i in unpointed} <= set(picks)  # the picker ran for the voice-only item(s)
    assert all(1 <= n <= 8 for n in seen["pick"])
    assert any(p for _, p in seen["ground"])  # pointed items keep their fingertip


def test_reground_ignore_pointing_treats_every_item_as_unpointed(synth_walk, monkeypatch):
    data, pid, wid = synth_walk
    seen: dict = {}
    _fake_models(monkeypatch, seen)
    reground.reground_project(data, pid, ignore_pointing=True)
    assert seen["ground"] and not any(p for _, p in seen["ground"])
    picks = json.loads((data / "walks" / wid / "stages" / "frame_pick.json").read_text())["items"]
    assert set(picks) == {i.id for i in _items(pid).values()}  # the picker ran for every item
    assert all(i.evidence.get("pointer") is None for i in _items(pid).values())


def test_reground_records_model_calls(synth_walk, monkeypatch):
    from punchwalk import db
    from punchwalk.clients import calls

    data, pid, wid = synth_walk
    seen: dict = {}
    _fake_models(monkeypatch, seen)
    real_pick = frame_pick.ask_pick

    def pick_with_call(*a, **k):
        calls.emit(calls.CallRecord(provider="baseten", model="meta-models/muse-spark-open", endpoint="/chat/completions",
                                    stage="pick_evidence", cached=True))
        return real_pick(*a, **k)

    monkeypatch.setattr(frame_pick, "ask_pick", pick_with_call)
    with db.session_factory() as s:
        n0 = len(s.exec(select(ModelCall).where(ModelCall.walk_id == wid)).all())
    reground.reground_project(data, pid)
    with db.session_factory() as s:
        n1 = len(s.exec(select(ModelCall).where(ModelCall.walk_id == wid)).all())
    assert n1 > n0


def test_dot_position_and_after_rerender(tmp_path):
    from punchwalk.render.evidence import render_evidence

    raw = tmp_path / "after_raw.jpg"
    Image.fromarray((np.random.default_rng(1).random((600, 450, 3)) * 60 + 100).astype(np.uint8)).save(raw, quality=95)
    render_evidence(raw, tmp_path / "after_frame.jpg", pointer=(300.0, 200.0))
    x, y = reground.dot_position(tmp_path / "after_frame.jpg", raw)
    assert abs(x - 300) < 3 and abs(y - 200) < 3
    assert reground.dot_position(raw, raw) is None
    crop = tmp_path / "crop.jpg"
    Image.new("RGB", (400, 300), (255, 0, 0)).save(crop)
    item = Item(project_id="p", number=1, origin_walk_id="w", title="t",
                evidence={"t": 1.0, "image": "crop.jpg", "raw": "crop.jpg", "crop": "crop.jpg", "width": 450,
                          "height": 600, "crop_box": {"x1": 0, "y1": 0, "x2": 200, "y2": 150}},
                closure={"rewalk_id": "w2", "after_image": "after.jpg", "after_frame": "after_frame.jpg",
                         "before_after": "ba.jpg"})
    out = reground.rerender_after(tmp_path, item)
    assert out["crop_box"][2] - out["crop_box"][0] == 200  # cut at the new card crop's size, around the dot
    x1, y1, x2, y2 = out["crop_box"]
    assert x1 <= 300 <= x2 and y1 <= 200 <= y2
    with Image.open(tmp_path / "ba.jpg") as im:
        assert im.getpixel((100, 100))[0] > 200  # left panel: the new before crop
    assert cv2.imread(str(tmp_path / "after.jpg")).shape[:2] == (150, 200)


def test_walks_without_items_of_their_own_are_skipped(synth_walk, monkeypatch):
    from punchwalk import db

    data, pid, wid = synth_walk
    with db.session_factory() as s:
        s.add(Walk(id="wlk_empty_rewalk", project_id=pid, kind="rewalk", rewalk_of=wid))
        s.commit()
    _fake_models(monkeypatch, {})
    rep = reground.reground_project(data, pid)
    assert rep["walks"]["wlk_empty_rewalk"] == {"skipped": "no items from this walk"}
    assert "ground" in rep["walks"][wid]


def test_card_crop_centres_on_the_located_region_when_there_is_no_outline(tmp_path):
    from punchwalk.core.models import Evidence
    from punchwalk.pipeline import ground

    raw = tmp_path / "raw.jpg"
    Image.new("RGB", (1080, 1436), (120, 120, 120)).save(raw)
    ev = Evidence(t=1.0, image="i.jpg", raw="raw.jpg", crop="c.jpg", width=1080, height=1436)
    focus = (580.0, 940.0, 670.0, 1035.0)
    x1, y1, x2, y2 = ground.card_crop_box(ev, raw, (523.0, 1288.0), None, focus)
    assert x1 <= 580 and x2 >= 670 and y1 <= 940 and y2 >= 1035  # the located region is in the card
    assert abs((y1 + y2) / 2 - 987.5) < 2 and not (y1 <= 1288 <= y2)  # centred on it, not on the (off) dot
    assert ground.card_crop_box(ev, raw, (523.0, 1288.0), None, None) == \
        ground.crop_box_for(1080, 1436, pointer=(523.0, 1288.0), defect_box=None)  # fallback: the fingertip
    outlined = ground.card_crop_box(ev, raw, None, [100.0, 100.0, 200.0, 200.0], focus)
    assert outlined == ground.crop_box_for(1080, 1436, defect_box=[100.0, 100.0, 200.0, 200.0])  # outline wins


def test_ground_stage_line_names_the_models_and_v1_is_unchanged():
    from punchwalk.core.enums import Grounding as G
    from punchwalk.pipeline import ground

    c = ground.ground_counts([G.sam_box] * 4 + [G.spark_box] * 2 + [G.sam_concept] + [G.pointed_only] * 4 + [G.none])
    assert c == {"outlined": 7, "total": 12, "concept": 1, "box": 4, "spark_box": 2, "pointed_only": 4, "none": 1}
    assert ground.ground_message(False, c) == "SAM 3.1 outlined 7 of 12 defects"
    v2 = ground.ground_message(True, c, 3)
    assert v2 == ("7 of 12 outlined: Muse Spark Open located each defect, SAM 3.1 drew 5 outlines and Muse Spark "
                  "Open 2 boxes, each checked by Muse Spark Open; a clearer photo picked for 3")


def test_refresh_display_recrops_picked_items_and_rewrites_the_stage_line(synth_walk, monkeypatch):
    from punchwalk import db
    from punchwalk.core.models import Evidence

    data, pid, wid = synth_walk
    _fake_models(monkeypatch, {})
    reground.reground_project(data, pid)
    with db.session_factory() as s:
        walk = s.get(Walk, wid)
        assert walk.stages["ground_defects"]["message"].startswith("3 of 3 outlined: Muse Spark Open located")
    items = _items(pid)
    target = next(i for i in items.values() if not i.evidence.get("pointer"))
    with db.session_factory() as s:  # this item's photo came from the picker and ended without an outline
        row = s.get(Item, target.id)
        row.evidence = {**row.evidence, "grounding": "none", "defect_svg": None, "defect_box": None}
        s.add(row)
        s.commit()
    stages = data / "walks" / wid / "stages"
    frame_pick.save_log(data / "walks" / wid, {target.id: {"outcome": "new frame", "t": 1.0}}, merge=False)
    ev = Evidence.model_validate(target.evidence)
    monkeypatch.setattr(ground_v2, "crop_focus", lambda item, row: ((40.0, 60.0, 120.0, 140.0), "locate"))
    before = {n: i.evidence for n, i in items.items()}
    rep = reground.refresh_display(data, pid)
    after = _items(pid)
    changed = [n for n in after if after[n].evidence != before[n] and n != target.number]
    assert not changed  # only the picked, unoutlined item is re-cropped
    cb = after[target.number].evidence["crop_box"]
    assert cb["x1"] <= 40 and cb["x2"] >= 120 and cb != ev.crop_box.model_dump()
    assert rep["walks"][wid]["message"].startswith("2 of 3 outlined") and "a clearer photo picked for 1" in \
        rep["walks"][wid]["message"]
    with db.session_factory() as s:
        st = s.get(Walk, wid).stages["ground_defects"]
    assert st["counts"]["outlined"] == 2 and st["message"] == rep["walks"][wid]["message"]
    assert json.loads((stages / "ground_v2.json").read_text())["items"][target.id]["crop_focus_from"] == "locate"
