from pathlib import Path

import pytest

from punchwalk.clients.cache import Cache, CacheMiss, canonical_json, make_key


def test_key_is_stable_across_dict_order_and_volatile_fields():
    a = make_key("meta", "/responses", "sam-3.1", {"b": 1, "a": [1, {"y": 2, "x": 1}], "user": "u1"})
    b = make_key("meta", "/responses", "sam-3.1", {"a": [1, {"x": 1, "y": 2}], "b": 1, "user": "u2"})
    assert a == b
    assert len(a) == 64


def test_key_changes_with_request_model_and_uploads():
    base = make_key("meta", "/responses", "sam-3.1", {"t": "tape"}, ["aa"])
    assert base != make_key("meta", "/responses", "sam-3.1", {"t": "tape2"}, ["aa"])
    assert base != make_key("meta", "/responses", "sam-3.2", {"t": "tape"}, ["aa"])
    assert base != make_key("meta", "/responses", "sam-3.1", {"t": "tape"}, ["bb"])
    assert base != make_key("baseten", "/responses", "sam-3.1", {"t": "tape"}, ["aa"])
    # upload order does not matter
    assert make_key("m", "/e", "x", {}, ["a", "b"]) == make_key("m", "/e", "x", {}, ["b", "a"])


def test_canonical_json_strips_nested_volatile():
    assert canonical_json({"x": {"request_id": 5, "k": 1}}) == '{"x":{"k":1}}'


def test_readonly_miss_raises_with_description(tmp_path: Path):
    c = Cache(mode="readonly", write_dir=tmp_path / "w", fixture_dir=tmp_path / "f")
    assert c.get("ab" * 32) is None
    with pytest.raises(CacheMiss) as e:
        c.require_live("ab" * 32, "SAM concept 'tape' on image x.jpg")
    assert "SAM concept 'tape'" in str(e.value) and "ab" * 32 in str(e.value)


def test_readwrite_roundtrip_with_sidecar(tmp_path: Path):
    c = Cache(mode="readwrite", write_dir=tmp_path / "w", fixture_dir=tmp_path / "f")
    key = make_key("meta", "/responses", "sam-3.1", {"q": 1})
    c.put(key, provider="meta", endpoint="/responses", model="sam-3.1", description="d", response={"ok": 1},
          raw_text="<0f>\n", meta={"media_size": [720, 1280]})
    hit = c.get(key)
    assert hit.response == {"ok": 1} and hit.raw_text == "<0f>\n" and hit.meta["media_size"] == [720, 1280]


def test_readonly_and_off_never_write(tmp_path: Path):
    for mode in ("readonly", "off"):
        c = Cache(mode=mode, write_dir=tmp_path / mode, fixture_dir=tmp_path / "f")
        assert c.put("cd" * 32, provider="p", endpoint="e", model="m", description="d", response={}) is None
        assert not (tmp_path / mode).exists()


def test_off_mode_ignores_existing_entries(tmp_path: Path):
    rw = Cache(mode="readwrite", write_dir=tmp_path / "w", fixture_dir=tmp_path / "f")
    rw.put("ef" * 32, provider="p", endpoint="e", model="m", description="d", response={"a": 1})
    assert Cache(mode="off", write_dir=tmp_path / "w", fixture_dir=tmp_path / "f").get("ef" * 32) is None


def test_reads_fall_back_to_fixture_dir_and_promote(tmp_path: Path):
    fixture = Cache(mode="readwrite", write_dir=tmp_path / "f", fixture_dir=tmp_path / "f")
    fixture.put("12" * 32, provider="p", endpoint="e", model="m", description="fixture", response={"from": "f"})
    c = Cache(mode="readonly", write_dir=tmp_path / "w", fixture_dir=tmp_path / "f")
    assert c.get("12" * 32).response == {"from": "f"}

    rw = Cache(mode="readwrite", write_dir=tmp_path / "w", fixture_dir=tmp_path / "f")
    rw.put("34" * 32, provider="p", endpoint="e", model="m", description="x", response={"n": 1}, raw_text="raw")
    out = rw.promote(["34" * 32], dest=tmp_path / "dest")
    assert out[0].exists() and out[0].with_suffix(".txt").read_text() == "raw"
    with pytest.raises(CacheMiss):
        rw.promote(["56" * 32], dest=tmp_path / "dest")
