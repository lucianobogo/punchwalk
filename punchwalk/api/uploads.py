"""Walk upload: a multipart body with ordered clips, streamed straight to disk.

Clips land in `DATA_DIR/walks/<walk_id>/clips/<order:02d>_<filename>` in the order they appear in the
form (the agreed layout the pipeline's ingest reads). Limits: <= 400 MB in total (counted while
streaming) and <= 10 minutes in total (ffprobe after the body is in). On any error the walk folder
is removed and nothing is written to the database.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import IO, Any

from python_multipart import MultipartParser
from python_multipart.multipart import parse_options_header
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from punchwalk.api.errors import ApiError
from punchwalk.api.guards import MAX_CLIPS, MAX_UPLOAD_BYTES, MAX_WALK_DURATION_S, MiB, too_large_detail
from punchwalk.config import get_settings

VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".m4v"})
VIDEO_CONTENT_TYPES = {"video/mp4": ".mp4", "video/quicktime": ".mov", "video/x-m4v": ".m4v"}
MAX_FIELD_BYTES = 4096
CLIP_FIELD = "clips"


@dataclass
class UploadedClip:
    order: int
    filename: str  # sanitized original name, e.g. "walk1_a.mp4"
    path: Path
    size_bytes: int = 0
    duration_s: float = 0.0
    created_at: datetime | None = None


@dataclass
class ParsedUpload:
    fields: dict[str, str] = field(default_factory=dict)
    clips: list[UploadedClip] = field(default_factory=list)
    total_bytes: int = 0


def walk_dir(walk_id: str) -> Path:
    return Path(get_settings().DATA_DIR) / "walks" / walk_id


def clip_relpath(walk_id: str, order: int, filename: str) -> str:
    return f"walks/{walk_id}/clips/{order:02d}_{filename}"


def safe_filename(raw: str | None, order: int, content_type: str | None) -> str:
    """Basename only, [A-Za-z0-9._-], <= 80 chars, with a video extension (or a readable 415)."""
    name = re.split(r"[\\/]", raw or "")[-1].strip()
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or f"clip_{order + 1}"
    stem, dot, ext = name.rpartition(".")
    ext = f".{ext.lower()}" if dot else ""
    if not dot:
        stem = name
    if ext not in VIDEO_EXTENSIONS:
        guessed = VIDEO_CONTENT_TYPES.get((content_type or "").split(";")[0].strip().lower())
        if guessed is None:
            shown = raw or name
            raise ApiError(415, f"“{shown}” isn't a video we can read. Upload .mp4 or .mov clips.")
        stem, ext = (name, guessed) if ext else (stem, guessed)
    return f"{stem[:72]}{ext}"


class _StreamingForm:
    """python-multipart callbacks that write `clips` parts to files and keep small text fields."""

    def __init__(self, walk_id: str, out: ParsedUpload) -> None:
        self.walk_id = walk_id
        self.out = out
        self.clip_dir = walk_dir(walk_id) / "clips"
        self._headers: dict[str, str] = {}
        self._hname = b""
        self._hvalue = b""
        self._name: str | None = None
        self._file: IO[bytes] | None = None
        self._clip: UploadedClip | None = None
        self._buf = bytearray()
        self._skip = False

    def callbacks(self) -> dict[str, Any]:
        return {
            "on_part_begin": self.on_part_begin,
            "on_header_field": lambda d, s, e: self._add("_hname", d[s:e]),
            "on_header_value": lambda d, s, e: self._add("_hvalue", d[s:e]),
            "on_header_end": self.on_header_end,
            "on_headers_finished": self.on_headers_finished,
            "on_part_data": self.on_part_data,
            "on_part_end": self.on_part_end,
        }

    def _add(self, attr: str, data: bytes) -> None:
        setattr(self, attr, getattr(self, attr) + data)

    def on_part_begin(self) -> None:
        self._headers, self._name, self._file, self._clip = {}, None, None, None
        self._buf = bytearray()
        self._skip = False

    def on_header_end(self) -> None:
        self._headers[self._hname.decode("latin-1").lower()] = self._hvalue.decode("utf-8", "replace")
        self._hname, self._hvalue = b"", b""

    def on_headers_finished(self) -> None:
        _, params = parse_options_header(self._headers.get("content-disposition", ""))
        name = params.get(b"name", b"").decode("utf-8", "replace")
        filename = params.get(b"filename")
        self._name = name
        if filename is None:
            return
        if name != CLIP_FIELD:
            self._skip = True  # an unexpected file field: drain and ignore it
            return
        order = len(self.out.clips)
        if order >= MAX_CLIPS:
            raise ApiError(400, f"A walk can have at most {MAX_CLIPS} clips. Upload the rest as another walk.")
        clean = safe_filename(filename.decode("utf-8", "replace"), order, self._headers.get("content-type"))
        self.clip_dir.mkdir(parents=True, exist_ok=True)
        path = self.clip_dir / f"{order:02d}_{clean}"
        self._clip = UploadedClip(order=order, filename=clean, path=path)
        self._file = path.open("wb")

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self._file is not None and self._clip is not None:
            self._file.write(data[start:end])
            self._clip.size_bytes += end - start
        elif not self._skip:
            self._buf += data[start:end]
            if len(self._buf) > MAX_FIELD_BYTES:
                raise ApiError(400, f"The form field “{self._name}” is too long.")

    def on_part_end(self) -> None:
        if self._file is not None and self._clip is not None:
            self._file.close()
            self._file = None
            self.out.clips.append(self._clip)
            self._clip = None
        elif self._name and not self._skip:
            self.out.fields[self._name] = self._buf.decode("utf-8", "replace").strip()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


async def receive_upload(request: Request, walk_id: str) -> ParsedUpload:
    """Stream the multipart body to disk. Raises ApiError (and removes the folder) on any problem."""
    ctype = request.headers.get("content-type", "")
    media_type, params = parse_options_header(ctype)
    boundary = params.get(b"boundary")
    if media_type != b"multipart/form-data" or not boundary:
        raise ApiError(415, "Send the clips as multipart/form-data, one `clips` part per clip, in walk order.")
    out = ParsedUpload()
    form = _StreamingForm(walk_id, out)
    parser = MultipartParser(boundary, form.callbacks())  # type: ignore[arg-type]
    limit = MAX_UPLOAD_BYTES
    try:
        async for chunk in request.stream():
            if not chunk:
                continue
            out.total_bytes += len(chunk)
            if out.total_bytes > limit + 2 * MiB:
                raise ApiError(413, too_large_detail(limit + 1))
            await run_in_threadpool(parser.write, chunk)
        await run_in_threadpool(parser.finalize)
        if sum(c.size_bytes for c in out.clips) > limit:
            raise ApiError(413, too_large_detail(limit + 1))
    except ApiError:
        form.close()
        discard(walk_id)
        raise
    except Exception as exc:
        form.close()
        discard(walk_id)
        raise ApiError(400, "The upload was cut off or isn't a valid form. Try uploading again.") from exc
    form.close()
    if not out.clips:
        discard(walk_id)
        raise ApiError(400, "Add at least one clip. Send each video as a `clips` part, in walk order.")
    return out


def _creation_time(path: Path) -> datetime | None:
    from punchwalk.pipeline import media

    try:
        data = media.ffprobe_json(path, ["-show_format"])
    except Exception:
        return None
    raw = ((data.get("format") or {}).get("tags") or {}).get("creation_time")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _probe_clips(clips: list[UploadedClip]) -> None:
    from punchwalk.pipeline import media

    for clip in clips:
        if clip.size_bytes == 0:
            raise ApiError(400, f"“{clip.filename}” is empty. Pick the clip again and re-upload.")
        try:
            info = media.probe(clip.path)
        except media.MediaError as exc:
            raise ApiError(415, f"We couldn't read “{clip.filename}” as a video. Upload .mp4 or .mov clips.") from exc
        clip.duration_s = float(info.duration_s)
        clip.created_at = _creation_time(clip.path)


def _minutes(seconds: float) -> str:
    mins, secs = divmod(round(seconds), 60)
    return f"{mins} min {secs} s" if secs else f"{mins} min"


async def validate_clips(walk_id: str, parsed: ParsedUpload) -> None:
    """ffprobe every clip (readable, has a duration) and enforce the 10-minute total."""
    try:
        await run_in_threadpool(_probe_clips, parsed.clips)
        total = sum(c.duration_s for c in parsed.clips)
        if total > MAX_WALK_DURATION_S + 0.5:
            raise ApiError(
                400,
                f"This walk is {_minutes(total)} long. A walk can be at most 10 minutes; "
                "split it into parts and upload each part as its own walk.",
            )
    except ApiError:
        discard(walk_id)
        raise


def discard(walk_id: str) -> None:
    shutil.rmtree(walk_dir(walk_id), ignore_errors=True)
