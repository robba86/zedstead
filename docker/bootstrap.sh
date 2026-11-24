#!/usr/bin/env bash
# systemd oneshot bootstrap for the zsm DayZ container. Runs as root (PID 1 is
# systemd). Fixes bind-mount ownership, seeds config from env, installs the
# real dayz-server.service (+ per-task .timer units) so the server is manageable
# from the web UI/CLI exactly like a native Ubuntu + systemd install. Does NOT
# start the server unless START_SERVER=1 — by default you drive it yourself.
#
# Not `set -e`: every step is best-effort with an explicit `|| true`, so one
# failing step (e.g. no Steam token yet) doesn't abort the whole boot.
set -uo pipefail

APP_USER=zsm
HOME_DIR=/home/zsm

# --- pull the container env back in from PID 1 -------------------------------
# `docker run -e` / compose env_file put SERVER_NAME, SERVER_PORT, RCON_*, ...
# on systemd (PID 1), but systemd does NOT forward its environment to the
# services it starts. Re-export the zsm-relevant vars from PID 1 so
# `zsm container-init` can see them. Whitelisted by prefix so we don't clobber
# this script's own PATH/HOME with PID 1's.
if [ -r /proc/1/environ ]; then
    while IFS= read -r -d '' kv; do
        case "$kv" in
            SERVER_*=*|STEAMCMD_DIR=*|STEAM_USERNAME=*|STEAM_QUERY_PORT=*|RCON_*=*|PROFILES_PATH=*|\
            BATTLEYE_PATH=*|CONFIG_PATH=*|BACKUP_DIR=*|BUNDLE_EXPORT_DIR=*|\
            ZSM_*=*|START_SERVER=*)
                export "$kv"
                ;;
        esac
    done < /proc/1/environ
fi

SERVER_DIR="${SERVER_DIR:-$HOME_DIR/server}"
CONFIG_DIR="$HOME_DIR/.config/zsm"
BACKUP_DIR="${BACKUP_DIR:-$HOME_DIR/backups}"
BUNDLE_EXPORT_DIR="${BUNDLE_EXPORT_DIR:-$HOME_DIR/bundles}"

# Run a zsm command as the unprivileged app user with the right identity env.
# `runuser` (no -l) keeps the SERVER_* vars we exported above; `env` forces the
# HOME/USER/PATH that make zsm resolve its config dir + binary correctly (PID 1
# would otherwise leak root's HOME).
run_as_zsm() {
    runuser -u "$APP_USER" -- env \
        HOME="$HOME_DIR" USER="$APP_USER" LOGNAME="$APP_USER" \
        PATH="$HOME_DIR/.local/bin:/usr/local/bin:/usr/bin:/bin" \
        "$@"
}

echo "[zsm-bootstrap] preparing container..."

# 1. Fix bind-mount ownership — Docker creates missing mount sources as root.
#    config + server chowned recursively (zsm rewrites small files there); the
#    Steam dir (many GB) top-level only (its contents are zsm-written).
mkdir -p "$CONFIG_DIR" "$SERVER_DIR" "$HOME_DIR/Steam" "$BACKUP_DIR" "$BUNDLE_EXPORT_DIR"
chown -R "$APP_USER:$APP_USER" "$CONFIG_DIR" "$SERVER_DIR"
chown "$APP_USER:$APP_USER" "$HOME_DIR/Steam" "$BACKUP_DIR" "$BUNDLE_EXPORT_DIR"

# 2. Create-or-merge config from env. Preserves app-configured fields (scheduled
#    tasks, mods, Discord, restart warnings) across a container recreate.
if [ "${ZSM_WRITE_CONFIG:-1}" = "1" ]; then
    run_as_zsm zsm container-init || echo "[zsm-bootstrap] container-init failed (continuing)"
fi

# 3. Install the server (idempotent; installs only when a Steam token
#    is cached and the server isn't present yet).
if [ "${ZSM_SETUP_ON_BOOT:-1}" = "1" ]; then
    run_as_zsm zsm setup || true
fi

# 3b. Push .env settings that live in the server's OWN config files now that
#     they're installed: serverDZ.cfg `hostname` (SERVER_NAME) + `steamQueryPort`
#     (STEAM_QUERY_PORT) + beserver_x64.cfg `RConPassword`/`RConPort` (RCON_*, so
#     the server's BattlEye matches zsm's RCON client). Gated by the same
#     ZSM_WRITE_CONFIG flag as container-init:
#     0 means "hands off", so we don't rewrite manually-managed files. No-op if
#     a target file isn't present.
if [ "${ZSM_WRITE_CONFIG:-1}" = "1" ]; then
    run_as_zsm zsm sync-server-cfg || true
fi

# Server not installed yet -> stop here. The container stays alive (systemd is
# PID 1) so you can fix it and recreate.
#
# Two DISTINCT failures, deliberately reported apart — they were one message
# before, and the merged wording ("no Steam token yet") sent an admin whose
# token was fine hunting for a login problem:
#
#  1. missing   — `zsm setup` never installed it (usually no cached token).
#     Tested with `-e`, matching setup's own check
#     (app/commands/setup.py: `(config.server_dir_path / binary).exists()`),
#     so a disagreement between the two can only mean the PATHS differ.
#  2. present but not executable — `-e` true, `-x` false. Almost always the
#     bind-mounted host dir sitting on a **noexec** filesystem: `access(X_OK)`
#     returns EACCES on MNT_NOEXEC even for root, so `-x` is what detects it,
#     and `ls -l` still shows `rwxrwxr-x`, hiding the cause. Left unreported
#     the unit installs fine and then dies on every start with exit 126
#     (`cannot execute: Permission denied`).
#
# Case 2 still installs the service below — the unit is correct, the mount is
# not — but fixing it does need a container recreate: mount flags are per mount
# point, so remounting the host source with `exec` does NOT propagate into the
# bind mount this container already holds.
SERVER_BIN=""
for _bin in "$SERVER_DIR/DayZServer" "$SERVER_DIR/DayZServer_x64.exe"; do
    [ -e "$_bin" ] && { SERVER_BIN="$_bin"; break; }
done

if [ -z "$SERVER_BIN" ]; then
    echo "------------------------------------------------------------------"
    echo "[zsm-bootstrap] DayZServer not found in: $SERVER_DIR"
    echo "Skipping service install — the server is not manageable yet."
    echo "Usually a missing Steam token; cache one, then recreate:"
    echo "  docker compose exec -u zsm dayz zsm steam-login"
    echo "  docker compose up -d --force-recreate"
    echo "If 'zsm setup' above said the server IS installed, the paths"
    echo "disagree — compare with: zsm settings get server_dir"
    echo "------------------------------------------------------------------"
    exit 0
fi

if [ ! -x "$SERVER_BIN" ]; then
    echo "------------------------------------------------------------------"
    echo "[zsm-bootstrap] WARNING: $SERVER_BIN is not executable."
    echo "The service will install but every start will fail with exit 126"
    echo "(cannot execute: Permission denied)."
    echo "Almost always a noexec mount on the host dir behind this volume:"
    echo "  findmnt -T $SERVER_DIR -o TARGET,SOURCE,FSTYPE,OPTIONS"
    echo "Remount it with exec (and fix /etc/fstab), or move the data to a"
    echo "local ext4/xfs filesystem — a network share often cannot exec."
    echo "Then recreate the container: docker compose up -d --force-recreate"
    echo "(a restart is not enough — this bind mount already carries noexec)."
    echo "------------------------------------------------------------------"
fi

# 4. Install the systemd service unit + scheduled-task timers so the server is
#    manageable from the web UI / CLI (Start/Stop, `zsm start`, scheduler) —
#    but DON'T start it. `install` (not `enable`) so nothing auto-starts on boot;
#    reinstalled from config every boot, so they survive a container recreate.
run_as_zsm zsm -y service generate || true
run_as_zsm zsm -y service install || true
run_as_zsm zsm service install-timers || true

# 5. Auto-start is opt-in. Default (START_SERVER != 1): stay idle and let the
#    admin drive from the web UI (`docker compose exec -u zsm dayz zsm web`)
#    or `zsm start`. Set START_SERVER=1 for a headless box that boots straight
#    into a running, freshly-prepped server.
if [ "${START_SERVER:-0}" != "1" ]; then
    echo "------------------------------------------------------------------"
    echo "[zsm-bootstrap] ready — DayZ server NOT auto-started (START_SERVER=0)."
    echo "Manage it from the app:"
    echo "  docker compose exec -u zsm dayz zsm web     # browser UI: Start / Stop"
    echo "  docker compose exec -u zsm dayz zsm start   # or one-off CLI"
    echo "------------------------------------------------------------------"
    exit 0
fi

# START_SERVER=1: prep pipeline (SteamCMD update, mods, XML merge) then launch.
if [ "${ZSM_UPDATE_ON_BOOT:-1}" = "1" ]; then
    run_as_zsm zsm -y run all || true
fi
systemctl start dayz-server.service || echo "[zsm-bootstrap] failed to start dayz-server.service"
echo "[zsm-bootstrap] done."
