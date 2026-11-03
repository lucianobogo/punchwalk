# DECISIONS

One line per decision: `date | decision | why | how to reverse`

2026-11-03 | Punchwalk (construction punch walks) is the hackathon project | best mix of impact, demo and odds of working in 30 hours | n/a
2026-11-03 | All LLM reasoning runs on Muse Spark Open via Baseten, hosted Muse Spark 1.3 only as a logged fallback | targets the Best Build on Meta Open Weight Models prize | set REASONING_PROVIDER=meta
2026-11-03 | Phone-first design (iPhone 15 Pro viewport), PWA installed to the Home Screen for the demo | project managers review punch lists on their phones; glasses footage is portrait | DESIGN.md
2026-11-03 | Orchestrator on Claude Fable 5.1, workers on Claude Opus 5.5 | lead plans and verifies, workers implement | edit model lines in .claude/agents/*.md
2026-11-03 | Design review (canvas "Punchwalk iPhone screens"): compact punch-list rows instead of big cards; three UI statuses (Open, Needs a look, Signed off); graphite numbers on light tape-blue tabs; one status-dependent primary action on item detail; new-item card over the walk video; labeled Record tab | big cards showed one item per screen, "Sent" vs "Open" confused, white-on-blue tab numbers failed contrast | DESIGN.md and docs/design/
2026-11-03 | Ingest canvas: portrait if any clip is portrait (sized from the first portrait clip), else the first clip's size; other clips fit+pad | glasses footage is the hero and must never be shrunk or pillarboxed | CANVAS_POLICY in punchwalk/pipeline/ingest.py (bump INGEST_VERSION when output geometry changes)
2026-11-03 | Each clip is trimmed to a whole multiple of frame_step(fps)=fps//gcd(fps,10) frames so part boundaries land exactly on SAM 10 fps frames; multi-clip walks also write sam_part_<k>.mp4 | exact sam_frame_offset per part for per-part SAM runs | _target_frames in ingest.py
2026-11-03 | HDR → SDR uses swscale color management first, then zscale+tonemap, then plain conversion with a warning | this ffmpeg has no zscale; hable tonemap came out far too dark | hdr_method order in ingest.py
