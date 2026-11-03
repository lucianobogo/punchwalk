import httpx
import pytest
import respx

from punchwalk.clients import meta_files
from punchwalk.clients.base import ApiError
from tests.conftest import PROBE

URL = "https://api.meta.ai/v1/files"


@respx.mock
def test_upload_multipart_and_cache(readwrite, no_fixture_cache, tmp_path):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={"id": "file-9", "status": "uploaded"}))
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * 100)
    assert meta_files.upload(f) == "file-9"
    body = route.calls[0].request.content
    assert body.index(b'name="purpose"') < body.index(b'name="file"')  # purpose before file
    assert b"user_data" in body and b"video/mp4" in body
    assert meta_files.upload(f) == "file-9" and route.call_count == 1  # same bytes: cached
    assert meta_files.sha_for_file_id("file-9") is not None
    assert meta_files.upload(f, force=True) == "file-9" and route.call_count == 2


@respx.mock
def test_auth_error_is_readable(readwrite, no_fixture_cache):
    respx.post(URL).mock(return_value=httpx.Response(401, json={"error": "invalid key"}))
    with pytest.raises(ApiError) as e:
        meta_files.upload(b"abc", filename="x.txt")
    assert e.value.auth_failed and "HTTP 401" in str(e.value) and "test-meta-key" not in str(e.value)


def test_bad_file_id_detection():
    assert meta_files.looks_like_bad_file_id(ApiError("meta", "/responses", 404, "File file-1 not found"))
    assert not meta_files.looks_like_bad_file_id(ApiError("meta", "/responses", 400, "bad prompt"))


def test_readonly_replay_of_probe_upload():
    assert meta_files.upload(PROBE / "media" / "clip3s.mp4").startswith("file-")
