# eval/

Scores the pipeline against the human labels.

```
make eval                                   # every walk in eval/labels/*.csv, from the cache (readonly)
uv run python eval/run_eval.py --walks walk1 walk2 --data-dir .verify/eval   # subset, keep the DATA_DIR
make eval-live                              # same, cache misses go live (counts toward the $25 cap)
uv run python eval/run_eval.py --labels fixtures/eval/labels_synth --data-dir .verify/eval-synth  # synthetic
```

- **Labels**: `eval/labels/walkN.csv`, one row per defect, with the header
  `walk,item_no,room,description,trade,time_mmss,pointed,fixed_in_rewalk,claimed_fixed_in_rewalk,clip`
  (example: `labels_template.csv`):
  - `walk`: the walk name, equal to the file name (walk1.csv → walk1)
  - `item_no`: the defect's number; a re-walk reuses the first walk's numbers
  - `room`, `description`, `trade`: what the item should say (trades match case-insensitively, as an id like
    `carpentry_millwork` or plain words like "Carpentry & millwork"; a blank trade or room is left out of that metric)
  - `time_mmss`: when the inspector starts describing it, as MM:SS inside the clip part named by `clip`
  - `pointed`: 1 if the inspector pointed at it, 0 if it was only described
  - `fixed_in_rewalk`: re-walk rows only, 1/0, whether it really was fixed
  - `claimed_fixed_in_rewalk`: re-walk rows only, 1/0, whether the inspector said it was fixed
  - `clip`: the clip part letter (a, b, ...) in upload order

  A walk whose rows fill `fixed_in_rewalk` / `claimed_fixed_in_rewalk` is a re-walk of the first walk before it.
  A file that is still the template (same rows), names another walk, or doesn't parse isn't ground truth: that
  walk is reported and left out, and the other files are still scored.
- **Project rooms/keywords** are part of the cached prompts, so they must equal `make record --rooms/--keywords`.
  Order of precedence: `--rooms/--keywords`, `<labels>/project.json`, `eval/project.json`, then the label rooms in walk order.
- **Media**: `DATA_DIR/record/<walk>/` (from `make record`), else `fixtures/pipeline/<walk>/media/`;
  `fixtures/pipeline/<walk>/cache/` is added to the cache read path.
- **Pipeline**: `runner.run_walk` on a temp DATA_DIR (or `--data-dir`, resumed: done walks don't re-run), one
  project `prj_eval`, walks `wlk_eval_<walk>` with a `job_eval_<walk>` Job row, in natural order like `make record --full`.
- **Matching** (`matching.py`): t_pred = `Item.quote_start_s` (else `video_start_s + 2`); a candidate needs |dt| <= 6 s and
  (same room or token Jaccard >= 0.2 of title + description vs the label description); Hungarian on time closeness + overlap.
- **Metrics** (`metrics.py`): recall, precision, trade, room, grounding (pointed labels outlined by SAM), needs-review
  rate; re-walk closure accuracy and false-claim catch, which read "n/a (Phase 7 pending)" until the re-walk's
  `resolve_closures` stage is `done`. Targets: recall ≥ 0.9, precision ≥ 0.9, trade ≥ 0.85, room ≥ 0.9, closure ≥ 0.9, false-claim catch = 100%. walk3 (`--holdout`) is reported separately and never tuned on.
  The re-walk closure numbers are measured on walk2, which was used for tuning (in-sample); walk3 is the only
  out-of-sample walk and has no re-walk.
- **Output**: the summary table on stdout, `report.md` (summary, per-item diffs, closures, timing and cost) and
  `report.json` (the same numbers + `app_summary`, the "How this walk was processed" eval block). Exit code is 0
  unless `--strict` is given and a non-holdout target fails or a walk errors.

`scripts/build_mock_api.py --data-dir DIR --out DIR` turns an eval DATA_DIR into mock API fixtures (see its docstring).
