"""Ingest of glasses-style footage: >30 fps sources are decimated to 30 fps; mixed portrait sizes fit the
first portrait clip's canvas (Ray-Ban Meta clips are 120 fps and vary slightly in size per clip)."""

from __future__ import annotations

import importlib.util
import shutil
import sys

import pytest

from punchwalk.config import REPO_ROOT
from punchwalk.pipeline import media
from punchwalk.pipeline.ingest import MAX_FPS, SAM_FPS, frame_step, ingest

if shutil.which("ffmpeg") is None:
    pytest.skip("ffmpeg not installed", allow_module_level=True)


def _synth():
    spec = importlib.util.spec_from_file_location("synth_clip", REPO_ROOT / "scripts" / "synth_clip.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["synth_clip"] = mod
    spec.loader.exec_module(mod)
    return mod


synth = _synth()


@pytest.mark.parametrize(("src_fps", "out_fps"), [(120, 30), (60, 30), (24, 24)])
def test_display_fps_capped_at_30(tmp_path, src_fps, out_fps):
    clip = synth.make_clip(tmp_path / "walk.mp4", 1.5, fps=src_fps, size=(360, 640), audio="tone", hand=False)
    meta = ingest([clip], tmp_path / "out")
    assert MAX_FPS == 30 and meta.display.fps == out_fps
    frames = media.count_frames(tmp_path / "out" / "display.mp4")
    assert frames == meta.display.frame_count and frames % frame_step(out_fps) == 0
    assert frames == pytest.approx(1.5 * out_fps, abs=frame_step(out_fps))
    assert meta.sam.fps == SAM_FPS and meta.sam.frame_count == pytest.approx(15, abs=1)
    assert media.probe(tmp_path / "out" / "display.mp4").fps == pytest.approx(out_fps, abs=0.01)


def test_mixed_portrait_sizes_fit_without_stretching(tmp_path):
    """Glasses parts of 1552x2064 then 1376x1840 (aspects 0.752 vs 0.748): one portrait canvas, from part a."""
    a = synth.make_clip(tmp_path / "walk_a.mp4", 1.0, fps=120, size=(776, 1032), audio="tone", hand=False)
    b = synth.make_clip(tmp_path / "walk_b.mp4", 1.0, fps=120, size=(688, 920), audio="tone", hand=False, variant=1)
    meta = ingest([a, b], tmp_path / "out")
    assert (meta.display.width, meta.display.height) == (776, 1032)  # part a's size (short side <= 1080)
    assert meta.orientation == "portrait" and meta.display.fps == 30
    assert [c.padded for c in meta.clips] == [False, False]  # aspects within 1%: scaled, not padded
    assert meta.clips[1].sam_frame_offset == 10  # part b starts exactly on SAM frame 10
