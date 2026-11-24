"""Steam Workshop name lookup — app/shared/steam.py::resolve_mod_id / search_workshop."""

from __future__ import annotations

import pytest

from app.shared import steam


def _page(*items: tuple[str, str]) -> bytes:
    """A Workshop browse page listing ``(workshop_id, title)`` results in order."""
    cards = "".join(
        '<div class="workshopItem">'
        f'<a href="https://steamcommunity.com/sharedfiles/filedetails/?id={wid}&searchtext=x" class="ugc">'
        '<div class="workshopItemPreviewHolder"></div></a>'
        f'<a href="https://steamcommunity.com/sharedfiles/filedetails/?id={wid}&searchtext=x" class="item_link">'
        f'<div class="workshopItemTitle ellipsis">{title}</div></a>'
        "</div>"
        for wid, title in items
    )
    return f"<html><body>{cards}</body></html>".encode()


def _page_hashed(*items: tuple[str, str]) -> bytes:
    """The markup Steam serves now: hashed class names, the title only in the thumbnail alt.

    Each result appears as two links, as it does on the real page.
    """
    cards = "".join(
        f'<a class="tK5agp5sRy8-" href="https://steamcommunity.com/sharedfiles/filedetails/?id={wid}">'
        f'<img alt="{title}" loading="lazy" src="https://images.steamusercontent.com/ugc/x/"></a>'
        f'<a class="_3Ycb1" href="https://steamcommunity.com/sharedfiles/filedetails/?id={wid}"></a>'
        for wid, title in items
    )
    return f"<html><body>{cards}</body></html>".encode()


class _Response:
    def __init__(self, content: bytes, status_code: int):
        self.content = content
        self.status_code = status_code


@pytest.fixture
def search_page(monkeypatch):
    """Serve ``content``/``status`` for every Workshop request and record the URLs asked for."""
    monkeypatch.setattr(steam, "STEAM_RATE_LIMIT_SECONDS", 0)
    served = {"content": b"", "status": 200, "urls": []}

    def fake_get(url, timeout=None):
        served["urls"].append(url)
        return _Response(served["content"], served["status"])

    monkeypatch.setattr(steam.requests, "get", fake_get)
    return served


class TestResolveModId:
    def test_a_name_no_title_matches_resolves_to_nothing(self, search_page):
        # 2026 Q3: a garbled By Name entry, Steam listing an unrelated item
        # first, and zsm installing that item under the garbled name.
        search_page["content"] = _page(
            ("3801237966", "DayZ Earthquake"),
            ("2572328470", "DayZ-Expansion-Market"),
        )
        assert steam.resolve_mod_id("@DayZ-ExpDayZ-Expansion-AIansion-Market", set()) is None

    def test_the_result_titled_as_the_mod_wins_even_when_not_first(self, search_page):
        search_page["content"] = _page(("111", "DayZ-Expansion-AI-Addon"), ("2792982069", "DayZ-Expansion-AI"))
        assert steam.resolve_mod_id("@DayZ-Expansion-AI", set()) == "2792982069"

    @pytest.mark.parametrize(
        ("name", "title"),
        [
            ("@Dabs Framework", "Dabs Framework"),
            ("@Community-Online-Tools", "Community Online Tools"),
            ("@cf", "CF"),
            ("VPPAdminTools", "VPPAdminTools"),
        ],
    )
    def test_titles_compare_without_case_at_sign_or_separators(self, search_page, name, title):
        search_page["content"] = _page(("42", title))
        assert steam.resolve_mod_id(name, set()) == "42"

    def test_a_result_without_a_readable_title_is_not_a_match(self, search_page):
        # No workshopItem cards, just a bare filedetails link: there is no title to compare.
        search_page["content"] = b'<a href="https://steamcommunity.com/sharedfiles/filedetails/?id=9"><img></a>'
        assert steam.resolve_mod_id("@CF", set()) is None

    def test_a_cached_name_is_not_searched(self, search_page):
        assert steam.resolve_mod_id("@CF", {"@CF"}) is None
        assert search_page["urls"] == []

    def test_a_failed_page_load_resolves_to_nothing(self, search_page):
        search_page["content"] = _page(("42", "CF"))
        search_page["status"] = 503
        assert steam.resolve_mod_id("@CF", set()) is None


    def test_reads_titles_off_the_current_markup(self, search_page):
        # 2026 Q3: Steam replaced workshopItem/workshopItemTitle with hashed
        # classes, so every title read as "Unknown (<id>)" and title matching
        # found nothing at all — @DayZ-Expansion-AI stopped resolving.
        search_page["content"] = _page_hashed(("2792982069", "DayZ-Expansion-AI"), ("111", "Other"))
        assert steam.resolve_mod_id("@DayZ-Expansion-AI", set()) == "2792982069"

    def test_the_first_of_two_equally_titled_results_wins(self, search_page):
        # The real page: the mod, then a copycat titled "@DayZ-Expansion-Market".
        search_page["content"] = _page_hashed(
            ("2572328470", "DayZ-Expansion-Market"),
            ("3502910598", "@DayZ-Expansion-Market"),
        )
        assert steam.resolve_mod_id("@DayZ-Expansion-Market", set()) == "2572328470"


class TestSearchWorkshop:
    def test_lists_every_result_in_page_order(self, search_page):
        search_page["content"] = _page(("1", "First"), ("2", "Second"))
        assert steam.search_workshop("x") == [("1", "First"), ("2", "Second")]

    def test_deduplicates_the_two_links_each_result_carries(self, search_page):
        search_page["content"] = _page_hashed(("1", "First"), ("2", "Second"))
        assert steam.search_workshop("x") == [("1", "First"), ("2", "Second")]


# -- the query is encoded into the browse URL (plans/68) ---------------------

_AWKWARD = "Dabs & Co #2 C++ mod"


@pytest.mark.parametrize(
    "search",
    [
        lambda q: steam.search_workshop(q),
        lambda q: steam.resolve_mod_id(q, set()),
    ],
    ids=["search_workshop", "resolve_mod_id"],
)
def test_the_query_reaches_the_url_encoded(search_page, search):
    from urllib.parse import parse_qs, urlsplit

    search(_AWKWARD)
    (url,) = search_page["urls"]
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://steamcommunity.com/workshop/browse/"
    assert parse_qs(parts.query) == {"appid": [str(steam.DAYZ_APP_ID)], "searchtext": [_AWKWARD]}
    assert parts.fragment == ""
    assert url.endswith("&searchtext=Dabs%20%26%20Co%20%232%20C%2B%2B%20mod")
