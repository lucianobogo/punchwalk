# fixtures/pipeline

Pipeline fixtures (Phase 1, pointing revision).

## `<walk>/` (written by `make record`, one folder per walk in `footage/`)

- `transcript.json`: Muse Voice Transcribe turns (the `stages/transcribe.json` document)
- `tracks.json`: SAM 3.1 hand tracks (the `stages/track_hands.json` document: per-frame
  `{frame, object_id, box, confidence, svg}` in SAM-frame pixels)
- `meta.json`: the ingest `IngestMeta`
- `poster.jpg`: 720p poster
- `hand_concept.json` (first walk only): "hand" vs "index finger" vs "pointing hand" on 3 pointing frames

The cache entries behind them are promoted into `fixtures/cache/`. Replaying them needs the exact
`sam_part_<k>.mp4` / `audio.wav` bytes that were recorded (cache keys hash the media), which stay in
`DATA_DIR/record/<walk>/`.

## `synth_walk/` (integration test fixture, our own synthetic media)

A 21 s, three-part portrait walk made with `scripts/synth_clip.py`. In parts a and b a drawn hand
slides in from the right edge, points at a scuff for ~2 s and withdraws while the narration
(macOS `say -v Samantha`) describes it. Part c has narration but no hand, so it's a voice-only
mention.

- `media/`: the ingest outputs (display.mp4, sam.mp4, sam_part_0..2.mp4, audio.wav, meta.json)
- `cache/`: the live responses recorded for it on Nov 3, 2026 with `scripts/record.py` (Files uploads, SAM 3.1
  "hand" per part, Muse Voice Transcribe; $0.043). The parts' SAM output: 26 hand records each in a and b,
  none in c. Three ASR turns.

Re-create it (live, ~$0.05):

```
uv run python - <<'EOF'
import sys; sys.path.insert(0, "scripts")
from pathlib import Path
import synth_clip as s
d = Path(".verify/record_synth/footage")
s.make_clip(d/"walk1_a.mp4", 7.0, variant=0, point_at=0.8, text="Kitchen. Upper cabinet left of the sink, knob missing.")
s.make_clip(d/"walk1_b.mp4", 7.0, variant=1, point_at=0.8, text="Hallway. Switch plate is crooked, needs to be straightened.")
s.make_clip(d/"walk1_c.mp4", 7.0, variant=2, hand=False, text="Bedroom. Scuff marks on the wall by the door.")
EOF
V=.verify/record_synth
uv run python scripts/record.py --footage $V/footage --out $V/out --fixtures $V/fixtures --promote-to $V/cache
# then copy $V/out/walk1/{display.mp4,sam.mp4,sam_part_*.mp4,audio.wav,meta.json} -> synth_walk/media/
#      and $V/cache/* (without files_index.json) -> synth_walk/cache/
```
