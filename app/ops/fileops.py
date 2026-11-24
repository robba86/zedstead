"""File operations — remove/copy mods, copy bikeys."""

from __future__ import annotations

import filecmp
import os
import shutil
from pathlib import Path
from typing import Callable

from rich.console import Console
from rich.markup import escape

from app.shared.config import DayzConfig
from app.modmgr.mods import is_safe_mod_dir_name, is_workshop_id
from app.modmgr import modlist
from app.shared.cli_ui import markup_emitter
from app.shared.zip_utils import copytree_without_links

console = Console()


def bikeys_only_this_mod_ships(config: DayzConfig, mod_dir: Path) -> list[str]:
    """The names of *mod_dir*'s bikeys that may leave the server's keys/ with
    it. A key another mod folder still ships stays (one author signs several
    mods with one key), and so does the game's own dayz.bikey, as in
    `copy_bikey_files`. Call it before the folder is deleted."""
    if not mod_dir.is_dir():
        return []
    still_shipped = {
        os.path.normcase(key.name)
        for other in config.server_dir_path.glob("@*")
        if other.is_dir() and other.name != mod_dir.name
        for key in other.rglob("*.bikey")
    }
    return [
        f.name for f in mod_dir.rglob("*.bikey")
        if f.name.lower() != _SERVER_KEY and os.path.normcase(f.name) not in still_shipped
    ]


def remove_mod_from_server(
    config: DayzConfig, mod_name: str, *, log: Callable[[str], None] | None = None
) -> None:
    """Remove a single mod directory and its bikeys from the server."""
    _emit = markup_emitter(log)
    if not is_safe_mod_dir_name(mod_name):
        # A blank/absolute/"../" name would resolve to (or above) the server
        # root; rmtree there would wipe the whole server. Refuse it.
        _emit(f"[yellow]Refusing to remove invalid mod name: {mod_name!r}[/yellow]")
        return

    mod_dir = config.server_dir_path / mod_name
    keys_dir = config.keys_dir

    # Collect bikey filenames before deleting the mod dir.
    bikeys_to_remove = bikeys_only_this_mod_ships(config, mod_dir)

    # Remove the mod directory
    if mod_dir.is_dir():
        shutil.rmtree(mod_dir)
        _emit(f"[green]Removed {mod_dir}[/green]")
    else:
        _emit(f"[dim]{mod_dir} not found, skipping directory removal.[/dim]")

    # Remove matching bikeys from server keys/
    if bikeys_to_remove and keys_dir.is_dir():
        for bikey_name in bikeys_to_remove:
            bikey_path = keys_dir / bikey_name
            if bikey_path.exists():
                bikey_path.unlink()
                _emit(f"[green]Removed bikey {bikey_name}[/green]")


def remove_server_mods(
    config: DayzConfig,
    *,
    log: Callable[[str], None] | None = None,
    stash: bool = True,
) -> list[str]:
    """Clear the @-prefixed mod directories out of the server root.

    This is the step in front of every mod copy, which makes it the last
    moment the *previous* versions exist anywhere: SteamCMD has already
    overwritten its own workshop cache by then. So by default they are moved
    into `mod_rollback`'s stash rather than deleted — a rename within the
    server dir, so it is cheaper than the `rmtree` it replaces, and a mod
    update that stops the server booting becomes undoable (TODO C5).

    ``stash=False`` for a caller that is **undoing** rather than updating:
    `pipeline._undo_copy_mods` must not overwrite the good stash with the
    broken mods it is in the middle of removing.

    A local mod's folder (plans/78 D1: the admin's own, with no Workshop ID)
    is left where it is: no download can bring it back. A mod list that
    cannot be read raises `ModListError` before anything is moved.

    Returns the mods set aside, so a pipeline undo knows whether the stash
    is this update's (`pipeline._undo_remove_old_mods`).
    """
    _emit = markup_emitter(log)
    local = modlist.local(config)
    names = [i.name for i in config.server_dir_path.glob("@*") if i.is_dir()]
    kept_local = sorted(n for n in names if n in local)
    names = [n for n in names if n not in local]
    if kept_local:
        _emit(
            f"[dim]Left {len(kept_local)} local mod folder(s) in place: "
            f"{escape(', '.join(kept_local))}.[/dim]"
        )

    kept: list[str] = []
    if stash and names:
        from app.ops import mod_rollback

        # stash_installed_mods says how many it kept and how to put them back.
        kept = mod_rollback.stash_installed_mods(config, names, log=lambda s: _emit(s))
        names = [n for n in names if n not in kept]

    removed = 0
    for name in names:
        item = config.server_dir_path / name
        if item.is_dir():
            shutil.rmtree(item)
            removed += 1
    _emit(f"[green]✓ Removed {removed} mod folder(s) from server directory.[/green]")
    return kept


def copy_mod_to_server(
    config: DayzConfig,
    mod_name: str,
    cached_ids: dict[str, str],
    *,
    log: Callable[[str], None] | None = None,
    generation: "mod_rollback.Generation | None" = None,
) -> bool:
    """Copy a single mod from SteamCMD workshop cache to server dir. Returns True on success.

    A mod that is already installed is set aside into the rollback stash
    first (*generation*, one per operation; a fresh one when not given), so
    the copy is a clean replace. Copying over it merged the two versions, and
    a PBO the new version renamed or dropped kept loading. The update
    pipeline never gets here with a mod installed: `remove_old_mods` ran
    first.
    """
    _emit = markup_emitter(log)
    if not is_safe_mod_dir_name(mod_name):
        # The copy target is server_dir / mod_name: anything but one @Name
        # component would put a workshop item's files outside the server dir.
        _emit(f"[yellow]Warning: {mod_name!r} is not a mod folder name, skipping.[/yellow]")
        return False
    mod_id = cached_ids.get(mod_name.lstrip("@"), cached_ids.get(mod_name, ""))
    if not mod_id:
        _emit(f"[yellow]Warning: No ID found for {mod_name}, skipping.[/yellow]")
        return False
    if not is_workshop_id(mod_id):
        _emit(f"[yellow]Warning: {mod_name}'s workshop ID {mod_id!r} is not a number, skipping.[/yellow]")
        return False

    source = config.workshop_dir / mod_id
    destination = config.server_dir_path / mod_name

    if not source.exists():
        _emit(f"[yellow]Warning: {mod_name} not found in workshop cache ({source}).[/yellow]")
        return False

    from app.shared.steam import is_mod_downloaded

    if not is_mod_downloaded(config, mod_id):
        # A silently-failed SteamCMD download leaves a hollow item dir behind —
        # often the bare Addons/ + Keys/ skeleton with no files in it. Copying
        # that creates a mod folder with no PBOs, keys or XML, and the mod then
        # looks installed everywhere while contributing nothing.
        _emit(
            f"[yellow]Warning: {mod_name} is empty in the workshop cache ({source}) — "
            f"the download failed. Check the Steam login and the workshop manifest "
            f"(`zsm cleanup repair-workshop`), then re-install it.[/yellow]"
        )
        return False

    if destination.is_dir():
        from app.ops import mod_rollback

        stash = generation or mod_rollback.Generation(config, log=_emit)
        if not stash.set_aside(mod_name):
            _emit(
                f"[yellow]  Could not set the installed {mod_name} aside — copying over it, "
                f"so files the new version dropped stay.[/yellow]"
            )
    _emit(f"  Copying {mod_name} (ID: {mod_id})...")
    skipped = copytree_without_links(source, destination, dirs_exist_ok=True)
    if skipped:
        _emit(f"[yellow]  Left out {skipped} symlink(s) in {mod_name}'s workshop files.[/yellow]")
    from app.traderplus.upstream_defaults import restore_missing_mod_files

    restore_missing_mod_files(destination, mod_id, log=_emit)
    return True


def copy_all_mods_to_server(
    config: DayzConfig, *, log: Callable[[str], None] | None = None
) -> int:
    """Copy all mods from workshop cache to server. Returns count of successful copies.

    A local mod has no Workshop copy and its folder was never removed, so it is
    skipped with a line (plans/78 D1).
    """
    _emit = markup_emitter(log)
    local = modlist.local(config)
    mod_list = [m for m in modlist.installed(config) if m not in local]
    for name in sorted(local):
        _emit(f"  Skipped [cyan]{escape(name)}[/cyan]: a local mod, kept as it is.")
    cached_ids = modlist.workshop_ids(config)
    total = len(mod_list)
    copied = 0
    from app.ops import mod_rollback

    generation = mod_rollback.Generation(config, log=_emit)
    for i, mod_name in enumerate(mod_list, 1):
        _emit(f"  [{i}/{total}] Copying [cyan]{mod_name}[/cyan]...")
        if copy_mod_to_server(config, mod_name, cached_ids, log=log, generation=generation):
            copied += 1
    _emit(f"\n[green]✓ Copied {copied}/{total} mods to server.[/green]")
    return copied


# The game's own key, which keys/ always keeps (BattlEye checks the vanilla
# files against it).
_SERVER_KEY = "dayz.bikey"


def _same_file_bytes(a: Path, b: Path) -> bool:
    """True when both files hold the same bytes; False when either cannot be read."""
    try:
        return filecmp.cmp(a, b, shallow=False)
    except OSError:
        return False


def copy_bikey_files(
    config: DayzConfig, *, log: Callable[[str], None] | None = None
) -> tuple[int, int]:
    """Clear bikeys (except dayz.bikey) and copy from each mod's Keys/ directory.

    Returns ``(removed, copied)``. The pipeline and the install job ignore it and
    read the log line instead; ``POST /api/mods/copy-bikeys`` (the Conflicts
    tables' "Copy bikeys" fix) reports the counts back to the browser, where
    there is no console to print to. ``removed`` counts every old key that was
    deleted or copied over, as when the old keys were all deleted first.

    The mods' keys are copied first and the old keys that no mod's key replaced
    are deleted after (plans/68 2P-C4). A directory or a link named ``*.bikey``
    is skipped, as `copytree_without_links` leaves links out, and a key that
    fails to copy is logged and keeps the copy keys/ already had, so one bad
    entry never leaves keys/ with fewer keys than before. Deleting first meant
    a directory named ``*.bikey`` in one mod stopped the copy half way, and the
    server started without the keys of every mod after it.

    A mod's key named ``dayz.bikey`` is never copied: that is the game's own
    key, and replacing it fails the signature check for every vanilla file
    (plans/68 CON-15). Two mods shipping different keys under one name are
    logged, naming both, since only the later one's copy can be in keys/.

    While an enabled local mod (plans/78 D1) ships no key in its folder, its
    key can only be one already in keys/, so no old key is removed then; the
    log names the mod.
    """
    _emit = markup_emitter(log)
    keys_dir = config.keys_dir
    keys_dir.mkdir(exist_ok=True)
    old_keys = [
        key for key in keys_dir.glob("*.bikey")
        if key.name != _SERVER_KEY and (key.is_file() or key.is_symlink())
    ]

    # Copy bikeys from active (enabled) mod directories
    listed = modlist.load(config)
    mod_list = listed.active()
    local = listed.local()
    keyless_local: list[str] = []  # enabled local mods whose folder holds no key
    copied = 0
    shipped: set[str] = set()  # every key name an enabled mod ships
    replaced: set[str] = set()  # the ones that are now in keys/
    copied_from: dict[str, str] = {}  # key name -> the mod whose copy is in keys/
    for mod_name in mod_list:
        mod_dir = config.server_dir_path / mod_name
        if not mod_dir.is_dir():
            continue
        if mod_name in local and not any(mod_dir.rglob("*.bikey")):
            keyless_local.append(mod_name)
        for bikey in mod_dir.rglob("*.bikey"):
            if bikey.is_symlink() or not bikey.is_file():
                continue
            if bikey.name.lower() == _SERVER_KEY:
                _emit(
                    f"[yellow]  Skipped {escape(mod_name)}'s {escape(bikey.name)}: "
                    "keys/dayz.bikey is the game's own key and is never replaced.[/yellow]"
                )
                continue
            name = os.path.normcase(bikey.name)  # one file on Windows whatever its case
            shipped.add(name)
            dest = keys_dir / bikey.name
            earlier = copied_from.get(name)
            if earlier is not None and not _same_file_bytes(bikey, dest):
                who = (
                    f"{escape(mod_name)} ships two keys" if earlier == mod_name
                    else f"{escape(earlier)} and {escape(mod_name)} both ship a key"
                )
                _emit(
                    f"[yellow]  {who} named {escape(bikey.name)}, with different contents. "
                    f"keys/ keeps the later one (from {escape(mod_name)}), so files signed "
                    "with the other may fail the signature check.[/yellow]"
                )
            try:
                if dest.is_symlink():
                    dest.unlink()  # copy the key in its place, never through the link
                shutil.copy2(str(bikey), str(keys_dir))
            except OSError as exc:
                _emit(
                    f"[yellow]  Could not copy {mod_name}'s {bikey.name}: "
                    f"{exc.strerror or exc}[/yellow]"
                )
                continue
            replaced.add(name)
            copied_from[name] = mod_name
            copied += 1

    # Remove the old .bikey files (never dayz.bikey) that no mod's key replaced
    removed = 0
    if keyless_local:
        unclaimed = [k for k in old_keys if os.path.normcase(k.name) not in shipped]
        if unclaimed:
            _emit(
                f"[yellow]  Kept {len(unclaimed)} key(s) no listed mod ships, because "
                f"{escape(', '.join(keyless_local))} has no key in its folder and one of "
                "them may be its.[/yellow]"
            )
        old_keys = [k for k in old_keys if os.path.normcase(k.name) in shipped]
    for key in old_keys:
        name = os.path.normcase(key.name)
        if name in shipped:
            removed += name in replaced
            continue
        try:
            key.unlink()
        except OSError as exc:
            _emit(f"[yellow]  Could not remove {key.name}: {exc.strerror or exc}[/yellow]")
            continue
        removed += 1

    _emit(f"[green]✓ Removed {removed} old bikey(s), copied {copied} new bikey(s).[/green]")
    return removed, copied
