# STATE

Status: RUNNING
Updated: 2026-11-03 16:49 ET
Current phase: 0 (Foundation, probes, contracts) — target Tue 5:30 PM
Last checkpoint: ckpt-0.7 (Phase 0 done except 0.6, which waits for footage)

## Next actions
1. Review reports from 0.1–0.4 (builder A), 0.7 (builder B), 0.5 (builder C); verify each; commit checkpoints.
2. When the human adds MODEL_API_KEY + BASETEN_API_KEY to .env: run `make probe` (0.3) and the mask check (0.4) live.
3. When footage/ and eval/labels/ land (~17:30 ET): 0.6 `make record`, then start Tracks A (Phase 1), B (Phase 5 on mocks), C (Phase 4).

## In flight
| Task | Agent | Started (ET) | Paths | Notes |
|---|---|---|---|---|
| 5.1 design system & shell | ui-builder | 16:42 | web/** except web/src/api/types.ts (owned by 0.7) | started early; data layer wired once 0.7 lands |

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
- SAM masks are box-local rasters (H×W stretches over [x1,x2+1)×[y1,y2+1)); helper `punchwalk/render/masks.py: mask_to_frame_svg()`; scale by frame/media size (verified on probe frame).
- SAM image input works as a data URL; strict `json_schema` works on Baseten Spark Open (always send `reasoning_effort`; default is xhigh); ASR turnId is zero-based.
- Best tape concept (provisional, synthetic frame): "strip of blue painter's tape" 0.953 > "blue painter's tape" 0.910 > "blue tape" 0.710 → `DEFAULT_TAPE_CONCEPT`. Re-check on real footage in 0.6.
- Cache: reads data/cache then fixtures/cache; `make probe` replays at $0 in readonly mode. Ledger: data/cost_ledger.jsonl.
- Tools present: uv 0.12, Python 3.12 system (project pins 3.11 via uv), node 24 + pnpm 9.15, ffmpeg 9, docker 29, flyctl (logged in).
- .env was an unmodified copy of .env.example at session start (all keys empty).
- Agreed Make entry points (used by later briefs): dev=`uvicorn punchwalk.api.app:app`, probe=`scripts/probe.py`, record=`scripts/record.py`, seed=`scripts/seed.py`, eval=`eval/run_eval.py`, build=`pnpm -C web build` → `punchwalk/static/`, screenshots=`pnpm -C web screenshots` → `.verify/screens/`.

## Final handoff
- not written yet
