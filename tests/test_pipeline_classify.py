"""classify_turns (PLAN 1.4) + transcribe vocabulary (PLAN 1.1) + track_hands (PLAN 1.2)."""

from __future__ import annotations

import json
import shutil

import pytest

from punchwalk.clients import calls, llm
from punchwalk.clients.base import ApiError
from punchwalk.config import reset_settings
from punchwalk.core.enums import WalkKind
from punchwalk.pipeline import classify
from punchwalk.pipeline import params as P
from punchwalk.pipeline.classify import (
    TurnLabel,
    TurnLabels,
    carry_rooms,
    fallback_labels,
    label_turns,
    normalize_room,
)
from punchwalk.pipeline.ingest import IngestMeta
from punchwalk.pipeline.track import part_label, track_walk
from punchwalk.vocab import BASE_KEYWORDS, keywords_for
from tests.conftest import FIXTURES

SYNTH = FIXTURES / "pipeline" / "synth_walk"


def _turns(texts):
    return [{"id": f"trn_{i}", "idx": i, "start_ms": i * 3000, "end_ms": i * 3000 + 2000, "text": t}
            for i, t in enumerate(texts)]


# --------------------------------------------------------------------------- vocabulary


def test_vocabulary_base_and_project_keywords():
    assert 70 <= len(BASE_KEYWORDS) <= 110
    for term in ("cover plate", "strike plate", "escutcheon", "caulk", "baseboard", "casing", "GWB",
                 "touch-up", "reveal", "kitchen", "Unit 2B"):
        assert term in BASE_KEYWORDS
    kw = keywords_for(["Oakline Millwork", "CAULK", "  Unit  2B "])
    base = keywords_for()
    assert kw[: len(base)] == base and base[0] == BASE_KEYWORDS[0]  # base order first
    assert kw.count("caulk") == 1 and "CAULK" not in kw and kw[-1] == "Oakline Millwork"
    assert keywords_for() == keywords_for([])


# --------------------------------------------------------------------------- rooms


def test_normalize_room_to_project_rooms():
    rooms = ["Kitchen", "Primary bathroom", "Hallway"]
    assert normalize_room("kitchen", rooms) == "Kitchen"
    assert normalize_room("the hallway", rooms) == "Hallway"
    assert normalize_room("bathroom", rooms) == "Primary bathroom"
    assert normalize_room("laundry room", rooms) == "Laundry room"
    assert normalize_room("  ", rooms) is None and normalize_room(None, rooms) is None


def test_room_state_machine_carries_last_room_forward():
    labels = [{"idx": 0, "room": "Kitchen"}, {"idx": 1, "room": None}, {"idx": 2, "room": "Hallway"},
              {"idx": 3, "room": None}]
    carry_rooms(labels)
    assert [x["room"] for x in labels] == ["Kitchen", "Kitchen", "Hallway", "Hallway"]
    assert [x["room_mentioned"] for x in labels] == [True, False, True, False]


# --------------------------------------------------------------------------- model call


def test_label_turns_chunks_and_fills_missing(monkeypatch):
    monkeypatch.setattr(P, "CLASSIFY_CHUNK_TURNS", 2)
    turns = _turns(["Kitchen.", "Cabinet knob missing.", "okay", "Hallway switch plate crooked."])
    seen = []

    def fake_structured(model, messages, images=(), reasoning_effort="low", max_tokens=0, stage=""):
        assert model is TurnLabels and reasoning_effort == "low" and stage == "classify_turns"
        user = messages[-1]["content"]
        chunk = json.loads(user.split("Turns:\n", 1)[1])
        seen.append(([t["idx"] for t in chunk], "Room in effect before these turns: Kitchen." in user))
        out = [TurnLabel(idx=t["idx"], kind="defect" if "missing" in t["text"] else "location",
                         room="kitchen" if t["idx"] == 0 else None, continues_previous=False,
                         defects=[t["text"]] if "missing" in t["text"] else [], status_target=None, claimed=None)
               for t in chunk if t["idx"] != 3]  # the model forgets turn 3
        return TurnLabels(turns=out, rationale="ok"), calls.CallRecord(provider="baseten", model="m", endpoint="/x")

    monkeypatch.setattr(llm, "structured", fake_structured)
    labels, info = label_turns(turns, ["Kitchen", "Hallway"], WalkKind.initial)
    assert seen == [([0, 1], False), ([2, 3], True)]
    assert info["method"] == "model" and info["chunks"] == 2 and info["missing"] == 1
    assert [x["kind"] for x in labels] == ["location", "defect", "location", "defect"]  # turn 3 by rules


def test_model_failure_falls_back_to_rules(monkeypatch):
    def boom(*a, **k):
        raise ApiError("baseten", "/chat/completions", 503, "down")

    monkeypatch.setattr(llm, "structured", boom)
    labels, info = label_turns(_turns(["Kitchen.", "knob missing", "kitchen knob, fixed"]), [], WalkKind.rewalk)
    assert info["method"] == "rules"
    assert [x["kind"] for x in labels] == ["location", "defect", "status_update"]
    assert labels[2]["claimed"] == "fixed"


def test_fallback_rules_not_fixed_on_rewalk():
    (lab,) = fallback_labels(_turns(["bedroom scuffs, still there"]), ["Bedroom"], WalkKind.rewalk)
    assert lab["kind"] == "status_update" and lab["claimed"] == "not_fixed" and lab["room"] == "Bedroom"


def test_schema_is_strict_and_flat():
    schema = llm.strict_schema(TurnLabels)
    item = schema["properties"]["turns"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == set(TurnLabel.model_fields)
    assert item["properties"]["claimed"]["type"] == ["string", "null"]
    assert classify.SYSTEM.count("status_update") >= 2


# --------------------------------------------------------------------------- track_hands


def test_track_walk_per_part_offsets_and_namespaced_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("FIXTURE_CACHE_DIR", str(SYNTH / "cache"))
    reset_settings()
    wdir = tmp_path / "walk"
    shutil.copytree(SYNTH / "media", wdir)
    meta = IngestMeta.load(wdir)
    doc = track_walk(wdir, meta)
    assert doc["concept"] == "hand" and doc["sam"]["fps"] == 10
    assert [p["label"] for p in doc["parts"]] == ["a", "b", "c"]
    assert all(p["cached"] and p["complete"] for p in doc["parts"])
    by_part = {lab: [r for r in doc["records"] if r["object_id"].startswith(lab + ":")] for lab in "abc"}
    off_b = meta.clips[1].sam_frame_offset
    assert by_part["a"] and all(r["frame"] < off_b for r in by_part["a"])
    assert by_part["b"] and all(off_b <= r["frame"] < meta.clips[2].sam_frame_offset for r in by_part["b"])
    assert by_part["c"] == []  # part c has no hand
    r = by_part["a"][0]
    assert set(r) == {"frame", "object_id", "box", "confidence", "svg"} and r["svg"].startswith("M")
    assert (wdir / "stages" / "track_hands.a.txt").read_text().startswith("<")
    assert part_label(0) == "a" and part_label(27) == "p27"


def test_track_walk_readonly_miss_is_loud(tmp_path):
    wdir = tmp_path / "walk"
    shutil.copytree(SYNTH / "media", wdir)
    from punchwalk.clients.cache import CacheMiss

    with pytest.raises(CacheMiss):
        track_walk(wdir, IngestMeta.load(wdir), concept="some other concept")
