"""Human labels (FOOTAGE.md "Labels"): CSV rows -> `Label`s grouped per walk.

Format (eval/labels_template.csv):

    walk,item_no,room,description,trade,time_mmss,pointed,fixed_in_rewalk,claimed_fixed_in_rewalk,clip

- One row per item per walk. Files can hold one walk (walk1.csv) or several (the `walk` column decides;
  a blank `walk` falls back to the file name).
- `time_mmss` is when the inspector starts describing the item. With a `clip` letter (a, b, ...) it is
  measured inside that part, so t_label = that part's offset in the concatenated walk + time_mmss.
- Re-walk rows (walk2) reuse the walk1 `item_no` and fill `fixed_in_rewalk` (the truth) and
  `claimed_fixed_in_rewalk` (what was said). A walk with any of those filled is a re-walk.
- Each file holds one walk: `walk` must equal the file name (walk1.csv -> walk1) or be blank.
- A file that is still a copy of eval/labels_template.csv (same rows), or whose `walk` column names
  another walk, is not truth: `discover_labels` raises `TemplateLabels` and nothing is scored.
- `project.json` next to the CSVs (optional) gives the project's rooms/keywords, which must equal the
  `scripts/record.py --rooms/--keywords` used to record (they are part of the cached prompts).
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path

from punchwalk.core.enums import Trade

COLUMNS = (
    "walk", "item_no", "room", "description", "trade", "time_mmss", "pointed",
    "fixed_in_rewalk", "claimed_fixed_in_rewalk", "clip",
)
REQUIRED = ("item_no", "description", "time_mmss")


TEMPLATE = Path(__file__).resolve().parent / "labels_template.csv"


class LabelError(ValueError):
    """A labels file can't be read; the message names the file, line and what to fix."""


class TemplateLabels(LabelError):
    """Label files that are still the template (or name the wrong walk): not ground truth yet."""

    def __init__(self, problems: list[tuple[Path, str]]):
        self.problems = problems  # (file, why)
        super().__init__("; ".join(f"{p.name}: {why}" for p, why in problems))


@dataclass(frozen=True)
class Label:
    walk: str
    item_no: int
    room: str
    description: str
    trade: Trade | None
    time_mmss: str
    pointed: bool
    fixed_in_rewalk: bool | None
    claimed_fixed_in_rewalk: bool | None
    clip: str | None
    source: str  # "walk1.csv:3", for messages
    t: float | None = None  # seconds into the concatenated walk, set by `with_times`

    @property
    def is_rewalk_row(self) -> bool:
        return self.fixed_in_rewalk is not None or self.claimed_fixed_in_rewalk is not None


@dataclass(frozen=True)
class ProjectConfig:
    rooms: list[str]
    keywords: list[str]
    source: str  # where the rooms came from, for the report


def parse_mmss(text: str, where: str = "") -> float:
    """'01:05' -> 65.0, '1:02:03' -> 3723.0, '42' -> 42.0, '00:07.5' -> 7.5."""
    raw = (text or "").strip()
    if not raw:
        raise LabelError(f"{where}: time_mmss is empty (expected MM:SS, e.g. 01:05)")
    parts = raw.split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        raise LabelError(f"{where}: time_mmss {raw!r} is not MM:SS (e.g. 01:05)") from None
    if len(nums) > 3 or any(n < 0 for n in nums):
        raise LabelError(f"{where}: time_mmss {raw!r} is not MM:SS (e.g. 01:05)")
    seconds = 0.0
    for n in nums:
        seconds = seconds * 60 + n
    return seconds


def parse_trade(text: str, where: str = "") -> Trade | None:
    """Enum value or its plain words ('Carpentry & millwork'); blank -> None."""
    raw = (text or "").strip().lower()
    if not raw:
        return None
    norm = re.sub(r"[^a-z0-9]+", "_", raw.replace("&", " ")).strip("_")
    try:
        return Trade(norm)
    except ValueError:
        valid = ", ".join(t.value for t in Trade)
        raise LabelError(f"{where}: unknown trade {text!r} (use one of: {valid})") from None


def parse_flag(text: str, column: str, where: str = "") -> bool | None:
    raw = (text or "").strip().lower()
    if raw == "":
        return None
    if raw in ("1", "y", "yes", "true"):
        return True
    if raw in ("0", "n", "no", "false"):
        return False
    raise LabelError(f"{where}: {column} must be 1, 0 or blank, not {text!r}")


def read_labels_file(path: Path) -> list[Label]:
    path = Path(path)
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = [h.strip() for h in (reader.fieldnames or [])]
        missing = [c for c in REQUIRED if c not in header]
        if missing:
            raise LabelError(f"{path.name}: missing column(s) {', '.join(missing)} (header must be {','.join(COLUMNS)})")
        rows: list[Label] = []
        for line_no, raw in enumerate(reader, start=2):
            row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
            if not any(row.values()):
                continue
            where = f"{path.name}:{line_no}"
            try:
                item_no = int(row.get("item_no", ""))
            except ValueError:
                raise LabelError(f"{where}: item_no {row.get('item_no')!r} is not a whole number") from None
            rows.append(Label(
                walk=row.get("walk") or path.stem,
                item_no=item_no,
                room=row.get("room", ""),
                description=row.get("description", ""),
                trade=parse_trade(row.get("trade", ""), where),
                time_mmss=row.get("time_mmss", ""),
                pointed=bool(parse_flag(row.get("pointed", ""), "pointed", where)),
                fixed_in_rewalk=parse_flag(row.get("fixed_in_rewalk", ""), "fixed_in_rewalk", where),
                claimed_fixed_in_rewalk=parse_flag(
                    row.get("claimed_fixed_in_rewalk", ""), "claimed_fixed_in_rewalk", where),
                clip=(row.get("clip") or "").lower() or None,
                source=where,
            ))
            parse_mmss(rows[-1].time_mmss, where)  # fail early on a bad time
    return rows


def natural_key(name: str) -> tuple:
    """walk2 < walk10."""
    return tuple(int(p) if p.isdigit() else p for p in re.split(r"(\d+)", name))


def _row_key(lb: Label) -> tuple:
    return (lb.walk, lb.item_no, lb.room, lb.description, lb.trade, lb.time_mmss, lb.pointed,
            lb.fixed_in_rewalk, lb.claimed_fixed_in_rewalk, lb.clip)


def template_problem(path: Path, rows: list[Label], template: Path | None = TEMPLATE) -> str | None:
    """Why `path` isn't real labels yet, or None. Same rows as the template, or a `walk` naming another walk."""
    if template is not None and Path(template).is_file():
        tpl = [_row_key(r) for r in read_labels_file(Path(template))]
        if rows and [_row_key(r) for r in rows] == tpl:
            return "template"
    others = sorted({r.walk for r in rows if r.walk != path.stem})
    if others:
        return f"walk column says {', '.join(others)}, not {path.stem}"
    return None


def discover_labels(labels_dir: Path, *, template: Path | None = TEMPLATE) -> dict[str, list[Label]]:
    """{walk: labels} from every *.csv in `labels_dir`, walks in natural order. Empty if there are none.

    Raises `TemplateLabels` if any file is still the template (or names another walk): then no file in
    the folder is used as truth.
    """
    labels_dir = Path(labels_dir)
    if not labels_dir.is_dir():
        return {}
    by_walk: dict[str, list[Label]] = {}
    problems: list[tuple[Path, str]] = []
    for path in sorted(labels_dir.glob("*.csv")):
        rows = read_labels_file(path)
        why = template_problem(path, rows, template)
        if why:
            problems.append((path, why))
            continue
        for label in rows:
            by_walk.setdefault(label.walk, []).append(label)
    if problems:
        raise TemplateLabels(problems)
    for walk, rows in by_walk.items():
        seen: dict[int, str] = {}
        for r in rows:
            if r.item_no in seen:
                raise LabelError(f"{r.source}: item_no {r.item_no} already used for {walk} at {seen[r.item_no]}")
            seen[r.item_no] = r.source
    return {w: by_walk[w] for w in sorted(by_walk, key=natural_key)}


def is_rewalk(labels: list[Label]) -> bool:
    return any(lb.is_rewalk_row for lb in labels)


def clip_index(letter: str) -> int:
    """'a' -> 0, 'b' -> 1, ... (also accepts '1'-based digits)."""
    letter = letter.strip().lower()
    if letter.isdigit():
        return int(letter) - 1
    if len(letter) == 1 and "a" <= letter <= "z":
        return ord(letter) - ord("a")
    raise LabelError(f"clip {letter!r} is not a part letter (a, b, ...)")


def label_time(label: Label, clip_offsets: list[float]) -> float:
    """t_label in seconds: time_mmss, plus the clip part's offset when `clip` is set."""
    t = parse_mmss(label.time_mmss, label.source)
    if not label.clip:
        return t
    idx = clip_index(label.clip)
    if not 0 <= idx < len(clip_offsets):
        raise LabelError(
            f"{label.source}: clip {label.clip!r} but the walk has {len(clip_offsets)} part(s)"
            f" ({', '.join(chr(97 + i) for i in range(len(clip_offsets))) or 'none'})"
        )
    return clip_offsets[idx] + t


def with_times(labels: list[Label], clip_offsets: list[float]) -> list[Label]:
    return [replace(lb, t=label_time(lb, clip_offsets)) for lb in labels]


def project_config(labels_dir: Path, by_walk: dict[str, list[Label]], *, rooms: list[str] | None = None,
                   keywords: list[str] | None = None, fallback: Path | None = None) -> ProjectConfig:
    """Rooms/keywords for the eval project: CLI > <labels>/project.json > `fallback` json > the labels."""
    for path in (Path(labels_dir) / "project.json", fallback):
        if rooms is None and path is not None and Path(path).is_file():
            data = json.loads(Path(path).read_text())
            return ProjectConfig(rooms=list(data.get("rooms") or []),
                                 keywords=list(keywords if keywords is not None else data.get("keywords") or []),
                                 source=str(path))
    if rooms is not None:
        return ProjectConfig(rooms=list(rooms), keywords=list(keywords or []), source="--rooms")
    derived: list[str] = []
    for walk_labels in by_walk.values():
        for lb in sorted(walk_labels, key=lambda x: (x.clip or "", parse_mmss(x.time_mmss, x.source))):
            if lb.room and lb.room not in derived:
                derived.append(lb.room)
    return ProjectConfig(rooms=derived, keywords=list(keywords or []), source="label rooms in walk order")
