"""Keep the mods an update replaced, so a broken update is undoable (TODO C5).

The failure this exists for: a mod publishes a new version, `zsm run` copies
it over the working one, and the server never comes back up. The old bytes
existed in exactly one place — `<server_dir>/@Mod` — and the copy has just
overwritten them. SteamCMD's workshop cache is no help; it was overwritten
first. Without this the only way back is finding the previous version
somewhere, which the Workshop does not offer.

**The stash is a move, not a copy.** `<server_dir>/.zsm-mod-rollback/` is on
the same filesystem as the mods, so setting the previous generation aside is a
rename: no bytes are read, no time is spent, and nothing is doubled until the
new version lands beside it. Copying instead would add minutes and tens of
gigabytes to every update, which is how a safety net becomes the thing people
turn off.

**One generation: the last operation that replaced installed mods.** That
is a whole update (`remove_server_mods`), and since 2026 Q4 also a copy
over a mod that is already there: a re-install from the Mods page, a backup
restore's re-download, a map switch (`Generation`, used by
`fileops.copy_mod_to_server`). The operation's first mod set aside replaces
the previous stash and the rest join it, so disk cost stays bounded at one
extra copy of the mods that operation replaced. `discard()` and the Cleanup
page get rid of it; `restore()` swaps it back.

A side effect worth naming: mods used to be copied with
``copytree(dirs_exist_ok=True)``, which *merges* — a file the mod stopped
shipping stayed behind for ever, and a stale PBO is a signature mismatch
nobody can explain (a renamed PBO loads twice). Moving the old directory aside
first makes each copy a clean replace. That is a real change in behaviour and
it is an improvement, but it is a change: a mod folder an admin hand-edited is
no longer preserved across an update (it is in the stash, which is where a
hand edit belongs after an update anyway).
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.shared.config import DayzConfig
from app.shared.json_utils import atomic_write_text

# Leading dot so `remove_server_mods`' `@*` glob, the Cleanup page's mod
# listings and every "which mods are installed" walk skip it without being
# taught about it.
ROLLBACK_DIRNAME = ".zsm-mod-rollback"
MANIFEST_NAME = "manifest.json"

__all__ = [
    "MANIFEST_NAME",
    "ROLLBACK_DIRNAME",
    "RestoreResult",
    "discard",
    "rollback_dir",
    "rollback_state",
    "restore",
    "should_offer_rollback",
    "stash_installed_mods",
]


@dataclass
class RestoreResult:
    ok: bool
    restored: list[str]
    message: str = ""


def rollback_dir(config: DayzConfig) -> Path:
    return config.server_dir_path / ROLLBACK_DIRNAME


def _manifest_path(config: DayzConfig) -> Path:
    return rollback_dir(config) / MANIFEST_NAME


def _read_manifest(config: DayzConfig) -> dict:
    try:
        return json.loads(_manifest_path(config).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_manifest(config: DayzConfig, taken_at: str, mods: list[str]) -> None:
    try:
        atomic_write_text(
            _manifest_path(config),
            json.dumps({"taken_at": taken_at, "mods": mods}, indent=2),
        )
    except OSError:
        pass


def _dir_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def discard(config: DayzConfig) -> bool:
    """Throw the stash away. True if there was one."""
    target = rollback_dir(config)
    if not target.is_dir():
        return False
    shutil.rmtree(target, ignore_errors=True)
    return not target.exists()


def stash_installed_mods(
    config: DayzConfig,
    names: list[str],
    *,
    log: Callable[[str], None] | None = None,
    add: bool = False,
) -> list[str]:
    """Move the currently-installed *names* aside. Returns what was moved.

    Replaces any previous stash: one generation is the promise, and keeping
    two would mean nobody can say how much disk this costs. ``add=True``
    joins the generation already there instead; only `Generation` passes it,
    for the second and later mods of one operation.

    Failure is not fatal and never raises. A mod that cannot be moved is
    simply not in the stash — an update that refuses to run because the safety
    net could not be set up is worse than an update with no safety net, and
    the caller reports what was actually kept rather than what it hoped for.
    """
    _emit = log or (lambda s: None)
    server_dir = config.server_dir_path
    target = rollback_dir(config)
    earlier: list[str] = []
    taken_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if add and target.is_dir():
        manifest = _read_manifest(config)
        earlier = list(manifest.get("mods") or [])
        taken_at = manifest.get("taken_at") or taken_at
    else:
        discard(config)

    moved: list[str] = []
    for name in names:
        source = server_dir / name
        if not source.is_dir():
            continue
        try:
            target.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target / name))
            moved.append(name)
        except OSError as e:
            _emit(f"Could not set {name} aside for rollback: {e}")

    if not moved:
        if not earlier:
            discard(config)
        return []

    _write_manifest(config, taken_at, [*earlier, *(m for m in moved if m not in earlier)])
    _emit(
        f"Kept the previous version of {len(moved)} mod(s) for rollback: "
        "'zsm mods rollback --restore' puts them back."
    )
    return moved


class Generation:
    """The stash of one operation that copies over installed mods.

    The first mod it sets aside replaces the previous stash; every later one
    joins it. So a re-install of three mods keeps all three previous versions,
    and does not wipe them one by one. Built per operation (a job, a CLI
    command) and handed to each `fileops.copy_mod_to_server` call.
    """

    def __init__(self, config: DayzConfig, *, log: Callable[[str], None] | None = None) -> None:
        self.config = config
        self.log = log
        self.moved: list[str] = []

    def set_aside(self, name: str) -> bool:
        """Move the installed *name* into this generation. False if it could
        not be moved; the caller then copies over it as before."""
        moved = stash_installed_mods(self.config, [name], log=self.log, add=bool(self.moved))
        self.moved += moved
        return bool(moved)


def rollback_state(config: DayzConfig) -> dict:
    """``{available, taken_at, mods, size_bytes}`` — what could be restored."""
    target = rollback_dir(config)
    if not target.is_dir():
        return {"available": False, "taken_at": "", "mods": [], "size_bytes": 0}
    manifest = _read_manifest(config)
    mods = [d.name for d in sorted(target.iterdir()) if d.is_dir()]
    return {
        "available": bool(mods),
        "taken_at": manifest.get("taken_at", ""),
        "mods": manifest.get("mods") or mods,
        "size_bytes": _dir_size(target),
    }


def restore(
    config: DayzConfig, *, log: Callable[[str], None] | None = None
) -> RestoreResult:
    """Put the stashed mods back, replacing what is installed now.

    The stash is consumed: after this the mods on disk *are* the stashed ones,
    so there is nothing left to roll back to and keeping an empty promise
    around would be worse than none. Re-running the update is how you go
    forward again.

    keys/ is synced to the restored folders afterwards (`copy_bikey_files`),
    since the update replaced or removed the old versions' keys and the stash
    never held them (plans/78 step 0). The mod XML is not re-merged here; the
    surfaces say how.
    """
    _emit = log or (lambda s: None)
    state = rollback_state(config)
    if not state["available"]:
        return RestoreResult(False, [], "There is no previous mod version to restore.")

    server_dir = config.server_dir_path
    target = rollback_dir(config)
    restored: list[str] = []
    failed: list[str] = []
    for name in state["mods"]:
        stashed = target / name
        if not stashed.is_dir():
            continue
        current = server_dir / name
        try:
            if current.is_dir():
                shutil.rmtree(current)
            shutil.move(str(stashed), str(current))
            restored.append(name)
            _emit(f"Restored {name}")
        except OSError as e:
            failed.append(name)
            _emit(f"Could not restore {name}: {e}")

    if failed:
        # The stash may now be the only copy of a mod that did not go back:
        # it stays, holding only those, so a second restore can retry.
        _write_manifest(config, state["taken_at"], failed)
    else:
        discard(config)
    if restored:
        _sync_keys(config, _emit)
    if failed:
        return RestoreResult(
            bool(restored),
            restored,
            f"Restored {len(restored)} mod(s); {len(failed)} failed: {', '.join(failed)}.",
        )
    return RestoreResult(
        True, restored, f"Restored the previous version of {len(restored)} mod(s)."
    )


def _sync_keys(config: DayzConfig, emit: Callable[[str], None]) -> None:
    """Copy the restored mods' keys into keys/, with markup-free log lines."""
    from app.modmgr.modlist import ModListError
    from app.ops.fileops import copy_bikey_files

    try:
        copy_bikey_files(config, log=lambda s: emit(s.strip()))
    except (ModListError, OSError) as e:
        emit(f"Could not copy the restored mods' keys into keys/: {e}")


def should_offer_rollback(config: DayzConfig) -> dict:
    """Is there something to roll back to *and* a reason to want it?

    The reason is the one `log_analysis` already knows: a run whose log never
    reaches "Player connect enabled" never became joinable. That is the shape
    of a mod update that broke the server — the process starts, the engine
    refuses the mod set, and nothing else in zsm notices because the unit is
    "active" for a while and then gone.

    Offering it *only* then is the point. A rollback button on a healthy
    server is an invitation to undo an update that worked.
    """
    from app.ops.log_analysis import analyze_rpt, latest_rpt

    state = rollback_state(config)
    if not state["available"]:
        return {**state, "offer": False, "reason": ""}

    path = latest_rpt(config)
    if path is None:
        return {**state, "offer": False, "reason": ""}
    try:
        outcome = (analyze_rpt(path).get("run") or {}).get("outcome", "")
    except Exception:
        return {**state, "offer": False, "reason": ""}

    if outcome != "startup_incomplete":
        return {**state, "offer": False, "reason": ""}
    return {
        **state,
        "offer": True,
        "reason": (
            f"The last run never became joinable ({path.name} never reaches "
            '"Player connect enabled"), and a mod update replaced '
            f"{len(state['mods'])} mod(s) before it."
        ),
    }
