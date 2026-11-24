"""CLI command to launch the zsm web UI (REST + WebSocket API + React frontend)."""

import ipaddress
import os
import sys
from pathlib import Path

import click

from app.shared.platform import IS_WINDOWS


def _readable(path: Path, label: str) -> None:
    """Raise a usage error unless ``path`` is an existing, readable file."""
    if not path.exists():
        raise click.UsageError(f"{label} not found: {path}")
    if path.is_dir():
        raise click.UsageError(f"{label} is a directory, not a PEM file: {path}")
    try:
        with path.open("rb"):
            pass
    except OSError as exc:
        raise click.UsageError(f"{label} is not readable: {path} ({exc})") from exc


def _check_pair(cert_path: Path, key_path: Path, cert_label: str, key_label: str) -> None:
    """Raise a usage error unless the two PEMs parse and belong together.

    Existence and readability are not enough: a mismatched pair is the common
    real mistake, and uvicorn's answer to it is the startup banner followed by
    a bare ``ssl.SSLError: [SSL] PEM lib`` traceback with no mention of which
    file is wrong. The invariant that matters still holds either way — it never
    falls back to HTTP — so this is about the message, and it keeps exit 2 like
    every other TLS misconfiguration.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    except (OSError, ValueError) as exc:
        raise click.UsageError(
            f"{cert_label} is not a readable PEM certificate: {cert_path} ({exc})"
        ) from exc
    try:
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    except TypeError as exc:
        # An encrypted key. uvicorn cannot answer a passphrase prompt at
        # startup, so this is a hard no rather than a prompt.
        raise click.UsageError(
            f"{key_label} is passphrase-protected: {key_path}. `zsm web` cannot "
            "prompt for it at startup — decrypt it with "
            f"`openssl pkey -in {key_path} -out <plaintext.pem>` first."
        ) from exc
    except (OSError, ValueError) as exc:
        raise click.UsageError(
            f"{key_label} is not a readable PEM private key: {key_path} ({exc})"
        ) from exc

    if key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ) != cert.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ):
        raise click.UsageError(
            f"{key_label} does not match {cert_label}: {key_path} is not the "
            f"private key for {cert_path}."
        )


# The auto-detected binds (``platform.auto_public_bind``) that turn on the
# self-signed certificate when nothing else chose TLS (plans/68 NET-1): both
# reasons zsm picks 0.0.0.0 by itself. With host networking a container's
# 0.0.0.0 is every interface of the Docker host, the same exposure as SSH.
AUTO_TLS_BINDS = frozenset({"ssh", "docker"})

_AUTO_WHY = {"ssh": "this is an SSH session", "docker": "this is a Docker container"}


def _auto_tls(config, tls_cert, tls_key, self_signed) -> str:
    """The ``auto_public_bind`` reason when ``zsm web`` should serve the
    self-signed certificate on its own, else ``""``.

    Only for a bind nobody asked for: the caller asks only when there is no
    ``--host``, and ``auto_public_bind`` answers ``""`` when ``web_bind_host``
    is set. Any TLS the admin did choose (a flag, ``web_self_signed``, a
    configured pair) stays theirs.
    """
    from app.shared.platform import auto_public_bind

    if tls_cert or tls_key or self_signed:
        return ""
    if getattr(config, "web_self_signed", False):
        return ""
    if (getattr(config, "web_tls_cert", "") or "").strip() or (
        getattr(config, "web_tls_key", "") or ""
    ).strip():
        return ""
    reason = auto_public_bind(config)
    return reason if reason in AUTO_TLS_BINDS else ""


def _resolve_tls(
    config, tls_cert, tls_key, self_signed, force_new_cert, host, san=(), auto=""
):
    """Resolve the TLS pair for ``zsm web`` → ``(certfile, keyfile, note)``.

    Order: ``--self-signed`` > ``--tls-cert``/``--tls-key`` > the
    ``web_self_signed`` / ``web_tls_cert``+``web_tls_key`` config fields >
    plain HTTP. An explicit flag always beats persisted state, which is why
    ``web_self_signed`` stands down when the command line named a pair.

    Every misconfiguration raises :class:`click.UsageError` (exit 2) rather
    than falling back to HTTP. A panel that quietly serves cleartext because a
    certificate was moved or half-configured is precisely the failure this
    command exists to remove — see plans/24.

    ``note`` is the banner text for the resolved source (empty when no TLS).
    *auto* is ``_auto_tls``'s reason when the self-signed pair was turned on
    for an auto-detected ``0.0.0.0``; the note then says so.
    """
    cfg_self_signed = bool(getattr(config, "web_self_signed", False))
    cfg_cert = (getattr(config, "web_tls_cert", "") or "").strip()
    cfg_key = (getattr(config, "web_tls_key", "") or "").strip()

    from_config = cfg_self_signed and not (tls_cert or tls_key)
    use_self_signed = self_signed or from_config or bool(auto)

    if force_new_cert and not use_self_signed:
        raise click.UsageError(
            "--force-new-cert only applies with --self-signed (or web_self_signed)."
        )
    if san and not use_self_signed:
        raise click.UsageError(
            "--san only applies with --self-signed (or web_self_signed)."
        )

    if use_self_signed:
        if self_signed and (tls_cert or tls_key):
            raise click.UsageError(
                "--self-signed cannot be combined with --tls-cert/--tls-key."
            )
        if from_config and (cfg_cert or cfg_key):
            # Same rule as the flag pair one level down. Guessing here would
            # either downgrade a real certificate to self-signed or ignore a
            # setting the operator turned on — both silent, both the failure
            # mode this whole command was written against.
            raise click.UsageError(
                "web_self_signed is on while web_tls_cert/web_tls_key are also "
                "set — Zedstead will not guess which certificate you meant. Clear "
                "one: `zsm settings set web_self_signed false`, or "
                "`zsm settings set web_tls_cert ''` (and web_tls_key)."
            )
        from app.web import tls as tls_mod

        # Whether a certificate was already there decides the wording below.
        # Renewal day used to print the first-run line word for word, so anyone
        # running under tmux/nohup met the browser warning ~795 days later with
        # nothing to connect it to — and that is the exact shape of a MITM,
        # which is the one signal self-signed depends on.
        cert_path = tls_mod.cert_paths()[0]
        had_cert = cert_path.exists()
        # Why it is being replaced, decided *before* the generator runs. Every
        # non-forced regeneration used to report itself as "renewed", so adding
        # --san borrowed expiry's word for something the operator had just
        # asked for — measured on a VM, 2026 Q3. An unreadable old cert
        # yields no SAN list, and falls through rather than claiming a change.
        existing_sans = tls_mod.cert_san_values(cert_path) if had_cert else []
        sans_changed = bool(existing_sans) and set(existing_sans) != set(
            tls_mod.san_entries(host, san)
        )
        cert, key, generated = tls_mod.ensure_self_signed(
            host, force=force_new_cert, extra_sans=san
        )
        fingerprint = tls_mod.fingerprint_sha256(cert)
        reaccept = "your browser will ask you to accept it again"
        if not generated:
            state = "reused"
        elif not had_cert:
            state = "generated"
        elif force_new_cert:
            state = f"regenerated — {reaccept}"
        elif sans_changed:
            # Covers --san and a host whose own address changed; naming only
            # --san would be wrong for the second.
            state = f"regenerated — the names/IPs it covers changed; {reaccept}"
        else:
            state = f"renewed — {reaccept}"
        # Say where HTTPS came from when no flag asked for it — otherwise the
        # operator has nothing to grep for when wondering why the panel is on
        # https today.
        if auto:
            source = (
                f"  On by itself: zsm web picked 0.0.0.0 because {_AUTO_WHY.get(auto, auto)}.\n"
                "  --host 127.0.0.1 keeps the app on this machine; an explicit\n"
                "  --host 0.0.0.0 or web_bind_host serves plain HTTP as before.\n"
            )
        elif from_config:
            source = "  Enabled by web_self_signed.\n"
        else:
            source = ""
        note = (
            f"TLS: self-signed certificate ({state}) — {cert}\n"
            f"{source}"
            f"  SHA-256 fingerprint: {fingerprint}\n"
            "  Your browser will warn that this certificate is untrusted. That is\n"
            "  expected; check the fingerprint above matches before accepting it.\n"
            "  Self-signed encrypts the connection but does not prove the server's\n"
            "  identity — for a public app use a real certificate behind a\n"
            "  reverse proxy (recipes: https://zedstead.com/docs/reverse-proxy/)."
        )
        return str(cert), str(key), note

    if tls_cert or tls_key:
        if not (tls_cert and tls_key):
            missing = "--tls-key" if tls_cert else "--tls-cert"
            raise click.UsageError(
                f"{missing} is required when the other is given "
                "(refusing to start in cleartext instead)."
            )
        cert_path, key_path = Path(tls_cert), Path(tls_key)
        _readable(cert_path, "--tls-cert")
        _readable(key_path, "--tls-key")
        _check_pair(cert_path, key_path, "--tls-cert", "--tls-key")
        return str(cert_path), str(key_path), f"TLS: {cert_path}"

    if cfg_cert or cfg_key:
        if not (cfg_cert and cfg_key):
            missing = "web_tls_key" if cfg_cert else "web_tls_cert"
            raise click.UsageError(
                f"{missing} is empty while the other is set — set both with "
                "`zsm settings set`, or clear both to serve plain HTTP."
            )
        cert_path, key_path = Path(cfg_cert), Path(cfg_key)
        _readable(cert_path, "web_tls_cert")
        _readable(key_path, "web_tls_key")
        _check_pair(cert_path, key_path, "web_tls_cert", "web_tls_key")
        return str(cert_path), str(key_path), f"TLS: {cert_path} (from config)"

    return None, None, ""


def _check_bind_host(host: str, port: int, source: str) -> None:
    """Refuse a host this machine can't resolve, and say where it came from.

    uvicorn's own error is only "Name or service not known", which names
    neither the host nor the ``web_bind_host`` setting a typo in the panel may
    have left behind, and in Docker the entrypoint's ``zsm web`` just dies.
    *source* is ``"--host"`` or ``"setting"``; the auto default always resolves.
    """
    import socket

    try:
        socket.getaddrinfo(host, port)
    except (socket.gaierror, UnicodeError) as exc:
        if source == "setting":
            where = "the web_bind_host setting"
            fix = (
                "  Reset it to auto:     zsm settings set web_bind_host ''\n"
                "  or start once with:   zsm web --host 127.0.0.1"
            )
        else:
            where = "--host"
            fix = "  Pass an IP address or a name this machine resolves, e.g. --host 127.0.0.1"
        raise click.UsageError(f"Can't bind to {host!r} (from {where}): {exc}.\n{fix}") from exc


@click.command("web")
@click.option(
    "--host",
    default=None,
    help="Host to bind to (default: 0.0.0.0 over SSH or in Docker, with a "
    "self-signed certificate; 127.0.0.1 otherwise).",
)
@click.option("--port", default=8433, type=int, help="Port to bind to.")
@click.option(
    "--set-password", is_flag=True, help="Set/change the web UI password and exit."
)
@click.option(
    "--reload",
    "reload_",
    is_flag=True,
    help="Dev mode: restart the backend when app/ changes, and keep the login "
    "session across restarts. Requires a source checkout.",
)
@click.option(
    "--tls-cert",
    "tls_cert",
    default=None,
    type=click.Path(),
    help="PEM certificate to serve HTTPS with. Requires --tls-key.",
)
@click.option(
    "--tls-key",
    "tls_key",
    default=None,
    type=click.Path(),
    help="PEM private key matching --tls-cert. Requires --tls-cert.",
)
@click.option(
    "--self-signed",
    is_flag=True,
    help="Generate (once) and use a self-signed certificate under the Zedstead "
    "config dir. Stops cleartext; does NOT authenticate the server — for a "
    "public app use a real certificate via a reverse proxy. Per-run: to "
    "keep it on, `zsm settings set web_self_signed true`.",
)
@click.option(
    "--force-new-cert",
    is_flag=True,
    help="With --self-signed: regenerate even if a valid certificate exists.",
)
@click.option(
    "--san",
    multiple=True,
    metavar="TEXT",
    help="With --self-signed: an extra hostname or IP to include in the "
    "generated certificate. Repeatable. Needed when the address clients use "
    "is not discoverable on the box — a cloud floating/elastic IP, or a DNS "
    "name pointed at it. Changing the set regenerates the certificate.",
)
def web_cmd(
    host,
    port,
    set_password,
    reload_,
    tls_cert,
    tls_key,
    self_signed,
    force_new_cert,
    san,
):
    """Launch the zsm web UI."""
    from app.commands.users import prompt_new_password
    from app.shared.config import DayzConfig
    from app.web.auth import hash_password
    from app.web.users import UserStore, has_no_login

    config = DayzConfig.load()

    if set_password:
        pw = prompt_new_password("New web UI password")
        config.web_password_hash = hash_password(pw)
        config.save()
        click.echo("Web UI password updated.")
        if UserStore().has_accounts():
            click.echo(
                "Accounts exist, so this is only the fallback for when users.json "
                "cannot be read. Change an account's password with `zsm users passwd NAME`."
            )
        return

    auto = ""
    if host is not None:
        _check_bind_host(host, port, "--host")
    else:
        from app.shared.platform import default_bind_host

        host = default_bind_host(config)
        if config.web_bind_host:
            _check_bind_host(host, port, "setting")
        else:
            # A 0.0.0.0 nobody asked for gets the self-signed certificate
            # rather than a password crossing the network in cleartext.
            auto = _auto_tls(config, tls_cert, tls_key, self_signed)

    # Resolved before the password prompt so a bad --tls-* invocation exits 2
    # immediately instead of asking for a password it is about to throw away.
    certfile, keyfile, tls_note = _resolve_tls(
        config, tls_cert, tls_key, self_signed, force_new_cert, host, san, auto=auto
    )
    scheme = "https" if certfile else "http"

    # Only when nobody could sign in: an instance made by `zsm instances
    # add` has no password of its own, and the machine-wide accounts are its
    # login (`has_no_login`, shared with `zsm service web install`).
    if has_no_login(config):
        click.echo("No web UI password set yet — set one now.")
        pw = prompt_new_password("Web UI password")
        config.web_password_hash = hash_password(pw)
        config.save()

    import asyncio

    from app.web.server import run_dev_server, start_server

    click.echo(
        f"Starting zsm web at {scheme}://{host}:{port}{' (reload)' if reload_ else ''}"
    )
    _print_open_links(scheme, host, port)
    if tls_note:
        click.echo(tls_note)
    if host in ("127.0.0.1", "localhost"):
        click.echo(
            "Note: bound to localhost only — NOT reachable from other machines.\n"
            "  To reach it remotely, either:\n"
            f"    • SSH tunnel:  ssh -L {port}:localhost:{port} user@thishost   (most secure)\n"
            f"    • Expose LAN:  zsm web --host 0.0.0.0 --self-signed   (password-gated, encrypted)\n"
            "  Make 0.0.0.0 permanent:  zsm settings set web_bind_host 0.0.0.0"
        )
    elif not certfile:
        click.echo(
            "Warning: serving over plain HTTP on a non-loopback address — the\n"
            "  password and session cookie cross the network in cleartext.\n"
            "    • Encrypt it:  zsm web --self-signed\n"
            "      (or put a reverse proxy with a real certificate in front —\n"
            "       tested Caddy/nginx recipes in https://zedstead.com/docs/reverse-proxy/)\n"
            "    • TLS terminating on another host? Then also set:\n"
            "        zsm settings set web_trusted_proxies <proxy-ip>"
        )
    if IS_WINDOWS:
        # It asks once, after the first sign-in, and nothing had said it would.
        click.echo(
            "Windows Firewall may ask once, often over your browser, whether to allow Python, which Zedstead runs on: click 'Allow'."
        )
    click.echo("Press Ctrl+C to stop.\n")

    try:
        if reload_:
            # Blocking: uvicorn's reloader supervises its own worker process,
            # so it owns the event loop (see server.run_dev_server).
            run_dev_server(
                host=host, port=port, ssl_certfile=certfile, ssl_keyfile=keyfile
            )
        else:
            restart = asyncio.run(
                start_server(
                    config,
                    host=host,
                    port=port,
                    ssl_certfile=certfile,
                    ssl_keyfile=keyfile,
                )
            )
            # `is True`: anything else (a mocked server's return) is not a request.
            if restart is True:
                _restart_self()
    except KeyboardInterrupt:
        click.echo("\nzsm web stopped.")


def browser_urls(scheme: str, host: str, port: int) -> list[str]:
    """The addresses a browser can open for a panel bound to *host*.

    A wildcard bind (``0.0.0.0``, ``::``) is no address a browser can open, so
    it becomes ``localhost`` plus every LAN IPv4 of this machine. In Docker the
    compose file uses host networking, so those are the host's own addresses.
    """
    try:
        wildcard = ipaddress.ip_address(host).is_unspecified
    except ValueError:
        wildcard = host == ""
    if not wildcard:
        shown = f"[{host}]" if ":" in host else host
        return [f"{scheme}://{shown}:{port}"]
    from app.web.tls import _local_ipv4s

    return [f"{scheme}://localhost:{port}"] + [
        f"{scheme}://{ip}:{port}" for ip in _local_ipv4s()
    ]


def _print_open_links(scheme: str, host: str, port: int) -> None:
    """Print the panel's openable addresses in bold, as terminal hyperlinks."""
    from rich.markup import escape

    from app.shared.cli_ui import console

    for url in browser_urls(scheme, host, port):
        console.print(
            f"  [bold green]Open in your browser:[/bold green] "
            f"[bold cyan][link={url}]{escape(url)}[/link][/bold cyan]",
            soft_wrap=True,
        )


def _restart_self() -> None:
    """Run this ``zsm web`` again in the same process, after a self-update.

    ``exec`` keeps the PID, so systemd's ``zsm-web.service`` sees the same
    process, and a panel started by hand keeps its terminal. ``-m app`` loads
    the code the update just installed into this interpreter's environment;
    the arguments are the ones this run was started with.
    """
    click.echo("Restarting zsm web with the new version.")
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable, "-m", "app", *sys.argv[1:]])
