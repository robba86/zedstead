"""The admin's own map image (`app/maps/map_data.py`, plans/69 Step 4).

No map image ships and none is downloaded, so this is the only image there is.
"""

from __future__ import annotations

import pytest

from app.maps import map_data


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(map_data, "maps_dir", lambda: tmp_path / "maps")


def test_a_terrain_that_names_a_path_is_refused(tmp_path):
    """`/api/map-editor/map-image` and `/api/maps/thumbnail` hand their
    `?terrain=` to `own_image`; a path in it must not reach a file outside the
    image dir (plans/57 E1)."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.png").write_bytes(b"not a map")

    for terrain in ("../outside/secret", str(outside / "secret")):
        assert map_data.own_image(terrain) is None, terrain


# ── the admin's own image (plans/69 Step 4) ────────────────────────────

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 16


@pytest.mark.parametrize(
    "data, kind",
    [(PNG, "png"), (JPEG, "jpg"), (WEBP, "webp"), (b"GIF89a", None), (b"<svg/>", None), (b"", None)],
)
def test_an_images_type_is_read_from_its_bytes(data, kind):
    assert map_data.image_type(data) == kind


def test_a_new_own_image_replaces_the_old_one_whatever_its_type(tmp_path):
    map_data.save_own_image("enoch", JPEG)
    png = map_data.save_own_image("enoch", PNG)
    assert sorted(p.name for p in (tmp_path / "maps" / "own").iterdir()) == ["enoch.png"]
    assert map_data.own_image("enoch") == png


@pytest.mark.parametrize(
    "terrain, data, message",
    [
        ("../outside", PNG, "not a map name"),
        ("", PNG, "not a map name"),
        ("enoch", b"<html>not an image</html>", "not a JPEG, PNG or WebP"),
    ],
)
def test_a_bad_name_or_a_file_that_is_not_an_image_is_refused(tmp_path, terrain, data, message):
    with pytest.raises(ValueError, match=message):
        map_data.save_own_image(terrain, data)
    assert not (tmp_path / "maps" / "own").exists() or not any((tmp_path / "maps" / "own").iterdir())


@pytest.mark.parametrize("terrain", ["a\\b", "a\x00b", "..", "."])
def test_a_name_the_shared_check_refuses_is_refused_here_too(tmp_path, terrain):
    """NEW-5: map_data had its own, weaker copy of the bare-name check, which
    took a backslash and a NUL byte; it uses `zip_utils.is_bare_name` now."""
    with pytest.raises(ValueError, match="not a map name"):
        map_data.save_own_image(terrain, PNG)
    assert map_data.own_image(terrain) is None
    assert not (tmp_path / "maps" / "own").exists() or not any((tmp_path / "maps" / "own").iterdir())


def test_an_image_past_the_cap_is_refused(monkeypatch):
    monkeypatch.setattr(map_data, "MAX_OWN_IMAGE_BYTES", 16)
    with pytest.raises(ValueError, match="larger than"):
        map_data.save_own_image("enoch", PNG)
    assert map_data.own_image("enoch") is None


def test_removing_the_own_image(tmp_path):
    map_data.save_own_image("enoch", PNG)
    assert map_data.remove_own_image("enoch") is True
    assert map_data.remove_own_image("enoch") is False
    assert map_data.own_image("enoch") is None


JPEG_THUMB = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def test_a_preview_needs_the_image_and_goes_with_it(tmp_path):
    with pytest.raises(ValueError, match="no image of your own"):
        map_data.save_own_thumbnail("enoch", JPEG_THUMB)
    map_data.save_own_image("enoch", PNG)
    thumb = map_data.save_own_thumbnail("enoch", JPEG_THUMB)
    assert map_data.own_thumbnail("enoch") == thumb
    # A new image makes the old preview wrong, and a removal takes it too.
    map_data.save_own_image("enoch", PNG)
    assert map_data.own_thumbnail("enoch") is None
    map_data.save_own_thumbnail("enoch", JPEG_THUMB)
    map_data.remove_own_image("enoch")
    assert map_data.own_thumbnail("enoch") is None


def test_a_preview_is_not_an_own_image_of_another_terrain(tmp_path):
    """Previews live in a folder of their own, so no terrain name reaches one."""
    map_data.save_own_image("enoch", PNG)
    map_data.save_own_thumbnail("enoch", JPEG_THUMB)
    assert map_data.own_image("enoch.thumb") is None
    assert map_data.own_image("thumbs") is None


@pytest.mark.parametrize(
    "terrain, data, message",
    [
        ("../outside", JPEG_THUMB, "not a map name"),
        ("enoch", PNG, "not a JPEG"),
    ],
)
def test_a_bad_preview_is_refused(terrain, data, message):
    map_data.save_own_image("enoch", PNG)
    with pytest.raises(ValueError, match=message):
        map_data.save_own_thumbnail(terrain, data)
    assert map_data.own_thumbnail("enoch") is None


def test_a_preview_past_the_cap_is_refused(monkeypatch):
    map_data.save_own_image("enoch", PNG)
    monkeypatch.setattr(map_data, "MAX_OWN_THUMBNAIL_BYTES", 16)
    with pytest.raises(ValueError, match="larger than"):
        map_data.save_own_thumbnail("enoch", JPEG_THUMB)
