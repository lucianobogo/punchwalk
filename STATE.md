# STATE

Status: RUNNING
Updated: 2026-11-03 18:32 ET
Current phase: A = 0.6 record + 2.5 fixes + 7.x (target Wed 7:30 AM); B = 6.1 UI/7.4 UI/8.2 (Phase 5 done, target was Wed 3 AM); C = CHANGE-2 + 8.1 seed; eval = 3.1–3.3 — all ahead of the PLAN clock
Last checkpoint: ckpt-5.4 (tagged; 0.1–0.5, 0.7, 4.1–4.4, 5.1–5.4 verified). Untagged commits pending verification: 0.6 script (0228dab; real fixtures pending), 2.5 (94626ef; fix round 1 with Track A), 3.1–3.3 (71c7a54..e75329f; verify after synth re-record), 8.1 (7dc84f3; verified on synth; tag after real walks seed), 9.2/9.3 drafts. Tagged: 0.1–0.5, 0.7, 1.1–1.7, 2.1–2.4, 4.1–4.4, 5.1–5.7, 6.1-backend, 6.2, 6.3, 9.1-image

## Next actions
1. Review + verify Track A (0.6, 1.x), Track C (4.x), Track B (5.1); commit one checkpoint per subphase.
2. When footage/ lands: `make record` (0.6) via the Track A builder; re-check the tape phrasing on a real frame.
3. After 4.x passes: brief 5.8 (UI → live API) and Phase 2 (items, grounding, verification).

## In flight
| Task | Agent | Started (ET) | Paths | Notes |
|---|---|---|---|---|
| 3.1–3.3 + follow-ups verify (worktree .verify/wt-eval @ 4e9a137) | verifier | 18:32 | read-only | synth_walk re-recorded by Track A, so the e2e replay is real |
| Track A: make record (walk1/2/3) + full runs (walk1, walk3) → 2.5 fixes → 7.1–7.4 → full run walk2 | builder (Track A, same agent) | 18:04 | punchwalk/pipeline/**, punchwalk/render/**, scripts/record.py, scripts/synth_clip.py, fixtures/pipeline/**, tests/test_pipeline_* | briefed by message |
| 8.1/CHANGE-2 small fixes (seed interrupt safety, channel_reason email case, --force ModelCall rows, hints, validators) | builder (fresh) | 18:25 | scripts/seed.py, punchwalk/dispatch/service.py, scripts/gen_mock_api.py (+ regen), punchwalk/config.py, punchwalk/api/schemas.py comment, docs/DEPLOY.md, tests | briefed |
| 6.1 UI + 7.4 UI + 8.2 verify (worktree .verify/wt-ui3 @ 2d5783a) | verifier | 18:28 | read-only | committed untagged pending PASS |
| 5.y fixes → 5.8 live data + 8.4 polish | ui-builder (same agent) | 18:28 | web/** except types.ts | briefed by message |

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
- **Labels (blocks the PLAN 3.4 eval targets): `eval/labels/*.csv` are still byte-identical copies of the template.** To make it fast, the pipeline's own output was turned into DRAFTS at `eval/labels_draft/walk1.csv` (13 rows, parts a–e) and `walk3.csv` (2 rows) — see `eval/labels_draft/README.md`. Correct them against what you actually pointed at and said (delete the duplicate stove row, fill walk3's room, check every time, room, trade and `pointed`), then copy them to `eval/labels/walk1.csv` and `walk3.csv`. Write `eval/labels/walk2.csv` by hand per FOOTAGE.md (one row per item you addressed on the re-walk, same item_no as walk1, time within the part + clip letter, fixed_in_rewalk = truth, claimed_fixed_in_rewalk = what you said) — a walk2 draft will appear once the re-walk pipeline (Phase 7) has run. Then `make eval` scores for real.

Fly: `fly auth whoami` works (lucianob@consaitech.com), so deploy can run unattended.

## Health
- make test: Python 305 passed after CHANGE-1 (builder report, 17:28); web typecheck/test/build green at 5.1 (29 screens)
- make eval: harness built; on real labels: blocked (labels are the template). Synthetic walk: recall 1.0, precision 1.0, trade 0.67, room 1.0
- make screenshots: 33/33 strict at 8.2 (ui-builder); verification of 6.1 UI/7.4 UI/8.2 running
- Live API spend: $1.07 of $25 at 18:25 (probe + synthetic fixtures + real-walk recording in progress: data/record/walk1|walk2|walk3 exist)
- Deploy: image verified (1.08 GB; in-image tests 57 pass incl. PDF + HDR; smoke ok); fly.toml valid; NOT deployed — human runbook in Needs human / docs/DEPLOY.md
- Live URL: none

## Key facts learned
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
