#!/usr/bin/env python
"""`make eval`: run the pipeline from the cache for every labeled walk and score it.

    uv run python eval/run_eval.py                       # every walk in eval/labels/*.csv, readonly cache
    uv run python eval/run_eval.py --walks walk1 walk2   # a subset (a re-walk pulls in its first walk)
    uv run python eval/run_eval.py --labels fixtures/eval/labels_synth --data-dir .verify/eval-synth
    PUNCHWALK_CACHE=readwrite uv run python eval/run_eval.py --live    # `make eval-live`: may call the APIs

For each labeled walk, in natural order (walk1, walk2, walk3, ...), in one project like `make record --full`:
1. Media: DATA_DIR/record/<walk>/ (what `make record` writes), else fixtures/pipeline/<walk>/media/.
   A per-walk recorded cache in fixtures/pipeline/<walk>/cache/ is added to the read path.
2. Pipeline: `runner.run_walk` on a temp DATA_DIR (or --data-dir, reused and resumed), with
   PUNCHWALK_CACHE=readonly unless --live. Walk ids are `wlk_eval_<walk>`; a re-walk (labels with
   fixed_in_rewalk / claimed_fixed_in_rewalk) runs with rewalk_of = the first walk before it.
3. Match items to labels (eval/matching.py), compute metrics (eval/metrics.py), print the summary
   table against the targets (eval/metrics.py) and write eval/report.md + eval/report.json (eval/report.py).

walk3 (--holdout) is reported separately and never used for tuning. Exit code is 0 unless --strict and
a non-holdout target fails or a walk's pipeline errors. A failing walk never stops the others.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if sys.path and Path(sys.path[0]).resolve() == Path(__file__).resolve().parent:
    sys.path[0] = str(REPO_ROOT)  # run as a script: import `eval.*` as a package, not its modules
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlmodel import col, select  # noqa: E402

from eval.labels import (  # noqa: E402
    Label,
    LabelError,
    ProjectConfig,
    discover_labels,
    is_rewalk,
    project_config,
    with_times,
)
from eval.matching import match_walk, pred_from_item  # noqa: E402
from eval.metrics import any_failed, initial_metrics, rewalk_metrics  # noqa: E402
from eval.report import CostSummary, EvalRun, StageTiming, WalkEval, render_json, render_markdown, text_table  # noqa: E402
from punchwalk.clients import cost as cost_mod  # noqa: E402
from punchwalk.config import get_settings, reset_settings  # noqa: E402
from punchwalk.core.enums import (  # noqa: E402
    ItemStatus,
    JobStatus,
    Provider,
    Stage,
    StageStatus,
    WalkKind,
    model_label,
)
from punchwalk.core.models import ClipInfo, Item, Job, ModelCall, Project, Walk, utcnow  # noqa: E402
from punchwalk.db import as_utc, reset_engines, session_factory  # noqa: E402

DEFAULT_LABELS = REPO_ROOT / "eval" / "labels"
DEFAULT_REPORT = REPO_ROOT / "eval" / "report.md"
PROJECT_ID = "prj_eval"


def walk_id_for(name: str) -> str:
    return "wlk_eval_" + "".join(ch if ch.isalnum() else "_" for ch in name.lower())


def rel_repo(path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------- environment


# Every setting that lets a walk send something (punchwalk/config.py). Process env beats `.env`, and an empty
# string is a real value for pydantic-settings, so these override a webhook/key that `.env` sets.
NO_SEND_ENV: dict[str, str] = {
    "AUTO_SEND": "false",  # no auto-send after a first walk, no re-send after a re-walk (runner._persist)
    "SLACK_WEBHOOK_URL": "",  # the team's Slack webhook
    "RESEND_API_KEY": "",  # email via Resend
    "EMAIL_FROM": "",
}


def build_eval_env(data_dir: Path, *, live: bool, orig_cache_dir: Path) -> dict[str, str]:
    """The environment variables a run sets: DATA_DIR, the cache mode/dir, and every send channel off."""
    return {
        "DATA_DIR": str(data_dir),
        "PUNCHWALK_CACHE": "readwrite" if live else "readonly",
        "PUNCHWALK_CACHE_DIR": str(orig_cache_dir if live else data_dir / "cache"),
        **NO_SEND_ENV,
    }


@contextmanager
def eval_environment(data_dir: Path, *, live: bool, orig_cache_dir: Path, orig_ledger: Path) -> Iterator[None]:
    """Point DATA_DIR (database, walk folders) at `data_dir` for the run; restore the env afterwards.

    readonly: reads come from <data_dir>/cache (a copy of the normal cache dir, e.g. data/cache, plus the
    per-walk fixture caches) + FIXTURE_CACHE_DIR (fixtures/cache). Nothing is written back.
    --live: the cache stays the normal one (new answers persist) and the cost ledger is the normal one
    (linked in), so the $25 cap still counts every live call.
    Both modes also switch every send channel off (NO_SEND_ENV): eval measures the pipeline and never posts
    to Slack or sends email, even when `.env` carries a real webhook or Resend key.
    """
    env = build_eval_env(data_dir, live=live, orig_cache_dir=orig_cache_dir)
    saved = {k: os.environ.get(k) for k in env}
    data_dir.mkdir(parents=True, exist_ok=True)
    if not live and orig_cache_dir.is_dir() and orig_cache_dir.resolve() != (data_dir / "cache").resolve():
        shutil.copytree(orig_cache_dir, data_dir / "cache", dirs_exist_ok=True)  # data/cache answers, read-only copy
    if live:
        link = data_dir / "cost_ledger.jsonl"
        if link.resolve() != orig_ledger.resolve():
            orig_ledger.parent.mkdir(parents=True, exist_ok=True)
            orig_ledger.touch(exist_ok=True)
            link.unlink(missing_ok=True)
            link.symlink_to(orig_ledger.resolve())
    os.environ.update(env)
    reset_settings()
    reset_engines()
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        reset_settings()
        reset_engines()


# --------------------------------------------------------------------------- inputs


def find_media(name: str, media_root: Path) -> Path:
    """Folder with the walk's prepared media (meta.json + the files it names)."""
    for cand in (media_root / name, REPO_ROOT / "fixtures" / "pipeline" / name / "media"):
        if (cand / "meta.json").is_file():
            return cand
    raise FileNotFoundError(
        f"no media for {name}: expected {media_root / name / 'meta.json'} (run `make record`) "
        f"or fixtures/pipeline/{name}/media/meta.json"
    )


def copy_media(src: Path, dest: Path) -> None:
    from punchwalk.pipeline.ingest import IngestMeta

    meta = IngestMeta.load(src)
    dest.mkdir(parents=True, exist_ok=True)
    for path in [*meta.output_paths(src), src / "meta.json"]:
        if not path.is_file():
            raise FileNotFoundError(f"{src} is missing {path.name} (named in its meta.json)")
        shutil.copy2(path, dest / path.name)


def seed_cache(name: str, cache_dir: Path) -> int:
    """Add fixtures/pipeline/<walk>/cache/ (a walk's own recorded answers) to the read path."""
    src = REPO_ROOT / "fixtures" / "pipeline" / name / "cache"
    if not src.is_dir():
        return 0
    shutil.copytree(src, cache_dir, dirs_exist_ok=True)
    return sum(1 for p in src.rglob("*.json"))


# --------------------------------------------------------------------------- pipeline


def ensure_project(project: ProjectConfig) -> None:
    with session_factory() as s:
        row = s.get(Project, PROJECT_ID)
        if row is None:
            s.add(Project(id=PROJECT_ID, name="Eval walks", rooms=list(project.rooms), keywords=list(project.keywords)))
            s.commit()
        elif list(row.rooms or []) != project.rooms or list(row.keywords or []) != project.keywords:
            print(f"   note: {PROJECT_ID} in this data dir has rooms {row.rooms} / keywords {row.keywords}; "
                  f"keeping them (delete the data dir to use {project.rooms})")


def walk_needs_run(walk: Walk) -> bool:
    from punchwalk.pipeline.runner import stage_order

    stages = walk.stages or {}
    return any((stages.get(st.value) or {}).get("status") != StageStatus.done for st in stage_order(walk.kind))


def run_pipeline(name: str, kind: WalkKind, rewalk_of: str | None, media_root: Path, *, force: bool) -> tuple[str, str | None, str | None]:
    """Create (or reuse) the walk + a Job row and run it. Returns (walk_id, error, error_detail)."""
    from punchwalk.pipeline.context import walk_dir
    from punchwalk.pipeline.runner import run_walk

    walk_id = walk_id_for(name)
    job_id = "job_eval_" + walk_id.removeprefix("wlk_eval_")
    try:
        src = find_media(name, media_root)
    except FileNotFoundError as exc:
        return walk_id, str(exc), None
    seed_cache(name, get_settings().cache_dir)
    with session_factory() as s:
        walk = s.get(Walk, walk_id)
        if walk is None:
            walk = Walk(id=walk_id, project_id=PROJECT_ID, kind=kind, rewalk_of=rewalk_of)
            s.add(walk)
            s.commit()
        needs = force or walk_needs_run(walk)
    wdir = walk_dir(walk_id)
    if not (wdir / "meta.json").is_file():
        copy_media(src, wdir)
    if not needs:
        return walk_id, None, None
    with session_factory() as s:
        job = s.get(Job, job_id) or Job(id=job_id, walk_id=walk_id)
        job.status, job.attempts, job.started_at, job.error = JobStatus.running, job.attempts + 1, utcnow(), None
        job.force = force
        s.add(job)
        s.commit()
    error = detail = None
    try:
        run_walk(walk_id, session_factory=session_factory, force=force)
    except Exception as exc:  # noqa: BLE001 - one broken walk must not stop the eval
        detail = f"{type(exc).__name__}: {exc}"
        with session_factory() as s:
            error = s.get(Walk, walk_id).error or detail
        if os.environ.get("PUNCHWALK_EVAL_TRACEBACK"):
            traceback.print_exc()
    with session_factory() as s:
        job = s.get(Job, job_id)
        job.status = JobStatus.failed if error else JobStatus.done
        job.error, job.finished_at = error, utcnow()
        s.add(job)
        s.commit()
    return walk_id, error, detail


def status_before_rewalk(item: Item) -> ItemStatus:
    """The item's status before a re-walk decided it (history), else its status now."""
    closure = item.closure if isinstance(item.closure, dict) else None
    if not closure:
        return ItemStatus(item.status)
    since = closure.get("decided_at")
    for h in sorted(item.history or [], key=lambda h: str(h.get("at") or "")):
        if h.get("field") == "status" and h.get("from") and (since is None or str(h.get("at")) >= str(since)[:19]):
            try:
                return ItemStatus(h["from"])
            except ValueError:
                break
    return ItemStatus(item.status)


def load_items(walk_id: str) -> list[Item]:
    with session_factory() as s:
        return list(s.exec(
            select(Item).where(Item.origin_walk_id == walk_id, col(Item.dismissed_at).is_(None)).order_by(Item.number)
        ).all())


def clip_offsets(walk_id: str) -> list[float]:
    with session_factory() as s:
        walk = s.get(Walk, walk_id)
        clips = sorted((ClipInfo.model_validate(c) for c in (walk.clips or [])), key=lambda c: c.order)
    return [c.offset_s for c in clips] or [0.0]


def stage_timings(walk: Walk) -> list[StageTiming]:
    from punchwalk.pipeline.runner import stage_order

    out = []
    for st in stage_order(walk.kind):
        raw = (walk.stages or {}).get(st.value)
        if raw:
            out.append(StageTiming(st.value, str(raw.get("status")), int(raw.get("duration_ms") or 0)))
    return out


def cost_summary(walk: Walk) -> CostSummary:
    with session_factory() as s:
        calls = s.exec(select(ModelCall).where(ModelCall.walk_id == walk.id)).all()
    c = CostSummary(calls=len(calls))
    for m in calls:
        c.cached += int(m.cached)
        c.failed += int(not m.ok)
        c.fallback_calls += int(m.provider == Provider.meta_fallback)
        c.live_usd += 0.0 if m.cached else m.est_cost_usd
        c.latency_ms += m.latency_ms or 0
        label = model_label(m.model)
        c.by_model[label] = c.by_model.get(label, 0) + 1
        if not m.ok:
            continue
        if m.model.startswith("sam") and m.endpoint == "/responses":
            frames = m.frames or 0
            c.est_live_usd += cost_mod.sam_video_cost(frames) if frames > 1 else cost_mod.sam_image_cost(1)
        elif m.model.startswith("muse-voice"):
            c.est_live_usd += cost_mod.asr_cost(int((walk.duration_s or 0) * 1000))
        elif m.tokens_in is not None or m.tokens_out is not None:
            c.est_live_usd += cost_mod.llm_cost(m.provider.value, m.tokens_in, m.tokens_out)
        elif "spark" in m.model:
            c.llm_calls_without_tokens += 1
    c.live_usd, c.est_live_usd = round(c.live_usd, 4), round(c.est_live_usd, 4)
    return c


def finish_walk_facts(we: WalkEval) -> None:
    with session_factory() as s:
        walk = s.get(Walk, we.walk_id)
    if walk is None:
        return
    we.stages = stage_timings(walk)
    we.cost = cost_summary(walk)
    created = as_utc(walk.created_at)
    we.created_at = created.isoformat().replace("+00:00", "Z") if created else None


# --------------------------------------------------------------------------- orchestration


def load_labels(labels_dir: Path, only: list[str] | None) -> dict[str, list[Label]]:
    """Usable label files (only the walks asked for, plus a re-walk's first walk); problem files are reported."""
    problems: list[tuple[Path, str]] = []
    wanted = set(only) if only else None
    by_walk = discover_labels(labels_dir, walks=wanted, problems=problems)
    if wanted and any(is_rewalk(rows) for rows in by_walk.values()):
        everything = discover_labels(labels_dir, problems=[])
        first = next((w for w, rows in everything.items() if not is_rewalk(rows)), None)
        if first and first not in by_walk:
            by_walk = {w: everything[w] for w in everything if w in by_walk or w == first}
        elif first is None:
            problems += [(Path(labels_dir) / "walk1.csv", "the first walk a re-walk needs isn't labeled (or isn't usable)")]
    if wanted:
        missing = sorted(wanted - set(by_walk) - {p.stem for p, _ in problems})
        if missing:
            raise LabelError(f"no labels file for {', '.join(missing)} in {rel_repo(labels_dir)}/")
    for path, why in problems:
        if why == "template":
            print(f"{rel_repo(path)} is still the template — fill it in (see eval/README.md for the columns) ({path.stem} not scored)")
        else:
            print(f"{rel_repo(path)}: {why} — fix it (see eval/README.md for the columns) ({path.stem} not scored)")
    return by_walk


def plan_walks(by_walk: dict[str, list[Label]], only: list[str] | None) -> list[tuple[str, WalkKind, str | None]]:
    """[(walk, kind, rewalk_of walk name)] in natural order; a selected re-walk pulls in its first walk."""
    order = list(by_walk)
    kinds = {w: WalkKind.rewalk if is_rewalk(rows) else WalkKind.initial for w, rows in by_walk.items()}
    parent: dict[str, str | None] = {}
    last_initial: str | None = None
    for w in order:
        parent[w] = last_initial if kinds[w] == WalkKind.rewalk else None
        if kinds[w] == WalkKind.initial and last_initial is None:
            last_initial = w
    wanted = set(only or order)
    unknown = wanted - set(order)
    if unknown:
        raise LabelError(f"no labels for {', '.join(sorted(unknown))} (labeled: {', '.join(order)})")
    for w in sorted(wanted):
        if kinds[w] == WalkKind.rewalk and not parent[w]:
            print(f"{w} is a re-walk but no usable first walk is labeled before it: not scored")
            wanted.discard(w)
        elif parent.get(w):
            wanted.add(parent[w])  # type: ignore[arg-type]
    return [(w, kinds[w], parent[w]) for w in order if w in wanted]


def evaluate(by_walk: dict[str, list[Label]], plan: list[tuple[str, WalkKind, str | None]], *,
             holdout: set[str], media_root: Path, force: bool) -> list[WalkEval]:
    results: dict[str, WalkEval] = {}
    for name, kind, parent in plan:
        print(f"== {name} ({'re-walk of ' + parent if parent else 'first walk'})", flush=True)
        parent_id = walk_id_for(parent) if parent else None
        if parent and results[parent].error:
            we = WalkEval(name=name, walk_id=walk_id_for(name), kind=kind.value, holdout=name in holdout,
                          labels=by_walk[name], rewalk_of=parent,
                          error=f"its first walk ({parent}) failed, so it can't be checked")
            results[name] = we
            continue
        walk_id, error, detail = run_pipeline(name, kind, parent_id, media_root, force=force)
        we = WalkEval(name=name, walk_id=walk_id, kind=kind.value, holdout=name in holdout, labels=by_walk[name],
                      rewalk_of=parent, error=error, error_detail=detail)
        results[name] = we
        finish_walk_facts(we)
        if error:
            print(f"   failed: {error}")
            continue
        try:
            we.clip_offsets = clip_offsets(walk_id)
            we.labels = with_times(by_walk[name], we.clip_offsets)
        except LabelError as exc:
            we.error = str(exc)
            print(f"   labels: {exc}")
            continue
        if kind == WalkKind.initial:
            items = load_items(walk_id)
            we.preds = [pred_from_item(i, status=status_before_rewalk(i)) for i in items]
            we.match = match_walk(we.labels, we.preds)
            we.initial = initial_metrics(we.labels, we.preds, we.match)
            print(f"   {len(we.preds)} items, {len(we.match.pairs)}/{len(we.labels)} labels matched")
        else:
            first = results[parent]  # type: ignore[index]
            after = {i.id: pred_from_item(i) for i in load_items(first.walk_id)}
            with session_factory() as s:
                walk = s.get(Walk, walk_id)
                state = (walk.stages or {}).get(Stage.resolve_closures.value) or {}
            pending = state.get("status") != StageStatus.done
            we.preds = [pred_from_item(i) for i in load_items(walk_id)]
            we.rewalk = rewalk_metrics(we.labels, first.match, after, pending=pending, new_items=len(we.preds))
            print("   closures: " + ("n/a (Phase 7 pending)" if pending else
                                     f"{we.rewalk.closure.text() if we.rewalk.closure else 'n/a'}"))
    return [results[name] for name, _, _ in plan]


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Score the pipeline against the human labels (PLAN Phase 3).")
    ap.add_argument("--labels", type=Path, default=DEFAULT_LABELS, help="folder of label CSVs (default eval/labels)")
    ap.add_argument("--walks", nargs="*", help="only these walks (a re-walk pulls in its first walk)")
    ap.add_argument("--data-dir", type=Path, help="DATA_DIR for the run (default: a temp dir); reused and resumed")
    ap.add_argument("--media-root", type=Path, help="where `make record` put the walks (default DATA_DIR/record)")
    ap.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="report path (report.json goes next to it)")
    ap.add_argument("--holdout", nargs="*", default=["walk3"], help="walks reported separately, never tuned on")
    ap.add_argument("--rooms", nargs="*", help="project rooms (must match `make record --rooms`)")
    ap.add_argument("--keywords", nargs="*", help="project keywords (must match `make record --keywords`)")
    ap.add_argument("--live", action="store_true", help="allow live API calls on cache misses (make eval-live)")
    ap.add_argument("--force", action="store_true", help="re-run every stage (default: resume)")
    ap.add_argument("--strict", action="store_true", help="exit 1 when a non-holdout target fails")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        by_walk = load_labels(args.labels, args.walks)
    except LabelError as exc:
        print(f"labels: {exc}")
        return 2
    if not by_walk:
        if not any(Path(args.labels).glob("*.csv")):
            print(f"no labels in {rel_repo(args.labels)}/ (expected walk1.csv, walk2.csv, walk3.csv; "
                  "format in eval/labels_template.csv and eval/README.md)")
        else:
            print("nothing to score: every labels file asked for is still the template or needs fixing")
        return 1 if args.strict else 0
    try:
        plan = plan_walks(by_walk, args.walks)
    except LabelError as exc:
        print(f"labels: {exc}")
        return 2
    if not plan:
        print("nothing to score")
        return 1 if args.strict else 0
    project = project_config(args.labels, {w: by_walk[w] for w, _, _ in plan}, rooms=args.rooms,
                             keywords=args.keywords, fallback=REPO_ROOT / "eval" / "project.json")

    settings = get_settings()
    orig_data = Path(settings.DATA_DIR)
    media_root = args.media_root or orig_data / "record"
    tmp = None
    if args.data_dir is None:
        tmp = tempfile.TemporaryDirectory(prefix="punchwalk-eval-")
        data_dir = Path(tmp.name)
    else:
        data_dir = args.data_dir if args.data_dir.is_absolute() else Path.cwd() / args.data_dir
    cache_mode = "readwrite" if args.live else "readonly"
    print(f"eval: {', '.join(w for w, _, _ in plan)} | labels {rel_repo(args.labels)} | cache {cache_mode} | "
          f"rooms {project.rooms} ({project.source}) | data {data_dir}")
    try:
        with eval_environment(data_dir, live=args.live, orig_cache_dir=settings.cache_dir,
                              orig_ledger=settings.ledger_path):
            ensure_project(project)
            walks = evaluate(by_walk, plan, holdout=set(args.holdout or []), media_root=media_root, force=args.force)
    finally:
        if tmp is not None:
            tmp.cleanup()

    run = EvalRun(labels_dir=rel_repo(args.labels), cache_mode=cache_mode, project=project, walks=walks)
    report = args.report if args.report.is_absolute() else Path.cwd() / args.report
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_markdown(run))
    report.with_suffix(".json").write_text(render_json(run))
    print()
    print(text_table(run.rows()))
    print(f"\nreport: {rel_repo(report)} (+ {report.with_suffix('.json').name})")
    if args.strict and any_failed([r for r in run.rows() if not r.holdout]):
        print("strict: a target failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
