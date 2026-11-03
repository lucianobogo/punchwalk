"""Shared plumbing for pipeline stages: walk paths, stage outputs, stable ids, the stage context.

Layout under DATA_DIR (CLAUDE.md "Media layout"):

    walks/<walk_id>/clips/<order:02d>_<original name>   uploaded clips (Track C writes these)
    walks/<walk_id>/{display.mp4, sam.mp4, sam_part_<k>.mp4, audio.wav, meta.json, poster.jpg}
    walks/<walk_id>/stages/<stage>.json                  stage outputs that are not DB rows
    walks/<walk_id>/items/<item_id>/{raw,context_a,context_b,crop}.jpg   evidence frames
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlmodel import Session

from punchwalk.config import get_settings
from punchwalk.core.enums import Stage, WalkKind
from punchwalk.pipeline.ingest import IngestMeta

SessionFactory = Callable[[], AbstractContextManager[Session]]

STAGES_DIR = "stages"
CLIPS_DIR = "clips"
ITEMS_DIR = "items"
POSTER_NAME = "poster.jpg"


class StageInputMissing(RuntimeError):
    """A stage needs the output of an earlier stage that is not there."""


def data_dir() -> Path:
    return Path(get_settings().DATA_DIR)


def walk_dir(walk_id: str) -> Path:
    return data_dir() / "walks" / walk_id


def rel_to_data(path: Path) -> str:
    """Path relative to DATA_DIR (how media paths are stored in the DB)."""
    path = Path(path).resolve()
    try:
        return path.relative_to(data_dir().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def stable_id(prefix: str, *parts: object) -> str:
    """Deterministic id in the `new_id` format (`prefix_` + 12 hex), so re-runs upsert rows."""
    digest = hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()
    return f"{prefix}_{digest[:12]}"


def stage_output_path(wdir: Path, stage: Stage | str) -> Path:
    return Path(wdir) / STAGES_DIR / f"{Stage(stage).value}.json"


def write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, path)
    return path


def write_stage_output(wdir: Path, stage: Stage | str, data: dict[str, Any]) -> Path:
    return write_json(stage_output_path(wdir, stage), data)


def read_stage_output(wdir: Path, stage: Stage | str) -> dict[str, Any]:
    path = stage_output_path(wdir, stage)
    if not path.is_file():
        raise StageInputMissing(
            f"{path.name} is missing in {path.parent}: run the '{Stage(stage).value}' stage first "
            "(re-run the walk with force=true if its state says done)"
        )
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class StageOutcome:
    """What a stage reports: counts for the checklist plus an optional readable note."""

    counts: dict[str, int] = field(default_factory=dict)
    message: str | None = None


@dataclass
class StageContext:
    """Everything a stage needs about the walk it runs on."""

    walk_id: str
    project_id: str
    kind: WalkKind
    walk_dir: Path
    session_factory: SessionFactory
    keywords: list[str] = field(default_factory=list)
    rooms: list[str] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)
    rewalk_of: str | None = None
    force: bool = False
    _meta: IngestMeta | None = field(default=None, repr=False)

    @property
    def meta(self) -> IngestMeta:
        if self._meta is None:
            if not (self.walk_dir / "meta.json").is_file():
                raise StageInputMissing(f"meta.json is missing in {self.walk_dir}: run the ingest stage first")
            self._meta = IngestMeta.load(self.walk_dir)
        return self._meta

    def reset_meta(self) -> None:
        self._meta = None

    def session(self) -> AbstractContextManager[Session]:
        return self.session_factory()

    def item_dir(self, item_id: str) -> Path:
        return self.walk_dir / ITEMS_DIR / item_id

    def output(self, stage: Stage) -> dict[str, Any]:
        return read_stage_output(self.walk_dir, stage)

    def save_output(self, stage: Stage, data: dict[str, Any]) -> Path:
        return write_stage_output(self.walk_dir, stage, data)
