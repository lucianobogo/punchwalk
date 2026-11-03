"""Re-walk pieces without media or models: decision table (7.3), human-change guard, frame alignment,
outline transfer (7.2), before/after render (7.4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import cv2
import numpy as np
import pytest
from PIL import Image

from punchwalk.core.enums import (
    REVIEW_FALSE_FIX,
    ClosureDecision,
    HistoryAction,
    HistoryActor,
    ItemStatus,
    VisionStatus,
)
from punchwalk.core.models import Item
from punchwalk.pipeline.closures import (
    REVIEW_FIX_UNCLEAR,
    REVIEW_LOOKS_FIXED,
    StatusMatch,
    align_frames,
    decide,
    human_changed_after,
    transform_box,
    transform_svg,
)
from punchwalk.pipeline.items import add_history
from punchwalk.render.before_after import DIVIDER_PX, PANEL_H, PANEL_W, render_before_after

V = VisionStatus


@pytest.mark.parametrize(
    ("claimed", "vision", "decision", "status"),
    [
        ("fixed", V.fixed, ClosureDecision.signed_off, ItemStatus.closed),
        ("fixed", V.still_visible, ClosureDecision.needs_review, ItemStatus.needs_review),
        ("fixed", V.unclear, ClosureDecision.needs_review, ItemStatus.needs_review),
        ("not_fixed", V.fixed, ClosureDecision.still_open, None),
        ("not_fixed", V.still_visible, ClosureDecision.still_open, None),
        ("not_fixed", V.not_checked, ClosureDecision.still_open, None),
        ("unclear", V.still_visible, ClosureDecision.still_open, None),
        ("unclear", V.unclear, ClosureDecision.still_open, None),
        ("unclear", V.fixed, ClosureDecision.needs_review, ItemStatus.needs_review),
    ],
)
def test_decision_table(claimed, vision, decision, status):
    d, s, note = decide(claimed, vision)
    assert (d, s) == (decision, status)
    if (claimed, vision) == ("fixed", V.still_visible):
        assert note == REVIEW_FALSE_FIX == "Marked fixed on the re-walk, but the defect is still visible."
    if (claimed, vision) == ("fixed", V.unclear):
        assert note == REVIEW_FIX_UNCLEAR
    if (claimed, vision) == ("unclear", V.fixed):
        assert note == REVIEW_LOOKS_FIXED


def test_human_changed_after_decision():
    decided = datetime.now(UTC) - timedelta(hours=1)
    item = Item(project_id="p", number=1, origin_walk_id="w", title="t")
    add_history(item, HistoryActor.human, HistoryAction.status_changed)  # now: after `decided`
    assert human_changed_after(item, decided.isoformat())
    assert not human_changed_after(item, (datetime.now(UTC) + timedelta(hours=1)).isoformat())
    assert not human_changed_after(item, None)  # never decided: nothing to protect
    model_only = Item(project_id="p", number=2, origin_walk_id="w", title="t")
    add_history(model_only, HistoryActor.model, HistoryAction.flagged)
    assert not human_changed_after(model_only, decided)


def test_match_schema_is_flat():
    from punchwalk.clients import llm

    schema = llm.strict_schema(StatusMatch)
    assert schema["properties"]["item_number"]["type"] == ["integer", "null"]
    assert set(schema["required"]) == {"item_number", "claimed", "confidence", "rationale"}


def _scene(shift=(0, 0)) -> np.ndarray:
    rng = np.random.default_rng(3)
    base = cv2.GaussianBlur((rng.random((640, 360)) * 255).astype(np.uint8), (0, 0), 2)
    img = cv2.cvtColor(base, cv2.COLOR_GRAY2BGR)
    M = np.float32([[1, 0, shift[0]], [0, 1, shift[1]]])
    return cv2.warpAffine(img, M, (360, 640), borderMode=cv2.BORDER_REFLECT)


def test_align_frames_and_transfer_outline(tmp_path):
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    cv2.imwrite(str(a), _scene(), [cv2.IMWRITE_JPEG_QUALITY, 95])
    cv2.imwrite(str(b), _scene((14, -9)), [cv2.IMWRITE_JPEG_QUALITY, 95])
    M = align_frames(a, b)
    assert M is not None and M[0, 2] == pytest.approx(14, abs=1.5) and M[1, 2] == pytest.approx(-9, abs=1.5)
    assert transform_svg("M10 10 L20 10 L20 20 Z", np.float64([[1, 0, 5], [0, 1, -2]])) == \
        "M15.0 8.0 L25.0 8.0 L25.0 18.0 Z"
    assert transform_box([10, 10, 20, 20], np.float64([[1, 0, 5], [0, 1, -2]])) == [15, 8, 25, 18]
    flat = tmp_path / "flat.jpg"
    cv2.imwrite(str(flat), np.full((640, 360, 3), 128, np.uint8))
    assert align_frames(flat, flat) is None  # nothing to line up on


def test_before_after_render(tmp_path):
    before, after = tmp_path / "b.jpg", tmp_path / "a.jpg"
    Image.new("RGB", (432, 324), (200, 30, 30)).save(before)
    Image.new("RGB", (640, 480), (30, 30, 200)).save(after)
    out = render_before_after(before, after, tmp_path / "ba.jpg")
    img = np.asarray(Image.open(out).convert("RGB")).astype(int)
    assert img.shape == (PANEL_H, PANEL_W * 2 + DIVIDER_PX, 3)
    assert img[300, 100][0] > 150 and img[300, PANEL_W + DIVIDER_PX + 100][2] > 150  # before left, after right
    assert img[300, PANEL_W + DIVIDER_PX // 2].min() > 230  # white divider, no text
