"""A community map's height grid from its maker, downloaded from zedstead.com.

KubeloLive gave Zedstead Banov's heightmap as an ESRI ASCII grid (plans/65,
2026-11-06). `registry.CLEARED_MAPS` names its fixed URL (`heights_url`); the
file is a gzip of the maker's ``.asc``. It replaces the world file as the
heights' source, so the server's PBO is never opened for them.

The download is untrusted input like a world file: it is capped before and
after it is unpacked, every number is checked, and a grid no real map has
raises `ValueError`.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import math
import threading
import time
from array import array
from pathlib import Path

from app.maps.registry import maker_heights_url
from app.shared import paths
from app.shared.json_utils import atomic_write_bytes, atomic_write_text

log = logging.getLogger(__name__)

#: The largest download and the largest unpacked grid accepted. Banov's
#: grid is 29 MB as text.
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
MAX_TEXT_BYTES = 96 * 1024 * 1024
#: The largest side accepted; real grids are 2048 or 4096.
MAX_GRID_SIDE = 8192
#: How long a failed download or build waits before the next try.
FAILURE_BACKOFF = 3600

_HEADER_KEYS = ("ncols", "nrows", "xllcorner", "yllcorner", "cellsize")
_failed: dict[str, float] = {}
_lock = threading.Lock()


def parse_esri_ascii(text: str):
    """An ESRI ASCII grid as `terrain_map.Heights`, row 0 south.

    The file's first data row is the north edge. The corner offsets are
    ignored: Terrain Builder writes them, and the map starts at 0, 0.
    Raises `ValueError` for a grid that is not square, too large, short,
    or holds a value that is not a finite height or the no-data value.
    """
    from app.maps.terrain_map import Heights

    tokens = text.split()
    header: dict[str, str] = {}
    pos = 0
    while pos + 1 < len(tokens) and tokens[pos].lower() in (*_HEADER_KEYS, "xllcenter", "yllcenter", "nodata_value"):
        header[tokens[pos].lower()] = tokens[pos + 1]
        pos += 2
    try:
        cols, rows = int(header["ncols"]), int(header["nrows"])
        cell = float(header["cellsize"])
    except (KeyError, ValueError) as exc:
        raise ValueError(f"not an ESRI ASCII grid header: {exc}") from exc
    nodata = float(header.get("nodata_value", "-9999"))
    if cols != rows or not 2 <= cols <= MAX_GRID_SIDE:
        raise ValueError(f"a {cols} x {rows} grid")
    if not math.isfinite(cell) or not 0.5 <= cell <= 100:
        raise ValueError(f"a cell size of {cell} m")
    if len(tokens) - pos < cols * rows:
        raise ValueError(f"{len(tokens) - pos} values for a {cols} x {rows} grid")
    values = array("f", bytes(4 * cols * rows))
    for r in range(rows):
        dest = (rows - 1 - r) * cols  # file row 0 is north
        for c in range(cols):
            v = float(tokens[pos + r * cols + c])
            if v == nodata:
                v = 0.0
            if not math.isfinite(v):
                raise ValueError(f"a height of {v} m")
            values[dest + c] = v
    return Heights(side=cols, cell=cell, values=values)


def _cache_files(terrain: str) -> tuple[Path, Path]:
    """The cached height file of *terrain* and its key, beside the world-file builds."""
    folder = paths.maps_dir() / "terrain"
    name = f"{terrain.lower()}.maker.heights"
    return folder / f"{name}.gz", folder / f"{name}.json"


def _unpacked(raw: bytes) -> bytes:
    """*raw* unpacked when it is gzip, capped at `MAX_TEXT_BYTES`.

    A host may already have undone the gzip (Content-Encoding), so plain
    text is taken as it is.
    """
    if raw[:2] != b"\x1f\x8b":
        return raw
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as fh:
        text = fh.read(MAX_TEXT_BYTES + 1)
    if len(text) > MAX_TEXT_BYTES:
        raise ValueError("the unpacked height grid is too large")
    return text


def maker_heights(terrain: str) -> Path | None:
    """The gzipped `encode_heights` file built from *terrain*'s maker grid.

    Downloaded and built on first use, then served from the cache until the
    URL or the encoding changes. ``None`` for a map without a maker grid, or
    for `FAILURE_BACKOFF` after a failed download or build.
    """
    from app.maps.map_data import fetch_capped
    from app.maps.terrain_map import FORMAT_VERSION, encode_heights

    url = maker_heights_url(terrain)
    if not url:
        return None
    gz, meta = _cache_files(terrain)
    key = {"url": url, "version": FORMAT_VERSION}

    def cached() -> bool:
        try:
            return gz.is_file() and json.loads(meta.read_text()) == key
        except (OSError, ValueError):
            return False

    if cached():
        return gz
    with _lock:
        if cached():
            return gz
        failed_at = _failed.get(terrain.lower())
        if failed_at is not None and time.monotonic() - failed_at < FAILURE_BACKOFF:
            return None
        part = gz.with_name(gz.name + ".download")
        try:
            gz.parent.mkdir(parents=True, exist_ok=True)
            log.info("Downloading the height grid for %s from %s", terrain, url)
            fetch_capped(url, part, MAX_DOWNLOAD_BYTES, f"The height grid for {terrain}")
            text = _unpacked(part.read_bytes()).decode("ascii")
            body = encode_heights(parse_esri_ascii(text))
        except Exception as exc:  # noqa: BLE001 - a download fails in many ways
            _failed[terrain.lower()] = time.monotonic()
            log.warning(
                "No height grid for %s: %s (next try in %d min)", terrain, exc, FAILURE_BACKOFF // 60
            )
            return None
        finally:
            part.unlink(missing_ok=True)
        _failed.pop(terrain.lower(), None)
        # The key goes last, so a key that matches always has its file.
        atomic_write_bytes(gz, gzip.compress(body, 6, mtime=0))
        atomic_write_text(meta, json.dumps(key))
        return gz
