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
2026-11-03 16:50 ET | 0.6+1.1-1.7 | builder (Track A) | briefed | STATE.md in-flight | verify per subphase
2026-11-03 16:50 ET | 4.1-4.4 | builder (Track C) | briefed | STATE.md in-flight | verify per subphase
2026-11-03 17:01 ET | CHANGE | human | tape → pointing hand; PLAN.md + DECISIONS.md updated; Tracks A/B/C re-briefed by message | DECISIONS.md | brief contract+docs builder
2026-11-03 17:12 ET | 5.1 | ui-builder | done: shell, tokens, 22 components, router, data layer, PWA, 29-screen harness; web typecheck/test/build green; committed untagged pending verification (make test red only until mocks regenerate for Point) | report | verify after CHANGE-1; ui-builder continues with 5.2–5.4
2026-11-03 17:23 ET | 4.1-4.4 | builder (Track C) | done: db+WAL, range media, SPA mount, all routes, streamed upload, 2-thread job worker, overlay (hands), guards; 44 API tests; smoke test ok | report | verifier
2026-11-03 17:25 ET | 4.1-4.4 | orchestrator | committed as 4 untagged checkpoints pending verification (so 6.x edits stay separate) | git log | verifier running; Track C continues with 6.1–6.3 backend
2026-11-03 17:27 ET | CHANGE-1 | builder | done: docs + contract (Mark→Point, Evidence.pointer, Grounding×4, stages) + mocks regenerated (208 files); 305 py tests pass | report | commit (untagged) + verify with 5.1
2026-11-03 17:27 ET | 1.1-1.7 (+0.6 script) | builder (Track A) | partial: pipeline built to the pointing spec, 36 new tests, synth-walk integration fixture ($0.097 spent, total $0.143); 0.6 blocked on footage | report | commit per subphase (untagged) + verify; Track A continues with Phase 2
2026-11-03 17:30 ET | 4.1-4.4 | verifier | PASS (44 API tests; 305 py total; range/traversal/limits/SSE/worker cap 2 all checked); 5 low findings forwarded to Track C | report | tags ckpt-4.1..4.4
2026-11-03 17:37 ET | 1.1-1.7 | verifier | FAIL: voice-only evidence frame window crosses clip boundaries (evidence.py:231); 7 smaller fixes; all else pass (73 tests) | report | Track A folds fixes into Phase 2 (fix round 1)
2026-11-03 17:38 ET | 5.2-5.4 | ui-builder | done: punch list, item detail, play sheet; 61 web tests; 30 screens, 01–13 strict; committed untagged | report | verify 5.1–5.4 in worktree .verify/wt-ui; ui-builder continues with 5.5–5.7
2026-11-03 17:41 ET | 6.1-6.3 + 4.x fixes | builder (Track C) | done: Slack/Resend/Outbox dispatch, approve-and-send, auto-send policy, PDF (503 locally: no Pango), 4.x fixes; 67 tests | report | commit (untagged) + verify; Track C → 9.1 Docker+deploy
2026-11-03 17:42 ET | 9.1 | orchestrator | deploy instruction denied by the auto-mode classifier; re-briefed Track C for a deploy-ready image + runbook; deploy listed under Needs human | STATE.md | human runs the runbook or grants deploy permission
2026-11-03 17:42 ET | CHANGE-1 | verifier | FAIL only on mock video determinism (-threads 4); contract/docs/mocks otherwise pass | report | builder fix round 1
2026-11-03 17:46 ET | 5.1-5.4 | verifier | PASS (61 web tests; 14/14 strict screens; AA contrast; no console errors); 5 minor fixes forwarded to ui-builder; Docker must build with VITE_DATA=live | .verify screenshots | tags ckpt-5.1..5.4
2026-11-03 17:47 ET | CHANGE-1 | builder+verifier | fix round 1 done (-threads 1; 3 identical runs; 134 contract tests, 61 web tests) → treated as PASS | commit | done
2026-11-03 17:48 ET | 3.1-3.3 | builder (eval) | briefed early on the synthetic walk (matching, metrics, report, build_mock_api) | STATE.md | verify on report; 3.4 after real footage
2026-11-03 17:50 ET | 2.1-2.5 + 1.x fixes | builder (Track A) | done: write_items, ground (sam_box on synth), verify (frame_shows_defect), voice_only mode, runner polish, item protection; 54 pipeline tests; 346 py tests; spend $0.221 | report | commit per subphase (untagged); verify in worktree; Track A → Phase 7
2026-11-03 17:53 ET | 9.1 | builder (Track C) | done (deploy-ready): image 1.08 GB, in-image tests 57 pass, container smoke ok, fly.toml valid, DEPLOY.md runbook; no Fly infra commands run | report | verify; human runs the deploy
2026-11-03 17:54 ET | 6.1-6.3 + 4.x fixes | verifier | PASS for 6.1 backend/6.2/6.3 (67 tests; Slack payload delivered; refusals; PDF HTML render); FAIL only on 4a: uvicorn --proxy-headers lets X-Forwarded-For spoof the demo limit locally (fix: --no-proxy-headers) + 4 copy/config nits → Track C | report | tags ckpt-6.1-backend, ckpt-6.2; 6.3 tag after the in-image PDF check
2026-11-03 18:01 ET | 9.1 (image) + 6.3 | verifier | PASS: in-image tests 57 pass incl. PDF + HDR; container smoke ok; fly.toml valid; DEPLOY.md accurate (2 stale lines once /mock-api 404 lands); 9.1 marked [~] pending the human's deploy; 6.3 → [x] | report | tags ckpt-6.3, ckpt-9.1-image
2026-11-03 18:02 ET | 5.5-5.7 + 5.x fixes | ui-builder | done: walks/walk detail (hero), record flow, how processed; 81 web tests; 31 screens, 16/16 strict; committed untagged (types.ts pending CHANGE-2 commit) | report | verify in a worktree after CHANGE-2 lands; ui-builder → 6.1 UI + 7.4 UI + 8.2
2026-11-03 18:04 ET | 1.1-1.7 + 2.1-2.4 | verifier | PASS (worktree 94626ef; 54 pipeline tests; outlines/dots/crops checked); 2.5 FAIL: forced re-run strips outlines from protected items + 9 lows → Track A | report | tags ckpt-1.1..1.7, ckpt-2.1..2.4
2026-11-03 18:04 ET | footage | human | footage/ landed 18:01 (walk1 5 parts ≈103 s, walk2 5 parts ≈77 s, walk3 15 s; HEVC 120 fps, varying sizes); eval/labels/*.csv are identical template copies (NOT real labels) | ls footage, md5 labels | Track A: make record + full runs; Needs human: real labels
