# BACKLOG

Ideas and cut items. Nothing here gets built before every PLAN.md box is done.

- Live hands-free capture with Meta's Wearables Device Access Toolkit (native iOS app; glasses stream straight in; spoken confirmations; look at a defect and hear its item)
- Procore and Fieldwire sync
- Spec citations from the project spec book (Muse Spark 1.3, 1M context)
- Offline mode for sites without signal
- 24/25 fps clips lose up to 0.5 s at the end of each clip (frame_step trimming). Not an issue for iPhone/glasses (30/60 fps). Optional fix: resample to 30 fps instead of trimming.
- LLM cost-cap estimate counts base64 image chars as tokens (~100× overestimate per image; safe but blocks early near the $25 cap). Fix: fixed ~1500-token budget per image part (punchwalk/clients/llm.py:349).
- Contract gaps noted by Track C (fold into the next dedicated contract task): rewalk-link response schema (API returns WalkOut); Closure.rewalk_point_id (re-walk point ↔ item link; API currently matches via closure.rewalk_turn_id / after_t ±3 s); StageState.message; ModelCall.started_at; PUNCHWALK_WORKER in Settings/.env.example; STAGES_INITIAL/REWALK order vs. runner order; DELETE project / undismiss routes.
- 8.1 seed must create Job rows for seeded walks (else /progress is 404). Runner's walk.error text is technical; map to a friendly sentence for the UI.
- 8.1 seed: cache keys hash exact media bytes + ASR keyword list → seed must reuse DATA_DIR/record/<walk>/ media (not re-ingest) and projects with keywords=[] (or the same --keywords as record.py); Docker ffmpeg differs → bake the recorded media.
- CHANGE-2 contract task (collect): DispatchPreviewOut.channel_reason; document POST /api/items/{id}/approve-and-send (→ DispatchOut) in schemas.py + a web client fn; DispatchCreate channel/item_ids required (UI sends both); AUTO_SEND / TRUST_PROXY / PUNCHWALK_WORKER in Settings + .env.example; Closure.rewalk_point_id; StageState.message; ModelCall.started_at; rewalk-link response = WalkOut.
- Auto-send to the Outbox marks items "dispatched" though nothing left the app; demo copy should read "ready to share" (UI wording).
- CHANGE-3 candidates (UI-reported contract gaps): overlay events for voice-only items (UI derives from quote_start_s); upload limits (10 min / 400 MB) exposed via healthz; ProcessingStepOut.started_at for a true timeline; ModelCallOut item number; a re-walk upload response in the mocks; mock upload always returns wlk_3a_first.
- DESIGN.md example copy for a > 10-min clip says "Split it into parts and upload them together", but the 10-min limit is on the walk total; UI copy now says to record it as separate walks (clips.ts). Update DESIGN.md's example text in a docs pass.
- CHANGE-3 candidates (from 6.1 UI): TradeSummaryOut.channel (the list derives it from the contact and can't see the global Slack webhook); ShareItemOut.item_id ("Open in Punchwalk" → the item); preview filtered by item_ids (heading count is rewritten client-side); RewalkItemOut dispatch info ("Sent again" comes from the item list).
- Deploy must allow same-origin framing (landing iframe of `/`): no X-Frame-Options: DENY / frame-ancestors 'none'.
- Pipeline: remove the stages/resend.json marker guard around resend_open_items now that the policy is idempotent per closure (Track A, low).
- Dark mode (PLAN 8.4 *(nice)*) skipped: tokens exist behind data-theme="dark" but light tints, Hold/Safety text and white-on-Tape-ink buttons need dark variants (> 30 min).
- Safari/WebKit plain Tab skips links unless "Press Tab to highlight each item" is on (platform behavior); Option-Tab works.
- API: StageState.message carries raw exception text on failure (UI filters it); Track A to map it to plain words (queued).
- UI low: on a phone /landing shows the demo banner even on an empty server (isDemoSession true); only show it when a project is loaded. Optional: a minimal service worker so a hard reload while offline shows the app shell.
- Walks processed before the overlay fingertip clamp keep an unclamped cached overlay.json until re-run (seed re-runs walks, so the demo data is fine).
- Seeded processing screen shows total_cost_usd $0 (all cache hits); consider showing the estimated live cost of the recorded calls instead (ModelCall est_cost from the ledger) — judges should see what a walk costs.
- walk3 pick_evidence: context_b frame at 15.77 s fails near the clip end (ffmpeg exit 234; non-fatal warning) — retry inside the clip.
- Re-walk rule: voice "not fixed" wins over vision "fixed" (#010 stays open) — correct per PLAN 7.3; mention in the demo story.
- Seed replay stores est_cost 0 / latency 0 for cached calls, so the demo's processing screen says 'from recorded model answers (no new cost)' instead of the real ≈ $0.4 per walk; keeping the recording's original cost/latency in the ModelCall rows would show real numbers.
