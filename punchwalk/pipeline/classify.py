"""Stage `classify_turns` (PLAN 1.4): one batched Muse Spark Open call labels every turn.

Per turn: kind (location | defect | status_update | other), room (if mentioned), defects[] (a
short description each), continues_previous (a description that runs over from the previous
turn), and for re-walk status updates {status_target, claimed: fixed | not_fixed | unclear}.
More than CLASSIFY_CHUNK_TURNS turns are sent in chunks (each chunk is told the room in effect).

A room state machine then carries the last-mentioned room forward. Rooms are normalized to the
project's room list when one matches. Turn rows get kind/room/defects/claimed; the rest
(continues_previous, status_target) is in `stages/classify_turns.json`.

If Spark Open (and its Meta fallback) fails, simple keyword rules label the turns instead and the
stage message says so: a walk never fails on classification.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlmodel import select

from punchwalk.clients import llm
from punchwalk.clients.base import ApiError
from punchwalk.core.enums import ClaimedStatus, Stage, TurnKind, WalkKind
from punchwalk.core.models import Turn
from punchwalk.pipeline import params as P
from punchwalk.pipeline.context import StageContext, StageOutcome
from punchwalk.vocab import ROOMS_AND_UNITS

log = logging.getLogger(__name__)


class TurnLabel(BaseModel):
    idx: int = Field(description="the turn's idx, copied from the input")
    kind: Literal["location", "defect", "status_update", "other"]
    room: str | None = Field(description="room named in this turn, else null")
    continues_previous: bool = Field(description="true if this turn continues the previous turn's defect description")
    defects: list[str] = Field(description="one short description per defect called out in this turn")
    status_target: str | None = Field(description="status_update only: which earlier item it refers to")
    claimed: Literal["fixed", "not_fixed", "unclear"] | None = Field(description="status_update only")


class TurnLabels(BaseModel):
    turns: list[TurnLabel]
    rationale: str = Field(description="one sentence, at most 200 characters")


SYSTEM = """You label the transcript of a construction punch walk.
An inspector walks a unit, points at each defect with a finger, and describes it out loud.
For every turn (one transcribed utterance) return one label with the same idx:
- kind: "location" if the turn only names where the inspector is ("Kitchen.", "Moving into the hallway");
  "defect" if it describes something that needs fixing (it may also name the room);
  "status_update" if it reports on an earlier item ("kitchen knob, fixed", "scuffs still there");
  "other" for anything else (filler, comments, "okay", "next").
- room: the room this turn names, using the project's room names when one fits; null if it names none.
- continues_previous: true only when this turn finishes a defect description started in the previous turn
  (for example "Upper cabinet left of the sink," then "knob missing."). Then put the defect on this turn too,
  described in full.
- defects: for kind "defect", one short punch-list description per distinct defect, in the order spoken
  (for example ["upper cabinet left of sink: knob missing"]). Use the inspector's facts only; never add
  quantities or causes. Empty list for other kinds.
- status_target and claimed: only for "status_update": what item it is about (short), and whether the
  inspector says it is "fixed", "not_fixed" (still there, not done) or "unclear". Null otherwise.
Keep rationale to one short sentence."""


def _user_prompt(chunk: list[dict[str, Any]], rooms: list[str], kind: WalkKind, room_before: str | None) -> str:
    lines = [
        f"Walk type: {'re-walk (checking earlier items)' if kind == WalkKind.rewalk else 'first walk'}.",
        f"Project rooms: {', '.join(rooms) if rooms else 'not listed'}.",
    ]
    if room_before:
        lines.append(f"Room in effect before these turns: {room_before}.")
    turns = [{"idx": t["idx"], "start_s": round(t["start_ms"] / 1000, 1), "text": t["text"]} for t in chunk]
    lines.append("Turns:\n" + json.dumps(turns, ensure_ascii=False))
    return "\n".join(lines)


# --------------------------------------------------------------------------- rooms


def normalize_room(room: str | None, rooms: list[str]) -> str | None:
    """Map a free-text room to the project's room list (case-insensitive, substring), else title case."""
    if not room or not room.strip():
        return None
    clean = " ".join(room.strip().split())
    low = clean.lower()
    for r in rooms:
        if r.lower() == low:
            return r
    for r in sorted(rooms, key=len, reverse=True):
        if r.lower() in low or low in r.lower():
            return r
    return clean[:1].upper() + clean[1:]


def carry_rooms(labels: list[dict[str, Any]]) -> None:
    """Room state machine: a turn without a room takes the last room mentioned before it."""
    current: str | None = None
    for lab in sorted(labels, key=lambda r: r["idx"]):
        if lab.get("room"):
            current = lab["room"]
            lab["room_mentioned"] = True
        else:
            lab["room"] = current
            lab["room_mentioned"] = False


# --------------------------------------------------------------------------- fallback rules

_STATUS_FIXED = re.compile(r"\b(fixed|done|repaired|replaced|good now|looks good|complete[d]?|resolved)\b", re.I)
_STATUS_NOT = re.compile(r"\b(still|not fixed|not done|remains|again|same as before)\b", re.I)
_DEFECT_WORDS = re.compile(
    r"\b(missing|loose|crooked|scuff\w*|crack\w*|chip\w*|stain\w*|smudge\w*|broken|damaged|gap|"
    r"debris|dirty|drip\w*|won't|doesn't|not working|out|needs?|ding\w*|dent\w*|splatter\w*)\b",
    re.I,
)


def fallback_labels(turns: list[dict[str, Any]], rooms: list[str], kind: WalkKind) -> list[dict[str, Any]]:
    """Keyword rules used when the model is unavailable."""
    known = rooms or [r for r in ROOMS_AND_UNITS if not r.lower().startswith("unit")]
    out = []
    for t in turns:
        text = t["text"]
        room = next((r for r in sorted(known, key=len, reverse=True) if r.lower() in text.lower()), None)
        status = kind == WalkKind.rewalk and (_STATUS_FIXED.search(text) or _STATUS_NOT.search(text))
        if status:
            claimed = "not_fixed" if _STATUS_NOT.search(text) else "fixed"
            lab = {"kind": "status_update", "defects": [], "status_target": text[:80], "claimed": claimed}
        elif _DEFECT_WORDS.search(text):
            lab = {"kind": "defect", "defects": [text.strip()], "status_target": None, "claimed": None}
        elif room:
            lab = {"kind": "location", "defects": [], "status_target": None, "claimed": None}
        else:
            lab = {"kind": "other", "defects": [], "status_target": None, "claimed": None}
        out.append({"idx": t["idx"], "room": room, "continues_previous": False, **lab})
    return out


# --------------------------------------------------------------------------- model call


def _chunks(turns: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [turns[i : i + size] for i in range(0, len(turns), size)] or []


def label_turns(
    turns: list[dict[str, Any]], rooms: list[str], kind: WalkKind
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Labels for every turn (model first, rules on failure) + info about how they were made."""
    if not turns:
        return [], {"method": "none", "chunks": 0}
    labels: dict[int, dict[str, Any]] = {}
    info: dict[str, Any] = {"method": "model", "chunks": 0, "providers": [], "missing": 0}
    room_before: str | None = None
    try:
        for chunk in _chunks(turns, P.CLASSIFY_CHUNK_TURNS):
            max_tokens = min(P.CLASSIFY_MAX_TOKENS_CAP,
                             P.CLASSIFY_MAX_TOKENS_BASE + P.CLASSIFY_MAX_TOKENS_PER_TURN * len(chunk))
            result, rec = llm.structured(
                TurnLabels,
                [{"role": "system", "content": SYSTEM},
                 {"role": "user", "content": _user_prompt(chunk, rooms, kind, room_before)}],
                reasoning_effort="low",
                max_tokens=max_tokens,
                stage=Stage.classify_turns.value,
            )
            info["chunks"] += 1
            info["providers"].append(rec.provider)
            wanted = {t["idx"] for t in chunk}
            for lab in result.turns:
                if lab.idx in wanted:
                    labels[lab.idx] = lab.model_dump()
            for lab in sorted(labels.values(), key=lambda r: r["idx"]):
                if lab.get("room"):
                    room_before = normalize_room(lab["room"], rooms)
    except (ApiError, llm.StructuredOutputError) as err:
        log.warning("classify_turns: model unavailable (%s); using keyword rules", err)
        return fallback_labels(turns, rooms, kind), {"method": "rules", "error": str(err)[:300]}
    rules = {r["idx"]: r for r in fallback_labels(turns, rooms, kind)}
    for t in turns:
        if t["idx"] not in labels:
            info["missing"] += 1
            labels[t["idx"]] = rules[t["idx"]]
    return [labels[t["idx"]] for t in turns], info


def run(ctx: StageContext) -> StageOutcome:
    with ctx.session() as s:
        rows = s.exec(select(Turn).where(Turn.walk_id == ctx.walk_id).order_by(Turn.idx)).all()
        turns = [{"id": r.id, "idx": r.idx, "start_ms": r.start_ms, "end_ms": r.end_ms, "text": r.text} for r in rows]
    labels, info = label_turns(turns, ctx.rooms, ctx.kind)
    for lab in labels:
        lab["room"] = normalize_room(lab.get("room"), ctx.rooms)
        if lab["kind"] != "defect":
            lab["defects"], lab["continues_previous"] = [], False
        if lab["kind"] != "status_update":
            lab["status_target"], lab["claimed"] = None, None
    carry_rooms(labels)
    merged = [{**t, **{k: v for k, v in lab.items() if k != "idx"}} for t, lab in zip(turns, labels, strict=True)]
    ctx.save_output(Stage.classify_turns, {"version": 1, "info": info, "turns": merged})

    with ctx.session() as s:
        by_id = {r.id: r for r in s.exec(select(Turn).where(Turn.walk_id == ctx.walk_id)).all()}
        for t in merged:
            row = by_id.get(t["id"])
            if row is None:
                continue
            row.kind = TurnKind(t["kind"])
            row.room = t["room"]
            row.defects = list(t["defects"])
            row.claimed = ClaimedStatus(t["claimed"]) if t["claimed"] else None
            s.add(row)
        s.commit()

    counts = {k: sum(1 for t in merged if t["kind"] == k) for k in ("defect", "location", "status_update", "other")}
    counts["defects"] = sum(len(t["defects"]) for t in merged)
    rooms = len({t["room"] for t in merged if t["room"]})
    counts["rooms"] = rooms
    msg = f"Muse Spark Open found {counts['defects']} defects in {rooms} rooms"
    if info.get("method") == "rules":
        msg = f"Model unavailable; keyword rules found {counts['defects']} defects in {rooms} rooms"
    return StageOutcome(counts=counts, message=msg)
