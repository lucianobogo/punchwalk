"""Eval harness end to end (PLAN 3.1-3.3) on the recorded synthetic walk, plus scripts/build_mock_api.py.

The synthetic walk (fixtures/pipeline/synth_walk, see tests/test_pipeline_integration.py) replays offline;
its labels are fixtures/eval/labels_synth/synth_walk.csv: knob (Kitchen, part a, pointed), switch plate
(Hallway, part b, pointed), scuff marks (Bedroom, part c, voice-only). Assertions stick to what a re-record
can't change (all three found, rooms right, both pointed items outlined); model-dependent numbers such as the
trade are checked for consistency between the table, report.md and report.json, not for a value.

The pipeline runs once per session into a template DATA_DIR; each test works on its own copy (resumed,
so nothing re-runs). The re-walk is faked at the `run_walk` seam (no recorded re-walk exists yet).
"""

from __future__ import annotations

import csv
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlmodel import Session, create_engine, select

from eval import run_eval
from eval.labels import ProjectConfig, with_times
from eval.matching import match_walk, pred_from_item
from eval.metrics import initial_metrics
from eval.report import EvalRun, WalkEval, render_json, render_markdown, text_table
from punchwalk.core.enums import STAGES_REWALK, ItemStatus, JobStatus, StageStatus, WalkKind, WalkStatus
from punchwalk.core.models import Item, Job, Walk
from scripts import build_mock_api as bm
from tests.conftest import FIXTURES

SYNTH = FIXTURES / "pipeline" / "synth_walk"
LABELS = FIXTURES / "eval" / "labels_synth"

if shutil.which("ffmpeg") is None:
    pytest.skip("ffmpeg not installed", allow_module_level=True)

_template: dict[str, Path] = {}


STALE = ("the synthetic walk's recorded answers no longer match the pipeline's prompts/frames (a cache miss; "
         "tests/test_pipeline_integration.py fails the same way): re-record fixtures/pipeline/synth_walk")


@pytest.fixture
def data_dir(tmp_path_factory, tmp_path) -> Path:
    """A DATA_DIR where the synthetic walk already ran through the eval (copied per test).

    PUNCHWALK_EVAL_SYNTH_DATA=<dir> starts from an existing eval data dir instead (a dev aid while the
    synthetic fixture is being re-recorded); it is copied, never changed.
    """
    if "dir" not in _template:
        base = tmp_path_factory.mktemp("eval_template")
        seed = os.environ.get("PUNCHWALK_EVAL_SYNTH_DATA")
        if seed:
            shutil.copytree(seed, base / "data")
        report = base / "out" / "report.md"
        code = run_eval.main(["--labels", str(LABELS), "--walks", "synth_walk", "--data-dir", str(base / "data"),
                              "--report", str(report)])
        assert code == 0
        doc = json.loads(report.with_suffix(".json").read_text())
        _template.update(dir=base / "data", report=report, error=doc["walks"][0]["error"])
    if _template["error"]:
        if "recorded model answer is missing" in _template["error"]:
            pytest.skip(STALE)
        pytest.fail(f"the synthetic walk failed in the eval: {_template['error']}")
    dest = tmp_path / "data"
    shutil.copytree(_template["dir"], dest)
    return dest


def engine_for(data: Path):
    return create_engine(f"sqlite:///{data / 'punchwalk.sqlite3'}")


# --------------------------------------------------------------------------- run_eval


def test_no_labels_is_a_clean_exit(tmp_path, capsys, monkeypatch):
    empty = tmp_path / "labels"
    empty.mkdir()
    monkeypatch.setattr(run_eval, "DEFAULT_LABELS", empty)  # what `make eval` reads
    assert run_eval.main(["--report", str(tmp_path / "r.md")]) == 0
    assert f"no labels in {empty}/ (expected walk1.csv, walk2.csv, walk3.csv" in capsys.readouterr().out
    assert not (tmp_path / "r.md").exists()


def test_template_labels_are_refused(tmp_path, capsys):
    labels = tmp_path / "labels"
    labels.mkdir()
    for name in ("walk1", "walk2", "walk3"):
        shutil.copyfile(run_eval.REPO_ROOT / "eval" / "labels_template.csv", labels / f"{name}.csv")
    assert run_eval.main(["--labels", str(labels), "--report", str(tmp_path / "r.md")]) == 0
    out = capsys.readouterr().out
    assert "walk1.csv is still the template — fill it in per FOOTAGE.md" in out
    assert "walk3.csv is still the template" in out and not (tmp_path / "r.md").exists()
    assert run_eval.main(["--labels", str(labels), "--strict", "--report", str(tmp_path / "r.md")]) == 1


def test_synthetic_walk_scores_and_writes_the_report(data_dir, tmp_path, capsys):
    report = tmp_path / "report.md"
    code = run_eval.main(["--labels", str(LABELS), "--walks", "synth_walk", "--data-dir", str(data_dir),
                          "--report", str(report)])
    out = capsys.readouterr().out
    assert code == 0
    assert "synth_walk  Recall                    1.00 (3/3)  >= 0.90  pass" in out
    doc = json.loads(report.with_suffix(".json").read_text())
    trade_row = next(r for r in doc["summary"] if r["key"] == "trade")
    assert f"synth_walk  Trade accuracy            {trade_row['value']}  >= 0.85  {trade_row['result']}" in out
    md = report.read_text()
    assert "| synth_walk | Room accuracy | 1.00 (3/3) | >= 0.90 | pass |" in md
    assert "Clip parts: a at 00:00.0, b at 00:07.0, c at 00:14.0." in md
    assert "| 1 | 00:00.0 | #001 |" in md and "Carpentry & millwork / " in md  # label 1 vs its item
    assert "The frame doesn't show the defect described." in md  # review_reason of the contradictory item
    assert "## Timing and cost" in md and "| synth_walk |" in md.split("## Timing and cost")[1]
    m = doc["walks"][0]["metrics"]
    assert (m["recall"]["num"], m["precision"]["num"], m["room"]["num"]) == (3, 3, 3)
    assert m["trade"]["den"] == 3 and f"| synth_walk | Trade accuracy | {trade_row['value']} |" in md
    assert m["grounding"] == {"num": 2, "den": 2, "value": 1.0}  # both pointed items outlined by SAM
    assert m["needs_review"]["den"] == 3
    assert doc["app_summary"]["metrics"][0] == {"key": "defects_found", "label": "Defects found", "value": 3, "total": 3}

    # the same DATA_DIR gives the same report, byte for byte (nothing re-runs, no "now")
    again = tmp_path / "again.md"
    run_eval.main(["--labels", str(LABELS), "--walks", "synth_walk", "--data-dir", str(data_dir), "--report", str(again)])
    assert again.read_text() == md
    assert again.with_suffix(".json").read_text() == report.with_suffix(".json").read_text()
    # --strict fails when a target is missed: add a label nothing was said about (recall 3/4)
    strict = tmp_path / "strict_labels"
    shutil.copytree(LABELS, strict)
    with (strict / "synth_walk.csv").open("a") as f:
        f.write("synth_walk,4,Bedroom,Door does not latch,doors_hardware,00:07,1,,,c\n")
    assert run_eval.main(["--labels", str(strict), "--data-dir", str(data_dir), "--report", str(again), "--strict"]) == 1
    assert run_eval.main(["--labels", str(strict), "--data-dir", str(data_dir), "--report", str(again)]) == 0

    with Session(engine_for(data_dir)) as s:  # eval rows look like a real upload: walk + finished job
        walk = s.get(Walk, "wlk_eval_synth_walk")
        assert walk.status == WalkStatus.ready and walk.project_id == run_eval.PROJECT_ID
        assert s.get(Job, "job_eval_synth_walk").status == JobStatus.done


def _rewalk_setup(tmp_path: Path, *, done: bool, monkeypatch) -> tuple[Path, Path]:
    """Labels synth_walk + synth_walk2 (re-walk), media for synth_walk2, and a fake re-walk run."""
    labels = tmp_path / "labels"
    labels.mkdir()
    shutil.copyfile(LABELS / "synth_walk.csv", labels / "synth_walk.csv")
    shutil.copyfile(LABELS / "project.json", labels / "project.json")
    (labels / "synth_walk2.csv").write_text(
        "walk,item_no,room,description,trade,time_mmss,pointed,fixed_in_rewalk,claimed_fixed_in_rewalk,clip\n"
        "synth_walk2,1,Kitchen,Upper cabinet left of the sink missing knob,carpentry_millwork,00:01,1,1,1,a\n"
        "synth_walk2,2,Hallway,Switch plate is crooked,electrical,00:01,1,0,1,b\n"
        "synth_walk2,3,Bedroom,Scuff marks on the wall by the door,painting,00:01,0,0,0,c\n"
    )
    media_root = tmp_path / "record"
    shutil.copytree(SYNTH / "media", media_root / "synth_walk2")
    import punchwalk.pipeline.runner as runner

    real = runner.run_walk

    def fake_run_walk(walk_id, *, session_factory, **kw):
        if walk_id != "wlk_eval_synth_walk2":
            return real(walk_id, session_factory=session_factory, **kw)
        now = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
        with session_factory() as s:
            w = s.get(Walk, walk_id)
            assert w.kind == WalkKind.rewalk and w.rewalk_of == "wlk_eval_synth_walk"  # the thin step's contract
            w.stages = {st.value: {"stage": st.value, "duration_ms": 5,
                                   "status": StageStatus.done if done or st.value != "resolve_closures"
                                   else StageStatus.skipped} for st in STAGES_REWALK}
            w.status = WalkStatus.ready
            w.clips = list(s.get(Walk, "wlk_eval_synth_walk").clips)  # what ingest would record (same media)
            s.add(w)
            if done:  # what Phase 7 decides: #1 signed off, #2 false claim caught, #3 still open
                outcome = {1: ("closed", "signed_off"), 2: ("needs_review", "needs_review"), 3: ("dispatched", "still_open")}
                for it in s.exec(select(Item).where(Item.origin_walk_id == "wlk_eval_synth_walk")).all():
                    status, decision = outcome[it.number]
                    if it.status != status:
                        it.history = [*it.history, {"at": now.isoformat(), "actor": "system", "action": "rewalk_checked",
                                                    "field": "status", "from": it.status.value, "to": status}]
                    it.status = ItemStatus(status)
                    it.closure = {"rewalk_id": walk_id, "decision": decision, "decided_at": now.isoformat(),
                                  "after_t": 1.2 + 7.0 * (it.number - 1)}  # 00:01 into part a / b / c
                    s.add(it)
            s.commit()

    monkeypatch.setattr(runner, "run_walk", fake_run_walk)
    return labels, media_root


def test_rewalk_metrics_when_closures_ran(data_dir, tmp_path, monkeypatch, capsys):
    labels, media_root = _rewalk_setup(tmp_path, done=True, monkeypatch=monkeypatch)
    report = tmp_path / "report.md"
    code = run_eval.main(["--labels", str(labels), "--walks", "synth_walk2", "--data-dir", str(data_dir),
                          "--media-root", str(media_root), "--report", str(report)])
    out = capsys.readouterr().out
    assert code == 0
    assert "synth_walk2  Closure accuracy          1.00 (3/3)  >= 0.90  pass" in out
    assert "synth_walk2  False-claim catch         1.00 (1/1)  = 1.00   pass" in out
    first = json.loads(_template["report"].with_suffix(".json").read_text())
    before = next(r for r in first["summary"] if r["key"] == "needs_review")["value"]
    assert f"synth_walk   Needs-review rate         {before}" in out  # status before the re-walk, from history
    md = report.read_text()
    assert "## synth_walk2 (re-walk of synth_walk)" in md
    assert "| 2 | 00:08.0 | yes | no | #002 | needs_review | needs_review | no | yes | yes |" in md
    doc = json.loads(report.with_suffix(".json").read_text())
    assert doc["app_summary"]["metrics"][-1]["key"] == "false_claims_caught"
    with Session(engine_for(data_dir)) as s:
        assert s.get(Job, "job_eval_synth_walk2").status == JobStatus.done


def test_rewalk_metrics_are_na_until_phase7(data_dir, tmp_path, monkeypatch, capsys):
    labels, media_root = _rewalk_setup(tmp_path, done=False, monkeypatch=monkeypatch)
    code = run_eval.main(["--labels", str(labels), "--data-dir", str(data_dir), "--media-root", str(media_root),
                          "--report", str(tmp_path / "r.md"), "--strict"])
    out = capsys.readouterr().out
    assert "Closure accuracy          n/a (Phase 7 pending)" in out
    doc = json.loads((tmp_path / "r.json").read_text())
    assert [r["result"] for r in doc["summary"] if r["walk"] == "synth_walk2"] == ["n/a", "n/a"]
    failing = any(r["result"] in ("FAIL", "error") for r in doc["summary"])
    assert code == (1 if failing else 0)  # pending closure rows never fail --strict
    assert "Closure metrics: n/a" in (tmp_path / "r.md").read_text()


def test_missing_media_is_an_error_row_not_a_crash(tmp_path, capsys):
    labels = tmp_path / "labels"
    labels.mkdir()
    (labels / "walk9.csv").write_text(
        "walk,item_no,room,description,trade,time_mmss,pointed,fixed_in_rewalk,claimed_fixed_in_rewalk,clip\n"
        "walk9,1,Kitchen,Knob missing,carpentry_millwork,00:05,1,,,\n")
    code = run_eval.main(["--labels", str(labels), "--report", str(tmp_path / "r.md"), "--media-root", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0 and "error: no media for walk9" in out
    md = (tmp_path / "r.md").read_text()
    assert "The pipeline failed: no media for walk9" in md
    # no project.json next to these labels: eval/project.json, the rooms `make record` uses
    assert "Project rooms: Kitchen, Bathroom, Bedroom, Hallway, Living room (" in md and "eval/project.json" in md


def test_live_mode_shares_the_real_ledger(tmp_path):
    ledger = tmp_path / "real" / "cost_ledger.jsonl"
    before = os.environ.get("DATA_DIR")
    with run_eval.eval_environment(tmp_path / "eval", live=True, orig_cache_dir=tmp_path / "real" / "cache",
                                   orig_ledger=ledger):
        from punchwalk.config import get_settings

        s = get_settings()
        assert s.PUNCHWALK_CACHE == "readwrite" and s.cache_dir == tmp_path / "real" / "cache"
        assert s.ledger_path.resolve() == ledger.resolve()  # the $25 cap counts eval-live calls
    assert os.environ.get("DATA_DIR") == before


def test_holdout_is_reported_separately(tmp_path):
    from eval.labels import read_labels_file

    (tmp_path / "walk3.csv").write_text(
        "walk,item_no,room,description,trade,time_mmss,pointed,fixed_in_rewalk,claimed_fixed_in_rewalk,clip\n"
        "walk3,1,Kitchen,Knob missing,carpentry_millwork,00:05,1,,,\n")
    labels = with_times(read_labels_file(tmp_path / "walk3.csv"), [0.0])
    it = Item(id="itm_1", project_id="p", number=1, origin_walk_id="w", title="Knob missing", room="Kitchen",
              trade="carpentry_millwork", quote_start_s=5.5, evidence={"grounding": "sam_box"}, point_id="pnt_1")
    preds = [pred_from_item(it)]
    match = match_walk(labels, preds)
    w3 = WalkEval(name="walk3", walk_id="wlk_eval_walk3", kind="initial", holdout=True, labels=labels, preds=preds,
                  match=match, initial=initial_metrics(labels, preds, match))
    run = EvalRun(labels_dir="eval/labels", cache_mode="readonly", project=ProjectConfig(["Kitchen"], [], "x"),
                  walks=[w3])
    text = text_table(run.rows())
    assert "(no tuned walks)" in text and "holdout (never used for tuning):" in text
    md = render_markdown(run)
    assert "## Holdout (never used for tuning)" in md and "## walk3 (first walk, holdout)" in md
    assert json.loads(render_json(run))["app_summary"] is None  # holdout numbers never reach the app
    assert render_markdown(run) == md


# --------------------------------------------------------------------------- build_mock_api


def test_build_mock_api_from_the_synthetic_run(data_dir, tmp_path):
    out = tmp_path / "mock_synth"
    bundle, _ = bm.build(data_dir, out, eval_summary=_template["report"].with_suffix(".json"))
    assert bm.validate_mock_dir(out) == []
    items = json.loads((out / "projects/prj_eval/items.json").read_text())["items"]
    assert [i["number"] for i in items] == [1, 2, 3]
    first = items[0]
    assert first["evidence"]["raw_url"] == f"/media/items/{first['id']}/frame.jpg"  # mock naming
    assert first["video_url"] == "/media/walks/wlk_eval_synth_walk/display.mp4"
    assert (out / "media/walks/wlk_eval_synth_walk/display.mp4").stat().st_size < 40_000_000
    assert json.loads((out / f"items/{first['id']}.json").read_text()) == first  # list copy == item file
    tok = json.loads((out / f"items/{first['id']}/share.post.json").read_text())["token"]
    assert tok == bm.share_token_for(first["id"]) and (out / f"share/{tok}.json").is_file()
    proc = json.loads((out / "walks/wlk_eval_synth_walk/processing.json").read_text())
    assert proc["eval"] is None  # like the live API: a report scored on synthetic labels is not shown
    walks = {w["id"]: w["status"] for w in json.loads((out / "walks.json").read_text())["walks"]}
    assert walks["wlk_eval_synth_walk"] == "ready" and walks["wlk_3a_first"] == "processing"  # example kept
    assert (out / "projects/prj_unit3a/walks.post.json").is_file()  # the upload the web mock replays
    assert (out / "README.md").read_text().startswith("# Mock API (built from real pipeline outputs)")

    # deterministic: a second build from the same DATA_DIR is byte-identical
    out2 = tmp_path / "mock_again"
    bm.build(data_dir, out2, eval_summary=_template["report"].with_suffix(".json"))
    files = sorted(p.relative_to(out) for p in out.rglob("*") if p.is_file())
    assert files == sorted(p.relative_to(out2) for p in out2.rglob("*") if p.is_file())
    for f in files:
        assert (out / f).read_bytes() == (out2 / f).read_bytes(), f
    # a report scored on eval/labels/ fills the block, as GET .../processing does
    real = tmp_path / "report.json"
    doc = json.loads(_template["report"].with_suffix(".json").read_text())
    real.write_text(json.dumps({**doc, "labels_dir": "eval/labels"}))
    out3 = tmp_path / "mock_real_report"
    bm.build(data_dir, out3, eval_summary=real, processing_from=None)
    proc = json.loads((out3 / "walks/wlk_eval_synth_walk/processing.json").read_text())
    assert proc["eval"]["source"] == "eval/report.md" and proc["eval"]["metrics"][0]["value"] == 3

    # rebuilding into an existing mock dir replaces it
    bm.build(data_dir, out, eval_summary=None, processing_from=None)
    assert not (out / "walks/wlk_3a_first.json").exists() and bm.validate_mock_dir(out) == []

    # the source DATA_DIR is untouched: no share tokens, no overlay cache written
    with Session(engine_for(data_dir)) as s:
        assert all(i.share_token is None for i in s.exec(select(Item)).all())
    assert not (data_dir / "walks/wlk_eval_synth_walk/overlay.json").exists()


def test_build_mock_api_refuses_the_committed_mocks_and_foreign_dirs(data_dir, tmp_path, capsys):
    healthz = bm.DEFAULT_OUT / "healthz.json"
    before = healthz.stat().st_mtime_ns
    assert bm.main(["--data-dir", str(data_dir)]) == 2
    assert "refusing to overwrite fixtures/mock_api without --force" in capsys.readouterr().out
    assert healthz.stat().st_mtime_ns == before
    foreign = tmp_path / "notes"
    foreign.mkdir()
    (foreign / "todo.txt").write_text("keep me")
    assert bm.main(["--data-dir", str(data_dir), "--out", str(foreign)]) == 2
    assert (foreign / "todo.txt").read_text() == "keep me"
    assert bm.main(["--data-dir", str(tmp_path / "nowhere"), "--out", str(tmp_path / "o")]) == 2
    assert "no database" in capsys.readouterr().out


# --------------------------------------------------------------------------- draft_labels


def test_label_drafts_from_the_pipeline(data_dir, tmp_path, monkeypatch):
    from eval import draft_labels as dl
    from eval.labels import discover_labels, read_labels_file

    out = tmp_path / "drafts"
    assert dl.main(["--data-dir", str(data_dir), "--walks", "synth_walk", "--out", str(out)]) == 0
    rows = read_labels_file(out / "synth_walk.csv")  # the exact labels format
    hand = read_labels_file(LABELS / "synth_walk.csv")
    assert [(r.item_no, r.room, r.time_mmss, r.clip, r.pointed) for r in rows] == \
        [(r.item_no, r.room, r.time_mmss, r.clip, r.pointed) for r in hand]  # times within the part, like a person
    assert all(r.trade and r.description and r.fixed_in_rewalk is None for r in rows)
    assert "DRAFT" in (out / "README.md").read_text() and "not ground\ntruth" in (out / "README.md").read_text()
    assert list(discover_labels(out)) == ["synth_walk"]  # parseable, and not mistaken for the template
    assert dl.main(["--data-dir", str(data_dir), "--walks", "synth_walk", "--out", str(dl.LABELS_DIR)]) == 2
    assert dl.main(["--data-dir", str(data_dir), "--walks", "walk7", "--out", str(out)]) == 2

    # re-walk draft: the first walk's item numbers, the re-walk moment from the closure, truth columns blank
    labels, media_root = _rewalk_setup(tmp_path, done=True, monkeypatch=monkeypatch)
    run_eval.main(["--labels", str(labels), "--data-dir", str(data_dir), "--media-root", str(media_root),
                   "--report", str(tmp_path / "r.md")])
    assert dl.main(["--data-dir", str(data_dir), "--walks", "synth_walk2", "--out", str(out)]) == 0
    rows2 = list(csv.reader((out / "synth_walk2.csv").read_text().splitlines()))[1:]
    assert [r[1] for r in rows2] == ["1", "2", "3"]
    assert [r[5:] for r in rows2] == [["00:01", "", "", "", "a"], ["00:01", "", "", "", "b"], ["00:01", "", "", "", "c"]]
