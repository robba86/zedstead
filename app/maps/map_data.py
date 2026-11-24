"""The map image for a terrain: the admin's own upload, or a cleared one.

No map image ships with zsm (a map's picture is its author's work). The admin
can upload their own (`own_image`). A community map whose maker agreed in
writing (`registry.CLEARED_MAPS`) has its image downloaded once from zedstead.com
into `maps_dir()` (`cleared_image`).
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from app.shared.json_utils import atomic_write_bytes
from app.shared.paths import maps_dir
from app.shared.zip_utils import is_bare_name

log = logging.getLogger(__name__)

# A terrain arrives from a query string (`/api/maps/thumbnail`,
# `/api/map-editor/map-image`), and `../..` or an absolute path there served any
# image on the box (plans/57 E1). A terrain is a mission folder's suffix, never
# a path, so every lookup and write below asks `zip_utils.is_bare_name` first,
# the one check for a name joined onto a directory (plans/68 NEW-5).


# ---------------------------------------------------------------------------
# The admin's own image (plans/69 Step 4)
#
# A map image the admin uploads for a terrain, in `own_images_dir()`. It is
# theirs to use, and it is how an admin gets a picture onto their own panel.
# ---------------------------------------------------------------------------

#: The largest image accepted. An 8192 px map is tens of MB as a JPEG or PNG.
MAX_OWN_IMAGE_BYTES = 64 * 1024 * 1024

#: The formats a browser draws, by extension; told apart by their first bytes.
OWN_IMAGE_EXTS = ("jpg", "png", "webp")

#: The largest preview accepted. The browser makes it 1024 px wide (a few
#: hundred KB); the Maps page and the Dashboard show it instead of the full
#: image, which a browser downsamples on every repaint of the card.
MAX_OWN_THUMBNAIL_BYTES = 2 * 1024 * 1024


def own_images_dir() -> Path:
    return maps_dir() / "own"


def own_thumbnails_dir() -> Path:
    # A folder of its own: `<terrain>.thumb.jpg` beside the images would be
    # the own image of a terrain named `<terrain>.thumb`.
    return own_images_dir() / "thumbs"


def image_type(data: bytes) -> str | None:
    """The extension for an image's bytes: ``jpg``, ``png``, ``webp`` or None.

    Read from the file's signature, never from its name or Content-Type, which
    the sender chooses.
    """
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def own_image(terrain: str) -> Path | None:
    """The image the admin uploaded for *terrain*, if there is one."""
    if not is_bare_name(terrain):
        return None
    for ext in OWN_IMAGE_EXTS:
        p = own_images_dir() / f"{terrain}.{ext}"
        if p.is_file():
            return p
    return None


def save_own_image(terrain: str, data: bytes) -> Path:
    """Store *data* as the admin's image for *terrain*, replacing any earlier one.

    Raises `ValueError` for a name that is not one folder name, an empty or
    oversized file, or bytes that are not a JPEG, PNG or WebP image. The
    caller checks that *terrain* is a map it knows.
    """
    if not is_bare_name(terrain):
        raise ValueError(f"{terrain!r} is not a map name")
    if len(data) > MAX_OWN_IMAGE_BYTES:
        raise ValueError(f"That file is larger than {MAX_OWN_IMAGE_BYTES // (1024 * 1024)} MB.")
    ext = image_type(data)
    if ext is None:
        raise ValueError("That file is not a JPEG, PNG or WebP image.")
    dest = own_images_dir() / f"{terrain}.{ext}"
    atomic_write_bytes(dest, data)
    for other in OWN_IMAGE_EXTS:
        if other != ext:
            (own_images_dir() / f"{terrain}.{other}").unlink(missing_ok=True)
    # The old preview shows the old image; the upload sends a new one next.
    (own_thumbnails_dir() / f"{terrain}.jpg").unlink(missing_ok=True)
    log.info("Saved the admin's own map image for %s (%d bytes)", terrain, len(data))
    return dest


def remove_own_image(terrain: str) -> bool:
    """Delete the admin's image for *terrain*; False when there was none."""
    path = own_image(terrain)
    if path is None:
        return False
    path.unlink(missing_ok=True)
    (own_thumbnails_dir() / f"{terrain}.jpg").unlink(missing_ok=True)
    return True


def own_thumbnail(terrain: str) -> Path | None:
    """The small preview of the admin's image for *terrain*, if there is one."""
    if not is_bare_name(terrain):
        return None
    p = own_thumbnails_dir() / f"{terrain}.jpg"
    return p if p.is_file() else None


def save_own_thumbnail(terrain: str, data: bytes) -> Path:
    """Store *data* as the preview of the admin's image for *terrain*.

    Raises `ValueError` for a name that is not one folder name, a terrain
    with no own image, an empty or oversized file, or bytes that are not a
    JPEG. The browser makes the preview from the file it just uploaded.
    """
    if not is_bare_name(terrain):
        raise ValueError(f"{terrain!r} is not a map name")
    if own_image(terrain) is None:
        raise ValueError("There is no image of your own for this map.")
    if len(data) > MAX_OWN_THUMBNAIL_BYTES:
        raise ValueError(f"That preview is larger than {MAX_OWN_THUMBNAIL_BYTES // (1024 * 1024)} MB.")
    if image_type(data) != "jpg":
        raise ValueError("That preview is not a JPEG image.")
    dest = own_thumbnails_dir() / f"{terrain}.jpg"
    atomic_write_bytes(dest, data)
    return dest


# ---------------------------------------------------------------------------
# A cleared map's image, downloaded from zedstead.com
# ---------------------------------------------------------------------------

#: The largest cleared image accepted; Banov Frost's is 28 MB.
MAX_CLEARED_IMAGE_BYTES = 64 * 1024 * 1024
#: How long a failed download waits before the next try.
CLEARED_FAILURE_BACKOFF = 3600
_cleared_failed: dict[str, float] = {}
_cleared_lock = threading.Lock()


def fetch_capped(url: str, dest: Path, max_bytes: int, what: str) -> int:
    """Download *url* into *dest*, refusing past *max_bytes*; returns the size.

    For a fixed URL from `registry.CLEARED_MAPS` only, never one a request
    chooses. TLS is verified (requests' default), with a 60 s timeout.
    """
    import requests

    from app.shared.zip_utils import save_capped

    with requests.get(url, timeout=60, stream=True) as resp:
        resp.raise_for_status()
        return save_capped(resp.iter_content(1 << 16), dest, max_bytes, source=what)


def cleared_images_dir() -> Path:
    """Where the cleared maps' downloaded images are kept, shared by every instance."""
    return maps_dir() / "cleared"


#: The formats a cleared image is kept in; zedstead.com serves WebP.
CLEARED_IMAGE_EXTS = ("webp", "jpg")


def _cleared_path(terrain: str, ext: str) -> Path:
    """The cache file of *terrain*'s cleared image in format *ext*."""
    return cleared_images_dir() / f"{terrain.lower()}.{ext}"


def cleared_image_cached(terrain: str) -> Path | None:
    """The downloaded image of a cleared map, without a download."""
    from app.maps.registry import cleared_map

    entry = cleared_map(terrain)
    if entry is None or not entry.image_url or not is_bare_name(terrain):
        return None
    for ext in CLEARED_IMAGE_EXTS:
        path = _cleared_path(terrain, ext)
        if path.is_file():
            return path
    return None


def cleared_image(terrain: str) -> Path | None:
    """The image of a cleared map, downloaded from its fixed URL on first use.

    ``None`` for a map with no cleared image, or while a failed download
    waits `CLEARED_FAILURE_BACKOFF`. The URL comes only from
    `registry.CLEARED_MAPS`, the download is capped at
    `MAX_CLEARED_IMAGE_BYTES`, and a body that is not a WebP or JPEG is not kept.
    """
    from app.maps.registry import cleared_map

    cached = cleared_image_cached(terrain)
    if cached is not None:
        return cached
    entry = cleared_map(terrain)
    if entry is None or not entry.image_url or not is_bare_name(terrain):
        return None
    with _cleared_lock:
        cached = cleared_image_cached(terrain)
        if cached is not None:
            return cached
        failed_at = _cleared_failed.get(terrain.lower())
        if failed_at is not None and time.monotonic() - failed_at < CLEARED_FAILURE_BACKOFF:
            return None
        part = _cleared_path(terrain, "part")
        try:
            part.parent.mkdir(parents=True, exist_ok=True)
            log.info("Downloading the map image for %s from %s", terrain, entry.image_url)
            fetch_capped(entry.image_url, part, MAX_CLEARED_IMAGE_BYTES, f"The map image for {terrain}")
            with open(part, "rb") as fh:
                ext = image_type(fh.read(16))
            if ext not in CLEARED_IMAGE_EXTS:
                raise ValueError("the download is not a WebP or JPEG image")
            dest = _cleared_path(terrain, ext)
            part.replace(dest)
        except Exception as exc:  # noqa: BLE001 - any failure means "no image yet"
            part.unlink(missing_ok=True)
            _cleared_failed[terrain.lower()] = time.monotonic()
            log.warning(
                "Could not download the map image for %s: %s (next try in %d min)",
                terrain, exc, CLEARED_FAILURE_BACKOFF // 60,
            )
            return None
        _cleared_failed.pop(terrain.lower(), None)
        log.info("Saved the map image for %s to %s", terrain, dest)
        return dest
