"""Re-seed the demo project from the baked snapshot: on boot, then every night.

With `DEMO_RESEED=true` the app starts one daemon thread that runs
`python scripts/seed.py --import DEMO_SNAPSHOT` once at startup and then daily at `DEMO_RESEED_AT`
(America/New_York, default 03:00). The import replaces the demo project (`prj_demo`) wholesale, so walks
a visitor uploaded into it and anything they changed are gone, and the demo opens exactly like the
snapshot. Other projects are never touched.

- It runs as a subprocess (the seed script sets its own offline environment) as the app's user.
- It waits until the job worker is idle (no queued or running job) before importing, for at most
  `IDLE_WAIT_MAX_S`; a busy worker postpones the import, it never interrupts a walk.
- A missing snapshot is logged and skipped. Every run is logged with its outcome.

Boot reset (PLAN 10.9): at boot only (never the nightly run), when `DEMO_RESET_TOKEN` is set and differs
from the token stored in `DATA_DIR/.demo_reset_token`, every project is dropped first (rows, jobs, model
calls and walk media, via `scripts/seed.py --drop-all`), then the snapshot is imported as usual and the
token is stored. The same token on the next restart drops nothing; an empty token never drops anything.
So changing the value in fly.toml and deploying wipes uploaded projects once.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

from sqlmodel import col, select

from punchwalk.clients.cost import eastern
from punchwalk.config import REPO_ROOT, get_settings
from punchwalk.core.enums import JobStatus
from punchwalk.core.models import Job
from punchwalk.db import session_factory

log = logging.getLogger("punchwalk.reseed")

SEED_SCRIPT = REPO_ROOT / "scripts" / "seed.py"
IDLE_POLL_S = 15.0
IDLE_WAIT_MAX_S = 45 * 60.0
IMPORT_TIMEOUT_S = 15 * 60.0
BOOT_DELAY_S = 5.0  # let the server answer health checks first
RESET_TOKEN_FILE = ".demo_reset_token"  # under DATA_DIR


def parse_hhmm(text: str) -> dtime:
    h, _, m = text.partition(":")
    return dtime(int(h), int(m))


def next_run(now: datetime, at: dtime) -> datetime:
    """The next `at` (America/New_York wall clock) strictly after `now`, as an aware UTC datetime."""
    tz = eastern()
    local = now.astimezone(tz)
    candidate = datetime.combine(local.date(), at, tzinfo=tz)
    if candidate <= local:
        candidate = datetime.combine(local.date() + timedelta(days=1), at, tzinfo=tz)
    return candidate.astimezone(UTC)


def worker_idle() -> bool:
    with session_factory() as s:
        busy = s.exec(
            select(Job.id).where(col(Job.status).in_([JobStatus.queued, JobStatus.running])).limit(1)
        ).first()
    return busy is None


def wait_until_idle(
    *, is_idle: Callable[[], bool] = worker_idle, sleep: Callable[[float], object] = time.sleep,
    poll_s: float = IDLE_POLL_S, max_wait_s: float = IDLE_WAIT_MAX_S,
) -> bool:  # fmt: skip
    """True once no job is queued or running; False if that didn't happen within `max_wait_s`."""
    waited = 0.0
    while not is_idle():
        if waited >= max_wait_s:
            return False
        sleep(poll_s)
        waited += poll_s
    return True


def run_import(snapshot: Path) -> bool:
    cmd = [sys.executable, str(SEED_SCRIPT), "--import", str(snapshot)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=IMPORT_TIMEOUT_S, cwd=REPO_ROOT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.error("re-seed: the import didn't run: %s", exc)
        return False
    tail = (res.stdout or "").strip().splitlines()[-3:] + (res.stderr or "").strip().splitlines()[-3:]
    if res.returncode != 0:
        log.error("re-seed: the import failed (exit %d): %s", res.returncode, " | ".join(tail))
        return False
    log.info("re-seed: restored prj_demo (%d items) from %s", demo_item_count(), snapshot)
    log.debug("re-seed output: %s", " | ".join(tail))
    return True


def run_drop_all() -> int | None:
    """`scripts/seed.py --drop-all`: drop every project. Returns how many, or None when it failed."""
    cmd = [sys.executable, str(SEED_SCRIPT), "--drop-all", "--json"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=IMPORT_TIMEOUT_S, cwd=REPO_ROOT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.error("demo reset: the drop didn't run: %s", exc)
        return None
    if res.returncode != 0:
        tail = (res.stdout or "").strip().splitlines()[-3:] + (res.stderr or "").strip().splitlines()[-3:]
        log.error("demo reset: the drop failed (exit %d): %s", res.returncode, " | ".join(tail))
        return None
    try:
        return len(json.loads((res.stdout or "").strip().splitlines()[-1])["dropped"])
    except (IndexError, KeyError, TypeError, ValueError):
        return 0


def token_path() -> Path:
    return Path(get_settings().DATA_DIR) / RESET_TOKEN_FILE


def stored_token() -> str:
    try:
        return token_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def maybe_reset(*, dropper: Callable[[], int | None] = run_drop_all) -> int | None:
    """The boot reset: drop every project when DEMO_RESET_TOKEN is set and differs from the stored one, then
    store it. Returns how many projects were dropped (None: no reset was due, or the drop failed)."""
    token = (get_settings().DEMO_RESET_TOKEN or "").strip()
    if not token:
        return None
    if stored_token() == token:
        log.info("demo reset: token %r already applied; nothing dropped", token)
        return None
    dropped = dropper()
    if dropped is None:
        log.error("demo reset: token %r not applied (the drop failed); it is tried again on the next boot", token)
        return None
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n", encoding="utf-8")
    log.info("demo reset: token changed, dropped %d projects", dropped)
    return dropped


def demo_item_count() -> int:
    from punchwalk.core.models import Item

    with session_factory() as s:
        return len(s.exec(select(Item.id).where(Item.project_id == "prj_demo")).all())


def reseed_once(
    reason: str, *, snapshot: Path | None = None, importer: Callable[[Path], bool] = run_import,
    wait: Callable[[], bool] = wait_until_idle, dropper: Callable[[], int | None] = run_drop_all,
) -> str:  # fmt: skip
    """One re-seed. Returns what happened: "done", "failed", "no_snapshot" or "busy". At boot, the reset
    token is checked first (after the worker is idle, before the import): see the module docstring."""
    path = Path(snapshot or get_settings().DEMO_SNAPSHOT)
    if not path.is_file():  # no demo to restore: never wipe anything either
        log.warning("re-seed (%s): no snapshot at %s; skipped", reason, path)
        return "no_snapshot"
    if not wait():
        log.warning("re-seed (%s): the job worker stayed busy; skipped until the next run", reason)
        return "busy"
    if reason == "boot":
        maybe_reset(dropper=dropper)
    log.info("re-seed (%s): importing %s", reason, path)
    return "done" if importer(path) else "failed"


class ReseedScheduler:
    """The boot + nightly re-seed thread. `clock` and the stop event make it testable without sleeping."""

    def __init__(self, at: dtime, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 run: Callable[[str], str] = reseed_once, boot: bool = True) -> None:  # fmt: skip
        self.at, self.clock, self.run, self.boot = at, clock, run, boot
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def seconds_until_next(self) -> float:
        now = self.clock()
        return max(0.0, (next_run(now, self.at) - now).total_seconds())

    def loop(self) -> None:
        if self.boot and not self.stop_event.wait(BOOT_DELAY_S):
            self._safe_run("boot")
        while not self.stop_event.is_set():
            wait_s = self.seconds_until_next()
            log.info("re-seed: next run in %.0f min (%s America/New_York)", wait_s / 60, self.at.strftime("%H:%M"))
            if self.stop_event.wait(wait_s):
                return
            self._safe_run("nightly")
            self.stop_event.wait(60)  # never twice in the same minute

    def _safe_run(self, reason: str) -> None:
        try:
            self.run(reason)
        except Exception:  # the scheduler must outlive any failed run
            log.exception("re-seed (%s) crashed", reason)

    def start(self) -> None:
        self.thread = threading.Thread(target=self.loop, name="punchwalk-reseed", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=5)


_scheduler: ReseedScheduler | None = None


def start_reseed() -> ReseedScheduler | None:
    """Start the scheduler when DEMO_RESEED is on (the app's lifespan calls this)."""
    global _scheduler
    s = get_settings()
    if not s.DEMO_RESEED or _scheduler is not None:
        return _scheduler
    _scheduler = ReseedScheduler(parse_hhmm(s.DEMO_RESEED_AT))
    _scheduler.start()
    log.info("re-seed: on (boot + daily at %s America/New_York, snapshot %s)", s.DEMO_RESEED_AT, s.DEMO_SNAPSHOT)
    return _scheduler


def stop_reseed() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.stop()
        _scheduler = None
