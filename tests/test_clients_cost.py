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
