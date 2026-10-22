"""REQ-03 (server side): only terminals registered with software that was signed by the
approved certificate authority may take votes.

Each terminal record keeps an "attestation" of its software release in Terminal.software_signature:
the signed manifest, the signature and the signer certificate (JSON). The server checks that the
certificate was issued by the approved CA, is in date and is a code-signing certificate, and that
the signature is valid for the manifest. A terminal without a valid attestation is refused.

Register this BEFORE any requirement that wraps the verify_terminal hook (REQ-19, the mission
allow-list, wraps whatever check is registered). That wrapper then runs this check first."""
import json
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from flask import current_app

from app.security import codesign
from app.security.hooks import hooks


def make_attestation(package):
    """Build the attestation text for a signed software package folder."""
    package = Path(package)
    return json.dumps({
        'manifest': (package / codesign.MANIFEST).read_text(),
        'signature': (package / codesign.SIGNATURE).read_text().strip(),
        'certificate': (package / codesign.CERTIFICATE).read_text(),
    })


def verify_attestation(text, ca_cert):
    """Return (ok, reason)."""
    try:
        data = json.loads(text)
        manifest = data['manifest'].encode()
        signature = bytes.fromhex(data['signature'])
        signer = x509.load_pem_x509_certificate(data['certificate'].encode())
    except (TypeError, ValueError, KeyError, AttributeError):
        return False, 'the software signature is missing or unreadable'
    ok, reason = codesign.check_certificate(signer, ca_cert)
    if not ok:
        return False, reason
    try:
        signer.public_key().verify(signature, manifest)
    except InvalidSignature:
        return False, 'the software signature does not match its manifest'
    return True, ''


def _approved_ca_path():
    configured = current_app.config.get('TERMINAL_CA_PATH')
    return Path(configured) if configured else Path(current_app.instance_path) / 'codesign' / 'ca.crt'


def verify_signed_terminal(terminal, request_meta):
    """Hook verify_terminal. Returns (allowed, message)."""
    if current_app.config.get('TERMINAL_SIGNATURE_REQUIRED', True):
        ca_path = _approved_ca_path()
        if not ca_path.exists():
            return False, 'Terminal refused: no approved certificate authority is installed'
        ca_cert = codesign.load_certificate(ca_path)
        ok, reason = verify_attestation(terminal.software_signature, ca_cert)
        if not ok:
            return False, f'Terminal refused: {reason}'
    return True, ''


def register_terminal_verification():
    """Start from the base check each time, so repeated create_app calls never stack or loop."""
    hooks.reset('verify_terminal')
    hooks.register('verify_terminal', verify_signed_terminal)