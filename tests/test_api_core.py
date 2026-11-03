"""API basics: health, projects, readable errors, guards, media range requests, the PWA fallback."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from punchwalk.api import schemas as S
from punchwalk.config import reset_settings
from tests import test_api_support as support
from tests.test_api_support import check, make_project, ready_walk, upload

# shared fixtures (defined in tests/test_api_support.py)
api = support.api
clip_path = support.clip_path


def assert_readable_error(res, status: int) -> dict:
    assert res.status_code == status, res.text
    body = res.json()
    assert set(body) == {"error", "detail"}, body
    assert isinstance(body["detail"], str) and len(body["detail"]) > 10
    return body


def test_healthz(api: TestClient) -> None:
    res = api.get("/healthz")
    assert res.status_code == 200
    health = check(S.HealthOut, res.json())
    assert health.ok and health.reasoning_provider == "baseten" and health.demo_mode is False
    assert res.json()["time"].endswith("Z")


def test_database_is_sqlite_wal_under_data_dir(api: TestClient) -> None:
    from sqlalchemy import text

    from punchwalk.db import db_path, get_engine

    assert db_path().is_file()
    with get_engine().connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"


def test_project_create_get_list_patch(api: TestClient) -> None:
    created = make_project(api)
    project = check(S.ProjectOut, created)
    assert project.walk_count == 0 and project.counts.all == 0
    ace = next(t for t in project.trades if t.trade == "electrical")
    assert ace.has_slack_webhook and "slack_webhook" not in created["trades"][0]

    got = check(S.ProjectOut, api.get(f"/api/projects/{project.id}").json())
    assert got.name == "Unit 2B final punch" and got.rooms == ["Kitchen", "Bathroom", "Hallway"]

    listed = check(S.ProjectListOut, api.get("/api/projects").json())
    assert [p.id for p in listed.projects] == [project.id]

    # Replacing the directory without a webhook keeps the stored one (the UI never sees it).
    trades = [{"trade": "electrical", "company": "Ace Electric Co", "email": "maria.chen@example.com"}]
    res = api.patch(f"/api/projects/{project.id}", json={"name": "Unit 2B punch", "trades": trades})
    patched = check(S.ProjectOut, res.json())
    assert patched.name == "Unit 2B punch" and patched.rooms == got.rooms
    assert patched.trades[0].company == "Ace Electric Co" and patched.trades[0].has_slack_webhook

    trades[0]["slack_webhook"] = ""
    res = api.patch(f"/api/projects/{project.id}", json={"trades": trades})
    assert check(S.ProjectOut, res.json()).trades[0].has_slack_webhook is False


def test_trades_directory_endpoint(api: TestClient) -> None:
    project = make_project(api)
    out = check(S.TradeListOut, api.get(f"/api/projects/{project['id']}/trades").json())
    assert [t.trade for t in out.trades] == ["electrical", "carpentry_millwork"]
    assert out.trades[0].contact and out.trades[0].contact.company == "Ace Electric"
    hist = api.get(f"/api/projects/{project['id']}/trades/electrical/dispatches")
    assert check(S.DispatchListOut, hist.json()).dispatches == []


def test_dispatch_and_report_are_501_with_readable_errors(api: TestClient) -> None:
    pid = make_project(api)["id"]
    body = assert_readable_error(api.get(f"/api/projects/{pid}/trades/electrical/preview"), 501)
    assert body["error"] == "not_implemented" and "6.1" in body["detail"]
    send = api.post(f"/api/projects/{pid}/trades/electrical/dispatch", json={"channel": "outbox", "item_ids": ["x"]})
    assert_readable_error(send, 501)
    assert_readable_error(api.get(f"/api/projects/{pid}/report.pdf"), 501)


def test_errors_are_readable(api: TestClient) -> None:
    body = assert_readable_error(api.get("/api/projects/prj_nope"), 404)
    assert body["error"] == "not_found"
    assert_readable_error(api.get("/api/walks/wlk_nope"), 404)
    assert_readable_error(api.get("/api/items/itm_nope"), 404)
    assert_readable_error(api.get("/api/nope/route"), 404)
    body = assert_readable_error(api.post("/api/projects", json={"name": ""}), 422)
    assert body["error"] == "invalid_request" and "name" in body["detail"]
    body = assert_readable_error(api.post("/api/projects", json={"name": "x", "bogus": 1}), 422)
    assert "bogus" in body["detail"]
    trades = [{"trade": "plumbing", "company": "A"}, {"trade": "plumbing", "company": "B"}]
    body = assert_readable_error(api.post("/api/projects", json={"name": "x", "trades": trades}), 400)
    assert "Plumbing is listed twice" in body["detail"]
    pid = make_project(api)["id"]
    assert_readable_error(api.get(f"/api/projects/{pid}/items?trade=roofing"), 422)
    assert_readable_error(api.delete("/api/projects"), 405)


def test_unexpected_errors_are_readable_500s(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from punchwalk.api import views
    from punchwalk.api.app import app

    pid = make_project(api)["id"]

    def boom(*_a, **_k):
        raise RuntimeError("database exploded")

    monkeypatch.setattr(views, "project_out", boom)
    with TestClient(app, raise_server_exceptions=False) as client:
        body = assert_readable_error(client.get(f"/api/projects/{pid}"), 500)
    assert body["error"] == "server_error" and "exploded" not in body["detail"]


def test_request_size_limits(api: TestClient) -> None:
    big = {"name": "x", "keywords": ["k" * 1000] * 1200}  # ~1.2 MB of JSON
    body = assert_readable_error(api.post("/api/projects", json=big), 413)
    assert body["error"] == "too_large"
    pid = make_project(api)["id"]
    res = api.post(
        f"/api/projects/{pid}/walks",
        content=b"x",
        headers={"content-type": "multipart/form-data; boundary=zz", "content-length": str(500 * 1024 * 1024)},
    )
    assert "400 MB" in assert_readable_error(res, 413)["detail"]


def test_cors_allows_the_vite_dev_server(api: TestClient) -> None:
    res = api.options(
        "/api/projects",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
    )
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-origin" not in api.get("/healthz", headers={"Origin": "https://evil.example"}).headers


def test_demo_mode_upload_rate_limit(api: TestClient, clip_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_MODE", "true")
    reset_settings()
    pid = make_project(api)["id"]
    for _ in range(3):
        assert upload(api, pid, [clip_path]).status_code == 201
    res = upload(api, pid, [clip_path])
    body = assert_readable_error(res, 429)
    assert body["error"] == "rate_limited" and "3 uploads per hour" in body["detail"]
    assert int(res.headers["retry-after"]) > 0
    # another device (Fly-Client-IP) still has its own quota
    files = [("clips", (clip_path.name, clip_path.read_bytes(), "video/mp4"))]
    other = api.post(f"/api/projects/{pid}/walks", files=files, headers={"Fly-Client-IP": "203.0.113.9"})
    assert other.status_code == 201


def test_no_rate_limit_outside_demo_mode(api: TestClient, clip_path: Path) -> None:
    pid = make_project(api)["id"]
    for _ in range(4):
        assert upload(api, pid, [clip_path]).status_code == 201


# --------------------------------------------------------------------------- media


def test_media_range_requests(api: TestClient, clip_path: Path) -> None:
    _, up = ready_walk(api, clip_path)
    url = f"/media/walks/{up['walk_id']}/display.mp4"
    size = clip_path.stat().st_size

    full = api.get(url)
    assert full.status_code == 200 and full.headers["content-type"] == "video/mp4"
    assert full.headers["accept-ranges"] == "bytes" and len(full.content) == size

    part = api.get(url, headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100
    assert part.headers["content-range"] == f"bytes 0-99/{size}"
    assert part.content == full.content[:100]

    tail = api.get(url, headers={"Range": "bytes=-10"})
    assert tail.status_code == 206 and tail.content == full.content[-10:]
    assert api.get(url, headers={"Range": f"bytes={size + 10}-"}).status_code == 416
    assert api.head(url).headers["content-length"] == str(size)


def test_media_rejects_traversal_and_private_files(api: TestClient, clip_path: Path) -> None:
    _, up = ready_walk(api, clip_path)
    for bad in (
        "/media/walks/../punchwalk.sqlite3",
        "/media/walks/%2e%2e/punchwalk.sqlite3",
        "/media/punchwalk.sqlite3",
        "/media/cost_ledger.jsonl",
        f"/media/walks/{up['walk_id']}/missing.mp4",
    ):
        assert_readable_error(api.get(bad), 404)


def test_spa_fallback(api: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from punchwalk.api import media

    monkeypatch.setattr(media, "STATIC_DIR", tmp_path / "not_built")
    assert "make build" in assert_readable_error(api.get("/"), 404)["detail"]
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>Punchwalk</title>")
    (static / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setattr(media, "STATIC_DIR", static)
    for path in ("/", "/walks/wlk_1", "/share/item/shr_abc"):
        res = api.get(path)
        assert res.status_code == 200 and "Punchwalk" in res.text and res.headers["content-type"].startswith("text/html")
    js = api.get("/assets/app.js")
    assert js.status_code == 200 and "immutable" in js.headers["cache-control"]
    assert_readable_error(api.get("/api/unknown"), 404)
    assert_readable_error(api.get("/media/nothing.mp4"), 404)
