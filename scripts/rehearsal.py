"""Live-upload rehearsal (PLAN 10.9): upload walks the way the phone does, time every stage, check the result.

Two jobs, one script:

1. **Make the test clips** (our own footage or synthetic; no third-party media):

       uv run python scripts/rehearsal.py make-clips --footage footage --out .verify/rehearsal/clips

   writes beat2/ (symlinks to the Ray-Ban Meta clips docs/DEMO_SCRIPT.md beat 2 uploads, in order),
   iphone_hdr_portrait.mp4 (HEVC 10-bit HLG BT.2020, true 1080x1920 portrait, 30 fps, AAC speech),
   iphone_hdr_rotated.mp4 (the same kind of clip stored 1920x1080 + a 90° display matrix, like an iPhone),
   five_seconds.mp4 (5 s SDR H.264 portrait, speech), silent.mp4 (20 s SDR, no audio stream at all) and
   landscape.mp4 (20 s 1920x1080 SDR H.264, speech).

2. **Run a rehearsal upload** against a server (local Docker or the deployed app):

       uv run python scripts/rehearsal.py --base-url http://localhost:8090 --project new \\
           --clips .verify/rehearsal/clips/five_seconds.mp4 --out .verify/rehearsal/run-five_seconds

   It creates a project ("Rehearsal <timestamp>") or uses `--project <id>`, streams one multipart POST
   (`clips` parts in order, the same path the Record screen uses) while timing it, polls
   /api/walks/<id>/progress every `--poll` s and logs each stage transition, then on `ready` fetches the
   walk, its items, /processing and /model-calls, downloads the poster, every item's evidence frame and
   the first ~2 s of display.mp4 (a Range request), and checks colours: mean luma and the fraction of
   clipped highlights (>= 30% clipped, a flat/washed-out frame or a near-black frame fails the run). It
   writes `summary.json`, `run.log`, `frames/` and `evidence_strip.jpg` into `--out`.

   Exit codes: 0 ready and sane · 2 the walk failed (its readable error is in summary.json) · 3 timed out ·
   4 ready but a colour/format check failed · 5 the upload was refused (e.g. the DEMO_MODE limit; pass
   `--wait-for-slot` to wait for the next slot instead) · 6 the server can't be reached.

   `recheck <run dir>…` re-scores saved frames with the current thresholds; `table` prints one row per run
   from their summary.json files:

       uv run python scripts/rehearsal.py table .verify/rehearsal/run-*/summary.json

There is no delete API, and re-seeding (docs/DEPLOY.md, snapshot `--import`) only replaces the demo
project: "Rehearsal …" projects stay in the live project list until they are removed by hand on the
server. DEMO_MODE allows 3 uploads per hour per IP.
On a server with AUTO_SEND on and a team SLACK_WEBHOOK_URL, the verified items of a rehearsal walk are
posted to that Slack channel (the rehearsal project has no trades directory, so the team webhook is used).
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# --------------------------------------------------------------------------- constants

# docs/DEMO_SCRIPT.md beat 2: "2-3 short glasses clips (e.g. 30 s with 2 defects)". walk1_d + walk1_e are
# the bedroom and hallway parts of the first walk (26 s, 3 defects called out).
BEAT2_CLIPS = ("walk1_d.MOV", "walk1_e.MOV")
# 60-90 s of original HLG glasses footage for the iPhone-style HDR clip (walk1 a+b+c = 79 s).
HDR_SOURCES = ("walk1_a.MOV", "walk1_b.MOV", "walk1_c.MOV")
HDR_ROTATED_SOURCES = ("walk1_c.MOV",)  # 30 s: also the "30 s walk" timing point
SDR_SOURCE = "walk3.MOV"  # laundry room, 16 s, speech from 0.5 s
LANDSCAPE_SOURCES = ("walk1_d.MOV", "walk1_e.MOV")
SILENT_SOURCES = ("walk1_b.MOV", "walk1_d.MOV")

FPS = 30
SDR_SETPARAMS = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"
CLIPPED_LUMA = 250  # 8-bit luma at or above this counts as a clipped highlight
MAX_CLIPPED_FRACTION = 0.30
MIN_MEAN_LUMA, MAX_MEAN_LUMA = 25.0, 215.0
# HLG shown without a tonemap: bright and grey. Measured on our glasses footage: raw HLG frames have mean
# luma 170-186 with mean saturation 9-10; tonemapped frames 110-155 with 15-24 (a plain wall is low-contrast
# but keeps its saturation, so spread alone is no signal).
UNTONEMAPPED_MEAN_LUMA, UNTONEMAPPED_MAX_SAT = 160.0, 12.0
MIN_LUMA_STD = 3.0  # a solid frame (black, grey or blank)
CHUNK = 1024 * 1024
TERMINAL_JOB = {"done", "failed"}


# =========================================================================== clip generation


def _ffmpeg(args: list[str], label: str) -> None:
    from punchwalk.pipeline import media

    media.run_ffmpeg(args, label=label, timeout_s=1800)


def _graph(
    sources: list[Path], chain_for: Callable[[Path], str], tail: str, *, audio: bool, start_s: float = 0.0
) -> list[str]:
    """Inputs + -filter_complex that normalise every source with its own chain, then concat them.

    Not the concat demuxer: our glasses clips differ in resolution, and an HEVC stream that changes
    size mid-way through the demuxer decodes into frozen, corrupted frames.
    """
    from punchwalk.pipeline import media

    args: list[str] = []
    parts: list[str] = []
    labels: list[str] = []
    for i, src in enumerate(sources):
        args += [*(["-ss", f"{start_s:.3f}"] if start_s and i == 0 else []), "-i", str(src)]
        parts.append(f"[{i}:v:0]{chain_for(src)},fps={FPS},setpts=PTS-STARTPTS[v{i}]")
        labels.append(f"[v{i}]")
        if audio:
            info = media.probe(src)
            if info.has_audio:
                parts.append(
                    f"[{i}:a:0]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS[a{i}]"
                )
            else:
                dur = max(0.1, info.duration_s - (start_s if i == 0 else 0.0))
                parts.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={dur:.3f}[a{i}]")
            labels.append(f"[a{i}]")
    n = len(sources)
    parts.append(f"{''.join(labels)}concat=n={n}:v=1:a={1 if audio else 0}[vc]{'[ac]' if audio else ''}")
    parts.append(f"[vc]{tail}[vout]")
    maps = ["-map", "[vout]", *(["-map", "[ac]"] if audio else [])]
    return [*args, "-filter_complex", ";".join(parts), *maps]


def _crop_to(w: int, h: int) -> str:
    """Centre-crop the (display-oriented) input to the w:h aspect, then scale to w x h."""
    return f"crop='min(iw,ih*{w}/{h})':'min(ih,iw*{h}/{w})',scale={w}:{h}:flags=bicubic,setsar=1"


def _sdr_chain(source: Path, w: int, h: int) -> str:
    """Crop/scale + an HLG/PQ → BT.709 conversion when the source is HDR (the pipeline's own tonemap)."""
    from punchwalk.pipeline import ingest, media

    info = media.probe(source)
    crop = _crop_to(w, h)
    if not info.hdr:
        return f"{crop},scale=out_color_matrix=bt709:out_range=tv,format=yuv420p"
    method = next((m for m in media.hdr_methods_available() if m != "none"), "none")
    # _hdr_chain starts with a resize: crop first (in the signal domain), then let it do the conversion.
    chain = ingest._hdr_chain(info, method, w, h)
    return ",".join([f"crop='min(iw,ih*{w}/{h})':'min(ih,iw*{h}/{w})'", *chain, "setsar=1"])


def _audio_args(audio: bool) -> list[str]:
    return ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"] if audio else ["-an"]


def make_sdr_clip(
    sources: list[Path],
    out: Path,
    *,
    duration_s: float,
    size: tuple[int, int],
    audio: bool = True,
    start_s: float = 0.0,
) -> Path:
    """SDR H.264 yuv420p 30 fps clip (BT.709, tv range) of `size` from `sources` played in order."""
    out.parent.mkdir(parents=True, exist_ok=True)
    work = out.parent / ".work"
    work.mkdir(exist_ok=True)
    w, h = size
    graph = _graph(
        sources,
        lambda src: _sdr_chain(src, w, h),
        f"format=yuv420p,{SDR_SETPARAMS}",
        audio=audio,
        start_s=start_s,
    )
    args = [
        *graph,
        "-t",
        f"{duration_s:.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "21",
        "-pix_fmt",
        "yuv420p",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-colorspace",
        "bt709",
        "-color_range",
        "tv",
        "-r",
        str(FPS),
        *_audio_args(audio),
        "-map_metadata",
        "-1",
        "-movflags",
        "+faststart",
        str(out),
    ]
    _ffmpeg(args, f"sdr {out.name}")
    shutil.rmtree(work, ignore_errors=True)
    return out


HLG_TAGS = [
    "-color_primaries",
    "bt2020",
    "-color_trc",
    "arib-std-b67",
    "-colorspace",
    "bt2020nc",
    "-color_range",
    "tv",
]
X265_HLG = "log-level=error:colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc:range=limited"


def make_hdr_clip(
    sources: list[Path],
    out: Path,
    *,
    duration_s: float,
    rotate_meta: int = 0,
    size: tuple[int, int] = (1080, 1920),
) -> Path:
    """iPhone-like HDR clip: HEVC Main10 HLG BT.2020 (tv range), 30 fps, AAC.

    The HLG signal is kept as is (crop + scale in the signal domain, no colour conversion), so the result
    is still real HLG footage. `rotate_meta=90` stores the pixels landscape (`size` transposed) with a
    display matrix telling players to rotate 90° clockwise, the way an iPhone records portrait video.
    """
    if rotate_meta not in (0, 90):
        raise ValueError("rotate_meta must be 0 or 90")
    out.parent.mkdir(parents=True, exist_ok=True)
    work = out.parent / ".work"
    work.mkdir(exist_ok=True)
    w, h = size
    tail = ["transpose=cclock"] if rotate_meta == 90 else []  # stored sideways; the matrix turns it upright
    tail.append("format=yuv420p10le")
    encoded = work / f"{out.stem}.enc.mp4" if rotate_meta else out
    args = [
        *_graph(sources, lambda src: _crop_to(w, h), ",".join(tail), audio=True),
        "-t",
        f"{duration_s:.3f}",
        "-c:v",
        "libx265",
        "-preset",
        "faster",
        "-crf",
        "22",
        "-profile:v",
        "main10",
        "-pix_fmt",
        "yuv420p10le",
        "-tag:v",
        "hvc1",
        "-x265-params",
        X265_HLG,
        *HLG_TAGS,
        "-r",
        str(FPS),
        *_audio_args(True),
        "-map_metadata",
        "-1",
        "-movflags",
        "+faststart",
        str(encoded),
    ]
    _ffmpeg(args, f"hdr {out.name}")
    if rotate_meta:
        # -display_rotation is counter-clockwise: -90 means "rotate 90° clockwise to display".
        _ffmpeg(
            [
                "-display_rotation",
                str(-rotate_meta),
                "-i",
                str(encoded),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(out),
            ],
            f"rotate {out.name}",
        )
    shutil.rmtree(work, ignore_errors=True)
    return out


def link_beat2(footage: Path, out_dir: Path) -> list[Path]:
    """beat2/01_walk1_d.MOV, 02_walk1_e.MOV, … as symlinks (names keep the upload order)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    links = []
    for i, name in enumerate(BEAT2_CLIPS, start=1):
        src = (footage / name).resolve()
        if not src.is_file():
            raise FileNotFoundError(f"{src} is missing; beat 2 uses {', '.join(BEAT2_CLIPS)} from footage/.")
        link = out_dir / f"{i:02d}_{name}"
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(src)
        links.append(link)
    return links


def make_all_clips(footage: Path, out: Path, *, only: set[str] | None = None) -> dict[str, list[Path]]:
    def want(name: str) -> bool:
        return not only or name in only

    def src(*names: str) -> list[Path]:
        paths = [footage / n for n in names]
        missing = [str(p) for p in paths if not p.is_file()]
        if missing:
            raise FileNotFoundError(f"missing footage: {', '.join(missing)}")
        return paths

    made: dict[str, list[Path]] = {}
    if want("beat2"):
        made["beat2"] = link_beat2(footage, out / "beat2")
    if want("iphone_hdr_portrait"):
        made["iphone_hdr_portrait"] = [
            make_hdr_clip(src(*HDR_SOURCES), out / "iphone_hdr_portrait.mp4", duration_s=75.0)
        ]
    if want("iphone_hdr_rotated"):
        made["iphone_hdr_rotated"] = [
            make_hdr_clip(
                src(*HDR_ROTATED_SOURCES), out / "iphone_hdr_rotated.mp4", duration_s=30.0, rotate_meta=90
            )
        ]
    if want("five_seconds"):
        made["five_seconds"] = [
            make_sdr_clip(
                src(SDR_SOURCE), out / "five_seconds.mp4", duration_s=5.0, size=(1080, 1920), start_s=0.3
            )
        ]
    if want("silent"):
        made["silent"] = [
            make_sdr_clip(
                src(*SILENT_SOURCES), out / "silent.mp4", duration_s=20.0, size=(1080, 1920), audio=False
            )
        ]
    if want("landscape"):
        made["landscape"] = [
            make_sdr_clip(src(*LANDSCAPE_SOURCES), out / "landscape.mp4", duration_s=20.0, size=(1920, 1080))
        ]
    return made


def describe_clip(path: Path) -> dict[str, Any]:
    """The ffprobe facts the rehearsal cares about (also used by tests)."""
    from punchwalk.pipeline import media

    data = media.ffprobe_json(path)
    streams = data.get("streams") or []
    v = next(s for s in streams if s.get("codec_type") == "video")
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    info = media.probe(path)
    return {
        "file": path.name,
        "size_mb": round(path.stat().st_size / 1e6, 2),
        "duration_s": round(info.duration_s, 2),
        "codec": v.get("codec_name"),
        "pix_fmt": v.get("pix_fmt"),
        "stored": f"{v.get('width')}x{v.get('height')}",
        "display": f"{info.display_width}x{info.display_height}",
        "rotation": info.rotation,
        "fps": round(info.fps, 2),
        "color_transfer": v.get("color_transfer"),
        "color_primaries": v.get("color_primaries"),
        "color_space": v.get("color_space"),
        "color_range": v.get("color_range"),
        "hdr": info.hdr,
        "audio": a.get("codec_name") if a else None,
    }


# =========================================================================== upload run


class Log:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.t0 = time.monotonic()
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")

    def __call__(self, msg: str) -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')} +{time.monotonic() - self.t0:7.1f}s] {msg}"
        print(line, flush=True)
        if self.path:
            with self.path.open("a") as f:
                f.write(line + "\n")


def _content_type(path: Path) -> str:
    ext = path.suffix.lower()
    return {".mp4": "video/mp4", ".m4v": "video/x-m4v", ".mov": "video/quicktime"}.get(
        ext, mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    )


@dataclass
class MultipartBody:
    """A streamed multipart/form-data body: small fields, then one `clips` part per file, in order."""

    fields: dict[str, str]
    files: list[Path]
    boundary: str = field(default_factory=lambda: f"punchwalk-rehearsal-{uuid.uuid4().hex}")
    sent: int = 0

    def _field_part(self, name: str, value: str) -> bytes:
        return (
            f'--{self.boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        ).encode()

    def _file_head(self, path: Path) -> bytes:
        name = path.name.split("_", 1)[1] if path.name[:2].isdigit() and "_" in path.name else path.name
        return (
            f'--{self.boundary}\r\nContent-Disposition: form-data; name="clips"; filename="{name}"\r\n'
            f"Content-Type: {_content_type(path)}\r\n\r\n"
        ).encode()

    @property
    def closing(self) -> bytes:
        return f"--{self.boundary}--\r\n".encode()

    @property
    def content_type(self) -> str:
        return f"multipart/form-data; boundary={self.boundary}"

    def length(self) -> int:
        n = sum(len(self._field_part(k, v)) for k, v in self.fields.items())
        n += sum(len(self._file_head(p)) + p.stat().st_size + 2 for p in self.files)
        return n + len(self.closing)

    def __iter__(self) -> Iterator[bytes]:
        for k, v in self.fields.items():
            chunk = self._field_part(k, v)
            self.sent += len(chunk)
            yield chunk
        for p in self.files:
            head = self._file_head(p)
            self.sent += len(head)
            yield head
            with p.open("rb") as f:
                while chunk := f.read(CHUNK):
                    self.sent += len(chunk)
                    yield chunk
            self.sent += 2
            yield b"\r\n"
        self.sent += len(self.closing)
        yield self.closing


class Refused(Exception):
    def __init__(self, status: int, detail: str, retry_after: float | None = None) -> None:
        super().__init__(f"{status}: {detail}")
        self.status, self.detail, self.retry_after = status, detail, retry_after


def _detail(resp: Any) -> str:
    try:
        body = resp.json()
    except Exception:
        return resp.text[:300]
    return str(body.get("detail") or body.get("error") or body)[:300]


def create_project(client: Any, name: str) -> dict:
    resp = client.post("/api/projects", json={"name": name})
    if resp.status_code != 201:
        raise Refused(resp.status_code, _detail(resp))
    return resp.json()


def upload(
    client: Any, project_id: str, clips: list[Path], kind: str, rewalk_of: str | None, log: Log
) -> dict:
    fields = {"kind": kind}
    if rewalk_of:
        fields["rewalk_of"] = rewalk_of
    body = MultipartBody(fields, clips)
    total = body.length()
    log(f"upload: {len(clips)} clip(s), {total / 1e6:.1f} MB → /api/projects/{project_id}/walks")
    last_report = [time.monotonic()]

    def stream() -> Iterator[bytes]:
        for chunk in body:
            now = time.monotonic()
            if now - last_report[0] > 5:
                last_report[0] = now
                log(f"upload: {body.sent / 1e6:.1f} of {total / 1e6:.1f} MB sent")
            yield chunk

    resp = client.post(
        f"/api/projects/{project_id}/walks",
        content=stream(),
        headers={"Content-Type": body.content_type, "Content-Length": str(total)},
    )
    if resp.status_code != 201:
        retry = resp.headers.get("retry-after")
        raise Refused(resp.status_code, _detail(resp), float(retry) if retry else None)
    return resp.json()


def upload_with_slot(
    client: Any, args: argparse.Namespace, project_id: str, clips: list[Path], log: Log
) -> tuple[dict, float]:
    deadline = time.monotonic() + 3600
    while True:
        t0 = time.monotonic()
        try:
            out = upload(client, project_id, clips, args.kind, args.rewalk_of, log)
            return out, time.monotonic() - t0
        except Refused as exc:
            if exc.status != 429 or not args.wait_for_slot or time.monotonic() > deadline:
                raise
            wait = min(max(exc.retry_after or 120.0, 30.0), 900.0)
            log(f"upload refused (429: {exc.detail}); waiting {wait:.0f} s for the next slot")
            time.sleep(wait)


@dataclass
class StageTrack:
    status: str = "pending"
    first_running: float | None = None  # monotonic, seen by this script
    finished: float | None = None
    duration_ms: int | None = None  # the server's own duration


def docker_mem_mib(container: str) -> float | None:
    """Current memory use of a local container (docker stats), in MiB."""
    try:
        out = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", container],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        ).stdout.strip()
    except Exception:
        return None
    if not out:
        return None
    used = out.split("/")[0].strip()
    units = {
        "KiB": 1 / 1024,
        "MiB": 1.0,
        "GiB": 1024.0,
        "kB": 1 / 1024,
        "MB": 1.0,
        "GB": 1024.0,
        "B": 1 / 1024**2,
    }
    for unit, factor in units.items():
        if used.endswith(unit):
            try:
                return round(float(used[: -len(unit)]) * factor, 1)
            except ValueError:
                return None
    return None


def poll(
    client: Any, walk_id: str, args: argparse.Namespace, log: Log
) -> tuple[dict, dict[str, StageTrack], float | None]:
    tracks: dict[str, StageTrack] = {}
    deadline = time.monotonic() + args.timeout
    last_msg, peak_mem, last_mem_t = None, None, 0.0
    errors = 0
    while True:
        try:
            resp = client.get(f"/api/walks/{walk_id}/progress")
            resp.raise_for_status()
            prog = resp.json()
            errors = 0
        except Exception as exc:  # a restart or a blip: keep polling until the deadline
            errors += 1
            log(f"poll: {type(exc).__name__}: {exc} (attempt {errors})")
            if time.monotonic() > deadline:
                raise TimeoutError(f"no progress for walk {walk_id} within {args.timeout} s") from exc
            time.sleep(args.poll)
            continue
        now = time.monotonic()
        for row in prog.get("stages") or []:
            st = tracks.setdefault(row["stage"], StageTrack())
            if row["status"] != st.status:
                if row["status"] == "running" and st.first_running is None:
                    st.first_running = now
                if row["status"] in ("done", "skipped", "failed"):
                    st.finished = now
                extra = f" ({row['duration_ms'] / 1000:.1f} s)" if row.get("duration_ms") else ""
                log(f"stage {row['stage']}: {st.status} → {row['status']}{extra}  {row.get('detail') or ''}")
                st.status = row["status"]
            st.duration_ms = row.get("duration_ms") or st.duration_ms
        if prog.get("message") and prog["message"] != last_msg:
            last_msg = prog["message"]
            log(f"job: {prog['status']} {prog['progress']:.0%}  {last_msg}")
        if args.docker_stats and now - last_mem_t >= 6:
            last_mem_t = now
            mem = docker_mem_mib(args.docker_stats)
            if mem is not None:
                peak_mem = max(peak_mem or 0.0, mem)
        if prog["status"] in TERMINAL_JOB:
            return prog, tracks, peak_mem
        if prog.get("walk_status") == "failed" and prog["status"] != "running":
            return prog, tracks, peak_mem
        if now > deadline:
            raise TimeoutError(
                f"walk {walk_id} still {prog['status']} at stage {prog.get('stage')} after {args.timeout} s"
            )
        time.sleep(args.poll)


# --------------------------------------------------------------------------- colour checks


def luma_stats(img: Any) -> dict[str, float]:
    """Mean luma, spread and the clipped-highlight fraction of an RGB image (BT.709 weights, 0..255)."""
    import numpy as np

    rgb = np.asarray(img.convert("RGB"), dtype=np.float32)
    y = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    sat = rgb.max(axis=2) - rgb.min(axis=2)
    return {
        "mean_luma": round(float(y.mean()), 1),
        "std_luma": round(float(y.std()), 1),
        "p01": round(float(np.percentile(y, 1)), 1),
        "p99": round(float(np.percentile(y, 99)), 1),
        "clipped_frac": round(float((y >= CLIPPED_LUMA).mean()), 4),
        "mean_sat": round(float(sat.mean()), 1),
    }


def luma_problems(stats: dict[str, float]) -> list[str]:
    out = []
    if stats["clipped_frac"] >= MAX_CLIPPED_FRACTION:
        out.append(f"{stats['clipped_frac']:.0%} of pixels clipped (>= {MAX_CLIPPED_FRACTION:.0%})")
    if stats["mean_luma"] < MIN_MEAN_LUMA:
        out.append(f"too dark (mean luma {stats['mean_luma']})")
    if stats["mean_luma"] > MAX_MEAN_LUMA:
        out.append(f"washed out (mean luma {stats['mean_luma']})")
    if stats["mean_luma"] > UNTONEMAPPED_MEAN_LUMA and stats["mean_sat"] < UNTONEMAPPED_MAX_SAT:
        out.append(
            f"bright and grey (mean luma {stats['mean_luma']}, saturation {stats['mean_sat']}), "
            "like HLG shown without a tonemap"
        )
    if stats["std_luma"] < MIN_LUMA_STD:
        out.append(f"a solid frame (luma std {stats['std_luma']})")
    return out


def _abs(base: str, url: str) -> str:
    return url if url.startswith("http") else f"{base.rstrip('/')}/{url.lstrip('/')}"


def download(
    client: Any, base: str, url: str, dest: Path, headers: dict[str, str] | None = None
) -> tuple[Path, int]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = client.get(_abs(base, url), headers=headers or {})
    if resp.status_code not in (200, 206):
        raise Refused(resp.status_code, f"GET {url}: {_detail(resp)}")
    dest.write_bytes(resp.content)
    return dest, resp.status_code


def check_display(
    client: Any, base: str, url: str, frames_dir: Path, duration_s: float, log: Log
) -> dict[str, Any]:
    """Range-request the first ~2 s of display.mp4, probe it and sample two frames."""
    from PIL import Image

    head = client.head(_abs(base, url))
    size = int(head.headers.get("content-length") or 0)
    want = int(size * min(1.0, 3.0 / max(duration_s, 1.0))) + 512 * 1024 if size else 4 * 1024 * 1024
    want = min(want, size) if size else want
    part, status = download(
        client, base, url, frames_dir / "display_head.mp4", {"Range": f"bytes=0-{want - 1}"}
    )
    out: dict[str, Any] = {"range_status": status, "bytes": part.stat().st_size, "size": size, "frames": []}
    try:
        out["probe"] = describe_clip(part)
    except Exception as exc:
        out["probe_error"] = str(exc)
    for t in (0.5, 1.5):
        frame = frames_dir / f"display_{t:.1f}s.jpg"
        proc = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                str(t),
                "-i",
                str(part),
                "-frames:v",
                "1",
                "-q:v",
                "3",
                str(frame),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and frame.is_file():
            stats = luma_stats(Image.open(frame))
            out["frames"].append({"t": t, "file": frame.name, **stats, "problems": luma_problems(stats)})
        else:
            out["frames"].append({"t": t, "error": (proc.stderr or "no frame")[-300:]})
    log(
        f"display.mp4: HTTP {status}, {out['bytes'] / 1e6:.1f} of {size / 1e6:.1f} MB; "
        + "; ".join(
            f"{f['t']}s luma {f.get('mean_luma')} clipped {f.get('clipped_frac')}" for f in out["frames"]
        )
    )
    return out


def evidence_strip(paths: list[tuple[int, Path]], dest: Path, height: int = 420) -> Path | None:
    from PIL import Image, ImageDraw

    tiles = []
    for number, p in paths:
        try:
            im = Image.open(p).convert("RGB")
        except Exception:
            continue
        im = im.resize((max(1, round(im.width * height / im.height)), height))
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, 64, 30], fill=(90, 162, 234))
        d.text((8, 8), f"#{number:03d}", fill=(40, 40, 40))
        tiles.append(im)
    if not tiles:
        return None
    gap = 8
    strip = Image.new("RGB", (sum(t.width for t in tiles) + gap * (len(tiles) - 1), height), (20, 20, 20))
    x = 0
    for t in tiles:
        strip.paste(t, (x, 0))
        x += t.width + gap
    strip.save(dest, quality=85)
    return dest


# --------------------------------------------------------------------------- the run


def _summarise_stages(prog: dict, tracks: dict[str, StageTrack]) -> list[dict[str, Any]]:
    rows = []
    for row in prog.get("stages") or []:
        tr = tracks.get(row["stage"], StageTrack())
        rows.append(
            {
                "stage": row["stage"],
                "status": row["status"],
                "server_s": round((row.get("duration_ms") or 0) / 1000, 2),
                "seen_s": round(tr.finished - tr.first_running, 1)
                if tr.finished and tr.first_running
                else None,
                "detail": row.get("detail"),
            }
        )
    return rows


def run(args: argparse.Namespace) -> int:
    import httpx
    from PIL import Image

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    frames = out / "frames"
    log = Log(out / "run.log")
    clips = [Path(c) for c in args.clips]
    for c in clips:
        if not c.is_file():
            log(f"clip not found: {c}")
            return 1
    summary: dict[str, Any] = {
        "name": args.name or out.name.removeprefix("run-"),
        "base_url": args.base_url,
        "started_at": datetime.now(UTC).isoformat(),
        "clips": [],
        "kind": args.kind,
    }
    for c in clips:
        try:
            summary["clips"].append(describe_clip(c))
        except Exception as exc:
            summary["clips"].append({"file": c.name, "probe_error": str(exc)})
    summary["input_duration_s"] = round(sum(c.get("duration_s", 0) for c in summary["clips"]), 2)
    summary["input_mb"] = round(sum(c.stat().st_size for c in clips) / 1e6, 1)

    def finish(code: int, outcome: str) -> int:
        summary["outcome"] = outcome
        summary["exit_code"] = code
        summary["finished_at"] = datetime.now(UTC).isoformat()
        (out / "summary.json").write_text(json.dumps(summary, indent=2))
        print_table([summary])
        log(f"outcome: {outcome} (exit {code}); summary at {out / 'summary.json'}")
        return code

    timeout = httpx.Timeout(connect=15.0, read=600.0, write=600.0, pool=30.0)
    with httpx.Client(base_url=args.base_url, timeout=timeout, follow_redirects=True) as client:
        try:
            health = client.get("/healthz").json()
        except Exception as exc:
            summary["error"] = f"can't reach {args.base_url}: {exc}"
            return finish(6, "unreachable")
        summary["health"] = health
        log(
            f"server: {args.base_url} version {health.get('version')} demo_mode={health.get('demo_mode')} "
            f"reasoning={health.get('reasoning_provider')}"
        )
        if health.get("demo_mode") and not args.wait_for_slot:
            log("note: DEMO_MODE is on (3 uploads per hour per IP); pass --wait-for-slot to wait on a 429")

        try:
            if args.project == "new":
                name = f"Rehearsal {datetime.now().strftime('%b %d %H:%M:%S')}"
                project = create_project(client, name)
                log(f"project: created {project['id']} “{name}”")
            else:
                project = {"id": args.project}
            summary["project_id"] = project["id"]
            t_upload = time.monotonic()
            up, upload_s = upload_with_slot(client, args, project["id"], clips, log)
        except Refused as exc:
            summary["error"] = exc.detail
            summary["http_status"] = exc.status
            log(f"refused: {exc.status} {exc.detail}")
            return finish(5, "refused")
        except httpx.HTTPError as exc:
            summary["error"] = f"upload failed: {type(exc).__name__}: {exc}"
            log(summary["error"])
            return finish(5, "upload_error")
        walk_id = up["walk_id"]
        summary.update(walk_id=walk_id, job_id=up["job_id"], upload_s=round(upload_s, 2))
        summary["upload_mbps"] = round(summary["input_mb"] * 8 / max(upload_s, 1e-3), 1)
        log(
            f"uploaded in {upload_s:.1f} s → walk {walk_id} (job {up['job_id']}); polling every {args.poll} s"
        )

        t_proc = time.monotonic()
        try:
            prog, tracks, peak_mem = poll(client, walk_id, args, log)
        except TimeoutError as exc:
            summary["error"] = str(exc)
            summary["processing_s"] = round(time.monotonic() - t_proc, 1)
            return finish(3, "timeout")
        summary["processing_s"] = round(time.monotonic() - t_proc, 1)
        summary["total_s"] = round(time.monotonic() - t_upload, 1)
        summary["realtime_x"] = round(summary["processing_s"] / max(summary["input_duration_s"], 1e-3), 2)
        summary["peak_mem_mib"] = peak_mem
        summary["stages"] = _summarise_stages(prog, tracks)
        summary["job_status"] = prog["status"]
        summary["attempts"] = prog.get("attempts")

        try:
            calls = client.get(f"/api/walks/{walk_id}/model-calls").json().get("calls", [])
        except Exception:
            calls = []
        summary["calls"] = len(calls)
        summary["cost_usd"] = round(sum(c.get("est_cost_usd") or 0 for c in calls), 4)
        summary["cached_calls"] = sum(1 for c in calls if c.get("cached"))
        summary["failed_calls"] = [
            {"stage": c.get("stage"), "model": c.get("model"), "error": c.get("error")}
            for c in calls
            if not c.get("ok")
        ]
        summary["fallback_calls"] = sum(1 for c in calls if c.get("provider") == "meta-fallback")
        cost_by_stage: dict[str, float] = {}
        for c in calls:
            k = c.get("stage") or "other"
            cost_by_stage[k] = round(cost_by_stage.get(k, 0) + (c.get("est_cost_usd") or 0), 4)
        summary["cost_by_stage"] = cost_by_stage

        if prog["status"] == "failed" or prog.get("walk_status") == "failed":
            summary["error"] = prog.get("error") or "failed without a message"
            log(f"walk failed: {summary['error']}")
            return finish(2, "failed")

        detail = client.get(f"/api/walks/{walk_id}").json()
        (out / "walk.json").write_text(json.dumps(detail, indent=2))
        try:
            processing = client.get(f"/api/walks/{walk_id}/processing").json()
            (out / "processing.json").write_text(json.dumps(processing, indent=2))
            summary["models_used"] = processing.get("models_used")
            summary["processing_cost_usd"] = processing.get("total_cost_usd")
            summary["processing_total_s"] = round((processing.get("total_duration_ms") or 0) / 1000, 1)
        except Exception as exc:
            summary["processing_error"] = str(exc)
        walk = detail["walk"]
        items = detail.get("items") or []
        summary["display"] = {
            "width": walk.get("width"),
            "height": walk.get("height"),
            "fps": walk.get("fps"),
            "orientation": walk.get("orientation"),
            "device": walk.get("device"),
            "duration_s": walk.get("duration_s"),
        }
        summary["turns"] = len(detail.get("turns") or [])
        summary["points"] = len(detail.get("points") or [])
        summary["items"] = len(items)
        summary["needs_review"] = sum(1 for i in items if i.get("status") == "needs_review")
        summary["item_list"] = [
            {
                "number": i["number"],
                "title": i["title"],
                "trade": i["trade"],
                "status": i["status"],
                "grounding": (i.get("evidence") or {}).get("grounding"),
                "consistent": (i.get("verification") or {}).get("consistent"),
            }
            for i in items
        ]
        problems: list[str] = []

        media = walk.get("media") or {}
        if media.get("poster"):
            p, _ = download(client, args.base_url, media["poster"], frames / "poster.jpg")
            stats = luma_stats(Image.open(p))
            summary["poster"] = {**stats, "problems": luma_problems(stats), "size": Image.open(p).size}
            problems += [f"poster: {x}" for x in summary["poster"]["problems"]]
        else:
            problems.append("no poster")
        if media.get("display_mp4"):
            disp = check_display(
                client, args.base_url, media["display_mp4"], frames, walk.get("duration_s") or 10, log
            )
            summary["display_check"] = disp
            if disp["range_status"] != 206:
                problems.append(f"display.mp4 Range request answered {disp['range_status']}, not 206")
            pr = disp.get("probe") or {}
            if pr and (
                pr.get("codec") != "h264"
                or pr.get("color_transfer") not in (None, "bt709")
                or pr.get("rotation")
            ):
                problems.append(
                    f"display.mp4 is {pr.get('codec')} trc={pr.get('color_transfer')} rot={pr.get('rotation')}"
                )
            for f in disp["frames"]:
                problems += [f"display {f['t']}s: {x}" for x in f.get("problems", [])]
        else:
            problems.append("no display.mp4")
        ev_paths = []
        ev_stats = []
        for it in items:
            ev = it.get("evidence") or {}
            if not ev.get("image_url"):
                continue
            p, _ = download(
                client, args.base_url, ev["image_url"], frames / f"evidence_{it['number']:03d}.jpg"
            )
            stats = luma_stats(Image.open(p))
            ev_stats.append({"number": it["number"], **stats, "problems": luma_problems(stats)})
            problems += [f"evidence #{it['number']:03d}: {x}" for x in luma_problems(stats)]
            ev_paths.append((it["number"], p))
        summary["evidence"] = ev_stats
        strip = evidence_strip(ev_paths, out / "evidence_strip.jpg")
        summary["evidence_strip"] = str(strip) if strip else None
        # the input's orientation must survive (never letterboxed into the other one)
        first = summary["clips"][0] if summary["clips"] else {}
        if first.get("display") and walk.get("width") and walk.get("height"):
            iw, ih = (int(x) for x in first["display"].split("x"))
            if (iw > ih) != (walk["width"] > walk["height"]):
                problems.append(
                    f"orientation changed: input {first['display']}, display {walk['width']}x{walk['height']}"
                )
        summary["problems"] = problems
        if problems:
            for p in problems:
                log(f"check failed: {p}")
            return finish(4, "ready_with_problems")
        return finish(0, "ready")


# --------------------------------------------------------------------------- table


STAGE_COLS = (
    "ingest",
    "transcribe",
    "classify_turns",
    "track_hands",
    "find_points",
    "fuse",
    "pick_evidence",
    "write_items",
    "ground_defects",
    "verify",
    "resolve_closures",
    "persist",
)
SHORT = {
    "classify_turns": "classify",
    "track_hands": "hands",
    "find_points": "points",
    "pick_evidence": "evid",
    "write_items": "write",
    "ground_defects": "ground",
    "resolve_closures": "closures",
    "transcribe": "asr",
}


def print_table(summaries: list[dict[str, Any]]) -> None:
    present = [
        s for s in STAGE_COLS if any(any(r["stage"] == s for r in x.get("stages") or []) for x in summaries)
    ]
    cols = [
        "run",
        "in s",
        "MB",
        "upload",
        *[SHORT.get(s, s) for s in present],
        "proc",
        "total",
        "x RT",
        "items",
        "$",
        "mem",
        "outcome",
    ]
    rows = []
    for x in summaries:
        by = {r["stage"]: r for r in x.get("stages") or []}
        rows.append(
            [
                x.get("name", "?"),
                f"{x.get('input_duration_s', 0):.0f}",
                f"{x.get('input_mb', 0):.0f}",
                f"{x.get('upload_s', 0):.1f}",
                *[
                    (f"{by[s]['server_s']:.1f}" if s in by and by[s]["status"] != "pending" else "-")
                    for s in present
                ],
                f"{x.get('processing_s', 0):.0f}",
                f"{x.get('total_s', 0):.0f}",
                f"{x.get('realtime_x', 0):.1f}",
                str(x.get("items", "-")),
                f"{x.get('cost_usd', 0):.3f}",
                f"{x['peak_mem_mib']:.0f}" if x.get("peak_mem_mib") else "-",
                x.get("outcome", "?"),
            ]
        )
    widths = [max(len(c), *(len(r[i]) for r in rows)) for i, c in enumerate(cols)]
    print(" | ".join(c.ljust(w) for c, w in zip(cols, widths, strict=True)))
    print("-+-".join("-" * w for w in widths))
    for r in rows:
        print(" | ".join(v.ljust(w) for v, w in zip(r, widths, strict=True)))


def recheck(run_dir: Path) -> int:
    """Re-score a finished run's saved frames with the current colour thresholds (no server needed)."""
    from PIL import Image

    path = run_dir / "summary.json"
    summary = json.loads(path.read_text())
    if summary.get("outcome") not in ("ready", "ready_with_problems"):
        return int(summary.get("exit_code", 1))
    frames = run_dir / "frames"
    keep = [p for p in summary.get("problems", []) if not p.startswith(("poster:", "display ", "evidence #"))]
    problems = list(keep)
    if (frames / "poster.jpg").is_file():
        stats = luma_stats(Image.open(frames / "poster.jpg"))
        summary["poster"] = {**summary.get("poster", {}), **stats, "problems": luma_problems(stats)}
        problems += [f"poster: {x}" for x in summary["poster"]["problems"]]
    for f in (summary.get("display_check") or {}).get("frames", []):
        if f.get("file") and (frames / f["file"]).is_file():
            f.update(luma_stats(Image.open(frames / f["file"])))
            f["problems"] = luma_problems(f)
            problems += [f"display {f['t']}s: {x}" for x in f["problems"]]
    for ev in summary.get("evidence", []):
        img = frames / f"evidence_{ev['number']:03d}.jpg"
        if img.is_file():
            ev.update(luma_stats(Image.open(img)))
            ev["problems"] = luma_problems(ev)
            problems += [f"evidence #{ev['number']:03d}: {x}" for x in ev["problems"]]
    summary["problems"] = problems
    summary["outcome"], summary["exit_code"] = ("ready_with_problems", 4) if problems else ("ready", 0)
    path.write_text(json.dumps(summary, indent=2))
    print(f"{run_dir.name}: {summary['outcome']}" + "".join(f"\n  {p}" for p in problems))
    return summary["exit_code"]


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "make-clips":
        p = argparse.ArgumentParser(prog="rehearsal.py make-clips")
        p.add_argument("--footage", default=str(REPO_ROOT / "footage"))
        p.add_argument("--out", default=str(REPO_ROOT / ".verify" / "rehearsal" / "clips"))
        p.add_argument(
            "--only",
            nargs="*",
            help="beat2 iphone_hdr_portrait iphone_hdr_rotated five_seconds silent landscape",
        )
        a = p.parse_args(argv[1:])
        made = make_all_clips(Path(a.footage), Path(a.out), only=set(a.only) if a.only else None)
        for name, paths in made.items():
            for path in paths:
                print(name, json.dumps(describe_clip(path)))
        return 0
    if argv and argv[0] == "recheck":
        return max([recheck(Path(d)) for d in argv[1:]] or [0])
    if argv and argv[0] == "table":
        files = [Path(f) for f in argv[1:]]
        print_table([json.loads(f.read_text()) for f in files if f.is_file()])
        return 0
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--base-url", required=True, help="e.g. http://localhost:8090 or https://punchwalk.fly.dev"
    )
    p.add_argument(
        "--project", default="new", help="a project id, or 'new' for a fresh 'Rehearsal <time>' project"
    )
    p.add_argument("--clips", nargs="+", required=True, help="clip files in walk order")
    p.add_argument("--kind", choices=["initial", "rewalk"], default="initial")
    p.add_argument("--rewalk-of", default=None, help="the first walk a re-walk checks (default: the latest)")
    p.add_argument("--timeout", type=float, default=900.0, help="seconds to wait for processing")
    p.add_argument("--poll", type=float, default=2.0)
    p.add_argument("--out", required=True, help="output folder, e.g. .verify/rehearsal/run-beat2")
    p.add_argument("--name", default=None, help="row name in the table (default: --out without 'run-')")
    p.add_argument(
        "--wait-for-slot", action="store_true", help="on a DEMO_MODE 429, wait and retry (up to 1 h)"
    )
    p.add_argument(
        "--docker-stats", metavar="CONTAINER", default=None, help="sample a local container's memory"
    )
    args = p.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
