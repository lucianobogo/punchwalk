"""Processing checklist rows (StageProgressOut) and the plain lines under each step.

The pipeline runner stores `Walk.stages[stage] = StageState` (status, timing, counts). This module
turns that into what the Record screen and "How this walk was processed" show, following the
wording of the mocks in fixtures/mock_api (DESIGN.md "Record").
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from punchwalk.api import schemas as S
from punchwalk.core.enums import (
    MODEL_ASR,
    MODEL_SAM,
    MODEL_SPARK_OPEN,
    STAGE_LABEL,
    STAGES_INITIAL,
    STAGES_REWALK,
    Stage,
    StageStatus,
    WalkKind,
    model_label,
)
from punchwalk.core.models import StageState, Walk
from punchwalk.db import as_utc

log = logging.getLogger("punchwalk.api")

SPARK = model_label(MODEL_SPARK_OPEN)
SAM = model_label(MODEL_SAM)
ASR = model_label(MODEL_ASR)

# The model each stage calls (null = computed in the app).
STAGE_MODEL: dict[Stage, str] = {
    Stage.transcribe: ASR,
    Stage.track_hands: SAM,
    Stage.classify_turns: SPARK,
    Stage.write_items: SPARK,
    Stage.ground_defects: SAM,
    Stage.verify: SPARK,
    Stage.resolve_closures: SPARK,
}

# Steps shown on "How this walk was processed" (docs/design/08-how-processed.png).
HEADLINE_STEPS_INITIAL: tuple[Stage, ...] = (
    Stage.transcribe,
    Stage.track_hands,
    Stage.fuse,
    Stage.write_items,
    Stage.ground_defects,
    Stage.verify,
)
HEADLINE_STEPS_REWALK: tuple[Stage, ...] = (
    Stage.transcribe,
    Stage.track_hands,
    Stage.classify_turns,
    Stage.resolve_closures,
)


def stage_order(kind: WalkKind) -> tuple[Stage, ...]:
    """Run order, as the runner executes it: `classify_turns` runs right after `transcribe`
    because `find_points` needs the defect windows (punchwalk/pipeline/runner.py)."""
    order = list(STAGES_REWALK if kind == WalkKind.rewalk else STAGES_INITIAL)
    has_both = Stage.classify_turns in order and Stage.find_points in order
    if has_both and order.index(Stage.classify_turns) > order.index(Stage.find_points):
        order.remove(Stage.classify_turns)
        order.insert(order.index(Stage.transcribe) + 1, Stage.classify_turns)
    return tuple(order)


def stage_states(walk: Walk) -> dict[Stage, StageState]:
    """Walk.stages parsed leniently: unknown keys are dropped, broken entries count as pending."""
    out: dict[Stage, StageState] = {}
    for key, raw in (walk.stages or {}).items():
        try:
            stage = Stage(key)
        except ValueError:
            continue
        if not isinstance(raw, dict):
            continue
        data = {k: v for k, v in raw.items() if k in StageState.model_fields}
        data["stage"] = stage
        try:
            out[stage] = StageState.model_validate(data)
        except ValidationError as exc:
            log.warning("walk %s: unreadable stage state %s: %s", walk.id, key, exc)
    return out


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _duration_words(seconds: float | None) -> str:
    if not seconds:
        return ""
    mins, secs = divmod(round(seconds), 60)
    return f"{mins} min {secs} s" if mins else f"{secs} s"


def _ratio(counts: dict[str, int]) -> float | None:
    total = counts.get("total")
    if not total:
        return None
    done = next((counts[k] for k in ("written", "checked", "done", "frames", "outlined") if k in counts), None)
    if done is None:
        return None
    return round(max(0.0, min(1.0, done / total)), 2)


def stage_detail(stage: Stage, state: StageState, walk: Walk) -> str | None:
    """The plain line under a checklist step, e.g. "Muse Voice Transcribe found 20 lines"."""
    c = state.counts or {}
    model = STAGE_MODEL.get(stage)
    if state.status == StageStatus.pending:
        return f"{model}, waiting" if model else None
    if state.status == StageStatus.failed:
        return state.error or "This step failed"
    if state.status == StageStatus.skipped:
        return "Skipped"
    if state.status == StageStatus.running:
        total = c.get("total")
        if stage in (Stage.write_items, Stage.verify) and total:
            key, verb = ("written", "written") if stage == Stage.write_items else ("checked", "checked")
            return f"{SPARK}, {c.get(key, 0)} of {total} {verb}"
        if stage == Stage.track_hands and total:
            return f"{SAM}, {c.get('frames', c.get('done', 0))} of {total} frames"
        return {
            Stage.ingest: "Joining clips and preparing the video",
            Stage.transcribe: f"{ASR}, listening",
            Stage.ground_defects: f"{SAM}, outlining each defect",
        }.get(stage, f"{model}, working" if model else "Working")

    # done
    if stage == Stage.ingest:
        n = c.get("clips", len(walk.clips or []))
        dur = _duration_words(walk.duration_s or sum(float(x.get("duration_s", 0)) for x in walk.clips or []))
        return f"{_plural(n, 'clip')}, {dur}" if dur else _plural(n, "clip")
    if stage == Stage.transcribe:
        return f"{ASR} found {_plural(c.get('lines', 0), 'line')}"
    if stage == Stage.track_hands:
        hands = c.get("hands", 0)
        if not hands:
            return f"{SAM} saw no hands"
        frames = c.get("frames")
        tail = f" across {frames} frames" if frames else ""
        return f"{SAM} followed {_plural(hands, 'hand')}{tail}"
    if stage == Stage.find_points:
        n = c.get("points", 0)
        line = f"{_plural(n, 'place')} you pointed" if n else "No pointing found"
        if c.get("voice_only"):
            line += f", {c['voice_only']} described without pointing"
        return line
    if stage == Stage.classify_turns:
        if "status_updates" in c and walk.kind == WalkKind.rewalk:
            return f"{SPARK} found {_plural(c['status_updates'], 'status update')}"
        return f"{SPARK} sorted {_plural(c.get('lines', 0), 'line')}"
    if stage == Stage.fuse:
        if walk.kind == WalkKind.rewalk and "status_updates" in c:
            return _plural(c["status_updates"], "status update")
        parts = []
        if c.get("pointed"):
            parts.append(f"{c['pointed']} matched to where you pointed")
        if c.get("voice_only"):
            parts.append(f"{c['voice_only']} described without pointing")
        return ", ".join(parts) if parts else "Nothing to match"
    if stage == Stage.pick_evidence:
        return f"{_plural(c.get('photos', 0), 'photo')} picked"
    if stage == Stage.write_items:
        return f"{SPARK} wrote {_plural(c.get('written', c.get('total', 0)), 'item')}"
    if stage == Stage.ground_defects:
        return f"{SAM} outlined {_plural(c.get('outlined', 0), 'defect')}"
    if stage == Stage.verify:
        checked = c.get("checked", c.get("total", 0))
        matched = c.get("matched", checked)
        flagged = max(0, checked - matched)
        line = f"{matched} match the photo and what was said"
        if flagged:
            line += f", {flagged} {'needs' if flagged == 1 else 'need'} a look"
        return line
    if stage == Stage.resolve_closures:
        so, op, nr = c.get("signed_off", 0), c.get("still_open", 0), c.get("needs_review", 0)
        return f"{so} signed off, {op} still open, {nr} {'needs' if nr == 1 else 'need'} a look"
    return "Saved"


def stage_rows(walk: Walk) -> list[S.StageProgressOut]:
    states = stage_states(walk)
    rows = []
    for stage in stage_order(walk.kind):
        state = states.get(stage) or StageState(stage=stage)
        running = state.status == StageStatus.running
        rows.append(
            S.StageProgressOut(
                stage=stage,
                label=STAGE_LABEL[stage],
                status=state.status,
                started_at=as_utc(state.started_at),
                finished_at=as_utc(state.finished_at),
                duration_ms=state.duration_ms,
                counts=dict(state.counts or {}),
                progress=_ratio(state.counts or {}) if running else None,
                detail=state.message or stage_detail(stage, state, walk),
                model_label=STAGE_MODEL.get(stage),
                message=state.message,
            )
        )
    return rows


def overall_progress(rows: list[S.StageProgressOut]) -> float:
    """0..1: each stage weighs the same; a running stage counts its own fraction (or half)."""
    if not rows:
        return 0.0
    score = 0.0
    for row in rows:
        if row.status in (StageStatus.done, StageStatus.skipped):
            score += 1.0
        elif row.status == StageStatus.running:
            score += row.progress if row.progress is not None else 0.5
    return round(min(1.0, score / len(rows)), 2)


def current_stage(rows: list[S.StageProgressOut], job_progress: dict[str, Any] | None) -> Stage | None:
    """The stage running now, else the one that failed, else the job's last reported stage."""
    for status in (StageStatus.running, StageStatus.failed):
        hit = next((r.stage for r in rows if r.status == status), None)
        if hit is not None:
            return hit
    raw = (job_progress or {}).get("stage")
    try:
        return Stage(raw) if raw else None
    except ValueError:
        return None
