# Punchwalk

Turn a construction punch walk into a verified, dispatched punch list.

An inspector walks a space wearing Ray-Ban Meta glasses (or holding a phone), points at each defect with an index finger for about 2 seconds, and describes it out loud. Nothing is staged or stuck on the wall. Punchwalk:

1. transcribes the narration with timestamps (**Muse Voice Transcribe**),
2. tracks the inspector's hand through the video (**SAM 3.1**); where the hand holds still, the fingertip marks the defect's location (a **pointing event**),
3. pairs each spoken description with the pointing event it belongs to (a description with no pointing becomes a voice-only item),
4. writes a punch item for each one: an AI summary of the problem, the action needed, trade, room and severity, plus an evidence frame with the defect outlined and a dot where the finger pointed (**Muse Spark Open** on Baseten names the target, **SAM 3.1** draws the pixel-precise outline),
5. checks every item against its own evidence and the original quote (**Muse Spark Open**),
6. sends each trade its list (Slack, email, or the in-app outbox),
7. on a re-walk, signs off fixed items with before/after proof, re-sends open ones, and flags anything "marked fixed, but the defect is still visible".

Project managers mostly use it **on their phones**: a filterable punch list of freeze-frames and summaries, with a button that plays the exact moment each item was called out.

Tagline: *The voice says what. SAM proves where. Spark Open writes it up and checks it.*

---

## How this repo gets built

- The **orchestrator** (Claude Fable 5.1, `.claude/agents/orchestrator.md`) runs as the main session. It plans, delegates, verifies and keeps state. It does not write product code.
- **Workers** (Claude Opus 5.5) do every implementation task and report back:
  - `builder`: pipeline, model clients, API, eval, scripts, Docker, deploy, docs
  - `ui-builder`: everything in `web/`, following DESIGN.md
  - `verifier`: independent acceptance checks; never edits product code
- **Coordination files** are owned by the orchestrator, and workers never edit them:
  - `PLAN.md`: what to build, with checkboxes
  - `STATE.md`: where the build is right now
  - `PROGRESS.md`: append-only log
  - `DECISIONS.md`, `BACKLOG.md`
- **Resume:** a SessionStart hook (`.claude/hooks/session-context.sh`) prints STATE.md, the latest PROGRESS.md entries and git status into every new, resumed or compacted session. Any session can continue from there.

## Hackathon constraints (these override everything else)

- Meta Global AI Developer Hackathon, **Startup track**. Submission due **Wed Nov 4, 2026, 9:00 PM ET**. **Code freeze: Wed 12:00 PM ET.** After the freeze, only demo-critical fixes.
- Rubric: Impact 30 · Use of Meta AI tooling 25 · Technical depth & execution 20 · Demo 25. The Startup track is judged on whether the product holds up in production. Judges use the 3-minute video, this repo, and the live product.
- **Bonus target: Best Build on Meta Open Weight Models.** Every LLM call in the product path goes to **Muse Spark Open via Baseten**. The hosted `muse-spark-1.3` is only an automatic, logged fallback.
- Everything in this repo is built during the event (Nov 3–4, 2026). The README must say so. The repo goes public before submission.
- Use only our own footage and assets. No third-party media.

## Capture devices

- **Ray-Ban Meta glasses** are the hero form factor: hands-free and at eye level, with the camera pointing where the inspector looks. Their video is portrait, clips are at most 3 minutes, and they reach us via the Meta AI app → phone → upload.
- A phone works too, in landscape or portrait.
- **How to point:** look straight at the spot, point at the defect with an index finger and hold it still for ~2 s while saying what's wrong. Keep the defect itself uncovered (point at its edge). A defect described without pointing still becomes an item (voice-only).
- A walk can be uploaded as **several clips in order**. The pipeline concatenates them and records clip boundaries; SAM tracks never merge across a boundary.
- Respect rotation metadata. Never letterbox portrait video into a landscape frame.

## Rules for every agent

1. Before changing anything, read the parts of CLAUDE.md, PLAN.md and DESIGN.md that your task touches. Trust the API facts below over memory.
2. Stay inside the scope you were given. Don't refactor unrelated code; mention anything else you notice in your report.
3. `make test` runs fully offline from the cache and fixtures. Live API calls go only through `punchwalk/clients/*` (with the cache and cost ledger), and only in `make probe`, `make record`, `make eval-live`, or real uploads.
4. **Hard cap: $25** of live Meta + Baseten spend during development (ledger: `data/cost_ledger.jsonl`). If it's reached, stop making live calls and report it.
5. Secrets live only in `.env` (gitignored and unreadable by agents). Keep `.env.example` current. Never print keys.
6. Never modify `footage/` or `eval/labels/` (human-provided ground truth).
7. **Look at what you make.** Open rendered images with Read. Take Playwright screenshots of UI (WebKit, `iPhone 15 Pro` profile, 393×852) and look at them before reporting done.
8. Never ask the human questions and never wait for an answer. If something needs a human (a credential, a device), say so in your report and keep going on everything else.
9. Never force-push, rewrite git history, or delete anything outside the repo.
10. **Workers:** don't commit, don't edit coordination files, don't spawn agents. End with the report format in your agent file.

## Stack (fixed; don't relitigate)

- **Backend:** Python 3.11, FastAPI, SQLModel + SQLite (WAL) under `DATA_DIR`, in-process job worker polling a `jobs` table (no Redis, no Celery). `uv` for dependencies.
- **Media/CV:** system `ffmpeg`/`ffprobe`, `opencv-python-headless`, numpy, scipy (`linear_sum_assignment`), Pillow.
- **Model clients:** `httpx` (Files, Responses streaming, ASR), `openai` SDK (Baseten + Meta chat completions), `meta-sam-parser` (SAM output parsing + prompt building), pydantic v2.
- **PDF:** Jinja2 HTML → WeasyPrint.
- **Web:** React + TypeScript + Vite + Tailwind CSS, a mobile-first PWA per DESIGN.md. Libraries:
  - `@fontsource-variable/archivo`
  - `lucide-react`
  - `vaul` (bottom sheets)
  - `@tanstack/react-query`
  - `react-router`
  - `qrcode` (landing page)

  Built into `punchwalk/static/` and served by FastAPI (one service, one URL). Video overlays: `<video>` plus an absolutely positioned SVG layer, synced with `requestVideoFrameCallback` (fallback: `timeupdate`).
- **UI tests:** Playwright (WebKit) with the `iPhone 15 Pro` device profile. `make screenshots` writes every screen in DESIGN.md's checklist to `.verify/screens/`.
- **Deploy:** one Docker image (node build stage → python runtime with ffmpeg + WeasyPrint system libs) on Fly.io with a volume at `/data`.
- **Make targets:** `setup dev test eval eval-live probe record seed build screenshots docker deploy`.

## Read these docs first (Phase 0). Trust them over memory.

- SAM: https://dev.meta.ai/docs/sam/segmenting · https://dev.meta.ai/docs/sam/reading-segmentation · https://dev.meta.ai/docs/sam/client-libraries · cookbook: https://github.com/meta-models/meta-model-cookbook/tree/main/07_sam
- Voice: https://dev.meta.ai/docs/speech-to-text · cookbook: https://github.com/meta-models/meta-model-cookbook/tree/main/06_muse_voice
- Files / structured output: https://dev.meta.ai/docs/file-handling · https://dev.meta.ai/docs/structured-output
- Baseten: https://docs.baseten.co/reference/inference-api/chat-completions · https://docs.baseten.co/inference/model-apis/vision · https://docs.baseten.co/inference/model-apis/reasoning

## API facts (verified Nov 3, 2026)

### Meta Model API

Base `https://api.meta.ai/v1`, header `Authorization: Bearer $MODEL_API_KEY`.

**Files:** `POST /v1/files` (multipart, `purpose=user_data`) → `id`. Synchronous: a 200 means the file is ready.

**SAM 3.1** (`"model": "sam-3.1"`) via `POST /v1/responses`:
- One user message. `content` = one text part + one media part. The text is either a short noun phrase (one concept, ≤ ~100 chars) **or** a box/point prompt built with `meta_sam_parser.build_object_prompt`, never both. One concept per request.
- Media part: `{"type": "input_video", "file_id": ...}` (or `video_url`), or `{"type": "input_image", "image_url": ...}` (confirm in the probe whether a data URL works or an upload is needed).
- Video: MP4/MOV with H.264/H.265/MPEG-4 (VP9/WebM rejected). ≤ 15,000 frames. ≤ 16 tracked objects per frame, ≤ 16 prompted objects. Billed per frame ($0.20 per 1k), minimum 50 frames per request. Images: $2.50 per 1k.
- `"stream": true` (send `Accept: text/event-stream`): one `response.output_text.delta` line per frame; `response.completed` ends the stream. Zero matches = empty output, which is valid, not an error.
- `metadata`: `{"include_confidence": "true", "mask_encoding": "one_bit"}`. For box/point prompts on video: `propagation_direction` (`both` is the default).
- Each frame line looks like `<Nf>id<|box;x1=..;y1=..;x2=..;y2=..;w=W;h=H;c=..|><|mask;x=0;y=0;c=..;data=H,W,<enc>payload|>,...` with a **base85** payload. **Never hand-parse it.** Use `meta-sam-parser`: `decode_mask_to_raster`, `decode_mask_to_rle`, `decode_mask_to_svg_path`, `build_object_prompt` (types `FramePrompt`, `PromptBox`, `PromptObject`, `PromptPoint`). Check `diagnostics` even when the stream completes.
- Frame index `N` is zero-based and can skip. Always read it from the marker. Box coordinates are source pixels. Prompt coordinates must use the media's pixel size (an aspect mismatch returns 400).
- Object ids are stable only while an object stays in view, **not** across exit/re-entry/occlusion. We merge tracks ourselves.
- **Mask coordinate space:** masks are smaller than the frame. In Phase 0, render a decoded mask over its frame and confirm the mapping (downscaled full frame vs. box-relative). Record the answer in `DECISIONS.md` and encode it in one helper.

**Muse Voice Transcribe** (`muse-voice-transcribe-1.0`): `POST /v1/asr/transcribe`, multipart with two parts:
- `request` = JSON `{"mode": "ENDPOINTING", "model": "muse-voice-transcribe-1.0", "audioEncoding": "WAV", "languageBias": ["English"], "keywords": [...]}`
- `audio` = WAV, mono, 16-bit PCM, 16 or 24 kHz. ≤ 32 MB, ≤ 10 minutes.
- Response: `{transcript, audioDurationMs, turns: [{turnId, startMs, endMs, transcript}]}`. Turn-level timestamps only (no word timestamps).
- 429 → exponential backoff. 500 → retry once.
- Extract audio: `ffmpeg -i in.mp4 -vn -ac 1 -ar 16000 -c:a pcm_s16le -map_metadata -1 audio.wav`

**Muse Spark 1.3** (`muse-spark-1.3`, fallback only): `POST /v1/chat/completions` with `response_format: {"type": "json_schema", "json_schema": {"name": ..., "strict": true, "schema": ...}}`. Strict schemas: the root is an object, every object sets `additionalProperties: false` and lists all its keys in `required`, no `oneOf`/`allOf`. With reasoning on, set max tokens ≥ 4000.

### Baseten: Muse Spark Open (primary reasoning model)

- OpenAI SDK with `base_url="https://inference.baseten.co/v1"`, `api_key=$BASETEN_API_KEY`, `model="meta-models/muse-spark-open-1026"`. This is the hackathon slug. **Do not** use the docs' `meta-models/muse-spark-open`.
- `response_format` supports `json_schema` and `json_object`. Also supports `tools` and `reasoning_effort` (`none` … `max`). Default to `low` for per-item calls and `medium` for verification. Always set `max_tokens` explicitly (if omitted, context reservation is throttled to ~49.5k tokens).
- Images: `{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}`. ≤ 8 images per request, ≤ 10 MB each. Send JPEGs ≤ 1600 px on the long side, quality ≈ 85. Video only by URL (≤ 2 per request, ≤ 300 s; the model samples ≤ 96 frames and ignores audio). No PDFs.
- Read the Baseten reasoning doc for how reasoning text comes back. Never store raw reasoning in item text; ask for a short `rationale` field in the schema instead.
- Keep schemas flat and simple. Represent optional values as nullable fields that are still listed in `required`. Test every schema once in the probe.
- Validate every JSON response with Pydantic. On failure, retry once with the validation error appended. If it still fails, mark the item `needs_review`. Never crash a walk.
- `REASONING_PROVIDER=baseten|meta` (default `baseten`). After 2 failed retries on 5xx/timeout, fall back to `muse-spark-1.3` and log `provider="meta-fallback"` in `model_calls`. The UI shows it.

## Domain model (keep names consistent across code, API and UI)

- **Project:** id, name, address, trades `[{trade, company, contact, email, phone, slack_webhook?}]`, keywords[], rooms[].
- **Walk:** id, project_id, kind `initial | rewalk`, status `uploaded → processing → ready | failed`, clips[] (order, filename, duration_s, offset_s), stage progress + timings, media paths, duration_s, fps, size, orientation, error.
- **Turn:** id, walk_id, start_ms, end_ms, text, kind `location | defect | status_update | other`, room.
- **Point** (one pointing event: the hand held still at a defect): id (`pnt_`), walk_id, number (1-based by time), t, frame (sam.mp4 index), fingertip_x/fingertip_y, hand_box, hold_s, sam_object_ids[], mean_conf, turn_id?. Display pixels.
- **Item:** project-scoped number (`#014`), origin_walk_id, point_id? (null = voice-only), turn_ids[], title, description, action, trade, room, location_detail, severity `cosmetic | functional | safety`, status `open | dispatched | closed | needs_review`, review_reason?, video_start_s, video_end_s, evidence {t, image, raw, crop, defect_svg, defect_box, crop_box, pointer {x, y}? (the "pointed here" marker in the evidence frame), pointing_frame? (the frame with the hand, outlined), hand_box?, grounding `sam_concept | sam_box | pointed_only | none`}, verification {consistent, note, provider}, closure {rewalk_id, after_image, voice_status, vision_status, decision}, history[]. `pointed_only` = pointed but no defect outline; `none` = voice-only, no marker.
- **Dispatch:** id, project_id, trade, item_ids[], channel `slack | email | outbox`, sent_at, payload.
- **ModelCall:** id, walk_id, stage, model, provider, latency_ms, tokens_in/out?, frames?, est_cost_usd, cached, ok, error?.
- **Trades enum:** electrical, plumbing, drywall, painting, carpentry_millwork, doors_hardware, flooring, windows_glazing, hvac, appliances, cleaning, general.
- **UI labels** (see DESIGN.md):
  - statuses: `open` and `dispatched` → "Open" (sending is shown as a detail: "Open, sent to Oakline Millwork Nov 3"), `needs_review` → "Needs a look", `closed` → "Signed off"
  - severities: "Cosmetic", "Functional", "Safety"
  - trades are shown in plain words ("Carpentry & millwork")

## Pipeline overview (details and thresholds in PLAN.md)

`ingest → transcribe → track hands (SAM) → find points → classify turns → fuse speech↔points → pick evidence → write items (Spark Open) → ground defect (SAM) → verify (Spark Open) → [rewalk: resolve closures] → persist`

Stage ids: `ingest, transcribe, track_hands, find_points, classify_turns, fuse, pick_evidence, write_items, ground_defects, verify, resolve_closures, persist`.

Every stage is idempotent, resumable, cached and timed, and writes a `ModelCall` row for each external call.

## Cache and fixtures

- `punchwalk/clients/cache.py`: key = sha256(provider, endpoint, model, canonical request JSON minus volatile fields, sha256 of any uploaded bytes). Store raw SAM stream text and JSON responses under `data/cache/`. `PUNCHWALK_CACHE=readwrite|readonly|off`. Tests run `readonly` and fail loudly on a cache miss.
- Commit small fixtures in `fixtures/`: transcripts, raw SAM outputs, Spark outputs for the labeled walks, and `fixtures/mock_api/` (realistic API responses the web app can run on alone). Raw footage stays in `footage/` (gitignored). Commit 720p demo copies to `demo/` only if each is < 40 MB.

## Layout

```
punchwalk/   api/  core/  pipeline/  clients/  render/  dispatch/  static/
web/         React PWA (src/api/types.ts mirrors punchwalk/api/schemas.py)
eval/        labels/*.csv (human-provided), labels_template.csv, run_eval.py, report.md
fixtures/    probe/, mock_api/, per-walk recorded outputs
demo/  scripts/  tests/  docs/  .verify/ (scratch, gitignored)
.claude/     agents/ (orchestrator, builder, ui-builder, verifier), hooks/, settings.json
```

## UX summary (DESIGN.md has the details and wins any conflict)

- **Phone first.** The iPhone 15 Pro viewport (393×852) is the primary target; desktop shows the app in a centered column, plus a landing page with the live app inside a phone frame.
- **Match the renders.** `docs/design/*.png` are reviewed renders of every key screen. Open them before building a screen.
- **The punch list is home.** Nobody should have to watch a full video. It's a list of compact rows: a thumbnail with its number tab, the title, a short AI summary, and a play button that jumps to the exact second. Item detail adds the big photo, the action needed, and the inspector's original quote as the source.
- Every finding shows its proof: the defect outlined in red `#D93B30`, plus a small blue dot `#2F86E0` where the finger pointed. In video, the hand is outlined in blue while it points. The numbered tabs keep the light painter's-tape blue look `#5AA2EA` with the number in graphite (the component is still called the "tape tab"; it no longer stands for physical tape).
- Where judges look (processing screen, "How this walk was processed"), name the Meta models plainly: "Muse Voice Transcribe", "SAM 3.1", "Muse Spark Open on Baseten". The SAM step reads "Following your hand (SAM 3.1): N points found".
