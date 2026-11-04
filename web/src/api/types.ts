// Mirrors punchwalk/api/schemas.py. Change both or neither.
// Enums and label maps mirror punchwalk/core/enums.py. tests/test_contracts.py checks that every
// schema, field, nullability, enum value and label below matches the Python side.
//
// Conventions: snake_case fields; every field is always present (optional values are null);
// times are ISO 8601 UTC strings; video times are seconds; media URLs are root-relative (/media/...).
// Mock responses for every route live in fixtures/mock_api/ (see its README.md).

// =========================================================================== enums

export type Trade =
  | 'electrical'
  | 'plumbing'
  | 'drywall'
  | 'painting'
  | 'carpentry_millwork'
  | 'doors_hardware'
  | 'flooring'
  | 'windows_glazing'
  | 'hvac'
  | 'appliances'
  | 'cleaning'
  | 'general';

export type Severity = 'cosmetic' | 'functional' | 'safety';

/** Internal status. UI shows: open + dispatched -> "Open", needs_review -> "Needs a look", closed -> "Signed off". */
export type ItemStatus = 'open' | 'dispatched' | 'closed' | 'needs_review';

export type WalkKind = 'initial' | 'rewalk';

export type WalkStatus = 'uploaded' | 'processing' | 'ready' | 'failed';

export type TurnKind = 'location' | 'defect' | 'status_update' | 'other';

/**
 * How the defect was located. sam_concept / sam_box = SAM outlined it; spark_box = Spark Open's box
 * (grounding v2, no SAM outline passed); pointed_only = only the
 * "pointed here" marker (no outline); none = voice-only item (no outline, no marker).
 */
export type Grounding = 'sam_concept' | 'sam_box' | 'spark_box' | 'pointed_only' | 'none';

export type Channel = 'slack' | 'email' | 'outbox';

/** meta-fallback = Muse Spark 1.3 answered after Baseten failed twice. */
export type Provider = 'meta' | 'baseten' | 'meta-fallback';

export type ReasoningProvider = 'baseten' | 'meta';

export type JobKind = 'process_walk';

export type JobStatus = 'queued' | 'running' | 'done' | 'failed';

export type StageStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped';

export type Stage =
  | 'ingest'
  | 'transcribe'
  | 'classify_turns'
  | 'track_hands'
  | 'find_points'
  | 'fuse'
  | 'pick_evidence'
  | 'write_items'
  | 'ground_defects'
  | 'verify'
  | 'resolve_closures'
  | 'persist';

export type Orientation = 'portrait' | 'landscape';

export type Device = 'glasses' | 'phone' | 'unknown';

export type HistoryActor = 'human' | 'system' | 'model';

export type HistoryAction =
  | 'created'
  | 'verified'
  | 'flagged'
  | 'dispatched'
  | 'edited'
  | 'status_changed'
  | 'rewalk_checked'
  | 'dismissed'
  | 'noted';

export type ClaimedStatus = 'fixed' | 'not_fixed' | 'unclear';

export type VoiceStatus = 'fixed' | 'not_fixed' | 'unclear' | 'not_mentioned';

export type VisionStatus = 'fixed' | 'still_visible' | 'unclear' | 'not_checked';

export type ClosureDecision = 'signed_off' | 'still_open' | 'needs_review' | 'not_checked';

// =========================================================================== constants

export const MODEL_ASR = 'muse-voice-transcribe-1.0';
export const MODEL_SAM = 'sam-3.1';
export const MODEL_SPARK_OPEN = 'meta-models/muse-spark-open-1026';
export const MODEL_SPARK_HOSTED = 'muse-spark-1.3';

export const REVIEW_NOT_IN_FRAME = "The frame doesn't show the defect described.";
export const REVIEW_FALSE_FIX = 'Marked fixed on the re-walk, but the defect is still visible.';
export const NOT_CHECKED_ON_REWALK = 'Not checked on re-walk';

// =========================================================================== UI labels (DESIGN.md "Words")

export const STATUS_LABEL: Record<ItemStatus, string> = {
  open: 'Open',
  dispatched: 'Open',
  needs_review: 'Needs a look',
  closed: 'Signed off',
};

export const SEVERITY_LABEL: Record<Severity, string> = {
  cosmetic: 'Cosmetic',
  functional: 'Functional',
  safety: 'Safety',
};

export const TRADE_LABEL: Record<Trade, string> = {
  electrical: 'Electrical',
  plumbing: 'Plumbing',
  drywall: 'Drywall',
  painting: 'Painting',
  carpentry_millwork: 'Carpentry & millwork',
  doors_hardware: 'Doors & hardware',
  flooring: 'Flooring',
  windows_glazing: 'Windows & glazing',
  hvac: 'HVAC',
  appliances: 'Appliances',
  cleaning: 'Cleaning',
  general: 'General',
};

/** Processing checklist step names (DESIGN.md "Record"). */
export const STAGE_LABEL: Record<Stage, string> = {
  ingest: 'Preparing the video',
  transcribe: 'Listening to the walk',
  track_hands: 'Following your hand (SAM 3.1)',
  find_points: 'Finding where you pointed',
  classify_turns: 'Sorting what was said',
  fuse: 'Matching what was said to each point',
  pick_evidence: 'Picking the clearest photo',
  write_items: 'Writing items',
  ground_defects: 'Outlining each defect',
  verify: 'Checking each item against its photo',
  resolve_closures: 'Checking what was fixed',
  persist: 'Saving the punch list',
};

/** outbox = the dispatch is recorded in the app and its link is copied. */
export const CHANNEL_LABEL: Record<Channel, string> = {
  slack: 'Slack',
  email: 'Email',
  outbox: 'Copy link',
};

export const WALK_KIND_LABEL: Record<WalkKind, string> = {
  initial: 'First walk',
  rewalk: 'Re-walk',
};

export const WALK_STATUS_LABEL: Record<WalkStatus, string> = {
  uploaded: 'Processing',
  processing: 'Processing',
  ready: 'Ready',
  failed: 'Failed',
};

/** Plain model names (keyed by model id). */
export const MODEL_LABEL: Record<string, string> = {
  'muse-voice-transcribe-1.0': 'Muse Voice Transcribe',
  'sam-3.1': 'SAM 3.1',
  'meta-models/muse-spark-open-1026': 'Muse Spark Open on Baseten',
  'meta-models/muse-spark-open': 'Muse Spark Open on Baseten',
  'muse-spark-1.3': 'Muse Spark 1.3',
  'meta-files': 'Meta Files API',
};

export const PROVIDER_LABEL: Record<Provider, string> = {
  meta: 'Meta Model API',
  baseten: 'Baseten',
  'meta-fallback': 'Meta Model API (fallback)',
};

// =========================================================================== shared pieces

export interface BoxOut {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

/** A pixel position (fingertip, "pointed here" marker). */
export interface XYOut {
  x: number;
  y: number;
}

/** todo = open + dispatched + needs_review; open = open + dispatched. */
export interface ItemCountsOut {
  todo: number;
  open: number;
  needs_review: number;
  signed_off: number;
  all: number;
}

// =========================================================================== health

/** GET /healthz */
export interface HealthOut {
  ok: boolean;
  version: string;
  demo_mode: boolean;
  reasoning_provider: ReasoningProvider;
  time: string;
}

// =========================================================================== projects

/** Trades-directory entry as returned (the Slack webhook URL is never returned). */
export interface TradeContactOut {
  trade: Trade;
  company: string;
  contact: string | null;
  email: string | null;
  phone: string | null;
  slack_channel: string | null;
  has_slack_webhook: boolean;
}

export interface TradeContactIn {
  trade: Trade;
  company: string;
  contact?: string | null;
  email?: string | null;
  phone?: string | null;
  slack_channel?: string | null;
  slack_webhook?: string | null;
}

/** GET /api/projects/{project_id} (also POST /api/projects, PATCH /api/projects/{project_id}) */
export interface ProjectOut {
  id: string;
  name: string;
  address: string | null;
  trades: TradeContactOut[];
  keywords: string[];
  rooms: string[];
  counts: ItemCountsOut;
  walk_count: number;
  last_walk_at: string | null;
  created_at: string;
  updated_at: string;
}

/** GET /api/projects */
export interface ProjectListOut {
  projects: ProjectOut[];
}

/** POST /api/projects (body). Omitted keys default to null / []. */
export interface ProjectCreate {
  name: string;
  address?: string | null;
  trades?: TradeContactIn[];
  keywords?: string[];
  rooms?: string[];
}

/** PATCH /api/projects/{project_id} (body; omitted = unchanged, trades replaces the directory) */
export interface ProjectPatch {
  name?: string | null;
  address?: string | null;
  trades?: TradeContactIn[] | null;
  keywords?: string[] | null;
  rooms?: string[] | null;
}

// =========================================================================== walks

export interface ClipOut {
  order: number;
  filename: string;
  duration_s: number;
  offset_s: number;
}

/** One row of the processing checklist. */
export interface StageProgressOut {
  stage: Stage;
  label: string;
  status: StageStatus;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  counts: Record<string, number>;
  progress: number | null;
  detail: string | null;
  model_label: string | null;
  message: string | null;
}

export interface WalkMediaOut {
  display_mp4: string | null;
  poster: string | null;
}

/** Initial walk: items it created. Re-walk: items it checked (items = checked). */
export interface WalkCountsOut {
  items: number;
  points: number;
  turns: number;
  needs_review: number;
  signed_off: number;
  still_open: number;
  not_checked: number;
}

/** Row of GET /api/walks and GET /api/projects/{project_id}/walks; header of walk detail. */
export interface WalkOut {
  id: string;
  project_id: string;
  project_name: string;
  kind: WalkKind;
  status: WalkStatus;
  rewalk_of: string | null;
  job_id: string | null;
  recorded_at: string | null;
  created_at: string;
  updated_at: string;
  clips: ClipOut[];
  stages: StageProgressOut[];
  media: WalkMediaOut;
  duration_s: number | null;
  fps: number | null;
  width: number | null;
  height: number | null;
  orientation: Orientation | null;
  device: Device;
  error: string | null;
  counts: WalkCountsOut;
}

/** GET /api/walks (?project_id=) and GET /api/projects/{project_id}/walks, newest first. */
export interface WalkListOut {
  walks: WalkOut[];
}

/** One line of the transcript. */
export interface TurnOut {
  id: string;
  walk_id: string;
  idx: number;
  start_ms: number;
  end_ms: number;
  text: string;
  kind: TurnKind;
  room: string | null;
  claimed: ClaimedStatus | null;
  item_ids: string[];
}

/** One pointing event: the hand held still while pointing at a defect (display pixels). */
export interface PointOut {
  id: string;
  walk_id: string;
  /** item it created (initial walk) or checked (re-walk); null until written. */
  item_id: string | null;
  /** that item's number, for the number tab. */
  item_number: number | null;
  /** 1-based, in time order within this walk. */
  number: number;
  /** seconds into display.mp4 (middle of the hold). */
  t: number;
  frame: number;
  fingertip_x: number;
  fingertip_y: number;
  hand_box: BoxOut;
  hold_s: number;
  sam_object_ids: string[];
  mean_conf: number | null;
  turn_id: string | null;
}

/** POST /api/projects/{project_id}/walks (multipart: kind, rewalk_of?, clips[] in order). */
export interface UploadOut {
  walk_id: string;
  job_id: string;
  status: WalkStatus;
}

/** POST /api/walks/{walk_id}/rewalk-link (body) -> WalkOut */
export interface RewalkLinkIn {
  initial_walk_id: string;
}

/** A tile in the "Items so far" strip. */
export interface ItemThumbOut {
  id: string;
  number: number;
  title: string;
  crop_url: string | null;
}

/** GET /api/walks/{walk_id}/progress (SSE: /api/walks/{walk_id}/events); POST /api/walks/{walk_id}/retry */
export interface JobProgressOut {
  job_id: string;
  walk_id: string;
  kind: JobKind;
  status: JobStatus;
  walk_status: WalkStatus;
  attempts: number;
  stage: Stage | null;
  stages: StageProgressOut[];
  progress: number;
  message: string | null;
  items_so_far: ItemThumbOut[];
  items_expected: number | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
}

// =========================================================================== items

export interface EvidenceOut {
  t: number;
  image_url: string;
  raw_url: string;
  crop_url: string;
  /** pixel size of image_url/raw_url; SVG paths and boxes use it (viewBox). */
  width: number;
  height: number;
  /** defect outline (red), SVG path d. */
  defect_svg: string | null;
  defect_box: BoxOut | null;
  crop_box: BoxOut | null;
  /** "pointed here" marker (small blue dot); null for voice-only items. */
  pointer: XYOut | null;
  /** URL of the frame where the hand points, hand outlined (blue). */
  pointing_frame: string | null;
  /** the hand in pointing_frame (same pixel size as image_url). */
  hand_box: BoxOut | null;
  grounding: Grounding;
  sam_concept: string | null;
}

export interface VerificationOut {
  consistent: boolean;
  note: string | null;
  issues: string[];
  provider: Provider;
  checked_at: string | null;
}

export interface ClosureOut {
  rewalk_id: string;
  rewalk_turn_id: string | null;
  quote: string | null;
  after_t: number | null;
  before_image_url: string | null;
  after_image_url: string | null;
  after_frame_url: string | null;
  before_after_url: string | null;
  voice_status: VoiceStatus;
  vision_status: VisionStatus;
  decision: ClosureDecision;
  note: string | null;
  decided_at: string | null;
}

export interface HistoryEntryOut {
  at: string;
  actor: HistoryActor;
  action: HistoryAction;
  field: string | null;
  from: string | null;
  to: string | null;
  note: string | null;
}

/** The latest send of this item ("Open, sent to Oakline Millwork Nov 3"). */
export interface DispatchSummaryOut {
  dispatch_id: string;
  trade: Trade;
  company: string | null;
  channel: Channel;
  sent_at: string;
  times_sent: number;
}

/** GET /api/items/{item_id} (also PATCH /api/items/{item_id}) */
export interface ItemOut {
  id: string;
  project_id: string;
  number: number;
  origin_walk_id: string;
  point_id: string | null;
  turn_ids: string[];
  title: string;
  description: string;
  action: string;
  trade: Trade;
  room: string | null;
  location_detail: string | null;
  severity: Severity;
  status: ItemStatus;
  review_reason: string | null;
  quote: string | null;
  quote_start_s: number | null;
  quote_end_s: number | null;
  video_url: string | null;
  video_start_s: number;
  video_end_s: number;
  confidence: number | null;
  evidence: EvidenceOut | null;
  verification: VerificationOut | null;
  closure: ClosureOut | null;
  history: HistoryEntryOut[];
  dispatch: DispatchSummaryOut | null;
  created_at: string;
  updated_at: string;
}

/** Counts per filter value over the unfiltered list. */
export interface FacetsOut {
  trade: Partial<Record<Trade, number>>;
  severity: Partial<Record<Severity, number>>;
  room: Record<string, number>;
  status: Partial<Record<ItemStatus, number>>;
}

/** GET /api/projects/{project_id}/items ?trade=&severity=&room=&status=&q=&sort=walk|severity */
export interface ItemListOut {
  project_id: string;
  items: ItemOut[];
  facets: FacetsOut;
  counts: ItemCountsOut;
  total: number;
}

/** PATCH /api/items/{item_id} (body; omitted = unchanged). DELETE /api/items/{item_id} dismisses. */
export interface ItemPatch {
  title?: string | null;
  description?: string | null;
  action?: string | null;
  trade?: Trade | null;
  room?: string | null;
  location_detail?: string | null;
  severity?: Severity | null;
  status?: ItemStatus | null;
  review_reason?: string | null;
  note?: string | null;
}

/** POST /api/items/{item_id}/share */
export interface ShareLinkOut {
  token: string;
  url: string;
}

/** GET /api/share/{token} (public; page at /share/item/{token}) */
export interface ShareItemOut {
  token: string;
  project_name: string;
  project_address: string | null;
  number: number;
  title: string;
  description: string;
  action: string;
  trade: Trade;
  company: string | null;
  room: string | null;
  location_detail: string | null;
  severity: Severity;
  status: ItemStatus;
  quote: string | null;
  quote_start_s: number | null;
  quote_end_s: number | null;
  video_url: string | null;
  video_start_s: number;
  video_end_s: number;
  evidence: EvidenceOut | null;
  closure: ClosureOut | null;
  walked_at: string | null;
  after_at: string | null;
}

/** GET /api/walks/{walk_id}. items: created by an initial walk, or checked by a re-walk. */
export interface WalkDetailOut {
  walk: WalkOut;
  turns: TurnOut[];
  points: PointOut[];
  items: ItemOut[];
}

// =========================================================================== overlay

/** The inspector's hand in one overlay frame, around a pointing event. */
export interface OverlayHandOut {
  point_id: string;
  /** the item's number, for the number tab at the fingertip; null if no item. */
  number: number | null;
  box: BoxOut;
  svg: string;
  /** set while the hand is held still (pointing), else null. */
  fingertip: XYOut | null;
}

export interface OverlayFrameOut {
  t: number;
  hands: OverlayHandOut[];
}

export interface OverlayEventOut {
  t: number;
  item_id: string;
  number: number;
}

/**
 * GET /api/walks/{walk_id}/overlay. Sampled at fps (10); only frames with a hand on screen are listed,
 * so no frame within 1/fps of the current time means no outlines. Coordinates: width x height.
 */
export interface OverlayOut {
  walk_id: string;
  fps: number;
  width: number;
  height: number;
  duration_s: number;
  frames: OverlayFrameOut[];
  events: OverlayEventOut[];
}

// =========================================================================== trades & dispatch

/** One row of the Trades list. Signed-off items aren't in to_review / to_send / sent. */
export interface TradeSummaryOut {
  trade: Trade;
  /** null -> show the trade name + "No company yet" */
  company: string | null;
  contact: TradeContactOut | null;
  /** needs a look (status needs_review) */
  to_review: number;
  /** open and never sent */
  to_send: number;
  /** sent on any channel (a copied link included), not signed off: dispatched, or open again after a send */
  sent: number;
  /** to_review == 0 && to_send == 0 -> "All caught up" */
  caught_up: boolean;
  /** where a send goes now; `outbox` is shown as "Copy link" (never "Outbox") */
  effective_channel: Channel;
  /** "Posts to #punch-electrical." / "Posts to your team's Slack channel." / "Emails maria.chen@example.com." / "Copy a link to share." */
  effective_channel_label: string;
  open_count: number;
  needs_review_count: number;
  signed_off_count: number;
  last_sent_at: string | null;
}

/** GET /api/projects/{project_id}/trades */
export interface TradeListOut {
  project_id: string;
  trades: TradeSummaryOut[];
}

/** A working channel for this trade (the preview lists only working ones). */
export interface DispatchChannelOut {
  channel: Channel;
  available: boolean;
  destination: string | null;
  reason: string | null;
  /** "Posts to #punch-electrical." / "Emails maria.chen@example.com." / "Copy a link to share." */
  label: string;
}

/** A needs-a-look item in the trade detail (opens the item; not sendable until approved). */
export interface DispatchItemRefOut {
  item_id: string;
  number: number;
  title: string;
}

/** An item already sent and not signed off ("Already sent", unchecked by default). */
export interface DispatchSentItemOut {
  item_id: string;
  number: number;
  title: string;
  sent_at: string;
  channel: Channel;
}

export interface DispatchLineOut {
  item_id: string;
  number: number;
  title: string;
  line: string;
  url: string;
}

/** GET /api/projects/{project_id}/trades/{trade}/preview */
export interface DispatchPreviewOut {
  project_id: string;
  trade: Trade;
  contact: TradeContactOut | null;
  channel: Channel;
  channel_reason: string | null;
  effective_channel: Channel;
  effective_channel_label: string;
  /** working channels only, effective_channel first */
  channels: DispatchChannelOut[];
  to_review: DispatchItemRefOut[];
  already_sent: DispatchSentItemOut[];
  heading: string;
  lines: DispatchLineOut[];
  subject: string;
  message_text: string;
  slack_text: string;
  item_ids: string[];
}

/** POST /api/projects/{project_id}/trades/{trade}/dispatch (body) */
export interface DispatchCreate {
  /** omitted / null -> the trade's effective channel */
  channel?: Channel | null;
  item_ids: string[];
}

/** Response of POST .../dispatch and of POST /api/items/{item_id}/approve-and-send?channel=; rows of GET .../dispatches */
export interface DispatchOut {
  id: string;
  project_id: string;
  trade: Trade;
  company: string | null;
  channel: Channel;
  /** CHANNEL_LABEL: "Slack" / "Email" / "Copy link" */
  channel_label: string;
  /** "Posted 2 items to #punch-electrical" / "Emailed 2 items to Brightline Painting" / "Copied a link for 3 items" */
  summary: string;
  destination: string | null;
  item_ids: string[];
  sent_at: string;
  ok: boolean;
  error: string | null;
  payload: Record<string, unknown>;
}

/** GET /api/projects/{project_id}/trades/{trade}/dispatches (newest first) */
export interface DispatchListOut {
  project_id: string;
  trade: Trade | null;
  dispatches: DispatchOut[];
}

// =========================================================================== re-walk

export interface RewalkItemOut {
  item_id: string;
  number: number;
  title: string;
  room: string | null;
  trade: Trade;
  status: ItemStatus;
  decision: ClosureDecision;
  reason: string | null;
  quote: string | null;
  before_image_url: string | null;
  after_image_url: string | null;
  before_at: string | null;
  after_at: string | null;
}

export interface RewalkCountsOut {
  checked: number;
  signed_off: number;
  still_open: number;
  needs_a_look: number;
  not_checked: number;
}

/** GET /api/walks/{walk_id}/rewalk-summary (re-walks only) */
export interface RewalkSummaryOut {
  walk_id: string;
  initial_walk_id: string;
  project_id: string;
  recorded_at: string | null;
  counts: RewalkCountsOut;
  headline: string;
  needs_a_look: RewalkItemOut[];
  signed_off: RewalkItemOut[];
  still_open: RewalkItemOut[];
  not_checked: RewalkItemOut[];
}

// =========================================================================== model calls & processing

export interface ModelCallOut {
  id: string;
  walk_id: string | null;
  item_id: string | null;
  stage: Stage | null;
  model: string;
  model_label: string;
  provider: Provider;
  latency_ms: number;
  tokens_in: number | null;
  tokens_out: number | null;
  frames: number | null;
  est_cost_usd: number;
  cached: boolean;
  ok: boolean;
  error: string | null;
  started_at: string;
}

/** GET /api/walks/{walk_id}/model-calls (oldest first) */
export interface ModelCallListOut {
  walk_id: string;
  calls: ModelCallOut[];
}

export interface ProcessingStepOut {
  stage: Stage;
  title: string;
  model_label: string | null;
  provider: Provider | null;
  detail: string;
  duration_ms: number;
  calls: number;
  fallback_calls: number;
  cost_usd: number;
}

export interface EvalMetricOut {
  key: string;
  label: string;
  value: number;
  total: number;
}

export interface EvalSummaryOut {
  source: string;
  updated_at: string | null;
  metrics: EvalMetricOut[];
}

/** GET /api/walks/{walk_id}/processing. eval null -> hide the "labeled test walks" section. */
export interface ProcessingSummaryOut {
  walk_id: string;
  kind: WalkKind;
  status: WalkStatus;
  total_duration_ms: number;
  total_cost_usd: number;
  models_used: string[];
  steps: ProcessingStepOut[];
  calls: ModelCallOut[];
  eval: EvalSummaryOut | null;
}
