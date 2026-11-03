# DECISIONS

One line per decision: `date | decision | why | how to reverse`

2026-11-03 | Punchwalk (construction punch walks) is the hackathon project | best mix of impact, demo and odds of working in 30 hours | n/a
2026-11-03 | All LLM reasoning runs on Muse Spark Open via Baseten, hosted Muse Spark 1.3 only as a logged fallback | targets the Best Build on Meta Open Weight Models prize | set REASONING_PROVIDER=meta
2026-11-03 | Phone-first design (iPhone 15 Pro viewport), PWA installed to the Home Screen for the demo | project managers review punch lists on their phones; glasses footage is portrait | DESIGN.md
2026-11-03 | Orchestrator on Claude Fable 5.1, workers on Claude Opus 5.5 | lead plans and verifies, workers implement | edit model lines in .claude/agents/*.md
2026-11-03 | Design review (canvas "Punchwalk iPhone screens"): compact punch-list rows instead of big cards; three UI statuses (Open, Needs a look, Signed off); graphite numbers on light tape-blue tabs; one status-dependent primary action on item detail; new-item card over the walk video; labeled Record tab | big cards showed one item per screen, "Sent" vs "Open" confused, white-on-blue tab numbers failed contrast | DESIGN.md and docs/design/
