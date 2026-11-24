"""The names Expansion AI resolves, for every mod that spawns its AI (plans/64).

Dynamic AI and Expansion's own patrols both name a faction and a loadout, and
Expansion looks both up at boot: a faction among the classes the installed mods
register, a loadout in ``ExpansionMod/Loadouts``. The two mods do not resolve
them quite alike (Expansion adds ``.json`` itself and accepts ``RANDOM``;
Dynamic AI wants the file name exactly), so each lookup takes the caller's rule
as arguments instead of guessing it.

Read-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.ops.log_analysis import profiles_dir
from app.shared.platform import IS_WINDOWS

# Expansion picks a registered faction itself (one that is not invincible,
# observer, passive or special-melee). Its own default patrols use it.
RANDOM = "RANDOM"

_JSON = ".json"


def loadouts_dir(config) -> Path:
    """Expansion's loadout folder. Both mods look loadout names up here."""
    return profiles_dir(config) / "ExpansionMod" / "Loadouts"


@dataclass(frozen=True)
class Loadouts:
    """The files in the loadout folder: as on disk, and lower-cased to them."""

    names: frozenset[str]
    lower: dict[str, str]


def read_loadouts(config) -> Loadouts:
    folder = loadouts_dir(config)
    names = frozenset(p.name for p in folder.iterdir() if p.is_file()) if folder.is_dir() else frozenset()
    return Loadouts(names, {n.lower(): n for n in names})


def loadout_problem(name, loadouts: Loadouts, *, extension_optional: bool = False,
                    empty_ok: bool = False) -> str | None:
    """None when the mod will find loadout *name*; otherwise why it will not.

    *extension_optional*: the mod strips a ``.json`` and adds its own, so
    ``HumanLoadout`` and ``HumanLoadout.json`` name the same file (Expansion).
    Without it the name is the file name, exactly (Dynamic AI).
    *empty_ok*: an empty name means the faction's default loadout (Expansion).
    """
    if not isinstance(name, str) or not name:
        return None if empty_ok and name in ("", None) else "no loadout named"
    target = name
    if extension_optional:
        target = (name[: -len(_JSON)] if name.endswith(_JSON) else name) + _JSON
    if target in loadouts.names or (IS_WINDOWS and target.lower() in loadouts.lower):
        return None
    if target.lower() in loadouts.lower:
        return (f"{name} differs from {loadouts.lower[target.lower()]} only in case, "
                "and the server's filesystem is case-sensitive")
    return f"{name} is not in ExpansionMod/Loadouts"


def faction_problem(name, factions: set[str], *, random_ok: bool = False) -> str | None:
    """None when Expansion will resolve faction *name*, or when nothing is known
    (an empty scan is "unknown", never "no faction is valid"); otherwise why not.

    Expansion resolves the class ``eAIFaction<name>``, so the spelling is exact.
    *random_ok*: ``RANDOM`` picks a registered faction (every caller that goes
    through Expansion's faction lookup).
    """
    if not factions:
        return None
    if not isinstance(name, str) or not name:
        return "no faction named"
    if name in factions or (random_ok and name == RANDOM):
        return None
    lower = {f.lower(): f for f in factions}
    if name.lower() in lower:
        return f"{name} differs from {lower[name.lower()]} only in case"
    return f"{name} is not a registered faction"


def installed_factions(config) -> set[str]:
    """Every faction the active mods register (``mod_conflict.registered_factions``).

    Raises ``OSError`` like the scan does: a report that cannot read the
    install should say so, not report every faction unknown.
    """
    from app.modmgr.mod_conflict import registered_factions
    from app.modmgr import modlist

    return registered_factions(config.server_dir_path, modlist.active(config))


def install_choices(config, *, loadout_stems: bool, random: bool) -> dict[str, list[str]]:
    """The faction and loadout lists an editor's pickers offer.

    *loadout_stems*: offer ``HumanLoadout`` rather than ``HumanLoadout.json``
    (Expansion adds the extension). *random*: offer ``RANDOM`` first.
    A scan that fails gives an empty faction list, and the picker keeps
    whatever the file already has.
    """
    folder = loadouts_dir(config)
    files = sorted(p.name for p in folder.glob("*" + _JSON)) if folder.is_dir() else []
    loadouts = [f[: -len(_JSON)] for f in files] if loadout_stems else files
    try:
        factions = sorted(installed_factions(config))
    except OSError:
        factions = []
    if random and factions:
        factions = [RANDOM, *factions]
    return {"factions": factions, "loadouts": loadouts}
