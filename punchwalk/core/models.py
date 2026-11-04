"""SQLModel tables for the Punchwalk domain (CLAUDE.md "Domain model").

Conventions:
- Ids are strings with a type prefix (`prj_`, `wlk_`, `trn_`, `pnt_`, `itm_`, `dsp_`, `mc_`, `job_`);
  use `new_id(prefix)`.
- Enums are stored by **value** (`"meta-fallback"`, not the member name) in VARCHAR columns.
- Lists and nested objects are JSON columns. Their shapes are the small Pydantic models below
  (`ClipInfo`, `StageState`, `Evidence`, ...): build them with the model, store
  `model.model_dump(mode="json")`, and read them back with `Model.model_validate(row.field)`.
- Media paths in JSON columns are relative to `DATA_DIR` (the API turns them into `/media/...` URLs).
- Coordinates (boxes, SVG paths) are stored in **display** pixels (`display.mp4` / evidence frame),
  already scaled from SAM-frame coordinates by the pipeline.
- Timestamps are timezone-aware UTC.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict
from pydantic import Field as PField
from sqlalchemy import JSON, Column, DateTime, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, SQLModel

from punchwalk.core.enums import (
    Channel,
    ClaimedStatus,
    ClosureDecision,
    Device,
    Grounding,
    HistoryAction,
    HistoryActor,
    ItemStatus,
    JobKind,
    JobStatus,
    Orientation,
    Provider,
    Severity,
    Stage,
    StageStatus,
    Trade,
    TurnKind,
    VisionStatus,
    VoiceStatus,
    WalkKind,
    WalkStatus,
)

# --------------------------------------------------------------------------- helpers


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    """A short random id with a type prefix, e.g. `itm_3f9c0a1b2d4e`."""
    return f"{prefix}_{uuid4().hex[:12]}"


def _enum_field(
    enum_cls: type[Enum], default: Any = ..., *, nullable: bool = False, index: bool = False
) -> Any:
    column = Column(
        SAEnum(
            enum_cls,
            values_callable=lambda e: [m.value for m in e],
            native_enum=False,
            validate_strings=True,
            length=32,
        ),
        nullable=nullable,
        index=index,
    )
    return Field(default=default, sa_column=column)


def _json_list() -> Any:
    return Field(default_factory=list, sa_column=Column(JSON, nullable=False))


def _json_dict() -> Any:
    return Field(default_factory=dict, sa_column=Column(JSON, nullable=False))


def _json_optional() -> Any:
    return Field(default=None, sa_column=Column(JSON, nullable=True))


def _created_at() -> Any:
    return Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


def _updated_at() -> Any:
    return Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False, onupdate=utcnow),
    )


def _optional_dt() -> Any:
    return Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


# --------------------------------------------------------------------------- JSON column shapes


class _Shape(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class TradeContact(_Shape):
    """One entry in a project's trades directory (`Project.trades`)."""

    trade: Trade
    company: str
    contact: str | None = None
    email: str | None = None
    phone: str | None = None
    slack_channel: str | None = None  # display name, e.g. "#punch-electrical"
    slack_webhook: str | None = None  # secret-ish; never returned by the API


class ClipInfo(_Shape):
    """One uploaded clip of a walk (`Walk.clips`), in walk order."""

    order: int
    filename: str
    duration_s: float
    offset_s: float  # start of this clip in the concatenated walk


class StageState(_Shape):
    """Progress + timing of one pipeline stage (`Walk.stages[stage]`)."""

    stage: Stage
    status: StageStatus = StageStatus.pending
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    counts: dict[str, int] = PField(default_factory=dict)  # e.g. {"lines": 20} / {"written": 7, "total": 12}
    error: str | None = None
    message: str | None = None  # plain line for the checklist ("Following your hand (SAM 3.1): 11 points found")


class WalkMediaPaths(_Shape):
    """Derived media for a walk (`Walk.media`), relative to DATA_DIR. Produced by ingest (PLAN 0.5)."""

    display_mp4: str | None = None
    sam_mp4: str | None = None
    audio_wav: str | None = None
    meta_json: str | None = None
    poster: str | None = None


class Box(_Shape):
    x1: float
    y1: float
    x2: float
    y2: float


class XY(_Shape):
    """A pixel position, e.g. the fingertip or the "pointed here" marker."""

    x: float
    y: float


class Evidence(_Shape):
    """Evidence frame for an item (`Item.evidence`). Paths relative to DATA_DIR; coordinates in `image` pixels."""

    t: float  # seconds into the walk
    image: str  # full frame: defect outline (red, 30% fill) + a small blue dot at `pointer`, no text
    raw: str  # the same frame without outlines (for the "Outlines on" toggle)
    crop: str  # 4:3 crop centered on the pointer + defect, outlines drawn
    width: int
    height: int
    defect_svg: str | None = None  # SVG path `d`, frame pixels
    defect_box: Box | None = None
    crop_box: Box | None = None  # region of the frame used for `crop`
    pointer: XY | None = None  # where the finger pointed, mapped into this frame; None for voice-only items
    pointing_frame: str | None = None  # the frame where the hand points, hand outlined (blue)
    hand_box: Box | None = None  # the hand in `pointing_frame` (same pixel size as `image`)
    grounding: Grounding = Grounding.none
    sam_concept: str | None = None  # noun phrase Spark Open asked SAM to outline


class Verification(_Shape):
    """Result of the Spark Open self-check (`Item.verification`, PLAN 2.3)."""

    consistent: bool
    note: str | None = None
    issues: list[str] = PField(default_factory=list)
    provider: Provider = Provider.baseten
    checked_at: datetime | None = None


class Closure(_Shape):
    """Re-walk outcome for an item (`Item.closure`, PLAN 7.1-7.4). Paths relative to DATA_DIR."""

    rewalk_id: str
    rewalk_turn_id: str | None = None
    quote: str | None = None  # what was said on the re-walk
    after_t: float | None = None  # seconds into the re-walk video
    after_image: str | None = None  # 4:3 crop of the re-walk frame, same framing as `Evidence.crop`
    after_frame: str | None = None  # full re-walk frame
    before_after: str | None = None  # items/<id>/before_after.jpg: before and after side by side (pipeline)
    voice_status: VoiceStatus = VoiceStatus.not_mentioned
    vision_status: VisionStatus = VisionStatus.not_checked
    decision: ClosureDecision = ClosureDecision.not_checked
    note: str | None = None
    decided_at: datetime | None = None


class HistoryEvent(_Shape):
    """One entry of `Item.history`. `from`/`to` are aliases (Python keyword)."""

    at: datetime
    actor: HistoryActor
    action: HistoryAction
    field: str | None = None  # which field changed, for edits/status changes
    from_: str | None = PField(default=None, alias="from")
    to: str | None = None
    note: str | None = None


# --------------------------------------------------------------------------- tables


class Project(SQLModel, table=True):
    __tablename__ = "projects"

    id: str = Field(default_factory=lambda: new_id("prj"), primary_key=True)
    name: str
    address: str | None = None
    trades: list[dict[str, Any]] = _json_list()  # [TradeContact]
    keywords: list[str] = _json_list()
    rooms: list[str] = _json_list()  # in walk order
    created_at: datetime = _created_at()
    updated_at: datetime = _updated_at()


class Walk(SQLModel, table=True):
    __tablename__ = "walks"

    id: str = Field(default_factory=lambda: new_id("wlk"), primary_key=True)
    project_id: str = Field(foreign_key="projects.id", index=True)
    kind: WalkKind = _enum_field(WalkKind, WalkKind.initial)
    status: WalkStatus = _enum_field(WalkStatus, WalkStatus.uploaded, index=True)
    rewalk_of: str | None = Field(default=None, foreign_key="walks.id")  # initial walk this re-walk checks
    clips: list[dict[str, Any]] = _json_list()  # [ClipInfo]
    stages: dict[str, Any] = _json_dict()  # {stage: StageState}
    media: dict[str, Any] = _json_dict()  # WalkMediaPaths
    duration_s: float | None = None
    fps: float | None = None
    width: int | None = None  # display.mp4 pixels, after rotation
    height: int | None = None
    orientation: Orientation | None = _enum_field(Orientation, None, nullable=True)
    device: Device = _enum_field(Device, Device.unknown)
    recorded_at: datetime | None = _optional_dt()  # from clip metadata, else upload time
    error: str | None = None
    created_at: datetime = _created_at()
    updated_at: datetime = _updated_at()


class Turn(SQLModel, table=True):
    """One transcribed utterance (Muse Voice Transcribe turn) with its classification."""

    __tablename__ = "turns"

    id: str = Field(default_factory=lambda: new_id("trn"), primary_key=True)
    walk_id: str = Field(foreign_key="walks.id", index=True)
    idx: int = 0  # order within the walk
    start_ms: int
    end_ms: int
    text: str
    kind: TurnKind = _enum_field(TurnKind, TurnKind.other)
    room: str | None = None
    claimed: ClaimedStatus | None = _enum_field(ClaimedStatus, None, nullable=True)  # status_update only
    defects: list[str] = _json_list()  # short defect descriptions from classification
    created_at: datetime = _created_at()


class Point(SQLModel, table=True):
    """One pointing event: the inspector's hand held still (~2 s) while pointing at a defect (PLAN 1.3).

    Found from the SAM 3.1 hand track; the fingertip is where the defect is. Display pixels.
    """

    __tablename__ = "points"

    id: str = Field(default_factory=lambda: new_id("pnt"), primary_key=True)
    walk_id: str = Field(foreign_key="walks.id", index=True)
    number: int  # 1-based, in time order within the walk
    t: float  # seconds on the display timeline (middle of the hold)
    frame: int  # sam.mp4 frame index at `t`
    fingertip_x: float
    fingertip_y: float
    hand_box: dict[str, Any] = _json_dict()  # Box, display pixels
    hold_s: float  # how long the hand was still
    sam_object_ids: list[str] = _json_list()  # "<clip>:<sam id>", e.g. "a:3" (ids reset per clip)
    mean_conf: float | None = None
    turn_id: str | None = None  # Turn.id of the mention it belongs to (no FK: stages persist independently)
    created_at: datetime = _created_at()


class Item(SQLModel, table=True):
    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("project_id", "number", name="uq_items_project_number"),)

    id: str = Field(default_factory=lambda: new_id("itm"), primary_key=True)
    project_id: str = Field(foreign_key="projects.id", index=True)
    number: int  # project-scoped; shown as "#014" in reports, "14" on the number tab
    origin_walk_id: str = Field(foreign_key="walks.id", index=True)
    point_id: str | None = Field(default=None, foreign_key="points.id")  # None for voice-only items
    turn_ids: list[str] = _json_list()
    title: str
    description: str = ""
    action: str = ""
    trade: Trade = _enum_field(Trade, Trade.general, index=True)
    room: str | None = None
    location_detail: str | None = None
    severity: Severity = _enum_field(Severity, Severity.cosmetic)
    status: ItemStatus = _enum_field(ItemStatus, ItemStatus.open, index=True)
    review_reason: str | None = None
    quote: str | None = None  # the inspector's original words (joined turn texts)
    quote_start_s: float | None = None
    quote_end_s: float | None = None
    video_start_s: float = 0.0
    video_end_s: float = 0.0
    confidence: float | None = None
    evidence: dict[str, Any] | None = _json_optional()  # Evidence
    verification: dict[str, Any] | None = _json_optional()  # Verification
    closure: dict[str, Any] | None = _json_optional()  # Closure
    history: list[dict[str, Any]] = _json_list()  # [HistoryEvent], by_alias dump
    share_token: str | None = Field(default=None, unique=True)
    dismissed_at: datetime | None = _optional_dt()  # soft delete ("Dismiss item")
    created_at: datetime = _created_at()
    updated_at: datetime = _updated_at()


class Dispatch(SQLModel, table=True):
    """One send of a trade's items over one channel."""

    __tablename__ = "dispatches"

    id: str = Field(default_factory=lambda: new_id("dsp"), primary_key=True)
    project_id: str = Field(foreign_key="projects.id", index=True)
    trade: Trade = _enum_field(Trade, index=True)
    item_ids: list[str] = _json_list()
    channel: Channel = _enum_field(Channel, Channel.outbox)
    destination: str | None = None  # "#punch-electrical", an email address, or "Outbox"
    sent_at: datetime = _created_at()
    ok: bool = True
    error: str | None = None
    payload: dict[str, Any] = _json_dict()  # exactly what was sent
    created_at: datetime = _created_at()


class ModelCall(SQLModel, table=True):
    """One external model call (live or cached), mirrored from `punchwalk.clients.calls.CallRecord`."""

    __tablename__ = "model_calls"

    id: str = Field(default_factory=lambda: new_id("mc"), primary_key=True)
    walk_id: str | None = Field(default=None, foreign_key="walks.id", index=True)
    item_id: str | None = None
    stage: Stage | None = _enum_field(Stage, None, nullable=True)
    model: str
    provider: Provider = _enum_field(Provider)
    endpoint: str | None = None
    latency_ms: int = 0
    tokens_in: int | None = None
    tokens_out: int | None = None
    frames: int | None = None
    units: float | None = None
    unit_kind: str | None = None
    est_cost_usd: float = 0.0
    cached: bool = False
    ok: bool = True
    error: str | None = None
    cache_key: str | None = None
    created_at: datetime = _created_at()


class Job(SQLModel, table=True):
    """A unit of background work polled by the in-process worker."""

    __tablename__ = "jobs"

    id: str = Field(default_factory=lambda: new_id("job"), primary_key=True)
    kind: JobKind = _enum_field(JobKind, JobKind.process_walk)
    walk_id: str | None = Field(default=None, foreign_key="walks.id", index=True)
    status: JobStatus = _enum_field(JobStatus, JobStatus.queued, index=True)
    attempts: int = 0
    force: bool = False  # re-run completed stages
    progress: dict[str, Any] = _json_dict()  # {"stage": ..., "counts": {...}, "message": ...}
    error: str | None = None
    created_at: datetime = _created_at()
    started_at: datetime | None = _optional_dt()
    finished_at: datetime | None = _optional_dt()
    updated_at: datetime = _updated_at()


ALL_TABLES: tuple[type[SQLModel], ...] = (Project, Walk, Turn, Point, Item, Dispatch, ModelCall, Job)
