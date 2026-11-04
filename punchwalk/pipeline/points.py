"""Stage `find_points` (PLAN 1.3, pointing revision): one pointing event per spoken mention.

The inspector points at each defect with an index finger, holds it ~2 s and describes it. For
each mention group (see `mentions.py`) we search the window [turn.start - 2.0 s, turn.end + 1.0 s]
for *holds*: a SAM hand track that stays roughly still (box-center motion below a threshold) for
>= 0.4 s over >= 4 consecutive SAM frames. Each mention gets the longest unused hold (ties: the
one closest to the mention's start); a group with n mentions takes its n best holds, assigned in
time order. No hold → no event, and the mention goes down the voice-only path.

Fingertip = the hand-mask point farthest from where the hand enters the frame: the entry edge is
the frame border the hand box touches (the one with the most mask pixels along it), else the box
side closest to a border; the entry point is the centroid of the mask's pixels along that side.
When the box touches no border (SAM's hand mask often stops at the wrist or sleeve), the border
this hand track touched most recently is used, and only then the box side closest to a border.
The fingertip is the mean of the few mask pixels farthest (Euclidean) from the entry point,
taken as the median over the frames of the hold.

Outputs: `Point` rows (number 1-based by t; display pixels) and `stages/find_points.json`:
    {"version", "events": [{"id", "number", "mention_key", "turn_id", "t", "frame", "fingertip"
       (SAM px), "fingertip_display", "hand_box" (SAM px), "hand_box_display", "hold_s",
       "hold_frames": [first, last], "sam_object_ids", "mean_conf", "svg" (SAM px), "entry_edge"}],
     "mentions": [Mention dicts with "point_id" or null], "candidates": {group: n holds found}}
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sqlmodel import select

from punchwalk.core.enums import Stage
from punchwalk.core.models import Box, Point
from punchwalk.pipeline import params as P
from punchwalk.pipeline.context import StageContext, StageOutcome, stable_id
from punchwalk.pipeline.frames import rasterize_svg
from punchwalk.pipeline.mentions import Mention, build_mentions

Box4 = tuple[float, float, float, float]
EDGES = ("left", "right", "top", "bottom")


# --------------------------------------------------------------------------- geometry


def center(box: Iterable[float]) -> tuple[float, float]:
    x1, y1, x2, y2 = box
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0


def touching_edges(box: Box4, w: int, h: int) -> list[str]:
    x1, y1, x2, y2 = box
    tol_x, tol_y = P.EDGE_TOUCH_FRAC * w, P.EDGE_TOUCH_FRAC * h
    return [e for e, ok in (("left", x1 <= tol_x), ("right", x2 >= w - tol_x),
                            ("top", y1 <= tol_y), ("bottom", y2 >= h - tol_y)) if ok]


def entry_edge(mask: np.ndarray | None, box: Box4, w: int, h: int, hint: str | None = None) -> str:
    """The frame border the hand comes in from (see module docstring).

    `hint` (the border this hand track touched most recently) is used when the box touches no
    border: SAM's hand mask often stops at the wrist or sleeve, so the box floats inside the frame.
    """
    x1, y1, x2, y2 = box
    tol_x, tol_y = P.EDGE_TOUCH_FRAC * w, P.EDGE_TOUCH_FRAC * h
    touching = touching_edges(box, w, h)
    if not touching and hint in EDGES:
        return hint
    if len(touching) == 1:
        return touching[0]
    if len(touching) > 1:
        if mask is None or not mask.any():
            return touching[0]
        band_x, band_y = max(2, int(math.ceil(tol_x))), max(2, int(math.ceil(tol_y)))
        strips = {"left": mask[:, :band_x], "right": mask[:, w - band_x:],
                  "top": mask[:band_y, :], "bottom": mask[h - band_y:, :]}
        return max(touching, key=lambda e: int(strips[e].sum()))
    gaps = {"left": x1, "right": w - x2, "top": y1, "bottom": h - y2}
    return min(EDGES, key=lambda e: gaps[e])


def entry_point(mask: np.ndarray | None, box: Box4, edge: str) -> tuple[float, float]:
    """Centroid of the mask pixels along the entry side of the hand (box-side midpoint if no mask)."""
    x1, y1, x2, y2 = box
    if mask is not None and mask.any():
        ys, xs = np.nonzero(mask)
        if edge in ("left", "right"):
            ext = xs.min() if edge == "left" else xs.max()
            band = max(2.0, 0.03 * (xs.max() - xs.min() + 1))
            sel = np.abs(xs - ext) <= band
        else:
            ext = ys.min() if edge == "top" else ys.max()
            band = max(2.0, 0.03 * (ys.max() - ys.min() + 1))
            sel = np.abs(ys - ext) <= band
        return float(xs[sel].mean()), float(ys[sel].mean())
    return {"left": (x1, (y1 + y2) / 2), "right": (x2, (y1 + y2) / 2),
            "top": ((x1 + x2) / 2, y1), "bottom": ((x1 + x2) / 2, y2)}[edge]


def fingertip(
    mask: np.ndarray | None, box: Box4, w: int, h: int, hint: str | None = None
) -> tuple[float, float, str]:
    """(x, y, entry edge): the mask point farthest from where the hand enters the frame."""
    edge = entry_edge(mask, box, w, h, hint)
    ex, ey = entry_point(mask, box, edge)
    if mask is None or not mask.any():
        x1, y1, x2, y2 = box
        opposite = {"left": (x2, (y1 + y2) / 2), "right": (x1, (y1 + y2) / 2),
                    "top": ((x1 + x2) / 2, y2), "bottom": ((x1 + x2) / 2, y1)}[edge]
        return float(opposite[0]), float(opposite[1]), edge
    ys, xs = np.nonzero(mask)
    d2 = (xs - ex) ** 2 + (ys - ey) ** 2
    k = max(3, int(0.002 * len(xs)))
    idx = np.argpartition(d2, -k)[-k:] if len(xs) > k else np.arange(len(xs))
    # +0.5: pixel centers
    return float(xs[idx].mean() + 0.5), float(ys[idx].mean() + 0.5), edge


# --------------------------------------------------------------------------- holds


@dataclass
class Hold:
    object_id: str
    records: list[dict[str, Any]] = field(default_factory=list)  # track_hands records, by frame
    fps: float = 10.0

    @property
    def first(self) -> int:
        return self.records[0]["frame"]

    @property
    def last(self) -> int:
        return self.records[-1]["frame"]

    @property
    def hold_s(self) -> float:
        return (self.last - self.first + 1) / self.fps

    @property
    def start_t(self) -> float:
        return self.first / self.fps

    @property
    def mean_conf(self) -> float:
        confs = [r["confidence"] for r in self.records if r.get("confidence") is not None]
        return float(np.mean(confs)) if confs else 0.0

    @property
    def middle(self) -> dict[str, Any]:
        return self.records[len(self.records) // 2]

    @property
    def uid(self) -> tuple[str, int]:
        return self.object_id, self.first


def _usable(r: dict[str, Any], w: int, h: int) -> bool:
    x1, y1, x2, y2 = r["box"]
    if (r.get("confidence") or 0.0) < P.POINT_MIN_CONF:
        return False
    return (x2 - x1) * (y2 - y1) >= P.POINT_MIN_BOX_FRAC * w * h


def find_holds(
    records: list[dict[str, Any]], *, fps: float, w: int, h: int, t0: float, t1: float
) -> list[Hold]:
    """Runs where one hand track is roughly still for >= POINT_MIN_HOLD_S within [t0, t1]."""
    lo, hi = math.ceil(t0 * fps - 1e-6), math.floor(t1 * fps + 1e-6)
    diag = math.hypot(w, h)
    by_obj: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        if lo <= r["frame"] <= hi and _usable(r, w, h):
            by_obj[r["object_id"]].append(r)
    holds: list[Hold] = []

    def flush(run: list[dict[str, Any]], oid: str) -> None:
        if len(run) >= P.POINT_MIN_HOLD_FRAMES:
            hold = Hold(oid, list(run), fps)
            if hold.hold_s + 1e-9 >= P.POINT_MIN_HOLD_S:
                holds.append(hold)

    for oid, rs in by_obj.items():
        rs.sort(key=lambda r: r["frame"])
        run: list[dict[str, Any]] = []
        sum_x = sum_y = 0.0
        for r in rs:
            cx, cy = center(r["box"])
            if run:
                px, py = center(run[-1]["box"])
                mx, my = sum_x / len(run), sum_y / len(run)
                still = (
                    r["frame"] - run[-1]["frame"] <= 1 + P.POINT_MAX_FRAME_GAP
                    and math.hypot(cx - px, cy - py) <= P.POINT_MAX_STEP_FRAC * diag
                    and math.hypot(cx - mx, cy - my) <= P.POINT_MAX_DRIFT_FRAC * diag
                )
                if not still:
                    flush(run, oid)
                    run, sum_x, sum_y = [], 0.0, 0.0
            run.append(r)
            sum_x += cx
            sum_y += cy
        flush(run, oid)
    return holds


def choose_holds(holds: list[Hold], n: int, mention_start_s: float, used: set[tuple[str, int]]) -> list[Hold]:
    """The n best unused holds (longest first, then closest to the mention start), in time order."""
    free = [h for h in holds if h.uid not in used]
    free.sort(key=lambda h: (-round(h.hold_s, 3), abs(h.start_t - mention_start_s), h.first))
    return sorted(free[:n], key=lambda h: h.first)


def entry_hint(hold: Hold, records: list[dict[str, Any]], w: int, h: int) -> str | None:
    """The border this hand track touched most recently up to the end of the hold (where it came in)."""
    own = sorted((r for r in records if r["object_id"] == hold.object_id and r["frame"] <= hold.last),
                 key=lambda r: -r["frame"])
    for r in own:
        edges = touching_edges(tuple(r["box"]), w, h)
        if len(edges) == 1:
            return edges[0]
        if edges:
            mask = rasterize_svg(r["svg"], w, h) if r.get("svg") else None
            return entry_edge(mask, tuple(r["box"]), w, h)
    return None


def hold_fingertip(
    hold: Hold, w: int, h: int, records: list[dict[str, Any]] | None = None
) -> tuple[float, float, str]:
    """Median fingertip over the hold's frames (robust to one bad mask). `records`: the whole
    track_hands record list, used to find the border the hand entered from."""
    hint = entry_hint(hold, records if records is not None else hold.records, w, h)
    tips = []
    edges = []
    for r in hold.records:
        mask = rasterize_svg(r.get("svg") or "", w, h) if r.get("svg") else None
        x, y, edge = fingertip(mask, tuple(r["box"]), w, h, hint)
        tips.append((x, y))
        edges.append(edge)
    xs, ys = zip(*tips, strict=True)
    edge = max(set(edges), key=edges.count)
    return float(np.median(xs)), float(np.median(ys)), edge


# --------------------------------------------------------------------------- stage


def classified_turns(ctx: StageContext) -> list[dict[str, Any]]:
    return ctx.output(Stage.classify_turns)["turns"]


def detect_events(
    mentions: list[Mention], records: list[dict[str, Any]], *, fps: float, w: int, h: int, duration_s: float
) -> tuple[list[tuple[Mention, Hold]], dict[str, int]]:
    """Pair mentions with holds. Returns [(mention, hold)] and {group: holds found in its window}."""
    groups: dict[str, list[Mention]] = defaultdict(list)
    for m in mentions:
        groups[m.group].append(m)
    used: set[tuple[str, int]] = set()
    pairs: list[tuple[Mention, Hold]] = []
    found: dict[str, int] = {}
    for gkey, ms in sorted(groups.items(), key=lambda kv: kv[1][0].start_s):
        t0 = max(0.0, ms[0].start_s - P.POINT_WINDOW_BEFORE_S)
        t1 = min(duration_s, ms[0].end_s + P.POINT_WINDOW_AFTER_S)
        holds = find_holds(records, fps=fps, w=w, h=h, t0=t0, t1=t1)
        found[gkey] = len(holds)
        chosen = choose_holds(holds, len(ms), ms[0].start_s, used)
        for m, hold in zip(sorted(ms, key=lambda m: m.index), chosen, strict=False):
            used.add(hold.uid)
            pairs.append((m, hold))
    return pairs, found


def run(ctx: StageContext) -> StageOutcome:
    meta = ctx.meta
    track = ctx.output(Stage.track_hands)
    w, h, fps = track["sam"]["width"], track["sam"]["height"], float(track["sam"]["fps"])
    sx, sy = meta.scale.sam_to_display_x, meta.scale.sam_to_display_y
    mentions = build_mentions(classified_turns(ctx))
    pairs, found = detect_events(mentions, track["records"], fps=fps, w=w, h=h, duration_s=meta.duration_s)

    events: list[dict[str, Any]] = []
    for m, hold in sorted(pairs, key=lambda p: (p[1].middle["frame"], p[0].key)):
        mid = hold.middle
        tx, ty, edge = hold_fingertip(hold, w, h, track["records"])
        x1, y1, x2, y2 = mid["box"]
        tx, ty = min(max(tx, x1), x2), min(max(ty, y1), y2)  # the median over the hold stays on the hand box
        events.append({
            "id": stable_id("pnt", ctx.walk_id, m.key),
            "number": len(events) + 1,
            "mention_key": m.key,
            "turn_id": m.turn_ids[0],
            "t": round(mid["frame"] / fps, 3),
            "frame": mid["frame"],
            "fingertip": [round(tx, 1), round(ty, 1)],
            "fingertip_display": [round(tx * sx, 1), round(ty * sy, 1)],
            "hand_box": [x1, y1, x2, y2],
            "hand_box_display": [round(x1 * sx, 1), round(y1 * sy, 1), round(x2 * sx, 1), round(y2 * sy, 1)],
            "hold_s": round(hold.hold_s, 3),
            "hold_frames": [hold.first, hold.last],
            "sam_object_ids": [hold.object_id],
            "mean_conf": round(hold.mean_conf, 4),
            "svg": mid.get("svg") or "",
            "entry_edge": edge,
        })
    by_key = {e["mention_key"]: e["id"] for e in events}
    ctx.save_output(Stage.find_points, {
        "version": 1,
        "fps": fps,
        "sam": {"width": w, "height": h},
        "events": events,
        "mentions": [{**m.to_dict(), "point_id": by_key.get(m.key)} for m in mentions],
        "candidates": found,
    })
    save_points(ctx, events)
    n_defects = sum(1 for m in mentions if m.kind == "defect")
    return StageOutcome(
        counts={"points": len(events), "mentions": len(mentions), "voice_only": len(mentions) - len(events)},
        message=f"Found {len(events)} pointing gestures for {n_defects} defects mentioned",
    )


def save_points(ctx: StageContext, events: list[dict[str, Any]]) -> None:
    with ctx.session() as s:
        existing = {p.id: p for p in s.exec(select(Point).where(Point.walk_id == ctx.walk_id)).all()}
        keep = set()
        for e in events:
            keep.add(e["id"])
            row = existing.get(e["id"]) or Point(id=e["id"], walk_id=ctx.walk_id, **_point_fields(e))
            for k, v in _point_fields(e).items():
                setattr(row, k, v)
            s.add(row)
        for pid, row in existing.items():
            if pid not in keep:
                s.delete(row)
        s.commit()


def _point_fields(e: dict[str, Any]) -> dict[str, Any]:
    x1, y1, x2, y2 = e["hand_box_display"]
    return {
        "number": e["number"],
        "t": e["t"],
        "frame": e["frame"],
        "fingertip_x": e["fingertip_display"][0],
        "fingertip_y": e["fingertip_display"][1],
        "hand_box": Box(x1=x1, y1=y1, x2=x2, y2=y2).model_dump(mode="json"),
        "hold_s": e["hold_s"],
        "sam_object_ids": e["sam_object_ids"],
        "mean_conf": e["mean_conf"],
        "turn_id": e["turn_id"],
    }
