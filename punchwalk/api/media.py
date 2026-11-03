"""`GET /media/{path}` (files under DATA_DIR, with HTTP range support) and the built PWA at `/`.

Range requests matter: iOS Safari won't seek (or even play) a <video> whose server ignores
`Range`. Starlette's FileResponse answers `Range: bytes=a-b` with 206 + Content-Range +
Accept-Ranges, `416` for unsatisfiable ranges, and supports HEAD and If-Range.

Only these top-level folders are served from DATA_DIR: the database, cost ledger and API cache
stay private.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response

from punchwalk.api.errors import ApiError
from punchwalk.config import REPO_ROOT, get_settings

PUBLIC_MEDIA_ROOTS = frozenset({"walks", "items", "projects", "demo"})
STATIC_DIR = REPO_ROOT / "punchwalk" / "static"
RESERVED_PREFIXES = ("api/", "media/", "healthz")

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


def media_file(rel: str) -> Path:
    """The file behind `/media/<rel>`, or a 404 ApiError."""
    if ".." in rel.replace("\\", "/").split("/"):
        raise ApiError(404, "That file isn't available.")
    top = rel.lstrip("/").split("/", 1)[0]
    if top not in PUBLIC_MEDIA_ROOTS:
        raise ApiError(404, "That file isn't available.")
    path = resolve_under(Path(get_settings().DATA_DIR), rel)
    if path is None or not path.is_file():
        raise ApiError(404, "That file isn't available. It may still be processing.")
    return path


@router.api_route("/media/{rel:path}", methods=["GET", "HEAD"], include_in_schema=False)
def get_media(rel: str) -> Response:
    path = media_file(rel)
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
