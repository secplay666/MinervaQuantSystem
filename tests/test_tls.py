"""Private CA and server certificate for the API (ADR-010 §7, amended)."""

from __future__ import annotations

import ipaddress
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402

from quant_system.app.tls import describe, init_ca, issue_server_certificate  # noqa: E402


def test_server_certificate_is_signed_by_the_ca_for_the_given_names(tmp_path: Path) -> None:
    ca = init_ca(tmp_path)
    assert init_ca(tmp_path).serial_number == ca.serial_number  # the CA is created once and kept
    issue_server_certificate(tmp_path, ["8.159.139.145", "127.0.0.1"], ["localhost"])
    chain = (tmp_path / "server.crt").read_bytes()
    leaf, issuer = x509.load_pem_x509_certificates(chain)
    assert issuer.fingerprint(issuer.signature_hash_algorithm) == ca.fingerprint(ca.signature_hash_algorithm)
    ca.public_key().verify(leaf.signature, leaf.tbs_certificate_bytes, ec.ECDSA(leaf.signature_hash_algorithm))
    sans = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert sans.get_values_for_type(x509.IPAddress) == [ipaddress.ip_address("8.159.139.145"),
                                                       ipaddress.ip_address("127.0.0.1")]
    assert sans.get_values_for_type(x509.DNSName) == ["localhost"]
    assert not leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    assert (leaf.not_valid_after_utc - leaf.not_valid_before_utc).days <= 398
    info = describe(tmp_path)
    assert info["server"]["alt_names"] == ["8.159.139.145", "127.0.0.1", "localhost"]
    assert len(info["ca"]["spki_pin"]) == 44  # base64 of a SHA-256 digest
    reissued = issue_server_certificate(tmp_path, ["127.0.0.1"], [])
    assert reissued.serial_number != leaf.serial_number and describe(tmp_path)["ca"] == info["ca"]
