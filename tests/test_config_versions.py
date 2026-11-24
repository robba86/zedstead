"""Tests for cli.config_versions — snapshot, restore, diff, rotation."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _patch_config_dir(tmp_path: Path, monkeypatch):
    """Put the config dir, and so the pre-instances versions store, in tmp."""
    monkeypatch.setattr("app.shared.paths.CONFIG_DIR", tmp_path / "config")


def _store() -> Path:
    """The store a file no instance owns goes to — every file in these tests."""
    from app.shared.paths import InstancePaths, instance_root

    return InstancePaths(instance_root("")).versions_dir


def _save(path: Path, text: str, encoding: str = "utf-8") -> None:
    """A write as Zedstead makes it (atomic_write_text), so config_versions
    records it and never reads it as a change made outside Zedstead."""
    from app.shared.json_utils import atomic_write_text

    atomic_write_text(path, text)


@pytest.fixture()
def xml_file(tmp_path: Path) -> Path:
    """A sample config file to version."""
    p = tmp_path / "types.xml"
    p.write_text("<types>\n  <type name='AKM'/>\n</types>\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------


class TestSnapshot:
    def test_creates_version(self, xml_file: Path):
        from app.shared.config_versions import load_index, snapshot

        entry = snapshot(xml_file, trigger="manual", note="initial")
        assert entry is not None
        assert entry.version == 1
        assert entry.trigger == "manual"
        assert entry.note == "initial"
        assert entry.size_bytes > 0
        assert len(entry.sha256) == 64

        idx = load_index(_store())
        assert len(idx.entries) == 1

    def test_skips_unchanged(self, xml_file: Path):
        from app.shared.config_versions import snapshot

        first = snapshot(xml_file, trigger="manual")
        assert first is not None
        second = snapshot(xml_file, trigger="manual")
        assert second is None  # unchanged — deduped

    def test_creates_new_version_on_change(self, xml_file: Path):
        from app.shared.config_versions import load_index, snapshot

        snapshot(xml_file, trigger="manual")
        _save(xml_file, "<types>\n  <type name='M4A1'/>\n</types>\n", encoding="utf-8")
        entry = snapshot(xml_file, trigger="editor")
        assert entry is not None
        assert entry.version == 2

        idx = load_index(_store())
        assert len(idx.entries) == 2

    def test_rotation(self, xml_file: Path):
        from app.shared.config_versions import load_index, snapshot

        for i in range(5):
            _save(xml_file, f"<v>{i}</v>\n", encoding="utf-8")
            snapshot(xml_file, trigger="auto_save", max_versions=3)

        idx = load_index(_store())
        versions = idx.get_versions("types.xml")
        assert len(versions) == 3
        assert versions[0].version == 5  # newest
        assert versions[-1].version == 3  # oldest kept

    def test_nonexistent_file_returns_none(self, tmp_path: Path):
        from app.shared.config_versions import snapshot

        fake = tmp_path / "nonexistent.xml"
        assert snapshot(fake) is None

    def test_multiple_files(self, tmp_path: Path):
        from app.shared.config_versions import load_index, snapshot

        a = tmp_path / "types.xml"
        b = tmp_path / "events.xml"
        _save(a, "<types/>", encoding="utf-8")
        _save(b, "<events/>", encoding="utf-8")

        snapshot(a)
        snapshot(b)

        idx = load_index(_store())
        assert len(idx.versioned_files()) == 2

    def test_same_name_different_dirs(self, tmp_path: Path):
        """Files with the same basename in different dirs get independent histories."""
        from app.shared.config_versions import load_index, snapshot

        dir_a = tmp_path / "mission"
        dir_b = tmp_path / "mission" / "db"
        dir_a.mkdir()
        dir_b.mkdir()

        a = dir_a / "types.xml"
        b = dir_b / "types.xml"
        _save(a, "<root/>", encoding="utf-8")
        _save(b, "<db/>", encoding="utf-8")

        snapshot(a, trigger="manual")
        snapshot(b, trigger="manual")

        idx = load_index(_store())
        # Both share file_name="types.xml" but have different source_paths
        all_versions = idx.get_versions("types.xml")
        assert len(all_versions) == 2

        # Scoped lookups return only the matching file
        a_versions = idx.get_versions("types.xml", source_path=str(a))
        b_versions = idx.get_versions("types.xml", source_path=str(b))
        assert len(a_versions) == 1
        assert len(b_versions) == 1
        assert a_versions[0].source_path == str(a)
        assert b_versions[0].source_path == str(b)

    def test_same_name_dedup_is_scoped(self, tmp_path: Path):
        """Dedup check doesn't cross-match files with the same name."""
        from app.shared.config_versions import snapshot

        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        a = dir_a / "config.xml"
        b = dir_b / "config.xml"
        _save(a, "<a/>", encoding="utf-8")
        _save(b, "<a/>", encoding="utf-8")  # same content

        entry_a = snapshot(a)
        entry_b = snapshot(b)
        # Both should snapshot despite identical content — they are different files
        assert entry_a is not None
        assert entry_b is not None


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------


class TestRestore:
    def test_restore_to_previous(self, xml_file: Path):
        from app.shared.config_versions import restore, snapshot

        snapshot(xml_file, trigger="manual")
        original = xml_file.read_text(encoding="utf-8")

        _save(xml_file, "<types><type name='changed'/></types>\n", encoding="utf-8")
        snapshot(xml_file, trigger="manual")

        result = restore(xml_file, version=1)
        assert result is True
        assert xml_file.read_text(encoding="utf-8") == original

    def test_restore_creates_pre_restore_snapshot(self, xml_file: Path):
        from app.shared.config_versions import load_index, restore, snapshot

        snapshot(xml_file, trigger="manual")
        _save(xml_file, "<changed/>", encoding="utf-8")
        # Don't snapshot the changed state — so pre_restore has something new to capture
        restore(xml_file, version=1)

        idx = load_index(_store())
        versions = idx.get_versions("types.xml")
        assert any(e.trigger == "pre_restore" for e in versions)

    def test_restore_nonexistent_version(self, xml_file: Path):
        from app.shared.config_versions import restore

        assert restore(xml_file, version=999) is False


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


class TestDiff:
    def test_diff_between_versions(self, xml_file: Path):
        from app.shared.config_versions import get_diff, snapshot

        snapshot(xml_file, trigger="manual")
        _save(xml_file, "<types>\n  <type name='M4A1'/>\n</types>\n", encoding="utf-8")
        snapshot(xml_file, trigger="manual")

        diff = get_diff("types.xml", 1, 2, store=_store())
        assert "AKM" in diff
        assert "M4A1" in diff
        assert diff.startswith("---")

    def test_diff_current(self, xml_file: Path):
        from app.shared.config_versions import get_diff_current, snapshot

        snapshot(xml_file, trigger="manual")
        _save(xml_file, "<types>\n  <type name='CHANGED'/>\n</types>\n", encoding="utf-8")

        diff = get_diff_current(xml_file, version=1)
        assert "CHANGED" in diff
        assert "AKM" in diff

    def test_diff_nonexistent_version(self, xml_file: Path):
        from app.shared.config_versions import get_diff

        assert get_diff("types.xml", 1, 2, store=_store()) == ""


class TestIsIdenticalToCurrent:
    """`identical` powers the web UI's "hide zero-diff versions" filter, so it
    must agree with get_diff_current() == "" in every case."""

    def _entry(self, xml_file: Path, version: int = 1):
        from app.shared.config_versions import load_index

        # get_versions is newest-first — pick by version number, not position.
        return next(
            e for e in load_index(_store()).get_versions(xml_file.name) if e.version == version
        )

    def test_unchanged_file(self, xml_file: Path):
        from app.shared.config_versions import get_diff_current, is_identical_to_current, snapshot

        snapshot(xml_file, trigger="manual")
        entry = self._entry(xml_file)

        assert is_identical_to_current(entry, store=_store()) is True
        assert get_diff_current(xml_file, version=1) == ""

    def test_changed_file(self, xml_file: Path):
        from app.shared.config_versions import get_diff_current, is_identical_to_current, snapshot

        snapshot(xml_file, trigger="manual")
        _save(xml_file, "<types>\n  <type name='CHANGED'/>\n</types>\n", encoding="utf-8")
        entry = self._entry(xml_file)

        assert is_identical_to_current(entry, store=_store()) is False
        assert get_diff_current(xml_file, version=1) != ""

    def test_xml_whitespace_only_change_is_identical(self, xml_file: Path):
        """The case that motivates the filter: XML is normalized before diffing,
        so a re-indented file has a different sha256 but an empty diff."""
        from app.shared.config_versions import get_diff_current, is_identical_to_current, snapshot

        snapshot(xml_file, trigger="manual")
        _save(xml_file, 
            "<types>\n\t\t\t<type name='AKM'/>\n</types>\n",
            encoding="utf-8",
        )
        entry = self._entry(xml_file)

        assert entry.sha256 != hashlib.sha256(xml_file.read_bytes()).hexdigest()
        assert is_identical_to_current(entry, store=_store()) is True
        assert get_diff_current(xml_file, version=1) == ""

    def test_missing_backup(self, xml_file: Path, monkeypatch):
        from app.shared import config_versions
        from app.shared.config_versions import is_identical_to_current, snapshot

        snapshot(xml_file, trigger="manual")
        entry = self._entry(xml_file)
        (_store() / config_versions._version_filename(entry)).unlink()
        _save(xml_file, "<types/>\n", encoding="utf-8")

        assert is_identical_to_current(entry, store=_store()) is True
        assert config_versions.get_diff_current(xml_file, version=1) == ""

    def test_missing_live_file(self, xml_file: Path):
        from app.shared.config_versions import get_diff_current, is_identical_to_current, snapshot

        snapshot(xml_file, trigger="manual")
        entry = self._entry(xml_file)
        xml_file.unlink()

        assert is_identical_to_current(entry, store=_store()) is True
        assert get_diff_current(xml_file, version=1) == ""

    def test_legacy_entry_without_source_path_stays_visible(self, xml_file: Path):
        from app.shared.config_versions import is_identical_to_current, snapshot

        snapshot(xml_file, trigger="manual")
        entry = self._entry(xml_file)
        entry.source_path = ""

        assert is_identical_to_current(entry, store=_store()) is False

    def test_cache_is_shared_across_entries(self, xml_file: Path):
        from app.shared.config_versions import is_identical_to_current, snapshot

        snapshot(xml_file, trigger="manual")
        _save(xml_file, "<types>\n  <type name='M4A1'/>\n</types>\n", encoding="utf-8")
        snapshot(xml_file, trigger="manual")

        cache: dict = {}
        v1 = self._entry(xml_file, 1)
        v2 = self._entry(xml_file, 2)

        assert is_identical_to_current(v1, cache, store=_store()) is False
        assert is_identical_to_current(v2, cache, store=_store()) is True
        assert list(cache) == [str(xml_file)]


# ---------------------------------------------------------------------------
# delete_version, cleanup_all
# ---------------------------------------------------------------------------


class TestCleanup:
    def test_delete_version(self, xml_file: Path):
        from app.shared.config_versions import delete_version, load_index, snapshot

        snapshot(xml_file, trigger="manual")
        _save(xml_file, "<v2/>", encoding="utf-8")
        snapshot(xml_file, trigger="manual")

        assert delete_version("types.xml", 1, store=_store()) is True
        idx = load_index(_store())
        assert len(idx.get_versions("types.xml")) == 1

    def test_delete_nonexistent(self, xml_file: Path):
        from app.shared.config_versions import delete_version

        assert delete_version("types.xml", 999, store=_store()) is False

    def test_cleanup_all(self, xml_file: Path):
        from app.shared.config_versions import cleanup_all, load_index, snapshot

        for i in range(10):
            _save(xml_file, f"<v>{i}</v>", encoding="utf-8")
            snapshot(xml_file, trigger="auto_save", max_versions=100)

        removed = cleanup_all(max_versions=3, store=_store())
        assert removed == 7
        idx = load_index(_store())
        assert len(idx.get_versions("types.xml")) == 3


# ---------------------------------------------------------------------------
# VersionIndex helpers
# ---------------------------------------------------------------------------


class TestVersionIndex:
    def test_versioned_files(self, tmp_path: Path):
        from app.shared.config_versions import load_index, snapshot

        a = tmp_path / "types.xml"
        b = tmp_path / "events.xml"
        _save(a, "<t/>", encoding="utf-8")
        _save(b, "<e/>", encoding="utf-8")

        snapshot(a)
        snapshot(b)
        _save(a, "<t2/>", encoding="utf-8")
        snapshot(a)

        idx = load_index(_store())
        files = idx.versioned_files()
        assert ("events.xml", 1) in files
        assert ("types.xml", 2) in files

    def test_next_version_starts_at_1(self):
        from app.shared.config_versions import VersionIndex

        idx = VersionIndex()
        assert idx.next_version("types.xml") == 1


# ---------------------------------------------------------------------------
# xml_utils / json_utils auto-snapshot integration
# ---------------------------------------------------------------------------


class TestAutoSnapshot:
    def test_save_xml_creates_snapshot(self, xml_file: Path):
        """save_xml() should auto-snapshot before overwriting."""
        from xml.etree import ElementTree as ET

        from app.shared.config_versions import load_index
        from app.xml.xml_utils import save_xml

        tree = ET.parse(xml_file)
        # First save — creates snapshot of original
        save_xml(tree, xml_file)
        # Modify and save again
        root = tree.getroot()
        ET.SubElement(root, "type", name="NewItem")
        save_xml(tree, xml_file)

        idx = load_index(_store())
        versions = idx.get_versions("types.xml")
        assert len(versions) >= 1

    def test_save_json_creates_snapshot(self, tmp_path: Path):
        """save_json() should auto-snapshot before overwriting."""
        from app.shared.config_versions import load_index
        from app.shared.json_utils import save_json

        p = tmp_path / "config.json"
        p.write_text('{"key": "old"}', encoding="utf-8")

        save_json(p, {"key": "new"})

        idx = load_index(_store())
        versions = idx.get_versions("config.json")
        assert len(versions) == 1
        assert versions[0].trigger == "auto_save"


# ---------------------------------------------------------------------------
# store_for — which instance's store a saved file goes to (plans/44 step 3)
# ---------------------------------------------------------------------------


def _instance(ident: str, server_dir: Path) -> Path:
    """Register *ident* with its own config.json pointing at *server_dir*."""
    import json

    from app.server.instances import ServerInstance, add_instance, load_registry, save_registry
    from app.shared.paths import instance_root

    root = instance_root(ident)
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.json").write_text(json.dumps({"server_dir": str(server_dir)}))
    save_registry(add_instance(load_registry(), ServerInstance(ident)))
    return root / "versions"


class TestStoreFor:
    def test_without_instances_every_file_goes_to_the_pre_instances_store(self, tmp_path: Path):
        from app.shared.config_versions import store_for

        assert store_for(tmp_path / "srv" / "types.xml") == tmp_path / "config" / "versions"
        assert store_for(None) == tmp_path / "config" / "versions"

    def test_a_mission_file_goes_to_the_instance_whose_server_holds_it(self, tmp_path: Path):
        from app.shared.config_versions import snapshot, store_for

        main_store = _instance("main", tmp_path / "srv-main")
        pvp_store = _instance("pvp", tmp_path / "srv-pvp")
        mission_file = tmp_path / "srv-pvp" / "mpmissions" / "types.xml"
        mission_file.parent.mkdir(parents=True)
        mission_file.write_text("<types/>\n")

        assert store_for(mission_file) == pvp_store
        snapshot(mission_file, trigger="manual")
        assert (pvp_store / "index.json").is_file()
        assert not main_store.exists()

    def test_an_instances_own_file_goes_to_that_instance(self, tmp_path: Path):
        from app.shared.config_versions import store_for
        from app.shared.paths import instance_root

        _instance("main", tmp_path / "srv-main")
        pvp_store = _instance("pvp", tmp_path / "srv-pvp")

        manual = instance_root("pvp") / "custom-xml" / "abc.xml"
        assert store_for(manual) == pvp_store

    def test_the_deepest_owner_wins(self, tmp_path: Path):
        from app.shared.config_versions import store_for

        _instance("outer", tmp_path / "srv")
        inner_store = _instance("inner", tmp_path / "srv" / "second")

        assert store_for(tmp_path / "srv" / "second" / "types.xml") == inner_store

    def test_a_file_no_instance_owns_keeps_the_pre_instances_store(self, tmp_path: Path):
        from app.shared.config_versions import store_for

        _instance("main", tmp_path / "srv-main")

        assert store_for(tmp_path / "elsewhere" / "x.xml") == tmp_path / "config" / "versions"


class TestOneVersionPerContent:
    """A file that goes A -> B -> A keeps one copy of A, the newest
    (config_versions._drop_repeats), so the Versions list does not fill with
    copies identical to each other and to the live file."""

    def test_a_repeat_replaces_the_older_copy(self, xml_file: Path):
        from app.shared.config_versions import _version_filename, load_index, snapshot

        a = xml_file.read_text(encoding="utf-8")
        snapshot(xml_file, trigger="editor")  # A
        _save(xml_file, "<types/>\n", encoding="utf-8")
        snapshot(xml_file, trigger="editor")  # B
        _save(xml_file, a, encoding="utf-8")
        newest = snapshot(xml_file, trigger="preset_apply")  # A again

        versions = load_index(_store()).get_versions(xml_file.name, source_path=str(xml_file))
        assert [v.version for v in versions] == [newest.version, 2]
        assert len({v.sha256 for v in versions}) == len(versions)
        assert all((_store() / _version_filename(v)).is_file() for v in versions)

    def test_restore_of_a_version_the_snapshot_drops(self, xml_file: Path):
        from app.shared.config_versions import load_index, restore, snapshot

        a = xml_file.read_text(encoding="utf-8")
        snapshot(xml_file, trigger="editor")  # v1 = A
        _save(xml_file, "<types/>\n", encoding="utf-8")
        snapshot(xml_file, trigger="editor")  # v2 = B
        _save(xml_file, a, encoding="utf-8")  # the file is A again, by hand

        # The pre-restore snapshot of A drops v1, the version being restored.
        assert restore(xml_file, 1)
        assert xml_file.read_text(encoding="utf-8") == a
        hashes = [v.sha256 for v in load_index(_store()).get_versions(xml_file.name, source_path=str(xml_file))]
        assert len(set(hashes)) == len(hashes)

    def test_cleanup_drops_existing_repeats(self, xml_file: Path):
        from app.shared.config_versions import cleanup_all, load_index, save_index, snapshot

        a = xml_file.read_text(encoding="utf-8")
        snapshot(xml_file, trigger="editor")
        _save(xml_file, "<types/>\n", encoding="utf-8")
        snapshot(xml_file, trigger="editor")
        # A repeat written before this rule existed: a copy of v1 as v3.
        index = load_index(_store())
        first = index.entries[0]
        from dataclasses import replace

        index.entries.append(replace(first, version=3, timestamp="2099-01-01T00:00:00+00:00"))
        save_index(index, _store())
        assert cleanup_all(store=_store()) == 1
        assert sorted(e.version for e in load_index(_store()).entries) == [2, 3]
        assert a  # the content itself is still held, by v3


class TestFoldedRepeatsStayOnTheTimeline:
    """A folded repeat loses its copy, never its event: the kept version
    records when and why, and the Timeline diff still runs for it."""

    def test_a_folded_repeat_is_a_timeline_event_with_a_diff(self, xml_file: Path):
        from app.shared.config_versions import get_diff_change, load_index, snapshot, timeline

        a = xml_file.read_text(encoding="utf-8")
        snapshot(xml_file, trigger="preset_apply:custom:night")  # v1 = A, folded later
        _save(xml_file, "<types><type name='B'/></types>\n", encoding="utf-8")
        snapshot(xml_file, trigger="tweak_reset:zombie_count")  # v2 = B
        _save(xml_file, a, encoding="utf-8")
        snapshot(xml_file, trigger="editor")  # v3 = A again: v1 folds into it
        _save(xml_file, "<types><type name='C'/></types>\n", encoding="utf-8")

        kept = load_index(_store()).get_latest(xml_file.name, source_path=str(xml_file))
        assert kept.version == 3
        assert [r["version"] for r in kept.repeats] == [1]
        assert kept.repeats[0]["trigger"] == "preset_apply:custom:night"

        events = timeline(_store())
        assert sorted(e["version"] for e in events) == [1, 2, 3]
        assert next(e for e in events if e["version"] == 1)["repeat_of"] == 3

        # v1's change ran from A to the next version, B.
        diff = get_diff_change(xml_file, 1, store=_store())
        assert '"AKM"' in diff and '"B"' in diff and '"C"' not in diff

    def test_a_version_the_write_did_not_change_is_no_event(self, xml_file: Path):
        """A preset or tweak keeps every file it might touch; a file it left
        alone is no change on the Timeline."""
        from app.shared.config_versions import snapshot, timeline

        snapshot(xml_file, trigger="tweak_apply:food_amount")  # the tweak left this file alone
        assert timeline(_store()) == []
        _save(xml_file, "<types/>\n", encoding="utf-8")
        assert [e["trigger"] for e in timeline(_store())] == ["tweak_apply:food_amount"]


class TestWhoActed:
    """A version records who made the write (app/shared/actor.py)."""

    def test_outside_a_request_the_cli_names_the_system_user(self, xml_file: Path, monkeypatch):
        from app.shared import actor
        from app.shared.config_versions import snapshot

        monkeypatch.setattr(actor, "_process_default", None)
        assert snapshot(xml_file, trigger="manual").actor.startswith("CLI")

    def test_a_request_holder_names_its_user_and_closes(self, xml_file: Path, monkeypatch):
        from app.shared import actor
        from app.shared.config_versions import snapshot

        monkeypatch.setattr(actor, "_process_default", "Zedstead")
        token = actor.begin()
        actor.set_name("mod1")
        try:
            assert snapshot(xml_file, trigger="editor").actor == "mod1"
        finally:
            actor.end(token)
        assert actor.current() == "Zedstead"

    def test_a_save_after_an_unchanged_snapshot_is_its_own_event(self, xml_file: Path, monkeypatch):
        """A tweak keeps a version of a file it leaves alone; an admin's save
        next has no new bytes to keep, but it is still the admin's change,
        never the tweak's. Only one trailing record is kept."""
        from app.shared import actor
        from app.shared.config_versions import load_index, snapshot, timeline

        monkeypatch.setattr(actor, "_process_default", "Zedstead")
        snapshot(xml_file, trigger="tweak_apply:food_amount")  # v1, the tweak leaves the file
        token = actor.begin()
        actor.set_name("admin")
        try:
            assert snapshot(xml_file, trigger="editor") is None  # same bytes as v1
            assert snapshot(xml_file, trigger="editor") is None  # a second no-op save
        finally:
            actor.end(token)
        _save(xml_file, "<types/>\n", encoding="utf-8")

        (event,) = timeline(_store())
        assert (event["trigger"], event["actor"]) == ("editor", "admin")
        (entry,) = load_index(_store()).entries
        assert len(entry.repeats) == 1

    def test_a_save_inside_a_preset_keeps_the_preset_as_the_cause(self, xml_file: Path):
        """One operation (a CLI run, a request): the preset's own version comes
        first, and the save inside it adds no "Save" event over it."""
        from app.shared.config_versions import snapshot, timeline

        snapshot(xml_file, trigger="preset_apply:hardcore_survival")
        assert snapshot(xml_file, trigger="auto_save") is None  # save_xml, inside the preset
        _save(xml_file, "<types/>\n", encoding="utf-8")
        assert [e["trigger"] for e in timeline(_store())] == ["preset_apply:hardcore_survival"]


class TestChangesOutsideZsm:
    """A file edited outside Zedstead (a text editor, another program) shows on
    the Timeline: config_versions records what Zedstead wrote (note_written)."""

    def test_an_edit_by_hand_shows_before_any_save(self, xml_file: Path):
        from app.shared.config_versions import OUTSIDE_ACTOR, get_outside_diff, snapshot, timeline

        snapshot(xml_file, trigger="editor")
        _save(xml_file, "<types>\n  <type name='M4A1'/>\n</types>\n")
        xml_file.write_text("<types>\n  <type name='SKS'/>\n</types>\n", encoding="utf-8")  # by hand

        pending, saved = timeline(_store())
        assert (pending["pending"], pending["version"], pending["actor"]) == (True, None, OUTSIDE_ACTOR)
        assert saved["trigger"] == "editor"
        diff = get_outside_diff(xml_file, store=_store())
        assert '-\t<type name="M4A1" />' in diff and '+\t<type name="SKS" />' in diff

    def test_the_next_save_keeps_the_edit_as_its_own_version(self, xml_file: Path):
        from app.shared.config_versions import OUTSIDE_TRIGGER, get_diff_change, snapshot, timeline

        snapshot(xml_file, trigger="editor")
        _save(xml_file, "<types>\n  <type name='M4A1'/>\n</types>\n")
        xml_file.write_text("<types>\n  <type name='SKS'/>\n</types>\n", encoding="utf-8")  # by hand
        snapshot(xml_file, trigger="editor")
        _save(xml_file, "<types>\n  <type name='AK74'/>\n</types>\n")

        events = timeline(_store())
        assert [e["trigger"] for e in events] == ["editor", OUTSIDE_TRIGGER, "editor"]
        assert not any(e.get("pending") for e in events)
        outside = events[1]
        diff = get_diff_change(xml_file, outside["version"], store=_store())
        assert '-\t<type name="M4A1" />' in diff and '+\t<type name="SKS" />' in diff

    def test_an_edit_after_a_snapshot_with_no_write_is_one_event(self, xml_file: Path):
        """A manual snapshot writes nothing: the outside edit after it is the
        event on that snapshot's version, not a second copy."""
        from app.shared.config_versions import OUTSIDE_TRIGGER, load_index, snapshot, timeline

        snapshot(xml_file, trigger="manual")
        xml_file.write_text("<types/>\n", encoding="utf-8")  # by hand
        snapshot(xml_file, trigger="editor")
        _save(xml_file, "<types><type name='B'/></types>\n")

        assert [e["trigger"] for e in timeline(_store())] == ["editor", OUTSIDE_TRIGGER]
        assert len(load_index(_store()).entries) == 2

    def test_zsm_writes_alone_are_never_outside_changes(self, xml_file: Path):
        from app.shared.config_versions import OUTSIDE_TRIGGER, restore, snapshot, timeline

        snapshot(xml_file, trigger="editor")
        _save(xml_file, "<types><type name='B'/></types>\n")
        snapshot(xml_file, trigger="editor")
        _save(xml_file, "<types><type name='C'/></types>\n")
        assert restore(xml_file, 1, store=_store())

        assert OUTSIDE_TRIGGER not in [e["trigger"] for e in timeline(_store())]

    def test_a_copy_onto_the_file_is_zsm_s_write(self, xml_file: Path):
        """Restore from backup copies a whole file (json_utils.copy_file), not
        atomic_write_text: it still counts as Zedstead's write."""
        from app.shared.config_versions import OUTSIDE_TRIGGER, snapshot, timeline
        from app.xml.xml_utils import restore_file

        backup = xml_file.with_suffix(xml_file.suffix + ".backup")
        backup.write_text("<types><type name='Z'/></types>\n", encoding="utf-8")
        snapshot(xml_file, trigger="editor")
        _save(xml_file, "<types><type name='B'/></types>\n")
        assert restore_file(xml_file)

        assert OUTSIDE_TRIGGER not in [e["trigger"] for e in timeline(_store())]
        assert not any(e.get("pending") for e in timeline(_store()))

    def test_a_file_zsm_never_versioned_is_not_recorded(self, tmp_path: Path):
        from app.shared.config_versions import load_index

        _save(tmp_path / "other.json", "{}")
        assert load_index(_store()).written == {}
