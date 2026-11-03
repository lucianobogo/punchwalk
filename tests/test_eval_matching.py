"""Eval harness units (PLAN 3.1-3.2): labels, matching (Hungarian), metrics. Hand-built rows, no pipeline."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from eval.labels import (
    TEMPLATE,
    LabelError,
    TemplateLabels,
    discover_labels,
    is_rewalk,
    label_time,
    parse_mmss,
    parse_trade,
    project_config,
    read_labels_file,
    with_times,
)
from eval.matching import candidate, match_walk, pred_from_item, tokens
from eval.metrics import (
    PHASE7_PENDING,
    Ratio,
    any_failed,
    initial_metrics,
    rewalk_metrics,
    summary_rows,
    verdict,
)
from punchwalk.config import REPO_ROOT
from punchwalk.core.enums import ItemStatus, Trade
from punchwalk.core.models import Item

HEADER = "walk,item_no,room,description,trade,time_mmss,pointed,fixed_in_rewalk,claimed_fixed_in_rewalk,clip\n"


def write_csv(path: Path, rows: list[str]) -> Path:
    path.write_text(HEADER + "\n".join(rows) + "\n")
    return path


def labels_from(tmp_path: Path, rows: list[str], offsets: list[float] | None = None):
    rows_ = read_labels_file(write_csv(tmp_path / "walk1.csv", rows))
    return with_times(rows_, offsets or [0.0])


def item(number: int, t: float | None, title: str, *, room: str | None = "Kitchen", trade: str | None = "electrical",
         description: str = "", grounding: str | None = "sam_concept", status: str = "open", pointed: bool = True,
         video_start: float | None = None, closure: dict | None = None) -> Item:
    """A hand-built Item row (not persisted); trade/room may be missing like a half-written item."""
    it = Item(id=f"itm_{number:03d}", project_id="prj_x", number=number, origin_walk_id="wlk_x", title=title,
              description=description, room=room, status=ItemStatus(status), quote_start_s=t,
              video_start_s=video_start if video_start is not None else max(0.0, (t or 0.0) - 2.0),
              point_id=f"pnt_{number}" if pointed else None,
              evidence={"grounding": grounding} if grounding else None, closure=closure)
    if trade is None:
        object.__setattr__(it, "trade", None)
    else:
        it.trade = Trade(trade)
    return it


# --------------------------------------------------------------------------- labels


def test_template_parses_and_part_offsets_apply():
    rows = read_labels_file(REPO_ROOT / "eval" / "labels_template.csv")
    assert [r.walk for r in rows] == ["walk1", "walk1", "walk1", "walk2", "walk2"]
    w1 = [r for r in rows if r.walk == "walk1"]
    assert w1[0].trade == Trade.carpentry_millwork and w1[0].pointed and w1[0].clip == "a"
    assert w1[2].pointed is False and w1[2].clip == "b"
    timed = with_times(w1, [0.0, 82.4])
    assert [r.t for r in timed] == [14.0, 27.0, pytest.approx(82.4 + 52.0)]
    w2 = [r for r in rows if r.walk == "walk2"]
    assert is_rewalk(w2) and not is_rewalk(w1)
    assert (w2[1].fixed_in_rewalk, w2[1].claimed_fixed_in_rewalk) == (False, True)
    assert label_time(w2[0], [0.0]) == 9.0  # blank clip: time as given


def test_time_and_trade_parsing():
    assert parse_mmss("01:05") == 65.0 and parse_mmss("1:02:03") == 3723.0 and parse_mmss("42") == 42.0
    assert parse_mmss("00:07.5") == 7.5
    assert parse_trade("Carpentry & millwork") == Trade.carpentry_millwork and parse_trade("") is None
    with pytest.raises(LabelError, match="not MM:SS"):
        parse_mmss("1m05", "walk1.csv:2")
    with pytest.raises(LabelError, match=r"walk1.csv:2: unknown trade 'tiling' \(use one of: electrical"):
        parse_trade("tiling", "walk1.csv:2")


def test_bad_rows_name_the_file_and_line(tmp_path):
    with pytest.raises(LabelError, match=r"walk1.csv:3: pointed must be 1, 0 or blank"):
        read_labels_file(write_csv(tmp_path / "walk1.csv", [
            "walk1,1,Kitchen,Knob missing,carpentry_millwork,00:14,1,,,",
            "walk1,2,Kitchen,Drips,cleaning,00:27,maybe,,,",
        ]))


def test_clip_letter_beyond_parts_is_a_readable_error(tmp_path):
    rows = read_labels_file(write_csv(tmp_path / "walk1.csv", ["walk1,1,Kitchen,Knob,carpentry_millwork,00:14,1,,,c"]))
    with pytest.raises(LabelError, match=r"clip 'c' but the walk has 2 part\(s\) \(a, b\)"):
        with_times(rows, [0.0, 50.0])


def test_discover_orders_walks_and_checks_duplicates(tmp_path):
    write_csv(tmp_path / "walk1.csv", ["walk1,1,Kitchen,Knob,carpentry_millwork,00:14,1,,,",
                                       "walk1,1,Hall,Again,general,00:30,1,,,"])
    write_csv(tmp_path / "walk10.csv", ["walk10,1,Kitchen,Knob,carpentry_millwork,00:09,1,1,1,"])
    write_csv(tmp_path / "walk2.csv", [",1,Kitchen,Knob,carpentry_millwork,00:09,1,1,1,"])  # blank walk = file name
    with pytest.raises(LabelError, match="item_no 1 already used for walk1"):
        discover_labels(tmp_path)
    write_csv(tmp_path / "walk1.csv", ["walk1,1,Kitchen,Knob,carpentry_millwork,00:14,1,,,"])
    assert list(discover_labels(tmp_path)) == ["walk1", "walk2", "walk10"]  # natural order
    assert discover_labels(tmp_path / "missing") == {}


def test_template_copies_are_never_truth(tmp_path):
    shutil.copyfile(TEMPLATE, tmp_path / "walk1.csv")  # what eval/labels/ held before the human filled it in
    write_csv(tmp_path / "walk3.csv", ["walk3,1,Kitchen,Knob,carpentry_millwork,00:14,1,,,"])
    with pytest.raises(TemplateLabels) as exc:
        discover_labels(tmp_path)
    assert exc.value.problems == [(tmp_path / "walk1.csv", "template")]
    # rows that name another walk (e.g. a hand-edited template copy) are refused too
    write_csv(tmp_path / "walk1.csv", ["walk1,1,Kitchen,Knob,carpentry_millwork,00:14,1,,,",
                                       "walk2,1,Kitchen,Knob,carpentry_millwork,00:09,1,1,1,"])
    with pytest.raises(TemplateLabels) as exc:
        discover_labels(tmp_path)
    assert exc.value.problems == [(tmp_path / "walk1.csv", "walk column says walk2, not walk1")]


def test_project_rooms_precedence(tmp_path):
    write_csv(tmp_path / "walk1.csv", ["walk1,2,Bedroom,Scuff,painting,00:40,1,,,",
                                       "walk1,1,Kitchen,Knob,carpentry_millwork,00:14,1,,,"])
    by_walk = discover_labels(tmp_path)
    assert project_config(tmp_path, by_walk).rooms == ["Kitchen", "Bedroom"]  # label rooms in walk order
    assert project_config(tmp_path, by_walk, rooms=["Hall"]).rooms == ["Hall"]
    (tmp_path / "project.json").write_text('{"rooms": ["Kitchen", "Hallway"], "keywords": ["strike plate"]}')
    cfg = project_config(tmp_path, by_walk)
    assert cfg.rooms == ["Kitchen", "Hallway"] and cfg.keywords == ["strike plate"] and cfg.source.endswith("project.json")


# --------------------------------------------------------------------------- matching


def test_tokens_drop_stopwords_and_plurals():
    assert tokens("Scuff marks on the wall, uh, by the door!") == {"scuff", "mark", "wall", "door"}


def test_time_and_room_match_and_window(tmp_path):
    labels = labels_from(tmp_path, ["walk1,1,Kitchen,Upper cabinet missing knob,carpentry_millwork,00:14,1,,,"])
    near = pred_from_item(item(1, 15.5, "Bulb out", room="kitchen "))  # room match, no word overlap
    far = pred_from_item(item(2, 20.5, "Upper cabinet knob missing"))  # 6.5 s away
    pair = candidate(labels[0], near)
    assert pair and pair.room_ok and pair.jaccard == 0.0 and pair.dt == 1.5
    assert candidate(labels[0], far) is None
    res = match_walk(labels, [near, far])
    assert [(p.label.item_no, p.pred.number) for p in res.pairs] == [(1, 1)]
    assert [p.number for p in res.false_positives] == [2] and res.missed == []


def test_jaccard_only_match_when_room_is_wrong_or_missing(tmp_path):
    labels = labels_from(tmp_path, ["walk1,1,Kitchen,Upper cabinet left of sink missing knob,carpentry_millwork,00:14,1,,,"])
    wrong_room = pred_from_item(item(1, 16.0, "Cabinet knob missing", room="Hallway",
                                     description="Knob missing on the upper cabinet left of the sink."))
    no_room = pred_from_item(item(2, 13.0, "Knob missing", room=None, description="Upper cabinet."))
    unrelated = pred_from_item(item(3, 14.0, "Outlet cover cracked", room="Bedroom"))
    assert candidate(labels[0], wrong_room).jaccard >= 0.2 and not candidate(labels[0], wrong_room).room_ok
    assert candidate(labels[0], unrelated) is None  # wrong room and no word overlap
    res = match_walk(labels, [wrong_room, no_room, unrelated])
    assert len(res.pairs) == 1 and res.pairs[0].pred.number in (1, 2)
    assert {p.number for p in res.false_positives} == {1, 2, 3} - {res.pairs[0].pred.number}


def test_part_offset_decides_the_match(tmp_path):
    rows = ["walk1,1,Kitchen,Knob missing,carpentry_millwork,00:14,1,,,a",
            "walk1,2,Bedroom,Outlet cover cracked,electrical,00:03,0,,,b"]
    with_offsets = labels_from(tmp_path, rows, [0.0, 82.4])
    preds = [pred_from_item(item(1, 14.2, "Knob missing")),
             pred_from_item(item(2, 86.0, "Cracked outlet cover", room="Bedroom"))]
    res = match_walk(with_offsets, preds)
    assert [(p.label.item_no, p.pred.number) for p in res.pairs] == [(1, 1), (2, 2)]
    assert res.pairs[1].label.t == pytest.approx(85.4)
    no_offsets = with_times(read_labels_file(tmp_path / "walk1.csv"), [0.0, 0.0])
    assert len(match_walk(no_offsets, preds).pairs) == 1  # at 00:03 the outlet label is 83 s from its item


def test_hungarian_beats_greedy(tmp_path):
    # Greedy by time would give L1 (10 s) the closest item P_outlet (10.5 s) and leave the knob item to L2.
    labels = labels_from(tmp_path, [
        "walk1,1,Kitchen,Cabinet knob missing,carpentry_millwork,00:10,1,,,",
        "walk1,2,Kitchen,Outlet cover cracked,electrical,00:13,1,,,",
    ])
    outlet = pred_from_item(item(1, 10.5, "Outlet cover cracked"))
    knob = pred_from_item(item(2, 12.5, "Missing cabinet knob"))
    res = match_walk(labels, [outlet, knob])
    assert {(p.label.item_no, p.pred.title) for p in res.pairs} == {(1, "Missing cabinet knob"), (2, "Outlet cover cracked")}


def test_pred_time_falls_back_to_clip_start_plus_two():
    it = item(1, None, "Knob", video_start=30.0)
    assert pred_from_item(it).t == 32.0
    assert pred_from_item(item(2, 41.25, "Knob")).t == 41.25


# --------------------------------------------------------------------------- metrics


def test_initial_metrics_math(tmp_path):
    labels = labels_from(tmp_path, [
        "walk1,1,Kitchen,Cabinet knob missing,carpentry_millwork,00:10,1,,,",
        "walk1,2,Kitchen,Coffee drips on counter,cleaning,00:30,1,,,",
        "walk1,3,Bedroom,Outlet cover cracked,electrical,00:50,0,,,",
        "walk1,4,Hallway,Switch plate crooked,electrical,01:10,1,,,",  # missed
    ])
    preds = [
        pred_from_item(item(1, 11.0, "Missing cabinet knob", trade="carpentry_millwork", grounding="sam_concept")),
        pred_from_item(item(2, 31.0, "Coffee drips", trade="general", grounding="pointed_only", status="needs_review")),
        pred_from_item(item(3, 50.5, "Outlet cover cracked", room=None, trade=None, grounding="none", pointed=False)),
        pred_from_item(item(4, 95.0, "Paint drip on trim", room="Living room", grounding="sam_box")),  # false positive
    ]
    res = match_walk(labels, preds)
    assert [p.label.item_no for p in res.pairs] == [1, 2, 3]
    m = initial_metrics(labels, preds, res)
    assert (m.recall, m.precision) == (Ratio(3, 4), Ratio(3, 4))
    assert m.trade == Ratio(1, 3)  # #2 wrong trade, #3 no trade at all
    assert m.room == Ratio(2, 3)  # #3 has no room
    assert m.grounding == Ratio(1, 3)  # pointed labels 1, 2, 4: only #1 has a SAM outline (#4 unmatched)
    assert m.needs_review == Ratio(1, 4)
    rows = summary_rows("walk1", holdout=False, initial=m)
    assert [(r.key, r.result) for r in rows] == [("recall", "FAIL"), ("precision", "FAIL"), ("trade", "FAIL"),
                                                 ("room", "FAIL"), ("grounding", "-"), ("needs_review", "-")]
    assert rows[0].value == "0.75 (3/4)" and rows[0].target == ">= 0.90" and any_failed(rows)


def test_verdicts_and_empty_ratios():
    assert verdict("recall", Ratio(9, 10)) == "pass" and verdict("recall", Ratio(8, 10)) == "FAIL"
    assert verdict("false_claim", Ratio(1, 1)) == "pass" and verdict("false_claim", Ratio(1, 2)) == "FAIL"
    assert verdict("trade", Ratio(0, 0)) == "n/a" and Ratio(0, 0).text() == "n/a (0 of 0)"
    assert verdict("grounding", Ratio(1, 2)) == "-"


def test_rewalk_closure_and_false_claim_catch(tmp_path):
    w1 = labels_from(tmp_path, [
        "walk1,1,Kitchen,Cabinet knob missing,carpentry_millwork,00:10,1,,,",
        "walk1,2,Kitchen,Coffee drips on counter,cleaning,00:30,1,,,",
        "walk1,3,Bedroom,Outlet cover cracked,electrical,00:50,0,,,",
        "walk1,4,Hallway,Switch plate crooked,electrical,01:10,1,,,",
        "walk1,5,Hallway,Door does not latch,doors_hardware,01:30,1,,,",  # never matched
    ])
    preds = [pred_from_item(item(n, t, title)) for n, t, title in [
        (1, 10.0, "Cabinet knob missing"), (2, 30.0, "Coffee drips on counter"),
        (3, 50.0, "Outlet cover cracked"), (4, 70.0, "Switch plate crooked")]]
    match = match_walk(w1, preds)
    assert len(match.pairs) == 4
    (tmp_path / "w2").mkdir()
    w2 = labels_from(tmp_path / "w2", [
        "walk2,1,Kitchen,Cabinet knob missing,carpentry_millwork,00:09,1,1,1,",  # fixed, said fixed -> signed off
        "walk2,2,Kitchen,Coffee drips on counter,cleaning,00:18,1,0,1,",  # false claim
        "walk2,3,Bedroom,Outlet cover cracked,electrical,00:30,0,0,0,",  # said not fixed
        "walk2,4,Hallway,Switch plate crooked,electrical,00:40,1,0,1,",  # false claim, missed by the pipeline
        "walk2,5,Hallway,Door does not latch,doors_hardware,00:50,1,1,1,",  # no initial match: excluded
    ])
    after = {p.item_id: p for p in [
        pred_from_item(item(1, 10.0, "Cabinet knob missing", status="closed", closure={"decision": "signed_off"})),
        pred_from_item(item(2, 30.0, "Coffee drips", status="needs_review", closure={"decision": "needs_review"})),
        pred_from_item(item(3, 50.0, "Outlet cover", status="dispatched", closure={"decision": "still_open"})),
        pred_from_item(item(4, 70.0, "Switch plate", status="closed", closure={"decision": "signed_off"})),
    ]}
    rw = rewalk_metrics(w2, match, after, pending=False)
    assert rw.closure == Ratio(3, 4)  # #4 signed off but not fixed
    assert rw.false_claim == Ratio(1, 2)  # #2 caught, #4 not
    assert rw.unmatched == 1 and rw.rows[4].pred is None
    rows = summary_rows("walk2", holdout=False, rewalk=rw)
    assert [(r.key, r.value, r.result) for r in rows] == [("closure", "0.75 (3/4)", "FAIL"),
                                                          ("false_claim", "0.50 (1/2)", "FAIL")]

    pending = rewalk_metrics(w2, match, after, pending=True)
    assert pending.closure is None and pending.false_claim is None
    rows = summary_rows("walk2", holdout=False, rewalk=pending)
    assert [(r.value, r.result) for r in rows] == [(PHASE7_PENDING, "n/a")] * 2 and not any_failed(rows)


def test_error_row():
    rows = summary_rows("walk1", holdout=True, error="Writing the items failed.")
    assert len(rows) == 1 and rows[0].result == "error" and rows[0].holdout and any_failed(rows)
