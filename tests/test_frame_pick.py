"""PLAN 10.4: evidence frame picker (windows, filters, pick/none, adopt + pin, description-only rewrite)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from punchwalk.config import reset_settings
from punchwalk.core.enums import REVIEW_NOT_IN_FRAME, ItemStatus, Stage, WalkKind
from punchwalk.core.models import Evidence, Item
from punchwalk.pipeline import frame_pick as fp
from punchwalk.pipeline import params as P
from punchwalk.pipeline import verify

# --------------------------------------------------------------------------- windows


def _draft(item_id, key, text, quote="", start=10.0, end=16.0):
    return {"item_id": item_id, "mention_key": key, "text": text, "quote": quote, "quote_start_s": start,
            "quote_end_s": end}


def test_single_item_window_is_the_turn_plus_margins():
    w = fp.turn_windows([_draft("a", "3.0", "wall nick", "there's a nick on the wall")])
    assert w["a"] == (10.0 - P.V4_WINDOW_BEFORE_S, 16.0 + P.V4_WINDOW_AFTER_S)
    assert fp.item_window(0.5, 2.0) == (0.0, 5.0)  # never before 0


def test_multi_item_turn_is_split_where_each_phrase_falls():
    quote = "the faucet is leaking here and then over on the left the cabinet door hinge is loose"
    drafts = [_draft("a", "5.0", "faucet leaking", quote, 20.0, 30.0),
              _draft("b", "5.1", "cabinet door hinge loose", quote, 20.0, 30.0)]
    w = fp.turn_windows(drafts)
    fb = fp.phrase_fraction(quote, "cabinet door hinge loose")
    assert 0.5 < fb < 0.9
    assert w["a"] == pytest.approx((18.5, 20.0 + fb * 10 + 3.0))
    assert w["b"] == pytest.approx((20.0 + fb * 10 - 1.5, 33.0))
    assert w["a"][1] < 33.0 and w["b"][0] > 18.5  # each window leans toward its own phrase


def test_multi_item_turn_falls_back_to_an_even_split():
    drafts = [_draft("a", "7.0", "zzz", "no words in common", 0.0, 10.0),
              _draft("b", "7.1", "qqq", "no words in common", 0.0, 10.0)]
    w = fp.turn_windows(drafts)
    assert w["a"] == (0.0, 8.0) and w["b"] == (3.5, 13.0)


# --------------------------------------------------------------------------- filters and candidates


def test_blurry_and_hand_frames_are_rejected():
    sharp = {1: 200.0, 2: 50.0, 3: 10.0, 4: 180.0}
    hand = {4: 0.2}
    infos = {i.frame: i for i in fp.filter_frames(sharp, hand)}
    assert infos[1].rejected is None
    assert infos[2].rejected == "blurry"  # under 35% of the sharpest
    assert infos[3].rejected == "blurry"
    assert infos[4].rejected == "hand in the way"


def test_candidates_spread_over_time_and_include_the_current_frame():
    infos = [fp.FrameInfo(f, 100.0 + (f % 7), 0.0) for f in range(0, 100)]
    chosen = fp.select_candidates(infos, current=55)
    assert len(chosen) <= P.V4_MAX_CANDIDATES and 55 in chosen
    assert chosen[0] < 15 and chosen[-1] > 85  # spread over the window
    infos[55].rejected = "blurry"
    assert 55 in fp.select_candidates(infos, current=55)  # the current frame is always offered
    assert fp.select_candidates([fp.FrameInfo(1, 5.0, 0.0, "blurry")], current=None) == []


# --------------------------------------------------------------------------- the picker


def _picker(monkeypatch, tmp_path, pick: int):
    meta = SimpleNamespace(clips=[SimpleNamespace(sam_frame_offset=0, sam_frame_count=300)],
                           sam=SimpleNamespace(frame_count=300, path="sam.mp4"),
                           display=SimpleNamespace(path="display.mp4"))
    track = {"records": [], "sam": {"width": 64, "height": 48, "fps": 10}}
    pk = fp.Picker(tmp_path, meta, track, [_draft("itm", "1.0", "stain", "a stain", 10.0, 12.0)])
    rng = np.random.default_rng(0)
    monkeypatch.setattr(pk, "frames", lambda idx: {f: (rng.random((48, 64, 3)) * 255).astype(np.uint8) for f in idx})
    asked = {}

    def fake_ask(title, description, quote, images):
        asked["n"] = len(images)
        return pick, "why", "baseten"

    monkeypatch.setattr(fp, "ask_pick", fake_ask)

    def fake_extract(t, frame, out):
        for name in ("raw.jpg", "context_a.jpg", "context_b.jpg"):
            Image.new("RGB", (64, 48), (frame % 255, 0, 0)).save(out / name)
        return out / "raw.jpg", out / "context_a.jpg", out / "context_b.jpg"

    monkeypatch.setattr(pk, "extract", fake_extract)
    return pk, asked


def test_pick_none_keeps_the_frame(monkeypatch, tmp_path):
    pk, asked = _picker(monkeypatch, tmp_path, pick=0)
    res = pk.choose(fp.PickInput("itm", "Stain", "", "a stain", 11.0, None), tmp_path / "out", fp.REASON_UNPOINTED)
    assert not res.picked and not res.changed and res.row["outcome"].startswith("none")
    assert 1 <= asked["n"] <= 8
    assert res.row["window_s"] == [8.5, 15.0]


def test_pick_of_the_current_frame_changes_nothing(monkeypatch, tmp_path):
    pk, _ = _picker(monkeypatch, tmp_path, pick=1)
    first = pk.window_frames("itm", 11.0)[0]  # the current frame is always a candidate; candidates are sorted
    res = pk.choose(fp.PickInput("itm", "Stain", "", None, first / 10, None), tmp_path / "out", "x")
    assert res.picked and not res.changed and res.row["outcome"] == "current frame is best"


def test_pick_of_another_frame_extracts_it_and_maps_or_drops_the_pointer(monkeypatch, tmp_path):
    pk, _ = _picker(monkeypatch, tmp_path, pick=1)
    res = pk.choose(fp.PickInput("itm", "Stain", "", None, 14.0, None), tmp_path / "out", "x")
    assert res.picked and res.changed and res.raw.is_file() and res.t == res.frame / 10
    # a pointed item whose pointer can't be carried into the new frame is grounded as unpointed
    pk2, _ = _picker(monkeypatch, tmp_path, pick=1)
    res2 = pk2.choose(fp.PickInput("itm", "Stain", "", None, 14.0, (30.0, 20.0)), tmp_path / "out2", "x")
    assert res2.changed and res2.pointer is None and "unpointed" in res2.row["pointer_map"]


def test_pick_call_failure_or_cost_cap_degrades(monkeypatch, tmp_path):
    from punchwalk.clients.cost import CostCapReached

    pk, _ = _picker(monkeypatch, tmp_path, pick=1)

    def capped(*a, **k):
        raise CostCapReached("cap")

    monkeypatch.setattr(fp, "ask_pick", capped)
    res = pk.choose(fp.PickInput("itm", "Stain", "", None, 11.0, None), tmp_path / "out", "x")
    assert not res.changed and res.row["outcome"] == "pick call failed"


# --------------------------------------------------------------------------- pinning and renders


def test_pin_before_copies_once_and_reads_back(tmp_path):
    root = tmp_path
    idir = root / "walks" / "w" / "items" / "itm"
    idir.mkdir(parents=True)
    for name in ("raw.jpg", "evidence.jpg", "crop.jpg"):
        Image.new("RGB", (40, 30), (10, 20, 30)).save(idir / name)
    ev = Evidence(t=3.0, image="walks/w/items/itm/evidence.jpg", raw="walks/w/items/itm/raw.jpg",
                  crop="walks/w/items/itm/crop.jpg", width=40, height=30, defect_svg="M1 1 L5 1 L5 5 Z")
    fp.pin_before(idir, ev.model_dump(mode="json"), root)
    pinned = fp.pinned_before(idir)
    assert pinned["raw"] == "walks/w/items/itm/pinned/raw.jpg" and (root / pinned["crop"]).is_file()
    assert Evidence.model_validate(pinned).t == 3.0
    fp.pin_before(idir, {**ev.model_dump(mode="json"), "t": 9.0}, root)  # a later pin never replaces the first
    assert fp.pinned_before(idir)["t"] == 3.0
    assert fp.pinned_before(tmp_path / "nothing") is None


def test_before_after_is_redrawn_from_the_new_crop_keeping_the_after_side(tmp_path):
    Image.new("RGB", (400, 300), (255, 0, 0)).save(tmp_path / "new_crop.jpg")
    Image.new("RGB", (400, 300), (0, 0, 255)).save(tmp_path / "after.jpg")
    closure = {"after_image": "after.jpg", "before_after": "ba.jpg"}
    out = fp.refresh_before_after(tmp_path, closure, tmp_path / "new_crop.jpg")
    with Image.open(out) as im:
        left, right = im.getpixel((100, 100)), im.getpixel((im.width - 100, 100))
    assert left[0] > 200 and right[2] > 200
    assert fp.refresh_before_after(tmp_path, None, tmp_path / "new_crop.jpg") is None


# --------------------------------------------------------------------------- verify: status rules + description-only rewrite


def test_apply_check_on_protected_items_only_touches_verification():
    row = Item(project_id="p", number=1, origin_walk_id="w", title="t", status=ItemStatus.closed)
    bad = verify.ItemCheck(consistent=False, frame_shows_defect=False, issues=["x"], suggested_fix=None, rationale="r")
    assert verify.apply_check(row, bad, "baseten", protected=True) == "flagged"
    assert row.status == ItemStatus.closed and row.review_reason is None and not row.history
    assert row.verification["note"] == REVIEW_NOT_IN_FRAME
    good = verify.ItemCheck(consistent=True, frame_shows_defect=True, issues=[], suggested_fix=None, rationale="r")
    row2 = Item(project_id="p", number=2, origin_walk_id="w", title="t", status=ItemStatus.needs_review)
    assert verify.apply_check(row2, good, "baseten") == "matched"
    assert row2.status == ItemStatus.open


@pytest.fixture
def walk_ctx(tmp_path, monkeypatch):
    from sqlmodel import Session, SQLModel, create_engine

    from punchwalk.core.models import Project, Walk
    from punchwalk.pipeline.context import StageContext, write_stage_output

    monkeypatch.setenv("GROUNDING_V2", "1")
    reset_settings()
    engine = create_engine(f"sqlite:///{tmp_path / 'f.sqlite3'}")
    SQLModel.metadata.create_all(engine)

    def factory():
        return Session(engine, expire_on_commit=False)

    wdir = tmp_path / "data" / "walks" / "wlk_f"
    for stage, doc in ((Stage.pick_evidence, {"items": {}}), (Stage.find_points, {"events": []})):
        write_stage_output(wdir, stage, doc)
    with factory() as s:
        s.add(Project(id="prj_f", name="f"))
        s.add(Walk(id="wlk_f", project_id="prj_f"))
        s.commit()
    ctx = StageContext(walk_id="wlk_f", project_id="prj_f", kind=WalkKind.initial, walk_dir=wdir,
                       session_factory=factory)
    return ctx, factory


def _add_item(factory, item_id, number, status=ItemStatus.needs_review):
    ev = Evidence(t=5.0, image=f"walks/wlk_f/items/{item_id}/evidence.jpg", raw=f"walks/wlk_f/items/{item_id}/raw.jpg",
                  crop=f"walks/wlk_f/items/{item_id}/crop.jpg", width=40, height=30)
    with factory() as s:
        s.add(Item(id=item_id, project_id="prj_f", number=number, origin_walk_id="wlk_f", title="Wall stain",
                   description="A stain on the wall.", action="Clean it", trade="cleaning", room="Kitchen",
                   severity="cosmetic", quote="a coffee stain on the counter", status=status,
                   evidence=ev.model_dump(mode="json"),
                   review_reason=REVIEW_NOT_IN_FRAME if status == ItemStatus.needs_review else None,
                   verification={"consistent": False, "note": REVIEW_NOT_IN_FRAME, "issues": [],
                                 "provider": "baseten"}))
        s.commit()


def _patch_frame_pass(monkeypatch, *, new_t, checks):
    from punchwalk.pipeline import ground

    monkeypatch.setattr(fp.Picker, "for_walk", classmethod(lambda cls, wdir: object()))

    def fake_pick(ctx, root, item, ev, extra, event, picker, reason, rows):
        rows[item.id] = {"reason": reason}
        return ev if new_t is None else ev.model_copy(update={"t": new_t})

    monkeypatch.setattr(fp, "pick_and_adopt", fake_pick)
    monkeypatch.setattr(ground, "reground_one", lambda ctx, item, ev: (ev, None, {}))
    answers = iter(checks)
    monkeypatch.setattr(verify, "check_one", lambda item, ev: (next(answers), "baseten"))
    rewrites = []

    def fake_rewrite(item, image, crop):
        rewrites.append(item.id)
        return "A coffee stain on the counter top.", "baseten"

    monkeypatch.setattr(fp, "rewrite_description", fake_rewrite)
    return rewrites


def _chk(consistent, shows):
    return verify.ItemCheck(consistent=consistent, frame_shows_defect=shows, issues=["words differ"],
                            suggested_fix=None, rationale="r")


def test_frame_pass_rewrites_only_the_description_then_rechecks(walk_ctx, monkeypatch):
    ctx, factory = walk_ctx
    _add_item(factory, "itm_a", 1)
    rewrites = _patch_frame_pass(monkeypatch, new_t=7.5, checks=[_chk(False, True), _chk(True, True)])
    with factory() as s:
        items = [s.get(Item, "itm_a")]
    rows = {"itm_a": {"ok": True, "consistent": False, "frame_shows_defect": False}}
    tally = {"matched": 0, "flagged": 1, "failed": 0}
    verify.frame_pass(ctx, items, rows, tally, reground_all=False)
    with factory() as s:
        it = s.get(Item, "itm_a")
    assert rewrites == ["itm_a"]
    assert it.description == "A coffee stain on the counter top."
    assert (it.title, it.action, it.trade, it.room, it.severity) == ("Wall stain", "Clean it", "cleaning", "Kitchen",
                                                                     "cosmetic")
    assert it.status == ItemStatus.open and it.review_reason is None
    assert any(h.get("field") == "description" for h in it.history)
    assert tally == {"matched": 1, "flagged": 0, "failed": 0}
    assert json.loads((ctx.walk_dir / "stages" / "frame_pick.json").read_text())["items"]["itm_a"]


def test_frame_pass_with_nothing_picked_leaves_the_item_alone(walk_ctx, monkeypatch):
    ctx, factory = walk_ctx
    _add_item(factory, "itm_b", 2)
    rewrites = _patch_frame_pass(monkeypatch, new_t=None, checks=[])
    with factory() as s:
        items = [s.get(Item, "itm_b")]
    verify.frame_pass(ctx, items, {"itm_b": {"ok": True, "consistent": False, "frame_shows_defect": False}},
                      {"matched": 0, "flagged": 1, "failed": 0}, reground_all=False)
    with factory() as s:
        it = s.get(Item, "itm_b")
    assert not rewrites and it.status == ItemStatus.needs_review and it.description == "A stain on the wall."


def test_frame_pass_on_protected_items_needs_reground_all_and_never_rewrites(walk_ctx, monkeypatch):
    ctx, factory = walk_ctx
    _add_item(factory, "itm_c", 3, status=ItemStatus.closed)
    rewrites = _patch_frame_pass(monkeypatch, new_t=8.0, checks=[_chk(False, True)])
    with factory() as s:
        items = [s.get(Item, "itm_c")]
    verify.frame_pass(ctx, items, {}, {"matched": 0, "flagged": 0, "failed": 0}, reground_all=False)
    assert not (ctx.walk_dir / "stages" / "frame_pick.json").exists()  # not touched without REGROUND_ALL
    verify.frame_pass(ctx, items, {}, {"matched": 0, "flagged": 0, "failed": 0}, reground_all=True)
    with factory() as s:
        it = s.get(Item, "itm_c")
    assert not rewrites  # text never changes on a protected item
    assert it.status == ItemStatus.closed and it.description == "A stain on the wall." and not it.history


def test_items_already_tried_by_the_ground_stage_are_skipped(walk_ctx, monkeypatch):
    ctx, factory = walk_ctx
    _add_item(factory, "itm_d", 4)
    fp.save_log(ctx.walk_dir, {"itm_d": {"reason": fp.REASON_UNPOINTED, "pick": 0}}, merge=False)
    rewrites = _patch_frame_pass(monkeypatch, new_t=9.0, checks=[])
    with factory() as s:
        items = [s.get(Item, "itm_d")]
    verify.frame_pass(ctx, items, {"itm_d": {"ok": True, "consistent": False, "frame_shows_defect": False}},
                      {"matched": 0, "flagged": 1, "failed": 0}, reground_all=False)
    assert not rewrites


def test_closures_use_the_pinned_photo():
    import inspect

    from punchwalk.pipeline import closures

    src = inspect.getsource(closures)
    assert "frame_pick.pinned_before(" in src and "Evidence.model_validate(pinned)" in src


def test_switches_default_off():
    from punchwalk.config import get_settings

    s = get_settings()
    assert s.GROUNDING_V2 is False and s.REGROUND_ALL is False
    assert isinstance(Path(fp.__file__), Path)


def test_a_rewalk_decision_is_never_undone_by_a_check():
    from punchwalk.core.enums import REVIEW_FALSE_FIX

    row = Item(project_id="p", number=3, origin_walk_id="w", title="t", status=ItemStatus.needs_review,
               review_reason=REVIEW_FALSE_FIX, closure={"rewalk_id": "w2", "decision": "needs_review"})
    good = verify.ItemCheck(consistent=True, frame_shows_defect=True, issues=[], suggested_fix=None, rationale="r")
    assert verify.status_locked(row)
    verify.apply_check(row, good, "baseten", protected=verify.status_locked(row))
    assert row.status == ItemStatus.needs_review and row.review_reason == REVIEW_FALSE_FIX and not row.history
    assert row.verification["consistent"] is True
