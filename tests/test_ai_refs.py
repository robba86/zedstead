"""Tests for app/ops/ai_refs.py — the faction and loadout names Expansion AI
resolves, shared by Dynamic AI and Expansion's own patrols (plans/64 step 1)."""

from __future__ import annotations

import pytest

from app.ops import ai_refs
from app.ops.ai_refs import Loadouts, faction_problem, loadout_problem
from tests.conftest import write_test_mods

FACTIONS_PBO = b"\x00junk eAIRegisterFaction(eAIFactionEast); eAIRegisterFaction(eAIFactionWest) \x00"


def _loadouts(*names: str) -> Loadouts:
    return Loadouts(frozenset(names), {n.lower(): n for n in names})


@pytest.fixture()
def install(sample_config):
    """Expansion AI active, registering East and West, with two loadouts."""
    cfg = sample_config
    write_test_mods(cfg, ['@DayZ-Expansion-AI'])
    addons = cfg.server_dir_path / "@DayZ-Expansion-AI" / "addons"
    addons.mkdir(parents=True)
    (addons / "ai_scripts.pbo").write_bytes(FACTIONS_PBO)
    folder = ai_refs.loadouts_dir(cfg)
    folder.mkdir(parents=True)
    for name in ("EastLoadout.json", "PoliceLoadout.json"):
        (folder / name).write_text("{}", encoding="utf-8")
    (folder / "notes.txt").write_text("", encoding="utf-8")
    return cfg


# ── loadouts ────────────────────────────────────────────────────────────


def test_the_exact_file_name_is_found():
    assert loadout_problem("EastLoadout.json", _loadouts("EastLoadout.json")) is None


def test_without_the_option_the_extension_is_part_of_the_name():
    """Dynamic AI looks the name up verbatim."""
    assert loadout_problem("EastLoadout", _loadouts("EastLoadout.json")) == (
        "EastLoadout is not in ExpansionMod/Loadouts")


@pytest.mark.parametrize("name", ["EastLoadout", "EastLoadout.json"])
def test_with_the_option_both_spellings_name_the_file(name):
    """Expansion strips a .json and adds its own."""
    assert loadout_problem(name, _loadouts("EastLoadout.json"), extension_optional=True) is None


def test_an_empty_name_is_refused_unless_allowed():
    assert loadout_problem("", _loadouts("EastLoadout.json")) == "no loadout named"
    assert loadout_problem(None, _loadouts()) == "no loadout named"
    # Expansion: empty is the faction's default loadout.
    assert loadout_problem("", _loadouts(), empty_ok=True) is None
    # A number is never a name, allowed or not.
    assert loadout_problem(5, _loadouts(), empty_ok=True) == "no loadout named"


def test_a_case_only_difference_is_named(monkeypatch):
    monkeypatch.setattr(ai_refs, "IS_WINDOWS", False)
    assert loadout_problem("eastloadout", _loadouts("EastLoadout.json"), extension_optional=True) == (
        "eastloadout differs from EastLoadout.json only in case, "
        "and the server's filesystem is case-sensitive")


def test_a_case_only_difference_is_fine_on_windows(monkeypatch):
    monkeypatch.setattr(ai_refs, "IS_WINDOWS", True)
    assert loadout_problem("eastloadout.json", _loadouts("EastLoadout.json")) is None


def test_read_loadouts_lists_files_only(install):
    found = ai_refs.read_loadouts(install)
    assert found.names == {"EastLoadout.json", "PoliceLoadout.json", "notes.txt"}
    assert found.lower["policeloadout.json"] == "PoliceLoadout.json"


def test_read_loadouts_without_the_folder(sample_config):
    assert ai_refs.read_loadouts(sample_config).names == frozenset()


# ── factions ────────────────────────────────────────────────────────────


def test_a_registered_faction_passes():
    assert faction_problem("East", {"East", "West"}) is None


def test_an_unknown_faction_is_named():
    assert faction_problem("Bandits", {"East", "West"}) == "Bandits is not a registered faction"


def test_a_case_only_faction_difference_is_named():
    assert faction_problem("east", {"East", "West"}) == "east differs from East only in case"


@pytest.mark.parametrize("name", ["", None])
def test_no_faction_named(name):
    assert faction_problem(name, {"East"}) == "no faction named"


def test_random_only_where_the_caller_accepts_it():
    assert faction_problem("RANDOM", {"East"}, random_ok=True) is None
    assert faction_problem("RANDOM", {"East"}) == "RANDOM is not a registered faction"
    # The keyword is exact, like every faction name.
    assert faction_problem("Random", {"East"}, random_ok=True) == "Random is not a registered faction"


def test_an_empty_scan_knows_nothing():
    """No faction could be read: unknown, never "every name is wrong"."""
    assert faction_problem("Anything", set()) is None
    assert faction_problem("", set()) is None


def test_installed_factions_reads_the_active_mods(install):
    assert ai_refs.installed_factions(install) == {"East", "West"}


# ── pickers ─────────────────────────────────────────────────────────────


def test_install_choices_dynamic_ai_shape(install):
    """File names and no RANDOM: what Dynamic AI's pickers offered before."""
    assert ai_refs.install_choices(install, loadout_stems=False, random=False) == {
        "factions": ["East", "West"],
        "loadouts": ["EastLoadout.json", "PoliceLoadout.json"],
    }


def test_install_choices_expansion_shape(install):
    assert ai_refs.install_choices(install, loadout_stems=True, random=True) == {
        "factions": ["RANDOM", "East", "West"],
        "loadouts": ["EastLoadout", "PoliceLoadout"],
    }


def test_install_choices_survives_a_failed_scan(install, monkeypatch):
    def _fail(config):
        raise OSError("unreadable")

    monkeypatch.setattr(ai_refs, "installed_factions", _fail)
    choices = ai_refs.install_choices(install, loadout_stems=True, random=True)
    assert choices["factions"] == []
    assert choices["loadouts"] == ["EastLoadout", "PoliceLoadout"]
