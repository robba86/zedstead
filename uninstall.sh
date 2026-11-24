#!/bin/sh
set -e

# ------------------------------------------------------------
# zsm uninstaller (uv version)
#
# Local uninstall:
#   ./uninstall.sh
#
# Remote uninstall:
#   curl -fsSL https://raw.githubusercontent.com/robba86/zedstead/main/uninstall.sh -o /tmp/zsm-uninstall.sh && sh /tmp/zsm-uninstall.sh
#
# Download first, then run: `sh -c "$(curl …)"` runs an empty script and
# exits 0 when the download fails.
# ------------------------------------------------------------

INSTALL_DIR="${HOME}/.local/share/zsm"
CONFIG_DIR="${HOME}/.config/zsm"

# ------------------------------------------------------------
# Helper output functions
# ------------------------------------------------------------

info()  { printf '\033[1;34m%s\033[0m\n' "$*"; }
ok()    { printf '\033[1;32m%s\033[0m\n' "$*"; }
warn()  { printf '\033[1;33m%s\033[0m\n' "$*"; }

# Single-keypress prompt. Returns 0 for yes, 1 for no.
# $1 = prompt, $2 = default ("y" or "n")
ask() {
    _prompt="$1"
    _default="${2:-n}"
    printf "%s " "$_prompt"
    if [ -t 0 ]; then
        old_stty=$(stty -g)
        stty -icanon -echo min 1 time 0
        REPLY=$(dd bs=1 count=1 2>/dev/null)
        stty "$old_stty"
        echo ""
    else
        read -r REPLY
    fi
    case "$REPLY" in
        "") [ "$_default" = "y" ] && return 0 || return 1 ;;
        [Yy]) return 0 ;;
        *) return 1 ;;
    esac
}

# Typed prompt for the one step that deletes the admin's data: the word
# "delete" and Enter, nothing less. Returns 0 for delete. A single keypress
# deleted a config dir by a slip, and so did "y" and Enter straight after the
# first question, whose answer is also y (first-hour test, 2026 Q4). A y, a
# yes, a stray Enter and anything else keep the folder. What was typed stays
# in $REPLY for the caller.
ask_delete() {
    printf "%s " "$1"
    REPLY=""
    read -r REPLY || true
    case "$REPLY" in
        [Dd][Ee][Ll][Ee][Tt][Ee]) return 0 ;;
        *) return 1 ;;
    esac
}

# systemd unit files zsm writes. Must stay in step with
# app/shared/systemd_scope.py (unit_dir, server_unit, task_unit_prefix) and
# app/server/panel_unit.py (the panel's unit name).
PANEL_UNIT="zsm-web.service"
USER_UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
# Overridable so a test never looks at, or acts on, the machine's real units.
SYSTEM_UNIT_DIR="${ZSM_SYSTEM_UNIT_DIR:-/etc/systemd/system}"

# Run "$@" as root: directly, or through sudo (which asks on the terminal).
as_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        return 1
    fi
}

# Names of the DayZ server's and the scheduled tasks' units in directory $1,
# on one line. The panel's unit is not among them: it is removed below.
units_in() {
    _found=""
    for _f in "$1"/dayz-server.service "$1"/dayz-server-*.service \
              "$1"/dayz-task*.timer "$1"/dayz-task*.service; do
        if [ -f "$_f" ]; then
            _found="$_found ${_f##*/}"
        fi
    done
    printf '%s' "${_found# }"
}

info "zsm uninstaller (uv)"
echo ""

if ! ask "This will uninstall zsm. Continue? [Y/n]" y; then
    echo "Uninstall cancelled."
    exit 0
fi

# ------------------------------------------------------------
# Step 1: Stop and remove the panel's service
# ------------------------------------------------------------
# zsm-web.service starts zsm from the tool removed in step 2. Left installed
# it stayed enabled at boot and kept running from the deleted files, answering
# 404 to every page (first-hour test, 2026 Q4).

if [ -f "$USER_UNIT_DIR/$PANEL_UNIT" ]; then
    info "Stopping and removing the app's service: $USER_UNIT_DIR/$PANEL_UNIT"
    if ! systemctl --user disable --now "$PANEL_UNIT" >/dev/null 2>&1; then
        warn "Could not reach your systemd user session, so a running app was not stopped."
        warn "It stops at the next reboot, or with: systemctl --user stop $PANEL_UNIT"
    fi
    rm -f "$USER_UNIT_DIR/$PANEL_UNIT" "$USER_UNIT_DIR"/*.wants/"$PANEL_UNIT"
    systemctl --user daemon-reload >/dev/null 2>&1 || true
fi

if [ -f "$SYSTEM_UNIT_DIR/$PANEL_UNIT" ]; then
    info "Stopping and removing the app's service: $SYSTEM_UNIT_DIR/$PANEL_UNIT"
    if as_root systemctl disable --now "$PANEL_UNIT" >/dev/null 2>&1 \
        && as_root rm -f "$SYSTEM_UNIT_DIR/$PANEL_UNIT" \
        && as_root systemctl daemon-reload; then
        :
    else
        warn "That needs root and did not work. The app's service is still installed."
        warn "Remove it as root: systemctl disable --now $PANEL_UNIT && rm -f $SYSTEM_UNIT_DIR/$PANEL_UNIT && systemctl daemon-reload"
    fi
fi

# ------------------------------------------------------------
# Step 2: Remove the uv tool
# ------------------------------------------------------------

if command -v uv >/dev/null 2>&1; then
    info "Removing zsm with uv..."
    uv tool uninstall zsm 2>/dev/null || warn "zsm was not installed as a uv tool (skipping)."
else
    warn "uv not found on PATH; skipping 'uv tool uninstall'."
    warn "If zsm is still on your PATH, install uv and run: uv tool uninstall zsm"
fi

# ------------------------------------------------------------
# Step 3: Remove the cloned source (remote-install location)
# ------------------------------------------------------------

if [ -d "$INSTALL_DIR" ]; then
    info "Removing cloned source: $INSTALL_DIR"
    rm -rf "$INSTALL_DIR"
fi

# ------------------------------------------------------------
# Step 4: Optionally remove user config + cached map images
# ------------------------------------------------------------

if [ -d "$CONFIG_DIR" ]; then
    echo ""
    warn "Config dir holds your server settings, presets and cached maps:"
    warn "  $CONFIG_DIR"
    if ask_delete "Press Enter to keep it. To delete it as well, type delete and press Enter:"; then
        rm -rf "$CONFIG_DIR"
        ok "Config removed."
    elif [ -n "$REPLY" ]; then
        info "Config kept at $CONFIG_DIR (only the word delete removes it)"
    else
        info "Config kept at $CONFIG_DIR"
    fi
fi

# ------------------------------------------------------------
# Step 5: Say what was left alone
# ------------------------------------------------------------
# The DayZ server is not zsm's to delete, and its service starts DayZ
# directly, so it keeps working. Said out loud because a service nobody
# mentioned reads as a leftover: the uninstall used to end "zsm uninstalled."
# with dayz-server.service still installed (first-hour test, 2026 Q4).

echo ""
info "Left alone: the DayZ server folder, SteamCMD and the downloaded mods."

USER_UNITS=$(units_in "$USER_UNIT_DIR")
SYSTEM_UNITS=$(units_in "$SYSTEM_UNIT_DIR")
if [ -n "$USER_UNITS$SYSTEM_UNITS" ]; then
    info "Still installed: the DayZ server's units (its service, and the timers of its scheduled tasks)."
    echo "  dayz-server runs without zsm. A dayz-task unit runs zsm, so it fails until zsm is installed again."
    if [ -n "$USER_UNITS" ]; then
        echo "  In $USER_UNIT_DIR: $USER_UNITS"
        echo "  To remove them: systemctl --user disable --now $USER_UNITS; cd $USER_UNIT_DIR && rm -f $USER_UNITS; systemctl --user daemon-reload"
    fi
    if [ -n "$SYSTEM_UNITS" ]; then
        echo "  In $SYSTEM_UNIT_DIR: $SYSTEM_UNITS"
        echo "  To remove them, as root: systemctl disable --now $SYSTEM_UNITS; cd $SYSTEM_UNIT_DIR && rm -f $SYSTEM_UNITS; systemctl daemon-reload"
    fi
fi

# ------------------------------------------------------------
# Step 6: Done
# ------------------------------------------------------------

echo ""
if command -v zsm >/dev/null 2>&1; then
    warn "'zsm' is still on your PATH. Restart your terminal; if it persists,"
    warn "run: uv tool uninstall zsm"
else
    ok "zsm uninstalled."
fi
