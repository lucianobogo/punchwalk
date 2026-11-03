# STATE

Status: RUNNING
Updated: 2026-11-03 17:30 ET
Current phase: 1 (A) / 4 (C) / 5 (B) in parallel; 0.6 pending footage — targets Tue 8:30 PM / 10:00 PM / Wed 3:00 AM
Last checkpoint: ckpt-4.4 (tagged; 0.1–0.5, 0.7, 4.1–4.4 verified). Untagged commits pending verification: 5.1 (89349ae), CHANGE-1 (0f06a60), 0.6 script + 1.1–1.7 (0228dab..dfa8cc7)

## Next actions
1. Review + verify Track A (0.6, 1.x), Track C (4.x), Track B (5.1); commit one checkpoint per subphase.
2. When footage/ lands: `make record` (0.6) via the Track A builder; re-check the tape phrasing on a real frame.
3. After 4.x passes: brief 5.8 (UI → live API) and Phase 2 (items, grounding, verification).

## In flight
| Task | Agent | Started (ET) | Paths | Notes |
|---|---|---|---|---|
| CHANGE-1 docs + contract + mocks (tape→pointing) | verifier | 17:00 (builder done 17:28) | committed 0f06a60 untagged | verifying (Python/docs/mocks); 5.1 UI verification deferred to the 5.2–5.4 verification |
| 1.1–1.7 verify (worktree .verify/wt-phase1 @ dfa8cc7) | verifier | 17:30 | read-only | commits 0.6 script + 1.1–1.7 untagged pending PASS |
| Track A: 2.1–2.5 items, grounding, verification (+ make record when footage lands) | builder (Track A, same agent) | 17:30 | punchwalk/pipeline/**, punchwalk/render/**, scripts/record.py, scripts/probe.py (tape→hand fix), fixtures/pipeline/**, fixtures/cache/**, tests/test_pipeline_* | briefed by message |
| 6.1 backend dispatch (Slack/email/outbox) + 6.2 auto-send policy + 6.3 PDF (nice) | builder (Track C, same agent) | 17:25 | punchwalk/dispatch/**, punchwalk/report/**, punchwalk/api/routes/trades.py + dispatch bits, tests/test_dispatch_*.py | briefed by message; fixes for 4.x go to the same agent |
| 5.1 verify (after CHANGE-1 lands) + 5.2–5.4 punch list, item detail, play sheet | ui-builder (same agent) | 17:12 | web/** except web/src/api/types.ts | 5.1 committed untagged (pending verification); 5.2–5.4 briefed by message |

## Needs human
- none blocking. Keys are in `.env` (confirmed by name, 16:15 ET); both work (probe 9/9 ok).
- **Optional check with the organizers:** the hackathon Baseten slug `meta-models/muse-spark-open-1026` returns 404 ("please check the model you provided"); the catalog slug `meta-models/muse-spark-open` answers. The client tries -1026 first and falls back automatically, logging which slug answered. If the organizers say -1026 should be live, nothing changes; if they say to use the catalog slug, set `SPARK_OPEN_MODEL=meta-models/muse-spark-open` in `.env`.
- Human decisions on record: **no git pushes from this build** (human pushes manually; checkpoints are committed + tagged locally). **Slack webhook**: human will decide later; dispatch uses the in-app outbox until `SLACK_WEBHOOK_URL` is set.
- Footage → `footage/` and labels → `eval/labels/` per FOOTAGE.md (expected ~17:30 ET).

Fly: `fly auth whoami` works (lucianob@consaitech.com), so deploy can run unattended.

## Health
- make test: Python 305 passed after CHANGE-1 (builder report, 17:28); web typecheck/test/build green at 5.1 (29 screens)
- make eval: not run yet (targets in PLAN.md 3.4)
- make screenshots: not run yet
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
