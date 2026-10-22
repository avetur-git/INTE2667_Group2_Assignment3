"""REQ-03: the secure-boot decision. The terminal software may only start if its
signature verifies against the approved certificate authority."""
from pathlib import Path

from app.security import codesign


def start_allowed(package, approved_ca_path):
    """Return (allowed, message)."""
    approved_ca_path = Path(approved_ca_path)
    if not approved_ca_path.exists():
        return False, 'no approved certificate authority is installed'
    ca_cert = codesign.load_certificate(approved_ca_path)
    ok, reason = codesign.verify_package(package, ca_cert)
    if not ok:
        return False, reason
    return True, f'signature verified (fingerprint {codesign.package_fingerprint(package)[:16]}...)'