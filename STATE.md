# STATE

Status: RUNNING
Updated: 2026-11-03 17:47 ET
Current phase: 1 (A) / 4 (C) / 5 (B) in parallel; 0.6 pending footage — targets Tue 8:30 PM / 10:00 PM / Wed 3:00 AM
Last checkpoint: ckpt-5.4 (tagged; 0.1–0.5, 0.7, 4.1–4.4, 5.1–5.4 verified). Untagged commits pending verification: CHANGE-1 (0f06a60, fix round 1), 0.6 script + 1.1–1.7 (0228dab..dfa8cc7, fix round 1 in Phase 2), 4.x fixes + 6.1–6.3 (4b1b7be..2a9fe64)

## Next actions
1. Review + verify Track A (0.6, 1.x), Track C (4.x), Track B (5.1); commit one checkpoint per subphase.
2. When footage/ lands: `make record` (0.6) via the Track A builder; re-check the tape phrasing on a real frame.
3. After 4.x passes: brief 5.8 (UI → live API) and Phase 2 (items, grounding, verification).

## In flight
| Task | Agent | Started (ET) | Paths | Notes |
|---|---|---|---|---|
| CHANGE-1 docs + contract + mocks (tape→pointing) | builder (fix round 1) | 17:00 | scripts/gen_mock_api.py, fixtures/mock_api | verifier FAIL: video determinism (-threads 1 fix) |
| 1.1–1.7 fix round 1 (folded into Phase 2) | builder (Track A) | 17:37 | pipeline | verifier FAIL: voice-only frame window; fixes sent |
| Track A: 2.1–2.5 items, grounding, verification (+ make record when footage lands) | builder (Track A, same agent) | 17:30 | punchwalk/pipeline/**, punchwalk/render/**, scripts/record.py, scripts/probe.py (tape→hand fix), fixtures/pipeline/**, fixtures/cache/**, tests/test_pipeline_* | briefed by message |
| 6.1–6.3 backend + 4.x fixes verify | verifier | 17:41 | read-only | committed untagged pending PASS |
| 9.1 Docker + fly.toml + DEPLOY.md (deploy-ready only; Fly commands reserved for the human) | builder (Track C, same agent) | 17:41 | Dockerfile, .dockerignore, fly.toml, scripts/entrypoint*, docs/DEPLOY.md | briefed by message; seed (8.1) comes later |
| 5.5–5.7 walks + walk detail, record, how processed | ui-builder (same agent) | 17:38 | web/** except types.ts | briefed by message |

## Needs human
- **Deploy needs you (blocked by the permission classifier, 17:45 ET):** the orchestrator was denied instructing a production deploy ("Production Deploy"). Track C is building the Docker image + fly.toml + `docs/DEPLOY.md` (a copy-paste runbook). When it lands: either run the runbook yourself (`fly apps create punchwalk`, `fly volumes create …`, import secrets, `make deploy`), or restart the orchestrator with permission to run Fly deploys. Without it the app stays deploy-ready but not live.
- Keys are in `.env` (confirmed by name, 16:15 ET); both work (probe 9/9 ok).
- **Optional check with the organizers:** the hackathon Baseten slug `meta-models/muse-spark-open-1026` returns 404 ("please check the model you provided"); the catalog slug `meta-models/muse-spark-open` answers. The client tries -1026 first and falls back automatically, logging which slug answered. If the organizers say -1026 should be live, nothing changes; if they say to use the catalog slug, set `SPARK_OPEN_MODEL=meta-models/muse-spark-open` in `.env`.
- Human decisions on record: **no git pushes from this build** (human pushes manually; checkpoints are committed + tagged locally). **Slack webhook**: human will decide later; dispatch uses the in-app outbox until `SLACK_WEBHOOK_URL` is set.
- Footage → `footage/` and labels → `eval/labels/` per FOOTAGE.md (expected ~17:30 ET).

Fly: `fly auth whoami` works (lucianob@consaitech.com), so deploy can run unattended.

## Health
- make test: Python 305 passed after CHANGE-1 (builder report, 17:28); web typecheck/test/build green at 5.1 (29 screens)
- make eval: not run yet (targets in PLAN.md 3.4)
- make screenshots: 30/30 written at 5.4 (14/14 strict for 5.2–5.4); 5.5–5.7 screens pending
- Live API spend: $0.14 of $25 (probe $0.046 + synthetic record/phrasing $0.097)
- Deploy: not deployed
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
