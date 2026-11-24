"""Tests for app.ops.fileops — destructive mod-removal guards."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.modmgr.mods import is_safe_mod_dir_name
from app.ops import fileops
from app.ops.fileops import copy_bikey_files, copy_mod_to_server, remove_mod_from_server
from tests.conftest import write_test_mods


class TestIsSafeModDirName:
    # "@Bob's Mod": a folder installed before titles were checked against the
    # launch command still counts as a mod folder.
    @pytest.mark.parametrize("name", ["@CF", "@Trader", "@a", "  @CF  ", "@Bob's Mod"])
    def test_valid_names(self, name):
        assert is_safe_mod_dir_name(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "",
            "   ",
            "CF",            # missing @ prefix
            "..",
            ".",
            "/etc",
            "@a/b",          # path separator
            "@a/..",         # parent escape
            "@a\\b",         # Windows separator
            "../@CF",
            "@CF\n",        # a name is one line of mods.txt and cached-mod-ids.txt
            "@A\n@B",
            "@A\rB",
            "@A\u2028B",    # str.splitlines() splits at these too
            "@A\x85B",
            "@A\tB",
        ],
    )
    def test_rejected_names(self, name):
        assert is_safe_mod_dir_name(name) is False


class TestRemoveModFromServer:
    def test_empty_name_does_not_wipe_server_dir(self, sample_config):
        # The catastrophic case: server_dir / "" == server_dir. An unguarded
        # rmtree would delete the entire server directory.
        server_dir = Path(sample_config.server_dir)
        marker = server_dir / "serverDZ.cfg"
        assert marker.is_file()

        remove_mod_from_server(sample_config, "")

        assert server_dir.is_dir()
        assert marker.is_file()

    def test_traversal_name_refused(self, sample_config):
        server_dir = Path(sample_config.server_dir)
        # A sibling dir outside the server that must never be touched.
        outside = server_dir.parent / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("important")

        remove_mod_from_server(sample_config, "../outside")

        assert (outside / "keep.txt").is_file()

    def test_valid_mod_is_removed(self, sample_config):
        server_dir = Path(sample_config.server_dir)
        mod_dir = server_dir / "@CF"
        (mod_dir / "addons").mkdir(parents=True)
        (mod_dir / "addons" / "cf.pbo").write_text("data")

        remove_mod_from_server(sample_config, "@CF")

        assert not mod_dir.exists()
        # Server dir and siblings untouched.
        assert (server_dir / "serverDZ.cfg").is_file()

    def test_a_key_another_mod_still_ships_stays(self, sample_config):
        """One author's mods often share a key: uninstalling one of them
        deleted the key the others are signed with, so they failed the
        signature check. Its own key still goes."""
        server_dir = Path(sample_config.server_dir)
        keys = sample_config.keys_dir
        keys.mkdir(exist_ok=True)
        for mod, key in (("@ModA", "Author.bikey"), ("@ModA", "ModA.bikey"), ("@ModB", "Author.bikey")):
            (server_dir / mod / "Keys").mkdir(parents=True, exist_ok=True)
            (server_dir / mod / "Keys" / key).write_text("key")
            (keys / key).write_text("key")

        remove_mod_from_server(sample_config, "@ModA")

        assert (keys / "Author.bikey").is_file()
        assert not (keys / "ModA.bikey").exists()

    def test_the_games_own_key_is_never_removed(self, sample_config):
        server_dir = Path(sample_config.server_dir)
        keys = sample_config.keys_dir
        keys.mkdir(exist_ok=True)
        (keys / "dayz.bikey").write_text("bohemia")
        (server_dir / "@Odd" / "Keys").mkdir(parents=True)
        (server_dir / "@Odd" / "Keys" / "dayz.bikey").write_text("copy")

        remove_mod_from_server(sample_config, "@Odd")

        assert (keys / "dayz.bikey").read_text() == "bohemia"

    def test_missing_valid_mod_is_noop(self, sample_config):
        server_dir = Path(sample_config.server_dir)
        # @Nonexistent isn't installed — should be a quiet no-op, no crash.
        remove_mod_from_server(sample_config, "@Nonexistent")
        assert server_dir.is_dir()


class TestCopyModToServer:
    """The copy runs workshop_dir / id -> server_dir / name. Both parts come
    from mods.txt and cached-mod-ids.txt, which a bundle import or the web
    can write, so each has to be one folder name."""

    @pytest.fixture()
    def config(self, sample_config, tmp_path):
        # A server dir two levels down, so a name that climbs out of it still
        # lands inside tmp_path.
        sample_config.server_dir = str(tmp_path / "box" / "server")
        sample_config.server_dir_path.mkdir(parents=True)
        # Pinned, or workshop_dir resolves to the host's real Steam cache.
        sample_config.workshop_content_dir = str(tmp_path / "workshop")
        item = sample_config.workshop_dir / "123"
        (item / "addons").mkdir(parents=True)
        (item / "addons" / "a.pbo").write_text("pbo")
        return sample_config

    def test_a_valid_mod_is_copied(self, config):
        assert copy_mod_to_server(config, "@Good", {"@Good": "123"}, log=lambda _s: None)
        assert (config.server_dir_path / "@Good" / "addons" / "a.pbo").is_file()

    @pytest.mark.parametrize("name", ["@../../escaped", "@../../../escaped", "../@x", "@a/b"])
    def test_a_name_that_is_not_one_folder_is_not_copied(self, config, name):
        assert not copy_mod_to_server(config, name, {name: "123"}, log=lambda _s: None)
        box = config.server_dir_path.parent
        assert [p.name for p in box.iterdir()] == ["server"]
        assert list(config.server_dir_path.iterdir()) == []

    @pytest.mark.parametrize("mod_id", ["../workshop/123", "123/addons"])
    def test_an_id_that_is_not_a_number_is_not_copied(self, config, mod_id):
        assert not copy_mod_to_server(config, "@Good", {"@Good": mod_id}, log=lambda _s: None)
        assert not (config.server_dir_path / "@Good").exists()

    @pytest.mark.parametrize("mod_id", ["１２３", "١٢٣"])
    def test_an_id_in_other_digits_is_not_copied(self, config, mod_id):
        """`str.isdigit` takes any script's digits; a Workshop ID is ASCII
        (`mods.is_workshop_id`), here even with a folder of that name."""
        item = config.workshop_dir / mod_id
        (item / "addons").mkdir(parents=True)
        (item / "addons" / "a.pbo").write_text("pbo")
        assert not copy_mod_to_server(config, "@Good", {"@Good": mod_id}, log=lambda _s: None)
        assert not (config.server_dir_path / "@Good").exists()

    def test_symlinks_in_the_workshop_item_are_left_out(self, config, tmp_path):
        # The item is the mod author's: a link in it must not copy a file from
        # elsewhere on the machine into the server dir, or recurse.
        secret = tmp_path / "secret.txt"
        secret.write_text("not the mod's")
        item = config.workshop_dir / "123"
        (item / "addons" / "config.txt").symlink_to(secret)
        (item / "loop").symlink_to(item, target_is_directory=True)
        (item / "dangling").symlink_to(tmp_path / "missing")
        lines: list[str] = []

        assert copy_mod_to_server(config, "@Good", {"@Good": "123"}, log=lines.append)
        dest = config.server_dir_path / "@Good"
        assert sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*")) == [
            "addons",
            "addons/a.pbo",
        ]
        assert any("3 symlink(s)" in line for line in lines)


class TestCopyBikeyFiles:
    """keys/ is rebuilt from the enabled mods' keys. A mod's tree is the mod
    author's, so an entry in it that is not a key file must not stop the copy,
    and nothing may leave keys/ with fewer keys than it had."""

    @pytest.fixture()
    def config(self, sample_config):
        server = sample_config.server_dir_path
        keys = sample_config.keys_dir
        keys.mkdir(parents=True, exist_ok=True)
        for name, data in (("dayz", b"dayz"), ("cf", b"old cf"), ("late", b"old late"), ("stale", b"stale")):
            (keys / f"{name}.bikey").write_bytes(data)
        for mod, key in (("@CF", "cf"), ("@Evil", "evil"), ("@Late", "late")):
            (server / mod / "keys").mkdir(parents=True)
            (server / mod / "keys" / f"{key}.bikey").write_bytes(f"new {key}".encode())
        write_test_mods(sample_config, ['@CF', '@Evil', '@Late'])
        return sample_config

    def _keys(self, config) -> dict[str, bytes | None]:
        return {
            p.name: p.read_bytes() if p.is_file() else None
            for p in sorted(config.keys_dir.iterdir())
        }

    def test_entries_that_are_not_key_files_are_skipped(self, config, tmp_path):
        evil = config.server_dir_path / "@Evil" / "keys"
        (evil / "trap.bikey").mkdir()
        secret = tmp_path / "secret.txt"
        secret.write_text("not a key")
        (evil / "link.bikey").symlink_to(secret)
        (config.keys_dir / "odd.bikey").mkdir()

        assert copy_bikey_files(config, log=lambda _s: None) == (3, 3)
        assert self._keys(config) == {
            "cf.bikey": b"new cf",
            "dayz.bikey": b"dayz",
            "evil.bikey": b"new evil",
            "late.bikey": b"new late",
            "odd.bikey": None,
        }

    def test_a_key_that_fails_to_copy_keeps_the_one_keys_had(self, config, monkeypatch):
        real_copy = fileops.shutil.copy2

        def copy2(src, dst, **kwargs):
            if Path(src).name == "late.bikey":
                raise PermissionError(13, "Permission denied")
            return real_copy(src, dst, **kwargs)

        monkeypatch.setattr(fileops.shutil, "copy2", copy2)
        lines: list[str] = []

        assert copy_bikey_files(config, log=lines.append) == (2, 2)
        assert self._keys(config) == {
            "cf.bikey": b"new cf",
            "dayz.bikey": b"dayz",
            "evil.bikey": b"new evil",
            "late.bikey": b"old late",
        }
        assert any("Could not copy @Late's late.bikey: Permission denied" in line for line in lines)

    def test_a_mods_dayz_bikey_never_replaces_the_games_own(self, config):
        """plans/68 CON-15: a mod's Keys/dayz.bikey replaced Bohemia's key on
        every run, so vanilla files failed the signature check."""
        (config.server_dir_path / "@Evil" / "keys" / "dayz.bikey").write_bytes(b"not bohemia")
        (config.server_dir_path / "@CF" / "keys" / "DayZ.bikey").write_bytes(b"nor this")

        for _run in range(2):
            lines: list[str] = []
            copy_bikey_files(config, log=lines.append)
            keys = self._keys(config)
            assert keys["dayz.bikey"] == b"dayz"
            assert "DayZ.bikey" not in keys
            # A normal mod's key still arrives.
            assert keys["evil.bikey"] == b"new evil" and keys["cf.bikey"] == b"new cf"
            assert any("Skipped @Evil's dayz.bikey" in line for line in lines)
            assert any("Skipped @CF's DayZ.bikey" in line for line in lines)

    def test_two_mods_with_different_keys_under_one_name_are_named(self, config):
        """plans/68 CON-15: @Late's shared.bikey replaced @CF's without a word."""
        (config.server_dir_path / "@CF" / "keys" / "shared.bikey").write_bytes(b"cf's shared")
        (config.server_dir_path / "@Late" / "keys" / "shared.bikey").write_bytes(b"late's shared")
        # The same key under the same name in two mods is no collision.
        (config.server_dir_path / "@CF" / "keys" / "author.bikey").write_bytes(b"one author")
        (config.server_dir_path / "@Evil" / "keys" / "author.bikey").write_bytes(b"one author")
        lines: list[str] = []

        copy_bikey_files(config, log=lines.append)

        collisions = [line for line in lines if "different contents" in line]
        assert len(collisions) == 1
        assert "@CF and @Late both ship a key named shared.bikey" in collisions[0]
        assert self._keys(config)["shared.bikey"] == b"late's shared"

    def test_a_key_name_holding_markup_is_printed_as_it_is(self, config):
        (config.server_dir_path / "@CF" / "keys" / "[red]k.bikey").write_bytes(b"a")
        (config.server_dir_path / "@Late" / "keys" / "[red]k.bikey").write_bytes(b"b")
        lines: list[str] = []
        copy_bikey_files(config, log=lines.append)
        assert any("a key named [red]k.bikey" in line for line in lines)

    def test_a_caller_s_log_gets_no_style_tags(self, config):
        """The update pipeline and the job log print a line as it is, so the
        `[dim]`/`[green]` the console renders must not reach them."""
        lines: list[str] = []
        copy_bikey_files(config, log=lines.append)
        assert lines
        assert not [line for line in lines if re.search(r"\[/?(dim|green|cyan|yellow|red|bold)\]", line)]


# --- local mods (plans/78 D1) -------------------------------------------------


def _local(config, name: str) -> None:
    from app.modmgr import modlist

    def change(ml) -> None:
        ml.add(name)
        for m in ml.mods:
            if m.name == name:
                m.local = True

    modlist.update(config, change)


def test_a_local_mod_folder_is_never_removed_or_stashed(sample_config):
    from app.ops import mod_rollback

    server = sample_config.server_dir_path
    for name in ("@Mine", "@CF"):
        (server / name / "addons").mkdir(parents=True)
    _local(sample_config, "@Mine")

    fileops.remove_server_mods(sample_config, log=lambda s: None)

    assert (server / "@Mine" / "addons").is_dir()
    assert not (server / "@CF").exists()
    assert mod_rollback.rollback_state(sample_config)["mods"] == ["@CF"]


def test_a_keyless_local_mod_keeps_the_keys_no_listed_mod_ships(sample_config):
    server = sample_config.server_dir_path
    (server / "@Mine" / "addons").mkdir(parents=True)
    _local(sample_config, "@Mine")
    keys = sample_config.keys_dir
    keys.mkdir(exist_ok=True)
    (keys / "mine.bikey").write_text("k")
    lines: list[str] = []

    fileops.copy_bikey_files(sample_config, log=lines.append)

    assert (keys / "mine.bikey").exists()
    assert any("@Mine" in l and "Kept 1 key" in l for l in lines)


def test_a_local_mod_with_its_own_key_lets_stale_keys_go(sample_config):
    server = sample_config.server_dir_path
    (server / "@Mine" / "keys").mkdir(parents=True)
    (server / "@Mine" / "keys" / "mine.bikey").write_text("k")
    _local(sample_config, "@Mine")
    keys = sample_config.keys_dir
    keys.mkdir(exist_ok=True)
    (keys / "stale.bikey").write_text("old")

    fileops.copy_bikey_files(sample_config, log=lambda s: None)

    assert (keys / "mine.bikey").exists()
    assert not (keys / "stale.bikey").exists()
