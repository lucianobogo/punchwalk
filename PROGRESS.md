# PROGRESS (append-only)

One line per event, newest at the bottom:
`YYYY-MM-DD HH:MM ET | task | agent | result | evidence | next`

2026-11-03 --:-- ET | setup | human | starter files added | - | orchestrator starts Phase 0
2026-11-03 16:05 ET | session start | orchestrator | repo at starter commit; .env keys EMPTY; git push blocked (ssh publickey); fly logged in | STATE.md | brief Phase 0
2026-11-03 16:12 ET | 0.1+0.2+0.3+0.4 | builder A | briefed (scaffold, clients, probe, mask helper); probe will block on keys | STATE.md in-flight | verify on report
2026-11-03 16:12 ET | 0.7 | builder B | briefed (models, schemas, types.ts, mock_api) | STATE.md in-flight | verify on report
2026-11-03 16:12 ET | 0.5 | builder C | briefed (ingest + synthetic clips) | STATE.md in-flight | verify on report
2026-11-03 16:15 ET | keys | human | MODEL_API_KEY + BASETEN_API_KEY now in .env; no pushes (human pushes manually); Slack decision deferred | STATE.md | spawn Phase 0 workers
2026-11-03 16:30 ET | 0.5 | builder C | done: ingest + synth_clip, 29 tests pass | report | verifier
2026-11-03 16:33 ET | 0.5 | verifier | FAIL: landscape-first walk pillarboxes portrait clips (ingest.py:305); all else pass (29 tests, HDR, idempotent) | .verify/mix/lp.jpg | builder C fix round 1
2026-11-03 16:35 ET | 0.5 | builder C | fix round 1 done: portrait-if-any canvas, exact SAM-frame boundaries, INGEST_VERSION=2; 37 tests | report | re-verify
2026-11-03 16:36 ET | 0.1-0.4 | builder A | done: scaffold, 7 client modules, probe 9/9 live calls ok ($0.046), mask check PNG, 53 tests; -1026 slug 404s (catalog slug works, auto-fallback) | fixtures/probe/, report | verifier
2026-11-03 16:37 ET | 0.5 | verifier | PASS after fix round 1 (37 ingest tests; portrait canvas; 90 total tests) | .verify/mix/lp2_all.jpg | commit ckpt-0.5
2026-11-03 16:41 ET | 0.1-0.4 | verifier | FAIL: strict_schema drops 'title' fields (llm.py:80); probe replay overwrote summary.json; .env.example missing 6 vars; all else pass (90 tests, mask IoU 0.92) | report | builder A fix round 1
2026-11-03 16:42 ET | 5.1 | ui-builder | briefed early (design system, shell, screenshot harness); types.ts/mocks wired when 0.7 lands | STATE.md in-flight | verify on report
2026-11-03 16:44 ET | 0.7 | builder B | done: 8 SQLModel tables, 55 schemas w/ routes, types.ts mirror, 118 mock JSON + 74 media (4.6 MB), 130 tests; make test 221 pass | report | verifier
2026-11-03 16:45 ET | 0.1-0.4 | builder A | fix round 1 done: strict_schema keeps title fields, probe replay no longer touches fixtures, .env.example complete, cap estimate; 221 tests | report | re-verify
2026-11-03 16:48 ET | 0.1-0.4 | verifier | PASS after fix round 1 (221 tests; probe replay $0; mask IoU 0.92) | report | commit ckpt-0.1..0.4
2026-11-03 16:49 ET | 0.7 | verifier | PASS (130 contract tests; deterministic mocks; media all resolve; 221 py tests) | report | commit ckpt-0.7; start Tracks A and C
