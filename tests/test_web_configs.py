"""Tests for /api/configs* and /api/tp-price* (app/web/routers/configs.py,
app/web/editors/*)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.xml.xml_utils import load_xml
from tests.conftest import (
    CE_1_29,
    CE_1_30,
    bohemia_ce,
    ce_block,
    ce_lines,
    ce_pick,
    private_fixture,
    write_test_mods,
)
from tests.test_limitsdefinition_xml import stock_shaped_limits

PASSWORD = "testpass123"

GLOBALS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<globals>
    <var name="CleanupAvoidance" type="0" value="100"/>
    <var name="AnimalMaxCount" type="0" value="200"/>
</globals>
"""

ECONOMY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<economy>
    <dynamic init="1" load="1" respawn="1" save="1"/>
    <animals init="1" load="1" respawn="0" save="1"/>
</economy>
"""

EVENTS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<events>
    <event name="StaticWreck">
        <nominal>5</nominal>
        <min>3</min>
        <max>5</max>
        <lifetime>3600</lifetime>
        <restock>0</restock>
        <saferadius>100</saferadius>
        <distanceradius>500</distanceradius>
        <cleanupradius>1000</cleanupradius>
        <flags deletable="0" init_random="1" remove_damaged="0"/>
        <position>fixed</position>
        <limit>mixed</limit>
        <active>1</active>
    </event>
</events>
"""

IGNORELIST_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ignorelist>
    <item name="Land_Something"/>
</ignorelist>
"""

MESSAGES_XML = """<?xml version="1.0" encoding="UTF-8"?>
<messages>
    <message>
        <repeat>15</repeat>
        <text>Welcome</text>
    </message>
</messages>
"""

ECONOMYCORE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<economycore>
    <defaults>
        <default name="dyn_dmin" value="1"/>
    </defaults>
</economycore>
"""


@pytest.fixture()
def client(sample_config):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    app = server.create_app(sample_config)
    tclient = TestClient(app)
    tclient.post("/api/auth/login", json={"password": PASSWORD})
    return tclient


def _mission_db(sample_config):
    d = sample_config.server_dir_path / "mpmissions" / "dayzOffline.chernarusplus" / "db"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_configs_requires_session(sample_config):
    from app.web import server

    app = server.create_app(sample_config)
    resp = TestClient(app).get("/api/configs")
    assert resp.status_code == 401


def test_list_configs_shape(client):
    resp = client.get("/api/configs")
    assert resp.status_code == 200
    body = resp.json()
    ids = {item["id"] for item in body}
    assert {"serverdz", "battleye", "globals", "events", "economycore", "tp-banking"} <= ids
    serverdz = next(item for item in body if item["id"] == "serverdz")
    assert serverdz["kind"] == "form"
    assert serverdz["exists"] is True


def test_list_configs_names_each_editors_files(client, sample_config):
    """`files` is what the Configs page's cards name and its drawer opens."""
    (_mission_db(sample_config) / "globals.xml").write_text(GLOBALS_XML)

    body = {item["id"]: item for item in client.get("/api/configs").json()}

    globals_files = body["globals"]["files"]
    assert [f["name"] for f in globals_files] == ["globals.xml"]
    assert globals_files[0]["language"] == "xml"
    # Server-dir-relative, forward slashes on every platform.
    assert globals_files[0]["path"].startswith("mpmissions/")
    assert "\\" not in globals_files[0]["path"]

    # BattlEye is the multi-file editor — the drawer's target picker exists for it.
    assert len(body["battleye"]["files"]) == 2

    # runcommand's fields live in zsm's own config.json, so there is no file to
    # preview and no Raw tab.
    assert body["runcommand"]["files"] == []
    assert body["runcommand"]["raw"] is False


def test_list_configs_includes_the_bespoke_pages(client, sample_config):
    d = _make_tp_dir(sample_config)
    (d / "TraderPlusPriceConfig.json").write_text(
        json.dumps({"Version": "2.5", "TraderCategories": []})
    )

    body = {item["id"]: item for item in client.get("/api/configs").json()}
    assert {"mod-xml", "types", "tp-price"} <= set(body)
    assert all(body[k]["kind"] == "custom" for k in ("mod-xml", "types", "tp-price"))
    assert body["types"]["group"] == "Mission"
    assert body["tp-price"]["group"] == "TraderPlus"

    # Only a page backed by a SINGLE file gets a preview eye.
    assert [f["name"] for f in body["tp-price"]["files"]] == ["TraderPlusPriceConfig.json"]
    assert body["tp-price"]["raw"] is True
    assert body["types"]["files"] == [] and body["types"]["raw"] is False
    assert body["mod-xml"]["files"] == [] and body["mod-xml"]["raw"] is False


def test_bespoke_raw_reads_and_writes_tp_price(client, sample_config):
    """The bespoke drawers have Raw and Versions tabs: GET and PUT both serve
    TraderPlus Prices, a malformed file is refused, and Versions lists it."""
    d = _make_tp_dir(sample_config)
    path = d / "TraderPlusPriceConfig.json"
    path.write_text(json.dumps({"Version": "2.5", "TraderCategories": []}))

    resp = client.get("/api/configs/tp-price/raw")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "TraderPlusPriceConfig.json"
    assert body["language"] == "json"
    assert "TraderCategories" in body["content"]

    meta = client.get("/api/configs/tp-price/meta").json()
    assert meta["kind"] == "custom" and meta["raw"] is True
    assert meta["targets"] == ["TraderPlusPriceConfig.json"]

    assert client.put("/api/configs/tp-price/raw", json={"content": "{not json"}).status_code == 400
    assert "TraderCategories" in path.read_text()

    saved = '{"Version": "2.5", "TraderCategories": [], "Note": 1}'
    assert client.put("/api/configs/tp-price/raw", json={"content": saved}).status_code == 200
    assert path.read_text() == saved
    assert client.get("/api/configs/tp-price/versions").status_code == 200


def test_bespoke_raw_lists_every_types_file(client, sample_config):
    """Types' Raw tab picks among db/types.xml and every custom types file,
    labelled by its folder. Mod XML has no raw view."""
    mission = sample_config.server_dir_path / "mpmissions" / "dayzOffline.chernarusplus"
    (mission / "db").mkdir(parents=True, exist_ok=True)
    (mission / "db" / "types.xml").write_text('<types><type name="A"/></types>')
    (mission / "custom").mkdir(exist_ok=True)
    (mission / "custom" / "extra.xml").write_text('<types><type name="B"/></types>')

    meta = client.get("/api/configs/types/meta").json()
    assert meta["targets"] == ["db/types.xml", "custom/extra.xml"]
    resp = client.get("/api/configs/types/raw", params={"target": "custom/extra.xml"})
    assert resp.status_code == 200 and 'name="B"' in resp.json()["content"]

    assert client.put(
        "/api/configs/types/raw", json={"target": "custom/extra.xml", "content": "<types"}
    ).status_code == 400
    assert client.get("/api/configs/mod-xml/raw").status_code == 404
    assert client.put("/api/configs/mod-xml/raw", json={"content": "x"}).status_code == 404


def test_unknown_config_404(client):
    assert client.get("/api/configs/does-not-exist").status_code == 404
    assert client.get("/api/configs/does-not-exist/raw").status_code == 404
    assert client.put("/api/configs/does-not-exist", json={"values": {}}).status_code == 404
    assert client.post("/api/configs/does-not-exist/restore").status_code == 404
    # runcommand is registered but has no file behind it.
    assert client.get("/api/configs/runcommand/raw").status_code == 404


def test_a_server_form_save_without_serverdz_cfg_is_refused(client, sample_config):
    """On an instance with no server installed the
    form answered "Saved." and wrote nothing."""
    sample_config.server_cfg_path.unlink()
    resp = client.put("/api/configs/serverdz", json={"values": {"hostname": "night-exp-test"}})
    assert resp.status_code == 400
    assert "does not exist yet" in resp.json()["detail"]
    assert not sample_config.server_cfg_path.exists()


def test_serverdz_form_round_trip(client, sample_config):
    resp = client.get("/api/configs/serverdz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "form"
    all_keys = {f["key"] for s in body["sections"] for f in s["fields"]}
    assert "hostname" in all_keys
    assert "maxPlayers" in all_keys

    resp = client.put("/api/configs/serverdz", json={"values": {"hostname": "New Hostname", "maxPlayers": "42"}})
    assert resp.status_code == 200

    body = client.get("/api/configs/serverdz").json()
    fields = {f["key"]: f["value"] for s in body["sections"] for f in s["fields"]}
    assert fields["hostname"] == "New Hostname"
    assert fields["maxPlayers"] == 42


@pytest.mark.parametrize("values, message", [
    ({"maxPlayers": "-5"}, "Max players must be at least 1"),
    ({"maxPlayers": "4.5"}, "Max players must be a whole number"),
    ({"maxPlayers": "many"}, "Max players needs a number"),
    ({"vonCodecQuality": "31"}, "VonCodec quality must be between 0 and 30"),
    ({"guaranteedUpdates": "0"}, "Guaranteed updates must be 1"),
    ({"instanceId": "2147483648"}, "Save folder id (instanceId) must be between -2147483648 and 2147483647"),
    ({"serverTimeAcceleration": "0"}, "Time acceleration must be between 0.1 and 64"),
    ({"verifySignatures": "1"}, "Verify signatures must be 2"),
    ({"serverFpsWarning": "5"}, "Server FPS warning must be at least 11"),
    ({"respawnTime": "-1"}, "Re-spawn time must be at least 0"),
    ({"thirdPersonView": "3"}, "3rd person view (1.30+) must be between 0 and 2"),
    ({"lightingConfig": "-1"}, "Lighting config must be at least 0"),
    ({"lightingConfig": "abc"}, "Lighting config needs a number"),
    ({"lightingConfig": "1.5"}, "Lighting config must be a whole number"),
    ({"timeStampFormat": "Long"}, "Timestamp format must be Short or Full"),
    ({"timeStampFormat": "full"}, "Timestamp format must be Short or Full"),
    ({"serverTime": "noon"}, "Time must be SystemTime or a date and time written YYYY/MM/DD/HH/MM, e.g. 2015/4/8/17/23"),
    ({"serverTime": "2015/13/8/17/23"}, "Time must be SystemTime or a date and time written YYYY/MM/DD/HH/MM, e.g. 2015/4/8/17/23"),
    ({"serverTime": "2015/4/8/17"}, "Time must be SystemTime or a date and time written YYYY/MM/DD/HH/MM, e.g. 2015/4/8/17/23"),
    ({"description": "x" * 256}, "Description is 256 characters long; DayZ takes at most 255"),
    ({"networkObjectBatchBandwidthLimit": "-0.1"}, "Network-object batch - Bandwidth limit must be at least 0"),
    ({"networkObjectBatchBandwidthLimit": "inf"}, "Network-object batch - Bandwidth limit needs a number"),
    ({"networkObjectBatchBandwidthLimit": "nan"}, "Network-object batch - Bandwidth limit needs a number"),
])
def test_serverdz_refuses_a_value_the_game_cannot_use(client, sample_config, values, message):
    """F30: maxPlayers = -5 was written as is. Ranges are Bohemia's wiki's."""
    before = sample_config.server_cfg_path.read_bytes()
    resp = client.put("/api/configs/serverdz", json={"values": values})
    assert resp.status_code == 400
    assert resp.json()["detail"] == message
    assert sample_config.server_cfg_path.read_bytes() == before


def test_serverdz_number_left_empty_removes_the_key(client, sample_config):
    """No number has to be filled in: an emptied box takes the key out of the
    file, so the server uses its default, and an absent key stays absent."""
    path = sample_config.server_cfg_path
    assert client.put("/api/configs/serverdz", json={"values": {"maxPlayers": "42"}}).status_code == 200
    assert "maxPlayers = 42;" in path.read_text()
    assert "vonCodecQuality" not in path.read_text()
    resp = client.put("/api/configs/serverdz", json={"values": {"maxPlayers": "", "vonCodecQuality": " "}})
    assert resp.status_code == 200
    text = path.read_text()
    assert "maxPlayers" not in text and "vonCodecQuality" not in text
    fields = {f["key"]: f for s in client.get("/api/configs/serverdz").json()["sections"] for f in s["fields"]}
    assert fields["maxPlayers"]["value"] is None


@pytest.mark.parametrize("value", ["0", "0.8", "1", "250"])
def test_serverdz_takes_a_bandwidth_limit_of_either_kind(client, sample_config, value):
    """server-configuration.wiki:111: a [0,1] value or a [1,inf] value,
    depending on networkObjectBatchUseDynamicMaximumBandwidth."""
    resp = client.put("/api/configs/serverdz", json={"values": {"networkObjectBatchBandwidthLimit": value}})
    assert resp.status_code == 200
    assert f"networkObjectBatchBandwidthLimit = {value};" in sample_config.server_cfg_path.read_text()


def test_serverdz_one_bad_field_writes_nothing(client, sample_config):
    before = sample_config.server_cfg_path.read_bytes()
    resp = client.put("/api/configs/serverdz", json={"values": {"hostname": "Changed", "maxPlayers": "-5"}})
    assert resp.status_code == 400
    assert sample_config.server_cfg_path.read_bytes() == before


@pytest.mark.parametrize("sep", ["\n", "\r", "\x0b", "\x0c", "\x1e", "\x85", " ", " "])
@pytest.mark.parametrize("key, label", [("hostname", "Hostname"), ("password", "Password")])
def test_serverdz_refuses_a_string_that_breaks_its_line(client, sample_config, sep, key, label):
    """The form wrote strings unchecked: a newline added a key of its own, and
    U+2028 made zsm read the key as unset (plans/68 LB-4)."""
    before = sample_config.server_cfg_path.read_bytes()
    resp = client.put(
        "/api/configs/serverdz",
        json={"values": {key: f"My Server;{sep}verifySignatures = 0", "maxPlayers": "40"}},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail.startswith(f"{label} contains ")
    assert "verifySignatures" not in detail  # the value is never quoted back
    assert sample_config.server_cfg_path.read_bytes() == before


def test_serverdz_still_takes_a_tab_in_a_string(client, sample_config):
    resp = client.put("/api/configs/serverdz", json={"values": {"hostname": "My\tServer"}})
    assert resp.status_code == 200, resp.text
    assert 'hostname = "My\tServer";' in sample_config.server_cfg_path.read_text()


# A serverDZ.cfg in the shape the server ships it, its values edited, in the
# private fixtures (servercfg/quoted-like-stock.cfg; plans/86 D2).
def stock_cfg() -> str:
    return private_fixture("servercfg/quoted-like-stock.cfg")


def test_serverdz_writes_text_quoted_like_stock(client, sample_config):
    """plans/68 A-3: the form wrote `hostname = NFL;` over Bohemia's
    `hostname = "NFL";`. Text is quoted, numbers and booleans stay bare."""
    cfg = sample_config.server_cfg_path
    cfg.write_text(stock_cfg())
    resp = client.put("/api/configs/serverdz", json={"values": {
        "hostname": "X", "description": "Y", "password": "p", "passwordAdmin": "a",
        "serverTime": "2015/4/8/17/23", "timeStampFormat": "Full", "logFile": "console.log",
        "maxPlayers": "42", "disableVoN": True, "serverTimeAcceleration": "1.5",
    }})
    assert resp.status_code == 200, resp.text
    assert cfg.read_text() == (
        stock_cfg()
        .replace('"NFL"', '"X"')
        .replace('"Some description"', '"Y"')
        .replace('password = "";', 'password = "p";')
        .replace('passwordAdmin = "";', 'passwordAdmin = "a";')
        .replace('"SystemTime"', '"2015/4/8/17/23"')
        .replace('"Short"', '"Full"')
        .replace('"server_console.log"', '"console.log"')
        .replace("maxPlayers = 60;", "maxPlayers = 42;")
        .replace("disableVoN = 0;", "disableVoN = 1;")
        .replace("serverTimeAcceleration = 1;", "serverTimeAcceleration = 1.5;")
    )


def test_serverdz_round_trips_a_semicolon_in_text(client, sample_config):
    cfg = sample_config.server_cfg_path
    cfg.write_text(stock_cfg())
    resp = client.put("/api/configs/serverdz", json={"values": {"hostname": "[EU] Frosty; PvE", "maxPlayers": "40"}})
    assert resp.status_code == 200, resp.text
    assert 'hostname = "[EU] Frosty; PvE";\t\t\t// Server name\n' in cfg.read_text()
    fields = {f["key"]: f["value"] for s in client.get("/api/configs/serverdz").json()["sections"] for f in s["fields"]}
    assert fields["hostname"] == "[EU] Frosty; PvE"
    assert fields["maxPlayers"] == 40


def test_serverdz_refuses_a_double_quote_in_text(client, sample_config):
    cfg = sample_config.server_cfg_path
    cfg.write_text(stock_cfg())
    before = cfg.read_bytes()
    resp = client.put(
        "/api/configs/serverdz",
        json={"values": {"maxPlayers": "40", "description": 'Say "hi"; verifySignatures = 0'}},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail.startswith("Description contains a double quote")
    assert "verifySignatures" not in detail
    assert cfg.read_bytes() == before


def test_serverdz_quotes_text_an_older_zsm_wrote_bare(client, sample_config):
    """A bare value with no `;` reads back as written, and the next save of
    that field quotes it."""
    cfg = sample_config.server_cfg_path
    cfg.write_text(stock_cfg().replace('"NFL"', "My Server").replace('"Short"', "Short"))
    fields = {f["key"]: f["value"] for s in client.get("/api/configs/serverdz").json()["sections"] for f in s["fields"]}
    assert (fields["hostname"], fields["timeStampFormat"]) == ("My Server", "Short")

    resp = client.put("/api/configs/serverdz", json={"values": {"hostname": "My Server"}})
    assert resp.status_code == 200, resp.text
    text = cfg.read_text()
    assert 'hostname = "My Server";' in text
    assert "timeStampFormat = Short;" in text  # a field not saved is left as it was


def test_serverdz_numbers_carry_their_range(client):
    fields = {f["key"]: f for s in client.get("/api/configs/serverdz").json()["sections"] for f in s["fields"]}
    assert (fields["maxPlayers"]["min"], fields["maxPlayers"].get("max")) == (1, None)
    # The serverDZ.cfg 1.29 ships says "(values 0-30)", the wiki 0-20; the
    # file's range is the one used.
    assert (fields["vonCodecQuality"]["min"], fields["vonCodecQuality"]["max"]) == (0, 30)
    # Wiki: "(use only number 1)".
    guaranteed = fields["guaranteedUpdates"]
    assert (guaranteed["type"], guaranteed["min"], guaranteed["max"]) == ("int", 1, 1)
    assert fields["speedhackDetection"]["type"] == "float"  # the wiki: "can be float"
    assert "min" not in fields["hostname"]


def test_serverdz_names_the_default_of_a_key_the_file_leaves_out(client, sample_config):
    """The form shows the server's default for an absent key instead of a
    blank, and the file stays without it until the admin changes the field."""
    path = sample_config.server_cfg_path
    text = "\n".join(l for l in path.read_text().splitlines() if not l.strip().startswith("timeStampFormat"))
    path.write_text(text + "\n")
    fields = {f["key"]: f for s in client.get("/api/configs/serverdz").json()["sections"] for f in s["fields"]}
    stamp = fields["timeStampFormat"]
    assert (stamp["value"], stamp["default"]) == (None, "Short")
    assert fields["instanceId"]["default"] == 1
    # Both are only the values in the wiki's example file: no "Default" pill.
    assert stamp["default_is_example"] is True and fields["instanceId"]["default_is_example"] is True
    # networkRangeClose's wiki line names its default ("default value if not set is 20").
    assert fields["networkRangeClose"]["default"] == 20 and "default_is_example" not in fields["networkRangeClose"]
    # instanceId names storage_<id>, not a Zedstead instance: the field says so.
    assert fields["instanceId"]["link"]["to"] == "/instances"
    assert "readonly" not in fields["instanceId"]
    assert "default" not in fields["logAverageFps"]  # its help says "not set"
    assert client.put("/api/configs/serverdz", json={"values": {"hostname": "Other"}}).status_code == 200
    assert "timeStampFormat" not in path.read_text()


def test_serverdz_offers_third_person_view_from_1_30(client, sample_config):
    """plans/66: 1.30's stock serverDZ.cfg ships thirdPersonView (0 off, 1 on,
    2 only in vehicles) in place of disable3rdPerson. A 1.29 file has no such
    key, and a save that does not touch it must not add one."""
    fields = {f["key"]: f for s in client.get("/api/configs/serverdz").json()["sections"] for f in s["fields"]}
    view = fields["thirdPersonView"]
    assert (view["type"], view["min"], view["max"]) == ("int", 0, 2)
    assert view["value"] is None
    assert "1.30" in view["help"]

    resp = client.put("/api/configs/serverdz", json={"values": {"hostname": "Changed"}})
    assert resp.status_code == 200, resp.text
    assert "thirdPersonView" not in sample_config.server_cfg_path.read_text()

    from app.parsers.servercfg import read_cfg_value

    resp = client.put("/api/configs/serverdz", json={"values": {"thirdPersonView": "2"}})
    assert resp.status_code == 200, resp.text
    assert read_cfg_value(sample_config.server_cfg_path, "thirdPersonView") == "2"


@pytest.mark.parametrize("value", ["223", "222", "2", "0"])
def test_serverdz_takes_a_terrain_mods_own_lighting(client, sample_config, value):
    """Namalsk's recommended cfgs set lightingConfig 223 and 222, and a map
    switch writes them; the form must be able to save them too."""
    from app.parsers.servercfg import read_cfg_value

    resp = client.put("/api/configs/serverdz", json={"values": {"lightingConfig": value}})
    assert resp.status_code == 200, resp.text
    assert read_cfg_value(sample_config.server_cfg_path, "lightingConfig") == value

    fields = {f["key"]: f for s in client.get("/api/configs/serverdz").json()["sections"] for f in s["fields"]}
    assert (fields["lightingConfig"]["min"], fields["lightingConfig"].get("max")) == (0, None)


def test_serverdz_accepts_the_edges(client):
    resp = client.put("/api/configs/serverdz", json={"values": {
        "maxPlayers": "1", "serverTimeAcceleration": "64", "speedhackDetection": "2.5", "vonCodecQuality": "30",
    }})
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("key, value", [
    ("timeStampFormat", "Short"),
    ("timeStampFormat", "Full"),
    ("serverTime", "SystemTime"),
    ("serverTime", "2015/4/8/17/23"),       # the wiki's own example, :39
    ("serverTime", "2024/12/31/23/59"),
    ("description", "x" * 255),
])
def test_serverdz_takes_what_the_wiki_allows_in_text(client, sample_config, key, value):
    """server-configuration.wiki:17, :38-39 and :78."""
    from app.parsers.servercfg import read_cfg_value

    resp = client.put("/api/configs/serverdz", json={"values": {key: value}})
    assert resp.status_code == 200, resp.text
    assert read_cfg_value(sample_config.server_cfg_path, key) == value


NEW_WIKI_KEYS = {
    # server-configuration.wiki:76 and :107-111; gameplay-settings.wiki:12.
    "motdInterval": "float",
    "networkObjectBatchLogSlow": "float",
    "networkObjectBatchEnforceBandwidthLimits": "bool",
    "networkObjectBatchUseEstimatedBandwidth": "bool",
    "networkObjectBatchUseDynamicMaximumBandwidth": "bool",
    "networkObjectBatchBandwidthLimit": "float",
    "enableCfgGameplayFile": "bool",
}
GONE_KEYS = [
    # In no Bohemia document, so they were removed.
    "networkObjectBatchKiloBytes", "networkObjectBatchTimeLimit",
    "networkObjectBatchPriorityDistanceFactor", "networkObjectBatchMaxDistanceFactor",
    "networkObjectBatchLogStats", "logBuildActions",
]


@pytest.mark.parametrize("raw, expected", [
    ("false", False), ("FALSE", False), ("False", False), (" 0 ", False), ("", False),
    ("true", True), ("TRUE", True), ("1", True),
])
def test_serverdz_reads_a_switch_in_any_case(raw, expected):
    from app.web.editors.server_forms import _coerce

    assert _coerce("bool", raw) is expected


@pytest.mark.parametrize("before, after", [
    # server-configuration.wiki:22 writes `disableBanlist = false;`
    ("disableBanlist = false;", "disableBanlist = true;"),
    ("disableBanlist = FALSE;", "disableBanlist = TRUE;"),
    ("disableBanlist = False;", "disableBanlist = True;"),
    ("disableBanlist = 0;", "disableBanlist = 1;"),
])
def test_serverdz_writes_a_switch_in_the_file_s_own_style(client, sample_config, before, after):
    cfg = sample_config.server_cfg_path
    cfg.write_text(f'hostname = "X";\n{before}\n')
    resp = client.put("/api/configs/serverdz", json={"values": {"disableBanlist": True}})
    assert resp.status_code == 200, resp.text
    assert cfg.read_text() == f'hostname = "X";\n{after}\n'


def test_serverdz_writes_an_absent_word_switch_as_bohemia_does(client, sample_config):
    """wiki:22-23 and :89 write these three as true/false; any other switch is 0/1."""
    from app.parsers.servercfg import read_cfg_values

    cfg = sample_config.server_cfg_path
    cfg.write_text('hostname = "X";\n')
    resp = client.put("/api/configs/serverdz", json={"values": {
        "disablePrioritylist": True, "disableMultiAccountMitigation": False, "disableVoN": True,
    }})
    assert resp.status_code == 200, resp.text
    assert read_cfg_values(cfg, ["disablePrioritylist", "disableMultiAccountMitigation", "disableVoN"]) == {
        "disablePrioritylist": "true", "disableMultiAccountMitigation": "false", "disableVoN": "1",
    }


def test_serverdz_offers_the_keys_the_wiki_documents(client, sample_config):
    body = client.get("/api/configs/serverdz").json()
    fields = {f["key"]: f for s in body["sections"] for f in s["fields"]}
    for key, kind in NEW_WIKI_KEYS.items():
        assert fields[key]["type"] == kind
        assert fields[key]["help"], key
    for key in GONE_KEYS:
        assert key not in fields


def test_serverdz_writes_a_new_key_the_file_lacks(client, sample_config):
    from app.parsers.servercfg import read_cfg_values

    resp = client.put("/api/configs/serverdz", json={"values": {
        "networkObjectBatchBandwidthLimit": "0.8", "enableCfgGameplayFile": True, "motdInterval": "30",
    }})
    assert resp.status_code == 200, resp.text
    assert read_cfg_values(sample_config.server_cfg_path, [
        "networkObjectBatchBandwidthLimit", "enableCfgGameplayFile", "motdInterval",
    ]) == {"networkObjectBatchBandwidthLimit": "0.8", "enableCfgGameplayFile": "1", "motdInterval": "30"}


def test_serverdz_offers_the_timestamp_formats_as_a_choice(client, sample_config):
    body = client.get("/api/configs/serverdz").json()
    fields = {f["key"]: f for s in body["sections"] for f in s["fields"]}
    assert fields["timeStampFormat"]["choices"] == ["Short", "Full"]
    assert "choices" not in fields["hostname"]


@pytest.mark.parametrize("value", ["-1", "0", "2147483647", "-2147483648"])
def test_serverdz_takes_any_32_bit_instance_id(client, sample_config, value):
    """instanceId must be a 32-bit integer; -1 selects storage_-1 (measured on 1.29)."""
    from app.parsers.servercfg import read_cfg_value

    resp = client.put("/api/configs/serverdz", json={"values": {"instanceId": value}})
    assert resp.status_code == 200, resp.text
    assert read_cfg_value(sample_config.server_cfg_path, "instanceId") == value


MISSIONS_CFG = """hostname = "Test";
maxPlayers = 60;
class Missions
{
    class DayZ
    {
        template="dayzOffline.chernarusplus"; // the mission
    };
};
"""


def test_serverdz_shows_the_mission_read_only(client, sample_config):
    """F31: `template` lives in class Missions, and the field was empty."""
    sample_config.server_cfg_path.write_text(MISSIONS_CFG)
    fields = {f["key"]: f for s in client.get("/api/configs/serverdz").json()["sections"] for f in s["fields"]}
    mission = fields["template"]
    assert mission["value"] == "dayzOffline.chernarusplus"
    assert mission["readonly"] is True
    assert mission["link"]["to"] == "/maps"


def test_serverdz_never_writes_the_mission(client, sample_config):
    sample_config.server_cfg_path.write_text(MISSIONS_CFG)
    resp = client.put("/api/configs/serverdz", json={"values": {"template": "dayzOffline.enoch", "maxPlayers": "40"}})
    assert resp.status_code == 200
    text = sample_config.server_cfg_path.read_text()
    assert 'template="dayzOffline.chernarusplus"; // the mission' in text
    assert "dayzOffline.enoch" not in text
    assert "maxPlayers = 40;" in text


def test_battleye_form_round_trip(client):
    resp = client.get("/api/configs/battleye")
    assert resp.status_code == 200
    fields = {f["key"]: f["value"] for s in resp.json()["sections"] for f in s["fields"]}
    assert fields["RConPassword"] == "mypass"
    assert fields["RConPort"] == 2305

    resp = client.put("/api/configs/battleye", json={"values": {"RConPassword": "newpass"}})
    assert resp.status_code == 200
    fields = {f["key"]: f["value"] for s in client.get("/api/configs/battleye").json()["sections"] for f in s["fields"]}
    assert fields["RConPassword"] == "newpass"


def test_battleye_save_sets_the_password_zsm_dials_with(client, sample_config):
    """Outside Docker nothing else sets config.rcon_password/rcon_port, so a
    save that wrote only beserver_x64.cfg left zsm "not configured" on a
    normal install (measured on a VPS, 2026 Q3)."""
    from app.parsers.battleye_cfg import read_beserver_cfg
    from app.rcon.rcon_ops import check_rcon_setup
    from app.shared.config import DayzConfig
    from app.shared.config_versions import load_index

    assert sample_config.rcon_password == ""
    resp = client.put(
        "/api/configs/battleye", json={"values": {"RConPassword": "newpass", "RConPort": "2310"}}
    )
    assert resp.status_code == 200, resp.text

    be_path = sample_config.beserver_cfg_path
    be = read_beserver_cfg(be_path)
    assert (be["RConPassword"], be["RConPort"]) == ("newpass", "2310")
    live = client.app.state.ctx.config
    assert (live.rcon_password, live.rcon_port) == ("newpass", 2310)
    saved = DayzConfig.load()
    assert (saved.rcon_password, saved.rcon_port) == ("newpass", 2310)
    assert check_rcon_setup(live) == []
    # The version snapshot is still taken before the write.
    assert load_index(sample_config.paths.versions_dir).get_versions(be_path.name, source_path=str(be_path))


def test_battleye_switch_is_a_header_toggle(client):
    """The form draws BattlEye as its card's On/Off button, the rest as fields."""
    data = client.get("/api/configs/battleye").json()
    fields = [f for s in data["sections"] for f in s["fields"]]
    assert [f["key"] for f in fields if f.get("widget") == "header"] == ["BattlEye"]


def test_battleye_unrelated_field_saves_as_before(client, sample_config):
    """Changing one field writes that field and keeps the file's others; zsm
    picks up the password already in the file, so the two agree."""
    from app.parsers.battleye_cfg import read_beserver_cfg
    from app.parsers.servercfg import read_cfg_value
    from app.rcon.rcon_ops import check_rcon_setup

    resp = client.put(
        "/api/configs/battleye", json={"values": {"RestrictRCon": True, "BattlEye": True}}
    )
    assert resp.status_code == 200, resp.text

    be = read_beserver_cfg(sample_config.beserver_cfg_path)
    assert be == {"RConPassword": "mypass", "RConPort": "2305", "RestrictRCon": "1"}
    assert read_cfg_value(sample_config.server_cfg_path, "BattlEye") == "1"
    live = client.app.state.ctx.config
    assert (live.rcon_password, live.rcon_port) == ("mypass", 2305)
    assert check_rcon_setup(live) == []


def test_battleye_missing_port_defaults_to_the_instance_port(client, sample_config):
    """A beserver_x64.cfg without RConPort shows this instance's rcon_port,
    and turning BattlEye on writes it, so RCon works without typing it."""
    from app.parsers.battleye_cfg import read_beserver_cfg

    sample_config.beserver_cfg_path.write_text("RConPassword mypass\n")
    state = client.app.state.ctx
    state.config.rcon_port = 2315
    state.config.save()

    assert _battleye_fields(client)["RConPort"] == 2315
    resp = client.put("/api/configs/battleye", json={"values": {"BattlEye": True}})
    assert resp.status_code == 200, resp.text
    assert read_beserver_cfg(sample_config.beserver_cfg_path)["RConPort"] == "2315"


def test_battleye_missing_port_stays_empty_for_a_remote_host(client, sample_config):
    """With rcon_host elsewhere, config.rcon_port is the rented server's port."""
    sample_config.beserver_cfg_path.write_text("RConPassword mypass\n")
    state = client.app.state.ctx
    state.config.rcon_host = "203.0.113.7"
    state.config.save()

    assert _battleye_fields(client)["RConPort"] is None
    resp = client.put("/api/configs/battleye", json={"values": {"RestrictRCon": True}})
    assert resp.status_code == 200, resp.text
    assert "RConPort" not in sample_config.beserver_cfg_path.read_text()


def test_battleye_bad_port_changes_nothing(client, sample_config):
    be_path = sample_config.beserver_cfg_path
    before = be_path.read_text()
    resp = client.put(
        "/api/configs/battleye", json={"values": {"RConPassword": "newpass", "RConPort": "99999"}}
    )
    assert resp.status_code == 400
    assert "RCon port" in resp.json()["detail"]
    assert be_path.read_text() == before
    assert client.app.state.ctx.config.rcon_password == ""


@pytest.mark.parametrize("sep", ["\n", "\x0b", "\x85", "\u2028"])
@pytest.mark.parametrize("key, label", [("RConPassword", "RCon password"), ("RConIP", "RCon IP (optional)")])
def test_battleye_refuses_a_value_that_breaks_its_line(client, sample_config, sep, key, label):
    """Refused before the write and before zsm adopts the password, so
    neither the file nor config.rcon_password changes (plans/68 LB-5)."""
    be_path = sample_config.beserver_cfg_path
    before = be_path.read_text()
    resp = client.put("/api/configs/battleye", json={"values": {key: f"pw{sep}RConIP 0.0.0.0"}})
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail.startswith(f"{label} contains ")
    assert "0.0.0.0" not in detail
    assert be_path.read_text() == before
    assert client.app.state.ctx.config.rcon_password == ""


def test_battleye_never_writes_a_key_only_zsm_could_see(client, sample_config):
    """A password holding U+2028 is one line to BattlEye. zsm reads it the
    same way now, and a save of another field refuses rather than writing the
    text after the separator as an RConIP line of its own (plans/68 LB-5)."""
    be_path = sample_config.beserver_cfg_path
    be_path.write_text("RConPassword pw\u2028RConIP 0.0.0.0\nRConPort 2305\n", encoding="utf-8")
    before = be_path.read_bytes()

    fields = {f["key"]: f["value"] for s in client.get("/api/configs/battleye").json()["sections"] for f in s["fields"]}
    assert (fields["RConPassword"], fields["RConIP"]) == ("pw\u2028RConIP 0.0.0.0", "")

    resp = client.put("/api/configs/battleye", json={"values": {"RestrictRCon": True}})
    assert resp.status_code == 400
    assert resp.json()["detail"].startswith("RCon password contains the character U+2028")
    assert be_path.read_bytes() == before


def test_battleye_save_keeps_a_remote_hosts_password(client, sample_config):
    """With rcon_host elsewhere this machine's file belongs to another install."""
    state = client.app.state.ctx
    state.config.rcon_host = "203.0.113.7"
    state.config.rcon_password = "the-remote-password"
    state.config.save()

    resp = client.put("/api/configs/battleye", json={"values": {"RConPassword": "local"}})
    assert resp.status_code == 200, resp.text
    assert "RConPassword local" in sample_config.beserver_cfg_path.read_text()
    assert state.config.rcon_password == "the-remote-password"


def test_battleye_form_does_not_promise_a_loopback_rcon(client):
    """The BattlEye form is where an admin sets RCon, so its text has to match
    what the server does. Measured on a running server: with RConIP unset it
    listens on 0.0.0.0 (app/ops/CLAUDE.md). Bohemia's docs describe
    RestrictRCon as a switch for RCon functions, not an address filter."""
    body = client.get("/api/configs/battleye").json()
    fields = {f["key"]: f for s in body["sections"] for f in s["fields"]}

    ip_help = fields["RConIP"]["help"]
    assert "0.0.0.0" in ip_help
    assert "default (127.0.0.1)" not in ip_help

    restrict = fields["RestrictRCon"]
    for text in (restrict["label"], restrict["help"]):
        assert "loopback" not in text.lower()
        assert "127.0.0.1" not in text

    assert "unencrypted" in fields["RConPassword"]["help"].lower()


# While the server runs BattlEye has renamed beserver_x64.cfg to its own
# active copy and puts that back at the next stop (measured on a VPS,
# 2026 Q3): a save made then was silently lost. `battleye_running` is that
# shape — see tests/conftest.py.


def _battleye_fields(client) -> dict:
    body = client.get("/api/configs/battleye").json()
    return {f["key"]: f["value"] for s in body["sections"] for f in s["fields"]}


def test_battleye_form_shows_battleyes_copy_while_running(client, battleye_running):
    fields = _battleye_fields(client)
    assert (fields["RConPassword"], fields["RConPort"]) == ("livepass", 2306)
    listed = {c["id"]: c for c in client.get("/api/configs").json()}
    assert listed["battleye"]["exists"] is True


def test_battleye_raw_tab_shows_battleyes_copy_while_running(client, sample_config, battleye_running):
    body = client.get("/api/configs/battleye/raw").json()
    assert body["name"] == sample_config.beserver_cfg_path.name
    assert body["content"] == battleye_running.read_text()


def test_battleye_save_is_refused_while_running(client, sample_config, battleye_running):
    from app.shared.config import DayzConfig
    from app.shared.config_versions import load_index

    server_cfg = sample_config.server_cfg_path.read_text()
    active = battleye_running.read_text()
    resp = client.put(
        "/api/configs/battleye",
        json={"values": {"RConPassword": "newpass", "RConIP": "127.0.0.1", "BattlEye": True}},
    )
    assert resp.status_code == 409, resp.text
    assert "Stop the server first." in resp.json()["detail"]
    # Nothing written, nothing snapshotted, config.json not saved.
    assert not sample_config.beserver_cfg_path.exists()
    assert battleye_running.read_text() == active
    assert sample_config.server_cfg_path.read_text() == server_cfg
    assert load_index(sample_config.paths.versions_dir).entries == []
    assert DayzConfig.load_or_none() is None
    assert client.app.state.ctx.config.rcon_password == ""


def test_battleye_save_when_stopped_is_unchanged(client, sample_config, server_stopped):
    from app.parsers.battleye_cfg import read_beserver_cfg

    resp = client.put(
        "/api/configs/battleye", json={"values": {"RConPassword": "newpass", "RConIP": "127.0.0.1"}}
    )
    assert resp.status_code == 200, resp.text
    assert read_beserver_cfg(sample_config.beserver_cfg_path) == {
        "RConPassword": "newpass", "RConPort": "2305", "RConIP": "127.0.0.1",
    }
    assert client.app.state.ctx.config.rcon_password == "newpass"


def test_a_globals_number_shows_the_unit_its_help_names(client, sample_config):
    """CleanupAvoidance's help says meters, and its box said nothing."""
    (_mission_db(sample_config) / "globals.xml").write_text(GLOBALS_XML)
    fields = {f["key"]: f for s in client.get("/api/configs/globals").json()["sections"] for f in s["fields"]}
    assert fields["CleanupAvoidance"]["unit"] == "meters"
    assert "unit" not in fields["AnimalMaxCount"]


def test_a_globals_on_off_var_is_a_switch(client, sample_config):
    """FoodDecay is 0 or 1, and its form field was a number box."""
    xml = GLOBALS_XML.replace("</globals>", '    <var name="FoodDecay" type="0" value="1"/>\n</globals>')
    (_mission_db(sample_config) / "globals.xml").write_text(xml)
    fields = {f["key"]: f for s in client.get("/api/configs/globals").json()["sections"] for f in s["fields"]}
    assert fields["FoodDecay"]["widget"] == "flag"
    assert "widget" not in fields["AnimalMaxCount"]
    assert client.put("/api/configs/globals", json={"values": {"FoodDecay": 0}}).status_code == 200
    assert '<var name="FoodDecay" type="0" value="0"/>' in (_mission_db(sample_config) / "globals.xml").read_text()


def test_globals_form_round_trip(client, sample_config):
    db = _mission_db(sample_config)
    (db / "globals.xml").write_text(GLOBALS_XML)

    resp = client.get("/api/configs/globals")
    assert resp.status_code == 200
    fields = {f["key"]: f["value"] for s in resp.json()["sections"] for f in s["fields"]}
    assert fields["CleanupAvoidance"] == 100

    resp = client.put("/api/configs/globals", json={"values": {"CleanupAvoidance": "150"}})
    assert resp.status_code == 200
    fields = {f["key"]: f["value"] for s in client.get("/api/configs/globals").json()["sections"] for f in s["fields"]}
    assert fields["CleanupAvoidance"] == 150


# Lines of Bohemia's db/globals.xml (dayzOffline.chernarusplus), read at the
# pinned 1.29 commit, each as the file writes it.
def stock_globals_xml() -> str:
    lines = ce_lines(bohemia_ce(CE_1_29, "dayzOffline.chernarusplus/db/globals.xml"))
    return "".join(ce_pick(
        lines, "<?xml", "<variables>", 'name="LootDamageMax"', 'name="LootDamageMin"',
        'name="TimeLogin"', 'name="ZombieMaxCount"', "</variables>",
    ))


def _globals_fields(client):
    body = client.get("/api/configs/globals").json()
    return {f["key"]: f for s in body["sections"] for f in s["fields"]}


def test_globals_form_types_each_field_and_states_the_wiki_bounds(client, sample_config):
    (_mission_db(sample_config) / "globals.xml").write_text(stock_globals_xml())

    fields = _globals_fields(client)

    assert (fields["LootDamageMax"]["type"], fields["LootDamageMax"]["value"]) == ("float", 0.82)
    assert (fields["LootDamageMax"]["min"], fields["LootDamageMax"]["max"]) == (0.0, 1.0)
    assert (fields["TimeLogin"]["type"], fields["TimeLogin"]["max"]) == ("int", 65536)
    assert fields["ZombieMaxCount"]["min"] == 0 and "max" not in fields["ZombieMaxCount"]


def test_globals_form_saves_an_untouched_float_byte_for_byte(client, sample_config):
    path = _mission_db(sample_config) / "globals.xml"
    path.write_text(stock_globals_xml())
    values = {k: f["value"] for k, f in _globals_fields(client).items()}

    resp = client.put("/api/configs/globals", json={"values": values})
    assert resp.status_code == 200, resp.text
    assert path.read_text() == stock_globals_xml()  # "0.0" was sent back as 0


@pytest.mark.parametrize("key, value, words", [
    ("LootDamageMax", 1.5, "0 to 1"),          # central-economy-configuration.wiki:179
    ("LootDamageMin", -0.1, "0 to 1"),         # :173
    ("TimeLogin", 70000, "0 to 65536"),        # :132
    ("ZombieMaxCount", -5, "at least 0"),
    ("ZombieMaxCount", "lots", "whole number"),
    ("ZombieMaxCount", 1.5, "whole number"),
    ("LootDamageMax", "nan", "a number"),
])
def test_globals_form_refuses_a_value_the_type_or_the_wiki_rules_out(client, sample_config, key, value, words):
    path = _mission_db(sample_config) / "globals.xml"
    path.write_text(stock_globals_xml())

    resp = client.put("/api/configs/globals", json={"values": {"TimeLogin": 30, key: value}})
    assert resp.status_code == 400
    assert words in resp.json()["detail"]
    assert path.read_text() == stock_globals_xml()  # TimeLogin was not written either


CUSTOM_GLOBALS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<variables>
    <var name="ZombieMaxCount" type="0" value="1500"/>
    <var name="LootDamageMin" type="1" value="0.0"/>
</variables>
"""


@pytest.mark.parametrize("row, words", [
    ({"name": "MyVar", "type": "7", "value": "1"}, "type must be 0"),
    ({"name": "MyVar", "type": "0", "value": "lots"}, "whole number"),
    ({"name": "LootDamageMax", "type": "0", "value": "1"}, "is float"),
    ({"name": "TimeLogout", "type": "0", "value": "99999"}, "0 to 65536"),
])
def test_custom_globals_table_refuses_what_the_wiki_rules_out(tmp_path, row, words):
    from app.web.editors.ce_tables import globals_load, globals_save

    path = tmp_path / "mod-globals.xml"
    path.write_text(CUSTOM_GLOBALS_XML)
    rows = globals_load(path)["rows"]

    with pytest.raises(ValueError, match=words):
        globals_save(path, [*rows, {"id": NEW_ROW_ID, "values": row}])
    assert path.read_text() == CUSTOM_GLOBALS_XML


def test_custom_globals_table_keeps_each_variables_type(tmp_path):
    """"Types of existing variables should never be modified"
    (central-economy-mission-files-modding.wiki:49)."""
    from app.web.editors.ce_tables import globals_load, globals_save

    path = tmp_path / "mod-globals.xml"
    path.write_text(CUSTOM_GLOBALS_XML)
    rows = globals_load(path)["rows"]
    retyped = [
        {**r, "values": {**r["values"], "type": "1"}} if r["id"] == "ZombieMaxCount" else r for r in rows
    ]

    with pytest.raises(ValueError, match="type of an existing variable"):
        globals_save(path, retyped)
    globals_save(path, rows)  # untouched rows write nothing new
    assert path.read_text() == CUSTOM_GLOBALS_XML

    globals_save(path, [*rows, {"id": NEW_ROW_ID, "values": {"name": "MyVar", "type": "2", "value": "any text"}}])
    assert '<var name="MyVar" type="2" value="any text"' in path.read_text()


def test_economy_table_round_trip(client, sample_config):
    db = _mission_db(sample_config)
    (db / "economy.xml").write_text(ECONOMY_XML)

    resp = client.get("/api/configs/economy")
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "table"
    rows = {r["id"]: r["values"] for r in body["rows"]}
    assert rows["dynamic"]["init"] is True
    assert rows["animals"]["respawn"] is False

    rows["animals"]["respawn"] = True
    resp = client.put("/api/configs/economy", json={"rows": list(rows_to_rows(rows))})
    assert resp.status_code == 200

    body = client.get("/api/configs/economy").json()
    rows2 = {r["id"]: r["values"] for r in body["rows"]}
    assert rows2["animals"]["respawn"] is True


def rows_to_rows(rows_by_id):
    return [{"id": rid, "values": values} for rid, values in rows_by_id.items()]


def test_events_table_round_trip(client, sample_config):
    db = _mission_db(sample_config)
    (db / "events.xml").write_text(EVENTS_XML)

    resp = client.get("/api/configs/events")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["values"]["name"] == "StaticWreck"

    resp = client.put(
        "/api/configs/events",
        json={"rows": [{"id": "StaticWreck", "values": {"name": "StaticWreck", "nominal": 10, "active": False}}]},
    )
    assert resp.status_code == 200

    rows = client.get("/api/configs/events").json()["rows"]
    assert rows[0]["values"]["nominal"] == 10
    assert rows[0]["values"]["active"] is False


def test_ignorelist_table_full_replace(client, sample_config):
    db = _mission_db(sample_config)
    (db / "cfgIgnoreList.xml").write_text(IGNORELIST_XML)

    resp = client.get("/api/configs/ignorelist")
    assert resp.status_code == 200
    assert len(resp.json()["rows"]) == 1

    resp = client.put(
        "/api/configs/ignorelist",
        json={"rows": [{"id": "a", "values": {"name": "Land_A"}}, {"id": "b", "values": {"name": "Land_B"}}]},
    )
    assert resp.status_code == 200
    rows = client.get("/api/configs/ignorelist").json()["rows"]
    names = {r["values"]["name"] for r in rows}
    assert names == {"Land_A", "Land_B"}


def test_ignorelist_trims_an_added_name(client, sample_config):
    """A pasted " Fence " was written with its spaces, and no class matched it."""
    path = _mission_db(sample_config) / "cfgIgnoreList.xml"
    path.write_text(IGNORELIST_XML)
    rows = client.get("/api/configs/ignorelist").json()["rows"]
    rows.append({"id": "new", "values": {"name": "  Fence\t"}})

    assert client.put("/api/configs/ignorelist", json={"rows": rows}).status_code == 200
    names = [r["values"]["name"] for r in client.get("/api/configs/ignorelist").json()["rows"]]
    assert "Fence" in names and len(names) == 2
    assert 'name="Fence"' in path.read_text()


def test_ignorelist_help_says_listed_items_are_wiped(client, sample_config):
    """The column help said a listed class "won't be despawned", the opposite
    of what the game does (ignorelist_xml.DESCRIPTION)."""
    (_mission_db(sample_config) / "cfgIgnoreList.xml").write_text(IGNORELIST_XML)
    col = client.get("/api/configs/ignorelist").json()["columns"][0]
    assert "does not spawn" in col["help"] and "wiped" in col["help"]
    assert "won't be despawned" not in col["help"]


def test_messages_table_round_trip(client, sample_config):
    db = _mission_db(sample_config)
    (db / "messages.xml").write_text(MESSAGES_XML)

    resp = client.get("/api/configs/messages")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["values"]["text"] == "Welcome"

    resp = client.put(
        "/api/configs/messages",
        json={
            "rows": [
                {"id": "0", "values": {"type": "Shutdown", "text": "Bye", "deadline": "300"}},
                {"id": "1", "values": {"type": "Other", "text": "Second message"}},
            ]
        },
    )
    assert resp.status_code == 200
    rows = client.get("/api/configs/messages").json()["rows"]
    assert len(rows) == 2
    assert rows[0]["values"]["type"] == "Shutdown"


def test_messages_save_keeps_the_flags_the_table_has_no_column_for(client, sample_config):
    """A message that greets on connect and repeats lost its repeat on every
    save, and one that spelled the flag `onconnect`, as the examples in
    Bohemia's stock file do, lost the flag: it was then never sent."""
    db = _mission_db(sample_config)
    (db / "messages.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<messages>
    <message>
        <delay>10</delay>
        <repeat>50</repeat>
        <onconnect>1</onconnect>
        <text>Greets, then repeats.</text>
    </message>
    <message>
        <delay>2</delay>
        <onconnect>1</onconnect>
        <text>Greets once.</text>
    </message>
    <message>
        <repeat>15</repeat>
        <text>Repeats.</text>
    </message>
</messages>
"""
    )

    rows = client.get("/api/configs/messages").json()["rows"]
    assert [r["values"]["type"] for r in rows] == ["OnConnect", "OnConnect", "Repeat"]
    assert client.put("/api/configs/messages", json={"rows": rows}).status_code == 200

    messages = load_xml(db / "messages.xml").getroot().findall("message")
    flags = [
        {child.tag: (child.text or "").strip() for child in msg if child.tag != "text"}
        for msg in messages
    ]
    assert flags == [
        {"delay": "10", "repeat": "50", "onconnect": "1"},
        {"delay": "2", "onconnect": "1"},
        {"repeat": "15"},
    ]
    assert client.get("/api/configs/messages").json()["rows"] == rows


def test_a_new_on_connect_message_is_written_as_the_wiki_spells_it(client, sample_config):
    db = _mission_db(sample_config)
    (db / "messages.xml").write_text(MESSAGES_XML)
    resp = client.put(
        "/api/configs/messages",
        json={"rows": [{"values": {"type": "OnConnect", "text": "Hello", "delay": "0"}}]},
    )
    assert resp.status_code == 200
    (message,) = load_xml(db / "messages.xml").getroot().findall("message")
    assert message.findtext("onConnect") == "1"
    assert message.find("repeat") is None


def test_messages_refuses_a_character_xml_cannot_hold(client, sample_config):
    """`Restart soon\\x0b!` was saved, and messages.xml then no longer parsed
    (plans/68 A-2). The save is refused, naming the element."""
    from app.xml.xml_utils import load_xml

    db = _mission_db(sample_config)
    (db / "messages.xml").write_text(MESSAGES_XML)
    before = (db / "messages.xml").read_bytes()

    resp = client.put(
        "/api/configs/messages",
        json={"rows": [{"id": "0", "values": {"type": "Other", "text": "Restart soon\x0b!"}}]},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "<text> contains the character U+000B, which an XML file cannot hold."
    assert (db / "messages.xml").read_bytes() == before
    load_xml(db / "messages.xml")


def test_messages_times_are_labelled_in_minutes(client, sample_config):
    """Delay and Deadline read as seconds until 2026 Q3, so a 10-hour restart
    countdown (`<deadline>600</deadline>`, per Bohemia's own shipped file) looked
    like 10 minutes. `server_status.get_shutdown_deadline` always read the field
    as minutes, so the editor was contradicting the status grid beside it."""
    db = _mission_db(sample_config)
    (db / "messages.xml").write_text(MESSAGES_XML)

    columns = {c["key"]: c for c in client.get("/api/configs/messages").json()["columns"]}

    for key in ("delay", "repeat", "deadline"):
        # The box shows the unit (webui UnitInput), so the label no longer says it.
        assert columns[key]["unit"] == "minutes", key
        assert "second" not in columns[key]["help"].lower(), key


def test_economycore_table_round_trip(client, sample_config):
    mission_root = sample_config.server_dir_path / "mpmissions" / "dayzOffline.chernarusplus"
    mission_root.mkdir(parents=True, exist_ok=True)
    (mission_root / "cfgeconomycore.xml").write_text(ECONOMYCORE_XML)

    resp = client.get("/api/configs/economycore")
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "table"
    rows = body["rows"]
    assert rows[0]["values"] == {"section": "defaults", "name": "dyn_dmin", "value": "1"}

    # Update the default, add a ce file registration.
    rows[0]["values"]["value"] = "0"
    rows.append({"id": "new", "values": {"section": "ce:ce", "name": "extra_types.xml", "value": "types"}})
    resp = client.put("/api/configs/economycore", json={"rows": rows})
    assert resp.status_code == 200

    content = (mission_root / "cfgeconomycore.xml").read_text()
    assert 'value="0"' in content
    assert 'folder="ce"' in content
    assert 'name="extra_types.xml"' in content


# Bohemia's cfgeconomycore.xml (dayzOffline.chernarusplus), read at the pinned
# 1.29 commit: the head, the first seven lines of <defaults> (lines 18-24) and
# its closing tag, and the last line. The file has no final newline; this
# text has one.
def stock_economycore_defaults() -> str:
    lines = ce_lines(bohemia_ce(CE_1_29, "dayzOffline.chernarusplus/cfgeconomycore.xml"))
    defaults = ce_block(lines, "<defaults>")
    return "".join(lines[:2] + defaults[:7] + defaults[-1:] + lines[-1:]) + "\n"


def _stock_economycore(sample_config):
    mission_root = sample_config.server_dir_path / "mpmissions" / "dayzOffline.chernarusplus"
    mission_root.mkdir(parents=True, exist_ok=True)
    path = mission_root / "cfgeconomycore.xml"
    path.write_text(stock_economycore_defaults())
    return path


def test_economycore_defaults_explain_themselves(client, sample_config):
    _stock_economycore(sample_config)

    rows = {r["values"]["name"]: r for r in client.get("/api/configs/economycore").json()["rows"]}

    assert "Bohemia's missions set 30" in rows["dyn_radius"]["help"]["value"]
    assert rows["log_ce_loop"]["choices"] == {"value": ["true", "false"]}


@pytest.mark.parametrize("name, value, words", [
    ("backup_period", "10", "at least 15"),        # central-economy-configuration.wiki:376
    ("world_segments", "1.5", "whole number"),     # Integer (:249)
    ("dyn_radius", "far", "a number"),             # Float (:269)
    ("log_ce_loop", "yes", "true or false"),       # Boolean
    ("dyn_dmax", "-1", "at least 0"),
    ("bogus_setting", "1", "not a cfgeconomycore.xml default"),
])
def test_economycore_refuses_a_default_the_engine_cannot_use(client, sample_config, name, value, words):
    path = _stock_economycore(sample_config)
    rows = client.get("/api/configs/economycore").json()["rows"]
    rows.append({"id": "new-1", "values": {"section": "defaults", "name": name, "value": value}})

    resp = client.put("/api/configs/economycore", json={"rows": rows})

    assert resp.status_code == 400
    assert words in resp.json()["detail"]
    assert path.read_text() == stock_economycore_defaults()


def test_economycore_takes_the_wiki_defaults(client, sample_config):
    path = _stock_economycore(sample_config)
    rows = client.get("/api/configs/economycore").json()["rows"]
    rows += [
        {"id": "new-1", "values": {"section": "defaults", "name": "backup_period", "value": "15"}},
        {"id": "new-2", "values": {"section": "defaults", "name": "backup_startup", "value": "true"}},
    ]

    resp = client.put("/api/configs/economycore", json={"rows": rows})

    assert resp.status_code == 200, resp.text
    text = path.read_text()
    assert 'name="backup_period" value="15"' in text and 'name="backup_startup" value="true"' in text
    assert '<default name="dyn_radius" value="30" />' in text


def test_economycore_custom_folder_is_readonly(client, sample_config):
    """custom/ is generated by the mod XML merge — this editor may not touch it.

    Rows go out flagged `readonly` (the webui greys them out). A custom/ row
    left out of the request is kept, so a stale tab can't drop a mod's
    registrations, and one added or moved there is a 400, since the next merge
    would delete it anyway.
    """
    mission_root = sample_config.server_dir_path / "mpmissions" / "dayzOffline.chernarusplus"
    mission_root.mkdir(parents=True, exist_ok=True)
    (mission_root / "cfgeconomycore.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<economycore>\n"
        "\t<defaults>\n"
        '\t\t<default name="test" value="1"/>\n'
        "\t</defaults>\n"
        '\t<ce folder="custom">\n'
        '\t\t<file name="bikes-types.xml" type="types"/>\n'
        "\t</ce>\n"
        "</economycore>\n"
    )

    body = client.get("/api/configs/economycore").json()
    assert body["readonly_reason"]
    custom_rows = [r for r in body["rows"] if r["values"]["section"] == "ce:custom"]
    assert custom_rows and all(r.get("readonly") for r in custom_rows)
    assert not any(r.get("readonly") for r in body["rows"] if r["values"]["section"] == "defaults")

    # Dropping the custom row is ignored: a stale tab can't remove it.
    rows = [r for r in body["rows"] if r["values"]["section"] != "ce:custom"]
    assert client.put("/api/configs/economycore", json={"rows": rows}).status_code == 200
    assert 'name="bikes-types.xml"' in (mission_root / "cfgeconomycore.xml").read_text()

    # A row added to custom, or moved there from another section, is refused
    # rather than dropped while the save says "Saved.".
    before = (mission_root / "cfgeconomycore.xml").read_text()
    moved = [dict(r, values=dict(r["values"], section="ce:custom")) if r["id"] == "default:test" else r
             for r in body["rows"]]
    added = body["rows"] + [{"id": "new-0-1", "values": {"section": "ce:custom", "name": "mine.xml", "value": "types"}}]
    for rows in (added, moved):
        resp = client.put("/api/configs/economycore", json={"rows": rows})
        assert resp.status_code == 400, resp.text
        assert "ce:custom" in resp.json()["detail"]
        assert resp.json()["go"] == "manual-xml-links"  # the editor offers that page
    assert (mission_root / "cfgeconomycore.xml").read_text() == before


@pytest.mark.parametrize("section", ["default", "ce:", "db", ""])
def test_economycore_refuses_a_mistyped_section(client, sample_config, section):
    """A row whose section the save did not recognise was skipped, which
    removed its entry from the file. Now it is a 400 naming the row."""
    path = _mission_root(sample_config) / "cfgeconomycore.xml"
    text = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<economycore>\n"
        "\t<defaults>\n"
        '\t\t<default name="dyn_radius" value="30"/>\n'
        "\t</defaults>\n"
        '\t<ce folder="db">\n'
        '\t\t<file name="types.xml" type="types"/>\n'
        "\t</ce>\n"
        "</economycore>\n"
    )
    path.write_text(text)
    rows = {r["id"]: r for r in client.get("/api/configs/economycore").json()["rows"]}
    rows["default:dyn_radius"]["values"]["section"] = section

    resp = client.put("/api/configs/economycore", json={"rows": list(rows.values())})
    assert resp.status_code == 400
    assert "dyn_radius" in resp.json()["detail"]
    assert path.read_text() == text


def test_economycore_save_keeps_disabled_wrappers(client, sample_config):
    """A disabled mod's parked <ce> lives in a comment — a save must keep it.

    Only `xml_economy.enable_mod_economy_entries` may remove one; dropping it
    here silently un-disables the mod's XML on the next merge.
    """
    mission_root = sample_config.server_dir_path / "mpmissions" / "dayzOffline.chernarusplus"
    mission_root.mkdir(parents=True, exist_ok=True)
    (mission_root / "cfgeconomycore.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<economycore>\n"
        "\t<defaults>\n"
        '\t\t<default name="test" value="1"/>\n'
        "\t</defaults>\n"
        '\t<ce folder="custom">\n'
        '\t\t<file name="live-types.xml" type="types"/>\n'
        "\t</ce>\n"
        '\t<!-- DISABLED @Cool-Mod: <ce folder="custom">'
        '<file name="cool-mod-types.xml" type="types"/></ce> -->\n'
        "</economycore>\n"
    )

    rows = client.get("/api/configs/economycore").json()["rows"]
    # The parked entry is a comment, so it is not an editable row.
    assert not any(r["values"]["name"] == "cool-mod-types.xml" for r in rows)

    resp = client.put("/api/configs/economycore", json={"rows": rows})
    assert resp.status_code == 200

    content = (mission_root / "cfgeconomycore.xml").read_text()
    assert "<!-- DISABLED @Cool-Mod:" in content
    assert 'name="cool-mod-types.xml"' in content
    assert 'name="live-types.xml"' in content


def test_restore_without_support_404(client):
    resp = client.post("/api/configs/serverdz/restore")
    assert resp.status_code == 404
    # And the editor says so up front, so the button can explain itself instead
    # of erroring when pressed.
    assert client.get("/api/configs/serverdz/meta").json()["restore"] is False


def test_restore_is_offered_on_the_files_zsm_backs_up(client, sample_config):
    """Exactly the six a merge writes: the five ALL_MERGE_CONFIGS targets plus
    cfgeconomycore.xml. Everywhere else there is no .backup to restore from."""
    from app.web import editors

    assert sorted(s.id for s in editors.list_editors() if s.restore) == [
        "economycore",
        "eventgroups",
        "eventspawns",
        "limitsdefinition",
        "limitsdefinitionuser",
        "randompresets",
    ]
    assert client.get("/api/configs/eventgroups/meta").json()["restore"] is True


def test_restore_puts_the_file_back_and_leaves_a_version(client, sample_config, tmp_mission_dir):
    """The button was dead until 2026 Q3 — no editor registered a restore, so
    every press 404'd. It is the documented recovery path for a damaged mod
    marker, so it has to work; and since merges stopped restoring these files it
    discards an admin's accumulated edits, so it has to be undoable."""
    from app.shared.config_versions import load_index

    path = tmp_mission_dir / "cfgeventgroups.xml"
    path.write_text('<?xml version="1.0"?><eventgroupdef/>', encoding="utf-8")
    path.with_suffix(".xml.backup").write_text(
        '<?xml version="1.0"?><eventgroupdef><group name="Vanilla"/></eventgroupdef>',
        encoding="utf-8",
    )
    path.write_text(
        '<?xml version="1.0"?><eventgroupdef><group name="Mine"/></eventgroupdef>',
        encoding="utf-8",
    )

    assert client.post("/api/configs/eventgroups/restore").json()["restored"] is True

    assert "Vanilla" in path.read_text() and "Mine" not in path.read_text()
    versions = load_index(sample_config.paths.versions_dir).get_versions(path.name, source_path=str(path))
    assert any(v.trigger == "restore_backup" for v in versions)


def test_restore_reports_nothing_to_do_without_a_backup(client, tmp_mission_dir):
    path = tmp_mission_dir / "cfgeventgroups.xml"
    path.write_text('<?xml version="1.0"?><eventgroupdef/>', encoding="utf-8")

    assert client.post("/api/configs/eventgroups/restore").json()["restored"] is False


def _make_tp_dir(sample_config):
    d = sample_config.server_dir_path / sample_config.profiles_path / "TraderPlus" / "TraderPlusConfig"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_tp_banking_form_round_trip(client, sample_config):
    d = _make_tp_dir(sample_config)
    (d / "TraderPlusBankingConfig.json").write_text(
        json.dumps(
            {
                "Version": "2.5",
                "BankingLogs": 1,
                "IsCreditCarNeededForTransaction": 1,
                "TransactionFees": 0.0,
                "DefaultStartCurrency": 500,
                "DefaultMaxCurrency": 100000,
                "CurrenciesAccepted": ["Coins"],
                "TheAmountHasBeenTransferedToTheAccount": "msg",
            }
        )
    )

    resp = client.get("/api/configs/tp-banking")
    assert resp.status_code == 200
    fields = {f["key"]: f["value"] for s in resp.json()["sections"] for f in s["fields"]}
    assert fields["default_start_currency"] == 500

    resp = client.put("/api/configs/tp-banking", json={"values": {"default_start_currency": 1000}})
    assert resp.status_code == 200
    fields = {f["key"]: f["value"] for s in client.get("/api/configs/tp-banking").json()["sections"] for f in s["fields"]}
    assert fields["default_start_currency"] == 1000


def test_tp_ids_table_round_trip(client, sample_config):
    d = _make_tp_dir(sample_config)
    (d / "TraderPlusIDsConfig.json").write_text(
        json.dumps({"Version": "2.5", "IDs": [{"ID": 0, "Categories": ["Weapons"], "LicencesRequired": [], "CurrenciesAccepted": ["Coins"]}]})
    )

    resp = client.get("/api/configs/tp-ids")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["values"]["categories"] == ["Weapons"]
    columns = {c["key"]: c for c in resp.json()["columns"]}
    assert columns["categories"]["type"] == "list" and columns["categories"]["item"] == "str"

    resp = client.put(
        "/api/configs/tp-ids",
        json={"rows": [{"id": "0", "values": {"id": 0, "categories": ["Weapons", "Food"], "licences_required": [], "currencies_accepted": ["Coins"]}}]},
    )
    assert resp.status_code == 200
    rows = client.get("/api/configs/tp-ids").json()["rows"]
    assert rows[0]["values"]["categories"] == ["Weapons", "Food"]


def test_tp_ids_refuses_a_second_row_with_an_id_in_use(client, sample_config):
    """A second row with ID 4 was saved, and removing
    it then removed both, so the vehicle trader's categories were lost."""
    d = _make_tp_dir(sample_config)
    path = d / "TraderPlusIDsConfig.json"
    path.write_text(json.dumps({"Version": "2.5", "IDs": [
        {"Id": 4, "Categories": ["Vehicles"], "LicencesRequired": [], "CurrenciesAccepted": []},
    ]}))
    before = path.read_text()
    row = {"id": "4", "values": {"id": 4, "categories": ["Vehicles"], "licences_required": [], "currencies_accepted": []}}
    new = {"id": "new-1-x", "values": {"id": 4, "categories": [], "licences_required": [], "currencies_accepted": []}}
    resp = client.put("/api/configs/tp-ids", json={"rows": [row, new]})
    assert resp.status_code == 400
    assert "Two rows have ID 4" in resp.json()["detail"]
    assert path.read_text() == before


def test_tp_ids_rows_of_a_file_with_a_duplicate_id_stay_apart(client, sample_config):
    """A file that already holds two ID-4 entries: removing the empty one keeps
    the real one."""
    d = _make_tp_dir(sample_config)
    path = d / "TraderPlusIDsConfig.json"
    path.write_text(json.dumps({"Version": "2.5", "IDs": [
        {"Id": 4, "Categories": ["Vehicles"], "LicencesRequired": [], "CurrenciesAccepted": []},
        {"Id": 4, "Categories": [], "LicencesRequired": [], "CurrenciesAccepted": []},
    ]}))
    rows = client.get("/api/configs/tp-ids").json()["rows"]
    assert [r["id"] for r in rows] == ["4", "4-dup-1"]
    resp = client.put("/api/configs/tp-ids", json={"rows": [rows[0]]})
    assert resp.status_code == 200, resp.text
    [kept] = client.get("/api/configs/tp-ids").json()["rows"]
    assert kept["values"]["categories"] == ["Vehicles"]


@pytest.mark.parametrize("editor_id, filename, payload", [
    ("tp-banking", "TraderPlusBankingConfig.json", {"values": {"default_start_currency": "4.5"}}),
    ("tp-general", "TraderPlusGeneralConfig.json", {"values": {"lock_pick_chance": "abc"}}),
    ("tp-safezone", "TraderPlusSafeZoneConfig.json", {"values": {"kick_after_delay": "4.5"}}),
    ("tp-garage", "TraderPlusGarageConfig.json", {"values": {"max_vehicle_stored": "4.5"}}),
    ("tp-insurance", "TraderPlusInsuranceConfig.json",
     {"rows": [{"id": "new-1-x", "values": {"vehicle_name": "Sedan_02",
                                            "insurance_price_coefficient": "abc"}}]}),
])
def test_a_refused_traderplus_save_keeps_no_version(client, sample_config, editor_id, filename, payload):
    """A save refused for a bad value took a version first, so Versions listed
    a snapshot of a save that never happened (app/web/CLAUDE.md: a refused
    save leaves no version)."""
    from app.shared.config_versions import load_index, store_for

    path = _make_tp_dir(sample_config) / filename
    path.write_text(json.dumps({"Version": "2.5"}))
    before = path.read_text()
    resp = client.put(f"/api/configs/{editor_id}", json=payload)
    assert resp.status_code == 400, resp.text
    assert path.read_text() == before
    assert load_index(store_for(path)).get_versions(path.name) == []


def test_tp_insurance_rows_of_a_file_with_a_duplicate_vehicle_stay_apart(client, sample_config):
    """Two entries for one vehicle shared a row id, so removing one row removed
    both, and each kept the hidden keys of the last entry with that name."""
    d = _make_tp_dir(sample_config)
    path = d / "TraderPlusInsuranceConfig.json"
    path.write_text(json.dumps({"Version": "2.5", "AuthorizedIDInsurance": [], "Insurances": [
        {"VehicleName": "Sedan_02", "InsurancePriceCoefficient": 1.5,
         "CollateralMoneyCoefficient": 0.5, "Note": "real"},
        {"VehicleName": "Sedan_02", "InsurancePriceCoefficient": 9.0,
         "CollateralMoneyCoefficient": 9.0, "Note": "stray"},
    ]}))
    rows = client.get("/api/configs/tp-insurance").json()["rows"]
    assert [r["id"] for r in rows] == ["Sedan_02", "Sedan_02-dup-1"]
    resp = client.put("/api/configs/tp-insurance", json={"rows": [rows[0]]})
    assert resp.status_code == 200, resp.text
    [kept] = json.loads(path.read_text())["Insurances"]
    assert kept["InsurancePriceCoefficient"] == 1.5
    assert kept["Note"] == "real"


def test_tp_insurance_coefficients_are_percentages_past_100(client, sample_config):
    """Both coefficients multiply the vehicle price (1.25 = 125 %), so the page
    types them as a percentage, and a save of 1.5 (150 %) keeps 1.5."""
    d = _make_tp_dir(sample_config)
    path = d / "TraderPlusInsuranceConfig.json"
    path.write_text(json.dumps({"Version": "2.5", "AuthorizedIDInsurance": [], "Insurances": [
        {"VehicleName": "Sedan_02", "InsurancePriceCoefficient": 1.25, "CollateralMoneyCoefficient": 0.75},
    ]}))
    data = client.get("/api/configs/tp-insurance").json()
    columns = {c["key"]: c for c in data["columns"]}
    assert columns["insurance_price_coefficient"]["share"] is True
    assert columns["collateral_money_coefficient"]["share"] is True
    assert "share" not in columns["vehicle_name"]
    row = data["rows"][0]
    row["values"]["insurance_price_coefficient"] = "1.5"
    resp = client.put("/api/configs/tp-insurance", json={"rows": [row]})
    assert resp.status_code == 200, resp.text
    [kept] = json.loads(path.read_text())["Insurances"]
    assert kept["InsurancePriceCoefficient"] == 1.5


def test_tp_vehicles_refuses_a_second_row_for_one_vehicle(client, sample_config):
    """Addendum to cfg-19: two rows "OffroadHatchback" shared a row
    id the same way."""
    d = _make_tp_dir(sample_config)
    path = d / "TraderPlusVehiclesConfig.json"
    path.write_text(json.dumps({"Version": "2.5", "VehiclesParts": [
        {"VehicleName": "OffroadHatchback", "Height": 3, "VehicleParts": ["HatchbackWheel"]},
    ]}))
    rows = client.get("/api/configs/tp-vehicles").json()["rows"]
    new = {"id": "new-1-x", "values": {"vehicle_name": "OffroadHatchback", "height": 1, "vehicle_parts": []}}
    before = path.read_text()
    resp = client.put("/api/configs/tp-vehicles", json={"rows": [*rows, new]})
    assert resp.status_code == 400 and "Two rows are vehicle OffroadHatchback" in resp.json()["detail"]
    assert path.read_text() == before


def test_tp_price_list_and_update(client, sample_config):
    d = _make_tp_dir(sample_config)
    (d / "TraderPlusPriceConfig.json").write_text(
        json.dumps(
            {
                "Version": "2.5",
                "EnableAutoCalculation": 0,
                "EnableAutoDestockAtRestart": 0,
                "EnableDefaultTraderStock": 0,
                "TraderCategories": [
                    {"CategoryName": "Weapons", "Products": ["M4A1,1,-1,1,1000,500"]},
                ],
            }
        )
    )

    resp = client.get("/api/tp-price")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["rows"][0]["classname"] == "M4A1"

    resp = client.get("/api/tp-price", params={"q": "nonexistent"})
    assert resp.json()["total"] == 0

    resp = client.put("/api/tp-price/0/0", json={"buy_price": 2000})
    assert resp.status_code == 200
    assert resp.json()["buy_price"] == 2000

    resp = client.post("/api/tp-price/0/multiplier", json={"multiplier": 2.0, "columns": ["buy_price"]})
    assert resp.status_code == 200
    assert resp.json()["changes"] == 1


def test_tp_price_refuses_a_price_below_minus_one(client, sample_config):
    """F80: -1 means "that direction is off"; -5 was written as is."""
    d = _make_tp_dir(sample_config)
    path = d / "TraderPlusPriceConfig.json"
    path.write_text(json.dumps({
        "Version": "2.5", "EnableAutoCalculation": 0, "EnableAutoDestockAtRestart": 0,
        "EnableDefaultTraderStock": 0,
        "TraderCategories": [{"CategoryName": "Weapons", "Products": ["M4A1,1,-1,1,1000,500"]}],
    }))
    before = path.read_bytes()

    assert client.put("/api/tp-price/0/0", json={"buy_price": -5}).status_code == 422
    assert client.put("/api/tp-price/0/0", json={"sell_price": -1.5}).status_code == 422
    assert path.read_bytes() == before

    resp = client.put("/api/tp-price/0/0", json={"sell_price": -1})
    assert resp.status_code == 200 and resp.json()["sell_price"] == -1

    # The multiplier leaves the "off" sentinel alone rather than making it -2.
    assert client.post("/api/tp-price/0/multiplier", json={"multiplier": 2.0}).status_code == 200
    row = client.get("/api/tp-price").json()["rows"][0]
    assert (row["buy_price"], row["sell_price"]) == (2000, -1)


def test_tp_price_zone_offers_list_and_add(client, sample_config):
    # plans/48 D: an installed zone mod's classes, added unpriced. Class names
    # are our own; nothing here comes from the mods.
    write_test_mods(sample_config, ["@RadZone"])
    (sample_config.server_dir_path / "@RadZone").mkdir()
    (sample_config.server_dir_path / "@RadZone" / "types.xml").write_text(
        '<types><type name="ZoneTestSuit"/><type name="ZoneTestMask"/></types>', encoding="utf-8"
    )
    d = _make_tp_dir(sample_config)
    (d / "TraderPlusPriceConfig.json").write_text(json.dumps({"TraderCategories": []}))
    (d / "TraderPlusIDsConfig.json").write_text(json.dumps({"IDs": [{"Id": 3, "Categories": []}]}))

    body = client.get("/api/tp-price/zone-offers").json()
    assert body["traders"] == [{"id": 3, "names": [], "categories": 0}]
    assert [(o["key"], o["items"]) for o in body["offers"]] == [
        ("radzone", ["ZoneTestSuit", "ZoneTestMask"]),
    ]

    resp = client.post("/api/tp-price/zone-offers",
                       json={"mod": "radzone", "category": "RadZone", "trader_ids": [3]})
    assert resp.status_code == 200
    assert resp.json()["added"] == ["ZoneTestSuit", "ZoneTestMask"]
    rows = client.get("/api/tp-price").json()["rows"]
    assert [(r["classname"], r["buy_price"], r["sell_price"]) for r in rows] == [
        ("ZoneTestSuit", -1, -1), ("ZoneTestMask", -1, -1),
    ]

    resp = client.post("/api/tp-price/zone-offers",
                       json={"mod": "radzone", "category": "RadZone", "trader_ids": []})
    assert resp.status_code == 400
    assert "already in the price file" in resp.json()["detail"]


def test_tp_price_zone_offer_uses_toxiczones_prices_only_when_asked(client, sample_config):
    # plans/48 D10: ToxicZone's own Trader.txt, read from the install, and
    # only with use_prices. Class names and prices are our own.
    write_test_mods(sample_config, ["@ToxicZone"])
    mod = sample_config.server_dir_path / "@ToxicZone"
    mod.mkdir()
    (mod / "types.xml").write_text('<types><type name="ZoneTestSuit"/></types>', encoding="utf-8")
    (mod / "Trader.txt").write_text("<Category> T\n\tZoneTestSuit, *, 40, 10\n", encoding="utf-8")
    d = _make_tp_dir(sample_config)
    (d / "TraderPlusPriceConfig.json").write_text(json.dumps({"TraderCategories": []}))
    (d / "TraderPlusIDsConfig.json").write_text(json.dumps({"IDs": []}))

    offer = client.get("/api/tp-price/zone-offers").json()["offers"][0]
    assert offer["prices"] == {"ZoneTestSuit": {"buy": 40.0, "sell": 10.0}}

    resp = client.post("/api/tp-price/zone-offers",
                       json={"mod": "toxiczone", "category": "Toxic", "use_prices": True})
    assert resp.status_code == 200 and resp.json()["priced"] == 1
    row = client.get("/api/tp-price").json()["rows"][0]
    assert (row["classname"], row["buy_price"], row["sell_price"]) == ("ZoneTestSuit", 40, 10)


# ---------------------------------------------------------------------------
# Run Command editor (config.json launch line)
# ---------------------------------------------------------------------------


def test_runcommand_round_trip(client, sample_config, monkeypatch):
    monkeypatch.setattr("app.web.editors.server_forms.os.cpu_count", lambda: 4)
    resp = client.get("/api/configs/runcommand")
    assert resp.status_code == 200
    body = resp.json()
    fields = {f["key"]: f["value"] for s in body["sections"] for f in s["fields"]}
    assert "server_port" in fields
    assert "adminLog" in fields
    assert "_extras" in fields

    resp = client.put(
        "/api/configs/runcommand",
        json={
            "values": {
                "server_port": 2402,
                "adminLog": True,
                "doLogs": True,
                "cpuCount": "4",
                "limitFPS": "",
                "_extras": "-someUnknownFlag",
            }
        },
    )
    assert resp.status_code == 200

    from app.shared.config import DayzConfig

    cfg = DayzConfig.load()
    assert cfg.server_port == 2402
    assert "-adminLog" in cfg.server_args
    assert "-doLogs" in cfg.server_args
    assert "-cpuCount=4" in cfg.server_args
    assert "-limitFPS" not in cfg.server_args
    assert "-someUnknownFlag" in cfg.server_args

    fields = {
        f["key"]: f["value"]
        for s in client.get("/api/configs/runcommand").json()["sections"]
        for f in s["fields"]
    }
    assert fields["server_port"] == 2402
    assert fields["adminLog"] is True
    assert fields["cpuCount"] == 4
    assert fields["_extras"] == "-someUnknownFlag"


def _runcommand_field(client, key):
    for section in client.get("/api/configs/runcommand").json()["sections"]:
        for f in section["fields"]:
            if f["key"] == key:
                return f["value"]
    raise KeyError(key)


def test_runcommand_save_refuses_a_drive_root_on_windows(client, sample_config, monkeypatch):
    monkeypatch.setattr("app.server.service_control.IS_WINDOWS", True)
    assert client.put(
        "/api/configs/runcommand", json={"values": {"adminLog": True}}
    ).status_code == 200
    before = _runcommand_field(client, "config_path")

    resp = client.put("/api/configs/runcommand", json={"values": {"config_path": "C:\\"}})
    assert resp.status_code == 400
    assert "-config" in resp.json()["detail"] and "drive root" in resp.json()["detail"]
    assert _runcommand_field(client, "config_path") == before


def test_runcommand_save_refuses_a_shell_metachar_in_a_path(client, sample_config):
    """A path flag that the launch command would run as syntax is a 400 on save,
    not a broken unit at the next install."""
    # A valid save first, so config.json exists — mutate_config then loads a
    # fresh copy for the next save and discards it on the raise, as in production.
    assert client.put(
        "/api/configs/runcommand", json={"values": {"adminLog": True}}
    ).status_code == 200
    before = _runcommand_field(client, "config_path")

    resp = client.put(
        "/api/configs/runcommand",
        json={"values": {"config_path": "serverDZ$HOME.cfg"}},
    )
    assert resp.status_code == 400
    assert "-config" in resp.json()["detail"]
    # Nothing was persisted — the save raised before config.save().
    assert _runcommand_field(client, "config_path") == before


@pytest.mark.parametrize(
    ("key", "name"),
    [
        ("config_path", "-config"),
        ("profiles_path", "-profiles"),
        ("battleye_path", "-BEpath"),
        ("server_port", "-port"),
    ],
)
def test_runcommand_refuses_an_emptied_path_or_port(client, sample_config, key, name):
    """Clearing one of these was skipped: the save answered 200 and the old
    value came back. Each is always on the launch line, so empty is a 400."""
    assert client.put(
        "/api/configs/runcommand", json={"values": {"adminLog": True}}
    ).status_code == 200
    before = _runcommand_field(client, key)

    resp = client.put("/api/configs/runcommand", json={"values": {key: "", "adminLog": False}})
    assert resp.status_code == 400
    assert name in resp.json()["detail"] and "can't be empty" in resp.json()["detail"]
    assert _runcommand_field(client, key) == before
    # Nothing else in the same save went through either.
    assert _runcommand_field(client, "adminLog") is True


def test_messages_cards_save_keeps_what_they_do_not_show(client, sample_config):
    """The Messages cards' save, through the route: rows carry their id and
    only some keys; the rest of each message stays."""
    db = _mission_db(sample_config)
    (db / "messages.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<messages>
    <message>
        <delay>5</delay>
        <repeat>60</repeat>
        <deadline>600</deadline>
        <shutdown>1</shutdown>
        <text>Bye</text>
    </message>
    <message>
        <text>Plain</text>
    </message>
</messages>
"""
    )
    resp = client.put(
        "/api/configs/messages",
        json={
            "rows": [
                {"id": "0", "values": {"type": "Shutdown", "text": "Bye", "deadline": "600"}},
                {"id": "1", "values": {"type": "Other", "text": "Plain", "repeat": "10"}},
            ]
        },
    )
    assert resp.status_code == 200
    values = [r["values"] for r in client.get("/api/configs/messages").json()["rows"]]
    assert values[0] == {"type": "Shutdown", "text": "Bye", "delay": "5", "repeat": "60", "deadline": "600"}
    assert values[1]["repeat"] == "10"

    resp = client.put(
        "/api/configs/messages",
        json={"rows": [{"id": "0", "values": {"type": "Bogus", "text": "Bye"}}]},
    )
    assert resp.status_code == 400


def test_runcommand_generated_command(client, sample_config, monkeypatch):
    """GET /api/runcommand returns the generated launch line + source links."""
    monkeypatch.setattr(
        "app.server.service_control._get_mod_string", lambda config: "@CF;@COT"
    )
    resp = client.put(
        "/api/configs/runcommand",
        json={"values": {"server_port": 2302, "adminLog": True}},
    )
    assert resp.status_code == 200

    resp = client.get("/api/runcommand")
    assert resp.status_code == 200
    body = resp.json()

    # Full command string reflects config: binary, port, adminLog flag.
    assert body["command"].startswith("./DayZServer")
    assert "-mod=@CF;@COT" in body["command"]
    assert "-port=2302" in body["command"]
    assert "-adminLog" in body["command"]

    parts = {p["label"]: p for p in body["parts"]}
    assert parts["Mods"]["origin"] == "mods"
    assert parts["Mods"]["link"] == {"route": "/mods", "label": "Mods"}
    assert parts["Game port"]["link"]["route"] == "/configs/runcommand"

    # Deduped nav targets for the "change in" chips.
    labels = {s["label"] for s in body["sources"]}
    assert {"Mods", "Run Command"} <= labels


def test_runcommand_omits_mod_flag_without_mods(client, monkeypatch):
    """No active mods → no -mod= at all (the engine rejects an empty list)."""
    monkeypatch.setattr("app.server.service_control._get_mod_string", lambda config: "")

    body = client.get("/api/runcommand").json()

    assert "-mod" not in body["command"]
    assert "Mods" not in {p["label"] for p in body["parts"]}
    assert "Mods" not in {s["label"] for s in body["sources"]}


def test_runcommand_rejects_bad_port(client):
    resp = client.put(
        "/api/configs/runcommand", json={"values": {"server_port": "99999"}}
    )
    assert resp.status_code == 400
    assert "1-65535" in resp.json()["detail"]


def _saved_args() -> str:
    from app.shared.config import DayzConfig

    return DayzConfig.load().server_args


def test_runcommand_points_server_mods_and_mission_at_their_pages(client):
    """-serverMod= comes from the Mods page's Server only mark and the mission
    from the Maps page: the two fields say so and link there, and a value typed
    into them still loads and saves as before."""
    fields = {f["key"]: f for s in client.get("/api/configs/runcommand").json()["sections"] for f in s["fields"]}
    assert (fields["serverMod"]["elsewhere"], fields["serverMod"]["link"]["to"]) == (True, "/mods")
    assert (fields["mission"]["elsewhere"], fields["mission"]["link"]["to"]) == (True, "/maps")

    resp = client.put("/api/configs/runcommand", json={"values": {"serverMod": "@Admin", "mission": "x.chernarusplus"}})
    assert resp.status_code == 200
    assert "-serverMod=@Admin" in _saved_args() and "-mission=x.chernarusplus" in _saved_args()
    assert _runcommand_field(client, "serverMod") == "@Admin"
    assert client.put("/api/configs/runcommand", json={"values": {"serverMod": ""}}).status_code == 200
    assert "-serverMod" not in _saved_args()


@pytest.mark.parametrize("values, message", [
    # server-configuration.wiki:182 "(current max is 200)"
    ({"limitFPS": "201"}, "-limitFPS must be between 1 and 200."),
    ({"limitFPS": "0"}, "-limitFPS must be between 1 and 200."),
    # :181 "less or equal than the numbers of available cores"
    ({"cpuCount": "0"}, "-cpuCount must be between 1 and 4."),
    ({"cpuCount": "5"}, "-cpuCount must be between 1 and 4."),
])
def test_runcommand_refuses_a_number_flag_the_server_cannot_use(client, sample_config, monkeypatch, values, message):
    monkeypatch.setattr("app.web.editors.server_forms.os.cpu_count", lambda: 4)
    sample_config.save()
    before = _saved_args()
    resp = client.put("/api/configs/runcommand", json={"values": values})
    assert resp.status_code == 400
    assert resp.json()["detail"] == message
    assert _saved_args() == before


def test_runcommand_takes_the_number_flags_at_their_limits(client, sample_config, monkeypatch):
    monkeypatch.setattr("app.web.editors.server_forms.os.cpu_count", lambda: 4)
    resp = client.put("/api/configs/runcommand", json={"values": {"limitFPS": "200", "cpuCount": "4"}})
    assert resp.status_code == 200, resp.text
    args = _saved_args()
    assert "-limitFPS=200" in args and "-cpuCount=4" in args


@pytest.mark.parametrize("extras, flag, where", [
    ("-port=2402", "-port", "Game port"),
    ("-config=other.cfg", "-config", "Server config"),
    ('"-profiles=/tmp/p"', "-profiles", "Profiles dir"),
    ("-BEpath=be -foo", "-BEpath", "BattlEye dir"),
    ("-mod=@CF", "-mod", "the mod list on the Mods page"),
    ("-servermod=@X", "-servermod", "Server mods"),
])
def test_runcommand_refuses_extras_that_zsm_sets_itself(client, sample_config, extras, flag, where):
    sample_config.save()
    before = _saved_args()
    resp = client.put("/api/configs/runcommand", json={"values": {"_extras": extras}})
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail.startswith(f"Extra args can't hold {flag}: Zedstead sets it from {where}")
    assert _saved_args() == before


def test_runcommand_keeps_other_extras(client, sample_config):
    resp = client.put("/api/configs/runcommand", json={"values": {"_extras": "-noFilePatching -modeX=1"}})
    assert resp.status_code == 200, resp.text
    assert "-noFilePatching -modeX=1" in _saved_args()


# ---------------------------------------------------------------------------
# Core Settings editor (dayzsetting.xml)
# ---------------------------------------------------------------------------

DAYZSETTING_XML = """<?xml version="1.0" encoding="UTF-8"?>
<settings>
    <video noshadergen="1" screenwidth="1024" screenheight="768"/>
    <render geometricdetail="2"/>
    <jobsystem globalqueue="4096" threadqueue="1024">
        <pc maxcores="2" reservedcores="1"/>
    </jobsystem>
</settings>
"""


def test_coresettings_round_trip(client, sample_config):
    path = sample_config.server_dir_path / "dayzsetting.xml"
    path.write_text(DAYZSETTING_XML)

    resp = client.get("/api/configs/coresettings")
    assert resp.status_code == 200
    fields = {f["key"]: f["value"] for s in resp.json()["sections"] for f in s["fields"]}
    assert fields["jobsystem--globalqueue"] == "4096"
    assert fields["pc--maxcores"] == "2"

    resp = client.put(
        "/api/configs/coresettings",
        json={"values": {"pc--maxcores": "8", "jobsystem--globalqueue": "8192"}},
    )
    assert resp.status_code == 200

    content = path.read_text()
    assert 'maxcores="8"' in content
    assert 'globalqueue="8192"' in content


@pytest.mark.parametrize("values, message", [
    # server-configuration.wiki:146-151: workers are maxcores - reservedcores.
    ({"pc--maxcores": "two"}, "maxcores must be a whole number of CPU cores."),
    ({"pc--reservedcores": "-1"}, "reservedcores must be a whole number of CPU cores."),
    ({"pc--maxcores": "0", "pc--reservedcores": "0"}, "maxcores must be at least 1."),
])
def test_coresettings_refuses_core_counts_the_job_system_cannot_use(client, sample_config, values, message):
    path = sample_config.server_dir_path / "dayzsetting.xml"
    path.write_text(DAYZSETTING_XML)
    resp = client.put("/api/configs/coresettings", json={"values": values})
    assert resp.status_code == 400
    assert resp.json()["detail"].startswith(message)
    assert path.read_text() == DAYZSETTING_XML


@pytest.mark.parametrize("values", [
    {"pc--reservedcores": "2"},
    {"pc--maxcores": "1"},
    {"pc--maxcores": "4", "pc--reservedcores": "0"},
])
def test_coresettings_takes_reserved_cores_at_or_above_max_cores(client, sample_config, values):
    """server-configuration.wiki:150: the workers are maxcores - reservedcores,
    "but still at least one worker thread is allocated"."""
    path = sample_config.server_dir_path / "dayzsetting.xml"
    path.write_text(DAYZSETTING_XML)
    resp = client.put("/api/configs/coresettings", json={"values": values})
    assert resp.status_code == 200
    content = path.read_text()
    for key, value in values.items():
        assert f'{key.split("--", 1)[1]}="{value}"' in content


def test_coresettings_puts_client_only_sections_behind_their_own_tab(client, sample_config):
    """Video and Render are ignored by a dedicated server: the form shows them
    on a "Client only" tab after the settings that change the server."""
    (sample_config.server_dir_path / "dayzsetting.xml").write_text(DAYZSETTING_XML)
    sections = client.get("/api/configs/coresettings").json()["sections"]
    tabs = {s["title"].split(" ")[0]: s.get("tab") for s in sections}
    assert tabs == {"Video": "Client only", "Render": "Client only", "Job": None}
    labels = {f["key"]: f["label"] for s in sections for f in s["fields"]}
    assert labels["video--noshadergen"] == "No shader generation"


def test_coresettings_missing_file_400(client):
    resp = client.get("/api/configs/coresettings")
    assert resp.status_code == 400
    assert "not found" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Stage 11 structured upgrades
# ---------------------------------------------------------------------------

SPAWNABLETYPES_XML = """<?xml version="1.0" encoding="UTF-8"?>
<spawnabletypes>
    <type name="AKM">
        <attachments chance="1.00">
            <item name="AK_WoodBttstck" chance="1.00"/>
        </attachments>
    </type>
</spawnabletypes>
"""

RANDOMPRESETS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<randompresets>
    <cargo chance="0.35" name="foodVillage">
        <item name="SardinesCan" chance="0.10"/>
    </cargo>
</randompresets>
"""

ENVIRONMENT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<env>
    <territories>
        <territory type="Herd" name="RedDeer" behavior="DZRedDeerGroupBeh">
            <file usable="RedDeer_TerritoryList" path="env/red_deer_territories.xml"/>
            <agent type="Deer" chance="0.5"/>
            <item name="globalCountMax" val="30"/>
            <item name="zoneCountMin" val="2"/>
            <item name="zoneCountMax" val="5"/>
        </territory>
    </territories>
</env>
"""

EFFECTAREA_JSON = """{
  "Areas": [
    {
      "AreaName": "Rify",
      "Type": "ContaminatedArea_Static",
      "Data": {"Pos": [1000.0, 0.0, 2000.0], "Radius": 150.0},
      "PlayerData": {}
    }
  ],
  "SafePositions": [[100, 200]]
}
"""


def _mission_root(sample_config):
    d = sample_config.server_dir_path / "mpmissions" / "dayzOffline.chernarusplus"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_spawnabletypes_table_round_trip(client, sample_config):
    root = _mission_root(sample_config)
    (root / "cfgspawnabletypes.xml").write_text(SPAWNABLETYPES_XML)

    body = client.get("/api/configs/spawnabletypes").json()
    assert body["kind"] == "table"
    row = body["rows"][0]
    assert row["values"]["name"] == "AKM"
    assert "attachments" in row["values"]["groups"]

    row["values"]["damage_min"] = "0.3"
    row["values"]["damage_max"] = "0.7"
    row["values"]["hoarder"] = True
    resp = client.put("/api/configs/spawnabletypes", json={"rows": [row]})
    assert resp.status_code == 200

    content = (root / "cfgspawnabletypes.xml").read_text()
    assert 'min="0.30"' in content
    assert 'max="0.70"' in content
    assert "hoarder" in content
    # Attachments untouched.
    assert "AK_WoodBttstck" in content


def test_randompresets_chances_are_shares(client, sample_config):
    """The two 0.0-1.0 chances were plain text cells; the page now types them
    as a percentage (webui ShareInput), and the row keeps the file's value."""
    (_mission_root(sample_config) / "cfgrandompresets.xml").write_text(RANDOMPRESETS_XML)
    columns = {c["key"]: c for c in client.get("/api/configs/randompresets").json()["columns"]}
    assert columns["preset_chance"]["share"] is True
    assert columns["chance"]["share"] is True
    assert "share" not in columns["preset"]


def test_randompresets_table_round_trip(client, sample_config):
    root = _mission_root(sample_config)
    (root / "cfgrandompresets.xml").write_text(RANDOMPRESETS_XML)

    body = client.get("/api/configs/randompresets").json()
    assert body["kind"] == "table"
    rows = body["rows"]
    assert rows[0]["values"] == {
        "kind": "cargo", "preset": "foodVillage", "preset_chance": "0.35", "item": "SardinesCan", "chance": "0.10",
    }

    rows[0]["values"]["chance"] = "0.20"
    rows.append({"id": "new", "values": {"preset": "foodVillage", "preset_chance": "0.35", "item": "PeachesCan", "chance": "0.15"}})
    resp = client.put("/api/configs/randompresets", json={"rows": rows})
    assert resp.status_code == 200

    content = (root / "cfgrandompresets.xml").read_text()
    assert 'name="PeachesCan"' in content
    assert 'chance="0.20"' in content


def test_environment_table_round_trip(client, sample_config):
    root = _mission_root(sample_config)
    (root / "cfgenvironment.xml").write_text(ENVIRONMENT_XML)

    body = client.get("/api/configs/environment").json()
    assert body["kind"] == "table"
    rows = {r["id"]: r for r in body["rows"]}
    assert rows["terr:RedDeer"]["values"]["global_max"] == "30"
    assert rows["agent:RedDeer:Deer"]["values"]["chance"] == "0.5"

    rows["terr:RedDeer"]["values"]["global_max"] = "50"
    rows["agent:RedDeer:Deer"]["values"]["chance"] = "0.9"
    resp = client.put("/api/configs/environment", json={"rows": list(rows.values())})
    assert resp.status_code == 200

    content = (root / "cfgenvironment.xml").read_text()
    assert '<item name="globalCountMax" val="50"/>' in content
    assert '<item name="zoneCountMin" val="2"/>' in content
    assert 'chance="0.9"' in content


SPAWNABLETYPES_TWO_XML = """<?xml version="1.0" encoding="UTF-8"?>
<spawnabletypes>
    <!-- weapons -->
    <type name="AKM">
        <attachments chance="1.00">
            <item name="AK_WoodBttstck" chance="1.00"/>
        </attachments>
    </type>
    <type name="M4A1">
        <hoarder/>
    </type>
</spawnabletypes>
"""


def _spawnable_rows(client):
    body = client.get("/api/configs/spawnabletypes").json()
    # A row's id is its position (stock files repeat names); keyed by name here.
    return body, {r["values"]["name"]: r for r in body["rows"]}


def test_spawnabletypes_save_adds_removes_and_renames(client, sample_config):
    """The table sends every row, so the save is the whole list. It used to
    look rows up by id and skip the rest: an added row was dropped, a removed
    one stayed and a rename did nothing, while the save said "Saved."."""
    path = _mission_root(sample_config) / "cfgspawnabletypes.xml"
    path.write_text(SPAWNABLETYPES_TWO_XML)

    _, rows = _spawnable_rows(client)
    rows["AKM"]["values"]["name"] = "AKM_Black"
    payload = [
        rows["AKM"],  # renamed; M4A1 is left out, so it is removed
        {"id": "new-2-1", "values": {
            "name": "Mosin9130", "damage_min": "0.1", "damage_max": "0.5",
            "tag": "", "hoarder": True, "groups": "",
        }},
    ]
    assert client.put("/api/configs/spawnabletypes", json={"rows": payload}).status_code == 200

    _, rows = _spawnable_rows(client)
    assert set(rows) == {"AKM_Black", "Mosin9130"}
    assert rows["AKM_Black"]["values"]["groups"] == "attachments[1]"  # kept its groups
    assert rows["Mosin9130"]["values"]["damage_max"] == "0.5"
    assert rows["Mosin9130"]["values"]["hoarder"] is True
    text = path.read_text()
    assert "<!-- weapons -->" in text
    assert "AK_WoodBttstck" in text and "M4A1" not in text


def test_spawnabletypes_added_row_gets_its_tree_before_the_first_save(client, sample_config):
    """An added row starts with the payload's `new_row_nested`, so its
    attachments can be filled in at once instead of after a save and reload."""
    path = _mission_root(sample_config) / "cfgspawnabletypes.xml"
    path.write_text(SPAWNABLETYPES_TWO_XML)

    body, _rows = _spawnable_rows(client)
    assert body["new_row_nested"] == {"groups": []}
    group = dict(body["nested_templates"]["groups"], items=[{"name": "Mag_Mosin_Clip", "chance": 0.5}])
    payload = body["rows"] + [{"id": "new-2-1", "values": {
        "name": "Mosin9130", "damage_min": "", "damage_max": "", "tag": "", "hoarder": False, "groups": "",
        "$nested": {"groups": [dict(group, kind="cargo")]},
        "$nested_base": body["new_row_nested"],
    }}]
    resp = client.put("/api/configs/spawnabletypes", json={"rows": payload})
    assert resp.status_code == 200, resp.text

    _, rows = _spawnable_rows(client)
    assert rows["Mosin9130"]["values"]["groups"] == "cargo[1]"
    assert 'name="Mag_Mosin_Clip"' in path.read_text()


def test_spawnabletypes_save_refuses_a_name_another_type_has(client, sample_config):
    path = _mission_root(sample_config) / "cfgspawnabletypes.xml"
    path.write_text(SPAWNABLETYPES_TWO_XML)
    _, rows = _spawnable_rows(client)

    added = {"id": "new-2-1", "values": {"name": "M4A1", "hoarder": False}}
    resp = client.put("/api/configs/spawnabletypes", json={"rows": [*rows.values(), added]})
    assert resp.status_code == 400
    assert "M4A1" in resp.json()["detail"]

    rows["AKM"]["values"]["name"] = "M4A1"
    resp = client.put("/api/configs/spawnabletypes", json={"rows": list(rows.values())})
    assert resp.status_code == 400
    assert path.read_text() == SPAWNABLETYPES_TWO_XML


def test_spawnabletypes_groups_column_is_read_only(client, sample_config):
    """The cell is a summary of the row's tree; the save never reads it, so
    the table must not offer it as something to type into."""
    (_mission_root(sample_config) / "cfgspawnabletypes.xml").write_text(SPAWNABLETYPES_TWO_XML)
    body, _ = _spawnable_rows(client)
    readonly = {c["key"] for c in body["columns"] if c.get("readonly")}
    assert readonly == {"groups"}


def test_environment_table_offers_values_only(client, sample_config):
    """Territories and agents are added and removed in the Raw tab; the table
    locks each row's names and the cells of the other kind of row."""
    (_mission_root(sample_config) / "cfgenvironment.xml").write_text(ENVIRONMENT_XML)

    body = client.get("/api/configs/environment").json()
    assert body["add_row"] is False and body["remove_row"] is False
    rows = {r["id"]: r for r in body["rows"]}
    assert set(rows["terr:RedDeer"]["locked"]) == {"territory", "agent", "chance"}
    assert set(rows["agent:RedDeer:Deer"]["locked"]) == {
        "territory", "agent", "global_max", "zone_min", "zone_max", "zone_cooldown",
    }


@pytest.mark.parametrize(
    "row_id, key, value",
    [
        ("terr:RedDeer", "territory", "Wolf"),  # used to land on another territory, or nowhere
        ("agent:RedDeer:Deer", "agent", "Doe"),  # used to be skipped silently
        ("agent:RedDeer:Deer", "global_max", "99"),  # a cell that does not apply
        ("terr:RedDeer", "chance", "0.2"),
    ],
)
def test_environment_save_refuses_a_locked_cell(client, sample_config, row_id, key, value):
    path = _mission_root(sample_config) / "cfgenvironment.xml"
    path.write_text(ENVIRONMENT_XML)
    rows = {r["id"]: r for r in client.get("/api/configs/environment").json()["rows"]}
    rows[row_id]["values"][key] = value

    resp = client.put("/api/configs/environment", json={"rows": list(rows.values())})
    assert resp.status_code == 400
    assert key in resp.json()["detail"]
    assert path.read_text() == ENVIRONMENT_XML


# Lines of Bohemia's Chernarus cfgenvironment.xml, read at the pinned 1.29
# commit (comments and file lists left out): the counts only on AmbientHen, the
# cooldown on AmbientFox. Here AmbientHen's Female agent comes before its Male
# one and keeps only its first spawn. The file has no final newline; this text
# has one.
def stock_environment() -> str:
    lines = ce_lines(bohemia_ce(CE_1_29, "dayzOffline.chernarusplus/cfgenvironment.xml"))
    hen = ce_block(lines, 'name="AmbientHen"')
    fox = ce_block(lines, 'name="AmbientFox"')
    return "".join(
        ce_pick(lines, "<?xml", "<env>", "<territories>")
        + ce_block(lines, 'name="Wolf"')
        + ce_pick(hen, "<territory", "<file", 'type="Female"', "<spawn", "</agent>")
        + ce_pick(hen, 'type="Male"', "<spawn", "</agent>")
        + ce_pick(hen, '"globalCountMax"', '"zoneCountMin"', '"zoneCountMax"', "</territory>")
        + ce_pick(fox, "<territory", "<file", 'type="Male"', "<spawn", "</agent>",
                  '"zoneTouchDisableEditPeriodSec"', "</territory>")
        + ce_pick(lines, "</territories>", "</env>")
    ) + "\n"


def _stock_environment(client, sample_config):
    path = _mission_root(sample_config) / "cfgenvironment.xml"
    path.write_text(stock_environment())
    return path, {r["id"]: r for r in client.get("/api/configs/environment").json()["rows"]}


def test_environment_untouched_save_keeps_the_file_byte_for_byte(client, sample_config):
    path, rows = _stock_environment(client, sample_config)
    assert rows["terr:AmbientFox"]["values"]["zone_cooldown"] == "450"
    assert rows["terr:Wolf"]["values"]["global_max"] == ""

    assert client.put("/api/configs/environment", json={"rows": list(rows.values())}).status_code == 200
    assert path.read_text() == stock_environment()


def test_environment_edits_the_zone_cooldown(client, sample_config):
    """ce-ambient-spawner.wiki documents zoneTouchDisableEditPeriodSec as the
    zone's cooldown; the table did not show it."""
    path, rows = _stock_environment(client, sample_config)
    rows["terr:AmbientFox"]["values"]["zone_cooldown"] = "600"
    rows["terr:AmbientHen"]["values"]["global_max"] = "50.0"   # same number: not rewritten

    assert client.put("/api/configs/environment", json={"rows": list(rows.values())}).status_code == 200
    text = path.read_text()
    assert '<item name="zoneTouchDisableEditPeriodSec" val="600" />' in text
    assert '<item name="globalCountMax" val="50" />' in text


@pytest.mark.parametrize("row_id, key, value, message", [
    ("terr:AmbientHen", "global_max", "-5.5", "Global max must be a whole number of 0 or more"),
    ("terr:AmbientHen", "zone_min", "1.5", "Zone min must be a whole number of 0 or more"),
    ("terr:AmbientFox", "zone_cooldown", "abc", "Zone cooldown must be a number"),
    ("agent:AmbientHen:Male", "chance", "abc", "Agent chance must be a number"),
    ("agent:AmbientHen:Male", "chance", "-1", "Agent chance must be a number of 0 or more"),
    ("agent:AmbientHen:Male", "chance", "nan", "Agent chance must be a finite number"),
])
def test_environment_refuses_a_value_that_is_not_a_count(client, sample_config, row_id, key, value, message):
    path, rows = _stock_environment(client, sample_config)
    rows[row_id]["values"][key] = value

    resp = client.put("/api/configs/environment", json={"rows": list(rows.values())})
    assert resp.status_code == 400
    assert message in resp.json()["detail"]
    assert path.read_text() == stock_environment()


def test_environment_save_refuses_a_row_not_in_the_file(client, sample_config):
    path = _mission_root(sample_config) / "cfgenvironment.xml"
    path.write_text(ENVIRONMENT_XML)
    rows = client.get("/api/configs/environment").json()["rows"]
    rows.append({"id": "new-2-1", "values": {"territory": "Wolf", "global_max": "5"}})

    resp = client.put("/api/configs/environment", json={"rows": rows})
    assert resp.status_code == 400
    assert "Raw tab" in resp.json()["detail"]
    assert path.read_text() == ENVIRONMENT_XML


def test_effectarea_table_round_trip(client, sample_config):
    root = _mission_root(sample_config)
    (root / "cfgEffectArea.json").write_text(EFFECTAREA_JSON)

    body = client.get("/api/configs/effectarea").json()
    assert body["kind"] == "table"
    row = body["rows"][0]
    assert row["values"]["area_name"] == "Rify"
    assert row["values"]["radius"] == 150.0

    row["values"]["radius"] = 200.0
    row["values"]["pos"] = "1100,5,2100"
    resp = client.put("/api/configs/effectarea", json={"rows": [row]})
    assert resp.status_code == 200

    data = json.loads((root / "cfgEffectArea.json").read_text())
    assert data["Areas"][0]["Data"]["Radius"] == 200.0
    assert data["Areas"][0]["Data"]["Pos"] == [1100.0, 5.0, 2100.0]
    # SafePositions preserved verbatim.
    assert data["SafePositions"] == [[100, 200]]


# ---------------------------------------------------------------------------
# cfggameplay.json — grouped by its own objects since 2026 Q3 (TODO H4)
# ---------------------------------------------------------------------------

# Bohemia's shape in miniature: a top-level scalar, three of the seven objects,
# and one nested object inside PlayerData.
GAMEPLAY_JSON = """{
    "version": 123,
    "GeneralData": { "disableBaseDamage": false, "disableRespawnDialog": false },
    "PlayerData": {
        "disablePersonalLight": true,
        "StaminaData": { "sprintStaminaModifierErc": 1.0 }
    },
    "UIData": { "use3DMap": true }
}
"""


def test_gameplay_form_is_grouped_by_the_files_own_objects(client, sample_config):
    """It rendered all 65 stock leaves as one section titled with the filename
    — the widest wall of fields in the app, and the only editor measurably
    discarding structure the file itself ships."""
    (_mission_root(sample_config) / "cfggameplay.json").write_text(GAMEPLAY_JSON)

    body = client.get("/api/configs/gameplay").json()
    assert body["kind"] == "form"
    assert [s["title"] for s in body["sections"]] == ["File", "General", "Player", "UI"]

    player = next(s for s in body["sections"] if s["title"] == "Player")
    # The label drops the part the section already says; the key does not,
    # because the key is what the save writes back through.
    assert "StaminaData.sprintStaminaModifierErc" in [f["label"] for f in player["fields"]]
    assert "PlayerData.StaminaData.sprintStaminaModifierErc" in [f["key"] for f in player["fields"]]


def test_gameplay_save_still_writes_the_nested_shape(client, sample_config):
    """Grouping is a presentation change. The dotted key is the wire format and
    has to keep round-tripping into the real nesting."""
    path = _mission_root(sample_config) / "cfggameplay.json"
    path.write_text(GAMEPLAY_JSON)

    resp = client.put("/api/configs/gameplay", json={"values": {
        "PlayerData.StaminaData.sprintStaminaModifierErc": "2.5",
        "UIData.use3DMap": False,
    }})
    assert resp.status_code == 200

    data = json.loads(path.read_text())
    assert data["PlayerData"]["StaminaData"]["sprintStaminaModifierErc"] == 2.5
    assert data["UIData"]["use3DMap"] is False
    assert data["version"] == 123  # untouched keys survive


def test_gameplay_lists_are_arrays_and_keep_their_numbers(client, sample_config):
    """plans/76 step 1. A list was a comma-joined string, and saving an edited
    one wrote every item back as a string: the stock `environmentMinTemps`
    [-3, -2, ...] became ["-4", "-2", ...]. The temperatures are float arrays
    in Bohemia's wiki (gameplay-settings.wiki:410-418), so a decimal is taken
    even where Chernarus wrote whole numbers."""
    path = _mission_root(sample_config) / "cfggameplay.json"
    path.write_text(json.dumps({
        "version": 123,
        "WorldsData": {
            "environmentMinTemps": [-3, -2, 0, 4, 9, 14, 18, 17, 13, 11, 9, 0],
            "wetnessWeightModifiers": [1.0, 1.0, 1.33, 1.66, 2.0],
        },
        "BaseBuildingData": {"HologramData": {"disallowedTypesInUnderground": ["FenceKit"]}},
    }))

    body = client.get("/api/configs/gameplay").json()
    fields = {f["key"]: f for s in body["sections"] for f in s["fields"]}
    temps = fields["WorldsData.environmentMinTemps"]
    assert (temps["type"], temps["item"]) == ("list", "float")
    assert temps["value"][:3] == [-3, -2, 0]
    assert fields["WorldsData.wetnessWeightModifiers"]["item"] == "float"
    kits = fields["BaseBuildingData.HologramData.disallowedTypesInUnderground"]
    assert (kits["item"], kits["value"]) == ("str", ["FenceKit"])

    resp = client.put("/api/configs/gameplay", json={"values": {
        "WorldsData.environmentMinTemps": ["-4", -2, "-3.5", 4, 9, 14, 18, 17, 13, 11, 9, 0],
        "WorldsData.wetnessWeightModifiers": [1.0, "1.5", 1.33, 1.66, 2.0],
        "BaseBuildingData.HologramData.disallowedTypesInUnderground": ["FenceKit", "A,B"],
    }})
    assert resp.status_code == 200, resp.text
    text = path.read_text()
    data = json.loads(text)
    assert data["WorldsData"]["environmentMinTemps"] == [-4, -2, -3.5, 4, 9, 14, 18, 17, 13, 11, 9, 0]
    assert type(data["WorldsData"]["environmentMinTemps"][1]) is int  # untouched, as the file had it
    assert "[-4, -2, -3.5, 4," in text  # the whole number typed stays whole
    assert data["WorldsData"]["wetnessWeightModifiers"] == [1.0, 1.5, 1.33, 1.66, 2.0]
    assert "[1.0, 1.5, 1.33, 1.66, 2.0]" in text  # an untouched float keeps its form
    # A comma inside a value no longer splits it in two.
    assert data["BaseBuildingData"]["HologramData"]["disallowedTypesInUnderground"] == ["FenceKit", "A,B"]


def test_a_list_item_emptied_in_its_box_is_dropped(client, sample_config):
    """The browser keeps an emptied list box and sends its ``""``; every list
    editor saves through ``coerce_list``, and the file got ``[""]`` where it
    had ``[]`` (found on TraderPlus IDs, 2026 Q4). A ``""`` the file
    already holds at that place stays."""
    from app.web.editors._mod_json import coerce_list

    assert coerce_list([""], "str", []) == []
    assert coerce_list(["A", "  "], "str", ["A"]) == ["A"]
    assert coerce_list(["", "B"], "str", ["", "B"]) == ["", "B"]
    assert coerce_list([1.5, ""], "float", [1.5]) == [1.5]

    path = _mission_root(sample_config) / "cfggameplay.json"
    path.write_text(json.dumps({
        "version": 123,
        "BaseBuildingData": {"HologramData": {"disallowedTypesInUnderground": []}},
    }))
    before = path.read_text()
    resp = client.put("/api/configs/gameplay", json={"values": {
        "BaseBuildingData.HologramData.disallowedTypesInUnderground": [""],
    }})
    assert resp.status_code == 200, resp.text
    assert path.read_text() == before


@pytest.mark.parametrize("key, value, words", [
    ("WorldsData.environmentMinTemps", [-3] * 11, "exactly 12"),
    ("WorldsData.environmentMaxTemps", [-3] * 13, "exactly 12"),
    ("WorldsData.wetnessWeightModifiers", [1, 1, 1, 1], "exactly 5"),
])
def test_a_gameplay_float_array_needs_the_wikis_count(client, sample_config, key, value, words):
    """gameplay-settings.wiki: the temperatures are "12 values exactly" (:410-418)
    and the wetness weights one per level, DRY to DRENCHED (:62)."""
    path = _mission_root(sample_config) / "cfggameplay.json"
    path.write_text(json.dumps({"WorldsData": {
        "environmentMinTemps": [0] * 12, "environmentMaxTemps": [0] * 12,
        "wetnessWeightModifiers": [1.0, 1.0, 1.33, 1.66, 2.0],
    }}))
    before = path.read_bytes()
    resp = client.put("/api/configs/gameplay", json={"values": {key: value}})
    assert resp.status_code == 400
    assert words in resp.text and key in resp.text
    assert path.read_bytes() == before


def test_a_gameplay_list_of_whole_numbers_refuses_a_decimal(client, sample_config):
    path = _mission_root(sample_config) / "cfggameplay.json"
    path.write_text('{"WorldsData": {"environmentMinTemps": [-3, -2]}}')
    resp = client.put("/api/configs/gameplay", json={"values": {
        "WorldsData.environmentMinTemps": [-3, "abc"],
    }})
    assert resp.status_code == 400
    assert "environmentMinTemps: item 2" in resp.text
    assert json.loads(path.read_text())["WorldsData"]["environmentMinTemps"] == [-3, -2]


def test_the_1_30_wheel_settings_render_with_help(client, sample_config):
    """plans/66: 1.30 adds four VehicleData keys. They render from the file
    like every other key; the help is the part zsm adds."""
    (_mission_root(sample_config) / "cfggameplay.json").write_text(
        '{"version": 123, "VehicleData": {"boatDecayMultiplier": 1, '
        '"canDetachAttachedCarWheels": true, "canDamageAttachedCarWheels": true, '
        '"canDetachAttachedBikeWheels": true, "canDamageAttachedBikeWheels": true}}'
    )
    body = client.get("/api/configs/gameplay").json()
    fields = {f["key"]: f for s in body["sections"] for f in s["fields"]}
    for key in ("canDetachAttachedCarWheels", "canDamageAttachedCarWheels",
                "canDetachAttachedBikeWheels", "canDamageAttachedBikeWheels"):
        field = fields[f"VehicleData.{key}"]
        assert field["value"] is True
        assert "1.30" in field["help"], key


# The end of Bohemia's Chernarus cfggameplay.json, read at the pinned commits:
# DayZ 1.29, then DayZ 1.30, which adds the four wheel switches (first seen
# in 1.30 Experimental, build 25319221).
def vehicledata(commit: str) -> str:
    """From the "VehicleData" line to the end of the file (no final newline)."""
    lines = ce_lines(bohemia_ce(commit, "dayzOffline.chernarusplus/cfggameplay.json"))
    return "".join(lines[next(i for i, line in enumerate(lines) if '"VehicleData"' in line):])


def test_a_1_30_switch_saved_into_a_1_29_file_is_written_as_a_switch(client, sample_config):
    """A key the file does not hold has no value to take its type from, so a
    switch the form sent was written as the text "True", which DayZ does not
    read as a switch. A tab opened on the 1.30 file and saved after the file
    went back to 1.29's is what reaches it."""
    path = _mission_root(sample_config) / "cfggameplay.json"
    path.write_text('{\n\t"version": 123,\n' + vehicledata(CE_1_29))
    resp = client.put("/api/configs/gameplay", json={"values": {
        "VehicleData.canDetachAttachedCarWheels": True,
        "VehicleData.canDamageAttachedCarWheels": False,
    }})
    assert resp.status_code == 200, resp.text
    data = json.loads(path.read_text())["VehicleData"]
    assert data["canDetachAttachedCarWheels"] is True
    assert data["canDamageAttachedCarWheels"] is False


def test_a_1_30_wheel_switch_is_turned_off_in_bohemia_s_file(client, sample_config):
    path = _mission_root(sample_config) / "cfggameplay.json"
    path.write_text('{\n\t"version": 123,\n' + vehicledata(CE_1_30))
    resp = client.put("/api/configs/gameplay", json={"values": {"VehicleData.canDetachAttachedBikeWheels": False}})
    assert resp.status_code == 200, resp.text
    assert path.read_text() == '{\n\t"version": 123,\n' + vehicledata(CE_1_30).replace(
        '"canDetachAttachedBikeWheels": true', '"canDetachAttachedBikeWheels": false'
    )


def test_a_gameplay_object_bohemia_adds_later_gets_its_own_section(client, sample_config):
    """The title map is a lookup with the raw key as fallback, so a new group
    is named rather than swept into the ungrouped section."""
    path = _mission_root(sample_config) / "cfggameplay.json"
    path.write_text('{"version": 1, "SomethingNewData": {"future": 1}}')

    body = client.get("/api/configs/gameplay").json()
    assert [s["title"] for s in body["sections"]] == ["File", "SomethingNewData"]


# Lines of Bohemia's 1.29 Sakhal cfggameplay.json, read at the pinned commit
# (tabs, the one "PlayerData" line indented with four spaces, inline lists, no
# final newline), with the blocks between them left out. The one change:
# "hitDirectionBreakPointRelative" loses its comma, being last in its block here.
SAKHAL_GAMEPLAY_LINES = (
    (1, 5), (8, 12), (33, 36), (40, 41), (48, 58), (60, 62),
    (87, 88), (90, 91), (95, 95), (97, 97), (100, 101), (109, 113),
)


def sakhal_gameplay() -> str:
    lines = ce_lines(bohemia_ce(CE_1_29, "dayzOffline.sakhal/cfggameplay.json"))
    text = "".join(line for first, last in SAKHAL_GAMEPLAY_LINES for line in lines[first - 1:last])
    return text.replace('"hitDirectionBreakPointRelative": 0.2,', '"hitDirectionBreakPointRelative": 0.2', 1)


def _gameplay_fields(client) -> dict:
    body = client.get("/api/configs/gameplay").json()
    return {f["key"]: f for s in body["sections"] for f in s["fields"]}


def _send_every_field(client) -> None:
    """Save the form exactly as loaded, every field sent back, the way a page
    that sent everything would."""
    values = {key: f["value"] for key, f in _gameplay_fields(client).items()}
    resp = client.put("/api/configs/gameplay", json={"values": values})
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_a_gameplay_save_with_no_edit_leaves_the_file_byte_identical(client, sample_config, newline):
    """Every save rewrote the whole file at four spaces, so a save without an
    edit was a whole-file diff of Bohemia's tab-indented layout."""
    from app.shared.config_versions import load_index, store_for

    path = _mission_root(sample_config) / "cfggameplay.json"
    path.write_bytes(sakhal_gameplay().replace("\n", newline).encode())
    _send_every_field(client)
    assert path.read_bytes() == sakhal_gameplay().replace("\n", newline).encode()
    # Nothing changed, so no version either.
    assert load_index(store_for(path)).get_versions(path.name) == []


def test_a_gameplay_edit_changes_only_that_values_text(client, sample_config):
    path = _mission_root(sample_config) / "cfggameplay.json"
    path.write_bytes(sakhal_gameplay().replace("\n", "\r\n").encode())
    resp = client.put("/api/configs/gameplay", json={"values": {
        "PlayerData.MovementData.rotationSpeedJog": "0.25",
        "WorldsData.environmentMinTemps": [-6.5, -9.5, -6.5, -9.5, 2, 6, 9, 10, 6, 1, -5, "-10.5"],
        "VehicleData.boatDecayMultiplier": "1.5",
    }})
    assert resp.status_code == 200, resp.text
    expected = (
        sakhal_gameplay()
        .replace('"rotationSpeedJog": 0.3', '"rotationSpeedJog": 0.25')
        .replace('1, -5, -10]', '1, -5, -10.5]')
        .replace('"boatDecayMultiplier": 1', '"boatDecayMultiplier": 1.5')
        .replace("\n", "\r\n")
    )
    assert path.read_bytes() == expected.encode()


def test_a_gameplay_number_typed_whole_stays_whole_where_the_file_wrote_it_whole(client, sample_config):
    path = _mission_root(sample_config) / "cfggameplay.json"
    path.write_text(sakhal_gameplay())
    resp = client.put("/api/configs/gameplay", json={"values": {
        "VehicleData.boatDecayMultiplier": "2", "WorldsData.lightingConfig": "0",
    }})
    assert resp.status_code == 200, resp.text
    text = path.read_text()
    assert '"boatDecayMultiplier": 2\n' in text and '"lightingConfig": 0,' in text


def test_gameplay_fields_carry_the_wikis_types_and_ranges(client, sample_config):
    """gameplay-settings.wiki: boatDecayMultiplier is a float (:65-68) though
    the stock file writes 1; the temperatures are float arrays (:410-418); the
    movement times have a lowest value of 0.01 (:189-209); the obstruction and
    hit-indicator modes are 0, 1 or 2 (:315, :320, :430, :435)."""
    (_mission_root(sample_config) / "cfggameplay.json").write_text(sakhal_gameplay())
    fields = _gameplay_fields(client)
    assert fields["VehicleData.boatDecayMultiplier"]["type"] == "float"
    assert fields["WorldsData.environmentMinTemps"]["item"] == "float"
    assert fields["PlayerData.MovementData.rotationSpeedJog"]["min"] == 0.01
    assert fields["PlayerData.WeaponObstructionData.staticMode"]["choices"] == ["0", "1", "2"]
    assert fields["WorldsData.lightingConfig"]["type"] == "int"
    hit = fields["UIData.HitIndicationData.hitDirectionBreakPointRelative"]
    assert (hit["min"], hit["max"]) == (0.0, 1.0)


@pytest.mark.parametrize("key, value, words", [
    ("PlayerData.MovementData.rotationSpeedJog", "abc", "not a number"),
    ("PlayerData.MovementData.rotationSpeedJog", "1e999", "not a number"),
    ("PlayerData.MovementData.rotationSpeedJog", "0", "below 0.01"),
    ("WorldsData.lightingConfig", "1.5", "whole number"),
    ("PlayerData.WeaponObstructionData.staticMode", "3", "one of 0, 1, 2"),
    ("UIData.HitIndicationData.hitDirectionBreakPointRelative", "1.5", "above 1"),
    ("UIData.HitIndicationData.hitDirectionIndicatorColorStr", "red", "0xAARRGGBB"),
    ("UIData.HitIndicationData.hitDirectionIndicatorColorStr", "0xffbb0a1", "0xAARRGGBB"),
    ("PlayerData.disablePersonalLight", "maybe", "on or off"),
    ("WorldsData.environmentMinTemps", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, "1e999"], "item 12"),
])
def test_a_gameplay_value_zsm_cannot_write_is_refused(client, sample_config, key, value, words):
    """A failed cast was swallowed and the raw string saved: "abc" went into
    the file as a string where DayZ reads a number."""
    path = _mission_root(sample_config) / "cfggameplay.json"
    path.write_text(sakhal_gameplay())
    resp = client.put("/api/configs/gameplay", json={"values": {key: value}})
    assert resp.status_code == 400, resp.text
    assert words in resp.text and key in resp.text
    assert path.read_text() == sakhal_gameplay()


def test_a_gameplay_switch_reads_false_as_off(client, sample_config):
    """`bool("false")` is True, so a switch sent as text was saved on."""
    path = _mission_root(sample_config) / "cfggameplay.json"
    path.write_text(sakhal_gameplay().replace('"disableBaseDamage": false', '"disableBaseDamage": true'))
    resp = client.put("/api/configs/gameplay", json={"values": {
        "GeneralData.disableBaseDamage": "false",
        "UIData.HitIndicationData.hitDirectionIndicatorColorStr": "0XFFBB0A1F",  # any case
    }})
    assert resp.status_code == 200, resp.text
    data = json.loads(path.read_text())
    assert data["GeneralData"]["disableBaseDamage"] is False
    assert data["UIData"]["HitIndicationData"]["hitDirectionIndicatorColorStr"] == "0XFFBB0A1F"


def test_gameplay_help_says_what_the_wiki_and_the_stock_files_say():
    """rotationSpeedJog: the wiki's default is 0.15 and all three stock files
    ship 0.3. lightingConfig: the wiki's default is 1, and 2 is Sakhal's
    lighting (server-configuration.wiki:121). staminaWeightLimitThreshold is
    stamina points divided by 1000 (gameplay-settings.wiki:113), not grams.
    The cold-area key has two names, and neither is renamed."""
    from app.parsers.cfggameplay import FIELD_DESCRIPTIONS as desc

    assert "0.15" in desc["rotationSpeedJog"] and "0.3" in desc["rotationSpeedJog"]
    assert "default is 1" in desc["lightingConfig"] and "2 = Sakhal" in desc["lightingConfig"]
    assert "1000" in desc["staminaWeightLimitThreshold"]
    assert "grams (" not in desc["staminaWeightLimitThreshold"]
    for key in ("disableColdAreaBuildingCheck", "disableColdAreaPlacementCheck"):
        assert "keeps the name your file has" in desc[key]


def test_a_gameplay_file_that_is_not_json_is_never_overwritten(client, sample_config):
    """It read as Bohemia's defaults, the form showed them, and the next save
    wrote them over the admin's file."""
    path = _mission_root(sample_config) / "cfggameplay.json"
    broken = sakhal_gameplay().replace('"lightingConfig": 2,', '"lightingConfig": 2,,')
    path.write_text(broken)

    resp = client.get("/api/configs/gameplay")
    assert resp.status_code == 400
    assert "Raw tab" in resp.text
    resp = client.put("/api/configs/gameplay", json={"values": {"version": "124"}})
    assert resp.status_code == 400
    assert path.read_text() == broken
    # The Raw tab still opens it, which is where it is fixed.
    assert client.get("/api/configs/gameplay/raw").status_code == 200


# ---------------------------------------------------------------------------
# cfglimitsdefinitionuser.xml — table kind since 2026 Q3 (TODO H4)
# ---------------------------------------------------------------------------

# Bohemia's shape, read at the pinned 1.29 commit. Sakhal's file, because its
# line ends are LF; Chernarus' has the same lines with CRLF. The member
# attribute is `name`; the code read `user` until 2026 Q3 and so saw every
# group as empty. A fixture written to the old code would hide that all over
# again.
def limitsuser_xml() -> str:
    """Lines 1-7, 14-20 and 25-28 and the last two: TownVillage, Tier12, Tier34."""
    lines = ce_lines(bohemia_ce(CE_1_29, "dayzOffline.sakhal/cfglimitsdefinitionuser.xml"))
    return "".join(lines[0:7] + lines[13:20] + lines[24:28] + lines[-2:])


def _limitsuser_rows(client):
    body = client.get("/api/configs/limitsdefinitionuser").json()
    assert body["kind"] == "table"
    return body, {row["id"]: row for row in body["rows"]}


def test_limitsdefinitionuser_is_a_table_of_whole_groups(client, sample_config):
    """One row per group, not per flag: a group is the unit people think in,
    and there are eight of them on Chernarus, not thousands."""
    (_mission_root(sample_config) / "cfglimitsdefinitionuser.xml").write_text(limitsuser_xml())

    body, rows = _limitsuser_rows(client)
    assert [c["key"] for c in body["columns"]] == ["section", "group", "flags"]
    assert set(rows) == {"usage:TownVillage", "value:Tier12", "value:Tier34"}
    assert rows["usage:TownVillage"]["values"]["flags"] == "Town, Village"
    assert rows["value:Tier12"]["values"]["section"] == "value"


def test_limitsdefinitionuser_edit_add_and_remove_round_trip(client, sample_config):
    path = _mission_root(sample_config) / "cfglimitsdefinitionuser.xml"
    path.write_text(limitsuser_xml())

    _, rows = _limitsuser_rows(client)
    rows["value:Tier12"]["values"]["flags"] = "Tier1, Tier2, Tier3"
    keep = [rows["usage:TownVillage"], rows["value:Tier12"]]
    keep.append({"id": "", "values": {"section": "usage", "group": "CoastZone", "flags": "Coast"}})

    assert client.put("/api/configs/limitsdefinitionuser", json={"rows": keep}).status_code == 200

    _, rows = _limitsuser_rows(client)
    assert rows["value:Tier12"]["values"]["flags"] == "Tier1, Tier2, Tier3"
    assert rows["usage:CoastZone"]["values"]["flags"] == "Coast"
    assert "value:Tier34" not in rows  # dropped by not being in the payload


def test_limitsdefinitionuser_keeps_a_value_group_a_value_group(client, sample_config):
    """The underlying editor hardcoded its child tag to <usage> until today, so
    editing a value group produced one the server reads as having no values.
    Checked through the route because that is the path a person takes."""
    path = _mission_root(sample_config) / "cfglimitsdefinitionuser.xml"
    path.write_text(limitsuser_xml())

    _, rows = _limitsuser_rows(client)
    rows["value:Tier34"]["values"]["flags"] = "Tier3, Tier4, Tier1"
    assert client.put(
        "/api/configs/limitsdefinitionuser", json={"rows": list(rows.values())}
    ).status_code == 200

    text = path.read_text()
    assert '<value name="Tier1"' in text
    assert "<usage" not in text.split("<valueflags>")[1]


def test_limitsdefinitionuser_save_keeps_a_mods_merge_markers(client, sample_config):
    """The route is the path a person takes, and the bug this guards was found
    from the other end: both limits parsers dropped every comment, so a save
    stranded a mod's entries in the file with nothing able to remove them.
    `xml_merge.remove_mod_xml_blocks` matches these markers as text."""
    path = _mission_root(sample_config) / "cfglimitsdefinitionuser.xml"
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        "<user_lists>\n"
        "    <valueflags>\n"
        '        <user name="Tier12">\n'
        '            <value name="Tier1" />\n'
        "        </user>\n"
        "        <!-- START MOD: @SomeMod -->\n"
        '        <user name="ModTier">\n'
        '            <value name="Tier1" />\n'
        "        </user>\n"
        "        <!-- END MOD: @SomeMod -->\n"
        "    </valueflags>\n"
        "</user_lists>\n"
    )

    _, rows = _limitsuser_rows(client)
    assert set(rows) == {"value:Tier12", "value:ModTier"}
    rows["value:Tier12"]["values"]["flags"] = "Tier1, Tier2"
    assert client.put(
        "/api/configs/limitsdefinitionuser", json={"rows": list(rows.values())}
    ).status_code == 200

    text = path.read_text()
    assert "START MOD: @SomeMod" in text and "END MOD: @SomeMod" in text


def test_limitsdefinitionuser_tolerates_the_files_own_section_spelling(client, sample_config):
    """Someone reading the XML sees <usageflags>, so typing that into the
    Section cell is a fair guess — and silently dropping their row would be the
    worst possible answer to it."""
    path = _mission_root(sample_config) / "cfglimitsdefinitionuser.xml"
    path.write_text(limitsuser_xml())

    _, rows = _limitsuser_rows(client)
    payload = list(rows.values())
    payload.append({"id": "", "values": {"section": "valueflags", "group": "Tier1", "flags": "Tier1"}})

    assert client.put("/api/configs/limitsdefinitionuser", json={"rows": payload}).status_code == 200
    _, rows = _limitsuser_rows(client)
    assert rows["value:Tier1"]["values"]["flags"] == "Tier1"


def test_limitsdefinitionuser_section_is_a_choice(client, sample_config):
    (_mission_root(sample_config) / "cfglimitsdefinitionuser.xml").write_text(limitsuser_xml())
    body, _ = _limitsuser_rows(client)
    section = next(c for c in body["columns"] if c["key"] == "section")
    assert section["choices"] == ["usage", "value"]


def test_limitsdefinitionuser_added_row_without_a_section_is_refused_not_dropped(client, sample_config):
    """Add row leaves the Section cell empty. Such a row was dropped while the
    save said "Saved."; now it is a 400 naming the row, and a row left wholly
    blank is still just ignored."""
    path = _mission_root(sample_config) / "cfglimitsdefinitionuser.xml"
    path.write_text(limitsuser_xml())
    _, rows = _limitsuser_rows(client)
    blank = {"id": "new-3-1", "values": {"section": "", "group": "", "flags": ""}}

    assert client.put(
        "/api/configs/limitsdefinitionuser", json={"rows": [*rows.values(), blank]}
    ).status_code == 200

    named = {"id": "new-3-2", "values": {"section": "", "group": "CoastZone", "flags": "Coast"}}
    before = path.read_text()
    resp = client.put("/api/configs/limitsdefinitionuser", json={"rows": [*rows.values(), named]})
    assert resp.status_code == 400
    assert "CoastZone" in resp.json()["detail"] and "usage, value" in resp.json()["detail"]
    assert path.read_text() == before

    named["values"]["section"] = "usage"
    assert client.put(
        "/api/configs/limitsdefinitionuser", json={"rows": [*rows.values(), named]}
    ).status_code == 200
    _, rows = _limitsuser_rows(client)
    assert rows["usage:CoastZone"]["values"]["flags"] == "Coast"


def test_limitsdefinitionuser_refuses_a_mistyped_section(client, sample_config):
    """A typo used to drop the row, which removed the group from the file."""
    path = _mission_root(sample_config) / "cfglimitsdefinitionuser.xml"
    path.write_text(limitsuser_xml())
    _, rows = _limitsuser_rows(client)
    rows["value:Tier34"]["values"]["section"] = "vlaue"

    resp = client.put("/api/configs/limitsdefinitionuser", json={"rows": list(rows.values())})
    assert resp.status_code == 400
    assert "Tier34" in resp.json()["detail"]
    assert path.read_text() == limitsuser_xml()


def test_limitsdefinition_refuses_a_mistyped_section(client, sample_config):
    """A typo in the Section cell used to delete the entry from the file."""
    path = _mission_root(sample_config) / "cfglimitsdefinition.xml"
    path.write_text(stock_shaped_limits())
    body = client.get("/api/configs/limitsdefinition").json()
    section = next(c for c in body["columns"] if c["key"] == "section")
    assert section["choices"] == ["categories", "tags", "usages", "values"]

    rows = {r["id"]: r for r in body["rows"]}
    rows["usages:Military"]["values"]["section"] = "usage"
    resp = client.put("/api/configs/limitsdefinition", json={"rows": list(rows.values())})
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "Military" in detail and "categories, tags, usages, values" in detail
    assert path.read_text() == stock_shaped_limits()

    # A row left blank is ignored; a named one in a real section is added.
    rows["usages:Military"]["values"]["section"] = "usages"
    payload = [
        *rows.values(),
        {"id": "new-4-1", "values": {"section": "", "name": ""}},
        {"id": "new-4-2", "values": {"section": "tags", "name": "shelves"}},
    ]
    assert client.put("/api/configs/limitsdefinition", json={"rows": payload}).status_code == 200
    ids = {r["id"] for r in client.get("/api/configs/limitsdefinition").json()["rows"]}
    assert ids == {"categories:tools", "tags:floor", "tags:shelves", "usages:Military", "values:Tier1"}


def test_limitsdefinition_refuses_removing_a_name_types_still_use(client, sample_config):
    """"categories / tools" was removed and saved while
    316 types named it; DayZ needs every name a type uses defined."""
    root = _mission_root(sample_config)
    path = root / "cfglimitsdefinition.xml"
    path.write_text(stock_shaped_limits())
    (root / "db").mkdir(exist_ok=True)
    (root / "db" / "types.xml").write_text(
        '<types><type name="Pliers"><category name="tools"/></type>'
        '<type name="Saw"><category name="tools"/><usage name="Military"/></type></types>'
    )
    rows = client.get("/api/configs/limitsdefinition").json()["rows"]
    kept = [r for r in rows if r["id"] != "categories:tools"]
    resp = client.put("/api/configs/limitsdefinition", json={"rows": kept})
    assert resp.status_code == 400
    assert "category tools (2 types)" in resp.json()["detail"]
    assert path.read_text() == stock_shaped_limits()
    # A name no type uses still goes.
    kept = [r for r in rows if r["id"] != "tags:floor"]
    assert client.put("/api/configs/limitsdefinition", json={"rows": kept}).status_code == 200


# ---------------------------------------------------------------------------
# cfgundergroundtriggers.json — a form with a tree (TODO H4, plans/76 step 2b)
# ---------------------------------------------------------------------------

# Shaped like Sakhal's, which is the one that matters: 30 triggers there, 8 on
# Livonia, an empty stub on Chernarus. `Comment` and `ParentNetworkId` are
# Sakhal-only keys — they are in this fixture precisely because no renderer in
# zsm knows them. The second trigger lacks them, as Livonia's do, and the
# first one's breadcrumbs differ in keys the way Bohemia's do.
UNDERGROUND_JSON = """{
    "Triggers":
    [
        {
            "Position": [ 9663.464844, 211.883530, 8466.494141 ],
            "Orientation": [ 45, 0, 0 ],
            "Size": [ 30, 8, 20 ],
            "EyeAccommodation": 1,
            "Comment": "Sakhal ships this key and no other terrain does",
            "ParentNetworkId": 12345,
            "Breadcrumbs":
            [
                { "Position": [ 9674.9, 213.5, 8469.3 ], "EyeAccommodation": 1, "Radius": -1 },
                { "Position": [ 9672.067383, 213.062958, 8467.806641 ], "EyeAccommodation": 0.7 }
            ]
        },
        {
            "Position": [ 100.000000, 5.500000, 200.000000 ],
            "Orientation": [ 0, 0, 0 ],
            "Size": [ 10, 4, 10 ],
            "EyeAccommodation": 0,
            "Breadcrumbs": []
        }
    ]
}
"""


def _underground(sample_config, text=UNDERGROUND_JSON, name="cfgundergroundtriggers.json"):
    path = _mission_root(sample_config) / name
    path.write_text(text)
    return path


def _underground_save(client, edit):
    base = client.get("/api/configs/undergroundtriggers").json()["nested"]
    return client.put(
        "/api/configs/undergroundtriggers",
        json={"values": {"$nested": edit(json.loads(json.dumps(base))), "$nested_base": base}},
    )


def test_undergroundtriggers_is_a_form_with_the_triggers_as_a_tree(client, sample_config):
    """Until 2026 Q3 this file had no surface anywhere in the app — zero
    references outside dayz-docs/ — while shipping 30 triggers on Sakhal. It
    was raw text until plans/76 step 2b."""
    _underground(sample_config)

    body = client.get("/api/configs/undergroundtriggers").json()
    assert body["kind"] == "form"
    triggers = body["nested"]["Triggers"]
    assert len(triggers) == 2
    assert triggers[0]["ParentNetworkId"] == 12345
    # The type is derived and shown, never a value anyone sets.
    (types,) = body["sections"]
    assert [(f["label"], f["value"], f["readonly"]) for f in types["fields"]] == [
        ("Sakhal ships this key and no other terrain does", "Transitional", True),
        ("Trigger 2", "Inner", True),
    ]


def test_undergroundtriggers_keeps_the_keys_no_renderer_knows(client, sample_config):
    """Sakhal's triggers carry 12 keys and Livonia's 7. The design decision is
    preserve-untouched: a save must never drop a
    key because this version of zsm has not heard of it, nor give an entry a
    key it did not have. Raw text got that for free; the tree has to earn it,
    through a reorder too, where every entry lands beside different keys."""
    path = _underground(sample_config)

    def edit(nested):
        triggers = nested["Triggers"]
        triggers[1]["EyeAccommodation"] = "1"
        triggers[0]["Breadcrumbs"].reverse()
        triggers.reverse()
        return nested

    assert _underground_save(client, edit).status_code == 200

    after = json.loads(path.read_text())["Triggers"]
    assert set(after[0]) == {"Position", "Orientation", "Size", "EyeAccommodation", "Breadcrumbs"}
    assert after[0]["EyeAccommodation"] == 1 and isinstance(after[0]["EyeAccommodation"], int)
    sakhal = after[1]
    assert sakhal["ParentNetworkId"] == 12345
    assert sakhal["Comment"].startswith("Sakhal ships")
    assert sakhal["Breadcrumbs"] == [
        {"Position": [9672.067383, 213.062958, 8467.806641], "EyeAccommodation": 0.7},
        {"Position": [9674.9, 213.5, 8469.3], "EyeAccommodation": 1, "Radius": -1},
    ]


def test_undergroundtriggers_keeps_bohemia_s_number_text_and_layout(client, sample_config):
    """Written the way the map layer writes it: an untouched number keeps its
    text (`211.883530`, not `211.88353`) and a position stays on one line."""
    path = _underground(sample_config)

    def edit(nested):
        nested["Triggers"][0]["Size"][0] = "35"
        return nested

    assert _underground_save(client, edit).status_code == 200
    text = path.read_text()
    assert '"Position": [ 9663.464844, 211.883530, 8466.494141 ]' in text
    assert '"Size": [ 35, 8, 20 ]' in text


def test_undergroundtriggers_takes_a_decimal_where_the_engine_reads_one(client, sample_config):
    """The wiki's sizes and eye accommodation are floats that JSON writes whole
    (`[ 30, 8, 20 ]`, `0`); found in the browser when 0.5 was refused. A flag
    the shape does not list stays whole."""
    path = _underground(sample_config)

    def edit(nested):
        nested["Triggers"][1]["Size"][1] = "4.5"
        nested["Triggers"][1]["EyeAccommodation"] = "0.25"
        nested["Triggers"][0]["Orientation"][0] = "45.5"
        return nested

    assert _underground_save(client, edit).status_code == 200
    after = json.loads(path.read_text())["Triggers"]
    assert after[1]["Size"] == [10, 4.5, 10]
    assert after[1]["EyeAccommodation"] == 0.25
    assert after[0]["Orientation"] == [45.5, 0, 0]


def test_undergroundtriggers_adds_a_whole_trigger_to_an_empty_stub(client, sample_config):
    """Chernarus ships `"Triggers": []`. Without a template the form read it as
    a list of names, and a typed name was refused as "A trigger must be an
    object". Add now puts the editor's template there, and it saves."""
    path = _underground(sample_config, text='{\n    "Triggers": []\n}\n')
    body = client.get("/api/configs/undergroundtriggers").json()
    template = body["nested_templates"]["Triggers"]
    crumb = body["nested_templates"]["Breadcrumbs"]

    def edit(nested):
        entry = json.loads(json.dumps(template))
        entry["Size"][0] = "12.5"
        entry["Breadcrumbs"] = [json.loads(json.dumps(crumb))]
        nested["Triggers"] = [entry]
        return nested

    assert _underground_save(client, edit).status_code == 200
    after = json.loads(path.read_text())["Triggers"]
    assert after[0]["Size"] == [12.5, 5.0, 10.0]
    assert after[0]["Breadcrumbs"][0]["Radius"] == -1
    # The ambient sound is in the file: the form can never add a key later.
    assert after[0]["AmbientSoundSet"] == "Underground_SoundSet"
    # The middle of the terrain, not the map's corner.
    assert after[0]["Position"][0] == after[0]["Position"][2] > 0


def test_undergroundtriggers_save_with_no_edits_leaves_a_crlf_file_byte_identical(client, sample_config):
    """Bohemia's files are CRLF and hand-laid-out; a save rewrote the whole
    file with LF. An edit now changes its own line and nothing else."""
    path = _mission_root(sample_config) / "cfgundergroundtriggers.json"
    crlf = UNDERGROUND_JSON.replace("\n", "\r\n").encode()
    path.write_bytes(crlf)

    assert _underground_save(client, lambda nested: nested).status_code == 200
    assert path.read_bytes() == crlf

    def edit(nested):
        nested["Triggers"][1]["EyeAccommodation"] = "0.5"
        return nested

    assert _underground_save(client, edit).status_code == 200
    assert path.read_bytes() == crlf.replace(b'"EyeAccommodation": 0,', b'"EyeAccommodation": 0.5,')


@pytest.mark.parametrize("key,value", [
    ("EyeAccommodation", "1.5"),             # 0..1 (underground-areas-configuration.wiki:13)
    ("Size", ["10", "4"]),                   # X, Y, Z (:32)
    ("Size", ["10", "0", "10"]),
])
def test_undergroundtriggers_refuses_a_value_the_file_cannot_hold(key, value, client, sample_config):
    path = _underground(sample_config)
    before = path.read_text()

    def edit(nested):
        nested["Triggers"][1][key] = value
        return nested

    resp = _underground_save(client, edit)
    assert resp.status_code == 400
    assert "Trigger 2" in resp.json()["detail"]
    assert path.read_text() == before


def test_undergroundtriggers_refuses_a_stale_base_and_leaves_the_file(client, sample_config):
    path = _underground(sample_config)
    base = client.get("/api/configs/undergroundtriggers").json()["nested"]
    newer = UNDERGROUND_JSON.replace('"EyeAccommodation": 0,', '"EyeAccommodation": 0.5,')
    path.write_text(newer)

    resp = client.put(
        "/api/configs/undergroundtriggers",
        json={"values": {"$nested": {"Triggers": []}, "$nested_base": base}},
    )
    assert resp.status_code == 400
    assert "Triggers changed in the file" in resp.json()["detail"]
    assert path.read_text() == newer


def test_undergroundtriggers_raw_tab_refuses_broken_json_and_leaves_the_file(client, sample_config):
    path = _underground(sample_config)

    assert client.put(
        "/api/configs/undergroundtriggers/raw", json={"content": "{not json"}
    ).status_code == 400
    assert "ParentNetworkId" in path.read_text()


def test_undergroundtriggers_is_found_whatever_bohemia_capitalises_it(client, sample_config):
    """The same file is cfgEffectArea.json on Chernarus and cfgeffectarea.json
    on Sakhal; cfgIgnoreList.xml varies the same way. All three stock missions
    spell this one in lower case today, and that is not a guarantee."""
    _underground(sample_config, name="cfgUndergroundTriggers.json")

    body = client.get("/api/configs/undergroundtriggers").json()
    assert body["nested"]["Triggers"][0]["ParentNetworkId"] == 12345


EVENTSPAWNS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<eventposdef>
    <event name="StaticHeliCrash">
        <pos x="1000.000000" z="2000.000000" a="-1.000000"/>
        <pos x="1100.000000" z="2100.000000" a="90.000000"/>
    </event>
    <!-- START MOD: @Bikes -->
    <event name="BikeSpawn">
        <pos x="500.000000" z="600.000000" a="0.000000"/>
    </event>
    <!-- END MOD: @Bikes -->
</eventposdef>
"""


def test_eventspawns_table_round_trip(client, sample_config):
    root = _mission_root(sample_config)
    (root / "cfgeventspawns.xml").write_text(EVENTSPAWNS_XML)

    body = client.get("/api/configs/eventspawns").json()
    assert body["kind"] == "table"
    assert body["filter_key"] == "event"
    rows = body["rows"]
    assert [r["id"] for r in rows] == ["StaticHeliCrash#0", "StaticHeliCrash#1", "BikeSpawn#0"]
    assert rows[0]["values"] == {"event": "StaticHeliCrash", "x": 1000.0, "z": 2000.0, "y": "", "a": -1.0}

    rows[0]["values"]["x"] = "1234.5"
    rows.pop(1)  # drop StaticHeliCrash#1
    rows.append({"id": "new-1", "values": {"event": "StaticHeliCrash", "x": 7, "z": 8, "a": 45}})
    assert client.put("/api/configs/eventspawns", json={"rows": rows}).status_code == 200

    body = client.get("/api/configs/eventspawns").json()
    by_event = {}
    for row in body["rows"]:
        by_event.setdefault(row["values"]["event"], []).append(row["values"])
    assert by_event["StaticHeliCrash"] == [
        {"event": "StaticHeliCrash", "x": 1234.5, "z": 2000.0, "y": "", "a": -1.0},
        {"event": "StaticHeliCrash", "x": 7.0, "z": 8.0, "y": "", "a": 45.0},
    ]
    assert by_event["BikeSpawn"] == [{"event": "BikeSpawn", "x": 500.0, "z": 600.0, "y": "", "a": 0.0}]


def test_eventspawns_save_keeps_mod_merge_comments(client, sample_config):
    root = _mission_root(sample_config)
    path = root / "cfgeventspawns.xml"
    path.write_text(EVENTSPAWNS_XML)

    rows = client.get("/api/configs/eventspawns").json()["rows"]
    assert client.put("/api/configs/eventspawns", json={"rows": rows}).status_code == 200

    content = path.read_text()
    assert "<!-- START MOD: @Bikes -->" in content
    assert "<!-- END MOD: @Bikes -->" in content


def test_eventspawns_new_event_row_creates_event(client, sample_config):
    root = _mission_root(sample_config)
    (root / "cfgeventspawns.xml").write_text(EVENTSPAWNS_XML)

    rows = client.get("/api/configs/eventspawns").json()["rows"]
    rows.append({"id": "new-0", "values": {"event": "VehicleSedan", "x": "1", "z": "2", "a": ""}})
    assert client.put("/api/configs/eventspawns", json={"rows": rows}).status_code == 200

    body = client.get("/api/configs/eventspawns").json()
    added = [r["values"] for r in body["rows"] if r["values"]["event"] == "VehicleSedan"]
    # An empty angle writes none: a position without `a` is as valid as one with.
    assert added == [{"event": "VehicleSedan", "x": 1.0, "z": 2.0, "y": "", "a": ""}]


# Lines of Bohemia's 1.29 Chernarus cfgeventspawns.xml, read at the pinned
# commit (its mixed tabs and spaces included): positions with and without `a`,
# with and without `y`, and numbers with as few decimals as the author typed.
def stock_eventspawns() -> str:
    lines = ce_lines(bohemia_ce(CE_1_29, "dayzOffline.chernarusplus/cfgeventspawns.xml"))
    santa = ce_block(lines, 'name="StaticSantaCrash"')
    bonfire = ce_block(lines, 'name="StaticBonfire"')
    return "".join(
        lines[:2] + santa[:4] + ce_pick(santa, 'x="8483.96"') + santa[-1:]
        + bonfire[:4] + bonfire[-1:] + lines[-1:]
    )


def _stock_eventspawns(sample_config):
    path = _mission_root(sample_config) / "cfgeventspawns.xml"
    path.write_text(stock_eventspawns())
    return path


def test_eventspawns_save_of_untouched_rows_keeps_the_file_byte_for_byte(client, sample_config):
    """An untouched save rewrote every number as 2593.470000 and added
    a="0.000000" to every position that has none (222 on Chernarus)."""
    path = _stock_eventspawns(sample_config)
    rows = client.get("/api/configs/eventspawns").json()["rows"]
    assert [r["values"]["y"] for r in rows] == [201.66, 238.0, "", "", ""]
    assert [r["values"]["a"] for r in rows] == [0.0, 0.0, 0.0, "", ""]

    assert client.put("/api/configs/eventspawns", json={"rows": rows}).status_code == 200

    assert path.read_text() == stock_eventspawns()


def test_eventspawns_writes_only_the_value_that_changed(client, sample_config):
    path = _stock_eventspawns(sample_config)
    rows = client.get("/api/configs/eventspawns").json()["rows"]
    rows[3]["values"]["x"] = "8100"      # a Bonfire position, which has no a
    rows[0]["values"]["y"] = "205.5"     # the Santa crash's height
    rows[1]["values"]["y"] = ""          # cleared: the attribute goes
    rows[2]["values"]["a"] = ""          # cleared: the attribute goes

    assert client.put("/api/configs/eventspawns", json={"rows": rows}).status_code == 200

    text = path.read_text()
    assert '<pos x="8100.000000" z="13023.365234"/>' in text
    assert '<pos x="2593.47" z="5094.11" a="0" y="205.500000"/>' in text
    assert '<pos x="2020.90" z="9813.24" a="0"/>' in text
    assert '<pos x="8483.96" z="13343.2"/>' in text


@pytest.mark.parametrize("key, value, message", [
    ("x", "nan", "X must be a finite number"),
    ("z", "abc", "Z must be a number"),
    ("a", "inf", "Angle must be a finite number"),
    ("y", "high", "Height must be a number"),
    ("x", "99999", "x is outside the map (0–15360)"),
    ("z", "-1", "z is outside the map (0–15360)"),
    ("x", "", "X and Z are required"),
])
def test_eventspawns_refuses_a_cell_it_cannot_write(client, sample_config, key, value, message):
    """`nan` was written through, text kept the old value without a word, and
    x=99999 landed off the map. The bound is the map editor's own."""
    path = _stock_eventspawns(sample_config)
    rows = client.get("/api/configs/eventspawns").json()["rows"]
    rows[0]["values"][key] = value

    resp = client.put("/api/configs/eventspawns", json={"rows": rows})

    assert resp.status_code == 400
    assert message in resp.json()["detail"]
    assert path.read_text() == stock_eventspawns()


def test_eventspawns_takes_any_angle_the_stock_files_use(client, sample_config):
    """Bohemia's files hold angles from -135 to 531.28, so only a number that
    is not finite is refused, not one outside 0..360."""
    path = _stock_eventspawns(sample_config)
    rows = client.get("/api/configs/eventspawns").json()["rows"]
    rows[0]["values"]["a"] = "531.28"
    rows[3]["values"]["a"] = "-135"

    assert client.put("/api/configs/eventspawns", json={"rows": rows}).status_code == 200
    text = path.read_text()
    assert 'a="531.280000"' in text and 'a="-135.000000"' in text


EVENTGROUPS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<eventgroupdef>
    <group name="StaticHeliCrash">
        <child type="Wreck_UH1Y" deloot="1" lootmax="0" lootmin="0" x="0.0" y="0.0" z="0.0" a="0.0"/>
        <child type="LandMineTrap" deloot="0" lootmax="3" lootmin="1" x="3.0" y="0.5" z="1.5" a="45.0"/>
    </group>
    <!-- START MOD: @Bikes -->
    <group name="BikeWreck">
        <child type="Bike" deloot="0" lootmax="1" lootmin="1" x="1.0" y="0.0" z="2.0" a="90.0"/>
    </group>
    <!-- END MOD: @Bikes -->
</eventgroupdef>
"""


def _eventgroups_row(group, type_, deloot, lootmin, lootmax, x, y, z, a):
    return {
        "group": group, "type": type_, "deloot": deloot,
        "lootmin": lootmin, "lootmax": lootmax,
        "x": x, "y": y, "z": z, "a": a,
    }


def test_eventgroups_is_a_table_not_raw_text(client, sample_config):
    """It shipped as a kind="text" fallback until 2026 Q3 on the grounds that
    it was too nested for a flat renderer — which cfgeventspawns had already
    disproved for this exact shape. Guards the regression."""
    (_mission_root(sample_config) / "cfgeventgroups.xml").write_text(EVENTGROUPS_XML)
    assert client.get("/api/configs/eventgroups/meta").json()["kind"] == "table"


def test_eventgroups_table_round_trip(client, sample_config):
    root = _mission_root(sample_config)
    (root / "cfgeventgroups.xml").write_text(EVENTGROUPS_XML)

    body = client.get("/api/configs/eventgroups").json()
    assert body["kind"] == "table"
    assert body["filter_key"] == "group"
    rows = body["rows"]
    assert [r["id"] for r in rows] == [
        "StaticHeliCrash#0", "StaticHeliCrash#1", "BikeWreck#0",
    ]
    assert rows[0]["values"] == _eventgroups_row(
        "StaticHeliCrash", "Wreck_UH1Y", True, 0, 0, 0.0, 0.0, 0.0, 0.0
    )
    # deloot is a checkbox column, so it has to arrive as a real bool.
    assert rows[1]["values"]["deloot"] is False

    rows[0]["values"]["type"] = "Wreck_Mi8"
    rows[0]["values"]["deloot"] = False
    rows[0]["values"]["lootmax"] = "4"       # the client sends text-input values
    rows.pop(1)                              # drop StaticHeliCrash#1
    rows.append({
        "id": "new-1",
        "values": _eventgroups_row("StaticHeliCrash", "Barrel", True, 1, 2, 7, 8, 9, 45),
    })
    assert client.put("/api/configs/eventgroups", json={"rows": rows}).status_code == 200

    body = client.get("/api/configs/eventgroups").json()
    by_group = {}
    for row in body["rows"]:
        by_group.setdefault(row["values"]["group"], []).append(row["values"])
    assert by_group["StaticHeliCrash"] == [
        _eventgroups_row("StaticHeliCrash", "Wreck_Mi8", False, 0, 4, 0.0, 0.0, 0.0, 0.0),
        _eventgroups_row("StaticHeliCrash", "Barrel", True, 1, 2, 7.0, 8.0, 9.0, 45.0),
    ]
    assert by_group["BikeWreck"] == [
        _eventgroups_row("BikeWreck", "Bike", False, 1, 1, 1.0, 0.0, 2.0, 90.0)
    ]


def test_eventgroups_empty_file_still_takes_a_first_row(client, sample_config):
    """A mission that has never been merged into holds a bare <eventgroupdef/>,
    so the table opens with no rows and has to stay usable."""
    (_mission_root(sample_config) / "cfgeventgroups.xml").write_text(
        '<?xml version="1.0"?><eventgroupdef/>'
    )

    body = client.get("/api/configs/eventgroups").json()
    assert body["kind"] == "table" and body["rows"] == []
    assert [c["key"] for c in body["columns"]][0] == "group"

    rows = [{"id": "new-0", "values": {"group": "TruckWreck", "type": "Truck"}}]
    assert client.put("/api/configs/eventgroups", json={"rows": rows}).status_code == 200
    saved = client.get("/api/configs/eventgroups").json()["rows"]
    assert [r["values"]["group"] for r in saved] == ["TruckWreck"]


def test_eventgroups_save_keeps_mod_merge_comments(client, sample_config):
    path = _mission_root(sample_config) / "cfgeventgroups.xml"
    path.write_text(EVENTGROUPS_XML)

    rows = client.get("/api/configs/eventgroups").json()["rows"]
    assert client.put("/api/configs/eventgroups", json={"rows": rows}).status_code == 200

    content = path.read_text()
    assert "<!-- START MOD: @Bikes -->" in content
    assert "<!-- END MOD: @Bikes -->" in content


def test_eventgroups_new_group_row_creates_the_group(client, sample_config):
    """An empty <group> has no row, so typing a new name into a row's Group cell
    is the only way to make one."""
    (_mission_root(sample_config) / "cfgeventgroups.xml").write_text(EVENTGROUPS_XML)

    rows = client.get("/api/configs/eventgroups").json()["rows"]
    rows.append({
        "id": "new-0",
        "values": {"group": "TruckWreck", "type": "Truck", "lootmin": "", "x": "1"},
    })
    assert client.put("/api/configs/eventgroups", json={"rows": rows}).status_code == 200

    body = client.get("/api/configs/eventgroups").json()
    added = [r["values"] for r in body["rows"] if r["values"]["group"] == "TruckWreck"]
    # Empty loot and height cells add no attribute; x, z and a every child has.
    assert added == [_eventgroups_row("TruckWreck", "Truck", False, "", "", 1.0, "", 0.0, 0.0)]


# Lines of Bohemia's 1.29 cfgeventgroups.xml, read at the pinned commit:
# Chernarus' train children (with and without `y`) and Sakhal's, which carry
# `spawnsecondary` and no loot attributes at all.
def stock_eventgroups() -> str:
    chernarus = ce_lines(bohemia_ce(CE_1_29, "dayzOffline.chernarusplus/cfgeventgroups.xml"))
    sakhal = ce_lines(bohemia_ce(CE_1_29, "dayzOffline.sakhal/cfgeventgroups.xml"))
    train = ce_block(chernarus, 'name="Train_Abandoned_Kamarovo"')
    return "".join(
        chernarus[:2] + train[:3] + train[-1:] + ce_block(sakhal, 'name="Scientist"') + chernarus[-1:]
    )


def _stock_eventgroups(sample_config):
    path = _mission_root(sample_config) / "cfgeventgroups.xml"
    path.write_text(stock_eventgroups())
    return path


def test_eventgroups_untouched_save_keeps_the_file_byte_for_byte(client, sample_config):
    """An untouched save added deloot, lootmin, lootmax and y to every child
    without them and turned 0.7 into 0.700000 (874 children on Chernarus)."""
    path = _stock_eventgroups(sample_config)
    rows = client.get("/api/configs/eventgroups").json()["rows"]
    assert rows[2]["values"] == _eventgroups_row(
        "Scientist", "StaticObj_FrozenScientist_DE", False, "", "", 0.0, "", 0.0, 0.0)

    assert client.put("/api/configs/eventgroups", json={"rows": rows}).status_code == 200

    assert path.read_text() == stock_eventgroups()


def test_eventgroups_writes_only_the_attribute_that_changed(client, sample_config):
    path = _stock_eventgroups(sample_config)
    rows = client.get("/api/configs/eventgroups").json()["rows"]
    rows[1]["values"]["lootmin"] = "2"
    rows[0]["values"]["y"] = ""            # cleared: the attribute goes
    rows[3]["values"]["deloot"] = True     # a child that had none gains it

    assert client.put("/api/configs/eventgroups", json={"rows": rows}).status_code == 200

    text = path.read_text()
    assert 'deloot="0" lootmax="3" lootmin="1" x="0" z="0" a="198.014"/>' in text
    assert 'lootmax="5" lootmin="2" x="47.815" z="73.054" a="232.503" y="2.122"' in text
    assert 'spawnsecondary="false" x="0.7" z="-1.25" a="180" deloot="1"' in text


@pytest.mark.parametrize("key, value, message", [
    ("type", " ", "Type is required"),
    ("lootmin", "1.5", "Loot min must be a whole number of 0 or more"),
    ("lootmax", "-1", "Loot max must be a whole number of 0 or more"),
    ("lootmin", "4", "Loot min (4) is more than Loot max (3)"),
    ("x", "nan", "X must be a finite number"),
    ("a", "abc", "Angle must be a number"),
])
def test_eventgroups_refuses_a_row_it_cannot_write(client, sample_config, key, value, message):
    path = _stock_eventgroups(sample_config)
    rows = client.get("/api/configs/eventgroups").json()["rows"]
    rows[0]["values"][key] = value

    resp = client.put("/api/configs/eventgroups", json={"rows": rows})

    assert resp.status_code == 400
    assert message in resp.json()["detail"]
    assert path.read_text() == stock_eventgroups()


def test_eventgroups_renaming_a_rows_group_moves_the_child(client, sample_config):
    """Same rule as cfgeventspawns: a row whose parent no longer matches its id
    is re-homed, not silently dropped."""
    (_mission_root(sample_config) / "cfgeventgroups.xml").write_text(EVENTGROUPS_XML)

    rows = client.get("/api/configs/eventgroups").json()["rows"]
    # A mod block's row is locked (plans/92), so the mission's own row moves.
    rows[1]["values"]["group"] = "Minefield"   # StaticHeliCrash#1 moves over
    assert client.put("/api/configs/eventgroups", json={"rows": rows}).status_code == 200

    body = client.get("/api/configs/eventgroups").json()
    groups = [r["values"]["group"] for r in body["rows"]]
    assert groups == ["StaticHeliCrash", "BikeWreck", "Minefield"]
    assert [r["values"]["type"] for r in body["rows"]][-1] == "LandMineTrap"


TP_GENERAL_JSON = json.dumps({
    "Version": "2.5",
    "UseGarageToTradeCar": 0,
    "LockPickChance": 0.4,
    "LicenceKeyWord": "Licence",
    "Licences": ["Weapons"],
    "AcceptedStates": {
        "AcceptWorn": 1, "AcceptDamaged": 0, "AcceptBadlyDamaged": 0,
        "CoefficientWorn": 0.75, "CoefficientDamaged": 0.5, "CoefficientBadlyDamaged": 0.25,
    },
    "Currencies": [{"ClassName": "TraderPlusMoney_100", "Value": 100}],
    "Traders": [],
    "TraderObjects": [{"ObjectName": "bench"}],
})

TP_SAFEZONE_JSON = json.dumps({
    "Version": "2.5",
    "IsHideOutActive": 1,
    "KickAfterDelay": 30,
    "MsgEnterZone": "Welcome",
    "SafeAreaLocation": [
        {"SafeZoneStatut": "Green Mountain", "X": 3700.0, "Y": 6000.0, "Radius": 80, "CountDown": 15}
    ],
    "SZSteamUIDs": ["7656119"],
    "CustomUnknownKey": {"keep": True},
})

TP_GARAGE_JSON = json.dumps({
    "Version": "2.5",
    "MaxVehicleStored": 5,
    "ParkInCost": 500,
    "WhitelistedObjects": ["OffroadHatchback"],
    "NPCs": [{"ClassName": "SurvivorM_Peter", "Position": [1, 2, 3]}],
})


def test_tp_general_form_round_trip(client, sample_config):
    d = _make_tp_dir(sample_config)
    (d / "TraderPlusGeneralConfig.json").write_text(TP_GENERAL_JSON)

    body = client.get("/api/configs/tp-general").json()
    assert body["kind"] == "form"
    fields = {f["key"]: f["value"] for s in body["sections"] for f in s["fields"]}
    assert fields["lock_pick_chance"] == 0.4
    assert fields["licences"] == ["Weapons"]
    assert fields["state:coefficient_worn"] == 0.75
    # The price multipliers are a 0-100 % slider (webui sliderScale), as the
    # insurance table's are a percentage.
    shares = {f["key"] for s in body["sections"] for f in s["fields"] if f.get("max") == 1}
    assert {"lock_pick_chance", "state:coefficient_worn", "state:coefficient_damaged",
            "state:coefficient_badly_damaged"} <= shares

    resp = client.put(
        "/api/configs/tp-general",
        json={"values": {"use_garage_to_trade_car": True, "licences": ["Weapons", "Cars"], "state:coefficient_worn": 0.8}},
    )
    assert resp.status_code == 200

    data = json.loads((d / "TraderPlusGeneralConfig.json").read_text())
    assert data["UseGarageToTradeCar"] == 1
    assert data["Licences"] == ["Weapons", "Cars"]
    assert data["AcceptedStates"]["CoefficientWorn"] == 0.8
    # Untouched nested objects preserved.
    assert data["Currencies"][0]["ClassName"] == "TraderPlusMoney_100"
    assert data["TraderObjects"] == [{"ObjectName": "bench"}]


def test_tp_safezone_form_round_trip(client, sample_config):
    d = _make_tp_dir(sample_config)
    (d / "TraderPlusSafeZoneConfig.json").write_text(TP_SAFEZONE_JSON)

    body = client.get("/api/configs/tp-safezone").json()
    assert body["kind"] == "form"
    fields = {f["key"]: f["value"] for s in body["sections"] for f in s["fields"]}
    assert fields["msg_enter_zone"] == "Welcome"
    assert fields["area:0:name"] == "Green Mountain"
    assert fields["area:0:radius"] == 80

    resp = client.put(
        "/api/configs/tp-safezone",
        json={"values": {"kick_after_delay": 60, "area:0:radius": 120, "sz_steam_uids": ["7656119", "7656120"]}},
    )
    assert resp.status_code == 200

    data = json.loads((d / "TraderPlusSafeZoneConfig.json").read_text())
    assert data["KickAfterDelay"] == 60
    assert data["SafeAreaLocation"][0]["Radius"] == 120
    assert data["SZSteamUIDs"] == ["7656119", "7656120"]
    # Unknown keys preserved.
    assert data["CustomUnknownKey"] == {"keep": True}


def test_tp_garage_form_round_trip(client, sample_config):
    d = _make_tp_dir(sample_config)
    (d / "TraderPlusGarageConfig.json").write_text(TP_GARAGE_JSON)

    body = client.get("/api/configs/tp-garage").json()
    assert body["kind"] == "form"
    fields = {f["key"]: f["value"] for s in body["sections"] for f in s["fields"]}
    assert fields["max_vehicle_stored"] == 5
    assert fields["whitelisted_objects"] == ["OffroadHatchback"]

    resp = client.put(
        "/api/configs/tp-garage",
        json={"values": {"park_in_cost": 1000, "save_vehicle_fuel": True}},
    )
    assert resp.status_code == 200

    data = json.loads((d / "TraderPlusGarageConfig.json").read_text())
    assert data["ParkInCost"] == 1000
    assert data["SaveVehicleFuel"] == 1
    # NPC placements untouched.
    assert data["NPCs"][0]["ClassName"] == "SurvivorM_Peter"


def test_tp_money_fields_show_the_cheapest_currency_as_their_unit(client, sample_config):
    """TraderPlus counts an amount in its cheapest currency's value, so the
    garage costs and the bank amounts show that currency's short name as the
    box's unit. Without a general config the box has no unit."""
    d = _make_tp_dir(sample_config)
    (d / "TraderPlusGarageConfig.json").write_text(TP_GARAGE_JSON)
    (d / "TraderPlusBankingConfig.json").write_text(json.dumps({"Version": "2.5", "CurrenciesAccepted": []}))

    def units(editor):
        body = client.get(f"/api/configs/{editor}").json()
        return {f["key"]: f.get("unit") for s in body["sections"] for f in s["fields"]}

    assert units("tp-garage")["park_in_cost"] is None
    (d / "TraderPlusGeneralConfig.json").write_text(json.dumps({"Version": "2.5", "Currencies": [
        {"ClassName": "MoneyRuble100", "Value": 100}, {"ClassName": "MoneyRuble1", "Value": 1},
    ]}))
    garage = units("tp-garage")
    assert garage["park_in_cost"] == garage["park_out_cost"] == "Ruble"
    assert garage["max_vehicle_stored"] is None
    banking = units("tp-banking")
    assert banking["default_start_currency"] == banking["default_max_currency"] == "Ruble"


def test_tp_currency_unit_is_one_short_name():
    """A Currencies entry can list several items in one ClassName, and a long
    name ran over the typed value in the box: the first item names the unit,
    without its count, a suffix after the count, or a TraderPlus prefix."""
    from app.web.editors.traderplus import _currency_name

    assert _currency_name("TraderPlus_Money_Ruble1_Coin,TraderPlus_Money_Euro1,TraderPlus_Money_Dollar1") == "Ruble"
    assert _currency_name("MoneyRuble1") == "Ruble"
    assert _currency_name("TraderPlus_Money_Euro100") == "Euro"
    assert _currency_name("Money") == "Money"
    assert _currency_name("GoldCoin") == "GoldCoin"


def test_tp_garage_npc_count_is_shown_read_only(client, sample_config):
    # The count is derived from NPCs, which the map editor places; the save
    # never reads it, so an editable box would drop whatever was typed.
    d = _make_tp_dir(sample_config)
    (d / "TraderPlusGarageConfig.json").write_text(TP_GARAGE_JSON)
    body = client.get("/api/configs/tp-garage").json()
    field = next(f for s in body["sections"] for f in s["fields"] if f["key"] == "npc_count")
    assert field["readonly"] is True


# TraderPlus lists of names travel as arrays (plans/76 follow-up). Every list
# field and column: (editor id, file, file key, field key, kind).
TP_LISTS = [
    ("tp-banking", "TraderPlusBankingConfig.json", "CurrenciesAccepted", "currencies_accepted", "form"),
    ("tp-general", "TraderPlusGeneralConfig.json", "Licences", "licences", "form"),
    ("tp-safezone", "TraderPlusSafeZoneConfig.json", "SZSteamUIDs", "sz_steam_uids", "form"),
    ("tp-safezone", "TraderPlusSafeZoneConfig.json", "WhitelistEntities", "whitelist_entities", "form"),
    ("tp-garage", "TraderPlusGarageConfig.json", "WhitelistedObjects", "whitelisted_objects", "form"),
    ("tp-ids", "TraderPlusIDsConfig.json", "Categories", "categories", "table"),
    ("tp-vehicles", "TraderPlusVehiclesConfig.json", "VehicleParts", "vehicle_parts", "table"),
]


def _tp_document(file_key: str, value) -> dict:
    """A small TraderPlus file holding *value* in the list under test."""
    if file_key == "Categories":
        return {"Version": "2.5", "IDs": [{"Id": 1, "Categories": value, "LicencesRequired": [], "CurrenciesAccepted": []}]}
    if file_key == "VehicleParts":
        return {"Version": "2.5", "VehiclesParts": [{"VehicleName": "OffroadHatchback", "Height": 3, "VehicleParts": value}]}
    return {"Version": "2.5", file_key: value, "Extra": {"keep": True}}


def _tp_write(d, fname: str, doc: dict) -> str:
    # The layout save_json writes, so an unchanged save can be compared byte for byte.
    text = json.dumps(doc, indent=4, ensure_ascii=False) + "\n"
    (d / fname).write_text(text, encoding="utf-8")
    return text


def _tp_list_value(body: dict, key: str, kind: str):
    if kind == "form":
        field = next(f for s in body["sections"] for f in s["fields"] if f["key"] == key)
        assert field["type"] == "list" and field["item"] == "str"
        # The Edit tab's pick list (FormEditor's MultiSelectList), which the
        # TraderPlus Interactive tab was until 2026 Q4.
        assert field["widget"] == "pick"
        return field["value"]
    column = next(c for c in body["columns"] if c["key"] == key)
    assert column["type"] == "list" and column["item"] == "str"
    return body["rows"][0]["values"][key]


def _tp_put(client, editor, key, kind, value, body):
    if kind == "form":
        return client.put(f"/api/configs/{editor}", json={"values": {key: value}})
    row = body["rows"][0]
    return client.put(f"/api/configs/{editor}", json={"rows": [{"id": row["id"], "values": {**row["values"], key: value}}]})


def _tp_file_list(d, fname: str, file_key: str):
    data = json.loads((d / fname).read_text())
    if file_key == "Categories":
        return data["IDs"][0]["Categories"]
    if file_key == "VehicleParts":
        return data["VehiclesParts"][0]["VehicleParts"]
    return data[file_key]


@pytest.mark.parametrize("editor,fname,file_key,key,kind", TP_LISTS)
def test_tp_list_round_trips_as_an_array_and_keeps_a_comma(client, sample_config, editor, fname, file_key, key, kind):
    d = _make_tp_dir(sample_config)
    _tp_write(d, fname, _tp_document(file_key, ["A, with a comma", "B"]))

    body = client.get(f"/api/configs/{editor}").json()
    assert _tp_list_value(body, key, kind) == ["A, with a comma", "B"]

    resp = _tp_put(client, editor, key, kind, ["A, with a comma", "C,D", "B"], body)
    assert resp.status_code == 200, resp.text
    assert _tp_file_list(d, fname, file_key) == ["A, with a comma", "C,D", "B"]
    body = client.get(f"/api/configs/{editor}").json()
    assert _tp_list_value(body, key, kind) == ["A, with a comma", "C,D", "B"]


@pytest.mark.parametrize("editor,fname,file_key,key,kind", TP_LISTS)
def test_tp_list_unchanged_save_is_byte_identical(client, sample_config, editor, fname, file_key, key, kind):
    d = _make_tp_dir(sample_config)
    # A number where names are expected stays a number when nobody touched it.
    before = _tp_write(d, fname, _tp_document(file_key, ["Coins", "With, comma", 76561198000000000]))
    if kind == "form":
        # A form save writes every key it knows; let the first one add them.
        assert client.put(f"/api/configs/{editor}", json={"values": {}}).status_code == 200
        before = (d / fname).read_text(encoding="utf-8")
        assert _tp_file_list(d, fname, file_key) == ["Coins", "With, comma", 76561198000000000]

    body = client.get(f"/api/configs/{editor}").json()
    value = _tp_list_value(body, key, kind)
    assert _tp_put(client, editor, key, kind, value, body).status_code == 200
    assert (d / fname).read_text(encoding="utf-8") == before
    # The browser's MultiSelectList holds every item as text; that is unchanged too.
    assert _tp_put(client, editor, key, kind, [str(v) for v in value], body).status_code == 200
    assert (d / fname).read_text(encoding="utf-8") == before


@pytest.mark.parametrize("editor,fname,file_key,key,kind", TP_LISTS)
def test_tp_list_still_takes_the_old_comma_string(client, sample_config, editor, fname, file_key, key, kind):
    # A tab opened before the lists became arrays sends the joined string.
    d = _make_tp_dir(sample_config)
    _tp_write(d, fname, _tp_document(file_key, ["A"]))
    body = client.get(f"/api/configs/{editor}").json()

    resp = _tp_put(client, editor, key, kind, " A, B ,,C", body)
    assert resp.status_code == 200, resp.text
    assert _tp_file_list(d, fname, file_key) == ["A", "B", "C"]


@pytest.mark.parametrize("editor,fname,file_key,key,kind", TP_LISTS)
def test_tp_list_refuses_an_item_that_is_not_a_name(client, sample_config, editor, fname, file_key, key, kind):
    d = _make_tp_dir(sample_config)
    before = _tp_write(d, fname, _tp_document(file_key, ["A"]))
    body = client.get(f"/api/configs/{editor}").json()

    resp = _tp_put(client, editor, key, kind, ["A", {"x": 1}], body)
    assert resp.status_code == 400
    assert "item 2 is not a single value" in resp.json()["detail"]
    assert (d / fname).read_text(encoding="utf-8") == before


@pytest.mark.parametrize("editor,fname,file_key,key,kind", TP_LISTS)
def test_tp_text_where_a_list_belongs_is_refused_not_split_into_letters(client, sample_config, editor, fname, file_key, key, kind):
    d = _make_tp_dir(sample_config)
    before = _tp_write(d, fname, _tp_document(file_key, "Coins"))

    resp = client.get(f"/api/configs/{editor}")
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert fname in detail and f'"{file_key}" holds text' in detail and "Raw tab" in detail

    payload = {"values": {key: ["Coins"]}} if kind == "form" else {"rows": [{"id": "x", "values": {key: ["Coins"]}}]}
    assert client.put(f"/api/configs/{editor}", json=payload).status_code == 400
    assert (d / fname).read_text(encoding="utf-8") == before


def test_tp_null_list_opens_as_empty(client, sample_config):
    d = _make_tp_dir(sample_config)
    _tp_write(d, "TraderPlusGarageConfig.json", {"Version": "2.5", "WhitelistedObjects": None})
    body = client.get("/api/configs/tp-garage").json()
    assert _tp_list_value(body, "whitelisted_objects", "form") == []


# ---------------------------------------------------------------------------
# Raw editor tab (GET/PUT /api/configs/{id}/raw, /meta, list-payload flags)
# ---------------------------------------------------------------------------


def test_list_configs_raw_flags(client):
    by_id = {item["id"]: item for item in client.get("/api/configs").json()}
    # Single-file editors advertise a Raw tab + their target basenames.
    assert by_id["serverdz"]["raw"] is True
    assert "serverDZ.cfg" in by_id["serverdz"]["targets"]
    assert by_id["battleye"]["raw"] is True
    assert len(by_id["battleye"]["targets"]) == 2
    # config.json-backed editor: no Raw tab.
    assert by_id["runcommand"]["raw"] is False
    assert by_id["runcommand"]["targets"] == []


def test_config_meta(client):
    meta = client.get("/api/configs/serverdz/meta").json()
    assert meta["id"] == "serverdz"
    assert meta["kind"] == "form"
    assert meta["raw"] is True
    assert "serverDZ.cfg" in meta["targets"]

    assert client.get("/api/configs/does-not-exist/meta").status_code == 404


def test_raw_get_serverdz(client):
    body = client.get("/api/configs/serverdz/raw").json()
    assert body["name"] == "serverDZ.cfg"
    assert body["language"] == "cfg"
    assert "hostname" in body["content"]
    assert body["targets"] == ["serverDZ.cfg"]


def test_raw_round_trip_globals(client, sample_config):
    db = _mission_db(sample_config)
    path = db / "globals.xml"
    path.write_text(GLOBALS_XML)

    body = client.get("/api/configs/globals/raw").json()
    assert body["language"] == "xml"
    assert "CleanupAvoidance" in body["content"]

    new_xml = GLOBALS_XML.replace('value="100"', 'value="250"')
    resp = client.put("/api/configs/globals/raw", json={"content": new_xml})
    assert resp.status_code == 200
    assert 'value="250"' in path.read_text()

    # The structured (Basic) view reflects the raw edit.
    fields = {
        f["key"]: f["value"]
        for s in client.get("/api/configs/globals").json()["sections"]
        for f in s["fields"]
    }
    assert fields["CleanupAvoidance"] == 250


def test_raw_invalid_xml_rejected_leaves_file(client, sample_config):
    db = _mission_db(sample_config)
    path = db / "globals.xml"
    path.write_text(GLOBALS_XML)

    resp = client.put("/api/configs/globals/raw", json={"content": "<globals><var></globals>"})
    assert resp.status_code == 400
    assert path.read_text() == GLOBALS_XML  # unchanged
    # The detail names the line and says what's wrong, not just expat's string —
    # the editor renders it as a persistent strip under the box.
    detail = resp.json()["detail"]
    assert detail.startswith("Line 1, column ")
    assert "closing tag doesn't match opening tag" in detail


def test_raw_invalid_json_rejected_leaves_file(client, sample_config):
    root = _mission_root(sample_config)
    path = root / "cfgEffectArea.json"
    path.write_text(EFFECTAREA_JSON)

    resp = client.put("/api/configs/effectarea/raw", json={"content": "{not valid json"})
    assert resp.status_code == 400
    assert path.read_text() == EFFECTAREA_JSON  # unchanged


def test_raw_unknown_target_400(client):
    assert client.get("/api/configs/serverdz/raw", params={"target": "evil"}).status_code == 400
    resp = client.put("/api/configs/serverdz/raw", json={"target": "../etc/passwd", "content": "x"})
    assert resp.status_code == 400


def test_raw_no_raw_editor_404(client):
    assert client.get("/api/configs/runcommand/raw").status_code == 404
    assert client.put("/api/configs/runcommand/raw", json={"content": "x"}).status_code == 404


def test_raw_battleye_target_pick(client):
    body = client.get("/api/configs/battleye/raw").json()
    assert len(body["targets"]) == 2
    second = body["targets"][1]
    picked = client.get("/api/configs/battleye/raw", params={"target": second}).json()
    assert picked["name"] == second


def test_raw_save_snapshots_existing_file(client, sample_config):
    db = _mission_db(sample_config)
    path = db / "globals.xml"
    path.write_text(GLOBALS_XML)

    from app.shared import config_versions

    before = len(config_versions.load_index(sample_config.paths.versions_dir).get_versions("globals.xml"))
    resp = client.put("/api/configs/globals/raw", json={"content": GLOBALS_XML.replace("100", "111")})
    assert resp.status_code == 200
    after = len(config_versions.load_index(sample_config.paths.versions_dir).get_versions("globals.xml"))
    assert after == before + 1


# ---------------------------------------------------------------------------
# Interactive views (Phase 2) — ignorelist common-items pool
# ---------------------------------------------------------------------------


def test_ignorelist_common_items(client):
    body = client.get("/api/configs/ignorelist/common-items").json()
    assert "FenceKit" in body["items"]
    assert len(body["items"]) > 5


def test_weather_presets_list(client):
    body = client.get("/api/configs/weather/presets").json()
    names = {p["name"] for p in body["presets"]}
    assert "Clear Skies" in names
    assert all("description" in p for p in body["presets"])


def test_a_weather_label_unit_moves_into_the_box(client):
    """"Change time (s)" showed no unit in its box: the "(s)" is now the box's
    "seconds", and a range such as "(0-1)" stays in the label."""
    assert client.post("/api/configs/weather/apply-preset", json={"name": "Overcast"}).status_code == 200
    fields = {
        f["key"]: f
        for s in client.get("/api/configs/weather").json()["sections"]
        for f in s["fields"]
    }
    assert fields["overcast.time"]["label"] == "Change time"
    assert fields["overcast.time"]["unit"] == "seconds"
    assert fields["overcast.actual"]["label"] == "Initial value (0-1)"
    assert "unit" not in fields["overcast.actual"]


def test_unit_from_label():
    from app.web.editors._help import unit_from_label

    assert unit_from_label("Stop time (s)") == ("Stop time", {"unit": "seconds"})
    assert unit_from_label("Speed min (m/s)") == ("Speed min", {"unit": "m/s"})
    assert unit_from_label("Initial speed (0-20 m/s)") == ("Initial speed (0-20 m/s)", {})
    assert unit_from_label("Overcast threshold min") == ("Overcast threshold min", {})


def test_weather_apply_preset(client, sample_config):
    from defusedxml.ElementTree import fromstring

    root = _mission_root(sample_config)
    resp = client.post("/api/configs/weather/apply-preset", json={"name": "Overcast"})
    assert resp.status_code == 200

    path = root / "cfgweather.xml"
    assert path.is_file()
    fromstring(path.read_text())  # valid XML, no raise

    # Basic view now reflects the rebuilt file.
    fields = {
        f["key"]: f["value"]
        for s in client.get("/api/configs/weather").json()["sections"]
        for f in s["fields"]
    }
    assert "enabled" in fields

    assert client.post("/api/configs/weather/apply-preset", json={"name": "Nope"}).status_code == 400


PLAYER_SPAWNPOINTS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<playerspawnpoints>
    <fresh>
        <generator_posbubbles>
            <pos x="7500.0" z="7500.0"/>
        </generator_posbubbles>
    </fresh>
</playerspawnpoints>
"""


def test_playerspawnpoints_raw_round_trip(client, sample_config):
    """The Players tab's file is editable from /configs: a table since plans/76
    step 3 (tests/test_web_editors_playerspawnpoints.py), the whole file on the
    Raw tab."""
    root = _mission_root(sample_config)
    (root / "cfgPlayerSpawnPoints.xml").write_text(PLAYER_SPAWNPOINTS_XML)

    meta = client.get("/api/configs/playerspawnpoints/meta").json()
    assert meta["kind"] == "table"
    assert meta["raw"] is True

    body = client.get("/api/configs/playerspawnpoints/raw").json()
    assert body["language"] == "xml"
    assert 'x="7500.0"' in body["content"]

    updated = body["content"].replace('x="7500.0"', 'x="1234.0"')
    assert client.put("/api/configs/playerspawnpoints/raw", json={"content": updated}).status_code == 200
    assert 'x="1234.0"' in (root / "cfgPlayerSpawnPoints.xml").read_text()

    # Malformed XML is rejected before it reaches the file.
    assert client.put("/api/configs/playerspawnpoints/raw", json={"content": "<oops>"}).status_code == 400
    assert 'x="1234.0"' in (root / "cfgPlayerSpawnPoints.xml").read_text()


def test_playerspawnpoints_found_in_db_subdir(client, sample_config):
    """The file legitimately lives in either the mission root or db/."""
    db = _mission_db(sample_config)
    (db / "cfgplayerspawnpoints.xml").write_text(PLAYER_SPAWNPOINTS_XML)

    body = client.get("/api/configs/playerspawnpoints").json()
    assert body["rows"][0]["nested"]["pos"] == [{"x": 7500.0, "z": 7500.0}]


# ---------------------------------------------------------------------------
# cfgeconomycore: the `type=` column is an enum of DayZ's six, and a folder the
# mission hasn't got is a warning (the engine silently loads nothing from it).
# ---------------------------------------------------------------------------

SIX_CE_TYPES = ["economy", "globals", "types", "spawnabletypes", "events", "messages"]


def _write_economycore(sample_config, body: str):
    mission_root = sample_config.server_dir_path / "mpmissions" / "dayzOffline.chernarusplus"
    mission_root.mkdir(parents=True, exist_ok=True)
    path = mission_root / "cfgeconomycore.xml"
    path.write_text(f'<?xml version="1.0" encoding="UTF-8"?>\n<economycore>\n{body}\n</economycore>\n')
    return mission_root, path


def test_economycore_rows_offer_the_six_file_types(client, sample_config):
    mission_root, _ = _write_economycore(
        sample_config,
        '\t<defaults>\n\t\t<default name="test" value="1"/>\n\t</defaults>\n'
        '\t<ce folder="db">\n\t\t<file name="types.xml" type="types"/>\n\t</ce>',
    )
    (mission_root / "db").mkdir(exist_ok=True)
    (mission_root / "db" / "types.xml").write_text("<types/>")

    body = client.get("/api/configs/economycore").json()

    ce_row = next(r for r in body["rows"] if r["values"]["section"] == "ce:db")
    assert ce_row["choices"]["value"] == SIX_CE_TYPES
    # The column is shared with `defaults`, whose value is a number — so the
    # enum has to be per row, not per column.
    default_row = next(r for r in body["rows"] if r["values"]["section"] == "defaults")
    assert "choices" not in default_row
    assert body["warnings"] == []


def test_economycore_rejects_an_unknown_file_type(client, sample_config):
    """DayZ refuses to load the *whole* economy on a bad type=, and a stale tab
    or a direct API call can post anything the <select> wouldn't offer."""
    _, path = _write_economycore(
        sample_config, '\t<ce folder="db">\n\t\t<file name="types.xml" type="types"/>\n\t</ce>',
    )
    before = path.read_text()

    rows = [{"id": "ce:db:types.xml", "values": {"section": "ce:db", "name": "types.xml", "value": "loot"}}]
    resp = client.put("/api/configs/economycore", json={"rows": rows})

    assert resp.status_code == 400
    assert "loot" in resp.json()["detail"]
    assert path.read_text() == before


def test_economycore_warns_about_a_missing_folder_or_file(client, sample_config):
    mission_root, _ = _write_economycore(
        sample_config,
        '\t<ce folder="db">\n\t\t<file name="gone.xml" type="types"/>\n\t</ce>\n'
        '\t<ce folder="nope">\n\t\t<file name="x.xml" type="types"/>\n\t</ce>',
    )
    (mission_root / "db").mkdir(exist_ok=True)

    warnings = client.get("/api/configs/economycore").json()["warnings"]

    assert any("no nope/ folder" in w for w in warnings)
    # A folder of the admin's own also names the supported way.
    assert any("no nope/ folder" in w and "Manual XML files" in w for w in warnings)
    assert any("db/gone.xml is registered but not present" in w for w in warnings)


def test_economycore_does_not_warn_about_an_empty_custom_folder(client, sample_config):
    """custom/ is legitimately empty until the first merge runs."""
    _write_economycore(
        sample_config,
        '\t<ce folder="custom">\n\t\t<file name="bikes-types.xml" type="types"/>\n\t</ce>',
    )

    assert client.get("/api/configs/economycore").json()["warnings"] == [
        '<ce folder="custom"> — the mission has no custom/ folder, '
        "so DayZ loads nothing from it."
    ]


def test_the_log_intervals_are_seconds_not_switches(client, sample_config):
    """Test pass 1, F52: logAverageFps and its siblings are intervals in
    seconds (dayz-docs server-configuration.wiki:79-81), but the form showed
    a checkbox, which can only write 0 or 1."""
    cfg = sample_config.server_cfg_path
    cfg.write_text(cfg.read_text() + "logAverageFps = 30;\n")
    data = client.get("/api/configs/serverdz").json()
    fields = {f["key"]: f for s in data["sections"] for f in s["fields"]}
    for key in ("logAverageFps", "logMemory", "logPlayers"):
        assert fields[key]["type"] == "int", key
    assert fields["logAverageFps"]["value"] == 30


def test_coresettings_save_keeps_the_file_s_comments(client, sample_config):
    path = sample_config.server_dir_path / "dayzsetting.xml"
    path.write_text(DAYZSETTING_XML.replace("<settings>", "<settings>\n    <!-- raised for the weekend event -->", 1))
    assert "<!-- raised for the weekend event -->" in path.read_text()

    resp = client.put("/api/configs/coresettings", json={"values": {"pc--maxcores": "8"}})
    assert resp.status_code == 200, resp.text

    content = path.read_text()
    assert 'maxcores="8"' in content
    assert "<!-- raised for the weekend event -->" in content


# A row added in the table carries the table's own id ("new-<n>-<time>"),
# and its name is whatever the Name cell holds.
NEW_ROW_ID = "new-1-1759786000000"


def test_events_table_saves_an_added_row_under_its_typed_name(client, sample_config):
    db = _mission_db(sample_config)
    (db / "events.xml").write_text(EVENTS_XML)

    resp = client.put(
        "/api/configs/events",
        json={"rows": [
            {"id": "StaticWreck", "values": {"name": "StaticWreck", "nominal": 5, "active": True}},
            {"id": NEW_ROW_ID, "values": {"name": "StaticMyEvent", "nominal": 3, "min": "", "active": True}},
            # Added and left empty: not an event.
            {"id": "new-2-1759786000001", "values": {"name": "", "nominal": "", "active": False}},
        ]},
    )
    assert resp.status_code == 200, resp.text

    content = (db / "events.xml").read_text()
    assert '<event name="StaticMyEvent">' in content
    assert "new-" not in content
    assert 'name=""' not in content
    names = [r["values"]["name"] for r in client.get("/api/configs/events").json()["rows"]]
    assert names == ["StaticWreck", "StaticMyEvent"]


def test_events_table_keeps_a_number_when_the_cell_is_not_one(client, sample_config):
    db = _mission_db(sample_config)
    (db / "events.xml").write_text(EVENTS_XML)
    before = client.get("/api/configs/events").json()["rows"][0]["values"]

    resp = client.put(
        "/api/configs/events",
        json={"rows": [{"id": "StaticWreck", "values": {"name": "StaticWreck", "nominal": "1o", "lifetime": ""}}]},
    )
    assert resp.status_code == 200, resp.text

    after = client.get("/api/configs/events").json()["rows"][0]["values"]
    assert (after["nominal"], after["lifetime"]) == (before["nominal"], before["lifetime"])


def test_economy_table_saves_an_added_row_under_its_typed_name(client, sample_config):
    db = _mission_db(sample_config)
    (db / "economy.xml").write_text(
        '<economy>\n    <dynamic init="1" load="1" respawn="1" save="1"/>\n</economy>\n'
    )

    resp = client.put(
        "/api/configs/economy",
        json={"rows": [
            {"id": "dynamic", "values": {"name": "dynamic", "init": True, "load": True, "respawn": True, "save": True}},
            {"id": NEW_ROW_ID, "values": {"name": "randoms", "init": True, "load": False, "respawn": True, "save": False}},
        ]},
    )
    assert resp.status_code == 200, resp.text

    content = (db / "economy.xml").read_text()
    assert "<randoms " in content
    assert "new-" not in content


TWO_EVENTS_XML = EVENTS_XML.replace(
    "</events>",
    '    <event name="StaticTrain"><nominal>2</nominal><min>1</min><max>2</max>'
    "<lifetime>60</lifetime><restock>0</restock><active>1</active></event>\n</events>",
)


def _row(name, **values):
    return {"id": name, "values": {"name": name, **values}}


def test_events_table_removes_a_row_removed_in_the_table(client, sample_config):
    db = _mission_db(sample_config)
    (db / "events.xml").write_text(TWO_EVENTS_XML)

    resp = client.put("/api/configs/events", json={"rows": [_row("StaticWreck", nominal=5)]})
    assert resp.status_code == 200, resp.text

    content = (db / "events.xml").read_text()
    assert 'name="StaticTrain"' not in content
    assert [r["id"] for r in client.get("/api/configs/events").json()["rows"]] == ["StaticWreck"]


def test_events_table_renames_an_event(client, sample_config):
    db = _mission_db(sample_config)
    (db / "events.xml").write_text(TWO_EVENTS_XML)

    rows = [
        {"id": "StaticWreck", "values": {"name": "StaticWreckRenamed", "nominal": 5}},
        _row("StaticTrain"),
    ]
    resp = client.put("/api/configs/events", json={"rows": rows})
    assert resp.status_code == 200, resp.text

    content = (db / "events.xml").read_text()
    assert 'name="StaticWreckRenamed"' in content
    assert 'name="StaticWreck"' not in content
    assert "<cleanupradius>1000</cleanupradius>" in content  # the same event, renamed


def test_events_table_refuses_a_rename_onto_another_event(client, sample_config):
    db = _mission_db(sample_config)
    (db / "events.xml").write_text(TWO_EVENTS_XML)
    before = (db / "events.xml").read_text()

    rows = [{"id": "StaticWreck", "values": {"name": "StaticTrain"}}, _row("StaticTrain")]
    resp = client.put("/api/configs/events", json={"rows": rows})
    assert resp.status_code == 400
    assert "StaticTrain" in resp.json()["detail"]
    assert (db / "events.xml").read_text() == before


def test_events_table_rename_takes_the_events_positions_along(client, sample_config):
    """The positions stayed in cfgeventspawns.xml under
    the old name, so the renamed event spawned nowhere, and the save said
    "Saved."."""
    from app.shared.config_versions import load_index, store_for

    db = _mission_db(sample_config)
    (db / "events.xml").write_text(TWO_EVENTS_XML.replace(
        "<restock>0</restock><active>", "<restock>0</restock><secondary>StaticWreck</secondary><active>",
    ))
    spawns = _mission_root(sample_config) / "cfgeventspawns.xml"
    spawns.write_text(EVENTSPAWNS_XML.replace("StaticHeliCrash", "StaticWreck"))

    rows = [{"id": "StaticWreck", "values": {"name": "StaticWreckX"}}, _row("StaticTrain")]
    resp = client.put("/api/configs/events", json={"rows": rows})
    assert resp.status_code == 200, resp.text

    text = spawns.read_text()
    assert '<event name="StaticWreckX">' in text and 'name="StaticWreck"' not in text
    assert 'name="BikeSpawn"' in text  # the mod's block is untouched
    assert load_index(store_for(spawns)).get_versions(spawns.name)  # the file as it was
    assert "<secondary>StaticWreckX</secondary>" in (db / "events.xml").read_text()


def test_events_table_refuses_a_rename_that_leaves_a_mods_positions_behind(client, sample_config):
    db = _mission_db(sample_config)
    (db / "events.xml").write_text(TWO_EVENTS_XML)
    spawns = _mission_root(sample_config) / "cfgeventspawns.xml"
    spawns.write_text(EVENTSPAWNS_XML.replace("BikeSpawn", "StaticWreck"))
    before = (db / "events.xml").read_text(), spawns.read_text()

    rows = [{"id": "StaticWreck", "values": {"name": "StaticWreckX"}}, _row("StaticTrain")]
    resp = client.put("/api/configs/events", json={"rows": rows})
    assert resp.status_code == 400
    assert "@Bikes" in resp.json()["detail"] and resp.json()["go"] == "mod-xml"
    assert ((db / "events.xml").read_text(), spawns.read_text()) == before


def test_events_table_rename_onto_a_name_with_its_own_positions_is_refused(client, sample_config):
    db = _mission_db(sample_config)
    spawns = _mission_root(sample_config) / "cfgeventspawns.xml"
    rows = [{"id": "StaticWreck", "values": {"name": "StaticHeliCrash"}}, _row("StaticTrain")]
    (db / "events.xml").write_text(TWO_EVENTS_XML)
    spawns.write_text(EVENTSPAWNS_XML.replace("    <!-- START MOD", (
        '    <event name="StaticWreck">\n        <pos x="10.000000" z="20.000000"/>\n'
        "    </event>\n    <!-- START MOD"
    )))
    before = spawns.read_text()
    resp = client.put("/api/configs/events", json={"rows": rows})
    assert resp.status_code == 400 and resp.json()["go"] == "eventspawns"
    assert spawns.read_text() == before
    assert 'name="StaticWreck"' in (db / "events.xml").read_text()


def test_economy_table_removes_and_renames_systems(client, sample_config):
    db = _mission_db(sample_config)
    (db / "economy.xml").write_text(ECONOMY_XML)
    flags = {"init": True, "load": True, "respawn": False, "save": True}

    rows = [{"id": "animals", "values": {"name": "zombies", **flags}}]
    resp = client.put("/api/configs/economy", json={"rows": rows})
    assert resp.status_code == 200, resp.text

    content = (db / "economy.xml").read_text()
    assert "<dynamic " not in content
    assert '<zombies init="1" load="1" respawn="0" save="1"' in content.replace(" />", "/>")
    assert "<animals" not in content


def test_economy_table_refuses_a_rename_that_is_not_a_system_name(client, sample_config):
    db = _mission_db(sample_config)
    (db / "economy.xml").write_text(ECONOMY_XML)
    before = (db / "economy.xml").read_text()

    rows = [{"id": "dynamic", "values": {"name": "two words"}}, _row("animals")]
    resp = client.put("/api/configs/economy", json={"rows": rows})
    assert resp.status_code == 400
    assert (db / "economy.xml").read_text() == before


@pytest.mark.parametrize("name", ["beasts", "Dynamic"])
def test_economy_table_refuses_a_system_the_engine_does_not_read(client, sample_config, name):
    """economy.xml's systems are the eight of Bohemia's file; another element
    is read by nothing, so a row naming one is a 400 and nothing is written."""
    db = _mission_db(sample_config)
    (db / "economy.xml").write_text(ECONOMY_XML)
    before = (db / "economy.xml").read_text()

    rows = [_row("dynamic"), _row("animals"), {"id": NEW_ROW_ID, "values": {"name": name}}]
    resp = client.put("/api/configs/economy", json={"rows": rows})
    assert resp.status_code == 400
    assert "randoms" in resp.json()["detail"]
    assert (db / "economy.xml").read_text() == before


def test_economy_table_offers_the_engine_systems(tmp_path):
    from app.web.editors.ce_tables import economy_load

    # Bohemia's whole db/economy.xml (Chernarus), read at the pinned 1.29 commit.
    stock = bohemia_ce(CE_1_29, "dayzOffline.chernarusplus/db/economy.xml")
    path = tmp_path / "economy.xml"
    path.write_text(stock)
    payload = economy_load(path)
    names = [r["id"] for r in payload["rows"]]
    assert payload["columns"][0]["choices"] == names


def test_fields_the_container_env_sets_are_locked_in_the_server_forms(client, sample_config, server_stopped):
    from app.parsers.servercfg import read_cfg_value
    from app.shared.container import record_env_fields

    record_env_fields(sample_config, {
        "server_name": "SERVER_NAME", "rcon_port": "RCON_PORT",
        "profiles_path": "PROFILES_PATH", "server_args": "SERVER_ARGS",
    })

    def fields(editor):
        return {f["key"]: f for s in client.get(f"/api/configs/{editor}").json()["sections"] for f in s["fields"]}

    serverdz = fields("serverdz")
    assert serverdz["hostname"]["locked"] and "SERVER_NAME" in serverdz["hostname"]["locked_note"]
    assert not serverdz["maxPlayers"].get("locked")
    resp = client.put("/api/configs/serverdz", json={"values": {"hostname": "Another"}})
    assert resp.status_code == 400 and "SERVER_NAME" in resp.json()["detail"]
    assert client.put("/api/configs/serverdz", json={"values": {"maxPlayers": 40}}).status_code == 200
    assert read_cfg_value(sample_config.server_cfg_path, "maxPlayers") == "40"

    battleye = fields("battleye")
    assert battleye["RConPort"]["locked"] and not battleye["RConPassword"].get("locked")
    resp = client.put("/api/configs/battleye", json={"values": {"RConPort": 2399}})
    assert resp.status_code == 400 and "RCON_PORT" in resp.json()["detail"]
    assert client.put("/api/configs/battleye", json={"values": {"RConPassword": "pw2"}}).status_code == 200

    runcommand = fields("runcommand")
    assert runcommand["profiles_path"]["locked"] and runcommand["adminLog"]["locked"]
    assert not runcommand["server_port"].get("locked")
    for values in ({"profiles_path": "other"}, {"adminLog": not runcommand["adminLog"]["value"]}):
        resp = client.put("/api/configs/runcommand", json={"values": values})
        assert resp.status_code == 400 and "recreate the container" in resp.json()["detail"], values
    assert client.put("/api/configs/runcommand", json={"values": {"server_port": 2410}}).status_code == 200


def test_serverdz_reads_and_saves_the_messages_of_the_day(client, sample_config):
    from app.parsers.servercfg import read_cfg_list

    cfg = sample_config.server_cfg_path
    cfg.write_text('hostname = "X";\nmotd[] = {\n  "One",\n  "Two"\n};\n')
    fields = {f["key"]: f for s in client.get("/api/configs/serverdz").json()["sections"] for f in s["fields"]}
    assert fields["motd"]["type"] == "list" and fields["motd"]["value"] == ["One", "Two"]
    resp = client.put("/api/configs/serverdz", json={"values": {"motd": ["Hello", "  ", "Bye"]}})
    assert resp.status_code == 200, resp.text
    assert read_cfg_list(cfg, "motd") == ["Hello", "Bye"]


def test_serverdz_refuses_a_message_of_the_day_with_a_quote(client, sample_config):
    cfg = sample_config.server_cfg_path
    before = cfg.read_text()
    resp = client.put("/api/configs/serverdz", json={"values": {"motd": ['a "b"'], "maxPlayers": 5}})
    assert resp.status_code == 400
    assert cfg.read_text() == before


def test_serverdz_third_person_view_replaces_the_old_key(client, sample_config):
    """A save of thirdPersonView takes disable3rdPerson out of the file, also
    when the same body sends both (servercfg.REPLACED_CFG_KEYS). The form shows
    the old switch, with its note, only while the file lacks the new key."""
    path = sample_config.server_cfg_path
    path.write_text(path.read_text() + "disable3rdPerson = 1;\n")

    def fields():
        return {f["key"]: f for s in client.get("/api/configs/serverdz").json()["sections"] for f in s["fields"]}

    old = fields()["disable3rdPerson"]
    assert old["replaced_in"] == "1.30"
    assert old["note"] == "For a server before DayZ 1.30. A save of 3rd person view (1.30+) removes this key."
    resp = client.put("/api/configs/serverdz", json={"values": {"thirdPersonView": "2", "disable3rdPerson": False}})
    assert resp.status_code == 200
    text = path.read_text()
    assert "thirdPersonView = 2;" in text and "disable3rdPerson" not in text
    assert "disable3rdPerson" not in fields()


@pytest.mark.parametrize("key", ["disablePersonalLight", "enableDebugMonitor", "adminLogPlayerHitsOnly",
                                 "adminLogPlacement", "adminLogBuildActions", "adminLogPlayerList"])
def test_serverdz_a_key_the_scripts_read_defaults_to_0(client, key):
    """The game reads these through ServerConfigGetInt, which "Returns 0 if
    not found" (DayZ-Script-Diff 1.30, scripts/3_game/global/game.c:1138). The
    wiki's example file sets disablePersonalLight and enableDebugMonitor to 1,
    which is not what a server without the key does."""
    fields = {f["key"]: f for s in client.get("/api/configs/serverdz").json()["sections"] for f in s["fields"]}
    assert fields[key]["default"] is False


def test_tp_beans_form_edits_the_badge_and_keeps_the_info_text(client, sample_config):
    """TraderPlusBeansLauncherConfig.json came with the 2026-11-09 defaults. The
    form edits the partner code and the hide switch, never the author's Info."""
    cfg_dir = sample_config.server_dir_path / sample_config.profiles_path / "TraderPlus" / "TraderPlusConfig"
    cfg_dir.mkdir(parents=True)
    path = cfg_dir / "TraderPlusBeansLauncherConfig.json"
    text = '{\n    "Info": "Invented help text.",\n    "PartnerCode": "ABC123",\n    "HideBadge": 0\n}\n'
    path.write_text(text)

    body = client.get("/api/configs/tp-beans").json()
    fields = {f["key"]: f for s in body["sections"] for f in s["fields"]}
    assert set(fields) == {"PartnerCode", "HideBadge"}
    assert fields["HideBadge"]["type"] == "bool"

    resp = client.put("/api/configs/tp-beans", json={"values": {"HideBadge": True, "Info": "x"}})
    assert resp.status_code == 200, resp.text
    assert path.read_text() == text.replace('"HideBadge": 0', '"HideBadge": 1')
