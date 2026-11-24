"""Firewall inspection and (opt-in) configuration for the DayZ server ports.

Read by ``zsm doctor`` (report only) and ``zsm service firewall`` (report,
or write with ``--apply``). See plans/22 for the lockout rules that govern
``--apply``; the short version, because every one of them is load-bearing:

* A public DayZ server **has to** expose its game UDP ports. "Protecting" a
  self-hoster therefore means minimising everything *else* — deny-by-default
  with a short allowlist — not layering rules on an open box.
* ``ufw --force enable`` behind ``default deny incoming`` locks the operator
  out of their own VPS unless SSH is allowed **first**. SSH detection runs
  before anything is written, allow rules are emitted before the default
  policy, and a failed detection is a refusal, never a guess.
* Only ufw and firewalld are supported. Raw iptables/nftables rules are
  unmanaged and ordering-sensitive — that is how people lose access to a box.
* The RCON port is never opened — it is not a port a player needs, and no
  matter which interface BattlEye binds it to, allowing it can only publish
  the RCon listener (see ``rcon_collision``, which records what is actually
  known there). Neither is the web panel port: that belongs behind TLS + a
  reverse proxy.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Literal, Optional

from app.shared.platform import IS_DOCKER, IS_WINDOWS, sudo_prefix

Backend = Literal["ufw", "firewalld"]

# How ssh_ports() arrived at its answer. "unknown" means detection failed
# while sshd is listening — callers must refuse to apply.
SshSource = Literal["ss", "ssh.socket", "sshd -T", "sshd_config", "default", "unknown"]

# "ss", "ssh.socket" and "sshd -T" observe the *running* system. "sshd_config"
# is a statement of intent that a drop-in — or socket activation, which ignores
# `Port` outright — can contradict, so it stays a weaker signal even though the
# reader below follows Include. Callers surface the difference rather than
# presenting every source as equal. See plans/27, plans/29.
CONFIDENT_SOURCES = frozenset({"ss", "ssh.socket", "sshd -T"})

# Strongest first. Only used to pick the single label `ssh_ports()` reports;
# the ports themselves are the union of everything that answered.
_SOURCE_PRECEDENCE: tuple = ("ss", "ssh.socket", "sshd -T", "sshd_config")

_SUBPROCESS_ERRORS = (OSError, subprocess.SubprocessError)

SSHD_CONFIG_PATH = Path("/etc/ssh/sshd_config")
# sshd resolves a relative `Include` against its config directory, not the CWD.
SSHD_CONFIG_DIR = Path("/etc/ssh")
_MAX_INCLUDE_DEPTH = 8

UNDO_HINT = {
    "ufw": "sudo ufw disable",
    "firewalld": "sudo firewall-cmd --permanent --set-target=default --zone=<zone> && sudo firewall-cmd --reload",
}


@dataclass(frozen=True)
class PortSpec:
    port: int
    proto: str  # "udp"
    label: str  # "game", "client/voice", "steam query"

    @property
    def rule(self) -> str:
        return f"{self.port}/{self.proto}"


@dataclass(frozen=True)
class FirewallStatus:
    backend: Optional[Backend]
    active: Optional[bool]  # None = could not determine
    open_udp: set = field(default_factory=set)  # ports the backend reports as allowed
    open_tcp: set = field(default_factory=set)  # ditto, tcp — where SSH lives
    raw: str = ""  # backend output, for the detail line
    error: Optional[str] = None
    # Set *in addition to* ``error`` when the table was read fine but the answer
    # is ambiguous — today only the firewalld non-default-zone case. Purely
    # informational, so surfaces can word it correctly; ``error`` stays set so
    # every path that makes a decision (``missing_ports``, ``staged_rules``,
    # ``service.py``) keeps treating it as "do not trust this". Never branch on
    # this instead of ``error``.
    zone_conflict: Optional[str] = None


@dataclass(frozen=True)
class StagedRules:
    """Ports the backend has a *rule* for, whether or not that rule is live yet.

    Deliberately not a `FirewallStatus`: that type's ``active`` answers "is the
    firewall on", and the verification gate in `apply_rules` runs while ufw is
    typically still inactive. `ufw status` prints no rule table until
    `ufw enable`, and firewalld's ``--permanent`` additions are absent from the
    runtime zone until ``--reload`` — so reading the *live* status at the gate
    would report the rule we just added as missing and abort every run.

    ``readable`` is False only when the table could not be read at all. It never
    means "the table is empty". See plans/27 (D2).
    """

    tcp: set = field(default_factory=set)
    udp: set = field(default_factory=set)
    readable: bool = False
    detail: str = ""


# ---------------------------------------------------------------------------
# Process helpers
# ---------------------------------------------------------------------------


def _run(argv: list[str], timeout: int = 10) -> tuple[int, str, str]:
    """(returncode, stdout, stderr). A missing binary or a timeout is a
    non-zero rc with the reason in stderr — this module reports, it never
    raises for an expected failure."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, "", f"{argv[0]}: not found"
    except _SUBPROCESS_ERRORS as exc:
        return 1, "", str(exc)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _listed(ports) -> str:
    """A sorted, comma-separated port list for a human-readable line."""
    return ", ".join(str(port) for port in sorted(ports))


# ---------------------------------------------------------------------------
# Backend detection and the ports we care about
# ---------------------------------------------------------------------------


def detect_backend() -> Optional[Backend]:
    """The managed firewall installed on this host, or None.

    ufw wins when both are present: it is what Debian/Ubuntu VPS images ship
    and what the docs tell users to run.
    """
    if IS_WINDOWS:
        return None
    if shutil.which("ufw"):
        return "ufw"
    if shutil.which("firewall-cmd"):
        return "firewalld"
    return None


# What every surface says when neither backend is installed. A stock Rocky
# VPS image can ship no firewall at all (measured: `rpm -q firewalld`
# reported it not installed, and there was no ufw either), and that box —
# public IP, nothing filtering — was the one the tool said *least* about:
# `--dry-run` printed nine `sudo ufw …` commands for a binary that does not
# exist, and doctor omitted its Firewall row entirely.
NO_BACKEND_SUMMARY = (
    "No managed firewall is installed — neither ufw nor firewall-cmd. "
    "Every port on this box is reachable from the internet."
)

# os-release ID / ID_LIKE token -> the command that installs a backend zsm
# manages. ufw on the Debian family, firewalld elsewhere, matching what each
# distribution actually ships and documents.
_BACKEND_INSTALL_BY_FAMILY: tuple[tuple[str, str], ...] = (
    ("debian", "sudo apt-get install ufw"),
    ("ubuntu", "sudo apt-get install ufw"),
    ("rhel", "sudo dnf install firewalld"),
    ("fedora", "sudo dnf install firewalld"),
    ("centos", "sudo dnf install firewalld"),
    ("suse", "sudo zypper install firewalld"),
    ("arch", "sudo pacman -S ufw"),
)


OS_RELEASE_PATH = Path("/etc/os-release")


def _os_release_ids() -> list[str]:
    """``ID`` plus ``ID_LIKE`` from /etc/os-release, lowercased."""
    try:
        text = OS_RELEASE_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    found: list[str] = []
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.strip() in ("ID", "ID_LIKE"):
            found.extend(value.strip().strip('"').strip("'").split())
    return [token.lower() for token in found]


def no_backend_advice() -> str:
    """The one actionable line to print when ``detect_backend()`` is None.

    Named from /etc/os-release rather than assumed: printing ufw commands on a
    box that has no ufw is the bug this replaces, and a wrong package manager
    would be the same bug again — so an unrecognised family names both
    supported backends and stops.
    """
    ids = _os_release_ids()
    for token, command in _BACKEND_INSTALL_BY_FAMILY:
        if token in ids:
            return f"Install one with:  {command}"
    return "Zedstead manages ufw or firewalld — install whichever your distribution ships."


def required_ports(config) -> list[PortSpec]:
    """The UDP ports a public DayZ server needs reachable, de-duplicated.

    Derived from ``config.server_port`` and ``serverDZ.cfg``'s
    ``steamQueryPort`` — never hardcoded. A second server on the same box has
    moved the game port, and a fixed 2302-2306 range would open the wrong
    ports and report the right ones as closed.

    Deliberately absent: ``config.rcon_port`` and the web panel port (TLS +
    reverse proxy, not a game-port allowlist). RCon is not a port a player
    needs, so it stays off the allowlist whichever interface BattlEye binds it
    to — ``rcon_collision`` records what is actually known about that.
    """
    from app.server.server_status import get_steam_query_port
    from app.server.service_control import server_port_number

    # A bundle can store the port as a string; the unit renders "2302" as
    # 2302, so the firewall reads it the same way (plans/68 B8).
    game = server_port_number(config)
    specs = [
        PortSpec(game, "udp", "game"),
        PortSpec(game + 1, "udp", "client/voice"),
        PortSpec(game + 2, "udp", "client/voice"),
        PortSpec(game + 3, "udp", "client/voice"),
        PortSpec(get_steam_query_port(config), "udp", "steam query"),
    ]
    seen: dict[int, PortSpec] = {}
    for spec in specs:
        seen.setdefault(spec.port, spec)
    return sorted(seen.values(), key=lambda s: s.port)


def rcon_collision(config) -> Optional[PortSpec]:
    """The required game port that is *also* ``config.rcon_port``, if any.

    zsm's default ``rcon_port`` is 2305, which is the ``server_port + 3``
    client/voice port of a default install, so one allow rule covers both.

    **Measured in 2026 Q3 on a running server: the exposure is real.**
    ``ss -ulnp`` on a default install reports

        UNCONN 0 0 0.0.0.0:2305 0.0.0.0:* users:(("enfMain",pid=...,fd=151))

    — the *game server process itself* holds the RCon socket, bound to
    ``0.0.0.0``, not to loopback. So one ``allow 2305/udp`` rule really does
    publish the RCon listener to the internet along with the client/voice port.

    That settles a question this module got wrong in both directions. zsm's
    own client dials ``config.rcon_host`` (``127.0.0.1`` by default) and zsm
    leaves ``RConIP`` unset
    (``app/web/editors/server_forms.py`` drops the key when empty) — both
    true, and both about the *client* or the *config*, not evidence about the
    listener. The editor help used to call ``127.0.0.1`` BattlEye's default;
    the measurement showed it is not, and the help now says so (plans/68
    NET-2). ``dayz-docs/`` documents only ``RConPassword`` and
    ``RestrictRCon`` and never mentions ``RConIP``, so it settles nothing
    either. Only the measurement did.

    We still neither silently drop the port (that would close one players may
    need) nor silently open it without saying so: callers warn and name the
    fix. See plans/27 (D6).
    """
    for spec in required_ports(config):
        if spec.port == config.rcon_port:
            return spec
    return None


# ---------------------------------------------------------------------------
# Status (read-only, always safe)
# ---------------------------------------------------------------------------

_UFW_UDP_RE = re.compile(r"^(\d+)(?::(\d+))?/udp\b", re.IGNORECASE)
_UFW_TCP_RE = re.compile(r"^(\d+)(?::(\d+))?/tcp\b", re.IGNORECASE)
# A ufw rule written without a protocol ("ufw allow 22") covers both, and ufw
# prints it bare. Reading it as *neither* is what would make the verification
# gate refuse a box whose SSH rule is perfectly fine.
_UFW_ANY_RE = re.compile(r"^(\d+)(?::(\d+))?\s+(?=[A-Za-z])")
_UFW_ADDED_RE = re.compile(r"^(\d+)(?::(\d+))?(?:/(tcp|udp))?$", re.IGNORECASE)
_FIREWALLD_PORTS_RE = re.compile(r"^\s*ports:\s*(.*)$", re.MULTILINE)
_FIREWALLD_PORT_RE = re.compile(r"(\d+)(?:-(\d+))?/(udp|tcp)")
_FIREWALLD_SERVICES_RE = re.compile(r"^\s*services:\s*(.*)$", re.MULTILINE)
# The default policy `--apply` leaves, as each backend's live listing prints it.
_UFW_DENY_INCOMING_RE = re.compile(r"^Default:\s*(?:deny|reject)\s*\(incoming\)", re.I | re.M)
_FIREWALLD_DROP_TARGET_RE = re.compile(r"^\s*target:\s*(?:DROP|%%REJECT%%)\s*$", re.M)
# `rich rules:` entries, in firewalld's own canonical ordering and the reverse.
_FIREWALLD_RICH_PORT_RE = re.compile(
    r'port\s+port="(\d+)(?:-(\d+))?"\s+protocol="(tcp|udp)"', re.IGNORECASE
)
_FIREWALLD_RICH_PORT_ALT_RE = re.compile(
    r'port\s+protocol="(tcp|udp)"\s+port="(\d+)(?:-(\d+))?"', re.IGNORECASE
)
# A zone block's header: `public` or `public (active)`.
_FIREWALLD_ZONE_HEAD_RE = re.compile(r"^(\S+)(?:\s+\(.*\))?$")
# The one firewalld built-in service the lockout gate has to understand.
# Guessing at the rest of /usr/lib/firewalld/services would be inventing facts.
_FIREWALLD_SERVICE_TCP = {"ssh": 22}


def _read_with_sudo(argv: list[str]) -> tuple[int, str, str]:
    """Run a read-only backend command, retrying once through ``sudo -n``.

    Never bare ``sudo``: a tty-less web poll would hang on the password prompt
    (same rule as ``systemd_scope.query``). Every read path in this module uses
    this — ``firewall-cmd --list-all`` did not, which is why doctor's firewalld
    row read "needs root" on boxes where a ``sudo -n`` would have worked, while
    the ufw row beside it did not. See plans/29 (D10c).
    """
    rc, out, err = _run(argv)
    if rc != 0 and sudo_prefix():
        rc, out, err = _run(["sudo", "-n", *argv])
    return rc, out, err


def _expand(low: str, high: Optional[str]) -> Iterable[int]:
    start = int(low)
    end = int(high) if high else start
    if end < start or end - start > 4096:  # guard a nonsense range
        return [start]
    return range(start, end + 1)


def _parse_ufw_status(out: str) -> tuple[Optional[bool], set, set]:
    """(active, open_udp, open_tcp) from `ufw status`."""
    active: Optional[bool] = None
    lowered = out.lower()
    if "status: active" in lowered:
        active = True
    elif "status: inactive" in lowered:
        active = False
    open_udp: set = set()
    open_tcp: set = set()
    for line in out.splitlines():
        stripped = line.strip()
        if "allow" not in stripped.lower():
            continue
        udp = _UFW_UDP_RE.match(stripped)
        if udp:
            open_udp.update(_expand(udp.group(1), udp.group(2)))
            continue
        tcp = _UFW_TCP_RE.match(stripped)
        if tcp:
            open_tcp.update(_expand(tcp.group(1), tcp.group(2)))
            continue
        both = _UFW_ANY_RE.match(stripped)
        if both:
            ports = set(_expand(both.group(1), both.group(2)))
            open_udp.update(ports)
            open_tcp.update(ports)
    return active, open_udp, open_tcp


def _parse_ufw_added(out: str) -> tuple[set, set]:
    """(open_udp, open_tcp) from `ufw show added`.

    That subcommand lists the rule table whether or not ufw is enabled, which
    is the only view the verification gate can use — see `StagedRules`.

    Only the token straight after ``allow`` counts, so a source-restricted rule
    (``ufw allow from 1.2.3.4 to any port 22``) is *not* read as a general
    allow. Reading it as one would let the gate pass a box that is about to be
    reachable from a single address.
    """
    open_udp: set = set()
    open_tcp: set = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[0] != "ufw" or "allow" not in parts:
            continue
        idx = parts.index("allow")
        if idx + 1 >= len(parts):
            continue
        match = _UFW_ADDED_RE.match(parts[idx + 1])
        if not match:
            continue
        ports = set(_expand(match.group(1), match.group(2)))
        proto = (match.group(3) or "").lower()
        if proto in ("", "udp"):
            open_udp.update(ports)
        if proto in ("", "tcp"):
            open_tcp.update(ports)
    return open_udp, open_tcp


def _parse_firewalld_rich(out: str, *, restricted_ok: bool) -> tuple[set, set]:
    """(open_udp, open_tcp) from the ``rich rules:`` block of `--list-all`.

    ``restricted_ok`` is the whole point of the flag, and the two call sites
    answer different questions (plans/29, decision 9):

    * **Status** asks "is this port reachable?" A false FAIL is the harm, so a
      source-restricted rule counts — the port really is open, from somewhere.
    * **The gate** asks "will SSH survive the policy flip?" A false PASS is the
      harm, so ``rule family="ipv4" source address="1.2.3.4" port port="22"
      protocol="tcp" accept`` must **not** count: it leaves the box reachable
      from one address and the next command denies everyone else. That is
      exactly what `_parse_ufw_added` refuses for ``ufw allow from …``.

    A ``drop``/``reject`` rule counts for neither, and anything this cannot
    parse contributes nothing — firewalld has never been exercised on real
    hardware in this repo, so an unparsed rule reducing to today's behaviour is
    the only safe failure mode.
    """
    open_udp: set = set()
    open_tcp: set = set()
    for line in out.splitlines():
        stripped = line.strip()
        if not stripped.startswith("rule "):
            continue
        tokens = stripped.split()
        if "accept" not in tokens:
            continue
        if not restricted_ok and {"source", "destination"} & set(tokens):
            continue
        found = [(low, high, proto) for low, high, proto in _FIREWALLD_RICH_PORT_RE.findall(stripped)]
        found += [(low, high, proto) for proto, low, high in _FIREWALLD_RICH_PORT_ALT_RE.findall(stripped)]
        for low, high, proto in found:
            target = open_udp if proto.lower() == "udp" else open_tcp
            target.update(_expand(low, high or None))
    return open_udp, open_tcp


def _parse_firewalld_ports(out: str, *, restricted_ok: bool) -> Optional[tuple[set, set]]:
    """(open_udp, open_tcp) from `firewall-cmd --list-all`, or None.

    None means the output carried no ``ports:`` line at all — we could not read
    it. A zone with nothing open still prints an empty ``ports:``, so an empty
    set is a real answer and None is not. Per plan 22's decision 4, "could not
    tell" must never render as "closed"; rendering it as closed is what made
    doctor FAIL on a correctly configured firewalld box. See plans/27 (D5).

    ``rich rules:`` count too — a port opened by one never appears in ``ports:``
    either, which was the other half of that same FAIL. See plans/29 (D10) and
    `_parse_firewalld_rich` for what ``restricted_ok`` decides.
    """
    chunks = _FIREWALLD_PORTS_RE.findall(out)
    if not chunks:
        return None
    open_udp: set = set()
    open_tcp: set = set()
    for chunk in chunks:
        for low, high, proto in _FIREWALLD_PORT_RE.findall(chunk):
            target = open_udp if proto.lower() == "udp" else open_tcp
            target.update(_expand(low, high or None))
    rich_udp, rich_tcp = _parse_firewalld_rich(out, restricted_ok=restricted_ok)
    return open_udp | rich_udp, open_tcp | rich_tcp


def _firewalld_zone_name(out: str) -> str:
    """The zone `firewall-cmd --list-all` just described, from its header line."""
    for line in out.splitlines():
        if line.strip() and not line[:1].isspace():
            match = _FIREWALLD_ZONE_HEAD_RE.match(line.strip())
            return match.group(1) if match else ""
    return ""


def _split_firewalld_zones(out: str) -> list:
    """[(zone, block)] from `firewall-cmd --list-all-zones` — one entry per
    zone, each block in the same shape `_parse_firewalld_ports` reads."""
    zones: list = []
    for line in out.splitlines():
        if line.strip() and not line[:1].isspace():
            match = _FIREWALLD_ZONE_HEAD_RE.match(line.strip())
            if match:
                zones.append((match.group(1), []))
                continue
        if zones:
            zones[-1][1].append(line)
    return [(name, "\n".join(body)) for name, body in zones]


def _firewalld_other_zone_with(ports: set, *, default_zone: str) -> Optional[tuple]:
    """The first zone other than `default_zone` that has any of `ports` open.

    A port open only in a non-default zone is **unknown**, not closed: a zone
    bound to a different interface may or may not be the one the game traffic
    arrives on, and this module has no way to tell. Naming the zone lets the
    caller say so instead of reporting a FAIL on a working box. See plans/29
    (D10, decision 9) — and note this deliberately does *not* union those ports
    into ``open_udp``, which would assert reachability we cannot establish.
    """
    if not default_zone:
        return None  # could not tell which zone is the default — do not guess
    rc, out, _err = _read_with_sudo(["firewall-cmd", "--list-all-zones"])
    if rc != 0:
        return None
    for name, block in _split_firewalld_zones(out):
        if name == default_zone:
            continue
        parsed = _parse_firewalld_ports(block, restricted_ok=True)
        found = parsed[0] & ports if parsed else set()
        if found:
            return name, found
    return None


def _firewalld_service_tcp(out: str) -> set:
    """TCP ports implied by the ``services:`` line — ssh, and only ssh.

    A port opened as a service never appears in ``ports:``, so without this the
    lockout gate would call a perfectly good ``--add-service=ssh`` box
    unprotected and refuse. See plans/27 (D5).
    """
    ports: set = set()
    for chunk in _FIREWALLD_SERVICES_RE.findall(out):
        for name in chunk.split():
            port = _FIREWALLD_SERVICE_TCP.get(name.lower())
            if port is not None:
                ports.add(port)
    return ports


def _firewalld_zone_services(out: str) -> list[str]:
    """Every name on the zone's ``services:`` line, in the order listed.

    Companion to `_firewalld_service_tcp`, deliberately not a rewrite of it.
    That one answers the *gate's* question — which TCP ports are covered — so a
    service it cannot map to a port contributes nothing, which is correct there
    and would be a lie here. This answers the *operator's* question, what is
    still open, so an unknown service survives as its own name.
    """
    names: list[str] = []
    for chunk in _FIREWALLD_SERVICES_RE.findall(out):
        for name in chunk.split():
            if name not in names:
                names.append(name)
    return names


def _zone_services_note(detail: str) -> list[str]:
    """What `--set-target=DROP` did *not* close, as log lines. Empty if nothing.

    The zone target is only the **fallthrough**: every service already in the
    zone survives it, so `--set-target=DROP` and `ufw default deny incoming`
    are not the same promise and the success report used to read as though they
    were. Rocky's default `public` ships `cockpit dhcpv6-client ssh`; measured
    on that box after a successful apply, 8433 **timed out** from outside while
    9090 answered **`Connection refused`** — cockpit's port is genuinely open,
    harmless there only because nothing was listening on it. Recorded as F28.

    zsm says this and does not act on it. Removing services a distribution put
    in its own default zone is the same class of action as writing `RConIP`
    into someone's BattlEye config — considered and rejected for the same
    reason: cockpit is RHEL's admin UI and the operator may be using it.
    """
    names = _firewalld_zone_services(detail)
    if not names:
        return []
    zone = _firewalld_zone_name(detail) or "public"
    lines = [
        "NOTE: `--set-target=DROP` sets the zone's fallthrough only — services already "
        f"in zone {zone} are still reachable, and Zedstead does not remove what your "
        "distribution put there. Still allowed:"
    ]
    for name in names:
        port = _FIREWALLD_SERVICE_TCP.get(name.lower())
        lines.append(f"  - {name}" + (f"  ({port}/tcp)" if port else ""))
    lines.append(
        "  Close one you do not use:  sudo firewall-cmd --permanent "
        f"--remove-service=<name> --zone={zone} && sudo firewall-cmd --reload"
    )
    return lines


def _parse_firewalld_state(out: str, err: str) -> Optional[bool]:
    """`firewall-cmd --state`, read as text — its exit code is not enough.

    The command exits non-zero for two unrelated reasons: firewalld is stopped
    (252, printing `not running`), and polkit refused an unprivileged caller
    (`Authorization failed.`). Branching on the code alone mapped both to
    ``False``, so on a RHEL box with a cold sudo timestamp a *running* firewalld
    was reported `inactive` — while the same failed read rendered the ports as
    `unknown` on the same screen. Measured on Rocky 10.2 (F26 of the
    2026 Q3 VPS test).

    firewall-cmd prints exactly `running` or `not running` and nothing else —
    on stderr when it exits non-zero — so an exact match against either stream
    is the whole message. **Do not relax this to a substring test**: the polkit
    refusal itself ends "…agent is running or run the application as
    superuser.", which would then read as a running firewall.

    Anything unrecognised is ``None``, this module's "could not tell". A
    genuinely stopped firewalld still answers ``False``, or doctor loses the
    ability to say a firewall is off.
    """
    for stream in (out, err):
        token = stream.strip().lower()
        if token == "running":
            return True
        if token == "not running":
            return False
    return None


def firewall_status(config=None) -> FirewallStatus:
    """Read-only view of the managed firewall. Never writes, never raises.

    A command that fails or is refused means ``active is None`` ("unknown"),
    which callers must treat as "could not tell" — never as "closed".
    """
    backend = detect_backend()
    if backend is None:
        return FirewallStatus(backend=None, active=None, error="no managed firewall found")

    if backend == "ufw":
        # `ufw status` needs root; `_read_with_sudo` retries through `sudo -n`.
        rc, out, err = _read_with_sudo(["ufw", "status", "verbose"])
        if rc != 0:
            return FirewallStatus(
                backend=backend, active=None, raw=(out or err).strip(),
                error=(err or out).strip() or f"ufw status exited {rc}",
            )
        active, open_udp, open_tcp = _parse_ufw_status(out)
        return FirewallStatus(
            backend=backend, active=active, open_udp=open_udp, open_tcp=open_tcp,
            raw=out.strip(),
        )

    _rc_state, out_state, err_state = _read_with_sudo(["firewall-cmd", "--state"])
    active = _parse_firewalld_state(out_state, err_state)
    rc, out, err = _read_with_sudo(["firewall-cmd", "--list-all"])
    if rc != 0:
        return FirewallStatus(
            backend=backend, active=active, raw=(out or err).strip(),
            error=(err or out).strip() or f"firewall-cmd --list-all exited {rc}",
        )
    parsed = _parse_firewalld_ports(out, restricted_ok=True)
    if parsed is None:
        return FirewallStatus(
            backend=backend, active=None, raw=out.strip(),
            error="could not parse `firewall-cmd --list-all` output (no ports: line)",
        )
    open_udp, open_tcp = parsed
    error = None
    zone_conflict = None
    if config is not None and active is not False:
        gap = {spec.port for spec in required_ports(config)} - open_udp
        elsewhere = (
            _firewalld_other_zone_with(gap, default_zone=_firewalld_zone_name(out))
            if gap else None
        )
        if elsewhere:
            zone, found = elsewhere
            # Assigned where the zone message is built, not as `zone_conflict=error`
            # at the return: a second "read fine but uncertain" case added to this
            # branch later would otherwise be silently worded as a zone conflict by
            # doctor. `error` is set from it so every decision path stays
            # conservative — see the FirewallStatus field comment.
            zone_conflict = (
                f"port(s) {_listed(found)} are open in zone {zone}, not the default zone "
                "— cannot tell whether that zone covers the server's interface"
            )
            error = zone_conflict
    return FirewallStatus(
        backend=backend, active=active, open_udp=open_udp,
        open_tcp=open_tcp | _firewalld_service_tcp(out), raw=out.strip(), error=error,
        zone_conflict=zone_conflict,
    )


def missing_ports(config, status: Optional[FirewallStatus] = None) -> list[PortSpec]:
    """Required ports the backend does not report as allowed.

    Empty when the status is unknown — "I couldn't read it" is not "it's shut".
    """
    status = status or firewall_status(config)
    if status.active is not True or status.error:
        return []
    return [spec for spec in required_ports(config) if spec.port not in status.open_udp]


def already_applied(config, status: Optional[FirewallStatus] = None) -> bool:
    """True only when the live firewall is what ``--apply`` would leave: on,
    deny-by-default, and every SSH port and required UDP port allowed.

    Read-only, and one-sided on purpose: anything unread or uncertain answers
    False, so a caller falls back to applying (additive, and behind the gate)
    rather than to skipping. Before this, `bootstrap-vps --firewall` re-applied
    and re-asked on a box it had just configured (plans/40 F5).
    """
    status = status or firewall_status(config)
    if status.active is not True or status.error or missing_ports(config, status):
        return False
    deny = _UFW_DENY_INCOMING_RE if status.backend == "ufw" else _FIREWALLD_DROP_TARGET_RE
    if not deny.search(status.raw):
        return False
    ssh = ssh_detect().ports
    return bool(ssh) and set(ssh) <= status.open_tcp


def staged_rules(config, *, backend: Backend) -> StagedRules:
    """Read back the rules the backend has recorded — see `StagedRules`.

    ufw: ``ufw show added``, which lists the table whether or not ufw is
    enabled. An old ufw without that subcommand falls back to the live status.
    firewalld: ``--permanent --list-all``, because the allow rules were added
    with ``--permanent`` and are not in the runtime zone until ``--reload``.

    Both retry through ``sudo -n`` only (never bare ``sudo``, which would hang
    a tty-less caller), and both answer ``readable=False`` rather than an empty
    set when they could not read at all.

    firewalld rich rules are read **strictly** here: a source-restricted rule
    leaves the box reachable from one address, so counting it would let the gate
    flip the policy on everyone else. See plans/29 (decision 9).
    """
    _read = _read_with_sudo

    if backend == "ufw":
        rc, out, err = _read(["ufw", "show", "added"])
        if rc == 0:
            open_udp, open_tcp = _parse_ufw_added(out)
            return StagedRules(tcp=open_tcp, udp=open_udp, readable=True, detail=out.strip())
        status = firewall_status(config)
        if status.error or status.active is None:
            return StagedRules(readable=False, detail=(status.error or err or out).strip())
        return StagedRules(
            tcp=status.open_tcp, udp=status.open_udp, readable=True, detail=status.raw
        )

    zone = _default_zone()
    rc, out, err = _read(["firewall-cmd", "--permanent", "--list-all", f"--zone={zone}"])
    if rc != 0:
        return StagedRules(
            readable=False,
            detail=(err or out).strip() or f"firewall-cmd --permanent --list-all exited {rc}",
        )
    parsed = _parse_firewalld_ports(out, restricted_ok=False)
    if parsed is None:
        return StagedRules(
            readable=False,
            detail="no ports: line in `firewall-cmd --permanent --list-all` output",
        )
    open_udp, open_tcp = parsed
    return StagedRules(
        tcp=open_tcp | _firewalld_service_tcp(out), udp=open_udp,
        readable=True, detail=out.strip(),
    )


# ---------------------------------------------------------------------------
# SSH detection — the lockout guard
# ---------------------------------------------------------------------------

def _sshd_running() -> bool:
    """True when sshd appears to be running — **and also when we could not tell**.

    The only caller uses False to justify assuming port 22 before flipping the
    default policy to deny, so "I could not look" must never answer "it is not
    running". An unnecessary refusal costs a support question; a wrong
    allowlist costs the box. See plans/27 (D4).

    Under socket activation there is no process to find at all until the first
    connection, so ``pgrep`` exiting 1 is a real answer *about processes* and
    no longer gets to be the last word: systemd holding an SSH socket counts as
    running, and a systemd we could not question fails safe. See plans/29 (D7).
    """
    rc, _out, _err = _run(["pgrep", "-x", "sshd"], timeout=5)
    if rc == 0:
        return True
    socket_ports, could_ask = _socket_unit_ports()
    if socket_ports:
        return True  # systemd is holding an SSH socket: sshd is "running"
    if not could_ask:
        return True  # could not ask systemd — fail safe (see the docstring)
    if rc != 127:
        return False  # pgrep ran and matched nothing — a real answer
    # No pgrep (minimal container images) — read /proc directly.
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        # Could not read /proc, and pgrep is not installed either: we have not
        # established that sshd is absent. Fail safe (see the docstring).
        return True
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            if (entry / "comm").read_text(encoding="utf-8", errors="ignore").strip() == "sshd":
                return True
        except OSError:
            continue
    return False


@dataclass(frozen=True)
class _SsScan:
    """What one ``ss -tlnp`` pass could and could not establish.

    ``ss -p`` names processes only for *your own* PIDs unless you are root, so a
    listener whose owner column is empty is one we were not allowed to see —
    not one that does not exist. Returning a bare list conflated those, which is
    how detection could report "nothing here" on a box with sshd listening.
    See plans/29 (D8).
    """

    ssh: set = field(default_factory=set)  # lines naming sshd
    hidden: set = field(default_factory=set)  # LISTEN lines with no users:( field
    unattributed: set = field(default_factory=set)  # owned by systemd, unidentifiable
    ran: bool = False  # False = ss itself failed


_SS_OWNER_RE = re.compile(r'\("([^"]+)"')


def _ss_line_port(line: str) -> Optional[int]:
    """The local port of one `ss` listener line, or None."""
    for field_ in line.split():
        match = re.search(r":(\d+)$", field_)
        if match:
            port = int(match.group(1))
            return port if 1 <= port <= 65535 else None
    return None


def _ss_scan(argv: list[str]) -> _SsScan:
    """Classify every LISTEN line of one `ss -tlnp` run — see `_SsScan`."""
    rc, out, _err = _run(argv)
    if rc != 0:
        return _SsScan()
    ssh: set = set()
    hidden: set = set()
    unattributed: set = set()
    for line in out.splitlines():
        stripped = line.strip()
        if not stripped.lower().startswith("listen"):
            continue
        port = _ss_line_port(stripped)
        if port is None:
            continue
        if "sshd" in stripped:
            ssh.add(port)
        elif "users:(" not in stripped:
            hidden.add(port)
        else:
            owners = {name.lower() for name in _SS_OWNER_RE.findall(stripped)}
            if owners and owners <= {"systemd"}:
                unattributed.add(port)
    return _SsScan(ssh=ssh, hidden=hidden, unattributed=unattributed, ran=True)


def _merge_ss(first: _SsScan, second: _SsScan) -> _SsScan:
    """Fold the ``sudo -n`` retry into the unprivileged scan.

    ``ssh`` **unions**: the allowlist may only grow, and a redetect that shrinks
    it is ignored everywhere else in this module for the same reason (plans/29,
    decision 6).

    ``hidden`` and ``unattributed`` **replace**, and that is the fix rather than
    a shortcut. ``ss`` lists every LISTEN line whatever your privileges — only
    the owner column is redacted — so a privileged pass that *ran* is the
    complete and final answer to "whose listener could I not see". Unioning the
    unprivileged pass's version into it re-reported incompleteness the retry had
    already resolved, and printed a note contradicting the line above it:

        SSH: port(s) 2222 (detected via ss)
          incomplete: `ss -tlnp` would not name the owner of port(s) 53, 80, 2222

    Measured on Ubuntu 26.04 and again on Rocky 10.2 (F14). It could only ever
    over-report, which is the safe direction — but a note that contradicts the
    detection above it teaches operators to ignore the notes, and those notes
    are what `--apply`'s refusals are built on.

    A retry that did not run (``ran=False``) is not evidence of anything and
    changes nothing.
    """
    if not second.ran:
        return first
    return _SsScan(
        ssh=first.ssh | second.ssh,
        hidden=second.hidden,
        unattributed=second.unattributed,
        ran=True,
    )


def _ports_from_ss() -> _SsScan:
    """Ports a listening sshd is bound to — authoritative, it is what is
    actually bound.

    ``ss -p`` only names processes for root, so an unprivileged run matches
    nothing and detection would degrade to the config file on the most common
    setup there is. Retry once through ``sudo -n`` — never bare ``sudo``, which
    would hang a tty-less caller on the password prompt (same rule as
    ``systemd_scope.query``). See plans/27 (D3).
    """
    scan = _ss_scan(["ss", "-tlnp"])
    if scan.ssh or not sudo_prefix():
        return scan
    # `sudo -n` cannot prompt and cannot hang — that is what -n means, and it is
    # why plan 27 decision 3 mandates it. Probing `sudo -n true` first (via
    # sudo_available_noninteractive) to decide whether to run `sudo -n ss`
    # therefore bought no safety at all; it only made the answer depend on how
    # recently the operator typed a password. See plans/29 (D8).
    return _merge_ss(scan, _ss_scan(["sudo", "-n", "ss", "-tlnp"]))


def _ports_from_sshd_t() -> list[int]:
    rc, out, _err = _run(["sshd", "-T"])
    if rc != 0:
        return []
    ports = [int(m) for m in re.findall(r"^port\s+(\d+)\s*$", out, re.MULTILINE | re.IGNORECASE)]
    return sorted(set(ports))


# Debian/Ubuntu name it ssh.socket; RHEL/Fedora sshd.socket.
SSH_SOCKET_UNITS = ("ssh.socket", "sshd.socket")
# The *runtime* property is `Listen`, one line per socket, formatted
# `Listen=<addr> (Stream)`. `-p ListenStream` prints nothing for a loaded unit,
# so a parser written against it reads empty forever and never says why.
_SYSTEMD_LISTEN_RE = re.compile(r"^Listen=(.+?)\s+\((\w+)\)\s*$", re.MULTILINE)
_SYSTEMD_LOADSTATE_RE = re.compile(r"^LoadState=(\S+)\s*$", re.MULTILINE)
_SYSTEMD_ACTIVESTATE_RE = re.compile(r"^ActiveState=(\S+)\s*$", re.MULTILINE)


def _listen_tcp_port(addr: str) -> Optional[int]:
    """The TCP port of one systemd ``Listen=`` address, or None.

    Deliberately narrow: a Unix path (``/run/foo.sock``) or any shape this does
    not recognise yields *nothing* rather than a guess. Being wrong about the
    address format then degrades to "this source found nothing", which the union
    tolerates — not to a wrong port, which is the lockout.
    """
    addr = addr.strip()
    if not addr or addr.startswith("/"):
        return None
    match = re.search(r":(\d+)$", addr)
    if not match:
        return None
    port = int(match.group(1))
    return port if 1 <= port <= 65535 else None


def _socket_unit_ports() -> tuple[set, bool]:
    """(ports, could_ask) — TCP ports systemd itself is listening on for sshd.

    Under ``ssh.socket`` activation (Ubuntu 22.10+, the default on 24.04 cloud
    images) **systemd holds the listening socket** and no process named sshd
    exists until the first connection. Both of this module's other live
    detectors look for a process called ``sshd``, so both go blind — and worse,
    ``sshd -T`` still reports ``sshd_config``'s ``Port``, which the daemon
    ignores entirely in that mode. Ask systemd instead. See plans/29 (D7).

    ``could_ask`` is False only when systemctl *existed and errored*. A missing
    systemctl (rc 127) and a ``LoadState=not-found`` unit are real negatives:
    socket activation requires systemd by definition, so "there is no systemd
    here" answers the question rather than failing to. Without that rule every
    non-systemd container would land in ``apply_rules``' new refusal.

    **``ActiveState`` is load-bearing — do not drop it.** RHEL-family images
    ship ``sshd.socket`` installed but *disabled*, so it is ``LoadState=loaded``
    and systemd still reports the ``Listen=`` it *would* bind if it were ever
    started. Measured on Rocky 10.2 (2026 Q3) with sshd really on 2222 via
    ``sshd_config``: this returned ``{22}`` and named itself as the source,
    while ``ss`` was unprivileged, ``sshd -T`` needed root and ``sshd_config``
    is 0600 there — so a dead unit was the *only* source that answered, and
    ``--apply`` would have allowed a port nothing listens on and denied the real
    one. Plan 27's gate cannot catch that: 22/tcp really would be in the table.

    An inactive socket unit holds no listener, so its ``Listen=`` is a
    configuration artifact, not a fact about what is bound — which is the
    distinction this whole module rests on. Anything other than ``active``
    (including a missing ``ActiveState``) therefore contributes *nothing*,
    rather than escalating to ``could_ask=False``: an unexpected output shape
    degrades to "this source found nothing", which the union tolerates, never to
    a wrong port, which is the lockout. Same rule as ``_listen_tcp_port``.
    """
    ports: set = set()
    could_ask = True
    for unit in SSH_SOCKET_UNITS:
        rc, out, _err = _run(
            ["systemctl", "show", unit, "-p", "Listen", "-p", "LoadState",
             "-p", "ActiveState"],
            timeout=5,
        )
        if rc == 127:
            return set(), True  # no systemd on this box — a real negative
        state = _SYSTEMD_LOADSTATE_RE.search(out)
        if rc != 0 or state is None:
            could_ask = False
            continue
        if state.group(1) == "not-found":
            continue  # that unit is not installed — also a real negative
        active = _SYSTEMD_ACTIVESTATE_RE.search(out)
        if active is None or active.group(1) != "active":
            continue  # installed but not listening — a real negative too
        for addr, kind in _SYSTEMD_LISTEN_RE.findall(out):
            if kind != "Stream":  # a Datagram socket is not SSH
                continue
            port = _listen_tcp_port(addr)
            if port is not None:
                ports.add(port)
    return ports, could_ask


_INCLUDE_RE = re.compile(r"^\s*Include\s+(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
_PORT_RE = re.compile(r"^\s*Port\s+(\d+)\s*$", re.MULTILINE | re.IGNORECASE)


@dataclass(frozen=True)
class _ConfigScan:
    """Ports found in sshd's config tree, and the files we could not read.

    An unreadable drop-in used to vanish: the union came back silently short,
    and ``/etc/ssh/sshd_config.d/50-cloud-init.conf`` is mode 0600 on Ubuntu
    cloud images — exactly where an operator is told to put a port override.
    Main file says ``Port 22``, unreadable drop-in says ``Port 2222``, and
    detection reports a confident-looking 22. See plans/29 (D9).
    """

    ports: set = field(default_factory=set)
    skipped: tuple = ()  # paths we could not read, verbatim


def _glob_include(pattern: str) -> tuple[list[Path], Optional[str]]:
    """(files, why-we-could-not-look) for one `Include` pattern, sorted so the
    order is stable.

    A relative pattern resolves against ``SSHD_CONFIG_DIR`` (sshd's own
    behaviour), not the process CWD. Everything goes through `Path.glob`, which
    handles a literal path as a pattern with no magic in it.
    """
    path = Path(pattern)
    if path.is_absolute():
        base, rel = Path(path.anchor), path.relative_to(path.anchor)
    else:
        base, rel = SSHD_CONFIG_DIR, path
    try:
        return sorted(match for match in base.glob(str(rel)) if match.is_file()), None
    except (OSError, ValueError, NotImplementedError, IndexError) as exc:
        return [], f"{pattern}: {exc}"


def _read_sshd_config(path: Path) -> tuple[Optional[str], Optional[str]]:
    """(text, why-we-could-not-read-it).

    A **missing** file is a real negative — ``(None, None)``. Anything else,
    permissions above all, is retried once through ``sudo -n cat`` (never bare
    ``sudo``: a tty-less caller would hang on the password prompt, same rule as
    every other read path here) and only then reported as skipped.
    """
    try:
        return path.read_text(encoding="utf-8", errors="ignore"), None
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        reason = exc.strerror or str(exc)
    if sudo_prefix():
        rc, out, _err = _run(["sudo", "-n", "cat", str(path)])
        if rc == 0:
            return out, None
    return None, f"{path}: {reason}"


def _walk_sshd_config(
    path: Path, ports: set, skipped: list, depth: int, seen: set
) -> None:
    """Collect `Port` directives from `path` and everything it Includes.

    Ubuntu 22.04+/Debian 12 ship `Include /etc/ssh/sshd_config.d/*.conf` and
    cloud-init writes into it, so a reader that ignores Include can report the
    main file's `Port 22` while the daemon is really on the drop-in's port —
    the lockout this module exists to prevent. See plans/27 (D1).

    Unreadable files are recorded, never raised; a self-including config
    terminates on the seen-set and, failing that, on ``_MAX_INCLUDE_DEPTH``.
    """
    if depth > _MAX_INCLUDE_DEPTH:
        return
    try:
        key = path.resolve()
    except OSError:
        key = path
    if key in seen:
        return
    seen.add(key)
    text, why = _read_sshd_config(path)
    if text is None:
        if why is not None:
            skipped.append(why)
        return
    ports.update(int(m) for m in _PORT_RE.findall(text))
    for directive in _INCLUDE_RE.findall(text):
        for pattern in directive.split():
            matches, why = _glob_include(pattern)
            if why is not None:
                skipped.append(why)
            for included in matches:
                _walk_sshd_config(included, ports, skipped, depth + 1, seen)


def _scan_sshd_config(path: Optional[Path] = None) -> _ConfigScan:
    """Every `Port` directive in the main config and everything it Includes,
    plus whatever could not be read — see `_ConfigScan`.

    The ports are the **union**, deliberately. Allowing a port sshd is not
    listening on is harmless; missing one it *is* listening on is the lockout —
    so modelling sshd's first-value-wins and `Match`-block semantics would be
    taking on real risk to save an unused allow rule. See plans/27 (step 1).
    """
    ports: set = set()
    skipped: list = []
    _walk_sshd_config(path or SSHD_CONFIG_PATH, ports, skipped, 0, set())
    return _ConfigScan(ports=ports, skipped=tuple(skipped))


def _ports_from_sshd_config(path: Optional[Path] = None) -> list[int]:
    """Sorted ports only — `_scan_sshd_config` is the full answer."""
    return sorted(_scan_sshd_config(path).ports)


# Shown next to a non-confident source so "detected via sshd_config" does not
# read like the same kind of fact as "detected via ss".
WEAK_SOURCE_NOTE = {
    "sshd_config": "a config file, not the running daemon",
    "default": "sshd is not running, so 22 was assumed",
    "unknown": "not detected",
}


def ssh_source_is_confident(source: SshSource) -> bool:
    """Whether `source` observed the running daemon rather than a config file.

    A config file is intent: it cannot see a daemon started before the last
    edit. Callers say so rather than presenting every source as equal — see
    `CONFIDENT_SOURCES`.
    """
    return source in CONFIDENT_SOURCES


@dataclass(frozen=True)
class SshDetection:
    """The whole answer to "which ports is SSH on, and how sure are we".

    Detection used to consult four sources in a fixed order and return the
    **first** one that answered. Which source answered first depended on things
    that change minute to minute on the same box — whether anyone had connected
    over SSH yet (socket activation), whether the sudo timestamp was still warm,
    whether the process could read a 0600 drop-in — so the *answer* changed
    minute to minute too, and one of the answers it could give was 22 on a box
    whose SSH is not on 22.

    So this is a **union**: every source runs, every port they find is allowed.
    Over-allowing is harmless (plan 27 step 1 relies on the same rule across
    `Include`d files); missing the port sshd is really on is the lockout. The
    union is also what makes the answer stable — it can only grow as privilege
    grows. See plans/29 (D7, D8, decision 2).
    """

    ports: list = field(default_factory=list)
    sources: tuple = ()  # every source that contributed, strongest first
    incomplete: tuple = ()  # why a source could not answer, in words
    sshd_present: bool = False

    @property
    def source(self) -> SshSource:
        """The strongest contributor — the single label `ssh_ports()` reports."""
        for name in _SOURCE_PRECEDENCE:
            if name in self.sources:
                return name  # type: ignore[return-value]
        return "unknown" if self.sshd_present else "default"

    @property
    def confident(self) -> bool:
        """True when something observed the running system, not just a config."""
        return any(source in CONFIDENT_SOURCES for source in self.sources)


def ssh_detect() -> SshDetection:
    """Run every SSH-port source and union what they found — see `SshDetection`.

    Falling back to 22 is only safe when sshd is *not* running. If it is up on
    an unknown port, guessing 22 and flipping the default policy to deny is
    exactly the lockout this module exists to prevent.
    """
    ss = _ports_from_ss()
    socket_ports, could_ask_systemd = _socket_unit_ports()
    sshd_t = _ports_from_sshd_t()
    config_scan = _scan_sshd_config()

    ports: set = set()
    sources: list = []
    for found, name in (
        (ss.ssh, "ss"),
        (socket_ports, "ssh.socket"),
        (set(sshd_t), "sshd -T"),
        (config_scan.ports, "sshd_config"),
    ):
        if found:
            ports |= set(found)
            sources.append(name)

    incomplete: list = []
    if not ss.ran:
        incomplete.append("`ss -tlnp` could not be run, so the listening sockets were not read")
    elif ss.hidden:
        incomplete.append(
            f"`ss -tlnp` would not name the owner of port(s) {_listed(ss.hidden)} "
            "(that needs root), so one of them could be sshd"
        )
    if ss.unattributed:
        incomplete.append(
            f"systemd itself owns the listener on port(s) {_listed(ss.unattributed)} "
            "and no ssh socket unit claims them"
        )
    if not could_ask_systemd:
        incomplete.append(
            "systemd could not be asked about " + " / ".join(SSH_SOCKET_UNITS)
        )
    incomplete.extend(f"could not read {path}" for path in config_scan.skipped)

    present = _sshd_running()
    if ports:
        return SshDetection(
            ports=sorted(ports), sources=tuple(sources),
            incomplete=tuple(incomplete), sshd_present=present,
        )
    if present:
        return SshDetection(incomplete=tuple(incomplete), sshd_present=True)
    # sshd is positively *not* running — `_sshd_running` fails safe, so False is
    # something we established rather than failed to establish. The port list is
    # then not derived from any source, so nothing about the sources is missing:
    # `incomplete` stays empty on purpose, or every unprivileged desktop (where
    # `ss` hides root-owned listeners it has no business naming) would land in
    # `apply_rules`' new refusal. See plans/29 (decision 4, row 3).
    return SshDetection(ports=[22])


def ssh_ports() -> tuple[list[int], SshSource]:
    """(ports, how) — how is one of 'ss', 'ssh.socket', 'sshd -T',
    'sshd_config', 'default', 'unknown'. Empty list + 'unknown' means detection
    failed while sshd is running: callers must refuse to apply.

    Unchanged contract — `ssh_detect()` is the full answer. Kept because
    `app/commands/service.py` and the test suite call it; plans/27 step 3 made
    the same choice for `ssh_source_is_confident` rather than widening this
    tuple.
    """
    detection = ssh_detect()
    return detection.ports, detection.source


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _default_zone() -> str:
    rc, out, _err = _run(["firewall-cmd", "--get-default-zone"])
    zone = out.strip()
    return zone if rc == 0 and zone else "public"


def build_apply_commands(config, *, backend: Backend, ssh: Iterable[int]) -> list[list[str]]:
    """The exact argv list ``--apply`` would run, in execution order.

    SSH allow rules come first and the default policy last — reversing that
    order disconnects the operator between the two commands.
    """
    ssh = list(ssh)
    if backend == "ufw":
        cmds = [["ufw", "allow", f"{port}/tcp"] for port in ssh]
        cmds += [["ufw", "allow", spec.rule] for spec in required_ports(config)]
        cmds.append(["ufw", "default", "deny", "incoming"])
        cmds.append(["ufw", "default", "allow", "outgoing"])
        cmds.append(["ufw", "--force", "enable"])
        return cmds

    zone = _default_zone()
    cmds = [["firewall-cmd", "--permanent", f"--add-port={port}/tcp"] for port in ssh]
    cmds += [["firewall-cmd", "--permanent", f"--add-port={spec.rule}"] for spec in required_ports(config)]
    cmds.append(["firewall-cmd", "--permanent", "--set-target=DROP", f"--zone={zone}"])
    cmds.append(["firewall-cmd", "--reload"])
    return cmds


def build_revert_commands(config, *, backend: Backend) -> list[list[str]]:  # noqa: ARG001
    """The argv that undoes ``--apply``'s policy flip — what plans/34's revert
    timer runs if nobody confirms.

    Not ``UNDO_HINT``: that is a display string with a literal ``<zone>`` and a
    shell ``&&``, and must never reach a subprocess. The two say the same thing
    and a test pins them together. The firewalld zone is resolved now, at arm
    time, because the timer fires with nobody there to resolve it.

    Deliberately zsm undoing zsm: ``ufw disable`` / the zone target back to
    ``default``. Never a provider snapshot — zsm holds no provider credentials.
    """
    if backend == "ufw":
        return [["ufw", "disable"]]
    zone = _default_zone()
    return [
        ["firewall-cmd", "--permanent", "--set-target=default", f"--zone={zone}"],
        ["firewall-cmd", "--reload"],
    ]


def split_at_policy(
    cmds: Iterable[list[str]], *, backend: Backend
) -> tuple[list[list[str]], list[list[str]]]:
    """(allow_cmds, policy_cmds) — everything before the first default-policy
    command, and the rest.

    The verification gate goes between them, which is only safe because allow
    rules are **additive**: a run that stops at this boundary has opened some
    ports and locked nothing. See plans/27 (D2).
    """
    listed = [list(cmd) for cmd in cmds]
    for idx, cmd in enumerate(listed):
        boundary = (
            cmd[:2] == ["ufw", "default"]
            if backend == "ufw"
            else any(part.startswith("--set-target=") for part in cmd)
        )
        if boundary:
            return listed[:idx], listed[idx:]
    return listed, []


def format_commands(cmds: Iterable[list[str]], *, privileged: bool = True) -> list[str]:
    """Human-readable command lines, with the sudo prefix that will be used."""
    prefix = sudo_prefix() if privileged else []
    return [" ".join([*prefix, *cmd]) for cmd in cmds]


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _ensure_privileges(log: Callable[[str], None], *, interactive: bool) -> bool:
    """Cache sudo credentials up front, or explain why we can't.

    Non-interactive callers get ``sudo -n`` only — a password prompt with no
    tty hangs forever (same reasoning as ``systemd_scope.query``).
    """
    from app.shared.platform import sudo_available_noninteractive

    if not sudo_prefix():
        return True  # root, or no sudo installed — run the commands directly
    if sudo_available_noninteractive():
        return True
    if not interactive:
        log("sudo needs a password and there is no terminal to ask on. "
            "Run this from a shell, or configure NOPASSWD for the firewall commands.")
        return False
    rc, _out, _err = _run(["sudo", "-v"], timeout=120)
    if rc != 0:
        log("sudo authentication failed — nothing was changed.")
        return False
    return True


# What to type when the SSH port could not be determined and `--apply` refuses.
# Keyed by backend like `UNDO_HINT` and `_NOT_LOCKED_DOWN` below — this was the
# one that stayed a hardcoded `sudo ufw allow`, and a RHEL box from a cold sudo
# timestamp takes this branch every time (every SSH source there needs root, so
# the union is empty). It is also, since F29 stopped the dry-run printing a
# command list for an unknown port, the only thing on screen telling the
# operator what to run. Recorded as F25.
_ALLOW_BY_HAND = {
    "ufw": "sudo ufw allow <port>/tcp",
    "firewalld": "sudo firewall-cmd --permanent --add-port=<port>/tcp && "
                 "sudo firewall-cmd --reload",
}

# Printed by every gate refusal. The reassurance is the point: an operator who
# reads "refused" as "half-applied" will go and 'fix' a firewall that is fine.
_NOT_LOCKED_DOWN = {
    "ufw": "Nothing has been locked down — the allow rules that ran are additive and "
           "cannot disconnect you. Check `sudo ufw show added` and re-run.",
    "firewalld": "Nothing has been locked down — the allow rules that ran are additive "
                 "and cannot disconnect you. Check "
                 "`sudo firewall-cmd --permanent --list-all` and re-run.",
}


def _run_commands(
    cmds: list[list[str]], *, prefix: list[str], log: Callable[[str], None], backend: Backend
) -> int:
    """Run each command in order, stopping at the first non-zero exit."""
    for cmd in cmds:
        argv = [*prefix, *cmd]
        log(f"$ {' '.join(argv)}")
        rc, out, err = _run(argv, timeout=60)
        detail = (out or err).strip()
        if detail:
            for line in detail.splitlines():
                log(f"  {line}")
        if rc != 0:
            log(f"FAILED at: {' '.join(argv)} (exit {rc})")
            log("The firewall may be half-configured, which is the dangerous state. "
                f"Undo with: {UNDO_HINT[backend]}")
            return 1
    return 0


def apply_rules(
    config,
    *,
    assume_yes: bool = False,
    log: Callable[[str], None] = print,
    confirm: Optional[Callable[[list[str]], bool]] = None,
    interactive: bool = True,
) -> int:
    """Write a deny-by-default firewall that opens exactly the DayZ UDP ports.

    Returns an exit code; never raises for an expected failure. Refuses — with
    a non-zero code and *nothing written* — on Windows, inside a container, with
    no managed firewall, when the SSH port could not be determined, and when the
    confirmation was declined. ``confirm`` receives the formatted command list;
    omitting it while ``assume_yes`` is False is itself a refusal, so a caller
    that forgets to wire up a prompt cannot apply by accident.

    The allow rules and the default policy are run as two batches with a
    verification gate between them: the SSH rule has to be readable in the
    firewall's own table before anything is denied. Aborting at the gate is
    safe — allow rules are additive, so a run that stopped there has locked
    nothing. See plans/27 (D2).
    """
    if IS_WINDOWS:
        log("Firewall management is Linux-only (ufw/firewalld).")
        return 1
    if IS_DOCKER:
        # "ufw's chains" until 2026 Q3. This branch runs *before*
        # `detect_backend()`, so the backend is genuinely unknown here — but the
        # fact is true of firewalld too (Docker installs its own DOCKER chain),
        # so the honest wording names neither rather than guessing one. Found by
        # auditing plans/31 B's own claim that no `apply_rules` output still
        # says ufw; it was the one reachable counter-example.
        log("Refusing to apply inside a container: Docker writes its own iptables "
            "rules that bypass the managed firewall's chains, so rules set here "
            "would not do what they say. Configure the firewall on the Docker "
            "host instead.")
        return 1

    backend = detect_backend()
    if backend is None:
        # This used to print nine `sudo ufw …` lines under `ssh=[22]`. On the
        # stage 8b box — a stock Rocky VPS, no firewall installed at all —
        # that was wrong three times over: `apt-get` is not the package manager,
        # ufw is not what the distribution ships, and 22 is the guess
        # `ssh_detect()` exists to refuse. Plan 30's package C fixed the same
        # output for `--dry-run` and doctor and left this copy behind (F24).
        # `no_backend_advice()` names *this* host's package manager.
        log(NO_BACKEND_SUMMARY)
        log(no_backend_advice())
        log("Then re-run. No command list is shown: which rules get written depends "
            "on the backend you install and on this box's SSH port, and neither is "
            "known yet.")
        log("Raw iptables/nftables rules are deliberately not written — they are "
            "unmanaged and ordering-sensitive.")
        return 1

    detection = ssh_detect()
    ports, source = detection.ports, detection.source
    if source == "unknown":
        log("Refusing to apply: sshd is running but its port could not be determined.")
        log("Setting a deny-by-default policy without allowing SSH would lock you out.")
        log(f"Allow it by hand first (e.g. `{_ALLOW_BY_HAND[backend]}`), then re-run.")
        return 1
    if detection.incomplete and not detection.confident:
        # A port list that came only from a config file, while part of the check
        # could not be completed, is the D9 lockout: the main file's `Port 22` is
        # read, the unreadable drop-in's `Port 2222` is not, and the answer looks
        # confident. An incomplete detection that a *confident* source answered
        # is only noted, not refused — see plans/29 (decision 4).
        log("Refusing to apply: the SSH port could not be established from the running "
            "system, and part of the check could not be completed:")
        for reason in detection.incomplete:
            log(f"  - {reason}")
        log("Setting a deny-by-default policy on a guess would lock you out. Re-run as "
            "root (or with sudo available), or allow the port by hand first.")
        return 1
    for reason in detection.incomplete:
        log(f"NOTE: {reason}.")

    cmds = build_apply_commands(config, backend=backend, ssh=ports)
    lines = format_commands(cmds)

    clash = rcon_collision(config)
    if clash is not None:
        log(f"WARNING: RCon port {clash.port} is also this server's {clash.label} port, "
            "so allowing it for players publishes the RCon listener too. Measured on a "
            "running server: the DayZ process binds RCon to 0.0.0.0, not to loopback.")
        log(f"  Fix before applying:  set  RConIP 127.0.0.1  in beserver_x64.cfg "
            "(BattlEye tab in the web UI), then restart the server.")
        log(f"  Confirm with:  ss -ulnp | grep {clash.port}   "
            "— it should show 127.0.0.1, not 0.0.0.0.")

    if not assume_yes:
        if confirm is None:
            log("Refusing to apply without a confirmation.")
            return 1
        if not confirm(lines):
            log("Aborted — nothing was changed.")
            return 1

    if not _ensure_privileges(log, interactive=interactive):
        return 1

    # The first detection ran before sudo was warm, which is precisely when `ss`
    # cannot see other users' listeners. Re-run it now and take the union: the
    # allowlist can only grow, growth is strictly safer than the list the
    # operator confirmed, and a shrink is ignored. `_ensure_privileges` stays
    # *below* the confirmation on purpose — moving it up would prompt for root
    # before showing what will run. See plans/29 (D8, decision 6).
    added = sorted(set(ssh_detect().ports) - set(ports))
    if added:
        ports = sorted(set(ports) | set(added))
        log(f"Detected additional SSH port(s) after acquiring privileges: "
            f"{', '.join(str(port) for port in added)} — allowing them too.")
        cmds = build_apply_commands(config, backend=backend, ssh=ports)

    prefix = sudo_prefix()
    allow_cmds, policy_cmds = split_at_policy(cmds, backend=backend)

    if _run_commands(allow_cmds, prefix=prefix, log=log, backend=backend) != 0:
        return 1

    # The gate. Exit codes are not evidence: `ufw allow 22/tcp` exits 0 without
    # proving the rule reached the table, and the next command denies everything
    # else. Read the table back before that, never after. See plans/27 (D2).
    listed = ", ".join(str(p) for p in ports)
    staged = staged_rules(config, backend=backend)
    if not staged.readable:
        log("Refusing to set the deny-by-default policy: the firewall's own rule table "
            "could not be read back, so there is no proof SSH would survive it."
            + (f" ({staged.detail.splitlines()[0]})" if staged.detail else ""))
        log(_NOT_LOCKED_DOWN[backend])
        return 1
    missing_ssh = [port for port in ports if port not in staged.tcp]
    if missing_ssh:
        log("Refusing to set the deny-by-default policy: the SSH allow rule for "
            f"{', '.join(f'{port}/tcp' for port in missing_ssh)} is not in the firewall "
            "table after adding it.")
        log(_NOT_LOCKED_DOWN[backend])
        return 1
    log(f"Verified: SSH port(s) {listed} are in the {backend} rule table — "
        "setting the default policy.")

    if _run_commands(policy_cmds, prefix=prefix, log=log, backend=backend) != 0:
        return 1

    note = "" if ssh_source_is_confident(source) else f" ({WEAK_SOURCE_NOTE.get(source, '')})"
    log(f"Firewall configured ({backend}) — SSH port(s) {listed} detected via {source}{note}.")
    log("The web app port is deliberately not opened — put it behind TLS and a "
        "reverse proxy instead.")
    # F28. `staged.detail` is the permanent zone listing read at the gate, and
    # setting the target does not touch `services:`, so it is still accurate
    # here — and re-reading would cost a subprocess call to say the same thing.
    if backend == "firewalld":
        for line in _zone_services_note(staged.detail):
            log(line)
    return 0
