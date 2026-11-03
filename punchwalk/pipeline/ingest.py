"""Ingest: 1..N ordered clips → display.mp4, sam.mp4 (+ per-part SAM files), audio.wav, meta.json.

Steps (PLAN 0.5):
  1. Normalize each clip: apply rotation (ffmpeg autorotates; portrait stays portrait), fit to the canvas
     (short side ≤ 1080, even dims, never letterboxed), constant frame rate, yuv420p, best-effort
     HDR→SDR, an audio track (silent if the clip has none). Each clip is trimmed to a frame count that
     is a whole number of tenths of a second (a multiple of fps / gcd(fps, 10) frames), so part
     boundaries fall exactly on SAM frames at any frame rate.
  2. Mixed clips: if ANY clip is portrait the canvas is portrait, sized from the first portrait clip
     (glasses footage is never shrunk or pillarboxed); otherwise it is the first clip's size. The
     canvas clip also sets the fps. Clips with another aspect ratio are scaled to fit and padded
     (landscape clips get bars top/bottom on a portrait canvas). See `canvas_policy`.
  3. Concatenate in order (concat demuxer over the intermediates, one re-encode) → display.mp4.
     A single clip is encoded straight to display.mp4 (still one encode).
  4. sam.mp4: 10 fps CFR, 720 px short side, no audio. For a multi-clip walk, sam_part_<k>.mp4 holds
     clip k's SAM frames [sam_frame_offset, sam_frame_offset + sam_frame_count), so SAM can run per part
     (object ids reset per part; global frame index = local index + sam_frame_offset).
  5. audio.wav: mono 16 kHz s16le, taken from display.mp4 so it shares the display timeline.
  6. meta.json (`IngestMeta`), written last: its presence means the outputs are complete.

CLI: `uv run python -m punchwalk.pipeline.ingest <clip> [<clip>...] --out <dir> [--force]`
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import re
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from punchwalk.pipeline import media
from punchwalk.pipeline.media import ClipInfo, MediaError

log = logging.getLogger(__name__)

# Bump when output settings change, so existing outputs are rebuilt instead of skipped.
INGEST_VERSION = 2

DISPLAY_MAX_SHORT_SIDE = 1080
SAM_SHORT_SIDE = 720
SAM_FPS = 10
MIN_FPS, MAX_FPS = 10, 60
AUDIO_RATE = 48000  # intermediate / display audio
WAV_RATE = 16000  # Muse Voice Transcribe input
ASR_MAX_DURATION_S = 600
SAM_MAX_FRAMES = 15000
CANVAS_POLICY = "portrait_if_any_fit_pad"
# Tag frames as BT.709 SDR; ffmpeg ≥ 7 takes color tags from the frames, not only the encoder flags.
SDR_SETPARAMS = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"
ASPECT_TOLERANCE = 0.01  # aspect ratios this close count as equal: scale, don't pad

DISPLAY_NAME = "display.mp4"
SAM_NAME = "sam.mp4"
AUDIO_NAME = "audio.wav"
META_NAME = "meta.json"
WORK_DIR_NAME = "_work"

VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".m4v"})
_WALK_FILE_RE = re.compile(r"^(walk\d+)(?:_([a-z]))?$", re.IGNORECASE)


class IngestError(MediaError):
    """Ingest failed; the message says which step and includes ffmpeg's stderr tail."""


# --------------------------------------------------------------------------- meta.json schema


class VideoTrack(BaseModel):
    path: str  # relative to the ingest output dir
    width: int
    height: int
    fps: int
    frame_count: int


class AudioTrack(BaseModel):
    path: str
    sample_rate: int = WAV_RATE
    channels: int = 1
    codec: str = "pcm_s16le"


class ScaleFactors(BaseModel):
    """Multiply SAM-frame pixel coordinates by these to get display.mp4 pixel coordinates."""

    sam_to_display_x: float
    sam_to_display_y: float


class ClipSource(BaseModel):
    codec: str
    width: int  # coded size, before rotation
    height: int
    rotation: int  # clockwise degrees applied when normalizing
    hdr: bool
    has_audio: bool
    fps: float
    color_transfer: str | None
    color_primaries: str | None
    size_bytes: int


class ClipMeta(BaseModel):
    order: int  # 0-based position in the walk
    filename: str
    duration_s: float  # duration in the walk timeline (trimmed to a whole 0.1 s)
    offset_s: float  # start of this clip in display.mp4 / audio.wav
    sam_frame_offset: int  # = round(offset_s * 10): global SAM index of this clip's first frame
    sam_frame_count: int
    sam_part_path: str  # SAM input for this clip alone ("sam.mp4" for a single-clip walk)
    display_frame_offset: int
    display_frame_count: int
    padded: bool  # scaled to fit + padded because its aspect differs from the canvas
    source: ClipSource


class IngestMeta(BaseModel):
    version: int = INGEST_VERSION
    duration_s: float
    display: VideoTrack
    sam: VideoTrack
    audio: AudioTrack
    orientation: Literal["portrait", "landscape"]
    scale: ScaleFactors
    clips: list[ClipMeta]
    hdr_detected: bool
    hdr_tonemapped: bool  # True only when every HDR clip went through a real tonemap/transfer conversion
    hdr_method: str | None  # "swscale" | "zscale" | "none" | None (no HDR clips)
    canvas_policy: str = CANVAS_POLICY
    warnings: list[str] = Field(default_factory=list)
    input_fingerprint: str
    tools: dict[str, str]
    timings_s: dict[str, float] = Field(default_factory=dict)
    created_at: datetime

    # ---- helpers for later stages -------------------------------------------------------------

    def sam_frame_time(self, sam_frame: int) -> float:
        """Seconds into display.mp4 of a global SAM frame index."""
        return sam_frame / self.sam.fps

    def sam_to_display(self, x: float, y: float) -> tuple[float, float]:
        """SAM-frame pixel coordinates → display.mp4 pixel coordinates."""
        return x * self.scale.sam_to_display_x, y * self.scale.sam_to_display_y

    def clip_at(self, t_s: float) -> ClipMeta:
        """The clip that contains display time `t_s` (clamped to the first/last clip)."""
        for clip in reversed(self.clips):
            if t_s >= clip.offset_s:
                return clip
        return self.clips[0]

    @classmethod
    def load(cls, out_dir: Path) -> IngestMeta:
        return cls.model_validate_json((Path(out_dir) / META_NAME).read_text())

    def save(self, out_dir: Path) -> Path:
        path = Path(out_dir) / META_NAME
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(self.model_dump_json(indent=2))
        os.replace(tmp, path)
        return path

    def output_paths(self, out_dir: Path) -> list[Path]:
        names = {self.display.path, self.sam.path, self.audio.path, *(c.sam_part_path for c in self.clips)}
        return [Path(out_dir) / n for n in sorted(names)]


# --------------------------------------------------------------------------- discovery


def discover_walks(footage_dir: Path) -> dict[str, list[Path]]:
    """Find walks in `footage_dir`: `walkN.<ext>` or ordered parts `walkN_a.<ext>`, `walkN_b.<ext>`, ….

    Returns {"walk1": [paths in order], ...} sorted by walk number. Ignores README.md, hidden files and
    anything that isn't .mp4/.mov/.m4v (any case). A walk with both a single file and parts is an error.
    """
    footage_dir = Path(footage_dir)
    found: dict[str, list[tuple[str, Path]]] = {}
    for path in sorted(footage_dir.iterdir()) if footage_dir.is_dir() else []:
        if path.name.startswith(".") or not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        match = _WALK_FILE_RE.match(path.stem)
        if not match:
            log.info("discover_walks: ignoring %s (expected walkN.<ext> or walkN_a.<ext>)", path.name)
            continue
        walk_id = match.group(1).lower()
        part = (match.group(2) or "").lower()
        found.setdefault(walk_id, []).append((part, path))

    walks: dict[str, list[Path]] = {}
    for walk_id in sorted(found, key=lambda w: int(w[4:])):
        parts = sorted(found[walk_id], key=lambda p: p[0])
        letters = [p[0] for p in parts]
        if "" in letters and len(parts) > 1:
            names = ", ".join(p[1].name for p in parts)
            raise ValueError(f"{walk_id} has both a single file and parts ({names}); keep one or the other")
        if len(set(letters)) != len(letters):
            names = ", ".join(p[1].name for p in parts)
            raise ValueError(f"{walk_id} has two files for the same part ({names})")
        walks[walk_id] = [p[1] for p in parts]
    return walks


# --------------------------------------------------------------------------- ingest


def input_fingerprint(clips: list[Path], extra: str = "") -> str:
    """sha256 over (ingest version, each clip's name + size + mtime): changes when any input changes."""
    entries = []
    for clip in clips:
        st = Path(clip).stat()
        entries.append([Path(clip).name, st.st_size, st.st_mtime_ns])
    blob = json.dumps({"v": INGEST_VERSION, "clips": entries, "extra": extra}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def ingest(
    clips: list[Path],
    out_dir: Path,
    force: bool = False,
    *,
    hdr_method: str | None = None,
) -> IngestMeta:
    """Normalize + concatenate `clips` (in order) into `out_dir`. Idempotent unless `force`.

    `hdr_method` forces an HDR→SDR method ("swscale" | "zscale" | "none"); by default the best one this
    ffmpeg supports is used, falling through to the next one if it fails.
    """
    clips = [Path(c) for c in clips]
    out_dir = Path(out_dir)
    if not clips:
        raise IngestError("ingest needs at least one clip")
    missing = [str(c) for c in clips if not c.is_file()]
    if missing:
        raise IngestError(f"clip(s) not found: {', '.join(missing)}")

    fingerprint = input_fingerprint(clips, extra=hdr_method or "")
    if not force:
        existing = _load_if_current(out_dir, fingerprint)
        if existing is not None:
            log.info("ingest: %s is up to date (fingerprint %s…), skipping", out_dir, fingerprint[:12])
            return existing

    try:
        return _run_ingest(clips, out_dir, fingerprint, hdr_method)
    except IngestError:
        raise
    except MediaError as exc:
        raise IngestError(str(exc)) from exc


def _load_if_current(out_dir: Path, fingerprint: str) -> IngestMeta | None:
    meta_path = out_dir / META_NAME
    if not meta_path.is_file():
        return None
    try:
        meta = IngestMeta.load(out_dir)
    except (ValidationError, ValueError, OSError) as exc:
        log.warning("ingest: ignoring unreadable %s (%s)", meta_path, exc)
        return None
    if meta.input_fingerprint != fingerprint or meta.version != INGEST_VERSION:
        return None
    if not all(p.is_file() and p.stat().st_size > 0 for p in meta.output_paths(out_dir)):
        return None
    return meta


def _run_ingest(clips: list[Path], out_dir: Path, fingerprint: str, hdr_method: str | None) -> IngestMeta:
    started = time.monotonic()
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / WORK_DIR_NAME
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    (out_dir / META_NAME).unlink(missing_ok=True)  # outputs are about to change

    warnings: list[str] = []
    timings: dict[str, float] = {}
    infos = [media.probe(c) for c in clips]
    for info in infos:
        log.info(
            "ingest: %s %s %dx%d rot=%d → %dx%d %.2ffps %.2fs hdr=%s audio=%s",
            info.filename,
            info.codec,
            info.width,
            info.height,
            info.rotation,
            info.display_width,
            info.display_height,
            info.fps,
            info.duration_s,
            info.hdr,
            info.has_audio,
        )

    canvas_clip = _canvas_clip(infos)
    fps = int(min(MAX_FPS, max(MIN_FPS, round(canvas_clip.fps))))
    canvas_w, canvas_h = media.fit_short_side(
        canvas_clip.display_width, canvas_clip.display_height, DISPLAY_MAX_SHORT_SIDE, allow_upscale=False
    )
    methods = _hdr_methods(hdr_method)

    single = len(clips) == 1
    frame_counts: list[int] = []
    padded_flags: list[bool] = []
    used_methods: list[str] = []
    intermediates: list[Path] = []
    for order, info in enumerate(infos):
        frames = _target_frames(info, fps)
        target = out_dir / f"{DISPLAY_NAME}.partial.mp4" if single else work / f"clip_{order:02d}.mov"
        padded, method, elapsed = _normalize_clip(
            info, target, canvas_w, canvas_h, fps, frames, methods, final=single, warnings=warnings
        )
        timings[f"normalize_{order}"] = round(elapsed, 3)
        frame_counts.append(frames)
        padded_flags.append(padded)
        if method is not None:
            used_methods.append(method)
        intermediates.append(target)

    display = out_dir / DISPLAY_NAME
    if single:
        os.replace(intermediates[0], display)
    else:
        timings["concat"] = round(_concat(intermediates, display, work, fps), 3)
    _ensure_no_rotation(display, warnings)

    display_frames = media.count_frames(display)
    expected_frames = sum(frame_counts)
    if display_frames != expected_frames:
        warnings.append(f"display.mp4 has {display_frames} frames, expected {expected_frames}")
        log.warning("ingest: %s", warnings[-1])

    sam_w, sam_h = media.fit_short_side(canvas_w, canvas_h, SAM_SHORT_SIDE, allow_upscale=True)
    timings["sam"] = round(_make_sam(display, out_dir / SAM_NAME, sam_w, sam_h), 3)
    sam_frames = media.count_frames(out_dir / SAM_NAME)

    timings["audio"] = round(_make_wav(display, out_dir / AUDIO_NAME), 3)

    clip_metas = _clip_metas(infos, frame_counts, padded_flags, fps, sam_frames)
    if not single:
        timings["sam_parts"] = round(_make_sam_parts(out_dir, clip_metas, warnings), 3)

    duration_s = round(display_frames / fps, 3)
    if duration_s > ASR_MAX_DURATION_S:
        warnings.append(
            f"walk is {duration_s:.0f}s long; Muse Voice Transcribe takes ≤ {ASR_MAX_DURATION_S}s per request"
        )
    if sam_frames > SAM_MAX_FRAMES:
        warnings.append(f"sam.mp4 has {sam_frames} frames; SAM accepts ≤ {SAM_MAX_FRAMES} per request")

    hdr_detected = any(i.hdr for i in infos)
    hdr_tonemapped = hdr_detected and bool(used_methods) and all(m != "none" for m in used_methods)
    if hdr_detected and not hdr_tonemapped:
        warnings.append(
            "HDR input was converted without a tonemap (no usable swscale/zscale path); colors may look flat"
        )

    shutil.rmtree(work, ignore_errors=True)
    timings["total"] = round(time.monotonic() - started, 3)
    meta = IngestMeta(
        duration_s=duration_s,
        display=VideoTrack(
            path=DISPLAY_NAME, width=canvas_w, height=canvas_h, fps=fps, frame_count=display_frames
        ),
        sam=VideoTrack(path=SAM_NAME, width=sam_w, height=sam_h, fps=SAM_FPS, frame_count=sam_frames),
        audio=AudioTrack(path=AUDIO_NAME),
        orientation="portrait" if canvas_h > canvas_w else "landscape",
        scale=ScaleFactors(
            sam_to_display_x=round(canvas_w / sam_w, 6), sam_to_display_y=round(canvas_h / sam_h, 6)
        ),
        clips=clip_metas,
        hdr_detected=hdr_detected,
        hdr_tonemapped=hdr_tonemapped,
        hdr_method=(used_methods[0] if len(set(used_methods)) == 1 else ",".join(used_methods))
        if used_methods
        else None,
        warnings=warnings,
        input_fingerprint=fingerprint,
        tools=media.tool_versions(),
        timings_s=timings,
        created_at=datetime.now(UTC),
    )
    meta.save(out_dir)
    log.info("ingest: wrote %s in %.1fs", out_dir / META_NAME, timings["total"])
    return meta


def _hdr_methods(forced: str | None) -> list[str]:
    available = media.hdr_methods_available()
    if forced is None:
        return available
    if forced not in ("swscale", "zscale", "none"):
        raise IngestError(f"unknown hdr_method {forced!r}; use swscale, zscale or none")
    if forced not in available:
        raise IngestError(f"hdr_method {forced!r} is not supported by this ffmpeg (available: {available})")
    return [forced]


def _canvas_clip(infos: list[ClipInfo]) -> ClipInfo:
    """The clip that defines the canvas and fps: the first portrait clip if any, else the first clip.

    Portrait video is never letterboxed into a landscape frame (CLAUDE.md "Capture devices").
    """
    return next((i for i in infos if i.orientation == "portrait"), infos[0])


def frame_step(fps: int) -> int:
    """Smallest frame count lasting a whole number of SAM frames (0.1 s) at `fps`: 30 → 3, 24 → 12, 25 → 5."""
    return fps // math.gcd(fps, SAM_FPS)


def _target_frames(info: ClipInfo, fps: int) -> int:
    """Frames this clip contributes: its frame count floored to a multiple of `frame_step(fps)`, so the
    clip lasts a whole number of tenths of a second and the next clip starts exactly on a SAM frame."""
    step = frame_step(fps)
    frames = math.floor(info.duration_s * fps + 1e-6) // step * step
    if frames < step:
        raise IngestError(f"{info.filename} is too short ({info.duration_s:.3f}s)")
    return frames


def _fit_to_canvas(info: ClipInfo, canvas_w: int, canvas_h: int) -> tuple[int, int, bool]:
    """Size to scale this clip to, and whether it needs padding to fill the canvas."""
    src_aspect = info.display_width / info.display_height
    canvas_aspect = canvas_w / canvas_h
    if abs(src_aspect - canvas_aspect) / canvas_aspect <= ASPECT_TOLERANCE:
        return canvas_w, canvas_h, False
    fit_w, fit_h = media.fit_within(info.display_width, info.display_height, canvas_w, canvas_h)
    return fit_w, fit_h, True


def _video_filter(
    info: ClipInfo, canvas_w: int, canvas_h: int, fps: int, method: str | None
) -> tuple[str, bool]:
    fit_w, fit_h, padded = _fit_to_canvas(info, canvas_w, canvas_h)
    sdr_out = "out_color_matrix=bt709:out_range=tv"
    chain: list[str] = []
    if method == "swscale":
        # newer swscale: color-managed transfer + gamut conversion (relative colorimetric):
        # SDR-range midtones come out accurate, highlights above SDR white clip. Resize first so the
        # (slow, float) color conversion runs at the output size.
        chain += [
            f"scale={fit_w}:{fit_h}:flags=bicubic",
            f"scale=out_transfer=bt709:out_primaries=bt709:{sdr_out}",
        ]
    elif method == "zscale":
        chain += [
            "zscale=t=linear:npl=100",
            "format=gbrpf32le",
            "zscale=p=bt709",
            "tonemap=tonemap=hable:desat=0",
            "zscale=t=bt709:m=bt709:r=tv",
            "format=yuv420p",
            f"scale={fit_w}:{fit_h}:flags=bicubic",
        ]
    else:
        chain.append(f"scale={fit_w}:{fit_h}:flags=bicubic:{sdr_out}")
    if padded:
        chain.append(f"pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih)/2:color=black")
    chain += [
        "setsar=1",
        f"fps={fps}",
        "tpad=stop_mode=clone:stop_duration=2",  # never run short of frames before -frames:v
        "format=yuv420p",
        SDR_SETPARAMS,
    ]
    return ",".join(chain), padded


def _sdr_tags() -> list[str]:
    return ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv"]


def _final_video_args(fps: int) -> list[str]:
    """display.mp4 encoder settings: H.264 yuv420p CFR, keyframe every ≤ 2 s, +faststart, AAC."""
    return [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-profile:v",
        "high",
        "-pix_fmt",
        "yuv420p",
        "-fps_mode",
        "cfr",
        "-r",
        str(fps),
        "-g",
        str(2 * fps),
        *_sdr_tags(),
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        str(AUDIO_RATE),
        "-ac",
        "2",
        "-movflags",
        "+faststart",
    ]


def _intermediate_args() -> list[str]:
    """Per-clip intermediates: near-lossless H.264 + PCM in .mov (PCM avoids AAC priming gaps at joins)."""
    return [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "16",
        "-pix_fmt",
        "yuv420p",
        *_sdr_tags(),
        "-c:a",
        "pcm_s16le",
        "-ar",
        str(AUDIO_RATE),
        "-ac",
        "2",
    ]


def _normalize_clip(
    info: ClipInfo,
    target: Path,
    canvas_w: int,
    canvas_h: int,
    fps: int,
    frames: int,
    methods: list[str],
    *,
    final: bool,
    warnings: list[str],
) -> tuple[bool, str | None, float]:
    """Encode one clip to `target`. Returns (padded, hdr method used or None, seconds)."""
    audio_samples = round(frames / fps * AUDIO_RATE)
    duration = frames / fps
    attempts = methods if info.hdr else [None]
    last_error: MediaError | None = None
    for method in attempts:
        vf, padded = _video_filter(info, canvas_w, canvas_h, fps, method)
        args = ["-i", info.path]
        if info.has_audio:
            audio_map = "0:a:0"
        else:
            args += ["-f", "lavfi", "-t", f"{duration + 1:.3f}", "-i", f"anullsrc=r={AUDIO_RATE}:cl=stereo"]
            audio_map = "1:a:0"
        af = f"aresample={AUDIO_RATE},aformat=channel_layouts=stereo,apad,atrim=end_sample={audio_samples}"
        args += [
            "-map",
            "0:v:0",
            "-map",
            audio_map,
            "-vf",
            vf,
            "-af",
            af,
            "-frames:v",
            str(frames),
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-dn",
            "-sn",
            *(_final_video_args(fps) if final else _intermediate_args()),
            str(target),
        ]
        label = f"normalize {info.filename}" + (f" hdr={method}" if method else "")
        try:
            elapsed = media.run_ffmpeg(args, label=label)
            return padded, method, elapsed
        except MediaError as exc:
            last_error = exc
            if method is None or method == attempts[-1]:
                break
            note = f"{info.filename}: HDR method {method!r} failed, trying the next one"
            warnings.append(note)
            log.warning("ingest: %s\n%s", note, exc)
    assert last_error is not None
    raise IngestError(f"could not normalize {info.filename}: {last_error}") from last_error


def _concat(parts: list[Path], display: Path, work: Path, fps: int) -> float:
    listing = work / "concat.txt"
    lines = []
    for part in parts:
        escaped = str(part.resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    listing.write_text("\n".join(lines) + "\n")
    partial = display.with_name(f"{DISPLAY_NAME}.partial.mp4")
    args = [
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(listing),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-map_metadata",
        "-1",
        "-vf",
        SDR_SETPARAMS,
        *_final_video_args(fps),
        str(partial),
    ]
    elapsed = media.run_ffmpeg(args, label=f"concat {len(parts)} clips")
    os.replace(partial, display)
    return elapsed


def _ensure_no_rotation(display: Path, warnings: list[str]) -> None:
    """Rotation is baked into the pixels; make sure no display matrix / rotate tag survived."""
    if media.probe(display).rotation == 0:
        return
    fixed = display.with_name(f"{DISPLAY_NAME}.norot.mp4")
    try:
        media.run_ffmpeg(
            [
                "-display_rotation",
                "0",
                "-i",
                str(display),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(fixed),
            ],
            label="strip rotation",
        )
        os.replace(fixed, display)
    except MediaError as exc:
        warnings.append(f"display.mp4 still carries a rotation tag: {exc}")
        log.warning("ingest: %s", warnings[-1])


def _sam_encode_args() -> list[str]:
    return [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-fps_mode",
        "cfr",
        "-r",
        str(SAM_FPS),
        "-g",
        str(2 * SAM_FPS),
        *_sdr_tags(),
        "-map_metadata",
        "-1",
        "-movflags",
        "+faststart",
    ]


def _make_sam(display: Path, sam: Path, width: int, height: int) -> float:
    partial = sam.with_name(f"{sam.name}.partial.mp4")
    vf = f"fps={SAM_FPS},scale={width}:{height}:flags=bicubic,setsar=1,format=yuv420p,{SDR_SETPARAMS}"
    args = ["-i", str(display), "-map", "0:v:0", "-an", "-vf", vf, *_sam_encode_args(), str(partial)]
    elapsed = media.run_ffmpeg(args, label="sam.mp4")
    os.replace(partial, sam)
    return elapsed


def _make_sam_parts(out_dir: Path, clips: list[ClipMeta], warnings: list[str]) -> float:
    total = 0.0
    for clip in clips:
        part = out_dir / clip.sam_part_path
        partial = part.with_name(f"{part.name}.partial.mp4")
        start, end = clip.sam_frame_offset, clip.sam_frame_offset + clip.sam_frame_count
        vf = f"trim=start_frame={start}:end_frame={end},setpts=PTS-STARTPTS,{SDR_SETPARAMS}"
        args = [
            "-i",
            str(out_dir / SAM_NAME),
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            vf,
            *_sam_encode_args(),
            str(partial),
        ]
        total += media.run_ffmpeg(args, label=f"sam part {clip.order}")
        os.replace(partial, part)
        got = media.count_frames(part)
        if got != clip.sam_frame_count:
            warnings.append(f"{part.name} has {got} frames, expected {clip.sam_frame_count}")
            log.warning("ingest: %s", warnings[-1])
    return total


def _make_wav(display: Path, wav: Path) -> float:
    partial = wav.with_name(f"{wav.stem}.partial.wav")
    args = [
        "-i",
        str(display),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(WAV_RATE),
        "-c:a",
        "pcm_s16le",
        "-map_metadata",
        "-1",
        str(partial),
    ]
    elapsed = media.run_ffmpeg(args, label="audio.wav")
    os.replace(partial, wav)
    return elapsed


def _clip_metas(
    infos: list[ClipInfo], frame_counts: list[int], padded: list[bool], fps: int, sam_total: int
) -> list[ClipMeta]:
    single = len(infos) == 1
    offsets_frames = [sum(frame_counts[:i]) for i in range(len(frame_counts))]
    sam_offsets = [round(off / fps * SAM_FPS) for off in offsets_frames]
    metas: list[ClipMeta] = []
    for order, info in enumerate(infos):
        sam_end = sam_offsets[order + 1] if order + 1 < len(infos) else sam_total
        metas.append(
            ClipMeta(
                order=order,
                filename=info.filename,
                duration_s=round(frame_counts[order] / fps, 3),
                offset_s=round(offsets_frames[order] / fps, 3),
                sam_frame_offset=sam_offsets[order],
                sam_frame_count=max(0, sam_end - sam_offsets[order]),
                sam_part_path=SAM_NAME if single else f"sam_part_{order}.mp4",
                display_frame_offset=offsets_frames[order],
                display_frame_count=frame_counts[order],
                padded=padded[order],
                source=ClipSource(
                    codec=info.codec,
                    width=info.width,
                    height=info.height,
                    rotation=info.rotation,
                    hdr=info.hdr,
                    has_audio=info.has_audio,
                    fps=round(info.fps, 3),
                    color_transfer=info.color_transfer,
                    color_primaries=info.color_primaries,
                    size_bytes=info.size_bytes,
                ),
            )
        )
    return metas


# --------------------------------------------------------------------------- CLI


def summarize(meta: IngestMeta, out_dir: Path) -> str:
    d, s = meta.display, meta.sam
    lines = [
        f"ingest → {out_dir}",
        f"  duration   {meta.duration_s:.2f}s  orientation {meta.orientation}",
        f"  display    {d.path} {d.width}x{d.height} @ {d.fps} fps, {d.frame_count} frames",
        f"  sam        {s.path} {s.width}x{s.height} @ {s.fps} fps, {s.frame_count} frames"
        f"  (sam→display x{meta.scale.sam_to_display_x:.4f}, y{meta.scale.sam_to_display_y:.4f})",
        f"  audio      {meta.audio.path} mono {meta.audio.sample_rate} Hz s16le",
        f"  hdr        detected={meta.hdr_detected} tonemapped={meta.hdr_tonemapped} method={meta.hdr_method}",
    ]
    for c in meta.clips:
        src = c.source
        lines.append(
            f"  clip {c.order}     {c.filename}: {src.codec} {src.width}x{src.height} rot={src.rotation}"
            f" hdr={src.hdr} audio={src.has_audio} | offset {c.offset_s:.2f}s dur {c.duration_s:.2f}s"
            f" sam[{c.sam_frame_offset}:+{c.sam_frame_count}] {c.sam_part_path}{' padded' if c.padded else ''}"
        )
    for w in meta.warnings:
        lines.append(f"  warning    {w}")
    lines.append(f"  timings    {json.dumps(meta.timings_s)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m punchwalk.pipeline.ingest",
        description="Normalize + concatenate ordered walk clips into display.mp4, sam.mp4, audio.wav, meta.json.",
    )
    parser.add_argument("clips", nargs="+", type=Path, help="clip files, in walk order")
    parser.add_argument("--out", required=True, type=Path, help="output directory")
    parser.add_argument("--force", action="store_true", help="re-encode even if outputs are up to date")
    parser.add_argument("--hdr-method", choices=["swscale", "zscale", "none"], default=None)
    parser.add_argument("-q", "--quiet", action="store_true", help="only print the summary")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO, format="%(message)s")
    try:
        meta = ingest(args.clips, args.out, force=args.force, hdr_method=args.hdr_method)
    except IngestError as exc:
        print(f"ingest failed: {exc}", file=sys.stderr)
        return 1
    print(summarize(meta, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
