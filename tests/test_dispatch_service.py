"""Dispatch (PLAN 6.1): message copy, channel policy, preview, sending over Slack / email / Outbox."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from punchwalk.api import schemas as S
from punchwalk.config import reset_settings
from punchwalk.dispatch import mail
from tests import test_api_support as support
from tests.test_api_support import check
from tests.test_dispatch_support import (
    WEBHOOK,
    clear_channel_env,
    fixture_json,
    fixture_text,
    item_row,
    make_project,
    seed_walk,
)

api = support.api  # shared fixture (tests/test_api_support.py)


@pytest.fixture(autouse=True)
def _no_real_channels(monkeypatch: pytest.MonkeyPatch, api: TestClient) -> None:
    clear_channel_env(monkeypatch)


@pytest.fixture
def public_base(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_BASE_URL", "https://punchwalk.example.com")
    reset_settings()


def preview(api: TestClient, pid: str, trade: str, qs: str = "") -> S.DispatchPreviewOut:
    res = api.get(f"/api/projects/{pid}/trades/{trade}/preview{qs}")
    assert res.status_code == 200, res.text
    return check(S.DispatchPreviewOut, res.json())


def test_preview_lists_open_items_safety_first_and_suggests_slack(api: TestClient, public_base) -> None:
    pid = make_project(api)["id"]
    seed_walk(pid)
    p = preview(api, pid, "electrical")
    # open #2 and #3 (needs-a-look #4 waits, signed-off #6 is done); safety first
    assert [ln.number for ln in p.lines] == [3, 2]
    assert p.channel == "slack" and p.heading == "Unit 2B final punch: 2 electrical items" == p.subject
    assert [(c.channel, c.available, c.destination) for c in p.channels] == [
        ("slack", True, "#punch-electrical"),
        ("email", False, "maria.chen@example.com"),
        ("outbox", True, "Outbox"),
    ]
    assert p.channels[1].reason == "Email sending isn't set up on this server yet."
    assert p.lines[0].line == "Bedroom, left of the bed. Replace the cover."
    assert p.lines[1].line == "Hallway, by the hall closet. Straighten and tighten the plate."
    assert p.lines[0].url.startswith("/share/item/shr_") and p.contact and p.contact.has_slack_webhook
    assert p.message_text == (
        "Unit 2B final punch: 2 electrical items\n\n"
        f"3. Outlet cover cracked (safety)\nBedroom, left of the bed. Replace the cover.\n"
        f"View item and video: https://punchwalk.example.com{p.lines[0].url}\n\n"
        f"2. Switch plate crooked\nHallway, by the hall closet. Straighten and tighten the plate.\n"
        f"View item and video: https://punchwalk.example.com{p.lines[1].url}\n"
    )
    assert p.slack_text.startswith("*Unit 2B final punch: 2 electrical items*\n\n*3*  Outlet cover cracked (safety)\n")
    assert f"<https://punchwalk.example.com{p.lines[1].url}|View item and video>" in p.slack_text
    # share links are stable across previews
    assert preview(api, pid, "electrical").lines[0].url == p.lines[0].url


def test_channel_policy(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    pid = make_project(api, webhook=None)["id"]
    seed_walk(pid)
    p = preview(api, pid, "electrical")
    assert p.channel == "outbox" and p.channels[0].reason == "No Slack channel set up for Ace Electric."
    glass = preview(api, pid, "windows_glazing")
    assert glass.channels[1].reason == "No email on file for Clearview Glass." and glass.channel == "outbox"
    # the server-wide webhook is the fallback for every trade
    monkeypatch.setenv("SLACK_WEBHOOK_URL", WEBHOOK)
    reset_settings()
    glass = preview(api, pid, "windows_glazing")
    assert glass.channel == "slack" and glass.channels[0].destination == "Slack"
    # email becomes the pick when Resend is configured and Slack isn't
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "")
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("EMAIL_FROM", "Punchwalk <punch@example.com>")
    reset_settings()
    carp = preview(api, pid, "carpentry_millwork")
    assert carp.channel == "email" and carp.channels[1].available
    # ?channel= picks another available channel, and ignores an unavailable one
    assert preview(api, pid, "carpentry_millwork", "?channel=outbox").channel == "outbox"
    assert preview(api, pid, "carpentry_millwork", "?channel=slack").channel == "email"
    # a trade with nothing open has an empty preview
    empty = preview(api, pid, "plumbing")
    assert empty.lines == [] and empty.heading == "Unit 2B final punch: 0 plumbing items"


@respx.mock
def test_send_over_slack_marks_items_and_logs_history(api: TestClient, public_base) -> None:
    route = respx.post(WEBHOOK).mock(return_value=httpx.Response(200, text=fixture_text("slack_ok.txt")))
    pid = make_project(api)["id"]
    _, ids = seed_walk(pid)
    p = preview(api, pid, "electrical")
    res = api.post(f"/api/projects/{pid}/trades/electrical/dispatch", json={"channel": "slack", "item_ids": p.item_ids})
    assert res.status_code == 201, res.text
    out = check(S.DispatchOut, res.json())
    assert out.ok and out.channel == "slack" and out.destination == "#punch-electrical" and out.company == "Ace Electric"
    assert out.item_ids == p.item_ids

    sent = json.loads(route.calls.last.request.content)
    assert sent["text"] == p.slack_text  # exactly what the preview showed
    assert sent["blocks"][0] == {"type": "section", "text": {"type": "mrkdwn", "text": f"*{p.heading}*"}}
    assert len(sent["blocks"]) == 3 and sent["blocks"][1]["accessory"]["type"] == "image"
    assert sent["blocks"][1]["accessory"]["image_url"].startswith("https://punchwalk.example.com/media/walks/")
    assert out.payload["text"] == sent["text"] and out.payload["blocks"] == sent["blocks"]
    assert out.payload["links"] == [f"https://punchwalk.example.com{ln.url}" for ln in p.lines]

    for item_id in (ids[1], ids[2]):
        got = check(S.ItemOut, api.get(f"/api/items/{item_id}").json())
        assert got.status == "dispatched"
        last = got.history[-1]
        assert (last.actor, last.action, last.from_, last.to) == ("human", "dispatched", "open", "dispatched")
        assert last.note == "Sent to Ace Electric in Slack (#punch-electrical)."
        assert got.dispatch and got.dispatch.dispatch_id == out.id and got.dispatch.times_sent == 1

    # dispatched-but-open items are still offered, and a second send says "again"
    again = api.post(f"/api/projects/{pid}/trades/electrical/dispatch", json={"channel": "outbox", "item_ids": [ids[1]]})
    assert again.status_code == 201
    got = check(S.ItemOut, api.get(f"/api/items/{ids[1]}").json())
    assert got.history[-1].note == "Sent again to Ace Electric from the outbox." and got.dispatch.times_sent == 2
    hist = check(S.DispatchListOut, api.get(f"/api/projects/{pid}/trades/electrical/dispatches").json())
    assert [d.channel for d in hist.dispatches] == ["outbox", "slack"]
    trades = check(S.TradeListOut, api.get(f"/api/projects/{pid}/trades").json())
    assert next(t for t in trades.trades if t.trade == "electrical").last_sent_at is not None


def test_no_image_blocks_when_the_app_is_not_public(api: TestClient) -> None:
    from punchwalk.dispatch.message import build_message

    pid = make_project(api)["id"]
    _, ids = seed_walk(pid)
    msg = build_message("P", item_row(ids[1]).trade, "Ace Electric", [item_row(ids[1])])
    assert all("accessory" not in b for b in msg.slack_blocks())  # http://localhost can't be fetched by Slack


@respx.mock
@pytest.mark.parametrize(("status", "body", "phrase"), [(404, "slack_no_service.txt", "no_service"),
                                                         (400, "slack_invalid_payload.txt", "invalid_payload")])  # fmt: skip
def test_slack_failure_is_a_readable_502_and_nothing_is_marked(api: TestClient, status, body, phrase) -> None:
    respx.post(WEBHOOK).mock(return_value=httpx.Response(status, text=fixture_text(body)))
    pid = make_project(api)["id"]
    _, ids = seed_walk(pid)
    res = api.post(f"/api/projects/{pid}/trades/electrical/dispatch", json={"channel": "slack", "item_ids": [ids[1]]})
    assert res.status_code == 502
    err = res.json()
    assert err["error"] == "channel_failed" and phrase in err["detail"] and "Nothing was marked as sent" in err["detail"]
    assert item_row(ids[1]).status == "open"
    hist = check(S.DispatchListOut, api.get(f"/api/projects/{pid}/trades/electrical/dispatches").json())
    (failed,) = hist.dispatches
    assert not failed.ok and phrase in (failed.error or "") and failed.payload["text"].startswith("*Unit 2B")
    assert check(S.ItemOut, api.get(f"/api/items/{ids[1]}").json()).dispatch is None  # failed sends don't count


@respx.mock
def test_slack_unreachable_is_a_readable_502(api: TestClient) -> None:
    respx.post(WEBHOOK).mock(side_effect=httpx.ConnectError("boom"))
    pid = make_project(api)["id"]
    _, ids = seed_walk(pid)
    res = api.post(f"/api/projects/{pid}/trades/electrical/dispatch", json={"channel": "slack", "item_ids": [ids[2]]})
    assert res.status_code == 502 and "couldn't be reached" in res.json()["detail"]


def test_outbox_send_and_refusals(api: TestClient) -> None:
    pid = make_project(api, webhook=None)["id"]
    _, ids = seed_walk(pid)
    url = f"/api/projects/{pid}/trades/electrical/dispatch"
    out = check(S.DispatchOut, api.post(url, json={"channel": "outbox", "item_ids": [ids[2]]}).json())
    assert out.channel == "outbox" and out.destination == "Outbox"
    assert set(out.payload) == {"subject", "text", "links"} and out.payload["links"][0].startswith("http://localhost")
    assert item_row(ids[2]).history[-1]["note"] == "Sent to Ace Electric from the outbox."

    def refused(body: dict, status: int, phrase: str) -> None:
        res = api.post(url, json=body)
        assert res.status_code == status and phrase in res.json()["detail"], res.text

    refused({"channel": "slack", "item_ids": [ids[1]]}, 400, "No Slack channel set up for Ace Electric.")
    refused({"channel": "outbox", "item_ids": [ids[3]]}, 409, "Item #004 needs a look first")
    refused({"channel": "outbox", "item_ids": [ids[5]]}, 409, "Item #006 is signed off")
    refused({"channel": "outbox", "item_ids": [ids[0]]}, 400, "belongs to Carpentry & millwork, not Electrical")
    refused({"channel": "outbox", "item_ids": ["itm_nope"]}, 404, "isn't in this project")
    refused({"channel": "outbox", "item_ids": []}, 422, "item_ids")


def test_email_through_resend(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from punchwalk.dispatch import channels

    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("EMAIL_FROM", "Punchwalk <punch@example.com>")
    reset_settings()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=fixture_json("resend_ok.json"))

    monkeypatch.setattr(channels, "transport", httpx.MockTransport(handler))
    assert mail.configured()
    pid = make_project(api)["id"]
    _, ids = seed_walk(pid)
    url = f"/api/projects/{pid}/trades/carpentry_millwork/dispatch"
    out = check(S.DispatchOut, api.post(url, json={"channel": "email", "item_ids": [ids[0]]}).json())
    assert out.channel == "email" and out.destination == "dev.patel@example.com"
    (req,) = seen
    assert str(req.url) == mail.RESEND_URL and req.headers["authorization"] == "Bearer re_test_key"
    body = json.loads(req.content)
    assert body["to"] == ["dev.patel@example.com"] and body["subject"] == "Unit 2B final punch: 1 carpentry & millwork item"
    assert "View item and video" in body["html"] and body["text"] == out.payload["text"]
    assert item_row(ids[0]).history[-1]["note"] == "Sent to Oakline Millwork by email (dev.patel@example.com)."

    monkeypatch.setattr(
        channels, "transport",
        httpx.MockTransport(lambda r: httpx.Response(422, json=fixture_json("resend_validation_error.json"))),
    )  # fmt: skip
    _, more = seed_walk_second(pid)
    res = api.post(url, json={"channel": "email", "item_ids": [more]})
    assert res.status_code == 502 and "valid email address" in res.json()["detail"]


def seed_walk_second(pid: str) -> tuple[str, str]:
    from punchwalk.core.enums import Trade
    from punchwalk.core.models import Item, Walk
    from punchwalk.db import session_factory

    with session_factory() as s:
        walk = Walk(project_id=pid, status="ready")
        s.add(walk)
        s.flush()
        item = Item(project_id=pid, number=50, origin_walk_id=walk.id, title="Gap under baseboard",
                    trade=Trade.carpentry_millwork, room="Hallway", action="Caulk the gap")  # fmt: skip
        s.add(item)
        s.commit()
        return walk.id, item.id


def test_approve_and_send(api: TestClient) -> None:
    pid = make_project(api, webhook=None)["id"]
    _, ids = seed_walk(pid)
    res = api.post(f"/api/items/{ids[3]}/approve-and-send")
    assert res.status_code == 201, res.text
    out = check(S.DispatchOut, res.json())
    assert out.item_ids == [ids[3]] and out.channel == "outbox"
    item = check(S.ItemOut, api.get(f"/api/items/{ids[3]}").json())
    assert item.status == "dispatched" and item.review_reason is None
    assert [(h.action, h.from_, h.to) for h in item.history[-2:]] == [
        ("status_changed", "needs_review", "open"),
        ("dispatched", "open", "dispatched"),
    ]
    assert item.history[-2].note == "Approved by the project manager."
    assert api.post(f"/api/items/{ids[5]}/approve-and-send").status_code == 409  # signed off
