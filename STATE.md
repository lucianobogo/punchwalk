# STATE

Status: RUNNING
Updated: 2026-11-03 18:01 ET
Current phase: 1 (A) / 4 (C) / 5 (B) in parallel; 0.6 pending footage — targets Tue 8:30 PM / 10:00 PM / Wed 3:00 AM
Last checkpoint: ckpt-5.4 (tagged; 0.1–0.5, 0.7, 4.1–4.4, 5.1–5.4 verified). Untagged commits pending verification: 0.6 script + 1.1–1.7 (0228dab..dfa8cc7) + 1.x fixes + 2.1–2.5 (3cbca8a..94626ef, verifying). Tagged so far: 0.1–0.5, 0.7, 4.1–4.4, 5.1–5.4, 6.1-backend, 6.2, 6.3, 9.1-image

## Next actions
1. Review + verify Track A (0.6, 1.x), Track C (4.x), Track B (5.1); commit one checkpoint per subphase.
2. When footage/ lands: `make record` (0.6) via the Track A builder; re-check the tape phrasing on a real frame.
3. After 4.x passes: brief 5.8 (UI → live API) and Phase 2 (items, grounding, verification).

## In flight
| Task | Agent | Started (ET) | Paths | Notes |
|---|---|---|---|---|
| 3.1–3.3 eval harness (built on the synthetic walk until footage lands) | builder (eval) | 17:48 | eval/run_eval.py, eval/report.md, eval/README?, scripts/build_mock_api.py, tests/test_eval*.py, fixtures/eval/** | reads Item rows via runner.run_walk on a temp DB; never overwrites fixtures/mock_api without --out |
| 1.x fixes + 2.1–2.5 verify (worktree .verify/wt-phase2 @ 94626ef) | verifier | 17:50 | read-only | commits untagged pending PASS |
| Track A: 7.1–7.4 re-walk loop (+ make record / full runs when footage lands) | builder (Track A, same agent) | 17:50 | punchwalk/pipeline/**, punchwalk/render/**, scripts/record.py, scripts/synth_clip.py, fixtures/pipeline/**, tests/test_pipeline_* | briefed by message |
| CHANGE-2 (small contract: channel_reason, approve-and-send doc, StageState.message, Settings AUTO_SEND/TRUST_PROXY/PUNCHWALK_WORKER) + 8.1 seed (on synthetic data, ready for real walks) | builder (Track C, same agent) | 17:53 | punchwalk/core/models.py, punchwalk/api/schemas.py, web/src/api/types.ts, scripts/gen_mock_api.py, fixtures/mock_api, punchwalk/config.py, .env.example, punchwalk/api/media.py, scripts/seed.py, tests/test_seed.py | briefed by message |
| 5.5–5.7 walks + walk detail, record, how processed | ui-builder (same agent) | 17:38 | web/** except types.ts | briefed by message |

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
- Keys are in `.env` (confirmed by name, 16:15 ET); both work (probe 9/9 ok).
- **Optional check with the organizers:** the hackathon Baseten slug `meta-models/muse-spark-open-1026` returns 404 ("please check the model you provided"); the catalog slug `meta-models/muse-spark-open` answers. The client tries -1026 first and falls back automatically, logging which slug answered. If the organizers say -1026 should be live, nothing changes; if they say to use the catalog slug, set `SPARK_OPEN_MODEL=meta-models/muse-spark-open` in `.env`.
- Human decisions on record: **no git pushes from this build** (human pushes manually; checkpoints are committed + tagged locally). **Slack webhook**: human will decide later; dispatch uses the in-app outbox until `SLACK_WEBHOOK_URL` is set.
- Footage → `footage/` and labels → `eval/labels/` per FOOTAGE.md (expected ~17:30 ET).

Fly: `fly auth whoami` works (lucianob@consaitech.com), so deploy can run unattended.

## Health
- make test: Python 305 passed after CHANGE-1 (builder report, 17:28); web typecheck/test/build green at 5.1 (29 screens)
- make eval: not run yet (targets in PLAN.md 3.4)
- make screenshots: 30/30 written at 5.4 (14/14 strict for 5.2–5.4); 5.5–5.7 screens pending
- Live API spend: $0.22 of $25 (probe $0.046; synthetic record + Phase 2 fixtures $0.175)
- Deploy: image verified (1.08 GB; in-image tests 57 pass incl. PDF + HDR; smoke ok); fly.toml valid; NOT deployed — human runbook in Needs human / docs/DEPLOY.md
- Live URL: none

## Key facts learned
- **CHANGE REQUEST 17:01 ET (human): no blue tape.** The inspector points with an index finger (~2 s); SAM tracks the hand; fingertip = defect location. PLAN.md 0.6/1.2/1.3/1.5/1.6/2.x/3.2/4.3/5.5/7.x updated; contract renamed Mark→Point; evidence shows a red defect outline + a small blue dot; item numbers keep the tape-tab look.
- SAM masks are box-local rasters (H×W stretches over [x1,x2+1)×[y1,y2+1)); helper `punchwalk/render/masks.py: mask_to_frame_svg()`; scale by frame/media size (verified on probe frame).
- SAM image input works as a data URL; strict `json_schema` works on Baseten Spark Open (always send `reasoning_effort`; default is xhigh); ASR turnId is zero-based.
- Hand concept (synthetic frames): "index finger" 2.64 ≈ "hand" 2.59 ≫ "pointing hand" 0 → default `SAM_HAND_CONCEPT=hand`; re-rank on real footage in `make record`. (Tape phrasing result superseded.)
- Cache: reads data/cache then fixtures/cache; `make probe` replays at $0 in readonly mode. Ledger: data/cost_ledger.jsonl.
- Tools present: uv 0.12, Python 3.12 system (project pins 3.11 via uv), node 24 + pnpm 9.15, ffmpeg 9, docker 29, flyctl (logged in).
- .env was an unmodified copy of .env.example at session start (all keys empty).
- Agreed Make entry points (used by later briefs): dev=`uvicorn punchwalk.api.app:app`, probe=`scripts/probe.py`, record=`scripts/record.py`, seed=`scripts/seed.py`, eval=`eval/run_eval.py`, build=`pnpm -C web build` → `punchwalk/static/`, screenshots=`pnpm -C web screenshots` → `.verify/screens/`.

## Final handoff
- not written yet
