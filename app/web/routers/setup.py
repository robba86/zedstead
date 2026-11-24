"""GET /api/setup-state — which first-run steps the server can see are done.

The Guide drawer's progress was hand-ticked and per-browser: on a box with the
server installed, mods in place and the service running it still read 0/10.
This is the read side of fixing that (TODO D2). It is an **aggregate over
facts that already exist** — every answer comes from the function some other
surface already uses to decide it, so there is no second detector to drift
away from the first.

Two rules this endpoint is built around:

1. ``steam`` is :meth:`WebState.steam_known` — the cached answer, never a
   SteamCMD run (plans/51 D; before that it went through ``steam_probe``,
   which still ran one whenever its 30 s cache had expired). Root
   ``CLAUDE.md``: two SteamCMD processes sharing one STEAMROOT corrupt the
   cached login token and the workshop manifest. An always-mounted drawer
   that re-fetches on WS events is exactly the caller that rule exists for.
2. Everything else is **one** ``asyncio.to_thread`` hop, the way
   ``collect_status`` does it: several answers touch the filesystem and
   ``service`` shells out to ``systemctl``.

``presets`` has no detector on purpose — see :func:`_detect`.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from app.shared.config import DayzConfig
from app.web.deps import get_state, require_session
from app.web.state import WebState

router = APIRouter(prefix="/api", tags=["setup"], dependencies=[Depends(require_session)])


# The map a fresh server install already runs (Bohemia's stock mission). It
# is not a pick: the Guide's map step would read done before the admin ever
# opened the Maps page. A map switch or Quick setup ticks the step in the
# browser (guide.jsx `markDone`), and someone who keeps Chernarus ticks it by hand.
_STOCK_MAP = "Chernarus"


def _step(done: bool, detail: str = "", level: str = "") -> dict:
    """One answer: is it done, and the short phrase the Guide shows beside it.

    ``level`` ("warning") asks the Guide to show the phrase as a callout rather
    than a muted line, for a step that waits on the admin's decision.
    """
    answer = {"done": bool(done), "detail": detail}
    if level:
        answer["level"] = level
    return answer


def _detect(config: DayzConfig) -> dict[str, dict]:
    """The blocking half — everything but ``steam``, in one thread.

    Imports stay function-local so importing this router stays cheap, matching
    ``status_poller.collect_status``.

    ``presets`` answers ``False`` with no detail and no probe behind it. "Has a
    preset been applied" is not a fact the filesystem carries — a preset is a
    set of config values, and nothing distinguishes values a preset wrote from
    values typed by hand a minute later. Persisting a ``last_preset_applied``
    field to make the tick appear would be a new stored fact for a cosmetic
    checkmark, wrong the moment someone edits one of those values. The hand
    tick owns this step, which is what the merge rule on the frontend is for.
    """
    from app.commands.doctor import server_is_installed
    from app.maps.map_ops import get_active_map_name
    from app.modmgr import modlist
    from app.ops.backup_ops import list_backups
    from app.server import service_control
    from app.server.scheduler import load_tasks

    installed = server_is_installed(config)
    map_name = get_active_map_name(config)
    mod_count = len(modlist.installed(config))
    # `config` is passed to both on purpose: it selects the systemd scope, and
    # the hot-path rule in root CLAUDE.md is to hand over the already-loaded
    # config rather than re-read config.json per poll.
    service = service_control.service_setup_state(config)
    running = service_control.is_server_running(config)
    # The same reads as the Backups page and the Scheduler page, so a step the
    # admin already did is not left open (the Dashboard cards counted them).
    backups = len(list_backups(config))
    tasks = len(load_tasks(config))

    return {
        "install": _step(installed, "Server installed." if installed else ""),
        "maps": _step(
            map_name is not None and map_name != _STOCK_MAP,
            f"Your server runs {map_name}, the map DayZ installs by default. "
            f"Pick another map on the Maps page, or press Mark as done to keep {map_name}."
            if map_name == _STOCK_MAP
            else map_name or "",
            "warning" if map_name == _STOCK_MAP else "",
        ),
        "mods": _step(
            mod_count > 0,
            f"{mod_count} mod{'' if mod_count == 1 else 's'} installed" if mod_count else "",
        ),
        "presets": _step(False),
        "service": _step(
            service["installed"],
            "Service installed, starts at boot."
            if service["enabled_at_boot"]
            else "Service installed." if service["installed"] else "",
        ),
        "backup": _step(backups > 0, f"{backups} quick backup{'' if backups == 1 else 's'}" if backups else ""),
        "scheduler": _step(tasks > 0, f"{tasks} scheduled task{'' if tasks == 1 else 's'}" if tasks else ""),
        "start": _step(running, "Server is running." if running else "Server is stopped."),
    }


@router.get("/setup-state")
async def get_setup_state(state: WebState = Depends(get_state)) -> dict:
    """``{"steps": {id: {done, detail}}}`` for the Guide's step ids.

    ``service`` has no Guide step of its own today; it is in the payload
    because it is the other half of the question the ``start`` step asks (a
    server that is stopped but installed as a unit is a different situation
    from one that was never set up), and it costs one ``systemctl is-enabled``
    that ``service_setup_state`` was going to make anyway.
    """
    detected = await asyncio.to_thread(_detect, state.config)
    steam_ok = state.steam_known()
    steam = _step(
        steam_ok,
        "Logged in to Steam." if steam_ok
        else "Steam login not checked yet." if steam_ok is None
        else "Not logged in to Steam.",
    )
    return {"steps": {"steam": steam, **detected}}
