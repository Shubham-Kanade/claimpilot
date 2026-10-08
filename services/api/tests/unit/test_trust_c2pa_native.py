"""The opt-in native C2PA backend (c2pa-python): real signing, validation, tampering, no network.

Skipped unless ``C2PA_NATIVE=1`` and the ``c2pa`` extra is installed (``uv sync --extra c2pa``).
On the Windows development machine the native parser raises access violations, so it never runs
by default; run these tests on purpose, ideally on Linux:

    C2PA_NATIVE=1 uv run --extra c2pa pytest tests/unit/test_trust_c2pa_native.py
"""

from __future__ import annotations

import datetime
import http.server
import io
import os
import socketserver
import threading
from contextlib import contextmanager
from typing import Any

import pytest
from test_trust_support import encode, jpeg_bytes, png_bytes, receipt_image, with_xmp, xmp_packet

from claimpilot.trust.c2pa import (
    NATIVE_ENV,
    C2paResult,
    c2pa_findings,
    inspect_c2pa,
    native_backend_active,
    native_enabled,
)

AI = "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"
COMPOSITE = "http://cv.iptc.org/newscodes/digitalsourcetype/compositeWithTrainedAlgorithmicMedia"
CAMERA = "http://cv.iptc.org/newscodes/digitalsourcetype/digitalCapture"

pytestmark = pytest.mark.skipif(
    os.environ.get(NATIVE_ENV, "").strip().lower() not in {"1", "true", "yes", "on"},
    reason="native C2PA tests are opt-in: set C2PA_NATIVE=1 (and install the c2pa extra)",
)


@pytest.fixture(scope="module")
def library():
    return pytest.importorskip("c2pa")


@pytest.fixture(scope="module")
def chain(library):
    """A throw-away root and signer certificate, as PEM (chain, private key)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = datetime.datetime.now(datetime.UTC)
    root_key = ec.generate_private_key(ec.SECP256R1())
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ClaimPilot Test Root CA")])
    root_usage = x509.KeyUsage(False, False, False, False, False, True, True, False, False)
    root = (
        x509.CertificateBuilder()
        .subject_name(root_name)
        .issuer_name(root_name)
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(root_usage, critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(root_key.public_key()), False)
        .sign(root_key, hashes.SHA256())
    )
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf_name = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Fictional Image Studio"),
            x509.NameAttribute(NameOID.COMMON_NAME, "Fictional Image Studio Signer"),
        ]
    )
    leaf_usage = x509.KeyUsage(True, False, False, False, False, False, False, False, False)
    leaf = (
        x509.CertificateBuilder()
        .subject_name(leaf_name)
        .issuer_name(root_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3000))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(leaf_usage, critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.EMAIL_PROTECTION]), False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()), False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()), False
        )
        .sign(root_key, hashes.SHA256())
    )
    pem = leaf.public_bytes(serialization.Encoding.PEM) + root.public_bytes(
        serialization.Encoding.PEM
    )
    key = leaf_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return pem, key


@pytest.fixture(scope="module")
def sign(library, chain):
    """``sign(raw, mime, source_type=None, generator=...)`` -> the file with a signed manifest."""
    pem, key = chain

    def _sign(raw: bytes, mime: str, *, source_type: str | None = None, generator: str = "Test"):
        action: dict[str, Any] = {
            "action": "c2pa.created",
            "digitalSourceType": source_type or CAMERA,
        }
        manifest = {
            "claim_generator_info": [{"name": generator, "version": "1.0"}],
            "title": "receipt",
            "assertions": [{"label": "c2pa.actions", "data": {"actions": [action]}}],
        }
        info = library.C2paSignerInfo(library.C2paSigningAlg.ES256, pem, key, None)
        with library.Signer.from_info(info) as signer, library.Builder(manifest) as builder:
            out = io.BytesIO()
            builder.sign(signer, mime, io.BytesIO(raw), out)
        return out.getvalue()

    return _sign


def plain(kind: str) -> tuple[bytes, str]:
    page = receipt_image(1, size=(200, 260))
    if kind == "jpeg":
        return jpeg_bytes(page), "image/jpeg"
    if kind == "png":
        return png_bytes(page), "image/png"
    return encode(page, "WEBP"), "image/webp"


def test_the_native_backend_loads_when_asked(library):
    assert native_enabled() and native_backend_active()


@pytest.mark.parametrize("kind", ["jpeg", "png", "webp"])
def test_a_valid_signed_file_is_read_and_validated(sign, kind):
    raw, mime = plain(kind)
    result = inspect_c2pa(sign(raw, mime, generator="Test Studio"))
    assert result.present and result.valid is True and result.trusted is False
    assert result.issuer == "Fictional Image Studio" and result.generator == "Test Studio 1.0"
    assert result.actions == ["c2pa.created"] and not result.ai_generated
    assert result.validation_state == "Valid" and result.failure_codes == []
    assert result.source == "library"
    [flag] = c2pa_findings(result)
    assert flag.code == "c2pa_present" and "valid content credentials" in flag.message
    assert "Fictional Image Studio" in flag.message and "not vouched for" in flag.message


@pytest.mark.parametrize("source_type", [AI, COMPOSITE])
def test_a_signed_ai_declaration_is_flagged(sign, source_type):
    raw, mime = plain("png")
    result = inspect_c2pa(sign(raw, mime, source_type=source_type, generator="Image Maker"))
    assert result.ai_generated and result.valid is True
    assert [f.code for f in c2pa_findings(result)] == ["ai_generated_c2pa"]


def test_changing_the_picture_after_signing_invalidates_the_credentials(sign):
    raw, mime = plain("jpeg")
    signed = bytearray(sign(raw, mime, source_type=AI))
    signed[-20] ^= 0xFF  # a pixel byte near the end of the scan data
    result = inspect_c2pa(bytes(signed))
    assert result.present and result.valid is False and result.validation_state == "Invalid"
    assert "assertion.dataHash.mismatch" in result.failure_codes
    assert "signingCredential.untrusted" not in result.failure_codes
    assert result.ai_generated  # an AI claim survives damage to the file
    assert [f.code for f in c2pa_findings(result)] == ["ai_generated_c2pa", "c2pa_invalid"]


def test_a_damaged_manifest_is_reported_invalid_not_ignored(sign):
    raw, mime = plain("jpeg")
    signed = bytearray(sign(raw, mime))
    position = bytes(signed).find(b"c2pa.actions")
    signed[position + 3] ^= 0x01  # corrupt an assertion label inside the manifest
    result = inspect_c2pa(bytes(signed))
    assert result.present and result.valid is False
    assert c2pa_findings(result)[0].code == "c2pa_invalid"


@pytest.mark.parametrize("kind", ["jpeg", "png", "webp"])
def test_files_without_credentials_have_nothing_to_report_natively(library, kind):
    assert inspect_c2pa(plain(kind)[0]) == C2paResult()


@contextmanager
def local_server():
    hits: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(404)
            self.end_headers()

        def log_message(self, format, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], hits
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_a_remote_manifest_url_is_never_fetched(library):
    """An upload must not be able to make the server call out (SSRF)."""
    with local_server() as (port, hits):
        packet = xmp_packet(
            ' xmlns:dcterms="http://purl.org/dc/terms/"'
            f' dcterms:provenance="http://127.0.0.1:{port}/remote.c2pa">'
        )
        raw = with_xmp(jpeg_bytes(receipt_image(2, size=(120, 160))), packet)
        result = inspect_c2pa(raw)
    assert hits == []
    assert result.present and result.valid is None and result.source == "library"
