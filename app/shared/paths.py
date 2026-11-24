"""Path constants and helpers for zsm."""

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from app.shared.platform import IS_WINDOWS

INSTALL_DIR = Path(__file__).resolve().parent.parent

if IS_WINDOWS:
    _APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "zsm"
    CONFIG_DIR = _APP_DIR
else:
    CONFIG_DIR = Path.home() / ".config" / "zsm"

CONFIG_FILE = CONFIG_DIR / "config.json"


# --- machine-wide, resolved at call time -------------------------------------
#
# plans/44 (TODO E1): a path read through one of these functions follows
# `app.shared.paths.CONFIG_DIR` when it is patched, which a `CONFIG_DIR / ...`
# bound at import time in another module never does. Only machine-wide paths
# belong here (plans/44 §2 Q2); per-instance ones hang off the config you hold,
# so the call itself says which side of the split a path is on.


def config_dir() -> Path:
    """The zsm config directory, read now rather than at import."""
    return CONFIG_DIR


def secure_config_dir() -> None:
    """Make the config dir owner-only: create it 0700, or tighten one that
    other users can read.

    The root group calls this before anything else runs, so every file a
    command writes afterwards lands inside it. Its own files are mostly 0600
    already; the directory keeps other local users from listing instance,
    backup and bundle names, and from reading a file inside that is not.

    Best effort: a directory owned by someone else, or a filesystem that
    refuses chmod, is left as it is, quietly. On Windows it does nothing:
    `%LOCALAPPDATA%` is per-user by its ACL, and chmod there only toggles
    the read-only flag.
    """
    if IS_WINDOWS:
        return
    path = config_dir()
    try:
        # `mode` applies to the last directory only; parents (~/.config)
        # get the usual mode.
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            os.chmod(path, mode & ~0o077)
    except OSError:
        pass


def instances_file() -> Path:
    """`instances.json`: the list of instances and the default one."""
    return config_dir() / "instances.json"


def instances_dir() -> Path:
    """Where each named instance keeps its own `config.json` and data."""
    return config_dir() / "instances"


def maps_dir() -> Path:
    """The admin's own map images, shared by every instance."""
    return config_dir() / "maps"


def locations_dir() -> Path:
    """User location data for modded maps, keyed by terrain like `maps/`."""
    return config_dir() / "locations"


def rclone_conf() -> Path:
    """The rclone config zsm writes: one set of offsite credentials per box."""
    return config_dir() / "rclone.conf"


def workshop_ids_file() -> Path:
    """The Workshop IDs zsm learned (plans/77): a name -> ID fact holds for every instance."""
    return config_dir() / "workshop-ids.json"


def install_id_file() -> Path:
    """A random id made once per config dir: the browser keys its cached
    jobs on it, so a reinstall shows none of the old install's jobs."""
    return config_dir() / "install-id"


def offsite_known_hosts() -> Path:
    """The SFTP host keys the admin confirmed for the offsite remote. Beside
    `rclone.conf` because it belongs to that remote, which is one per box."""
    return config_dir() / "offsite_known_hosts"


def instance_root(instance_id: str) -> Path:
    """The directory an instance's per-instance files live in.

    ``""`` is the pre-instances layout — everything directly in the config
    dir — which is where every install lives until plans/44 step 4 migrates
    it, so a config that names no instance keeps reading today's paths.
    """
    return instances_dir() / instance_id if instance_id else config_dir()


# --- per instance ------------------------------------------------------------


@dataclass(frozen=True)
class InstancePaths:
    """Every per-instance file under the config dir (plans/44 §2 Q2).

    Reached as ``config.paths``, never built from a module constant: two
    configs for two instances give two disjoint sets, which a name bound at
    import time cannot. Machine-wide files are the functions above.
    """

    root: Path

    @property
    def config_file(self) -> Path:
        return self.root / "config.json"

    @property
    def docker_env(self) -> Path:
        # Which config fields the container's environment sets at each start
        # (`container.record_env_fields`); the web shows those read-only.
        return self.root / "docker-env.json"

    # Telemetry about this server; merged across servers every graph is wrong.
    @property
    def metrics_db(self) -> Path:
        return self.root / "metrics.sqlite"

    @property
    def session_log(self) -> Path:
        return self.root / "player_sessions.jsonl"

    @property
    def chat_log(self) -> Path:
        return self.root / "chat_log.jsonl"

    @property
    def ban_metadata(self) -> Path:
        return self.root / "ban_metadata.json"

    # "What changed since this server last started."
    @property
    def start_snapshot(self) -> Path:
        return self.root / "last_start_snapshot.json"

    @property
    def baseline_snapshot(self) -> Path:
        return self.root / "baseline_snapshot.json"

    # The sha256 of the server unit zsm last installed (plans/78 D2).
    @property
    def installed_unit_hash(self) -> Path:
        return self.root / "installed_unit.sha256"

    # Keyed to this instance's mission files.
    @property
    def versions_dir(self) -> Path:
        return self.root / "versions"

    @property
    def views_dir(self) -> Path:
        return self.root / "views"

    @property
    def custom_xml_dir(self) -> Path:
        return self.root / "custom-xml"

    # Written once a merge downloaded TraderPlus_types.xml from GitHub, so a
    # merge never brings back a file the admin deleted
    # (`trader_common.ensure_upstream_defaults`).
    @property
    def traderplus_types_fetched(self) -> Path:
        return self.root / "traderplus-types-fetched"

    # The mission files as they were before the update pipeline's last merge,
    # one generation, so its undo puts back exactly that (app/xml/xml_undo.py).
    @property
    def xml_undo_dir(self) -> Path:
        return self.root / "xml-undo"

    # One server's data.
    @property
    def backups_dir(self) -> Path:
        return self.root / "backups"

    @property
    def bundles_dir(self) -> Path:
        return self.root / "bundles"

    @property
    def spawn_test_dir(self) -> Path:
        return self.root / "spawn-test"

    # The stock mission files' hashes as this server's last download left
    # them, and the copies held around a download (plans/67).
    @property
    def mission_stock_dir(self) -> Path:
        return self.root / "mission-stock"

    # Q2 calls presets arguable and settles on per instance: promoting them to
    # machine-wide later is easy, splitting a shared set later is not.
    @property
    def presets_dir(self) -> Path:
        return self.root / "presets"

    # The mod set differs per server.
    @property
    def mod_backups_dir(self) -> Path:
        return self.root / "mod_backups"

    @property
    def mods_json(self) -> Path:
        return self.root / "mods.json"

    # Written when a Quick setup finishes. Until then the page stays offered,
    # whichever button installed the server (quick_setup.py::setup_offered).
    @property
    def quick_setup_done(self) -> Path:
        return self.root / "quick_setup_done"

    # The mods Quick setup's "Turn off these mods" turned off, as a JSON list,
    # so "Turn them on again" turns on the same ones (quick_setup.py::mods_off).
    @property
    def quick_setup_mods_off(self) -> Path:
        return self.root / "quick_setup_mods_off.json"

    @property
    def mod_updates(self) -> Path:
        return self.root / "mod_updates.json"

    # How this server's last boot after an update went (plans/73).
    @property
    def post_update(self) -> Path:
        return self.root / "post_update.json"

    # Not in plans/44's Q2 table; classified afterwards.
    # The zedstead.com install id and listing key live in config.json, so the
    # state of pinging and publishing them follows the instance too.
    @property
    def site_state(self) -> Path:
        return self.root / "zsm-app.json"

    # Windows task XML, generated from this instance's scheduled tasks.
    @property
    def schtasks_dir(self) -> Path:
        return self.root / "schtasks"
