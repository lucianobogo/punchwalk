"""Shared fixtures for the API tests: an app on a tmp DATA_DIR, a tiny clip, and a fake runner.

The fake runner stands in for `punchwalk.pipeline.runner.run_walk`: it emits StageEvents, writes the
files the real pipeline writes (display.mp4, meta.json, stages/track_hands.json, find_points.json,
evidence images) and inserts a Turn, a Point, an Item and a ModelCall.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from punchwalk.config import get_settings, reset_settings
from punchwalk.core.enums import (
    MODEL_ASR,
    MODEL_SAM,
    MODEL_SPARK_OPEN,
    STAGES_INITIAL,
    Grounding,
    HistoryAction,
    HistoryActor,
    ItemStatus,
    Provider,
    Severity,
    Stage,
    StageStatus,
    Trade,
    TurnKind,
    WalkStatus,
)
from punchwalk.core.models import (
    XY,
    Box,
    Evidence,
    HistoryEvent,
    Item,
    ModelCall,
    Point,
    StageState,
    Turn,
    Verification,
    Walk,
    WalkMediaPaths,
    utcnow,
)

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")

SAM_W, SAM_H = 72, 128  # sam.mp4 pixels (portrait)
DISPLAY_W, DISPLAY_H = 144, 256  # display.mp4 pixels: SAM x 2


def check(schema: type[BaseModel], data: Any) -> Any:
    """Validate a response body with its schema and require an exact round trip (no extra keys)."""
    model = schema.model_validate(data)
    assert model.model_dump(mode="json") == data, f"{schema.__name__} does not round-trip"
    return model


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from punchwalk.api import jobs
    from punchwalk.api.app import app
    from punchwalk.api.guards import upload_limiter
    from punchwalk.db import reset_engines

    monkeypatch.setenv("PUNCHWALK_WORKER", "0")
    reset_settings()
    reset_engines()
    upload_limiter.reset()
    jobs.set_runner(fake_runner)
    with TestClient(app) as client:
        yield client
    jobs.set_runner(None)
    reset_engines()


@pytest.fixture(scope="session")
def clip_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A 2-second portrait H.264 + AAC clip (144x256), made with ffmpeg."""
    out = tmp_path_factory.mktemp("clips") / "walk1_a.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc=size={DISPLAY_W}x{DISPLAY_H}:rate=10:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            "-movflags", "+faststart", str(out),
        ],
        check=True,
    )  # fmt: skip
    return out


def make_project(api: TestClient, **extra: Any) -> dict:
    body = {
        "name": "Unit 2B final punch",
        "address": "418 Alder Street, Unit 2B",
        "trades": [
            {
                "trade": "electrical",
                "company": "Ace Electric",
                "email": "maria.chen@example.com",
                "slack_channel": "#punch-electrical",
                "slack_webhook": "https://hooks.slack.com/services/T000/B000/XXXX",
            },
            {"trade": "carpentry_millwork", "company": "Oakline Millwork", "email": "dev.patel@example.com"},
        ],
        "keywords": ["Unit 2B"],
        "rooms": ["Kitchen", "Bathroom", "Hallway"],
        **extra,
    }
    res = api.post("/api/projects", json=body)
    assert res.status_code == 201, res.text
    return res.json()


def upload(api: TestClient, project_id: str, clips: list[Path], **form: str) -> Any:
    files = [("clips", (p.name, p.read_bytes(), "video/mp4")) for p in clips]
    return api.post(f"/api/projects/{project_id}/walks", data=form, files=files)


# --------------------------------------------------------------------------- the fake runner


def _data_dir() -> Path:
    return Path(get_settings().DATA_DIR)


def _hand_svg(x: float, y: float) -> str:
    # an L-shaped "hand" with a pointing finger, many redundant points along straight edges
    pts = [(x, y + 20), (x + 6, y + 20), (x + 6, y + 8), (x + 6.1, y + 4), (x + 6, y), (x + 9, y),
           (x + 9, y + 8), (x + 18, y + 8), (x + 18, y + 20), (x + 18, y + 30), (x, y + 30)]  # fmt: skip
    return "M" + " L".join(f"{a:.1f} {b:.1f}" for a, b in pts) + " Z"


def write_pipeline_files(walk: Walk, clip: Path) -> WalkMediaPaths:
    wdir = _data_dir() / "walks" / walk.id
    (wdir / "stages").mkdir(parents=True, exist_ok=True)
    shutil.copy(clip, wdir / "display.mp4")
    (wdir / "poster.jpg").write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    meta = {
        "version": 2,
        "duration_s": 2.0,
        "display": {"path": "display.mp4", "width": DISPLAY_W, "height": DISPLAY_H, "fps": 10, "frame_count": 20},
        "sam": {"path": "sam.mp4", "width": SAM_W, "height": SAM_H, "fps": 10, "frame_count": 20},
        "orientation": "portrait",
        "scale": {"sam_to_display_x": 2.0, "sam_to_display_y": 2.0},
    }
    (wdir / "meta.json").write_text(json.dumps(meta))
    records = []
    for frame in range(20):
        x = 20 + frame * 0.5
        records.append(
            {"frame": frame, "object_id": "a:1", "box": [x, 40, x + 18, 70], "confidence": 0.9,
             "svg": _hand_svg(x, 40)}  # fmt: skip
        )
    records.append({"frame": 5, "object_id": "a:7", "box": [1, 1, 5, 5], "confidence": 0.4, "svg": ""})
    track = {"version": 1, "concept": "hand", "sam": {"width": SAM_W, "height": SAM_H, "fps": 10, "frame_count": 20},
             "parts": [], "records": records}  # fmt: skip
    (wdir / "stages" / "track_hands.json").write_text(json.dumps(track))
    point_id = f"pnt_{walk.id[4:]}"
    events = [{"id": point_id, "number": 1, "t": 1.0, "frame": 10, "hold_frames": [8, 12]}]
    (wdir / "stages" / "find_points.json").write_text(json.dumps({"version": 1, "events": events}))
    return WalkMediaPaths(
        display_mp4=f"walks/{walk.id}/display.mp4",
        sam_mp4=f"walks/{walk.id}/sam.mp4",
        audio_wav=f"walks/{walk.id}/audio.wav",
        meta_json=f"walks/{walk.id}/meta.json",
        poster=f"walks/{walk.id}/poster.jpg",
    )


RUNNER_CALLS: list[dict[str, Any]] = []


def fake_runner(walk_id: str, *, session_factory, progress=None, force: bool = False) -> None:
    """Behaves like run_walk for a 2 s walk with one pointing event and one item."""
    RUNNER_CALLS.append({"walk_id": walk_id, "force": force})
    with session_factory() as s:
        walk = s.get(Walk, walk_id)
        assert walk is not None
        clip = next((_data_dir() / "walks" / walk_id / "clips").iterdir())
        media = write_pipeline_files(walk, clip)
        start = utcnow()
        stages = {}
        counts = {
            Stage.ingest: {"clips": 1, "seconds": 2},
            Stage.transcribe: {"lines": 2},
            Stage.track_hands: {"frames": 20, "hands": 2, "parts": 1},
            Stage.find_points: {"points": 1, "mentions": 1, "voice_only": 0},
            Stage.classify_turns: {"lines": 2},
            Stage.fuse: {"items": 1, "pointed": 1, "voice_only": 0},
            Stage.pick_evidence: {"photos": 1, "pointed": 1, "hand_clear": 1},
            Stage.write_items: {"written": 1, "total": 1},
            Stage.ground_defects: {"outlined": 1},
            Stage.verify: {"checked": 1, "matched": 1, "total": 1},
        }
        for i, stage in enumerate(STAGES_INITIAL):
            if progress:
                progress({"stage": stage, "status": "running", "counts": {}, "message": f"{stage} running"})
            t0 = start + timedelta(seconds=i)
            stages[stage.value] = StageState(
                stage=stage,
                status=StageStatus.done,
                started_at=t0,
                finished_at=t0 + timedelta(milliseconds=800),
                duration_ms=800,
                counts=counts.get(stage, {}),
            ).model_dump(mode="json")
            if progress:
                progress({"stage": stage, "status": "done", "counts": counts.get(stage, {}), "message": None})
        walk.stages = stages
        walk.media = media.model_dump(mode="json")
        walk.width, walk.height, walk.fps, walk.duration_s = DISPLAY_W, DISPLAY_H, 10.0, 2.0
        s.add(walk)

        turns = [
            Turn(id=f"trn_{walk_id[4:]}_0", walk_id=walk_id, idx=0, start_ms=0, end_ms=600, text="Kitchen.",
                 kind=TurnKind.location, room="Kitchen"),
            Turn(id=f"trn_{walk_id[4:]}_1", walk_id=walk_id, idx=1, start_ms=700, end_ms=1900,
                 text="Knob is missing on this cabinet door.", kind=TurnKind.defect, room="Kitchen"),
        ]  # fmt: skip
        point = Point(
            id=f"pnt_{walk_id[4:]}",
            walk_id=walk_id,
            number=1,
            t=1.0,
            frame=10,
            fingertip_x=64.0,
            fingertip_y=84.0,
            hand_box=Box(x1=50, y1=80, x2=86, y2=140).model_dump(),
            hold_s=0.5,
            sam_object_ids=["a:1"],
            mean_conf=0.9,
            turn_id=turns[1].id,
        )
        existing = s.exec(_numbers(walk.project_id)).all()
        number = max(existing, default=0) + 1
        idir = f"walks/{walk_id}/items/itm_{walk_id[4:]}"
        (_data_dir() / idir).mkdir(parents=True, exist_ok=True)
        for name in ("evidence.jpg", "raw.jpg", "crop.jpg", "pointing.jpg"):
            (_data_dir() / idir / name).write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
        item = Item(
            id=f"itm_{walk_id[4:]}",
            project_id=walk.project_id,
            number=number,
            origin_walk_id=walk_id,
            point_id=point.id,
            turn_ids=[turns[1].id],
            title="Missing cabinet knob",
            description="The upper cabinet door left of the sink has no knob.",
            action="Install a matching knob",
            trade=Trade.carpentry_millwork,
            room="Kitchen",
            location_detail="Upper cabinet left of the sink",
            severity=Severity.cosmetic,
            status=ItemStatus.open,
            quote=turns[1].text,
            quote_start_s=0.7,
            quote_end_s=1.9,
            video_start_s=0.0,
            video_end_s=2.0,
            confidence=0.88,
            evidence=Evidence(
                t=1.0,
                image=f"{idir}/evidence.jpg",
                raw=f"{idir}/raw.jpg",
                crop=f"{idir}/crop.jpg",
                width=DISPLAY_W,
                height=DISPLAY_H,
                defect_svg="M60 70 L80 70 L80 90 L60 90 Z",
                defect_box=Box(x1=60, y1=70, x2=80, y2=90),
                crop_box=Box(x1=0, y1=40, x2=144, y2=148),
                pointer=XY(x=64, y=84),
                pointing_frame=f"{idir}/pointing.jpg",
                hand_box=Box(x1=50, y1=80, x2=86, y2=140),
                grounding=Grounding.sam_concept,
                sam_concept="cabinet knob hole",
            ).model_dump(mode="json"),
            verification=Verification(consistent=True, note="Matches the photo and what was said.",
                                      provider=Provider.baseten, checked_at=utcnow()).model_dump(mode="json"),
            history=[
                HistoryEvent(at=utcnow(), actor=HistoryActor.model, action=HistoryAction.created,
                             field="status", to="open", note="Written by Muse Spark Open on Baseten.")
                .model_dump(mode="json", by_alias=True)
            ],
        )  # fmt: skip
        s.add_all([*turns, point])
        s.flush()
        s.add(item)
        for stage, model, provider, cost in (
            (Stage.transcribe, MODEL_ASR, Provider.meta, 0.001),
            (Stage.track_hands, MODEL_SAM, Provider.meta, 0.01),
            (Stage.write_items, MODEL_SPARK_OPEN, Provider.baseten, 0.002),
            (Stage.verify, "muse-spark-1.3", Provider.meta_fallback, 0.003),
        ):
            s.add(ModelCall(walk_id=walk_id, stage=stage, model=model, provider=provider, latency_ms=1200,
                            est_cost_usd=cost, item_id=item.id if stage == Stage.verify else None))  # fmt: skip
        walk.status = WalkStatus.ready
        s.add(walk)
        s.commit()


def _numbers(project_id: str):
    from sqlmodel import select

    return select(Item.number).where(Item.project_id == project_id)


def process(api: TestClient) -> int:
    from punchwalk.api.jobs import run_pending_jobs_once

    return run_pending_jobs_once()


def ready_walk(api: TestClient, clip: Path) -> tuple[dict, dict]:
    """A project with one uploaded + processed walk. Returns (project, upload response)."""
    project = make_project(api)
    res = upload(api, project["id"], [clip])
    assert res.status_code == 201, res.text
    assert process(api) == 1
    return project, res.json()
