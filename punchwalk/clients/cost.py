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
    pass


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


def append_ledger(record) -> None:  # record: CallRecord (import cycle avoided)
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = record.model_dump(
        include={
            "ts", "provider", "model", "endpoint", "units", "unit_kind", "est_cost_usd", "walk_id",
            "stage", "latency_ms", "cached", "ok", "error", "tokens_in", "tokens_out", "frames",
        }
    )
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, sort_keys=True) + "\n")


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


def assert_under_cap(next_cost_usd: float = 0.0) -> float:
    """Raise CostCapReached when spend (plus the next call's estimate) reaches COST_CAP_USD."""
    cap = get_settings().COST_CAP_USD
    spent = total_usd()
    if spent + next_cost_usd >= cap:
        raise CostCapReached(
            f"Live spend ${spent:.2f} (+${next_cost_usd:.2f} for this call) reaches the "
            f"${cap:.2f} cap in {ledger_path()}. Stop making live calls."
        )
    return spent
