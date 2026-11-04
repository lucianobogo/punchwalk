"""The eval block of GET /api/walks/{id}/processing (PLAN 3.3): eval/report.json's app_summary, or null."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from punchwalk.api import schemas as S
from punchwalk.api import views
from tests import test_api_support as support
from tests.test_api_support import check, ready_walk

api = support.api
clip_path = support.clip_path

SUMMARY = {
    "source": "eval/report.md",
    "updated_at": "2026-11-04T02:10:00Z",
    "metrics": [
        {"key": "defects_found", "label": "Defects found", "value": 11, "total": 12},
        {"key": "right_trade", "label": "Right trade", "value": 10, "total": 11},
        {"key": "false_claims_caught", "label": "False \"fixed\" claims caught", "value": 1, "total": 1},
    ],
}


def write_report(path: Path, *, labels_dir: str = "eval/labels", app_summary=SUMMARY) -> Path:
    path.write_text(json.dumps({"labels_dir": labels_dir, "cache_mode": "readonly", "walks": [], "summary": [],
                                "app_summary": app_summary}))
    return path


@pytest.fixture
def report(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "report.json"
    monkeypatch.setenv("PUNCHWALK_EVAL_REPORT", str(path))
    return path


def test_processing_summary_carries_the_eval_block(api: TestClient, clip_path: Path, report: Path) -> None:
    _, up = ready_walk(api, clip_path)
    url = f"/api/walks/{up['walk_id']}/processing"
    assert check(S.ProcessingSummaryOut, api.get(url).json()).eval is None  # no report yet: hide the section

    write_report(report)
    proc = check(S.ProcessingSummaryOut, api.get(url).json())
    assert proc.eval is not None and proc.eval.source == "eval/report.md"
    assert [(m.key, m.value, m.total) for m in proc.eval.metrics] == [
        ("defects_found", 11, 12), ("right_trade", 10, 11), ("false_claims_caught", 1, 1)]

    # a newer report is picked up (cached by mtime/size, not forever)
    changed = {**SUMMARY, "metrics": SUMMARY["metrics"][:1]}
    write_report(report, app_summary=changed)
    os.utime(report, ns=(report.stat().st_atime_ns, report.stat().st_mtime_ns + 1_000_000))
    assert len(check(S.ProcessingSummaryOut, api.get(url).json()).eval.metrics) == 1


@pytest.mark.parametrize("labels_dir, app_summary, raw", [
    ("fixtures/eval/labels_synth", SUMMARY, None),  # synthetic labels: not the product's numbers
    ("/tmp/scratch-labels", SUMMARY, None),
    ("eval/labels", None, None),  # nothing scored (e.g. only holdout walks)
    ("eval/labels", {"source": "eval/report.md", "metrics": "broken"}, None),
    ("eval/labels", SUMMARY, "{not json"),
])
def test_eval_block_is_null_unless_a_real_scored_report(report: Path, labels_dir, app_summary, raw) -> None:
    if raw is not None:
        report.write_text(raw)
    else:
        write_report(report, labels_dir=labels_dir, app_summary=app_summary)
    assert views.eval_summary() is None


def test_default_path_is_eval_report_json() -> None:
    assert Path(views.__file__).resolve().parents[2] / "eval" / "report.json" == views.EVAL_REPORT_JSON


# --------------------------------------------------------------------------- model names on the same screen


def test_every_spark_open_slug_has_the_plain_name() -> None:
    from punchwalk.config import Settings
    from punchwalk.core.enums import MODEL_LABEL, model_label

    for slug in [Settings().SPARK_OPEN_MODEL, *Settings().SPARK_OPEN_MODEL_ALTERNATES]:
        assert model_label(slug) == "Muse Spark Open on Baseten", slug
    assert set(Settings().SPARK_OPEN_MODEL_ALTERNATES) <= set(MODEL_LABEL)


def test_files_api_uploads_are_not_a_model_used(api: TestClient, clip_path: Path, report: Path) -> None:
    from punchwalk.core.enums import Provider, Stage
    from punchwalk.core.models import ModelCall
    from punchwalk.db import session_factory

    _, up = ready_walk(api, clip_path)
    with session_factory() as s:
        s.add(ModelCall(walk_id=up["walk_id"], stage=Stage.track_hands, model=views.FILES_MODEL, provider=Provider.meta))
        s.add(ModelCall(walk_id=up["walk_id"], stage=Stage.verify, model="meta-models/muse-spark-open",
                        provider=Provider.baseten))
        s.commit()
    proc = check(S.ProcessingSummaryOut, api.get(f"/api/walks/{up['walk_id']}/processing").json())
    assert "meta-files" not in proc.models_used
    assert "Meta Files API" not in proc.models_used
    files = [c for c in proc.calls if c.model == views.FILES_MODEL]
    assert files and {c.model_label for c in files} == {"Meta Files API"}  # "Every model call" names it plainly
    assert proc.models_used.count("Muse Spark Open on Baseten") == 1
