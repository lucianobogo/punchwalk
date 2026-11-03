"""Stage `transcribe` (PLAN 1.1): Muse Voice Transcribe on audio.wav → Turn rows.

ENDPOINTING mode, keywords = base construction vocabulary + project keywords (`punchwalk.vocab`).
Writes one Turn row per ASR turn (idx, start_ms, end_ms, text; kind stays `other` until
`classify_turns`) and `stages/transcribe.json` with the full transcript and the keyword list.
Turn ids are stable (`trn_` + hash of walk id and index), so a re-run replaces rows in place.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlmodel import select

from punchwalk.clients import asr
from punchwalk.core.enums import Stage
from punchwalk.core.models import Turn
from punchwalk.pipeline.context import StageContext, StageOutcome, stable_id
from punchwalk.vocab import keywords_for


def transcribe_audio(wav: Path, keywords: list[str]) -> dict[str, Any]:
    """Run ASR (cached) and return the `stages/transcribe.json` document."""
    tr = asr.transcribe(wav, keywords=keywords, mode="ENDPOINTING")
    return {
        "version": 1,
        "model": "muse-voice-transcribe-1.0",
        "mode": "ENDPOINTING",
        "keywords": keywords,
        "transcript": tr.transcript,
        "audio_duration_ms": tr.audio_duration_ms,
        "turns": [
            {"idx": i, "start_ms": t.start_ms, "end_ms": t.end_ms, "text": t.text} for i, t in enumerate(tr.turns)
        ],
    }


def turn_id(walk_id: str, idx: int) -> str:
    return stable_id("trn", walk_id, "turn", idx)


def save_turns(ctx: StageContext, turns: list[dict[str, Any]]) -> list[Turn]:
    """Upsert Turn rows for this walk; drop rows that the new transcript no longer has."""
    rows: list[Turn] = []
    with ctx.session() as s:
        existing = {t.id: t for t in s.exec(select(Turn).where(Turn.walk_id == ctx.walk_id)).all()}
        keep: set[str] = set()
        for t in turns:
            tid = turn_id(ctx.walk_id, t["idx"])
            keep.add(tid)
            row = existing.get(tid) or Turn(id=tid, walk_id=ctx.walk_id, start_ms=0, end_ms=0, text="")
            row.idx, row.start_ms, row.end_ms, row.text = t["idx"], t["start_ms"], t["end_ms"], t["text"]
            s.add(row)
            rows.append(row)
        for tid, row in existing.items():
            if tid not in keep:
                s.delete(row)
        s.commit()
    return rows


def run(ctx: StageContext) -> StageOutcome:
    wav = ctx.walk_dir / ctx.meta.audio.path
    doc = transcribe_audio(wav, keywords_for(ctx.keywords))
    ctx.save_output(Stage.transcribe, doc)
    save_turns(ctx, doc["turns"])
    n = len(doc["turns"])
    return StageOutcome(counts={"lines": n}, message=f"Muse Voice Transcribe found {n} lines")
