"""PLAN 10.3: grounding v2 (locate -> SAM box/point prompts -> score -> check -> smooth), offline."""

from __future__ import annotations

import json

import cv2
import numpy as np
import pytest
from meta_sam_parser import build_object_prompt

from punchwalk.clients import sam
from punchwalk.config import REPO_ROOT, reset_settings
from punchwalk.core.enums import Grounding
from punchwalk.pipeline import ground, ground_v2
from punchwalk.pipeline import params as P
from punchwalk.pipeline.ground_v2 import Candidate, ItemInput, Located

W, H = 1080, 1436
FX = REPO_ROOT / "fixtures" / "pipeline" / "ground_v2"


def _cand(box, *, source="box", conf=0.8, cid=1, grounding=Grounding.sam_box, disc=False) -> Candidate:
    m = np.zeros((H, W), np.uint8)
    x1, y1, x2, y2 = box
    if disc:
        cv2.ellipse(m, ((x1 + x2) // 2, (y1 + y2) // 2), ((x2 - x1) // 2, (y2 - y1) // 2), 0, 0, 360, 1, -1)
    else:
        m[y1:y2, x1:x2] = 1
    return Candidate(id=cid, source=source, grounding=grounding, confidence=conf, soft=m.astype(np.float32), mask=m)


# --------------------------------------------------------------------------- (c) scoring


def test_tight_mask_beats_whole_object_and_whole_object_is_rejected():
    spark = (400.0, 850.0, 760.0, 1190.0)  # the burner ring
    ring = _cand((400, 850, 760, 1190), disc=True, cid=1)
    cooktop = _cand((300, 560, 1080, 1370), source="concept", cid=100, conf=0.95, grounding=Grounding.sam_concept)
    out = ground_v2.score_candidates([cooktop, ring], spark, "region", (700, 1100), W, H)
    assert out[0] is ring and ring.rejected is None
    assert cooktop.rejected and "x the located box" in cooktop.rejected  # 5x the box area


def test_frame_fraction_rule_and_whole_surface_exception():
    spark = (0.0, 0.0, 900.0, 1000.0)
    big = _cand((0, 0, 900, 1000))  # 58% of the frame, same as the box
    ground_v2.score_candidates([big], spark, "region", None, W, H)
    assert big.rejected and "of the frame" in big.rejected
    big2 = _cand((0, 0, 900, 1000))
    ground_v2.score_candidates([big2], spark, "whole_surface", None, W, H)
    assert big2.rejected is None
    assert not ground_v2.spark_box_ok(spark, "region", W, H)
    assert ground_v2.spark_box_ok(spark, "whole_surface", W, H)


def test_fingertip_rules():
    spark = (400.0, 400.0, 500.0, 500.0)
    on = _cand((400, 400, 500, 500), cid=1)
    far = _cand((400, 400, 500, 500), cid=2)
    ground_v2.score_candidates([on], spark, "spot", (450, 450), W, H)
    ground_v2.score_candidates([far], spark, "spot", (450, 1300), W, H)  # 800 px away > 25% of the width
    assert on.rejected is None and on.finger_dist == 0.0
    assert far.rejected and "fingertip" in far.rejected
    near = _cand((400, 400, 500, 500), cid=3)
    ground_v2.score_candidates([near], spark, "spot", (450, 530), W, H)  # 30 px below: allowed, scored lower
    assert near.rejected is None and 0 < near.score < on.score


def test_near_duplicates_dropped_and_specks_rejected():
    spark = (400.0, 400.0, 600.0, 600.0)
    a = _cand((400, 400, 600, 600), cid=1, conf=0.9)
    b = _cand((402, 401, 600, 600), cid=2, conf=0.7)  # IoU ~0.99 with a
    speck = _cand((500, 500, 503, 503), cid=3)
    out = ground_v2.score_candidates([b, speck, a], spark, "region", None, W, H)
    assert out[0] is a and a.rejected is None
    assert b.rejected and b.rejected.startswith("duplicate")
    assert speck.rejected and "speck" in speck.rejected


# --------------------------------------------------------------------------- (b) prompts


def test_prompts_pointed_and_unpointed_in_media_px():
    box = (300.0, 800.0, 700.0, 1100.0)
    specs = ground_v2.build_objects(box, (650.0, 1050.0), W, H)
    assert [s for s, _ in specs] == ["box", "box+finger", "finger", "finger+neg"]
    assert [o.id for _, o in specs] == [1, 2, 3, 4]
    neg = specs[3][1].negative
    assert len(neg) == 4
    for x, y in neg:  # just outside the box
        inside = box[0] <= x < box[2] and box[1] <= y < box[3]
        assert not inside
        assert ground_v2.point_to_mask((x, y), _cand((300, 800, 700, 1100)).mask) <= 0.2 * 400
    unp = ground_v2.build_objects(box, None, W, H)
    assert [s for s, _ in unp] == ["box", "box+neg"]
    text = build_object_prompt(size=(W, H), objects=sam.image_prompt_objects([o for _, o in specs], (W, H)))
    assert text.startswith("<0f>1<|box;x1=300;y1=800;")
    assert "w=1080;h=1436" in text and text.count("<|point;") == 1 + 1 + 1 + 4


def test_negative_points_off_the_image_are_dropped():
    assert len(ground_v2.negative_points((0.0, 0.0, 100.0, 100.0), W, H)) == 2  # left and top are outside


def test_crop_rule_and_round_trip():
    small = (500.0, 700.0, 540.0, 760.0)  # 40 px short side < 15% of 1080
    crop = ground_v2.crop_for_box(small, W, H)
    assert crop is not None
    cx1, cy1, cx2, cy2 = crop
    assert min(cx2 - cx1, cy2 - cy1) >= P.V2_CROP_MIN_SHORT_PX - 1
    assert cx1 <= small[0] and cy1 <= small[1] and cx2 >= small[2] and cy2 >= small[3]
    mbox, mptr = ground_v2.to_media(small, (520.0, 730.0), crop)
    assert mbox[0] == small[0] - cx1 and mptr == (520.0 - cx1, 730.0 - cy1)
    # a SAM record on the crop maps back onto the same frame pixels
    rec = sam.SamRecord(object_id=1, frame_index=0, box=mbox, confidence=0.9, media_size=(cx2 - cx1, cy2 - cy1))
    rec._raster = np.ones((6, 4), np.uint8)
    soft = ground_v2.record_to_frame(rec, (cx2 - cx1, cy2 - cy1), crop, W, H)
    ys, xs = np.nonzero(soft >= 0.5)
    assert (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1) == (500, 700, 540, 760)
    assert ground_v2.crop_for_box((100.0, 100.0, 900.0, 900.0), W, H) is None  # big boxes run on the frame


def test_locate_box_scaled_back_and_concept_cleaned(monkeypatch, tmp_path):
    raw = tmp_path / "raw.jpg"
    cv2.imwrite(str(raw), np.full((2000, 1500, 3), 128, np.uint8))  # long side 2000 -> sent at 1600
    seen = {}

    def fake_structured(model, messages, images=(), **kw):
        seen["prompt"] = messages[-1]["content"]
        import io

        from PIL import Image
        seen["size"] = Image.open(io.BytesIO(images[0])).size
        return model(visible=True, box=[400, 800, 480, 880], extent="spot", concept="Burn Mark on glass", rationale="x"), \
            type("R", (), {"provider": "baseten"})()

    monkeypatch.setattr(ground_v2.llm, "structured", fake_structured)
    item = ItemInput("itm_x", raw, 1500, 2000, "Burn mark", pointer=(550.0, 1050.0))
    loc = ground_v2.locate(item)
    assert seen["size"] == (1200, 1600)
    assert "(440, 840)" in seen["prompt"]  # the ring in model px
    assert loc.box == (500.0, 1000.0, 600.0, 1100.0)  # back to display px
    assert loc.concept == "burn mark on"
    assert ground_v2.clean_concept("Punch item") is None


# --------------------------------------------------------------------------- (d)/(e) never ship a failed outline


def _item(tmp_path, pointer=(450.0, 450.0)) -> ItemInput:
    raw = tmp_path / "raw.jpg"
    cv2.imwrite(str(raw), np.full((H, W, 3), 150, np.uint8))
    return ItemInput("itm_t", raw, W, H, "Wall stain", "A brown stain", "there's a stain here", pointer, 7)


def _patch_pipeline(monkeypatch, cands, pick, box=(400.0, 400.0, 500.0, 500.0)):
    monkeypatch.setattr(ground_v2, "locate", lambda item, save_dir=None: Located(True, box, "region", "stain", "r"))
    monkeypatch.setattr(ground_v2, "segment", lambda item, loc, notes, save_dir=None: cands)
    calls = {}

    def fake_check(item, offered, save_dir=None):
        calls["offered"] = [c.source for c in offered]
        return pick, "why", "baseten"

    monkeypatch.setattr(ground_v2, "check", fake_check)
    return calls


def test_picked_mask_is_smoothed_and_mapped(monkeypatch, tmp_path):
    c = _cand((400, 400, 500, 500), disc=True)
    calls = _patch_pipeline(monkeypatch, [c], pick=1)
    res = ground_v2.ground_item(_item(tmp_path))
    assert calls["offered"] == ["box", "spark_box"]
    assert res.grounding == Grounding.sam_box and res.defect_svg.startswith("M")
    assert res.row["result"]["source"] == "box" and res.concept == "stain"


def test_concept_pick_maps_to_sam_concept_and_box_pick_to_rounded_rect(monkeypatch, tmp_path):
    c = _cand((400, 400, 500, 500), disc=True, source="concept", cid=100, grounding=Grounding.sam_concept)
    _patch_pipeline(monkeypatch, [c], pick=1)
    assert ground_v2.ground_item(_item(tmp_path)).grounding == Grounding.sam_concept
    _patch_pipeline(monkeypatch, [c], pick=2)  # the Spark box
    res = ground_v2.ground_item(_item(tmp_path))
    assert res.grounding == Grounding.spark_box and res.row["result"]["source"] == "spark_box"
    assert res.defect_box == (400.0, 400.0, 500.0, 500.0) and res.defect_svg.count("L") > 8


def test_nothing_picked_or_all_rejected_means_no_red_outline(monkeypatch, tmp_path):
    good = _cand((400, 400, 500, 500), disc=True)
    _patch_pipeline(monkeypatch, [good], pick=0)
    res = ground_v2.ground_item(_item(tmp_path))
    assert res.defect_svg is None and res.grounding == Grounding.pointed_only
    res = ground_v2.ground_item(_item(tmp_path, pointer=None))
    assert res.defect_svg is None and res.grounding == Grounding.none
    # every mask fails (c) and the box is most of the frame: the check is never called, nothing shown
    wall = _cand((0, 0, 1080, 1000))
    calls = _patch_pipeline(monkeypatch, [wall], pick=1, box=(0.0, 0.0, 1000.0, 1000.0))
    res = ground_v2.ground_item(_item(tmp_path))
    assert "offered" not in calls and res.defect_svg is None
    assert res.row["result"]["reason"] == "no candidate passed the score"


def test_not_visible_and_api_errors_never_crash(monkeypatch, tmp_path):
    from punchwalk.clients.base import ApiError

    monkeypatch.setattr(ground_v2, "locate", lambda item, save_dir=None: Located(False, None, "spot", None, "r"))
    res = ground_v2.ground_item(_item(tmp_path))
    assert res.defect_svg is None and "not visible" in res.row["result"]["reason"]

    def boom(item, save_dir=None):
        raise ApiError("baseten", "/chat/completions", 503, "down")

    monkeypatch.setattr(ground_v2, "locate", boom)
    res = ground_v2.ground_item(_item(tmp_path))
    assert res.defect_svg is None and "locate_error" in res.row


def test_check_region_zooms_small_shapes():
    r = ground_v2.check_region([(500.0, 700.0, 520.0, 720.0)], None, W, H)
    side = min(r[2] - r[0], r[3] - r[1])
    assert side == round(P.V2_CHECK_MIN_FRAC * W)
    assert r[0] <= 500 and r[2] >= 520


# --------------------------------------------------------------------------- (g) pointer mapping


def _texture(seed=1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = (rng.random((480, 640)) * 255).astype(np.uint8)
    return cv2.GaussianBlur(img, (5, 5), 1.0)


def test_homography_maps_a_shifted_view_and_fails_on_unrelated():
    src = _texture()
    M = np.float32([[1, 0, 25], [0, 1, -15]])
    dst = cv2.warpAffine(src, M, (640, 480), borderMode=cv2.BORDER_REFLECT)
    q = ground_v2.map_pointer_homography(src, dst, (300.0, 240.0))
    assert q is not None and abs(q[0] - 325) < 2 and abs(q[1] - 225) < 2
    assert ground_v2.map_pointer_homography(src, _texture(seed=9), (300.0, 240.0)) is None


def test_resolve_pointer_rules(tmp_path):
    notes: dict = {}
    raw = tmp_path / "raw.jpg"
    cv2.imwrite(str(raw), _texture())
    kw = dict(pointing_raw=None, evidence_raw=raw, fingertip_display=(1.0, 1.0), notes=notes)
    assert ground_v2.resolve_pointer(None, aligned=False, same_frame=False, **kw) is None
    assert ground_v2.resolve_pointer((5.0, 5.0), aligned=True, same_frame=False, **kw) == (5.0, 5.0)
    assert ground_v2.resolve_pointer((5.0, 5.0), aligned=False, same_frame=True, **kw) == (5.0, 5.0)
    assert ground_v2.resolve_pointer((5.0, 5.0), aligned=False, same_frame=False, **kw) is None  # -> unpointed
    assert "unpointed" in notes["pointer_map"]


# --------------------------------------------------------------------------- ground.py switch


def test_switch_off_by_default_and_v2_mapping(monkeypatch, tmp_path):
    from punchwalk.config import get_settings
    from punchwalk.core.models import Evidence

    assert get_settings().GROUNDING_V2 is False
    monkeypatch.setenv("GROUNDING_V2", "1")
    reset_settings()
    assert get_settings().GROUNDING_V2 is True
    res = ground_v2.V2Result(Grounding.none, None, None, "stain", {"result": {"source": None, "reason": "x"}})
    monkeypatch.setattr(ground_v2, "ground_item", lambda item, save_dir=None: res)
    ev = Evidence(t=1.0, image="i.jpg", raw="r.jpg", crop="c.jpg", width=W, height=H, pointer={"x": 5, "y": 5})
    item = type("I", (), {"id": "itm_1", "title": "t", "description": "", "quote": None, "number": 1})()
    rows: dict = {}
    g, svg, box, concept, notes = ground._ground_v2(tmp_path, tmp_path / "r.jpg", ev, item, {"aligned": True}, None,
                                                    "old concept", rows)
    assert g == Grounding.pointed_only and svg is None and concept == "stain"  # the dot stays: pointed_only
    assert rows["itm_1"]["result"]["reason"] == "x" and notes["v2"] is True


# --------------------------------------------------------------------------- replay (recorded live once)


def test_replay_item_003_burner_ring(monkeypatch):
    """#003 'Stove top damaged at front left burner': v1 outlined the whole cooktop; v2 outlines the ring."""
    monkeypatch.setenv("FIXTURE_CACHE_DIR", str(FX / "cache"))
    reset_settings()
    meta = json.loads((FX / "item_003" / "item.json").read_text())
    expected = json.loads((FX / "item_003" / "expected.json").read_text())
    item = ItemInput(meta["item_id"], FX / "item_003" / "raw.jpg", meta["width"], meta["height"], meta["title"],
                     meta["description"], meta["quote"], tuple(meta["pointer"]), meta["number"])
    res = ground_v2.ground_item(item)
    assert res.grounding.value == expected["grounding"]
    assert res.row["result"]["source"] == expected["source"]
    assert res.row["check"]["pick"] == expected["pick"]
    assert list(res.defect_box) == pytest.approx(expected["defect_box"], abs=1.0)
    x1, y1, x2, y2 = res.defect_box
    assert (x2 - x1) * (y2 - y1) < 0.2 * meta["width"] * meta["height"]  # not the whole cooktop
    ring = (390, 849, 758, 1187)  # the burner ring, read off the frame by eye
    assert ground_v2.box_iou(res.defect_box, ring) > 0.6
    cooktop = [c for c in res.row["candidates"] if c["rejected"] is None]
    assert cooktop, "at least one candidate passed the score"


# --------------------------------------------------------------------------- 10.2 contact sheet


def test_contact_sheet_orders_by_number_and_reads_overlay(tmp_path):
    import importlib.util
    import sqlite3

    from PIL import Image

    spec = importlib.util.spec_from_file_location("contact_sheet", REPO_ROOT / "scripts" / "contact_sheet.py")
    cs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cs)
    data = tmp_path / "d"
    (data / "walks" / "w1" / "stages").mkdir(parents=True)
    con = sqlite3.connect(data / "punchwalk.sqlite3")
    con.execute("create table items (id text, number int, origin_walk_id text, project_id text, title text, evidence text)")
    for n, pointed in ((2, False), (1, True)):
        for name in ("raw", "evidence"):
            Image.new("RGB", (300, 400), (120, 120, 120)).save(data / f"{name}{n}.jpg")
        ev = {"image": f"evidence{n}.jpg", "raw": f"raw{n}.jpg", "grounding": "sam_box",
              "pointer": {"x": 10, "y": 10} if pointed else None}
        con.execute("insert into items values (?,?,?,?,?,?)", (f"itm_{n}", n, "w1", "prj", f"Item {n}", json.dumps(ev)))
    con.commit()
    con.close()
    out = tmp_path / "sheet.jpg"
    assert cs.main(["--data-dir", str(data), "--project", "prj", "--out", str(out), "--cols", "3"]) == 0
    idx = json.loads(out.with_suffix(".json").read_text())["items"]
    assert [i["number"] for i in idx] == [1, 2] and [i["pointed"] for i in idx] == [True, False]
    (data / "walks" / "w1" / "stages" / "ground_v2.json").write_text(json.dumps(
        {"items": {"itm_1": {"result": {"grounding": "sam_concept", "defect_svg": "M10 10 L50 10 L50 50 Z"}}}}))
    cs.main(["--data-dir", str(data), "--project", "prj", "--out", str(out), "--overlay-from", "stages/ground_v2.json"])
    idx = json.loads(out.with_suffix(".json").read_text())["items"]
    assert [i["grounding"] for i in idx] == ["sam_concept", "not in overlay"]
    assert Image.open(out).size[0] == 4 * 520 + 5 * 12  # default: 4 tiles of 520 px per row


# --------------------------------------------------------------------------- 10.3 fixes (verifier findings)


def test_c_shaped_arc_and_off_box_blob_are_rejected_by_shape():
    spark = (450.0, 870.0, 690.0, 1125.0)
    arc = np.zeros((H, W), np.uint8)
    cv2.ellipse(arc, (570, 1000), (130, 140), 0, 20, 300, 1, 18)  # a C-shaped band around the outlet
    c_arc = Candidate(id=2, source="box+finger", grounding=Grounding.sam_box, confidence=0.8, mask=arc,
                      soft=arc.astype(np.float32))
    blob = _cand((700, 860, 760, 920), cid=3)  # beside the box, on blank wall
    good = _cand((470, 890, 670, 1110), cid=1, disc=True)
    ground_v2.score_candidates([c_arc, blob, good], spark, "region", (600, 1000), W, H)
    assert good.rejected is None and good.inside > 0.9 and good.solidity > 0.9
    assert c_arc.rejected and ("shape" in c_arc.rejected or "inside" in c_arc.rejected)
    assert blob.rejected and "inside the located box" in blob.rejected


def test_small_box_crop_keeps_the_fingertip_inside():
    box = (500.0, 700.0, 520.0, 720.0)
    crop = ground_v2.crop_for_box(box, W, H, pointer=(900.0, 1200.0))
    assert crop[0] <= 500 and crop[2] >= 900 + P.V2_CROP_POINTER_MARGIN_PX and crop[3] >= 1200 + P.V2_CROP_POINTER_MARGIN_PX
    specs = ground_v2.build_objects((10.0, 10.0, 50.0, 50.0), (-5.0, 20.0), 640, 640)  # fingertip off the media
    assert [s for s, _ in specs] == ["box", "box+neg"]


def test_raw_of_the_wrong_size_is_resized_not_trusted(tmp_path):
    raw = tmp_path / "raw.jpg"
    cv2.imwrite(str(raw), np.full((718, 540, 3), 90, np.uint8))  # half size
    notes: dict = {}
    img = ground_v2.load_frame(ItemInput("itm", raw, W, H, "t"), notes)
    assert img.size == (W, H) and notes["resized_from"] == [540, 718]


def test_cost_cap_leaves_the_item_without_an_outline(monkeypatch, tmp_path):
    from punchwalk.clients.cost import CostCapReached

    def capped(item, save_dir=None):
        raise CostCapReached("spend cap reached")

    monkeypatch.setattr(ground_v2, "locate", capped)
    res = ground_v2.ground_item(_item(tmp_path))
    assert res.defect_svg is None and res.row["result"]["reason"] == "cost cap reached"
    assert res.grounding == Grounding.pointed_only


def test_hand_at_the_fingertip_drops_finger_prompts_and_rejects_hand_masks(monkeypatch, tmp_path):
    hand = np.zeros((H, W), np.uint8)
    hand[1000:1300, 600:800] = 1  # the hand, its tip at (650, 1000)
    assert ground_v2.hand_at((650.0, 995.0), hand, P.V2_HAND_NEAR_FINGER_PX)
    assert not ground_v2.hand_at((100.0, 100.0), hand, P.V2_HAND_NEAR_FINGER_PX)
    finger_blob = _cand((600, 950, 700, 1060), cid=2, source="box+finger")  # half of it on the finger
    finger_blob.hand_overlap = float(np.logical_and(finger_blob.mask, hand).sum()) / finger_blob.mask.sum()
    ground_v2.score_candidates([finger_blob], (590.0, 940.0, 710.0, 1070.0), "region", (650, 995), W, H)
    assert finger_blob.rejected and "hand" in finger_blob.rejected
    sent = {}

    def fake_objects(media, objects):
        sent["sources"] = [o.positive for o in objects]
        return sam.SamResult(media="image", raw_text="", frames={}, diagnostics=[], media_size=(W, H))

    monkeypatch.setattr(ground_v2.sam, "objects_on_image", fake_objects)
    item = _item(tmp_path, pointer=(650.0, 995.0))
    item.hand_mask = hand
    notes: dict = {}
    ground_v2.segment(item, Located(True, (500.0, 800.0, 900.0, 1100.0), "region", None, "r"), notes)
    assert "dropped" in notes["finger_prompts"] and all(not p for p in sent["sources"])
