"""DayzConfig dataclass — load/save an instance's config.json (plans/44)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

from app.shared.json_utils import atomic_write_text, load_json
# CONFIG_DIR is no longer read here, but tests patch app.shared.config.CONFIG_DIR
# alongside CONFIG_FILE (conftest.sample_config), so the name stays importable.
from app.shared.paths import CONFIG_DIR, CONFIG_FILE, InstancePaths, instance_root  # noqa: F401
from app.shared.platform import IS_WINDOWS

# DayZ Steam Workshop app id.
_WORKSHOP_APPID = "221100"

# Workshop content locations a regular (desktop) Steam install uses on Linux,
# in priority order. When desktop Steam is already installed, tar-based
# SteamCMD downloads here instead of the bare ~/Steam path it uses on a clean
# server, so the hardcoded ~/Steam default points at an empty/missing dir.
_LINUX_WORKSHOP_CANDIDATES = (
    ".local/share/Steam",  # native desktop Steam
    ".steam/steam",  # legacy / compat symlink target
    ".steam/root",
    "Steam",  # clean tar-SteamCMD default
    ".var/app/com.valvesoftware.Steam/data/Steam",  # flatpak
)


def detect_workshop_content_dir() -> Path | None:
    """Return the first existing, non-empty Workshop content dir for app 221100.

    Scans the locations a desktop Steam install uses on Linux. Returns None
    when nothing is found (e.g. mods not downloaded yet). Windows SteamCMD
    keeps content inside its own dir, so detection is Linux-only.
    """
    if IS_WINDOWS:
        return None
    home = Path.home()
    for base in _LINUX_WORKSHOP_CANDIDATES:
        candidate = home / base / "steamapps" / "workshop" / "content" / _WORKSHOP_APPID
        try:
            if candidate.is_dir() and any(p.is_dir() for p in candidate.iterdir()):
                return candidate
        except OSError:
            continue
    return None



# Fields that describe where a config came from rather than a setting. Never
# written to config.json, ignored if a hand edit puts them there.
TRANSIENT_FIELDS = frozenset({"instance_id"})

# The instance this process acts on (plans/44 D1). The root group sets it from
# `--instance`, or from the registry's default, and `load()` reads it, so the
# dev reload worker and every `zsm` a command spawns act on the same one.
INSTANCE_ENV = "ZSM_INSTANCE"


class ConfigNotFound(FileNotFoundError):
    """`zsm init` has not been run yet.

    A FileNotFoundError subclass so every existing `except FileNotFoundError`
    around a config load keeps working, and a named type so the CLI's top-level
    group can tell "you have not configured zsm" apart from a genuinely
    missing file it was asked to read (`app/__main__.py`).
    """


@dataclass
class DayzConfig:
    server_dir: str = ""
    steamcmd_dir: str = ""
    steam_username: str = ""
    workshop_content_dir: str = ""
    server_name: str = "DayZ Server"
    server_args: str = "-adminLog -netLog -freezeCheck -cpuCount=2 -doLogs"
    server_port: int = 2302
    battleye_path: str = field(default_factory=lambda: "BattlEye" if IS_WINDOWS else "battleye")
    profiles_path: str = "profiles"
    config_path: str = "serverDZ.cfg"
    # Which Steam app the server is (plans/66): "stable" (223350) or
    # "experimental" (1042420, Bohemia's pre-release build). Read it only
    # through steam.server_branch / steam.server_app_id, which turn any other
    # value into "stable".
    server_branch: str = "stable"
    # Which side a value takes when a server update and the admin both changed
    # it (plans/67, per value since plans/71): "mine" keeps the admin's and
    # lists it, "dayz" keeps DayZ's and lists it, "ask" keeps DayZ's until the
    # admin chooses. A file that cannot be merged gets the same choice whole.
    # Read it only through mission_stock.conflict_policy.
    mission_update_conflict: str = "mine"
    check_steam_login: bool = True
    bundle_export_dir: str = ""
    backup_dir: str = ""
    backup_max_count: int = 5
    # Offsite backup (TODO C1). Routing only — the credentials live in
    # <config dir>/rclone.conf, which app/ops/offsite_ops.py writes at 0600
    # and never reads back to a caller. `kind` is informational here; the
    # authority is the `type =` in that file (offsite_ops.read_remote_kind).
    backup_remote_kind: str = ""
    backup_remote_path: str = ""
    backup_remote_auto: bool = True
    rclone_path: str = ""
    session_tracker_enabled: bool = True
    toast_player_connect: bool = True
    toast_restart_warning: bool = True
    toast_doctor_check: bool = True
    toast_version_update: bool = True
    rcon_password: str = ""
    rcon_port: int = 2305
    # Where the BattlEye RCon socket is. Default is unchanged behaviour: the
    # server on this machine. Pointing it at a remote host lets zsm do player
    # management, broadcast and kick/ban against any server that exposes
    # BattlEye RCon — including a rented one (TODO G1b).
    rcon_host: str = "127.0.0.1"
    discord_webhook_url: str = ""
    discord_hook_start: bool = True
    discord_hook_stop: bool = True
    discord_hook_crash: bool = True
    discord_hook_update: bool = True
    discord_hook_wipe: bool = True
    discord_hook_mods: bool = True
    log_cleanup_days: int = 0
    log_cleanup_keep: int = 0
    show_field_tooltips: bool = True
    status_logs_auto_scroll: bool = True
    # The mod list, its flags and its load order are in mods.json, not here
    # (`app/modmgr/modlist.py`, plans/77).
    # Mods set aside because the server did not boot after an update (plans/73):
    # name -> {"build", "time_updated", "at"}. Tells them apart from a mod the
    # admin disabled, so zsm can say when the author has caught up.
    update_triage: dict[str, dict] = field(default_factory=dict)
    # Phase 1: Awareness & Safety
    steam_api_key: str = ""
    mod_update_check_interval: int = 3600
    config_version_max: int = 50
    config_version_enabled: bool = True
    # Steam A2S query port pushed into serverDZ.cfg `steamQueryPort` by
    # sync_server_cfg (container bootstrap / reinstall). 0 = unmanaged: leave
    # whatever serverDZ.cfg already has (the app reads the effective port from
    # serverDZ.cfg via server_status.get_steam_query_port, not from here).
    steam_query_port: int = 0
    # Phase 2: Automation & Control
    scheduler_enabled: bool = True
    scheduled_tasks: list[dict] = field(default_factory=list)
    restart_warning_minutes: list[int] = field(
        default_factory=lambda: [15, 10, 5, 3, 1]
    )
    restart_warning_template: str = "Server restart in {minutes} minute(s)!"
    # Minutes before a restart that new joins are locked over RCon; 0 = off.
    restart_lock_at_minutes: int = 0
    auto_backup_before_restart: bool = True
    # Scheduled `update` task: defer when more than this many players are
    # online. 0 = any player defers it, -1 = never check (TODO C3).
    update_max_players: int = 0
    # After an update that starts the server: seconds to wait for "Player
    # connect enabled" before the boot counts as failed (plans/73 D3).
    post_update_boot_timeout: int = 600
    # Phase 3: Community & Communication
    discord_hook_player_join: bool = True
    discord_hook_player_leave: bool = True
    discord_hook_restart_warning: bool = True
    discord_hook_mod_updates: bool = True
    discord_hook_chat: bool = False
    discord_hook_status: bool = False
    discord_status_message_id: str = ""
    # zedstead.com (TODO F7, plans/57): two opt-ins, both off until the admin turns
    # one on. The ping is an anonymous install count; the listing publishes
    # this server's address on zedstead.com's public server list. The backend
    # address is fixed in app/ops/site_ops.py (ENDPOINT), which does the
    # sending; no setting changes it.
    site_ping: bool = False
    site_listing: bool = False
    # Optional host name shown in the list instead of the bare IP.
    site_address: str = ""
    # Generated together the first time either switch turns on — never at
    # install, so an install that never opts in has no ID at all. The secret
    # authenticates the listing's PUT/DELETE, so knowing someone's install ID
    # is not enough to overwrite or remove their listing. Neither is in the
    # settings registry: GET /api/settings serves registry fields, and the
    # secret must never reach a browser.
    site_install_id: str = ""
    site_secret: str = ""
    chat_log_enabled: bool = True
    # Phase 4: Intelligence & Insights
    economy_analysis_enabled: bool = True
    metrics_enabled: bool = True
    metrics_interval: int = 60
    metrics_retention_days: int = 30
    perf_alert_fps_below: int = 15
    perf_alert_memory_above_mb: int = 8000
    # Phase 1: Mod rollback
    mod_backup_before_update: bool = True
    mod_backup_max_versions: int = 2
    # systemd service scope: "" = auto-resolve (see app.shared.systemd_scope),
    # "system" = /etc/systemd/system + sudo, "user" = ~/.config/systemd/user
    # + `systemctl --user` (no sudo). Docker pins "system" via container-init.
    service_scope: str = ""
    # The crash-loop brake, written into the unit as StartLimitBurst /
    # StartLimitIntervalSec. `Restart=always` + `RestartSec=30` on their own
    # relaunch a server that cannot start, every 30 seconds, for ever and in
    # silence. After this many starts inside the window systemd gives up and
    # leaves the unit `failed`, which is a state the panel can show. Either
    # value at 0 disables the brake and restores the old behaviour.
    # A start zsm makes itself resets the counter, so this never counts an
    # admin restarting a few times in a row — see service_control.start_server.
    restart_limit_burst: int = 3
    restart_limit_interval_seconds: int = 300
    # The unit's `Restart=` / `RestartSec=`. Defaults are exactly what the
    # template hardcoded until 2026 Q3, so an existing install's unit does
    # not change meaning. "on-failure" leaves a clean shutdown alone (a
    # scheduled restart's own stop), "no" never retries. Neither reaches
    # systemd until the unit is regenerated.
    service_restart_policy: str = "always"
    service_restart_seconds: int = 30
    # Names or UIDs to flag wherever they appear in the player stats (TODO
    # D3). Both kinds, because an admin adds whichever they have to hand: a
    # UID off a ban, a name off a chat log.
    player_watchlist: list[str] = field(default_factory=list)
    # Web UI (app/web) — scrypt hash, empty until `zsm web` sets one.
    web_password_hash: str = ""
    # Run the headless notifier (restart warnings, doctor, metrics, mod
    # updates, crash/Discord) inside `zsm web`. Turn off on a second
    # `zsm web` for the same config so the notification loops don't run twice.
    web_notifier_enabled: bool = True
    # Ask GitHub for the latest zsm release, for the panel's update banner
    # (app/shared/version_check.py). Off = no request is made at all.
    version_check_enabled: bool = True
    # Bind host for `zsm web`. "" = auto (0.0.0.0 in
    # Docker/over SSH, else 127.0.0.1). Set to "0.0.0.0" to always expose
    # (web is password-gated) — survives env-stripped shells (tmux, systemd,
    # sudo) where SSH auto-detect fails and the server would bind loopback-only.
    web_bind_host: str = ""
    # Force the `Secure` attribute on the web session cookie even when the
    # request looks like plain HTTP. Normally unnecessary: the cookie is marked
    # Secure automatically whenever the request scheme is https — directly
    # (`zsm web --self-signed` / `--tls-cert`) or behind a reverse proxy that
    # sets X-Forwarded-Proto (see web_trusted_proxies). Set this only for
    # topologies where that header can't be trusted or forwarded.
    #
    # This must never default to True: the common deployment is loopback HTTP,
    # where a Secure cookie is silently dropped by the browser and nobody can
    # log in at all.
    web_force_secure_cookie: bool = False
    # Comma-separated proxy IPs whose X-Forwarded-* headers `zsm web` trusts,
    # or "*" to trust any. Empty = uvicorn's own default, which reads the
    # FORWARDED_ALLOW_IPS environment variable and falls back to loopback only
    # — right for a reverse proxy on the same host. Set this when TLS is
    # terminated on a different machine. Never "*" on an untrusted network:
    # any client could then forge its own scheme and source IP.
    web_trusted_proxies: str = ""
    # Comma-separated host names the panel answers to (plans/68 NET-9). Empty
    # = any, which is every install's behaviour before this setting. Set, a
    # request or WebSocket whose Host header (port stripped) is not listed is
    # refused, so a DNS-rebinding page cannot reach a loopback-bound panel.
    # X-Forwarded-Host is not consulted. The running panel re-reads it from
    # the file (app/web/allowed_hosts.py), so `zsm settings set
    # web_allowed_hosts ""` undoes a typo without a restart.
    web_allowed_hosts: str = ""
    # TLS for `zsm web`. Both must be set to serve HTTPS directly; empty =
    # plain HTTP (the default, correct for a loopback bind or when a reverse
    # proxy terminates TLS). `zsm web --self-signed` does NOT write these —
    # it generates its own pair under CONFIG_DIR/tls/ and leaves the config
    # alone. Point these at a certificate you already have.
    web_tls_cert: str = ""
    web_tls_key: str = ""
    # Serve HTTPS with the generated self-signed pair on every `zsm web`,
    # without passing --self-signed each time. This exists because --self-signed
    # is a per-run flag: a later bare `zsm web` silently drops back to plain
    # HTTP, and the only symptom is the browser no longer warning you.
    #
    # Prefer this over pointing web_tls_cert at CONFIG_DIR/tls/cert.pem — that
    # works, but the config branch never calls the generator, so the pair then
    # stops renewing itself. Mutually exclusive with web_tls_cert/web_tls_key;
    # setting both is a startup error rather than a guess.
    web_self_signed: bool = False
    # Lock every path and program setting against changes from the web UI
    # (plans/58). Off by default, so nothing changes for an install that never
    # turns it on. While on, no web request can repoint server_dir, steamcmd_dir,
    # rclone_path, the launch-command paths, etc. — the point being that an admin
    # handed a web login on a shared box cannot read the zsm source or run a
    # program of their choosing. Only the CLI on the box can change it, including
    # `zsm settings set web_lock_paths false` to lift the lock.
    web_lock_paths: bool = False
    # Where the unpacked DayZ-Expansion-Missions templates live (the folder
    # holding Chernarus/, Enoch/, …). Empty = CONFIG_DIR/expansion-templates,
    # which is where `Download templates` unpacks the GitHub zip. Point this at
    # your own unpacked copy to use one zsm did not fetch — the repo ships no
    # LICENSE file, so its files are never vendored into zsm itself.
    expansion_template_dir: str = ""
    # Which instance this config belongs to (plans/44). Set by `for_instance`
    # from the directory the file was read from; "" is the pre-instances
    # layout, which is every install until plans/44 step 4 migrates it.
    instance_id: str = field(default="", compare=False, repr=False)

    def __post_init__(self) -> None:
        # Expand ~ to the user's home directory in path fields
        if self.server_dir:
            self.server_dir = str(Path(self.server_dir).expanduser())
        if self.steamcmd_dir:
            self.steamcmd_dir = str(Path(self.steamcmd_dir).expanduser())
        if self.workshop_content_dir:
            self.workshop_content_dir = str(Path(self.workshop_content_dir).expanduser())
        if self.backup_dir:
            self.backup_dir = str(Path(self.backup_dir).expanduser())
        if self.bundle_export_dir:
            self.bundle_export_dir = str(Path(self.bundle_export_dir).expanduser())
        if self.expansion_template_dir:
            self.expansion_template_dir = str(Path(self.expansion_template_dir).expanduser())
        if self.web_tls_cert:
            self.web_tls_cert = str(Path(self.web_tls_cert).expanduser())
        if self.web_tls_key:
            self.web_tls_key = str(Path(self.web_tls_key).expanduser())

    # --- server_args helpers ----------------------------------------------

    @property
    def cpu_count(self) -> int:
        """Extract -cpuCount=N from server_args (default 2)."""
        m = re.search(r"-cpuCount=(\d+)", self.server_args)
        return int(m.group(1)) if m else 2

    @cpu_count.setter
    def cpu_count(self, value: int) -> None:
        """Update -cpuCount=N in server_args, appending if absent."""
        if re.search(r"-cpuCount=\d+", self.server_args):
            self.server_args = re.sub(r"-cpuCount=\d+", f"-cpuCount={value}", self.server_args)
        else:
            self.server_args = f"{self.server_args} -cpuCount={value}".strip()

    # --- resolved paths --------------------------------------------------

    @property
    def server_dir_path(self) -> Path:
        return Path(self.server_dir)

    @property
    def server_cfg_path(self) -> Path:
        return self.server_dir_path / self.config_path

    @property
    def steamcmd_path(self) -> Path:
        binary = "steamcmd.exe" if IS_WINDOWS else "steamcmd.sh"
        return Path(self.steamcmd_dir) / binary

    @property
    def steamcmd_sh_path(self) -> Path:
        """Alias for steamcmd_path (backwards compatibility)."""
        return self.steamcmd_path

    @property
    def workshop_dir(self) -> Path:
        if self.workshop_content_dir:
            return Path(self.workshop_content_dir)
        if IS_WINDOWS:
            # Windows SteamCMD downloads within its own directory
            return Path(self.steamcmd_dir) / "steamapps" / "workshop" / "content" / "221100"
        # On a clean server the tar-based SteamCMD downloads to ~/Steam
        # regardless of +force_install_dir. Prefer that when present.
        default = Path.home() / "Steam" / "steamapps" / "workshop" / "content" / "221100"
        if default.is_dir():
            return default
        # Desktop Steam installs download elsewhere (~/.local/share/Steam, …).
        # Auto-detect so existing configs work without re-running init.
        detected = detect_workshop_content_dir()
        return detected if detected is not None else default

    @property
    def workshop_acf_path(self) -> Path:
        """SteamCMD's workshop manifest — ``steamapps/workshop/appworkshop_221100.acf``.

        Two levels above ``workshop_dir`` (which is ``…/workshop/content/221100``).
        """
        return self.workshop_dir.parents[1] / "appworkshop_221100.acf"

    @property
    def battleye_dir(self) -> Path:
        return self.server_dir_path / self.battleye_path

    @property
    def beserver_cfg_path(self) -> Path:
        from app.shared.platform import IS_WINDOWS

        if IS_WINDOWS:
            return self.battleye_dir / "BEServer_x64.cfg"
        return self.battleye_dir / "beserver_x64.cfg"

    @property
    def keys_dir(self) -> Path:
        return self.server_dir_path / "keys"

    @property
    def paths(self) -> InstancePaths:
        """Every per-instance file under the config dir, for *this* config's
        instance, resolved now (a patched `CONFIG_DIR` is honoured)."""
        return InstancePaths(instance_root(self.instance_id))

    # --- persistence ------------------------------------------------------

    @property
    def _file(self) -> Path:
        # A named instance's own file. The legacy layout keeps the module's
        # own CONFIG_FILE, which tests patch.
        return self.paths.config_file if self.instance_id else CONFIG_FILE

    def save(self) -> None:
        # Written back where it was read from.
        target = self._file
        target.parent.mkdir(parents=True, exist_ok=True)
        persisted = {k: v for k, v in asdict(self).items() if k not in TRANSIENT_FIELDS}
        data = json.dumps(persisted, indent=2) + "\n"
        # Atomic write (temp file, fsync, os.replace): a bare write_text()
        # truncates the file first, so a crash mid-write — or two threads
        # saving at once (the notification loop saves off-thread) — leaves a
        # truncated config that load_json() can't parse, losing all settings.
        # Owner-only: it holds the RCon password and the panel's hash. On
        # Windows the rename is tried again while a reader has the file open.
        atomic_write_text(target, data, owner_only=True)

    @classmethod
    def load(cls) -> DayzConfig:
        """The config this process acts on (plans/44 step 4): the instance
        `$ZSM_INSTANCE` names, else the registry's default, else the
        pre-instances `config.json` of an install not migrated yet."""
        from app.server.instances import load_registry

        named = os.environ.get(INSTANCE_ENV, "").strip()
        if named:
            return cls.for_instance(named)
        default = load_registry().default_id
        if default:
            return cls.for_instance(default)
        if not CONFIG_FILE.exists():
            raise ConfigNotFound(
                f"No config found at {CONFIG_FILE}. Run 'zsm init' first."
            )
        return cls._from_file(CONFIG_FILE)

    def reloaded(self) -> DayzConfig | None:
        """This config's own file read again, or None when it is gone.

        For a process that keeps the config it started with, the web above
        all: `load()` would follow `$ZSM_INSTANCE` or the registry's default,
        which is not necessarily the instance this one came from.
        """
        if not self._file.exists():
            return None
        config = self._from_file(self._file)
        config.instance_id = self.instance_id
        return config

    @classmethod
    def for_instance(cls, instance_id: str) -> DayzConfig:
        """Load the config of one named instance (plans/44 §3.1).

        The instance is a property of the config you hold, not a mode of the
        process: two of these side by side resolve two disjoint sets of paths.
        """
        from app.server.instances import validate_instance_id

        ident = validate_instance_id(instance_id)
        path = InstancePaths(instance_root(ident)).config_file
        if not path.exists():
            raise ConfigNotFound(f"No config for instance '{ident}' at {path}.")
        config = cls._from_file(path)
        config.instance_id = ident
        return config

    @classmethod
    def _from_file(cls, path: Path) -> DayzConfig:
        data = load_json(path)
        return cls(**{
            k: v for k, v in data.items()
            if k in cls.__dataclass_fields__ and k not in TRANSIENT_FIELDS
        })

    @classmethod
    def load_or_none(cls) -> DayzConfig | None:
        try:
            return cls.load()
        except FileNotFoundError:
            return None

    # --- migration --------------------------------------------------------

    @classmethod
    def from_env_file(cls, env_path: Path) -> DayzConfig:
        """Import values from an existing .env file."""
        values: dict[str, str] = {}
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()

        return cls(
            server_dir=str(env_path.parent),
            steamcmd_dir=values.get("STEAMCMD_DIR", ""),
            steam_username=values.get("STEAM_USERNAME", ""),
            server_name=values.get("SERVER_NAME", "DayZ Server"),
            server_args=values.get("SERVER_ARGS", "-adminLog -netLog -freezeCheck -cpuCount=2"),
            server_port=int(values.get("SERVER_PORT", "2302")),
            battleye_path=values.get("BATTLEYE_PATH", "BattlEye" if IS_WINDOWS else "battleye"),
            profiles_path=values.get("PROFILES_PATH", "profiles"),
            config_path=values.get("CONFIG_PATH", "serverDZ.cfg"),
        )
