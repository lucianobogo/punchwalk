"""Predicted items <-> labeled items, per walk (PLAN 3.1).

- t_pred is the item's **quote start** (`Item.quote_start_s`: the start of the first turn the item came
  from, i.e. when the inspector started describing it, the same moment `time_mmss` labels). Items
  without one fall back to `video_start_s + 2.0` (the runner opens the clip 2 s before the quote).
- A pair is a candidate when |t_pred - t_label| <= 6 s AND (the rooms match, case/whitespace-insensitive,
  OR the token Jaccard of (title + description) vs the label description is >= 0.2).
- Tokens: lowercase alphanumeric runs, a small stopword list dropped, a trailing plural "s" stripped.
- Score = 0.5 * time closeness (1 - |dt| / 6) + 0.4 * min(1, Jaccard / 0.5) + 0.1 * room match; the
  assignment is Hungarian (`scipy.optimize.linear_sum_assignment`) on -score over candidate pairs only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment

from eval.labels import Label
from punchwalk.core.enums import Grounding, ItemStatus, Trade

MAX_DT_S = 6.0
MIN_JACCARD = 0.2
QUOTE_FALLBACK_LEAD_S = 2.0
W_TIME, W_TEXT, W_ROOM = 0.5, 0.4, 0.1
JACCARD_FULL = 0.5  # a Jaccard this high counts as a perfect text match in the score
_NO_PAIR = 1e6

STOPWORDS = frozenset((
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have", "in", "is", "it", "its",
    "of", "on", "or", "that", "the", "this", "to", "was", "were", "with", "there", "here", "needs", "need",
    "needed", "just", "one", "some", "our", "we", "you", "uh", "um", "so", "very", "also", "into", "onto",
    "near", "next",
))


@dataclass(frozen=True)
class Pred:
    """One predicted item, flattened from an Item row."""

    item_id: str
    number: int
    t: float
    title: str
    description: str
    room: str | None
    trade: Trade | None
    grounding: Grounding | None
    status: ItemStatus
    review_reason: str | None
    pointed: bool
    origin_walk_id: str
    closure_decision: str | None = None


@dataclass(frozen=True)
class Pair:
    label: Label
    pred: Pred
    dt: float  # t_pred - t_label
    jaccard: float
    room_ok: bool
    score: float

    @property
    def trade_ok(self) -> bool:
        return self.label.trade is not None and self.pred.trade == self.label.trade


@dataclass
class MatchResult:
    pairs: list[Pair] = field(default_factory=list)
    missed: list[Label] = field(default_factory=list)  # labels with no predicted item
    false_positives: list[Pred] = field(default_factory=list)  # predictions with no label

    def pair_for(self, item_no: int) -> Pair | None:
        return next((p for p in self.pairs if p.label.item_no == item_no), None)


def _enum(cls: type, value: Any) -> Any:
    if value is None or value == "":
        return None
    try:
        return cls(value)
    except ValueError:
        return None


def pred_t(item: Any) -> float:
    if getattr(item, "quote_start_s", None) is not None:
        return float(item.quote_start_s)
    return float(item.video_start_s or 0.0) + QUOTE_FALLBACK_LEAD_S


def pred_from_item(item: Any, status: ItemStatus | str | None = None) -> Pred:
    """Item row -> Pred. Missing trade/room/evidence stay None (scored as wrong, never a crash)."""
    ev = item.evidence if isinstance(item.evidence, dict) else {}
    closure = item.closure if isinstance(item.closure, dict) else {}
    return Pred(
        item_id=item.id,
        number=int(item.number),
        t=pred_t(item),
        title=item.title or "",
        description=item.description or "",
        room=item.room,
        trade=_enum(Trade, item.trade),
        grounding=_enum(Grounding, ev.get("grounding")),
        status=_enum(ItemStatus, status if status is not None else item.status) or ItemStatus.open,
        review_reason=item.review_reason,
        pointed=item.point_id is not None,
        origin_walk_id=item.origin_walk_id,
        closure_decision=closure.get("decision"),
    )


def tokens(text: str) -> set[str]:
    out = set()
    for tok in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if tok in STOPWORDS:
            continue
        if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
            tok = tok[:-1]
        out.add(tok)
    return out


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def norm_room(room: str | None) -> str:
    return " ".join((room or "").lower().split())


def rooms_match(a: str | None, b: str | None) -> bool:
    return bool(norm_room(a)) and norm_room(a) == norm_room(b)


def candidate(label: Label, pred: Pred, *, max_dt: float = MAX_DT_S, min_jaccard: float = MIN_JACCARD) -> Pair | None:
    """The pair's facts and score when it may match, else None."""
    if label.t is None:
        raise ValueError(f"{label.source}: label time not resolved (call labels.with_times first)")
    dt = pred.t - label.t
    if abs(dt) > max_dt:
        return None
    jac = jaccard(tokens(f"{pred.title} {pred.description}"), tokens(label.description))
    room_ok = rooms_match(pred.room, label.room)
    if not (room_ok or jac >= min_jaccard):
        return None
    score = W_TIME * (1 - abs(dt) / max_dt) + W_TEXT * min(1.0, jac / JACCARD_FULL) + W_ROOM * room_ok
    return Pair(label=label, pred=pred, dt=round(dt, 3), jaccard=round(jac, 3), room_ok=room_ok, score=round(score, 4))


def match_walk(labels: list[Label], preds: list[Pred], *, max_dt: float = MAX_DT_S,
               min_jaccard: float = MIN_JACCARD) -> MatchResult:
    """Hungarian assignment of predictions to labels over candidate pairs (one-to-one)."""
    labels = sorted(labels, key=lambda lb: (lb.t if lb.t is not None else 0.0, lb.item_no))
    preds = sorted(preds, key=lambda p: (p.t, p.number))
    if not labels or not preds:
        return MatchResult(missed=list(labels), false_positives=list(preds))
    cands: dict[tuple[int, int], Pair] = {}
    cost = np.full((len(labels), len(preds)), _NO_PAIR)
    for i, lb in enumerate(labels):
        for j, pr in enumerate(preds):
            pair = candidate(lb, pr, max_dt=max_dt, min_jaccard=min_jaccard)
            if pair is not None:
                cands[(i, j)] = pair
                cost[i, j] = -pair.score
    rows, cols = linear_sum_assignment(cost)
    pairs = [cands[(i, j)] for i, j in zip(rows.tolist(), cols.tolist(), strict=True) if (i, j) in cands]
    used_l = {p.label.item_no for p in pairs}
    used_p = {p.pred.item_id for p in pairs}
    return MatchResult(
        pairs=sorted(pairs, key=lambda p: (p.label.t or 0.0, p.label.item_no)),
        missed=[lb for lb in labels if lb.item_no not in used_l],
        false_positives=[p for p in preds if p.item_id not in used_p],
    )
