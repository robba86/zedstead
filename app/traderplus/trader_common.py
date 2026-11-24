"""Shared utilities for TraderPlus editor."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from app.shared.config import DayzConfig
from app.modmgr import modlist


# ---------------------------------------------------------------------------
# Reading a list out of a TraderPlus JSON file
# ---------------------------------------------------------------------------

_KINDS = {str: "text", bool: "true/false", int: "a number", float: "a number", dict: "an object"}


def json_list(data: dict, key: str, default: list | None = None) -> list:
    """The list *data* holds under *key*, as a new list.

    A missing key or ``null`` is *default* (empty unless given). Anything else
    that is not a list raises ValueError: ``list("Coins")`` used to turn text
    the file held where TraderPlus expects a list into one entry per letter,
    which the next save wrote back. The parsers add the file's name to the
    message, and the editors answer it with a 400 that points at the Raw tab.
    """
    value = data.get(key) if isinstance(data, dict) else None
    if value is None:
        return list(default) if default is not None else []
    if isinstance(value, list):
        return list(value)
    kind = _KINDS.get(type(value), type(value).__name__)
    raise ValueError(f'"{key}" holds {kind} where TraderPlus expects a list')


def over_raw(raw: dict, known: dict, read: dict | None = None) -> dict:
    """*known* written over a copy of *raw*, the entry as the file had it.

    An entry's ``to_dict`` goes through here, so a key the editor does not
    know (one a later TraderPlus adds, or the admin's own) stays, and the
    file's key order stays: rebuilt from the known keys alone, an entry lost
    the rest on every save, and a reordered one was written out afresh.

    *read* is *known* as the parser read it from *raw*. A key *raw* lacks is
    then added only when its value differs from that reading, the default the
    parser filled in, so a save with no edits adds no key. Without *read* (an
    entry the editor created) every known key is written.
    """
    out = dict(raw)
    for key, value in known.items():
        if key in raw or read is None or value != read.get(key):
            out[key] = value
    return out


def entry_over_raw(entry: Any) -> dict:
    """``entry.known()`` over ``entry.raw`` (:func:`over_raw`), compared with
    the same entry read afresh from its raw."""
    read = type(entry).from_dict(entry.raw).known() if entry.raw else None
    return over_raw(entry.raw, entry.known(), read)


def refuse_in(path: Path, exc: ValueError, where: str = "") -> ValueError:
    """*exc* from :func:`json_list` as the sentence an editor shows."""
    at = f"{path.name}{', ' + where if where else ''}"
    return ValueError(f"{at}: {exc}. Correct it on the Raw tab.")


# ---------------------------------------------------------------------------
# Mod detection
# ---------------------------------------------------------------------------

def is_mod_active(config: DayzConfig, mod_names: list[str]) -> bool:
    """Check if any of the given mod names are active (enabled) in the mod list."""
    mods = modlist.active(config)
    # Mod-list names are bare ("@TraderPlus"); the split on "=" leaves them as they are.
    lower_names = {n.lower() for n in mod_names}
    for m in mods:
        name_part = m.split("=")[0].strip().lower()
        if name_part in lower_names:
            return True
    return False


def has_traderplus_config(config: DayzConfig) -> bool:
    """Check if TraderPlus price config file exists."""
    path = get_traderplus_config_dir(config)
    return (path / "TraderPlusPriceConfig.json").is_file() if path else False


def has_traderplus_general_config(config: DayzConfig) -> bool:
    """Check if TraderPlus general config file exists."""
    path = get_traderplus_config_dir(config)
    return (path / "TraderPlusGeneralConfig.json").is_file() if path else False


def has_traderplus_banking_config(config: DayzConfig) -> bool:
    """Check if TraderPlus banking config file exists."""
    path = get_traderplus_config_dir(config)
    return (path / "TraderPlusBankingConfig.json").is_file() if path else False


def has_traderplus_ids_config(config: DayzConfig) -> bool:
    """Check if TraderPlus IDs config file exists."""
    path = get_traderplus_config_dir(config)
    return (path / "TraderPlusIDsConfig.json").is_file() if path else False


def has_traderplus_insurance_config(config: DayzConfig) -> bool:
    """Check if TraderPlus insurance config file exists."""
    path = get_traderplus_config_dir(config)
    return (path / "TraderPlusInsuranceConfig.json").is_file() if path else False


def has_traderplus_vehicles_config(config: DayzConfig) -> bool:
    """Check if TraderPlus vehicles config file exists."""
    path = get_traderplus_config_dir(config)
    return (path / "TraderPlusVehiclesConfig.json").is_file() if path else False


def has_traderplus_safezone_config(config: DayzConfig) -> bool:
    """Check if TraderPlus safe zone config file exists."""
    path = get_traderplus_config_dir(config)
    return (path / "TraderPlusSafeZoneConfig.json").is_file() if path else False


def has_traderplus_garage_config(config: DayzConfig) -> bool:
    """Check if TraderPlus garage config file exists."""
    path = get_traderplus_config_dir(config)
    return (path / "TraderPlusGarageConfig.json").is_file() if path else False


def has_traderplus_types_xml(config: DayzConfig) -> bool:
    """Check if TraderPlus types XML file exists and is registered in cfgeconomycore."""
    from app.xml.xml_utils import get_mission_dir
    from app.xml.economycore_xml import get_economycore_path, parse_economycore_file

    mission_dir = get_mission_dir(config)
    if not mission_dir:
        return False

    types_file = mission_dir / "custom" / "traderplus-TraderPlus_types.xml"
    if not types_file.is_file():
        return False

    econ_path = get_economycore_path(mission_dir)
    if not econ_path:
        return False

    try:
        ef = parse_economycore_file(econ_path)
        for rc in ef.rootclasses:
            if rc.folder == "custom":
                for f in rc.files:
                    if f.name == "traderplus-TraderPlus_types.xml" and f.type == "types":
                        return True
    except (OSError, ET.ParseError):
        return False

    return False


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

# The one folder of profiles/TraderPlus that holds configuration. Its siblings
# are player data (TraderPlusBankDatabase, TraderPlusGarageDatabase, the
# TraderPlusDatabase stock: backup_ops._MOD_PROFILE_DIRS) and the logs.
TRADERPLUS_CONFIG_DIR = "TraderPlusConfig"
# Where the mod ships its default configs, inside @TraderPlus.
_MOD_CONFIG_SOURCE = Path("ServerProfile", "TraderPlus", TRADERPLUS_CONFIG_DIR)


def _profiles_dir(config: DayzConfig) -> Path:
    """Return the resolved profiles directory."""
    return config.server_dir_path / config.profiles_path


def _profile_config_dir(config: DayzConfig) -> Path:
    """``profiles/TraderPlus/TraderPlusConfig``, whether it exists or not."""
    return _profiles_dir(config) / "TraderPlus" / TRADERPLUS_CONFIG_DIR


def get_traderplus_config_dir(config: DayzConfig) -> Path | None:
    """Return TraderPlus config dir if it exists."""
    d = _profile_config_dir(config)
    return d if d.is_dir() else None


# ---------------------------------------------------------------------------
# Reset operations
# ---------------------------------------------------------------------------

def _traderplus_mod_dir(config: DayzConfig) -> Path:
    """Return the @TraderPlus mod directory in the server dir."""
    return config.server_dir_path / "@TraderPlus"


def has_traderplus_json_source(config: DayzConfig) -> bool:
    """Check if the @TraderPlus mod has default JSON config to copy from."""
    return (_traderplus_mod_dir(config) / _MOD_CONFIG_SOURCE).is_dir()


def has_traderplus_types_source(config: DayzConfig) -> bool:
    """Check if the installed @TraderPlus ships a TraderPlus_types.xml to merge.

    The Workshop update of 2026-11-09 dropped the mod's Centraleconomy folder,
    so a current copy has none, and the custom/ types file is then absent on
    purpose. Matched by file name in any case, anywhere in the mod folder.
    """
    mod_dir = _traderplus_mod_dir(config)
    if not mod_dir.is_dir():
        return False
    return any(p.name.lower() == "traderplus_types.xml" for p in mod_dir.rglob("*.xml"))


def _default_config_files(config: DayzConfig) -> list[Path] | None:
    """The config part of the mod's defaults: the files of its
    ``ServerProfile/TraderPlus/TraderPlusConfig``, None when it has none.

    The install offer and Reset TraderPlus JSON both copy exactly these, so
    the two cannot drift. Never the rest of ``ServerProfile/TraderPlus``: it
    holds the mod author's sample bank account and garage (a real Steam ID)
    and empty database and log folders, which TraderPlus makes itself
    (``MakeDirectory`` in its PBOs).
    """
    src = _traderplus_mod_dir(config) / _MOD_CONFIG_SOURCE
    if not src.is_dir():
        return None
    return sorted(p for p in src.iterdir() if p.is_file())


#: The manual XML file (`xml_manual`) that holds the GitHub copy of
#: TraderPlus_types.xml when the installed mod ships none. A manual file, not
#: a custom/ copy, since every merge wipes custom/ and re-copies only from mods.
UPSTREAM_TYPES_FILE_ID = "traderplus-types"


def reset_sources(config: DayzConfig) -> dict:
    """Where the Cleanup page's two TraderPlus reset buttons copy from.

    ``json`` and ``xml`` are ``"mod"`` when the installed ``@TraderPlus``
    ships the defaults, ``"github"`` when it does not and the reset downloads
    them from `upstream_defaults.UPSTREAM_URL`, and None when the mod is not
    installed. Reads only the disk: the overview is a GET and never downloads.
    """
    from app.traderplus.upstream_defaults import UPSTREAM_URL

    installed = _traderplus_mod_dir(config).is_dir()

    def _from(in_mod: bool) -> str | None:
        """The source name for one button."""
        if not installed:
            return None
        return "mod" if in_mod else "github"

    return {
        "installed": installed,
        "json": _from(has_traderplus_json_source(config)),
        "xml": _from(has_traderplus_types_source(config)),
        "upstream_url": UPSTREAM_URL,
    }


def has_traderplus_xml_source(config: DayzConfig) -> bool:
    """Check if the @TraderPlus mod directory exists (contains XMLs to copy)."""
    return _traderplus_mod_dir(config).is_dir()


def _install_config_files(config: DayzConfig, *, overwrite: bool) -> tuple[str, list[str]]:
    """Copy the default ``TraderPlusConfig`` files into the profiles folder.

    The one copy behind the install offer and Reset TraderPlus JSON. The
    files come from the installed mod (`_default_config_files`) or, when it
    ships none, from the author's GitHub repository (`upstream_defaults`).
    Without *overwrite*, an existing ``TraderPlusConfig`` is left alone. With
    it, each file replaced is kept as a version first, and a file the
    defaults do not include stays.

    Returns ``(status, log lines)``: status ``"copied"``, ``"exists"`` or
    ``"no_source"`` (mod not installed, or its config folder is empty). Raises `UpstreamError` when the
    download fails.
    """
    import tempfile

    from app.shared.config_versions import snapshot
    from app.shared.json_utils import copy_file
    from app.traderplus import upstream_defaults

    if not _traderplus_mod_dir(config).is_dir():
        return "no_source", ["@TraderPlus mod folder not found"]
    dest = _profile_config_dir(config)
    if dest.is_dir() and not overwrite:
        return "exists", []

    msgs: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        defaults = _default_config_files(config)
        if defaults is None:
            defaults = upstream_defaults.fetch_config_files(Path(tmp))
            msgs.append(
                f"This @TraderPlus version ships no default configs, so Zedstead "
                f"downloaded {len(defaults)} from {upstream_defaults.UPSTREAM_URL}"
            )
        if not defaults:
            return "no_source", [f"@TraderPlus/{_MOD_CONFIG_SOURCE.as_posix()} is empty"]
        for default in defaults:  # every version first, so a failure writes nothing
            snapshot(dest / default.name, trigger="pre_restore", note="before Reset TraderPlus JSON")
        dest.mkdir(parents=True, exist_ok=True)
        for default in defaults:
            copy_file(default, dest / default.name)
        json_count = sum(1 for p in defaults if p.suffix == ".json")
    msgs.append(
        f"Copied {json_count} default JSON file(s) into "
        f"{dest.relative_to(config.server_dir_path)}"
    )
    return "copied", msgs


def copy_traderplus_default_config(config: DayzConfig) -> str:
    """Copy the mod's default TraderPlus config into the profiles folder.

    Non-destructive: unlike ``reset_traderplus_json`` this never overwrites an
    existing config. Used as the post-install "set up default configs?" offer
    in the web install job.

    Copies the config part only (``_default_config_files``) into
    ``profiles/TraderPlus/TraderPlusConfig``. It used to copy the mod's
    whole ``ServerProfile/TraderPlus``, which planted the mod author's sample
    bank account and garage, a real Steam ID, on a fresh server. A mod that
    ships no defaults gets them from GitHub (`_install_config_files`).

    Returns ``"copied"``, ``"exists"`` (``TraderPlusConfig`` already
    present), ``"no_source"`` (mod not installed) or ``"failed"`` (the
    GitHub download failed).
    """
    from app.traderplus.upstream_defaults import UpstreamError

    try:
        return _install_config_files(config, overwrite=False)[0]
    except UpstreamError:
        return "failed"


def reset_traderplus_json(config: DayzConfig) -> tuple[bool, list[str]]:
    """Replace the TraderPlus config files with the mod's defaults.

    **Only ``TraderPlusConfig/``.** This used to delete the whole
    ``profiles/TraderPlus`` and copy the mod's ``ServerProfile/TraderPlus``
    in its place, so every player's bank account, garage vehicles and the
    traders' stock went, with no backup, and the mod author's sample bank
    account and garage arrived instead. The
    databases and the logs are never touched now, and each config file it
    replaces is kept as a version first (Analysis > Change history). A file in
    ``TraderPlusConfig`` the mod does not ship is left where it is. A mod
    that ships no defaults gets them from GitHub (`_install_config_files`).

    Returns (success, log_messages).
    """
    from app.traderplus.upstream_defaults import UpstreamError

    try:
        status, msgs = _install_config_files(config, overwrite=True)
    except UpstreamError as e:
        return False, [str(e)]
    if status != "copied":
        return False, msgs
    return True, msgs + ["Kept bank accounts, garages, trader stock and logs"]


def _save_upstream_types(config: DayzConfig) -> tuple[bool, list[str]]:
    """Download GitHub's TraderPlus_types.xml into the manual XML file
    `UPSTREAM_TYPES_FILE_ID` (created, or replaced after a version is kept).
    Does not apply it to the mission. Returns (success, log_messages)."""
    from app.shared.config_versions import snapshot
    from app.traderplus import upstream_defaults
    from app.xml import xml_manual

    try:
        content = upstream_defaults.fetch_types_xml()
    except upstream_defaults.UpstreamError as e:
        return False, [str(e)]

    manual_dir = config.paths.custom_xml_dir
    file_id = UPSTREAM_TYPES_FILE_ID
    note = f"TraderPlus_types.xml from {upstream_defaults.UPSTREAM_URL}"
    try:
        if xml_manual.get_file(file_id, manual_dir=manual_dir):
            snapshot(
                xml_manual.file_content_path(file_id, manual_dir=manual_dir),
                trigger="pre_restore", note="before Reset TraderPlus XML",
            )
            xml_manual.update_file(
                file_id, manual_dir=manual_dir, content=content,
                kind="types", mod="@TraderPlus", enabled=True, note=note,
            )
            xml_manual.clear_baseline(file_id, manual_dir=manual_dir)
        else:
            xml_manual.create_file(
                file_id, "types", content, manual_dir=manual_dir, mod="@TraderPlus", note=note,
            )
    except xml_manual.ManualFileError as e:
        return False, [f"The downloaded TraderPlus_types.xml was refused: {e}"]
    return True, [
        f"This @TraderPlus version ships no types XML, so Zedstead downloaded "
        f"TraderPlus_types.xml from {upstream_defaults.UPSTREAM_URL}",
        f"Saved it as the manual XML file {file_id}",
    ]


def _reset_types_from_upstream(config: DayzConfig, mission_dir: Path) -> tuple[bool, list[str]]:
    """Put GitHub's TraderPlus_types.xml into the mission as a manual XML file.

    For a mod that ships no types XML. The file of record is the manual file
    `UPSTREAM_TYPES_FILE_ID` (`_save_upstream_types`), applied at once and
    re-applied by every merge after.
    """
    from app.xml import xml_manual

    ok, msgs = _save_upstream_types(config)
    if not ok:
        return False, msgs
    xml_manual.apply_manual_file(
        config.server_dir_path, mission_dir.name, UPSTREAM_TYPES_FILE_ID,
        manual_dir=config.paths.custom_xml_dir,
    )
    return True, msgs + ["Applied it to the mission"]


def ensure_upstream_defaults(config: DayzConfig, log=None) -> bool:
    """Fill in what an enabled @TraderPlus no longer ships, before a merge.

    The Workshop update of 2026-11-09 dropped the mod's default JSON and its
    TraderPlus_types.xml. Every merge (Re-configure all, `zsm run`, install,
    the row's Re-configure) runs this, so the GitHub fallback is not reached
    only through the Cleanup reset buttons. It never overwrites: the JSON is
    copied only when ``TraderPlusConfig`` is missing, and the types XML is
    downloaded only when the mod ships none and no merge downloaded it before
    (`paths.traderplus_types_fetched`), so a file the admin deleted stays
    deleted. Reset TraderPlus XML on the Cleanup page brings it back. A failed
    download is logged and the merge goes on.

    Returns True when it created the manual types file, which the caller's
    merge must then apply.
    """
    from app.traderplus.upstream_defaults import UpstreamError
    from app.xml import xml_manual

    emit = log or (lambda _msg: None)
    if not _traderplus_mod_dir(config).is_dir() or not is_mod_active(config, ["@TraderPlus"]):
        return False

    try:
        status, msgs = _install_config_files(config, overwrite=False)
        if status == "copied":
            for msg in msgs:
                emit(msg)
    except UpstreamError as e:
        emit(f"Warning: TraderPlus default JSON could not be downloaded: {e}")

    from app.shared.json_utils import atomic_write_text

    manual_dir = config.paths.custom_xml_dir
    fetched = config.paths.traderplus_types_fetched
    if (
        has_traderplus_types_source(config)
        or fetched.is_file()
        or xml_manual.get_file(UPSTREAM_TYPES_FILE_ID, manual_dir=manual_dir)
    ):
        return False
    ok, msgs = _save_upstream_types(config)
    for msg in msgs:
        emit(msg if ok else f"Warning: TraderPlus types XML: {msg}")
    if ok:
        atomic_write_text(fetched, f"{UPSTREAM_TYPES_FILE_ID}\n")
    return ok


def reset_traderplus_xml(config: DayzConfig) -> tuple[bool, list[str]]:
    """Remove TraderPlus custom XMLs and copy fresh from the mod.

    Delegates to `merge_mod_economy_entries`, the same scoped re-link the Mods
    page's per-mod "Re-configure" runs: each XML is wrapped with the right root
    tag (types/events/spawnabletypes) and registered in cfgeconomycore.xml with
    the matching `type=`, while every other mod's entries are read back off disk
    and re-registered alongside it. A mod that ships no types XML gets it from
    GitHub as a manual XML file (`_reset_types_from_upstream`).

    Returns (success, log_messages).
    """
    from app.modmgr.mods import kebab_case
    from app.xml.xml_economy import merge_mod_economy_entries
    from app.xml.xml_utils import get_mission_dir

    msgs: list[str] = []

    mission_dir = get_mission_dir(config)
    if not mission_dir:
        return False, ["Could not resolve mission directory"]

    mod_dir = _traderplus_mod_dir(config)
    if not mod_dir.is_dir():
        return False, ["@TraderPlus mod folder not found"]

    if not has_traderplus_types_source(config):
        return _reset_types_from_upstream(config, mission_dir)

    custom_dir = mission_dir / "custom"
    prefix = kebab_case(mod_dir.name) + "-"
    removed = len(list(custom_dir.glob(f"{prefix}*.xml"))) if custom_dir.is_dir() else 0
    if removed:
        msgs.append(f"Removed {removed} old file(s) from custom/")

    copied = merge_mod_economy_entries(
        config.server_dir_path, mission_dir.name, mod_dir.name,
        manual_dir=config.paths.custom_xml_dir,
    )

    if not copied:
        return False, msgs + [
            "No types/events/spawnabletypes XML files found in @TraderPlus mod",
        ]

    msgs.append(f"Copied {len(copied)} XML file(s) to custom/")
    msgs.append("Updated cfgeconomycore.xml")
    return True, msgs


# ---------------------------------------------------------------------------
# Price math
# ---------------------------------------------------------------------------

def apply_price_multiplier(
    buy: float, sell: float, multiplier: float,
) -> tuple[float, float]:
    """Adjust buy/sell prices by multiplier, keeping sell <= buy and both >= 1.

    Prices of -1 (disabled) are left unchanged.
    """
    new_buy = buy
    new_sell = sell

    if buy >= 1:
        new_buy = max(1.0, round(buy * multiplier))
    if sell >= 1:
        new_sell = max(1.0, round(sell * multiplier))

    # Ensure sell never exceeds buy (when both are enabled)
    if new_buy >= 1 and new_sell >= 1:
        new_sell = min(new_sell, new_buy)

    return new_buy, new_sell
