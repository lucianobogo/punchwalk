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
- `cache/`: every live response the full pipeline needs, recorded on Nov 3, 2026 with
  `scripts/record.py --full --rooms Kitchen Hallway Bedroom` (28 entries): Files uploads, SAM 3.1 "hand" per part
  (26 hand records each in a and b, none in c), Muse Voice Transcribe (3 turns), Muse Spark Open classify / write
  (3) / verify (3), SAM 3.1 image calls for the defect outlines, and the hand-phrasing comparison. Recording
  cost: $0.043 (Phase 1) + $0.078 (Phase 2, two passes) + $0.023 (phrasings).
- `hand_concept.json`: "index finger" 2.640 (mean conf 0.880 x 3 frames) > "hand" 2.585 (0.862 x 3) >
  "pointing hand" 0 (0 frames), on 3 pointing frames of this synthetic walk. The default stays "hand".

The integration test (tests/test_pipeline_integration.py) replays all of it readonly. Keys hash the exact
frame bytes, so a different ffmpeg / libjpeg build can make the image calls miss; re-record with the
commands below if that happens. The test project must keep rooms Kitchen, Hallway, Bedroom and no keywords.

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
uv run python scripts/record.py --footage $V/footage --out $V/out --fixtures $V/fixtures --promote-to $V/cache \
    --full --rooms Kitchen Hallway Bedroom
# then copy $V/out/walk1/{display.mp4,sam.mp4,sam_part_*.mp4,audio.wav,meta.json} -> synth_walk/media/
#      $V/cache/* (without files_index.json) -> synth_walk/cache/, $V/fixtures/walk1/hand_concept.json -> synth_walk/
```

## `synth_rewalk/` (re-walk integration fixture, tests/test_pipeline_rewalk.py)

The re-walk of `synth_walk` (`uv run python scripts/synth_clip.py --out <dir> --rewalk`): the same three
scenes, with the kitchen scuff gone ("Kitchen cabinet knob, fixed."), the hallway scuff still drawn but
called fixed ("Hallway switch plate, fixed.": the false claim), and the bedroom scuff still there
("Bedroom scuff marks, still there."). `media/` holds its ingest outputs; `cache/` holds only the entries
`synth_walk/cache` doesn't already have (Files, SAM hands, ASR, classify, 3 match + 3 visual-check calls).
Recorded with walk1 + walk2 in one footage folder:
`scripts/record.py --footage <dir> --full --rooms Kitchen Hallway Bedroom` (walk2 runs as the re-walk).
Expected: #1 signed off, #2 needs a look ("Marked fixed on the re-walk, but the defect is still visible."),
#3 still open and sent again.

Prompts carry no walk or item ids, so these replay for any walk ids; any change to the write_items, verify
or resolve_closures prompts, or to the renders the model sees, needs a re-record of both fixtures.
