"""Shared test fixtures for zsm."""

from __future__ import annotations

import http.client
import json
from collections import Counter
import os
import re
import ssl
import textwrap
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

# Plain output whatever the shell asks for. Rich reads these when a Console is
# built, and several are built at import time, so this has to happen here
# rather than in a fixture. With FORCE_COLOR set (a resumed Claude Code shell
# had FORCE_COLOR=3), 13 tests that compare Rich output as text failed on the
# escape codes. CI sets none of these.
for _var in ("FORCE_COLOR", "TTY_COMPATIBLE", "TTY_INTERACTIVE"):
    os.environ.pop(_var, None)


@pytest.fixture()
def timers_unknown(monkeypatch):
    """Task timer units read as unknown, so ``next_restart_occurrence`` counts
    every enabled task, and no test reads the dev box's real unit dir."""
    monkeypatch.setattr("app.server.scheduler.timer_install_state", lambda config, unit_dir=None: ({}, []))


@pytest.fixture()
def tmp_server_dir(tmp_path: Path) -> Path:
    """Minimal server directory structure."""
    for subdir in ("mpmissions", "profiles", "keys"):
        (tmp_path / subdir).mkdir()
    be = tmp_path / "battleye"
    be.mkdir()
    (be / "beserver_x64.cfg").write_text("RConPassword mypass\nRConPort 2305\n")
    # Default serverDZ.cfg with a mission template, where Bohemia's file has it
    (tmp_path / "serverDZ.cfg").write_text(
        textwrap.dedent("""\
            hostname = "Test Server";
            class Missions
            {
                class DayZ
                {
                    template = "dayzOffline.chernarusplus";
                };
            };
        """)
    )
    return tmp_path


@pytest.fixture()
def tmp_mission_dir(tmp_server_dir: Path) -> Path:
    """Mission directory with db/ subfolder inside tmp_server_dir."""
    mission = tmp_server_dir / "mpmissions" / "dayzOffline.chernarusplus"
    mission.mkdir(parents=True, exist_ok=True)
    (mission / "db").mkdir(exist_ok=True)
    return mission


@pytest.fixture()
def sample_config(tmp_server_dir: Path, tmp_path: Path, monkeypatch) -> "DayzConfig":
    """DayzConfig pointing at the temp server directory.

    Patches CONFIG_DIR / CONFIG_FILE so DayzConfig.save() writes to tmp_path.
    """
    from app.shared.config import DayzConfig

    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    config_file = config_dir / "config.json"

    monkeypatch.setattr("app.shared.paths.CONFIG_DIR", config_dir)
    monkeypatch.setattr("app.shared.paths.CONFIG_FILE", config_file)
    monkeypatch.setattr("app.shared.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("app.shared.config.CONFIG_FILE", config_file)

    cfg = DayzConfig(
        server_dir=str(tmp_server_dir),
        steamcmd_dir=str(tmp_path / "steamcmd"),
        steam_username="testuser",
        server_port=2302,
        battleye_path="battleye",
        profiles_path="profiles",
        config_path="serverDZ.cfg",
    )
    return cfg


# The measured shape (Linux 1.29 server, 2026 Q3): while the server runs,
# BattlEye has renamed beserver_x64.cfg to its own active copy, and the
# battleye dir holds no beserver_x64.cfg at all.
ACTIVE_BESERVER_CFG = "beserver_x64_active_770d861c.cfg"
ACTIVE_BESERVER_TEXT = "RConPassword livepass\nRestrictRCon 0\nRConPort 2306\n"


#: The announced official map `unreleased_map` adds to the catalog.
UNRELEASED_MAP = "Testlands"
UNRELEASED_TERRAIN = "testlands"


@pytest.fixture()
def unreleased_map(monkeypatch) -> str:
    """An official map that is announced but not shipped, in ``VANILLA_MAPS``.

    Badlands was the real one until DayZ 1.30 shipped it (2026 Q4). The
    entry has the shape seed_data's comment above ``Badlands`` describes. Every
    reader holds the same dict, so the entry reaches them all; the registry
    was built at import and leaves it out, as it does any unshipped map.
    """
    from app.shared.seed_data import VANILLA_MAPS

    monkeypatch.setitem(VANILLA_MAPS, UNRELEASED_MAP, {
        "mission": None,
        "terrain": UNRELEASED_TERRAIN,
        "released": False,
        "release": "Expansion announced",
        "url": "https://store.steampowered.com/",
    })
    return UNRELEASED_MAP


def _answer_server_up(monkeypatch, up: bool) -> None:
    """``is_server_running`` and ``is_server_process_alive`` both answer *up*,
    without asking systemd."""
    for name in ("is_server_running", "is_server_process_alive"):
        monkeypatch.setattr(f"app.server.service_control.{name}", lambda config=None: up)


@pytest.fixture()
def battleye_running(sample_config: "DayzConfig", monkeypatch) -> Path:
    """The server "running": only BattlEye's active copy of beserver_x64.cfg
    exists, with values of its own (so a reader that found it shows), and
    ``is_server_running`` / ``is_server_process_alive`` answer True. Returns
    the active file."""
    sample_config.beserver_cfg_path.unlink()
    active = sample_config.battleye_dir / ACTIVE_BESERVER_CFG
    active.write_text(ACTIVE_BESERVER_TEXT)
    _answer_server_up(monkeypatch, True)
    return active


@pytest.fixture()
def server_stopped(monkeypatch) -> None:
    """``is_server_running`` / ``is_server_process_alive`` answer False,
    without asking systemd."""
    _answer_server_up(monkeypatch, False)


@pytest.fixture()
def container_mounts(monkeypatch, tmp_path, tmp_path_factory) -> Path:
    """Run as inside the Docker image: an overlay root, with ``<tmp>/home/server``
    and ``<tmp>/home/.config/zsm`` bind-mounted the way docker-compose.yml
    mounts /home/zsm/server and the config, and a tmpfs on ``<tmp>/home/run``.
    Returns ``<tmp>/home``, the stand-in for /home/zsm."""
    from app.shared import container

    home = tmp_path.resolve() / "home"
    esc = str(home).replace(" ", "\\040")
    mountinfo = tmp_path_factory.mktemp("proc") / "mountinfo"
    mountinfo.write_text(
        "8898 8897 0:52 / / rw,relatime master:1 - overlay overlay rw,lowerdir=/l\n"
        f"8914 8898 259:9 /data/server {esc}/server rw,relatime - ext4 /dev/sda1 rw\n"
        f"8916 8898 259:9 /data/config {esc}/.config/zsm rw,relatime - ext4 /dev/sda1 rw\n"
        f"8920 8898 0:60 / {esc}/run rw,nosuid - tmpfs tmpfs rw\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(container, "IS_DOCKER", True)
    monkeypatch.setattr(container, "MOUNTINFO", mountinfo)
    return home


def write_xml(path: Path, content: str) -> Path:
    """Write XML content to a file, returns the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


PRIVATE = Path(__file__).resolve().parent / "private"


def private_fixture(name: str) -> str:
    """The server-output fixture *name* (`rpt/…`, `adm/…`) from `tests/private/`,
    byte for byte, or a skip.

    Logs a DayZ server wrote and files a server install ships are Bohemia's
    server's output, so they are not in this repository (plans/86 D2). They
    live in a private repository, checked out at `tests/private/`
    (gitignored). Without it the test skips and the run's summary says so.
    """
    path = PRIVATE / name
    if not path.is_file():
        pytest.skip(f"{PRIVATE_SKIP}: {name}")
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


DAYZ_DOCS = Path(__file__).resolve().parents[1] / "dayz-docs"


def dayz_docs(*parts: str) -> Path:
    """A file or folder of Bohemia's own reference material, or a skip.

    ``dayz-docs/`` is Bohemia's content and is not in the repository. A test
    that measures zsm against it runs where the folder has been captured and
    is skipped everywhere else, never failed.
    """
    path = DAYZ_DOCS.joinpath(*parts)
    if not path.exists():
        pytest.skip(f"dayz-docs/{'/'.join(parts)} is not in this checkout")
    return path


# Bohemia's stock mission files are theirs (ADPL-SA), so no test holds a copy
# (plans/86). A test that needs one reads it with `bohemia_ce` from Bohemia's
# own repository at a pinned commit, and is skipped where that download fails.
CE_RAW = "https://raw.githubusercontent.com/BohemiaInteractive/DayZ-Central-Economy"
#: DayZ 1.29's stock missions: the repository before 1.30's files (2026-08-13).
CE_1_29 = "9a21bb9f5fb9c62a7ce2761402196091588133e6"
#: DayZ 1.30's stock missions, the first with Badlands' (2026-10-16).
CE_1_30 = "12c79880b426c6bc727294f64a1da6d709d9bc9c"
CE_TIMEOUT = 30

# A test whose fixture is missing skips, and a skip looks like success at a
# glance. Each kind counts its skips, and the run's summary names them.
CE_SKIP = "Bohemia's mission file could not be downloaded"
PRIVATE_SKIP = "tests/private/ does not hold the fixture"
#: Per kind, the tests it skipped, counted by test file.
_fixture_skips: dict[str, Counter] = {CE_SKIP: Counter(), PRIVATE_SKIP: Counter()}
_FIXTURE_FIXES = {
    CE_SKIP: (
        "Bohemia's mission files could not be downloaded",
        ["Fix: let this machine reach raw.githubusercontent.com, then run the tests again."],
    ),
    PRIVATE_SKIP: (
        "tests/private/ does not hold their fixture (server output)",
        [
            "Expected for a contributor: the maintainer keeps these fixtures out of the repository.",
        ],
    ),
}

#: The files this run has read, so each is decoded once.
_ce_texts: dict[tuple[str, str], str] = {}
#: Set by `pytest_configure`: its cache dir keeps the downloads between runs.
_pytest_config = None


def _ce_download(url: str) -> bytes:
    """GET *url*, its certificate verified, or raise."""
    context = ssl.create_default_context()
    with urllib.request.urlopen(url, timeout=CE_TIMEOUT, context=context) as resp:
        return resp.read()


def _ce_cache_dir() -> Path | None:
    """`.pytest_cache/d/bohemia-ce` (gitignored), or None without pytest's cache plugin."""
    cache = getattr(_pytest_config, "cache", None)
    return cache.mkdir("bohemia-ce") if cache is not None else None


def bohemia_ce(commit: str, path: str) -> str:
    """Bohemia's *path* in DayZ-Central-Economy at *commit*, or a skip.

    The text is the file as it stands, line ends, tabs and a BOM included.
    The first run downloads it into pytest's cache, and later runs read it
    from there. A network or HTTP error skips the test, never fails it.
    """
    key = (commit, path)
    if key in _ce_texts:
        return _ce_texts[key]
    cache = _ce_cache_dir()
    cached = cache / commit / path if cache is not None else None
    if cached is not None and cached.is_file():
        data = cached.read_bytes()
    else:
        url = f"{CE_RAW}/{commit}/{urllib.parse.quote(path)}"
        try:
            data = _ce_download(url)
        except (OSError, http.client.HTTPException) as exc:
            pytest.skip(f"{CE_SKIP}: {path} ({url}): {exc}")
        if cached is not None:
            cached.parent.mkdir(parents=True, exist_ok=True)
            part = cached.with_name(f"{cached.name}.{os.getpid()}.part")
            part.write_bytes(data)
            os.replace(part, cached)
    text = _ce_texts[key] = data.decode("utf-8")
    return text


def ce_lines(text: str) -> list[str]:
    """*text*'s lines, each with its own line end (`\\r\\n` stays whole)."""
    return re.findall(r"[^\n]*\n|[^\n]+$", text)


def ce_pick(lines: list[str], *markers: str) -> list[str]:
    """One line per marker: the first that holds it, after the line the
    marker before it picked. So `"</event>"` after an event's name is that
    event's own closing line."""
    picked, at = [], 0
    for marker in markers:
        at = next(i for i in range(at, len(lines)) if marker in lines[i])
        picked.append(lines[at])
        at += 1
    return picked


def ce_block(lines: list[str], marker: str) -> list[str]:
    """The lines from the first that holds *marker* to the one that closes
    what it opens: an XML element's own closing tag (the line itself for
    one that closes there), or for JSON the bracket that balances it."""
    start = next(i for i, line in enumerate(lines) if marker in line)
    first = lines[start].strip()
    tag = re.match(r"<([\w.:-]+)", first)
    if tag:
        close = f"</{tag.group(1)}>"
        if first.endswith("/>") or close in first:
            return lines[start:start + 1]
        end = next(i for i in range(start + 1, len(lines)) if close in lines[i])
        return lines[start:end + 1]
    depth = 0
    for end in range(start, len(lines)):
        depth += sum(map(lines[end].count, "{[")) - sum(map(lines[end].count, "}]"))
        if depth <= 0:
            return lines[start:end + 1]
    raise ValueError(f"{marker!r} opens a block that never closes")


SITE_DOCS = Path(
    os.environ.get("ZSM_SITE") or Path(__file__).resolve().parents[2] / "zedstead-site"
) / "src" / "docs"


def site_doc(name: str) -> Path:
    """One page of the user docs, or a skip.

    The docs (``install.md``, ``help.md`` …) moved to the zedstead.com site on
    2026 Q4: the ``zedstead-site`` repo, beside this checkout or at
    ``$ZSM_SITE``, under ``src/docs/``. A test that checks a page against
    the code runs where that checkout is and is skipped everywhere else.
    """
    path = SITE_DOCS / name
    if not path.exists():
        pytest.skip(f"{name} is not in {SITE_DOCS} (set ZSM_SITE to your zedstead-site checkout)")
    return path


def site_doc_pages() -> list[Path]:
    """Every page in the site's docs, or [] without a zedstead-site checkout."""
    return sorted(SITE_DOCS.glob("*.md")) if SITE_DOCS.is_dir() else []


def set_instance_id(server_dir: Path, value: object) -> None:
    """Give *server_dir*'s serverDZ.cfg an ``instanceId`` line. The fixtures'
    cfg has none, which zsm reads as 1, the value Bohemia's file ships."""
    cfg = server_dir / "serverDZ.cfg"
    cfg.write_text(f"instanceId = {value};\n" + cfg.read_text())


def write_json(path: Path, data: dict | list) -> Path:
    """Write JSON data to a file, returns the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def write_test_mods(config, names, **flags) -> None:
    """Make *names* the instance's mod list (mods.json, plans/77), in that order.

    The one way a test writes a mod list, so no test depends on the file's
    format. A mod already in the list keeps its flags; *flags* are
    `set_test_flags`'s, applied after.
    """
    from app.modmgr import modlist

    modlist.update(config, lambda ml: ml.set_names(list(names)))
    if flags:
        set_test_flags(config, **flags)


def set_test_flags(config, *, disabled=None, server_only=None, pinned=None, manual=None) -> None:
    """Set the mod list's flags the way assigning the old config.json fields did:
    *disabled* and *server_only* replace the whole set. A name they hold that
    the list lacks is added to it, so a test may set a flag before the list."""
    from app.modmgr import modlist

    def change(ml) -> None:
        for names in (disabled, server_only):
            for n in names or ():
                ml.add(n)
        if disabled is not None:
            for m in ml.mods:
                m.enabled = m.name not in set(disabled)
        if server_only is not None:
            for m in ml.mods:
                m.server_only = m.name in set(server_only)
        if pinned is not None:
            ml.pinned = list(pinned)
        if manual is not None:
            ml.manual = manual

    modlist.update(config, change)


def mods_lines(text: str) -> list[str]:
    """The mod names in *text* written one per line, as mods.txt held them."""
    return [line.strip().rstrip(";") for line in text.splitlines() if line.strip()]


def installed_names(config) -> list[str]:
    from app.modmgr import modlist

    return modlist.installed(config)


def learned_ids() -> dict[str, str]:
    """What workshop-ids.json holds: the IDs zsm learned, without the shipped ones."""
    import json

    from app.shared.paths import workshop_ids_file

    path = workshop_ids_file()
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def disabled_names(config) -> list[str]:
    """The disabled mods, in list order."""
    from app.modmgr import modlist

    return [m.name for m in modlist.load(config).mods if not m.enabled]


def server_only_names(config) -> list[str]:
    from app.modmgr import modlist

    return modlist.server_only(config)


def pinned_names(config) -> list[str]:
    from app.modmgr import modlist

    return modlist.load(config).pinned


def is_manual(config) -> bool:
    from app.modmgr import modlist

    return modlist.load(config).manual


def write_test_ids(ids: dict[str, str]) -> None:
    """Workshop IDs zsm learned (workshop-ids.json), *ids* being name -> ID."""
    from app.shared.paths import workshop_ids_file

    write_json(workshop_ids_file(), ids)


def manual_dir() -> Path:
    """The manual XML store of a config with no instance, which is where the
    web routes write too. Read at call time, so it follows ``sample_config``'s
    config dir when a test has one and ``_isolate_config_dir``'s otherwise."""
    from app.shared.config import DayzConfig

    return DayzConfig().paths.custom_xml_dir


def pytest_configure(config):
    global _pytest_config
    _pytest_config = config
    config.addinivalue_line(
        "markers", "real_scrypt_cost: hash passwords at app.web.auth's own scrypt cost"
    )


def count_fixture_skip(report, skips: dict[str, Counter] | None = None) -> None:
    """Count *report* under its kind if a missing fixture skipped its test."""
    if not report.skipped or not isinstance(report.longrepr, tuple):
        return
    for kind, counts in (_fixture_skips if skips is None else skips).items():
        if kind in str(report.longrepr[2]):
            counts[report.nodeid.split("::")[0]] += 1


def missing_fixture_lines(skips: dict[str, Counter] | None = None) -> list[str]:
    """What the summary says about the tests a missing fixture skipped: per
    kind the count, the test files and the fix. [] when none skipped."""
    lines = []
    for kind, counts in (_fixture_skips if skips is None else skips).items():
        if not counts:
            continue
        what, fix = _FIXTURE_FIXES[kind]
        total = sum(counts.values())
        lines.append(f"{total} test{'' if total == 1 else 's'} skipped because {what}:")
        lines += [f"  {file} ({n})" for file, n in sorted(counts.items())]
        lines += [f"  {line}" for line in fix]
    if lines:
        lines.append("This run is not green: the tests above did not run.")
    return lines


def pytest_runtest_logreport(report):
    count_fixture_skip(report)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    lines = missing_fixture_lines()
    if lines:
        terminalreporter.section("fixtures missing: this run is not green", sep="!", red=True, bold=True)
        for line in lines:
            terminalreporter.line(line, red=True)


@pytest.fixture(autouse=True)
def _no_site_requests(monkeypatch):
    """No test reaches zedstead.com.

    ``site_ops.ENDPOINT`` is the live backend, so a test that turns a
    switch on and lets the heartbeat run would count a fake install there.
    Every request fails loudly instead. A test that expects one replaces this
    with its own ``net`` fixture, which records the requests.
    """
    from types import SimpleNamespace

    import requests

    from app.ops import site_ops

    def refuse(method, url, **_kwargs):
        raise AssertionError(f"a test tried to reach zedstead.com: {method} {url}")

    monkeypatch.setattr(
        site_ops,
        "requests",
        SimpleNamespace(request=refuse, RequestException=requests.RequestException),
    )


@pytest.fixture(autouse=True)
def _cheap_scrypt(monkeypatch, request):
    """Hash the suite's passwords at p=1, the cost before plans/68 AUTH-14.

    p=5 is about 0.13 s a hash, and hundreds of tests hash a password. A hash
    carries its own cost, so verifying still checks what was hashed. A test
    of the cost itself opts out with ``@pytest.mark.real_scrypt_cost``.
    """
    if request.node.get_closest_marker("real_scrypt_cost"):
        return
    monkeypatch.setattr("app.web.auth._SCRYPT_P", 1)


@pytest.fixture(autouse=True)
def _default_system_scope(monkeypatch, request, tmp_path_factory):
    """Pin systemd scope resolution to ``system`` for the whole suite.

    ``resolve_service_scope`` otherwise depends on host state (rule 3: an
    existing ``/etc/systemd/system/dayz-server.service`` → system), so a dev
    box with the unit installed resolves differently from a clean CI box —
    non-deterministic tests. Pinning to ``system`` keeps every legacy
    integration test (which assumes the sudo/system path) deterministic
    regardless of host. An explicit ``config.service_scope`` still wins, so
    user-scope tests opt in with ``service_scope="user"``. The resolver's own
    rules are covered directly in ``tests/test_systemd_scope.py`` (exempted
    below, since it monkeypatches ``SYSTEM_UNIT_DIR``/``IS_ROOT`` to drive the
    real resolver).
    """
    if request.module.__name__.rsplit(".", 1)[-1] == "test_systemd_scope":
        return
    import app.shared.systemd_scope as ss

    def _pinned(config=None):
        explicit = (getattr(config, "service_scope", "") or "").strip().lower()
        if explicit in ("system", "user"):
            return explicit
        return "system"

    monkeypatch.setattr(ss, "resolve_service_scope", _pinned)

    # The unit guard (plans/78 D2) reads the installed unit before a write.
    # In system scope that is /etc/systemd/system, so a hand-written unit on
    # the host would decide the test; it reads a throwaway directory instead.
    from app.server import unit_guard

    real_installed_path = unit_guard.installed_path
    host_free = tmp_path_factory.mktemp("system-units")

    def _installed_path(config):
        path = real_installed_path(config)
        return path if ss.get_scope(config).user_mode else host_free / path.name

    monkeypatch.setattr(unit_guard, "installed_path", _installed_path)


@pytest.fixture(autouse=True)
def _isolate_config_dir(tmp_path_factory, monkeypatch):
    """Point zsm's config dir at a throwaway directory for every test.

    Anything that resolves its path at call time — ``config.paths.*``, the
    machine-wide functions in ``app.shared.paths``, the Versions store that
    every ``save_xml``/``save_json`` writes to — follows this, so a test that
    saves an XML file without building a ``sample_config`` no longer writes a
    timestamped backup into the real ``~/.config/zsm/versions``. It replaced
    one fixture per import-time constant (plans/44 step 3), which is what the
    constants cost while they existed. ``sample_config`` patches the same
    names again, to its own ``tmp_path``.

    Uses ``tmp_path_factory`` (a separate base temp dir), not the per-test
    ``tmp_path`` — some tests assert their own ``tmp_path`` contains exactly
    the files they wrote.
    """
    config_dir = tmp_path_factory.mktemp("zsm-test-config")
    monkeypatch.setattr("app.shared.paths.CONFIG_DIR", config_dir)
    monkeypatch.setattr("app.shared.paths.CONFIG_FILE", config_dir / "config.json")
    monkeypatch.setattr("app.shared.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("app.shared.config.CONFIG_FILE", config_dir / "config.json")
    # The root group migrates a legacy install and refuses while another
    # `zsm web` runs (plans/44 D3). The host's process table must not decide
    # a test; the migration's own tests pass `panel_running` explicitly.
    monkeypatch.setattr("app.server.instance_migration.other_panel_running", lambda: False)
    # The root group also pins the instance in the environment (D1), which
    # outlives the invocation: one test's pin must not reach the next test.
    os.environ.pop("ZSM_INSTANCE", None)
    yield
    os.environ.pop("ZSM_INSTANCE", None)


#: Invented terrain names the world-file reader tests build files for.
INVENTED_TERRAINS = frozenset({"testmap", "somemap", "othermap", "testdlc", "goodmap", "bigmap", "nosuchmap"})


@pytest.fixture
def invented_terrains_allowed(monkeypatch):
    """Put the invented test terrains on `registry.TERRAIN_LAYER_MAPS`, so the
    world-file readers' own tests reach the code behind the allowlist."""
    from app.maps import registry

    monkeypatch.setattr(registry, "TERRAIN_LAYER_MAPS", registry.TERRAIN_LAYER_MAPS | INVENTED_TERRAINS)
