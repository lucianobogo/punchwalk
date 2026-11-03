# PLAN: Punchwalk

The orchestrator (`.claude/agents/orchestrator.md`, Claude Fable 5.1) works through this plan. For each item it briefs an Opus 5.5 worker, has the verifier check the work, commits a checkpoint, and ticks the box. **Workers never edit this file.**

**Definition of done:**
- a live URL with a seeded demo project
- a new walk uploaded from an iPhone works end to end
- eval targets met on walk1 + walk2
- every screen in DESIGN.md's screenshot checklist passes on the iPhone 15 Pro profile
- README and demo script written
- `make test` green

**Legend:**
- `[ ]` todo
- `[x]` done and verified (tagged `ckpt-X.Y`)
- `[~]` partial (the gap is logged in PROGRESS.md and BACKLOG.md)
- `[-]` cut on purpose (reason in PROGRESS.md)

**Cut line:** if the build is more than 1 hour behind the target clock, mark the next *(nice)* items `[-]` until it's back on schedule. 7.x, 8.1, 8.2 and 9.x are never cut.

**Parallel tracks** (after 0.7):
- **A**: pipeline & eval (builder)
- **B**: web app (ui-builder), against mocks until Phase 4 passes
- **C**: API, dispatch, deploy (builder)

Track ownership and paths are in the orchestrator's instructions.

| Phase | Track | Target clock (ET) |
|---|---|---|
| 0 Foundation, probes, contracts | – | Tue 5:30 PM |
| 1 Perception & fusion | A | Tue 8:30 PM |
| 2 Items, grounding, verification | A | Tue 11:00 PM |
| 3 Evaluation + tuning | A | Wed 1:00 AM |
| 4 API & jobs | C | Tue 10:00 PM |
| 5 Web app | B | Wed 3:00 AM |
| 6 Dispatch & report | B + C | Wed 5:00 AM |
| 7 Re-walk loop | A, then B | Wed 7:30 AM |
| 8 Demo readiness | all | Wed 10:30 AM |
| 9 Ship | C | Wed 12:00 PM (code freeze) |

---

## Phase 0: Foundation, probes, contracts

- [x] **0.1 Scaffold.**
  - Layout per CLAUDE.md; Makefile with every target listed there; uv + pnpm; ruff, pytest.
  - `.env.example` already exists; keep it current.
  - `.gitignore` already exists; extend it if needed.
- [x] **0.2 Clients.**
  - `cache.py`, `cost.py` (ledger + $25 cap), `meta_files.py`, `asr.py`.
  - `sam.py`: concept→video, concept→image, box→image, box→video; streamed; returns raw text + parsed records.
  - `llm.py`: Baseten primary, Meta fallback. Helper `structured(schema_model, messages, images)` with Pydantic validation and one repair retry.
  - Unit tests with recorded responses.
- [x] **0.3 `make probe`.** One live call each:
  - Files upload (3-second clip)
  - SAM concept `blue painter's tape` on one frame
  - ASR on 5 s of audio
  - Spark Open with one image + `json_schema`

  Print latency and cost; save outputs to `fixtures/probe/`. *(Done on Nov 3 with the tape concept; superseded by the pointing-hand change — the hand phrasing comparison moved to 0.6.)* Also try 2–3 phrasings for the tape concept (e.g. `blue tape`, `strip of blue painter's tape`) on the same frame and keep the best one in config. **AC:** all four calls succeed, or the exact error is logged under Needs human / Blocked and that client is mocked.
- [x] **0.4 Mask coordinate check.** Render a decoded SAM mask + box over its frame, confirm the mapping, write helper `mask_to_frame_svg()`, and record the finding in DECISIONS.md. **AC:** a PNG in `fixtures/probe/` where the outline sits on the tape (open it and look).
- [x] **0.5 Ingest.** A walk is 1..N clips in order (`walkN.*`, or parts `walkN_a.*`, `walkN_b.*`, …). Normalize each clip (apply rotation metadata; portrait stays portrait), then concatenate in order. Produce:
  - `display.mp4`: H.264, ≤ 1080 px on the short side, yuv420p, AAC, `+faststart`
  - `sam.mp4`: H.264, 10 fps, 720 px on the short side, no audio
  - `audio.wav`: mono s16le 16 kHz
  - `meta.json`: duration, fps, sizes, orientation, frame counts, scale factors, clip offsets

  Handle iPhone HEVC. If HDR is detected, make a best-effort tonemap to SDR.
- [ ] **0.6 `make record`.** Run ingest + transcribe + SAM hand tracking live once for every walk in `footage/`; store cache + fixtures. If a walk has parts, run SAM per part (ids reset per part) and offset frame indices by each part's start. Also compare the hand phrasings ("hand", "index finger", "pointing hand") on real pointing frames and keep whichever tracks best in config. **AC:** fixtures exist for walk1 and walk2 (and walk3 if present).
- [x] **0.7 Contracts.** These let Track B start right away:
  - SQLModel domain models (`punchwalk/core/models.py`) and API response schemas (`punchwalk/api/schemas.py`)
  - the same types in TypeScript (`web/src/api/types.ts`)
  - realistic mock API responses in `fixtures/mock_api/`: one project, an initial walk and a re-walk, ~11 items (one signed off with before/after, one needs a look, one safety), overlay data, transcript, model-call timeline. Use real frames from the footage where they already exist; synthetic placeholders are fine until Phase 3 refreshes them.

  **AC:** a test round-trips every mock through the schemas.

## Phase 1: Perception & fusion (Track A)

- [x] **1.1 Transcribe.** ENDPOINTING mode. Keywords = ~80-term base construction vocabulary in `punchwalk/vocab.py` (trades, fixtures, "cover plate", "strike plate", "escutcheon", "caulk", "baseboard", "casing", "drywall", "GWB", "touch-up", "reveal", room names, unit numbers) + project keywords.
- [x] **1.2 Track hands.** SAM 3.1 concept tracking on `sam.mp4` with the hand concept from config (`SAM_HAND_CONCEPT`, default "hand"). Store raw text + per-frame records (box, confidence, SVG in SAM-frame coordinates) in `stages/track_hands.json`.
- [x] **1.3 Pointing events.** Within each defect turn window `[turn.start − 2.0 s, turn.end + 1.0 s]`, find frames where a hand is extended into the frame and roughly still for ≥ 0.4 s. Fingertip = the hand-mask point farthest from where the hand enters the frame. One pointing event per mention: `{t, fingertip_xy, hand_box, hold_s}` → `Point` rows, numbered by time. No hand in the window → the mention takes the voice-only path.
- [x] **1.4 Classify turns.** One batched Spark Open call covering all turns, with structured output per turn:
  - `kind`
  - `room` (if mentioned)
  - `defects[]` (a short description each)
  - `status_update` `{target, claimed: fixed | not_fixed | unclear}`

  A room state machine carries the last-mentioned room forward.
- [x] **1.5 Fuse.** Each defect mention becomes exactly one item, with its pointing event if any (several candidates → the longest hold, then the closest to the mention's start). Mentions without a pointing event are **voice-only items**. There is no tape-track merging and there are no "unnarrated mark" items. Each item gets `video_start_s = turn.start − 2.0` and `video_end_s = turn.end + 1.0` (clamped).
- [x] **1.6 Evidence frames.**
  - Candidates = sharp frames within ±1.5 s of the pointing event where the hand mask does **not** cover the area around the fingertip (prefer frames after the hand withdraws).
  - Score = Laplacian variance (sharpness) of the context crop around the fingertip.
  - Map the fingertip into the chosen frame as the "pointed here" marker (`evidence.pointer`). Extract that frame from `display.mp4` at full resolution, plus the pointing frame and two context frames at ±0.7 s.
  - Voice-only items: sample 5 frames across the turn window and pick the sharpest (no marker).
  - Scale SAM coordinates → display coordinates using `meta.json`.
- [x] **1.7 Tests.** Synthetic tracks/turns for merge, fusion and evidence selection, plus a fixture-driven integration test. **AC:** `make test` green offline.

## Phase 2: Items, defect grounding, verification (Track A)

- [x] **2.1 Item writer** (Spark Open, `reasoning_effort=low`).
  - **Inputs:** the evidence frame with the fingertip marked, the pointing frame, two context frames, the turn text(s), room, trades enum.
  - **Output schema:**
    - `title`: ≤ 60 chars, punch-list style
    - `description`: 1–2 sentences summarizing the problem, including location detail. This is the AI summary shown on the card, so make it clearer than the raw quote (no filler words, no "uh").
    - `action`: ≤ 80 chars, imperative, what the trade needs to do (e.g. "Install a matching knob"). Only what's evident from the narration and the frame; no specs, sizes or quantities.
    - `trade`, `room`, `location_detail`, `severity`
    - `sam_concept`: a short noun phrase naming the defective object or area, e.g. "cabinet door", "door strike plate", "wall scuff mark"
    - `defect_box`: `{x1,y1,x2,y2}` in this image's pixels, near the marker, or null
    - `confidence`
    - `rationale`: ≤ 200 chars
  - **Prompt rules:** state only what the narration says and the frame shows; never invent quantities; if the frame doesn't show the defect, set `defect_box` to null and lower the confidence.
- [x] **2.2 Ground the defect** (SAM, image mode). Try these in order and record which one worked:
  1. SAM concept prompt `sam_concept` on the evidence frame → pick the instance nearest the fingertip marker, within ~25% of the frame width.
  2. SAM box prompt from `defect_box` (expanded 10%), if it lies within that radius.
  3. Marker-only evidence (`grounding = pointed_only`; voice-only items without a marker: `none`).

  Render the evidence JPEG: full frame, defect outline + 30% fill (red), a small blue dot where the finger pointed, no burned-in text (the UI draws the number tab and captions). Also save a 4:3 crop centered on marker + defect for cards.
- [x] **2.3 Verify** (Spark Open, `reasoning_effort=medium`). Input: the rendered evidence + item JSON + the original quote. Check that the title, description and action match both what was said and what the frame shows. Output: `{consistent, issues[], suggested_fix?}`. If inconsistent → `needs_review` with the note. Never drop an item.
- [x] **2.4 Voice-only grounding.** `GROUNDING_MODE=point+voice|voice_only`. Voice-only items (and every item in voice_only mode) go through the same 2.1–2.3 path using the sampled frame.
- [ ] **2.5 Job runner.** Stages run with per-stage timing, 2 retries, resumable state (a re-run skips completed stages unless `force=true`), and progress events (stage, counts) for the UI.

## Phase 3: Evaluation (Track A)

- [ ] **3.1 `eval/run_eval.py`.** Load `eval/labels/*.csv` (format in FOOTAGE.md).
  - Match predicted ↔ labeled items per walk: |t_pred − t_label| ≤ 6 s AND (room match OR token Jaccard of descriptions ≥ 0.2).
  - Hungarian assignment on match score.
  - If a row has a `clip` value, t_label = that part's offset in the concatenated walk + `time_mmss`.
- [ ] **3.2 Metrics.**
  - **Initial walks:** recall, precision, trade accuracy, room accuracy, grounding rate for pointed items (`pointed=1` rows whose item is grounded by `sam_concept` or `sam_box`), needs_review rate.
  - **Re-walk** (for each walk1 label matched to a predicted item):
    - closure accuracy: predicted signed-off vs. `fixed_in_rewalk`
    - **false-claim catch**: rows with `claimed_fixed_in_rewalk=1` and `fixed_in_rewalk=0` must end `needs_review`
- [ ] **3.3 Report + real mocks.** Write `eval/report.md` (summary table + per-item diffs) on every run; `make eval` uses the cache. Add `scripts/build_mock_api.py`, which regenerates `fixtures/mock_api/` from real pipeline outputs so the UI shows real evidence.
- [ ] **3.4 Iterate** on prompts and thresholds until walk1 + walk2 reach:
  - recall ≥ 0.9, precision ≥ 0.9
  - trade ≥ 0.85, room ≥ 0.9
  - closure ≥ 0.9, false-claim catch = 100%

  Spend at most 2 h. Never tune on walk3 (the holdout); report it separately. Log what changed in DECISIONS.md.

## Phase 4: API & jobs (Track C)

- [x] **4.1 DB.** Persist the 0.7 models (SQLModel `create_all`), media served from `DATA_DIR` with HTTP range support (needed for video seeking on iOS Safari).
- [x] **4.2 Endpoints:**
  - projects (+ trades directory)
  - walk upload: one or more ordered clips, multipart streamed to disk, ≤ 400 MB and ≤ 10 min in total
  - walk status/progress (polling, or SSE if simple)
  - walk detail (turns, marks, items)
  - overlay data
  - items: list (filters: trade, severity, room, status, q; sort) and patch with history. Each item includes `video_start_s` and `video_end_s`.
  - dispatch: preview / send
  - rewalk link
  - model calls
  - report PDF
  - `/healthz`
- [x] **4.3 Overlay endpoint.** Per SAM frame (10 fps): `[{point_id, number, box, svg, fingertip}]` (hand outlines while pointing) in display coordinates (simplified paths; payload < 3 MB for a 3-minute walk), plus item events `[{t, item_id}]`.
- [x] **4.4 Guards.** ≤ 2 concurrent jobs; per-IP upload limit in `DEMO_MODE`; readable errors.

## Phase 5: Web app (Track B; mobile first, follow DESIGN.md exactly)

- [x] **5.1 Design system & shell.**
  - Tailwind tokens from DESIGN.md (color, type, radii, spacing); Archivo variable font, self-hosted.
  - Shared components: tape tab, buttons, chips, pills, cards, sheets (`vaul`), tab bar, top bars, toasts, skeletons.
  - Router, and a react-query data layer with `VITE_DATA=mock|live`.
  - PWA manifest + icons + Apple meta + safe areas.
  - A `/dev/components` gallery.
  - `make screenshots`: Playwright WebKit, `iPhone 15 Pro` profile, writes every DESIGN.md checklist screen to `.verify/screens/`.

  **AC:** the gallery screenshot matches DESIGN.md, and the manifest and icons are valid.
- [x] **5.2 Punch list (home)** per DESIGN.md and `docs/design/01-punch-list.png`:
  - status segments (To do, Signed off, All), with Needs a look pinned first
  - filter chips + sheets with counts; search, group and sort; filters in the URL
  - compact item rows with tape-tab thumbnails and play buttons
  - empty states
- [x] **5.3 Item detail** per DESIGN.md and `docs/design/02-item-detail.png`:
  - evidence with pinch-zoom and an outline toggle; before/after slider when signed off
  - quote card with Play this moment; details list; checked and history
  - one status-dependent primary action plus a More sheet; edits logged as human overrides
- [x] **5.4 Play this moment** sheet per DESIGN.md: starts at `video_start_s`, caption of the quote, pauses at `video_end_s`, "Keep watching".
- [ ] **5.5 Walks** list + walk detail (the demo's hero screen) per DESIGN.md and `docs/design/04-walk-detail.png`:
  - portrait video with hand outlines and numbered tabs at the fingertip while pointing
  - the new-item card slides up over the video at its moment, then settles into the list
  - timeline with item markers
  - Items so far / Transcript tabs, tap to seek
  - the video collapses to a mini-player on scroll
- [ ] **5.6 Record** per DESIGN.md: project → first walk / re-walk → add clips (reorderable) → upload with progress → live processing checklist naming each model → done.
- [ ] **5.7 How this walk was processed** per DESIGN.md: model-call timeline, time and cost per walk, eval numbers.
- [ ] **5.8 Live data.** Once Phase 4 passes, switch the app to the API (`VITE_DATA=live` by default); mocks stay for tests and screenshots.

## Phase 6: Dispatch & report (Tracks B + C)

- [ ] **6.1 Trades & dispatch.**
  - Backend channels: Slack incoming webhook (if set); email via Resend (if set) *(nice)*; in-app Outbox (always).
  - Trades screens per DESIGN.md.
  - Public read-only share page `/share/item/:token`, styled like the item detail, with "Play this moment".
- [x] **6.2 Auto-send policy.** After verification, `consistent=true` items are sent automatically; `needs_review` items wait for a human tap. Every automatic decision is recorded in the item's history.
- [x] **6.3 PDF punch report** *(nice)*. Cover page (project, date, totals by trade/status), items grouped by room with evidence, sign-off block.

## Phase 7: Re-walk loop (Track A, then B)

- [ ] **7.1 Resolve status updates.** The re-walk runs stages 0–1.6. Then, for each `status_update` turn, Spark Open maps it to an open item, given the open items `[{id, title, room, description, location_detail}]`, the utterance, the current room and the pointed location. Output: `{item_id | null, claimed, confidence}`.
- [ ] **7.2 Visual check.**
  - The re-walk runs the same pointing detection. For each matched item, use the evidence frame of its re-walk pointing event (else the sharpest frame in the turn window).
  - Spark Open compares [original evidence crop, new crop around the pointed spot] → `{defect_still_present: yes | no | unclear, note}`. There is no tape check.
- [ ] **7.3 Decision table.**
  - Claimed fixed + fixed on camera → **signed off**.
  - Said not fixed, or defect still visible → **open**, send again.
  - Claimed fixed + defect still visible → **needs a look: "Marked fixed on the re-walk, but the defect is still visible."** This is the headline demo moment.
  - Open items in visited rooms that were never mentioned → stay open, flagged "not checked on re-walk".
- [ ] **7.4 Before/after + re-walk summary.** Side-by-side renders (Track A) and the re-walk summary screen per DESIGN.md (Track B); re-send still-open items.
- [ ] **7.5 Eval.** Closure metrics from 3.2 pass.

## Phase 8: Demo readiness

- [ ] **8.1 `make seed`.**
  - Creates the demo project "Unit 2B final punch", with a fictional trades directory (`@example.com` contacts).
  - Loads walk1 (first walk) and walk2 (re-walk), plus walk3 if present, from cache, at zero API cost.
  - `DEMO_MODE` banner: "This is a demo project. Upload your own walk to try it live."
- [ ] **8.2 Landing page** per DESIGN.md. On desktop: the live app inside a phone frame, plus a QR code. On a phone: straight into the demo project.
- [ ] **8.3 Screenshot audit.** The verifier runs `make screenshots` and checks all checklist screens against DESIGN.md; the ui-builder fixes what it finds; repeat until PASS.
- [ ] **8.4 Polish.** Empty, error and loading states everywhere; accessibility floor; no console errors; dark mode *(nice)*.

## Phase 9: Ship (Track C)

- [~] **9.1 Docker + deploy.** *(image + fly.toml + runbook verified; the deploy itself is reserved for the human — see STATE.md → Needs human)*
  - Multi-stage Dockerfile, `fly.toml`, volume at `/data`, health check.
  - Make sure the demo media and cached results that `make seed` needs reach the deployed app (baked into the image or copied to the volume).
  - If `fly auth whoami` works or `FLY_API_TOKEN` is set: deploy, seed, and smoke-test the live URL with the iPhone profile.
  - Otherwise stop in a deploy-ready state and put the exact commands under Needs human.
- [ ] **9.2 README.** Include:
  - pitch, live URL, video link placeholder, iPhone screenshots (copy from `.verify/screens/` to `docs/screens/`)
  - architecture diagram (Mermaid)
  - each Meta model and exactly what it does
  - open-weights statement (Muse Spark Open via Baseten, slug `meta-models/muse-spark-open-1026`)
  - eval table, including the holdout
  - cost and time per walk
  - how to run, limitations
  - roadmap: live hands-free capture through Meta's Wearables Device Access Toolkit (glasses stream straight in, items are confirmed by voice, a trade looks at a defect and hears its item), Procore/Fieldwire sync, spec citations with Muse Spark 1.3's 1M context, live captions
  - **"Built during the Meta Global AI Developer Hackathon, Nov 3–4, 2026. Everything in this repo was created during the event."**, followed by a dated feature list
- [ ] **9.3 `docs/DEMO_SCRIPT.md`.** Built from the shot list below, with the exact screens to capture on the iPhone for each beat.
- [ ] **9.4 Final pass.** `make test`, `make eval`, `make screenshots` (all PASS), a fresh clone sets up cleanly, `docker build` works, no secrets anywhere in git history.

## If ahead (in order)

1. Spec citations: load public-domain UFGS spec sections (wbdg.org) into Muse Spark 1.3's 1M context and add a "Spec reference" to each item (clearly labeled as the hosted model).
2. Live captions while recording: Voice Transcribe realtime WebSocket from the Record screen.
3. CSV export in a generic punch-tool format.

---

## Demo video shot list (3:00, recorded on an iPhone)

The app runs from the iPhone Home Screen (installed PWA, no Safari bar), captured with iOS Screen Recording.

| Time | Beat |
|---|---|
| 0:00–0:12 | **Hook:** 2 s of the inspector in Ray-Ban Metas pointing at a defect, then the glasses' view with pointing events lighting up. "The walk takes minutes. The write-up takes hours, and the final payment waits on it." |
| 0:12–0:40 | **Record:** add the glasses clips → Upload walk → the processing checklist fills in, naming each Meta model as it works. |
| 0:40–1:30 | **Punch list:** filter to Electrical and Functional; a card with its tape tab, outlines, AI summary and action; "Play this moment" jumps to the exact second. Walk detail with items sliding in as the video plays. |
| 1:30–2:00 | **Trades:** "Send 3 items to Ace Electric" → the Slack message lands. An item whose frame doesn't support the description shows up as Needs a look. |
| 2:00–2:35 | **Re-walk summary:** items signed off with before/after; "Marked fixed on the re-walk, but the defect is still visible." |
| 2:35–3:00 | **How this walk was processed** (models, time, cost) and eval numbers; "built Nov 3–4"; live URL and QR code. |

## Human checkpoints

- **Before bed:** read STATE.md → Needs human; make sure `fly auth whoami` works (or `FLY_API_TOKEN` is set) and the Slack webhook is in `.env`.
- **Wed 8 AM:** open the live URL on your iPhone; run one fresh upload end to end.
- **Wed 12 PM:** code freeze.
- **Wed 12–4 PM:** record and edit the video, plus a 30–45 s cut for X.
- **Wed by 7 PM:** submit (public repo, video, X post link, live URL). Theme: *Build for Your World*.
