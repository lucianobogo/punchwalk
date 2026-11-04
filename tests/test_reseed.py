"""Demo re-seed scheduler: time math in America/New_York, idle wait, skip rules. No real sleeps."""

from __future__ import annotations

from datetime import UTC, datetime
from datetime import time as dtime
from pathlib import Path

import pytest

from punchwalk.api import reseed
from punchwalk.config import reset_settings
from tests import test_api_support as support

THREE_AM = dtime(3, 0)
api = support.api  # shared fixture


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 11, 5, 7, 59, tzinfo=UTC), datetime(2026, 11, 5, 8, 0, tzinfo=UTC)),  # 02:59 EST -> 03:00
        (datetime(2026, 11, 5, 8, 0, tzinfo=UTC), datetime(2026, 11, 6, 8, 0, tzinfo=UTC)),  # exactly 03:00 -> next day
        (datetime(2026, 11, 5, 15, 0, tzinfo=UTC), datetime(2026, 11, 6, 8, 0, tzinfo=UTC)),  # 10:00 EST
        (datetime(2026, 11, 6, 4, 30, tzinfo=UTC), datetime(2026, 11, 6, 8, 0, tzinfo=UTC)),  # 23:30 EST Nov 5
        # DST ends Sun Nov 1, 2026: the night before, 03:00 EDT is 07:00 UTC; the night after, 08:00 UTC
        (datetime(2026, 10, 31, 12, 0, tzinfo=UTC), datetime(2026, 11, 1, 8, 0, tzinfo=UTC)),
        (datetime(2026, 10, 30, 12, 0, tzinfo=UTC), datetime(2026, 10, 31, 7, 0, tzinfo=UTC)),
    ],
)
def test_next_run_is_3am_new_york(now: datetime, expected: datetime) -> None:
    assert reseed.next_run(now, THREE_AM) == expected


def test_scheduler_wait_uses_the_clock() -> None:
    sched = reseed.ReseedScheduler(THREE_AM, clock=lambda: datetime(2026, 11, 5, 7, 0, tzinfo=UTC))
    assert sched.seconds_until_next() == 3600.0


def test_wait_until_idle_polls_without_sleeping() -> None:
    states = iter([False, False, True])
    slept: list[float] = []
    assert reseed.wait_until_idle(is_idle=lambda: next(states), sleep=slept.append, poll_s=10, max_wait_s=60)
    assert slept == [10, 10]
    slept.clear()
    assert not reseed.wait_until_idle(is_idle=lambda: False, sleep=slept.append, poll_s=10, max_wait_s=30)
    assert slept == [10, 10, 10]


def test_reseed_once_skips_a_missing_snapshot_and_a_busy_worker(tmp_path: Path) -> None:
    calls: list[Path] = []

    def importer(p: Path) -> bool:
        calls.append(p)
        return True

    assert reseed.reseed_once("boot", snapshot=tmp_path / "none.tar.gz", importer=importer) == "no_snapshot"
    snap = tmp_path / "snap.tar.gz"
    snap.write_bytes(b"x")
    assert reseed.reseed_once("nightly", snapshot=snap, importer=importer, wait=lambda: False) == "busy"
    assert calls == []
    assert reseed.reseed_once("nightly", snapshot=snap, importer=importer, wait=lambda: True) == "done"
    assert reseed.reseed_once("nightly", snapshot=snap, importer=lambda p: False, wait=lambda: True) == "failed"
    assert calls == [snap]


def test_worker_idle_reads_the_jobs_table(api) -> None:
    from punchwalk.core.enums import JobStatus
    from punchwalk.core.models import Job
    from punchwalk.db import session_factory

    assert reseed.worker_idle()
    with session_factory() as s:
        s.add(Job(status=JobStatus.running))
        s.commit()
    assert not reseed.worker_idle()


def test_scheduler_loop_runs_at_boot_then_waits_for_3am(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reseed, "BOOT_DELAY_S", 0)
    ran: list[str] = []
    sched = reseed.ReseedScheduler(THREE_AM, clock=lambda: datetime(2026, 11, 5, 7, 0, tzinfo=UTC),
                                   run=lambda reason: ran.append(reason) or "done")  # fmt: skip
    waits: list[float] = []

    def fake_wait(timeout: float | None = None) -> bool:
        waits.append(timeout or 0)
        if len(waits) >= 4:
            sched.stop_event.set()
            return True
        return False

    monkeypatch.setattr(sched.stop_event, "wait", fake_wait)
    sched.loop()
    assert ran == ["boot", "nightly"] and waits[:3] == [0, 3600.0, 60]


def test_start_reseed_only_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_RESEED", "false")
    reset_settings()
    assert reseed.start_reseed() is None


def test_reseed_time_setting_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import ValidationError

    from punchwalk.config import Settings

    monkeypatch.setenv("DEMO_RESEED_AT", "3:5")
    assert Settings().DEMO_RESEED_AT == "03:05"
    monkeypatch.setenv("DEMO_RESEED_AT", "25:00")
    with pytest.raises(ValidationError):
        Settings()


def test_app_logs_punchwalk_info_lines(capsys: pytest.CaptureFixture[str]) -> None:
    import logging

    from punchwalk.api.app import configure_logging

    root = configure_logging()
    configure_logging()  # idempotent: still one handler
    assert root.level == logging.INFO
    assert sum(1 for h in root.handlers if getattr(h, "_punchwalk", False)) == 1
    logging.getLogger("punchwalk.reseed").info("re-seed (boot): importing /app/demo/seed_snapshot.tar.gz")
    err = capsys.readouterr().err
    assert "INFO punchwalk.reseed: re-seed (boot): importing" in err


def test_a_boot_reseed_logs_import_and_restore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, api,
                                               caplog: pytest.LogCaptureFixture) -> None:  # fmt: skip
    import logging
    import subprocess

    snap = tmp_path / "snap.tar.gz"
    snap.write_bytes(b"x")
    monkeypatch.setattr(reseed.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="ok\n", stderr=""))  # fmt: skip
    monkeypatch.setattr(reseed, "demo_item_count", lambda: 14)
    with caplog.at_level(logging.INFO, logger="punchwalk.reseed"):
        assert reseed.reseed_once("boot", snapshot=snap, wait=lambda: True) == "done"
    lines = [r.getMessage() for r in caplog.records]
    assert f"re-seed (boot): importing {snap}" in lines
    assert f"re-seed: restored prj_demo (14 items) from {snap}" in lines



# --------------------------------------------------------------------------- boot reset token (PLAN 10.9)


@pytest.fixture
def reset_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    snap = tmp_path / "snap.tar.gz"
    snap.write_bytes(b"x")
    order: list[str] = []

    def drop() -> int:
        order.append("drop")
        return 3

    def importer(p: Path) -> bool:
        order.append("import")
        return True

    def run(reason: str, token: str) -> str:
        monkeypatch.setenv("DEMO_RESET_TOKEN", token)
        reset_settings()
        return reseed.reseed_once(reason, snapshot=snap, importer=importer, wait=lambda: True, dropper=drop)

    yield run, order
    reset_settings()


def test_a_new_reset_token_drops_everything_before_the_import_once(reset_env, caplog) -> None:
    import logging

    run, order = reset_env
    with caplog.at_level(logging.INFO, logger="punchwalk.reseed"):
        assert run("boot", "2026-11-04-a") == "done"
    assert order == ["drop", "import"]
    assert reseed.stored_token() == "2026-11-04-a"
    assert "demo reset: token changed, dropped 3 projects" in [r.getMessage() for r in caplog.records]
    order.clear()
    assert run("boot", "2026-11-04-a") == "done"  # a restart with the same token: no reset
    assert order == ["import"]
    order.clear()
    assert run("boot", "2026-11-04-b") == "done"  # changed again: one more reset
    assert order == ["drop", "import"] and reseed.stored_token() == "2026-11-04-b"


def test_an_empty_token_or_the_nightly_run_never_drops(reset_env) -> None:
    run, order = reset_env
    assert run("boot", "") == "done"
    assert run("nightly", "2026-11-04-a") == "done"  # only boot looks at the token
    assert order == ["import", "import"] and reseed.stored_token() == ""


def test_a_failed_drop_keeps_the_old_token_so_the_next_boot_retries(reset_env, monkeypatch) -> None:
    run, order = reset_env
    monkeypatch.setenv("DEMO_RESET_TOKEN", "t1")
    reset_settings()
    assert reseed.maybe_reset(dropper=lambda: None) is None
    assert reseed.stored_token() == ""
    assert run("boot", "t1") == "done" and order == ["drop", "import"]


def test_run_drop_all_reads_the_seed_scripts_json(monkeypatch) -> None:
    import subprocess

    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout='{"dropped": ["prj_a", "prj_demo"], "walks": []}\n',
                                           stderr="")

    monkeypatch.setattr(reseed.subprocess, "run", fake_run)
    assert reseed.run_drop_all() == 2
    assert seen["cmd"][-2:] == ["--drop-all", "--json"]
    monkeypatch.setattr(reseed.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom"))
    assert reseed.run_drop_all() is None
