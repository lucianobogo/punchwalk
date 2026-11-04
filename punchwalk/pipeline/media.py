"""ffprobe / ffmpeg helpers shared by the pipeline stages.

Everything here shells out to the system `ffmpeg` / `ffprobe` (CLAUDE.md "Stack"). Commands are logged
with their timing, and failures raise `MediaError` carrying the tail of ffmpeg's stderr so the message
is actionable.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shlex
import shutil
import subprocess
import time
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

log = logging.getLogger(__name__)

HDR_TRANSFERS = frozenset({"smpte2084", "arib-std-b67"})
HDR_PRIMARIES = frozenset({"bt2020"})
STDERR_TAIL_LINES = 25
DEFAULT_TIMEOUT_S = 1800.0


class MediaError(RuntimeError):
    """An ffmpeg/ffprobe call failed or a media file is unusable."""


class ClipInfo(BaseModel):
    """What ffprobe tells us about one source clip."""

    path: str
    filename: str
    size_bytes: int
    duration_s: float
    codec: str
    width: int  # coded width (before rotation)
    height: int  # coded height (before rotation)
    rotation: int  # clockwise degrees a player applies to display the clip: 0 | 90 | 180 | 270
    display_width: int  # width after rotation
    display_height: int  # height after rotation
    fps: float
    pix_fmt: str | None
    color_transfer: str | None
    color_primaries: str | None
    color_space: str | None
    color_range: str | None
    hdr: bool
    has_audio: bool
    audio_codec: str | None
    audio_sample_rate: int | None
    audio_channels: int | None

    @property
    def orientation(self) -> str:
        return orientation_of(self.display_width, self.display_height)


# --------------------------------------------------------------------------- binaries and capabilities


def _binary(name: str) -> str:
    env_override = os.environ.get(f"PUNCHWALK_{name.upper()}")
    found = env_override or shutil.which(name)
    if not found:
        raise MediaError(f"`{name}` was not found on PATH. Install ffmpeg (e.g. `brew install ffmpeg`).")
    return found


def ffmpeg_bin() -> str:
    return _binary("ffmpeg")


def ffprobe_bin() -> str:
    return _binary("ffprobe")


@lru_cache(maxsize=1)
def tool_versions() -> dict[str, str]:
    """First line of `ffmpeg -version` / `ffprobe -version`, e.g. 'ffmpeg version 9.0.2'."""
    versions: dict[str, str] = {}
    for name, binary in (("ffmpeg", ffmpeg_bin()), ("ffprobe", ffprobe_bin())):
        out = subprocess.run(
            [binary, "-hide_banner", "-version"], capture_output=True, text=True, check=False
        )
        first = (out.stdout or "").splitlines()[:1]
        versions[name] = " ".join(first[0].split()[:3]) if first else "unknown"
    return versions


@lru_cache(maxsize=1)
def ffmpeg_filters() -> frozenset[str]:
    out = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-filters"], capture_output=True, text=True, check=False
    ).stdout
    names = set()
    for line in out.splitlines():
        parts = line.split()
        # lines look like: " TS colorspace        V->V       Convert between colorspaces."
        if len(parts) >= 3 and "->" in parts[2]:
            names.add(parts[1])
    return frozenset(names)


@lru_cache(maxsize=1)
def scale_has_color_management() -> bool:
    """True when the `scale` filter can convert transfer/primaries (newer swscale, ffmpeg >= 8)."""
    out = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-h", "filter=scale"], capture_output=True, text=True, check=False
    ).stdout
    return "out_transfer" in out and "out_primaries" in out


def hdr_methods_available() -> list[str]:
    """HDR→SDR methods this ffmpeg supports, best first. 'none' (plain matrix conversion) is always last.

    - "zscale": zimg linearize → BT.709 gamut → hable tonemap → BT.709 (the Docker image's ffmpeg 7.1).
    - "lut3d": the same conversion baked into a 3D LUT (`hdr_lut_file`) for ffmpeg's built-in lut3d
      filter; for builds without zscale (Homebrew ffmpeg 9). Matches zscale within ~2/255 on real
      HLG footage.
    - "swscale": newer swscale color management with perceptual intent (ffmpeg >= 8): a last resort,
      ~10% brighter than the reference. Never relative colorimetric: that clips everything above
      HLG/PQ reference white and washes the picture out.
    """
    methods: list[str] = []
    filters = ffmpeg_filters()
    if {"zscale", "tonemap"} <= filters:
        methods.append("zscale")
    if "lut3d" in filters:
        methods.append("lut3d")
    if scale_has_color_management():
        methods.append("swscale")
    methods.append("none")
    return methods


# --------------------------------------------------------------------------- HDR → SDR math
#
# One reference conversion for every path, measured against zscale (Docker) and Apple's AVFoundation
# decode of the same Ray-Ban Meta HLG frames (DECISIONS.md, COLOR-FIX):
#   decode (BT.2020 non-constant-luminance matrix, source range) → HDR RGB signal
#   → display light: HLG inverse OETF + OOTF (gamma 1.2 per channel, 1000 nit peak) | PQ EOTF; 1.0 = 203 nits
#     (BT.2408 HDR reference white, zscale npl=203)
#   → BT.2020 → BT.709 primaries (linear) → hable on max(R,G,B), normalized at peak 10 (ffmpeg tonemap)
#   → BT.1886 inverse EOTF (gamma 1/2.4, what zimg's "bt709" means for display light) → BT.709 limited.

HDR_REFERENCE_WHITE_NITS = 203.0
HDR_TONEMAP_PEAK = 10.0
HLG_PEAK_NITS = 1000.0
LUT_SIZE = 65
LUT_VERSION = 2  # bump when the math below changes (LUT files are cached by content hash anyway)
_HLG_A, _HLG_B, _HLG_C = 0.17883277, 0.28466892, 0.55991073
_BT2020_TO_BT709 = (
    (1.6604910021, -0.5876411388, -0.0728498633),
    (-0.1245504745, 1.1328998971, -0.0083494226),
    (-0.0181507634, -0.1005788980, 1.1187296614),
)


def hdr_kind(transfer: str | None, primaries: str | None) -> str | None:
    """'hlg' | 'pq' | 'wcg' (BT.2020 primaries, SDR transfer) | None (plain SDR)."""
    if transfer == "arib-std-b67":
        return "hlg"
    if transfer == "smpte2084":
        return "pq"
    if primaries in HDR_PRIMARIES:
        return "wcg"
    return None


def _hable(x):
    a, b, c, d, e, f = 0.15, 0.50, 0.10, 0.20, 0.02, 0.30
    return (x * (x * a + b * c) + d * e) / (x * (x * a + b) + d * f) - e / f


def hdr_to_sdr_rgb(rgb, kind: str, *, bt2020_primaries: bool = True):
    """Reference HDR → SDR conversion of non-linear RGB signal values in [0, 1] (array [..., 3]).

    Returns BT.709 / BT.1886 non-linear RGB in [0, 1]. `kind` is 'hlg', 'pq' or 'wcg' (`hdr_kind`).
    """
    import numpy as np

    x = np.clip(np.asarray(rgb, dtype=np.float64), 0.0, 1.0)
    if kind == "hlg":
        scene = np.where(x <= 0.5, x * x / 3.0, (np.exp((x - _HLG_C) / _HLG_A) + _HLG_B) / 12.0)
        # OOTF gamma 1.2 per channel, as zimg (zscale) applies it, so Mac and Docker outputs match; BT.2100's
        # luminance-based OOTF is ~1-2 chroma levels less saturated and no closer to Apple's decode.
        light = np.power(scene, 1.2) * (HLG_PEAK_NITS / HDR_REFERENCE_WHITE_NITS)
    elif kind == "pq":
        m1, m2 = 2610 / 16384, 2523 / 4096 * 128
        c1, c2, c3 = 3424 / 4096, 2413 / 4096 * 32, 2392 / 4096 * 32
        p = np.power(x, 1 / m2)
        light = np.power(np.maximum(p - c1, 0.0) / (c2 - c3 * p), 1 / m1) * (10000.0 / HDR_REFERENCE_WHITE_NITS)
    elif kind == "wcg":
        light = np.power(x, 2.4)
    else:
        raise ValueError(f"unknown HDR kind {kind!r}; expected hlg, pq or wcg")
    if bt2020_primaries:
        light = np.maximum(light @ np.array(_BT2020_TO_BT709).T, 0.0)
    if kind != "wcg":
        sig = np.maximum(light.max(axis=-1), 1e-6)
        light = light * (_hable(sig) / _hable(HDR_TONEMAP_PEAK) / sig)[..., None]
    return np.power(np.clip(light, 0.0, 1.0), 1 / 2.4)


def _safe_filter_path(path: Path) -> bool:
    """True when `path` can go into an ffmpeg filter argument without escaping."""
    return all(ch.isalnum() or ch in "/._-" for ch in str(path))


def hdr_lut_file(kind: str, *, bt2020_primaries: bool = True, size: int = LUT_SIZE) -> Path:
    """A .cube 3D LUT of `hdr_to_sdr_rgb` for ffmpeg's lut3d filter, written once to the temp dir."""
    import hashlib
    import tempfile

    import numpy as np

    tag = f"v{LUT_VERSION}-{kind}-{'2020' if bt2020_primaries else '709'}-{size}"
    tag += f"-{HDR_REFERENCE_WHITE_NITS:g}-{HDR_TONEMAP_PEAK:g}-{HLG_PEAK_NITS:g}"
    digest = hashlib.sha256(tag.encode()).hexdigest()[:12]
    base = Path(os.environ.get("PUNCHWALK_LUT_DIR") or Path(tempfile.gettempdir()) / "punchwalk-luts")
    path = base / f"hdr_to_sdr_{kind}_{digest}.cube"
    if not _safe_filter_path(path):
        raise MediaError(f"LUT path {path} has characters ffmpeg filters can't take; set PUNCHWALK_LUT_DIR")
    if path.is_file() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    v = np.linspace(0.0, 1.0, size)
    b, g, r = np.meshgrid(v, v, v, indexing="ij")  # .cube order: red changes fastest
    out = hdr_to_sdr_rgb(np.stack([r, g, b], axis=-1).reshape(-1, 3), kind, bt2020_primaries=bt2020_primaries)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w") as fh:
        fh.write(f"# punchwalk HDR->SDR {tag}\nLUT_3D_SIZE {size}\n")
        np.savetxt(fh, out, fmt="%.6f")
    os.replace(tmp, path)
    return path


# --------------------------------------------------------------------------- running commands


def stderr_tail(stderr: str, lines: int = STDERR_TAIL_LINES) -> str:
    return "\n".join((stderr or "").strip().splitlines()[-lines:])


def run_ffmpeg(args: list[str], *, label: str, timeout_s: float = DEFAULT_TIMEOUT_S) -> float:
    """Run ffmpeg with `args` (without the binary). Returns elapsed seconds; raises MediaError on failure."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args]
    log.info("ffmpeg[%s]: %s", label, shlex.join(cmd))
    start = time.monotonic()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"ffmpeg[{label}] timed out after {timeout_s:.0f}s") from exc
    elapsed = time.monotonic() - start
    if proc.returncode != 0:
        raise MediaError(
            f"ffmpeg[{label}] failed (exit {proc.returncode}) after {elapsed:.1f}s.\n"
            f"command: {shlex.join(cmd)}\nstderr (tail):\n{stderr_tail(proc.stderr)}"
        )
    log.info("ffmpeg[%s]: done in %.2fs", label, elapsed)
    return elapsed


def ffprobe_json(path: Path, extra_args: list[str] | None = None) -> dict:
    """Run ffprobe on `path` and return its JSON (streams + format by default)."""
    path = Path(path)
    if not path.exists():
        raise MediaError(f"File not found: {path}")
    cmd = [
        ffprobe_bin(),
        "-v",
        "error",
        "-of",
        "json",
        *(extra_args if extra_args is not None else ["-show_streams", "-show_format"]),
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise MediaError(f"ffprobe could not read {path.name}: {stderr_tail(proc.stderr) or 'no output'}")
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise MediaError(f"ffprobe returned invalid JSON for {path.name}") from exc


# --------------------------------------------------------------------------- probing


def parse_rate(rate: str | None) -> float:
    """'30000/1001' → 29.97; '0/0' or None → 0.0."""
    if not rate:
        return 0.0
    try:
        frac = Fraction(rate)
    except (ValueError, ZeroDivisionError):
        return 0.0
    return float(frac) if frac > 0 else 0.0


def parse_rotation(stream: dict) -> int:
    """Clockwise display rotation (0/90/180/270) from the display matrix side data or the legacy `rotate` tag.

    ffprobe's display-matrix `rotation` is counterclockwise (an iPhone portrait clip reports -90);
    the legacy `rotate` tag is clockwise (the same clip says "90").
    """
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            try:
                ccw = float(side["rotation"])
            except (TypeError, ValueError):
                continue
            return _snap_rotation(-ccw)
    tag = (stream.get("tags") or {}).get("rotate")
    if tag is not None:
        try:
            return _snap_rotation(float(tag))
        except ValueError:
            pass
    return 0


def _snap_rotation(cw_degrees: float) -> int:
    return int(round(cw_degrees / 90.0)) * 90 % 360


def is_hdr(transfer: str | None, primaries: str | None) -> bool:
    return (transfer or "") in HDR_TRANSFERS or (primaries or "") in HDR_PRIMARIES


def orientation_of(width: int, height: int) -> str:
    return "portrait" if height > width else "landscape"


def probe(path: Path) -> ClipInfo:
    """Probe one clip: codec, size, rotation, display size, fps, HDR, audio."""
    path = Path(path)
    data = ffprobe_json(path)
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video" and not _is_cover_art(s)), None)
    if video is None:
        raise MediaError(f"{path.name} has no video stream")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if width <= 0 or height <= 0:
        raise MediaError(f"{path.name}: could not read the video size")
    rotation = parse_rotation(video)
    disp_w, disp_h = (height, width) if rotation in (90, 270) else (width, height)

    fps = parse_rate(video.get("avg_frame_rate")) or parse_rate(video.get("r_frame_rate"))
    duration = _float(video.get("duration")) or _float((data.get("format") or {}).get("duration"))
    if duration <= 0:
        raise MediaError(f"{path.name}: could not read the duration")

    transfer = video.get("color_transfer")
    primaries = video.get("color_primaries")
    return ClipInfo(
        path=str(path),
        filename=path.name,
        size_bytes=path.stat().st_size,
        duration_s=duration,
        codec=str(video.get("codec_name") or "unknown"),
        width=width,
        height=height,
        rotation=rotation,
        display_width=disp_w,
        display_height=disp_h,
        fps=fps or 30.0,
        pix_fmt=video.get("pix_fmt"),
        color_transfer=transfer,
        color_primaries=primaries,
        color_space=video.get("color_space"),
        color_range=video.get("color_range"),
        hdr=is_hdr(transfer, primaries),
        has_audio=audio is not None,
        audio_codec=audio.get("codec_name") if audio else None,
        audio_sample_rate=int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
        audio_channels=int(audio["channels"]) if audio and audio.get("channels") else None,
    )


def _is_cover_art(stream: dict) -> bool:
    return bool((stream.get("disposition") or {}).get("attached_pic"))


def _float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def count_frames(path: Path, stream: str = "v:0") -> int:
    """Number of packets (= frames for our H.264 outputs) in a stream, without decoding."""
    data = ffprobe_json(
        path, ["-select_streams", stream, "-count_packets", "-show_entries", "stream=nb_read_packets"]
    )
    streams = data.get("streams") or []
    if not streams:
        raise MediaError(f"{Path(path).name} has no stream {stream}")
    return int(streams[0].get("nb_read_packets") or 0)


def keyframe_times(path: Path) -> list[float]:
    """Presentation times of the video keyframes (decodes only keyframes)."""
    data = ffprobe_json(
        path,
        ["-select_streams", "v:0", "-skip_frame", "nokey", "-show_entries", "frame=pts_time"],
    )
    return [float(f["pts_time"]) for f in data.get("frames") or [] if f.get("pts_time") not in (None, "N/A")]


def is_faststart(path: Path) -> bool:
    """True when the MP4's `moov` atom comes before `mdat` (playback can start before the download ends)."""
    order: list[bytes] = []
    with open(path, "rb") as fh:
        size_total = os.fstat(fh.fileno()).st_size
        pos = 0
        while pos + 8 <= size_total and len(order) < 64:
            fh.seek(pos)
            header = fh.read(8)
            size = int.from_bytes(header[:4], "big")
            kind = header[4:8]
            if size == 1:
                size = int.from_bytes(fh.read(8), "big")
            elif size == 0:
                size = size_total - pos
            order.append(kind)
            if size < 8:
                break
            pos += size
    if b"moov" not in order or b"mdat" not in order:
        return False
    return order.index(b"moov") < order.index(b"mdat")


# --------------------------------------------------------------------------- geometry


def even(value: float) -> int:
    """Round half-up to the nearest even integer ≥ 2 (H.264 yuv420p needs even dimensions)."""
    return max(2, math.floor(value / 2.0 + 0.5) * 2)


def fit_short_side(width: int, height: int, short_side: int, *, allow_upscale: bool) -> tuple[int, int]:
    """Scale (width, height) so the short side is `short_side` (or ≤ it), keeping the aspect, even dims."""
    current = min(width, height)
    factor = short_side / current
    if not allow_upscale:
        factor = min(1.0, factor)
    if factor == 1.0 and width % 2 == 0 and height % 2 == 0:
        return width, height
    return even(width * factor), even(height * factor)


def fit_within(width: int, height: int, box_w: int, box_h: int) -> tuple[int, int]:
    """Largest even size with the source aspect that fits inside box_w × box_h."""
    factor = min(box_w / width, box_h / height)
    w, h = even(width * factor), even(height * factor)
    return min(w, box_w), min(h, box_h)


# --------------------------------------------------------------------------- frames


def extract_frame(video: Path, t_s: float, out_path: Path, *, quality: int = 2) -> Path:
    """Write the frame at `t_s` seconds to `out_path` (.jpg or .png), at the video's own resolution."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    args = ["-ss", f"{max(0.0, t_s):.3f}", "-i", str(video), "-frames:v", "1"]
    if out_path.suffix.lower() in (".jpg", ".jpeg"):
        args += ["-q:v", str(quality)]
    run_ffmpeg([*args, str(out_path)], label=f"frame@{t_s:.2f}")
    return out_path


def read_rgb_pixel(video: Path, x: int, y: int, t_s: float = 0.0) -> tuple[int, int, int]:
    """RGB of one pixel of the frame at `t_s` (for tests and sanity checks)."""
    cmd = [
        ffmpeg_bin(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{t_s:.3f}",
        "-i",
        str(video),
        "-frames:v",
        "1",
        "-vf",
        f"crop=1:1:{x}:{y},format=rgb24",
        "-f",
        "rawvideo",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0 or len(proc.stdout) < 3:
        raise MediaError(
            f"could not read pixel ({x},{y}) of {Path(video).name}: {stderr_tail(proc.stderr.decode())}"
        )
    r, g, b = proc.stdout[:3]
    return r, g, b
