# Label DRAFTS: correct me, then copy to eval/labels/

These files were written by `eval/draft_labels.py` from the pipeline's own output. **They are not ground
truth.** Scoring the pipeline against them would only measure the pipeline against itself.

To turn a draft into labels (columns in eval/README.md, "Labels"):

1. Watch the walk and fix every row: the room, a plain description of the defect, the trade, `time_mmss`
   (when you start describing it, within its clip part), `pointed` and `clip`.
2. Delete rows for things that aren't defects or that repeat another row (the pipeline can write one defect
   twice), and add rows for defects it missed (next free `item_no`). Rooms it couldn't tell are blank.
3. Re-walk drafts (walk2): fill `time_mmss`/`clip`/`pointed` for the re-walk, and `fixed_in_rewalk` (the truth)
   and `claimed_fixed_in_rewalk` (what you said) for every row. They are blank on purpose.
4. Copy the corrected file to `eval/labels/<walk>.csv`, then `make eval`.

Drafts in this folder: walk1, walk3.
