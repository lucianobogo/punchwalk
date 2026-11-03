"""`make record` (PLAN 0.6): record the live perception calls for every walk in footage/ once.

For each walk found by `discover_walks(footage/)` (walkN.* or parts walkN_a.*, walkN_b.*, ...):
  1. ingest into DATA_DIR/record/<walk>/ (display.mp4, sam.mp4, sam_part_<k>.mp4, audio.wav, meta.json)
  2. transcribe (Muse Voice Transcribe, ENDPOINTING, base vocabulary + --keywords)
  3. track hands (SAM 3.1 concept on sam.mp4, per part)
  4. promote every cache entry those calls used into fixtures/cache/ (so tests and `make seed` replay
     offline), and write compact fixtures to fixtures/pipeline/<walk>/:
     transcript.json, tracks.json, meta.json, poster.jpg (720p)
Then the hand-concept comparison: 3 phrasings ("hand", "index finger", "pointing hand") on 3 frames
of the first walk where the hand is pointing (SAM image mode, 9 calls, about $0.02). Ranked by
mean confidence x frames detected. The default concept is not changed; the numbers are printed and
saved to fixtures/pipeline/<walk>/hand_concept.json.

`--full` then runs the whole pipeline (runner.run_walk: classify, pointing, evidence, Spark Open item
writing, SAM defect outlines, verification) on each recorded walk, in a scratch SQLite DB under --out,
with a project made from --rooms / --keywords (walks named in --rewalk run as re-walks of the first
walk), prints items/cost/time per walk, and promotes those cache entries too. A seed or test that
replays them must use the same project rooms/keywords (they are part of the prompts).

Calls go through punchwalk/clients/* with the cache in readwrite mode (a re-run replays at $0).
A walk whose estimated cost exceeds --max-walk-usd (default $2) is not sent. Replays need the exact
same sam.mp4 / audio.wav bytes (cache keys hash them), so keep DATA_DIR/record/<walk>/ around.

    uv run python scripts/record.py [--walk walk1] [--footage DIR] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import cv2  # noqa: E402

from punchwalk.clients import cache as cache_mod  # noqa: E402
from punchwalk.clients import calls, cost, sam  # noqa: E402
from punchwalk.config import get_settings, reset_settings  # noqa: E402
from punchwalk.pipeline import params as P  # noqa: E402
from punchwalk.pipeline.frames import read_frames  # noqa: E402
from punchwalk.pipeline.ingest import IngestMeta, discover_walks, ingest  # noqa: E402
from punchwalk.pipeline.points import find_holds  # noqa: E402
from punchwalk.pipeline.track import hand_concept, track_walk  # noqa: E402
from punchwalk.pipeline.transcribe import transcribe_audio  # noqa: E402
from punchwalk.vocab import keywords_for  # noqa: E402

POSTER_SHORT_SIDE = 720


class Collector:
    """calls sink: every record (for cost/timing) and the cache keys used."""

    def __init__(self) -> None:
        self.records: list[calls.CallRecord] = []

    def __call__(self, record: calls.CallRecord) -> None:
        self.records.append(record)

    def keys(self) -> list[str]:
        return sorted({r.cache_key for r in self.records if r.cache_key and r.ok})

    def live_usd(self) -> float:
        return round(sum(r.est_cost_usd for r in self.records if not r.cached), 6)


def estimate_usd(meta: IngestMeta) -> float:
    sam_usd = sum(cost.sam_video_cost(c.sam_frame_count) for c in meta.clips)
    return round(sam_usd + cost.asr_cost(int(meta.duration_s * 1000)), 4)


def write_poster(meta: IngestMeta, walk_dir: Path, out: Path) -> Path:
    """720p (short side) poster from display.mp4 near 1 s."""
    from punchwalk.pipeline.prepare import write_poster as full_poster

    full = full_poster(walk_dir, meta)
    img = cv2.imread(str(full))
    h, w = img.shape[:2]
    scale = POSTER_SHORT_SIDE / min(w, h)
    if scale < 1:
        img = cv2.resize(img, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return out


def pointing_frames(track: dict[str, Any], n: int = 3) -> list[int]:
    """Frames where the hand is held still (longest holds first), else the largest hand boxes."""
    s = track["sam"]
    end = s["frame_count"] / s["fps"]
    holds = find_holds(track["records"], fps=s["fps"], w=s["width"], h=s["height"], t0=0.0, t1=end)
    holds.sort(key=lambda h: -h.hold_s)
    frames: list[int] = []
    for h in holds:
        f = h.middle["frame"]
        if all(abs(f - g) > s["fps"] for g in frames):
            frames.append(f)
        if len(frames) == n:
            return sorted(frames)
    def area(r: dict[str, Any]) -> float:
        x1, y1, x2, y2 = r["box"]
        return (x2 - x1) * (y2 - y1)

    for r in sorted(track["records"], key=lambda r: -area(r) * (r.get("confidence") or 0)):
        if all(abs(r["frame"] - g) > s["fps"] for g in frames):
            frames.append(r["frame"])
        if len(frames) == n:
            break
    return sorted(frames)


def compare_phrasings(walk_dir: Path, meta: IngestMeta, track: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    frames = pointing_frames(track)
    if not frames:
        return {"frames": [], "ranking": [], "note": "no hand frames found; comparison skipped"}
    imgs = read_frames(walk_dir / meta.sam.path, frames)
    paths = []
    for f in frames:
        p = out_dir / f"hand_frame_{f:05d}.jpg"
        out_dir.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(p), imgs[f], [cv2.IMWRITE_JPEG_QUALITY, 92])
        paths.append(p)
    rows = []
    for phrase in P.HAND_PHRASINGS:
        confs = []
        for p in paths:
            res = sam.concept_on_image(p, phrase)
            recs = res.all_records()
            confs.append(max((r.confidence or 0.0) for r in recs) if recs else 0.0)
        detected = sum(1 for c in confs if c > 0)
        mean_conf = sum(confs) / len(confs)
        rows.append({"phrase": phrase, "per_frame_max_conf": [round(c, 4) for c in confs],
                     "frames_detected": detected, "mean_conf": round(mean_conf, 4),
                     "score": round(mean_conf * detected, 4)})
    rows.sort(key=lambda r: -r["score"])
    return {"frames": frames, "ranking": rows, "current_default": hand_concept(),
            "note": "frames chosen from the current concept's track; scores = mean max-confidence x frames detected"}


def record_walk(name: str, clips: list[Path], args: argparse.Namespace, keywords: list[str]) -> dict[str, Any]:
    t0 = time.monotonic()
    walk_dir = args.out / name
    meta = ingest(clips, walk_dir)
    est = estimate_usd(meta)
    summary: dict[str, Any] = {"walk": name, "clips": [c.name for c in clips], "duration_s": meta.duration_s,
                               "sam_frames": sum(c.sam_frame_count for c in meta.clips), "estimate_usd": est}
    if est > args.max_walk_usd:
        summary["skipped"] = f"estimated ${est:.2f} > --max-walk-usd ${args.max_walk_usd:.2f}; not sent"
        return summary
    if args.dry_run:
        summary["skipped"] = "dry run"
        return summary
    cost.assert_under_cap(est)
    col = Collector()
    calls.add_sink(col)
    try:
        with calls.call_context(walk_id=f"record:{name}", stage="transcribe"):
            transcript = transcribe_audio(walk_dir / meta.audio.path, keywords)
        with calls.call_context(walk_id=f"record:{name}", stage="track_hands"):
            track = track_walk(walk_dir, meta)
    finally:
        calls.remove_sink(col)
    fx = args.fixtures / name
    fx.mkdir(parents=True, exist_ok=True)
    (fx / "transcript.json").write_text(json.dumps(transcript, indent=1, ensure_ascii=False))
    (fx / "tracks.json").write_text(json.dumps(track, separators=(",", ":")))
    shutil.copy2(walk_dir / "meta.json", fx / "meta.json")
    write_poster(meta, walk_dir, fx / "poster.jpg")
    promoted = [] if args.no_promote else cache_mod.get_cache().promote(col.keys(), dest=args.promote_to)
    summary.update({
        "turns": len(transcript["turns"]),
        "hand_records": len(track["records"]),
        "hand_objects": len({r["object_id"] for r in track["records"]}),
        "parts_complete": all(p["complete"] for p in track["parts"]),
        "live_usd": col.live_usd(),
        "calls": len(col.records),
        "cached_calls": sum(1 for r in col.records if r.cached),
        "promoted": len(promoted),
        "seconds": round(time.monotonic() - t0, 1),
        "_track": track,
        "_meta": meta,
        "_dir": walk_dir,
    })
    return summary


def run_full(done: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    """Run the pipeline on each recorded walk (scratch DB, prepared media) and promote its cache entries."""
    from sqlmodel import Session, SQLModel, create_engine, select

    from punchwalk.core.enums import WalkKind
    from punchwalk.core.models import Item, Project, Walk
    from punchwalk.pipeline.context import walk_dir
    from punchwalk.pipeline.runner import run_walk

    db = args.out / "record.sqlite3"
    db.unlink(missing_ok=True)
    engine = create_engine(f"sqlite:///{db}")
    SQLModel.metadata.create_all(engine)

    def factory() -> Session:
        return Session(engine, expire_on_commit=False)

    with factory() as s:
        s.add(Project(id="prj_record", name="Recorded walks", rooms=list(args.rooms), keywords=list(args.keywords)))
        s.commit()
    results = []
    first_id = None
    for rec in done:
        name = rec["walk"]
        walk_id = f"wlk_record_{name}"
        kind = WalkKind.rewalk if name in set(args.rewalk) and first_id else WalkKind.initial
        wdir = walk_dir(walk_id)
        shutil.rmtree(wdir, ignore_errors=True)
        wdir.mkdir(parents=True)
        meta = rec["_meta"]
        for path in meta.output_paths(rec["_dir"]) + [rec["_dir"] / "meta.json"]:
            shutil.copy2(path, wdir / path.name)
        with factory() as s:
            s.add(Walk(id=walk_id, project_id="prj_record", kind=kind, rewalk_of=first_id if kind == "rewalk" else None))
            s.commit()
        first_id = first_id or walk_id
        col = Collector()
        calls.add_sink(col)
        t0 = time.monotonic()
        try:
            run_walk(walk_id, session_factory=factory)
        finally:
            calls.remove_sink(col)
        promoted = [] if args.no_promote else cache_mod.get_cache().promote(col.keys(), dest=args.promote_to)
        with factory() as s:
            items = s.exec(select(Item).where(Item.origin_walk_id == walk_id).order_by(Item.number)).all()
            grounding: dict[str, int] = {}
            lines = []
            for i in items:
                g = (i.evidence or {}).get("grounding", "-")
                grounding[g] = grounding.get(g, 0) + 1
                lines.append(f"#{i.number:03d} [{i.status}] {i.title} | {i.trade}/{i.severity} | {g}"
                             + (f" | {i.review_reason}" if i.review_reason else ""))
        results.append({"walk": name, "walk_id": walk_id, "items": len(items),
                        "needs_review": sum(1 for i in items if i.status == "needs_review"),
                        "grounding": grounding, "live_usd": col.live_usd(), "calls": len(col.records),
                        "cached_calls": sum(1 for r in col.records if r.cached), "promoted": len(promoted),
                        "seconds": round(time.monotonic() - t0, 1), "lines": lines, "dir": str(wdir)})
    return results


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    s = get_settings()
    ap = argparse.ArgumentParser(description="Record live ASR + SAM hand tracking for every walk in footage/.")
    ap.add_argument("--footage", type=Path, default=REPO_ROOT / "footage")
    ap.add_argument("--out", type=Path, default=Path(s.DATA_DIR) / "record", help="ingest outputs per walk")
    ap.add_argument("--fixtures", type=Path, default=REPO_ROOT / "fixtures" / "pipeline")
    ap.add_argument("--promote-to", type=Path, default=Path(s.FIXTURE_CACHE_DIR), help="committed cache dir")
    ap.add_argument("--walk", action="append", help="only these walks (repeatable), e.g. --walk walk1")
    ap.add_argument("--keywords", nargs="*", default=[], help="extra ASR keywords (project keywords)")
    ap.add_argument("--max-walk-usd", type=float, default=2.0)
    ap.add_argument("--skip-phrasings", action="store_true", help="skip the 3-phrasing hand concept comparison")
    ap.add_argument("--no-promote", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="ingest and estimate only; no API calls")
    ap.add_argument("--full", action="store_true", help="also run the whole pipeline (Phase 1-2) on each walk")
    ap.add_argument("--rooms", nargs="*", default=[], help="project rooms for --full (prompt context)")
    ap.add_argument("--rewalk", nargs="*", default=["walk2"], help="walks that are re-walks (for --full)")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    os.environ.setdefault("PUNCHWALK_CACHE", "readwrite")  # record = store live responses
    reset_settings()
    args = parse_args(argv)
    walks = discover_walks(args.footage)
    if args.walk:
        walks = {k: v for k, v in walks.items() if k in set(args.walk)}
    if not walks:
        print(f"no walks found in {args.footage} (expected walkN.mp4/.mov or walkN_a.mp4, walkN_b.mp4, ...)")
        return 1
    print(f"cache mode {get_settings().PUNCHWALK_CACHE}; ledger before: ${cost.total_usd():.4f}")
    keywords = keywords_for(args.keywords)
    results = []
    for name, clips in walks.items():
        print(f"\n== {name}: {', '.join(c.name for c in clips)}")
        res = record_walk(name, clips, args, keywords)
        results.append(res)
        if "skipped" in res:
            print(f"   skipped: {res['skipped']} (estimate ${res['estimate_usd']:.3f})")
            continue
        print(f"   {res['duration_s']:.1f}s, {res['sam_frames']} SAM frames, {res['turns']} turns, "
              f"{res['hand_records']} hand records / {res['hand_objects']} ids, complete={res['parts_complete']}")
        print(f"   cost ${res['live_usd']:.4f} (estimate ${res['estimate_usd']:.4f}), {res['calls']} calls "
              f"({res['cached_calls']} cached), {res['seconds']}s, promoted {res['promoted']} cache entries")

    done = [r for r in results if "skipped" not in r]
    if done and not args.skip_phrasings and not args.dry_run:
        first = done[0]
        col = Collector()
        calls.add_sink(col)
        try:
            with calls.call_context(walk_id=f"record:{first['walk']}", stage="track_hands"):
                cmp = compare_phrasings(first["_dir"], first["_meta"], first["_track"],
                                        first["_dir"] / "hand_frames")
        finally:
            calls.remove_sink(col)
        cmp["cost_usd"] = col.live_usd()
        out = args.fixtures / first["walk"] / "hand_concept.json"
        out.write_text(json.dumps(cmp, indent=1))
        if not args.no_promote:
            cache_mod.get_cache().promote(col.keys(), dest=args.promote_to)
        print(f"\nhand concept on {first['walk']} frames {cmp['frames']} (default {cmp.get('current_default')!r}):")
        for i, r in enumerate(cmp["ranking"], 1):
            print(f"   {i}. {r['phrase']!r}: score {r['score']:.3f} = mean conf {r['mean_conf']:.3f} x "
                  f"{r['frames_detected']} frames  {r['per_frame_max_conf']}")
        if not cmp["ranking"]:
            print(f"   {cmp['note']}")
        print(f"   cost ${cmp['cost_usd']:.4f} -> {out}")
    if done and args.full and not args.dry_run:
        full = run_full(done, args)
        for r in full:
            print(f"\n== {r['walk']} full pipeline: {r['items']} items, {r['needs_review']} need a look, grounding "
                  f"{r['grounding']}; cost ${r['live_usd']:.4f}, {r['calls']} calls ({r['cached_calls']} cached), "
                  f"{r['seconds']}s, promoted {r['promoted']} entries")
            for line in r["lines"]:
                print(f"   {line}")
    print(f"\nledger after: ${cost.total_usd():.4f}")
    return 0 if done or args.dry_run else 1


if __name__ == "__main__":
    raise SystemExit(main())
