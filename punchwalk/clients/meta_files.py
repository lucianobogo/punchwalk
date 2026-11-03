"""Meta Files API: `POST /v1/files` (multipart, purpose=user_data) -> file id.

Uploads are synchronous (a 200 means ready). Results are cached by content sha256, so the
same bytes are uploaded once. `file_index()` maps file ids back to content hashes so SAM
cache keys depend on the video's bytes, not on a per-upload id.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from punchwalk.clients import calls
from punchwalk.clients.base import (
    PROVIDER_META,
    ApiError,
    http_client,
    meta_headers,
    raise_for_status,
    read_bytes,
    sha256_bytes,
    wrap_transport_errors,
)
from punchwalk.clients.cache import Cache, get_cache, make_key
from punchwalk.config import REPO_ROOT, get_settings

ENDPOINT = "/files"
INDEX_NAME = "files_index.json"

_MIME = {
    ".mp4": "video/mp4",
    ".mov": "video/mp4",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".json": "application/json",
    ".txt": "text/plain",
}


def _key(sha: str, purpose: str) -> str:
    return make_key(PROVIDER_META, ENDPOINT, "", {"purpose": purpose}, [sha])


def _index_paths(cache: Cache) -> list[Path]:
    return [cache.write_dir / INDEX_NAME, cache.fixture_dir / INDEX_NAME]


def file_index(cache: Cache | None = None) -> dict[str, dict]:
    cache = cache or get_cache()
    merged: dict[str, dict] = {}
    for p in reversed(_index_paths(cache)):
        if p.exists():
            try:
                merged.update(json.loads(p.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
    return merged


def _remember(cache: Cache, file_id: str, info: dict) -> None:
    if cache.mode != "readwrite":
        return
    p = cache.write_dir / INDEX_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    data[file_id] = info
    p.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")


def _portable_path(p: Path) -> str:
    """Repo-relative when inside the repo (so committed fixture indexes have no local paths)."""
    try:
        return str(p.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(p.resolve())


def local_path(info: dict) -> Path | None:
    raw = info.get("path")
    if not raw:
        return None
    p = Path(raw)
    p = p if p.is_absolute() else REPO_ROOT / p
    return p if p.exists() else None


def sha_for_file_id(file_id: str) -> str | None:
    info = file_index().get(file_id)
    return info.get("sha256") if info else None


def upload(
    source: bytes | str | Path,
    purpose: str = "user_data",
    *,
    filename: str | None = None,
    force: bool = False,
) -> str:
    """Upload bytes or a file path; returns the file id. `force=True` skips the cache
    (used when the API rejects an old id)."""
    data = read_bytes(source)
    sha = sha256_bytes(data)
    name = filename or (Path(source).name if not isinstance(source, bytes) else f"{sha[:12]}.bin")
    cache = get_cache()
    key = _key(sha, purpose)
    desc = f"Files upload {name} ({len(data)} bytes, sha256 {sha[:12]})"

    if not force:
        hit = cache.get(key)
        if hit is not None:
            calls.emit(
                calls.CallRecord(
                    provider=PROVIDER_META, model="", endpoint=ENDPOINT, units=len(data),
                    unit_kind="bytes", cached=True, cache_key=key,
                )
            )
            return hit.response["id"]
    cache.require_live(key, desc)

    s = get_settings()
    mime = _MIME.get(Path(name).suffix.lower(), "application/octet-stream")
    t0 = time.monotonic()
    record = calls.CallRecord(
        provider=PROVIDER_META, model="", endpoint=ENDPOINT, units=len(data), unit_kind="bytes", cache_key=key
    )
    try:
        with wrap_transport_errors(PROVIDER_META, ENDPOINT), http_client() as client:
            # `purpose` goes before `file` (lower upload latency per the docs).
            resp = client.post(
                f"{s.META_API_BASE}{ENDPOINT}",
                headers=meta_headers(s),
                data={"purpose": purpose},
                files={"file": (name, data, mime)},
            )
            raise_for_status(resp, PROVIDER_META, ENDPOINT)
            body = resp.json()
    except ApiError as err:
        record.ok, record.error = False, str(err)[:500]
        record.latency_ms = int((time.monotonic() - t0) * 1000)
        calls.emit(record)
        raise
    record.latency_ms = int((time.monotonic() - t0) * 1000)
    calls.emit(record)
    file_id = body.get("id")
    if not file_id:
        raise ApiError(PROVIDER_META, ENDPOINT, 200, f"upload response has no id: {json.dumps(body)[:300]}")
    cache.put(
        key, provider=PROVIDER_META, endpoint=ENDPOINT, model="", description=desc, response=body,
        meta={"sha256": sha, "filename": name},
    )
    _remember(
        cache,
        file_id,
        {
            "sha256": sha,
            "filename": name,
            "bytes": len(data),
            "path": None if isinstance(source, bytes) else _portable_path(Path(source)),
            "uploaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        },
    )
    return file_id


def looks_like_bad_file_id(err: ApiError) -> bool:
    """True when an API error says a file id is unknown or expired (re-upload once)."""
    text = err.body.lower()
    return err.status in (400, 404, 410) and "file" in text and any(
        w in text for w in ("not found", "expired", "invalid", "unknown", "does not exist", "no such")
    )
