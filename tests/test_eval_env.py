"""`make eval` / `make eval-live` never send: the eval environment switches every send channel off."""

from __future__ import annotations

import os

import pytest

from eval import run_eval
from punchwalk.config import get_settings, reset_settings


@pytest.mark.parametrize("live", [False, True])
def test_eval_env_turns_every_send_channel_off(tmp_path, monkeypatch, live):
    monkeypatch.setenv("AUTO_SEND", "true")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T000/B000/realish")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("EMAIL_FROM", "punch@example.com")
    cache, ledger = tmp_path / "cache", tmp_path / "ledger.jsonl"
    cache.mkdir()

    env = run_eval.build_eval_env(tmp_path / "data", live=live, orig_cache_dir=cache)
    assert {k: env[k] for k in run_eval.NO_SEND_ENV} == {
        "AUTO_SEND": "false", "SLACK_WEBHOOK_URL": "", "RESEND_API_KEY": "", "EMAIL_FROM": "",
    }

    with run_eval.eval_environment(tmp_path / "data", live=live, orig_cache_dir=cache, orig_ledger=ledger):
        s = get_settings()  # what the pipeline sees: process env beats .env, so a real .env can't re-enable sends
        assert s.AUTO_SEND is False
        assert s.SLACK_WEBHOOK_URL.get_secret_value() == ""
        assert s.RESEND_API_KEY.get_secret_value() == ""
        assert s.EMAIL_FROM == ""

    assert os.environ["SLACK_WEBHOOK_URL"].startswith("https://hooks.slack.com/")  # restored afterwards
    assert os.environ["AUTO_SEND"] == "true"
    reset_settings()
