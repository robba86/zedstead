"""Tests for app/shared/server_args.py."""

from __future__ import annotations

import pytest

from app.shared.server_args import split_server_mod_flag


def test_split_takes_the_server_mod_token_out_and_keeps_the_rest_in_order():
    entries, rest = split_server_mod_flag("-port=2302 -serverMod=@A;@B -dologs")
    assert entries == ["@A", "@B"]
    assert rest == "-port=2302 -dologs"


@pytest.mark.parametrize(
    "token", ['"-serverMod=@A;@B"', "'-serverMod=@A;@B'", "-SERVERMOD=@A;@B"]
)
def test_split_reads_quoted_and_any_case(token: str):
    assert split_server_mod_flag(f"-port=2302 {token}") == (["@A", "@B"], "-port=2302")


def test_split_drops_empty_entries():
    assert split_server_mod_flag("-serverMod=;@A;;") == (["@A"], "")


def test_split_without_the_flag_returns_the_input_unchanged():
    assert split_server_mod_flag("-port=2302 -mod=@CF") == ([], "-port=2302 -mod=@CF")
    assert split_server_mod_flag("") == ([], "")
