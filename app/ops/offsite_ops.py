"""Offsite backup transport — get a backup zip off the machine (TODO C1).

Backups live next to the server they protect, so the VPS dying takes both.
This module is the other end: after a backup is written, push it somewhere
else.

**The transport is one `rclone` binary, not a library per cloud.** The
alternatives were `paramiko` + `boto3` (two large dependencies on every
install, for a feature most users will configure once) or the system `sftp`
(zero dependencies, but no S3 and no B2, which the requirement names). rclone
is a single static binary that speaks SFTP, S3 and B2 — plus ~70 other
backends we get for free — so zsm gains no Python dependency at all. It is
fetched on demand into the config dir, the same way SteamCMD, the map images
and the Expansion templates already are.

**zsm writes rclone's config file itself.** Nobody has to run
`rclone config` in a terminal, which would not work from a browser panel
anyway. Two consequences that are the point of the arrangement:

- **Secrets stay out of `config.json`.** ``config.json`` holds only routing —
  which kind of remote and which path — while the password, the SSH key path
  and the S3/B2 keys live in ``<config dir>/rclone.conf`` at mode 0600, which
  zsm writes and never reads back to a caller. ``remote_status()`` reports
  *whether* a remote is configured and which fields hold a value, never a
  value. A save that leaves a field blank keeps the stored one.
- **zsm owns exactly one section of that file**, ``[zsm-offsite]``. A file
  the user maintains themselves is left alone apart from that section, so
  pointing ``rclone_path`` at a system rclone with existing remotes is safe.

Upload only, by design: the problem is "the backup died with the box", and
copying it off solves that. Pulling one back down is the admin's own rclone /
FTP client for now.

Sync, with ``log`` and ``should_cancel`` callables — the ``bundle_ops.
export_bundle`` shape — so the CLI calls it directly and the web runs it in
``asyncio.to_thread`` with ``job.log_line_threadsafe``.
"""

from __future__ import annotations

import base64
import hashlib
import platform
import re
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path
from typing import Callable, Iterable

from app.modmgr.mods import breaks_a_line
from app.shared.json_utils import atomic_write_text
from app.shared.platform import IS_WINDOWS

#: The one rclone.conf section zsm writes. Anything else in that file is the
#: user's and is preserved.
REMOTE_NAME = "zsm-offsite"

#: Remote kinds zsm knows how to write a config section for. rclone supports
#: far more; these are the three the offsite-backup requirement names, and
#: each one's fields are spelled out in ``REMOTE_FIELDS`` below.
SUPPORTED_KINDS: tuple[str, ...] = ("sftp", "s3", "b2")

#: Per kind: the rclone config keys zsm will write, and which of them are
#: secret (never echoed back, and for SFTP passed through ``rclone obscure``).
#: A key the section never had and the payload leaves blank is not written —
#: for SFTP that is what makes "no key_file, no pass" mean "use ssh-agent and
#: ~/.ssh", which is the normal case on a VPS. A blank key the section already
#: has keeps its value (see ``write_remote``).
REMOTE_FIELDS: dict[str, tuple[str, ...]] = {
    "sftp": ("host", "user", "port", "key_file", "pass"),
    "s3": ("provider", "access_key_id", "secret_access_key", "endpoint", "region"),
    "b2": ("account", "key", "hard_delete"),
}

#: Per kind: the fields a target cannot work without. ``form_problems``
#: refuses a save that leaves one empty, and the web form marks them. SFTP's
#: user, port and key are optional: rclone falls back to the local user, 22
#: and ssh-agent with ~/.ssh.
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "sftp": ("host",),
    "s3": ("access_key_id", "secret_access_key"),
    "b2": ("account", "key"),
}

#: Kinds whose destination path starts with a bucket name, so a blank path
#: names nowhere to write. A blank SFTP path is the remote user's home.
PATH_REQUIRED_KINDS: frozenset[str] = frozenset({"s3", "b2"})

#: Fields whose value must never be returned to a caller.
SECRET_FIELDS: frozenset[str] = frozenset({"pass", "secret_access_key", "key"})

#: SFTP's password is the only one rclone expects *obscured* rather than
#: plain; the S3/B2 keys are stored as given. Obscuring is not encryption —
#: rclone says so itself — which is why the file is 0600 either way.
OBSCURED_FIELDS: frozenset[str] = frozenset({"pass"})

#: The rclone release zsm downloads when the machine has none, and the sha256
#: of each of its builds. The hashes are copied from that release's
#: ``SHA256SUMS`` after checking its PGP signature against rclone's release key
#: (FBF737ECE9F8AB18604BD2AC93935E02FF3B54FA, https://rclone.org/release_signing/).
#: They live here rather than being fetched beside the zip, so the host that
#: serves the zip cannot vouch for it too (plans/68 SUP-5, 2P-C9). To bump:
#: change the version, verify the new ``SHA256SUMS`` the same way
#: (``gpg --verify``), and replace the table.
RCLONE_VERSION = "v1.75.1"
RCLONE_SHA256: dict[str, str] = {
    "rclone-v1.75.1-linux-386.zip": "9250a48ace446b32ad3f3cb26e0744f9245efb65ef4f51dfb416288950b84285",
    "rclone-v1.75.1-linux-amd64.zip": "982b5aa772841168f8e380f139e9e787b2a105403e32b94da8676a0e1c0a13ab",
    "rclone-v1.75.1-linux-arm64.zip": "03f2504174034b6d004152ed7369251c9a9ec1f7e0836eda420f5c7a5ec0dff9",
    "rclone-v1.75.1-linux-arm-v7.zip": "33c683053b677d9a89d4a985e8a25cfed7c8a95dd8379e367a5c73d8745356c3",
    "rclone-v1.75.1-osx-amd64.zip": "29253d0288b8fbbac46baad6e5f6add6cb01d462c79f10805bbd4631c4cdf82c",
    "rclone-v1.75.1-osx-arm64.zip": "c61d7a371c62bcbbe882c3423aa4b8bf63485c248dd0f692997b8f0c3f6d0c6f",
    "rclone-v1.75.1-windows-386.zip": "42257481a961b39cdee3b3eea7b9945a1961825e332d07b3aa321808c0afc436",
    "rclone-v1.75.1-windows-amd64.zip": "200eb602c126d82aa38b51e0f6b9ae837473ff99b51278d3f6f837574c494d6e",
    "rclone-v1.75.1-windows-arm64.zip": "c3c6cd0424dd49076ad179c30c3f9e5cde2c004ec07ea9fe6911f23e32eafe0f",
}

_DOWNLOAD_BASE = "https://downloads.rclone.org/{version}/rclone-{version}-{os}-{arch}.zip"

# How long a `rclone about`/`rclone lsd` probe may take. An unreachable host
# otherwise sits in rclone's own retry loop and the panel waits with it.
PROBE_TIMEOUT = 30.0


class RcloneError(Exception):
    """Raised when rclone is missing, misconfigured, or fails a transfer."""


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def _config_dir() -> Path:
    # Imported late: tests monkeypatch app.shared.paths.CONFIG_DIR.
    from app.shared.paths import CONFIG_DIR

    return Path(CONFIG_DIR)


def rclone_config_path() -> Path:
    """Where zsm keeps the rclone config it writes."""
    from app.shared.paths import rclone_conf

    return rclone_conf()


def rclone_binary_path() -> Path:
    """Where a zsm-downloaded rclone lives."""
    name = "rclone.exe" if IS_WINDOWS else "rclone"
    return _config_dir() / "bin" / name


def find_rclone(config=None) -> Path | None:
    """An rclone zsm can run, or None.

    Order: an explicit ``config.rclone_path`` (the escape hatch — a system
    install, a custom build), then ``$PATH``, then the copy zsm downloaded.
    A configured path that does not exist is **not** silently skipped; that
    would turn a typo into a surprise download.
    """
    explicit = (getattr(config, "rclone_path", "") or "").strip() if config else ""
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_file() else None

    on_path = shutil.which("rclone")
    if on_path:
        return Path(on_path)

    downloaded = rclone_binary_path()
    return downloaded if downloaded.is_file() else None


# ---------------------------------------------------------------------------
# Fetching the binary
# ---------------------------------------------------------------------------


def download_url() -> str:
    """The zip URL of the pinned rclone release for this machine.

    rclone publishes ``v<ver>/rclone-v<ver>-<os>-<arch>.zip``; the names are
    its own, not Python's, so the two mappings below are the translation.
    """
    machine = platform.machine().lower()
    arch = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
        "armv7l": "arm-v7",
        "i386": "386",
        "i686": "386",
    }.get(machine)
    if arch is None:
        raise RcloneError(
            f"No rclone build is known for this architecture ({machine}). "
            "Install rclone yourself and set the rclone_path setting."
        )

    system = platform.system().lower()
    os_name = {"linux": "linux", "windows": "windows", "darwin": "osx"}.get(system)
    if os_name is None:
        raise RcloneError(
            f"No rclone build is known for this platform ({system}). "
            "Install rclone yourself and set the rclone_path setting."
        )
    return _DOWNLOAD_BASE.format(version=RCLONE_VERSION, os=os_name, arch=arch)


def ensure_rclone(config=None, *, log: Callable[[str], None] | None = None) -> Path:
    """Return a usable rclone, downloading one if there is none.

    Raises ``RcloneError`` rather than returning None: every caller needs the
    binary, and the message names the setting that skips the download.
    """
    emit = log or (lambda _line: None)

    existing = find_rclone(config)
    if existing is not None:
        return existing

    explicit = (getattr(config, "rclone_path", "") or "").strip() if config else ""
    if explicit:
        raise RcloneError(
            f"rclone_path points at {explicit}, which is not a file. "
            "Fix the setting or clear it to let Zedstead download rclone."
        )

    url = download_url()
    filename = url.rsplit("/", 1)[-1]
    expected = RCLONE_SHA256.get(filename)
    if expected is None:
        raise RcloneError(
            f"Zedstead has no checked sha256 for {filename}, so it cannot verify the "
            "download. Install rclone yourself and set the rclone_path setting."
        )
    target = rclone_binary_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    emit(f"Downloading rclone from {url}")

    import requests  # late: keeps `import offsite_ops` cheap for the CLI

    archive = target.parent / "rclone-download.zip"
    try:
        digest = hashlib.sha256()
        resp = requests.get(url, timeout=120, stream=True)
        resp.raise_for_status()
        with open(archive, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                digest.update(chunk)
                fh.write(chunk)
        if digest.hexdigest() != expected:
            raise RcloneError(
                f"The rclone download does not match the sha256 rclone published "
                f"for {filename} ({RCLONE_VERSION}). Nothing was installed."
            )
        emit(f"Checked {filename} against its pinned sha256")
        _extract_binary(archive, target)
    except RcloneError:
        raise
    except Exception as exc:  # network, zip, disk — all the same to the caller
        raise RcloneError(f"Could not download rclone: {exc}") from exc
    finally:
        archive.unlink(missing_ok=True)

    if not IS_WINDOWS:
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    emit(f"rclone installed at {target}")
    return target


def _extract_binary(archive: Path, target: Path) -> None:
    """Pull the single rclone executable out of the release zip.

    The zip holds a versioned top directory (``rclone-v1.75.1-linux-amd64/``)
    with the binary, the docs and the man page, so this matches on the
    basename rather than a fixed path — the version is in the name, so a bump
    of ``RCLONE_VERSION`` needs no change here.
    """
    wanted = "rclone.exe" if IS_WINDOWS else "rclone"
    with zipfile.ZipFile(archive) as zf:
        member = next(
            (n for n in zf.namelist() if Path(n).name == wanted and not n.endswith("/")),
            None,
        )
        if member is None:
            raise RcloneError(f"The rclone download contained no {wanted}.")
        with zf.open(member) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)


# ---------------------------------------------------------------------------
# rclone.conf — zsm owns one section of it
# ---------------------------------------------------------------------------

_SECTION_RE = re.compile(r"^\s*\[(?P<name>[^\]]+)\]\s*$")


def _split_sections(text: str) -> list[tuple[str | None, list[str]]]:
    """Split an rclone/INI file into ``(section name, lines)`` blocks.

    Written by hand rather than with ``configparser`` for one reason: this
    file may be the user's own, and configparser would rewrite every other
    section — comments, spacing, key order — on the way out. zsm rewrites
    its own block and copies the rest through byte for byte.
    """
    blocks: list[tuple[str | None, list[str]]] = []
    current: tuple[str | None, list[str]] = (None, [])
    # "\n" only, as rclone reads the file: str.splitlines would also split at
    # a Unicode separator inside a value and write it back as a line break.
    for line in text.split("\n"):
        match = _SECTION_RE.match(line)
        if match:
            blocks.append(current)
            current = (match.group("name"), [line])
        else:
            current[1].append(line)
    blocks.append(current)
    return [b for b in blocks if b[0] is not None or b[1]]


def write_remote(
    config, kind: str, fields: dict[str, str], *, clear: Iterable[str] = ()
) -> Path:
    """Write the ``[zsm-offsite]`` section, leaving every other one intact.

    ``fields`` is filtered to ``REMOTE_FIELDS[kind]`` — an unknown key is
    dropped rather than written, because the values come from a web form and
    an arbitrary key here is an arbitrary rclone option. Empty values are
    dropped too: for SFTP that is what makes "no key_file and no pass" mean
    "use ssh-agent and ~/.ssh", the normal case on a VPS.

    **A blank field keeps what is stored, when the kind stays the same.** No
    route hands a stored value back, so the form arrives blank after a reload;
    replacing the section with what it sent wiped the host, user, password and
    key path whenever the admin only changed the destination path or the
    auto-upload switch. Now the stored section is the base and a typed value
    replaces its key. ``clear`` names keys to drop on purpose (filtered to the
    same field list), which is how an optional value such as ``key_file`` is
    taken away. A different kind replaces the section outright, and a save of
    the same kind with nothing typed and nothing cleared writes nothing.
    """
    if kind not in SUPPORTED_KINDS:
        raise RcloneError(f"Unsupported remote kind: {kind!r}")

    allowed = REMOTE_FIELDS[kind]
    values = {k: str(v).strip() for k, v in fields.items() if k in allowed}
    values = {k: v for k, v in values.items() if v}
    cleared = {str(k) for k in clear} & set(allowed)
    # One value is one line of rclone.conf. A line break inside it would start
    # a line of its own, which is the arbitrary key the filter above refuses;
    # that includes every separator str.splitlines knows, since a Unicode one
    # would become a real line break wherever Python rewrites the file.
    # The value is never echoed: it may be a password.
    for key, value in values.items():
        if breaks_a_line(value.replace("\t", "")):
            raise RcloneError(f"The {key} field must be a single line.")

    stored = _section_values()
    same_kind = stored.get("type") == kind
    if same_kind and not values and not cleared:
        return rclone_config_path()

    for key in values:
        if key in OBSCURED_FIELDS:
            values[key] = obscure(config, values[key])

    if same_kind:
        # The stored values are already in rclone's form (the password
        # obscured), so they are kept as they are, never obscured twice.
        kept = {k: v for k, v in stored.items() if k in allowed and v and k not in cleared}
        values = {**kept, **values}

    lines = [f"[{REMOTE_NAME}]", f"type = {kind}"]
    lines += [f"{k} = {values[k]}" for k in allowed if k in values]
    if kind == "sftp":
        # zsm's own key, never the form's: rclone checks the host key only
        # when this is set (plans/68 NET-4).
        lines.append(f"known_hosts_file = {known_hosts_path()}")

    path = rclone_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    kept = [b for b in _split_sections(existing) if b[0] != REMOTE_NAME]

    out: list[str] = []
    for _name, block in kept:
        out.extend(block)
    while out and not out[-1].strip():
        out.pop()
    if out:
        out.append("")
    out.extend(lines)
    _write_owner_only(path, "\n".join(out) + "\n")
    return path


def _write_owner_only(path: Path, text: str) -> None:
    """Write *text* to *path* at mode 0600.

    0600 from creation, not after: between a default-mode write and a chmod
    there is a window where the credentials are world-readable. The atomic
    writer's temp file is a mkstemp, which is 0600 from the start
    (``owner_only`` keeps it so over an older, wider file), and the
    file it replaces is never emptied first: text that could not be encoded
    used to leave rclone.conf at 0 bytes, the user's own remotes included.
    """
    atomic_write_text(path, text, owner_only=True)


def clear_remote() -> bool:
    """Remove zsm's section. Returns True if there was one.

    The confirmed SFTP host keys go with it: they belong to that remote, and
    setting the remote up again is how an admin deliberately accepts a host
    whose key changed.
    """
    known_hosts_path().unlink(missing_ok=True)
    path = rclone_config_path()
    if not path.is_file():
        return False
    blocks = _split_sections(path.read_text(encoding="utf-8"))
    kept = [b for b in blocks if b[0] != REMOTE_NAME]
    if len(kept) == len(blocks):
        return False
    out: list[str] = []
    for _name, block in kept:
        out.extend(block)
    _write_owner_only(path, "\n".join(out).rstrip("\n") + "\n" if out else "")
    return True


def read_remote_kind() -> str:
    """The ``type =`` of zsm's section, or "" — never its credentials."""
    path = rclone_config_path()
    if not path.is_file():
        return ""
    for name, block in _split_sections(path.read_text(encoding="utf-8")):
        if name != REMOTE_NAME:
            continue
        for line in block:
            key, sep, value = line.partition("=")
            if sep and key.strip() == "type":
                return value.strip()
    return ""


def remote_status(config) -> dict:
    """What the panel needs to describe the offsite target.

    Deliberately carries no secret: the configured *kind* and *path*, whether
    the binary and the config section exist, and where each of them is. A
    caller wanting to know if a password is right runs ``test_remote``.
    """
    binary = find_rclone(config)
    kind = read_remote_kind()
    return {
        "kind": kind,
        "configured": bool(kind),
        "path": (getattr(config, "backup_remote_path", "") or "").strip(),
        "auto_upload": bool(getattr(config, "backup_remote_auto", False)),
        "binary": str(binary) if binary else "",
        "binary_available": binary is not None,
        "config_file": str(rclone_config_path()),
        # "" for S3/B2, else `host_key_status`'s answer. Read from the files
        # only: the host is asked by `probe_remote`, never here.
        "host_key": host_key_status(),
        # A plain-http S3 endpoint (plans/68 NET-15), "" otherwise.
        "warning": endpoint_warning(),
        "known_hosts_file": str(known_hosts_path()),
        "supported_kinds": list(SUPPORTED_KINDS),
        "fields": {k: list(v) for k, v in REMOTE_FIELDS.items()},
        "secret_fields": sorted(SECRET_FIELDS),
        # What form_problems requires, so the form marks it before a save.
        "required_fields": {k: list(v) for k, v in REQUIRED_FIELDS.items()},
        "path_required_kinds": sorted(PATH_REQUIRED_KINDS),
        # Which of the kind's fields hold a value: names only, never a value,
        # so the card can say "stored, leave blank to keep" and offer Clear.
        "set_fields": stored_field_names(),
    }


def stored_field_names() -> list[str]:
    """The names of the configured kind's fields that hold a value."""
    values = _section_values()
    return [k for k in REMOTE_FIELDS.get(values.get("type", ""), ()) if values.get(k)]


def form_problems(kind: str, fields: dict, clear: Iterable[str], path: str) -> dict[str, str]:
    """What is wrong with an offsite form before it is saved: field name to message.

    A required field counts as filled when the form typed a value, or when the
    stored target has the same kind, holds a value for it and the form did not
    clear it. No route returns a stored value, so the form arrives blank after
    a reload, and a blank field there means "keep". Another kind replaces the
    whole target, so then every required field must be typed. The key ``path``
    stands for the destination path. An empty dict means the form is fine.
    """
    stored = set(stored_field_names()) if read_remote_kind() == kind else set()
    stored -= {str(k) for k in clear}
    typed = {k: str(v).strip() for k, v in fields.items() if str(v).strip()}
    problems: dict[str, str] = {}
    for key in REQUIRED_FIELDS.get(kind, ()):
        if key not in typed and key not in stored:
            problems[key] = "Required."
    port = typed.get("port")
    if port is not None and not (port.isdigit() and 1 <= int(port) <= 65535):
        problems["port"] = "A number from 1 to 65535."
    hard_delete = typed.get("hard_delete")
    if hard_delete is not None and hard_delete.lower() not in ("true", "false"):
        problems["hard_delete"] = "true or false."
    endpoint = typed.get("endpoint")
    if endpoint is not None and re.search(r"\s", endpoint):
        problems["endpoint"] = "A URL has no spaces."
    if kind in PATH_REQUIRED_KINDS and not path.strip():
        problems["path"] = "Required: the bucket name, then an optional folder."
    return problems


def is_configured(config) -> bool:
    """True when an upload could actually be attempted."""
    return bool(read_remote_kind()) and find_rclone(config) is not None


def plain_http_warning(kind: str, endpoint: str) -> str:
    """A warning when an S3 endpoint is plain ``http://``, else "".

    A warning and never a refusal (plans/68 NET-15): a MinIO on the LAN
    without TLS is a setup people really run. But a backup carries config.json
    and its credentials, and over http every one of them crosses the network
    readable, so the admin is told where they set it up and in every upload.
    """
    if kind != "s3" or not endpoint.strip().lower().startswith("http://"):
        return ""
    return (
        "Warning: the S3 endpoint uses http://, so every backup, the credentials "
        "in it included, travels unencrypted. Use https:// unless the endpoint is "
        "on a network you trust."
    )


def endpoint_warning() -> str:
    """`plain_http_warning` for the configured remote, or ""."""
    values = _section_values()
    return plain_http_warning(values.get("type", ""), values.get("endpoint", ""))


# ---------------------------------------------------------------------------
# SFTP host keys — zsm's own known_hosts (plans/68 NET-4)
# ---------------------------------------------------------------------------
#
# rclone's SFTP backend accepts any host key unless its section names a
# known_hosts file, and a backup holds config.json with every credential in
# it. So zsm keeps the keys the admin confirmed in a file of its own, beside
# rclone.conf, and points the section at it. First contact shows the
# fingerprint and saves nothing until the admin confirms it; a key that differs
# from a confirmed one is refused and never replaced here.

#: Seconds ssh-keyscan waits for the host.
KEYSCAN_TIMEOUT = 10

_KEY_TYPE_RE = re.compile(r"[A-Za-z0-9@._-]+")


def known_hosts_path() -> Path:
    from app.shared.paths import offsite_known_hosts

    return offsite_known_hosts()


def _section_values() -> dict[str, str]:
    """zsm's section as key -> value. Internal: it holds the credentials."""
    path = rclone_config_path()
    if not path.is_file():
        return {}
    for name, block in _split_sections(path.read_text(encoding="utf-8")):
        if name != REMOTE_NAME:
            continue
        values = {}
        for line in block[1:]:
            key, sep, value = line.partition("=")
            if sep:
                values[key.strip()] = value.strip()
        return values
    return {}


def sftp_endpoint() -> tuple[str, int] | None:
    """``(host, port)`` of an SFTP remote, or None for any other kind."""
    values = _section_values()
    host = values.get("host", "")
    if values.get("type") != "sftp" or not host:
        return None
    try:
        port = int(values.get("port") or 22)
    except ValueError:
        port = 22
    return host, port


def known_hosts_pattern(host: str, port: int) -> str:
    """How known_hosts names *host*: bare on port 22, ``[host]:port`` on any
    other, as OpenSSH and the Go library rclone uses both read it."""
    host = host.strip("[]")
    return host if port == 22 else f"[{host}]:{port}"


def key_fingerprint(blob: str) -> str:
    """``SHA256:…``, the form ``ssh-keygen -lf`` prints."""
    digest = hashlib.sha256(base64.b64decode(blob)).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def scan_host_keys(host: str, port: int) -> list[tuple[str, str]]:
    """``(key type, base64 key)`` for every host key *host* offers.

    The one place zsm reaches the host for its key, through OpenSSH's
    ``ssh-keyscan``, and the boundary tests replace. Raises ``RcloneError``
    when the tool is missing or the host gives no key.
    """
    exe = shutil.which("ssh-keyscan")
    if not exe:
        raise RcloneError(
            "Checking the SFTP host's key needs ssh-keyscan, part of the OpenSSH "
            "client. Install it (apt install openssh-client) and test again."
        )
    try:
        proc = subprocess.run(
            [exe, "-T", str(KEYSCAN_TIMEOUT), "-p", str(port), "--", host],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=KEYSCAN_TIMEOUT + 20,
        )
    except subprocess.TimeoutExpired as exc:
        raise RcloneError(f"{host} did not answer on port {port}.") from exc
    except OSError as exc:
        raise RcloneError(f"Could not run ssh-keyscan: {exc}") from exc
    keys: list[tuple[str, str]] = []
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3 or line.startswith("#") or not _KEY_TYPE_RE.fullmatch(parts[1]):
            continue
        try:
            base64.b64decode(parts[2], validate=True)
        except ValueError:
            continue
        keys.append((parts[1], parts[2]))
    if not keys:
        raise RcloneError(f"{host} gave no SSH host key on port {port}.")
    return keys


def _confirmed_keys(pattern: str) -> list[tuple[str, str]]:
    path = known_hosts_path()
    if not path.is_file():
        return []
    keys = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 3 and pattern in parts[0].split(","):
            keys.append((parts[1], parts[2]))
    return keys


def _section_checks_host_keys() -> bool:
    return bool(_section_values().get("known_hosts_file"))


def host_key_status() -> str:
    """Read from the files, no network:

    - ``""``: not an SFTP remote.
    - ``"confirmed"``: rclone checks the host against a key the admin confirmed.
    - ``"unconfirmed"``: nobody has confirmed a key yet, so uploads are refused.
    - ``"unchecked"``: a remote set up before zsm checked host keys. It still
      uploads, unchecked, until its first Test asks for the key.
    """
    endpoint = sftp_endpoint()
    if endpoint is None:
        return ""
    if not _section_checks_host_keys():
        return "unchecked"
    if _confirmed_keys(known_hosts_pattern(*endpoint)):
        return "confirmed"
    return "unconfirmed"


def _describe(keys: list[tuple[str, str]]) -> list[dict]:
    return [{"type": kind, "fingerprint": key_fingerprint(blob)} for kind, blob in keys]


def check_host_key() -> dict | None:
    """Compare what the SFTP host offers now with what the admin confirmed.

    None for a remote that is not SFTP. Otherwise ``state`` is:

    - ``known``: every key the host offers is confirmed.
    - ``unknown``: first contact, or the host offers a key type nobody
      confirmed beside one that is. ``keys`` lists the unconfirmed ones.
    - ``changed``: a key differs from the confirmed one of its type, or none
      of the confirmed keys is offered any more. Refused; ``keys`` is what the
      host offers now and ``confirmed`` what was confirmed.

    Raises ``RcloneError`` when the host's key cannot be read.
    """
    endpoint = sftp_endpoint()
    if endpoint is None:
        return None
    return _check(*endpoint)[0]


def _check(host: str, port: int) -> tuple[dict, list[tuple[str, str]]]:
    """``check_host_key``'s answer, plus the unconfirmed keys themselves."""
    pattern = known_hosts_pattern(host, port)
    offered = scan_host_keys(host, port)
    confirmed = _confirmed_keys(pattern)
    confirmed_set = set(confirmed)
    confirmed_types = {kind for kind, _ in confirmed}
    result = {"host": pattern, "known_hosts_file": str(known_hosts_path())}

    differs = [k for k in offered if k[0] in confirmed_types and k not in confirmed_set]
    if confirmed and (differs or not confirmed_set.intersection(offered)):
        return {
            **result,
            "state": "changed",
            "keys": _describe(offered),
            "confirmed": _describe(confirmed),
            "message": (
                f"Refused: the SSH host key of {pattern} is not the one you confirmed. "
                "Someone may be intercepting the connection, or the server was "
                "reinstalled. Nothing was uploaded. If you know why it changed, "
                "remove the offsite target and set it up again, which asks you to "
                "confirm the new key."
            ),
        }, []
    new = [k for k in offered if k not in confirmed_set]
    if new:
        first = (
            f"First connection to {pattern}: confirm its host key."
            if not confirmed
            else f"{pattern} also offers a host key you have not confirmed."
        )
        return {
            **result,
            "state": "unknown",
            "keys": _describe(new),
            "message": (
                f"{first} Compare the fingerprint with what `ssh-keygen -lf <key>.pub` "
                "prints for the server's /etc/ssh/ssh_host_*_key.pub, or with what "
                "your provider shows. Zedstead saves it only when you confirm."
            ),
        }, new
    return {**result, "state": "known", "keys": _describe(offered), "message": ""}, []


def _ensure_section_checks_host_keys() -> None:
    """Add ``known_hosts_file`` to an SFTP section written before zsm set it."""
    path = rclone_config_path()
    if not path.is_file() or _section_checks_host_keys():
        return
    out: list[str] = []
    for name, block in _split_sections(path.read_text(encoding="utf-8")):
        if name == REMOTE_NAME:
            body = list(block)
            trailing: list[str] = []
            while body and not body[-1].strip():
                trailing.insert(0, body.pop())
            body.append(f"known_hosts_file = {known_hosts_path()}")
            block = body + trailing
        out.extend(block)
    _write_owner_only(path, "\n".join(out).rstrip("\n") + "\n")


def trust_host_keys(fingerprints: Iterable[str]) -> dict:
    """Save the host keys the admin confirmed, after asking the host again.

    *fingerprints* must be exactly the ``unknown`` keys the host offers now;
    anything else is refused, so a key that changed between showing and
    confirming is never the one saved. A ``changed`` key is refused too: it is
    only replaced by removing the remote and setting it up again.
    """
    endpoint = sftp_endpoint()
    if endpoint is None:
        raise RcloneError("No SFTP offsite target configured.")
    check, new = _check(*endpoint)
    if check["state"] == "changed":
        raise RcloneError(check["message"])
    if check["state"] == "unknown":
        offered = {k["fingerprint"] for k in check["keys"]}
        if set(fingerprints) != offered:
            raise RcloneError(
                "The host now offers a different key from the one you confirmed. "
                "Nothing was saved; test again and compare the fingerprint."
            )
        pattern = check["host"]
        path = known_hosts_path()
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        lines = [line for line in existing.splitlines() if line.strip()]
        lines += [f"{pattern} {kind} {blob}" for kind, blob in new]
        _write_owner_only(path, "\n".join(lines) + "\n")
    _ensure_section_checks_host_keys()
    return {**check, "state": "known", "message": ""}


# ---------------------------------------------------------------------------
# Running rclone
# ---------------------------------------------------------------------------


def obscure(config, value: str) -> str:
    """rclone's own obfuscation for a config password.

    Not encryption — rclone documents it as reversible — but its config
    parser expects the obscured form, and writing a plain password there
    makes rclone warn on every run.

    The value goes in on stdin (``rclone obscure -`` reads its first line), so
    it is never on the command line: not in the process table, and a value
    starting with ``-`` is not read as a flag (plans/68 INJ-7).
    """
    binary = ensure_rclone(config)
    try:
        proc = subprocess.run(
            [str(binary), "obscure", "-"],
            input=value + "\n",
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        # A fixed message, and no chained exception: what rclone printed
        # before it was killed may be the encoded password (plans/68 SEC-10).
        raise RcloneError(
            f"rclone did not encode the password within {PROBE_TIMEOUT:g} seconds."
        ) from None
    except OSError as exc:
        raise RcloneError(f"Could not run rclone: {exc}") from exc
    if proc.returncode != 0:
        raise RcloneError("rclone could not encode the password.")
    return proc.stdout.strip()


def _base_argv(binary: Path) -> list[str]:
    return [str(binary), "--config", str(rclone_config_path())]


def remote_target(config, filename: str | None = None) -> str:
    """``zsm-offsite:<path>[/<filename>]``, the rclone destination."""
    prefix = (getattr(config, "backup_remote_path", "") or "").strip().strip("/")
    parts = [p for p in (prefix, filename) if p]
    return f"{REMOTE_NAME}:" + "/".join(parts)


def _run_streaming(
    argv: list[str],
    log: Callable[[str], None] | None,
    should_cancel: Callable[[], bool] | None,
) -> int:
    """Run *argv*, feeding each output line to *log*, honouring a cancel.

    The cancel is checked per line rather than with a watcher task because
    this is sync code shared with the CLI — rclone's ``--stats`` output gives
    it a heartbeat to check on even when a single file is still uploading.
    """
    emit = log or (lambda _line: None)
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        raise RcloneError(f"Could not run rclone: {exc}") from exc

    assert proc.stdout is not None
    cancelled = False
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        if line:
            emit(line)
        if should_cancel is not None and should_cancel() and not cancelled:
            cancelled = True
            emit("Cancelling — stopping rclone.")
            proc.terminate()
    proc.wait()
    if cancelled:
        raise RcloneError("Upload cancelled.")
    return proc.returncode


def upload(
    config,
    local_path: Path,
    *,
    log: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> str:
    """Copy one file to the configured remote. Returns the destination.

    ``copyto``, not ``copy``: ``copy`` treats its destination as a directory
    and would need the name appended anyway, and ``copyto`` is explicit that
    one file lands at one name — which is what makes re-running it idempotent
    rather than nesting directories.
    """
    emit = log or (lambda _line: None)
    if not local_path.is_file():
        raise RcloneError(f"Nothing to upload: {local_path}")
    kind = read_remote_kind()
    if not kind:
        raise RcloneError(
            "No offsite target configured. Set one up on the Backups page or "
            "with `zsm backup remote set`."
        )
    _guard_host_key(emit)
    warning = endpoint_warning()
    if warning:
        emit(warning)

    binary = ensure_rclone(config, log=emit)
    destination = remote_target(config, local_path.name)
    size_mb = local_path.stat().st_size / (1024 * 1024)
    emit(f"Uploading {local_path.name} ({size_mb:.1f} MB) to {destination} [{kind}]")

    argv = _base_argv(binary) + [
        "copyto",
        str(local_path),
        destination,
        # One line per interval instead of rclone's multi-line block: the job
        # log is a flat list of lines, and the block form redraws itself with
        # terminal escapes that render as noise there.
        "--stats=5s",
        "--stats-one-line",
        "--stats-log-level=NOTICE",
    ]
    code = _run_streaming(argv, emit, should_cancel)
    if code != 0:
        raise RcloneError(f"rclone exited {code} — the backup was not uploaded.")
    emit(f"Uploaded to {destination}")
    return destination


_HOW_TO_CONFIRM = (
    "Press Test connection on the Backups page's Offsite card, or run "
    "`zsm backup remote test`, and confirm the host's key."
)


def _guard_host_key(emit: Callable[[str], None]) -> None:
    """Before an SFTP upload: refuse a host whose key nobody confirmed.

    A remote written before zsm checked host keys has no ``known_hosts_file``
    and keeps uploading as it did, with a warning in the log, until its first
    Test asks for the key. Refusing it would stop every scheduled offsite copy
    on the day zsm is updated.
    """
    status = host_key_status()
    if status in ("", "confirmed"):
        return
    if status == "unconfirmed":
        raise RcloneError("The SFTP host's key is not confirmed yet. " + _HOW_TO_CONFIRM)
    emit(
        "Warning: this SFTP target was set up before Zedstead checked host keys, so "
        "nothing checks which server receives the backup. " + _HOW_TO_CONFIRM
    )


def probe_remote(config, *, log: Callable[[str], None] | None = None) -> dict:
    """Probe the remote: ``{"ok", "message"}``, plus ``host_key`` for SFTP
    (``check_host_key``'s answer). Never raises.

    An SFTP host whose key is not confirmed is not listed: the answer is the
    key to confirm (``state: "unknown"``) or the refusal (``"changed"``).
    """
    if not read_remote_kind():
        return {"ok": False, "message": "No offsite target configured."}
    host_key = None
    if sftp_endpoint() is not None:
        try:
            host_key = check_host_key()
        except RcloneError as exc:
            return {"ok": False, "message": str(exc)}
        if host_key is not None and host_key["state"] != "known":
            return {"ok": False, "message": host_key["message"], "host_key": host_key}
        # Confirmed already, so a remote written before NET-4 starts checking.
        _ensure_section_checks_host_keys()
    ok, message = _list_remote(config, log)
    out = {"ok": ok, "message": message}
    if host_key is not None:
        out["host_key"] = host_key
    return out


def test_remote(
    config, *, log: Callable[[str], None] | None = None
) -> tuple[bool, str]:
    """``probe_remote`` as ``(ok, message)``. Never raises."""
    result = probe_remote(config, log=log)
    return result["ok"], result["message"]


def _list_remote(config, log: Callable[[str], None] | None) -> tuple[bool, str]:
    """``rclone lsd`` the target.

    ``lsd`` (list directories) rather than ``about``: not every backend
    implements ``about``, and a "command not supported" answer from a working
    remote would read as a failure.
    """
    emit = log or (lambda _line: None)
    try:
        binary = ensure_rclone(config, log=emit)
        target = remote_target(config)
        proc = subprocess.run(
            _base_argv(binary) + ["lsd", target, "--retries=1"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT,
        )
    except RcloneError as exc:
        return False, str(exc)
    except subprocess.TimeoutExpired:
        return False, f"The remote did not answer within {PROBE_TIMEOUT:.0f}s."
    except OSError as exc:
        return False, f"Could not run rclone: {exc}"

    if proc.returncode == 0:
        return True, f"Connected to {remote_target(config)}."
    detail = (proc.stderr or proc.stdout or "").strip().splitlines()
    return False, detail[-1] if detail else f"rclone exited {proc.returncode}."


def upload_candidates(paths: Iterable[Path]) -> list[Path]:
    """The files worth pushing offsite, in the order given.

    A quick backup is a zip plus a ``.meta.json`` sidecar, and the sidecar is
    what makes the zip *listable* again (see
    ``app/web/CLAUDE.md``) — so both go, or the offsite copy restores but
    never shows up in a list.
    """
    out: list[Path] = []
    for path in paths:
        if not path.is_file():
            continue
        out.append(path)
        if path.suffix == ".zip":
            sidecar = path.parent / f"{path.stem}.meta.json"
            if sidecar.is_file():
                out.append(sidecar)
    return out


def push_backup(
    config,
    zip_path: Path,
    *,
    log: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[str]:
    """Upload a backup zip **and its sidecar**. Returns the destinations."""
    destinations = []
    for path in upload_candidates([zip_path]):
        destinations.append(upload(config, path, log=log, should_cancel=should_cancel))
    return destinations


def auto_push(
    config,
    zip_path: Path | None,
    *,
    log: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict:
    """Push a just-created backup offsite, if that is configured.

    **Never raises.** The backup itself already succeeded and is on disk, so a
    remote that is down must not turn a good backup into a failed job — that
    would invite the admin to re-run it and lose nothing but time. It must not
    be *quiet* either, which is the failure mode the whole feature exists to
    prevent, so the error goes in the log **and** in the returned dict for the
    caller to surface. Returns ``{"uploaded": [...], "error": str | None}``;
    both empty when no remote is configured or auto-upload is off.
    """
    emit = log or (lambda _line: None)
    if zip_path is None or not getattr(config, "backup_remote_auto", False):
        return {"uploaded": [], "error": None}
    if not read_remote_kind():
        return {"uploaded": [], "error": None}

    try:
        return {"uploaded": push_backup(config, zip_path, log=emit, should_cancel=should_cancel), "error": None}
    except RcloneError as exc:
        emit(f"Offsite upload FAILED: {exc}")
        emit("The backup is still on this machine — push it later from the Backups page.")
        return {"uploaded": [], "error": str(exc)}
    except Exception as exc:  # a transport is third-party; don't fail the backup
        emit(f"Offsite upload FAILED: {exc}")
        return {"uploaded": [], "error": str(exc)}
