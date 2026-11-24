"""Tests for cli.servercfg — serverDZ.cfg reader/writer."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from app.parsers.servercfg import (
    CfgValueError,
    get_available_missions,
    is_storage_folder,
    read_cfg_value,
    read_cfg_values,
    read_mission_template,
    remove_cfg_key,
    set_mission_template,
    storage_folder_name,
    write_cfg_value,
)


SAMPLE_CFG = textwrap.dedent("""\
    hostname = "Test Server";
    passwordAdmin = "secret";
    maxPlayers = 60;
    verifySignatures = 2;
    // disableVoN = 1;
    class Missions
    {
        class DayZ
        {
            template = "dayzOffline.chernarusplus";
        };
    };
""")


# ---------------------------------------------------------------------------
# read_cfg_value / read_cfg_values
# ---------------------------------------------------------------------------


class TestReadCfg:
    def test_read_single_value(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(SAMPLE_CFG)
        assert read_cfg_value(cfg, "hostname") == "Test Server"

    def test_read_numeric_value(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(SAMPLE_CFG)
        assert read_cfg_value(cfg, "maxPlayers") == "60"

    def test_read_missing_key_returns_none(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(SAMPLE_CFG)
        assert read_cfg_value(cfg, "nonExistentKey") is None

    def test_read_multiple_values(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(SAMPLE_CFG)
        result = read_cfg_values(cfg, ["hostname", "maxPlayers", "missing"])
        assert result["hostname"] == "Test Server"
        assert result["maxPlayers"] == "60"
        assert result["missing"] is None

    def test_read_from_nonexistent_file(self, tmp_path: Path):
        cfg = tmp_path / "nope.cfg"
        assert read_cfg_value(cfg, "hostname") is None

    def test_commented_lines_ignored(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(SAMPLE_CFG)
        assert read_cfg_value(cfg, "disableVoN") is None

    def test_keys_inside_class_block_not_read_at_root(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(SAMPLE_CFG)
        # 'template' is inside class Missions, not root level
        assert read_cfg_value(cfg, "template") is None


# ---------------------------------------------------------------------------
# write_cfg_value
# ---------------------------------------------------------------------------


class TestWriteCfg:
    def test_write_replaces_existing(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(SAMPLE_CFG)
        write_cfg_value(cfg, "maxPlayers", "80")
        assert read_cfg_value(cfg, "maxPlayers") == "80"

    def test_write_quoted_value(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(SAMPLE_CFG)
        write_cfg_value(cfg, "hostname", "New Name", quoted=True)
        assert read_cfg_value(cfg, "hostname") == "New Name"

    def test_write_appends_new_key_before_class_missions(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(SAMPLE_CFG)
        write_cfg_value(cfg, "vonCodecQuality", "20")
        assert read_cfg_value(cfg, "vonCodecQuality") == "20"
        # Verify the key appears before class Missions
        text = cfg.read_text(encoding="utf-8")
        von_pos = text.index("vonCodecQuality")
        missions_pos = text.index("class Missions")
        assert von_pos < missions_pos

    def test_write_does_nothing_when_file_missing(self, tmp_path: Path):
        cfg = tmp_path / "nope.cfg"
        write_cfg_value(cfg, "hostname", "whatever")
        assert not cfg.exists()

    def test_write_preserves_other_keys(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(SAMPLE_CFG)
        write_cfg_value(cfg, "maxPlayers", "100")
        assert read_cfg_value(cfg, "hostname") == "Test Server"
        assert read_cfg_value(cfg, "passwordAdmin") == "secret"

    @pytest.mark.parametrize("sep", ["\n", "\r", "\x0b", "\x0c", "\x1c", "\x85", "\u2028", "\u2029"])
    @pytest.mark.parametrize("quoted", [False, True])
    def test_a_value_that_breaks_its_line_is_refused(self, tmp_path: Path, sep: str, quoted: bool):
        """Every writer of a root key comes through here, so one refusal covers
        the Server form, the container sync and the map switch (plans/68 LB-4)."""
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(SAMPLE_CFG)
        with pytest.raises(CfgValueError, match="^password contains "):
            write_cfg_value(cfg, "password", f"join{sep}verifySignatures = 0", quoted=quoted)
        assert cfg.read_text() == SAMPLE_CFG

    def test_a_quoted_value_holding_a_quote_is_refused(self, tmp_path: Path):
        """It would end the string early (plans/68 LB-6)."""
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(SAMPLE_CFG)
        with pytest.raises(CfgValueError, match="^hostname contains a double quote"):
            write_cfg_value(cfg, "hostname", 'X"; verifySignatures = 0; x = "X', quoted=True)
        assert cfg.read_text() == SAMPLE_CFG

    def test_a_tab_is_still_written(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(SAMPLE_CFG)
        write_cfg_value(cfg, "hostname", "My\tServer", quoted=True)
        assert 'hostname = "My\tServer";' in cfg.read_text()


# ---------------------------------------------------------------------------
# A quoted value is one token (plans/68 A-3): a `;` inside it is part of the
# value, and nothing inside it is a key or a brace.
# ---------------------------------------------------------------------------


class TestQuotedValueIsOneToken:
    CFG = textwrap.dedent("""\
        hostname = "[EU] Frosty; PvE";
        description = "No password = none; {have fun";
        password = "";
        maxPlayers = 60;
        class Missions
        {
            class DayZ
            {
                template = "dayzOffline.chernarusplus";
            };
        };
    """)

    def test_a_semicolon_inside_quotes_is_read(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(self.CFG)
        assert read_cfg_value(cfg, "hostname") == "[EU] Frosty; PvE"
        assert read_cfg_value(cfg, "description") == "No password = none; {have fun"

    def test_a_quoted_value_is_replaced_whole(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(self.CFG)
        write_cfg_value(cfg, "hostname", "A; B", quoted=True)
        assert cfg.read_text() == self.CFG.replace('"[EU] Frosty; PvE"', '"A; B"')
        assert read_cfg_value(cfg, "hostname") == "A; B"

    def test_a_key_named_inside_a_value_is_not_that_key(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(self.CFG)
        write_cfg_value(cfg, "password", "join", quoted=True)
        assert cfg.read_text() == self.CFG.replace('password = "";', 'password = "join";')
        remove_cfg_key(cfg, "password")
        assert cfg.read_text() == self.CFG.replace('password = "";\n', "")

    def test_a_brace_inside_quotes_hides_no_later_key(self, tmp_path: Path):
        """The description opens a `{` it never closes; the keys after it are
        still at the root."""
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(self.CFG)
        assert read_cfg_value(cfg, "maxPlayers") == "60"
        write_cfg_value(cfg, "maxPlayers", "40")
        assert cfg.read_text() == self.CFG.replace("maxPlayers = 60;", "maxPlayers = 40;")

    def test_a_doubled_quote_is_one_quote(self, tmp_path: Path):
        """check_server_cfg's grammar: a string escapes a quote by doubling it."""
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text('description = "Say ""hi""; then join";\nmaxPlayers = 60;\n')
        assert read_cfg_value(cfg, "description") == 'Say "hi"; then join'
        write_cfg_value(cfg, "description", "Hello", quoted=True)
        assert cfg.read_text() == 'description = "Hello";\nmaxPlayers = 60;\n'

    def test_a_bare_value_still_ends_at_the_semicolon(self, tmp_path: Path):
        """What an older zsm wrote unquoted reads back the same, as long as it
        holds no `;`."""
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text("hostname = My Server;\nlogFile = server_console.log; // bare\n")
        assert read_cfg_value(cfg, "hostname") == "My Server"
        assert read_cfg_value(cfg, "logFile") == "server_console.log"
        cfg.write_text("hostname = My Server; verifySignatures = 0;\n")
        assert read_cfg_value(cfg, "hostname") == "My Server"

    @pytest.mark.parametrize("value", ["60; verifySignatures = 0", '60"'])
    def test_a_bare_value_holding_a_semicolon_or_quote_is_refused(self, tmp_path: Path, value: str):
        """It would end the value early, and the rest would be a key of its own."""
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(SAMPLE_CFG)
        with pytest.raises(CfgValueError, match="^maxPlayers contains a (semicolon|double quote)"):
            write_cfg_value(cfg, "maxPlayers", value)
        assert cfg.read_text() == SAMPLE_CFG

    def test_a_comment_after_the_value_is_kept(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text('hostname = "Old";\t\t// was "password = x;"\n')
        write_cfg_value(cfg, "hostname", "New", quoted=True)
        assert cfg.read_text() == 'hostname = "New";\t\t// was "password = x;"\n'
        write_cfg_value(cfg, "password", "pw", quoted=True)
        assert cfg.read_text() == 'hostname = "New";\t\t// was "password = x;"\npassword = "pw";\n'


# ---------------------------------------------------------------------------
# Key-collision regression: a short key must not match a longer one's suffix
# ("password" inside "adminPassword" under IGNORECASE + unanchored search).
# ---------------------------------------------------------------------------


class TestKeySuffixCollision:
    CFG = textwrap.dedent("""\
        hostname = "X";
        adminPassword = "ADMIN";
        maxPlayers = 60;
        class Missions {};
    """)

    def test_write_short_key_does_not_clobber_longer_key_line(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(self.CFG)
        # No 'password' line exists — must be appended, not stolen from adminPassword.
        write_cfg_value(cfg, "password", "PUBLIC", quoted=True)
        assert read_cfg_value(cfg, "adminPassword") == "ADMIN"
        assert read_cfg_value(cfg, "password") == "PUBLIC"
        assert "adminpassword" not in cfg.read_text()  # key not renamed/lowercased

    def test_update_short_key_leaves_longer_key_untouched(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(self.CFG + 'password = "OLD";\n')
        write_cfg_value(cfg, "password", "NEW", quoted=True)
        assert read_cfg_value(cfg, "password") == "NEW"
        assert read_cfg_value(cfg, "adminPassword") == "ADMIN"

    def test_remove_short_key_leaves_longer_key(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(self.CFG + 'password = "OLD";\n')
        remove_cfg_key(cfg, "password")
        assert read_cfg_value(cfg, "password") is None
        assert read_cfg_value(cfg, "adminPassword") == "ADMIN"


# ---------------------------------------------------------------------------
# Regex-replacement safety: a value containing characters that re.sub treats
# specially in a replacement string (backslash, "\g<…>", "\1") must be written
# literally — not run through re's escape machinery (crash) or expanded as a
# group reference (corruption).
# ---------------------------------------------------------------------------


class TestReplacementEscaping:
    def test_value_with_backslash_does_not_crash(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(SAMPLE_CFG)
        # A backslash in the replacement string used to raise "bad escape \\s".
        write_cfg_value(cfg, "passwordAdmin", r"pa\ss", quoted=True)
        assert read_cfg_value(cfg, "passwordAdmin") == r"pa\ss"

    def test_value_with_group_reference_token_inserted_literally(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(SAMPLE_CFG)
        # "\g<0>" used to be expanded to the whole match, corrupting the line.
        write_cfg_value(cfg, "hostname", r"My \g<0> Server", quoted=True)
        assert read_cfg_value(cfg, "hostname") == r"My \g<0> Server"

    def test_value_with_numeric_backref_inserted_literally(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(SAMPLE_CFG)
        write_cfg_value(cfg, "hostname", r"clan \1 server", quoted=True)
        assert read_cfg_value(cfg, "hostname") == r"clan \1 server"

    def test_template_with_backslash_does_not_crash(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(SAMPLE_CFG)
        set_mission_template(cfg, r"dayzOffline.we\rd")
        assert r"dayzOffline.we\rd" in cfg.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# remove_cfg_key
# ---------------------------------------------------------------------------


class TestRemoveCfgKey:
    def test_remove_existing_key(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(SAMPLE_CFG)
        remove_cfg_key(cfg, "passwordAdmin")
        assert read_cfg_value(cfg, "passwordAdmin") is None
        # Other keys preserved
        assert read_cfg_value(cfg, "hostname") == "Test Server"

    def test_remove_nonexistent_key_is_noop(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(SAMPLE_CFG)
        original = cfg.read_text()
        remove_cfg_key(cfg, "nonExistentKey")
        assert cfg.read_text() == original

    def test_remove_on_missing_file_is_noop(self, tmp_path: Path):
        cfg = tmp_path / "nope.cfg"
        remove_cfg_key(cfg, "hostname")  # should not raise


# ---------------------------------------------------------------------------
# set_mission_template
# ---------------------------------------------------------------------------


class TestSetMissionTemplate:
    def test_updates_template(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(SAMPLE_CFG)
        set_mission_template(cfg, "dayzOffline.namalsk")
        text = cfg.read_text(encoding="utf-8")
        assert '"dayzOffline.namalsk"' in text
        assert '"dayzOffline.chernarusplus"' not in text

    def test_noop_on_missing_file(self, tmp_path: Path):
        cfg = tmp_path / "nope.cfg"
        set_mission_template(cfg, "dayzOffline.namalsk")  # should not raise
        assert not cfg.exists()

    def test_bohemias_own_file_keeps_the_rest_of_the_line(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        stock = _wiki_cfg().replace("EXAMPLE NAME", "Best template server", 1)
        cfg.write_text(stock, encoding="utf-8")
        set_mission_template(cfg, "dayzOffline.sakhal")
        assert read_mission_template(cfg) == "dayzOffline.sakhal"
        assert cfg.read_text(encoding="utf-8") == stock.replace(
            '"dayzOffline.chernarusplus"', '"dayzOffline.sakhal"', 1
        )

    @pytest.mark.parametrize("decoy", [
        'template = "dayzOffline.enoch";\n',
        'maxPlayers = 60; // was template = "dayzOffline.enoch";\n',
        'class Other { class DayZ { template = "dayzOffline.enoch"; }; };\n',
    ])
    def test_a_template_dayz_does_not_read_is_left_alone(self, tmp_path: Path, decoy: str):
        """The writer finds the template by the reader's rule, so the value
        written is the one read back. It used to change the first line that
        looked like `template = "…";`, wherever it was."""
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(decoy + SAMPLE_CFG)
        set_mission_template(cfg, "dayzOffline.sakhal")
        text = cfg.read_text(encoding="utf-8")
        assert read_mission_template(cfg) == "dayzOffline.sakhal"
        assert text.startswith(decoy)
        assert '"dayzOffline.chernarusplus"' not in text

    @pytest.mark.parametrize("comment", [
        '/* template = "dayzOffline.enoch"; */',
        '/* was Livonia:\n    template = "dayzOffline.enoch"; } };\n    */',
    ])
    def test_a_template_in_a_block_comment_is_not_the_mission(self, tmp_path: Path, comment: str):
        """DayZ skips `/* */` like `//`. The walk read the commented line as
        the mission and the writer changed it, leaving the real one."""
        cfg = tmp_path / "serverDZ.cfg"
        assert SAMPLE_CFG.count("template =") == 1
        cfg.write_text(SAMPLE_CFG.replace("template =", f"{comment}\n        template =", 1))
        assert read_mission_template(cfg) == "dayzOffline.chernarusplus"

        set_mission_template(cfg, "dayzOffline.sakhal")
        text = cfg.read_text(encoding="utf-8")
        assert read_mission_template(cfg) == "dayzOffline.sakhal"
        assert comment in text
        assert text.count("class DayZ") == 1

    def test_a_bare_template_is_written_quoted(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(SAMPLE_CFG.replace('"dayzOffline.chernarusplus"', "dayzOffline.chernarusplus"))
        set_mission_template(cfg, "dayzOffline.sakhal")
        assert 'template = "dayzOffline.sakhal";' in cfg.read_text(encoding="utf-8")
        assert read_mission_template(cfg) == "dayzOffline.sakhal"

    @pytest.mark.parametrize("missions", [
        "",
        "class Missions {};\n",
        "class Missions\n{\n};\n",
        "class Missions\n{\n\tclass DayZ\n\t{\n\t};\n};\n",
        'class Missions { class DayZ { }; };',
    ])
    def test_a_file_that_names_no_mission_gets_one(self, tmp_path: Path, missions: str):
        """A map switch reports the mission it set, so a cfg with no
        Missions >> DayZ >> template gets the part it lacks. It used to be
        left as it was, and the server had no mission to load."""
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text('hostname = "x";\n' + missions)
        set_mission_template(cfg, "dayzOffline.sakhal")
        text = cfg.read_text(encoding="utf-8")
        assert read_mission_template(cfg) == "dayzOffline.sakhal"
        assert check_server_cfg(text) is None
        assert text.lower().count("class missions") == 1


# ---------------------------------------------------------------------------
# get_available_missions
# ---------------------------------------------------------------------------


class TestGetAvailableMissions:
    def test_lists_mission_dirs(self, tmp_server_dir: Path):
        mp = tmp_server_dir / "mpmissions"
        (mp / "dayzOffline.chernarusplus").mkdir()
        (mp / "dayzOffline.namalsk").mkdir()
        missions = get_available_missions(tmp_server_dir)
        assert "dayzOffline.chernarusplus" in missions
        assert "dayzOffline.namalsk" in missions

    def test_ignores_dotfiles(self, tmp_server_dir: Path):
        mp = tmp_server_dir / "mpmissions"
        (mp / ".hidden").mkdir()
        missions = get_available_missions(tmp_server_dir)
        assert ".hidden" not in missions

    def test_empty_when_no_mpmissions(self, tmp_path: Path):
        assert get_available_missions(tmp_path) == []


# ---------------------------------------------------------------------------
# check_server_cfg (plans/51 C): the structure an upload or Raw save must keep
# ---------------------------------------------------------------------------

import re  # noqa: E402

import pytest  # noqa: E402

from app.parsers.battleye_cfg import check_beserver_cfg  # noqa: E402
from app.parsers.servercfg import check_server_cfg  # noqa: E402
from tests.conftest import dayz_docs, private_fixture  # noqa: E402


def _wiki_cfg() -> str:
    """Bohemia's own serverDZ.cfg, verbatim: both blocks of the wiki page."""
    wiki = dayz_docs("wiki", "pages", "server-configuration.wiki")
    blocks = re.findall(r'<syntaxhighlight lang="cpp">\n(.*?)</syntaxhighlight>', wiki.read_text(), re.S)
    return blocks[0] + blocks[1]


MODDED = (
    "﻿/* Community server */\r\n"
    'hostname = "[EU] Frosty; PvE // no raid";\r\n'
    'description = "Say ""hi"" in chat";\r\n'
    "password = \"\";\r\n"
    "maxPlayers = 60;\r\n"
    "disableBanlist = false;\r\n"
    "serverTime = SystemTime;\r\n"
    "networkObjectBatchBandwidthLimit = 0.8;\r\n"
    "defaultVisibility=1375;\r\n"
    'motd[] = {\r\n  "Welcome",\r\n  "Discord: example.gg/x"\r\n};\r\n'
    "motdInterval = 300;   // seconds\r\n"
    "#include \"extra.cfg\"\r\n"
    "class Missions\r\n{\r\n\tclass DayZ\r\n\t{\r\n"
    '\t\ttemplate = "dayzOffline.enoch";\r\n\t};\r\n};\r\n'
)


def test_bohemias_own_server_cfg_passes():
    assert check_server_cfg(_wiki_cfg()) is None


def test_a_realistic_modded_server_cfg_passes():
    """CRLF, a BOM, both comment kinds, `;` and `//` inside strings, a doubled
    quote, an unquoted value, a multi-line array and a preprocessor line."""
    assert check_server_cfg(MODDED) is None


@pytest.mark.parametrize("text, words", [
    ("garbage garbage", "Line 1: expected '=' after 'garbage'"),
    ('hostname = "x"\nmaxPlayers = 60;\n', "Line 1: hostname has no ';'"),
    ('hostname = "x;\n', "Line 1: this string is never closed"),
    ("/* never closed\nhostname = 1;", "comment is never closed"),
    ('class Missions { class DayZ { template = "a.b"; };', "never closed with '}'"),
    ('hostname = "x";\n};\n', "Line 2: '}' closes nothing"),
    ('motd[] = { "a"; "b" };\n', "';' inside the array motd[]"),
    ("maxPlayers = ;\n", "maxPlayers has no value"),
    ('{"hostname": "x"}', "expected a setting name"),
])
def test_broken_structure_is_named_by_line(text, words):
    assert words in (check_server_cfg(text) or "")


@pytest.mark.parametrize("missions", [
    "",                                                     # no class Missions
    "class Missions {};",
    'class Missions { class DayZ { template = ""; }; };',   # present but empty
])
def test_a_file_that_names_no_mission_is_refused(missions):
    assert "No mission is set" in check_server_cfg(f'hostname = "x";\n{missions}\n')


def test_a_top_level_template_is_not_a_mission():
    """The engine reads Missions >> DayZ >> template; a stray top-level key
    loads nothing, so it does not count."""
    assert "No mission is set" in check_server_cfg('template = "dayzOffline.chernarusplus";\n')


@pytest.mark.parametrize("missions", [
    'class Missions { template = "dayzOffline.enoch"; };',
    'class Missions { class Other { template = "dayzOffline.enoch"; }; };',
    'class Missions { class DayZ { class Inner { template = "dayzOffline.enoch"; }; }; };',
    'class Missions { class DayZ { /* template = "dayzOffline.enoch"; */ }; };',
])
def test_a_template_elsewhere_in_missions_is_not_a_mission(missions):
    """The check used to accept a template anywhere inside class Missions,
    so an upload could pass with a mission that neither DayZ nor zsm reads."""
    assert "No mission is set" in check_server_cfg(f'hostname = "x";\n{missions}\n')


def test_beserver_cfg_lines():
    assert check_beserver_cfg("RConPassword abc\nRConPort 2306\nRestrictRCon 0\n") is None
    assert check_beserver_cfg("RConIP \n# comment\n// comment\n\nMaxPing 300\n") is None
    for wrong in ('hostname = "x";', "RConPort = 2306", '{"a": 1}', "<xml/>"):
        assert "Key value" in (check_beserver_cfg(wrong) or ""), wrong


def test_beserver_cfg_message_never_quotes_the_line():
    """The line may carry RConPassword; the 400 goes to a toast."""
    problem = check_beserver_cfg("RConPassword = hunter2\n")
    assert problem and "hunter2" not in problem


# ---------------------------------------------------------------------------
# Each key is written in the shape Bohemia's own serverDZ.cfg gives it
# (plans/68 A-3)
# ---------------------------------------------------------------------------


def test_quoted_keys_are_the_ones_stock_quotes():
    """Every root key the wiki's serverDZ.cfg writes as `key = "…";`, and no
    other. `template` is inside class Missions, so it is indented there."""
    from app.parsers.servercfg import QUOTED_KEYS

    stock = set(re.findall(r'^(\w+)\s*=\s*"', _wiki_cfg(), re.M))
    assert stock == set(QUOTED_KEYS)


def test_every_text_field_of_the_form_is_quoted_by_stock():
    """A text field stock writes bare would need its own decision."""
    from app.commands.config_cmd import _CFG_CATEGORIES
    from app.parsers.servercfg import quoted_like_stock

    text_keys = [key for items in _CFG_CATEGORIES.values() for _l, key, kind in items if kind == "string"]
    assert text_keys and all(quoted_like_stock(key) for key in text_keys)
    other = [key for items in _CFG_CATEGORIES.values() for _l, key, kind in items if kind in ("int", "float", "bool")]
    assert not any(quoted_like_stock(key) for key in other)


def test_write_quotes_a_key_as_stock_does_by_default(tmp_path: Path):
    cfg = tmp_path / "serverDZ.cfg"
    cfg.write_text(SAMPLE_CFG)
    write_cfg_value(cfg, "hostname", "NFL")
    write_cfg_value(cfg, "passwordAdmin", "pw")
    write_cfg_value(cfg, "maxPlayers", "80")
    write_cfg_value(cfg, "logFile", "server_console.log")
    text = cfg.read_text()
    assert 'hostname = "NFL";' in text
    assert 'passwordAdmin = "pw";' in text
    assert "maxPlayers = 80;" in text
    assert 'logFile = "server_console.log";' in text
    with pytest.raises(CfgValueError, match="^hostname contains a double quote"):
        write_cfg_value(cfg, "hostname", 'Say "hi"')


LONE = "\ud83d"  # half an emoji, what JSON's "\\ud83d" decodes to: no UTF-8 file can hold it


def test_a_value_no_file_can_hold_is_refused(tmp_path: Path):
    cfg = tmp_path / "serverDZ.cfg"
    cfg.write_text(SAMPLE_CFG, encoding="utf-8")
    with pytest.raises(CfgValueError):
        write_cfg_value(cfg, "hostname", f"My Server {LONE}")
    assert cfg.read_text(encoding="utf-8") == SAMPLE_CFG


def test_a_write_that_fails_leaves_the_file_as_it_was(tmp_path: Path):
    """`write_text` emptied the file before it encoded the text, so the failed
    request left a serverDZ.cfg of 0 bytes."""
    cfg = tmp_path / "serverDZ.cfg"
    cfg.write_text(SAMPLE_CFG, encoding="utf-8")
    with pytest.raises(UnicodeEncodeError):
        set_mission_template(cfg, f"dayzOffline{LONE}")
    assert cfg.read_text(encoding="utf-8") == SAMPLE_CFG
    assert [f.name for f in tmp_path.iterdir()] == ["serverDZ.cfg"]


# The first lines and the Missions block of the serverDZ.cfg DayZ 1.29 ships,
# in the private fixtures (servercfg/stock-1.29-crlf.cfg; plans/86 D2): it is
# CRLF.
def stock_crlf() -> str:
    return private_fixture("servercfg/stock-1.29-crlf.cfg")


def test_a_crlf_cfg_stays_crlf_through_every_writer(tmp_path):
    cfg = tmp_path / "serverDZ.cfg"
    cfg.write_bytes(stock_crlf().encode())
    write_cfg_value(cfg, "hostname", "X")
    write_cfg_value(cfg, "maxPlayers", "42")          # appended before class Missions
    set_mission_template(cfg, "dayzOffline.enoch")
    remove_cfg_key(cfg, "password")
    assert cfg.read_bytes().decode() == (
        stock_crlf()
        .replace('"EXAMPLE NAME"', '"X"')
        .replace('password = "";              // Password to connect to the server\r\n', "")
        .replace("\r\nclass Missions", "\r\nmaxPlayers = 42;\r\nclass Missions")
        .replace("dayzOffline.chernarusplus", "dayzOffline.enoch")
    )


@pytest.mark.parametrize("before", [
    b'hostname = "X";\r\nclass Missions\r\n{\r\n    class DayZ\r\n    {\r\n    };\r\n};\r\n',
    b'hostname = "X";\r\nclass Missions\r\n{\r\n};\r\n',
    b'hostname = "X";\r\n',
])
def test_a_crlf_cfg_gets_crlf_mission_lines(tmp_path, before):
    cfg = tmp_path / "serverDZ.cfg"
    cfg.write_bytes(before)
    set_mission_template(cfg, "dayzOffline.enoch")
    assert b"\n" not in cfg.read_bytes().replace(b"\r\n", b"")
    assert read_mission_template(cfg) == "dayzOffline.enoch"


def test_a_key_appended_to_a_file_without_a_final_newline_gets_its_own_line(tmp_path):
    cfg = tmp_path / "serverDZ.cfg"
    cfg.write_bytes(b'hostname = "X";')
    write_cfg_value(cfg, "maxPlayers", "42")
    assert cfg.read_bytes() == b'hostname = "X";\nmaxPlayers = 42;\n'


def test_a_byte_order_mark_neither_hides_the_first_key_nor_is_lost(tmp_path):
    """Notepad saves UTF-8 with a BOM; the first key read as missing."""
    cfg = tmp_path / "serverDZ.cfg"
    cfg.write_bytes('﻿hostname = "X";\r\nmaxPlayers = 60;\r\n'.encode())
    assert read_cfg_values(cfg, ["hostname", "maxPlayers"]) == {"hostname": "X", "maxPlayers": "60"}
    write_cfg_value(cfg, "hostname", "Y")
    assert cfg.read_bytes() == '﻿hostname = "Y";\r\nmaxPlayers = 60;\r\n'.encode()


# ---------------------------------------------------------------------------
# instanceId names the persistence folder
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cfg, folder", [
    # Measured on 1.29 (2026 Q4) from "Selected storage directory":
    ("instanceId = 7;", "storage_7"),
    ("instanceId = 0;", "storage_0"),
    ("instanceId = -1;", "storage_-1"),
    ("instanceId = 1;             // DayZ server instance id", "storage_1"),
    ("instanceId=12;", "storage_12"),
    # The server refuses to start with these, so nothing is saved elsewhere.
    ("", "storage_1"),
    ("instanceId = two;", "storage_1"),
    ('instanceId = "";', "storage_1"),
    ("// instanceId = 9;", "storage_1"),
    # Only the root key is the server's.
    ("class Other\n{\n    instanceId = 9;\n};", "storage_1"),
    ("// instanceId = 9;\ninstanceId = 3;", "storage_3"),
])
def test_the_storage_folder_is_named_by_instance_id(tmp_path, cfg, folder):
    path = tmp_path / "serverDZ.cfg"
    path.write_text(f'hostname = "x";\n{cfg}\n')
    assert storage_folder_name(path) == folder


def test_no_cfg_at_all_means_storage_1(tmp_path):
    assert storage_folder_name(tmp_path / "serverDZ.cfg") == "storage_1"


@pytest.mark.parametrize("name, expected", [
    ("storage_1", True), ("storage_27", True), ("storage_0", True), ("storage_-1", True),
    ("storage", False), ("storage_", False), ("storage_old", False), ("storage_1.bak", False),
    ("Storage_1", False), ("my_storage_1", False), ("storage_1\n", False),
])
def test_what_counts_as_a_storage_folder(name, expected):
    assert is_storage_folder(name) is expected


@pytest.mark.parametrize("comment", [
    '/* hostname = "Old"; maxPlayers = 10; */',
    '/* the old settings:\nhostname = "Old";\nmaxPlayers = 10;\n*/',
])
def test_a_root_key_in_a_block_comment_is_not_the_setting(tmp_path: Path, comment: str):
    """DayZ skips `/* */` like `//`. The reader showed the commented value,
    the writer changed the commented line and left the real one, and a
    remove deleted the comment's line."""
    cfg = tmp_path / "serverDZ.cfg"
    cfg.write_text(comment + "\n" + SAMPLE_CFG)
    assert read_cfg_values(cfg, ["hostname", "maxPlayers"]) == {"hostname": "Test Server", "maxPlayers": "60"}

    write_cfg_value(cfg, "hostname", "New")
    write_cfg_value(cfg, "maxPlayers", "42")
    text = cfg.read_text(encoding="utf-8")
    assert text.startswith(comment + "\n")
    assert read_cfg_values(cfg, ["hostname", "maxPlayers"]) == {"hostname": "New", "maxPlayers": "42"}

    remove_cfg_key(cfg, "maxPlayers")
    text = cfg.read_text(encoding="utf-8")
    assert text.startswith(comment + "\n")
    assert read_cfg_value(cfg, "maxPlayers") is None


def test_a_brace_in_a_block_comment_does_not_hide_the_keys_after_it(tmp_path: Path):
    cfg = tmp_path / "serverDZ.cfg"
    cfg.write_text("/* class Old {\n*/\n" + SAMPLE_CFG)
    assert read_cfg_value(cfg, "maxPlayers") == "60"
    write_cfg_value(cfg, "maxPlayers", "42")
    assert cfg.read_text(encoding="utf-8").count("maxPlayers") == 1
    assert read_cfg_value(cfg, "maxPlayers") == "42"


# ---------------------------------------------------------------------------
# read_cfg_list / write_cfg_list (motd[])
# ---------------------------------------------------------------------------


def test_read_cfg_list_reads_a_multi_line_array(tmp_path: Path):
    from app.parsers.servercfg import read_cfg_list

    cfg = tmp_path / "serverDZ.cfg"
    cfg.write_bytes(MODDED.encode())
    assert read_cfg_list(cfg, "motd") == ["Welcome", "Discord: example.gg/x"]


def test_read_cfg_list_is_none_without_the_array(tmp_path: Path):
    from app.parsers.servercfg import read_cfg_list

    cfg = tmp_path / "serverDZ.cfg"
    cfg.write_text(SAMPLE_CFG)
    assert read_cfg_list(cfg, "motd") is None


def test_read_cfg_list_ignores_a_brace_in_a_message_and_a_commented_array(tmp_path: Path):
    from app.parsers.servercfg import read_cfg_list

    cfg = tmp_path / "serverDZ.cfg"
    cfg.write_text('// motd[] = { "old" };\nmotd[] = { "a } b", "say ""hi""" };\nmaxPlayers = 10;\n')
    assert read_cfg_list(cfg, "motd") == ["a } b", 'say "hi"']


def test_write_cfg_list_replaces_a_multi_line_array_and_keeps_crlf(tmp_path: Path):
    from app.parsers.servercfg import read_cfg_list, write_cfg_list

    cfg = tmp_path / "serverDZ.cfg"
    cfg.write_bytes(MODDED.encode())
    write_cfg_list(cfg, "motd", ["Hello", "Rules: be nice"])
    text = cfg.read_bytes().decode()
    assert 'motd[] = { "Hello", "Rules: be nice" };\r\nmotdInterval = 300;' in text
    assert "Welcome" not in text
    assert text.startswith("﻿")
    assert check_server_cfg(text) is None
    assert read_cfg_list(cfg, "motd") == ["Hello", "Rules: be nice"]


def test_write_cfg_list_adds_the_array_before_class_missions(tmp_path: Path):
    from app.parsers.servercfg import read_cfg_list, write_cfg_list

    cfg = tmp_path / "serverDZ.cfg"
    cfg.write_text(SAMPLE_CFG)
    write_cfg_list(cfg, "motd", ["Hi"])
    text = cfg.read_text()
    assert text.index('motd[] = { "Hi" };') < text.index("class Missions")
    assert read_cfg_list(cfg, "motd") == ["Hi"]
    write_cfg_list(cfg, "motd", [])
    assert "motd[] = {};" in cfg.read_text()
    assert read_cfg_list(cfg, "motd") == []


def test_write_cfg_list_refuses_a_quote_before_writing(tmp_path: Path):
    from app.parsers.servercfg import write_cfg_list

    cfg = tmp_path / "serverDZ.cfg"
    cfg.write_text(SAMPLE_CFG)
    with pytest.raises(CfgValueError):
        write_cfg_list(cfg, "motd", ["ok", 'a "quote"'])
    assert cfg.read_text() == SAMPLE_CFG


def test_a_number_with_a_unit_carries_it_to_the_web_form():
    """The web form shows the unit in the box (webui UnitInput.jsx)."""
    from app.web.editors.server_forms import _gameplay_field, _serverdz_field

    assert _serverdz_field("respawnTime", "", "int", "5")["unit"] == "seconds"
    assert _serverdz_field("pingWarning", "", "int", "200")["unit"] == "ms"
    assert "unit" not in _serverdz_field("maxPlayers", "", "int", "60")
    assert _gameplay_field("PlayerData.MovementData.timeToSprint", 0.45)["unit"] == "seconds"


def test_a_label_does_not_repeat_the_unit_its_box_shows():
    """"Message of the day interval (seconds)" beside a "seconds" box says the
    unit twice, so the web form drops it. The CLI's label keeps it."""
    from app.commands.config_cmd import _CFG_CATEGORIES
    from app.parsers.servercfg import FIELD_UNITS
    from app.web.editors._help import label_without_unit
    from app.web.editors.server_forms import _serverdz_field

    field = _serverdz_field("motdInterval", "Message of the day interval (seconds)", "float", "1")
    assert field["label"] == "Message of the day interval"
    assert label_without_unit("Ping (ms)", None) == "Ping (ms)"
    assert label_without_unit("Range (meters)", "seconds") == "Range (meters)"
    assert label_without_unit("(seconds)", "seconds") == "(seconds)"
    for items in _CFG_CATEGORIES.values():
        for label, key, kind in items:
            unit = FIELD_UNITS.get(key)
            if unit:
                shown = _serverdz_field(key, label, kind, None)["label"]
                assert not shown.lower().endswith(f"({unit})"), key
