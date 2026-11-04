#!/usr/bin/env python
"""Rebuild the mock API fixtures from real pipeline outputs (PLAN 3.3), so the UI shows real evidence.

    uv run python scripts/build_mock_api.py --data-dir .verify/eval-synth --out .verify/mock_synth
    uv run python scripts/build_mock_api.py --data-dir DIR --project prj_eval --out fixtures/mock_api --force

- Reads a DATA_DIR that the pipeline ran in (e.g. `eval/run_eval.py --data-dir DIR`, or a live data/).
  The source is never changed: the database is copied to a temp file first, and the routes run on the copy.
- Every response is produced by the API's own route functions (punchwalk/api/routes/*, which build their
  bodies with punchwalk/api/views.py), so each file equals what the live API returns for that request.
  The file set and naming follow fixtures/mock_api/README.md (same as scripts/gen_mock_api.py).
- Media: every `/media/...` URL is copied into <out>/media and rewritten to the mock layout:
  walks/<walk>/display.mp4 and poster.jpg stay put; walks/<walk>/items/<item>/<name> becomes
  items/<item>/<name>, with raw.jpg renamed frame.jpg. display.mp4 is re-encoded (720p short side, H.264
  CRF 28, AAC, +faststart, single-threaded so it is deterministic) when it is bigger than that or over 40 MB.
- Items without a share link get a deterministic token (`shr_` + sha256(item id)[:10]) in the copy, so
  share/<token>.json and the dispatch previews exist and don't change between runs.
- Responses that aren't plain reads are handled like this: share.post.json is the ShareLinkOut for that token;
  dispatch.post.json is not written (the web mock builds sends from the preview); requests/ are example
  bodies built from the real project.
- Processing example: if no selected walk is still processing, the processing project/walk from
  --processing-from (default fixtures/mock_api: prj_unit3a / wlk_3a_first, with its progress replay and the
  upload response the web mock plays) is carried over unchanged, with its media.
- The "How this walk was processed" eval block comes from the views, which read eval/report.json
  (--eval-summary sets PUNCHWALK_EVAL_REPORT for the build): shown only for a report scored on eval/labels/,
  exactly as the live API does.
- Deterministic: the same DATA_DIR gives byte-identical output (no "now" anywhere; healthz.time is the
  newest walk's created_at).
- Refuses to write into fixtures/mock_api without --force, and refuses any non-empty --out that doesn't
  already look like a mock dir. With --force it builds next to fixtures/mock_api, runs the contract test's
  format/point checks and then tests/test_contracts.py itself on the result, and swaps it in only if both pass
  (otherwise the old mocks stay), so --force can't break `make test`. Other --out dirs get the same checks as
  a printed warning. fixtures/mock_api/README.md is kept; other outputs get a short README.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pydantic import BaseModel  # noqa: E402
from sqlmodel import Session, col, create_engine, select  # noqa: E402

from punchwalk import __version__  # noqa: E402
from punchwalk.api import schemas as S  # noqa: E402
from punchwalk.config import reset_settings  # noqa: E402
from punchwalk.core.enums import ItemStatus, ReasoningProvider, Trade, WalkKind, WalkStatus  # noqa: E402
from punchwalk.core.models import Item, Project, Walk  # noqa: E402
from punchwalk.db import DB_FILENAME, as_utc, reset_engines  # noqa: E402

DEFAULT_OUT = REPO_ROOT / "fixtures" / "mock_api"
DEFAULT_EVAL = REPO_ROOT / "eval" / "report.json"
BASE_URL = "https://punchwalk.example.com"  # absolute base inside message text (same as gen_mock_api.py)
MAX_VIDEO_BYTES = 40_000_000
VIDEO_SHORT_SIDE = 720
RENAME = {"raw.jpg": "frame.jpg"}  # pipeline name -> mock name (README "Media")
MOCK_MARKERS = ("healthz.json", "projects.json", "walks.json")


class BuildError(RuntimeError):
    """Something the person running the script has to fix; the message says what."""


@dataclass
class Bundle:
    """The mock dir in memory: JSON documents + media to copy (target rel -> source path)."""

    docs: dict[str, Any] = field(default_factory=dict)
    media: dict[str, Path] = field(default_factory=dict)
    verbatim: set[str] = field(default_factory=set)  # media copied as-is (the synthetic example's)


def dump(model: BaseModel, *, request: bool = False) -> Any:
    return model.model_dump(mode="json", by_alias=True, exclude_unset=request)


def share_token_for(item_id: str) -> str:
    return "shr_" + hashlib.sha256(item_id.encode()).hexdigest()[:10]


# --------------------------------------------------------------------------- source


@contextmanager
def source_environment(data_dir: Path, base_url: str, eval_report: Path | None) -> Iterator[None]:
    """Views read meta.json / stage files under DATA_DIR and the eval report; messages use APP_BASE_URL."""
    env = {"DATA_DIR": str(data_dir), "APP_BASE_URL": base_url, "PUNCHWALK_CACHE": "off",
           # no report -> a path that can't exist, so the block is null rather than the repo's report
           "PUNCHWALK_EVAL_REPORT": str(eval_report or data_dir / ".no-eval-report.json")}
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    reset_settings()
    reset_engines()
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        reset_settings()
        reset_engines()


def copy_database(data_dir: Path, dest: Path) -> Path:
    src = data_dir / DB_FILENAME
    if not src.is_file():
        raise BuildError(f"no database at {src}: run the pipeline there first (e.g. eval/run_eval.py --data-dir {data_dir})")
    target = dest / DB_FILENAME
    with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as a, sqlite3.connect(target) as b:
        a.backup(b)
    return target


def assign_share_tokens(db: Path) -> None:
    """Plain SQL on the copy, so the ORM's `updated_at` onupdate doesn't move (output stays deterministic)."""
    with sqlite3.connect(db) as con:
        ids = [r[0] for r in con.execute("SELECT id FROM items WHERE share_token IS NULL")]
        con.executemany("UPDATE items SET share_token = ? WHERE id = ?", [(share_token_for(i), i) for i in ids])


# --------------------------------------------------------------------------- real responses


def build_real(session: Session, project_ids: list[str] | None) -> Bundle:
    from punchwalk.api.errors import ApiError
    from punchwalk.api.overlay import build_overlay
    from punchwalk.api.routes import items as r_items
    from punchwalk.api.routes import projects as r_projects
    from punchwalk.api.routes import trades as r_trades
    from punchwalk.api.routes import walks as r_walks
    from punchwalk.config import get_settings

    projects = session.exec(select(Project)).all()
    known = {p.id for p in projects}
    if project_ids:
        missing = [p for p in project_ids if p not in known]
        if missing:
            raise BuildError(f"project(s) {', '.join(missing)} not in this data dir (have: {', '.join(sorted(known))})")
        chosen = set(project_ids)
    else:
        chosen = known
    if not chosen:
        raise BuildError("the data dir has no projects")
    b = Bundle()
    docs = b.docs

    walks = [w for w in session.exec(select(Walk)).all() if w.project_id in chosen]
    newest = max((as_utc(w.created_at) for w in walks), default=None) or max(as_utc(p.created_at) for p in projects)
    docs["healthz.json"] = dump(S.HealthOut(ok=True, version=__version__, demo_mode=True,
                                            reasoning_provider=ReasoningProvider(get_settings().REASONING_PROVIDER),
                                            time=newest))
    plist = r_projects.list_projects(session=session)
    plist.projects = [p for p in plist.projects if p.id in chosen]
    docs["projects.json"] = dump(plist)
    for pid in sorted(chosen):
        docs[f"projects/{pid}.json"] = dump(r_projects.read_project(pid, session=session))
        docs[f"projects/{pid}/items.json"] = dump(r_projects.list_items(
            pid, trade=None, severity=None, room=None, status=None, q=None, sort="walk", session=session))
        docs[f"projects/{pid}/walks.json"] = dump(r_projects.list_project_walks(pid, session=session))
        trades = r_trades.list_trades(pid, session=session)
        docs[f"projects/{pid}/trades.json"] = dump(trades)
        for t in trades.trades:
            docs[f"projects/{pid}/trades/{t.trade.value}/preview.json"] = dump(
                r_trades.dispatch_preview(pid, Trade(t.trade), channel=None, session=session))
            docs[f"projects/{pid}/trades/{t.trade.value}/dispatches.json"] = dump(
                r_trades.list_dispatches(pid, Trade(t.trade), session=session))
    wlist = r_walks.list_walks(project_id=None, session=session)
    wlist.walks = [w for w in wlist.walks if w.project_id in chosen]
    docs["walks.json"] = dump(wlist)

    for w in sorted(walks, key=lambda w: w.id):
        docs[f"walks/{w.id}.json"] = dump(r_walks.read_walk(w.id, session=session))
        with contextlib.suppress(ApiError):  # no job row (run outside the queue): only processing walks poll it
            docs[f"walks/{w.id}/progress.json"] = dump(r_walks.read_progress(w.id, session=session))
        docs[f"walks/{w.id}/model-calls.json"] = dump(r_walks.read_model_calls(w.id, session=session))
        docs[f"walks/{w.id}/processing.json"] = dump(r_walks.read_processing(w.id, session=session))
        if w.status == WalkStatus.ready:
            docs[f"walks/{w.id}/overlay.json"] = dump(build_overlay(session, w))  # = GET .../overlay, no cache write
        if w.kind == WalkKind.rewalk:
            docs[f"walks/{w.id}/rewalk-summary.json"] = dump(r_walks.read_rewalk_summary(w.id, session=session))

    items = session.exec(
        select(Item).where(col(Item.project_id).in_(chosen), col(Item.dismissed_at).is_(None)).order_by(Item.id)
    ).all()
    for it in items:
        docs[f"items/{it.id}.json"] = dump(r_items.read_item(it.id, session=session))
        tok = it.share_token or share_token_for(it.id)
        docs[f"items/{it.id}/share.post.json"] = dump(S.ShareLinkOut(token=tok, url=f"/share/item/{tok}"))
        docs[f"share/{tok}.json"] = dump(r_items.read_share(tok, session=session))

    docs.update(example_requests(session, sorted(chosen)[0], items, walks))
    return b


def example_requests(session: Session, pid: str, items: list[Item], walks: list[Walk]) -> dict[str, Any]:
    """Example request bodies (validated by the contract schemas), taken from the real project."""
    from punchwalk.api import views

    project = session.get(Project, pid)
    trades = [S.TradeContactIn(**c.model_dump(exclude={"slack_webhook"})) for c in views.contacts(project)][:2]
    out: dict[str, Any] = {
        "requests/projects.post.json": dump(S.ProjectCreate(
            name=project.name, address=project.address, trades=trades, keywords=list(project.keywords or []),
            rooms=list(project.rooms or [])), request=True),
        "requests/projects.patch.json": dump(S.ProjectPatch(rooms=[*(project.rooms or []), "Closet"]), request=True),
        "requests/items.patch.sign_off.json": dump(S.ItemPatch(status=ItemStatus.closed, note="Checked in person."),
                                                   request=True),
    }
    mine = [i for i in items if i.project_id == pid]
    if mine:
        first = mine[0]
        out["requests/items.patch.edit.json"] = dump(S.ItemPatch(trade=Trade(first.trade), title=first.title), request=True)
        sendable = [i for i in mine if i.status in (ItemStatus.open, ItemStatus.dispatched)] or mine
        trade = Trade(sendable[0].trade)
        out["requests/dispatch.post.json"] = dump(S.DispatchCreate(
            channel="outbox", item_ids=[i.id for i in sendable if i.trade == trade]), request=True)
    initial = sorted((w for w in walks if w.project_id == pid and w.kind == WalkKind.initial), key=lambda w: w.id)
    if initial:
        out["requests/rewalk-link.post.json"] = dump(S.RewalkLinkIn(initial_walk_id=initial[0].id), request=True)
    return out


# --------------------------------------------------------------------------- media


def media_target(rel: str) -> str:
    """DATA_DIR-relative media path -> path under <out>/media (README "Media" layout)."""
    m = re.fullmatch(r"walks/[^/]+/items/([^/]+)/([^/]+)", rel)
    if m:
        return f"items/{m.group(1)}/{RENAME.get(m.group(2), m.group(2))}"
    return rel


def rewrite_media(b: Bundle, data_dir: Path) -> None:
    """Point every /media/ URL at the mock layout and record which source file each target needs."""
    taken: dict[str, str] = {}  # target -> source rel

    def fix(node: Any) -> Any:
        if isinstance(node, str) and node.startswith("/media/"):
            rel = node.removeprefix("/media/")
            target = media_target(rel)
            if taken.get(target, rel) != rel:  # two sources would land on one name: keep the original path
                target = rel
            taken[target] = rel
            src = data_dir / rel
            if not src.is_file():
                raise BuildError(f"{node} is referenced but {src} doesn't exist")
            b.media[target] = src
            return "/media/" + target
        if isinstance(node, dict):
            return {k: fix(v) for k, v in node.items()}
        if isinstance(node, list):
            return [fix(v) for v in node]
        return node

    for rel in sorted(b.docs):
        b.docs[rel] = fix(b.docs[rel])


def probe_video(path: Path) -> tuple[int, int] | None:
    if shutil.which("ffprobe") is None:
        return None
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "json", str(path)], capture_output=True, text=True)
    try:
        s = json.loads(out.stdout)["streams"][0]
        return int(s["width"]), int(s["height"])
    except (KeyError, IndexError, ValueError):
        return None


def write_video(src: Path, dest: Path) -> str:
    """Copy display.mp4, or re-encode it to 720p (short side) when it is bigger or over 40 MB."""
    size = probe_video(src)
    big = src.stat().st_size > MAX_VIDEO_BYTES or (size is not None and min(size) > VIDEO_SHORT_SIDE)
    if not big:
        shutil.copyfile(src, dest)
        return "copied"
    if shutil.which("ffmpeg") is None:
        if src.stat().st_size > MAX_VIDEO_BYTES:
            raise BuildError(f"{src} is over 40 MB and ffmpeg isn't installed to shrink it")
        shutil.copyfile(src, dest)
        return "copied (ffmpeg missing, not resized)"
    w, h = size or (0, 0)
    scale = f"scale=-2:{VIDEO_SHORT_SIDE}" if w >= h else f"scale={VIDEO_SHORT_SIDE}:-2"
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", scale, "-c:v", "libx264", "-preset", "medium",
           "-crf", "28", "-pix_fmt", "yuv420p", "-x264-params", "threads=1", "-threads", "1", "-c:a", "aac",
           "-b:a", "96k", "-movflags", "+faststart", "-map_metadata", "-1", "-fflags", "+bitexact",
           "-flags:v", "+bitexact", "-flags:a", "+bitexact", str(dest)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise BuildError(f"ffmpeg couldn't re-encode {src}: {res.stderr.strip()[:300]}")
    if dest.stat().st_size > MAX_VIDEO_BYTES:
        raise BuildError(f"{dest} is still over 40 MB after re-encoding at CRF 28")
    return f"re-encoded to {VIDEO_SHORT_SIDE}p"


# --------------------------------------------------------------------------- processing example


def load_processing_example(src: Path, real: Bundle) -> Bundle | None:
    """The processing project/walk from a synthetic mock dir, when the real data has none."""
    walks_doc = real.docs.get("walks.json", {}).get("walks", [])
    if any(w["status"] in (WalkStatus.processing, WalkStatus.uploaded) for w in walks_doc):
        return None
    if not (src / "walks.json").is_file():
        return None
    src_walks = json.loads((src / "walks.json").read_text())["walks"]
    proc_walks = [w for w in src_walks if w["status"] in (WalkStatus.processing, WalkStatus.uploaded)]
    if not proc_walks:
        return None
    pids = {w["project_id"] for w in proc_walks}
    wids = {w["id"] for w in src_walks if w["project_id"] in pids}
    ex = Bundle()
    for path in sorted(src.rglob("*.json")):
        rel = path.relative_to(src).as_posix()
        parts = rel.split("/")
        keep = (
            (parts[0] == "projects" and (parts[1].removesuffix(".json") in pids))
            or (parts[0] == "walks" and len(parts) > 1 and parts[1].removesuffix(".json") in wids)
        )
        if parts[0] == "items" and len(parts) == 2:
            keep = json.loads(path.read_text()).get("project_id") in pids
        if keep:
            ex.docs[rel] = json.loads(path.read_text())
    item_ids = sorted(rel.split("/")[1].removesuffix(".json") for rel in ex.docs if rel.startswith("items/"))
    for iid in item_ids:
        share_post = src / "items" / iid / "share.post.json"
        if share_post.is_file():
            ex.docs[f"items/{iid}/share.post.json"] = json.loads(share_post.read_text())
            tok = ex.docs[f"items/{iid}/share.post.json"]["token"]
            if (src / "share" / f"{tok}.json").is_file():
                ex.docs[f"share/{tok}.json"] = json.loads((src / "share" / f"{tok}.json").read_text())
    ex.docs["projects.json"] = [p for p in json.loads((src / "projects.json").read_text())["projects"] if p["id"] in pids]
    ex.docs["walks.json"] = [w for w in src_walks if w["project_id"] in pids]
    for doc in list(ex.docs.values()):
        for s in _strings(doc):
            if s.startswith("/media/"):
                ex.media[s.removeprefix("/media/")] = src / "media" / s.removeprefix("/media/")
    for rel, p in ex.media.items():
        if not p.is_file():
            raise BuildError(f"processing example media {rel} is missing in {src / 'media'}")
    # read the bytes now: --out may be the same dir and gets cleaned before writing
    ex.media = {rel: _snapshot(p) for rel, p in ex.media.items()}
    ex.verbatim = set(ex.media)
    return ex


_snapshots: tempfile.TemporaryDirectory | None = None


def _snapshot(path: Path) -> Path:
    global _snapshots
    if _snapshots is None:
        _snapshots = tempfile.TemporaryDirectory(prefix="punchwalk-mock-")
    dest = Path(_snapshots.name) / hashlib.sha256(str(path).encode()).hexdigest()[:16] / path.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, dest)
    return dest


def merge_example(real: Bundle, ex: Bundle) -> None:
    extra_projects = ex.docs.pop("projects.json")
    extra_walks = ex.docs.pop("walks.json")
    projects = real.docs["projects.json"]["projects"] + extra_projects
    projects.sort(key=lambda p: (p.get("created_at") or "", p["id"]), reverse=True)  # API order: newest first
    real.docs["projects.json"]["projects"] = projects
    walks = real.docs["walks.json"]["walks"] + extra_walks
    walks.sort(key=lambda w: (w.get("created_at") or "", w["id"]), reverse=True)
    real.docs["walks.json"]["walks"] = walks
    for rel, doc in ex.docs.items():
        if rel in real.docs:
            raise BuildError(f"the processing example's {rel} collides with a real file")
        real.docs[rel] = doc
    real.media.update(ex.media)
    real.verbatim |= ex.verbatim


def _strings(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in _strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    return []


# --------------------------------------------------------------------------- output


def looks_like_mock_dir(path: Path) -> bool:
    return any((path / m).is_file() for m in MOCK_MARKERS)


def prepare_out(out: Path, *, force: bool) -> bool:
    """Check we may write `out` and empty it. Returns True when README.md is kept (fixtures/mock_api)."""
    is_default = out.resolve() == DEFAULT_OUT.resolve()
    if is_default and not force:
        raise BuildError(f"refusing to overwrite {DEFAULT_OUT.relative_to(REPO_ROOT)} without --force "
                         "(it is what the web app and the contract tests run on)")
    if out.exists() and any(out.iterdir()) and not looks_like_mock_dir(out):
        raise BuildError(f"{out} isn't empty and doesn't look like a mock API dir; pick an empty --out")
    out.mkdir(parents=True, exist_ok=True)
    for child in out.iterdir():
        if is_default and child.name == "README.md":
            continue
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    return is_default


def write_bundle(b: Bundle, out: Path) -> dict[str, str]:
    notes: dict[str, str] = {}
    for rel in sorted(b.docs):
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(b.docs[rel], indent=2, ensure_ascii=False) + "\n")
    for rel in sorted(b.media):
        dest = out / "media" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if rel.endswith("display.mp4") and rel not in b.verbatim:
            notes[rel] = write_video(b.media[rel], dest)
        else:
            shutil.copyfile(b.media[rel], dest)
    return notes


def readme_text(data_dir: Path, b: Bundle, example: bool) -> str:
    projects = ", ".join(f"`{p['id']}`" for p in b.docs["projects.json"]["projects"])
    walks = ", ".join(f"`{w['id']}` ({w['status']})" for w in b.docs["walks.json"]["walks"])
    lines = [
        "# Mock API (built from real pipeline outputs)",
        "",
        f"Built by `scripts/build_mock_api.py --data-dir {data_dir}`. Every file is what the API returns for that "
        "request; the naming convention is the one in fixtures/mock_api/README.md.",
        "",
        f"- Projects: {projects}",
        f"- Walks: {walks}",
    ]
    if example:
        lines.append("- The processing project/walk is the synthetic example from scripts/gen_mock_api.py "
                     "(the real data has no walk still processing).")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- validation


def validate_mock_dir(root: Path) -> list[str]:
    """Contract checks on a built dir: each JSON maps to one schema and round-trips; media resolve, none orphaned."""
    try:
        from tests.test_contracts import MOCK_ROUTES
    except ImportError as exc:  # e.g. inside the Docker image, which has no tests/
        return [f"skipped validation: {exc}"]
    problems: list[str] = []
    referenced: set[str] = set()
    for path in sorted(root.rglob("*.json")):
        rel = path.relative_to(root).as_posix()
        hits = [schema for pattern, schema in MOCK_ROUTES if re.fullmatch(pattern, rel)]
        if len(hits) != 1:
            problems.append(f"{rel}: matches {len(hits)} schemas (need 1)")
            continue
        data = json.loads(path.read_text())
        obj = hits[0].model_validate(data)
        dumped = obj.model_dump(mode="json", by_alias=True, exclude_unset=rel.startswith("requests/"))
        if dumped != data:
            problems.append(f"{rel}: changes when round-tripped through {hits[0].__name__}")
        for s in _strings(data):
            if "/media/" in s:
                if not s.startswith("/media/"):
                    problems.append(f"{rel}: media URL isn't root-relative: {s[:80]}")
                    continue
                target = root / "media" / s.removeprefix("/media/")
                if not target.is_file():
                    problems.append(f"{rel}: missing media {s}")
                referenced.add(s.removeprefix("/media/"))
    media = root / "media"
    on_disk = {f.relative_to(media).as_posix() for f in media.rglob("*") if f.is_file()} if media.is_dir() else set()
    problems += [f"orphan media: {m}" for m in sorted(on_disk - referenced)]
    return problems


def contract_format_problems(root: Path) -> list[str]:
    """tests/test_contracts.py's media-format, point and overlay checks, on any mock dir.

    Anything listed here would fail `make test` if this dir were fixtures/mock_api.
    """
    from PIL import Image

    problems: list[str] = []
    media = root / "media"
    for f in sorted(media.rglob("*.jpg")):
        rel = f.relative_to(root).as_posix()
        with Image.open(f) as im:
            size = im.size
            if im.format != "JPEG":
                problems.append(f"{rel}: not a JPEG")
        if f.name in ("crop.jpg", "after.jpg") and size != (800, 600):
            problems.append(f"{rel}: crops are 800x600 in the contract, this is {size[0]}x{size[1]}")
        if f.name in ("evidence.jpg", "frame.jpg", "after_frame.jpg", "pointing.jpg") and size not in ((1080, 1920), (1920, 1080)):
            problems.append(f"{rel}: full frames are 1080x1920 / 1920x1080 in the contract, this is {size[0]}x{size[1]}")
    for f in sorted(media.rglob("*.mp4")):
        if f.stat().st_size >= 2_000_000:
            problems.append(f"{f.relative_to(root).as_posix()}: {f.stat().st_size / 1e6:.1f} MB (the contract caps videos at 2 MB)")

    points: dict[str, tuple[str, Any]] = {}
    for p in sorted((root / "walks").glob("*.json")):
        detail = S.WalkDetailOut.model_validate_json(p.read_text())
        ts = [pt.t for pt in detail.points]
        if ts != sorted(ts) or [pt.number for pt in detail.points] != list(range(1, len(ts) + 1)):
            problems.append(f"walks/{p.name}: points must be numbered 1.. in time order")
        for pt in detail.points:
            b = pt.hand_box
            where = f"walks/{p.name} point {pt.number} ({pt.id})"
            if not (b.x1 - 1 <= pt.fingertip_x <= b.x2 + 1 and b.y1 - 1 <= pt.fingertip_y <= b.y2 + 1):
                problems.append(f"{where}: fingertip ({pt.fingertip_x}, {pt.fingertip_y}) outside hand_box "
                                f"x {b.x1}..{b.x2}, y {b.y1}..{b.y2}")
            if not 0.4 <= pt.hold_s <= 3.0:
                problems.append(f"{where}: hold_s {pt.hold_s} outside 0.4..3.0")
            if pt.frame != round(pt.t * 10):
                problems.append(f"{where}: frame {pt.frame} != round(t * 10) = {round(pt.t * 10)}")
            points[pt.id] = (p.stem, pt)
    for p in sorted((root / "items").glob("*.json")):
        it = S.ItemOut.model_validate_json(p.read_text())
        ev, where = it.evidence, f"items/{p.name}"
        if not 0 <= it.video_start_s < it.video_end_s:
            problems.append(f"{where}: video_start_s {it.video_start_s} / video_end_s {it.video_end_s}")
        if it.quote_start_s is not None and not it.video_start_s <= it.quote_start_s <= it.video_end_s:
            problems.append(f"{where}: quote_start_s outside the clip window")
        if ev is None:
            problems.append(f"{where}: no evidence")
            continue
        if it.point_id is None:
            if ev.grounding != "none" or ev.pointer or ev.pointing_frame or ev.hand_box:
                problems.append(f"{where}: a voice-only item has grounding none and no pointer/pointing frame")
            continue
        if it.point_id not in points:
            problems.append(f"{where}: point {it.point_id} is in no walk detail")
            continue
        walk_id, pt = points[it.point_id]
        if walk_id != it.origin_walk_id or ev.grounding == "none" or ev.pointer is None or ev.hand_box is None:
            problems.append(f"{where}: a pointed item needs its walk's point, a grounding, a pointer and a hand_box")
        elif not (0 <= ev.pointer.x <= ev.width and 0 <= ev.pointer.y <= ev.height):
            problems.append(f"{where}: pointer outside the frame")
        if not (ev.pointing_frame or "").endswith("/pointing.jpg"):
            problems.append(f"{where}: pointing_frame should be .../pointing.jpg")
        if abs(ev.t - pt.t) > 1.5:
            problems.append(f"{where}: evidence frame {ev.t} s is more than 1.5 s from its point at {pt.t} s")
        if ev.grounding == "pointed_only" and (ev.defect_svg or ev.defect_box):
            problems.append(f"{where}: pointed_only evidence has no outline")
        if ev.grounding in ("sam_concept", "sam_box") and not (ev.defect_svg and ev.defect_box):
            problems.append(f"{where}: a SAM grounding needs defect_svg and defect_box")
    for p in sorted((root / "walks").glob("*/overlay.json")):
        ov = S.OverlayOut.model_validate_json(p.read_text())
        ts = [f.t for f in ov.frames]
        where = p.relative_to(root).as_posix()
        if ts != sorted(ts) or len(ts) != len(set(ts)):
            problems.append(f"{where}: frames out of order or repeated")
        bad: dict[str, list[float]] = {}
        for f in ov.frames:
            if abs(f.t * ov.fps - round(f.t * ov.fps)) >= 1e-6:
                bad.setdefault(f"off the {ov.fps} fps grid", []).append(f.t)
            for h in f.hands:
                b = h.box
                if not (0 <= b.x1 < b.x2 <= ov.width and 0 <= b.y1 < b.y2 <= ov.height):
                    bad.setdefault(f"hand box outside {ov.width}x{ov.height}", []).append(f.t)
                if not (h.svg.startswith("M") and h.svg.endswith("Z")):
                    bad.setdefault("hand svg isn't a closed path", []).append(f.t)
                if h.fingertip and not (b.x1 - 1 <= h.fingertip.x <= b.x2 + 1 and b.y1 - 1 <= h.fingertip.y <= b.y2 + 1):
                    bad.setdefault("fingertip outside its hand box", []).append(f.t)
        for why, at in bad.items():
            problems.append(f"{where}: {why} in {len(at)} frame(s), t {at[0]}..{at[-1]} s")
    return problems


def swap_in_and_run_contracts(staging: Path) -> None:
    """Put `staging` at fixtures/mock_api, run tests/test_contracts.py, and restore the old mocks if it fails."""
    backup = DEFAULT_OUT.with_name(DEFAULT_OUT.name + ".previous")
    if backup.exists():
        shutil.rmtree(backup)
    os.replace(DEFAULT_OUT, backup)
    try:
        os.replace(staging, DEFAULT_OUT)
        res = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "tests/test_contracts.py"], cwd=REPO_ROOT,
                             capture_output=True, text=True, env={**os.environ, "PUNCHWALK_CACHE": "readonly"})
        if res.returncode != 0:
            tail = "\n".join(line for line in res.stdout.splitlines() if line.startswith(("FAILED", "E ")))[:1500]
            raise BuildError("tests/test_contracts.py fails on the new mocks; fixtures/mock_api was left as it was:\n"
                             + (tail or res.stdout[-1500:]))
    except BaseException:
        if DEFAULT_OUT.exists():
            shutil.rmtree(DEFAULT_OUT)
        os.replace(backup, DEFAULT_OUT)
        raise
    shutil.rmtree(backup)


# --------------------------------------------------------------------------- main


def build(data_dir: Path, out: Path, *, projects: list[str] | None = None, force: bool = False,
          processing_from: Path | None = DEFAULT_OUT, eval_summary: Path | None = DEFAULT_EVAL,
          base_url: str = BASE_URL) -> tuple[Bundle, dict[str, str]]:
    data_dir = data_dir.resolve()
    if out.resolve() == DEFAULT_OUT.resolve() and not force:
        raise BuildError(f"refusing to overwrite {DEFAULT_OUT.relative_to(REPO_ROOT)} without --force "
                         "(it is what the web app and the contract tests run on)")
    report = eval_summary.resolve() if eval_summary is not None and eval_summary.is_file() else None
    with tempfile.TemporaryDirectory(prefix="punchwalk-mockdb-") as tmp, source_environment(data_dir, base_url, report):
        db = copy_database(data_dir, Path(tmp))
        assign_share_tokens(db)
        engine = create_engine(f"sqlite:///{db}")
        try:
            with Session(engine, expire_on_commit=False) as s:
                bundle = build_real(s, projects)
        finally:
            engine.dispose()
        rewrite_media(bundle, data_dir)
    example = load_processing_example(processing_from, bundle) if processing_from else None
    if example:
        merge_example(bundle, example)
    if out.resolve() != DEFAULT_OUT.resolve():
        prepare_out(out, force=force)
        notes = write_bundle(bundle, out)
        (out / "README.md").write_text(readme_text(data_dir, bundle, example is not None))
        return bundle, notes
    # fixtures/mock_api: build next to it, check it like `make test` does, swap only if everything passes
    staging = DEFAULT_OUT.with_name(DEFAULT_OUT.name + ".staging")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    try:
        notes = write_bundle(bundle, staging)
        if (DEFAULT_OUT / "README.md").is_file():
            shutil.copyfile(DEFAULT_OUT / "README.md", staging / "README.md")
        problems = validate_mock_dir(staging) + contract_format_problems(staging)
        if problems:
            raise BuildError("the new mocks would fail tests/test_contracts.py, so fixtures/mock_api was left as "
                             "it was:\n  " + "\n  ".join(problems[:25]))
        swap_in_and_run_contracts(staging)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return bundle, notes


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Rebuild the mock API fixtures from real pipeline outputs.")
    ap.add_argument("--data-dir", type=Path, required=True, help="DATA_DIR the pipeline ran in")
    ap.add_argument("--project", action="append", help="project id (repeatable; default: every project)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output dir (default fixtures/mock_api: needs --force)")
    ap.add_argument("--force", action="store_true", help="allow overwriting fixtures/mock_api")
    ap.add_argument("--processing-from", default=str(DEFAULT_OUT),
                    help="mock dir to take the processing walk example from, or 'none'")
    ap.add_argument("--eval-summary", default=str(DEFAULT_EVAL), help="eval/report.json to fill the eval block, or 'none'")
    ap.add_argument("--no-validate", action="store_true", help="skip the contract checks on the output")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out = args.out if args.out.is_absolute() else Path.cwd() / args.out
    data_dir = args.data_dir if args.data_dir.is_absolute() else Path.cwd() / args.data_dir
    proc = None if args.processing_from.lower() == "none" else Path(args.processing_from)
    ev = None if args.eval_summary.lower() == "none" else Path(args.eval_summary)
    try:
        bundle, notes = build(data_dir, out, projects=args.project, force=args.force, processing_from=proc,
                              eval_summary=ev)
    except BuildError as exc:
        print(f"build_mock_api: {exc}")
        return 2
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    for f in files:
        note = notes.get(f.removeprefix("media/"))
        print(f"  {f}" + (f"  ({note})" if note else ""))
    n_json = sum(1 for f in files if f.endswith(".json"))
    print(f"wrote {n_json} JSON files and {sum(1 for f in files if f.startswith('media/'))} media files to {out}")
    if not args.no_validate:
        problems = validate_mock_dir(out)
        if problems:
            print("contract problems:\n  " + "\n  ".join(problems[:30]))
            return 1
        print("contract check: every file round-trips through its schema; media resolve, none orphaned")
        later = contract_format_problems(out)
        if later:
            print(f"{len(later)} format/point problem(s) that tests/test_contracts.py would reject if this were "
                  "fixtures/mock_api (build_mock_api --force refuses then):\n  " + "\n  ".join(later[:25]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
