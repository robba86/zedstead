"""Self-signed certificate generation for ``zsm web --self-signed``.

A self-signed cert stops **passive interception** — the admin password and the
session cookie no longer cross the network in cleartext. It does **not**
authenticate the server to the browser: anyone who can intercept can also
present their own certificate, and the browser has no way to tell them apart
beyond the fingerprint the user checks by hand. For a public panel, use a real
certificate via a reverse proxy; this is the "stop the cleartext now" option.

Nothing here imports FastAPI or uvicorn, and everything works on Windows
(``cryptography`` ships wheels for every supported platform, so there is no
dependency on ``openssl(1)`` being installed).
"""

import datetime
import ipaddress
import os
import socket
from collections.abc import Iterable
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.shared.json_utils import atomic_write_bytes

# 825 days is the longest validity browsers still tolerate for a non-public
# certificate. Regenerate before it lapses so a long-running panel does not
# start failing on a date rather than on a change.
VALIDITY_DAYS = 825
RENEW_WHEN_DAYS_LEFT = 30


def tls_dir() -> Path:
    """``CONFIG_DIR/tls``, created ``0700``.

    ``paths.CONFIG_DIR`` is read **at call time**, through the module, because
    tests monkeypatch it (see ``tests/conftest.py``); a from-import bound at
    import time would write into the real ``~/.config/zsm``.

    The mode is a no-op on Windows, where ``os.chmod`` only toggles the
    read-only bit — the directory is inside the user profile there, which is
    the practical equivalent.
    """
    from app.shared import paths

    d = Path(paths.CONFIG_DIR) / "tls"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def cert_paths() -> tuple[Path, Path]:
    """``(cert.pem, key.pem)`` under :func:`tls_dir`."""
    d = tls_dir()
    return d / "cert.pem", d / "key.pem"


def _local_ipv4s() -> list[str]:
    """Every non-loopback IPv4 bound on this host, best-effort."""
    found: list[str] = []
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in found:
                found.append(ip)
    except OSError:
        pass
    # getaddrinfo(hostname) often only knows the loopback alias on a VPS whose
    # hostname is not in /etc/hosts; the UDP-connect trick reports the address
    # the default route would use without sending a packet.
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))  # TEST-NET-1, never routed
            ip = s.getsockname()[0]
        finally:
            s.close()
        if ip and not ip.startswith("127.") and ip not in found:
            found.append(ip)
    except OSError:
        pass
    return found


def san_entries(
    bind_host: str | None = None, extra: Iterable[str] | None = None
) -> list[str]:
    """Names/IPs for ``subjectAltName``.

    Modern browsers ignore the CN entirely, so this list is what decides
    whether the certificate is usable at all. Without the host's LAN or public
    address in it the browser rejects the cert even after the user clicks
    through the warning — which reads as "zsm's TLS is broken".

    Order is stable and deduplicated: hostname, ``localhost``, ``127.0.0.1``,
    ``::1``, every non-loopback IPv4 on the host, ``bind_host`` when it parses
    as an IP address (a wildcard bind like ``0.0.0.0`` is skipped — it is not
    an identity a client can connect to), and finally ``extra``.

    ``extra`` is ``zsm web --san``, and it exists because everything above it
    is **locally discoverable only**. On a cloud VPS with a private NIC plus a
    floating/elastic public IP, both discovery paths report the private
    address; the public one the client actually dials is nowhere in the
    certificate, so the browser adds a name mismatch on top of the untrusted
    warning and stricter clients refuse outright. A DNS name pointed at the box
    has the same problem.
    """
    entries: list[str] = []

    def add(value: str) -> None:
        if value and value not in entries:
            entries.append(value)

    try:
        add(socket.gethostname())
    except OSError:
        pass
    add("localhost")
    add("127.0.0.1")
    add("::1")
    for ip in _local_ipv4s():
        add(ip)
    if bind_host:
        try:
            parsed = ipaddress.ip_address(bind_host)
        except ValueError:
            add(bind_host)  # a hostname the operator bound by name
        else:
            if not parsed.is_unspecified:
                add(str(parsed))
    for value in extra or ():
        value = (value or "").strip()
        try:
            add(str(ipaddress.ip_address(value)))
        except ValueError:
            add(value)
    return entries


def _load_cert(cert_path: Path) -> x509.Certificate | None:
    try:
        return x509.load_pem_x509_certificate(Path(cert_path).read_bytes())
    except (OSError, ValueError):
        return None


def cert_expiry(cert_path: Path) -> datetime.datetime | None:
    """UTC expiry of ``cert_path``, or ``None`` if it is missing/unparseable."""
    cert = _load_cert(cert_path)
    if cert is None:
        return None
    return cert.not_valid_after_utc


def cert_san_values(cert_path: Path) -> list[str]:
    """The DNS names and IPs in ``cert_path``'s subjectAltName, as strings.

    Empty when the certificate is missing, unparseable, or carries no SAN
    extension — all of which already mean "regenerate".
    """
    cert = _load_cert(cert_path)
    if cert is None:
        return []
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except x509.ExtensionNotFound:
        return []
    names = list(ext.value.get_values_for_type(x509.DNSName))
    names += [str(ip) for ip in ext.value.get_values_for_type(x509.IPAddress)]
    return names


def needs_regeneration(
    cert: Path, key: Path, *, force: bool = False, sans: Iterable[str] | None = None
) -> bool:
    """True if the pair is missing, unreadable, expired, or nearly expired.

    ``sans`` is the SAN set the caller wants. A certificate that does not carry
    exactly that set is regenerated: without this, ``zsm web --san <public-ip>``
    would silently do nothing from the second run onwards, because the existing
    pair is otherwise perfectly valid. Same reasoning covers a host that has
    changed address — the old certificate no longer names it.
    """
    if force:
        return True
    if not Path(cert).exists() or not Path(key).exists():
        return True
    expiry = cert_expiry(cert)
    if expiry is None:
        return True
    if sans is not None and set(sans) != set(cert_san_values(cert)):
        return True
    left = expiry - datetime.datetime.now(datetime.timezone.utc)
    return left.days < RENEW_WHEN_DAYS_LEFT


def _build_san(names: list[str]) -> x509.SubjectAlternativeName:
    general: list[x509.GeneralName] = []
    for value in names:
        try:
            general.append(x509.IPAddress(ipaddress.ip_address(value)))
        except ValueError:
            general.append(x509.DNSName(value))
    return x509.SubjectAlternativeName(general)


def generate_self_signed(
    cert_path: Path,
    key_path: Path,
    bind_host: str | None = None,
    extra_sans: Iterable[str] | None = None,
) -> None:
    """Write a fresh EC P-256 self-signed cert/key pair to the given paths.

    EC over RSA: a smaller key, a faster handshake, and universally supported
    by browsers. The key is written **unencrypted** on purpose — uvicorn would
    otherwise prompt for a passphrase at startup, which makes an unattended
    service impossible. That is why it is written ``0600``.
    """
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "zsm web"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "zsm (self-signed)"),
        ]
    )
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        # Backdate slightly so a client whose clock runs behind ours does not
        # reject a certificate generated seconds ago.
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=VALIDITY_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(_build_san(san_entries(bind_host, extra_sans)), critical=False)
        .sign(key, hashes.SHA256())
    )

    cert_path = Path(cert_path)
    key_path = Path(key_path)
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    # Through mkstemp, which creates the file 0600: write_bytes then chmod
    # left the key readable under the umask (0644) between the two calls, and
    # a regenerated key replaces the old file rather than reusing its mode.
    atomic_write_bytes(
        key_path,
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        owner_only=True,
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def ensure_self_signed(
    bind_host: str | None = None,
    *,
    force: bool = False,
    extra_sans: Iterable[str] | None = None,
) -> tuple[Path, Path, bool]:
    """``(cert, key, generated)`` for ``CONFIG_DIR/tls``.

    Idempotent: a valid pair is reused, so restarting the panel does not hand
    the browser a new certificate to re-accept. ``force`` regenerates, and so
    does a change to ``extra_sans`` (see :func:`needs_regeneration`).
    """
    cert, key = cert_paths()
    extra = list(extra_sans or ())
    wanted = san_entries(bind_host, extra)
    if needs_regeneration(cert, key, force=force, sans=wanted):
        generate_self_signed(cert, key, bind_host, extra)
        return cert, key, True
    return cert, key, False


def fingerprint_sha256(cert_path: Path) -> str:
    """``'AB:CD:…'`` — 32 uppercase hex pairs.

    Printed at startup: it is the only way a user can tell the browser's
    warning is about *their* certificate and not an interception.
    """
    cert = _load_cert(cert_path)
    if cert is None:
        return ""
    digest = cert.fingerprint(hashes.SHA256())
    return ":".join(f"{b:02X}" for b in digest)
