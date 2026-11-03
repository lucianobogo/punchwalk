"""API request/response schemas (Pydantic v2).

Mirrored field-for-field in `web/src/api/types.ts`; change both or neither
(`tests/test_contracts.py` compares them). Mock responses for every GET route live in
`fixtures/mock_api/` (naming convention in its README).

Conventions:
- JSON is snake_case. Every field is always present; optional values are `null`.
- Times are ISO 8601 UTC strings; video times are float seconds into that walk's `display.mp4`.
- Media URLs are root-relative (`/media/...`), served with HTTP range support.
- Boxes and SVG paths are in the pixel space of the image/video they belong to
  (`EvidenceOut.width/height`, `OverlayOut.width/height`).
- Items are numbered per project: `number` is an int; the number tab shows `7`, reports show `#007`.
- All routes are under `/api` except `/healthz` and `/media/...`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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
    ReasoningProvider,
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


class Schema(BaseModel):
    """Base for every API schema: unknown keys are rejected; aliases are used on output."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)


# =========================================================================== shared pieces


class BoxOut(Schema):
    x1: float
    y1: float
    x2: float
    y2: float


class XYOut(Schema):
    """A pixel position (fingertip, "pointed here" marker)."""

    x: float
    y: float


class ItemCountsOut(Schema):
    """Counts behind the punch-list segments. `todo` = open + dispatched + needs_review."""

    todo: int
    open: int  # open + dispatched (UI "Open")
    needs_review: int
    signed_off: int
    all: int


# =========================================================================== health


# GET /healthz
class HealthOut(Schema):
    ok: bool
    version: str
    demo_mode: bool
    reasoning_provider: ReasoningProvider
    time: datetime


# =========================================================================== projects


class TradeContactOut(Schema):
    """A trades-directory entry as returned by the API (the Slack webhook URL is never returned)."""

    trade: Trade
    company: str
    contact: str | None
    email: str | None
    phone: str | None
    slack_channel: str | None
    has_slack_webhook: bool


class TradeContactIn(Schema):
    trade: Trade
    company: str
    contact: str | None = None
    email: str | None = None
    phone: str | None = None
    slack_channel: str | None = None
    slack_webhook: str | None = None


# GET /api/projects/{project_id}
# (also the response of POST /api/projects and PATCH /api/projects/{project_id})
class ProjectOut(Schema):
    id: str
    name: str
    address: str | None
    trades: list[TradeContactOut]
    keywords: list[str]
    rooms: list[str]  # in walk order
    counts: ItemCountsOut
    walk_count: int
    last_walk_at: datetime | None
    created_at: datetime
    updated_at: datetime


# GET /api/projects
class ProjectListOut(Schema):
    projects: list[ProjectOut]


# POST /api/projects  (body)
class ProjectCreate(Schema):
    name: str = Field(min_length=1, max_length=120)
    address: str | None = None
    trades: list[TradeContactIn] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    rooms: list[str] = Field(default_factory=list)


# PATCH /api/projects/{project_id}  (body; omitted fields are unchanged, `trades` replaces the directory)
class ProjectPatch(Schema):
    name: str | None = None
    address: str | None = None
    trades: list[TradeContactIn] | None = None
    keywords: list[str] | None = None
    rooms: list[str] | None = None


# =========================================================================== walks


class ClipOut(Schema):
    order: int
    filename: str
    duration_s: float
    offset_s: float


class StageProgressOut(Schema):
    """One row of the processing checklist. `detail` is the plain line under the step name."""

    stage: Stage
    label: str  # STAGE_LABEL[stage]
    status: StageStatus
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    counts: dict[str, int]
    progress: float | None  # 0..1 for stages with a progress bar, else null
    detail: str | None  # e.g. "Muse Voice Transcribe found 20 lines"
    model_label: str | None  # e.g. "SAM 3.1"; null for in-app stages


class WalkMediaOut(Schema):
    display_mp4: str | None
    poster: str | None


class WalkCountsOut(Schema):
    """For an initial walk: items it created. For a re-walk: items it checked (`items` = checked)."""

    items: int
    points: int  # pointing events found in this walk
    turns: int
    needs_review: int
    signed_off: int
    still_open: int
    not_checked: int


# Row of GET /api/walks and GET /api/projects/{project_id}/walks; header of walk detail
class WalkOut(Schema):
    id: str
    project_id: str
    project_name: str
    kind: WalkKind
    status: WalkStatus
    rewalk_of: str | None
    job_id: str | None  # current or last processing job
    recorded_at: datetime | None
    created_at: datetime
    updated_at: datetime
    clips: list[ClipOut]
    stages: list[StageProgressOut]  # in run order
    media: WalkMediaOut
    duration_s: float | None
    fps: float | None
    width: int | None  # display.mp4 pixels
    height: int | None
    orientation: Orientation | None
    device: Device
    error: str | None  # readable, e.g. "This walk is longer than 10 minutes. Split it into parts..."
    counts: WalkCountsOut


# GET /api/walks  (all projects, newest first; ?project_id= filters)
# GET /api/projects/{project_id}/walks
class WalkListOut(Schema):
    walks: list[WalkOut]


class TurnOut(Schema):
    """One line of the transcript."""

    id: str
    walk_id: str
    idx: int
    start_ms: int
    end_ms: int
    text: str
    kind: TurnKind
    room: str | None
    claimed: ClaimedStatus | None  # status_update turns only
    item_ids: list[str]  # items this line created or updated


class PointOut(Schema):
    """One pointing event: the hand held still while pointing at a defect. Display pixels."""

    id: str
    walk_id: str
    item_id: str | None  # the item it created (initial walk) or checked (re-walk); null until written
    item_number: int | None  # that item's number, for the number tab
    number: int  # 1-based, in time order within this walk
    t: float  # seconds into display.mp4 (middle of the hold)
    frame: int  # sam.mp4 frame index
    fingertip_x: float
    fingertip_y: float
    hand_box: BoxOut
    hold_s: float  # how long the hand was still
    sam_object_ids: list[str]
    mean_conf: float | None
    turn_id: str | None  # the transcript line it belongs to


# POST /api/projects/{project_id}/walks
#   multipart: kind=initial|rewalk, rewalk_of=<walk id>? , clips=<file> (repeated, in walk order)
#   limits: <= 400 MB and <= 10 min in total
class UploadOut(Schema):
    walk_id: str
    job_id: str
    status: WalkStatus


# POST /api/walks/{walk_id}/rewalk-link  (body: link a re-walk to the walk it checks)
class RewalkLinkIn(Schema):
    initial_walk_id: str


class ItemThumbOut(Schema):
    """A tile in the "Items so far" strip."""

    id: str
    number: int
    title: str
    crop_url: str | None


# GET /api/walks/{walk_id}/progress   (poll; GET /api/walks/{walk_id}/events streams the same as SSE)
# POST /api/walks/{walk_id}/retry     (failed walks; same response)
class JobProgressOut(Schema):
    job_id: str
    walk_id: str
    kind: JobKind
    status: JobStatus
    walk_status: WalkStatus
    attempts: int
    stage: Stage | None  # the stage running now (or the one that failed)
    stages: list[StageProgressOut]
    progress: float  # 0..1 overall
    message: str | None  # e.g. "12 items ready"
    items_so_far: list[ItemThumbOut]
    items_expected: int | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime


# =========================================================================== items


class EvidenceOut(Schema):
    t: float
    image_url: str  # full frame with outlines (no burned-in text)
    raw_url: str  # same frame without outlines
    crop_url: str  # 4:3 crop with outlines, for rows and thumbnails
    width: int  # pixel size of image_url/raw_url; SVG paths and boxes use it
    height: int
    defect_svg: str | None  # SVG path `d` of the defect outline (red)
    defect_box: BoxOut | None
    crop_box: BoxOut | None  # region of the frame used for crop_url
    pointer: XYOut | None  # "pointed here" marker (small blue dot); null for voice-only items
    pointing_frame: str | None  # URL: the frame where the hand points, hand outlined (blue)
    hand_box: BoxOut | None  # the hand in pointing_frame (same pixel size as image_url)
    grounding: Grounding
    sam_concept: str | None


class VerificationOut(Schema):
    consistent: bool
    note: str | None  # "Matches the photo and what was said." or the verifier's note
    issues: list[str]
    provider: Provider
    checked_at: datetime | None


class ClosureOut(Schema):
    rewalk_id: str
    rewalk_turn_id: str | None
    quote: str | None
    after_t: float | None
    before_image_url: str | None  # 4:3, from the original walk
    after_image_url: str | None  # 4:3, same framing, from the re-walk
    after_frame_url: str | None  # full re-walk frame
    voice_status: VoiceStatus
    vision_status: VisionStatus
    decision: ClosureDecision
    note: str | None
    decided_at: datetime | None


class HistoryEntryOut(Schema):
    at: datetime
    actor: HistoryActor
    action: HistoryAction
    field: str | None
    from_: str | None = Field(alias="from")
    to: str | None
    note: str | None


class DispatchSummaryOut(Schema):
    """The latest send of this item ("Open, sent to Oakline Millwork Nov 3")."""

    dispatch_id: str
    trade: Trade
    company: str | None
    channel: Channel
    sent_at: datetime
    times_sent: int


# GET /api/items/{item_id}   (also the response of PATCH /api/items/{item_id})
class ItemOut(Schema):
    id: str
    project_id: str
    number: int
    origin_walk_id: str
    point_id: str | None  # null for voice-only items
    turn_ids: list[str]
    title: str
    description: str  # the AI summary
    action: str
    trade: Trade
    room: str | None
    location_detail: str | None
    severity: Severity
    status: ItemStatus
    review_reason: str | None
    quote: str | None  # the inspector's words
    quote_start_s: float | None  # when the quote starts ("Play this moment, 0:14")
    quote_end_s: float | None
    video_url: str | None  # origin walk's display.mp4
    video_start_s: float
    video_end_s: float
    confidence: float | None
    evidence: EvidenceOut | None
    verification: VerificationOut | None
    closure: ClosureOut | None
    history: list[HistoryEntryOut]  # oldest first
    dispatch: DispatchSummaryOut | None
    created_at: datetime
    updated_at: datetime


class FacetsOut(Schema):
    """Counts per filter value over the whole (unfiltered) list."""

    trade: dict[Trade, int]
    severity: dict[Severity, int]
    room: dict[str, int]
    status: dict[ItemStatus, int]


# GET /api/projects/{project_id}/items
#   ?trade=&severity=&room=&status=&q=&sort=walk|severity  (repeat a filter key for OR)
class ItemListOut(Schema):
    project_id: str
    items: list[ItemOut]  # walk order (origin walk, then quote time)
    facets: FacetsOut
    counts: ItemCountsOut
    total: int  # len(items) after filters


# PATCH /api/items/{item_id}  (body; every change is logged in history as a human override)
#   Sign off -> status=closed; Reopen -> status=open; Mark as needs a look -> status=needs_review.
# DELETE /api/items/{item_id} dismisses an item (soft delete) -> 204
class ItemPatch(Schema):
    title: str | None = None
    description: str | None = None
    action: str | None = None
    trade: Trade | None = None
    room: str | None = None
    location_detail: str | None = None
    severity: Severity | None = None
    status: ItemStatus | None = None
    review_reason: str | None = None
    note: str | None = None  # free-text reason, stored on the history entry


# POST /api/items/{item_id}/share
class ShareLinkOut(Schema):
    token: str
    url: str  # "/share/item/<token>"


# GET /api/share/{token}   (public, read-only; page at /share/item/{token})
class ShareItemOut(Schema):
    token: str
    project_name: str
    project_address: str | None
    number: int
    title: str
    description: str
    action: str
    trade: Trade
    company: str | None
    room: str | None
    location_detail: str | None
    severity: Severity
    status: ItemStatus
    quote: str | None
    quote_start_s: float | None
    quote_end_s: float | None
    video_url: str | None
    video_start_s: float
    video_end_s: float
    evidence: EvidenceOut | None
    closure: ClosureOut | None
    walked_at: datetime | None


# GET /api/walks/{walk_id}  (walk + transcript + pointing events + items)
#   items: for an initial walk, the items it created; for a re-walk, the items it checked.
class WalkDetailOut(Schema):
    walk: WalkOut
    turns: list[TurnOut]
    points: list[PointOut]  # in time order
    items: list[ItemOut]


# =========================================================================== overlay


class OverlayHandOut(Schema):
    """The inspector's hand in one overlay frame, around a pointing event."""

    point_id: str
    number: int | None  # the item's number, for the number tab at the fingertip; null if no item
    box: BoxOut
    svg: str  # simplified hand outline path `d`
    fingertip: XYOut | None  # set while the hand is held still (pointing), else null


class OverlayFrameOut(Schema):
    t: float
    hands: list[OverlayHandOut]


class OverlayEventOut(Schema):
    t: float  # the second the item was called out
    item_id: str
    number: int


# GET /api/walks/{walk_id}/overlay
#   Sampled at `fps` (10). Only frames with a hand on screen (around a pointing event) are listed: no
#   frame within 1/fps of the current time means no outlines. Coordinates in width x height (display px).
class OverlayOut(Schema):
    walk_id: str
    fps: float
    width: int
    height: int
    duration_s: float
    frames: list[OverlayFrameOut]
    events: list[OverlayEventOut]


# =========================================================================== trades & dispatch


class TradeSummaryOut(Schema):
    trade: Trade
    contact: TradeContactOut | None  # null when the trade isn't in the directory
    open_count: int  # open + dispatched
    needs_review_count: int
    signed_off_count: int
    last_sent_at: datetime | None


# GET /api/projects/{project_id}/trades
class TradeListOut(Schema):
    project_id: str
    trades: list[TradeSummaryOut]  # trades with a directory entry or any item


class DispatchChannelOut(Schema):
    channel: Channel
    available: bool
    destination: str | None  # "#punch-electrical", "maria.chen@example.com", "Outbox"
    reason: str | None  # why it's unavailable, e.g. "No email on file for Clearview Glass."


class DispatchLineOut(Schema):
    item_id: str
    number: int
    title: str
    line: str  # location plus action: "Hallway. Straighten and tighten the plate."
    url: str  # "View item and video" link (share page)


# GET /api/projects/{project_id}/trades/{trade}/preview
#   The items that would be sent now: open + dispatched items of this trade (needs_review waits).
class DispatchPreviewOut(Schema):
    project_id: str
    trade: Trade
    contact: TradeContactOut | None
    channel: Channel  # suggested channel
    channels: list[DispatchChannelOut]
    heading: str  # bold first line: "Unit 2B final punch: 2 electrical items"
    lines: list[DispatchLineOut]
    subject: str  # email subject
    message_text: str  # plain text (email, outbox, copy link)
    slack_text: str  # Slack mrkdwn
    item_ids: list[str]


# POST /api/projects/{project_id}/trades/{trade}/dispatch  (body)
class DispatchCreate(Schema):
    channel: Channel
    item_ids: list[str] = Field(min_length=1)


# Response of POST .../dispatch; rows of GET .../dispatches
class DispatchOut(Schema):
    id: str
    project_id: str
    trade: Trade
    company: str | None
    channel: Channel
    destination: str | None
    item_ids: list[str]
    sent_at: datetime
    ok: bool
    error: str | None
    payload: dict[str, Any]


# GET /api/projects/{project_id}/trades/{trade}/dispatches   (sent history, newest first)
class DispatchListOut(Schema):
    project_id: str
    trade: Trade | None
    dispatches: list[DispatchOut]


# =========================================================================== re-walk


class RewalkItemOut(Schema):
    item_id: str
    number: int
    title: str
    room: str | None
    trade: Trade
    status: ItemStatus
    decision: ClosureDecision
    reason: str | None  # "Marked fixed on the re-walk, but the defect is still visible."
    quote: str | None  # what was said on the re-walk
    before_image_url: str | None
    after_image_url: str | None
    before_at: datetime | None  # when the original walk was recorded
    after_at: datetime | None  # when the re-walk was recorded


class RewalkCountsOut(Schema):
    checked: int
    signed_off: int
    still_open: int
    needs_a_look: int
    not_checked: int


# GET /api/walks/{walk_id}/rewalk-summary  (re-walks only; 404 for initial walks)
class RewalkSummaryOut(Schema):
    walk_id: str
    initial_walk_id: str
    project_id: str
    recorded_at: datetime | None
    counts: RewalkCountsOut
    headline: str  # "6 signed off. 3 still open. 1 needs a look."
    needs_a_look: list[RewalkItemOut]
    signed_off: list[RewalkItemOut]
    still_open: list[RewalkItemOut]
    not_checked: list[RewalkItemOut]


# =========================================================================== model calls & processing


class ModelCallOut(Schema):
    id: str
    walk_id: str | None
    item_id: str | None
    stage: Stage | None
    model: str
    model_label: str  # MODEL_LABEL[model]
    provider: Provider
    latency_ms: int
    tokens_in: int | None
    tokens_out: int | None
    frames: int | None
    est_cost_usd: float
    cached: bool
    ok: bool
    error: str | None
    started_at: datetime


# GET /api/walks/{walk_id}/model-calls  (oldest first)
class ModelCallListOut(Schema):
    walk_id: str
    calls: list[ModelCallOut]


class ProcessingStepOut(Schema):
    stage: Stage
    title: str  # STAGE_TITLE[stage]
    model_label: str | None  # null for in-app stages
    provider: Provider | None
    detail: str  # "11 points found across the whole video"
    duration_ms: int
    calls: int
    fallback_calls: int  # calls served by meta-fallback
    cost_usd: float


class EvalMetricOut(Schema):
    key: str  # defects_found | right_trade | false_claims_caught | ...
    label: str  # "Defects found"
    value: int
    total: int


class EvalSummaryOut(Schema):
    source: str  # "eval/report.md" (or "mock" in fixtures)
    updated_at: datetime | None
    metrics: list[EvalMetricOut]


# GET /api/walks/{walk_id}/processing
class ProcessingSummaryOut(Schema):
    walk_id: str
    kind: WalkKind
    status: WalkStatus
    total_duration_ms: int
    total_cost_usd: float
    models_used: list[str]  # model labels, in first-use order
    steps: list[ProcessingStepOut]
    calls: list[ModelCallOut]
    eval: EvalSummaryOut | None  # null when eval/report.md doesn't exist: hide the section


# GET /api/projects/{project_id}/report.pdf -> application/pdf (no schema)
# GET /media/{path} -> files under DATA_DIR, with HTTP range support (no schema)
