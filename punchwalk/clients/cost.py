"""Price estimates and the cost ledger (`DATA_DIR/cost_ledger.jsonl`, one line per live call).

Prices (Nov 3, 2026):
- SAM 3.1 video: $0.20 per 1k frames, 50-frame minimum per request (Meta docs / CLAUDE.md).
- SAM 3.1 image: $2.50 per 1k images.
- Muse Voice Transcribe: $0.18 per hour of audio (speech-to-text doc), billed in whole seconds.
- Muse Spark Open on Baseten: $1.25 / 1M input, $4.25 / 1M output tokens (Baseten /v1/models catalog).
- Muse Spark 1.3 (Meta, fallback only): no published price found; conservative estimate
  $3.00 / 1M input, $15.00 / 1M output.
"""

from __future__ import annotations

import json
import math
import threading
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from pathlib import Path

from punchwalk.config import get_settings

SAM_VIDEO_PER_FRAME = 0.20 / 1000
SAM_VIDEO_MIN_FRAMES = 50
SAM_IMAGE_PER_IMAGE = 2.50 / 1000
ASR_PER_SECOND = 0.18 / 3600
SPARK_OPEN_IN_PER_TOKEN = 1.25 / 1_000_000
SPARK_OPEN_OUT_PER_TOKEN = 4.25 / 1_000_000
SPARK_META_IN_PER_TOKEN = 3.00 / 1_000_000  # estimate, see module docstring
SPARK_META_OUT_PER_TOKEN = 15.00 / 1_000_000  # estimate


class CostCapReached(RuntimeError):
    """`daily` is True when it's today's cap (COST_DAILY_CAP_USD), not the total (COST_CAP_USD)."""

    def __init__(self, message: str, *, daily: bool = False) -> None:
        super().__init__(message)
        self.daily = daily


def eastern() -> tzinfo:
    """America/New_York (the daily cap's calendar day); a fixed UTC-5 if the tz database is missing."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("America/New_York")
    except Exception:  # noqa: BLE001 (ZoneInfoNotFoundError, or no tzdata at all)
        return timezone(timedelta(hours=-5), "EST")


def sam_video_cost(frames: int) -> float:
    return max(int(frames), SAM_VIDEO_MIN_FRAMES) * SAM_VIDEO_PER_FRAME


def sam_image_cost(images: int = 1) -> float:
    return images * SAM_IMAGE_PER_IMAGE


def asr_cost(audio_ms: int) -> float:
    return math.floor(max(audio_ms, 0) / 1000) * ASR_PER_SECOND


def llm_cost(provider: str, tokens_in: int | None, tokens_out: int | None) -> float:
    tin, tout = tokens_in or 0, tokens_out or 0
    if provider == "baseten":
        return tin * SPARK_OPEN_IN_PER_TOKEN + tout * SPARK_OPEN_OUT_PER_TOKEN
    return tin * SPARK_META_IN_PER_TOKEN + tout * SPARK_META_OUT_PER_TOKEN


def ledger_path() -> Path:
    return get_settings().ledger_path


_ledger_lock = threading.Lock()  # per-item calls run on a thread pool (calls.map_in_order)


def append_ledger(record) -> None:  # record: CallRecord (import cycle avoided)
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = record.model_dump(
        include={
            "ts", "provider", "model", "endpoint", "units", "unit_kind", "est_cost_usd", "walk_id",
            "stage", "latency_ms", "cached", "ok", "error", "tokens_in", "tokens_out", "frames",
        }
    )
    text = json.dumps(line, sort_keys=True) + "\n"
    with _ledger_lock, path.open("a", encoding="utf-8") as f:
        f.write(text)


def read_ledger(path: Path | None = None) -> list[dict]:
    p = path or ledger_path()
    if not p.exists():
        return []
    rows = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if raw:
            try:
                rows.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    return rows


def total_usd(path: Path | None = None) -> float:
    return round(sum(float(r.get("est_cost_usd") or 0.0) for r in read_ledger(path) if not r.get("cached")), 6)


def _row_day(row: dict, tz: tzinfo) -> date | None:
    try:
        ts = datetime.fromisoformat(str(row.get("ts")))
    except ValueError:
        return None
    return (ts if ts.tzinfo else ts.replace(tzinfo=UTC)).astimezone(tz).date()


def spend_on(day: date, rows: list[dict] | None = None) -> float:
    """Live spend whose ledger timestamp falls on `day` (America/New_York)."""
    tz = eastern()
    rows = read_ledger() if rows is None else rows
    return round(sum(float(r.get("est_cost_usd") or 0.0) for r in rows
                     if not r.get("cached") and _row_day(r, tz) == day), 6)  # fmt: skip


def today_eastern(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(eastern()).date()


def spend_today_usd(now: datetime | None = None) -> float:
    return spend_on(today_eastern(now))


DAILY_CAP_SENTENCE = "Today's live model budget is used up. It resets at midnight Eastern; try again tomorrow."


def daily_cap_reached(now: datetime | None = None) -> bool:
    """True when today's live spend (America/New_York) has reached COST_DAILY_CAP_USD."""
    return spend_today_usd(now) >= get_settings().COST_DAILY_CAP_USD


def seconds_until_midnight_eastern(now: datetime | None = None) -> int:
    local = (now or datetime.now(UTC)).astimezone(eastern())
    midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), tzinfo=eastern())
    return max(60, int((midnight - local).total_seconds()))


def assert_under_cap(next_cost_usd: float = 0.0, *, now: datetime | None = None) -> float:
    """Raise CostCapReached when spend (plus the next call's estimate) reaches a cap.

    Two caps: COST_CAP_USD on the whole ledger and COST_DAILY_CAP_USD on today's spend (America/New_York
    calendar day). Returns the total spent so far.
    """
    s = get_settings()
    rows = read_ledger()
    spent = round(sum(float(r.get("est_cost_usd") or 0.0) for r in rows if not r.get("cached")), 6)
    if spent + next_cost_usd >= s.COST_CAP_USD:
        raise CostCapReached(
            f"Live spend ${spent:.2f} (+${next_cost_usd:.2f} for this call) reaches the "
            f"${s.COST_CAP_USD:.2f} cap in {ledger_path()}. Stop making live calls."
        )
    today = spend_on(today_eastern(now), rows)
    if today + next_cost_usd >= s.COST_DAILY_CAP_USD:
        raise CostCapReached(
            f"Today's live model budget (${s.COST_DAILY_CAP_USD:.2f}) is used up: ${today:.2f} spent "
            f"(+${next_cost_usd:.2f} for this call). It resets at midnight Eastern; try again tomorrow.",
            daily=True,
        )
    return spent
