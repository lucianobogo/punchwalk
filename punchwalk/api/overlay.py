"""`GET /api/walks/{id}/overlay` (PLAN 4.3): hand outlines per SAM frame, in display pixels.

Sources:
- `stages/track_hands.json` (Track A, punchwalk/pipeline/track.py): every SAM hand record
  `{frame, object_id "a:3", box [x1,y1,x2,y2], confidence, svg}` in SAM-frame pixels.
- `stages/find_points.json` (punchwalk/pipeline/points.py): each pointing event's `hold_frames`.
- `meta.json` (IngestMeta): `scale.sam_to_display_x/y`, display size, SAM fps.
- Point rows (id, number, frame, hold_s, sam_object_ids, fingertip) and Item rows (`point_id`, number).

A hand is listed around each pointing event (the point's SAM object, from `AROUND_HOLD_S` before
the hold to `AROUND_HOLD_S` after it) with the point id and the item's number (for the tab); the
fingertip is set only while the hand holds still. Hands that never point aren't listed.

Paths are scaled to display pixels and simplified with Ramer-Douglas-Peucker; if the JSON is still
over `MAX_PAYLOAD_BYTES` the tolerance doubles (up to 3 times). The result is cached at
`DATA_DIR/walks/<id>/overlay.json` (+ `overlay.key`, a fingerprint of every input) and rebuilt when
any input changes.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import math
import os
import re
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from punchwalk.api import schemas as S
from punchwalk.api.errors import ApiError
from punchwalk.api.views import (
    checked_by_rewalk,
    lenient,
    live_items,
    point_items,
    read_ingest_meta,
    walk_points,
)
from punchwalk.config import get_settings
from punchwalk.core.enums import WalkKind, WalkStatus
from punchwalk.core.models import Closure, Item, Point, Turn, Walk

log = logging.getLogger("punchwalk.api")

OVERLAY_FPS = 10.0
MAX_PAYLOAD_BYTES = 3_000_000
AROUND_HOLD_S = 1.5  # show the hand coming in to point and leaving again
BASE_EPSILON_FRAC = 0.003  # RDP tolerance as a fraction of the long display side (~6 px at 1920)
OVERLAY_NAME = "overlay.json"
KEY_NAME = "overlay.key"
OVERLAY_VERSION = 2  # bump when the builder changes, so cached overlays are rebuilt

_CMD = re.compile(r"[MmLlZzHhVv]|-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")

Pt = tuple[float, float]


# =========================================================================== geometry


def parse_svg_path(d: str) -> list[list[Pt]]:
    """M/L/H/V/Z path (absolute or relative) -> polygons. Curves aren't produced upstream."""
    polys: list[list[Pt]] = []
    cur: list[Pt] = []
    x = y = 0.0
    cmd = "M"
    nums: list[float] = []

    def flush_nums() -> None:
        nonlocal x, y, cmd, cur
        i = 0
        while i < len(nums):
            if cmd in "MLml":
                if i + 1 >= len(nums):
                    break
                dx, dy = nums[i], nums[i + 1]
                i += 2
                if cmd.islower():
                    x, y = x + dx, y + dy
                else:
                    x, y = dx, dy
                if cmd in "Mm" and cur:
                    polys.append(cur)
                    cur = []
                cur.append((x, y))
                if cmd == "M":
                    cmd = "L"
                elif cmd == "m":
                    cmd = "l"
            elif cmd in "Hh":
                x = x + nums[i] if cmd == "h" else nums[i]
                i += 1
                cur.append((x, y))
            elif cmd in "Vv":
                y = y + nums[i] if cmd == "v" else nums[i]
                i += 1
                cur.append((x, y))
            else:
                i += 1
        nums.clear()

    for tok in _CMD.findall(d or ""):
        if tok[0].isalpha():
            flush_nums()
            if tok in "Zz":
                if cur:
                    polys.append(cur)
                    x, y = cur[0]
                    cur = []
                continue
            cmd = tok
        else:
            nums.append(float(tok))
    flush_nums()
    if cur:
        polys.append(cur)
    return [p for p in polys if len(p) >= 3]


def _perp_dist(p: Pt, a: Pt, b: Pt) -> float:
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    return abs(dy * px - dx * py + bx * ay - by * ax) / math.hypot(dx, dy)


def rdp(points: list[Pt], epsilon: float) -> list[Pt]:
    """Ramer-Douglas-Peucker on an open polyline (iterative)."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        lo, hi = stack.pop()
        best, idx = 0.0, -1
        for i in range(lo + 1, hi):
            d = _perp_dist(points[i], points[lo], points[hi])
            if d > best:
                best, idx = d, i
        if idx != -1 and best > epsilon:
            keep[idx] = True
            stack.append((lo, idx))
            stack.append((idx, hi))
    return [p for p, k in zip(points, keep, strict=True) if k]


def simplify_polygon(poly: list[Pt], epsilon: float) -> list[Pt]:
    """RDP on a closed ring: split at the point farthest from the first, simplify both halves."""
    if len(poly) <= 4:
        return poly
    far = max(range(len(poly)), key=lambda i: math.hypot(poly[i][0] - poly[0][0], poly[i][1] - poly[0][1]))
    a = rdp(poly[: far + 1], epsilon)
    b = rdp([*poly[far:], poly[0]], epsilon)
    out = a[:-1] + b[:-1]
    return out if len(out) >= 3 else poly


def svg_from_polys(polys: list[list[Pt]]) -> str:
    parts = []
    for poly in polys:
        pts = " ".join(f"L{round(x)} {round(y)}" for x, y in poly[1:])
        parts.append(f"M{round(poly[0][0])} {round(poly[0][1])} {pts} Z")
    return " ".join(parts)


def scale_simplify_svg(d: str, sx: float, sy: float, epsilon: float) -> str:
    polys = []
    for poly in parse_svg_path(d):
        scaled = [(x * sx, y * sy) for x, y in poly]
        simple = simplify_polygon(scaled, epsilon)
        if len(simple) >= 3:
            polys.append(simple)
    return svg_from_polys(polys)


def box_path(box: S.BoxOut) -> str:
    x1, y1, x2, y2 = (round(v) for v in (box.x1, box.y1, box.x2, box.y2))
    return f"M{x1} {y1} L{x2} {y1} L{x2} {y2} L{x1} {y2} Z"


def _box4(raw: Any) -> tuple[float, float, float, float] | None:
    if isinstance(raw, dict):
        raw = [raw.get(k) for k in ("x1", "y1", "x2", "y2")]
    if isinstance(raw, list | tuple) and len(raw) == 4 and all(isinstance(v, int | float) for v in raw):
        return float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3])
    return None


# =========================================================================== inputs


def walk_dir(walk_id: str) -> Path:
    return Path(get_settings().DATA_DIR) / "walks" / walk_id


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _stat(path: Path) -> str:
    try:
        st = path.stat()
        return f"{st.st_size}:{st.st_mtime_ns}"
    except OSError:
        return "-"


def _events(session: Session, walk: Walk, points: list[Point], by_point: dict[str, Item]) -> list[S.OverlayEventOut]:
    """The second each item was called out in this walk."""
    events: list[S.OverlayEventOut] = []
    point_t = {p.id: p.t for p in points}
    if walk.kind == WalkKind.rewalk:
        turns = {t.id: t for t in session.exec(select(Turn).where(Turn.walk_id == walk.id)).all()}
        for it in checked_by_rewalk(session, walk):
            cl = lenient(Closure, it.closure)
            if cl is None:
                continue
            turn = turns.get(cl.rewalk_turn_id or "")
            t = turn.start_ms / 1000 if turn else cl.after_t
            if t is not None:
                events.append(S.OverlayEventOut(t=round(t, 2), item_id=it.id, number=it.number))
    else:
        point_of = {it.id: pid for pid, it in by_point.items()}
        for it in live_items(session, Item.origin_walk_id == walk.id):
            if it.quote_start_s is not None:
                t = it.quote_start_s
            elif it.id in point_of:
                t = point_t[point_of[it.id]]
            elif isinstance(it.evidence, dict) and isinstance(it.evidence.get("t"), int | float):
                t = float(it.evidence["t"])
            else:
                t = it.video_start_s
            events.append(S.OverlayEventOut(t=round(t, 2), item_id=it.id, number=it.number))
    return sorted(events, key=lambda e: (e.t, e.number))


def fingerprint(session: Session, walk: Walk) -> str:
    wdir = walk_dir(walk.id)
    points = walk_points(session, walk.id)
    items = live_items(session, Item.project_id == walk.project_id)
    parts: list[Any] = [
        OVERLAY_VERSION,
        _stat(wdir / "stages" / "track_hands.json"),
        _stat(wdir / "stages" / "find_points.json"),
        _stat(wdir / "meta.json"),
        walk.kind,
        walk.width,
        walk.height,
        sorted(
            (p.id, p.number, p.frame, p.hold_s, list(p.sam_object_ids or []), p.fingertip_x, p.fingertip_y)
            for p in points
        ),
        sorted(
            (
                i.id,
                i.number,
                i.point_id,
                i.origin_walk_id,
                i.quote_start_s,
                i.video_start_s,
                json.dumps(i.closure, sort_keys=True, default=str) if i.closure else None,
            )
            for i in items
            if i.origin_walk_id == walk.id or (isinstance(i.closure, dict) and i.closure.get("rewalk_id") == walk.id)
        ),
    ]
    return hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()


# =========================================================================== build


def build_overlay(session: Session, walk: Walk) -> S.OverlayOut:
    wdir = walk_dir(walk.id)
    meta = read_ingest_meta(walk) or {}
    display = meta.get("display") or {}
    width = int(walk.width or display.get("width") or 0)
    height = int(walk.height or display.get("height") or 0)
    if not width or not height:
        raise ApiError(409, "The overlay is ready once the video has been prepared.")
    duration = float(walk.duration_s or meta.get("duration_s") or 0.0)
    track = _read_json(wdir / "stages" / "track_hands.json") or {}
    sam = track.get("sam") or meta.get("sam") or {}
    sam_fps = float(sam.get("fps") or (meta.get("sam") or {}).get("fps") or OVERLAY_FPS)
    scale = meta.get("scale") or {}
    sam_w, sam_h = sam.get("width"), sam.get("height")
    sx = float(scale.get("sam_to_display_x") or (width / sam_w if sam_w else 1.0))
    sy = float(scale.get("sam_to_display_y") or (height / sam_h if sam_h else 1.0))

    points = walk_points(session, walk.id)
    by_point = point_items(session, walk, points)
    events = _events(session, walk, points, by_point)

    windows = hold_windows(wdir, points, sam_fps)
    records = [r for r in track.get("records") or [] if isinstance(r, dict) and isinstance(r.get("frame"), int)]
    records.sort(key=lambda r: (r["frame"], str(r.get("object_id"))))

    epsilon = BASE_EPSILON_FRAC * max(width, height)
    overlay = None
    for _attempt in range(4):
        frames = _frames(records, windows, by_point, sam_fps, sx, sy, epsilon)
        overlay = S.OverlayOut(
            walk_id=walk.id,
            fps=sam_fps,
            width=width,
            height=height,
            duration_s=duration,
            frames=frames,
            events=events,
        )
        size = len(overlay.model_dump_json())
        if size <= MAX_PAYLOAD_BYTES:
            break
        log.info("overlay for %s is %d bytes; simplifying more", walk.id, size)
        epsilon *= 2
    assert overlay is not None
    return overlay


class HoldWindow:
    """One point's hold (SAM frames, inclusive) and the wider window its hand is shown in."""

    def __init__(self, point: Point, hold_lo: int, hold_hi: int, around: int) -> None:
        self.point = point
        self.hold_lo, self.hold_hi = hold_lo, hold_hi
        self.lo, self.hi = hold_lo - around, hold_hi + around
        self.object_ids = set(point.sam_object_ids or [])
        hb = _box4(point.hand_box)
        self.hand_center = ((hb[0] + hb[2]) / 2, (hb[1] + hb[3]) / 2) if hb else None


def hold_windows(wdir: Path, points: list[Point], sam_fps: float) -> list[HoldWindow]:
    """Exact hold frames from find_points.json; else `hold_s` centered on the point's frame."""
    exact: dict[str, tuple[int, int]] = {}
    for e in (_read_json(wdir / "stages" / "find_points.json") or {}).get("events") or []:
        hf = e.get("hold_frames") if isinstance(e, dict) else None
        if e.get("id") and isinstance(hf, list) and len(hf) == 2:
            exact[e["id"]] = (int(hf[0]), int(hf[1]))
    around = round(AROUND_HOLD_S * sam_fps)
    out = []
    for p in points:
        half = (p.hold_s or 0.0) * sam_fps / 2
        lo, hi = exact.get(p.id) or (math.floor(p.frame - half), math.ceil(p.frame + half))
        out.append(HoldWindow(p, lo, hi, around))
    return out


def _frames(
    records: list[dict[str, Any]],
    windows: list[HoldWindow],
    by_point: dict[str, Item],
    sam_fps: float,
    sx: float,
    sy: float,
    epsilon: float,
) -> list[S.OverlayFrameOut]:
    """Hands around each pointing event; the fingertip only while the hand holds still."""
    frames: list[S.OverlayFrameOut] = []
    current_frame: int | None = None
    for r in records:
        frame, oid = r["frame"], str(r.get("object_id"))
        win = next((w for w in windows if w.lo <= frame <= w.hi and oid in w.object_ids), None)
        b = _box4(r.get("box"))
        if win is None or b is None:
            continue
        box = S.BoxOut(
            x1=round(b[0] * sx, 1), y1=round(b[1] * sy, 1), x2=round(b[2] * sx, 1), y2=round(b[3] * sy, 1)
        )
        fingertip = None
        if win.hold_lo <= frame <= win.hold_hi:
            # The fingertip is measured once per hold; follow the hand's small drift within it.
            dx = dy = 0.0
            if win.hand_center is not None:
                dx = (box.x1 + box.x2) / 2 - win.hand_center[0]
                dy = (box.y1 + box.y2) / 2 - win.hand_center[1]
            fingertip = S.XYOut(x=round(win.point.fingertip_x + dx, 1), y=round(win.point.fingertip_y + dy, 1))
        item = by_point.get(win.point.id)
        hand = S.OverlayHandOut(
            point_id=win.point.id,
            number=item.number if item else None,
            box=box,
            svg=scale_simplify_svg(str(r.get("svg") or ""), sx, sy, epsilon) or box_path(box),
            fingertip=fingertip,
        )
        if frame != current_frame:
            current_frame = frame
            frames.append(S.OverlayFrameOut(t=round(frame / sam_fps, 2), hands=[]))
        frames[-1].hands.append(hand)
    return frames


# =========================================================================== cache


def invalidate_overlay(walk_id: str) -> None:
    for name in (OVERLAY_NAME, KEY_NAME):
        with contextlib.suppress(OSError):
            (walk_dir(walk_id) / name).unlink()


def get_overlay(session: Session, walk: Walk) -> S.OverlayOut:
    """Cached overlay for ready walks (rebuilt when any input changes); built fresh otherwise."""
    if walk.status != WalkStatus.ready:
        return build_overlay(session, walk)
    wdir = walk_dir(walk.id)
    key = fingerprint(session, walk)
    cache, key_file = wdir / OVERLAY_NAME, wdir / KEY_NAME
    try:
        if key_file.read_text().strip() == key:
            return S.OverlayOut.model_validate_json(cache.read_bytes())
    except (OSError, ValueError):
        pass
    overlay = build_overlay(session, walk)
    try:
        wdir.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".json.tmp")
        tmp.write_text(overlay.model_dump_json())
        os.replace(tmp, cache)
        key_file.write_text(key)
    except OSError:
        log.warning("could not cache the overlay for %s", walk.id)
    return overlay
