"""Walk upload, the job worker, progress (poll + SSE), walk detail, overlay, model calls, processing."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from punchwalk.api import jobs
from punchwalk.api import schemas as S
from punchwalk.config import get_settings
from punchwalk.core.enums import JobStatus, Stage, WalkStatus
from punchwalk.core.models import Job, Walk
from punchwalk.db import session_factory
from tests import test_api_support as support
from tests.test_api_support import (
    DISPLAY_H,
    DISPLAY_W,
    check,
    fake_runner,
    make_project,
    process,
    ready_walk,
    upload,
)

# shared fixtures (defined in tests/test_api_support.py)
api = support.api
clip_path = support.clip_path


def test_upload_creates_walk_and_job_and_streams_clips_to_disk(api: TestClient, clip_path: Path) -> None:
    pid = make_project(api)["id"]
    second = clip_path.with_name("walk1 b (2).MOV")
    second.write_bytes(clip_path.read_bytes())
    res = upload(api, pid, [clip_path, second])
    assert res.status_code == 201, res.text
    out = check(S.UploadOut, res.json())
    assert out.status == WalkStatus.uploaded and out.walk_id.startswith("wlk_") and out.job_id.startswith("job_")

    clips_dir = Path(get_settings().DATA_DIR) / "walks" / out.walk_id / "clips"
    assert sorted(p.name for p in clips_dir.iterdir()) == ["00_walk1_a.mp4", "01_walk1_b_2_.mov"]
    assert (clips_dir / "00_walk1_a.mp4").read_bytes() == clip_path.read_bytes()

    walk = check(S.WalkOut, api.get(f"/api/walks/{out.walk_id}").json()["walk"])
    assert walk.status == "uploaded" and walk.job_id == out.job_id and walk.kind == "initial"
    assert [c.filename for c in walk.clips] == ["walk1_a.mp4", "walk1_b_2_.mov"]
    assert walk.clips[1].offset_s == pytest.approx(walk.clips[0].duration_s, abs=0.01)
    assert walk.duration_s == pytest.approx(4.0, abs=0.2)
    assert all(s.status == "pending" for s in walk.stages)
    assert walk.stages[2].stage == Stage.classify_turns  # run order: classify before find_points

    prog = check(S.JobProgressOut, api.get(f"/api/walks/{out.walk_id}/progress").json())
    assert prog.status == "queued" and prog.progress == 0.0 and prog.attempts == 0
    assert prog.stages[0].detail is None and prog.stages[1].detail == "Muse Voice Transcribe, waiting"


@pytest.mark.parametrize(
    ("name", "content", "status", "phrase"),
    [
        ("notes.txt", b"hello", 415, "isn't a video"),
        ("fake.mp4", b"not really a video", 415, "couldn't read"),
        ("empty.mp4", b"", 400, "is empty"),
    ],
)
def test_upload_rejects_bad_files(api: TestClient, name: str, content: bytes, status: int, phrase: str) -> None:
    pid = make_project(api)["id"]
    ctype = "text/plain" if name.endswith(".txt") else "video/mp4"
    res = api.post(f"/api/projects/{pid}/walks", files=[("clips", (name, content, ctype))])
    assert res.status_code == status, res.text
    assert phrase in res.json()["detail"]
    assert not list((Path(get_settings().DATA_DIR) / "walks").glob("*")), "failed uploads leave nothing behind"
    with session_factory() as s:
        assert s.exec(select(Walk)).all() == [] and s.exec(select(Job)).all() == []


def test_upload_rejects_no_clips_and_walks_over_10_minutes(
    api: TestClient, clip_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = make_project(api)["id"]
    res = api.post(f"/api/projects/{pid}/walks", data={"kind": "initial"}, files=[("other", ("x.bin", b"1", "a/b"))])
    assert res.status_code == 400 and "at least one clip" in res.json()["detail"]
    assert api.post(f"/api/projects/{pid}/walks", json={"x": 1}).status_code == 415

    from punchwalk.api import uploads

    monkeypatch.setattr(uploads, "MAX_WALK_DURATION_S", 1.0)  # the 2 s clip is "too long"
    res = upload(api, pid, [clip_path])
    assert res.status_code == 400
    assert "at most 10 minutes" in res.json()["detail"] and "split it" in res.json()["detail"]

    res = upload(api, pid, [clip_path], kind="sideways")
    assert res.status_code == 400 and "kind" in res.json()["detail"]
    assert upload(api, "prj_missing", [clip_path]).status_code == 404


def test_fake_runner_drives_walk_to_ready(api: TestClient, clip_path: Path) -> None:
    project, up = ready_walk(api, clip_path)
    prog = check(S.JobProgressOut, api.get(f"/api/walks/{up['walk_id']}/progress").json())
    assert prog.status == JobStatus.done and prog.walk_status == WalkStatus.ready
    assert prog.progress == 1.0 and prog.stage is None and prog.attempts == 1
    assert prog.message == "1 item ready" and prog.items_expected == 1
    assert [t.title for t in prog.items_so_far] == ["Missing cabinet knob"]
    assert prog.items_so_far[0].crop_url.endswith("/crop.jpg")
    details = {s.stage: s.detail for s in prog.stages}
    assert details[Stage.transcribe] == "Muse Voice Transcribe found 2 lines"
    assert details[Stage.track_hands] == "SAM 3.1 followed 2 hands across 20 frames"
    assert details[Stage.find_points] == "1 place you pointed"
    assert details[Stage.write_items] == "Muse Spark Open on Baseten wrote 1 item"

    detail = check(S.WalkDetailOut, api.get(f"/api/walks/{up['walk_id']}").json())
    assert detail.walk.status == "ready" and detail.walk.width == DISPLAY_W and detail.walk.orientation == "portrait"
    assert detail.walk.media.display_mp4 == f"/media/walks/{up['walk_id']}/display.mp4"
    assert detail.walk.counts.items == 1 and detail.walk.counts.points == 1 and detail.walk.counts.turns == 2
    assert [t.text for t in detail.turns] == ["Kitchen.", "Knob is missing on this cabinet door."]
    assert detail.turns[1].item_ids == [detail.items[0].id]
    (point,) = detail.points
    assert point.item_id == detail.items[0].id and point.item_number == 1 and point.number == 1
    assert point.hand_box.x2 == 86
    item = detail.items[0]
    assert item.point_id == point.id and item.number == 1
    assert item.video_url == detail.walk.media.display_mp4
    assert item.evidence and item.evidence.pointer and item.evidence.pointer.x == 64
    assert item.evidence.pointing_frame and item.evidence.pointing_frame.startswith("/media/walks/")

    listed = check(S.WalkListOut, api.get("/api/walks").json())
    assert [w.id for w in listed.walks] == [up["walk_id"]]
    by_project = check(S.WalkListOut, api.get(f"/api/projects/{project['id']}/walks").json())
    assert by_project.walks[0].counts.items == 1
    assert check(S.ProjectOut, api.get(f"/api/projects/{project['id']}").json()).walk_count == 1


def test_progress_callback_updates_job_while_running(api: TestClient, clip_path: Path) -> None:
    seen: list[dict] = []

    def watching_runner(walk_id: str, *, session_factory, progress=None, force=False) -> None:
        def spy(event):
            progress(event)
            with session_factory() as s:
                job = s.exec(select(Job).where(Job.walk_id == walk_id)).one()
                walk = s.get(Walk, walk_id)
                seen.append({"job": dict(job.progress), "status": job.status, "walk": walk.status})

        fake_runner(walk_id, session_factory=session_factory, progress=spy, force=force)

    jobs.set_runner(watching_runner)
    pid = make_project(api)["id"]
    upload(api, pid, [clip_path])
    process(api)
    assert seen[0]["status"] == JobStatus.running and seen[0]["walk"] == WalkStatus.processing
    assert seen[0]["job"] == {"stage": "ingest", "status": "running", "counts": {}, "message": "ingest running"}
    assert any(e["job"]["stage"] == "verify" and e["job"]["status"] == "done" for e in seen)


def test_real_stage_event_dataclass_is_accepted(api: TestClient, clip_path: Path) -> None:
    from punchwalk.pipeline.runner import StageEvent

    def runner(walk_id: str, *, session_factory, progress=None, force=False) -> None:
        progress(StageEvent(stage=Stage.ingest, status="running", counts={"clips": 1}, message="Preparing"))
        with session_factory() as s:
            job = s.exec(select(Job).where(Job.walk_id == walk_id)).one()
            assert job.progress == {"stage": "ingest", "status": "running", "counts": {"clips": 1}, "message": "Preparing"}
        fake_runner(walk_id, session_factory=session_factory, progress=None, force=force)

    jobs.set_runner(runner)
    ready_walk(api, clip_path)


def test_failed_job_retries_then_fails_readably(api: TestClient, clip_path: Path) -> None:
    attempts: list[int] = []

    def flaky(walk_id: str, *, session_factory, progress=None, force=False) -> None:
        attempts.append(1)
        raise TimeoutError("Baseten timed out")

    jobs.set_runner(flaky)
    pid = make_project(api)["id"]
    up = upload(api, pid, [clip_path]).json()
    assert process(api) == jobs.MAX_JOB_ATTEMPTS == 3
    prog = check(S.JobProgressOut, api.get(f"/api/walks/{up['walk_id']}/progress").json())
    assert prog.status == "failed" and prog.walk_status == "failed" and prog.attempts == 3
    assert prog.error == "Baseten timed out" and prog.finished_at is not None
    walk = check(S.WalkOut, api.get(f"/api/walks/{up['walk_id']}").json()["walk"])
    assert walk.error == "Baseten timed out"

    # Retry: a new job; this time the runner works.
    jobs.set_runner(fake_runner)
    res = api.post(f"/api/walks/{up['walk_id']}/retry")
    retry = check(S.JobProgressOut, res.json())
    assert retry.status == "queued" and retry.job_id != prog.job_id and retry.walk_status == "uploaded"
    assert api.post(f"/api/walks/{up['walk_id']}/retry").status_code == 409  # already queued
    process(api)
    assert api.get(f"/api/walks/{up['walk_id']}/progress").json()["status"] == "done"
    assert api.post(f"/api/walks/{up['walk_id']}/retry").status_code == 409  # ready walks need force


def test_non_retryable_errors_fail_at_once_with_the_runners_message(api: TestClient, clip_path: Path) -> None:
    from punchwalk.pipeline.media import MediaError

    def broken(walk_id: str, *, session_factory, progress=None, force=False) -> None:
        with session_factory() as s:  # like run_walk: readable error on the walk, then re-raise
            walk = s.get(Walk, walk_id)
            walk.status, walk.error = WalkStatus.failed, "Preparing the video: the clip has no video stream."
            s.add(walk)
            s.commit()
        raise MediaError("ffprobe: no video stream in 00_walk1_a.mp4")

    jobs.set_runner(broken)
    pid = make_project(api)["id"]
    up = upload(api, pid, [clip_path]).json()
    assert process(api) == 1
    prog = api.get(f"/api/walks/{up['walk_id']}/progress").json()
    assert prog["status"] == "failed" and prog["attempts"] == 1
    assert prog["error"] == "Preparing the video: the clip has no video stream."


def test_missing_runner_fails_readably(api: TestClient, clip_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    jobs.set_runner(None)
    monkeypatch.setitem(sys.modules, "punchwalk.pipeline.runner", None)  # import fails
    pid = make_project(api)["id"]
    up = upload(api, pid, [clip_path]).json()
    assert process(api) == 1
    prog = api.get(f"/api/walks/{up['walk_id']}/progress").json()
    assert prog["status"] == "failed" and "pipeline isn't installed" in prog["error"]


def test_interrupted_jobs_are_requeued_on_startup(api: TestClient, clip_path: Path) -> None:
    pid = make_project(api)["id"]
    up = upload(api, pid, [clip_path]).json()
    assert jobs.claim_next_job() == up["job_id"]  # "running", then the process dies
    assert jobs.claim_next_job() is None
    assert jobs.recover_interrupted_jobs() == 1
    assert process(api) == 1
    assert api.get(f"/api/walks/{up['walk_id']}/progress").json()["attempts"] == 2


def test_worker_threads_process_jobs_at_most_two_at_a_time(api: TestClient, clip_path: Path) -> None:
    import threading

    lock, db_lock = threading.Lock(), threading.Lock()
    state = {"in_flight": 0, "peak": 0}
    pause = threading.Event()  # time.sleep is patched out in tests; Event.wait really waits

    def slow_runner(walk_id: str, *, session_factory, progress=None, force=False) -> None:
        with lock:
            state["in_flight"] += 1
            state["peak"] = max(state["peak"], state["in_flight"])
        try:
            pause.wait(0.3)  # long enough for every free worker thread to pick up a job
            with db_lock:  # the fake runner numbers items with max()+1; keep that part serial
                fake_runner(walk_id, session_factory=session_factory, progress=progress, force=force)
        finally:
            with lock:
                state["in_flight"] -= 1

    jobs.set_runner(slow_runner)
    pid = make_project(api)["id"]
    ids = [upload(api, pid, [clip_path]).json()["walk_id"] for _ in range(5)]
    worker = jobs.JobWorker(threads=jobs.MAX_CONCURRENT_JOBS, poll_s=0.05)
    worker.start()
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            states = {api.get(f"/api/walks/{w}/progress").json()["status"] for w in ids}
            if states == {"done"}:
                break
            pause.wait(0.05)
    finally:
        worker.stop()
    assert states == {"done"}
    assert state["peak"] == jobs.MAX_CONCURRENT_JOBS == 2  # two ran together, never three
    numbers = sorted(i["number"] for i in api.get(f"/api/projects/{pid}/items").json()["items"])
    assert numbers == [1, 2, 3, 4, 5]


def test_sse_streams_progress_until_done(api: TestClient, clip_path: Path) -> None:
    _, up = ready_walk(api, clip_path)
    with api.stream("GET", f"/api/walks/{up['walk_id']}/events") as res:
        assert res.status_code == 200 and res.headers["content-type"].startswith("text/event-stream")
        body = "".join(res.iter_text())
    data = [line[6:] for line in body.splitlines() if line.startswith("data: ") and line != "data: {}"]
    final = check(S.JobProgressOut, json.loads(data[-1]))
    assert final.status == "done" and "event: end" in body
    assert api.get("/api/walks/wlk_nope/events").status_code == 404


def test_rewalk_upload_and_link(api: TestClient, clip_path: Path) -> None:
    project, first = ready_walk(api, clip_path)
    pid = project["id"]
    re = upload(api, pid, [clip_path], kind="rewalk").json()  # links to the latest first walk
    walk = check(S.WalkOut, api.get(f"/api/walks/{re['walk_id']}").json()["walk"])
    assert walk.kind == "rewalk" and walk.rewalk_of == first["walk_id"]
    assert [s.stage for s in walk.stages][-2:] == [Stage.resolve_closures, Stage.persist]

    other = upload(api, pid, [clip_path]).json()
    linked = check(S.WalkOut, api.post(f"/api/walks/{other['walk_id']}/rewalk-link",
                                       json={"initial_walk_id": first["walk_id"]}).json())  # fmt: skip
    assert linked.kind == "rewalk" and linked.rewalk_of == first["walk_id"]
    bad = api.post(f"/api/walks/{first['walk_id']}/rewalk-link", json={"initial_walk_id": first["walk_id"]})
    assert bad.status_code == 400
    summary = check(S.RewalkSummaryOut, api.get(f"/api/walks/{re['walk_id']}/rewalk-summary").json())
    assert summary.initial_walk_id == first["walk_id"] and summary.counts.checked == 0
    assert summary.headline == "0 signed off. 0 still open. 0 need a look."
    assert api.get(f"/api/walks/{first['walk_id']}/rewalk-summary").status_code == 404


def test_overlay_in_display_coordinates_and_cached(api: TestClient, clip_path: Path) -> None:
    _, up = ready_walk(api, clip_path)
    wid = up["walk_id"]
    res = api.get(f"/api/walks/{wid}/overlay")
    assert res.status_code == 200, res.text
    ov = check(S.OverlayOut, res.json())
    assert (ov.width, ov.height, ov.fps, ov.duration_s) == (DISPLAY_W, DISPLAY_H, 10.0, 2.0)
    # hold frames 8..12, shown 1.5 s (15 frames) around it -> all 20 frames of object a:1, never a:7
    assert [f.t for f in ov.frames] == [round(i / 10, 2) for i in range(20)]
    assert all(len(f.hands) == 1 for f in ov.frames)
    hand = ov.frames[10].hands[0]
    assert hand.point_id.startswith("pnt_") and hand.number == 1
    assert hand.box.x1 == pytest.approx((20 + 10 * 0.5) * 2) and hand.box.y2 == 140  # SAM px x 2
    assert hand.fingertip and (hand.fingertip.x, hand.fingertip.y) == (64.0, 84.0)  # the point's own frame
    drifted = ov.frames[12].hands[0].fingertip  # the hand moved 2 display px right; the fingertip follows
    assert drifted and (drifted.x, drifted.y) == (66.0, 84.0)
    assert ov.frames[7].hands[0].fingertip is None and ov.frames[8].hands[0].fingertip is not None
    assert ov.frames[13].hands[0].fingertip is None
    # simplified: the redundant point at (x+6.1, y+4) is dropped; integer coordinates
    assert hand.svg.count("L") < 10 and "." not in hand.svg
    assert [(e.t, e.number) for e in ov.events] == [(0.7, 1)]

    cache = Path(get_settings().DATA_DIR) / "walks" / wid / "overlay.json"
    assert cache.is_file() and json.loads(cache.read_text()) == res.json()
    cache.write_text(json.dumps({**res.json(), "fps": 99.0}))  # a stale-looking cache with a valid key
    assert api.get(f"/api/walks/{wid}/overlay").json()["fps"] == 99.0  # served from cache
    (Path(get_settings().DATA_DIR) / "walks" / wid / "stages" / "find_points.json").write_text(
        json.dumps({"version": 1, "events": []})
    )
    rebuilt = api.get(f"/api/walks/{wid}/overlay").json()  # input changed -> rebuilt
    assert rebuilt["fps"] == 10.0
    # no exact hold frames any more: hold_s (0.5 s) centered on frame 10 -> fingertip on frames 7..13
    pointing = [i for i, f in enumerate(rebuilt["frames"]) if f["hands"][0]["fingertip"]]
    assert pointing == list(range(7, 14))


def test_overlay_before_ingest_is_a_readable_409(api: TestClient, clip_path: Path) -> None:
    pid = make_project(api)["id"]
    up = upload(api, pid, [clip_path]).json()
    res = api.get(f"/api/walks/{up['walk_id']}/overlay")
    assert res.status_code == 409 and "prepared" in res.json()["detail"]


def test_overlay_payload_stays_under_3mb_for_a_3_minute_walk(api: TestClient, clip_path: Path) -> None:
    from punchwalk.api import overlay as O

    _, up = ready_walk(api, clip_path)
    wdir = Path(get_settings().DATA_DIR) / "walks" / up["walk_id"]
    # 3 minutes at 10 fps, a hand on screen the whole time, with a dense (unsimplified) outline
    import math

    records = []
    for frame in range(1800):
        cx, cy = 36 + 10 * math.sin(frame / 30), 64
        ring = [(cx + 20 * math.cos(a / 40 * 2 * math.pi), cy + 30 * math.sin(a / 40 * 2 * math.pi)) for a in range(40)]
        svg = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in ring) + " Z"
        records.append({"frame": frame, "object_id": "a:1", "box": [cx - 20, cy - 30, cx + 20, cy + 30],
                        "confidence": 0.9, "svg": svg})  # fmt: skip
    (wdir / "stages" / "track_hands.json").write_text(json.dumps({"records": records}))
    (wdir / "stages" / "find_points.json").write_text(
        json.dumps({"events": [{"id": f"pnt_{up['walk_id'][4:]}", "hold_frames": [0, 1799]}]})
    )
    size = len(api.get(f"/api/walks/{up['walk_id']}/overlay").content)
    assert size < O.MAX_PAYLOAD_BYTES, size


def test_model_calls_and_processing_summary(
    api: TestClient, clip_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PUNCHWALK_EVAL_REPORT", str(tmp_path / "no_report.json"))  # eval block: tests/test_api_eval_block.py
    _, up = ready_walk(api, clip_path)
    wid = up["walk_id"]
    calls = check(S.ModelCallListOut, api.get(f"/api/walks/{wid}/model-calls").json())
    assert [c.model_label for c in calls.calls] == [
        "Muse Voice Transcribe", "SAM 3.1", "Muse Spark Open on Baseten", "Muse Spark 1.3"
    ]  # fmt: skip
    assert calls.calls[3].provider == "meta-fallback" and calls.calls[3].item_id is not None

    proc = check(S.ProcessingSummaryOut, api.get(f"/api/walks/{wid}/processing").json())
    assert proc.status == "ready" and proc.eval is None
    assert proc.total_cost_usd == pytest.approx(0.016)
    assert proc.models_used == ["Muse Voice Transcribe", "SAM 3.1", "Muse Spark Open on Baseten"]
    steps = {s.stage: s for s in proc.steps}
    assert list(steps) == [Stage.transcribe, Stage.track_hands, Stage.fuse,
                           Stage.write_items, Stage.ground_defects, Stage.verify]  # fmt: skip
    assert steps[Stage.track_hands].detail == "1 point found across the whole video"
    assert steps[Stage.verify].fallback_calls == 1 and steps[Stage.verify].provider == "meta-fallback"
    assert steps[Stage.track_hands].model_label == "SAM 3.1" and steps[Stage.track_hands].calls == 1
    assert steps[Stage.fuse].model_label is None and steps[Stage.fuse].detail.startswith("Timing and position")
    assert proc.total_duration_ms == 10_800  # 11 stages, 1 s apart, 0.8 s each
