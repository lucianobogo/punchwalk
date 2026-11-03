"""`make seed` (PLAN 8.1) on the synthetic recording: offline, readonly cache, tmp DATA_DIR."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from punchwalk.api import jobs
from punchwalk.api import schemas as S
from punchwalk.config import REPO_ROOT, get_settings, reset_settings
from tests import test_api_support as support
from tests.test_api_support import check

api = support.api

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def load_seed():
    spec = importlib.util.spec_from_file_location("seed_script", REPO_ROOT / "scripts" / "seed.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["seed_script"] = mod  # dataclasses look their module up here
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def seed(monkeypatch: pytest.MonkeyPatch, api: TestClient):
    # the seed sets these itself; registering them here makes monkeypatch restore them afterwards
    for name in ("PUNCHWALK_CACHE", "SLACK_WEBHOOK_URL", "RESEND_API_KEY"):
        monkeypatch.setenv(name, "readonly" if name == "PUNCHWALK_CACHE" else "")
    monkeypatch.setenv("AUTO_SEND", "1")
    reset_settings()
    jobs.set_runner(None)  # the shared api fixture installs a fake runner; the seed runs the real pipeline
    return load_seed()


def test_seed_synth_walk_end_to_end(seed, api: TestClient) -> None:
    info = seed.run(["--walks", "synth_walk"])
    assert info["live_cost_usd"] == 0.0 and info["cached_calls"] > 0
    (walk,) = info["walks"]
    assert walk["walk_id"] == "wlk_demo_synth_walk" and walk["status"] == "ready" and len(walk["items"]) == 3

    project = check(S.ProjectOut, api.get("/api/projects/prj_demo").json())
    assert project.name == "Unit 2B final punch" and project.address == "418 Alder Street, Unit 2B"
    assert project.rooms == ["Kitchen", "Hallway", "Bedroom"]  # the synthetic recording's rooms
    assert project.walk_count == 1 and project.counts.all == 3
    ace = next(t for t in project.trades if t.trade == "electrical")
    assert ace.company == "Ace Electric" and ace.has_slack_webhook
    assert all(t.email is None or t.email.endswith("@example.com") for t in project.trades)

    items = check(S.ItemListOut, api.get("/api/projects/prj_demo/items").json())
    assert items.total == 3 and [i.number for i in sorted(items.items, key=lambda i: i.number)] == [1, 2, 3]
    for item in items.items:
        assert item.evidence and item.video_url == "/media/walks/wlk_demo_synth_walk/display.mp4"
        assert api.get(item.evidence.crop_url).status_code == 200
        assert any(h.action == "created" for h in item.history)
    # the auto-send policy ran: verified items went out, flagged ones wait with a note
    for item in items.items:
        if item.status == "needs_review":
            assert any((h.note or "").startswith("Held for a look") for h in item.history)
        else:
            assert item.status == "dispatched" and item.dispatch is not None

    detail = check(S.WalkDetailOut, api.get("/api/walks/wlk_demo_synth_walk").json())
    assert detail.walk.status == "ready" and detail.walk.media.display_mp4 and detail.walk.media.poster
    assert len(detail.turns) == 3 and len(detail.points) == 2
    assert all(s.status in ("done", "skipped") for s in detail.walk.stages)
    progress = check(S.JobProgressOut, api.get("/api/walks/wlk_demo_synth_walk/progress").json())
    assert progress.status == "done" and progress.progress == 1.0
    overlay = check(S.OverlayOut, api.get("/api/walks/wlk_demo_synth_walk/overlay").json())
    assert overlay.frames and overlay.events
    calls = check(S.ModelCallListOut, api.get("/api/walks/wlk_demo_synth_walk/model-calls").json())
    assert calls.calls and all(c.cached for c in calls.calls)


def test_seed_is_idempotent_and_reset_starts_over(seed, api: TestClient) -> None:
    seed.run(["--walks", "synth_walk"])
    first = {i["id"]: i["status"] for i in api.get("/api/projects/prj_demo/items").json()["items"]}
    again = seed.run(["--walks", "synth_walk"])  # ready walks are left alone
    assert again["walks"][0]["status"] == "ready"
    assert {i["id"]: i["status"] for i in api.get("/api/projects/prj_demo/items").json()["items"]} == first
    assert len(api.get("/api/walks?project_id=prj_demo").json()["walks"]) == 1

    api.patch(f"/api/items/{next(iter(first))}", json={"title": "Edited by the PM"})
    seed.run(["--walks", "synth_walk", "--reset"])
    titles = {i["title"] for i in api.get("/api/projects/prj_demo/items").json()["items"]}
    assert "Edited by the PM" not in titles and len(titles) == 3


def test_seed_refuses_other_rooms_and_missing_walks(seed, api: TestClient) -> None:
    seed.run(["--walks", "synth_walk"])
    with pytest.raises(seed.SeedError, match="run with --reset"):
        seed.run(["--walks", "synth_walk", "--rooms", "Kitchen"])
    with pytest.raises(seed.SeedError, match="No recording of walk1"):
        seed.run([])  # walk1/walk2 aren't recorded in this checkout
    assert seed.main(["--walks", "no_such_walk"]) == 1


def test_seed_cache_miss_is_reported_with_the_stage(seed, api: TestClient, tmp_path: Path, monkeypatch) -> None:
    # a copy of the recording whose audio differs by one byte: every ASR cache key misses
    media = tmp_path / "demo" / "synth_walk"
    shutil.copytree(REPO_ROOT / "fixtures" / "pipeline" / "synth_walk" / "media", media)
    wav = media / "audio.wav"
    data = bytearray(wav.read_bytes())
    data[-1] ^= 0xFF
    wav.write_bytes(bytes(data))
    monkeypatch.setattr(seed, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("FIXTURE_CACHE_DIR", str(tmp_path / "no_cache"))
    reset_settings()
    with pytest.raises(seed.SeedError) as err:
        seed.run(["--walks", "synth_walk", "--rooms", "Kitchen", "Hallway", "Bedroom"])
    text = str(err.value)
    assert "failed at stage transcribe" in text and "Re-record" in text
    assert Path(get_settings().DATA_DIR, "walks", "wlk_demo_synth_walk", "audio.wav").is_file()


def test_snapshot_export_and_import(seed, api: TestClient, tmp_path: Path) -> None:
    seed.run(["--walks", "synth_walk", "--export", str(tmp_path / "snap.tar.gz")])
    before = api.get("/api/projects/prj_demo/items").json()
    import tarfile

    with tarfile.open(tmp_path / "snap.tar.gz") as tar:
        names = tar.getnames()
    assert "manifest.json" in names and any(n.endswith("/display.mp4") for n in names)
    assert not any(n.endswith(("audio.wav", "sam.mp4")) or "/clips/" in n for n in names)

    api.patch(f"/api/items/{before['items'][0]['id']}", json={"title": "Changed after the export"})
    info = seed.run(["--import", str(tmp_path / "snap.tar.gz")])  # replaces the project, no pipeline run
    assert info["walks"][0]["status"] == "ready"
    after = api.get("/api/projects/prj_demo/items").json()
    assert [(i["id"], i["title"], i["status"]) for i in after["items"]] == [
        (i["id"], i["title"], i["status"]) for i in before["items"]
    ]
    assert api.get(after["items"][0]["evidence"]["crop_url"]).status_code == 200
    assert api.get("/api/walks/wlk_demo_synth_walk/overlay").status_code == 200
    assert api.get("/api/walks/wlk_demo_synth_walk/progress").json()["status"] == "done"
