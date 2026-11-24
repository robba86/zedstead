"""zedstead.com: the anonymous install ping and the public server listing (TODO F7).

Shared by the CLI (`zsm listing`) and `zsm web`'s heartbeat
(`app/web/site_heartbeat.py`). Two opt-ins (plans/57), both off until
the admin turns one on, and independent of each other:

- **The ping** (`site_ping`) counts installs: a random ID of its own, the
  zsm version, the OS and whether zsm runs in Docker, at most once a day.
- **The listing** (`site_listing`) puts this server on zedstead.com's public
  list: the install ID, the query port and an optional display host name. Off
  by default because the site's `/hardware/` page tells people to host at
  home, and an automatic listing would put home IP addresses on a public page.

**The ping never carries the listing's install ID** (plans/68 NET-16). The
listing is public and tied to the server's address, so with one shared ID the
anonymous count could be joined to a listed server. The ping's ID is made at
its first send and kept in the state file beside `last_ping`, not in
config.json, so it is not in a bundle; `forget` deletes it with that file. It
travels under the key ``install_id``, which is the name the backend reads.

**The client never reports what the backend can measure.** The listing carries
no server name, map, player count or version: the backend reads those itself
over Steam A2S, from the address the request came from. A client that could
post its own player count could post any number.

**The backend address is `ENDPOINT`, fixed in code.** No setting changes it,
so no config.json can send the listing key to another host (plans/57 C).

**`send_ping`, `publish` and `unpublish` return `(ok, message)` and never
raise.** A backend that is down must reach the admin as a sentence in
`zsm listing status`, not as a traceback in the `zsm web` console.

The secret travels in an ``Authorization: Bearer`` header and never in a body,
so a logged payload cannot leak it. `ping_payload` / `listing_payload` return
*exactly* what is sent — `zsm listing status` prints them, because an opt-in
only means something if the admin can see what they opted into — and
`tests/test_site_ops.py` pins their key sets, so a new field cannot slip
in unnoticed.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import requests

from app import __version__
from app.shared.config import DayzConfig
from app.shared.json_utils import atomic_write_text
from app.shared.platform import IS_DOCKER, IS_WINDOWS

TIMEOUT = 10
PING_INTERVAL = 24 * 3600
# How often `zsm web` refreshes the listing while the server runs. The
# backend drops a row after 24 h without one (plans/57 C).
LISTING_INTERVAL = 15 * 60
SWITCHES = ("site_ping", "site_listing")
# The zedstead.com backend. Fixed: no setting changes where the listing key goes.
ENDPOINT = "https://zedstead.com"

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# The switches
# ---------------------------------------------------------------------------

def set_switch(config: DayzConfig, field: str, on: bool) -> None:
    """Turn a switch on or off. The first turn-on of either generates the
    install ID and the secret, together; an install that never opts in has
    neither.

    The settings registry's setter and the init wizard both come through here.
    Turning the listing *off* sends nothing by itself: this runs inside
    `WebState.mutate_config` on the event loop, and `heartbeat` removes a
    listing it published once it sees the switch off.
    """
    if field not in SWITCHES:
        raise ValueError(f"{field} is not a zedstead.com switch")
    setattr(config, field, on)
    if on:
        if not config.site_install_id:
            config.site_install_id = uuid.uuid4().hex
        if not config.site_secret:
            config.site_secret = secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# What is sent — exactly this, nothing else
# ---------------------------------------------------------------------------

def ping_payload(config: DayzConfig) -> dict:
    return {
        # The ping's own ID, never `site_install_id` (plans/68 NET-16).
        # "" until the first send makes one.
        "install_id": ping_id(config),
        "version": __version__,
        "os": "windows" if IS_WINDOWS else "linux",
        "docker": IS_DOCKER,
    }


def listing_payload(config: DayzConfig) -> dict:
    from app.server.server_status import get_steam_query_port

    return {
        "install_id": config.site_install_id,
        "query_port": get_steam_query_port(config),
        "address": config.site_address or None,
    }


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def send_ping(config: DayzConfig) -> tuple[bool, str]:
    reason = _not_sendable(config, "site_ping")
    if reason:
        return False, reason
    if not _ensure_ping_id(config):
        # An ID that changed on every ping would count one install many times.
        reason = "nothing sent: the ping ID could not be saved"
        _log.warning("zedstead.com ping refused: %s", reason)
        return False, reason
    ok, message = _request("POST", _url("/v1/ping"), "ping sent", json=ping_payload(config))
    # The attempt counts, not the success: "at most once a day" holds for a
    # backend that is down too.
    _record(config, "ping", ok, message, last_ping=time.time())
    return ok, message


def publish(config: DayzConfig) -> tuple[bool, str]:
    reason = _not_sendable(config, "site_listing")
    if reason:
        return False, reason
    ok, message = _request(
        "PUT", _server_url(config), "listed",
        json=listing_payload(config), headers=_auth(config),
    )
    _record(config, "publish", ok, message, **({"last_publish": time.time()} if ok else {}))
    return ok, message


def unpublish(config: DayzConfig) -> tuple[bool, str]:
    """Take this server off the list.

    Not gated on `site_listing`, unlike the other two: the switch having
    just been turned off is exactly when this is needed. A 404 counts as done,
    since there is then nothing listed to remove.
    """
    reason = _not_sendable(config, None)
    if reason:
        return False, reason
    ok, message = _request(
        "DELETE", _server_url(config), "removed from the list",
        headers=_auth(config), also_ok=(404,),
    )
    _record(config, "unpublish", ok, message, **({"last_publish": None} if ok else {}))
    return ok, message


def forget(config: DayzConfig) -> tuple[bool, str]:
    """Remove the listing, then drop both IDs, turn both switches off and
    delete the state file, which holds the ping's ID. Returns the removal's
    result.

    The IDs go even when the removal fails: without them nothing can refresh
    the listing, and the backend drops a row after 24 h without a heartbeat.
    The caller saves the config (the web never calls this; its one config
    write is `mutate_config`).
    """
    result = unpublish(config)
    for field in SWITCHES:
        setattr(config, field, False)
    config.site_install_id = ""
    config.site_secret = ""
    try:
        state_path(config).unlink()
    except OSError:
        pass
    return result


def heartbeat(config: DayzConfig, now: float | None = None) -> None:
    """One tick of `zsm web`'s heartbeat: the ping when a day has passed,
    the listing while the server runs, and a published listing removed once
    its switch is off (which also covers a switch turned off while `zsm web`
    was not running)."""
    now = time.time() if now is None else now
    state = read_state(config)
    if config.site_ping and now - float(state.get("last_ping") or 0) >= PING_INTERVAL:
        send_ping(config)
    if config.site_listing:
        from app.server.service_control import is_server_running

        # The backend answers a server that does not reply to A2S with a 422,
        # and drops a stopped one by itself.
        if is_server_running(config):
            publish(config)
    elif state.get("last_publish"):
        unpublish(config)


def _not_sendable(config: DayzConfig, switch: str | None) -> str | None:
    """Why this request must not go out, or None when it may."""
    if switch is not None and not getattr(config, switch):
        return f"{switch} is off"
    if not (config.site_install_id and config.site_secret):
        # Only a hand-edited config.json gets here: turning a switch on
        # through the registry or the wizard creates both.
        return "no install ID yet (turning a switch on creates one)"
    return None


def _url(path: str) -> str:
    """The full backend URL of an API path."""
    return ENDPOINT + path


def _server_url(config: DayzConfig) -> str:
    """This install's own listing URL."""
    return _url("/v1/servers/" + quote(config.site_install_id, safe=""))


def _auth(config: DayzConfig) -> dict:
    return {"Authorization": f"Bearer {config.site_secret}"}


def _request(
    method: str, url: str, done: str, also_ok: tuple[int, ...] = (), **kwargs
) -> tuple[bool, str]:
    try:
        resp = requests.request(method, url, timeout=TIMEOUT, **kwargs)
    except (requests.RequestException, ValueError) as exc:
        return False, f"could not reach {url}: {exc}"
    if resp.ok or resp.status_code in also_ok:
        return True, done
    return False, _refusal(resp)


def _refusal(resp) -> str:
    """The backend writes its refusals for `zsm listing status` to show
    verbatim (plans/57 C): a JSON ``detail``/``error``/``message``, or text."""
    text = ""
    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        text = next(
            (body[key] for key in ("detail", "error", "message") if isinstance(body.get(key), str)),
            "",
        )
    text = " ".join((text or resp.text or "").split())[:300]
    return f"HTTP {resp.status_code}: {text}" if text else f"HTTP {resp.status_code}"


# ---------------------------------------------------------------------------
# State file: when each call last went out, and how the last one went
# ---------------------------------------------------------------------------

def state_path(config: DayzConfig) -> Path:
    """Per instance: the install id and listing key it tracks live in that
    instance's config.json."""
    return config.paths.site_state


def read_state(config: DayzConfig) -> dict:
    """``last_ping`` (time of the last ping sent), ``last_publish`` (time of
    the last successful listing, None once removed), ``last_result`` and
    ``ping_id`` (see `ping_id`)."""
    try:
        data = json.loads(state_path(config).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def ping_id(config: DayzConfig) -> str:
    """The ping's own random ID (32 lowercase hex, like the backend expects),
    or "" before the first ping makes one."""
    value = read_state(config).get("ping_id")
    if isinstance(value, str) and len(value) == 32 and all(c in "0123456789abcdef" for c in value):
        return value
    return ""


def _ensure_ping_id(config: DayzConfig) -> str:
    """`ping_id`, made and saved the first time. "" when it cannot be saved."""
    current = ping_id(config)
    if current:
        return current
    state = read_state(config)
    state["ping_id"] = uuid.uuid4().hex
    try:
        _write_state(config, state)
    except OSError:
        return ""
    return state["ping_id"]


def _record(config: DayzConfig, action: str, ok: bool, message: str, **fields) -> None:
    state = read_state(config)
    state.update(fields)
    state["last_result"] = {"action": action, "ok": ok, "message": message, "at": time.time()}
    try:
        _write_state(config, state)
    except OSError:
        # Costs the status line, never the call.
        pass


def _write_state(config: DayzConfig, state: dict) -> None:
    atomic_write_text(state_path(config), json.dumps(state, indent=2) + "\n", owner_only=True)
