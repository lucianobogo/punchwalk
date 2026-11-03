"""scripts/record.py (PLAN 0.6) offline: synthetic footage, Files/SAM/ASR mocked over HTTP (respx).

The real clients run (cache writes, cost ledger, promotion); only the network is replaced. The SAM
video reply is the recorded hand stream of the synthetic walk's part a.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys

import httpx
import pytest
import respx

from punchwalk.config import REPO_ROOT, reset_settings
from tests.conftest import FIXTURES

if shutil.which("ffmpeg") is None:
    pytest.skip("ffmpeg not installed", allow_module_level=True)

SYNTH_CACHE = FIXTURES / "pipeline" / "synth_walk" / "cache"
HAND_TEXT = (SYNTH_CACHE / "44" / "443f5493ad1087343a10f670383cc629cbb6c3b2de8c5eba553420adffe0445b.txt").read_text()
IMAGE_TEXT = (FIXTURES / "clients" / "sam_image_tape.txt").read_text()
ASR = json.loads((FIXTURES / "clients" / "asr_response.json").read_text())


def _load(name: str, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


record = _load("record_script", REPO_ROOT / "scripts" / "record.py")
synth = _load("synth_clip", REPO_ROOT / "scripts" / "synth_clip.py")


def _sse(text: str, frames: int) -> bytes:
    lane = {"item_id": "msg_0", "output_index": 0, "content_index": 0}
    evs = [{"type": "response.created", "response": {"status": "in_progress"}}]
    evs += [{"type": "response.output_text.delta", **lane, "delta": ln} for ln in text.splitlines(keepends=True)]
    evs += [{"type": "response.completed",
             "response": {"status": "completed", "usage": {"video_frames_processed": frames}}}]
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in evs).encode()


def _sam(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    media = body["input"][0]["content"][1]["type"]
    text = HAND_TEXT if media == "input_video" else IMAGE_TEXT
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          content=_sse(text, 30 if media == "input_video" else 1))


@pytest.fixture
def footage(tmp_path):
    d = tmp_path / "footage"
    synth.make_clip(d / "walk1_a.mp4", 3.0, audio="tone", variant=0, point_at=0.2, hold_s=1.6)
    synth.make_clip(d / "walk1_b.mp4", 3.0, audio="tone", variant=1, hand=False)
    (d / "README.md").write_text("ignored")
    return d


@respx.mock
def test_record_writes_fixtures_promotes_cache_and_ranks_phrasings(footage, tmp_path, readwrite, no_fixture_cache,
                                                                   capsys, ledger_rows):
    files = respx.post("https://api.meta.ai/v1/files").mock(
        side_effect=lambda req: httpx.Response(200, json={"id": f"file-{len(files.calls)}"}))
    sam_route = respx.post("https://api.meta.ai/v1/responses").mock(side_effect=_sam)
    asr_route = respx.post("https://api.meta.ai/v1/asr/transcribe").mock(return_value=httpx.Response(200, json=ASR))
    out, fx, promote = tmp_path / "out", tmp_path / "fixtures", tmp_path / "promoted"
    argv = ["--footage", str(footage), "--out", str(out), "--fixtures", str(fx), "--promote-to", str(promote)]

    assert record.main(argv) == 0
    printed = capsys.readouterr().out
    assert "== walk1: walk1_a.mp4, walk1_b.mp4" in printed and "hand concept on walk1" in printed
    assert asr_route.call_count == 1
    assert sam_route.call_count == 2 + 9  # one video per part + 3 phrasings x 3 frames

    w = fx / "walk1"
    transcript = json.loads((w / "transcript.json").read_text())
    assert transcript["turns"][0]["text"].startswith("kitchen") and "escutcheon" in transcript["keywords"]
    tracks = json.loads((w / "tracks.json").read_text())
    assert [p["label"] for p in tracks["parts"]] == ["a", "b"]
    assert all(r["frame"] < tracks["parts"][0]["frame_count"] * 2 for r in tracks["records"])
    assert (w / "meta.json").is_file() and (w / "poster.jpg").stat().st_size > 1000
    concept = json.loads((w / "hand_concept.json").read_text())
    assert [r["phrase"] for r in concept["ranking"]] and len(concept["ranking"]) == 3
    assert len(concept["frames"]) == 3 and concept["current_default"] == "hand"

    promoted = sorted(p.name for p in promote.rglob("*.json"))
    assert len(promoted) >= 2 + 2 + 1  # files uploads + SAM videos + ASR (+ image calls)
    assert any(p.suffix == ".txt" for p in promote.rglob("*"))  # raw SAM stream sidecars
    assert any(r["stage"] == "track_hands" and not r["cached"] for r in ledger_rows())

    # second run replays everything from the cache: no new requests, $0
    before = sam_route.call_count, asr_route.call_count
    assert record.main([*argv, "--skip-phrasings"]) == 0
    assert (sam_route.call_count, asr_route.call_count) == before
    assert "cost $0.0000" in capsys.readouterr().out


def test_record_dry_run_and_cost_guard(footage, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("PUNCHWALK_CACHE", "readonly")
    reset_settings()
    base = ["--footage", str(footage), "--out", str(tmp_path / "o"), "--fixtures", str(tmp_path / "f")]
    assert record.main([*base, "--dry-run"]) == 0
    assert "skipped: dry run" in capsys.readouterr().out
    assert record.main([*base, "--max-walk-usd", "0.001"]) == 1
    assert "--max-walk-usd" in capsys.readouterr().out
    assert not (tmp_path / "f").exists()


def test_record_no_walks(tmp_path, capsys):
    (tmp_path / "empty").mkdir()
    assert record.main(["--footage", str(tmp_path / "empty")]) == 1
    assert "no walks found" in capsys.readouterr().out
