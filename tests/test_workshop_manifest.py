"""A deleted workshop mod must also leave SteamCMD's manifest.

Removing a workshop item's folder without dropping it from
``appworkshop_221100.acf`` leaves SteamCMD believing the item is installed. It
then tries to reuse that item's chunks on the *next* download, hits the missing
files, and aborts the whole transfer with ``Missing game files`` — surfacing as
a bare ``ERROR! Download item <id> failed (Failure).`` while SteamCMD still
exits 0. One removed mod therefore breaks every later workshop download until
the manifest is repaired.
"""
from __future__ import annotations

import pytest

from app.ops import cleanup_ops
from app.parsers.acf_parser import (
    dump_acf,
    get_installed_timestamps,
    parse_acf,
    parse_acf_file,
    remove_workshop_items,
)
from app.shared.steam import download_mod_async, is_mod_downloaded

KEPT_ID = "111111"
STALE_ID = "222222"

ACF_TEXT = """"AppWorkshop"
{
\t"appid"\t\t"221100"
\t"SizeOnDisk"\t\t"4079464"
\t"WorkshopItemsInstalled"
\t{
\t\t"111111"
\t\t{
\t\t\t"size"\t\t"100"
\t\t\t"timeupdated"\t\t"1700000000"
\t\t\t"manifest"\t\t"412011409904253888"
\t\t}
\t\t"222222"
\t\t{
\t\t\t"size"\t\t"200"
\t\t\t"timeupdated"\t\t"1700000001"
\t\t\t"manifest"\t\t"412011409904253889"
\t\t}
\t}
\t"WorkshopItemDetails"
\t{
\t\t"111111"
\t\t{
\t\t\t"manifest"\t\t"412011409904253888"
\t\t\t"timeupdated"\t\t"1700000000"
\t\t}
\t\t"222222"
\t\t{
\t\t\t"manifest"\t\t"412011409904253889"
\t\t\t"timeupdated"\t\t"1700000001"
\t\t}
\t}
}
"""


@pytest.fixture()
def workshop_config(sample_config, tmp_path):
    # workshop_content_dir MUST be pinned — DayzConfig.workshop_dir otherwise
    # auto-detects the host's real Steam install and these tests delete under it.
    sample_config.steam_username = "someuser"
    sample_config.workshop_content_dir = str(tmp_path / "Steam" / "steamapps" / "workshop" / "content" / "221100")
    sample_config.workshop_dir.mkdir(parents=True, exist_ok=True)
    sample_config.workshop_acf_path.write_text(ACF_TEXT, encoding="utf-8")
    return sample_config


def _make_item(config, mod_id: str, *, populated: bool = True):
    item = config.workshop_dir / mod_id
    (item / "Addons").mkdir(parents=True, exist_ok=True)
    (item / "Keys").mkdir(parents=True, exist_ok=True)
    if populated:
        (item / "Addons" / "mod.pbo").write_bytes(b"pbo")
    return item


# --- ACF path ---------------------------------------------------------------


def test_workshop_acf_path_sits_beside_the_content_dir(workshop_config):
    # …/steamapps/workshop/appworkshop_221100.acf, i.e. two levels above
    # …/workshop/content/221100 — not one.
    assert workshop_config.workshop_acf_path.parent == workshop_config.workshop_dir.parents[1]
    assert workshop_config.workshop_acf_path.name == "appworkshop_221100.acf"


# --- ACF read/write ---------------------------------------------------------


def test_dump_acf_round_trips():
    data = parse_acf(ACF_TEXT)
    assert parse_acf(dump_acf(data)) == data


@pytest.mark.parametrize(
    "text",
    [
        # Cut off inside the quote that opens a section's block.
        '"AppWorkshop"\n{\n\t"appid"\t\t"221100"\n\t"WorkshopItemsInstalled"\t"',
        '"AppWorkshop"\t\t"221100"\n',
    ],
)
def test_a_manifest_cut_off_mid_write_has_no_timestamps(text):
    assert get_installed_timestamps(parse_acf(text)) == {}


def test_remove_workshop_items_drops_only_the_named_ids(workshop_config):
    assert remove_workshop_items(workshop_config.workshop_acf_path, [STALE_ID]) is True

    app = parse_acf_file(workshop_config.workshop_acf_path)["AppWorkshop"]
    assert set(app["WorkshopItemsInstalled"]) == {KEPT_ID}
    assert set(app["WorkshopItemDetails"]) == {KEPT_ID}
    assert app["appid"] == "221100"


def test_remove_workshop_items_writes_the_manifest_atomically(workshop_config, monkeypatch):
    """A kill mid-write must leave SteamCMD the old manifest, never half of one."""
    import os

    from app.parsers import acf_parser

    acf = workshop_config.workshop_acf_path
    os.chmod(acf, 0o644)
    writes = []
    real = acf_parser.atomic_write_text
    monkeypatch.setattr(acf_parser, "atomic_write_text", lambda path, text, **kw: (writes.append(path), real(path, text, **kw)))
    assert remove_workshop_items(acf, [STALE_ID]) is True
    assert writes == [acf]
    assert set(parse_acf_file(acf)["AppWorkshop"]["WorkshopItemsInstalled"]) == {KEPT_ID}
    assert acf.stat().st_mode & 0o777 == 0o644


def test_remove_workshop_items_is_a_noop_for_unknown_ids(workshop_config):
    before = workshop_config.workshop_acf_path.read_text(encoding="utf-8")
    assert remove_workshop_items(workshop_config.workshop_acf_path, ["999999"]) is False
    assert workshop_config.workshop_acf_path.read_text(encoding="utf-8") == before


def test_remove_workshop_items_deletes_an_unparseable_manifest(workshop_config):
    # Fallback: a manifest we can't rewrite is deleted so SteamCMD regenerates
    # it. Slower, but never leaves download-breaking stale entries behind.
    workshop_config.workshop_acf_path.write_text("not a vdf file at all", encoding="utf-8")
    assert remove_workshop_items(workshop_config.workshop_acf_path, [STALE_ID]) is True
    assert not workshop_config.workshop_acf_path.exists()


# --- removal + repair -------------------------------------------------------


def test_remove_workshop_mods_purges_the_manifest(workshop_config):
    _make_item(workshop_config, KEPT_ID)
    item = _make_item(workshop_config, STALE_ID)

    removed = cleanup_ops.remove_workshop_mods(workshop_config, [item])

    assert removed == 1
    assert not item.exists()
    installed = get_installed_timestamps(parse_acf_file(workshop_config.workshop_acf_path))
    assert set(installed) == {KEPT_ID}


def test_a_failed_removal_still_purges_what_was_removed(workshop_config, monkeypatch):
    """An rmtree that fails on the second item (a file held open, a
    permission) raised before the ACF was touched, so the first item, already
    deleted, stayed in the manifest and broke every later download. The item
    that failed half way goes from the manifest too: SteamCMD then downloads
    it again instead of reusing chunks it may have lost."""
    first = _make_item(workshop_config, STALE_ID)
    second = _make_item(workshop_config, KEPT_ID)
    real_rmtree = cleanup_ops.shutil.rmtree

    def rmtree(path, *args, **kwargs):
        if str(path) == str(second):
            raise PermissionError(13, "Permission denied", str(path))
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(cleanup_ops.shutil, "rmtree", rmtree)

    with pytest.raises(PermissionError):
        cleanup_ops.remove_workshop_mods(workshop_config, [first, second])

    assert not first.exists()
    installed = get_installed_timestamps(parse_acf_file(workshop_config.workshop_acf_path))
    assert installed == {}


def test_find_stale_entries_flags_missing_and_hollow_items(workshop_config):
    _make_item(workshop_config, KEPT_ID)
    _make_item(workshop_config, STALE_ID, populated=False)  # Addons/ + Keys/, no files

    assert cleanup_ops.find_stale_workshop_entries(workshop_config) == [STALE_ID]


def test_repair_workshop_manifest_removes_stale_entries(workshop_config):
    _make_item(workshop_config, KEPT_ID)

    removed = cleanup_ops.repair_workshop_manifest(workshop_config)

    assert removed == [STALE_ID]
    installed = get_installed_timestamps(parse_acf_file(workshop_config.workshop_acf_path))
    assert set(installed) == {KEPT_ID}


def test_repair_is_a_noop_when_everything_is_present(workshop_config):
    _make_item(workshop_config, KEPT_ID)
    _make_item(workshop_config, STALE_ID)

    assert cleanup_ops.repair_workshop_manifest(workshop_config) == []


# --- hollow item dirs -------------------------------------------------------


def test_is_mod_downloaded_rejects_the_bare_directory_skeleton(workshop_config):
    # What a failed download leaves: Addons/ and Keys/ exist but are empty.
    # "any entry in the item dir" would call this a good download.
    _make_item(workshop_config, STALE_ID, populated=False)
    assert is_mod_downloaded(workshop_config, STALE_ID) is False


# --- auto-repair between download attempts ----------------------------------


class _RepairingRunner:
    """SteamCMD that keeps exiting 0 with nothing downloaded until the
    manifest is repaired — the real failure mode this guards."""

    def __init__(self, config) -> None:
        self.config = config
        self.lines: list[str] = []
        self.calls = 0

    async def run_command(self, cmd: list[str]) -> int:
        self.calls += 1
        if not self.config.workshop_acf_path.is_file() or STALE_ID not in self.config.workshop_acf_path.read_text():
            _make_item(self.config, KEPT_ID)
        return 0

    def write(self, text: str) -> None:
        self.lines.append(text)


async def test_download_repairs_a_stale_manifest_and_retries(workshop_config, monkeypatch):
    monkeypatch.setattr("app.shared.steam.STEAM_RATE_LIMIT_SECONDS", 0)
    runner = _RepairingRunner(workshop_config)

    ok = await download_mod_async(runner, workshop_config, KEPT_ID, "@Kept", max_retries=3)

    assert ok is True
    assert runner.calls == 2  # first attempt fetched nothing, repair, then success
    assert any("Repaired SteamCMD's workshop manifest" in line for line in runner.lines)
