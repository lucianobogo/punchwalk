"""HDR → SDR color regression tests (COLOR-FIX).

The Ray-Ban Meta / iPhone clips are 8-bit HEVC tagged HLG (arib-std-b67) / BT.2020. The old swscale
"relative colorimetric" path clipped everything above HLG reference white and washed the walk videos and
evidence photos out (30% of pixels at 255 on the coffee-stain frame). These tests pin the fix:

- `fixtures/ingest/hlg_walk1a.mov`: 15 frames of real walk1 footage, cut with `ffmpeg -c copy` (segment
  muxer at a keyframe, so no edit-list tricks).
- `fixtures/ingest/hlg_walk1a_t0.3_zscale.png`: the frame at t=0.3 s of `ingest()`'s display.mp4 made in
  the Docker image (ffmpeg 7.1, zscale path), downscaled to 360x478.
- `fixtures/ingest/hlg_walk1a_t0.3_apple.png`: the same instant decoded by macOS AVFoundation
  (AVAssetImageGenerator drawn into an sRGB context): how the clip looks in Photos/QuickTime.

Every HDR method must land near both references; the synthetic patches check the tone curve against
`media.hdr_to_sdr_rgb` (the reference math, which matches zscale within ~2/255) on known HLG/PQ code values.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from punchwalk.pipeline import media
from punchwalk.pipeline.ingest import ingest

if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
    pytest.skip("ffmpeg/ffprobe not installed", allow_module_level=True)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "ingest"
HLG_CLIP = FIXTURES / "hlg_walk1a.mov"
REF_ZSCALE = FIXTURES / "hlg_walk1a_t0.3_zscale.png"
REF_APPLE = FIXTURES / "hlg_walk1a_t0.3_apple.png"
REF_T = 0.3
REF_SIZE = (360, 478)
TONEMAP_METHODS = [m for m in media.hdr_methods_available() if m in ("zscale", "lut3d")]


def _frame(video: Path, t: float, size: tuple[int, int] = REF_SIZE) -> np.ndarray:
    """RGB float array of the frame at `t`, resized like the references."""
    proc = subprocess.run(
        [media.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(video),
         "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True, check=True,
    )  # fmt: skip
    from io import BytesIO

    img = Image.open(BytesIO(proc.stdout)).convert("RGB").resize(size, Image.LANCZOS)
    return np.asarray(img, dtype=np.float64)


def _ref(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.float64)


def _luma(rgb: np.ndarray) -> np.ndarray:
    return rgb @ np.array([0.2126, 0.7152, 0.0722])


def _stats(rgb: np.ndarray) -> tuple[float, float]:
    """(mean luma, fraction of pixels with luma >= 250)."""
    y = _luma(rgb)
    return float(y.mean()), float((y >= 250).mean())


# --------------------------------------------------------------------------- real footage


@pytest.fixture(scope="module", params=TONEMAP_METHODS or ["none"])
def hlg_display(request, tmp_path_factory) -> tuple[str, Path]:
    method = request.param
    if method == "none":
        pytest.skip("this ffmpeg has neither zscale nor lut3d")
    out = tmp_path_factory.mktemp(f"hlg_{method}")
    meta = ingest([HLG_CLIP], out, hdr_method=method)
    assert meta.hdr_detected and meta.hdr_tonemapped and meta.hdr_method == method
    return method, out / meta.display.path


def test_fixture_is_real_hlg():
    info = media.probe(HLG_CLIP)
    assert info.hdr and info.color_transfer == "arib-std-b67" and info.color_primaries == "bt2020"
    assert HLG_CLIP.stat().st_size <= 2_000_000


def test_references_agree_with_each_other():
    """Sanity check of the fixtures themselves: zscale output ≈ Apple's own rendering of the clip."""
    z, a = _ref(REF_ZSCALE), _ref(REF_APPLE)
    assert abs(_stats(z)[0] - _stats(a)[0]) < 6
    assert np.abs(z - a).mean() < 8


def test_real_hlg_frame_matches_references(hlg_display):
    method, display = hlg_display
    got = _frame(display, REF_T)
    mean_y, clipped = _stats(got)
    ref_y, _ = _stats(_ref(REF_ZSCALE))
    apple_y, _ = _stats(_ref(REF_APPLE))
    # the washed-out swscale conversion measured mean 212 / 30% clipped on this kind of frame
    assert abs(mean_y - ref_y) < 5, (method, mean_y, ref_y)
    assert abs(mean_y - apple_y) < 8, (method, mean_y, apple_y)
    assert clipped < 0.005, (method, clipped)
    assert np.abs(got - _ref(REF_ZSCALE)).mean() < 4, method
    assert np.abs(got - _ref(REF_APPLE)).mean() < 8, method


def test_real_hlg_output_is_tagged_sdr(hlg_display):
    _, display = hlg_display
    v = next(s for s in media.ffprobe_json(display)["streams"] if s["codec_type"] == "video")
    assert v["pix_fmt"] == "yuv420p"
    assert (v.get("color_transfer"), v.get("color_primaries"), v.get("color_space"), v.get("color_range")) == (
        "bt709", "bt709", "bt709", "tv")  # fmt: skip


def test_relative_colorimetric_conversion_would_fail():
    """The regression this file guards against: plain swscale color management clips the frame."""
    if not media.scale_has_color_management():
        pytest.skip("this ffmpeg's scale filter has no color management")
    proc = subprocess.run(
        [media.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-ss", f"{REF_T}", "-i", str(HLG_CLIP),
         "-frames:v", "1", "-vf", "scale=1080:1436,scale=out_transfer=bt709:out_primaries=bt709,format=rgb24",
         "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True, check=True,
    )  # fmt: skip
    from io import BytesIO

    bad = np.asarray(Image.open(BytesIO(proc.stdout)).convert("RGB").resize(REF_SIZE), dtype=np.float64)
    mean_y, clipped = _stats(bad)
    assert mean_y - _stats(_ref(REF_ZSCALE))[0] > 30 and clipped > 0.1  # i.e. the metric above catches it


# --------------------------------------------------------------------------- synthetic code values

PATCHES = [  # non-linear signal values (8-bit), one patch each
    (0.25, 0.25, 0.25),
    (0.50, 0.50, 0.50),
    (0.75, 0.75, 0.75),  # HLG reference white (203 nits)
    (0.90, 0.90, 0.90),
    (0.60, 0.35, 0.25),  # warm, skin/wood-like
    (0.30, 0.45, 0.65),  # blue, painter's-tape-like
]
PATCH_PX = 64


def _patch_clip(path: Path, transfer: str | None, tmp: Path) -> Path:
    """A 1 s H.264 clip of flat patches whose pixels ARE the given signal values, tagged `transfer`."""
    row = np.zeros((PATCH_PX, PATCH_PX * len(PATCHES), 3), np.uint8)
    for i, rgb in enumerate(PATCHES):
        row[:, i * PATCH_PX:(i + 1) * PATCH_PX] = np.round(np.array(rgb) * 255)
    png = tmp / "patches.png"
    Image.fromarray(np.vstack([row] * 2)).save(png)
    if transfer is None:
        tags = "color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"
        matrix = "bt709"
    else:
        tags = f"color_primaries=bt2020:color_trc={transfer}:colorspace=bt2020nc:range=tv"
        matrix = "bt2020"
    media.run_ffmpeg(
        ["-loop", "1", "-framerate", "30", "-i", str(png), "-t", "1",
         "-vf", f"setparams=color_primaries=bt709:color_trc=bt709:colorspace=gbr,"
                f"scale=out_color_matrix={matrix}:out_range=tv,format=yuv444p,setparams={tags}",
         "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p", str(path)],
        label="patches",
    )  # fmt: skip
    return path


def _patch_rgb(video: Path, index: int) -> np.ndarray:
    x, y = index * PATCH_PX + PATCH_PX // 2, PATCH_PX // 2
    return np.array(media.read_rgb_pixel(video, x, y, t_s=0.5), dtype=np.float64)


@pytest.mark.parametrize("transfer,kind", [("arib-std-b67", "hlg"), ("smpte2084", "pq")])
@pytest.mark.parametrize("method", TONEMAP_METHODS or ["skip"])
def test_tone_curve_matches_reference(tmp_path, transfer, kind, method):
    if method == "skip":
        pytest.skip("this ffmpeg has neither zscale nor lut3d")
    clip = _patch_clip(tmp_path / "patches.mp4", transfer, tmp_path)
    meta = ingest([clip], tmp_path / "out", hdr_method=method)
    display = tmp_path / "out" / meta.display.path
    expected = media.hdr_to_sdr_rgb(np.array(PATCHES), kind) * 255
    for i in range(len(PATCHES)):
        got = _patch_rgb(display, i)
        assert np.abs(got - expected[i]).max() <= 6, (method, kind, PATCHES[i], got, expected[i])
    if kind == "hlg":  # reference white is a light grey, not clipped white (it was 255 before the fix)
        white = _patch_rgb(display, 2)
        assert 140 <= white.mean() <= 175, white


def test_sdr_input_is_left_alone(tmp_path):
    clip = _patch_clip(tmp_path / "sdr.mp4", None, tmp_path)
    meta = ingest([clip], tmp_path / "out")
    assert not meta.hdr_detected and meta.hdr_method is None
    display = tmp_path / "out" / meta.display.path
    for i, rgb in enumerate(PATCHES):
        got = _patch_rgb(display, i)
        assert np.abs(got - np.round(np.array(rgb) * 255)).max() <= 4, (rgb, got)


def test_reference_math_endpoints():
    black = media.hdr_to_sdr_rgb(np.zeros(3), "hlg")
    assert np.allclose(black, 0, atol=1e-3)
    ref_white = media.hdr_to_sdr_rgb(np.full(3, 0.75), "hlg")
    assert 0.55 < ref_white.mean() < 0.68  # HLG 75% → ~157/255, like Apple's decode of the walk footage
    peak = media.hdr_to_sdr_rgb(np.ones(3), "hlg")
    assert 0.85 < peak.mean() <= 1.0  # highlights roll off below clipping
    pq_white = media.hdr_to_sdr_rgb(np.full(3, 0.5807), "pq")  # PQ code for 203 nits
    assert abs(pq_white.mean() - ref_white.mean()) < 0.03  # HLG and PQ reference white agree
    wcg = media.hdr_to_sdr_rgb(np.full(3, 0.5), "wcg")  # BT.2020 SDR grey stays grey
    assert np.allclose(wcg, 0.5, atol=1e-3)


def test_lut_file_is_cached_and_well_formed(tmp_path, monkeypatch):
    monkeypatch.setenv("PUNCHWALK_LUT_DIR", str(tmp_path))
    path = media.hdr_lut_file("hlg", size=9)
    again = media.hdr_lut_file("hlg", size=9)
    assert path == again and path.parent == tmp_path
    lines = [ln for ln in path.read_text().splitlines() if ln and not ln.startswith("#")]
    assert lines[0] == "LUT_3D_SIZE 9" and len(lines) == 1 + 9**3
    first, last = (np.array(lines[i].split(), float) for i in (1, -1))
    assert np.allclose(first, 0, atol=1e-3)
    assert np.allclose(last, media.hdr_to_sdr_rgb(np.ones(3), "hlg"), atol=1e-5)


def test_hdr_kind():
    assert media.hdr_kind("arib-std-b67", "bt2020") == "hlg"
    assert media.hdr_kind("smpte2084", "bt2020") == "pq"
    assert media.hdr_kind("bt2020-10", "bt2020") == "wcg"
    assert media.hdr_kind("bt709", "bt709") is None
    assert media.hdr_kind(None, None) is None
