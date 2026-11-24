"""Tests for app/ops/whitelist_ops.py and GET/POST/DELETE /api/whitelist.

The point of the feature is a lockout: `enableWhitelist = 1` with an empty
whitelist.txt makes the engine refuse everybody (kick 0x0004000C), and zsm
could reach that state from the serverDZ.cfg editor with no way back. So the
tests that matter most here are the ones about *not damaging* a working
whitelist — the separator the file already uses, and the atomic write.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.ops import whitelist_ops
from tests.conftest import private_fixture

PASSWORD = "testpass123"

A = "76561198000000001"
B = "76561198000000002"
C = "76561198000000003"


@pytest.fixture()
def wl(tmp_path):
    return tmp_path / "whitelist.txt"


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


def test_a_missing_or_empty_file_is_an_empty_list(wl):
    assert whitelist_ops.read_whitelist(wl) == []
    wl.write_text("", encoding="utf-8")
    assert whitelist_ops.read_whitelist(wl) == []
    wl.write_text("   \n\n", encoding="utf-8")
    assert whitelist_ops.read_whitelist(wl) == []


def test_semicolons_are_read_in_file_order(wl):
    wl.write_text(f"{A};{B};{C}\n", encoding="utf-8")
    assert whitelist_ops.read_whitelist(wl) == [A, B, C]


def test_one_id_per_line_is_read_too(wl):
    """Bohemia's own layout, and the one the stock file ships in. A reader
    that only understood the semicolon form an earlier zsm wrote would tell an
    admin their whitelist was empty when it is not."""
    wl.write_text(f"{A}\n{B}\n{C}\n", encoding="utf-8")
    assert whitelist_ops.read_whitelist(wl) == [A, B, C]


def test_a_bom_and_stray_tokens_do_not_break_it(wl):
    wl.write_text(f"﻿{A}; not-an-id ;{B};;\n", encoding="utf-8")
    assert whitelist_ops.read_whitelist(wl) == [A, B]


# ---------------------------------------------------------------------------
# writing — the half that can lock people out
# ---------------------------------------------------------------------------


def test_a_newline_file_stays_a_newline_file(wl):
    """Whichever form is on disk is the form this admin's server is currently
    accepting. Normalising it would be zsm deciding on no evidence that the
    other one is wrong — and being wrong there refuses every player."""
    wl.write_text(f"{A}\n{B}\n", encoding="utf-8")
    whitelist_ops.add_whitelist_id(wl, C)
    assert wl.read_text(encoding="utf-8") == f"{A}\n{B}\n{C}\n"


def test_a_semicolon_file_stays_a_semicolon_file(wl):
    wl.write_text(f"{A};{B}\n", encoding="utf-8")
    whitelist_ops.add_whitelist_id(wl, C)
    assert wl.read_text(encoding="utf-8") == f"{A};{B};{C}\n"


def test_a_new_file_is_one_id_per_line(wl):
    """The layout of the stock file Bohemia ships. The first version wrote a
    single ID and then, finding no newline between IDs, switched the file to
    semicolons on the second add."""
    whitelist_ops.add_whitelist_id(wl, A)
    whitelist_ops.add_whitelist_id(wl, B)
    assert wl.read_text(encoding="utf-8") == f"{A}\n{B}\n"


def test_an_id_with_a_trailing_newline_is_refused(wl):
    """It passed the Steam64 check and went in as the ID plus a blank line,
    and a second add wrote the same ID again (plans/68 LB-8)."""
    whitelist_ops.add_whitelist_id(wl, A)
    with pytest.raises(ValueError):
        whitelist_ops.add_whitelist_id(wl, A + "\n")
    assert wl.read_text(encoding="utf-8") == f"{A}\n"


# The whitelist.txt a 1.29 Linux server install ships, byte for byte (copied
# out of the test container in 2026 Q3), in the private fixtures
# (whitelist/stock-1.29.txt; plans/86 D2): CRLF, no final newline, and an
# example line holding a 44-character player ID. Keep it as it is: it is the
# only statement of the format there is.
def stock() -> bytes:
    return private_fixture("whitelist/stock-1.29.txt").encode()


def test_the_stock_file_keeps_its_header_and_its_player_ids(wl):
    """Found by plans/42 stage 3: adding one ID to the stock file rewrote it as
    that ID alone — header gone, and the 44-character entry with it. On a
    server with the switch on, deleting a 44-character ID kicks that player."""
    wl.write_bytes(stock())
    assert whitelist_ops.read_whitelist(wl) == [EXAMPLE_ID]

    whitelist_ops.add_whitelist_id(wl, A)
    whitelist_ops.add_whitelist_id(wl, B)
    assert wl.read_bytes() == stock() + f"\r\n{A}\r\n{B}".encode()
    assert whitelist_ops.read_whitelist(wl) == [EXAMPLE_ID, A, B]

    whitelist_ops.remove_whitelist_id(wl, A)
    whitelist_ops.remove_whitelist_id(wl, B)
    # No final newline before, none after: an add then remove left one.
    assert wl.read_bytes() == stock()


EXAMPLE_ID = "1111111111112222222222222333333333XXXXXXAAAA"
PLAYER_ID = "Xq3Hb7Rk9Lm2Np5Tv8Wz1Ac4Df6Gh0Jk-Mn_Pr+St/U="


def test_the_44_character_player_id_is_listed_added_and_removed(wl):
    """The stock header asks for "his player ID (44 characters long ID)" from
    the .ADM, and the panel took only Steam64 IDs."""
    wl.write_bytes(stock())
    assert whitelist_ops.add_whitelist_id(wl, PLAYER_ID) is True
    assert whitelist_ops.read_whitelist(wl) == [EXAMPLE_ID, PLAYER_ID]
    assert whitelist_ops.remove_whitelist_id(wl, EXAMPLE_ID) is True
    assert whitelist_ops.read_whitelist(wl) == [PLAYER_ID]
    assert wl.read_bytes().startswith(stock()[: stock().index(b"1111")])
    for bad in (PLAYER_ID[:-1], PLAYER_ID + "A", PLAYER_ID[:-1] + "!"):
        with pytest.raises(ValueError):
            whitelist_ops.add_whitelist_id(wl, bad)


def test_a_file_with_a_final_newline_keeps_it(wl):
    wl.write_bytes(b"//admins\r\n" + A.encode() + b"\r\n")
    whitelist_ops.add_whitelist_id(wl, B)
    whitelist_ops.remove_whitelist_id(wl, B)
    assert wl.read_bytes() == b"//admins\r\n" + A.encode() + b"\r\n"


def test_an_id_in_a_comment_is_not_an_entry(wl):
    wl.write_text(f"//{A} was banned\n{B}\t//{C} alt\n", encoding="utf-8")
    assert whitelist_ops.read_whitelist(wl) == [B]


def test_removing_an_id_takes_its_label_and_nothing_else(wl):
    wl.write_text(f"//admins\n{A}\t//Robba\n{B}\t//Kalle\n", encoding="utf-8")
    whitelist_ops.remove_whitelist_id(wl, A)
    assert wl.read_text(encoding="utf-8") == f"//admins\n{B}\t//Kalle\n"


def test_removing_from_a_semicolon_line_keeps_the_rest_of_it(wl):
    wl.write_text(f"{A};{B};{C}\n", encoding="utf-8")
    whitelist_ops.remove_whitelist_id(wl, B)
    assert wl.read_text(encoding="utf-8") == f"{A};{C}\n"


def test_add_refuses_anything_that_is_not_an_id(wl):
    for bad in ("", "123", "7656119000000000", "abc", "76561198000000001x"):
        with pytest.raises(ValueError):
            whitelist_ops.add_whitelist_id(wl, bad)
    assert not wl.exists()


def test_add_is_idempotent_and_says_so(wl):
    assert whitelist_ops.add_whitelist_id(wl, A) is True
    assert whitelist_ops.add_whitelist_id(wl, A) is False
    assert whitelist_ops.read_whitelist(wl) == [A]


def test_remove_reports_a_miss_instead_of_rewriting_the_file(wl):
    wl.write_text(f"{A};{B}\n", encoding="utf-8")
    before = wl.read_text(encoding="utf-8")
    assert whitelist_ops.remove_whitelist_id(wl, C) is False
    assert wl.read_text(encoding="utf-8") == before
    assert whitelist_ops.remove_whitelist_id(wl, A) is True
    assert whitelist_ops.read_whitelist(wl) == [B]


def test_the_path_is_the_server_install_root(sample_config):
    path = whitelist_ops.whitelist_path(sample_config)
    assert path.parent == sample_config.server_dir_path
    assert path.name == "whitelist.txt"


# ---------------------------------------------------------------------------
# the endpoints
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(sample_config):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    app = server.create_app(sample_config)
    tclient = TestClient(app)
    tclient.post("/api/auth/login", json={"password": PASSWORD})
    return tclient


def test_whitelist_requires_a_session(sample_config):
    from app.web import server

    app = server.create_app(sample_config)
    assert TestClient(app).get("/api/whitelist").status_code == 401


def test_the_list_reports_the_switch_beside_the_ids(client, sample_config):
    """The two are only dangerous together, so the page must not have to
    remember to ask for the switch separately."""
    body = client.get("/api/whitelist").json()
    assert body["ids"] == []
    assert body["enabled"] is False
    assert body["path"].endswith("whitelist.txt")

    cfg = sample_config.server_cfg_path
    cfg.write_text(cfg.read_text(encoding="utf-8") + "\nenableWhitelist = 1;\n", encoding="utf-8")
    assert client.get("/api/whitelist").json()["enabled"] is True


def test_add_and_remove_round_trip(client, sample_config):
    assert client.post("/api/whitelist", json={"steam_id": A}).json() == {"added": True}
    assert client.post("/api/whitelist", json={"steam_id": A}).json() == {"added": False}
    assert client.get("/api/whitelist").json()["ids"] == [A]

    assert client.request("DELETE", f"/api/whitelist?steam_id={A}").status_code == 200
    assert client.get("/api/whitelist").json()["ids"] == []


def test_a_bad_id_is_422_not_a_written_file(client, sample_config):
    resp = client.post("/api/whitelist", json={"steam_id": "nope"})
    assert resp.status_code == 422
    assert not whitelist_ops.whitelist_path(sample_config).exists()


def test_removing_something_that_is_not_there_is_404(client):
    assert client.request("DELETE", f"/api/whitelist?steam_id={A}").status_code == 404


def test_the_comment_on_an_id_line_is_read(tmp_path):
    """The stock file's example ID says it is one only in its comment, so the
    Whitelist tab listed it as a real player."""
    path = tmp_path / "whitelist.txt"
    example = "1111111111112222222222222333333333XXXXXXAAAA"
    path.write_text(f"//header\r\n76561198000000001\r\n{example}\t//Example of a character ID\r\n", newline="")

    assert whitelist_ops.whitelist_comments(path) == {example: "Example of a character ID"}
