"""PLAN 10.9: per-item model calls on a small thread pool, with results, DB writes and ModelCall rows in
item order (never completion order). Fake clients with random delays; offline."""

from __future__ import annotations

import random
import threading
from contextlib import closing
from pathlib import Path

import pytest

from punchwalk.clients import calls
from punchwalk.core.enums import Stage, WalkKind
from punchwalk.core.models import Evidence, Item, Project, Walk


def _pause(seconds: float) -> None:
    threading.Event().wait(seconds)  # conftest turns time.sleep into a no-op


def _record(name: str) -> calls.CallRecord:
    return calls.emit(
        calls.CallRecord(provider="baseten", model=name, endpoint="/chat/completions", cached=True)
    )


def test_map_in_order_yields_and_delivers_records_in_item_order() -> None:
    rng = random.Random(7)
    delays = {i: rng.uniform(0.0, 0.03) for i in range(12)}
    threads: set[str] = set()
    seen: list[calls.CallRecord] = []
    calls.add_sink(seen.append)

    def work(i: int) -> int:
        threads.add(threading.current_thread().name)
        _record(f"m{i}a")
        _pause(delays[i])  # items finish out of order
        _record(f"m{i}b")
        return i * 10

    with calls.call_context(walk_id="wlk_p", stage="write_items"):
        out = [(r.item, r.get()) for r in calls.map_in_order(work, range(12), workers=4)]
    assert out == [(i, i * 10) for i in range(12)]
    assert [r.model for r in seen] == [f"m{i}{x}" for i in range(12) for x in "ab"]
    assert {r.walk_id for r in seen} == {"wlk_p"} and {r.stage for r in seen} == {
        "write_items"
    }  # caller's tags
    assert len(threads) > 1 and all(t.startswith("punchwalk-call") for t in threads)


def test_map_in_order_reraises_in_order_and_still_accounts_for_finished_calls() -> None:
    seen: list[str] = []
    calls.add_sink(lambda r: seen.append(r.model))

    def work(i: int) -> int:
        _record(f"m{i}")
        _pause(0.02 if i == 1 else 0.0)
        if i == 1:
            raise ValueError("boom")
        return i

    got = []
    with (
        pytest.raises(ValueError, match="boom"),
        closing(calls.map_in_order(work, range(6), workers=3)) as results,
    ):
        for r in results:
            got.append(r.get())
    assert got == [0]
    assert seen[:2] == ["m0", "m1"]  # item order up to the failure...
    assert set(seen) <= {f"m{i}" for i in range(6)} and len(seen) == len(
        set(seen)
    )  # ...then whatever ran, once


def test_a_failure_at_item_4_starts_at_most_3_later_items() -> None:
    """Lazy submission: at most POOL_WORKERS tasks ahead of the consumer, so stopping at a failed item
    wastes at most POOL_WORKERS - 1 later calls (here 3), however fast the workers are."""
    started: list[int] = []
    lock = threading.Lock()

    def work(i: int) -> int:
        with lock:
            started.append(i)
        _pause(0.05 if i == 4 else 0.0)  # the others finish at once: an eager pool would race ahead
        if i == 4:
            raise RuntimeError("item 4 failed")
        return i

    got = []
    with (
        pytest.raises(RuntimeError, match="item 4"),
        closing(calls.map_in_order(work, range(1, 21), workers=4)) as rs,
    ):
        for r in rs:
            got.append(r.get())
    assert got == [1, 2, 3]
    later = sorted(i for i in started if i > 4)
    assert len(later) <= 3, later
    assert set(range(1, 5)) <= set(started)


def test_lazy_window_keeps_workers_busy_and_order() -> None:
    active, peak = [0], [0]
    lock = threading.Lock()

    def work(i: int) -> int:
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        _pause(0.01)
        with lock:
            active[0] -= 1
        return i

    assert [r.get() for r in calls.map_in_order(work, range(16), workers=4)] == list(range(16))
    assert 2 <= peak[0] <= 4


def test_map_in_order_with_one_worker_runs_inline() -> None:
    names = []
    out = [
        r.get()
        for r in calls.map_in_order(
            lambda i: names.append(threading.current_thread().name) or i, [1, 2, 3], workers=1
        )
    ]
    assert out == [1, 2, 3] and set(names) == {threading.current_thread().name}


def test_ledger_appends_from_many_threads_stay_whole_lines(tmp_path: Path, monkeypatch) -> None:
    from punchwalk.clients import cost
    from punchwalk.config import reset_settings

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    reset_settings()

    def live(i: int) -> None:
        for k in range(20):
            calls.emit(
                calls.CallRecord(provider="baseten", model=f"m{i}-{k}", endpoint="/x", est_cost_usd=0.0)
            )

    list(calls.map_in_order(live, range(8), workers=8))
    rows = cost.read_ledger()
    assert len(rows) == 160 and len({r["model"] for r in rows}) == 160


def test_cache_put_from_threads_is_atomic(tmp_path: Path, monkeypatch) -> None:
    from punchwalk.clients.cache import Cache

    cache = Cache(mode="readwrite", write_dir=tmp_path / "c", fixture_dir=tmp_path / "f")

    def put(i: int) -> None:
        cache.put(
            "ab" + "0" * 62,
            provider="p",
            endpoint="/e",
            model="m",
            description="d",
            response={"i": i},
            raw_text=f"text {i}",
        )

    list(calls.map_in_order(put, range(16), workers=8))
    hit = cache.get("ab" + "0" * 62)
    assert hit is not None and hit.raw_text.startswith("text ")
    assert not list((tmp_path / "c").rglob("*.tmp"))


# --------------------------------------------------------------------------- stages


@pytest.fixture
def walk_db(tmp_path: Path, monkeypatch):
    """A walk with 8 items (numbers 1..8) and the stage outputs write_items / verify read."""
    from sqlmodel import Session, SQLModel, create_engine

    from punchwalk.config import reset_settings
    from punchwalk.pipeline.context import StageContext, write_stage_output

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    reset_settings()
    engine = create_engine(f"sqlite:///{tmp_path / 'p.sqlite3'}")
    SQLModel.metadata.create_all(engine)

    def factory():
        return Session(engine, expire_on_commit=False)

    ev = Evidence(
        t=1.0,
        image="walks/wlk_p/items/x/raw.jpg",
        raw="walks/wlk_p/items/x/raw.jpg",
        crop="walks/wlk_p/items/x/raw.jpg",
        width=720,
        height=1280,
    ).model_dump(mode="json")
    with factory() as s:
        s.add(Project(id="prj_p", name="p"))
        s.add(Walk(id="wlk_p", project_id="prj_p", kind=WalkKind.initial))
        for n in range(1, 9):
            s.add(
                Item(
                    id=f"itm_{n}",
                    project_id="prj_p",
                    number=n,
                    origin_walk_id="wlk_p",
                    title=f"draft {n}",
                    quote=f"quote {n}",
                    evidence=ev,
                )
            )
        s.commit()
    wdir = tmp_path / "walks" / "wlk_p"
    drafts = [
        {"item_id": f"itm_{n}", "quote": f"quote {n}", "text": f"quote {n}", "room": None}
        for n in range(1, 9)
    ]
    write_stage_output(wdir, Stage.fuse, {"drafts": drafts})
    write_stage_output(wdir, Stage.pick_evidence, {"items": {}})
    ctx = StageContext(
        walk_id="wlk_p", project_id="prj_p", kind=WalkKind.initial, walk_dir=wdir, session_factory=factory
    )
    return ctx, factory


def test_write_items_applies_results_in_item_order(walk_db, monkeypatch) -> None:
    from sqlmodel import select

    from punchwalk.pipeline import runner, write_items

    ctx, factory = walk_db
    rng = random.Random(3)
    finished: list[int] = []

    def fake_write_one(ctx_, draft, ev, extra):
        n = int(draft["item_id"].split("_")[1])
        _pause(rng.uniform(0.0, 0.04))
        _record(f"write-{n}")
        finished.append(n)
        return {
            "ok": True,
            "provider": "baseten",
            "model": "m",
            "model_view": [720, 1280],
            "defect_box": None,
            "result": {
                "title": f"Item {n}",
                "description": f"Desc {n}.",
                "action": "Fix it",
                "trade": "painting",
                "room": None,
                "location_detail": None,
                "severity": "cosmetic",
                "sam_concept": "wall",
                "defect_box": None,
                "confidence": 0.9,
                "rationale": "r",
            },
        }

    monkeypatch.setattr(write_items, "write_one", fake_write_one)
    reports: list[dict] = []
    ctx.on_progress = reports.append
    buf = runner._CallBuffer("wlk_p")
    calls.add_sink(buf)
    with calls.call_context(walk_id="wlk_p", stage=Stage.write_items.value):
        out = write_items.run(ctx)
    assert out.counts["written"] == 8
    assert finished != sorted(finished)  # the fake really finished out of order...
    assert [r.model for r in buf.records] == [f"write-{n}" for n in range(1, 9)]  # ...the records did not
    assert [r["written"] for r in reports] == list(range(9))
    assert list(ctx.output(Stage.write_items)["items"]) == [f"itm_{n}" for n in range(1, 9)]
    with factory() as s:
        items = s.exec(select(Item).order_by(Item.number)).all()
    assert [i.title for i in items] == [f"Item {n}" for n in range(1, 9)]


def test_verify_applies_checks_in_item_order(walk_db, monkeypatch) -> None:
    from sqlmodel import select

    from punchwalk.pipeline import runner, verify
    from punchwalk.pipeline.context import write_stage_output

    ctx, factory = walk_db
    write_stage_output(
        ctx.walk_dir, Stage.write_items, {"items": {f"itm_{n}": {"ok": True} for n in range(1, 9)}}
    )
    rng = random.Random(11)

    def fake_check(item, ev):
        n = item.number
        _pause(rng.uniform(0.0, 0.04))
        _record(f"check-{n}")
        ok = n % 3 != 0
        return verify.ItemCheck(
            consistent=ok,
            frame_shows_defect=True,
            issues=[] if ok else [f"issue {n}"],
            suggested_fix=None,
            rationale="r",
        ), "baseten"

    monkeypatch.setattr(verify, "check_one", fake_check)
    buf = runner._CallBuffer("wlk_p")
    calls.add_sink(buf)
    with calls.call_context(walk_id="wlk_p", stage=Stage.verify.value):
        out = verify.run(ctx)
    assert out.counts == {"checked": 8, "matched": 6, "flagged": 2, "failed": 0, "total": 8}
    assert [r.model for r in buf.records] == [f"check-{n}" for n in range(1, 9)]
    assert list(ctx.output(Stage.verify)["items"]) == [f"itm_{n}" for n in range(1, 9)]
    with factory() as s:
        items = s.exec(select(Item).order_by(Item.number)).all()
    assert [i.review_reason for i in items if i.review_reason] == ["issue 3.", "issue 6."]
