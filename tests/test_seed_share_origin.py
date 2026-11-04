"""Seed import: dispatch payloads carry the importing app's origin in their share links (not the snapshot's)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from punchwalk.config import reset_settings
from tests.test_seed import api, load_seed, seed  # noqa: F401  (fixtures)

LOCAL = "http://localhost:8000"
LIVE = "https://punchwalk.fly.dev"


def test_rewrite_share_origin_on_a_sample_payload():
    mod = load_seed()
    payload = {
        "text": f"*Unit 2B: 1 electrical item*\n<{LOCAL}/share/item/shr_eab039f5ad|View item and video>",
        "blocks": [{"type": "section", "text": {"type": "mrkdwn",
                                                 "text": f"<{LOCAL}/share/item/shr_eab039f5ad|View item>"}}],
        "links": [{"number": 7, "url": f"{LOCAL}/share/item/shr_eab039f5ad"}, "http://127.0.0.1:8090/share/x"],
        "webhook": "https://hooks.slack.example.com/services/T000/B000/xyz",  # not a share link: untouched
        "count": 4,
    }
    out = mod.rewrite_share_origin(payload, LIVE + "/")
    assert out["text"].endswith(f"<{LIVE}/share/item/shr_eab039f5ad|View item and video>")
    assert out["blocks"][0]["text"]["text"] == f"<{LIVE}/share/item/shr_eab039f5ad|View item>"
    assert out["links"] == [{"number": 7, "url": f"{LIVE}/share/item/shr_eab039f5ad"}, f"{LIVE}/share/x"]
    assert out["webhook"] == payload["webhook"] and out["count"] == 4
    assert "localhost" not in str(out)
    assert mod.rewrite_share_origin(payload, LOCAL)["text"] == payload["text"]  # local import: unchanged


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_import_rewrites_dispatch_links_to_app_base_url(seed, api: TestClient, tmp_path: Path, monkeypatch):  # noqa: F811
    from punchwalk.core.models import Dispatch
    from punchwalk.db import session_factory

    seed.run(["--walks", "synth_walk"])
    with session_factory() as s:
        s.add(Dispatch(id="dsp_origin_test", project_id="prj_demo", trade="electrical", item_ids=[],
                       channel="slack", payload={"text": f"<{LOCAL}/share/item/shr_1|View>",
                                                 "links": [f"{LOCAL}/share/item/shr_1"]}))
        s.commit()
    snap = tmp_path / "snap.tar.gz"
    seed.run(["--export", str(snap), "--export-only"])
    monkeypatch.setenv("APP_BASE_URL", LIVE)
    reset_settings()
    seed.run(["--import", str(snap)])
    with session_factory() as s:
        d = s.get(Dispatch, "dsp_origin_test")
    assert d.payload == {"text": f"<{LIVE}/share/item/shr_1|View>", "links": [f"{LIVE}/share/item/shr_1"]}
