#!/usr/bin/env python
"""Pre-filled label DRAFTS from the pipeline's own items, for the human to correct (never ground truth).

    uv run python eval/draft_labels.py --data-dir data --walks walk1 walk2 walk3
    uv run python eval/draft_labels.py --data-dir .verify/eval-synth --walks synth_walk --out .verify/drafts

Writes <out>/<walk>.csv (default eval/labels_draft/) in the exact eval/labels format, plus a README.md
that says these are drafts. It refuses to write into eval/labels/: a person copies a corrected draft there.

- Database: <data-dir>/record/record.sqlite3 (what `scripts/record.py --full` writes; walks
  `wlk_record_<walk>`), else <data-dir>/punchwalk.sqlite3 (an eval or app data dir; walks `wlk_eval_<walk>`
  or `wlk_record_<walk>`). `--db` and `--walk-id walk=ID` override. The database is opened read-only.
- First walks: one row per item, in item-number order: item_no = 1, 2, ... within the walk (item numbers are
  project-wide, so walk3's would otherwise start at 14), room, description = the
  item title, trade, time_mmss = the quote start **within its clip part** (nearest second; a quote starting
  < 0.5 s before a part boundary counts as that part's 00:00),
  pointed = 1 when the item has a point, fixed/claimed blank, clip = the part letter (blank for one-clip walks).
- Re-walks: one row per item of the walk it re-checks (the same item_no as that walk's draft), with room/description/trade copied,
  time_mmss + clip from the re-walk's closure moment when the pipeline found one (else blank, so the loader
  refuses the file until someone fills it), pointed blank, fixed_in_rewalk and claimed_fixed_in_rewalk blank:
  those are the truth the eval measures, so the pipeline never pre-fills them.
"""

from __future__ import annotations

import argparse
import csv
import io
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if sys.path and Path(sys.path[0]).resolve() == Path(__file__).resolve().parent:
    sys.path[0] = str(REPO_ROOT)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import json  # noqa: E402

from eval.labels import COLUMNS  # noqa: E402

DEFAULT_OUT = REPO_ROOT / "eval" / "labels_draft"
LABELS_DIR = REPO_ROOT / "eval" / "labels"
README = """# Label DRAFTS: correct me, then copy to eval/labels/

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

Drafts in this folder: {walks}.
"""


class DraftError(RuntimeError):
    pass


@dataclass
class Source:
    db: Path
    walk_ids: dict[str, str]


def find_db(data_dir: Path, db: Path | None) -> Path:
    for cand in ([db] if db else [data_dir / "record" / "record.sqlite3", data_dir / "punchwalk.sqlite3"]):
        if cand is not None and cand.is_file():
            return cand
    raise DraftError(f"no pipeline database in {data_dir} (looked for record/record.sqlite3 and punchwalk.sqlite3); "
                     "run `make record` with --full, or eval/run_eval.py --data-dir DIR")


def connect(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def resolve_walk(con: sqlite3.Connection, walk: str, override: str | None) -> sqlite3.Row:
    slug = "".join(ch if ch.isalnum() else "_" for ch in walk.lower())
    for wid in ([override] if override else [f"wlk_record_{slug}", f"wlk_eval_{slug}", walk]):
        row = con.execute("SELECT id, kind, rewalk_of, clips FROM walks WHERE id = ?", (wid,)).fetchone()
        if row is not None:
            return row
    known = ", ".join(r[0] for r in con.execute("SELECT id FROM walks ORDER BY id"))
    raise DraftError(f"no walk for {walk} (tried wlk_record_{slug}, wlk_eval_{slug}); walks here: {known or 'none'}")


def clip_offsets(walk: sqlite3.Row) -> list[float]:
    clips = sorted(json.loads(walk["clips"] or "[]"), key=lambda c: c["order"])
    return [float(c["offset_s"]) for c in clips] or [0.0]


BOUNDARY_SNAP_S = 0.5  # a quote starting this close before a part's start belongs to that part


def part_time(t: float, offsets: list[float]) -> tuple[str, str]:
    """(time_mmss within the part, rounded to the second; clip letter, or '' for a one-clip walk)."""
    idx = max((i for i, off in enumerate(offsets) if off <= t + BOUNDARY_SNAP_S), default=0)
    within = max(0.0, t - offsets[idx])
    m, s = divmod(int(round(within)), 60)
    return f"{m:02d}:{s:02d}", (chr(ord("a") + idx) if len(offsets) > 1 else "")


def walk_items(con: sqlite3.Connection, walk_id: str) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT number, room, title, trade, quote_start_s, video_start_s, point_id, closure FROM items "
        "WHERE origin_walk_id = ? AND dismissed_at IS NULL ORDER BY number", (walk_id,)
    ).fetchall()


def draft_initial(con: sqlite3.Connection, name: str, walk: sqlite3.Row) -> list[dict[str, Any]]:
    offsets = clip_offsets(walk)
    rows = []
    for no, it in enumerate(walk_items(con, walk["id"]), start=1):
        t = it["quote_start_s"] if it["quote_start_s"] is not None else (it["video_start_s"] or 0.0) + 2.0
        mmss, clip = part_time(float(t), offsets)
        rows.append({"walk": name, "item_no": no, "room": it["room"] or "", "description": it["title"],
                     "trade": it["trade"] or "", "time_mmss": mmss, "pointed": 1 if it["point_id"] else 0,
                     "fixed_in_rewalk": "", "claimed_fixed_in_rewalk": "", "clip": clip})
    return rows


def draft_rewalk(con: sqlite3.Connection, name: str, walk: sqlite3.Row) -> list[dict[str, Any]]:
    if not walk["rewalk_of"]:
        raise DraftError(f"{name} is a re-walk but doesn't say which walk it re-checks (rewalk_of is empty)")
    offsets = clip_offsets(walk)
    rows = []
    for no, it in enumerate(walk_items(con, walk["rewalk_of"]), start=1):
        closure = json.loads(it["closure"]) if it["closure"] else {}
        after_t = closure.get("after_t") if closure.get("rewalk_id") == walk["id"] else None
        mmss, clip = part_time(float(after_t), offsets) if after_t is not None else ("", "")
        rows.append({"walk": name, "item_no": no, "room": it["room"] or "", "description": it["title"],
                     "trade": it["trade"] or "", "time_mmss": mmss, "pointed": "",
                     "fixed_in_rewalk": "", "claimed_fixed_in_rewalk": "", "clip": clip})
    return rows


def to_csv(rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(COLUMNS), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def write_drafts(data_dir: Path, walks: list[str], out: Path, *, db: Path | None = None,
                 walk_ids: dict[str, str] | None = None) -> dict[str, Path]:
    if out.resolve() == LABELS_DIR.resolve() or LABELS_DIR.resolve() in out.resolve().parents:
        raise DraftError("drafts never go into eval/labels/ (that folder is human ground truth); use --out elsewhere")
    con = connect(find_db(data_dir, db))
    try:
        docs: dict[str, str] = {}
        for name in walks:
            walk = resolve_walk(con, name, (walk_ids or {}).get(name))
            rows = draft_rewalk(con, name, walk) if walk["kind"] == "rewalk" else draft_initial(con, name, walk)
            docs[name] = to_csv(rows)
    finally:
        con.close()
    out.mkdir(parents=True, exist_ok=True)
    written = {}
    for name, text in docs.items():
        path = out / f"{name}.csv"
        path.write_text(text)
        written[name] = path
    names = sorted(p.stem for p in out.glob("*.csv"))
    (out / "README.md").write_text(README.format(walks=", ".join(names)))
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write label drafts from pipeline output (for a human to correct).")
    ap.add_argument("--data-dir", type=Path, required=True, help="DATA_DIR the pipeline ran in (e.g. data)")
    ap.add_argument("--walks", nargs="+", required=True, help="walk names, e.g. walk1 walk2 walk3")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="default eval/labels_draft (never eval/labels)")
    ap.add_argument("--db", type=Path, help="database path (default: found under --data-dir)")
    ap.add_argument("--walk-id", action="append", default=[], help="walk=ID when ids aren't wlk_record_/wlk_eval_")
    args = ap.parse_args(argv)
    try:
        ids = dict(pair.split("=", 1) for pair in args.walk_id)
    except ValueError:
        print("draft_labels: --walk-id takes walk=ID, e.g. --walk-id walk1=wlk_abc123")
        return 2
    try:
        written = write_drafts(args.data_dir, args.walks, args.out, db=args.db, walk_ids=ids)
    except DraftError as exc:
        print(f"draft_labels: {exc}")
        return 2
    for name, path in written.items():
        n = path.read_text().count("\n") - 1
        print(f"  {path} ({n} rows) DRAFT: correct it, then copy to eval/labels/{name}.csv")
    print(f"  {args.out / 'README.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
