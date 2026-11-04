#!/usr/bin/env python
"""`make seed`: the demo project "Unit 2B final punch", loaded from recorded walks at $0.

    uv run python scripts/seed.py                         # walk1 (first walk), walk2 (re-walk), walk3 if present
    uv run python scripts/seed.py --walks synth_walk      # the synthetic recording in fixtures/pipeline
    uv run python scripts/seed.py --reset                 # drop the demo project first, then seed
    uv run python scripts/seed.py --drop-project prj_x    # drop one project (rows + walk media), nothing else
    uv run python scripts/seed.py --drop-all              # drop EVERY project (the boot reset token uses this)

What it does:
1. Creates or updates project `prj_demo`: address, rooms, keywords, and a fictional trades directory
   (@example.com contacts; Ace Electric has a https://hooks.slack.example.com webhook, which the app
   never contacts, so the Slack path shows without network calls).
2. For each walk, in order: copies the recorded media (display.mp4, sam.mp4, sam_part_*.mp4,
   audio.wav, meta.json, poster.jpg) into DATA_DIR/walks/wlk_demo_<walk>/ and any walk-local cache
   entries into DATA_DIR/cache. Then it creates the Walk and a Job and runs the job through the
   app's job runner, which runs the real pipeline, then the auto-send policy, with
   PUNCHWALK_CACHE=readonly. Every model answer comes from the cache; a miss stops the seed and
   says which walk and stage missed and how to re-record.
3. Prints the project, walks, items and the live cost (always $0).

Where recorded walks are found, first match wins:
  DATA_DIR/record/<walk>/        (`scripts/record.py` output; its cache entries are in fixtures/cache)
  demo/<walk>/                   (committed demo copies; optional demo/<walk>/cache/)
  fixtures/pipeline/<walk>/media (synthetic test recordings; cache in fixtures/pipeline/<walk>/cache)

Cache keys hash the exact media bytes and the prompts, and the prompts include the project's rooms
and keywords and the project-scoped item numbers. So the seed must use the rooms and keywords
`scripts/record.py --full` used, and seed the walks into one fresh project in the recording's order.
Rooms come from, in order: --rooms, `recording.json` next to the media ({"rooms": [...],
"keywords": [...]}), the known synthetic profile (synth_walk: Kitchen, Hallway, Bedroom), and
finally the demo rooms. For real walks that means recording with:
  uv run python scripts/record.py --full --rooms Kitchen Bathroom Bedroom Hallway "Living room"

Snapshots (for a deploy whose ffmpeg differs from the recording machine's): Spark Open prompts
include JPEG frames that ffmpeg extracts at run time, so a cache recorded on a Mac can miss in the
Linux image. Seed where the recording was made, then ship the result instead of re-running it:
  uv run python scripts/seed.py --export demo/seed_snapshot.tar.gz       # after a successful seed
  python scripts/seed.py --import demo/seed_snapshot.tar.gz              # in the container: no pipeline
`--export-only` packs the demo project as it is (no seeding, no row writes), e.g. after re-rendering its media.
Add `--video-crf 28` to the export to re-encode each display.mp4 in the snapshot (same size and fps,
so the overlay's display-pixel coordinates still line up; DATA_DIR/walks is left as it is).
A snapshot holds the demo project's rows (project, walks, turns, points, items, dispatches, model
calls, jobs) and each walk's public media plus stages/ (display.mp4, poster.jpg, meta.json,
stages/*.json, items/**). It leaves out audio.wav and sam*.mp4, which only the pipeline needs.

The seed keeps its sends: the automatic send after the first walk and the re-walk's re-send run as
in the app. The seed blanks SLACK_WEBHOOK_URL and RESEND_API_KEY, so only trades with their own
(example-domain) webhook get anything; the others have no working Slack or email, so their items stay
To send with a "Couldn't go out automatically" note. `--no-sends` (AUTO_SEND off: no Dispatch rows at
all) is still there if a snapshot without any sends is wanted.

Idempotent: walk ids are `wlk_demo_<walk>` and the project id is `prj_demo`. A walk that's already
ready is left alone unless --force. A real Slack webhook (SLACK_WEBHOOK_URL) is ignored while seeding.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PROJECT_ID = "prj_demo"
PROJECT_NAME = "Unit 2B final punch"
PROJECT_ADDRESS = "418 Alder Street, Unit 2B"
DEMO_ROOMS = ["Kitchen", "Bathroom", "Bedroom", "Hallway", "Living room"]
DEFAULT_WALKS = ["walk1", "walk2", "walk3"]
OPTIONAL_WALKS = {"walk3"}
REWALK_NAMES = {"walk2", "synth_rewalk"}  # same default as scripts/record.py --rewalk
DEMO_WEBHOOK = "https://hooks.slack.example.com/services/T000DEMO/B000DEMO/punchwalkdemo"

# Rooms/keywords each known recording was made with (see fixtures/pipeline/README.md).
KNOWN_PROFILES: dict[str, dict[str, list[str]]] = {
    "synth_walk": {"rooms": ["Kitchen", "Hallway", "Bedroom"], "keywords": []},
    "synth_rewalk": {"rooms": ["Kitchen", "Hallway", "Bedroom"], "keywords": []},
}

TRADES: list[dict[str, Any]] = [
    {"trade": "electrical", "company": "Ace Electric", "contact": "Maria Chen", "email": "maria.chen@example.com",
     "phone": "(555) 010-2201", "slack_channel": "#punch-electrical", "slack_webhook": DEMO_WEBHOOK},
    {"trade": "carpentry_millwork", "company": "Oakline Millwork", "contact": "Dev Patel",
     "email": "dev.patel@example.com", "phone": "(555) 010-2202"},
    {"trade": "cleaning", "company": "Spotless Final Clean", "contact": "Rosa Alvarez",
     "email": "rosa.alvarez@example.com", "phone": "(555) 010-2203"},
    {"trade": "general", "company": "Northfield Builders", "contact": "Sam Okafor", "phone": "(555) 010-2204"},
    {"trade": "doors_hardware", "company": "Keystone Door & Hardware", "contact": "Lena Brooks",
     "email": "lena.brooks@example.com", "phone": "(555) 010-2205"},
    {"trade": "painting", "company": "Brightline Painting", "contact": "Tom Reyes", "email": "tom.reyes@example.com",
     "phone": "(555) 010-2206"},
    {"trade": "windows_glazing", "company": "Clearview Glass", "contact": "Ana Kowalski", "phone": "(555) 010-2207"},
    {"trade": "plumbing", "company": "Riverside Plumbing", "contact": "Joe Lindqvist",
     "email": "joe.lindqvist@example.com", "phone": "(555) 010-2208"},
    {"trade": "appliances", "company": "Northline Appliance Service", "contact": "Priya Nair",
     "email": "priya.nair@example.com", "phone": "(555) 010-2209"},
]  # fmt: skip


class SeedError(RuntimeError):
    """A readable reason the seed stopped (missing recording, cache miss, ...)."""


@dataclass
class Source:
    walk: str
    media_dir: Path  # holds meta.json + the ingest outputs
    cache_dir: Path | None  # walk-local cache entries to copy into DATA_DIR/cache
    origin: str  # "record" | "demo" | "fixtures"

    def profile(self) -> dict[str, list[str]] | None:
        for candidate in (self.media_dir / "recording.json", self.media_dir.parent / "recording.json"):
            if candidate.is_file():
                data = json.loads(candidate.read_text())
                return {"rooms": list(data.get("rooms") or []), "keywords": list(data.get("keywords") or [])}
        return KNOWN_PROFILES.get(self.walk)


def find_source(walk: str) -> Source | None:
    from punchwalk.config import get_settings

    data = Path(get_settings().DATA_DIR)
    candidates = [
        (data / "record" / walk, None, "record"),
        (REPO_ROOT / "demo" / walk, REPO_ROOT / "demo" / walk / "cache", "demo"),
        (REPO_ROOT / "fixtures" / "pipeline" / walk / "media", REPO_ROOT / "fixtures" / "pipeline" / walk / "cache",
         "fixtures"),
    ]  # fmt: skip
    for media_dir, cache_dir, origin in candidates:
        if (media_dir / "meta.json").is_file():
            return Source(walk, media_dir, cache_dir if cache_dir and cache_dir.is_dir() else None, origin)
    return None


def media_files(src: Path) -> list[Path]:
    from punchwalk.pipeline.ingest import IngestMeta

    meta = IngestMeta.load(src)
    files = [*meta.output_paths(src), src / "meta.json"]
    if (src / "poster.jpg").is_file():
        files.append(src / "poster.jpg")
    missing = [p.name for p in files if not p.is_file()]
    if missing:
        raise SeedError(f"{src} is missing {', '.join(missing)}; re-run scripts/record.py for this walk.")
    return files


def recorded_at(src: Path):
    from punchwalk.pipeline.ingest import IngestMeta

    created = IngestMeta.load(src).created_at
    return created if created.tzinfo else created.replace(tzinfo=UTC)


# --------------------------------------------------------------------------- database


def reset_project(project_id: str = PROJECT_ID) -> list[str]:
    """Drop one project: its dispatches, items, walks (with their points, turns, model calls and jobs) and
    each walk's media folder under DATA_DIR/walks. Returns the dropped walk ids."""
    from sqlmodel import col, delete, select

    from punchwalk.config import get_settings
    from punchwalk.core.models import Dispatch, Item, Job, ModelCall, Point, Project, Turn, Walk
    from punchwalk.db import session_factory

    with session_factory() as s:
        walk_ids = list(s.exec(select(Walk.id).where(Walk.project_id == project_id)).all())
        s.exec(delete(Dispatch).where(col(Dispatch.project_id) == project_id))
        s.exec(delete(Item).where(col(Item.project_id) == project_id))
        for table in (Point, Turn, ModelCall, Job):
            s.exec(delete(table).where(col(table.walk_id).in_(walk_ids)))
        s.exec(delete(Walk).where(col(Walk.id).in_(walk_ids)))
        s.exec(delete(Project).where(col(Project.id) == project_id))
        s.commit()
    walks_root = (Path(get_settings().DATA_DIR) / "walks").resolve()
    for wid in walk_ids:
        folder = (walks_root / wid).resolve()
        if folder.parent == walks_root:  # never outside DATA_DIR/walks
            shutil.rmtree(folder, ignore_errors=True)
    return walk_ids


def project_ids() -> list[str]:
    from sqlmodel import select

    from punchwalk.core.models import Project
    from punchwalk.db import session_factory

    with session_factory() as s:
        return sorted(s.exec(select(Project.id)).all())


def drop_projects(ids: list[str] | None = None) -> dict[str, Any]:
    """Drop the given projects (None: every project) with `reset_project`. Returns what was dropped."""
    known = project_ids()
    targets = known if ids is None else list(dict.fromkeys(ids))
    missing = [p for p in targets if p not in known]
    if missing:
        raise SeedError(f"No project {', '.join(missing)} in {_db_where()}; nothing was dropped.")
    walks: list[str] = []
    for pid in targets:
        walks += reset_project(pid)
    return {"dropped": targets, "walks": walks}


def _db_where() -> str:
    from punchwalk.config import get_settings

    return f"DATA_DIR {get_settings().DATA_DIR}"


def upsert_project(rooms: list[str], keywords: list[str]) -> None:
    from punchwalk.core.models import Project, TradeContact, utcnow
    from punchwalk.db import session_factory

    trades = [TradeContact.model_validate(t).model_dump(mode="json") for t in TRADES]
    with session_factory() as s:
        project = s.get(Project, PROJECT_ID) or Project(id=PROJECT_ID, name=PROJECT_NAME)
        if project.rooms and list(project.rooms) != rooms:
            raise SeedError(
                f"The demo project already uses rooms {project.rooms}, not {rooms}. Item numbers and prompts "
                "would no longer match the recording; run with --reset."
            )
        project.name, project.address = PROJECT_NAME, PROJECT_ADDRESS
        project.rooms, project.keywords, project.trades = rooms, keywords, trades
        project.updated_at = utcnow()
        s.add(project)
        s.commit()


SEED_INTERRUPTED = "The seed was interrupted before this walk finished; run scripts/seed.py again."


def seed_walk(source: Source, *, kind: str, rewalk_of: str | None, force: bool, rooms: list[str]) -> str:
    """Copy media, create Walk + Job, run the job. Returns the walk id."""
    from sqlmodel import select

    from punchwalk.api import jobs
    from punchwalk.config import get_settings
    from punchwalk.core.enums import Device, JobKind, JobStatus, WalkKind, WalkStatus
    from punchwalk.core.models import Job, ModelCall, Walk, utcnow
    from punchwalk.db import session_factory

    walk_id = f"wlk_demo_{source.walk}"
    data = Path(get_settings().DATA_DIR)
    with session_factory() as s:
        existing = s.get(Walk, walk_id)
        if existing is not None and existing.status == WalkStatus.ready and not force:
            return walk_id

    wdir = data / "walks" / walk_id
    wdir.mkdir(parents=True, exist_ok=True)
    for path in media_files(source.media_dir):
        dest = wdir / path.name
        if not dest.is_file() or dest.stat().st_size != path.stat().st_size:
            shutil.copy2(path, dest)
    if source.cache_dir is not None:
        shutil.copytree(source.cache_dir, get_settings().cache_dir, dirs_exist_ok=True)

    with session_factory() as s:
        earlier_calls = set(s.exec(select(ModelCall.id).where(ModelCall.walk_id == walk_id)).all())
        walk = s.get(Walk, walk_id) or Walk(id=walk_id, project_id=PROJECT_ID)
        walk.kind = WalkKind(kind)
        walk.rewalk_of = rewalk_of
        walk.status = WalkStatus.processing
        walk.error = None
        walk.recorded_at = recorded_at(source.media_dir)
        walk.device = Device.glasses if source.walk.startswith("walk") else Device.unknown
        s.add(walk)
        now = utcnow()
        # Created `running`, so a live app's worker (which only claims `queued` jobs) never picks it up.
        # `progress.seed` marks it as a seed job (the runner's progress events replace it while it runs;
        # close_seed_job() writes it back).
        job = Job(kind=JobKind.process_walk, walk_id=walk_id, status=JobStatus.running, attempts=1,
                  started_at=now, force=force, progress={"seed": True})  # fmt: skip
        s.add(job)
        s.commit()
        job_id = job.id
    # Never leave the job queued or running: a live app's worker (or its boot-time recovery, which
    # re-queues `running` jobs) would replay it with its own readwrite cache and make live calls. So the
    # job is closed as failed on every exit that isn't a clean finish, Ctrl-C and crashes included.
    try:
        status = jobs.run_job(job_id)
    except BaseException:
        close_seed_job(job_id, walk_id, SEED_INTERRUPTED)
        raise
    finally:
        drop_replayed_calls(walk_id, earlier_calls)
    if status != JobStatus.done:
        close_seed_job(job_id, walk_id, None)
        raise SeedError(_failure(walk_id, source, rooms))
    return walk_id


def drop_replayed_calls(walk_id: str, earlier: set[str]) -> int:
    """After a re-run (--force, or resuming a failed walk), drop the older ModelCall rows it repeated.

    A call is repeated when the new run logged the same cache key (or, without a key, the same stage,
    model and item). Older rows the re-run didn't repeat stay: the pipeline keeps some finished work
    (an item that was already sent, say), and its calls still belong on the processing screen.
    Returns how many rows were dropped.
    """
    from sqlmodel import col, delete, select

    from punchwalk.core.models import ModelCall
    from punchwalk.db import session_factory

    if not earlier:
        return 0
    with session_factory() as s:
        calls = s.exec(select(ModelCall).where(ModelCall.walk_id == walk_id)).all()
        new = [c for c in calls if c.id not in earlier]
        keys = {c.cache_key for c in new if c.cache_key}
        sigs = {(c.stage, c.model, c.item_id) for c in new if not c.cache_key}
        stale = [
            c.id for c in calls
            if c.id in earlier and (c.cache_key in keys if c.cache_key else (c.stage, c.model, c.item_id) in sigs)
        ]  # fmt: skip
        if stale:
            s.exec(delete(ModelCall).where(col(ModelCall.id).in_(stale)))
            s.commit()
        return len(stale)


def close_seed_job(job_id: str, walk_id: str, error: str | None) -> None:
    """Mark a seed job (and its walk) failed. `error` overrides the reason the job recorded."""
    from punchwalk.core.enums import JobStatus, WalkStatus
    from punchwalk.core.models import Job, Walk, utcnow
    from punchwalk.db import session_factory

    with session_factory() as s:
        job, walk = s.get(Job, job_id), s.get(Walk, walk_id)
        reason = error or (job.error if job else None) or (walk.error if walk else None)
        if job is not None:
            if job.status != JobStatus.failed:
                job.status, job.finished_at = JobStatus.failed, utcnow()
            job.error = reason
            job.progress = {**(job.progress or {}), "seed": True}
            s.add(job)
        if walk is not None and (walk.status != WalkStatus.failed or error):
            walk.status, walk.error = WalkStatus.failed, reason
            s.add(walk)
        s.commit()


def _failure(walk_id: str, source: Source, rooms: list[str]) -> str:
    from punchwalk.api.progress import stage_states
    from punchwalk.core.enums import StageStatus
    from punchwalk.core.models import Walk
    from punchwalk.db import session_factory

    with session_factory() as s:
        walk = s.get(Walk, walk_id)
        failed = next((st for st, state in stage_states(walk).items() if state.status == StageStatus.failed), None)
        error = walk.error if walk else "unknown error"
    hint = (
        "Re-record it (live, small cost): uv run python scripts/record.py "
        f"--walk {source.walk} --full --rooms {' '.join(repr(r) if ' ' in r else r for r in rooms)}"
        if source.origin != "fixtures"
        else f"Re-record the synthetic walk with the commands in fixtures/pipeline/README.md ({source.walk})."
    )
    stage = failed.value if failed else "?"
    return f"{source.walk} ({walk_id}) failed at stage {stage}: {error}\n{hint}"


def dispatch_count() -> int:
    from sqlmodel import select

    from punchwalk.core.models import Dispatch
    from punchwalk.db import session_factory

    with session_factory() as s:
        return len(s.exec(select(Dispatch.id).where(Dispatch.project_id == PROJECT_ID)).all())


# --------------------------------------------------------------------------- summary


def summary(walk_ids: list[str]) -> dict[str, Any]:
    from sqlmodel import select

    from punchwalk.core.models import Item, ModelCall, Point, Project, Turn, Walk
    from punchwalk.db import session_factory

    out: dict[str, Any] = {"project": PROJECT_ID, "walks": [], "live_cost_usd": 0.0, "cached_calls": 0}
    with session_factory() as s:
        project = s.get(Project, PROJECT_ID)
        out["name"] = project.name if project else None
        out["rooms"] = list(project.rooms or []) if project else []
        for wid in walk_ids:
            walk = s.get(Walk, wid)
            items = s.exec(select(Item).where(Item.origin_walk_id == wid).order_by(Item.number)).all()
            calls = s.exec(select(ModelCall).where(ModelCall.walk_id == wid)).all()
            live = sum(c.est_cost_usd or 0.0 for c in calls if not c.cached)
            out["live_cost_usd"] += live
            out["cached_calls"] += sum(1 for c in calls if c.cached)
            out["walks"].append({
                "walk_id": wid,
                "kind": walk.kind.value if walk else None,
                "status": walk.status.value if walk else None,
                "duration_s": walk.duration_s if walk else None,
                "turns": len(s.exec(select(Turn.id).where(Turn.walk_id == wid)).all()),
                "points": len(s.exec(select(Point.id).where(Point.walk_id == wid)).all()),
                "items": [f"#{i.number:03d} {i.status.value:<12} {i.trade.value:<18} {i.title}" for i in items],
                "model_calls": len(calls),
                "live_cost_usd": round(live, 4),
            })
    out["live_cost_usd"] = round(out["live_cost_usd"], 4)
    out["dispatches"] = dispatch_count()
    return out


def print_summary(info: dict[str, Any]) -> None:
    print(f"Project {info['project']}: {info['name']} (rooms: {', '.join(info['rooms'])})")
    for w in info["walks"]:
        print(f"  {w['walk_id']}  {w['kind']:<8} {w['status']:<6} {w['duration_s'] or 0:.0f} s  "
              f"{w['turns']} lines, {w['points']} points, {len(w['items'])} items, {w['model_calls']} model calls")
        for line in w["items"]:
            print(f"    {line}")
    print(f"Live API cost: ${info['live_cost_usd']:.4f} ({info['cached_calls']} calls answered from the cache)")
    print(f"Sends: {info.get('dispatches', 0)}")


# --------------------------------------------------------------------------- snapshots

SNAPSHOT_VERSION = 1
SNAPSHOT_SKIP = ("audio.wav",)  # plus sam*.mp4 and clips/: pipeline inputs, not needed to serve the demo


def _tables():
    from punchwalk.core.models import Dispatch, Item, Job, ModelCall, Point, Project, Turn, Walk

    return {"projects": Project, "walks": Walk, "turns": Turn, "points": Point, "items": Item,
            "dispatches": Dispatch, "model_calls": ModelCall, "jobs": Job}  # fmt: skip


def reencode_display(src: Path, dest: Path, crf: int) -> None:
    """display.mp4 -> a smaller copy: libx264 at `crf`, same resolution and fps, AAC 96k, +faststart."""
    import subprocess

    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", str(src),
           "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
           "-pix_fmt", "yuv420p", "-fps_mode", "passthrough", "-c:a", "aac", "-b:a", "96k",
           "-movflags", "+faststart", "-map_metadata", "0", str(dest)]  # fmt: skip
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise SeedError("--video-crf needs ffmpeg on PATH.") from exc
    except subprocess.CalledProcessError as exc:
        raise SeedError(f"Couldn't re-encode {src}: {(exc.stderr or '').strip()[-400:]}") from exc
    if _video_shape(dest) != _video_shape(src):
        raise SeedError(f"Re-encoding {src} changed its size or frame rate: {_video_shape(src)} -> {_video_shape(dest)}")


def _video_shape(path: Path) -> tuple[str, ...]:
    import subprocess

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate",
         "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    return tuple(out.strip().split(","))


def export_snapshot(path: Path, video_crf: int | None = None) -> dict[str, int]:
    """Write the demo project's rows + public walk media to a .tar.gz.

    `video_crf` re-encodes each display.mp4 for the snapshot only (see `reencode_display`).
    """
    import io
    import tarfile
    import tempfile

    from sqlmodel import col, select

    from punchwalk.config import get_settings
    from punchwalk.core.models import Dispatch, Item, Job, ModelCall, Point, Project, Turn, Walk
    from punchwalk.db import session_factory

    rows: dict[str, list[dict[str, Any]]] = {}
    with session_factory() as s:
        if s.get(Project, PROJECT_ID) is None:
            raise SeedError("There's no demo project to export; run the seed first.")
        walk_ids = list(s.exec(select(Walk.id).where(Walk.project_id == PROJECT_ID)).all())
        queries = {
            "projects": select(Project).where(Project.id == PROJECT_ID),
            "walks": select(Walk).where(Walk.project_id == PROJECT_ID),
            "items": select(Item).where(Item.project_id == PROJECT_ID),
            "dispatches": select(Dispatch).where(Dispatch.project_id == PROJECT_ID),
            "turns": select(Turn).where(col(Turn.walk_id).in_(walk_ids)),
            "points": select(Point).where(col(Point.walk_id).in_(walk_ids)),
            "model_calls": select(ModelCall).where(col(ModelCall.walk_id).in_(walk_ids)),
            "jobs": select(Job).where(col(Job.walk_id).in_(walk_ids)),
        }
        for name, q in queries.items():
            rows[name] = [r.model_dump(mode="json") for r in s.exec(q).all()]
    manifest = {"version": SNAPSHOT_VERSION, "project_id": PROJECT_ID, "rows": rows}
    data = Path(get_settings().DATA_DIR)
    path.parent.mkdir(parents=True, exist_ok=True)
    files = 0
    with tempfile.TemporaryDirectory(prefix="seed_snapshot_") as tmp, tarfile.open(path, "w:gz") as tar:
        blob = json.dumps(manifest).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(blob)
        tar.addfile(info, io.BytesIO(blob))
        for wid in walk_ids:
            wdir = data / "walks" / wid
            for f in sorted(p for p in wdir.rglob("*") if p.is_file()):
                rel = f.relative_to(wdir)
                if rel.parts[0] in ("clips", "_work") or f.name in SNAPSHOT_SKIP or f.name.startswith("sam"):
                    continue
                source = f
                if video_crf is not None and rel.as_posix() == "display.mp4":
                    source = Path(tmp) / f"{wid}_display.mp4"
                    reencode_display(f, source, video_crf)
                    print(f"re-encoded {wid}/display.mp4 at CRF {video_crf}: "
                          f"{f.stat().st_size / 1e6:.1f} MB -> {source.stat().st_size / 1e6:.1f} MB", file=sys.stderr)  # fmt: skip
                tar.add(source, arcname=f"walks/{wid}/{rel.as_posix()}")
                files += 1
    return {name: len(r) for name, r in rows.items()} | {"files": files}


def import_snapshot(path: Path) -> list[str]:
    """Replace the demo project with the snapshot's rows and media. Returns the walk ids."""
    import tarfile

    from punchwalk.config import get_settings
    from punchwalk.db import session_factory

    if not path.is_file():
        raise SeedError(f"No snapshot at {path}. Make one with: uv run python scripts/seed.py --export {path}")
    data = Path(get_settings().DATA_DIR)
    with tarfile.open(path, "r:gz") as tar:
        manifest = json.loads(tar.extractfile("manifest.json").read())  # type: ignore[union-attr]
        if manifest.get("version") != SNAPSHOT_VERSION or manifest.get("project_id") != PROJECT_ID:
            raise SeedError(f"{path} isn't a demo snapshot this version can read.")
        # Check everything before touching the demo project, so a bad snapshot leaves it as it was.
        rows = _validate_rows(path, manifest.get("rows") or {})
        base_url = get_settings().APP_BASE_URL  # on Fly: https://punchwalk.fly.dev (fly.toml)
        for d in rows["dispatches"]:
            d.payload = rewrite_share_origin(d.payload, base_url)
        members = [m for m in tar.getmembers() if m.isfile() and m.name.startswith("walks/")]
        for m in members:
            parts = Path(m.name).parts
            if ".." in parts or Path(m.name).is_absolute():
                raise SeedError(f"Unsafe path in snapshot: {m.name}")
        reset_project()
        tar.extractall(data, members=members, filter="data")
    with session_factory() as s:
        for name in SNAPSHOT_TABLES:
            for row in rows[name]:
                s.add(row)
            s.flush()
        s.commit()
    return [w.id for w in rows["walks"]]


SNAPSHOT_TABLES = ("projects", "walks", "turns", "points", "items", "dispatches", "model_calls", "jobs")

# The origin in front of a share link ("http://localhost:8000/share/item/shr_..."): the snapshot is made
# locally, so its dispatch payloads carry the local origin; the importing app swaps in its own.
_SHARE_ORIGIN_RE = re.compile(r"https?://[^\s/\"'<>|]+(?=/share/)")


def rewrite_share_origin(value: Any, base_url: str) -> Any:
    """`value` (a dispatch payload: text, blocks, links ...) with every share link's origin replaced by
    `base_url`. Other URLs (webhooks, images) and everything else are left as they are."""
    base = base_url.rstrip("/")
    if isinstance(value, str):
        return _SHARE_ORIGIN_RE.sub(base, value)
    if isinstance(value, list):
        return [rewrite_share_origin(v, base) for v in value]
    if isinstance(value, dict):
        return {k: rewrite_share_origin(v, base) for k, v in value.items()}
    return value


def _validate_rows(path: Path, raw_rows: dict[str, Any]) -> dict[str, list[Any]]:
    """Every manifest row as a model instance; a SeedError naming the first bad row otherwise."""
    from pydantic import ValidationError

    tables = _tables()
    out: dict[str, list[Any]] = {}
    for name in SNAPSHOT_TABLES:
        out[name] = []
        for n, raw in enumerate(raw_rows.get(name) or []):
            try:
                out[name].append(tables[name].model_validate(raw))
            except (ValidationError, TypeError, ValueError) as exc:
                raise SeedError(
                    f"{path} has a bad {name} row (#{n + 1}): {exc}. Nothing was changed; export a new snapshot."
                ) from exc
    return out


# --------------------------------------------------------------------------- main


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Seed the demo project from recorded walks (no API cost).")
    ap.add_argument("--walks", nargs="+", default=None, help="walk names in recording order (default: walk1 walk2 [walk3])")
    ap.add_argument("--rooms", nargs="+", default=None, help="override the rooms the walks were recorded with")
    ap.add_argument("--keywords", nargs="*", default=None, help="override the recording's ASR keywords")
    ap.add_argument("--reset", action="store_true", help="drop the demo project (rows + walk media) first")
    ap.add_argument("--force", action="store_true", help="re-run walks that are already ready")
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    ap.add_argument("--no-sends", action="store_true",
                    help="seed with AUTO_SEND off: no automatic sends or re-walk re-sends, so no Dispatch rows")
    snap = ap.add_mutually_exclusive_group()
    snap.add_argument("--drop-project", nargs="+", metavar="PROJECT_ID", default=None,
                      help="drop these projects (rows + walk media) and stop; no seeding")
    snap.add_argument("--drop-all", action="store_true",
                      help="drop EVERY project (rows + walk media) and stop; no seeding")
    snap.add_argument("--export", type=Path, metavar="TAR_GZ", help="after seeding, write a snapshot of the demo project")
    snap.add_argument("--import", dest="import_", type=Path, metavar="TAR_GZ",
                      help="load a snapshot instead of running the pipeline (replaces the demo project)")
    ap.add_argument("--video-crf", type=int, default=None, metavar="N",
                    help="with --export: re-encode each display.mp4 in the snapshot with libx264 at CRF N "
                         "(same size and fps; e.g. 28). Default: copy the videos as they are")
    ap.add_argument("--export-only", action="store_true",
                    help="with --export: pack the demo project as it is now, without seeding or writing any row "
                         "(e.g. after scripts/rerender_display.py)")
    args = ap.parse_args(argv)
    if args.export_only and not args.export:
        ap.error("--export-only needs --export")
    if args.video_crf is not None and not args.export:
        ap.error("--video-crf only applies to --export")
    if args.video_crf is not None and not 0 <= args.video_crf <= 51:
        ap.error("--video-crf must be between 0 and 51")
    return args


def _offline_settings(*, no_sends: bool = False) -> None:
    """Replay only, and never post the demo to a real Slack channel. `no_sends`: AUTO_SEND off."""
    os.environ["PUNCHWALK_CACHE"] = "readonly"
    os.environ["SLACK_WEBHOOK_URL"] = ""
    os.environ["RESEND_API_KEY"] = ""
    if no_sends:
        os.environ["AUTO_SEND"] = "false"
    from punchwalk.config import reset_settings

    reset_settings()


def run(argv: list[str] | None = None) -> dict[str, Any]:
    args = parse_args(argv)
    _offline_settings(no_sends=args.no_sends)
    from punchwalk.db import init_db

    init_db()
    if args.drop_all or args.drop_project:
        info = drop_projects(None if args.drop_all else args.drop_project)
        if args.json:
            print(json.dumps(info))
        else:
            print(f"dropped {len(info['dropped'])} project(s), {len(info['walks'])} walk(s): "
                  f"{', '.join(info['dropped']) or '-'}")
        return info
    if args.import_:
        walk_ids = import_snapshot(args.import_)
        info = summary(walk_ids)
        print(json.dumps(info, indent=1)) if args.json else print_summary(info)
        return info
    if args.export_only:
        counts = export_snapshot(args.export, video_crf=args.video_crf)
        print(f"Snapshot written to {args.export}: {counts}", file=sys.stderr)
        return counts
    names = args.walks or DEFAULT_WALKS
    sources: list[Source] = []
    for name in names:
        src = find_source(name)
        if src is None:
            if args.walks is None and name in OPTIONAL_WALKS:
                continue
            raise SeedError(
                f"No recording of {name}. Looked in DATA_DIR/record/{name}/, demo/{name}/ and "
                f"fixtures/pipeline/{name}/media. Record it with `make record` (scripts/record.py --full), "
                "or seed the synthetic walk: --walks synth_walk"
            )
        sources.append(src)

    profiles = {json.dumps(p, sort_keys=True) for s in sources if (p := s.profile())}
    if len(profiles) > 1:
        raise SeedError("These walks were recorded with different rooms/keywords; seed them separately.")
    profile = json.loads(profiles.pop()) if profiles else {"rooms": DEMO_ROOMS, "keywords": []}
    rooms = args.rooms or profile["rooms"]
    keywords = args.keywords if args.keywords is not None else profile["keywords"]

    if args.reset:
        reset_project()
    if args.no_sends and dispatch_count() and not args.reset:
        raise SeedError("The demo project already has sends; run with --reset --no-sends to start it without any.")
    upsert_project(rooms, keywords)
    walk_ids: list[str] = []
    first_initial: str | None = None
    for src in sources:
        kind = "rewalk" if src.walk in REWALK_NAMES and first_initial else "initial"
        wid = seed_walk(src, kind=kind, rewalk_of=first_initial if kind == "rewalk" else None, force=args.force,
                        rooms=rooms)
        first_initial = first_initial or (wid if kind == "initial" else None)
        walk_ids.append(wid)
        print(f"seeded {src.walk} from {src.origin}: {src.media_dir}", file=sys.stderr)
    info = summary(walk_ids)
    if args.json:
        print(json.dumps(info, indent=1))
    else:
        print_summary(info)
    if args.no_sends and dispatch_count():
        raise SeedError("--no-sends, but the demo project has Dispatch rows; nothing was exported.")
    if args.export:
        counts = export_snapshot(args.export, video_crf=args.video_crf)
        print(f"Snapshot written to {args.export}: {counts}", file=sys.stderr)
    return info


def main(argv: list[str] | None = None) -> int:
    try:
        run(argv)
    except SeedError as exc:
        print(f"seed failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
