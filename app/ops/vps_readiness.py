"""Host-level readiness probes for a VPS: CPU, steal, RAM, swap, disk, file
handles, clock. Pure reads — nothing here changes the system.

Shares its thresholds with ``preflight.sh`` via ``app/data/vps-thresholds.env``;
see plans/23. Every probe returns ``None`` for "could not measure" and callers
treat that as a pass, never a failure — a check that fails because it could not
measure trains users to ignore doctor.

Lives in ``app/ops/`` rather than inside ``app/commands/doctor.py`` because
``zsm bootstrap-vps`` (TODO-2 A3) will reuse the same probes to decide what to
change.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from app.shared.platform import IS_WINDOWS

# Unix only. `app.__main__` imports this module on every start (through
# `bootstrap-vps`), so a plain `import resource` stopped every zsm command on
# Windows with ModuleNotFoundError (first-hour test, 2026 Q4). Its one
# user, `file_handle_limits`, returns before it on Windows.
try:
    import resource
except ImportError:
    resource = None

THRESHOLDS_PATH = Path(__file__).resolve().parents[1] / "data" / "vps-thresholds.env"

# Anything a probe source can raise. Callers turn these into "unknown".
_READ_ERRORS = (OSError, ValueError, subprocess.SubprocessError)

_KEY_VALUE_RE = re.compile(r"^([A-Z0-9_]+)=(-?\d+)$")


@dataclass(frozen=True)
class Probe:
    """One host measurement rendered as a doctor check row.

    ``value`` is the measured number (MB, GB, %, count) for tests and future
    consumers; ``None`` means the probe could not measure, in which case ``ok``
    is always True.
    """

    label: str
    ok: bool
    detail: str
    value: Optional[float] = None


def _read_file(path: str) -> str:
    """Raw text read. Raises ``OSError`` — every caller catches it and reports
    "unknown" rather than failing its check."""
    return Path(path).read_text(encoding="utf-8", errors="replace")


def _run(argv: list[str], timeout: float = 5.0) -> str:
    """Raw command output. Raises on a missing binary or a non-zero exit."""
    proc = subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, check=False
    )
    if proc.returncode != 0:
        raise subprocess.SubprocessError(f"{argv[0]} exited {proc.returncode}")
    return proc.stdout


def thresholds() -> dict[str, int]:
    """Parse app/data/vps-thresholds.env into {KEY: int}.

    ``preflight.sh`` carries a copy of these numbers inline (it is fetched over
    curl with no repo behind it); ``tests/test_vps_readiness.py`` fails if the
    two drift.
    """
    values: dict[str, int] = {}
    for raw in _read_file(str(THRESHOLDS_PATH)).splitlines():
        match = _KEY_VALUE_RE.match(raw.strip())
        if match:
            values[match.group(1)] = int(match.group(2))
    return values


# ── Individual probes ───────────────────────────────────────────────────
#
# Each returns raw measurements, or None per field when unmeasurable. The
# threshold comparisons and the wording live in `probes()` below, so the shell
# port in preflight.sh only has to mirror one place.


CPUFREQ_MAX = "/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq"


def cpu_info() -> tuple[Optional[int], Optional[str], Optional[float]]:
    """(cores, model name, MHz). Any field may be None.

    MHz comes from cpufreq's *max* frequency when the kernel exposes it, and
    only falls back to /proc/cpuinfo's ``cpu MHz``. On a box with frequency
    scaling ``cpu MHz`` is the current, idle-throttled clock (a 4.6 GHz laptop
    reads 800 MHz), which would warn on hardware that is in fact fast. A VPS
    guest normally has no cpufreq and its ``cpu MHz`` is the host's fixed
    clock — the number this check actually wants.
    """
    if IS_WINDOWS:
        return None, None, None
    cores: Optional[int] = None
    model: Optional[str] = None
    mhz: Optional[float] = None
    try:
        cores = os.cpu_count()
    except _READ_ERRORS:
        cores = None
    try:
        mhz = int(_read_file(CPUFREQ_MAX).strip()) / 1000.0
    except _READ_ERRORS:
        mhz = None
    try:
        for line in _read_file("/proc/cpuinfo").splitlines():
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            if key == "model name" and model is None:
                model = value
            elif key == "cpu MHz" and mhz is None:
                mhz = float(value)
    except _READ_ERRORS:
        pass
    return cores, model, mhz


def _cpu_times() -> Optional[list[int]]:
    """The aggregate ``cpu`` line of /proc/stat as ints, or None when it does
    not carry the steal field (index 7) this box needs."""
    for line in _read_file("/proc/stat").splitlines():
        if line.startswith("cpu "):
            fields = [int(v) for v in line.split()[1:]]
            return fields if len(fields) >= 8 else None
    return None


def cpu_steal_pct(sample_seconds: float = 0.5, _sleep=time.sleep) -> Optional[float]:
    """Percent of CPU time stolen by the hypervisor, measured over a window.

    A single /proc/stat read gives steal cumulative since boot, which on a
    long-lived box is close to useless — so sample twice and report the delta.
    ``_sleep`` is injected so tests do not actually sleep.
    """
    if IS_WINDOWS:
        return None
    try:
        first = _cpu_times()
        _sleep(sample_seconds)
        second = _cpu_times()
    except _READ_ERRORS:
        return None
    if first is None or second is None:
        return None
    total_delta = sum(second) - sum(first)
    if total_delta <= 0:
        return None
    return 100.0 * (second[7] - first[7]) / total_delta


def mem_mb() -> tuple[Optional[int], Optional[int]]:
    """(MemTotal, SwapTotal) in MB."""
    if IS_WINDOWS:
        return None, None
    total = swap = None
    try:
        for line in _read_file("/proc/meminfo").splitlines():
            key, _, value = line.partition(":")
            if key == "MemTotal":
                total = int(value.split()[0]) // 1024
            elif key == "SwapTotal":
                swap = int(value.split()[0]) // 1024
    except _READ_ERRORS:
        return None, None
    return total, swap


# Advertised RAM sizes a VPS plan is sold as, in MB. Mirrored into
# preflight.sh's generated block.
RAM_SIZES_MB = (
    512, 1024, 2048, 3072, 4096, 6144, 8192, 12288,
    16384, 24576, 32768, 49152, 65536, 98304, 131072,
)


def advertised_ram_mb(measured_mb: Optional[int]) -> Optional[int]:
    """``measured_mb`` normalised to the plan size the box is sold as.

    A guest never presents its nominal size — kernel image plus reserved
    regions take a slice, and the slice is **not a constant**: two VPS
    plans, both sold as 4 GB, reported 3814 MB and 3557 MB (RHEL's
    default ``crashkernel`` reservation explains most of the 257 MB between
    them). Comparing ``MemTotal`` against ``MIN_RAM_MB=4096`` therefore told
    every buyer of the entry plan their 4 GB box did not have about 4 GB. So
    the *measurement* is normalised and the threshold keeps meaning "4 GB
    advertised" — lowering the number cannot work, because no single number is
    below both boxes and above a genuinely undersized one.

    Rounds **up only, and only within ``RAM_ROUND_TOLERANCE_PCT``**: a box that
    really has 2900 MB is not a 4 GB box and must keep failing. What keeps a
    1 GB or 2 GB box out is the ladder rather than the tolerance — 1024 and
    2048 are on it, so such a box rounds to itself and stays far below
    ``MIN_RAM_MB``. Returns the measurement unchanged when nothing fits.
    """
    if measured_mb is None:
        return None
    slack = 100 - thresholds_or_defaults()["RAM_ROUND_TOLERANCE_PCT"]
    for size in RAM_SIZES_MB:
        if measured_mb > size:
            continue
        return size if measured_mb * 100 >= size * slack else measured_mb
    return measured_mb


def disk_free_gb(path: Path) -> Optional[float]:
    """Free space on the filesystem holding ``path``, in GB."""
    try:
        return shutil.disk_usage(str(path)).free / (1024**3)
    except _READ_ERRORS:
        return None


def _device_of(path) -> int:
    """The filesystem `path` lives on. Raises `OSError`; callers report 0."""
    return os.stat(path).st_dev


def swapfile_gb_on(path: Path, *, _device_of=_device_of) -> float:
    """Swap held in *files* on the same filesystem as ``path``, in GB.

    F3: the Swap rule tells a small box to add swap, and on a 40 GB disk a 4 GB
    swapfile then pushed free space from 34.3 GB to 29.5 GB — under
    `MIN_DISK_GB`. Following one check's advice failed another, on the plan the
    docs recommend. The space is spent on this checklist's own instruction, so
    it does not count against the disk rule.

    Two exclusions, both deliberate:

    * **Swap partitions.** They live on another block device and never took a
      byte from the filesystem being measured, so counting them would inflate
      the answer on a box that genuinely has no room.
    * **A swapfile we cannot locate.** If `/proc/swaps` or `os.stat` will not
      answer, it contributes nothing — "could not tell" must not mask a real
      shortfall, which is the same rule the rest of this module follows.

    Must stay in step with preflight.sh's `SWAPFILE_GB` block.

    ``_device_of`` is injected the same way ``cpu_steal_pct`` injects
    ``_sleep``: a test needs two filesystems to exercise the exclusion and a
    checkout only has one, and monkeypatching the real `os.stat` reaches
    pytest's own tmpdir cleanup.
    """
    try:
        target_dev = _device_of(path)
        lines = _read_file("/proc/swaps").splitlines()
    except _READ_ERRORS:
        return 0.0
    total_kb = 0
    for line in lines[1:]:  # line 0 is the header
        fields = line.split()
        if len(fields) < 3 or fields[1] != "file":
            continue
        try:
            if _device_of(fields[0]) != target_dev:
                continue
            total_kb += int(fields[2])
        except _READ_ERRORS:
            continue
    return total_kb / (1024**2)


# The kernel's "not enforcing a limit" value, as `/proc/sys/fs/file-max`
# spells it: LONG_MAX. Measured 9223372036854775807 on a stock VPS,
# and printed verbatim it reads as noise rather than as good news (F12).
# `ulimit -Hn` spells the same fact as the word "unlimited", and
# `resource.RLIM_INFINITY` is -1 — three spellings, one meaning.
NO_LIMIT = 2 ** 63 - 1


def _fmt_limit(value: Optional[int]) -> str:
    """A limit for display: the number, ``unlimited``, or ``?``.

    The three are genuinely different facts and used to collapse into two:
    ``?`` meant both "could not read it" and "there is no limit", and a real
    no-limit value printed as twenty digits. Must stay in step with
    preflight.sh's `FILE_MAX_TEXT` / `NOFILE_TEXT`.
    """
    if value is None:
        return "?"
    return "unlimited" if value >= NO_LIMIT else str(value)


def file_handle_limits() -> tuple[Optional[int], Optional[int], Optional[int]]:
    """(fs.file-max, RLIMIT_NOFILE soft, RLIMIT_NOFILE hard).

    Host level only. The server unit already sets ``LimitNOFILE=65536``
    (app/templates/dayz-server.service.j2) — that is not re-checked here.

    The **hard** limit is what the checklist judges. systemd's
    ``DefaultLimitNOFILESoft`` is 1024 on a stock box and a login shell
    commonly inherits it, so comparing the *soft* limit warned on nearly every
    fresh VPS — about something that does not affect the server and that the
    user cannot act on. The hard limit is the ceiling they could actually raise.

    ``None`` means **could not read it**, and only that. An unlimited rlimit
    comes back as `NO_LIMIT` rather than `None`: `resource.RLIM_INFINITY` is
    **-1** on Linux, so returning it raw would read as a limit of -1 and fail
    every comparison below, while mapping it to `None` made "there is no limit"
    indistinguishable from "I could not look". See `_fmt_limit`.
    """
    if IS_WINDOWS:
        return None, None, None
    file_max = soft = hard = None
    try:
        file_max = int(_read_file("/proc/sys/fs/file-max").strip())
    except _READ_ERRORS:
        file_max = None
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        if soft == resource.RLIM_INFINITY:
            soft = NO_LIMIT
        if hard == resource.RLIM_INFINITY:
            hard = NO_LIMIT
    except _READ_ERRORS:
        soft = hard = None
    return file_max, soft, hard


def clock_synced() -> Optional[bool]:
    """Whether the clock is NTP-synchronised. None without timedatectl (a
    container, a non-systemd box). Steam auth and A2S both dislike a skew."""
    if IS_WINDOWS:
        return None
    try:
        out = _run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    except _READ_ERRORS:
        return None
    answer = out.strip().lower()
    if answer in ("yes", "no"):
        return answer == "yes"
    return None


def noexec_mounts(paths: Iterable[Path]) -> list[Path]:
    """Which of ``paths`` sit on a filesystem mounted ``noexec``.

    Used by preflight.sh's port of this checklist; zsm's own directories are
    covered post-install by the server-directory check.

    **Intentionally uncalled from Python.** Plan 23's checklist scoped the
    ``noexec`` check to ``preflight.sh`` only — it is a pre-purchase question,
    not a post-install one — and this exists for TODO-2 **A3**
    (``bootstrap-vps``), which will want it. It is not cruft to delete, and it
    is not an oversight to wire into ``probes()`` either; adding a doctor row
    for it is a decision, not a tidy-up.
    """
    if IS_WINDOWS:
        return []
    try:
        mounts: list[tuple[str, set[str]]] = []
        for line in _read_file("/proc/mounts").splitlines():
            fields = line.split()
            if len(fields) >= 4:
                mounts.append((fields[1], set(fields[3].split(","))))
    except _READ_ERRORS:
        return []
    hits: list[Path] = []
    for path in paths:
        target = str(path)
        # Longest matching mountpoint wins — /home shadows / for /home/user.
        best = ""
        best_opts: set[str] = set()
        for point, opts in mounts:
            if (target == point or target.startswith(point.rstrip("/") + "/")) and len(
                point
            ) >= len(best):
                best, best_opts = point, opts
        if best and "noexec" in best_opts:
            hits.append(path)
    return hits


def provider_vendor() -> Optional[str]:
    """The DMI system vendor (Hetzner, Contabo, Vultr, ...). Feeds the hosting
    comparison advice (TODO-2 C7/F2); never a pass/fail signal."""
    if IS_WINDOWS:
        return None
    try:
        vendor = _read_file("/sys/class/dmi/id/sys_vendor").strip()
    except _READ_ERRORS:
        return None
    return vendor or None


def kernel_and_distro() -> tuple[Optional[str], Optional[str]]:
    """(kernel release, distro PRETTY_NAME). Informational."""
    if IS_WINDOWS:
        return None, None
    kernel = distro = None
    try:
        kernel = os.uname().release
    except _READ_ERRORS:
        kernel = None
    try:
        for line in _read_file("/etc/os-release").splitlines():
            if line.startswith("PRETTY_NAME="):
                distro = line.partition("=")[2].strip().strip('"')
                break
    except _READ_ERRORS:
        distro = None
    return kernel, distro


# ── Verdict ─────────────────────────────────────────────────────────────
#
# Mirrored verbatim in preflight.sh so the two artefacts never disagree about
# the same machine. tests/test_vps_readiness.py pins the copies together.

VERDICT_UNKNOWN = "Could not measure RAM — no verdict."
VERDICT_TOO_SMALL = "Too small for a DayZ server — vanilla needs about 4 GB RAM."
VERDICT_VANILLA_SMALL = "Fit for vanilla, roughly 20 slots. Not enough for heavy mods."
VERDICT_VANILLA = "Fit for vanilla up to roughly 60 slots, or a few light mods."
VERDICT_MODDED_LIGHT = "Fit for a lightly modded server, roughly 40 slots."
VERDICT_MODDED_HEAVY = "Fit for a heavily modded server (Expansion + 40-60 players)."
VERDICT_STEAL_SUFFIX = "Shared vCPU — expect stutter under load."


def verdict(ram_mb: Optional[int], steal_pct: Optional[float] = None) -> str:
    """One-line fitness summary from a *measured* MemTotal. Same tiers as
    preflight.sh.

    Takes the raw measurement and normalises it here, so the verdict and the
    RAM row cannot disagree on the same screen. The four boundaries used to be
    literals in this function and a third copy of the thresholds; they are in
    ``vps-thresholds.env`` now, which is where the file already claimed they
    were.
    """
    if ram_mb is None:
        return VERDICT_UNKNOWN
    limits = thresholds_or_defaults()
    advertised = advertised_ram_mb(ram_mb)
    if advertised < limits["MIN_RAM_MB"]:
        line = VERDICT_TOO_SMALL
    elif advertised < limits["TIER_VANILLA_MB"]:
        line = VERDICT_VANILLA_SMALL
    elif advertised < limits["TIER_MODDED_LIGHT_MB"]:
        line = VERDICT_VANILLA
    elif advertised < limits["TIER_MODDED_HEAVY_MB"]:
        line = VERDICT_MODDED_LIGHT
    else:
        line = VERDICT_MODDED_HEAVY
    if steal_pct is not None and steal_pct > limits["MAX_STEAL_PCT"]:
        line = f"{line} {VERDICT_STEAL_SUFFIX}"
    return line


def thresholds_or_defaults() -> dict[str, int]:
    """``thresholds()`` that degrades instead of raising — a packaging accident
    must not take doctor down with it."""
    try:
        return thresholds()
    except _READ_ERRORS:
        return {
            "MIN_CORES": 2,
            "MIN_MHZ": 2400,
            "MAX_STEAL_PCT": 5,
            "MIN_RAM_MB": 4096,
            "RAM_ROUND_TOLERANCE_PCT": 15,
            "TIER_VANILLA_MB": 6144,
            "TIER_MODDED_LIGHT_MB": 8192,
            "TIER_MODDED_HEAVY_MB": 16384,
            "SWAP_REQUIRED_BELOW_MB": 12288,
            "MIN_DISK_GB": 30,
            "MIN_FILE_MAX": 100000,
            "MIN_NOFILE": 65536,
        }


# ── The checklist ───────────────────────────────────────────────────────


def _disk_path(config) -> Path:
    """The filesystem the server will live on: its dir when known, else $HOME."""
    if config is not None:
        try:
            server_dir = config.server_dir_path
            if server_dir and Path(server_dir).is_dir():
                return Path(server_dir)
        except (AttributeError, OSError, ValueError):
            pass
    return Path.home()


def probes(config=None) -> list[Probe]:
    """All host probes, in checklist order.

    ``config`` optional — only the disk probe uses it, to measure the volume the
    server dir actually sits on. Empty on Windows: every source here is /proc.
    """
    if IS_WINDOWS:
        return []

    limits = thresholds_or_defaults()
    out: list[Probe] = []

    cores, model, mhz = cpu_info()
    if cores is None and model is None and mhz is None:
        out.append(Probe("CPU", True, "unknown — could not read CPU info"))
    else:
        bits = [f"{cores} core(s)" if cores else "cores unknown"]
        if model:
            bits.append(model)
        if mhz:
            bits.append(f"{mhz:.0f} MHz")
        detail = ", ".join(bits)
        problems = []
        if cores is not None and cores < limits["MIN_CORES"]:
            problems.append(f"fewer than {limits['MIN_CORES']} cores")
        if mhz is not None and mhz < limits["MIN_MHZ"]:
            problems.append(f"below {limits['MIN_MHZ']} MHz")
        if problems:
            detail = (
                f"{detail} — {' and '.join(problems)}. DayZ is single-thread bound, "
                "so clock speed matters more than core count."
            )
        out.append(Probe("CPU", not problems, detail, float(cores) if cores else None))

    steal = cpu_steal_pct()
    if steal is None:
        out.append(Probe("CPU steal", True, "unknown — /proc/stat has no steal field"))
    else:
        ok = steal <= limits["MAX_STEAL_PCT"]
        detail = f"{steal:.1f}% stolen by the hypervisor"
        if not ok:
            detail += (
                f" — over {limits['MAX_STEAL_PCT']}%. This is a shared vCPU under "
                "contention; expect stutter regardless of RAM."
            )
        out.append(Probe("CPU steal", ok, detail, steal))

    ram, swap = mem_mb()
    if ram is None:
        out.append(Probe("RAM", True, "unknown — could not read /proc/meminfo"))
    else:
        advertised = advertised_ram_mb(ram)
        ok = advertised >= limits["MIN_RAM_MB"]
        # The measured figure stays on screen — rounding is for the comparison
        # only, and hiding the real number would make this check unfalsifiable.
        detail = f"{ram} MB total"
        if advertised != ram and advertised >= 1024:
            gb = advertised // 1024
            detail += f" ({'an' if str(gb).startswith('8') else 'a'} {gb} GB box)"
        if not ok:
            detail += (
                f" — below {limits['MIN_RAM_MB']} MB. Enough for vanilla with a "
                "handful of slots; expect trouble above that."
            )
        out.append(Probe("RAM", ok, detail, float(ram)))

    if swap is None or ram is None:
        out.append(Probe("Swap", True, "unknown — could not read /proc/meminfo"))
    else:
        # Advertised size, exactly like the RAM row above: a box sold as 12 GB
        # presents ~11800 MB, and comparing that directly told its owner to add
        # swap the swap rule does not ask for. Same bug as F1, found while
        # fixing it (F22).
        needed = advertised_ram_mb(ram) < limits["SWAP_REQUIRED_BELOW_MB"]
        ok = swap > 0 or not needed
        if swap > 0:
            detail = f"{swap} MB"
        elif needed:
            detail = (
                f"none, with only {ram} MB RAM — add swap below "
                f"{limits['SWAP_REQUIRED_BELOW_MB']} MB RAM or the server is killed "
                "by the OOM reaper on a mod-heavy restart."
            )
        else:
            detail = f"none — not required above {limits['SWAP_REQUIRED_BELOW_MB']} MB RAM"
        out.append(Probe("Swap", ok, detail, float(swap)))

    disk_path = _disk_path(config)
    free = disk_free_gb(disk_path)
    if free is None:
        out.append(Probe("Disk space", True, f"unknown — could not stat {disk_path}"))
    else:
        # Swap the checklist asked for does not count against the checklist's
        # own disk rule — F3. Same discipline as the RAM row above: the
        # comparison is adjusted, the **measured** figure stays on screen, and
        # the adjustment is named so the row can be argued with.
        swapfile = swapfile_gb_on(disk_path)
        ok = free + swapfile >= limits["MIN_DISK_GB"]
        detail = f"{free:.1f} GB free on {disk_path}"
        if swapfile > 0:
            detail += f" (+{swapfile:.1f} GB held by a swap file on this disk)"
        if not ok:
            detail += (
                f" — below {limits['MIN_DISK_GB']} GB. The server plus mods and "
                "backups will not fit."
            )
        out.append(Probe("Disk space", ok, detail, free))

    file_max, _soft_nofile, hard_nofile = file_handle_limits()
    if file_max is None and hard_nofile is None:
        out.append(Probe("File handles", True, "unknown — could not read the limits"))
    else:
        problems = []
        if file_max is not None and file_max < limits["MIN_FILE_MAX"]:
            problems.append(f"fs.file-max {file_max} < {limits['MIN_FILE_MAX']}")
        # Hard limit, not soft — see file_handle_limits().
        if hard_nofile is not None and hard_nofile < limits["MIN_NOFILE"]:
            problems.append(f"ulimit -Hn {hard_nofile} < {limits['MIN_NOFILE']}")
        detail = (
            f"fs.file-max {_fmt_limit(file_max)}, "
            f"hard nofile {_fmt_limit(hard_nofile)} "
            "(the server unit pins its own LimitNOFILE=65536)"
        )
        if problems:
            detail = "; ".join(problems)
        out.append(
            Probe("File handles", not problems, detail, float(file_max or 0) or None)
        )

    synced = clock_synced()
    if synced is None:
        out.append(Probe("Clock", True, "unknown — timedatectl unavailable"))
    elif synced:
        out.append(Probe("Clock", True, "NTP synchronised"))
    else:
        out.append(
            Probe(
                "Clock",
                False,
                "not NTP synchronised — Steam login and the A2S server browser "
                "both reject a skewed clock. Run: sudo timedatectl set-ntp true",
            )
        )

    kernel, distro = kernel_and_distro()
    vendor = provider_vendor()
    bits = [b for b in (distro, f"kernel {kernel}" if kernel else None, vendor) if b]
    out.append(Probe("Host", True, ", ".join(bits) if bits else "unknown"))

    return out
