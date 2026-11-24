"""Mods page help text: the tab hints and the action tooltips.

Served by GET /api/mods/help (``app.web.routers.mods.mods_help_payload``),
which also feeds the Ctrl+K search index. The web strips any console markup
through ``app.web.editors._help.strip_markup`` on the way out, so plain text is
all a new entry needs.
"""

from __future__ import annotations


ITEM_DESCRIPTIONS: dict[str, str] = {
    "Install": (
        "Downloads each mod through SteamCMD, copies it and its bikeys into the server "
        "directory, adds it to the mod list and rebuilds the launch command. With "
        "\"Install missing dependencies\" ticked, the mods it requires come along."
    ),
    "Uninstall": (
        "Removes the mod's folder and bikeys from the server directory, its entry in the mod list "
        "and the XML it added to the mission, then rebuilds the launch command. The data "
        "the mod wrote under the profiles folder (trader stock, garages and so on) is kept; "
        "Cleanup → Leftover mod data deletes it. The copy SteamCMD downloaded stays in the "
        "Workshop cache until you remove it on the Workshop Cache tab."
    ),
    "Update": (
        "Downloads the newest version from the Steam Workshop and copies it into the server "
        "directory with its bikeys. Restart the server to load it."
    ),
    "Scan for Conflicts": (
        "Checks the installed mods for: missing dependencies, missing bikeys, addons a mod "
        "requires that no installed mod provides, and mod errors in the newest server log "
        "(.RPT). Each finding says how to fix it, and most have a button that does."
    ),
    "Available Updates": (
        "Asks the Steam Workshop which installed mods have a newer version. Needs a Steam "
        "Web API key (steam_api_key in Settings). Update one mod from its row, or all of "
        "them at once."
    ),
    "Enable/Disable": (
        "A disabled mod stays installed and stays after the mod it follows in the load "
        "order, moving with that mod, so turning it back on loads it there. The "
        "server does not load it while it is off: it is left out of the launch command, the XML merge, "
        "the bikey copy and the conflict scan. Turn mods off one at a time to find the "
        "one behind a server error."
    ),
}
