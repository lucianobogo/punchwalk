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
    # the hint names the rooms this seed used, not the default demo rooms
    assert "--walk synth_walk --full --rooms Kitchen Hallway Bedroom" in text and "Bathroom" not in text
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


def test_interrupted_seed_closes_its_job_so_a_restart_never_replays_it(seed, api: TestClient) -> None:
    from punchwalk.core.models import Job, Walk
    from punchwalk.db import session_factory

    def interrupted(walk_id, *, session_factory, progress, force):  # Ctrl-C in the middle of a stage
        progress({"stage": "transcribe", "status": "running", "message": "Transcribing"})
        raise KeyboardInterrupt

    jobs.set_runner(interrupted)
    try:
        with pytest.raises(KeyboardInterrupt):
            seed.run(["--walks", "synth_walk"])
    finally:
        jobs.set_runner(None)
    with session_factory() as s:
        (job,) = s.exec(Job.__table__.select()).all()  # type: ignore[attr-defined]
        walk = s.get(Walk, "wlk_demo_synth_walk")
    assert job.status == "failed" and job.finished_at is not None and "interrupted" in job.error
    assert job.progress.get("seed") is True
    assert walk.status == "failed" and "interrupted" in walk.error
    assert jobs.recover_interrupted_jobs() == 0  # nothing left running for the next boot to re-queue
    with session_factory() as s:
        assert s.get(Job, job.id).status == "failed"


def test_force_rerun_does_not_double_count_model_calls(seed, api: TestClient) -> None:
    first = seed.run(["--walks", "synth_walk"])["walks"][0]["model_calls"]
    assert first > 0
    again = seed.run(["--walks", "synth_walk", "--force"])["walks"][0]
    assert again["status"] == "ready" and again["model_calls"] == first


def test_bad_snapshot_leaves_the_demo_project_alone(seed, api: TestClient, tmp_path: Path) -> None:
    import io
    import json
    import tarfile

    snap = tmp_path / "snap.tar.gz"
    seed.run(["--walks", "synth_walk", "--export", str(snap)])
    before = api.get("/api/projects/prj_demo/items").json()

    with tarfile.open(snap) as tar:
        manifest = json.loads(tar.extractfile("manifest.json").read())  # type: ignore[union-attr]
    manifest["rows"]["items"][-1]["severity"] = "catastrophic"  # not a Severity
    bad = tmp_path / "bad.tar.gz"
    with tarfile.open(bad, "w:gz") as tar:
        blob = json.dumps(manifest).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(blob)
        tar.addfile(info, io.BytesIO(blob))
    with pytest.raises(seed.SeedError, match="bad items row"):
        seed.run(["--import", str(bad)])
    assert api.get("/api/projects/prj_demo/items").json() == before
    assert Path(get_settings().DATA_DIR, "walks", "wlk_demo_synth_walk", "display.mp4").is_file()


def test_boot_recovery_closes_seed_jobs_and_requeues_the_rest(api: TestClient) -> None:
    from punchwalk.core.enums import JobStatus, WalkStatus
    from punchwalk.core.models import Job, Project, Walk
    from punchwalk.db import session_factory

    with session_factory() as s:  # a SIGKILLed seed (two ways to recognise it) next to a normal upload
        s.add(Project(id="prj_x", name="X"))
        s.flush()
        for wid in ("wlk_demo_x", "wlk_marked", "wlk_upload"):
            s.add(Walk(id=wid, project_id="prj_x", status=WalkStatus.processing))
        s.flush()
        s.add(Job(id="job_demo", walk_id="wlk_demo_x", status=JobStatus.running, attempts=1))
        s.add(Job(id="job_marked", walk_id="wlk_marked", status=JobStatus.running, attempts=1,
                  progress={"seed": True}))  # fmt: skip
        s.add(Job(id="job_upload", walk_id="wlk_upload", status=JobStatus.running, attempts=1))
        s.commit()

    assert jobs.recover_interrupted_jobs() == 1  # only the upload goes back to the queue
    with session_factory() as s:
        for job_id, walk_id in (("job_demo", "wlk_demo_x"), ("job_marked", "wlk_marked")):
            job, walk = s.get(Job, job_id), s.get(Walk, walk_id)
            assert job.status == JobStatus.failed and job.error == "Seed interrupted; run scripts/seed.py again."
            assert job.finished_at is not None and walk.status == WalkStatus.failed
        assert s.get(Job, "job_upload").status == JobStatus.queued
        assert s.get(Walk, "wlk_upload").status == WalkStatus.processing
    assert jobs.claim_next_job() == "job_upload" and jobs.claim_next_job() is None


def test_snapshot_export_can_reencode_the_display_video(seed, api: TestClient, tmp_path: Path) -> None:
    import tarfile

    seed.run(["--walks", "synth_walk"])
    original = Path(get_settings().DATA_DIR, "walks", "wlk_demo_synth_walk", "display.mp4")
    before = original.read_bytes()
    seed.run(["--walks", "synth_walk", "--export", str(tmp_path / "snap.tar.gz"), "--video-crf", "35"])
    assert original.read_bytes() == before  # DATA_DIR/walks is left as it is
    with tarfile.open(tmp_path / "snap.tar.gz") as tar:
        tar.extract("walks/wlk_demo_synth_walk/display.mp4", tmp_path / "out", filter="data")
    copy = tmp_path / "out" / "walks" / "wlk_demo_synth_walk" / "display.mp4"
    assert copy.read_bytes() != before and seed._video_shape(copy) == seed._video_shape(original)
    with pytest.raises(SystemExit):
        seed.parse_args(["--video-crf", "28"])  # only with --export
