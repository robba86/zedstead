"""Tests for app/ops/offsite_ops.py — the offsite backup transport (TODO C1).

**No test here downloads rclone or runs one.** The binary is found through
``find_rclone``, which every test points at a stub file or a fake ``subprocess``;
the config writer and the section parser are pure and exercised for real.
"""

from __future__ import annotations

import base64
import stat
import subprocess
import zipfile

import pytest

from app.ops import offsite_ops
from app.ops.offsite_ops import RcloneError


@pytest.fixture()
def cfg_dir(tmp_path, monkeypatch):
    """Point CONFIG_DIR at a tmp dir — rclone.conf must never hit the real one."""
    d = tmp_path / "config"
    d.mkdir()
    monkeypatch.setattr("app.shared.paths.CONFIG_DIR", d)
    return d


@pytest.fixture()
def fake_rclone(cfg_dir, monkeypatch):
    """A stub binary on the configured path, so find_rclone succeeds."""
    binary = cfg_dir / "fake-rclone"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    from app.shared.config import DayzConfig

    return DayzConfig(
        server_dir=str(cfg_dir / "server"),
        steamcmd_dir=str(cfg_dir / "steamcmd"),
        steam_username="alice",
        rclone_path=str(binary),
    )


def _blob(kind: bytes, tag: bytes) -> str:
    """A base64 key blob. Not a real key: zsm only ever hashes its bytes."""
    return base64.b64encode(b"\x00\x00\x00" + bytes([len(kind)]) + kind + tag).decode()


KEY_A = ("ssh-ed25519", _blob(b"ssh-ed25519", b"key A"))
KEY_B = ("ssh-ed25519", _blob(b"ssh-ed25519", b"key B"))
KEY_RSA = ("ssh-rsa", _blob(b"ssh-rsa", b"rsa key"))


def _fp(key: tuple[str, str]) -> str:
    return offsite_ops.key_fingerprint(key[1])


@pytest.fixture()
def ssh_host(monkeypatch):
    """The SFTP host, faked at the one boundary zsm reaches it through:
    ``scan_host_keys``. Set ``keys`` to what it offers now."""

    class _Host:
        def __init__(self):
            self.keys = [KEY_A]
            self.scans: list[tuple[str, int]] = []

        def scan(self, hostname, port):
            self.scans.append((hostname, port))
            return list(self.keys)

    host = _Host()
    monkeypatch.setattr(offsite_ops, "scan_host_keys", host.scan)
    return host


def _confirmed_sftp(config, ssh_host, **fields) -> None:
    """An SFTP target whose host key the admin has confirmed."""
    offsite_ops.write_remote(config, "sftp", {"host": "h", **fields})
    offsite_ops.trust_host_keys([_fp(k) for k in ssh_host.keys])


# ---------------------------------------------------------------------------
# Finding the binary
# ---------------------------------------------------------------------------


def test_explicit_path_wins_over_path_lookup(fake_rclone, monkeypatch):
    monkeypatch.setattr(offsite_ops.shutil, "which", lambda _n: "/usr/bin/rclone")
    assert str(offsite_ops.find_rclone(fake_rclone)) == fake_rclone.rclone_path


def test_a_wrong_explicit_path_is_not_silently_replaced(fake_rclone, monkeypatch):
    """A typo must not turn into a surprise download of a different binary."""
    fake_rclone.rclone_path = "/nope/rclone"
    monkeypatch.setattr(offsite_ops.shutil, "which", lambda _n: "/usr/bin/rclone")
    assert offsite_ops.find_rclone(fake_rclone) is None
    with pytest.raises(RcloneError, match="rclone_path points at"):
        offsite_ops.ensure_rclone(fake_rclone)


def test_falls_back_to_the_downloaded_copy(cfg_dir, monkeypatch):
    monkeypatch.setattr(offsite_ops.shutil, "which", lambda _n: None)
    assert offsite_ops.find_rclone(None) is None
    target = offsite_ops.rclone_binary_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x")
    assert offsite_ops.find_rclone(None) == target


def test_download_url_names_a_real_looking_build():
    url = offsite_ops.download_url()
    version = offsite_ops.RCLONE_VERSION
    assert url.startswith(f"https://downloads.rclone.org/{version}/rclone-{version}-")
    assert url.endswith(".zip")


def _release_zip(binary: bytes = b"BINARY") -> bytes:
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("rclone-v1.99.0-linux-amd64/rclone", binary)
        zf.writestr("rclone-v1.99.0-linux-amd64/rclone.exe", binary)
    return buf.getvalue()


@pytest.fixture()
def rclone_site(cfg_dir, monkeypatch):
    """downloads.rclone.org, faked at ``requests.get``: nothing is fetched."""
    import hashlib

    class _Resp:
        def __init__(self, body: bytes):
            self.body = body

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=1):
            for i in range(0, len(self.body), chunk_size):
                yield self.body[i : i + chunk_size]

    class _Site:
        def __init__(self):
            self.zip = _release_zip()
            self.urls: list[str] = []

        def get(self, url, **_kwargs):
            self.urls.append(url)
            return _Resp(self.zip)

    site = _Site()
    filename = offsite_ops.download_url().rsplit("/", 1)[-1]
    # The pinned table holds the real release's hashes; this zip is a stand-in.
    monkeypatch.setitem(offsite_ops.RCLONE_SHA256, filename, hashlib.sha256(site.zip).hexdigest())
    monkeypatch.setattr("requests.get", site.get)
    monkeypatch.setattr(offsite_ops.shutil, "which", lambda _n: None)
    return site


def test_the_download_is_checked_against_the_pinned_sha256(rclone_site):
    path = offsite_ops.ensure_rclone(None)
    assert path.read_bytes() == b"BINARY"
    # Only the zip is fetched: the hash it is checked against is not the host's to give.
    assert rclone_site.urls == [offsite_ops.download_url()]


def test_a_download_that_does_not_match_its_sha256_is_refused(rclone_site):
    rclone_site.zip = _release_zip(b"SOMETHING ELSE")
    with pytest.raises(RcloneError, match="does not match the sha256"):
        offsite_ops.ensure_rclone(None)
    assert not offsite_ops.rclone_binary_path().exists()
    assert not (offsite_ops.rclone_binary_path().parent / "rclone-download.zip").exists()


def test_a_build_with_no_pinned_sha256_is_refused_before_any_download(rclone_site, monkeypatch):
    filename = offsite_ops.download_url().rsplit("/", 1)[-1]
    monkeypatch.delitem(offsite_ops.RCLONE_SHA256, filename)
    with pytest.raises(RcloneError, match="no checked sha256"):
        offsite_ops.ensure_rclone(None)
    assert not offsite_ops.rclone_binary_path().exists()
    assert rclone_site.urls == []


@pytest.mark.parametrize(
    "system, machine",
    [
        (system, machine)
        for system in ("Linux", "Windows", "Darwin")
        for machine in ("x86_64", "aarch64", "i686")
        if not (system == "Darwin" and machine == "i686")
    ]
    + [("Linux", "armv7l")],
)
def test_every_build_zsm_downloads_has_a_pinned_sha256(monkeypatch, system, machine):
    monkeypatch.setattr(offsite_ops.platform, "system", lambda: system)
    monkeypatch.setattr(offsite_ops.platform, "machine", lambda: machine)
    filename = offsite_ops.download_url().rsplit("/", 1)[-1]
    assert filename in offsite_ops.RCLONE_SHA256


def test_the_sha256_table_is_the_pinned_version_and_well_formed():
    for filename, digest in offsite_ops.RCLONE_SHA256.items():
        assert filename.startswith(f"rclone-{offsite_ops.RCLONE_VERSION}-")
        assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)


def test_unknown_architecture_says_to_install_rclone(monkeypatch):
    monkeypatch.setattr(offsite_ops.platform, "machine", lambda: "sparc64")
    with pytest.raises(RcloneError, match="rclone_path"):
        offsite_ops.download_url()


def test_extract_finds_the_binary_under_a_versioned_directory(tmp_path):
    """`-current` means the top directory's version changes without notice."""
    archive = tmp_path / "rclone.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("rclone-v1.99.0-linux-amd64/README.txt", "docs")
        zf.writestr("rclone-v1.99.0-linux-amd64/rclone", "BINARY")
    target = tmp_path / "rclone"
    offsite_ops._extract_binary(archive, target)
    assert target.read_text() == "BINARY"


def test_extract_reports_an_archive_with_no_binary(tmp_path):
    archive = tmp_path / "rclone.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("rclone-v1.99.0/README.txt", "docs")
    with pytest.raises(RcloneError, match="contained no"):
        offsite_ops._extract_binary(archive, tmp_path / "rclone")


# ---------------------------------------------------------------------------
# rclone.conf
# ---------------------------------------------------------------------------


def test_write_remote_writes_only_the_given_fields(fake_rclone):
    path = offsite_ops.write_remote(
        fake_rclone, "sftp", {"host": "backup.example.com", "user": "dayz"}
    )
    text = path.read_text()
    assert "[zsm-offsite]" in text
    assert "type = sftp" in text
    assert "host = backup.example.com" in text
    # No key_file and no pass: that is how "use ssh-agent and ~/.ssh" is said.
    assert "key_file" not in text
    assert "pass" not in text


def test_write_remote_drops_unknown_keys(fake_rclone):
    """The payload comes from a web form; an arbitrary key is an rclone option."""
    path = offsite_ops.write_remote(
        fake_rclone, "sftp", {"host": "h", "shell_type": "unix", "../evil": "x"}
    )
    text = path.read_text()
    assert "shell_type" not in text
    assert "evil" not in text


@pytest.mark.parametrize(
    "value",
    ["h\nextra_key = 1", "h\rextra_key = 1", "h\x00x", "h\u2028extra_key = 1", "h\x0bextra_key = 1"],
)
def test_write_remote_refuses_a_value_that_is_not_one_line(fake_rclone, value):
    """A line break inside an allowed field would add a key of its own."""
    path = offsite_ops.rclone_config_path()
    with pytest.raises(RcloneError, match="host field must be a single line") as err:
        offsite_ops.write_remote(fake_rclone, "sftp", {"host": value})
    assert "extra_key" not in str(err.value)  # the value is never echoed
    assert not path.exists() or "extra_key" not in path.read_text()


def test_a_value_no_file_can_hold_leaves_rclone_conf_as_it_was(fake_rclone):
    """The file used to be opened, emptied and only then encoded: a lone
    surrogate in a field left 0 bytes, the admin's own remotes gone."""
    path = offsite_ops.rclone_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    mine = "[mine]\ntype = s3\naccess_key_id = AKIA\n"
    path.write_text(mine, encoding="utf-8")
    with pytest.raises(RcloneError, match="host field must be a single line"):
        offsite_ops.write_remote(fake_rclone, "sftp", {"host": "h\ud83d"})
    with pytest.raises(UnicodeEncodeError):
        offsite_ops._write_owner_only(path, "[x]\nhost = h\ud83d\n")
    assert path.read_text(encoding="utf-8") == mine
    assert [f.name for f in path.parent.iterdir() if f.name.startswith("rclone.conf")] == ["rclone.conf"]


def test_a_separator_in_another_section_is_not_turned_into_a_line(fake_rclone):
    """rclone reads the file by "\\n"; a rewrite must not split anywhere else."""
    path = offsite_ops.rclone_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[mine]\ntype = sftp\nhost = h\u2028extra_key = 1\n", encoding="utf-8")
    offsite_ops.write_remote(fake_rclone, "b2", {"account": "acc", "key": "k"})
    lines = path.read_text(encoding="utf-8").split("\n")
    assert "host = h\u2028extra_key = 1" in lines
    assert "extra_key = 1" not in lines


def test_write_remote_preserves_other_sections(fake_rclone):
    path = offsite_ops.rclone_config_path()
    path.write_text(
        "[mine]\ntype = drive\n# a comment I wrote\ntoken = secret\n\n"
        "[zsm-offsite]\ntype = sftp\nhost = old.example.com\n",
        encoding="utf-8",
    )
    offsite_ops.write_remote(fake_rclone, "b2", {"account": "acc", "key": "k"})

    text = path.read_text()
    assert "[mine]" in text
    assert "# a comment I wrote" in text
    assert "token = secret" in text
    assert "old.example.com" not in text
    assert text.count("[zsm-offsite]") == 1
    assert "type = b2" in text


def test_write_remote_is_owner_only(fake_rclone):
    path = offsite_ops.write_remote(fake_rclone, "b2", {"account": "a", "key": "k"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_write_remote_obscures_an_sftp_password(fake_rclone, monkeypatch):
    calls: list[list[str]] = []

    def _run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="OBSCURED\n", stderr="")

    monkeypatch.setattr(offsite_ops.subprocess, "run", _run)
    path = offsite_ops.write_remote(
        fake_rclone, "sftp", {"host": "h", "user": "u", "pass": "hunter2"}
    )
    assert "pass = OBSCURED" in path.read_text()
    assert "hunter2" not in path.read_text()
    assert calls and calls[0][1:] == ["obscure", "-"]


@pytest.mark.parametrize("password", ["hunter2", "--config=/tmp/x", "-h"])
def test_the_password_goes_to_rclone_on_stdin_not_argv(fake_rclone, monkeypatch, password):
    """Off the command line: out of the process table, and never read as a flag."""
    calls: list[tuple[list[str], dict]] = []

    def _run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="OBSCURED\n", stderr="")

    monkeypatch.setattr(offsite_ops.subprocess, "run", _run)
    assert offsite_ops.obscure(fake_rclone, password) == "OBSCURED"
    argv, kwargs = calls[0]
    assert argv[1:] == ["obscure", "-"]
    assert not any(password in part for part in argv)
    assert kwargs["input"] == password + "\n"
    assert "stdin" not in kwargs


def test_an_obscure_timeout_raises_rclone_error_without_the_password(fake_rclone, monkeypatch):
    """The routes catch RcloneError only; a TimeoutExpired escaped to the log."""

    def _run(argv, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=argv + ["hunter2"], timeout=kwargs["timeout"], output="OBSCURED-hunter2"
        )

    monkeypatch.setattr(offsite_ops.subprocess, "run", _run)
    with pytest.raises(RcloneError, match="did not encode the password") as info:
        offsite_ops.write_remote(
            fake_rclone, "sftp", {"host": "h", "user": "u", "pass": "hunter2"}
        )
    assert "hunter2" not in str(info.value)
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__


@pytest.fixture()
def fake_obscure(monkeypatch):
    """`rclone obscure`, faked: returns OBSCURED(<value>) and counts calls."""
    calls: list[str] = []

    def _run(argv, **kwargs):
        calls.append(kwargs["input"])
        return subprocess.CompletedProcess(
            argv, 0, stdout=f"OBSCURED({kwargs['input'].strip()})\n", stderr=""
        )

    monkeypatch.setattr(offsite_ops.subprocess, "run", _run)
    return calls


def _sftp_section() -> dict[str, str]:
    return offsite_ops._section_values()


def test_a_blank_field_keeps_the_stored_value(fake_rclone, fake_obscure):
    """The form is blank after a reload; saving it again used to leave only
    `type`, the host, user, port, password and key path gone."""
    offsite_ops.write_remote(
        fake_rclone,
        "sftp",
        {"host": "h", "user": "u", "port": "2222", "key_file": "/k", "pass": "hunter2"},
    )
    offsite_ops.write_remote(fake_rclone, "sftp", {"user": "dayz", "host": "", "pass": ""})

    values = _sftp_section()
    assert values["host"] == "h"
    assert values["user"] == "dayz"
    assert values["port"] == "2222"
    assert values["key_file"] == "/k"
    # Kept as stored, never obscured a second time.
    assert values["pass"] == "OBSCURED(hunter2)"
    assert fake_obscure == ["hunter2\n"]
    assert values["known_hosts_file"] == str(offsite_ops.known_hosts_path())


def test_a_save_with_nothing_typed_leaves_rclone_conf_alone(fake_rclone):
    """Changing only the path or the auto switch must not rewrite the target,
    not even to add known_hosts_file to a section from before NET-4."""
    path = offsite_ops.rclone_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy = "[zsm-offsite]\ntype = sftp\nhost = h\nuser = u\n"
    path.write_text(legacy, encoding="utf-8")

    offsite_ops.write_remote(fake_rclone, "sftp", {"host": "", "user": " ", "pass": ""})

    assert path.read_text(encoding="utf-8") == legacy
    assert offsite_ops.host_key_status() == "unchecked"


def test_clear_removes_a_stored_field_and_only_a_known_one(fake_rclone):
    offsite_ops.write_remote(fake_rclone, "sftp", {"host": "h", "key_file": "/k"})
    offsite_ops.write_remote(fake_rclone, "sftp", {}, clear=["key_file", "type", "shell_type"])

    values = _sftp_section()
    assert "key_file" not in values
    assert values["type"] == "sftp"
    assert values["host"] == "h"
    assert "shell_type" not in values


def test_another_kind_replaces_the_section(fake_rclone, fake_obscure):
    offsite_ops.write_remote(fake_rclone, "sftp", {"host": "h", "user": "u", "pass": "pw"})
    offsite_ops.write_remote(fake_rclone, "s3", {"access_key_id": "A"})

    values = _sftp_section()
    assert values == {"type": "s3", "access_key_id": "A"}


def test_status_names_the_stored_fields_but_never_their_values(fake_rclone, fake_obscure):
    offsite_ops.write_remote(fake_rclone, "sftp", {"host": "h.example", "pass": "hunter2"})
    status = offsite_ops.remote_status(fake_rclone)

    assert status["set_fields"] == ["host", "pass"]
    assert "hunter2" not in repr(status)
    assert "h.example" not in repr(status)


def test_write_remote_rejects_an_unknown_kind(fake_rclone):
    with pytest.raises(RcloneError, match="Unsupported remote kind"):
        offsite_ops.write_remote(fake_rclone, "dropbox", {})


def test_read_remote_kind_and_clear(fake_rclone):
    assert offsite_ops.read_remote_kind() == ""
    offsite_ops.write_remote(fake_rclone, "s3", {"access_key_id": "A", "secret_access_key": "B"})
    assert offsite_ops.read_remote_kind() == "s3"
    assert offsite_ops.clear_remote() is True
    assert offsite_ops.read_remote_kind() == ""
    assert offsite_ops.clear_remote() is False


def test_status_never_carries_a_secret(fake_rclone):
    offsite_ops.write_remote(
        fake_rclone, "s3", {"access_key_id": "AKIA1", "secret_access_key": "topsecret"}
    )
    fake_rclone.backup_remote_path = "bucket/dayz"
    status = offsite_ops.remote_status(fake_rclone)

    assert status["kind"] == "s3"
    assert status["configured"] is True
    assert status["path"] == "bucket/dayz"
    assert status["binary_available"] is True
    assert "topsecret" not in repr(status)
    assert "AKIA1" not in repr(status)


# --- a plain-http S3 endpoint is warned about, never refused (plans/68 NET-15)


@pytest.mark.parametrize(
    ("kind", "fields", "warned"),
    [
        ("s3", {"endpoint": "http://minio.lan:9000"}, True),
        ("s3", {"endpoint": "HTTP://minio.lan:9000"}, True),
        ("s3", {"endpoint": "https://s3.example.com"}, False),
        ("s3", {}, False),
        ("sftp", {"host": "h"}, False),
    ],
)
def test_the_status_the_panel_shows_warns_about_an_http_endpoint(fake_rclone, kind, fields, warned):
    offsite_ops.write_remote(fake_rclone, kind, fields)
    warning = offsite_ops.remote_status(fake_rclone)["warning"]
    assert ("http://" in warning and "unencrypted" in warning) if warned else warning == ""


def test_the_offsite_card_shows_the_warning():
    """The status has carried it since NET-15; the card showed nothing.
    There is no frontend test harness, so this reads the source."""
    from pathlib import Path

    card = Path(__file__).resolve().parents[1] / "webui/src/components/OffsiteRemoteCard.jsx"
    source = card.read_text(encoding="utf-8")
    assert '<p className="banner banner-warning">{status.warning}</p>' in source


def test_an_upload_over_http_still_runs_and_says_so_in_its_log(fake_rclone, tmp_path, monkeypatch):
    offsite_ops.write_remote(fake_rclone, "s3", {"endpoint": "http://minio.lan:9000"})
    zip_path = tmp_path / "b.zip"
    zip_path.write_bytes(b"PK")
    monkeypatch.setattr(offsite_ops.subprocess, "Popen", lambda argv, **_k: _FakePopen([]))
    lines: list[str] = []

    offsite_ops.upload(fake_rclone, zip_path, log=lines.append)

    assert any("http://" in line and "unencrypted" in line for line in lines)
    assert lines[-1].startswith("Uploaded to ")


def test_cli_set_warns_about_an_http_endpoint_and_saves_it(fake_rclone, monkeypatch):
    from click.testing import CliRunner

    from app.commands.backup import backup_group
    from app.shared.config import DayzConfig

    monkeypatch.setattr(DayzConfig, "load", classmethod(lambda cls: fake_rclone))
    monkeypatch.setattr(DayzConfig, "save", lambda self: None)
    result = CliRunner().invoke(
        backup_group,
        ["remote", "set", "--kind", "s3", "--host", "http://minio.lan:9000", "--path", "b"],
        obj={},
    )

    assert result.exit_code == 0, result.output
    assert "unencrypted" in result.output
    assert offsite_ops.read_remote_kind() == "s3"


def test_cli_set_keeps_what_it_is_not_given_and_clears_on_request(fake_rclone, monkeypatch):
    from click.testing import CliRunner

    from app.commands.backup import backup_group
    from app.shared.config import DayzConfig

    monkeypatch.setattr(DayzConfig, "load", classmethod(lambda cls: fake_rclone))
    monkeypatch.setattr(DayzConfig, "save", lambda self: None)

    def _set(*args):
        result = CliRunner().invoke(backup_group, ["remote", "set", "--kind", "s3", *args], obj={})
        assert result.exit_code == 0, result.output

    _set("--provider", "AWS", "--access-key-id", "A", "--region", "eu")
    _set("--path", "bucket")
    assert offsite_ops._section_values() == {
        "type": "s3", "provider": "AWS", "access_key_id": "A", "region": "eu"
    }
    _set("--clear", "region")
    assert "region" not in offsite_ops._section_values()


def test_remote_target_joins_path_and_filename(fake_rclone):
    fake_rclone.backup_remote_path = "/dayz-backups/prod/"
    assert offsite_ops.remote_target(fake_rclone) == "zsm-offsite:dayz-backups/prod"
    assert (
        offsite_ops.remote_target(fake_rclone, "a.zip")
        == "zsm-offsite:dayz-backups/prod/a.zip"
    )


def test_remote_target_with_no_path_is_the_remote_root(fake_rclone):
    assert offsite_ops.remote_target(fake_rclone, "a.zip") == "zsm-offsite:a.zip"


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


class _FakePopen:
    def __init__(self, lines, returncode=0):
        self.stdout = iter(lines)
        self.returncode = returncode
        self.terminated = False

    def terminate(self):
        self.terminated = True

    def wait(self):
        return self.returncode


def test_upload_refuses_without_a_configured_remote(fake_rclone, tmp_path):
    zip_path = tmp_path / "backup.zip"
    zip_path.write_bytes(b"PK")
    with pytest.raises(RcloneError, match="No offsite target configured"):
        offsite_ops.upload(fake_rclone, zip_path)


def test_upload_refuses_a_missing_file(fake_rclone):
    offsite_ops.write_remote(fake_rclone, "sftp", {"host": "h"})
    with pytest.raises(RcloneError, match="Nothing to upload"):
        offsite_ops.upload(fake_rclone, offsite_ops.rclone_config_path().parent / "gone.zip")


def test_upload_runs_copyto_and_logs_every_line(fake_rclone, ssh_host, tmp_path, monkeypatch):
    _confirmed_sftp(fake_rclone, ssh_host, host="backup.example.com")
    fake_rclone.backup_remote_path = "dayz"
    zip_path = tmp_path / "dayz-backup-x.zip"
    zip_path.write_bytes(b"PK" * 100)

    seen: list[list[str]] = []

    def _popen(argv, **kwargs):
        seen.append(argv)
        return _FakePopen(["Transferred: 1 / 1\n", "\n"])

    monkeypatch.setattr(offsite_ops.subprocess, "Popen", _popen)

    lines: list[str] = []
    dest = offsite_ops.upload(fake_rclone, zip_path, log=lines.append)

    assert dest == "zsm-offsite:dayz/dayz-backup-x.zip"
    argv = seen[0]
    assert argv[1:3] == ["--config", str(offsite_ops.rclone_config_path())]
    assert argv[3] == "copyto"
    assert argv[4:6] == [str(zip_path), dest]
    # A flat job log cannot render rclone's redrawing multi-line stats block.
    assert "--stats-one-line" in argv
    assert "Transferred: 1 / 1" in lines
    assert lines[-1] == f"Uploaded to {dest}"
    # Blank lines are dropped rather than padding the job log.
    assert "" not in lines


def test_upload_reports_a_non_zero_exit(fake_rclone, ssh_host, tmp_path, monkeypatch):
    _confirmed_sftp(fake_rclone, ssh_host)
    zip_path = tmp_path / "b.zip"
    zip_path.write_bytes(b"PK")
    monkeypatch.setattr(
        offsite_ops.subprocess, "Popen", lambda *a, **k: _FakePopen(["boom\n"], returncode=1)
    )
    with pytest.raises(RcloneError, match="was not uploaded"):
        offsite_ops.upload(fake_rclone, zip_path)


def test_upload_cancel_terminates_rclone(fake_rclone, ssh_host, tmp_path, monkeypatch):
    _confirmed_sftp(fake_rclone, ssh_host)
    zip_path = tmp_path / "b.zip"
    zip_path.write_bytes(b"PK")
    fake = _FakePopen(["a\n", "b\n", "c\n"])
    monkeypatch.setattr(offsite_ops.subprocess, "Popen", lambda *a, **k: fake)

    with pytest.raises(RcloneError, match="cancelled"):
        offsite_ops.upload(fake_rclone, zip_path, should_cancel=lambda: True)
    assert fake.terminated is True


def test_test_remote_returns_a_message_instead_of_raising(fake_rclone, ssh_host, monkeypatch):
    assert offsite_ops.test_remote(fake_rclone) == (False, "No offsite target configured.")

    _confirmed_sftp(fake_rclone, ssh_host)
    monkeypatch.setattr(
        offsite_ops.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="", stderr="dial tcp: refused\n"),
    )
    ok, message = offsite_ops.test_remote(fake_rclone)
    assert ok is False
    assert "refused" in message


def test_test_remote_times_out_instead_of_waiting_on_rclones_retries(
    fake_rclone, ssh_host, monkeypatch
):
    _confirmed_sftp(fake_rclone, ssh_host)

    def _raise(*_a, **_k):
        raise subprocess.TimeoutExpired("rclone", offsite_ops.PROBE_TIMEOUT)

    monkeypatch.setattr(offsite_ops.subprocess, "run", _raise)
    ok, message = offsite_ops.test_remote(fake_rclone)
    assert ok is False
    assert "did not answer" in message


# ---------------------------------------------------------------------------
# SFTP host keys (plans/68 NET-4)
# ---------------------------------------------------------------------------


def _lsd_ok(monkeypatch) -> list[list[str]]:
    """rclone answers every lsd; returns the argv it was called with."""
    calls: list[list[str]] = []

    def _run(argv, **_k):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(offsite_ops.subprocess, "run", _run)
    return calls


def _no_upload(monkeypatch) -> None:
    def _popen(*_a, **_k):
        raise AssertionError("rclone must not run")

    monkeypatch.setattr(offsite_ops.subprocess, "Popen", _popen)


def test_key_fingerprint_is_what_ssh_keygen_prints():
    # `ssh-keygen -lf` on a throwaway key: SHA256, unpadded base64.
    blob = "AAAAC3NzaC1lZDI1NTE5AAAAIKTME9hVYL+mYt05CxfRVs/IZPPm2XrrZqjs6T8x5Km2"
    assert offsite_ops.key_fingerprint(blob) == "SHA256:QpLCxrWY/MucRqtszcT54pVg1zxQcvST0nO9IUXqDK8"


def test_an_sftp_remote_checks_host_keys_against_zsms_own_file(fake_rclone, cfg_dir):
    """rclone accepts any host key unless the section names a known_hosts file."""
    text = offsite_ops.write_remote(fake_rclone, "sftp", {"host": "h"}).read_text()
    assert f"known_hosts_file = {cfg_dir / 'offsite_known_hosts'}" in text

    text = offsite_ops.write_remote(fake_rclone, "b2", {"account": "a", "key": "k"}).read_text()
    assert "known_hosts_file" not in text


def test_first_contact_shows_the_fingerprint_and_saves_nothing(
    fake_rclone, ssh_host, tmp_path, monkeypatch
):
    offsite_ops.write_remote(fake_rclone, "sftp", {"host": "h"})
    lsd = _lsd_ok(monkeypatch)

    result = offsite_ops.probe_remote(fake_rclone)

    assert result["ok"] is False
    assert result["host_key"]["state"] == "unknown"
    assert result["host_key"]["keys"] == [{"type": "ssh-ed25519", "fingerprint": _fp(KEY_A)}]
    assert "confirm" in result["message"]
    assert not offsite_ops.known_hosts_path().exists()
    assert lsd == []  # the host is not listed before its key is confirmed
    assert offsite_ops.remote_status(fake_rclone)["host_key"] == "unconfirmed"

    zip_path = tmp_path / "b.zip"
    zip_path.write_bytes(b"PK")
    _no_upload(monkeypatch)
    with pytest.raises(RcloneError, match="not confirmed yet"):
        offsite_ops.upload(fake_rclone, zip_path)


def test_confirming_saves_the_key_and_the_next_test_connects(fake_rclone, ssh_host, monkeypatch):
    offsite_ops.write_remote(fake_rclone, "sftp", {"host": "h"})
    offsite_ops.trust_host_keys([_fp(KEY_A)])

    known = offsite_ops.known_hosts_path()
    assert known.read_text() == f"h ssh-ed25519 {KEY_A[1]}\n"
    assert stat.S_IMODE(known.stat().st_mode) == 0o600
    assert offsite_ops.remote_status(fake_rclone)["host_key"] == "confirmed"

    lsd = _lsd_ok(monkeypatch)
    result = offsite_ops.probe_remote(fake_rclone)
    assert result["ok"] is True
    assert result["host_key"]["state"] == "known"
    assert lsd and lsd[0][3] == "lsd"


def test_confirming_needs_the_fingerprint_the_host_offers_now(fake_rclone, ssh_host):
    offsite_ops.write_remote(fake_rclone, "sftp", {"host": "h"})
    shown = offsite_ops.check_host_key()["keys"]

    with pytest.raises(RcloneError, match="different key"):
        offsite_ops.trust_host_keys(["SHA256:somethingelse"])
    # The key changes between showing it and confirming it.
    ssh_host.keys = [KEY_B]
    with pytest.raises(RcloneError, match="different key"):
        offsite_ops.trust_host_keys([k["fingerprint"] for k in shown])

    assert not offsite_ops.known_hosts_path().exists()


def test_a_changed_key_is_refused_and_never_replaced(fake_rclone, ssh_host, monkeypatch):
    _confirmed_sftp(fake_rclone, ssh_host)
    before = offsite_ops.known_hosts_path().read_text()
    ssh_host.keys = [KEY_B]
    lsd = _lsd_ok(monkeypatch)

    result = offsite_ops.probe_remote(fake_rclone)
    assert result["ok"] is False
    assert result["host_key"]["state"] == "changed"
    assert result["host_key"]["keys"] == [{"type": "ssh-ed25519", "fingerprint": _fp(KEY_B)}]
    assert result["host_key"]["confirmed"] == [{"type": "ssh-ed25519", "fingerprint": _fp(KEY_A)}]
    assert result["message"].startswith("Refused")
    assert lsd == []

    with pytest.raises(RcloneError, match="Refused"):
        offsite_ops.trust_host_keys([_fp(KEY_B)])
    assert offsite_ops.known_hosts_path().read_text() == before


def test_a_new_key_type_beside_a_confirmed_one_is_asked_about(fake_rclone, ssh_host):
    _confirmed_sftp(fake_rclone, ssh_host)
    ssh_host.keys = [KEY_A, KEY_RSA]

    check = offsite_ops.check_host_key()
    assert check["state"] == "unknown"
    assert check["keys"] == [{"type": "ssh-rsa", "fingerprint": _fp(KEY_RSA)}]

    offsite_ops.trust_host_keys([_fp(KEY_RSA)])
    assert offsite_ops.check_host_key()["state"] == "known"
    assert offsite_ops.known_hosts_path().read_text().count("\nh ") == 1  # two lines


def test_a_port_other_than_22_is_written_the_way_known_hosts_reads_it(fake_rclone, ssh_host):
    _confirmed_sftp(fake_rclone, ssh_host, port="2222")
    assert ssh_host.scans[-1] == ("h", 2222)
    assert offsite_ops.known_hosts_path().read_text() == f"[h]:2222 ssh-ed25519 {KEY_A[1]}\n"


def test_a_remote_from_before_host_keys_still_uploads_and_says_so(
    fake_rclone, ssh_host, tmp_path, monkeypatch
):
    """Refusing it would stop every scheduled offsite copy the day zsm updates."""
    offsite_ops.rclone_config_path().write_text("[zsm-offsite]\ntype = sftp\nhost = h\n")
    assert offsite_ops.remote_status(fake_rclone)["host_key"] == "unchecked"
    zip_path = tmp_path / "b.zip"
    zip_path.write_bytes(b"PK")
    monkeypatch.setattr(offsite_ops.subprocess, "Popen", lambda *a, **k: _FakePopen([]))

    lines: list[str] = []
    offsite_ops.upload(fake_rclone, zip_path, log=lines.append)

    assert any("set up before Zedstead checked host keys" in line for line in lines), lines
    assert ssh_host.scans == []  # an upload never asks the host for its key


def test_confirming_a_remote_from_before_host_keys_starts_checking_them(
    fake_rclone, ssh_host, monkeypatch
):
    conf = offsite_ops.rclone_config_path()
    conf.write_text(
        "[mine]\ntype = drive\n\n[zsm-offsite]\ntype = sftp\nhost = h\nuser = dayz\n"
    )
    assert offsite_ops.probe_remote(fake_rclone)["host_key"]["state"] == "unknown"

    offsite_ops.trust_host_keys([_fp(KEY_A)])

    text = conf.read_text()
    assert "[mine]\ntype = drive\n" in text
    assert "user = dayz" in text
    assert f"known_hosts_file = {offsite_ops.known_hosts_path()}" in text
    assert offsite_ops.remote_status(fake_rclone)["host_key"] == "confirmed"


def test_clearing_the_remote_forgets_its_host_keys(fake_rclone, ssh_host):
    """Setting the remote up again is how a changed key is accepted on purpose."""
    _confirmed_sftp(fake_rclone, ssh_host)
    offsite_ops.clear_remote()
    assert not offsite_ops.known_hosts_path().exists()


def test_s3_and_b2_have_no_host_key(fake_rclone, monkeypatch):
    offsite_ops.write_remote(fake_rclone, "b2", {"account": "a", "key": "k"})
    _lsd_ok(monkeypatch)
    result = offsite_ops.probe_remote(fake_rclone)
    assert result == {"ok": True, "message": "Connected to zsm-offsite:."}
    assert offsite_ops.remote_status(fake_rclone)["host_key"] == ""


def test_scan_host_keys_reads_ssh_keyscan(monkeypatch):
    seen: list[list[str]] = []
    out = (
        "# h:2222 SSH-2.0-OpenSSH_9.6\n"
        f"[h]:2222 ssh-ed25519 {KEY_A[1]}\n"
        f"[h]:2222 ssh-rsa {KEY_RSA[1]}\n"
        "[h]:2222 bad key!type AAAA\n"
    )

    def _run(argv, **_k):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    monkeypatch.setattr(offsite_ops.shutil, "which", lambda _n: "/usr/bin/ssh-keyscan")
    monkeypatch.setattr(offsite_ops.subprocess, "run", _run)

    assert offsite_ops.scan_host_keys("h", 2222) == [KEY_A, KEY_RSA]
    # `--` keeps a host that starts with "-" from being read as an option.
    assert seen[0][1:] == ["-T", str(offsite_ops.KEYSCAN_TIMEOUT), "-p", "2222", "--", "h"]


def test_scan_host_keys_says_what_is_missing(monkeypatch):
    monkeypatch.setattr(offsite_ops.shutil, "which", lambda _n: None)
    with pytest.raises(RcloneError, match="ssh-keyscan"):
        offsite_ops.scan_host_keys("h", 22)

    monkeypatch.setattr(offsite_ops.shutil, "which", lambda _n: "/usr/bin/ssh-keyscan")
    monkeypatch.setattr(
        offsite_ops.subprocess,
        "run",
        lambda argv, **_k: subprocess.CompletedProcess(argv, 1, stdout="", stderr=""),
    )
    with pytest.raises(RcloneError, match="gave no SSH host key"):
        offsite_ops.scan_host_keys("h", 22)


# --- the CLI: `zsm backup remote test` -------------------------------------


@pytest.fixture()
def cli(fake_rclone, monkeypatch):
    from click.testing import CliRunner

    from app.commands.backup import backup_group
    from app.shared.config import DayzConfig

    monkeypatch.setattr(DayzConfig, "load", classmethod(lambda cls: fake_rclone))

    def invoke(*args, yes=False):
        return CliRunner().invoke(
            backup_group, ["remote", "test", *args], obj={"non_interactive": yes}
        )

    return invoke


def test_cli_test_asks_before_saving_a_host_key(cli, fake_rclone, ssh_host, monkeypatch):
    offsite_ops.write_remote(fake_rclone, "sftp", {"host": "h"})
    _lsd_ok(monkeypatch)
    asked: list[str] = []

    def _confirm(prompt, **kwargs):
        asked.append(prompt)
        assert kwargs["default"] is False
        return True

    monkeypatch.setattr("app.shared.cli_ui.confirm", _confirm)
    result = cli()

    assert result.exit_code == 0, result.output
    assert _fp(KEY_A) in result.output
    assert asked
    assert offsite_ops.remote_status(fake_rclone)["host_key"] == "confirmed"
    assert "Connected to" in result.output


def test_cli_test_saves_nothing_without_a_yes(cli, fake_rclone, ssh_host, monkeypatch):
    offsite_ops.write_remote(fake_rclone, "sftp", {"host": "h"})
    monkeypatch.setattr("app.shared.cli_ui.confirm", lambda *_a, **_k: False)

    result = cli()

    assert result.exit_code == 1
    assert "--accept-host-key" in result.output
    assert not offsite_ops.known_hosts_path().exists()


def test_cli_yes_never_trusts_a_host_key_by_itself(cli, fake_rclone, ssh_host):
    """--yes is "I mean it" for an action, not for a fingerprint nobody read."""
    offsite_ops.write_remote(fake_rclone, "sftp", {"host": "h"})
    result = cli(yes=True)
    assert result.exit_code == 1
    assert not offsite_ops.known_hosts_path().exists()


def test_cli_accept_host_key_takes_only_the_offered_fingerprint(
    cli, fake_rclone, ssh_host, monkeypatch
):
    offsite_ops.write_remote(fake_rclone, "sftp", {"host": "h"})
    _lsd_ok(monkeypatch)

    wrong = cli("--accept-host-key", _fp(KEY_B), yes=True)
    assert wrong.exit_code == 1
    assert not offsite_ops.known_hosts_path().exists()

    right = cli("--accept-host-key", _fp(KEY_A), yes=True)
    assert right.exit_code == 0, right.output
    assert offsite_ops.remote_status(fake_rclone)["host_key"] == "confirmed"


def test_cli_test_refuses_a_changed_key(cli, fake_rclone, ssh_host):
    _confirmed_sftp(fake_rclone, ssh_host)
    ssh_host.keys = [KEY_B]

    result = cli("--accept-host-key", _fp(KEY_B), yes=True)

    assert result.exit_code == 1
    assert "Refused" in result.output
    assert _fp(KEY_B) in result.output and _fp(KEY_A) in result.output
    assert KEY_B[1] not in offsite_ops.known_hosts_path().read_text()


# ---------------------------------------------------------------------------
# What gets pushed
# ---------------------------------------------------------------------------


def test_the_meta_sidecar_travels_with_the_zip(tmp_path):
    """Without it the offsite copy restores but never appears in a listing."""
    zip_path = tmp_path / "dayz-backup-x-20260921-010000.zip"
    zip_path.write_bytes(b"PK")
    meta = tmp_path / "dayz-backup-x-20260921-010000.meta.json"
    meta.write_text("{}")

    assert offsite_ops.upload_candidates([zip_path]) == [zip_path, meta]


def test_a_zip_with_no_sidecar_goes_alone(tmp_path):
    zip_path = tmp_path / "dayz-server-x-bundle-a.zip"
    zip_path.write_bytes(b"PK")
    assert offsite_ops.upload_candidates([zip_path]) == [zip_path]


def test_a_missing_file_is_skipped(tmp_path):
    assert offsite_ops.upload_candidates([tmp_path / "gone.zip"]) == []
