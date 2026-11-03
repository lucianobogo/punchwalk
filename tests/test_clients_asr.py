import io
import json
import wave

import httpx
import pytest
import respx

from punchwalk.clients import asr
from punchwalk.clients.base import ApiError
from tests.conftest import FIXTURES, PROBE

URL = "https://api.meta.ai/v1/asr/transcribe"
RECORDED = json.loads((FIXTURES / "clients" / "asr_response.json").read_text())


def make_wav(seconds: float, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


def test_parse_recorded_response():
    t = asr.parse_response(RECORDED)
    assert t.audio_duration_ms == 5040 and len(t.turns) == 1
    assert t.turns[0].turn_id == 1 and t.turns[0].start_ms == 0 and t.turns[0].end_ms == 3180
    assert "knob missing" in t.turns[0].text


def test_parse_offsets_and_drops_empty_turns():
    body = {"transcript": "a b", "audioDurationMs": 4000, "turns": [
        {"turnId": 0, "startMs": 100, "endMs": 900, "transcript": "a"},
        {"turnId": 1, "startMs": 1000, "endMs": 1100, "transcript": "  "},
        {"turnId": 2, "startMs": 2000, "endMs": 2500, "transcript": "b"}]}
    t = asr.parse_response(body, offset_ms=60_000, first_turn_id=5)
    assert [(x.turn_id, x.start_ms, x.text) for x in t.turns] == [(5, 60_100, "a"), (6, 62_000, "b")]


def test_split_wav_by_duration():
    chunks = asr.split_wav(make_wav(2.5), max_s=1)
    assert [o for o, _ in chunks] == [0, 1000, 2000]
    with wave.open(io.BytesIO(chunks[-1][1])) as w:
        assert w.getnframes() == 8000 and w.getframerate() == 16000
    assert len(asr.split_wav(make_wav(2.5))) == 1


@respx.mock
def test_transcribe_multipart_and_chunk_offsets(tmp_path, monkeypatch, no_fixture_cache):
    monkeypatch.setattr(asr, "MAX_CHUNK_S", 1)
    wav = tmp_path / "a.wav"
    wav.write_bytes(make_wav(2.2))
    monkeypatch.setenv("PUNCHWALK_CACHE", "off")
    from punchwalk.config import reset_settings
    reset_settings()
    one = {"transcript": "x", "audioDurationMs": 1000, "turns": [{"turnId": 0, "startMs": 200, "endMs": 800, "transcript": "x"}]}
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=one))
    t = asr.transcribe(wav, keywords=["escutcheon"])
    assert route.call_count == 3
    assert [x.start_ms for x in t.turns] == [200, 1200, 2200] and [x.turn_id for x in t.turns] == [1, 2, 3]
    body = route.calls[0].request.content.decode("latin-1")
    assert 'name="request"' in body and 'name="audio"' in body
    req_json = json.loads(body.split('name="request"')[1].split("\r\n\r\n", 1)[1].split("\r\n--")[0])
    assert req_json == {"mode": "ENDPOINTING", "model": "muse-voice-transcribe-1.0", "audioEncoding": "WAV",
                        "languageBias": ["English"], "keywords": ["escutcheon"]}


@respx.mock
def test_readonly_refuses_before_any_request(tmp_path, no_fixture_cache):
    wav = tmp_path / "a.wav"
    wav.write_bytes(make_wav(0.5))
    route = respx.post(URL).mock(side_effect=[httpx.Response(429), httpx.Response(429), httpx.Response(200, json=RECORDED)])
    from punchwalk.clients.cache import CacheMiss

    with pytest.raises(CacheMiss):  # readonly: a miss is refused before any request
        asr.transcribe(wav)
    assert route.call_count == 0


@respx.mock
def test_429_retries_and_500_retries_once(tmp_path, readwrite, no_fixture_cache):
    wav = tmp_path / "a.wav"
    wav.write_bytes(make_wav(0.5))
    route = respx.post(URL).mock(side_effect=[httpx.Response(429), httpx.Response(429), httpx.Response(200, json=RECORDED)])
    assert asr.transcribe(wav).turns[0].end_ms == 3180 and route.call_count == 3

    wav.write_bytes(make_wav(0.6))
    before = route.call_count
    route2 = respx.post(URL).mock(side_effect=[httpx.Response(500, text="budget"), httpx.Response(500, text="budget"),
                                               httpx.Response(200, json=RECORDED)])
    with pytest.raises(ApiError, match="HTTP 500"):
        asr.transcribe(wav)
    assert route2.call_count - before == 2  # respx reuses the route; count is cumulative


def test_readonly_replay_of_probe_audio():
    t = asr.transcribe(PROBE / "media" / "audio5s.wav", keywords=["cabinet", "knob", "sink", "kitchen"])
    assert "cabinet" in t.transcript
