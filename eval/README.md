# eval/

Scores the pipeline against the human labels (PLAN Phase 3).

```
make eval                                   # every walk in eval/labels/*.csv, from the cache (readonly)
uv run python eval/run_eval.py --walks walk1 walk2 --data-dir .verify/eval   # subset, keep the DATA_DIR
make eval-live                              # same, cache misses go live (counts toward the $25 cap)
uv run python eval/run_eval.py --labels fixtures/eval/labels_synth --data-dir .verify/eval-synth  # synthetic
```

- **Labels**: `eval/labels/walkN.csv`, format in FOOTAGE.md and `labels_template.csv`. One walk per file, and the
  `walk` column must equal the file name. A file that is still the template (same rows) or that names another
  walk is refused ("is still the template — fill it in per FOOTAGE.md"), and then nothing in the folder is scored.
  A walk whose rows fill `fixed_in_rewalk` / `claimed_fixed_in_rewalk` is a re-walk of the first walk before it.
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
  `resolve_closures` stage is `done`. Targets are PLAN 3.4. walk3 (`--holdout`) is reported separately and never tuned on.
  The re-walk closure numbers are measured on walk2, which was used for tuning (in-sample); walk3 is the only
  out-of-sample walk and has no re-walk.
- **Output**: the summary table on stdout, `report.md` (summary, per-item diffs, closures, timing and cost) and
  `report.json` (the same numbers + `app_summary`, the "How this walk was processed" eval block). Exit code is 0
  unless `--strict` is given and a non-holdout target fails or a walk errors.

`scripts/build_mock_api.py --data-dir DIR --out DIR` turns an eval DATA_DIR into mock API fixtures (see its docstring).
