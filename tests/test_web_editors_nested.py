"""``app/web/editors/_nested.py``: fitting an edited tree to the file's shape."""

from __future__ import annotations

import pytest

from app.shared.json_utils import SourceFloat
from app.web.editors._nested import apply_nested, conform, is_nested


def test_what_counts_as_nested():
    assert is_nested({"a": 1})
    assert is_nested([{"a": 1}])
    assert is_nested([[1, 2, 3]])
    assert not is_nested([1, 2])
    assert not is_nested([])
    assert not is_nested("x")


def test_numbers_keep_the_type_the_file_has():
    assert conform("5", 4, "a") == 5 and isinstance(conform("5", 4, "a"), int)
    assert conform("5", 4.5, "a") == 5.0 and isinstance(conform("5", 4.5, "a"), float)
    assert conform("1", 0, "a") == 1  # a 0/1 flag stays an int, never true
    with pytest.raises(ValueError, match=r"a\.b\[2\]: '4\.5' is not a whole number"):
        conform({"b": [1, "4.5"]}, {"b": [1, 2]}, "a")
    with pytest.raises(ValueError, match="is not a number"):
        conform("inf", 1.0, "a")
    with pytest.raises(ValueError, match="can't be left empty"):
        conform("", 1, "a")


def test_an_untouched_value_is_the_file_s_own_object():
    old = SourceFloat("0.20000000298023224")
    assert conform(0.20000000298023224, old, "a") is old
    entry = {"x": old, "n": 1}
    assert conform({"x": 0.20000000298023224, "n": 1}, entry, "a") is entry


def test_a_new_entry_is_fitted_to_its_neighbour():
    old = [{"Name": "a", "Chance": 0.5, "Count": 1}]
    new = conform(old + [{"Name": "b", "Chance": "1", "Count": "2"}], old, "L")
    assert new[1] == {"Name": "b", "Chance": 1.0, "Count": 2}
    assert isinstance(new[1]["Chance"], float)


def test_keys_are_neither_added_nor_dropped():
    with pytest.raises(ValueError, match="has no key 'y'"):
        conform({"x": 1, "y": 2}, {"x": 1}, "a")
    assert conform({}, {"x": 1}, "a") == {"x": 1}


def test_only_allowed_keys_are_written_and_a_stale_base_is_refused():
    doc = {"L": [1], "M": [{"a": 1}]}
    apply_nested(doc, {"$nested": {"L": [9], "M": [{"a": 2}]}, "$nested_base": {"L": [1], "M": [{"a": 1}]}}, {"M"})
    assert doc == {"L": [1], "M": [{"a": 2}]}
    with pytest.raises(ValueError, match="M changed in the file"):
        apply_nested(doc, {"$nested": {"M": []}, "$nested_base": {"M": [{"a": 1}]}}, {"M"})
    with pytest.raises(ValueError, match="M changed in the file"):
        apply_nested(doc, {"$nested": {"M": []}}, {"M"})


def test_a_moved_or_removed_entry_keeps_its_own_keys():
    """Entries of one list need not share keys (Sakhal's underground triggers,
    a breadcrumb with or without `Radius`). A removal or a move shifts every
    entry after it, and fitting by position took the neighbour's keys, or
    refused one the neighbour lacked."""
    old = [
        {"Position": [1, 2, 3], "EyeAccommodation": 1, "Radius": -1},
        {"Position": [4, 5, 6], "EyeAccommodation": SourceFloat("0.7")},
        {"Position": [7, 8, 9], "EyeAccommodation": SourceFloat("0.4"), "Comment": "x"},
    ]
    # Remove the first: the other two keep exactly their own keys and objects.
    after = conform([dict(old[1]), dict(old[2])], old, "B")
    assert after[0] is old[1] and after[1] is old[2]
    # Reverse, with an edit: each entry is fitted to an entry with its keys.
    edited = {**old[2], "Comment": "y"}
    after = conform([edited, dict(old[1]), dict(old[0])], old, "B")
    assert after == [
        {"Position": [7, 8, 9], "EyeAccommodation": 0.4, "Comment": "y"},
        old[1],
        old[0],
    ]
    assert after[1] is old[1] and after[2] is old[0]
    assert after[0]["EyeAccommodation"] is old[2]["EyeAccommodation"]
    # A copy of the first entry appended at the end is fitted to the entry it copies.
    after = conform(old + [{**old[0], "Radius": "5"}], old, "B")
    assert after[3] == {"Position": [1, 2, 3], "EyeAccommodation": 1, "Radius": 5}


def test_a_key_a_sibling_writes_with_a_fraction_takes_one_everywhere():
    # Bohemia's underground triggers: EyeAccommodation 0, 0.2 and 1 in one list.
    old = [{"Eye": 1, "Flag": 0}, {"Eye": 0.2, "Flag": 1}]
    new = conform([{"Eye": "0.5", "Flag": 0}, {"Eye": 0.2, "Flag": 1}], old, "T")
    assert new[0]["Eye"] == 0.5
    assert conform([{"Eye": "1", "Flag": 0}, old[1]], old, "T")[0]["Eye"] == 1
    with pytest.raises(ValueError, match=r"T\[1\]\.Flag: '0\.5' is not a whole number"):
        conform([{"Eye": 1, "Flag": "0.5"}, old[1]], old, "T")


def test_a_key_the_game_reads_as_a_decimal_takes_one_though_written_whole():
    old = {"Triggers": [{"Position": [100, 2, 300], "EyeAccommodation": 1, "UseRaycast": 0}]}
    doc = {"Triggers": [dict(t) for t in old["Triggers"]]}
    edit = {"Triggers": [{"Position": [100.5, 2, 300], "EyeAccommodation": "0.5", "UseRaycast": 0}]}
    apply_nested(
        doc,
        {"$nested": edit, "$nested_base": old},
        {"Triggers"},
        floats=frozenset({"Position", "EyeAccommodation"}),
    )
    assert doc["Triggers"][0]["Position"] == [100.5, 2, 300]
    assert doc["Triggers"][0]["EyeAccommodation"] == 0.5
    bad = {"Triggers": [{"Position": [100, 2, 300], "EyeAccommodation": 1, "UseRaycast": "0.5"}]}
    with pytest.raises(ValueError, match="UseRaycast: '0.5' is not a whole number.*Raw tab"):
        apply_nested(doc, {"$nested": bad, "$nested_base": doc}, {"Triggers"}, floats=frozenset({"Position"}))


def test_an_empty_list_borrows_its_entry_from_a_sibling():
    """plans/76: a patrol whose `Waypoints` is empty takes a new waypoint on the
    shape of another patrol's, so a typed 5 is a number, not "5"."""
    old = [
        {"Name": "a", "Waypoints": [[1.5, 2.0, 3.0]], "Units": [{"Count": 1, "Loadout": "x"}]},
        {"Name": "b", "Waypoints": [], "Units": []},
    ]
    new = [
        old[0],
        {"Name": "b", "Waypoints": [["4", "5", "6.5"]], "Units": [{"Count": "3", "Loadout": "y"}]},
    ]
    after = conform(new, old, "Patrols")
    assert after[1]["Waypoints"] == [[4.0, 5.0, 6.5]]
    assert after[1]["Units"] == [{"Count": 3, "Loadout": "y"}]
    assert isinstance(after[1]["Units"][0]["Count"], int)
    assert old[0]["Units"] == [{"Count": 1, "Loadout": "x"}]


def test_a_borrowed_entry_with_other_keys_is_written_as_sent():
    old = [{"L": [{"a": 1}]}, {"L": []}]
    after = conform([old[0], {"L": [{"b": "2"}]}], old, "X")
    assert after[1]["L"] == [{"b": "2"}]


def test_an_empty_list_without_a_template_is_written_as_sent():
    assert conform([{"a": "1"}], [], "L") == [{"a": "1"}]


def test_a_template_fits_an_entry_of_an_empty_top_level_list():
    doc = {"Zones": [], "Other": 1}
    template = {"Name": "", "Radius": 0.0, "Flag": 0, "Items": []}
    entry = {"Name": "z", "Radius": "5", "Flag": "1", "Items": ["A"]}
    apply_nested(
        doc,
        {"$nested": {"Zones": [entry]}, "$nested_base": {"Zones": []}},
        {"Zones"},
        templates={"Zones": template},
    )
    assert doc["Zones"] == [{"Name": "z", "Radius": 5.0, "Flag": 1, "Items": ["A"]}]
    assert isinstance(doc["Zones"][0]["Radius"], float)
    assert isinstance(doc["Zones"][0]["Flag"], int)
    assert template == {"Name": "", "Radius": 0.0, "Flag": 0, "Items": []}
    with pytest.raises(ValueError, match=r"Zones\[1\]: has no key 'x'"):
        apply_nested(
            {"Zones": []},
            {"$nested": {"Zones": [{"x": 1}]}, "$nested_base": {"Zones": []}},
            {"Zones"},
            templates={"Zones": template},
        )
