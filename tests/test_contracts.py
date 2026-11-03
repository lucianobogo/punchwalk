"""Contract tests: mocks <-> Pydantic schemas <-> TypeScript types <-> SQLModel tables.

- Every JSON file in fixtures/mock_api/ maps to exactly one schema and round-trips through it unchanged.
- Every media URL in the mocks resolves to a file under fixtures/mock_api/media/ (and no media is orphaned).
- Cross-references between mocks (item ids, share tokens, walk ids) resolve.
- web/src/api/types.ts mirrors schemas.py (same interfaces, fields, types, nullability) and enums.py
  (same enum values and *_LABEL maps).
- Every SQLModel table round-trips through SQLite.

Offline and fast: no network, no ffmpeg needed (the video probe test skips without ffprobe).
"""

from __future__ import annotations

import inspect
import json
import re
import shutil
import subprocess
import types
import typing
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select

from punchwalk.api import schemas as S
from punchwalk.core import enums as E
from punchwalk.core import models as M

ROOT = Path(__file__).resolve().parents[1]
MOCK = ROOT / "fixtures" / "mock_api"
MEDIA = MOCK / "media"
TYPES_TS = ROOT / "web" / "src" / "api" / "types.ts"

# Mock file (path relative to fixtures/mock_api) -> schema. Mirrors the README naming convention.
MOCK_ROUTES: list[tuple[str, type[BaseModel]]] = [
    (r"healthz\.json", S.HealthOut),
    (r"projects\.json", S.ProjectListOut),
    (r"projects/[^/]+\.json", S.ProjectOut),
    (r"projects/[^/]+/items\.json", S.ItemListOut),
    (r"projects/[^/]+/walks\.json", S.WalkListOut),
    (r"projects/[^/]+/walks\.post\.json", S.UploadOut),
    (r"projects/[^/]+/trades\.json", S.TradeListOut),
    (r"projects/[^/]+/trades/[a-z_]+/preview\.json", S.DispatchPreviewOut),
    (r"projects/[^/]+/trades/[a-z_]+/dispatches\.json", S.DispatchListOut),
    (r"projects/[^/]+/trades/[a-z_]+/dispatch\.post\.json", S.DispatchOut),
    (r"walks\.json", S.WalkListOut),
    (r"walks/[^/]+\.json", S.WalkDetailOut),
    (r"walks/[^/]+/progress\.json", S.JobProgressOut),
    (r"walks/[^/]+/progress/\d+\.json", S.JobProgressOut),
    (r"walks/[^/]+/overlay\.json", S.OverlayOut),
    (r"walks/[^/]+/model-calls\.json", S.ModelCallListOut),
    (r"walks/[^/]+/processing\.json", S.ProcessingSummaryOut),
    (r"walks/[^/]+/rewalk-summary\.json", S.RewalkSummaryOut),
    (r"items/[^/]+\.json", S.ItemOut),
    (r"items/[^/]+/share\.post\.json", S.ShareLinkOut),
    (r"share/[^/]+\.json", S.ShareItemOut),
    (r"requests/projects\.post\.json", S.ProjectCreate),
    (r"requests/projects\.patch\.json", S.ProjectPatch),
    (r"requests/items\.patch\.[a-z_]+\.json", S.ItemPatch),
    (r"requests/dispatch\.post\.json", S.DispatchCreate),
    (r"requests/rewalk-link\.post\.json", S.RewalkLinkIn),
]

PRJ, W1, W2 = "prj_unit2b", "wlk_2b_first", "wlk_2b_rewalk"


def mock_files() -> list[Path]:
    return sorted(MOCK.rglob("*.json"))


def rel(p: Path) -> str:
    return p.relative_to(MOCK).as_posix()


def schema_for(relpath: str) -> type[BaseModel]:
    hits = [schema for pattern, schema in MOCK_ROUTES if re.fullmatch(pattern, relpath)]
    assert len(hits) == 1, f"{relpath}: matches {len(hits)} schemas in MOCK_ROUTES (need exactly 1)"
    return hits[0]


def load(relpath: str) -> Any:
    return json.loads((MOCK / relpath).read_text())


def parsed(relpath: str) -> Any:
    return schema_for(relpath).model_validate(load(relpath))


# =========================================================================== mocks <-> schemas


def test_mock_dir_is_populated() -> None:
    assert (MOCK / "README.md").exists(), "fixtures/mock_api/README.md documents the naming convention"
    assert len(mock_files()) > 50, "run `uv run python scripts/gen_mock_api.py`"


@pytest.mark.parametrize("path", mock_files(), ids=rel)
def test_mock_round_trips(path: Path) -> None:
    r = rel(path)
    schema = schema_for(r)
    data = json.loads(path.read_text())
    obj = schema.model_validate(data)  # also validates every enum value
    is_request = r.startswith("requests/")
    dumped = obj.model_dump(mode="json", by_alias=True, exclude_unset=is_request)
    assert dumped == data, f"{r} changes when round-tripped through {schema.__name__}"
    assert schema.model_validate(dumped) == obj


def _strings(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in _strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    return []


def test_media_urls_resolve_and_nothing_is_orphaned() -> None:
    referenced: set[str] = set()
    for p in mock_files():
        for s in _strings(json.loads(p.read_text())):
            if "/media/" in s:
                assert s.startswith("/media/"), f"{rel(p)}: media URLs must be root-relative: {s}"
                target = MEDIA / s.removeprefix("/media/")
                assert target.is_file(), f"{rel(p)} references missing media {s}"
                referenced.add(target.relative_to(MEDIA).as_posix())
    on_disk = {
        f.relative_to(MEDIA).as_posix() for f in MEDIA.rglob("*") if f.is_file() and f.name != ".DS_Store"
    }
    assert on_disk - referenced == set(), f"unreferenced media files: {sorted(on_disk - referenced)}"


def test_media_formats() -> None:
    from PIL import Image

    for f in MEDIA.rglob("*.jpg"):
        with Image.open(f) as im:
            assert im.format == "JPEG", f
            w, h = im.size
            if f.name in ("crop.jpg", "after.jpg"):
                assert (w, h) == (800, 600), f"{f}: crops are 4:3 800x600"
            if f.name in ("evidence.jpg", "frame.jpg", "after_frame.jpg"):
                assert (w, h) in ((1080, 1920), (1920, 1080)), f"{f}: full frames are the walk's pixel size"
    for f in MEDIA.rglob("*.mp4"):
        assert f.stat().st_size < 2_000_000, f"{f} is over 2 MB"


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe not installed")
def test_display_videos_match_walks() -> None:
    for wid in (W1, W2, "wlk_3a_first"):
        walk = parsed(f"walks/{wid}.json").walk
        url = walk.media.display_mp4
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=codec_name,pix_fmt,width,height:format=duration",
                "-of",
                "json",
                str(MEDIA / url.removeprefix("/media/")),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        info = json.loads(out.stdout)
        video = next(s for s in info["streams"] if s["codec_name"] == "h264")
        assert video["pix_fmt"] == "yuv420p"
        assert any(s["codec_name"] == "aac" for s in info["streams"])
        assert abs(float(info["format"]["duration"]) - walk.duration_s) < 0.5
        # same aspect as the walk, so overlay coordinates (walk pixels) scale onto the video
        assert abs(video["width"] / video["height"] - walk.width / walk.height) < 0.01


# =========================================================================== scenario coverage


def test_scenario_has_what_every_screen_needs() -> None:
    projects = parsed("projects.json").projects
    assert {p.name for p in projects} >= {"Unit 2B final punch"}
    lst: S.ItemListOut = parsed(f"projects/{PRJ}/items.json")
    items = lst.items
    assert len(items) >= 11
    by_status = {s: [i for i in items if i.status == s] for s in E.ItemStatus}
    assert by_status[E.ItemStatus.closed], "need signed-off items"
    for it in by_status[E.ItemStatus.closed]:
        assert it.closure and it.closure.before_image_url and it.closure.after_image_url
    reasons = {i.review_reason for i in by_status[E.ItemStatus.needs_review]}
    assert {E.REVIEW_NO_DESCRIPTION, E.REVIEW_FALSE_FIX} <= reasons
    false_fix = next(i for i in items if i.review_reason == E.REVIEW_FALSE_FIX)
    assert false_fix.closure and false_fix.closure.voice_status == E.VoiceStatus.fixed
    assert false_fix.closure.vision_status == E.VisionStatus.still_visible
    assert by_status[E.ItemStatus.dispatched] and by_status[E.ItemStatus.open]
    assert any(i.severity == E.Severity.safety for i in items)
    voice_only = [i for i in items if i.mark_id is None]
    assert voice_only and all(
        i.evidence and i.evidence.grounding == E.Grounding.tape_only for i in voice_only
    )
    assert any(i.dispatch and i.history for i in by_status[E.ItemStatus.dispatched])
    assert any(h.actor == E.HistoryActor.human for i in items for h in i.history), "need a human override"
    assert {g for i in items if i.evidence for g in [i.evidence.grounding]} == set(E.Grounding)
    assert any(i.verification and i.verification.provider == E.Provider.meta_fallback for i in items)
    # counts and facets agree with the list
    assert lst.counts.all == len(items) == lst.total
    assert lst.counts.todo == lst.counts.open + lst.counts.needs_review
    assert sum(lst.facets.trade.values()) == len(items)

    walks = parsed("walks.json").walks
    assert {w.status for w in walks} >= {E.WalkStatus.processing, E.WalkStatus.ready}
    w1: S.WalkDetailOut = parsed(f"walks/{W1}.json")
    w2: S.WalkDetailOut = parsed(f"walks/{W2}.json")
    assert w1.walk.kind == E.WalkKind.initial and w2.walk.kind == E.WalkKind.rewalk
    assert w2.walk.rewalk_of == W1
    assert len(w1.walk.clips) == 2 and w1.walk.clips[1].offset_s == w1.walk.clips[0].duration_s
    assert w1.walk.orientation == E.Orientation.portrait and (w1.walk.width, w1.walk.height) == (1080, 1920)
    assert w1.turns and w1.marks
    assert any(t.kind == E.TurnKind.status_update and t.claimed for t in w2.turns)

    overlay: S.OverlayOut = parsed(f"walks/{W1}/overlay.json")
    assert overlay.fps == 10 and len({m.mark_id for f in overlay.frames for m in f.marks}) >= 3

    proc: S.ProcessingSummaryOut = parsed(f"walks/{W1}/processing.json")
    labels = {c.model_label for c in proc.calls}
    assert {"Muse Voice Transcribe", "SAM 3.1", "Muse Spark Open on Baseten"} <= labels
    assert any(c.provider == E.Provider.meta_fallback and c.ok for c in proc.calls)
    assert proc.total_cost_usd == pytest.approx(sum(c.est_cost_usd for c in proc.calls), abs=1e-3)

    summary: S.RewalkSummaryOut = parsed(f"walks/{W2}/rewalk-summary.json")
    assert summary.needs_a_look and summary.signed_off and summary.still_open
    assert summary.headline == "6 signed off. 3 still open. 1 needs a look."

    progress: S.JobProgressOut = parsed("walks/wlk_3a_first/progress.json")
    assert progress.status == E.JobStatus.running and progress.items_so_far
    replay = sorted((MOCK / "walks/wlk_3a_first/progress").glob("*.json"))
    states = [S.JobProgressOut.model_validate_json(p.read_text()).status for p in replay]
    assert states[0] == E.JobStatus.queued and states[-1] == E.JobStatus.done


def test_overlay_and_moments_are_consistent() -> None:
    for wid in (W1, W2):
        ov: S.OverlayOut = parsed(f"walks/{wid}/overlay.json")
        ts = [f.t for f in ov.frames]
        assert ts == sorted(ts) and len(ts) == len(set(ts))
        for f in ov.frames:
            assert abs(f.t * ov.fps - round(f.t * ov.fps)) < 1e-6, "frames sit on the fps grid"
            for m in f.marks:
                b = m.box
                assert 0 <= b.x1 < b.x2 <= ov.width and 0 <= b.y1 < b.y2 <= ov.height
                assert m.svg.startswith("M") and m.svg.endswith("Z")
        for e in ov.events:
            assert 0 <= e.t <= ov.duration_s
    for p in sorted((MOCK / "items").glob("*.json")):
        it = S.ItemOut.model_validate_json(p.read_text())
        assert 0 <= it.video_start_s < it.video_end_s
        if it.quote_start_s is not None:
            assert it.video_start_s <= it.quote_start_s <= it.video_end_s


def test_cross_references_resolve() -> None:
    item_files = {
        p.stem: S.ItemOut.model_validate_json(p.read_text()) for p in (MOCK / "items").glob("*.json")
    }
    walk_ids = {p.stem for p in (MOCK / "walks").glob("*.json")}
    share_tokens = {p.stem for p in (MOCK / "share").glob("*.json")}

    for pid in ("prj_unit2b", "prj_unit3a"):
        lst: S.ItemListOut = parsed(f"projects/{pid}/items.json")
        for it in lst.items:
            assert item_files[it.id] == it, f"{it.id}: list copy differs from items/{it.id}.json"
            assert it.origin_walk_id in walk_ids
        for pv in (MOCK / f"projects/{pid}/trades").glob("*/preview.json"):
            preview = S.DispatchPreviewOut.model_validate_json(pv.read_text())
            for line in preview.lines:
                assert line.item_id in item_files
                assert line.url.removeprefix("/share/item/") in share_tokens

    for wid in walk_ids:
        detail: S.WalkDetailOut = parsed(f"walks/{wid}.json")
        turn_ids = {t.id for t in detail.turns}
        for it in detail.items:
            assert item_files[it.id] == it
            assert set(it.turn_ids) <= turn_ids or it.origin_walk_id != wid
        for t in detail.turns:
            assert set(t.item_ids) <= set(item_files)
        for m in detail.marks:
            assert m.item_id in item_files
        for c in parsed(f"walks/{wid}/model-calls.json").calls:
            assert c.item_id is None or c.item_id in item_files
        for p in [
            MOCK / f"walks/{wid}/progress.json",
            *sorted((MOCK / f"walks/{wid}/progress").glob("*.json")),
        ]:
            for thumb in S.JobProgressOut.model_validate_json(p.read_text()).items_so_far:
                assert thumb.id in item_files

    summary: S.RewalkSummaryOut = parsed(f"walks/{W2}/rewalk-summary.json")
    for group in (summary.needs_a_look, summary.signed_off, summary.still_open, summary.not_checked):
        for row in group:
            assert item_files[row.item_id].closure is not None
    for p in (MOCK / "projects").glob("*/trades/*/dispatches.json"):
        for d in S.DispatchListOut.model_validate_json(p.read_text()).dispatches:
            assert set(d.item_ids) <= set(item_files)
    for it in item_files.values():
        if it.dispatch:
            assert it.dispatch.dispatch_id.startswith("dsp_")


# =========================================================================== labels


def test_label_maps_cover_their_enums() -> None:
    assert set(E.STATUS_LABEL) == set(E.ItemStatus)
    assert set(E.SEVERITY_LABEL) == set(E.Severity)
    assert set(E.TRADE_LABEL) == set(E.Trade)
    assert set(E.STAGE_LABEL) == set(E.Stage) == set(E.STAGE_TITLE)
    assert set(E.CHANNEL_LABEL) == set(E.Channel)
    assert set(E.WALK_KIND_LABEL) == set(E.WalkKind)
    assert set(E.WALK_STATUS_LABEL) == set(E.WalkStatus)
    assert set(E.PROVIDER_LABEL) == set(E.Provider)
    assert E.STATUS_LABEL[E.ItemStatus.dispatched] == "Open"
    assert E.TRADE_LABEL[E.Trade.carpentry_millwork] == "Carpentry & millwork"
    assert E.format_item_number(14) == "#014"


# =========================================================================== TypeScript mirror


def _ts() -> str:
    return TYPES_TS.read_text()


def _ts_interfaces() -> dict[str, dict[str, tuple[bool, str]]]:
    """interface name -> {field: (optional, type)}"""
    out: dict[str, dict[str, tuple[bool, str]]] = {}
    for name, body in re.findall(r"export interface (\w+) \{\n(.*?)\n\}", _ts(), re.S):
        fields = {}
        for line in body.splitlines():
            m = re.match(r"\s+(\w+)(\?)?: (.+);$", line)
            if m:
                fields[m.group(1)] = (bool(m.group(2)), " ".join(m.group(3).split()))
        out[name] = fields
    return out


def _ts_unions() -> dict[str, list[str]]:
    return {
        name: re.findall(r"'([^']*)'", body)
        for name, body in re.findall(r"export type (\w+) =([^;]+);", _ts())
    }


def _ts_label_maps() -> dict[str, dict[str, str]]:
    out = {}
    for name, body in re.findall(r"export const (\w+_LABEL): Record<[^=]+> = \{(.*?)\};", _ts(), re.S):
        out[name] = dict(re.findall(r"^\s*'?([\w./-]+)'?: '([^']*)',?$", body, re.M))
    return out


def _schema_classes() -> list[type[S.Schema]]:
    return [
        c
        for _, c in inspect.getmembers(S, inspect.isclass)
        if issubclass(c, S.Schema) and c is not S.Schema and c.__module__ == S.__name__
    ]


def _enum_classes() -> list[type[Enum]]:
    return [
        c
        for _, c in inspect.getmembers(E, inspect.isclass)
        if issubclass(c, Enum) and c.__module__ == E.__name__
    ]


def _ts_type(ann: Any) -> str:
    """The TypeScript type a Python annotation must be mirrored as."""
    if ann is type(None):
        return "null"
    if ann is Any:
        return "unknown"
    if ann in (str, datetime):
        return "string"
    if ann in (int, float):
        return "number"
    if ann is bool:
        return "boolean"
    if inspect.isclass(ann) and issubclass(ann, (Enum, BaseModel)):
        return ann.__name__
    origin, args = typing.get_origin(ann), typing.get_args(ann)
    if origin in (typing.Union, types.UnionType):
        parts = [_ts_type(a) for a in args if a is not type(None)]
        return " | ".join(parts + (["null"] if type(None) in args else []))
    if origin is list:
        inner = _ts_type(args[0])
        return f"({inner})[]" if "|" in inner else f"{inner}[]"
    if origin is dict:
        key, val = args
        if inspect.isclass(key) and issubclass(key, Enum):
            return f"Partial<Record<{key.__name__}, {_ts_type(val)}>>"
        return f"Record<string, {_ts_type(val)}>"
    raise AssertionError(f"no TypeScript mapping for {ann!r}")


def test_types_ts_header() -> None:
    assert _ts().startswith("// Mirrors punchwalk/api/schemas.py. Change both or neither.")


def test_types_ts_mirrors_every_schema() -> None:
    ts = _ts_interfaces()
    problems = []
    for cls in _schema_classes():
        if cls.__name__ not in ts:
            problems.append(f"missing interface {cls.__name__}")
            continue
        fields = ts[cls.__name__]
        py = {(f.alias or n): f for n, f in cls.model_fields.items()}
        if set(py) != set(fields):
            problems.append(
                f"{cls.__name__}: fields differ: py-only {sorted(set(py) - set(fields))}, "
                f"ts-only {sorted(set(fields) - set(py))}"
            )
            continue
        for name, f in py.items():
            optional, ts_type = fields[name]
            want = _ts_type(f.annotation)
            if ts_type != want:
                problems.append(f"{cls.__name__}.{name}: ts `{ts_type}` but python implies `{want}`")
            if optional != (not f.is_required()):
                problems.append(
                    f"{cls.__name__}.{name}: ts optional={optional}, python required={f.is_required()}"
                )
    extra = set(ts) - {c.__name__ for c in _schema_classes()}
    if extra:
        problems.append(f"interfaces with no schema: {sorted(extra)}")
    assert not problems, "\n".join(problems)


def test_types_ts_mirrors_every_enum_and_label() -> None:
    unions = _ts_unions()
    for cls in _enum_classes():
        assert cls.__name__ in unions, f"types.ts is missing `export type {cls.__name__}`"
        assert unions[cls.__name__] == [m.value for m in cls], f"{cls.__name__} values differ (order too)"
    ts_maps = _ts_label_maps()
    py_maps = {n: v for n, v in vars(E).items() if n.endswith("_LABEL") and isinstance(v, dict)}
    assert set(ts_maps) == set(py_maps)
    for name, mapping in py_maps.items():
        assert ts_maps[name] == {str(k): v for k, v in mapping.items()}, f"{name} differs"
    for const in (
        "MODEL_ASR",
        "MODEL_SAM",
        "MODEL_SPARK_OPEN",
        "MODEL_SPARK_HOSTED",
        "REVIEW_NO_DESCRIPTION",
        "REVIEW_FALSE_FIX",
        "NOT_CHECKED_ON_REWALK",
    ):
        m = re.search(rf"export const {const} = '([^']*)';", _ts())
        assert m and m.group(1) == getattr(E, const), f"{const} differs"


# =========================================================================== SQLite round trip


def test_sqlite_round_trip(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'punchwalk.db'}")
    SQLModel.metadata.create_all(engine)
    assert set(SQLModel.metadata.tables) >= {t.__tablename__ for t in M.ALL_TABLES}
    at = datetime(2026, 11, 3, 15, 12, tzinfo=UTC)

    project = M.Project(
        id="prj_t",
        name="Unit 2B final punch",
        address="418 Alder Street",
        trades=[
            M.TradeContact(
                trade=E.Trade.electrical, company="Ace Electric", email="maria.chen@example.com"
            ).model_dump(mode="json")
        ],
        keywords=["strike plate"],
        rooms=["Kitchen", "Bathroom"],
    )
    walk = M.Walk(
        id="wlk_t",
        project_id="prj_t",
        kind=E.WalkKind.initial,
        status=E.WalkStatus.ready,
        clips=[M.ClipInfo(order=0, filename="walk1_a.mp4", duration_s=82.4, offset_s=0).model_dump()],
        stages={
            "transcribe": M.StageState(
                stage=E.Stage.transcribe, status=E.StageStatus.done, counts={"lines": 20}
            ).model_dump(mode="json")
        },
        media=M.WalkMediaPaths(display_mp4="walks/wlk_t/display.mp4").model_dump(),
        duration_s=153.5,
        fps=30.0,
        width=1080,
        height=1920,
        orientation=E.Orientation.portrait,
        device=E.Device.glasses,
        recorded_at=at,
    )
    turn = M.Turn(
        id="trn_t",
        walk_id="wlk_t",
        idx=0,
        start_ms=14000,
        end_ms=17200,
        text="Knob's missing.",
        kind=E.TurnKind.defect,
        room="Kitchen",
        defects=["missing knob"],
    )
    mark = M.Mark(
        id="mrk_t",
        walk_id="wlk_t",
        sam_object_ids=["a:1"],
        intervals=[[9.3, 18.4]],
        best_t=14.6,
        box_at_best=M.Box(x1=1, y1=2, x2=3, y2=4).model_dump(),
        mean_conf=0.81,
    )
    evidence = M.Evidence(
        t=14.6,
        image="items/itm_t/evidence.jpg",
        raw="items/itm_t/frame.jpg",
        crop="items/itm_t/crop.jpg",
        width=1080,
        height=1920,
        tape_svg="M0 0 L1 1 Z",
        grounding=E.Grounding.sam_concept,
        sam_concept="cabinet knob hole",
    )
    history = [
        M.HistoryEvent(
            at=at,
            actor=E.HistoryActor.human,
            action=E.HistoryAction.edited,
            field="trade",
            from_="carpentry_millwork",
            to="general",
        ).model_dump(mode="json", by_alias=True)
    ]
    item = M.Item(
        id="itm_t",
        project_id="prj_t",
        number=14,
        origin_walk_id="wlk_t",
        mark_id="mrk_t",
        turn_ids=["trn_t"],
        title="Missing cabinet knob",
        trade=E.Trade.carpentry_millwork,
        severity=E.Severity.cosmetic,
        status=E.ItemStatus.needs_review,
        review_reason=E.REVIEW_FALSE_FIX,
        video_start_s=11.0,
        video_end_s=18.2,
        evidence=evidence.model_dump(mode="json"),
        verification=M.Verification(consistent=True, provider=E.Provider.meta_fallback).model_dump(
            mode="json"
        ),
        closure=M.Closure(rewalk_id="wlk_t", decision=E.ClosureDecision.needs_review).model_dump(mode="json"),
        history=history,
        share_token="shr_t",
    )
    dispatch = M.Dispatch(
        id="dsp_t",
        project_id="prj_t",
        trade=E.Trade.electrical,
        item_ids=["itm_t"],
        channel=E.Channel.slack,
        destination="#punch-electrical",
        payload={"text": "hi"},
    )
    call = M.ModelCall(
        id="mc_t",
        walk_id="wlk_t",
        stage=E.Stage.verify,
        model=E.MODEL_SPARK_HOSTED,
        provider=E.Provider.meta_fallback,
        latency_ms=3900,
        tokens_in=4250,
        tokens_out=640,
        est_cost_usd=0.0027,
    )
    job = M.Job(
        id="job_t",
        walk_id="wlk_t",
        status=E.JobStatus.running,
        attempts=1,
        progress={"stage": "write_items", "counts": {"written": 7, "total": 12}},
    )

    with Session(engine) as s:
        s.add_all([project, walk, turn, mark, item, dispatch, call, job])
        s.commit()

    with Session(engine) as s:
        got_item = s.exec(select(M.Item)).one()
        assert got_item.number == 14 and got_item.status == E.ItemStatus.needs_review
        assert M.Evidence.model_validate(got_item.evidence) == evidence
        assert M.HistoryEvent.model_validate(got_item.history[0]).from_ == "carpentry_millwork"
        assert got_item.history[0]["from"] == "carpentry_millwork"
        assert M.Verification.model_validate(got_item.verification).provider == E.Provider.meta_fallback
        got_walk = s.get(M.Walk, "wlk_t")
        assert (
            got_walk and got_walk.clips[0]["offset_s"] == 0 and got_walk.orientation == E.Orientation.portrait
        )
        assert M.StageState.model_validate(got_walk.stages["transcribe"]).counts == {"lines": 20}
        assert s.get(M.Mark, "mrk_t").intervals == [[9.3, 18.4]]  # type: ignore[union-attr]
        assert s.get(M.Turn, "trn_t").kind == E.TurnKind.defect  # type: ignore[union-attr]
        assert s.get(M.Dispatch, "dsp_t").payload == {"text": "hi"}  # type: ignore[union-attr]
        assert s.get(M.Job, "job_t").progress["counts"]["written"] == 7  # type: ignore[union-attr]
        got_project = s.get(M.Project, "prj_t")
        assert got_project and M.TradeContact.model_validate(got_project.trades[0]).company == "Ace Electric"
        assert got_project.created_at is not None
        # enums are stored by value, so raw SQL and other tools read "meta-fallback"
        raw = s.exec(text("SELECT provider, stage FROM model_calls")).one()  # type: ignore[call-overload]
        assert tuple(raw) == ("meta-fallback", "verify")

    with Session(engine) as s:  # project-scoped numbers are unique
        s.add(M.Item(project_id="prj_t", number=14, origin_walk_id="wlk_t", title="dupe"))
        with pytest.raises(Exception, match="UNIQUE"):
            s.commit()
