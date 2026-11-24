"""Discord webhook notifications for server events.

Supports both plain text (legacy) and rich embeds. Message editing is used
for the auto-updating status embed feature.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.shared.config import DayzConfig

log = logging.getLogger(__name__)

# Embed colours (Discord expects decimal, not hex)
COLOR_GREEN = 0x2ECC71
COLOR_RED = 0xE74C3C
COLOR_ORANGE = 0xF39C12
COLOR_BLUE = 0x3498DB
COLOR_GRAY = 0x95A5A6
COLOR_PURPLE = 0x9B59B6


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

# Nothing zsm posts may ping anyone. Player names and chat go out verbatim
# apart from markdown escaping, and Discord does not decide whether to ping
# from the markdown: only this field does. Mentions still render, silently.
NO_MENTIONS: dict[str, list] = {"parse": []}


def _discord_request(
    url: str,
    payload: dict[str, Any],
    method: str = "POST",
) -> bytes | None:
    """Send a request to a Discord webhook. Returns response body or None.

    Every request goes through here, so every one carries `NO_MENTIONS`.
    """
    if not url:
        return None
    if not url.startswith("https://"):
        log.debug("Discord webhook URL must use HTTPS: %s", url[:50])
        return None
    data = json.dumps({**payload, "allowed_mentions": NO_MENTIONS}).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "User-Agent": "zsm"},
        method=method,
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.read()
    except Exception as exc:
        log.debug("Discord webhook failed: %s", exc)
        return None


def _webhook_url(webhook_url: str, path: str = "", query: dict[str, str] | None = None) -> str:
    """*webhook_url* with *path* added to its path and *query* to its query.

    A webhook that posts into a thread is the URL with ``?thread_id=<id>``,
    so neither can be glued onto the end of the string.
    """
    parts = urllib.parse.urlsplit(webhook_url)
    params = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    params += list((query or {}).items())
    return urllib.parse.urlunsplit(
        parts._replace(path=parts.path + path, query=urllib.parse.urlencode(params))
    )


def _send(webhook_url: str, content: str) -> bool:
    """Send a simple text message to a Discord webhook. Returns True on success."""
    if not webhook_url:
        return False
    return _discord_request(webhook_url, {"content": content}) is not None


def _send_embed(
    webhook_url: str,
    embed: dict[str, Any],
    username: str = "DayZ Server",
    wait: bool = False,
) -> str | None:
    """Send a Discord webhook with an embed.

    Returns the message ID if ``wait=True`` and the send succeeds.
    """
    if not webhook_url:
        return None
    url = _webhook_url(webhook_url, query={"wait": "true"} if wait else None)
    payload: dict[str, Any] = {"username": username, "embeds": [embed]}
    body = _discord_request(url, payload)
    if wait and body:
        try:
            return json.loads(body).get("id")
        except (json.JSONDecodeError, AttributeError):
            pass
    return None


def _edit_message(
    webhook_url: str, message_id: str, embed: dict[str, Any]
) -> bool:
    """Edit a previously sent webhook message. Returns True on success."""
    if not webhook_url or not message_id:
        return False
    url = _webhook_url(webhook_url, f"/messages/{message_id}")
    return _discord_request(url, {"embeds": [embed]}, method="PATCH") is not None


def _is_enabled(config: DayzConfig, hook: str) -> bool:
    """Check if a specific hook is enabled and webhook URL is set."""
    if not config.discord_webhook_url:
        return False
    return getattr(config, f"discord_hook_{hook}", False)


# Every character Discord's markdown gives a meaning to: emphasis and
# underline (* _), strikethrough (~), code (`), spoilers (|), quotes (>),
# headings and subtext at a line start (# -#), list bullets (- * +), masked
# links ([text](url)), mentions, channels, emoji and timestamps (<...>, @).
# The backslash itself comes first in the class only for readability: one
# pass over the text escapes each character once, so an escaping backslash
# in the input is escaped too and cannot cancel the escape after it.
# Discord shows a backslash before any punctuation as that character alone,
# so escaping one too many costs nothing.
_MARKDOWN = re.compile(r"[\\*_~`|>#\-+\[\]()<@]")


def _escape_discord(text: object) -> str:
    """*text* as Discord shows it typed: no formatting, links or mentions.

    The one escape for every name, title and message zsm did not write
    (players, chat, mods, Workshop titles, missions). Line breaks become
    spaces: such a value is one line in the post, and a break would let it
    start a line of its own.
    """
    flat = " ".join(str(text).splitlines())
    return _MARKDOWN.sub(lambda m: "\\" + m.group(0), flat)


def _code_block(text: str) -> str:
    """*text* in a code block it cannot close. Markdown is not read inside
    one, so nothing is escaped; a zero-width space after every backtick
    keeps any three of them from forming the closing fence."""
    return "```\n" + text.replace("`", "`\u200b") + "\n```"


def _server_label(config: DayzConfig) -> str:
    """The server's name, escaped: every caller puts it in a description."""
    return _escape_discord(config.server_name or "DayZ Server")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M UTC")


def _make_embed(
    title: str,
    description: str = "",
    color: int = COLOR_GRAY,
    fields: list[dict] | None = None,
    footer: str = "",
) -> dict[str, Any]:
    """Build a Discord embed dict."""
    embed: dict[str, Any] = {
        "title": title,
        "color": color,
        "timestamp": _now_iso(),
    }
    if description:
        embed["description"] = description
    if fields:
        embed["fields"] = fields
    if footer:
        embed["footer"] = {"text": footer}
    return embed


# ---------------------------------------------------------------------------
# Server lifecycle events (upgraded to embeds)
# ---------------------------------------------------------------------------


def notify_start(config: DayzConfig) -> None:
    if not _is_enabled(config, "start"):
        return
    embed = _make_embed(
        "Server Started",
        f"**{_server_label(config)}** is now online.",
        color=COLOR_GREEN,
    )
    _send_embed(config.discord_webhook_url, embed)


def notify_stop(config: DayzConfig) -> None:
    if not _is_enabled(config, "stop"):
        return
    embed = _make_embed(
        "Server Stopped",
        f"**{_server_label(config)}** is now offline.",
        color=COLOR_RED,
    )
    _send_embed(config.discord_webhook_url, embed)


def notify_update(config: DayzConfig, *, summary: str = "") -> None:
    if not _is_enabled(config, "update"):
        return
    desc = f"**{_server_label(config)}** — Update pipeline complete."
    if summary:
        desc += f"\n{summary}"
    embed = _make_embed("Update Complete", desc, color=COLOR_BLUE)
    _send_embed(config.discord_webhook_url, embed)


def notify_wipe(config: DayzConfig, *, mission: str = "") -> None:
    if not _is_enabled(config, "wipe"):
        return
    desc = f"**{_server_label(config)}** has been wiped."
    if mission:
        desc += f" (mission: {_escape_discord(mission)})"
    embed = _make_embed("Server Wiped", desc, color=COLOR_ORANGE)
    _send_embed(config.discord_webhook_url, embed)


def notify_mods(config: DayzConfig, *, action: str, mods: list[str]) -> None:
    if not _is_enabled(config, "mods") or not mods:
        return
    label = "\n".join(f"- {_escape_discord(m)}" for m in mods[:10])
    if len(mods) > 10:
        label += f"\n- (+{len(mods) - 10} more)"
    embed = _make_embed(
        f"Mods {action.capitalize()}",
        f"**{_server_label(config)}**\n{label}",
        color=COLOR_PURPLE,
    )
    _send_embed(config.discord_webhook_url, embed)


def notify_crash(config: DayzConfig, *, details: str = "") -> None:
    if not _is_enabled(config, "crash"):
        return
    desc = f"**{_server_label(config)}** — Crash detected!"
    if details:
        desc += "\n" + _code_block(details[:500])
    embed = _make_embed("Crash Detected", desc, color=COLOR_RED)
    _send_embed(config.discord_webhook_url, embed)


def notify_boot_failed(
    config: DayzConfig,
    *,
    occasion: str,
    detail: str,
    suspects: list[str],
    lines: list[str],
) -> None:
    """The server did not come back after an update (plans/73). One message
    per failed boot; mod names and log lines are not zsm's text."""
    if not _is_enabled(config, "crash"):
        return
    desc = f"**{_server_label(config)}** did not come back {_escape_discord(occasion)}."
    if detail:
        desc += "\n" + _escape_discord(detail)
    if suspects:
        names = ", ".join(f"**{_escape_discord(m)}**" for m in suspects[:10])
        more = f" (+{len(suspects) - 10} more)" if len(suspects) > 10 else ""
        desc += f"\nSuspects: {names}{more}. Set them aside from the Update page or with `zsm mods triage`."
    if lines:
        desc += "\n" + _code_block("\n".join(lines)[:1200])
    embed = _make_embed("Server did not come back after an update", desc, color=COLOR_RED)
    _send_embed(config.discord_webhook_url, embed)


def notify_world_damage(config: DayzConfig, *, occasion: str, items: int, backup: str) -> None:
    """The server came back after an update but could not read many saved
    items (release item 24). One message per boot."""
    if not _is_enabled(config, "crash"):
        return
    desc = (
        f"**{_server_label(config)}** came back {_escape_discord(occasion)}, but could not "
        f"read {items} saved items (bases, vehicles, gear). They leave the world at its next save."
    )
    if backup:
        desc += f"\nThe newest backup to restore: {_escape_discord(backup)}. See the Update page."
    embed = _make_embed("Saved items lost after an update", desc, color=COLOR_ORANGE)
    _send_embed(config.discord_webhook_url, embed)


# ---------------------------------------------------------------------------
# New: Player events
# ---------------------------------------------------------------------------


def notify_player_join(config: DayzConfig, name: str, guid: str = "") -> None:
    """Player connected notification."""
    if not _is_enabled(config, "player_join"):
        return
    embed = _make_embed(
        "Player Connected",
        f"**{_escape_discord(name)}** joined the server",
        color=COLOR_GREEN,
    )
    _send_embed(config.discord_webhook_url, embed)


def notify_player_leave(
    config: DayzConfig, name: str, guid: str = "", playtime: str = ""
) -> None:
    """Player disconnected notification."""
    if not _is_enabled(config, "player_leave"):
        return
    desc = f"**{_escape_discord(name)}** left the server"
    if playtime:
        desc += f" (played {playtime})"
    embed = _make_embed("Player Disconnected", desc, color=COLOR_RED)
    _send_embed(config.discord_webhook_url, embed)


# ---------------------------------------------------------------------------
# New: Restart warnings
# ---------------------------------------------------------------------------


def notify_restart_warning(config: DayzConfig, minutes: int) -> None:
    """Restart countdown warning."""
    if not _is_enabled(config, "restart_warning"):
        return
    plural = "s" if minutes != 1 else ""
    embed = _make_embed(
        "Restart Warning",
        f"Server restarting in **{minutes} minute{plural}**",
        color=COLOR_ORANGE,
    )
    _send_embed(config.discord_webhook_url, embed)


# ---------------------------------------------------------------------------
# New: Mod updates available
# ---------------------------------------------------------------------------


def notify_mod_updates(
    config: DayzConfig,
    outdated: list[tuple[str, str, str]],
) -> None:
    """Mod updates available notification.

    ``outdated`` is a list of ``(mod_id, title, age_display)`` tuples.
    """
    if not _is_enabled(config, "mod_updates"):
        return
    lines = [f"- **{_escape_discord(title)}** (updated {age})" for _, title, age in outdated[:10]]
    if len(outdated) > 10:
        lines.append(f"- (+{len(outdated) - 10} more)")
    count = len(outdated)
    embed = _make_embed(
        f"{count} Mod Update{'s' if count != 1 else ''} Available",
        "\n".join(lines),
        color=COLOR_BLUE,
    )
    _send_embed(config.discord_webhook_url, embed)


def notify_triage_updated(config: DayzConfig, lines: list[str]) -> None:
    """Mods set aside after an update have updated since (plans/73 D). An
    offer only: zsm never enables them by itself."""
    if not _is_enabled(config, "mod_updates") or not lines:
        return
    body = "\n".join(f"- {_escape_discord(line)}" for line in lines[:10])
    if len(lines) > 10:
        body += f"\n- (+{len(lines) - 10} more)"
    embed = _make_embed(
        "Set-aside mods have updated",
        f"**{_server_label(config)}**\n{body}\nEnable them on the Mods page or with `zsm mods enable`.",
        color=COLOR_BLUE,
    )
    _send_embed(config.discord_webhook_url, embed)


# ---------------------------------------------------------------------------
# New: Chat relay
# ---------------------------------------------------------------------------


def notify_chat(
    config: DayzConfig, name: str, channel: str, message: str
) -> None:
    """In-game chat message relay."""
    if not _is_enabled(config, "chat"):
        return
    _send(
        config.discord_webhook_url,
        f"**[{_escape_discord(channel)}] {_escape_discord(name)}:** {_escape_discord(message)}",
    )


# ---------------------------------------------------------------------------
# New: Auto-updating status embed
# ---------------------------------------------------------------------------


def build_status_embed(
    server_name: str,
    online: bool,
    player_count: int = 0,
    max_players: int = 0,
    map_name: str = "",
    uptime: str = "",
    next_restart: str = "",
    server_fps: str = "",
    mod_count: int = 0,
) -> dict[str, Any]:
    """Build a status embed dict for the auto-updating server status message."""
    status_text = "Online" if online else "Offline"
    color = COLOR_GREEN if online else COLOR_RED

    fields = []
    if online:
        fields.append({"name": "Players", "value": f"{player_count}/{max_players}", "inline": True})
        if map_name:
            fields.append({"name": "Map", "value": map_name, "inline": True})
        if uptime:
            fields.append({"name": "Uptime", "value": uptime, "inline": True})
        if next_restart:
            fields.append({"name": "Next Restart", "value": next_restart, "inline": True})
        if server_fps:
            fields.append({"name": "Server FPS", "value": server_fps, "inline": True})
        if mod_count:
            fields.append({"name": "Mods", "value": str(mod_count), "inline": True})

    return _make_embed(
        f"{status_text} — {server_name}",
        "",
        color=color,
        fields=fields,
        footer="Auto-updating status",
    )


def send_or_update_status(
    config: DayzConfig, embed: dict[str, Any]
) -> str | None:
    """Send a new status embed or edit the existing one.

    Returns the message ID (for storing in config).
    """
    if not config.discord_webhook_url:
        return None

    msg_id = getattr(config, "discord_status_message_id", "")
    if msg_id:
        if _edit_message(config.discord_webhook_url, msg_id, embed):
            return msg_id
        # Edit failed (message deleted?) — fall through to send new

    new_id = _send_embed(
        config.discord_webhook_url, embed, wait=True
    )
    return new_id
