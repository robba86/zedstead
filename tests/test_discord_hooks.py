"""Tests for cli.discord_hooks — embed construction and webhook logic."""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import pytest

from app.monitoring.discord_hooks import (
    _escape_discord,
    _make_embed,
    build_status_embed,
    notify_chat,
    notify_crash,
    notify_mod_updates,
    notify_mods,
    notify_player_join,
    notify_player_leave,
    notify_restart_warning,
    notify_start,
    notify_stop,
    notify_update,
    notify_wipe,
    send_or_update_status,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def cfg():
    """Minimal config mock with all hooks enabled."""
    c = MagicMock()
    c.discord_webhook_url = "https://discord.com/api/webhooks/123/token"
    c.server_name = "Test DayZ"
    c.discord_status_message_id = ""
    # Enable all hooks
    for attr in (
        "discord_hook_start", "discord_hook_stop", "discord_hook_update",
        "discord_hook_wipe", "discord_hook_mods", "discord_hook_crash",
        "discord_hook_player_join", "discord_hook_player_leave",
        "discord_hook_restart_warning", "discord_hook_mod_updates",
        "discord_hook_chat", "discord_hook_status",
    ):
        setattr(c, attr, True)
    return c


@pytest.fixture()
def sent():
    """Every request zsm sends to the webhook, as (method, decoded body)."""
    import json as _json

    bodies: list[tuple[str, dict]] = []

    def fake_urlopen(req, timeout=None):
        bodies.append((req.get_method(), _json.loads(req.data)))
        resp = MagicMock()
        resp.read.return_value = b'{"id": "42"}'
        return resp

    with patch("app.monitoring.discord_hooks.urllib.request.urlopen", fake_urlopen):
        yield bodies


# Discord's escape rule: a backslash before any punctuation character shows
# that character and nothing else.
_ESCAPE = re.compile(r"\\([^0-9A-Za-z\s])")
_SPECIAL = r"[\\*_~`|>#\-+\[\]()<@]"


def _shown(text: str) -> str:
    """What Discord displays for *text*, escapes resolved."""
    return _ESCAPE.sub(r"\1", text)


def _live(text: str) -> list[str]:
    """The markdown characters in *text* that no backslash escapes."""
    return [
        m.group(0)
        for m in re.finditer(rf"\\[^0-9A-Za-z\s]|{_SPECIAL}", text)
        if len(m.group(0)) == 1
    ]


# ---------------------------------------------------------------------------
# Embed construction
# ---------------------------------------------------------------------------


class TestMakeEmbed:
    def test_basic_embed(self):
        embed = _make_embed("Title", "Description", color=0x00FF00)
        assert embed["title"] == "Title"
        assert embed["description"] == "Description"
        assert embed["color"] == 0x00FF00
        assert "timestamp" in embed

    def test_with_fields(self):
        fields = [{"name": "K", "value": "V", "inline": True}]
        embed = _make_embed("T", fields=fields)
        assert embed["fields"] == fields

    def test_with_footer(self):
        embed = _make_embed("T", footer="foot")
        assert embed["footer"]["text"] == "foot"

    def test_no_optional_keys(self):
        embed = _make_embed("T")
        assert "description" not in embed
        assert "fields" not in embed
        assert "footer" not in embed


# ---------------------------------------------------------------------------
# Lifecycle events (embed upgrade)
# ---------------------------------------------------------------------------


class TestLifecycleEmbeds:
    @patch("app.monitoring.discord_hooks._send_embed")
    def test_notify_start(self, mock_send, cfg):
        notify_start(cfg)
        mock_send.assert_called_once()
        embed = mock_send.call_args[0][1]
        assert "Started" in embed["title"]

    @patch("app.monitoring.discord_hooks._send_embed")
    def test_notify_stop(self, mock_send, cfg):
        notify_stop(cfg)
        embed = mock_send.call_args[0][1]
        assert "Stopped" in embed["title"]

    @patch("app.monitoring.discord_hooks._send_embed")
    def test_notify_update(self, mock_send, cfg):
        notify_update(cfg, summary="3 mods updated")
        embed = mock_send.call_args[0][1]
        assert "Update" in embed["title"]
        assert "3 mods" in embed["description"]

    @patch("app.monitoring.discord_hooks._send_embed")
    def test_notify_wipe(self, mock_send, cfg):
        notify_wipe(cfg, mission="chernarusplus")
        embed = mock_send.call_args[0][1]
        assert "Wiped" in embed["title"]
        assert "chernarusplus" in embed["description"]

    @patch("app.monitoring.discord_hooks._send_embed")
    def test_notify_mods(self, mock_send, cfg):
        notify_mods(cfg, action="installed", mods=["@ModA", "@ModB"])
        embed = mock_send.call_args[0][1]
        assert "Installed" in embed["title"]
        assert "@ModA" in _shown(embed["description"])

    @patch("app.monitoring.discord_hooks._send_embed")
    def test_notify_crash(self, mock_send, cfg):
        notify_crash(cfg, details="segfault")
        embed = mock_send.call_args[0][1]
        assert "Crash" in embed["title"]
        assert "segfault" in embed["description"]

    @patch("app.monitoring.discord_hooks._send_embed")
    def test_disabled_hook_skips(self, mock_send, cfg):
        cfg.discord_hook_start = False
        notify_start(cfg)
        mock_send.assert_not_called()

    @patch("app.monitoring.discord_hooks._send_embed")
    def test_empty_url_skips(self, mock_send, cfg):
        cfg.discord_webhook_url = ""
        notify_start(cfg)
        mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# New event types
# ---------------------------------------------------------------------------


class TestNewEvents:
    @patch("app.monitoring.discord_hooks._send_embed")
    def test_player_join(self, mock_send, cfg):
        notify_player_join(cfg, "PlayerA")
        embed = mock_send.call_args[0][1]
        assert "Connected" in embed["title"]
        assert "PlayerA" in embed["description"]

    @patch("app.monitoring.discord_hooks._send_embed")
    def test_player_leave(self, mock_send, cfg):
        notify_player_leave(cfg, "PlayerB", playtime="2h 15m")
        embed = mock_send.call_args[0][1]
        assert "Disconnected" in embed["title"]
        assert "2h 15m" in embed["description"]

    @patch("app.monitoring.discord_hooks._send_embed")
    def test_restart_warning(self, mock_send, cfg):
        notify_restart_warning(cfg, 5)
        embed = mock_send.call_args[0][1]
        assert "Restart" in embed["title"]
        assert "5 minutes" in embed["description"]

    @patch("app.monitoring.discord_hooks._send_embed")
    def test_restart_warning_singular(self, mock_send, cfg):
        notify_restart_warning(cfg, 1)
        embed = mock_send.call_args[0][1]
        assert "1 minute" in embed["description"]
        assert "minutes" not in embed["description"]

    @patch("app.monitoring.discord_hooks._send_embed")
    def test_mod_updates(self, mock_send, cfg):
        outdated = [
            ("111", "TraderPlus", "3h ago"),
            ("222", "Expansion", "1d ago"),
        ]
        notify_mod_updates(cfg, outdated)
        embed = mock_send.call_args[0][1]
        assert "2 Mod Updates" in embed["title"]
        assert "TraderPlus" in embed["description"]

    @patch("app.monitoring.discord_hooks._send")
    def test_chat_relay(self, mock_send, cfg):
        notify_chat(cfg, "Player1", "Global", "Hello world")
        mock_send.assert_called_once()
        text = mock_send.call_args[0][1]
        assert "[Global]" in text
        assert "Player1" in text
        assert "Hello world" in text


# ---------------------------------------------------------------------------
# Mentions: a post never pings anyone (plans/68 P1)
# ---------------------------------------------------------------------------


class TestNoMentions:
    """Player names and chat reach the webhook verbatim apart from markdown
    escaping, and escaping is not what decides whether Discord pings. Every
    request zsm sends says outright that nothing in it may ping."""

    def test_chat_relay_pings_nobody(self, sent, cfg):
        notify_chat(cfg, "\\@everyone", "Global", "@here <@123> <@&456> \\@everyone")
        [(method, body)] = sent
        assert method == "POST"
        assert body["allowed_mentions"] == {"parse": []}
        # The text itself still arrives; only the pings are switched off.
        assert "here" in body["content"]

    def test_embeds_ping_nobody(self, sent, cfg):
        notify_player_join(cfg, "@everyone")
        [(_, body)] = sent
        assert body["allowed_mentions"] == {"parse": []}
        assert body["embeds"]

    def test_status_edits_ping_nobody(self, sent, cfg):
        cfg.discord_status_message_id = "42"
        send_or_update_status(cfg, build_status_embed("S", True))
        [(method, body)] = sent
        assert method == "PATCH"
        assert body["allowed_mentions"] == {"parse": []}


# ---------------------------------------------------------------------------
# Markdown: text zsm did not write shows as typed (plans/68 P1, FE-7)
# ---------------------------------------------------------------------------

NASTY = [
    "[Free skins](https://evil.example)",
    "\\@everyone",
    "\\*not\\* bold",
    "ends in a backslash\\",
    "# Heading",
    "-# subtext",
    "- bullet + plus * star",
    "||spoiler||",
    "<@123> <@&456> <#789> <t:0:R> <:e:1>",
    "**bold** __underline__ ~~strike~~ `code`",
    "> quote >>> block",
    "(Global) a_b",
]


class TestMarkdownEscape:
    """A player name, a chat line, a mod name or a Workshop title reaches
    Discord as text: no formatting, no masked link, nothing that ends zsm's
    own bold early. One helper does it, for every value."""

    @pytest.mark.parametrize("text", NASTY)
    def test_every_markdown_character_is_shown_as_typed(self, text):
        escaped = _escape_discord(text)
        assert _live(escaped) == []
        assert _shown(escaped) == text

    def test_a_line_break_cannot_start_a_line_of_its_own(self):
        escaped = _escape_discord("a\n# b\r\n- c")
        assert "\n" not in escaped and "\r" not in escaped
        assert _shown(escaped) == "a # b - c"

    def test_mod_names_are_escaped(self, sent, cfg):
        notify_mods(cfg, action="installed", mods=["@Mod [click](https://evil.example)"])
        [(_, body)] = sent
        desc = body["embeds"][0]["description"]
        assert "](" not in desc
        assert "- @Mod [click](https://evil.example)" in _shown(desc)

    def test_workshop_titles_are_escaped(self, sent, cfg):
        notify_mod_updates(cfg, [("1", "[Update now](https://evil.example) **x**", "1h ago")])
        [(_, body)] = sent
        desc = body["embeds"][0]["description"]
        assert "](" not in desc
        assert "[Update now](https://evil.example) **x**" in _shown(desc)
        assert desc.startswith("- **") and desc.endswith("** (updated 1h ago)")

    def test_chat_channel_name_and_message_are_escaped(self, sent, cfg):
        notify_chat(cfg, "a*b", "Gl_bal", "[x](https://evil.example)")
        [(_, body)] = sent
        content = body["content"]
        assert content.startswith("**[") and "](" not in content
        assert _shown(content) == "**[Gl_bal] a*b:** [x](https://evil.example)"

    def test_server_name_and_mission_are_escaped(self, sent, cfg):
        cfg.server_name = "EU || PvE *Trader*"
        notify_wipe(cfg, mission="my_[mission]")
        [(_, body)] = sent
        desc = body["embeds"][0]["description"]
        assert _shown(desc) == "**EU || PvE *Trader*** has been wiped. (mission: my_[mission])"
        assert desc == (
            f"**{_escape_discord('EU || PvE *Trader*')}** has been wiped."
            f" (mission: {_escape_discord('my_[mission]')})"
        )
        assert "||" not in desc and "](" not in desc

    def test_crash_details_cannot_close_their_code_block(self, sent, cfg):
        notify_crash(cfg, details="x`````y\n[a](https://evil.example)")
        [(_, body)] = sent
        desc = body["embeds"][0]["description"]
        assert desc.count("```") == 2
        assert desc.endswith("\n```")
        assert "x`````y\n[a](https://evil.example)" in desc.replace("​", "")


# ---------------------------------------------------------------------------
# Status embed
# ---------------------------------------------------------------------------


class TestStatusEmbed:
    def test_online_embed(self):
        embed = build_status_embed(
            "My Server", online=True, player_count=24, max_players=60,
            map_name="Chernarus", uptime="3h 42m", server_fps="28", mod_count=23,
        )
        assert "Online" in embed["title"]
        assert len(embed["fields"]) >= 4
        assert any(f["value"] == "24/60" for f in embed["fields"])

    def test_offline_embed(self):
        embed = build_status_embed("My Server", online=False)
        assert "Offline" in embed["title"]
        assert embed.get("fields", []) == []

    @patch("app.monitoring.discord_hooks._edit_message", return_value=True)
    def test_update_existing_message(self, mock_edit, cfg):
        cfg.discord_status_message_id = "existing_msg_id"
        embed = build_status_embed("Test", online=True, player_count=10, max_players=60)
        result = send_or_update_status(cfg, embed)
        mock_edit.assert_called_once()
        assert result == "existing_msg_id"

    @patch("app.monitoring.discord_hooks._send_embed", return_value="new_msg_id")
    def test_send_new_message(self, mock_send, cfg):
        cfg.discord_status_message_id = ""
        embed = build_status_embed("Test", online=True, player_count=10, max_players=60)
        result = send_or_update_status(cfg, embed)
        mock_send.assert_called_once()
        assert result == "new_msg_id"

    @patch("app.monitoring.discord_hooks._send_embed", return_value="fallback_id")
    @patch("app.monitoring.discord_hooks._edit_message", return_value=False)
    def test_fallback_to_new_on_edit_failure(self, mock_edit, mock_send, cfg):
        cfg.discord_status_message_id = "deleted_msg"
        embed = build_status_embed("Test", online=True, player_count=5, max_players=60)
        result = send_or_update_status(cfg, embed)
        mock_edit.assert_called_once()
        mock_send.assert_called_once()
        assert result == "fallback_id"


# ---------------------------------------------------------------------------
# A webhook URL that posts into a thread
# ---------------------------------------------------------------------------


class TestThreadWebhook:
    """Discord's way to post into a forum or channel thread is the webhook URL
    with ``?thread_id=<id>``. ``?wait=true`` was appended after it, and
    ``/messages/<id>`` after the query, so the status message never posted
    and an edit went to the webhook itself instead of the message."""

    @pytest.fixture()
    def urls(self):
        seen: list[tuple[str, str]] = []

        def fake_urlopen(req, timeout=None):
            seen.append((req.get_method(), req.full_url))
            resp = MagicMock()
            resp.read.return_value = b'{"id": "42"}'
            return resp

        with patch("app.monitoring.discord_hooks.urllib.request.urlopen", fake_urlopen):
            yield seen

    def test_a_new_status_message_keeps_the_thread(self, urls, cfg):
        cfg.discord_webhook_url = "https://discord.com/api/webhooks/123/token?thread_id=777"
        cfg.discord_status_message_id = ""
        assert send_or_update_status(cfg, build_status_embed("S", True)) == "42"
        assert urls == [("POST", "https://discord.com/api/webhooks/123/token?thread_id=777&wait=true")]

    def test_an_edit_goes_to_the_message_in_the_thread(self, urls, cfg):
        cfg.discord_webhook_url = "https://discord.com/api/webhooks/123/token?thread_id=777"
        cfg.discord_status_message_id = "42"
        send_or_update_status(cfg, build_status_embed("S", True))
        assert urls == [("PATCH", "https://discord.com/api/webhooks/123/token/messages/42?thread_id=777")]

    def test_a_plain_webhook_url_is_unchanged(self, urls, cfg):
        cfg.discord_status_message_id = ""
        send_or_update_status(cfg, build_status_embed("S", True))
        cfg.discord_status_message_id = "42"
        send_or_update_status(cfg, build_status_embed("S", True))
        assert urls == [
            ("POST", "https://discord.com/api/webhooks/123/token?wait=true"),
            ("PATCH", "https://discord.com/api/webhooks/123/token/messages/42"),
        ]
