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
EVIDENCE_AFTER_HOLD_S = 2.0  # 3.4: ...and up to this long after the hold ends (long holds hide the defect;
# later frames mostly lose the pointer because the camera moves on)
EVIDENCE_CLEAR_BOX_FRAC = 0.15  # SPEC: square around the fingertip, as a fraction of frame width
EVIDENCE_MAX_HAND_COVER = 0.05  # SPEC: that square must hold < this fraction of hand-mask pixels
EVIDENCE_AFTER_BONUS = 1.25  # prefer frames after the hold with no hand in view (score multiplier)
EVIDENCE_MAX_CANDIDATES = 31  # SAM frames considered around a point (10 fps x 3 s + 1)
CONTEXT_FRAME_OFFSET_S = 0.7  # SPEC: context frames at +/- this
CONTEXT_END_MARGIN_S = 0.2  # ...but no later than this before the clip's end
VOICE_ONLY_SAMPLES = 5  # SPEC
# Camera-motion compensation of the fingertip: ORB on the region around the fingertip.
ALIGN_REGION_FRAC = 0.35  # square side as a fraction of the frame's short side
ALIGN_MIN_INLIERS = 10
ALIGN_MIN_PHASE_RESPONSE = 0.05  # phase-correlation peak needed to trust the shift
ALIGN_MIN_TEXTURE_STD = 2.0  # gray-level std of the region below which there is nothing to align on
ALIGN_MAX_SHIFT_FRAC = 0.25  # reject alignments that move the pointer further than this
POSTER_WINDOW_S = (0.5, 1.5)  # poster.jpg: sharpest frame in this window
CROP_ASPECT = 4 / 3  # card crop, width / height (pick_evidence's provisional crop and the rendered crop)
CROP_MIN_FRAC = 0.45  # crop height at least this fraction of the frame's short side
CROP_MARGIN = 1.8  # the crop spans the pointer + defect extent times this
DOT_RADIUS_PX = (6, 8)  # DESIGN.md: the "pointed here" dot is 6-8 px at full frame
DOT_RADIUS_DIV = 150  # radius = short side / this, clamped to DOT_RADIUS_PX
EVIDENCE_JPEG_QUALITY = 2  # ffmpeg -q:v (2 = best)

# --------------------------------------------------------------------------- 1.4 classify turns
CLASSIFY_CHUNK_TURNS = 60  # SPEC: chunk if more than ~60 turns
CLASSIFY_MAX_TOKENS_BASE = 1500
CLASSIFY_MAX_TOKENS_PER_TURN = 120
CLASSIFY_MAX_TOKENS_CAP = 12000

# --------------------------------------------------------------------------- 2.2 ground defects
GROUND_RADIUS_FRAC = 0.25  # SPEC: defect instance within ~25% of the frame width of the fingertip
GROUND_BOX_MAX_DIST_FRAC = 0.15  # 3.4: Spark Open's defect_box must come within this share of the width of the dot
GROUND_BOX_MAX_DIST_FRAC_ABSENT = 0.03  # ...3% for missing/loose/crooked defects: the dot IS the spot (empty hole)
GROUND_CONCEPT_MAX_BOX_RATIO = 4.0  # a concept instance > this x Spark's box is the whole object: try the box first
GROUND_BOX_EXPAND = 0.10  # SPEC: box prompt = Spark Open's defect_box grown 10%
GROUND_MAX_AREA_FRAC = 0.35  # an "instance" bigger than this share of the frame is a wall, not a defect
GROUND_SVG_EPSILON_PX = 1.5  # outline simplification (display px)

# --------------------------------------------------------------------------- 10.3 grounding v2 (GROUNDING_V2=1)
# (a) locate
V2_LOCATE_MAX_TOKENS = 2500
V2_LOCATE_EFFORT = "medium"  # the box quality decides everything downstream; ~1 cent per item
V2_LOCATE_GRID = True  # draw a labelled pixel grid on the locate image (measured: much tighter boxes)
V2_GRID_STEP_PX = 100
V2_RING_RADIUS_DIV = 60  # hollow fingertip ring radius = short side / this
# (b) segment
V2_CROP_SMALL_FRAC = 0.15  # box short side below this share of the frame's short side -> run SAM on a crop
V2_CROP_SCALE = 2.5  # the crop spans this many times the box
V2_CROP_MIN_SHORT_PX = 640  # ...and at least this many px on its short side
V2_NEG_OFFSET_FRAC = 0.12  # negative points sit this share of the box size outside each side
V2_NEG_OFFSET_MIN_PX = 6
# (c) score
V2_MAX_AREA_RATIO = 2.5  # SPEC: reject masks larger than this x the Spark box area
V2_MIN_AREA_RATIO = 0.03  # ...and specks smaller than this x the box area
V2_MAX_FRAME_FRAC = 0.4  # SPEC: reject masks over this share of the frame unless extent is whole_surface
V2_DUP_IOU = 0.85  # SPEC: near-duplicates (mask IoU above this) keep only the best
V2_MAX_FINGER_DIST_FRAC = 0.25  # reject masks further than this share of the frame width from the fingertip
V2_FINGER_SCALE_FRAC = 0.08  # fingertip score falls to 0 at this share of the frame's short side from the mask
V2_EXPECTED_FILL = {"spot": 0.6, "region": 0.6, "whole_surface": 0.75}  # mask area / box area when tight
V2_W_IOU = 0.40  # score weights (renormalised over the terms that apply: no fingertip -> no finger term)
V2_W_CONF = 0.20
V2_W_FINGER = 0.25
V2_W_AREA = 0.15
V2_MIN_SCORE = 0.35  # below this a candidate is not shown to the check call
V2_CHECK_TOP = 3  # SPEC: top masks shown to the check call (plus the Spark box)
V2_CHECK_MAX_TOKENS = 2500
V2_CHECK_CONTEXT_SCALE = 3.0  # check images are cropped to the candidates' union grown by this...
V2_CHECK_MIN_FRAC = 0.25  # ...at least this share of the frame's short side
V2_CHECK_MIN_SHORT_PX = 768  # smaller check crops are upscaled to this short side
# (f) smooth
V2_SMOOTH_SIGMA_FRAC = 0.04  # blur sigma = this x sqrt(mask area) px...
V2_SMOOTH_MIN_SIGMA_PX = 2.0  # ...at least this
V2_HOLE_FILL_FRAC = 0.15  # fill holes smaller than this share of the component (a ring keeps its middle)
V2_DP_EPSILON_PX = 2.0  # Douglas-Peucker tolerance before Chaikin (frame px)
V2_CHAIKIN_ITERS = 2
V2_BOX_CORNER_FRAC = 0.18  # rounded-rect corner radius as a share of the box's short side
# (g) pointer mapping
V2_HOMOGRAPHY_MIN_INLIERS = 15
V2_HOMOGRAPHY_MAX_SHIFT_FRAC = 0.35

# --------------------------------------------------------------------------- 2.5 runner
STAGE_RETRIES = 2  # SPEC: retries per stage for retryable errors (5xx, timeouts)
STAGE_RETRY_BACKOFF_S = (2.0, 6.0)

# --------------------------------------------------------------------------- 7.x re-walk
CLOSURE_MAX_CALLS = 40  # SPEC: Spark Open calls per re-walk (matching + visual checks)
CLOSURE_MIN_MATCH_CONF = 0.5  # a mention takes an item only at or above this match confidence
CLOSURE_ALIGN_MIN_INLIERS = 25  # ORB matches needed to line up the first-walk and re-walk frames
CLOSURE_ALIGN_MAX_SHIFT_FRAC = 0.35  # a phase-correlation shift larger than this share of the width is rejected
PIXEL_RING_PX = 14  # ring around the defect used as "the surrounding surface"
PIXEL_MIN_CONTRAST = 8.0  # the mark must stand out this much (gray levels) on the first walk to be measured
PIXEL_STILL_RATIO = 0.5  # re-walk contrast >= this share of the first walk's = still there
PIXEL_GONE_RATIO = 0.2  # <= this share = gone
CLOSURE_ALIGN_MIN_NCC = 0.3  # a phase-correlation shift is kept only if the gradients then correlate this well
# (a fixed defect removes some edges, so a correct shift on a plain wall can score only ~0.4)
CLOSURE_SECOND_LOOK = True  # 7.x: an automatic sign-off needs two independent "fixed" answers (or pixels)
