#!/bin/sh
# ------------------------------------------------------------
# zsm preflight — does this box look like it can run a DayZ server?
#
#   curl -fsSL https://raw.githubusercontent.com/robba86/zedstead/main/preflight.sh -o /tmp/zsm-preflight.sh && sh /tmp/zsm-preflight.sh
#
# Download first, then run: `sh -c "$(curl …)"` expands to `sh -c ""` when
# the download fails, so it runs nothing and still exits 0.
#
# Reads only. Installs nothing, changes nothing, needs no root. Safe to run on
# an hourly-billed VPS you are evaluating before committing to a month.
#
# Already installed zsm? `zsm doctor --preflight` runs this same checklist
# from the app and prints the same verdict.
#
# Exit codes:
#   0  everything passed
#   1  at least one check warned
#
# The thresholds and the verdict wording below are COPIES of the app's, kept
# inline because this script is fetched over curl with no repo behind it.
# tests/test_vps_readiness.py fails if either copy drifts. See plans/23.
# ------------------------------------------------------------

# --- BEGIN generated from app/data/vps-thresholds.env ---
MIN_CORES=2
MIN_MHZ=2400
MAX_STEAL_PCT=5
MIN_RAM_MB=4096
RAM_ROUND_TOLERANCE_PCT=15
TIER_VANILLA_MB=6144
TIER_MODDED_LIGHT_MB=8192
TIER_MODDED_HEAVY_MB=16384
SWAP_REQUIRED_BELOW_MB=12288
MIN_DISK_GB=30
MIN_FILE_MAX=100000
MIN_NOFILE=65536
# --- END generated from app/data/vps-thresholds.env ---

# --- BEGIN generated from app/ops/vps_readiness.py ---
RAM_SIZES_MB="512 1024 2048 3072 4096 6144 8192 12288 16384 24576 32768 49152 65536 98304 131072"
VERDICT_UNKNOWN="Could not measure RAM — no verdict."
VERDICT_TOO_SMALL="Too small for a DayZ server — vanilla needs about 4 GB RAM."
VERDICT_VANILLA_SMALL="Fit for vanilla, roughly 20 slots. Not enough for heavy mods."
VERDICT_VANILLA="Fit for vanilla up to roughly 60 slots, or a few light mods."
VERDICT_MODDED_LIGHT="Fit for a lightly modded server, roughly 40 slots."
VERDICT_MODDED_HEAVY="Fit for a heavily modded server (Expansion + 40-60 players)."
VERDICT_STEAL_SUFFIX="Shared vCPU — expect stutter under load."
# --- END generated from app/ops/vps_readiness.py ---

FAILED=0

# ------------------------------------------------------------
# Output. A check that could not measure prints "?" and does NOT fail — a
# check failing because it could not measure trains you to ignore the tool.
# ------------------------------------------------------------

ok()      { printf '\033[1;32mOK  \033[0m %-13s %s\n' "$1" "$2"; }
warn()    { printf '\033[1;33mWARN\033[0m %-13s %s\n' "$1" "$2"; FAILED=1; }
unknown() { printf '\033[1;30m?   \033[0m %-13s %s\n' "$1" "$2"; }
info()    { printf '\033[1;34m--  \033[0m %-13s %s\n' "$1" "$2"; }

have() { command -v "$1" >/dev/null 2>&1; }

# MemTotal normalised to the plan size the box is sold as. A guest never
# presents its nominal size and the shortfall is not a constant (two 4 GB
# VPS plans: 3814 MB and 3557 MB), so RAM is judged as advertised size.
# Port of advertised_ram_mb() in app/ops/vps_readiness.py — keep the two in
# step. Rounds up only, and only within RAM_ROUND_TOLERANCE_PCT, so a box that
# really has 2900 MB still fails.
advertised_ram_mb() {  # $1 = measured MB -> the plan size on stdout
    for SIZE in $RAM_SIZES_MB; do
        [ "$1" -gt "$SIZE" ] && continue
        if [ $(( $1 * 100 )) -ge $(( SIZE * (100 - RAM_ROUND_TOLERANCE_PCT) )) ]; then
            printf '%s' "$SIZE"
        else
            printf '%s' "$1"
        fi
        return
    done
    printf '%s' "$1"
}

printf '\n\033[1mzsm preflight — VPS readiness\033[0m\n\n'

# ------------------------------------------------------------
# CPU: cores, model, clock
# ------------------------------------------------------------

CORES=""
if have nproc; then
    CORES=$(nproc 2>/dev/null)
elif [ -r /proc/cpuinfo ]; then
    CORES=$(grep -c '^processor' /proc/cpuinfo 2>/dev/null)
fi

MODEL=""
[ -r /proc/cpuinfo ] && MODEL=$(grep -m1 'model name' /proc/cpuinfo 2>/dev/null | cut -d: -f2- | sed 's/^ *//')

# cpufreq's max frequency first: on a box with frequency scaling /proc/cpuinfo's
# "cpu MHz" is the current idle clock (a 4.6 GHz laptop reads 800 MHz) and would
# warn on hardware that is in fact fast. A VPS guest usually has no cpufreq and
# its "cpu MHz" is the host's fixed clock, which is the number we want.
MHZ=""
if [ -r /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq ]; then
    MHZ=$(awk '{printf "%d", $1/1000}' /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq 2>/dev/null)
elif [ -r /proc/cpuinfo ]; then
    MHZ=$(awk -F: '/cpu MHz/ {printf "%d", $2; exit}' /proc/cpuinfo 2>/dev/null)
fi

if [ -z "$CORES" ] && [ -z "$MODEL" ] && [ -z "$MHZ" ]; then
    unknown "CPU" "could not read CPU info"
else
    DETAIL="${CORES:-?} core(s)"
    [ -n "$MODEL" ] && DETAIL="$DETAIL, $MODEL"
    [ -n "$MHZ" ] && DETAIL="$DETAIL, $MHZ MHz"
    CPU_BAD=""
    [ -n "$CORES" ] && [ "$CORES" -lt "$MIN_CORES" ] && CPU_BAD="fewer than $MIN_CORES cores"
    if [ -n "$MHZ" ] && [ "$MHZ" -lt "$MIN_MHZ" ]; then
        [ -n "$CPU_BAD" ] && CPU_BAD="$CPU_BAD and "
        CPU_BAD="${CPU_BAD}below $MIN_MHZ MHz"
    fi
    if [ -n "$CPU_BAD" ]; then
        warn "CPU" "$DETAIL — $CPU_BAD. DayZ is single-thread bound, so clock speed matters more than core count."
    else
        ok "CPU" "$DETAIL"
    fi
fi

# ------------------------------------------------------------
# CPU steal — the shared-vCPU tell. Cumulative-since-boot steal is close to
# useless on a long-lived box, so sample twice a second apart.
# ------------------------------------------------------------

STEAL=""
if [ -r /proc/stat ]; then
    STAT1=$(awk '/^cpu /{print; exit}' /proc/stat 2>/dev/null)
    sleep 1
    STAT2=$(awk '/^cpu /{print; exit}' /proc/stat 2>/dev/null)
    if [ -n "$STAT1" ] && [ -n "$STAT2" ]; then
        STEAL=$(printf '%s\n%s\n' "$STAT1" "$STAT2" | awk '
            NR==1 { if (NF < 9) exit; s1=$9; t1=0; for (i=2; i<=NF; i++) t1+=$i }
            NR==2 { if (NF < 9) exit; s2=$9; t2=0; for (i=2; i<=NF; i++) t2+=$i
                    if (t2 > t1) printf "%.1f", 100*(s2-s1)/(t2-t1) }')
    fi
fi

if [ -z "$STEAL" ]; then
    unknown "CPU steal" "/proc/stat has no steal field"
else
    if [ "$(printf '%s' "$STEAL" | awk -v m="$MAX_STEAL_PCT" '{print ($1 > m) ? 1 : 0}')" = "1" ]; then
        warn "CPU steal" "$STEAL% stolen by the hypervisor — over $MAX_STEAL_PCT%. This is a shared vCPU under contention; expect stutter regardless of RAM."
    else
        ok "CPU steal" "$STEAL% stolen by the hypervisor"
    fi
fi

# ------------------------------------------------------------
# RAM and swap
# ------------------------------------------------------------

RAM_MB=""
SWAP_MB=""
if [ -r /proc/meminfo ]; then
    RAM_MB=$(awk '/^MemTotal:/ {printf "%d", $2/1024; exit}' /proc/meminfo 2>/dev/null)
    SWAP_MB=$(awk '/^SwapTotal:/ {printf "%d", $2/1024; exit}' /proc/meminfo 2>/dev/null)
fi

RAM_CLASS=""
[ -n "$RAM_MB" ] && RAM_CLASS=$(advertised_ram_mb "$RAM_MB")

# The measured figure stays on screen — the rounding is for the comparison
# only, and hiding the real number would make this check unfalsifiable.
if [ -z "$RAM_MB" ]; then
    unknown "RAM" "could not read /proc/meminfo"
else
    RAM_DETAIL="$RAM_MB MB total"
    if [ "$RAM_CLASS" != "$RAM_MB" ] && [ "$RAM_CLASS" -ge 1024 ]; then
        RAM_GB=$((RAM_CLASS / 1024))
        case "$RAM_GB" in 8*) RAM_ARTICLE="an" ;; *) RAM_ARTICLE="a" ;; esac
        RAM_DETAIL="$RAM_DETAIL ($RAM_ARTICLE $RAM_GB GB box)"
    fi
    if [ "$RAM_CLASS" -lt "$MIN_RAM_MB" ]; then
        warn "RAM" "$RAM_DETAIL — below $MIN_RAM_MB MB. Enough for vanilla with a handful of slots; expect trouble above that."
    else
        ok "RAM" "$RAM_DETAIL"
    fi
fi

if [ -z "$SWAP_MB" ] || [ -z "$RAM_MB" ]; then
    unknown "Swap" "could not read /proc/meminfo"
elif [ "$SWAP_MB" -gt 0 ]; then
    ok "Swap" "$SWAP_MB MB"
elif [ "$RAM_CLASS" -lt "$SWAP_REQUIRED_BELOW_MB" ]; then
    warn "Swap" "none, with only $RAM_MB MB RAM — add swap below $SWAP_REQUIRED_BELOW_MB MB RAM or the server is killed by the OOM reaper on a mod-heavy restart."
else
    ok "Swap" "none — not required above $SWAP_REQUIRED_BELOW_MB MB RAM"
fi

# ------------------------------------------------------------
# Disk free on $HOME (the server has not been installed yet)
# ------------------------------------------------------------

TARGET="${HOME:-/}"
DISK_GB=""
have df && DISK_GB=$(df -Pk "$TARGET" 2>/dev/null | awk 'NR==2 {printf "%.1f", $4/1048576}')

# F3: the Swap rule above tells a small box to add swap, and on a 40 GB disk a
# 4 GB swapfile then took free space from 34.3 GB to 29.5 GB — under
# MIN_DISK_GB. Following one check's advice failed another, on the plan the docs
# recommend. Count a swap *file* on the same filesystem back in, since the space
# went on this checklist's own instruction.
#
# Swap *partitions* are excluded on purpose: another block device, never took a
# byte from the filesystem being measured. So is a swapfile we cannot locate —
# "could not tell" must not mask a real shortfall. Mount point rather than
# device number because that is what df can answer. Must stay in step with
# app/ops/vps_readiness.py::swapfile_gb_on().
SWAPFILE_GB="0.0"
if [ -n "$DISK_GB" ] && [ -r /proc/swaps ]; then
    TARGET_MNT=$(df -Pk "$TARGET" 2>/dev/null | awk 'NR==2 {print $6}')
    SWAPFILE_KB=0
    # Redirected from the file, not piped: a pipeline runs the loop in a
    # subshell and SWAPFILE_KB would come back 0 every time.
    while read -r SW_NAME SW_TYPE SW_SIZE _SW_REST; do
        [ "$SW_TYPE" = "file" ] || continue
        SW_MNT=$(df -Pk "$SW_NAME" 2>/dev/null | awk 'NR==2 {print $6}')
        if [ -n "$SW_MNT" ] && [ -n "$TARGET_MNT" ] && [ "$SW_MNT" = "$TARGET_MNT" ]; then
            SWAPFILE_KB=$((SWAPFILE_KB + SW_SIZE))
        fi
    done < /proc/swaps
    [ "$SWAPFILE_KB" -gt 0 ] && SWAPFILE_GB=$(awk -v k="$SWAPFILE_KB" 'BEGIN {printf "%.1f", k/1048576}')
fi

DISK_NOTE=""
[ "$SWAPFILE_GB" != "0.0" ] && DISK_NOTE=" (+$SWAPFILE_GB GB held by a swap file on this disk)"

if [ -z "$DISK_GB" ]; then
    unknown "Disk space" "could not stat $TARGET"
elif [ "$(awk -v d="$DISK_GB" -v s="$SWAPFILE_GB" -v m="$MIN_DISK_GB" 'BEGIN {print (d + s < m) ? 1 : 0}')" = "1" ]; then
    warn "Disk space" "$DISK_GB GB free on $TARGET$DISK_NOTE — below $MIN_DISK_GB GB. The server plus mods and backups will not fit."
else
    ok "Disk space" "$DISK_GB GB free on $TARGET$DISK_NOTE"
fi

# ------------------------------------------------------------
# noexec — a $HOME the server binary cannot be executed from
# ------------------------------------------------------------

MOUNT_POINT=""
have df && MOUNT_POINT=$(df -P "$TARGET" 2>/dev/null | awk 'NR==2 {print $6}')
if [ -z "$MOUNT_POINT" ] || [ ! -r /proc/mounts ]; then
    unknown "noexec" "could not read the mount options"
elif awk -v mp="$MOUNT_POINT" '$2 == mp && $4 ~ /(^|,)noexec(,|$)/ {found=1} END {exit !found}' /proc/mounts; then
    warn "noexec" "$MOUNT_POINT is mounted noexec — the DayZ server binary cannot run from there. Install elsewhere, or remount."
else
    ok "noexec" "$MOUNT_POINT is executable"
fi

# ------------------------------------------------------------
# File handles. Host level only — zsm's own unit sets LimitNOFILE=65536.
#
# The HARD limit is what is judged, not the soft one. systemd's
# DefaultLimitNOFILESoft is 1024 on a stock box and a login shell commonly
# inherits it, so checking `ulimit -n` warned on nearly every fresh VPS about
# something that does not affect the server unit and that the user cannot act
# on. Must stay in step with app/ops/vps_readiness.py::file_handle_limits().
# ------------------------------------------------------------

FILE_MAX=""
[ -r /proc/sys/fs/file-max ] && FILE_MAX=$(cat /proc/sys/fs/file-max 2>/dev/null)
NOFILE=$(ulimit -Hn 2>/dev/null)

# "No limit" and "could not read it" are different facts and both used to print
# as `?` — while a real no-limit fs.file-max printed as twenty digits
# (9223372036854775807, LONG_MAX, measured on a stock VPS) which reads
# as noise rather than as good news. F12. The kernel spells it three ways: the
# number here, the word from `ulimit`, and -1 from getrlimit. Keep the judged
# value and the displayed value apart. Must stay in step with
# app/ops/vps_readiness.py::_fmt_limit().
NO_LIMIT="9223372036854775807"
FILE_MAX_TEXT="${FILE_MAX:-?}"
[ "$FILE_MAX" = "$NO_LIMIT" ] && FILE_MAX_TEXT="unlimited"
NOFILE_TEXT="${NOFILE:-?}"
if [ "$NOFILE" = "unlimited" ] || [ "$NOFILE" = "$NO_LIMIT" ]; then
    NOFILE_TEXT="unlimited"
    # Keep it numeric and huge rather than blanking it: empty means "could not
    # read", which would send an unlimited box down the `unknown` branch below.
    NOFILE="$NO_LIMIT"
fi

if [ -z "$FILE_MAX" ] && [ -z "$NOFILE" ]; then
    unknown "File handles" "could not read the limits"
else
    FH_BAD=""
    [ -n "$FILE_MAX" ] && [ "$FILE_MAX" -lt "$MIN_FILE_MAX" ] && FH_BAD="fs.file-max $FILE_MAX < $MIN_FILE_MAX"
    if [ -n "$NOFILE" ] && [ "$NOFILE" -lt "$MIN_NOFILE" ]; then
        [ -n "$FH_BAD" ] && FH_BAD="$FH_BAD; "
        FH_BAD="${FH_BAD}ulimit -Hn $NOFILE < $MIN_NOFILE"
    fi
    if [ -n "$FH_BAD" ]; then
        warn "File handles" "$FH_BAD"
    else
        ok "File handles" "fs.file-max ${FILE_MAX_TEXT}, hard nofile ${NOFILE_TEXT} (the server unit pins its own LimitNOFILE=65536)"
    fi
fi

# ------------------------------------------------------------
# Clock — Steam login and the A2S server browser both reject a skew
# ------------------------------------------------------------

if have timedatectl; then
    SYNCED=$(timedatectl show -p NTPSynchronized --value 2>/dev/null)
    case "$SYNCED" in
        yes) ok "Clock" "NTP synchronised" ;;
        no)  warn "Clock" "not NTP synchronised — Steam login and the A2S server browser both reject a skewed clock. Run: sudo timedatectl set-ntp true" ;;
        *)   unknown "Clock" "timedatectl gave no answer" ;;
    esac
else
    unknown "Clock" "timedatectl unavailable"
fi

# ------------------------------------------------------------
# Informational — never a pass/fail signal
# ------------------------------------------------------------

KERNEL=""
have uname && KERNEL=$(uname -r 2>/dev/null)
DISTRO=""
[ -r /etc/os-release ] && DISTRO=$(awk -F= '/^PRETTY_NAME=/ {gsub(/"/, "", $2); print $2; exit}' /etc/os-release)
VENDOR=""
[ -r /sys/class/dmi/id/sys_vendor ] && VENDOR=$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null | sed 's/[[:space:]]*$//')

HOST_DETAIL=""
for BIT in "$DISTRO" "${KERNEL:+kernel $KERNEL}"; do
    [ -n "$BIT" ] || continue
    [ -n "$HOST_DETAIL" ] && HOST_DETAIL="$HOST_DETAIL, "
    HOST_DETAIL="$HOST_DETAIL$BIT"
done
info "Host" "${HOST_DETAIL:-unknown}"

# No URL here on purpose. The /hosting/ page has existed since 2026 Q3, so
# do not restore the link on the strength of finding it in the site repo's
# src/routes.js (the zedstead-site repo since 2026 Q3):
# zedstead.com has no DNS record at all (NXDOMAIN from the .app registry, measured
# 2026 Q3), so the line would resolve to nothing for every user who runs
# this. Restore it when the domain answers, not when the page exists.
if [ -n "$VENDOR" ]; then
    info "Provider" "$VENDOR — check its DDoS protection and network before you commit"
else
    info "Provider" "unknown (no DMI vendor) — check your provider's DDoS protection before you commit"
fi

if have ss; then
    # Column 4 is the LOCAL address:port on data rows (the header splits
    # "Local Address:Port" across two fields, hence NR>1).
    UDP_PORTS=$(ss -lun 2>/dev/null | awk 'NR>1 {n=split($4, a, ":"); print a[n]}' | grep -E '^[0-9]+$' | sort -un | tr '\n' ' ')
    info "Open UDP" "${UDP_PORTS:-none} (informational — 'zsm service firewall' manages the policy)"
else
    info "Open UDP" "ss unavailable"
fi

# ------------------------------------------------------------
# Verdict — same tiers as `zsm doctor --preflight`
# ------------------------------------------------------------

# Same advertised size the RAM row judged, so the two cannot disagree.
if [ -z "$RAM_MB" ]; then
    VERDICT="$VERDICT_UNKNOWN"
elif [ "$RAM_CLASS" -lt "$MIN_RAM_MB" ]; then
    VERDICT="$VERDICT_TOO_SMALL"
elif [ "$RAM_CLASS" -lt "$TIER_VANILLA_MB" ]; then
    VERDICT="$VERDICT_VANILLA_SMALL"
elif [ "$RAM_CLASS" -lt "$TIER_MODDED_LIGHT_MB" ]; then
    VERDICT="$VERDICT_VANILLA"
elif [ "$RAM_CLASS" -lt "$TIER_MODDED_HEAVY_MB" ]; then
    VERDICT="$VERDICT_MODDED_LIGHT"
else
    VERDICT="$VERDICT_MODDED_HEAVY"
fi

if [ -n "$STEAL" ] && [ "$(printf '%s' "$STEAL" | awk -v m="$MAX_STEAL_PCT" '{print ($1 > m) ? 1 : 0}')" = "1" ]; then
    VERDICT="$VERDICT $VERDICT_STEAL_SUFFIX"
fi

printf '\n\033[1mVerdict:\033[0m %s\n' "$VERDICT"
printf '\033[1;30mInstall zsm on this box and "zsm doctor --preflight" re-runs these checks any time:\033[0m\n'
printf '\033[1;30m  curl -fsSL https://raw.githubusercontent.com/robba86/zedstead/main/install.sh -o /tmp/zsm-install.sh && sh /tmp/zsm-install.sh\033[0m\n\n'

exit $FAILED
