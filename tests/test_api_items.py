"""Punch list: filters, sort, facets, search; item edits with history; dismiss; share links."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from punchwalk.api import schemas as S
from punchwalk.config import REPO_ROOT
from punchwalk.core.enums import Channel, ItemStatus, Severity, Trade
from punchwalk.core.models import Dispatch, Item, utcnow
from punchwalk.db import session_factory
from tests import test_api_support as support
from tests.test_api_support import check, ready_walk

MOCK = REPO_ROOT / "fixtures" / "mock_api"

# shared fixtures (defined in tests/test_api_support.py)
api = support.api
clip_path = support.clip_path


def add_items(project_id: str, walk_id: str) -> list[str]:
    """Three more items next to the fake runner's #1 (carpentry, Kitchen, cosmetic, open)."""
    rows = [
        (2, "Switch plate crooked", Trade.electrical, "Hallway", Severity.cosmetic, ItemStatus.dispatched, 5.0),
        (3, "Outlet cover cracked", Trade.electrical, "Bathroom", Severity.safety, ItemStatus.needs_review, 3.0),
        (4, "Door doesn't latch", Trade.doors_hardware, "Bedroom", Severity.functional, ItemStatus.closed, 4.0),
    ]
    ids = []
    with session_factory() as s:
        for number, title, trade, room, severity, status, t in rows:
            item = Item(project_id=project_id, number=number, origin_walk_id=walk_id, title=title, trade=trade,
                        room=room, severity=severity, status=status, quote_start_s=t, video_start_s=t - 2,
                        video_end_s=t + 2, description=f"{title}.", action="Fix it",
                        review_reason="The frame doesn't show the defect described." if number == 3 else None)  # fmt: skip
            s.add(item)
            ids.append(item.id)
        s.commit()
    return ids


def items(api: TestClient, pid: str, qs: str = "") -> S.ItemListOut:
    res = api.get(f"/api/projects/{pid}/items{qs}")
    assert res.status_code == 200, res.text
    return check(S.ItemListOut, res.json())


def test_item_list_filters_sort_facets(api: TestClient, clip_path: Path) -> None:
    project, up = ready_walk(api, clip_path)
    pid = project["id"]
    add_items(pid, up["walk_id"])

    full = items(api, pid)
    assert full.total == 4 and [i.number for i in full.items] == [1, 3, 4, 2]  # walk order: quote time
    assert full.counts == S.ItemCountsOut(todo=3, open=2, needs_review=1, signed_off=1, all=4)
    assert full.facets.trade == {"electrical": 2, "carpentry_millwork": 1, "doors_hardware": 1}
    assert list(full.facets.room) == ["Kitchen", "Bathroom", "Hallway", "Bedroom"]  # project order, then others
    assert full.facets.status == {"open": 1, "dispatched": 1, "closed": 1, "needs_review": 1}

    elec = items(api, pid, "?trade=electrical")
    assert [i.number for i in elec.items] == [3, 2] and elec.total == 2
    assert elec.facets == full.facets and elec.counts == full.counts  # facets/counts are unfiltered
    assert items(api, pid, "?trade=electrical&trade=doors_hardware").total == 3  # repeat = OR
    assert [i.number for i in items(api, pid, "?status=open&status=dispatched&status=needs_review").items] == [1, 3, 2]
    assert [i.number for i in items(api, pid, "?severity=safety").items] == [3]
    assert [i.number for i in items(api, pid, "?room=Hallway").items] == [2]
    assert [i.number for i in items(api, pid, "?sort=severity").items] == [3, 4, 1, 2]
    assert [i.number for i in items(api, pid, "?q=KNOB").items] == [1]  # title/description/quote search
    assert [i.number for i in items(api, pid, "?q=%23003").items] == [3]  # "#003"
    assert items(api, pid, "?q=nothing-like-this").total == 0
    assert api.get(f"/api/projects/{pid}/items?sort=random").status_code == 422

    proj = check(S.ProjectOut, api.get(f"/api/projects/{pid}").json())
    assert proj.counts == full.counts


def test_item_out_matches_the_mock_shape(api: TestClient, clip_path: Path) -> None:
    project, _ = ready_walk(api, clip_path)
    item = items(api, project["id"]).items[0].model_dump(mode="json")
    mock = json.loads((MOCK / "items" / "itm_2b_01.json").read_text())
    assert set(item) == set(mock)
    assert set(item["evidence"]) == set(mock["evidence"])
    assert item["video_start_s"] == 0.0 and item["video_end_s"] == 2.0


def test_patch_logs_history_as_human_overrides(api: TestClient, clip_path: Path) -> None:
    project, _ = ready_walk(api, clip_path)
    item_id = items(api, project["id"]).items[0].id

    res = api.patch(f"/api/items/{item_id}", json={"trade": "general", "title": "  Missing knob  ", "room": "Kitchen"})
    out = check(S.ItemOut, res.json())
    assert out.trade == "general" and out.title == "Missing knob"
    edits = [h for h in out.history if h.actor == "human"]
    assert [(h.action, h.field, h.from_, h.to) for h in edits] == [
        ("edited", "title", "Missing cabinet knob", "Missing knob"),
        ("edited", "trade", "carpentry_millwork", "general"),
    ]  # room was unchanged -> not logged
    assert res.json()["history"][-1]["from"] == "carpentry_millwork"  # serialized with the alias
    assert edits[0].note == "Changed by the project manager."

    signed = check(S.ItemOut, api.patch(f"/api/items/{item_id}", json={"status": "closed", "note": "Saw it fixed"}).json())
    last = signed.history[-1]
    assert signed.status == "closed" and (last.action, last.field, last.from_, last.to) == ("status_changed", "status", "open", "closed")
    assert last.note == "Saw it fixed"
    reopened = check(S.ItemOut, api.patch(f"/api/items/{item_id}", json={"status": "open"}).json())
    assert reopened.history[-1].note == "Reopened by the project manager."

    review = api.patch(f"/api/items/{item_id}", json={"status": "needs_review", "review_reason": "Check the photo"})
    assert check(S.ItemOut, review.json()).review_reason == "Check the photo"
    back = check(S.ItemOut, api.patch(f"/api/items/{item_id}", json={"status": "open"}).json())
    assert back.review_reason is None  # cleared once it no longer needs a look

    cleared = check(S.ItemOut, api.patch(f"/api/items/{item_id}", json={"location_detail": None}).json())
    assert cleared.location_detail is None and cleared.history[-1].to is None

    got = check(S.ItemOut, api.get(f"/api/items/{item_id}").json())
    assert got.history == cleared.history and got.history == sorted(got.history, key=lambda h: h.at)


@pytest.mark.parametrize(
    ("body", "status", "phrase"),
    [
        ({"title": ""}, 400, "Title can't be empty"),
        ({"title": None}, 400, "Title can't be empty"),
        ({"title": "x" * 121}, 400, "too long"),
        ({"severity": "catastrophic"}, 422, "severity"),
        ({"owner": "me"}, 422, "owner"),
    ],
)
def test_patch_validation_is_readable(api: TestClient, clip_path: Path, body: dict, status: int, phrase: str) -> None:
    project, _ = ready_walk(api, clip_path)
    item_id = items(api, project["id"]).items[0].id
    res = api.patch(f"/api/items/{item_id}", json=body)
    assert res.status_code == status and phrase in res.json()["detail"], res.text


def test_dismiss_hides_the_item(api: TestClient, clip_path: Path) -> None:
    project, up = ready_walk(api, clip_path)
    pid = project["id"]
    add_items(pid, up["walk_id"])
    item_id = items(api, pid).items[0].id
    res = api.delete(f"/api/items/{item_id}")
    assert res.status_code == 204 and res.content == b""
    assert api.delete(f"/api/items/{item_id}").status_code == 204  # idempotent
    assert item_id not in {i.id for i in items(api, pid).items}
    assert items(api, pid).counts.all == 3
    assert api.get(f"/api/items/{item_id}").status_code == 404
    assert "dismissed" in api.get(f"/api/items/{item_id}").json()["detail"]
    with session_factory() as s:
        row = s.get(Item, item_id)
        assert row.dismissed_at is not None and row.history[-1]["action"] == "dismissed"
    detail = check(S.WalkDetailOut, api.get(f"/api/walks/{up['walk_id']}").json())
    assert item_id not in {i.id for i in detail.items}
    assert detail.points[0].item_id is None


def test_share_link_and_public_page(api: TestClient, clip_path: Path) -> None:
    project, _ = ready_walk(api, clip_path)
    item_id = items(api, project["id"]).items[0].id
    link = check(S.ShareLinkOut, api.post(f"/api/items/{item_id}/share").json())
    assert link.token.startswith("shr_") and len(link.token) == 14 and link.url == f"/share/item/{link.token}"
    assert api.post(f"/api/items/{item_id}/share").json() == link.model_dump()  # stable

    shared = check(S.ShareItemOut, api.get(f"/api/share/{link.token}").json())
    assert shared.project_name == "Unit 2B final punch" and shared.company == "Oakline Millwork"
    assert shared.video_url and shared.evidence and shared.walked_at is not None
    assert shared.closure is None and shared.after_at is None  # no re-walk yet
    assert api.get("/api/share/shr_0000000000").status_code == 404
    api.delete(f"/api/items/{item_id}")
    assert api.get(f"/api/share/{link.token}").status_code == 404


def test_share_after_at_is_the_rewalk_date(api: TestClient, clip_path: Path) -> None:
    """The after photo's caption date: the re-walk's recorded_at (else created_at), not closure.decided_at."""
    from datetime import UTC, datetime

    from punchwalk.core.enums import WalkKind
    from punchwalk.core.models import Walk

    project, up = ready_walk(api, clip_path)
    item_id = items(api, project["id"]).items[0].id
    token = api.post(f"/api/items/{item_id}/share").json()["token"]
    recorded = datetime(2026, 11, 3, 15, 12, tzinfo=UTC)
    uploaded = datetime(2026, 11, 3, 18, 0, tzinfo=UTC)
    with session_factory() as s:
        rw = Walk(project_id=project["id"], kind=WalkKind.rewalk, rewalk_of=up["walk_id"], recorded_at=recorded)
        s.add(rw)
        row = s.get(Item, item_id)
        row.status = ItemStatus.closed
        row.closure = {"rewalk_id": rw.id, "after_image": f"items/{item_id}/after.jpg", "decision": "signed_off",
                       "decided_at": "2026-11-04T13:21:07Z"}  # fmt: skip
        s.add(row)
        s.commit()
        rw_id = rw.id
    shared = check(S.ShareItemOut, api.get(f"/api/share/{token}").json())
    assert shared.closure and shared.closure.decided_at.day == 4
    assert shared.after_at == recorded

    with session_factory() as s:  # no recorded_at: the upload time stands in, as on the Re-walk page
        w = s.get(Walk, rw_id)
        w.recorded_at, w.created_at = None, uploaded
        s.add(w)
        s.commit()
    assert check(S.ShareItemOut, api.get(f"/api/share/{token}").json()).after_at == uploaded

    with session_factory() as s:  # a closure without an after photo has no after date
        row = s.get(Item, item_id)
        row.closure = {**row.closure, "after_image": None}
        s.add(row)
        s.commit()
    assert check(S.ShareItemOut, api.get(f"/api/share/{token}").json()).after_at is None


def test_dispatch_summary_on_items_and_trades(api: TestClient, clip_path: Path) -> None:
    project, _ = ready_walk(api, clip_path)
    pid = project["id"]
    item_id = items(api, pid).items[0].id
    with session_factory() as s:
        first = utcnow() - timedelta(hours=2)
        s.add(Dispatch(id="dsp_a", project_id=pid, trade=Trade.carpentry_millwork, item_ids=[item_id],
                       channel=Channel.email, destination="dev.patel@example.com", sent_at=first, payload={"text": "a"}))
        s.add(Dispatch(id="dsp_b", project_id=pid, trade=Trade.carpentry_millwork, item_ids=[item_id],
                       channel=Channel.outbox, destination="Outbox", sent_at=utcnow(), payload={"text": "b"}))  # fmt: skip
        s.commit()
    item = check(S.ItemOut, api.get(f"/api/items/{item_id}").json())
    assert item.dispatch and item.dispatch.dispatch_id == "dsp_b" and item.dispatch.times_sent == 2
    assert item.dispatch.company == "Oakline Millwork"
    hist = check(S.DispatchListOut, api.get(f"/api/projects/{pid}/trades/carpentry_millwork/dispatches").json())
    assert [d.id for d in hist.dispatches] == ["dsp_b", "dsp_a"]
    trades = check(S.TradeListOut, api.get(f"/api/projects/{pid}/trades").json())
    carp = next(t for t in trades.trades if t.trade == "carpentry_millwork")
    assert carp.open_count == 1 and carp.last_sent_at is not None
