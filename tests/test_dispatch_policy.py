"""Auto-send policy (PLAN 6.2) and the re-walk re-send seam (PLAN 7.4)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import select

from punchwalk.api import jobs
from punchwalk.config import reset_settings
from punchwalk.core.enums import ClosureDecision, ItemStatus, WalkKind
from punchwalk.core.models import Closure, Dispatch, Item
from punchwalk.db import session_factory
from punchwalk.dispatch.policy import auto_dispatch_after_processing, resend_open_items
from tests import test_api_support as support
from tests.test_dispatch_support import WEBHOOK, clear_channel_env, item_row, make_project, seed_walk

api = support.api
clip_path = support.clip_path


@pytest.fixture(autouse=True)
def _auto_send_on(monkeypatch: pytest.MonkeyPatch, api: TestClient) -> None:
    monkeypatch.setenv("AUTO_SEND", "1")
    clear_channel_env(monkeypatch)  # also resets Settings


def run_policy(walk_id: str):
    with session_factory() as s:
        return auto_dispatch_after_processing(s, walk_id)


def notes(item_id: str) -> list[str]:
    return [h.get("note") for h in item_row(item_id).history]


@respx.mock
def test_verified_items_go_out_and_needs_a_look_items_wait(api: TestClient) -> None:
    route = respx.post(WEBHOOK).mock(return_value=httpx.Response(200, text="ok"))
    pid = make_project(api)["id"]
    walk_id, ids = seed_walk(pid)
    report = run_policy(walk_id)

    # electrical #2 #3 -> Slack; carpentry #1 and glazing #5 -> Copy link (no Slack, no Resend)
    assert sorted(report.sent_items) == sorted([ids[0], ids[1], ids[2], ids[4]])
    assert report.held == [ids[3]] and report.failed == {}
    assert route.call_count == 1
    assert json.loads(route.calls.last.request.content)["text"].startswith("*Unit 2B final punch: 2 electrical items*")
    for i in (0, 1, 2, 4):
        item = item_row(ids[i])
        assert item.status == ItemStatus.dispatched
        last = item.history[-1]
        assert last["actor"] == "system" and last["action"] == "dispatched"
        assert last["note"].endswith(": checked against the photo and the quote.")
    assert item_row(ids[1]).history[-1]["note"] == (
        "Posted 2 items to #punch-electrical automatically: checked against the photo and the quote."
    )
    assert item_row(ids[0]).history[-1]["note"] == (
        "Copied a link for 1 item automatically: checked against the photo and the quote."
    )
    held = item_row(ids[3])
    assert held.status == ItemStatus.needs_review
    assert held.history[-1]["note"] == "Held for a look: The frame doesn't show the defect described."
    assert item_row(ids[5]).status == ItemStatus.closed and len(item_row(ids[5]).history) == 1  # untouched


@respx.mock
def test_auto_send_is_idempotent(api: TestClient) -> None:
    route = respx.post(WEBHOOK).mock(return_value=httpx.Response(200, text="ok"))
    pid = make_project(api)["id"]
    walk_id, ids = seed_walk(pid)
    run_policy(walk_id)
    before = {i: len(item_row(i).history) for i in ids}
    again = run_policy(walk_id)
    assert again.sent == {} and again.held == [ids[3]]
    assert {i: len(item_row(i).history) for i in ids} == before  # no duplicate notes
    assert route.call_count == 1
    with session_factory() as s:
        assert len(s.exec(select(Dispatch)).all()) == 3  # electrical, carpentry, glazing


def test_unverified_open_items_are_held(api: TestClient) -> None:
    pid = make_project(api, webhook=None)["id"]
    walk_id, ids = seed_walk(pid)
    with session_factory() as s:
        item = s.get(Item, ids[0])
        item.verification = None
        s.add(item)
        s.commit()
    report = run_policy(walk_id)
    assert ids[0] in report.held and item_row(ids[0]).status == ItemStatus.open
    assert notes(ids[0])[-1] == "Held for a look: it hasn't been checked against its photo yet."


@respx.mock
def test_slack_failure_during_auto_send_leaves_items_open_with_a_note(api: TestClient) -> None:
    respx.post(WEBHOOK).mock(return_value=httpx.Response(404, text="no_service"))
    pid = make_project(api)["id"]
    walk_id, ids = seed_walk(pid)
    report = run_policy(walk_id)
    assert "electrical" in report.failed and ids[0] in report.sent_items
    assert item_row(ids[1]).status == ItemStatus.open
    assert notes(ids[1])[-1].startswith("Not sent automatically: Slack didn't accept the message (no_service).")


def test_auto_send_off_and_rewalks_skip(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    pid = make_project(api, webhook=None)["id"]
    walk_id, ids = seed_walk(pid)
    monkeypatch.setenv("AUTO_SEND", "0")
    reset_settings()
    assert run_policy(walk_id).sent == {}
    monkeypatch.setenv("AUTO_SEND", "1")
    reset_settings()
    rewalk_id, _ = seed_walk_rewalk(pid)
    assert run_policy(rewalk_id).sent == {}
    assert item_row(ids[0]).status == ItemStatus.open


def seed_walk_rewalk(pid: str):
    from punchwalk.core.models import Walk

    with session_factory() as s:
        w = Walk(project_id=pid, kind=WalkKind.rewalk, status="ready")
        s.add(w)
        s.commit()
        return w.id, []


def test_job_worker_auto_sends_after_a_first_walk(api: TestClient, clip_path: Path) -> None:
    pid = make_project(api, webhook=None)["id"]
    up = support.upload(api, pid, [clip_path]).json()
    assert jobs.run_pending_jobs_once() == 1
    items = api.get(f"/api/projects/{pid}/items").json()["items"]
    (item,) = items
    assert item["status"] == "dispatched"  # the fake runner's item passed its self-check
    assert item["history"][-1]["note"].startswith("Copied a link for 1 item automatically")
    assert item["dispatch"]["channel"] == "outbox"
    assert api.get(f"/api/walks/{up['walk_id']}/progress").json()["status"] == "done"


def test_resend_open_items_after_a_rewalk(api: TestClient) -> None:
    pid = make_project(api, webhook=None)["id"]
    walk_id, ids = seed_walk(pid)
    run_policy(walk_id)
    rewalk_id, _ = seed_walk_rewalk(pid)
    with session_factory() as s:
        for idx, decision in ((1, ClosureDecision.still_open), (2, ClosureDecision.signed_off)):
            item = s.get(Item, ids[idx])
            item.closure = Closure(rewalk_id=rewalk_id, decision=decision).model_dump(mode="json")
            if decision == ClosureDecision.signed_off:
                item.status = ItemStatus.closed
            s.add(item)
        s.commit()
        report = resend_open_items(s, pid, rewalk_id)
    assert report.sent_items == [ids[1]]
    assert notes(ids[1])[-1] == "Copied a link for 1 item again after the re-walk: still open."


def test_resend_after_a_rewalk_sends_once_per_closure(api: TestClient) -> None:
    from punchwalk.core.models import utcnow

    pid = make_project(api, webhook=None)["id"]
    walk_id, ids = seed_walk(pid)
    run_policy(walk_id)
    rewalk_id, _ = seed_walk_rewalk(pid)

    def close_still_open(decided_at) -> None:
        with session_factory() as s:
            item = s.get(Item, ids[1])
            item.closure = Closure(
                rewalk_id=rewalk_id, decision=ClosureDecision.still_open, decided_at=decided_at
            ).model_dump(mode="json")
            s.add(item)
            s.commit()

    def resend() -> list[str]:
        with session_factory() as s:
            return resend_open_items(s, pid, rewalk_id).sent_items

    def dispatch_count() -> int:
        with session_factory() as s:
            return len(s.exec(select(Dispatch)).all())

    close_still_open(utcnow())
    assert resend() == [ids[1]]
    sent = dispatch_count()
    assert resend() == [] and dispatch_count() == sent  # a second call sends nothing
    assert sum(n.endswith("again after the re-walk: still open.") for n in notes(ids[1])) == 1
    # the re-walk processed again writes a new closure: that one is re-sent (once)
    close_still_open(utcnow())
    assert resend() == [ids[1]] and resend() == []


def test_auto_send_off_also_skips_the_rewalk_resend(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    pid = make_project(api, webhook=None)["id"]
    walk_id, ids = seed_walk(pid)
    run_policy(walk_id)
    rewalk_id, _ = seed_walk_rewalk(pid)
    with session_factory() as s:
        item = s.get(Item, ids[1])
        item.closure = Closure(rewalk_id=rewalk_id, decision=ClosureDecision.still_open).model_dump(mode="json")
        s.add(item)
        s.commit()
    monkeypatch.setenv("AUTO_SEND", "false")
    reset_settings()
    with session_factory() as s:
        assert resend_open_items(s, pid, rewalk_id).sent_items == []

