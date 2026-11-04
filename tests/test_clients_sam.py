import json

import httpx
import pytest
import respx

from punchwalk.clients import cost, sam
from tests.conftest import FIXTURES, PROBE

CLIENTS = FIXTURES / "clients"
FRAME = PROBE / "media" / "frame.jpg"
CLIP = PROBE / "media" / "clip3s.mp4"
URL = "https://api.meta.ai/v1/responses"


def test_parse_recorded_image_output():
    res = sam.parse_output((CLIENTS / "sam_image_tape.txt").read_text(), "image")
    assert list(res.frames) == [0]
    (r,) = res.frames[0]
    assert r.object_id == 0 and r.frame_index == 0
    # API x2/y2 are inclusive (495, 980); the parser makes them half-open
    assert r.box == (362.0, 923.0, 496.0, 981.0)
    assert r.confidence == pytest.approx(0.9532, abs=1e-3)
    assert r.mask_size == (134, 58)
    raster = r.raster()
    assert raster.shape == (58, 134) and 0.6 < raster.mean() < 1.0
    assert r.svg_path.startswith("M") and res.media_size == (720, 1280) and r.media_size == (720, 1280)
    assert res.diagnostics == []


def test_parse_recorded_video_output_frame_indices():
    res = sam.parse_output((CLIENTS / "sam_video_tape.txt").read_text(), "video")
    assert sorted(res.frames) == list(range(90))
    assert res.object_ids == [0]
    first, last = res.frames[0][0], res.frames[89][0]
    assert first.box[0] > last.box[0]  # the synthetic clip pans right, so the tape drifts left
    assert all(len(v) == 1 for v in res.frames.values())


def test_zero_matches_is_empty_not_error():
    res = sam.parse_output("", "video")
    assert res.frames == {} and res.diagnostics == [] and res.media_size is None


def test_frame_indices_read_from_markers_not_positions():
    lines = (CLIENTS / "sam_video_tape.txt").read_text().splitlines(keepends=True)
    shuffled = lines[40].replace("<40f>", "<77f>", 1) + lines[3]  # skip + out of order
    res = sam.parse_output(shuffled, "video")
    assert sorted(res.frames) == [3, 77]


def _sse_from_text(text: str, usage: dict | None = None) -> bytes:
    lane = {"item_id": "msg_0", "output_index": 0, "content_index": 0}
    evs = [{"type": "response.created", "response": {"status": "in_progress"}}]
    evs += [{"type": "response.output_text.delta", **lane, "delta": ln} for ln in text.splitlines(keepends=True)]
    evs += [{"type": "response.output_text.done", **lane, "text": text},
            {"type": "response.completed", "response": {"status": "completed", "usage": usage or {}}}]
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in evs).encode()


def test_parse_sse_fixture():
    out = sam.parse_sse((CLIENTS / "sam_video_5f.sse").read_text().splitlines())
    assert out["completed"] and out["usage"] == {"video_frames_processed": 5}
    assert out["text"].count("\n") == 5


@respx.mock
def test_concept_on_image_streams_caches_and_bills(readwrite, no_fixture_cache, ledger_rows):
    text = (CLIENTS / "sam_image_tape.txt").read_text()
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, content=_sse_from_text(text), headers={"content-type": "text/event-stream"})
    )
    res = sam.concept_on_image(FRAME, "strip of blue painter's tape")
    body = json.loads(route.calls[0].request.content)
    req = route.calls[0].request
    assert req.headers["accept"] == "text/event-stream" and req.headers["authorization"] == "Bearer test-meta-key"
    assert body["model"] == "sam-3.1" and body["stream"] is True
    assert body["metadata"] == {"include_confidence": "true", "mask_encoding": "one_bit"}
    content = body["input"][0]["content"]
    assert content[0] == {"type": "input_text", "text": "strip of blue painter's tape"}
    assert content[1]["type"] == "input_image" and content[1]["image_url"].startswith("data:image/jpeg;base64,")
    assert len(res.all_records()) == 1 and not res.cached
    assert ledger_rows()[-1]["est_cost_usd"] == pytest.approx(cost.SAM_IMAGE_PER_IMAGE)

    again = sam.concept_on_image(FRAME, "strip of blue painter's tape")
    assert again.cached and route.call_count == 1
    assert again.all_records()[0].box == res.all_records()[0].box


@respx.mock
def test_image_falls_back_to_file_upload_when_data_url_rejected(readwrite, no_fixture_cache):
    text = (CLIENTS / "sam_image_tape.txt").read_text()
    sse = httpx.Response(200, content=_sse_from_text(text), headers={"content-type": "text/event-stream"})
    route = respx.post(URL).mock(side_effect=[httpx.Response(400, json={"error": {"message": "input_image data URLs are not supported"}}), sse])
    respx.post("https://api.meta.ai/v1/files").mock(return_value=httpx.Response(200, json={"id": "file-123", "status": "uploaded"}))
    res = sam.concept_on_image(FRAME, "blue tape")
    assert sam.IMAGE_INPUT_MODE == "file_id"
    assert json.loads(route.calls[1].request.content)["input"][0]["content"][1] == {"type": "input_image", "file_id": "file-123"}
    assert len(res.all_records()) == 1


@respx.mock
def test_boxes_on_image_builds_object_prompt(readwrite, no_fixture_cache):
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, content=_sse_from_text(""), headers={"content-type": "text/event-stream"})
    )
    res = sam.boxes_on_image(FRAME, [(362, 923, 496, 981)])
    text = json.loads(route.calls[0].request.content)["input"][0]["content"][0]["text"]
    assert text == "<0f>0<|box;x1=362;y1=923;x2=495;y2=980;w=720;h=1280|>"
    assert res.frames == {}


@respx.mock
def test_stream_error_event_raises_api_error(readwrite, no_fixture_cache):
    body = b'data: {"type": "response.failed", "response": {"error": {"message": "boom"}}}\n\n'
    respx.post(URL).mock(return_value=httpx.Response(200, content=body, headers={"content-type": "text/event-stream"}))
    with pytest.raises(sam.ApiError, match="boom"):
        sam.concept_on_image(FRAME, "blue tape")


def test_concept_validation():
    with pytest.raises(ValueError):
        sam.concept_on_image(FRAME, "  ")
    with pytest.raises(ValueError):
        sam.concept_on_image(FRAME, "x" * 101)
    with pytest.raises(ValueError):
        sam.concept_on_image(FRAME, "<0f>0<|box;x1=1|>")


def test_readonly_replay_of_probe_image_and_video():
    """The probe's live calls replay from fixtures/cache with the network blocked."""
    img = sam.concept_on_image(FRAME, "strip of blue painter's tape")
    assert img.cached and img.all_records()[0].confidence > 0.9
    vid = sam.concept_on_video(CLIP, "strip of blue painter's tape")
    assert vid.cached and len(vid.frames) == 90 and vid.media_size == (720, 1280)
    assert vid.usage["video_frames_processed"] == 90


def test_readonly_miss_raises():
    from punchwalk.clients.cache import CacheMiss

    with pytest.raises(CacheMiss, match="never recorded"):
        sam.concept_on_image(FRAME, "never recorded")


# --------------------------------------------------------------------------- PLAN 10.9: decode each mask once


def _strict_parse(text: str):
    """The parser with the library's own strict accept check, rasters decoded strictly (the old path)."""
    import numpy as np
    from meta_sam_parser import _segmentation, decode_mask_to_raster

    patched = _segmentation.decode_mask_to_raster
    _segmentation.decode_mask_to_raster = decode_mask_to_raster
    try:
        res = sam.parse_output(text, "video", (720, 1280))
    finally:
        _segmentation.decode_mask_to_raster = patched
    for r in res.all_records():
        if r.mask is not None:
            r._raster = np.frombuffer(decode_mask_to_raster(r.mask), dtype=np.uint8).reshape(r.mask.height,
                                                                                            r.mask.width)
    return res


def test_masks_are_decoded_once_and_raster_identical_to_the_strict_path() -> None:
    """walk1's part-d hand track (32 masks): same records, diagnostics and mask bits as the strict path;
    the rasters come straight from the parser's accept check (no second decode)."""
    import numpy as np

    assert sam._PARSER_MODULE is not None, "meta_sam_parser internals moved: the decode-once patch is off"
    key = "51ace85ad407dccca692a81af9e422b7ee125b3dcbbda75383b6573a97e00715"
    text = (FIXTURES / "cache" / key[:2] / f"{key}.txt").read_text()
    old, new = _strict_parse(text), sam.parse_output(text, "video", (720, 1280))
    assert new.diagnostics == old.diagnostics
    ra, rb = old.all_records(), new.all_records()
    assert len(ra) == len(rb) == 32
    for a, b in zip(ra, rb, strict=True):
        assert (a.object_id, a.frame_index, a.box, a.confidence) == (b.object_id, b.frame_index, b.box, b.confidence)
        assert b._raster is not None  # filled by the parse itself
        assert np.array_equal(a._raster, b.raster())


def test_a_bad_mask_payload_is_still_diagnosed_like_the_strict_parser() -> None:
    key = "51ace85ad407dccca692a81af9e422b7ee125b3dcbbda75383b6573a97e00715"
    text = (FIXTURES / "cache" / key[:2] / f"{key}.txt").read_text()
    first = text.index("data=")
    start = text.index("!", first)
    broken = text[:start] + "~" + text[start + 1:]  # one_bit mask whose payload doesn't start with "!"
    old, new = _strict_parse(broken), sam.parse_output(broken, "video", (720, 1280))
    assert [d["code"] for d in new.diagnostics] == [d["code"] for d in old.diagnostics]
    assert len(new.all_records()) == len(old.all_records())
