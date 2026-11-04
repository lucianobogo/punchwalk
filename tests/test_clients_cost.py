import pytest

from punchwalk.clients import calls, cost


def test_prices():
    assert cost.sam_video_cost(10) == pytest.approx(50 * 0.0002)  # 50-frame minimum
    assert cost.sam_video_cost(1000) == pytest.approx(0.20)
    assert cost.sam_image_cost(4) == pytest.approx(0.01)
    assert cost.asr_cost(3_600_999) == pytest.approx(0.18)  # whole seconds, rounded down
    assert cost.llm_cost("baseten", 1_000_000, 1_000_000) == pytest.approx(1.25 + 4.25)
    assert cost.llm_cost("meta", 1_000_000, 0) == pytest.approx(3.00)


def test_emit_writes_live_calls_only_and_totals(ledger_rows):
    calls.emit(calls.CallRecord(provider="meta", model="sam-3.1", endpoint="/responses", est_cost_usd=0.5))
    calls.emit(calls.CallRecord(provider="meta", model="sam-3.1", endpoint="/responses", est_cost_usd=9, cached=True))
    calls.emit(calls.CallRecord(provider="baseten", model="x", endpoint="/chat/completions", est_cost_usd=0.25))
    rows = ledger_rows()
    assert len(rows) == 2
    assert {"ts", "provider", "model", "endpoint", "units", "unit_kind", "est_cost_usd", "latency_ms", "cached", "ok"} <= set(rows[0])
    assert cost.total_usd() == pytest.approx(0.75)


def test_cap(monkeypatch):
    from punchwalk.config import reset_settings

    monkeypatch.setenv("COST_CAP_USD", "1.0")
    reset_settings()
    calls.emit(calls.CallRecord(provider="meta", model="m", endpoint="/e", est_cost_usd=0.6))
    assert cost.assert_under_cap(0.1) == pytest.approx(0.6)
    with pytest.raises(cost.CostCapReached, match=r"\$1.00 cap"):
        cost.assert_under_cap(0.4)
    calls.emit(calls.CallRecord(provider="meta", model="m", endpoint="/e", est_cost_usd=0.4))
    with pytest.raises(cost.CostCapReached):
        cost.assert_under_cap()


def test_context_and_sinks(ledger_rows):
    seen: list[calls.CallRecord] = []
    calls.add_sink(seen.append)
    with calls.call_context(walk_id="w1", stage="track"):
        calls.emit(calls.CallRecord(provider="meta", model="m", endpoint="/e", cached=True))
        with calls.call_context(stage="inner"):
            calls.emit(calls.CallRecord(provider="meta", model="m", endpoint="/e"))
    calls.emit(calls.CallRecord(provider="meta", model="m", endpoint="/e"))
    assert [(r.walk_id, r.stage) for r in seen] == [("w1", "track"), ("w1", "inner"), (None, "")]
    assert [r["stage"] for r in ledger_rows()] == ["inner", ""]


def test_daily_cap_counts_today_in_new_york(monkeypatch):
    import json
    from datetime import UTC, datetime

    from punchwalk.config import reset_settings

    monkeypatch.setenv("COST_CAP_USD", "60")
    monkeypatch.setenv("COST_DAILY_CAP_USD", "10")
    reset_settings()
    path = cost.ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"ts": "2026-11-05T04:30:00.000+00:00", "est_cost_usd": 9.0, "cached": False},  # 23:30 ET on Nov 4
        {"ts": "2026-11-05T05:10:00.000+00:00", "est_cost_usd": 6.0, "cached": False},  # 00:10 ET on Nov 5
        {"ts": "2026-11-05T05:20:00.000+00:00", "est_cost_usd": 50.0, "cached": True},  # cached: free
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    now = datetime(2026, 11, 5, 15, 0, tzinfo=UTC)  # 10:00 ET
    assert str(cost.today_eastern(now)) == "2026-11-05"
    assert cost.spend_today_usd(now) == pytest.approx(6.0)
    assert cost.assert_under_cap(3.0, now=now) == pytest.approx(15.0)  # 6 + 3 < 10; total 15 < 60
    with pytest.raises(cost.CostCapReached, match="Today's live model budget") as err:
        cost.assert_under_cap(4.5, now=now)
    assert err.value.daily
    # yesterday, the late-evening spend counted against Nov 4 only
    assert cost.spend_today_usd(datetime(2026, 11, 5, 4, 45, tzinfo=UTC)) == pytest.approx(9.0)


def test_total_cap_still_applies(monkeypatch):
    from punchwalk.config import reset_settings

    monkeypatch.setenv("COST_CAP_USD", "1.0")
    monkeypatch.setenv("COST_DAILY_CAP_USD", "100")
    reset_settings()
    calls.emit(calls.CallRecord(provider="meta", model="m", endpoint="/e", est_cost_usd=0.9))
    with pytest.raises(cost.CostCapReached) as err:
        cost.assert_under_cap(0.2)
    assert not err.value.daily


def test_budget_script(monkeypatch, capsys):
    import importlib.util

    from punchwalk.config import REPO_ROOT, reset_settings

    monkeypatch.setenv("COST_DAILY_CAP_USD", "10")
    reset_settings()
    calls.emit(calls.CallRecord(provider="baseten", model="m", endpoint="/e", est_cost_usd=1.5))
    spec = importlib.util.spec_from_file_location("budget_script", REPO_ROOT / "scripts" / "budget.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    b = mod.budget()
    assert b["spend_today_usd"] == pytest.approx(1.5) and b["left_today_usd"] == pytest.approx(8.5)
    assert b["spend_total_usd"] == pytest.approx(1.5) and b["total_cap_usd"] == 25
    assert mod.main([]) == 0 and "of $10.00" in capsys.readouterr().out
