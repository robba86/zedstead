"""The height grid a map's maker gave Zedstead (app/maps/maker_heights.py).

Invented grids only: never the maker's file.
"""

import gzip
import struct

import pytest

from app.maps import maker_heights, map_data, registry
from app.maps.maker_heights import parse_esri_ascii

GRID_URL = "https://zedstead.com/maps/banov.asc.gz"


@pytest.fixture
def banov_grid(monkeypatch):
    """Banov with a hosted maker grid. Not the shipped setting: the grid is
    not hosted until KubeloLive agrees to a public download (registry.py)."""
    cleared = registry.ClearedMap(registry._KUBELO, heights_url=GRID_URL, roads=True)
    monkeypatch.setitem(registry.CLEARED_MAPS, "banov", cleared)
    monkeypatch.setattr(maker_heights, "_failed", {})


def _asc(rows, cell=7.5, nodata=-9999):
    """An invented ESRI ASCII grid; *rows* is north first, as in the file."""
    n = len(rows)
    head = f"ncols {n}\nnrows {n}\nxllcorner 200000.0\nyllcorner 0.0\ncellsize {cell}\nNODATA_value {nodata}\n"
    return head + "\n".join(" ".join(str(v) for v in row) for row in rows) + "\n"


def test_the_grid_is_flipped_so_row_zero_is_south():
    h = parse_esri_ascii(_asc([[10, 11], [-2, 3]]))
    assert (h.side, h.cell) == (2, 7.5)
    assert list(h.values) == [-2.0, 3.0, 10.0, 11.0]


def test_no_data_is_sea_level():
    assert list(parse_esri_ascii(_asc([[-9999, 1], [2, 3]])).values)[2] == 0.0


@pytest.mark.parametrize(
    "text",
    [
        "ncols 2\nnrows 3\ncellsize 7.5\n1 2 3 4 5 6\n",  # not square
        "ncols 2\nnrows 2\ncellsize 7.5\n1 2 3\n",  # short
        "ncols 2\nnrows 2\ncellsize 0\n1 2 3 4\n",  # no cell size
        "ncols 2\nnrows 2\ncellsize 7.5\n1 nan 3 4\n",  # not finite
        "ncols 99999\nnrows 99999\ncellsize 7.5\n",  # too large
        "1 2 3 4\n",  # no header
    ],
)
def test_a_grid_no_real_map_has_is_refused(text):
    with pytest.raises(ValueError):
        parse_esri_ascii(text)


def test_banov_grid_is_downloaded_once_and_cached(monkeypatch, banov_grid):
    asked = []
    body = gzip.compress(_asc([[5, 6], [7, 8]]).encode())

    def fake_fetch(url, dest, max_bytes, what):
        asked.append(url)
        dest.write_bytes(body)
        return len(body)

    monkeypatch.setattr(map_data, "fetch_capped", fake_fetch)
    first = maker_heights.maker_heights("banov")
    second = maker_heights.maker_heights("banov")
    assert first == second and first.is_file()
    assert asked == [GRID_URL]
    data = gzip.decompress(first.read_bytes())
    assert data[:4] == b"DZTH" and struct.unpack_from("<I", data, 4)[0] == 2


def test_a_host_that_unpacked_the_gzip_still_works(monkeypatch, banov_grid):
    monkeypatch.setattr(
        map_data, "fetch_capped", lambda url, dest, m, w: dest.write_text(_asc([[1, 2], [3, 4]]))
    )
    assert maker_heights.maker_heights("banov") is not None


def test_a_failed_download_waits_before_the_next_try(monkeypatch, banov_grid):
    calls = []

    def broken(*a, **k):
        calls.append(1)
        raise OSError("offline")

    monkeypatch.setattr(map_data, "fetch_capped", broken)
    assert maker_heights.maker_heights("banov") is None
    assert maker_heights.maker_heights("banov") is None
    assert calls == [1]
    assert not list((maker_heights.paths.maps_dir() / "terrain").glob("banov.*"))


def test_a_map_without_a_maker_grid_downloads_nothing(monkeypatch):
    monkeypatch.setattr(map_data, "fetch_capped", lambda *a, **k: pytest.fail("downloaded"))
    assert maker_heights.maker_heights("banov") is None  # not hosted (registry.py)
    assert maker_heights.maker_heights("banovfrost") is None
    assert maker_heights.maker_heights("chernarusplus") is None
