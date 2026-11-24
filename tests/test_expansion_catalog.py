"""The DayZ-Expansion module catalog (app/modmgr/expansion.py).

The dependency edges are the measured ones (plan 36): everything needs Core,
Core needs @CF + @Dabs Framework, Vehicles also needs Licensed, and Animations /
Licensed / Map-Assets need nothing.
"""

from __future__ import annotations

from app.modmgr import expansion
from app.shared.seed_data import parse_workshop_mod_ids

CORE = expansion.CORE_ID
CF = expansion.CF_ID
DABS = expansion.DABS_ID
LICENSED = expansion.LICENSED_ID
VEHICLES = "2291785437"
ANIMATIONS = "2793893086"
MAP_ASSETS = "2792983824"
MAIN = "2116151222"


def test_the_catalog_holds_the_nineteen_modules_and_two_frameworks():
    assert len(expansion.MODULES) == 19
    assert len(expansion.FRAMEWORKS) == 2
    assert {m.id for m in expansion.FRAMEWORKS} == {CF, DABS}
    # Every id appears once.
    ids = [m.id for m in expansion.ALL]
    assert len(ids) == len(set(ids))


def test_every_catalog_id_is_in_the_seed_registry():
    """The catalog must never become a second list of Expansion IDs — it names
    modules by the ID they already have in WORKSHOP_MOD_IDS, and reads the name
    back from there."""
    registry = parse_workshop_mod_ids()
    unknown = [m.id for m in expansion.ALL if m.id not in registry]
    assert unknown == []
    for m in expansion.ALL:
        assert expansion.module_name(m.id) == registry[m.id]
        assert expansion.module_name(m.id).startswith("@")


def test_every_dependency_edge_points_at_a_catalog_entry():
    known = {m.id for m in expansion.ALL}
    for m in expansion.ALL:
        assert set(m.deps) <= known, m.label


def test_vehicles_pulls_licensed_core_and_both_frameworks():
    assert expansion.required_for([VEHICLES]) == {LICENSED, CORE, CF, DABS}


def test_a_plain_module_pulls_core_and_the_frameworks_only():
    assert expansion.required_for([MAIN]) == {CORE, CF, DABS}
    assert expansion.required_for(["2828486817"]) == {CORE, CF, DABS}  # Quests


def test_the_three_standalone_modules_need_nothing():
    for mod_id in (ANIMATIONS, LICENSED, MAP_ASSETS):
        assert expansion.required_for([mod_id]) == set(), mod_id


def test_core_needs_both_frameworks_and_the_frameworks_need_nothing():
    assert expansion.required_for([CORE]) == {CF, DABS}
    assert expansion.required_for([CF, DABS]) == set()


def test_required_for_excludes_what_was_selected():
    # Ticking Vehicles *and* Core leaves Core out of the "also needed" set.
    assert expansion.required_for([VEHICLES, CORE]) == {LICENSED, CF, DABS}


def test_unknown_ids_are_ignored_rather_than_carrying_edges():
    assert expansion.required_for(["999999"]) == set()
    assert expansion.required_for([VEHICLES, "999999"]) == {LICENSED, CORE, CF, DABS}
    assert expansion.get("999999") is None
    assert expansion.module_name("999999") == ""


def test_requires_is_the_closure_in_catalog_order():
    # Frameworks first, then modules in catalog order — the order an install
    # should walk them in.
    assert expansion.requires(VEHICLES) == [CF, DABS, CORE, LICENSED]
    assert expansion.requires(ANIMATIONS) == []


def test_selecting_everything_needs_only_the_two_frameworks_on_top():
    all_modules = [m.id for m in expansion.MODULES]
    assert expansion.required_for(all_modules) == {CF, DABS}


def test_ids_are_strings_so_a_numeric_selection_still_resolves():
    assert expansion.required_for([int(VEHICLES)]) == {LICENSED, CORE, CF, DABS}


def test_class_counts_are_the_measured_ones():
    by_label = {m.label: m for m in expansion.MODULES}
    assert by_label["Map Assets"].classes == 986
    assert by_label["Vehicles"].classes == 549
    assert by_label["Licensed"].classes == 362
    # @DayZ-Expansion is a content module, not a container: it has classes of
    # its own and requires only Core.
    assert by_label["Expansion (main)"].classes == 105
    assert by_label["Expansion (main)"].deps == (CORE,)


# ── the cheatsheet pairings (a hint, never a dependency) ──────────────


def test_pairings_point_at_real_modules_and_are_not_dependencies():
    """Expansion's installation cheatsheet packages some modules together. None
    of those pairs is in `requiredAddons`, so none of them may leak into the
    dependency closure — the picker shows them as text and ticks nothing."""
    for mod_id, pair in expansion.PAIRINGS.items():
        assert expansion.get(mod_id), mod_id
        assert pair.note
        for dep_id in pair.with_ids:
            assert expansion.get(dep_id), dep_id
            assert dep_id not in expansion.required_for([mod_id])


def test_the_four_cheatsheet_packages_are_the_ones_on_the_sheet():
    by_label = {m.label: m.id for m in expansion.MODULES}
    packaged = {
        expansion.get(mod_id).label: sorted(expansion.get(i).label for i in pair.with_ids)
        for mod_id, pair in expansion.PAIRINGS.items()
    }
    assert packaged == {
        "Base Building": ["Book", "Licensed"],
        "Groups": ["Book"],
        "Missions": ["Licensed"],
        "Expansion (main)": ["Map Assets"],
    }
    # Vehicles is NOT here: its Licensed edge is a measured dependency, and
    # listing it twice would say the cheatsheet is where that came from.
    assert by_label["Vehicles"] not in expansion.PAIRINGS
