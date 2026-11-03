"""PDF punch report (PLAN 6.3): context, HTML, and the PDF itself when WeasyPrint can run here."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from punchwalk.core.models import Project
from punchwalk.db import session_factory
from punchwalk.report import render
from tests import test_api_support as support
from tests.test_dispatch_support import make_project, seed_walk

api = support.api


def weasyprint_works() -> bool:
    try:
        render.render_pdf("<p>x</p>")
    except render.ReportUnavailable:
        return False
    return True


def context_for(pid: str) -> dict:
    with session_factory() as s:
        project = s.get(Project, pid)
        return render.build_context(s, project, now=datetime(2026, 11, 4, 15, 0, tzinfo=UTC))


def test_context_groups_items_by_room_with_totals(api: TestClient) -> None:
    pid = make_project(api)["id"]
    seed_walk(pid, with_files=True)
    ctx = context_for(pid)
    assert ctx["project"] == {"name": "Unit 2B final punch", "address": "418 Alder Street, Unit 2B"}
    assert ctx["generated"] == "November 4, 2026" and ctx["total"] == 6
    assert dict(ctx["by_status"]) == {"Open": 4, "Needs a look": 1, "Signed off": 1}
    assert [r["trade"] for r in ctx["by_trade"]] == ["Electrical", "Carpentry & millwork", "Windows & glazing"]
    elec = ctx["by_trade"][0]
    assert (elec["open"], elec["needs_review"], elec["signed_off"], elec["total"]) == (2, 1, 1, 4)
    rooms = [(room, [i.number for i in entries]) for room, entries in ctx["rooms"]]
    assert rooms == [("Kitchen", ["#001", "#004"]), ("Bedroom", ["#003", "#005", "#006"]), ("Hallway", ["#002"])]
    first = ctx["rooms"][0][1][0]
    assert first.image_uri and first.image_uri.startswith("file://") and first.image_uri.endswith("/crop.jpg")
    assert ctx["rooms"][1][1][0].is_safety and ctx["rooms"][1][1][0].severity == "Safety"


def test_html_has_cover_items_and_sign_off(api: TestClient) -> None:
    pid = make_project(api)["id"]
    seed_walk(pid, with_files=True)
    html = render.render_html(context_for(pid))
    assert "<h1>Unit 2B final punch</h1>" in html and "418 Alder Street, Unit 2B" in html
    assert "Punch report, November 4, 2026." in html
    for text in ("#003", "Outlet cover cracked", "Action:</strong> Replace the cover", "Needs a look", "Signed off",
                 "Carpentry &amp; millwork", "Sign-off", "Project manager, name and signature"):  # fmt: skip
        assert text in html, text
    assert html.count('class="item"') == 6 and html.count("<img ") == 6
    assert "size: letter portrait" in html


def test_html_escapes_and_handles_missing_photos(api: TestClient) -> None:
    pid = api.post("/api/projects", json={"name": "Unit <3B> & \"Co\""}).json()["id"]
    seed_walk(pid, with_files=False)
    html = render.render_html(context_for(pid))
    assert "Unit &lt;3B&gt; &amp; &#34;Co&#34;" in html and "<3B>" not in html
    assert "No photo" in html and "<img " not in html


def test_filename() -> None:
    assert render.report_filename(Project(name="Unit 2B final punch")) == "punch-report-unit-2b-final-punch.pdf"


def test_report_route(api: TestClient) -> None:
    pid = make_project(api)["id"]
    seed_walk(pid, with_files=True)
    res = api.get(f"/api/projects/{pid}/report.pdf")
    if weasyprint_works():
        assert res.status_code == 200 and res.headers["content-type"] == "application/pdf"
        assert 'filename="punch-report-unit-2b-final-punch.pdf"' in res.headers["content-disposition"]
        assert res.content.startswith(b"%PDF")
    else:
        assert res.status_code == 503
        body = res.json()
        assert body["error"] == "unavailable" and "WeasyPrint" in body["detail"]
    assert api.get("/api/projects/prj_nope/report.pdf").status_code == 404


@pytest.mark.skipif(not weasyprint_works(), reason="WeasyPrint's system libraries (Pango) aren't installed here")
def test_pdf_is_valid_with_at_least_two_pages(api: TestClient) -> None:
    pid = make_project(api)["id"]
    seed_walk(pid, with_files=True)
    with session_factory() as s:
        pdf = render.project_report_pdf(s, s.get(Project, pid))
    assert pdf.startswith(b"%PDF-") and pdf.rstrip().endswith(b"%%EOF")
    pages = re.findall(rb"/Type\s*/Page(?!s)", pdf)
    assert len(pages) >= 2, len(pages)
