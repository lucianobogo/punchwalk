# BACKLOG

Ideas and cut items. Nothing here gets built before every PLAN.md box is done.

- Live hands-free capture with Meta's Wearables Device Access Toolkit (native iOS app; glasses stream straight in; spoken confirmations; look at a tape and hear its item)
- Procore and Fieldwire sync
- Spec citations from the project spec book (Muse Spark 1.3, 1M context)
- Offline mode for sites without signal
- 24/25 fps clips lose up to 0.5 s at the end of each clip (frame_step trimming). Not an issue for iPhone/glasses (30/60 fps). Optional fix: resample to 30 fps instead of trimming.
- LLM cost-cap estimate counts base64 image chars as tokens (~100× overestimate per image; safe but blocks early near the $25 cap). Fix: fixed ~1500-token budget per image part (punchwalk/clients/llm.py:349).
- Contract gaps noted by Track C (fold into the next dedicated contract task): rewalk-link response schema (API returns WalkOut); Closure.rewalk_point_id (re-walk point ↔ item link; API currently matches via closure.rewalk_turn_id / after_t ±3 s); StageState.message; ModelCall.started_at; PUNCHWALK_WORKER in Settings/.env.example; STAGES_INITIAL/REWALK order vs. runner order; DELETE project / undismiss routes.
- 8.1 seed must create Job rows for seeded walks (else /progress is 404). Runner's walk.error text is technical; map to a friendly sentence for the UI.
