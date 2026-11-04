"""SAM 3.1 via `POST /v1/responses` (streamed). One concept per request; text OR box prompt.

The stream's output text is parsed only with `meta_sam_parser`. Zero matches is a valid,
empty result. Box coordinates are source pixels with half-open right/bottom (the parser
converts the API's inclusive x2/y2). Masks are box-local rasters; see
`punchwalk.render.masks` for the mapping to frame pixels.
"""

from __future__ import annotations

import base64
import io
import json
import re
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
from meta_sam_parser import (
    CompletedOutcome,
    FramePrompt,
    PromptBox,
    PromptObject,
    PromptPoint,
    SegmentationBoxRecord,
    SegmentationMask,
    SegmentationMaskRecord,
    SegmentationTextRecord,
    build_object_prompt,
    decode_mask_to_raster,
    decode_mask_to_svg_path,
    image_segmentation_format,
    video_segmentation_format,
)
from PIL import Image

from punchwalk.clients import calls, cost, meta_files
from punchwalk.clients.base import (
    PROVIDER_META,
    ApiError,
    http_client,
    meta_headers,
    raise_for_status,
    read_bytes,
    sha256_bytes,
    sha256_file,
    with_retries,
    wrap_transport_errors,
)
from punchwalk.clients.cache import get_cache, make_key
from punchwalk.config import get_settings

ENDPOINT = "/responses"
Media = Literal["image", "video"]

# Which image input form the API accepted ("data_url" or "file_id"); set on first success.
IMAGE_INPUT_MODE: str = "data_url"


@dataclass
class SamRecord:
    object_id: int
    frame_index: int
    box: tuple[float, float, float, float]  # x1, y1, x2, y2 in source px (x2/y2 exclusive)
    confidence: float | None
    mask: SegmentationMask | None = None
    mask_confidence: float | None = None
    media_size: tuple[int, int] | None = None  # (w, h) of the media SAM saw
    _raster: np.ndarray | None = field(default=None, repr=False)
    _svg: str | None = field(default=None, repr=False)

    @property
    def box_w(self) -> float:
        return self.box[2] - self.box[0]

    @property
    def box_h(self) -> float:
        return self.box[3] - self.box[1]

    @property
    def mask_size(self) -> tuple[int, int] | None:
        """(width, height) of the mask raster, or None."""
        return (int(self.mask.width), int(self.mask.height)) if self.mask else None

    def raster(self) -> np.ndarray:
        """Decoded mask as an (H, W) uint8 array of 0/1, in mask-raster coordinates."""
        if self._raster is None:
            if self.mask is None:
                self._raster = np.zeros((0, 0), dtype=np.uint8)
            else:
                flat = np.frombuffer(decode_mask_to_raster(self.mask), dtype=np.uint8)
                self._raster = flat.reshape(int(self.mask.height), int(self.mask.width))
        return self._raster

    @property
    def svg_path(self) -> str:
        """Mask outline as an SVG path in mask-raster coordinates ('' if empty)."""
        if self._svg is None:
            self._svg = decode_mask_to_svg_path(self.mask) if self.mask else ""
        return self._svg


@dataclass
class SamResult:
    media: Media
    raw_text: str
    frames: dict[int, list[SamRecord]]
    diagnostics: list[dict[str, Any]]
    media_size: tuple[int, int] | None  # (width, height) in source px
    complete: bool = True
    incomplete_reason: str | None = None
    usage: dict[str, Any] | None = None
    cached: bool = False
    record: calls.CallRecord | None = None
    request_summary: dict[str, Any] = field(default_factory=dict)

    @property
    def object_ids(self) -> list[int]:
        return sorted({r.object_id for recs in self.frames.values() for r in recs})

    def all_records(self) -> list[SamRecord]:
        return [r for f in sorted(self.frames) for r in self.frames[f]]


# --------------------------------------------------------------------------- parsing


def parse_output(raw_text: str, media: Media, media_size: tuple[int, int] | None = None) -> SamResult:
    """Parse SAM output text (concatenated deltas) with meta_sam_parser."""
    fmt = video_segmentation_format() if media == "video" else image_segmentation_format()
    parser = fmt.create_parser()
    parser.push(raw_text, emit=False)
    result = parser.finish(CompletedOutcome()).result

    frames: dict[int, list[SamRecord]] = {}
    by_key: dict[tuple[int, int], SamRecord] = {}
    for rec in result.records:
        if isinstance(rec, SegmentationTextRecord):
            continue
        fi = rec.frame.frame_index if rec.frame is not None else 0
        oid = int(rec.object_id)
        if isinstance(rec, SegmentationBoxRecord):
            sr = SamRecord(
                object_id=oid,
                frame_index=fi,
                box=(float(rec.left), float(rec.top), float(rec.right), float(rec.bottom)),
                confidence=rec.confidence,
            )
            existing = by_key.get((fi, oid))
            if existing is not None:
                frames[fi].remove(existing)
            by_key[(fi, oid)] = sr
            frames.setdefault(fi, []).append(sr)
        elif isinstance(rec, SegmentationMaskRecord):
            sr = by_key.get((fi, oid))
            if sr is None:
                b = rec.bounds
                sr = SamRecord(object_id=oid, frame_index=fi, box=(b.left, b.top, b.right, b.bottom), confidence=None)
                by_key[(fi, oid)] = sr
                frames.setdefault(fi, []).append(sr)
            sr.mask, sr.mask_confidence = rec.mask, rec.confidence  # latest revision wins
            sr._raster = sr._svg = None
            if sr.confidence is None:
                sr.confidence = rec.confidence
    size = media_size or size_from_output(raw_text)
    for recs in frames.values():
        for r in recs:
            r.media_size = size
    diags = [
        {"severity": d.severity, "code": d.code, "message": d.message, "line": d.line, "raw": d.raw[:300]}
        for d in result.diagnostics
    ]
    return SamResult(
        media=media,
        raw_text=raw_text,
        frames=dict(sorted(frames.items())),
        diagnostics=diags,
        media_size=size,
    )


_SIZE_RE = re.compile(r"<\|box;[^|]*?\bw=(\d+);\s*h=(\d+)")


def size_from_output(raw_text: str) -> tuple[int, int] | None:
    """Source (w, h) from a box token's `w`/`h` fields (header fields only; masks untouched)."""
    m = _SIZE_RE.search(raw_text)
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_sse(lines) -> dict[str, Any]:
    """Collect a Responses SSE stream into {text, completed, usage, incomplete, error, events}."""
    text_parts: list[str] = []
    done_text: str | None = None
    out: dict[str, Any] = {"completed": False, "incomplete": None, "error": None, "usage": None, "events": {}}
    for line in lines:
        if not line or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            ev = json.loads(payload)
        except json.JSONDecodeError:
            continue
        et = ev.get("type", "")
        out["events"][et] = out["events"].get(et, 0) + 1
        if et == "response.output_text.delta":
            text_parts.append(ev.get("delta") or "")
        elif et == "response.output_text.done":
            done_text = ev.get("text")
        elif et == "response.completed":
            out["completed"] = True
            resp = ev.get("response") or {}
            out["usage"] = resp.get("usage")
            if done_text is None and not text_parts:
                done_text = _text_from_response(resp)
        elif et == "response.incomplete":
            out["incomplete"] = ((ev.get("response") or {}).get("incomplete_details") or {}).get("reason") or "incomplete"
        elif et in ("response.failed", "error"):
            err = (ev.get("response") or {}).get("error") or ev.get("error") or ev
            out["error"] = err.get("message") if isinstance(err, dict) else str(err)
    out["text"] = "".join(text_parts) if text_parts else (done_text or "")
    return out


def _text_from_response(resp: dict) -> str:
    parts = []
    for item in resp.get("output") or []:
        for c in item.get("content") or []:
            if c.get("type") == "output_text":
                parts.append(c.get("text") or "")
    return "".join(parts)


# --------------------------------------------------------------------------- media helpers


def _image_bytes_and_size(image: bytes | str | Path) -> tuple[bytes, tuple[int, int], str]:
    data = read_bytes(image)
    with Image.open(io.BytesIO(data)) as im:
        size = im.size
        fmt = (im.format or "JPEG").lower()
    mime = "image/png" if fmt == "png" else "image/jpeg" if fmt in ("jpeg", "jpg", "mpo") else f"image/{fmt}"
    return data, size, mime


def video_probe(path: str | Path) -> tuple[tuple[int, int] | None, int | None]:
    """((display w, h), frame count) via ffprobe, honoring rotation. None when unavailable."""
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
                "-show_entries", "stream=width,height,nb_read_packets:stream_side_data=rotation:stream_tags=rotate",
                "-of", "json", str(path),
            ],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
        st = json.loads(out)["streams"][0]
        w, h = int(st["width"]), int(st["height"])
        rot = 0
        for sd in st.get("side_data_list") or []:
            if "rotation" in sd:
                rot = int(sd["rotation"])
        rot = rot or int((st.get("tags") or {}).get("rotate", 0) or 0)
        if abs(rot) % 180 == 90:
            w, h = h, w
        n = int(st.get("nb_read_packets") or 0) or None
        return (w, h), n
    except (OSError, subprocess.SubprocessError, KeyError, ValueError, IndexError):
        return None, None


def _resolve_video(video: str | Path) -> tuple[str, str, Path | None]:
    """-> (file_id, content key, local path or None). Uploads local paths (cached)."""
    p = Path(str(video))
    if p.exists():
        sha = sha256_file(p)
        return meta_files.upload(p), sha, p
    file_id = str(video)
    info = meta_files.file_index().get(file_id) or {}
    path = meta_files.local_path(info)
    return file_id, info.get("sha256") or f"file_id:{file_id}", path


# --------------------------------------------------------------------------- core request


def _metadata(extra: dict[str, str] | None = None) -> dict[str, str]:
    md = {"include_confidence": "true", "mask_encoding": "one_bit"}
    md.update(extra or {})
    return md


def _run(
    *,
    media: Media,
    text: str,
    media_part: dict[str, Any],
    media_key: dict[str, Any],
    upload_shas: list[str],
    metadata: dict[str, str],
    media_size: tuple[int, int] | None,
    frames_hint: int | None,
    desc: str,
) -> SamResult:
    """Send one streamed SAM request (or replay it from the cache) and parse the result."""
    s = get_settings()
    cache = get_cache()
    body = {
        "model": s.SAM_MODEL,
        "stream": True,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": text}, media_part]}],
        "metadata": metadata,
    }
    # Key on media content, not on per-upload ids or inline base64.
    key_request = {**body, "input": [{"text": text, "media": media_key}]}
    key = make_key(PROVIDER_META, ENDPOINT, s.SAM_MODEL, key_request, upload_shas)
    summary = {"model": s.SAM_MODEL, "media": media, "text": text[:200], "metadata": metadata, "media_ref": media_key}

    hit = cache.get(key)
    if hit is not None:
        res = parse_output(hit.raw_text or "", media, media_size or tuple(hit.meta.get("media_size") or ()) or None)
        res.usage, res.cached, res.request_summary = (hit.response or {}).get("usage"), True, summary
        res.complete = (hit.response or {}).get("completed", True)
        if media == "video":  # frames processed, as on a live call (not frames with detections)
            processed = (res.usage or {}).get("video_frames_processed")
            n_frames = int(processed or frames_hint or (max(res.frames) + 1 if res.frames else 0))
        else:
            n_frames = 1
        res.record = calls.emit(
            calls.CallRecord(
                provider=PROVIDER_META, model=s.SAM_MODEL, endpoint=ENDPOINT, cached=True, cache_key=key,
                frames=n_frames, unit_kind="frames" if media == "video" else "images",
            )
        )
        return res
    cache.require_live(key, desc)
    est = cost.sam_video_cost(frames_hint or 0) if media == "video" else cost.sam_image_cost(1)
    cost.assert_under_cap(est)

    def call() -> dict[str, Any]:
        with (
            wrap_transport_errors(PROVIDER_META, ENDPOINT, s.SAM_MODEL),
            http_client() as client,
            client.stream(
                "POST", f"{s.META_API_BASE}{ENDPOINT}", headers=meta_headers(s, accept="text/event-stream"), json=body
            ) as resp,
        ):
            raise_for_status(resp, PROVIDER_META, ENDPOINT, s.SAM_MODEL)
            ctype = resp.headers.get("content-type", "")
            if "text/event-stream" not in ctype:
                data = json.loads(resp.read())
                return {"text": _text_from_response(data), "completed": data.get("status", "completed") == "completed",
                        "usage": data.get("usage"), "incomplete": None, "error": None, "events": {"json": 1}}
            out = parse_sse(resp.iter_lines())
        if out["error"]:
            raise ApiError(PROVIDER_META, ENDPOINT, 500, f"stream error: {out['error']}", model=s.SAM_MODEL)
        return out

    t0 = time.monotonic()
    record = calls.CallRecord(provider=PROVIDER_META, model=s.SAM_MODEL, endpoint=ENDPOINT, cache_key=key)
    try:
        out = with_retries(
            call, should_retry=lambda e, a: e.transient and a < 3, backoff_s=lambda a: 2.0 * a
        )
    except ApiError as err:
        record.ok, record.error = False, str(err)[:500]
        record.latency_ms = int((time.monotonic() - t0) * 1000)
        calls.emit(record)
        raise
    record.latency_ms = int((time.monotonic() - t0) * 1000)

    res = parse_output(out["text"], media, media_size)
    res.usage, res.complete, res.incomplete_reason = out["usage"], out["completed"], out["incomplete"]
    res.request_summary = summary
    if media == "video":
        processed = (out["usage"] or {}).get("video_frames_processed")
        n = int(processed or frames_hint or (max(res.frames) + 1 if res.frames else 0))
        record.frames, record.units, record.unit_kind = n, max(n, cost.SAM_VIDEO_MIN_FRAMES), "frames"
        record.est_cost_usd = cost.sam_video_cost(n)
    else:
        record.frames, record.units, record.unit_kind, record.est_cost_usd = 1, 1, "images", cost.sam_image_cost(1)
    res.record = calls.emit(record)
    cache.put(
        key, provider=PROVIDER_META, endpoint=ENDPOINT, model=s.SAM_MODEL, description=desc,
        response={k: out[k] for k in ("completed", "incomplete", "usage", "events")},
        raw_text=out["text"],
        meta={"media_size": list(res.media_size) if res.media_size else None, "summary": summary},
    )
    return res


def _image_request(image: bytes | str | Path, text: str, desc: str) -> SamResult:
    global IMAGE_INPUT_MODE
    data, size, mime = _image_bytes_and_size(image)
    sha = sha256_bytes(data)
    common = dict(
        media="image", text=text, media_key={"image_sha256": sha}, upload_shas=[sha],
        metadata=_metadata(), media_size=size, frames_hint=1, desc=desc,
    )
    if IMAGE_INPUT_MODE == "data_url":
        part = {"type": "input_image", "image_url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}
        try:
            return _run(media_part=part, **common)
        except ApiError as err:
            if err.status != 400 or "image" not in err.body.lower():
                raise
            IMAGE_INPUT_MODE = "file_id"
    fid = meta_files.upload(data, filename=f"frame_{sha[:12]}.{'png' if mime == 'image/png' else 'jpg'}")
    return _run(media_part={"type": "input_image", "file_id": fid}, **common)


def _video_request(
    video: str | Path, text: str, metadata: dict[str, str], media_size: tuple[int, int] | None, desc: str
) -> SamResult:
    file_id, content_key, path = _resolve_video(video)
    size, n_frames = video_probe(path) if path else (None, None)
    common = dict(
        media="video", text=text, media_key={"video": content_key}, upload_shas=[],
        metadata=metadata, media_size=media_size or size, frames_hint=n_frames, desc=desc,
    )
    try:
        return _run(media_part={"type": "input_video", "file_id": file_id}, **common)
    except ApiError as err:
        if path is None or not meta_files.looks_like_bad_file_id(err):
            raise
        new_id = meta_files.upload(path, force=True)
        return _run(media_part={"type": "input_video", "file_id": new_id}, **common)


# --------------------------------------------------------------------------- public API


def _check_concept(concept: str) -> str:
    c = " ".join(concept.split())
    if not c:
        raise ValueError("SAM concept must be a non-empty noun phrase")
    if len(c) > 100:
        raise ValueError(f"SAM concept must be one short noun phrase (<= 100 chars), got {len(c)}")
    if c.startswith("<"):
        raise ValueError("SAM concept looks like a box/point prompt; use boxes_on_image/boxes_on_video")
    return c


def concept_on_video(video: str | Path, concept: str, *, media_size: tuple[int, int] | None = None) -> SamResult:
    """Track every instance of `concept` through a video (local path, uploaded once, or file id)."""
    c = _check_concept(concept)
    return _video_request(video, c, _metadata(), media_size, f"SAM concept {c!r} on video {Path(str(video)).name}")


def concept_on_image(image: bytes | str | Path, concept: str) -> SamResult:
    c = _check_concept(concept)
    name = "bytes" if isinstance(image, bytes) else Path(image).name
    return _image_request(image, c, f"SAM concept {c!r} on image {name}")


BoxLike = tuple[int, int, int, int]  # x1, y1, x2, y2 (half-open) in media pixels


def _prompt_objects(boxes: list[BoxLike] | list[PromptObject], frame: int = 0) -> list[PromptObject]:
    objs: list[PromptObject] = []
    for i, b in enumerate(boxes):
        if isinstance(b, PromptObject):
            objs.append(b)
        else:
            x1, y1, x2, y2 = (int(round(v)) for v in b)
            objs.append(PromptObject(id=i, prompts=[FramePrompt(frame=frame, box=PromptBox(x1, y1, x2, y2))]))
    return objs


def boxes_on_image(image: bytes | str | Path, boxes: list[BoxLike] | list[PromptObject]) -> SamResult:
    """Segment the objects inside each box (object ids = list positions)."""
    _, size, _ = _image_bytes_and_size(image)
    text = build_object_prompt(size=size, objects=_prompt_objects(boxes))
    name = "bytes" if isinstance(image, bytes) else Path(image).name
    return _image_request(image, text, f"SAM {len(boxes)} box prompt(s) on image {name}")


@dataclass(frozen=True)
class ImageObject:
    """One prompted object on a single image (frame 0): a box and/or positive and negative points,
    in media pixels. `id` becomes the object id in SAM's output."""

    id: int
    box: tuple[float, float, float, float] | None = None  # x1, y1, x2, y2 (x2/y2 exclusive)
    positive: tuple[tuple[float, float], ...] = ()
    negative: tuple[tuple[float, float], ...] = ()


def image_prompt_objects(objects: Sequence[ImageObject], size: tuple[int, int]) -> list[PromptObject]:
    """ImageObjects -> PromptObjects on frame 0, coordinates rounded and clamped into the media
    (`size` = (w, h)). Raises ValueError for an object with no box and no points, or an empty box."""
    w, h = size
    out: list[PromptObject] = []
    for o in objects:
        box = None
        if o.box is not None:
            x1, y1, x2, y2 = o.box
            bx1, by1 = int(min(max(round(x1), 0), w - 1)), int(min(max(round(y1), 0), h - 1))
            bx2, by2 = int(min(max(round(x2), 0), w)), int(min(max(round(y2), 0), h))
            if bx2 <= bx1 or by2 <= by1:
                raise ValueError(f"SAM object {o.id}: empty box {o.box} inside a {w}x{h} image")
            box = PromptBox(bx1, by1, bx2, by2)
        points = [
            PromptPoint(int(min(max(round(x), 0), w - 1)), int(min(max(round(y), 0), h - 1)), label)
            for pts, label in ((o.positive, "positive"), (o.negative, "negative"))
            for x, y in pts
        ]
        if box is None and not points:
            raise ValueError(f"SAM object {o.id}: needs a box or at least one point")
        out.append(PromptObject(id=int(o.id), prompts=[FramePrompt(frame=0, box=box, points=points)]))
    return out


def objects_on_image(image: bytes | str | Path, objects: Sequence[ImageObject]) -> SamResult:
    """Segment several prompted objects (box / box + points / points, with negative points) on one
    image in ONE request (`<0f>` prompt). Records come back with `object_id` = `ImageObject.id`."""
    if not objects:
        raise ValueError("objects_on_image needs at least one object")
    if len(objects) > 16:
        raise ValueError(f"SAM takes at most 16 prompted objects per request (got {len(objects)})")
    _, size, _ = _image_bytes_and_size(image)
    text = build_object_prompt(size=size, objects=image_prompt_objects(objects, size))
    name = "bytes" if isinstance(image, bytes) else Path(image).name
    return _image_request(image, text, f"SAM {len(objects)} object prompt(s) on image {name}")


def boxes_on_video(
    video: str | Path,
    frame_prompt: list[PromptObject] | str,
    *,
    media_size: tuple[int, int] | None = None,
    propagation_direction: Literal["both", "forward", "backward"] = "both",
) -> SamResult:
    """Track prompted objects through a video. `frame_prompt` is PromptObjects (built against
    `media_size`, the video's display size) or a prompt string from build_object_prompt."""
    if isinstance(frame_prompt, str):
        text = frame_prompt
    else:
        size = media_size
        if size is None:
            _, _, path = _resolve_video(video)
            size = video_probe(path)[0] if path else None
        if size is None:
            raise ValueError("boxes_on_video needs media_size=(w, h) when the video path is unknown")
        text = build_object_prompt(size=size, objects=frame_prompt)
        media_size = size
    md = _metadata({"propagation_direction": propagation_direction})
    return _video_request(video, text, md, media_size, f"SAM box prompt on video {Path(str(video)).name}")
