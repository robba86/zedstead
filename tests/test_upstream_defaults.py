"""Files a mod of TheDmitri's stopped shipping, put back from his GitHub repository.

`upstream_defaults.restore_missing_mod_files` runs after every copy of a mod
into the server dir. Every file here is invented; no request leaves the test.
"""

from __future__ import annotations

from urllib.parse import unquote

from app.modmgr.recommended_mods import TOXICZONE as TOXICZONE_ID
from app.traderplus import upstream_defaults as up

TYPES = "Xmls & Trader & Tools/types.xml"
TRADER = "Xmls & Trader & Tools/Trader.txt"


def _fake_toxiczone(monkeypatch, files: dict[str, str]) -> list[str]:
    """Serve *files* (repo path -> text) as TheDmitri/ToxicZone; returns the paths asked."""
    asked: list[str] = []

    def _get(url, dest, max_bytes):
        assert url.startswith(up.TOXICZONE.raw_base), url
        path = unquote(url[len(up.TOXICZONE.raw_base):])
        asked.append(path)
        if path not in files:
            raise up.UpstreamError(f"Could not download {url}: 404")
        dest.write_text(files[path])

    monkeypatch.setattr(up, "_get", _get)
    return asked


def test_a_mod_that_still_ships_its_files_is_never_touched(tmp_path, monkeypatch):
    mod = tmp_path / "@ToxicZone"
    (mod / "Moved").mkdir(parents=True)
    (mod / "Moved" / "TYPES.XML").write_text("<types/>")
    (mod / "Moved" / "trader.txt").write_text("mine")
    asked = _fake_toxiczone(monkeypatch, {})

    assert up.restore_missing_mod_files(mod, TOXICZONE_ID) == []
    assert asked == [], "a file of that name anywhere in the mod counts"


def test_missing_files_come_back_at_their_repo_path(tmp_path, monkeypatch):
    mod = tmp_path / "@ToxicZone"
    (mod / "Addons").mkdir(parents=True)
    _fake_toxiczone(monkeypatch, {TYPES: "<types><type name=\"InventedGasMask\"/></types>", TRADER: "rows"})
    lines: list[str] = []

    assert up.restore_missing_mod_files(mod, TOXICZONE_ID, log=lines.append) == [TYPES, TRADER]
    assert "InventedGasMask" in (mod / "Xmls & Trader & Tools" / "types.xml").read_text()
    assert (mod / "Xmls & Trader & Tools" / "Trader.txt").read_text() == "rows"
    assert all("github.com/TheDmitri/ToxicZone" in line for line in lines)


def test_a_failed_download_is_logged_and_leaves_no_partial_file(tmp_path, monkeypatch):
    mod = tmp_path / "@ToxicZone"
    (mod / "Addons").mkdir(parents=True)
    _fake_toxiczone(monkeypatch, {TRADER: "rows"})
    lines: list[str] = []

    assert up.restore_missing_mod_files(mod, TOXICZONE_ID, log=lines.append) == [TRADER]
    assert not (mod / "Xmls & Trader & Tools" / "types.xml").exists()
    assert any("Warning" in line and "types.xml" in line for line in lines)


def test_a_mod_without_a_fallback_is_skipped(tmp_path, monkeypatch):
    mod = tmp_path / "@RadZone"
    mod.mkdir()
    asked = _fake_toxiczone(monkeypatch, {})
    assert up.restore_missing_mod_files(mod, "2294323732") == []
    assert asked == []


def test_copy_mod_to_server_puts_the_files_back(sample_config, monkeypatch, tmp_path):
    """The hook sits in the one copy every install, update and map switch runs."""
    from app.ops import fileops

    sample_config.workshop_content_dir = str(tmp_path / "workshop" / "content" / "221100")
    source = sample_config.workshop_dir / TOXICZONE_ID
    (source / "Addons").mkdir(parents=True)
    (source / "Addons" / "ToxicZone.pbo").write_text("pbo")
    monkeypatch.setattr("app.shared.steam.is_mod_downloaded", lambda config, mod_id: True)
    _fake_toxiczone(monkeypatch, {TYPES: "<types/>", TRADER: "rows"})

    assert fileops.copy_mod_to_server(sample_config, "@ToxicZone", {"@ToxicZone": TOXICZONE_ID})
    dest = sample_config.server_dir_path / "@ToxicZone"
    assert (dest / "Xmls & Trader & Tools" / "types.xml").is_file()
    assert not (source / "Xmls & Trader & Tools").exists(), "the Workshop cache is never written"
