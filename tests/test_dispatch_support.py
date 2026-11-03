"""Shared setup for dispatch and report tests: a project with a trades directory and a few items.

No ffmpeg needed: rows are written straight to the database. The fake Slack webhook URL is caught by
respx (tests are offline; conftest blocks real sockets).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from punchwalk.config import REPO_ROOT, get_settings
from punchwalk.core.enums import (
    HistoryAction,
    HistoryActor,
    ItemStatus,
    Provider,
    Severity,
    Trade,
    WalkKind,
    WalkStatus,
)
from punchwalk.core.models import Evidence, HistoryEvent, Item, Verification, Walk, utcnow
from punchwalk.db import session_factory

FIXTURES = REPO_ROOT / "fixtures" / "dispatch"
WEBHOOK = "https://hooks.slack.com/services/T0TEST/B0TEST/abcdefabcdef"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text()


def fixture_json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def make_project(api: TestClient, *, webhook: str | None = WEBHOOK) -> dict:
    trades = [
        {
            "trade": "electrical",
            "company": "Ace Electric",
            "contact": "Maria Chen",
            "email": "maria.chen@example.com",
            "slack_channel": "#punch-electrical",
            "slack_webhook": webhook,
        },
        {"trade": "carpentry_millwork", "company": "Oakline Millwork", "email": "dev.patel@example.com"},
        {"trade": "windows_glazing", "company": "Clearview Glass"},
    ]
    res = api.post(
        "/api/projects",
        json={"name": "Unit 2B final punch", "address": "418 Alder Street, Unit 2B", "trades": trades,
              "rooms": ["Kitchen", "Bathroom", "Bedroom", "Hallway"]},  # fmt: skip
    )
    assert res.status_code == 201, res.text
    return res.json()


def _evidence(walk_id: str, item_id: str, with_files: bool) -> dict:
    rel = f"walks/{walk_id}/items/{item_id}"
    if with_files:
        from PIL import Image

        d = Path(get_settings().DATA_DIR) / rel
        d.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (400, 300), (218, 211, 200)).save(d / "crop.jpg", quality=80)
        Image.new("RGB", (540, 960), (218, 211, 200)).save(d / "evidence.jpg", quality=80)
    return Evidence(
        t=10.0, image=f"{rel}/evidence.jpg", raw=f"{rel}/raw.jpg", crop=f"{rel}/crop.jpg", width=540, height=960
    ).model_dump(mode="json")


# number, title, trade, room, detail, action, severity, status, consistent
ROWS: list[tuple] = [
    (1, "Missing cabinet knob", Trade.carpentry_millwork, "Kitchen", "Upper cabinet left of the sink",
     "Install a matching knob", Severity.cosmetic, ItemStatus.open, True),
    (2, "Switch plate crooked", Trade.electrical, "Hallway", "By the hall closet",
     "Straighten and tighten the plate", Severity.cosmetic, ItemStatus.open, True),
    (3, "Outlet cover cracked", Trade.electrical, "Bedroom", "Left of the bed",
     "Replace the cover", Severity.safety, ItemStatus.open, True),
    (4, "Bulb out above the sink", Trade.electrical, "Kitchen", None,
     "Replace the bulb", Severity.functional, ItemStatus.needs_review, False),
    (5, "Window screen missing", Trade.windows_glazing, "Bedroom", None,
     "Install the screen", Severity.functional, ItemStatus.open, True),
    (6, "Door doesn't latch", Trade.electrical, "Bedroom", None,
     "Adjust the strike plate", Severity.functional, ItemStatus.closed, True),
]  # fmt: skip


def seed_walk(project_id: str, *, with_files: bool = False, kind: WalkKind = WalkKind.initial) -> tuple[str, list[str]]:
    """A ready walk with the ROWS items. Returns (walk_id, item ids in ROWS order)."""
    ids = []
    with session_factory() as s:
        walk = Walk(project_id=project_id, kind=kind, status=WalkStatus.ready, duration_s=120.0)
        s.add(walk)
        s.flush()
        for number, title, trade, room, detail, action, severity, status, consistent in ROWS:
            item = Item(project_id=project_id, number=number, origin_walk_id=walk.id, title=title, trade=trade,
                        room=room, location_detail=detail, action=action, severity=severity, status=status,
                        description=f"{title}.", quote=f"{title}, right here.", quote_start_s=float(number * 10),
                        video_start_s=float(number * 10 - 3), video_end_s=float(number * 10 + 3))  # fmt: skip
            item.evidence = _evidence(walk.id, item.id, with_files)
            item.verification = Verification(
                consistent=consistent,
                note="Matches the photo and what was said." if consistent else "The photo doesn't show a bulb.",
                provider=Provider.baseten,
            ).model_dump(mode="json")
            if status == ItemStatus.needs_review:
                item.review_reason = "The frame doesn't show the defect described."
            item.history = [
                HistoryEvent(at=utcnow(), actor=HistoryActor.model, action=HistoryAction.created, field="status",
                             to="open", note="Written by Muse Spark Open on Baseten.").model_dump(mode="json", by_alias=True)
            ]  # fmt: skip
            s.add(item)
            ids.append(item.id)
        s.commit()
        return walk.id, ids


def item_row(item_id: str) -> Item:
    with session_factory() as s:
        item = s.get(Item, item_id)
        assert item is not None
        return item


def clear_channel_env(monkeypatch) -> None:
    """A developer's .env may hold real Slack/Resend keys; dispatch tests never use them."""
    from punchwalk.config import reset_settings

    for name in ("SLACK_WEBHOOK_URL", "RESEND_API_KEY", "EMAIL_FROM"):
        monkeypatch.setenv(name, "")
    reset_settings()
