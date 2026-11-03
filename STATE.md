# STATE

Status: RUNNING
Updated: 2026-11-03 17:25 ET
Current phase: 1 (A) / 4 (C) / 5 (B) in parallel; 0.6 pending footage — targets Tue 8:30 PM / 10:00 PM / Wed 3:00 AM
Last checkpoint: ckpt-0.7 (Phase 0 done except 0.6, which waits for footage)

## Next actions
1. Review + verify Track A (0.6, 1.x), Track C (4.x), Track B (5.1); commit one checkpoint per subphase.
2. When footage/ lands: `make record` (0.6) via the Track A builder; re-check the tape phrasing on a real frame.
3. After 4.x passes: brief 5.8 (UI → live API) and Phase 2 (items, grounding, verification).

## In flight
| Task | Agent | Started (ET) | Paths | Notes |
|---|---|---|---|---|
| CHANGE: docs + contract tape→pointing (Mark→Point, Evidence.pointer, Grounding, stages, mocks) | builder | 17:01 | CLAUDE.md, DESIGN.md, FOOTAGE.md, eval/labels_template.csv, punchwalk/core/**, punchwalk/api/schemas.py, web/src/api/types.ts, fixtures/mock_api/**, scripts/gen_mock_api.py, tests/test_contracts.py, punchwalk/config.py, .env.example | Tracks A/B/C were told the new names; they finish contract-dependent parts last |
| Track A: 0.6 + 1.1–1.7 perception & fusion | builder | 16:50 | punchwalk/pipeline/** (not ingest.py/media.py unless needed), punchwalk/vocab.py, scripts/record.py, fixtures/pipeline/**, fixtures/cache/** (promotions), tests/test_pipeline_*.py | re-briefed at 17:01: hands/pointing instead of tape; make record runs live only once footage lands |
| Track C: 4.1–4.4 API & jobs | verifier | 16:50 (builder done 17:20) | punchwalk/api/** (not schemas.py), punchwalk/db.py, tests/test_api_*.py | committed untagged (4 commits) pending verification; verifier running |
| 6.1 backend dispatch (Slack/email/outbox) + 6.2 auto-send policy + 6.3 PDF (nice) | builder (Track C, same agent) | 17:25 | punchwalk/dispatch/**, punchwalk/report/**, punchwalk/api/routes/trades.py + dispatch bits, tests/test_dispatch_*.py | briefed by message; fixes for 4.x go to the same agent |
| 5.1 verify (after CHANGE-1 lands) + 5.2–5.4 punch list, item detail, play sheet | ui-builder (same agent) | 17:12 | web/** except web/src/api/types.ts | 5.1 committed untagged (pending verification); 5.2–5.4 briefed by message |

## Needs human
- none blocking. Keys are in `.env` (confirmed by name, 16:15 ET); both work (probe 9/9 ok).
- **Optional check with the organizers:** the hackathon Baseten slug `meta-models/muse-spark-open-1026` returns 404 ("please check the model you provided"); the catalog slug `meta-models/muse-spark-open` answers. The client tries -1026 first and falls back automatically, logging which slug answered. If the organizers say -1026 should be live, nothing changes; if they say to use the catalog slug, set `SPARK_OPEN_MODEL=meta-models/muse-spark-open` in `.env`.
- Human decisions on record: **no git pushes from this build** (human pushes manually; checkpoints are committed + tagged locally). **Slack webhook**: human will decide later; dispatch uses the in-app outbox until `SLACK_WEBHOOK_URL` is set.
- Footage → `footage/` and labels → `eval/labels/` per FOOTAGE.md (expected ~17:30 ET).

Fly: `fly auth whoami` works (lucianob@consaitech.com), so deploy can run unattended.

## Health
- make test: 221 passed (verifier, Phase 0; offline, sockets blocked)
- make eval: not run yet (targets in PLAN.md 3.4)
- make screenshots: not run yet
- Live API spend: $0.05 of $25 (probe, 11 ledger lines)
- Deploy: not deployed
- Live URL: none

## Key facts learned
- **CHANGE REQUEST 17:01 ET (human): no blue tape.** The inspector points with an index finger (~2 s); SAM tracks the hand; fingertip = defect location. PLAN.md 0.6/1.2/1.3/1.5/1.6/2.x/3.2/4.3/5.5/7.x updated; contract renamed Mark→Point; evidence shows a red defect outline + a small blue dot; item numbers keep the tape-tab look.
- SAM masks are box-local rasters (H×W stretches over [x1,x2+1)×[y1,y2+1)); helper `punchwalk/render/masks.py: mask_to_frame_svg()`; scale by frame/media size (verified on probe frame).
- SAM image input works as a data URL; strict `json_schema` works on Baseten Spark Open (always send `reasoning_effort`; default is xhigh); ASR turnId is zero-based.
- Best tape concept (provisional, synthetic frame): "strip of blue painter's tape" 0.953 > "blue painter's tape" 0.910 > "blue tape" 0.710 → `DEFAULT_TAPE_CONCEPT`. Re-check on real footage in 0.6.
- Cache: reads data/cache then fixtures/cache; `make probe` replays at $0 in readonly mode. Ledger: data/cost_ledger.jsonl.
- Tools present: uv 0.12, Python 3.12 system (project pins 3.11 via uv), node 24 + pnpm 9.15, ffmpeg 9, docker 29, flyctl (logged in).
- .env was an unmodified copy of .env.example at session start (all keys empty).
- Agreed Make entry points (used by later briefs): dev=`uvicorn punchwalk.api.app:app`, probe=`scripts/probe.py`, record=`scripts/record.py`, seed=`scripts/seed.py`, eval=`eval/run_eval.py`, build=`pnpm -C web build` → `punchwalk/static/`, screenshots=`pnpm -C web screenshots` → `.verify/screens/`.

## Final handoff
- not written yet
