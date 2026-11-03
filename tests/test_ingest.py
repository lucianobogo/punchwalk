"""Ingest tests: synthetic clips (scripts/synth_clip.py) → display.mp4, sam.mp4, audio.wav, meta.json.

Offline: needs only the system ffmpeg/ffprobe. Narration uses macOS `say` when present, otherwise a sine
tone; the one test that needs speech skips without `say`.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from punchwalk.pipeline import ingest as ingest_mod
from punchwalk.pipeline import media
from punchwalk.pipeline.ingest import IngestError, IngestMeta, discover_walks, ingest

if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
    pytest.skip("ffmpeg/ffprobe not installed", allow_module_level=True)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_synth():
    spec = importlib.util.spec_from_file_location("synth_clip", REPO_ROOT / "scripts" / "synth_clip.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["synth_clip"] = module
    spec.loader.exec_module(module)
    return module


synth = _load_synth()
HAS_SAY = synth.say_available()
WALL_RGB = (0xD9, 0xCB, 0xB0)


def _streams(path: Path) -> list[dict]:
    return media.ffprobe_json(path)["streams"]


def _video(path: Path) -> dict:
    return next(s for s in _streams(path) if s["codec_type"] == "video")


def _audio(path: Path) -> dict | None:
    return next((s for s in _streams(path) if s["codec_type"] == "audio"), None)


def _close(rgb: tuple[int, int, int], ref: tuple[int, int, int], tol: int) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(rgb, ref, strict=True))


def _wav_rms(path: Path, start_s: float, end_s: float) -> float:
    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        wf.setpos(int(start_s * rate))
        raw = wf.readframes(int((end_s - start_s) * rate))
    samples = [int.from_bytes(raw[i : i + 2], "little", signed=True) for i in range(0, len(raw) - 1, 2)]
    return (sum(s * s for s in samples) / max(1, len(samples))) ** 0.5


# --------------------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def rotated_parts(tmp_path_factory):
    """Two portrait parts stored landscape + display matrix (iPhone style); part b is 2.55 s long."""
    d = tmp_path_factory.mktemp("parts")
    clips = [
        synth.make_clip(d / "walk1_a.mp4", 3.0, rotate_meta=90, variant=0),
        synth.make_clip(d / "walk1_b.mp4", 2.55, rotate_meta=90, variant=1),
    ]
    out = d / "out"
    meta = ingest(clips, out)
    return clips, meta, out


# --------------------------------------------------------------------------- probe + helpers


def test_probe_reads_rotation_and_display_size(rotated_parts):
    clips, _, _ = rotated_parts
    info = media.probe(clips[0])
    assert (info.width, info.height) == (1280, 720)
    assert info.rotation == 90
    assert (info.display_width, info.display_height) == (720, 1280)
    assert info.orientation == "portrait"
    assert info.codec == "h264"
    assert info.has_audio
    assert not info.hdr
    assert info.fps == pytest.approx(30.0)


@pytest.mark.parametrize(
    ("stream", "expected"),
    [
        ({"side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}]}, 90),
        ({"side_data_list": [{"rotation": 90}]}, 270),
        ({"side_data_list": [{"rotation": 180}]}, 180),
        ({"side_data_list": [{"rotation": -180}]}, 180),
        ({"tags": {"rotate": "90"}}, 90),
        ({"tags": {"rotate": "270"}}, 270),
        ({}, 0),
    ],
)
def test_parse_rotation(stream, expected):
    assert media.parse_rotation(stream) == expected


def test_geometry_helpers():
    assert media.fit_short_side(1440, 1920, 1080, allow_upscale=False) == (1080, 1440)
    assert media.fit_short_side(1920, 1080, 1080, allow_upscale=False) == (1920, 1080)
    assert media.fit_short_side(640, 360, 1080, allow_upscale=False) == (640, 360)
    assert media.fit_short_side(1080, 1440, 720, allow_upscale=True) == (720, 960)
    assert media.fit_short_side(3840, 2160, 720, allow_upscale=True) == (1280, 720)
    assert media.fit_within(1280, 720, 720, 1280) == (720, 406)
    assert media.even(405.4) == 406 and media.even(1) == 2
    assert media.parse_rate("30000/1001") == pytest.approx(29.97, abs=0.01)
    assert media.parse_rate("0/0") == 0.0


def test_run_ffmpeg_error_includes_stderr_tail(tmp_path):
    with pytest.raises(media.MediaError) as exc:
        media.run_ffmpeg(["-i", str(tmp_path / "missing.mp4"), str(tmp_path / "x.mp4")], label="bad")
    assert "ffmpeg[bad] failed" in str(exc.value)
    assert "missing.mp4" in str(exc.value)


def test_is_faststart_detects_moov_position(tmp_path):
    slow = tmp_path / "slow.mp4"
    fast = tmp_path / "fast.mp4"
    src = ["-f", "lavfi", "-i", "color=c=gray:s=64x64:r=10:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p"]
    media.run_ffmpeg([*src, str(slow)], label="slow")
    media.run_ffmpeg([*src, "-movflags", "+faststart", str(fast)], label="fast")
    assert not media.is_faststart(slow)
    assert media.is_faststart(fast)


# --------------------------------------------------------------------------- rotation + parts


def test_rotated_portrait_stays_portrait_without_rotate_tag(rotated_parts):
    _, meta, out = rotated_parts
    display = out / "display.mp4"
    assert meta.orientation == "portrait"
    assert (meta.display.width, meta.display.height) == (720, 1280)
    v = _video(display)
    assert (v["width"], v["height"]) == (720, 1280)
    assert media.parse_rotation(v) == 0
    assert "rotate" not in (v.get("tags") or {})
    assert not any("rotation" in sd for sd in v.get("side_data_list") or [])
    # upright: the white baseboard is at the bottom, wall at the top and edges (nothing letterboxed)
    bottom = media.read_rgb_pixel(display, 360, 1270, t_s=1.0)
    assert min(bottom) > 225, bottom
    for x, y in [(360, 5), (5, 640), (714, 640)]:
        assert _close(media.read_rgb_pixel(display, x, y, t_s=1.0), WALL_RGB, 18), (x, y)
    sam_px = media.read_rgb_pixel(out / "sam.mp4", 360, 1275, t_s=1.0)
    assert min(sam_px) > 225


def test_parts_offsets_and_durations(rotated_parts):
    clips, meta, out = rotated_parts
    a, b = meta.clips
    assert [c.filename for c in meta.clips] == ["walk1_a.mp4", "walk1_b.mp4"]
    assert (a.order, b.order) == (0, 1)
    assert a.offset_s == 0.0 and a.duration_s == pytest.approx(3.0)
    assert b.duration_s == pytest.approx(2.5)  # 2.55 s floored to a whole tenth
    assert b.offset_s == pytest.approx(3.0)
    for c in meta.clips:
        assert c.sam_frame_offset == round(c.offset_s * 10)
        assert c.display_frame_offset == round(c.offset_s * meta.display.fps)
    assert meta.duration_s == pytest.approx(sum(media.probe(c).duration_s for c in clips), abs=0.15)
    assert meta.duration_s == pytest.approx(5.5)
    assert meta.display.frame_count == a.display_frame_count + b.display_frame_count == 165
    assert meta.sam.frame_count == 55
    assert (a.sam_frame_count, b.sam_frame_count) == (30, 25)
    # per-part SAM inputs hold exactly their clip's frames
    assert a.sam_part_path == "sam_part_0.mp4" and b.sam_part_path == "sam_part_1.mp4"
    for c in meta.clips:
        assert media.count_frames(out / c.sam_part_path) == c.sam_frame_count
    assert meta.clip_at(0.5).order == 0 and meta.clip_at(3.2).order == 1
    assert meta.sam_frame_time(b.sam_frame_offset) == pytest.approx(b.offset_s)
    assert not (out / "_work").exists()
    for c in meta.clips:
        assert c.source.rotation == 90 and c.source.codec == "h264" and not c.padded


@pytest.mark.skipif(not HAS_SAY, reason="needs macOS `say` for narration onsets")
def test_audio_stays_in_sync_across_the_boundary(rotated_parts):
    _, meta, out = rotated_parts
    wav = out / "audio.wav"
    b = meta.clips[1]
    # part b's narration starts 0.4 s into the part (synth adelay); before that it is silent
    quiet = _wav_rms(wav, b.offset_s + 0.08, b.offset_s + 0.32)
    speech = _wav_rms(wav, b.offset_s + 0.5, b.offset_s + 1.6)
    assert speech > 8 * max(quiet, 20), (quiet, speech)


def test_output_specs(rotated_parts):
    _, meta, out = rotated_parts
    display, sam, wav = out / "display.mp4", out / "sam.mp4", out / "audio.wav"

    v = _video(display)
    a = _audio(display)
    assert v["codec_name"] == "h264" and v["pix_fmt"] == "yuv420p"
    assert v["r_frame_rate"] == "30/1" and v["avg_frame_rate"] == "30/1"
    assert min(v["width"], v["height"]) <= 1080
    assert v.get("color_transfer") == "bt709" and v.get("color_primaries") == "bt709"
    assert a is not None and a["codec_name"] == "aac"
    assert media.is_faststart(display)
    keys = media.keyframe_times(display)
    gaps = [b - a for a, b in zip([*keys, meta.duration_s][:-1], [*keys, meta.duration_s][1:], strict=True)]
    assert keys[0] == pytest.approx(0.0) and max(gaps) <= 2.0 + 1e-3, keys

    sv = _video(sam)
    assert sv["codec_name"] == "h264" and sv["pix_fmt"] == "yuv420p"
    assert sv["r_frame_rate"] == "10/1"
    assert min(sv["width"], sv["height"]) == 720
    assert sv["width"] % 2 == 0 and sv["height"] % 2 == 0
    assert _audio(sam) is None
    assert media.is_faststart(sam)
    assert media.count_frames(sam) == meta.sam.frame_count

    wa = _audio(wav)
    assert wa["codec_name"] == "pcm_s16le" and int(wa["sample_rate"]) == 16000 and wa["channels"] == 1
    with wave.open(str(wav), "rb") as wf:
        assert wf.getsampwidth() == 2
        assert wf.getnframes() / wf.getframerate() == pytest.approx(meta.duration_s, abs=0.1)

    assert meta.scale.sam_to_display_x == pytest.approx(meta.display.width / meta.sam.width)
    assert meta.scale.sam_to_display_y == pytest.approx(meta.display.height / meta.sam.height)
    assert meta.sam.fps == 10 and meta.display.fps == 30
    assert set(meta.tools) == {"ffmpeg", "ffprobe"}
    assert meta.canvas_policy == "portrait_if_any_fit_pad"


def test_meta_json_round_trips(rotated_parts):
    _, meta, out = rotated_parts
    loaded = IngestMeta.load(out)
    assert loaded == meta
    assert loaded.sam_to_display(10, 20) == pytest.approx(
        (10 * meta.scale.sam_to_display_x, 20 * meta.scale.sam_to_display_y)
    )


# --------------------------------------------------------------------------- sizes, codecs, HDR, audio


def test_glasses_size_source_is_downscaled_with_scale_factors(tmp_path):
    clip = synth.make_clip(tmp_path / "walk.mp4", 1.5, size=(1440, 1920), audio="tone")
    meta = ingest([clip], tmp_path / "out")
    assert (meta.display.width, meta.display.height) == (1080, 1440)
    assert (meta.sam.width, meta.sam.height) == (720, 960)
    assert meta.scale.sam_to_display_x == pytest.approx(1.5)
    assert meta.scale.sam_to_display_y == pytest.approx(1.5)
    assert meta.clips[0].sam_part_path == "sam.mp4"
    assert meta.sam.frame_count == 15


def test_hevc_landscape_input(tmp_path):
    clip = synth.make_clip(tmp_path / "walk.mp4", 2.0, portrait=False, hevc=True, audio="tone")
    assert media.probe(clip).codec == "hevc"
    meta = ingest([clip], tmp_path / "out")
    assert meta.orientation == "landscape"
    assert (meta.display.width, meta.display.height) == (1280, 720)
    assert (meta.sam.width, meta.sam.height) == (1280, 720)
    assert _video(tmp_path / "out" / "display.mp4")["codec_name"] == "h264"
    assert meta.clips[0].source.codec == "hevc"


def test_hdr_input_is_tonemapped(tmp_path):
    clip = synth.make_clip(tmp_path / "walk.mp4", 1.5, hdr=True, rotate_meta=90, audio="tone")
    info = media.probe(clip)
    assert info.hdr and info.color_transfer == "arib-std-b67" and info.codec == "hevc"
    meta = ingest([clip], tmp_path / "out")
    assert meta.hdr_detected and meta.clips[0].source.hdr
    v = _video(tmp_path / "out" / "display.mp4")
    assert v["pix_fmt"] == "yuv420p" and v.get("color_transfer") == "bt709"
    if media.hdr_methods_available()[0] == "none":
        assert not meta.hdr_tonemapped
        pytest.skip("this ffmpeg has no tonemap path; checked the fallback only")
    assert meta.hdr_tonemapped and meta.hdr_method in ("swscale", "zscale")
    if meta.hdr_method == "swscale":  # relative colorimetric: SDR-range colors come back as they were
        wall = media.read_rgb_pixel(tmp_path / "out" / "display.mp4", 360, 40, t_s=0.5)
        assert _close(wall, WALL_RGB, 15), wall


def test_hdr_without_tonemap_falls_back_with_warning(tmp_path):
    clip = synth.make_clip(tmp_path / "walk.mp4", 1.0, hdr=True, audio="none")
    meta = ingest([clip], tmp_path / "out", hdr_method="none")
    assert meta.hdr_detected and not meta.hdr_tonemapped and meta.hdr_method == "none"
    assert any("tonemap" in w for w in meta.warnings)
    assert _video(tmp_path / "out" / "display.mp4")["pix_fmt"] == "yuv420p"


def test_clip_without_audio_gets_a_silent_track(tmp_path):
    clips = [
        synth.make_clip(tmp_path / "walk_a.mp4", 1.0, audio="none", variant=0),
        synth.make_clip(tmp_path / "walk_b.mp4", 1.0, audio="tone", variant=1),
    ]
    assert not media.probe(clips[0]).has_audio
    meta = ingest(clips, tmp_path / "out")
    assert not meta.clips[0].source.has_audio and meta.clips[1].source.has_audio
    assert _audio(tmp_path / "out" / "display.mp4") is not None
    assert _wav_rms(tmp_path / "out" / "audio.wav", 0.1, 0.9) < 30  # the silent part
    assert _wav_rms(tmp_path / "out" / "audio.wav", 1.2, 1.8) > 300  # the tone part
    assert meta.duration_s == pytest.approx(2.0)


def test_mixed_orientation_portrait_first(tmp_path):
    clips = [
        synth.make_clip(tmp_path / "walk_a.mp4", 1.0, portrait=True, audio="tone"),
        synth.make_clip(tmp_path / "walk_b.mp4", 1.0, portrait=False, audio="tone", variant=1),
    ]
    meta = ingest(clips, tmp_path / "out")
    assert meta.orientation == "portrait"
    assert (meta.display.width, meta.display.height) == (720, 1280)
    assert [c.padded for c in meta.clips] == [False, True]
    assert meta.canvas_policy == "portrait_if_any_fit_pad"
    display = tmp_path / "out" / "display.mp4"
    assert _close(media.read_rgb_pixel(display, 360, 640, t_s=1.5), WALL_RGB, 18)  # picture in the middle
    assert max(media.read_rgb_pixel(display, 360, 20, t_s=1.5)) < 20  # padding above it


def test_mixed_orientation_landscape_first_still_gets_portrait_canvas(tmp_path):
    """Portrait (glasses) footage is never pillarboxed into a landscape frame, whatever the clip order."""
    clips = [
        synth.make_clip(tmp_path / "walk_a.mp4", 1.0, portrait=False, audio="tone"),
        synth.make_clip(tmp_path / "walk_b.mp4", 1.0, size=(1080, 1440), audio="tone", variant=1),
    ]
    meta = ingest(clips, tmp_path / "out")
    assert meta.orientation == "portrait"
    assert (meta.display.width, meta.display.height) == (1080, 1440)  # the portrait clip, not shrunk
    assert [c.padded for c in meta.clips] == [True, False]
    display = tmp_path / "out" / "display.mp4"
    # portrait clip fills the frame edge to edge; the landscape clip has bars top and bottom
    for x, y in [(5, 40), (1074, 40), (540, 5)]:
        assert _close(media.read_rgb_pixel(display, x, y, t_s=1.5), WALL_RGB, 18), (x, y)
    assert max(media.read_rgb_pixel(display, 540, 20, t_s=0.5)) < 20
    assert _close(media.read_rgb_pixel(display, 540, 600, t_s=0.5), WALL_RGB, 18)


@pytest.mark.parametrize(("fps", "step"), [(30, 3), (60, 6), (24, 12), (25, 5), (15, 3), (10, 1)])
def test_frame_step(fps, step):
    assert ingest_mod.frame_step(fps) == step
    assert (step / fps * 10) == pytest.approx(round(step / fps * 10))


def test_24fps_parts_start_exactly_on_sam_frames(tmp_path):
    clips = [
        synth.make_clip(tmp_path / "walk_a.mp4", 1.3, fps=24, audio="tone"),
        synth.make_clip(tmp_path / "walk_b.mp4", 1.3, fps=24, audio="tone", variant=1),
    ]
    meta = ingest(clips, tmp_path / "out")
    assert meta.display.fps == 24
    a, b = meta.clips
    assert a.display_frame_count == b.display_frame_count == 24  # 31 frames floored to a multiple of 12
    assert b.offset_s == pytest.approx(1.0)
    assert b.sam_frame_offset == 10 and a.sam_frame_count == 10
    assert b.offset_s * 10 == pytest.approx(b.sam_frame_offset)
    assert meta.sam.frame_count == 20
    for c in meta.clips:
        assert media.count_frames(tmp_path / "out" / c.sam_part_path) == c.sam_frame_count


# --------------------------------------------------------------------------- idempotency + errors


def test_idempotent_skip_and_force(tmp_path, monkeypatch):
    clip = synth.make_clip(tmp_path / "walk.mp4", 1.0, audio="tone")
    out = tmp_path / "out"
    first = ingest([clip], out)
    display_mtime = (out / "display.mp4").stat().st_mtime_ns

    calls: list[str] = []
    real_run = media.run_ffmpeg

    def counting(args, *, label, **kw):
        calls.append(label)
        return real_run(args, label=label, **kw)

    monkeypatch.setattr(media, "run_ffmpeg", counting)
    second = ingest([clip], out)
    assert calls == []
    assert second.created_at == first.created_at
    assert (out / "display.mp4").stat().st_mtime_ns == display_mtime

    third = ingest([clip], out, force=True)
    assert any(label.startswith("normalize") for label in calls)
    assert third.created_at > first.created_at

    # a changed input (new mtime) is re-encoded without force
    calls.clear()
    st = clip.stat()
    os.utime(clip, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    ingest([clip], out)
    assert calls

    # a missing output is rebuilt
    calls.clear()
    (out / "sam.mp4").unlink()
    ingest([clip], out)
    assert "sam.mp4" in calls and (out / "sam.mp4").is_file()


def test_unreadable_clip_raises_ingest_error(tmp_path):
    bad = tmp_path / "walk.mp4"
    bad.write_bytes(b"not a video at all" * 100)
    with pytest.raises(IngestError) as exc:
        ingest([bad], tmp_path / "out")
    assert "walk.mp4" in str(exc.value)
    with pytest.raises(IngestError):
        ingest([tmp_path / "nope.mp4"], tmp_path / "out")
    with pytest.raises(IngestError):
        ingest([], tmp_path / "out")


def test_cli_prints_summary(rotated_parts, capsys):
    clips, meta, out = rotated_parts
    code = ingest_mod.main([*map(str, clips), "--out", str(out), "-q"])
    assert code == 0
    printed = capsys.readouterr().out
    assert "display.mp4 720x1280 @ 30 fps" in printed
    assert "walk1_b.mp4" in printed and "sam[30:+25]" in printed


def test_cli_module_entrypoint(rotated_parts):
    clips, _, out = rotated_parts
    proc = subprocess.run(
        [sys.executable, "-m", "punchwalk.pipeline.ingest", *map(str, clips), "--out", str(out), "-q"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "orientation portrait" in proc.stdout


# --------------------------------------------------------------------------- discovery + synth


def test_discover_walks_ordering(tmp_path):
    for name in [
        "walk2_b.MOV",
        "walk2_a.mov",
        "walk1.mp4",
        "walk10.m4v",
        "walk3_a.mp4",
        "README.md",
        ".hidden.mp4",
        "notes.txt",
        "random.mp4",
    ]:
        (tmp_path / name).write_bytes(b"x")
    walks = discover_walks(tmp_path)
    assert list(walks) == ["walk1", "walk2", "walk3", "walk10"]
    assert [p.name for p in walks["walk2"]] == ["walk2_a.mov", "walk2_b.MOV"]
    assert [p.name for p in walks["walk1"]] == ["walk1.mp4"]
    assert discover_walks(tmp_path / "missing") == {}


def test_discover_walks_rejects_single_plus_parts(tmp_path):
    (tmp_path / "walk4.mp4").write_bytes(b"x")
    (tmp_path / "walk4_a.mp4").write_bytes(b"x")
    with pytest.raises(ValueError, match="walk4"):
        discover_walks(tmp_path)


def test_synth_clips_are_small_and_named_in_order(tmp_path):
    paths = synth.make_walk(tmp_path, 2.0, parts=3, audio="tone")
    assert [p.name for p in paths] == ["walk_a.mp4", "walk_b.mp4", "walk_c.mp4"]
    assert all(p.stat().st_size < 1_000_000 for p in paths)
    assert list(discover_walks(tmp_path)) == []  # "walk_a" has no number: not a footage walk
    tone = synth.make_clip(tmp_path / "t.mp4", 1.0, audio="tone")
    assert media.probe(tone).has_audio
