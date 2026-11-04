"""scripts/rehearsal.py (PLAN 10.9): the test-clip generator's ffprobe properties, the streamed multipart
body and the colour checks. Offline: synthetic sources from scripts/synth_clip.py, no footage, no server."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
    pytest.skip("ffmpeg/ffprobe not installed", allow_module_level=True)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


synth = _load("synth_clip")
rh = _load("rehearsal")


def _has_encoder(name: str) -> bool:
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return f" {name} " in out


@pytest.fixture(scope="module")
def sources(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("src")
    out = {"sdr": synth.make_clip(d / "sdr.mp4", 2.0, portrait=True, audio="tone", size=(360, 640))}
    if _has_encoder("libx265"):
        out["hdr"] = synth.make_clip(
            d / "hdr.mp4", 2.0, portrait=True, hdr=True, audio="tone", size=(360, 640)
        )
    return out


def test_sdr_portrait_clip(tmp_path, sources):
    out = rh.make_sdr_clip([sources["sdr"]], tmp_path / "five.mp4", duration_s=1.5, size=(540, 960))
    d = rh.describe_clip(out)
    assert (d["codec"], d["pix_fmt"], d["display"], d["rotation"]) == ("h264", "yuv420p", "540x960", 0)
    assert d["color_transfer"] == "bt709" and d["hdr"] is False
    assert d["audio"] == "aac" and d["fps"] == 30 and abs(d["duration_s"] - 1.5) < 0.15


def test_silent_clip_has_no_audio_stream(tmp_path, sources):
    out = rh.make_sdr_clip(
        [sources["sdr"]], tmp_path / "silent.mp4", duration_s=1.0, size=(540, 960), audio=False
    )
    assert rh.describe_clip(out)["audio"] is None


def test_landscape_clip_from_two_sources(tmp_path, sources):
    out = rh.make_sdr_clip(
        [sources["sdr"], sources["sdr"]], tmp_path / "land.mp4", duration_s=3.0, size=(640, 360)
    )
    d = rh.describe_clip(out)
    assert d["display"] == "640x360" and d["audio"] == "aac"
    assert d["duration_s"] > 2.5  # both sources played back to back


@pytest.mark.parametrize("rotate", [0, 90])
def test_hdr_clip_is_iphone_like(tmp_path, sources, rotate):
    if "hdr" not in sources:
        pytest.skip("needs libx265")
    out = rh.make_hdr_clip(
        [sources["hdr"]], tmp_path / f"hdr{rotate}.mp4", duration_s=1.0, rotate_meta=rotate, size=(360, 640)
    )
    d = rh.describe_clip(out)
    assert (d["codec"], d["pix_fmt"]) == ("hevc", "yuv420p10le")
    assert (d["color_transfer"], d["color_primaries"], d["color_space"], d["color_range"]) == (
        "arib-std-b67",
        "bt2020",
        "bt2020nc",
        "tv",
    )
    assert d["hdr"] is True and d["audio"] == "aac" and d["fps"] == 30
    assert d["display"] == "360x640"  # upright portrait either way
    assert d["stored"] == ("640x360" if rotate else "360x640")
    assert d["rotation"] == rotate


def test_multipart_body_length_matches_stream(tmp_path):
    a, b = tmp_path / "01_walk1_d.MOV", tmp_path / "b.mp4"
    a.write_bytes(b"x" * 3_000_001)
    b.write_bytes(b"y" * 17)
    body = rh.MultipartBody({"kind": "initial"}, [a, b])
    data = b"".join(body)
    assert len(data) == body.length() == body.sent
    assert b'filename="walk1_d.MOV"' in data  # the order prefix is dropped
    assert b"Content-Type: video/quicktime" in data and b"Content-Type: video/mp4" in data
    assert data.endswith(f"--{body.boundary}--\r\n".encode())


def test_luma_checks():
    ok = Image.linear_gradient("L").resize((64, 64)).convert("RGB")
    assert rh.luma_problems(rh.luma_stats(ok)) == []
    blown = Image.new("RGB", (64, 64), (255, 255, 255))
    assert any("clipped" in p for p in rh.luma_problems(rh.luma_stats(blown)))
    solid = Image.new("RGB", (64, 64), (128, 128, 128))
    assert any("solid" in p for p in rh.luma_problems(rh.luma_stats(solid)))
    # HLG without a tonemap: bright and nearly colourless, with normal contrast
    grey = Image.linear_gradient("L").resize((64, 64)).point(lambda v: 120 + v // 2).convert("RGB")
    assert any("without a tonemap" in p for p in rh.luma_problems(rh.luma_stats(grey)))
    # a plain beige wall close up: low contrast but saturated, which is fine
    wall = Image.linear_gradient("L").resize((64, 64)).point(lambda v: 140 + v // 10)
    beige = Image.merge("RGB", (wall, wall.point(lambda v: v - 8), wall.point(lambda v: v - 22)))
    assert rh.luma_problems(rh.luma_stats(beige)) == []


def _solid(path: Path, color: str, size: str, seconds: float = 1.0, hevc: bool = False) -> Path:
    codec = ["-c:v", "libx265", "-x265-params", "log-level=error"] if hevc else ["-c:v", "libx264"]
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={size}:r=30:d={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            *codec,
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
    )
    return path


def _rgb_at(path: Path, t: float) -> tuple[int, int, int]:
    """Mean colour of the frame at `t` (a 16x16 thumbnail; a 1x1 scale makes some ffmpeg builds abort)."""
    raw = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(t),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            "scale=16:16",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    n = len(raw) // 3
    return tuple(round(sum(raw[c::3]) / n) for c in range(3))  # type: ignore[return-value]


def test_sources_of_different_sizes_join_cleanly(tmp_path):
    """The concat demuxer froze/corrupted frames after a resolution change; the concat filter doesn't."""
    hevc = _has_encoder("libx265")
    red = _solid(tmp_path / "red.mp4", "red", "360x640", hevc=hevc)
    blue = _solid(tmp_path / "blue.mp4", "blue", "400x700", hevc=hevc)
    out = rh.make_sdr_clip([red, blue], tmp_path / "joined.mp4", duration_s=2.0, size=(270, 480))
    r, _, b = _rgb_at(out, 0.5)
    assert r > 150 and b < 100
    r, _, b = _rgb_at(out, 1.5)
    assert b > 150 and r < 100  # the second source is playing, not a frozen first frame
