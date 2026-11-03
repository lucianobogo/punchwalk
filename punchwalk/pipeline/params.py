"""Tunable pipeline thresholds (PLAN 1.2-1.6, pointing revision), in one place for Phase 3.

Values marked "SPEC" come from the brief / PLAN; the rest are first-pass engineering defaults
listed here so they are easy to find and tune.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- 1.2 track hands
DEFAULT_HAND_CONCEPT = "hand"  # SPEC; settings.SAM_HAND_CONCEPT overrides
HAND_PHRASINGS = ("hand", "index finger", "pointing hand")  # SPEC: compared in `make record`
SVG_EPSILON_PX = 1.0  # polygon simplification for stored hand outlines (SAM-frame px)

# --------------------------------------------------------------------------- 1.3 pointing events
POINT_WINDOW_BEFORE_S = 2.0  # SPEC: search window starts this long before the defect turn
POINT_WINDOW_AFTER_S = 1.0  # SPEC: and ends this long after it
POINT_MIN_HOLD_S = 0.4  # SPEC: a point = the hand roughly still for at least this long
POINT_MIN_HOLD_FRAMES = 4  # SPEC: ... over at least this many consecutive SAM frames
# "Roughly still": frame-to-frame box-center motion below this fraction of the frame diagonal,
# and the center never drifts further than POINT_MAX_DRIFT_FRAC from the run's first center.
POINT_MAX_STEP_FRAC = 0.02
POINT_MAX_DRIFT_FRAC = 0.05
POINT_MAX_FRAME_GAP = 1  # SAM frames a hold may skip (frame indices can skip)
POINT_MIN_CONF = 0.3  # ignore hand detections below this confidence
POINT_MIN_BOX_FRAC = 0.002  # ignore specks smaller than this fraction of the frame
# The hand "enters" from a frame border when its box is within this fraction of that border.
EDGE_TOUCH_FRAC = 0.02

# --------------------------------------------------------------------------- 1.5 fuse
VIDEO_BEFORE_S = 2.0  # SPEC: item clip starts this long before the turn
VIDEO_AFTER_S = 1.0  # SPEC: and ends this long after it
TITLE_MAX_CHARS = 60

# --------------------------------------------------------------------------- 1.6 evidence
EVIDENCE_SEARCH_S = 1.5  # SPEC: look for the evidence frame within +/- this of the point
EVIDENCE_CLEAR_BOX_FRAC = 0.15  # SPEC: square around the fingertip, as a fraction of frame width
EVIDENCE_MAX_HAND_COVER = 0.05  # SPEC: that square must hold < this fraction of hand-mask pixels
EVIDENCE_AFTER_BONUS = 1.25  # prefer frames after the hold with no hand in view (score multiplier)
EVIDENCE_MAX_CANDIDATES = 31  # SAM frames considered around a point (10 fps x 3 s + 1)
CONTEXT_FRAME_OFFSET_S = 0.7  # SPEC: context frames at +/- this
VOICE_ONLY_SAMPLES = 5  # SPEC
# Camera-motion compensation of the fingertip: ORB on the region around the fingertip.
ALIGN_REGION_FRAC = 0.35  # square side as a fraction of the frame's short side
ALIGN_MIN_INLIERS = 10
ALIGN_MIN_PHASE_RESPONSE = 0.05  # phase-correlation peak needed to trust the shift
ALIGN_MIN_TEXTURE_STD = 2.0  # gray-level std of the region below which there is nothing to align on
ALIGN_MAX_SHIFT_FRAC = 0.25  # reject alignments that move the pointer further than this
POSTER_WINDOW_S = (0.5, 1.5)  # poster.jpg: sharpest frame in this window
CROP_ASPECT = 4 / 3  # card crop, width / height
CROP_FRAC_OF_SHORT_SIDE = 0.6  # crop height as a fraction of the frame's short side
EVIDENCE_JPEG_QUALITY = 2  # ffmpeg -q:v (2 = best)

# --------------------------------------------------------------------------- 1.4 classify turns
CLASSIFY_CHUNK_TURNS = 60  # SPEC: chunk if more than ~60 turns
CLASSIFY_MAX_TOKENS_BASE = 1500
CLASSIFY_MAX_TOKENS_PER_TURN = 120
CLASSIFY_MAX_TOKENS_CAP = 12000

# --------------------------------------------------------------------------- 2.2 ground defects
GROUND_RADIUS_FRAC = 0.25  # SPEC: defect instance within ~25% of the frame width of the fingertip
GROUND_BOX_EXPAND = 0.10  # SPEC: box prompt = Spark Open's defect_box grown 10%
GROUND_MAX_AREA_FRAC = 0.35  # an "instance" bigger than this share of the frame is a wall, not a defect
GROUND_SVG_EPSILON_PX = 1.5  # outline simplification (display px)

# --------------------------------------------------------------------------- 2.5 runner
STAGE_RETRIES = 2  # SPEC: retries per stage for retryable errors (5xx, timeouts)
STAGE_RETRY_BACKOFF_S = (2.0, 6.0)
