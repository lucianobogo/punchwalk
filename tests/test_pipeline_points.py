"""find_points (PLAN 1.3, pointing): holds, fingertip, mention pairing. Synthetic tracks, no media."""

from __future__ import annotations

import numpy as np
import pytest

from punchwalk.pipeline import params as P
from punchwalk.pipeline.mentions import build_mentions
from punchwalk.pipeline.points import (
    choose_holds,
    detect_events,
    entry_edge,
    find_holds,
    fingertip,
    hold_fingertip,
)

W, H, FPS = 720, 1280, 10.0


def _svg_rect(x1, y1, x2, y2) -> str:
    return f"M{x1} {y1} L{x2} {y1} L{x2} {y2} L{x1} {y2} Z"


def hand_track(oid="a:0", *, enter=10, hold=(14, 34), leave=38, tip=(300.0, 700.0), edge="right", conf=0.9):
    """A hand box entering from `edge`, still from hold[0] to hold[1], then leaving."""
    recs = []
    tx, ty = tip
    for f in range(enter, leave + 1):
        if f < hold[0]:
            k = (hold[0] - f) / (hold[0] - enter)
        elif f > hold[1]:
            k = (f - hold[1]) / (leave - hold[1])
        else:
            k = 0.0
        if edge == "right":
            x1 = tx + k * (W - tx)
            box = [x1, ty - 40, W, ty + 50]
        else:  # bottom
            y1 = ty + k * (H - ty)
            box = [tx - 45, y1, tx + 45, H]
        recs.append({"frame": f, "object_id": oid, "box": box, "confidence": conf, "svg": _svg_rect(*box)})
    return recs


def test_hold_found_between_entry_and_withdrawal():
    holds = find_holds(hand_track(), fps=FPS, w=W, h=H, t0=0.0, t1=10.0)
    longest = max(holds, key=lambda h: h.hold_s)
    assert (longest.first, longest.last) == (14, 34)
    assert longest.hold_s == pytest.approx(2.1)


def test_hold_needs_min_frames_and_duration():
    short = hand_track(hold=(14, 16), leave=20)  # 3 still frames < POINT_MIN_HOLD_FRAMES
    holds = find_holds(short, fps=FPS, w=W, h=H, t0=0.0, t1=10.0)
    assert all(h.hold_s < 1.0 for h in holds)
    assert not [h for h in holds if h.first == 14 and h.last == 16]
    exact = hand_track(hold=(14, 14 + P.POINT_MIN_HOLD_FRAMES - 1), leave=24)
    holds = find_holds(exact, fps=FPS, w=W, h=H, t0=0.0, t1=10.0)
    assert any(h.first == 14 and h.hold_s == pytest.approx(P.POINT_MIN_HOLD_S) for h in holds)


def test_hold_window_and_low_confidence_filters():
    assert find_holds(hand_track(), fps=FPS, w=W, h=H, t0=5.0, t1=9.0) == []
    assert find_holds(hand_track(conf=0.1), fps=FPS, w=W, h=H, t0=0.0, t1=10.0) == []


def test_moving_hand_is_not_a_hold():
    recs = [{"frame": f, "object_id": "a:1", "box": [f * 30.0, 600, f * 30.0 + 200, 700], "confidence": 0.9,
             "svg": ""} for f in range(20)]
    assert find_holds(recs, fps=FPS, w=W, h=H, t0=0.0, t1=5.0) == []


def _l_mask() -> np.ndarray:
    """A hand entering from the bottom: a vertical forearm, then a finger pointing up-left."""
    m = np.zeros((H, W), np.uint8)
    m[900:H, 400:480] = 1  # forearm to the bottom border
    m[820:900, 380:500] = 1  # palm
    m[790:820, 250:420] = 1  # index finger pointing left
    return m


def test_fingertip_on_l_shaped_mask_is_farthest_from_entry():
    m = _l_mask()
    ys, xs = np.nonzero(m)
    box = (float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1))
    x, y, edge = fingertip(m, box, W, H)
    assert edge == "bottom"
    assert x == pytest.approx(251, abs=10) and 790 <= y <= 821  # the finger spans x 250..420


def test_entry_edge_rules():
    m = np.zeros((H, W), np.uint8)
    m[600:700, 500:W] = 1
    assert entry_edge(m, (500, 600, W, 700), W, H) == "right"
    # corner: the border with more mask pixels along it wins
    m2 = np.zeros((H, W), np.uint8)
    m2[1000:H, 600:W] = 1
    m2[1200:H, 300:W] = 1
    assert entry_edge(m2, (300, 1000, W, H), W, H) == "bottom"
    # floating box: the hint (where the track came in) beats the closest side
    assert entry_edge(None, (100, 600, 300, 700), W, H) == "left"
    assert entry_edge(None, (100, 600, 300, 700), W, H, hint="right") == "right"


def test_hold_fingertip_uses_track_entry_when_box_floats():
    """SAM's hand box often stops at the sleeve; the tip must stay on the finger end."""
    recs = hand_track(tip=(200.0, 700.0))
    for r in recs:  # during the hold the box no longer reaches the right border
        if 14 <= r["frame"] <= 34:
            r["box"] = [200.0, 660.0, 380.0, 750.0]
            r["svg"] = _svg_rect(*r["box"])
    hold = max(find_holds(recs, fps=FPS, w=W, h=H, t0=0, t1=10), key=lambda h: h.hold_s)
    x, _, edge = hold_fingertip(hold, W, H, recs)
    assert edge == "right" and x < 215


def test_choose_holds_longest_then_closest_and_unused():
    a = find_holds(hand_track("a:0", enter=0, hold=(2, 8), leave=10), fps=FPS, w=W, h=H, t0=0, t1=99)
    b = find_holds(hand_track("a:1", enter=20, hold=(22, 40), leave=44), fps=FPS, w=W, h=H, t0=0, t1=99)
    holds = a + b
    best = choose_holds(holds, 1, 0.0, set())
    assert best[0].object_id == "a:1"  # longest wins even though a:0 is closer to the start
    used = {best[0].uid}
    assert choose_holds(holds, 1, 0.0, used)[0].object_id == "a:0"


def _turn(idx, start, end, text, kind="defect", defects=None, cont=False):
    return {"id": f"trn_{idx}", "idx": idx, "start_ms": int(start * 1000), "end_ms": int(end * 1000), "text": text,
            "kind": kind, "room": "Kitchen", "defects": defects if defects is not None else [text],
            "continues_previous": cont, "status_target": None, "claimed": None}


def test_detect_events_one_point_per_mention_and_voice_only():
    turns = [
        _turn(0, 2.0, 4.0, "knob missing"),
        _turn(1, 6.0, 7.0, "okay", kind="other", defects=[]),
        _turn(2, 20.0, 22.0, "scuff on wall"),  # nobody points: voice only
    ]
    mentions = build_mentions(turns)
    assert [m.key for m in mentions] == ["0.0", "2.0"]
    pairs, found = detect_events(mentions, hand_track(), fps=FPS, w=W, h=H, duration_s=30.0)
    assert [(m.key, h.first) for m, h in pairs] == [("0.0", 14)]
    assert found == {"0": pytest.approx(found["0"]), "2": 0} and found["0"] >= 1


def test_two_defects_in_one_turn_get_two_points_in_time_order():
    turns = [_turn(0, 1.0, 9.0, "two things", defects=["knob missing", "door scuffed"])]
    recs = hand_track("a:0", enter=5, hold=(8, 20), leave=24, tip=(300, 600)) + \
        hand_track("a:1", enter=50, hold=(54, 70), leave=74, tip=(420, 900))
    pairs, _ = detect_events(build_mentions(turns), recs, fps=FPS, w=W, h=H, duration_s=12.0)
    assert [(m.text, h.object_id) for m, h in pairs] == [("knob missing", "a:0"), ("door scuffed", "a:1")]


def test_mentions_group_continuations():
    turns = [_turn(0, 1.0, 2.0, "Upper cabinet left of the sink,", defects=[]),
             _turn(1, 2.5, 3.5, "knob missing.", defects=["upper cabinet left of sink: knob missing"], cont=True)]
    (m,) = build_mentions(turns)
    assert m.turn_ids == ["trn_0", "trn_1"] and m.start_s == 1.0 and m.end_s == 3.5
    assert m.text == "upper cabinet left of sink: knob missing"
    assert m.quote == "Upper cabinet left of the sink, knob missing."
