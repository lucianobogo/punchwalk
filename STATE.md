# STATE

Status: RUNNING
Updated: 2026-11-03 18:45 ET
Current phase: A = 0.6 record + 2.5 fixes + 7.x (target Wed 7:30 AM); B = 6.1 UI/7.4 UI/8.2 (Phase 5 done, target was Wed 3 AM); C = CHANGE-2 + 8.1 seed; eval = 3.1–3.3 — all ahead of the PLAN clock
Last checkpoint: ckpt-5.4 (tagged; 0.1–0.5, 0.7, 4.1–4.4, 5.1–5.4 verified). Untagged commits pending verification: 0.6 script (0228dab; real fixtures pending), 2.5 (94626ef; fix round 1 with Track A), 8.1 (7dc84f3 + fixes; verified on synth; tag after real walks seed), 9.2/9.3 drafts. 6.1 UI/7.4 UI/8.2 (92ac215..2d5783a) verified except two touch targets — tag after the fix. Tagged: 0.1–0.5, 0.7, 1.1–1.7, 2.1–2.4, 3.1–3.3, 4.1–4.4, 5.1–5.7, 6.1-backend, 6.2, 6.3, 9.1-image

## Next actions
1. Review + verify Track A (0.6, 1.x), Track C (4.x), Track B (5.1); commit one checkpoint per subphase.
2. When footage/ lands: `make record` (0.6) via the Track A builder; re-check the tape phrasing on a real frame.
3. After 4.x passes: brief 5.8 (UI → live API) and Phase 2 (items, grounding, verification).

## In flight
| Task | Agent | Started (ET) | Paths | Notes |
|---|---|---|---|---|
| eval follow-ups 2 (fail loudly on cache miss, template files skip not block, case-insensitive trades/rooms, model_label for the catalog slug, blank-trade labels, build_mock_api checks) + first real `make eval` on walk1/walk3 | builder (eval, same agent) | 18:43 | eval/**, tests/test_eval*, punchwalk/core/enums.py + web/src/api/types.ts (MODEL_LABEL only), punchwalk/api/views.py (meta-files label) | briefed |
| 0.6 + 2.5 fixes + 7.1–7.4 + ingest fps verify (worktree .verify/wt-phase7 @ 0179d57) | verifier | 18:45 | read-only | commits untagged pending PASS |
| Track A: 3.4 tuning on real footage (≤ 2 h; never on walk3) + fingertip clamp, then re-run walk2, then 7.5 | builder (Track A, same agent) | 18:45 | punchwalk/pipeline/**, punchwalk/render/**, params.py, fixtures/cache, scripts/record.py | briefed; eval numbers relayed from the eval builder |
| 5.y fixes + 6.1/7.4/8.2 fix round 1 (two 44 px targets + minors) → 5.8 live data + 8.4 polish | ui-builder (same agent) | 18:28 | web/** except types.ts | briefed by message |

## Needs human
- **Deploy needs you (the permission classifier blocks me from running Fly deploys).** The image is built and tested (`docs/DEPLOY.md` has the full runbook). From the repo root:
  ```
  fly apps create punchwalk            # if the name is taken: pick one, edit `app` + APP_BASE_URL in fly.toml
  fly volumes create punchwalk_data --region ewr --size 3 -a punchwalk -y
  grep -E '^(MODEL_API_KEY|BASETEN_API_KEY|SLACK_WEBHOOK_URL|RESEND_API_KEY|EMAIL_FROM)=.+' .env | fly secrets import -a punchwalk
  fly deploy --remote-only             # later redeploys: make deploy
  curl -s https://punchwalk.fly.dev/healthz
  ```
  After 8.1 lands, seed the demo project: `fly ssh console -a punchwalk -C "sh -c 'cd /app && setpriv --reuid=punch --regid=punch --init-groups /app/.venv/bin/python scripts/seed.py'"`. Or restart the orchestrator with permission to run Fly commands and it will do all of this.
- **License:** the repo goes public before submission and has no LICENSE file. Pick one (MIT suggested) and drop it in as `LICENSE`; README has a `<!-- LICENSE -->` placeholder.
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
- Deploy: image verified (1.08 GB; in-image tests 57 pass incl. PDF + HDR; smoke ok); fly.toml valid; NOT deployed — human runbook in Needs human / docs/DEPLOY.md
- Live URL: none

## Key facts learned
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
