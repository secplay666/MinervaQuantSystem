"""TLS for the API without a domain (ADR-010 §7, amended): a private CA and a
server certificate for the public IP, served by uvicorn directly.

    quant-app tls init --ip 8.159.139.145 --ip 127.0.0.1
    quant-app tls show

Files live in ``~/.config/minerva/tls`` (keys mode 600): ``ca.crt`` is
installed once on each PC (current-user trust store) and pinned inside the
Android app; ``server.crt``/``server.key`` are what uvicorn serves.  The CA
key never leaves the server.  ``pins`` prints the SHA-256 SPKI pins used by
Android's network security config.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

DEFAULT_TLS_DIR = Path.home() / ".config" / "minerva" / "tls"
CA_DAYS = 3650
SERVER_DAYS = 397  # stay within the one-year limit clients enforce for leaf certificates


def _write_private(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _key_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def spki_pin(certificate: x509.Certificate) -> str:
    """Base64 SHA-256 of the SubjectPublicKeyInfo (Android <pin digest="SHA-256">)."""
    der = certificate.public_key().public_bytes(serialization.Encoding.DER,
                                                serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(hashlib.sha256(der).digest()).decode()


def fingerprint(certificate: x509.Certificate) -> str:
    return certificate.fingerprint(hashes.SHA256()).hex(":").upper()


def init_ca(directory: Path, name: str = "Minerva Private CA") -> x509.Certificate:
    directory.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = directory / "ca.crt", directory / "ca.key"
    if cert_path.exists():
        return x509.load_pem_x509_certificate(cert_path.read_bytes())
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name),
                         x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Minerva")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=CA_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                         content_commitment=False, key_encipherment=False, data_encipherment=False,
                                         key_agreement=False, encipher_only=False, decipher_only=False),
                           critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    _write_private(key_path, _key_pem(key))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert


def issue_server_certificate(directory: Path, ips: list[str], names: list[str]) -> x509.Certificate:
    """(Re)issue server.crt/server.key for the given IP addresses and DNS names."""
    ca_cert = x509.load_pem_x509_certificate((directory / "ca.crt").read_bytes())
    ca_key = serialization.load_pem_private_key((directory / "ca.key").read_bytes(), password=None)
    key = ec.generate_private_key(ec.SECP256R1())
    alt_names: list[x509.GeneralName] = [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips]
    alt_names += [x509.DNSName(name) for name in names]
    if not alt_names:
        raise ValueError("give at least one --ip or --dns")
    now = datetime.now(timezone.utc)
    common = ips[0] if ips else names[0]
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common)]))
            .issuer_name(ca_cert.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=SERVER_DAYS))
            .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=False, crl_sign=False,
                                         content_commitment=False, key_encipherment=False, data_encipherment=False,
                                         key_agreement=True, encipher_only=False, decipher_only=False),
                           critical=True)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    _write_private(directory / "server.key", _key_pem(key))
    # Full chain, so clients that only trust the CA can build the path.
    chain = cert.public_bytes(serialization.Encoding.PEM) + ca_cert.public_bytes(serialization.Encoding.PEM)
    (directory / "server.crt").write_bytes(chain)
    return cert


def describe(directory: Path) -> dict:
    out: dict = {"directory": str(directory)}
    for name in ("ca", "server"):
        path = directory / f"{name}.crt"
        if path.exists():
            cert = x509.load_pem_x509_certificate(path.read_bytes())
            entry = {"subject": cert.subject.rfc4514_string(), "not_after": cert.not_valid_after_utc.isoformat(),
                     "sha256": fingerprint(cert), "spki_pin": spki_pin(cert)}
            if name == "server":
                sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
                entry["alt_names"] = [str(v) for v in sans.get_values_for_type(x509.IPAddress)] + \
                    sans.get_values_for_type(x509.DNSName)
            out[name] = entry
    return out
