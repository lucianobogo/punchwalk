"""`GET /media/{path}` (files under DATA_DIR, with HTTP range support) and the built PWA at `/`.

Range requests matter: iOS Safari won't seek (or even play) a <video> whose server ignores
`Range`. Starlette's FileResponse answers `Range: bytes=a-b` with 206 + Content-Range +
Accept-Ranges, `416` for unsatisfiable ranges, and supports HEAD and If-Range.

Only what the app shows is public (`is_public_media`): each walk's display.mp4 and poster.jpg, and
the images under walks/<id>/items/ (evidence, crops, pointing and after frames). The uploaded
originals (clips/, with the phone's location metadata), audio.wav, sam*.mp4, meta.json, stages/,
the database, the cost ledger and the API cache are never served. `items/` and `demo/` (seeded
media) serve images and videos only.
"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response

from punchwalk.api.errors import ApiError, error_response
from punchwalk.config import REPO_ROOT, get_settings

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
WALK_PUBLIC_FILES = frozenset({"display.mp4", "poster.jpg"})
PRIVATE_SEGMENTS = frozenset({"clips", "stages", "_work", "cache"})
STATIC_DIR = REPO_ROOT / "punchwalk" / "static"
RESERVED_PREFIXES = ("api/", "media/", "healthz", "mock-api/")  # never the SPA index (mock-api: mock builds only)

mimetypes.add_type("video/mp4", ".mp4")
mimetypes.add_type("video/quicktime", ".mov")
mimetypes.add_type("audio/wav", ".wav")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")

router = APIRouter()


def resolve_under(root: Path, rel: str) -> Path | None:
    """`root/rel` if it stays inside `root` and has no `..`/hidden segments; else None."""
    parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or p.startswith(".") for p in parts):
        return None
    root = root.resolve()
    path = (root / Path(*parts)).resolve()
    if path != root and root not in path.parents:
        return None
    return path


def is_public_media(rel: str) -> bool:
    """The allowlist described in the module docstring."""
    parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or p.startswith(".") for p in parts):
        return False
    if PRIVATE_SEGMENTS.intersection(parts):
        return False
    name = parts[-1].lower()
    ext = Path(name).suffix
    top = parts[0]
    if top == "walks":
        if len(parts) == 3:
            return name in WALK_PUBLIC_FILES
        return len(parts) >= 5 and parts[2] == "items" and ext in IMAGE_EXTENSIONS
    if top == "items":
        return ext in IMAGE_EXTENSIONS
    if top == "demo":
        return ext in IMAGE_EXTENSIONS or (ext == ".mp4" and not name.startswith("sam"))
    return False


def media_file(rel: str) -> Path:
    """The file behind `/media/<rel>`, or a 404 ApiError."""
    if not is_public_media(rel):
        raise ApiError(404, "That file isn't available.")
    path = resolve_under(Path(get_settings().DATA_DIR), rel)
    if path is None or not path.is_file():
        raise ApiError(404, "That file isn't available. It may still be processing.")
    return path


_RANGE = re.compile(r"^\s*(\d*)\s*-\s*(\d*)\s*$")


def unsatisfiable(range_header: str | None, size: int) -> bool:
    """True when a `bytes=` Range can't be served from a file of `size` bytes (every part out of range).

    Malformed headers return False and are left to FileResponse."""
    if not range_header or not range_header.strip().lower().startswith("bytes="):
        return False
    satisfiable = False
    for spec in range_header.split("=", 1)[1].split(","):
        m = _RANGE.match(spec)
        if not m or (not m.group(1) and not m.group(2)):
            return False
        start, end = m.group(1), m.group(2)
        if start:
            if int(start) < size and (not end or int(end) >= int(start)):
                satisfiable = True
        elif int(end) > 0 and size > 0:  # suffix range: the last N bytes
            satisfiable = True
    return not satisfiable


@router.api_route("/media/{rel:path}", methods=["GET", "HEAD"], include_in_schema=False)
def get_media(rel: str, request: Request) -> Response:
    path = media_file(rel)
    size = path.stat().st_size
    if unsatisfiable(request.headers.get("range"), size):
        return error_response(
            416,
            f"That byte range is outside the file, which is {size} bytes long.",
            headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
        )
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    # Media files are written once per id; videos can be cached briefly, the app revalidates.
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "public, max-age=300"})


# --------------------------------------------------------------------------- the PWA


spa_router = APIRouter()


def static_index() -> Path | None:
    index = STATIC_DIR / "index.html"
    return index if index.is_file() else None


@spa_router.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
def spa(full_path: str) -> Response:
    """Built files from punchwalk/static; any other non-API path gets index.html (client routing)."""
    if full_path.startswith(RESERVED_PREFIXES):
        raise ApiError(404, "Nothing here. Check the link and try again.")
    index = static_index()
    if index is None:
        raise ApiError(404, "The web app isn't built yet. Run `make build`, or use the Vite dev server on :5173.")
    if full_path:
        path = resolve_under(STATIC_DIR, full_path)
        if path is not None and path.is_file():
            immutable = full_path.startswith("assets/")
            cache = "public, max-age=31536000, immutable" if immutable else "no-cache"
            media_type = mimetypes.guess_type(path.name)[0]
            return FileResponse(path, media_type=media_type, headers={"Cache-Control": cache})
    return FileResponse(index, media_type="text/html", headers={"Cache-Control": "no-cache"})
