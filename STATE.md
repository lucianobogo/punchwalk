# STATE

Status: RUNNING
Updated: 2026-11-03 18:59 ET
Current phase: A = 0.6 record + 2.5 fixes + 7.x (target Wed 7:30 AM); B = 6.1 UI/7.4 UI/8.2 (Phase 5 done, target was Wed 3 AM); C = CHANGE-2 + 8.1 seed; eval = 3.1–3.3 — all ahead of the PLAN clock
Last checkpoint: ckpt-5.4 (tagged; 0.1–0.5, 0.7, 4.1–4.4, 5.1–5.4 verified). Untagged commits pending verification: 8.1 (verified on synth; tag after real walks seed), 5.8 + 8.4 + UI fix rounds (verifying), 9.2/9.3 drafts. 6.1 UI/7.4 UI/8.2 (92ac215..2d5783a) verified except two touch targets — tag after the fix. Tagged: 0.1–0.7, 1.1–1.7, 2.1–2.5, 3.1–3.3, 4.1–4.4, 5.1–5.7, 6.1-backend, 6.2, 6.3, 7.1–7.3, 7.4-pipeline, 9.1-image

## Next actions
1. Review + verify Track A (0.6, 1.x), Track C (4.x), Track B (5.1); commit one checkpoint per subphase.
2. When footage/ lands: `make record` (0.6) via the Track A builder; re-check the tape phrasing on a real frame.
3. After 4.x passes: brief 5.8 (UI → live API) and Phase 2 (items, grounding, verification).

## In flight
| Task | Agent | Started (ET) | Paths | Notes |
|---|---|---|---|---|
| eval follow-ups 2 (fail loudly on cache miss, template files skip not block, case-insensitive trades/rooms, model_label for the catalog slug, blank-trade labels, build_mock_api checks) + first real `make eval` on walk1/walk3 | builder (eval, same agent) | 18:43 | eval/**, tests/test_eval*, punchwalk/core/enums.py + web/src/api/types.ts (MODEL_LABEL only), punchwalk/api/views.py (meta-files label) | briefed |
| CHANGE-4: Closure.before_after (+ ClosureOut.before_after_url, types.ts, mocks, views) | builder (fresh fix builder) | 18:56 | contract files + scripts/gen_mock_api.py + punchwalk/api/views.py | briefed; Track A writes the field |
| Track A: 3.4 tuning on real footage (≤ 2 h; never on walk3) + fingertip clamp, then re-run walk2, then 7.5 | builder (Track A, same agent) | 18:45 | punchwalk/pipeline/**, punchwalk/render/**, params.py, fixtures/cache, scripts/record.py | briefed; eval numbers relayed from the eval builder |
| 5.8 + 8.4 + UI fix rounds verify (worktree .verify/wt-ui4 @ 63ceda3) | verifier | 18:49 | read-only | ui-builder idle until real-walk screenshots (after 3.4 + seed) |

## Needs human
- Deploy: DONE by the human (18:55 ET) — https://punchwalk.fly.dev is live (healthz ok, demo_mode true, PWA served, /mock-api 404). Remaining on the live app: the demo data. After Track A's 3.4 tuning, the orchestrator builds the seed snapshot on this Mac (`scripts/seed.py --reset --export demo/seed_snapshot.tar.gz`); then it must reach Fly — the orchestrator will try `fly ssh sftp` / `fly ssh console` itself; if the permission classifier blocks that, the exact commands appear here (they're also in docs/DEPLOY.md → "Seed").
- License: DONE — MIT, copyright 2026 Luciano Bogomolni (LICENSE at the repo root; README's `<!-- LICENSE -->` placeholder gets filled in the final README pass).
- **Slack on camera (optional):** the seeded Ace Electric webhook is an example-domain placeholder (never contacted). To make the Slack beat real, put your webhook in `.env` as SLACK_WEBHOOK_URL (global) or follow the pre-flight swap in docs/DEMO_SCRIPT.md.
- Keys are in `.env` (confirmed by name, 16:15 ET); both work (probe 9/9 ok).
- **Optional check with the organizers:** the hackathon Baseten slug `meta-models/muse-spark-open-1026` returns 404 ("please check the model you provided"); the catalog slug `meta-models/muse-spark-open` answers. The client tries -1026 first and falls back automatically, logging which slug answered. If the organizers say -1026 should be live, nothing changes; if they say to use the catalog slug, set `SPARK_OPEN_MODEL=meta-models/muse-spark-open` in `.env`.
- Human decisions on record: **no git pushes from this build** (human pushes manually; checkpoints are committed + tagged locally). **Slack webhook**: human will decide later; dispatch uses the in-app outbox until `SLACK_WEBHOOK_URL` is set.
- Labels: DONE (18:42 ET) — walk1 (12 items), walk2 (re-walk statuses incl. two false claims) and walk3 (2 holdout items) are in `eval/labels/` and committed. Thank you.

Fly: `fly auth whoami` works (lucianob@consaitech.com), so deploy can run unattended.

## Health
- make test: 427 passed + 1 skipped (Pango) Python, 100 web (Track A report 18:45)
- make eval: harness verified; real labels landed 18:42 → first real run on walk1/walk3 in progress; walk2 after Phase 7
- make screenshots: 33/33 strict at 8.2 (ui-builder); verification of 6.1 UI/7.4 UI/8.2 running
- Live API spend: $1.43 of $25 at 18:45 (real walks: record $0.44 + full runs $0.54; synthetic fixtures $0.45)
- Deploy: LIVE at https://punchwalk.fly.dev (human ran the runbook 18:55 ET); demo project not seeded yet (snapshot after 3.4)
- Live URL: https://punchwalk.fly.dev

## Key facts learned
- Re-walk vs labels (verifier, pre-tuning): 10/12 closure decisions right; both false claims (L3 stovetop, L12 hallway pencil marks) caught; no wrong sign-offs; 2 false alarms (L4 switch screw, L8 window screen); walk1 13 items vs 12 labels (one duplicate).
- Real footage results (Phase 1): walk1 14/16 turns got a pointing hold, hand conf 0.90; full run: 13 items (one duplicate from a missed continuation turn), 2 needs a look; re-walk: 6 signed off, 4 false-fix flags (#13 confirmed by eye), 2 re-sent, 1 not checked. Known issues for 3.4: hand still in the evidence frame on long holds; defect_box on the wrong object (#2 range hood); classify continuation duplicates.
- Real footage (18:01): Ray-Ban Meta, HEVC, 120 fps, portrait, sizes vary per clip (1552×2064 … 1376×1840); walk1 = a–e (103 s), walk2 = a–e (77 s), walk3 = 15 s; 335 MB total (gitignored). Labels: template copies → Needs human.
- **CHANGE REQUEST 17:01 ET (human): no blue tape.** The inspector points with an index finger (~2 s); SAM tracks the hand; fingertip = defect location. PLAN.md 0.6/1.2/1.3/1.5/1.6/2.x/3.2/4.3/5.5/7.x updated; contract renamed Mark→Point; evidence shows a red defect outline + a small blue dot; item numbers keep the tape-tab look.
- SAM masks are box-local rasters (H×W stretches over [x1,x2+1)×[y1,y2+1)); helper `punchwalk/render/masks.py: mask_to_frame_svg()`; scale by frame/media size (verified on probe frame).
- SAM image input works as a data URL; strict `json_schema` works on Baseten Spark Open (always send `reasoning_effort`; default is xhigh); ASR turnId is zero-based.
- Hand concept (synthetic frames): "index finger" 2.64 ≈ "hand" 2.59 ≫ "pointing hand" 0 → default `SAM_HAND_CONCEPT=hand`; re-rank on real footage in `make record`. (Tape phrasing result superseded.)
- Cache: reads data/cache then fixtures/cache; `make probe` replays at $0 in readonly mode. Ledger: data/cost_ledger.jsonl.
- Tools present: uv 0.12, Python 3.12 system (project pins 3.11 via uv), node 24 + pnpm 9.15, ffmpeg 9, docker 29, flyctl (logged in).
- .env was an unmodified copy of .env.example at session start (all keys empty).
- Agreed Make entry points (used by later briefs): dev=`uvicorn punchwalk.api.app:app`, probe=`scripts/probe.py`, record=`scripts/record.py`, seed=`scripts/seed.py`, eval=`eval/run_eval.py`, build=`pnpm -C web build` → `punchwalk/static/`, screenshots=`pnpm -C web screenshots` → `.verify/screens/`.

## Final handoff
- not written yet. README placeholders to fill at the end: VIDEO_URL, live URL "(deploying)", SCREENS_REAL, EVAL_TABLE, COST_REAL, NOV4_FEATURES, LICENSE; DEMO_SCRIPT item-number table after seeding.
