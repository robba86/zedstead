#!/bin/sh
set -e

# ------------------------------------------------------------
# zsm installer (uv version)
#
# Local install:
#   ./install.sh
#
# Remote install:
#   curl -fsSL https://raw.githubusercontent.com/robba86/zedstead/main/install.sh -o /tmp/zsm-install.sh && sh /tmp/zsm-install.sh
#
# Unattended (cloud-init, provisioning scripts):
#   curl -fsSL .../install.sh -o /tmp/zsm-install.sh && sh /tmp/zsm-install.sh --yes
#
# Download first, then run. `sh -c "$(curl …)"` expands to `sh -c ""` when the
# download fails: an empty script runs nothing and exits 0, so a provisioning
# script branching on the codes below reads a 404 as a successful install.
# Measured in 2026 Q3 — curl printed "(22) ... error: 404" and $? was 0.
#
# Exit codes (stable contract — provisioning scripts branch on these):
#   0  installed successfully (or --dry-run / --check-deps with nothing missing)
#   1  generic failure (uv install failed, clone failed, ...)
#   2  usage error (unknown flag, or a flag missing its argument)
#   3  cancelled (user answered n, or no tty and no --yes)
#   4  missing OS dependencies, and you declined to install them
#   5  OS packages could not be installed (no root, no sudo, or apt-get failed)
# ------------------------------------------------------------

# ------------------------------------------------------------
# Helper output functions
# ------------------------------------------------------------

info()  { printf '\033[1;34m%s\033[0m\n' "$*"; }
ok()    { printf '\033[1;32m%s\033[0m\n' "$*"; }
warn()  { printf '\033[1;33m%s\033[0m\n' "$*"; }
error() { printf '\033[1;31mError: %s\033[0m\n' "$*" >&2; exit 1; }

die_usage() { printf '%s\n' "$*" >&2; exit 2; }

# The ZEDSTEAD logo in the web UI's accent blue (#5b8cff, webui/src/index.css)
# with "SERVER MANAGER" below it, shown only on a terminal so a cloud-init log
# stays plain. A terminal without 24-bit colour gets the nearest of 256, or
# plain blue. install.ps1 and install.bat carry the same rows
# (tests/test_install_banner.py).
banner() {
    [ -t 1 ] || return 0
    case "${COLORTERM:-}" in
        truecolor|24bit) blue='38;2;91;140;255' ;;
        *) if [ "$(tput colors 2>/dev/null || echo 0)" -ge 256 ]; then
               blue='38;5;69'
           else
               blue='1;34'
           fi ;;
    esac
    printf '\033[%sm' "$blue"
    printf '%s\n' \
        '   _____    ______  ____   _____  ______  ______  ___     ____' \
        '  /__  /   / ____/ / __ \ / ___/ /_  __/ / ____/ /   |   / __ \' \
        '    / /   / __/   / / / / \__ \   / /   / __/   / /| |  / / / /' \
        '   / /__ / /___  / /_/ / ___/ /  / /   / /___  / ___ | / /_/ /' \
        '  /____//_____/ /_____/ /____/  /_/   /_____/ /_/  |_|/_____/'
    printf '\033[0m\n%s\n\n' '                         SERVER MANAGER'
}

# A REPO_URL may carry credentials (https://user:token@host/…, the way a
# private repo is cloned unattended), and this script's output lands in
# cloud-init's log. Everything printed that can hold the URL goes through here.
# git itself still gets the real URL, and it stays in the clone's .git/config:
# a later update of a private repo needs it to fetch.
redact_urls() { sed 's#://[^/@[:space:]]*@#://***@#g'; }

# Every git call goes through here. git exits 128 on an authentication
# failure, a bad branch or a dead remote, and `set -e` let that leak out as
# this script's own exit code — the contract above promises 1 for "clone
# failed", and TODO-2 A8's cloud-config branches on these codes. Seen twice on
# real boxes (2026 Q3), from two different causes: bad HTTPS credentials on
# Ubuntu and a missing SSH key on Rocky, both exit 128.
run_git() {  # $1 = what to say on failure, rest = git arguments
    git_label="$1"; shift
    if ! git_out=$(git "$@" 2>&1); then
        error "$(printf '%s failed: %s' "$git_label" "$git_out" | redact_urls)"
    fi
    # `[ -n … ] && printf` alone would return 1 on empty output, and `set -e`
    # would take that as a failed function.
    [ -n "$git_out" ] && printf '%s\n' "$git_out" | redact_urls
    return 0
}

# Single keypress: y/Y or Enter accepts, anything else declines. Needs a tty —
# callers check `[ -t 0 ]` first.
confirm_yn() {  # $1 = question. Returns 0 for yes.
    printf '%s [Y/n] ' "$1"
    old_stty=$(stty -g)
    stty -icanon -echo min 1 time 0
    REPLY=$(dd bs=1 count=1 2>/dev/null)
    stty "$old_stty"
    echo ""
    case "$REPLY" in
        ""|[Yy]) return 0 ;;
        *) return 1 ;;
    esac
}

# ------------------------------------------------------------
# Defaults
# ------------------------------------------------------------

REPO_URL="${REPO_URL:-https://github.com/robba86/zedstead.git}"
INSTALL_DIR="${HOME}/.local/share/zsm"
BRANCH="${BRANCH:-main}"
ASSUME_YES=0
CHECK_DEPS=0
DRY_RUN=0
NO_PATH_EDIT=0
PREFIX=""
DEPS_MISSING=0
# The PATH we were *called* with. Step 1 prepends ~/.local/bin to our own PATH
# when it bootstraps uv, so from that point on `command -v zsm` answers for
# this script and not for the user's shell. Step 7 must ask about theirs.
PATH_AT_START="$PATH"

usage() {
    cat <<'EOF'
zsm installer

Usage: install.sh [options]

Options:
  -y, --yes          Answer yes to every question: install zsm (with uv and
                     Python), install the missing OS packages, and add the bin
                     directory to your shell startup file. The only flag that
                     suppresses a prompt. Required for unattended runs —
                     without a terminal and without it the installer refuses
                     and exits 3.
      --check-deps   Report missing OS packages and exit. Installs no packages
                     and no zsm — but it is not read-only: when this is not a
                     local checkout it clones or updates the repo first
                     (`git reset --hard origin/BRANCH` on an existing clone in
                     ~/.local/share/zsm), because the runtime package list is
                     read from it. Does not prompt: git runs with terminal
                     prompting and SSH interaction disabled, so a clone it
                     cannot authenticate fails instead of asking.
      --dry-run      Print what would be done and exit 0. Changes nothing.
      --no-path-edit Don't offer to add the PATH line at the end; print it for
                     you to add yourself. (uv's installer is never allowed to
                     edit a startup file either way — this script asks about
                     that one directory itself.)
      --prefix DIR   Install the zsm entry point into DIR instead of uv's
                     default (~/.local/bin). uv itself still goes to
                     ~/.local/bin, so the PATH line offered at the end covers
                     both directories when they differ.
      --branch NAME  Clone/update this branch instead of main.
  -h, --help         Show this help and exit.

Exit codes:
  0  installed successfully (or --dry-run / --check-deps with nothing missing)
  1  generic failure (uv install failed, clone failed, ...)
  2  usage error (unknown flag, or a flag missing its argument)
  3  cancelled (user answered n, or no tty and no --yes)
  4  missing OS dependencies, and you declined to install them
  5  OS packages could not be installed (no root, no sudo, or apt-get failed)

Examples:
  ./install.sh
  curl -fsSL https://raw.githubusercontent.com/robba86/zedstead/main/install.sh -o /tmp/zsm-install.sh && sh /tmp/zsm-install.sh --yes
EOF
}

# ------------------------------------------------------------
# OS dependency handling
#
# Keep BOOTSTRAP_PKGS in sync with the [bootstrap] section of
# app/data/os-packages.txt — tests/test_os_packages.py asserts these are
# identical. This copy exists because `curl … | sh` has no repo to read the
# file from yet. The [runtime] section is read from the repo after the
# clone/local-source step, so there is exactly one copy of it.
# ------------------------------------------------------------

BOOTSTRAP_PKGS="git curl ca-certificates tar"

read_pkg_section() {  # $1 = section, $2 = file
    awk -v want="[$1]" '
        /^\[/ { inside = ($0 == want); next }
        inside && $0 !~ /^#/ && NF { print }
    ' "$2" | tr '\n' ' '
}

query_provides() {
    # Virtual names that an *installed* package Provides.
    #
    # Ubuntu 24.04's 64-bit time_t transition renamed libcurl4 to libcurl4t64,
    # which merely Provides the old name. `dpkg-query -W libcurl4` then answers
    # "unknown ok not-installed" while `apt-get install libcurl4` resolves the
    # virtual name and reports it already installed — so the installer asked
    # for a package apt refused to install, forever, with no way out. The
    # virtual name is still the right thing to hand apt (it works on 22.04 and
    # 24.04), so the lookup is what gets fixed. Mirrored in
    # app/shared/os_packages.py::_provided_names for zsm doctor.
    dpkg-query -W -f='${Status}|${Provides}\n' 2>/dev/null | awk -F'|' '
        $1 != "install ok installed" { next }
        {
            n = split($2, prov, ",")
            for (i = 1; i <= n; i++) {
                sub(/\(.*$/, "", prov[i])
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", prov[i])
                if (prov[i] != "") print prov[i]
            }
        }'
}

missing_pkgs() {  # $1 = space-separated names
    if ! command -v dpkg-query >/dev/null 2>&1; then
        # Not a dpkg host. Report nothing rather than claim everything is
        # missing — a false "you are missing 8 packages" is worse than silence.
        return 0
    fi
    out=""
    provides=""
    have_provides=0
    for p in $1; do
        if dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed"; then
            continue
        fi
        case "$p" in
            # Arch-qualified (libstdc++6:i386): ${Provides} carries no
            # architecture, so a match there could not tell i386 from amd64,
            # and a false "present" for a 32-bit dep is the exact failure this
            # check exists to prevent.
            *:*) out="$out $p"; continue ;;
        esac
        if [ "$have_provides" -eq 0 ]; then
            provides=$(query_provides)
            have_provides=1
        fi
        if printf '%s\n' "$provides" | grep -Fqx "$p"; then
            continue
        fi
        out="$out $p"
    done
    printf '%s' "${out# }"
}

detect_sudo() {  # sets SUDO, or exits 5 when root is needed and unreachable
    SUDO=""
    if [ "$(id -u)" -ne 0 ]; then
        if ! command -v sudo >/dev/null 2>&1; then
            printf 'Error: installing OS packages needs root, and sudo is not available.\n' >&2
            exit 5
        fi
        SUDO="sudo -n"
        if ! $SUDO true 2>/dev/null; then
            SUDO="sudo"
        fi
    fi
}

ensure_packages() {  # $1 = space-separated names
    miss=$(missing_pkgs "$1")
    if [ -z "$miss" ]; then
        return 0
    fi

    if [ "$CHECK_DEPS" -eq 1 ]; then
        warn "Missing OS packages: $miss"
        echo "  Install them with:  sudo apt-get install -y $miss"
        DEPS_MISSING=1
        return 0
    fi

    # --yes is the only thing that suppresses a question, so there is one rule
    # to remember instead of a matrix.
    if [ "$ASSUME_YES" -eq 0 ]; then
        warn "Missing OS packages: $miss"
        if [ ! -t 0 ]; then
            echo "  Install them with:  sudo apt-get install -y $miss"
            echo "  Or re-run:          install.sh --yes"
            exit 4
        fi
        echo "  These are needed for SteamCMD and the DayZ server binary."
        if ! confirm_yn "Install them now with apt-get?"; then
            echo "  Install them yourself with:  sudo apt-get install -y $miss"
            echo "  Then re-run this installer."
            exit 4
        fi
        # Remember the answer: ensure_packages runs twice (bootstrap before the
        # clone, runtime after), and asking again reads as a loop.
        ASSUME_YES=1
    fi

    detect_sudo
    info "Installing OS packages: $miss"
    case "$miss" in
        *:i386*) $SUDO dpkg --add-architecture i386 || exit 5 ;;
    esac
    $SUDO apt-get update || exit 5
    # shellcheck disable=SC2086
    $SUDO apt-get install -y --no-install-recommends $miss || exit 5
}

ensure_utf8_locale() {
    # Decision: generate a locale only when the host has *no* UTF-8 locale at
    # all. C.UTF-8 is fine; requiring en_US.UTF-8 would be an invented
    # requirement (see plans/21).
    # Under the same consent as the OS packages: --yes, or a yes to the
    # package question (which sets ASSUME_YES).
    if [ "$ASSUME_YES" -eq 0 ] || [ "$CHECK_DEPS" -eq 1 ]; then
        return 0
    fi
    if locale -a 2>/dev/null | grep -qiE 'utf-?8'; then
        return 0
    fi
    info "No UTF-8 locale found — generating en_US.UTF-8"
    detect_sudo
    $SUDO sed -i 's/^# *\(en_US.UTF-8\)/\1/' /etc/locale.gen 2>/dev/null || true
    $SUDO locale-gen 2>/dev/null || warn "locale-gen failed; continuing"
}

# ------------------------------------------------------------
# Argument parsing (POSIX — no getopt, no bashisms)
# ------------------------------------------------------------

while [ $# -gt 0 ]; do
    case "$1" in
        -y|--yes)       ASSUME_YES=1 ;;
        # Removed: --yes installs the OS packages. Still accepted, so
        # a cloud-init file written for an older installer does not exit 2.
        --install-deps) ;;
        --check-deps)   CHECK_DEPS=1 ;;
        --dry-run)      DRY_RUN=1 ;;
        --no-path-edit) NO_PATH_EDIT=1 ;;
        --prefix)
            [ $# -ge 2 ] || die_usage "Error: --prefix needs a directory. Try --help."
            PREFIX="$2"; shift ;;
        --prefix=*)
            PREFIX="${1#--prefix=}"
            [ -n "$PREFIX" ] || die_usage "Error: --prefix needs a directory. Try --help." ;;
        --branch)
            [ $# -ge 2 ] || die_usage "Error: --branch needs a name. Try --help."
            BRANCH="$2"; shift ;;
        --branch=*)
            BRANCH="${1#--branch=}"
            [ -n "$BRANCH" ] || die_usage "Error: --branch needs a name. Try --help." ;;
        -h|--help)      usage; exit 0 ;;
        --)             shift; break ;;
        *)              die_usage "Error: unknown option '$1'. Try --help." ;;
    esac
    shift
done

# ------------------------------------------------------------
# --dry-run: report and stop, before any side effect
# ------------------------------------------------------------

if [ "$DRY_RUN" -eq 1 ]; then
    info "zsm installer — dry run, nothing will be changed."
    echo "  repo:        $(printf '%s' "$REPO_URL" | redact_urls) ($BRANCH)"
    echo "  install dir: $INSTALL_DIR"
    echo "  bin prefix:  ${PREFIX:-uv default (~/.local/bin)}"
    if [ "$NO_PATH_EDIT" -eq 1 ]; then path_edit=no; else path_edit=yes; fi
    echo "  check-deps: $CHECK_DEPS   path-edit: $path_edit"
    exit 0
fi

banner
info "zsm installer (uv)"
echo ""

# ------------------------------------------------------------
# Confirmation. --check-deps changes nothing, so it never prompts.
# ------------------------------------------------------------

if [ "$ASSUME_YES" -eq 0 ] && [ "$CHECK_DEPS" -eq 0 ]; then
    if [ ! -t 0 ]; then
        printf '%s\n' \
          "No terminal attached and --yes not given; refusing to install unattended." \
          "Re-run with:  install.sh --yes" >&2
        exit 3
    fi
    if ! confirm_yn "This will install Zedstead (zsm) and its dependencies (uv, Python). Continue?"; then
        echo "Installation cancelled."
        exit 3
    fi
fi

# ------------------------------------------------------------
# Step 1: Install uv if missing (not needed to only report deps)
# ------------------------------------------------------------

if [ "$CHECK_DEPS" -eq 0 ]; then
    if ! command -v uv >/dev/null 2>&1; then
        info "uv not found. Installing uv..."

        # INSTALLER_NO_MODIFY_PATH is unconditional: this script asks about
        # the PATH line itself at the end, and uv installs into the same
        # ~/.local/bin, so letting uv also edit ~/.profile / ~/.bashrc /
        # ~/.zshenv would leave two entries for one directory and print PATH
        # advice that contradicts ours. Its output is captured and shown only
        # on failure, so the last thing on screen is this script's own answer.
        if ! uv_out=$(curl -LsSf https://astral.sh/uv/install.sh \
                      | env INSTALLER_NO_MODIFY_PATH=1 sh 2>&1); then
            printf '%s\n' "$uv_out" >&2
            error "uv installation failed."
        fi

        # uv installs into ~/.local/bin by default
        export PATH="$HOME/.local/bin:$PATH"
    fi

    if ! command -v uv >/dev/null 2>&1; then
        # Exited 0 without producing a uv (a curl failure feeding an empty
        # script does exactly this). Its output was captured, so show it now
        # rather than leaving the user with a bare "failed".
        if [ -n "${uv_out:-}" ]; then
            printf '%s\n' "$uv_out" >&2
        fi
        error "uv installation failed."
    fi

    info "Found uv: $(uv --version)"

    # ------------------------------------------------------------
    # Step 2: Ensure Python is available via uv
    # ------------------------------------------------------------

    info "Ensuring Python 3.11+ is installed via uv..."

    uv python install 3.11

    # Report the Python that is actually on this box, not the newest one uv
    # could *download*. `uv python list` includes downloads, newest first, so
    # `head -n 1` named an interpreter that is not installed: "Installed Python
    # 3.11.16" followed by "Python ready: cpython-3.15.0rc2 <download
    # available>", and on Rocky "Python 3.11 is already installed" followed by
    # the same line — the installer contradicting itself in two consecutive
    # lines. Measured in 2026 Q3 on both boxes (F7). `--only-installed` plus
    # the `3.11` request filter is what makes this the version we asked for
    # rather than whichever installed Python sorts first.
    py_ready=$(uv python list --only-installed 3.11 2>/dev/null | head -n 1 | awk '{print $1}')
    ok "Python ready: ${py_ready:-3.11 (uv-managed)}"
fi

# ------------------------------------------------------------
# Step 3: Determine package source
# ------------------------------------------------------------

# `dirname -- "$0"` is load-bearing for the old `sh -c "$(curl …)" -- …`
# one-liner, which sets $0 to "--": `dirname "--"` reads that as an
# end-of-options marker and errors with "missing operand", and under `set -e`
# that killed the install right after the OS packages went on. The published
# form downloads first now (see the header) so $0 is a real path, but the
# guard stays for anyone pasting the old shape. Fall back to the cwd if the cd
# still fails.
SCRIPT_DIR="$(cd "$(dirname -- "$0")" 2>/dev/null && pwd)" || SCRIPT_DIR=""
[ -n "$SCRIPT_DIR" ] || SCRIPT_DIR="$(pwd)"

if [ -f "$SCRIPT_DIR/pyproject.toml" ] && [ -d "$SCRIPT_DIR/app" ]; then
    PACKAGE_DIR="$SCRIPT_DIR"
    info "Installing from local source: $PACKAGE_DIR"

    # A local checkout skips the clone below, so the bootstrap packages are
    # never checked there — do it here when only reporting.
    if [ "$CHECK_DEPS" -eq 1 ]; then
        ensure_packages "$BOOTSTRAP_PKGS"
    fi
else
    # ------------------------------------------------------------
    # Step 4: Clone or update repo
    # ------------------------------------------------------------

    # --check-deps promises it installs nothing, and it keeps that promise —
    # but "installs nothing" is not "changes nothing", and the clone below is a
    # real write (a hard reset of an existing checkout). Say so before doing it,
    # for the reader who never opened --help. Fetching the list without a clone
    # would mean a second download path for one file; the notice is the honest
    # trade.
    # Two wordings, the same test the clone below uses: on a first run there
    # is nothing to update or reset, and "git reset --hard" read as a threat
    # to a machine that had no zsm yet (first-hour test, 2026 Q4).
    if [ "$CHECK_DEPS" -eq 1 ]; then
        if [ -d "$INSTALL_DIR/.git" ]; then
            warn "--check-deps: updating $INSTALL_DIR to read the package list (git reset --hard)."
        else
            warn "--check-deps: downloading the source to $INSTALL_DIR to read the package list."
        fi
    fi

    ensure_packages "$BOOTSTRAP_PKGS"

    # git must fail rather than ask, and must fail as 1 rather than as 128.
    #
    # Prompting: --check-deps is documented as scriptable, and both prompts
    # were hit on real boxes (2026 Q3) — "Username for
    # 'https://github.com':" on an HTTPS remote, and SSH's "Are you sure you
    # want to continue connecting (yes/no)?" on an ssh:// one. Without a
    # terminal those hang instead of failing, which is the worst outcome for
    # a cloud-init run. GIT_SSH_COMMAND defers to an operator who set their
    # own.
    GIT_TERMINAL_PROMPT=0; export GIT_TERMINAL_PROMPT
    GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -o BatchMode=yes}"
    export GIT_SSH_COMMAND

    if [ -d "$INSTALL_DIR/.git" ]; then
        info "Updating existing installation..."
        run_git "Updating $INSTALL_DIR" -C "$INSTALL_DIR" fetch origin "$BRANCH"
        run_git "Updating $INSTALL_DIR" -C "$INSTALL_DIR" reset --hard "origin/$BRANCH"
    else
        info "Cloning zsm repository..."
        rm -rf "$INSTALL_DIR"
        run_git "Cloning $REPO_URL" clone --branch "$BRANCH" --single-branch \
            "$REPO_URL" "$INSTALL_DIR"
    fi

    PACKAGE_DIR="$INSTALL_DIR"
fi

# ------------------------------------------------------------
# Step 5: Runtime OS packages — the [runtime] section of the shared list
# ------------------------------------------------------------

PKG_FILE="$PACKAGE_DIR/app/data/os-packages.txt"
if [ -f "$PKG_FILE" ]; then
    RUNTIME_PKGS="$(read_pkg_section runtime "$PKG_FILE")"
    ensure_packages "$RUNTIME_PKGS"
else
    warn "Package list not found at $PKG_FILE — skipping the runtime dependency check."
fi

ensure_utf8_locale

if [ "$CHECK_DEPS" -eq 1 ]; then
    echo ""
    if [ "$DEPS_MISSING" -eq 1 ]; then
        warn "Dependency check: packages are missing (see above). Nothing was installed."
        exit 4
    fi
    ok "Dependency check: all OS packages present. Nothing was installed."
    exit 0
fi

# ------------------------------------------------------------
# Step 6: Install CLI tool using uv tool
# ------------------------------------------------------------

info "Installing zsm with uv..."

if [ -n "$PREFIX" ]; then
    UV_TOOL_BIN_DIR="$PREFIX"; export UV_TOOL_BIN_DIR
    info "Installing the zsm entry point into $PREFIX"
fi

uv tool install --force --reinstall "$PACKAGE_DIR"

# ------------------------------------------------------------
# Step 7: Verify installation
# ------------------------------------------------------------

BIN_DIR="${PREFIX:-$HOME/.local/bin}"

# Where uv itself ended up. With --prefix the zsm entry point and uv land in
# *different* directories, and a PATH line naming only one of them leaves the
# other invisible — which for uv means this installer re-downloads it on every
# run and `uv tool upgrade zsm` is not available.
UV_DIR=""
uv_path=$(command -v uv 2>/dev/null || true)
if [ -n "$uv_path" ]; then
    UV_DIR=$(dirname "$uv_path")
fi

# Ask whether the *caller's* shell can see a directory, not whether ours can.
# Step 1 prepends ~/.local/bin to our own PATH when it bootstraps uv, so
# testing our PATH reported "installed successfully" on a fresh box where the
# user's shell could not run zsm at all — and suppressed the hint that fixes it.
on_caller_path() {  # $1 = directory
    case ":$PATH_AT_START:" in
        *:"$1":*) return 0 ;;
    esac
    return 1
}

MISSING_DIRS=""      # space separated, for fish
MISSING_JOINED=""    # colon separated, for the export line
for d in "$BIN_DIR" "$UV_DIR"; do
    [ -n "$d" ] || continue
    if on_caller_path "$d"; then
        continue
    fi
    case " $MISSING_DIRS " in
        *" $d "*) continue ;;   # BIN_DIR and UV_DIR are the same without --prefix
    esac
    MISSING_DIRS="${MISSING_DIRS:+$MISSING_DIRS }$d"
    MISSING_JOINED="${MISSING_JOINED:+$MISSING_JOINED:}$d"
done

# Name the file the user's shell actually reads. zsh reads none of ~/.profile,
# ~/.bash_profile or ~/.bashrc, so the generic "~/.bashrc or ~/.zshrc" advice
# was a coin flip — and Debian/Ubuntu's ~/.local/bin snippet lives in
# ~/.profile, which is why a zsh box never picks it up on its own.
#
# $SHELL is the *login* shell from /etc/passwd, not the one you are typing in:
# running this from bash on a box whose login shell is zsh made it advise
# `exec zsh -l`. The parent process is the shell that actually launched us, so
# ask that first and keep $SHELL as the fallback.
detect_shell() {
    parent=$(ps -p "$PPID" -o comm= 2>/dev/null || true)
    parent="${parent#-}"          # login shells show up as "-bash"
    parent="${parent##*/}"
    case "$parent" in
        zsh|bash|fish) printf '%s' "$parent"; return 0 ;;
    esac
    printf '%s' "${SHELL##*/}"
}

SHELL_NAME="$(detect_shell)"
case "$SHELL_NAME" in
    zsh)  RC_FILE="$HOME/.zshrc" ;;
    bash) RC_FILE="$HOME/.bashrc" ;;
    fish) RC_FILE="$HOME/.config/fish/config.fish" ;;
    *)    RC_FILE="" ;;
esac

# The line that fixes it, in the syntax that shell actually uses. fish quotes
# each directory; the export form needs none, the whole value is already quoted.
if [ "$SHELL_NAME" = "fish" ]; then
    PATH_LINE="fish_add_path"
    for d in $MISSING_DIRS; do
        PATH_LINE="$PATH_LINE \"$d\""
    done
else
    PATH_LINE="export PATH=\"$MISSING_JOINED:\$PATH\""
fi

print_path_hint() {
    echo ""
    if [ -n "$RC_FILE" ]; then
        echo "Add this to $RC_FILE:"
    else
        echo "Add this to your shell's startup file:"
    fi
    echo ""
    echo "  $PATH_LINE"
    echo ""
    if [ -n "$SHELL_NAME" ]; then
        echo "Then run:  exec $SHELL_NAME -l    (or open a new terminal)"
    else
        echo "Then restart your terminal."
    fi
}

add_to_path() {
    # Appending is the last step and the only one that touches a file the user
    # owns, so it is asked separately and skipped entirely with --no-path-edit.
    if [ "$NO_PATH_EDIT" -eq 1 ] || [ -z "$RC_FILE" ]; then
        print_path_hint
        return 0
    fi
    if [ -f "$RC_FILE" ] && grep -Fq "$PATH_LINE" "$RC_FILE"; then
        # Already there from an earlier run — appending a second copy is the
        # classic installer sin. The current shell just has not re-read it.
        echo ""
        echo "$RC_FILE already has it. Run:  exec $SHELL_NAME -l"
        return 0
    fi
    if [ "$ASSUME_YES" -eq 0 ]; then
        if [ ! -t 0 ]; then
            print_path_hint
            return 0
        fi
        echo ""
        echo "  $PATH_LINE"
        if ! confirm_yn "Add that line to $RC_FILE?"; then
            print_path_hint
            return 0
        fi
    fi
    mkdir -p "$(dirname "$RC_FILE")"
    printf '\n# Added by the zsm installer\n%s\n' "$PATH_LINE" >> "$RC_FILE"
    ok "Added to $RC_FILE."
    echo ""
    echo "Run:  exec $SHELL_NAME -l    (or open a new terminal), then:  zsm"
}

echo ""
if [ ! -x "$BIN_DIR/zsm" ]; then
    # Checks the file rather than `command -v`, which would answer for this
    # script's PATH and, with --prefix, not look in the right place at all.
    warn "Expected the zsm entry point at $BIN_DIR/zsm, but it is not there."
    echo ""
    echo "  ls -l \"$BIN_DIR\"    then re-run this script."
elif [ -z "$MISSING_DIRS" ]; then
    ok "zsm installed successfully!"
    echo ""
    echo "Run:"
    echo "  zsm"
    echo ""
    echo "Re-run this script anytime to update."
else
    ok "zsm installed to $BIN_DIR."
    if on_caller_path "$BIN_DIR"; then
        # zsm resolves; it is uv that does not. Worth one line, because the
        # cost is silent: every re-run downloads uv again.
        warn "uv is in $UV_DIR, which is not on your PATH — re-running this"
        warn "installer would download it again each time."
    elif [ -n "$UV_DIR" ] && [ "$UV_DIR" != "$BIN_DIR" ]; then
        warn "Neither $BIN_DIR (zsm) nor $UV_DIR (uv) is on your PATH."
    else
        warn "That directory is not on your PATH, so the 'zsm' command will not resolve yet."
    fi
    add_to_path
fi
