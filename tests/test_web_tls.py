"""Tests for `zsm web` TLS: cert generation (app/web/tls.py) and the CLI's
flag/config resolution (app/commands/web.py::_resolve_tls).

Nothing here starts a server — the helpers are the whole surface. Every test
that touches the cert store repoints ``app.shared.paths.CONFIG_DIR`` at
``tmp_path`` first; ``tls.tls_dir()`` reads it at call time for exactly this
reason, and a stray ``~/.config/zsm/tls`` after a run means that broke.
"""

import datetime
from pathlib import Path

import click
import pytest
from cryptography import x509

from app.shared.platform import IS_WINDOWS
from app.web import tls


@pytest.fixture()
def tls_home(tmp_path, monkeypatch) -> Path:
    """Point CONFIG_DIR at tmp_path so no test writes into the real config."""
    monkeypatch.setattr("app.shared.paths.CONFIG_DIR", tmp_path)
    return tmp_path


# --- certificate generation ------------------------------------------------


def test_ensure_self_signed_creates_pair(tls_home):
    cert, key, generated = tls.ensure_self_signed("127.0.0.1")
    assert generated is True
    assert cert.exists() and key.exists()
    assert cert.parent == tls_home / "tls"


def test_ensure_self_signed_is_idempotent(tls_home):
    cert, key, _ = tls.ensure_self_signed("127.0.0.1")
    mtimes = (cert.stat().st_mtime_ns, key.stat().st_mtime_ns)

    cert2, key2, generated = tls.ensure_self_signed("127.0.0.1")

    assert generated is False
    assert (cert2, key2) == (cert, key)
    assert (cert2.stat().st_mtime_ns, key2.stat().st_mtime_ns) == mtimes


def test_force_regenerates(tls_home):
    cert, _, _ = tls.ensure_self_signed("127.0.0.1")
    first = tls.fingerprint_sha256(cert)

    cert2, _, generated = tls.ensure_self_signed("127.0.0.1", force=True)

    assert generated is True
    assert tls.fingerprint_sha256(cert2) != first


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX permission bits are a no-op on Windows")
def test_key_permissions(tls_home):
    _, key, _ = tls.ensure_self_signed("127.0.0.1")
    assert key.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX permission bits are a no-op on Windows")
def test_the_key_is_owner_only_without_a_chmod_after_it(tls_home, monkeypatch):
    """It was written under the umask (0644) and chmodded a moment later; the
    file has to be created 0600, so no chmod is needed at all."""
    monkeypatch.setattr(tls.os, "chmod", lambda *a, **k: None)
    _, key, _ = tls.ensure_self_signed("127.0.0.1")
    assert key.stat().st_mode & 0o777 == 0o600


def test_san_includes_localhost_and_bind_ip(tls_home):
    cert_path, _, _ = tls.ensure_self_signed("192.0.2.10")
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value

    names = set(san.get_values_for_type(x509.DNSName))
    ips = {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}

    assert "localhost" in names
    assert "127.0.0.1" in ips
    assert "192.0.2.10" in ips


def test_san_skips_wildcard_bind():
    """0.0.0.0 is not an identity a client connects to — it must not be a SAN."""
    entries = tls.san_entries("0.0.0.0")
    assert "0.0.0.0" not in entries
    assert "localhost" in entries


def test_extra_sans_reach_the_certificate(tls_home):
    """--san covers what discovery cannot: a cloud floating IP and a DNS name."""
    cert_path, _, _ = tls.ensure_self_signed(
        "127.0.0.1", extra_sans=["203.0.113.10", "zsm.example.com"]
    )
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value

    assert "203.0.113.10" in {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}
    assert "zsm.example.com" in set(san.get_values_for_type(x509.DNSName))


def test_changing_the_san_set_regenerates(tls_home):
    """Without this, `--san` silently does nothing from the second run on: the
    existing pair is valid and unexpired, so it would just be reused. See
    plans/28 B2."""
    cert, _, _ = tls.ensure_self_signed("127.0.0.1")
    first = tls.fingerprint_sha256(cert)

    cert2, _, generated = tls.ensure_self_signed(
        "127.0.0.1", extra_sans=["203.0.113.10"]
    )
    assert generated is True
    assert tls.fingerprint_sha256(cert2) != first

    # ...and the new set is now the stable one — no regeneration on a re-run.
    _, _, again = tls.ensure_self_signed("127.0.0.1", extra_sans=["203.0.113.10"])
    assert again is False

    # Dropping it back off regenerates too; the old cert names an address the
    # operator has asked it not to.
    _, _, dropped = tls.ensure_self_signed("127.0.0.1")
    assert dropped is True


def _write_expired_cert(cert_path: Path, key_path: Path) -> None:
    """A syntactically valid certificate whose validity ended yesterday."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "expired")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=10))
        .not_valid_after(now - datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


def test_needs_regeneration_on_expiry(tmp_path):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    _write_expired_cert(cert, key)
    assert tls.needs_regeneration(cert, key) is True


def test_needs_regeneration_inside_renewal_window(tls_home, monkeypatch):
    """A cert with fewer than RENEW_WHEN_DAYS_LEFT days left is replaced
    before it lapses, so a long-running panel never fails on a date."""
    monkeypatch.setattr(tls, "VALIDITY_DAYS", tls.RENEW_WHEN_DAYS_LEFT - 1)
    cert, key, _ = tls.ensure_self_signed("127.0.0.1")
    assert tls.needs_regeneration(cert, key) is True

    monkeypatch.setattr(tls, "VALIDITY_DAYS", tls.RENEW_WHEN_DAYS_LEFT + 30)
    cert, key, generated = tls.ensure_self_signed("127.0.0.1")
    assert generated is True
    assert tls.needs_regeneration(cert, key) is False


def test_needs_regeneration_when_missing(tmp_path):
    assert tls.needs_regeneration(tmp_path / "nope.pem", tmp_path / "nope.key") is True


def test_needs_regeneration_on_unreadable_cert(tls_home):
    cert, key, _ = tls.ensure_self_signed("127.0.0.1")
    cert.write_text("not a certificate")
    assert tls.needs_regeneration(cert, key) is True


def test_fingerprint_format(tls_home):
    cert, _, _ = tls.ensure_self_signed("127.0.0.1")
    fp = tls.fingerprint_sha256(cert)
    parts = fp.split(":")
    assert len(parts) == 32
    assert all(len(p) == 2 and p == p.upper() for p in parts)
    int(fp.replace(":", ""), 16)  # all hex


def test_generated_cert_is_not_a_ca(tls_home):
    cert_path, _, _ = tls.ensure_self_signed("127.0.0.1")
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    bc = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    assert bc.ca is False
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert x509.oid.ExtendedKeyUsageOID.SERVER_AUTH in eku


def test_private_key_is_not_passphrase_protected(tls_home):
    """uvicorn cannot answer a passphrase prompt at startup — see plans/24."""
    _, key, _ = tls.ensure_self_signed("127.0.0.1")
    text = key.read_text()
    assert "ENCRYPTED" not in text
    assert text.startswith("-----BEGIN PRIVATE KEY-----")


# --- CLI resolution --------------------------------------------------------


@pytest.fixture()
def pem_pair(tmp_path, monkeypatch):
    """A real cert/key pair on disk, outside the CONFIG_DIR store."""
    monkeypatch.setattr("app.shared.paths.CONFIG_DIR", tmp_path / "cfg")
    cert = tmp_path / "my-cert.pem"
    key = tmp_path / "my-key.pem"
    tls.generate_self_signed(cert, key, "127.0.0.1")
    return cert, key


def _resolve(config, **kw):
    from app.commands.web import _resolve_tls

    opts = {
        "tls_cert": None,
        "tls_key": None,
        "self_signed": False,
        "force_new_cert": False,
        "host": "127.0.0.1",
        "san": (),
    }
    opts.update(kw)
    return _resolve_tls(
        config,
        opts["tls_cert"],
        opts["tls_key"],
        opts["self_signed"],
        opts["force_new_cert"],
        opts["host"],
        opts["san"],
    )


def test_resolve_tls_none_by_default(sample_config):
    assert _resolve(sample_config) == (None, None, "")


def test_resolve_tls_half_pair_raises(sample_config, pem_pair):
    cert, key = pem_pair
    with pytest.raises(click.UsageError):
        _resolve(sample_config, tls_cert=str(cert))
    with pytest.raises(click.UsageError):
        _resolve(sample_config, tls_key=str(key))


def test_resolve_tls_missing_file_raises(sample_config, tmp_path):
    """The anti-silent-downgrade test: a moved cert is an error, not HTTP."""
    with pytest.raises(click.UsageError):
        _resolve(
            sample_config,
            tls_cert=str(tmp_path / "gone.pem"),
            tls_key=str(tmp_path / "gone.key"),
        )


def test_resolve_tls_config_missing_file_raises(sample_config, tmp_path):
    sample_config.web_tls_cert = str(tmp_path / "gone.pem")
    sample_config.web_tls_key = str(tmp_path / "gone.key")
    with pytest.raises(click.UsageError):
        _resolve(sample_config)


def test_resolve_tls_config_half_pair_raises(sample_config, pem_pair):
    cert, _ = pem_pair
    sample_config.web_tls_cert = str(cert)
    sample_config.web_tls_key = ""
    with pytest.raises(click.UsageError):
        _resolve(sample_config)


def test_resolve_tls_uses_config_pair(sample_config, pem_pair):
    cert, key = pem_pair
    sample_config.web_tls_cert = str(cert)
    sample_config.web_tls_key = str(key)

    certfile, keyfile, note = _resolve(sample_config)

    assert (certfile, keyfile) == (str(cert), str(key))
    assert "config" in note


def test_resolve_tls_prefers_flags_over_config(sample_config, pem_pair, tmp_path):
    cfg_cert = tmp_path / "cfg-cert.pem"
    cfg_key = tmp_path / "cfg-key.pem"
    tls.generate_self_signed(cfg_cert, cfg_key, "127.0.0.1")
    sample_config.web_tls_cert = str(cfg_cert)
    sample_config.web_tls_key = str(cfg_key)

    cert, key = pem_pair
    certfile, keyfile, _ = _resolve(
        sample_config, tls_cert=str(cert), tls_key=str(key)
    )

    assert (certfile, keyfile) == (str(cert), str(key))


def test_resolve_tls_self_signed_beats_config(sample_config, pem_pair, tmp_path):
    cert, key = pem_pair
    sample_config.web_tls_cert = str(cert)
    sample_config.web_tls_key = str(key)

    certfile, keyfile, note = _resolve(sample_config, self_signed=True)

    assert Path(certfile).parent == tmp_path / "cfg" / "tls"
    assert Path(keyfile).exists()
    assert "fingerprint" in note.lower()


def test_resolve_tls_self_signed_rejects_explicit_paths(sample_config, pem_pair):
    cert, key = pem_pair
    with pytest.raises(click.UsageError):
        _resolve(
            sample_config, self_signed=True, tls_cert=str(cert), tls_key=str(key)
        )


def test_resolve_tls_force_without_self_signed_raises(sample_config):
    with pytest.raises(click.UsageError):
        _resolve(sample_config, force_new_cert=True)


def test_self_signed_banner_distinguishes_renewal(sample_config, tls_home, monkeypatch):
    """Renewal day used to print the first-run line word for word.

    Under tmux/nohup nobody sees either, and the browser warning arrives ~795
    days later looking exactly like a MITM — the one signal self-signed
    depends on. See plans/28 E2.
    """
    _, _, first = _resolve(sample_config, self_signed=True)
    assert "(generated)" in first

    _, _, second = _resolve(sample_config, self_signed=True)
    assert "(reused)" in second

    # Shorten the validity so the existing cert lands inside the renewal
    # window — the real renewal path, not --force-new-cert's. Same technique as
    # test_needs_regeneration_inside_renewal_window.
    monkeypatch.setattr(tls, "RENEW_WHEN_DAYS_LEFT", tls.VALIDITY_DAYS + 1)
    _, _, third = _resolve(sample_config, self_signed=True)
    assert "renewed" in third
    assert "accept it again" in third


def test_self_signed_banner_names_a_san_change_as_such(sample_config, tls_home):
    """Adding --san must not report itself as "renewed".

    Every non-forced regeneration used to fall into the renewal bucket, so the
    banner told the operator the certificate had aged out when in fact they had
    just changed the address list. Measured on a VM, 2026 Q3.
    """
    _resolve(sample_config, self_signed=True)

    _, _, note = _resolve(sample_config, self_signed=True, san=("203.0.113.10",))

    assert "names/IPs it covers changed" in note
    assert "renewed" not in note
    assert "accept it again" in note


def test_self_signed_banner_says_regenerated_on_force(sample_config, tls_home):
    _resolve(sample_config, self_signed=True)
    _, _, note = _resolve(sample_config, self_signed=True, force_new_cert=True)
    assert "regenerated" in note
    assert "accept it again" in note


def test_resolve_tls_mismatched_pair_raises(sample_config, pem_pair, tmp_path):
    """A mismatched pair is the common real mistake, and uvicorn's answer to it
    is the startup banner followed by a bare `ssl.SSLError: [SSL] PEM lib`
    traceback naming neither file. See plans/28 E1."""
    cert, _ = pem_pair
    other_cert = tmp_path / "other-cert.pem"
    other_key = tmp_path / "other-key.pem"
    tls.generate_self_signed(other_cert, other_key, "127.0.0.1")

    with pytest.raises(click.UsageError, match="does not match"):
        _resolve(sample_config, tls_cert=str(cert), tls_key=str(other_key))


def test_resolve_tls_unparseable_pem_raises(sample_config, tmp_path):
    junk_cert = tmp_path / "junk.pem"
    junk_key = tmp_path / "junk.key"
    junk_cert.write_text("-----BEGIN CERTIFICATE-----\nnot base64\n")
    junk_key.write_text("-----BEGIN PRIVATE KEY-----\nnot base64\n")

    with pytest.raises(click.UsageError, match="not a readable PEM certificate"):
        _resolve(sample_config, tls_cert=str(junk_cert), tls_key=str(junk_key))


def test_resolve_tls_config_pair_is_validated_too(sample_config, pem_pair, tmp_path):
    """The config path gets the same check as the flags — it is the one a
    running panel picks up unattended."""
    cert, _ = pem_pair
    other_cert = tmp_path / "other-cert.pem"
    other_key = tmp_path / "other-key.pem"
    tls.generate_self_signed(other_cert, other_key, "127.0.0.1")
    sample_config.web_tls_cert = str(cert)
    sample_config.web_tls_key = str(other_key)

    with pytest.raises(click.UsageError, match="does not match"):
        _resolve(sample_config)


# --- web_self_signed (plans/28 decision 8) ----------------------------------


def test_web_self_signed_serves_https_without_the_flag(sample_config, tls_home):
    """The whole point: a bare `zsm web` keeps HTTPS on.

    `--self-signed` is per-run, and the drop back to plain HTTP is silent —
    no flag means no TLS was requested, so there is nothing to fail on.
    """
    sample_config.web_self_signed = True

    certfile, keyfile, note = _resolve(sample_config)

    assert certfile and keyfile
    assert certfile == str(tls_home / "tls" / "cert.pem")
    assert "self-signed certificate" in note
    assert "Enabled by web_self_signed" in note


def test_web_self_signed_renews_itself(sample_config, tls_home, monkeypatch):
    """Unlike web_tls_cert pinned at the same file, this path calls the
    generator every run — which is the reason the field exists."""
    sample_config.web_self_signed = True
    _resolve(sample_config)

    monkeypatch.setattr(tls, "RENEW_WHEN_DAYS_LEFT", tls.VALIDITY_DAYS + 1)
    _, _, note = _resolve(sample_config)

    assert "renewed" in note


def test_explicit_tls_flags_beat_web_self_signed(sample_config, pem_pair, tls_home):
    """An explicit flag beats persisted state — the rule already stated for
    --self-signed vs. the config pair, applied the other way round."""
    sample_config.web_self_signed = True
    cert, key = pem_pair

    certfile, keyfile, note = _resolve(
        sample_config, tls_cert=str(cert), tls_key=str(key)
    )

    assert (certfile, keyfile) == (str(cert), str(key))
    assert "self-signed" not in note


def test_web_self_signed_with_a_config_pair_raises(sample_config, pem_pair):
    """Guessing would either downgrade a real certificate to self-signed or
    ignore a setting the operator turned on. Both silent; both refused."""
    cert, key = pem_pair
    sample_config.web_self_signed = True
    sample_config.web_tls_cert = str(cert)
    sample_config.web_tls_key = str(key)

    with pytest.raises(click.UsageError, match="will not guess"):
        _resolve(sample_config)


def test_force_new_cert_and_san_work_with_web_self_signed(sample_config, tls_home):
    """Both flags gate on --self-signed; they must gate on the effective state,
    or web_self_signed would be a second-class way to turn TLS on."""
    sample_config.web_self_signed = True
    cert, _, _ = _resolve(sample_config)
    first = tls.fingerprint_sha256(Path(cert))

    _, _, note = _resolve(sample_config, force_new_cert=True)
    assert "regenerated" in note
    assert tls.fingerprint_sha256(Path(cert)) != first

    _resolve(sample_config, san=("203.0.113.10",))
    parsed = x509.load_pem_x509_certificate(Path(cert).read_bytes())
    san = parsed.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "203.0.113.10" in {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}


def test_web_self_signed_round_trips_and_is_off_by_default(sample_config):
    from app.shared.config import DayzConfig

    assert DayzConfig().web_self_signed is False

    sample_config.web_self_signed = True
    sample_config.save()
    assert DayzConfig.load().web_self_signed is True


def test_web_self_signed_is_in_the_settings_registry():
    """Both `zsm settings` and /api/settings read this one list; a field with
    no entry is reachable only by hand-editing config.json."""
    from app.shared.settings_registry import _REGISTRY

    entry = next(s for s in _REGISTRY if s.key == "web_self_signed")
    assert (entry.section, entry.kind) == ("Web panel", "bool")


def test_resolve_tls_san_without_self_signed_raises(sample_config):
    """--san has nothing to act on without --self-signed; accepting it silently
    would look like it applied to a --tls-cert pair, which it cannot."""
    with pytest.raises(click.UsageError):
        _resolve(sample_config, san=("203.0.113.10",))


def test_config_fields_round_trip(sample_config):
    from app.shared.config import DayzConfig

    sample_config.web_tls_cert = "/etc/ssl/zsm.pem"
    sample_config.web_tls_key = "/etc/ssl/zsm.key"
    sample_config.save()

    loaded = DayzConfig.load()
    assert loaded.web_tls_cert == "/etc/ssl/zsm.pem"
    assert loaded.web_tls_key == "/etc/ssl/zsm.key"


# --- an auto-picked 0.0.0.0 serves HTTPS (plans/68 NET-1) -------------------

_SSH_VARS = ("SSH_CLIENT", "SSH_TTY", "SSH_CONNECTION")


@pytest.fixture()
def where(monkeypatch):
    """Put the test "in" an SSH session, Docker, or neither."""
    import app.shared.platform as plat

    def place(kind: str) -> None:
        for var in _SSH_VARS:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setattr(plat, "IS_DOCKER", kind == "docker")
        if kind == "ssh":
            monkeypatch.setenv("SSH_CONNECTION", "198.51.100.7 50000 192.0.2.10 22")

    return place


def _run_web(config, args=()):
    """`zsm web` with the server stubbed: ``(output, start_server kwargs)``."""
    from unittest.mock import AsyncMock, patch

    from click.testing import CliRunner

    from app.commands.web import web_cmd
    from app.web.auth import hash_password

    config.web_password_hash = hash_password("a-long-enough-password")
    with patch("app.shared.config.DayzConfig.load", return_value=config), \
         patch("app.web.server.start_server", new_callable=AsyncMock) as started:
        result = CliRunner().invoke(web_cmd, list(args))
    assert result.exit_code == 0, result.output
    return result.output, started.await_args.kwargs


def test_auto_public_bind_names_the_reason(where):
    import types

    import app.shared.platform as plat

    unset, pinned = types.SimpleNamespace(web_bind_host=""), types.SimpleNamespace(web_bind_host="0.0.0.0")
    where("ssh")
    assert plat.auto_public_bind(unset) == "ssh"
    assert plat.auto_public_bind(pinned) == ""
    assert plat.auto_public_bind(unset, env={}) == ""  # a systemd unit
    where("docker")
    assert plat.auto_public_bind(unset) == "docker"
    where("")
    assert plat.auto_public_bind(unset) == ""


def test_ssh_auto_bind_serves_the_self_signed_certificate(sample_config, tls_home, where):
    where("ssh")
    output, kwargs = _run_web(sample_config)

    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["ssl_certfile"] == str(tls_home / "tls" / "cert.pem")
    assert "Starting zsm web at https://0.0.0.0:8433" in output
    assert "On by itself" in output and "SSH session" in output
    assert "plain HTTP on a non-loopback" not in output
    assert sample_config.web_self_signed is False, "nothing is persisted"


def test_san_and_force_new_cert_work_with_the_auto_certificate(sample_config, tls_home, where):
    where("ssh")
    _run_web(sample_config, ["--san", "203.0.113.10", "--force-new-cert"])
    parsed = x509.load_pem_x509_certificate((tls_home / "tls" / "cert.pem").read_bytes())
    san = parsed.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "203.0.113.10" in {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}


def test_an_explicit_host_keeps_plain_http(sample_config, tls_home, where):
    where("ssh")
    output, kwargs = _run_web(sample_config, ["--host", "0.0.0.0"])
    assert kwargs["ssl_certfile"] is None
    assert "Starting zsm web at http://0.0.0.0:8433" in output
    assert not (tls_home / "tls" / "cert.pem").exists()


def test_web_bind_host_keeps_plain_http(sample_config, tls_home, where):
    """A Docker or SSH install that already set web_bind_host is unaffected."""
    sample_config.web_bind_host = "0.0.0.0"
    for kind in ("ssh", "docker"):
        where(kind)
        output, kwargs = _run_web(sample_config)
        assert kwargs["ssl_certfile"] is None
        assert "http://0.0.0.0:8433" in output


def test_a_certificate_the_admin_chose_wins_over_the_auto_one(sample_config, pem_pair, where):
    where("ssh")
    cert, key = pem_pair
    output, kwargs = _run_web(sample_config, ["--tls-cert", str(cert), "--tls-key", str(key)])
    assert (kwargs["ssl_certfile"], kwargs["ssl_keyfile"]) == (str(cert), str(key))
    assert "On by itself" not in output

    sample_config.web_tls_cert, sample_config.web_tls_key = str(cert), str(key)
    output, kwargs = _run_web(sample_config)
    assert kwargs["ssl_certfile"] == str(cert)
    assert "(from config)" in output


def test_web_self_signed_still_names_itself_over_ssh(sample_config, tls_home, where):
    where("ssh")
    sample_config.web_self_signed = True
    output, kwargs = _run_web(sample_config)
    assert kwargs["ssl_certfile"]
    assert "Enabled by web_self_signed" in output
    assert "On by itself" not in output


def test_loopback_and_a_systemd_panel_stay_on_http(sample_config, tls_home, where):
    """No SSH session and no Docker: 127.0.0.1 over HTTP, as a systemd-started
    panel gets (``panel_bind_host`` asks with an empty environment)."""
    from app.server.panel_unit import panel_bind_host

    where("")
    output, kwargs = _run_web(sample_config)
    assert (kwargs["host"], kwargs["ssl_certfile"]) == ("127.0.0.1", None)
    assert "http://127.0.0.1:8433" in output
    where("ssh")
    assert panel_bind_host(sample_config) == "127.0.0.1"


def test_docker_auto_bind_serves_the_self_signed_certificate(sample_config, tls_home, where):
    """With host networking a container's auto-picked 0.0.0.0 is every
    interface of the Docker host, so it gets the SSH rule. It needs no SSH
    session, so a panel unit systemd starts inside the container gets it too."""
    where("docker")
    output, kwargs = _run_web(sample_config)

    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["ssl_certfile"] == str(tls_home / "tls" / "cert.pem")
    assert "Starting zsm web at https://0.0.0.0:8433" in output
    assert "On by itself" in output and "Docker container" in output
    assert "plain HTTP on a non-loopback" not in output
    assert sample_config.web_self_signed is False, "nothing is persisted"


def test_docker_keeps_plain_http_when_the_bind_is_chosen(sample_config, tls_home, where):
    """The Docker docs page's way to keep HTTP, e.g. for a proxy on the Docker host
    pointing at http://127.0.0.1:8433: set web_bind_host, or pass --host."""
    where("docker")
    sample_config.web_bind_host = "127.0.0.1"
    output, kwargs = _run_web(sample_config)
    assert (kwargs["host"], kwargs["ssl_certfile"]) == ("127.0.0.1", None)
    assert "Starting zsm web at http://127.0.0.1:8433" in output

    sample_config.web_bind_host = ""
    output, kwargs = _run_web(sample_config, ["--host", "0.0.0.0"])
    assert (kwargs["host"], kwargs["ssl_certfile"]) == ("0.0.0.0", None)
    assert "Starting zsm web at http://0.0.0.0:8433" in output
    assert not (tls_home / "tls" / "cert.pem").exists()


def test_the_panel_says_on_windows_that_the_firewall_may_ask(sample_config, tls_home, monkeypatch):
    """Windows Firewall asked about Python after the first sign-in, and no
    line had said it would (first-hour test, Windows 11)."""
    from app.commands import web as web_cmd_module

    sample_config.web_bind_host = "127.0.0.1"
    output, _kwargs = _run_web(sample_config)
    assert "Windows Firewall" not in output

    monkeypatch.setattr(web_cmd_module, "IS_WINDOWS", True)
    output, _kwargs = _run_web(sample_config)
    assert "Windows Firewall may ask once, often over your browser, whether to allow Python" in output
    assert "click 'Allow'" in output


def test_a_wildcard_bind_prints_addresses_a_browser_can_open(sample_config, tls_home, where, monkeypatch):
    import app.web.tls as tls_mod

    monkeypatch.setattr(tls_mod, "_local_ipv4s", lambda: ["192.0.2.7"])
    where("docker")
    output, _ = _run_web(sample_config)
    assert "Open in your browser: https://localhost:8433" in output
    assert "Open in your browser: https://192.0.2.7:8433" in output
    assert "Open in your browser: https://0.0.0.0" not in output


def test_browser_urls_keep_a_named_host():
    from app.commands.web import browser_urls

    assert browser_urls("http", "127.0.0.1", 8433) == ["http://127.0.0.1:8433"]
    assert browser_urls("https", "::1", 9000) == ["https://[::1]:9000"]
