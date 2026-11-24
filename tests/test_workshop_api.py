"""Tests for cli.workshop_api and cli.acf_parser."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.parsers.acf_parser import get_installed_timestamps, parse_acf
import urllib.error

from app.modmgr import workshop_api
from app.modmgr.workshop_api import (
    ModUpdateInfo,
    UpdateCheck,
    UpdateCheckError,
    WorkshopModInfo,
    check_for_updates,
    get_workshop_details,
    load_detected_updates,
    load_stored_timestamps,
    load_update_check,
    save_detected_updates,
    save_stored_timestamps,
)


# ---------------------------------------------------------------------------
# ACF Parser
# ---------------------------------------------------------------------------


SAMPLE_ACF = """\
"AppWorkshop"
{
    "appid"         "221100"
    "SizeOnDisk"    "3066063023"
    "WorkshopItemsInstalled"
    {
        "2086515808"
        {
            "size"          "364196"
            "timeupdated"   "1670078051"
            "manifest"      "8234567890123456789"
        }
        "2075704025"
        {
            "size"          "512000"
            "timeupdated"   "1680000000"
        }
    }
    "WorkshopItemDetails"
    {
        "2086515808"
        {
            "manifest"      "8234567890123456789"
            "timeupdated"   "1670078051"
        }
        "9999999999"
        {
            "timeupdated"   "1690000000"
        }
    }
}
"""


class TestAcfParser:
    def test_parse_structure(self):
        data = parse_acf(SAMPLE_ACF)
        assert "AppWorkshop" in data
        assert data["AppWorkshop"]["appid"] == "221100"

    def test_nested_sections(self):
        data = parse_acf(SAMPLE_ACF)
        installed = data["AppWorkshop"]["WorkshopItemsInstalled"]
        assert "2086515808" in installed
        assert installed["2086515808"]["timeupdated"] == "1670078051"

    def test_get_installed_timestamps(self):
        data = parse_acf(SAMPLE_ACF)
        ts = get_installed_timestamps(data)
        assert ts["2086515808"] == 1670078051
        assert ts["2075704025"] == 1680000000
        # From WorkshopItemDetails (fallback for items not in Installed)
        assert ts["9999999999"] == 1690000000

    def test_parse_empty(self):
        data = parse_acf("")
        assert data == {}

    def test_parse_comments(self):
        text = '"key" "value" // this is a comment\n"key2" "value2"'
        data = parse_acf(text)
        assert data["key"] == "value"
        assert data["key2"] == "value2"

    def test_parse_unquoted(self):
        text = "key value\nkey2\n{\n  nested nval\n}"
        data = parse_acf(text)
        assert data["key"] == "value"
        assert data["key2"]["nested"] == "nval"

    def test_parse_file(self, tmp_path: Path):
        from app.parsers.acf_parser import parse_acf_file

        p = tmp_path / "test.acf"
        p.write_text(SAMPLE_ACF, encoding="utf-8")
        data = parse_acf_file(p)
        assert "AppWorkshop" in data


# ---------------------------------------------------------------------------
# Workshop API — update detection
# ---------------------------------------------------------------------------


class TestModUpdateInfo:
    def test_age_display_minutes(self):
        info = ModUpdateInfo(
            mod_id="123", title="Test", remote_updated=int(time.time()) - 300, local_updated=0
        )
        assert "m ago" in info.age_display

    def test_age_display_hours(self):
        info = ModUpdateInfo(
            mod_id="123", title="Test", remote_updated=int(time.time()) - 7200, local_updated=0
        )
        assert "h ago" in info.age_display

    def test_age_display_days(self):
        info = ModUpdateInfo(
            mod_id="123", title="Test", remote_updated=int(time.time()) - 172800, local_updated=0
        )
        assert "d ago" in info.age_display


class TestCheckForUpdates:
    def test_detects_outdated_mods(self):
        with patch("app.modmgr.workshop_api.get_workshop_details") as mock_details:
            mock_details.return_value = {
                "111": WorkshopModInfo(mod_id="111", title="ModA", time_updated=2000),
                "222": WorkshopModInfo(mod_id="222", title="ModB", time_updated=3000),
            }
            outdated = check_for_updates(
                mod_ids=["111", "222"],
                local_timestamps={"111": 1000, "222": 3000},
                api_key="test_key",
            ).updates
            assert len(outdated) == 1
            assert outdated[0].mod_id == "111"
            assert outdated[0].title == "ModA"

    def test_no_updates(self):
        with patch("app.modmgr.workshop_api.get_workshop_details") as mock_details:
            mock_details.return_value = {
                "111": WorkshopModInfo(mod_id="111", title="ModA", time_updated=1000),
            }
            outdated = check_for_updates(
                mod_ids=["111"],
                local_timestamps={"111": 1000},
                api_key="test_key",
            ).updates
            assert len(outdated) == 0

    def test_no_api_key_is_an_error_not_an_empty_answer(self):
        # plans/50 B (F17): `[]` here read as "no updates" on the Updates tab.
        with pytest.raises(UpdateCheckError) as err:
            check_for_updates(["111"], {"111": 1000}, api_key="")
        assert err.value.reason == workshop_api.NO_API_KEY
        assert "steam_api_key" in err.value.message

    def test_empty_mod_list_returns_empty(self):
        result = check_for_updates([], {}, api_key="key")
        assert result.updates == [] and result.total == 0
        assert result.checked_at is not None

    def test_counts_what_steam_answered_for(self):
        # A mod deleted or hidden on the Workshop is in total, not in checked.
        with patch("app.modmgr.workshop_api.get_workshop_details") as mock_details:
            mock_details.return_value = {
                "111": WorkshopModInfo(mod_id="111", title="ModA", time_updated=1000),
            }
            result = check_for_updates(["111", "222"], {"111": 1000}, api_key="k")
        assert (result.checked, result.total) == (1, 2)
        assert mock_details.call_args.kwargs["strict"] is True

    def test_new_mod_not_in_local(self):
        with patch("app.modmgr.workshop_api.get_workshop_details") as mock_details:
            mock_details.return_value = {
                "111": WorkshopModInfo(mod_id="111", title="ModA", time_updated=5000),
            }
            outdated = check_for_updates(
                mod_ids=["111"],
                local_timestamps={},  # not installed yet
                api_key="test_key",
            ).updates
            assert len(outdated) == 1


# ---------------------------------------------------------------------------
# GetDetails requests (plans/50 B)
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


_ONE_MOD = json.dumps({"response": {"publishedfiledetails": [
    {"result": 1, "publishedfileid": "111", "title": "ModA", "time_updated": 5}
]}}).encode()


class TestGetDetailsRequest:
    def test_it_is_a_get_with_the_ids_in_the_query(self, monkeypatch):
        # Steam answers a POST to GetDetails with 405 whatever the key
        # (measured in 2026 Q3), which is why nothing using it ever worked.
        seen = []

        def _urlopen(req, timeout):
            seen.append(req)
            return _Resp(_ONE_MOD)

        monkeypatch.setattr(workshop_api.urllib.request, "urlopen", _urlopen)
        assert list(get_workshop_details(["111"], "k", strict=True)) == ["111"]
        (req,) = seen
        assert req.get_method() == "GET"
        assert req.data is None
        assert "publishedfileids%5B0%5D=111" in req.full_url

    def test_children_query_is_a_get_too(self, monkeypatch):
        seen = []

        def _urlopen(req, timeout):
            seen.append(req)
            return _Resp(json.dumps({"response": {"publishedfiledetails": []}}).encode())

        monkeypatch.setattr(workshop_api.urllib.request, "urlopen", _urlopen)
        workshop_api.get_workshop_children(["111"], "k")
        assert seen[0].get_method() == "GET" and "includechildren=1" in seen[0].full_url

    def test_ids_that_are_not_digits_are_dropped_from_the_answer(self, monkeypatch):
        """plans/68 CON-12: an ID from Steam's answer becomes a SteamCMD
        argument and a folder under the workshop dir, so it is held to the
        digits-only rule an ID from a file is."""
        answer = json.dumps({"response": {"publishedfiledetails": [
            {"result": 1, "publishedfileid": "111", "title": "Good", "children": [
                {"publishedfileid": "222"},
                {"publishedfileid": "+quit"},
                {"publishedfileid": "../333"},
                {"publishedfileid": "٤٥"},
            ]},
            {"result": 1, "publishedfileid": "-x", "title": "Bad", "children": []},
        ]}}).encode()
        monkeypatch.setattr(
            workshop_api.urllib.request, "urlopen", lambda req, timeout: _Resp(answer)
        )
        assert list(get_workshop_details(["111"], "k", strict=True)) == ["111"]
        assert workshop_api.get_workshop_children(["111"], "k") == {"111": ["222"]}

    @pytest.mark.parametrize("code", [401, 403])
    def test_strict_names_the_setting_when_the_key_is_refused(self, monkeypatch, code):
        def _urlopen(req, timeout):
            raise urllib.error.HTTPError(req.full_url, code, "no", {}, None)

        monkeypatch.setattr(workshop_api.urllib.request, "urlopen", _urlopen)
        with pytest.raises(UpdateCheckError) as err:
            get_workshop_details(["111"], "secret-key", strict=True)
        assert err.value.reason == workshop_api.REJECTED
        assert "steam_api_key" in err.value.message
        assert "secret-key" not in err.value.message

    @pytest.mark.parametrize("failure", [
        urllib.error.URLError("Name or service not known"),
        TimeoutError("timed out"),
        urllib.error.HTTPError("u", 500, "err", {}, None),
    ])
    def test_strict_reports_anything_else_as_unreachable(self, monkeypatch, failure):
        def _urlopen(req, timeout):
            raise failure

        monkeypatch.setattr(workshop_api.urllib.request, "urlopen", _urlopen)
        with pytest.raises(UpdateCheckError) as err:
            get_workshop_details(["111"], "secret-key", strict=True)
        assert err.value.reason == workshop_api.UNREACHABLE
        assert "secret-key" not in err.value.message

    def test_strict_reports_a_body_that_is_not_json(self, monkeypatch):
        monkeypatch.setattr(workshop_api.urllib.request, "urlopen", lambda req, timeout: _Resp(b"<html>"))
        with pytest.raises(UpdateCheckError) as err:
            get_workshop_details(["111"], "k", strict=True)
        assert err.value.reason == workshop_api.UNREACHABLE

    def test_non_strict_still_answers_empty(self, monkeypatch):
        # The title lookup is best-effort; its silence on failure is right.
        def _urlopen(req, timeout):
            raise urllib.error.URLError("down")

        monkeypatch.setattr(workshop_api.urllib.request, "urlopen", _urlopen)
        assert get_workshop_details(["111"], "k") == {}


# ---------------------------------------------------------------------------
# Detected-updates cache
# ---------------------------------------------------------------------------


class TestDetectedUpdatesCache:
    def test_round_trip_keeps_when_and_how_many(self, tmp_path: Path):
        path = tmp_path / "mod_updates.json"
        info = ModUpdateInfo(mod_id="1", title="A", remote_updated=10, local_updated=5)
        save_detected_updates(path, UpdateCheck(updates=[info], checked=24, total=25, checked_at=1234))
        check = load_update_check(path)
        assert (check.checked, check.total, check.checked_at) == (24, 25, 1234)
        assert [u.mod_id for u in check.updates] == ["1"]
        assert [u.mod_id for u in load_detected_updates(path)] == ["1"]

    def test_an_empty_check_is_still_a_check(self, tmp_path: Path):
        path = tmp_path / "mod_updates.json"
        save_detected_updates(path, UpdateCheck(checked=3, total=3, checked_at=99))
        assert load_update_check(path).checked_at == 99

    def test_the_legacy_bare_list_still_loads(self, tmp_path: Path):
        path = tmp_path / "mod_updates.json"
        path.write_text(json.dumps([{
            "mod_id": "1", "title": "A", "remote_updated": 10, "local_updated": 5, "detected_at": 1,
        }]), encoding="utf-8")
        check = load_update_check(path)
        assert [u.mod_id for u in check.updates] == ["1"]
        assert check.checked_at is None  # no timestamp in the old format

    def test_no_file_is_never_checked(self, tmp_path: Path):
        check = load_update_check(tmp_path / "missing.json")
        assert check.updates == [] and check.checked_at is None

    def test_removing_mods_keeps_the_check_itself(self, tmp_path: Path):
        path = tmp_path / "mod_updates.json"
        infos = [ModUpdateInfo(mod_id=i, title=i, remote_updated=10, local_updated=5) for i in "12"]
        save_detected_updates(path, UpdateCheck(updates=infos, checked=2, total=2, checked_at=7))
        workshop_api.remove_detected_updates(path, {"1"})
        check = load_update_check(path)
        assert [u.mod_id for u in check.updates] == ["2"] and check.checked_at == 7


# ---------------------------------------------------------------------------
# Timestamp persistence
# ---------------------------------------------------------------------------


class TestTimestampPersistence:
    def test_save_and_load(self, tmp_path: Path):
        p = tmp_path / "timestamps.json"
        save_stored_timestamps(p, {"111": 1000, "222": 2000})

        loaded = load_stored_timestamps(p)
        assert loaded["111"] == 1000
        assert loaded["222"] == 2000

    def test_load_missing_file(self, tmp_path: Path):
        p = tmp_path / "missing.json"
        assert load_stored_timestamps(p) == {}

    def test_load_corrupt_file(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text("not json", encoding="utf-8")
        assert load_stored_timestamps(p) == {}


class TestPublicFileSizes:
    """The Maps page's download sizes: the key-free details call, best effort."""

    def test_it_posts_the_ids_without_a_key_and_reads_the_sizes(self, monkeypatch):
        seen = []
        answer = json.dumps({"response": {"publishedfiledetails": [
            {"result": 1, "publishedfileid": "111", "file_size": "2000"},
            {"result": 9, "publishedfileid": "222"},  # removed or private
            {"result": 1, "publishedfileid": "999", "file_size": 5},  # not asked for
        ]}}).encode()

        def _urlopen(req, timeout):
            seen.append(req)
            return _Resp(answer)

        monkeypatch.setattr(workshop_api.urllib.request, "urlopen", _urlopen)
        assert workshop_api.get_public_file_sizes(["111", "222", "+quit"]) == {"111": 2000}
        (req,) = seen
        assert req.get_method() == "POST"
        assert req.full_url == workshop_api.PUBLIC_DETAILS_URL
        body = req.data.decode()
        assert "key=" not in body
        assert "itemcount=2" in body and "%2Bquit" not in body

    @pytest.mark.parametrize("failure", [
        urllib.error.URLError("Name or service not known"),
        TimeoutError("timed out"),
        urllib.error.HTTPError("u", 500, "err", {}, None),
    ])
    def test_any_failure_is_no_sizes(self, monkeypatch, failure):
        def _urlopen(req, timeout):
            raise failure

        monkeypatch.setattr(workshop_api.urllib.request, "urlopen", _urlopen)
        assert workshop_api.get_public_file_sizes(["111"]) == {}

    @pytest.mark.parametrize("body", [
        b"[]",
        b"null",
        b'{"response": []}',
        b'{"response": {"publishedfiledetails": ["x"]}}',
        b'{"response": {"publishedfiledetails": {"publishedfileid": "111"}}}',
        b'{"response": {"publishedfiledetails": [{"result": 1, "publishedfileid": ["111"]}]}}',
        b'{"response": {"publishedfiledetails": [{"result": 1, "publishedfileid": "111", "file_size": 1e999}]}}',
        b'{"response": {"publishedfiledetails": [{"result": 1, "publishedfileid": "111", "file_size": -5}]}}',
    ])
    def test_an_answer_of_any_other_shape_is_no_sizes(self, monkeypatch, body):
        """NB-4: the promise is `{}` on any failure, and the Maps page's
        download-sizes route answered 500 on these instead."""
        monkeypatch.setattr(workshop_api.urllib.request, "urlopen", lambda req, timeout: _Resp(body))
        assert workshop_api.get_public_file_sizes(["111"]) == {}

    def test_no_workshop_ids_asks_nothing(self, monkeypatch):
        def _urlopen(req, timeout):
            raise AssertionError("asked Steam about nothing")

        monkeypatch.setattr(workshop_api.urllib.request, "urlopen", _urlopen)
        assert workshop_api.get_public_file_sizes(["../x"]) == {}
