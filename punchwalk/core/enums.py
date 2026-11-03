"""Domain enums and the UI label maps.

Every enum here is mirrored as a string-literal union in `web/src/api/types.ts`, and every
`*_LABEL` dict is mirrored as a TS constant with the same name. `tests/test_contracts.py`
fails if the two drift. Change both or neither.

Enum member names equal their values (except `Provider.meta_fallback`, whose value has a
hyphen), so `Trade.electrical == "electrical"`.
"""

from __future__ import annotations

from enum import StrEnum


class Trade(StrEnum):
    electrical = "electrical"
    plumbing = "plumbing"
    drywall = "drywall"
    painting = "painting"
    carpentry_millwork = "carpentry_millwork"
    doors_hardware = "doors_hardware"
    flooring = "flooring"
    windows_glazing = "windows_glazing"
    hvac = "hvac"
    appliances = "appliances"
    cleaning = "cleaning"
    general = "general"


class Severity(StrEnum):
    cosmetic = "cosmetic"
    functional = "functional"
    safety = "safety"


class ItemStatus(StrEnum):
    """Internal item status. The UI shows three: Open (open + dispatched), Needs a look, Signed off."""

    open = "open"
    dispatched = "dispatched"
    closed = "closed"
    needs_review = "needs_review"


class WalkKind(StrEnum):
    initial = "initial"
    rewalk = "rewalk"


class WalkStatus(StrEnum):
    uploaded = "uploaded"
    processing = "processing"
    ready = "ready"
    failed = "failed"


class TurnKind(StrEnum):
    location = "location"
    defect = "defect"
    status_update = "status_update"
    other = "other"


class Grounding(StrEnum):
    """How the defect outline was found (PLAN 2.2). `tape_only` = no defect outline."""

    sam_concept = "sam_concept"
    sam_box = "sam_box"
    tape_only = "tape_only"


class Channel(StrEnum):
    slack = "slack"
    email = "email"
    outbox = "outbox"


class Provider(StrEnum):
    """Who served a model call. `meta-fallback` = Muse Spark 1.3 after Baseten failed twice."""

    meta = "meta"
    baseten = "baseten"
    meta_fallback = "meta-fallback"


class ReasoningProvider(StrEnum):
    """`REASONING_PROVIDER` setting."""

    baseten = "baseten"
    meta = "meta"


class JobKind(StrEnum):
    process_walk = "process_walk"


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"


class StageStatus(StrEnum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"
    skipped = "skipped"


class Stage(StrEnum):
    """Pipeline stages in run order (CLAUDE.md "Pipeline overview")."""

    ingest = "ingest"
    transcribe = "transcribe"
    track_tape = "track_tape"
    merge_marks = "merge_marks"
    classify_turns = "classify_turns"
    fuse = "fuse"
    pick_evidence = "pick_evidence"
    write_items = "write_items"
    ground_defects = "ground_defects"
    verify = "verify"
    resolve_closures = "resolve_closures"
    persist = "persist"


class Orientation(StrEnum):
    portrait = "portrait"
    landscape = "landscape"


class Device(StrEnum):
    glasses = "glasses"
    phone = "phone"
    unknown = "unknown"


class HistoryActor(StrEnum):
    human = "human"
    system = "system"
    model = "model"


class HistoryAction(StrEnum):
    created = "created"
    verified = "verified"
    flagged = "flagged"
    dispatched = "dispatched"
    edited = "edited"
    status_changed = "status_changed"
    rewalk_checked = "rewalk_checked"
    dismissed = "dismissed"
    noted = "noted"  # a system remark with no state change, e.g. "Not sent: no email on file"


class ClaimedStatus(StrEnum):
    """What a re-walk `status_update` turn claims (PLAN 1.4)."""

    fixed = "fixed"
    not_fixed = "not_fixed"
    unclear = "unclear"


class VoiceStatus(StrEnum):
    """Closure: what the inspector said about the item on the re-walk."""

    fixed = "fixed"
    not_fixed = "not_fixed"
    unclear = "unclear"
    not_mentioned = "not_mentioned"


class VisionStatus(StrEnum):
    """Closure: what the re-walk frame shows (PLAN 7.2)."""

    fixed = "fixed"
    still_visible = "still_visible"
    unclear = "unclear"
    not_checked = "not_checked"


class ClosureDecision(StrEnum):
    """Closure decision table (PLAN 7.3)."""

    signed_off = "signed_off"
    still_open = "still_open"
    needs_review = "needs_review"
    not_checked = "not_checked"


# --------------------------------------------------------------------------- stage order

STAGES_INITIAL: tuple[Stage, ...] = (
    Stage.ingest,
    Stage.transcribe,
    Stage.track_tape,
    Stage.merge_marks,
    Stage.classify_turns,
    Stage.fuse,
    Stage.pick_evidence,
    Stage.write_items,
    Stage.ground_defects,
    Stage.verify,
    Stage.persist,
)
STAGES_REWALK: tuple[Stage, ...] = (
    Stage.ingest,
    Stage.transcribe,
    Stage.track_tape,
    Stage.merge_marks,
    Stage.classify_turns,
    Stage.fuse,
    Stage.pick_evidence,
    Stage.resolve_closures,
    Stage.persist,
)

# --------------------------------------------------------------------------- model ids

MODEL_ASR = "muse-voice-transcribe-1.0"
MODEL_SAM = "sam-3.1"
MODEL_SPARK_OPEN = "meta-models/muse-spark-open-1026"
MODEL_SPARK_HOSTED = "muse-spark-1.3"

# --------------------------------------------------------------------------- fixed review reasons

REVIEW_NO_DESCRIPTION = "Tape mark with no description"
REVIEW_FALSE_FIX = "Marked fixed on the re-walk, but the defect is still visible."
NOT_CHECKED_ON_REWALK = "Not checked on re-walk"

# --------------------------------------------------------------------------- UI labels
# Mirrored in web/src/api/types.ts with the same names (DESIGN.md "Words").

STATUS_LABEL: dict[ItemStatus, str] = {
    ItemStatus.open: "Open",
    ItemStatus.dispatched: "Open",
    ItemStatus.needs_review: "Needs a look",
    ItemStatus.closed: "Signed off",
}

SEVERITY_LABEL: dict[Severity, str] = {
    Severity.cosmetic: "Cosmetic",
    Severity.functional: "Functional",
    Severity.safety: "Safety",
}

TRADE_LABEL: dict[Trade, str] = {
    Trade.electrical: "Electrical",
    Trade.plumbing: "Plumbing",
    Trade.drywall: "Drywall",
    Trade.painting: "Painting",
    Trade.carpentry_millwork: "Carpentry & millwork",
    Trade.doors_hardware: "Doors & hardware",
    Trade.flooring: "Flooring",
    Trade.windows_glazing: "Windows & glazing",
    Trade.hvac: "HVAC",
    Trade.appliances: "Appliances",
    Trade.cleaning: "Cleaning",
    Trade.general: "General",
}

# Processing checklist step names (DESIGN.md "Record").
STAGE_LABEL: dict[Stage, str] = {
    Stage.ingest: "Preparing the video",
    Stage.transcribe: "Listening to the walk",
    Stage.track_tape: "Finding tape",
    Stage.merge_marks: "Following each strip of tape",
    Stage.classify_turns: "Sorting what was said",
    Stage.fuse: "Matching what was said to each mark",
    Stage.pick_evidence: "Picking the clearest photo",
    Stage.write_items: "Writing items",
    Stage.ground_defects: "Outlining each defect",
    Stage.verify: "Checking each item against its photo",
    Stage.resolve_closures: "Checking what was fixed",
    Stage.persist: "Saving the punch list",
}

CHANNEL_LABEL: dict[Channel, str] = {
    Channel.slack: "Slack",
    Channel.email: "Email",
    # The in-app outbox: the dispatch is recorded and its link is copied for the PM to paste.
    Channel.outbox: "Copy link",
}

WALK_KIND_LABEL: dict[WalkKind, str] = {
    WalkKind.initial: "First walk",
    WalkKind.rewalk: "Re-walk",
}

WALK_STATUS_LABEL: dict[WalkStatus, str] = {
    WalkStatus.uploaded: "Processing",
    WalkStatus.processing: "Processing",
    WalkStatus.ready: "Ready",
    WalkStatus.failed: "Failed",
}

# Names judges see (CLAUDE.md "UX summary"): name the Meta models plainly.
MODEL_LABEL: dict[str, str] = {
    MODEL_ASR: "Muse Voice Transcribe",
    MODEL_SAM: "SAM 3.1",
    MODEL_SPARK_OPEN: "Muse Spark Open on Baseten",
    MODEL_SPARK_HOSTED: "Muse Spark 1.3",
}

PROVIDER_LABEL: dict[Provider, str] = {
    Provider.meta: "Meta Model API",
    Provider.baseten: "Baseten",
    Provider.meta_fallback: "Meta Model API (fallback)",
}

# Step titles on "How this walk was processed" (docs/design/08-how-processed.png).
STAGE_TITLE: dict[Stage, str] = {
    Stage.ingest: "Prepare the video",
    Stage.transcribe: "Transcribe the narration",
    Stage.track_tape: "Track every strip of tape",
    Stage.merge_marks: "Join sightings of the same tape",
    Stage.classify_turns: "Sort what was said",
    Stage.fuse: "Match each sentence to its tape",
    Stage.pick_evidence: "Pick the clearest photo",
    Stage.write_items: "Write each item",
    Stage.ground_defects: "Outline the defect",
    Stage.verify: "Check each item against its photo",
    Stage.resolve_closures: "Check each fix",
    Stage.persist: "Save the punch list",
}


def model_label(model: str) -> str:
    """Plain name for a model id; unknown ids are shown as-is."""
    return MODEL_LABEL.get(model, model)


def format_item_number(number: int) -> str:
    """Project-scoped item number as written in reports and the API: 14 -> '#014'.

    The tape tab in the UI shows the bare number ('14'), per DESIGN.md.
    """
    return f"#{number:03d}"
