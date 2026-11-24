"""Named server instances (TODO E1, plans/44 step 1).

An instance is a **name**. Its server directory and ports live in that
instance's own `config.json` and its mods in its `mods.json`; `instances.json` holds only the list of names and
which one is the default. That is the whole difference from the April design
on `feature/server-instances` (plans/44 §1.1), which kept instances as rows in
`config.json` behind a `set_active`/`get_active` pointer: one piece of global
state whose every write changed what every other caller read. There is no
"active instance" here. A default is only what `--instance` falls back to.

Only the pure helpers survive from that branch (`PORT_OFFSET`, the port
arithmetic, `service_name`, the path rewrite). `DayzConfig.load()` reads the
registry's default, and `instance_migration` writes the first registry.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, NamedTuple

from app.shared.json_utils import atomic_write_text
from app.shared.systemd_scope import MAIN_INSTANCE

if TYPE_CHECKING:
    from app.shared.config import DayzConfig

DEFAULT_INSTANCE = MAIN_INSTANCE

# Port offset between instances (community standard).
PORT_OFFSET = 100

# It becomes a directory name and part of a systemd unit name, so the set is
# the intersection of what both accept without quoting.
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")

# A label only names the instance (the panel's sidebar and tab title), so it
# may hold anything but a line break; long enough for "Deer Isle PvP #2".
MAX_LABEL = 64


def validate_instance_id(value: str) -> str:
    """Return *value* stripped, or raise ``ValueError`` saying what is allowed."""
    ident = (value or "").strip()
    if not _ID_RE.match(ident):
        raise ValueError(
            f"Invalid instance name {value!r}: use 1-32 lowercase letters, digits "
            "and hyphens, starting with a letter or digit."
        )
    return ident


@dataclass(frozen=True)
class ServerInstance:
    id: str
    label: str = ""

    @property
    def display_name(self) -> str:
        return self.label or self.id


@dataclass(frozen=True)
class InstanceRegistry:
    """The contents of `instances.json`. Every change returns a new registry."""

    instances: tuple[ServerInstance, ...] = ()
    default: str = ""

    def get(self, instance_id: str) -> ServerInstance | None:
        return next((i for i in self.instances if i.id == instance_id), None)

    def ids(self) -> list[str]:
        return [i.id for i in self.instances]

    @property
    def default_id(self) -> str:
        """The default, or the first instance if none is set, or ``""``."""
        if self.default and self.get(self.default):
            return self.default
        return self.instances[0].id if self.instances else ""


def add_instance(registry: InstanceRegistry, instance: ServerInstance) -> InstanceRegistry:
    """Add *instance*. The first one added becomes the default."""
    ident = validate_instance_id(instance.id)
    if registry.get(ident):
        raise ValueError(f"Instance '{ident}' already exists.")
    added = replace(instance, id=ident)
    return InstanceRegistry(
        instances=(*registry.instances, added),
        default=registry.default or ident,
    )


def remove_instance(registry: InstanceRegistry, instance_id: str) -> InstanceRegistry:
    """Remove *instance_id*. Refuses the last one: a machine with no instance
    has nothing for `zsm` to act on, and the recovery path would be a panel
    that cannot start (the `plans/43` last-admin rule, applied to servers)."""
    if not registry.get(instance_id):
        raise ValueError(f"No instance named '{instance_id}'.")
    if len(registry.instances) == 1:
        raise ValueError(f"'{instance_id}' is the only instance; it cannot be removed.")
    kept = tuple(i for i in registry.instances if i.id != instance_id)
    default = registry.default if registry.default != instance_id else kept[0].id
    return InstanceRegistry(instances=kept, default=default)


def set_default(registry: InstanceRegistry, instance_id: str) -> InstanceRegistry:
    if not registry.get(instance_id):
        raise ValueError(f"No instance named '{instance_id}'.")
    return replace(registry, default=instance_id)


def clean_label(label: str) -> str:
    """*label* with its whitespace collapsed, or ``ValueError`` when it is too
    long to show beside the panel's wordmark."""
    label = " ".join((label or "").split())
    if len(label) > MAX_LABEL:
        raise ValueError(f"A label is at most {MAX_LABEL} characters.")
    return label


def set_label(registry: InstanceRegistry, instance_id: str, label: str) -> InstanceRegistry:
    """Rename *instance_id*'s display name. The id stays: it names the
    instance's directory and its systemd units."""
    if not registry.get(instance_id):
        raise ValueError(f"No instance named '{instance_id}'.")
    label = clean_label(label)
    return replace(registry, instances=tuple(
        replace(i, label=label) if i.id == instance_id else i for i in registry.instances
    ))


# --- instances.json -----------------------------------------------------------


def load_registry(path: Path | None = None) -> InstanceRegistry:
    """Read `instances.json`. A missing file is an empty registry: a fresh
    box, or an install still on the pre-instances layout."""
    from app.shared.paths import instances_file

    path = path or instances_file()
    if not path.is_file():
        return InstanceRegistry()
    data = json.loads(path.read_text(encoding="utf-8"))
    instances = tuple(
        ServerInstance(id=validate_instance_id(row["id"]), label=str(row.get("label", "")))
        for row in data.get("instances", [])
    )
    return InstanceRegistry(instances=instances, default=str(data.get("default", "")))


def instance_summary(instance_id: str) -> dict:
    """Which instance this is, for a surface that names it (plans/62 step 1).

    ``{"id", "label", "count"}``: ``label`` is the display name, ``count`` the
    number of instances on the machine. The pre-instances layout (an empty
    id) has nothing to name and reports ``""``/``""``/``0``. A registry that
    cannot be read still names the instance by its id, with ``count`` 0,
    because a hand edit gone wrong must not take the panel's login check down
    with it; the CLI is where that error is reported.
    """
    if not instance_id:
        return {"id": "", "label": "", "count": 0}
    try:
        registry = load_registry()
    except (OSError, ValueError, KeyError, TypeError):
        return {"id": instance_id, "label": instance_id, "count": 0}
    entry = registry.get(instance_id)
    return {
        "id": instance_id,
        "label": entry.display_name if entry else instance_id,
        "count": len(registry.instances),
    }


def save_registry(registry: InstanceRegistry, path: Path | None = None) -> None:
    """Write `instances.json` atomically (the same temp-file + replace as
    `DayzConfig.save`, for the same reason: a half-written file loses every
    instance at once)."""
    from app.shared.paths import instances_file

    path = path or instances_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "default": registry.default,
        "instances": [{"id": i.id, "label": i.label} for i in registry.instances],
    }
    atomic_write_text(path, json.dumps(data, indent=2) + "\n", owner_only=True)


def save_new_config(config: DayzConfig) -> None:
    """Save a config built from nothing (`zsm init`, a container's first boot)
    as an instance, never as the pre-instances `config.json`.

    It belongs to the instance this process acts on (`--instance`, else the
    registry's default), and on a fresh box to `main`. The file goes first and
    the registry second, the same commit order as the migration: a registry
    never names an instance whose config was not written.
    """
    from app.shared.config import INSTANCE_ENV

    registry = load_registry()
    ident = os.environ.get(INSTANCE_ENV, "").strip() or registry.default_id or DEFAULT_INSTANCE
    config.instance_id = validate_instance_id(ident)
    config.save()
    if not registry.get(config.instance_id):
        save_registry(add_instance(registry, ServerInstance(config.instance_id)))


# --- ports --------------------------------------------------------------------


class InstancePorts(NamedTuple):
    """The three ports an admin sets; everything else is derived from them."""

    game: int
    query: int
    rcon: int

    def all(self) -> set[int]:
        """Every UDP port the server holds: the game port and the three after
        it (`firewall_ops.required_ports`), the Steam query port and RCon.
        RCon is off the firewall list but not off this one — two servers
        cannot both bind it."""
        return {self.game, self.game + 1, self.game + 2, self.game + 3, self.query, self.rcon}


def server_dir_conflict(server_dir: str | Path, *, own_id: str = "") -> str | None:
    """Why *server_dir* cannot be a server directory because of what else is
    there, or None.

    A Reinstall server deletes the whole server dir before it downloads, so a
    dir that is or holds the filesystem root, the home folder, Zedstead's own
    config (the accounts, every instance's config) or another instance's
    server would go with it. /home/zsm was accepted as an instance's server
    dir; only an exact duplicate was refused.
    A dir inside another instance's is refused too: that instance's reinstall
    would delete it. *own_id* is skipped, so an instance never clashes with
    itself.
    """
    from app.shared import paths
    from app.shared.config import DayzConfig

    try:
        mine = Path(server_dir).expanduser().resolve()
    except (OSError, RuntimeError):
        return None
    if mine == Path(mine.anchor) or mine == Path.home().resolve():
        return (f"{mine} is the {'filesystem root' if mine == Path(mine.anchor) else 'home folder'}. "
                "A Reinstall server deletes the whole server directory, so pick a folder of its own, "
                "such as one beside the first server's.")
    own_config = paths.config_dir().resolve()
    if own_config.is_relative_to(mine):
        return (f"{mine} holds Zedstead's own settings ({own_config}). A Reinstall server deletes the "
                "whole server directory, accounts and every instance's config with it, so pick a "
                "folder of its own.")
    for other in load_registry().instances:
        if other.id == own_id:
            continue
        try:
            theirs = DayzConfig.for_instance(other.id).server_dir_path
        except FileNotFoundError:
            continue
        if not str(theirs) or not theirs.is_absolute():
            continue
        theirs = theirs.resolve()
        if theirs == mine:
            return f"'{other.id}' already uses {mine}: two servers cannot share one directory."
        if mine.is_relative_to(theirs) or theirs.is_relative_to(mine):
            return (f"'{other.id}' uses {theirs}, and {mine} is inside it or holds it. A Reinstall "
                    "server deletes a server directory with everything in it, so it would delete "
                    "the other server too. Pick a directory beside it, not in it.")
    return None


def instance_ports(config: DayzConfig) -> InstancePorts:
    """Read an instance's ports the way the rest of zsm does: the game and
    RCon ports from its config, the query port from `config.steam_query_port`
    when zsm manages it (an instance `add`/`clone` made, Docker's
    `STEAM_QUERY_PORT`) and from its `serverDZ.cfg` otherwise."""
    from app.server.server_status import get_steam_query_port

    query = config.steam_query_port if config.steam_query_port > 0 else get_steam_query_port(config)
    return InstancePorts(config.server_port, query, config.rcon_port)


def _readable_configs(registry: InstanceRegistry) -> dict[str, DayzConfig]:
    """Every registered instance's config, skipping one whose `config.json`
    is missing."""
    from app.shared.config import DayzConfig

    configs: dict[str, DayzConfig] = {}
    for inst in registry.instances:
        try:
            configs[inst.id] = DayzConfig.for_instance(inst.id)
        except FileNotFoundError:
            continue
    return configs


def taken_ports(registry: InstanceRegistry) -> dict[str, InstancePorts]:
    """The ports of every registered instance whose config can be read."""
    return {ident: instance_ports(c) for ident, c in _readable_configs(registry).items()}


def check_port_conflicts(taken: dict[str, InstancePorts], candidate: InstancePorts) -> list[str]:
    """One line per port *candidate* shares with an instance in *taken*."""
    conflicts: list[str] = []
    wanted = candidate.all()
    for name, ports in taken.items():
        for port in sorted(wanted & ports.all()):
            conflicts.append(f"port {port} is used by '{name}'")
    return conflicts


def port_clash(config: DayzConfig, game_port: int | None = None) -> str | None:
    """The first port *config*'s server would share with another registered
    instance, as the Instances page words it, or None.

    *game_port* replaces the config's own (a bundle's, an adopted server's).
    A socket test passes while the other server is stopped, so Adopt said
    "Port 2402 is free" for exp's port, and a bundle import took main's.
    None for a config with no instance id: the
    pre-instances layout has nobody to clash with.
    """
    own = getattr(config, "instance_id", "")
    if not own:
        return None
    mine = instance_ports(config)
    game = mine.game if game_port is None else game_port
    others = {k: v for k, v in taken_ports(load_registry()).items() if k != own}
    conflicts = check_port_conflicts(others, InstancePorts(game, mine.query, mine.rcon))
    return conflicts[0] if conflicts else None


def suggest_ports(taken: Iterable[InstancePorts]) -> InstancePorts:
    """The next free set, `PORT_OFFSET` above the highest in use, stepping on
    until none of the six collides (a moved query port can sit anywhere)."""
    taken = list(taken)
    if not taken:
        return InstancePorts(2302, 27016, 2305)
    used = set().union(*(p.all() for p in taken))
    step = 1
    while True:
        candidate = InstancePorts(
            max(p.game for p in taken) + PORT_OFFSET * step,
            max(p.query for p in taken) + PORT_OFFSET * step,
            max(p.rcon for p in taken) + PORT_OFFSET * step,
        )
        if not candidate.all() & used:
            return candidate
        step += 1


# --- add and clone (plans/44 step 5) -------------------------------------------

# What `add` takes from the default instance: this machine's Steam setup, which
# a second server uses as it is. Nothing about the server itself.
MACHINE_FIELDS = ("steamcmd_dir", "steam_username", "workshop_content_dir", "service_scope")

# What identifies one server to the outside. A clone inheriting them would
# publish to zedstead.com as the same server and edit the same Discord message.
# The ids are created again when zedstead.com is next used; the listing starts off,
# because the copy has no server behind it yet.
CLONE_CLEARED = {
    "site_install_id": "",
    "site_secret": "",
    "site_listing": False,
    "site_address": "",
    "discord_status_message_id": "",
}

# Where an instance writes data of its own. Shared, one server's retention
# would prune the other's backups; empty means the instance's own directory.
OWN_DATA_PATHS = ("backup_dir", "bundle_export_dir", "backup_remote_path")

# Per-instance files a clone copies: what the admin wrote, not what the server
# produced (backups, telemetry, versions stay with the source).
CLONED_DIRS = ("custom_xml_dir", "presets_dir")


def _apply_branch(config: DayzConfig, branch: str | None, source: DayzConfig | None) -> list[str]:
    """Set the new instance's branch; the note says when it is not the source's.
    Raises ``ValueError`` when the directory already holds the other server."""
    from app.shared.steam import BRANCH_LABELS, SERVER_APP_IDS, installed_server_branch, server_branch

    if branch is not None:
        if branch not in SERVER_APP_IDS:
            raise ValueError(f"Unknown branch {branch!r}: use stable or experimental.")
        config.server_branch = branch
    mine = server_branch(config)
    installed = installed_server_branch(config.server_dir_path)
    if installed not in (None, mine):
        what = ("both the stable and the Experimental DayZ server" if installed == "mixed"
                else f"the {BRANCH_LABELS[installed]} DayZ server")
        raise ValueError(f"{config.server_dir} already holds {what}, and one directory cannot hold "
                         f"both. Give the {BRANCH_LABELS[mine]} instance a server directory of its own.")
    theirs = server_branch(source) if source is not None else "stable"
    if mine == theirs:
        return []
    if mine == "experimental":
        return [f"This instance installs the Experimental server (Steam app {SERVER_APP_IDS[mine]}) "
                "on its first update; only players on DayZ Experimental can join it."]
    return [f"This instance installs the {BRANCH_LABELS[mine]} server (Steam app "
            f"{SERVER_APP_IDS[mine]}) on its first update, not the {BRANCH_LABELS[theirs]} one."]


def _accounts_sign_in() -> bool:
    """Whether users.json holds a readable account (`UserStore.has_accounts`),
    which is then the panel's login on every instance."""
    from app.web.users import UserStore

    return UserStore().has_accounts()


@dataclass(frozen=True)
class NewInstance:
    config: DayzConfig
    ports: InstancePorts
    notes: tuple[str, ...] = ()


def server_dir_unwritable(server_dir: str | Path) -> str | None:
    """Why this user cannot create or write *server_dir*, or None when it can.

    SteamCMD reports a full install even when it cannot use the directory, so
    without this check the failure comes only at the first mod copy. It looks at
    the nearest folder that exists and creates nothing.
    """
    path = Path(server_dir).expanduser()
    folder = next((p for p in (path, *path.parents) if p.exists()), None)
    if folder is not None and folder.is_dir() and os.access(folder, os.W_OK | os.X_OK):
        return None
    return (
        f"Zedstead cannot create or write {path}: this user has no write access to {folder or path}. "
        "Pick a folder inside a directory that this user can write to, such as its home directory."
    )


def create_instance(
    instance_id: str,
    *,
    server_dir: str,
    label: str = "",
    source: DayzConfig | None = None,
    branch: str | None = None,
) -> NewInstance:
    """`zsm instances add` (no *source*) and `clone` (*source* is the config
    copied). Writes the instance's `config.json`, then the registry, with the
    next free set of ports. Raises ``ValueError`` saying what is wrong.

    *branch* ("stable" or "experimental") is the server the instance installs
    (plans/66); None keeps the source's, or stable for `add`.
    """
    import copy
    import shutil

    from app.shared.config import DayzConfig
    from app.shared.paths import InstancePaths, instance_root

    from app.server import instance_migration

    if instance_migration.pending():
        # The first instance registered becomes the default, and `load()`
        # would read it instead of the legacy config the migration has not
        # copied yet: the install would seem to lose every setting.
        raise ValueError("This install has not moved into instances/main yet (an app from "
                         "before the upgrade is probably still running). Restart the app "
                         "first; it moves the install when it starts.")
    registry = load_registry()
    ident = validate_instance_id(instance_id)
    label = clean_label(label)
    if registry.get(ident):
        raise ValueError(f"Instance '{ident}' already exists.")
    root = instance_root(ident)
    if root.exists():
        raise ValueError(f"{root} still exists from a removed instance. Run "
                         f"`zsm instances remove {ident} --purge` to delete it, or pick another name.")
    server = Path(server_dir).expanduser()
    if not server.is_absolute():
        raise ValueError(f"The server directory must be an absolute path, not {server_dir!r}.")
    from app.server.service_control import server_dir_problem

    problem = (
        server_dir_problem(str(server))
        or server_dir_conflict(server, own_id=ident)
        or server_dir_unwritable(server)
    )
    if problem:
        raise ValueError(problem)

    ports = suggest_ports(taken_ports(registry).values())
    notes: list[str] = []
    from app.shared.container import server_dir_volume_problem

    volume = server_dir_volume_problem(str(server))
    if volume:
        notes.append(volume)
    if source is not None:
        config = copy.deepcopy(source)
        for name, value in CLONE_CLEARED.items():
            setattr(config, name, value)
        if _accounts_sign_in():
            # The accounts are the clone's login, as they are an added
            # instance's, so it starts without a copy of the fallback
            # password (2P-W2). With no readable account that copy is the
            # only way in when the panel runs on the clone, so it stays.
            config.web_password_hash = ""
        for name in OWN_DATA_PATHS:
            old = getattr(source, name)
            new = rewrite_instance_path(old, source.instance_id, ident) if source.instance_id else ""
            if old and new == old:
                new = ""
            setattr(config, name, new)
            if old:
                notes.append(f"{name} was {old}; the copy uses {new or 'its own directory'}.")
    else:
        config = DayzConfig()
        default = registry.default_id
        if default:
            try:
                machine = DayzConfig.for_instance(default)
            except FileNotFoundError:
                machine = None
            for name in MACHINE_FIELDS if machine else ():
                setattr(config, name, getattr(machine, name))
    config.server_dir = str(server)
    config.server_port, config.steam_query_port, config.rcon_port = ports
    config.instance_id = ident
    notes.extend(_apply_branch(config, branch, source))
    config.save()
    if source is not None:
        mine = InstancePaths(root)
        for name in CLONED_DIRS:
            src = getattr(source.paths, name)
            if src.is_dir():
                shutil.copytree(src, getattr(mine, name), symlinks=True)
    save_registry(add_instance(registry, ServerInstance(ident, label)))
    return NewInstance(config, ports, tuple(notes))


def installed_units(config: DayzConfig) -> list[str]:
    """The systemd units *config*'s instance has installed: its server unit
    and its task timers (plans/44 step 6). Removing the instance would leave
    them pointing at a server nothing manages. Empty without systemd; a
    Windows scheduled task is not read."""
    from app.shared.platform import IS_WINDOWS, has_systemd
    from app.shared.systemd_scope import get_scope, server_unit, task_unit_prefix

    if IS_WINDOWS or not has_systemd():
        return []
    unit_dir = get_scope(config).unit_dir
    found = [server_unit(config)] if (unit_dir / server_unit(config)).exists() else []
    if unit_dir.is_dir():
        found += sorted(p.name for p in unit_dir.glob(f"{task_unit_prefix(config)}*.timer"))
    return found


def leftover_dir(instance_id: str, registry: InstanceRegistry | None = None) -> Path | None:
    """The directory under `instances/` of a name that is not on the list, or
    ``None``. A web Remove leaves one behind, and it keeps the name taken."""
    from app.shared.paths import instance_root

    if (registry or load_registry()).get(instance_id):
        return None
    try:
        root = instance_root(validate_instance_id(instance_id))
    except ValueError:
        return None
    return root if root.is_dir() else None


def removal_problem(
    instance_id: str, registry: InstanceRegistry | None = None, *, purge: bool = False,
) -> str | None:
    """Why *instance_id* cannot be removed now, or ``None``: it is not
    registered (with *purge*, a leftover directory counts), its server is
    running, or its systemd units are installed (`installed_units`). Removing
    the last instance is refused later, by `remove_instance`. The CLI and the
    web page ask the same question."""
    from app.server.service_control import is_server_running
    from app.shared.config import DayzConfig

    registry = registry or load_registry()
    if not registry.get(instance_id) and not (purge and leftover_dir(instance_id, registry)):
        return f"No instance named '{instance_id}'."
    try:
        config = DayzConfig.for_instance(instance_id)
    except FileNotFoundError:
        return None
    if is_server_running(config):
        return f"The server of '{instance_id}' is running. Stop it first."
    units = installed_units(config)
    if units:
        return (f"'{instance_id}' still has {', '.join(units)} installed. Remove them first: "
                f"`zsm --instance {instance_id} service remove` and "
                f"`zsm --instance {instance_id} service remove-timers`.")
    return None


def delete_instance(instance_id: str, *, purge: bool = False) -> Path:
    """Drop *instance_id* from the registry; with *purge*, also delete its
    directory under `instances/`, also when an earlier removal left only that
    directory. Never its server directory. Returns that directory. The caller
    checks that its server is not running and that it has no units installed
    (`installed_units`)."""
    import shutil

    from app.shared.paths import instance_root

    registry = load_registry()
    leftover = leftover_dir(instance_id, registry) if purge else None
    if leftover:
        shutil.rmtree(leftover)
        return leftover
    registry = remove_instance(registry, instance_id)
    root = instance_root(validate_instance_id(instance_id))
    save_registry(registry)
    if purge and root.is_dir():
        shutil.rmtree(root)
    return root


# --- the list, for `instances list` and the web's Instances page ---------------


@dataclass(frozen=True)
class InstanceRow:
    """One instance as the list shows it. ``config`` is ``None`` when its
    `config.json` is missing, and then there are no ports either."""

    id: str
    label: str
    default: bool
    config: DayzConfig | None
    ports: InstancePorts | None
    conflicts: tuple[str, ...]


def instance_rows(registry: InstanceRegistry | None = None) -> list[InstanceRow]:
    """Every registered instance with its ports and the ports it shares with
    another instance, in registry order."""
    registry = registry or load_registry()
    configs = _readable_configs(registry)
    taken = {ident: instance_ports(c) for ident, c in configs.items()}
    rows = []
    for inst in registry.instances:
        ports = taken.get(inst.id)
        others = {k: v for k, v in taken.items() if k != inst.id}
        conflicts = tuple(check_port_conflicts(others, ports)) if ports else ()
        rows.append(InstanceRow(inst.id, inst.label, inst.id == registry.default_id,
                                configs.get(inst.id), ports, conflicts))
    return rows


# --- naming -------------------------------------------------------------------


def service_name(instance_id: str) -> str:
    """The systemd unit for an instance, by name: `systemd_scope.server_unit`,
    which is what everything holding a config asks."""
    from types import SimpleNamespace

    from app.shared.systemd_scope import server_unit

    return server_unit(SimpleNamespace(instance_id=validate_instance_id(instance_id)))


def rewrite_instance_path(path_str: str, source_id: str, target_id: str) -> str:
    """Replace *source_id* in a path, as a whole path segment only, so cloning
    `main` does not also rewrite a `/srv/maintenance/` above it."""
    if not path_str:
        return ""
    parts = Path(path_str).parts
    return str(Path(*[target_id if p == source_id else p for p in parts]))
