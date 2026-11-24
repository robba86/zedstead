"""Tests for app/ops/vps_readiness.py and its shell twin, preflight.sh.

The tests that matter most here are the two drift tests: preflight.sh carries
inline copies of the thresholds and of the verdict wording (it is fetched over
curl with no repo behind it), and the whole point of plan 23 is that the two
artefacts never give different answers about the same machine.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.ops import vps_readiness
from app.ops.vps_readiness import Probe, cpu_steal_pct, probes, thresholds

PREFLIGHT_SH = Path(__file__).resolve().parents[1] / "preflight.sh"

_EXPECTED_KEYS = {
    "MIN_CORES",
    "MIN_MHZ",
    "MAX_STEAL_PCT",
    "MIN_RAM_MB",
    "RAM_ROUND_TOLERANCE_PCT",
    "TIER_VANILLA_MB",
    "TIER_MODDED_LIGHT_MB",
    "TIER_MODDED_HEAVY_MB",
    "SWAP_REQUIRED_BELOW_MB",
    "MIN_DISK_GB",
    "MIN_FILE_MAX",
    "MIN_NOFILE",
}


def _generated_block(source: str, marker: str) -> str:
    """The text between preflight.sh's BEGIN/END markers for `marker`."""
    match = re.search(
        rf"^# --- BEGIN generated from {re.escape(marker)} ---$(.*?)"
        rf"^# --- END generated from {re.escape(marker)} ---$",
        source,
        re.M | re.S,
    )
    assert match, f"generated block for {marker} not found in preflight.sh"
    return match.group(1)


def _by_label(found: list[Probe], label: str) -> Probe:
    for probe in found:
        if probe.label == label:
            return probe
    raise AssertionError(f"no {label} probe in {[p.label for p in found]}")


# --- thresholds -----------------------------------------------------------


def test_thresholds_parse():
    values = thresholds()
    assert set(values) == _EXPECTED_KEYS
    assert all(isinstance(v, int) for v in values.values())


def test_preflight_sh_thresholds_match_file():
    """The anti-drift test A5b exists for: edit a number in either copy and
    this fails."""
    block = _generated_block(
        PREFLIGHT_SH.read_text(encoding="utf-8"), "app/data/vps-thresholds.env"
    )
    inlined = {
        m.group(1): int(m.group(2))
        for m in re.finditer(r"^([A-Z0-9_]+)=(-?\d+)$", block, re.M)
    }
    assert inlined == thresholds()


def test_threshold_fallback_matches_the_env_file(monkeypatch):
    """``thresholds_or_defaults()``'s hardcoded fallback is a third copy of the
    numbers. It exists so a packaging accident cannot take doctor down with it,
    which is why it must *not* be derived from the file it stands in for — a
    test is the right tool, and until now there was none. The two drift tests
    above compare the env file to preflight.sh, not to this dict. See
    plans/28 C2."""
    real = thresholds()

    def _raise():
        raise OSError("simulated packaging accident")

    monkeypatch.setattr(vps_readiness, "thresholds", _raise)
    assert vps_readiness.thresholds_or_defaults() == real


def test_preflight_sh_verdict_wording_matches_module():
    """Both artefacts must print the same verdict on the same machine."""
    block = _generated_block(
        PREFLIGHT_SH.read_text(encoding="utf-8"), "app/ops/vps_readiness.py"
    )
    inlined = {
        m.group(1): m.group(2)
        for m in re.finditer(r'^(VERDICT_[A-Z_]+)="(.*)"$', block, re.M)
    }
    from_module = {
        name: getattr(vps_readiness, name)
        for name in dir(vps_readiness)
        if name.startswith("VERDICT_")
    }
    assert inlined == from_module


def test_preflight_sh_ram_ladder_matches_module():
    """The advertised-size ladder is the other half of the RAM comparison, so
    it drifts the same way the thresholds would."""
    block = _generated_block(
        PREFLIGHT_SH.read_text(encoding="utf-8"), "app/ops/vps_readiness.py"
    )
    match = re.search(r'^RAM_SIZES_MB="([^"]*)"$', block, re.M)
    assert match, "RAM_SIZES_MB not found in preflight.sh"
    assert [int(v) for v in match.group(1).split()] == list(vps_readiness.RAM_SIZES_MB)


@pytest.mark.skipif(shutil.which("dash") is None, reason="dash not installed")
def test_preflight_sh_advertised_ram_matches_python():
    """The shell port of ``advertised_ram_mb`` must answer identically — the
    two artefacts are supposed to give the same verdict on the same machine,
    and the rounding is now part of that answer."""
    source = PREFLIGHT_SH.read_text(encoding="utf-8")
    header = source.split("printf '\\n\\033[1mzsm preflight", 1)[0]
    cases = [0, 980, 1900, 2900, 3300, 3481, 3482, 3557, 3814, 4096, 11800, 200000]
    script = header + "\n".join(
        f'advertised_ram_mb {n}; printf "\\n"' for n in cases
    )
    result = subprocess.run(
        ["dash", "-c", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    from_shell = [int(v) for v in result.stdout.split()]
    assert from_shell == [vps_readiness.advertised_ram_mb(n) for n in cases]


@pytest.mark.skipif(shutil.which("dash") is None, reason="dash not installed")
def test_preflight_sh_is_posix():
    result = subprocess.run(
        ["dash", "-n", str(PREFLIGHT_SH)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


# --- CPU steal ------------------------------------------------------------

_STAT_1 = "cpu  100 0 100 1000 0 0 0 0 0 0\nintr 1\n"
# +100 user, +100 idle, +100 steal over the window: 100/300 = 33.3 %.
_STAT_2 = "cpu  200 0 100 1100 0 0 0 100 0 0\nintr 1\n"


def test_cpu_steal_two_samples(monkeypatch):
    bodies = iter([_STAT_1, _STAT_2])
    monkeypatch.setattr(vps_readiness, "_read_file", lambda _p: next(bodies))
    monkeypatch.setattr(vps_readiness, "IS_WINDOWS", False)

    slept: list[float] = []
    steal = cpu_steal_pct(sample_seconds=0.5, _sleep=slept.append)

    assert slept == [0.5]  # injected, so the suite never actually sleeps
    assert steal == pytest.approx(100 / 3, abs=0.01)


def test_cpu_steal_none_without_proc(monkeypatch):
    def _raise(_path):
        raise OSError("no /proc")

    monkeypatch.setattr(vps_readiness, "_read_file", _raise)
    monkeypatch.setattr(vps_readiness, "IS_WINDOWS", False)
    assert cpu_steal_pct(_sleep=lambda _s: None) is None


def test_cpu_steal_none_without_steal_field(monkeypatch):
    """A kernel whose cpu line stops before field 8 cannot report steal."""
    short = "cpu  100 0 100 1000 0 0 0\n"
    monkeypatch.setattr(vps_readiness, "_read_file", lambda _p: short)
    monkeypatch.setattr(vps_readiness, "IS_WINDOWS", False)
    assert cpu_steal_pct(_sleep=lambda _s: None) is None


# --- swap -----------------------------------------------------------------


def test_swap_probe_passes_with_large_ram(monkeypatch):
    monkeypatch.setattr(vps_readiness, "IS_WINDOWS", False)
    monkeypatch.setattr(vps_readiness, "mem_mb", lambda: (16384, 0))
    swap = _by_label(probes(), "Swap")
    assert swap.ok is True
    assert "not required" in swap.detail


def test_swap_probe_warns_on_small_ram_no_swap(monkeypatch):
    monkeypatch.setattr(vps_readiness, "IS_WINDOWS", False)
    monkeypatch.setattr(vps_readiness, "mem_mb", lambda: (4096, 0))
    swap = _by_label(probes(), "Swap")
    assert swap.ok is False
    assert "swap" in swap.detail.lower()


def test_swap_not_required_on_a_box_sold_as_twelve_gb(monkeypatch):
    """F22: the swap rule is "swap below 12 GB RAM", and a box sold as 12 GB
    presents ~11800 MB. Comparing MemTotal directly told its owner to add swap
    the rule does not ask for — the same bug as the RAM row's, one probe over.
    """
    monkeypatch.setattr(vps_readiness, "IS_WINDOWS", False)
    monkeypatch.setattr(vps_readiness, "mem_mb", lambda: (11800, 0))
    swap = _by_label(probes(), "Swap")
    assert swap.ok is True
    assert "not required" in swap.detail


def test_swap_still_required_on_a_box_that_really_is_small(monkeypatch):
    """The rounding must not excuse a box the swap rule is actually for: 8 GB
    is on the ladder, so an 8 GB box rounds to itself and stays under 12288."""
    monkeypatch.setattr(vps_readiness, "IS_WINDOWS", False)
    monkeypatch.setattr(vps_readiness, "mem_mb", lambda: (7900, 0))
    swap = _by_label(probes(), "Swap")
    assert swap.ok is False
    assert "7900 MB RAM" in swap.detail  # the measured figure, as everywhere


# --- RAM is judged as advertised size, not as MemTotal --------------------
#
# Both figures are real: two VPS plans, both sold as 4 GB.


@pytest.mark.parametrize("measured", [3557, 3814])
def test_a_four_gb_box_passes_the_ram_check(monkeypatch, measured):
    monkeypatch.setattr(vps_readiness, "IS_WINDOWS", False)
    monkeypatch.setattr(vps_readiness, "mem_mb", lambda: (measured, 0))
    ram = _by_label(probes(), "RAM")
    assert ram.ok is True, ram.detail
    assert vps_readiness.verdict(measured) == vps_readiness.VERDICT_VANILLA_SMALL


@pytest.mark.parametrize("measured", [3557, 3814])
def test_the_ram_row_prints_the_measured_figure(monkeypatch, measured):
    """Rounding is for the comparison only. Hiding the real number would make
    the check unfalsifiable."""
    monkeypatch.setattr(vps_readiness, "IS_WINDOWS", False)
    monkeypatch.setattr(vps_readiness, "mem_mb", lambda: (measured, 0))
    ram = _by_label(probes(), "RAM")
    assert f"{measured} MB total" in ram.detail
    assert "(a 4 GB box)" in ram.detail
    assert ram.value == float(measured)


@pytest.mark.parametrize("measured", [980, 1900, 2900])
def test_an_undersized_box_still_fails(monkeypatch, measured):
    """The STOP condition of plan 30 A: the rounding must not lift a 1 GB or
    2 GB box over the bar. The ladder is what does that work — 1024, 2048 and
    3072 are on it, so such a box rounds to itself."""
    monkeypatch.setattr(vps_readiness, "IS_WINDOWS", False)
    monkeypatch.setattr(vps_readiness, "mem_mb", lambda: (measured, 0))
    ram = _by_label(probes(), "RAM")
    assert ram.ok is False
    assert f"{measured} MB total" in ram.detail
    assert vps_readiness.verdict(measured) == vps_readiness.VERDICT_TOO_SMALL


@pytest.mark.parametrize("measured", [980, 1900, 2900, 3300, 3557, 3814, 4096, 11800, 16000])
def test_verdict_and_the_ram_row_cannot_disagree(monkeypatch, measured):
    """The bug A2 guards against: the verdict carried its own copy of the
    boundaries, so it could call a box too small while the RAM row on the same
    screen passed it. Both now read MIN_RAM_MB, through the same rounding."""
    monkeypatch.setattr(vps_readiness, "IS_WINDOWS", False)
    monkeypatch.setattr(vps_readiness, "mem_mb", lambda: (measured, 0))
    ram = _by_label(probes(), "RAM")
    too_small = vps_readiness.verdict(measured) == vps_readiness.VERDICT_TOO_SMALL
    assert ram.ok is not too_small


def test_advertised_ram_passes_none_through():
    assert vps_readiness.advertised_ram_mb(None) is None


# --- unmeasurable degrades to a pass --------------------------------------


def test_unmeasurable_probe_is_ok(monkeypatch):
    """Decision 5, pinned: a probe that cannot measure reports unknown and
    passes. A check failing because it could not measure trains users to
    ignore doctor."""

    def _raise(*_a, **_kw):
        raise OSError("nothing readable here")

    monkeypatch.setattr(vps_readiness, "IS_WINDOWS", False)
    monkeypatch.setattr(vps_readiness, "_read_file", _raise)
    monkeypatch.setattr(vps_readiness, "_run", _raise)
    monkeypatch.setattr(vps_readiness.shutil, "disk_usage", _raise)
    monkeypatch.setattr(vps_readiness.resource, "getrlimit", _raise)
    monkeypatch.setattr(vps_readiness.os, "cpu_count", _raise)
    monkeypatch.setattr(vps_readiness.os, "uname", _raise)

    found = probes()
    assert found, "probes() must still return the full checklist"
    for probe in found:
        assert probe.ok is True, f"{probe.label} failed because it could not measure"
        assert "unknown" in probe.detail.lower(), f"{probe.label}: {probe.detail}"


# --- disk vs. the swap advice (F3) -----------------------------------------


_SWAPS_FILE = (
    "Filename\t\t\t\tType\t\tSize\tUsed\tPriority\n"
    "/swapfile                               file\t\t4194300\t0\t-1\n"
)
_SWAPS_PARTITION = (
    "Filename\t\t\t\tType\t\tSize\tUsed\tPriority\n"
    "/dev/sda2                               partition\t4194300\t0\t-1\n"
)


def _devices(mapping, default=99):
    """A `_device_of` stand-in. Injected rather than monkeypatched: the real
    `os.stat` is also pytest's tmpdir cleanup, and a checkout has only one
    filesystem so the exclusions cannot be exercised for real."""
    return lambda target: mapping.get(str(target), default)


def test_swapfile_on_the_same_filesystem_counts(monkeypatch):
    monkeypatch.setattr(vps_readiness, "_read_file", lambda _p: _SWAPS_FILE)
    same = _devices({"/home/zsm": 1, "/swapfile": 1})
    assert vps_readiness.swapfile_gb_on(
        Path("/home/zsm"), _device_of=same
    ) == pytest.approx(4.0, abs=0.01)


def test_a_swap_partition_never_counts(monkeypatch):
    """It lives on another block device and never took a byte from the
    filesystem being measured, so crediting it would inflate the answer on a
    box that genuinely has no room."""
    monkeypatch.setattr(vps_readiness, "_read_file", lambda _p: _SWAPS_PARTITION)
    same = _devices({"/home/zsm": 1, "/dev/sda2": 1})
    assert vps_readiness.swapfile_gb_on(Path("/home/zsm"), _device_of=same) == 0.0


def test_a_swapfile_on_another_filesystem_never_counts(monkeypatch):
    """The dev box this was written on is the case: / and /home are separate
    filesystems, so its 4 GB /swap.img is correctly worth nothing to /home."""
    monkeypatch.setattr(vps_readiness, "_read_file", lambda _p: _SWAPS_FILE)
    split = _devices({"/home/zsm": 1, "/swapfile": 2})
    assert vps_readiness.swapfile_gb_on(Path("/home/zsm"), _device_of=split) == 0.0


def test_unreadable_swaps_contributes_nothing(monkeypatch):
    """"Could not tell" must not mask a real shortfall — the same rule every
    other probe in this module follows."""

    def _raise(_p):
        raise OSError("no /proc/swaps here")

    monkeypatch.setattr(vps_readiness, "_read_file", _raise)
    assert vps_readiness.swapfile_gb_on(Path("/home/zsm")) == 0.0


def test_an_unstattable_target_contributes_nothing(monkeypatch):
    monkeypatch.setattr(vps_readiness, "_read_file", lambda _p: _SWAPS_FILE)

    def _raise(_t):
        raise OSError("gone")

    assert vps_readiness.swapfile_gb_on(Path("/home/zsm"), _device_of=_raise) == 0.0


def test_the_swap_advice_no_longer_fails_the_disk_check(monkeypatch):
    """F3, reproduced: preflight told a 3814 MB box to add swap, the 4 GB
    swapfile took free space from 34.3 GB to 29.5 GB, and `MIN_DISK_GB=30` then
    warned. Following one check's advice failed another, on the plan the docs
    recommend."""
    monkeypatch.setattr(vps_readiness, "IS_WINDOWS", False)
    monkeypatch.setattr(vps_readiness, "disk_free_gb", lambda _p: 29.5)
    monkeypatch.setattr(vps_readiness, "swapfile_gb_on", lambda _p: 4.0)

    row = _by_label(probes(), "Disk space")
    assert row.ok is True
    # The measured figure stays on screen and the adjustment is named, so the
    # row can still be argued with — same discipline as the RAM row.
    assert "29.5 GB free" in row.detail
    assert "held by a swap file on this disk" in row.detail
    assert row.value == pytest.approx(29.5)


def test_a_genuinely_small_disk_still_fails(monkeypatch):
    """The fix must not quietly become "disk space is fine now". 25 GB with no
    swapfile is 25 GB."""
    monkeypatch.setattr(vps_readiness, "IS_WINDOWS", False)
    monkeypatch.setattr(vps_readiness, "disk_free_gb", lambda _p: 25.0)
    monkeypatch.setattr(vps_readiness, "swapfile_gb_on", lambda _p: 0.0)

    row = _by_label(probes(), "Disk space")
    assert row.ok is False
    assert "will not fit" in row.detail


def test_a_swapfile_cannot_rescue_an_arbitrarily_small_disk(monkeypatch):
    """4 GB of credit is 4 GB — it does not turn 20 GB into enough."""
    monkeypatch.setattr(vps_readiness, "IS_WINDOWS", False)
    monkeypatch.setattr(vps_readiness, "disk_free_gb", lambda _p: 20.0)
    monkeypatch.setattr(vps_readiness, "swapfile_gb_on", lambda _p: 4.0)
    assert _by_label(probes(), "Disk space").ok is False


def test_preflight_sh_disk_check_also_credits_the_swapfile():
    """Source-level, and that is all it proves: preflight.sh reads /proc/swaps
    itself, so there is no way to feed it a fake one without restructuring the
    script. What this catches is somebody "simplifying" one of the two ports
    and leaving the other — the drift plans/23 exists to prevent."""
    source = PREFLIGHT_SH.read_text(encoding="utf-8")
    assert "SWAPFILE_GB" in source
    assert 'SW_TYPE" = "file"' in source, "a swap partition must not be credited"
    # The credit must reach the comparison, not only the printed detail.
    assert 'd="$DISK_GB" -v s="$SWAPFILE_GB"' in source
    assert "d + s < m" in source, (
        "the swapfile is printed but not added into the threshold comparison"
    )
    # A pipeline would run the accumulator loop in a subshell and always yield 0.
    assert "done < /proc/swaps" in source


# --- file handles: "no limit" is not "could not read it" (F12) -------------


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, "?"),
        (100000, "100000"),
        (vps_readiness.NO_LIMIT, "unlimited"),
    ],
)
def test_fmt_limit_keeps_the_three_cases_apart(value, expected):
    """`?` used to mean both "could not read it" and "there is no limit", and a
    real no-limit `fs.file-max` printed as twenty digits."""
    assert vps_readiness._fmt_limit(value) == expected


def test_file_handles_row_says_unlimited_not_long_max(monkeypatch):
    """F12, measured: a stock VPS reports `fs.file-max
    9223372036854775807` — LONG_MAX, the kernel's no-limit sentinel — and the
    row printed it verbatim."""
    monkeypatch.setattr(vps_readiness, "IS_WINDOWS", False)
    monkeypatch.setattr(
        vps_readiness, "file_handle_limits", lambda: (vps_readiness.NO_LIMIT, 1024, 524288)
    )
    row = _by_label(probes(), "File handles")
    assert row.ok is True
    assert "unlimited" in row.detail
    assert "9223372036854775807" not in row.detail


def test_an_unlimited_hard_nofile_is_not_a_shortfall(monkeypatch):
    """`resource.RLIM_INFINITY` is **-1** on Linux, so a raw pass-through would
    read as a limit of -1 and fail the comparison — which is why
    `file_handle_limits` maps it to `NO_LIMIT` rather than returning it, and
    why it must not map it to `None` either (that is "could not read")."""
    monkeypatch.setattr(vps_readiness, "IS_WINDOWS", False)
    monkeypatch.setattr(
        vps_readiness.resource,
        "getrlimit",
        lambda _w: (vps_readiness.resource.RLIM_INFINITY,) * 2,
    )
    _file_max, soft, hard = vps_readiness.file_handle_limits()
    assert soft == hard == vps_readiness.NO_LIMIT

    row = _by_label(probes(), "File handles")
    assert row.ok is True
    assert "hard nofile unlimited" in row.detail


def test_file_handles_row_still_says_question_mark_when_unreadable(monkeypatch):
    """The other half: an unreadable limit must not borrow "unlimited"."""
    monkeypatch.setattr(vps_readiness, "IS_WINDOWS", False)
    monkeypatch.setattr(vps_readiness, "file_handle_limits", lambda: (None, None, 524288))
    row = _by_label(probes(), "File handles")
    assert row.ok is True
    assert "fs.file-max ?" in row.detail


def test_preflight_sh_no_limit_sentinel_matches_module():
    """preflight.sh carries the sentinel inline (it is curl'd with no repo
    behind it), so it drifts the same way the thresholds would."""
    source = PREFLIGHT_SH.read_text(encoding="utf-8")
    match = re.search(r'^NO_LIMIT="(\d+)"$', source, re.M)
    assert match, "NO_LIMIT not found in preflight.sh"
    assert int(match.group(1)) == vps_readiness.NO_LIMIT


# --- disk -----------------------------------------------------------------


def test_disk_probe_uses_server_dir_when_present(monkeypatch, sample_config, tmp_server_dir):
    monkeypatch.setattr(vps_readiness, "IS_WINDOWS", False)
    disk = _by_label(probes(sample_config), "Disk space")
    assert str(tmp_server_dir) in disk.detail


def test_disk_probe_falls_back_to_home_without_config(monkeypatch):
    monkeypatch.setattr(vps_readiness, "IS_WINDOWS", False)
    disk = _by_label(probes(None), "Disk space")
    assert str(Path.home()) in disk.detail


# --- platform -------------------------------------------------------------


def test_probes_empty_on_windows(monkeypatch):
    monkeypatch.setattr(vps_readiness, "IS_WINDOWS", True)
    assert probes() == []


# --- verdict --------------------------------------------------------------


@pytest.mark.parametrize(
    "ram_mb, expected",
    [
        (None, vps_readiness.VERDICT_UNKNOWN),
        (2048, vps_readiness.VERDICT_TOO_SMALL),
        (4096, vps_readiness.VERDICT_VANILLA_SMALL),
        (6144, vps_readiness.VERDICT_VANILLA),
        (8192, vps_readiness.VERDICT_MODDED_LIGHT),
        (16384, vps_readiness.VERDICT_MODDED_HEAVY),
    ],
)
def test_verdict_tiers(ram_mb, expected):
    assert vps_readiness.verdict(ram_mb) == expected


def test_verdict_notes_high_steal():
    line = vps_readiness.verdict(16384, steal_pct=42.0)
    assert line.startswith(vps_readiness.VERDICT_MODDED_HEAVY)
    assert vps_readiness.VERDICT_STEAL_SUFFIX in line
