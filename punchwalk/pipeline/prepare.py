"""Stage `ingest` for a walk in the DB: clips → display/sam/audio/meta (PLAN 0.5) + poster.jpg.

Reads the uploaded clips from `DATA_DIR/walks/<walk_id>/clips/<order:02d>_<name>` (in name order)
and writes the ingest outputs next to them. When there are no clips but a complete `meta.json`
is already there (a walk loaded from recorded media, e.g. `make seed` or the integration test),
those outputs are used as they are. Updates Walk.media / clips / duration_s / fps / size /
orientation.
"""

from __future__ import annotations

import re
from pathlib import Path

from punchwalk.core.enums import Orientation
from punchwalk.core.models import ClipInfo, Walk, WalkMediaPaths
from punchwalk.pipeline import media
from punchwalk.pipeline import params as P
from punchwalk.pipeline.context import CLIPS_DIR, POSTER_NAME, StageContext, StageOutcome, rel_to_data
from punchwalk.pipeline.frames import laplacian_variance, read_frames
from punchwalk.pipeline.ingest import META_NAME, VIDEO_EXTENSIONS, IngestError, IngestMeta, ingest

_ORDER_PREFIX = re.compile(r"^\d{2,3}_")


def uploaded_clips(wdir: Path) -> list[Path]:
    d = Path(wdir) / CLIPS_DIR
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
                  and not p.name.startswith("."))


def original_name(path_or_name: str) -> str:
    """'00_walk1_a.mp4' -> 'walk1_a.mp4'."""
    return _ORDER_PREFIX.sub("", Path(path_or_name).name)


def write_poster(wdir: Path, meta: IngestMeta) -> Path:
    """poster.jpg: the sharpest SAM frame between 0.5 s and 1.5 s, extracted from display.mp4."""
    fps = meta.sam.fps
    lo = int(P.POSTER_WINDOW_S[0] * fps)
    hi = min(meta.sam.frame_count - 1, int(P.POSTER_WINDOW_S[1] * fps))
    candidates = list(range(min(lo, hi), hi + 1)) or [0]
    frames = read_frames(wdir / meta.sam.path, candidates)
    best = max(frames, key=lambda f: laplacian_variance(frames[f])) if frames else 0
    return media.extract_frame(wdir / meta.display.path, best / fps, wdir / POSTER_NAME)


def run(ctx: StageContext) -> StageOutcome:
    clips = uploaded_clips(ctx.walk_dir)
    if clips:
        meta = ingest(clips, ctx.walk_dir, force=ctx.force)
        source = "clips"
    elif (ctx.walk_dir / META_NAME).is_file():
        meta = IngestMeta.load(ctx.walk_dir)
        missing = [p.name for p in meta.output_paths(ctx.walk_dir) if not p.is_file()]
        if missing:
            raise IngestError(f"walk {ctx.walk_id} has meta.json but is missing {', '.join(missing)}")
        source = "existing"
    else:
        raise IngestError(
            f"walk {ctx.walk_id} has no clips: expected video files in {ctx.walk_dir / CLIPS_DIR}"
        )
    ctx.reset_meta()
    poster = ctx.walk_dir / POSTER_NAME
    if ctx.force or not poster.is_file():
        poster = write_poster(ctx.walk_dir, meta)

    with ctx.session() as s:
        walk = s.get(Walk, ctx.walk_id)
        walk.media = WalkMediaPaths(
            display_mp4=rel_to_data(ctx.walk_dir / meta.display.path),
            sam_mp4=rel_to_data(ctx.walk_dir / meta.sam.path),
            audio_wav=rel_to_data(ctx.walk_dir / meta.audio.path),
            meta_json=rel_to_data(ctx.walk_dir / META_NAME),
            poster=rel_to_data(poster),
        ).model_dump(mode="json")
        walk.clips = [
            ClipInfo(order=c.order, filename=original_name(c.filename), duration_s=c.duration_s,
                     offset_s=c.offset_s).model_dump(mode="json")
            for c in meta.clips
        ]
        walk.duration_s = meta.duration_s
        walk.fps = float(meta.display.fps)
        walk.width, walk.height = meta.display.width, meta.display.height
        walk.orientation = Orientation(meta.orientation)
        s.add(walk)
        s.commit()
    msg = f"{len(meta.clips)} clip(s), {meta.duration_s:.0f} s, {meta.display.width}x{meta.display.height}"
    if source == "existing":
        msg += " (prepared media)"
    return StageOutcome(counts={"clips": len(meta.clips), "seconds": int(round(meta.duration_s))}, message=msg)
