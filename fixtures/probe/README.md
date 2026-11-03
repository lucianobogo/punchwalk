# fixtures/probe

Output of `make probe` (scripts/probe.py): one live call per API on a small clip, used to
confirm request/response shapes. Each `*.json`/`*.txt` is a saved response. A failed call is logged
in `ERRORS.md` (absent = none).

- `summary.json`: built from the cost ledger's **live** probe rows (`stage == "probe"`), so it holds the
  real latencies and costs and a cached rerun never zeroes it. Rebuild it without API calls with
  `uv run python scripts/probe.py --rebuild-summary`.
- Outputs land here only with `PUNCHWALK_CACHE=readwrite` (what `make probe` sets). `readonly`/`off`
  replays write to `.verify/probe/` and leave this folder untouched. Every run writes its own timings to
  `.verify/probe/last_run.json`.
- `media/`: the synthetic 3 s portrait clip (720x1280, H.264, faststart), a frame, and a 5 s WAV
  (narration by macOS `say -v Samantha`). Our own synthetic asset.
- `tape_concept.json`: tape phrasing comparison on the frame (best is the default SAM_TAPE_CONCEPT;
  provisional until checked on real footage).
- `mask_check.png`: the SAM mask (blue fill and outline) and box (yellow) over the frame. The outline sits
  on the tape. `mask_check_large.png` shows the same on a large object (cabinet door, red), and
  `mask_check_video.png` on frame 45 of the video result.
- Mapping finding: masks are **box-local**. The H x W raster covers the box [x1, x2] x [y1, y2]
  (inclusive API coordinates; W = x2 - x1 + 1, H = y2 - y1 + 1). Observed scale 1.0 on both a 134x58 and a
  285x475 box. `punchwalk/render/masks.py` stretches the raster over the box (so a downscaled mask also
  maps correctly), then scales source px to the target frame.

## Findings worth keeping (Nov 3, 2026 live run)

- Baseten `meta-models/muse-spark-open-1026` returned `HTTP 404 {"error":"please check the model you provided"}`.
  The catalog slug `meta-models/muse-spark-open` answered, with strict `json_schema` accepted. `llm.py` tries
  the hackathon slug first, then `SPARK_OPEN_MODEL_ALTERNATES`, and remembers which slug worked.
- SAM accepted an `input_image` **data URL** (no Files upload needed). The video stream reports
  `usage.video_frames_processed`. Each `response.output_text.delta` is one frame line ending in `\n`.
- Live latencies: Files 2.3 s (127 KB), SAM image 1.4 to 2.9 s, SAM video 7.0 s (90 frames), ASR 3.6 s (5 s audio),
  Spark Open 4.2 s (1382 in / 236 out tokens), Spark 1.3 5.1 s. Total probe spend: $0.0464.
