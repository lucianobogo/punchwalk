"""The ground_defects line of "How this walk was processed" (GET /api/walks/{id}/processing): built from the
stage counts, names the Meta models plainly, tells grounding v2 from v1, and never crashes on old state."""

from __future__ import annotations

import pytest

from punchwalk.api import schemas as S
from punchwalk.api.views import _step_detail, ground_detail
from punchwalk.core.enums import Stage, StageStatus
from punchwalk.pipeline.ground import ground_message

# wlk_demo_walk1 / wlk_demo_walk3 after `reground --refresh-display` (data/punchwalk.sqlite3, Nov 4)
WALK1 = {"outlined": 7, "total": 12, "concept": 1, "box": 4, "spark_box": 2, "pointed_only": 4, "none": 1}
WALK3 = {"outlined": 1, "total": 2, "concept": 0, "box": 1, "spark_box": 0, "pointed_only": 1, "none": 0}
V1 = {"outlined": 11, "total": 12, "concept": 6, "box": 5, "spark_box": 0, "pointed_only": 1, "none": 0}


def test_v2_walk1_counts_and_message() -> None:
    line = ground_detail(WALK1, ground_message(True, WALK1, picked=3))
    assert line == (
        "Muse Spark Open located each defect; SAM 3.1 outlined 5 of 12 and Muse Spark Open boxed 2 more, "
        "each checked by Muse Spark Open. A clearer photo was picked for 3."
    )


def test_v2_sam_only_with_one_pick() -> None:
    line = ground_detail(WALK3, ground_message(True, WALK3, picked=1))
    assert line == (
        "Muse Spark Open located each defect; SAM 3.1 outlined 1 of 2, each checked by Muse Spark Open. "
        "A clearer photo was picked for 1."
    )


def test_v2_without_picks_is_one_sentence() -> None:
    assert ground_detail(WALK3, ground_message(True, WALK3)) == (
        "Muse Spark Open located each defect; SAM 3.1 outlined 1 of 2, each checked by Muse Spark Open."
    )


def test_v2_spark_boxes_only_and_nothing_outlined() -> None:
    spark_only = {"outlined": 2, "total": 3, "concept": 0, "box": 0, "spark_box": 2, "pointed_only": 1, "none": 0}
    assert ground_detail(spark_only) == (  # spark_box > 0 is v2 even without the message
        "Muse Spark Open located each defect and boxed 2 of 3, each checked by Muse Spark Open."
    )
    none = {"outlined": 0, "total": 2, "concept": 0, "box": 0, "spark_box": 0, "pointed_only": 2, "none": 0}
    assert ground_detail(none, ground_message(True, none)) == (
        "Muse Spark Open looked for each defect; none of 2 could be outlined."
    )
    empty = {"outlined": 0, "total": 0}
    assert ground_detail(empty, ground_message(True, empty)) == "No defects to outline in this walk."


def test_counts_win_over_the_message_when_present() -> None:
    c = {**WALK3, "v2": 1, "picked": 2}
    assert ground_detail(c, "").endswith("A clearer photo was picked for 2.")
    assert ground_detail({**WALK1, "v2": 0}, "").startswith("Muse Spark Open names what to outline")


def test_v1_counts() -> None:
    line = ground_detail(V1, ground_message(False, V1))
    assert line == "Muse Spark Open names what to outline near your fingertip; SAM 3.1 draws it. 11 of 12 outlined."
    assert ground_detail({"outlined": 4}) == (
        "Muse Spark Open names what to outline near your fingertip; SAM 3.1 draws it. 4 outlined."
    )


@pytest.mark.parametrize("counts", [{}, {"done": 3}, {"outlined": 0, "total": 0, "concept": 0}])
@pytest.mark.parametrize("message", ["", None, "SAM 3.1 outlined 0 of 0 defects", "something else entirely"])
def test_old_or_missing_counts_never_crash(counts: dict, message: str | None) -> None:
    line = ground_detail(counts, message or "")
    assert "Muse Spark Open" in line or line == "No defects to outline in this walk."
    assert not line.startswith(("Spark Open", "SAM draws"))


def _row(counts: dict | None, message: str | None, status: StageStatus = StageStatus.done) -> S.StageProgressOut:
    return S.StageProgressOut(
        stage=Stage.ground_defects, label="Outline", status=status, started_at=None, finished_at=None,
        duration_ms=1, counts=counts or {}, progress=None, detail=message, model_label="SAM 3.1", message=message,
    )  # fmt: skip


def test_step_detail_uses_the_stage_counts() -> None:
    msg = ground_message(True, WALK1, picked=3)
    assert _step_detail(None, Stage.ground_defects, _row(WALK1, msg)).startswith(  # type: ignore[arg-type]
        "Muse Spark Open located each defect; SAM 3.1 outlined 5 of 12"
    )
    old = _step_detail(None, Stage.ground_defects, _row(None, None))  # type: ignore[arg-type]
    assert old == "Muse Spark Open names what to outline near your fingertip; SAM 3.1 draws it."
    running = _row({"outlined": 2, "total": 5}, "SAM 3.1, 2 of 5", StageStatus.running)
    assert _step_detail(None, Stage.ground_defects, running) == "SAM 3.1, 2 of 5"  # type: ignore[arg-type]
