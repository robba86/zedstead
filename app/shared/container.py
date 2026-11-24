"""Container bootstrap helpers — sync zsm config from environment variables.

Used by ``zsm container-init`` (the systemd bootstrap oneshot). This is the one
spot that maps the container's ``SERVER_*`` env vars onto ``DayzConfig`` fields,
so the rest of the app stays Docker-agnostic.

The critical property is *merge, don't clobber*: a container recreate must
preserve everything configured through the web UI (scheduled tasks, mod load order,
Discord webhooks, restart warnings). Only the env-mapped server settings are
overwritten, and only when their env var is actually set to a non-empty value.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import string
from pathlib import Path
from typing import Iterable, Mapping

from app.shared.config import DayzConfig
from app.shared.platform import IS_DOCKER

# Env var -> (DayzConfig field, caster). Mirrors the compose .env surface.
_ENV_MAP: dict[str, tuple[str, type]] = {
    "SERVER_DIR": ("server_dir", str),
    "STEAMCMD_DIR": ("steamcmd_dir", str),
    "STEAM_USERNAME": ("steam_username", str),
    "SERVER_NAME": ("server_name", str),
    "SERVER_PORT": ("server_port", int),
    "STEAM_QUERY_PORT": ("steam_query_port", int),
    "RCON_PASSWORD": ("rcon_password", str),
    "RCON_PORT": ("rcon_port", int),
    "SERVER_ARGS": ("server_args", str),
    "SERVER_BRANCH": ("server_branch", str),
    "PROFILES_PATH": ("profiles_path", str),
    "BATTLEYE_PATH": ("battleye_path", str),
    "CONFIG_PATH": ("config_path", str),
    "BACKUP_DIR": ("backup_dir", str),
    "BUNDLE_EXPORT_DIR": ("bundle_export_dir", str),
}

_FIELDS = frozenset(f for f, _c in _ENV_MAP.values())

# First-boot defaults for a fresh container (no config.json + no env override).
# These are path fields the app leaves empty by default but the container needs.
_CONTAINER_DEFAULTS: dict[str, str] = {
    "server_dir": "/home/zsm/server",
    "steamcmd_dir": "/home/zsm/steamcmd",
    "backup_dir": "/home/zsm/backups",
    "bundle_export_dir": "/home/zsm/bundles",
}

# The RCon password `.env.example` shipped until plans/68 (NET-3). Anyone can
# guess it, and BattlEye RCon (kick, ban, #shutdown) listens on every interface
# unless RConIP says otherwise, so the bootstrap never keeps it: not from the
# env, not from config.json, not from the BattlEye cfg.
PLACEHOLDER_RCON_PASSWORDS = frozenset({"changeme"})

# Where an admin reads a password the bootstrap generated. The value itself is
# never printed: the bootstrap's output is the container log.
RCON_PASSWORD_WHERE = (
    "Read it in the app under Configs -> BattlEye (admins only) once the "
    "server is installed."
)


def is_placeholder_rcon_password(value: str) -> bool:
    return value.strip().lower() in PLACEHOLDER_RCON_PASSWORDS


def new_rcon_password() -> str:
    """24 random letters and digits (~143 bits). Alphanumeric only: the
    BattlEye cfg is ``key value`` per line, and RCon tools differ on what else
    they accept."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(24))


def _env_values(env: Mapping[str, str]):
    """Yield ``(var, field, value)`` for each mapped variable *env* sets.

    The one reading of the env: unset, empty, unparseable and the
    ``changeme`` placeholder are skipped (see ``settle_rcon_password``).
    """
    for var, (field_name, caster) in _ENV_MAP.items():
        raw = env.get(var)
        if raw is None or raw == "":
            continue
        if var == "RCON_PASSWORD" and is_placeholder_rcon_password(raw):
            continue
        try:
            value = caster(raw)
        except (TypeError, ValueError):
            continue
        yield var, field_name, value


def apply_env_overrides(
    config: DayzConfig, env: Mapping[str, str] | None = None
) -> list[str]:
    """Overlay mapped env vars onto ``config`` in place.

    Only fields whose env var is set to a non-empty (and, for ints, parseable)
    value are touched; everything else is preserved. Returns the names of the
    fields that actually changed. ``RCON_PASSWORD=changeme`` counts as unset
    (see ``settle_rcon_password``).
    """
    env = os.environ if env is None else env
    changed: list[str] = []
    for _var, field_name, value in _env_values(env):
        if getattr(config, field_name) != value:
            setattr(config, field_name, value)
            changed.append(field_name)
    return changed


def env_set_fields(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """``{field: env var}`` for every field *env* sets at each container start."""
    env = os.environ if env is None else env
    return {field_name: var for var, field_name, _value in _env_values(env)}


def record_env_fields(config: DayzConfig, fields: dict[str, str]) -> None:
    """Write which fields the environment sets, for the web UI to lock.

    The panel runs under systemd, which does not hand it the container's
    environment, so the bootstrap's view is recorded in the instance dir.
    None set removes the record.
    """
    from app.shared.json_utils import atomic_write_text

    path = config.paths.docker_env
    if not fields:
        path.unlink(missing_ok=True)
        return
    atomic_write_text(path, json.dumps(fields, indent=2, sort_keys=True) + "\n")


def env_locked_fields(config: DayzConfig) -> dict[str, str]:
    """``{field: env var}`` the last container start set from the environment.

    An edit to one of these from the web is undone at the next start, so the
    web shows them read-only. Empty outside a container, or for a record that
    cannot be read.
    """
    try:
        data = json.loads(config.paths.docker_env.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items()
            if k in _FIELDS and isinstance(v, str) and _ENV_MAP.get(v, ("",))[0] == k}


def env_lock_note(var: str) -> str:
    """Why a field set from the environment is read-only, and where to change it."""
    return (f"Set by the container's {var} environment variable at every start. "
            "Change it in the compose .env and recreate the container.")


def env_lock_problem(locked: Mapping[str, str], changed: Iterable[str],
                     labels: Mapping[str, str] | None = None) -> str | None:
    """The refusal for a web save that changes something the environment sets.

    *locked* maps a key to its env var (``env_locked_fields``, or a server
    file's keys derived from it); *changed* is what the save would change.
    """
    hits = [k for k in changed if k in locked]
    if not hits:
        return None
    labels = labels or {}
    names = ", ".join(f"{labels.get(k, k)} ({locked[k]})" for k in hits)
    return (f"{names}: set by the container's environment at every start, so a change "
            "here would be undone. Change it in the compose .env and recreate the container.")


def sync_server_cfg(config: DayzConfig, notes: list[str] | None = None) -> dict[str, str]:
    """Push .env-driven values that live in serverDZ.cfg from the zsm config.

    Writes ``hostname`` (from ``SERVER_NAME`` — the name players actually see
    in the server browser) and ``steamQueryPort`` (from ``STEAM_QUERY_PORT``;
    ``config.steam_query_port == 0`` means unmanaged — the cfg is left alone).

    A name ``write_cfg_value`` refuses (a line break or a double quote,
    plans/68 LB-6) is skipped, not raised: this runs at every container start,
    and the server must still come up. The reason goes into *notes*.

    Returns the cfg keys actually written mapped to their new values; empty
    dict when serverDZ.cfg doesn't exist yet (server not installed) or
    everything already matches.
    """
    from app.parsers.servercfg import cfg_value_problem, read_cfg_value, write_cfg_value

    cfg_path = config.server_cfg_path
    if not cfg_path.exists():
        return {}
    written: dict[str, str] = {}
    name = config.server_name
    problem = cfg_value_problem(name, quoted=True) if name else None
    if problem:
        if notes is not None:
            notes.append(f"serverDZ.cfg hostname left as it was: the server name (SERVER_NAME) {problem}.")
    elif name and read_cfg_value(cfg_path, "hostname") != name:
        write_cfg_value(cfg_path, "hostname", name, quoted=True)
        written["hostname"] = name
    written.update(sync_query_port(config))
    return written


def sync_query_port(config: DayzConfig) -> dict[str, str]:
    """Write ``config.steam_query_port`` into serverDZ.cfg when zsm manages it.

    0 means unmanaged and leaves the cfg alone, which is every install that
    predates it outside Docker. Set by the container's ``STEAM_QUERY_PORT``
    and by `zsm instances add`/`clone`, whose free port lives nowhere else
    until the server is installed (plans/44 step 5). Returns what it wrote.
    """
    from app.parsers.servercfg import read_cfg_value, write_cfg_value

    cfg_path = config.server_cfg_path
    port = config.steam_query_port
    if port <= 0 or not cfg_path.exists() or read_cfg_value(cfg_path, "steamQueryPort") == str(port):
        return {}
    write_cfg_value(cfg_path, "steamQueryPort", str(port))
    return {"steamQueryPort": str(port)}


def sync_battleye_cfg(config: DayzConfig, notes: list[str] | None = None) -> int | None:
    """Write the RCON password/port from config into BattlEye's beserver cfg.

    The DayZ server's BattlEye reads ``RConPassword``/``RConPort`` from
    beserver_x64.cfg; ``config.rcon_*`` (from RCON_PASSWORD/RCON_PORT) is only
    what zsm's own RCON *client* uses. Without this the two disagree and RCON
    never connects — no restart warnings, player management, or chat logging.

    No-op (returns None) when no RCON password is set. Merges into any existing
    beserver cfg so unrelated keys are preserved. Returns the RCon port written.

    A password ``write_beserver_cfg`` would refuse (a line break, plans/68
    LB-6) is not written and the file keeps its own; a file holding such a
    value already is not written at all. Neither raises, since this runs at
    every container start; the reason goes into *notes*, never the password.
    """
    from app.parsers.battleye_cfg import (
        BeserverValueError,
        beserver_value_problem,
        read_beserver_cfg,
        write_beserver_cfg,
    )

    if not config.rcon_password:
        return None
    path = config.beserver_cfg_path
    data = read_beserver_cfg(path) if path.exists() else {}
    problem = beserver_value_problem(config.rcon_password)
    if problem:
        if notes is not None:
            notes.append(
                f"beserver_x64.cfg RConPassword left as it was: the RCon password (RCON_PASSWORD) {problem}."
            )
    else:
        data["RConPassword"] = config.rcon_password
    data["RConPort"] = str(config.rcon_port)
    data.setdefault("RestrictRCon", "0")
    try:
        write_beserver_cfg(path, data)
    except BeserverValueError as e:
        if notes is not None:
            notes.append(f"beserver_x64.cfg was not written: {e}")
        return None
    return config.rcon_port


def settle_rcon_password(config: DayzConfig, env: Mapping[str, str]) -> list[str]:
    """Make sure *config* has an RCon password nobody can guess.

    Runs after ``apply_env_overrides``, which already took a real
    ``RCON_PASSWORD``. When config.json still has none, or has the old
    ``changeme``, the password in the BattlEye cfg is kept if it is a real one
    (an admin who set it by hand there loses nothing), and otherwise a random
    one is generated. ``sync_battleye_cfg`` then writes it over the cfg's.

    Returns markup-free lines for the bootstrap log. They never contain the
    password: they say what happened and where to read it.
    """
    notes: list[str] = []
    raw = env.get("RCON_PASSWORD") or ""
    if raw and is_placeholder_rcon_password(raw):
        notes.append(
            "RCON_PASSWORD in .env is the old placeholder 'changeme', which "
            "anyone can guess, so it is ignored. Leave it empty, or set a long "
            "random password."
        )
    current = config.rcon_password
    if current and not is_placeholder_rcon_password(current):
        return notes
    from app.parsers.battleye_cfg import live_beserver_cfg, read_beserver_cfg

    try:
        in_cfg = read_beserver_cfg(live_beserver_cfg(config)).get("RConPassword", "")
    except (OSError, UnicodeDecodeError):
        in_cfg = ""
    if in_cfg and not is_placeholder_rcon_password(in_cfg):
        config.rcon_password = in_cfg
        notes.append("RCon password: using the one already in the BattlEye cfg.")
        return notes
    config.rcon_password = new_rcon_password()
    if current or in_cfg:
        notes.append(
            "RCon password was the placeholder 'changeme': generated a random one. "
            "RCon tools set up with 'changeme' need the new one. " + RCON_PASSWORD_WHERE
        )
    else:
        notes.append(
            "RCon password was not set: generated a random one. " + RCON_PASSWORD_WHERE
        )
    return notes


def sync_config_from_env(
    env: Mapping[str, str] | None = None,
    notes: list[str] | None = None,
) -> tuple[DayzConfig, bool, list[str]]:
    """Create-or-merge the zsm config from container env vars, then save it.

    First boot (no config.json): build a fresh config from container defaults +
    env. Later boots: load the persisted config and overlay only the env-mapped
    fields, so state configured in the web UI survives a container recreate.

    Returns ``(config, created, changed_fields)``. What ``settle_rcon_password``
    has to say is appended to *notes* when the caller passes a list.
    """
    env = os.environ if env is None else env
    existing = DayzConfig.load_or_none()
    created = existing is None
    config = existing if existing is not None else DayzConfig(**_CONTAINER_DEFAULTS)
    changed = apply_env_overrides(config, env)
    settled = settle_rcon_password(config, env)
    if notes is not None:
        notes.extend(settled)
    # The container runs systemd as PID 1 with NOPASSWD sudo — user-scope
    # services are unsupported here. Pin system scope so a stale/user-set value
    # can't route container service management through `systemctl --user`. Not
    # reported in `changed` (that tracks env-driven overrides, not this pin).
    config.service_scope = "system"
    if created:
        from app.server.instances import save_new_config

        save_new_config(config)
    else:
        config.save()
    record_env_fields(config, env_set_fields(env))
    return config, created, changed


# --- what a container recreate keeps ----------------------------------------

MOUNTINFO = Path("/proc/self/mountinfo")

# A mount of one of these goes with the container too.
_RAM_FSTYPES = frozenset({"tmpfs", "ramfs"})

DOCKER_DOCS = "https://zedstead.com/docs/docker/"


def read_mounts(mountinfo: Path | None = None) -> dict[str, str]:
    """``{mount point: fs type}`` from ``/proc/self/mountinfo``. A later mount
    on the same point hides an earlier one, as in the kernel. Empty when the
    file cannot be read (not Linux)."""
    try:
        text = (mountinfo or MOUNTINFO).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    mounts: dict[str, str] = {}
    for line in text.splitlines():
        head, sep, tail = line.partition(" - ")
        fields, fstype = head.split(), tail.split()[:1]
        if sep and len(fields) >= 5 and fstype:
            # The kernel writes a space in a path as \040.
            point = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), fields[4])
            mounts[point] = fstype[0]
    return mounts


def lost_on_recreate(path: str | Path, mounts: Mapping[str, str] | None = None) -> bool:
    """Whether a container recreate deletes *path*: it is on the container's
    own root filesystem or a RAM mount, not on a volume or a bind mount.
    Reads the real mounts, not the compose file, so a mount added in a
    docker-compose.override.yml counts.

    False outside a container and when the mounts cannot be read: a guess
    would warn about every path.
    """
    if not IS_DOCKER:
        return False
    mounts = read_mounts() if mounts is None else mounts
    if not mounts:
        return False
    try:
        target = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    for point in (target, *target.parents):
        fstype = mounts.get(str(point))
        if fstype is not None:
            return str(point) == "/" or fstype in _RAM_FSTYPES
    return False


def server_dir_volume_problem(server_dir: str, mounts: Mapping[str, str] | None = None) -> str | None:
    """Why *server_dir* is deleted with the container, or None.

    A second instance's server dir in Docker was inside the container and
    gone at the next recreate, with no warning anywhere.
    Asked by `instances add`/`clone` (a note) and by doctor's
    "Server volume" row.
    """
    if not server_dir or not lost_on_recreate(server_dir, mounts):
        return None
    return (
        f"{server_dir} is on no volume, so it is inside the container and is deleted with "
        "it at the next recreate or rebuild (docker compose up --force-recreate), with the "
        "server's world and every base on it. Mount a host folder there in a "
        f"docker-compose.override.yml and recreate the container first: {DOCKER_DOCS}"
    )
