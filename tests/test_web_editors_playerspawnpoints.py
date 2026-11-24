"""cfgplayerspawnpoints.xml as a table with a tree (plans/76 step 3,
`app/web/editors/playerspawnpoints.py`).

A row per section, its params as columns, its groups and positions as the
row's tree. A save writes into the elements in place, so every comment (the
START/END MOD markers `xml_merge` matches as text, a parked mod, Sakhal's
explanations) and every element outside the edit stays byte for byte.
"""

from __future__ import annotations

import copy
import difflib
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.web.editors import playerspawnpoints as psp
from tests.conftest import CE_1_29, bohemia_ce, ce_block, ce_lines, ce_pick, dayz_docs

PASSWORD = "testpass123"

def spawns_xml() -> str:
    """Lines of Bohemia's Chernarus cfgplayerspawnpoints.xml, read at the
    pinned 1.29 commit (the head of <fresh>, WestCherno's and EastCherno's
    first position, the frame of <hop>), between lines of the test's own: a
    decimal min_steepness, a second WestCherno position, the comments, a mod's
    merged and parked groups, and a <hop> with positions outside any group."""
    lines = ce_lines(bohemia_ce(CE_1_29, "dayzOffline.chernarusplus/cfgplayerspawnpoints.xml"))
    west = ce_block(lines, '<group name="WestCherno">')
    east = ce_block(lines, '<group name="EastCherno">')
    hop = ce_block(lines, "<hop>")
    return "".join(
        ce_pick(lines, "<?xml", "<playerspawnpoints>", "<fresh>", "<spawn_params>", "<min_dist_infected>",
                "<max_dist_infected>", "</spawn_params>", "<generator_params>", "<grid_density>")
        + ["            <min_steepness>-39.999996</min_steepness>\n"]
        + ce_pick(lines, "</generator_params>", "<group_params>", "<enablegroups>", "<lifetime>",
                  "</group_params>", "<generator_posbubbles>")
        + ["            <!-- Stock groups -->\n"]
        + west[:2] + ['                <pos x="10595" z="2171.072998" />\n'] + west[-1:]
        + [
            "            <!-- START MOD: @Spawns -->\n",
            '            <group name="ModTown" lifetime="300" counter="25">\n',
            '                <pos x="4212.421875" z="11038.256836" />\n',
            "            </group>\n",
            "            <!-- END MOD: @Spawns -->\n",
            '            <!-- DISABLED @Old: <group name="Gone"><pos x="1" z="2" /></group> -->\n',
        ]
        + east[:2] + east[-1:]
        + ce_pick(lines, "</generator_posbubbles>", "</fresh>", "<hop>", "<spawn_params>")
        + ["            <min_dist_infected>0</min_dist_infected>\n"]
        + ce_pick(hop, "</spawn_params>", "<generator_posbubbles>")
        + ['            <pos x="7500.0" z="7500.0" />\n', '            <pos x="7600.0" z="7400.0" />\n']
        + ce_pick(hop, "</generator_posbubbles>", "</hop>")
        + lines[-1:]
    )


def _changed_lines(before: str, after: str) -> list[str]:
    return [
        line for line in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0)
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    ]


def _comments(text: str) -> list[str]:
    return re.findall(r"<!--.*?-->", text, re.S)


def _write(tmp_path: Path, text: str | None = None) -> Path:
    path = tmp_path / "cfgplayerspawnpoints.xml"
    path.write_bytes((spawns_xml() if text is None else text).encode())
    return path


def _sent(payload: dict) -> list[dict]:
    """The rows as TableEditor sends them back untouched."""
    return [{"id": r["id"], "values": copy.deepcopy(r["values"])} for r in payload["rows"]]


def _edit_tree(rows: list[dict], payload: dict, section: str, key: str, edit) -> None:
    """Edit one row's tree the way TableEditor sends it: the edited key under
    `$nested`, the key as loaded under `$nested_base`."""
    loaded = next(r for r in payload["rows"] if r["id"] == section)["nested"][key]
    tree = copy.deepcopy(loaded)
    edit(tree)
    row = next(r for r in rows if r["id"] == section)
    row["values"]["$nested"] = {key: tree}
    row["values"]["$nested_base"] = {key: loaded}


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------


def test_payload_is_a_row_per_section_with_params_as_columns_and_positions_as_a_tree(tmp_path):
    payload = psp.table_load(_write(tmp_path))

    assert payload["kind"] == "table"
    assert payload["add_row"] is False
    assert payload["nested_templates"] == {
        "groups": {"name": "", "lifetime": "", "counter": "", "pos": []},
        "pos": {"x": 0.0, "z": 0.0},
    }
    keys = [c["key"] for c in payload["columns"]]
    assert keys[0] == "section"
    assert "spawn_params.min_dist_infected" in keys and "group_params.counter" in keys
    types = {c["key"]: c["type"] for c in payload["columns"]}
    assert types["group_params.enablegroups"] == "bool"
    assert types["generator_params.grid_density"] == "int"
    assert types["generator_params.min_steepness"] == "float"
    assert all(c.get("help") for c in payload["columns"])

    fresh, hop = payload["rows"]
    assert (fresh["id"], hop["id"]) == ("fresh", "hop")
    assert fresh["values"]["spawn_params.min_dist_infected"] == 30
    assert fresh["values"]["generator_params.min_steepness"] == -39.999996
    assert fresh["values"]["group_params.enablegroups"] is True
    # Not in the file: empty for a number, the documented default for a switch.
    assert fresh["values"]["spawn_params.min_dist_player"] == ""
    assert fresh["values"]["group_params.groups_as_regular"] is True
    assert fresh["values"]["$base"]["spawn_params.min_dist_infected"] == 30

    groups = fresh["nested"]["groups"]
    assert [g["name"] for g in groups] == ["WestCherno", "ModTown", "EastCherno"]
    assert groups[0]["pos"] == [{"x": 6063.018555, "z": 1931.907227}, {"x": 10595, "z": 2171.072998}]
    # A group without its own lifetime/counter offers them empty.
    assert (groups[0]["lifetime"], groups[0]["counter"]) == ("", "")
    # A group's own lifetime/counter are its attributes.
    assert groups[1] == {"name": "ModTown", "lifetime": 300, "counter": 25,
                         "pos": [{"x": 4212.421875, "z": 11038.256836}]}
    # The layout without groups: the bare positions, and no groups list.
    assert hop["nested"] == {"pos": [{"x": 7500.0, "z": 7500.0}, {"x": 7600.0, "z": 7400.0}]}


@pytest.mark.parametrize("mission", ["dayzOffline.chernarusplus", "dayzOffline.enoch", "dayzOffline.sakhal"])
def test_payload_on_bohemias_files(mission):
    path = dayz_docs("vanilla", mission, "cfgplayerspawnpoints.xml")
    text = path.read_text()
    payload = psp.table_load(path)

    assert [r["id"] for r in payload["rows"]] == ["fresh", "hop", "travel"]
    groups = [g for r in payload["rows"] for g in r["nested"].get("groups", [])]
    assert len(groups) == text.count("<group ")
    assert sum(len(g["pos"]) for g in groups) == text.count("<pos ")
    keys = {c["key"] for c in payload["columns"]}
    assert {"generator_params.grid_density", "group_params.enablegroups"} <= keys
    for row in payload["rows"]:
        assert isinstance(row["values"]["generator_params.grid_density"], int)


@pytest.mark.parametrize("mission", ["dayzOffline.chernarusplus", "dayzOffline.enoch", "dayzOffline.sakhal"])
def test_an_untouched_save_of_bohemias_file_is_byte_identical(tmp_path, mission):
    original = dayz_docs("vanilla", mission, "cfgplayerspawnpoints.xml").read_bytes()
    path = tmp_path / "cfgplayerspawnpoints.xml"
    path.write_bytes(original)

    psp.table_save(path, _sent(psp.table_load(path)))

    assert path.read_bytes() == original


def test_one_position_and_one_param_change_two_lines_of_sakhals_file(tmp_path):
    original = dayz_docs("vanilla", "dayzOffline.sakhal", "cfgplayerspawnpoints.xml").read_text()
    path = tmp_path / "cfgplayerspawnpoints.xml"
    path.write_text(original)
    payload = psp.table_load(path)
    rows = _sent(payload)
    rows[0]["values"]["spawn_params.max_dist_player"] = "175"

    def move(groups):
        groups[0]["pos"][1]["x"] = 14230.5

    _edit_tree(rows, payload, "fresh", "groups", move)
    psp.table_save(path, rows)

    assert _changed_lines(original, path.read_text()) == [
        "-            <max_dist_player>150</max_dist_player>",
        "+            <max_dist_player>175</max_dist_player>",
        '-                <pos x="14230.625977" z="9040.563477" />',
        '+                <pos x="14230.500000" z="9040.563477" />',
    ]
    assert _comments(path.read_text()) == _comments(original)


# ---------------------------------------------------------------------------
# Saves
# ---------------------------------------------------------------------------


def test_an_edit_keeps_the_mod_markers_and_the_parked_mod_byte_for_byte(tmp_path):
    path = _write(tmp_path)
    payload = psp.table_load(path)
    rows = _sent(payload)
    rows[0]["values"]["generator_params.min_steepness"] = "-30"

    def edit(groups):
        groups[2]["pos"][0]["z"] = 3400  # EastCherno, after the mod block
        groups[0]["pos"].reverse()       # WestCherno, before it

    _edit_tree(rows, payload, "fresh", "groups", edit)
    psp.table_save(path, rows)
    after = path.read_text()

    assert _comments(after) == _comments(spawns_xml())
    mod_block = spawns_xml()[spawns_xml().index("<!-- START MOD"):spawns_xml().index("<group name=\"EastCherno\"")]
    assert mod_block in after
    assert _changed_lines(spawns_xml(), after) == [
        # A decimal param keeps the file's number of places.
        "-            <min_steepness>-39.999996</min_steepness>",
        "+            <min_steepness>-30.000000</min_steepness>",
        # The two positions swapped: one line moves.
        '+                <pos x="10595" z="2171.072998" />',
        '-                <pos x="10595" z="2171.072998" />',
        '-                <pos x="8040.858398" z="3332.236328" />',
        '+                <pos x="8040.858398" z="3400.000000" />',
    ]


def test_a_whole_x_takes_a_fraction(tmp_path):
    """Bohemia writes some coordinates whole; the game reads them as decimals."""
    path = _write(tmp_path)
    payload = psp.table_load(path)
    rows = _sent(payload)
    _edit_tree(rows, payload, "fresh", "groups", lambda g: g[0]["pos"][1].update(x=10595.5))
    psp.table_save(path, rows)

    assert '<pos x="10595.5" z="2171.072998" />' in path.read_text()


def test_a_group_added_from_the_template_lands_after_the_last_group(tmp_path):
    path = _write(tmp_path)
    payload = psp.table_load(path)
    rows = _sent(payload)

    def add(groups):
        group = copy.deepcopy(payload["nested_templates"]["groups"])
        group["name"] = "Berezino"
        point = copy.deepcopy(payload["nested_templates"]["pos"])
        point.update(x=12000.5, z=9000.25)
        group["pos"].append(point)
        groups.append(group)

    _edit_tree(rows, payload, "fresh", "groups", add)
    psp.table_save(path, rows)
    after = path.read_text()

    assert _comments(after) == _comments(spawns_xml())
    groups = psp.table_load(path)["rows"][0]["nested"]["groups"]
    assert groups[-1] == {"name": "Berezino", "lifetime": "", "counter": "",
                          "pos": [{"x": 12000.5, "z": 9000.25}]}
    added = [line for line in _changed_lines(spawns_xml(), after) if line.startswith("+")]
    assert added == [
        # The template's empty lifetime/counter are not written.
        '+            <group name="Berezino">',
        # Copied from its neighbour's element, so in its number style.
        '+                <pos x="12000.500000" z="9000.250000" />',
        "+            </group>",
    ]
    assert after.index('name="Berezino"') > after.index('name="EastCherno"')


def test_a_group_takes_its_own_lifetime_and_counter(tmp_path):
    """The wiki: "Lifetime and Counter can be turned off by putting in value of
    -1 and it can be overwritten for each specific group", its example being
    ``<group name="Tents" lifetime="300" counter="25">``. A group that has
    neither could not get them: the tree adds no key a group lacks."""
    path = _write(tmp_path)
    payload = psp.table_load(path)
    rows = _sent(payload)
    _edit_tree(rows, payload, "fresh", "groups", lambda g: g[0].update(lifetime="300", counter=-1))
    psp.table_save(path, rows)

    assert _changed_lines(spawns_xml(), path.read_text()) == [
        '-            <group name="WestCherno">',
        '+            <group name="WestCherno" lifetime="300" counter="-1">',
    ]


@pytest.mark.parametrize("edit, message", [
    ({"lifetime": "soon"}, "not a number"),
    ({"counter": "2.5"}, "not a whole number"),
    ({"lifetime": -2}, "-1 (off) or more"),
])
def test_a_groups_lifetime_or_counter_dayz_cannot_read_is_refused(tmp_path, edit, message):
    path = _write(tmp_path)
    payload = psp.table_load(path)
    rows = _sent(payload)
    _edit_tree(rows, payload, "fresh", "groups", lambda g: g[0].update(edit))
    with pytest.raises(ValueError, match=re.escape(message)):
        psp.table_save(path, rows)
    assert path.read_text() == spawns_xml()


@pytest.mark.parametrize("cells, error", [
    ({"generator_params.grid_density": 201}, "can't be larger than the grid's smaller side"),
    ({"generator_params.grid_width": 4097}, "1 to 4096"),
    ({"generator_params.grid_width": 2}, "can't be larger than the grid's smaller side"),
    ({"generator_params.grid_width": 2, "generator_params.grid_density": 0}, None),
    ({"generator_params.grid_density": 0}, None),
])
def test_the_table_keeps_the_grid_inside_bohemias_limits(tmp_path, cells, error):
    """Sakhal's stock file: width and height 1 to 4096 metres, density no
    larger than the smaller side (0 = the position alone). The section here
    has no grid_width/grid_height, so a density is checked against a side
    only once the row has one."""
    text = spawns_xml().replace(
        "<grid_density>4</grid_density>",
        "<grid_density>4</grid_density>\n            <grid_width>200</grid_width>",
    )
    path = _write(tmp_path, text)
    rows = _sent(psp.table_load(path))
    rows[0]["values"].update(cells)
    if error:
        with pytest.raises(ValueError, match=re.escape(error)):
            psp.table_save(path, rows)
        assert path.read_text() == text
    else:
        psp.table_save(path, rows)


def test_a_position_added_to_a_section_without_groups(tmp_path):
    path = _write(tmp_path)
    payload = psp.table_load(path)
    rows = _sent(payload)
    _edit_tree(rows, payload, "hop", "pos", lambda p: p.append({"x": 7700.0, "z": 7300.5}))
    psp.table_save(path, rows)

    assert '<pos x="7700.0" z="7300.5" />' in path.read_text()
    assert len(psp.table_load(path)["rows"][1]["nested"]["pos"]) == 3


def test_a_template_left_as_it_is_is_refused(tmp_path):
    path = _write(tmp_path)
    payload = psp.table_load(path)

    rows = _sent(payload)
    _edit_tree(rows, payload, "fresh", "groups", lambda g: g.append({"name": "", "pos": []}))
    with pytest.raises(ValueError, match="needs a name"):
        psp.table_save(path, rows)

    rows = _sent(payload)
    _edit_tree(rows, payload, "fresh", "groups", lambda g: g[0]["pos"].append({"x": 0.0, "z": 0.0}))
    with pytest.raises(ValueError, match="0, 0"):
        psp.table_save(path, rows)

    assert path.read_text() == spawns_xml()


def test_a_stale_tree_or_cell_is_refused(tmp_path):
    path = _write(tmp_path)
    payload = psp.table_load(path)
    # Someone moves a position and changes a param after the page loaded.
    path.write_text(
        spawns_xml().replace('x="8040.858398"', 'x="8100.0"').replace(">30</min_dist_infected>", ">35</min_dist_infected>")
    )
    newer = path.read_text()

    rows = _sent(payload)
    _edit_tree(rows, payload, "fresh", "groups", lambda g: g[0].update(name="West"))
    with pytest.raises(ValueError, match="changed in the file since this page loaded it"):
        psp.table_save(path, rows)

    rows = _sent(payload)
    rows[0]["values"]["spawn_params.min_dist_infected"] = "40"
    with pytest.raises(ValueError, match="changed in the file since this page loaded it"):
        psp.table_save(path, rows)

    assert path.read_text() == newer

    # A cell the old page did not touch is not written back over the newer file.
    rows = _sent(payload)
    rows[0]["values"]["spawn_params.max_dist_infected"] = "80"
    psp.table_save(path, rows)
    after = path.read_text()
    assert ">35</min_dist_infected>" in after and ">80</max_dist_infected>" in after
    assert 'x="8100.0"' in after


def test_a_missing_param_is_created_in_its_block_only_when_set(tmp_path):
    path = _write(tmp_path)
    rows = _sent(psp.table_load(path))
    rows[0]["values"]["spawn_params.min_dist_player"] = "65"
    rows[0]["values"]["group_params.groups_as_regular"] = False
    psp.table_save(path, rows)
    after = path.read_text()

    assert "<min_dist_player>65</min_dist_player>" in after
    assert "<groups_as_regular>false</groups_as_regular>" in after
    # The hop section has no generator_params: nothing set there, none made.
    assert after.count("<generator_params>") == 1


def test_bad_values_and_removed_sections_are_refused(tmp_path):
    path = _write(tmp_path)
    payload = psp.table_load(path)

    rows = _sent(payload)
    rows[0]["values"]["generator_params.grid_density"] = "4.5"
    with pytest.raises(ValueError, match="whole number"):
        psp.table_save(path, rows)

    rows = _sent(payload)
    rows[0]["values"]["spawn_params.min_dist_infected"] = "far"
    with pytest.raises(ValueError, match="fresh Spawn: min dist infected: 'far' is not a number"):
        psp.table_save(path, rows)

    rows = _sent(payload)
    rows[0]["values"]["spawn_params.min_dist_infected"] = ""
    with pytest.raises(ValueError, match="empty"):
        psp.table_save(path, rows)

    rows = _sent(payload)
    rows[0]["values"]["section"] = "travel"
    with pytest.raises(ValueError, match="fixed"):
        psp.table_save(path, rows)

    with pytest.raises(ValueError, match="can't be removed"):
        psp.table_save(path, _sent(payload)[:1])

    assert path.read_text() == spawns_xml()


# ---------------------------------------------------------------------------
# Through the web
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(sample_config):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    tclient = TestClient(server.create_app(sample_config))
    tclient.post("/api/auth/login", json={"password": PASSWORD})
    return tclient


def _mission(sample_config) -> Path:
    root = sample_config.server_dir_path / "mpmissions" / "dayzOffline.chernarusplus"
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_the_editor_is_a_table_and_the_raw_tab_edits_the_whole_file(client, sample_config):
    (_mission(sample_config) / "db").mkdir(exist_ok=True)
    path = _mission(sample_config) / "db" / "cfgplayerspawnpoints.xml"
    path.write_text(spawns_xml())

    meta = client.get("/api/configs/playerspawnpoints/meta").json()
    assert meta["kind"] == "table" and meta["raw"] is True

    payload = client.get("/api/configs/playerspawnpoints").json()
    rows = _sent(payload)
    _edit_tree(rows, payload, "fresh", "groups", lambda g: g[0]["pos"][0].update(z=1940.5))
    assert client.put("/api/configs/playerspawnpoints", json={"rows": rows}).status_code == 200
    assert '<pos x="6063.018555" z="1940.500000" />' in path.read_text()

    # The same edit again is now stale.
    resp = client.put("/api/configs/playerspawnpoints", json={"rows": rows})
    assert resp.status_code == 400
    assert "changed in the file" in resp.json()["detail"]

    raw = client.get("/api/configs/playerspawnpoints/raw").json()
    assert raw["content"] == path.read_text()
    content = raw["content"].replace('name="EastCherno"', 'name="Elektro"')
    assert client.put("/api/configs/playerspawnpoints/raw", json={"content": content}).status_code == 200
    assert 'name="Elektro"' in path.read_text()
    assert client.put("/api/configs/playerspawnpoints/raw", json={"content": "<oops>"}).status_code == 400
    assert 'name="Elektro"' in path.read_text()
