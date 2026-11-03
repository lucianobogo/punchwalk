#!/usr/bin/env python
"""`make seed` (PLAN 8.1): the demo project "Unit 2B final punch", loaded from recorded walks at $0.

    uv run python scripts/seed.py                         # walk1 (first walk), walk2 (re-walk), walk3 if present
    uv run python scripts/seed.py --walks synth_walk      # the synthetic recording in fixtures/pipeline
    uv run python scripts/seed.py --reset                 # drop the demo project first, then seed

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
A snapshot holds the demo project's rows (project, walks, turns, points, items, dispatches, model
calls, jobs) and each walk's public media plus stages/ (display.mp4, poster.jpg, meta.json,
stages/*.json, items/**). It leaves out audio.wav and sam*.mp4, which only the pipeline needs.

Idempotent: walk ids are `wlk_demo_<walk>` and the project id is `prj_demo`. A walk that's already
ready is left alone unless --force. A real Slack webhook (SLACK_WEBHOOK_URL) is ignored while seeding.
"""

from __future__ import annotations

import argparse
import json
import os
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


def reset_project() -> None:
    from sqlmodel import col, delete, select

    from punchwalk.config import get_settings
    from punchwalk.core.models import Dispatch, Item, Job, ModelCall, Point, Project, Turn, Walk
    from punchwalk.db import session_factory

    with session_factory() as s:
        walk_ids = list(s.exec(select(Walk.id).where(Walk.project_id == PROJECT_ID)).all())
        s.exec(delete(Dispatch).where(col(Dispatch.project_id) == PROJECT_ID))
        s.exec(delete(Item).where(col(Item.project_id) == PROJECT_ID))
        for table in (Point, Turn, ModelCall, Job):
            s.exec(delete(table).where(col(table.walk_id).in_(walk_ids)))
        s.exec(delete(Walk).where(col(Walk.id).in_(walk_ids)))
        s.exec(delete(Project).where(col(Project.id) == PROJECT_ID))
        s.commit()
    for wid in walk_ids:
        shutil.rmtree(Path(get_settings().DATA_DIR) / "walks" / wid, ignore_errors=True)


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


def seed_walk(source: Source, *, kind: str, rewalk_of: str | None, force: bool) -> str:
    """Copy media, create Walk + Job, run the job. Returns the walk id."""
    from punchwalk.api import jobs
    from punchwalk.config import get_settings
    from punchwalk.core.enums import Device, JobKind, JobStatus, WalkKind, WalkStatus
    from punchwalk.core.models import Job, Walk, utcnow
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
        job = Job(kind=JobKind.process_walk, walk_id=walk_id, status=JobStatus.running, attempts=1,
                  started_at=now, force=force)  # fmt: skip
        s.add(job)
        s.commit()
        job_id = job.id
    status = jobs.run_job(job_id)
    if status != JobStatus.done:
        # Never leave the job queued for a retry: a live app's worker would replay it with its own
        # (readwrite) cache and make live calls. Close it as failed, with the reason on the walk.
        with session_factory() as s:
            job, walk = s.get(Job, job_id), s.get(Walk, walk_id)
            if job is not None and job.status != JobStatus.failed:
                job.status, job.finished_at = JobStatus.failed, utcnow()
                s.add(job)
            if walk is not None and walk.status != WalkStatus.failed:
                walk.status, walk.error = WalkStatus.failed, (job.error if job else None) or walk.error
                s.add(walk)
            s.commit()
        raise SeedError(_failure(walk_id, source))
    return walk_id


def _failure(walk_id: str, source: Source) -> str:
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
        f"--walk {source.walk} --full --rooms {' '.join(repr(r) if ' ' in r else r for r in DEMO_ROOMS)}"
        if source.origin != "fixtures"
        else f"Re-record the synthetic walk with the commands in fixtures/pipeline/README.md ({source.walk})."
    )
    stage = failed.value if failed else "?"
    return f"{source.walk} ({walk_id}) failed at stage {stage}: {error}\n{hint}"


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
    return out


def print_summary(info: dict[str, Any]) -> None:
    print(f"Project {info['project']}: {info['name']} (rooms: {', '.join(info['rooms'])})")
    for w in info["walks"]:
        print(f"  {w['walk_id']}  {w['kind']:<8} {w['status']:<6} {w['duration_s'] or 0:.0f} s  "
              f"{w['turns']} lines, {w['points']} points, {len(w['items'])} items, {w['model_calls']} model calls")
        for line in w["items"]:
            print(f"    {line}")
    print(f"Live API cost: ${info['live_cost_usd']:.4f} ({info['cached_calls']} calls answered from the cache)")


# --------------------------------------------------------------------------- snapshots

SNAPSHOT_VERSION = 1
SNAPSHOT_SKIP = ("audio.wav",)  # plus sam*.mp4 and clips/: pipeline inputs, not needed to serve the demo


def _tables():
    from punchwalk.core.models import Dispatch, Item, Job, ModelCall, Point, Project, Turn, Walk

    return {"projects": Project, "walks": Walk, "turns": Turn, "points": Point, "items": Item,
            "dispatches": Dispatch, "model_calls": ModelCall, "jobs": Job}  # fmt: skip


def export_snapshot(path: Path) -> dict[str, int]:
    """Write the demo project's rows + public walk media to a .tar.gz."""
    import io
    import tarfile

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
    with tarfile.open(path, "w:gz") as tar:
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
                tar.add(f, arcname=f"walks/{wid}/{rel.as_posix()}")
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
        reset_project()
        members = [m for m in tar.getmembers() if m.isfile() and m.name.startswith("walks/")]
        for m in members:
            parts = Path(m.name).parts
            if ".." in parts or Path(m.name).is_absolute():
                raise SeedError(f"Unsafe path in snapshot: {m.name}")
        tar.extractall(data, members=members, filter="data")
    tables = _tables()
    with session_factory() as s:
        for name in ("projects", "walks", "turns", "points", "items", "dispatches", "model_calls", "jobs"):
            for raw in manifest["rows"].get(name, []):
                s.add(tables[name].model_validate(raw))
            s.flush()
        s.commit()
    return [w["id"] for w in manifest["rows"].get("walks", [])]


# --------------------------------------------------------------------------- main


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Seed the demo project from recorded walks (no API cost).")
    ap.add_argument("--walks", nargs="+", default=None, help="walk names in recording order (default: walk1 walk2 [walk3])")
    ap.add_argument("--rooms", nargs="+", default=None, help="override the rooms the walks were recorded with")
    ap.add_argument("--keywords", nargs="*", default=None, help="override the recording's ASR keywords")
    ap.add_argument("--reset", action="store_true", help="drop the demo project (rows + walk media) first")
    ap.add_argument("--force", action="store_true", help="re-run walks that are already ready")
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    snap = ap.add_mutually_exclusive_group()
    snap.add_argument("--export", type=Path, metavar="TAR_GZ", help="after seeding, write a snapshot of the demo project")
    snap.add_argument("--import", dest="import_", type=Path, metavar="TAR_GZ",
                      help="load a snapshot instead of running the pipeline (replaces the demo project)")
    return ap.parse_args(argv)


def _offline_settings() -> None:
    """Replay only, and never post the demo to a real Slack channel."""
    os.environ["PUNCHWALK_CACHE"] = "readonly"
    os.environ["SLACK_WEBHOOK_URL"] = ""
    os.environ["RESEND_API_KEY"] = ""
    from punchwalk.config import reset_settings

    reset_settings()


def run(argv: list[str] | None = None) -> dict[str, Any]:
    args = parse_args(argv)
    _offline_settings()
    from punchwalk.db import init_db

    init_db()
    if args.import_:
        walk_ids = import_snapshot(args.import_)
        info = summary(walk_ids)
        print(json.dumps(info, indent=1)) if args.json else print_summary(info)
        return info
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
    upsert_project(rooms, keywords)
    walk_ids: list[str] = []
    first_initial: str | None = None
    for src in sources:
        kind = "rewalk" if src.walk in REWALK_NAMES and first_initial else "initial"
        wid = seed_walk(src, kind=kind, rewalk_of=first_initial if kind == "rewalk" else None, force=args.force)
        first_initial = first_initial or (wid if kind == "initial" else None)
        walk_ids.append(wid)
        print(f"seeded {src.walk} from {src.origin}: {src.media_dir}", file=sys.stderr)
    info = summary(walk_ids)
    if args.json:
        print(json.dumps(info, indent=1))
    else:
        print_summary(info)
    if args.export:
        counts = export_snapshot(args.export)
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
