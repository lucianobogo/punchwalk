"""Stage `write_items` (PLAN 2.1): Muse Spark Open turns each provisional item into a punch item.

One call per item (reasoning_effort=low). Images, in order: (1) the evidence frame with a blue dot
where the finger pointed (no dot for voice-only items), (2) the pointing frame (pointed items),
(3, 4) the context frames 0.7 s before and after. Text: the inspector's words, the room, the trades.

Output (`ItemWrite`): title <= 60, description (the card's AI summary, 1-2 sentences), action <= 80
(imperative), trade, room, location_detail, severity, sam_concept (noun phrase for SAM),
defect_box ([x1, y1, x2, y2] in image 1's pixels, near the dot, or null), confidence, rationale.
Rules: only what the narration says and the frame shows, no invented quantities; if the frame
doesn't show the defect, defect_box is null and confidence lower.

On a reply that fails validation twice (or every provider failing), the item keeps its
provisional text and becomes needs_review with a plain note. Items a human touched, or that are
dispatched or closed, are not rewritten. `stages/write_items.json` keeps each result, with
defect_box converted to display pixels for ground_defects.
"""

from __future__ import annotations

import logging
import re
from contextlib import closing
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from punchwalk.clients import calls, llm
from punchwalk.clients.base import ApiError
from punchwalk.core.enums import TRADE_LABEL, HistoryAction, HistoryActor, ItemStatus, Severity, Stage, Trade
from punchwalk.core.models import Evidence
from punchwalk.pipeline.classify import normalize_room
from punchwalk.pipeline.context import StageContext, StageOutcome, data_dir
from punchwalk.pipeline.items import add_history, is_protected, walk_items
from punchwalk.render.evidence import render_evidence

log = logging.getLogger(__name__)

TITLE_MAX, ACTION_MAX, CONCEPT_MAX = 60, 80, 60
MAX_TOKENS = 1800
REVIEW_WRITE_FAILED = "Couldn't write this item from the photo and what was said."

TradeValue = Literal[tuple(t.value for t in Trade)]  # type: ignore[valid-type]
SeverityValue = Literal[tuple(s.value for s in Severity)]  # type: ignore[valid-type]


class ItemWrite(BaseModel):
    title: str = Field(description="punch-list style, at most 60 characters")
    description: str = Field(description="1-2 sentences: the problem and where it is; no filler words")
    action: str = Field(description="imperative, at most 80 characters, what the trade must do")
    trade: TradeValue  # type: ignore[valid-type]
    room: str | None
    location_detail: str | None = Field(description="where in the room, e.g. 'upper cabinet left of the sink'")
    severity: SeverityValue  # type: ignore[valid-type]
    sam_concept: str | None = Field(description="short noun phrase naming the defective object or area")
    defect_box: list[float] | None = Field(description="[x1, y1, x2, y2] of the defect in image 1's pixels, or null")
    confidence: float = Field(description="0 to 1")
    rationale: str = Field(description="at most 200 characters")


SYSTEM = """You write construction punch-list items from a walk-through.
An inspector pointed at a defect with a finger and described it out loud. You get the inspector's words,
the room, and photos from the video. Write one punch item.

Rules:
- State only what the narration says and the photos show. Never invent quantities, sizes, specs, brands or causes.
- title: punch-list style, at most 60 characters (for example "Cabinet knob missing, upper left of sink").
- description: 1-2 plain sentences summarizing the problem and where it is. Clearer than the raw quote,
  no filler words ("uh", "so", "okay"), no repeated words. Describe the defect itself; never mention the
  photos, the video, the blue dot or what is or isn't visible (that goes in rationale and confidence).
- location_detail: where in the room, in a few clean words (for example "upper cabinet left of the sink"),
  or null.
- action: at most 80 characters, imperative, what the trade needs to do (for example "Install a matching knob").
- trade: the one trade that fixes it, from the list given. Marks, scuffs, nicks, holes or screws left in
  walls and ceilings -> painting (touch-up); spills, stains and smudges on counters, mirrors, glass or
  appliances -> cleaning; missing or loose fixtures -> the trade that installs them.
- severity: "safety" if it could hurt someone (exposed wiring, trip hazard, broken glass), "functional" if
  something doesn't work as it should (door won't latch, bulb out, loose bar), else "cosmetic".
- sam_concept: a short noun phrase naming the defective object or area as it looks in photo 1, for an
  image segmentation model (for example "cabinet door", "door strike plate", "wall scuff mark").
- defect_box: [x1, y1, x2, y2] around the defect in photo 1's pixels. The blue dot is exactly where the
  inspector's finger pointed: the defect is at or right next to the dot, so the box must contain or touch
  it. If photo 1 doesn't show the defect there, set defect_box to null and lower confidence.
- confidence: 0 to 1, how sure you are that the item matches both the words and the photo.
- rationale: one short sentence, at most 200 characters."""


def _trim(text: str, limit: int) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"


_REPEAT_RE = re.compile(r"\b((?:\w+\W+){1,3}?)\1+", re.IGNORECASE)
_PHOTO_RE = re.compile(r"[^.]*\b(photos?|in the image|video|visible in|blue dot)\b[^.]*\.?\s*", re.IGNORECASE)


def clean_text(text: str | None) -> str:
    """Collapse whitespace and ASR stutters ("left of left of the sink" -> "left of the sink")."""
    clean = re.sub(r"\s+", " ", text or "").strip()
    prev = None
    while prev != clean:
        prev, clean = clean, _REPEAT_RE.sub(r"\1", clean)
    return clean


def clean_description(text: str | None) -> str:
    """clean_text + drop sentences about the photos (the card summarizes the defect, not the evidence)."""
    clean = clean_text(text)
    kept = _PHOTO_RE.sub("", clean).strip()
    return kept or clean


def model_view(w: int, h: int) -> tuple[float, int, int]:
    """Scale and size of image 1 as the model sees it (llm.prepare_image: long side <= 1600)."""
    s = min(1.0, llm.IMAGE_LONG_SIDE / max(w, h))
    return s, max(1, round(w * s)), max(1, round(h * s))


def clean_box(box: list[float] | None, scale: float, w: int, h: int) -> list[float] | None:
    """Model box (model-view px) -> display px, clamped; None if malformed or tiny."""
    if not box or len(box) != 4:
        return None
    x1, y1, x2, y2 = (float(v) / scale for v in box)
    x1, x2 = sorted((min(max(x1, 0.0), w), min(max(x2, 0.0), w)))
    y1, y2 = sorted((min(max(y1, 0.0), h), min(max(y2, 0.0), h)))
    if x2 - x1 < 4 or y2 - y1 < 4:
        return None
    return [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)]


def _prompt(draft: dict[str, Any], ev: Evidence, rooms: list[str], scale: float, mw: int, mh: int,
            has_pointing: bool) -> str:
    lines = [f'The inspector said: "{draft["quote"]}"']
    if draft.get("text") and draft["text"] != draft["quote"]:
        lines.append(f"The defect, as noted from the narration: {draft['text']}")
    lines.append(f"Room: {draft.get('room') or 'not said'}." + (f" Project rooms: {', '.join(rooms)}." if rooms else ""))
    lines.append("Trades: " + ", ".join(f"{t.value} ({TRADE_LABEL[t]})" for t in Trade) + ".")
    if ev.pointer is not None:
        px, py = round(ev.pointer.x * scale), round(ev.pointer.y * scale)
        lines.append(f"Photo 1 ({mw}x{mh} px) is the clearest frame; the blue dot at ({px}, {py}) marks where "
                     "the finger pointed.")
    else:
        lines.append(f"Photo 1 ({mw}x{mh} px) is the clearest frame from while the inspector spoke; nobody pointed, "
                     "so there is no dot.")
    n = 2
    if has_pointing:
        lines.append(f"Photo {n} is the moment the inspector pointed (the hand may hide the defect).")
        n += 1
    lines.append(f"Photos {n} and {n + 1} are 0.7 s before and after photo 1.")
    return "\n".join(lines)


def write_one(ctx: StageContext, draft: dict[str, Any], ev: Evidence, extra: dict[str, Any]) -> dict[str, Any]:
    """One Spark Open call for one item. Returns the stage-output row (raises on API failure)."""
    root = data_dir()
    raw = root / ev.raw
    w, h = ev.width, ev.height
    images: list[Path] = []
    if ev.pointer is not None:
        marked = ctx.item_dir(draft["item_id"]) / "marked.jpg"
        render_evidence(raw, marked, pointer=(ev.pointer.x, ev.pointer.y))
        images.append(marked)
    else:
        images.append(raw)
    pointing = extra.get("pointing_raw")
    if pointing and (root / pointing).is_file():
        images.append(root / pointing)
    for key in ("context_a", "context_b"):
        if extra.get(key) and (root / extra[key]).is_file():
            images.append(root / extra[key])
    scale, mw, mh = model_view(w, h)
    prompt = _prompt(draft, ev, ctx.rooms, scale, mw, mh, has_pointing=bool(pointing))
    result, rec = llm.structured(
        ItemWrite,
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        images=images,
        reasoning_effort="low",
        max_tokens=MAX_TOKENS,
        stage=Stage.write_items.value,
    )
    return {
        "ok": True,
        "result": result.model_dump(),
        "defect_box": clean_box(result.defect_box, scale, w, h),
        "provider": rec.provider,
        "model": rec.model,
        "model_view": [mw, mh],
    }


def apply_result(item, row: dict[str, Any], rooms: list[str]) -> None:
    r = row["result"]
    item.title = _trim(clean_text(r["title"]), TITLE_MAX) or item.title
    item.description = clean_description(r["description"])
    item.action = _trim(r["action"], ACTION_MAX)
    item.trade = Trade(r["trade"])
    item.room = normalize_room(r.get("room"), rooms) or item.room
    item.location_detail = clean_text(r.get("location_detail")) or None
    item.severity = Severity(r["severity"])
    item.confidence = round(min(1.0, max(0.0, float(r["confidence"]))), 3)
    ev = Evidence.model_validate(item.evidence) if item.evidence else None
    if ev is not None:
        concept = _trim(r.get("sam_concept") or "", CONCEPT_MAX).rstrip("…") or None
        item.evidence = ev.model_copy(update={"sam_concept": concept}).model_dump(mode="json")
    add_history(item, HistoryActor.model, HistoryAction.edited, note="Muse Spark Open wrote the item")


def run(ctx: StageContext) -> StageOutcome:
    drafts = {d["item_id"]: d for d in ctx.output(Stage.fuse)["drafts"]}
    extras = ctx.output(Stage.pick_evidence)["items"]
    with ctx.session() as s:
        items = [i for i in walk_items(ctx, s) if i.id in drafts]
    total = len(items)
    rows: dict[str, dict[str, Any]] = {}
    written = failed = skipped = 0
    ctx.report({"written": 0, "total": total})

    def model_row(item) -> dict[str, Any]:
        """One item's model call (runs on the pool: no DB writes here)."""
        if is_protected(item):
            return {"ok": True, "skipped": "a person edited or sent this item"}
        if not item.evidence:
            return {"ok": False, "error": "no evidence frame"}
        ev = Evidence.model_validate(item.evidence)
        try:
            return write_one(ctx, drafts[item.id], ev, extras.get(item.id, {}))
        except (llm.StructuredOutputError, ApiError) as err:
            log.warning("write_items: %s failed: %s", item.id, err)
            return {"ok": False, "error": str(err)[:500]}

    # PLAN 10.9: the model calls run on a small pool; each result is saved here, one by one, in item order.
    with closing(calls.map_in_order(model_row, items)) as results:
        for task in results:
            item, res = task.item, task.get()
            rows[item.id] = res
            if res.get("skipped"):
                skipped += 1
            elif res["ok"]:
                written += 1
            else:
                failed += 1
            _save_row(ctx, item, res)
            ctx.report({"written": written + failed + skipped, "total": total})
    ctx.save_output(Stage.write_items, {"version": 1, "items": rows})
    return StageOutcome(
        counts={"written": written, "total": total, "failed": failed, "kept": skipped},
        message=f"Muse Spark Open wrote {written} of {total} items",
    )


def _save_row(ctx: StageContext, item, res: dict[str, Any]) -> None:
    """Apply one item's result to its row (in the stage's thread)."""
    if res.get("skipped"):
        return
    with ctx.session() as s:
        row = s.get(type(item), item.id)
        if res["ok"]:
            apply_result(row, res, ctx.rooms)
        else:
            row.status = ItemStatus.needs_review
            row.review_reason = REVIEW_WRITE_FAILED
            add_history(row, HistoryActor.system, HistoryAction.flagged, note=REVIEW_WRITE_FAILED)
        s.add(row)
        s.commit()
