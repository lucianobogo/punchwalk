"""Defect mentions: what was said, grouped from classified turns (used by find_points and fuse).

A *mention* is one defect the inspector called out. A defect turn may describe several defects
(`defects[]`), and a description may continue over the next turn (`continues_previous`); such
turns form one group sharing a time window. `status_update` turns (re-walk "kitchen knob, fixed")
become one mention each, kind `status_update`.

Input rows are the `turns` of `stages/classify_turns.json`:
    {"id", "idx", "start_ms", "end_ms", "text", "kind", "room", "defects", "continues_previous",
     "status_target", "claimed"}
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Mention:
    key: str  # stable: "<first turn idx>.<position in the group>"
    kind: str  # defect | status_update
    turn_ids: list[str]
    turn_idxs: list[int]
    start_s: float  # first turn start
    end_s: float  # last turn end
    text: str  # this defect's short description (or the status update's target)
    quote: str  # the inspector's words: the group's turn texts joined
    room: str | None
    index: int = 0  # position of this mention in its group
    count: int = 1  # mentions in the group
    claimed: str | None = None  # status_update only
    group: str = ""  # key of the group (first turn idx)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Group:
    kind: str
    turns: list[dict[str, Any]]
    defects: list[str]


def _groups(turns: list[dict[str, Any]]) -> list[_Group]:
    groups: list[_Group] = []
    for t in sorted(turns, key=lambda r: r["idx"]):
        kind = t.get("kind")
        if kind == "defect":
            prev = groups[-1] if groups else None
            if t.get("continues_previous") and prev is not None and prev.kind == "defect" \
                    and prev.turns[-1]["idx"] == t["idx"] - 1:
                prev.turns.append(t)
                prev.defects.extend(d for d in t.get("defects") or [] if d.strip())
                continue
            groups.append(_Group("defect", [t], [d for d in t.get("defects") or [] if d.strip()]))
        elif kind == "status_update":
            groups.append(_Group("status_update", [t], []))
    return groups


def build_mentions(turns: list[dict[str, Any]]) -> list[Mention]:
    """Mentions in walk order (by group start, then position in the group)."""
    out: list[Mention] = []
    for g in _groups(turns):
        first, last = g.turns[0], g.turns[-1]
        quote = " ".join(t["text"].strip() for t in g.turns).strip()
        room = next((t.get("room") for t in g.turns if t.get("room")), None)
        texts = (g.defects or [quote]) if g.kind == "defect" else [first.get("status_target") or quote]
        for i, text in enumerate(texts):
            out.append(
                Mention(
                    key=f"{first['idx']}.{i}",
                    kind=g.kind,
                    turn_ids=[t["id"] for t in g.turns],
                    turn_idxs=[t["idx"] for t in g.turns],
                    start_s=first["start_ms"] / 1000.0,
                    end_s=last["end_ms"] / 1000.0,
                    text=text.strip(),
                    quote=quote,
                    room=room,
                    index=i,
                    count=len(texts),
                    claimed=first.get("claimed") if g.kind == "status_update" else None,
                    group=str(first["idx"]),
                )
            )
    return out


def mention_from_dict(d: dict[str, Any]) -> Mention:
    return Mention(**d)
