"""Rendering: the terminal summary table, eval/report.md and eval/report.json (PLAN 3.3).

Everything here is a pure function of the `WalkEval` results, so the same DATA_DIR gives the same
bytes (no "now" timestamps, ids sorted, floats rounded).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from eval.labels import Label, ProjectConfig
from eval.matching import MAX_DT_S, MIN_JACCARD, MatchResult, Pair, Pred
from eval.metrics import InitialMetrics, RewalkMetrics, SummaryRow, summary_rows
from punchwalk.core.enums import TRADE_LABEL, format_item_number


@dataclass
class StageTiming:
    stage: str
    status: str
    duration_ms: int


@dataclass
class CostSummary:
    calls: int = 0
    cached: int = 0
    failed: int = 0
    live_usd: float = 0.0  # what this run spent (cached calls cost $0)
    est_live_usd: float = 0.0  # what the same calls cost live (SAM frames/images, ASR seconds, LLM tokens if known)
    llm_calls_without_tokens: int = 0  # cached LLM calls carry no token counts, so their cost isn't in the estimate
    latency_ms: int = 0  # recorded latency (0 for cached calls)
    by_model: dict[str, int] = field(default_factory=dict)  # model label -> calls
    fallback_calls: int = 0


@dataclass
class WalkEval:
    name: str
    walk_id: str
    kind: str  # initial | rewalk
    holdout: bool
    labels: list[Label]
    preds: list[Pred] = field(default_factory=list)
    match: MatchResult | None = None
    initial: InitialMetrics | None = None
    rewalk: RewalkMetrics | None = None
    rewalk_of: str | None = None  # walk name
    clip_offsets: list[float] = field(default_factory=list)
    error: str | None = None
    error_detail: str | None = None
    stages: list[StageTiming] = field(default_factory=list)
    cost: CostSummary = field(default_factory=CostSummary)
    created_at: str | None = None  # walk row creation, ISO (the eval summary's updated_at)

    def rows(self) -> list[SummaryRow]:
        return summary_rows(self.name, holdout=self.holdout, initial=self.initial, rewalk=self.rewalk,
                            error=self.error)


@dataclass
class EvalRun:
    labels_dir: str
    cache_mode: str
    project: ProjectConfig
    walks: list[WalkEval]

    def rows(self) -> list[SummaryRow]:
        return [r for w in self.walks for r in w.rows()]


# --------------------------------------------------------------------------- formatting helpers


def fmt_t(t: float | None) -> str:
    if t is None:
        return "-"
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m):02d}:{s:04.1f}"


def fmt_dt(dt: float) -> str:
    text = f"{dt:+.1f}"
    return "0.0" if text in ("+0.0", "-0.0") else text


def fmt_ms(ms: int) -> str:
    return f"{ms / 1000:.1f} s" if ms < 60_000 else f"{ms // 60_000} min {ms % 60_000 / 1000:.0f} s"


def cell(value: Any) -> str:
    text = "-" if value is None or value == "" else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def trade_word(t: Any) -> str:
    return "-" if t is None else TRADE_LABEL.get(t, str(t))


def md_table(header: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(cell(c) for c in row) + " |" for row in rows]
    return out


# --------------------------------------------------------------------------- terminal table


def text_table(rows: list[SummaryRow]) -> str:
    """The summary printed by `make eval`: tuned walks first, then the holdout, separately."""
    header = ["walk", "metric", "value", "target", "result"]
    tuned = [r for r in rows if not r.holdout]
    hold = [r for r in rows if r.holdout]
    body = [[r.walk, r.metric, r.value, r.target, r.result] for r in tuned]
    hbody = [[r.walk, r.metric, r.value, r.target, r.result] for r in hold]
    widths = [max(len(str(x)) for x in col) for col in zip(header, *body, *hbody, strict=False)]

    def line(vals: list[str]) -> str:
        return "  ".join(str(v).ljust(w) for v, w in zip(vals, widths, strict=True)).rstrip()

    out = [line(header), line(["-" * w for w in widths])]
    out += [line(r) for r in body] or ["(no tuned walks)"]
    if hbody:
        out += ["", "holdout (never used for tuning):", line(header), line(["-" * w for w in widths])]
        out += [line(r) for r in hbody]
    return "\n".join(out)


# --------------------------------------------------------------------------- report.md


def _pair_row(p: Pair) -> list[Any]:
    lb, pr = p.label, p.pred
    room = f"{lb.room or '-'} / {pr.room or '-'} ({'ok' if p.room_ok else 'wrong'})"
    trade = f"{trade_word(lb.trade)} / {trade_word(pr.trade)} ({'ok' if p.trade_ok else 'wrong'})"
    return [lb.item_no, fmt_t(lb.t), format_item_number(pr.number), fmt_t(pr.t), fmt_dt(p.dt), p.jaccard,
            room, trade, lb.description, pr.title, pr.grounding.value if pr.grounding else "-",
            pr.status.value, pr.review_reason, "match"]


def _missed_row(lb: Label) -> list[Any]:
    return [lb.item_no, fmt_t(lb.t), "-", "-", "-", "-", f"{lb.room or '-'} / -", f"{trade_word(lb.trade)} / -",
            lb.description, "-", "-", "-", "-", "miss"]


def _fp_row(pr: Pred) -> list[Any]:
    return ["-", "-", format_item_number(pr.number), fmt_t(pr.t), "-", "-", f"- / {pr.room or '-'}",
            f"- / {trade_word(pr.trade)}", "-", pr.title, pr.grounding.value if pr.grounding else "-",
            pr.status.value, pr.review_reason, "false positive"]


DIFF_HEADER = ["Label", "t label", "Item", "t item", "dt (s)", "Jaccard", "Room (label / item)",
               "Trade (label / item)", "Label description", "Item title", "Grounding", "Status",
               "Review reason", "Result"]


def _initial_section(w: WalkEval) -> list[str]:
    assert w.match is not None
    entries: list[tuple[float, int, list[Any]]] = []
    for p in w.match.pairs:
        entries.append((p.label.t or 0.0, 0, _pair_row(p)))
    for lb in w.match.missed:
        entries.append((lb.t or 0.0, 1, _missed_row(lb)))
    for pr in w.match.false_positives:
        entries.append((pr.t, 2, _fp_row(pr)))
    entries.sort(key=lambda e: (e[0], e[1], str(e[2][0]), str(e[2][2])))
    out = [f"{len(w.labels)} labels, {len(w.preds)} items: {len(w.match.pairs)} matched, "
           f"{len(w.match.missed)} missed, {len(w.match.false_positives)} false positives.", ""]
    out += md_table(DIFF_HEADER, [e[2] for e in entries])
    return out


def _rewalk_section(w: WalkEval) -> list[str]:
    rw = w.rewalk
    assert rw is not None
    out: list[str] = []
    if rw.pending:
        out += ["Closure metrics: n/a. The re-walk's `resolve_closures` stage (Phase 7) hasn't run, so no "
                "item has a re-walk decision yet. The rows below show the items as they stand.", ""]
    if rw.unmatched:
        out += [f"{rw.unmatched} row(s) excluded: their {w.rewalk_of or 'first-walk'} label matched no item.", ""]
    if rw.new_items:
        out += [f"The re-walk created {rw.new_items} new item(s) (defects that matched no open item).", ""]
    header = ["Label", "t label", "Claimed fixed", "Fixed (truth)", "Item", "Status after", "Decision",
              "Signed off", "Closure correct", "False claim caught"]
    rows = []
    for r in rw.rows:
        yn = {True: "yes", False: "no", None: "-"}
        rows.append([r.label.item_no, fmt_t(r.label.t), yn[r.label.claimed_fixed_in_rewalk], yn[r.label.fixed_in_rewalk],
                     format_item_number(r.pred.number) if r.pred else "no match",
                     r.pred.status.value if r.pred else "-", r.pred.closure_decision if r.pred else "-",
                     yn[r.signed_off], yn[r.correct] if not rw.pending else "n/a",
                     (yn[r.caught] if r.false_claim else "-") if not rw.pending else "n/a"])
    return out + md_table(header, rows)


def _cost_section(walks: list[WalkEval]) -> list[str]:
    rows = []
    for w in walks:
        c = w.cost
        total = sum(s.duration_ms for s in w.stages)
        est = f"${c.est_live_usd:.4f}" + (f" + {c.llm_calls_without_tokens} LLM calls" if c.llm_calls_without_tokens else "")
        rows.append([w.name, fmt_ms(total), c.calls, c.cached, c.failed, c.fallback_calls, f"${c.live_usd:.4f}", est,
                     fmt_ms(c.latency_ms)])
    out = md_table(["Walk", "Pipeline time (this run)", "Model calls", "Cached", "Failed", "Fallback",
                    "Live cost (this run)", "Est. live cost", "Recorded latency"], rows)
    out += ["", "Est. live cost prices SAM 3.1 frames/images and Muse Voice Transcribe seconds at list price; "
            "cached Muse Spark Open calls carry no token counts, so they are counted, not priced.", ""]
    for w in walks:
        if not w.stages:
            continue
        stages = ", ".join(f"{s.stage} {fmt_ms(s.duration_ms)}" + ("" if s.status == "done" else f" ({s.status})")
                           for s in w.stages)
        models = ", ".join(f"{k} x{v}" for k, v in sorted(w.cost.by_model.items()))
        out += [f"- **{w.name}** stages: {stages}." + (f" Calls: {models}." if models else "")]
    return out


def render_markdown(run: EvalRun) -> str:
    rows = run.rows()
    tuned = [r for r in rows if not r.holdout]
    hold = [r for r in rows if r.holdout]
    names = ", ".join(w.name for w in run.walks) or "none"
    out = [
        "# Punchwalk eval report",
        "",
        f"Generated by `eval/run_eval.py` from the labels in `{run.labels_dir}` ({names}). "
        f"Pipeline answers come from the cache (`PUNCHWALK_CACHE={run.cache_mode}`). "
        f"Project rooms: {', '.join(run.project.rooms) or 'none'} ({run.project.source}).",
        "",
        f"Matching: an item matches a label when its quote starts within {MAX_DT_S:.0f} s of the label's time "
        f"and the rooms agree or the words overlap (token Jaccard >= {MIN_JACCARD}); one-to-one by Hungarian "
        "assignment on time closeness + word overlap. Targets are PLAN 3.4.",
        "",
        "## Summary",
        "",
    ]
    out += md_table(["Walk", "Metric", "Value", "Target", "Result"],
                    [[r.walk, r.metric, r.value, r.target, r.result] for r in tuned]) if tuned else ["No tuned walks."]
    if hold:
        out += ["", "## Holdout (never used for tuning)", ""]
        out += md_table(["Walk", "Metric", "Value", "Target", "Result"],
                        [[r.walk, r.metric, r.value, r.target, r.result] for r in hold])
    for w in run.walks:
        kind = "re-walk" + (f" of {w.rewalk_of}" if w.rewalk_of else "") if w.kind == "rewalk" else "first walk"
        out += ["", f"## {w.name} ({kind}{', holdout' if w.holdout else ''})", ""]
        if w.clip_offsets and len(w.clip_offsets) > 1:
            parts = ", ".join(f"{chr(97 + i)} at {fmt_t(o)}" for i, o in enumerate(w.clip_offsets))
            out += [f"Clip parts: {parts}.", ""]
        if w.error:
            out += [f"The pipeline failed: {w.error}"]
            if w.error_detail:
                out += ["", f"Detail: `{cell(w.error_detail)[:600]}`"]
            continue
        out += _rewalk_section(w) if w.kind == "rewalk" else _initial_section(w)
    out += ["", "## Timing and cost", ""]
    out += _cost_section(run.walks)
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------- report.json


def _ratio(r: Any) -> dict[str, Any] | None:
    return None if r is None else {"num": r.num, "den": r.den, "value": None if r.value is None else round(r.value, 4)}


def app_summary(run: EvalRun) -> dict[str, Any] | None:
    """The 'How this walk was processed' eval block (schemas.EvalSummaryOut), over the tuned walks."""
    tuned = [w for w in run.walks if not w.holdout and not w.error]
    initial = [w.initial for w in tuned if w.initial]
    if not initial:
        return None
    metrics = [
        {"key": "defects_found", "label": "Defects found",
         "value": sum(m.recall.num for m in initial), "total": sum(m.recall.den for m in initial)},
        {"key": "right_trade", "label": "Right trade",
         "value": sum(m.trade.num for m in initial), "total": sum(m.trade.den for m in initial)},
        {"key": "right_room", "label": "Right room",
         "value": sum(m.room.num for m in initial), "total": sum(m.room.den for m in initial)},
    ]
    claims = [w.rewalk.false_claim for w in tuned if w.rewalk and not w.rewalk.pending and w.rewalk.false_claim]
    if claims:
        metrics.append({"key": "false_claims_caught", "label": "False \"fixed\" claims caught",
                        "value": sum(c.num for c in claims), "total": sum(c.den for c in claims)})
    stamps = sorted(w.created_at for w in run.walks if w.created_at)
    return {"source": "eval/report.md", "updated_at": stamps[-1] if stamps else None, "metrics": metrics}


def render_json(run: EvalRun) -> str:
    walks = []
    for w in run.walks:
        entry: dict[str, Any] = {"walk": w.name, "walk_id": w.walk_id, "kind": w.kind, "holdout": w.holdout,
                                 "labels": len(w.labels), "items": len(w.preds), "error": w.error}
        if w.initial:
            entry["metrics"] = {k: _ratio(w.initial.get(k)) for k in
                                ("recall", "precision", "trade", "room", "grounding", "needs_review")}
        if w.rewalk:
            entry["metrics"] = {"pending": w.rewalk.pending, "closure": _ratio(w.rewalk.closure),
                                "false_claim": _ratio(w.rewalk.false_claim)}
        walks.append(entry)
    doc = {
        "labels_dir": run.labels_dir,
        "cache_mode": run.cache_mode,
        "rooms": run.project.rooms,
        "walks": walks,
        "summary": [r.__dict__ for r in run.rows()],
        "app_summary": app_summary(run),
    }
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
