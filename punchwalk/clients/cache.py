"""Response cache for every model call.

key = sha256(provider, endpoint, model, canonical request JSON minus volatile fields,
             sha256 of any uploaded bytes)

Entries are JSON files `<dir>/<key[:2]>/<key>.json`; raw SAM stream text goes in a
`<key>.txt` sidecar. Reads check PUNCHWALK_CACHE_DIR then FIXTURE_CACHE_DIR; writes go to
PUNCHWALK_CACHE_DIR. Modes (PUNCHWALK_CACHE):
- readwrite: read, and store live responses
- readonly:  read only; a miss raises CacheMiss (tests run this way)
- off:       never read or write
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from punchwalk.config import get_settings

# Request fields that never change the answer and must not change the key.
VOLATILE_FIELDS = frozenset({"user", "request_id", "idempotency_key", "timestamp", "ts", "session_id", "sessionId"})


class CacheMiss(LookupError):
    def __init__(self, key: str, description: str):
        self.key = key
        self.description = description
        super().__init__(
            f"cache miss in readonly mode for {description} (key {key}). "
            "Record it with a live run (`make probe` / `make record`, PUNCHWALK_CACHE=readwrite) "
            "or mock this call in the test."
        )


def _strip_volatile(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_volatile(v) for k, v in value.items() if k not in VOLATILE_FIELDS}
    if isinstance(value, list | tuple):
        return [_strip_volatile(v) for v in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(_strip_volatile(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def make_key(
    provider: str, endpoint: str, model: str, request: Any, upload_sha256s: Iterable[str] = ()
) -> str:
    h = hashlib.sha256()
    for part in (provider, endpoint, model, canonical_json(request), *sorted(upload_sha256s)):
        h.update(part.encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


@dataclass
class CacheEntry:
    key: str
    provider: str
    endpoint: str
    model: str
    description: str
    response: Any
    raw_text: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    source_dir: Path | None = None


class Cache:
    def __init__(
        self, mode: str | None = None, write_dir: Path | None = None, fixture_dir: Path | None = None
    ):
        s = get_settings()
        self.mode = mode or s.PUNCHWALK_CACHE
        self.write_dir = Path(write_dir or s.cache_dir)
        self.fixture_dir = Path(fixture_dir or s.FIXTURE_CACHE_DIR)

    # paths -------------------------------------------------------------
    @staticmethod
    def _json_path(root: Path, key: str) -> Path:
        return root / key[:2] / f"{key}.json"

    def _read_dirs(self) -> list[Path]:
        dirs = [self.write_dir]
        if self.fixture_dir.resolve() != self.write_dir.resolve():
            dirs.append(self.fixture_dir)
        return dirs

    # read / write --------------------------------------------------------
    def get(self, key: str) -> CacheEntry | None:
        if self.mode == "off":
            return None
        for root in self._read_dirs():
            p = self._json_path(root, key)
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                txt = p.with_suffix(".txt")
                return CacheEntry(
                    key=key,
                    provider=data.get("provider", ""),
                    endpoint=data.get("endpoint", ""),
                    model=data.get("model", ""),
                    description=data.get("description", ""),
                    response=data.get("response"),
                    raw_text=txt.read_text(encoding="utf-8") if txt.exists() else None,
                    meta=data.get("meta") or {},
                    created_at=data.get("created_at", ""),
                    source_dir=root,
                )
        return None

    def require_live(self, key: str, description: str) -> None:
        """Call before a live request: in readonly mode a miss is an error."""
        if self.mode == "readonly":
            raise CacheMiss(key, description)

    def put(
        self,
        key: str,
        *,
        provider: str,
        endpoint: str,
        model: str,
        description: str,
        response: Any,
        raw_text: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Path | None:
        if self.mode != "readwrite":
            return None
        p = self._json_path(self.write_dir, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        doc = {
            "key": key,
            "provider": provider,
            "endpoint": endpoint,
            "model": model,
            "description": description,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "meta": meta or {},
            "response": response,
        }
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(p)
        if raw_text is not None:
            p.with_suffix(".txt").write_text(raw_text, encoding="utf-8")
        return p

    def delete(self, key: str) -> None:
        p = self._json_path(self.write_dir, key)
        for f in (p, p.with_suffix(".txt")):
            f.unlink(missing_ok=True)

    def promote(self, keys: Iterable[str], dest: Path | None = None) -> list[Path]:
        """Copy entries (and sidecars) into `dest` (default FIXTURE_CACHE_DIR) for committing."""
        target = Path(dest or self.fixture_dir)
        copied: list[Path] = []
        for key in keys:
            src = None
            for root in self._read_dirs():
                if self._json_path(root, key).exists():
                    src = self._json_path(root, key)
                    break
            if src is None:
                raise CacheMiss(key, "promote(): entry not found in any cache dir")
            out = self._json_path(target, key)
            if src.resolve() == out.resolve():
                copied.append(out)
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out)
            copied.append(out)
            if src.with_suffix(".txt").exists():
                shutil.copy2(src.with_suffix(".txt"), out.with_suffix(".txt"))
        return copied


def get_cache() -> Cache:
    return Cache()
