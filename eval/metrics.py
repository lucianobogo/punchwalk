"""Metrics over matched walks (PLAN 3.2) and the PLAN 3.4 targets.

Initial walks: recall, precision, trade accuracy (over matched), room accuracy (over matched), grounding
rate for pointed labels (pointed=1 rows whose matched item is outlined by SAM: `sam_concept` or
`sam_box`; unmatched pointed rows count as not grounded), needs_review rate (over predictions).

Re-walks (rows reuse the initial walk's item_no): for each row whose initial label matched an item,
closure accuracy = (item signed off, i.e. status `closed`) == fixed_in_rewalk, and false-claim catch =
rows with claimed_fixed_in_rewalk=1 and fixed_in_rewalk=0 whose item ends `needs_review`. Both are
"n/a (Phase 7 pending)" until the re-walk's `resolve_closures` stage has run (status `done`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from eval.labels import Label
from eval.matching import MatchResult, Pred
from punchwalk.core.enums import Grounding, ItemStatus

SAM_GROUNDED = frozenset({Grounding.sam_concept, Grounding.sam_box})
PHASE7_PENDING = "n/a (Phase 7 pending)"


@dataclass(frozen=True)
class Ratio:
    num: int
    den: int

    @property
    def value(self) -> float | None:
        return None if self.den == 0 else self.num / self.den

    def text(self) -> str:
        return "n/a (0 of 0)" if self.den == 0 else f"{self.value:.2f} ({self.num}/{self.den})"


@dataclass(frozen=True)
class Target:
    key: str
    label: str
    minimum: float | None  # None = reported, no target

    def text(self) -> str:
        if self.minimum is None:
            return "-"
        return "= 1.00" if self.minimum >= 1.0 else f">= {self.minimum:.2f}"


# PLAN 3.4. Order = summary table order.
TARGETS: dict[str, Target] = {t.key: t for t in (
    Target("recall", "Recall", 0.9),
    Target("precision", "Precision", 0.9),
    Target("trade", "Trade accuracy", 0.85),
    Target("room", "Room accuracy", 0.9),
    Target("grounding", "Grounding rate (pointed)", None),
    Target("needs_review", "Needs-review rate", None),
    Target("closure", "Closure accuracy", 0.9),
    Target("false_claim", "False-claim catch", 1.0),
)}
INITIAL_KEYS = ("recall", "precision", "trade", "room", "grounding", "needs_review")
REWALK_KEYS = ("closure", "false_claim")


def verdict(key: str, ratio: Ratio | None) -> str:
    """'pass' / 'FAIL' against the target, '-' when there is no target, 'n/a' without data."""
    target = TARGETS[key]
    if ratio is None or ratio.value is None:
        return "n/a"
    if target.minimum is None:
        return "-"
    return "pass" if ratio.value + 1e-9 >= target.minimum else "FAIL"


@dataclass
class InitialMetrics:
    recall: Ratio
    precision: Ratio
    trade: Ratio
    room: Ratio
    grounding: Ratio
    needs_review: Ratio

    def get(self, key: str) -> Ratio:
        return getattr(self, key)


def initial_metrics(labels: list[Label], preds: list[Pred], match: MatchResult) -> InitialMetrics:
    matched = match.pairs
    pointed = [lb for lb in labels if lb.pointed]
    by_label = {p.label.item_no: p for p in matched}
    grounded = sum(1 for lb in pointed if (p := by_label.get(lb.item_no)) and p.pred.grounding in SAM_GROUNDED)
    return InitialMetrics(
        recall=Ratio(len(matched), len(labels)),
        precision=Ratio(len(matched), len(preds)),
        trade=Ratio(sum(1 for p in matched if p.trade_ok), len(matched)),
        room=Ratio(sum(1 for p in matched if p.room_ok), len(matched)),
        grounding=Ratio(grounded, len(pointed)),
        needs_review=Ratio(sum(1 for p in preds if p.status == ItemStatus.needs_review), len(preds)),
    )


@dataclass(frozen=True)
class ClosureRow:
    """One re-walk label row, joined to the initial walk's matched item (if any)."""

    label: Label
    pred: Pred | None  # the item's state after the re-walk; None when its initial label matched nothing
    signed_off: bool | None  # predicted: status closed
    correct: bool | None  # signed_off == fixed_in_rewalk (None: no truth or no item)
    false_claim: bool  # claimed fixed but not fixed
    caught: bool | None  # false claim ended needs_review


@dataclass
class RewalkMetrics:
    pending: bool  # resolve_closures hasn't run: report n/a
    rows: list[ClosureRow] = field(default_factory=list)
    closure: Ratio | None = None
    false_claim: Ratio | None = None
    unmatched: int = 0  # rows whose initial label matched no item (excluded)
    new_items: int = 0  # items the re-walk created (defects that matched no open item)

    def get(self, key: str) -> Ratio | None:
        return getattr(self, key)


def rewalk_metrics(labels: list[Label], initial_match: MatchResult | None, after: dict[str, Pred], *,
                   pending: bool, new_items: int = 0) -> RewalkMetrics:
    """`after` maps item_id -> Pred with the item's state after the re-walk."""
    rows: list[ClosureRow] = []
    for lb in sorted(labels, key=lambda x: x.item_no):
        pair = initial_match.pair_for(lb.item_no) if initial_match else None
        pred = after.get(pair.pred.item_id, pair.pred) if pair else None
        signed = None if pred is None else pred.status == ItemStatus.closed
        correct = None if pred is None or lb.fixed_in_rewalk is None else signed == lb.fixed_in_rewalk
        false_claim = lb.claimed_fixed_in_rewalk is True and lb.fixed_in_rewalk is False
        caught = (pred.status == ItemStatus.needs_review) if (false_claim and pred is not None) else None
        rows.append(ClosureRow(label=lb, pred=pred, signed_off=signed, correct=correct,
                               false_claim=false_claim, caught=caught))
    out = RewalkMetrics(pending=pending, rows=rows, new_items=new_items,
                        unmatched=sum(1 for r in rows if r.pred is None))
    if pending:
        return out
    judged = [r for r in rows if r.correct is not None]
    out.closure = Ratio(sum(1 for r in judged if r.correct), len(judged))
    claims = [r for r in rows if r.false_claim and r.pred is not None]
    out.false_claim = Ratio(sum(1 for r in claims if r.caught), len(claims))
    return out


@dataclass(frozen=True)
class SummaryRow:
    walk: str
    key: str
    metric: str
    value: str
    target: str
    result: str  # pass | FAIL | n/a | - | error
    holdout: bool


def summary_rows(walk: str, *, holdout: bool, initial: InitialMetrics | None = None,
                 rewalk: RewalkMetrics | None = None, error: str | None = None) -> list[SummaryRow]:
    if error is not None:
        return [SummaryRow(walk, "error", "Pipeline", f"error: {error}", "-", "error", holdout)]
    rows: list[SummaryRow] = []
    if initial is not None:
        for key in INITIAL_KEYS:
            r = initial.get(key)
            rows.append(SummaryRow(walk, key, TARGETS[key].label, r.text(), TARGETS[key].text(),
                                   verdict(key, r), holdout))
    if rewalk is not None:
        for key in REWALK_KEYS:
            r = rewalk.get(key)
            value = PHASE7_PENDING if rewalk.pending else (r.text() if r else "n/a")
            rows.append(SummaryRow(walk, key, TARGETS[key].label, value, TARGETS[key].text(),
                                   "n/a" if rewalk.pending else verdict(key, r), holdout))
    return rows


def any_failed(rows: list[SummaryRow]) -> bool:
    return any(r.result in ("FAIL", "error") for r in rows)
